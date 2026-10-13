#!/usr/bin/env python3
"""arm64_replays: every registered replay run on arm64 Linux under qemu-user, compared with its
registered native captures, and each run's divergence labelled.

Subtitle: S7's second exit clause, "every replay's arm64 divergence labelled" (design 5, S7), and
the evidence of the arm64-linux target's `replays` clause (Documentation/s7-green-means.md): the
receipt Documentation/receipts/s7-arm64-replays-v1.json, which
Documentation/scripts/check_s7_exit.py's `arm64` predicate and tools/s7/target_receipt.py read.

The steps
---------
1. `run`: each replay's binary, linked for aarch64-linux-musl by tools/s7/arm64_link.py, run
   under the pinned qemu-aarch64 with tools/s4/tick_replay.py's argv and environment, which are
   read from the tools that make them, never typed: a stage entry's from
   tools/s4/stage_run.py `command`, exactly as tick_replay's run_stage_lifted calls it, and the
   boot entry's as tick_replay's run_lifted builds its tools/s4/loop_run.py spec. Every output
   is redirected under the work directory: the write overlay (reset as
   tools/oracle/save_state.py reset_lifted resets it, and required empty), the tick capture,
   the word log, the stage files, the import table and the run record. Two things differ from
   the x86-64 run, and the run record says both: the probe's wall-clock alarm (its
   `--timeout`, an alarm around WinMain, never an input of the game) is multiplied by
   `--alarm-scale`, since qemu is slower than the hardware; and SA2_F80_STATS names a file for
   the shims' transcendental counts. A run whose stop note is the alarm is refused, so the
   scaled bound can never be what a comparison reads. The same tool runs an x86-64 binary of
   the same sources natively (`--runner native`), the reference the cross-target comparison
   reads.
2. `compare`: tick_replay's own comparison, imported and called as tick_replay.replay calls it
   (word_native, word_compare; for a stage entry compare_trace and input_record; the goal
   verdict through tools/s4/stage_boot.py goal_evidence and goal_verdict), with every file it
   writes in a temporary directory under the work directory. Nothing in tick_replay's own
   output directories is read or written. The native side (word_native) holds every native
   repeat's stream in memory at once, which is a word-policy stage entry's whole peak: it is
   computed once per replay and read by the comparison of each run given (`--also-work`, the
   x86-64 reference's runs beside the arm64 ones), each comparison writing in a directory of its
   own. tick_replay's and wordlog's per-process caches are held read-once in this process
   (`read_once`): each log is read once here, and the kept copies were most of the peak. Run one
   comparison at a time.
   The comparison record carries the registry's sha256, and the receipt refuses one made under
   another registry.
3. `receipt`: the labels, recomputed from the comparison's result with tick_replay's own
   functions (m5_record and identity_problems, as tools/s7/target_receipt.py derives a target's
   reading), never copied from a label the comparison wrote; the transcendental sites from the
   shims' per-site counts, read back to the lifted function holding each call through the
   binary's own symbol table; and the divergence's magnitude: the words and fields that differ
   from the native capture, and, when an x86-64 run of the same sources is given, every word the
   arm64 log differs from it on, with the ulp distance where both words read as floats.

A label is "no divergence" when the reading has neither a residue nor a divergent tick,
"residues only" when every divergence is a recorded residue class, and "divergence" otherwise,
which no predicate accepts. The first divergent tick of a "residues only" run is the literal
first divergence the classes absorbed (m5_record's `residues_from`, design 8's M5 tick).

What it writes: under --work (default private/generated/s7/arm64-replays), run.rec and
compare.rec per replay, never a `.json` (tools/s4/m7_inputs.py counts every *.json under
private/generated as a possible game-run record); and with `receipt`, the tracked receipt.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s7/arm64_replays.py run --build <arm64_link out> --work <dir> [--replay boot]
  .venv/bin/python tools/s7/arm64_replays.py run --build <x86-64 link out> --work <dir> --runner native
  .venv/bin/python tools/s7/arm64_replays.py compare --work <dir> [--also-work <x86-64 work>] [--replay boot]
  .venv/bin/python tools/s7/arm64_replays.py receipt --work <dir> [--reference <x86-64 work>] [--out PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def _root() -> Path:
    """The repository: the nearest directory above this file holding AGENTS.md and
    tools/pins.json (this file's grandparent in the tree; a snapshot's copy finds the tree)."""
    for p in Path(__file__).resolve().parents:
        if (p / "AGENTS.md").is_file() and (p / "tools" / "pins.json").is_file():
            return p
    raise SystemExit(f"no repository above {__file__}")


ROOT = _root()
SELF = "tools/s7/arm64_replays.py"
SCHEMA = "sa2-s7-arm64-replays/v1"
RECEIPT = "Documentation/receipts/s7-arm64-replays-v1.json"
REGISTRY = "Documentation/replays.json"
RESIDUES = "Documentation/receipts/residues"
TARGET = "arm64-linux"
TRIPLE = "aarch64-linux-musl"
WORK = ROOT / "private" / "generated" / "s7" / "arm64-replays"
QEMU = ROOT / "private" / "predecessor-tools" / "qemu" / "root" / "usr" / "bin" / "qemu-aarch64"
QEMU_PACKAGE = (ROOT / "private" / "predecessor-tools" / "qemu" / "deb"
                / "qemu-user_1%3a10.0.13+ds-0+deb13u1_amd64.deb")
QEMU_PIN = "qemu-user"            # its key under tools/pins.json's "tools", when pinned
PINS = "tools/pins.json"
LINK_REC = "link.rec"
RUN_REC = "run.rec"
COMPARE_REC = "compare.rec"
STATS = "f80-stats.rec"
RUNNERS = ("qemu", "native")
ALARM_NOTE = "exceeded its wall-clock budget"   # runtime/sa2_trap.c on_alarm
ALARM_SCALE = 30                  # the probe's alarm under qemu, as a multiple of tick_replay's
PROBE_DEFAULT_ALARM = 120         # harness/crt_probe.c's --timeout when none is passed
LABELS = ("no divergence", "residues only", "divergence")
# The files whose code makes the receipt: this tool, the link tool, the target passes, and the
# x86-64 tools whose argv, environment and comparison the runs and the readings are.
PIPELINE = (SELF, "tools/s7/arm64_link.py", "tools/s7/cross_build.py",
            "tools/s7/f80_legalize.py", "tools/s7/f80_shims.c", "tools/s4/tick_replay.py",
            "tools/s4/stage_run.py", "tools/s4/stage_boot.py", "tools/s4/loop_run.py",
            "tools/s4/reach_receipt.py", "tools/s4/wordlog.py", "tools/s4/device_profile.py",
            "tools/oracle/save_state.py")
WORDS_LISTED = 32                 # cross-target differing words a replay's record lists


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(path: Path | str) -> str:
    path = Path(path)
    return str(path.relative_to(ROOT)) if path.is_absolute() and path.is_relative_to(ROOT) \
        else str(path)


def pinned(path: Path) -> dict:
    return {"path": rel(path), "sha256": sha256(path)}


def pipeline_used() -> dict:
    """Each PIPELINE file as this process uses it, by path and sha256: this tool's neighbours
    from beside this file (a snapshot's copy when the run is a snapshot's), the rest from the
    tree."""
    here = Path(__file__).resolve().parent
    out = {}
    for f in PIPELINE:
        p = here / Path(f).name if f.startswith("tools/s7/") else ROOT / f
        out[f] = pinned(p) if p.is_file() else None
    return out


def tools(work: Path):
    """tick_replay and its neighbours, imported with the word-log reader's builds and its
    decompressed logs kept under `work`: tools/s4/wordlog.py reads SA2_WORDLOG_BUILD when it is
    first imported, and reads its BUILD at every call, so a process that imported it earlier
    (a test session) is pointed there too, never at the shared private/generated/s4/wordlog."""
    os.environ["SA2_WORDLOG_BUILD"] = str(work / "wordlog-build")
    for sub in ("tools/s4", "tools/s3/whole_image", "tools/oracle"):
        if str(ROOT / sub) not in sys.path:
            sys.path.insert(0, str(ROOT / sub))
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import device_profile as DP  # noqa: PLC0415
    import loop_run as LR  # noqa: PLC0415
    import reach_receipt as RR  # noqa: PLC0415
    import save_state as SS  # noqa: PLC0415
    import stage_boot as SB  # noqa: PLC0415
    import stage_run as SR  # noqa: PLC0415
    import tick_replay as T  # noqa: PLC0415
    import wordlog as WL  # noqa: PLC0415
    WL.BUILD = work / "wordlog-build"
    read_once(T, WL)
    return {"DP": DP, "LR": LR, "RR": RR, "SS": SS, "SB": SB, "SR": SR, "T": T, "WL": WL}


class _ReadOnce(dict):
    """A per-process cache that holds a value until it is read once: `key in`, a store and one
    read behave as a dict's, and the read drops the value."""

    def __getitem__(self, key):
        return self.pop(key)


