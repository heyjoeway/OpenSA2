"""The native import log: read only when it covers every slot exactly.

`tools/s4/native_imports.py` turns `harness/replay/native_import_probe.c`'s
per-slot counts into the imports a native replay called, which M4's
replay-reachable set is built from. Each refusal here is a way a log could
make that set smaller than the run's without saying so.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import native_imports as NI  # noqa: E402

TABLE = {0x1000: "A.dll!f", 0x1004: "A.dll!g", 0x2000: "B.dll!Ordinal#4"}


def log(tmp_path: Path, rows: list[str], header: str = "slot,calls,ours,tick") -> Path:
    p = tmp_path / NI.LOG
    p.write_text("\n".join([header, *rows]) + "\n")
    return p


GOOD = ["0x00001000,5,1,9", "0x00001004,0,1,9", "0x00002000,76,1,9"]


def test_a_whole_log_reads_and_zero_is_not_called(tmp_path: Path) -> None:
    p = log(tmp_path, GOOD)
    assert NI.read(p, TABLE) == {"A.dll!f": 5, "A.dll!g": 0, "B.dll!Ordinal#4": 76}
    assert NI.called(p, TABLE) == ["A.dll!f", "B.dll!Ordinal#4"]
    assert NI.read_at(p, TABLE)[1] == 9


@pytest.mark.parametrize("rows,header,why", [
    (GOOD, "slot,calls,ours", "header"),
    (["refused,0,0,0", "VirtualProtect on an import slot failed"], "slot,calls,ours,tick",
     "did not patch"),
    (GOOD[:2], "slot,calls,ours,tick", "absent"),
    ([*GOOD, "0x00003000,1,1,9"], "slot,calls,ours,tick", "not one of"),
    ([*GOOD, GOOD[0]], "slot,calls,ours,tick", "twice"),
    (["0x00001000,5,0,9", *GOOD[1:]], "slot,calls,ours,tick", "no longer held"),
    (["0x00001000,5,1", *GOOD[1:]], "slot,calls,ours,tick", "is not slot,calls,ours,tick"),
    (["0x00001000,5,1,8", *GOOD[1:]], "slot,calls,ours,tick", "at ticks"),
])
def test_a_log_that_does_not_cover_every_slot_is_refused(tmp_path, rows, header, why) -> None:
    with pytest.raises(SystemExit, match=why):
        NI.read(log(tmp_path, rows, header), TABLE)


def test_ordinal_imports_are_named_as_the_file_imports_them() -> None:
    """pefile names OLEAUT32's ordinals from its own table; the executable
    imports them by ordinal, and the runtime's table says `Ordinal#N`."""
    if not NI.EXE.is_file():
        pytest.skip(f"the unpacked executable is not present: {NI.EXE}")
    t = NI.slots()
    assert len(t) == 251
    assert t[0x00873250] == "OLEAUT32.dll!Ordinal#6"
    assert t[0x00873398] == "XINPUT1_3.dll!Ordinal#4"


TICKS = "tick,frame_count\n1,1\n2,2\n3,3\n"


def _entry(tmp_path: Path, sets: list[list[str]], tick_text: str = TICKS, at: int = 3,
           pads: list | None = None, exe: str | None = None) -> dict:
    """A registry entry whose logs, tick captures and receipts live in tmp_path."""
    rows = {"A.dll!f": "0x00001000", "A.dll!g": "0x00001004", "B.dll!Ordinal#4": "0x00002000"}
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "ticks.csv").write_text(TICKS)
    if exe is None:
        exe = json.loads(NI.PINS.read_text())["executables"]["sonic2app-unpacked.exe"]["sha256"]
    logs = []
    for i, called in enumerate(sets):
        d = tmp_path / f"run{i}"
        d.mkdir()
        (d / "ticks.csv").write_text(tick_text)
        p = d / NI.LOG
        p.write_text("slot,calls,ours,tick\n" + "".join(
            f"{rows[k]},{3 if k in called else 0},1,{at}\n" for k in rows))
        sha = NI.sha256(p)
        (d / "receipt.json").write_text(json.dumps({
            "artifacts": [{"file": NI.LOG, "check": {"sha256": sha}}],
            "session": {"host_game_controllers": [] if pads is None else pads},
            "inputs": {"executable_sha256": exe,
                       "built_probes": [{"source": str(NI.PROBE),
                                         "source_sha256": NI.sha256(NI.PROBE)}]}}))
        logs.append({"log": str(p.relative_to(tmp_path)), "sha256": sha,
                     "receipt": str((d / "receipt.json").relative_to(tmp_path))})
    imports = dict(logs[0], repeats=logs[1:])
    return {"name": "r", "native": {"capture": "x/ticks.csv",
                                    "sha256": NI.sha256(tmp_path / "x" / "ticks.csv"),
                                    "imports": imports}}


