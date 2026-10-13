#!/usr/bin/env python3
"""wine_run: a registered replay's lifted run on the x86-64 Windows build, under Proton's Wine.

Subtitle: tools/s4/tick_replay.py's lifted run of a registered replay (run_lifted for a tick
entry, run_stage_lifted for a stage entry), with its argv and environment, for the binary
tools/s7/win_link.py links, run headless by Proton's Wine in a scratch WINEPREFIX inside
bwrap with Steam's tree bound read-only; then its capture compared with the registered lifted
capture, and its replay verdict recomputed, by tick_replay's own functions, imported and
called. Nothing is written outside --out.

What it holds the run to, as tick_replay does: the capture's device profile
(`native_device_profile`, `RR.profile_env`), the reach configuration (`RR.ENV`), the
environment with every device variable of the shell stripped (`RR.clean_environ`), an empty
write overlay (a fresh one in --out, which is what `SS.reset_lifted` and `SS.require_empty`
make of the shared one), the run's own record showing the profile and every switch
(`DP.check_run`, `RR.switch_problems`), and, for a word-policy entry, the word log checked
against the capture and kept (`keep_lifted_words`). A stage entry's settings are
tools/s4/stage_run.py `command`'s, called as run_stage_lifted calls it (the entry's stage, its
pinned input recording, its poll budget and alarm, the observers written into --out), and its
applied input is held to the recording (`input_record`). The binary must be the one
win_link.py linked for the entry's runner: its link.record names the binary's sha256, the
runner's hooks and its wraps. Two things differ from a Linux run, each by this host's nature
and each recorded: the binary is the Windows one, run as `wine <exe>`, and the paths handed to
it are those Wine maps to the same files (a relative path from the repository, which is Wine's
working directory on its Z: drive; an absolute one is on Z: too).

What it compares (the record's `compare`):
  capture  the run's lifted.csv against the registered lifted capture (the tick-replay
           receipt's entry, its file on disk held to the receipt's sha256): equal bytes, and
           tick_replay's `compare` of the two streams, the registered capture as the
           reference side, for the first differing tick and field when they are not. With
           --seconds the run is stopped there, as Documentation/scripts/fast_check.py stops
           Metal Harbor, and every complete row it wrote is compared with the registered
           capture's first rows (at least --min-ticks of them); the run writes no run record
           then, so nothing below is checked.
  words    for a word-policy entry, the kept word log against the registered one, as the
           stream the replay compares (tick_replay's `log_stream` under the entry's mask, with
           its identity words): every named field and digest column, every tick. The logs'
           own bytes are not compared: they carry the host's clock and block addresses, so
           two Linux runs' logs differ too. A registered log no longer at the receipt's pin
           (a refresh rewrote it) is recorded as not comparable, not as a difference.
  verdict  tick_replay's `word_native` and `word_compare` (or `compare` for an entry without
           a word policy) over the native captures and this run, in --out, and for a stage
           entry its `compare_trace` and the goal verdict (tools/s4/stage_boot.py
           goal_verdict), as tick_replay.replay makes them: the whole result is kept
           (`verdict.tick_result`), and held to the receipt's recorded result on the first
           divergence, the class, the residue groups and the M5 record (`m5_record`).

The words and the verdict run in a forked process whose private memory may grow by what the
machine has available when it starts, less --headroom-gb (default 8, the machine's memory
rule), and no further (`bounded`). tick_replay's comparison holds the streams it is comparing
and releases each native repeat's before it reads the next (about 1.2 GB for Metal Harbor,
where holding every repeat's stream at once took 18 GB); the bound is the guard. A comparison
that needs more ends as a problem in the record, never with the machine out of memory; one
that cannot start with a gigabyte above the headroom is not started. The record keeps the
bound, the memory available, and the process's peak resident size.

`--evidence RECORD...` runs nothing: from one whole run record per registered replay, each
ok, on the unpacked executable, under the registry as it stands, it writes the x86_64-windows
target's replay runs, `Documentation/receipts/s7-x86_64-windows-replays-v1.json` (schema
`sa2-s7-target-replays/v1`, Documentation/s7-green-means.md `replays`), which
tools/s7/target_receipt.py reads. Each run's `tick_result` is the record's recomputed result,
never a reading typed beside it.

Wine runs headless: DISPLAY and WAYLAND_DISPLAY are unset, so Wine opens no window, and the
game's devices are the runtime's recording stand-ins, which open none either. No input is
sent to anything: the boot replay has none, and a stage replay's recording is read by the
game's own input hook (harness/replay/wrap_input_replay.c). The prefix is created on first use
under --prefix (default private/generated/s7/wine/prefix), never Steam's; `wineserver -k` ends
it.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s7/wine_run.py --exe <win_link out>/sa2_main.exe \\
      --out private/generated/s7/win/run-boot [--replay boot] [--timeout 1800] \\
      [--exe-kind shipped] [--seconds 150 --min-ticks 3000] [--headroom-gb 8]
A stage replay (`--replay metalharbor`) needs a build linked with win_link.py --stage.
`--exe-kind shipped` hands the run the shipped sonic2app.exe with SA2_EXE_KIND=shipped, as
tools/s4/loop_run.py --exe shipped does (`exe_argv`), for a build linked with
win_link.py --exe-overlay: the release's configuration (the user's DRM ruling of 2026-10-11).
The run's own record must say the loader mapped the kind asked for (its `exe.kind`), and the
capture is compared with the same registered one, which is the unpacked executable's.
Prints the comparison; writes <out>/wine-run.record (JSON, not .json: the M7 census counts
every *.json under private/generated as a possible run record), <out>/run.runrec (the
game's own run record), lifted.csv, the kept word log, stdout and stderr.

  .venv/bin/python tools/s7/wine_run.py --evidence <boot out>/wine-run.record \\
      <metalharbor out>/wine-run.record
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STEAM = Path.home() / ".local" / "share" / "Steam"
PROTON = STEAM / "steamapps" / "common" / "Proton - Experimental" / "files" / "bin"
WINE, WINESERVER = PROTON / "wine", PROTON / "wineserver"
PREFIX = ROOT / "private" / "generated" / "s7" / "wine" / "prefix"
BWRAP = "/usr/bin/bwrap"
# Wine's own settings: no debug channels, no Mono or Gecko install prompt, no menu entries.
WINE_ENV = {"WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml=;winemenubuilder.exe=d"}
# Display variables: with none, Wine opens no window and needs no compositor.
DISPLAY_VARS = ("DISPLAY", "WAYLAND_DISPLAY")
RUN_RECORD = "run.runrec"
LINK_RECORD = "link.record"         # tools/s7/win_link.py's, beside the binary
# The runtime's import table dump of a stage run. tick_replay names it imports.table.json; here
# it is not .json, for the M7 census.
IMPORT_TABLE = "imports.table"
GB = 1 << 30
# What the comparison leaves available (`bounded`): the machine's rule, that every job keeps
# 8 GB free.
HEADROOM_GB = 8
SELF = "tools/s7/wine_run.py"
TARGET, TRIPLE = "x86_64-windows", "x86_64-windows-gnu"
# The target's replay runs, where tools/s7/target_receipt.py reads them (evidence_path).
EVIDENCE = ROOT / "Documentation" / "receipts" / f"s7-{TARGET}-replays-v1.json"
EVIDENCE_SCHEMA = "sa2-s7-target-replays/v1"
RECORD_SCHEMA = "sa2-s7-wine-run/v1"
# What wrote the evidence's numbers: this tool, the comparison it calls, and the link.
PIPELINE = (SELF, "tools/s4/tick_replay.py", "tools/s4/stage_boot.py", "tools/s7/win_link.py")


class Refused(SystemExit):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path) -> str:
    """A path as the run is handed it: relative to the repository, Wine's working directory,
    with forward slashes, which Wine and the runtime both read."""
    return Path(os.path.abspath(path)).relative_to(ROOT).as_posix()


def wrapped(argv: list[str], prefix: Path) -> list[str]:
    """bwrap around a Wine command: the whole filesystem as it is, Steam's tree read-only on
    top (Proton's Wine and every Steam prefix), the repository as the working directory."""
    return [BWRAP, "--dev-bind", "/", "/", "--ro-bind", str(STEAM), str(STEAM),
            "--die-with-parent", "--chdir", str(ROOT), "--"] + argv


def wine_environ(base: dict[str, str], prefix: Path) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k not in DISPLAY_VARS}
    env.update(WINE_ENV)
    env["WINEPREFIX"] = str(prefix)
    return env