class _Unkept(dict):
    """A per-process cache that keeps nothing: every lookup misses, so the value is computed."""

    def __setitem__(self, key, value) -> None:
        pass


# tick_replay's and wordlog's per-process caches, by module and name, and how this process holds
# each. They key a pure function's answer by its inputs' content, for the replay-results gate,
# which recomputes every replay once per falsification. This process reads each log once, so
# keeping them only adds copies: one native stream of Metal Harbor was held up to four times (the
# capture text, the parsed stream, its canonical form, the copy handed out) for each of its
# registered captures, and the comparison peaked near 19 GB. A store followed by one read
# (`_ReadOnce`) or no store at all (`_Unkept`, where the code returns the value it stores) gives
# the same values: a miss recomputes from the same bytes.
CACHES = (("T", "_STREAMS", _ReadOnce), ("T", "_CANONICAL", _ReadOnce), ("T", "_WORDS", _ReadOnce),
          ("WL", "_FIELDS", _ReadOnce), ("WL", "_CAPTURES", _Unkept), ("WL", "_DIFFS", _Unkept))


def read_once(T, WL) -> None:
    """tick_replay's and wordlog's caches (CACHES) as this process holds them; each must exist as
    a dict, so a renamed cache is refused rather than quietly kept."""
    mods = {"T": T, "WL": WL}
    for mod, name, kind in CACHES:
        have = getattr(mods[mod], name, None)
        if not isinstance(have, dict):
            raise SystemExit(f"{mods[mod].__name__}.{name} is not a dict: {SELF} holds that "
                             "cache read-once and needs it named again")
        if not isinstance(have, kind):
            setattr(mods[mod], name, kind())


def entries() -> dict[str, dict]:
    return {e["name"]: e for e in json.loads((ROOT / REGISTRY).read_text())["replays"]}


def qemu_runner() -> dict:
    """The runner as the receipt records it: qemu-user, its binary and package by sha256, and
    whether tools/pins.json pins it. A pin that names a hash is held to it: the binary to its
    `sha256`, the package to its `package_sha256`, each when the pin names one."""
    if not QEMU.is_file():
        raise SystemExit(f"{rel(QEMU)} is absent")
    pins = json.loads((ROOT / PINS).read_text()).get("tools", {}).get(QEMU_PIN)
    binary = sha256(QEMU)
    package = sha256(QEMU_PACKAGE) if QEMU_PACKAGE.is_file() else None
    p = subprocess.run([str(QEMU), "--version"], capture_output=True, text=True)
    version = (p.stdout.splitlines() or [""])[0].strip()
    rec = {"kind": "qemu", "program": "qemu-user", "version": version, "headless": True,
           "binary": rel(QEMU), "binary_sha256": binary,
           "package": {"path": rel(QEMU_PACKAGE), "sha256": package},
           "pin": {"file": PINS, "key": f"tools.{QEMU_PIN}", "pinned": pins is not None}}
    if pins is not None:
        if not isinstance(pins, dict) or not (pins.get("sha256") or pins.get("package_sha256")):
            raise SystemExit(f"{PINS} tools.{QEMU_PIN} names no sha256 to hold qemu to")
        if pins.get("sha256") and binary != pins["sha256"]:
            raise SystemExit(f"{rel(QEMU)} is not the qemu {PINS} pins (tools.{QEMU_PIN}.sha256)")
        if pins.get("package_sha256") and package != pins["package_sha256"]:
            raise SystemExit(f"{rel(QEMU_PACKAGE)} is not the package {PINS} pins "
                             f"(tools.{QEMU_PIN}.package_sha256)")
    return rec


# ------------------------------------------------------------------ run


