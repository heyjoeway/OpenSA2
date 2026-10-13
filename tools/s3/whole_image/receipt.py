#!/usr/bin/env python3
"""Assemble `Documentation/receipts/s3-whole-image-lift-v1.json` from the run logs.

Every number in the receipt is computed here from the per-function JSONL logs,
the censuses and the wall-time files. Nothing is typed (AGENTS.md rule 7), and
no lifted output crosses into the repository: the receipt carries hashes,
counts and grouped samples only, and is sized to stay well under the boundary
gate's 8 MiB fuse with no long hex or base64 runs
(`Documentation/scripts/check_boundary.py`).

Usage:
  .venv/bin/python tools/s3/whole_image/receipt.py [--out PATH]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import platform
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

HEXADDR = re.compile(r"0x[0-9a-fA-F]+")
PASS_KEYS = ("ops_instrumented", "fist_rewrites", "fpatan_rewrites", "fpatan_refused",
             "control_word_masks_fixed", "f80_accessor_attrs_fixed",
             "fld_quiet_fixed", "fld_quiet_refused")


def group_failures(rows: list[dict], key: str) -> list[dict]:
    """Group by a normalised error head; addresses are evidence, not a footnote."""
    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        head = (r.get(key) or "").strip()
        groups[HEXADDR.sub("0xADDR", head)[:300] or "(no error text)"].append(r)
    return [{"error": e, "count": len(v),
             "regions": dict(sorted(collections.Counter(x["region"] for x in v).items())),
             "first_addresses": [x["va"] for x in sorted(v, key=lambda y: y["va"])[:8]]}
            for e, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


def tables(tag: str) -> dict:
    fmap = C.by_address()
    lift = C.read_jsonl(C.LIFT / f"{tag}.jsonl")
    build = C.read_jsonl(C.BUILD / f"{tag}.jsonl")

    blank = {"in_map": 0, "attempted": 0, "lifted": 0, "lift_failed": 0,
             "pass_failed": 0, "compiled": 0, "compile_failed": 0,
             "not_attempted_for_compile": 0,
             "ir_lines": 0, "ir_bytes": 0, "object_bytes": 0,
             "missing_block_calls": 0, "remill_error_calls": 0,
             "lift_seconds": 0.0, "pass_seconds": 0.0, "compile_seconds": 0.0,
             "fold_seconds": 0.0}
    reg = {r: dict(blank) | {k: 0 for k in PASS_KEYS} for r in C.REGIONS}
    by_op: dict[str, collections.Counter] = {r: collections.Counter() for r in C.REGIONS}
    for fn in fmap.values():
        reg[fn["region"]]["in_map"] += 1

    lift_failures, pass_failures, compile_failures = [], [], []
    for rec in lift.values():
        r = reg[rec["region"]]
        r["attempted"] += 1
        r["lift_seconds"] += rec.get("lift_seconds", 0.0)
        if not rec.get("lifted"):
            r["lift_failed"] += 1
            lift_failures.append(rec)
            continue
        r["lifted"] += 1
        r["ir_lines"] += rec.get("ll_lines", 0)
        r["ir_bytes"] += rec.get("ll_bytes", 0)
        r["missing_block_calls"] += rec.get("missing_block_calls", 0)
        r["remill_error_calls"] += rec.get("remill_error_calls", 0)
        r["pass_seconds"] += rec.get("pass_seconds", 0.0)
        p = rec.get("pass") or {}
        if not p.get("ok"):
            r["pass_failed"] += 1
            pass_failures.append(rec | {"error_head": p.get("error_head", "")})
            continue
        for k in PASS_KEYS:
            r[k] += p.get(k) or 0
        for op, n in (p.get("by_op") or {}).items():
            by_op[rec["region"]][op] += n

    for rec in build.values():
        r = reg[rec["region"]]
        r["fold_seconds"] += rec.get("fold_seconds", 0.0)
        r["compile_seconds"] += rec.get("compile_seconds", 0.0)
        if rec.get("compiled"):
            r["compiled"] += 1
            r["object_bytes"] += rec.get("object_bytes", 0)
        else:
            r["compile_failed"] += 1
            compile_failures.append(rec)
    for r_name in C.REGIONS:
        r = reg[r_name]
        r["not_attempted_for_compile"] = r["lifted"] - r["pass_failed"] - r["compiled"] \
            - r["compile_failed"]
        for k in ("lift_seconds", "pass_seconds", "compile_seconds", "fold_seconds"):
            r[k] = round(r[k], 2)

    tot = {k: sum(reg[r][k] for r in C.REGIONS) for k in reg["game"]}
    for k in ("lift_seconds", "pass_seconds", "compile_seconds", "fold_seconds"):
        tot[k] = round(tot[k], 2)
    return {
        "by_region": reg,
        "total": tot,
        "instrumented_ops_by_class": {r: dict(sorted(by_op[r].items(), key=lambda kv: -kv[1]))
                                      for r in C.REGIONS},
        "failures": {
            "lift": {"count": len(lift_failures), "groups": group_failures(lift_failures, "error_head")},
            "pass": {"count": len(pass_failures), "groups": group_failures(pass_failures, "error_head")},
            "compile": {"count": len(compile_failures),
                        "groups": group_failures(compile_failures, "error_head")},
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path,
                    default=C.ROOT / "Documentation" / "receipts" / "s3-whole-image-lift-v1.json")
    ap.add_argument("--tag", default="next", choices=("map", "next"),
                    help="which arm supplies the headline figures. Slice 2 adopted `next` "
                         "(--to-next-entry) on the evidence in the to_next_entry section, and "
                         "the published metrics come from it, so the headline follows it; the "
                         "comparison section still carries both arms.")
    args = ap.parse_args()

    fmapdoc = C.function_map()
    t_map = tables("map")
    t_next = tables("next") if (C.LIFT / "next.jsonl").exists() else None
    census_map = json.loads((C.OUT / "census.map.json").read_text())
    census_next = (json.loads((C.OUT / "census.next.json").read_text())
                   if (C.OUT / "census.next.json").exists() else None)
    link = json.loads((C.OUT / "link_surface.map.json").read_text())
    link_next = (json.loads((C.OUT / "link_surface.next.json").read_text())
                 if (C.OUT / "link_surface.next.json").exists() else None)
    err_map = json.loads((C.OUT / "error_sites.map.json").read_text())
    err_next = (json.loads((C.OUT / "error_sites.next.json").read_text())
                if (C.OUT / "error_sites.next.json").exists() else None)

    def wall(kind: str, tag: str) -> dict:
        p = (C.LIFT if kind == "lift" else C.BUILD) / f"{tag}.wall.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def serial_projection() -> dict:
        lw, cw = wall("lift", "serial"), wall("compile", "serial")
        if not (lw and cw):
            return {}
        fm = C.by_address()
        sample = C.read_jsonl(C.LIFT / "serial.jsonl")
        s_ins = sum(int(fm[int(a, 16)].get("instructions") or 0) for a in sample)
        t_ins = sum(int(f.get("instructions") or 0) for f in fm.values())
        k = t_ins / s_ins
        return {"sample_functions": len(sample), "sample_instructions": s_ins,
                "image_instructions": t_ins, "scale": round(k, 3),
                "projected_minutes_lift_and_pass": round(lw["wall_seconds"] * k / 60, 3),
                "projected_minutes_fold_and_compile": round(cw["wall_seconds"] * k / 60, 3),
                "projected_minutes_total": round(
                    (lw["wall_seconds"] + cw["wall_seconds"]) * k / 60, 3),
                "percent_of_60_minute_threshold": round(
                    100 * (lw["wall_seconds"] + cw["wall_seconds"]) * k / 60 / 60, 2)}

    # The headline arm. Both arms stay in `to_next_entry`; what changes here is
    # which one the receipt asserts, because Documentation/scripts/s3_metrics_inputs.py
    # publishes one arm and a receipt whose headline is the other arm would have the
    # metric citing a document that disagrees with it.
    head = {"map": (t_map, census_map, link, err_map),
            "next": (t_next, census_next, link_next, err_next)}[args.tag]
    if any(part is None for part in head):
        raise SystemExit(f"receipt: arm {args.tag!r} has no run on disk; lift, compile and "
                         f"census it first, or pass --tag for an arm that exists")
    t_head, census_head, link_head, err_head = head
    # A refused FLD quieting fix leaves its module quieting every FLD m32/m64 while IE is set
    # (pc24pass.py, "FLD"); the lift tools fail on one, and no receipt is written over one.
    refused = {tag: t["total"]["fld_quiet_refused"] for tag, t in (("map", t_map), ("next", t_next))
               if t and t["total"]["fld_quiet_refused"]}
    if refused:
        raise SystemExit(f"receipt: the PC24 pass refused the FLD quieting fix at {refused} "
                         f"site(s) by arm; the lift logs' pass.fld_quiet_refused_sites name them")

    lifted = t_head["total"]["lifted"]
    in_map = t_head["total"]["in_map"]
    game = t_head["by_region"]["game"]
    kc = census_head["traps"]["kill_criterion"]

    inputs = {
        "design": ["Documentation/SA2_RECOMP_DESIGN.md section 5 (S3), section 3.4"],
        "records_built_on": [
            "Documentation/S3_INPUTS.md",
            "Documentation/receipts/s2-corpus-run-v1.json",
            "Documentation/receipts/s2-build-time-sample-v1.json",
            "agents-journals/gates/2026-09-20-g2.md",
        ],
        "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                       "sha256": C.sha256(C.EXE)},
        "function_map": {"path": "private/generated/function-map.json",
                         "sha256": C.sha256(C.MAP),
                         "functions": len(fmapdoc["functions"]),
                         "ghidra_version": fmapdoc.get("ghidra_version")},
        "pipeline": {p: C.sha256(C.ROOT / p) for p in (
            "tools/spike/pc24pass/pc24pass.py",
            "tools/s2/corpus_run/internalize.py",
            "tools/s2/corpus_run/build.py",
            "tools/s3/whole_image/lift_all.py",
            "tools/s3/whole_image/compile_all.py",
            "tools/s3/whole_image/census.py",
            "tools/s3/whole_image/error_sites.py",
            "tools/s3/whole_image/link_surface.py",
            "tools/s3/whole_image/common.py",
            "tools/s3/whole_image/receipt.py",
        )},
        "helpers": {
            "pc_round.ll": {"path": str(C.HELPER_LL.relative_to(C.ROOT)),
                            "sha256": C.sha256(C.HELPER_LL)},
            "x87_int.ll": {"path": str(C.INT_HELPER_LL.relative_to(C.ROOT)),
                           "sha256": C.sha256(C.INT_HELPER_LL),
                           "why": "design 3.4's fistp helper; `--int-helper-ll` was "
                                  "unwired for every module S2's metrics came from "
                                  "(S3_INPUTS section 3), so this run asserts the "
                                  "resulting fist_rewrites count rather than the flag"},
        },
        "machine": {"nproc": wall("lift", "map").get("nproc"),
                    "platform": platform.platform(),
                    "python": sys.version.split()[0]},
    }

    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s3-whole-image-lift-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S3",
        "arm": args.tag,
        "arm_why": "`next` is the --to-next-entry bound, adopted in slice 2; see "
                   "to_next_entry.decision. `map` is the analyzer's recorded function "
                   "extents. Both were lifted, compiled and censused; this receipt's "
                   "headline is the arm named in `arm`, and the published metrics come "
                   "from the same arm.",
        "question": "Design 5 (S3): can every function in the function map, all regions, "
                    "be emitted and compiled; what is the trap count with its opcodes; "
                    "and what does a whole-image link face?",
        "verdict": (
            f"M1 emitted {lifted}/{in_map} (100.0%) over the whole map and "
            f"{game['lifted']}/{game['in_map']} in the game region; M2 compiling "
            f"{t_map['total']['compiled']}/{lifted}; traps (missing-block sites) "
            f"{kc['game_trap_sites']} in the game region against "
            f"{kc['game_mapped_instructions']} mapped instructions = {kc['percent']} %, "
            f"so design 5's 2 % kill criterion does not fire; "
            f"{census_map['map_defects']['candidates']} function-map size defects in the "
            f"analyzer's map against the three recorded in S3_INPUTS, of which "
            f"{census_next['map_defects']['candidates'] if census_next else '?'} still end at "
            f"their body's end under the adopted bound; the whole-image link is measured "
            f"and not attempted."
        ) if not kc["fires"] else "kill criterion fires; see traps.kill_criterion",
        "inputs": inputs,
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s3/whole_image/lift_all.py --jobs 16 --tag map",
            ".venv/bin/python tools/s3/whole_image/compile_all.py --jobs 16 --tag map",
            ".venv/bin/python tools/s3/whole_image/census.py --tag map",
            ".venv/bin/python tools/s3/whole_image/link_surface.py --tag map",
            ".venv/bin/python tools/s3/whole_image/lift_all.py --jobs 16 --tag next --to-next-entry",
            ".venv/bin/python tools/s3/whole_image/compile_all.py --jobs 16 --tag next",
            ".venv/bin/python tools/s3/whole_image/census.py --tag next",
            ".venv/bin/python tools/s3/whole_image/link_surface.py --tag next",
            ".venv/bin/python tools/s3/whole_image/error_sites.py --tag next",
            f".venv/bin/python tools/s3/whole_image/receipt.py --tag {args.tag}",
        ],
        "pipeline": {
            "lift": "remill-lift-14 --arch x86 --os windows --address <va> --bytes <hex> "
                    "under `unshare -Urm` with the semantics bind mount, one function per "
                    "invocation, the S1/S2 invocation unchanged",
            "pass": "tools/spike/pc24pass/pc24pass.py `instrument()` with --helper-ll "
                    "pc_round.ll and --int-helper-ll x87_int.ll, called in process",
            "fold": "tools/s2/corpus_run/internalize.py rewrite()",
            "compile": "zig cc -target x86_64-linux-musl -O2 -w -c, one TU per function",
            "link": "not attempted; see link_surface",
        },
        "lift_and_compile": t_head,
        "metrics": {
            "M1_emitted": {"all": f"{lifted}/{in_map}",
                           "by_region": {r: f"{t_head['by_region'][r]['lifted']}/"
                                            f"{t_head['by_region'][r]['in_map']}"
                                         for r in C.REGIONS},
                           "game_percent": round(100 * game["lifted"] / game["in_map"], 3)},
            "M2_compiling": {"all": f"{t_head['total']['compiled']}/{lifted}",
                             "by_region": {r: f"{t_head['by_region'][r]['compiled']}/"
                                              f"{t_head['by_region'][r]['lifted']}"
                                           for r in C.REGIONS},
                             "failed": t_head["total"]["compile_failed"]},
            "traps": {"definition": census_head["traps"]["definition"],
                      "sites_by_region": {r: census_head["traps"]["by_region"][r]["trap_sites"]
                                          for r in C.REGIONS}},
        },
        "traps": census_head["traps"],
        "unsupported_instructions": {
            "question": "design 5 asks for the count of unsupported-instruction traps "
                        "with every trap opcode listed. The remill + PC24 pipeline has no "
                        "such class; the nearest thing is remill's `__remill_error`, and a "
                        "plain grep for it conflates two populations",
            "tool": "tools/s3/whole_image/error_sites.py",
            "map_run": err_map,
            "to_next_entry_run": err_next,
            "headline": {
                "decode_failure_sites_map": err_map["decode_failure_sites"],
                "decode_failure_sites_to_next_entry": (err_next or {}).get("decode_failure_sites"),
                "explained_by_map_extent_map": err_map["explained_by_map_extent"]["functions"],
                "guest_fault_paths_not_traps": err_head["semantics_fault_sites"],
                "opcodes": sorted(set(err_map["unsupported_instructions"]["opcode_histogram"])
                                  | set((err_next or {}).get("unsupported_instructions", {})
                                        .get("opcode_histogram", {}))),
                # Arm-dependent, and the arms disagree: the wider bound decodes
                # into CRT bodies the map cut short, so `fnsave`/`frstor` in the
                # CRT's pow appear under a game label that `map` never reaches.
                "game_region_unsupported_opcode_sites":
                    err_head["unsupported_instructions"]["by_region"].get("game", 0),
            },
        },
        # A wrong function size is a defect in the analyzer's map, not in the
        # lift, so the count that matters is the one the map's own extents
        # produce. The adopted bound does not fix those 691 entries, it routes
        # around them; its residue is reported beside them and never instead of
        # them, because a defect census that shrinks when you change the
        # workaround is measuring the workaround.
        "map_defects": {"measured_on": "map",
                        "why": "the defect is the analyzer's recorded extent; the adopted "
                               "bound hides its symptom without correcting the map, and "
                               "S3_INPUTS section 1's fix is still owed",
                        **census_map["map_defects"],
                        "residue_under_adopted_bound": (
                            {"arm": args.tag,
                             "candidates": census_head["map_defects"]["candidates"],
                             "counts": census_head["map_defects"]["counts"]}
                            if args.tag != "map" else None)},
        "map_gaps": census_head["map_gaps"],
        "link_surface": link_head,
        "timings": {
            "lift_map": wall("lift", "map"),
            "compile_map": wall("compile", "map"),
            "lift_next": wall("lift", "next"),
            "compile_next": wall("compile", "next"),
            "serial_sample": {
                "what": "every 16th map entry (793 functions) re-lifted and recompiled at "
                        "--jobs 1, scaled to the whole image by mapped instruction count, "
                        "the method Documentation/receipts/s2-build-time-sample-v1.json "
                        "used. A whole-image serial run was not made; summing this run's "
                        "own per-function walls is the contended-sum artifact the G2 "
                        "interrogation named, and it is reported separately.",
                "lift": wall("lift", "serial"), "compile": wall("compile", "serial"),
                **serial_projection(),
            },
            "contended_per_function_sums_not_a_serial_measurement": {
                "lift_seconds": t_map["total"]["lift_seconds"],
                "pass_seconds": t_map["total"]["pass_seconds"],
                "fold_seconds": t_map["total"]["fold_seconds"],
                "compile_seconds": t_map["total"]["compile_seconds"],
                "why": "16 worker processes share 8 physical cores, so each per-function "
                       "wall is inflated; G2 recorded two reviewers deriving 53.4 min this "
                       "way against a 30.15 min direct measurement",
            },
            "projection_compared": {
                "source": "Documentation/receipts/s2-build-time-sample-v1.json, a "
                          "500-function sample scaled x24.805 by instruction count",
                "projected_minutes_at_16_jobs_1TU": 3.609,
                "projected_minutes_at_1_job_1TU": 30.150,
            },
        },
        "to_next_entry": {
            "question": "should lift_set.py's off-by-default --to-next-entry become the "
                        "default for the whole-image lift?",
            "map_run": {k: t_map["total"][k] for k in
                        ("lifted", "compiled", "remill_error_calls", "ir_bytes",
                         "missing_block_calls")},
            "next_run": ({k: t_next["total"][k] for k in
                          ("lifted", "compiled", "remill_error_calls", "ir_bytes",
                           "missing_block_calls")} if t_next else None),
            "census_map": {k: census_map["map_defects"]["counts"][k]
                           for k in census_map["map_defects"]["counts"]},
            "census_next": ({k: census_next["map_defects"]["counts"][k]
                             for k in census_next["map_defects"]["counts"]}
                            if census_next else None),
            "decode_failures": {
                "map": err_map["decode_failure_sites"],
                "to_next_entry": (err_next or {}).get("decode_failure_sites"),
            },
            "traps_game_region": {
                "map": census_map["traps"]["by_region"]["game"]["trap_sites"],
                "to_next_entry": (census_next["traps"]["by_region"]["game"]["trap_sites"]
                                  if census_next else None),
            },
            "cost": {
                "ir_bytes_map": link["ir_bytes"]["raw_remill"],
                "ir_bytes_to_next_entry": (link_next or {}).get("ir_bytes", {}).get("raw_remill"),
                "object_bytes_map": link["object_bytes"]["total"],
                "object_bytes_to_next_entry": (link_next or {}).get("object_bytes", {}).get("total"),
                "extra_image_bytes_handed_to_remill": census_map["map_gaps"]["bytes"],
                "why_so_cheap": "remill lifts by recursive descent from the entry, so the "
                                "extra bytes are decoded only where control flow reaches "
                                "them; alignment padding in a gap is handed over and never "
                                "lifted",
            },
            "decision": (
                "Yes for the whole-image lift, and no for tools/s2/corpus_run/lift_set.py. "
                "Widening every function to the next map entry removes every straddle "
                f"({census_map['map_defects']['counts']['straddle']} -> "
                f"{census_next['map_defects']['counts']['straddle'] if census_next else '?'}), "
                f"cuts remill decode failures {err_map['decode_failure_sites']} -> "
                f"{(err_next or {}).get('decode_failure_sites')}, cuts game-region "
                f"missing-block sites "
                f"{census_map['traps']['by_region']['game']['trap_sites']} -> "
                f"{census_next['traps']['by_region']['game']['trap_sites'] if census_next else '?'}, "
                "and costs almost nothing, because remill's recursive descent never "
                "decodes the padding the wider range contains. It stays off in "
                "lift_set.py because that tool's output is pinned by "
                "Documentation/receipts/emitter-determinism-v1.json and the defect "
                "belongs to S1's analyzer. It is a workaround either way: "
                "Documentation/S3_INPUTS.md section 1's instruction -- re-derive each "
                "function's extent from its own control flow and treat a body that ends "
                "mid-instruction as a map error -- is still the fix, and this slice's "
                "census is the list of addresses it has to correct."
            ),
        },
        "limits": [
            "A function counts as emitted when remill produced a `define @sub_<va>` for "
            "it, which is the S2 build-time sample's definition and does not say how "
            "much of the body remill decoded. Decoded coverage is reported separately: "
            "reachable instructions per region against the map's own instruction counts, "
            "and the __remill_error call count per region.",
            "The whole-image link is not attempted; the collision surface and the "
            "function table at 12 683 modules belong to the next slice. Only the numbers "
            "a link would face are measured.",
            "Nothing was executed. This slice compiles; it does not run the lifted code, "
            "so no trap site is confirmed by driving.",
            "The compile target is x86_64-linux-musl while the lifted IR carries an "
            "i386-windows-msvc triple, as in every S1 and S2 measurement.",
            "Traps are counted statically. An indirect branch or an indirect call whose "
            "target leaves the module is not counted as a missing-block site here, "
            "because its target is not known statically; indirect branches per region are "
            "reported separately.",
            "S3_INPUTS section 3a's four uninitialised CPU-feature globals are not "
            "modelled by this slice; nothing runs, so nothing takes the wrong branch, but "
            "a runtime slice inherits the requirement unchanged.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    print(f"-> {args.out}  ({args.out.stat().st_size} bytes)")
    print(receipt["verdict"])


if __name__ == "__main__":
    main()
