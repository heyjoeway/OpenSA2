"""tools/s5/play_live.py: the two hosts, the kept renderer, a session with no person, the
recorder's counts for M7, and how a session ends.

No game, player or window runs. The dry run's commands are read from play_live's own
output; the renderer store, the synthetic script and the per-tick watcher are called
directly; and run_session runs with stand-ins for loop_run, the game and the player (Python
processes speaking the descriptors and FIFOs play_live gives them), so each way a session
asks the game to end is exercised over real pipes:

  * --host sdl3-gpu runs the GPU player as tools/s5/compare_hosts.py runs it (through
    host/player/player_env.sh, with no inherited SA2_D3D9GPU_ variable and the device's
    report in the session), from a read-only copy kept by its sha256, and names its own
    receipt; dxvk-native is the default and unchanged;
  * --synthetic-keys N --max-frames M: refused apart, refused without --receipt, a script of
    N jump presses fixed by its seed, irregular, inside the frame budget; the recorder's
    counts kept out of M7's census, which a session without it enters (planted in a
    checkout of the test's own, which this checkout's census never reads, and held to that);
  * the goal (the per-tick table, read as it grows), the window's close (the player's end
    request), the first Ctrl-C and the frame budget each ask the game to stop once, and the
    player then reads the stream to its end: nobody is cancelled, and the session says
    what ended it; a close as the goal is reached waits, at most GOAL_HOLD_S, for the
    clause's AFTER_GOAL_TICKS past it.

Every signal a test sends goes through a guard that lets it reach only this process's own
descendants.
"""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import play_live as PLV  # noqa: E402
import m7_inputs as M  # noqa: E402  M7's census of recorded runs

REAL_KILL = os.kill
HEADER = "\t".join(PLV.PRC.X.PLAY_TICKS)


def descends_from_this_process(pid: int) -> bool:
    me = os.getpid()
    while pid > 1:
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return False
        if pid == me:
            return True
    return False


@pytest.fixture(autouse=True)
def signals_reach_only_our_children(monkeypatch):
    def guarded(pid: int, sig: int) -> None:
        if not descends_from_this_process(pid):
            raise AssertionError(f"a test signalled pid {pid}, which is not its own child")
        REAL_KILL(pid, sig)
    monkeypatch.setattr(os, "kill", guarded)


@pytest.fixture(autouse=True)
def no_inherited_settings(monkeypatch):
    for name in ("SDL_AUDIO_DRIVER", "SDL_AUDIODRIVER"):
        monkeypatch.delenv(name, raising=False)


def dry_run(capsys, *argv: str) -> dict[str, str]:
    assert PLV.main(["--dry-run", *argv]) == 0
    out = {}
    for line in capsys.readouterr().out.splitlines():
        label, _, rest = line.partition(": ")
        out[label] = rest
    return out


def plan_of(*argv: str) -> dict:
    return PLV.plan(PLV.parser().parse_args(["--dry-run", *argv]))


def spec_env(pl: dict) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in pl["spec"].split("|")[1:])


MH = "private/generated/s5/input/metalharbor/dry-run"


# ---------------------------------------------------------------- the hosts


def test_the_dxvk_host_is_the_default_and_runs_run_player(capsys):
    lines = dry_run(capsys, "--scene", "metalharbor", "--receipt")
    assert lines["host"] == "dxvk-native" and "renderer" not in lines
    assert lines["player build"] == "OUT=private/generated/s5/input/bin bash host/player/build.sh"
    assert " bash host/player/run_player.sh --interactive " in lines["player"]
    assert "Documentation/receipts/s5-play-dxvk-native-v1.json" in lines["receipt"]


