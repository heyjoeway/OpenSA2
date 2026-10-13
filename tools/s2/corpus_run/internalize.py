#!/usr/bin/env python3
"""Fold remill's per-module missing-block stubs so 147 lifted modules link as one program.

The S2 wrap journal records this as an S3 blocker: *"Each lifted `.ll` defines stubs
named `sub_<hex>.<n>` -- LLVM's collision rename of remill's missing-block trampolines
-- while the real call site uses the plain `@sub_<hex>` declare. They are unreachable,
but they are external, so several `.ll` files define the same symbol and a whole-image
link would collide."* Linking the fixture-covered closure needs it solved now, not in S3.

Almost every such stub has the same body: store the incoming `program_counter` into
`State.gpr.rip`, then `tail call @__remill_missing_block(state, pc, memory)`. remill
passes the *call target* as the pc (the S2 wrap receipt established that), so the stub
carries all the information a dispatch needs. This rewriter therefore

  - deletes every `define ... @sub_<hex>.<n>(...)` body **whose body is exactly a
    missing-block trampoline**, and redirects its references to `@__sa2_dispatch_stub`,
  - deletes every `declare ... @sub_<hex>.<n>` line,
  - declares `__sa2_dispatch_stub` once.

`harness/plan_driver.c` defines it as `sa2_ft_dispatch(pc, ...)`: a call whose target is
lifted and linked now *reaches* it instead of trapping, and one whose target is not
traps with its own address exactly as before.

**Not a trampoline: kept, not deleted** (G2 finding 4). The "assert the body is exactly a
trampoline" above was not an assertion. The body was appended to `unexpected_stub_bodies`
and deleted anyway, and its references were redirected to the dispatch stub. Two of the
165 corpus modules hit it: `@sub_7a8133.1` in `sub_007A80DD` and `@sub_7b9aa8.8` in
`sub_007B99AC` are real lifted blocks -- stack adjustments, `__remill_write_memory_32`,
a call to another lifted function -- that remill emitted as sub-traces and LLVM renamed.
Deleting them was harmless only because both addresses happen to be lifted as their own
modules too, so the redirected dispatch reached an identical body. That is a property of
this closure, not of the rewrite.

Such a body is now given `internal` linkage and kept, and its references are left
pointing at it: the module calls its own block rather than dispatching to a separately
lifted copy. `internal` is what solves the original problem (two modules defining the
same external symbol) without deleting guest code. `--delete-unexpected` reproduces the
old behaviour for an A/B, and `build.py` exits non-zero when anything was deleted that
way.

**Then the speed passes** (S5). The folded text goes through
`tools/s2/corpus_run/speed_rewrite.py` before it is returned: the float80 spill calls
lose `nobuiltin`, the intrinsics the runtime defines as an identity or a zero become
that value, the spliced x87 helpers become `alwaysinline`, a lifted function the
selection rule picks works on a local copy of its State, and that function's guest-memory
accesses become loads and stores behind the runtime's bounds (`state_local.py`). It runs
here, at the end of the fold, because every fold goes through this function --
`compile_all.py` (the map), `lift_arms.py` (the arm traces), `lift_extra.py` (the
extras), `build.py` (the fixture corpus) and the emitter determinism gate -- so no fold
leaves out the passes. That does not make every object current. An object folded
before a change to the fold code (this file, `speed_rewrite.py`, `state_local.py`, the
PC24 pass) stays as it was until its fold runs
again: `tools/s4/rebuild_image.py` rebuilds every object the link reads and stamps the
fold code it used, and `Documentation/scripts/check_run_reach.py` fails while the stamp
is not the tree's. `build.py` folds again any module whose cached fold differs from what
the current code makes, so the fixture corpus tests the current fold once `build.py` has
run since the change; `check_fixture_corpus.py` and `check_controls.py` run the driver it
last linked. The two fixtures `tools/s2/wrap/wrap.py` builds are compiled without the
fold at all. Its counts are `speed_spill`, `speed_intrin`, `speed_pcround`,
`speed_local` (with `speed_local_kept` and `speed_local_refused`) and `speed_fast` (with
`speed_fast_kept`) in the returned statistics.

Usage:
  .venv/bin/python tools/s2/corpus_run/internalize.py <in.ll> <out.ll> [--json stats.json]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import speed_rewrite  # tools/s2/corpus_run/speed_rewrite.py, beside this file

STUB = re.compile(r"sub_[0-9a-fA-F]+\.\d+")
DEFINE = re.compile(r"^define\s[^@\n]*@(sub_[0-9a-fA-F]+\.\d+)\(")
DECLARE = re.compile(r"^declare\s[^@\n]*@(sub_[0-9a-fA-F]+\.\d+)\(")
DISPATCH = "__sa2_dispatch_stub"


class UnexpectedStubBody(Exception):
    """A `sub_<hex>.<n>` define whose body is not a missing-block trampoline was deleted.

    Only raised under `--delete-unexpected`, which exists to reproduce the pre-fold
    behaviour. The default keeps such a body.
    """


def rewrite(text: str, delete_unexpected: bool = False) -> tuple[str, dict]:
    """Fold the collision-renamed missing-block stubs.

    A define whose body is a missing-block trampoline is deleted and its references are
    redirected to `@__sa2_dispatch_stub`. A define whose body is anything else is kept
    with `internal` linkage and its references are left alone. `delete_unexpected=True`
    restores the pre-G2 behaviour (delete and redirect regardless) and raises
    `UnexpectedStubBody` on the first such body, so the old path cannot run silently.
    The folded text then goes through `speed_rewrite.rewrite` (the module docstring).
    """
    lines = text.splitlines()
    stats = {"stub_defines_removed": 0, "stub_declares_removed": 0,
             "references_redirected": 0, "unexpected_stub_bodies": [],
             "stub_bodies_kept_internal": [], "deleted_unexpected": []}

    # Pass 1: classify every stub define, so pass 2 knows which symbols may be redirected.
    bodies: dict[str, tuple[int, int, bool]] = {}   # name -> (first line, last line, is_trampoline)
    i = 0
    while i < len(lines):
        m = DEFINE.match(lines[i])
        if m:
            j = i + 1
            while j < len(lines) and lines[j] != "}":
                j += 1
            joined = "\n".join(lines[i + 1:j])
            trampoline = "__remill_missing_block" in joined and "call" not in joined.replace(
                "tail call ptr @__remill_missing_block", "").replace(
                "call ptr @__remill_missing_block", "")
            bodies[m.group(1)] = (i, j, trampoline)
            i = j + 1
            continue
        i += 1

    keep = {n for n, (_, _, t_) in bodies.items() if not t_ and not delete_unexpected}
    stats["unexpected_stub_bodies"] = sorted(n for n, (_, _, t_) in bodies.items() if not t_)
    stats["stub_bodies_kept_internal"] = sorted(keep)
    if delete_unexpected and stats["unexpected_stub_bodies"]:
        stats["deleted_unexpected"] = stats["unexpected_stub_bodies"]
        raise UnexpectedStubBody(
            "not missing-block trampolines and deleted anyway: "
            + ", ".join(stats["unexpected_stub_bodies"]))

    def redirect(line: str) -> str:
        """Rewrite references to deleted stub symbols only; a kept symbol stays itself."""
        def sub(mo: re.Match) -> str:
            if mo.group(0) in keep:
                return mo.group(0)
            stats["references_redirected"] += 1
            return DISPATCH
        return STUB.sub(sub, line)

    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = DEFINE.match(line)
        if m:
            start, stop, _t = bodies[m.group(1)]
            if m.group(1) in keep:
                # `define ptr @sub_x.1(` -> `define internal ptr @sub_x.1(`: the one change
                # the whole-program link needs, and no guest code is discarded.
                out.append(line.replace("define ", "define internal ", 1))
                out += [redirect(b) for b in lines[start + 1:stop]]
                out.append("}")
            else:
                stats["stub_defines_removed"] += 1
            i = stop + 1
            continue
        if DECLARE.match(line):
            stats["stub_declares_removed"] += 1
            i += 1
            continue
        out.append(redirect(line))
        i += 1
    body = "\n".join(out)
    if stats["references_redirected"]:
        body += f"\n\ndeclare ptr @{DISPATCH}(ptr noalias, i32, ptr noalias)\n"
    text, speed = speed_rewrite.rewrite(body + "\n")
    stats.update({f"speed_{name}": count for name, count in speed.items()})
    return text, stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--delete-unexpected", action="store_true",
                    help="delete a stub body that is not a trampoline, as the pre-G2 "
                         "rewriter did; raises instead of doing it silently")
    args = ap.parse_args()
    text, stats = rewrite(args.src.read_text(errors="replace"),
                          delete_unexpected=args.delete_unexpected)
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(text)
    if args.json:
        args.json.write_text(json.dumps(stats, indent=1) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
