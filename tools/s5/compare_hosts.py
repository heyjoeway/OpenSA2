#!/usr/bin/env python3
"""Hold the second host (host/d3d9gpu on SDL_gpu) to the first (DXVK Native) on one stream.

`run` defaults to --scope r2-draws-full. It requires stage 3, the original
logical END and consumed-record account, exact accepted outcome maps, zero unmapped,
failed or suppressed geometry, actual UP uploads and identical hashes on every completed
frame. With --controls it requires geometry-noop to change a completed geometry-bearing
frame and count-draw to omit exactly the independently identified first accepted draw.
R2_DRAW_OK additionally requires the registered original boot stream and its reference;
a synthetic full stream has only R2_STREAM_GEOMETRY_OK.

--scope r2-draws-partial --max-frames N runs the unchanged registered boot stream through
N original Presents and FRAME records. Offline complete=true means finalized. The checker
requires stop=--max-frames, zero consumed END, stream_records_read_to_end=false,
stream_sha256_after_stop=true, exact consumed records/bytes, all completed hashes and the
same geometry suppression contrast. Its marker is R2_PARTIAL_GEOMETRY_OK.

--scope r2a-clears explicitly retains the historical contract below. Its receipt marker
is R2A_CLEARS_OK and cannot establish R2 geometry. The independent clear matrix and build,
player and pipeline provenance checks remain required in all scopes.

Historical r2a-clears subtitle: R2a's check that can fail (private/generated/s5/r2a/PLAN.md, section 6, with
the critic's amendments). The second host has no shader route yet, so it draws nothing;
what it must already do exactly as DXVK does is everything else: answer every call as
DXVK answers it, take every draw as DXVK takes it (counted, not drawn), and give every
frame no draw reached the same pixels. `run` plays one stream on both hosts and fails
unless every named check below passes. The hosts are two binaries built from
host/player/player.cpp: sa2_player (host/player/build.sh), the DXVK host, which runs every
DXVK run; and sa2_player_gpu (host/d3d9gpu/build.sh), the second host with host/d3d9gpu
linked in, which runs the second host's run, every control and the clear matrix's SDL_gpu
leg. The role is this tool's, not the report's. It runs only a build tied to the tree it
pins (`provenance`): the build's four records (host/player/build_record.py: sa2_player.json
and sa2_player_gpu.json, each player's exact record from its compiles' depfiles, and
player_tools.json and d3d9gpu_tools.json, the other binaries' group records) name every
binary in it, no binary twice, and every source and generated input it read, each with the
hash it has now, and every file the pipeline pins under host/player, host/d3d9gpu and
host/sa2_stream.h; each player's exact record names its own translation units, the stream
header and its build script (REQUIRED_SOURCES), and every file its recorded depfiles name,
read again here (a record that dropped a file its compiles read is refused, and so is one
whose depfiles name a path through a symbolic link in its checkout); the DXVK
player's record names no host/d3d9gpu source and its contract is DXVK's five keys, the GPU
player's contract is SDL's three; a build that fails this is
refused before anything runs, and after the runs each run's player (and the second host's
device's own account of its file) is held to the build's binary for its role (a failure
there fails the verdict):

  a  runs      both runs complete and pass (host/player's report and
               tools/s5/play_report.py check): exit 0, end of stream, one END, no error,
               no call not handled, no host failure, no readback or Present failure, at
               stage 3 no placeholder texture (below it every texture is one, by the
               player's design), the census's Present count, the stream the census read,
               and each host's identity: its d3d9_host, SDL verified, DXVK's library and
               dxvk.conf verified by the DXVK run, and the second host's run pinning,
               verifying and expecting no DXVK file (check_s5_exit.py renderer_loaded's
               "pins no DXVK library and verified none"), each of these fields in its
               report (an absent field is not a null one); the two reports' keys, at the
               top and under files and host_identity, are one set (the second host's player
               writes the DXVK player's report fields, its DXVK pins null); neither report
               has a field of a player that loaded the second host's library
               (LIBRARY_FIELDS), and the two runs' players are two files
  b  device    the device's own report (SA2_D3D9GPU_REPORT): complete, no failure, no
               fault, nothing drawn, nothing not implemented, nothing left alive at the
               end, the player's Present count, its library the player that ran (its file
               and sha256 those of the run's files.player: one process's two reads of
               /proc/self/exe, which tie the device's report to the run's executable; that
               the executable is the build's sa2_player_gpu is provenance's), no frame
               without a swapchain texture in a window not hidden, no clear past an image's
               edge, the x87 control word and MXCSR unchanged, no debug mode, and every
               failed answer it gave shown on the player's rows
  c  draws     the census's draw count = DXVK's report's = d3d9gpu's report's = the
               device's; the device's refused, empty and skipped draws by method and
               primitive type, per frame and after the last Present, = the census's; the
               refused ones = the failures on both reports' draw rows; the UP arena's
               bytes = the census's
  d  answers   both reports' method rows (every category, failures, HRESULTs differing
               from the guest's) and their calls, textures, allowed failures, targets and
               host failures are equal, and so is the x87 control word around CreateDevice
  e  frames    both times files have the census's rows, row i frame i, equal in every
               column but wall_us and bgrx_hash; every frame the census finds no draw call
               in has the same bgrx_hash on both hosts; and there is at least one such frame
               (its detail lists DXVK's distinct hashes on them, each marked when it is an
               all-black back buffer)
  f  lineage   no draw-free frame shows a back buffer a draw reached since its last full
               colour clear (it would not be made by clears alone), and the census's Clear
               calls are the device's (the lineage rests on what the device received)
  g  identity  both hosts ran on one GPU and driver: DXVK's device name and driver (its
               log) are SDL_gpu's (the device's report)
  h  reference with a registered reference times file (--reference, or the one this tool
               knows for the stream), the fresh DXVK run equals it in the six fields; a
               difference means the driver or the environment drifted
  i  matrix    the clear matrix (host/d3d9gpu/test/clear_matrix.h, host/d3d9gpu/test_d3d9gpu.sh
               step 2), which `run` runs on the build before the players: through the DXVK
               run's library (d3d9gpu_clear_matrix), through sa2_player_gpu --clear-matrix on
               SDL_gpu and on the CPU model (test_d3d9gpu --clear-matrix), every line equal,
               each run exit 0, the device's report complete with no fault and its library
               the second host's run's player, and both GPU runs on the players' GPU and
               driver (DXVK's log, the device's report). Its images hold what a comparable
               frame, made by clears alone, cannot: partial rectangles, scissor, channel
               order, and the row order and pitch of a read back (GetRenderTargetData and
               LockRect, as the player reads its frames)

With --controls it then reruns the second host under named faults (host/d3d9gpu
d9g.h, SA2_D3D9GPU_FAULT) and requires each to move its check as named: clear-colour
must fail e on every draw-free frame; count-draw must fail c; scene-answer must fail d
where the stream exercises the rule (else "not exercised", which is not a pass);
present-scribble (the back buffer scribbled after every Present) must still pass e;
present-scribble with clear-noop must fail e on every draw-free frame. Each must do so
for its fault's exact effect and no other (CONTROLS' "reason"): the control's run passes
every other check, the device's report excused only its fault's name (and, for
count-draw, the one draw it left uncounted), and its target check passes once that effect
is undone (e outside the comparable frames' hashes; c with the draw count-draw left
uncounted put back; d against DXVK's report with the rule's BeginScene answers made
D3D_OK), so a run that crashed, stopped early or read back no frame is not a control that
behaved. The verdict with controls is PASS only when the checks pass and every control
behaved as required.

  run      --stream S --build BIN [--stage 3] [--out DIR] [--controls] [--hidden]
           [--scope r2-draws-full|r2-draws-partial|r2a-clears] [--max-frames N]
           [--reference TIMES] [--allow-notes]
           (--build is required: a build is named, never assumed; DIR must be new or empty;
           BIN holds both builds, host/player/build.sh's and host/d3d9gpu/build.sh's)
  check    --run DIR [--reference TIMES]   recompute compare.json from the files a run left,
           refused unless the build is still the one the run recorded and still matches the
           tree, every file the run recorded (census, reports, times files, the matrix's) has
           the hash it recorded, and every run's check.json was written by the play_report.py
           this check pins. The controls are the ones the run ran (controls.run), the
           reference the one it held (unless --reference names another), and the
           compare_hosts.py that ran the players and the matrix is carried forward as
           inputs.ran_by beside the pins of the one that checked
  dxvk-ab  --before BIN --before-commit C --after BIN --stream S [--stage N] [--out DIR]
           [--allow-notes] [--hidden]
           the DXVK path unchanged by a change to the DXVK path: both DXVK players
           (sa2_player) on DXVK, each under its own checkout's run_player.sh and
           player_env.sh (the before's: its clone at C), every times column but wall_us
           equal row for row, the reports equal outside a named allowlist
           (DXVK_AB_ALLOWED) and the identity fields the two binaries changed, each held
           per side (DXVK_AB_SPLIT: the after names its host "dxvk" and writes none of
           LIBRARY_FIELDS; a before may write them with DXVK's answers, its expected
           library its own build's; the after writes end_request as a run without
           --end-fd does, END_REQUEST_UNUSED, and a before writes that or none of it),
           and DXVK's logs equal outside DXVK_AB_LOG_ALLOWED; each side's own checkout's
           dxvk.conf path, where a report field or a log line holds it, reads as OWN_CONF,
           and its own install's DXVK library path (DXVK_LIBRARY), where a report field
           holds it, as OWN_LIBRARY (any other path still differs). --after is held to
           the tree as `run`'s build is (its sa2_player.json the DXVK player's exact
           record); --before must carry a build record (host/player/build_record.py)
           whose sources, tools/pins.json among them, are commit C's, file for file (git
           cat-file), none from outside its checkout, whose checkout's run_player.sh,
           player_env.sh and dxvk.conf are C's, and whose player is the one that runs; the
           same player on both sides is refused, and so is an after whose DXVK path is C's
           (dxvk-unchanged's UNCHANGED, by the same rule: an A/B of one DXVK path compares
           nothing). dxvk-ab.json names what differs (host_sources_differing, the rule's
           list), each side's run-time files, and the split fields with both sides' values
  dxvk-unchanged  --after BIN --before-commit C [--out DIR]
           whether the DXVK path of this build is commit C's by construction: every source
           of the DXVK player's exact record (sa2_player.json: what its compiles read, its
           build script, generator, dxvk.conf and pins), host/player/run_player.sh and
           player_env.sh, each C's (git cat-file), and no file C holds where an include of
           a file this build read, or of a header in its compiler's system directories
           (the search list build_record.py recorded from the compiler in the build's
           environment, which a depfile does not show), could have found it first, and no
           symbolic link or other entry not a file under those directories at C (git
           ls-tree; shadowing_at).
           Exit 0 UNCHANGED, 1 CHANGED (dxvk-ab is then the evidence: it varies exactly
           these, the before built and run from a clone at C), 2 refused (a build not held
           to the tree, no commit C, a record with no search list or whose compiler's
           --version line is not the recorded one). What it does not hold (the compiler,
           the install's headers, the system's, build_record.py) is named in
           dxvk-unchanged.json's not_held
  receipt  --run DIR --id ID --out PATH [--also DIR ...]   the tracked receipt (its id the
           caller's: no default, so no receipt takes another's; PATH must not exist), from
           compare.json (hashes, counts and frame numbers; no shipped bytes, AGENTS.md rule
           9); --also adds
           another stream's comparison by the same pipeline and binaries (Metal Harbor's,
           the synthetic stream's), whose failure, or a control of its own not behaving,
           fails the receipt's verdict. Refused for a run whose build was not held to the
           tree, that ran no clear matrix (check i, which the receipt records), or whose
           runs another compare_hosts.py made than the one it pins (inputs.ran_by), and when
           every comparable frame of every run is black (no wrong clear colour, and no read
           back that reads nothing, could show on them)

Everything is written under DIR (default private/generated/s5/r2a/runs/<UTC stamp>); each
run's frames file is deleted once play_report.py has held it to its report (its times file
is kept). Exit status: 0 PASS, 1 FAIL (for receipt: the run's verdict with controls not PASS,
or every comparable frame black), 2 usage or a refused input (a build not the tree's, a run
dir or a compare.json that cannot be used, the main run's or an --also run's alike).
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import fnmatch
import hashlib
import importlib.util
import itertools
import json
import os
import re
import mmap
import math
import struct
import shlex
import subprocess
import tempfile
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import play_report  # noqa: E402
import stream_dump  # noqa: E402

# host/player/build_record.py, the writer of the build records: its depfile reader, with which
# provenance reads a player's recorded depfiles again (loaded by path; host/player is no package).
_spec = importlib.util.spec_from_file_location("build_record", ROOT / "host" / "player" / "build_record.py")
build_record = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_record)

PY = ROOT / ".venv" / "bin" / "python"
# This tool by repository path, and the sha256 of the code running now (read as it was
# imported): what made a run's players and matrix, which `check` carries forward.
SELF = "tools/s5/compare_hosts.py"
RAN_BY = play_report.sha256_file(Path(__file__).resolve())
# The repository whose commits dxvk-ab's --before-commit names.
GIT_REPO = ROOT
DEFAULT_RUNS = ROOT / "private" / "generated" / "s5" / "r2a" / "runs"
RUN_PLAYER = ROOT / "host" / "player" / "run_player.sh"
PLAYER_ENV = ROOT / "host" / "player" / "player_env.sh"
PLAY_REPORT = "tools/s5/play_report.py"
# Beside each run's check.json: the sha256 of the play_report.py that wrote it.
CHECKED_BY = "check.play_report.sha256"
# The clear matrix's hosts (check i), DXVK's lines first: the others must equal them; and the
# binaries that run it (DXVK's leg, the CPU model's, and SDL_gpu's: the second host's player).
MATRIX_HOSTS = ("dxvk", "sdlgpu", "cpu")
MATRIX_BINARIES = ("d3d9gpu_clear_matrix", "test_d3d9gpu", "sa2_player_gpu")
MATRIX_TIMEOUT_S = 600
PLAYER_TIMEOUT_S = 4 * 3600
# The six fields every registered comparison holds (Documentation/receipts/s5-metalharbor-v1.json).
BOOT_REFERENCE_SHA256 = "cf1913bdef6b7eb6fd89d86affe60fa5b1812bf516fffafde7693c13203ccc76"
BOOT_STREAM_SHA256 = "37ff5dd7756d8411d7c12161397d2cca8d3a4bc9f58af78644abcc0ef5f4d555"
METAL_HARBOR = {
    "name": "metalharbor", "stage": 3, "frames": 15709, "marker": "R4_METALHARBOR_OK",
    "stream_sha256": "f7f6b4e18ed1a0a117fa2701e12c0092b86d3887ec4b3e82a1d40cdfecee2e53",
    "reference": "private/generated/s5/pushC/fresh-picture/frames.y4m.times.tsv",
    "reference_sha256": "f4864c3d5d0b210b60ead83da1086d9bf30618199228228b6b603927921e024b",
}
TIMES_FIELDS = ("frame", "seq", "virtual_us", "guest_frames", "polls", "bgrx_hash")
NON_HASH_FIELDS = TIMES_FIELDS[:5]
# Registered DXVK references by stream sha256 and stage: the boot's (the live run of
# Documentation/receipts/s5-first-frame-v1.json, which the platform-startup regression
# reproduced row for row).
REFERENCES = {
    ("37ff5dd7756d8411d7c12161397d2cca8d3a4bc9f58af78644abcc0ef5f4d555", 3):
        "private/generated/s5/pushB/live/player/frames.y4m.times.tsv",
}
DRAW_METHODS = ("DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP")
CHECK_TITLES = {
    "a": "runs", "b": "device", "c": "draws", "d": "answers", "e": "frames", "f": "lineage", "g": "identity",
    "h": "reference", "i": "matrix",
}
# Each control's "reason" is what its fault may change in its target check, held to the
# fault's exact effect, and nothing else may fail (control_outcome, reason_failures):
# "hashes", only comparable frames' bgrx_hash (every row present, frame i at row i, equal
# outside the hash); "uncounted", check c passes once the one draw the device left
# uncounted is put back (into one skipped class and one frame, one the stream's first
# skipped draw can be in: restored_draws); "begin_scene", check d passes against DXVK's
# report with every BeginScene the rule allowed answered D3D_OK (answered_ok).
CONTROLS = (
    {"name": "clear-colour", "faults": "clear-colour", "check": "e", "want": "fail", "must_differ": "colour_cleared",
     "reason": "hashes",
     "why": "a Clear colour off by one in each channel must change every draw-free frame: the comparison sees the "
            "clear colour, black frames or not"},
    {"name": "count-draw", "faults": "count-draw", "check": "c", "want": "fail", "exercised_by_draws": True,
     "reason": "uncounted",
     "why": "one draw the device leaves uncounted must break the three-way draw count"},
    {"name": "scene-answer", "faults": "scene-answer", "check": "d", "want": "fail",
     "exercised_by": "scene order: BeginScene inside a scene", "reason": "begin_scene",
     "why": "BeginScene inside a scene answered D3D_OK, not DXVK's D3DERR_INVALIDCALL, must break answer parity"},
    {"name": "present-scribble", "faults": "present-scribble", "check": "e", "want": "pass", "reason": "hashes",
     "why": "the back buffer scribbled after every Present (D3D9 allows it under DISCARD) must not show on a "
            "draw-free frame, each of which is cleared whole before it is read"},
    {"name": "present-scribble+clear-noop", "faults": "present-scribble,clear-noop", "check": "e", "want": "fail",
     "must_differ": "after_first_present", "reason": "hashes",
     "why": "with Clear doing nothing the scribble must show on every draw-free frame: the comparison sees what "
            "Clear covers, which a zero-initialised black back buffer alone would hide"},
)
# One BeginScene the player lists under a named rule (allowed_host_failures.calls): its seq,
# host and guest answers, and the rule (host/player/player.cpp Player::count).
ALLOWED_CALL = re.compile(r"seq (\d+) Device::BeginScene: host (0x[0-9a-f]{8}), guest (0x[0-9a-f]{8}) \((.+)\)")
D3D_OK = "0x00000000"
# The two hosts are two binaries (host/player/player.cpp, "Two binaries"): the binary each
# host's runs are, each player's exact build record, and its expectations contract
# (directory under the build, and its keys in order: host/player/generate_expectations.py).
PLAYERS = {"dxvk": "sa2_player", "sdlgpu": "sa2_player_gpu"}
PLAYER_RECORDS = {"sa2_player": "sa2_player.json", "sa2_player_gpu": "sa2_player_gpu.json"}
CONTRACTS = {"sa2_player": ("expected", ("sdl_sha256", "dxvk_sha256", "dxvk_config_sha256", "sdl_version",
                                         "sdl_revision")),
             "sa2_player_gpu": ("expected-gpu", ("sdl_sha256", "sdl_version", "sdl_revision"))}
EXACT_RECORD = "sa2-build-record/v2"
# What each player's exact record must name among its sources: its compiles' own translation
# units, the stream header they read and its build script (the second host's player also
# d9g's entry point and its build script). The rest of the record is held to its depfiles,
# which provenance reads again; a record without these would hold nothing of the binary.
PLAYER_TUS = ("player", "input", "mixer", "stream", "sha256", "texcodec")
REQUIRED_SOURCES = {
    "sa2_player": (*(f"host/player/{tu}.cpp" for tu in PLAYER_TUS), "host/sa2_stream.h", "host/player/build.sh"),
    "sa2_player_gpu": (*(f"host/player/{tu}.cpp" for tu in PLAYER_TUS), "host/sa2_stream.h",
                       "host/d3d9gpu/d9g_entry.cpp", "host/d3d9gpu/build.sh"),
}
# The report fields of the one player that dlopened the second host's library (development_snapshot's
# --d3d9 sdlgpu): neither binary writes them (check a), and dxvk-ab holds them per side.
LIBRARY_FIELDS = ("files.libsa2_d3d9gpu", "host_identity.d3d9gpu_file_verified",
                  "host_identity.expected_d3d9gpu_sha256")
# Check a: where the two players' reports have one key set ("" the top level). Below these,
# a report's keys follow what its run met (a rule's name under allowed_host_failures.rules).
REPORT_KEY_LEVELS = ("", "files", "host_identity")
# dxvk-ab: report fields that may differ between two DXVK runs of one stream, each
# named. Wall time and the window's own events; the player's file (two builds); each run's
# own output paths; DXVK's log, whose path is per run and whose memory-budget lines move
# with the GPU's free memory (its differing lines are listed in dxvk-ab.json).
DXVK_AB_ALLOWED = (
    "wall_s", "t_start_monotonic_s", "window", "window.*", "events", "events.*", "host.window_end",
    "host.window_end.*", "files.player", "files.player.*", "dxvk.log", "dxvk.log_bytes_read", "dxvk.log_sha256",
    "env.DXVK_LOG_PATH", "frames_out.path", "frames_out.times",
)
# dxvk-ab: the identity fields the host's player writes, not free to differ but fixed per
# side, each by the values it may take (ABSENT: not in the report). The after side is the
# DXVK player, sa2_player: it names its host and writes none of LIBRARY_FIELDS. The before
# side is a player of any commit: an older one writes none of them; the one player of
# development_snapshot wrote DXVK's answers, its expected library the before build's own
# (OWN_CONTRACT: contract.d3d9gpu_sha256 of its expected/expectations.json, which
# before_provenance holds to its record).
ABSENT, OWN_CONTRACT = "<absent>", "<the before build's own contract.d3d9gpu_sha256>"
# The report's "end_request" block (host/player/player.cpp end_request_json) of a run given no
# --end-fd, as every dxvk-ab run is (run_player passes none): no fd, no request, nothing written.
# A player from before the block writes none of it; one that writes it writes exactly this.
END_REQUEST_UNUSED = {"fd": False, "requested": False, "by": None, "after_frame": None, "at_s": None,
                      "written": False, "closes_after": 0, "error": None}
DXVK_AB_SPLIT = {
    "host_identity.d3d9_host": {"after": ("dxvk",), "before": (ABSENT, "dxvk")},
    "files.libsa2_d3d9gpu": {"after": (ABSENT,), "before": (ABSENT, None)},
    "host_identity.d3d9gpu_file_verified": {"after": (ABSENT,), "before": (ABSENT, False)},
    "host_identity.expected_d3d9gpu_sha256": {"after": (ABSENT,), "before": (ABSENT, OWN_CONTRACT)},
    "end_request": {"after": (END_REQUEST_UNUSED,), "before": (ABSENT, END_REQUEST_UNUSED)},
}
# DXVK's log lines that may differ between two runs, each named: the loader's address of
# vkGetInstanceProcAddr (address-space layout) and a heap's budget (the GPU's free memory).
# Any other differing line fails dxvk-ab.
DXVK_AB_LOG_ALLOWED = (
    r"info:  Vulkan: Found vkGetInstanceProcAddr in \S+ @ 0x[0-9a-f]+",
    r"info:    Budget: \d+(\.\d+)? [KMGT]?i?B",
)
# The paths dxvk-ab's before group record (v1) must hold at --before-commit, file for file:
# what host/player/build.sh read from the checkout, its tools/pins.json the clone's
# (host/d3d9gpu/build_before.sh builds with SA2_REPO the clone).
BEFORE_SOURCES = ("host/player", "host/sa2_stream.h", "host/dxvk_smoke.cpp", "tools/pins.json")
# The DXVK path's run-time files: each side of dxvk-ab runs its player under its own
# checkout's (the after this tree's, the before its clone's at the commit), so
# dxvk-unchanged holds them to the commit beside the player's sources; and the dxvk.conf
# each side's player_env.sh names (the before's held to the commit with them).
RUNTIME = ("host/player/run_player.sh", "host/player/player_env.sh")
DXVK_CONF = "host/player/dxvk.conf"
# dxvk-ab: a side's own checkout's dxvk.conf path (player_env.sh names it by its own
# directory), as each report field holding it exactly and each DXVK log line holding it
# reads after it is taken out; a path not the side's own still differs.
OWN_CONF = "<its own checkout's host/player/dxvk.conf>"
# dxvk-ab: DXVK's library as a side's own checkout's player_env.sh names it when nothing
# overrides it (SA2_DXVK_D3D9 from SA2_S5_INSTALL, from SA2_REPO, the checkout), and the file
# that path resolves to, as each report field holding one of them exactly reads
# (env.SA2_DXVK_D3D9, files.libdxvk_d3d9.path and .real_path); a path that is not the side's own
# install's still differs, and the library's bytes (files.libdxvk_d3d9.sha256) are compared as
# they are.
DXVK_LIBRARY = "private/s5-tools/install/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0"
OWN_LIBRARY = f"<its own checkout's {DXVK_LIBRARY}>"
SCHEMA = "sa2-s5-compare-hosts/v1"
SCOPES = ("r2a-clears", "r2-draws-full", "r2-draws-partial")
SCOPE_TEXT = {
    "r2a-clears": "R2a clear-only evidence. Draws are counted and skipped; this scope cannot establish R2 geometry.",
    "r2-draws-full": "R2 full original stream to its logical END. Every completed frame hash and every accepted draw outcome is checked. This is presentation evidence, not G5 closure.",
    "r2-draws-partial": "R2 partial original boot stream stopped by --max-frames. Offline complete means finalized. This does not establish full boot, original END or G5 acceptance.",
}
R2_CONTROLS = (
    {"name": "geometry-noop", "faults": "geometry-noop", "check": "e", "want": "fail",
     "reason": "geometry", "why": "suppressing all mapped geometry must change completed pixels on a geometry-bearing frame"},
    {"name": "count-draw", "faults": "count-draw", "check": "c", "want": "fail", "exercised_by_draws": True,
     "reason": "uncounted", "why": "omitting exactly the first accepted draw from the account must fail accounting"},
)
# The binaries a comparison's runs are made by (compare.json inputs.files; `check` refuses a
# build whose binaries are no longer these; an --also run must have run the same).
BINARIES = ("sa2_player", "sa2_player_gpu", "sa2_frame_census")
# v2: inputs.files and build_records as the two binaries have them (sa2_player and
# sa2_player_gpu, four records); its id is the caller's (--id), never a default.
RECEIPT_SCHEMA = "sa2-s5-host2/v2"
# The build's records (host/player/build_record.py): each player's exact record and each
# build script's group record of its other binaries (host/player/build.sh: sa2_player.json,
# player_tools.json; host/d3d9gpu/build.sh: sa2_player_gpu.json, d3d9gpu_tools.json), each
# naming the binaries it wrote and the sources it read, no binary named twice. A build is
# run only when they match the tree the comparison pins (the review's finding 1: a receipt
# must not pin sources that did not build the binaries that ran).
BUILD_RECORDS = ("sa2_player.json", "sa2_player_gpu.json", "player_tools.json", "d3d9gpu_tools.json")
# The pipeline's files some build record must name (what the builds compile from and read).
RECORDED = ("host/d3d9gpu/", "host/player/", "host/sa2_stream.h", "host/dxvk_smoke.cpp", "tools/pins.json")
# FNV-1a 64 (host/player/player.cpp frame_hash): an all-black w x h back buffer's
# bgrx_hash is the offset basis times the prime w*h times.
FNV_BASIS, FNV_PRIME = 0xcbf29ce484222325, 0x100000001b3


# ------------------------------------------------------------------ small helpers
def sha256(path: Path) -> str:
    return play_report.sha256_file(Path(path))


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def rel(p: Path | str) -> str:
    p = Path(p).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def read_times(path: Path) -> list[dict]:
    """A player times file's rows, as strings by column name; [] when absent."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    if not lines:
        return []
    names = lines[0].split("\t")
    return [dict(zip(names, line.split("\t"))) for line in lines[1:] if line.strip()]


