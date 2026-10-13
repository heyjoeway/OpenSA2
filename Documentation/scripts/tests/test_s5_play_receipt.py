"""G5's live-play receipt (tools/s5/play_receipt.py): its table's observer, and the receipt
written from crafted session evidence.

No game, player or window runs here. The observer, the tick probe included unchanged with
its wrap renamed, is compiled with a driver standing in for GameLoop, guest memory and the
runtime's counters, and its rows are held to literal values: the keys of polls between two
GameLoop returns and inside the second count toward the second, the frames are the Presents
so far, the latch is the byte as the tick left it, a word the tick probe cannot read comes
out as its mark, which the writer refuses, and the tick probe's own stage trace still comes
out. Unset, it writes nothing. Each other test writes a crafted project root under
tmp_path: the real host selection record, a registry naming Metal Harbor at level 10,
tools/pins.json pinning a crafted DXVK library, and a play_live session directory per run
holding what play_live, the game, the player and loop_run would have left. That is the
observer's source, the per-tick table, the player's report and the run record. Then it
calls write() as play_live's end of session does, and reads the result with
Documentation/scripts/check_s5_exit.py, the clause's own reader.

  * a session that reaches the goal ring after entering the stage gets the tracked
    receipt, field by field, with the observer that wrote its table named beside the
    pipeline, and keeps the same bytes in its own record; the play clause takes it for the
    dxvk-native host while the clause stays unmet with no second host's receipt;
  * every other session gets only its record and leaves Documentation/ alone, with each
    reason named: no goal, a goal before the stage, never in the stage, the table absent,
    misheaded or misnumbered, a state word the observer could not read or wider than its
    width, another run's or no run's report, a DXVK library changed or outside the root, a
    report, run record or observer absent or unreadable, an observer that is not the one
    the writer makes now, and the writer changed or gone during the session;
  * a receipt in place is replaced by the next session to reach the goal ring, and kept
    by one that does not; replaced, it is first copied byte for byte into the replacing
    session's directory, never over a file there, so a receipt the clause takes survives a
    later one it refuses; a replacement that fails leaves the receipt in place and no part;
  * files are named below the root as the session wrote them, links not followed;
  * no test writes the checkout's receipt or its part.

The observer is compiled by the pinned zig at the target and optimisation tools/s4/link_s4.py
compiles a session's hooks with, every warning an error. Without the pinned zig its tests
fail: a skip would pass the gate with the observer unheld (AGENTS.md rule 8).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import play_receipt as PRC  # noqa: E402
import check_s5_exit as X  # noqa: E402
import link_s4 as LS  # noqa: E402  how a session's build compiles its hooks
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s5_play_live_evidence as E  # noqa: E402  a session whose latency evidence joins

RUN_ID = "20261003T140000-4242-0a1b2c3d"
LATER = "20261003T150000-4343-4e5f6a7b"


def session_dir(run_id: str = RUN_ID) -> str:
    return f"private/generated/s5/input/metalharbor/{run_id}"


SESSION = session_dir()
DXVK = "private/s5-tools/install/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0"
LIB = b"a DXVK library, as these tests write it"
PIN = hashlib.sha256(LIB).hexdigest()
RECEIPT = "Documentation/receipts/s5-play-dxvk-native-v1.json"
GPU_RECEIPT = "Documentation/receipts/s5-play-sdl3-gpu-v1.json"
HEADER = "tick\tcurrent_level\tgame_mode\tgame_state\tplayer\tgoal_latch\tframes\tkeys"
PRESS_TICKS = E.press_ticks(110)
LATENCY = (f"input latency, press to presented frame, over {E.PRESSES} presses in the stage: "
           "median 20.0 ms, p90 20.0 ms, max 20.0 ms")
SHOWS = ("dxvk-native: level 10 entered at tick 101, goal at tick 300, 400 frames presented of "
         f"the 360 made by 60 ticks past it, {2 * E.PRESSES} host key events; {LATENCY}")
# The session's latency files, as write() is given them and the receipt names them.
LATENCY_FILES = {"keys": "player-keys.tsv", "polls": "play-polls.tsv",
                 "presents": "player-presents.tsv", "relay_frames": "relay-frames.tsv",
                 "poll_report": "play-polls-report.json",
                 "relay_report": "relay-frames-report.json", "session": "play-live.json"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def the_checkouts_play_receipt_is_left_alone():
    """No test here writes the checkout's play receipt or its part: each writes a crafted
    root under tmp_path, so a writer that wrote below its own ROOT instead of the root it is
    given would write the tracked receipt in the checkout."""
    paths = [ROOT / RECEIPT, ROOT / (RECEIPT + ".part")]

    def state():
        return [path.stat().st_mtime_ns if path.exists() else None for path in paths]
    before = state()
    yield
    assert state() == before, f"a test wrote {RECEIPT} or its part in the checkout"


def project(tmp_path: Path) -> Path:
    """A root with the real selection record, a registry naming Metal Harbor at level 10, the
    real tools/pins.json pinning the crafted DXVK library, and that library."""
    root = tmp_path / "root"
    (root / X.SELECTION).parent.mkdir(parents=True)
    shutil.copyfile(ROOT / X.SELECTION, root / X.SELECTION)
    (root / X.REGISTRY).write_text(json.dumps({"schema": "sa2-replay-registry/v1", "replays": [
        {"name": "boot"}, {"name": "metalharbor", "stage": {"name": "metalharbor", "level": 10}}]}))
    pins = (ROOT / X.PINS).read_text()
    real = json.loads(pins)["tools"]["dxvk"]["library_sha256"]
    (root / X.PINS).parent.mkdir(parents=True)
    (root / X.PINS).write_text(pins.replace(real, PIN))
    (root / DXVK).parent.mkdir(parents=True)
    (root / DXVK).write_bytes(LIB)
    return root


def table(goal: int | None = 300, entry: int = 101, end: int = 400) -> str:
    """The game's per-tick table: loading to tick entry-1, Metal Harbor from `entry`, a key
    held over each press of PRESS_TICKS, the goal latch set from `goal`, a Present per tick."""
    lines = [HEADER]
    for tick in range(1, end + 1):
        inside = tick >= entry
        lines.append("\t".join(str(v) for v in (
            tick, "0x000a", "0x00000005" if inside else "0x00000004",
            "0x0010" if inside else "0x0001", "0x01ab2000" if inside else "0x00000000",
            "0x01" if goal is not None and tick >= goal else "0x00", tick,
            E.keys_column(PRESS_TICKS, tick))))
    return "\n".join(lines) + "\n"


def with_cell(tick: int, column: str, value: str, base: str | None = None) -> str:
    """`base` (by default table()) with the cell of `column` at `tick` set to `value`."""
    lines = (table() if base is None else base).splitlines()
    cells = lines[tick].split("\t")
    cells[HEADER.split("\t").index(column)] = value
    lines[tick] = "\t".join(cells)
    return "\n".join(lines) + "\n"


def report(root: Path, run_id: str = RUN_ID, host: str = "dxvk-native") -> dict:
    """The DXVK player's report, the members the play clause and its latency join read as
    host/player/player.cpp writes them, for a session the goal ended; its stream header's meta
    names `host`, as play_live's SA2_REC_META does."""
    meta = json.dumps({"audio": True, "build": "present-play-mh.x", "host": host,
                       "interactive": True, "run": run_id, "runner": "tools/s5/play_live.py",
                       "scene": "metalharbor", "transport": "fifo"},
                      separators=(",", ":"), sort_keys=True)
    rel = {name: f"{session_dir(run_id)}/{LATENCY_FILES[name]}"
           for name in ("keys", "presents", "polls", "relay_frames")}
    return {"tool": "host/player sa2_player", **E.report_fields(rel, E.PRESSES, 400),
            "present_failures": 0, "readback_failures": 0, "errors": 0,
            "calls": {"handled": 4181795, "skipped_by_stage": 0, "not_handled": 0},
            "textures": {"placeholder_binds": 0}, "host_failures": {"count": 0},
            "stream_problems": {"count": 0}, "notes": {"count": 0},
            "stream_header": json.dumps({"schema": "sa2-d3d9-stream/v2",
                                         "writer": "host/sa2_present_rec.c", "meta": meta}),
            "window": {"frames_with_flags": {"occluded": 0, "hidden": 0, "minimized": 0,
                                             "input_focus": 400}},
            "files": {"player": {"path": "/proc/self/exe", "sha256": "e1" * 32},
                      "libdxvk_d3d9": {"path": str(root / DXVK), "real_path": str(root / DXVK),
                                       "sha256": PIN}},
            "host_identity": {"dxvk_file_verified": True}}


