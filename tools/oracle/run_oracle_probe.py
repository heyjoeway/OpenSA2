#!/usr/bin/env python3
"""Run one bounded native capture of the shipped game, and leave a receipt.

Subtitle: the live oracle for S4, without writing the user's installation.

This is the successor to the predecessor's `run_sa2_native_probe.py`
(`Documentation/predecessor/NATIVE_PROBE_RUNNER.md`). The sequence is the
same one that document describes — lease, verify hashes, compile the hook
DLL, stage a temporary profile, run under Proton with a bounded timeout,
collect the capture, write a manifest. Four things are different, each for a
reason the records name:

1. **It never writes the installation.** The predecessor created a mod
   directory inside the private game copy, rewrote the Mod Loader profile
   index, and restored both in a `finally`. This runs the game from a mirror
   (`mirror_install.py`) whose shipped bulk is shared by symlink and whose
   writable directories are copies, and then *proves* the installation is
   untouched by comparing a full content snapshot taken before and after
   (`tree_snapshot.py`). The proof is the point: the restore-afterwards
   design has no way to notice a file the game created. The game's save
   directory is a copy too, put back from the installation before every run
   (`mirror_install.reset_save_dir`), so every run starts from the
   installation's saves and the receipt records them (session.saves); the
   path to it is checked before and after every run, because a write there
   is one the snapshot cannot see (`mirror_install.save_path_problems`,
   receipt `mirror_path_differences`). The saves are a recorded input (the
   user's ruling of 2026-09-24): a run does not start unless the
   installation's are the pinned start state (`tools/oracle/save_state.py`,
   `Documentation/save-state.json`), and the receipt records the pin's
   digest and whether the saves the run started from matched it.

2. **It checks that the capture is a capture.** The predecessor accepted any
   non-empty output file. Four of its 395 surviving captures end in a row
   torn off mid-write when `timeout` killed the game, and nothing looked.
   `capture_check.py` runs on every artifact and the result is in the
   receipt; a torn trailing row is reported, and dropped only with
   --trim-torn-row, which records what it dropped.

3. **It records the session.** Captures taken here run on the user's live
   desktop session, because this machine has no headless display: with
   DISPLAY and WAYLAND_DISPLAY unset the game exits immediately with code 5.
   The receipt says so, so a later reader knows which captures could have
   been affected by window focus. Since 2026-09-23 the game is paced by the
   Mod Loader's frame limiter and presents to its own top-level window
   (profile_for), and whether the window's visibility changes that pace is
   not measured. The receipt also records the display's current modes
   (session.display_modes: the refresh rate sets the unlimited rate) and the
   environment the game is launched with (game_environment), by name, size
   and digest (session.environment) and in full (session.game_environment):
   native heap addresses differed between runs launched from two
   environments (agents-journals/s4/2026-09-23-recording-session.md), so the
   game gets a fixed one (XAUTHORITY given as a link the runner points at
   the session's file, whose own name is new at every login:
   session.xauthority), and the recorder refuses a replay launched with
   another than its recording's. With
   --device-profile it also records the host's devices as the game sees
   them (harness/replay/native_device_profile.c): the file stays beside the
   capture, and session.device_profile keeps its sha256 and counts for the
   lifted replay that reads it back.

4. **Input never leaves the game process.** There is no OS-level input path
   here and there must not be one. Scripted input is the hook's job: the
   probe DLL's `OnControl` writes the game's own projected-input globals.
   The runner passes argv to the game and nothing else, and refuses a game
   argument that is one of its own flags: everything after `--` reaches the
   game, and a runner flag put there once did, as two arguments that moved
   the game's heap (private/generated/s4/wordobs/reports/judge-plan.md,
   run B).

5. **The tick probe's word log is checked, and the digests are computed
   from it** (the judge's plan, private/generated/s4/wordobs/reports/
   judge-plan.md; harness/replay/word_log_core.h; tools/s4/wordlog.py). A
   run that builds harness/replay/native_tick_probe.c must collect its log
   (`--binary-capture sa2-word-log.bin`, a runner flag, so before `--`), and
   the capture is refused when the probe left none or the log is not whole,
   planned, and equal to the probe's own row on every tick
   (wordlog.check_capture). The probe's row carries the named fields alone:
   it is kept as sa2-tick-named.csv, and sa2-tick-capture.csv, the file every
   consumer reads, is built from the log with the digest columns of the
   tree's mask (wordlog.capture_csv), so a mask is an input of the host and
   never of the game. The log is kept compressed (zstd) once the compressed
   file is shown to decompress to it, and the receipt records what
   wordlog.receipt_fields says of it (`word_log`), with the time the oracle
   had been idle before the launch and the host's load average
   (session.idle_before_run, session.load_average), since whether the game
   takes CRI's second path at tick 369 has looked like it follows how long
   the machine sat idle (design-always-on-snapshots.md). The log stays
   private: its keyframe is the game's own data section.

6. **The receipt names the Proton build, and a batch can hold the launch.**
   The wrapper this runner launches (PROTON) is a private copy of Proton's
   launcher script whose `files` is a link into Steam's own
   `Proton - Experimental`, which Steam updates in place: it moved from
   20260917b to 20260924 at 04:27 on 2026-09-26 under U6's captures, and the
   wrapper's own `version` file still names 20260910b
   (private/generated/s4/n0/, the N0 canary). So every receipt records
   `proton_build()` as session.proton: the launcher's sha256, the build the
   distribution's `version` file names, and a digest of every file under the
   linked `files` tree by name, size and time. tools/oracle/oracle_mode.py
   compares it between captures once they record it (UNTIL_RECORDED_SESSION),
   so a mask is never chosen from runs of two builds. When the environment
   names a launch gate (LAUNCH_GATE, which tools/oracle/retake_batch.py
   writes), the runner holds the launch until the gate's conditions hold, or
   refuses the run without launching (`launch_gate`), and the receipt records
   what the gate measured (session.launch_gate; null without a gate).
   Neither touches the loader's profile (profile_for) or the launch command.
   The build is read again once the game has exited (session.proton_at_end),
   since Steam updates it under a running game too, and it holds what else a
   launch reads that Steam or a person can change: the wrapper's
   user_settings.py and the Steam client's files (STEAM_CLIENT_FILES). The
   host's state at launch and at the end (session.host_state, host_state):
   whether the desktop session was locked or idle, and the suspend clock,
   which moves only while the host is suspended, so a suspend inside a run
   shows although elapsed_seconds is monotonic. The gate also refuses a game
   still running outside any runner (game_pids), a Steam client that
   restarted, a host suspended since the batch began, and a batch that no
   longer holds its sleep inhibitor (sleep_inhibitors).

Usage (from the repository root, after `source tools/env.sh`):

  .venv/bin/python tools/oracle/run_oracle_probe.py \\
      --name mh-trace-v9a \\
      --build vanilla_probe=harness/replay/native_vanilla_probe.c \\
      --build stage_trace=harness/replay/native_stage_trace.c \\
      --define stage_trace:TICK_LIMIT=600 --device-profile \\
      --timeout 240 -- --level metalharbor --character sonic

  # or with a freshly compiled hook:
  .venv/bin/python tools/oracle/run_oracle_probe.py \\
      --name one-sided-v11 --source <tracked.c> --mod-id one_sided_oracle_v2 \\
      --capture sa2-one-sided-oracle-v1.csv --timeout 240
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
from typing import Iterator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))

import capture_check  # noqa: E402
import oracle_mode as OM  # noqa: E402
import mirror_install  # noqa: E402
import save_state as SS  # noqa: E402  the pinned start state every run is held to
import tree_snapshot  # noqa: E402

# The device profile's reader, the same one the replay tools use, so a receipt
# says whether the lifted build can replay the capture (tools/s4/device_profile.py).
sys.path.append(str(ROOT / "tools" / "s4"))
import device_profile as DP  # noqa: E402
# The word log's reader and capture check (tools/s4/wordlog.py): the one
# definition of what a native log must be, and of the digest columns it gives.
import wordlog as WL  # noqa: E402

# The private tree, resolved. Proton records in the prefix the paths it was
# launched by (its config_info names the wrapper's files/ directories) and
# updates the prefix when those strings change, so a copy of these tools in a
# scratch tree whose `private` links here must launch the one oracle by the
# path strings the tree itself uses. In the tree itself `private` is a
# directory, and this is ROOT / "private" exactly.
PRIVATE = (ROOT / "private").resolve()
OUT = PRIVATE / "generated" / "oracle"
MIRROR = OUT / "game"
CAPTURES = OUT / "captures"
LOCK = OUT / "locks" / "oracle-probe.lock"
# Written when each run's game has exited: what `idle_before_run` measures from.
LAST_EXIT = "last-game-exit.json"          # under OUT / "locks", read when called
INSTALL = PRIVATE / "install"
PREFIX = PRIVATE / "proton-prefix"
PROTON = PRIVATE / "predecessor-tools" / "proton-private-wrapper" / "proton"
STEAM_CLIENT = Path.home() / ".local/share/Steam"
# The Steam client's own process, which the game needs: without it the game
# stops at a message box, and with it Steam fills the achievement table at
# tick 4 (steam_pids).
STEAM_BINARY = STEAM_CLIENT / "ubuntu12_32" / "steam"
PROC = Path("/proc")
PINS = ROOT / "tools" / "pins.json"
# The launch gate (the module docstring, 6): the environment variable naming
# the gate's file, the file's schema, and the word every refusal starts with.
LAUNCH_GATE = "SA2_ORACLE_LAUNCH_GATE"
GATE_SCHEMA = "sa2-oracle-launch-gate/v1"
GATE_REFUSED = "SA2_ORACLE_LAUNCH_REFUSED"
# --device-profile: the probe that records the host's device answers, built as
# this mod and loaded after every other probe.
PROFILE_PROBE = ROOT / DP.PROBE
PROFILE_MOD = "device_profile"

# Widths the movement-trace format writes legitimately: `tick,player,entity`
# when the player or entity pointer is null.
SHORT_ROW_WIDTHS = (3,)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


@contextmanager
def lease(wait_seconds: float) -> Iterator[None]:
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK.open("a+", encoding="utf-8")
    deadline = time.monotonic() + wait_seconds
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.seek(0)
                    raise SystemExit(
                        f"another oracle run holds {LOCK}: {handle.read().strip()}"
                    ) from None
                time.sleep(0.2)
        handle.seek(0)
        handle.truncate()
        json.dump(
            {
                "owner_pid": os.getpid(),
                "hostname": socket.gethostname(),
                "start_utc": datetime.now(timezone.utc).isoformat(),
                "command": shlex.join([sys.executable, *sys.argv]),
            },
            handle,
            indent=1,
        )
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def verify_executable() -> str:
    pins = json.loads(PINS.read_text())
    expected = pins["executables"]["sonic2app-unpacked.exe"]["sha256"]
    executable = (INSTALL / "sonic2app-unpacked.exe").resolve()
    actual = sha256(executable)
    if actual != expected:
        raise SystemExit(
            f"the installation's unpacked executable does not match tools/pins.json:\n"
            f"  {executable}\n  expected {expected}\n  actual   {actual}"
        )
    return actual


def compile_probe(source: Path, mod_id: str, defines: list[str]) -> tuple[Path, str]:
    zig = PRIVATE / "predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig"
    if not zig.is_file():
        raise SystemExit(f"pinned zig is missing: {zig}")
    build = OUT / "probe-build" / mod_id
    build.mkdir(parents=True, exist_ok=True)
    dll = build / f"{mod_id}.dll"
    command = [
        str(zig), "cc", "-target", "x86-windows-gnu", "-shared", "-O2",
        "-Wall", "-Wextra", "-Werror",
        f"-Wl,--out-implib,{build / (mod_id + '.lib')}",
        *[f"-D{d}" for d in defines],
        "-o", str(dll), str(source),
    ]
    environment = dict(os.environ)
    environment["ZIG_LOCAL_CACHE_DIR"] = str(OUT / "zig-cache" / mod_id)
    environment["ZIG_GLOBAL_CACHE_DIR"] = str(OUT / "zig-cache" / "global")
    subprocess.run(command, check=True, env=environment)
    described = subprocess.run(
        ["file", str(dll)], check=True, capture_output=True, text=True
    ).stdout
    if "PE32" not in described or not (
        "Intel 80386" in described or "Intel i386" in described
    ):
        raise SystemExit(f"probe DLL is not a 32-bit PE: {described.strip()}")
    problems = export_problems(dll)
    if problems:
        raise SystemExit(f"probe DLL {dll.name}: " + "; ".join(problems))
    return dll, sha256(dll)


# What the Mod Loader itself requires (SA2ModLoader/dllmain.cpp: without
# SA2ModInfo a DLL "is not a valid mod file"), and the callbacks through which a
# probe can do anything at all. `Init` is optional to the loader, and four
# probes this runner has built have none; requiring it, as this check once
# claimed to, would refuse them.
REQUIRED_EXPORTS = ("SA2ModInfo",)
CALLBACKS = ("Init", "OnFrame", "OnInput", "OnControl", "OnExit")
# The loader's render events (SA2ModLoader/dllmain.cpp 1378-1381), raised from
# its Direct3D wrappers. The vanilla probe keeps the BeginScene wrapper for the
# frame limiter since 2026-09-23, and that wrapper raises OnRenderSceneStart
# inside every frame (direct3d.cpp, BeginScene_r). No probe exports one, and no
# probe may: its code would run where no capture has measured it.
RENDER_EVENTS = ("OnRenderSceneStart", "OnRenderSceneEnd", "OnRenderDeviceLost",
                 "OnRenderDeviceReset")


def export_problems(dll: Path) -> list[str]:
    names = dll_exports(dll)
    out = [f"does not export {s}" for s in REQUIRED_EXPORTS if s not in names]
    if not any(c in names for c in CALLBACKS):
        out.append(f"exports none of the loader's callbacks {list(CALLBACKS)}, so it can "
                   "record nothing")
    out += [f"exports {e}, a render event the Mod Loader raises from its Direct3D wrappers; "
            "no probe may run there" for e in RENDER_EVENTS if e in names]
    return out


def dll_exports(dll: Path) -> set[str]:
    """The names in a DLL's export directory, and nothing else.

    This was a substring search of `objdump -p`, whose output also carries the
    optional header and the import table, so "Init" matched
    `SizeOfInitializedData` and an imported `InitializeCriticalSection` and a
    probe with no Init export passed. Found 2026-09-22 when a probe without one
    was about to be built; every DLL in the installation passed it the same way.
    """
    import pefile  # the venv's; imported here so the module loads without it

    image = pefile.PE(str(dll), fast_load=True)
    image.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
    table = getattr(image, "DIRECTORY_ENTRY_EXPORT", None)
    return {s.name.decode() for s in table.symbols if s.name} if table else set()


# The Mod Loader's game patches (SA2ModLoader/config.cpp, patches.cpp). Every
# one is written out: `"Patches": {}` looks like "none" and is the opposite,
# because the loader reads each missing key as `value(key, true)` once the
# object exists. Until 2026-09-22 every capture this runner took had all eight
# applied -- SyncLoad among them, which replaces the game's threaded loaders at
# 0x00428470 and 0x00428740 -- and nothing recorded it. All are false except
# FramerateLimiter, which patches no game code (it only sets the loader's
# enable_frame_limit: config.cpp 182, dllmain.cpp 1105-1108, direct3d.cpp
# 115-118) and has been the pace since the evening of 2026-09-23. The user
# adopted this configuration on 2026-09-24, as option A of the vanilla
# decision's addendum (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md).
GAME_PATCHES = ("FramerateLimiter", "DisableExitPrompt", "SyncLoad", "ExtendVertexBuffer",
                "EnvMapFix", "ScreenFadeFix", "CECarFix", "ParticlesFix")


def profile_for(mods: list[str], resolution: tuple[int, int], skip_intro: bool,
                pause_on_inactive: bool, text_language: int, voice_language: int) -> dict:
    """Every setting the loader reads, stated, so none arrives as a default.

    `EnablePauseOnInactive` defaults to true, and then the loader's window
    procedure zeroes WindowActive on focus loss and WinMain stops calling
    GameLoop (0x00401DE0 -> 0x00425DA0): on a live desktop, a click elsewhere
    would change the capture. `GameVoiceLanguage` is written into the game at
    init whatever the game's own configuration says.

    The resolution fields and the custom window size are read and not used in
    ScreenMode 0 (config.cpp 139-144; window.cpp 355-364): the window's client
    area and the back buffer come from the game's own Config/UserConfig.cfg,
    1280x720 in the mirror, and nothing is stretched. The document itself must
    stay byte for byte what it is while captures taken with it are replayed:
    its digest is part of every capture's oracle mode
    (tools/oracle/oracle_mode.py config_of), and a replay whose mode differs
    from its recording's is refused.
    """
    return {
        "SettingsVersion": 3,
        "Graphics": {
            "ScreenMode": 0,
            "SelectedScreen": 0,
            "HorizontalResolution": resolution[0],
            "VerticalResolution": resolution[1],
            "EnablePauseOnInactive": pause_on_inactive,
            "EnableCustomWindow": False,
            "CustomWindowWidth": resolution[0],
            "CustomWindowHeight": resolution[1],
            "EnableResizableWindow": False,
            # True since 2026-09-23: the loader creates no child window over
            # the game's own (window.cpp 312, 337, 468-497), and its Present
            # wrapper makes the game's own call. With the child, Wine rendered
            # it offscreen and never copied it to the screen on this machine:
            # every capture before then ran with a black picture, and the user
            # could not see to play. The child's offscreen present had also
            # been the pace (58.5 to 58.8 Hz); without it and without the
            # limiter the game ran at several times 60 Hz on this 240 Hz
            # display (stretch-true-v1a, v1b). Adopted by the user on
            # 2026-09-24 (option A of the vanilla decision's addendum).
            "StretchToWindow": True,
            "SkipIntro": skip_intro,
            "DisableBorderImage": False,
        },
        # FramerateLimiter is the loader's own limiter, not a patch of the
        # game's code. It runs from the BeginScene wrapper, the one range the
        # vanilla probe keeps (direct3d.cpp 27-50, BeginScene_r), and holds
        # each frame to at least 1/60 s of wall clock, never making up a slow
        # one: a floor, not a lock. It is the pace of every capture since
        # 2026-09-23; how far runs fall below 60 Hz, and why, is in
        # Documentation/risks.json, "Determinism (time, RNG seeding)".
        "Patches": {p: p == "FramerateLimiter" for p in GAME_PATCHES},
        "DebugSettings": {"EnableDebugConsole": False, "EnableDebugScreen": False,
                          "EnableDebugFile": True, "EnableDebugCrashLog": True},
        "TestSpawn": {"GameTextLanguage": text_language, "GameVoiceLanguage": voice_language},
        "EnabledMods": mods,
    }


def stage_profile(profile_doc: dict) -> Path:
    profiles = MIRROR / "mods" / ".modloader" / "profiles"
    profiles.mkdir(parents=True, exist_ok=True)
    name = "SA2OracleProbe"
    profile = profiles / f"{name}.json"
    profile.write_text(json.dumps(profile_doc, separators=(",", ":")))
    (profiles / "Profiles.json").write_text(json.dumps({
        "ProfileIndex": 0,
        "ProfilesList": [{"Name": name, "Filename": profile.name}],
    }, separators=(",", ":")))
    return profile


def trim_torn_row(path: Path) -> dict | None:
    """Drop a trailing row the kill signal cut in half, and say what was dropped."""
    data = path.read_bytes()
    if not data.endswith(b"\n"):
        cut = data.rfind(b"\n")
        if cut < 0:
            return None
        dropped = data[cut + 1:]
        path.write_bytes(data[: cut + 1])
        return {"bytes_dropped": len(dropped), "reason": "no trailing newline"}
    return None


# BTN_JOYSTICK .. BTN_GAMEPAD's range (linux/input-event-codes.h: 0x120-0x13f):
# the buttons that make evdev, joydev and Wine's winebus call a device a game
# controller. A keyboard or mouse never reports them.
CONTROLLER_BUTTONS = range(0x120, 0x140)


def host_game_controllers(text: str | None = None) -> list[str]:
    """The host's input devices that are game controllers, by name.

    Wine hands a host controller to the game as a DirectInput and XInput
    device, and the game enumerates devices before the Mod Loader's Init, where
    no probe can see what that calls (2026-09-23 review of the import log). The
    receipt records the answer so a capture's device environment is a fact and
    not an inference from which imports it happened to call.
    """
    if text is None:
        try:
            text = Path("/proc/bus/input/devices").read_text()
        except OSError:
            return ["<unreadable: /proc/bus/input/devices>"]
    out = []
    for block in text.strip().split("\n\n"):
        name, handlers, keys = "", "", 0
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line[len("N: Name="):].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line[len("H: Handlers="):]
            elif line.startswith("B: KEY="):
                for word in line[len("B: KEY="):].split():
                    keys = (keys << 64) | int(word, 16)
        if any(h.startswith("js") for h in handlers.split()) or \
                any(keys >> b & 1 for b in CONTROLLER_BUTTONS):
            out.append(name or "<unnamed>")
    return out


def host_hid_devices(root: Path = Path("/sys/class/hidraw")) -> list[str]:
    """Every HID device on the host, as "BUS:VENDOR:PRODUCT name", sorted.

    `host_game_controllers` asks evdev, which is not what Wine asks. Wine's
    DirectInput enumerates HID devices through winebus, and on this machine it
    hands the game's joystick scan a keyboard-and-mouse receiver that evdev
    reports as no controller at all (the `+dinput` trace of 2026-09-23,
    agents-journals/s4/2026-09-23-native-outcomes.md). This list is the fact,
    not a verdict on it; it stays in the private receipt, since it names the
    host's hardware.
    """
    out = set()
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return [f"<unreadable: {root}>"]
    for dev in entries:
        fields = {}
        try:
            for line in (dev / "device" / "uevent").read_text().splitlines():
                k, _, v = line.partition("=")
                fields[k] = v
        except OSError:
            continue
        out.add(f"{fields.get('HID_ID', '?')} {fields.get('HID_NAME', '?')}")
    return sorted(out)


def environment_record(env: dict[str, str]) -> dict:
    """What the game inherits of this runner's environment, without a value.

    The runner passes its caller's environment through, and Wine hands it to
    the game, whose C runtime copies it into its heap at start-up. Native heap
    addresses, the player object's among them, have differed between runs
    launched from the user's terminal and runs launched by an agent, and
    agreed within each (agents-journals/s4/2026-09-23-recording-session.md):
    so each receipt says which environment a run had. Names, the entry count,
    the size of the NAME=VALUE block and its sha256 identify it; values are
    left out, because an agent's environment carries tokens. The PATH entry
    count is kept because `source tools/env.sh` prepends to PATH every time it
    is sourced, so a terminal's environment grows by use."""
    block = "".join(f"{k}={v}\0" for k, v in sorted(env.items())).encode()
    return {"names": sorted(env), "count": len(env), "bytes": len(block),
            "sha256": hashlib.sha256(block).hexdigest(),
            "path_entries": len([p for p in env.get("PATH", "").split(":") if p])}