def test_the_second_host_runs_the_gpu_player_as_compare_hosts_runs_it(capsys, monkeypatch):
    """Through player_env.sh, with no inherited SA2_D3D9GPU_ setting (a fault or a capability
    turned off would change what the receipt's renderer draws) and the device's report in
    the session's player directory; the receipt is the second host's."""
    monkeypatch.setenv("SA2_D3D9GPU_FAULT", "draw")
    monkeypatch.setenv("SA2_D3D9GPU_CAPS_OFF", "border")
    lines = dry_run(capsys, "--host", "sdl3-gpu", "--scene", "metalharbor", "--receipt")
    assert lines["host"] == "sdl3-gpu"
    assert lines["player build"] == ("OUT=private/generated/s5/input/bin bash "
                                     "host/d3d9gpu/build.sh")
    assert lines["renderer"] == (
        "private/generated/s5/input/renderers/<sha256>/sa2_player_gpu, a read-only copy of "
        "private/generated/s5/input/bin/sa2_player_gpu kept by its sha256, run with no "
        "inherited SA2_D3D9GPU_ variable")
    launcher = ("bash -c 'source \"$0\" && exec \"$@\"' host/player/player_env.sh "
                "'<renderer-store>/<sha256>/sa2_player_gpu' --interactive --video-fd 40 "
                "--cancel-fd 41 --end-fd 42 ")
    assert f"SA2_D3D9GPU_REPORT={MH}/player/d3d9gpu.json {launcher}" in lines["player"]
    assert "Documentation/receipts/s5-play-sdl3-gpu-v1.json" in lines["receipt"]
    pl = plan_of("--host", "sdl3-gpu", "--scene", "metalharbor", "--receipt")
    assert not {k for k in pl["player_env"] if k.startswith("SA2_D3D9GPU_")} - {
        "SA2_D3D9GPU_REPORT"}
    assert json.loads(spec_env(pl)["SA2_REC_META"])["host"] == "sdl3-gpu"
    assert pl["receipt"]["host"] == "sdl3-gpu"
    # compare_hosts.py's own way of running the GPU player, word for word.
    compare = (ROOT / "tools/s5/compare_hosts.py").read_text()
    assert ('cmd = ["bash", "-c", \'source "$0" && exec "$@"\', str(PLAYER_ENV), '
            'str(build / PLAYERS["sdlgpu"])]') in compare
    assert 'env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_D3D9GPU")}' \
        in compare


def test_the_window_title_is_the_game_s_and_names_no_stream():
    """A player's window says Sonic Adventure 2 (the release's players, 2026-10-12): the host and
    the stream path it once named are the report's (the launcher's record names the host)."""
    player = (ROOT / "host/player/player.cpp").read_text()
    assert 'const std::string title = "Sonic Adventure 2";' in player
    assert '"SA2 player (' not in player


def unlock(top: Path) -> None:
    for path in [top, *top.rglob("*")]:
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o755)


def test_the_gpu_player_is_kept_by_its_sha256_read_only_and_reused(tmp_path):
    bin_dir, store = tmp_path / "bin", tmp_path / "store"
    bin_dir.mkdir()
    (bin_dir / "sa2_player_gpu").write_bytes(b"a GPU player")
    (bin_dir / "sa2_player_gpu.json").write_text('{"schema": "a build record"}\n')
    digest = PLV.PR.sha256(bin_dir / "sa2_player_gpu")
    try:
        kept = PLV.keep_renderer(bin_dir, store)
        assert kept == store / digest / "sa2_player_gpu"
        assert kept.read_bytes() == b"a GPU player"
        assert stat.S_IMODE(kept.stat().st_mode) == 0o555
        assert stat.S_IMODE(kept.parent.stat().st_mode) == 0o555
        assert (kept.parent / "sa2_player_gpu.json").read_text() == '{"schema": "a build record"}\n'
        assert sorted(p.name for p in store.iterdir()) == [digest]     # no part left behind
        # A rebuild into the same directory leaves the kept copy as it was.
        (bin_dir / "sa2_player_gpu").write_bytes(b"a rebuilt GPU player")
        again = PLV.keep_renderer(bin_dir, store)
        assert again != kept and kept.read_bytes() == b"a GPU player"
        (bin_dir / "sa2_player_gpu").write_bytes(b"a GPU player")
        assert PLV.keep_renderer(bin_dir, store) == kept
    finally:
        unlock(store)