def replay_env(TR, RR, LR, entry: dict, out: Path, profile) -> tuple[dict[str, str], dict]:
    """run_lifted's settings for a tick entry, with the outputs and the write overlay in `out`:
    loop_run's BASE_ENV, the reach configuration, the hook's outputs, the profile's switches.
    Returns (the variables the run is handed beyond the shell's, the profile's switches)."""
    words = "word_policy" in entry
    env = dict(LR.BASE_ENV)
    env["SA2_VFS_WRITE_ROOT"] = rel(out / "vfs")
    env.update(RR.ENV)
    env["SA2_REPLAY_TICKS"] = rel(out / "lifted.csv")
    if words:
        env["SA2_REPLAY_WORDS"] = rel(out / TR.LIFTED_WORDS)
    try:
        prof = RR.profile_env(profile)
    except TR.DP.Refusal as e:
        raise Refused(f"wine_run: {e}") from None
    env.update(prof)
    return env, prof


def spec_env(cmd: list[str], base: dict[str, str]) -> dict[str, str]:
    """The environment loop_run.py gives a run of `cmd` (its one `--run` spec,
    "label|VAR=VALUE|..."), laid over `base` (loop_run's BASE_ENV), as loop_run reads it."""
    specs = [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == "--run"]
    if len(specs) != 1:
        raise Refused(f"wine_run: stage_run.command gave {len(specs)} --run specs, not one")
    env = dict(base)
    for kv in specs[0].split("|")[1:]:
        if "=" not in kv:
            raise Refused(f"wine_run: --run {specs[0][:80]!r}: {kv!r} is not VAR=VALUE")
        k, v = kv.split("=", 1)
        env[k] = v
    return env


