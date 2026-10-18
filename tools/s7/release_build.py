#!/usr/bin/env python3
"""Build a prebuilt release directory (x86-64 Linux, or x86-64 Windows with --target): the game, the
player hosts, the installer and the launcher, laid out to run from wherever the directory is put.

Subtitle: `.venv/bin/python tools/s7/release_build.py` writes
`private/generated/s7/release/linux-x86_64/` (never tracked: it holds the lifted game and the
code-section bytes of the user's DRM ruling), building in `private/generated/s7/release/build/`.

What it builds, and from what
-----------------------------
* **The game**, `bin/sa2-game`: the lifted image, in the double f80 mode by default ("The f80
  mode" below), linked by `tools/s4/link_s4.py` exactly as `tools/s5/play_live.py` links its boot
  session (the reach build's generated inputs as `tools/s4/loop_run.py`'s `link_args` gives them,
  the tick probe's hook and wrap, the presentation recorder `host/sa2_present_rec.c` and its wraps,
  `harness/crt_probe.c` as the driver), plus two things the product needs:
  - `--exe-overlay`: the shipped executable's code-section overlay table, generated here by
    `tools/s7/text_readset.py --out`, so the game loads the player's own `sonic2app.exe` as-is
    under `SA2_EXE_KIND=shipped` (the user's ruling,
    `Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md`);
  - the launch observer (`observer_source`) in the tick probe's place, as play_live puts its
    receipt observer there: the tick probe unchanged, inside a wrap that writes one row per
    GameLoop return (the tick, the Presents so far, `GameMode`, `CurrentMenu`) to
    `SA2_LAUNCH_TICKS`, and one row per keyboard poll that read the host FIFO's records. With
    `SA2_LAUNCH_TICKS` unset it writes nothing. It reads guest memory and host counters and writes
    nothing the guest can see. The launcher's selftest reads it (host/launch/sa2_launch.h).
  The game's settings (`share/game.env`) are generated from the same constants play_live's boot
  session takes: the reach configuration (`tools/s4/reach_receipt.py` ENV), no run bound
  (play_live's UNBOUNDED), the default device profile, and the recorder's sound.
* **The players**, `bin/sa2_player` (DXVK Native) and `bin/sa2_player_gpu` (the renderer on SDL3's
  GPU API), each by its own build script (`host/player/build.sh`, `host/d3d9gpu/build.sh`) with
  `OUT` in the build directory. Both scripts embed the build machine's SDL3 directory as the
  players' DT_RPATH; the copies here have it rewritten in place to `$ORIGIN/../lib`
  (`set_rpath`: the string is shorter, so it is overwritten and padded with NULs, and readelf is
  asked afterwards), so the loader takes `lib/` beside them before anything on the host.
* **The libraries** in `lib/`: the pinned SDL3 and DXVK Native the players were built against
  (`libSDL3.so.0`, `libdxvk_d3d9.so.0`), and the two the players need that a desktop may not have
  (`libpng16.so.16`, `libzstd.so.1`), copied from the build machine. glibc, libstdc++, libgcc_s,
  zlib and the Vulkan loader stay the host's, as every Linux game's do; `release.manifest` lists
  them.
* **`share/dxvk.conf`**: host/player/dxvk.conf, the file the DXVK player's expectations pin.
* **The installer**, `bin/sa2-install`: `host/install/build.sh` against the table
  `tools/s7/install_table.py --out-dir` generates here.
* **The launcher**, `bin/sa2recomp`: `host/launch/build.sh`, against the same table (the selftest
  holds each file the game opened to it).

* **`share/licenses/`**: the notice of each bundled library (SDL3's and DXVK's from the pinned
  install and sources, libpng's and zstd's from the build machine's packages), which their
  licenses ask a binary distribution to carry.

The game is shipped with its debug information stripped (`strip --strip-debug`; the symbol table
stays), and every object is compiled afresh (`link_s4.phase_compile` with `rebuild`): its
freshness test is the objects' mtimes, and a source copied with an older mtime would otherwise
link a stale object.

`release.manifest` (text, so no census reads it as a run record) lists every file with its size
and SHA-256; its first line, a JSON head, names the sources by checkout-relative path and SHA-256
(`sources`: every file compiled or generated into a binary here, so a record that names the
manifest names the code that made it; tools/s7/installer_receipt.py compares them with the tree),
the tables' hashes and the host libraries assumed, with no path of the build machine's.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s7/release_build.py [--target x86_64-linux|x86_64-windows] [--src DIR]
      [--out DIR] [--work DIR] [--f80 double|native] [--win-objects DIR | --f80-objects DIR]
      [--only tables,game,players,install,launch,assemble]

`--src` builds another copy of the sources (a snapshot); the generated inputs (the lift, the
function table, the edge table, the lifted extras, the SDL3/DXVK install) are always this
checkout's `private/`, read only. Each step refuses on the first failure and says which.

The x86-64 Windows release (`--target x86_64-windows`)
-----------------------------------------------------
`private/generated/s7/release/windows-x86_64/`, built in `release/build/windows-x86_64/`, every
binary built for the triple x86_64-windows-gnu by the pinned zig:

* **The game**, `bin/sa2-game.exe`: `tools/s7/win_link.py` (the Windows link of the replay build:
  the runtime with its Windows layer, the double-mode lifted objects by default or the cross-built
  native-x87 ones of `--win-objects` under `--f80 native`, `zig lld-link`), with the same hooks,
  wraps, overlay table and settings as the Linux game: the launch observer and the presentation
  recorder as hooks, the tick probe's and the recorder's wraps, `--exe-overlay` the table the
  tables step writes, and `share/game.env` the same file.
  The recorder is compiled through a generated unit (`recorder_source`) that gives it the one
  POSIX name the Windows C library lacks, O_CLOEXEC, as runtime/sa2_win32.h spells it
  (_O_NOINHERIT), and then includes host/sa2_present_rec.c unchanged.
* **The player**, `bin/sa2_player_gpu.exe` with `bin/SDL3.dll` beside it: `tools/s7/host2_win_build.py`
  (host 2 alone; DXVK Native is a Linux library), its outputs copied. Its libraries (libpng, zlib,
  zstd, DXVK's shader lowering and dxbc-spirv) are linked in statically.
* **The installer** and **the launcher**, `bin/sa2-install.exe` and `bin/sa2recomp.exe`:
  `host/install/build.sh` and `host/launch/build.sh` with `--cxx zig --target x86_64-windows-gnu`,
  statically linked (zig's libc++ and mingw-w64's runtime).
* **`share/licenses/`**: SDL3's, DXVK's, dxbc-spirv's, libpng's, zlib's, zstd's and
  mingw-w64's notices, each from the pinned source it was built from.

Nothing else is bundled: the Windows release uses the system's UCRT, and Vulkan or Direct3D 12
through SDL. `release.manifest` is written the same way, with `target` x86_64-windows and the
Windows tools among its sources.

The f80 mode: double by default (`--f80 double|native`, `--f80-objects DIR`)
----------------------------------------------------------------------------
The PC releases ship the double f80 mode (the user's decision of 2026-10-12,
`Documentation/decisions/2026-10-12-S7-pc-double-default.md`). With no flag the game links the
lifted image that tools/s8/perf_f80.py `build --mode double` legalised for the target's triple
(tools/s8/f80_double.py: the x87 registers held as binary64, at the game's own PC24 precision),
from `F80_OBJECTS`: `private/generated/s8/pc-double/linux-obj` (x86_64-linux-musl) or
`private/generated/s8/pc-double/windows-obj` (x86_64-windows-gnu). With it go the mode's shims,
the build's f80_shims.o, which define what the legalised code calls (the `sa2_df80_*` functions)
and the marker by which the runtime reads the register slots in that mode (`sa2_f80_state_double`).
`--f80-objects DIR` names another perf_f80.py build (any legalised mode its summary names; with
`--f80 double`, the double mode only).

`--f80 native` builds the exact native-x87 game as before: the staged x86-64 lifted objects on
Linux, the cross-built ones on Windows (`--win-objects`, which alone also means `--f80 native`).
The registered replays and every gate still measure that build; tools/s8/pc_double.py `boot`
holds each release's game, in whichever mode, to the registered boot replay before it is handed
over.

`f80_build` checks the f80 build before any step runs: its summary names the triple and a
legalised mode (the mode asked for, when one is), every module compiled, the census found nothing
disallowed, the pass and the shims are still the checkout's files at the summary's sha256, and
so is every other input its `deps` records (the modules the pass imports, the files the shims
include: perf_f80.py `deps`), the shims object is the one the summary names, and every module's
IR still hashes to its record's `ir_sha256`. A missing or stale build is refused with the exact
perf_f80.py command that rebuilds it (`f80_command`). A build whose summary predates `deps` is
checked as far as it names, and the inputs left unchecked are printed (`f80_deps`). On Linux
the build's map and arm objects become the link directory (hard links under the work directory,
`f80_linkdir`; their names must be the staged directory's), and its extras, in
extra_functions.<tag>.json's order, and the shims are the link's extra objects; on
Windows the build is win_link.py's `--objects` and the shims its `--extra-object`. Everything else
is built as for the native-x87 game. The game's notes record the build (`f80`: mode, triple, the
summary's, the pass's and the shims' sha256; no `f80` for the native-x87 game), the manifest's
sources add the pass and shims sources (F80_SOURCE_GLOBS), and README.txt says which build the
release holds (`player_readme`).

Code-patch tweaks (`--variants DIR`, the Linux game only)
-------------------------------------------------------
A Mod Loader patch to the game's code bytes does nothing here, since the lifted game never runs
those bytes: tools/s8/variants/variants.py `build --objects <the game's f80 build> --out DIR` lifts
each function such a patch reaches again from the patched bytes, as a variant, and writes the C
that names the patch's tweak to runtime/sa2_tweaks.c and wraps each such function
(runtime/sa2_tweaks.h, "Add a code-patch tweak"). `--variants DIR` links that C, the variant
objects and the wraps into the game, after `variants_for` checks that DIR was built for the f80
build this game links and that no file moved since; the game's notes record it (`variants`), and
the manifest's sources add the tool and the specs it built from (`variant_globs`). With no tweak
named in SA2_TWEAKS, and in every replay, each wrap only calls the lifted original.

README.txt lists every tweak in runtime/sa2_tweaks.h but the code-patch tweaks the game does not
link (`player_readme`), and the build names those (`unlinked_patches`): a Windows game, a Linux
game built without --variants, or variants built without that tweak's spec. When DIR links a test
fixture (tools/s8/variants/fixtures/), the build says so, and README.txt leaves it out. The host
tweaks (SA2_TWEAK_HOST_LIST) are listed with the game's, in name order, but those whose home is the
Android app (`DESKTOP_HOMES`).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path



def checkout_root(here: Path) -> Path:
    """The checkout whose generated inputs a build reads: this file's own, or, for a snapshot of
    the sources kept under a checkout's private/ (`--src`), the checkout holding it."""
    for p in here.parents[1:]:
        if (p / "private" / "generated").is_dir() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"release_build: no checkout with private/generated above {here}")


