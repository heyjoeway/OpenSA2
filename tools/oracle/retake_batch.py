#!/usr/bin/env python3
"""Take the whole native re-take batch unattended, and leave one receipt.

Subtitle: the one automated re-take the user directed on 2026-09-26, as one
command: the device-profile control, the boots, the stack control, the Metal
Harbor re-take series and the attract demo, each run launched only under the
batch's conditions.

    source tools/env.sh
    .venv/bin/python tools/oracle/retake_batch.py --dry-run     # the plan and its checks; nothing runs
    .venv/bin/python tools/oracle/retake_batch.py               # the whole batch, about 2 h 10 min
    .venv/bin/python tools/oracle/retake_batch.py --boots 0 --no-stack-pad --series 3 --no-demo \\
        --after private/generated/oracle/batches/retake-batch-<stamp>.json   # a top-up series

Run it from a shell of the desktop login. A real batch holds a sleep
inhibitor for its whole life: it starts itself again under
`systemd-inhibit --what=sleep --mode=block` (INHIBIT_*), and refuses to launch
anything unless logind lists that inhibitor. It changes no screen-lock or idle
setting of the user's.

Why
---
The user's direction of 2026-09-26
(Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md) meets M4
honestly with the locale fix and one automated re-take: nobody plays again,
and the re-take replays the user's recording through the hook. The probe
change that carries the locale leaves every capture taken with the old probe
refused, so every capture a gate reads is taken again at once, from one
frozen tree and on one Proton build
(private/generated/s4/g4plan/reports/plan.md, section 3). U6 took its
captures with six hand-written scripts
(private/generated/s4/wordlog-u6/work/boot_batch.sh, mh_series*.sh), and
rule 13 makes the third time a tool.

What it runs
------------
Every run goes through an existing runner, in this order, from this tree:

  0. the device-profile control (a batch that is not a top-up): two boots
     through tools/oracle/boot_capture.py that differ only in
     --device-profile, devprofile-control-off-<v> (--no-device-profile) and
     devprofile-control-on-<v>, each the first of its own series. The probe's
     header (harness/replay/native_device_profile.c) makes such a pair, whose
     tick captures are byte-identical, the condition for registering any
     capture its probe took; the batch judges it at once (judge_control) and
     stops if it fails. A top-up is held to the control its earlier batch
     passed on the same frozen probe;
  1. plain boots: boot_capture.py, one run per call, in series
     (--boots, default 3,3,3,3,2: 14 boots, 5 of them cold), named
     <boot-prefix>a, b, d, e and on (c is the stack control's);
  2. the stack control: boot_capture.py --stack-pad, <boot-prefix>c-stackpad;
  3. Metal Harbor: tools/oracle/retake_replays.py RECORDING --count N, one
     call per series (--series, default 3,3,3,3,3,3,2: 20 re-takes), whose
     command line refuses fewer than retake_replays.MIN_COUNT;
  4. the attract demo: tools/oracle/run_oracle_probe.py with the command that
     took attract-demo-v2a (private/generated/s4/p6/deliver/MANIFEST.md) and
     the word log the tick probe now requires (DEMO_ARGS).

The first run of every series is cold: launched after at least --min-idle
(90) seconds with the oracle idle, by the runner's own measure
(run_oracle_probe.idle_before_run). Every other run follows the one before it
as soon as the conditions allow. --max-load and --min-idle take only finite
values stricter than the plan's (1.0 and 90 s); --max-wait and --poll only
finite ones.

The conditions
--------------
Before anything launches (the preflight): the display is there, Steam is
running, no game is running (run_oracle_probe.game_pids), the batch holds its
sleep inhibitor, the saves are the pin, every capture name is free, no
recording session or other capture holds the oracle, the recording can be
re-taken (retake_replays.check_recording), the Proton build is readable, and
a batch that is not a top-up opens with the control pair and two boots. The
references are then fixed: the Proton build (run_oracle_probe.proton_build,
the Steam client's files among it), the Steam client's process, the suspend
clock, the save pin, and the freeze, the sha256 of every file a run is made
of (FREEZE_*: harness/replay/, tools/oracle/*.py and tools/s4/*.py but their
tests, the word-log reader tools/s4/wordlog.c every run compiles, the replay
registry the series judge reads, the save pin and tools/pins.json).

Before every launch, in this tool before each call and in the runner at the
launch itself, through the launch gate this tool writes and names in the
environment (run_oracle_probe.LAUNCH_GATE, launch_gate), so the runs inside
one retake_replays series are held too:
  * the one-minute load is below --max-load (1.0), polled every --poll
    seconds for at most --max-wait seconds;
  * the first run of a series has been idle --min-idle seconds, waited for the
    same way;
  * Steam is running, as the process the batch began with;
  * no game is running, and the batch's sleep inhibitor is held;
  * the host has not been suspended since the batch began;
  * the saves are the pin, and the pin is the one the batch began on;
  * the Proton build is the one the batch began on;
  * every frozen file hashes as it did when the batch began;
  * the run that ended last, if it ended since the batch began, was launched
    through this batch's gate (so no other run took the oracle in between),
    and, once the first two boots have agreed on a device profile, recorded
    that profile.

After every call, from each capture's receipt (capture_record): the capture is
whole (record_stage.accepted); it was launched through this batch's gate at
this step; its load at launch was below --max-load; the first run of a series
was cold; the host was not suspended during it (session.host_state's suspend
clock); its Proton build at launch and at its end, its save pin, probe
sources and headers, and its word-log reader's inputs are the batch's; every
probe it built is the DLL the batch's first build of that probe was, and the
installation it launched from is the first run's, byte for byte (so what the
freeze cannot name, the compiler and the recording's input script among it,
is held too); and its device profile is the batch's: the first two boots must
agree on one, every run with the probe must record it, and before any Metal
Harbor series it must hold the recording's devices (record_stage.
device_differences). The control's run without the probe must record none. A
Metal Harbor series must run every re-take, and every check retake_replays
judges must hold except the two its re-takes cannot pass before the mask and
the registration chosen from them exist (TOLERATED): U6's seven series each
failed exactly those two, and `retake_replays.py --rejudge` settles item 5
under the new mask.

The first refusal stops the batch: nothing more launches, the receipt says
which step and why, and the exit status is 1. SIGTERM and SIGHUP stop it as
Ctrl-C does, and any error stops it too: the receipt never stays RUNNING.

What it writes
--------------
One receipt, private/generated/oracle/batches/retake-batch-<stamp>.json (or
--receipt), rewritten after every step: the command, the plan, the limits,
the references, the sleep inhibitor, the preflight with the host's state,
and for each step its command, exit status, log, gate and every capture with
its receipt's sha256, its load and idle time at launch, the host's lock and
suspend state at launch and at its end, what the gate measured, its device
profile, Proton build and word log; the control's verdict; the rule-11 note;
the verdict; and the series summaries a re-judge reads. Beside it, in
retake-batch-<stamp>/, each call's output (<step>.log) and the gate the
runner reads (launch-gate.json). --dry-run writes nothing.

Rule 11
-------
Every run is the runner's: the unpacked executable launched directly under
the private Proton wrapper, never through Steam; input only through the hook
(the input probe replaying the user's recording for Metal Harbor, none for a
boot or the demo); nothing written to the Steam installation or its prefix
beyond what the runner does; the live desktop session recorded in every
receipt, with whether it was locked or idle at launch and at the end. The
batch leaves the user's screen-lock and idle settings as they are, so a run
can launch on a locked or blank screen; whether a locked or occluded screen
changes the game's timing is tested by the repeats' agreement, and the
receipt says which runs ran in which state (rule_11). It is the open
"Determinism (time, RNG seeding)" risk (Documentation/risks.json,
oracle_runs_on_a_live_desktop). Anything that needs the user's hands, such
as Steam or its login, or a host that suspended, stops the batch.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import boot_capture as BC  # noqa: E402  the boot runner, and who else holds the oracle
import oracle_mode as OM  # noqa: E402  first_difference
import record_stage as R  # noqa: E402  accepted: what a whole capture is; device_differences
import retake_replays as RT  # noqa: E402  the Metal Harbor runner and its recording check
import run_oracle_probe as RP  # noqa: E402  the runner: its gate, Proton build, idle and load
import save_state as SS  # noqa: E402  the save pin

PY = str(ROOT / ".venv" / "bin" / "python")
SCHEMA = "sa2-retake-batch/v1"
RECORDING = "mh-record-20260924-202305"
BOOT_PREFIX = "boot-ticks-v15"
DEMO_NAME = "attract-demo-v3a"
BOOT_SERIES = (3, 3, 3, 3, 2)
MH_SERIES = (3, 3, 3, 3, 3, 3, 2)
BOOT_LETTERS = "abdefghijklmnopqrstuvwxyz"      # c is the stack control's
MIN_IDLE = 90.0
MAX_LOAD = 1.0
MAX_WAIT = 1800.0
POLL = 15.0
# The device-profile control's two boots: devprofile-control-{off,on}-<version>
# (the last pair, of the probe before the locale's, was v1a on 2026-09-23).
CONTROL_PREFIX = "devprofile-control-"
CONTROL_VERSION = "v2a"
CONTROL_CRITERION = (
    "harness/replay/native_device_profile.c: a pair of captures that differ only in "
    "--device-profile, each with harness/replay/native_vanilla_probe.c built first, whose "
    "tick captures must be byte-identical, run and passed before a capture taken with the "
    "probe is registered")
# The sleep inhibitor a real batch runs under (main, under_inhibitor): logind
# lists it by its holder, systemd-inhibit, this process's parent.
INHIBIT_WHO = "sa2 retake_batch"
INHIBIT_WHY = "a native re-take batch; a suspend would stop the game under a capture"
INHIBITED_ENV = "SA2_RETAKE_BATCH_UNDER_INHIBITOR"
# The checks retake_replays judges that a re-take made for a new mask cannot
# pass until that mask and the registration chosen from the re-takes exist:
# every one of U6's seven series failed exactly these two
# (private/generated/oracle/captures/mh-record-20260924-202305/retakes-*.json),
# and `retake_replays.py --rejudge` judges them again under the new mask.
TOLERATED = ("tick_captures_identical", "import_logs_registrable")
# What a run is made of, frozen for the batch: every file under these trees,
# the Python files these globs match but their tests, and these files: the
# word-log reader every run compiles and runs to build its tick capture from
# its word log (run_oracle_probe.digest_inputs, held per capture), the replay
# registry the series judge reads (retake_replays.identity_columns), the save
# pin and the pins.
FREEZE_TREES = ("harness/replay",)
FREEZE_GLOBS = ("tools/oracle/*.py", "tools/s4/*.py")
FREEZE_FILES = ("Documentation/save-state.json", "tools/pins.json", "tools/s4/wordlog.c",
                "Documentation/replays.json")
# The attract demo: attract-demo-v2a's runner command (the p6 MANIFEST) with
# the word log every run that builds the tick probe collects.
DEMO_TICKS = 3100
DEMO_ARGS = ("--timeout", "240", "--no-skip-intro",
             "--build", "vanilla_probe=harness/replay/native_vanilla_probe.c",
             "--build", "tick_probe=harness/replay/native_tick_probe.c",
             "--define", f"tick_probe:TICK_LIMIT={DEMO_TICKS}",
             "--build", "stage_trace=harness/replay/native_stage_trace.c",
             "--define", f"stage_trace:TICK_LIMIT={DEMO_TICKS}",
             "--device-profile",
             "--capture", "sa2-vanilla.csv", "--capture", "sa2-tick-capture.csv",
             "--capture", "sa2-stage-trace.csv",
             "--capture-policy", "sa2-vanilla.csv=1",
             "--capture-policy", "sa2-tick-capture.csv=1000",
             "--capture-policy", "sa2-stage-trace.csv=1000",
             "--binary-capture", "sa2-word-log.bin")
# Rough seconds per run, launch to launch, from past receipts (a boot ran
# 23.2 to 23.6 s, a Metal Harbor re-take 263 to 265 s, the demo 55 s), plus
# builds and the installation's snapshot; for the dry run's estimate only.
ROUGH_SECONDS = {"control_off": 30, "control_on": 30, "boot": 30, "stack_pad": 30,
                 "series": 275, "demo": 65}
SUMMARY = re.compile(r"^summary: (.+\.json)\s*$", re.M)


class Stop(Exception):
    """The batch stops here: `step` and the reason."""

    def __init__(self, step: str, reason: str):
        super().__init__(f"{step}: {reason}")
        self.step, self.reason = step, reason


@dataclass(frozen=True)
class Step:
    """One runner call. `label` names it in the gate, the log and the
    receipt; `name` is its capture's, None for a Metal Harbor series, whose
    runs retake_replays names; `cold` holds its first run to --min-idle."""
    label: str
    kind: str          # control_off, control_on, boot, stack_pad, series or demo
    name: str | None
    count: int
    cold: bool
    series: str


def plan(boots: list[int], stack_pad: bool, series: list[int], demo: bool,
         boot_prefix: str = BOOT_PREFIX, demo_name: str = DEMO_NAME, control: bool = True,
         control_version: str = CONTROL_VERSION) -> list[Step]:
    """The batch's steps, in order (the module docstring, "What it runs")."""
    if sum(boots) > len(BOOT_LETTERS):
        raise SystemExit(f"--boots: at most {len(BOOT_LETTERS)} boots, one per letter")
    if any(n < 1 for n in boots):
        raise SystemExit("--boots: every series holds at least one boot")
    if any(n < RT.MIN_COUNT for n in series):
        raise SystemExit(f"--series: retake_replays.py takes at least {RT.MIN_COUNT} re-takes "
                         "a call")
    steps = []
    if control:
        for half in ("off", "on"):
            steps.append(Step(f"control-{half}", f"control_{half}",
                              f"{CONTROL_PREFIX}{half}-{control_version}", 1, True,
                              f"control-{half}"))
    letters = iter(BOOT_LETTERS)
    number = 0
    for s, size in enumerate(boots, start=1):
        for k in range(size):
            number += 1
            steps.append(Step(f"boot-{number:02d}", "boot", boot_prefix + next(letters),
                              1, k == 0, f"boots-{s}"))
    if stack_pad:
        steps.append(Step("stack-pad", "stack_pad", f"{boot_prefix}c-stackpad", 1, True,
                          "stack-pad"))
    for s, size in enumerate(series, start=1):
        steps.append(Step(f"metalharbor-{s}", "series", None, size, True, f"metalharbor-{s}"))
    if demo:
        steps.append(Step("demo", "demo", demo_name, 1, True, "demo"))
    return steps