def test_a_kept_renderer_that_does_not_hash_to_its_name_is_refused(tmp_path):
    bin_dir, store = tmp_path / "bin", tmp_path / "store"
    bin_dir.mkdir()
    (bin_dir / "sa2_player_gpu").write_bytes(b"a GPU player")
    digest = PLV.PR.sha256(bin_dir / "sa2_player_gpu")
    (store / digest).mkdir(parents=True)
    (store / digest / "sa2_player_gpu").write_bytes(b"something else")
    with pytest.raises(SystemExit, match="a kept renderer is never written over"):
        PLV.keep_renderer(bin_dir, store)
    assert (store / digest / "sa2_player_gpu").read_bytes() == b"something else"


def test_no_gpu_player_to_keep_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no sa2_player_gpu in"):
        PLV.keep_renderer(ROOT / "private" / "no-such-bin", tmp_path)


def test_the_renderer_store_stays_under_private():
    with pytest.raises(SystemExit, match="--renderer-store /tmp/x: the renderers"):
        plan_of("--host", "sdl3-gpu", "--renderer-store", "/tmp/x")


# ---------------------------------------------------------------- a session with no person


@pytest.mark.parametrize("argv, why", [
    (["--synthetic-keys", "40"], "--synthetic-keys and --max-frames go together"),
    (["--max-frames", "3000"], "--synthetic-keys and --max-frames go together"),
    (["--synthetic-seed", "4"], "--synthetic-seed seeds --synthetic-keys"),
    (["--synthetic-keys", "40", "--max-frames", "3000"],
     "--synthetic-keys verifies the input latency, whose logs only a --receipt session writes"),
    (["--receipt", "--synthetic-keys", "0", "--max-frames", "3000"], "must be above 0"),
    (["--receipt", "--synthetic-keys", "400", "--max-frames", "3000"],
     "--synthetic-keys 400 in --max-frames 3000: 75 ms between presses, under 150"),
])
def test_a_session_with_no_person_needs_both_options_and_the_receipt(argv, why):
    with pytest.raises(SystemExit, match=re.escape(why)):
        plan_of("--scene", "metalharbor", *argv)


def downs_and_ups(script: str) -> list[tuple[int, int, str]]:
    return [(int(a), int(b), c) for a, b, c in (line.split() for line in script.splitlines())]


def test_the_synthetic_script_is_its_seeds_irregular_and_inside_the_budget():
    one = PLV.synthetic_script(40, 3000, 7)
    assert one == PLV.synthetic_script(40, 3000, 7)          # byte for byte, by its seed
    assert one != PLV.synthetic_script(40, 3000, 8)
    *rows, close = downs_and_ups(one)
    assert close == (45_000, 0, "close")                     # 90 % of 50 s, after the presses
    assert [state for _, _, state in rows] == ["down", "up"] * 40
    assert {scancode for _, scancode, _ in rows} == {PLV.JUMP_SCANCODE} == {13}
    times = [at for at, _, _ in rows]
    assert times == sorted(times) and len(set(times)) == 80
    assert 0.2 * 50_000 <= times[0] and times[-1] <= 0.85 * 50_000   # 3000 frames at 60/s
    gaps = {b - a for a, b in zip(times[::2], times[2::2])}
    assert len(gaps) > 20                                         # irregular
    holds = [up - down for down, up in zip(times[::2], times[1::2])]
    assert min(holds) >= 40 and max(holds) <= 250


