#!/usr/bin/env python3
"""Build the S4 driver once and run it under a named set of environments.

Subtitle: `link_s4.py` with the generated edge table and the extra-block
objects already filled in, plus one run per row of a matrix, each run's JSON
kept under its own name.

Why a tool and not a command line
---------------------------------
Driving `link_s4.py` for this slice means repeating the edge table, the extra
functions file and twenty-nine `--extra-o` paths every time, which is the step
rule 13 says becomes a tool before it is repeated a third time. It had been
repeated four times by the time this was written, and once with a stale
`--extra-c`, which produces a binary that links and runs and is not the one
anybody meant to test.

It also keeps the discipline the previous two slices paid for:

  * the compile *and* the link status are checked before any run JSON is read,
    because a failed compile leaves the previous binary in place and
    `link_s4.py` will happily run it;
  * every run's JSON is written, including the failing ones, because the run
    that gets deleted is always the one that failed;
  * each run's mtime is checked against the moment that run started, so a JSON
    left by an earlier invocation can never be reported as this one's.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/loop_run.py --name loop.next+allarms \\
      --run baseline \\
      --run 'quit_at_8|SA2_PUMP_POLLS=8' \\
      --run 'close_at_4|SA2_PUMP_INJECT=4:0x10'

A `--run` is `label[|VAR=VALUE[|VAR=VALUE...]]`. The separator is `|` and not
`:` because `SA2_PUMP_INJECT` uses `:` inside its own value, and a separator
that also occurs inside values is a parser that silently drops half a setting.

The shipped executable
----------------------
Every run loads the unpacked executable unless `--exe shipped` says otherwise.
Then the build links the code-section overlay table (`--exe-overlay [TABLE]`,
passed through to `link_s4.py`; runtime/sa2_exe_overlay.h), each run's first
argument is the shipped `private/exe/sonic2app.exe` and its environment holds
SA2_EXE_KIND=shipped, which is what makes the loader map that file
(runtime/guest_memory.h). Nothing else may set SA2_EXE_KIND: a `--run` that does
is refused, a value in this tool's own environment is dropped, and every run's
record must say the loader mapped the executable asked for (`exe.kind`,
harness/crt_probe.c), or the tool exits 3 after writing the matrix.
"""
from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

HERE = Path(__file__).resolve().parent
PY = C.ROOT / ".venv" / "bin" / "python"
LINKDIR = C.GEN / "s4" / "link"
RUNDIR = C.GEN / "s4" / "run"

SHIPPED_EXE = C.ROOT / "private" / "exe" / "sonic2app.exe"
EXE_KINDS = ("unpacked", "shipped")
EXE_KIND_VAR = "SA2_EXE_KIND"
# What a run's own settings choose about the image it runs, never the caller's shell: held equal
# to reach_receipt.RUN_IMAGE_VARS (not imported, so that loop_run stays importable on its own).
RUN_IMAGE_VARS = (EXE_KIND_VAR, "SA2_GUEST_POISON")

BASE_ENV = {
    "SA2_INSTALL": "private/install",
    "SA2_VFS_WRITE_ROOT": "private/generated/s4/vfsroot",
}

# The d3d9 device's closed world (runtime/sa2_d3d9.c; the user's ruling of 2026-10-09,
# item 1) reports a method off the M7 list on the game's stderr in lines that start so:
# in live play, once per method when it is first called and in a summary at exit. A
# live session keeps this tool's output as its game.log (tools/s5/play_live.py), not
# the game's stderr, so run_game copies each such line to stdout as it arrives.
CLOSED_WORLD_PREFIX = "*** d3d9 CLOSED WORLD"
STDERR_TAIL = 1500      # characters of the game's stderr a row keeps when it wrote no JSON
READER_JOIN_S = 5       # how long a killed game's pipes are waited for before the timeout is raised