def get(d: dict | None, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def has(d: dict | None, dotted: str) -> bool:
    """Whether the dotted key is in `d` at all (get() reads an absent key as None)."""
    *head, last = dotted.split(".")
    cur = get(d, ".".join(head)) if head else d
    return isinstance(cur, dict) and last in cur


def expand_runs(runs: list) -> dict[int, int]:
    """[[first, last, n], ...] as {frame: n}."""
    out = {}
    for first, last, n in runs or []:
        for f in range(int(first), int(last) + 1):
            out[f] = int(n)
    return out


def ranges(frames: list[int]) -> list[list[int]]:
    """[1, 2, 3, 7] as [[1, 3], [7, 7]]."""
    out: list[list[int]] = []
    for f in sorted(frames):
        if out and out[-1][1] + 1 == f:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def black_hash(w: int, h: int) -> str:
    """The bgrx_hash of a w x h back buffer whose every pixel is black, as the times file
    writes it."""
    return f"0x{(FNV_BASIS * pow(FNV_PRIME, int(w) * int(h), 1 << 64)) % (1 << 64):016x}"


def normalise_version(v: str) -> str:
    """'595.91.07' as '595.91.7': each dotted part without leading zeros."""
    return ".".join(str(int(p)) if p.isdigit() else p for p in v.split("."))


def dxvk_device(log_dir: Path, name: str | None) -> dict:
    """The device DXVK created, from its log: the name after "Creating device:" and the
    driver line under it (the adapter's Driver field is DXVK's placeholder)."""
    files = [Path(log_dir) / name] if name else sorted(Path(log_dir).glob("*_d3d9.log"))
    out = {"device_name": None, "driver": None, "found_devices": []}
    for f in files:
        if not f.is_file():
            continue
        creating = False
        for raw in f.read_text(errors="replace").splitlines():
            body = raw[len("info:  "):] if raw.startswith("info:  ") else None
            if body is None:
                continue
            if body.startswith("Found device: "):
                out["found_devices"].append(body[len("Found device: "):])
            if body.startswith("Creating device:"):
                creating = True
                continue
            if creating and out["device_name"] is None and not body.startswith(" ") and body.endswith(":"):
                out["device_name"] = body[:-1]
                continue
            if creating and out["driver"] is None and body.strip().startswith("Driver") and ":" in body:
                out["driver"] = body.split(":", 1)[1].strip()
        break
    return out


# ------------------------------------------------------------------ running
def refuse(path: Path) -> str | None:
    return play_report.refuse_output(path)


def run_census(build: Path, stream: Path, stage: int, out_dir: Path) -> dict:
    out = out_dir / "census.json"
    cmd = [str(build / "sa2_frame_census"), str(stream), "--stage", str(stage), "--out", str(out),
           "--tsv", str(out_dir / "census.tsv")]
    p = subprocess.run(cmd, capture_output=True, text=True)
    (out_dir / "census.log").write_text(p.stdout + p.stderr)
    if p.returncode != 0:
        raise RuntimeError(f"the census failed ({p.returncode}): {p.stderr.strip()[:300]}")
    return json.loads(out.read_text())


def run_player(build: Path, stream: Path, stage: int, run_dir: Path, *, sdlgpu: bool, faults: str = "",
               hidden: bool = False, allow_notes: bool = False, checkout: Path | None = None, max_frames: int = 0,
               expect: Path | None = None) -> dict:
    """One player run into run_dir, under host/player/player_env.sh: the DXVK host's
    (`sdlgpu` false) through host/player/run_player.sh, which runs the build's sa2_player
    (`checkout`'s run_player.sh and player_env.sh: this tree's unless named, dxvk-ab's
    before its clone's); the second host's, the build's sa2_player_gpu. The report, the
    times file (frames deleted once checked), the player's output, play_report.py's verdict,
    and for the second host the device's report. DXVK's log goes to run_dir/dxvk-log, a
    directory of its own (the player removes an earlier run's log where it writes its own).
    With `expect`, the player checks the pixels that file lists (--expect, the format
    host/player/synth_stream.cpp writes): tools/s5/m7_synthetic.py's authored streams."""
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir = run_dir / "dxvk-log"
    log_dir.mkdir(exist_ok=True)
    frames = run_dir / "frames.y4m"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_D3D9GPU")}
    # One SA2_PLAY_OUT beside every run of a comparison, so the reports' environments agree.
    env.update({"OUT": str(build), "SA2_PLAY_OUT": str(run_dir.parent / "play-out"), "SA2_DXVK_LOG_PATH": str(log_dir),
                "SDL_AUDIO_DRIVER": "dummy"})
    args = [str(stream), "--stage", str(stage), "--frames-out", str(frames), "--report", str(run_dir / "report.json")]
    if max_frames:
        args += ["--max-frames", str(max_frames)]
    if allow_notes:
        args.append("--allow-notes")
    if hidden:
        args.append("--hidden")
    if expect is not None:
        args += ["--expect", str(expect)]
    cmd = ["bash", str(RUN_PLAYER if checkout is None else Path(checkout) / RUNTIME[0])]
    if sdlgpu:
        env["SA2_D3D9GPU_REPORT"] = str(run_dir / "d3d9gpu.json")
        if faults:
            env["SA2_D3D9GPU_FAULT"] = faults
        cmd = ["bash", "-c", 'source "$0" && exec "$@"', str(PLAYER_ENV), str(build / PLAYERS["sdlgpu"])]
    with (run_dir / "player.out").open("w") as out, (run_dir / "player.err").open("w") as err:
        p = subprocess.run([*cmd, *args], env=env, stdout=out, stderr=err, timeout=PLAYER_TIMEOUT_S)
    (run_dir / "exit").write_text(f"{p.returncode}\n")
    check_args = ["--report", str(run_dir / "report.json"), "--exit", str(p.returncode), "--frames-out", str(frames),
                  "--out", str(run_dir / "check.json")]
    if not sdlgpu:
        check_args += ["--dxvk-log", str(log_dir)]
    # Which play_report.py wrote check.json (`check` refuses one another wrote): hashed
    # before and after it runs, and named as changed when the two differ.
    by = sha256(ROOT / PLAY_REPORT)
    c = subprocess.run([str(PY), str(ROOT / PLAY_REPORT), "check", *check_args], capture_output=True, text=True)
    (run_dir / "check.txt").write_text(c.stdout + c.stderr)
    (run_dir / CHECKED_BY).write_text((by if sha256(ROOT / PLAY_REPORT) == by else "changed while it ran") + "\n")
    if frames.exists():
        frames.unlink()
    return load_run(run_dir)


def load_run(run_dir: Path) -> dict:
    report = read_json(run_dir / "report.json") or {}
    exit_text = (run_dir / "exit").read_text().strip() if (run_dir / "exit").exists() else ""
    log = report.get("dxvk") or {}
    run = {
        "dir": rel(run_dir),
        "report": report,
        "check": read_json(run_dir / "check.json") or {},
        "checked_by": (run_dir / CHECKED_BY).read_text().strip() if (run_dir / CHECKED_BY).is_file() else None,
        "exit": int(exit_text) if exit_text.lstrip("-").isdigit() else None,
        "times": read_times(run_dir / "frames.y4m.times.tsv"),
        "device": read_json(run_dir / "d3d9gpu.json"),
        "log": dxvk_device(run_dir / "dxvk-log", Path(log["log"]).name if log.get("log") else None),
    }
    return run


def run_matrix(build: Path, out_dir: Path, hidden: bool = False) -> dict:
    """The clear matrix (check i) into out_dir/matrix, as host/d3d9gpu/test_d3d9gpu.sh step 2
    runs it: d3d9gpu_clear_matrix through DXVK's library (the one player_env.sh names, as
    the DXVK run's) and sa2_player_gpu --clear-matrix on SDL_gpu (the second host's player
    itself), each under player_env.sh, and test_d3d9gpu --clear-matrix on the CPU model.
    Each host's lines, stderr and exit status, DXVK's library and the binaries that ran are
    left for `check` to read again."""
    d = out_dir / "matrix"
    (d / "dxvk-log").mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_D3D9GPU")}
    env.update({"OUT": str(build), "SA2_PLAY_OUT": str(out_dir / "play-out"), "SA2_DXVK_LOG_PATH": str(d / "dxvk-log"),
                "SDL_AUDIO_DRIVER": "dummy"})
    hide = ["--hidden"] if hidden else []
    matrix = str(build / "d3d9gpu_clear_matrix")
    cmds = {
        "dxvk": (["bash", "-c", 'source "$0" && printf %s "$SA2_DXVK_D3D9" > "$1" && exec "$2" "$SA2_DXVK_D3D9" "${@:3}"',
                  str(PLAYER_ENV), str(d / "dxvk.library"), matrix, *hide], env),
        "sdlgpu": (["bash", "-c", 'source "$0" && exec "$@"', str(PLAYER_ENV), str(build / PLAYERS["sdlgpu"]),
                    "--clear-matrix", *hide], env | {"SA2_D3D9GPU_REPORT": str(d / "d3d9gpu.json")}),
        "cpu": ([str(build / "test_d3d9gpu"), "--clear-matrix"], env),
    }
    meta = {"binaries": {b: sha256(build / b) if (build / b).is_file() else None for b in MATRIX_BINARIES}, "exit": {}}
    for host, (cmd, e) in cmds.items():
        with (d / f"{host}.tsv").open("w") as out, (d / f"{host}.err").open("w") as err:
            try:
                meta["exit"][host] = subprocess.run(cmd, env=e, stdout=out, stderr=err, timeout=MATRIX_TIMEOUT_S).returncode
            except (OSError, subprocess.TimeoutExpired) as exc:
                meta["exit"][host] = None
                err.write(f"compare_hosts.py: {exc}\n")
    lib = Path((d / "dxvk.library").read_text()) if (d / "dxvk.library").is_file() else None
    meta["dxvk_library"] = {"path": str(lib) if lib else None, "sha256": sha256(lib) if lib and lib.is_file() else None}
    (d / "run.json").write_text(json.dumps(meta, indent=1) + "\n")
    return load_matrix(out_dir)


def load_matrix(out_dir: Path) -> dict | None:
    """What run_matrix left in out_dir/matrix, or None when the run ran no matrix."""
    d = out_dir / "matrix"
    if not d.is_dir():
        return None
    meta = read_json(d / "run.json") or {}
    tsv = {h: d / f"{h}.tsv" for h in MATRIX_HOSTS}
    return {"dir": rel(d), "exit": meta.get("exit") or {}, "binaries": meta.get("binaries") or {},
            "dxvk_library": meta.get("dxvk_library") or {},
            "lines": {h: p.read_text().splitlines() if p.is_file() else [] for h, p in tsv.items()},
            "outputs": {h: sha256(p) for h, p in tsv.items() if p.is_file()},
            "device": read_json(d / "d3d9gpu.json"), "log": dxvk_device(d / "dxvk-log", None)}


# ------------------------------------------------------------------ checks
def skipped_draws(census: dict) -> int:
    """The census's draws the device would skip (no shader route): the ones a fault can drop."""
    return sum(n for by in (get(census, "draws.skipped_no_shader_route") or {}).values() for n in by.values())


def result(name: str, failures: list[str], detail: dict | None = None) -> dict:
    return {"check": name, "title": CHECK_TITLES[name], "pass": not failures, "failures": failures,
            "detail": detail or {}}


def stream_account(stream: Path, *, max_frames: int = 0) -> dict:
    """Read original records only. This does not write a prefix or synthesize records.

    The existing decoder supplies framing and blobs. This independent account supplies
    declaration extents, accepted draw classes, 64-byte UP arena layout and consumed
    records. A bound ends after the original FRAME that makes the selected Present.
    """
    if type(max_frames) is not int or max_frames < 0:
        raise ValueError("max_frames must be a nonnegative exact integer")
    counts = {name: 0 for name in ("call", "d3dx", "frame", "note", "end", "unknown", "seq_gaps", "after_end")}
    kinds = {k: {} for k in ("skipped_no_shader_route", "refused", "empty")}
    frames, frame_draws, frame_accepted, total = 0, 0, 0, 0
    arena = padding = arena_sum = pad_sum = peak = vertex_bytes = index_bytes = uploaded = up_draws = 0
    declarations, bound, device, clear_calls = {}, None, False, 0
    per_frame, accepted_per_frame, first_accepted = {}, {}, None
    sizes = (4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8)
    primitive_names = {1: "POINTLIST", 2: "LINELIST", 3: "LINESTRIP", 4: "TRIANGLELIST", 5: "TRIANGLESTRIP", 6: "TRIANGLEFAN"}

    def vertices(primitive: int, count: int) -> int:
        return {1: count, 2: 2 * count, 3: count + 1, 4: 3 * count, 5: count + 2, 6: count + 2}.get(primitive, 0)

    with stream.open("rb") as file, mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
        head, off = stream_dump.read_header(data)
        # The synthetic writer uses names.h's short names; the recorder uses COM names.
        interface_names = {"Device": "IDirect3DDevice9", "SwapChain": "IDirect3DSwapChain9"}
        table = {key: (interface_names.get(interface, interface), method, argc)
                 for key, (interface, method, argc) in stream_dump.method_table(head).items()}
        if stream_dump.U32.unpack_from(data, 8)[0] == stream_dump.VERSION_AUDIO:
            counts["audio"] = 0
        seq_expected, pending, ended = 0, False, False
        while off < len(data):
            if ended or off + 12 > len(data):
                raise ValueError("original stream has a tail after END or a truncated record")
            kind, length, seq = stream_dump.HEAD.unpack_from(data, off)
            if seq != seq_expected or length < 12 or length % 4 or off + length > len(data):
                raise ValueError(f"original stream framing fails at record {seq_expected}")
            rec = stream_dump.decode(data, off, kind, length)
            if not rec["fills"]:
                raise ValueError(f"original stream record {seq} does not fill its frame")
            name = stream_dump.KIND_NAMES.get(kind)
            if name not in counts:
                raise ValueError(f"unknown original record kind {kind}")
            counts[name] += 1
            seq_expected += 1
            off += length
            if kind == stream_dump.END:
                ended = True
            if kind == stream_dump.FRAME:
                if not pending:
                    raise ValueError("FRAME has no preceding Present in the independent account")
                pending = False
                if max_frames and frames == max_frames:
                    break
            if kind != stream_dump.CALL:
                continue
            interface, method, argc = table.get((rec["type"], rec["slot"]), (None, None, None))
            args = rec["args"]
            if len(args) != argc:
                raise ValueError(f"record {seq} argc differs from original header")

            def blob(role, arg):
                b = next((b for b in rec["blobs"] if b[:2] == (role, arg)), None)
                return bytes(data[b[2]:b[2] + b[3]]) if b else None

            if interface == "IDirect3D9" and method == "CreateDevice" and not device:
                device = True
                continue
            if not device:
                continue
            if method == "Present" and interface in ("IDirect3DDevice9", "IDirect3DSwapChain9"):
                frames += 1
                per_frame[frames] = frame_draws
                accepted_per_frame[frames] = frame_accepted
                frame_draws = frame_accepted = 0
                arena_sum += arena
                pad_sum += padding
                peak = max(peak, arena)
                arena = padding = 0
                pending = True
                continue
            if interface != "IDirect3DDevice9":
                continue
            if method == "Clear":
                clear_calls += 1
            if method == "CreateVertexDeclaration":
                raw, out = blob(stream_dump.IN, 0), blob(stream_dump.OUT, 1)
                extent, complete = 0, False
                if raw and out and len(out) >= 4:
                    for start in range(0, len(raw) - 7, 8):
                        stream_no, offset, dtype, _, _, _ = struct.unpack_from("<HHBBBB", raw, start)
                        if stream_no == 255:
                            complete = True
                            break
                        if stream_no == 0:
                            if dtype >= len(sizes):
                                raise ValueError("unknown declaration type in UP extent account")
                            extent = max(extent, offset + sizes[dtype])
                    if complete:
                        declarations[struct.unpack_from("<I", out)[0]] = extent
            if method == "SetVertexDeclaration":
                bound = declarations.get(args[0]) if args[0] else None
            if method == "SetFVF" and args[0]:
                # FVF conversion remains unmapped in R2. Do not invent its layout.
                raise ValueError("nonzero FVF is outside the independently accounted mapped R2 domain")
            if method not in DRAW_METHODS:
                continue
            primitive = args[0]
            pcount = args[{"DrawPrimitive": 2, "DrawIndexedPrimitive": 5, "DrawPrimitiveUP": 1, "DrawIndexedPrimitiveUP": 3}[method]]
            stride = args[3] if method == "DrawPrimitiveUP" else args[7] if method == "DrawIndexedPrimitiveUP" else 1
            nvertices = vertices(primitive, pcount)
            index_size, nup, ibytes = 0, 0, 0
            if method == "DrawPrimitiveUP":
                raw = blob(stream_dump.IN, 2)
                if not nvertices or raw is None or len(raw) < nvertices * stride:
                    continue  # the shared player makes no call for this blob
                nup = nvertices
            if method == "DrawIndexedPrimitiveUP":
                index_size = 2 if args[5] == 101 else 4
                raw, indices = blob(stream_dump.IN, 6), blob(stream_dump.IN, 4)
                nup, ibytes = args[1] + args[2], nvertices * index_size
                if raw is None or len(raw) < nup * stride or indices is None or len(indices) < ibytes:
                    continue
            cls = "refused" if bound is None or not stride else "empty" if not pcount or (method in ("DrawIndexedPrimitive", "DrawIndexedPrimitiveUP") and not args[3 if method == "DrawIndexedPrimitive" else 2]) else "skipped_no_shader_route"
            prim = primitive_names.get(primitive, f"type {primitive}")
            by = kinds[cls].setdefault(method, {})
            by[prim] = by.get(prim, 0) + 1
            total += 1
            frame_draws += 1
            if cls != "skipped_no_shader_route":
                continue
            frame_accepted += 1
            if first_accepted is None:
                first_accepted = {"method": method, "primitive": prim, "frame": frames + 1, "seq": seq}
            if nup:
                data_bytes = nup * stride
                buffer_bytes = (nup - 1) * stride + max(bound, stride)
                if max(data_bytes, buffer_bytes, ibytes) > 0xffffffff:
                    raise ValueError("UP extent exceeds the mapped 32-bit allocation domain")
                aligned = (arena + 63) & ~63
                padding += aligned - arena + max(0, buffer_bytes - data_bytes)
                arena = aligned + buffer_bytes + ibytes
                vertex_bytes += data_bytes
                index_bytes += ibytes
                uploaded += buffer_bytes + ibytes
                up_draws += 1
        if max_frames:
            if frames != max_frames or pending or counts["end"] != 0:
                raise ValueError("bound did not stop after an original FRAME before END")
        elif not ended or counts["end"] != 1 or off != len(data):
            raise ValueError("full account did not consume the original logical END")
    return {"frames": frames, "draws": {"total": total, **kinds, "per_frame": runs_of(per_frame), "after_last_present": frame_draws},
            "consumed_records": counts, "consumed_bytes": off, "first_accepted": first_accepted,
            "accepted_per_frame": runs_of(accepted_per_frame), "accepted_after_last_present": frame_accepted,
            "up_vertex_bytes": vertex_bytes, "up_index_bytes": index_bytes, "up_arena_bytes": arena_sum,
            "up_pad_bytes": pad_sum, "up_frame_peak_bytes": peak, "up_draw_uploaded_bytes": uploaded, "up_draws": up_draws,
            "clears": {"host_calls": clear_calls}}


def bounded_census(census: dict, account: dict) -> dict:
    out = copy.deepcopy(census)
    out.update(account)
    frames = account["frames"]
    for key in ("draw_free_frames", "draw_free_frames_back_buffer_drawn_earlier", "draw_free_frames_never_colour_cleared"):
        out[key] = [f for f in census[key] if f <= frames]
    out["frames_made_by_clears_alone"] = len(out["draw_free_frames"]) - len(out["draw_free_frames_back_buffer_drawn_earlier"])
    return out


def geometry_scope_facts(doc: dict) -> list[str]:
    """Hold serialized scope facts again before a marker or receipt can claim geometry."""
    f = []
    scope, bound = doc.get("acceptance_scope"), doc.get("max_frames")
    partial = scope == "r2-draws-partial"
    if scope not in ("r2-draws-full", "r2-draws-partial") or type(bound) is not int or bound < 0 or partial != (bound > 0):
        return ["no explicit typed geometry scope and bound"]
    frames, consumed = get(doc, "census.frames"), get(doc, "census.consumed_bytes")
    size, stream_hash = get(doc, "stream.bytes"), get(doc, "stream.sha256")
    if type(doc.get("stage")) is not int or doc["stage"] != 3:
        f.append("geometry receipt requires stage 3")
    if not exact_count(frames) or frames == 0 or (partial and frames != bound):
        f.append("geometry receipt has no exact completed frame count matching its bound")
    if (not exact_count(consumed) or consumed == 0 or not exact_count(size) or size == 0
            or (consumed >= size if partial else consumed != size)):
        f.append("geometry receipt has no exact original consumed-byte account")
    if not isinstance(stream_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", stream_hash):
        f.append("geometry receipt has no original stream hash")
    if partial and stream_hash != BOOT_STREAM_SHA256:
        f.append("partial geometry receipt is not the registered original boot stream")
    records = get(doc, "census.consumed_records")
    keys = {"call", "d3dx", "frame", "note", "end", "unknown", "seq_gaps", "after_end"}
    if (not isinstance(records, dict) or set(records) not in (keys, keys | {"audio"})
            or not all(exact_count(v) for v in records.values())):
        f.append("geometry receipt has no exact original consumed-record account")
    else:
        if (records["end"] != (0 if partial else 1) or records["frame"] != frames
                or any(records[k] != 0 for k in ("unknown", "seq_gaps", "after_end"))):
            f.append("geometry receipt's original record account contradicts its scope")
    prov = get(doc, "inputs.provenance") or {}
    files = get(doc, "inputs.files") or {}
    pins = get(doc, "inputs.pipeline") or {}
    if not all(isinstance(v, dict) for v in (prov, files, pins)):
        return f + ["geometry receipt has malformed provenance, files or pipeline pins"]
    sha = lambda v: isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v) is not None
    if (prov.get("pass") is not True or prov.get("failures") != [] or not isinstance(prov.get("records"), dict)
            or set(prov["records"]) != set(BUILD_RECORDS) or not all(sha(v) for v in prov["records"].values())):
        f.append("geometry receipt lacks passing complete build provenance")
    binaries = prov.get("binaries") or {}
    if not isinstance(binaries, dict):
        return f + ["geometry receipt lacks recorded binary groups"]
    if set(binaries) != set(BUILD_RECORDS):
        f.append("geometry receipt lacks all recorded binary groups")
    for host, binary in PLAYERS.items():
        named = binaries.get(binary + ".json") if isinstance(binaries, dict) else None
        if not sha(files.get(binary)) or named != {binary: files.get(binary)}:
            f.append(f"{host}: geometry receipt player differs from its exact build record")
    if files.get("sa2_player") == files.get("sa2_player_gpu"):
        f.append("geometry receipt names one player file for both hosts")
    if not sha(files.get("sa2_frame_census")) or get(binaries.get("d3d9gpu_tools.json"), "sa2_frame_census") != files.get("sa2_frame_census"):
        f.append("geometry receipt census differs from its build record")
    if pins.get(SELF) != RAN_BY or get(doc, "inputs.ran_by") != RAN_BY:
        f.append("geometry receipt was not run and checked by its pinned current comparer")
    runs = doc.get("runs")
    if not isinstance(runs, dict) or set(runs) != {"dxvk", "sdlgpu"}:
        return f + ["geometry receipt has no two player summaries"]
    for host, run in runs.items():
        if get(run, "player.sha256") != files.get(PLAYERS[host]):
            f.append(f"{host}: serialized player file differs from the pinned executable")
        want = {"complete": True, "pass": True, "exit": 0, "exit_status": 0, "check_pass": True,
                "stage": 3, "presents": frames, "draws": get(doc, "census.draws"),
                "stop": "--max-frames" if partial else "end of stream", "max_frames": bound,
                "stream_records_read_to_end": not partial, "stream_sha256_after_stop": partial,
                "stream_sha256": stream_hash, "stream_bytes": consumed, "allow_truncated": False,
                "stats_only": False, "placeholder_binds": 0}
        if not isinstance(run, dict):
            f.append(f"{host}: missing player summary")
            continue
        for key, expected in want.items():
            if key not in run or type(run[key]) is not type(expected) or run[key] != expected:
                f.append(f"{host}: serialized {key} contradicts geometry scope")
        if not isinstance(run.get("records"), dict) or run["records"] != records or any(not exact_count(v) for v in run["records"].values()):
            f.append(f"{host}: serialized consumed records differ from the original account")
    return f


def acceptance_marker(doc: dict) -> str:
    if doc.get("verdict_with_controls") != "PASS":
        return "R2_FAIL"
    scope = doc.get("acceptance_scope")
    if scope == "r2a-clears":
        return "R2A_CLEARS_OK"
    if geometry_scope_facts(doc):
        return "R2_FAIL"
    checks = doc.get("checks") or []
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        return "R2_FAIL"
    names = [c.get("check") for c in checks]
    if len(names) != len(set(names)) or any(n not in CHECK_TITLES for n in names):
        return "R2_FAIL"
    if any(c.get("pass") is not True or c.get("failures") != [] for c in checks):
        return "R2_FAIL"
    by = {c.get("check"): c for c in checks}
    if scope not in SCOPES or not all(by.get(k, {}).get("pass") is True for k in "abcdefgi"):
        return "R2_FAIL"
    c, e = by["c"], by["e"]
    if not exact_count(get(c, "detail.encoded")) or get(c, "detail.encoded") == 0:
        return "R2_FAIL"
    if any(type(get(c, "detail." + key)) is not int or get(c, "detail." + key) != 0
           for key in ("unmapped", "backend_failed", "suppressed")):
        return "R2_FAIL"
    if get(e, "detail.hash_scope") != "all completed frames":
        return "R2_FAIL"
    controls = get(doc, "controls.results") or []
    if (get(doc, "controls.run") is not True or not isinstance(controls, list)
            or not all(isinstance(c, dict) for c in controls) or len(controls) != len(R2_CONTROLS)):
        return "R2_FAIL"
    for expected in R2_CONTROLS:
        matching = [r for r in controls if r.get("name") == expected["name"]]
        if len(matching) != 1:
            return "R2_FAIL"
        got = matching[0]
        if (got.get("as_required") is not True or got.get("exercised") is not True or got.get("got") != "fail"
                or any(got.get(k) != expected[k] for k in ("faults", "check", "want"))):
            return "R2_FAIL"
    frames = get(doc, "census.frames")
    if not exact_count(frames) or frames == 0 or get(e, "detail.comparable") != frames:
        return "R2_FAIL"
    boot = get(doc, "stream.sha256") == BOOT_STREAM_SHA256
    if scope == "r2-draws-partial":
        if not boot or type(doc.get("max_frames")) is not int or doc["max_frames"] != frames:
            return "R2_FAIL"
        return "R2_PARTIAL_GEOMETRY_OK"
    if type(doc.get("max_frames")) is not int or doc["max_frames"] != 0:
        return "R2_FAIL"
    if boot:
        reference = by.get("h", {})
        if (reference.get("pass") is not True or get(reference, "detail.sha256") != BOOT_REFERENCE_SHA256
                or type(get(reference, "detail.rows")) is not int or get(reference, "detail.rows") != frames):
            return "R2_FAIL"
    if get(doc, "stream.sha256") == METAL_HARBOR["stream_sha256"]:
        reference = by.get("h", {})
        if metalharbor_facts(doc.get("stage"), frames, reference.get("detail") or {},
                             get(reference, "detail.player_settings"), get(by["g"], "detail.sdl_gpu")):
            return "R2_FAIL"
        return METAL_HARBOR["marker"]
    return "R2_DRAW_OK" if boot else "R2_STREAM_GEOMETRY_OK"


def restore_r2_draws(dev: dict, census: dict, dropped) -> tuple[dict | None, list[str]]:
    first = census.get("first_accepted")
    if dropped != 1 or type(dropped) is not int or not isinstance(first, dict):
        return None, ["c: no exact independently identified first accepted draw for count-draw"]
    out = copy.deepcopy(dev)
    method, prim, fr = first["method"], first["primitive"], first["frame"]
    for key in ("total", "accepted", "drawn"):
        if not exact_count(out.get(key)):
            return None, [f"c: missing exact draws.{key} for count-draw"]
        out[key] += 1
    mapped = out.get("encoded")
    if not isinstance(mapped, dict):
        return None, ["c: no encoded outcome map for count-draw"]
    row = mapped.setdefault(method, {})
    if not exact_count(row.get(prim, 0)):
        return None, ["c: invalid omitted encoded cell for count-draw"]
    row[prim] = row.get(prim, 0) + 1
    why = []
    frames = frame_counts(out.get("per_frame"), census.get("frames"), "count-draw per_frame", why)
    if why:
        return None, why
    if fr <= census["frames"]:
        frames[fr] += 1
    elif fr == census["frames"] + 1 and exact_count(out.get("after_last_present")):
        out["after_last_present"] += 1
    else:
        return None, ["c: first accepted draw frame is not in the stream account"]
    out["per_frame"] = runs_of(frames)
    return out, []


def scope_of(ev: dict) -> str:
    return ev.get("scope", "r2-draws-full")


def geometry_scope(ev: dict) -> bool:
    return scope_of(ev) in ("r2-draws-full", "r2-draws-partial")


def controls_for(scope: str) -> tuple:
    return CONTROLS if scope == "r2a-clears" else R2_CONTROLS


def exact_count(value) -> bool:
    return type(value) is int and 0 <= value <= 0xffffffffffffffff


def count_map(value, label: str, failures: list[str]) -> dict:
    if not isinstance(value, dict):
        failures.append(f"{label} is absent or not a method/primitive count map")
        return {}
    out = {}
    for method, primitives in value.items():
        if method not in DRAW_METHODS or not isinstance(primitives, dict) or not primitives:
            failures.append(f"{label}: invalid method or primitive map {method!r}")
            continue
        for primitive, n in primitives.items():
            if not isinstance(primitive, str) or not primitive or not exact_count(n) or n == 0:
                failures.append(f"{label}.{method}.{primitive}: {n!r} is not a positive exact count")
            else:
                out[(method, primitive)] = n
    return out


def frame_counts(value, frames, label: str, failures: list[str]) -> dict:
    if not exact_count(frames) or not isinstance(value, list):
        failures.append(f"{label}: missing frame count or runs")
        return {}
    out, next_frame = {}, 1
    for row in value:
        if not isinstance(row, list) or len(row) != 3 or not all(exact_count(n) for n in row):
            failures.append(f"{label}: invalid exact run {row!r}")
            return {}
        first, last, n = row
        if first != next_frame or last < first or last > frames:
            failures.append(f"{label}: noncontiguous or out-of-range run {row!r}")
            return {}
        out.update({i: n for i in range(first, last + 1)})
        next_frame = last + 1
    if next_frame != frames + 1:
        failures.append(f"{label}: runs end at frame {next_frame - 1}, expected {frames}")
    return out


def r2_draw_account(ev: dict) -> tuple[list[str], dict]:
    f = []
    cd = ev["census"].get("draws") or {}
    dd = get(ev["sdl"]["device"], "draws") or {}
    allowed = {"drawn", "total", "fault_uncounted", "accepted", "unmapped_count", "backend_failed_count",
               "suppressed_count", "encoded", "unmapped", "backend_failed", "fault_suppressed", "refused",
               "empty", "failures", "per_frame", "after_last_present"}
    if set(dd) != allowed:
        f.append(f"draws schema fields differ: missing {sorted(allowed - set(dd))}, unknown {sorted(set(dd) - allowed)}")
    expected = count_map(cd.get("skipped_no_shader_route"), "census accepted", f)
    maps = {k: count_map(dd.get(k), "draws." + k, f) for k in
            ("encoded", "unmapped", "backend_failed", "fault_suppressed", "refused", "empty")}
    for key in ("drawn", "total", "fault_uncounted", "accepted", "unmapped_count", "backend_failed_count", "suppressed_count"):
        if not exact_count(dd.get(key)):
            f.append(f"draws.{key}: {dd.get(key)!r} is absent or not an exact count")
    for key, cls in (("drawn", "encoded"), ("unmapped_count", "unmapped"),
                     ("backend_failed_count", "backend_failed"), ("suppressed_count", "fault_suppressed")):
        if dd.get(key) != sum(maps[cls].values()):
            f.append(f"draws.{key} differs from the {cls} map sum")
    accepted = {}
    for cls in ("encoded", "unmapped", "backend_failed", "fault_suppressed"):
        for cell, n in maps[cls].items():
            accepted[cell] = accepted.get(cell, 0) + n
    if dd.get("accepted") != sum(accepted.values()):
        f.append("draws.accepted differs from the four accepted outcome maps")
    if accepted != expected:
        f.append(f"accepted draw outcomes differ: device {accepted}; census {expected}")
    classified = sum(sum(m.values()) for m in maps.values())
    census_total = sum(expected.values()) + sum(sum(count_map(cd.get(k), "census " + k, f).values())
                                                 for k in ("refused", "empty"))
    if not exact_count(cd.get("total")) or cd.get("total") != census_total:
        f.append("census total differs from its accepted, refused and empty maps")
    if dd.get("total") != classified:
        f.append("draws.total differs from all six outcome maps")
    failures = dd.get("failures")
    if not isinstance(failures, list) or len(failures) > 40 or any(not isinstance(x, str) or not x for x in failures):
        f.append("draws.failures is absent or not at most forty nonempty strings")
    elif failures:
        f.append(f"draws.failures: {failures[:3]}")
    for key in ("unmapped_count", "backend_failed_count"):
        if dd.get(key) != 0:
            f.append(f"draws.{key} must be zero for R2 geometry acceptance")
    if geometry_scope(ev) and not expected:
        f.append("geometry scope has no independently accepted draw; use explicit r2a-clears for clear-only evidence")
    suppression = ev.get("fault") == "geometry-noop"
    if suppression:
        if maps["fault_suppressed"] != expected or dd.get("drawn") != 0:
            f.append("geometry-noop must suppress every accepted mapped draw and encode none")
    else:
        if dd.get("suppressed_count") != 0:
            f.append("draws.suppressed_count must be zero for R2 geometry acceptance")
        if expected and (not exact_count(dd.get("drawn")) or dd["drawn"] == 0):
            f.append("the stream has accepted geometry but no draw was encoded")
    return f, {"accepted": dd.get("accepted"), "encoded": dd.get("drawn"),
               "unmapped": dd.get("unmapped_count"), "backend_failed": dd.get("backend_failed_count"),
               "suppressed": dd.get("suppressed_count")}


def check_a(ev: dict) -> dict:
    f: list[str] = []
    census = ev["census"]
    if scope_of(ev) not in SCOPES:
        f.append(f"unknown comparison scope {scope_of(ev)!r}")
    if geometry_scope(ev) and census.get("stage") != 3:
        f.append("R2 geometry requires stage 3 with original shaders and assets")
    if scope_of(ev) == "r2-draws-partial":
        if not exact_count(ev.get("max_frames")) or ev["max_frames"] == 0 or ev["max_frames"] != census.get("frames"):
            f.append("partial scope requires a positive exact --max-frames bound equal to completed frames")
        if census.get("stream_sha256") != BOOT_STREAM_SHA256:
            f.append("partial geometry evidence requires the registered original boot stream")
    for host, run in (("dxvk", ev["dxvk"]), ("sdlgpu", ev["sdl"])):
        rep = run["report"]
        if run["exit"] != 0:
            f.append(f"{host}: the player exited {run['exit']}")
        if run["check"].get("pass") is not True:
            f.append(f"{host}: tools/s5/play_report.py check: {run['check'].get('failures', ['no verdict'])[:3]}")
        want = {"complete": True, "pass": True, "exit_status": 0, "stop": "end of stream", "errors": 0,
                "readback_failures": 0, "present_failures": 0, "records.end": 1, "calls.not_handled": 0,
                "host_failures.count": 0, "host_identity.d3d9_host": host, "host_identity.sdl_file_verified": True,
                "presents": census.get("frames"), "stage": census.get("stage"),
                "stream_sha256": census.get("stream_sha256")}
        if geometry_scope(ev):
            partial = scope_of(ev) == "r2-draws-partial"
            want.update({"stop": "--max-frames" if partial else "end of stream", "records.end": 0 if partial else 1,
                         "stream_records_read_to_end": not partial, "stream_sha256_after_stop": partial,
                         "options.max_frames": ev.get("max_frames") if partial else 0,
                         "options.allow_truncated": False, "options.stats_only": False})
            kinds = census.get("consumed_records")
            if not isinstance(kinds, dict):
                f.append(f"{host}: no independent consumed-record account")
            else:
                for kind, n in kinds.items():
                    want["records." + kind] = n
            want["stream_bytes"] = census.get("consumed_bytes")
        # Below stage 3 every texture is the player's 1x1 placeholder by design (--stage);
        # at stage 3 none may be (check d holds both hosts' counts equal at every stage).
        if census.get("stage") == 3:
            want["textures.placeholder_binds"] = 0
        # Each host pins and verifies its own d3d9 and nothing of the other's: the second
        # host's player is its own renderer (its files.player), and it pins, verifies and
        # expects no DXVK library and no dxvk.conf (check_s5_exit.py renderer_loaded's "pins
        # no DXVK library and verified none").
        if host == "sdlgpu":
            want.update({"host_identity.dxvk_file_verified": False, "host_identity.config_file_verified": False,
                         "host_identity.expected_dxvk_sha256": None, "host_identity.expected_config_sha256": None,
                         "files.libdxvk_d3d9": None, "files.dxvk_conf": None})
        else:
            want.update({"host_identity.dxvk_file_verified": True, "host_identity.config_file_verified": True})
        for key, value in want.items():
            if not has(rep, key):
                # absent is not null: a report that dropped a field is not one that wrote it
                f.append(f"{host}: {key} is absent, not {value!r}")
            elif get(rep, key) != value or type(get(rep, key)) is not type(value):
                f.append(f"{host}: {key} is {get(rep, key)!r}, not {value!r}")
        # A field of the player that loaded the second host's library: not a report of the
        # two binaries (the key's presence fails, whatever its value).
        for key in LIBRARY_FIELDS:
            if has(rep, key):
                f.append(f"{host}: the report has {key} ({get(rep, key)!r}), a field of the player that loaded the "
                         f"second host's library, not of {PLAYERS[host]}")
    # The second host's player writes the DXVK player's report fields (check_s5_exit.py's
    # report contract), its two DXVK pins null: the reports' keys at the top, under files
    # and under host_identity are one set (deeper, a report's keys follow what its run met).
    reps = {"dxvk": ev["dxvk"]["report"], "sdlgpu": ev["sdl"]["report"]}
    for where in REPORT_KEY_LEVELS:
        keys = {h: set(d) if isinstance(d := (get(r, where) if where else r), dict) else set()
                for h, r in reps.items()}
        if keys["dxvk"] != keys["sdlgpu"]:
            f.append(f"the reports' keys {'under ' + where if where else 'at the top'} differ: dxvk alone "
                     f"{sorted(keys['dxvk'] - keys['sdlgpu'])}, sdlgpu alone {sorted(keys['sdlgpu'] - keys['dxvk'])} "
                     f"(the second host's player writes the DXVK player's report fields)")
    # Two hosts are two binaries: one file on both is one player twice (renderer_loaded
    # cannot tell; provenance holds each to its own record).
    players = {h: get(ev[k]["report"], "files.player.sha256") for h, k in (("dxvk", "dxvk"), ("sdlgpu", "sdl"))}
    if players["dxvk"] == players["sdlgpu"]:
        f.append(f"both hosts ran one player file ({players['dxvk']}): the second host is its own binary")
    return result("a", f)


def check_b(ev: dict) -> dict:
    f: list[str] = []
    dev, rep = ev["sdl"]["device"], ev["sdl"]["report"]
    if not isinstance(dev, dict):
        return result("b", ["the device wrote no report (SA2_D3D9GPU_REPORT)"])
    # A control's run (control_outcome) is under its named fault, and count-draw leaves the
    # first skipped draw uncounted when the stream has one; every other run is under none.
    fault = ev.get("fault")
    uncounted = int("count-draw" in (fault or "").split(",") and skipped_draws(ev["census"]) > 0)
    want = {"schema": "sa2-d3d9gpu-report/v1", "complete": True, "failure": None, "fault": fault,
            "fault_error": None, "draws.drawn": 0, "not_implemented.count": 0, "present.failures": 0,
            "clears.past_edge": 0, "fpu.unchanged": True, "sdl_gpu.debug_mode": False,
            "present.presents": rep.get("presents"), "draws.fault_uncounted": uncounted}
    if geometry_scope(ev):
        del want["draws.drawn"]
    for key, value in want.items():
        if not has(dev, key) or get(dev, key) != value or type(get(dev, key)) is not type(value):
            f.append(f"{key} is {get(dev, key)!r}, not {value!r}")
    if dev.get("objects", {}).get("live_at_destroy"):
        f.append(f"objects left alive at the device's end: {dev['objects']['live_at_destroy']}")
    # The device's own account of the file its code ran from is the player that ran: the
    # renderer is the player binary (its files.player, which provenance holds to the build).
    lib, path = get(dev, "library.sha256"), get(dev, "library.path")
    pinned, real = get(rep, "files.player.sha256"), get(rep, "files.player.real_path")
    if not lib or lib != pinned or path != real:
        f.append(f"the device's library {lib} ({path}) is not the player that ran ({pinned}, {real})")
    if not ev.get("hidden") and get(dev, "present.mode") == "window" and get(dev, "present.frames_without_swapchain_texture"):
        f.append(f"{get(dev, 'present.frames_without_swapchain_texture')} Present(s) found no swapchain texture "
                 f"in a window not hidden")
    rows = {(m.get("type"), m.get("method")): int(m.get("failed", 0)) for m in rep.get("methods", [])}
    for m in dev.get("methods", []):
        if m.get("failed") and m["failed"] > rows.get((m["type"], m["method"]), 0):
            f.append(f"{m['type']}::{m['method']} failed {m['failed']} time(s) in the device; the player's row "
                     f"shows {rows.get((m['type'], m['method']), 0)}")
    detail = {"sdl_gpu": dev.get("sdl_gpu"), "present": dev.get("present"), "fpu": dev.get("fpu"),
              "clears": dev.get("clears"), "uploads": dev.get("uploads"), "readbacks": dev.get("readbacks")}
    return result("b", f, detail)


def check_c_legacy(ev: dict) -> dict:
    f: list[str] = []
    census, dev = ev["census"], ev["sdl"]["device"] or {}
    cd, dd = census.get("draws", {}), dev.get("draws", {})
    if "accepted" in dd:
        # A new report is usable in historical clear scope only with no accepted geometry.
        account_failures, _ = r2_draw_account(ev)
        f.extend(account_failures)
        if dd.get("accepted") != 0:
            f.append("r2a-clears cannot accept a new report with accepted geometry")
        dd = dict(dd, skipped_no_shader_route={})
    totals = {"census": cd.get("total"), "dxvk report": ev["dxvk"]["report"].get("draws"),
              "sdlgpu report": ev["sdl"]["report"].get("draws"), "device": dd.get("total")}
    if len(set(totals.values())) != 1 or None in totals.values():
        f.append(f"draw totals differ: {totals}")
    for cls in ("skipped_no_shader_route", "refused", "empty"):
        if dd.get(cls) != cd.get(cls):
            f.append(f"{cls}: device {dd.get(cls)}; census {cd.get(cls)}")
    a, b = expand_runs(dd.get("per_frame")), expand_runs(cd.get("per_frame"))
    if a != b:
        first = next((k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)), None)
        f.append(f"per-frame draws differ from frame {first}: device {a.get(first)}, census {b.get(first)}")
    if dd.get("after_last_present") != cd.get("after_last_present"):
        f.append(f"draws after the last Present: device {dd.get('after_last_present')}, census "
                 f"{cd.get('after_last_present')}")
    refused = sum(n for by in (dd.get("refused") or {}).values() for n in by.values())
    for host, run in (("dxvk", ev["dxvk"]), ("sdlgpu", ev["sdl"])):
        failed = sum(int(m.get("failed", 0)) for m in run["report"].get("methods", [])
                     if m.get("type") == "Device" and m.get("method") in DRAW_METHODS)
        if failed != refused:
            f.append(f"{host}: the draw rows' failures ({failed}) are not the device's refused draws ({refused})")
    for key in ("up_vertex_bytes", "up_index_bytes"):
        if get(dev, "uploads." + key) != census.get(key):
            f.append(f"UP arena {key}: device {get(dev, 'uploads.' + key)}, census {census.get(key)}")
    return result("c", f, {"totals": totals, "refused": refused})