ROOT = checkout_root(Path(__file__).resolve())
GEN = ROOT / "private" / "generated"
OUT = GEN / "s7" / "release" / "linux-x86_64"
WORK = GEN / "s7" / "release" / "build" / "linux-x86_64"
# The release targets: the default, and the Windows one (module docstring).
TARGETS = ("x86_64-linux", "x86_64-windows")
WIN_OUT = GEN / "s7" / "release" / "windows-x86_64"
WIN_WORK = GEN / "s7" / "release" / "build" / "windows-x86_64"
WIN_TRIPLE = "x86_64-windows-gnu"
# tools/s7/cross_build.py's objects for the Windows triple: its default output, else where S7's
# third wave built them.
WIN_OBJECTS = (GEN / "s7" / "targets" / WIN_TRIPLE,
               GEN / "s7" / "wave3" / "winport" / "targets" / WIN_TRIPLE)
HOST2_WIN = GEN / "s7" / "host2-win"
RECORDER_SHIM = "sa2_present_rec_win.c"
# The Windows release's own sources beside the Linux ones: its two build tools.
WIN_SOURCE_GLOBS = ("tools/s7/win_link.py", "tools/s7/host2_win_build.py")
S5_INSTALL = ROOT / "private" / "s5-tools" / "install"
# The f80 mode (module docstring): the triple each target's build must name, the shims object it
# holds, and the sources the manifest adds for a game linked from one. The PC releases ship the
# double mode (Documentation/decisions/2026-10-12-S7-pc-double-default.md), from F80_OBJECTS,
# tools/s8/perf_f80.py's double builds; `--f80 native` links the native-x87 objects.
F80_TRIPLES = {"x86_64-linux": "x86_64-linux-musl", "x86_64-windows": WIN_TRIPLE}
F80_SHIMS = "f80_shims.o"
F80_CHOICES = ("double", "native")
F80_DEFAULT = "double"
F80_OBJECTS = {"x86_64-linux": GEN / "s8" / "pc-double" / "linux-obj",
               "x86_64-windows": GEN / "s8" / "pc-double" / "windows-obj"}
NATIVE_MODE = "native-x87"
# The modes perf_f80.py builds (its MODES), for the command that rebuilds one.
PERF_F80_MODES = ("exact", "double")
# The pass, the tool that ran it, and the shims, with what they import and include
# (tools/s8/f80_double.py imports tools/s7/f80_legalize.py; tools/s8/f80_shims_double.c includes
# tools/s7/f80_shims.c whole): every input perf_f80.py's summary records (`deps`).
F80_SOURCE_GLOBS = ("tools/s8/perf_f80.py", "tools/s8/f80_double.py", "tools/s7/f80_legalize.py",
                    "tools/s8/f80_shims_double.c", "tools/s7/f80_shims.c")
PERF_F80 = ROOT / "tools" / "s8" / "perf_f80.py"
# A game linked with code-patch tweaks (--variants) adds the tool that made them, and the specs it
# built them from (variant_globs).
VARIANT_TOOL = "tools/s8/variants/variants.py"
STEPS = ("tables", "game", "players", "install", "launch", "assemble")
TAG = "next+allarms"
GAME = "sa2-game"
LAUNCHER = "sa2recomp"
INSTALLER = "sa2-install"
PLAYERS = {"dxvk-native": "sa2_player", "sdl3-gpu": "sa2_player_gpu"}
RPATH = "$ORIGIN/../lib"
# Libraries the players need that come with the release; the rest are the host's.
BUNDLED = ("libSDL3.so.0", "libdxvk_d3d9.so.0", "libpng16.so.16", "libzstd.so.1")
HOST_LIBS = ("libc.so.6", "libm.so.6", "libstdc++.so.6", "libgcc_s.so.1", "libz.so.1",
             "libvulkan.so.1 (the Vulkan loader and an ICD)")
# The observer (observer_source): its output variable, and the state words it reads, named as
# Documentation/scripts/check_s5_exit.py names them (STATE_WORDS).
TICKS_ENV = "SA2_LAUNCH_TICKS"
OBSERVER = "sa2_launch_observer.c"
STATE_COLUMNS = ("game_mode", "current_menu")
ENV_FILE = "share/game.env"
MANIFEST = "release.manifest"
SCHEMA = "sa2-s7-release/v1"
# The sources each binary is made from, as checkout-relative globs (release.manifest `sources`):
# the launcher and installer (with host/player's SHA-256 they share), the game (the runtime, the
# probe it links as its driver, the tick probe the observer wraps, the presentation recorder), and
# both players; and the tools that generate the tables and the observer.
SOURCE_GLOBS = ("host/launch/*.cpp", "host/launch/*.h", "host/launch/build.sh",
                "host/launch/tweaks/*.cpp", "host/launch/tweaks/*.h",
                "host/install/*.cpp", "host/install/*.h", "host/install/build.sh",
                "runtime/*.c", "runtime/*.cpp", "runtime/*.h", "runtime/*.inc", "runtime/tweaks/*.c",
                "harness/crt_probe.c", "harness/replay/wrap_tick_probe.c", "harness/replay/*.h",
                "host/sa2_present_rec.c", "host/*.h",
                "host/player/*.cpp", "host/player/*.c", "host/player/*.h", "host/player/build.sh",
                "host/player/tweaks/*.cpp", "host/player/tweaks/*.h",
                "host/player/dxvk.conf",
                "host/d3d9gpu/*.cpp", "host/d3d9gpu/*.c", "host/d3d9gpu/*.h", "host/d3d9gpu/*.hlsl",
                "host/d3d9gpu/*.glsl", "host/d3d9gpu/shader/*", "host/d3d9gpu/build.sh",
                "tools/s4/link_s4.py", "tools/s4/tweak_sources.py",
                "tools/s7/install_table.py", "tools/s7/text_readset.py", "tools/s7/release_build.py")
# Each bundled library's notice: (the release's name, where the build machine holds it).
LICENSES = (("SDL3.txt", S5_INSTALL / "share/licenses/SDL3/LICENSE.txt"),
            ("DXVK.txt", ROOT / "private/s5-tools/src/dxvk-3.1.1/LICENSE"),
            ("libpng16.txt", Path("/usr/share/doc/libpng16-16t64/copyright")),
            ("zstd.txt", Path("/usr/share/doc/libzstd1/copyright")))


