#!/usr/bin/env python3
"""Does answering D3DXGetShaderConstantTable change anything a lifted run compares?

Subtitle: the lifted build through the boot replay's run, the attract-demo run
(tools/s4/demo_run.py, as long as its default capture) and the synthetic Metal
Harbor run, each twice from one binary -- SA2_D3DX_CONSTANT_TABLE off, then
on -- with the tick capture and
the stage trace compared row by row, the tables' calls and counts read from
each run's own record and import table, and the tables' headers held to the
native trace's.

    source tools/env.sh
    .venv/bin/python tools/s4/d3dx_table_check.py [--scenario boot|demo|mh ...]
        [--break tablekeep] [--break tableanswer] [--out DIR] [--timeout S] [--write]

Why
---
Native gets a constant table from every D3DXGetShaderConstantTable call and
calls nothing on any of them through boot, the title and City Escape's
gameplay (Documentation/receipts/s4-native-outcomes-v1.json, outcome
d3dx-constant-table). With the switch on, runtime/sa2_d3d9.c answers the same:
D3D_OK and a table that answers QueryInterface, AddRef and Release and
refuses every other method by name. Whether that reproduces native in the
lifted build is a run, not an argument: the game must build its tables and
call nothing else on them, no refusal may fire, and nothing a replay compares
may move. harness/d3dx_table_probe.c shows the refusals work; this shows the
game never reaches one.

The scenarios
-------------
  boot  the boot replay's lifted run, as tools/s4/tick_replay.py makes it (the
        reach configuration on the boot capture's device profile,
        tick_replay.native_device_profile, and the tick hook by -wrap
        sub_433ee0), with the stage trace written beside the tick capture;
        build d3dx-boot. Its tick capture is also compared with the registered
        native capture (Documentation/replays.json, replay boot) by
        stage_run.compare.
  demo  tools/s4/demo_run.py's command, under the build d3dx-demo: from boot
        for as many ticks as demo_run.DEFAULT_NATIVE's stage trace holds, and
        300 more, on that capture's device profile, compared with its stage
        trace and tick capture.
  mh    tools/s4/stage_run.py's stage walk into Metal Harbor by test spawn,
        replaying the synthetic controller recording
        private/generated/replay/synth/forward-jump/sa2-input.csv on the default
        profile; build d3dx-mh. No native capture stands for it.

Each scenario runs its arms in order from one build: `off`
(SA2_D3DX_CONSTANT_TABLE=0), `on` (=1) and, for each --break B, `on-B` (=1
with SA2_D3D9_BREAK=B). Every arm sets SA2_D3D9_BREAK, the empty string when no
break, so one exported in the shell cannot run in its place, and
SA2_D3D9_LIFE_DUMP, which writes host files only. Each arm's tick capture,
stage trace, run record, import table and life dump land under
<out>/<scenario>/<arm>/. Each arm starts from the pinned saves and an overlay
that holds nothing else (tools/oracle/save_state.py reset_lifted and
require_empty), runs without the shell's device switches, breaks and profile
(reach_receipt.clean_environ), and is refused unless its record shows the
profile it was handed and every switch its row sets on
(device_profile.check_run, reach_receipt.switch_problems).

The verdicts, per scenario (each arm against `off`)
---------------------------------------------------
  one_binary          every arm ran the same binary
  ran_to_the_leash    every poll ran, to the host's quit and the game's own
                      ExitProcess(0), nothing injected; so no refusal stopped it
  import_row          the D3DX import's row: off, called N > 0 times and refused
                      N; on, called N times and refused none
  tables_built        on: one table per call (N), none dropped from the rows;
                      off: none
  no_other_method     on: no ID3DXConstantTable slot but QueryInterface, AddRef
                      and Release has a call, named or not
  only_final_releases on: no QueryInterface and no AddRef on any table, and as
                      many Releases as tables destroyed -- native calls nothing
                      on a table in the measured span, so the only calls a run
                      may make on one are the final Releases the next verdicts
                      account for and place
  counts_consistent   on: the counts add up table by table -- every table at
                      count 0 was destroyed by its final Release and every
                      destroyed one is at 0, and the counts left sum to the
                      tables built plus the AddRefs and answered QueryInterfaces
                      less the Releases
  released_after_the_last_frame
                      on: every table the run destroyed had its final Release
                      after the run's last Present, in the device's method
                      sequence -- in the game's exit path, past every compared
                      tick, where no native capture looks
  headers_are_natives on: the tables' Version, Constants and CTAB bytes, in
                      order, are the headers the native trace prints for the
                      game's calls on its main thread, one per call, in both
                      traced logs the outcome cites
  ticks_identical     the tick capture equals off's byte for byte on every row,
                      and both have the same rows
  trace_identical     the stage trace, likewise
  nothing_else_moved  every other d3d9 method's call count, the import calls,
                      the iterations and the stop are off's; the device's
                      objects built and destroyed and its method calls differ
                      from off's by the tables and their calls exactly

A record whose method surface counts one slot twice -- its `implemented` and
`dynamic_surface` rows, which harness/crt_probe.c writes from one count per
slot, disagreeing -- contradicts itself, and is refused rather than judged.

"Every row" is counted: a comparison of files a tick out of step, or with a
tick held twice, compares nothing and fails (tools/s4/d3d9_life_check.py
same_rows). A --break arm is judged by the same verdicts and reported beside
the others; its verdicts are expected to fail where the break bites, which is
how a verdict is shown able to fail.

What the runs are built and run from -- the runtime, the harness and hooks,
these tools and native_outcomes.py, whose log reader and main-thread rule
headers_are_natives uses, the replay registry, the native-outcomes receipt,
and each scenario's native capture or controller recording -- is hashed
before the arms run and must hash the same after them, or nothing is judged.
The result, those hashes included, is written to <out>/result.<scenario>.json.

With --write, and only when all three scenarios ran their off and on arms
from one tree and every verdict held for on, the results go into
Documentation/receipts/s4-d3dx-table-v1.json, with the commit the runs
started from; its inputs.pipeline pins what check_receipt_pins.py holds
current. Without --write nothing under Documentation/ is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "oracle"))
import d3d9_life_check as LC  # noqa: E402
import demo_run as D  # noqa: E402
import device_profile as DP  # noqa: E402
import native_outcomes as NO  # noqa: E402
import reach_receipt as RR  # noqa: E402
import save_state as SS  # noqa: E402  the pinned saves every lifted run starts from
import stage_run as SR  # noqa: E402
import tick_replay as TR  # noqa: E402

OUT = ROOT / "private" / "generated" / "s4" / "d3dx-table"
RECEIPT = ROOT / "Documentation" / "receipts" / "s4-d3dx-table-v1.json"
QUESTION = ("With SA2_D3DX_CONSTANT_TABLE on, does the lifted build get "
            "D3DXGetShaderConstantTable's native answer -- success and a table, for every call -- "
            "and call nothing on the tables but QueryInterface, AddRef and Release, with every "
            "compared row as it is with the switch off, through the boot replay's run, the "
            "attract-demo run and the synthetic Metal Harbor run?")
BUILDS = {"boot": "d3dx-boot", "demo": "d3dx-demo", "mh": "d3dx-mh"}
SYNTH = ROOT / "private" / "generated" / "replay" / "synth" / "forward-jump" / "sa2-input.csv"
IMPORT = "d3dx9_42.dll!D3DXGetShaderConstantTable"
TABLE = "ID3DXConstantTable"
COM_SLOTS = {0, 1, 2}                 # QueryInterface, AddRef, Release
BREAKS = ("tableanswer", "tablekeep")
OUTCOME = "d3dx-constant-table"
# What the runs are built and run from, recorded before the runs and required
# unchanged after them, so a result never describes a tree that moved under it.
PIPELINE = ["runtime/sa2_d3d9.c", "runtime/sa2_d3d9.h", "runtime/sa2_d3d9_m7_list.h",
            "runtime/sa2_imports.c",
            "harness/crt_probe.c", *SR.HOOKS, "harness/replay/tick_probe_core.h",
            "harness/replay/tick_fields.h", "harness/replay/tick_digest.h",
            "tools/s4/d3dx_table_check.py", "tools/s4/d3d9_life_check.py", "tools/s4/demo_run.py",
            "tools/s4/stage_run.py", "tools/s4/device_profile.py", "tools/s4/reach_receipt.py",
            "tools/s4/tick_replay.py", "tools/s4/loop_run.py", "tools/s4/link_s4.py",
            "tools/s4/native_outcomes.py",
            "Documentation/replays.json", "Documentation/receipts/s4-native-outcomes-v1.json"]


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def sha(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None

# ---------------------------------------------------------------- the runs


def arm_env(arm: str, brk: str) -> dict[str, str]:
    """What an arm adds: the switch, and the break, set even when empty.
    command() adds the host-only life dump beside them."""
    return {"SA2_D3DX_CONSTANT_TABLE": "0" if arm == "off" else "1",
            "SA2_D3D9_BREAK": brk}


def files(scn: str, arm: str, out: Path) -> dict[str, Path]:
    d = out / scn / arm
    return {"dir": d, "ticks": d / "lifted.csv", "trace": d / "stage-trace.csv",
            "imports": d / "imports.table.json", "life": d / "life.txt"}


def loop_cmd(name: str, spec: str, hooks: list[str], wraps: list[str], timeout: int,
             dump: Path, build: bool) -> list[str]:
    cmd = [SR.PY, str(HERE / "loop_run.py"), "--tag", RR.TAG, "--main", RR.MAIN, "--name", name,
           "--out-prefix", name, "--run", spec]
    for h in hooks:
        cmd += ["--hook", h]
    for w in wraps:
        cmd += ["--wrap", w]
    cmd += [f"--probe-arg={a}" for a in RR.PROBE_ARGS]
    cmd += ["--probe-arg=--timeout", f"--probe-arg={timeout}", "--timeout", str(timeout + 120),
            "--probe-arg=--dump-imports", f"--probe-arg={rel(dump)}"]
    return cmd + ([] if build else ["--no-build"])


def spec_of(label: str, env: dict[str, str]) -> str:
    bad = [f"{k}={v}" for k, v in env.items() if "|" in k + v]
    if bad:
        raise SystemExit(f"D3DX_TABLE_REFUSED a setting holds loop_run's separator: {bad}")
    return "|".join([label] + [f"{k}={v}" for k, v in env.items()])


def override_run_env(cmd: list[str], overrides: dict[str, str]) -> list[str]:
    """`cmd` with its one `--run` spec's settings overridden: the spec
    (loop_run's `label|K=V|...`) is parsed, updated and written back.
    demo_run.command refuses an extra setting that names a variable it sets
    itself, and once the switch-on push puts SA2_D3DX_CONSTANT_TABLE in
    reach_receipt.ENV it sets the switch, which the off arm must turn off; so
    the switch goes in here. run_arm then checks loop_run's own row for it."""
    if cmd.count("--run") != 1:
        raise SystemExit(f"D3DX_TABLE_REFUSED a command with {cmd.count('--run')} runs")
    i = cmd.index("--run")
    label, *kvs = cmd[i + 1].split("|")
    env = dict(kv.split("=", 1) for kv in kvs)
    env.update(overrides)
    return cmd[:i + 1] + [spec_of(label, env)] + cmd[i + 2:]


def command(scn: str, arm: str, brk: str, a, build: bool) -> list[str]:
    """The loop_run command for one arm of one scenario."""
    f = files(scn, arm, a.out)
    extra = dict(arm_env(arm, brk), SA2_D3D9_LIFE_DUMP=rel(f["life"]))
    name = BUILDS[scn] + a.build_suffix
    if scn == "boot":
        # tick_replay.run_lifted's run, under this build's name, with the stage
        # trace beside the tick capture (the hook only reads guest memory), on
        # the boot capture's device profile as that run is (RR.profile_env).
        env = dict(RR.ENV, SA2_REPLAY_TICKS=rel(f["ticks"]), SA2_STAGE_TRACE=rel(f["trace"]),
                   **RR.profile_env(a.boot_profile), **extra)
        return loop_cmd(name, spec_of(arm, env), [TR.HOOK], [TR.WRAP], a.timeout, f["imports"],
                        build)
    if scn == "demo":
        switch = {"SA2_D3DX_CONSTANT_TABLE": extra.pop("SA2_D3DX_CONSTANT_TABLE")}
        cmd, _, _, _ = D.command(a.demo_polls, a.demo_profile, a.timeout, name=name, label=arm,
                                 out=f["dir"], extra_env=extra)
        cmd = override_run_env(cmd, switch)
        cmd += ["--probe-arg=--dump-imports", f"--probe-arg={rel(f['imports'])}"]
        return cmd + ([] if build else ["--no-build"])
    # mh: stage_run.command's stage walk with the synthetic recording, under this
    # build's name (stage_run names its builds after the stage, which are not ours).
    level, character, cmdline = SR.STAGES["metalharbor"]
    env = dict(RR.ENV, SA2_IMPORT_BUDGET="8000000", SA2_PUMP_POLLS=str(a.mh_polls),
               SA2_TEST_SPAWN_LEVEL=str(level), SA2_TEST_SPAWN_CHARACTER=str(character),
               SA2_GUEST_CMDLINE=cmdline, SA2_REPLAY_TICKS=rel(f["ticks"]),
               SA2_STAGE_TRACE=rel(f["trace"]),
               SA2_VIRTUAL_BUDGET_MS=str(max(30000, a.mh_polls * 20)),
               SA2_INPUT_SCRIPT=str(a.input.resolve()),
               SA2_INPUT_APPLIED=rel(f["dir"] / "input-applied.csv"), **extra)
    return loop_cmd(name, spec_of(arm, env), SR.HOOKS, SR.WRAPS, a.timeout, f["imports"], build)


def run_arm(scn: str, arm: str, brk: str, a, build: bool) -> dict:
    """One arm: its record, its files, loop_run's row, and the binary's digest.
    Every output is removed first, so a file an earlier run left cannot read as
    this one's."""
    f = files(scn, arm, a.out)
    f["dir"].mkdir(parents=True, exist_ok=True)
    name = BUILDS[scn] + a.build_suffix
    run_json, matrix = SR.RUNDIR / f"{name}.{arm}.json", SR.RUNDIR / f"{name}.matrix.json"
    for p in (f["ticks"], f["trace"], f["imports"], Path(str(f["life"]) + ".exit"), run_json,
              matrix):
        p.unlink(missing_ok=True)
    cmd = command(scn, arm, brk, a, build)
    # Every arm starts from the pinned saves and an overlay that holds nothing
    # else, as the replay runs it stands for do (tools/oracle/save_state.py
    # reset_lifted and require_empty; P6's review, W2), so no arm reads what an
    # earlier one wrote.
    try:
        saves = SS.reset_lifted(cmd, ROOT)
        SS.require_empty(saves)
    except SS.SaveStateError as e:
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: {e.code}") from None
    try:
        # Without a device switch, break or profile from this shell
        # (RR.clean_environ): SA2_D3D9_BREAK among them, which every arm sets.
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                           timeout=a.timeout + 1800, env=RR.clean_environ())
    except subprocess.TimeoutExpired as e:
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: loop_run ran past {e.timeout} s")
    (f["dir"] / "loop_run.log").write_text(p.stdout + "\n--- stderr ---\n" + p.stderr)
    if not run_json.is_file() or not matrix.is_file():
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: no run record (loop_run exited "
                         f"{p.returncode})\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    (f["dir"] / "run.json").write_bytes(run_json.read_bytes())
    (f["dir"] / "matrix.json").write_bytes(matrix.read_bytes())
    m = json.loads(matrix.read_text())
    row = next((r for r in m.get("rows", []) if r.get("label") == arm), None)
    if row is None:
        # Without the arm's row there is no configuration to hold its devices to, and an
        # empty one would pass the switch check below (the P6 fold review, N1).
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: loop_run's matrix has no row for "
                         "this arm, so its configuration cannot be checked")
    rec = json.loads(run_json.read_text())
    # The run read the profile it was handed and had the devices it was handed
    # (P6's review, N5): a run on another device set is not the scenario's.
    try:
        read = DP.check_run(rec, profile_of(scn, a))
    except DP.Refusal as e:
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: the run is not the one asked for: "
                         f"{e}") from None
    off = RR.switch_problems(rec, row.get("env") or {})
    if off:
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: its devices are not its "
                         f"configuration's: {'; '.join(off)}")
    table = json.loads(f["imports"].read_text()) if f["imports"].is_file() else {}
    return {"arm": arm, "break": brk, "record": rec, "files": f, "env": row.get("env") or {},
            "binary_sha256": m.get("sha256"), "imports": table,
            "run_json": run_json, "saves": saves, "device_profile_read": read,
            "guest_paths": DP.run_guest(rec)}