def test_a_registered_log_and_its_repeat_give_the_called_set(tmp_path: Path) -> None:
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]])
    assert NI.replay_calls(e, TABLE, tmp_path) == ["A.dll!f"]


def test_repeats_that_call_different_sets_are_refused(tmp_path: Path) -> None:
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f", "A.dll!g"]])
    with pytest.raises(SystemExit, match="different sets"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_a_run_whose_ticks_are_not_the_replays_is_refused(tmp_path: Path) -> None:
    """The tick capture of the counting run is the evidence that counting
    changed nothing the replay compares, and it is hashed from the file beside
    the log: a receipt is not pinned, so it is not asked."""
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], tick_text=TICKS + "4,5\n")
    with pytest.raises(SystemExit, match="perturbed"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_a_log_counted_short_of_the_replay_is_refused(tmp_path: Path) -> None:
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], at=2)
    with pytest.raises(SystemExit, match="judges 3 ticks"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_the_evdev_controller_list_is_not_the_device_question(tmp_path) -> None:
    """The runner's evdev list could not see the keyboard receiver Wine hands the
    game's joystick scan (2026-09-23), so the import log no longer asks it: the
    device set is `replay_profile`'s question, tested in test_native_outcomes.py."""
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], pads=["Example Pad"])
    assert NI.replay_calls(e, TABLE, tmp_path) == ["A.dll!f"]


# ---------------------------------------------------------------- the vanilla oracle

def _vanilla(monkeypatch, answer) -> list[Path]:
    """Stands tools/s4/tick_replay.py's native_vanilla_problems in with `answer`
    (capture directory -> problems), and returns the directories it was asked
    about. The replay tool is imported only for a vanilla entry, so a stub in
    sys.modules is what replay_calls gets."""
    asked: list[Path] = []

    def problems(capture_dir: Path) -> list[str]:
        asked.append(capture_dir)
        return answer(capture_dir)
    monkeypatch.setitem(sys.modules, "tick_replay",
                        types.SimpleNamespace(native_vanilla_problems=problems))
    return asked


def test_a_vanilla_entry_counts_from_runs_the_vanilla_check_accepts(tmp_path, monkeypatch):
    e = dict(_entry(tmp_path, [["A.dll!f"], ["A.dll!f"]]), oracle="vanilla")
    asked = _vanilla(monkeypatch, lambda d: [])
    assert NI.replay_calls(e, TABLE, tmp_path) == ["A.dll!f"]
    assert asked == [tmp_path / "run0", tmp_path / "run1"]


def test_a_vanilla_entry_whose_repeat_restored_nothing_is_refused(tmp_path, monkeypatch):
    e = dict(_entry(tmp_path, [["A.dll!f"], ["A.dll!f"]]), oracle="vanilla")
    _vanilla(monkeypatch, lambda d: ["its first built probe is not the vanilla probe"]
             if d.name == "run1" else [])
    with pytest.raises(SystemExit, match=r"run1/sa2-import-calls\.csv is not a vanilla capture: "
                                         "its first built probe is not the vanilla probe"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_an_entry_without_the_flag_is_not_held_to_the_vanilla_check(tmp_path, monkeypatch):
    """The user has not ruled on the vanilla oracle (the decision is a proposal),
    so the registered replays are read as the loader's until an entry says so."""
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]])
    asked = _vanilla(monkeypatch, lambda d: ["must not be asked"])
    assert NI.replay_calls(e, TABLE, tmp_path) == ["A.dll!f"]
    assert asked == []