def command(step: Step, recording: str, min_idle: float, max_wait: float) -> list[str]:
    """The runner call `step` makes, from the repository root. The control's
    two halves are boot_capture.py's boot, the first without the profile
    probe (--no-device-profile) and the second exactly a plain boot."""
    if step.kind in ("boot", "stack_pad", "control_off", "control_on"):
        cmd = [PY, str(HERE / "boot_capture.py"), "--name", str(step.name)]
        if step.kind == "stack_pad":
            cmd.append("--stack-pad")
        if step.kind == "control_off":
            cmd.append("--no-device-profile")
        if step.cold:
            cmd += ["--min-idle", f"{min_idle:g}", "--max-wait", f"{max_wait:g}"]
        return cmd
    if step.kind == "series":
        return [PY, str(HERE / "retake_replays.py"), recording, "--count", str(step.count)]
    return [PY, str(HERE / "run_oracle_probe.py"), "--name", str(step.name), *DEMO_ARGS]


def freeze(root: Path | None = None) -> dict[str, str]:
    """Repository path -> sha256 of every file FREEZE_* names under `root`
    (this tree by default)."""
    root = ROOT if root is None else root
    files = {p for top in FREEZE_TREES for p in (root / top).rglob("*") if p.is_file()}
    files |= {p for g in FREEZE_GLOBS for p in root.glob(g)
              if p.is_file() and not p.name.startswith("test_")}
    files |= {root / f for f in FREEZE_FILES if (root / f).is_file()}
    return {p.relative_to(root).as_posix(): RP.sha256(p) for p in sorted(files)}


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, doc: dict) -> None:
    """`doc` at `path`, replaced whole, so a reader never finds half of it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def launch(cmd: list[str], log: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run `cmd` from the repository root with `env`, its output written to
    `log` as it comes; its exit status and its output. Every runner bounds
    its own run (the runner's --timeout, its lease and idle waits), so this
    sets no bound of its own: killing a runner's parent would leave the game."""
    log.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    with log.open("w") as out, subprocess.Popen(cmd, cwd=str(ROOT), env=env, text=True,
                                                stdout=subprocess.PIPE,
                                                stderr=subprocess.STDOUT) as p:
        for line in p.stdout:
            out.write(line)
            out.flush()
            lines.append(line)
    return p.returncode, "".join(lines)


