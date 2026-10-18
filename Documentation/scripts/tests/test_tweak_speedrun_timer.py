"""The speedrun-timer host tweak (host/launch/tweaks/speedrun-timer.{h,cpp}): the launcher's
read-only watch over the game's timer, sent as UDP datagrams to 127.0.0.1:16838 and as LiveSplit
Server commands to 127.0.0.1:16834.

The tweak's file is built with the system g++, as host/launch/build.sh builds the launcher, with
the host's reader (host/player/tweaks.cpp) and a driver that does what sa2_launch.cpp does in
play: reads SA2_TWEAKS for the launcher's home, forks a child as sa2_launch_posix.cpp does, makes
the Watch on its id before the child's exec, waits for it to end; the Watch's record (the
launcher's said_line, kept in launcher.log) goes to a file, SA2ST_LOG. The child stands in for the
game: it maps the guest space as runtime/guest_memory.c does (one SA2_GUEST_END mapping at an
address the host chooses), behind a decoy mapping of the same size with no image in it, writes sonic2app.exe's PE header shape at guest 0x00400000, and then
plays a script, one row per frame, into the guest words speedrun-timer.h names. The test listens
on both ports, so it needs them free; its LiveSplit Server stub answers getcurrenttimerphase as
LiveSplit does, and can be reset as a runner resets it.
"""
from __future__ import annotations

import os
import shutil
import socket
import struct
import subprocess
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LAUNCH = ROOT / "host" / "launch"
PLAYER = ROOT / "host" / "player"
CXX = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-pthread"]
UDP_PORT, LIVESPLIT_PORT = 16838, 16834
HEAD = "sa2recomp: sa2 host tweaks: speedrun timer published: "

FAKE_GAME = r"""
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <sys/mman.h>
#include <unistd.h>
// argv[1]: "image" or "bare"; argv[2]: the script, one frame a line:
//   mode level state character minutes seconds frames timer_stopped stop2 time_stopped latch
int main(int argc, char** argv) {
    if (argc != 3) return 2;
    const size_t size = 0x20000000u;
    void* decoy = mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    uint8_t* g = (uint8_t*)mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (decoy == MAP_FAILED || g == MAP_FAILED) return 3;
    ((uint8_t*)decoy)[0x400000] = 'M';
    if (!strcmp(argv[1], "image")) {
        uint8_t* h = g + 0x400000;
        h[0] = 'M'; h[1] = 'Z';
        h[0x3C] = 0x80;
        memcpy(h + 0x80, "PE\0\0", 4);
        h[0x80 + 24] = 0x0B; h[0x80 + 25] = 0x01;
        h[0x80 + 24 + 28 + 2] = 0x40;                      // ImageBase 0x00400000
    }
    FILE* f = fopen(argv[2], "r");
    if (!f) return 4;
    usleep(400000);
    unsigned m, lv, st, ch, mi, se, fr, ts, s2, tst, la;
    uint32_t frame = 0;
    while (fscanf(f, "%u %u %u %u %u %u %u %u %u %u %u", &m, &lv, &st, &ch, &mi, &se, &fr, &ts, &s2, &tst, &la) == 11) {
        volatile uint8_t* v = g;
        v[0x0174AFDA] = ts; v[0x0174AFDB] = mi; v[0x0174AFDC] = se; v[0x0174AFDD] = fr;
        v[0x0174AFF7] = tst; v[0x0174AFF9] = s2; v[0x0174B002] = la;
        *(volatile uint32_t*)(g + 0x0174B040) = m;
        *(volatile int16_t*)(g + 0x01934B70) = (int16_t)lv;
        v[0x01934B80] = ch;
        *(volatile int16_t*)(g + 0x01934BE0) = (int16_t)st;
        *(volatile uint32_t*)(g + 0x0174B03C) = frame * 3;
        *(volatile uint32_t*)(g + 0x0174B038) = ++frame;
        usleep(12000);
    }
    usleep(200000);
    return 0;
}
"""

