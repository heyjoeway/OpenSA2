"""tools/oracle/boot_capture.py, and the native side of tools/s4/stage_run.py,
since the tick probe keeps its word log.

The boot capture command is one tool so every boot capture -- a mask source, a
held-out capture, a registered one -- is taken as the comparison runs are
(the user's ruling of 2026-09-24, Documentation/decisions/2026-09-24-S4-halt-220h.md).
Each case is a way that could go wrong quietly: a runner flag after `--`,
where it reaches the game; a command that drifts from the registered one; a
capture launched while a recording session needs the oracle, or before the
oracle has sat idle as long as asked; a stage run whose native digests are
another mask's than the lifted side's.

No runner, game or Wine process runs: the runner's subprocess is replaced. The
word logs are written in harness/replay/word_log_core.h's format over a
made-up section (tools/oracle/test_oracle_tools.py synthetic_word_log), and the
reader, tools/s4/wordlog.py, runs for real.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "oracle"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import boot_capture as B  # noqa: E402
import stage_run as SR  # noqa: E402
from test_oracle_tools import probe_row, synthetic_word_log  # noqa: E402  the log's writer

RP = B.RP
REGISTERED = ["--timeout", "200", "--no-skip-intro",
              "--build", "vanilla_probe=harness/replay/native_vanilla_probe.c",
              "--build", "import_probe=harness/replay/native_import_probe.c",
              "--define", "import_probe:TICK_LIMIT=1200",
              "--build", "tick_probe=harness/replay/native_tick_probe.c",
              "--define", "tick_probe:TICK_LIMIT=1200",
              "--device-profile",
              "--capture", "sa2-vanilla.csv", "--capture", "sa2-import-calls.csv",
              "--capture", "sa2-tick-capture.csv",
              "--capture-policy", "sa2-vanilla.csv=1",
              "--capture-policy", "sa2-import-calls.csv=251:calls",
              "--min-rows", "1200", "--require-varying", "frame_count"]


# ------------------------------------------------------------ the command


def test_the_command_is_the_registered_boot_s_with_the_word_log_and_nothing_for_the_game():
    cmd = B.command("boot-x")
    assert cmd[:2] == [B.PY, str(B.RUNNER)]
    assert cmd[2:] == ["--name", "boot-x"] + REGISTERED + ["--binary-capture",
                                                           "sa2-word-log.bin"]
    args = RP.arg_parser().parse_args(cmd[2:])
    assert RP.game_arguments(args) == [] and "--" not in cmd
    builds = [(m, Path(s)) for m, s in (b.split("=", 1) for b in args.build)]
    assert RP.word_log_refusal(builds, args.capture, args.binary_capture) is None


def test_the_command_holds_every_flag_the_registry_s_boot_entry_names():
    """Documentation/replays.json's boot entry says how its captures were
    taken: with this tool, or with the runner and this tool's flags (those of
    the captures from before the word log, or with it)."""
    doc = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    how = next(e for e in doc["replays"] if e["name"] == "boot")["native"]["how"]
    if how.startswith("tools/oracle/boot_capture.py"):
        return
    assert how.startswith("tools/oracle/run_oracle_probe.py ")
    registered = how.split(" (", 1)[0].split()[1:]
    assert registered in (REGISTERED, REGISTERED + ["--binary-capture", "sa2-word-log.bin"])


def test_the_stack_control_adds_its_define_and_the_lock_wait_is_passed_on():
    cmd = B.command("boot-y", stack_pad=True, lock_timeout=1200)
    args = RP.arg_parser().parse_args(cmd[2:])
    assert "tick_probe:STACK_PAD=8192" in args.define and args.lock_timeout == 1200


def test_the_dry_run_prints_the_command_and_runs_nothing(monkeypatch, capsys):
    def refuse(*a, **k):
        raise AssertionError("something ran")

    monkeypatch.setattr(B.subprocess, "run", refuse)
    assert B.main(["--name", "boot-z", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"BOOT_CAPTURE_DRY_RUN from {B.ROOT}: ")
    assert out.strip().endswith("--binary-capture sa2-word-log.bin")


# ------------------------------------------------------------ waiting for a quiet oracle


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_it_waits_for_a_recording_session_and_for_the_idle_time_asked(monkeypatch):
    clock = Clock()
    sessions = [[41], [41], []]
    idle = iter([10.0, 10.0, 30.0, 95.0])
    monkeypatch.setattr(B.RT, "live_sessions", lambda: sessions.pop(0) if sessions else [])
    monkeypatch.setattr(B, "runner_pids", lambda: [])
    monkeypatch.setattr(RP, "idle_before_run", lambda: {"seconds": next(idle)})
    got = B.wait_for_quiet(90, 600, clock=clock, sleep=clock.sleep)
    assert got == {"waited_s": 3 * B.POLL, "idle": {"seconds": 95.0}}


def test_it_refuses_after_the_longest_wait_and_names_why(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(B.RT, "live_sessions", lambda: [])
    monkeypatch.setattr(B, "runner_pids", lambda: [7])
    monkeypatch.setattr(RP, "idle_before_run", lambda: {"seconds": None})
    with pytest.raises(SystemExit) as e:
        B.wait_for_quiet(90, 20, clock=clock, sleep=clock.sleep)
    assert e.value.code == ("BOOT_CAPTURE_REFUSED after waiting 20 s: another capture is running "
                            "(run_oracle_probe.py, pid 7); the oracle has been idle None s, not "
                            "90 s")


def test_a_run_reports_the_word_log_and_whether_it_is_the_registered_boot(tmp_path, monkeypatch,
                                                                          capsys):
    monkeypatch.setattr(RP, "CAPTURES", tmp_path)
    monkeypatch.setattr(B, "wait_for_quiet", lambda *a: {"waited_s": 0.0,
                                                         "idle": {"seconds": 120.0}})
    registered = B.registered_boot()
    ran = []

    def runner(cmd, **kw):
        ran.append((cmd, kw["cwd"]))
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "receipt.json").write_text(json.dumps({
            "word_log": {"ok": True, "stored_as": "sa2-word-log.bin.zst", "sha256": "5" * 64,
                         "ticks": 1200, "end_reason": "tick_limit", "problems": [],
                         "capture_sha256": registered, "cost": {"scan_ms": {"mean": 0.6}}},
            "session": {"idle_before_run": {"seconds": 120.0}}}))
        return subprocess.CompletedProcess(cmd, 0, "SA2_ORACLE_CAPTURE_OK name=b\n", "")

    monkeypatch.setattr(B.subprocess, "run", runner)
    assert B.main(["--name", "b"]) == 0
    assert ran == [(B.command("b"), str(B.ROOT))]
    out = capsys.readouterr().out
    got = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert (got["same_as_registered_boot"], got["word_log"]["ticks"], got["scan_ms"]) == (
        bool(registered), 1200, {"mean": 0.6})
    assert out.rstrip().endswith("BOOT_CAPTURE_OK name=b runner_exit=0")
    with pytest.raises(SystemExit, match="BOOT_CAPTURE_REFUSED .* exists"):
        B.main(["--name", "b"])


# ------------------------------------------------------------ stage_run's native side


def native_capture(folder: Path, ticks: int = 5) -> Path:
    """A native capture as the runner leaves one since the word log: the log
    kept compressed and collected, and a tick capture built under another mask
    (its digest columns made up)."""
    folder.mkdir(parents=True)
    raw = synthetic_word_log(folder / "sa2-word-log.bin", ticks)
    subprocess.run(["zstd", "-3", "-q", "--rm", str(raw)], check=True)
    kept = folder / "sa2-word-log.bin.zst"
    rows = probe_row(kept).splitlines()
    (folder / "sa2-tick-capture.csv").write_text(
        rows[0] + ",d_00910000\n" + "".join(f"{r},0x00000001\n" for r in rows[1:]))
    (folder / "receipt.json").write_text(json.dumps({"artifacts": [
        {"file": kept.name, "check": {"ok": True, "sha256": SR.WL.sha256(kept)}}]}))
    return folder / "sa2-tick-capture.csv"


def test_the_native_digests_are_the_word_log_s_under_the_tree_s_mask(tmp_path):
    """The lifted side hashes with the tree's tick_digest.h in the run, so the
    native side's digest columns are built from its log with the same mask,
    whatever mask the capture was taken under."""
    native = native_capture(tmp_path / "cap")
    got, source = SR.native_ticks(native, tmp_path / "run" / "native-ticks.csv")
    assert got == tmp_path / "run" / "native-ticks.csv"
    assert got.read_text() == SR.WL.capture_csv(tmp_path / "cap" / "sa2-word-log.bin.zst")
    assert got.read_text() != native.read_text()
    assert (source["from"], source["mask"]) == (str(tmp_path / "cap" / "sa2-word-log.bin.zst"),
                                                "harness/replay/tick_digest.h")


def test_a_log_that_is_not_the_one_its_run_collected_is_refused(tmp_path):
    native = native_capture(tmp_path / "cap")
    receipt = tmp_path / "cap" / "receipt.json"
    receipt.write_text(receipt.read_text().replace('"sha256": "', '"sha256": "0'))
    with pytest.raises(SystemExit, match="and its receipt collected 0"):
        SR.native_ticks(native, tmp_path / "run" / "native-ticks.csv")


def test_a_capture_from_before_the_word_log_is_read_as_it_is(tmp_path):
    (tmp_path / "old").mkdir()
    native = tmp_path / "old" / "sa2-tick-capture.csv"
    native.write_text("tick,frame_count\n1,0x00000001\n")
    got, source = SR.native_ticks(native, tmp_path / "run" / "native-ticks.csv")
    assert (got, source["mask"]) == (native, "the capture's own")
    assert not (tmp_path / "run").exists()


# ------------------------------------------------ the reviews of 2026-09-25


def refuse_in_receipt(folder: Path) -> None:
    """The receipt as the runner writes it for a log it refused: the file is
    the one collected, and its check is not ok."""
    receipt = folder / "receipt.json"
    rec = json.loads(receipt.read_text())
    rec["artifacts"][0]["check"].update(ok=False, problems=["frame_count differs from the log"])
    receipt.write_text(json.dumps(rec))


def test_a_log_its_run_refused_gives_no_native_digests(tmp_path):
    """W2: the runner writes the sha256 of a log it refused too, so the hash
    alone would let stage_run take that log's digests."""
    native = native_capture(tmp_path / "cap")
    refuse_in_receipt(tmp_path / "cap")
    with pytest.raises(SystemExit, match="its run refused it: frame_count differs from the log"):
        SR.native_ticks(native, tmp_path / "run" / "native-ticks.csv")
    assert not (tmp_path / "run").exists()


