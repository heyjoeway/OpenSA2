#!/usr/bin/env python3
"""The driven run's reach, with the build inputs that produced it.

Why this exists
---------------
"369 WinMain iterations and 378 frames" has steered S4 since slice 12. It was
quoted in journals, carried into the timeline, and used to judge whether a
slice helped. On 2026-09-21 it silently became 1 iteration, and every one of
the 23 gates passed, because **the run's reach was not a recorded quantity and
not a checked one.**

The cause was a generated build input. `mb_edges.<tag>.c` carries the named
dispatch exceptions, and one of them -- the fall-through into 0x00826f60 --
existed only because somebody had once passed `--allow-target VA=reason` on a
command line. Regenerating the table without that argument dropped the row
from `allow=1` with a reason to `allow=0` with none, and the guest stopped in
CRI Sofdec's colour conversion instead of running 369 iterations.

So a reach number is worth nothing without the inputs that produced it. This
writes both, and `Documentation/scripts/check_run_reach.py` fails a push whose
current inputs no longer match the receipt's.

What the manifest covers
------------------------
Everything that can change the number without changing a tracked source file:

* the shipped executable's hash, which is pinned elsewhere but belongs here
  too, because a receipt that cannot say what it ran against is not evidence;
* the hashes of the tools in the path from source to binary, the fold code
  among them (the PC24 pass, `internalize.py`, `speed_rewrite.py` and
  `state_local.py`);
* **the generated inputs**: the edge table's `output_c_sha256` (already
  written by `mb_edges.py` and never checked until now), `lift_extra.py`'s
  state, a digest of the lifted module set, and the fold-code stamp
  `tools/s4/rebuild_image.py` writes beside the objects it rebuilds
  (`fold_code`): which fold code made the objects the run links. The run
  refuses objects whose stamp is not the tree's fold code, and
  `Documentation/scripts/check_run_reach.py` fails a push while it is not;
* the tracked reviewed-exception record;
* the environment and the probe arguments, because a run driven with different
  switches is a different measurement and the last one of those cost a day.

The device profile
------------------
The host's device answers are a recorded property of each native capture (the
user's ruling of 2026-09-23), read by the runtime from SA2_DEVICE_PROFILE. A
reach run that stands for no capture runs on the built-in default and records
"default"; `--replay NAME` measures it on registered replay NAME's profile,
which is what that replay's hooked run must reproduce
(tools/s4/tick_replay.py compares the profiles first). The profile is recorded
in the reach, not the manifest: it is a property of the run, and the manifest
is what the tree alone determines.

A capture's profile needs the recording devices on, and the answers that
replace a refusal with native's outcome (`DEVICES`): each recording device
refuses a capture's profile at configuration while it is off, and the D3DX
constant table answers D3DXGetShaderConstantTable as native does. ENV switches
every one of them on, for the default profile too, since the before/after
comparison on the default profile of 2026-09-24; so the manifest's
environment, which the claim rule reads
(Documentation/scripts/check_refused_presentation.py `replay_environment`),
shows them on. `profile_env` is the one definition of what a run on a profile
sets beside ENV, and every tool that builds a run takes it: this file's `run`,
tools/s4/tick_replay.py `run_lifted`, tools/s4/stage_run.py `command` and
tools/s4/demo_run.py `command`. With ENV switching the devices on, it sets a
capture's profile path and nothing more.

The saves
---------
A reach run starts from the pinned saves (Documentation/save-state.json), as
the replay runs it is compared with do (tools/s4/tick_replay.py
`non_perturbation` holds the hooked run to this receipt field for field): the
installation must be the pin, and the write overlay's save directory is
cleared first (tools/oracle/save_state.py `reset_lifted`). The runtime lists
the overlay, not the installation, when the game searches a directory the
overlay holds, so a save directory left there changed the reach: on
2026-09-24 a run with the shared overlay holding `resource/gd_PC/SAVEDATA`
made twelve import calls more than the same run with it cleared, two more
FindNextFileA and FindClose among them. The game's own directory is always
one the overlay holds, so anything else left at the overlay's root changes
the reach the same way, and the run refuses an overlay that holds anything
after the reset (`save_state.require_empty`; P6's review, W3).

The devices
-----------
The run's record must show each switch it was handed on (`SWITCH_RECORD`,
`switch_problems`): the runtime reports every device's configuration in its
record, and a run whose devices were not its environment's is refused rather
than measured.

Usage
-----

    source tools/env.sh
    .venv/bin/python tools/s4/reach_receipt.py [--name reach] [--replay NAME] [--write]

Without `--write` it prints the manifest and the reach it measured and writes
nothing. Prints summary lines prefixed `REACH`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "s3" / "whole_image"))
import common as C  # noqa: E402
import device_profile as DP  # noqa: E402

OUT = C.ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
REGISTRY = C.ROOT / "Documentation" / "replays.json"
LINK = C.GEN / "s4" / "link"

# The run this number means. Changing any of it changes the measurement, so it
# is here rather than in a caller's shell.
TAG = "next+allarms"
MAIN = "harness/crt_probe.c"
# The budget is a leash, not a measurement: it stops a run at the same import
# call every time so two runs end identically. 400 000 was set when the run died
# at 165 551; once the data DLL mapped (2026-09-22) the run spent the whole
# budget loading inside one GameLoop call and was stopped by it rather than by
# any wall, so the reach measured the leash. 4 000 000 runs in about 11 s.
#
# The pump's poll budget is the second leash, and it was the same mistake a
# second time. At SA2_WND_DEFAULT_POLLS (600) the host posts WM_QUIT and the
# game leaves through its own exit path, so from 2026-09-22 (development_snapshot) the reach
# read 600 iterations because the budget said so, and the boot replay's "first
# divergence at tick 600" was the lifted run being told to quit while the
# native capture ran on to 1200. The walls met there -- wvsprintfA, the CRI
# decoder thread, cvtps2pi -- were real code on that exit path, and it runs
# natively too, but none of them was where the lifted game stopped keeping up.
# The replay runs this configuration, so its budget must outlast every retained
# native capture; tools/s4/tick_replay.py refuses a replay it cannot, and
# 1300 is the longest (1200 ticks) with room.
# The standard handles are no longer set here. SA2_STD_HANDLES=pipe gave the
# lifted run three pipes from 2026-09-23, parity with the live oracle's runner,
# which captures the game's output; a native trace then showed Wine types those
# pipes as character devices (private/generated/oracle/captures/n0-trace-v1a,
# the `host GetStdHandle` lines), so from 2026-09-26 the capture's device
# profile records the handles and their types and the runtime answers from it
# (runtime/sa2_imports.c, "The standard handles"), refusing SA2_STD_HANDLES
# beside such a profile. A run on the default profile gets no standard handle,
# a GUI game launched the ordinary way.
#
# The recording devices, and the D3DX constant table. Each recording device is
# off by default in the runtime and refuses a capture's profile at
# configuration while it is off (runtime/sa2_dinput.c, runtime/sa2_xaudio2.c
# and runtime/sa2_ddraw.c, each *_config_from_env): the lifted game would take
# another path than the capture's game did and still carry the capture's
# sha256. The WMI locator the game's joystick scan asks for is the DirectInput
# device's (runtime/sa2_wbem.h, reached through its enumeration). The table
# (runtime/sa2_d3d9.c) answers D3DXGetShaderConstantTable with success and a
# table, as native does. ENV has switched all four on for every run since the
# before/after comparison on the default profile (2026-09-24), which moved no
# reach field but the import calls. tools/s4/demo_run.py kept its own copy of
# the recording devices' map until the replay tools took this one (2026-09-24).
DEVICES = {"SA2_DINPUT_DEVICE": "1", "SA2_XAUDIO2_DEVICE": "1", "SA2_DDRAW_DEVICE": "1",
           "SA2_D3DX_CONSTANT_TABLE": "1"}
ENV = {"SA2_D3D9_DEVICE": "1", "SA2_DSOUND_DEVICE": "1",
       "SA2_IMPORT_BUDGET": "4000000", "SA2_PUMP_POLLS": "1300", **DEVICES}
# Each device's falsification switch, read beside its device switch (the same
# *_config_from_env, and sa2_d3d9.c and sa2_dsound.c for the two devices ENV
# has switched on longest). A run that means one names it in its own settings;
# one left in a shell would change every run on the default profile, where the
# runtime honours it, and nothing would record it. SA2_D3D9_BREAK and
# SA2_DSOUND_BREAK joined on 2026-09-24 (P6's review, N3/N2): with the D3DX
# table on in every run, a shell's SA2_D3D9_BREAK=tablekeep reached every
# reach and replay run unrecorded. SA2_CTYPE_BREAK, the character data's
# (runtime/sa2_imports.c, "The character data"), joined on 2026-09-26: it is
# no device's, and it changes what every run's C runtime classifies the same
# way.
# SA2_LOCALE_BREAK, SA2_ENV_BREAK and SA2_STDIO_BREAK joined when the profile
# came to carry the capture's locale, environment block and standard handles
# (runtime/sa2_imports.c, "The locale", GetEnvironmentStringsW and "The standard
# handles"): a shell's break would otherwise put every capture's run back on the
# answers before, without a record. SA2_TIME_BREAK joined on 2026-09-27, when
# QueryPerformanceFrequency came to answer native's 10,000,000 (runtime/sa2_imports.c
# SA2_QPC_HZ): a shell's `qpf` would put every run back on 3,579,545 unrecorded.
DEVICE_BREAKS = ("SA2_DINPUT_BREAK", "SA2_XAUDIO2_BREAK", "SA2_DDRAW_BREAK",
                 "SA2_D3D9_BREAK", "SA2_DSOUND_BREAK", "SA2_CTYPE_BREAK",
                 "SA2_LOCALE_BREAK", "SA2_ENV_BREAK", "SA2_STDIO_BREAK", "SA2_TIME_BREAK")
# Every variable that decides a run's devices: the profile, the host-device
# overrides beside it (tools/s4/device_profile.py OWNED), the recording
# devices and their breaks. The tools set them; a shell never does, since
# loop_run.py hands the guest os.environ under the run's own settings.
# The host keyboard's sources and log (runtime/sa2_dinput.h, "The host keyboard"), which
# neither a shell nor a caller's extra settings may hand a registered run: only
# tools/s5/play_live.py sets them. `run`'s extra_env and the stage and attract-demo
# commands' refuse them, and a run record that shows one was set (`dinput.host_input`) is
# refused by switch_problems, which every registered run asks of its record.
HOST_INPUT_VARS = ("SA2_INPUT_DI_SCRIPT", "SA2_INPUT_DI_SOURCE", "SA2_INPUT_DI_LOG")
DEVICE_VARS = tuple(DP.OWNED) + tuple(DEVICES) + DEVICE_BREAKS + HOST_INPUT_VARS
# Which executable a run loads, and the poison tier, are a run's own settings as well, but
# not device variables: the loader maps the shipped sonic2app.exe only under
# SA2_EXE_KIND=shipped (runtime/guest_memory.h), and harness/crt_probe.c reads
# SA2_GUEST_POISON after init. clean_environ strips them, so a shell's value of either never
# reaches a run unrecorded; a tool that wants the shipped executable sets it in that run's own
# settings (tools/s4/loop_run.py --exe shipped), and the run's record says which file the
# loader mapped (`exe`).
RUN_IMAGE_VARS = ("SA2_EXE_KIND", "SA2_GUEST_POISON")
# Where a run's own record shows each switch ENV turns on took effect: each
# device's block in the run record (harness/crt_probe.c), and the D3DX table's
# switch inside the d3d9 one. The environment a run was handed is not what its
# devices were: the runtime refuses a capture's profile with a recording device
# off, but runs one with the table off, and the reach fields cannot tell the
# difference (P6's review, N4: they read the same either way on a capture's
# profile, with D3DXGetShaderConstantTable refused on every call).
SWITCH_RECORD = {"SA2_D3D9_DEVICE": ("d3d9", "device_enabled"),
                 "SA2_DSOUND_DEVICE": ("dsound", "device_enabled"),
                 "SA2_DINPUT_DEVICE": ("dinput", "device_enabled"),
                 "SA2_XAUDIO2_DEVICE": ("xaudio2", "device_enabled"),
                 "SA2_DDRAW_DEVICE": ("ddraw", "device_enabled"),
                 "SA2_D3DX_CONSTANT_TABLE": ("d3d9", "constant_tables", "switch")}
# Empty, and passed to the run below. Until 2026-09-22 this read
# ["--drive-loop"] and was recorded in every receipt while `run()` never passed
# it: the run's own JSON says `"driven": false`, because WinMain reaches its
# own loop and nothing drives it from outside. The gate watched a field that
# never reached the run it described.
PROBE_ARGS: list[str] = []
TOOLS = ["tools/s4/reach_receipt.py", "tools/s4/loop_run.py", "tools/s4/link_s4.py",
         "tools/s4/mb_edges.py", "tools/s4/lift_extra.py", "harness/crt_probe.c",
         "tools/s4/device_profile.py",
         # which saves the run starts from, and how the overlay is cleared
         "Documentation/save-state.json", "tools/oracle/save_state.py",
         # The fold code (tools/s4/rebuild_image.py FOLD_CODE): what turns the lifted IR
         # into the objects the run links. None was pinned here, so an edit to it left the
         # receipt reading current over objects folded by the previous code (review of the
         # S5 speed change, SHOULD-FIX 3); `generated.fold_code` says what made them.
         "tools/spike/pc24pass/pc24pass.py", "tools/s2/corpus_run/internalize.py",
         "tools/s2/corpus_run/speed_rewrite.py", "tools/s2/corpus_run/state_local.py"]
# The runtime is a build input like any other. It was not in the manifest, so a
# runtime change that moved the reach left this receipt reading current, and
# M4 -- read from an import table a probe dumped on 2026-09-20 -- went on
# reporting 122 implemented imports while the runtime implemented at least 151.
RUNTIME_GLOBS = ("runtime/*.c", "runtime/*.cpp", "runtime/*.h")


def runtime_digest(root: Path = C.ROOT) -> str:
    """Every runtime source by path and content, in one digest."""
    h = hashlib.sha256()
    files = [p for g in RUNTIME_GLOBS for p in sorted(root.glob(g))]
    for p in files:
        h.update(f"{p.relative_to(root)}:{sha(p)}\n".encode())
    return h.hexdigest() if files else ""


def profile_env(profile: dict | str) -> dict[str, str]:
    """What a run on `profile` sets beside ENV: nothing for the default, and for
    a capture's profile its path (SA2_DEVICE_PROFILE, tools/s4/device_profile.py
    run_env) and every switch of DEVICES that ENV does not already turn on,
    which is none while ENV turns them all on. `profile` is DP.DEFAULT or a
    profile DP.capture_profile or DP.replay_profile checked. Raises DP.Refusal
    for a path a run spec cannot carry."""
    env = DP.run_env(profile)
    if profile == DP.DEFAULT:
        return env
    return {**{k: v for k, v in DEVICES.items() if ENV.get(k) != v}, **env}


def clean_environ() -> dict[str, str]:
    """This process's environment without any of DEVICE_VARS or RUN_IMAGE_VARS, for a
    run the tools configure: a profile, a host-device override, a device switch, a
    device's break, the executable's kind or the poison tier left in a shell would
    otherwise reach a run meant for another configuration
    (tools/s4/device_profile.py clean_environ strips the first two)."""
    return {k: v for k, v in DP.clean_environ().items()
            if k not in DEVICE_VARS and k not in RUN_IMAGE_VARS}


def switch_problems(record: dict, env: dict[str, str]) -> list[str]:
    """Each switch `env` turns on ("1") that the run's own record does not show
    on (SWITCH_RECORD), and a record whose DirectInput keyboard answered from a
    host source (`dinput.host_input`, which harness/crt_probe.c prints only when
    one of HOST_INPUT_VARS was set), as sentences; [] when the record shows every
    switch on and no host source. The reach run, the tick replay's lifted runs
    and the presentation runs ask it of their records, so a run whose devices
    were not its configuration's is refused, not measured."""
    out = []
    for var, path in SWITCH_RECORD.items():
        if env.get(var) != "1":
            continue
        got = record
        for k in path:
            got = got.get(k) if isinstance(got, dict) else None
        if got is not True:
            out.append(f"{var}=1 was set and the run's record reads {'.'.join(path)} = {got!r}")
    dinput = record.get("dinput") if isinstance(record.get("dinput"), dict) else {}
    if "host_input" in dinput:
        host = dinput["host_input"]
        source = host.get("source") if isinstance(host, dict) else host
        out.append(f"the run's record reads dinput.host_input (source {source!r}): a host "
                   f"keyboard answered its DirectInput device, which only "
                   f"tools/s5/play_live.py sets")
    return out


