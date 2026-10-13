#!/usr/bin/env python3
"""Lift jump-table arms as their own remill traces (S3 arm-seeding experiment).

Subtitle: seed remill with `--entry_address` per arm, through the same PC24
pass, fold and compile every other whole-image module goes through.

The whole-image lift (`tools/s3/whole_image/lift_all.py`) gives remill one
entry per map function, and remill's recursive descent stops at an indirect
branch, so a jump table's arms are never decoded. This tool re-lifts the same
owner byte range once per arm with `--entry_address <arm>`, which is the only
knob `remill-lift-14` exposes for it.

Everything after the lifter is imported from the existing pipeline, unchanged:
`pc24pass.instrument` with both helpers exactly as `lift_all.py` passes them,
then `internalize.rewrite`, then `zig cc -target x86_64-linux-musl -O2 -w -c`.

Output (all git-ignored) under `private/generated/s3/arms/`:
  lift/<tag>/sub_XXXXXXXX.ll          raw remill IR for the arm trace
  lift/<tag>/sub_XXXXXXXX.pc24.ll     after the PC24 pass
  fold/<tag>/sub_XXXXXXXX.pc24.ll     after internalize
  build/<tag>/ir_XXXXXXXX.o           the object
  <tag>.jsonl                         one record per arm, written as it lands

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/lift_arms.py --owner 0x004015f0 --tag fun4015f0
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "arms"

MISSING_BLOCK_CALL = re.compile(r"\bcall [^\n]*@__remill_missing_block\b")
ERROR_CALL = re.compile(r"\bcall [^\n]*@__remill_error\b")
JUMP_CALL = re.compile(r"\bcall [^\n]*@__remill_jump\b")
RET_CALL = re.compile(r"\bcall [^\n]*@__remill_function_return\b")
FNCALL_CALL = re.compile(r"\bcall [^\n]*@__remill_function_call\b")
DEFINE = re.compile(r"^define [^\n]*@([\w.$]+)\(", re.M)
# remill emits exactly one `store i32 %k, ptr %PC` per decoded guest
# instruction (it publishes the instruction's own address into State.PC before
# lifting it), which is the per-instruction marker the S3 interrogation counted.
PC_STORE = re.compile(r"^  store i32 %\d+, ptr %PC, align 4$", re.M)
DEF_SPLIT = re.compile(r"^define [^\n]*@([\w.$]+)\(", re.M)


def decoded_per_define(text: str) -> dict[str, int]:
    parts = DEF_SPLIT.split(text)
    return {parts[i]: len(PC_STORE.findall(parts[i + 1]))
            for i in range(1, len(parts), 2)}


def owner_range(owner: int) -> tuple[int, int]:
    """The `--to-next-entry` byte range: the adopted whole-image bound."""
    fmap = C.by_address()
    starts = sorted(fmap)
    i = bisect.bisect_right(starts, owner)
    end = starts[i] if i < len(starts) else owner + fmap[owner]["size"]
    return owner, end


def arms_of(owner: int) -> list[int]:
    fn = C.by_address()[owner]
    out: list[int] = []
    for jt in fn.get("jump_tables") or []:
        for t in jt.get("targets", []):
            ta = int(t, 16)
            if ta not in out:
                out.append(ta)
    return out


def lift_one(owner: int, end: int, entry: int, lldir: Path, image: bytes) -> dict:
    import pc24pass  # tools/spike/pc24pass, read-only

    raw = image[owner - C.IMAGE_BASE:end - C.IMAGE_BASE]
    ll = lldir / f"sub_{entry:08X}.ll"
    inner = (f"mount --bind {C.REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 "
             f"--arch x86 --os windows --address {owner:#x} --entry_address {entry:#x} "
             f"--bytes {raw.hex()} --ir_out {ll}")
    cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        rc, err = p.returncode, p.stderr + p.stdout
    except subprocess.TimeoutExpired:
        rc, err = -1, "timeout after 900 s"
    lift_s = round(time.time() - t0, 3)
    text = ll.read_text(errors="replace") if ll.exists() else ""
    per_define = decoded_per_define(text)
    rec = {
        "owner": f"0x{owner:08x}", "entry": f"0x{entry:08x}",
        "bytes": len(raw), "range_end": f"0x{end:08x}",
        "rc": rc, "lift_seconds": lift_s,
        "defines": DEFINE.findall(text),
        "lifted": rc == 0 and re.search(rf"^define [^\n]*@sub_{entry:x}\(", text, re.M) is not None,
        "ll_lines": text.count("\n"),
        # remill publishes PC once per decoded instruction *and* once per exit
        # block (a `ret`, an indirect branch, an edge out of the byte range), so
        # this is instructions + exits. `tools/s3/arms/cost.py` owns the
        # instruction count and validates itself against this number; the raw
        # marker count is recorded here rather than a count that looks like an
        # instruction count and is not one.
        "pc_markers_in_define": per_define.get(f"sub_{entry:x}", 0),
        "pc_markers_per_define": per_define,
        "missing_block_calls": len(MISSING_BLOCK_CALL.findall(text)),
        "remill_error_calls": len(ERROR_CALL.findall(text)),
        "remill_jump_calls": len(JUMP_CALL.findall(text)),
        "remill_function_call_calls": len(FNCALL_CALL.findall(text)),
        "remill_function_return_calls": len(RET_CALL.findall(text)),
        "error_head": "" if rc == 0 else "\n".join(err.strip().splitlines()[:3]),
    }
    if not rec["lifted"]:
        return rec
    t1 = time.time()
    out, st = pc24pass.instrument(text, C.HELPER_LL.read_text(),
                                  C.INT_HELPER_LL.read_text(), True, True, True)
    (lldir / f"sub_{entry:08X}.pc24.ll").write_text(out)
    rec["pass"] = {"ok": True, "out_lines": out.count("\n"),
                   "ops_instrumented": st.get("ops_instrumented"),
                   "fist_rewrites": st.get("fist_rewrites"),
                   "fld_quiet_fixed": st.get("fld_quiet_fixed"),
                   "fld_quiet_refused": st.get("fld_quiet_refused")}
    if st.get("fld_quiet_refused"):
        # The module would keep remill's quieting of every FLD m32/m64 while IE is set
        # (pc24pass.py, "FLD"). An exception, not SystemExit: lift_arms_image.py and
        # rebuild_image.py call this in a process pool, which hands an exception back.
        raise RuntimeError(f"the PC24 pass refused the FLD quieting fix in arm trace "
                           f"0x{entry:08x} (owner 0x{owner:08x}): "
                           f"{st['fld_quiet_refused_sites'][:3]}")
    rec["pass_seconds"] = round(time.time() - t1, 3)
    # post-pass trap counts, which is what the module actually carries
    rec["after_pass"] = {
        "missing_block_calls": len(MISSING_BLOCK_CALL.findall(out)),
        "remill_error_calls": len(ERROR_CALL.findall(out)),
        "remill_jump_calls": len(JUMP_CALL.findall(out)),
        "remill_function_return_calls": len(RET_CALL.findall(out)),
    }
    return rec


def fold_and_compile(entry: int, lldir: Path, folddir: Path, objdir: Path) -> dict:
    import internalize  # tools/s2/corpus_run/internalize.py, read-only
    import spikelib as S  # noqa

    src = lldir / f"sub_{entry:08X}.pc24.ll"
    dst = folddir / f"sub_{entry:08X}.pc24.ll"
    obj = objdir / f"ir_{entry:08X}.o"
    text, st = internalize.rewrite(src.read_text(errors="replace"))
    dst.write_text(text)
    rec = {"folded": True,
           "fold": {k: st[k] for k in ("stub_defines_removed", "stub_declares_removed",
                                       "references_redirected")},
           "ir_bytes": len(text),
           "after_fold": {
               "missing_block_calls": len(MISSING_BLOCK_CALL.findall(text)),
               "remill_error_calls": len(ERROR_CALL.findall(text)),
               "remill_jump_calls": len(JUMP_CALL.findall(text)),
               "remill_function_return_calls": len(RET_CALL.findall(text)),
               "dispatch_stub_calls": text.count("@__sa2_dispatch_stub"),
           },
           "defines_after_fold": DEFINE.findall(text)}
    cmd = [str(S.ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-w", "-c",
           str(dst), "-o", str(obj)]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=str(objdir))
    rec["compile_seconds"] = round(time.time() - t0, 3)
    rec["compiled"] = p.returncode == 0 and obj.exists()
    rec["object_bytes"] = obj.stat().st_size if obj.exists() else 0
    rec["compile_error_head"] = "" if rec["compiled"] else (p.stderr + p.stdout)[:600]
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--tag", required=True)
    args = ap.parse_args()

    owner = int(args.owner, 16)
    start, end = owner_range(owner)
    arms = arms_of(owner)
    lldir = OUT / "lift" / args.tag
    folddir = OUT / "fold" / args.tag
    objdir = OUT / "build" / args.tag
    for d in (lldir, folddir, objdir):
        d.mkdir(parents=True, exist_ok=True)
    log = OUT / f"{args.tag}.jsonl"
    image = C.memory_image()

    print(f"owner {owner:#010x} range {start:#x}..{end:#x} ({end - start} bytes), "
          f"{len(arms)} arms: {[hex(a) for a in arms]}", flush=True)
    with log.open("w") as fh:          # each record written as it lands
        for entry in arms:
            rec = lift_one(owner, end, entry, lldir, image)
            if rec["lifted"]:
                rec.update(fold_and_compile(entry, lldir, folddir, objdir))
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            print(f"  {entry:#010x} rc={rec['rc']} lifted={rec['lifted']} "
                  f"pc_markers={rec['pc_markers_in_define']} "
                  f"defines={rec['defines']} "
                  f"mb={rec['missing_block_calls']} err={rec['remill_error_calls']} "
                  f"jmp={rec['remill_jump_calls']} ret={rec['remill_function_return_calls']} "
                  f"compiled={rec.get('compiled')}", flush=True)
    print(f"-> {log}")


if __name__ == "__main__":
    main()
