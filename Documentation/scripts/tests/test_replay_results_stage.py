"""A stage replay through the replay-results gate, M4's per-entry lifted log, and M6's stage runs.

No stage replay is registered until the user's Metal Harbor recording is, so
the falsifications Documentation/scripts/check_replay_results.py aims at a
stage entry (its check 6, and M4's per-entry lifted log) cannot run on the
tree's own records yet, and the gate counts them as not applicable. Here they
run against one made-up stage replay, recorded by tools/s4/tick_replay.py's own
replay() with loop_run replaced by a stand-in: the control must pass, and every
case check_replay_results.stage_cases builds must be caught by a problem the
control does not have. Also here: tools/s4/import_status.py taking a stage
entry's lifted log from its own run's table, and tools/s4/stage_boot.py's
stage runs starting from the pinned saves.

Every capture, trace, table and value is made up (AGENTS.md rule 9). The
native checks that read an oracle capture's receipt -- the probe, the oracle
mode, the pinned saves, the device profile -- have their own tests
(test_tick_replay.py, test_oracle_mode.py, test_lifted_saves.py,
test_device_profile_replay.py) and are answered here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_replay_results as CRR  # noqa: E402
import import_status as IS  # noqa: E402
import native_imports as NI  # noqa: E402
import native_outcomes as NO  # noqa: E402
import stage_boot as SB  # noqa: E402
import stage_run as SR  # noqa: E402
import tick_replay as T  # noqa: E402

NAME = "metalharbor-test"
GOAL, TICKS = 40, 60
INPUT_HEADER = "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old"
PROFILE = {"path": "private/captures/run0/sa2-device-profile.txt", "sha256": "a" * 64,
           "guest": {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"},
           "counts": {}}
STAGE_ROWS = [{"dll": "k.dll", "name": "f", "implemented": True, "calls": 3},
              {"dll": "k.dll", "name": "g", "implemented": True, "calls": 0}]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace_text(n: int, player: int = 0x03900000) -> str:
    lines = [",".join(SB.TRACE_HEADER)] + [
        f"{t},0x000a,0x00000005,0x0010,0x{t:08x},0x{player:08x},0x{0x41200000 + t:08x},"
        "0x00000000,0x00000000" for t in range(1, n + 1)]
    return "\n".join(lines) + "\n"


def capture_text(n: int) -> str:
    return "tick,frame\n" + "".join(f"{t},0x{t:08x}\n" for t in range(1, n + 1))


def input_text() -> str:
    rows = [INPUT_HEADER] + [f"{t},0,0x{t:x}" + ",0x0" * 10 for t in range(TICKS + 1)]
    return "\n".join(rows) + "\n"


def applied_text(polls: int) -> str:
    out = [",".join(T.APPLIED_HEADER)]
    for t in range(polls):
        matched = t <= TICKS
        out.append(f"{t},0,{int(matched)},0x{t if matched else 0:x}" + ",0x0" * 10)
    return "\n".join(out) + "\n"


def put(root: Path, rel: str, text: str) -> dict:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return {"file": rel, "sha256": sha(p)}


def stage_entry(root: Path) -> dict:
    """Two native replays of the recording in private/captures/rec, each with its
    tick capture, end record, stage trace and applied input beside it, and the
    receipt that records its input probe's build and the applied input it
    collected."""
    caps = []
    for k in range(2):
        d = f"private/captures/run{k}"
        cap = put(root, f"{d}/sa2-tick-capture.csv", capture_text(TICKS))
        end = put(root, f"{d}/sa2-input-end.csv",
                  f"goal_tick,end_tick,misses\n{GOAL},{GOAL + 300},0\n")
        trace = put(root, f"{d}/sa2-stage-trace.csv", trace_text(TICKS))
        applied = put(root, f"{d}/sa2-input-applied.csv", applied_text(TICKS + 1))
        (root / d / "receipt.json").write_text(json.dumps({
            "cwd": "/c", "command": "wine-env runinprefix ./sonic2app-unpacked.exe",
            "inputs": {"built_probes": [{"mod_id": "input_probe", "defines": [
                "INPUT_MODE=2", 'INPUT_SCRIPT="/x/input-scripts/rec.h"', "TICK_LIMIT=400"]}]},
            "artifacts": [{"file": "sa2-input-applied.csv",
                           "check": {"sha256": applied["sha256"]}}]}))
        caps.append({"capture": cap["file"], "sha256": cap["sha256"],
                     "receipt": f"{d}/receipt.json", "end": end, "stage_trace": trace,
                     "input_applied": applied})
    script = put(root, "private/captures/rec/sa2-input.csv", input_text())
    return {"name": NAME, "stage": {"name": "metalharbor", "level": 10},
            "input": {"script": script["file"], "sha256": script["sha256"]},
            "oracle": "vanilla", "native": dict(caps[0], repeats=caps[1:]),
            "field_policy": {}, "residues": []}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """tick_replay, stage_run and the gate pointed at tmp_path; the native
    checks that read oracle receipts answered; loop_run a stand-in that writes
    what a stage run writes; M4's native side a made-up log and pre-Init set."""
    import rebuild_image as RI
    for var in set(T.DP.OWNED) | set(T.RR.DEVICE_VARS):
        monkeypatch.delenv(var, raising=False)
    for mod in (T, SR, CRR):
        monkeypatch.setattr(mod, "ROOT", tmp_path)
    for const, rel in (("RECEIPT", CRR.RECEIPT_REL), ("REGISTRY", "Documentation/replays.json"),
                       ("RESULTS", "private/generated/replay-results.json"),
                       ("IMPORT_STATUS", "private/generated/import-status.json")):
        monkeypatch.setattr(CRR, const, tmp_path / rel)
    for mod in (T, SR):
        monkeypatch.setattr(mod, "OUT", tmp_path / "replay")
        monkeypatch.setattr(mod, "RUNDIR", tmp_path / "run")
    monkeypatch.setattr(RI, "staged_not_rebuilt", lambda: [])
    monkeypatch.setattr(T.SS, "reset_lifted", lambda cmd, root: {"removed": {}, "left": {}})
    for f in ("native_probe_current", "native_oracle", "native_start"):
        monkeypatch.setattr(T, f, lambda *a, **k: None)
    # The synthetic entry has no digest mask of its own, which tick_replay
    # refuses (stage_mask_problems; test_tick_replay.py holds that): answered
    # here, so what runs past it is what is tested.
    monkeypatch.setattr(T, "stage_mask_problems", lambda entry, root=None: [])
    monkeypatch.setattr(T, "native_device_profile", lambda entry: PROFILE)
    monkeypatch.setattr(T, "inputs", lambda entry, root=None: {"lifted": {"l": 1},
                                                                "compare": {"c": 1}})
    (tmp_path / "run").mkdir()

    def fake_run(cmd, **kw):
        spec = cmd[cmd.index("--run") + 1].split("|")
        label, env = spec[0], dict(kv.split("=", 1) for kv in spec[1:])
        build = cmd[cmd.index("--name") + 1]
        polls = int(env["SA2_PUMP_POLLS"])
        record = {"pump": {"iterations": polls, "quit_by_host": 1, "poll_limit": polls},
                  "calls": {"total": 777}, "d3d9": {"frames": {"present": polls},
                                                    "device_enabled": True,
                                                    "constant_tables": {"switch": True}},
                  "trap": {"note": ""},
                  "device_profile": {"default": False, "source": env["SA2_DEVICE_PROFILE"],
                                     "sha256": "a" * 64},
                  "virtual": {"cwd": env.get("SA2_GUEST_CWD", "C:\\SA2"),
                              "exe_path": env.get("SA2_GUEST_EXE_PATH", "C:\\SA2\\sonic2app.exe")},
                  **{k: {"device_enabled": True} for k in ("dsound", "dinput", "xaudio2",
                                                            "ddraw")}}
        (tmp_path / "run" / f"{build}.{label}.json").write_text(json.dumps(record))
        (tmp_path / "run" / f"{build}.matrix.json").write_text(json.dumps({"sha256": "c" * 64}))
        table = next(a.split("=", 1)[1] for a in cmd if a.endswith(".table.json"))
        (tmp_path / table).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / table).write_text(json.dumps({"rows": STAGE_ROWS}))
        texts = {"SA2_REPLAY_TICKS": capture_text(polls - 1),
                 "SA2_STAGE_TRACE": trace_text(polls - 1, player=0x04001cd0),
                 "SA2_GOAL_LATCH": "tick,goal_latch\n" + "".join(
                     f"{t},{'0x01' if t >= GOAL else '0x00'}\n" for t in range(polls)),
                 "SA2_INPUT_APPLIED": applied_text(polls)}
        for var, text in texts.items():
            if var in env:
                (tmp_path / env[var]).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / env[var]).write_text(text)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(T.subprocess, "run", fake_run)
    # M4's native side for the made-up replay: a log that calls nothing, and a
    # pre-Init set that holds nothing, so the stage run's own table decides.
    outcomes = tmp_path / NO.RECEIPT_REL
    outcomes.parent.mkdir(parents=True, exist_ok=True)
    outcomes.write_text(json.dumps({"schema": NO.SCHEMA, "id": NO.RECEIPT_ID, "logs": [],
                                    "outcomes": [], "host": {}, "devices": {},
                                    "native_pre_init": {"imports": []}}))
    monkeypatch.setattr(NI, "replay_profile", lambda e, root, outcomes: {"sha256": "a" * 64})
    monkeypatch.setattr(NI, "replay_calls", lambda e, table, root, probe: [])
    return tmp_path


