#!/usr/bin/env python3
"""Take one native boot capture, configured as the boot replay's comparison runs.

Subtitle: the boot capture command as one tool (AGENTS.md rule 13), so every
boot capture -- a mask source, a held-out capture, a registered one -- is taken
the same way.

    source tools/env.sh
    .venv/bin/python tools/oracle/boot_capture.py --name boot-ticks-v14a
    .venv/bin/python tools/oracle/boot_capture.py --name boot-ticks-v14b --min-idle 90
    .venv/bin/python tools/oracle/boot_capture.py --name boot-ticks-v14c --stack-pad
    .venv/bin/python tools/oracle/boot_capture.py --name boot-ticks-v14d --dry-run
    .venv/bin/python tools/oracle/boot_capture.py --name devprofile-control-off-v2a --no-device-profile

What it runs
------------
tools/oracle/run_oracle_probe.py with the boot replay's probe set, in its load
order: the vanilla probe first, the import probe before the tick probe (so its
OnFrame reads the counts before the tick probe's ExitProcess at TICK_LIMIT),
and the device-profile probe last; 1200 ticks; the runner's profile and fixed
game environment; and the tick probe's word log collected
(`--binary-capture sa2-word-log.bin`, a runner flag, so before any `--`; a
boot passes the game no argument at all, so there is none). That is the
command Documentation/replays.json's boot entry registers ("how"), with the
word log, which the runner requires of every run that builds the tick probe:
it checks the log, builds sa2-tick-capture.csv's digest columns from it with
the tree's mask, keeps it compressed, and records it in the receipt
(run_oracle_probe.py, its docstring's item 5).

Why one command
---------------
The user's ruling of 2026-09-24 (Documentation/decisions/2026-09-24-S4-halt-220h.md)
takes masks from runs configured exactly like the comparison runs. The judge's
plan (private/generated/s4/wordobs/reports/judge-plan.md) takes the boot's mask
sources and its held-out capture with this tool, at least two of them after the
oracle has sat idle for 90 s: `--min-idle` waits for that before launching, by
the measure the runner records in every receipt (session.idle_before_run), and
refuses to launch after `--max-wait`. The stack control (the registered
v13c-stackpad repeat) is `--stack-pad`.

The profile probe's control is `--no-device-profile`: the same boot without
the device-profile probe. harness/replay/native_device_profile.c's header asks
for a pair of boots that differ only in --device-profile, whose tick captures
are byte-identical, before any capture taken with a changed probe is
registered; tools/oracle/retake_batch.py takes and judges that pair first. A
run without the probe is never registered.

What it reports
---------------
The runner's verdict and its exit code, then what the receipt records of the
word log and of the machine at launch, and whether the capture built from the
log is byte-identical to the boot capture Documentation/replays.json registers
(reported, not judged: a registration moves when its capture is replaced).

Rule 11: the runner launches the unpacked executable directly, input only
through the hook (a boot has none), and records the live desktop session. This
tool waits for, and never launches during, a recording session
(tools/oracle/record_stage.py), which needs the oracle between its runs.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import run_oracle_probe as RP  # noqa: E402  the runner, its word log and its idle measure
import retake_replays as RT  # noqa: E402  live_sessions: a recording session running now

PY = str(ROOT / ".venv" / "bin" / "python")
RUNNER = HERE / "run_oracle_probe.py"
REGISTRY = ROOT / "Documentation" / "replays.json"
TICKS = 1200
TIMEOUT = 200
# The boot replay's probes in load order, each with its defines; the runner
# builds the device-profile probe itself, last (--device-profile).
PROBES = (("vanilla_probe", "harness/replay/native_vanilla_probe.c", ()),
          ("import_probe", "harness/replay/native_import_probe.c", (f"TICK_LIMIT={TICKS}",)),
          ("tick_probe", "harness/replay/native_tick_probe.c", (f"TICK_LIMIT={TICKS}",)))
CAPTURES = ("sa2-vanilla.csv", "sa2-import-calls.csv", RP.TICK_CAPTURE)
# The import log has one row per import slot (251) and no frame counter.
POLICIES = ("sa2-vanilla.csv=1", "sa2-import-calls.csv=251:calls")
STACK_PAD = "tick_probe:STACK_PAD=8192"     # the registered stack control's define
POLL = 5.0                                  # seconds between looks while waiting


def command(name: str, *, stack_pad: bool = False, profile: bool = True,
            lock_timeout: float | None = None) -> list[str]:
    """The runner call, from the repository root. Every runner flag, the word
    log's among them; no `--`, since a boot passes the game no argument."""
    cmd = [PY, str(RUNNER), "--name", name, "--timeout", str(TIMEOUT), "--no-skip-intro"]
    if lock_timeout is not None:
        cmd += ["--lock-timeout", str(lock_timeout)]
    for mod, source, defines in PROBES:
        cmd += ["--build", f"{mod}={source}"]
        for d in defines:
            cmd += ["--define", f"{mod}:{d}"]
    if stack_pad:
        cmd += ["--define", STACK_PAD]
    if profile:
        # Off only for the non-perturbation control's other half
        # (harness/replay/native_device_profile.c: a pair differing only in
        # --device-profile, due again whenever the probe changes).
        cmd += ["--device-profile"]
    for c in CAPTURES:
        cmd += ["--capture", c]
    for p in POLICIES:
        cmd += ["--capture-policy", p]
    cmd += ["--min-rows", str(TICKS), "--require-varying", "frame_count",
            "--binary-capture", RP.WORD_LOG]
    return cmd