RUN = {"ok": True, "dinput": {"host_input": {
    "source": "fifo", "polls": 401, "keys_written": E.PRESSES * E.HOLD, "script_lines": 0,
    "records": 2 * E.PRESSES, "last_seq": 2 * E.PRESSES - 1, "seq_gaps": 0,
    "partial_dropped": 0}}, **E.run_fields(401, 400)}


def session(root: Path, ticks: str | None = None, rep: dict | None = None,
            run: dict | None = None, run_id: str = RUN_ID, host: str = "dxvk-native",
            presses: list[int] | None = None) -> dict:
    """A play_live session's directory as play_live, the game, the player and loop_run leave
    it, with the latency logs of `presses` and the session's play-live.json, and what
    play_live hands write() for it."""
    out = root / session_dir(run_id)
    (out / "player").mkdir(parents=True, exist_ok=True)
    (out / PRC.OBSERVER).write_text(PRC.ticks_observer())
    (out / "play-ticks.tsv").write_text(table() if ticks is None else ticks)
    (out / "player" / "report.json").write_text(
        json.dumps(report(root, run_id) if rep is None else rep))
    run = RUN if run is None else run
    (out / "run.json").write_text(json.dumps(run))
    E.session(out, root, PRESS_TICKS if presses is None else presses, 400)
    E.write(out / "play-live.json", E.live_session(run if isinstance(run, dict) else RUN, 400))
    return {"host": host, "run_id": run_id, "level": 10, "pipeline": PRC.pipeline(),
            "ticks": out / "play-ticks.tsv", "observer": out / PRC.OBSERVER,
            "report": out / "player" / "report.json", "run": out / "run.json",
            "record": out / "play-record.json", "replaced": out / "play-receipt-replaced.json",
            **{name: out / file for name, file in LATENCY_FILES.items()}}


def untracked(got: dict) -> dict:
    """write()'s answer less the latency it measured, which the tests below that need it read."""
    return {key: value for key, value in got.items() if key != "latency"}


# ------------------------------------------------------------------ the tracked receipt


def test_a_session_that_reaches_the_goal_ring_writes_the_receipt_the_play_clause_reads(tmp_path):
    root = project(tmp_path)
    s = session(root)
    got = PRC.write(s, root=root)
    assert got == {"receipt": RECEIPT, "record": str(s["record"]), "replaced": None,
                   "entered": 101, "goal": 300, "latency": E.summary(E.PRESSES)}
    text = (root / RECEIPT).read_bytes()
    assert s["record"].read_bytes() == text          # the session keeps the receipt's bytes
    doc = json.loads(text)
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d{4}", doc.pop("written"))
    out = root / SESSION
    assert doc == {
        "schema": "sa2-s5-play/v1", "generated_by": "tools/s5/play_live.py",
        "inputs": {"pipeline": {name: sha(ROOT / name) for name in (
            "tools/s5/play_live.py", "tools/s5/play_receipt.py",
            "harness/replay/wrap_tick_probe.c")},
            "observer": {"path": f"{SESSION}/sa2_play_ticks.c",
                         "sha256": hashlib.sha256(PRC.ticks_observer().encode()).hexdigest()}},
        "host": "dxvk-native", "stage": "metalharbor", "run_id": RUN_ID,
        "renderer": {"path": DXVK, "sha256": PIN},
        "evidence": {
            "ticks": {"path": f"{SESSION}/play-ticks.tsv", "sha256": sha(out / "play-ticks.tsv")},
            "player_report": {"path": f"{SESSION}/player/report.json",
                              "sha256": sha(out / "player" / "report.json")},
            "run": {"path": f"{SESSION}/run.json", "sha256": sha(out / "run.json")},
            **{name: {"path": f"{SESSION}/{file}", "sha256": sha(out / file)}
               for name, file in LATENCY_FILES.items()}},
        "input_latency": E.summary(E.PRESSES)}
    assert list(doc["evidence"]) == ["ticks", "player_report", "run", "keys", "polls",
                                     "presents", "relay_frames", "poll_report", "relay_report",
                                     "session"]
    assert not s["replaced"].exists()
    assert X.session(root, "dxvk-native", 10)["shows"] == SHOWS
    assert PRC.reading("dxvk-native", root) == f"the play clause takes this host's session: {SHOWS}"