def recorded(root: Path) -> dict:
    """The gate's inputs over one registered stage replay, as the tools write
    them: the registry, the tick-replay receipt from replay() itself, M5's
    input, and M4's import status from import_status's own functions."""
    entry = stage_entry(root)
    registry = {"replays": [entry], "retired": []}
    reg = root / "Documentation" / "replays.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps(registry))
    doc = T.replay(entry, run=True)
    receipt = {"id": "s4-tick-replay-v1", "registry_sha256": sha(reg), "replays": [doc],
               "m5": [T.m5_record(doc)]}
    reach = put(root, "private/run/reach.imports.table.json",
                json.dumps({"rows": [dict(r, calls=0) for r in STAGE_ROWS]}))
    rows = json.loads((root / reach["file"]).read_text())["rows"]
    implemented, not_counted = IS.counted(rows, json.loads(IS.REASONS.read_text()))
    reach_doc = IS.replay_reach(registry, rows, root=root, tick_replay=receipt)
    status = {"source": reach["file"], "source_sha256": reach["sha256"],
              "implemented": implemented, "not_counted": not_counted,
              "reached": reach_doc["reached"], "replay_reach": reach_doc}
    return {"receipt": receipt, "registry": registry, "results": {"replays": receipt["m5"]},
            "live": {NAME: T.inputs(entry)}, "registry_sha": sha(reg), "committed": [],
            "outputs": False, "poll_budget": None, "status": status, "probe": None}