def parse_loop_cmd(cmd: list[str]) -> dict:
    """What a tools/s4/loop_run.py command runs: its one --run (label and settings), the probe's
    arguments, its process bound, and its build's main, hooks and wraps."""
    out = {"label": None, "env": {}, "probe_args": [], "timeout": 600, "main": None,
           "hooks": [], "wraps": []}
    runs, i = [], 0
    while i < len(cmd):
        a = str(cmd[i])
        if a.startswith("--probe-arg="):
            out["probe_args"].append(a.split("=", 1)[1])
        elif a in ("--run", "--timeout", "--main", "--hook", "--wrap") and i + 1 < len(cmd):
            v = str(cmd[i + 1])
            i += 1
            if a == "--run":
                runs.append(v)
            elif a == "--timeout":
                out["timeout"] = int(v)
            elif a == "--main":
                out["main"] = v
            else:
                out[a[2:] + "s"].append(v)
        i += 1
    if len(runs) != 1:
        raise SystemExit(f"a replay's loop_run command carries {len(runs)} --run, not one")
    parts = runs[0].split("|")
    out["label"] = parts[0]
    for kv in parts[1:]:
        k, _, v = kv.partition("=")
        out["env"][k] = v
    return out


def loop_command(t: dict, entry: dict, out: Path) -> tuple[list[str], dict]:
    """The tools/s4/loop_run.py command tick_replay's run of `entry` uses, with its observers
    under `out`, and what of the entry it read: a stage entry's from stage_run.command as
    run_stage_lifted calls it; the boot entry's as run_lifted builds it."""
    T, SR, RR, DP = t["T"], t["SR"], t["RR"], t["DP"]
    name = entry["name"]
    profile = T.native_device_profile(entry)
    words = out / "lifted-words.bin"
    if T.is_stage(entry):
        script = ROOT / entry["input"]["script"]
        if sha256(script) != entry["input"]["sha256"]:
            raise SystemExit(f"{name}: its input {entry['input']['script']} does not hash to its pin")
        polls = T.stage_poll_budget(entry)
        alarm = max(600, polls // 5)       # tick_replay.run_stage_lifted's
        watch = {"SA2_STAGE_TRACE": rel(out / "stage-trace.csv")}
        if "word_policy" in entry:
            watch["SA2_REPLAY_WORDS"] = rel(words)
        cmd, _run_json, _ticks = SR.command(
            entry["stage"]["name"], polls, label="ticks", input_script=script, extra_env=watch,
            timeout=alarm, device_profile=profile, name=f"replay-{name}", out_dir=out,
            observe=True)
        cmd += ["--probe-arg=--dump-imports", f"--probe-arg={rel(out / 'imports.table.rec')}"]
        return [str(c) for c in cmd], {"profile": profile, "polls": polls, "stage": True}
    # run_lifted: RR.ENV, the tick capture, the word log for a word-policy entry, the profile.
    env = dict(RR.ENV)
    env["SA2_REPLAY_TICKS"] = rel(out / "lifted.csv")
    if "word_policy" in entry:
        env["SA2_REPLAY_WORDS"] = rel(words)
    env.update(RR.profile_env(profile))
    spec = "|".join(["ticks"] + [f"{k}={v}" for k, v in env.items()])
    cmd = [str(T.PY), str(T.HERE / "loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN,
           "--name", T.BUILD, "--hook", T.HOOK, "--wrap", T.WRAP,
           "--out-prefix", f"replay-{name}", "--run", spec]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    return cmd, {"profile": profile, "stage": False}


def scaled_probe_args(args: list[str], scale: int) -> tuple[list[str], dict]:
    """The probe's arguments with its wall-clock alarm multiplied by `scale` (added at the
    probe's default when the command passes none)."""
    out, alarm, i = [], None, 0
    while i < len(args):
        if args[i] == "--timeout" and i + 1 < len(args):
            alarm = int(args[i + 1], 0)
            i += 2
            continue
        out.append(args[i])
        i += 1
    given = alarm if alarm is not None else PROBE_DEFAULT_ALARM
    ran = given * scale if given else 0
    return out + ["--timeout", str(ran)], {"tick_replay": given, "ran": ran, "scale": scale,
                                           "passed_by_tick_replay": alarm is not None}


def run_one(t: dict, name: str, build: Path, work: Path, runner: str, scale: int) -> dict:
    T, LR, RR, SS, DP = t["T"], t["LR"], t["RR"], t["SS"], t["DP"]
    entry = entries()[name]
    out = work / name
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty: give each run a directory of its own")
    out.mkdir(parents=True, exist_ok=True)
    link = build / "bin" / name / LINK_REC
    linkrec = json.loads(link.read_text())
    binary = ROOT / linkrec["binary"]
    if sha256(binary) != linkrec["binary_sha256"]:
        raise SystemExit(f"{binary} is not the binary its link record names")
    cmd, read = loop_command(t, entry, out)
    plan = parse_loop_cmd(cmd)
    vfs = out / "vfsroot"
    vfs.mkdir()
    spec_env = dict(plan["env"], SA2_VFS_WRITE_ROOT=rel(vfs))
    # The overlay this run reads, reset as tick_replay resets the shared one, and held empty.
    saves = SS.reset_lifted(["--run", "|".join([plan["label"]] + [f"{k}={v}" for k, v in
                                                                   spec_env.items()])], ROOT)
    SS.require_empty(saves)
    env = {k: v for k, v in RR.clean_environ().items()
           if k != getattr(LR, "EXE_KIND_VAR", "SA2_EXE_KIND")}
    env.update(LR.BASE_ENV)
    env.update(spec_env)
    stats = out / STATS
    env["SA2_F80_STATS"] = str(stats)
    probe_args, alarm = scaled_probe_args(plan["probe_args"], scale if runner == "qemu" else 1)
    runrec = out / "probe.runrec"
    argv = ([str(QEMU)] if runner == "qemu" else []) + [str(binary), str(t["T"].C.EXE),
                                                         "--json", str(runrec)] + probe_args
    t0 = time.time()
    p = subprocess.Popen(argv, cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                         stderr=open(out / "stderr.txt", "w"))
    bound = max(plan["timeout"], alarm["ran"]) + 900
    try:
        rc = p.wait(timeout=bound)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise SystemExit(f"{name}: the run outlived its bound of {bound} s and was killed")
    seconds = round(time.time() - t0, 2)
    if rc or not runrec.is_file():
        raise SystemExit(f"{name}: the run did not complete (rc {rc}); see {out / 'stderr.txt'}")
    d = json.loads(runrec.read_text())
    stop = T.stop_note(d)
    if ALARM_NOTE in stop:
        raise SystemExit(f"{name}: the run ended at the probe's alarm ({stop[:200]}), not at its "
                         "own end: raise --alarm-scale")
    try:
        device = DP.check_run(d, read["profile"])
    except DP.Refusal as e:
        raise SystemExit(f"{name}: the run is not the one asked for: {e}") from None
    off = RR.switch_problems(d, {**RR.ENV, **RR.profile_env(read["profile"])})
    if off:
        raise SystemExit(f"{name}: its devices are not its configuration's: {'; '.join(off)}")
    capture = out / "lifted.csv"
    if not capture.is_file():
        raise SystemExit(f"{name}: the run wrote no tick capture")
    lifted = {"device_profile": device, "guest_paths": DP.run_guest(d), "saves": saves,
              "capture": rel(capture), "sha256": sha256(capture),
              "binary_sha256": linkrec["binary_sha256"], "run_json": rel(runrec),
              "reach": T.reach_of(d), "poll_handoff": T.poll_counters(d),
              "stop": {"note": stop, "exit_pc": (d.get("trap") or {}).get("exit_pc"),
                       "esp": (d.get("trap") or {}).get("esp"),
                       "stack": (d.get("trap") or {}).get("stack", [])}}
    if "word_policy" in entry:
        lifted["words"] = T.keep_lifted_words(capture, out / "lifted-words.bin")
    if read["stage"]:
        for key, f in (("goal_latch", "goal-latch.csv"), ("stage_trace", "stage-trace.csv"),
                       ("input_applied", "input-applied.csv")):
            if not (out / f).is_file():
                raise SystemExit(f"{name}: the observed run wrote no {f}")
            lifted[key] = pinned(out / f)
        table = out / "imports.table.rec"
        lifted["import_table"] = pinned(table)
        lifted["polls"] = read["polls"]
    rec = {"schema": "sa2-s7-arm64-run/v1", "generated_by": SELF, "replay": name,
           "runner": qemu_runner() if runner == "qemu" else
           {"kind": "native", "machine": os.uname().machine},
           "link": pinned(link), "binary": pinned(binary), "triple": linkrec["triple"],
           "argv": [rel(a) for a in argv], "env": {k: env[k] for k in sorted(env)
                                                   if k.startswith("SA2_")},
           "loop_run": {k: plan[k] for k in ("label", "main", "hooks", "wraps", "timeout")},
           "probe_alarm": alarm, "returncode": rc, "seconds": seconds,
           "started": datetime.fromtimestamp(t0, timezone.utc).isoformat(timespec="seconds"),
           "lifted": lifted,
           "f80_stats": pinned(stats) if stats.is_file() else None,
           "tools": pipeline_used()}
    (out / RUN_REC).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"replay": name, "runner": runner, "seconds": seconds, "rc": rc,
                      "stop": stop[:160], "capture": lifted["sha256"]}), flush=True)
    return rec


