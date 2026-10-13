#!/usr/bin/env python3
"""Register the inherited fixture corpus as M3's denominator (design 3.7, 8, S2).

Reads every fixture under `private/fixtures/LocalAssets-SA2`, decodes it with
`corpus.py`'s format table, classifies whether the spike harness can drive its
function today, and writes

  Documentation/receipts/fixture-registry.json    (sa2-recomp-fixture-registry/v1)
  Documentation/receipts/s2-fixture-corpus-v1.json (sa2-recomp-receipt/v1)

The registry names files, counts and addresses only: no fixture row and no
shipped byte crosses into the tree (design 10, `check_boundary.py`).

`--sanity-run` additionally runs the rows of every fixture whose function is
already lifted (0x00426CC0 and 0x00426D50, the spike and entry-test builds)
through the existing harness path and records pass/fail in the receipt. That is
evidence, not the M3 metric: M3 reads `private/generated/fixture-results.json`,
which the main thread wires later.

Usage:
    source tools/env.sh
    .venv/bin/python tools/s2/fixtures/register_corpus.py [--sanity-run] [--dry-run]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "s2" / "entry_test"))

import corpus as C  # noqa: E402

ROOT = C.ROOT
REGISTRY = ROOT / "Documentation" / "receipts" / "fixture-registry.json"
RECEIPT = ROOT / "Documentation" / "receipts" / "s2-fixture-corpus-v1.json"
GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "fixture_corpus"
IMAGE = GEN / "spike" / "fixtures" / "image.bin"
SPIKE_BUILD = GEN / "spike" / "fixtures" / "build"
PASS_BUILD = GEN / "spike" / "pc24pass" / "root" / "fixtures" / "build"
ACTIVE_BUILD = GEN / "s2" / "entry_test" / "harness"

REGISTRY_NOTE = (
    "M3's denominator is every fixture registered here: `fixtures passing / fixtures "
    "registered` (design 8). Entries with `runnable_now` false are counted in the "
    "denominator on purpose and will fail until S2's wrap reaches them, so the metric "
    "shows the real gap instead of a flattering ratio over the two fixtures the S1 spike "
    "harness happens to drive. `runnable_now` is generated, never classified by hand: "
    "it is true exactly when the S2 runtime ran the fixture and every row matched "
    "(private/generated/fixture-results.json, written by tools/s2/corpus_run), and each "
    "`blocker` is composed from that fixture's own decoded sections, the addresses it "
    "names, which of them the emitter has lifted, and what the run reported. Before the "
    "G2 fold it was a hand-kept field on each `Format`: true for 2 of 54 while 10 passed. "
    "The blocker classes below are the stages that clear them: "
    "a fixture whose function is outside the ten lifted spike functions "
    "needs S3 (whole-image lift: every function in the map emitted and compiled); a "
    "fixture whose rows are a window inside a large routine, or a whole-kernel "
    "composition over several functions, needs S3 for the callees and S4's runtime "
    "(guest memory, function table, direct-call entry) before the window can be entered "
    "at all; a fixture whose function calls an oracle the harness serves natively "
    "(0x00459260, the transcendental direction-to-angles routine, design 7.4 residue "
    "class) needs S2's own runtime v0 stub policy or S4's live oracle; a fixture that is "
    "shipped level data rather than a function's inputs and outputs is not registered at "
    "all and is listed in the receipt instead. Rows and shipped bytes never appear here: "
    "this file names files, counts and addresses only (design 10). Counts and layouts "
    "are Documentation/receipts/s2-fixture-corpus-v1.json."
)


def now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def commit() -> str | None:
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


# ----------------------------------------------------------------- inventory
def inventory(fm: C.FunctionMap) -> list[dict]:
    rows = []
    for path in C.corpus_files():
        d = C.decode(path)
        f = d.fmt
        override = C.FILE_NOTES.get(path.stem, {})
        stager = override.get("stager", f.stager if f else None)
        entry: dict = {
            "id": path.stem,
            "source": C.rel(path),
            "magic": d.magic,
            "bytes": d.file_bytes,
            "sha256": C.sha256(path),
            "decoded": f is not None and d.error is None,
            "format": f"{d.magic}/v{d.header.get('version')}" if f and "version" in d.header else d.magic,
            # The stagers live in the predecessor checkout, not in this tree; the path is
            # absolute so the cited-files gate reads it as a reference, not as a claim
            # that this repository holds Documentation/scripts/stage_*.py.
            "stager": f"{C.PREDECESSOR}/Documentation/scripts/{stager}" if stager else None,
            "header": d.header if f else {},
            "sections": d.sections if f else {},
            "rows": d.rows if f and d.error is None else None,
            "layout_reproduces_file": (d.bytes_consumed == d.file_bytes) if f else False,
            "bytes_from_layout": d.bytes_consumed if f else None,
            "functions": list(f.functions) if f else [],
            "function_basis": f.function_basis if f else None,
            "authority": f.authority if f else None,
            "authority_basis": f.authority_basis if f else None,
            "covers": f.covers if f else None,
            "inputs": list(f.inputs) if f else [],
            "outputs": list(f.outputs) if f else [],
            "runnable_now": bool(f.runnable_now) if f else False,
            "blocker": (f.blocker if f else None),
            "covers_function": bool(f.covers_function) if f else False,
            "notes": override.get("notes", f.notes if f else None),
            "undecoded_reason": d.error,
        }
        if f and f.tail:
            entry["tail"] = f.tail
        entry["function_map"] = [fm.describe(a) for a in entry["functions"]]
        rows.append(entry)
    return sorted(rows, key=lambda r: r["id"])


def registrable(entry: dict) -> bool:
    """A fixture enters M3's denominator when it is a function fixture in design 3.7's sense:
    decoded, naming a native function, and carrying both inputs and expected words."""
    return bool(entry["decoded"] and entry["covers_function"] and entry["functions"]
                and entry["rows"] is not None and entry["inputs"] and entry["outputs"])


RESULTS = GEN / "fixture-results.json"
CORPUS_RUN = GEN / "s2" / "corpus_run"


def run_outcomes() -> dict:
    """What the S2 runtime's own run did with each fixture.

    `runnable_now` used to be a hand-written field on each `Format` in corpus.py: a
    judgement, kept by hand, against a harness that no longer exists. It was true for 2
    of 54 while 10 passed. It is now read from the run: a fixture is runnable now when
    the S2 runtime ran it and every row matched. Nothing here is a classification.
    """
    out: dict = {"passed": set(), "failed": set(), "evidence": {},
                 "source": [], "available": False}
    if RESULTS.is_file():
        r = json.loads(RESULTS.read_text())
        out["passed"] |= set(r.get("passed") or [])
        out["failed"] |= set(r.get("failed") or [])
        out["source"].append(C.rel(RESULTS))
        out["available"] = True
    run = CORPUS_RUN / "run.pc24.json"
    if run.is_file():
        for x in json.loads(run.read_text())["results"]:
            out["evidence"][x["fixture"]] = {
                "driven_by": "tools/s2/corpus_run/run.py",
                "entry": x.get("function"), "rows": x.get("rows"),
                "rows_matching_native": x.get("rows_matching_native"),
                "status_counts": x.get("status_counts"),
                "first_divergence": x.get("first_divergence"),
                "trap_addresses": x.get("trap_addresses"),
            }
        out["source"].append(C.rel(run))
    return out



# --------------------------------------------------------------- the survey
# One entry per fixture the 2026-09-20 corpus survey tried and could not drive. Each
# says which field the fixture does not record and which instruction consumes it, with
# the disassembly, read out of private/exe/sonic2app-unpacked.exe. A fixture with no
# entry here has not been surveyed at that depth; the derived sentence below says so
# rather than inventing a reason.
SURVEYED: dict[str, str] = {}


def _survey(*ids: str):
    def deco(fn):
        for i in ids:
            SURVEYED[i] = fn.__doc__.strip()
        return fn
    return deco


# Every entry that used to stand here is gone, because every fixture it covered now
# drives: the eight are `ground-contact-composition-v1`,
# `ground-source-cache-contact-expectations-v2`,
# `ground-all-source-contact-expectations-v3`, `ground-contact-expectations-slope-v1`,
# `ground-full-chain-v1`, `ground-full-chain-slope-v1`, `ground-candidate-loop-v1` and
# `ground-executor-multi-v1`, and how each one drives is
# agents-journals/s2/2026-09-20-s2-corpus-final.md. The survey table is kept, empty,
# because the next fixture that resists still deserves an entry with an address in it.



def derived_blocker(entry: dict, outcome: dict, lifted: set[int]) -> str | None:
    """Why this fixture is not passing, said in terms of its own decoded sections.

    The four class templates in corpus.py (`B_TEN`, `B_WINDOW`, `B_KERNEL`, `B_ORACLE`)
    were substituted per fixture with only the addresses changed, so a fixture that does
    ship the state its blocker said it lacked still read "entered mid-function with the
    whole player/work state live". This composes the sentence out of what the fixture
    itself records: the sections it carries, the addresses it names, which of those the
    emitter has lifted, and -- when it was actually driven -- what the run reported.
    """
    fid = entry["id"]
    if fid in outcome["passed"]:
        return None
    fns = entry["functions"]
    lifted_now = [a for a in fns if int(a, 16) in lifted]
    ev = outcome["evidence"].get(fid)
    sections = ", ".join(f"{k}[{v}]" for k, v in sorted((entry["sections"] or {}).items()))
    recorded = (f"it records {entry['rows']} rows over sections {sections}; "
                f"inputs ({', '.join(entry['inputs'])}); "
                f"outputs ({', '.join(entry['outputs'])})")
    emitted = (f"{len(lifted_now)} of its {len(fns)} address(es) are lifted "
               f"({', '.join(lifted_now) or 'none'})")
    if ev:
        if ev.get("first_divergence"):
            d = ev["first_divergence"]
            return (f"driven and failed: {ev['rows_matching_native']}/{ev['rows']} rows match, "
                    f"first divergence at row {d['row']} field {d['field']} "
                    f"(expected {d['expected']}, observed {d['observed']}, status {d['status']}, "
                    f"exit {d['exit_pc']}); {emitted}; {recorded}")
        return (f"driven and failed: {ev['rows_matching_native']}/{ev['rows']} rows match, "
                f"statuses {ev.get('status_counts')}, traps at "
                f"{list((ev.get('trap_addresses') or {}))[:4]}; {emitted}; {recorded}")
    # The hand-written `Format.blocker` is deliberately not carried over. It was one of
    # four class templates with the addresses substituted, and at least three of its
    # claims were false of the fixture it was attached to -- `candidate-cache-builder-v1`
    # read "0x004974F0 and 0x00497560, neither lifted" when 0x004974F0 is lifted, and
    # `ground-near-set-v1..v4`, `compact-contact-corpus-v1` and
    # `ground-candidate-executor-v1` read "entered mid-function with the whole player/work
    # state live" when they ship that state in their own sections.
    if fid in SURVEYED:
        return f"{SURVEYED[fid]} -- {emitted}; {recorded}"
    return (f"not driven and not surveyed at instruction depth: "
            f"tools/s2/corpus_run/plans.py has no builder for it; {emitted}; {recorded}")


# --------------------------------------------------------------- superseded files
# A fixture whose rows are a strict prefix of another fixture's, over the same cases and
# the same compared fields, is the same evidence counted twice. M3's denominator is
# "fixtures registered", so a duplicate inflates both sides of the ratio and adds nothing
# to either. These two are v1 captures that their own v2 re-capture replaced: identical
# on every recorded word, with one compared column each that the plan itself zeroes
# before the call, so most of their rows are satisfied without executing anything
# (G2b finding 2). They still drive -- `tools/s2/corpus_run/run.py` has a builder for
# each, and `Documentation/scripts/check_fixture_corpus.py` still re-runs them -- but
# they are not in M3's denominator and the registry records why.
#
# The checks behind each entry are reproducible in one command each:
#   .venv/bin/python -c "import sys; sys.path.insert(0,'tools/s2/corpus_run'); \
#       import fixture_rows as FR; a=FR.load('ground-near-set-v1')[1]; \
#       b=FR.load('ground-near-set-v2')[1]; \
#       print(len(a)==len(b), all(x==y[:len(x)] for x,y in zip(a,b)))"
SUPERSEDED: dict[str, dict] = {
    "ground-near-set-v1": {
        "superseded_by": "ground-near-set-v2",
        "why": ("all 26726 rows are identical to ground-near-set-v2's on every one of the "
                "nine words SA2GNS1 records; SA2GNS2 adds a tenth, `distance_bits`, and "
                "compares it. v1's single compared column, `selected`, is the guest count "
                "at [0x01A5A2CC], which the plan pokes to 0 before the call, so on the "
                "24821 rows whose recorded count is 0 the record is reproduced without "
                "executing the lifted code at all -- 92.9 % of its compared words."),
    },
    "ground-admission-source-v1": {
        "superseded_by": "ground-admission-source-v2",
        "why": ("all 782 rows are identical to ground-admission-source-v2's on every one "
                "of the seven words SA2GAS1 records, and their `cols` tables agree; "
                "SA2GAS2 adds `distance_bits`. v1's single compared column, `accepted`, is "
                "the near-set count at 0x01DE9484, which the plan pokes to 0, so the 606 "
                "rows whose recorded count is 0 are reproduced with the call removed -- "
                "77.5 % of its compared words."),
    },
}


def registry_entries(rows: list[dict], outcome: dict, lifted: set[int]) -> list[dict]:
    out = []
    for e in rows:
        if not registrable(e):
            continue
        fid = e["id"]
        if fid in SUPERSEDED:
            continue
        ev = outcome["evidence"].get(fid)
        out.append({"id": fid, "function": e["functions"][0], "source": e["source"],
                    "rows": e["rows"], "authority": e["authority"], "format": e["format"],
                    "runnable_now": fid in outcome["passed"],
                    "driven": fid in outcome["passed"] or fid in outcome["failed"] or bool(ev),
                    "runnable_now_basis": ("the S2 runtime ran it and every row matched"
                                           if fid in outcome["passed"] else
                                           ("the S2 runtime ran it and rows diverged"
                                            if ev or fid in outcome["failed"] else
                                            "never driven")),
                    "blocker": derived_blocker(e, outcome, lifted),
                    "covers": e["covers"]})
    return sorted(out, key=lambda r: r["id"])


# ----------------------------------------------------------------- sanity run
def sanity_run(rows: list[dict]) -> dict:
    """Run every fixture whose function is already lifted through the existing harness path.

    The binaries are the S1 spike build's (tools/spike/fixtures/run_fixtures.py and
    tools/spike/pc24pass/run_pc24pass.py); nothing under tools/spike or tools/s2/entry_test
    is edited. This is evidence for this unit, not M3's numerator.
    """
    import entry_test as E  # for the vector and output file shapes; invoked, not edited

    builds = {
        0x00426CC0: {"remill_pc24pass": PASS_BUILD / "remill_00426CC0",
                     "remill_fp80": SPIKE_BUILD / "remill_00426CC0"},
        0x0048B9F0: {"remill_pc24pass": PASS_BUILD / "remill_0048B9F0",
                     "remill_fp80": SPIKE_BUILD / "remill_0048B9F0"},
    }
    result: dict = {
        "harness": "tools/spike/fixtures (the S1 build), invoked, not edited",
        "image": C.rel(IMAGE),
        "binaries": {f"{va:#010x}": {k: C.rel(v) for k, v in b.items() if v.exists()}
                     for va, b in builds.items()},
        "note": "a row passes when the lifted function returns through its own ret (status 0) and "
                "its payload words equal the fixture's native words exactly",
        "fixtures": [],
    }
    if not IMAGE.exists():
        result["not_run"] = ("private/generated/spike/fixtures/image.bin is absent; run "
                             "tools/spike/fixtures/run_fixtures.py and "
                             "tools/spike/pc24pass/run_pc24pass.py first")
        return result
    OUT.mkdir(parents=True, exist_ok=True)
    for e in rows:
        try:
            va, in_words, out_words, ins, expected = C.rows_for_harness(
                e["magic"], (ROOT / e["source"]).read_bytes())
        except KeyError:
            continue
        fx: dict = {"id": e["id"], "harness_function": f"{va:#010x}",
                    "registered_function": e["functions"][0] if e["functions"] else None,
                    "rows": len(ins), "shape": f"{in_words} in / {out_words} out", "runs": {}}
        vec = OUT / f"{e['id']}.vectors.bin"
        E.write_vectors(vec, va, in_words, out_words, ins)
        for tag, binary in builds.get(va, {}).items():
            if not binary.exists():
                fx["runs"][tag] = {"error": f"{C.rel(binary)} is absent"}
                continue
            out = OUT / f"{e['id']}.{tag}.bin"
            p = subprocess.run([str(binary), str(IMAGE), str(vec), str(out)],
                               capture_output=True, text=True)
            if p.returncode:
                fx["runs"][tag] = {"error": f"harness exited {p.returncode}"}
                continue
            obs = E.read_out(out)
            returned = sum(1 for r in obs if r[0] == 0)
            matched = sum(1 for i, r in enumerate(obs) if r[0] == 0 and r[2:] == expected[i])
            run = {"binary": C.rel(binary), "rows": len(obs),
                   "returned_through_own_ret": returned, "matches_native": matched,
                   "pass": matched == len(obs) == len(expected)}
            if returned < len(obs):
                left = sorted({f"{r[1]:#010x}" for r in obs if r[0] == 1})
                if left:
                    run["left_the_lifted_bytes_at"] = left
                run["statuses"] = sorted({r[0] for r in obs})
            fx["runs"][tag] = run
        fx["pass"] = fx["runs"].get("remill_pc24pass", {}).get("pass", False)
        result["fixtures"].append(fx)
    result["fixtures"].sort(key=lambda r: r["id"])
    result["passing"] = sorted(f["id"] for f in result["fixtures"] if f["pass"])
    result["failing"] = sorted(f["id"] for f in result["fixtures"] if not f["pass"])
    return result


# ----------------------------------------------------------------- outputs
def counts(rows: list[dict], registered: list[dict]) -> dict:
    def tally(key: str, source: list[dict]) -> dict:
        out: dict[str, int] = {}
        for r in source:
            out[str(r[key])] = out.get(str(r[key]), 0) + 1
        return dict(sorted(out.items()))

    return {
        "files_in_corpus": len(rows),
        "decoded": sum(1 for r in rows if r["decoded"]),
        "undecoded": sum(1 for r in rows if not r["decoded"]),
        "registered": len(registered),
        "not_registered": len(rows) - len(registered),
        "by_format": tally("format", rows),
        "by_magic": tally("magic", rows),
        "by_authority": tally("authority", registered),
        "by_runnable_now": tally("runnable_now", registered),
        "native_rows_registered": sum(r["rows"] for r in registered),
    }


def functions_covered(rows: list[dict], registered: list[dict], fm: C.FunctionMap) -> list[dict]:
    ids = {r["id"] for r in registered}
    seen: dict[str, dict] = {}
    for r in rows:
        if r["id"] not in ids:
            continue
        for i, described in enumerate(r["function_map"]):
            entry = seen.setdefault(described["address"],
                                    dict(described, named_by=[], primary_for=[],
                                         rows_as_primary=0))
            entry["named_by"].append(r["id"])
            if i == 0:
                entry["primary_for"].append(r["id"])
                entry["rows_as_primary"] += r["rows"]
    for entry in seen.values():
        entry["named_by"] = sorted(set(entry["named_by"]))
        entry["primary_for"] = sorted(set(entry["primary_for"]))
    return [seen[k] for k in sorted(seen)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sanity-run", action="store_true", help="run the lifted fixtures through the S1 harness")
    ap.add_argument("--dry-run", action="store_true", help="print the counts, write nothing")
    ap.add_argument("--registry-only", action="store_true",
                    help="write Documentation/receipts/fixture-registry.json and leave "
                         "s2-fixture-corpus-v1.json alone (it belongs to another unit)")
    args = ap.parse_args()

    fm = C.FunctionMap()
    rows = inventory(fm)
    outcome = run_outcomes()
    lift_log = CORPUS_RUN / "lift.json"
    lifted = ({int(a, 16) for a, e in json.loads(lift_log.read_text())["functions"].items()
               if e.get("lifted")} if lift_log.is_file() else set())
    registered = registry_entries(rows, outcome, lifted)
    count = counts(rows, registered)
    undecoded = [{"id": r["id"], "source": r["source"], "magic": r["magic"], "bytes": r["bytes"],
                  "reason": r["undecoded_reason"] or "no format recorded for this magic"}
                 for r in rows if not r["decoded"]]
    not_registered = [{"id": r["id"], "source": r["source"], "magic": r["magic"],
                       "rows": r["rows"], "authority": r["authority"],
                       "reason": (r["undecoded_reason"] or "no format recorded for this magic")
                       if not r["decoded"] else (r["blocker"] or "covers no native function")}
                      for r in rows if not registrable(r)]

    m3 = {
        "registered": len(registered),
        "runnable_now": sum(1 for r in registered if r["runnable_now"]),
        "blocked": sum(1 for r in registered if not r["runnable_now"]),
        "native_rows_registered": count["native_rows_registered"],
        "m3_will_read": (f"fixtures passing / fixtures registered = "
                         f"<passing> / {len(registered)}; before S2's wrap runs any fixture "
                         f"private/generated/fixture-results.json lists none as passing, so M3 reads "
                         f"0/{len(registered)}"),
        "denominator_source": "Documentation/receipts/fixture-registry.json, fixtures[].id "
                              "(Documentation/scripts/metrics.py, m3_fixtures)",
        "numerator_source": "private/generated/fixture-results.json, passed[] intersected with the "
                            "registered ids; the main thread wires S2's results file",
    }

    sanity = sanity_run(rows) if args.sanity_run else {"not_run": "pass --sanity-run"}

    if args.dry_run:
        print(json.dumps({"counts": count, "m3_denominator": m3,
                          "undecoded": undecoded, "not_registered": not_registered}, indent=2))
        return

    registry = {"schema": "sa2-recomp-fixture-registry/v1",
                "generated_by": "tools/s2/fixtures/register_corpus.py",
                "note": REGISTRY_NOTE,
                "runnable_now_source": outcome["source"] or
                    ["no run recorded yet; every runnable_now is false"],
                "superseded": [
                    {"id": fid, "source": next((e["source"] for e in rows
                                                if e["id"] == fid), None), **body}
                    for fid, body in sorted(SUPERSEDED.items())],
                "superseded_note": (
                    "Registered fixture files whose rows are a strict prefix of another "
                    "fixture's over the same cases: the same evidence counted twice, so "
                    "they are not in M3's denominator. They are still decoded, still "
                    "driven by tools/s2/corpus_run/run.py and still re-run by "
                    "Documentation/scripts/check_fixture_corpus.py; what they are not is "
                    "a second entry in the ratio. Each `why` states the word-for-word "
                    "check that establishes the duplication and how much of the "
                    "superseded file a no-call run reproduces."),
                "fixtures": registered}
    REGISTRY.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    if args.registry_only:
        print(f"registered {len(registered)} fixtures; runnable_now {m3['runnable_now']} "
              f"(generated from {', '.join(outcome['source']) or 'nothing'}), "
              f"blocked {m3['blocked']}")
        print(f"wrote {C.rel(REGISTRY)}")
        return

    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s2-fixture-corpus-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S2",
        "question": "Which native functions does the inherited reference-project fixture corpus cover, how "
                    "many native rows does each carry, which can the harness drive today, and what is "
                    "M3's denominator once they are registered?",
        "written": now(),
        "inputs": {
            "corpus_directories": [C.rel(d) for d in C.CORPUS_DIRS],
            "files": len(rows),
            "commit": commit(),
            "tool": {"path": "tools/s2/fixtures/register_corpus.py",
                     "sha256": C.sha256(HERE / "register_corpus.py")},
            "decoder": {"path": "tools/s2/fixtures/corpus.py",
                        "sha256": C.sha256(HERE / "corpus.py")},
            "predecessor": {"repository": str(C.PREDECESSOR),
                            "stagers": "Documentation/scripts/stage_*.py",
                            "receipts": "Documentation/native-evidence-receipts/*.json"},
            "function_map": {"path": C.rel(C.FUNCTION_MAP), "available": fm.available,
                             "functions": len(fm.entries)},
            "sha256": {r["source"]: r["sha256"] for r in rows},
            "other_bin_files_under_private_fixtures": [
                {"path": C.rel(p), "bytes": p.stat().st_size, "sha256": C.sha256(p),
                 "why_not_a_fixture": "a shipped SA2 SET file copied from the installation "
                                      "(u32 entry count then 32-byte entries), not a staged "
                                      "fixture: no magic, no function, no expected words"}
                for p in sorted(
                    (ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "source").rglob("*.bin"))
            ],
        },
        "command": "source tools/env.sh && .venv/bin/python tools/s2/fixtures/register_corpus.py --sanity-run",
        "inventory": rows,
        "counts": count,
        "functions_covered": functions_covered(rows, registered, fm),
        "undecoded": undecoded,
        "not_registered": not_registered,
        "m3_denominator": m3,
        "sanity_run": sanity,
        "outputs": {"registry": C.rel(REGISTRY), "receipt": C.rel(RECEIPT)},
        "authority": "shipped-data-derived for the inventory (magics, sizes, hashes, layouts and row "
                     "counts read out of the private fixture files and the predecessor's stagers); "
                     "directly-traced where a fixture's authority field says so, which is the "
                     "predecessor's own receipt naming a native capture for those rows; the "
                     "runnable_now classification is an integration choice of this unit.",
        "limits": [
            "A registered row count is the fixture's own header count, not a count of native calls: a "
            "whole-kernel fixture's row is one captured window, not one call of the named function.",
            "`function` in the registry is the containing native function the fixture's words belong "
            "to. For window kernels that is the routine the window sits inside, not a function the "
            "fixture drives end to end.",
            "`runnable_now` is judged against the S1 spike harness shape "
            "(tools/spike/fixtures/shapes.h: ten functions plus the entry test's 0x00426D50), not "
            "against a runtime that does not exist yet.",
            "The sanity run is evidence for this unit only. M3's numerator comes from "
            "private/generated/fixture-results.json, which this tool does not write.",
            "Documentation/scripts/s1_metrics_inputs.py still rewrites "
            "Documentation/receipts/fixture-registry.json with `fixtures: []`. Re-running it "
            "would erase this registry and put M3 back to 0/0. That script belongs to S1 and was "
            "not edited by this unit; the main thread has to stop it overwriting the registry.",
            "The G1 interrogation counted 63 unregistered .bin files under private/fixtures. Two "
            "of those are shipped SA2 SET files under LocalAssets-SA2/source (listed in "
            "inputs.other_bin_files_under_private_fixtures); they are game data, not fixtures. The "
            "other 61 are this corpus.",
        ],
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(f"registered {len(registered)} fixtures of {len(rows)} files "
          f"({count['native_rows_registered']} native rows); "
          f"runnable_now {m3['runnable_now']}, blocked {m3['blocked']}; "
          f"undecoded {len(undecoded)}")
    print(f"wrote {C.rel(REGISTRY)} and {C.rel(RECEIPT)}")


if __name__ == "__main__":
    main()