def test_the_capture_a_kept_log_gives_is_held_to_its_receipt_and_its_run_s_verdict(tmp_path):
    """run_oracle_probe.capture_from_log: the capture the kept log gives with a
    mask, from a log shown to be its run's and accepted by it; None for a
    capture from before the word log."""
    native_capture(tmp_path / "cap")
    kept = tmp_path / "cap" / "sa2-word-log.bin.zst"
    assert RP.kept_word_log(tmp_path / "cap") == kept
    assert RP.capture_from_log(tmp_path / "cap") == SR.WL.capture_csv(kept)
    assert RP.capture_from_log(tmp_path / "cap", RP.WL.MASK) == SR.WL.capture_csv(kept)
    (tmp_path / "old").mkdir()
    assert RP.capture_from_log(tmp_path / "old") is None
    refuse_in_receipt(tmp_path / "cap")
    with pytest.raises(SystemExit, match="its run refused it"):
        RP.capture_from_log(tmp_path / "cap")
    receipt = tmp_path / "cap" / "receipt.json"
    receipt.write_text(receipt.read_text().replace('"sha256": "', '"sha256": "0'))
    with pytest.raises(SystemExit, match="and its receipt collected 0"):
        RP.kept_word_log(tmp_path / "cap")


@pytest.mark.parametrize("idle, verdict, code", [
    (120.0, "BOOT_CAPTURE_OK name=b runner_exit=0", 0),
    (4.0, "BOOT_CAPTURE_PROBLEM name=b runner_exit=0 min_idle_met=false (idle 4.0 s at launch, "
          "not 90 s)", 1),
])
def test_a_boot_launched_less_idle_than_asked_is_a_problem(tmp_path, monkeypatch, capsys, idle,
                                                           verdict, code):
    """N5: the wait ends before the runner takes the lease, so the receipt's
    idle time is what the run launched after, and it is held to --min-idle."""
    monkeypatch.setattr(RP, "CAPTURES", tmp_path)
    monkeypatch.setattr(B, "wait_for_quiet", lambda *a: {"waited_s": 0.0,
                                                         "idle": {"seconds": 95.0}})

    def runner(cmd, **kw):
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "receipt.json").write_text(json.dumps({
            "word_log": {"ok": True}, "session": {"idle_before_run": {"seconds": idle}}}))
        return subprocess.CompletedProcess(cmd, 0, "SA2_ORACLE_CAPTURE_OK name=b\n", "")

    monkeypatch.setattr(B.subprocess, "run", runner)
    assert B.main(["--name", "b", "--min-idle", "90"]) == code
    out = capsys.readouterr().out
    assert out.rstrip().endswith(verdict)
    got = json.loads(out[out.index("{"):out.rindex("}") + 1])
    assert got["min_idle_met"] is (code == 0)