# ------------------------------------------------------------------ compare


def run_files(name: str, work: Path) -> dict:
    """A run's kept files as its run record names them, each held to the sha256 it recorded."""
    lifted = json.loads((work / name / RUN_REC).read_text())["lifted"]
    for key in ("words", "stage_trace", "goal_latch", "input_applied"):
        f = lifted.get(key)
        if f and sha256(ROOT / f["path"]) != f["sha256"]:
            raise SystemExit(f"{name}: {f['path']} changed since its run was recorded")
    if sha256(ROOT / lifted["capture"]) != lifted["sha256"]:
        raise SystemExit(f"{name}: {lifted['capture']} changed since its run was recorded")
    return lifted


def compare_one(t: dict, name: str, works: list[Path]) -> list[dict]:
    """tick_replay's comparison of one replay's runs, one per work directory, against the
    registered native captures, as tick_replay.replay makes it, from each run's kept files; and
    the goal verdict. The native side (tick_replay.word_native, or native_repeats) is computed
    once and read by every run's comparison: it is the same for each, and for a word-policy stage
    entry it is the comparison's whole memory peak, every native repeat's stream at once. Each
    run's comparison writes its own files in a directory of its own."""
    T, SB = t["T"], t["SB"]
    entry = entries()[name]
    lifteds = [run_files(name, work) for work in works]
    T.residue_classes(entry)
    cap = ROOT / entry["native"]["capture"]
    if sha256(cap) != entry["native"]["sha256"]:
        raise SystemExit(f"{entry['native']['capture']} does not hash to the registry's pin")
    probe = T.native_probe_current(entry)
    header, native = T.read_capture(cap)
    t0 = time.time()
    scratch = works[0] / name / "compare-tmp"
    scratch.mkdir(exist_ok=True)
    recs = []
    with tempfile.TemporaryDirectory(dir=scratch) as td:
        ctx = T.word_native(entry, Path(td)) if "word_policy" in entry else None
        det = None if ctx else T.native_repeats(entry, header, native, Path(td))
        native_seconds = time.time() - t0
        for k, (work, lifted) in enumerate(zip(works, lifteds)):
            t1 = time.time()
            own = Path(td) / f"run{k}"
            own.mkdir()
            stop = (lifted.get("stop") or {}).get("note", "")
            if ctx is not None:
                result = T.word_compare(entry, ctx, lifted, own)
            else:
                lheader, lrows = T.read_capture(ROOT / lifted["capture"])
                result = T.compare(header, native, lheader, lrows, entry.get("field_policy", {}),
                                   T.capture_labels(entry), own, judged=det["judged_ticks"],
                                   name=name, lifted_stop=stop)
            goal = None
            if T.is_stage(entry):
                result["trace"] = T.compare_trace(entry, ROOT / lifted["stage_trace"]["path"],
                                                  own, lifted_stop=stop)
                inp = T.input_record(entry, ROOT / lifted["input_applied"]["path"], len(native))
                if inp["problems"]:
                    raise SystemExit(f"{name}: the run in {rel(work)} did not replay the "
                                     f"recording: {'; '.join(inp['problems'])}")
                lifted["input"] = inp
                verdict = SB.goal_verdict(SB.goal_evidence(entry, {"lifted": lifted}), ROOT)
                goal = verdict.get("complete") is True
            rec = {"schema": "sa2-s7-arm64-compare/v1", "generated_by": SELF, "replay": name,
                   "run": pinned(work / name / RUN_REC), "native_probe": probe,
                   "registry_sha256": sha256(ROOT / REGISTRY),
                   "native_capture": {"path": entry["native"]["capture"],
                                      "sha256": entry["native"]["sha256"]},
                   "native_side": {"seconds": round(native_seconds, 1),
                                   "shared_with": [rel(w / name / RUN_REC) for w in works]},
                   "result": result, "goal_complete": goal,
                   "seconds": round(time.time() - t1, 1), "tools": pipeline_used()}
            (work / name / COMPARE_REC).write_text(json.dumps(rec, indent=1, sort_keys=True)
                                                   + "\n")
            m5 = T.m5_record({"name": name, "result": result})
            print(json.dumps({"replay": name, "work": rel(work), "m5": m5, "goal_complete": goal,
                              "seconds": rec["seconds"], "native_seconds": round(native_seconds, 1)}),
                  flush=True)
            recs.append(rec)
    return recs