def flag_values(cmd: list[str], flag: str) -> list[str]:
    """Every value of a repeated `flag VALUE` in `cmd`, and of `flag=VALUE`."""
    out = [cmd[i + 1] for i, a in enumerate(cmd[:-1]) if a == flag]
    return out + [a[len(flag) + 1:] for a in cmd if a.startswith(flag + "=")]


def stage_setup(TR, RR, LR, SR, entry: dict, out: Path, profile) -> dict:
    """run_stage_lifted's observed run of a stage entry, through stage_run.command: its
    environment (BASE_ENV with the write overlay in `out`, then the command's run spec), its
    probe arguments, the import-table dump, and the hooks and wraps its binary links."""
    name = entry["name"]
    script = ROOT / entry["input"]["script"]
    if not script.is_file() or sha256(script) != entry["input"]["sha256"]:
        raise Refused(f"wine_run: {name}: its input {entry['input']['script']} is missing or "
                      f"does not hash to its pin")
    polls = TR.stage_poll_budget(entry)
    alarm = max(600, polls // 5)        # run_stage_lifted's: at least 5 ticks a second
    watch = {"SA2_STAGE_TRACE": rel(out / "stage-trace.csv")}
    if "word_policy" in entry:
        watch["SA2_REPLAY_WORDS"] = rel(out / TR.LIFTED_WORDS)
    try:
        cmd, _, ticks = SR.command(entry["stage"]["name"], polls, label="ticks",
                                   input_script=script, extra_env=watch, timeout=alarm,
                                   device_profile=profile, name=f"wine-{name}", out_dir=out,
                                   observe=True)
    except SystemExit as e:
        raise Refused(f"wine_run: {e}") from None
    if ticks != out / "lifted.csv":
        raise Refused(f"wine_run: stage_run.command writes the capture at {ticks}, not "
                      f"{out / 'lifted.csv'}")
    env = spec_env(cmd, {**LR.BASE_ENV, "SA2_VFS_WRITE_ROOT": rel(out / "vfs")})
    probe = flag_values(cmd, "--probe-arg") + ["--dump-imports", rel(out / IMPORT_TABLE)]
    return {"env": env, "profile_env": RR.profile_env(profile), "probe": probe,
            "hooks": flag_values(cmd, "--hook"), "wraps": flag_values(cmd, "--wrap"),
            "polls": polls, "alarm": alarm}


def tick_setup(TR, RR, LR, entry: dict, out: Path, profile) -> dict:
    env, prof = replay_env(TR, RR, LR, entry, out, profile)
    return {"env": env, "profile_env": prof, "probe": list(RR.PROBE_ARGS), "hooks": [TR.HOOK],
            "wraps": [TR.WRAP]}


def linked_for(exe: Path, hooks: list[str], wraps: list[str], production: list[str]) -> list[str]:
    """Why `exe` is not the binary win_link.py linked for a runner with these hooks and wraps,
    from the link.record beside it, or []."""
    rec = exe.parent / LINK_RECORD
    if not rec.is_file():
        return [f"no {LINK_RECORD} beside {exe} (tools/s7/win_link.py writes it), so what the "
                f"binary links is not known"]
    d = json.loads(rec.read_text())
    out = []
    if (d.get("link") or {}).get("sha256") != sha256(exe):
        out.append(f"{exe.name} is not the binary its {LINK_RECORD} names")
    if d.get("hooks") != hooks:
        out.append(f"the binary links the hooks {d.get('hooks')}, and this replay's runner "
                   f"links {hooks}")
    if d.get("wrapped") != list(production) + list(wraps):
        out.append(f"the binary wraps {d.get('wrapped')}, and this replay's runner wraps "
                   f"{list(production) + list(wraps)}")
    return out


def registered(TR, name: str) -> dict:
    """The receipt's lifted run of `name`, its capture on disk held to the receipt's hash."""
    receipt = json.loads(TR.RECEIPT.read_text())
    rows = [r for r in receipt.get("replays", []) if r.get("name") == name]
    if len(rows) != 1:
        raise Refused(f"wine_run: {TR.RECEIPT.name} has {len(rows)} entries named {name}")
    lifted = rows[0]["lifted"]
    cap = ROOT / lifted["capture"]
    if not cap.is_file() or sha256(cap) != lifted["sha256"]:
        raise Refused(f"wine_run: the registered capture {lifted['capture']} is missing or does "
                      f"not hash to the receipt's {lifted['sha256'][:12]}; the receipt's run is "
                      f"being refreshed, or it moved")
    return {"receipt": str(TR.RECEIPT.relative_to(ROOT)), "receipt_sha256": sha256(TR.RECEIPT),
            "receipt_commit": receipt.get("commit"), "lifted": lifted,
            "result": rows[0].get("result")}


def exe_problems(run: dict, kind: str) -> list[str]:
    """The run's own record against the executable it was asked to load: harness/crt_probe.c
    writes `exe.kind` from what the loader mapped, never from the path it was handed."""
    exe = run.get("exe")
    if not isinstance(exe, dict):
        return [f"the run record has no `exe` block, so which executable it loaded is not "
                f"known (asked for the {kind} one)"]
    if exe.get("kind") != kind:
        return [f"asked for the {kind} executable; the run record says the loader mapped "
                f"{exe.get('kind')!r}"]
    return []


def verdict(result: dict | None) -> dict:
    """What two replay results are compared on: the first divergence, the class, and per
    residue group whether its rule held and how many words differed."""
    result = result or {}
    groups = [{"record": g.get("record"), "group": g.get("group"),
               "held": (g.get("rule") or {}).get("held"),
               "differing": (g.get("lifted") or {}).get("differing")}
              for g in (result.get("residues") or {}).get("groups", [])]
    return {"first_divergence": result.get("first_divergence"), "class": result.get("class"),
            "judged_ticks": result.get("judged_ticks"), "groups": groups}


def prefix_compare(got: bytes, want: bytes) -> dict:
    """A stopped run's capture against the registered one: its complete rows (the header and
    every row ended by a newline) against as many of the registered capture's first bytes."""
    rows = got[:got.rfind(b"\n") + 1]
    n = max(rows.count(b"\n") - 1, 0)
    return {"ticks_compared": n, "registered_ticks": max(want.count(b"\n") - 1, 0),
            "prefix_equal": n > 0 and want[:len(rows)] == rows}


def proc_field(path: str, field: str) -> int:
    """A `Field:  N kB` line of /proc/meminfo or /proc/self/status, in bytes."""
    for line in Path(path).read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1]) * 1024
    raise Refused(f"wine_run: {path} has no {field}")