def test_the_made_up_stage_replay_is_recorded_as_the_tools_record_it(world):
    base = recorded(world)
    doc = base["receipt"]["replays"][0]
    assert doc["result"]["first_divergence"] is None
    assert doc["result"]["trace"]["first_divergence"] is None
    assert doc["lifted"]["input"]["problems"] == []
    assert doc["non_perturbation"]["equal"] is True
    assert base["status"]["replay_reach"]["lifted_by_replay"] == {NAME: ["k.dll!f"]}
    assert base["status"]["reached"] == ["k.dll!f"]


def test_the_control_passes_every_check(world):
    base = recorded(world)
    for outputs in (False, True):
        docs = dict(copy.deepcopy(base), outputs=outputs)
        assert CRR.run_docs(docs) == [], outputs


def test_every_stage_falsification_is_caught_by_a_problem_the_control_lacks(world):
    base = recorded(world)
    scratch: list[Path] = []
    try:
        cases, inapplicable = CRR.stage_cases(base, scratch)
        assert inapplicable == []
        assert len(cases) == 16
        assert CRR.run_cases(base, cases) == []
    finally:
        for d in scratch:
            import shutil
            shutil.rmtree(d, ignore_errors=True)


def test_each_stage_case_is_caught_by_the_check_it_names(world):
    """Not only caught, but by the right sentence: each case's new problems
    name what it moved."""
    base = recorded(world)
    want = {
        "both runs' recorded guest directory edited": "recorded guest directory is not",
        "a stage capture longer than its own budget": "does not outlast the native capture",
        "the baseline's import calls moved by one": "the baseline run's recorded reach",
        "the baseline's import table re-pinned": "baseline run's import table",
        "its recorded trace comparison edited": "recorded comparison is not what its kept",
        "the lifted stage trace moved and nothing re-pinned": "stage trace",
        "the goal-latch column moved and nothing re-pinned": "goal-latch column",
        "an applied input row flipped to a miss": "found no recorded row",
        "a recorded input row dropped from the applied log": "where the recording's row",
        "a repeat's end record re-pinned": "end record",
        "two end records naming different goal ticks": "name goal ticks",
        "the registered capture listed again as a repeat": "one run listed 2 times",
        "a repeat that replayed another recording": "was not built to replay rec",
        "a repeat's applied input from another recording, re-pinned":
            "the native replay applied other values than the recording's",
        "M4's lifted log from its table re-pinned": "its lifted run's import table",
        "M4's reached set dropped an import only its lifted run called":
            "is not what the replays' call logs give",
    }
    control = {o: set(CRR.run_docs(dict(copy.deepcopy(base), outputs=o))) for o in (False, True)}
    scratch: list[Path] = []
    try:
        for name, docs in CRR.stage_cases(base, scratch)[0]:
            new = set(CRR.run_docs(docs)) - control[docs["outputs"]]
            key = name.split(": ", 1)[1]
            assert any(want[key] in p for p in new), (name, new)
    finally:
        import shutil
        for d in scratch:
            shutil.rmtree(d, ignore_errors=True)