# ------------------------------------------------------------------ labels and magnitude


def label(rec: dict, identity: list[str]) -> tuple[str, list[str], int | None]:
    """(label, residues, first divergent tick) of one reading, from its M5 record
    (tick_replay.m5_record) and the identity rule's problems for it."""
    residues = list(rec.get("residues") or [])
    tick = rec.get("first_divergent_tick")
    if identity or tick is not None:
        return "divergence", residues, tick
    if not residues:
        return "no divergence", [], None
    return "residues only", residues, rec.get("residues_from")


def f32(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word & 0xFFFFFFFF))[0]


def ulp_distance(a: int, b: int) -> int | None:
    """The distance in float32 units in the last place between two words read as floats: the
    count of representable floats between them, across zero; None when either is a NaN or an
    infinity."""
    fa, fb = f32(a), f32(b)
    if not (math.isfinite(fa) and math.isfinite(fb)):
        return None

    def ordered(w: int) -> int:
        w &= 0xFFFFFFFF
        return -(w & 0x7FFFFFFF) if w & 0x80000000 else w
    return abs(ordered(a) - ordered(b))


def word_kind(a: int, b: int) -> str:
    fa, fb = f32(a), f32(b)
    if math.isnan(fa) and math.isnan(fb):
        return "nan sign" if (a ^ b) == 0x80000000 else "nan payload"
    if math.isnan(fa) or math.isnan(fb):
        return "nan against a number"
    return "number"


def cross_target(t: dict, name: str, work: Path, reference: Path | None) -> dict | None:
    """Every word the arm64 run's log differs from the x86-64 run's on, at any tick, from
    tools/s4/wordlog.py diff, with each word's float32 reading: the arm64 divergence alone,
    since both runs share every host residue. None without a reference run."""
    if reference is None:
        return None
    WL = t["WL"]
    mine = json.loads((work / name / RUN_REC).read_text())
    ref = json.loads((reference / name / RUN_REC).read_text())
    if ref["runner"].get("kind") != "native" or not str(ref.get("triple", "")).startswith("x86_64-"):
        raise SystemExit(f"{name}: the reference run is not a native x86-64 run")
    sources = same_sources(ROOT / mine["link"]["path"], ROOT / ref["link"]["path"])
    a, b = mine["lifted"].get("words"), ref["lifted"].get("words")
    if not a or not b:
        return {"compared": False, "why": "a run kept no word log"}
    for f in (a, b):
        if sha256(ROOT / f["path"]) != f["sha256"]:
            raise SystemExit(f"{f['path']} changed since its run was recorded")
    rows, meta = WL.diff(ROOT / b["path"], ROOT / a["path"])
    words = []
    for r in rows:
        x86, arm = r["a"], r["b"]
        words.append({"va": f"0x{r['va']:08x}", "first_tick": r["first_tick"],
                      "last_tick": r["last_tick"], "ticks_differing": r["ticks_differing"],
                      "x86_64": f"0x{x86:08x}", "arm64": f"0x{arm:08x}",
                      "kind": word_kind(x86, arm), "ulp": ulp_distance(x86, arm)})
    kinds: dict[str, int] = {}
    for w in words:
        kinds[w["kind"]] = kinds.get(w["kind"], 0) + 1
    ulps = [w["ulp"] for w in words if w["ulp"] is not None]
    return {"compared": True, "reference_run": pinned(reference / name / RUN_REC),
            "reference_binary_sha256": ref["binary"]["sha256"], "same_sources": sources,
            "residue_groups": residue_groups(work / name / COMPARE_REC,
                                             reference / name / COMPARE_REC),
            "compared_through_tick": meta.get("compared_through"),
            "words_differing": len(words), "kinds": kinds,
            "max_ulp": max(ulps) if ulps else None,
            "first_tick": min((w["first_tick"] for w in words), default=None),
            "files_equal": files_equal(mine["lifted"], ref["lifted"]),
            "words": sorted(words, key=lambda w: (w["first_tick"], w["va"]))[:WORDS_LISTED]}


# Each file of a run that tick_replay's comparison reads besides the word log, by how the run
# record names its digest: the tick capture, and a stage replay's trace, goal latch and applied
# input. The word log is held word by word instead (cross_target, tools/s4/wordlog.py diff):
# its bytes also carry the host clock's readings at every tick, which differ between any two
# runs, so two logs equal in every word still differ as files.
COMPARED_FILES = (("tick capture", lambda l: l.get("sha256")),
                  ("stage trace", lambda l: (l.get("stage_trace") or {}).get("sha256")),
                  ("goal latch", lambda l: (l.get("goal_latch") or {}).get("sha256")),
                  ("applied input", lambda l: (l.get("input_applied") or {}).get("sha256")))


def files_equal(mine: dict, ref: dict) -> dict[str, bool]:
    """Each COMPARED_FILES file, the arm64 run's against the x86-64 run's, equal by sha256; a
    file neither run keeps is left out, one only one run keeps reads unequal."""
    out = {}
    for what, get in COMPARED_FILES:
        a, b = get(mine), get(ref)
        if a is None and b is None:
            continue
        out[what] = a is not None and a == b
    return out


def same_sources(mine: Path, ref: Path) -> dict:
    """Both links' runtime and harness compiled from the same files: each link record's
    compiled sources by name with their sha256, required equal. Refused when they are not, so
    a cross-target difference is never one of the sources."""
    for p in (mine, ref):
        if not p.is_file():
            raise SystemExit(f"{p} is absent")
    a = json.loads(mine.read_text())["compile"]["objects"]
    b = json.loads(ref.read_text())["compile"]["objects"]
    sa = {k: v["source_sha256"] for k, v in a.items()}
    sb = {k: v["source_sha256"] for k, v in b.items()}
    if sa != sb:
        differ = sorted(k for k in set(sa) | set(sb) if sa.get(k) != sb.get(k))
        raise SystemExit(f"the x86-64 reference was not linked from the same sources: {differ[:5]}")
    return {"sources": len(sa), "digest": hashlib.sha256(
        json.dumps(sa, sort_keys=True).encode()).hexdigest()}