# The environment the game is launched with: these of the caller's, and a
# fixed PATH, nothing else. Heap addresses followed the environment a run was
# launched from (environment_record), so a recording the user launched and its
# replays an agent launched differed in them on every row; an agent's
# environment also carries tokens the game has no use for. What stays is what
# the display, the audio, Wine and Proton read: the session's own values, the
# same in a terminal and in an agent's shell of one login. PATH is fixed
# because `source tools/env.sh` prepends to it each time. Measured 2026-09-24
# with the session's own XAUTHORITY, before the link below replaced it
# (private/generated/s4/addendum-native/RESULTS.md, step 3): Proton starts with
# it, and two boots launched from callers whose environments differed by 3013
# bytes recorded one game environment and byte-identical tick captures. The
# receipt records it (session.environment, session.game_environment), and
# tools/oracle/record_stage.py refuses a replay launched with another.
#
# One kept value is not the session's own. XAUTHORITY names the X authority
# file the display server writes at login, and mutter's name for it ends in a
# random suffix that is new at every login, so a recording's game environment
# would outlive neither a logout nor a crash, and every native replay of it
# after one would be refused (the probe review of 2026-09-24, W2). The game is
# given a fixed name instead, XAUTHORITY_LINK under OUT, and link_xauthority
# points that link at the caller's file before each launch.
GAME_ENV_KEEP = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "DISPLAY", "WAYLAND_DISPLAY",
                 "XAUTHORITY", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
                 "DBUS_SESSION_BUS_ADDRESS", "WINEDEBUG")