def test_a_session_with_no_person_is_shown_and_its_counts_kept_out_of_m7(capsys):
    argv = ("--scene", "metalharbor", "--receipt", "--synthetic-keys", "40", "--max-frames",
            "3000", "--sound-driver", "dummy")
    lines = dry_run(capsys, *argv)
    assert lines["synthetic"] == (
        f"40 presses of J (scancode 13, seed {PLV.SYNTHETIC_SEED}) written to "
        f"{MH}/synthetic-keys.tsv and queued by the player from its first Present, then the "
        "window's close request 45000 ms after it; the window's own keys are not sent; the "
        "session ends at the close, or at 3000 Presents; never a tracked receipt, and the "
        f"recorder's counts go to {MH}/counts.synthetic.json, outside M7's registry")
    assert lines["player"].endswith(f"--synthetic-keys {MH}/synthetic-keys.tsv --max-frames "
                                    "3600 --audio")
    assert "--max-frames: once it shows 3000 Presents" in lines["end"]
    pl = plan_of(*argv)
    assert spec_env(pl)["SA2_REC_COUNTS"] == f"{MH}/counts.synthetic.json"
    assert pl["synthetic"] == {"presses": 40, "max_frames": 3000, "seed": PLV.SYNTHETIC_SEED,
                               "key": "J", "scancode": 13, "lines": 81, "close_ms": 45000}


def recorded_run(where: Path, counts_name: str) -> None:
    """What a session's game leaves for M7: its run record (the probe's, of a guest run) and
    the recorder's counts under `counts_name`."""
    where.mkdir(parents=True, exist_ok=True)
    (where / "run.json").write_text(json.dumps({"winmain": {}, "d3d9": {"implemented": []}}))
    (where / counts_name).write_text(json.dumps({
        "schema": M.COUNTS_SCHEMA, "writer": M.RECORDER,
        "methods": [{"interface": "IDirect3DDevice9", "method": "Present", "calls": 3}]}))


@pytest.fixture
def checkout_of_its_own(tmp_path):
    """A directory M7's census of this checkout never reads, to plant recorded runs in.

    tmp_path is under TMPDIR, which units point under private/generated: a run planted there
    is one the census (tools/s4/m7_inputs.py, disk_runs) finds and registers, and its
    registry only grows (the play review measured 317 runs, 216 of them planted by tests).
    The census does not enter a directory holding a `.git` entry, a checkout or an overlay
    of one, so the planted tree is a checkout's: its own private/generated, which a census
    rooted there walks and this checkout's never does. After the test the census of this
    checkout is taken, and must find nothing under tmp_path."""
    root = tmp_path / "checkout"
    (root / ".git").mkdir(parents=True)
    yield root
    mine = tmp_path.resolve()
    found = [run for run in M.disk_runs(ROOT) if (ROOT / run).resolve().is_relative_to(mine)]
    assert found == [], f"M7's census of {ROOT} registers runs a test planted: {found}"


def test_a_session_enters_m7s_census_unless_it_had_no_person(checkout_of_its_own):
    """The recorder writes its counts where SA2_REC_COUNTS names (host/sa2_present_rec.c),
    and play_live moves the run record beside them: M7's census of recorded runs
    (tools/s4/m7_inputs.py) finds a person's session there, and never a --synthetic-keys
    one, which writes them under another name."""
    root = checkout_of_its_own
    planted = []
    for synthetic in (False, True):
        argv = ["--scene", "metalharbor", "--receipt"]
        if synthetic:
            argv += ["--synthetic-keys", "40", "--max-frames", "3000"]
        pl = plan_of(*argv)
        counts = spec_env(pl)["SA2_REC_COUNTS"]
        run = pl["paths"]["run"].relative_to(ROOT).as_posix()
        assert Path(counts).parent == Path(run).parent       # beside the run record
        assert Path(counts).name == ("counts.synthetic.json" if synthetic else "counts.json")
        where = Path(counts).parent.as_posix() + ("-synthetic" if synthetic else "")
        recorded_run(root / where, Path(counts).name)
        planted.append(where)
    assert M.disk_runs(root) == [planted[0]]                # the person's, not the script's


# ---------------------------------------------------------------- the per-tick watcher