def residue_groups(mine: Path, ref: Path) -> list[dict] | None:
    """Each residue group's reading in the arm64 comparison beside the x86-64 run's of the same
    sources: the words that differ from the native capture, from which tick, and the listing's
    sha256 (tick_replay's own digest of every differing word, its tick and both values). None
    when the reference has no comparison."""
    if not ref.is_file():
        return None

    def groups(p: Path) -> dict:
        res = (json.loads(p.read_text())["result"].get("residues") or {})
        return {(g.get("record"), g.get("group")): g for g in res.get("groups") or []}
    a, b = groups(mine), groups(ref)
    out = []
    for key in sorted(set(a) | set(b), key=lambda k: (str(k[0]), str(k[1]))):
        row = {"record": key[0], "group": key[1]}
        for side, g in (("arm64", a.get(key)), ("x86_64", b.get(key))):
            lifted = (g or {}).get("lifted") or {}
            row[side] = None if g is None else {
                "differing": lifted.get("differing"),
                "first_tick": (lifted.get("first") or {}).get("tick"),
                "listing_sha256": lifted.get("listing_sha256"),
                "rule_held": (g.get("rule") or {}).get("held")}
        row["equal"] = row["arm64"] == row["x86_64"]
        out.append(row)
    return out


class Elf:
    """An ELF64 little-endian file's function symbols, for reading a return address back to
    the function that holds it."""

    def __init__(self, path: Path) -> None:
        data = path.read_bytes()
        if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
            raise SystemExit(f"{path} is not an ELF64 little-endian file")
        shoff = int.from_bytes(data[0x28:0x30], "little")
        size = int.from_bytes(data[0x3A:0x3C], "little")
        count = int.from_bytes(data[0x3C:0x3E], "little")
        secs = [data[shoff + i * size: shoff + (i + 1) * size] for i in range(count)]
        self.funcs: list[tuple[int, int, str]] = []
        for sh in secs:
            if int.from_bytes(sh[4:8], "little") != 2:          # SHT_SYMTAB
                continue
            off, sz = int.from_bytes(sh[24:32], "little"), int.from_bytes(sh[32:40], "little")
            strs = secs[int.from_bytes(sh[40:44], "little")]
            soff = int.from_bytes(strs[24:32], "little")
            for k in range(off, off + sz, 24):
                info, value = data[k + 4], int.from_bytes(data[k + 8:k + 16], "little")
                fsize = int.from_bytes(data[k + 16:k + 24], "little")
                if info & 0xF != 2 or not value:                # STT_FUNC
                    continue
                name_at = soff + int.from_bytes(data[k:k + 4], "little")
                name = data[name_at:data.index(b"\0", name_at)].decode()
                self.funcs.append((value, fsize, name))
        self.funcs.sort()
        self.by_name = {n: v for v, _, n in self.funcs}
        # Every executable section's code words, by address: (address, file offset, size).
        self.code = [(int.from_bytes(sh[16:24], "little"), int.from_bytes(sh[24:32], "little"),
                      int.from_bytes(sh[32:40], "little")) for sh in secs
                     if int.from_bytes(sh[4:8], "little") == 1                 # SHT_PROGBITS
                     and int.from_bytes(sh[8:16], "little") & 0x4]             # SHF_EXECINSTR
        self.data = data

    def fma_functions(self) -> dict:
        """The fused multiply-add words in the code (the encoding tools/s7/arm64_link.py
        fma_words counts), each by the function that holds it."""
        by: dict[str, int] = {}
        for addr, off, size in self.code:
            for k in range(off, off + size - 3, 4):
                if self.data[k + 3] == 0x1F:
                    held = self.holding(addr + k - off)
                    fn = held[0] if held else "(no function)"
                    by[fn] = by.get(fn, 0) + 1
        return dict(sorted(by.items()))

    def holding(self, addr: int) -> tuple[str, int] | None:
        lo, hi = 0, len(self.funcs)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.funcs[mid][0] <= addr:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            return None
        value, fsize, name = self.funcs[lo - 1]
        return (name, addr - value) if addr < value + max(fsize, 1) else None


def transcendentals(run: dict) -> dict:
    """The shims' transcendental counts for a run, each call site read back to the lifted
    function holding it (sub_<VA>, the guest function's address) through the binary's symbol
    table, related to the run's addresses by the anchor the shims report."""
    stats_ref = run.get("f80_stats")
    if not stats_ref:
        raise SystemExit(f"{run['replay']}: the run kept no shim counts ({STATS})")
    path = ROOT / stats_ref["path"]
    if sha256(path) != stats_ref["sha256"]:
        raise SystemExit(f"{path} changed since its run was recorded")
    stats = json.loads(path.read_text())
    if "sites" not in stats or "anchor" not in stats:
        raise SystemExit(f"{path} has no per-site counts: link the shims with -DSA2_F80_SITES")
    if stats.get("site_overflow"):
        raise SystemExit(f"{path}: {stats['site_overflow']} calls found no site slot")
    binary = ROOT / run["binary"]["path"]
    if sha256(binary) != run["binary"].get("sha256"):
        raise SystemExit(f"{run['replay']}: {run['binary']['path']} is not the binary it ran")
    elf = Elf(binary)
    anchor = stats["anchor"]
    slide = int(anchor["address"], 16) - elf.by_name[anchor["symbol"]]
    sites = []
    for s in stats["sites"]:
        ret = int(s["ret"], 16) - slide
        held = elf.holding(ret)
        fn, off = held if held else (None, None)
        guest = int(fn[4:], 16) if fn and fn.startswith("sub_") else None
        sites.append({"function": fn, "offset": off, "guest_va": f"0x{guest:08x}" if guest
                      is not None else None, "shim": s["fn"], "calls": s["calls"]})
    sites.sort(key=lambda s: (s["guest_va"] or "", s["offset"] or 0, s["shim"]))
    calls = {k: v for k, v in stats["calls"].items() if v}
    static = static_branches(run)
    if static["tail_calls"]:
        raise SystemExit(f"{run['replay']}: the lifted objects tail-call a transcendental shim "
                         f"{static['tail_calls']} times, and a tail call's site would be read "
                         "back to its caller's caller")
    total = sum(s["calls"] for s in sites)
    if total != sum(stats["calls"].values()):
        raise SystemExit(f"{path}: the sites count {total} calls and the shims "
                         f"{sum(stats['calls'].values())}")
    return {"stats": stats_ref, "semantics": stats.get("semantics"), "calls": calls,
            "sites": sites, "site_count": len(sites), "static": static,
            "f80_ops": {k: v for k, v in (stats.get("ops") or {}).items() if v},
            "fma": fma_reading(elf, run)}


