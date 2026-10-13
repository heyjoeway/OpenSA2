#!/usr/bin/env python3
"""The boot replay live through DXVK Native: the game's call stream reaches the player as it is made.

Subtitle: tools/s5/present_run.py's presentation build of the boot replay, its recorder
writing into a named pipe instead of a file; a relay that puts every byte on disk and
hands it on to host/player's sa2_player (DXVK Native in an SDL3 window) as it arrives;
then the runner's own checks on the live run, the live frames held to a file replay's
frame for frame, the checks that the frames reached the window while the game ran and
that the named frame was presented to a window the compositor had mapped, and the
receipts.

Usage:
  .venv/bin/python tools/s5/present_live.py --file-stream S --file-replay R   # build, run live, check
  .venv/bin/python tools/s5/present_live.py --check              # hold the last live run again
  (--out DIR, --player-bin DIR, --build NAME, --file-run DIR, --receipt PATH and --wait
   move or add to the defaults below)

Why a tool
----------
A live run is three parties that have to start in order and end cleanly whatever one of
them does -- the game (through tools/s4/loop_run.py), the relay, and the player -- and its
claim, that this is the registered boot replay and that these are the frames a file replay
of its stream shows, reaching a window while the game ran, is worth something only if it
is checked the same way every time. So the run, the checks and the receipts are one
command, and the receipts are generated.

Two processes, and why
----------------------
The game is a static binary (tools/s4/link_s4.py links it for x86_64-linux-musl) and
cannot load DXVK Native, a glibc shared library: a static binary has no dynamic loader.
So DXVK runs in a second process, the player (host/player, glibc), and the game's calls
reach it as the recording device's COM objects answered them: the recorder
(host/sa2_present_rec.c) takes every call at those objects' thunks and serialises it into
the stream (host/sa2_stream.h), and the player replays each call on DXVK's own device as
soon as its bytes arrive. Nothing flows back to the game.

The transport
-------------
The recorder open(2)s SA2_REC_TRACE and write(2)s whole frames: it flushes after every
SA2S_FRAME and never seeks. So a named pipe at that path is a stream it writes unchanged,
and no recorder change is needed. This tool makes the pipe and opens its read end before
the game starts (O_NONBLOCK, so the game's open never waits for a reader). Then two
threads relay:

  * the reader (this process's main thread) takes each chunk off the pipe and appends it to
    a copy on disk, and nothing else: it never waits for the player, so neither does the
    game. It also finds each SA2S_FRAME record in passing, by the framing alone
    (FrameClock), and notes when it arrived. (tools/s5/play_live.py's paced sessions hold
    the reader to the player instead, so that the game waits: relay's lead_frames.)
  * the feeder hands the copy to the player's stdin as it grows. sa2_player reads "-"
    through the same reader it uses for a file (host/player/stream.cpp), and replays each
    frame, Present included, the moment its bytes are in; while it waits for bytes it
    answers its window's events (the reader's idle hook).

The reader holds the pipe open until the game closes it, so the game never writes into a
pipe nobody reads (SIGPIPE, which the runtime does not catch) even if the player dies: the
copy still takes every byte and the run still ends as the registered run. A third thread
waits on the game and notes when it exited, so the game's time is its own and not the
player's catch-up.

What the guest can see: nothing new. The build, the recorder and the run are
present_run's, with the stream going to the pipe (present_run's partial path) instead of a
file; the pipe is drained by a thread that only writes a file, so the game's writes wait on
disk at most, never on the window; and wall time reaches no guest value (the virtual clock,
runtime/sa2_imports.c). The one wall-clock input the run has is the probe's alarm, which a
live run turns off (--probe-arg=--timeout 0: runtime/sa2_call.c alarms only for a timeout
that is not 0), with loop_run's process bound raised to RUN_TIMEOUT: r2's report, section 6
(private/generated/s5/dxvk/understand/r2-build-and-run.md).

The checks (each can fail; the receipt keeps what each read)
------------------------------------------------------------
  inputs       present_run.check_inputs: the tree's inputs are the registered result's
  build        present_run.check_build: both hooks compiled afresh, the recorder object
               held to tools/s5/objcheck.py
  run.*        present_run.checks on the live run, as it holds a file run: lifted_csv,
               reach, record, configuration, words, counts, and stream (here the relay's
               copy: whole, true to its rules, its meta this run's, its sidecar closed)
  records      the copy's records equal, byte for byte, those of --file-stream, a file
               run's stream from the same recorder and header (its meta names them), whose
               READY names its sha256; the headers differ only in their meta
  delivery     the recorder's own count of what it wrote (its sidecar: records, bytes,
               frames) is what the relay read, what the copy holds and what the player was
               given; the player hashed what it read to the copy's sha256
  player       the player's own verdict: pass, exit 0, every Present shown and read back,
               the stream whole to its END, 0 errors, 0 calls not handled, 0 Presents or
               read-backs failed
  frames       the live frames equal --file-replay's frame for frame: each frame's
               bgrx_hash (FNV-1a over the read-back BGR of every pixel), virtual time,
               guest frame and poll count (frames.y4m.times.tsv), and the y4m files byte
               for byte
  first_non_black  the first Present whose read-back picture is not all black (its
               bgrx_hash is not the hash of an all-black frame); its PNG, byte-equal to the
               file replay's PNG of that frame
  liveness     on one clock (CLOCK_MONOTONIC: the relay's, the player's start time, which
               its report must carry, and the word log's own clocks): every frame was read
               back after its bytes reached the relay and before the game exited, frame 1
               before the game's last tick entered GameLoop, and the player read a pipe
  on_screen    the desktop session was a local wayland or x11 one, active, not locked and
               not idle, before the run and after it; the player's SDL video driver was
               wayland or x11; the window's state was sampled at every Present, and every
               Present succeeded; and the named frame -- the first frame after the one
               whose Present mapped the window, whose picture is not black, at whose Present
               the window held the compositor's keyboard focus and was not suspended -- has
               a PNG byte-equal to the file replay's
  receipt_paths  every file the receipts cite exists (check_cited_files.py's own rules),
               and no path in them points into a checkout by its absolute path

What SDL3 can and cannot observe of the window on Wayland (SDL 3.4.16, read in
private/s5-tools/src/SDL3-3.4.16): two of its window flags come from the compositor --
keyboard focus (wl_keyboard.enter, which a compositor sends only to a mapped surface;
SDL_waylandevents.c) and SDL_WINDOW_OCCLUDED, which SDL sets only from xdg_toplevel's
"suspended" state (SDL_waylandwindow.c), itself the compositor's policy. The rest do not:
SDL gets no minimize events from xdg-shell, HIDDEN is set only by SDL_HideWindow, and the
SHOWN and EXPOSED events come from SDL_CreateWindow itself before any buffer exists. So
"on screen" here rests on the session, the focus, the suspended state and the Presents;
no capture of the screen is taken (on Wayland that needs the screenshot portal, which asks
the user).

Outputs, under --out (default private/generated/s5/live/run/): the run's own files as
present_run writes them (lifted.csv, lifted-words.bin.zst, counts.json, run.json, ...),
boot.fifo and the recorder's sidecar boot.fifo.status, boot.live.sa2s (the relay's copy,
with the sidecar copied beside it), relay.json, player/ (frames.y4m and its times, snaps/,
report.json, player.log), dxvk-log/, and present-live.json, the full receipt. The
checkpoint receipt, --receipt (default private/generated/s5/live/s5-first-frame-v1.json),
is generated from it.

Never run it during a gate pass or beside another presentation run: the build and the run
are heavy, and the machine is shared. It waits for those to end, for memory, and for an
unlocked, non-idle desktop session, up to --wait seconds, and then refuses.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import hashlib
import json
import os
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import present_run as PR  # noqa: E402  the runner whose build, run and checks this reuses
import check_cited_files as CCF  # noqa: E402  the cited-files gate's own rules
from check_s5_exit import AUDIO_TOOL  # noqa: E402  the audio-scenes tool, as G5's contract names it

TR, RR, SS, WL = PR.TR, PR.RR, PR.SS, PR.WL

GEN_S5 = Path(PR.GEN_S5)
LIVE = GEN_S5 / "live"
PROBE_TIMEOUT = 0        # no alarm around the guest call (r2 section 6; runtime/sa2_call.c)
RUN_TIMEOUT = 3600       # loop_run's process bound for the game, in seconds
FEED_GRACE = 600         # after the game ends, how long the player may take to catch up
FILE_STREAM = GEN_S5 / "trace" / "boot.sa2s"
FILE_REPLAY = GEN_S5 / "play" / "boot"
PLAYER_DIR = ROOT / "host" / "player"
DXVK_LIB = "private/s5-tools/install/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0"
SDL3_LIB = "private/s5-tools/install/lib/libSDL3.so.0"
CODE_DIRS = ("host", "tools/s5")            # every file here is hashed into the receipt
CODE_PREFIXES = ("host/", "tools/", "runtime/", "harness/", "Documentation/scripts/")
MAGIC = b"SA2D3D9S"
KIND_FRAME = 3
CHUNK = 1 << 20
F_SETPIPE_SZ = 1031      # fcntl.h's value; the fcntl module does not name it everywhere
# A held relay (relay's lead_frames): the feeder hands the copy on in LEAD_PIECE pieces, so
# what it has handed on is known to within one, and the writer's pipe is sized to the last
# PIPE_FIT_FRAMES frames, from PIPE_MIN up to CHUNK (pipe_size_for).
LEAD_PIECE = 16 << 10
PIPE_MIN = 16 << 10
PIPE_FIT_FRAMES = 8
# A held relay takes its writer as no longer waiting on it after this many whole frames
# running with no pause finding its pipe full where a pause could have come (fit_pipe).
QUIET_FRAMES = 2
FNV_BASIS, FNV_PRIME = 0xCBF29CE484222325, 0x100000001B3
GATE_SCRIPTS = ("Documentation/scripts/run_all_gates.py", "Documentation/scripts/ship.py")
MIN_FREE_GB = 6.0        # the link peaks near 1 GB, the game and the player take a few more
# SDL_WindowFlags bits (include/SDL3/SDL_video.h, SDL 3.4.16), as the player records them.
SDL_WINDOW_OCCLUDED = 0x4
SDL_WINDOW_HIDDEN = 0x8
SDL_WINDOW_MINIMIZED = 0x40
SDL_WINDOW_INPUT_FOCUS = 0x200
SDL_WINDOW_MOUSE_FOCUS = 0x400
VIDEO_DRIVERS = ("wayland", "x11")
# The recorder's sidecar once its stream is closed (host/sa2_present_rec.c, rec_exit).
SIDECAR_CLOSED = re.compile(r"closed: (\d+) records, (\d+) bytes, (\d+) frames")
TRANSPORT = ("two processes: the game is a static binary (musl) and cannot load DXVK Native, "
             "a glibc library, so DXVK runs in a separate player process (host/player). The "
             "recorder (host/sa2_present_rec.c) takes every Direct3D 9 call at the recording "
             "device's COM objects, as they answered it, and writes it into the stream; the "
             "stream goes through a named pipe to this tool's relay, which hands it to the "
             "player, which replays each call on DXVK's device as its bytes arrive. Nothing "
             "flows back to the game")
WAYLAND_OBSERVES = {
    "observed": [
        "keyboard focus: SDL sets SDL_WINDOW_INPUT_FOCUS on wl_keyboard.enter "
        "(src/video/wayland/SDL_waylandevents.c), which a compositor sends only to a mapped "
        "surface; a Wayland surface is mapped by its first committed buffer, the first Present",
        "suspended: SDL sets SDL_WINDOW_OCCLUDED only from xdg_toplevel's suspended state "
        "(src/video/wayland/SDL_waylandwindow.c), and when to send that is the compositor's "
        "policy; 'not occluded' means only 'never suspended'",
    ],
    "not_observed": [
        "minimized: SDL gets no minimize events from xdg-shell (SDL_waylandwindow.c's own "
        "comment); SDL_WINDOW_MINIMIZED is set only by the application's own SDL_MinimizeWindow",
        "hidden: SDL_WINDOW_HIDDEN is set only by SDL_HideWindow (src/video/SDL_video.c)",
        "shown and exposed: Wayland_ShowWindow, called from SDL_CreateWindow, sends "
        "WINDOW_SHOWN and WINDOW_EXPOSED itself before any buffer exists",
        "covered but not suspended: nothing reports a window another window covers",
        "the pixels on the screen: no capture is taken; on Wayland one needs the screenshot "
        "portal, which asks the user",
    ],
    "sdl": "SDL 3.4.16, private/s5-tools/src/SDL3-3.4.16",
}
NAMED_FRAME_RULE = ("the first frame after the window's first Present (the commit that maps a "
                    "Wayland window) whose read-back picture is not all black and at whose "
                    "Present the window held the compositor's keyboard focus and was not "
                    "suspended")
FIRST_NON_BLACK_RULE = ("the first Present whose read-back picture is not all black: its "
                        "bgrx_hash (the player's FNV-1a over every pixel's B, G and R) is not "
                        "the hash of an all-black frame of the back buffer's size")


def rel(p) -> str:
    return PR.rel(Path(p))


def sha256(path: Path) -> str:
    return PR.sha256(Path(path))


# ---------------------------------------------------------------- the relay

class FrameClock:
    """When each SA2S_FRAME record went past, found from the framing alone
    (host/sa2_stream.h): a 16-byte file header whose last word is its length, the header
    JSON padded to 4, then records that each begin [kind, bytes, seq]. A frame's time is
    the moment its record's last byte arrived, which is when the whole frame had."""

    def __init__(self, arrival_diagnostics: dict | None = None) -> None:
        self.arrival_diagnostics = arrival_diagnostics
        if arrival_diagnostics is not None:
            arrival_diagnostics.setdefault("arrivals", [])
        self.pos = 0                 # stream bytes seen
        self.next: int | None = None  # offset of the next record, once the header is read
        self.pending = b""           # bytes from `next` on that do not yet hold its header
        self.waiting: list[int] = []  # end offsets of FRAME records not yet whole
        self.frames: list[float] = []
        self.ends: list[int] = []    # the end offset of each whole FRAME record, in order
        self.records = 0
        self.header_bytes: int | None = None
        self.problem: str | None = None

    def feed(self, data: bytes, t: float, arrival_ns: int | None = None) -> None:
        base = self.pos - len(self.pending)          # stream offset of buf[0]
        buf = self.pending + data
        self.pos += len(data)
        self.pending = b""
        if self.problem:
            return
        if self.next is None:
            if len(buf) < 16:
                self.pending = buf
                return
            if buf[:8] != MAGIC:
                self.problem = "the stream does not begin with " + MAGIC.decode()
                return
            self.header_bytes = struct.unpack_from("<I", buf, 12)[0]
            self.next = (self.header_bytes + 3) & ~3
        while True:
            at = self.next - base
            if at + 12 > len(buf):
                if at < len(buf):
                    self.pending = buf[at:]
                break
            kind, size, _seq = struct.unpack_from("<III", buf, at)
            if size < 12 or size % 4:
                self.problem = f"the record at offset {self.next} has size {size}"
                break
            if kind == KIND_FRAME:
                self.waiting.append(self.next + size)
            self.records += 1
            self.next += size
        while self.waiting and self.waiting[0] <= self.pos:
            self.ends.append(self.waiting.pop(0))
            self.frames.append(t)
            if self.arrival_diagnostics is not None:
                if arrival_ns is not None:
                    self.arrival_diagnostics["arrivals"].append((len(self.ends), arrival_ns))
                self.arrival_diagnostics["complete_frames"] = len(self.ends)
                self.arrival_diagnostics["dropped_frames"] = (
                    len(self.ends) - len(self.arrival_diagnostics["arrivals"]))
        if self.arrival_diagnostics is not None:
            self.arrival_diagnostics["record_headers_seen"] = self.records
            self.arrival_diagnostics["framing_problem"] = self.problem


