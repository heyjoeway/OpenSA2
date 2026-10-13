"""Authored isolated S5 governance controls; no Git, build or stage measurement."""
from pathlib import Path
import ast
import hashlib
import importlib.util
import json
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
RECORD = "Documentation/decisions/2026-10-06-S5-continue-650h.md"
QUOTE = "ok clearly progress is being made. you can continue. don't check back in until you exceed 650 hours"


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "Documentation/scripts"))
    result = []
    for name in ["metrics", "gate_report", "check_kill_criteria"]:
        spec = importlib.util.spec_from_file_location(name, ROOT / f"Documentation/scripts/{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        result.append(module)
    return result


@pytest.fixture
def config(tmp_path):
    path = tmp_path / RECORD
    path.parent.mkdir(parents=True)
    path.write_text(f"# S5 continuation\n\nStage: S5\n\n> {QUOTE}\n")
    stages = [{"id": "S4", "title": "Runtime", "budget_hours": 110,
               "start_commit": "start", "gate_commit": "gate"},
              {"id": "S5", "title": "Presentation", "budget_hours": 400,
               "main_thread": "transcripts", "start_commit": None, "gate_commit": None,
               "continuation": {"record": RECORD, "hours": 650, "comparison": "gt"}}]
    doc = {"stages": stages, "idle_gap_hours": 2.0}
    def write():
        (tmp_path / "Documentation/stages.json").write_text(json.dumps(doc))
    write()
    return tmp_path, doc, write


def spend(metrics, monkeypatch, root, raw, whole=800.0):
    own = {key: 0 for key in metrics.TRANSCRIPT_KEYS}
    own.update(_commits=[], _main_thread=raw-25, _units=25, _spent=raw, unit_count=1,
               main_thread_rule="transcripts", idle_gap_hours=2.0, session_count=1,
               counted_from="authored", counted_to="authored", ledger_rev="authored",
               ledger_to="authored", method_spent_hours=200.0, method_main_thread_hours=175.0,
               method_percent=50.0, first_to_last_spent_hours=whole,
               first_to_last_main_thread_hours=whole-40, first_to_last_unit_hours=40,
               first_to_last_percent=whole/4, units_split=1)
    monkeypatch.setattr(metrics, "stage_spend", lambda _root, _stage: own)
    monkeypatch.setattr(metrics, "closed_stage_spend", lambda _root: [])
    monkeypatch.setattr(metrics, "git", lambda *_a: "authored-head")
    monkeypatch.setattr(metrics, "stage_rate", lambda *_a: {"rate": "authored; no measured rate"})
    return metrics.budget_info(root, [])


def halt(stage="active", comparison="ge", threshold=150, precondition=False):
    entry = {"id": "design-halt", "status": "open", "evaluation": {
        "kind": "budget", "stage": stage, "comparison": comparison,
        "threshold": threshold, "units": "% of budget"}}
    if precondition:
        entry["precondition"] = {"kind": "budget", "stage": "active", "comparison": "ge", "threshold": 0}
    return entry


def report_branch(gate, metrics, info, stage):
    tree = ast.parse(Path(gate.__file__).read_text())
    render = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render")
    node = next(n for n in render.body if isinstance(n, ast.If)
                and isinstance(n.test, ast.Name) and n.test.id == "counted"
                and any(isinstance(x, ast.Constant) and x.value == "Review timing:" for x in ast.walk(n)))
    env = dict(counted=True, stage=stage, info=info, spend=info, metrics=metrics, line=gate.line, out=[])
    exec(compile(ast.Module(body=[node], type_ignores=[]), gate.__file__, "exec"), env)
    return "\n".join(env["out"])


@pytest.mark.parametrize("raw,due", [(399, False), (400, False), (599.999, False), (600, False),
                                   (649.99999, False), (650, False), (650.00001, True), (651, True)])
def test_actual_status_report_and_kill_share_unrounded_boundary(modules, config, monkeypatch, raw, due):
    metrics, gate, kill = modules
    root, _doc, _write = config
    info = spend(metrics, monkeypatch, root, raw)
    assert info["budget_hours"] == 400 and info["idle_gap_hours"] == 2.0
    assert info["continuation"]["spent_hours"] == raw and info["continuation"]["due"] is due
    assert info["first_to_last_spent_hours"] == 800
    assert info["status"].startswith("HALT:" if due else "continuation authorized;")
    if raw == 650.00001:
        assert info["spent_hours"] == 650.0 and info["percent"] == 162.5
    budget = kill.Budget(root)
    budget._info = info
    result = kill.evaluate(halt(), root, {}, budget)
    assert result.met is due and result.detail == info["status"]
    printed = metrics.render_budget(info)
    assert info["status"] in printed and "first to last" in printed and "method:" in printed
    stage = metrics.active_stage(metrics.load_stages(root))
    report = report_branch(gate, metrics, info, stage)
    assert info["status"] in report and "First to last:" in report and "Commit spacing:" in report
    assert "mandatory 100 % review due: first to last" not in report


@pytest.mark.parametrize("mutation,raw,expected", [("inclusive", 650, False), ("rounded", 650.00001, True)])
def test_boundary_controls_kill_actual_in_memory_mutants(modules, config, monkeypatch, mutation, raw, expected):
    metrics, _gate, kill = modules
    root, _doc, _write = config
    original = metrics.continuation_due
    assert spend(metrics, monkeypatch, root, raw)["continuation"]["due"] is expected
    if mutation == "inclusive":
        monkeypatch.setattr(metrics, "continuation_due", lambda c, s: s >= c["hours"])
    else:
        monkeypatch.setattr(metrics, "continuation_due", lambda c, s: round(s, 2) > c["hours"])
    wrong = spend(metrics, monkeypatch, root, raw)
    assert wrong["continuation"]["due"] is not expected
    budget = kill.Budget(root); budget._info = wrong
    assert kill.evaluate(halt(), root, {}, budget).met is not expected
    monkeypatch.setattr(metrics, "continuation_due", original)
    assert spend(metrics, monkeypatch, root, raw)["continuation"]["due"] is expected


@pytest.mark.parametrize("field,value", [("hours", True), ("hours", "650"), ("hours", 0),
    ("hours", 400), ("hours", float("nan")), ("hours", float("inf")), ("hours", 10**1000),
    ("comparison", "ge"), ("comparison", "unknown"), ("record", None),
    ("record", "/absolute.md"), ("record", "Documentation/decisions/../elsewhere.md"),
    ("record", "Documentation/decisions/2099-01-01-S5-continue-650h.md"),
    ("record", "Documentation/decisions/2026-10-06-S5-continue-700h.md")])
def test_invalid_metadata_is_refused(modules, config, field, value):
    metrics = modules[0]; root, doc, write = config
    doc["stages"][1]["continuation"][field] = value; write()
    with pytest.raises(metrics.MalformedInput, match="continuation"):
        metrics.load_stages(root)


@pytest.mark.parametrize("fault", ["absent-object", "missing-key", "extra-key", "missing-record",
                                   "escaped-record", "wrong-stage", "wrong-reading", "review-watch", "legacy-halt"])
def test_missing_conflicting_and_other_stage_metadata_is_refused(modules, config, fault):
    metrics = modules[0]; root, doc, write = config
    stage = doc["stages"][1]
    if fault == "absent-object": stage["continuation"] = None
    elif fault == "missing-key": stage["continuation"].pop("comparison")
    elif fault == "extra-key": stage["continuation"]["reading"] = "first_to_last"
    elif fault == "missing-record": (root / RECORD).unlink()
    elif fault == "escaped-record":
        data = (root / RECORD).read_bytes(); (root / RECORD).unlink()
        target = root / "outside.md"; target.write_bytes(data); (root / RECORD).symlink_to(target)
    elif fault == "wrong-stage": stage["id"] = "S6"
    elif fault == "wrong-reading": stage.pop("main_thread")
    elif fault == "review-watch": stage["review_reading"] = "first_to_last"
    elif fault == "legacy-halt":
        (root / "Documentation/decisions/2026-10-06-S5-halt-700h.md").write_text("> S5 may run to 700 hours.\n")
    write()
    with pytest.raises(metrics.MalformedInput): metrics.load_stages(root)


@pytest.mark.parametrize("text", [f"# S5\n> {QUOTE}\n", f"Stage: S6\n> {QUOTE}\n",
    f"Stage: S5\nStage: S5\n> {QUOTE}\n", f"> Stage: S5\n> {QUOTE}\n",
    f"Stage: S5\n{QUOTE}\n", f"Stage: S5\n> {QUOTE.replace('650', '700')}\n",
    f"Stage: S5\n> Asked: {QUOTE}\n> Answered: Yes\n",
    f"Stage: S5\n> {QUOTE}, but you must stop now\n",
    f"```\nStage: S5\n```\n> {QUOTE}\n",
    f"Stage: S5\n```\n> {QUOTE}\n```\n",
    "Stage: S5\n> you can continue.\n\n> don't check back in until you exceed 650 hours\n"])
def test_record_context_and_actual_direct_quote_are_required(modules, config, text):
    metrics = modules[0]; root, _doc, _write = config
    (root / RECORD).write_text(text)
    with pytest.raises(metrics.MalformedInput, match="continuation"): metrics.load_stages(root)


@pytest.mark.parametrize("text", [
    f"Stage: S5\n\n    > {QUOTE}\n", f"Stage: S5\n\n\t> {QUOTE}\n",
    f"Stage: S5\n\n>     {QUOTE}\n",
    f"Stage: S5\n\n>   \t{QUOTE}\n",
    f"<!--\nStage: S5\n\n> {QUOTE}\n-->\n", f"<!--\nStage: S5\n\n> {QUOTE}\n",
    f"<pre>\nStage: S5\n\n> {QUOTE}\n</pre>\n",
    f"<script>\nStage: S5\n\n> {QUOTE}\n</script>\n",
    f"<script>\nStage: S5\n\n> {QUOTE}\n",
    f"<?\nStage: S5\n\n> {QUOTE}\n?>\n",
    f"<![CDATA[\nStage: S5\n\n> {QUOTE}\n]]>\n",
    f"<!DOCTYPE\nStage: S5\n\n> {QUOTE}\n>\n",
    f"Stage: S5\n\n> {QUOTE.upper()}\n",
    f"Stage: S5\n\n> {QUOTE}\n\n> {QUOTE.replace('650', '700')}\n",
    f"Stage: S5\n\n> {QUOTE}\n\n> {QUOTE}\n",
    f"Stage: S5\n\n> ```\n\n> {QUOTE}\n\n> ```\n",
])
def test_hidden_code_case_or_ambiguous_authority_is_refused(modules, config, text):
    metrics = modules[0]; root, _doc, _write = config
    (root / RECORD).write_text(text)
    with pytest.raises(metrics.MalformedInput, match="continuation"): metrics.load_stages(root)


def test_visible_exact_blockquote_allows_whitespace_wrapping_only(modules, config):
    metrics = modules[0]; root, _doc, _write = config
    text = "Stage: S5\n\n> ok clearly progress is being made.\n> you can continue. don't check back in\n> until you exceed 650 hours\n"
    (root / RECORD).write_text(text)
    result = metrics.load_continuation(root, metrics.active_stage(metrics.load_stages(root)))
    assert result["hours"] == 650 and result["comparison"] == "gt"


@pytest.mark.parametrize("hours,quoted,accepted", [(650.00001, "650", False),
    (650.0001, "650", False), (650.00001, "650.00001", True)])
def test_authority_hours_never_use_display_precision(modules, config, hours, quoted, accepted):
    metrics = modules[0]; root, doc, write = config
    record = f"Documentation/decisions/2026-10-06-S5-continue-{hours}h.md"
    doc["stages"][1]["continuation"].update(record=record, hours=hours)
    (root / record).write_text(f"Stage: S5\n\n> {QUOTE.replace('650', quoted)}\n")
    write()
    if accepted:
        result = metrics.load_continuation(root, metrics.active_stage(metrics.load_stages(root)))
        assert result["hours"] == 650.00001
    else:
        with pytest.raises(metrics.MalformedInput, match="continuation"): metrics.load_stages(root)


@pytest.mark.parametrize("raw,due", [(650.00001, False), (650.00002, True)])
def test_fractional_authority_is_rendered_at_full_precision(modules, config, monkeypatch, capsys, raw, due):
    metrics, gate, kill = modules; root, doc, write = config
    record = "Documentation/decisions/2026-10-06-S5-continue-650.00001h.md"
    doc["stages"][1]["continuation"].update(record=record, hours=650.00001)
    (root / record).write_text(f"Stage: S5\n\n> {QUOTE.replace('650', '650.00001')}\n")
    write()
    info = spend(metrics, monkeypatch, root, raw)
    assert info["continuation"]["due"] is due and info["spent_hours"] == 650.0
    boundary = "binding active time > 650.00001 h"
    assert boundary in info["status"] and boundary in metrics.render_budget(info)
    stage = metrics.active_stage(metrics.load_stages(root))
    assert boundary in report_branch(gate, metrics, info, stage)
    budget = kill.Budget(root); budget._info = info
    assert kill.evaluate(halt(), root, {}, budget).met is due
    tree = ast.parse(Path(kill.__file__).read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    note = next(n for n in run.body if isinstance(n, ast.For) and isinstance(n.target, ast.Tuple)
                and all(isinstance(t, ast.Name) for t in n.target.elts)
                and [t.id for t in n.target.elts] == ["identifier", "continuation"])
    exec(compile(ast.Module(body=[note], type_ignores=[]), kill.__file__, "exec"),
         {"continuations": {"design-halt": info["continuation"]}, "metrics_module": metrics})
    assert "evaluated at " + boundary in capsys.readouterr().out


@pytest.mark.parametrize("entry", [halt(comparison="gt"), halt(threshold=100), halt(precondition=True), halt(stage="S4")])
def test_continuation_does_not_change_other_kill_rules(modules, config, monkeypatch, entry):
    metrics, _gate, kill = modules; root, _doc, _write = config
    info = spend(metrics, monkeypatch, root, 650)
    info["closed_stages"] = [{"stage": "S4", "percent": 151}]
    budget = kill.Budget(root); budget._info = info
    assert budget.continuation(entry) is None
    assert kill.evaluate(entry, root, {}, budget).met


def test_default_and_legacy_inclusive_statuses_are_preserved(modules, config, monkeypatch):
    metrics, _gate, kill = modules; root, doc, write = config
    doc["stages"][1].pop("continuation"); write()
    for raw, prefix in [(399, "snapshot due"), (400, "gate review due"), (600, "HALT (150 % reached)")]:
        info = spend(metrics, monkeypatch, root, raw)
        assert "continuation" not in info and info["status"].startswith(prefix)
    assert metrics.threshold_status(120, (200, 220, "legacy.md")).startswith("gate review due")
    assert metrics.threshold_status(200, (200, 220, "legacy.md")).startswith("HALT (200 % reached")
    budget = kill.Budget(root); budget._info = dict(info, active_stage="S4", percent=200,
        halt_percent=200, halt_ruling="legacy.md")
    assert budget.moved_halt(halt()) == (200, "legacy.md")
    assert kill.evaluate(halt(), root, {}, budget).met


def test_closed_s5_continuation_does_not_follow_the_next_stage(modules, config, monkeypatch):
    metrics = modules[0]; root, doc, write = config
    doc["stages"][1]["gate_commit"] = "closed-s5"
    doc["stages"].append({"id": "S6", "title": "Breadth", "budget_hours": None,
                          "main_thread": "transcripts", "start_commit": None, "gate_commit": None})
    write()
    info = spend(metrics, monkeypatch, root, 650.00001)
    assert info["active_stage"] == "S6" and "continuation" not in info
    assert info["status"] == "no fixed ceiling"


def test_kill_marker_binds_effective_stage_record_hash_hours_and_comparison(modules, config, monkeypatch):
    metrics, _gate, kill = modules; root, _doc, _write = config
    info = spend(metrics, monkeypatch, root, 650)
    budget = kill.Budget(root); budget._info = info
    tree = ast.parse(Path(kill.__file__).read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    node = next(n for n in run.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "marker" for t in n.targets))
    code = compile(ast.Module(body=[node], type_ignores=[]), kill.__file__, "exec")
    def marker(c):
        env = dict(hashlib=hashlib, json=json, metrics_module=metrics, criteria=[halt()], moved_halts={},
                   continuations={"design-halt": c}, budget=budget, condition_text=kill.condition_text)
        exec(code, env); return env["marker"]
    original = marker(info["continuation"])
    for key, value in [("hours", 700), ("comparison", "ge"), ("record", "another.md"),
                       ("record_sha256", "0"*64)]:
        changed = dict(info["continuation"], **{key: value})
        assert marker(changed) != original
    budget._info = dict(info, active_stage="S6")
    assert marker(info["continuation"]) != original