def profile_of(scn: str, a) -> dict | str:
    """The device profile a scenario's runs are handed: the boot capture's, the
    demo capture's, or the default for the synthetic Metal Harbor run, which
    stands for no capture."""
    return {"boot": a.boot_profile, "demo": a.demo_profile}.get(scn, DP.DEFAULT)

# ------------------------------------------------------------ what native says


CALL_RE = re.compile(r"\w+:trace:d3dx:D3DXGetShaderConstantTable \(\w+, \w+\): Forwarded to "
                     r"D3DXGetShaderConstantTableEx\Z")
SIZE_RE = re.compile(r"\w+:trace:d3dx:D3DXFindShaderComment Returning comment data at \w+ with "
                     r"size (\d+)\Z")
HEAD_RE = re.compile(r"\w+:trace:d3dx:D3DXGetShaderConstantTableEx Creator \"[^\"]*\", Version "
                     r"(0x[0-9a-f]+), Constants (\d+), Target")


def call_headers(lines: list[tuple[int, str]]) -> tuple[list[tuple[str, int, int]], list[str]]:
    """One thread's (line number, line) pairs, in order, read as the game's
    D3DXGetShaderConstantTable calls: each call's (Version, Constants, CTAB
    bytes) is the first table header after it, before the next call, with the
    comment size printed before that header. A header no call precedes is the
    Ex function called by something other than the game's import -- a
    D3DXCompileShader asked for its table, say -- and is not a call's, so it is
    left out. A call with no header, or a call's header with no size before
    it, is a problem."""
    heads: list[tuple[str, int, int]] = []
    problems: list[str] = []
    call = size = None
    for n, line in lines:
        if CALL_RE.match(line):
            if call is not None:
                problems.append(f"line {call}: a call with no table header before the next call")
            call, size = n, None
        elif m := SIZE_RE.match(line):
            size = int(m.group(1))
        elif m := HEAD_RE.match(line):
            if call is not None and size is None:
                problems.append(f"line {n}: the header of the call at line {call} has no comment "
                                "size before it")
            elif call is not None:
                heads.append((f"0x{int(m.group(1), 16):08x}", int(m.group(2)), size))
            call = size = None
    if call is not None:
        problems.append(f"line {call}: a call with no table header after it")
    return heads, problems