GAME_PATH = "/usr/local/bin:/usr/bin:/bin"
XAUTHORITY_LINK = "xauthority"          # under OUT, read when called (tests move OUT)


def game_environment(env: dict[str, str]) -> dict[str, str]:
    """The environment the game gets: GAME_ENV_KEEP's names from `env`, where
    present, and GAME_PATH, with XAUTHORITY, when the caller has one, given as
    the fixed link link_xauthority maintains. Recorded whole in the receipt."""
    out = {k: env[k] for k in GAME_ENV_KEEP if k in env}
    if out.get("XAUTHORITY"):
        out["XAUTHORITY"] = str(OUT / XAUTHORITY_LINK)
    else:
        out.pop("XAUTHORITY", None)  # an empty one names no file; the game gets none
    out["PATH"] = GAME_PATH
    return out


def link_xauthority(env: dict[str, str]) -> dict | None:
    """Point the fixed XAUTHORITY the game gets (game_environment) at the
    caller's X authority file, before a launch, and say what was done for the
    receipt; None when the caller has none, so the game gets none.

    The link is made beside it and renamed over it, so a launch never finds a
    half-made one. Only a link is ever replaced: anything else at that path is
    refused, never removed. A caller whose XAUTHORITY is the link itself, or
    leads to it, gets it pointed at the file it resolves to. The file's name is
    not recorded: it is the session's, and new at every login."""
    source = env.get("XAUTHORITY")
    if not source:
        return None
    link = OUT / XAUTHORITY_LINK
    if link.exists() and not link.is_symlink():
        raise SystemExit(f"{link} is not the link this runner makes for XAUTHORITY; move it "
                         "away, then run this again")
    target = Path(os.path.realpath(source))
    if target == link:
        raise SystemExit(f"XAUTHORITY ({source}) leads back to {link}, which cannot point at "
                         "itself; run this from a shell whose XAUTHORITY is the session's file")
    if not target.is_file():
        raise SystemExit(f"XAUTHORITY ({source}) leads to no X authority file, so the game "
                         "could not open the display; run this from a shell of the current "
                         "desktop login, whose XAUTHORITY names that login's file")
    link.parent.mkdir(parents=True, exist_ok=True)
    new = link.with_name(f".{link.name}.{os.getpid()}")
    new.unlink(missing_ok=True)
    os.symlink(target, new)
    os.replace(new, link)
    return {"given_as": str(link), "a_link_to_the_caller_s_file": True,
            "the_file_exists": target.is_file()}


def host_display_modes(text: str | None = None) -> list[str]:
    """Each connected output's current mode, "OUTPUT WxH RATE", from
    `xrandr --current`. The refresh rate sets how fast the game runs with no
    limiter (several times 60 Hz on this machine's 240 Hz display) and what a
    vsync'd present waits for, and no receipt recorded it before 2026-09-23.
    A failure is recorded as one line saying so, never as no display."""
    if text is None:
        try:
            done = subprocess.run(["xrandr", "--current"], capture_output=True, text=True,
                                  timeout=10)
        except (OSError, subprocess.SubprocessError) as error:
            return [f"<xrandr failed: {error}>"]
        if done.returncode:
            return [f"<xrandr exited {done.returncode}: {done.stderr.strip()[:200]}>"]
        text = done.stdout
    out, output = [], None
    for line in text.splitlines():
        if not line[:1].isspace():
            words = line.split()
            output = words[0] if len(words) > 1 and words[1] == "connected" else None
            continue
        words = line.split()
        if output and words:
            out += [f"{output} {words[0]} {w.rstrip('*+')}" for w in words[1:] if "*" in w]
    return out or ["<xrandr listed no current mode>"]


def mirror_rebuild_reasons(asked: bool) -> list[str]:
    """Why the mirror must be rebuilt before this run, or [] when it is fit.
    The reasons are: asked for; missing; built before its save directory was
    its own (a mirror from before 2026-09-23); or anything on the way to the
    save directory that is not what the build made, which is a previous run's
    write there or an installation that has changed since
    (mirror_install.save_path_problems)."""
    if asked:
        return ["--rebuild-mirror"]
    if not MIRROR.exists():
        return ["there is no mirror"]
    if not mirror_install.save_dir_is_private(MIRROR):
        return [f"its {'/'.join(mirror_install.SAVE_DIR)} is shared with the installation"]
    return [f"on the way to its save directory: {p}"
            for p in mirror_install.save_path_problems(INSTALL, MIRROR)]


def profile_artifact(path: Path) -> tuple[dict, dict]:
    """session.device_profile, and the artifact row, for the profile a run left.

    The record is the one the replay tools check a capture against: the file,
    its sha256 and the counts tools/s4/device_profile.py --summary prints. A
    profile the runtime would refuse -- malformed, oversized, or recording a
    device it cannot reproduce, such as an attached pad -- is a problem, and
    problems fail the run: the profile probe is loaded only into captures meant
    to be replayed on the lifted side, which such a profile rules out. The file
    and the refusal are still kept, so the capture says what the host had.

    The profile is read through DP.load, as the replay tools and the runtime
    read it: at most one byte past the size limit, so the runner applies the
    same limit and never holds an oversized file whole. The sha256 and size
    recorded are the whole file's, hashed a block at a time.
    """
    sha, size = sha256(path), path.stat().st_size
    row = {"file": path.name, "present": True, "device_profile": True}
    try:
        profile, _ = DP.load(path, path.name)
    except DP.ProfileError as error:
        record = {"file": path.name, "sha256": sha, "counts": None,
                  "refused": {"line": error.line, "kind": error.kind, "message": error.message}}
        row["check"] = {"ok": False, "problems": [str(error)], "bytes": size, "sha256": sha}
        return record, row
    counts = DP.counts(profile)
    row["check"] = {"ok": True, "problems": [], "bytes": size, "sha256": sha, "counts": counts}
    return {"file": path.name, "sha256": sha, "counts": counts}, row


def capture_policies(specs: list[str], captures: list[str]) -> dict[str, tuple[int, list[str]]]:
    """`--capture-policy NAME=MIN_ROWS[:COLUMN,...]` for each named capture.

    `--min-rows` and `--require-varying` apply to every capture, which was right
    while every run wrote one tick stream. A run that also writes an import log
    (one row per import slot, no frame counter) would fail the stream's checks
    for a reason that is not a defect, so a capture can carry its own. A policy
    replaces both checks for that capture only, and must name a capture this run
    asks for: a policy for a file nobody collects would check nothing.
    """
    out: dict[str, tuple[int, list[str]]] = {}
    for spec in specs:
        name, sep, rest = spec.partition("=")
        rows, _, cols = rest.partition(":")
        if not sep or not name or not rows.isdigit() or int(rows) < 1:
            raise SystemExit(f"--capture-policy wants NAME=MIN_ROWS[:COLUMN,...], got {spec!r}")
        if name not in captures:
            raise SystemExit(f"--capture-policy names {name!r}, which is not a --capture")
        if name in out:
            raise SystemExit(f"--capture-policy names {name!r} twice")
        out[name] = (int(rows), [c for c in cols.split(",") if c])
    return out


# ---------------------------------------------------------------- the word log

TICK_PROBE = "native_tick_probe.c"     # harness/replay/'s probe that keeps the word log
WORD_LOG = WL.LOG                      # word_log_core.h's WL_FILE, under mods/
TICK_CAPTURE = WL.CAPTURE              # the tick capture every consumer reads
TICK_NAMED = "sa2-tick-named.csv"      # the tick probe's own row: the named fields alone
ZSTD_LEVEL = 3
# How the reader can fail after a run: its own refusal (a SystemExit), or a
# file it could not read. Each is recorded as the log's refusal.
READER_FAILURES = (WL.WordLogError, OSError, ValueError)
# A word log in mods/ written before this run's launch is an earlier run's, and
# is never collected as this run's: every boot's named row is byte-identical,
# so the capture check alone cannot tell (the runner review of 2026-09-25, W1).
# File times come from the kernel's coarse clock, which can trail time.time_ns()
# by a tick; the slack is far below any gap between two runs of the game.
LOG_CLOCK_SLACK_NS = 2_000_000_000
TICK_MOD = "tick_probe"                # the mod id every tool builds TICK_PROBE as


def failure(error: BaseException) -> str:
    """A reader failure as the receipt records it."""
    return str(error.code) if isinstance(error, SystemExit) else f"{type(error).__name__}: {error}"


def keeps_word_log(builds: list[tuple[str, Path]]) -> bool:
    """Whether a run builds the tick probe, which keeps the word log."""
    return any(Path(source).name == TICK_PROBE for _, source in builds)


def word_log_refusal(builds: list[tuple[str, Path]], captures: list[str],
                     binary: list[str]) -> str | None:
    """Why a run cannot start as asked, for the word log's sake; None when it
    can. A run that builds the tick probe collects its log and its row, or the
    capture would be refused after the whole run; and the log is asked for
    only where a tick probe writes it."""
    if keeps_word_log(builds):
        if WORD_LOG not in binary:
            return (f"{TICK_PROBE} keeps a word log and writes it as mods/{WORD_LOG}, without "
                    f"which its capture is refused: pass --binary-capture {WORD_LOG}, a runner "
                    "flag, so before '--'")
        if captures and TICK_CAPTURE not in captures:
            return (f"{TICK_PROBE}'s log is checked against the row it writes, {TICK_CAPTURE}: "
                    f"pass --capture {TICK_CAPTURE}")
    elif WORD_LOG in binary:
        return f"{WORD_LOG} is the word log {TICK_PROBE} writes, and no --build builds it"
    return None