def mem_available() -> int:
    return proc_field("/proc/meminfo", "MemAvailable")


def bounded(fn, headroom: int) -> dict:
    """fn() in a forked process whose private writable memory (RLIMIT_DATA) may grow by what is
    available now less `headroom`, and no further, so a comparison too large for the machine
    ends in MemoryError rather than in the machine running out. fn's result must be JSON.
    Returns {"result": ...} or {"problem": ...}, with the memory available, the bound, the
    exit code and the process's peak resident size."""
    avail = mem_available()
    rec: dict = {"available_mb": avail >> 20, "headroom_mb": headroom >> 20}
    room = avail - headroom
    if room < GB:
        rec["problem"] = (f"not started: {avail >> 20} MB available, which leaves less than "
                          f"1 GB above the {headroom >> 20} MB headroom")
        return rec
    limit = proc_field("/proc/self/status", "VmData") + room
    rec["bound_mb"] = limit >> 20
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:                                    # the comparison's process
        os.close(rfd)
        code = 0
        try:
            resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
            payload = json.dumps({"result": fn()}, default=str)
        except MemoryError:
            code = 3
            payload = json.dumps({"problem": f"MemoryError: the comparison needed more than "
                                             f"its bound of {limit >> 20} MB"})
        except BaseException as e:                  # noqa: BLE001  a refusal, recorded
            code = 4
            payload = json.dumps({"problem": f"{type(e).__name__}: {e}"[:4000]})
        with os.fdopen(wfd, "w") as fh:
            fh.write(payload)
        os._exit(code)
    os.close(wfd)
    with os.fdopen(rfd) as fh:
        data = fh.read()
    _, status, usage = os.wait4(pid, 0)
    rec["exit"] = os.waitstatus_to_exitcode(status)
    rec["peak_rss_mb"] = usage.ru_maxrss >> 10
    try:
        got = json.loads(data) if data else {}
    except json.JSONDecodeError:
        got = {}
    if rec["exit"] == 0 and "result" in got:
        rec["result"] = got["result"]
    else:
        rec["problem"] = got.get("problem") or (f"the comparison's process ended with "
                                                f"{rec['exit']} and wrote no result")
    return rec