def test_the_play_clause_takes_the_one_host_and_stays_unmet_without_the_second(tmp_path):
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    result = X.play(root)
    assert result.met is False
    assert result.detail == (f"{SHOWS}; no receipt Documentation/receipts/s5-play-sdl3-gpu-v1.json "
                             f"(its writer: tools/s5/play_live.py, not written yet)")


def test_with_no_receipt_in_place_the_reading_is_the_clauses_refusal(tmp_path):
    root = project(tmp_path)
    assert PRC.reading("dxvk-native", root) == (
        "the play clause refuses this host's session: no receipt "
        "Documentation/receipts/s5-play-dxvk-native-v1.json (its writer: tools/s5/play_live.py, "
        "not written yet)")
    assert PRC.reading("sdl3-gpu", root).endswith(f"no receipt {GPU_RECEIPT} (its writer: "
                                                  "tools/s5/play_live.py, not written yet)")


def test_the_receipt_goes_where_it_is_asked_with_the_evidence_named_below_the_root(tmp_path):
    root = project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    got = PRC.write(session(root), root=root, dest_root=elsewhere)
    assert got["receipt"] == RECEIPT and not (root / "Documentation" / "receipts").exists()
    doc = json.loads((elsewhere / RECEIPT).read_text())
    assert doc["evidence"]["run"]["path"] == f"{SESSION}/run.json"


def test_a_receipt_in_place_is_kept_in_the_replacing_sessions_directory(tmp_path):
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    first = (root / RECEIPT).read_bytes()
    later = session(root, run_id=LATER)
    got = PRC.write(later, root=root)
    assert got["replaced"] == {"run_id": RUN_ID, "kept": str(later["replaced"])}
    assert later["replaced"].read_bytes() == first
    assert (root / SESSION / "play-record.json").read_bytes() == first
    assert (root / RECEIPT).read_bytes() == later["record"].read_bytes()
    assert json.loads((root / RECEIPT).read_text())["run_id"] == LATER


def test_a_receipt_the_clause_takes_survives_a_later_one_it_refuses(tmp_path):
    """The review's case: the next session reaches the goal ring with a DXVK library the
    player loaded and hashed, but not the one tools/pins.json pins. The writer, which holds
    the library to what the player hashed, replaces the receipt, and the clause refuses what
    is in place; the receipt it replaced is in its directory, and put back, the clause takes
    it again."""
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    other = root / "private" / "other-dxvk" / "libdxvk_d3d9.so.0"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"another build of DXVK")
    rep = report(root, LATER)
    rep["files"]["libdxvk_d3d9"] = {"path": str(other), "real_path": str(other),
                                    "sha256": sha(other)}
    later = session(root, rep=rep, run_id=LATER)
    assert PRC.write(later, root=root)["replaced"]["run_id"] == RUN_ID
    assert PRC.reading("dxvk-native", root) == (
        "the play clause refuses this host's session: dxvk-native: its renderer is not the "
        "DXVK library tools/pins.json pins")
    shutil.copyfile(later["replaced"], root / RECEIPT)
    assert PRC.reading("dxvk-native", root) == f"the play clause takes this host's session: {SHOWS}"


def test_a_session_the_clause_would_refuse_for_its_window_is_not_tracked(tmp_path):
    """The window minimized at a Present: the writer's latency join refuses it as the clause
    does, so the receipt in place stays."""
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    before = (root / RECEIPT).read_bytes()
    rep = report(root, LATER)
    rep["window"]["frames_with_flags"]["minimized"] = 5
    got = PRC.write(session(root, rep=rep, run_id=LATER), root=root)
    assert got["not_tracked"] == [
        "the input latency: window.frames_with_flags.minimized is 5, expected 0"]
    assert (root / RECEIPT).read_bytes() == before


@pytest.mark.parametrize("end, after", [(330, 30), (300 + 59, 59)])
def test_a_session_ended_too_soon_after_the_goal_is_not_tracked(tmp_path, end, after):
    """The review's counterexample: a window closed (or a Ctrl-C) fewer than the clause's
    AFTER_GOAL_TICKS ticks after the goal ends a session the clause refuses. The writer holds
    the table to all the clause reads of it, so the receipt in place, one the clause takes,
    stays."""
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    before = (root / RECEIPT).read_bytes()
    got = PRC.write(session(root, ticks=table(end=end), run_id=LATER), root=root)
    assert untracked(got) == {"receipt": None, "record": str(root / session_dir(LATER) /
                                                             "play-record.json"),
                              "not_tracked": [f"the per-tick table: dxvk-native: the session "
                                              f"ends {after} ticks after the goal, under 60"]}
    assert (root / RECEIPT).read_bytes() == before
    assert PRC.reading("dxvk-native", root) == f"the play clause takes this host's session: {SHOWS}"


def test_a_session_that_ends_the_clause_s_ticks_after_the_goal_is_tracked(tmp_path):
    root = project(tmp_path)
    got = PRC.write(session(root, ticks=table(end=300 + 60)), root=root)
    assert got["receipt"] == RECEIPT, got.get("not_tracked")
    assert X.session(root, "dxvk-native", 10)["shows"].startswith(
        "dxvk-native: level 10 entered at tick 101, goal at tick 300, 400 frames presented of "
        "the 360 made by 60 ticks past it")