def sources(src: Path, globs: tuple = SOURCE_GLOBS) -> dict:
    """{checkout-relative path: sha256} of every file `globs` names under `src`."""
    found = {}
    for pattern in globs:
        for p in sorted(src.glob(pattern)):
            if p.is_file():
                found[p.relative_to(src).as_posix()] = sha256(p)
    return dict(sorted(found.items()))


def rel(p: Path) -> str:
    p = Path(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def place(src: Path, dst: Path) -> None:
    """Copy by rename: a run using the release's binary (a Wine route, host 2's evidence run)
    keeps the file it mapped, where an in-place copy truncates it under the run."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def run(cmd: list, log: Path, env: dict | None = None, cwd: Path | None = None) -> None:
    """Run `cmd`, its output to `log`; refuse on a nonzero exit, quoting the log's tail."""
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(log, "w") as out:
        p = subprocess.run([str(c) for c in cmd], stdout=out, stderr=subprocess.STDOUT,
                           env=env, cwd=str(cwd) if cwd else None)
    took = time.time() - t0
    if p.returncode:
        tail = log.read_text(errors="replace")[-3000:]
        raise SystemExit(f"release_build: {' '.join(str(c) for c in cmd)[:300]} exited "
                         f"{p.returncode} after {took:.0f} s; {rel(log)}:\n{tail}")
    print(f"  ok in {took:.0f} s ({rel(log)})", flush=True)


# ---------------------------------------------------------------- modules of the source tree

def load(src: Path, relpath: str, name: str):
    """A module of the source tree, by path."""
    spec = importlib.util.spec_from_file_location(name, src / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def source_modules(src: Path) -> dict:
    """tick_replay, reach_receipt, device_profile, present_run, play_live and link_s4 of `src`,
    imported with this checkout's shared helpers (common, spikelib) imported first, so a
    snapshot's tools read this checkout's generated inputs (common.ROOT is this checkout)."""
    for d in ("tools/spike", "tools/s3/whole_image"):
        sys.path.insert(0, str(ROOT / d))
    import common  # noqa: F401
    import spikelib  # noqa: F401
    for d in ("tools/oracle", "tools/s5", "tools/s4"):
        sys.path.insert(0, str(src / d))
    import device_profile, link_s4, play_live, present_run, reach_receipt, tick_replay  # noqa: E401
    for m in (device_profile, link_s4, play_live, present_run, reach_receipt, tick_replay):
        if not Path(m.__file__).resolve().is_relative_to(src):
            raise SystemExit(f"release_build: {m.__name__} came from {m.__file__}, not {src}")
    return {"DP": device_profile, "L4": link_s4, "PL": play_live, "PR": present_run,
            "RR": reach_receipt, "TR": tick_replay}


# ---------------------------------------------------------------- the observer

# For a Windows (COFF) link: the dinput observer's definition made a strong one. runtime/sa2_dinput.h
# declares sa2_dinput_host_records_read weak, so a definition in a unit that includes it is weak too;
# in an ELF link a weak definition still answers sa2_dinput.c's weak reference, and in a COFF link
# it does not (measured under Wine, 2026-10-11: the keyboard read the launcher's Enter and the
# observer wrote no K row). The header's declaration is renamed away in this unit, and the
# definition declared without the attribute.
STRONG_OBSERVER_HEAD = """#define sa2_dinput_host_records_read sa2_dinput_host_records_read_header_decl
"""
STRONG_OBSERVER_TAIL = """#undef sa2_dinput_host_records_read
void sa2_dinput_host_records_read(uint32_t poll, uint32_t first_seq, uint32_t last_seq,
                                  uint32_t records);
"""


def observer_source(src: Path, words: dict, strong: bool = False) -> str:
    """The launch observer's C source: the tick probe of `src`, included by absolute path with its
    GameLoop wrap renamed, and a wrap around it writing one row per GameLoop return to
    SA2_LAUNCH_TICKS; and the dinput FIFO observer (runtime/sa2_dinput.h,
    sa2_dinput_host_records_read) writing one row per poll that read host records.
    `words` maps each STATE_COLUMNS name to (va, width). `strong` (the Windows link) makes the
    dinput observer a strong definition (STRONG_OBSERVER_HEAD)."""
    probe = (src / "harness" / "replay" / "wrap_tick_probe.c").resolve()
    reads = ", ".join(f"tp_read(0x{words[c][0]:08X}u, {words[c][1]})" for c in STATE_COLUMNS)
    cols = "\\t".join(("T", "tick", "presents") + STATE_COLUMNS)
    head = STRONG_OBSERVER_HEAD if strong else ""
    tail = STRONG_OBSERVER_TAIL if strong else ""
    return head + f"""/* Generated by tools/s7/release_build.py (observer_source); do not edit.
 *
 * The launch observer: harness/replay/wrap_tick_probe.c, unchanged, with its GameLoop wrap
 * renamed, and a wrap around it that writes one row per GameLoop return to {TICKS_ENV}:
 *   T <tick> <presents> <game_mode> <current_menu>
 * (the tick probe's count of GameLoop returns, the device's Present count, and the state words
 * Documentation/scripts/check_s5_exit.py names in STATE_WORDS, read with the probe's tp_read);
 * and, after each keyboard poll that read records from the host FIFO
 * (runtime/sa2_dinput.h, sa2_dinput_host_records_read):
 *   K <tick> <poll> <first_seq> <last_seq> <records>
 * Nothing is written when {TICKS_ENV} is unset. It reads guest memory and host counters only,
 * and writes nothing the guest can see; the dinput observer touches neither the guest, nor
 * floating point, nor the scheduler, as that header asks.
 */
#define __wrap_sub_433ee0 sa2_launch_probe_gameloop
#include "{probe}"
#undef __wrap_sub_433ee0

#include "sa2_d3d9.h"
#include "sa2_dinput.h"
{tail}
static FILE *launch_out;
static int launch_off;

static void launch_open(void) {{
    if (launch_out || launch_off) return;
    const char *path = getenv("{TICKS_ENV}");
    if (!path || !*path || !(launch_out = fopen(path, "w"))) {{
        launch_off = 1;
        return;
    }}
    fputs("# sa2 launch observer v1\\n{cols}\\n", launch_out);
    fflush(launch_out);
}}

void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory) {{
    void *r = sa2_launch_probe_gameloop(state, pc, memory);
    launch_open();
    if (launch_out) {{
        sa2_d3d9_stats st;
        sa2_d3d9_stats_get(&st);
        fprintf(launch_out, "T\\t%lu\\t%u\\t%u\\t%u\\n", sa2_replay_gameloop_returns(),
                st.present_calls, {reads});
        fflush(launch_out);     /* the launcher reads the file while the game runs */
    }}
    return r;
}}

void sa2_dinput_host_records_read(uint32_t poll, uint32_t first_seq, uint32_t last_seq,
                                  uint32_t records) {{
    launch_open();
    if (launch_out) {{
        fprintf(launch_out, "K\\t%lu\\t%u\\t%u\\t%u\\t%u\\n", sa2_replay_gameloop_returns(),
                poll, first_seq, last_seq, records);
        fflush(launch_out);
    }}
}}
"""


def state_words(src: Path) -> dict:
    """STATE_COLUMNS' (va, width), from check_s5_exit.STATE_WORDS of the source tree."""
    x = load(src, "Documentation/scripts/check_s5_exit.py", "rb_check_s5_exit")
    return {c: (x.STATE_WORDS[c][1], x.STATE_WORDS[c][2]) for c in STATE_COLUMNS}


# ---------------------------------------------------------------- the ELF rpath

def elf_dynamic(data: bytes) -> tuple[int, dict, list]:
    """(file offset of .dynstr, {tag: value}, [(tag, value, entry offset)]) of a 64-bit
    little-endian ELF, read from its program headers (PT_DYNAMIC, PT_LOAD)."""
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise SystemExit("not a 64-bit little-endian ELF")
    phoff, = struct.unpack_from("<Q", data, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x36)
    loads, dyn = [], None
    for i in range(phnum):
        p_type, _flags, off, vaddr, _pa, filesz, _memsz, _al = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + i * phentsize)
        if p_type == 1:
            loads.append((vaddr, off, filesz))
        elif p_type == 2:
            dyn = (off, filesz)
    if dyn is None:
        raise SystemExit("no PT_DYNAMIC")
    entries, tags = [], {}
    for at in range(dyn[0], dyn[0] + dyn[1], 16):
        tag, val = struct.unpack_from("<qQ", data, at)
        if tag == 0:
            break
        entries.append((tag, val, at))
        tags.setdefault(tag, val)
    strtab = tags.get(5)
    for vaddr, off, filesz in loads:
        if strtab is not None and vaddr <= strtab < vaddr + filesz:
            return off + (strtab - vaddr), tags, entries
    raise SystemExit("DT_STRTAB is in no PT_LOAD")


def set_rpath(path: Path, new: str = RPATH) -> str:
    """Overwrite `path`'s DT_RPATH string in place with `new`, NUL-padded (it must be no longer
    than the old one), and refuse a DT_RUNPATH or a second rpath entry. The old string."""
    data = bytearray(path.read_bytes())
    strtab, _tags, entries = elf_dynamic(bytes(data))
    if any(t == 29 for t, _v, _a in entries):
        raise SystemExit(f"{path} has a DT_RUNPATH; the release expects DT_RPATH alone")
    rpaths = [v for t, v, _a in entries if t == 15]
    if len(rpaths) != 1:
        raise SystemExit(f"{path} has {len(rpaths)} DT_RPATH entries, not one")
    at = strtab + rpaths[0]
    end = data.index(0, at)
    old = data[at:end].decode()
    if len(new) > len(old):
        raise SystemExit(f"{path}: the new rpath {new!r} is longer than {old!r}")
    data[at:end] = new.encode() + b"\0" * (len(old) - len(new))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(bytes(data))
    os.chmod(tmp, 0o755)
    os.replace(tmp, path)
    shown = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True).stdout
    if f"Library rpath: [{new}]" not in shown or "(RUNPATH)" in shown:
        raise SystemExit(f"{path}: readelf does not show the rpath {new!r}:\n{shown}")
    return old