def judge(TR, entry: dict, out: Path, lifted: dict, want: dict, reg_run: dict,
          reference_words: Path | None, no_verdict: bool) -> dict:
    """The run's word stream against the registered log, and the replay's verdict recomputed
    over this run, by tick_replay's functions, in out/verdict, as tick_replay.replay makes it:
    for a stage entry with the stage trace's comparison and the goal verdict. {"words": ...,
    "verdict": ...}, each None when the entry or the arguments do not ask for it."""
    res: dict = {"words": None, "verdict": None}
    ctx = None
    if "word_policy" in entry and "words" in lifted:
        work = out / "verdict"
        work.mkdir(exist_ok=True)
        ctx = TR.word_native(entry, work)
        reg_words = want.get("words") or {}
        reg_log = ROOT / str(reg_words.get("path") or "")
        words = {"sha256": lifted["words"]["sha256"], "registered_sha256": reg_words.get("sha256")}
        at_pin = bool(reg_words.get("path")) and reg_log.is_file() \
            and sha256(reg_log) == reg_words.get("sha256")
        if not at_pin and reference_words is not None:
            reg_log = Path(os.path.abspath(reference_words))
            words["against"] = {"reference": str(reg_log), "sha256": sha256(reg_log)}
        elif at_pin:
            words["against"] = {"registered": reg_words.get("path")}
        if not at_pin and reference_words is None:
            words["comparable"] = False
            words["why"] = (f"the registered word log {reg_words.get('path')} is not on disk at the "
                            f"receipt's pin (a refresh rewrote it since the receipt)")
        else:
            extra_cols = ctx["spec"]["extra"]
            wh, wrows = TR.log_stream(ROOT / lifted["words"]["path"], ctx["mask"], extra_cols,
                                      native=False)
            rh, rrows = TR.log_stream(reg_log, ctx["mask"], extra_cols, native=False)
            words["comparable"] = True
            words["ticks"] = [len(wrows), len(rrows)]
            words["equal"] = wh == rh and wrows == rrows
            if not words["equal"]:
                diff = next(((t, [c for c, a, b in zip(wh[1:], x, y) if a != b][:8])
                             for (t, x), (_, y) in zip(wrows, rrows) if x != y), None)
                words["first_difference"] = {"tick": diff[0], "columns": diff[1]} if diff else \
                    {"tick": min(len(wrows), len(rrows)) + 1, "columns": ["run length"]}
            del wrows, rrows        # released before the verdict reads the native streams
        res["words"] = words
    if not no_verdict:
        work = out / "verdict"
        work.mkdir(exist_ok=True)
        t1 = time.time()
        stop = lifted["stop"]["note"]
        if "word_policy" in entry:
            ctx = ctx or TR.word_native(entry, work)
            result = TR.word_compare(entry, ctx, lifted, work)
            ctx = None
        else:
            header, native = TR.read_capture(ROOT / entry["native"]["capture"])
            det = TR.native_repeats(entry, header, native, work)
            lh, lrows = TR.read_capture(out / "lifted.csv")
            result = TR.compare(header, native, lh, lrows, entry.get("field_policy", {}),
                                TR.capture_labels(entry), work, judged=det["judged_ticks"],
                                name=entry["name"], lifted_stop=stop)
        goals = [None, None]
        if TR.is_stage(entry):
            import stage_boot as SB  # noqa: PLC0415  read-only: goal_evidence, goal_verdict
            result["trace"] = TR.compare_trace(entry, ROOT / lifted["stage_trace"]["path"], work,
                                               lifted_stop=stop)
            goals = [SB.goal_verdict(SB.goal_evidence(entry, {"lifted": run}), ROOT)
                     .get("complete") is True for run in (lifted, reg_run["lifted"])]
        name = entry["name"]
        mine = dict(verdict(result), m5=TR.m5_record({"name": name, "result": result}),
                    goal_complete=goals[0])
        theirs = dict(verdict(reg_run["result"]), goal_complete=goals[1],
                      m5=TR.m5_record({"name": name, "result": reg_run["result"]}))
        res["verdict"] = {"wine": mine, "registered": theirs, "equal": mine == theirs,
                          "goal_complete": goals[0], "tick_result": result,
                          "seconds": round(time.time() - t1, 2)}
    return res


def evidence_problems(d: dict, registry_sha256: str) -> list[str]:
    """Why a run record is not one the target's replay runs may hold, [] when it is: whole and
    ok, on the unpacked executable (the registered replays'), under this registry, with its
    recomputed result and, for a stage replay, its goal verdict."""
    out = []
    v = d.get("verdict") or {}
    for bad, why in (
            (d.get("schema") != RECORD_SCHEMA, f"its schema is {d.get('schema')!r}"),
            (d.get("ok") is not True, "it is not ok"),
            (d.get("seconds_bound") is not None or (d.get("run") or {}).get("cut"),
             "the run was cut at --seconds"),
            (d.get("exe_kind") != "unpacked", f"it loaded the {d.get('exe_kind')} executable, and "
                                              "the registered replays are the unpacked one's"),
            (d.get("registry_sha256") != registry_sha256, "it ran under another registry"),
            (not isinstance(v.get("tick_result"), dict), "it records no recomputed result"),
            (v.get("equal") is not True, "its verdict is not the registered one"),
            (bool(d.get("stage")) and not isinstance(v.get("goal_complete"), bool),
             "a stage replay's run records no goal verdict"),
            (not Path((d.get("binary") or {}).get("path") or "/").resolve().is_relative_to(
                ROOT / "private"), "its binary is not under private/")):
        if bad:
            out.append(why)
    return out