def fma_reading(elf: Elf, run: dict) -> dict:
    """The fused multiply-adds in the binary the run ran, by the function holding each, beside
    the link record's count of them by object group (tools/s7/arm64_link.py fma_words), whose
    count for the whole binary must be the same: where AArch64 rounds a*b+c once and x86-64
    rounds it twice, and whether any of it is the lifted code's, the runtime's or the shims'."""
    groups = json.loads((ROOT / run["link"]["path"]).read_text()).get("fma_words")
    by = elf.fma_functions()
    words = sum(by.values())
    if isinstance(groups, dict) and groups.get("binary") is not None \
            and groups["binary"] != words:
        raise SystemExit(f"{run['replay']}: the binary holds {words} fused multiply-add words, "
                         f"and its link record counts {groups['binary']}")
    return {"words": words, "functions": by, "by_group": groups}


def static_branches(run: dict) -> dict:
    """The transcendental shims' call sites in the lifted objects the run's binary was linked
    from: the modules tools/s7/cross_build.py's log records a transcendental call in, and each
    one's branches to a shim, calls and tail calls, from its relocations
    (tools/s7/arm64_link.py shim_branches)."""
    import arm64_link as A  # noqa: PLC0415  beside this file
    link_ref = run.get("link") or {}
    link = ROOT / str(link_ref.get("path") or "")
    if not link.is_file() or sha256(link) != link_ref.get("sha256"):
        raise SystemExit(f"{run['replay']}: its link record {link_ref.get('path')} is missing or "
                         "moved since the run")
    lifted = json.loads(link.read_text())["lifted"]
    log = ROOT / lifted["log"]
    if sha256(log) != lifted["log_sha256"]:
        raise SystemExit(f"{lifted['log']} moved since the link")
    by_shim: dict[str, dict[str, int]] = {}
    modules = 0
    for line in log.read_text().splitlines():
        r = json.loads(line) if line.strip() else {}
        if not r.get("transcendental"):
            continue
        modules += 1
        for name, row in A.shim_branches(log.parent / r["object"]).items():
            got = by_shim.setdefault(name, {"call": 0, "tail": 0})
            got["call"] += row["call"]
            got["tail"] += row["tail"]
    return {"modules": modules, "by_shim": dict(sorted(by_shim.items())),
            "calls": sum(r["call"] for r in by_shim.values()),
            "tail_calls": sum(r["tail"] for r in by_shim.values())}


def rule_bound(rule: dict | None) -> str:
    """By how much a residue group's words differ, as the group's rule measured it
    (tick_replay.evaluate_rule): `zero`, the run holds zero in every listed word; `below`, each
    limited word's highest value against its limit; `native`, the run held to the native
    capture's own form; `none`, no bound. A broken rule says where it broke."""
    rule = rule or {}
    kind = rule.get("kind")
    if rule.get("held") is False:
        b = rule.get("broken") or {}
        return f"its {kind} rule broken at tick {b.get('tick')} ({b.get('va')} = {b.get('value')})"
    if kind == "zero":
        return "zero in the run in every listed word, where the capture holds the host's values"
    if kind == "below":
        limits, top = rule.get("limits") or {}, rule.get("max") or {}
        return "each below its limit: " + ", ".join(
            f"{va} at most {int(top[va], 16)} of {limits[va]}" for va in sorted(limits)
            if va in top)
    if kind == "native":
        return f"held to the native capture's own {rule.get('form')} form"
    if kind == "none":
        return "no bound (the record's rule is none)"
    return f"rule {kind!r}"


def magnitude_text(name: str, lab: str, rec: dict, result: dict, cross: dict | None,
                   tr: dict) -> str:
    """One paragraph: what differs from the native capture and by how much, and what differs
    from the x86-64 run of the same sources."""
    parts = []
    groups = (result.get("residues") or {}).get("groups") or []
    if groups:
        parts.append("against the native capture, the residue groups' words differ: " + "; ".join(
            f"{g.get('group')} ({g.get('record')}) {((g.get('lifted') or {}).get('differing'))}"
            f" words from tick {(((g.get('lifted') or {}).get('first')) or {}).get('tick')},"
            f" {rule_bound(g.get('rule'))}"
            for g in groups))
    div = result.get("first_divergence")
    if div:
        parts.append(f"first unclassed divergence at tick {div.get('tick')} in {div.get('field')}"
                     f" ({div.get('kind')})")
    elif lab == "no divergence":
        parts.append("no compared word or field differs from the native capture")
    if cross is None:
        parts.append("no x86-64 run of the same sources was compared")
    elif not cross.get("compared"):
        parts.append(f"the x86-64 comparison was not made: {cross.get('why')}")
    elif cross["words_differing"] == 0:
        same = [k for k, v in cross["files_equal"].items() if v]
        other = [k for k, v in cross["files_equal"].items() if not v]
        parts.append(f"against the x86-64 run of the same sources, no word of the logged "
                     f"section differs at any of {cross['compared_through_tick']} ticks"
                     + (f"; byte-identical: {', '.join(same)}" if same else "")
                     + (f"; not identical: {', '.join(other)}" if other else ""))
    else:
        parts.append(f"against the x86-64 run of the same sources, {cross['words_differing']} "
                     f"words differ from tick {cross['first_tick']} ("
                     + ", ".join(f"{k} {v}" for k, v in sorted(cross["kinds"].items()))
                     + (f"; largest float distance {cross['max_ulp']} ulp" if cross["max_ulp"]
                        is not None else "") + ")")
    if cross and cross.get("compared") and cross.get("residue_groups") is not None:
        rg = cross["residue_groups"]
        unequal = [f"{g['group']}" for g in rg if not g["equal"]]
        parts.append(f"the {len(rg)} residue groups read " + (
            "exactly as in the x86-64 run (the same differing words, first ticks and listings)"
            if not unequal else f"differently from the x86-64 run in {', '.join(unequal)}"))
    elif cross and cross.get("compared"):
        parts.append("the x86-64 run was not itself compared with the native capture")
    parts.append(f"{tr['site_count']} transcendental call sites reached"
                 + (": " + ", ".join(f"{s['function']} {s['shim']} x{s['calls']}"
                                     for s in tr["sites"]) if tr["sites"] else ""))
    fma = tr.get("fma")
    if fma is not None:
        groups = {k: v for k, v in (fma.get("by_group") or {}).items() if k != "binary"}
        text = f"the binary holds {fma['words']} fused multiply-add words" + (
            ": " + ", ".join(f"{f} x{n}" for f, n in fma["functions"].items())
            if fma["functions"] else "")
        if groups and all(v == 0 for v in groups.values()):
            text += ", and its lifted objects, runtime, harness and shims hold none"
        elif groups:
            text += ", by object group " + ", ".join(f"{k} {v}" for k, v in sorted(groups.items()))
        parts.append(text)
    return f"{name}: " + "; ".join(parts) + "."