# ---------------------------------------------------------------- the steps

def step_tables(src: Path, work: Path) -> None:
    """The installer's table (`src`'s tools/s7/install_table.py, held to the executables this
    checkout pins) and the code-section overlay table (this checkout's tools/s7/text_readset.py,
    which reads the lift and the executables of the checkout it is in), both into `work`."""
    py = ROOT / ".venv" / "bin" / "python"
    print("== tables", flush=True)
    run([py, src / "tools/s7/install_table.py", "--out-dir", work / "install",
         "--exe", ROOT / "private/exe/sonic2app.exe",
         "--reference", ROOT / "private/exe/sonic2app-unpacked.exe",
         "--pins", ROOT / "tools/pins.json",
         "--drm-receipt", ROOT / "Documentation/receipts/s7-drm-unwrap-v1.json",
         "--receipt", ROOT / "Documentation/receipts/install-hashes-v1.json"],
        work / "logs" / "install_table.log")
    (work / "overlay").mkdir(parents=True, exist_ok=True)
    run([py, ROOT / "tools/s7/text_readset.py", "--out", work / "overlay" / "sa2_exe_overlay_table.c"],
        work / "logs" / "text_readset.log")


def link_inputs() -> tuple[list[Path], list[Path]]:
    """The reach build's generated inputs, as tools/s4/loop_run.py's link_args gives them: the
    edge table, and the lifted extras' C file and objects when there are any."""
    linkdir = GEN / "s4" / "link"
    extra = [linkdir / f"mb_edges.{TAG}.c"]
    if not extra[0].is_file():
        raise SystemExit(f"no generated edge table {rel(extra[0])}; run tools/s4/mb_edges.py")
    state = linkdir / f"extra_functions.{TAG}.json"
    objects: list[Path] = []
    if state.is_file():
        st = json.loads(state.read_text())
        if st.get("objects"):
            extra.append(linkdir / f"extra_functions.{TAG}.c")
            objects = [Path(o) for o in st["objects"]]
    return extra, objects


def f80_command(root: Path, triple: str, mode: str = F80_DEFAULT) -> str:
    """The command that builds `root` as tools/s8/perf_f80.py's build for `triple` in `mode`."""
    return (f"source tools/env.sh; tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build "
            f"--target {triple} --mode {mode} --out {rel(root)} --jobs 4")


def f80_choice(target: str, f80: str | None, f80_objects: Path | None,
               win_objects: Path | None) -> tuple[Path | None, str | None]:
    """(the perf_f80.py build the game links, None for the native-x87 objects; the mode that
    build must be in, None for any legalised one), from the flags (module docstring, "The f80
    mode"): the double build by default, `--f80 native` or `--win-objects` the native-x87 game."""
    if f80_objects is not None and win_objects is not None:
        raise SystemExit("release_build: --f80-objects names the Windows game's objects; drop --win-objects")
    if win_objects is not None and target != "x86_64-windows":
        raise SystemExit("release_build: --win-objects names the Windows game's native-x87 objects; "
                         "the Linux game's are the staged ones (--f80 native)")
    if f80 == "native":
        if f80_objects is not None:
            raise SystemExit("release_build: --f80 native links the native-x87 objects; drop --f80-objects")
        return None, None
    if win_objects is not None:
        if f80 is not None:
            raise SystemExit(f"release_build: --win-objects names native-x87 objects, not a {f80} build; "
                             "drop it, or name the build with --f80-objects")
        return None, None
    if f80_objects is not None:
        return f80_objects, f80
    return F80_OBJECTS[target], f80 or F80_DEFAULT


def f80_deps(mode: str, memfast: bool = False) -> dict[str, str]:
    """perf_f80.py's `deps` for `mode` in this checkout: every input of the mode's pass and shims,
    {checkout-relative path: sha256}, as a build of it now would record them. Its own process, so
    its imports leave this one's alone."""
    if mode not in PERF_F80_MODES:
        raise SystemExit(f"release_build: perf_f80.py builds no {mode!r} mode "
                         f"({', '.join(PERF_F80_MODES)})")
    cmd = [sys.executable, str(PERF_F80), "deps", "--mode", mode] + (["--memfast"] if memfast else [])
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if p.returncode:
        raise SystemExit(f"release_build: {' '.join(cmd[1:])} failed: {p.stderr.strip()[-1500:]}")
    return json.loads(p.stdout)


def f80_build(root: Path, triple: str, mode: str | None = None) -> dict:
    """tools/s8/perf_f80.py's build at `root`, checked for `triple` and, when given, `mode`
    (module docstring, "The f80 mode"); its identity, as the game's notes record it. A missing or
    stale build is refused with the command that rebuilds it."""
    root = root.resolve()
    summary_p, log_p, shims = root / "summary.json", root / "build.jsonl", root / F80_SHIMS
    if not (summary_p.is_file() and log_p.is_file() and shims.is_file()):
        raise SystemExit(f"release_build: {rel(root)} holds no f80 build (summary.json, build.jsonl, "
                         f"{F80_SHIMS}); build it: {f80_command(root, triple, mode or F80_DEFAULT)}")
    s = json.loads(summary_p.read_text())
    problems = []
    if s.get("target") != triple:
        problems.append(f"it was built for {s.get('target')!r}, not {triple}")
    if not s.get("legalized") or not s.get("f80_mode"):
        problems.append("its summary names no legalised f80 mode")
    elif mode is not None and s["f80_mode"] != mode:
        problems.append(f"it is the {s['f80_mode']!r} mode, not {mode}")
    if s.get("modules") != s.get("compiled") or not s.get("modules"):
        problems.append(f"{s.get('compiled')} of its {s.get('modules')} modules compiled")
    if not (s.get("census") or {}).get("ok"):
        problems.append(f"its census is not ok: {(s.get('census') or {}).get('disallowed')}")
    shims_rec = s.get("shims") or {}
    named = {"pass": (s.get("pass"), s.get("pass_sha256")),
             "shims source": (shims_rec.get("source"), shims_rec.get("source_sha256"))}
    for what, (path, want) in named.items():
        if not path or not (ROOT / path).is_file() or sha256(ROOT / path) != want:
            problems.append(f"its {what} {path} is not this checkout's file at {want}")
    deps = s.get("deps")
    for path, want in sorted((deps or {}).items()):
        if (path, want) in named.values():
            continue                            # checked above
        have = sha256(ROOT / path) if (ROOT / path).is_file() else None
        if have is None:
            problems.append(f"its input {path} (built from {want}) is gone from this checkout")
        elif have != want:
            problems.append(f"its input {path} is {have} here, not the {want} it was built from")
    if sha256(shims) != shims_rec.get("object_sha256"):
        problems.append(f"its {F80_SHIMS} is not the object its summary names")
    recs = [json.loads(line) for line in log_p.read_text().splitlines() if line.strip()]
    if len(recs) != s.get("modules") or not all(r.get("compiled") for r in recs):
        problems.append("its build.jsonl does not hold one compiled record per module")
    stale = [r["object"] for r in recs if not r.get("ir_sha256") or not (ROOT / r.get("ir", "")).is_file()
             or sha256(ROOT / r["ir"]) != r["ir_sha256"]]
    if stale:
        problems.append(f"{len(stale)} of its modules were compiled from IR that has changed or "
                        f"gone since: {stale[:3]}")
    if problems:
        again = mode or (s.get("f80_mode") if s.get("f80_mode") in PERF_F80_MODES else F80_DEFAULT)
        raise SystemExit(f"release_build: the f80 build {rel(root)}: " + "; ".join(problems)
                         + f"; rebuild it: {f80_command(root, triple, again)}")
    if deps is None:
        # Built before perf_f80.py recorded deps: what the pass imports and the shims include are
        # named, as a build now would record them, and left unchecked.
        unchecked = (sorted(set(f80_deps(s["f80_mode"], bool(s.get("memfast"))))
                            - {p for p, _ in named.values()})
                     if s["f80_mode"] in PERF_F80_MODES else
                     [f"whatever the {s['f80_mode']} pass imports and its shims include"])
        if unchecked:
            print(f"release_build: the f80 build {rel(root)} records no deps (built before perf_f80.py "
                  f"recorded them); unchecked: {', '.join(unchecked)}", flush=True)
    return {"mode": s["f80_mode"], "triple": triple, "objects": rel(root),
            "summary_sha256": sha256(summary_p), "build_jsonl_sha256": sha256(log_p),
            "pass": s["pass"], "pass_sha256": s["pass_sha256"],
            "shims_source": s["shims"]["source"], "shims_source_sha256": s["shims"]["source_sha256"],
            "shims_sha256": sha256(shims), "modules": s["modules"]}