def native_headers() -> tuple[list[tuple[str, int, int]], list[str]]:
    """The (Version, Constants, CTAB bytes) native's d3dx9 printed for the
    game's D3DXGetShaderConstantTable calls (call_headers), in order, on the
    thread the outcome's header count names -- the game's main thread, found
    by native_outcomes.main_thread -- in every traced log the outcome counts
    headers in; and the problems: a log that cannot be read or has no main
    thread, a call without its header, a log whose calls and headers are not
    as many as the receipt counts calls there, and a log that disagrees with
    the first."""
    rec = NO.load(NO.RECEIPT)
    out = next((o for o in rec["outcomes"] if o.get("id") == OUTCOME), None)
    if not out:
        return [], [f"the native-outcomes receipt has no outcome {OUTCOME}"]
    counts = out.get("counts", [])
    heads = sorted((c for c in counts if c.get("fn") == "trace:d3dx:D3DXGetShaderConstantTableEx"),
                   key=lambda c: c["log"])
    calls = {c["log"]: c.get("matches") for c in counts
             if c.get("fn") == "trace:d3dx:D3DXGetShaderConstantTable"
             and c.get("thread") == NO.MAIN}
    if not heads:
        return [], [f"outcome {OUTCOME} counts no table header in any log"]
    seqs, problems = {}, []
    for c in heads:
        log_rel = c["log"]
        key = Path(log_rel).parent.name
        if log_rel != f"{NO.CAPTURES}/{key}/{NO.LOG_NAME}" or c.get("thread") != NO.MAIN:
            problems.append(f"{log_rel}: the outcome counts headers on thread {c.get('thread')!r} "
                            f"of a file that is not a capture's {NO.LOG_NAME} on the main thread")
            continue
        try:
            log = NO.read_log(key, ROOT)
            tid = NO.main_thread(log)
        except NO.Refused as e:
            problems.append(f"{log_rel}: {e}")
            continue
        # the log must be the one the receipt measured: its digest and main thread
        entry = next((e for e in rec.get("logs") or [] if e.get("key") == key), None)
        if entry is None or (entry.get("sha256"), entry.get("main_thread")) != (log.sha256, tid):
            problems.append(f"{log_rel}: not the log the receipt records (receipt: "
                            + ("no entry" if entry is None else
                               f"sha256 {str(entry.get('sha256'))[:12]}, main thread "
                               f"{entry.get('main_thread')}")
                            + f"; read: sha256 {log.sha256[:12]}, main thread {tid})")
            continue
        seq, why = call_headers([(n, log.lines[n - 1]) for n in log.by_tid.get(tid, [])])
        problems += [f"{log_rel}: {w}" for w in why]
        if len(seq) != calls.get(log_rel):
            problems.append(f"{log_rel}: {len(seq)} calls with headers on thread {tid}, where the "
                            f"receipt counts {calls.get(log_rel)} calls")
        seqs[log_rel] = seq
    firsts = list(seqs.values())
    for log, seq in seqs.items():
        if firsts and seq != firsts[0]:
            problems.append(f"{log}'s {len(seq)} headers are not {next(iter(seqs))}'s "
                            f"{len(firsts[0])}")
    return (firsts[0] if firsts else []), problems