def test_an_oracle_flag_this_tool_does_not_read_is_refused(tmp_path, monkeypatch) -> None:
    e = dict(_entry(tmp_path, [["A.dll!f"], ["A.dll!f"]]), oracle="loader")
    _vanilla(monkeypatch, lambda d: [])
    with pytest.raises(SystemExit, match="'loader' is not a registry flag"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_a_run_of_another_executable_is_refused(tmp_path: Path) -> None:
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], exe="0" * 64)
    with pytest.raises(SystemExit, match="pins"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_a_log_from_another_probe_is_refused(tmp_path: Path) -> None:
    e = _entry(tmp_path, [["A.dll!f"], ["A.dll!f"]])
    other = tmp_path / NI.PROBE.name
    other.write_bytes(NI.PROBE.read_bytes() + b"\n")
    with pytest.raises(SystemExit, match="now in the tree"):
        NI.replay_calls(e, TABLE, tmp_path, other)


def test_a_log_without_a_repeat_is_refused(tmp_path: Path) -> None:
    e = _entry(tmp_path, [["A.dll!f"]])
    with pytest.raises(SystemExit, match="no repeat"):
        NI.replay_calls(e, TABLE, tmp_path)


# ---------------------------------------------------------------- word-policy replays
#
# A replay with a word policy is compared from its word logs under its own
# digest mask, so the run that wrote an import log is held to the registered
# capture as that mask gives it (native_imports.word_captures), not by the
# stored file, whose digest columns follow the tree's boot mask.

def _word_entry(tmp_path: Path, sets: list[list[str]], stored: list[str]) -> dict:
    """_entry's logs, each run's own ticks.csv holding `stored[k]`, the runs
    registered as the capture (run0) and its repeats, with a word policy."""
    e = _entry(tmp_path, sets)
    caps = []
    for k, text in enumerate(stored):
        p = tmp_path / f"run{k}" / "ticks.csv"
        p.write_text(text)
        caps.append({"capture": f"run{k}/ticks.csv", "sha256": NI.sha256(p),
                     "receipt": f"run{k}/receipt.json"})
    e["native"].update(caps[0], repeats=caps[1:])
    e["word_policy"] = {"identity": "harness/replay/masks/identity-words.json",
                        "compare": "identity", "evidence": "ruled"}
    return e


def _answer(monkeypatch, tmp_path: Path, texts: dict[int, str]) -> None:
    """word_captures answered: run k's log gives texts[k] under the replay's mask."""
    run = {(tmp_path / f"run{k}").resolve(): t for k, t in texts.items()}
    monkeypatch.setattr(NI, "word_captures",
                        lambda entry, root: (run, run[(tmp_path / "run0").resolve()]))


def test_a_word_policy_run_is_held_to_its_capture_under_the_replay_s_own_mask(
        tmp_path, monkeypatch):
    """Two runs whose stored captures differ (the boot mask's digest columns)
    and whose logs give one capture under the replay's mask: the logs are the
    replay's. Without the word policy the same runs are refused."""
    stored = [TICKS, TICKS.replace("3,3", "3,4")]
    e = _word_entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], stored)
    _answer(monkeypatch, tmp_path, {0: "the capture", 1: "the capture"})
    assert NI.replay_calls(e, TABLE, tmp_path) == ["A.dll!f"]
    del e["word_policy"]
    with pytest.raises(SystemExit, match="perturbed"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_a_word_policy_run_whose_log_gives_another_capture_is_refused(tmp_path, monkeypatch):
    e = _word_entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], [TICKS, TICKS])
    _answer(monkeypatch, tmp_path, {0: "the capture", 1: "another capture"})
    with pytest.raises(SystemExit, match=r"run1/sa2-import-calls.csv captured ticks whose word "
                                          r"log gives another capture than the registered one "
                                          r"under the replay's own digest mask"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_an_import_log_of_a_run_the_replay_does_not_register_is_refused(tmp_path, monkeypatch):
    """The word check reads the registered runs' logs, so a log from a run the
    registry does not list as the capture or a repeat has nothing to be held to."""
    e = _word_entry(tmp_path, [["A.dll!f"], ["A.dll!f"]], [TICKS, TICKS])
    e["native"]["repeats"] = []
    _answer(monkeypatch, tmp_path, {0: "the capture"})
    with pytest.raises(SystemExit, match="run1/sa2-import-calls.csv is not one of its "
                                         "registered native runs"):
        NI.replay_calls(e, TABLE, tmp_path)


def test_word_captures_reads_every_registered_log_under_the_replay_s_own_mask(
        tmp_path, monkeypatch):
    """Each registered run's log (tick_replay.native_log, the comparer's rule),
    read by the one reader under tick_replay.effective_mask's header, native
    rules on; keyed by run directory, with the registered capture's text."""
    import tick_replay as T
    import wordlog as WL
    e = _word_entry(tmp_path, [["A.dll!f"], ["A.dll!f"], ["A.dll!f"]], [TICKS] * 3)
    mask = tmp_path / "effective.h"
    monkeypatch.setattr(T, "identity_spec", lambda entry, root: {"spec": 1})
    monkeypatch.setattr(T, "effective_mask",
                        lambda entry, spec, out, root: (mask, {"spec": spec}))
    monkeypatch.setattr(T, "native_log",
                        lambda entry, cap, root: tmp_path / Path(cap["capture"]).parent / "w.bin")
    calls = []
    monkeypatch.setattr(WL, "capture_csv",
                        lambda log, m, native: calls.append((log, m, native)) or log.parent.name)
    run, want = NI.word_captures(e, tmp_path)
    assert calls == [(tmp_path / f"run{k}" / "w.bin", mask, True) for k in range(3)]
    assert run == {(tmp_path / f"run{k}").resolve(): f"run{k}" for k in range(3)}
    assert want == "run0"