def tick_row(tick: int, entered: bool, latch: str = "0x00", frames: int | None = None) -> str:
    return "\t".join(str(v) for v in (
        tick, "0x000a", "0x00000005" if entered else "0x00000004",
        "0x0010" if entered else "0x0001", "0x01ab2000" if entered else "0x00000000", latch,
        tick if frames is None else frames, 0)) + "\n"


def test_the_goal_ends_a_session_once_the_clause_s_second_and_the_margin_are_made(tmp_path):
    ticks = tmp_path / "play-ticks.tsv"
    watch = PLV.TickWatch(10)
    assert watch.read(ticks) is None                        # no table yet
    ticks.write_text(HEADER + "\n" + "".join(tick_row(t, t >= 50) for t in range(1, 100)))
    assert watch.read(ticks) is None and watch.entered == 50
    last = 100 + PLV.PRC.X.AFTER_GOAL_TICKS + PLV.END_MARGIN_TICKS
    with ticks.open("a") as f:
        f.write("".join(tick_row(t, True, "0x01") for t in range(100, last)))
        f.write(tick_row(last, True, "0x01")[:9])           # half a row: not read yet
    assert watch.read(ticks) is None and watch.goal == 100
    with ticks.open("a") as f:
        f.write(tick_row(last, True, "0x01")[9:])
    assert watch.read(ticks) == {"by": "the goal", "entered_tick": 50, "goal_tick": 100,
                                 "tick": last}


@pytest.mark.parametrize("latch", ["0x01", "0xffffffff"])
def test_a_latch_before_the_stage_or_unread_is_no_goal(tmp_path, latch):
    ticks = tmp_path / "play-ticks.tsv"
    entered = latch == "0xffffffff"
    ticks.write_text(HEADER + "\n" + "".join(tick_row(t, entered, latch) for t in range(1, 2000)))
    watch = PLV.TickWatch(10)
    assert watch.read(ticks) is None and watch.goal is None


def test_the_frame_budget_ends_a_session_and_another_table_ends_none(tmp_path):
    ticks = tmp_path / "play-ticks.tsv"
    ticks.write_text(HEADER + "\n" + "".join(tick_row(t, True, frames=2 * t) for t in range(1, 60)))
    assert PLV.TickWatch(10, 120).read(ticks) is None
    with ticks.open("a") as f:
        f.write(tick_row(60, True, frames=120))
    assert PLV.TickWatch(10, 120).read(ticks) == {"by": "--max-frames", "frames": 120, "tick": 60}
    ticks.write_text(HEADER.replace("keys", "keys_applied") + "\n" +
                     "".join(tick_row(t, True, "0x01") for t in range(1, 2000)))
    assert PLV.TickWatch(10, 1).read(ticks) is None


# ---------------------------------------------------------------- how a session ends


def free_descriptors(n: int) -> list[int]:
    got, fd = [], 100
    while len(got) < n:
        if PLV.free_fd(fd):
            got.append(fd)
        fd += 1
    return got


# A game that streams until asked to stop (SIGTERM), writing a per-tick row every 10 ms to
# argv[2] when one is named: in the stage from tick 3, the goal latch from tick argv[3].
GAME = r"""
import os, signal, sys, time
stop = []
signal.signal(signal.SIGTERM, lambda *a: stop.append(1))
w = os.open(sys.argv[1], os.O_WRONLY)
ticks = open(sys.argv[2], "w") if len(sys.argv) > 2 else None
goal = int(sys.argv[3]) if len(sys.argv) > 3 else 0
if ticks:
    ticks.write("\t".join(("tick", "current_level", "game_mode", "game_state", "player",
                           "goal_latch", "frames", "keys")) + "\n")
os.write(w, b"SA2D3D9S" + bytes(8))
t, deadline = 0, time.monotonic() + 20
while not stop and time.monotonic() < deadline:
    os.write(w, bytes(1024))
    t += 1
    if ticks:
        inside = t >= 3
        ticks.write(f"{t}\t0x000a\t{'0x00000005' if inside else '0x00000004'}\t"
                    f"{'0x0010' if inside else '0x0001'}\t{'0x01ab2000' if inside else '0x0'}\t"
                    f"{'0x01' if goal and t >= goal else '0x00'}\t{t}\t0\n")
        ticks.flush()
    time.sleep(0.01)
os.close(w)
sys.exit(0 if stop else 3)
"""

