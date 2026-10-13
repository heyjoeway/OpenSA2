"""Tests for Documentation/scripts/check_replay_regression.py.

The gate's readings come from the tree through `current_readings`; every test
here but the last group hands `main` its readings instead (`now=`), so what
varies is the baseline, its history, the registry and the decisions, in a
throwaway git repository. The last group runs `current_readings` itself over a
small synthetic tree, with `tick_replay.inputs` and `identity_problems` stood
in for, since those two read the real build.

Each check is shown failing on the state it exists for and passing on the one
beside it: design 7.3 ("a replay that has passed never silently stops being
run") and the user's 2026-09-26 direction ("every registered replay stays in
the gates for all later stages as a regression check").
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
REPO = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_replay_regression as RR  # noqa: E402

USER = "Date: 2026-10-01. Stage S5. Made by the user, in their words below.\n\n"
AGENT = "Date: 2026-10-01. Stage S5. Made by the agent.\n\n"


def env() -> dict[str, str]:
    e = dict(os.environ)
    e.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
              "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
              "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    return e


def git(repo: Path, *args: str) -> str:
    return subprocess.run(("git", "-C", str(repo)) + args, env=env(), capture_output=True,
                          text=True, check=True).stdout.strip()


def write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def reading(name: str, met: bool = False, tick: int | None = 381, judged: int = 387,
            goal: bool | None = None, ticks: int = 1200, cls: str = "bug",
            field: str = "d_01750000", trace: int | None = None) -> dict:
    if tick is None:
        m5, cls, field = f"{name} no divergence/{ticks}", None, None
    else:
        m5 = f"{name} {tick}/{ticks} ({cls}: {field})"
    return {"m5": m5, "ticks": ticks, "first_divergent_tick": tick, "field": field,
            "class": cls, "judged_ticks": judged, "trace_judged_ticks": trace,
            "m5_met": met, "goal_complete": goal}


MET_BOOT = reading("boot", met=True, tick=None, judged=1200)
# The sentence a decision must hold to accept boot reading `reading("boot", tick=4)`,
# written out here rather than built by the gate's own `accepts`.
ACCEPT4 = ("Accepts replay `boot` reading `boot 4/1200 (bug: d_01750000)` (does not meet M5), "
           "judged over 387 ticks, no goal.\n")
MH_GOAL = reading("metalharbor", tick=1, judged=121, ticks=15585, goal=True, trace=121)


def registry(*names: str, retired: list[dict] | None = None) -> str:
    return json.dumps({"replays": [{"name": n} for n in names], "retired": retired or []})


LEDGER = {
    "claims": [{"authority": "observed", "depends_on": [], "id": RR.CLAIM_ID, "outputs": [],
                "sources": [{"path": RR.BASELINE_REL, "sha256": "0" * 64}],
                "summary": "the baseline"}],
    "suites": [{"checker": "Documentation/scripts/check_replay_regression.py",
                "claims": [RR.CLAIM_ID], "depends_on": [],
                "command": ["python3", "Documentation/scripts/check_replay_regression.py"],
                "files": [{"path": "Documentation/scripts/check_replay_regression.py",
                           "sha256": "0" * 64}],
                "id": RR.SUITE_ID, "marker": {"kind": "sha256", "value": "0" * 64},
                "summary": "the suite"}],
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, RR.REGISTRY_REL, registry("boot"))
    write(r, RR.LEDGER_REL, json.dumps(LEDGER, indent=2, sort_keys=True) + "\n")
    write(r, RR.TICK_REPLAY_REL, json.dumps({"commit": "c" * 40, "replays": []}))
    write(r, RR.STAGE_BOOT_REL, json.dumps({"replays": []}))
    write(r, "Documentation/scripts/check_replay_regression.py",
          (SCRIPTS / "check_replay_regression.py").read_text())
    commit(r, "registry")
    return r


def run(repo: Path, readings: dict, *args: str, stale: list[str] | None = None,
        capsys=None) -> tuple[int, str]:
    code = RR.main(["--root", str(repo), *args], now=lambda root: (readings, list(stale or [])))
    out = capsys.readouterr() if capsys else None
    return code, (out.out + out.err) if out else ""


def refresh(repo: Path, readings: dict, capsys, *args: str) -> tuple[int, str]:
    return run(repo, readings, "--refresh", *args, capsys=capsys)


# coverage ---------------------------------------------------------------------------------

def test_no_baseline_is_missing_not_passing(repo, capsys):
    code, out = run(repo, {"boot": reading("boot")}, capsys=capsys)
    assert code == 1 and "MISSING no baseline" in out


def test_a_registered_replay_not_in_the_baseline_fails_until_refresh(repo, capsys):
    assert refresh(repo, {"boot": reading("boot")}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, RR.REGISTRY_REL, registry("boot", "metalharbor"))
    now = {"boot": reading("boot"), "metalharbor": MH_GOAL}
    code, out = run(repo, now, capsys=capsys)
    assert code == 1 and "NOT IN BASELINE metalharbor" in out
    assert refresh(repo, now, capsys)[0] == 0
    code, out = run(repo, now, capsys=capsys)
    assert code == 0 and "replay regression OK: 2 registered replays" in out


def test_a_readings_problem_fails_the_gate_and_the_refresh(repo, capsys):
    assert refresh(repo, {"boot": reading("boot")}, capsys)[0] == 0
    stale = ["boot: its recorded inputs are not the tree's; re-run tools/s4/tick_replay.py"]
    code, out = run(repo, {"boot": reading("boot")}, stale=stale, capsys=capsys)
    assert code == 1 and "its recorded inputs are not the tree's" in out
    code, out = run(repo, {"boot": reading("boot")}, "--refresh", stale=stale, capsys=capsys)
    assert code == 1 and "REFUSED" in out


# regression -------------------------------------------------------------------------------

@pytest.mark.parametrize("now, says", [
    (reading("boot", tick=4), "no longer meets M5"),
    (reading("boot", tick=122, ticks=1200, judged=121, cls="unrepeatable",
             field="native_unrepeated"), "judged 121 ticks, was 1200"),
    (reading("boot", met=True, tick=None, judged=1100), "judged 1100 ticks, was 1200"),
    (reading("boot", met=True, tick=None, judged=1200, ticks=1100), "native capture holds 1100"),
])
def test_a_replay_that_met_m5_and_reads_worse_fails(repo, capsys, now, says):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline: boot meets M5")
    code, out = run(repo, {"boot": now}, capsys=capsys)
    assert code == 1 and "REGRESSION boot" in out and says in out


def test_a_stage_replay_that_reached_its_goal_and_no_longer_does_fails(repo, capsys):
    write(repo, RR.REGISTRY_REL, registry("metalharbor"))
    assert refresh(repo, {"metalharbor": MH_GOAL}, capsys)[0] == 0
    commit(repo, "baseline: the goal reached")
    lost = dict(MH_GOAL, goal_complete=False)
    code, out = run(repo, {"metalharbor": lost}, capsys=capsys)
    assert code == 1 and "REGRESSION metalharbor" in out and "no longer reaches the goal" in out


def test_a_gain_fails_until_refresh_holds_it(repo, capsys):
    assert refresh(repo, {"boot": reading("boot")}, capsys)[0] == 0
    commit(repo, "baseline: boot unmet")
    code, out = run(repo, {"boot": MET_BOOT}, capsys=capsys)
    assert code == 1 and "BASELINE STALE boot: meets M5 now" in out
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    assert run(repo, {"boot": MET_BOOT}, capsys=capsys)[0] == 0


def test_a_ratcheted_reading_that_moved_without_getting_worse_is_stale(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    longer = dict(MET_BOOT, judged_ticks=1300, ticks=1300, m5="boot no divergence/1300")
    code, out = run(repo, {"boot": longer}, capsys=capsys)
    assert code == 1 and "BASELINE STALE boot" in out and "REGRESSION" not in out


def test_a_labelled_residue_that_moved_earlier_is_stale_not_a_regression(repo, capsys):
    """M5 still holds, over as many ticks: the baseline records the move on a plain
    refresh, which prints it, and needs no decision of the user's."""
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline: boot clean")
    moved = reading("boot", met=True, tick=900, cls="residue host-timing-v1", field="d_x",
                    judged=1200)
    code, out = run(repo, {"boot": moved}, capsys=capsys)
    assert code == 1 and "BASELINE STALE boot" in out and "REGRESSION" not in out
    code, out = refresh(repo, {"boot": moved}, capsys)
    assert code == 0 and "moved: boot `boot no divergence/1200` -> `boot 900/1200" in out
    commit(repo, "baseline: the residue recorded")
    assert run(repo, {"boot": moved}, capsys=capsys)[0] == 0