def runner_flags_among(game_args: list[str], flags: set[str]) -> list[str]:
    """The game arguments that are this runner's own flags (`--capture`,
    `--binary-capture=x`): everything after `--` reaches the game, so a runner
    flag there is two arguments the game gets and a capture the runner never
    takes. The judge's run B did this, and the game's heap moved by 0x30 on
    every tick (private/generated/s4/wordobs/reports/judge-plan.md)."""
    return [a for a in game_args if a.startswith("-") and a.split("=", 1)[0] in flags]


def load_average() -> list[float] | None:
    """The host's 1, 5 and 15 minute load averages, or None where the OS has none."""
    try:
        return [round(x, 2) for x in os.getloadavg()]
    except OSError:
        return None


def idle_before_run(now: float | None = None) -> dict:
    """How long the oracle had been idle when this run's game was launched, in
    seconds, by two measures, and `seconds`, the smaller of those known (the
    most recent activity either shows). since_last_game_exit_s runs from the
    end of the last game this runner launched (mark_game_exit);
    since_newest_capture_s from the newest capture directory's last change,
    the measure the design panel's cold runs used (a directory changes when a
    run's files, or a tool's verdict beside them, are written). The judge's
    plan asks for mask sources taken after at least 90 s of it, because
    whether the game takes CRI's second path at tick 369 has looked like it
    follows how long the machine sat idle
    (private/generated/s4/wordobs/reports/design-always-on-snapshots.md)."""
    now = time.time() if now is None else now
    out: dict = {"seconds": None, "since_last_game_exit_s": None, "last_game_exit": None,
                 "since_newest_capture_s": None, "newest_capture": None}
    try:
        last = json.loads((OUT / "locks" / LAST_EXIT).read_text())
        out["since_last_game_exit_s"] = round(now - float(last["epoch"]), 1)
        out["last_game_exit"] = last.get("name")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    newest: tuple[float, str] | None = None
    try:
        for entry in CAPTURES.iterdir():
            if entry.is_dir():
                changed = entry.stat().st_mtime
                if newest is None or changed > newest[0]:
                    newest = (changed, entry.name)
    except OSError:
        pass
    if newest is not None:
        out["since_newest_capture_s"] = round(now - newest[0], 1)
        out["newest_capture"] = newest[1]
    known = [v for v in (out["since_last_game_exit_s"], out["since_newest_capture_s"])
             if v is not None]
    out["seconds"] = min(known) if known else None
    return out


def mark_game_exit(name: str) -> None:
    """Record that `name`'s game has exited, for the next run's idle_before_run."""
    path = OUT / "locks" / LAST_EXIT
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps({"name": name, "epoch": time.time(),
                               "utc": datetime.now(timezone.utc).isoformat()}) + "\n")
    os.replace(tmp, path)


def last_game_exit() -> dict | None:
    """What mark_game_exit last wrote ({name, epoch, utc}), or None when
    nothing readable is there."""
    try:
        last = json.loads((OUT / "locks" / LAST_EXIT).read_text())
        float(last["epoch"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return last if isinstance(last, dict) else None


# -------------------------------------------- the Proton build and the launch gate

def files_tree(top: Path) -> dict:
    """Every regular file and link under `top` as one digest: a file by its
    name, size and modification time, a link by its name and target, sorted
    by name; directories only by what they hold. Steam replaces a build's
    files in place, so a new build moves this even where a `version` file
    lags; the time stands in for the bytes, which would cost a full read of
    the tree at every launch. A run of the game writes nothing under it: no
    entry there changed after Steam's update at 04:27 on 2026-09-26, across
    the five captures taken since (locale-trace-v1a, locale-boot-v2a, N0's
    Metal Harbor canary and its two traces)."""
    lines: list[tuple[str, str]] = []
    for here, dirs, names in os.walk(top):
        dirs.sort()
        for name in names + dirs:
            path = os.path.join(here, name)
            rel = os.path.relpath(path, top)
            try:
                st = os.lstat(path)
            except OSError as error:
                lines.append((rel, f"? {rel} {error.strerror}"))
                continue
            if os.path.islink(path):
                lines.append((rel, f"l {rel} {os.readlink(path)}"))
            elif os.path.isfile(path):
                lines.append((rel, f"f {rel} {st.st_size} {st.st_mtime_ns}"))
    lines.sort()
    text = "\n".join(line for _, line in lines).encode(errors="surrogateescape")
    return {"entries": len(lines), "sha256": hashlib.sha256(text).hexdigest()}


# What else a launch reads that Steam or a person can change beside the
# distribution: the wrapper's user_settings.py, whose settings the launcher adds
# to the game's environment past GAME_ENV_KEEP (its init_session), and the Steam
# client's files the game reaches through Proton: the client libraries
# (lsteamclient loads ~/.steam/sdk32/steamclient.so, linux32 on this host; the
# running client is ubuntu12_32's) and the DLLs the launcher copies into the
# prefix whenever their times move (its prefix_info). Recorded by size and
# time, like files_tree (the RT instrument review, finding 4).
STEAM_CLIENT_FILES = ("linux32/steamclient.so", "ubuntu12_32/steamclient.so",
                      "legacycompat/steamclient.dll", "legacycompat/steamclient64.dll",
                      "legacycompat/Steam.dll")


def proton_build(launcher: Path | None = None, steam_client: Path | None = None) -> dict:
    """The Proton build a run launches, as session.proton (the module
    docstring, 6): the launcher script (PROTON) and its sha256; the wrapper's
    own `version` file, recorded as it reads and never taken as the build,
    since the wrapper's copy is older than the files it links to; where its
    `files` link leads; the build the distribution's `version` file (beside
    those files) names, its last word; files_tree of them; the sha256 of the
    wrapper's user_settings.py (null when there is none); and the size and
    time of each of the Steam client's STEAM_CLIENT_FILES under `steam_client`
    (STEAM_CLIENT; null for one that is not there). Every value is one a
    second run of the same build records alike, since oracle_mode compares
    the whole record between captures. What cannot be read is listed under
    `problems`, and the record is still written."""
    launcher = Path(PROTON if launcher is None else launcher)
    files = launcher.parent / "files"
    problems: list[str] = []

    def text(path: Path) -> str | None:
        try:
            return path.read_text(errors="replace").strip()
        except OSError as error:
            problems.append(f"{path}: {error.strerror or error}")
            return None

    out: dict = {"launcher": str(launcher), "launcher_sha256": None,
                 "wrapper_version_file": text(launcher.parent / "version"),
                 "files_link": os.readlink(files) if files.is_symlink() else None,
                 "files": os.path.realpath(files), "distribution_version_file": None,
                 "build": None, "files_tree": None}
    try:
        out["launcher_sha256"] = sha256(launcher)
    except OSError as error:
        problems.append(f"{launcher}: {error.strerror or error}")
    real = Path(out["files"])
    if real.is_dir():
        out["distribution_version_file"] = text(real.parent / "version")
        words = (out["distribution_version_file"] or "").split()
        out["build"] = words[-1] if words else None
        out["files_tree"] = files_tree(real)
    else:
        problems.append(f"{files} leads to no directory")
    settings = launcher.parent / "user_settings.py"
    try:
        out["user_settings_sha256"] = sha256(settings) if settings.is_file() else None
    except OSError as error:
        out["user_settings_sha256"] = None
        problems.append(f"{settings}: {error.strerror or error}")
    client = Path(STEAM_CLIENT if steam_client is None else steam_client)
    out["steam_client"] = {}
    for rel in STEAM_CLIENT_FILES:
        try:
            st = (client / rel).stat()
        except FileNotFoundError:
            out["steam_client"][rel] = None
        except OSError as error:
            out["steam_client"][rel] = None
            problems.append(f"{client / rel}: {error.strerror or error}")
        else:
            out["steam_client"][rel] = f"{st.st_size} {st.st_mtime_ns}"
    if problems:
        out["problems"] = problems
    return out


def steam_pids() -> list[int]:
    """The processes running the Steam client (STEAM_BINARY), read from each
    /proc/<pid>/exe; a client that updated itself while running reads as the
    same path with " (deleted)"."""
    want = os.path.realpath(STEAM_BINARY)
    out = []
    for proc in PROC.glob("[0-9]*"):
        try:
            exe = os.readlink(proc / "exe")
        except OSError:
            continue
        if exe.removesuffix(" (deleted)") == want:
            out.append(int(proc.name))
    return sorted(out)


GAME_EXE = "sonic2app-unpacked.exe"


def game_pids() -> list[int]:
    """The native game's processes, launching or running: Wine's process for
    GAME_EXE, which shows the game's Windows path as its first argument, and
    the Proton launcher's (`proton runinprefix ./GAME_EXE`), this process
    aside. A lifted run, which names the executable's file as an argument of
    its own binary, is neither. A runner stopped by SIGINT kills its `timeout`
    and leaves these running without a bound, the game still writing the
    mirror's mods/, where the next run's captures are collected (the RT batch
    review, finding 3)."""
    out = []
    for proc in PROC.glob("[0-9]*"):
        try:
            raw = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        args = [a.decode(errors="replace") for a in raw.split(b"\0") if a]
        names = [re.split(r"[\\/]", a)[-1].lower() for a in args]
        if int(proc.name) != os.getpid() and names and (
                names[0] == GAME_EXE or ("runinprefix" in args and GAME_EXE in names)):
            out.append(int(proc.name))
    return sorted(out)


def suspend_clock() -> float:
    """CLOCK_BOOTTIME less CLOCK_MONOTONIC, in seconds: it grows only while the
    host is suspended."""
    return round(time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic(), 3)


def _command_out(cmd: list[str], timeout: float = 5.0) -> str:
    """`cmd`'s standard output, killed after `timeout` seconds;
    subprocess.SubprocessError or OSError when it fails. Through Popen, not
    subprocess.run, which the runner's tests replace with the game."""
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True) as p:
        try:
            out = p.communicate(timeout=timeout)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            raise
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, cmd)
    return out


def host_state() -> dict:
    """The host now, for session.host_state: the suspend clock (suspend_clock),
    and logind's word on the graphical session this user is shown on: its id,
    whether it is locked (LockedHint) and whether it is idle (IdleHint; the
    screen may be blank). Read-only: this changes no lock or idle setting.
    Null where logind cannot say; the batch records the lock state of every
    run (AGENTS.md rule 11; tools/oracle/retake_batch.py rule_11)."""
    out: dict = {"boottime_minus_monotonic_s": suspend_clock(), "session": None,
                 "locked": None, "idle": None}
    try:
        sid = _command_out(["loginctl", "show-user", str(os.getuid()), "-p", "Display",
                            "--value"]).strip()
        if sid:
            text = _command_out(["loginctl", "show-session", sid, "-p", "LockedHint",
                                 "-p", "IdleHint"])
            kv = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            out.update(session=sid,
                       locked={"yes": True, "no": False}.get(kv.get("LockedHint", "")),
                       idle={"yes": True, "no": False}.get(kv.get("IdleHint", "")))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return out


def sleep_inhibitors() -> list[dict] | None:
    """logind's inhibitors now ({what, who, why, mode, uid, pid} each), read
    with busctl; None when they cannot be read."""
    try:
        doc = json.loads(_command_out(
            ["busctl", "--system", "--json=short", "call", "org.freedesktop.login1",
             "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
             "ListInhibitors"]))
        return [dict(zip(("what", "who", "why", "mode", "uid", "pid"), row))
                for row in doc["data"][0]]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError,
            TypeError):
        return None