def f80_linkdir(root: Path, work: Path, staged_extras: list[Path]) -> tuple[Path, list[Path]]:
    """An f80 build's map and arm objects as a link directory under `work` (hard links, at
    <dir>/<TAG>/ as link_s4 reads common.BUILD), and the objects the link adds in place of the
    staged extras: the build's extras in their order, then its shims."""
    import common as C  # noqa: PLC0415  this checkout's, which source_modules imported
    root = root.resolve()
    recs = [json.loads(line) for line in (root / "build.jsonl").read_text().splitlines() if line.strip()]
    lifted = {Path(r["object"]).name: root / r["object"] for r in recs
              if r["population"] in ("map", "arms")}
    staged = {p.name for p in (C.BUILD / TAG).glob("ir_*.o")}
    if set(lifted) != staged:
        raise SystemExit(f"release_build: the f80 build's map and arm objects ({len(lifted)}) are not "
                         f"the staged link directory's ({len(staged)}); they differ by "
                         f"{sorted(set(lifted) ^ staged)[:5]}")
    linkroot = work / "f80-lifted"
    objdir = linkroot / TAG
    objdir.mkdir(parents=True, exist_ok=True)
    for t in objdir.glob("ir_*.o"):
        if t.name not in lifted or not t.samefile(lifted[t.name]):
            t.unlink()
    for name, o in lifted.items():
        if not (objdir / name).exists():
            os.link(o, objdir / name)
    extra = {Path(r["object"]).name: root / r["object"] for r in recs if r["population"] == "extra"}
    missing = [o.name for o in staged_extras if o.name not in extra]
    if missing or len(extra) != len(staged_extras):
        raise SystemExit(f"release_build: the f80 build's extras are not the link's "
                         f"({len(extra)} for {len(staged_extras)}; missing {missing[:3]})")
    return linkroot, [extra[o.name] for o in staged_extras] + [root / F80_SHIMS]


def variants_for(src: Path, variants: Path, objects: Path | None, wraps: list[str]) -> dict:
    """tools/s8/variants/variants.py `linked` for a built variants directory (module docstring,
    "Code-patch tweaks"): its C, objects and wraps, refused by name when they were built for
    other lifted objects than `objects`, or when a wrap is one the link already has."""
    if objects is None:
        raise SystemExit("release_build: --variants are built for an f80 build's lifted objects; the "
                         "native-x87 game (--f80 native) links the staged objects, which they are not")
    v = load(src, "tools/s8/variants/variants.py", "variants").linked(variants, objects)
    clash = sorted(set(v["wraps"]) & set(wraps))
    if clash:
        raise SystemExit(f"release_build: --variants wraps {clash}, which this link already wraps")
    return v