def test_the_ratchet_holds_from_a_residue_reading_too(repo, capsys):
    labelled = reading("boot", met=True, tick=900, cls="residue host-timing-v1", field="d_x",
                       judged=1200)
    assert refresh(repo, {"boot": labelled}, capsys)[0] == 0
    commit(repo, "baseline: boot on a residue")
    code, out = run(repo, {"boot": reading("boot", tick=900, field="d_x", judged=1200)},
                    capsys=capsys)
    assert code == 1 and "REGRESSION boot: no longer meets M5" in out


def test_an_unratcheted_replay_may_move_and_says_so(repo, capsys):
    assert refresh(repo, {"boot": reading("boot")}, capsys)[0] == 0
    commit(repo, "baseline")
    code, out = run(repo, {"boot": reading("boot", tick=4)}, capsys=capsys)
    assert code == 0 and "note: boot meets neither M5 nor a goal" in out


# retirement and accepted regressions ------------------------------------------------------

@pytest.mark.parametrize("retired, says", [
    ([], "is not a decision record"),
    ([{"name": "boot", "reason": "superseded"}], "is not a decision record"),
    ([{"name": "boot", "decision": "Documentation/decisions/agent.md"}],
     "made or adopted by the user"),
    ([{"name": "boot", "decision": "Documentation/decisions/other.md"}],
     'does not say "Retires replay `boot`"'),
    ([{"name": "boot", "decision": "Documentation/decisions/../decisions/user.md"}],
     "is not a decision record"),
    ([{"name": "boot", "decision": "Documentation/decisions/user.txt"}],
     "is not a decision record"),
    ([{"name": "boot", "decision": "Documentation/receipts/user.md"}],
     "is not a decision record"),
    ([{"name": "boot", "decision": "Documentation/decisions/user.md"}], None),
])
def test_a_replay_leaves_the_registry_only_on_the_user_s_decision(repo, capsys, retired, says):
    write(repo, RR.REGISTRY_REL, registry("boot", "metalharbor"))
    both = {"boot": reading("boot"), "metalharbor": MH_GOAL}
    assert refresh(repo, both, capsys)[0] == 0
    commit(repo, "baseline: two replays")
    write(repo, "Documentation/decisions/agent.md", AGENT + "Retires replay `boot`.\n")
    write(repo, "Documentation/decisions/other.md", USER + "Retires replay `metalharbor`.\n")
    write(repo, "Documentation/decisions/user.md", USER + "Retires replay `boot`.\n")
    write(repo, "Documentation/decisions/user.txt", USER + "Retires replay `boot`.\n")
    write(repo, "Documentation/receipts/user.md", USER + "Retires replay `boot`.\n")
    write(repo, RR.REGISTRY_REL, registry("metalharbor", retired=retired))
    gate_code, gate_out = run(repo, {"metalharbor": MH_GOAL}, capsys=capsys)
    code, out = refresh(repo, {"metalharbor": MH_GOAL}, capsys)
    if says is None:
        assert code == 0 and "retired: boot" in out
        commit(repo, "boot retired on the user's decision")
        assert run(repo, {"metalharbor": MH_GOAL}, capsys=capsys)[0] == 0
    else:
        assert gate_code == 1 and "DROPPED boot" in gate_out and says in gate_out
        assert code == 1 and "boot left the registry" in out and says in out