def holds_sleep(inhibitors: list[dict] | None, pid: int, who: str) -> bool:
    """Whether process `pid` holds a blocking sleep inhibitor named `who`."""
    return any(i.get("pid") == pid and i.get("who") == who and i.get("mode") == "block"
               and "sleep" in str(i.get("what", "")).split(":") for i in inhibitors or [])


# What a launch gate holds, each with its type; a gate missing any is refused
# (read_gate). The file is written by tools/oracle/retake_batch.py before each
# step it runs.
GATE_KEYS = {"schema": str, "batch": str, "step": str, "root": str,
             "batch_started_epoch": float, "written_epoch": float,
             "max_load": float, "min_idle_first_s": float,
             "wait_s": float, "poll_s": float, "steam": bool, "proton": dict,
             "freeze": dict, "saves_pin": str, "profile_sha256": (str, type(None)),
             "steam_pid": int, "suspend_clock_s": float, "sleep_inhibitor": dict}
# How far the suspend clock may move, in seconds, before the host counts as
# suspended: it moves by milliseconds between two readings on a host that
# never slept.
SUSPEND_SLACK_S = 2.0


def read_gate(path: Path) -> tuple[dict, str]:
    """The launch gate at `path` and its bytes' sha256; SystemExit, and no
    launch, when it cannot be read or holds less than GATE_KEYS: a gate named
    and not whole must not read as no gate."""
    try:
        data = Path(path).read_bytes()
        spec = json.loads(data)
    except (OSError, ValueError) as error:
        raise SystemExit(f"{GATE_REFUSED}: {LAUNCH_GATE} names {path}, which cannot be read "
                         f"({error})") from None
    bad = []
    for key, kind in GATE_KEYS.items():
        value = spec.get(key, KeyError) if isinstance(spec, dict) else KeyError
        if kind in (float, int):
            # A NaN or an infinity compares false both ways: a limit that is one
            # lifts its condition (the RT instrument review, finding 2).
            ok = (isinstance(value, (int, float) if kind is float else int)
                  and not isinstance(value, bool) and math.isfinite(value))
        else:
            ok = isinstance(value, kind)
        if not ok:
            bad.append(key)
    if not bad and (spec["schema"] != GATE_SCHEMA or spec["poll_s"] <= 0
                    or spec["max_load"] <= 0 or spec["wait_s"] < 0
                    or spec["min_idle_first_s"] < 0 or not spec["freeze"]
                    or not isinstance(spec["sleep_inhibitor"].get("pid"), int)
                    or not spec["sleep_inhibitor"].get("who")):
        bad.append("schema, poll_s, max_load, wait_s, min_idle_first_s, freeze or "
                   "sleep_inhibitor")
    if bad:
        raise SystemExit(f"{GATE_REFUSED}: the launch gate {path} is not whole ({', '.join(bad)})")
    return spec, hashlib.sha256(data).hexdigest()


def _sha256_or_none(path: Path) -> str | None:
    try:
        return sha256(path)
    except OSError:
        return None


def gate_problems(spec: dict) -> tuple[list[str], list[str], dict]:
    """Why a run cannot launch now under the gate `spec`: the refusals, what
    is worth waiting for, and what was measured.

    Refusals: another tree's gate; Steam not running, when the gate asks for
    it, or not the client process the batch began with (`steam_pid`: a client
    that restarted, as one that updated itself does, is another host service
    under every later run); a game still running (game_pids); the batch's
    sleep inhibitor gone (`sleep_inhibitor`, sleep_inhibitors); the host
    suspended since the batch began (the suspend clock more than
    SUSPEND_SLACK_S past `suspend_clock_s`); the Proton build (proton_build)
    not the batch's; a frozen file whose
    sha256 moved or that is gone; the save pin moved, or the installation's
    saves not the pin; and a run that ended since the batch began
    (last_game_exit) but left no receipt, was not launched through this
    batch's gate, or, once the batch has a device profile to hold runs to,
    recorded another (last_run_problems). Waited for: the
    one-minute load at or above `max_load`, and, for the first launch since
    the gate was written (no game has exited since `written_epoch`), fewer
    than `min_idle_first_s` idle seconds by idle_before_run."""
    hard: list[str] = []
    wait: list[str] = []
    seen: dict = {}
    if spec["root"] != str(ROOT):
        hard.append(f"the gate was written for the tree at {spec['root']}, and this runner is "
                    f"{ROOT}'s")
    seen["steam_pids"] = steam_pids()
    if spec["steam"] and not seen["steam_pids"]:
        hard.append("Steam is not running: it needs the user's hands (AGENTS.md rule 11), to "
                    "start it and log in, before the batch runs again")
    elif spec["steam"] and spec["steam_pid"] not in seen["steam_pids"]:
        hard.append(f"Steam restarted during the batch (pid {spec['steam_pid']} is gone, now "
                    f"{seen['steam_pids']}): a client that restarted or updated itself is "
                    "another host service under every later run")
    seen["game_pids"] = game_pids()
    if seen["game_pids"]:
        hard.append(f"a game is still running (pid {seen['game_pids']}): an earlier run's game "
                    "outlived its runner and writes the mirror's mods/ this run collects")
    inhibitor = spec["sleep_inhibitor"]
    if not holds_sleep(sleep_inhibitors(), inhibitor["pid"], inhibitor["who"]):
        hard.append(f"the batch's sleep inhibitor ({inhibitor['who']}, pid {inhibitor['pid']}) "
                    "is no longer held, so the host can suspend under a run")
    seen["suspend_clock_s"] = suspend_clock()
    slept = seen["suspend_clock_s"] - spec["suspend_clock_s"]
    if slept > SUSPEND_SLACK_S:
        hard.append(f"the host was suspended for {slept:.0f} s since the batch began: the "
                    "idle time it waited on and the host it launches on are not a quiet "
                    "host's; that needs the user (AGENTS.md rule 11)")
    build = proton_build()
    seen["proton_build"] = build.get("build")
    moved = OM.first_difference(spec["proton"], build)
    if moved:
        hard.append(f"the Proton build is not the one the batch began on: {moved}")
    frozen = [rel for rel, want in sorted(spec["freeze"].items())
              if _sha256_or_none(ROOT / rel) != want]
    if frozen:
        hard.append(f"{len(frozen)} frozen file(s) changed since the batch began: "
                    + ", ".join(frozen[:5]))
    try:
        pin = SS.load()
        wrong = SS.problems(INSTALL, pin)
    except SystemExit as error:
        hard.append(str(error.code))
    else:
        if pin.digest != spec["saves_pin"]:
            hard.append(f"the save pin is {pin.digest[:12]}, and the batch began on "
                        f"{spec['saves_pin'][:12]}")
        if wrong:
            hard.append(SS.refusal(INSTALL, pin, wrong))
    last = last_game_exit()
    seen["last_run"] = None if last is None else last.get("name")
    seen["first_of_step"] = last is None or float(last["epoch"]) < spec["written_epoch"]
    hard += last_run_problems(last, spec)
    if seen["first_of_step"] and spec["min_idle_first_s"] > 0:
        seen["idle_s"] = idle_before_run()["seconds"]
        if seen["idle_s"] is None or seen["idle_s"] < spec["min_idle_first_s"]:
            wait.append(f"the oracle has been idle {seen['idle_s']} s, not "
                        f"{spec['min_idle_first_s']:g} s, and the first run of a series is cold")
    seen["load"] = load_average()
    if seen["load"] is None:
        hard.append("the host reports no load average")
    elif seen["load"][0] >= spec["max_load"]:
        wait.append(f"the one-minute load is {seen['load'][0]}, not below "
                    f"{spec['max_load']:g}")
    return hard, wait, seen


def last_run_problems(last: dict | None, spec: dict) -> list[str]:
    """Why the run that ended last breaks the batch, [] when it does not or
    ended before the batch began: it left a receipt, was launched through this
    batch's gate, so no other run took the oracle during the batch, and,
    once the batch holds runs to a device profile (`profile_sha256`), it
    recorded that profile, the one the batch's first two boots agreed on."""
    if last is None or float(last["epoch"]) < spec["batch_started_epoch"]:
        return []
    name = str(last.get("name"))
    try:
        rec = json.loads((CAPTURES / name / "receipt.json").read_text())
        session = rec["session"]
        gate = session.get("launch_gate") or {}
        got = (session.get("device_profile") or {}).get("sha256")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return [f"the last run, {name}, left no readable receipt, so its device profile is "
                "unknown"]
    out = []
    if gate.get("batch") != spec["batch"]:
        out.append(f"the last run, {name}, was not launched through batch {spec['batch']}'s "
                   "gate: another run took the oracle during the batch")
    if spec["profile_sha256"] is not None and got != spec["profile_sha256"]:
        out.append(f"the last run, {name}, recorded device profile {str(got)[:12]}, and the "
                   f"batch's first two boots {spec['profile_sha256'][:12]}")
    return out


def wait_for_gate(spec: dict, *, clock=time.monotonic, sleep=time.sleep) -> dict:
    """Hold until gate_problems finds nothing, polling every `poll_s`: what the
    gate measured when it passed, or SystemExit starting GATE_REFUSED, without
    a launch, at the first refusal or after `wait_s` seconds of waiting."""
    start = clock()
    polls = 0
    while True:
        polls += 1
        hard, wait, seen = gate_problems(spec)
        waited = round(clock() - start, 1)
        if hard:
            raise SystemExit(f"{GATE_REFUSED} {spec['step']}: " + "; ".join(hard))
        if not wait:
            return {"batch": spec["batch"], "step": spec["step"], "polls": polls,
                    "waited_s": waited, "max_load": spec["max_load"],
                    "min_idle_first_s": spec["min_idle_first_s"],
                    "profile_sha256_required": spec["profile_sha256"],
                    "frozen_files": len(spec["freeze"]), **seen}
        if waited >= spec["wait_s"]:
            raise SystemExit(f"{GATE_REFUSED} {spec['step']} after waiting {waited:g} s: "
                             + "; ".join(wait))
        if polls == 1:
            print(f"  launch gate {spec['step']}: waiting: " + "; ".join(wait), file=sys.stderr)
        sleep(spec["poll_s"])


def launch_gate(env: dict[str, str], *, clock=time.monotonic, sleep=time.sleep) -> dict | None:
    """None when `env` names no launch gate (LAUNCH_GATE); otherwise the
    gate's file read (read_gate) and held (wait_for_gate), and what it
    measured with the file's path and sha256, for session.launch_gate. It
    raises SystemExit, and the run launches nothing, on any refusal."""
    path = env.get(LAUNCH_GATE)
    if not path:
        return None
    spec, digest = read_gate(Path(path))
    return {**wait_for_gate(spec, clock=clock, sleep=sleep), "gate": str(path),
            "gate_sha256": digest}


