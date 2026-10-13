#!/usr/bin/env python3
"""tools/s7/refresh_after_runtime.py: the chain runs its steps in order, each step's run before
its check, stops at the first failure and says how to resume, and the real chain re-runs each
receipt by the command that receipt records."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("refresh_after_runtime_under_test",
                                               ROOT / "tools/s7/refresh_after_runtime.py")
RA = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = RA
_spec.loader.exec_module(RA)


def note(name: str, rc: int = 0) -> str:
    """A stub command: append `name` to trail.txt in the chain's root, then exit `rc`."""
    return f"echo {name} >> trail.txt; exit {rc}"


def trail(root: Path) -> list[str]:
    path = root / "trail.txt"
    return path.read_text().split() if path.is_file() else []


def stub_chain(fail: str | None = None) -> list:
    """Four steps, each a run and a check that record themselves; `fail` names the one command
    (as step:part) that exits 1."""
    out = []
    for name in ("one", "two", "three", "four"):
        out.append(RA.Step(name, f"stub {name}",
                           run=(note(f"{name}:run", 1 if fail == f"{name}:run" else 0),),
                           check=(note(f"{name}:check", 1 if fail == f"{name}:check" else 0),)))
    return out


def test_every_step_runs_in_order_each_run_before_its_check(tmp_path: Path, capsys) -> None:
    rc = RA.run_chain(stub_chain(), tmp_path, tmp_path / "logs")
    assert rc == 0
    assert trail(tmp_path) == ["one:run", "one:check", "two:run", "two:check",
                               "three:run", "three:check", "four:run", "four:check"]
    table = (tmp_path / "logs" / "chain.txt").read_text().splitlines()
    assert [row.split("\t")[:2] for row in table] == [["one", "0"], ["two", "0"],
                                                      ["three", "0"], ["four", "0"]]
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "01-one.log", "02-two.log", "03-three.log", "04-four.log", "chain.txt"]
    assert "REFRESH_OK steps=4" in capsys.readouterr().out


def test_the_first_failing_check_stops_the_chain_and_names_the_resume(tmp_path: Path,
                                                                      capsys) -> None:
    rc = RA.run_chain(stub_chain(fail="two:check"), tmp_path, tmp_path / "logs")
    assert rc == 1
    assert trail(tmp_path) == ["one:run", "one:check", "two:run", "two:check"]
    out = capsys.readouterr().out
    assert "REFRESH_FAILED two (exit 1" in out
    assert "resume with: .venv/bin/python tools/s7/refresh_after_runtime.py --from two" in out
    table = (tmp_path / "logs" / "chain.txt").read_text().splitlines()
    assert [row.split("\t")[:2] for row in table] == [["one", "0"], ["two", "1"]]
    log = (tmp_path / "logs" / "02-two.log").read_text()
    assert log.splitlines()[-1] == "# exit 1"


def test_a_failing_run_skips_its_own_check(tmp_path: Path) -> None:
    assert RA.run_chain(stub_chain(fail="three:run"), tmp_path, tmp_path / "logs") == 1
    assert trail(tmp_path) == ["one:run", "one:check", "two:run", "two:check", "three:run"]


def test_from_resumes_at_the_named_step(tmp_path: Path) -> None:
    assert RA.run_chain(stub_chain(), tmp_path, tmp_path / "logs", start="three") == 0
    assert trail(tmp_path) == ["three:run", "three:check", "four:run", "four:check"]
    # The log keeps the step's place in the whole chain.
    assert sorted(p.name for p in (tmp_path / "logs").iterdir()) == [
        "03-three.log", "04-four.log", "chain.txt"]


def test_only_and_an_unknown_step(tmp_path: Path, capsys) -> None:
    assert RA.run_chain(stub_chain(), tmp_path, tmp_path / "logs", only=["two", "four"]) == 0
    assert trail(tmp_path) == ["two:run", "two:check", "four:run", "four:check"]
    assert RA.run_chain(stub_chain(), tmp_path, tmp_path / "logs", start="five") == 2
    assert "REFRESH_REFUSED no step 'five'" in capsys.readouterr().out
    assert trail(tmp_path) == ["two:run", "two:check", "four:run", "four:check"]


def test_a_step_whose_output_exists_is_skipped(tmp_path: Path, capsys) -> None:
    (tmp_path / "done.txt").write_text("")
    chain = stub_chain()
    chain[1] = RA.Step("two", "stub two", run=(note("two:run"),), skip_if="done.txt")
    assert RA.run_chain(chain, tmp_path, tmp_path / "logs") == 0
    assert trail(tmp_path) == ["one:run", "one:check", "three:run", "three:check",
                               "four:run", "four:check"]
    assert "REFRESH two: skipped, done.txt is there" in capsys.readouterr().out


def test_dry_run_runs_nothing(tmp_path: Path, capsys) -> None:
    assert RA.run_chain(stub_chain(), tmp_path, tmp_path / "logs", dry_run=True) == 0
    assert trail(tmp_path) == [] and not (tmp_path / "logs").exists()
    assert "REFRESH one: would run echo one:run >> trail.txt; exit 0" in capsys.readouterr().out