def receipt(t: dict, work: Path, reference: Path | None, names: list[str]) -> dict:
    T = t["T"]
    registry_bytes = (ROOT / REGISTRY).read_bytes()
    registry_doc = json.loads(registry_bytes)
    regs = entries()
    missing = [n for n in regs if n not in names]
    if missing:
        raise SystemExit(f"the receipt covers every registered replay; {missing} not given")
    compares = {n: json.loads((work / n / COMPARE_REC).read_text()) for n in names}
    runs = {n: json.loads((work / n / RUN_REC).read_text()) for n in names}
    registry_sha = hashlib.sha256(registry_bytes).hexdigest()
    runner = qemu_runner()
    for n in names:
        if sha256(work / n / RUN_REC) != compares[n]["run"]["sha256"]:
            raise SystemExit(f"{n}: its comparison is of another run record")
        if runs[n]["runner"].get("kind") != "qemu" or runs[n]["triple"] != TRIPLE:
            raise SystemExit(f"{n}: the run is not an {TRIPLE} run under qemu")
        if runs[n]["runner"].get("binary_sha256") != runner["binary_sha256"]:
            raise SystemExit(f"{n}: the run's qemu is not {rel(QEMU)} as it is now")
        if compares[n].get("registry_sha256") != registry_sha:
            raise SystemExit(f"{n}: its comparison was made under another {REGISTRY}: compare "
                             "again")
    try:
        ident = T.identity_problems({"replays": [{"name": n, "result": compares[n]["result"]}
                                                 for n in names]}, registry_doc, ROOT)
    except SystemExit as e:
        ident = [f"the identity rule cannot be checked: {e}"]
    loose = [p for p in ident if not any(p.startswith(f"{n}: ") for n in names)]
    replays = {}
    for n in names:
        result = compares[n]["result"]
        rec = T.m5_record({"name": n, "result": result})
        own = [p for p in ident if p.startswith(f"{n}: ")] + loose
        lab, residues, tick = label(rec, own)
        for rid in residues:
            path = ROOT / RESIDUES / f"{rid}.json"
            if not path.is_file():
                raise SystemExit(f"{n} names the residue {rid}, and {RESIDUES}/{rid}.json is not a "
                                 "record: write the class's record first")
            doc = json.loads(path.read_text())
            if doc.get("id") != rid or not isinstance(doc.get("class"), str):
                raise SystemExit(f"{RESIDUES}/{rid}.json is not the record of {rid}")
        tr = transcendentals(runs[n])
        cross = cross_target(t, n, work, reference)
        run = runs[n]
        replays[n] = {
            "result": lab, "residues": residues, "first_divergent_tick": tick,
            "transcendental_sites": tr["site_count"],
            "magnitude": magnitude_text(n, lab, rec, result, cross, tr),
            "m5": rec, "identity_problems": own,
            "binary_sha256": run["binary"]["sha256"], "tick_result": result,
            **({"goal_complete": compares[n]["goal_complete"]} if T.is_stage(regs[n]) else {}),
            "transcendentals": tr, "cross_target": cross,
            "run": {"record": pinned(work / n / RUN_REC), "compare": pinned(work / n / COMPARE_REC),
                    "link": run["link"], "argv": run["argv"], "env": run["env"],
                    "probe_alarm": run["probe_alarm"], "seconds": run["seconds"],
                    "started": run["started"], "stop": run["lifted"]["stop"]["note"],
                    "lifted_capture": {"path": run["lifted"]["capture"],
                                       "sha256": run["lifted"]["sha256"]},
                    "words": run["lifted"].get("words")}}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                            text=True).stdout.strip() or None
    return {"schema": SCHEMA, "generated_by": SELF, "target": TARGET, "triple": TRIPLE,
            "question": "Does every registered replay, run on arm64 Linux (an aarch64-linux-musl "
                        "build under qemu-user), read no divergence or residues only against "
                        "its registered native captures, and what are its divergence's "
                        "transcendental sites and magnitude?",
            "runner": runner, "registry_sha256": registry_sha,
            "replays": replays,
            "inputs": {"pipeline": {f: sha256(ROOT / f) for f in PIPELINE if (ROOT / f).is_file()}},
            "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "commit": commit}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd in ("run", "compare", "receipt"):
        p = sub.add_parser(cmd)
        p.add_argument("--work", default=str(WORK), help="where the runs and comparisons are")
        p.add_argument("--replay", action="append", default=[], help="default: every registered")
    r = sub.choices["run"]
    r.add_argument("--build", required=True, help="tools/s7/arm64_link.py's --out")
    r.add_argument("--runner", choices=RUNNERS, default="qemu")
    r.add_argument("--alarm-scale", type=int, default=ALARM_SCALE)
    sub.choices["compare"].add_argument(
        "--also-work", action="append", default=[],
        help="another work directory whose runs of the same replays are compared too, against "
             "the same native side computed once (e.g. the x86-64 reference's)")
    rc = sub.choices["receipt"]
    rc.add_argument("--reference", default=None, help="an x86-64 work directory of the same "
                    "sources, run with --runner native, for the cross-target comparison")
    rc.add_argument("--out", default=str(ROOT / RECEIPT))
    args = ap.parse_args(argv)
    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    t = tools(work)
    names = args.replay or list(entries())
    if args.cmd == "run":
        for n in names:
            run_one(t, n, Path(args.build).resolve(), work, args.runner, args.alarm_scale)
    elif args.cmd == "compare":
        also = [Path(w).resolve() for w in args.also_work]
        for n in names:
            compare_one(t, n, [work] + also)
    else:
        doc = receipt(t, work, Path(args.reference).resolve() if args.reference else None, names)
        out = Path(args.out)
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        print(f"-> {out}")
        for n, r in doc["replays"].items():
            print(f"{n}: {r['result']} {r['residues']} first {r['first_divergent_tick']} "
                  f"sites {r['transcendental_sites']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