def zstd_compress(log: Path) -> dict:
    """`zstd -3` the log beside itself, kept only once the compressed file
    decompresses to the log's bytes: {file, level, bytes, sha256, seconds}, or
    {problem} and no compressed file. The caller removes the log."""
    zst = log.with_name(log.name + ".zst")
    zst.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        done = subprocess.run(["zstd", f"-{ZSTD_LEVEL}", "-q", "-o", str(zst), str(log)],
                              capture_output=True, text=True)
    except OSError as error:
        return {"problem": f"zstd could not run: {error}"}
    seconds = time.monotonic() - started
    if done.returncode or not zst.is_file():
        zst.unlink(missing_ok=True)
        return {"problem": f"zstd exited {done.returncode}: {done.stderr.strip()[-300:]}"}
    digest = hashlib.sha256()
    with subprocess.Popen(["zstd", "-dc", str(zst)], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as unzipped:
        for block in iter(lambda: unzipped.stdout.read(1 << 20), b""):
            digest.update(block)
    if unzipped.returncode or digest.hexdigest() != sha256(log):
        zst.unlink(missing_ok=True)
        return {"problem": "the compressed log does not decompress to the log's bytes"}
    return {"file": zst.name, "level": ZSTD_LEVEL, "bytes": zst.stat().st_size,
            "sha256": sha256(zst), "seconds": round(seconds, 3)}


def digest_inputs() -> dict:
    """What the digest columns built from a log are a function of: the reader's
    inputs (tools/s4/wordlog.py `reader`), the tree's mask among them, by
    repository path and sha256."""
    files = (WL.SOURCE, WL.CORE, WL.ROW, WL.FIELDS, WL.MASK)
    return {str(p.relative_to(ROOT)): sha256(p) for p in files}


def kept_word_log(capture_dir: Path) -> Path | None:
    """The word log `capture_dir`'s run kept (the .zst, or the raw log where
    zstd failed), once shown to be the file its run collected (the sha256 its
    receipt's artifact recorded) and a log the run accepted (that artifact's
    check.ok): a log the runner refused never gives digest columns (the runner
    review of 2026-09-25, W2). None for a capture that kept no log (one from
    before the word log); WL.WordLogError naming why otherwise."""
    for name in (WORD_LOG + ".zst", WORD_LOG):
        log = capture_dir / name
        if not log.is_file():
            continue
        rec = json.loads((capture_dir / "receipt.json").read_text())
        row = next((a for a in rec.get("artifacts") or []
                    if isinstance(a, dict) and a.get("file") == name), None)
        check = (row or {}).get("check") or {}
        want, got = check.get("sha256"), sha256(log)
        if not want or got != want:
            raise WL.WordLogError(f"{log} hashes to {got[:12]}, and its receipt collected "
                                  f"{str(want)[:12]}")
        if check.get("ok") is not True:
            raise WL.WordLogError(f"{log}: its run refused it: "
                                  + ("; ".join(check.get("problems") or []) or "no reason kept"))
        return log
    return None


def capture_from_log(capture_dir: Path, mask: Path | None = None) -> str | None:
    """The tick capture `capture_dir`'s kept word log gives with `mask` (the
    tree's by default): the named fields as the log holds them and the digest
    columns of that mask, the text the runner writes as TICK_CAPTURE when the
    mask is the tree's. None for a capture that kept no log; WL.WordLogError
    when the log is not its run's, or its run or the reader refuses it."""
    log = kept_word_log(capture_dir)
    return None if log is None else WL.capture_csv(log, mask, native=True)


def finish_word_log(capture_dir: Path, artifacts: list[dict],
                    policy: tuple[int, list[str]]) -> dict:
    """The tick probe's word log once the run's files are in `capture_dir`
    (the module docstring, 5): checked against the probe's own row, the tick
    capture built from it with the tree's mask, and then kept compressed.

    `artifacts` is edited in place. The probe's row becomes TICK_NAMED's
    artifact, as collected; the log's artifact names the file kept and holds
    the check's problems; and TICK_CAPTURE's is the built file checked with
    `policy` (min_rows, require_varying), or absent with the reason when the
    log was refused, so the capture is refused and nothing stands in for the
    digest columns. Returns the receipt's `word_log`."""
    by_name = {a.get("file"): a for a in artifacts}
    log_row = by_name[WORD_LOG]
    named_row = by_name.get(TICK_CAPTURE)
    log, named = capture_dir / WORD_LOG, capture_dir / TICK_NAMED
    record: dict = {"collected_as": WORD_LOG, "named_row": TICK_NAMED,
                    "digest_inputs": digest_inputs()}
    if named_row is not None:
        if named_row.get("present"):
            (capture_dir / TICK_CAPTURE).rename(named)
            record["named_row_sha256"] = sha256(named)
            if isinstance(named_row.get("check"), dict) and "path" in named_row["check"]:
                named_row["check"]["path"] = str(named)     # where the checked bytes now are
        named_row.update(file=TICK_NAMED, collected_as=TICK_CAPTURE)
    problems: list[str] = []
    if not log.is_file() and log_row.get("predates_launch"):
        problems += (log_row.get("check") or {}).get("problems") or []
    elif not log.is_file():
        problems.append(f"the tick probe left no {WORD_LOG}, so nothing shows that its row is "
                        "the game's memory, and no digest column can be computed")
    elif not named.is_file():
        problems.append(f"the tick probe left no {TICK_CAPTURE} to check its log against")
    else:
        try:
            checked = WL.check_capture(named, log)
        except READER_FAILURES as error:
            # The check returns its refusals; one that raises instead (the U1
            # reader review's W4) is recorded as a refusal, not a crash after
            # the run that would leave the capture without a receipt.
            checked = {"ok": False,
                       "problems": [f"the capture check could not run: {failure(error)}"]}
        record["capture_check"] = checked
        problems += checked["problems"]
    if log.is_file():
        try:
            record.update(WL.receipt_fields(log))
        except READER_FAILURES as error:
            record["receipt_fields_refused"] = failure(error)
    text = None
    if not problems:
        try:
            text = WL.capture_csv(log, native=True)
        except READER_FAILURES as error:
            problems.append(f"the reader could not build {TICK_CAPTURE} from the log: "
                            f"{failure(error)}")
    built_from = {"log": WORD_LOG, "log_sha256": record.get("sha256"), "named_row": TICK_NAMED,
                  "named_row_sha256": record.get("named_row_sha256"),
                  "digest_inputs": record["digest_inputs"]}
    if text is not None:
        out = capture_dir / TICK_CAPTURE
        out.write_text(text)
        min_rows, varying = policy
        try:
            checked_out = capture_check.check(out, varying, min_rows, list(SHORT_ROW_WIDTHS))
        except SystemExit as error:
            checked_out = {"ok": False, "problems": [str(error)]}
        artifacts.append({"file": TICK_CAPTURE, "present": True, "trimmed": None,
                          "check": checked_out, "built_from_log": built_from,
                          "policy": {"min_rows": min_rows, "require_varying": varying}})
        record["capture_sha256"] = checked_out.get("sha256")
        # The built capture's own policy check (the runner review, N4): `ok`
        # is the log's verdict, and this the capture's.
        record["capture_ok"] = checked_out.get("ok") is True
    else:
        record["capture_ok"] = False
        artifacts.append({"file": TICK_CAPTURE, "present": False, "built_from_log": built_from,
                          "refused": problems[0]})
    kept = zstd_compress(log) if log.is_file() else {"problem": f"no {WORD_LOG} to keep"}
    record["zstd"] = kept
    check = {"ok": not problems, "problems": problems}
    if log.is_file() and "problem" not in kept:
        check.update(bytes=kept["bytes"], sha256=kept["sha256"], log_bytes=log.stat().st_size,
                     log_sha256=sha256(log))
        log.unlink()                    # the compressed file decompresses to it
        log_row.update(file=kept["file"], collected_as=WORD_LOG)
    elif log.is_file():
        check.update(bytes=log.stat().st_size, sha256=sha256(log))
    else:
        check["problems"] = list(dict.fromkeys(
            (log_row.get("check") or {}).get("problems", []) + problems))
    log_row.update(word_log=True, check=check)
    record.update(stored_as=log_row["file"], ok=not problems, problems=problems)
    return record


def arg_parser() -> argparse.ArgumentParser:
    """The runner's arguments; the tools that build a runner call hold their
    commands to it (no runner flag after `--`)."""
    # No abbreviations (the runner review of 2026-09-25, N2): `--binary-cap x`
    # would be the log flag before '--' and pass runner_flags_among after it,
    # which knows the full spellings only.
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="capture directory name")
    parser.add_argument("--build", action="append", default=[], metavar="MODID=SOURCE.c",
                        help="compile SOURCE.c into the mirror as mod MODID; "
                             "repeat for several hooks, loaded in the order given")
    parser.add_argument("--define", action="append", default=[], metavar="MODID:NAME=VALUE",
                        help="a compiler definition for one --build")
    parser.add_argument("--profile-mod", action="append", default=[],
                        help="an existing mirror mod to enable, in order")
    parser.add_argument("--capture", action="append", default=[],
                        help="basename written under mods/; default: everything new")
    parser.add_argument("--timeout", type=int, default=240,
                        help="bounded run time in seconds (10-480)")
    parser.add_argument("--lock-timeout", type=float, default=60.0)
    parser.add_argument("--resolution", default="640x480")
    parser.add_argument("--skip-intro", action="store_true", default=True)
    parser.add_argument("--no-skip-intro", dest="skip_intro", action="store_false")
    parser.add_argument("--pause-on-inactive", action="store_true", default=False,
                        help="let the loader pause the game on focus loss (off: a capture on "
                             "a live desktop must not depend on where the user clicks)")
    parser.add_argument("--text-language", type=int, default=1)
    parser.add_argument("--voice-language", type=int, default=1,
                        help="written into the game at init by the loader; 1 is what the "
                             "lifted build reads from this installation's configuration")
    parser.add_argument("--trim-torn-row", action="store_true",
                        help="drop a trailing row the kill cut in half, and record it")
    parser.add_argument("--rebuild-mirror", action="store_true")
    parser.add_argument("--require-varying", action="append", default=[])
    parser.add_argument("--min-rows", type=int, default=1)
    parser.add_argument("--binary-capture", action="append", default=[], metavar="NAME",
                        help="a file written under mods/ that is not a CSV (a memory "
                             "snapshot): copied and hashed, never parsed, required non-empty; "
                             f"except {WORD_LOG}, the tick probe's word log, which is required "
                             "of every run that builds that probe, checked, and kept compressed")
    parser.add_argument("--capture-policy", action="append", default=[],
                        metavar="NAME=MIN_ROWS[:COLUMN,...]",
                        help="the checks for one --capture in place of --min-rows and "
                             "--require-varying, for a run that writes captures of different "
                             "shapes (a tick stream and an import log)")
    parser.add_argument("--device-profile", action="store_true",
                        help=f"build {DP.PROBE} as mod {PROFILE_MOD}, load it after every other "
                             f"probe, and require the {DP.FILE} it writes: the host's device "
                             "answers, which the receipt records as session.device_profile and "
                             "a lifted replay of this capture reads back")
    parser.add_argument("game_args", nargs=argparse.REMAINDER)
    return parser


def game_arguments(args: argparse.Namespace) -> list[str]:
    """What the game is given: everything after `--`."""
    game_args = list(args.game_args)
    return game_args[1:] if game_args and game_args[0] == "--" else game_args