def arrival_stamp(diagnostics: dict) -> int | None:
    """Hold an absolute read-boundary stamp; diagnostic failure leaves transport running."""
    if diagnostics.get("clock_errors"):
        return None
    try:
        value = time.monotonic_ns()
        if type(value) is not int or value < 0:
            raise ValueError("invalid monotonic nanosecond sample")
        previous = diagnostics.get("last_read_ns")
        if previous is not None and value < previous:
            raise ValueError("monotonic nanosecond sample moved backwards")
    except Exception as error:
        diagnostics["clock_errors"] = diagnostics.get("clock_errors", 0) + 1
        diagnostics.setdefault("error", f"{type(error).__name__}: {error}")
        return None
    diagnostics["last_read_ns"] = value
    return value


class Spool:
    """The relay's copy on disk, how much of it is there and how much has been handed on:
    the reader appends and says so, the feeder waits for more and says what it handed on,
    and a held reader (relay's lead_frames) waits for that."""

    def __init__(self) -> None:
        self.size = 0
        self.done = False
        self.taken = 0             # bytes the feeder has handed on
        self.feeding = True        # the feeder is still handing bytes on
        self.cv = threading.Condition()

    def grew(self, n: int) -> None:
        with self.cv:
            self.size += n
            self.cv.notify_all()

    def close(self) -> None:
        with self.cv:
            self.done = True
            self.cv.notify_all()

    def wait_past(self, have: int) -> tuple[int, bool]:
        with self.cv:
            while self.size <= have and not self.done:
                self.cv.wait(1.0)
            return self.size, self.done

    def took(self, n: int) -> None:
        with self.cv:
            self.taken += n
            self.cv.notify_all()

    def feeder_ended(self) -> None:
        with self.cv:
            self.feeding = False
            self.cv.notify_all()

    def unfed(self, ends: list[int]) -> int:
        """How many of the whole frames whose end offsets are `ends` (in order) the feeder
        has not handed on in full."""
        with self.cv:
            return len(ends) - bisect.bisect_right(ends, self.taken)

    def hold(self, ends: list[int], lead: int, slice_s: float) -> bool:
        """Wait, at most slice_s, while `lead` or more of those frames are not handed on in
        full and the feeder still runs; whether the reader may read now."""
        def held() -> bool:
            return self.feeding and len(ends) - bisect.bisect_right(ends, self.taken) >= lead
        with self.cv:
            if held():
                self.cv.wait_for(lambda: not held(), timeout=slice_s)
            return not held()


def open_pipe(fifo: Path) -> int:
    """The read end of `fifo`, made fresh: O_NONBLOCK, so it opens with no writer yet and a
    writer's open never waits; a larger pipe buffer where the kernel allows it."""
    fifo.parent.mkdir(parents=True, exist_ok=True)
    if fifo.exists() or fifo.is_symlink():
        fifo.unlink()
    os.mkfifo(fifo, 0o600)
    fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        fcntl.fcntl(fd, F_SETPIPE_SZ, CHUNK)
    except OSError:
        pass
    return fd