def check_c(ev: dict) -> dict:
    if not geometry_scope(ev):
        return check_c_legacy(ev)
    f, detail = r2_draw_account(ev)
    census, dev = ev["census"], ev["sdl"]["device"] or {}
    cd, dd = census.get("draws") or {}, dev.get("draws") or {}
    totals = {"census": cd.get("total"), "dxvk report": ev["dxvk"]["report"].get("draws"),
              "sdlgpu report": ev["sdl"]["report"].get("draws"), "device": dd.get("total")}
    if not all(exact_count(n) for n in totals.values()) or len(set(totals.values())) != 1:
        f.append(f"draw totals differ or are not exact: {totals}")
    for cls in ("refused", "empty"):
        a = count_map(cd.get(cls), "census " + cls, f)
        b = count_map(dd.get(cls), "draws." + cls, f)
        if a != b:
            f.append(f"{cls}: device {b}; census {a}")
    a = frame_counts(dd.get("per_frame"), census.get("frames"), "device per_frame", f)
    b = frame_counts(cd.get("per_frame"), census.get("frames"), "census per_frame", f)
    if a != b:
        f.append("per-frame draws differ from the census")
    accepted_frames = frame_counts(census.get("accepted_per_frame"), census.get("frames"), "census accepted_per_frame", f)
    accepted_after = census.get("accepted_after_last_present")
    if not exact_count(accepted_after):
        f.append("census accepted_after_last_present is absent or not an exact count")
    elif sum(accepted_frames.values()) + accepted_after != sum(count_map(cd.get("skipped_no_shader_route"), "census accepted", f).values()):
        f.append("census accepted frame account differs from accepted method/primitive totals")
    if any(n > b.get(fr, 0) for fr, n in accepted_frames.items()):
        f.append("census accepted frame count exceeds all calls in that frame")
    for value, label in ((dd.get("after_last_present"), "device"), (cd.get("after_last_present"), "census")):
        if not exact_count(value):
            f.append(f"{label} after_last_present is absent or not an exact count")
    if dd.get("after_last_present") != cd.get("after_last_present"):
        f.append("draws after the last Present differ")
    if exact_count(dd.get("after_last_present")) and dd.get("total") != sum(a.values()) + dd["after_last_present"]:
        f.append("draws.total differs from the per-frame and after-last-Present account")
    refused = count_map(dd.get("refused"), "draws.refused", f)
    for host, run in (("dxvk", ev["dxvk"]), ("sdlgpu", ev["sdl"])):
        rows = {m.get("method"): m for m in run["report"].get("methods", []) if m.get("type") == "Device"
                and m.get("method") in DRAW_METHODS}
        for method in DRAW_METHODS:
            wanted = sum(n for (m, _), n in refused.items() if m == method)
            got = rows.get(method, {}).get("failed", 0)
            if not exact_count(got) or got != wanted:
                f.append(f"{host}: {method} failure row {got!r} differs from refused count {wanted}")
    for key in ("up_vertex_bytes", "up_index_bytes", "up_arena_bytes", "up_pad_bytes", "up_frame_peak_bytes"):
        want, got = census.get(key), get(dev, "uploads." + key)
        if not exact_count(want) or not exact_count(got) or got != want:
            f.append(f"UP arena {key}: device {got!r}, census {want!r}, exact required")
    for key, source in (("up_arena_uploads", "up_draws"), ("up_draw_uploaded_bytes", "up_draw_uploaded_bytes")):
        want = 0 if ev.get("fault") == "geometry-noop" else census.get(source)
        got = get(dev, "uploads." + key)
        if not exact_count(want) or not exact_count(got) or got != want:
            f.append(f"encoded UP {key}: device {got!r}, expected {want!r}, exact required")
    return result("c", f, detail | {"totals": totals, "refused": sum(refused.values())})