DRIVER = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include "tweaks.h"
#include "tweaks/speedrun-timer.h"
extern char** environ;
// The launcher's record, as sa2_launch.cpp's said_line keeps it and main() writes launcher.log.
static FILE* g_log = nullptr;
static void said_line(const std::string& line) {
    if (g_log) std::fprintf(g_log, "sa2recomp: %s\n", line.c_str()), std::fflush(g_log);
}
int main(int argc, char** argv) {
    const sa2tw::Reading r = sa2tw::read(std::getenv("SA2_TWEAKS"), sa2tw::off(nullptr), "launcher");
    sa2tw::g_on = r.on;
    if (r.on) std::fprintf(stderr, "sa2recomp: %s\n", r.line.c_str());
    else if (!r.line.empty()) std::fprintf(stderr, "%s\n", r.line.c_str());
    if (const char* log = std::getenv("SA2ST_LOG")) g_log = std::fopen(log, "w");
    if (argc < 2) return 2;
    if (argc == 3 && !std::strcmp(argv[1], "--pid")) {   // a Watch on a process not the driver's own
        const sa2st::Watch watch(std::atoi(argv[2]), &said_line);
        usleep(300000);
        return 0;
    }
    // As sa2_launch_posix.cpp spawns: fork, and the Watch made as fork returns, before the child's
    // exec (held back here, so the Watch's first look is always at the forked copy).
    const pid_t pid = fork();
    if (pid == 0) {
        usleep(150000);
        execve(argv[1], argv + 1, environ);
        _exit(127);
    }
    if (pid < 0) return 2;
    const sa2st::Watch watch(pid, &said_line);
    int status = 0;
    waitpid(pid, &status, 0);
    usleep(100000);
    return WIFEXITED(status) ? WEXITSTATUS(status) : 3;
}
"""

# One row per frame: (mode, level, state, character, minutes, seconds, frames, timer_stopped,
# stop2, time_stopped, latch).
def frames_of(n: int) -> tuple[int, int, int]:
    return n // 3600, n // 60 % 60, n % 60


def stage(level: int, ticks: range, latch: int = 0, stopped: int = 0, state: int = 16) -> list[tuple]:
    return [(5, level, state, 0, *frames_of(t), stopped, 0, 0, latch) for t in ticks]


MENU = [(14, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)] * 5
SCRIPT = (MENU
          # stage 1 (City Escape, 13): 0 .. 130, a pause that holds the timer, the goal at 130
          + stage(13, range(0, 101)) + stage(13, [100] * 8, state=17) + stage(13, range(101, 131))
          + stage(13, [130] * 10, latch=1, stopped=1)
          + [(7, 13, 4, 0, *frames_of(130), 1, 0, 0, 1)] * 3 + [(7, 13, 4, 0, 0, 0, 0, 0, 0, 0, 0)] * 3
          # stage 2: run to 70 and left from the pause menu, no goal
          + stage(16, range(0, 71)) + [(7, 16, 4, 0, 0, 0, 0, 0, 0, 0, 0)] * 3
          # stage 3: to 50, a checkpoint restart back to 30, on to 65, the goal
          + stage(17, range(0, 51)) + stage(17, range(30, 66)) + stage(17, [65] * 10, latch=1, stopped=1)
          + [(7, 17, 4, 0, 0, 0, 0, 0, 0, 0, 0)] * 3)
FINAL = 130 + 65
# A second attempt from the game's menu after the first, as a runner who resets LiveSplit and plays
# again without leaving the game: City Escape to 60, its goal.
SECOND = (MENU * 12 + stage(13, range(0, 61)) + stage(13, [60] * 10, latch=1, stopped=1)
          + [(7, 13, 4, 0, 0, 0, 0, 0, 0, 0, 0)] * 3)
TWO = SCRIPT + SECOND


@pytest.fixture(scope="module")
def exes(tmp_path_factory) -> dict:
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the launcher is built with it (host/launch/build.sh)")
    d = tmp_path_factory.mktemp("speedrun-timer")
    (d / "fake.cpp").write_text(FAKE_GAME)
    (d / "driver.cpp").write_text(DRIVER)
    out = {"fake": d / "fake-game", "driver": d / "driver", "obj": d / "speedrun-timer.o", "dir": d}
    for cmd in ([*CXX, str(d / "fake.cpp"), "-o", str(out["fake"])],
                [*CXX, f"-I{LAUNCH}", f"-I{PLAYER}", "-c", str(LAUNCH / "tweaks" / "speedrun-timer.cpp"),
                 "-o", str(out["obj"])],
                [*CXX, f"-I{LAUNCH}", f"-I{PLAYER}", str(d / "driver.cpp"), str(out["obj"]),
                 str(PLAYER / "tweaks.cpp"), "-o", str(out["driver"])]):
        done = subprocess.run(cmd, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    (d / "script.txt").write_text("".join(" ".join(map(str, r)) + "\n" for r in SCRIPT))
    (d / "two.txt").write_text("".join(" ".join(map(str, r)) + "\n" for r in TWO))
    return out


PHASE_ASK = "getcurrenttimerphase"


class Listeners:
    """The UDP port's datagrams and a LiveSplit Server stub's commands, while a run lasts. The stub
    keeps LiveSplit's timer phase (NotRunning; starttimer makes it Running) and answers
    getcurrenttimerphase with it, a CRLF line, as LiveSplit Server does. `reset_at_split`: the
    runner resets LiveSplit (the phase back to NotRunning) as that split arrives."""

    def __init__(self, livesplit: bool, reset_at_split: int = 0):
        self.phase, self.reset_at_split, self.resets = "NotRunning", reset_at_split, 0
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.udp.bind(("127.0.0.1", UDP_PORT))
        except OSError as e:
            pytest.fail(f"UDP 127.0.0.1:{UDP_PORT} is taken ({e}); the test listens there as a reader would")
        self.udp.settimeout(0.05)
        self.datagrams: list[bytes] = []
        self.commands: list[str] = []
        self.tcp = None
        if livesplit:
            self.tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.tcp.bind(("127.0.0.1", LIVESPLIT_PORT))
            except OSError as e:
                pytest.fail(f"TCP 127.0.0.1:{LIVESPLIT_PORT} is taken ({e}); the test's LiveSplit Server stub listens there")
            self.tcp.listen(1)
        self.done = False
        self.threads = [threading.Thread(target=self._udp)] + ([threading.Thread(target=self._tcp)] if livesplit else [])
        for t in self.threads:
            t.start()

    def _udp(self):
        while not self.done:
            try:
                self.datagrams.append(self.udp.recv(256))
            except socket.timeout:
                pass

    def _tcp(self):
        self.tcp.settimeout(0.05)
        conn, buf = None, b""
        while not self.done:
            try:
                if conn is None:
                    conn, _ = self.tcp.accept()
                    conn.settimeout(0.05)
                chunk = conn.recv(4096)
                if not chunk:
                    conn = None
                buf += chunk
                *lines, tail = buf.split(b"\r\n")
                for x in lines[len(self.commands):]:
                    self._command(x.decode(), conn)
            except socket.timeout:
                pass

    def _command(self, c: str, conn) -> None:
        self.commands.append(c)
        if c == PHASE_ASK:
            conn.sendall(self.phase.encode() + b"\r\n")
        elif c == "starttimer" and self.phase == "NotRunning":
            self.phase = "Running"
        elif c == "split" and self.commands.count("split") == self.reset_at_split:
            self.phase, self.resets = "NotRunning", self.resets + 1

    def told(self) -> list[str]:
        """The commands, the phase questions left out."""
        return [c for c in self.commands if c != PHASE_ASK]

    def close(self):
        time.sleep(0.1)
        self.done = True
        for t in self.threads:
            t.join()
        self.udp.close()
        if self.tcp:
            self.tcp.close()


def run(exes: dict, kind: str = "image", livesplit: bool = True, script: str = "script.txt", reset_at_split: int = 0,
        argv: list[str] | None = None, **env: str) -> tuple[subprocess.CompletedProcess, Listeners]:
    environ = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    environ.update(env)
    log = exes["dir"] / "launcher.log"
    log.unlink(missing_ok=True)
    environ["SA2ST_LOG"] = str(log)
    lis = Listeners(livesplit, reset_at_split)
    try:
        done = subprocess.run([str(exes["driver"]), *(argv or [str(exes["fake"]), kind, str(exes["dir"] / script)])],
                              capture_output=True, text=True, env=environ, timeout=60)
    finally:
        lis.close()
    done.log = log.read_text() if log.is_file() else ""
    return done, lis


def tweak_lines(text: str) -> list[str]:
    return [x for x in text.splitlines() if x.startswith(HEAD)]


FIELDS = struct.Struct("<4sHHIIIIhhBBBBBBBBIIIII8s")


def decode(d: bytes) -> dict:
    (magic, version, size, seq, fc, fci, mode, state, level, char, mins, secs, frames, ts, s2, tst, latch,
     stage_frames, game_time, splits, started, ms, rest) = FIELDS.unpack(d)
    return dict(magic=magic, version=version, size=size, seq=seq, frame_count=fc, ingame=fci, mode=mode,
                state=state, level=level, character=char, row=(mode, level, state, char, mins, secs, frames, ts, s2,
                                                              tst, latch),
                stage=stage_frames, game_time=game_time, splits=splits, started=started, ms=ms, rest=rest)


def test_on_it_sends_a_datagram_per_change_with_the_guest_words(exes):
    done, lis = run(exes, SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0, done.stderr
    assert FIELDS.size == 64 and all(len(d) == 64 for d in lis.datagrams)
    got = [decode(d) for d in lis.datagrams]
    assert [g["seq"] for g in got] == list(range(1, len(got) + 1))
    assert all(g["magic"] == b"SA2T" and g["version"] == 1 and g["size"] == 64 and g["rest"] == bytes(8) for g in got)
    # Each frame of the script, seen as written; a 2 ms poll sees nearly every 12 ms frame.
    seen = {g["frame_count"]: g for g in got if g["frame_count"]}
    assert len(seen) >= 0.9 * len(SCRIPT), f"{len(seen)} of {len(SCRIPT)} frames seen"
    assert len(got) <= len(SCRIPT) + 1          # one datagram per change, the first sample included
    for fc, g in seen.items():
        row = SCRIPT[fc - 1]
        assert g["row"] == row and g["ingame"] == (fc - 1) * 3, fc
        assert g["stage"] == (row[4] * 60 + row[5]) * 60 + row[6]
    assert got[-1]["splits"] == 2 and got[-1]["started"] == 1 and got[-1]["game_time"] == FINAL
    lines = done.stderr.splitlines()
    assert lines[0] == "sa2recomp: sa2 host tweaks (launcher): speedrun timer published (SA2_TWEAKS=speedrun-timer)"
    assert lines[1].startswith(HEAD + "UDP datagrams to 127.0.0.1:16838 (64 bytes each")
    assert lines[2].startswith(HEAD + "game time is each cleared stage's time plus the running stage's timer; left alone: ")
    assert any(x.startswith(HEAD + "the game's guest memory found at 0x") for x in lines)
    assert lines[3].startswith(HEAD + "a run ends only by LiveSplit's reset")
    assert lines[-1].startswith(HEAD + f"{len(got)} datagrams, 2 splits, game time 0:00:03.250, ")
    assert lines[-1].endswith("; the game's memory can no longer be read (the game ended)")
    # The launcher's record (launcher.log) holds every line the tweak said, in order, the guest's
    # finding and the end summary among them.
    log = done.log.splitlines()
    assert log == tweak_lines(done.stderr) and len(log) >= 6
    assert any(x.startswith(HEAD + "the game's guest memory found at 0x") for x in log)
    assert log[-1] == lines[-1]


def test_livesplit_gets_the_start_the_game_time_and_a_split_at_each_goal(exes):
    done, lis = run(exes, SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0, done.stderr
    c = lis.told()
    assert lis.commands.count(PHASE_ASK) >= 2         # asked every 250 ms while connected
    assert c[:3] == ["starttimer", "initgametime", "pausegametime"]
    assert c.count("starttimer") == 1 and c.count("split") == 2 and "reset" not in c
    # The game time each split is given: stage 1's 130 frames, then 130 + stage 3's 65.
    before = [c[i - 1] for i, x in enumerate(c) if x == "split"]
    assert before == ["setgametime 0:00:02.166", "setgametime 0:00:03.250"]
    assert c[-1] == "split"          # the fall to the next 0:00:00 moves the game time no further
    times = [x.split()[1] for x in c if x.startswith("setgametime ")]
    assert all(a != b for a, b in zip(times, times[1:]))      # sent when it changes, only then
    # Stage 2, left without its goal, rose to 130 + 70 and fell back: it adds nothing.
    assert "0:00:03.333" in times and times.index("0:00:03.333") < len(times) - 1
    assert "LiveSplit Server connected at 127.0.0.1:16834" in done.stderr
    assert done.stderr.splitlines()[-1].split("; ")[-2].startswith("LiveSplit Server: ")
    assert "LiveSplit Server connected at 127.0.0.1:16834" in done.log
    assert "reset" not in done.stderr.lower().replace("livesplit's reset", "")   # no reset: one run


def test_after_a_livesplit_reset_the_next_stage_start_begins_a_new_run(exes):
    """Two attempts in one game process, LiveSplit reset between them as the runner resets it: the
    second gets its own starttimer, and its game time and splits count from 0."""
    done, lis = run(exes, script="two.txt", reset_at_split=2, SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0, done.stderr
    assert lis.resets == 1
    c = lis.told()
    assert c.count("starttimer") == 2 and c.count("split") == 3 and "reset" not in c
    second = c.index("starttimer", 1)
    assert c[second:second + 3] == ["starttimer", "initgametime", "pausegametime"]
    assert c[second - 1] == "split" and c[second - 2] == "setgametime 0:00:03.250"     # run 1's end
    # Run 2's game time is its own City Escape, from 0 to 60 frames, then its split.
    times = [x.split()[1] for x in c[second:] if x.startswith("setgametime ")]
    assert times[0] < "0:00:00.100" and times[-1] == "0:00:01.000" and c[-1] == "split"
    got = [decode(d) for d in lis.datagrams]
    assert got[-1]["game_time"] == 60 and got[-1]["splits"] == 1 and got[-1]["started"] == 1
    # Between the reset and the new run's start the datagram says no run.
    assert any(g["started"] == 0 and g["game_time"] == 0 and g["splits"] == 0 and g["mode"] == 14 for g in got[1:]
               if g["frame_count"] > len(SCRIPT))
    reset = HEAD + "LiveSplit's timer was reset after 2 splits at game time 0:00:03.250; the run ended"
    for text in (done.stderr, done.log):
        assert text.count(reset) == 1
        assert text.count(HEAD + "run 2 begins at this stage start (game time from 0:00:00.000)") == 1
        assert tweak_lines(text)[-1].startswith(HEAD + f"{len(got)} datagrams, 2 runs; the last: 1 splits, "
                                                "game time 0:00:01.000, ")


def test_with_no_livesplit_the_datagram_run_lasts_the_game_process(exes):
    """The limit said at the start: nothing tells the watch that a run ended, so a second attempt
    adds to the first's game time and splits."""
    done, lis = run(exes, livesplit=False, script="two.txt", SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0, done.stderr
    last = decode(lis.datagrams[-1])
    assert last["game_time"] == FINAL + 60 and last["splits"] == 3 and last["started"] == 1
    assert "with no LiveSplit Server connected, the datagram's game time and splits are the game process's first run" \
        in done.log


def test_without_livesplit_the_datagrams_go_on_and_its_absence_is_said_once(exes):
    done, lis = run(exes, livesplit=False, SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0, done.stderr
    assert decode(lis.datagrams[-1])["game_time"] == FINAL
    assert done.stderr.count("LiveSplit Server is not listening at 127.0.0.1:16834") == 1
    assert "LiveSplit Server never connected at 127.0.0.1:16834" in done.stderr.splitlines()[-1]
    assert done.log.count("LiveSplit Server is not listening at 127.0.0.1:16834") == 1


def test_off_unset_or_in_a_replay_it_does_nothing(exes):
    done, lis = run(exes)
    assert done.returncode == 0 and done.stderr == "" and lis.datagrams == [] and lis.commands == []
    assert done.log == ""
    done, lis = run(exes, SA2_TWEAKS="no-event-bars")
    assert done.returncode == 0 and lis.datagrams == [] and lis.commands == []
    assert done.stderr == "sa2 host tweaks (launcher): none on (SA2_TWEAKS=no-event-bars)\n"
    done, lis = run(exes, SA2_TWEAKS="speedrun-timer", SA2_REPLAY_TICKS="x")
    assert done.returncode == 0 and lis.datagrams == [] and lis.commands == []
    assert done.stderr == ("sa2 host tweaks (launcher): SA2_TWEAKS=speedrun-timer ignored in a replay "
                           "(SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)\n")


def test_a_process_with_no_image_is_named_and_nothing_is_sent(exes):
    done, lis = run(exes, kind="bare", SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0 and lis.datagrams == [] and lis.commands == []
    summary = (HEAD + "the game's guest memory was never found (no mapping held sonic2app.exe's headers at guest "
               "0x00400000); 0 datagrams, 0 splits")
    assert done.stderr.splitlines()[-1].startswith(summary)
    assert done.log.splitlines()[-1].startswith(summary)       # kept for a launch with no terminal


def test_a_game_whose_memory_is_refused_is_named_in_the_record(exes):
    """A Watch on a process whose memory the host will not open (pid 1, another user's: as
    kernel.yama.ptrace_scope refuses a game) says why, on stderr and in launcher.log."""
    if os.geteuid() == 0:
        pytest.skip("run as root, every process's memory opens; this case needs an ordinary user")
    done, lis = run(exes, argv=["--pid", "1"], SA2_TWEAKS="speedrun-timer")
    assert done.returncode == 0 and lis.datagrams == []
    why = HEAD + "cannot read the game's memory, so nothing is published: /proc/1/mem: "
    assert done.stderr.splitlines()[-1].startswith(why)
    assert done.log.splitlines()[-1].startswith(why) and "Permission denied" in done.log.splitlines()[-1]


def test_the_watch_can_only_read_the_game(exes):
    """No call in the tweak's object writes another process's memory or stops it."""
    nm = subprocess.run(["nm", "-u", str(exes["obj"])], capture_output=True, text=True, check=True).stdout
    undefined = {line.split()[-1].split("@")[0] for line in nm.splitlines() if line.strip()}
    assert {"pread", "pread64"} & undefined
    assert not {"pwrite", "pwrite64", "write", "process_vm_writev", "ptrace", "mmap", "kill"} & undefined
    src = (LAUNCH / "tweaks" / "speedrun-timer.cpp").read_text()
    assert "O_RDONLY" in src and "WriteProcessMemory" not in src and "O_RDWR" not in src