def import_table_path(name: str) -> Path:
    """Where the run dumps the runtime's import table: M4's numerator."""
    return C.GEN / "s4" / "run" / f"{name}.imports.table.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def object_digest(d: Path, pattern: str) -> tuple[str, int]:
    """A digest over a directory's objects, by name and size.

    Names and sizes rather than contents: hashing 18 394 objects on every push
    would make this too slow to run on every push, which would make it a gate
    nobody runs. The contents are tied to the code that made them instead: the
    fold-code stamp (`manifest`'s `generated.fold_code`) records the fold code
    the last complete rebuild ran, and the run and check_run_reach.py hold it to
    the tree. The emitter-determinism gate does not cover these objects; it
    re-folds its seven panel functions in a scratch directory.
    """
    if not d.is_dir():
        return "", 0
    rows = sorted((p.name, p.stat().st_size) for p in d.glob(pattern))
    h = hashlib.sha256()
    for name, size in rows:
        h.update(f"{name}:{size}\n".encode())
    return h.hexdigest(), len(rows)


def module_set_digest() -> tuple[str, int]:
    """The blocks S4 lifted on its own (`tools/s4/lift_extra.py`)."""
    return object_digest(C.GEN / "s4" / "lift" / "obj", "*.o")


def whole_image_digest() -> tuple[str, int]:
    """The whole-image objects `link_s4.py` links for TAG.

    Until 2026-09-22 only the directory above was digested, while the docstring
    it shared spoke of the 18 394 objects the link reads: the build's largest
    input was not in the manifest at all (review of the tick-replay slice).
    """
    return object_digest(C.BUILD / TAG, "ir_*.o")