def busy() -> list[str]:
    """Why the oracle is not free now: a recording session running
    (record_stage.py, which takes the oracle one run at a time), or another
    runner holding it. [] when neither."""
    out = []
    sessions = RT.live_sessions()
    if sessions:
        out.append(f"a recording session is running (record_stage.py, pid "
                   f"{', '.join(map(str, sessions))})")
    runners = runner_pids()
    if runners:
        out.append(f"another capture is running (run_oracle_probe.py, pid "
                   f"{', '.join(map(str, runners))})")
    return out


def runner_pids() -> list[int]:
    """The processes running tools/oracle/run_oracle_probe.py under a Python
    interpreter; the tests replace it."""
    out = []
    for proc in RT.PROC.glob("[0-9]*"):
        try:
            raw = (proc / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [a.decode(errors="replace") for a in raw.split(b"\0")]
        if len(argv) > 1 and "python" in Path(argv[0]).name and argv[1].endswith(
                "run_oracle_probe.py"):
            out.append(int(proc.name))
    return sorted(out)


def wait_for_quiet(min_idle: float, max_wait: float, *, clock=time.monotonic,
                   sleep=time.sleep) -> dict:
    """Wait until no recording session or other capture runs and, with
    `min_idle`, the oracle has been idle that many seconds by the runner's own
    measure (RP.idle_before_run's `seconds`; unknown counts as not idle
    enough). Returns what it waited for; SystemExit after `max_wait` seconds."""
    start = clock()
    while True:
        reasons = busy()
        idle = RP.idle_before_run()
        if min_idle and (idle["seconds"] is None or idle["seconds"] < min_idle):
            reasons.append(f"the oracle has been idle {idle['seconds']} s, not {min_idle:g} s")
        if not reasons:
            return {"waited_s": round(clock() - start, 1), "idle": idle}
        if clock() - start >= max_wait:
            raise SystemExit(f"BOOT_CAPTURE_REFUSED after waiting {max_wait:g} s: "
                             + "; ".join(reasons))
        sleep(POLL)


def registered_boot() -> str | None:
    """The sha256 of the boot capture Documentation/replays.json registers."""
    try:
        doc = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return None
    boot = next((e for e in doc.get("replays") or [] if e.get("name") == "boot"), {})
    return (boot.get("native") or {}).get("sha256")


def report(name: str) -> dict:
    """What the receipt records of the word log and of the machine at launch,
    and whether the capture built from the log is the registered boot capture's
    bytes; {} when the run left no receipt."""
    path = RP.CAPTURES / name / "receipt.json"
    if not path.is_file():
        return {}
    rec = json.loads(path.read_text())
    log = rec.get("word_log") or {}
    session = rec.get("session") or {}
    cost = log.get("cost") or {}
    registered = registered_boot()
    return {"capture": str(RP.CAPTURES / name),
            "word_log": {k: log.get(k) for k in ("ok", "stored_as", "sha256", "ticks",
                                                 "end_reason", "problems", "capture_sha256")},
            "scan_ms": cost.get("scan_ms"),
            "worst_600_tick_window_s_after_400": cost.get("worst_600_tick_window_s_after_400"),
            "idle_before_run": session.get("idle_before_run"),
            "load_average": session.get("load_average"),
            "registered_boot_sha256": registered,
            "same_as_registered_boot": (bool(registered)
                                        and log.get("capture_sha256") == registered)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="the capture's directory name")
    ap.add_argument("--stack-pad", action="store_true",
                    help=f"the stack control: --define {STACK_PAD}")
    ap.add_argument("--no-device-profile", action="store_true",
                    help="the profile probe's non-perturbation control: the same boot without "
                         "--device-profile, never registered")
    ap.add_argument("--min-idle", type=float, default=0.0, metavar="SECONDS",
                    help="launch only once the oracle has been idle this long "
                         "(session.idle_before_run); the judge's plan takes two boot captures "
                         "after 90 s")
    ap.add_argument("--max-wait", type=float, default=1800.0, metavar="SECONDS",
                    help="how long to wait for a quiet oracle before refusing (default 1800)")
    ap.add_argument("--lock-timeout", type=float, default=None,
                    help="the runner's wait for the oracle's lock")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the runner command and run nothing")
    args = ap.parse_args(argv)
    cmd = command(args.name, stack_pad=args.stack_pad, profile=not args.no_device_profile,
                  lock_timeout=args.lock_timeout)
    if args.dry_run:
        print(f"BOOT_CAPTURE_DRY_RUN from {ROOT}: {shlex.join(cmd)}")
        return 0
    if (RP.CAPTURES / args.name).exists():
        raise SystemExit(f"BOOT_CAPTURE_REFUSED {RP.CAPTURES / args.name} exists")
    waited = wait_for_quiet(args.min_idle, args.max_wait)
    print(f"launching {args.name} after waiting {waited['waited_s']} s; the oracle had been "
          f"idle {waited['idle']['seconds']} s", flush=True)
    done = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    got = report(args.name)
    if args.min_idle:
        # The wait ends before the runner takes the lease, so another run can
        # come between; the receipt says what this one launched after (the
        # runner review of 2026-09-25, N5).
        idle = (got.get("idle_before_run") or {}).get("seconds")
        got["min_idle_met"] = idle is not None and idle >= args.min_idle
    print(json.dumps(got, indent=1))
    ok = done.returncode == 0 and got.get("min_idle_met") is not False
    print(f"BOOT_CAPTURE_{'OK' if ok else 'PROBLEM'} name={args.name} "
          f"runner_exit={done.returncode}"
          + ("" if got.get("min_idle_met") is not False
             else f" min_idle_met=false (idle {(got.get('idle_before_run') or {}).get('seconds')} s at "
                  f"launch, not {args.min_idle:g} s)"))
    return done.returncode or (0 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