def write_evidence(paths: list[Path]) -> int:
    """The target's replay runs (EVIDENCE) from one run record per registered replay."""
    reg_sha = sha256(ROOT / "Documentation" / "replays.json")
    reg = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    names = [e["name"] for e in reg["replays"]]
    runs: dict = {}
    runners = []
    problems = []
    for p in paths:
        p = Path(os.path.abspath(p))
        d = json.loads(p.read_text())
        name = d.get("replay")
        if name in runs:
            problems.append(f"{p}: a second run of {name}")
            continue
        problems += [f"{p}: {why}" for why in evidence_problems(d, reg_sha)]
        v = d.get("verdict") or {}
        runs[name] = {
            "binary_sha256": (d.get("binary") or {}).get("sha256"),
            "exe_kind": d.get("exe_kind"),
            "tick_result": v.get("tick_result"),
            "m5": (v.get("wine") or {}).get("m5"),
            "capture_equal": (d.get("compare") or {}).get("capture_equal"),
            "words_equal": ((d.get("compare") or {}).get("words") or {}).get("equal"),
            "verdict_equal": v.get("equal"),
            "run": {"record": {"path": p.relative_to(ROOT).as_posix(), "sha256": sha256(p)},
                    "seconds": (d.get("run") or {}).get("seconds"),
                    "binary": Path((d.get("binary") or {}).get("path") or ".").resolve()
                    .relative_to(ROOT).as_posix()},
            "judge": d.get("judge")}
        if d.get("stage"):
            runs[name]["goal_complete"] = v.get("goal_complete")
        runners.append(d.get("runner") or {})
    missing, stray = sorted(set(names) - set(runs)), sorted(set(runs) - set(names))
    if missing or stray:
        problems.append(f"the runs must be the registered replays {names}: missing {missing}, "
                        f"not registered {stray}")
    if any(r.get("kind") != "wine" or r.get("headless") is not True for r in runners):
        problems.append("a run is not a headless Wine run")
    versions = sorted({str(r.get("wine_version")) for r in runners})
    if len(versions) != 1:
        problems.append(f"the runs ran under {len(versions)} Wine versions: {versions}")
    if problems:
        raise Refused("wine_run --evidence: " + "; ".join(problems))
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True,
                          text=True).stdout.strip()
    runner = {k: runners[0].get(k) for k in ("kind", "wine", "wine_version", "bwrap", "ro_bind",
                                             "headless", "display_vars_unset", "window")}
    runner["prefix"] = Path(runners[0]["prefix"]).relative_to(ROOT).as_posix()
    doc = {"schema": EVIDENCE_SCHEMA, "generated_by": SELF, "target": TARGET, "triple": TRIPLE,
           "question": "Does every registered replay, run on the x86-64 Windows build under "
                       "Proton's Wine, reproduce its registered lifted capture, and what does "
                       "tick_replay's comparison read of each run?",
           "runner": runner, "registry_sha256": reg_sha,
           "replays": {n: runs[n] for n in names},
           "inputs": {"pipeline": {f: sha256(ROOT / f) for f in PIPELINE}},
           "commit": head, "written": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    EVIDENCE.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"-> {EVIDENCE.relative_to(ROOT)}: {', '.join(names)} under {versions[0]}")
    return 0