def manifest() -> dict:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import rebuild_image as RI
    edges_json = LINK / f"mb_edges.{TAG}.json"
    edges = json.loads(edges_json.read_text()) if edges_json.is_file() else {}
    digest, count = module_set_digest()
    whole, whole_count = whole_image_digest()
    return {
        "tag": TAG,
        "main": MAIN,
        "exe_sha256": sha(C.ROOT / "private" / "exe" / "sonic2app-unpacked.exe"),
        "pipeline": {t: sha(C.ROOT / t) for t in TOOLS},
        "runtime_sha256": runtime_digest(),
        "generated": {
            "mb_edges_c_sha256": edges.get("output_c_sha256", ""),
            "mb_edges_named_exceptions": edges.get("named_exceptions", 0),
            "mb_edges_reviewed_exceptions": len(edges.get("reviewed_exceptions", {}) or {}),
            # The tracked projection, not the generated file. The generated
            # one carries lift_seconds and compile_seconds, so hashing it would
            # make this receipt stale on every regeneration for no semantic
            # reason -- and a staleness gate that fires for no reason is one
            # people learn to re-run past, which is the failure this receipt
            # exists to prevent.
            "lifted_extras_sha256": sha(C.ROOT / "Documentation" / "lifted-extras.json"),
            "lifted_module_digest": digest,
            "lifted_module_count": count,
            "whole_image_module_digest": whole,
            "whole_image_module_count": whole_count,
            # Which fold code made the objects above: the stamp the last complete
            # rebuild wrote ({} when there is none). The run refuses one that is not
            # the tree's, and check_run_reach.py compares it with the tree.
            "fold_code": RI.stamped_fold_code(),
        },
        "tracked": {
            "Documentation/dispatch-exceptions.json":
                sha(C.ROOT / "Documentation" / "dispatch-exceptions.json"),
            "Documentation/lifted-extras.json":
                sha(C.ROOT / "Documentation" / "lifted-extras.json"),
            "Documentation/lifted-extras-backfill.json":
                sha(C.ROOT / "Documentation" / "lifted-extras-backfill.json"),
        },
        "environment": dict(ENV),
        "probe_args": list(PROBE_ARGS),
    }