@pytest.mark.parametrize("ticks, why", [
    (lambda: with_cell(200, "current_level", "0x000b"),
     "dxvk-native: the session leaves level 10 at tick 200, before the goal at tick 300"),
    (lambda: "\n".join([HEADER] + [
        "\t".join(cells[:2] + ["0x00000005", "0x0010", "0x01ab2000"] + cells[5:])
        for cells in (line.split("\t") for line in table().splitlines()[1:])]) + "\n",
     "dxvk-native: the session is in the stage from its first tick, so it does not show the "
     "stage's start"),
])
def test_a_table_the_clause_refuses_is_not_tracked_whatever_the_latency(tmp_path, ticks, why):
    root = project(tmp_path)
    got = PRC.write(session(root, ticks=ticks()), root=root)
    assert got["not_tracked"] == [f"the per-tick table: {why}"]
    assert not (root / "Documentation" / "receipts").exists()


@pytest.mark.parametrize("how", ["a second SIGTERM", "SIGKILL to loop_run's group"])
def test_a_session_whose_game_was_stopped_by_force_is_not_tracked(tmp_path, how):
    root = project(tmp_path)
    s = session(root)
    live = json.loads(s["session"].read_text())
    live["session"]["stop"] = {"how": how, "pid": 4242, "signalled": [4242, 4242],
                               "seconds": 150.3}
    s["session"].write_text(json.dumps(live))
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == ["the input latency: forced/unknown game stop"]


def test_a_gpu_session_whose_stream_names_the_other_host_is_not_tracked(tmp_path):
    root = project(tmp_path)
    s = gpu_session(root)
    rep = json.loads(s["report"].read_text())
    rep["stream_header"] = report(root, host="dxvk-native")["stream_header"]
    s["report"].write_text(json.dumps(rep))
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == ["the input latency: host is 'dxvk-native', expected 'sdl3-gpu'"]


def test_a_join_that_fails_outside_its_refusals_still_writes_the_record(tmp_path, monkeypatch):
    """Review finding: the join ran outside the writer's reasons, so an exception other than
    a refusal left the session with no record. The record is written, with the reason."""
    root = project(tmp_path)
    s = session(root)

    def broken(*args, **kwargs):
        raise ValueError("a join that breaks")
    monkeypatch.setattr(PRC.X, "latency_evidence", broken)
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == [
        "the input latency could not be joined: ValueError: a join that breaks"]
    assert json.loads(s["record"].read_text())["not_tracked"] == got["not_tracked"]
    assert not (root / "Documentation" / "receipts").exists()


def test_a_receipt_in_place_that_does_not_parse_is_kept_as_it_was(tmp_path):
    root = project(tmp_path)
    (root / RECEIPT).parent.mkdir(parents=True)
    (root / RECEIPT).write_bytes(b"{not a receipt")
    s = session(root)
    assert PRC.write(s, root=root)["replaced"] == {"run_id": None, "kept": str(s["replaced"])}
    assert s["replaced"].read_bytes() == b"{not a receipt"


def test_a_kept_receipt_is_never_written_over(tmp_path):
    """write() runs once a session; a session directory that holds a kept receipt already
    keeps it, and the receipt in place stays."""
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    before = (root / RECEIPT).read_bytes()
    later = session(root, run_id=LATER)
    later["replaced"].write_text("a receipt kept before\n")
    with pytest.raises(FileExistsError):
        PRC.write(later, root=root)
    assert later["replaced"].read_text() == "a receipt kept before\n"
    assert (root / RECEIPT).read_bytes() == before