def main(argv: list[str] | None = None) -> None:
    parser = arg_parser()
    args = parser.parse_args(argv)

    if not 10 <= args.timeout <= 480:
        raise SystemExit("--timeout must be between 10 and 480 seconds")
    builds: list[tuple[str, Path]] = []
    for spec in args.build:
        mod_id, sep, source = spec.partition("=")
        if not sep or not mod_id or not source:
            raise SystemExit(f"--build wants MODID=SOURCE.c, got {spec!r}")
        builds.append((mod_id, Path(source).resolve()))
    # A flag rather than a probe every run loads: a receipt's command must
    # still reproduce the run it describes, and every capture taken before the
    # profile probe existed ran without it. Its load order is fixed -- last, so
    # every other probe's Init and OnFrame keep the places they had -- and so
    # is its mod id. The replay tools require it last (tools/s4/device_profile.py
    # capture_profile), so a --build of it anywhere, flag or not, is refused:
    # that would load it where the non-perturbation control never measured it.
    if any(m == PROFILE_MOD or s == PROFILE_PROBE for m, s in builds):
        raise SystemExit(f"{DP.PROBE} is built only by --device-profile, which loads it last "
                         f"as {PROFILE_MOD}; do not --build it")
    if args.device_profile:
        builds.append((PROFILE_MOD, PROFILE_PROBE))
    defines: dict[str, list[str]] = {}
    for spec in args.define:
        mod_id, sep, define = spec.partition(":")
        if not sep:
            raise SystemExit(f"--define wants MODID:NAME=VALUE, got {spec!r}")
        defines.setdefault(mod_id, []).append(define)
    unknown = set(defines) - {mod_id for mod_id, _ in builds}
    if unknown:
        raise SystemExit(f"--define names a mod that is not built: {sorted(unknown)}")
    if DP.FILE in args.capture + args.binary_capture:
        raise SystemExit(f"{DP.FILE} is collected and checked as the device profile "
                         "(--device-profile); it is not a --capture")
    refused = word_log_refusal(builds, args.capture, args.binary_capture)
    if refused:
        raise SystemExit(refused)
    if TICK_MOD in args.profile_mod:
        # The mirror's last build of it, loaded as it is, keeps a log no check
        # asks for (the runner review of 2026-09-25, N7).
        raise SystemExit(f"--profile-mod {TICK_MOD} loads the mirror's last {TICK_PROBE} build "
                         f"without its word log's checks: build it with --build "
                         f"{TICK_MOD}=harness/replay/{TICK_PROBE} and --binary-capture {WORD_LOG}")
    if TICK_NAMED in args.capture + args.binary_capture:
        raise SystemExit(f"{TICK_NAMED} is the name the tick probe's own row is kept under; it "
                         "is not a --capture")
    args.builds = builds
    args.defines = defines
    args.policies = capture_policies(args.capture_policy, args.capture)
    capture_dir = CAPTURES / args.name
    if capture_dir.exists():
        raise SystemExit(f"refusing to overwrite an existing capture: {capture_dir}")
    width, _, height = args.resolution.partition("x")
    resolution = (int(width), int(height))
    game_args = game_arguments(args)
    flags = runner_flags_among(game_args, {s for a in parser._actions for s in a.option_strings})
    if flags:
        raise SystemExit(f"{flags} follow '--', where every argument goes to the game: they are "
                         "this runner's flags, and belong before '--'")

    if keeps_word_log(builds):
        # Everything the reader needs after the run, before it: a reader that
        # cannot be built then would refuse the capture after the whole run
        # (the runner review of 2026-09-25, N1).
        try:
            WL.reader()
            WL.named_fields()
            WL.buffer_bytes_required()
            WL.mask_blocks()
        except READER_FAILURES as error:
            raise SystemExit("the word log's reader cannot be built or read, so this run's "
                             f"capture would be refused after the game: {failure(error)}")

    with lease(args.lock_timeout):
        run(args, capture_dir, resolution, game_args)


def pinned_saves() -> SS.Pin:
    """The pinned start state, or SystemExit, before anything is built or
    launched, when the staged installation's saves are not it: the message
    names each difference and the command that restores them (the user's
    ruling of 2026-09-24 makes the saves a recorded input). The mirror copies
    the installation's saves in before the run, so this is the check that
    every run starts from the pin."""
    pin = SS.load()
    wrong = SS.problems(INSTALL, pin)
    if wrong:
        raise SystemExit(SS.refusal(INSTALL, pin, wrong))
    return pin