def step_game(src: Path, work: Path, out: Path, f80: Path | None = None,
              f80_note: dict | None = None, variants: Path | None = None) -> dict:
    """The game, linked as play_live links its boot session, with the overlay table and the
    launch observer in the tick probe's place; with `f80`, from that f80 build's lifted image
    (`f80_note` its f80_build check, made here when not given); without, the native-x87 game.
    With `variants`, the code-patch tweaks built there too (variants_for)."""
    print("== game", flush=True)
    m = source_modules(src)
    tr, pr, rr, l4 = m["TR"], m["PR"], m["RR"], m["L4"]
    l4.OUT = work / "s4" / "link"
    l4.RUNTIME = src / "runtime"
    (work / "s3").mkdir(parents=True, exist_ok=True)
    s3 = work / "s3" / "link"
    if not s3.exists():
        s3.symlink_to(GEN / "s3" / "link")      # link_s4 reads the function table there
    obs = work / "game" / OBSERVER
    obs.parent.mkdir(parents=True, exist_ok=True)
    obs.write_text(observer_source(src, state_words(src)))
    overlay = work / "overlay" / "sa2_exe_overlay_table.c"
    if not overlay.is_file():
        raise SystemExit(f"no overlay table at {rel(overlay)}: run the tables step")
    extra, objects = link_inputs()
    if f80 is not None:
        f80_note = f80_note or f80_build(f80, F80_TRIPLES["x86_64-linux"])
        linkroot, objects = f80_linkdir(f80, work, objects)
        l4.C.BUILD = linkroot                   # link_s4 links the ir_*.o of C.BUILD / tag
        print(f"  lifted: the {f80_note['mode']} f80 build {f80_note['objects']}", flush=True)
    hooks = [obs, src / pr.RECORDER]
    wraps = [tr.WRAP] + list(pr.WRAPS)
    extra = extra + hooks
    patches = None
    if variants is not None:
        patches = variants_for(src, variants, f80, list(l4.PRODUCTION_WRAPS) + wraps)
        extra, objects, wraps = (extra + [patches["c"]], objects + patches["objects"],
                                 wraps + patches["wraps"])
        named = [t["name"] + ("" if t.get("shipped") else " (a test fixture, in no tweak list)")
                 for t in patches["note"]["tweaks"]]
        print(f"  variants: {', '.join(named) or 'no code-patch tweak'} ({rel(variants)})", flush=True)
    state = {"name": GAME, "tag": TAG, "hooks": [rel(h) for h in hooks], "wraps": wraps}
    t0 = time.time()
    # rebuild: every object compiled afresh (link_s4's freshness test is mtimes alone).
    l4.phase_compile(state, GAME, TAG, src / rr.MAIN, True, extra, overlay)
    if not state.get("compile_ok"):
        bad = {k: v.get("stderr_tail", "")[-1500:] for k, v in state["compile"].items() if v["rc"]}
        raise SystemExit(f"release_build: the game's compile failed: {json.dumps(bad)[:4000]}")
    l4.phase_link(state, GAME, TAG, [], extra, objects, wraps, overlay)
    link = state["link"]
    (work / "game" / "link.state").write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")
    if link.get("rc"):
        raise SystemExit(f"release_build: the game's link failed: {link.get('stderr_tail', '')[-3000:]}")
    binary = l4.OUT / "mains" / GAME / "sa2_main"
    (out / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(binary, out / "bin" / GAME)
    # The debug information names the build machine's paths and is no use to a player.
    run(["strip", "--strip-debug", out / "bin" / GAME], work / "logs" / "strip.log")
    print(f"  game linked in {time.time() - t0:.0f} s: {link.get('sha256', '')[:16]}", flush=True)
    (out / "share").mkdir(parents=True, exist_ok=True)
    (out / ENV_FILE).write_text("\n".join(env_lines(m)) + "\n")
    overlay_note = dict(link.get("exe_overlay") or {})
    if "table" in overlay_note:
        overlay_note["table"] = Path(str(overlay_note["table"])).name
    note = {"sha256": link.get("sha256"), "shipped_sha256": sha256(out / "bin" / GAME),
            "hooks": state["hooks"], "wraps": wraps, "exe_overlay": overlay_note,
            "observer_sha256": sha256(obs), "probe_args": list(rr.PROBE_ARGS)}
    if f80 is not None:
        note["f80"] = f80_note
    if patches is not None:
        note["variants"] = patches["note"]
    return note


def resolve_lib(name: str, binary: Path) -> Path:
    """`name` as the pinned SDL3/DXVK install holds it, or else as the loader resolves it for
    `binary` (ldd), so the copy is the file the players were built and checked against."""
    for d in (S5_INSTALL / "lib", S5_INSTALL / "lib" / "x86_64-linux-gnu"):
        if (d / name).exists():
            return (d / name).resolve()
    shown = subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout
    for line in shown.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == name and parts[1] == "=>" and parts[2].startswith("/"):
            return Path(parts[2]).resolve()
    raise SystemExit(f"release_build: the loader finds no {name} for {binary}:\n{shown}")


def step_players(src: Path, work: Path, out: Path) -> dict:
    print("== players", flush=True)
    pdir = work / "players"
    pdir.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SA2_", "DXVK_"))}
    env.update(OUT=str(pdir), SA2_REPO=str(src))
    run(["bash", src / "host/player/build.sh"], work / "logs" / "player-build.log", env=env)
    run(["bash", src / "host/d3d9gpu/build.sh"], work / "logs" / "d3d9gpu-build.log", env=env)
    (out / "bin").mkdir(parents=True, exist_ok=True)
    (out / "lib").mkdir(parents=True, exist_ok=True)
    (out / "share").mkdir(parents=True, exist_ok=True)
    rpaths = {}
    for host, name in PLAYERS.items():
        dst = out / "bin" / name
        shutil.copy2(pdir / name, dst)
        rpaths[name] = {"was": set_rpath(dst), "now": RPATH}
    for name in BUNDLED:
        shutil.copyfile(resolve_lib(name, pdir / PLAYERS["dxvk-native"]), out / "lib" / name)
    shutil.copyfile(src / "host/player/dxvk.conf", out / "share" / "dxvk.conf")
    (out / "share" / "licenses").mkdir(parents=True, exist_ok=True)
    for name, notice in LICENSES:
        if not notice.is_file():
            raise SystemExit(f"release_build: no notice for a bundled library at {notice}")
        shutil.copyfile(notice, out / "share" / "licenses" / name)
    return {"rpath": rpaths, "records": {n: sha256(pdir / f"{n}.json") for n in PLAYERS.values()
                                         if (pdir / f"{n}.json").is_file()}}


def step_install(src: Path, work: Path, out: Path) -> None:
    print("== install", flush=True)
    table = work / "install" / "sa2_install_table.cpp"
    run(["bash", src / "host/install/build.sh", "--src", src / "host/install", "--table", table,
         "--out", out / "bin" / INSTALLER], work / "logs" / "install-build.log")


def step_launch(src: Path, work: Path, out: Path) -> None:
    print("== launch", flush=True)
    table = work / "install" / "sa2_install_table.cpp"
    run(["bash", src / "host/launch/build.sh", "--table", table, "--out", out / "bin" / LAUNCHER],
        work / "logs" / "launch-build.log")


def f80_globs(notes: dict) -> tuple:
    """F80_SOURCE_GLOBS when the game the notes describe was linked from an f80 build, else ()."""
    return F80_SOURCE_GLOBS if (notes.get("game") or {}).get("f80") else ()


def variant_globs(notes: dict) -> tuple:
    """When the game the notes describe links code-patch tweaks, the tool and the specs (within the
    checkout) it built them from; else ()."""
    v = (notes.get("game") or {}).get("variants")
    if not v:
        return ()
    return (VARIANT_TOOL,) + tuple(t["spec"] for t in v.get("tweaks", []) if not Path(t["spec"]).is_absolute())


def variant_tweaks(notes: dict) -> tuple:
    """The names of the code-patch tweaks the game the notes describe links (fixtures among them)."""
    v = (notes.get("game") or {}).get("variants") or {}
    return tuple(t["name"] for t in v.get("tweaks", []))


def unlinked_patches(src: Path, game: dict) -> list[str]:
    """The code-patch tweaks `src`'s runtime/sa2_tweaks.h registers that the game (its notes,
    `game`) does not link."""
    have = set(variant_tweaks({"game": game}))
    rows = load(src, "tools/s4/tweak_sources.py", "release_tweak_sources").patches(src / "runtime")
    return [r["name"] for r in rows if r["name"] not in have]


def game_f80_mode(notes: dict) -> str:
    """The f80 mode of the game the notes describe: its f80 build's, else NATIVE_MODE."""
    return ((notes.get("game") or {}).get("f80") or {}).get("mode") or NATIVE_MODE


README = "README.txt"
# The host tweaks' homes a desktop release has (runtime/sa2_tweaks.h, "Add a host tweak"): README.txt
# lists their tweaks with the game's.
DESKTOP_HOMES = ("player", "launcher")
# What README.txt says of each build of the game (player_readme).
README_BUILD = {
    "double": ["  The game here is the double build: its x87 floating point runs in 64-bit doubles at",
               "  the precision the game sets for itself, about twice as fast as the exact build."],
    NATIVE_MODE: ["  The game here is the exact build: its x87 floating point runs in the processor's",
                  "  own 80-bit registers, as the original game's does."],
}


def player_readme(target: str, f80_mode: str = NATIVE_MODE, src: Path = ROOT, linked: tuple = ()) -> str:
    """The player's README.txt at the release's top: how to start, what the first start does,
    controllers, saves and where the logs are, the tweaks (each one's summary in `src`'s
    runtime/sa2_tweaks.h SA2_TWEAK_LIST, but the code-patch tweaks not in `linked`, the ones the
    game links: variant_tweaks; and SA2_TWEAK_HOST_LIST's whose home a desktop has, DESKTOP_HOMES,
    all in name order), and which build of the game it is (`f80_mode`, game_f80_mode's).
    Written by the assemble step, so it is in the manifest."""
    win = target == "x86_64-windows"
    ts = load(src, "tools/s4/tweak_sources.py", "release_tweak_sources")
    left_out = {r["name"] for r in ts.patches(src / "runtime")} - set(linked)
    tweaks = [r for r in ts.every_row(src / "runtime")
              if r["name"] not in left_out and r.get("home", "player") in DESKTOP_HOMES]
    hosts = any("home" in r for r in tweaks)
    if not tweaks:
        raise SystemExit(f"release_build: {src / 'runtime' / 'sa2_tweaks.h'} lists no tweak this game has")
    pad = max(len(t["name"]) for t in tweaks)
    build = README_BUILD.get(f80_mode, [f"  The game here is the {f80_mode} f80 build (tools/s8/perf_f80.py)."])
    run = "bin\\sa2recomp.exe (double-click it)" if win else "bin/sa2recomp"
    data = "%LOCALAPPDATA%\\sa2recomp" if win else "~/.local/share/sa2recomp (and ~/.config/sa2recomp)"
    env = "share\\game.env" if win else "share/game.env"
    return "\n".join([
        "Sonic Adventure 2, recompiled",
        "=============================",
        "",
        "This is a native build of Sonic Adventure 2 (the 2012 PC release). It needs your own copy of",
        "the game, from Steam or a folder, and contains none of the game's files.",
        "",
        "Start",
        f"  Run {run}.",
        "  The first start finds the game in Steam and checks every file (seconds to a minute). If Steam",
        "  has no copy, it asks for the folder that holds sonic2app.exe (or a ZIP of it).",
        "  The game folder is only read, never changed.",
        "",
        "Controls",
        "  Keyboard, as the game's own settings say.",
        "  A controller (Xbox, PlayStation, Switch Pro and most others) drives player 1, connected",
        "  before the game starts or at any time after; while none is connected the keyboard does.",
        "",
        "Saves and logs",
        f"  Saves, settings and each session's logs are under {data}.",
        "",
        "Tweaks",
        "  Optional changes to the game, each off unless named. To turn one on, add a line to",
        f"  {env}:",
        *[f"    SA2_TWEAKS={t['name']:<{pad}}    {t['summary']}" for t in tweaks],
        "  Name several with commas. Each session's game.log begins by saying which are on.",
        *(["  A host tweak (the player's or the launcher's) is said in player.err or launcher.log."]
          if hosts else []),
        "",
        "This build",
        *build,
        "  release.manifest lists every file with its SHA-256.",
        "",
        "Closing the window ends the game.",
        "",
    ])


def step_assemble(src: Path, work: Path, out: Path, notes: dict) -> None:
    print("== assemble", flush=True)
    unlinked = unlinked_patches(src, notes.get("game") or {})
    if unlinked:
        print(f"  code-patch tweaks the game does not link: {', '.join(unlinked)} (README.txt leaves them "
              "out; the game names them if SA2_TWEAKS does)", flush=True)
    (out / README).write_text(player_readme("x86_64-linux", game_f80_mode(notes), src, variant_tweaks(notes)))
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != MANIFEST)
    missing = [n for n in [f"bin/{GAME}", f"bin/{LAUNCHER}", f"bin/{INSTALLER}", ENV_FILE,
                           "share/dxvk.conf"] + [f"bin/{n}" for n in PLAYERS.values()]
               + [f"lib/{n}" for n in BUNDLED] + [f"share/licenses/{n}" for n, _ in LICENSES]
               if not (out / n).is_file()]
    if missing:
        raise SystemExit(f"release_build: the release directory lacks {missing}")
    tables = {"install_table_sha256": sha256(work / "install" / "sa2_install_table.cpp"),
              "exe_overlay_table_sha256": sha256(work / "overlay" / "sa2_exe_overlay_table.c")}
    head = {"schema": SCHEMA, "generated_by": "tools/s7/release_build.py", "target": "x86_64-linux",
            "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "sources": sources(src, SOURCE_GLOBS + f80_globs(notes) + variant_globs(notes)),
            "observer_sha256": sha256(work / "game" / OBSERVER) if (work / "game" / OBSERVER).is_file() else None,
            "host_libraries": list(HOST_LIBS), **tables, **notes}
    # No path of the build machine's: the checkout's are written relative to it, the rest from ~.
    text = json.dumps(head, sort_keys=True).replace(str(ROOT) + "/", "").replace(str(Path.home()) + "/", "~/")
    lines = ["# " + text]
    lines += [f"{sha256(p)}  {p.stat().st_size}  {p.relative_to(out)}" for p in files]
    (out / MANIFEST).write_text("\n".join(lines) + "\n")
    print(f"  {len(files)} files; {rel(out / MANIFEST)}", flush=True)