def run(name: str, extra_env: dict[str, str] | None = None,
        device_profile: dict | str = DP.DEFAULT) -> dict:
    """The reach run. `extra_env` adds switches to ENV, never overrides one, as
    tools/s4/tick_replay.py's run_lifted does. `device_profile` is DP.DEFAULT,
    or a profile DP.replay_profile checked; SA2_DEVICE_PROFILE and the
    recording devices a capture's profile needs are set from it
    (`profile_env`) and from nowhere else, and the run's own record must say it
    read that one. The host-device overrides (DP.HOST_OVERRIDES) and the host
    keyboard (HOST_INPUT_VARS) are refused here, and they, the device switches
    and the devices' breaks are stripped from the inherited environment: a
    reach run's devices are its profile's and ENV's, and `extra_env` can turn
    none of them off. The run starts from the pinned saves and an overlay that
    holds nothing else (the module docstring, "The saves"), and the record
    keeps what the reset cleared and what it left (`saves`), which must be
    nothing. The run's own record must show every switch it was handed on and
    no host keyboard (`switch_problems`)."""
    extra = dict(extra_env or {})
    try:
        profile_vars = profile_env(device_profile)
    except DP.Refusal as e:
        raise SystemExit(f"REACH_FAILED {e}") from None
    clash = sorted(set(extra) & (set(ENV) | set(DP.OWNED) | set(profile_vars)
                                 | set(HOST_INPUT_VARS)))
    if clash:
        raise SystemExit(f"REACH_FAILED extra_env may not set {clash}: ENV is the reach "
                         f"configuration, and the device profile, host-device overrides and "
                         f"the devices a capture's profile switches on included, is passed as "
                         f"device_profile; the host keyboard is no reach run's")
    # The link reads build/next+allarms, which is hard links to the map's and
    # the arms' objects. A rebuild that replaced those files without restaging
    # left the old objects linked, and a reach and a replay were measured on a
    # build nobody had made (2026-09-23 review). Refuse rather than measure it.
    sys.path.insert(0, str(HERE))
    import rebuild_image as RI
    stale = RI.staged_not_rebuilt()
    if stale:
        raise SystemExit(f"{len(stale)} object(s) the link reads are not the current map or arm "
                         f"objects (first {stale[:3]}): run tools/s3/arms/stage_merged_build.py "
                         f"--tag next --merged-tag {TAG} --force")
    # The same for the code that folded them: objects the last complete rebuild did not
    # fold with the tree's fold code are another build than the manifest describes.
    other = RI.fold_code_problems()
    if other:
        raise SystemExit("REACH_FAILED " + "; ".join(other))
    spec = "|".join(["a"] + [f"{k}={v}" for k, v in ENV.items()]
                    + [f"{k}={v}" for k, v in profile_vars.items()]
                    + [f"{k}={v}" for k, v in extra.items()])
    # The tag and the driver are passed, not left to loop_run.py's defaults: a
    # manifest that records a setting the run merely happens to share is the
    # defect PROBE_ARGS was.
    cmd = [str(C.ROOT / ".venv" / "bin" / "python"), str(HERE / "loop_run.py"),
           "--tag", TAG, "--main", MAIN, "--name", name, "--run", spec]
    # `--probe-arg=VALUE`, never `--probe-arg VALUE`: argparse refuses a value
    # that starts with `--` in the second form, loop_run exits on the error,
    # and this read the previous run's JSON as if it were this one's
    # (2026-09-22). The JSON is deleted first and the exit code required, so a
    # run that did not happen cannot be reported.
    for arg in PROBE_ARGS:
        cmd.append(f"--probe-arg={arg}")
    # The dump is an output, not a switch that changes the run, so it is not
    # in PROBE_ARGS: the tick replay passes PROBE_ARGS too, and two tools
    # writing one file is a race nobody would see.
    table = import_table_path(name)
    table.unlink(missing_ok=True)
    cmd += ["--probe-arg=--dump-imports", f"--probe-arg={table.relative_to(C.ROOT)}"]
    out = C.GEN / "s4" / "run" / f"{name}.a.json"
    out.unlink(missing_ok=True)
    # The saves the replay runs start from: the installation is the pin and the
    # overlay this command's run writes holds no save directory, and nothing
    # else either, since the hooked run is held to this one field for field.
    sys.path.insert(0, str(HERE.parent / "oracle"))
    import save_state as SS
    try:
        saves = SS.reset_lifted(cmd, C.ROOT)
        SS.require_empty(saves)
    except SS.SaveStateError as e:
        raise SystemExit(f"REACH_FAILED {e.code}") from None
    # Without SA2_DEVICE_PROFILE, a host-device override, a device switch or a
    # device's break from this shell: loop_run.py hands the guest os.environ
    # under the run's own settings, so a stray one would reach a run meant for
    # another device set.
    p = subprocess.run(cmd, cwd=str(C.ROOT), capture_output=True, text=True, timeout=3600,
                       env=clean_environ())
    if p.returncode != 0:
        raise SystemExit(f"REACH_FAILED loop_run exited {p.returncode}\n{p.stdout[-3000:]}\n{p.stderr[-2000:]}")
    if not out.is_file():
        raise SystemExit(f"REACH_FAILED the run wrote no JSON\n{p.stdout[-3000:]}\n{p.stderr[-2000:]}")
    d = json.loads(out.read_text())
    try:
        read = DP.check_run(d, device_profile)
    except DP.Refusal as e:
        raise SystemExit(f"REACH_FAILED {e}") from None
    off = switch_problems(d, {**ENV, **profile_vars, **extra})
    if off:
        raise SystemExit(f"REACH_FAILED the run's devices are not its configuration's: "
                         f"{'; '.join(off)}")
    pump, d3d9, calls = d.get("pump", {}), d.get("d3d9", {}), d.get("calls", {})
    # The run says whether it was driven; PROBE_ARGS says whether it should be.
    if bool(pump.get("driven")) != ("--drive-loop" in PROBE_ARGS):
        raise SystemExit(f"REACH_FAILED the run reports driven={pump.get('driven')!r} and "
                         f"PROBE_ARGS is {PROBE_ARGS!r}: the manifest would describe another run")
    return {
        "iterations": pump.get("iterations", 0),
        "import_calls": calls.get("total", 0),
        "frames_presented": (d3d9.get("frames") or {}).get("present", 0),
        "stop": (d.get("trap") or {}).get("note", "")[:300],
        "device_profile": read,
        # The directory and executable the run answered, the profile's
        # capture's (DP.check_run held them to it) or C:\SA2 on the default:
        # the import calls move with them (tools/s4/device_profile.py).
        "guest_paths": DP.run_guest(d),
        # What the run set beside ENV for its devices, from its profile or from
        # extra_env: the manifest's environment is ENV alone, so without this a
        # run would be recorded without the switches it ran with. Empty while
        # ENV switches every device on.
        "device_switches": {k: v for k, v in {**profile_vars, **extra}.items()
                            if k in DEVICES},
        "saves": saves,
        "run_json": str(out.relative_to(C.ROOT)),
        "import_table": {
            "path": str(table.relative_to(C.ROOT)),
            "sha256": sha(table),
            "implemented": json.loads(table.read_text()).get("implemented") if table.is_file() else None,
            "refused": refusals(table),
        },
    }