def write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def pipe_size_for(sizes: list[int], waiting: bool) -> int:
    """The writer's pipe for a held relay, given the last frames' sizes, a power of two
    from PIPE_MIN to CHUNK. While the writer waits on the relay (`waiting`: the game is
    ahead of the picture), the largest that is at most half the median frame: the game
    then waits early in its write of the frame after the one the relay holds, and every
    frame in the pipe is one more frame the game is ahead of the picture. Otherwise the
    smallest that holds the largest frame: the recorder writes each frame at its Present
    in one write(2), which then does not wait on the relay, or on the player through it."""
    if waiting:
        half = sorted(sizes)[len(sizes) // 2] // 2 if sizes else 0
        want = PIPE_MIN
        while want * 2 <= min(half, CHUNK):
            want <<= 1
        return want
    want = PIPE_MIN
    while want < min(max(sizes, default=0), CHUNK):
        want <<= 1
    return want


def pipe_bytes_waiting(fd: int) -> int:
    """The bytes in the pipe `fd` (FIONREAD)."""
    buf = bytearray(4)
    fcntl.ioctl(fd, termios.FIONREAD, buf)
    return int.from_bytes(buf, "little")


def fit_pipe(fd: int, clock: FrameClock, held: dict, could_hold: bool) -> None:
    """At a whole frame: the size pipe_size_for gives the pipe `fd` over the frames `clock`
    has seen whole, for a writer taken as waiting or not. A pause that finds the writer's
    pipe full takes it as waiting at once (read_pipe). A whole frame since which no pause
    did is quiet, if a pause could have come since the last (`could_hold`: the copy had as
    many whole frames as the hold takes, the feeder ran and the writer was there), and the
    QUIET_FRAMES-th quiet frame in a row takes the writer as not waiting; a frame no pause
    could come before, such as the first, neither counts nor breaks the row. One quiet
    frame is not enough: a pause that ends before a writer that is ahead has had the CPU
    to fill its pipe finds it not full, as the pause that hands the first frame to a
    player not yet pacing does on a busy machine. A larger size is set now; a smaller one
    is left to resize_pipe after the reader's next read, when the pipe is emptiest (a pipe
    holding more than the new size refuses it: EBUSY, counted in resize_refused, and tried
    again after the next read)."""
    if held["waited_this_frame"]:
        held["quiet_frames"] = 0
    elif could_hold:
        held["quiet_frames"] += 1
        if held["quiet_frames"] >= QUIET_FRAMES:
            held["writer_waiting"] = False
    held["waited_this_frame"] = False
    ends = clock.ends[-(PIPE_FIT_FRAMES + 1):]
    held["want_bytes"] = pipe_size_for([b - a for a, b in zip(ends, ends[1:])] or ends[-1:],
                                       held["writer_waiting"])
    if held["pipe_bytes"] is None or held["want_bytes"] > held["pipe_bytes"]:
        resize_pipe(fd, held)


def resize_pipe(fd: int, held: dict) -> None:
    """Set the pipe `fd` to held["want_bytes"] when it is not that size, counting what
    the kernel refused."""
    if held["want_bytes"] is None or held["want_bytes"] == held["pipe_bytes"]:
        return
    try:
        held["pipe_bytes"] = fcntl.fcntl(fd, F_SETPIPE_SZ, held["want_bytes"])
        held["resizes"] += 1
        held["pipe_min"] = min(held["pipe_min"] or held["pipe_bytes"], held["pipe_bytes"])
        held["pipe_max"] = max(held["pipe_max"] or 0, held["pipe_bytes"])
    except OSError:
        held["resize_refused"] += 1


def read_pipe(fd: int, copy_fd: int, spool: Spool, clock: FrameClock, writer_alive,
              poll_ms: int = 100, lead_frames: int | None = None) -> dict:
    """Take everything written into the pipe `fd` and append it to the file `copy_fd`,
    until the writer closes the pipe, or until writer_alive() is false and no writer ever
    opened it. Waits on nothing but the pipe and the disk, unless `lead_frames`: then,
    while that many whole frames or more are in the copy and not handed on in full by a
    feeder that still runs, and writer_alive() holds, it reads nothing (the writer's pipe
    fills and its write waits): lead_frames while the writer is taken as waiting on it,
    one more while it is not (fit_pipe); and it sizes the pipe to the frames (fit_pipe).
    st["held"], there only then, counts both."""
    st: dict = {"bytes": 0, "chunks": 0, "first_byte_s": None, "last_byte_s": None,
                "max_gap_s": 0.0, "end": None}
    t0 = time.monotonic()
    pl = select.poll()
    pl.register(fd, select.POLLIN)
    last = None
    held = None
    if lead_frames:
        # The smallest pipe to begin with, the writer taken as waiting until whole frames
        # come without a pause finding it so, where one could have come (fit_pipe): the
        # first frames are often a few kB each, and the first comes before any could.
        held = st["held"] = {"lead_frames": lead_frames, "pauses": 0, "paused_s": 0.0,
                             "writer_waited": 0, "writer_waiting": True,
                             "waited_this_frame": False, "quiet_frames": 0,
                             "max_unfed_frames": 0,
                             "pipe_bytes": None, "want_bytes": PIPE_MIN, "pipe_min": None,
                             "pipe_max": None, "resizes": 0, "resize_refused": 0}
        resize_pipe(fd, held)
    whole = 0
    while True:
        # The tight hold, lead_frames, while the writer waits on the relay (the game ahead
        # of the picture); one frame more while it does not, so that a game behind it is
        # not stopped at every frame the player is still replaying.
        # Nothing is held for a writer that is gone: it writes nothing more, and the run's
        # end (its player's grace, then cancel) comes only once this returns.
        lead = (lead_frames if held["writer_waiting"] else lead_frames + 1) if held else 0
        if held and spool.feeding and spool.unfed(clock.ends) >= lead and writer_alive():
            t = time.monotonic()
            held["pauses"] += 1
            while not spool.hold(clock.ends, lead, 0.1) and writer_alive():
                pass
            held["paused_s"] += time.monotonic() - t
            # A full pipe now: the writer waited on this pause (the game is ahead). One not
            # full says less (the writer may not have had the CPU yet): fit_pipe weighs it.
            try:
                waited = pipe_bytes_waiting(fd) >= held["pipe_bytes"] - 4096
            except (OSError, TypeError):
                waited = False
            if waited:
                held["writer_waiting"] = held["waited_this_frame"] = True
            held["writer_waited"] += waited
        events = pl.poll(poll_ms)
        if not events:
            if not writer_alive():
                # The process that should write is gone. A writer that opened the pipe and
                # closed it shows as POLLHUP (Linux holds POLLHUP back on an O_NONBLOCK
                # reader only until a writer has been seen), so none ever did.
                st["end"] = "the game ended without opening the pipe"
                break
            continue
        try:
            data = os.read(fd, CHUNK)
            arrival_ns = None
            if data and clock.arrival_diagnostics is not None:
                arrival_ns = arrival_stamp(clock.arrival_diagnostics)
                clock.arrival_diagnostics["bytes_read"] += len(data)
        except BlockingIOError:
            continue
        if held and held["want_bytes"] < (held["pipe_bytes"] or 0):
            resize_pipe(fd, held)       # just emptied: the likeliest moment a shrink takes
        now = time.monotonic() - t0
        if not data:
            if events[0][1] & select.POLLHUP:
                st["end"] = "the writer closed the pipe"
                break
            continue
        write_all(copy_fd, data)
        spool.grew(len(data))
        if clock.arrival_diagnostics is None:
            clock.feed(data, now)
        else:
            clock.feed(data, now, arrival_ns)
        st["bytes"] += len(data)
        st["chunks"] += 1
        if st["first_byte_s"] is None:
            st["first_byte_s"] = round(now, 6)
        if last is not None:
            st["max_gap_s"] = max(st["max_gap_s"], now - last)
        last = now
        st["last_byte_s"] = round(now, 6)
        if held and len(clock.ends) > whole:
            held["max_unfed_frames"] = max(held["max_unfed_frames"], spool.unfed(clock.ends))
            # Since the last whole frame the copy had `whole` of them, so a pause could
            # have come only if that is `lead` or more, with the feeder still running and
            # the writer still there (`lead` held since then: only a pause that finds the
            # writer waiting changes it, and then fit_pipe counts no quiet frame).
            fit_pipe(fd, clock, held,
                     could_hold=whole >= lead and spool.feeding and writer_alive())
            whole = len(clock.ends)
    st["closed_monotonic"] = time.monotonic()
    st["max_gap_s"] = round(st["max_gap_s"], 6)
    st["t0_monotonic"] = t0
    if held:
        held["paused_s"] = round(held["paused_s"], 6)
    return st


def feed(copy: Path, spool: Spool, sink: int, st: dict, piece: int = CHUNK) -> None:
    """Hand the file `copy` to the file descriptor `sink` as it grows, `piece` bytes at
    most to a write, saying after each what it has handed on (Spool.took); then close
    `sink`. A sink that stops reading holds up this thread only, and a held reader until
    this thread ends or the writer is gone. Fills `st`, with the bytes it took while the
    pipe was still open (the spool not yet closed) and when it first and last wrote, on
    CLOCK_MONOTONIC."""
    st.update(bytes=0, bytes_taken_while_open=0, first_write_monotonic=None,
              last_write_monotonic=None, blocked_s=0.0, blocked_max_s=0.0, error=None,
              closed=False)
    try:
        with open(copy, "rb", buffering=0) as src:
            while True:
                size, done = spool.wait_past(st["bytes"])
                while st["bytes"] < size:
                    data = src.read(min(piece, size - st["bytes"]))
                    if not data:
                        raise OSError(f"the copy ends at {st['bytes']} bytes, before {size}")
                    t = time.monotonic()
                    write_all(sink, data)
                    t1 = time.monotonic()
                    st["blocked_s"] += t1 - t
                    st["blocked_max_s"] = max(st["blocked_max_s"], t1 - t)
                    st["bytes"] += len(data)
                    spool.took(len(data))
                    if not done:
                        st["bytes_taken_while_open"] += len(data)
                    if st["first_write_monotonic"] is None:
                        st["first_write_monotonic"] = t
                    st["last_write_monotonic"] = t1
                if done and st["bytes"] >= size:
                    break
    except OSError as e:           # BrokenPipeError: the player went away
        st["error"] = f"{type(e).__name__}: {e}"
    finally:
        spool.feeder_ended()       # a held reader takes everything again
        try:
            os.close(sink)
            st["closed"] = True
        except OSError:
            pass
        st["blocked_s"] = round(st["blocked_s"], 6)
        st["blocked_max_s"] = round(st["blocked_max_s"], 6)


def relay(fd: int, copy: Path, sink: int, writer_alive, grace: float = FEED_GRACE,
          lead_frames: int | None = None, arrival_diagnostics: dict | None = None) -> dict:
    """The transport of one live run: the pipe's read end `fd` drained into `copy` by this
    thread (read_pipe), and `copy` handed to `sink` as it grows by a feeder thread (feed),
    which is given `grace` seconds after the pipe closes to finish. `sink` is closed when
    the feeder ends, which is the player's end of stream.

    With `lead_frames` the relay is held (tools/s5/play_live.py's paced sessions): the
    reader takes nothing more from the pipe while that many whole frames are in the copy
    and not handed on in full (one more once the writer has not been found waiting on it
    for QUIET_FRAMES whole frames), so
    a writer ahead of the sink's reader waits in its write instead of the copy running
    ahead of the picture; the feeder hands on LEAD_PIECE at a time, and the pipe is sized
    to the frames (fit_pipe). Every byte still reaches the copy and the sink, and once the
    feeder ends (the sink's reader gone) or writer_alive() fails nothing is held. Without
    it (present_live's runs) the reader never waits for the sink."""
    if arrival_diagnostics is not None:
        return relay_arrivals(fd, copy, sink, writer_alive, grace, lead_frames,
                              arrival_diagnostics)
    copy_fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    spool, clock, fed = Spool(), FrameClock(), {}
    feeder = threading.Thread(target=feed, args=(copy, spool, sink, fed),
                              kwargs={"piece": LEAD_PIECE if lead_frames else CHUNK}, daemon=True)
    feeder.start()
    try:
        st = read_pipe(fd, copy_fd, spool, clock, writer_alive, lead_frames=lead_frames)
    finally:
        spool.close()
        os.close(copy_fd)
    feeder.join(timeout=grace)
    st.update(frames_arrived=len(clock.frames), records=clock.records,
              framing_problem=clock.problem, frame_arrival_s=[round(t, 6) for t in clock.frames],
              feed=fed, feed_ended=not feeder.is_alive(), feed_thread=feeder)
    return st


def relay_arrivals(fd: int, copy: Path, sink: int, writer_alive, grace: float,
                   lead_frames: int | None, diagnostics: dict) -> dict:
    """The optional receipt transport retains its prefix through failed reads and cleanup."""
    diagnostics.update(requested=True, arrivals=[], complete_frames=0, dropped_frames=0,
                       record_headers_seen=0, bytes_read=0, clock_errors=0, io_errors=0,
                       transport_end=None, framing_problem=None, feed_ended=False,
                       feed_error=None, copy_closed=False, relay_finished=False,
                       clock_info=None)
    try:
        info = time.get_clock_info("monotonic")
        diagnostics["clock_info"] = {key: getattr(info, key) for key in
                                     ("implementation", "monotonic", "adjustable", "resolution")}
        if (not info.monotonic or info.adjustable or info.resolution <= 0 or
                "CLOCK_MONOTONIC)" not in info.implementation):
            raise ValueError("monotonic clock is not CLOCK_MONOTONIC")
    except Exception as error:
        diagnostics.update(clock_errors=1, error=f"clock info {type(error).__name__}: {error}")
    spool, clock, fed = Spool(), FrameClock(diagnostics), {}
    copy_fd = feeder = None
    failure = failure_traceback = None
    st = {}
    try:
        copy_fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        feeder = threading.Thread(target=feed, args=(copy, spool, sink, fed),
                                  kwargs={"piece": LEAD_PIECE if lead_frames else CHUNK},
                                  daemon=True)
        feeder.start()
        st = read_pipe(fd, copy_fd, spool, clock, writer_alive, lead_frames=lead_frames)
        diagnostics["transport_end"] = st["end"]
    except BaseException as error:
        failure, failure_traceback = error, error.__traceback__
        diagnostics.setdefault("error", f"transport {type(error).__name__}: {error}")
    finally:
        spool.close()
        if copy_fd is not None:
            try:
                os.close(copy_fd)
                diagnostics["copy_closed"] = True
            except OSError as error:
                diagnostics["io_errors"] += 1
                diagnostics.setdefault("error", f"copy close {type(error).__name__}: {error}")
                if failure is None:
                    failure, failure_traceback = error, error.__traceback__
        if feeder is not None and feeder.ident is not None:
            try:
                feeder.join(timeout=grace)
                diagnostics["feed_ended"] = not feeder.is_alive()
            except BaseException as error:
                diagnostics.setdefault("error", f"feeder join {type(error).__name__}: {error}")
                if failure is None:
                    failure, failure_traceback = error, error.__traceback__
            diagnostics["feed_error"] = fed.get("error")
            if diagnostics["feed_ended"] and not fed.get("closed"):
                diagnostics["feed_error"] = diagnostics["feed_error"] or "feeder sink did not close"
        else:
            try:
                os.close(sink)
            except OSError as error:
                diagnostics["io_errors"] += 1
                diagnostics["feed_error"] = f"unstarted feeder sink close: {error}"
        diagnostics.update(complete_frames=len(clock.frames),
                           dropped_frames=len(clock.frames) - len(diagnostics["arrivals"]),
                           record_headers_seen=clock.records, framing_problem=clock.problem,
                           parser_partial=bool(clock.pending or clock.waiting or
                                               clock.next != clock.pos),
                           relay_finished=True)
    if failure is not None:
        raise failure.with_traceback(failure_traceback)
    st.update(frames_arrived=len(clock.frames), records=clock.records,
              framing_problem=clock.problem, frame_arrival_s=[round(t, 6) for t in clock.frames],
              feed=fed, feed_ended=diagnostics["feed_ended"], feed_thread=feeder,
              arrival_diagnostics=diagnostics)
    return st


# ---------------------------------------------------------------- the game

def live_command(build: str, p: dict, profile_env: dict, meta: dict) -> tuple[list[str], str]:
    """present_run's command, with the two wall-clock bounds a live run raises: the probe's
    alarm off, and loop_run's process bound RUN_TIMEOUT. Refuses a command that does not
    carry exactly one of each, so a change of present_run's shape is seen, not guessed at."""
    cmd, spec = PR.command(build, p, profile_env, meta)
    probe = [i for i, a in enumerate(cmd) if a == "--probe-arg=--timeout"]
    bound = [i for i, a in enumerate(cmd) if a == "--timeout"]
    if len(probe) != 1 or len(bound) != 1 or not cmd[probe[0] + 1].startswith("--probe-arg="):
        raise SystemExit("present_run.command() no longer carries one probe --timeout and one "
                         "loop_run --timeout; present_live cannot raise them")
    cmd = list(cmd)
    cmd[probe[0] + 1] = f"--probe-arg={PROBE_TIMEOUT}"
    cmd[bound[0] + 1] = str(RUN_TIMEOUT)
    return cmd, spec


def is_gate_pass(argv: list[str]) -> bool:
    """A Python process running a gate pass's script. A shell whose command text only
    names the script (a waiter, a monitor, the wrapper that starts it) is not one."""
    return bool(argv) and Path(argv[0]).name.startswith("python") and any(
        a.endswith(GATE_SCRIPTS) for a in argv[1:])


def is_presentation_run(argv: list[str]) -> bool:
    """A Python process running a presentation run of the game: present_run.py or
    present_live.py (not --check, which runs nothing), the audio-scenes tool's sessions
    (`run` or `all`, not `receipt`, which runs no game: the tool builds present-as-* images
    and runs the game and the player, and between its loop_run children only the tool itself
    shows), or loop_run.py on a present-* build. One runs at a time on this machine."""
    if not argv or not Path(argv[0]).name.startswith("python"):
        return False
    args = argv[1:]
    if any(a.endswith(("tools/s5/present_run.py", "tools/s5/present_live.py")) for a in args):
        return "--check" not in args
    if any(a.endswith(AUDIO_TOOL) for a in args):
        return any(a in ("run", "all") for a in args)
    return (any(a.endswith("tools/s4/loop_run.py") for a in args)
            and any(b.startswith("present-") for a, b in zip(args, args[1:]) if a == "--name"))


def processes(match) -> list[str]:
    """Processes on this machine, in any tree, whose argv `match` accepts, but this one
    and its parents."""
    mine, pid = set(), os.getpid()
    while pid > 1:
        mine.add(str(pid))
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or pid in mine:
            continue
        try:
            argv = [a.decode(errors="replace")
                    for a in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if a]
        except OSError:
            continue
        if match(argv):
            out.append(f"{pid}: {' '.join(argv)[:160]}")
    return out


def gate_passes() -> list[str]:
    """Gate passes running on this machine, in any tree."""
    return processes(is_gate_pass)


def mem_available_gb() -> float:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / (1 << 20)
    return 0.0


# ---------------------------------------------------------------- the player

def build_player(bin_dir: Path, log: Path) -> dict:
    """host/player/build.sh into bin_dir, against this tree's host/sa2_stream.h."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OUT=str(bin_dir))
    env.pop("SA2_STREAM_H_DIR", None)
    t = time.time()
    r = subprocess.run(["bash", str(PLAYER_DIR / "build.sh")], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    log.write_text(f"$ OUT={rel(bin_dir)} bash host/player/build.sh\n{r.stdout}\n{r.stderr}")
    return {"command": f"OUT={rel(bin_dir)} bash host/player/build.sh", "rc": r.returncode,
            "seconds": round(time.time() - t, 2), "log": rel(log),
            "ok": r.returncode == 0 and "BUILD_OK" in r.stdout}


def player_inputs(bin_dir: Path) -> dict:
    binary = bin_dir / "sa2_player"
    return {"binary": rel(binary), "binary_sha256": sha256(binary) if binary.is_file() else None,
            "sources": {rel(f): sha256(f) for f in sorted(PLAYER_DIR.rglob("*"))
                        if f.is_file() and "__pycache__" not in f.parts},
            "stream_h_sha256": sha256(ROOT / PR.STREAM_H)}


def snap_frames(file_replay: Path, extra: int | None) -> list[int]:
    """The frames the file replay kept as PNG at stage 3, the first thirty (the named frame
    is among them unless the window took focus late), and the file replay's first frame
    that is not black."""
    ref = sorted(int(m.group(1)) for f in (file_replay / "stage3").glob("frame-*.png")
                 if (m := re.fullmatch(r"frame-(\d+)\.png", f.name)))
    return sorted(set(ref) | set(range(1, 31)) | ({extra} if extra else set()))


def player_command(pp: dict, snaps: list[int]) -> list[str]:
    """The player on its stdin, run from ROOT with paths relative to it."""
    return ["bash", rel(PLAYER_DIR / "run_player.sh"), "-", "--stage", "3",
            "--frames-out", rel(pp["y4m"]), "--snap", ",".join(map(str, snaps)),
            "--snap-dir", rel(pp["snaps"]), "--report", rel(pp["report"])]


def player_env(out: Path, bin_dir: Path, dxvk_log: Path) -> dict:
    """The desktop session's environment, which the window needs, with player_env.sh's
    outputs under this run: DXVK's log in `dxvk_log`, the player from bin_dir.
    SDL_VIDEO_DRIVER is left as it is (unset: SDL3 chooses), as the file replay left it."""
    env = dict(os.environ, SA2_PLAY_OUT=rel(out), OUT=rel(bin_dir),
               SA2_DXVK_LOG_PATH=rel(dxvk_log), DXVK_LOG_LEVEL="info")
    env.pop("DXVK_LOG_PATH", None)
    return env


# ---------------------------------------------------------------- the frames

def black_hash(w: int, h: int) -> str:
    """The player's frame_hash of an all-black w x h frame: FNV-1a over w*h zero words."""
    return f"0x{FNV_BASIS * pow(FNV_PRIME, w * h, 1 << 64) % (1 << 64):016x}"


# A frame's picture and its time: the read-back hash, and the recorded virtual time, guest
# frame and poll count. Its seq is where its Present sits in the stream, which moves by a
# constant when a recorder writes records before the first frame (the recorder fold's
# Direct3DCreate9, seq 0), so seq is held to one offset across all frames instead.
PICTURE_COLS = ("frame", "virtual_us", "guest_frames", "polls", "bgrx_hash")


def read_times(path: Path) -> list[dict]:
    rows = []
    lines = path.read_text().splitlines()
    head = lines[0].split("\t")
    for line in lines[1:]:
        if line.strip():
            rows.append(dict(zip(head, line.split("\t"))))
    return rows


def compare_frames(mine: list[dict], theirs: list[dict]) -> dict:
    """Frame for frame over PICTURE_COLS, and every frame's seq one offset from the other
    play's; wall_us is each play's own clock and is left out."""
    differing = [r.get("frame") for r, s in zip(mine, theirs)
                 if any(r.get(k) != s.get(k) for k in PICTURE_COLS)]
    try:
        offsets = sorted({int(r["seq"]) - int(s["seq"]) for r, s in zip(mine, theirs)})
    except (KeyError, ValueError):
        offsets = []
    return {"live": len(mine), "file": len(theirs), "compared": min(len(mine), len(theirs)),
            "differing": len(differing), "first_differing": differing[:10],
            "seq_offsets": offsets[:5],
            "ok": bool(mine) and len(mine) == len(theirs) and not differing and len(offsets) == 1}


def _same_from(a: Path, b: Path, skip_a: int, skip_b: int) -> dict:
    ha, hb = hashlib.sha256(), hashlib.sha256()
    first = None
    off = 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        fa.seek(skip_a)
        fb.seek(skip_b)
        while True:
            x, y = fa.read(1 << 22), fb.read(1 << 22)
            ha.update(x)
            hb.update(y)
            if first is None and x != y:
                n = min(len(x), len(y))
                first = off + next((i for i in range(n) if x[i] != y[i]), n)
            if not x and not y:
                break
            off += max(len(x), len(y))
    return {"sha256": [ha.hexdigest(), hb.hexdigest()], "first_differing_byte": first,
            "bytes": [a.stat().st_size - skip_a, b.stat().st_size - skip_b]}


def same_bytes(a: Path, b: Path) -> dict:
    out = {"a": rel(a), "b": rel(b)}
    if not a.is_file() or not b.is_file():
        return {**out, "ok": False, "why": "missing"}
    out.update(_same_from(a, b, 0, 0))
    out["ok"] = out["first_differing_byte"] is None and out["bytes"][0] == out["bytes"][1]
    return out


def same_y4m_frames(a: Path, b: Path) -> dict:
    """Two YUV4MPEG2 files' frames, byte for byte, past each file's own stream header
    line (the player fold added XCOLORRANGE=LIMITED to it); the header lines are kept."""
    out = {"a": rel(a), "b": rel(b)}
    if not a.is_file() or not b.is_file():
        return {**out, "ok": False, "why": "missing"}
    heads = []
    for f in (a, b):
        with open(f, "rb") as h:
            heads.append(h.readline(256))
    out["headers"] = [x.decode(errors="replace").rstrip("\n") for x in heads]
    if not all(x.startswith(b"YUV4MPEG2 ") and x.endswith(b"\n") for x in heads):
        return {**out, "ok": False, "why": "not a YUV4MPEG2 stream header"}
    out.update(_same_from(a, b, len(heads[0]), len(heads[1])))
    geometry = [x.split()[1:3] for x in heads]       # W and H
    out["ok"] = (out["first_differing_byte"] is None and out["bytes"][0] == out["bytes"][1]
                 and geometry[0] == geometry[1])
    return out


def first_game_frame(rows: list[dict], w: int, h: int) -> dict | None:
    black = black_hash(w, h)
    for r in rows:
        if r.get("bgrx_hash") != black:
            return {"index": int(r["frame"]), "seq": int(r["seq"]),
                    "virtual_us": int(r["virtual_us"]), "bgrx_hash": r["bgrx_hash"],
                    "black_hash": black}
    return None


def pngs_of(snaps: Path, file_replay: Path) -> dict[int, dict]:
    """Every PNG the live run kept, by frame, with the file replay's PNG of the same frame."""
    out = {}
    for f in sorted(snaps.glob("frame-*.png")):
        m = re.fullmatch(r"frame-(\d+)\.png", f.name)
        if not m:
            continue
        ref = file_replay / "stage3" / f.name
        out[int(m.group(1))] = {"png": rel(f), "png_sha256": sha256(f),
                                "file_replay_png": rel(ref) if ref.is_file() else None,
                                "file_replay_png_sha256": sha256(ref) if ref.is_file() else None}
    return out


def with_png(frame: dict | None, pngs: dict[int, dict]) -> dict | None:
    """`frame` with its PNG and the file replay's, and whether the two are byte-equal."""
    if frame is None:
        return None
    got = pngs.get(frame["index"]) or {"png": None, "png_sha256": None, "file_replay_png": None,
                                       "file_replay_png_sha256": None}
    return {**frame, **got, "png_equal": bool(got["png_sha256"] and got["png_sha256"]
                                              == got["file_replay_png_sha256"])}


def first_non_black(rows: list[dict], w: int, h: int, pngs: dict[int, dict]) -> dict:
    """The check on the first non-black frame: there is one, and its PNG is the file
    replay's."""
    frame = with_png(first_game_frame(rows, w, h), pngs)
    return {"rule": FIRST_NON_BLACK_RULE, "frame": frame,
            "ok": bool(frame and frame["png_equal"])}


# ---------------------------------------------------------------- the stream

def stream_header(path: Path) -> tuple[int, dict, dict]:
    """(bytes before the first record, the header JSON, its meta) of a stream file. The
    writer embeds the meta as a JSON string (host/sa2_stream.h)."""
    with open(path, "rb") as f:
        head = f.read(16)
        if len(head) < 16 or head[:8] != MAGIC:
            raise ValueError(f"{path} is not a stream")
        hb = struct.unpack_from("<I", head, 12)[0]
        text = f.read(hb - 16).decode(errors="replace").rstrip(" \0\n")
    header = json.loads(text)
    meta = header.get("meta")
    if isinstance(meta, str):
        meta = json.loads(meta)
    return (hb + 3) & ~3, header, meta if isinstance(meta, dict) else {}


def compare_records(mine: Path, theirs: Path) -> dict:
    """The two streams' records, byte for byte, past each one's own header; which header
    keys differ; and whether their metas name the same recorder and header, without which
    the records have no reason to agree."""
    out: dict = {"live": rel(mine), "file": rel(theirs)}
    try:
        (ha, ja, ma), (hb, jb, mb) = stream_header(mine), stream_header(theirs)
    except (OSError, ValueError) as e:
        return {**out, "ok": False, "why": str(e)}
    out["header_bytes"] = [ha, hb]
    out["header_keys_differing"] = sorted(k for k in set(ja) | set(jb) if ja.get(k) != jb.get(k))
    out["meta_keys_differing"] = sorted(k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k))
    out["same_recorder"] = bool(ma.get("recorder_sha256") and ma.get("stream_h_sha256")
                                and ma.get("recorder_sha256") == mb.get("recorder_sha256")
                                and ma.get("stream_h_sha256") == mb.get("stream_h_sha256"))
    ra, rb = hashlib.sha256(), hashlib.sha256()
    first = None
    n = 0
    with open(mine, "rb") as fa, open(theirs, "rb") as fb:
        fa.seek(ha)
        fb.seek(hb)
        while True:
            x, y = fa.read(1 << 22), fb.read(1 << 22)
            ra.update(x)
            rb.update(y)
            if first is None and x != y:
                k = min(len(x), len(y))
                first = n + next((i for i in range(k) if x[i] != y[i]), k)
            if not x and not y:
                break
            n += max(len(x), len(y))
    out["records_bytes"] = [mine.stat().st_size - ha, theirs.stat().st_size - hb]
    out["records_sha256"] = [ra.hexdigest(), rb.hexdigest()]
    out["first_differing_record_byte"] = first
    out["ok"] = (first is None and out["records_bytes"][0] == out["records_bytes"][1]
                 and out["header_keys_differing"] in ([], ["meta"]) and out["same_recorder"])
    return out