# A player that reads the stream to its end and answers play_live's end request (SIGUSR1)
# as host/player/player.cpp does, with one byte on --end-fd (unless PLAY_TEST_DEAF); with
# PLAY_TEST_END, after its first bytes it sends the byte itself, as a closed window does;
# with PLAY_TEST_CLOSE_AT_GOAL (the per-tick table's path), it does so as soon as the table
# shows the goal latch, as a person closing the window at the goal ring does.
PLAYER = r"""
import os, signal, sys, threading, time
a = sys.argv
video, end = int(a[a.index("--video-fd") + 1]), int(a[a.index("--end-fd") + 1])
asked = []
def answer(*_):
    if not asked:
        asked.append(1)
        os.write(end, b"\x01")
signal.signal(signal.SIGUSR1, signal.SIG_IGN if os.environ.get("PLAY_TEST_DEAF") else answer)
def close_at_goal(path):
    while True:
        try:
            if any(line.split("\t")[5] == "0x01" for line in open(path).read().splitlines()[1:]
                   if line.count("\t") == 7):
                return answer()
        except OSError:
            pass
        time.sleep(0.005)
if os.environ.get("PLAY_TEST_CLOSE_AT_GOAL"):
    threading.Thread(target=close_at_goal, args=(os.environ["PLAY_TEST_CLOSE_AT_GOAL"],),
                     daemon=True).start()
n = len(os.read(video, 16))
if os.environ.get("PLAY_TEST_END"):
    answer()
while True:
    b = os.read(video, 65536)
    if not b:
        break
    n += len(b)
open(os.environ["PLAY_TEST_SEEN"], "w").write(str(n))
open(os.environ["PLAY_TEST_SEEN"] + ".answered", "w").write(str(len(asked)))
"""


def stand_in(tmp_path: Path, monkeypatch, *, ticks: bool = False, goal: int = 0,
             end: bool = False, budget: int | None = None, deaf: bool = False,
             close_at_goal: bool = False) -> dict:
    video, cancel, end_fd = free_descriptors(3)
    monkeypatch.setattr(PLV, "VIDEO_FD", video)
    monkeypatch.setattr(PLV, "CANCEL_FD", cancel)
    monkeypatch.setattr(PLV, "END_FD", end_fd)
    monkeypatch.setattr(PLV, "TICK_WATCH_EVERY", 0.05)
    monkeypatch.setattr(PLV, "END_MARGIN_TICKS", 5)
    monkeypatch.setattr(PLV.PRC.X, "AFTER_GOAL_TICKS", 5)
    p = PLV.paths(tmp_path)
    p["player"]["dir"].mkdir()
    (tmp_path / "game.py").write_text(GAME)
    (tmp_path / "player.py").write_text(PLAYER)
    args = [str(tmp_path / "game.py"), str(p["game_fifo"])]
    if ticks:
        args += [str(p["ticks"]), str(goal)]
    loop = f"import subprocess, sys; sys.exit(subprocess.run([sys.executable, *{args!r}]).returncode)"
    env = {"PLAY_TEST_SEEN": str(tmp_path / "seen.txt")} | ({"PLAY_TEST_END": "1"} if end else {}) \
        | ({"PLAY_TEST_DEAF": "1"} if deaf else {}) \
        | ({"PLAY_TEST_CLOSE_AT_GOAL": str(p["ticks"])} if close_at_goal else {})
    monkeypatch.setattr(PLV, "END_ASK_GRACE", 1.0)
    return {"paths": p, "build": f"test-play-{os.getpid()}", "binary": Path(sys.executable),
            "cmd": [sys.executable, "-c", loop],
            "player_cmd": [sys.executable, str(tmp_path / "player.py"), "--video-fd", str(video),
                           "--cancel-fd", str(cancel), "--end-fd", str(end_fd),
                           "--input-path", str(p["input_fifo"])],
            "player_env": dict(os.environ, **env),
            "limits": {"stream_cap_bytes": 10 ** 15, "disk_floor_bytes": 0},
            "pace": {"on": False, "lead_frames": None},
            "receipt": {"on": ticks, "level": 10},
            "synthetic": {"max_frames": budget} if budget else None}