def test_a_pipeline_that_fails_inside_fails_the_step(tmp_path: Path) -> None:
    chain = [RA.Step("pipe", "a failing command piped into a passing one",
                     run=("false | cat",)), RA.Step("after", "never", run=(note("after"),))]
    assert RA.run_chain(chain, tmp_path, tmp_path / "logs") == 1
    assert trail(tmp_path) == []


# ---------------------------------------------------------------- the real chain

def test_the_real_chain_is_in_dependency_order() -> None:
    names = [s.name for s in RA.steps()]
    assert names == ["reach", "tick-replays", "stage-boot", "poll-handoff",
                     "m7-record-metalharbor", "m7-record-release", "m7-evidence", "m7-inputs",
                     "corpus", "drm-unwrap", "target-x86_64-linux", "targets", "ledgers"]


@pytest.mark.parametrize("step,receipt", [
    ("stage-boot", "Documentation/receipts/s4-stage-boot-v1.json"),
    ("poll-handoff", "Documentation/receipts/s5-newfile-wait-v1.json"),
    ("m7-inputs", "Documentation/receipts/d3d9-methods.json"),
    ("drm-unwrap", "Documentation/receipts/s7-drm-unwrap-v1.json"),
    ("target-x86_64-linux", "Documentation/receipts/s7-target-x86_64-linux-v1.json"),
])
def test_each_step_runs_the_command_its_receipt_records(step: str, receipt: str) -> None:
    recorded = json.loads((ROOT / receipt).read_text())["command"]
    run = {s.name: s.run for s in RA.steps()}[step]
    assert run[0] == recorded


def test_each_receipt_step_is_held_by_its_gate() -> None:
    checks = {s.name: s.check for s in RA.steps()}
    assert checks["reach"] == (".venv/bin/python Documentation/scripts/check_run_reach.py",)
    assert checks["tick-replays"] == (
        ".venv/bin/python Documentation/scripts/check_replay_results.py",
        ".venv/bin/python Documentation/scripts/check_replay_regression.py")
    assert checks["stage-boot"] == (".venv/bin/python Documentation/scripts/check_stage_results.py",)
    assert checks["poll-handoff"] == (".venv/bin/python Documentation/scripts/check_poll_handoff.py",)
    assert checks["m7-inputs"] == (".venv/bin/python Documentation/scripts/check_m7_inputs.py",)
    # targets.json is held by metrics.py rendering it (m8_targets refuses one targets.py did not
    # write); `metrics.py --check` would compare with METRICS.md, which ship.py regenerates.
    assert checks["targets"] == (".venv/bin/python tools/s7/targets.py --check",
                                 ".venv/bin/python Documentation/scripts/metrics.py")
    # Every other check is a gate of run_all_gates.py's list, or the receipt tool's own --check.
    sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
    import run_all_gates as G  # noqa: E402
    gated = {f".venv/bin/python Documentation/scripts/{c[0]}" for _, c in G.GATES
             if len(c) == 1 and "/" not in c[0]}
    for s in RA.steps():
        for c in s.check:
            assert c in gated or c.endswith(" --check") or c.endswith("/metrics.py"), (s.name, c)


def test_the_m7_recordings_go_where_the_evidence_refresh_finds_them() -> None:
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import m7_inputs as M  # noqa: E402
    import m7_synthetic as MS  # noqa: E402
    key = MS.recording_key(ROOT, M.tree_runtime_digest(ROOT))
    by = {s.name: s for s in RA.steps()}
    for step, name in (("m7-record-metalharbor", "metalharbor"), ("m7-record-release", "release")):
        assert by[step].skip_if == f"{MS.EVIDENCE_ROOT}/record-{key}/{name}/present-run.json"
        assert f"record-{key}/{name}" in by[step].run[0]


def test_a_step_waits_for_memory_before_it_starts(tmp_path: Path, capsys) -> None:
    readings = iter([2.0, 5.0, 9.0, 12.0, 30.0])
    slept = []
    chain = stub_chain()[:2]
    assert RA.run_chain(chain, tmp_path, tmp_path / "logs", min_available_gb=8,
                        available=lambda: next(readings), sleep=slept.append) == 0
    # The first step waited through two short readings; the second started at once.
    assert slept == [60.0, 60.0]
    assert trail(tmp_path) == ["one:run", "one:check", "two:run", "two:check"]
    out = capsys.readouterr().out
    assert "REFRESH one: waiting, 2.0 GB available, 8 GB wanted" in out
    assert "REFRESH one: waiting, 5.0 GB available, 8 GB wanted" in out
    assert "REFRESH two: waiting" not in out


def test_available_memory_is_read_from_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       32000000 kB\nMemAvailable:    8388608 kB\n")
    assert RA.available_gb(meminfo) == 8.0
    assert RA.available_gb(tmp_path / "absent") == float("inf")