def check_d(ev: dict) -> dict:
    f: list[str] = []
    a, b = ev["dxvk"]["report"], ev["sdl"]["report"]
    ra = {(m.get("type"), m.get("method")): m for m in a.get("methods", [])}
    rb = {(m.get("type"), m.get("method")): m for m in b.get("methods", [])}
    differing = []                     # what differs, by row ("Type::Method") or field name
    for key in sorted(set(ra) | set(rb), key=str):
        if ra.get(key) != rb.get(key):
            differing.append(f"{key[0]}::{key[1]}")
            f.append(f"{key[0]}::{key[1]}: dxvk {ra.get(key)}; sdlgpu {rb.get(key)}")
    for key in ("calls", "textures", "allowed_host_failures", "targets", "host_failures", "not_handled_values",
                "errors", "draws", "presents", "recorder_descriptions", "device.x87_cw_before",
                "device.x87_cw_after", "device.behavior"):
        if get(a, key) != get(b, key):
            differing.append(key)
            f.append(f"{key}: dxvk {get(a, key)!r}; sdlgpu {get(b, key)!r}")
    return result("d", f, {"method_rows": len(ra), "differing": differing})


def check_e(ev: dict) -> dict:
    f: list[str] = []
    census = ev["census"]
    comparable = (list(range(1, census["frames"] + 1)) if geometry_scope(ev) and exact_count(census.get("frames"))
                  else [int(x) for x in census.get("draw_free_frames", [])])
    da, db = ev["dxvk"]["times"], ev["sdl"]["times"]
    frames = census.get("frames")
    if len(da) != frames or len(db) != frames:
        f.append(f"times rows: dxvk {len(da)}, sdlgpu {len(db)}, census frames {frames}")
    if geometry_scope(ev):
        for host, rows in (("dxvk", da), ("sdlgpu", db)):
            for i, row in enumerate(rows, 1):
                if any(not isinstance(row.get(k), str) or not row[k].isdigit() for k in NON_HASH_FIELDS):
                    f.append(f"{host}: times row {i} has an absent or invalid numeric column")
                if not isinstance(row.get("bgrx_hash"), str) or not re.fullmatch(r"0x[0-9a-f]{16}", row["bgrx_hash"]):
                    f.append(f"{host}: times row {i} has no exact 64-bit bgrx hash")
    # Row i is frame i on both hosts, so a frame is looked up by its position.
    for host, rows in (("dxvk", da), ("sdlgpu", db)):
        bad = next((i + 1 for i, r in enumerate(rows) if r.get("frame") != str(i + 1)), None)
        if bad is not None:
            f.append(f"{host}: times row {bad} is frame {rows[bad - 1].get('frame')!r}, not {bad}")
    other = [i + 1 for i, (x, y) in enumerate(zip(da, db)) if any(x.get(k) != y.get(k) for k in NON_HASH_FIELDS)]
    if other:
        f.append(f"{len(other)} row(s) differ outside bgrx_hash and wall_us, first at row {other[0]}")
    if not comparable:
        f.append("no comparable frame: the census finds no frame without a draw call")
    # Everything above is about the rows, not the pixels: a control whose fault changes
    # only pixels must leave it empty (control_outcome, reason "hashes").
    row_failures = list(f)
    mismatched = []
    for fr in comparable:
        x = da[fr - 1].get("bgrx_hash") if 0 < fr <= len(da) else None
        y = db[fr - 1].get("bgrx_hash") if 0 < fr <= len(db) else None
        if x is None or x != y:
            mismatched.append(fr)
            if len(mismatched) <= 10:
                f.append(f"frame {fr}: bgrx_hash {y} (sdlgpu) != {x} (dxvk)")
    if len(mismatched) > 10:
        f.append(f"... {len(mismatched)} draw-free frame(s) differ in all")
    drawn_equal = [i + 1 for i, (x, y) in enumerate(zip(da, db))
                   if i + 1 not in set(comparable) and x.get("bgrx_hash") == y.get("bgrx_hash")]
    # What the comparable frames show (the review's finding 2): DXVK's distinct hashes on
    # them, each marked when it is an all-black back buffer of the census's CreateDevice size.
    bb = get(census, "device.back_buffer") or []
    black = black_hash(bb[0], bb[1]) if len(bb) >= 2 else None
    seen: dict[str, int] = {}
    for fr in comparable:
        if 0 < fr <= len(da):
            seen[da[fr - 1].get("bgrx_hash")] = seen.get(da[fr - 1].get("bgrx_hash"), 0) + 1
    hashes = [{"bgrx_hash": h, "frames": n, "black": h == black} for h, n in sorted(seen.items(), key=str)]
    return result("e", f, {"comparable_frames": ranges(comparable), "comparable": len(comparable),
                           "row_failures": row_failures,
                           "mismatched_frames": ranges(mismatched), "mismatched": len(mismatched),
                           "comparable_hashes": hashes, "black_hash": black,
                           "comparable_not_black": sum(h["frames"] for h in hashes if not h["black"]),
                           "informational_frames_with_draws_equal": len(drawn_equal),
                           "informational_first": drawn_equal[:20],
                           "hash_scope": "all completed frames" if geometry_scope(ev) else "draw-free frames only"})


def check_f(ev: dict) -> dict:
    dirty = ev["census"].get("draw_free_frames_back_buffer_drawn_earlier", [])
    f = [f"draw-free frame {fr} shows a back buffer a draw reached since its last full colour clear"
         for fr in dirty[:10]]
    if geometry_scope(ev):
        f = []  # geometry frames may retain earlier geometry; clear-only lineage is still recorded
    elif len(dirty) > 10:
        f.append(f"... {len(dirty)} such frames")
    # The lineage is the census's reading of the stream; it rests on the clears the device
    # actually received, call for call.
    census_calls = get(ev["census"], "clears.host_calls")
    device_calls = get(ev["sdl"]["device"], "clears.calls")
    if census_calls is None or census_calls != device_calls:
        f.append(f"Clear calls: the census sends {census_calls}, the device received {device_calls}")
    return result("f", f, {"draw_free_frames_with_prior_geometry": dirty, "frames_made_by_clears_alone": ev["census"].get("frames_made_by_clears_alone"),
                           "clear_calls": {"census": census_calls, "device": device_calls}})


def check_g(ev: dict) -> dict:
    f: list[str] = []
    log = ev["dxvk"]["log"]
    sdl = (ev["sdl"]["device"] or {}).get("sdl_gpu") or {}
    if not log.get("device_name") or log.get("device_name") != sdl.get("device_name"):
        f.append(f"DXVK's device {log.get('device_name')!r} is not SDL_gpu's {sdl.get('device_name')!r}")
    sdl_driver = f"{sdl.get('driver_name')} {normalise_version(str(sdl.get('driver_info') or ''))}"
    if not log.get("driver") or log.get("driver") != sdl_driver:
        f.append(f"DXVK's driver {log.get('driver')!r} is not SDL_gpu's {sdl_driver!r} "
                 f"(driver_info {sdl.get('driver_info')!r}, driver_version {sdl.get('driver_version')!r})")
    return result("g", f, {"dxvk": log, "sdl_gpu": sdl, "adapter": ev["dxvk"]["report"].get("adapter")})


def valid_player_options(options) -> bool:
    """The actual player's nine unconditional JSON option fields."""
    try:
        return (isinstance(options, dict)
                and all(type(options.get(k)) is bool for k in
                        ("pace", "vsync", "hidden", "allow_truncated", "allow_notes", "stats_only"))
                and all(type(options.get(k)) is int and 0 <= options[k] <= 0xffffffff
                        for k in ("max_frames", "report_every"))
                and type(options.get("speed")) in (int, float)
                and math.isfinite(options["speed"]) and options["speed"] > 0)
    except OverflowError:
        return False


def measured_nvidia_gpu(gpu) -> bool:
    return (isinstance(gpu, dict) and gpu.get("driver") == "vulkan" and gpu.get("driver_name") == "NVIDIA"
            and gpu.get("vk_query_ok") is True and type(gpu.get("vk_vendor_id")) is int
            and gpu["vk_vendor_id"] == 0x10de and gpu.get("vk_query") == "vkGetPhysicalDeviceProperties"
            and str(gpu.get("device_name", "")).startswith("NVIDIA "))


def complete_vulkan_settings(gpu) -> bool:
    required = {"driver", "device_name", "driver_name", "driver_version", "driver_info",
                "depth_format", "debug_mode", "vulkan_options", "shader_formats", "vk_query_ok", "vk_query",
                "vk_vendor_id", "vk_device_id", "sdl_revision", "sdl_patched", "sdl_capabilities_present",
                "detected", "capability_override", "sampler_clamp_to_border", "depth_clip_and_clamp", "fallback_owed"}
    options = get(gpu, "vulkan_options")
    return (isinstance(gpu, dict) and required <= set(gpu) and isinstance(options, dict)
            and isinstance(options.get("api_version"), str) and bool(options["api_version"])
            and all(isinstance(options.get(k), list) and all(isinstance(v, str) for v in options[k])
                    for k in ("features", "extensions"))
            and all(isinstance(gpu.get(k), str) and bool(gpu[k])
                    for k in ("driver_version", "driver_info", "sdl_revision")))


def metalharbor_facts(stage, frames, reference, settings, gpu) -> list[str]:
    """Registered MH facts at both raw report and serialized marker boundaries."""
    f = []
    if type(stage) is not int or stage != METAL_HARBOR["stage"]:
        f.append("registered Metal Harbor requires stage 3")
    if type(frames) is not int or frames != METAL_HARBOR["frames"]:
        f.append("registered Metal Harbor requires its complete original frame count")
    if (not isinstance(reference, dict) or reference.get("sha256") != METAL_HARBOR["reference_sha256"]
            or type(reference.get("rows")) is not int or reference["rows"] != METAL_HARBOR["frames"]):
        f.append("registered Metal Harbor requires the held complete DXVK reference bytes; a custom reference cannot replace them")
    if not measured_nvidia_gpu(gpu):
        f.append("registered Metal Harbor requires queried NVIDIA Vulkan identity")
    if not complete_vulkan_settings(gpu):
        f.append("registered Metal Harbor requires complete measured SDL Vulkan settings")
    if not isinstance(settings, dict) or set(settings) != {"dxvk", "sdlgpu"}:
        return f + ["registered Metal Harbor lacks both player settings"]
    required_device = {"created", "failure", "back_buffer", "auto_depth", "interval", "behavior",
                       "guest_behavior", "guest_multisample", "x87_cw_before", "x87_cw_after",
                       "guest_device", "guest_back_buffer", "guest_depth"}
    for host, recorded in settings.items():
        options, device, env = (get(recorded, k) for k in ("options", "device", "env"))
        if not valid_player_options(options):
            f.append(f"{host}: registered Metal Harbor has incomplete or invalid player options")
        elif options["max_frames"] != 0 or options["allow_truncated"] or options["stats_only"]:
            f.append(f"{host}: registered Metal Harbor requires unbounded renderer playback")
        if not isinstance(device, dict) or not required_device <= set(device):
            f.append(f"{host}: registered Metal Harbor has incomplete requested device settings")
        elif device["created"] is not True or device["failure"] is not None:
            f.append(f"{host}: registered Metal Harbor requires a successfully created device")
        elif (type(get(options, "vsync")) is bool
              and device["interval"] != ("ONE" if options["vsync"] else "IMMEDIATE")):
            f.append(f"{host}: registered Metal Harbor vsync contradicts the created interval")
        if (not isinstance(env, dict) or not set(AA_ENV_KEYS) <= set(env)
                or not all(isinstance(v, str) for v in env.values())):
            f.append(f"{host}: registered Metal Harbor has incomplete player environment settings")
    try:
        for key in ("options", "device"):
            if json.dumps(get(settings["dxvk"], key), sort_keys=True, allow_nan=False) != json.dumps(
                    get(settings["sdlgpu"], key), sort_keys=True, allow_nan=False):
                f.append(f"registered Metal Harbor player {key} settings differ")
        envs = [{k: v for k, v in (get(settings[host], "env") or {}).items()
                 if k not in (*AA_ENV_OUTPUTS, "SA2_DXVK_D3D9")} for host in ("dxvk", "sdlgpu")]
        if envs[0] != envs[1]:
            f.append("registered Metal Harbor non-output player environment settings differ")
    except (ValueError, TypeError, AttributeError, OverflowError):
        f.append("registered Metal Harbor settings are not finite JSON objects")
    return f


def check_h(ev: dict) -> dict | None:
    ref = ev.get("reference")
    registered = (scope_of(ev) == "r2-draws-full"
                  and ev["census"].get("stream_sha256") == METAL_HARBOR["stream_sha256"]
                  and not ev.get("fault"))  # controls deliberately omit the reference and matrix
    if ref is None and not registered:
        return None
    ref = ref or {"path": None, "sha256": None, "rows": []}
    f: list[str] = []
    settings = {host: {k: ev[role]["report"].get(k) for k in ("options", "device", "env")}
                for host, role in (("dxvk", "dxvk"), ("sdlgpu", "sdl"))} if registered else None
    detail = {"reference": ref["path"], "sha256": ref["sha256"], "rows": len(ref["rows"])}
    if registered:
        detail["player_settings"] = settings
        f += metalharbor_facts(ev["census"].get("stage"), ev["census"].get("frames"), detail, settings,
                              get(ev["sdl"].get("device"), "sdl_gpu"))
    if (scope_of(ev) == "r2-draws-full" and ev["census"].get("stream_sha256") == BOOT_STREAM_SHA256
            and ref.get("sha256") != BOOT_REFERENCE_SHA256):
        f.append("full boot requires the existing registered DXVK reference bytes; a custom reference cannot replace them")
    rows = ref["rows"]
    fresh = ev["dxvk"]["times"]
    if len(rows) != len(fresh):
        f.append(f"the reference has {len(rows)} rows, the fresh DXVK run {len(fresh)}")
    for i, (x, y) in enumerate(zip(rows, fresh)):
        bad = [k for k in TIMES_FIELDS if x.get(k) != y.get(k)]
        if bad:
            f.append(f"frame {i + 1}: {bad} differ from the reference: the driver or the environment drifted")
            break
    return result("h", f, detail)


def check_i(ev: dict) -> dict | None:
    m = ev.get("matrix")
    if m is None:
        return None
    f: list[str] = []
    for host in MATRIX_HOSTS:
        if m["exit"].get(host) != 0:
            f.append(f"{host}: the clear matrix exited {m['exit'].get(host)!r}")
    want = m["lines"]["dxvk"]
    if not want:
        f.append("dxvk: the clear matrix printed no case")
    for host in MATRIX_HOSTS[1:]:
        got = m["lines"][host]
        if got != want:
            i = next(i for i in range(max(len(got), len(want))) if got[i:i + 1] != want[i:i + 1])
            f.append(f"{host}: line {i + 1} is {(got[i:i + 1] or ['<none>'])[0]!r}, DXVK's "
                     f"{(want[i:i + 1] or ['<none>'])[0]!r}")
    # The renderers the comparison's runs pinned: DXVK's run's library, and the second host's
    # run's player.
    dx = get(ev["dxvk"]["report"], "files.libdxvk_d3d9.sha256")
    if not dx or m["dxvk_library"].get("sha256") != dx:
        f.append(f"dxvk: the matrix ran DXVK's library {m['dxvk_library'].get('sha256')}, not the DXVK run's ({dx})")
    dev = m.get("device") or {}
    lib, pinned = get(dev, "library.sha256"), get(ev["sdl"]["report"], "files.player.sha256")
    if not lib or lib != pinned:
        f.append(f"sdlgpu: the matrix ran the player {lib}, not the second host's run's ({pinned})")
    if dev.get("complete") is not True or dev.get("fault") is not None or dev.get("failure") is not None:
        f.append(f"sdlgpu: the matrix's device report is complete {dev.get('complete')!r}, fault {dev.get('fault')!r}, "
                 f"failure {dev.get('failure')!r}")
    # The GPU and driver the players ran on (check g's): DXVK's from its logs, SDL_gpu's from
    # the device's reports.
    ml, pl = m.get("log") or {}, ev["dxvk"]["log"] or {}
    for k in ("device_name", "driver"):
        if not ml.get(k) or ml.get(k) != pl.get(k):
            f.append(f"dxvk: the matrix ran on {k} {ml.get(k)!r}, the DXVK run on {pl.get(k)!r}")
    ms, ps = dev.get("sdl_gpu") or {}, get(ev["sdl"]["device"], "sdl_gpu") or {}
    for k in ("device_name", "driver_name", "driver_info"):
        if not ms.get(k) or ms.get(k) != ps.get(k):
            f.append(f"sdlgpu: the matrix ran on {k} {ms.get(k)!r}, the second host's run on {ps.get(k)!r}")
    return result("i", f, {"cases": len(want), "names": [line.split("\t")[0] for line in want],
                           "outputs": m["outputs"], "dxvk_library": m["dxvk_library"].get("sha256"), "library": lib,
                           "binaries": m["binaries"]})


def evaluate(ev: dict) -> list[dict]:
    out = [check_a(ev), check_b(ev), check_c(ev), check_d(ev), check_e(ev), check_f(ev), check_g(ev)]
    for extra in (check_h(ev), check_i(ev)):
        if extra is not None:
            out.append(extra)
    return out


def runs_of(by_frame: dict[int, int]) -> list[list[int]]:
    """{frame: n} as [[first, last, n], ...] (expand_runs' inverse)."""
    out: list[list[int]] = []
    for f in sorted(by_frame):
        if out and out[-1][2] == by_frame[f] and out[-1][1] + 1 == f:
            out[-1][1] = f
        else:
            out.append([f, f, by_frame[f]])
    return out


def restored_draws(dev: dict, census: dict, dropped) -> tuple[dict | None, list[str]]:
    """The device's draws (its report's "draws") with the draw count-draw left uncounted put
    back: `dropped` (its fault_uncounted) added to the total, to the one skipped class and to
    the one frame, or after the last Present, where the census has that many more than the
    device. The fault drops the stream's first skipped draw from each and nothing else
    (host/d3d9gpu/d9g_device.cpp Device::count_draw), so None, with why, when the device is
    short of the census in any other way there; when a count is not a whole number or is
    missing (a class the device counted none of is absent from its report, a frame and the
    draws after the last Present never are); or when the device is short in a frame after the
    first skipped draw's. That frame is no later than the first by which the census has
    counted more draws than it refused or found empty (every frame before the first skipped
    draw's holds only those), so where the census counts none of them it is exactly the
    census's first frame with a draw."""
    why = []
    whole = lambda v: type(v) is int  # noqa: E731  (JSON's true is no count)

    def short_of(census_n: dict, device_n: dict, absent) -> dict:
        """The census's count less the device's, where they differ; where one is missing
        (`absent` None) or not whole, the two values."""
        out = {}
        for k in set(census_n) | set(device_n):
            a, b = census_n.get(k, absent), device_n.get(k, absent)
            if a != b:
                out[k] = a - b if whole(a) and whole(b) else f"census {a!r}, device {b!r}"
        return out

    cells = lambda by: {(m, p): n for m, ps in (by or {}).items() for p, n in (ps or {}).items()}  # noqa: E731
    short = short_of(cells(census.get("skipped_no_shader_route")), cells(dev.get("skipped_no_shader_route")), 0)
    if not whole(dropped) or list(short.values()) != [dropped]:
        by_class = {f"{m} {p}": n for (m, p), n in sorted(short.items())}
        why.append(f"c: the device's skipped draws are short of the census's by {by_class}, not by the {dropped!r} "
                   f"draw(s) it left uncounted in one class")
    after = "after the last Present"
    df = {**expand_runs(dev.get("per_frame")), after: dev.get("after_last_present")}
    cf = {**expand_runs(census.get("per_frame")), after: census.get("after_last_present")}
    shortf = short_of(cf, df, None)
    if not whole(dropped) or list(shortf.values()) != [dropped]:
        why.append(f"c: the device's draws per frame are short of the census's by "
                   f"{dict(sorted(shortf.items(), key=lambda kv: str(kv[0])))}, not by the {dropped!r} draw(s) it "
                   f"left uncounted in one frame")
    else:
        unskipped = sum(n for cls in ("refused", "empty") for n in cells(census.get(cls)).values())
        order = [*sorted(k for k in cf if k != after), after]
        latest = next((k for k, seen in zip(order, itertools.accumulate(cf[k] for k in order)) if seen > unskipped),
                      None)
        (frame,) = shortf
        if latest is None or order.index(frame) > order.index(latest):
            name = lambda k: k if k == after else f"in frame {k}"  # noqa: E731
            why.append(f"c: the device is short of the census {name(frame)}, but the first skipped draw, the one the "
                       f"fault leaves out, is {name(latest)} or earlier: the census's draws to there outnumber the "
                       f"{unskipped} it refused or found empty")
    if why:
        return None, why
    (m, p), = short
    skipped = {k: dict(v) for k, v in (dev.get("skipped_no_shader_route") or {}).items()}
    by_type = skipped.setdefault(m, {})        # absent where the device counted none of the method
    by_type[p] = by_type.get(p, 0) + dropped
    frame, = shortf
    df[frame] += dropped
    total = dev.get("total")
    return dict(dev, total=total + dropped if whole(total) else total, skipped_no_shader_route=skipped,
                per_frame=runs_of({k: v for k, v in df.items() if k != after}), after_last_present=df[after]), []