def ended_cleanly(got: dict, tmp_path: Path, answered: int = 1) -> None:
    """The game asked once and gone, and the player given the whole stream: nobody cancelled.
    The player answered `answered` end requests (it is asked before the game, so its keys
    are up when the game stops)."""
    assert got["stop"]["how"] == "SIGTERM: WM_QUIT at the game's next keyboard poll"
    assert (got["game_rc"], got["player_rc"], got["interrupted"], got["player_cancelled"],
            got["limit"]) == (0, 0, False, False, None)
    assert got["relay"]["end"] == "the writer closed the pipe"
    assert int((tmp_path / "seen.txt").read_text()) == got["relay"]["bytes"]
    assert int((tmp_path / "seen.txt.answered").read_text()) == answered


def test_the_window_s_close_asks_for_the_end_and_the_player_reads_to_it(tmp_path, monkeypatch):
    got = PLV.run_session(stand_in(tmp_path, monkeypatch, end=True))
    ended_cleanly(got, tmp_path)
    assert got["end"]["by"] == "the window's close"


def test_a_player_that_does_not_answer_is_not_waited_on_past_the_grace(tmp_path, monkeypatch):
    got = PLV.run_session(stand_in(tmp_path, monkeypatch, ticks=True, goal=20, deaf=True))
    ended_cleanly(got, tmp_path, answered=0)
    assert got["end"]["by"] == "the goal"
    assert got["end"]["at_s"] + PLV.END_ASK_GRACE <= got["seconds"]["game_ended"] + 0.5


def test_the_goal_ends_the_session_by_itself(tmp_path, monkeypatch):
    got = PLV.run_session(stand_in(tmp_path, monkeypatch, ticks=True, goal=20))
    ended_cleanly(got, tmp_path)
    assert {k: got["end"][k] for k in ("by", "entered_tick", "goal_tick")} == {
        "by": "the goal", "entered_tick": 3, "goal_tick": 20}
    assert got["end"]["tick"] >= 20 + 5 + 5
    rows = (tmp_path / "play-ticks.tsv").read_text().splitlines()
    assert len(rows) - 1 >= 30                              # the game ran past the margin


def test_a_close_at_the_goal_waits_for_the_clause_s_ticks_past_it(tmp_path, monkeypatch):
    """The window closed as the goal ring is touched: the player's keys are already up, and
    the game runs on to the clause's AFTER_GOAL_TICKS past the goal before it is asked to
    stop, so the session is one the clause can take (it refuses one that ends sooner, and
    play_receipt then does not track it)."""
    pl = stand_in(tmp_path, monkeypatch, ticks=True, goal=20, close_at_goal=True)
    monkeypatch.setattr(PLV.PRC.X, "AFTER_GOAL_TICKS", 40)
    monkeypatch.setattr(PLV, "END_MARGIN_TICKS", 10 ** 6)      # the goal's own end never comes
    got = PLV.run_session(pl)
    ended_cleanly(got, tmp_path)
    assert got["end"]["by"] == "the window's close"
    hold = got["goal_hold"]
    assert hold["goal_tick"] == 20 and hold["from_tick"] < 20 + 40 <= hold["to_tick"], hold
    rows = (tmp_path / "play-ticks.tsv").read_text().splitlines()[1:]
    assert len(rows) - 20 >= 40                 # check_s5_exit.session()'s `after`