def run_bounded(argv: list[str], env: dict[str, str], out: Path, timeout: int,
                seconds: float | None) -> tuple[int | None, bool]:
    """Run under bwrap in its own process group, stopped at `seconds` (a cut, as asked) or at
    `timeout` (a time-out). Returns (rc, cut); rc is None when it was stopped."""
    limit = seconds if seconds else timeout
    with (out / "stdout.txt").open("w") as so, (out / "stderr.txt").open("w") as se:
        proc = subprocess.Popen(argv, env=env, stdout=so, stderr=se, cwd=str(ROOT),
                                start_new_session=True)
        try:
            return proc.wait(timeout=limit), False
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return None, bool(seconds)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, help="the Windows build (win_link.py)")
    ap.add_argument("--out", type=Path, help="a fresh output directory")
    ap.add_argument("--evidence", nargs="+", type=Path, default=None, metavar="RECORD",
                    help="run nothing: write the target's replay runs from these run records, "
                         "one per registered replay")
    ap.add_argument("--replay", default="boot")
    ap.add_argument("--prefix", type=Path, default=PREFIX, help="the scratch WINEPREFIX")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop the run here and compare the capture's complete rows with the "
                         "registered capture's first rows (fast_check's Metal Harbor check)")
    ap.add_argument("--min-ticks", type=int, default=1,
                    help="with --seconds: the fewest complete rows that pass")
    ap.add_argument("--reference-words", type=Path, default=None,
                    help="a Linux run's kept word log to compare the streams with when the "
                         "registered one is not at its pin; recorded as a reference, never as "
                         "the registered log")
    ap.add_argument("--exe-kind", choices=("unpacked", "shipped"), default="unpacked",
                    help="the executable the run loads: the unpacked one (default, the "
                         "registered replays') or the shipped one through the overlay table, "
                         "which needs a build linked with win_link.py --exe-overlay")
    ap.add_argument("--no-verdict", action="store_true",
                    help="compare the capture only; skip recomputing the replay's verdict")
    ap.add_argument("--headroom-gb", type=int, default=HEADROOM_GB,
                    help="memory the words and verdict comparison leaves available (default 8)")
    args = ap.parse_args()
    if args.evidence is not None:
        return write_evidence(args.evidence)
    if args.exe is None or args.out is None:
        ap.error("--exe and --out are required for a run")

    out, exe, prefix = (Path(os.path.abspath(p)) for p in (args.out, args.exe, args.prefix))
    for p, what in ((out, "--out"), (prefix, "--prefix")):
        if not p.is_relative_to(ROOT / "private"):
            raise Refused(f"wine_run: {what} {p} is not under private/")
    if prefix.is_relative_to(STEAM) or "compatdata" in prefix.parts:
        raise Refused(f"wine_run: {prefix} is a Steam prefix; the run uses its own")
    if out.exists() and any(out.iterdir()):
        raise Refused(f"wine_run: {out} is not empty; every run starts from a fresh directory")
    for tool in (WINE, WINESERVER, Path(BWRAP)):
        if not tool.is_file():
            raise Refused(f"wine_run: {tool} is not there")
    if not exe.is_file():
        raise Refused(f"wine_run: no binary at {exe}")
    if args.seconds is not None and args.seconds <= 0:
        raise Refused("wine_run: --seconds must be positive")
    out.mkdir(parents=True, exist_ok=True)
    prefix.mkdir(parents=True, exist_ok=True)
    # wordlog.py builds its reader under SA2_WORDLOG_BUILD, read when it is imported: here,
    # never the shared private/generated/s4/wordlog.
    os.environ["SA2_WORDLOG_BUILD"] = str(out / "wordlog-build")

    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import link_s4 as LS  # noqa: E402  read-only: PRODUCTION_WRAPS
    import loop_run as LR  # noqa: E402  read-only
    import reach_receipt as RR  # noqa: E402  read-only
    import tick_replay as TR  # noqa: E402  read-only: its functions are called, never main

    reg = json.loads(TR.REGISTRY.read_text())
    entries = [e for e in (reg["replays"] if isinstance(reg, dict) else reg)
               if e.get("name") == args.replay]
    if len(entries) != 1:
        raise Refused(f"wine_run: the registry has {len(entries)} entries named {args.replay}")
    entry = entries[0]
    stage = TR.is_stage(entry)
    if stage:
        problems = TR.stage_entry_problems(entry)
        if problems:
            raise Refused(f"wine_run: {'; '.join(problems)}")
    reg_run = registered(TR, args.replay)
    profile = TR.native_device_profile(entry)
    if stage:
        import stage_run as SR  # noqa: E402,PLC0415  read-only: command and goal
        setup = stage_setup(TR, RR, LR, SR, entry, out, profile)
    else:
        setup = tick_setup(TR, RR, LR, entry, out, profile)
    link_problems = linked_for(exe, setup["hooks"], setup["wraps"], LS.PRODUCTION_WRAPS)
    if link_problems:
        raise Refused(f"wine_run: {exe} is not linked for {args.replay}'s runner: "
                      f"{'; '.join(link_problems)}")
    extra = dict(setup["env"])
    # The executable and SA2_EXE_KIND are --exe-kind's alone, as loop_run.py's are --exe's:
    # clean_environ drops a kind the shell holds (reach_receipt.RUN_IMAGE_VARS).
    game_exe, kind_env = LR.exe_argv(args.exe_kind)
    extra.update(kind_env)
    env = wine_environ(RR.clean_environ(), prefix)
    env.pop(LR.EXE_KIND_VAR, None)
    env.update(extra)
    run_json = out / RUN_RECORD
    argv = [str(WINE), str(exe), str(game_exe), "--json", rel(run_json)] + setup["probe"]
    record: dict = {
        "schema": RECORD_SCHEMA, "replay": args.replay, "stage": stage,
        "registry_sha256": sha256(TR.REGISTRY),
        "exe_kind": args.exe_kind, "binary": {"path": str(exe), "sha256": sha256(exe)},
        "linked_for": {"hooks": setup["hooks"], "wraps": setup["wraps"]},
        "runner": {"kind": "wine", "wine": str(WINE), "wine_version": None,
                   "prefix": str(prefix), "bwrap": BWRAP, "ro_bind": str(STEAM),
                   "headless": True, "display_vars_unset": list(DISPLAY_VARS),
                   "window": "none: no display variable, and the runtime's devices open none"},
        "argv": argv[1:], "env": extra, "wine_env": WINE_ENV, "registered": reg_run,
        "seconds_bound": args.seconds,
    }
    try:
        v = subprocess.run(wrapped([str(WINE), "--version"], prefix),
                           env=wine_environ(os.environ, prefix), capture_output=True,
                           text=True, timeout=120)
        record["runner"]["wine_version"] = v.stdout.strip()
    except subprocess.TimeoutExpired:
        pass
    t0 = time.time()
    try:
        rc, cut = run_bounded(wrapped(argv, prefix), env, out, args.timeout, args.seconds)
    finally:
        subprocess.run(wrapped([str(WINESERVER), "-k"], prefix), env=env,
                       capture_output=True, timeout=120)
    record["run"] = {"rc": rc, "cut": cut, "seconds": round(time.time() - t0, 2)}
    rec_path = out / "wine-run.record"

    def finish(code: int) -> int:
        rec_path.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")
        print(f"-> {rec_path}")
        return code

    cap = out / "lifted.csv"
    want = reg_run["lifted"]
    if cut:
        # Stopped as asked: no run record, so only the capture's complete rows are judged.
        got = cap.read_bytes() if cap.is_file() else b""
        cmp = dict(prefix_compare(got, (ROOT / want["capture"]).read_bytes()),
                   registered_sha256=want["sha256"], min_ticks=args.min_ticks)
        record["compare"] = cmp
        record["ok"] = bool(cmp["prefix_equal"] and cmp["ticks_compared"] >= args.min_ticks)
        print(json.dumps({"replay": args.replay, "cut_at_seconds": args.seconds,
                          "compare": cmp, "ok": record["ok"]}, indent=1))
        return finish(0 if record["ok"] else 1)
    if rc is None or not run_json.is_file() or not cap.is_file():
        record["run"]["problem"] = ("timed out" if rc is None else
                                    f"rc {rc}; run record {run_json.is_file()}, capture "
                                    f"{cap.is_file()}")
        print(f"wine_run: the run did not complete: {record['run']['problem']}")
        print((out / "stderr.txt").read_text()[-3000:])
        return finish(1)
    d = json.loads(run_json.read_text())
    problems = []
    try:
        record["run"]["device_profile"] = TR.DP.check_run(d, profile)
    except TR.DP.Refusal as e:
        problems.append(f"device profile: {e}")
    problems += RR.switch_problems(d, {**RR.ENV, **setup["profile_env"]})
    problems += exe_problems(d, args.exe_kind)
    record["run"]["exe"] = d.get("exe")
    record["run"].update(reach=TR.reach_of(d), poll_handoff=TR.poll_counters(d),
                         stop=TR.stop_note(d), guest_paths=TR.DP.run_guest(d))
    lifted = {"capture": rel(cap), "sha256": sha256(cap),
              "stop": {"note": TR.stop_note(d)}}
    if stage:
        applied = out / "input-applied.csv"
        native_ticks = len(TR.read_capture(ROOT / entry["native"]["capture"])[1])
        if applied.is_file():
            lifted["input"] = TR.input_record(entry, applied, native_ticks)
            problems += [f"input: {p}" for p in lifted["input"]["problems"]]
        else:
            problems.append("the stage run wrote no applied-input log: its input hook did not run")
        lifted["goal"] = SR.goal(out / "goal-latch.csv")
        # Pinned as run_stage_lifted pins them: the trace comparison and the goal verdict
        # read them (`judge`).
        for key, f in (("goal_latch", out / "goal-latch.csv"), ("stage_trace",
                                                                 out / "stage-trace.csv"),
                       ("input_applied", applied)):
            if f.is_file():
                lifted[key] = {"path": rel(f), "sha256": sha256(f)}
            else:
                problems.append(f"the stage run wrote no {f.name}")
        table = out / IMPORT_TABLE
        if not table.is_file() or not json.loads(table.read_text()).get("rows"):
            problems.append(f"the stage run dumped no import table at {rel(table)}")
    if "word_policy" in entry:
        try:
            lifted["words"] = TR.keep_lifted_words(cap, out / TR.LIFTED_WORDS)
        except SystemExit as e:
            problems.append(f"word log: {e}")
    record["run"]["problems"] = problems

    # The capture against the registered one.
    cmp = {"capture_equal": lifted["sha256"] == want["sha256"],
           "capture_sha256": lifted["sha256"], "registered_sha256": want["sha256"]}
    if not cmp["capture_equal"]:
        rh, rrows = TR.read_capture(ROOT / want["capture"])
        wh, wrows = TR.read_capture(cap)
        work = out / "compare-capture"
        work.mkdir(exist_ok=True)
        first = TR.compare(rh, rrows, wh, wrows, entry.get("field_policy", {}),
                           TR.capture_labels(entry), work, judged=len(rrows), name=args.replay,
                           lifted_stop=lifted["stop"]["note"])
        cmp["first_difference"] = first.get("first_divergence")
    record["compare"] = cmp
    record["lifted"] = lifted

    # The word streams, and the replay's verdict, recomputed over this run, within the
    # machine's memory (`bounded`).
    asked = ("word_policy" in entry and "words" in lifted) or not args.no_verdict
    if asked and not problems:
        j = bounded(lambda: judge(TR, entry, out, lifted, want, reg_run, args.reference_words,
                                  args.no_verdict), args.headroom_gb * GB)
        res = j.pop("result", None)
        record["judge"] = j
        if res is None:
            problems.append(f"words and verdict: {j['problem']}")
        else:
            if res["words"] is not None:
                cmp["words"] = res["words"]
            if res["verdict"] is not None:
                record["verdict"] = res["verdict"]
    words = cmp.get("words") or {}
    ok = (not problems and cmp["capture_equal"]
          and (not words.get("comparable") or words.get("equal"))
          and ("word_policy" not in entry or "words" in cmp)
          and (args.no_verdict or record.get("verdict", {}).get("equal")))
    record["ok"] = bool(ok)
    print(json.dumps({"replay": args.replay, "rc": rc, "seconds": record["run"]["seconds"],
                      "problems": problems, "compare": cmp,
                      "verdict_equal": record.get("verdict", {}).get("equal"),
                      "ok": record["ok"]}, indent=1, default=str))
    return finish(0 if ok else 1)


if __name__ == "__main__":
    raise SystemExit(main())