# ---------------------------------------------------------------- judging


def import_row(run: dict) -> dict | None:
    rows = [r for r in run["imports"].get("rows", []) if f"{r['dll']}!{r['name']}" == IMPORT]
    if len(rows) != 1:
        return None
    return {"calls": rows[0].get("calls"), "refused": rows[0].get("refused_calls")}


def surface(rec: dict) -> dict[tuple[str, int], tuple[str, int]]:
    """The run's method surface: (interface, slot) -> (method, calls), from
    `implemented` (every named slot, called or not) and `dynamic_surface`
    (every slot called, named or not). harness/crt_probe.c writes both from
    one count per slot, so a slot the two count differently is a record that
    contradicts itself, and it is refused rather than judged by either copy."""
    d3 = rec.get("d3d9") or {}
    out: dict[tuple[str, int], tuple[str, int]] = {}
    for r in d3.get("implemented", []) + d3.get("dynamic_surface", []):
        k = (r["interface"], r["slot"])
        if k in out and out[k][1] != r["calls"]:
            raise SystemExit(f"D3DX_TABLE_REFUSED the record counts {r['interface']}::"
                             f"{r['method']} (slot {r['slot']}) {out[k][1]} and {r['calls']} "
                             "times: its implemented and dynamic_surface rows disagree")
        out[k] = (r["method"], r["calls"])
    return out