def test_the_wait_past_the_goal_is_bounded(tmp_path, monkeypatch):
    """A game that cannot make the ticks (here: the clause's count out of its reach) is asked
    to stop GOAL_HOLD_S after the end was asked for, not never."""
    pl = stand_in(tmp_path, monkeypatch, ticks=True, goal=20, close_at_goal=True)
    monkeypatch.setattr(PLV.PRC.X, "AFTER_GOAL_TICKS", 10 ** 6)
    monkeypatch.setattr(PLV, "GOAL_HOLD_S", 0.5)
    got = PLV.run_session(pl)
    ended_cleanly(got, tmp_path)
    assert 0.5 <= got["goal_hold"]["seconds"] < 3, got["goal_hold"]
    assert got["goal_hold"]["to_tick"] - 20 < 10 ** 6


def test_an_end_with_no_goal_in_the_table_does_not_wait(tmp_path, monkeypatch):
    got = PLV.run_session(stand_in(tmp_path, monkeypatch, ticks=True, end=True))
    ended_cleanly(got, tmp_path)
    assert got["end"]["by"] == "the window's close" and got["goal_hold"] is None


def test_the_frame_budget_ends_a_session_with_no_person(tmp_path, monkeypatch):
    got = PLV.run_session(stand_in(tmp_path, monkeypatch, ticks=True, budget=25))
    ended_cleanly(got, tmp_path)
    assert got["end"]["by"] == "--max-frames" and got["end"]["frames"] >= 25


def test_the_first_ctrl_c_asks_for_the_end_and_the_handler_is_put_back(tmp_path, monkeypatch):
    before = signal.getsignal(signal.SIGINT)
    pl = stand_in(tmp_path, monkeypatch)
    timer = threading.Timer(1.5, signal.raise_signal, args=(signal.SIGINT,))
    timer.start()
    try:
        got = PLV.run_session(pl)
    finally:
        timer.cancel()
    ended_cleanly(got, tmp_path)
    assert got["end"]["by"] == "Ctrl-C"
    assert signal.getsignal(signal.SIGINT) is before


def test_a_session_the_game_ends_says_so(tmp_path, monkeypatch):
    pl = stand_in(tmp_path, monkeypatch)
    pl["cmd"] = [sys.executable, "-c", "import time; time.sleep(0.5)"]
    got = PLV.run_session(pl)
    assert got["end"]["by"] == "the game ended" and got["stop"] is None
    assert not (tmp_path / "seen.txt.answered").exists()       # no player ever ran


# ---------------------------------------------------------------- the closing line


def test_the_closing_line_gives_the_stage_s_latency_or_says_why_there_is_none():
    latency = {"summary": {
        "presses": 30, "by_status": {"presented": 27, "lost": 1, "unsent": 1,
                                     "unpresented": 1},
        "total_us": {"p50": 41_500, "p90": 60_000, "max": 90_250},
        "from_dequeue_us": {"p50": 40_000, "p90": 58_000, "max": 88_000},
        "stage": {"presses": 25, "by_status": {"presented": 23, "lost": 1, "unsent": 1,
                                               "unpresented": 0},
                  "total_us": {"p50": 42_000, "p90": 61_000, "max": 90_250},
                  "from_dequeue_us": {"p50": 40_500, "p90": 59_000, "max": 88_000}}}}
    assert PLV.latency_said(latency) == (
        "input latency, press to presented frame: median 42.0 ms, p90 61.0, max 90.2 over 23 "
        "presses in the stage (1 lost, 1 unsent, 0 unpresented; from dequeue: median 40.5 ms, "
        "p90 59.0, max 88.0)")
    latency["summary"]["stage"]["total_us"] = None
    latency["diagnostic"] = True
    assert PLV.latency_said(latency).startswith(
        "input latency (diagnostic), press to presented frame: median 41.5 ms")
    assert PLV.latency_said(latency).endswith("over 27 presses in the session (1 lost, 1 "
                                              "unsent, 1 unpresented; from dequeue: median "
                                              "40.0 ms, p90 58.0, max 88.0)")
    assert PLV.latency_said(None) == "input latency: not measured"