# ---------------------------------------------------------------- the Windows release

def recorder_source(src: Path) -> str:
    """The unit the Windows game compiles host/sa2_present_rec.c through: the one POSIX name it
    uses that the Windows C library does not declare, O_CLOEXEC, as runtime/sa2_win32.h defines
    it (_O_NOINHERIT: the descriptor is not inherited by a child), then the recorder unchanged."""
    rec = (src / "host" / "sa2_present_rec.c").resolve()
    return f"""/* Generated by tools/s7/release_build.py (recorder_source); do not edit.
 *
 * host/sa2_present_rec.c for the Windows game: <fcntl.h> of mingw-w64 has no O_CLOEXEC, which the
 * recorder's open of SA2_REC_TRACE names; runtime/sa2_win32.h gives the runtime the same name as
 * _O_NOINHERIT. The recorder itself is included unchanged.
 */
#include <fcntl.h>
#ifndef O_CLOEXEC
#define O_CLOEXEC _O_NOINHERIT
#endif
#include "{rec}"
"""


def win_objects(given: Path | None) -> Path:
    for d in ((given,) if given else WIN_OBJECTS):
        if d is not None and (d / "build.jsonl").is_file():
            return d
    raise SystemExit(f"release_build: no cross-built objects for {WIN_TRIPLE} at "
                     f"{[rel(d) for d in ((given,) if given else WIN_OBJECTS)]}; run tools/s7/cross_build.py")


def env_lines(m: dict) -> list[str]:
    """share/game.env's lines: play_live's boot session's settings with the recorder's sound."""
    rr = m["RR"]
    env = dict(rr.ENV)
    env.update(rr.profile_env(m["DP"].DEFAULT))
    env.update(dict(m["PL"].UNBOUNDED))
    env["SA2_REC_AUDIO"] = "1"
    lines = ["# Generated by tools/s7/release_build.py: the game's settings, play_live's boot",
             "# session's (reach ENV, the default profile, no bound) with the recorder's sound."]
    return lines + [f"{k}={v}" for k, v in env.items()]


def step_game_windows(src: Path, work: Path, out: Path, objects: Path,
                      f80_note: dict | None = None) -> dict:
    """The Windows game: win_link.py with the Linux game's hooks, wraps and overlay table; with
    `f80_note` (f80_build's, for `objects`), the f80 build's shims linked after its objects."""
    print("== game (x86_64-windows)", flush=True)
    m = source_modules(src)
    tr, pr = m["TR"], m["PR"]
    gdir = work / "game"
    gdir.mkdir(parents=True, exist_ok=True)
    obs = gdir / OBSERVER
    obs.write_text(observer_source(src, state_words(src), strong=True))
    shim = gdir / RECORDER_SHIM
    shim.write_text(recorder_source(src))
    overlay = work / "overlay" / "sa2_exe_overlay_table.c"
    if not overlay.is_file():
        raise SystemExit(f"no overlay table at {rel(overlay)}: run the tables step")
    wraps = [tr.WRAP] + list(pr.WRAPS)
    cmd = [ROOT / ".venv" / "bin" / "python", src / "tools/s7/win_link.py", "--out", gdir / "link",
           "--src", src, "--objects", objects, "--exe-overlay", overlay, "--jobs", "4"]
    for h in (obs, shim):
        cmd += ["--hook", h]
    for w in wraps:
        cmd += ["--wrap", w]
    if f80_note is not None:
        cmd += ["--extra-object", objects / F80_SHIMS]
    run(cmd, work / "logs" / "win_link.log")
    record = json.loads((gdir / "link" / "link.record").read_text())
    exe = gdir / "link" / "sa2_main.exe"
    if record.get("link", {}).get("sha256") != sha256(exe):
        raise SystemExit(f"release_build: {rel(exe)} is not the binary its link.record names")
    (out / "bin").mkdir(parents=True, exist_ok=True)
    place(exe, out / "bin" / f"{GAME}.exe")
    (out / "share").mkdir(parents=True, exist_ok=True)
    (out / ENV_FILE).write_text("\n".join(env_lines(m)) + "\n")
    overlay_note = dict(record.get("exe_overlay") or {})
    if "table" in overlay_note:
        overlay_note["table"] = Path(str(overlay_note["table"])).name
    note = {"sha256": record["link"]["sha256"], "shipped_sha256": sha256(out / "bin" / f"{GAME}.exe"),
            "hooks": [obs.name, shim.name], "wraps": wraps, "exe_overlay": overlay_note,
            "observer_sha256": sha256(obs), "recorder_shim_sha256": sha256(shim),
            "objects": record.get("objects"), "link_response_sha256": record["link"].get("response_sha256")}
    if f80_note is not None:
        if [x.get("sha256") for x in record.get("extra_objects") or []] != [f80_note["shims_sha256"]]:
            raise SystemExit(f"release_build: {rel(exe)}'s link.record does not name the f80 shims")
        note["f80"] = f80_note
    return note


def win_licenses() -> tuple:
    """(the release's name, the notice) of every library the Windows binaries carry."""
    zig = Path(shutil.which("zig") or "").resolve()
    mingw = zig.parent / "lib" / "libc" / "mingw" / "COPYING" if zig.name else Path("/nonexistent")
    dxvk = ROOT / "private/s5-tools/src/dxvk-3.1.1"
    src = HOST2_WIN / "src"
    return (("SDL3.txt", src / "SDL3-3.4.16/LICENSE.txt"),
            ("DXVK.txt", dxvk / "LICENSE"),
            ("dxbc-spirv.txt", dxvk / "subprojects/dxbc-spirv/LICENSE"),
            ("libpng.txt", src / "libpng-1.6.59/LICENSE"),
            ("zlib.txt", src / "zlib-1.3.2/LICENSE"),
            ("zstd.txt", src / "zstd-1.5.7/LICENSE"),
            ("mingw-w64.txt", mingw))


def step_players_windows(src: Path, work: Path, out: Path) -> dict:
    """Host 2 for Windows: tools/s7/host2_win_build.py (cached by its stamps), its player and
    SDL3.dll copied beside the game."""
    print("== players (x86_64-windows)", flush=True)
    run([ROOT / ".venv" / "bin" / "python", src / "tools/s7/host2_win_build.py", "--out", HOST2_WIN],
        work / "logs" / "host2_win_build.log")
    record = json.loads((HOST2_WIN / "host2_win_build.runrec").read_text())
    (out / "bin").mkdir(parents=True, exist_ok=True)
    for name in ("sa2_player_gpu.exe", "SDL3.dll"):
        got = sha256(HOST2_WIN / "bin" / name)
        if record.get("outputs", {}).get(f"bin/{name}") != got:
            raise SystemExit(f"release_build: {name} is not the file host2_win_build.runrec names")
        place(HOST2_WIN / "bin" / name, out / "bin" / name)
    (out / "share" / "licenses").mkdir(parents=True, exist_ok=True)
    for name, notice in win_licenses():
        if not notice.is_file():
            raise SystemExit(f"release_build: no notice for a bundled library at {notice}")
        shutil.copyfile(notice, out / "share" / "licenses" / name)
    return {"host2_win_build": {"runrec_sha256": sha256(HOST2_WIN / "host2_win_build.runrec"),
                                "outputs": {k: v for k, v in record.get("outputs", {}).items()
                                            if k.startswith("bin/")}}}