def test_a_replacement_that_fails_leaves_the_receipt_in_place_and_no_part(tmp_path, monkeypatch):
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    before = (root / RECEIPT).read_bytes()

    def full(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(PRC.os, "replace", full)
    later = session(root, run_id=LATER)
    with pytest.raises(OSError, match="No space left on device"):
        PRC.write(later, root=root)
    assert (root / RECEIPT).read_bytes() == before
    assert sorted(p.name for p in (root / RECEIPT).parent.iterdir()) == [Path(RECEIPT).name]
    assert later["replaced"].read_bytes() == before


def test_a_session_that_does_not_reach_the_goal_leaves_the_receipt_in_place(tmp_path):
    root = project(tmp_path)
    PRC.write(session(root), root=root)
    before = (root / RECEIPT).read_bytes()
    later = session(root, ticks=table(goal=None), run_id=LATER)
    got = PRC.write(later, root=root)
    assert got["receipt"] is None and (root / RECEIPT).read_bytes() == before
    assert not later["replaced"].exists()


def test_files_are_named_below_the_root_as_the_session_wrote_them(tmp_path):
    """A worktree's private/ is a link into the main checkout: the receipt names the files
    below the root it was given, not where the link leads."""
    real = project(tmp_path)
    root = tmp_path / "worktree"
    root.mkdir()
    for top in ("Documentation", "tools"):
        shutil.copytree(real / top, root / top)
    (root / "private").symlink_to(real / "private")
    rep = report(root)
    got = PRC.write(session(root, rep=rep), root=root)
    assert got["receipt"] == RECEIPT
    doc = json.loads((root / RECEIPT).read_text())
    assert doc["renderer"] == {"path": DXVK, "sha256": PIN}
    assert doc["evidence"]["ticks"]["path"] == f"{SESSION}/play-ticks.tsv"
    assert doc["inputs"]["observer"]["path"] == f"{SESSION}/sa2_play_ticks.c"
    assert X.session(root, "dxvk-native", 10)["shows"] == SHOWS


# ------------------------------------------------------------------ the untracked record


def other_header() -> str:
    return table().replace("\tkeys\n", "\tkeys_applied\n", 1)


def misnumbered() -> str:
    return table().replace("\n2\t0x000a", "\n3\t0x000a", 1)


def with_lib(root: Path, **lib) -> dict:
    rep = report(root)
    rep["files"]["libdxvk_d3d9"].update(lib)
    return rep


UNREAD = "the tick probe's mark for a word it could not read"


@pytest.mark.parametrize("make, why", [
    (lambda root: {"ticks": table(goal=None)},
     ["the goal latch never rises: the session did not reach the goal ring"]),
    (lambda root: {"ticks": table(goal=50)},
     ["the goal latch is set at tick 50, not after the stage is entered at tick 101"]),
    (lambda root: {"ticks": table(goal=101)},
     ["the goal latch is set at tick 101, not after the stage is entered at tick 101"]),
    (lambda root: {"ticks": table().replace("\t0x0010\t", "\t0x000c\t")},
     ["the table never shows level 10 in play (GameMode_Level, GameStates_Ingame, a player)"]),
    (lambda root: {"ticks": other_header()},
     ["the per-tick table play-ticks.tsv: header is not tick current_level game_mode "
      "game_state player goal_latch frames keys"]),
    (lambda root: {"ticks": misnumbered()},
     ["the per-tick table play-ticks.tsv: tick does not run 1, 2, 3, ... with one row each"]),
    (lambda root: {"ticks": table() + "401\t0x000a\t0x00000005\n"},
     ["the per-tick table play-ticks.tsv line 402: 3 cells, not 8"]),
    # An unreadable latch, which the clause would take for the goal ring reached at tick 200.
    (lambda root: {"ticks": with_cell(200, "goal_latch", "0xffffffff", table(goal=None))},
     [f"the per-tick table play-ticks.tsv: goal_latch is 0xffffffff at tick 200, {UNREAD}"]),
    (lambda root: {"ticks": with_cell(150, "player", "0xffffffff")},
     [f"the per-tick table play-ticks.tsv: player is 0xffffffff at tick 150, {UNREAD}"]),
    (lambda root: {"ticks": with_cell(5, "current_level", "0x10000")},
     ["the per-tick table play-ticks.tsv: current_level is 0x10000 at tick 5, not a 2-byte "
      "value"]),
    (lambda root: {"ticks": with_cell(5, "game_state", "-1")},
     ["the per-tick table play-ticks.tsv: game_state is -0x1 at tick 5, not a 2-byte value"]),
    # Another run's report names that run's own logs.
    (lambda root: {"rep": report(root, "20261003T090000-1-00000000")},
     ["the player's report is of run '20261003T090000-1-00000000', not this session's "
      f"'{RUN_ID}'", "the input latency: host_input.key_log.path does not name keys"]),
    (lambda root: {"rep": report(root) | {"stream_header": "{}"}},
     ["the player's report: its stream header's meta names no run",
      "the input latency: its stream header's meta names no run"]),
    (lambda root: {"rep": with_lib(root, sha256="ab" * 32)},
     [f"the DXVK library {DXVK} does not hash to what the player loaded"]),
    (lambda root: {"rep": with_lib(root, path="/opt/elsewhere/libdxvk_d3d9.so.0")},
     ["the DXVK library the player loaded /opt/elsewhere/libdxvk_d3d9.so.0 is not under {root}"]),
    (lambda root: {"rep": with_lib(root, path=None, real_path=None)},
     ["the player's report names no DXVK library it loaded"]),
    (lambda root: {"run": ["not", "an", "object"]},
     ["the run record is not a JSON object"]),
])
def test_a_session_not_fit_for_the_receipt_is_kept_untracked_with_its_reasons(tmp_path, make, why):
    root = project(tmp_path)
    why = [reason.replace("{root}", str(root)) for reason in why]
    s = session(root, **make(root))
    got = PRC.write(s, root=root)
    assert untracked(got) == {"receipt": None, "record": str(s["record"]), "not_tracked": why}
    record = json.loads(s["record"].read_text())
    assert record["not_tracked"] == why and record["schema"] == "sa2-s5-play/v1"
    assert record["run_id"] == RUN_ID
    assert not (root / "Documentation" / "receipts").exists()
    assert X.play(root).detail.startswith(
        "no receipt Documentation/receipts/s5-play-dxvk-native-v1.json")


@pytest.mark.parametrize("gone, why", [
    ("play-ticks.tsv", f"the per-tick table {SESSION}/play-ticks.tsv is absent"),
    ("player/report.json", f"the player's report {SESSION}/player/report.json is absent"),
    ("run.json", f"the run record {SESSION}/run.json is absent"),
    ("sa2_play_ticks.c", f"the table's observer {SESSION}/sa2_play_ticks.c is absent"),
])
def test_a_session_missing_a_file_is_kept_untracked(tmp_path, gone, why):
    """The game writes no table when no hook its build links reads SA2_PLAY_TICKS; a player
    or a run that died leaves no report or run record."""
    root = project(tmp_path)
    s = session(root)
    (root / SESSION / gone).unlink()
    got = PRC.write(s, root=root)
    assert got["receipt"] is None and got["not_tracked"] == [why]


MOVED = (f"the table's observer {SESSION}/sa2_play_ticks.c is not the one "
         "tools/s5/play_receipt.py generates now: the columns or the tick probe it was made "
         "from moved")


def test_an_observer_other_than_the_writers_is_not_the_receipts(tmp_path):
    root = project(tmp_path)
    s = session(root)
    s["observer"].write_text(PRC.ticks_observer().replace("0x0174B002u", "0x0174B003u"))
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == [MOVED]
    assert json.loads(s["record"].read_text())["inputs"]["observer"] == {
        "path": f"{SESSION}/sa2_play_ticks.c", "sha256": sha(s["observer"])}


def test_columns_that_moved_during_the_session_are_not_the_receipts(tmp_path, monkeypatch):
    """The session compiled its observer from check_s5_exit.py's STATE_WORDS as they were;
    by the session's end the latch's address moved."""
    root = project(tmp_path)
    s = session(root)
    monkeypatch.setitem(X.STATE_WORDS, "goal_latch", (None, 0x0174B003, 1))
    assert PRC.write(s, root=root)["not_tracked"] == [MOVED]


def test_a_table_the_writer_no_longer_observes_is_a_reason_not_an_exit(tmp_path, monkeypatch):
    root = project(tmp_path)
    s = session(root)
    monkeypatch.setattr(X, "PLAY_TICKS", X.PLAY_TICKS[:-1])
    why = PRC.write(s, root=root)["not_tracked"]
    assert why[0].startswith("check_s5_exit.PLAY_TICKS is ('tick', ") and why[0].endswith(
        "which this observer writes")


def test_unreadable_evidence_is_named_as_such(tmp_path):
    root = project(tmp_path)
    s = session(root)
    s["report"].write_text("{not json")
    s["run"].write_text("")
    got = PRC.write(s, root=root)
    assert [w.split(":")[0] for w in got["not_tracked"]] == [
        "the player's report does not parse", "the run record does not parse"]


def test_a_writer_changed_during_the_session_is_not_the_receipts(tmp_path):
    root = project(tmp_path)
    s = session(root)
    s["pipeline"] = dict(s["pipeline"], **{"tools/s5/play_receipt.py": "00" * 32})
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == ["tools/s5/play_receipt.py changed during the session: the "
                                  "receipt's writer is not the one the session started with"]


def test_a_pipeline_file_gone_during_the_session_is_a_reason_and_the_record_is_kept(
        tmp_path, monkeypatch):
    root = project(tmp_path)
    s = session(root)
    gone = "tools/s5/play_receipt_gone.py"
    monkeypatch.setattr(PRC, "PIPELINE", PRC.PIPELINE + (gone,))
    s["pipeline"] = dict(s["pipeline"], **{gone: "00" * 32})
    why = [f"{gone} changed during the session: the receipt's writer is not the one the "
           "session started with"]
    assert PRC.write(s, root=root)["not_tracked"] == why
    assert json.loads(s["record"].read_text())["not_tracked"] == why


def test_every_reason_is_kept_not_only_the_first(tmp_path):
    root = project(tmp_path)
    s = session(root, ticks=table(goal=None), rep=report(root, "another"))
    assert PRC.write(s, root=root)["not_tracked"] == [
        "the goal latch never rises: the session did not reach the goal ring",
        f"the player's report is of run 'another', not this session's '{RUN_ID}'"]


# ------------------------------------------------------------------ the second host

GPU = b"the GPU player, as these tests write it"


def gpu_session(root: Path, **lib) -> dict:
    """An sdl3-gpu session: the GPU player kept by its sha256 as play_live keeps it, and a
    report naming it as the binary that ran and pinning no DXVK library."""
    kept = root / "private/generated/s5/input/renderers" / hashlib.sha256(GPU).hexdigest() / \
        "sa2_player_gpu"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(GPU)
    rep = report(root, host="sdl3-gpu")
    rep["files"] = {"player": {"path": "/proc/self/exe", "real_path": str(kept.resolve()),
                               "sha256": hashlib.sha256(GPU).hexdigest()},
                    "libdxvk_d3d9": None, "dxvk_conf": None}
    rep["host_identity"] = {"dxvk_file_verified": False, "d3d9_host": "sa2_player_gpu"}
    for dotted, value in lib.items():
        node = rep
        *parts, last = dotted.split("__")
        for part in parts:
            node = node[part]
        node[last] = value
    s = session(root, rep=rep, host="sdl3-gpu")
    s["renderer"] = kept
    return s


def test_a_gpu_session_names_the_player_it_ran_as_its_renderer(tmp_path):
    root = project(tmp_path)
    s = gpu_session(root)
    got = PRC.write(s, root=root)
    assert got["receipt"] == GPU_RECEIPT and not (root / RECEIPT).exists()
    doc = json.loads((root / GPU_RECEIPT).read_text())
    assert (doc["host"], doc["renderer"]) == ("sdl3-gpu", {
        "path": str(s["renderer"].relative_to(root)), "sha256": hashlib.sha256(GPU).hexdigest()})
    assert X.session(root, "sdl3-gpu", 10)["shows"] == SHOWS.replace("dxvk-native", "sdl3-gpu")


@pytest.mark.parametrize("change, why", [
    ({"files__player__sha256": "ab" * 32}, "does not hash to what ran"),
    ({"files__player__real_path": "/opt/elsewhere/sa2_player_gpu"},
     "is not the binary the report names ran (/opt/elsewhere/sa2_player_gpu)"),
    ({"files__player__real_path": None},
     "the player's report names no binary that ran (files.player.real_path)"),
    ({"files__libdxvk_d3d9": {"path": DXVK, "sha256": PIN}},
     f"the GPU player's report pins a DXVK library (files.libdxvk_d3d9 '{PIN}')"),
    ({"host_identity__dxvk_file_verified": True},
     "the GPU player's report pins a DXVK library (files.libdxvk_d3d9 None)"),
])
def test_a_gpu_session_is_held_to_the_player_that_ran(tmp_path, change, why):
    root = project(tmp_path)
    got = PRC.write(gpu_session(root, **change), root=root)
    assert got["receipt"] is None and len(got["not_tracked"]) == 1, got["not_tracked"]
    assert why in got["not_tracked"][0]


def test_a_gpu_session_that_names_no_player_it_ran_is_untracked(tmp_path):
    root = project(tmp_path)
    s = gpu_session(root)
    del s["renderer"]
    assert PRC.write(s, root=root)["not_tracked"] == ["the session names no GPU player it ran"]


# ------------------------------------------------------------------ how the session ended


def test_a_session_whose_report_is_not_final_is_untracked(tmp_path):
    """A window closed hard, or a player cancelled: the report is written, but not final."""
    root = project(tmp_path)
    rep = report(root) | {"complete": False, "pass": False, "exit_status": 3}
    got = PRC.write(session(root, rep=rep), root=root)
    assert got["not_tracked"] == [
        "the player's report is not final (complete is False): the session did not end with "
        "the stream read to its END", "the input latency: complete is False, expected True"]
    assert not (root / "Documentation" / "receipts").exists()


# ------------------------------------------------------------------ the input latency


@pytest.mark.parametrize("name", list(LATENCY_FILES))
def test_a_session_missing_a_latency_file_is_untracked(tmp_path, name):
    root = project(tmp_path)
    s = session(root)
    s[name].unlink()
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == [f"{PRC.LATENCY_FILES[name]} {SESSION}/{LATENCY_FILES[name]} "
                                  "is absent"]
    assert got["latency"] is None
    record = json.loads(s["record"].read_text())
    assert record["evidence"][name] is None and record["input_latency"] is None


def test_a_join_refusal_is_a_reason_in_its_own_words(tmp_path):
    root = project(tmp_path)
    s = session(root)
    lines = s["keys"].read_text().splitlines(keepends=True)
    s["keys"].write_text("".join(lines[:-1]))
    got = PRC.write(s, root=root)
    assert got["not_tracked"] == [
        f"the input latency: host_input.key_log.rows is {2 * E.PRESSES}, expected "
        f"{2 * E.PRESSES - 1}"]


def synthetic(root: Path) -> dict:
    """A --synthetic-keys session: the player's own queued keys, marked synthetic in its log
    and counted apart in its report."""
    s = session(root)
    lines = s["keys"].read_text().splitlines(keepends=True)
    column = X.PLAY_KEYS.index("synthetic")
    for i in range(1, len(lines)):
        cells = lines[i].split("\t")
        cells[column] = "1"
        lines[i] = "\t".join(cells)
    s["keys"].write_text("".join(lines))
    rep = report(root)
    rep["host_input"] |= {"key_events": 0, "synthetic_key_events": 2 * E.PRESSES}
    rep["latency_diagnostics"]["requested_synthetic"] = True
    rep["synthetic_keys"] = {"path": f"{SESSION}/synthetic-keys.tsv", "pushed": 2 * E.PRESSES,
                             "real_keys_ignored": 0, "diagnostic": True}
    s["report"].write_text(json.dumps(rep))
    return s


def test_a_synthetic_session_is_never_tracked_and_keeps_its_latency_as_a_diagnostic(tmp_path):
    root = project(tmp_path)
    s = synthetic(root)
    got = PRC.write(s, root=root)
    assert got["receipt"] is None
    assert got["not_tracked"] == [
        f"the session's keys were synthetic ({2 * E.PRESSES} synthetic key events, "
        "--synthetic-keys): a scripted session is never the play receipt",
        "the input latency: synthetic raw key row"]
    diagnostic = E.summary(E.PRESSES) | {"diagnostic": True, "stage_ticks": [101, 300]}
    assert got["latency"] == diagnostic
    record = json.loads(s["record"].read_text())
    assert (record["input_latency"], record["input_latency_diagnostic"]) == (None, diagnostic)
    assert not (root / "Documentation" / "receipts").exists()


def test_a_synthetic_session_that_never_reaches_the_goal_measures_to_its_last_tick(tmp_path):
    """--synthetic-keys sessions end at their frame budget, never at the goal: the diagnostic
    join takes the stage from its entry to the last tick."""
    root = project(tmp_path)
    s = synthetic(root)
    s["ticks"].write_text(table(goal=None))
    got = PRC.write(s, root=root)
    assert got["not_tracked"][0] == ("the goal latch never rises: the session did not reach "
                                     "the goal ring")
    assert got["latency"]["stage_ticks"] == [101, 400]
    assert got["latency"]["summary"]["stage"]["by_status"]["presented"] == E.PRESSES


# ------------------------------------------------------------------ the definitions


def test_the_writer_is_play_live_and_the_pipeline_its_files_as_on_disk():
    assert PRC.PIPELINE == ("tools/s5/play_live.py", "tools/s5/play_receipt.py",
                            "harness/replay/wrap_tick_probe.c")
    assert PRC.pipeline() == {name: sha(ROOT / name) for name in PRC.PIPELINE}
    assert PRC.HOSTS == ("dxvk-native", "sdl3-gpu")
    assert [PRC.receipt_path(host) for host in PRC.HOSTS] == [RECEIPT, GPU_RECEIPT]
    assert (PRC.TICKS_ENV, PRC.POLLS_ENV, PRC.POLLS_REPORT_ENV) == (
        "SA2_PLAY_TICKS", "SA2_PLAY_POLLS", "SA2_PLAY_POLLS_REPORT")
    assert set(PRC.LATENCY_FILES) == set(X.LATENCY_EVIDENCE) | {X.SESSION_EVIDENCE}
    with pytest.raises(SystemExit, match="no play receipt for the host 'unity'"):
        PRC.receipt_path("unity")
    assert "\t".join(X.PLAY_TICKS) == HEADER
    assert PRC.UNREADABLE == 0xFFFFFFFF


# ------------------------------------------------------------------ the table's observer

# The zig tools/pins.json pins, and the flags a session's build compiles a hook with, every
# warning an error where the build passes -w.
ZIG = LS.S.ZIG
CFLAGS = [*LS.TARGET, LS.OPT, "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "runtime"),
          "-I", str(LS.C.REMILL_INC)]
# Stand-ins for what the observer links against in the game's build: guest memory holding
# the five state words, the d3d9 device's and the keyboard's counters, and GameLoop, which
# takes one line of stdin per call: the state the call leaves (level, mode, state, player in
# hex, latch, where 999 is a latch that cannot be read), the Presents it makes, the keys
# polled inside it. main() reads, before each call, the keys polled between the last return
# and this call.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "guest_memory.h"
#include "sa2_d3d9.h"
#include "sa2_dinput.h"

sa2_guest_t sa2_guest;
static uint32_t level, mode, state, player, latch, presents;
static int latch_unreadable;
static uint64_t keys;

int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    uint32_t v;
    switch (addr) {
    case 0x01934B70u: v = level; break;
    case 0x0174B040u: v = mode; break;
    case 0x01934BE0u: v = state; break;
    case 0x01DEA6C0u: v = player; break;
    case 0x0174B002u:
        if (latch_unreadable) return SA2_OK + 1;
        v = latch;
        break;
    default: return SA2_OK + 1;
    }
    memcpy(out, &v, len);
    return SA2_OK;
}
void sa2_d3d9_stats_get(sa2_d3d9_stats *out) {
    memset(out, 0, sizeof *out);
    out->present_calls = presents;
}
void sa2_dinput_input_stats_get(sa2_di_input_stats *out) {
    memset(out, 0, sizeof *out);
    out->keys_written = keys;
}
void *__real_sub_433ee0(void *st, uint32_t pc, void *mem) {
    unsigned a, b, c, d, e, f;
    unsigned long long g;
    (void)pc;
    (void)mem;
    if (scanf("%u %u %u %x %u %u %llu", &a, &b, &c, &d, &e, &f, &g) == 7) {
        level = a; mode = b; state = c; player = d; presents += f; keys += g;
        latch_unreadable = e == 999u;
        latch = latch_unreadable ? 0u : e;
    }
    return st;
}
void *__wrap_sub_433ee0(void *st, uint32_t pc, void *memory);
int main(void) {
    unsigned long long between;
    while (scanf("%llu", &between) == 1) {
        keys += between;
        if (__wrap_sub_433ee0(&keys, 0, NULL) != &keys)
            return 2;
    }
    return 0;
}
"""
# Four GameLoop calls: loading, with two Presents; the stage entered, three keys polled
# before the call and one inside it, a Present; the goal latch set, a Present; nothing new.
CALLS = ("0 10 4 1 0 0 2 0\n3 10 5 16 1ab2000 0 1 1\n"
         "0 10 5 16 1ab2000 1 1 0\n0 10 5 16 1ab2000 1 0 0\n")


@pytest.fixture(scope="module")
def observer(tmp_path_factory) -> Path:
    """The observer play_live would link, compiled with the driver above by the pinned zig
    as a session's build compiles its hooks."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is missing: {ZIG}")
    d = tmp_path_factory.mktemp("observer")
    (d / PRC.OBSERVER).write_text(PRC.ticks_observer())
    (d / "driver.c").write_text(DRIVER)
    exe = d / "driver"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zig-cache"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    r = subprocess.run([str(ZIG), "cc", *CFLAGS, str(d / PRC.OBSERVER), str(d / "driver.c"),
                        "-o", str(exe)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def run_observer(exe: Path, tmp_path: Path, calls: str = CALLS,
                 **env: str) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    return subprocess.run([str(exe)], input=calls, capture_output=True, text=True, timeout=60,
                          env=clean | env, cwd=tmp_path)