def answered_ok(rep: dict, rule: str) -> tuple[dict, list[str]]:
    """DXVK's report as scene-answer would leave it: every BeginScene `rule` allowed (inside a
    scene, which DXVK answers D3DERR_INVALIDCALL) answered D3D_OK instead, counted as
    host/player/player.cpp Player::count counts it: not failed, not allowed, and differing
    from the guest where the guest's answer was not D3D_OK. Only BeginScene's row and the
    allowed failures change. With why where the report cannot give the result call by call:
    its allowed calls not all listed (the player lists the first 20), so the list the fault
    leaves is unknown; a call under the rule not listed or not a BeginScene answer; a row
    counting fewer failures or differing answers than the rule's calls make; or a first
    failure, or first differing answer, that the fault moves to a call the report does not
    name."""
    why = []
    allowed = rep.get("allowed_host_failures") or {}
    rules, calls = dict(allowed.get("rules") or {}), list(allowed.get("calls") or [])
    listed = sum(rules.values())
    n = rules.pop(rule, 0)
    ours = [c for c in calls if c.endswith(f" ({rule})")]
    answers = [(int(a[1]), a[2], a[3]) for a in map(ALLOWED_CALL.fullmatch, ours) if a]   # (seq, host, guest)
    if listed != len(calls):
        why.append(f"d: DXVK's report lists {len(calls)} of its {listed} allowed call(s): the allowed calls left once "
                   f"the fault answers {rule!r} cannot be derived")
    if len(answers) != n:
        why.append(f"d: DXVK's report lists {len(answers)} of its {n} call(s) under {rule!r} as BeginScene answers: "
                   f"the fault's answers cannot be derived call by call")
    out = copy.deepcopy(rep)
    out["allowed_host_failures"] = dict(allowed, rules=rules, calls=[c for c in calls if c not in ours])
    row = next((m for m in out.get("methods") or [] if (m.get("type"), m.get("method")) == ("Device", "BeginScene")),
               {})
    # Failures: the rule's calls are no longer among them; the first failure is kept unless
    # it was one of the rule's (the next one is not in the report).
    failed = int(row.get("failed", 0)) - n
    if failed < 0:
        why.append(f"d: DXVK's BeginScene row ({row}) counts fewer failures than its {n} call(s) under {rule!r}")
    elif failed and row.get("first_fail_seq") in {s for s, _, _ in answers}:
        why.append(f"d: DXVK's first failed BeginScene (seq {row['first_fail_seq']}) is one {rule!r} allowed: the "
                   f"first failure the fault leaves is not in its report")
    # Differing answers: a call under the rule differed where the guest's answer was not
    # DXVK's failure, and differs, answered D3D_OK, where the guest's was not D3D_OK.
    was = [a for a in answers if a[1] != a[2]]
    now = [a for a in answers if a[2] != D3D_OK]
    others = int(row.get("hr_differs_from_guest", 0)) - len(was)
    first = None                       # (first_host_hr, guest_eax) the fault leaves, where known
    if others < 0:
        why.append(f"d: DXVK's BeginScene row ({row}) counts fewer answers differing from the guest's than its "
                   f"{len(was)} call(s) under {rule!r} that differ")
    elif others == 0 and now:
        first = (D3D_OK, min(now)[2])
    elif others and not now and (not was or (row.get("first_host_hr"), row.get("guest_eax")) != min(was)[1:]):
        first = (row.get("first_host_hr"), row.get("guest_eax"))     # an answer outside the rule came first
    elif others:
        why.append(f"d: DXVK's first BeginScene answer differing from the guest's, with {rule!r} answered D3D_OK, "
                   f"is not in its report ({others} differing outside the rule, {len(now)} under it)")
    if failed > 0:
        row["failed"] = failed
    else:
        for k in ("failed", "first_fail_hr", "first_fail_seq"):
            row.pop(k, None)
    if others + len(now) > 0:
        row["hr_differs_from_guest"] = others + len(now)
        if first:
            row["first_host_hr"], row["guest_eax"] = first
    else:
        for k in ("hr_differs_from_guest", "first_host_hr", "guest_eax"):
            row.pop(k, None)
    return out, why


def reason_failures(control: dict, target: dict, ev: dict) -> list[str]:
    """What the control's target check shows beyond its fault's exact effect (CONTROLS'
    "reason"); [] when the target failed, or passed, for that effect alone."""
    d = target["detail"]
    if control["reason"] == "geometry":
        why = list(d.get("row_failures", []))
        mismatches = {f for a, b in d.get("mismatched_frames", []) for f in range(a, b + 1)}
        geometry = {f for f, n in expand_runs(ev["census"].get("accepted_per_frame")).items() if n}
        if not mismatches & geometry:
            why.append("geometry-noop changed no completed geometry-bearing frame")
        return why
    if control["reason"] == "hashes":
        return [f"{control['check']}: {x} (not a comparable frame's hash)" for x in d.get("row_failures", [])]
    if control["reason"] == "uncounted":
        dev = ev["sdl"]["device"] or {}
        dropped = get(dev, "draws.fault_uncounted")
        restored, why = (restore_r2_draws(dev.get("draws") or {}, ev["census"], dropped) if geometry_scope(ev) else
                         restored_draws(dev.get("draws") or {}, ev["census"].get("draws") or {}, dropped))
        if restored is None:
            return why
        again = check_c(dict(ev, sdl=dict(ev["sdl"], device=dict(dev, draws=restored))))
        return [f"c: {x} (with the {dropped} draw(s) left uncounted put back)" for x in again["failures"]]
    if control["reason"] == "begin_scene":
        want, why = answered_ok(ev["dxvk"]["report"], control["exercised_by"])
        again = check_d(dict(ev, dxvk=dict(ev["dxvk"], report=want)))
        return why + [f"d: {x} (DXVK's side with its answers under the rule made D3D_OK)" for x in again["failures"]]
    raise ValueError(f"control {control['name']}: no reason {control['reason']!r}")


def control_outcome(control: dict, ev: dict) -> dict:
    """Whether a control's run moved its check as required, for its fault's named reason:
    the run is evaluated under its fault (check b expects the fault's name and, for
    count-draw, the draw it drops), and every check but the target, and the target beyond
    the reason, must pass."""
    ev = dict(ev, fault=control["faults"], reference=None, matrix=None)
    checks = evaluate(ev)
    by = {c["check"]: c for c in checks}
    target = by[control["check"]]
    got = "pass" if target["pass"] else "fail"
    out = {"name": control["name"], "faults": control["faults"], "check": control["check"], "want": control["want"],
           "got": got, "why": control["why"], "failures": target["failures"][:5], "exercised": True}
    if control.get("exercised_by"):
        rules = get(ev["dxvk"]["report"], "allowed_host_failures.rules") or {}
        out["exercised"] = bool(rules.get(control["exercised_by"]))
    if control.get("exercised_by_draws"):
        out["exercised"] = skipped_draws(ev["census"]) > 0
    # A run that crashed, stopped early or read back nothing fails its target too: what it
    # fails beyond the fault's named reason makes it no control.
    collateral = [f"{c['check']} {c['title']}: {x}" for c in checks if c["check"] != control["check"]
                  for x in c["failures"]]
    if got == "fail":
        collateral += reason_failures(control, target, ev)
    out["collateral"] = collateral[:8]
    as_required = out["exercised"] and got == control["want"] and not collateral
    if control.get("must_differ"):
        # The draw-free frames the fault must change: those whose back buffer was ever
        # cleared whole in colour (clear-colour; a frame never cleared holds zeros, which
        # no clear colour touches), or every one after the first Present (the scribble
        # comes after a Present, and frame 1 has none before it).
        comparable = [int(x) for x in ev["census"].get("draw_free_frames", [])]
        never = set(ev["census"].get("draw_free_frames_never_colour_cleared", []))
        need = [f for f in comparable if (f not in never if control["must_differ"] == "colour_cleared" else f > 1)]
        mismatched = set()
        for first, last in target["detail"].get("mismatched_frames", []):
            mismatched.update(range(first, last + 1))
        out["must_differ"] = ranges(need)
        out["differ"] = ranges(sorted(mismatched))
        if not need:
            out["exercised"] = False
            as_required = False
        elif as_required:
            as_required = set(need) <= mismatched
    fault = get(ev["sdl"]["device"], "fault")
    if fault != control["faults"]:
        as_required = False
        out["failures"] = [f"the run's device report names fault {fault!r}, not {control['faults']!r}"] + out["failures"]
    out["as_required"] = as_required
    out["outcome"] = "not exercised" if not out["exercised"] else ("as required" if as_required else "NOT as required")
    return out


def reference_for(census: dict, given: str | None) -> dict | None:
    registered = (census.get("stream_sha256") == METAL_HARBOR["stream_sha256"]
                  and census.get("stage") == METAL_HARBOR["stage"])
    path = given or (METAL_HARBOR["reference"] if registered else
                     REFERENCES.get((census.get("stream_sha256"), census.get("stage"))))
    if not path:
        return None
    p = Path(path) if Path(path).is_absolute() else ROOT / path
    return {"path": rel(p), "sha256": sha256(p) if p.exists() else None, "rows": read_times(p)}