def step_install_windows(src: Path, work: Path, out: Path) -> None:
    print("== install (x86_64-windows)", flush=True)
    run(["bash", src / "host/install/build.sh", "--src", src / "host/install", "--cxx", "zig",
         "--target", WIN_TRIPLE, "--table", work / "install" / "sa2_install_table.cpp",
         "--out", out / "bin" / f"{INSTALLER}.exe"], work / "logs" / "install-build.log")


def step_launch_windows(src: Path, work: Path, out: Path) -> None:
    print("== launch (x86_64-windows)", flush=True)
    run(["bash", src / "host/launch/build.sh", "--cxx", "zig", "--target", WIN_TRIPLE,
         "--table", work / "install" / "sa2_install_table.cpp", "--out", out / "bin" / f"{LAUNCHER}.exe"],
        work / "logs" / "launch-build.log")


def step_assemble_windows(src: Path, work: Path, out: Path, notes: dict) -> None:
    print("== assemble (x86_64-windows)", flush=True)
    unlinked = unlinked_patches(src, {})
    if unlinked:
        print(f"  code-patch tweaks the Windows game does not link (tools/s7/win_link.py takes no "
              f"variants yet): {', '.join(unlinked)}; README.txt leaves them out", flush=True)
    (out / README).write_text(player_readme("x86_64-windows", game_f80_mode(notes), src), newline="\r\n")
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != MANIFEST)
    missing = [n for n in [f"bin/{GAME}.exe", f"bin/{LAUNCHER}.exe", f"bin/{INSTALLER}.exe",
                           "bin/sa2_player_gpu.exe", "bin/SDL3.dll", ENV_FILE]
               + [f"share/licenses/{n}" for n, _ in win_licenses()] if not (out / n).is_file()]
    if missing:
        raise SystemExit(f"release_build: the release directory lacks {missing}")
    tables = {"install_table_sha256": sha256(work / "install" / "sa2_install_table.cpp"),
              "exe_overlay_table_sha256": sha256(work / "overlay" / "sa2_exe_overlay_table.c")}
    head = {"schema": SCHEMA, "generated_by": "tools/s7/release_build.py", "target": "x86_64-windows",
            "triple": WIN_TRIPLE, "written": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "sources": sources(src, SOURCE_GLOBS + WIN_SOURCE_GLOBS + f80_globs(notes)),
            "observer_sha256": sha256(work / "game" / OBSERVER) if (work / "game" / OBSERVER).is_file() else None,
            "host_libraries": ["the system's UCRT (Windows 10 and later)",
                               "a Vulkan or Direct3D 12 driver (through SDL3's GPU API)"],
            **tables, **notes}
    text = json.dumps(head, sort_keys=True).replace(str(ROOT) + "/", "").replace(str(Path.home()) + "/", "~/")
    lines = ["# " + text]
    lines += [f"{sha256(p)}  {p.stat().st_size}  {p.relative_to(out).as_posix()}" for p in files]
    (out / MANIFEST).write_text("\n".join(lines) + "\n")
    print(f"  {len(files)} files; {rel(out / MANIFEST)}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--src", type=Path, default=ROOT, help="the sources (default this checkout)")
    ap.add_argument("--target", choices=TARGETS, default=TARGETS[0])
    ap.add_argument("--out", type=Path, default=None, help="default: the target's release directory")
    ap.add_argument("--work", type=Path, default=None, help="default: the target's build directory")
    ap.add_argument("--f80", choices=F80_CHOICES, default=None,
                    help="the game's f80 mode: double (the default: tools/s8/perf_f80.py's double "
                         f"build, {', or '.join(rel(d) for d in F80_OBJECTS.values())}) or native "
                         "(the exact native-x87 game) (module docstring, \"The f80 mode\")")
    ap.add_argument("--win-objects", type=Path, default=None,
                    help=f"(--f80 native, which it implies) cross_build.py's native-x87 objects for "
                         f"{WIN_TRIPLE} (default: {', or '.join(rel(d) for d in WIN_OBJECTS)})")
    ap.add_argument("--f80-objects", type=Path, default=None,
                    help="another tools/s8/perf_f80.py build for the target's triple "
                         "(x86_64-linux-musl or x86_64-windows-gnu) in place of the default double "
                         "build: its lifted objects and f80 shims")
    ap.add_argument("--variants", type=Path, default=None,
                    help="(x86_64-linux) a directory tools/s8/variants/variants.py built for the "
                         "game's lifted objects: its code-patch tweaks are linked into the game "
                         "(module docstring, \"Code-patch tweaks\")")
    ap.add_argument("--only", default=",".join(STEPS))
    a = ap.parse_args(argv)
    f80_dir, f80_mode = f80_choice(a.target, a.f80, a.f80_objects, a.win_objects)
    windows = a.target == "x86_64-windows"
    if a.variants is not None and windows:
        raise SystemExit("release_build: --variants is linked into the Linux game only; the Windows "
                         "game's link (tools/s7/win_link.py) does not take them yet")
    a.out = a.out or (WIN_OUT if windows else OUT)
    a.work = a.work or (WIN_WORK if windows else WORK)
    # Absolute, not resolved: in a worktree whose private/ is a link to the main checkout's, a
    # resolved --out leaves GEN (this checkout's private/generated) and link_s4's records.
    src, out, work = a.src.resolve(), Path(os.path.abspath(a.out)), Path(os.path.abspath(a.work))
    steps = [s for s in a.only.split(",") if s]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        raise SystemExit(f"release_build: no step {bad}; the steps are {', '.join(STEPS)}")
    for d in (out, work):
        if not d.is_relative_to(GEN):
            raise SystemExit(f"release_build: {d} is not under private/generated, which is never "
                             "tracked; the release holds the lifted game")
    # The f80 build is checked before any step, so a missing or stale one is refused at once.
    f80_note = None
    if "game" in steps:
        f80_dir = f80_dir.resolve() if f80_dir is not None else None
        f80_note = f80_build(f80_dir, F80_TRIPLES[a.target], f80_mode) if f80_dir is not None else None
        print(f"== f80: the {f80_note['mode']} build {f80_note['objects']}" if f80_note else
              f"== f80: {NATIVE_MODE}", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    notes_path = work / "notes.state"
    notes = json.loads(notes_path.read_text()) if notes_path.is_file() else {}
    if windows:
        objects = (f80_dir or win_objects(a.win_objects)) if "game" in steps else None
        if "tables" in steps:
            step_tables(src, work)
        if "game" in steps:
            notes["game"] = step_game_windows(src, work, out, objects, f80_note)
        if "players" in steps:
            notes["players"] = step_players_windows(src, work, out)
        if "install" in steps:
            step_install_windows(src, work, out)
        if "launch" in steps:
            step_launch_windows(src, work, out)
        merged = json.loads(notes_path.read_text()) if notes_path.is_file() else {}
        merged.update({k: v for k, v in notes.items() if k in steps})
        notes_path.write_text(json.dumps(merged, indent=1, sort_keys=True) + "\n")
        if "assemble" in steps:
            step_assemble_windows(src, work, out, merged)
        print("RELEASE_BUILD_OK", flush=True)
        return 0
    if "tables" in steps:
        step_tables(src, work)
    if "game" in steps:
        notes["game"] = step_game(src, work, out, f80_dir, f80_note, a.variants)
    if "players" in steps:
        notes["players"] = step_players(src, work, out)
    if "install" in steps:
        step_install(src, work, out)
    if "launch" in steps:
        step_launch(src, work, out)
    # Merged with what is on disk now, so two steps run apart keep each other's notes.
    merged = json.loads(notes_path.read_text()) if notes_path.is_file() else {}
    merged.update({k: v for k, v in notes.items() if k in steps})
    notes = merged
    notes_path.write_text(json.dumps(notes, indent=1, sort_keys=True) + "\n")
    if "assemble" in steps:
        step_assemble(src, work, out, notes)
    print("RELEASE_BUILD_OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