def table_slots(rec: dict) -> dict[int, int]:
    """Calls per ID3DXConstantTable slot, named or not, from the method surface."""
    return {s: c for (i, s), (_, c) in surface(rec).items() if i == TABLE}


def methods(rec: dict) -> dict[tuple[str, str, int], int]:
    """Calls per (interface, method, slot) over every interface but the table."""
    return {(i, m, s): c for (i, s), (m, c) in surface(rec).items() if i != TABLE}


def rows_problems(cmp: dict, what: str) -> list[str]:
    """Why a same_rows(off's, the arm's) comparison is not identity on every row."""
    why = LC._rows(cmp, what, "the arm's")
    if LC._comparable(cmp) and not (cmp["shared_rows"] == cmp["rows_a"] == cmp["rows_b"]):
        why.append(f"{what}: {cmp['shared_rows']} rows shared of off's {cmp['rows_a']} and the "
                   f"arm's {cmp['rows_b']}")
    return why


def judge(off: dict, on: dict, polls: int, native: list[tuple[str, int, int]],
          native_problems: list[str]) -> dict[str, list[str]]:
    """Every verdict for `on` against `off`, each with why it does not hold."""
    v: dict[str, list[str]] = {}
    v["one_binary"] = ([] if off["binary_sha256"] and off["binary_sha256"] == on["binary_sha256"]
                       else [f"binaries {off['binary_sha256']} and {on['binary_sha256']}"])
    v["ran_to_the_leash"] = [f"{x['arm']}: {w}" for x in (off, on)
                             for w in LC.finishes(x["record"], polls)]
    ro, rn = import_row(off), import_row(on)
    why = []
    if not ro or not rn:
        why.append(f"the import tables hold {IMPORT} as off {ro} and on {rn}, not one row each")
    else:
        if not (ro["calls"] and ro["refused"] == ro["calls"]):
            why.append(f"off: called {ro['calls']} and refused {ro['refused']}, wanted N > 0 "
                       "both")
        if not (rn["calls"] == ro["calls"] and rn["refused"] == 0):
            why.append(f"on: called {rn['calls']} and refused {rn['refused']}, wanted "
                       f"{ro['calls']} and 0")
    v["import_row"] = why
    ct_on = (on["record"].get("d3d9") or {}).get("constant_tables") or {}
    ct_off = (off["record"].get("d3d9") or {}).get("constant_tables") or {}
    n = (rn or {}).get("calls")
    why = []
    if ct_off.get("switch") is not False or ct_off.get("built") != 0:
        why.append(f"off: switch {ct_off.get('switch')} built {ct_off.get('built')}, wanted "
                   "false and 0")
    if ct_on.get("switch") is not True or ct_on.get("built") != n or ct_on.get("rows_dropped") \
            or len(ct_on.get("rows") or []) != n:
        why.append(f"on: switch {ct_on.get('switch')} built {ct_on.get('built')} rows "
                   f"{len(ct_on.get('rows') or [])} dropped {ct_on.get('rows_dropped')}, wanted "
                   f"true and {n} tables, one row each")
    v["tables_built"] = why
    slots = table_slots(on["record"])
    other = {s: c for s, c in slots.items() if s not in COM_SLOTS and c}
    v["no_other_method"] = ([f"slot {s} has {c} call(s)" for s, c in sorted(other.items())]
                            + ([] if slots else ["the record has no ID3DXConstantTable rows"]))
    # Native calls nothing on a table in the measured span, so the only calls a
    # run may make on one are its final Releases, which counts_consistent
    # accounts for and released_after_the_last_frame places: no QueryInterface,
    # no AddRef, and one Release per table destroyed.
    why = [f"{k} {m} call(s) on a table" for s, m in ((0, "QueryInterface"), (1, "AddRef"))
           if (k := slots.get(s, 0))]
    if slots.get(2, 0) != (ct_on.get("destroyed") or 0):
        why.append(f"{slots.get(2, 0)} Release call(s) for {ct_on.get('destroyed')} table(s) "
                   "destroyed: a Release that destroyed nothing is a call native was not "
                   "measured making")
    v["only_final_releases"] = why
    rows = ct_on.get("rows") or []
    why = []
    for r in rows:
        dead = int(r["released_by"], 16) != 0
        if (r["refs"] == 0) != dead:
            why.append(f"table {r['seq']} ({r['table']}) is at count {r['refs']} and "
                       f"{'was' if dead else 'was not'} destroyed")
    if ct_on.get("destroyed") != sum(1 for r in rows if int(r["released_by"], 16)):
        why.append(f"destroyed {ct_on.get('destroyed')} is not the rows' "
                   f"{sum(1 for r in rows if int(r['released_by'], 16))}")
    left = sum(r["refs"] for r in rows)
    made = (ct_on.get("built") or 0) + slots.get(1, 0) + (ct_on.get("qi_answered") or 0) \
        - slots.get(2, 0)
    if left != made:
        why.append(f"the counts left sum to {left}, and built + AddRef + answered QueryInterface "
                   f"- Release is {made}")
    v["counts_consistent"] = why
    last = ct_on.get("last_present_seq")
    early = [r for r in rows if int(r["released_by"], 16) and
             not (isinstance(last, int) and r.get("released_seq", 0) > last)]
    v["released_after_the_last_frame"] = (
        [f"the record has no last Present ({last!r})"] if not isinstance(last, int) or not last
        else [f"table {r['seq']} was released at call {r.get('released_seq')}, not after the "
              f"last Present at call {last}" for r in early[:5]])
    got = [(r["version"], r["constants"], r["ctab_bytes"]) for r in rows]
    why = list(native_problems)
    if not native:
        why.append("no native header to compare")
    elif got != native:
        k = next((i for i, (x, y) in enumerate(zip(got, native)) if x != y), min(len(got),
                                                                              len(native)))
        why.append(f"{len(got)} tables against native's {len(native)} headers; the first that "
                   f"differs is #{k + 1}: {got[k] if k < len(got) else None} against "
                   f"{native[k] if k < len(native) else None}")
    v["headers_are_natives"] = why
    fo, fn = off["files"], on["files"]
    v["ticks_identical"] = rows_problems(LC.same_rows(fo["ticks"], fn["ticks"]), "the tick capture")
    v["trace_identical"] = rows_problems(LC.same_rows(fo["trace"], fn["trace"]), "the stage trace")
    why = []
    mo, mn = methods(off["record"]), methods(on["record"])
    moved = sorted(k for k in set(mo) | set(mn) if mo.get(k, 0) != mn.get(k, 0))
    why += [f"{i}::{m} (slot {s}) {mo.get((i, m, s), 0)} -> {mn.get((i, m, s), 0)}"
            for i, m, s in moved[:10]]
    for k, get in (("import calls", lambda r: (r.get("calls") or {}).get("total")),
                   ("iterations", lambda r: (r.get("pump") or {}).get("iterations")),
                   ("stop", lambda r: (r.get("trap") or {}).get("note"))):
        if get(off["record"]) != get(on["record"]):
            why.append(f"{k}: {get(off['record'])!r} -> {get(on['record'])!r}")
    d_off, d_on = off["record"].get("d3d9") or {}, on["record"].get("d3d9") or {}
    life_off, life_on = d_off.get("lifetime") or {}, d_on.get("lifetime") or {}
    table_calls = sum(slots.values())
    for k, a_, b_, extra in (
            ("objects built", d_off.get("objects_built"), d_on.get("objects_built"),
             ct_on.get("built") or 0),
            ("objects destroyed", life_off.get("objects_destroyed"),
             life_on.get("objects_destroyed"), ct_on.get("destroyed") or 0),
            ("method calls", d_off.get("method_calls"), d_on.get("method_calls"),
             (ct_on.get("built") or 0) + table_calls)):
        if not isinstance(a_, int) or not isinstance(b_, int) or b_ - a_ != extra:
            why.append(f"{k}: off {a_}, on {b_}, wanted on = off + {extra}")
    v["nothing_else_moved"] = why
    return v