def pipeline() -> dict:
    """What the comparison is made of, by repository path (check_receipt_pins.py's
    inputs.pipeline): this tool and its checker, the second host's sources and the player's,
    and everything else the build records name (provenance fails a record naming a file not
    pinned here)."""
    files = [ROOT / SELF, ROOT / PLAY_REPORT, ROOT / "tools/s5/stream_dump.py", ROOT / "host/sa2_stream.h", ROOT / "host/dxvk_smoke.cpp",
             ROOT / "tools/pins.json"]
    for d in ("host/d3d9gpu", "host/player"):
        files += sorted(p for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return {rel(p): sha256(p) for p in files}


def checker_failures(runs: list[dict], pins: dict) -> list[str]:
    """Each run whose check.json the pinned play_report.py did not write (its CHECKED_BY)."""
    want = pins.get(PLAY_REPORT)
    return [f"{run.get('dir')}: its check.json was written by play_report.py {run.get('checked_by')}, not the "
            f"pinned {want}" for run in runs if run.get("checked_by") != want]


def depfiles_unrecorded(build: Path, rec: dict) -> list[str]:
    """Each file an exact record's depfiles name (read again with build_record.py's own
    depfile reader: the record held to its depfiles, not a second parse of them) that the
    record does not hold as the kind its place makes it: under the build, generated (by its
    path there); in the record's checkout but not under its private/, a source (by its path
    in the checkout); anywhere else, external (by its absolute path). And each such file
    read through a symbolic link in the checkout outside its private/ (build_record.py
    tree_links: the record would hold what the link leads to, a commit the link), and a
    record with no depfile, no checkout, or a checkout under the build (where its sources
    would read as generated). A record that dropped a file its compiles read fails here."""
    out: list[str] = []
    deps = rec.get("depfiles") or {}
    tree = Path(rec["tree"]).resolve() if isinstance(rec.get("tree"), str) else None
    bin_r = build.resolve()
    if not deps:
        out.append("names no depfile (an exact record is made from its compiles' depfiles)")
    if tree is None:
        return out + ["names no checkout (tree)"]
    if tree.is_relative_to(bin_r):
        return out + [f"its checkout {tree} is under the build {bin_r}, where its sources read as generated"]
    for dep in sorted(deps):
        try:
            paths = build_record.depfile_paths((build / dep).read_text())
        except (OSError, SystemExit) as exc:
            out.append(f"depfile {dep} cannot be read again ({exc})")
            continue
        for p in paths:
            if not Path(p).is_absolute():
                out.append(f"depfile {dep} names {p}, a relative path, which cannot be read again")
                continue
            links = build_record.tree_links(tree, Path(p))
            if links:
                out.append(f"depfile {dep} names {p}, read through the checkout's symbolic link {links[0]}, which "
                           f"no record can hold")
                continue
            r = Path(p).resolve()
            if r.is_relative_to(bin_r):
                kind, key = "generated", str(r.relative_to(bin_r))
            elif r.is_relative_to(tree) and not r.is_relative_to(tree / "private"):
                kind, key = "sources", str(r.relative_to(tree))
            else:
                kind, key = "external", str(r)
            if key not in (rec.get(kind) or {}):
                out.append(f"depfile {dep} names {p}, which the record does not hold ({kind} {key})")
    return out


def provenance(build: Path, pins: dict, dxvk_runs: list[dict] = (), gpu_runs: list[dict] = (),
               matrix: dict | None = None) -> dict:
    """Whether the build's binaries are what its records say they are, built from the tree
    `pins` holds: each record's binaries are the files in the build, and no binary is named
    by two records; each of its sources and generated inputs (and, in an exact record, its
    external headers and its depfiles) has the hash it had when the build read it; every
    pinned file under RECORDED is named by a record; each player's record is its exact
    record (sa2-build-record/v2, it alone, its expectations among its generated inputs,
    REQUIRED_SOURCES among its sources, every file its depfiles name held:
    depfiles_unrecorded) and its contract its host's keys (CONTRACTS), and the DXVK
    player's record names no host/d3d9gpu source. With runs, by the role this tool gave
    them: every DXVK run's player (files.player) is the build's sa2_player; every
    second-host run's (the second host's run and the controls') is the build's
    sa2_player_gpu, and so is the file its device's report says its code ran from; every
    run's check.json is the pinned play_report.py's.
    With `matrix`, the matrix's binaries are the build's. A failure here means the receipt
    would pin sources that did not make what ran."""
    f: list[str] = []
    recs: dict[str, dict] = {}
    for name in BUILD_RECORDS:
        rec = read_json(build / name)
        if not isinstance(rec, dict) or not all(isinstance(rec.get(k), dict) for k in ("binaries", "sources")):
            script = "host/d3d9gpu/build.sh" if name in ("sa2_player_gpu.json", "d3d9gpu_tools.json") \
                else "host/player/build.sh"
            f.append(f"{name}: no build record in {rel(build)} ({script} writes it)")
            continue
        recs[name] = rec
        for b, want in rec["binaries"].items():
            have = sha256(build / b) if (build / b).is_file() else None
            if have != want:
                f.append(f"{name}: {b} is {have}, not the {want} the build wrote")
        for path, want in rec["sources"].items():
            if path not in pins:
                # The receipt pins the pipeline; a source it does not pin could move unseen.
                f.append(f"{name}: source {path} is not pinned by the pipeline (compare_hosts.py pipeline())")
                continue
            if pins[path] != want:
                f.append(f"{name}: source {path} is {pins[path]} in the tree, {want} in the build")
        for kind in ("generated", "depfiles"):
            for path, want in (rec.get(kind) or {}).items():
                have = sha256(build / path) if (build / path).is_file() else None
                if have != want:
                    f.append(f"{name}: {kind[:-1] if kind == 'depfiles' else kind} {path} is {have}, {want} in the "
                             f"build")
        for path, want in (rec.get("external") or {}).items():
            have = sha256(Path(path)) if Path(path).is_file() else None
            if have != want:
                f.append(f"{name}: external {path} is {have}, {want} in the build")
    owners: dict[str, list[str]] = {}
    for name, rec in recs.items():
        for b in rec["binaries"]:
            owners.setdefault(b, []).append(name)
    for b, names in sorted(owners.items()):
        if len(names) > 1:
            f.append(f"{b}: named by {len(names)} records ({', '.join(names)}), not one")
    named = {p for r in recs.values() for p in r["sources"]}
    for path in sorted(p for p in pins if p.startswith(RECORDED) and p not in named):
        f.append(f"{path}: pinned, and no build record names it (the build predates it)")
    # Each player's own record and contract: the DXVK player built from nothing of the
    # second host's and expecting DXVK's files; the GPU player expecting SDL's alone.
    for binary, name in PLAYER_RECORDS.items():
        rec = recs.get(name)
        if rec is None:
            continue
        if rec.get("schema") != EXACT_RECORD or list(rec["binaries"]) != [binary]:
            f.append(f"{name}: {rec.get('schema')} of {sorted(rec['binaries'])}, not {binary}'s exact record "
                     f"({EXACT_RECORD})")
        missing = [p for p in REQUIRED_SOURCES[binary] if p not in rec["sources"]]
        if missing:
            f.append(f"{name}: names none of {missing} among its sources (REQUIRED_SOURCES)")
        f += [f"{name}: {why}" for why in depfiles_unrecorded(build, rec)]
        where, keys = CONTRACTS[binary]
        if f"{where}/expectations.json" not in (rec.get("generated") or {}):
            f.append(f"{name}: its generated inputs do not name {where}/expectations.json")
        contract = get(read_json(build / where / "expectations.json"), "contract")
        if not isinstance(contract, dict) or list(contract) != list(keys):
            f.append(f"{binary}'s contract ({where}/expectations.json) is "
                     f"{list(contract) if isinstance(contract, dict) else contract!r}, not {list(keys)}")
    second = sorted(p for p in (recs.get("sa2_player.json") or {}).get("sources", {}) if p.startswith("host/d3d9gpu/"))
    if second:
        f.append(f"sa2_player.json: the DXVK player was built from the second host's {second[:4]}")
    built = {b: h for r in recs.values() for b, h in r["binaries"].items()}
    for host, runs in (("dxvk", dxvk_runs), ("sdlgpu", gpu_runs)):
        binary = PLAYERS[host]
        for run in runs:
            rep, where = run.get("report") or {}, run.get("dir")
            if get(rep, "files.player.sha256") != built.get(binary):
                f.append(f"{where}: the player that ran ({get(rep, 'files.player.sha256')}) is not the build's "
                         f"{binary} ({built.get(binary)})")
            if host == "sdlgpu" and get(run.get("device"), "library.sha256") != built.get(binary):
                f.append(f"{where}: the device ran from {get(run.get('device'), 'library.sha256')}, not the build's "
                         f"{binary} ({built.get(binary)})")
    f += checker_failures([*dxvk_runs, *gpu_runs], pins)
    if matrix is not None:
        for b in MATRIX_BINARIES:
            ran = (matrix.get("binaries") or {}).get(b)
            if ran is None or ran != built.get(b):
                f.append(f"{matrix.get('dir')}: the matrix's {b} ({ran}) is not the build's ({built.get(b)})")
    return {"pass": not f, "failures": f,
            "records": {n: sha256(build / n) for n in BUILD_RECORDS if (build / n).is_file()},
            "binaries": {n: r["binaries"] for n, r in recs.items()},
            "runs_held": len(dxvk_runs) + len(gpu_runs)}


def summarise(run: dict) -> dict:
    rep = run["report"]
    return {"dir": run["dir"], "exit": run["exit"], "presents": rep.get("presents"), "draws": rep.get("draws"),
            "bgrx_digest": get(rep, "frames_out.bgrx_digest"), "wall_s": rep.get("wall_s"),
            "hidden": get(rep, "options.hidden"), "live_desktop_session": get(rep, "host.live_desktop_session"),
            "live_desktop_session_basis": get(rep, "host.live_desktop_session_basis"),
            "focus_or_occlusion": get(rep, "host.focus_or_occlusion"),
            "frames_with_flags": get(rep, "window.frames_with_flags"), "sdl_video_driver": get(rep, "host.sdl_video_driver"),
            "adapter": rep.get("adapter"), "d3d9_host": get(rep, "host_identity.d3d9_host"),
            "check_pass": run["check"].get("pass"), "player": get(rep, "files.player"), "complete": rep.get("complete"), "pass": rep.get("pass"),
            "exit_status": rep.get("exit_status"), "stage": rep.get("stage"), "stream_sha256": rep.get("stream_sha256"),
            "allow_truncated": get(rep, "options.allow_truncated"), "stats_only": get(rep, "options.stats_only"),
            "placeholder_binds": get(rep, "textures.placeholder_binds"), "stop": rep.get("stop"), "records": rep.get("records"),
            "stream_records_read_to_end": rep.get("stream_records_read_to_end"),
            "stream_sha256_after_stop": rep.get("stream_sha256_after_stop"), "stream_bytes": rep.get("stream_bytes"),
            "max_frames": get(rep, "options.max_frames")}


RUN_OUTPUTS = ("report.json", "frames.y4m.times.tsv", "d3d9gpu.json", "check.json", CHECKED_BY, "exit")
MATRIX_OUTPUTS = (*(f"{h}.tsv" for h in MATRIX_HOSTS), "run.json", "d3d9gpu.json")


def recorded_outputs(out_dir: Path) -> dict[str, str]:
    """Every file `check` reads again, by path under the run directory, with its sha256:
    the census, each run's (the main runs' and the controls'), the matrix's and the DXVK
    logs check g and check i read."""
    out = {"census.json": out_dir / "census.json"}
    # Scope controls are replay evidence too, including R2's geometry suppression.
    names = dict.fromkeys(c["name"] for c in (*CONTROLS, *R2_CONTROLS))
    runs = ["dxvk", "sdlgpu", *(f"control-{name}" for name in names)]
    for r in runs:
        out.update({f"{r}/{n}": out_dir / r / n for n in RUN_OUTPUTS})
    out.update({f"matrix/{n}": out_dir / "matrix" / n for n in MATRIX_OUTPUTS})
    for d in ("dxvk/dxvk-log", "matrix/dxvk-log"):
        out.update({f"{d}/{p.name}": p for p in sorted((out_dir / d).glob("*_d3d9.log"))})
    return {k: sha256(p) for k, p in out.items() if p.is_file()}


def assemble(out_dir: Path, args: argparse.Namespace, census: dict, dxvk: dict, sdl: dict,
             controls: list[dict] | None, started: str, pins: dict | None = None,
             control_runs: list[dict] = (), matrix: dict | None = None, ran_by: str = RAN_BY) -> dict:
    ev = {"census": census, "dxvk": dxvk, "sdl": sdl, "hidden": bool(args.hidden),
          "reference": (None if getattr(args, "scope", "r2-draws-full") == "r2-draws-partial" else
                        reference_for(census, getattr(args, "reference", None))), "matrix": matrix,
          "scope": getattr(args, "scope", "r2-draws-full"), "max_frames": getattr(args, "max_frames", 0)}
    checks = evaluate(ev)
    build = Path(args.build)
    pins = pins if pins is not None else pipeline()
    # The verdict needs the build tied to the tree it pins, and every run to the build.
    prov = provenance(build, pins, [dxvk], [sdl, *control_runs], matrix)
    verdict = "PASS" if all(c["pass"] for c in checks) and prov["pass"] else "FAIL"
    files = {}
    for name in BINARIES:
        if (build / name).exists():
            files[name] = sha256(build / name)
    for key in ("libdxvk_d3d9", "libSDL3", "dxvk_conf"):
        files[key] = get(dxvk["report"], f"files.{key}.sha256")
    stream = Path(args.stream)
    doc = {
        "schema": SCHEMA, "tool": "tools/s5/compare_hosts.py run", "started": started, "finished": now(),
        "stream": {"path": rel(stream), "sha256": census.get("stream_sha256"), "bytes": stream.stat().st_size},
        "stage": census.get("stage"), "hidden": bool(args.hidden),
        "acceptance_scope": ev["scope"], "max_frames": ev["max_frames"],
        "inputs": {"build": rel(build), "files": files, "census": {"path": rel(out_dir / "census.json"),
                                                                   "sha256": sha256(out_dir / "census.json")},
                   "pipeline": pins, "provenance": prov,
                   # the compare_hosts.py that ran the players and the matrix (`check` carries
                   # it forward; the receipt is refused when it is not the one pinned)
                   "ran_by": ran_by},
        "census": {"frames": census.get("frames"), "draws": get(census, "draws.total"),
                   "draw_free_frames": ranges(census.get("draw_free_frames", [])),
                   "draw_free_dirty": census.get("draw_free_frames_back_buffer_drawn_earlier", []),
                   "up_vertex_bytes": census.get("up_vertex_bytes"),
                   "consumed_records": census.get("consumed_records"), "consumed_bytes": census.get("consumed_bytes"),
                   "first_accepted": census.get("first_accepted"),
                   "accepted_per_frame": census.get("accepted_per_frame"),
                   "accepted_after_last_present": census.get("accepted_after_last_present")},
        "runs": {"dxvk": summarise(dxvk), "sdlgpu": summarise(sdl)},
        "outputs": recorded_outputs(out_dir),
        "checks": checks,
        "verdict": verdict,
    }
    if controls is None:
        doc["controls"] = {"run": False}
        doc["verdict_with_controls"] = "NOT_RUN"
    else:
        ok = all(c["as_required"] for c in controls)
        doc["controls"] = {"run": True, "results": controls, "all_as_required": ok}
        doc["verdict_with_controls"] = "PASS" if verdict == "PASS" and ok else "FAIL"
    doc["scope"] = ("R2a: the second host draws nothing (no shader route); the frames compared are the ones no draw "
                    "call reached. Valid for SDL_gpu's default device configuration (no SDL_GPUVulkanOptions feature "
                    "chain): a feature chain added in R1 or R3 means rerunning this comparison.") if ev["scope"] == "r2a-clears" else SCOPE_TEXT[ev["scope"]]
    doc["marker"] = acceptance_marker(doc)
    return doc


def print_summary(doc: dict) -> None:
    print(f"compare_hosts {doc['stream']['path']} stage {doc['stage']} {doc.get('acceptance_scope')}: {doc['verdict']} "
          f"(with controls: {doc['verdict_with_controls']})")
    print(f"  marker: {doc.get('marker', 'R2_FAIL')}")
    prov = doc["inputs"]["provenance"]
    print(f"  build provenance: {'pass' if prov['pass'] else 'FAIL'} (records {sorted(prov['records'])}, "
          f"{prov['runs_held']} run(s) held to the build)")
    for f in prov["failures"][:6]:
        print(f"      {f}")
    for c in doc["checks"]:
        extra = ""
        if c["check"] == "e":
            d = c["detail"]
            extra = (f" comparable {d['comparable']} frame(s) {d['comparable_frames']}, mismatched {d['mismatched']}; "
                     f"DXVK's hashes on them {[(h['bgrx_hash'], h['frames'], 'black' if h['black'] else 'not black') for h in d['comparable_hashes']][:6]}")
        if c["check"] == "i":
            extra = f" {c['detail']['cases']} case(s) through DXVK, SDL_gpu and the CPU model"
        print(f"  {c['check']} {c['title']}: {'pass' if c['pass'] else 'FAIL'}{extra}")
        for f in c["failures"][:6]:
            print(f"      {f}")
    for c in doc["controls"].get("results", []):
        print(f"  control {c['name']}: check {c['check']} {c['got']} (wants {c['want']}): {c['outcome']}")
        for x in c.get("collateral", [])[:3]:
            print(f"      beyond its fault's reason: {x}")


# ------------------------------------------------------------------ commands
def refuse_changed_self(pins: dict) -> bool:
    """False, with the reason on stderr, when the compare_hosts.py the tree holds is not the
    one running (edited since it was imported): its pins would name code that did not run."""
    if pins.get(SELF) != RAN_BY:
        print(f"refused: {SELF} is {pins.get(SELF)} in the tree, {RAN_BY} as it runs (edited since it started): "
              f"run it again", file=sys.stderr)
        return False
    return True


def refuse_unbuilt(build: Path, pins: dict) -> bool:
    """False, with the reasons on stderr, unless the build's records match the tree."""
    prov = provenance(build, pins)
    if not prov["pass"]:
        print(f"refused: the build in {rel(build)} is not the tree this comparison pins (rebuild it with "
              f"host/player/build.sh and host/d3d9gpu/build.sh):", file=sys.stderr)
        for f in prov["failures"][:12]:
            print(f"  {f}", file=sys.stderr)
    return prov["pass"]


def cmd_run(args: argparse.Namespace) -> int:
    stream = Path(args.stream).resolve()
    out_dir = (Path(args.out) if args.out else
               DEFAULT_RUNS / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")).resolve()
    if (why := refuse(out_dir / "compare.json")):
        print(f"refused: {why}", file=sys.stderr)
        return 2
    # A comparison writes into a directory of its own: what an earlier run left (a control
    # run, a device report, a matrix) would otherwise be read back as this run's.
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        print(f"refused: {rel(out_dir)} exists and is not empty (a comparison writes into a new directory)",
              file=sys.stderr)
        return 2
    # The players and the census are run by absolute path.
    build = args.build = Path(args.build).resolve()
    started = now()
    pins = pipeline()          # what the comparison is made of, as it starts
    if not refuse_changed_self(pins) or not refuse_unbuilt(build, pins):
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    census = run_census(build, stream, args.stage, out_dir)
    if args.scope != "r2a-clears":
        account = stream_account(stream, max_frames=args.max_frames)
        if args.scope == "r2-draws-partial":
            census = bounded_census(census, account)
        else:
            for key in ("draws", "frames"):
                if account[key] != census[key]:
                    raise RuntimeError(f"independent original-stream {key} differs from the census")
            census.update({k: v for k, v in account.items() if k not in ("draws", "frames", "clears")})
        (out_dir / "census.json").write_text(json.dumps(census, indent=1) + "\n")
    print(f"census: {census['frames']} frames, {census['draws']['total']} draws, draw-free "
          f"{ranges(census['draw_free_frames'])}")
    matrix = run_matrix(build, out_dir, args.hidden)
    print(f"clear matrix: exit {matrix['exit']}, {len(matrix['lines']['dxvk'])} case(s)")
    dxvk = run_player(build, stream, args.stage, out_dir / "dxvk", sdlgpu=False, hidden=args.hidden,
                      allow_notes=args.allow_notes, max_frames=args.max_frames)
    print(f"dxvk: exit {dxvk['exit']}, check {dxvk['check'].get('pass')}")
    sdl = run_player(build, stream, args.stage, out_dir / "sdlgpu", sdlgpu=True, hidden=args.hidden,
                     allow_notes=args.allow_notes, max_frames=args.max_frames)
    print(f"sdlgpu: exit {sdl['exit']}, check {sdl['check'].get('pass')}")
    controls, control_runs = None, []
    if args.controls:
        controls = []
        for c in controls_for(args.scope):
            run = run_player(build, stream, args.stage, out_dir / f"control-{c['name']}", sdlgpu=True,
                             faults=c["faults"], hidden=args.hidden, allow_notes=args.allow_notes, max_frames=args.max_frames)
            control_runs.append(run)
            ev = {"census": census, "dxvk": dxvk, "sdl": run, "hidden": bool(args.hidden),
                  "scope": args.scope, "max_frames": args.max_frames}
            outcome = control_outcome(c, ev)
            controls.append(outcome)
            print(f"control {c['name']}: {outcome['outcome']}")
    doc = assemble(out_dir, args, census, dxvk, sdl, controls, started, pins, control_runs, matrix)
    (out_dir / "compare.json").write_text(json.dumps(doc, indent=1) + "\n")
    print_summary(doc)
    final = doc["verdict_with_controls"] if args.controls else doc["verdict"]
    return 0 if final == "PASS" else 1


def checked_run(out_dir: Path, reference: str | None = None) -> tuple[int, dict | None]:
    """Recompute a recorded comparison without writing its evidence."""
    old = read_json(out_dir / "compare.json") or {}
    scope, bound = old.get("acceptance_scope"), old.get("max_frames")
    if scope not in SCOPES or type(bound) is not int or bound < 0 or ((scope == "r2-draws-partial") != (bound > 0)):
        print("refused: no explicit valid scope and bound in compare.json; rerun it", file=sys.stderr)
        return 2, None
    census = read_json(out_dir / "census.json")
    if census is None:
        print(f"refused: no census.json in {out_dir}", file=sys.stderr)
        return 2, None
    if not get(old, "inputs.build"):
        print(f"refused: no compare.json naming its build in {out_dir}", file=sys.stderr)
        return 2, None
    # The compare_hosts.py that ran the players and the matrix: carried forward, never
    # replaced by the one checking (the receipt refuses a run whose two differ).
    ran_by = get(old, "inputs.ran_by")
    if not ran_by:
        print(f"refused: the compare.json in {out_dir} names no compare_hosts.py that ran it (inputs.ran_by): "
              f"rerun the comparison", file=sys.stderr)
        return 2, None
    build = (ROOT / old["inputs"]["build"]).resolve()
    # The reference the run held (check h), unless --reference names another.
    held = next((get(c, "detail.reference") for c in old.get("checks") or [] if c.get("check") == "h"), None)
    ns = argparse.Namespace(stream=str(ROOT / old.get("stream", {}).get("path", census["stream"])),
                            hidden=old.get("hidden", False), build=str(build), reference=reference or held,
                            scope=old.get("acceptance_scope"), max_frames=old.get("max_frames"))
    # Recomputed from the files the run left, against the build that made them: the build
    # must still be the one the run recorded, and still match the tree.
    pins = pipeline()
    if not refuse_changed_self(pins):
        return 2, None
    then = {k: v for k, v in (old.get("inputs", {}).get("files") or {}).items() if k in BINARIES}
    now_ = {k: sha256(build / k) if (build / k).is_file() else None for k in then}
    if not then or then != now_:
        print(f"refused: the build in {rel(build)} is not the one the run recorded ({then} then, {now_} now)",
              file=sys.stderr)
        return 2, None
    if not refuse_unbuilt(build, pins):
        return 2, None
    # ... and the files it reads must be the ones the run wrote.
    recorded, present = old.get("outputs") or {}, recorded_outputs(out_dir)
    if not recorded or recorded != present:
        moved = sorted(k for k in set(recorded) | set(present) if recorded.get(k) != present.get(k))
        print(f"refused: the files in {out_dir} are not the ones the run recorded (outputs): "
              f"{[(k, recorded.get(k, 'absent'), present.get(k, 'absent')) for k in moved[:6]]}", file=sys.stderr)
        return 2, None
    if scope != "r2a-clears":
        original = Path(ns.stream)
        if not original.is_file() or sha256(original) != get(old, "stream.sha256"):
            print("refused: the original stream no longer matches the recorded hash", file=sys.stderr)
            return 2, None
        try:
            account = stream_account(original, max_frames=bound)
        except (OSError, ValueError, stream_dump.StreamError) as exc:
            print(f"refused: original-stream account: {exc}", file=sys.stderr)
            return 2, None
        if (any(census.get(k) != v for k, v in account.items() if k != "clears")
                or get(census, "clears.host_calls") != get(account, "clears.host_calls")):
            print("refused: the saved census differs from the original consumed records and UP layout", file=sys.stderr)
            return 2, None
    dxvk, sdl = load_run(out_dir / "dxvk"), load_run(out_dir / "sdlgpu")
    # The controls the run ran, by its own record (not by which directories exist).
    controls, control_runs = None, []
    if get(old, "controls.run") is True:
        controls = []
        for c in controls_for(ns.scope):
            run = load_run(out_dir / f"control-{c['name']}")
            control_runs.append(run)
            controls.append(control_outcome(c, {"census": census, "dxvk": dxvk, "sdl": run, "hidden": ns.hidden,
                                              "scope": ns.scope, "max_frames": ns.max_frames}))
    # Each run's verdict (check.json) is play_report.py's at run time: reused only when that
    # is the play_report.py this check pins.
    if (stale := checker_failures([dxvk, sdl, *control_runs], pins)):
        print("refused: a run's check.json was written by another play_report.py than the one this check pins "
              "(rerun the comparison):", file=sys.stderr)
        for f in stale[:8]:
            print(f"  {f}", file=sys.stderr)
        return 2, None
    doc = assemble(out_dir, ns, census, dxvk, sdl, controls, old.get("started", now()), pins, control_runs,
                   load_matrix(out_dir), ran_by=ran_by)
    final = doc["verdict_with_controls"] if controls is not None else doc["verdict"]
    return (0 if final == "PASS" else 1), doc


def cmd_check(args: argparse.Namespace) -> int:
    out_dir = Path(args.run)
    status, doc = checked_run(out_dir, args.reference)
    if doc is None:
        return status
    text = json.dumps(doc, indent=1) + "\n"
    document_sha = hashlib.sha256(text.encode()).hexdigest()
    (out_dir / "compare.json").write_text(text)
    args.checked_document_sha256 = document_sha
    print_summary(doc)
    ran_by, pinned = doc["inputs"]["ran_by"], doc["inputs"]["pipeline"].get(SELF)
    if ran_by != pinned:
        print(f"  note: the runs were made by {SELF} {ran_by}, checked by {pinned}: no receipt takes this "
              f"run (rerun the comparison)")
    return status


def flatten(d, prefix: str = "") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        if not d and prefix:
            out[prefix] = {}
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(flatten(v, f"{prefix}[{i}]"))
        if not d:
            out[prefix] = []
    else:
        out[prefix] = d
    return out


def allowed(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pat) for pat in DXVK_AB_ALLOWED)


def split_key(path: str) -> str | None:
    """The DXVK_AB_SPLIT field a flattened report path is (or is inside), or None."""
    return next((k for k in DXVK_AB_SPLIT if path == k or path.startswith((k + ".", k + "["))), None)


def same_json(a, b) -> bool:
    """`a` equals `b` as JSON values: Python's == with every type held, at every depth
    (False is not 0, 0 is not 0.0, None is not absent)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same_json(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same_json(x, y) for x, y in zip(a, b))
    return a == b


def compare_dxvk_ab(before: dict, after: dict, *, before_expected_d3d9gpu: str | None,
                    own_confs: dict[str, list[str]] | None = None,
                    own_libraries: dict[str, list[str]] | None = None) -> dict:
    """The DXVK path unchanged: every times column but wall_us, row for row, and every
    report field outside DXVK_AB_ALLOWED and DXVK_AB_SPLIT; each split field one of the
    values its side may hold, read whole and held type for type (same_json; OWN_CONTRACT:
    `before_expected_d3d9gpu`, the before build's own expectation of the second host's
    library, None when its contract has none). A report field whose value is exactly its
    side's own checkout's dxvk.conf (own_confs[side], each side running under its own
    checkout's player_env.sh) reads as OWN_CONF, and one whose value is exactly its side's
    own install's DXVK library (own_libraries[side], own_library) as OWN_LIBRARY; a path
    that is not its side's own is compared as it is."""
    failures: list[str] = []
    ta, tb = before["times"], after["times"]
    if not ta or not tb:
        failures.append(f"times: before {len(ta)} rows, after {len(tb)}")
    if ta and tb and set(ta[0]) != set(tb[0]):
        failures.append(f"times columns: before {sorted(ta[0])}, after {sorted(tb[0])}")
    if len(ta) != len(tb):
        failures.append(f"times rows: before {len(ta)}, after {len(tb)}")
    cols = sorted(set(ta[0]) - {"wall_us"}) if ta else []
    differ = [i + 1 for i, (x, y) in enumerate(zip(ta, tb)) if any(x.get(c) != y.get(c) for c in cols)]
    if differ:
        failures.append(f"times: {len(differ)} row(s) differ outside wall_us, first at row {differ[0]}")
    own_confs, own_libraries = own_confs or {}, own_libraries or {}
    fa, fb = flatten(before["report"]), flatten(after["report"])
    as_own: dict[str, list[str]] = {}
    as_own_library: dict[str, list[str]] = {}
    for side, flat in (("before", fa), ("after", fb)):
        confs, libraries = own_confs.get(side) or [], own_libraries.get(side) or []
        as_own[side] = sorted(k for k, v in flat.items() if isinstance(v, str) and v in confs)
        as_own_library[side] = sorted(k for k, v in flat.items() if isinstance(v, str) and v in libraries
                                      and k not in as_own[side])
        for k in as_own[side]:
            flat[k] = OWN_CONF
        for k in as_own_library[side]:
            flat[k] = OWN_LIBRARY
    outside, inside, split = [], [], set()
    for key in sorted(set(fa) | set(fb)):
        if fa.get(key, ABSENT) == fb.get(key, ABSENT):
            continue
        if (k := split_key(key)) is not None:
            split.add(k)
        else:
            (inside if allowed(key) else outside).append(key)
    for key in outside[:20]:
        failures.append(f"report {key}: before {fa.get(key, ABSENT)!r}, after {fb.get(key, ABSENT)!r}")
    if len(outside) > 20:
        failures.append(f"... {len(outside)} report field(s) differ outside the allowlist")
    # Each split field, on each side, one of the values that side may hold (read whole, not
    # flattened: a dict where None is wanted is not None).
    values = {}
    for key, sides in DXVK_AB_SPLIT.items():
        values[key] = {}
        for side, run in (("before", before), ("after", after)):
            have = get(run["report"], key) if has(run["report"], key) else ABSENT
            values[key][side] = have
            may = [before_expected_d3d9gpu if v == OWN_CONTRACT else v for v in sides[side]]
            # (an own contract with no such key: the field may not be there with any value)
            may = [v for v, w in zip(may, sides[side]) if not (w == OWN_CONTRACT and v is None)]
            if not any(same_json(have, v) for v in may):
                failures.append(f"{side}: {key} is {have!r}, not {' or '.join(map(repr, may))}")
    for host, run in (("before", before), ("after", after)):
        if run["exit"] != 0 or run["check"].get("pass") is not True:
            failures.append(f"{host}: exit {run['exit']}, play_report.py check {run['check'].get('pass')}")
    return {"pass": not failures, "failures": failures, "times_rows": len(ta), "times_columns_compared": cols,
            "report_fields_differing_inside_allowlist": inside, "report_fields_differing_outside_allowlist": outside,
            "allowlist": list(DXVK_AB_ALLOWED),
            "split_fields": {k: values[k] for k in DXVK_AB_SPLIT},
            "split_fields_differing": sorted(split),
            "own_conf": {"paths": {k: list(v) for k, v in own_confs.items()}, "fields": as_own},
            "own_library": {"paths": {k: list(v) for k, v in own_libraries.items()}, "fields": as_own_library},
            "split_rule": {k: {side: [OWN_CONTRACT if v == OWN_CONTRACT else v for v in vs] for side, vs in sides.items()}
                           for k, sides in DXVK_AB_SPLIT.items()}}


def log_difference(a: Path, b: Path, own_confs: dict[str, list[str]] | None = None) -> dict:
    """DXVK's two logs, line by line: every line that differs, as (-before / +after), and
    the failures, one per differing line no DXVK_AB_LOG_ALLOWED pattern matches on both
    sides (and a log that is not there). Each side's own checkout's dxvk.conf
    (own_confs[side]: DXVK's "Found config file" line) reads as OWN_CONF in its lines."""
    def lines(p, confs):
        if not p.is_file():
            return None
        text = p.read_text(errors="replace")
        for c in sorted(confs or [], key=len, reverse=True):
            text = text.replace(c, OWN_CONF)
        return text.splitlines()
    la, lb = lines(a, (own_confs or {}).get("before")), lines(b, (own_confs or {}).get("after"))
    failures = [f"{n}: no DXVK log ({p})" for n, p, ls in (("before", a, la), ("after", b, lb)) if ls is None]
    la, lb = la or [], lb or []
    differing = []
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<none>"
        y = lb[i] if i < len(lb) else "<none>"
        if x == y:
            continue
        differing.append(f"line {i + 1}: -{x} / +{y}")
        if not any(re.fullmatch(pat, x) and re.fullmatch(pat, y) for pat in DXVK_AB_LOG_ALLOWED):
            failures.append(f"DXVK's log line {i + 1} differs outside DXVK_AB_LOG_ALLOWED: -{x} / +{y}")
    return {"differing": differing, "failures": failures}


def git_bytes(*args: str) -> bytes | None:
    """git's output in GIT_REPO (read-only commands), or None when it fails."""
    p = subprocess.run(["git", "-C", str(GIT_REPO), *args], capture_output=True)
    return p.stdout if p.returncode == 0 else None


def commit_of(commit: str) -> str | None:
    """`commit` as a full commit id in GIT_REPO, or None when it names none."""
    return (git_bytes("rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}") or b"").decode().strip() or None


def blob_sha(full: str, path: str) -> str | None:
    """The sha256 of `path` at commit `full`, or None when the commit holds no such file."""
    blob = git_bytes("cat-file", "blob", f"{full}:{path}")
    return hashlib.sha256(blob).hexdigest() if blob is not None else None


def before_provenance(before: Path, commit: str) -> dict:
    """Whether the before build is a build of commit C's DXVK path, and runs as one: its
    record (host/player/build_record.py's sa2_player.json) names the player in the directory
    and its generated inputs as they are; its sources are C's (git cat-file), none from
    outside its checkout (its tools/pins.json the clone's: host/d3d9gpu/build_before.sh
    builds with SA2_REPO the clone); and its checkout (the record's tree), under whose
    run_player.sh and player_env.sh dxvk-ab runs it, holds C's RUNTIME files and dxvk.conf.
    A group record (sa2-build-record/v1, every commit before the two binaries) names as its
    sources exactly C's files under BEFORE_SOURCES; an exact record (v2) names what the
    player's compiles read, every file of it C's, and among them host/player/player.cpp
    and build.sh."""
    f: list[str] = []
    full = commit_of(commit)
    if full is None:
        f.append(f"--before-commit {commit!r} is no commit in {GIT_REPO}")
    rec = read_json(before / "sa2_player.json")
    player = sha256(before / "sa2_player") if (before / "sa2_player").is_file() else None
    held = 0
    if not isinstance(rec, dict) or not all(isinstance(rec.get(k), dict) for k in ("binaries", "sources")):
        f.append(f"{rel(before)}: no build record (sa2_player.json; host/d3d9gpu/test_d3d9gpu.sh builds the before "
                 f"player with one)")
        rec = None
    else:
        if rec["binaries"].get("sa2_player") != player:
            f.append(f"{rel(before)}: sa2_player is {player}, not the {rec['binaries'].get('sa2_player')} its record names")
        for path, want in (rec.get("generated") or {}).items():
            have = sha256(before / path) if (before / path).is_file() else None
            if have != want:
                f.append(f"{rel(before)}: generated {path} is {have}, {want} in its record")
    exact = bool(rec) and rec.get("schema") == EXACT_RECORD
    outside = sorted(p for p in (rec or {}).get("sources", {}) if Path(p).is_absolute())
    for path in outside:
        f.append(f"{rel(before)}: its record names {path}, outside its checkout, which no commit holds (build it "
                 f"with host/d3d9gpu/build_before.sh, which reads the clone alone)")
    if full and rec and not exact:
        listed = (git_bytes("ls-tree", "-r", "--name-only", full, "--", *BEFORE_SOURCES) or b"").decode().splitlines()
        if not listed:
            f.append(f"commit {full} holds none of {BEFORE_SOURCES}")
        for path in listed:
            want = blob_sha(full, path)
            if rec["sources"].get(path) != want:
                f.append(f"{rel(before)}: source {path} is {rec['sources'].get(path)} in its record, {want} at {full[:12]}")
            held += 1
        for path in sorted(rec["sources"]):
            if not Path(path).is_absolute() and path not in listed:
                f.append(f"{rel(before)}: its record names {path}, which commit {full[:12]} does not hold")
    if full and rec and exact:
        for path, want in sorted(rec["sources"].items()):
            if Path(path).is_absolute():
                continue
            have = blob_sha(full, path)
            if have is None:
                f.append(f"{rel(before)}: its record names {path}, which commit {full[:12]} does not hold")
            elif have != want:
                f.append(f"{rel(before)}: source {path} is {want} in its record, {have} at {full[:12]}")
            held += 1
        for path in ("host/player/player.cpp", "host/player/build.sh"):
            if path not in rec["sources"]:
                f.append(f"{rel(before)}: its exact record does not name {path}")
    # Its checkout: what dxvk-ab runs it under (run_player.sh, player_env.sh, and the dxvk.conf
    # that names), each C's.
    tree = Path(rec["tree"]) if rec and isinstance(rec.get("tree"), str) else None
    runtime: dict[str, str | None] = {}
    if rec and tree is None:
        f.append(f"{rel(before)}: its record names no checkout (tree), whose run-time files it runs under")
    if full and tree is not None:
        for path in (*RUNTIME, DXVK_CONF):
            runtime[path] = sha256(tree / path) if (tree / path).is_file() else None
            if runtime[path] != (want := blob_sha(full, path)):
                f.append(f"{rel(before)}: its checkout's {path} is {runtime[path]}, {want} at {full[:12]}")
    return {"pass": not f, "failures": f, "commit": full, "player": player, "sources_held": held,
            "record": sha256(before / "sa2_player.json") if (before / "sa2_player.json").is_file() else None,
            "record_schema": (rec or {}).get("schema"), "checkout": str(tree) if tree else None,
            "runtime": runtime}


# The tree's directories a compile of the DXVK player searches before the install's headers
# (host/player/build.sh): a quoted include's own directory (host/player, and host/ for the
# stream header's) and -I host/ (STREAM_H_DIR), which angle includes search too.
INCLUDE_DIRS = ("host/player", "host")
FILE_MODES = ("100644", "100755")


def system_include_dirs(compiler: dict | None) -> tuple[list[str] | None, str | None]:
    """The directories the build's compiler searched after every -I, which is where the
    system's headers are, none of them in an -MMD depfile: the search list build_record.py
    recorded from the compiler itself in the build's environment (compiler.search), when
    the record's compiler is still the compiler that list is of (its --version line now the
    recorded one). (list, None), or (None, why) when the record names no compiler or no
    list, or the compiler cannot be run or is another now."""
    if not isinstance(compiler, dict):
        return None, "names no compiler"
    cmd, version, search = compiler.get("command"), compiler.get("version"), compiler.get("search")
    if not isinstance(search, list) or not search or not all(isinstance(d, str) and d for d in search):
        return None, "records no include search list (build_record.py compiler.search)"
    if not isinstance(cmd, str) or not cmd.strip() or not isinstance(version, str):
        return None, "names no command and version"
    try:
        p = subprocess.run([*shlex.split(cmd), "--version"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=120)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return None, f"cannot be run ({exc})"
    now_version = p.stdout.splitlines()[0] if p.returncode == 0 and p.stdout else None
    if now_version != version:
        return None, (f"is another compiler now ({cmd} --version: {now_version!r}, recorded {version!r}): its "
                      f"recorded search list may not be this one's")
    return list(search), None


def shadowing_at(full: str, rec: dict, system_dirs: list[str]) -> dict[str, str] | None:
    """The files commit `full` holds under INCLUDE_DIRS, at any depth, at a path an include
    could name, which this build did not read, each with why it is named: at the commit,
    that include could have found the file first. Such a path is an
    include directory, then either any trailing part of the path of a file this build read
    ("SDL3/SDL.h" of the install's header names host/SDL3/SDL.h), or the path of a header
    under one of `system_dirs` (the compiler's search list: "cerrno" names host/cerrno,
    which an -MMD depfile, naming no system header, cannot show). (A file at such a path now
    is in the record when a compile read it; a source of the record is held by its hash
    instead.) And every entry the commit holds there that is not a file (a symbolic link,
    whose target no commit holds and through which an include could find anything, or a
    submodule), whether or not this build read its path. None when git cannot list the
    commit's tree."""
    listing = git_bytes("ls-tree", "-r", "-z", full, "--", *(d + "/" for d in INCLUDE_DIRS))
    if listing is None:
        return None
    held, entries = set(), {}
    for entry in filter(None, listing.split(b"\0")):
        meta, _, name = entry.partition(b"\t")
        mode, kind = meta.decode().split(" ")[:2]
        path = os.fsdecode(name)
        if mode in FILE_MODES:
            held.add(path)
        else:
            entries[path] = (f"at the commit, a {'symbolic link' if mode == '120000' else kind + ' entry'} (mode "
                             f"{mode}) under an include directory, through which an include could find what the "
                             f"commit does not hold")
    read = [*(rec.get("sources") or {}), *(rec.get("external") or {}), *(rec.get("generated") or {})]
    tails = set()
    for path in read:
        parts = Path(path).parts[1:] if Path(path).is_absolute() else Path(path).parts
        tails.update("/".join(parts[i:]) for i in range(len(parts)))
    candidates = {f"{d}/{t}" for d in INCLUDE_DIRS for t in tails}
    own = set(rec.get("sources") or {})
    before = {p: "what this build read" for p in (candidates & held) - own}
    for path in sorted(held - own - set(before)):
        for d in INCLUDE_DIRS:
            name = path[len(d) + 1:] if path.startswith(d + "/") else None
            found = next((f"{s}/{name}" for s in system_dirs if name and os.path.isfile(os.path.join(s, name))), None)
            if found:
                before[path] = f"the compiler's system header {found}"
                break
    out = {p: f"at the commit, where an include could find it before {why}" for p, why in before.items()}
    return dict(sorted({**out, **entries}.items()))


def dxvk_path(build: Path, full: str) -> dict:
    """The DXVK path of `build` (its sa2_player.json, which provenance holds to this tree)
    item by item against commit `full`: every source of the DXVK player's exact record (what
    its compiles read, its build script, generator, dxvk.conf and pins), each RUNTIME file
    as this tree holds it, and every file the commit holds where an include of a file this
    build read, or of a system header, could have found it first (shadowing_at). `differing`
    names each that is not the commit's; empty, the DXVK path is the commit's by
    construction. These are exactly what dxvk-ab varies between its sides (the before built
    and run from a clone at the commit), so one rule says UNCHANGED (dxvk-unchanged) and
    refuses an A/B as comparing nothing (dxvk-ab). `refusal` is set, and nothing is held,
    when the record gives no search list this check can use (system_include_dirs): without
    it a file shadowing a system header could not be seen."""
    rec = read_json(build / "sa2_player.json") or {}
    system_dirs, why = system_include_dirs(rec.get("compiler"))
    if system_dirs is None:
        compiler = rec.get("compiler")
        name = compiler.get("command") if isinstance(compiler, dict) else compiler
        return {"refusal": f"sa2_player.json's compiler ({name!r}) {why}: a file of the commit ahead of a system "
                           f"header could not be seen"}
    sources = {p: {"build": h, "commit": blob_sha(full, p)} for p, h in sorted((rec.get("sources") or {}).items())}
    runtime = {p: {"tree": sha256(ROOT / p) if (ROOT / p).is_file() else None, "commit": blob_sha(full, p)}
               for p in RUNTIME}
    shadows = shadowing_at(full, rec, system_dirs)
    if shadows is None:
        return {"refusal": f"git cannot list commit {full[:12]}'s {', '.join(INCLUDE_DIRS)} (ls-tree)"}
    differing = [p for p, v in sources.items() if v["build"] != v["commit"]]
    differing += [p for p, v in runtime.items() if v["tree"] != v["commit"]]
    differing += [f"{p}: {why}" for p, why in shadows.items()]
    return {"refusal": None, "sources": sources, "runtime": runtime, "shadowing": list(shadows),
            "system_include_dirs": system_dirs, "differing": differing}


def own_conf(checkout: Path | str) -> list[str]:
    """A checkout's dxvk.conf as its player_env.sh names it and as it resolves (OWN_CONF)."""
    p = Path(checkout) / DXVK_CONF
    return sorted({str(p), os.path.realpath(p)})


def own_library(checkout: Path | str) -> list[str]:
    """A checkout's DXVK library as its player_env.sh names it and as it resolves (OWN_LIBRARY)."""
    p = Path(checkout) / DXVK_LIBRARY
    return sorted({str(p), os.path.realpath(p)})


def cmd_dxvk_ab(args: argparse.Namespace) -> int:
    stream = Path(args.stream).resolve()
    out_dir = (Path(args.out) if args.out else DEFAULT_RUNS / (
        "dxvk-ab-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))).resolve()
    if (why := refuse(out_dir / "dxvk-ab.json")):
        print(f"refused: {why}", file=sys.stderr)
        return 2
    args.before, args.after = Path(args.before).resolve(), Path(args.after).resolve()
    # The "after" side is this tree's build, held to it as `run` holds its build (its
    # sa2_player.json the DXVK player's exact record, built from nothing of the second
    # host's, its contract DXVK's), run under this tree's run-time files; "before" is a build
    # of the player from a clone at --before-commit, held to that commit by its record and
    # run under its clone's run-time files. The same player on both sides compares nothing.
    pins = pipeline()
    if not refuse_changed_self(pins) or not refuse_unbuilt(args.after, pins):
        return 2
    players = {n: sha256(b / "sa2_player") if (b / "sa2_player").is_file() else None
               for n, b in (("before", args.before), ("after", args.after))}
    if players["before"] == players["after"]:
        print(f"refused: before and after are the same player ({players['after']}): an A/B of one build "
              f"compares nothing", file=sys.stderr)
        return 2
    bprov = before_provenance(args.before, args.before_commit)
    if not bprov["pass"]:
        print(f"refused: the before build in {rel(args.before)} is not held to commit {args.before_commit}:",
              file=sys.stderr)
        for f in bprov["failures"][:12]:
            print(f"  {f}", file=sys.stderr)
        return 2
    # An A/B of the DXVK path needs a change in it: by dxvk-unchanged's own rule (dxvk_path),
    # whose items are exactly what the two sides differ in (the before held to the commit).
    path = dxvk_path(args.after, bprov["commit"])
    if path["refusal"]:
        print(f"refused: {path['refusal']}", file=sys.stderr)
        return 2
    changed = path["differing"]
    if not changed:
        print(f"refused: the after's DXVK path is commit {bprov['commit'][:12]}'s (all "
              f"{len(path['sources'])} sources of its sa2_player.json and its {len(RUNTIME)} run-time files the "
              f"commit's, no file of the commit shadowing an include): an A/B of two builds of one DXVK path "
              f"compares nothing (compare_hosts.py dxvk-unchanged says UNCHANGED, by the same rule)", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    checkouts = {"before": Path(bprov["checkout"]), "after": ROOT}
    runs = {}
    for name, build in (("before", args.before), ("after", args.after)):
        runs[name] = run_player(build, stream, args.stage, out_dir / name, sdlgpu=False, hidden=args.hidden,
                                allow_notes=args.allow_notes, checkout=checkouts[name])
    # The before's own expectation of the second host's library, where its player had one
    # (its expected/expectations.json, which before_provenance held to its record).
    own = get(read_json(args.before / "expected" / "expectations.json"), "contract.d3d9gpu_sha256")
    confs = {n: own_conf(c) for n, c in checkouts.items()}
    libraries = {n: own_library(c) for n, c in checkouts.items()}
    res = compare_dxvk_ab(runs["before"], runs["after"], before_expected_d3d9gpu=own, own_confs=confs,
                          own_libraries=libraries)
    prov = provenance(args.after, pins, [runs["after"]])
    if not prov["pass"]:
        res["pass"] = False
        res["failures"] = res["failures"] + [f"after: {f}" for f in prov["failures"]]
    if get(runs["before"]["report"], "files.player.sha256") != players["before"]:
        res["pass"] = False
        res["failures"].append(f"before: the player that ran is not {rel(args.before)}/sa2_player ({players['before']})")
    res["after_provenance"] = prov
    res["before_provenance"] = bprov
    logs = {n: Path(r["report"].get("dxvk", {}).get("log") or "/nonexistent") for n, r in runs.items()}
    ld = log_difference(logs["before"], logs["after"], own_confs=confs)
    res["dxvk_log_lines_differing"] = ld["differing"][:40]
    res["dxvk_log_allowed"] = list(DXVK_AB_LOG_ALLOWED)
    if ld["failures"]:
        res["pass"] = False
        res["failures"] += ld["failures"][:20]
    doc = {"schema": "sa2-s5-dxvk-ab/v1", "tool": "tools/s5/compare_hosts.py dxvk-ab", "written": now(),
           "stream": {"path": rel(stream), "sha256": runs["after"]["report"].get("stream_sha256")},
           "stage": args.stage, "hidden": bool(args.hidden),
           "players": {n: {"build": rel(b), "sha256": players[n]}
                       for n, b in (("before", args.before), ("after", args.after))},
           # the change the A/B covers: what the after's DXVK path holds that the commit's does
           # not (dxvk_path: sources, run-time files, shadowing files), which the sides differ in
           "host_sources_differing": changed,
           "pipeline": pins, "ran_by": RAN_BY,
           # the run-time files each side ran under: its own checkout's (the before's held to
           # the commit by before_provenance)
           "runtime": {n: {"checkout": str(c), **{p: sha256(c / p) if (c / p).is_file() else None
                                                  for p in (*RUNTIME, DXVK_CONF)}}
                       for n, c in checkouts.items()},
           "outputs": {f"{n}/{f}": sha256(out_dir / n / f) for n in runs
                       for f in ("report.json", "frames.y4m.times.tsv") if (out_dir / n / f).exists()},
           "verdict": "PASS" if res["pass"] else "FAIL", **res}
    (out_dir / "dxvk-ab.json").write_text(json.dumps(doc, indent=1) + "\n")
    print(f"dxvk-ab {rel(stream)} stage {args.stage}: {doc['verdict']} ({res['times_rows']} rows; "
          f"{len(res['report_fields_differing_inside_allowlist'])} allowed field(s) differ: "
          f"{res['report_fields_differing_inside_allowlist'][:8]}; split fields differing "
          f"{res['split_fields_differing']}; before {bprov['commit'][:12]}; "
          f"{len(changed)} differ: {changed[:8]})")
    for f in res["failures"][:10]:
        print(f"  {f}")
    return 0 if res["pass"] else 1


# dxvk-unchanged: what it does not hold to the commit, said in its document.
UNCHANGED_NOT_HELD = (
    "the compiler (sa2_player.json compiler: its command, version and search list) and the install's headers "
    "(sa2_player.json external): recorded, not held to the commit; a toolchain change between the commit and this "
    "build is invisible here, and the compiler is held to be the build's by its --version line alone",
    "the system's headers and libraries (not in a depfile): their bytes are not the tree's; a file of the commit "
    "where an include could find it before a header in the compiler's system directories is held (shadowing_at), by "
    "the search list build_record.py recorded from the compiler in the build's environment (system_include_dirs: "
    "CPATH and CPLUS_INCLUDE_PATH as the build had them), its headers looked for where this check runs",
    "the libraries the player loads: held at run time to tools/pins.json (a source held here) by the player itself",
    "host/player/build_record.py, which wrote sa2_player.json: it makes no byte of the player; what it recorded is "
    "held to the record's own depfiles by provenance (depfiles_unrecorded), which reads them with build_record.py's "
    "own depfile reader: the record checked against its depfiles, not an independent parse of them",
    "a file of the commit that only a __has_include test could find (no file a compile read, and no header in the "
    "compiler's system directories, names its path)",
)


def cmd_dxvk_unchanged(args: argparse.Namespace) -> int:
    """Whether the build's DXVK path is commit C's by construction (exit 0 UNCHANGED), or
    not (1 CHANGED): dxvk_path's items, each as C holds it. 2: refused."""
    out_dir = (Path(args.out) if args.out else DEFAULT_RUNS / (
        "dxvk-unchanged-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))).resolve()
    if (why := refuse(out_dir / "dxvk-unchanged.json")):
        print(f"refused: {why}", file=sys.stderr)
        return 2
    after = Path(args.after).resolve()
    pins = pipeline()
    # held to the tree: its sa2_player.json the DXVK player's exact record (provenance)
    if not refuse_changed_self(pins) or not refuse_unbuilt(after, pins):
        return 2
    full = commit_of(args.before_commit)
    if full is None:
        print(f"refused: --before-commit {args.before_commit!r} is no commit in {GIT_REPO}", file=sys.stderr)
        return 2
    rec = read_json(after / "sa2_player.json") or {}
    outside = sorted(p for p in rec.get("sources", {}) if Path(p).is_absolute())
    if outside:
        print(f"refused: sa2_player.json names sources outside the tree, which no commit holds: {outside}",
              file=sys.stderr)
        return 2
    path = dxvk_path(after, full)
    if path["refusal"]:
        print(f"refused: {path['refusal']}", file=sys.stderr)
        return 2
    differing = path["differing"]
    verdict = "CHANGED" if differing else "UNCHANGED"
    doc = {"schema": "sa2-s5-dxvk-unchanged/v3", "tool": "tools/s5/compare_hosts.py dxvk-unchanged",
           "written": now(), "after": rel(after), "before_commit": full,
           "player": {"sa2_player": sha256(after / "sa2_player") if (after / "sa2_player").is_file() else None,
                      "record": sha256(after / "sa2_player.json")},
           "sources": path["sources"], "runtime": path["runtime"], "shadowing": path["shadowing"],
           "system_include_dirs": path["system_include_dirs"], "differing": differing,
           "not_held": list(UNCHANGED_NOT_HELD), "compiler": rec.get("compiler"),
           "external": len(rec.get("external") or {}), "pipeline": pins, "ran_by": RAN_BY, "verdict": verdict}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dxvk-unchanged.json").write_text(json.dumps(doc, indent=1) + "\n")
    if differing:
        print(f"dxvk-unchanged {full[:12]}: CHANGED, {len(differing)} differ: {differing[:8]}")
        return 1
    print(f"dxvk-unchanged {full[:12]}: UNCHANGED by construction ({len(path['sources'])} sources of sa2_player.json "
          f"{doc['player']['record'][:16]} and {len(path['runtime'])} run-time files are the commit's, byte for "
          f"byte; no file of the commit shadows an include)")
    return 0


RECEIPT_KEYS = {"schema", "id", "generated_by", "written", "question", "answer", "scope", "inputs", "stream",
                "stage", "hidden", "runs", "checks", "controls", "comparable_frames", "counts", "clear_matrix",
                "other_runs", "verdict", "acceptance_scope", "marker", "max_frames"}


OTHER_RUNS_SCOPE = (
    " Each other_runs entry is another stream compared by the same pipeline and the same binaries (the two "
    "players, the census): its own evidence is its checks and, where it ran them, its controls; an entry with no "
    "controls of its own rests on the controls of this receipt's run, which share its pipeline and binaries, "
    "and on nothing else.")


MATRIX_SCOPE = (
    " The comparable frames are made by clears alone (check f) and compared by one hash each, so a fault in a "
    "partial rectangle, a channel order, or the row order or pitch of a read back shows on them only where their "
    "own clears exercise it: a frame cleared whole in one colour exercises the colour and the read back reading "
    "something, and none of the rest. clear_matrix holds the rest for this build: check i's cases (partial "
    "rectangles, scissor, non-grey colours, a render-target texture), each image read back with "
    "GetRenderTargetData and LockRect as the player reads its frames, equal line for line through the DXVK run's "
    "library, this build's second-host player on SDL_gpu and the CPU model.")


def comparable_evidence(doc: dict) -> dict:
    """What the run's comparable frames show: DXVK's distinct hashes on them, each marked
    black or not (check e's detail)."""
    d = next(c for c in doc["checks"] if c["check"] == "e")["detail"]
    return {"comparable_hashes": d.get("comparable_hashes"), "black_hash": d.get("black_hash"),
            "comparable_not_black": d.get("comparable_not_black")}


def other_run(doc: dict, run_dir: str) -> dict:
    """Another stream's comparison by the same pipeline and binaries (Metal Harbor's, the
    critic's A6; the synthetic stream's, the review's finding 2), as one entry of the
    receipt: its checks, its comparable frames and what they show, and its controls when
    it ran them. A control that ran and did not behave fails the entry; one the stream does
    not exercise is recorded as such."""
    e = next(c for c in doc["checks"] if c["check"] == "e")
    controls = [{k: c[k] for k in ("name", "faults", "check", "want", "got", "exercised", "outcome")}
                for c in doc.get("controls", {}).get("results", [])]
    verdict = doc["verdict"]
    if any(c["outcome"] == "NOT as required" for c in controls):
        verdict = "FAIL"
    return {"run_dir": run_dir, "stream": doc["stream"], "stage": doc["stage"], "hidden": doc["hidden"],
            "verdict": verdict, "outputs": doc["outputs"],
            "checks": [{"check": c["check"], "title": c["title"], "pass": c["pass"], "failures": c["failures"]}
                       for c in doc["checks"]],
            "controls": controls,
            "comparable_frames": e["detail"]["comparable_frames"],
            "counts": {"frames": doc["census"]["frames"], "draws": doc["census"]["draws"],
                       "comparable": e["detail"]["comparable"], **comparable_evidence(doc)},
            "live_desktop_session": {h: r.get("live_desktop_session") for h, r in doc["runs"].items()}}


def build_receipt(doc: dict, run_dir: str, others: list[tuple[dict, str]] = (), *, receipt_id: str) -> dict:
    """The tracked receipt `receipt_id`, from compare.json alone: hashes, counts, frame
    numbers and verdicts. Nothing of the stream's bytes (AGENTS.md rule 9)."""
    e = next(c for c in doc["checks"] if c["check"] == "e")
    b = next(c for c in doc["checks"] if c["check"] == "b")
    g = next(c for c in doc["checks"] if c["check"] == "g")
    i = next(c for c in doc["checks"] if c["check"] == "i")
    prov = doc["inputs"]["provenance"]
    rec = {
        "schema": RECEIPT_SCHEMA,
        "acceptance_scope": doc["acceptance_scope"], "marker": acceptance_marker(doc), "max_frames": doc["max_frames"],
        "id": receipt_id,
        "generated_by": "tools/s5/compare_hosts.py receipt",
        "written": now(),
        "question": "R2a (private/generated/s5/r2a/PLAN.md): does the second host (host/d3d9gpu on SDL_gpu, no "
                    "shader route) play the stream to END answering every call as DXVK Native does, take every draw "
                    "as DXVK does (counted, none drawn), and give every frame no draw call reached the same pixels?",
        "answer": "yes" if doc["verdict_with_controls"] == "PASS" else "no",
        "scope": doc["scope"] + (MATRIX_SCOPE if doc["acceptance_scope"] == "r2a-clears" else
                                     " The independent clear matrix still checks rectangles, scissor, channel and row order and pitch.") + (OTHER_RUNS_SCOPE if others else ""),
        "inputs": {"pipeline": doc["inputs"]["pipeline"], "ran_by": doc["inputs"]["ran_by"],
                   "files": doc["inputs"]["files"],
                   "build_records": prov["records"], "binaries": prov["binaries"],
                   "census_sha256": doc["inputs"]["census"]["sha256"], "outputs": doc["outputs"],
                   "run_dir": run_dir},
        "stream": doc["stream"],
        "stage": doc["stage"],
        "hidden": doc["hidden"],
        "runs": {h: {k: v for k, v in r.items() if k != "dir"} | {"device": (b["detail"].get("sdl_gpu") if h == "sdlgpu"
                                                                             else g["detail"].get("dxvk"))}
                 for h, r in doc["runs"].items()},
        "checks": [{"check": c["check"], "title": c["title"], "pass": c["pass"], "failures": c["failures"]}
                   for c in doc["checks"]],
        "controls": [{k: c[k] for k in ("name", "faults", "check", "want", "got", "exercised", "outcome", "why")}
                     for c in doc["controls"].get("results", [])],
        "comparable_frames": e["detail"]["comparable_frames"],
        "counts": {"frames": doc["census"]["frames"], "draws": doc["census"]["draws"],
                   "comparable": e["detail"]["comparable"], **comparable_evidence(doc),
                   "informational_frames_with_draws_equal": e["detail"]["informational_frames_with_draws_equal"],
                   "present": b["detail"].get("present"), "clears": b["detail"].get("clears"),
                   "readbacks": b["detail"].get("readbacks")},
        "clear_matrix": {"pass": i["pass"], "failures": i["failures"],
                         **{k: i["detail"].get(k) for k in ("cases", "names", "outputs", "dxvk_library", "library",
                                                             "binaries")}},
        "other_runs": [other_run(o, d) for o, d in others],
        "verdict": doc["verdict_with_controls"],
    }
    if doc["acceptance_scope"] != "r2a-clears":
        rec["question"] = "Does this original-stream replay encode every accepted mapped draw and match fresh DXVK in every completed frame under the declared scope?"
    if rec["marker"] == METAL_HARBOR["marker"]:
        rec["inputs"]["registered_replay"] = copy.deepcopy(METAL_HARBOR)
        rec["question"] = "Does the full registered original Metal Harbor replay match both fresh NVIDIA DXVK and its held reference in every completed frame?"
    if any(o["verdict"] != "PASS" for o in rec["other_runs"]):
        rec["verdict"] = "FAIL"
        rec["answer"] = "no"
        rec["marker"] = "R2_FAIL"
    assert set(rec) == RECEIPT_KEYS
    return rec


def receipt_refusal(doc: dict | None, where: str, main: dict | None = None) -> str | None:
    """Why a compare.json cannot enter the receipt, or None."""
    if not doc or doc.get("schema") != SCHEMA:
        return f"no compare.json of schema {SCHEMA} in {where}"
    if doc.get("acceptance_scope") not in SCOPES:
        return f"{where}: no explicit supported acceptance scope; rerun it"
    if doc["acceptance_scope"] != "r2a-clears" and acceptance_marker(doc) == "R2_FAIL":
        return f"{where}: no geometry acceptance marker from all-frame parity and the suppression control"
    prov = get(doc, "inputs.provenance") or {}
    if prov.get("pass") is not True:
        return (f"{where}: its build was not held to the tree it pins (inputs.provenance: "
                f"{(prov.get('failures') or ['none recorded'])[:3]})")
    if main is None and not any(c.get("check") == "i" for c in doc.get("checks") or []):
        return f"{where}: its comparison ran no clear matrix (check i), which the receipt records: rerun it"
    # The pins name the compare_hosts.py whose checks made the verdict; its runs (players,
    # matrix, controls' faults) must have been made by that same file.
    ran_by, pinned = get(doc, "inputs.ran_by"), (get(doc, "inputs.pipeline") or {}).get(SELF)
    if not ran_by or ran_by != pinned:
        return (f"{where}: its runs were made by {SELF} {ran_by}, and it pins {pinned} (a `check` under another "
                f"{SELF}): rerun the comparison")
    if main is not None:
        if doc["acceptance_scope"] != main["acceptance_scope"]:
            return f"{where} has another acceptance scope"
        if doc["inputs"]["pipeline"] != main["inputs"]["pipeline"]:
            return f"{where} was compared by another pipeline"
        if any(doc["inputs"]["files"].get(n) != main["inputs"]["files"].get(n) for n in BINARIES):
            return f"{where} ran other binaries ({', '.join(BINARIES)})"
    return None


# A receipt id: the receipts' own form (Documentation/receipts/<id>.json), named by the caller.
RECEIPT_ID = re.compile(r"s5-[a-z0-9]+(-[a-z0-9]+)*-v[0-9]+")


def receipt_inputs(run_dir: Path, doc: dict) -> dict:
    """The physical semantic-input graph held through receipt publication.

    Hashes stream through sha256(), including large original streams. Optional
    absent run files remain absent; adding one or changing the named graph fails.
    compare.json is held separately because cmd_check legitimately rewrites it.
    This is a drift guard, not a replacement for the scope's existing validator.
    """
    paths = {ROOT / p for p in doc["inputs"]["pipeline"]}
    paths.update(ROOT / p for p in pipeline())
    paths.add(ROOT / doc["stream"]["path"])
    paths.add(ROOT / doc["inputs"]["census"]["path"])
    for c in doc.get("checks") or []:
        reference = get(c, "detail.reference") if c.get("check") == "h" else None
        if reference:
            paths.add(ROOT / reference)
    paths.update(run_dir / p for p in recorded_outputs(run_dir))
    paths.update(run_dir / p for p in doc.get("outputs") or {})
    build = ROOT / doc["inputs"]["build"]
    for name in BUILD_RECORDS:
        path = build / name
        rec = read_json(path)
        if not isinstance(rec, dict):
            raise ValueError(f"no build record {path}")
        paths.add(path)
        for kind in ("sources", "binaries", "generated", "depfiles", "external"):
            if not isinstance(rec.get(kind, {}), dict):
                raise ValueError(f"{path}: {kind} is not an input map")
        paths.update(ROOT / p for p in rec.get("sources", {}))
        for kind in ("binaries", "generated", "depfiles"):
            paths.update(build / p for p in rec.get(kind, {}))
        paths.update(Path(p) for p in rec.get("external", {}))
    names = dict.fromkeys(c["name"] for c in (*CONTROLS, *R2_CONTROLS))
    for role in ("dxvk", "sdlgpu", *(f"control-{n}" for n in names)):
        path = run_dir / role / "report.json"
        report = read_json(path)
        if report is None and not path.exists():
            report = {}
        if not isinstance(report, dict) or not isinstance(report.get("files", {}), dict):
            raise ValueError(f"{path}: invalid report/files input map")
        for item in report.get("files", {}).values():
            if isinstance(item, dict) and item.get("real_path"):
                paths.add(Path(item["real_path"]))
    for role in ("dxvk", "sdlgpu", *(f"control-{n}" for n in names), "matrix"):
        path = run_dir / role / "d3d9gpu.json"
        device = read_json(path)
        if device is None and not path.exists():
            device = {}
        if not isinstance(device, dict):
            raise ValueError(f"{path}: invalid device input map")
        keys_file = get(device, "pipeline.keys_file")
        if keys_file:
            paths.add(Path(keys_file))
    held = {}
    for path in sorted(paths):
        if not path.is_file():
            raise ValueError(f"missing receipt input {path}")
        stat = path.stat()
        held[str(path.absolute())] = {"sha256": sha256(path), "real_path": str(path.resolve()),
                                     "mode": stat.st_mode, "device": stat.st_dev, "inode": stat.st_ino}
    return held

def receipt_inputs_unchanged(held: list[tuple[Path, dict, dict, str]]) -> bool:
    """Rewalk each held graph; also certify the validator-produced compare.json."""
    try:
        for run_dir, doc, inputs, document_sha in held:
            if receipt_inputs(run_dir, doc) != inputs or sha256(run_dir / "compare.json") != document_sha:
                print(f"refused: receipt inputs changed before publication in {run_dir}", file=sys.stderr)
                return False
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"refused: receipt input inventory: {exc}", file=sys.stderr)
        return False
    return True

def publish_receipt(out: Path, text: str, held: list[tuple[Path, dict, dict, str]]) -> bool:
    """Prepare privately, then exclusively publish and immediately recheck.

    The creator descriptor holds its inode until final checks and cleanup finish.
    A measured refusal rolls back only the inode this writer owns. These checks
    do not lock unrelated input writers or make input/output changes one atomic
    transaction; changes after the last observation remain unmeasured.
    """
    temporary = None
    fd = None
    owned = None
    succeeded = False
    try:
        fd, name = tempfile.mkstemp(prefix=f".{out.name}.", dir=out.parent)
        temporary = Path(name)
        stat = os.fstat(fd)
        owned = (stat.st_dev, stat.st_ino)
        with os.fdopen(fd, "w", closefd=False) as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        expected = hashlib.sha256(text.encode()).hexdigest()
        stat = temporary.lstat()
        if (stat.st_dev, stat.st_ino) != owned or sha256(temporary) != expected:
            raise ValueError("provisional receipt identity or bytes changed")
        if not receipt_inputs_unchanged(held):
            return False
        os.link(temporary, out)  # exclusive: a concurrent --out cannot be replaced
        stat = temporary.lstat()
        if (stat.st_dev, stat.st_ino) != owned:
            raise ValueError("provisional receipt ownership changed")
        temporary.unlink()
        temporary = None
        # Success cleanup is inside the measured boundary; no input-affecting
        # preparation/cleanup remains after the final observations.
        if not receipt_inputs_unchanged(held):
            return False
        stat = out.lstat()
        if (stat.st_dev, stat.st_ino) != owned or sha256(out) != expected:
            raise ValueError("published receipt identity or bytes changed")
        succeeded = True
        return True
    except (OSError, ValueError) as exc:
        print(f"refused: receipt publication: {exc}", file=sys.stderr)
        return False
    finally:
        try:
            for path in ([out] if owned is not None and not succeeded else []) + ([temporary] if temporary is not None else []):
                try:
                    stat = path.lstat()
                    if (stat.st_dev, stat.st_ino) == owned:
                        path.unlink()
                except FileNotFoundError:
                    pass
        finally:
            if fd is not None:
                os.close(fd)


def cmd_receipt(args: argparse.Namespace) -> int:
    if not RECEIPT_ID.fullmatch(args.id or ""):
        print(f"refused: --id {args.id!r} is not a receipt id ({RECEIPT_ID.pattern})", file=sys.stderr)
        return 2
    if Path(args.out).exists():
        print(f"refused: {args.out} exists: a receipt is written once, under an id of its own", file=sys.stderr)
        return 2
    run_dir = Path(args.run)
    held = []
    # Hold every main/--also graph before the first validator can change any of
    # them. Each geometry check may rewrite only its own compare.json.
    for directory in (run_dir, *(Path(p) for p in args.also or [])):
        doc = read_json(directory / "compare.json")
        if (why := receipt_refusal(doc, str(directory), held[0][1] if held else None)):
            print(f"refused: {why}", file=sys.stderr)
            return 2
        try:
            document_sha = sha256(directory / "compare.json")
            inputs = receipt_inputs(directory, doc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"refused: receipt input inventory: {exc}", file=sys.stderr)
            return 1
        held.append((directory, doc, inputs, document_sha))
    for index, (directory, doc, inputs, document_sha) in enumerate(held):
        if not receipt_inputs_unchanged(held):
            return 1
        if doc.get("acceptance_scope") != "r2a-clears":
            check_args = argparse.Namespace(run=str(directory), reference=None)
            status = cmd_check(check_args)
            if status != 0:
                print(f"refused: geometry evidence in {directory} did not reverify against its original artifacts", file=sys.stderr)
                return status
            doc = read_json(directory / "compare.json")
            document_sha = getattr(check_args, "checked_document_sha256", None)
            # A repeated --also alias legitimately shares this writer's document.
            for j, (other, _, original_inputs, _) in enumerate(held):
                if other.resolve() == directory.resolve():
                    held[j] = (other, doc, original_inputs, document_sha)
            if not receipt_inputs_unchanged(held):
                return 1
            if (why := receipt_refusal(doc, str(directory), held[0][1] if index else None)):
                print(f"refused: {why}", file=sys.stderr)
                return 2
        if not index and doc.get("verdict_with_controls") != "PASS":
            print(f"refused: the run's verdict with controls is {doc.get('verdict_with_controls')}, not PASS: "
                  f"a receipt records a comparison whose controls all behaved", file=sys.stderr)
            return 1
    doc = held[0][1]
    others = [(o, rel(d)) for d, o, _, _ in held[1:]]
    # All-black frames cannot show a wrong clear colour or a missing readback.
    if sum(comparable_evidence(d)["comparable_not_black"] or 0 for d in [doc, *(o for o, _ in others)]) == 0:
        print("refused: every comparable frame of every run is black, which no wrong clear colour and no read back "
              "reading nothing would change: add a run whose comparable frames are not (--also, e.g. the synthetic "
              "stream at stage 0)", file=sys.stderr)
        return 1
    rec = build_receipt(doc, rel(run_dir), others, receipt_id=args.id)
    text = json.dumps(rec, indent=1) + "\n"
    if not publish_receipt(Path(args.out), text, held):
        return 1
    print(f"receipt {args.out}: {rec['verdict']}, comparable frames {rec['comparable_frames']}; "
          f"{len(others)} other run(s): {[(o['stream']['path'], o['verdict']) for o in rec['other_runs']]}")
    return 0



AA_ENV_OUTPUTS = ("DXVK_LOG_PATH", "DXVK_SHADER_CACHE_PATH", "SA2_PLAY_OUT")
AA_ENV_KEYS = ("DXVK_WSI_DRIVER", "DXVK_CONFIG_FILE", "DXVK_CONFIG", "DXVK_LOG_PATH", "DXVK_LOG_LEVEL",
               "DXVK_SHADER_CACHE", "DXVK_SHADER_CACHE_PATH", "DXVK_HUD", "DXVK_FILTER_DEVICE_NAME",
               "DXVK_FILTER_DEVICE_UUID", "DXVK_FORCE_WINDOWED", "SDL_NO_SIGNAL_HANDLERS", "SDL_VIDEO_DRIVER",
               "VK_LOADER_LAYERS_DISABLE", "VK_LOADER_LAYERS_ENABLE", "VK_INSTANCE_LAYERS", "VK_DRIVER_FILES",
               "VK_ICD_FILENAMES", "SA2_DXVK_D3D9", "SA2_PLAY_OUT")


def check_aa(a: Path, b: Path) -> dict:
    """Two independently rechecked full registered boot runs, with each host held to its repeat."""
    f, docs, raw, settings, repeats = [], {}, {}, {}, {}
    paths = {"a": Path(a).resolve(), "b": Path(b).resolve()}
    output = {"schema": "sa2-s5-host2-aa/v1", "acceptance_scope": "registered-boot-aa",
              "tool": "tools/s5/compare_hosts.py aa", "written": now(),
              "verdict": "FAIL", "failures": f, "runs": {}, "hosts": repeats}
    pins = pipeline()
    try:
        if paths["a"] == paths["b"] or paths["a"].samefile(paths["b"]):
            f.append("A/A requires separate physical run directories")
            return output
        for label, path in paths.items():
            saved = read_json(path / "compare.json") or {}
            held_hash = sha256(path / "compare.json")
            status, doc = checked_run(path)
            if doc is None:
                f.append(f"{label}: recorded run could not be rechecked")
                return output
            docs[label] = doc
            if any(saved.get(key) != doc[key] for key in ("stream", "stage", "hidden", "acceptance_scope", "max_frames")):
                f.append(f"{label}: saved run identity differs from its freshly checked identity")
            if saved.get("outputs") != doc["outputs"]:
                f.append(f"{label}: raw artifacts changed while the recorded run was rechecked")
            if status != 0 or acceptance_marker(doc) != "R2_DRAW_OK":
                f.append(f"{label}: full registered boot, END, reference and controls did not pass")
            if saved.get("inputs", {}).get("pipeline") != doc["inputs"]["pipeline"]:
                f.append(f"{label}: recorded pipeline differs from the current rechecked pipeline")
            if (doc["acceptance_scope"] != "r2-draws-full" or doc["stage"] != 3 or doc["max_frames"] != 0
                    or doc["stream"]["sha256"] != BOOT_STREAM_SHA256):
                f.append(f"{label}: not the original full registered stage-3 boot")
            raw[label] = {host: load_run(path / host) for host in PLAYERS}
            settings[label] = {}
            for host, run in raw[label].items():
                report, env = run["report"], run["report"].get("env")
                if (not isinstance(env, dict) or not set(AA_ENV_KEYS) <= set(env)
                        or not all(isinstance(v, str) for v in env.values())):
                    f.append(f"{label}/{host}: incomplete player environment settings")
                    env = {}
                options = report.get("options")
                if not valid_player_options(options):
                    f.append(f"{label}/{host}: incomplete or invalid player options")
                for field, name in (("times", "frames.y4m.times.tsv"), ("path", "frames.y4m")):
                    named = get(report, "frames_out." + field)
                    if not isinstance(named, str) or (ROOT / named).resolve() != path / host / name:
                        f.append(f"{label}/{host}: reported frame output is not this run's {name}")
                start = report.get("t_start_monotonic_s")
                if type(start) not in (int, float) or not math.isfinite(start) or start <= 0:
                    f.append(f"{label}/{host}: no finite positive actual start time")
                # Output paths differ per execution; every other recorded environment setting is held.
                for key in AA_ENV_OUTPUTS:
                    value = env.get(key)
                    wanted = path / host / "dxvk-log" if key == "DXVK_LOG_PATH" else path / "play-out"
                    if key == "DXVK_SHADER_CACHE_PATH":
                        wanted /= "dxvk-cache"
                    if not isinstance(value, str) or (ROOT / value).resolve() != wanted:
                        f.append(f"{label}/{host}: {key} is not this run's output path")
                requested = report.get("device")
                if (not isinstance(requested, dict) or not {"created", "failure", "back_buffer", "auto_depth",
                        "interval", "behavior", "guest_behavior", "guest_multisample", "x87_cw_before",
                        "x87_cw_after", "guest_device", "guest_back_buffer", "guest_depth"} <= set(requested)):
                    f.append(f"{label}/{host}: incomplete requested device settings")
                if (isinstance(options, dict) and type(options.get("vsync")) is bool
                        and get(report, "device.interval") != ("ONE" if options["vsync"] else "IMMEDIATE")):
                    f.append(f"{label}/{host}: vsync option contradicts the created device interval")
                host_info = copy.deepcopy(report.get("host") or {})
                host_info.pop("window_end", None)
                settings[label][host] = {"env": {k: v for k, v in env.items() if k not in AA_ENV_OUTPUTS},
                    "options": report.get("options"), "files": report.get("files"),
                    "host_identity": report.get("host_identity"), "host": host_info,
                    "device": report.get("device"), "adapter": report.get("adapter"), "dxvk_device": run["log"] if host == "dxvk" else None}
            device = raw[label]["sdlgpu"]["device"] or {}
            gpu = device.get("sdl_gpu") or {}
            if not complete_vulkan_settings(gpu):
                f.append(f"{label}: incomplete measured SDL Vulkan settings")
            if not measured_nvidia_gpu(gpu):
                f.append(f"{label}: no measured NVIDIA Vulkan device identity")
            device_pipeline = copy.deepcopy(device.get("pipeline") or {})
            keys_file = device_pipeline.pop("keys_file", None)
            keys_path = path / "sdlgpu" / "d3d9gpu.keys.json"
            if (not isinstance(keys_file, str) or (ROOT / keys_file).resolve() != keys_path
                    or not keys_path.is_file() or sha256(keys_path) != device_pipeline.get("keys_sha256")):
                f.append(f"{label}: shader key artifact is not bound to this actual run")
            device_env = device.get("env")
            required_env = ("SA2_D3D9GPU_REPORT", "SA2_D3D9GPU_FAULT", "SA2_D3D9GPU_PRESENT",
                "SA2_D3D9GPU_DEBUG", "SA2_D3D9GPU_CAPS_OFF", "SA2_D3D9GPU_TEST_RUN",
                "SA2_D3D9GPU_SHADER_DUMP", "SA2_D3D9GPU_SHADER_FAULT", "SDL_VIDEO_DRIVER",
                "VK_DRIVER_FILES", "VK_ICD_FILENAMES", "VK_LOADER_LAYERS_DISABLE",
                "VK_LOADER_LAYERS_ENABLE", "VK_INSTANCE_LAYERS", "SDL_ASSERT")
            if (not isinstance(device_env, dict) or not set(required_env) <= set(device_env)
                    or not all(isinstance(v, str) for v in device_env.values())):
                f.append(f"{label}: incomplete device environment settings")
                device_env = {}
            report_path, dump_path = device_env.get("SA2_D3D9GPU_REPORT"), device_env.get("SA2_D3D9GPU_SHADER_DUMP")
            if not isinstance(report_path, str) or (ROOT / report_path).resolve() != path / "sdlgpu/d3d9gpu.json":
                f.append(f"{label}: device report output path is not this run's")
            if not isinstance(dump_path, str) or (dump_path and not (ROOT / dump_path).resolve().is_relative_to(path / "sdlgpu")):
                f.append(f"{label}: shader dump output escapes this run")
            settings[label]["sdlgpu"].update(sdl_gpu=gpu, pipeline=device_pipeline,
                device_env={k: v for k, v in device_env.items() if k not in ("SA2_D3D9GPU_REPORT", "SA2_D3D9GPU_SHADER_DUMP")},
                shader_dump_enabled=bool(dump_path))
            output["runs"][label] = {"run_dir": rel(path), "compare_sha256": held_hash,
                "outputs": doc["outputs"], "shader_keys_sha256": sha256(keys_path) if keys_path.is_file() else None,
                "settings": settings[label]}
        da, db = docs["a"], docs["b"]
        for key in ("stream", "stage", "hidden", "acceptance_scope", "max_frames"):
            if da[key] != db[key]:
                f.append(f"A/B {key} identity differs")
        for key in ("build", "files", "pipeline", "provenance"):
            if da["inputs"][key] != db["inputs"][key]:
                f.append(f"A/B {key} identity differs")
        if set(da["outputs"]) != set(db["outputs"]):
            f.append("A/B raw artifact inventory differs")
        physical_a = {(p.stat().st_dev, p.stat().st_ino) for name in da["outputs"]
                      for p in (paths["a"] / name,)}
        for name in db["outputs"]:
            p = paths["b"] / name
            if (p.stat().st_dev, p.stat().st_ino) in physical_a:
                f.append(f"A/B share physical output {name}")
        ka, kb = paths["a"] / "sdlgpu/d3d9gpu.keys.json", paths["b"] / "sdlgpu/d3d9gpu.keys.json"
        if ka.is_file() and kb.is_file() and ka.samefile(kb):
            f.append("A/B share physical shader key output")
        for host in PLAYERS:
            if json.dumps(settings["a"][host], sort_keys=True, allow_nan=False) != json.dumps(settings["b"][host], sort_keys=True, allow_nan=False):
                f.append(f"{host}: actual driver/player/shader/device settings differ")
            if raw["a"][host]["report"].get("t_start_monotonic_s") == raw["b"][host]["report"].get("t_start_monotonic_s"):
                f.append(f"{host}: both reports name the same execution start")
            ra, rb = raw["a"][host]["times"], raw["b"][host]["times"]
            mismatch = [{"row": i, "fields": [k for k in TIMES_FIELDS if x.get(k) != y.get(k)]}
                        for i, (x, y) in enumerate(zip(ra, rb), 1)
                        if any(x.get(k) != y.get(k) for k in TIMES_FIELDS)]
            repeats[host] = {"pass": len(ra) == len(rb) and not mismatch,
                             "rows_a": len(ra), "rows_b": len(rb), "columns": list(TIMES_FIELDS),
                             "mismatches": mismatch}
            if not repeats[host]["pass"]:
                f.append(f"{host}: A/A TIMES_FIELDS differ")
        for label, path in paths.items():
            held = output["runs"][label]
            if (recorded_outputs(path) != docs[label]["outputs"] or sha256(path / "compare.json") != held["compare_sha256"]
                    or sha256(path / "sdlgpu/d3d9gpu.keys.json") != held["shader_keys_sha256"]):
                f.append(f"{label}: raw artifacts changed during A/A verification")
        build = ROOT / da["inputs"]["build"]
        current_build = provenance(build, pins, [raw[k]["dxvk"] for k in paths],
                                   [raw[k]["sdlgpu"] for k in paths])
        if (not current_build["pass"] or current_build["records"] != da["inputs"]["provenance"]["records"]
                or current_build["binaries"] != da["inputs"]["provenance"]["binaries"]):
            f.append("build inputs or outputs drift during A/A verification")
        reference = next(c["detail"] for c in da["checks"] if c["check"] == "h")
        if sha256(ROOT / reference["reference"]) != reference["sha256"]:
            f.append("registered reference drift during A/A verification")
        if pipeline() != pins or any(d["inputs"]["pipeline"] != pins for d in docs.values()):
            f.append("pipeline source drift during A/A verification")
        source = ROOT / da["stream"]["path"]
        if sha256(source) != da["stream"]["sha256"]:
            f.append("original source drift during A/A verification")
        output.update(stream=da["stream"], stage=da["stage"],
            inputs={k: da["inputs"][k] for k in ("build", "files", "pipeline", "provenance", "ran_by")})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        f.append(f"A/A input refused: {exc}")
    output["verdict"] = "FAIL" if f else "PASS"
    return output


def cmd_aa(args: argparse.Namespace) -> int:
    out = Path(args.out).resolve()
    if not RECEIPT_ID.fullmatch(args.id or "") or out.exists():
        print("refused: A/A needs a valid receipt id and a new output path", file=sys.stderr)
        return 2
    if any(out.is_relative_to(Path(p).resolve()) for p in (args.a, args.b)):
        print("refused: A/A output must be outside both input trees", file=sys.stderr)
        return 2
    held = []
    try:
        # checked_run/check_aa are non-writing. Hold both physical graphs and
        # saved-document identities before either actual run is rechecked.
        for name in (args.a, args.b):
            path = Path(name).resolve()
            saved = read_json(path / "compare.json")
            if not isinstance(saved, dict):
                raise ValueError(f"no saved comparison document in {path}")
            held.append((path, saved, receipt_inputs(path, saved), sha256(path / "compare.json")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"refused: A/A input inventory: {exc}", file=sys.stderr)
        return 2
    doc = check_aa(Path(args.a), Path(args.b))
    if doc["verdict"] != "PASS":
        for failure in doc["failures"]:
            print(f"BOOT_AA_FAIL {failure}", file=sys.stderr)
        return 1
    if (not receipt_inputs_unchanged(held)
            or any(get(doc, f"runs.{label}.compare_sha256") != item[3]
                   for label, item in zip(("a", "b"), held))):
        print("refused: A/A checked document identities changed", file=sys.stderr)
        return 2
    doc["id"] = args.id
    try:
        text = json.dumps(doc, indent=1, allow_nan=False) + "\n"
    except (ValueError, TypeError, OverflowError) as exc:
        print(f"refused: A/A receipt serialization: {exc}", file=sys.stderr)
        return 2
    if not publish_receipt(out, text, held):
        return 2
    print(f"BOOT_AA_OK {out}")
    return 0

def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--stream", required=True)
    r.add_argument("--stage", type=int, default=3)
    r.add_argument("--out")
    r.add_argument("--build", required=True, help="the build to run (host/player/build.sh's OUT); no default")
    r.add_argument("--controls", action="store_true")
    r.add_argument("--scope", choices=SCOPES, default="r2-draws-full")
    r.add_argument("--max-frames", type=int, default=0)
    r.add_argument("--hidden", action="store_true")
    r.add_argument("--reference")
    r.add_argument("--allow-notes", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("--run", required=True)
    c.add_argument("--reference")
    a = sub.add_parser("dxvk-ab")
    a.add_argument("--before", required=True)
    a.add_argument("--before-commit", required=True, help="the commit the before build was built from (a clone)")
    a.add_argument("--after", required=True)
    a.add_argument("--stream", required=True)
    a.add_argument("--stage", type=int, default=3)
    a.add_argument("--out")
    a.add_argument("--allow-notes", action="store_true")
    a.add_argument("--hidden", action="store_true")
    u = sub.add_parser("dxvk-unchanged")
    u.add_argument("--after", required=True, help="this tree's build (both build scripts' OUT)")
    u.add_argument("--before-commit", required=True, help="the commit whose DXVK player this build's is held to")
    u.add_argument("--out")
    rc = sub.add_parser("receipt")
    rc.add_argument("--run", required=True)
    rc.add_argument("--id", required=True, help="the receipt's id (s5-...-vN): never a default")
    rc.add_argument("--out", required=True)
    rc.add_argument("--also", action="append", help="another stream's run (no controls) by the same pipeline")
    aa = sub.add_parser("aa", help="recheck two separate full registered boot runs and compare each host's repeat")
    aa.add_argument("--a", required=True)
    aa.add_argument("--b", required=True)
    aa.add_argument("--id", required=True)
    aa.add_argument("--out", required=True)
    args = p.parse_args(argv)
    if args.cmd == "run" and ((args.scope == "r2-draws-partial") != (args.max_frames > 0) or args.max_frames < 0):
        print("usage: partial scope requires --max-frames N > 0; other scopes require no bound", file=sys.stderr)
        return 2
    if args.cmd in ("run", "dxvk-ab") and not 0 <= args.stage <= 3:
        print("usage: --stage 0..3", file=sys.stderr)
        return 2
    return {"run": cmd_run, "check": cmd_check, "dxvk-ab": cmd_dxvk_ab, "dxvk-unchanged": cmd_dxvk_unchanged,
            "receipt": cmd_receipt, "aa": cmd_aa}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