def under_inhibitor(argv: list[str]) -> None:
    """Start this batch again under a blocking sleep inhibitor, in place of
    this process: systemd-inhibit holds the inhibitor while its child, the
    batch, runs, and logind lists systemd-inhibit as its holder. It never
    asks for a password; where one is needed, it fails and nothing runs.
    Only the sleep is inhibited: the screen still locks and blanks as the
    user's settings say."""
    cmd = ["systemd-inhibit", "--no-ask-password", "--what=sleep", "--mode=block",
           f"--who={INHIBIT_WHO}", f"--why={INHIBIT_WHY}",
           sys.executable, str(Path(__file__).resolve()), *argv]
    print("taking a sleep inhibitor: " + shlex.join(cmd), flush=True)
    try:
        os.execvpe(cmd[0], cmd, {**os.environ, INHIBITED_ENV: "1"})
    except OSError as error:
        raise SystemExit(f"RETAKE_BATCH_REFUSED: cannot run under a sleep inhibitor "
                         f"(systemd-inhibit: {error}); nothing ran") from None


# ------------------------------------------------------------ the references

def references(after: Path | None) -> dict:
    """What every run is held to: read now, or with `after` taken from that
    earlier batch's receipt, whose device profile, control, installation and
    probe builds it keeps. The Steam client's process and the suspend clock
    are always read now: a top-up holds its own runs to them."""
    now = {"steam_pid": min(RP.steam_pids(), default=None),
           "suspend_clock_s": RP.suspend_clock()}
    if after is None:
        return {"from": None, "proton": RP.proton_build(), "freeze": freeze(),
                "saves_pin": SS.load().record(ROOT), "device_profile_sha256": None,
                "device_profile_control": None, "installation_sha256": None,
                "probe_dlls": {}, **now}
    try:
        doc = json.loads(Path(after).read_text())
        refs = doc["references"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SystemExit(f"--after {after}: not a batch receipt ({error})") from None
    if doc.get("schema") != SCHEMA or not refs.get("device_profile_sha256"):
        raise SystemExit(f"--after {after}: not a batch receipt whose first two boots agreed on "
                         "a device profile")
    if (refs.get("device_profile_control") or {}).get("passed") is not True:
        raise SystemExit(f"--after {after}: not a batch receipt whose device-profile control "
                         "passed, so no capture a top-up of it takes can be registered")
    return {**refs, "probe_dlls": dict(refs.get("probe_dlls") or {}), **now,
            "from": {"receipt": str(after), "sha256": RP.sha256(Path(after)),
                     "batch": doc.get("batch"), "verdict": doc.get("verdict"),
                     "root": doc.get("root")}}


def preflight(steps: list[Step], recording: str, refs: dict,
              inhibitor: dict | None) -> dict:
    """Everything checked before anything launches; `problems` lists what
    stops the batch there. `inhibitor` is the sleep inhibitor a real batch
    holds ({pid, who}); None for a dry run, which only checks one can be
    taken."""
    problems: list[str] = []
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if not display:
        problems.append("there is no display: the game exits at once with code 5 without one "
                        "(run_oracle_probe.py, 3); run this from a shell of the desktop login")
    steam = RP.steam_pids()
    if not steam:
        problems.append("Steam is not running: it needs the user's hands (AGENTS.md rule 11), "
                        "to start it and log in")
    games = RP.game_pids()
    if games:
        problems.append(f"a game is still running (pid {games}), outside any runner: an "
                        "earlier run's game outlived its runner; it needs ending first")
    listed = RP.sleep_inhibitors()
    if inhibitor is None:
        if shutil.which("systemd-inhibit") is None or listed is None:
            problems.append("no sleep inhibitor can be taken (systemd-inhibit, and logind's "
                            "list of inhibitors through busctl): the batch runs under one")
    elif not RP.holds_sleep(listed, inhibitor["pid"], inhibitor["who"]):
        problems.append(f"the batch holds no sleep inhibitor ({inhibitor['who']}, pid "
                        f"{inhibitor['pid']}): run it as `.venv/bin/python "
                        "tools/oracle/retake_batch.py`, which takes one")
    pin = SS.load()
    wrong = SS.problems(RP.INSTALL, pin)
    if wrong:
        problems.append(SS.refusal(RP.INSTALL, pin, wrong))
    names = [s.name for s in steps if s.name]
    taken = [n for n in names if (RP.CAPTURES / n).exists()]
    if taken:
        problems.append(f"{len(taken)} capture name(s) already taken: {', '.join(taken)}")
    if len(set(names)) != len(names):
        problems.append("the plan names a capture twice")
    problems += BC.busy()
    build = RP.proton_build()
    if build.get("problems"):
        problems.append("the Proton build cannot be read whole: " + "; ".join(build["problems"]))
    end = None
    if any(s.kind == "series" for s in steps):
        end, why = RT.check_recording(recording)
        problems += [f"{recording}: {w}" for w in why]
    played = RP.CAPTURES / recording / "sa2-input.csv"
    kinds = [s.kind for s in steps]
    if refs["from"] is not None:
        if refs["from"].get("root") != str(ROOT):
            # A probe's DLL depends on the path of the tree it is built in (the
            # same native_vanilla_probe.c builds another DLL in another tree), so
            # every run of a top-up from another tree would fail probe_dlls, after
            # its whole run.
            problems.append(f"the earlier batch ran from {refs['from'].get('root')}, and this is "
                            f"{ROOT}: a top-up runs from the batch's own tree, whose probes "
                            "build the batch's DLLs")
        for what, now, then in (("Proton build", build, refs["proton"]),
                                ("freeze", freeze(), refs["freeze"]),
                                ("save pin", pin.record(ROOT), refs["saves_pin"])):
            moved = OM.first_difference(then, now)
            if moved:
                problems.append(f"the {what} is not the earlier batch's ({moved}); a top-up is "
                                "configured like the batch it tops up, or it is another batch")
        if "control_off" in kinds or "control_on" in kinds:
            problems.append("a top-up takes no device-profile control: the batch it tops up "
                            "passed one on the same frozen probe")
    elif kinds[:4] != ["control_off", "control_on", "boot", "boot"]:
        problems.append("a batch opens with the device-profile control's two boots and then two "
                        "plain boots, which fix the device profile every later run is held to "
                        "(or it tops up an earlier batch with --after)")
    return {"ok": not problems, "problems": problems, "display": display, "steam_pids": steam,
            "game_pids": games, "sleep_inhibitor": inhibitor,
            "host_state": RP.host_state(),
            "saves_pin": pin.record(ROOT), "proton_build": build.get("build"),
            "recording": {"name": recording, "end": end,
                          "input_sha256": RP.sha256(played) if played.is_file() else None},
            "checked_utc": utc()}


# -------------------------------------------------------------- after a call

def series_summary(step: Step, output: str) -> tuple[dict | None, list[str]]:
    """The summary retake_replays wrote for `step` (its last `summary:` line)
    and why the series fails the batch: a missing summary, a re-take that did
    not run, too few re-takes, or a failed check outside TOLERATED."""
    found = SUMMARY.findall(output)
    if not found:
        return None, ["retake_replays wrote no summary: " + " | ".join(
            output.strip().splitlines()[-4:])]
    path = Path(found[-1])
    try:
        doc = json.loads(path.read_text())
        runs, checks = doc["runs"], doc["checks"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return None, [f"{path}: not a series summary ({error})"]
    problems = [f"{r.get('name')} did not run: {str(r['error'])[:300]}"
                for r in runs if r.get("error")]
    if len(runs) != step.count:
        problems.append(f"{len(runs)} re-take(s), not {step.count}")
    failed = [k for k, v in checks.items() if not v]
    beyond = [k for k in failed if k not in TOLERATED]
    if beyond:
        problems.append("retake_replays failed " + ", ".join(beyond)
                        + " (only " + ", ".join(TOLERATED) + " wait for the new mask)")
    record = {"path": str(path), "sha256": RP.sha256(path), "verdict": doc.get("verdict"),
              "failed_checks": failed, "runs": [r.get("name") for r in runs],
              "whole": len(runs) == step.count and not any(r.get("error") for r in runs)}
    return record, problems


def snapshot_before(name: str) -> Path:
    """The runner's content snapshot of the installation `name` launched from."""
    return RP.OUT / "snapshots" / f"{name}-install-before.json"


def build_key(b: dict) -> str:
    """One probe build, by what it was built from: its mod, its source's
    repository path and its defines (the recording's input script among
    them, by path)."""
    source = str(b.get("source"))
    try:
        source = Path(source).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        pass
    return " ".join([str(b.get("mod_id")), source, *sorted(map(str, b.get("defines") or []))])


def capture_record(name: str, step: Step, first: bool, batch: str, refs: dict,
                   limits: dict) -> tuple[dict, list[str]]:
    """What the batch keeps of one capture's receipt, and why the capture
    breaks the batch's conditions (the module docstring, "The conditions").
    The first run's installation snapshot and the first build of each probe
    become the batch's (refs), and every later run is held to them."""
    path = RP.CAPTURES / name / "receipt.json"
    try:
        rec = json.loads(path.read_text())
        session = rec["session"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {"name": name}, [f"{name} left no readable receipt ({error})"]
    problems: list[str] = []
    whole, why = R.accepted(rec)
    problems += [f"{name}: {w}" for w in why]
    gate = session.get("launch_gate") or {}
    if gate.get("batch") != batch or gate.get("step") != step.label:
        problems.append(f"{name} was not launched through this batch's gate at {step.label}")
    load = (session.get("load_average") or {}).get("at_launch")
    if not load or load[0] >= limits["max_load"]:
        problems.append(f"{name} launched at a one-minute load of {load and load[0]}, not below "
                        f"{limits['max_load']:g}")
    idle = (session.get("idle_before_run") or {}).get("seconds")
    if first and step.cold and (idle is None or idle < limits["min_idle_s"]):
        problems.append(f"{name} opens its series and launched after {idle} s idle, not "
                        f"{limits['min_idle_s']:g} s")
    for key, when in (("proton", ""), ("proton_at_end", " by its end")):
        moved = OM.first_difference(refs["proton"], session.get(key))
        if moved:
            problems.append(f"{name} ran on another Proton build than the batch's{when}: {moved}")
    host = session.get("host_state") or {}
    start, end = ((host.get(k) or {}).get("boottime_minus_monotonic_s")
                  for k in ("at_launch", "at_end"))
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        problems.append(f"{name} records no suspend clock at launch and at its end, so a "
                        "suspend during it is not ruled out")
    elif end - start > RP.SUSPEND_SLACK_S:
        problems.append(f"{name}: the host was suspended {end - start:.0f} s during the run")
    saves = session.get("saves") or {}
    if saves.get("matches_pin") is not True or \
            (saves.get("pin") or {}).get("digest") != refs["saves_pin"]["digest"]:
        problems.append(f"{name} did not start from the batch's pinned saves")
    built = (rec.get("inputs") or {}).get("built_probes") or []
    for b in built:
        try:
            rel = Path(b["source"]).resolve().relative_to(ROOT).as_posix()
        except (KeyError, ValueError):
            problems.append(f"{name} built {b.get('source')}, outside this tree")
            continue
        wanted = {rel: b.get("source_sha256")}
        wanted |= {(Path(rel).parent / h).as_posix(): v
                   for h, v in (b.get("headers_sha256") or {}).items()}
        off = [p for p, v in wanted.items() if refs["freeze"].get(p) != v]
        if off:
            problems.append(f"{name} built {b.get('mod_id')} from files the freeze does not hold: "
                            + ", ".join(off[:4]))
        key, dll = build_key(b), b.get("dll_sha256")
        if not dll:
            problems.append(f"{name} records no DLL for {key}")
        elif refs["probe_dlls"].setdefault(key, dll) != dll:
            problems.append(f"{name} built {key} as another DLL ({dll[:12]}) than the batch's "
                            f"first build of it ({refs['probe_dlls'][key][:12]}): something "
                            "the freeze does not name moved (the compiler, an included file)")
    log = rec.get("word_log") or {}
    reader = log.get("digest_inputs") or {}
    if not reader:
        problems.append(f"{name} records no word-log reader inputs")
    off = [p for p, v in sorted(reader.items()) if refs["freeze"].get(p) != v]
    if off:
        problems.append(f"{name} built its tick capture with a reader the freeze does not hold: "
                        + ", ".join(off[:4]))
    snap = snapshot_before(name)
    installation = RP.sha256(snap) if snap.is_file() else None
    if installation is None:
        problems.append(f"{name} left no snapshot of the installation it launched from")
    elif (refs.get("installation_sha256") or installation) != installation:
        problems.append(f"{name} launched from another installation than the batch's first run "
                        f"(snapshot {installation[:12]}, the batch's "
                        f"{refs['installation_sha256'][:12]})")
    elif refs.get("installation_sha256") is None:
        refs["installation_sha256"] = installation
    profile = (session.get("device_profile") or {}).get("sha256")
    want = refs["device_profile_sha256"]
    if step.kind == "control_off":
        if profile or any(b.get("mod_id") == RP.PROFILE_MOD for b in built):
            problems.append(f"{name} is the control's run without the profile probe and loaded "
                            "it or recorded a profile")
    elif not profile:
        problems.append(f"{name} records no device profile")
    elif want is not None and profile != want:
        problems.append(f"{name} recorded device profile {profile[:12]}, and the batch's first "
                        f"two boots {want[:12]}")
    record = {"name": name, "receipt_sha256": RP.sha256(path),
              "whole": whole, "return_code": (rec.get("run") or {}).get("return_code"),
              "elapsed_seconds": (rec.get("run") or {}).get("elapsed_seconds"),
              "load_at_launch": load,
              "load_at_end": (session.get("load_average") or {}).get("at_end"),
              "idle_before_run_s": idle, "opens_series": first, "launch_gate": gate or None,
              "device_profile_sha256": profile,
              "proton_build": (session.get("proton") or {}).get("build"),
              "proton_build_at_end": (session.get("proton_at_end") or {}).get("build"),
              "live_desktop_session": session.get("live_desktop_session"),
              "host_state": host or None,
              "installation_untouched": rec.get("installation_untouched"),
              "installation_snapshot_sha256": installation,
              "word_log": {k: log.get(k) for k in ("ok", "stored_as", "sha256", "ticks",
                                                   "end_reason", "capture_sha256")}
              if log else None}
    return record, problems


# ------------------------------------------------------ the device profile

def receipt_of(name: str) -> dict:
    """`name`'s receipt, {} when it cannot be read."""
    try:
        return json.loads((RP.CAPTURES / name / "receipt.json").read_text())
    except (OSError, ValueError):
        return {}


def without_profile(rec: dict) -> dict:
    """What a run was configured with, the profile probe taken out: its probe
    builds but the profile probe's, the mods it enabled and the loader's
    profile without it, the game's arguments, timeout, executable,
    installation and launch command, and the game's environment, save pin,
    Proton build, WINEDEBUG, standard handles and oracle mode. Two runs that
    differ only in --device-profile give the same."""
    inputs, session = rec.get("inputs") or {}, rec.get("session") or {}
    mods = [m for m in inputs.get("enabled_mods") or [] if m != RP.PROFILE_MOD]
    loader = json.loads(json.dumps(inputs.get("modloader_profile") or {}))
    if isinstance(loader.get("EnabledMods"), list):
        loader["EnabledMods"] = [m for m in loader["EnabledMods"] if m != RP.PROFILE_MOD]
    return {"built_probes": [{k: b.get(k) for k in ("mod_id", "source", "source_sha256",
                                                     "headers_sha256", "defines", "dll_sha256")}
                             for b in inputs.get("built_probes") or []
                             if b.get("mod_id") != RP.PROFILE_MOD],
            "enabled_mods": mods, "modloader_profile": loader,
            **{k: inputs.get(k) for k in ("game_args", "timeout_seconds", "executable_sha256",
                                          "installation")},
            "command": rec.get("command"), "cwd": rec.get("cwd"),
            "saves_pin": ((session.get("saves") or {}).get("pin") or {}).get("digest"),
            **{k: session.get(k) for k in ("game_environment", "proton", "winedebug",
                                           "standard_handles", "oracle")}}


def tick_capture_sha256(name: str) -> str | None:
    path = RP.CAPTURES / name / RP.TICK_CAPTURE
    return RP.sha256(path) if path.is_file() else None


def judge_control(off: str, on: str, refs: dict) -> dict:
    """The device-profile control, by its own criterion (CONTROL_CRITERION):
    each run built the vanilla probe first; the two differ only in the
    profile probe (without_profile, and only `on` loaded it, last); and their
    tick captures are byte-identical. Its verdict, `passed`, and what it
    compared."""
    recs = {off: receipt_of(off), on: receipt_of(on)}
    built = {n: [b.get("mod_id") for b in (r.get("inputs") or {}).get("built_probes") or []]
             for n, r in recs.items()}
    ticks = {n: tick_capture_sha256(n) for n in (off, on)}
    vanilla_first = all(b[:1] == ["vanilla_probe"] for b in built.values())
    profile_only_in_on = (RP.PROFILE_MOD not in built[off] and built[on][-1:] == [RP.PROFILE_MOD]
                          and not ((recs[off].get("session") or {}).get("device_profile"))
                          and bool((recs[on].get("session") or {}).get("device_profile")))
    difference = OM.first_difference(without_profile(recs[off]), without_profile(recs[on]))
    identical = ticks[off] is not None and ticks[off] == ticks[on]
    return {"criterion": CONTROL_CRITERION, "probe": RP.DP.PROBE,
            "probe_sha256": refs["freeze"].get(RP.DP.PROBE),
            "off": off, "on": on, "tick_capture_sha256": ticks,
            "tick_captures_identical": identical, "vanilla_probe_first": vanilla_first,
            "profile_probe_only_in_on": profile_only_in_on,
            "configuration_difference": difference,
            "passed": identical and vanilla_first and profile_only_in_on and difference is None}


def control_problems(verdict: dict) -> list[str]:
    """Why the control failed, [] when it passed."""
    out = []
    if not verdict["vanilla_probe_first"]:
        out.append("a run of the pair did not build the vanilla probe first")
    if not verdict["profile_probe_only_in_on"]:
        out.append(f"the profile probe was not loaded by {verdict['on']} alone, last")
    if verdict["configuration_difference"] is not None:
        out.append("the pair differs in more than --device-profile: "
                   + verdict["configuration_difference"])
    if not verdict["tick_captures_identical"]:
        t = verdict["tick_capture_sha256"]
        out.append("their tick captures are not byte-identical ("
                   + ", ".join(f"{k} {(v or 'none')[:12]}" for k, v in t.items()) + ")")
    return [f"the device-profile control failed ({CONTROL_CRITERION}): " + "; ".join(out)] \
        if out else []


def recording_problems(boot: str, recording: str) -> list[str]:
    """Why the device set `boot` recorded cannot be the recording's, [] when
    it can: its profile against the recording's, section by section
    (record_stage.device_differences). Every Metal Harbor re-take is held to
    the recording's devices and to the batch's one profile, so a boot that
    fails this means every series would fail, found before the first."""
    recording_profile = (receipt_of(recording).get("session") or {}).get("device_profile")
    mine = (receipt_of(boot).get("session") or {}).get("device_profile")
    got = R.device_differences(recording_profile, [{"name": boot, "device_profile": mine}],
                               recording)
    return [f"the batch's device profile cannot be the recording's, so every Metal Harbor "
            f"re-take would fail: " + "; ".join(got)] if got else []


# ------------------------------------------------------------------ the batch

def gate_spec(batch: str, started: float, step: Step, refs: dict, limits: dict,
              inhibitor: dict) -> dict:
    """The launch gate for `step` (run_oracle_probe.GATE_KEYS), written now,
    for the batch begun at `started` (epoch seconds)."""
    return {"schema": RP.GATE_SCHEMA, "batch": batch, "step": step.label, "root": str(ROOT),
            "batch_started_epoch": started, "written_epoch": time.time(),
            "max_load": limits["max_load"],
            "min_idle_first_s": limits["min_idle_s"] if step.cold else 0.0,
            "wait_s": limits["max_wait_s"], "poll_s": limits["poll_s"], "steam": True,
            "proton": refs["proton"], "freeze": refs["freeze"],
            "saves_pin": refs["saves_pin"]["digest"],
            "profile_sha256": refs["device_profile_sha256"],
            "steam_pid": refs["steam_pid"], "suspend_clock_s": refs["suspend_clock_s"],
            "sleep_inhibitor": inhibitor}


def take_step(step: Step, doc: dict, gate_path: Path, logs: Path, recording: str) -> dict:
    """Run one step under its gate and examine what it made; its record, or
    Stop. Judges the device-profile control after its second run, and settles
    the batch's device profile after its first two boots."""
    refs, limits, batch = doc["references"], doc["limits"], doc["batch"]
    spec = gate_spec(batch, doc["started_epoch"], step, refs, limits, doc["sleep_inhibitor"])
    write_json(gate_path, spec)
    cmd = command(step, recording, limits["min_idle_s"], limits["max_wait_s"])
    record: dict = {"step": step.label, "kind": step.kind, "series": step.series,
                    "cold": step.cold, "command": shlex.join(cmd),
                    "gate": {"path": str(gate_path), "sha256": RP.sha256(gate_path),
                             "written_epoch": spec["written_epoch"],
                             "min_idle_first_s": spec["min_idle_first_s"],
                             "profile_sha256": spec["profile_sha256"]},
                    "started_utc": utc()}
    doc["steps"].append(record)
    print(f"== {step.label} ({step.series}{', cold' if step.cold else ''}) "
          f"{step.name or f'{step.count} re-takes of {recording}'}", flush=True)
    try:
        record["before_call"] = RP.wait_for_gate(spec, clock=time.monotonic, sleep=time.sleep)
    except SystemExit as refused:
        raise Stop(step.label, str(refused.code)) from None
    log = logs / f"{step.label}.log"
    code, output = launch(cmd, log, {**os.environ, RP.LAUNCH_GATE: str(gate_path)})
    record.update(exit=code, log=str(log), ended_utc=utc())
    problems: list[str] = []
    if step.kind == "series":
        record["summary"], problems = series_summary(step, output)
        names = (record["summary"] or {}).get("runs") or []
        names = [n for n in names if (RP.CAPTURES / str(n) / "receipt.json").is_file()]
    else:
        names = [str(step.name)]
        if code != 0:
            problems.append(f"the runner call exited {code}: " + " | ".join(
                output.strip().splitlines()[-4:]))
    record["captures"] = []
    for k, name in enumerate(names):
        got, why = capture_record(name, step, k == 0, batch, refs, limits)
        record["captures"].append(got)
        problems += why
    if step.kind == "control_on" and not problems:
        off = next(s["captures"][0]["name"] for s in doc["steps"] if s["kind"] == "control_off")
        refs["device_profile_control"] = judge_control(off, str(step.name), refs)
        problems += control_problems(refs["device_profile_control"])
    boots = [c for s in doc["steps"] if s["kind"] == "boot" for c in s.get("captures", [])]
    if refs["device_profile_sha256"] is None and step.kind == "boot" and len(boots) == 2:
        first, second = (c.get("device_profile_sha256") for c in boots)
        if first and first == second:
            refs["device_profile_sha256"] = first
            record["device_profile_fixed"] = first
            earlier = [c for s in doc["steps"] if s["kind"] in ("control_on", "stack_pad")
                       for c in s.get("captures", [])]
            problems += [f"{c['name']} recorded device profile "
                         f"{str(c.get('device_profile_sha256'))[:12]}, and the batch's first two "
                         f"boots {first[:12]}" for c in earlier
                         if c.get("device_profile_sha256") != first]
            if any(s["kind"] == "series" for s in doc["plan"]):
                problems += recording_problems(boots[1]["name"], recording)
        else:
            problems.append(f"the first two boots recorded device profiles {str(first)[:12]} and "
                            f"{str(second)[:12]}: the profile is not one per host, so no mask "
                            "can be chosen from these runs")
    if problems:
        record["problems"] = problems
        raise Stop(step.label, "; ".join(problems))
    return record


def rule_11(records: list[dict]) -> dict:
    """The note AGENTS.md rule 11 asks for, over every capture the batch took,
    with each run's lock state at launch and at its end."""
    live = {r["name"]: r.get("live_desktop_session") for r in records}
    locked = {r["name"]: [((r.get("host_state") or {}).get(k) or {}).get("locked")
                          for k in ("at_launch", "at_end")] for r in records}
    seen = {v for pair in locked.values() for v in pair}
    return {
        "live_desktop_session": live,
        "every_run_on_the_live_desktop": bool(live) and all(v is True for v in live.values()),
        "session_locked": locked,
        "idle": {r["name"]: [((r.get("host_state") or {}).get(k) or {}).get("idle")
                             for k in ("at_launch", "at_end")] for r in records},
        "lock_states_seen": sorted({"locked" if v else "unlocked" if v is False else "unknown"
                                    for v in seen}),
        "display": os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"),
        "session_type": os.environ.get("XDG_SESSION_TYPE"),
        "launched": ("the unpacked executable, directly, by tools/oracle/run_oracle_probe.py "
                     "under the private Proton wrapper; never through Steam"),
        "input": ("only through the hook: the input probe replays the user's recording for "
                  "Metal Harbor (INPUT_MODE=2); a boot and the attract demo take none; no "
                  "synthetic OS-level input anywhere"),
        "installation": ("nothing written to the Steam installation or its prefix beyond what "
                         "the runner does; each receipt proves the staged installation "
                         "untouched (installation_untouched)"),
        "sleep": ("the batch held a blocking sleep inhibitor (systemd-inhibit --what=sleep) "
                  "and refused any run the host was suspended during or since it began"),
        "focus_and_occlusion": (
            "Each run ran on the user's live desktop session, with the loader's pause on "
            "focus loss off (EnablePauseOnInactive false, run_oracle_probe.profile_for), so a "
            "click elsewhere does not stop the game loop. The batch left the user's "
            "screen-lock and idle settings as they were, so a run could launch on a locked or "
            "blank screen: session_locked and idle give each run's state at launch and at its "
            "end, from logind. Whether a locked or occluded screen changes the game's timing "
            "is tested by the repeats' agreement: the boots and the Metal Harbor re-takes are "
            "repeats of one configuration that the masks and the series judge compare, so "
            "repeats taken locked and unlocked that agree show no effect on what is compared, "
            "and repeats that disagree along the lock state are a finding. It tests the effect "
            "only where lock_states_seen holds both states. It is a determinism risk for "
            "replays, registered as the open 'Determinism (time, RNG seeding)' risk in "
            "Documentation/risks.json (oracle_runs_on_a_live_desktop)"),
    }


def outputs(doc: dict, recording: str) -> dict:
    """What the batch made, by role, and the re-judge its whole series feed
    (retake_replays.py --rejudge takes no series with a re-take that did not
    run, nor one that broke a condition of the batch's)."""
    caps = {k: [c["name"] for s in doc["steps"] if s["kind"] == k for c in s.get("captures", [])]
            for k in ("control_off", "control_on", "boot", "stack_pad", "demo")}
    cold = [s["captures"][0]["name"] for s in doc["steps"]
            if s["kind"] == "boot" and s["cold"] and s.get("captures")]
    series = [s["summary"] for s in doc["steps"] if s["kind"] == "series" and s.get("summary")]
    summaries = [Path(s["summary"]["path"]).name for s in doc["steps"]
                 if s["kind"] == "series" and s.get("summary") and s["summary"]["whole"]
                 and not s.get("problems")]
    return {"device_profile_control": doc["references"].get("device_profile_control"),
            "control": caps["control_off"] + caps["control_on"],
            "boots": caps["boot"], "cold_boots": cold, "stack_pad": caps["stack_pad"],
            "series": series, "retakes": [n for s in series for n in s["runs"]],
            "demo": caps["demo"],
            "rejudge_command": (shlex.join([".venv/bin/python", "tools/oracle/retake_replays.py",
                                            recording, "--rejudge", *summaries])
                                if summaries else None)}


def interrupted(signum, frame):
    """SIGTERM and SIGHUP stop the batch as Ctrl-C does."""
    raise KeyboardInterrupt(signal.Signals(signum).name)


def run_batch(args: argparse.Namespace, argv: list[str]) -> int:
    steps = plan(args.boots, args.stack_pad, args.series, args.demo, args.boot_prefix,
                 args.demo_name, control=args.after is None,
                 control_version=args.control_version)
    limits = {"max_load": args.max_load, "min_idle_s": args.min_idle,
              "max_wait_s": args.max_wait, "poll_s": args.poll}
    refs = references(args.after)
    inhibitor = None if args.dry_run else {"pid": os.getppid(), "who": INHIBIT_WHO}
    checked = preflight(steps, args.recording, refs, inhibitor)
    if args.dry_run:
        return dry_run(steps, args, refs, checked)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    batch = f"retake-batch-{stamp}"
    path = args.receipt or RP.OUT / "batches" / f"{batch}.json"
    work = path.with_name(path.stem)
    doc = {"schema": SCHEMA, "private": True, "batch": batch, "root": str(ROOT),
           "command": shlex.join([".venv/bin/python", "tools/oracle/retake_batch.py", *argv]),
           "started_utc": utc(), "started_epoch": time.time(), "ended_utc": None,
           "recording": args.recording,
           "plan": [asdict(s) for s in steps], "limits": limits,
           "tolerated_retake_checks": list(TOLERATED), "references": refs,
           "sleep_inhibitor": inhibitor,
           "preflight": checked, "steps": [], "verdict": "RUNNING", "stopped": None}
    code = 0
    kept = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        try:
            if not checked["ok"]:
                raise Stop("preflight", "; ".join(checked["problems"]))
            write_json(path, doc)
            for step in steps:
                take_step(step, doc, work / "launch-gate.json", work, args.recording)
                write_json(path, doc)
            doc["verdict"] = "BATCH_OK"
        except Stop as stop:
            doc["verdict"], doc["stopped"] = "BATCH_STOPPED", {"step": stop.step,
                                                              "reason": stop.reason}
            code = 1
        except KeyboardInterrupt as why:
            doc["verdict"], doc["stopped"] = "BATCH_STOPPED", {
                "step": doc["steps"][-1]["step"] if doc["steps"] else None,
                "reason": f"interrupted ({why or 'SIGINT'}); a runner and its game may "
                          "outlive the batch",
                "left_running": {"runners": BC.runner_pids(), "games": RP.game_pids()}}
            code = 130
        except Exception as error:      # noqa: BLE001  the receipt never stays RUNNING
            doc["verdict"], doc["stopped"] = "BATCH_STOPPED", {
                "step": doc["steps"][-1]["step"] if doc["steps"] else None,
                "reason": f"{type(error).__name__}: {error}",
                "left_running": {"runners": BC.runner_pids(), "games": RP.game_pids()}}
            code = 1
        records = [c for s in doc["steps"] for c in s.get("captures", [])]
        doc.update(ended_utc=utc(), rule_11=rule_11(records),
                   outputs=outputs(doc, args.recording))
        write_json(path, doc)
    finally:
        for sig, handler in kept.items():
            signal.signal(sig, handler)
    out = doc["outputs"]
    if code == 0:
        print(f"RETAKE_BATCH_OK {batch}: device-profile control passed "
              f"({', '.join(out['control']) or 'the earlier batch'}), {len(out['boots'])} boots "
              f"({len(out['cold_boots'])} cold), stack control "
              f"{', '.join(out['stack_pad']) or 'none'}, "
              f"{len(out['retakes'])} re-takes in {len(out['series'])} series, demo "
              f"{', '.join(out['demo']) or 'none'}")
    else:
        print(f"RETAKE_BATCH_STOPPED {batch} at {doc['stopped']['step']}: "
              f"{doc['stopped']['reason']}")
    print(f"receipt: {path}")
    return code


def dry_run(steps: list[Step], args: argparse.Namespace, refs: dict, checked: dict) -> int:
    """The plan, each call's command, the references and the preflight;
    nothing launched and nothing written."""
    runs = sum(s.count for s in steps)
    colds = sum(1 for s in steps if s.cold)
    rough = sum(ROUGH_SECONDS[s.kind] * s.count for s in steps) + colds * args.min_idle
    print(f"RETAKE_BATCH_DRY_RUN: {len(steps)} calls, {runs} runs, {colds} of them cold; roughly "
          f"{rough / 60:.0f} min on a quiet host. Nothing runs and nothing is written.")
    for s in steps:
        print(f"  {s.label:15} {s.series:15} {'cold' if s.cold else 'warm'}  "
              f"{shlex.join(command(s, args.recording, args.min_idle, args.max_wait))}")
    profile = refs["device_profile_sha256"]
    print(f"Proton build: {refs['proton'].get('build')}; frozen files: {len(refs['freeze'])}; "
          f"save pin {refs['saves_pin']['digest'][:12]}; device profile "
          + ("from the first two boots" if profile is None else profile[:12])
          + "; device-profile control "
          + ("the first two runs" if refs["from"] is None else "the earlier batch's, passed"))
    print(f"limits: load below {args.max_load:g}, the first run of a series {args.min_idle:g} s "
          f"idle, waits of at most {args.max_wait:g} s polled every {args.poll:g} s")
    host = checked["host_state"]
    print(f"host: Steam pid {refs['steam_pid']}, session locked {host.get('locked')}, idle "
          f"{host.get('idle')}; a real batch runs under systemd-inhibit --what=sleep")
    if checked["problems"]:
        print("A real batch stops before any run:")
        print("\n".join(f"  - {p}" for p in checked["problems"]))
        return 1
    print("preflight: ok")
    return 0


def sizes(text: str) -> list[int]:
    """`3,3,2` as [3, 3, 2]; `0` or `` as []."""
    if text.strip() in ("", "0"):
        return []
    try:
        out = [int(x) for x in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a comma-separated list of counts") \
            from None
    return out


def main(argv: list[str] | None = None, *, reexec: bool = False) -> int:
    """The command line. Only the script itself (`reexec`, the __main__ block)
    starts itself again under the sleep inhibitor: a caller that imports this
    module and calls main() never execs a real batch in its place, and without
    the inhibitor its preflight refuses before any run."""
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--boots", type=sizes, default=list(BOOT_SERIES), metavar="N,N,...",
                    help="plain boots per series, the first of each cold (default "
                         f"{','.join(map(str, BOOT_SERIES))}; 0 for none)")
    ap.add_argument("--stack-pad", action=argparse.BooleanOptionalAction, default=True,
                    help="take the stack control, <boot-prefix>c-stackpad (default on)")
    ap.add_argument("--series", type=sizes, default=list(MH_SERIES), metavar="N,N,...",
                    help="Metal Harbor re-takes per series, the first of each cold (default "
                         f"{','.join(map(str, MH_SERIES))}; 0 for none)")
    ap.add_argument("--demo", action=argparse.BooleanOptionalAction, default=True,
                    help="take the attract demo (default on)")
    ap.add_argument("--recording", default=RECORDING,
                    help=f"the recording the series re-take (default {RECORDING})")
    ap.add_argument("--boot-prefix", default=BOOT_PREFIX,
                    help=f"the boots' name before their letter (default {BOOT_PREFIX})")
    ap.add_argument("--demo-name", default=DEMO_NAME,
                    help=f"the attract demo's capture name (default {DEMO_NAME})")
    ap.add_argument("--control-version", default=CONTROL_VERSION,
                    help=f"the device-profile control's names end in it: "
                         f"{CONTROL_PREFIX}off-<v> and {CONTROL_PREFIX}on-<v> (default "
                         f"{CONTROL_VERSION}); a top-up takes no control")
    ap.add_argument("--min-idle", type=float, default=MIN_IDLE, metavar="SECONDS",
                    help=f"how long the oracle sits idle before a series' first run (default "
                         f"{MIN_IDLE:g})")
    ap.add_argument("--max-load", type=float, default=MAX_LOAD,
                    help=f"launch only below this one-minute load (default {MAX_LOAD:g})")
    ap.add_argument("--max-wait", type=float, default=MAX_WAIT, metavar="SECONDS",
                    help=f"the longest wait for the load or the idle time before a launch is "
                         f"refused (default {MAX_WAIT:g})")
    ap.add_argument("--poll", type=float, default=POLL, metavar="SECONDS",
                    help=f"seconds between looks while waiting (default {POLL:g})")
    ap.add_argument("--after", type=Path, default=None, metavar="RECEIPT",
                    help="a top-up: hold every run to that earlier batch's references (its "
                         "Proton build, freeze, pin, device profile, control, installation "
                         "and probe builds)")
    ap.add_argument("--receipt", type=Path, default=None, metavar="JSON",
                    help="write the receipt here (default "
                         "private/generated/oracle/batches/retake-batch-<stamp>.json)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan, every call and the preflight; launch and write nothing")
    args = ap.parse_args(argv)
    if not all(math.isfinite(v) for v in (args.max_load, args.min_idle, args.max_wait,
                                          args.poll)):
        ap.error("--max-load, --min-idle, --max-wait and --poll must be finite: a NaN compares "
                 "false both ways and would lift the load and idle conditions")
    if args.max_load <= 0 or args.poll <= 0 or args.max_wait < 0:
        ap.error("--max-load and --poll must be positive, and --max-wait not negative")
    if args.max_load > MAX_LOAD or args.min_idle < MIN_IDLE:
        ap.error(f"--max-load above {MAX_LOAD:g} or --min-idle below {MIN_IDLE:g} would loosen "
                 "the batch's conditions (the G4 plan, section 3); only a stricter value is "
                 "taken")
    if not (args.boots or args.stack_pad or args.series or args.demo):
        ap.error("nothing to take")
    if not re.fullmatch(r"[a-z0-9]+", args.control_version):
        ap.error("--control-version: lower-case letters and digits only")
    if reexec and not args.dry_run and os.environ.get(INHIBITED_ENV) != "1":
        under_inhibitor(argv)            # in place of this process; returns only in tests
    return run_batch(args, argv)


if __name__ == "__main__":
    raise SystemExit(main(reexec=True))