def test_the_gate_reports_what_an_entry_s_controls_check_finds(world, monkeypatch):
    """The U6 fold review's CR1: every pass holds each entry's controls to what it
    records of them (tick_replay.control_problems, tested there), and says so by
    the replay's name."""
    base = recorded(world)
    docs = dict(copy.deepcopy(base), outputs=True)
    assert CRR.run_docs(docs) == []
    monkeypatch.setattr(T, "control_problems", lambda entry, root=None: ["x"])
    assert CRR.run_docs(docs) == [f"replay {NAME}: x"]


def test_a_stage_entry_with_a_word_policy_gets_the_three_registry_cases(world):
    """The U6 fold review's CR5: the made-up stage entry has no word policy, so
    the cases stage_cases builds for one (the U6 registration review, W5) run on
    the live registry alone; here the entry has one, and a boot entry with a
    repeat sits beside it, and each case moves what its name says."""
    base = recorded(world)
    boot_repeat = {"capture": "private/captures/boot1/sa2-tick-capture.csv", "sha256": "b" * 64}
    base["registry"]["replays"].insert(0, {"name": "boot", "native": {
        "capture": "private/captures/boot0/sa2-tick-capture.csv", "repeats": [boot_repeat]}})
    policy = {"identity": T.IDENTITY_RECORD, "compare": "identity", "evidence": "ruled"}
    base["registry"]["replays"][1]["word_policy"] = policy
    scratch: list[Path] = []
    try:
        got = dict(CRR.stage_cases(base, scratch)[0])
    finally:
        import shutil
        for d in scratch:
            shutil.rmtree(d, ignore_errors=True)
    want = [f"{NAME}: its word_policy dropped", f"{NAME}: its word_policy emptied",
            f"{NAME}: a boot capture appended as a repeat"]
    assert [n for n in got if n in want] == want
    entry = {n: got[n]["registry"]["replays"][1] for n in want}
    assert "word_policy" not in entry[want[0]] and entry[want[1]]["word_policy"] == {}
    assert entry[want[2]]["word_policy"] == policy
    assert entry[want[2]]["native"]["repeats"][-1] == boot_repeat
    assert entry[want[2]]["native"]["repeats"][:-1] == \
        base["registry"]["replays"][1]["native"]["repeats"]
    assert [got[n]["names"] for n in want] == [
        ("has no word_policy",), ("its word_policy must name the identity record",),
        ("not configured like",)]
    assert all(got[n]["outputs"] for n in want)


def test_with_no_stage_entry_the_stage_cases_are_none():
    base = {"registry": {"replays": [{"name": "boot"}]}, "receipt": {"replays": []},
            "status": {}}
    assert CRR.stage_cases(base, []) == ([], [])


# ------------------------------------------------ M4: a stage entry's own table

def tables(root: Path, stage_rows: list[dict], reach_rows: list[dict]) -> tuple[dict, list]:
    t = put(root, "replay/s/imports.table.json", json.dumps({"rows": stage_rows}))
    receipt = {"replays": [{"name": NAME, "lifted": {"import_table": {"path": t["file"],
                                                                      "sha256": t["sha256"]}}}]}
    return receipt, reach_rows