def test_refresh_records_a_worse_reading_only_on_the_user_s_decision(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline: boot meets M5")
    worse = reading("boot", tick=4)
    code, out = refresh(repo, {"boot": worse}, capsys)
    assert code == 1 and "would be recorded reading worse" in out
    write(repo, "Documentation/decisions/agent.md", AGENT + ACCEPT4)
    code, out = refresh(repo, {"boot": worse}, capsys, "--accept-regression",
                        "Documentation/decisions/agent.md")
    assert code == 1 and "made or adopted by the user" in out
    write(repo, "Documentation/decisions/user.md", USER + ACCEPT4)
    code, out = refresh(repo, {"boot": worse}, capsys, "--accept-regression",
                        "Documentation/decisions/user.md")
    assert code == 0 and "accepted: boot" in out
    doc = json.loads((repo / RR.BASELINE_REL).read_text())
    assert doc["replays"][0]["accepted"] == [{"decision": "Documentation/decisions/user.md",
                                              "from": RR.reading_of(MET_BOOT),
                                              "to": RR.reading_of(worse)}]
    commit(repo, "baseline: boot at tick 4, on the user's decision")
    assert run(repo, {"boot": worse}, capsys=capsys)[0] == 0


def test_a_hand_edited_baseline_cannot_record_a_regression(repo, capsys):
    """Committed: boot meets M5. Working: the baseline edited to the worse reading,
    so the current comparison sees no move; the history does."""
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline: boot meets M5")
    worse = reading("boot", tick=4)
    doc = json.loads((repo / RR.BASELINE_REL).read_text())
    doc["replays"][0].update(worse)
    write(repo, RR.BASELINE_REL, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    code, out = run(repo, {"boot": worse}, capsys=capsys)
    assert code == 1 and "HISTORY boot" in out
    commit(repo, "a hand edit, committed without the gate")
    code, out = run(repo, {"boot": worse}, capsys=capsys)
    assert code == 1 and "HISTORY boot" in out
    # the way out is the user's decision, recorded by --refresh
    write(repo, "Documentation/decisions/user.md", USER + ACCEPT4)
    code, out = refresh(repo, {"boot": worse}, capsys, "--accept-regression",
                        "Documentation/decisions/user.md")
    assert code == 0, out
    assert run(repo, {"boot": worse}, capsys=capsys)[0] == 0


def test_a_committed_baseline_that_does_not_parse_does_not_reset_the_ratchet(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline: boot meets M5")
    write(repo, RR.BASELINE_REL, "{ not json\n")
    commit(repo, "a broken baseline")
    worse = reading("boot", tick=4)
    doc = {"schema": RR.SCHEMA, "replays": [dict(name="boot", accepted=[], **worse)]}
    write(repo, RR.BASELINE_REL, json.dumps(doc))
    commit(repo, "a worse one after it")
    code, out = run(repo, {"boot": worse}, capsys=capsys)
    assert code == 1 and "HISTORY boot" in out and "does not parse" in out


# the ledger ---------------------------------------------------------------------------------

def test_refresh_moves_this_suite_s_pins_and_marker_and_nothing_else(repo, capsys):
    code, out = refresh(repo, {"boot": MET_BOOT}, capsys)
    assert code == 0
    ledger = json.loads((repo / RR.LEDGER_REL).read_text())
    suite, = ledger["suites"]
    claim, = ledger["claims"]
    printed = out.strip().splitlines()[-1].split("=", 1)[1]
    assert suite["marker"] == {"kind": "sha256", "value": printed}
    sha = hashlib.sha256((repo / RR.BASELINE_REL).read_bytes()).hexdigest()
    assert claim["sources"] == [{"path": RR.BASELINE_REL, "sha256": sha}]
    checker = repo / "Documentation/scripts/check_replay_regression.py"
    assert suite["files"][0]["sha256"] == hashlib.sha256(checker.read_bytes()).hexdigest()
    assert run(repo, {"boot": MET_BOOT}, capsys=capsys)[0] == 0


def test_the_marker_moves_with_a_ratcheted_reading_and_not_with_an_unratcheted_one():
    a = {"boot": reading("boot"), "metalharbor": MH_GOAL}
    assert RR.marker(a) == RR.marker({"boot": reading("boot", tick=4), "metalharbor": MH_GOAL})
    assert RR.marker(a) != RR.marker({"boot": MET_BOOT, "metalharbor": MH_GOAL})
    assert RR.marker(a) != RR.marker({"boot": reading("boot")})


def test_the_gate_is_in_run_all_gates_and_the_ledger():
    import run_all_gates
    assert ("replay regression", ("check_replay_regression.py",)) in run_all_gates.GATES
    assert ("replay results", ("check_replay_results.py",)) in run_all_gates.GATES
    ledger = json.loads((REPO / RR.LEDGER_REL).read_text())
    suite = next(s for s in ledger["suites"] if s["id"] == RR.SUITE_ID)
    assert suite["checker"] == "Documentation/scripts/check_replay_regression.py"
    assert suite["claims"] == [RR.CLAIM_ID]
    claim = next(c for c in ledger["claims"] if c["id"] == RR.CLAIM_ID)
    assert [s["path"] for s in claim["sources"]] == [RR.BASELINE_REL]


# M5 over one part, and the readings from the tree ------------------------------------------

def test_m5_over_one_part_is_stage_exit_s_predicate(tmp_path):
    residues = tmp_path / "residues"
    residues.mkdir()
    (residues / "host-timing-v1.json").write_text("{}")
    assert RR.part_meets_m5("boot no divergence/1200", residues)
    assert not RR.part_meets_m5("boot no divergence/0", residues)
    assert not RR.part_meets_m5("boot 381/1200 (bug: d_01750000)", residues)
    assert RR.part_meets_m5("boot 381/1200 (residue host-timing-v1: d_x)", residues)
    assert not RR.part_meets_m5("boot 381/1200 (residue host-services-v1: d_x)", residues)
    assert not RR.part_meets_m5("boot 122/15585 (unrepeatable: native_unrepeated)", residues)


def synthetic_tree(root: Path, boot_result: dict, mh_goal: bool | None = True) -> None:
    """A registry of boot and a stage replay, the receipts that describe them and
    METRICS.md's row, consistent with each other."""
    import tick_replay as T
    entries = [{"name": "boot", "native": {}}, {"name": "metalharbor", "native": {},
                                                "stage": {"level": 10}}]
    write(root, RR.REGISTRY_REL, json.dumps({"replays": entries}))
    mh_result = {"reported": {"tick": 1, "field": "d_00910000"}, "class": "bug",
                 "native_ticks": 15585, "judged_ticks": 121,
                 "trace": {"judged_ticks": 121}}
    docs = [{"name": "boot", "inputs": {"lifted": {"x": 1}}, "result": boot_result},
            {"name": "metalharbor", "inputs": {"lifted": {"x": 2}}, "result": mh_result}]
    receipt = {"registry_sha256": T.sha256(root / RR.REGISTRY_REL), "replays": docs,
               "m5": [T.m5_record(d) for d in docs]}
    write(root, RR.TICK_REPLAY_REL, json.dumps(receipt))
    goals = [] if mh_goal is None else [{"replay": "metalharbor", "complete": mh_goal}]
    write(root, RR.STAGE_BOOT_REL, json.dumps({"replays": goals}))
    row = "; ".join(RR.render(r) for r in receipt["m5"])
    write(root, RR.METRICS_REL, f"| M5 replays | {row} | harness level 2/3 | measured |\n")
    (root / RR.RESIDUES_REL).mkdir(parents=True, exist_ok=True)


@pytest.fixture
def stand_ins(monkeypatch):
    import tick_replay as T
    monkeypatch.setattr(T, "inputs", lambda entry, root: {"lifted": {
        "x": 1 if entry["name"] == "boot" else 2}})
    ident: list[str] = []
    monkeypatch.setattr(T, "identity_problems", lambda receipt, registry, root: list(ident))
    return ident


CLEAN_BOOT = {"reported": None, "class": None, "native_ticks": 1200, "judged_ticks": 1200}


def test_the_readings_from_a_consistent_tree(tmp_path, stand_ins):
    synthetic_tree(tmp_path, CLEAN_BOOT)
    readings, stale = RR.current_readings(tmp_path)
    assert stale == []
    assert readings["boot"] == {
        "m5": "boot no divergence/1200", "ticks": 1200, "first_divergent_tick": None,
        "field": None, "class": None, "judged_ticks": 1200, "trace_judged_ticks": None,
        "compared_words": None, "m5_met": True, "goal_complete": None}
    assert readings["metalharbor"]["m5_met"] is False
    assert readings["metalharbor"]["goal_complete"] is True
    assert readings["metalharbor"]["trace_judged_ticks"] == 121


def test_an_identity_problem_unmeets_its_own_replay(tmp_path, stand_ins):
    synthetic_tree(tmp_path, CLEAN_BOOT)
    stand_ins.append("boot: the open check fails at tick 5 d_x (native 1, lifted 2)")
    readings, _ = RR.current_readings(tmp_path)
    assert readings["boot"]["m5_met"] is False


def drop_from_receipt(root: Path, name: str) -> None:
    """The replay's result and M5 record gone from the tick-replay receipt."""
    doc = json.loads((root / RR.TICK_REPLAY_REL).read_text())
    doc["replays"] = [d for d in doc["replays"] if d["name"] != name]
    doc["m5"] = [r for r in doc["m5"] if r["name"] != name]
    write(root, RR.TICK_REPLAY_REL, json.dumps(doc))


def test_an_identity_rule_that_cannot_be_checked_unmeets_every_replay(tmp_path, stand_ins,
                                                                        monkeypatch):
    import tick_replay as T
    synthetic_tree(tmp_path, CLEAN_BOOT)

    def refuses(receipt, registry, root):
        raise SystemExit("the identity record does not read")
    monkeypatch.setattr(T, "identity_problems", refuses)
    readings, _ = RR.current_readings(tmp_path)
    assert readings["boot"]["m5_met"] is False


@pytest.mark.parametrize("break_it, says", [
    (lambda root: write(root, RR.REGISTRY_REL, (root / RR.REGISTRY_REL).read_text() + "\n"),
     "was written under another"),
    (lambda root: write(root, RR.METRICS_REL, "| M5 replays | boot no divergence/1200 | x |\n"),
     "METRICS.md's M5 row reads None"),
    (lambda root: write(root, RR.STAGE_BOOT_REL, json.dumps({"replays": []})),
     "holds no goal verdict"),
    (lambda root: drop_from_receipt(root, "metalharbor"),
     "holds ['boot'] and the registry registers"),
])
def test_readings_that_cannot_be_tied_to_the_tree_are_named(tmp_path, stand_ins, break_it, says):
    synthetic_tree(tmp_path, CLEAN_BOOT)
    break_it(tmp_path)
    _, stale = RR.current_readings(tmp_path)
    assert any(says in s for s in stale), stale


def test_a_receipt_whose_m5_is_not_its_result_s_is_named(tmp_path, stand_ins):
    synthetic_tree(tmp_path, CLEAN_BOOT)
    doc = json.loads((tmp_path / RR.TICK_REPLAY_REL).read_text())
    doc["m5"][0]["first_divergent_tick"] = 1
    write(tmp_path, RR.TICK_REPLAY_REL, json.dumps(doc))
    _, stale = RR.current_readings(tmp_path)
    assert any("not derived from its result" in s for s in stale), stale


def test_moved_inputs_are_named(tmp_path, stand_ins, monkeypatch):
    import tick_replay as T
    synthetic_tree(tmp_path, CLEAN_BOOT)
    monkeypatch.setattr(T, "inputs", lambda entry, root: {"lifted": {"x": 9}})
    _, stale = RR.current_readings(tmp_path)
    assert sum("recorded inputs are not the tree's" in s for s in stale) == 2


# review of 2026-09-26 -------------------------------------------------------------------------

REAL_DECISIONS = REPO / "Documentation" / "decisions"


@pytest.mark.parametrize("old", ["2026-09-23-S4-tick-100-oracle-fault.md",
                                 "2026-09-24-S4-110h-review.md"])
def test_an_old_user_decision_that_merely_names_the_replay_authorises_nothing(repo, capsys, old):
    rel = f"Documentation/decisions/{old}"
    write(repo, rel, (REAL_DECISIONS / old).read_text())
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    code, out = refresh(repo, {"boot": reading("boot", tick=4)}, capsys, "--accept-regression", rel)
    assert code == 1 and "does not say" in out
    write(repo, RR.REGISTRY_REL, registry(retired=[{"name": "boot", "decision": rel}]))
    code, out = run(repo, {}, capsys=capsys)
    assert code == 1 and "DROPPED boot" in out


def test_a_decision_accepts_only_the_reading_it_names(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/user.md", USER + ACCEPT4)
    code, out = refresh(repo, {"boot": reading("boot", tick=2)}, capsys, "--accept-regression",
                        "Documentation/decisions/user.md")
    assert code == 1 and "does not say" in out


def test_the_user_phrase_must_be_on_the_date_line(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/agent.md",
          AGENT + "The host choice was made by the user earlier.\n" + ACCEPT4)
    code, out = refresh(repo, {"boot": reading("boot", tick=4)}, capsys, "--accept-regression",
                        "Documentation/decisions/agent.md")
    assert code == 1 and "in its Date paragraph" in out


@pytest.mark.parametrize("now, says", [
    (dict(MH_GOAL, judged_ticks=100), "judged 100 ticks, was 121"),
    (dict(MH_GOAL, trace_judged_ticks=100), "its stage trace judged 100 ticks"),
    (dict(MH_GOAL, ticks=15000), "native capture holds 15000"),
])
def test_a_goal_ratcheted_replay_compared_over_less_is_a_regression(repo, capsys, now, says):
    write(repo, RR.REGISTRY_REL, registry("metalharbor"))
    assert refresh(repo, {"metalharbor": MH_GOAL}, capsys)[0] == 0
    commit(repo, "baseline")
    code, out = run(repo, {"metalharbor": now}, capsys=capsys)
    assert code == 1 and "REGRESSION metalharbor" in out and says in out


def test_fewer_words_compared_is_a_regression(repo, capsys):
    base = dict(MET_BOOT, compared_words=644081)
    assert refresh(repo, {"boot": base}, capsys)[0] == 0
    commit(repo, "baseline")
    code, out = run(repo, {"boot": dict(base, compared_words=600000)}, capsys=capsys)
    assert code == 1 and "compared 600000 words, was 644081" in out


def test_a_met_replay_whose_trace_judges_less_is_a_regression(repo, capsys):
    base = dict(MET_BOOT, trace_judged_ticks=1200)
    assert refresh(repo, {"boot": base}, capsys)[0] == 0
    commit(repo, "baseline")
    code, out = run(repo, {"boot": dict(base, trace_judged_ticks=900)}, capsys=capsys)
    assert code == 1 and "its stage trace judged 900 ticks" in out


def test_a_replay_removed_from_registry_and_baseline_together_is_dropped(repo, capsys):
    write(repo, RR.REGISTRY_REL, registry("boot", "metalharbor"))
    assert refresh(repo, {"boot": MET_BOOT, "metalharbor": MH_GOAL}, capsys)[0] == 0
    commit(repo, "baseline")
    doc = json.loads((repo / RR.BASELINE_REL).read_text())
    doc["replays"] = [r for r in doc["replays"] if r["name"] != "boot"]
    write(repo, RR.BASELINE_REL, json.dumps(doc))
    write(repo, RR.REGISTRY_REL, registry("metalharbor"))
    code, out = run(repo, {"metalharbor": MH_GOAL}, capsys=capsys)
    assert code == 1 and "DROPPED boot" in out


@pytest.mark.parametrize("decision, to", [
    ("Documentation/decisions/agent.md", reading("boot", tick=4)),
    ("Documentation/decisions/user.md", reading("boot", tick=5)),
])
def test_a_hand_written_accepted_record_needs_the_user_s_decision_and_the_move(repo, capsys,
                                                                              decision, to):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    worse = reading("boot", tick=4)
    write(repo, "Documentation/decisions/agent.md", AGENT + ACCEPT4)
    write(repo, "Documentation/decisions/user.md", USER + ACCEPT4)
    doc = json.loads((repo / RR.BASELINE_REL).read_text())
    doc["replays"][0].update(RR.reading_of(worse),
                             accepted=[{"decision": decision, "from": RR.reading_of(MET_BOOT),
                                        "to": RR.reading_of(to)}])
    write(repo, RR.BASELINE_REL, json.dumps(doc))
    code, out = run(repo, {"boot": worse}, capsys=capsys)
    assert code == 1 and "HISTORY boot" in out


def test_a_goal_the_stage_boot_receipt_says_was_missed_reads_false(tmp_path, stand_ins):
    synthetic_tree(tmp_path, CLEAN_BOOT, mh_goal=False)
    readings, _ = RR.current_readings(tmp_path)
    assert readings["metalharbor"]["goal_complete"] is False


def test_history_that_cannot_be_read_is_missing(repo, capsys, monkeypatch):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    real = RR.git
    monkeypatch.setattr(RR, "git", lambda root, *a: real(root, "log", "--no-such-flag")
                        if a[0] == "log" else real(root, *a))
    code, out = run(repo, {"boot": MET_BOOT}, capsys=capsys)
    assert code == 1 and "MISSING" in out


def test_render_is_the_rendering_metrics_writes():
    import stage_exit as SE
    recs = [{"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
             "class": None},
            {"name": "boot", "ticks": 1200, "first_divergent_tick": 381, "field": "d_x",
             "class": "bug"}]
    for rec in recs:
        want = (SE.m5_part(rec) if hasattr(SE, "m5_part") else
                ("boot no divergence/1200" if rec["first_divergent_tick"] is None
                 else "boot 381/1200 (bug: d_x)"))
        assert RR.render(rec) == want


def test_no_decision_on_record_authorises_anything_for_a_registered_replay(repo, capsys):
    """Every decision in the repository, as it stands: none retires a replay or
    accepts a reading of one, whatever it says about `boot` or `metalharbor`."""
    readings = [MET_BOOT, reading("boot", tick=4), MH_GOAL, dict(MH_GOAL, goal_complete=False)]
    decisions = sorted(REAL_DECISIONS.glob("*.md"))
    assert len(decisions) >= 19
    for path in decisions:
        rel = f"Documentation/decisions/{path.name}"
        write(repo, rel, path.read_text())
        for name in ("boot", "metalharbor"):
            assert RR.decision_problems(repo, rel, name, RR.retires(name)), rel
            for r in readings:
                assert RR.decision_problems(repo, rel, name, RR.accepts(name, r)), rel


def test_the_sentence_may_wrap_and_the_date_paragraph_may_too(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/user.md",
          "# Boot at tick 4\n\nDate: 2026-10-01. Stage S5. Recorded by the agent; made by\n"
          "the user in their words below.\n\n> accept it\n\naccepts replay `boot` reading\n"
          "`boot 4/1200 (bug:\nd_01750000)` (does not meet\nM5), judged over 387 ticks,\nno goal.\n")
    code, out = refresh(repo, {"boot": reading("boot", tick=4)}, capsys, "--accept-regression",
                        "Documentation/decisions/user.md")
    assert code == 0 and "accepted: boot" in out, out


def test_the_user_phrase_outside_the_date_paragraph_does_not_count(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/agent.md",
          "Date: 2026-10-01. Stage S5. Made by the agent.\n\nThe tick-4 reading was made by "
          "the user's own recording.\n\n" + ACCEPT4)
    code, out = refresh(repo, {"boot": reading("boot", tick=4)}, capsys, "--accept-regression",
                        "Documentation/decisions/agent.md")
    assert code == 1 and "in its Date paragraph" in out


def test_a_decision_for_one_window_does_not_accept_a_narrower_one(repo, capsys):
    """Same M5 part, judged over less: the sentence names the window, so the
    decision that accepted 387 ticks does not accept 200."""
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/user.md", USER + ACCEPT4)
    narrower = reading("boot", tick=4, judged=200)
    code, out = refresh(repo, {"boot": narrower}, capsys, "--accept-regression",
                        "Documentation/decisions/user.md")
    assert code == 1 and "judged over 200 ticks, no goal" in out


def test_a_replay_name_is_matched_exactly(repo, capsys):
    write(repo, RR.REGISTRY_REL, registry("boot", "metalharbor"))
    assert refresh(repo, {"boot": reading("boot"), "metalharbor": MH_GOAL}, capsys)[0] == 0
    commit(repo, "baseline")
    write(repo, "Documentation/decisions/user.md", USER + "Retires replay `Boot`.\n")
    write(repo, RR.REGISTRY_REL, registry(
        "metalharbor", retired=[{"name": "boot", "decision": "Documentation/decisions/user.md"}]))
    code, out = run(repo, {"metalharbor": MH_GOAL}, capsys=capsys)
    assert code == 1 and "DROPPED boot" in out


def test_refresh_keeps_the_accepted_records_it_found(repo, capsys):
    assert refresh(repo, {"boot": MET_BOOT}, capsys)[0] == 0
    commit(repo, "baseline")
    worse = reading("boot", tick=4)
    write(repo, "Documentation/decisions/user.md", USER + ACCEPT4)
    assert refresh(repo, {"boot": worse}, capsys, "--accept-regression",
                   "Documentation/decisions/user.md")[0] == 0
    commit(repo, "accepted")
    assert refresh(repo, {"boot": dict(worse, first_divergent_tick=5,
                                       m5="boot 5/1200 (bug: d_01750000)")}, capsys)[0] == 0
    doc = json.loads((repo / RR.BASELINE_REL).read_text())
    assert [a["decision"] for a in doc["replays"][0]["accepted"]] == [
        "Documentation/decisions/user.md"]


def test_the_marker_moves_with_a_ratcheted_replay_s_goal_and_window():
    base = {"metalharbor": MH_GOAL}
    assert RR.marker(base) != RR.marker({"metalharbor": dict(MH_GOAL, goal_complete=False)})
    met = dict(MH_GOAL, m5_met=True)                   # ratcheted either way: only the goal moves
    assert RR.marker({"metalharbor": met}) != RR.marker(
        {"metalharbor": dict(met, goal_complete=False)})
    assert RR.marker(base) != RR.marker({"metalharbor": dict(MH_GOAL, judged_ticks=100)})
    assert RR.marker(base) != RR.marker({"metalharbor": dict(MH_GOAL, compared_words=5)})


def test_the_words_compared_come_from_the_result_s_mask(tmp_path, stand_ins):
    synthetic_tree(tmp_path, dict(CLEAN_BOOT, mask={"words": 644081, "mask_sha256": "0" * 64}))
    readings, stale = RR.current_readings(tmp_path)
    assert stale == [] and readings["boot"]["compared_words"] == 644081


def test_render_is_what_metrics_writes_into_the_m5_row(tmp_path):
    """metrics.py's own m5_replays over a replay-results file, against render: the
    gate ties METRICS.md's row to the receipt by this rendering, before and after
    RC moves it into stage_exit.m5_part."""
    import metrics
    shapes = [
        [{"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
          "class": None},
         {"name": "metalharbor", "ticks": 15585, "first_divergent_tick": 338,
          "field": "d_01940000", "class": "bug"}],
        # the records tick_replay writes under residue classes (RC): each names the
        # classes seen, which RC's rendering writes into the row and HEAD's ignores
        [{"name": "boot", "ticks": 1200, "first_divergent_tick": None, "field": None,
          "class": "residue", "residues": ["host-timing-v1"]},
         {"name": "metalharbor", "ticks": 15585, "first_divergent_tick": 338,
          "field": "d_01940000", "class": "bug",
          "residues": ["host-services-steam-stats-v1", "host-timing-v1"]}],
    ]
    for recs in shapes:
        write(tmp_path, metrics.REPLAY_RESULTS, json.dumps({"replays": recs}))
        assert metrics.m5_replays(tmp_path).value == "; ".join(RR.render(r) for r in recs)