def refusals(table: Path) -> dict[str, dict[str, int]] | None:
    """Every row of the run's import table as {"DLL!name": {"calls": n,
    "refused": m}}, zeros included: how often the run called the import and how
    often its runtime refused it (`sa2_import_refused` counts them on the
    import's row). For check_refused_presentation's claim rule: an import whose
    native outcome answers success is reproduced only if this run called it and
    never refused it.

    Zeros are kept because a missing key is not a zero. Keeping only the
    refused rows made an import the run never called, and one the table never
    dumps, read as "never refused". The table holds the import-address-table
    rows only (harness/crt_probe.c --dump-imports, `sa2_import_count()`), so an
    import resolved through GetProcAddress has no key here at all. Two rows with
    one name are summed, so neither row's refusals are lost.

    None when the table has no `refused_calls` column, which the gate reads as
    no record at all rather than as no refusals. The gate recomputes this from
    the table the receipt names and does not take the receipt's copy on
    trust."""
    rows = json.loads(table.read_text()).get("rows", []) if table.is_file() else []
    if not rows or not all("refused_calls" in r for r in rows):
        return None
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        k = f"{r['dll']}!{r['name']}"
        was = out.get(k, {"calls": 0, "refused": 0})
        out[k] = {"calls": was["calls"] + r.get("calls", 0),
                  "refused": was["refused"] + r["refused_calls"]}
    return out