def summary(run: dict) -> dict:
    rec, f = run["record"], run["files"]
    d3 = rec.get("d3d9") or {}
    ct = d3.get("constant_tables") or {}
    slots = table_slots(rec)
    return {"arm": run["arm"], "break": run["break"], "binary_sha256": run["binary_sha256"],
            "run": rel(f["dir"] / "run.json"),
            "stop": ((rec.get("trap") or {}).get("note") or "")[:300],
            "pump": {k: (rec.get("pump") or {}).get(k)
                     for k in ("iterations", "quit_by_host", "quit_by_guest", "injected")},
            "import_row": import_row(run),
            "tables": {k: ct.get(k) for k in ("switch", "built", "destroyed", "qi_answered",
                                              "qi_declined", "rows_dropped")},
            "table_calls_by_slot": {str(s): c for s, c in sorted(slots.items()) if c},
            "release_callers": sorted({r["released_by"] for r in ct.get("rows") or []
                                       if int(r["released_by"], 16)}),
            "files": {k: (rel(p) if p.is_file() else None) for k, p in f.items()
                      if k in ("ticks", "trace", "imports")},
            "saves": run.get("saves"),
            "device_profile_read": run.get("device_profile_read"),
            "guest_paths": run.get("guest_paths"),
            "sha256": {k: sha(p) for k, p in f.items() if k in ("ticks", "trace")},
            "rows": {"ticks": max(0, len(f["ticks"].read_text().splitlines()) - 1)
                     if f["ticks"].is_file() else 0,
                     "trace": max(0, len(f["trace"].read_text().splitlines()) - 1)
                     if f["trace"].is_file() else 0},
            "devices_configured": D.devices_configured(rec)}


def against_native(scn: str, run: dict, a) -> dict | None:
    """The arm against the scenario's native capture: recorded, not judged here
    (the arms' identity is what is judged, so they read the same)."""
    f = run["files"]
    if scn == "boot":
        return {"native": a.boot_native_rel,
                "ticks": SR.compare(ROOT / a.boot_native_rel, f["ticks"])}
    if scn == "demo":
        n = D.DEFAULT_NATIVE
        return {"native": DP.rel(n, ROOT),
                "trace": D.compare_traces(D.read_trace(n / "sa2-stage-trace.csv"),
                                          D.read_trace(f["trace"])),
                "ticks": SR.compare(n / "sa2-tick-capture.csv", f["ticks"])}
    return None

