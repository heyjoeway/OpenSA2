#!/usr/bin/env python3
"""Run the lifted build into a stage the way the Mod Loader's test spawn does.

Subtitle: the reach configuration plus harness/replay/wrap_test_spawn.c and the
tick hook, started at a level through test spawn, then the ticks it ran
compared field by field with a native test-spawn capture and the stop printed.

    source tools/env.sh
    .venv/bin/python tools/s4/stage_run.py [--native <capture directory>]
    .venv/bin/python tools/s4/stage_run.py --input <sa2-input.csv>   # with a recorded controller
    .venv/bin/python tools/s4/stage_run.py --no-native      # walk the stage, compare nothing
    .venv/bin/python tools/s4/stage_run.py --no-native --input <synthetic sa2-input.csv>

Why
---
Documentation/decisions/2026-09-23-S4-stage-route.md: M6's first stage is
Metal Harbor through the Mod Loader's test spawn, reproduced on the lifted side
by wrap_test_spawn.c. Walking the walls stage loading and gameplay hit -- the
missing blocks, unmapped vtable slots and imports S4's queue names -- is this
run, repeated after each fix, which is AGENTS.md rule 13's case for a tool.
The environment is the reach configuration's (tools/s4/reach_receipt.py ENV),
with the stage's settings on top: the test spawn's level and character, the
guest command line the native process has, a larger import budget and pump
budget for a run that plays rather than boots.

The native side's digest columns are built from the capture's word log with
the tree's mask whenever the capture kept one (tools/s4/wordlog.py; the runner
keeps it since 2026-09-25), so they hash the words the lifted side's do: the
lifted wrapper computes its digests in the run with the tree's
harness/replay/tick_digest.h, and a native capture taken under another mask is
still compared word for word (`native_ticks`).

A run compared with a native capture stands for that capture, so it reads the
capture's device profile (SA2_DEVICE_PROFILE), checked against the capture's
receipt by tools/s4/device_profile.py, with every device switch the profile
needs on (tools/s4/reach_receipt.py DEVICES, in the reach configuration for
every run; profile_env is the one definition of what a profile adds), and the
run's own record must say it read that one; a capture whose receipt
records no profile is refused before anything runs, never run on the default
(the user's ruling of 2026-09-23). The capture must also be the vanilla
oracle's as it stands in the tree (tools/oracle/oracle_mode.py
vanilla_problems, asked through tools/s4/tick_replay.py
native_vanilla_problems): the lifted build runs the game's own code, and every
stage capture is taken with the Mod Loader's rewrites undone. That couples
every stage comparison to the vanilla oracle, which the user adopted on
2026-09-23 (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md).

`--no-native` walks the stage with no capture and so on the default, and
records "default". It takes `--input` too: a synthetic recording
(tools/s4/synth_input.py) stands for no capture, so it replays on the default
profile and is compared with nothing.

Every run starts from the pinned saves, as every native one does (the user's
ruling of 2026-09-24): the staged installation must be the pin, and the write
overlay's save directory is cleared first, so the guest reads the
installation's (tools/oracle/save_state.py reset_lifted). A save the game
writes during a run, at the Light Dash pickup, stays in the overlay until the
next run clears it, and stage-run.json keeps what was cleared (`saves`).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import device_profile as DP  # noqa: E402
import reach_receipt as RR  # noqa: E402
import tick_replay as TR  # noqa: E402
import wordlog as WL  # noqa: E402  the word log's reader: a native capture's digest columns
sys.path.append(str(HERE.parent / "oracle"))
import save_state as SS  # noqa: E402  the pinned saves every lifted run starts from

PY = str(ROOT / ".venv" / "bin" / "python")
OUT = ROOT / "private" / "generated" / "replay"
RUNDIR = ROOT / "private" / "generated" / "s4" / "run"
CAPTURES = ROOT / "private" / "generated" / "oracle" / "captures"
# The default native side: the test spawn with no input. mh-spawn-vanilla-v2a
# was taken with a vanilla probe the tree has since replaced and without the
# device profile probe, so it is refused on both counts until RECAPTURE has
# been run and its directory made the default here (fold-profile's spec L5,
# 2026-09-23). The default is named, not guessed: a capture found on disk is
# not made the default until someone has looked at its receipt.
DEFAULT_NATIVE = CAPTURES / "mh-spawn-vanilla-v2a"
RECAPTURE_NAME = "mh-spawn-vanilla-v3a"
# The default capture as a stage run's native side must now be taken: its own
# probes and checks, the vanilla probe first and the device profile probe.
RECAPTURE = (f".venv/bin/python tools/oracle/run_oracle_probe.py --name {RECAPTURE_NAME} "
             "--timeout 200 --no-skip-intro "
             "--build vanilla_probe=harness/replay/native_vanilla_probe.c "
             "--build tick_probe=harness/replay/native_tick_probe.c "
             "--define tick_probe:TICK_LIMIT=1200 "
             "--capture sa2-vanilla.csv --capture sa2-tick-capture.csv "
             "--capture-policy sa2-vanilla.csv=1 --min-rows 1200 --require-varying frame_count "
             f"--binary-capture {WL.LOG} "
             "--device-profile -- --level metalharbor --character sonic")
# wrap_input_replay.c passes through unless SA2_INPUT_SCRIPT is set, so every
# stage run links it and only a run given --input writes the controller.
# harness/stage_goal_probe.c passes through unless SA2_GOAL_LATCH is set: it
# reads the goal latch at each GameLoop call through a wrap of the CRT's
# clock(), GameLoop's first call, for M6's "replay complete"
# (tools/s4/stage_boot.py). On the stage-boot configuration it changed nothing,
# on or off: U9's probe-on and probe-off runs of 2026-09-23 gave the same run
# record, stage trace and tick capture as each other, and as the stage-boot
# evidence kept that day (agents-journals/s4/2026-09-23-m6-goal-latch.md). The
# kept evidence was re-measured on 2026-09-24 and moved since, so the claim is
# on against off, not against today's kept files.
HOOKS = ["harness/replay/wrap_tick_probe.c", "harness/replay/wrap_test_spawn.c",
         "harness/replay/wrap_input_replay.c", "harness/stage_goal_probe.c"]
WRAPS = ["sub_433ee0", "sub_77dd10", "sub_434160", "sub_4436a0", "sub_426760", "sub_77e780",
         "sub_7a8948"]
# What a run records beside its own record. A run with observe=False sets none
# of them: tools/s4/tick_replay.py's baseline for "the observers changed
# nothing" is that run.
OBSERVERS = ("SA2_REPLAY_TICKS", "SA2_STAGE_TRACE", "SA2_GOAL_LATCH", "SA2_INPUT_APPLIED")
STAGES = {"metalharbor": (10, 0, "--level metalharbor --character sonic")}


def native_ticks(native: Path, out: Path) -> tuple[Path, dict]:
    """The native tick capture a lifted run is compared with, and where its
    digest columns came from. When the capture kept its word log, the capture
    is built from the log with the tree's mask (WL.capture_csv, which refuses a
    log that is not whole or overflowed) and written to `out`, after the log is
    shown to be the file its run collected (the receipt's artifact); otherwise
    it is the capture's own file, whose digests are the mask's of its day. A
    log its run refused (its artifact's check.ok) is refused here too, though
    its file is the one collected (the runner review of 2026-09-25, W2)."""
    folder = native.parent
    for name in (WL.LOG + ".zst", WL.LOG):
        log = folder / name
        if not log.is_file():
            continue
        receipt = json.loads((folder / "receipt.json").read_text())
        row = next((a for a in receipt.get("artifacts") or []
                    if isinstance(a, dict) and a.get("file") == name), None)
        check = (row or {}).get("check") or {}
        want = check.get("sha256")
        got = WL.sha256(log)
        if not want or got != want:
            raise WL.WordLogError(f"{DP.rel(log, ROOT)} hashes to {got[:12]}, and its receipt "
                                  f"collected {str(want)[:12]}")
        if check.get("ok") is not True:
            raise WL.WordLogError(f"{DP.rel(log, ROOT)}: its run refused it: "
                                  + ("; ".join(check.get("problems") or []) or "no reason kept"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(WL.capture_csv(log, native=True))
        return out, {"from": DP.rel(log, ROOT), "log_sha256": got,
                     "mask": DP.rel(WL.MASK, ROOT), "mask_sha256": WL.sha256(WL.MASK)}
    return native, {"from": DP.rel(native, ROOT), "mask": "the capture's own"}


def compare(native: Path, lifted: Path) -> dict:
    """First divergence per column. Allocation addresses are compared by which
    allocation they name, with the boot replay's field policy and
    tools/s4/tick_replay.py's normalisation: compared raw, the two heap
    pointers GameLoop holds read as diverging at tick 1 when they agree. The
    native side's digests are its word log's under the tree's mask when it
    kept one (native_ticks)."""
    if not lifted.is_file() or lifted.stat().st_size == 0:
        return {"ticks": 0, "note": "no lifted capture"}
    native = native if native.is_absolute() else ROOT / native
    # A native capture built against other probe headers hashes other words
    # into its digest columns: mh-spawn-v1a, built before the digest mask was
    # regenerated, read as 59 blocks differing from tick 1 that were the mask's
    # version and not the game's (2026-09-23). The replay tool's own check
    # refuses such a capture, and so does this.
    try:
        TR.native_probe_current({"name": native.parent.name,
                                 "native": {"capture": str(native), "repeats": [],
                                            "receipt": str((native.parent / "receipt.json")
                                                           .relative_to(ROOT))}})
    except (SystemExit, ValueError) as e:
        return {"ticks": 0, "note": f"the native capture is not comparable: {e}"}
    try:                                  # a refusal is the result, not a crash after the run
        native, digests = native_ticks(native, lifted.parent / "native-ticks.csv")
    except (SystemExit, OSError, ValueError) as e:
        return {"ticks": 0, "note": f"the native capture's word log cannot be read: {e}"}
    try:
        head, n = TR.read_capture(native)
        lhead, lf = TR.read_capture(lifted)
    except SystemExit as e:
        return {"ticks": 0, "note": str(e), "native_digests": digests}
    if head != lhead:
        return {"ticks": 0, "note": "the headers differ"}
    boot = [r for r in json.loads(TR.REGISTRY.read_text())["replays"] if r["name"] == "boot"]
    if not boot:
        return {"ticks": 0, "note": "no replay named boot in the registry, so no field policy"}
    policy = boot[0].get("field_policy", {})
    n, lf = TR.normalise(head, n, policy), TR.normalise(head, lf, policy, TR.GUEST_HEAP)
    fields = head[1:]                     # read_capture's rows carry the values after the tick
    first = {}
    for (t, a), (_, b) in zip(n, lf):
        for i, (x, y) in enumerate(zip(a, b)):
            if x != y and fields[i] not in first:
                first[fields[i]] = t
    named = {k: v for k, v in first.items() if not k.startswith("d_")}
    return {"ticks": len(lf), "native_ticks": len(n), "native_digests": digests,
            "first_divergence_named": dict(sorted(named.items(), key=lambda kv: kv[1])),
            "digest_columns_differing": len([k for k in first if k.startswith("d_")]),
            "first_digest_divergence": min((v for k, v in first.items() if k.startswith("d_")),
                                           default=None)}


def command(stage: str, polls: int = 1300, label: str = "a",
            input_script: Path | None = None,
            extra_env: dict[str, str] | None = None,
            timeout: int | None = None,
            device_profile: dict | str = DP.DEFAULT, *, name: str | None = None,
            out_dir: Path | None = None,
            observe: bool = True, exe: str = "unpacked",
            exe_overlay: Path | str | None = None) -> tuple[list[str], Path, Path]:
    """The loop_run command for a stage, its run JSON and its tick capture.
    With `input_script`, a recording in native_input_probe.c's format is
    replayed into the controller, and the run is named apart so it never
    shares a binary directory with the stage walk. `name` and `out_dir` name
    the build and where the observers write, for a caller that must not share
    either with the walk (tools/s4/tick_replay.py's stage entry); `observe`
    False sets none of OBSERVERS, and then extra_env may not add one. `exe` is
    which executable the run loads (tools/s4/loop_run.py --exe): "unpacked", the
    default and every registered run's, or "shipped", which links the overlay
    table `exe_overlay` (link_s4.py's default when None). The goal
    latch column is written beside the tick capture. `device_profile` is the
    checked profile of the capture the run stands for (DP.capture_profile), or
    DP.DEFAULT for a walk that stands for none. What a run on it sets beside the
    reach configuration -- SA2_DEVICE_PROFILE, and any device switch the
    configuration leaves off, which is none -- is RR.profile_env's and nobody
    else's; the host-device overrides (DP.HOST_OVERRIDES) and the host keyboard
    (RR.HOST_INPUT_VARS) are never a stage run's, and `extra_env` turns no
    device off and sets nothing this command sets itself."""
    watched = sorted(set(extra_env or {}) & set(OBSERVERS))
    if watched and not observe:
        raise SystemExit(f"STAGE_RUN_REFUSED {', '.join(watched)} in extra_env of a run that "
                         "observes nothing")
    try:
        profile_env = RR.profile_env(device_profile)
    except DP.Refusal as e:
        raise SystemExit(f"STAGE_RUN_REFUSED {e}") from None
    # The devices are the reach configuration's (RR.DEVICES, in RR.ENV), which
    # a setting of the caller's would otherwise override below; and the host
    # keyboard (RR.HOST_INPUT_VARS) is tools/s5/play_live.py's, never a stage run's.
    owned = sorted(set(extra_env or {}) & (set(DP.OWNED) | set(profile_env) | set(RR.DEVICES)
                                           | set(RR.HOST_INPUT_VARS)))
    if owned:
        raise SystemExit(f"STAGE_RUN_REFUSED {', '.join(owned)} in extra_env: the device set is "
                         "the capture's checked profile, passed as device_profile, and the "
                         "reach configuration's devices, with no host keyboard")
    for var in RR.DEVICE_VARS:
        if var in os.environ:
            # loop_run.py hands the guest os.environ under the run's own
            # settings, and the other tools that build this command (stage_boot,
            # reach_extend, d3d9_stride_check) run it with theirs, so a variable
            # left in the shell would reach a run meant for another device set.
            raise SystemExit(f"STAGE_RUN_REFUSED {var} is set in this shell; unset it, since "
                             "a run's devices are the capture's it stands for, set by the tools")
    level, character, cmdline = STAGES[stage]
    name = name or f"stage-{stage}" + ("-input" if input_script else "")
    out = out_dir or OUT / name
    out.mkdir(parents=True, exist_ok=True)
    ticks = out / "lifted.csv"
    # The import budget scales with the poll budget as tools/s4/demo_run.py's
    # does: 8 000 000 up to 1600 polls, so the stage-boot configuration's run
    # is the one it was, and a replay of a whole recording is not cut short.
    env = dict(RR.ENV, SA2_IMPORT_BUDGET=str(max(8_000_000, polls * 5000)),
               SA2_PUMP_POLLS=str(polls),
               SA2_TEST_SPAWN_LEVEL=str(level), SA2_TEST_SPAWN_CHARACTER=str(character),
               SA2_GUEST_CMDLINE=cmdline)
    if observe:
        env["SA2_REPLAY_TICKS"] = str(ticks.relative_to(ROOT))
        env["SA2_GOAL_LATCH"] = str((out / "goal-latch.csv").relative_to(ROOT))
    # The virtual clock's budget, scaled with the poll budget: the game renders
    # a frame per poll at about 17 virtual ms, so 20 ms a poll is room for a long
    # run, and the default poll budget keeps the runtime's own 30 s.
    env["SA2_VIRTUAL_BUDGET_MS"] = str(max(30000, polls * 20))
    env.update(profile_env)
    # Laid over a setting of this command's own, a caller's would run a stage
    # other than the one these arguments describe, or turn off a switch of the
    # reach configuration's (SA2_D3D9_DEVICE, which the D3DX table's answer
    # needs, or SA2_DSOUND_DEVICE): refused, as tools/s4/demo_run.py refuses it
    # (P6's review, N2).
    sets = set(env) | ({"SA2_INPUT_SCRIPT", "SA2_INPUT_APPLIED"} if input_script else set())
    clash = sorted(set(extra_env or {}) & sets)
    if clash:
        raise SystemExit(f"STAGE_RUN_REFUSED {', '.join(clash)} in extra_env: this command sets "
                         "it itself")
    env.update(extra_env or {})
    if input_script:
        env["SA2_INPUT_SCRIPT"] = str(input_script.resolve())
        if observe:
            env["SA2_INPUT_APPLIED"] = str((out / "input-applied.csv").relative_to(ROOT))
    spec = "|".join([label] + [f"{k}={v}" for k, v in env.items()])
    cmd = [PY, str(HERE / "loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN, "--name", name,
           "--out-prefix", name, "--run", spec]
    for h in HOOKS:
        cmd += ["--hook", h]
    for w in WRAPS:
        cmd += ["--wrap", w]
    if exe == "shipped":
        cmd += ["--exe", "shipped", "--exe-overlay"] + ([str(exe_overlay)] if exe_overlay else [])
    elif exe != "unpacked":
        raise SystemExit(f"STAGE_RUN_REFUSED exe {exe!r}: unpacked or shipped")
    elif exe_overlay is not None:
        raise SystemExit("STAGE_RUN_REFUSED an overlay table without exe='shipped'")
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    if timeout is not None:
        # The probe's alarm around its WinMain call (120 s by default; 0 is none),
        # and loop_run's own bound on the process (600 s by default), which would
        # otherwise kill a longer run before the probe could stop it with a note.
        cmd += ["--probe-arg=--timeout", f"--probe-arg={timeout}",
                "--timeout", str((timeout or 3600) + 120)]
    return cmd, RUNDIR / f"{name}.{label}.json", ticks


def native_side(native: Path) -> tuple[dict | None, list[str]]:
    """The checked device profile of the capture a run stands for, and every
    reason it cannot stand for one: not the vanilla oracle's as it stands in the
    tree (tools/oracle/oracle_mode.py vanilla_problems, through
    TR.native_vanilla_problems), not started from the pinned saves the lifted
    run starts from (save_state native_start_problems), or no profile the
    runtime can reproduce. All are listed, so one refusal names all of them."""
    problems = list(TR.native_vanilla_problems(native, root=ROOT))
    problems += SS.native_start_problems(native, ROOT)
    try:
        profile = DP.capture_profile(native, ROOT)
    except DP.Refusal as e:
        return None, problems + [str(e)]
    return (profile if not problems else None), problems


def refusal(native: Path, problems: list[str], recording: bool) -> str:
    """What a refused native side prints: each reason, then what makes one."""
    lines = [f"STAGE_RUN_REFUSED {DP.rel(native, ROOT)} cannot stand for this run:"]
    lines += [f"  - {p}" for p in problems]
    if native == DEFAULT_NATIVE:
        lines.append(f"  re-capture it: {RECAPTURE}")
        lines.append("  then pass --native "
                     f"{DP.rel(CAPTURES / RECAPTURE_NAME, ROOT)}, or make it "
                     "DEFAULT_NATIVE in tools/s4/stage_run.py")
    elif recording:
        lines.append("  re-record it with tools/oracle/record_stage.py, whose runs are taken "
                     "that way")
    lines.append("  --no-native walks the stage on the default profile and compares nothing")
    return "\n".join(lines)


def goal(latch: Path) -> dict:
    """Where the run's goal latch first reads nonzero (harness/stage_goal_probe.c),
    read by stage_boot.py's own reader: what a walk watches for, and no verdict.
    M6's verdict is stage_boot.py's, against a registered replay's native goal
    tick."""
    import stage_boot as SB           # here: stage_boot imports this module
    if not latch.is_file():
        return {"latch": None, "first_nonzero_tick": None}
    try:
        rows = SB.read_goal(latch)
    except SystemExit as e:
        return {"latch": str(latch.relative_to(ROOT)), "refused": str(e)}
    return {"latch": str(latch.relative_to(ROOT)),
            "first_nonzero_tick": next((t for t, v in enumerate(rows) if t >= 1 and v), None)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", default="metalharbor", choices=sorted(STAGES))
    ap.add_argument("--native", type=Path, default=None,
                    help="the native capture directory; default the recording's own with "
                         "--input (tools/oracle/record_stage.py writes the tick capture "
                         "beside sa2-input.csv), else the test-spawn capture with no input")
    ap.add_argument("--polls", type=int, default=1300)
    ap.add_argument("--input", type=Path, default=None,
                    help="a controller recording (native_input_probe.c's CSV) to replay")
    ap.add_argument("--timeout", type=int, default=None,
                    help="the probe's wall-clock alarm around the guest's WinMain call, in "
                         "seconds (120 by default, about the default poll budget; 0 for none); "
                         "loop_run's own process bound is raised to match")
    ap.add_argument("--trace", action="store_true",
                    help="also write the stage trace (level, mode, state, player position)")
    ap.add_argument("--no-native", action="store_true",
                    help="walk the stage and compare with no capture, on the default device "
                         "profile")
    ap.add_argument("--exe", choices=("unpacked", "shipped"), default="unpacked",
                    help="the executable the run loads: the unpacked one (default) or the "
                         "shipped sonic2app.exe through the overlay table "
                         "(tools/s4/loop_run.py --exe); a shipped run is named apart")
    ap.add_argument("--exe-overlay", default=None, metavar="TABLE",
                    help="the overlay table a shipped run links (default link_s4.py's)")
    args = ap.parse_args()
    if args.exe_overlay and args.exe != "shipped":
        ap.error("--exe-overlay is for --exe shipped")
    if args.no_native and args.native:
        ap.error("--no-native compares with no capture, so it takes no --native")
    name = (f"stage-{args.stage}" + ("-input" if args.input else "")
            + ("-shipped" if args.exe == "shipped" else ""))
    # With --no-native the run stands for no capture, a synthetic --input
    # recording included: the default profile, compared with nothing. Otherwise
    # the capture is --native, or the recording's own directory with --input
    # (tools/oracle/record_stage.py writes the tick capture beside
    # sa2-input.csv), or the default test spawn; its device set is refused here,
    # before a build or a run, as is a capture the vanilla oracle did not take.
    native, profile = None, DP.DEFAULT
    if not args.no_native:
        native = args.native or (args.input.parent if args.input else DEFAULT_NATIVE)
        native = native if native.is_absolute() else ROOT / native
        checked, problems = native_side(native)
        if problems:
            print(refusal(native, problems, recording=args.input is not None and not args.native))
            return 1
        profile = checked
    trace = OUT / name / "stage-trace.csv"
    extra = {"SA2_STAGE_TRACE": str(trace.relative_to(ROOT))} if args.trace else None
    cmd, run_json, ticks = command(args.stage, args.polls, input_script=args.input,
                                   extra_env=extra, timeout=args.timeout, device_profile=profile,
                                   name=name, exe=args.exe, exe_overlay=args.exe_overlay)
    try:
        saves = SS.reset_lifted(cmd, ROOT)
    except SS.SaveStateError as e:
        print(f"STAGE_RUN_REFUSED {e.code}")
        return 1
    ticks.unlink(missing_ok=True)
    run_json.unlink(missing_ok=True)
    # The goal probe truncates its column at the run's first GameLoop call, so a
    # run that died before one would otherwise leave the last run's column to be
    # read as this one's `goal`.
    (ticks.parent / "goal-latch.csv").unlink(missing_ok=True)
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=3600,
                           env=RR.clean_environ())
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        p = subprocess.CompletedProcess(cmd, -1, out, f"timed out: {e}")
    stop, read, guest = "", None, None
    if run_json.is_file():
        d = json.loads(run_json.read_text())
        stop = ((d.get("trap") or {}).get("note") or "")[:600]
        try:
            read = DP.check_run(d, profile)
        except DP.Refusal as e:
            read = f"refused: {e}"
        try:
            guest = DP.run_guest(d)
        except DP.Refusal as e:
            guest = f"refused: {e}"
    result = {"stage": args.stage, "run_json": str(run_json.relative_to(ROOT)), "stop": stop,
              **({"exe": (d.get("exe") if run_json.is_file() else None)}
                 if args.exe != "unpacked" else {}),
              "saves": saves,
              "device_profile": DP.recorded(profile), "device_profile_read": read,
              "guest_paths": guest,
              "input": None if args.input is None else str(args.input),
              "native": None if native is None else DP.rel(native, ROOT),
              "vs_native": None if native is None
              else compare(native / "sa2-tick-capture.csv", ticks),
              "goal": goal(ticks.parent / "goal-latch.csv")}
    (ticks.parent / "stage-run.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    if not run_json.is_file():
        # loop_run can exit 0 with no record when the process died before writing it.
        print(f"loop_run exited {p.returncode} and wrote no run record")
        print(p.stdout[-2000:], p.stderr[-2000:])
        return 1
    if read != DP.recorded(profile):
        print(f"STAGE_RUN_REFUSED the run did not read the device profile it was handed: {read}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