def replay_device_profile(name: str, registry: Path = REGISTRY) -> dict:
    """Registered replay `name`'s device profile, checked as its own lifted run
    checks it (tools/s4/device_profile.py replay_profile)."""
    entries = {e["name"]: e for e in json.loads(registry.read_text())["replays"]}
    if name not in entries:
        raise SystemExit(f"REACH_FAILED {name!r} is not a registered replay; registered are "
                         f"{sorted(entries)}")
    try:
        return DP.replay_profile(entries[name])
    except DP.Refusal as e:
        raise SystemExit(f"REACH_FAILED {e}") from None


def recorded_device_profile(receipt: Path = OUT) -> dict | str:
    """The device profile a reach receipt was measured on, checked again, for a
    run that must reproduce it (tools/s4/reach_reproduce.py): DP.DEFAULT, or
    the recorded file, which must still hash to its sha256 and still read, with
    the guest directory the receipt was measured in, which must still be the
    one its capture's receipt records (DP.guest_paths): a reach measured in the
    capture's directory and reproduced in C:\\SA2 would be another run."""
    reach = json.loads(receipt.read_text()).get("reach", {})
    rec = reach.get("device_profile", DP.DEFAULT)
    if rec == DP.DEFAULT:
        return DP.DEFAULT
    capture = (C.ROOT / str(rec.get("path"))).parent / "receipt.json"
    try:
        want = DP.guest_paths(json.loads(capture.read_text()), str(rec.get("path")))
    except (OSError, ValueError) as e:
        raise SystemExit(f"REACH_FAILED the receipt's device profile's capture: {e}") from None
    if reach.get("guest_paths") != want:
        raise SystemExit(f"REACH_FAILED the reach receipt records guest directory "
                         f"{reach.get('guest_paths')} and its device profile's capture ran in "
                         f"{want}: re-run tools/s4/reach_receipt.py --replay <name> --write")
    path = C.ROOT / str(rec.get("path"))
    try:
        profile, sha = DP.load(path, str(rec.get("path")))
    except DP.Refusal as e:
        raise SystemExit(f"REACH_FAILED the receipt's device profile: {e}") from None
    if sha != rec.get("sha256"):
        raise SystemExit(f"REACH_FAILED {rec.get('path')} hashes to {sha[:12]} and the reach "
                         f"receipt records {str(rec.get('sha256'))[:12]}")
    return {"path": rec["path"], "sha256": sha, "counts": DP.counts(profile), "guest": want}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="reach")
    ap.add_argument("--replay", default=None, metavar="NAME",
                    help="run on the device profile of registered replay NAME's native "
                         "capture, as that replay's lifted run does; without it the run uses "
                         "the built-in default and records \"default\"")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    m = manifest()
    profile = replay_device_profile(a.replay) if a.replay else DP.DEFAULT
    r = run(a.name, device_profile=profile)
    r["device_profile_of_replay"] = a.replay
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                          capture_output=True, text=True).stdout.strip()
    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-run-reach-v1",
        "question": "how far does the driven guest run, and from which build inputs?",
        "generated_by": "tools/s4/reach_receipt.py",
        "commit": head,
        "inputs": {"pipeline": m["pipeline"]},
        "manifest": m,
        "reach": r,
    }
    print(f"REACH iterations={r['iterations']} import_calls={r['import_calls']} "
          f"frames={r['frames_presented']} "
          f"named_exceptions={m['generated']['mb_edges_named_exceptions']} "
          f"reviewed={m['generated']['mb_edges_reviewed_exceptions']} "
          f"modules={m['generated']['lifted_module_count']} "
          f"device_profile={DP.describe(r['device_profile'])}")
    if a.write:
        OUT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        print(f"REACH wrote {OUT.relative_to(C.ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