# -------------------------------------------------------------------- main


def inputs(scn: str, a) -> dict:
    """The runtime, the pipeline, and the scenario's own inputs, by sha256."""
    got = {"runtime_sha256": TR.runtime_digest(ROOT),
           "pipeline": {q: TR.must_sha256(ROOT / q) for q in PIPELINE}}
    if scn == "boot" and a.boot_native_rel:
        got["native_ticks"] = {a.boot_native_rel: TR.must_sha256(ROOT / a.boot_native_rel)}
    if scn == "boot":
        got["device_profile"] = DP.recorded(a.boot_profile)
    if scn == "demo":
        n = D.DEFAULT_NATIVE
        got["native"] = {DP.rel(n / f, ROOT): TR.must_sha256(n / f)
                         for f in ("sa2-stage-trace.csv", "sa2-tick-capture.csv")}
        got["device_profile"] = DP.recorded(a.demo_profile)
    if scn == "mh":
        got["input"] = {str(a.input): TR.must_sha256(a.input)}
    return got


def check_scenario(scn: str, a) -> tuple[int, dict]:
    arms = [(x, "") for x in a.arms] + [(f"on-{b}", b) for b in a.brk]
    polls = {"boot": TR.poll_budget(), "demo": a.demo_polls, "mh": a.mh_polls}[scn]
    before = inputs(scn, a)
    runs = {}
    for i, (arm, brk) in enumerate(arms):
        print(f"D3DX_TABLE {scn}/{arm}: running", flush=True)
        runs[arm] = run_arm(scn, arm, brk, a, build=(i == 0))
        r = runs[arm]
        if (r["record"].get("trap") or {}).get("status") == LC.TRAP_TIMEOUT:
            raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: stopped by the harness's "
                             "wall-clock alarm; rerun with a larger --timeout")
        if r["env"].get("SA2_D3D9_BREAK") != brk or \
                r["env"].get("SA2_D3DX_CONSTANT_TABLE") != arm_env(arm, brk)["SA2_D3DX_CONSTANT_TABLE"]:
            raise SystemExit(f"D3DX_TABLE_REFUSED {scn}/{arm}: its loop_run row sets "
                             f"{r['env'].get('SA2_D3DX_CONSTANT_TABLE')!r} and "
                             f"{r['env'].get('SA2_D3D9_BREAK')!r}")
    if inputs(scn, a) != before:
        raise SystemExit(f"D3DX_TABLE_REFUSED {scn}: an input moved while the arms ran")
    result = {"scenario": scn, "build": BUILDS[scn] + a.build_suffix, "polls": polls,
              "inputs": before,
              "arms": {k: summary(r) for k, r in runs.items()},
              "against_native": {k: against_native(scn, r, a) for k, r in runs.items()}}
    ok = True
    if "off" in runs and len(runs) > 1:
        native, nprob = native_headers()
        result["native_headers"] = {"count": len(native), "problems": nprob}
        result["verdicts"] = {}
        for arm, r in runs.items():
            if arm == "off":
                continue
            v = judge(runs["off"], r, polls, native, nprob)
            result["verdicts"][arm] = {k: {"holds": not why, "why": why} for k, why in v.items()}
            held = [k for k, why in v.items() if not why]
            failed = [k for k, why in v.items() if why]
            print(f"D3DX_TABLE {scn} {arm} against off: {len(held)} of {len(v)} hold"
                  + (f"; FAILS: {', '.join(failed)}" if failed else ""))
            for k in failed:
                print(f"  {k}: {'; '.join(v[k])[:600]}")
            if arm == "on" and failed:
                ok = False
    for arm, s in result["arms"].items():
        print(f"  {arm}: tables {s['tables']} import {s['import_row']} table calls "
              f"{s['table_calls_by_slot']} rows {s['rows']} stop: {s['stop'][:100]}")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / f"result.{scn}.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    print(f"D3DX_TABLE {scn} {'OK' if ok else 'FAILED'} -> {rel(a.out / f'result.{scn}.json')}")
    return (0 if ok else 1), result