def test_the_observer_is_compiled_by_the_pinned_zig_as_the_build_compiles_hooks():
    pinned = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]["zig"]["path"]
    assert ZIG == ROOT / pinned
    assert CFLAGS[:3] == ["-target", "x86_64-linux-musl", "-O2"]


def test_the_observer_writes_the_clauses_row_at_every_gameloop_return(observer, tmp_path):
    r = run_observer(observer, tmp_path, SA2_PLAY_TICKS="play-ticks.tsv",
                     SA2_STAGE_TRACE="stage-trace.csv")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "play-ticks.tsv").read_text() == (
        HEADER + "\n"
        "1\t0x000a\t0x00000004\t0x0001\t0x00000000\t0x00\t2\t0\n"
        "2\t0x000a\t0x00000005\t0x0010\t0x01ab2000\t0x00\t3\t4\n"
        "3\t0x000a\t0x00000005\t0x0010\t0x01ab2000\t0x01\t4\t0\n"
        "4\t0x000a\t0x00000005\t0x0010\t0x01ab2000\t0x01\t4\t0\n")
    trace = (tmp_path / "stage-trace.csv").read_text().splitlines()
    assert [line.split(",")[:4] for line in trace] == [
        ["tick", "current_level", "game_mode", "game_state"],
        ["1", "0x000a", "0x00000004", "0x0001"], ["2", "0x000a", "0x00000005", "0x0010"],
        ["3", "0x000a", "0x00000005", "0x0010"], ["4", "0x000a", "0x00000005", "0x0010"]]


