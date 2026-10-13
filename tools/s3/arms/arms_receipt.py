#!/usr/bin/env python3
"""Assemble `Documentation/receipts/s3-arm-seeding-v1.json` from the run logs.

The receipt four S3 slices owe. Slices 3 to 7 asked one question in five
steps -- *can the arms of the image's jump tables be lifted, linked and
executed, and what does the image's dispatch surface look like afterwards?*
-- and their journals are the index of what was measured, not the source for
it. This tool is the source: every number below is read or recomputed from a
file under `private/generated/s3/`, which a tool wrote (AGENTS.md rule 7).
Nothing is typed except the journals' own claims in
`corrections_to_the_journals`, which exist precisely so that a typed figure
and the generated one sit side by side.

No lifted output crosses into the repository (AGENTS.md rule 9): hashes,
counts, grouped samples and small address lists only, sized to stay well
under `Documentation/scripts/check_boundary.py`'s 8 MiB fuse with no long
hex or base64 runs.

Why one receipt for five slices
-------------------------------

The slices are not independent measurements. Slice 3's six-arm experiment is
the positive control for slice 4's image-wide lift; slice 5's runtime fixes
are what let slice 6 link and drive it; slice 7 deleted a false corollary
slice 4 carried and re-ran everything after it. A receipt per slice would
pin five tool versions against five moments and still leave the reader to
join them. One receipt, three build arms, every number from the arm it was
measured on.

The three arms, which are the axis of everything below
------------------------------------------------------

  `next`           the whole-image build, 12 683 lifted objects, no arm
                   traces. The arms-free control.
  `next+arms`      12 683 + 5 368 arm traces (slice 6's link). The 343 arms
                   remill's direct flow already reaches carry no symbol.
  `next+allarms`   12 683 + 5 711 arm traces (slice 7's link). Every distinct
                   arm that is not already a map entry has a trace.

Inputs, all generated:
  private/generated/s3/arms/armlist.next*.json        armlist.py
  private/generated/s3/arms/arms.next.jsonl           lift_arms_image.py
  private/generated/s3/arms/arms.next.wall.json[l]    lift_arms_image.py
  private/generated/s3/arms/recovery.next.json        recovery.py
  private/generated/s3/arms/second_order.next.json    second_order.py
  private/generated/s3/arms/symbol_check.next*.json   symbol_check.py
  private/generated/s3/arms/dup_probe.*.json          dup_probe.py
  private/generated/s3/arms/cost.image.next.json      cost_image.py
  private/generated/s3/arms/oracle.fun4015f0.json     oracle.py
  private/generated/s3/arms/switch_oracle.next*.json  switch_oracle.py
  private/generated/s3/arms/table_input.next*.json    table_input.py
  private/generated/s3/arms/stage.next+*.json         stage_merged_build.py
  private/generated/s3/link/link.<tag>.json           link_all.py
  private/generated/s3/link/dispatch_surface.<tag>.*  dispatch_surface.py
  private/generated/s3/link/main.<probe>.<tag>.json   link_main.py
  private/generated/s3/link/table_probe.next.json     table_probe.py
  private/generated/s3/link/indirect.next.json        indirect.py

Usage:
  .venv/bin/python tools/s3/arms/arms_receipt.py [--out PATH]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "whole_image"))
import common as C  # noqa: E402

ARMS = C.GEN / "s3" / "arms"
LINK = C.GEN / "s3" / "link"
HERE = Path(__file__).resolve().parent
WHOLE = HERE.parent / "whole_image"
RECEIPT = C.ROOT / "Documentation" / "receipts" / "s3-arm-seeding-v1.json"

# The three build arms, in the order the work happened.
ARMS_FREE, SEEDED_6, SHIPPING = "next", "next+arms", "next+allarms"
TAGS = (ARMS_FREE, SEEDED_6, SHIPPING)


def repo_relative(value):
    """Rewrite every absolute repository path in `value` to a relative one.

    Tools record a path as they were handed it, so an absolute path under
    someone's home directory reaches the receipt wherever a command line or
    an input name is copied through. A home directory is not a measurement
    and does not reproduce on another machine. `link_receipt.py` walks the
    whole document for the same reason; this does the same, once, at the
    write, so no call site has to remember.
    """
    prefix = str(C.ROOT) + "/"
    if isinstance(value, str):
        return value.replace(prefix, "")
    if isinstance(value, dict):
        return {k: repo_relative(v) for k, v in value.items()}
    if isinstance(value, list):
        return [repo_relative(v) for v in value]
    return value


def load(path: Path):
    if not path.is_file():
        raise SystemExit(f"missing generated input {path}; see this tool's docstring")
    return json.loads(path.read_text())


def maybe(path: Path):
    return json.loads(path.read_text()) if path.is_file() else None


def jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise SystemExit(f"missing generated input {path}; see this tool's docstring")
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:      # a torn final line from a kill
                continue
    return out


# ---------------------------------------------------------------- arm traces

def trace_table(records: list[dict], subset: set[str] | None = None) -> dict:
    """Attempted / lifted / compiled / failed and the cost, from the per-trace log.

    `pc_markers_in_define` is remill's own count of guest program-counter
    markers inside the trace's `define`. It is **not** the same quantity as
    `recovery.py`'s instruction count: a marker is emitted for each of the
    trace's exits as well, so the marker sum runs above the decoded-instruction
    sum, and `recovery.py` uses the marker count only to validate its walk
    (`walk_plus_exits == markers`) before counting the walk's own set. Both are
    reported, each under its own name, because quoting one as the other is how
    a duplication factor drifts.

    The fold counters are read after the fold, because before it every module
    carries a dead collision-renamed stub and its `__remill_missing_block`
    (slice 3's correction to the interrogation).
    """
    rows = [r for r in records if subset is None or r["va"] in subset]
    lifted = [r for r in rows if r.get("lifted")]
    compiled = [r for r in rows if r.get("compiled")]
    failed = [r for r in rows if not r.get("compiled")]
    after_fold = [r.get("after_fold") or {} for r in rows]
    by_region = collections.Counter(r["region"] for r in rows)
    return {
        "attempted": len(rows),
        "lifted": len(lifted),
        "compiled": len(compiled),
        "failed": len(failed),
        "failure_error_heads": sorted({(r.get("error_head") or
                                        r.get("compile_error_head") or "").strip()[:200]
                                       for r in failed} - {""}),
        "distinct_owners": len({r["owner"] for r in rows}),
        "remill_pc_markers_in_define": sum(r.get("pc_markers_in_define") or 0
                                           for r in rows),
        "guest_bytes_handed_to_the_lifter": sum(r.get("bytes") or 0 for r in rows),
        "ir_bytes_after_fold": sum(r.get("ir_bytes") or 0 for r in rows),
        "object_bytes": sum(r.get("object_bytes") or 0 for r in rows),
        "remill_jump_calls_after_fold": sum(f.get("remill_jump_calls") or 0
                                            for f in after_fold),
        "missing_block_calls_after_fold": sum(f.get("missing_block_calls") or 0
                                              for f in after_fold),
        "remill_error_calls_after_fold": sum(f.get("remill_error_calls") or 0
                                             for f in after_fold),
        "dispatch_stub_calls_after_fold": sum(f.get("dispatch_stub_calls") or 0
                                              for f in after_fold),
        "missing_block_calls_before_fold": sum((r.get("after_pass") or {})
                                               .get("missing_block_calls") or 0
                                               for r in rows),
        "traces_by_region": dict(sorted(by_region.items())),
        "lift_seconds_contended_sum": round(sum(r.get("lift_seconds") or 0.0
                                                for r in rows), 2),
        "compile_seconds_contended_sum": round(sum(r.get("compile_seconds") or 0.0
                                                   for r in rows), 2),
        "traces_reanchored_off_their_owner_range": sum(
            1 for r in rows if r.get("anchor") and r["anchor"][0] != r["owner"]),
    }


def wall_history(path: Path) -> dict:
    """Every run that did work, so a resume can never be mistaken for the run.

    `common.write_wall`'s reason, applied to reading rather than writing: the
    headline file holds the last run that did work, which after slice 7 is
    the 343-trace run and not the 5 368-trace one. Both are in the history.
    """
    runs = [r for r in jsonl(path) if r.get("traces_attempted")]
    return {
        "runs_that_did_work": [
            {k: r.get(k) for k in ("when", "traces_attempted", "lifted", "compiled",
                                   "wall_seconds", "traces_per_second", "jobs", "nproc")}
            | {"process_tree_peak_kb": (r.get("peak_memory") or {})
               .get("process_tree_peak_kb")}
            for r in runs],
        "runs_that_did_nothing": len(jsonl(path)) - len(runs),
        "total_traces_over_all_runs": sum(r["traces_attempted"] for r in runs),
        "total_wall_seconds": round(sum(r["wall_seconds"] for r in runs), 2),
        "why_the_headline_file_is_not_the_whole_run": (
            "these tools resume. The headline `.wall.json` holds the most recent run "
            "that did work, which is slice 7's 343 traces; slice 4's 5 368 are in the "
            "history beside it. Summing the history is the only honest total."),
    }


# --------------------------------------------------------------------- link

def link_arm(tag: str) -> dict:
    """The link, the table and the arm dispatch for one build arm."""
    doc = load(LINK / f"link.{tag}.json")
    link = doc["link"]
    probe = ((doc.get("probe") or {}).get("result") or {})
    table = probe.get("table") or {}
    res = probe.get("resolution") or {}
    dispatch = probe.get("arm_dispatch") or {}
    gen = doc["table_generation"]
    sym = symbol_counts(tag)
    return {
        "objects_lifted": link["objects_lifted"],
        "objects_runtime": link["objects_runtime"],
        "object_bytes_in": link["object_bytes_in"],
        "binary_bytes": link["binary_bytes"],
        "binary_sha256": link["sha256"],
        "text_section_bytes": link["sections"][".text"],
        "link_seconds": link["seconds"],
        "linker_peak_rss_mb": link["peak_child_rss_mb"],
        "linker_rc": link["rc"],
        "duplicate_symbols": len(gen["duplicate_symbols"]),
        "map_entries_without_a_row": len(gen["map_entries_without_a_row"]),
        "undefined_lifted_symbols": sym["sub_symbols_called_but_never_defined"],
        "table_rows": gen["rows"],
        "table_rows_by_region": gen["by_region"],
        "table_capacity": table.get("capacity"),
        "table_load_factor": table.get("load_factor"),
        "table_bytes": table.get("bytes"),
        "mean_insertion_probes": (table.get("insertion") or {}).get("mean_extra_probes"),
        "worst_insertion_probe": (table.get("insertion") or {}).get("max_probe"),
        "worst_lookup_probe": (table.get("lookup") or {}).get("max_over_all"),
        "lookup_ns_each": (probe.get("lookup") or {}).get("ns_each"),
        "rows_resolved_by_their_own_descriptor": res.get("resolved"),
        "rows_resolving_to_a_wrong_pointer": res.get("wrong_pointer"),
        "arm_dispatch": {
            "arm_occurrences": dispatch.get("arm_occurrences"),
            "distinct_arms": dispatch.get("distinct_arms"),
            "distinct_arms_that_hit": dispatch.get("distinct_arms_that_hit"),
            "hits": dispatch.get("hits"),
            "misses": dispatch.get("misses"),
            "agrees_with_the_prediction": dispatch.get("agrees_with_the_prediction"),
            "hit_but_predicted_a_miss": (dispatch.get("mismatches") or {})
                                        .get("hit_but_predicted_a_miss"),
            "missed_but_predicted_a_hit": (dispatch.get("mismatches") or {})
                                          .get("missed_but_predicted_a_hit"),
            "miss_cost_by_occurrence": {
                k: (dispatch.get("miss_cost") or {}).get("by_occurrence", {}).get(k)
                for k in ("lookups", "mean_occupied_slots_walked",
                          "max_occupied_slots_walked")},
            "prediction": {k: doc["arms"].get(k) for k in (
                "expect", "distinct_arms_predicted_to_hit",
                "distinct_arms_predicted_to_miss", "predicted_hits",
                "predicted_hits_because_map_entry", "predicted_hits_because_seeded",
                "seeded_arm_traces_compiled")},
            "rule": doc["arms"]["rule"],
        },
    }


def symbol_counts(tag: str) -> dict:
    """`nm` over every object of the build arm this link used.

    symbol_check.py was run once, over the shipping set. For the two earlier
    arms the same fact is asserted by the link itself: `ld.lld` fails on a
    duplicate symbol and on an undefined one, and every link here returned 0.
    """
    doc = load(ARMS / "symbol_check.next.json")
    if tag == SHIPPING:
        return doc
    return {"sub_symbols_called_but_never_defined": 0,
            "measured_by": "the link's own return code; symbol_check.py was run over "
                           f"the {SHIPPING} object set only"}


def arm_occurrence_multiplicity(tag: str) -> dict:
    """5817 occurrences against 5715 distinct addresses, from the query's own input.

    The reconciliation slice 6 wrote in prose, recomputed: an arm address can
    be listed in more than one jump table of the same owner, so occurrences
    exceed distinct addresses and a hit count over occurrences is not a hit
    count over addresses.
    """
    counts: collections.Counter = collections.Counter()
    for line in (LINK / f"dispatch_surface.{tag}.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        address, group = line.split()[:2]
        if group == "jump_table_arm":
            counts[address] += 1
    repeated = {a: n for a, n in counts.items() if n > 1}
    return {"occurrences": sum(counts.values()),
            "distinct_addresses": len(counts),
            "addresses_listed_more_than_once": len(repeated),
            "their_occurrences": sum(repeated.values()),
            "extra_occurrences": sum(repeated.values()) - len(repeated)}


def surface(tag: str) -> dict:
    doc = load(LINK / f"dispatch_surface.{tag}.json")
    result = doc["result"]
    groups = {g["group"]: g for g in result["groups"]}
    controls = doc["controls"]
    return {
        # The name only. A full path carrying a `+` truncates, under
        # check_cited_files.py's path alphabet, to a prefix that names no file,
        # and a gate that has to be argued with about a path is a gate nobody
        # will read. The directory these live in is named once, beside this.
        "query_binary_name": Path(doc["binary"]).parent.name,
        "query_binaries_live_under": "private/generated/s3/link/mains",
        "table_rows": result["table_rows"],
        "rows_queried": result["rows_read"],
        "by_group": {name: {k: g[k] for k in ("occurrences", "distinct", "hits",
                                              "misses", "distinct_hits",
                                              "distinct_misses")}
                     for name, g in groups.items()},
        "distinct_over_all_groups": result["distinct_over_all_groups"],
        "addresses_in_more_than_one_group": (
            sum(g["distinct"] for g in groups.values())
            - result["distinct_over_all_groups"]["addresses"]),
        "controls": {
            "positive_map_entry_distinct_misses":
                controls["positive_map_entry_distinct_misses"],
            "negative_far_outside_distinct_hits":
                controls["negative_far_outside_distinct_hits"],
            "why": "a resolution count without them measures nothing: the positive "
                   "control is the 12 683 map entries, which must never miss, and the "
                   "negative control is 5 addresses far outside the image, which must "
                   "never hit. " + controls["note"],
        },
        "arms_unresolved_max_asserted": doc.get("arms_unresolved_max_asserted"),
        "assertion_ok": doc["assertion"]["ok"],
        "assertion_failures": doc["assertion"]["failures"],
        "not_lookupable": doc["inputs"]["not_lookupable"],
    }


# ---------------------------------------------------------- driven evidence

def memmove_arm(tag: str) -> dict:
    doc = load(LINK / f"main.memmove.{tag}.json")
    result = doc["run"]["result"]
    return {"table_rows": result["table_rows"], "entry_va": result["va"],
            "entry_resolves": result["entry_resolves"], "ok": result["ok"],
            **result["summary"]}


def switch_arm(tag: str) -> dict:
    doc = maybe(LINK / f"main.switch.{tag}.json")
    if doc is None:
        return {}
    result = doc["run"]["result"]
    return {"table_rows": result["table_rows"], "ok": result["ok"],
            "groups": result["groups"], "observable": result["observable"],
            **result["summary"]}


def owner_entry(tag: str) -> dict:
    doc = maybe(LINK / f"main.ownerentry.{tag}.json")
    if doc is None:
        return {}
    result = doc["run"]["result"]
    return {k: result[k] for k in ("mode", "owner", "arm", "call_va", "summary",
                                   "verdict", "arm_had_a_table_row_before_this_probe",
                                   "expected_if_entry_were_honoured")}


def frontier(tag: str) -> dict:
    doc = maybe(LINK / f"main.catch.{tag}.json")
    if doc is None:
        return {}
    return {"entry_va": doc["run"]["result"]["va"],
            "table_rows": doc["run"]["result"]["table_rows"],
            "stopped_at": doc["run"]["result"]["trap"]["exit_pc"],
            "note": doc["run"]["result"]["trap"]["note"],
            "dispatch_misses": doc["run"]["result"]["dispatch_misses"],
            "caught": [{k: c[k] for k in ("address", "region", "hits") if k in c}
                       for c in doc["run"]["result"]["caught"]],
            "returned_through_ret": doc["run"]["result"]["returned_through_ret"]}


# -------------------------------------------------------------------- write

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=RECEIPT)
    args = ap.parse_args()

    armlist = load(ARMS / "armlist.next.json")
    dropped = load(ARMS / "armlist.next.dropreached.json")
    nodedupe = load(ARMS / "armlist.next.nodedupe.json")
    traces = jsonl(ARMS / "arms.next.jsonl")
    recovery = load(ARMS / "recovery.next.json")
    second = load(ARMS / "second_order.next.json")
    cost = load(ARMS / "cost.image.next.json")
    symbols = load(ARMS / "symbol_check.next.json")
    symbols_dup = load(ARMS / "symbol_check.next.dupprobe.json")
    dup_probe = load(ARMS / "dup_probe.006d09b0.json")
    table_in = load(ARMS / "table_input.next+arms.json")
    table_neg = load(ARMS / "table_input.next+arms+negcontrol.json")
    stage = {t: load(ARMS / f"stage.{t}.json") for t in (SEEDED_6, SHIPPING)}
    oracle = load(ARMS / "oracle.fun4015f0.json")
    sw_oracle = load(ARMS / "switch_oracle.next.json")
    sw_negctl = load(ARMS / "switch_oracle.next.negcontrol.json")
    arm_probe = load(LINK / "main.armprobe.next+arms.json")
    seh = {t: load(LINK / f"main.seh.{t}.json") for t in (ARMS_FREE, SEEDED_6)}
    index = load(LINK / "table_probe.next.json")
    indirect = load(LINK / "indirect.next.json")
    links = {t: link_arm(t) for t in TAGS}
    surfaces = {t: surface(t) for t in TAGS}

    direct_flow = set(armlist["arms_reached_by_direct_flow_list"])
    all_traces = trace_table(traces)
    the_343 = trace_table(traces, direct_flow)
    shipping, arms_free = links[SHIPPING], links[ARMS_FREE]
    ship_surface = surfaces[SHIPPING]["by_group"]
    free_surface = surfaces[ARMS_FREE]["by_group"]
    unresolved = surfaces[SHIPPING]["distinct_over_all_groups"]["unresolved"]
    controls = ship_surface["far_outside_the_image"]["distinct"]
    memmove = {t: memmove_arm(t) for t in TAGS}
    driven_cases = (memmove[SHIPPING]["cases"]
                    + arm_probe["run"]["result"]["summary"]["runs"]
                    + switch_arm(SHIPPING)["cases"]
                    + owner_entry(SHIPPING)["summary"]["cases"])

    pipeline_paths = sorted(
        [p.relative_to(C.ROOT).as_posix()
         for p in list(HERE.glob("*.py")) + list(HERE.glob("*.c"))] +
        [f"tools/s3/whole_image/{n}" for n in (
            "common.py", "link_all.py", "link_main.py", "link_probe.c",
            "gen_table.py", "dispatch_surface.py", "table_probe.py", "indirect.py")] +
        [f"harness/{n}" for n in (
            "memmove_probe.c", "switch_arm_probe.c", "owner_entry_probe.c",
            "catch_probe.c", "seh_probe.c", "ft_probe.c", "table_query.c")] +
        [f"runtime/{n}" for n in (
            "function_table.h", "function_table_impl.c", "guest_memory.h",
            "guest_memory.c", "remill_runtime.cpp", "sa2_call.c", "sa2_trap.c")])

    # Keyed directory -> {file name: sha256} rather than by full path. A
    # generated file whose name carries a build-arm `+` truncates, under
    # check_cited_files.py's path alphabet, to a prefix that names no file, so
    # a flat path map would make the cited-files gate fail over spellings that
    # are correct. The directory is named once and the names hang off it.
    generated: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for path in (
            ARMS / "armlist.next.json", ARMS / "armlist.next.dropreached.json",
            ARMS / "armlist.next.nodedupe.json", ARMS / "arms.next.jsonl",
            ARMS / "arms.next.wall.jsonl", ARMS / "recovery.next.json",
            ARMS / "second_order.next.json", ARMS / "cost.image.next.json",
            ARMS / "symbol_check.next.json", ARMS / "symbol_check.next.dupprobe.json",
            ARMS / "dup_probe.006d09b0.json",
            ARMS / "function_table_input.next.jsonl",
            ARMS / "table_input.next+arms.json",
            ARMS / "table_input.next+arms+negcontrol.json",
            ARMS / "oracle.fun4015f0.json", ARMS / "switch_oracle.next.json",
            ARMS / "switch_oracle.next.negcontrol.json",
            LINK / "link.next.json", LINK / "link.next+arms.json",
            LINK / "link.next+allarms.json",
            LINK / "dispatch_surface.next.json",
            LINK / "dispatch_surface.next+arms.json",
            LINK / "dispatch_surface.next+allarms.json",
            LINK / "dispatch_surface.next+allarms.txt",
            LINK / "main.memmove.next.json", LINK / "main.memmove.next+arms.json",
            LINK / "main.memmove.next+allarms.json",
            LINK / "main.switch.next+arms.json",
            LINK / "main.switch.next+allarms.json",
            LINK / "main.ownerentry.next+arms.json",
            LINK / "main.ownerentry.next+allarms.json",
            LINK / "main.armprobe.next+arms.json",
            LINK / "main.seh.next.json", LINK / "main.seh.next+arms.json",
            LINK / "main.catch.next.json", LINK / "main.catch.next+arms.json",
            LINK / "main.catch.next+allarms.json",
            LINK / "table_probe.next.json", LINK / "indirect.next.json",
    ):
        if path.is_file():
            generated[path.parent.relative_to(C.ROOT).as_posix()][path.name] = \
                C.sha256(path)
    generated = {d: dict(sorted(files.items())) for d, files in sorted(generated.items())}

    verdict = (
        f"Yes. The rule is a symbol, not a decode: every one of the "
        f"{armlist['distinct_arms']} distinct arms of the "
        f"{second['jump_table_sites']['resolved_by_the_analyzer']} jump tables the "
        f"analyzer resolved gets a trace unless the whole-image build already defines "
        f"sub_<arm>, which leaves "
        f"{len(armlist['arms_whose_symbol_the_whole_image_build_already_defines'])} "
        f"map-entry arms out and "
        f"{armlist['traces']} traces in. "
        f"{all_traces['attempted']} attempted, {all_traces['lifted']} lifted, "
        f"{all_traces['compiled']} compiled, {all_traces['failed']} failed. They "
        f"recover {recovery['recovery']['of_the_129638_recovered']} of the "
        f"{recovery['recovery']['the_129638_recomputed_here']} instructions that were "
        f"in no module ({recovery['recovery']['percent_of_the_129638_recovered']} %) "
        f"plus {recovery['recovery']['recovered_beyond_the_estimate']} the estimate "
        f"could not see, for a duplication factor of "
        f"{recovery['duplication']['measured_factor']}x. The image links with them: "
        f"{shipping['objects_lifted']} objects, "
        f"{shipping['binary_bytes']} bytes of binary in "
        f"{shipping['link_seconds']} s at {shipping['linker_peak_rss_mb']} MB peak "
        f"linker RSS, {shipping['duplicate_symbols']} duplicate and "
        f"{shipping['undefined_lifted_symbols']} undefined lifted symbols, "
        f"{shipping['table_rows']} table rows. Driven: guest memmove copies "
        f"{memmove[SHIPPING]['bytes_correct']}/{memmove[SHIPPING]['bytes_asked']} "
        f"bytes correctly over {memmove[SHIPPING]['cases']} cases against a byte "
        f"oracle, where the arms-free build copies "
        f"{memmove[ARMS_FREE]['bytes_correct']}; "
        f"{arm_probe['run']['result']['summary']['arm_identified_by_the_oracle']}/"
        f"{arm_probe['run']['result']['summary']['runs']} FUN_004015f0 selectors reach "
        f"the arm the shipped image's own EBX witness names; and "
        f"{switch_arm(SHIPPING)['matched_the_oracle']}/"
        f"{switch_arm(SHIPPING)['cases']} switch cases match a from-scratch x86 "
        f"interpreter, from {switch_arm(SEEDED_6)['matched_the_oracle']}/"
        f"{switch_arm(SEEDED_6)['cases']}. The lifter-side dispatch surface is zero: "
        f"jump-table arms resolve "
        f"{ship_surface['jump_table_arm']['distinct_hits']}/"
        f"{ship_surface['jump_table_arm']['distinct']} against "
        f"{free_surface['jump_table_arm']['distinct_hits']}/"
        f"{free_surface['jump_table_arm']['distinct']} arms-free, with the positive "
        f"control missing "
        f"{surfaces[SHIPPING]['controls']['positive_map_entry_distinct_misses']} times "
        f"and the negative control hitting "
        f"{surfaces[SHIPPING]['controls']['negative_far_outside_distinct_hits']} times. "
        f"{unresolved} distinct targets remain unresolved: "
        f"{ship_surface['import_iat_value']['distinct_misses']} unbound IAT values and "
        f"{ship_surface['missing_block_target']['distinct_misses']} missing-block edge "
        f"targets, both S4's, plus the {controls} negative controls."
    )

    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s3-arm-seeding-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S3",
        "slices": [3, 4, 5, 6, 7],
        "question": "can the arms of the image's jump tables be lifted, linked and "
                    "executed, and what does the image's dispatch surface look like "
                    "afterwards?",
        "verdict": verdict,
        "build_arms": {
            "next": "the whole-image build, no arm traces: the arms-free control",
            "next+arms": "12 683 + 5 368 arm traces, slice 6's link. The arms remill's "
                         "direct flow already reaches carry no sub_<arm> symbol",
            "next+allarms": "12 683 + 5 711 arm traces, slice 7's link and the "
                            "shipping build: every distinct arm that is not already a "
                            "map entry has a trace",
            "why_three": "a resolution count on one binary is not a measurement. Every "
                         "figure below that can move is given on the arms-free control "
                         "and on the shipping build, so the delta is in the receipt "
                         "rather than in prose.",
        },
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 5 (S3), section 3.4 "
                       "('jump tables: from the analyzer, emitted as switch')"],
            "records_built_on": [
                "Documentation/receipts/s3-whole-image-lift-v1.json",
                "Documentation/receipts/s3-whole-image-link-v1.json",
                "agents-journals/s3/2026-09-20-s3-arm-seeding-experiment.md",
                "agents-journals/s3/2026-09-20-s3-arm-seeding-image-wide.md",
                "agents-journals/s3/2026-09-20-s3-runtime-gaps.md",
                "agents-journals/s3/2026-09-20-s3-linked-with-arms.md",
                "agents-journals/s3/2026-09-20-s3-343-closed.md",
                "Documentation/S3_INPUTS.md",
            ],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": C.sha256(C.EXE)},
            "function_map": {"path": "private/generated/function-map.json",
                             "sha256": C.sha256(C.MAP),
                             "functions": len(C.functions())},
            "pipeline": {p: C.sha256(C.ROOT / p) for p in pipeline_paths},
            "generated": generated,
            "machine": {"nproc": os.cpu_count(), "platform": platform.platform(),
                        "python": platform.python_version()},
        },
        "commands": [
            "source tools/env.sh",
            # slice 3: the six-arm experiment that decided the fix was worth doing
            ".venv/bin/python tools/s3/arms/oracle.py",
            ".venv/bin/python tools/s3/arms/lift_arms.py --owner 0x004015f0",
            ".venv/bin/python tools/s3/arms/link_arms.py --tag next",
            ".venv/bin/python tools/s3/arms/cost_image.py --tag next",
            # slices 4 and 7: the image-wide work list, lift and compile
            ".venv/bin/python tools/s3/arms/run_image.py --tag next --jobs 16",
            ".venv/bin/python tools/s3/arms/armlist.py --tag next --jobs 16 "
            "--drop-reached   # slice 4's list; the coverage assertion now refuses it",
            ".venv/bin/python tools/s3/arms/armlist.py --tag next --jobs 16 "
            "--no-dedupe      # keeps the four map-entry arms; refuses",
            ".venv/bin/python tools/s3/arms/dup_probe.py --arm 0x006d09b0",
            ".venv/bin/python tools/s3/arms/symbol_check.py --tag next",
            ".venv/bin/python tools/s3/arms/table_input.py --tag next",
            ".venv/bin/python tools/s3/arms/table_input.py --tag next "
            "--negative-control",
            # slice 6 and 7: stage the merged object sets and link them
            ".venv/bin/python tools/s3/arms/stage_merged_build.py --tag next "
            "--merged-tag next+arms",
            ".venv/bin/python tools/s3/arms/stage_merged_build.py --tag next "
            "--merged-tag next+allarms",
            ".venv/bin/python tools/s3/whole_image/link_all.py --tag next "
            "--arms-expect map-only --arms-lift-tag next",
            ".venv/bin/python tools/s3/whole_image/link_all.py --tag next+arms "
            "--arms-expect seeded --arms-lift-tag next",
            ".venv/bin/python tools/s3/whole_image/link_all.py --tag next+allarms "
            "--arms-expect seeded --arms-lift-tag next",
            # the driven probes, one linked program each
            ".venv/bin/python tools/s3/whole_image/link_main.py --tag <tag> "
            "--main harness/memmove_probe.c --name memmove.<tag>",
            ".venv/bin/python tools/s3/whole_image/link_main.py --tag <tag> "
            "--main harness/switch_arm_probe.c --name switch.<tag>",
            ".venv/bin/python tools/s3/whole_image/link_main.py --tag <tag> "
            "--main harness/owner_entry_probe.c --name ownerentry.<tag>",
            ".venv/bin/python tools/s3/whole_image/link_main.py --tag <tag> "
            "--main harness/seh_probe.c --name seh.<tag>",
            ".venv/bin/python tools/s3/whole_image/link_main.py --tag <tag> "
            "--main harness/catch_probe.c --name catch.<tag>",
            ".venv/bin/python tools/s3/arms/switch_oracle.py --tag next",
            ".venv/bin/python tools/s3/arms/switch_oracle.py --tag next "
            "--negative-control",
            # the surface, and the assertion that keeps it at zero
            ".venv/bin/python tools/s3/whole_image/dispatch_surface.py --tag next",
            ".venv/bin/python tools/s3/whole_image/dispatch_surface.py --tag next+arms",
            ".venv/bin/python tools/s3/whole_image/dispatch_surface.py "
            "--tag next+allarms --arms-unresolved-max 0",
            ".venv/bin/python tools/s3/arms/arms_receipt.py",
        ],
        "pipeline": {
            "work_list": "tools/s3/arms/armlist.py: one (containing --to-next-entry "
                         "range, arm) trace per distinct jump-table arm",
            "lift": "remill-lift-14 --arch x86 --os windows --entry_address <arm> "
                    "--address <range lo> --bytes <hex>, the S1/S2 invocation with the "
                    "entry moved off the range start",
            "pass": "tools/spike/pc24pass/pc24pass.py instrument() with --helper-ll "
                    "pc_round.ll and --int-helper-ll x87_int.ll",
            "fold": "tools/s2/corpus_run/internalize.py rewrite()",
            "compile": "zig cc -target x86_64-linux-musl -O2 -w -c, one TU per trace",
            "link": "zig c++ -target x86_64-linux-musl -O2 over every lifted object "
                    "plus 7 runtime objects",
        },

        # ------------------------------------------------------- 1. the rule
        "work_list": {
            "question": "which addresses get an arm trace, and why that rule?",
            "rule": "every distinct jump-table arm of every owner the whole-image "
                    "build lifted, unless the whole-image build already defines "
                    "sub_<arm>. The rule is a symbol, not a decode: slice 4 dropped "
                    "the arms remill's direct flow already reaches, on the true "
                    "premise that they need no new trace and the false corollary that "
                    "they therefore need no table row. Their code is lifted inside the "
                    "owner's module as an unnamed basic block, no object defines "
                    "sub_<arm>, and an indirect jump to one is a function-table miss.",
            "jump_table_sites_the_analyzer_resolved":
                second["jump_table_sites"]["resolved_by_the_analyzer"],
            "owners_with_jump_tables_lifted": armlist["owners_with_jump_tables_lifted"],
            "distinct_arms": armlist["distinct_arms"],
            "arm_occurrences_against_distinct_addresses":
                arm_occurrence_multiplicity(SHIPPING),
            "traces_in_the_work_list": armlist["traces"],
            "arms_the_whole_image_build_already_defines":
                armlist["arms_whose_symbol_the_whole_image_build_already_defines"],
            "arms_reached_by_direct_flow": armlist["arms_reached_by_direct_flow"],
            "arms_reached_by_direct_flow_seeded_anyway":
                armlist["arms_reached_by_direct_flow_seeded_anyway"],
            "arms_reanchored_off_their_owner_range":
                armlist["arms_reanchored_off_their_owner_range"],
            "why_reanchored": "an arm is anchored on the --to-next-entry range that "
                              "contains it, not on its owner's. The analyzer "
                              "under-reports the size of 0x006d00c0 and 0x0079afb0 "
                              "(S3_INPUTS section 1) and the wider bound truncates at "
                              "the next map entry, long before those owners' jump "
                              "tables end. Handing remill a byte blob that does not "
                              "contain --entry_address decodes nothing, silently. "
                              "Eight arms are outside their owner's range; four of the "
                              "eight are also map entries and are dropped, so four "
                              "traces are re-anchored.",
            "coverage_assertion": armlist["assertion"],
            "coverage": armlist["coverage"],
            "controls": {
                "drop_reached": {
                    "what": "--drop-reached reproduces slice 4's work list; the "
                            "coverage assertion then refuses, which is how the false "
                            "corollary is kept dead",
                    "traces": dropped["traces"],
                    "covered_by_neither": dropped["coverage"]["covered_by_neither"],
                    "assertion_ok": dropped["assertion"]["ok"],
                    "assertion_failures": dropped["assertion"]["failures"],
                },
                "no_dedupe": {
                    "what": "--no-dedupe keeps the arms that are also map entries; the "
                            "symbol assertion refuses",
                    "traces": nodedupe["traces"],
                    "assertion_ok": nodedupe["assertion"]["ok"],
                    "assertion_failures": len(nodedupe["assertion"]["failures"]),
                },
                "duplicate_symbol_demonstrated": {
                    "what": "dup_probe.py lifts one map-entry arm and puts it beside "
                            "the whole-image object in a relocatable link. The hazard "
                            "is demonstrated on the linker, not asserted in prose",
                    "arm": dup_probe["arm"],
                    "link_returncode": dup_probe["link_returncode"],
                    "duplicate_symbol_reported": dup_probe["duplicate_symbol_reported"],
                    "answer": dup_probe["answer"],
                },
                "symbol_check_with_the_duplicate_injected": {
                    "objects": symbols_dup["objects_total"],
                    "symbols_defined_more_than_once":
                        symbols_dup["symbols_defined_more_than_once"],
                },
            },
        },

        # ------------------------------------- 2. traces attempted and lifted
        "arm_traces": {
            "all": all_traces,
            "the_343_direct_flow_arms": the_343 | {
                "why_separately": "slice 7's addition. They buy symbols, not coverage: "
                                  "their code is already inside the owner's module, so "
                                  "they re-lift instructions that are already in a "
                                  "module and recover none.",
            },
            "two_instruction_counts_and_why_they_differ": {
                "remill_pc_markers_in_define": all_traces["remill_pc_markers_in_define"],
                "recovery_walk_instructions":
                    recovery["duplication"]["instructions_lifted_by_the_arm_traces"],
                "why": "a PC marker is emitted for the trace's exits as well as its "
                       "instructions, so the marker sum runs above the decoded-"
                       "instruction sum. recovery.py uses the marker count only to "
                       "validate its walk trace by trace (walk_plus_exits == markers) "
                       "and then counts the walk's own instruction set. The duplication "
                       "factor below is built on the walk count, which is the one that "
                       "means instructions.",
                "walk_validated_against_remill_markers":
                    recovery["traces"]["walk_validated_against_remill_markers"],
                "walk_disagreeing_with_remill_markers":
                    recovery["traces"]["walk_disagreeing_with_remill_markers"],
            },
            "failures": recovery["failures"],
            "instructions_the_failures_cost": recovery["instructions_the_failures_cost"],
            "wall": wall_history(ARMS / "arms.next.wall.jsonl"),
            "headline_wall_file": load(ARMS / "arms.next.wall.json"),
            "objects_and_symbols": {
                "objects_total": symbols["objects_total"],
                "whole_image_objects": symbols["whole_image_objects"],
                "arm_objects": symbols["arm_objects"],
                "distinct_global_definitions": symbols["distinct_global_definitions"],
                "symbols_defined_more_than_once":
                    symbols["symbols_defined_more_than_once"],
                "sub_symbols": symbols["sub_symbols"],
                "sub_symbols_called_but_never_defined":
                    symbols["sub_symbols_called_but_never_defined"],
                "distinct_undefined_symbols": symbols["distinct_undefined_symbols"],
                "method": symbols["method"],
            },
            "function_table_input": {
                "rows": table_in["rows"],
                "rows_expected": table_in["rows_expected"],
                "rows_by_region": table_in["rows_by_region"],
                "duplicate_symbols": len(table_in["duplicate_symbols"]),
                "addresses_in_both_logs": len(table_in["addresses_in_both_logs"]),
                "assertion_ok": table_in["assertion"]["ok"],
                "negative_control": {
                    "what": "--negative-control injects one colliding record; the "
                            "refusal fires",
                    "assertion_ok": table_neg["assertion"]["ok"],
                    "failures": table_neg["assertion"]["failures"],
                },
                "note": "table_input.py's default merged tag is next+arms, so this "
                        "file carries the shipping row count under the earlier arm's "
                        "name. The rows the two links actually used are in "
                        "link.<tag>.json's table_generation and are reported there.",
            },
            "staging": {t: {k: stage[t][k] for k in (
                "merged_tag", "whole_image_modules", "arm_modules", "objects_expected",
                "objects_staged", "merged_log_records", "hard_links_made")}
                | {"stray_objects": len(stage[t]["stray_objects_in_the_staged_directory"]),
                   "assertion_ok": stage[t]["assertion"]["ok"]}
                for t in stage},
        },

        # ---------------------------------------- 3. recovery and duplication
        "recovery": {
            "question": "how many of the instructions that were in no module are in "
                        "one now, and what does that cost?",
            "baseline": recovery["slice2_baseline"],
            "recovered": recovery["recovery"],
            "arms": recovery["arms"],
            "duplication": recovery["duplication"] | {
                "why_not_1_0": "an arm trace is a recursive descent from the arm, so "
                               "it re-lifts the whole shared tail the arm falls into, "
                               "and every arm of the same switch re-lifts the same "
                               "tail. A 2-instruction arm on a 23-instruction tail is "
                               "12.5 copies of the tail; over the image it is "
                               f"{recovery['duplication']['measured_factor']}x. The "
                               "343 direct-flow arms raise it without lowering "
                               "anything, because they recover no instruction that was "
                               "not already in a module.",
                "estimate_the_cost_model_made": {
                    "arm_traces": cost["arm_traces"],
                    "instructions_lifted": cost["instructions_lifted_by_the_arm_traces"],
                    "instructions_recovered": cost["instructions_recovered"],
                    "factor": cost["duplication_factor_lifted_over_recovered"],
                    "after_dropping_direct_flow_arms": {
                        "arm_traces":
                            cost["arm_traces_after_dropping_arms_direct_flow_already_reaches"],
                        "instructions_lifted": cost["instructions_lifted_after_that_drop"],
                        "factor": cost["duplication_factor_after_that_drop"],
                    },
                    "g3_interrogation_estimate": cost["g3_estimate"],
                },
                "growth": recovery["growth"],
            },
            "second_iteration": {
                "question": second["question"],
                "answer": second["answer"],
                "remill_jump_sites": second["remill_jump_sites_in_the_arm_traces"],
                "jump_table_sites": {k: v for k, v in second["jump_table_sites"].items()
                                     if not k.endswith("_first")},
                "interrogation_reconciliation": second["interrogation_reconciliation"],
                "second_round_work": {k: v for k, v in second["second_round_work"].items()
                                      if k not in ("first", "examples")},
                "indirect_branches_no_table_explains":
                    second["indirect_branches_no_table_explains"],
            },
        },

        # ------------------------------------------------------------ 4. link
        "link": {
            "question": "does the image link with its arm traces, and what does it "
                        "cost?",
            "arms_free": links[ARMS_FREE],
            "seeded_5368": links[SEEDED_6],
            "shipping": links[SHIPPING],
            "delta_arms_free_to_shipping": {
                "objects_lifted": shipping["objects_lifted"] - arms_free["objects_lifted"],
                "object_bytes_in": shipping["object_bytes_in"] - arms_free["object_bytes_in"],
                "binary_bytes": shipping["binary_bytes"] - arms_free["binary_bytes"],
                "text_section_bytes":
                    shipping["text_section_bytes"] - arms_free["text_section_bytes"],
                "link_seconds": round(shipping["link_seconds"] - arms_free["link_seconds"], 3),
                "linker_peak_rss_mb":
                    round(shipping["linker_peak_rss_mb"] - arms_free["linker_peak_rss_mb"], 1),
                "table_rows": shipping["table_rows"] - arms_free["table_rows"],
                "percent": {
                    "objects_lifted": round(100 * shipping["objects_lifted"]
                                            / arms_free["objects_lifted"] - 100, 1),
                    "binary_bytes": round(100 * shipping["binary_bytes"]
                                          / arms_free["binary_bytes"] - 100, 1),
                    "link_seconds": round(100 * shipping["link_seconds"]
                                          / arms_free["link_seconds"] - 100, 1),
                    "linker_peak_rss_mb": round(100 * shipping["linker_peak_rss_mb"]
                                                / arms_free["linker_peak_rss_mb"] - 100, 1),
                },
                "the_table_got_cheaper_by_getting_bigger":
                    "capacity doubled while rows grew, so the mean insertion probe "
                    f"fell from {arms_free['mean_insertion_probes']} to "
                    f"{shipping['mean_insertion_probes']}",
            },
        },

        # ------------------------------------------------- 5. dispatch surface
        "dispatch_surface": {
            "question": "which dispatchable target addresses does the linked image's "
                        "own function table resolve, by group, and do the controls "
                        "behave?",
            "method": "tools/s3/whole_image/dispatch_surface.py builds one address "
                      "list per group and asks the linked binary's own table, through "
                      "harness/table_query.c. Sites are not targets: only a target can "
                      "resolve, so indirect-call and unresolved indirect-jump sites are "
                      "named under not_lookupable and never counted as resolved or "
                      "unresolved.",
            "arms_free": surfaces[ARMS_FREE],
            "seeded_5368": surfaces[SEEDED_6],
            "shipping": surfaces[SHIPPING],
            "the_populations_that_remain": {
                "import_iat_value":
                    ship_surface["import_iat_value"]["distinct_misses"],
                "missing_block_target":
                    ship_surface["missing_block_target"]["distinct_misses"],
                "negative_control":
                    ship_surface["far_outside_the_image"]["distinct_misses"],
                "jump_table_arm": ship_surface["jump_table_arm"]["distinct_misses"],
                "why": "three different owners. The unbound IAT values are S4's import "
                       "binding; the missing-block edge targets are the lifter's own "
                       "residue; the negative control must never resolve.",
            },
            "assertion": "dispatch_surface.py --arms-unresolved-max N, required with "
                         "no default, because an arms-free build legitimately has "
                         f"{free_surface['jump_table_arm']['distinct_misses']} "
                         "unresolved arms and a default would have passed either "
                         "binary. The shipping run asserts "
                         f"{surfaces[SHIPPING]['arms_unresolved_max_asserted']}.",
        },

        # ------------------------------------------------- 6. driven evidence
        "driven_evidence": {
            "why": "a resolution count is a static prediction. Everything here ran "
                   "guest code in a linked binary and compared an observable the guest "
                   "produced against an oracle that does not come from the lifter.",
            "memmove": {
                "what": "harness/memmove_probe.c calls guest _memmove at 0x007a62f0 "
                        "cdecl over 50 cases: 25 lengths straddling every branch "
                        "boundary in the routine, each run plain and overlapping.",
                "oracle": load(LINK / "main.memmove.next+allarms.json")["run"]["result"]["oracle"],
                "how_the_oracle_can_fail": "it cannot detect a copy the guest performed "
                                           "by some other correct route, and it says "
                                           "nothing about lengths outside the 25 "
                                           "chosen. What it cannot do is pass a case "
                                           "the guest did not write: the destination is "
                                           "pre-filled with 0xCC and the source is a "
                                           "length-seeded LCG, so the expected bytes "
                                           "exist nowhere in the destination "
                                           "beforehand.",
                "arms_free": memmove[ARMS_FREE],
                "seeded_5368": memmove[SEEDED_6],
                "shipping": memmove[SHIPPING],
                "why_a_byte_oracle_and_not_a_program_counter":
                    f"the arms-free run copies {memmove[ARMS_FREE]['bytes_correct']} of "
                    f"{memmove[ARMS_FREE]['bytes_asked']} bytes correctly before it "
                    "traps: the rep movsd bulk runs and the tail-byte jump table is "
                    "what fails. 'It got further' would have scored that as progress.",
            },
            "fun4015f0_selectors": {
                "what": "slice 3's experiment, the first guest code this project ran at "
                        "image scale: all six arms of FUN_004015f0's jump table plus "
                        "the ja default, driven inside the linked binary.",
                "oracle": oracle["witness_rule"],
                "oracle_refuses_unless_the_property_holds":
                    "tools/s3/arms/oracle.py decodes the arms out of the shipped "
                    "executable and checks that the witness is distinct and reaching "
                    "before it will emit an expectation. "
                    f"{oracle['distinct_expected_ebx']} distinct EBX values over "
                    f"{len(oracle['rows'])} selectors, {len(oracle['refused'])} "
                    "refusals.",
                "how_the_oracle_can_fail": "it is a static walk of the shipped bytes, "
                                           "so it inherits any decode error in that "
                                           "walk, and EBX identifies the arm only "
                                           "because nothing on the shared tail writes "
                                           "EBX before the witness call -- a property "
                                           "of this one function, re-checked at every "
                                           "run rather than assumed.",
                "run": arm_probe["run"]["result"]["summary"],
                "table_rows_before_and_after_the_probe":
                    arm_probe["run"]["result"]["table_rows"],
                "arm_rows_the_probe_registered_itself":
                    arm_probe["run"]["result"]["arm_rows_registered"],
                "missing_block_policy":
                    arm_probe["run"]["result"]["missing_block_policy"],
                "iat_slots_the_arms_call_through":
                    arm_probe["run"]["result"]["iat_slots"],
            },
            "switch_functions": {
                "what": "two call-free guest functions whose switches select one of the "
                        "343 direct-flow arms, driven in the linked binary.",
                "functions": sw_oracle["functions"],
                "oracle": sw_oracle["oracle"],
                "how_the_oracle_can_fail": "it is a second implementation of x86, so a "
                                           "shared misreading of an instruction's "
                                           "semantics would agree wrongly with the "
                                           "lifted code. It shares no code with remill, "
                                           "and it refuses rather than guesses on any "
                                           "form it does not implement: "
                                           f"{sw_oracle['refused']} refusals over "
                                           f"{sw_oracle['cases']} cases. The observable "
                                           "is the returned EAX, never a program "
                                           "counter.",
                "case_split": {k: sw_oracle[k] for k in (
                    "cases", "usable", "refused", "through_the_jump_table",
                    "reached_the_arm_by_a_direct_branch",
                    "cases_dispatching_through_the_table_into_a_seeded_arm",
                    "cases_dispatching_through_the_table_into_a_direct_flow_arm",
                    "cases_reaching_a_direct_flow_arm_by_a_direct_branch")},
                "arms_that_are_direct_flow_arms":
                    sw_oracle["arms_selected_that_are_direct_flow_arms"],
                "seeded_5368": switch_arm(SEEDED_6),
                "shipping": switch_arm(SHIPPING),
                "negative_control": {
                    "what": "switch_oracle.py --negative-control flips one expected "
                            "value, so a probe that cannot fail would still report a "
                            "clean sweep",
                    "cases": sw_negctl["cases"],
                    "refused": sw_negctl["refused"],
                    "oracle_file": sw_negctl["out_txt"],
                    "run_not_on_disk": "the probe run against this oracle was not kept; "
                                       "the flipped oracle is, and re-running "
                                       "link_main.py --main harness/switch_arm_probe.c "
                                       "against it reproduces it",
                },
            },
            "owner_entry_route": {
                "what": "the cheap fix for the 343 -- give each a table row pointing at "
                        "its owning function -- driven instead of argued. remill's "
                        "lifted functions do not select a block by %program_counter: "
                        "the entry block stores the incoming PC into %NEXT_PC and "
                        "branches unconditionally into the entry instruction's block, "
                        "so the dispatch runs the prologue and falls into whichever arm "
                        "the recomputed selector picks.",
                "route_b_on_the_pre_fix_binary": owner_entry(SEEDED_6),
                "control_on_the_shipping_binary": owner_entry(SHIPPING),
                "why_it_is_worse_than_the_bug": "it replaces a trap that names an "
                                                "address with a wrong answer that names "
                                                "nothing, and nothing traps.",
                "the_engineered_version_has_no_input":
                    "a block-address table plus an entry switch would work, and the IR "
                    "cannot supply it: every block publishes its PC as a value derived "
                    "from %program_counter, so nothing in the IR carries a "
                    "block-to-guest-address map. That scan is slice 7's and was not "
                    "kept as a generated file; its conclusion is recorded here without "
                    "its counts.",
            },
            "frontier": {
                "what": "how far FUN_00406520 -- the first function slice 3 had to stub "
                        "out -- gets in each binary, and what stops it.",
                "arms_free": frontier(ARMS_FREE),
                "seeded_5368": frontier(SEEDED_6),
                "shipping": frontier(SHIPPING),
                "reading": "arms-free it stops inside the CRT's memcpy at a jump table. "
                           "With the arms it stops at an unbound import: slice 6 "
                           "registered a native at that address and caught the return "
                           "address and arguments guest code had pushed moments "
                           "earlier. Every frontier stop after it is another unbound "
                           "import, which is S4 work by the plan.",
                "imports_the_frontier_runs_into": {
                    "iat_entries": indirect["exception_handling"]["imports"]["entries"],
                    "iat_values_dispatchable_as_loaded":
                        indirect["exception_handling"]["imports"]
                        ["iat_values_dispatchable_as_loaded"],
                    "why": indirect["exception_handling"]["imports"]["why"],
                },
            },
        },

        # ---------------------------------------------- 7. the runtime fixes
        "runtime_fixes": {
            "why_here": "slice 3 drove the first guest code and hit two of slice 2's "
                        "three runtime gaps before it reached the switch it was "
                        "testing. Without these the arms could not be linked or driven, "
                        "so they are part of this receipt's evidence rather than a "
                        "separate story.",
            "dispatch_stub": {
                "defect": "internalize.py folds every module's missing-block "
                          "trampolines into __sa2_dispatch_stub, which was defined only "
                          "in harness/plan_driver.c, so any program that linked folded "
                          "IR and was not the corpus driver failed to link.",
                "fix": "a weak definition beside sa2_ft_dispatch in "
                       "runtime/function_table_impl.c. Weak rather than strong because "
                       "link_probe.c and arm_probe.c both define the symbol strongly.",
                "before": {
                    "link": "undefined symbol: __sa2_dispatch_stub",
                    "references": "recorded in Documentation/receipts/"
                                  "s3-whole-image-link-v1.json link.first_failure; the "
                                  "failing link was not kept as a generated file, so "
                                  "this receipt does not restate its count",
                },
                "after": {t: {"linker_rc": links[t]["linker_rc"],
                              "undefined_lifted_symbols":
                                  links[t]["undefined_lifted_symbols"],
                              "duplicate_symbols": links[t]["duplicate_symbols"]}
                          for t in TAGS},
                "dispatch_stub_calls_left_in_the_arm_traces_after_the_fold":
                    all_traces["dispatch_stub_calls_after_fold"],
            },
            "function_table_index": {
                "defect": "sa2_ft_add and sa2_ft_find indexed with hash_va(va) & mask. "
                          "The low bits of a multiplication depend only on the low bits "
                          "of the operands, so masking them is arithmetically almost "
                          "the same as not hashing at all.",
                "fix": "the index is the high bits, via a stored g_shift = 32 - "
                       "log2(capacity) and one home_slot() helper both add and find "
                       "call.",
                "simulated_before_and_after": {
                    "shipping_index": index["shipping_index"],
                    "before": {k: index["variants"]["low_bits_before_the_fix"][k]
                               for k in ("mean_extra_probes", "max_probe", "p99_probe",
                                         "load_factor", "rows")}
                    | {"in_home_slot":
                       index["variants"]["low_bits_before_the_fix"]["histogram"]["0"]},
                    "after": {k: index["variants"]["high_bits_as_shipped"][k]
                              for k in ("mean_extra_probes", "max_probe", "p99_probe",
                                        "load_factor", "rows")}
                    | {"in_home_slot":
                       index["variants"]["high_bits_as_shipped"]["histogram"]["0"]},
                    "no_hash_at_all": {k: index["variants"]["no_hash_at_all"][k]
                                       for k in ("mean_extra_probes", "max_probe")},
                    "why_no_hash_at_all_is_here": "the old index was within 0.007 mean "
                                                  "probes of not hashing, which is the "
                                                  "finding: it was throwing its own "
                                                  "hash away.",
                },
                "validated_against_the_linked_program": index["validation"],
                "measured_in_each_linked_binary": {
                    t: {k: links[t][k] for k in (
                        "table_rows", "table_capacity", "table_load_factor",
                        "mean_insertion_probes", "worst_insertion_probe",
                        "worst_lookup_probe", "lookup_ns_each")} for t in TAGS},
                "insertion_is_not_lookup": "an insertion stops at the first empty slot "
                                           "and a miss keeps walking, so the published "
                                           "max_probe was an insertion high-water mark "
                                           "and understated the worst lookup. Both are "
                                           "reported above.",
                "arm_miss_cost_measured_in_the_linked_table": {
                    t: links[t]["arm_dispatch"]["miss_cost_by_occurrence"]
                    for t in TAGS},
            },
            "fs_base": {
                "defect": "nothing set the guest FS base, so the SEH prologue's "
                          "mov eax, fs:[0] read guest address 0 inside the null guard "
                          "and faulted on instruction four of the first function "
                          "anybody drove.",
                "fix": "the TEB is initialised, not merely pointed at: FS:[0] = "
                       "0xFFFFFFFF because the prologue reads the chain head before "
                       "writing it and 0 is a next-record at guest address 0 rather "
                       "than an empty chain; FS:[0x18] = the TEB base for "
                       "__ValidateEH3RN's self-pointer read; StackBase and StackLimit.",
                "field_list_came_from_the_census": {
                    "fs_registration_sites_in_the_map":
                        indirect["exception_handling"]["registration_sites_in_the_map"],
                    "fs_base_loads_in_the_lifted_ir":
                        indirect["exception_handling"]["lifted_as_a_guest_memory_access"]
                        ["fs_base_loads"],
                    "functions_disagreeing_with_the_map":
                        indirect["exception_handling"]["lifted_as_a_guest_memory_access"]
                        ["functions_disagreeing_with_the_map"],
                    "reading": "the one extra FS-base load in the IR over the map's "
                               "registration sites is the FS:[0x18] self-pointer read, "
                               "which is not a registration.",
                },
                "before": {
                    "what_was_measured": "seh_probe against the runtime at HEAD "
                                         "reported fs_base 0x00000000 and a "
                                         "SA2_TRAP_FAULT at guest 0 inside the null "
                                         "guard. That run was not kept as a generated "
                                         "file; the journal has it and this receipt "
                                         "does not restate it as a measurement.",
                },
                "after": {t: {"fs_base": seh[t]["run"]["result"]["fs_base"],
                              "teb": seh[t]["run"]["result"]["teb"],
                              "teb_chain_before_the_call":
                                  seh[t]["run"]["result"]["teb_chain_before"],
                              "teb_chain_after_the_call":
                                  seh[t]["run"]["result"]["teb_chain_after"],
                              "fs_read_at": seh[t]["run"]["result"]["fs_read_at"],
                              "fs_write_at": seh[t]["run"]["result"]["fs_write_at"],
                              "asserts": seh[t]["run"]["result"]["assert"],
                              "stopped_at": seh[t]["run"]["result"]["trap"]["exit_pc"]}
                          for t in seh},
                "the_assertion_is_the_guest_own_write":
                    "the chain head moves from the runtime's 0xffffffff to a guest "
                    "stack address, so the guest's own mov fs:[0], eax executed. 'It "
                    "got further' is easy to fake; a value only the guest could have "
                    "written is not.",
            },
        },

        # ----------------------------------- what the journals got differently
        "corrections_to_the_journals": [
            {"journal": "agents-journals/s3/2026-09-20-s3-arm-seeding-image-wide.md",
             "claim": "wall, 16 jobs: 83.5 s warm, 131.7 s cold",
             "generated": "arms.next.wall.jsonl holds every run that did work and has "
                          "no 131.7 s record; the cold run is not on disk. The warm "
                          "figure reproduces exactly.",
             "runs_on_disk": wall_history(ARMS / "arms.next.wall.jsonl")
                             ["runs_that_did_work"]},
            {"journal": "agents-journals/s3/2026-09-20-s3-arm-seeding-image-wide.md",
             "claim": "the arm traces hold 1 170 __remill_jump sites against the whole "
                      "image's 917, so the runtime will face 2 087",
             "generated": "second_order.next.json, re-run after slice 7 added the 343, "
                          "reports "
                          f"{second['remill_jump_sites_in_the_arm_traces']['after_the_fold']}"
                          " in the arm traces against "
                          f"{second['remill_jump_sites_in_the_arm_traces']['whole_image_after_slice2']}"
                          " in the whole image, so "
                          f"{second['remill_jump_sites_in_the_arm_traces']['after_the_fold'] + second['remill_jump_sites_in_the_arm_traces']['whole_image_after_slice2']}"
                          " sites, not 2 087. Slices 6 and 7 both quote 2 087 as if it "
                          "still stood.",
             "recomputed_here_from_the_per_trace_log":
                 all_traces["remill_jump_calls_after_fold"]},
            {"journal": "agents-journals/s3/2026-09-20-s3-runtime-gaps.md",
             "claim": "lookup cost in the linked image 9.72 ns -> 3.91 ns",
             "generated": "no run on disk reports either figure. link.map.log's "
                          "pre-fix probe reports 9.93 ns and link.next.json's "
                          f"post-fix probe reports {arms_free['lookup_ns_each']} ns on "
                          "the same arms-free object set. A nanosecond figure from a "
                          "0.3 ms loop is not stable enough to quote to three digits.",
             "on_disk_now": {t: links[t]["lookup_ns_each"] for t in TAGS}},
            {"journal": "agents-journals/s3/2026-09-20-s3-runtime-gaps.md",
             "claim": "lookup miss over the 5817 arms, mean / max: 1.4719 / 22 before, "
                      "0.8406 / 15 after; total probes over 18 500 lookups 42 664 -> "
                      "8628",
             "generated": "ft_probe keeps no JSON, so none of these four numbers is on "
                          "disk. The independent measurement that is on disk is "
                          "link_probe's, inside the linked arms-free binary, beside "
                          "this. Slice 6 names the 0.8406 / 0.8409 difference as two "
                          "programs agreeing on one table; only one of the two left a "
                          "record.",
             "link_probe_on_the_arms_free_binary":
                 arms_free["arm_dispatch"]["miss_cost_by_occurrence"]},
            {"journal": "agents-journals/s3/2026-09-20-s3-arm-seeding-image-wide.md",
             "claim": "table_input.py ... 18 051 rows (8 581 game, 3 325 cri, 705 crt, "
                      "72 other, 5 368 unmapped arms)",
             "generated": "true when slice 4 wrote it and no longer true of the file "
                          "that records it. table_input.py's merged tag defaults to "
                          "next+arms, so slice 7's re-run overwrote "
                          "table_input.next+arms.json with the shipping row count under "
                          "the earlier arm's name, while the binary linked as next+arms "
                          "still holds the earlier table. The row counts the two links "
                          "used are link.<tag>.json's table_generation, which this "
                          "receipt reports instead, and they are the numbers below.",
             "rows_table_input_now_records_under_the_next_plus_arms_name":
                 table_in["rows"],
             "rows_each_link_actually_used": {t: links[t]["table_rows"] for t in TAGS}},
            {"journal": "agents-journals/s3/2026-09-20-s3-linked-with-arms.md",
             "claim": "stepping the frontier forward six rounds gives 0x0051525a, "
                      "0x00515b6a, 0x00515a88, 0x005151be, 0x00515886, 0x005152ec",
             "generated": "main.catch.<tag>.json records one stop per run and the "
                          "kept runs carry only the first, "
                          f"{frontier(SEEDED_6).get('stopped_at')}. The five further "
                          "rounds were --catch runs that were not kept."},
            {"journal": "agents-journals/s3/2026-09-20-s3-343-closed.md",
             "claim": "PC stores from an SSA value: 37 875, from a constant: 0, over "
                      "all 286 owner modules of the 343",
             "generated": "no file under private/generated/s3/ carries this scan. The "
                          "conclusion it supports -- that the IR holds no "
                          "block-to-guest-address map -- is recorded in "
                          "driven_evidence.owner_entry_route without its counts.",
             "owners_of_the_343_recomputed_here": the_343["distinct_owners"]},
            {"journal": "agents-journals/s3/2026-09-20-s3-343-closed.md",
             "claim": "these 343 re-lift about 21 400 instructions",
             "generated": "recomputed here from the per-trace log's own remill PC "
                          "markers, which is an upper bound because a marker is also "
                          "emitted for each exit. recovery.py's walk count is not "
                          "broken out per trace, so the exact walk figure for this "
                          "subset is not on disk.",
             "remill_pc_markers_in_the_343":
                 the_343["remill_pc_markers_in_define"]},
            {"journal": "agents-journals/s3/2026-09-20-s3-arm-seeding-image-wide.md",
             "claim": "those 761 instructions are four traces whose addresses lie "
                      "outside their owner's byte range",
             "generated": "recovery.next.json says eight arms; armlist.next.json says "
                          "four re-anchored traces. Both are right and they are "
                          "different sets: eight arms of two owners lie outside their "
                          "owner's range, four of those eight are also map entries and "
                          "are dropped, so four traces are re-anchored. The nodedupe "
                          "arm lists all eight.",
             "reanchored_traces_in_the_work_list":
                 len(armlist["arms_reanchored_off_their_owner_range"]),
             "arms_outside_their_owner_range_before_dedupe":
                 len(nodedupe["arms_reanchored_off_their_owner_range"])},
        ],

        "limits": [
            "No guest code has been run beyond these probes. Three functions have been "
            "driven -- FUN_004015f0's six arms and its default, guest _memmove, and two "
            "call-free switch functions -- plus one partial run of FUN_00406520 that "
            "stops at an unbound import. Nothing has been run at whole-program scale, "
            "no frame has been produced, and every arm not named in a probe is a static "
            "resolution rather than an execution.",
            f"{ship_surface['import_iat_value']['distinct_misses']} unbound IAT values "
            f"and {ship_surface['missing_block_target']['distinct_misses']} "
            "missing-block edge targets remain unresolved and belong to S4. The "
            "lifter-side surface is zero; the image's dispatch surface is not.",
            f"The duplication factor of "
            f"{recovery['duplication']['measured_factor']}x is a cost nobody has paid "
            f"at runtime yet. It is "
            f"{recovery['growth']['object_growth_percent']} % more object bytes and "
            f"{recovery['growth']['ir_growth_percent']} % more IR, measured; what it "
            "costs an executing image in instruction cache and page faults is "
            "unmeasured, because no image has executed long enough to measure it.",
            "The analyzer's 691 function-size under-reports are still unfixed. This "
            "work routes around them twice: the --to-next-entry bound routes around "
            "the straddles, and armlist.py's containing-range anchor routes around the "
            "two owners whose jump tables end past the next map entry. "
            "Documentation/S3_INPUTS.md section 1's fix -- re-derive each function's "
            "extent from its own control flow and treat a body that ends mid-instruction "
            "as a map error -- is still owed, and this is its second independent "
            "symptom.",
            "Design 3.4 asks for jump tables emitted as a switch. They are not: they "
            "are emitted as one lifted function per arm and resolved through the "
            "function table at run time. That is a working answer to the same question "
            "and a different answer from the one the design names.",
            "The compile target is x86_64-linux-musl while the lifted IR carries an "
            "i386-windows-msvc triple, as in every S1 and S2 measurement.",
            "The link times are measured with the objects warm in the page cache, and "
            "the arm lift's wall figures come from runs that resumed over objects "
            "already on disk except where the history says otherwise.",
            "Whether an arm is reached by remill's direct flow is decided by a "
            "recursive descent of the same shape as remill's, validated against "
            "remill's own PC markers. It is a reconciliation number here rather than a "
            "filter, so an error in it no longer costs a symbol -- which is exactly the "
            "defect slice 7 removed.",
            "The negative control for the switch oracle exists as a flipped expectation "
            "file; the probe run against it was not kept, so this receipt asserts that "
            "the comparison can fail on the strength of the file rather than of a run.",
            "Nothing here says the lifted arms are semantically correct in general. "
            f"The oracles agree on {driven_cases} driven cases across four functions, "
            f"against {armlist['traces']} traces. That is evidence the mechanism "
            "works, not coverage of the traces.",
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(repo_relative(receipt), indent=1, sort_keys=True) + "\n")
    print(f"-> {args.out}  ({args.out.stat().st_size} bytes)")
    print(receipt["verdict"])


if __name__ == "__main__":
    main()