def provenance() -> dict:
    """The commit the runs start from and whether the tree differs from it; a
    git that fails is refused rather than read as commit "" and a clean tree.
    Neither may take the index lock (GIT_OPTIONAL_LOCKS=0)."""
    def git(*args: str) -> str:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        if p.returncode:
            raise SystemExit(f"D3DX_TABLE_REFUSED git {' '.join(args)} exited {p.returncode}: "
                             f"{p.stderr.strip()[-300:]}")
        return p.stdout.strip()
    return {"commit": git("rev-parse", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain"))}


def receipt(results: dict[str, dict], a, origin: dict) -> dict:
    """The receipt, from every scenario's result; refused unless all three ran
    their off and on arms and every verdict held for on. A --break arm's
    verdicts are kept beside the others: where they fail is the record of
    what each verdict can see."""
    missing = sorted(set(BUILDS) - set(results))
    if missing or a.arms != ["off", "on"]:
        raise SystemExit(f"D3DX_TABLE_REFUSED --write needs every scenario's off and on arms; "
                         f"missing {missing}, arms {a.arms}")
    failed = []
    for s, r in sorted(results.items()):
        on = (r.get("verdicts") or {}).get("on")
        failed += ([f"{s}: {k}" for k, v in sorted(on.items()) if not v["holds"]] if on
                   else [f"{s}: no verdicts for the on arm"])
    if failed:
        raise SystemExit(f"D3DX_TABLE_REFUSED not every verdict holds: {failed}")
    shared = {k: results["boot"]["inputs"][k] for k in ("runtime_sha256", "pipeline")}
    if any({k: r["inputs"][k] for k in shared} != shared for r in results.values()):
        raise SystemExit("D3DX_TABLE_REFUSED the scenarios ran from different trees")
    held = sorted(next(iter(results.values()))["verdicts"]["on"])
    return {"schema": "sa2-recomp-receipt/v1", "id": "s4-d3dx-table-v1", "question": QUESTION,
            "generated_by": "tools/s4/d3dx_table_check.py",
            "command": ".venv/bin/python tools/s4/d3dx_table_check.py --write"
                       + "".join(f" --break {b}" for b in a.brk),
            **origin, "inputs": shared,
            "scenarios": {s: {k: v for k, v in r.items() if k != "inputs"}
                          | {"inputs": {k: v for k, v in r["inputs"].items() if k not in shared}}
                          for s, r in sorted(results.items())},
            "answer": ("Yes, in every scenario: " + "; ".join(held) + " hold for the switch-on "
                       "arm against the switch-off arm of one build."),
            "limits": [
                "Native calls no table method in the measured span, so no run can show a "
                "refusal of one works; harness/d3dx_table_probe.c does "
                "(Documentation/scripts/tests/test_d3dx_table_probe.py), and a tableanswer arm "
                "holds every verdict here for that reason.",
                "The tables' Releases come in the game's exit path, after the last Present, "
                "where no native capture looks; native's Release there is not measured.",
                "One run of each scenario per arm; the Metal Harbor run replays one synthetic "
                "recording on the default device profile and has no native capture."]}


def boot_profile(scns, boot: list[dict]):
    """The device profile the boot scenario runs on: the boot replay's capture's,
    checked as the boot replay's own run checks it (tick_replay.native_device_profile;
    P6's review, W2), or the default profile when no boot scenario runs."""
    if "boot" not in scns:
        return DP.DEFAULT
    if not boot:
        raise SystemExit("D3DX_TABLE_REFUSED no replay named boot is registered, so the boot "
                         "scenario has no run to stand for")
    try:
        return TR.native_device_profile(boot[0])
    except SystemExit as e:
        raise SystemExit(f"D3DX_TABLE_REFUSED the boot replay's device profile: {e}") from None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", action="append", choices=sorted(BUILDS), default=None)
    ap.add_argument("--break", dest="brk", choices=BREAKS, action="append", default=[],
                    help="also run the switch on under this SA2_D3D9_BREAK, and report its "
                         "verdicts; repeatable")
    ap.add_argument("--arms", default="off,on",
                    help="the arms to run, in order (off,on); 'off' alone only records its files")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--build-suffix", default="",
                    help="appended to each build name (d3dx-boot, d3dx-demo, d3dx-mh)")
    ap.add_argument("--timeout", type=int, default=3000,
                    help="the probe's wall-clock alarm around WinMain, in seconds, per run")
    ap.add_argument("--mh-polls", type=int, default=3000)
    ap.add_argument("--input", type=Path, default=SYNTH,
                    help="the controller recording the Metal Harbor run replays")
    ap.add_argument("--write", action="store_true",
                    help=f"write {RECEIPT.relative_to(ROOT)} when every scenario's switch-on "
                         "arm holds every verdict")
    a = ap.parse_args()
    a.out = a.out if a.out.is_absolute() else ROOT / a.out
    a.arms = [x for x in a.arms.split(",") if x]
    if any(x not in ("off", "on") for x in a.arms) or not a.arms:
        raise SystemExit(f"D3DX_TABLE_REFUSED --arms {a.arms}: off and on only")
    scns = a.scenario or ["boot", "demo", "mh"]
    boot = [r for r in json.loads(TR.REGISTRY.read_text())["replays"] if r["name"] == "boot"]
    a.boot_native_rel = boot[0]["native"]["capture"] if boot else None
    # The boot scenario is the boot replay's run, so it runs on the boot
    # capture's device profile, checked as that run checks it
    # (tick_replay.native_device_profile; P6's review, W2).
    a.boot_profile = boot_profile(scns, boot)
    a.demo_profile, a.demo_polls = None, 0
    if "demo" in scns:
        profile, problems = D.native_side(D.DEFAULT_NATIVE)
        if problems:
            raise SystemExit(f"D3DX_TABLE_REFUSED {DP.rel(D.DEFAULT_NATIVE, ROOT)} cannot stand "
                             f"for the demo: {'; '.join(problems)}")
        a.demo_profile = profile
        a.demo_polls = len(D.read_trace(D.DEFAULT_NATIVE / "sa2-stage-trace.csv")) + 300
    if "mh" in scns and not a.input.is_file():
        raise SystemExit(f"D3DX_TABLE_REFUSED no controller recording at {a.input}")
    if a.write and (sorted(scns) != sorted(BUILDS) or a.arms != ["off", "on"]
                    or a.build_suffix or a.out != OUT):
        raise SystemExit("D3DX_TABLE_REFUSED --write runs every scenario's off and on arms under "
                         "the tool's own build names and output directory")
    origin = provenance() if a.write else {}
    codes, results = {}, {}
    for s in scns:
        codes[s], results[s] = check_scenario(s, a)
    if a.write:
        RECEIPT.write_text(json.dumps(receipt(results, a, origin), indent=1, sort_keys=True)
                           + "\n")
        print(f"D3DX_TABLE wrote {rel(RECEIPT)}")
    return max(codes.values())


if __name__ == "__main__":
    raise SystemExit(main())