def test_the_observers_table_is_the_one_write_reads(observer, tmp_path):
    run_observer(observer, tmp_path, SA2_PLAY_TICKS="play-ticks.tsv")
    rows = PRC.table(tmp_path / "play-ticks.tsv")
    assert [(r["tick"], r["frames"], r["keys"], r["goal_latch"]) for r in rows] == [
        (1, 2, 0, 0), (2, 3, 4, 0), (3, 4, 0, 1), (4, 4, 0, 1)]
    assert PRC.stage_span(rows, 10) == (1, 2)          # zero-based: ticks 2 and 3
    with pytest.raises(PRC.Unfit, match="the session ends 1 ticks after the goal, under 60"):
        PRC.goal(rows, 10, "dxvk-native")              # four ticks are no session to track


def test_a_latch_the_observer_cannot_read_is_its_mark_which_write_refuses(observer, tmp_path):
    """The latch's byte comes out as the tick probe's 0xffffffff when sa2_peek fails, which
    the clause would read as the goal ring reached."""
    calls = "0 10 5 16 1ab2000 0 1 0\n0 10 5 16 1ab2000 999 1 0\n"
    r = run_observer(observer, tmp_path, calls, SA2_PLAY_TICKS="play-ticks.tsv")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "play-ticks.tsv").read_text() == (
        HEADER + "\n"
        "1\t0x000a\t0x00000005\t0x0010\t0x01ab2000\t0x00\t1\t0\n"
        "2\t0x000a\t0x00000005\t0x0010\t0x01ab2000\t0xffffffff\t2\t0\n")
    with pytest.raises(PRC.Unfit) as refused:
        PRC.table(tmp_path / "play-ticks.tsv")
    assert str(refused.value) == f"the per-tick table play-ticks.tsv: goal_latch is 0xffffffff " \
                                 f"at tick 2, {UNREAD}"