def sidecar_counts(text: str | None) -> dict | None:
    """The recorder's own count of what it wrote, from its sidecar once the stream closed."""
    m = SIDECAR_CLOSED.fullmatch((text or "").strip())
    return ({"records": int(m.group(1)), "bytes": int(m.group(2)), "frames": int(m.group(3))}
            if m else None)


def check_delivery(relay_st: dict, copied: int | None, copy_sha: str | None,
                   sidecar: str | None) -> dict:
    """What the recorder says it wrote (its sidecar), what the relay read, what the copy
    holds and what the player was given: the same bytes, records and frames."""
    fed = relay_st.get("feed") or {}
    wrote = sidecar_counts(sidecar)
    out = {"recorder_wrote": wrote, "recorder_sidecar": (sidecar or "").strip() or None,
           "relay_read": {"bytes": relay_st.get("bytes"), "records": relay_st.get("records"),
                          "frames": relay_st.get("frames_arrived")},
           "copy_bytes": copied, "copy_sha256": copy_sha, "player_given": fed.get("bytes"),
           "player_given_while_the_pipe_was_open": fed.get("bytes_taken_while_open"),
           "feed_error": fed.get("error"), "end": relay_st.get("end"),
           "framing_problem": relay_st.get("framing_problem")}
    out["ok"] = bool(wrote and copied
                     and wrote["bytes"] == relay_st.get("bytes") == copied == fed.get("bytes")
                     and wrote["records"] == relay_st.get("records")
                     and wrote["frames"] == relay_st.get("frames_arrived")
                     and not fed.get("error") and not relay_st.get("framing_problem")
                     and relay_st.get("end") == "the writer closed the pipe")
    return out


# ---------------------------------------------------------------- liveness

def game_clock(words: Path) -> dict:
    """When the game ran, by its own word log (harness/replay/wrap_tick_probe.c): the ref
    clock is CLOCK_MONOTONIC in nanoseconds, read at GameLoop's first entry and in the
    log's end record, which the probe writes at the process's exit (words_end, atexit);
    each tick's entry is stamped on wl_clock (t_enter, tools/s4/wordlog.py stats), which the
    two readings of both clocks put on CLOCK_MONOTONIC."""
    if not words.is_file():
        return {"from": rel(words), "why": "no word log"}
    try:
        i = WL.info(words)
        rows = WL.stats(words)
    except SystemExit as e:           # WL.WordLogError is a SystemExit
        return {"from": rel(words), "why": str(e)}
    hz = i.get("ref_hz") or 0
    ok = bool(i.get("whole") and hz and i.get("ref_end", 0) > i.get("ref_init", 0)
              and i.get("clk_end", 0) > i.get("clk_init", 0))
    rate = (i["ref_end"] - i["ref_init"]) / (i["clk_end"] - i["clk_init"]) if ok else 0
    ticks = [(i["ref_init"] + (r["t_enter"] - i["clk_init"]) * rate) / hz
             for r in rows] if ok else []
    return {"from": rel(words), "whole": i.get("whole"), "end_reason": i.get("end_reason"),
            "last_tick": i.get("last_tick"), "ref_hz": hz,
            "first_entry_monotonic_s": i["ref_init"] / hz if ok else None,
            "exit_monotonic_s": i["ref_end"] / hz if ok else None,
            "tick_entry_monotonic_s": ticks}