def run_game(argv: list[str], env: dict, timeout: float) -> tuple[int, str]:
    """The game's exit status and the tail of its stderr, as subprocess.run with
    capture_output and `timeout` gave them, with stderr read while the game runs: every
    closed-world line goes to stdout at once, and only the last STDERR_TAIL characters
    are held, so a session of hours does not hold all it wrote. On the timeout the game
    is killed and subprocess.TimeoutExpired raised, as subprocess.run did, after waiting
    at most READER_JOIN_S for its pipes to close."""
    p = subprocess.Popen(argv, cwd=str(C.ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, errors="replace", env=env)
    tail: collections.deque[str] = collections.deque()
    held = [0]

    def read_stderr() -> None:
        for line in p.stderr:
            if line.startswith(CLOSED_WORLD_PREFIX):
                print(line.rstrip("\n"), flush=True)
            tail.append(line)
            held[0] += len(line)
            while len(tail) > 1 and held[0] - len(tail[0]) >= STDERR_TAIL:
                held[0] -= len(tail.popleft())

    def drain_stdout() -> None:
        for _line in p.stdout:
            pass

    readers = [threading.Thread(target=read_stderr, daemon=True),
               threading.Thread(target=drain_stdout, daemon=True)]
    for t in readers:
        t.start()
    try:
        p.wait(timeout=timeout)
    except BaseException:
        p.kill()
        p.wait()
        # A process the game started could hold its pipes open past its kill; the readers are
        # daemons, so the timeout is raised within READER_JOIN_S rather than at that EOF.
        deadline = time.monotonic() + READER_JOIN_S
        for t in readers:
            t.join(max(0.0, deadline - time.monotonic()))
        raise
    for t in readers:
        t.join()
    return p.returncode, "".join(tail)[-STDERR_TAIL:]


def closed_world_shown(cw: dict | None) -> str:
    """The '== label' line's account of the run record's d3d9.closed_world block."""
    if not cw:
        return ""
    if not cw.get("unlisted_methods"):
        return f" closed_world={cw.get('mode')}: none off the M7 list"
    names = "; ".join((f"{u['interface']}::{u['method']}" if u.get("method") else
                       f"{u['interface']} slot {u['slot']}") + f" x{u['calls']} ({u['outcome']})"
                      for u in cw.get("unlisted", []))
    return (f" closed_world={cw.get('mode')}: {cw['unlisted_methods']} method(s) off the M7 "
            f"list, {cw.get('unlisted_calls')} call(s): {names}")


def sh(cmd: list[str], timeout: int = 3600) -> subprocess.CompletedProcess:
    print("$ " + " ".join(str(c) for c in cmd).replace(str(C.ROOT) + "/", "")[:400], flush=True)
    p = subprocess.run([str(c) for c in cmd], cwd=str(C.ROOT), capture_output=True,
                       text=True, timeout=timeout)
    for line in (p.stdout or "").strip().splitlines()[-4:]:
        print("  " + line[:300], flush=True)
    if p.returncode and p.stderr:
        print("  ! " + p.stderr.strip().splitlines()[-1][:400], flush=True)
    return p


def link_args(tag: str, name: str, main: str, hooks: list[str] | None = None,
              wraps: list[str] | None = None, exe_overlay: Path | str | None = None) -> list[str]:
    """`link_s4.py`'s arguments for this build.

    `hooks` are host C sources linked after the generated inputs, and `wraps`
    the lifted symbols they wrap. Both default to none, which is the reach
    build exactly: the hooks go last so the generated inputs keep their
    `extra<k>_` object names whether or not a hook is present. `exe_overlay`
    is the shipped executable's overlay table, passed to link_s4.py's
    `--exe-overlay`; None, the default, links none.
    """
    extra_state = LINKDIR / f"extra_functions.{tag}.json"
    args = [PY, HERE / "link_s4.py", "--tag", tag, "--main", main, "--name", name,
            "--extra-c", LINKDIR / f"mb_edges.{tag}.c"]
    if not (LINKDIR / f"mb_edges.{tag}.c").is_file():
        raise SystemExit(f"no generated edge table for {tag}; run tools/s4/mb_edges.py first")
    if extra_state.is_file():
        st = json.loads(extra_state.read_text())
        if st.get("objects"):
            args += ["--extra-c", LINKDIR / f"extra_functions.{tag}.c"]
            for o in st["objects"]:
                args += ["--extra-o", o]
    for h in hooks or []:
        args += ["--extra-c", h]
    for w in wraps or []:
        args += ["--wrap", w]
    if exe_overlay is not None:
        args += ["--exe-overlay", exe_overlay]
    return args


def exe_argv(kind: str) -> tuple[Path, dict[str, str]]:
    """The executable a run of `kind` is handed as its first argument, and what its
    environment adds: the unpacked file and nothing, or the shipped file and
    SA2_EXE_KIND=shipped (runtime/guest_memory.h)."""
    if kind == "shipped":
        return SHIPPED_EXE, {EXE_KIND_VAR: "shipped"}
    if kind == "unpacked":
        return C.EXE, {}
    raise SystemExit(f"--exe {kind!r}: not one of {EXE_KINDS}")


def exe_problem(record: dict, kind: str) -> str | None:
    """Why a run record does not show the loader mapped the executable `kind` asked
    for (harness/crt_probe.c `exe`), or None. A record with no `exe` block is from a
    probe older than the block, which is accepted only for the unpacked default."""
    exe = record.get("exe")
    if not isinstance(exe, dict):
        return None if kind == "unpacked" else "the run record has no exe block"
    if exe.get("kind") != kind:
        refusal = ((exe.get("overlay") or {}).get("refusal") or "") if isinstance(
            exe.get("overlay"), dict) else ""
        return (f"the run asked for the {kind} executable and its record says the loader "
                f"mapped the {exe.get('kind')!r} one" + (f" ({refusal})" if refusal else ""))
    return None


def build_ok(name: str) -> bool:
    st_path = LINKDIR / f"main.{name}.json"
    if not st_path.is_file():
        print("no link state; refusing to run", flush=True)
        return False
    st = json.loads(st_path.read_text())
    if not st.get("compile_ok", False):
        bad = {k: v for k, v in st.get("compile", {}).items() if v.get("rc")}
        print("the compile failed (" + ", ".join(bad) + "); refusing to run the previous binary",
              flush=True)
        for v in bad.values():
            print(v.get("stderr_tail", "")[-2000:], flush=True)
        return False
    if st.get("link", {}).get("rc"):
        print("the link failed; refusing to run the previous binary:\n"
              + st["link"].get("stderr_tail", "")[-2000:], flush=True)
        return False
    return True


def index_for_m7(out: Path) -> None:
    """Add this run's record to M7's census of every game run as soon as it is written
    (tools/s4/m7_inputs.py index_run_record, the user's ruling of 2026-10-09, item 1: "Count the
    device's per-method table from every game run"). This tool deletes a label's JSON before it
    runs that label again, so a run it does not index now could be replaced before the census's
    next update reads it; a failure here is printed, never fatal to the run."""
    try:
        sys.path.insert(0, str(HERE))
        import m7_inputs  # noqa: E402
        m7_inputs.index_run_record(C.ROOT, out, by="tools/s4/loop_run.py")
    except Exception as e:  # noqa: BLE001 (the run's own result must not depend on the census)
        print(f"  ! M7 census: {out.name} was not indexed ({e}); the census's next update reads it "
              f"only if it is still there", flush=True)


def main() -> None:
    sys.path.insert(0, str(HERE))
    import link_s4 as LS  # noqa: E402 (here: only the command line needs its default table)
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--name", default="loop.next+allarms")
    ap.add_argument("--main", default="harness/crt_probe.c")
    ap.add_argument("--run", action="append", default=[],
                    help="label[|VAR=VALUE...]; repeatable")
    ap.add_argument("--probe-arg", action="append", default=[])
    ap.add_argument("--hook", action="append", default=[],
                    help="a host C source linked into the build, e.g. "
                         "harness/replay/wrap_tick_probe.c; repeatable")
    ap.add_argument("--wrap", action="append", default=[],
                    help="a lifted symbol the hooks wrap (link_s4.py --wrap); repeatable")
    ap.add_argument("--exe", choices=EXE_KINDS, default="unpacked",
                    help="which executable every run loads: the unpacked one (default) or the "
                         "shipped sonic2app.exe through the overlay (SA2_EXE_KIND=shipped), "
                         "which needs --exe-overlay")
    ap.add_argument("--exe-overlay", nargs="?", default=None, const=str(LS.EXE_OVERLAY_TABLE),
                    help="link the shipped executable's overlay table (link_s4.py "
                         "--exe-overlay; default table "
                         f"{LS.EXE_OVERLAY_TABLE.relative_to(C.ROOT)})")
    ap.add_argument("--out-prefix", default=None, help="default: --name")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()
    if args.exe == "shipped" and args.exe_overlay is None:
        raise SystemExit("--exe shipped needs a build that links the overlay table: pass "
                         "--exe-overlay [TABLE]")

    RUNDIR.mkdir(parents=True, exist_ok=True)
    prefix = args.out_prefix or args.name

    if not args.no_build:
        cmd = (link_args(args.tag, args.name, args.main, args.hook, args.wrap, args.exe_overlay)
               + ["--phase", "compile"])
        if args.rebuild:
            cmd += ["--rebuild"]
        sh(cmd)
        sh(link_args(args.tag, args.name, args.main, args.hook, args.wrap, args.exe_overlay)
           + ["--phase", "link"])
    if not build_ok(args.name):
        raise SystemExit(2)
    linked_overlay = (json.loads((LINKDIR / f"main.{args.name}.json").read_text())
                      .get("link") or {}).get("exe_overlay")
    if args.exe == "shipped" and not linked_overlay:
        print("--exe shipped, and the build links no overlay table (link.exe_overlay); "
              "refusing to run", flush=True)
        raise SystemExit(2)
    exe_path, exe_env = exe_argv(args.exe)

    binary = LINKDIR / "mains" / args.name / "sa2_main"
    rows = []
    for spec in args.run or ["baseline"]:
        parts = spec.split("|")
        label, env = parts[0], dict(BASE_ENV)
        for kv in parts[1:]:
            if "=" not in kv:
                raise SystemExit(f"--run {spec!r}: {kv!r} is not VAR=VALUE")
            k, v = kv.split("=", 1)
            if k == EXE_KIND_VAR:
                raise SystemExit(f"--run {spec!r}: {EXE_KIND_VAR} is --exe's to set")
            env[k] = v
        env.update(exe_env)
        out = RUNDIR / f"{prefix}.{label}.json"
        # The output is *removed* before the run rather than compared against
        # the round's start time afterwards. The mtime comparison is what this
        # tool did first, inherited from `crt_run.py`, and it is not a
        # comparison: on this filesystem a file written after `time.time()` was
        # sampled can carry an mtime up to six milliseconds *earlier*, because
        # inode timestamps come from the kernel's coarse cached clock while
        # `time.time()` reads the fine one. Two rows of a matrix that finished
        # inside one of those ticks were reported as "the probe wrote no JSON"
        # when both had written one. Deleting first makes existence the proof
        # and removes the clock from the question entirely.
        if out.is_file():
            out.unlink()
        started = time.time()
        import os
        # SA2_EXE_KIND is --exe's alone, and SA2_GUEST_POISON an explicit --env's: a value
        # inherited from the caller's environment is dropped (reach_receipt.RUN_IMAGE_VARS), so a
        # default run loads the unpacked file with the fast path on, whatever the shell holds.
        inherited = {k: v for k, v in os.environ.items() if k not in RUN_IMAGE_VARS}
        rc, stderr_tail = run_game([str(binary), str(exe_path), "--json", str(out)] + args.probe_arg,
                                   dict(inherited, **env), args.timeout)
        row = {"label": label, "env": {k: v for k, v in env.items() if k not in BASE_ENV},
               "rc": rc, "json": str(out.relative_to(C.ROOT)),
               "seconds": round(time.time() - started, 2)}
        if not out.is_file():
            row["error"] = "the probe wrote no JSON for this run"
            row["stderr_tail"] = stderr_tail
        else:
            index_for_m7(out)
            d = json.loads(out.read_text())
            row.update(ok=d["ok"], calls=d["calls"]["total"],
                       exit_pc=d["trap"]["exit_pc"], note=d["trap"]["note"])
            pump = d.get("pump", {})
            row["pump"] = {k: pump.get(k) for k in
                           ("iterations", "empty_polls", "message_polls", "gets",
                            "dispatches", "guest_wndproc_calls", "quit_by_host",
                            "quit_by_guest", "postquit_caller", "status")}
            row["asserts_failed"] = sorted(k for k, v in d.get("assert", {}).items() if not v)
            wrong_exe = exe_problem(d, args.exe)
            if wrong_exe:
                row["exe_error"] = wrong_exe
            cw = (d.get("d3d9") or {}).get("closed_world")
            if cw is not None:
                row["closed_world"] = {k: cw.get(k) for k in
                                       ("mode", "unlisted_methods", "unlisted_calls", "unlisted")}
        rows.append(row)
        print(f"== {label}: rc={row['rc']} ok={row.get('ok')} calls={row.get('calls')} "
              f"{str(row.get('note'))[:140]}{closed_world_shown(row.get('closed_world'))}",
              flush=True)

    summary = RUNDIR / f"{prefix}.matrix.json"
    doc = {"written": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "binary": str(binary.relative_to(C.ROOT)),
           "sha256": C.sha256(binary),
           "rows": rows}
    if args.exe != "unpacked" or linked_overlay:
        # Kept only beside the overlay, so a default run's matrix is the one it always was.
        doc.update(exe={"kind": args.exe, "path": str(exe_path.relative_to(C.ROOT))},
                   exe_overlay=linked_overlay)
    summary.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {summary}")
    wrong = [f"{r['label']}: {r['exe_error']}" for r in rows if r.get("exe_error")]
    if wrong:
        print("LOOP_RUN_WRONG_EXE " + "; ".join(wrong), flush=True)
        raise SystemExit(3)


if __name__ == "__main__":
    main()