def test_a_stage_entry_s_lifted_log_is_its_own_run_s_table(tmp_path):
    receipt, reach = tables(tmp_path, STAGE_ROWS, [dict(r, calls=0) for r in STAGE_ROWS])
    got = IS.lifted_logs([{"name": "boot"}, {"name": NAME, "stage": {}}], reach, tmp_path,
                         receipt)
    assert got == {"boot": [], NAME: ["k.dll!f"]}
    # boot's is the reach table's, whatever the stage run called
    reach[1] = dict(reach[1], calls=5)
    assert IS.lifted_logs([{"name": "boot"}], reach, tmp_path, receipt) == {"boot": ["k.dll!g"]}


def test_a_stage_table_that_is_not_its_pin_is_refused(tmp_path):
    receipt, reach = tables(tmp_path, STAGE_ROWS, [dict(r, calls=0) for r in STAGE_ROWS])
    receipt["replays"][0]["lifted"]["import_table"]["sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="missing or does not hash to its pin"):
        IS.lifted_logs([{"name": NAME, "stage": {}}], reach, tmp_path, receipt)
    receipt["replays"][0]["lifted"].pop("import_table")
    with pytest.raises(SystemExit, match="missing or does not hash to its pin"):
        IS.lifted_logs([{"name": NAME, "stage": {}}], reach, tmp_path, receipt)


def test_a_stage_table_from_another_runtime_is_refused(tmp_path):
    """Both tables come from one runtime, so their implemented lists match."""
    receipt, reach = tables(tmp_path, STAGE_ROWS, [dict(STAGE_ROWS[0], calls=0)])
    with pytest.raises(SystemExit, match="different runtimes"):
        IS.lifted_logs([{"name": NAME, "stage": {}}], reach, tmp_path, receipt)


# ------------------------------------------------------- M6's stage runs

@pytest.fixture
def stage_boot_run(tmp_path, monkeypatch):
    """stage_boot.run_stage with loop_run a stand-in: its environment and the
    saves reset it asked for are kept in the returned dict."""
    for var in set(T.DP.OWNED) | set(T.RR.DEVICE_VARS):
        monkeypatch.delenv(var, raising=False)
    for mod in (SB, SR):
        monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(SR, "OUT", tmp_path / "replay")
    monkeypatch.setattr(SR, "RUNDIR", tmp_path / "run")
    (tmp_path / "run").mkdir()
    seen: dict = {"left": {}}

    def reset(cmd, root):
        seen["reset"] = cmd
        return {"removed": {}, "left": dict(seen["left"])}

    monkeypatch.setattr(SB.SS, "reset_lifted", reset)

    def fake(cmd, **kw):
        seen["env"] = kw.get("env")
        spec = cmd[cmd.index("--run") + 1].split("|")
        env = dict(kv.split("=", 1) for kv in spec[1:])
        polls = int(env["SA2_PUMP_POLLS"])
        (tmp_path / "run" / f"stage-metalharbor.{spec[0]}.json").write_text(json.dumps(
            {"pump": {"iterations": polls, "poll_limit": polls, "quit_by_host": 1,
                      "empty_polls": polls - 1}, "trap": {"note": "x"}}))
        (tmp_path / env["SA2_STAGE_TRACE"]).write_text(trace_text(polls - 1))
        (tmp_path / env["SA2_REPLAY_TICKS"]).write_text(capture_text(polls - 1))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(SB.subprocess, "run", fake)
    return seen


def test_a_stage_boot_run_starts_from_the_pinned_saves_and_an_empty_overlay(tmp_path,
                                                                            stage_boot_run):
    got = SB.run_stage("metalharbor")
    assert stage_boot_run["reset"][:2] == [SR.PY, str(SR.HERE / "loop_run.py")]
    assert got["saves"] == {"removed": {}, "left": {}}
    assert got["booting"] is True
    # the run's environment is the tools' clean one, not the raw shell's
    assert stage_boot_run["env"] is not None
    assert not set(stage_boot_run["env"]) & set(T.RR.DEVICE_VARS)


def test_a_stage_boot_run_over_an_overlay_that_holds_anything_is_refused(tmp_path,
                                                                        stage_boot_run):
    stage_boot_run["left"] = {"Config/UserConfig.cfg": "0" * 64}
    with pytest.raises(SystemExit, match="STAGE_BOOT_REFUSED metalharbor"):
        SB.run_stage("metalharbor")
    assert "env" not in stage_boot_run          # refused before the run