def run(args, capture_dir: Path, resolution, game_args: list[str]) -> None:
    executable_sha = verify_executable()
    pin = pinned_saves()
    # Before anything is built, so a path it refuses costs nothing; the lease
    # held around run() keeps any other launch from moving it meanwhile.
    xauthority = link_xauthority(dict(os.environ))

    rebuilt_because = mirror_rebuild_reasons(args.rebuild_mirror)
    if rebuilt_because:
        # stderr, as every note here: stdout is the verdict and its detail,
        # which record_stage.py reads.
        print("  rebuilding the mirror: " + "; ".join(rebuilt_because[:5]), file=sys.stderr)
        mirror_install.build(INSTALL, MIRROR, mirror_install.DEFAULT_SYMLINK_THRESHOLD,
                             force=True, require_save_dir=True)
    # Every run starts from the installation's saves, and a save the game
    # writes stays in the mirror (mirror_install.SAVE_DIR).
    saves_undone = mirror_install.reset_save_dir(INSTALL, MIRROR)
    if saves_undone:
        print(f"  what a previous run did to the mirror's saves, undone: {saves_undone}",
              file=sys.stderr)
    save_dir = MIRROR.joinpath(*mirror_install.SAVE_DIR)
    saves = {"layout": "the mirror's own copy of " + "/".join(mirror_install.SAVE_DIR)
                       + ", put back from the installation before the run",
             "mirror_rebuilt_because": rebuilt_because,
             "undone_before_run": saves_undone,
             "at_start": mirror_install.saves_of(save_dir),
             "pin": pin.record(ROOT)}
    saves["matches_pin"] = SS.digest(saves["at_start"]) == pin.digest

    built = []
    mods = list(args.profile_mod)
    for mod_id, source in args.builds:
        if not source.is_file():
            raise SystemExit(f"probe source does not exist: {source}")
        dll, dll_sha = compile_probe(source, mod_id, args.defines.get(mod_id, []))
        mod_dir = MIRROR / "mods" / mod_id
        shutil.rmtree(mod_dir, ignore_errors=True)
        mod_dir.mkdir(parents=True)
        shutil.copy2(dll, mod_dir / dll.name)
        (mod_dir / "mod.ini").write_text(
            "Name=SA2 Oracle Probe\nAuthor=OpenSA2 S4 live oracle\n"
            f"Description=Bounded capture generated by {Path(__file__).name}\n"
            f"Version=1.0\nDLLFile={dll.name}\n"
        )
        built.append({
            "mod_id": mod_id,
            "source": str(source),
            "source_sha256": sha256(source),
            # The headers beside the source, which a probe includes by name:
            # the tick probe's field list lives in one, and a capture must be
            # tied to the list it sampled, not only to the file that includes it.
            "headers_sha256": {h.name: sha256(h) for h in sorted(source.parent.glob("*.h"))},
            "defines": args.defines.get(mod_id, []),
            "dll_sha256": dll_sha,
        })
        mods.append(mod_id)
    if not mods:
        raise SystemExit("nothing to load: pass --profile-mod and/or --source")
    overlap = sorted(set(args.capture) & set(args.binary_capture))
    if overlap:
        raise SystemExit(f"--capture and --binary-capture both name {overlap}")
    for mod in mods:
        if not (MIRROR / "mods" / mod).is_dir():
            raise SystemExit(f"mod is not in the mirror: {MIRROR / 'mods' / mod}")
    profile_doc = profile_for(mods, resolution, args.skip_intro, args.pause_on_inactive,
                              args.text_language, args.voice_language)
    profile_path = stage_profile(profile_doc)

    mods_dir = MIRROR / "mods"
    before_files = {p.name: p.stat().st_mtime_ns for p in mods_dir.glob("*") if p.is_file()}
    for name in args.capture + args.binary_capture:
        (mods_dir / name).unlink(missing_ok=True)
    (mods_dir / "SA2ModLoader.log").unlink(missing_ok=True)
    # Always, flag or not: a profile or a vanilla log an earlier run left in the
    # mirror must never be collected as this run's (the P1 review, 2026-09-23:
    # a stale vanilla log would record the vanilla mode for a run whose probe
    # never ran).
    (mods_dir / DP.FILE).unlink(missing_ok=True)
    (mods_dir / OM.VANILLA_LOG).unlink(missing_ok=True)

    snapshot_before = OUT / "snapshots" / f"{args.name}-install-before.json"
    snapshot_before.parent.mkdir(parents=True, exist_ok=True)
    snapshot_before.write_text(
        json.dumps(tree_snapshot.snapshot(INSTALL), indent=1, sort_keys=True) + "\n"
    )

    game_env = game_environment(dict(os.environ))
    session = {
        "display": os.environ.get("DISPLAY"),
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "xdg_session_type": os.environ.get("XDG_SESSION_TYPE"),
        "headless": not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        "host_game_controllers": host_game_controllers(),
        "host_hid_devices": host_hid_devices(),
        # A Wine trace changes what the log holds and how fast the game runs,
        # so a capture says whether it ran under one.
        "winedebug": os.environ.get("WINEDEBUG"),
        "standard_handles": "stdin an empty pipe; stdout and stderr pipes this runner reads",
        "display_modes": host_display_modes(),
        "environment": environment_record(game_env),
        "game_environment": game_env,
        "xauthority": xauthority,
        "saves": saves,
        # The Proton build the command below launches (the module docstring, 6).
        "proton": proton_build(),
    }
    command = [
        "timeout", "--signal=TERM", "--kill-after=5s", f"{args.timeout}s",
        "env",
        f"STEAM_COMPAT_DATA_PATH={PREFIX}",
        f"STEAM_COMPAT_CLIENT_INSTALL_PATH={STEAM_CLIENT}",
        str(PROTON), "runinprefix", "./sonic2app-unpacked.exe", *game_args,
    ]
    # A batch's launch gate, when the environment names one: held here, after
    # every build and the installation's snapshot, so the idle time and the
    # load it waits on are the ones measured next (launch_gate). It refuses
    # the run, launching nothing, when a condition fails.
    session["launch_gate"] = launch_gate(dict(os.environ))
    # The last things read before the launch: how long the oracle sat idle,
    # and the host's load (idle_before_run).
    session["idle_before_run"] = idle_before_run()
    # The one number U4's mask rule reads (tools/s4/snapshot_fields.py IDLE):
    # the smaller, most recent measure.
    session["idle_before_run_seconds"] = session["idle_before_run"]["seconds"]
    session["load_average"] = {"at_launch": load_average(), "cpus": os.cpu_count()}
    # Whether the desktop was locked or idle, and the suspend clock (host_state).
    session["host_state"] = {"at_launch": host_state()}
    launched_ns = time.time_ns()
    started = time.monotonic()
    # stdin is an empty pipe, not whatever launched this runner: the game's CRT
    # types descriptor 0 by GetFileType at startup, and the lifted replay's
    # SA2_STD_HANDLES=pipe answers pipe for all three standard handles.
    completed = subprocess.run(command, cwd=MIRROR, input="", capture_output=True, text=True,
                               env=game_env)
    elapsed = time.monotonic() - started
    mark_game_exit(args.name)
    session["load_average"]["at_end"] = load_average()
    session["host_state"]["at_end"] = host_state()
    # Steam updates Proton in place, under a running game too (the RT batch
    # review, finding 2): the build again, now the game has exited.
    session["proton_at_end"] = proton_build()

    snapshot_after = OUT / "snapshots" / f"{args.name}-install-after.json"
    snapshot_after.write_text(
        json.dumps(tree_snapshot.snapshot(INSTALL), indent=1, sort_keys=True) + "\n"
    )
    differences = tree_snapshot.compare(
        json.loads(snapshot_before.read_text()), json.loads(snapshot_after.read_text())
    )
    # What the snapshot cannot see: the mirror's own directories on the way to
    # its save directory. A write there would carry into every later run.
    mirror_differences = mirror_install.save_path_problems(INSTALL, MIRROR)
    saves["at_end"] = mirror_install.saves_of(save_dir)

    wanted = list(args.capture)
    if not wanted:
        wanted = sorted(
            p.name for p in mods_dir.glob("*.csv")
            if p.is_file() and p.stat().st_mtime_ns > before_files.get(p.name, 0)
        )

    capture_dir.mkdir(parents=True)
    artifacts = []
    for name in wanted:
        produced = mods_dir / name
        if not produced.is_file():
            artifacts.append({"file": name, "present": False})
            continue
        destination = capture_dir / name
        shutil.copy2(produced, destination)
        trimmed = trim_torn_row(destination) if args.trim_torn_row else None
        min_rows, varying = args.policies.get(name, (args.min_rows, args.require_varying))
        try:
            checked = capture_check.check(
                destination, varying, min_rows, list(SHORT_ROW_WIDTHS),
            )
        except SystemExit as error:
            checked = {"ok": False, "problems": [str(error)]}
        artifacts.append({"file": name, "present": True, "trimmed": trimmed,
                          "check": checked,
                          "policy": {"min_rows": min_rows, "require_varying": varying}})
    for name in args.binary_capture:
        produced = mods_dir / name
        if not produced.is_file() or produced.stat().st_size == 0:
            artifacts.append({"file": name, "present": produced.is_file(), "binary": True,
                              "check": {"ok": False, "problems": ["missing or empty"]}})
            continue
        if name == WORD_LOG and produced.stat().st_mtime_ns < launched_ns - LOG_CLOCK_SLACK_NS:
            written = datetime.fromtimestamp(produced.stat().st_mtime, timezone.utc)
            artifacts.append({"file": name, "present": False, "binary": True,
                              "predates_launch": True,
                              "check": {"ok": False, "problems": [
                                  f"the {WORD_LOG} in mods/ was written at "
                                  f"{written.isoformat(timespec='seconds')}, before this run's "
                                  "launch, so it is an earlier run's and not this one's"]}})
            continue
        shutil.copy2(produced, capture_dir / name)
        size = produced.stat().st_size
        produced.unlink()        # snapshots run to hundreds of MB; the capture keeps the copy
        artifacts.append({"file": name, "present": True, "binary": True,
                          "check": {"ok": True, "problems": [], "bytes": size,
                                    "sha256": sha256(capture_dir / name)}})
    # The tick probe's word log: checked, the tick capture built from it, kept
    # compressed (the module docstring, 5).
    word_log = None
    if keeps_word_log(args.builds):
        word_log = finish_word_log(
            capture_dir, artifacts,
            args.policies.get(TICK_CAPTURE, (args.min_rows, args.require_varying)))
    # The device profile. Present, it is kept, read with the runtime's own
    # grammar and refusals, and recorded; a capture without one says so with
    # null, and one run with --device-profile that left none is a problem.
    session["device_profile"] = None
    produced = mods_dir / DP.FILE
    if produced.is_file():
        shutil.copy2(produced, capture_dir / DP.FILE)
        session["device_profile"], profile_artifact_row = profile_artifact(capture_dir / DP.FILE)
        artifacts.append(profile_artifact_row)
    elif args.device_profile:
        artifacts.append({"file": DP.FILE, "present": False, "device_profile": True})
    # The vanilla probe's log is kept whenever the probe ran, whatever --capture
    # named: the capture's oracle mode is read from it (tools/oracle/oracle_mode.py).
    if any(OM._is_vanilla(b) for b in built) and not (capture_dir / OM.VANILLA_LOG).exists():
        produced = mods_dir / OM.VANILLA_LOG
        if produced.is_file():
            shutil.copy2(produced, capture_dir / OM.VANILLA_LOG)
            artifacts.append({"file": OM.VANILLA_LOG, "present": True, "vanilla_log": True,
                              "check": {"ok": True, "problems": [],
                                        "bytes": produced.stat().st_size,
                                        "sha256": sha256(capture_dir / OM.VANILLA_LOG)}})
    log = mods_dir / "SA2ModLoader.log"
    if log.is_file():
        shutil.copy2(log, capture_dir / "SA2ModLoader.log")
    (capture_dir / "native-run.log").write_text(completed.stdout + completed.stderr)

    receipt = {
        "schema": "sa2-oracle-capture/v1",
        "private": True,
        "redistribute": False,
        "name": args.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "command": shlex.join(command),
        "cwd": str(MIRROR),
        "inputs": {
            "installation": str(INSTALL.resolve()),
            "executable_sha256": executable_sha,
            "built_probes": built,
            "enabled_mods": mods,
            "game_args": game_args,
            "timeout_seconds": args.timeout,
            # What the loader was told, byte for byte. The loader's log does
            # not list the game patches it applies, so this is the only record.
            "modloader_profile": profile_doc,
            "modloader_profile_sha256": sha256(profile_path),
        },
        "session": {
            **session,
            "live_desktop_session": not session["headless"],
            "note": (
                "This capture ran on the user's live desktop session. This "
                "machine has no headless display server (no Xvfb, gamescope, "
                "weston or Xnest) and the title exits immediately with code 5 "
                "under Wine's null driver, so there is no headless route. "
                "Window focus and occlusion are therefore variables of this "
                "run. The focus analysis in "
                "agents-journals/s4/2026-09-21-s4-live-oracle.md covered the "
                "configuration before 2026-09-23, paced by the Mod Loader's "
                "child window. Since then the game presents to its own window "
                "and the loader's frame limiter paces it, a floor that never "
                "makes up a slow frame; whether the window's visibility changes "
                "that pace is not measured (Documentation/risks.json, "
                "Determinism). host_state records whether the session was "
                "locked or idle at launch and at the end; the runner changes "
                "no lock or idle setting, and whether a locked or occluded "
                "screen changes the timing is tested by the agreement of "
                "repeats taken in both states (tools/oracle/retake_batch.py, "
                "rule_11)."
            ),
        },
        "run": {
            "return_code": completed.returncode,
            "timed_out": completed.returncode == 124,
            "elapsed_seconds": round(elapsed, 3),
            "modloader_log_bytes": log.stat().st_size if log.is_file() else 0,
        },
        "installation_untouched": not differences,
        "installation_differences": differences,
        "mirror_path_differences": mirror_differences,
        "artifacts": artifacts,
        # What wordlog.receipt_fields says of the tick probe's log, the check
        # and the digest columns' inputs; null for a run with no tick probe.
        "word_log": word_log,
    }
    # Which oracle produced this capture, read from its own files and recorded
    # (the user's direction of 2026-09-23): every comparison recomputes it and
    # refuses a receipt that disagrees. A capture whose mode cannot be
    # established is a problem, because no comparison can use it.
    oracle_problem = None
    try:
        receipt["session"]["oracle"] = OM.mode_from(receipt, capture_dir)
    except OM.ModeError as e:
        oracle_problem = str(e)
        receipt["session"]["oracle"] = {"refused": oracle_problem}
    (capture_dir / "receipt.json").write_text(
        json.dumps(receipt, indent=1, sort_keys=True) + "\n"
    )

    problems = [a for a in artifacts if not a.get("present") or not a.get("check", {}).get("ok")]
    verdict_ok = (
        bool(artifacts)
        and not problems
        and not oracle_problem
        and not differences
        and not mirror_differences
        and saves["matches_pin"]
        and completed.returncode in (0, 124)
    )
    print(
        f"{'SA2_ORACLE_CAPTURE_OK' if verdict_ok else 'SA2_ORACLE_CAPTURE_PROBLEM'} "
        f"name={args.name} exit={completed.returncode} elapsed={elapsed:.1f}s "
        f"artifacts={len(artifacts)} problems={len(problems)} "
        f"installation_untouched={not differences} out={capture_dir}"
    )
    for artifact in artifacts:
        if not artifact.get("present"):
            why = artifact.get("refused")
            print(f"  MISSING {artifact['file']}" + (f": {why}" if why else ""))
            continue
        checked = artifact["check"]
        state = "ok" if checked.get("ok") else "REJECTED"
        if artifact.get("device_profile"):
            print(f"  {state} {artifact['file']} counts={checked.get('counts')} "
                  f"sha256={checked.get('sha256')}")
        elif artifact.get("word_log"):
            print(f"  {state} {artifact['file']} ticks={(word_log or {}).get('ticks')} "
                  f"end={(word_log or {}).get('end_reason')} "
                  f"log_bytes={checked.get('log_bytes', checked.get('bytes'))} "
                  f"bytes={checked.get('bytes')} sha256={checked.get('sha256')}")
        elif artifact.get("binary"):
            print(f"  {state} {artifact['file']} bytes={checked.get('bytes')} "
                  f"sha256={checked.get('sha256')}")
        else:
            print(
                f"  {state} {artifact['file']} rows={checked.get('rows')} "
                f"columns={checked.get('columns')} sha256={checked.get('sha256')}"
            )
        for problem in checked.get("problems", []):
            print(f"      {problem}")
    if oracle_problem:
        print(f"  ORACLE MODE REFUSED: {oracle_problem}")
    else:
        print(f"  oracle mode: {OM.describe(receipt['session']['oracle'])}")
    if word_log is not None:
        scan = ((word_log.get("cost") or {}).get("scan_ms") or {}).get("mean")
        print(f"  word log: {'checked' if word_log['ok'] else 'REFUSED'}, "
              f"{word_log.get('ticks')} ticks, ended by {word_log.get('end_reason')}, kept as "
              f"{word_log.get('stored_as')}"
              + (f"; the scan took {scan:.3f} ms a tick on average" if scan is not None else ""))
    idle = session["idle_before_run"]
    print(f"  idle before the run: {idle['seconds']} s; load average at launch "
          f"{session['load_average']['at_launch']}")
    print(f"  Proton: {session['proton'].get('build')}"
          + ("" if session["proton_at_end"] == session["proton"] else
             f"; MOVED during the run, at its end "
             f"{OM.first_difference(session['proton'], session['proton_at_end'])}"))
    start, end = session["host_state"]["at_launch"], session["host_state"]["at_end"]
    slept = end["boottime_minus_monotonic_s"] - start["boottime_minus_monotonic_s"]
    print(f"  host: session locked {start.get('locked')} at launch and {end.get('locked')} at "
          f"the end, idle {start.get('idle')} and {end.get('idle')}; suspended {slept:.1f} s "
          "during the run")
    gate = session["launch_gate"]
    if gate is not None:
        print(f"  launch gate {gate['step']} of batch {gate['batch']}: passed after "
              f"{gate['waited_s']} s ({gate['polls']} look(s))")
    for line in differences[:20]:
        print(f"  INSTALLATION WRITTEN: {line}")
    for line in mirror_differences[:20]:
        print(f"  MIRROR WRITTEN: {line}")
    if not saves["matches_pin"]:
        print(f"  SAVES NOT PINNED: the run started from {saves['at_start']}, not the pinned "
              f"state {pin.files} ({SS.rel(pin.path)})")
    if not verdict_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