def test_unset_the_observer_writes_nothing_and_passes_gameloop_through(observer, tmp_path):
    r = run_observer(observer, tmp_path)
    assert r.returncode == 0, r.stderr
    assert list(tmp_path.iterdir()) == []


def test_the_observer_is_the_tick_probe_unchanged_with_its_wrap_renamed():
    """...after the runtime's headers, so the counters' structs it hands the runtime are
    laid out before anything the tick probe defines."""
    source = PRC.ticks_observer()
    probe = str((ROOT / "harness/replay/wrap_tick_probe.c").resolve())
    renamed = (f"#define __wrap_sub_433ee0 sa2_play_ticks_tick_probe\n#include \"{probe}\"\n"
               "#undef __wrap_sub_433ee0\n")
    assert renamed in source
    assert source.index('#include "sa2_d3d9.h"\n#include "sa2_dinput.h"\n') < source.index(
        renamed)
    # The runtime's two headers, the C library's five the poll log needs, and the tick probe.
    assert re.findall(r"#include (\S+)", source) == [
        '"sa2_d3d9.h"', '"sa2_dinput.h"', "<time.h>", "<limits.h>", "<unistd.h>",
        "<sys/stat.h>", "<errno.h>", f'"{probe}"']
    assert source.count("void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory) {") == 1
    assert 'getenv("SA2_PLAY_TICKS")' in source


def test_an_observer_for_another_table_is_refused(monkeypatch):
    monkeypatch.setattr(X, "PLAY_TICKS", X.PLAY_TICKS[:-1])
    with pytest.raises(SystemExit, match="not the tick, state words"):
        PRC.ticks_observer()
