#!/usr/bin/env python3
"""S2 corpus run, phase `run`: drive registered fixtures through runtime v0 and compare.

One plan per fixture (tools/s2/corpus_run/plans.py), one process per plan
(harness/plan_driver.c), then a row-by-row comparison against the fixture's own
expected words. A fixture passes only when every row returned (status 0), every
expected word matched, every registered row was driven or declared excluded by name,
and the same plan with the lifted call removed does NOT reproduce the record.

The no-call comparison is the G2b fold's answer to "what does a passing row prove?".
Every row op-stream is a sequence of pokes, register writes, one or more OP_CALLs and
peeks; strip the OP_CALLs and the peeks read back whatever the plan itself wrote. A
column that comes back equal to the fixture's expected word on every row with the call
removed is a column the fixture's pass does not depend on the lifted code for -- either
an honest "the window did not clobber this" assertion or a word the plan poked and read
straight back. Over the whole corpus 1210 of 1999 columns and 45.8 % of compared words
were in that class and nothing recorded it, so "1.3 million rows" read as a much larger
claim than it was. `informative_columns` and `informative_words` are now recorded per
fixture beside the raw counts, and a fixture the no-call run satisfies outright fails.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/run.py [--variant pc24] [--only ID ...] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import fixture_rows as FR   # noqa: E402
import plans as P           # noqa: E402

GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
REGISTRY = ROOT / "Documentation" / "receipts" / "fixture-registry.json"

# harness/plan_ops.h, op by op: the words each op occupies after its opcode, so a row's
# stream can be walked without knowing what any op means. OP_POKE is the one whose
# length depends on its own argument. An opcode this table does not know is an error
# rather than a guess, because guessing a length desynchronises the whole walk.
OP_WORDS = {P.OP_END: 1, P.OP_FILL: 4, P.OP_REG: 3, P.OP_CALL: 3, P.OP_PEEK: 3,
            P.OP_EMIT_REG: 2, P.OP_EMIT_ST0: 1, P.OP_EMIT_WORD: 2, P.OP_RESET: 1,
            P.OP_SENTINEL: 1, P.OP_EMIT_ST0_F64: 1, P.OP_PEEK_PTR: 3, P.OP_CW: 2,
            P.OP_EMIT_CW: 1}
for _name, _words in (("OP_ST", 4), ("OP_ST_F80", 5), ("OP_ST_UNSUPPLIED", 2),
                      ("OP_EMIT_ST", 2)):
    if hasattr(P, _name):                      # added by the x87-entry fold unit
        OP_WORDS[getattr(P, _name)] = _words


def walk_ops(stream: bytes):
    """Yield (opcode, start word index, word length) over one op stream."""
    words = struct.unpack(f"<{len(stream) // 4}I", stream)
    i = 0
    while i < len(words):
        op = words[i]
        if op == P.OP_POKE:
            length = 3 + (words[i + 2] + 3) // 4
        elif op in OP_WORDS:
            length = OP_WORDS[op]
        else:
            raise SystemExit(f"plan op stream: unknown opcode {op} at word {i}; "
                             f"harness/plan_ops.h has one this walk does not")
        yield op, i, length
        if op == P.OP_END:
            return
        i += length


def strip_calls(stream: bytes) -> bytes:
    """The same op stream with every OP_CALL removed: the pokes, the register writes and
    the peeks, without the lifted code in between."""
    words = struct.unpack(f"<{len(stream) // 4}I", stream)
    kept: list[int] = []
    for op, i, length in walk_ops(stream):
        if op != P.OP_CALL:
            kept += list(words[i:i + length])
    return struct.pack(f"<{len(kept)}I", *kept)


def read_out(path: Path) -> tuple[int, list[list[int]]]:
    data = path.read_bytes()
    assert data[:8] == b"SA2FXO1\0", path
    va, rows, words = struct.unpack_from("<3I", data, 8)
    return va, [list(struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)) for i in range(rows)]


def execute(plan: P.Plan, binary: Path, tag: str) -> tuple[int, list[list[int]], float, object]:
    """Encode the plan, run the driver, read the rows back."""
    pdir, odir = OUT / "plans", OUT / "out"
    pdir.mkdir(parents=True, exist_ok=True)
    odir.mkdir(parents=True, exist_ok=True)
    pf, of = pdir / f"{tag}.plan.bin", odir / f"{tag}.out.bin"
    pf.write_bytes(plan.encode())
    t0 = time.time()
    proc = subprocess.run([str(binary), str(EXE), str(pf), str(of)],
                          capture_output=True, text=True, timeout=7200)
    secs = round(time.time() - t0, 3)
    if proc.returncode != 0 or not of.exists():
        return proc.returncode, [], secs, proc
    _va, obs = read_out(of)
    return proc.returncode, obs, secs, proc


def compare(plan: P.Plan, obs: list[list[int]]) -> tuple[int, list[bool], dict | None]:
    """(rows matching, per-row ok, first divergence)."""
    matched, row_ok, first = 0, [False] * len(obs), None
    for i, (o, e) in enumerate(zip(obs, plan.expected)):
        got = tuple(o[2:])
        if o[0] == 0 and got == tuple(e):
            matched += 1
            row_ok[i] = True
        elif first is None:
            bad = next((k for k in range(len(e)) if k < len(got) and got[k] != e[k]), None)
            first = {"row": i, "status": o[0], "exit_pc": f"0x{o[1]:08x}",
                     "field": plan.columns[bad] if bad is not None and bad < len(plan.columns) else None,
                     "field_index": bad,
                     "expected": [f"0x{w:08x}" for w in e],
                     "observed": [f"0x{w:08x}" for w in got]}
    return matched, row_ok, first


def no_call_report(plan: P.Plan, binary: Path, tag: str) -> dict:
    """Drive the same plan with every OP_CALL stripped and say what still matched.

    `informative_columns` are the columns whose no-call value differs from the fixture's
    expected word on at least one row: the columns this fixture's pass actually spends on
    the lifted code. The rest are satisfied without executing it.
    """
    probe = P.Plan(f"{plan.fixture}.nocall", plan.entry_va, plan.out_words, plan.columns,
                   [strip_calls(r) for r in plan.rows], plan.expected, plan.abi,
                   globals_ops=plan.globals_ops)
    rc, obs, secs, _proc = execute(probe, binary, f"{plan.fixture}.nocall")
    if rc != 0 or not obs:
        return {"drove": False, "reason": f"the no-call variant's driver exited {rc}"}
    informative, satisfied_rows = [], []
    words = matched_words = 0
    rows_matching = 0
    for k, name in enumerate(plan.columns):
        rows_equal = 0
        for o, e in zip(obs, plan.expected):
            got = o[2:]
            if k < len(e) and k < len(got):
                if got[k] == e[k]:
                    rows_equal += 1
        satisfied_rows.append(rows_equal)
    for o, e in zip(obs, plan.expected):
        got = o[2:]
        n = min(len(e), len(got), len(plan.columns))
        words += n
        eq = sum(1 for k in range(n) if got[k] == e[k])
        matched_words += eq
        if o[0] == 0 and tuple(got[:len(e)]) == tuple(e):
            rows_matching += 1
    informative = [name for k, name in enumerate(plan.columns)
                   if satisfied_rows[k] < len(obs)]
    return {
        "drove": True,
        "seconds": secs,
        "rows": len(obs),
        "rows_matching_native_without_the_call": rows_matching,
        "columns": len(plan.columns),
        "informative_columns": len(informative),
        "columns_satisfied_without_the_call": len(plan.columns) - len(informative),
        "informative_column_names": informative,
        "words_compared": words,
        "informative_words": words - matched_words,
        "words_satisfied_without_the_call": matched_words,
        "passed_without_the_call": rows_matching == len(plan.expected) == len(obs),
    }


def registered_rows() -> dict[str, int]:
    doc = json.loads(REGISTRY.read_text())
    return {str(f["id"]): f["rows"] for f in doc["fixtures"] if isinstance(f.get("rows"), int)}


def drive(plan: P.Plan, binary: Path, limit: int | None, registered: dict | None = None,
          with_no_call: bool = True) -> dict:
    if limit:
        plan.rows = plan.rows[:limit]
        plan.expected = plan.expected[:limit]
    rc, obs, secs, proc = execute(plan, binary, plan.fixture)
    result = {"fixture": plan.fixture, "function": f"0x{plan.entry_va:08x}",
              "rows": len(plan.rows), "seconds": secs, "rc": rc,
              "abi": plan.abi, "note": plan.note,
              "driver_stderr": proc.stderr.strip().splitlines()[-4:],
              "plan": str((OUT / "plans" / f"{plan.fixture}.plan.bin").relative_to(ROOT)),
              "out": str((OUT / "out" / f"{plan.fixture}.out.bin").relative_to(ROOT))}
    if rc != 0 or not obs:
        result.update(passed=False, reason="driver failed")
        return result
    statuses: dict[str, int] = {}
    for o in obs:
        statuses[str(o[0])] = statuses.get(str(o[0]), 0) + 1
    matched, _row_ok, first = compare(plan, obs)
    traps: dict[str, int] = {}
    for o in obs:
        if o[0] != 0:
            traps[f"0x{o[1]:08x}"] = traps.get(f"0x{o[1]:08x}", 0) + 1
    passed = matched == len(plan.expected) and len(obs) == len(plan.expected)
    failures: list[str] = []
    if not passed:
        failures.append(f"{matched}/{len(plan.expected)} rows match native")
    result.update(trap_addresses=dict(sorted(traps.items(), key=lambda kv: -kv[1])),
                  rows_returned=statuses.get("0", 0), status_counts=statuses,
                  rows_matching_native=matched, first_divergence=first)

    # Every registered row is driven or declared excluded by name. Without this the
    # corpus totals could hide a shortfall inside a surplus: `ground-gather-output-v1`
    # drove 442 rows against 152 registered and `ground-candidate-executor-v1` 11 against
    # 13, so `native_rows_driven` exceeded `native_rows_registered` while four registered
    # rows had never been driven at all (G2b finding 6).
    reg = (registered or {}).get(plan.fixture)
    excluded = [dict(e) for e in plan.excluded]
    if reg is not None and not limit:
        shortfall = reg - len(plan.rows)
        result.update(rows_registered=reg, rows_driven=len(plan.rows),
                      rows_registered_not_driven=max(shortfall, 0),
                      rows_excluded_with_reason=excluded)
        if shortfall != len(excluded):
            failures.append(
                f"{reg} registered rows, {len(plan.rows)} driven, {len(excluded)} declared "
                f"excluded: every registered row must be driven or named in `Plan.excluded`")
    elif reg is None:
        result.update(rows_registered=None, rows_driven=len(plan.rows),
                      rows_registered_not_driven=None,
                      rows_excluded_with_reason=excluded)

    if with_no_call and not limit:
        nc = no_call_report(plan, binary, f"{plan.fixture}")
        result["no_call"] = nc
        if nc.get("passed_without_the_call"):
            failures.append("the same plan reproduces the record with every OP_CALL "
                            "removed, so nothing it compares is evidence about the lift")
    result["passed"] = passed and not failures
    if failures:
        result["failed_because"] = failures
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="pc24")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-call-check", dest="no_call", action="store_true", default=True)
    ap.add_argument("--skip-no-call-check", dest="no_call", action="store_false")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    binary = OUT / "build" / f"corpus_driver_{args.variant}"
    if not binary.exists():
        raise SystemExit(f"{binary} is not built; run tools/s2/corpus_run/build.py")

    registered = registered_rows()
    ids = args.only or sorted(P.BUILDERS)
    results = []
    for fid in ids:
        if fid not in P.BUILDERS:
            print(f"  {fid}: no plan builder")
            continue
        # `fixture_rows.load` picks the reader the fixture's own magic names and hands
        # back (header, rows, every decoded section). A builder that needs another of
        # the fixture's sections (the admission family reads its `cols` table, the
        # funnel family the whole shipped COL table) gets the whole map.
        header, records, secs = FR.load(fid)
        plan = P.BUILDERS[fid](records, header, secs)
        r = drive(plan, binary, args.limit, registered, args.no_call)
        results.append(r)
        mark = "PASS" if r.get("passed") else "FAIL"
        nc = r.get("no_call") or {}
        scope = f"{r.get('rows_matching_native', 0)}/{r['rows']} rows"
        if nc.get("drove"):
            scope += (f"; {nc['informative_columns']}/{nc['columns']} columns and "
                      f"{nc['informative_words']}/{nc['words_compared']} words informative")
        print(f"  {mark} {fid}: {scope} ({r['seconds']}s) {r.get('status_counts', {})}")
        for why in r.get("failed_because", []):
            print(f"       {why}")
        if r.get("first_divergence"):
            d = r["first_divergence"]
            print(f"       first divergence row {d['row']} field {d['field']} "
                  f"status {d['status']} exit {d['exit_pc']}")
            print(f"         expected {d['expected']}")
            print(f"         observed {d['observed']}")
    js = args.out or (OUT / f"run.{args.variant}.json")
    js.write_text(json.dumps({"variant": args.variant, "results": results}, indent=1) + "\n")
    cols = sum((r.get("no_call") or {}).get("columns", 0) for r in results)
    info = sum((r.get("no_call") or {}).get("informative_columns", 0) for r in results)
    words = sum((r.get("no_call") or {}).get("words_compared", 0) for r in results)
    iwords = sum((r.get("no_call") or {}).get("informative_words", 0) for r in results)
    if cols:
        print(f"informative: {info}/{cols} columns, {iwords}/{words} compared words "
              f"({100.0 * iwords / words:.1f}%)")
    print(f"{sum(1 for r in results if r.get('passed'))}/{len(results)} fixtures passed -> {js}")


if __name__ == "__main__":
    main()