def read_backs(relay_st: dict, rows: list[dict], player_t0: float | None) -> list[float]:
    """Each frame's read-back on the relay's clock (seconds after its t0). The player's
    wall_us counts from its own start: with that start's CLOCK_MONOTONIC time (the report's
    t_start_monotonic_s; the relay's clock is CLOCK_MONOTONIC too) the time is exact;
    without it the player is taken to start when it was spawned, which it follows by its
    start-up time, so each read-back is then early by that much."""
    t0 = relay_st.get("t0_monotonic")
    start = player_t0 if player_t0 is not None else relay_st.get("player_spawned_monotonic")
    if t0 is None or start is None:
        return []
    return [start + int(r["wall_us"]) / 1e6 - t0 for r in rows if r.get("wall_us")]


def frame_lags(relay_st: dict, rows: list[dict], player_t0: float | None) -> list[float]:
    """For each frame, how long after its last byte reached the relay the player read it
    back, just before its Present."""
    arr = relay_st.get("frame_arrival_s") or []
    return [rb - a for rb, a in zip(read_backs(relay_st, rows, player_t0), arr)]


def dist(v: list[float]) -> dict | None:
    if not v:
        return None
    s = sorted(v)
    return {"n": len(s), "min": round(s[0], 4), "median": round(s[len(s) // 2], 4),
            "p99": round(s[min(len(s) - 1, int(len(s) * 0.99))], 4), "max": round(s[-1], 4)}


def liveness(relay_st: dict, rows: list[dict], report: dict, game: dict) -> dict:
    """Whether every frame reached the window while the game ran, on one clock
    (CLOCK_MONOTONIC): each frame read back after its bytes reached the relay and before
    the game exited, by the player's exact start time and the game's own exit record;
    frame 1 read back before the game's last tick entered GameLoop; and the player reading
    a pipe. With the pace the game made frames at, which is not real time."""
    t_start = report.get("t_start_monotonic_s")
    exact = isinstance(t_start, (int, float)) and not isinstance(t_start, bool)
    player_t0 = t_start if exact else None
    arr = relay_st.get("frame_arrival_s") or []
    t0 = relay_st.get("t0_monotonic")
    rb = read_backs(relay_st, rows, player_t0)
    lags = frame_lags(relay_st, rows, player_t0)
    on_relay = (lambda x: round(x - t0, 6) if x is not None and t0 is not None else None)
    exit_s = on_relay(game.get("exit_monotonic_s"))
    ticks = [x - t0 for x in game.get("tick_entry_monotonic_s") or []] if t0 is not None else []
    frames = [int(r["frame"]) for r in rows]
    late = [f for f, x in zip(frames, rb) if exit_s is None or x >= exit_s]
    not_after_arrival = [f for f, lag in zip(frames, lags) if lag <= 0]
    vus = [int(r["virtual_us"]) for r in rows]
    pace = (round((vus[-1] - vus[0]) / 1e6 / (arr[len(vus) - 1] - arr[0]), 4)
            if len(vus) > 1 and len(arr) >= len(vus) and arr[len(vus) - 1] > arr[0] else None)
    out = {
        "clock": ("CLOCK_MONOTONIC, seconds after the relay's start: the relay's "
                  "time.monotonic(), the player's now_s() from its t_start_monotonic_s "
                  "(host/player/player.cpp), and the word log's ref clock "
                  "(harness/replay/wrap_tick_probe.c, words_ref), with each tick's wl_clock "
                  "stamp put on it by the log's own readings of both clocks"),
        "lag_exact": exact,
        "player_start": ("t_start_monotonic_s, from the player's report" if exact else
                         "NOT EXACT: the player's report has no t_start_monotonic_s, so its "
                         "spawn stands in and every read-back and lag is early by its "
                         "start-up time"),
        "game_clock": {k: v for k, v in game.items() if k != "tick_entry_monotonic_s"},
        "game_first_entry_s": on_relay(game.get("first_entry_monotonic_s")),
        "game_ticks": len(ticks),
        "game_first_tick_s": round(ticks[0], 6) if ticks else None,
        "game_last_tick_s": round(ticks[-1], 6) if ticks else None,
        "ticks_entered_after_frame_1_was_read_back": (sum(1 for x in ticks if x > rb[0])
                                                      if rb else None),
        "game_exit_s": exit_s,
        "pipe_closed_s": on_relay(relay_st.get("closed_monotonic")),
        "frames_arrived": len(arr), "frames_read_back": len(rb), "frames_in_times": len(rows),
        "first_arrival_s": arr[0] if arr else None,
        "first_read_back_s": round(rb[0], 6) if rb else None,
        "last_read_back_s": round(rb[-1], 6) if rb else None,
        "last_read_back_before_exit_s": (round(exit_s - max(rb), 6)
                                          if rb and exit_s is not None else None),
        "read_back_at_or_after_game_exit": {"count": len(late), "first": late[:10]},
        "read_back_not_after_arrival": {"count": len(not_after_arrival),
                                        "first": not_after_arrival[:10]},
        "read_back_before_the_next_frame_arrived": sum(
            1 for i in range(min(len(rb), len(arr)) - 1) if rb[i] < arr[i + 1]),
        "lag_s": dist(lags),
        "arrival_gap_s": dist([b - a for a, b in zip(arr, arr[1:])]),
        "pace_virtual_s_per_wall_s": pace,
        "pace_note": ("virtual seconds of the frames the game made per wall second: the boot "
                      "runs slower than real time, so 'live' means while the game ran, not "
                      "at the game's speed"),
        "live_input": report.get("live_input"),
        "player_idle": (report.get("window") or {}).get("idle"),
        "feed_blocked_s": (relay_st.get("feed") or {}).get("blocked_s"),
        "feed_blocked_max_s": (relay_st.get("feed") or {}).get("blocked_max_s"),
    }
    out["ok"] = bool(exact and exit_s is not None and arr
                     and len(rb) == len(arr) == len(rows)
                     and not late and not not_after_arrival
                     and ticks and rb[0] < ticks[-1]
                     and report.get("live_input") is True)
    return out


# ---------------------------------------------------------------- on screen

def window_flags(report: dict) -> dict[int, int]:
    """The window's SDL flags at each Present, by frame, from the player's runs of
    [first frame, last frame, flags] (report.window.flags_at_present)."""
    flags: dict[int, int] = {}
    for run in (report.get("window") or {}).get("flags_at_present") or []:
        first, last, fl = (int(x) for x in run)
        for f in range(first, last + 1):
            flags[f] = fl
    return flags


def named_frame(rows: list[dict], flags: dict[int, int], black: str) -> dict | None:
    """The checkpoint's frame (NAMED_FRAME_RULE)."""
    for r in rows:
        f = int(r["frame"])
        fl = flags.get(f)
        if (f >= 2 and r.get("bgrx_hash") != black and fl is not None
                and fl & SDL_WINDOW_INPUT_FOCUS and not fl & SDL_WINDOW_OCCLUDED):
            return {"index": f, "seq": int(r["seq"]), "virtual_us": int(r["virtual_us"]),
                    "bgrx_hash": r["bgrx_hash"], "window_flags": f"0x{fl:x}"}
    return None


def session_holds(s: dict) -> bool:
    """A local graphical session on seat0, active, not locked and not idle (GNOME blanks
    the screen when it goes idle)."""
    lg = s.get("logind") or {}
    return bool(s.get("live_desktop_session") and lg.get("LockedHint") == "no"
                and lg.get("IdleHint") == "no")


def on_screen(before: dict, after: dict, report: dict, rows: list[dict],
              pngs: dict[int, dict]) -> dict:
    """Whether the named frame was presented to a window the compositor had mapped, on a
    desktop someone could see: the session before and after, the SDL video driver, the
    window's state at every Present, every Present succeeding, and the named frame with a
    PNG equal to the file replay's."""
    host = report.get("host") or {}
    bb = (report.get("device") or {}).get("back_buffer") or [1280, 720]
    flags = window_flags(report)
    presents = report.get("presents")
    frame = with_png(named_frame(rows, flags, black_hash(bb[0], bb[1])), pngs)
    focus = sum(1 for v in flags.values() if v & SDL_WINDOW_INPUT_FOCUS)
    suspended = sum(1 for v in flags.values() if v & SDL_WINDOW_OCCLUDED)
    lg = [(s.get("logind") or {}) for s in (before, after)]
    out = {
        "basis": ("an active, local, unlocked, non-idle desktop session before and after the "
                  "run; the window holding the compositor's keyboard focus and not suspended "
                  "at the named frame's Present; every Present succeeding; no capture of the "
                  "screen"),
        "session": {"before": {"live_desktop_session": before.get("live_desktop_session"),
                               "locked": lg[0].get("LockedHint"), "idle": lg[0].get("IdleHint"),
                               "type": lg[0].get("Type")},
                    "after": {"live_desktop_session": after.get("live_desktop_session"),
                              "locked": lg[1].get("LockedHint"), "idle": lg[1].get("IdleHint"),
                              "type": lg[1].get("Type")},
                    "ok": session_holds(before) and session_holds(after)},
        "sdl_video_driver": host.get("sdl_video_driver"),
        "presents": presents, "present_failures": report.get("present_failures"),
        "window_sampled_at_every_present": bool(presents) and set(flags) == set(
            range(1, int(presents) + 1)),
        "presents_with": {"keyboard_focus": focus, "suspended": suspended,
                          "keyboard_focus_and_not_suspended": sum(
                              1 for v in flags.values()
                              if v & SDL_WINDOW_INPUT_FOCUS and not v & SDL_WINDOW_OCCLUDED)},
        "what_sdl_observes_on_wayland": WAYLAND_OBSERVES,
        "rule": NAMED_FRAME_RULE,
        "frame": frame,
    }
    out["ok"] = bool(out["session"]["ok"] and host.get("sdl_video_driver") in VIDEO_DRIVERS
                     and out["window_sampled_at_every_present"]
                     and report.get("present_failures") == 0
                     and frame and frame["png_equal"])
    return out


# ---------------------------------------------------------------- the host

def session() -> dict:
    """Read the current user's graphical session, rather than another user's seat."""
    env = {k: os.environ.get(k, "") for k in (
        "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_ID",
        "WAYLAND_DISPLAY", "DISPLAY", "SDL_VIDEO_DRIVER")}
    out: dict = {"env": env}
    uid = str(os.getuid())
    try:
        display = subprocess.run(
            ["loginctl", "show-user", uid, "-p", "Display"],
            capture_output=True, text=True, timeout=10, check=True)
        user = dict(x.split("=", 1) for x in display.stdout.splitlines() if "=" in x)
        sid = user.get("Display", "")
        if sid:
            show = subprocess.run(
                ["loginctl", "show-session", sid, "-p", "User", "-p", "Type",
                 "-p", "Active", "-p", "Remote", "-p", "State", "-p", "Seat",
                 "-p", "Class", "-p", "LockedHint", "-p", "IdleHint", "-p", "Display"],
                capture_output=True, text=True, timeout=10, check=True)
            kv = dict(x.split("=", 1) for x in show.stdout.splitlines() if "=" in x)
            out["logind"] = {"session": sid, **kv}
        else:
            out["logind"] = {"why": "the current user has no graphical Display session"}
    except (OSError, subprocess.SubprocessError) as e:
        out["logind"] = {"why": str(e)}
    try:
        out["mutter_check_alive_timeout_ms"] = subprocess.run(
            ["gsettings", "get", "org.gnome.mutter", "check-alive-timeout"],
            capture_output=True, text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        out["mutter_check_alive_timeout_ms"] = None
    lg = out.get("logind") or {}
    session_matches = not env["XDG_SESSION_ID"] or env["XDG_SESSION_ID"] == lg.get("session")
    display_matches = (bool(env["WAYLAND_DISPLAY"]) if lg.get("Type") == "wayland"
                       else bool(env["DISPLAY"] and env["DISPLAY"] == lg.get("Display")))
    out["live_desktop_session"] = bool(
        env["XDG_SESSION_TYPE"] in VIDEO_DRIVERS
        and lg.get("Type") == env["XDG_SESSION_TYPE"]
        and lg.get("User") == uid and lg.get("Class") == "user"
        and lg.get("Seat") == "seat0" and lg.get("Active") == "yes"
        and lg.get("Remote") == "no" and session_matches and display_matches)
    return out



def pinned(name: str) -> dict | None:
    """tools/pins.json's entry for `name`, or None when it pins none."""
    pins = json.loads((ROOT / "tools" / "pins.json").read_text())
    tools = pins.get("tools", pins)
    if isinstance(tools, dict) and isinstance(tools.get(name), dict):
        return {"source": "tools/pins.json", **tools[name]}
    return None


def library(path: str, pin_name: str) -> dict:
    real = (ROOT / path).resolve()
    got = sha256(real) if real.is_file() else None
    pin = pinned(pin_name)
    return {"path": path, "resolved": real.name, "sha256": got,
            "pin": pin or f"none: tools/pins.json pins no {pin_name!r} in this tree",
            "matches_pin": bool(pin and got and pin.get("library_sha256") == got)}


def dxvk_presenter(log_dir: Path) -> dict:
    """What DXVK's log says about the device and the swap chain it made, its present mode
    among them."""
    logs = sorted(log_dir.glob("*_d3d9.log"))
    out: dict = {"logs": [rel(p) for p in logs]}
    lines = []
    for p in logs:
        lines += [x.strip() for x in p.read_text(errors="replace").splitlines()
                  if "Present mode" in x or "Presenter:" in x or "Swap chain" in x
                  or "DXVK:" in x or "Driver" in x or "Found config" in x]
    out["lines"] = lines[:40]
    modes = [re.search(r"Present mode:\s*(\S+)", x) for x in lines]
    out["present_modes"] = sorted({m.group(1) for m in modes if m})
    return out


def git_status_paths() -> list[str] | None:
    """The paths where the working tree differs from its commit, untracked files included;
    read only (no index refresh is written)."""
    p = subprocess.run(["git", "--no-optional-locks", "status", "--porcelain=v1", "-z",
                        "--untracked-files=all"], cwd=ROOT, capture_output=True, text=True)
    if p.returncode:
        return None
    out, parts = [], p.stdout.split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) > 3:
            out.append(entry[3:])
            if entry[0] in "RC":          # a rename's or copy's source follows
                i += 1
        i += 1
    return sorted(out)


def code_state() -> dict:
    """The code the run ran, named so it can be found again: the commit, every file under
    CODE_DIRS by sha256, and where the tree differs from the commit. Until every code path
    is the commit's, only the hashes name the code."""
    files = {}
    for d in CODE_DIRS:
        for f in sorted((ROOT / d).rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                files[rel(f)] = sha256(f)
    differing = git_status_paths()
    code_differing = (None if differing is None
                      else [x for x in differing if x.startswith(CODE_PREFIXES)])
    return {"commit": PR.git_head(), "files_sha256": files,
            "tree_differs_from_commit": differing,
            "code_differing_from_commit": code_differing,
            "commit_names_the_code": code_differing == [],
            "how": ("files_sha256 is every file under " + " and ".join(f"{d}/" for d in CODE_DIRS)
                    + "; the commit names the code only when no path under "
                    + ", ".join(CODE_PREFIXES) + " differs from it")}


# ---------------------------------------------------------------- one live run

def live_paths(out: Path) -> dict:
    """present_run's paths for a run in `out`, its trace being the relay's copy and its
    partial path, where the recorder writes, the pipe."""
    fifo, copy = out / "boot.fifo", out / "boot.live.sa2s"
    p = PR.paths(out, copy)
    p["partial"], p["partial_status"] = fifo, Path(f"{fifo}.status")
    player = out / "player"
    return {"game": p, "fifo": fifo, "copy": copy, "relay": out / "relay.json",
            "player": {"dir": player, "y4m": player / "frames.y4m",
                       "times": player / "frames.y4m.times.tsv", "snaps": player / "snaps",
                       "report": player / "report.json", "log": player / "player.log"},
            "dxvk_log": out / "dxvk-log", "receipt": out / "present-live.json",
            "build_log": out / "player-build.log"}


def run_live(build: str, lp: dict, reg: dict, bin_dir: Path, snaps: list[int],
             inputs: dict) -> dict:
    """The game, the relay and the player, together, as present_run.run builds and runs
    the game; what each did, and when each ended, on CLOCK_MONOTONIC."""
    p, pp = lp["game"], lp["player"]
    profile = TR.native_device_profile(reg["entry"])
    profile_env = RR.profile_env(profile)
    for k in ("ticks", "words", "words_zst", "counts", "counts_status", "imports", "run",
              "matrix", "log", "partial_status"):
        if p.get(k) is not None:
            Path(p[k]).unlink(missing_ok=True)
    for f in (lp["copy"], Path(f"{lp['copy']}.status"), lp["relay"], lp["receipt"],
              pp["y4m"], pp["times"], pp["report"], pp["log"]):
        f.unlink(missing_ok=True)
    shutil.rmtree(pp["snaps"], ignore_errors=True)
    shutil.rmtree(lp["dxvk_log"], ignore_errors=True)
    for d in (p["out"], p["overlay"], pp["dir"], pp["snaps"], lp["dxvk_log"]):
        d.mkdir(parents=True, exist_ok=True)
    forgotten = PR.forget_hook_objects(build)
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{secrets.token_hex(4)}"
    meta = {"build": build, "replay": PR.REPLAY, "run": run_id, **inputs, "transport": "fifo",
            "live_runner_sha256": sha256(Path(__file__))}
    cmd, spec = live_command(build, p, profile_env, meta)
    saves = SS.reset_lifted(cmd, ROOT)
    SS.require_empty(saves)

    fd = open_pipe(lp["fifo"])
    pcmd = player_command(pp, snaps)
    plog = open(pp["log"], "w")
    glog = open(p["log"], "w")
    t_player = time.monotonic()
    player = subprocess.Popen(pcmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=plog,
                              stderr=subprocess.STDOUT,
                              env=player_env(p["out"], bin_dir, lp["dxvk_log"]))
    sink = os.dup(player.stdin.fileno())        # non-inheritable, so the game never holds it
    player.stdin.close()
    try:
        fcntl.fcntl(sink, F_SETPIPE_SZ, CHUNK)
    except OSError:
        pass
    glog.write(f"$ {' '.join(cmd)}\n")
    glog.flush()
    t_game = time.monotonic()
    game = subprocess.Popen(cmd, cwd=ROOT, stdout=glog, stderr=subprocess.STDOUT,
                            env=PR.clean_env(), start_new_session=True)
    exited = threading.Event()
    ends: dict = {}

    def wait_game() -> None:        # the game's own end, whatever the player is doing
        try:
            game.wait(timeout=RUN_TIMEOUT + 900)
        except subprocess.TimeoutExpired:
            os.killpg(game.pid, signal.SIGKILL)
            game.wait()
        ends["game"] = time.monotonic()
        exited.set()

    waiter = threading.Thread(target=wait_game, daemon=True)
    waiter.start()
    try:
        st = relay(fd, lp["copy"], sink, lambda: not exited.is_set())
    except BaseException:
        os.killpg(game.pid, signal.SIGKILL)
        player.kill()
        raise
    finally:
        os.close(fd)
    waiter.join()
    glog.close()
    try:
        player_rc = player.wait(timeout=FEED_GRACE)
    except subprocess.TimeoutExpired:
        player.kill()
        player_rc = player.wait()
    ends["player"] = time.monotonic()
    plog.close()
    run_dir = PR.C.GEN / "s4" / "run"
    for src, dst in ((run_dir / f"{build}.ticks.json", p["run"]),
                     (run_dir / f"{build}.matrix.json", p["matrix"])):
        if src.is_file():
            shutil.move(str(src), str(dst))
    # present_run's stream check reads the recorder's sidecar beside the stream it holds.
    if p["partial_status"].is_file():
        shutil.copyfile(p["partial_status"], f"{lp['copy']}.status")
    st.pop("feed_thread")
    st.update(player_spawned_monotonic=t_player, game_spawned_monotonic=t_game,
              game_exited_monotonic=ends["game"], player_exited_monotonic=ends["player"],
              player_started_before_relay_s=round(st["t0_monotonic"] - t_player, 6))
    lp["relay"].write_text(json.dumps(st, indent=1) + "\n")
    return {"command": cmd, "spec": spec, "saves": saves, "rc": game.returncode,
            "seconds": round(ends["game"] - t_game, 2), "inputs": inputs, "meta": meta,
            "hook_objects_deleted": forgotten, "player_command": pcmd,
            "player_rc": player_rc, "player_seconds": round(ends["player"] - t_player, 2),
            "player_after_game_s": round(ends["player"] - ends["game"], 2),
            "pipe_open_after_spawn_s": round(st["closed_monotonic"] - t_game, 2),
            "profile": profile if isinstance(profile, str) else
            {"path": profile.get("path"), "sha256": profile.get("sha256")}}


# ---------------------------------------------------------------- checks

def check_player(report: dict, copy_sha: str | None, presents: int | None) -> dict:
    """The player's own verdict, and the counts it rests on."""
    calls = report.get("calls") or {}
    frames_out = report.get("frames_out") or {}
    out = {"pass": report.get("pass"), "exit_status": report.get("exit_status"),
           "failures": (report.get("failures") or [])[:10], "presents": report.get("presents"),
           "stream_presents": presents, "frames_written": frames_out.get("frames"),
           "errors": report.get("errors"), "not_handled": calls.get("not_handled"),
           "present_failures": report.get("present_failures"),
           "readback_failures": report.get("readback_failures"),
           "calls": calls, "stop": report.get("stop"), "records": report.get("records"),
           "stream_sha256": report.get("stream_sha256"), "copy_sha256": copy_sha,
           "allowed_host_failures": (report.get("allowed_host_failures") or {}).get("rules"),
           "events": report.get("events"), "host": report.get("host"),
           "device": report.get("device"), "dxvk": report.get("dxvk")}
    out["ok"] = bool(report.get("complete") and report.get("pass") is True
                     and report.get("exit_status") == 0 and presents
                     and report.get("presents") == presents
                     and frames_out.get("frames") == presents and report.get("errors") == 0
                     and calls.get("not_handled") == 0 and report.get("stop") == "end of stream"
                     and report.get("present_failures") == 0
                     and report.get("readback_failures") == 0
                     and (report.get("records") or {}).get("seq_gaps") == 0
                     and copy_sha and report.get("stream_sha256") == copy_sha)
    return out


def timing_of(frame: dict | None, relay_st: dict, rows: list[dict], report: dict,
              live: dict) -> None:
    """A named frame's own times, on the relay's clock: when its bytes arrived, when it was
    read back, how long before the game's exit, and how long it was the last frame
    presented; each marked exact or not."""
    if not frame:
        return
    t_start = report.get("t_start_monotonic_s")
    exact = live.get("lag_exact") is True
    i = frame["index"] - 1
    arr = relay_st.get("frame_arrival_s") or []
    rb = read_backs(relay_st, rows, t_start if exact else None)
    frame["times_exact"] = exact
    if i < len(arr):
        frame["arrived_at_relay_s"] = arr[i]
    if i < len(rb):
        frame["read_back_s"] = round(rb[i], 6)
        if i < len(arr):
            frame["read_back_after_arrival_s"] = round(rb[i] - arr[i], 6)
        if live.get("game_exit_s") is not None:
            frame["read_back_before_game_exit_s"] = round(live["game_exit_s"] - rb[i], 6)
    if i + 1 < len(rows):
        # Read back just before its Present; the next frame's read-back comes just before
        # the Present that replaced it.
        frame["presented_for_s"] = (int(rows[i + 1]["wall_us"]) - int(rows[i]["wall_us"])) / 1e6


def hold(lp: dict, reg: dict, run: dict | None, file_stream: Path, file_replays: list[Path],
         sessions: tuple[dict, dict]) -> dict:
    """Every after-the-run check over a live run's outputs. The live frames are held to
    each of `file_replays`; the first is also the reference for the PNGs."""
    file_replay = file_replays[0]
    p, pp = lp["game"], lp["player"]
    profile = TR.native_device_profile(reg["entry"])
    out: dict = {"run": PR.checks(p, reg, trace=lp["copy"], meta_want=(run or {}).get("meta"),
                                  profile=profile, profile_env=RR.profile_env(profile),
                                  saves=(run or {}).get("saves"))}
    ready = PR.read_ready(file_stream.with_suffix(".READY")) or {}
    try:
        file_meta = PR.stream_meta(file_stream) if file_stream.is_file() else None
    except PR.SD.StreamError:
        file_meta = None
    out["records"] = compare_records(lp["copy"], file_stream)
    out["records"]["file_ready_sha256"] = ready.get("digest")
    # READY names the stream's bytes and, from fold 3's runner on, the run that wrote it.
    out["records"]["file_stream_is_ready"] = bool(
        ready.get("digest") and file_stream.is_file() and sha256(file_stream) == ready["digest"]
        and (ready.get("meta") is None or ready["meta"] == file_meta))
    out["records"]["ok"] = bool(out["records"]["ok"] and out["records"]["file_stream_is_ready"])
    relay_st = json.loads(lp["relay"].read_text()) if lp["relay"].is_file() else {}
    copy_sha = sha256(lp["copy"]) if lp["copy"].is_file() else None
    copied = lp["copy"].stat().st_size if lp["copy"].is_file() else None
    status = p["partial_status"]
    out["delivery"] = check_delivery(relay_st, copied, copy_sha,
                                     status.read_text() if status.is_file() else None)
    report = json.loads(pp["report"].read_text()) if pp["report"].is_file() else {}
    presents = (out["run"].get("stream") or {}).get("frames")
    out["player"] = check_player(report, copy_sha, presents)
    mine = read_times(pp["times"]) if pp["times"].is_file() else []
    against = []
    for fr in file_replays:
        file_times = fr / "frames.y4m.times.tsv"
        theirs = read_times(file_times) if file_times.is_file() else []
        one = {"file_replay": rel(fr), "times": compare_frames(mine, theirs),
               "y4m": same_y4m_frames(pp["y4m"], fr / "frames.y4m")}
        one["ok"] = one["times"]["ok"] and one["y4m"]["ok"]
        against.append(one)
    out["frames"] = {"against": against, "ok": bool(against) and all(a["ok"] for a in against)}
    bb = (report.get("device") or {}).get("back_buffer") or [1280, 720]
    pngs = pngs_of(pp["snaps"], file_replay)
    out["first_non_black"] = first_non_black(mine, bb[0], bb[1], pngs)
    words = p["words"] if p["words"].is_file() else p["words_zst"]
    out["liveness"] = liveness(relay_st, mine, report, game_clock(words))
    out["on_screen"] = on_screen(sessions[0], sessions[1], report, mine, pngs)
    for frame in (out["first_non_black"]["frame"], out["on_screen"]["frame"]):
        timing_of(frame, relay_st, mine, report, out["liveness"])
    # The other PNGs, for a reader's eye: informative only, since the file replay's stage3
    # directory can hold PNGs an earlier play of it left; frames.times holds every frame.
    same = {n: v["png_sha256"] == v["file_replay_png_sha256"]
            for n, v in pngs.items() if v["file_replay_png_sha256"]}
    out["snaps_vs_file_replay"] = {"compared": len(same), "equal": sum(same.values()),
                                   "differing": sorted(n for n, v in same.items() if not v)}
    return out


CHECKS = ("records", "delivery", "player", "frames", "first_non_black", "liveness",
          "on_screen")


def verdict(full: dict) -> bool:
    """Every part holds: the inputs, the build, every run check and every live check, the
    receipts' paths, and both exit statuses."""
    c = full.get("checks") or {}
    run = full.get("run") or {}
    return bool((full.get("inputs_check") or {}).get("ok")
                and (full.get("build_check") or {}).get("ok")
                and c.get("run") and all((v or {}).get("ok") for v in c["run"].values())
                and all((c.get(k) or {}).get("ok") for k in CHECKS)
                and (full.get("receipt_paths") or {}).get("ok")
                and run.get("rc") == 0 and run.get("player_rc") == 0)


def failed_parts(full: dict) -> list[str]:
    c = full.get("checks") or {}
    parts = [("inputs", full.get("inputs_check")), ("build", full.get("build_check")),
             *[(f"run.{k}", v) for k, v in (c.get("run") or {}).items()],
             *[(k, c.get(k)) for k in CHECKS], ("receipt_paths", full.get("receipt_paths"))]
    out = [k for k, v in parts if not (v or {}).get("ok")]
    if not c.get("run"):
        out.insert(2, "run (no checks)")
    run = full.get("run") or {}
    if run.get("rc") != 0:
        out.append(f"the game's exit status {run.get('rc')}")
    if run.get("player_rc") != 0:
        out.append(f"the player's exit status {run.get('player_rc')}")
    return out


# ---------------------------------------------------------------- receipts

def inputs(build: str, lp: dict, bin_dir: Path, player_build: dict | None,
           run: dict | None) -> dict:
    p = lp["game"]
    matrix = json.loads(p["matrix"].read_text()) if p["matrix"].is_file() else {}
    return {
        "run": (run or {}).get("inputs"),
        "code": code_state(),
        "replay": {"name": PR.REPLAY, "registry": rel(TR.REGISTRY),
                   "registered_result": rel(TR.OUT / PR.REPLAY / "result.json"),
                   "registered_result_sha256": sha256(TR.OUT / PR.REPLAY / "result.json")},
        "recorder": {"source": PR.RECORDER, "sha256": sha256(ROOT / PR.RECORDER),
                     "stream_h_sha256": sha256(ROOT / PR.STREAM_H),
                     "build": build, "binary": matrix.get("binary"),
                     "binary_sha256": matrix.get("sha256")},
        "player": {**player_inputs(bin_dir), "build": player_build},
        "dxvk": library(DXVK_LIB, "dxvk"),
        "sdl3": library(SDL3_LIB, "sdl3"),
        "present_live_sha256": sha256(Path(__file__)),
        "present_run_sha256": sha256(Path(PR.__file__)),
    }


def file_run(path: Path | None) -> dict:
    """The file run the file stream came from (present_run's output directory), for its
    row time: the measurement beside the rule 11 record."""
    if path is None:
        return {"why": "not given (--file-run)"}
    matrix = path / "matrix.json"
    rows = (json.loads(matrix.read_text()).get("rows") or []) if matrix.is_file() else []
    return {"dir": rel(path), "matrix": rel(matrix) if matrix.is_file() else None,
            "row_s": rows[0].get("seconds") if rows else None}


def focus_and_occlusion(report: dict, sessions: tuple[dict, dict], checks: dict,
                        presenter: dict, row_s, file_run_info: dict) -> dict:
    """What this run shows about window focus and occlusion (AGENTS.md rule 11): what was
    measured, what SDL can and cannot observe, what the structure rules out, and what
    follows."""
    win = report.get("window") or {}
    live = checks.get("liveness") or {}
    scr = checks.get("on_screen") or {}
    lg = [(s.get("logind") or {}) for s in sessions]
    run_ok = bool(checks.get("run")) and all((v or {}).get("ok") for v in checks["run"].values())
    suspended = (scr.get("presents_with") or {}).get("suspended")
    return {
        "ran_on_live_desktop_session": [s.get("live_desktop_session") for s in sessions],
        "measured": {
            "session_locked_before_after": [x.get("LockedHint") for x in lg],
            "session_idle_before_after": [x.get("IdleHint") for x in lg],
            "sdl_video_driver": scr.get("sdl_video_driver"),
            "presents_with": scr.get("presents_with"),
            "window_events": win.get("log"),
            "events": report.get("events"),
            "input_events": win.get("input_events"),
            "window_at_end": (report.get("host") or {}).get("window_end"),
            "player_idle": win.get("idle"),
            "dxvk_present_modes": presenter.get("present_modes"),
            "player_feed_blocked_s": live.get("feed_blocked_s"),
            "player_feed_blocked_max_s": live.get("feed_blocked_max_s"),
            "longest_gap_between_frames_s": (live.get("arrival_gap_s") or {}).get("max"),
            "mutter_check_alive_timeout_ms": sessions[0].get("mutter_check_alive_timeout_ms"),
            "lag_s": live.get("lag_s"), "lag_exact": live.get("lag_exact"),
        },
        "what_sdl_observes_on_wayland": WAYLAND_OBSERVES,
        "structure": {
            "player_takes_input": ("no input except a close request, which stops the player "
                                   "and fails the run; nothing reaches the game"),
            "why": ("host/player/player.cpp discards every SDL event but a close request; the "
                    "stream runs one way, game to player, and the player has no channel back "
                    "to the game"),
            "game_waits_on_window": False,
            "why_not": ("the pipe's reader only appends to a file and never waits on the "
                        "player, which is fed from that file by another thread; a Present that "
                        "blocks holds up the player alone. The probe's alarm is off, so no "
                        "wall-clock bound inside the game can fire either"),
        },
        "determinism_risk_for_replays": False,
        "determinism_risk_why": ("from the structure, not from this run passing: focus and "
                                 "occlusion reach the player's window only, the game never "
                                 "waits on the player, and no wall-clock value reaches a guest "
                                 "value (the virtual clock), so neither can move a guest value "
                                 "or a frame; a frame is read back before its Present"),
        "occlusion": ("not exercised: the compositor never suspended the window at a Present"
                      if suspended == 0 else f"suspended at {suspended} Presents"
                      if suspended else "not measured: the player sampled no window state"),
        "measurement": {
            "lifted_outputs_equal_the_registered_replay": run_ok,
            "frames_equal_the_file_replays": (checks.get("frames") or {}).get("ok"),
            "game_row_s": row_s, "file_run": file_run_info,
        },
        "wall_time": ("the player's alone: a blocked Present delays what is on screen and the "
                      "player's finish, not the game's run"),
    }


def summary(full: dict) -> str:
    """What this run showed, from its own checks, the process boundary first."""
    c = full.get("checks") or {}
    if not verdict(full):
        return f"The live run did not hold: {', '.join(failed_parts(full))}."
    rep = c.get("player") or {}
    live = c.get("liveness") or {}
    scr = c.get("on_screen") or {}
    named = scr.get("frame") or {}
    first = (c.get("first_non_black") or {}).get("frame") or {}
    against = ", ".join(a["file_replay"] for a in (c.get("frames") or {}).get("against") or [])
    lag = (live.get("lag_s") or {}).get("median")
    return (f"The lifted boot replay ran live with DXVK Native in a separate player process: "
            f"the game is a static binary and cannot load DXVK, so its Direct3D 9 calls, taken "
            f"at the recording device's COM objects as they answered them, went through a named "
            f"pipe to the player, which replayed them on DXVK's device in an SDL3 window "
            f"({scr.get('sdl_video_driver')}). Its lifted outputs equal the registered "
            f"replay's; all {rep.get('presents')} frames were read back after their bytes "
            f"reached the relay and before the game exited (median {lag} s later, at "
            f"{live.get('pace_virtual_s_per_wall_s')} virtual seconds per wall second), equal "
            f"frame for frame to {against}. Frame {named.get('index')} was presented while the "
            f"window held the compositor's keyboard focus and was not suspended, on an active, "
            f"unlocked, non-idle desktop session: PNG sha256 {named.get('png_sha256')}. The "
            f"first non-black frame is frame {first.get('index')}. No capture of the screen "
            f"was taken.")


def kept_command(cmd: list[str] | None) -> tuple[list[str] | None, str | None]:
    """The game's command as the receipt cites it: the word log the run wrote is kept only
    compressed (the words check reads either), so SA2_REPLAY_WORDS names the .zst. The path
    is the command's own, wherever the run is held from."""
    words = next((m.group(1) for a in cmd or []
                  if (m := re.search(r"SA2_REPLAY_WORDS=([^|]+)", a))), None)
    if not words or (ROOT / words).exists() or not (ROOT / f"{words}.zst").is_file():
        return cmd, None
    was, now = f"SA2_REPLAY_WORDS={words}", f"SA2_REPLAY_WORDS={words}.zst"
    return ([a.replace(was, now) for a in cmd],
            "SA2_REPLAY_WORDS is written as the file the run keeps: the run was given the same "
            "path without its .zst, and present_live compressed that log after the run and "
            "removed it, so a receipt that named it would cite a file that is gone")


def checkpoint_receipt(full: dict, lp: dict) -> dict:
    """The receipt the kill criterion S5-video-checkpoint-60h asks for, drawn from the full
    receipt: the frame, its image hash, and the run and inputs that produced it."""
    c = full.get("checks") or {}
    run = full.get("run") or {}
    rep = c.get("player") or {}
    host = rep.get("host") or {}
    matrix = {}
    if lp["game"]["matrix"].is_file():
        matrix = json.loads(lp["game"]["matrix"].read_text())
    row = (matrix.get("rows") or [{}])[0]
    game_cmd, cmd_note = kept_command(run.get("command"))
    code = (full.get("inputs") or {}).get("code") or {}
    ok = verdict(full)
    return {
        "schema": "sa2-s5-first-frame/v2",
        "id": "s5-first-frame-v1",
        "status": "final" if code.get("commit_names_the_code") else "draft",
        "status_why": ("the commit names the code that ran" if code.get("commit_names_the_code")
                       else "the commit does not name the code that ran; code.files_sha256 "
                            "does, and code.code_differing_from_commit says where"),
        "generated_by": "tools/s5/present_live.py",
        "written": full.get("written"),
        "run_written": full.get("run_written") or full.get("written"),
        "question": ("S5-video-checkpoint-60h (Documentation/kill-criteria.json): is a frame of "
                     "the game on screen through DXVK Native, the d3d9 imports reaching DXVK "
                     "through the recording device's COM objects in an SDL3 window, from a live "
                     "run -- the game running and its calls reaching DXVK as they are made?"),
        "answer": "yes" if ok else "no: see the checks",
        "how_the_calls_reach_dxvk": TRANSPORT,
        "summary": summary(full),
        "code": code,
        "inputs": {k: v for k, v in (full.get("inputs") or {}).items() if k != "code"},
        "command": {"present_live": full.get("run_argv") or full.get("argv"),
                    "held_again_by": full.get("argv") if full.get("mode") == "check" else None,
                    "game": game_cmd, "game_note": cmd_note,
                    "player": run.get("player_command")},
        "transport": ("named pipe: the recorder writes SA2_REC_TRACE=" + rel(lp["fifo"])
                      + "; present_live's reader appends each chunk to " + rel(lp["copy"])
                      + ", and a feeder thread hands the copy to the player's stdin as it grows"),
        "host": {"sdl_video_driver": host.get("sdl_video_driver"),
                 "dxvk": rep.get("dxvk"),
                 "present_modes": (full.get("dxvk_presenter") or {}).get("present_modes"),
                 "device": rep.get("device"),
                 "session": full.get("session"), "session_after": full.get("session_after")},
        "checkpoint_frame": c.get("on_screen"),
        "first_non_black_frame": c.get("first_non_black"),
        "liveness": c.get("liveness"),
        "focus_and_occlusion": full.get("focus_and_occlusion"),
        "frames": {"presents": rep.get("presents"), "frames_written": rep.get("frames_written"),
                   "stream_frames": ((c.get("run") or {}).get("stream") or {}).get("frames")},
        "parity": {
            "inputs": {k: (full.get("inputs_check") or {}).get(k) for k in ("ok", "differing")},
            "build": {k: (full.get("build_check") or {}).get(k)
                      for k in ("ok", "binary_sha256", "hooks_reused", "not_allowed",
                                "code_finding_count", "tied", "objects")},
            "lifted": {k: {"ok": (v or {}).get("ok"),
                           **({"want": v.get("want"), "got": v.get("got")}
                              if k in ("lifted_csv", "reach") else {})}
                       for k, v in (c.get("run") or {}).items()},
            "record": {x: ((c.get("run") or {}).get("record") or {}).get(x)
                       for x in ("leaves", "allowed", "differing_count")},
            "words": {x: ((c.get("run") or {}).get("words") or {}).get(x)
                      for x in ("ticks", "tick_hashes", "diff")},
            "stream_records_vs_file_stream": {x: (c.get("records") or {}).get(x) for x in
                                              ("ok", "file", "records_bytes", "records_sha256",
                                               "meta_keys_differing", "same_recorder",
                                               "file_ready_sha256")},
            "frames_vs_file_replays": {
                "ok": (c.get("frames") or {}).get("ok"),
                "against": [{"file_replay": a["file_replay"], "ok": a["ok"], "times": a["times"],
                             "y4m_frames": {x: a["y4m"].get(x) for x in
                                            ("ok", "headers", "bytes", "sha256")}}
                            for a in (c.get("frames") or {}).get("against") or []]},
            "snaps_vs_file_replay": c.get("snaps_vs_file_replay"),
            "delivery": c.get("delivery"),
            "player": {x: rep.get(x) for x in ("ok", "pass", "exit_status", "errors",
                                                "not_handled", "present_failures",
                                                "readback_failures", "stop", "stream_sha256",
                                                "allowed_host_failures")},
        },
        "wall": {"game_row_s": row.get("seconds"),
                 "game_process_s": run.get("seconds"),
                 "pipe_open_after_spawn_s": run.get("pipe_open_after_spawn_s"),
                 "player_s": run.get("player_seconds"),
                 "player_after_game_s": run.get("player_after_game_s"),
                 "registered_alarm_s": PR.REGISTERED_ALARM_S,
                 "past_registered_alarm": (row.get("seconds") is not None
                                           and row["seconds"] > PR.REGISTERED_ALARM_S),
                 "probe_timeout_s": PROBE_TIMEOUT, "loop_run_timeout_s": RUN_TIMEOUT},
        "receipt_paths": full.get("receipt_paths"),
        "failed": failed_parts(full),
        "ok": ok,
        "full_receipt": rel(lp["receipt"]),
    }


def relativized(obj, roots: list[tuple[str, str]] | None = None):
    """`obj` with every absolute path into this checkout, or into what its links resolve to
    (a worktree's private/ and .venv), written relative to the repository."""
    if roots is None:
        roots = [(str(ROOT) + "/", ""), (str(ROOT), ".")]
        for name in ("private", ".venv"):
            real = (ROOT / name).resolve()
            if real != ROOT / name:
                roots.append((str(real) + "/", name + "/"))
                roots.append((str(real), name))

    def fix(s: str) -> str:
        for a, b in roots:
            s = s.replace(a, b)
        return s

    if isinstance(obj, dict):
        return {fix(k) if isinstance(k, str) else k: relativized(v, roots) for k, v in obj.items()}
    if isinstance(obj, list):
        return [relativized(v, roots) for v in obj]
    if isinstance(obj, str):
        return fix(obj)
    return obj


def receipt_path_problems(checkpoint: str, full: str, writing: list[Path]) -> dict:
    """The receipts' paths. The checkpoint receipt, which goes under Documentation/receipts/,
    as the cited-files gate will read it (check_cited_files.py's CITED and normalise): every
    file it cites exists, but those being written now. And neither receipt holds this
    checkout's absolute path or a worktree's. The full receipt stays under private/ and
    keeps the game's command as it ran."""
    cited, missing = set(), set()
    skip = {rel(w) for w in writing}
    for pattern in (CCF.CITED, CCF.CITED_NATIVE_RUN):
        for m in pattern.finditer(checkpoint):
            path = CCF.normalise(m.group(1))
            if path is None or path.split("/", 1)[0] in CCF.SKIPPED_PREFIXES:
                continue
            cited.add(path)
            if path not in skip and not ((ROOT / path).exists() or (ROOT / path).is_symlink()):
                missing.add(path)
    absolute = sorted({m.group(0) for text in (checkpoint, full) for m in re.finditer(
        re.escape(str(ROOT)) + r"[^\"\s]*|/\.claude/worktrees/[^\"\s]*", text)})
    return {"cited": len(cited), "missing": sorted(missing), "absolute": absolute[:10],
            "ok": not missing and not absolute}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--build", default=f"present-live.{PR.tree_tag()}")
    ap.add_argument("--out", type=Path, default=LIVE / "run")
    ap.add_argument("--player-bin", type=Path, default=LIVE / "bin",
                    help="where host/player/build.sh puts the player (built here unless "
                         "--no-player-build)")
    ap.add_argument("--no-player-build", action="store_true")
    ap.add_argument("--file-stream", type=Path, default=None,
                    help="a file run's stream from this tree's recorder, beside its READY "
                         f"(default {FILE_STREAM}; --check: the run's)")
    ap.add_argument("--file-replay", type=Path, action="append", default=None,
                    help="a file replay's output directory (frames.y4m, its times, stage3/); "
                         "repeatable, the first the PNGs' reference "
                         f"(default {FILE_REPLAY}; --check: the run's)")
    ap.add_argument("--file-run", type=Path, default=None,
                    help="present_run's output directory for the file stream's run (its "
                         "matrix.json): its row time goes beside this run's in the rule 11 "
                         "record (--check: the run's)")
    ap.add_argument("--receipt", type=Path, default=LIVE / "s5-first-frame-v1.json")
    ap.add_argument("--check", action="store_true", help="hold the last live run again, no run")
    ap.add_argument("--wait", type=int, default=7200,
                    help="seconds to wait for a gate pass or another presentation run to "
                         "end, for memory, and for an unlocked, non-idle desktop session "
                         "before refusing")
    args = ap.parse_args(argv)
    why = PR.build_name_problem(args.build)
    if why:
        print(why, file=sys.stderr)
        return 2
    out = ROOT / args.out
    bin_dir = ROOT / args.player_bin
    lp = live_paths(out)
    old = None
    if args.check:
        old = json.loads(lp["receipt"].read_text()) if lp["receipt"].is_file() else None
        if not old:
            print(f"no live run to hold: {rel(lp['receipt'])} is absent", file=sys.stderr)
            return 2
    file_stream = ROOT / (args.file_stream or (old or {}).get("file_stream") or FILE_STREAM)
    file_replays = [ROOT / f for f in (args.file_replay or (old or {}).get("file_replays")
                                       or [FILE_REPLAY])]
    file_replay = file_replays[0]
    run_dir = args.file_run or ((old or {}).get("file_run") or {}).get("dir")
    reg = PR.registered()
    full: dict = {"schema": "sa2-present-live/v2", "replay": PR.REPLAY, "build": args.build,
                  "mode": "check" if args.check else "run",
                  "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "argv": [str(Path(sys.executable).name), rel(Path(__file__))]
                  + (argv if argv is not None else sys.argv[1:]),
                  "checked_by_sha256": sha256(Path(__file__)),
                  "file_stream": rel(file_stream), "file_replays": [rel(f) for f in file_replays],
                  "file_run": file_run(ROOT / run_dir if run_dir else None)}
    full["inputs_check"] = PR.check_inputs(reg)
    if old:
        for k in ("run", "session", "session_after", "build_check", "inputs", "overlay_after"):
            full[k] = old.get(k)
        full["run_written"] = old.get("run_written") or old.get("written")
        full["run_argv"] = old.get("run_argv") or old.get("argv")
    else:
        if not full["inputs_check"]["ok"]:
            print(f"NOT RUN: the tree's inputs are not the registered result's: "
                  f"{full['inputs_check']['differing'][:10]}", file=sys.stderr)
            return 1

        def blocked() -> list[str]:
            why = [f"gate pass {g}" for g in gate_passes()]
            why += [f"presentation run {g}" for g in processes(is_presentation_run)]
            if mem_available_gb() < MIN_FREE_GB:
                why.append(f"{mem_available_gb():.1f} GB available (want {MIN_FREE_GB})")
            s = session()
            if not session_holds(s):
                why.append(f"the desktop session is not live, unlocked and non-idle: "
                           f"{s.get('logind')}")
            return why

        waited = 0
        while (busy := blocked()) and waited < args.wait:
            if waited % 600 == 0:
                print("waiting: " + "; ".join(busy), flush=True)
            time.sleep(20)
            waited += 20
        if busy := blocked():
            print("refusing: " + "; ".join(busy) + " (the machine is shared with the gates, and "
                  "a frame on a locked or idle screen is on no one's screen: that needs the "
                  "user at the desktop)", file=sys.stderr)
            return 2
        player_build = None
        out.mkdir(parents=True, exist_ok=True)
        if not args.no_player_build:
            player_build = build_player(bin_dir, lp["build_log"])
            if not player_build["ok"]:
                print(f"the player did not build; see {rel(lp['build_log'])}", file=sys.stderr)
                return 2
        file_rows = read_times(file_replay / "frames.y4m.times.tsv")
        first_ref = first_game_frame(file_rows, 1280, 720)
        snaps = snap_frames(file_replay, first_ref["index"] if first_ref else None)
        full["session"] = session()
        full["run_written"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        full["run"] = run_live(args.build, lp, reg, bin_dir, snaps, PR.run_inputs(reg))
        full["session_after"] = session()
        full["build_check"] = PR.check_build(args.build, lp["game"]["matrix"])
        full["overlay_after"] = PR.overlay_files(lp["game"])
        full["inputs"] = inputs(args.build, lp, bin_dir, player_build, full["run"])
        print(f"game: rc {full['run']['rc']} in {full['run']['seconds']} s; player: rc "
              f"{full['run']['player_rc']} in {full['run']['player_seconds']} s", flush=True)
    full["dxvk_presenter"] = dxvk_presenter(lp["dxvk_log"])
    p = lp["game"]
    if p["words"].is_file():
        # Kept compressed, as present_run keeps it, before the checks: they read either
        # form, and then every path they record is the file that stays.
        z = subprocess.run(["zstd", "-3", "-q", "-f", "--rm", str(p["words"]), "-o",
                            str(p["words_zst"])], capture_output=True, text=True)
        full["words_kept"] = {"path": rel(p["words_zst"]), "rc": z.returncode}
    sessions = (full.get("session") or {}, full.get("session_after") or {})
    full["checks"] = hold(lp, reg, full.get("run"), file_stream, file_replays, sessions)
    report = (json.loads(lp["player"]["report"].read_text())
              if lp["player"]["report"].is_file() else {})
    rows = (json.loads(p["matrix"].read_text()).get("rows") or []) if p["matrix"].is_file() else []
    secs = rows[0].get("seconds") if rows else None
    full["focus_and_occlusion"] = focus_and_occlusion(report, sessions, full["checks"],
                                                      full["dxvk_presenter"], secs,
                                                      full["file_run"])
    full["alarm"] = {"registered_alarm_s": PR.REGISTERED_ALARM_S, "run_alarm_s": PROBE_TIMEOUT,
                     "row_seconds": secs, "past_registered_alarm": (
                         secs is not None and secs > PR.REGISTERED_ALARM_S)}
    receipt = ROOT / args.receipt
    full["written"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    full = relativized(full)
    full["receipt_paths"] = {"ok": True}          # provisional: the check reads the texts
    full["receipt_paths"] = receipt_path_problems(
        json.dumps(relativized(checkpoint_receipt(full, lp)), default=str),
        json.dumps(full, default=str), [lp["receipt"], receipt])
    full["ok"] = verdict(full)
    lp["receipt"].write_text(json.dumps(full, indent=1, default=str) + "\n")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(relativized(checkpoint_receipt(full, lp)), indent=1,
                                  default=str) + "\n")
    c = full["checks"]
    for k, v in [("inputs", full.get("inputs_check") or {}), ("build", full.get("build_check") or {}),
                 *[(f"run.{k}", v) for k, v in c["run"].items()],
                 *[(k, c[k]) for k in CHECKS], ("receipt_paths", full["receipt_paths"])]:
        print(f"{'ok  ' if (v or {}).get('ok') else 'FAIL'} {k}")
    f = c["on_screen"].get("frame") or {}
    first = c["first_non_black"].get("frame") or {}
    print(f"named frame: {f.get('index')} {f.get('png')} {f.get('png_sha256')} (file replay's "
          f"PNG {'equal' if f.get('png_equal') else 'NOT equal'}); first non-black frame: "
          f"{first.get('index')}")
    live = c["liveness"]
    print(f"liveness: lag exact {live.get('lag_exact')}, {live['frames_read_back']} frames read "
          f"back, {live['read_back_at_or_after_game_exit']['count']} at or after the game's "
          f"exit, last {live.get('last_read_back_before_exit_s')} s before it; lag "
          f"{live.get('lag_s')}")
    print(f"receipt {rel(lp['receipt'])}; checkpoint {rel(receipt)}; "
          f"{'OK' if full['ok'] else 'NOT OK: ' + ', '.join(failed_parts(full))}")
    return 0 if full["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
