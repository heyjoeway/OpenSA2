#!/usr/bin/env python3
"""Assemble the guest-file-system receipt from the files the runs wrote.

Subtitle: `tools/s4/crt_receipt.py` for the file-I/O slice. Every number comes
out of a generated file with its hash; nothing in the output is typed.

The receipt is written to `private/generated/s4/`, not to
`Documentation/receipts/`: this slice does not own the durable record and an
integrating thread places it.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/fs_receipt.py --tag next+allarms
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))
import common as C  # noqa: E402

GEN4 = C.GEN / "s4"
RUN = GEN4 / "run"
LINK = GEN4 / "link"

PIPELINE = [
    "runtime/sa2_files.c", "runtime/sa2_files.h",
    "runtime/sa2_imports.c", "runtime/sa2_imports.h", "runtime/guest_memory.h",
    "harness/crt_probe.c", "harness/vfs_probe.c",
    "tools/s4/link_s4.py", "tools/s4/crt_run.py", "tools/s4/build_cost.py",
    "tools/s4/vfs_check.py", "tools/s4/vfs_overlay.py", "tools/s4/fs_matrix.py",
    "tools/s4/fs_break.py", "tools/s4/install_guard.py", "tools/s4/fs_receipt.py",
]


def load(p: Path):
    return json.loads(p.read_text()) if p.is_file() else None


def _regression(corpus, controls):
    """Counted out of the two suites' own result files rather than scraped from
    their stdout, so a number here cannot be a tally nobody recomputed."""
    out = {}
    if corpus:
        rows = corpus.get("results", [])
        out["fixture_corpus"] = f"{sum(1 for r in rows if r.get('passed'))}/{len(rows)}"
        out["fixture_corpus_variant"] = corpus.get("variant")
    if controls:
        every = [c for rows in controls.values() for c in rows]
        asserting = [c for c in every if c.get("kind") != "probe"]
        breaks = [c for c in every if c.get("kind") == "break"]
        circular = [c for c in breaks if not c.get("moves_a_column_the_lift_decides")]
        out["controls"] = (f"{sum(1 for c in asserting if c.get('control_holds'))}/"
                           f"{len(asserting)}")
        out["break_controls_moving_nothing"] = len(circular)
        out["probe_controls"] = len(every) - len(asserting)
        out["fixtures_with_controls"] = len(controls)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next+allarms")
    ap.add_argument("--name", default="fs.next+allarms")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    base = load(RUN / f"{args.name}.baseline.json")
    matrix = load(RUN / f"{args.name}.matrix.json")
    brk = load(RUN / "fs.break.verdict.json")
    vfs = load(GEN4 / "vfs" / "vfs.verdict.json")
    vfs_fold = load(GEN4 / "vfs" / "vfs.fold.json")
    guard = load(GEN4 / "guard" / "install.compare.before-after-all.json")
    cost = load(GEN4 / "build_cost" / f"build_cost.{args.tag}.json")
    linkst = load(LINK / f"main.{args.name}.json")
    extra = load(LINK / f"extra_functions.{args.tag}.json")
    imports = load(RUN / "fs.imports.table.json")
    prev_imports = load(RUN / "crt.imports.table.json")
    corpus = load(C.GEN / "s2" / "corpus_run" / "run.pc24.json")
    controls = load(C.GEN / "s2" / "corpus_run" / "controls.json")
    missing = [n for n, v in (("baseline", base), ("matrix", matrix), ("break", brk),
                              ("vfs", vfs), ("guard", guard), ("cost", cost),
                              ("link", linkst), ("imports", imports)) if v is None]
    if missing:
        raise SystemExit(f"these inputs have not been generated yet: {missing}")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(C.ROOT),
                          capture_output=True, text=True).stdout.strip()
    files = base["files"]

    doc = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-guest-filesystem-v1",
        "stage": "S4",
        "date": time.strftime("%Y-%m-%d"),
        "question": "Given its own installed files, how far does the guest get, and can a "
                    "guest path ever name a host file outside the installation?",
        "authority": "driven",
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 3.5, section 5 S4"],
            "inherited": [
                "Documentation/receipts/s4-crt-startup-v1.json",
                "Documentation/receipts/s4-import-binding-v1.json",
                "Documentation/receipts/s3-build-time-v2.json",
                "agents-journals/s4/2026-09-20-s4-crt-startup.md",
            ],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe",
                           "sha256": C.sha256(C.EXE)},
            "installation": {
                "guest_root": files["guest_root"],
                "host_read_root_is_the_user_s_own_install": True,
                "files": guard["files_after"],
                "note": "the installation is read-only input; no path under it is ever "
                        "opened for writing and its bytes appear in nothing tracked",
            },
            "binary": {
                "tag": args.tag,
                "path": linkst["link"].get("binary"),
                "sha256": linkst["link"].get("sha256"),
                "bytes": linkst["link"].get("binary_bytes"),
                "lifted_objects": linkst["link"].get("objects_lifted"),
                "extra_blocks": extra.get("registered") if extra else None,
                "runtime_and_driver_objects": linkst["link"].get("objects_runtime"),
            },
            "pipeline": {f: C.sha256(C.ROOT / f) for f in PIPELINE},
            "git_head": head,
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s4/install_guard.py --snapshot before",
            ".venv/bin/python tools/s4/vfs_check.py",
            f".venv/bin/python tools/s4/crt_run.py --rounds 1 --name {args.name} "
            f"--out fs.round.json --probe-arg=--require-files --probe-arg=--require-stop "
            f"--probe-arg=--stop-import --probe-arg=LoadIconW "
            f"--env SA2_INSTALL=private/install "
            f"--env SA2_VFS_WRITE_ROOT=private/generated/s4/vfsroot",
            f".venv/bin/python tools/s4/fs_matrix.py --name {args.name}",
            ".venv/bin/python tools/s4/fs_break.py --name fs.break",
            ".venv/bin/python tools/s4/install_guard.py --snapshot after-all --compare before",
            f".venv/bin/python tools/s4/build_cost.py --tag {args.tag} --repeats 3",
            ".venv/bin/python tools/s2/corpus_run/run.py",
            ".venv/bin/python tools/s2/corpus_run/controls.py",
            ".venv/bin/python Documentation/scripts/run_all_gates.py",
        ],
        "mapping": {
            "guest_root": files["guest_root"],
            "read_root": files["read_root"],
            "write_root": files["write_root"],
            "rule": "a guest path is normalised lexically (separators folded, '.' dropped, "
                    "'..' popped, a pop past the root refused), the guest root's own "
                    "components are stripped case-blind, and the remainder is resolved one "
                    "component at a time under the overlay and then the installation",
            "case": "exact spelling first, then an ASCII case-folded scan of the parent "
                    "directory; two host entries differing only in case refuse",
            "writes": "always under the write root; an existing installation file is copied "
                      "up first unless the open truncates",
            "escape": "SA2_VFS_ESCAPE and the other refusals raise a trap in the import stub, "
                      "never ERROR_FILE_NOT_FOUND",
        },
        "unit_probe": {
            "json": "private/generated/s4/vfs/vfs.verdict.json",
            "cases": vfs_fold["tally"] if vfs_fold else None,
            "case_fold_is_load_bearing": vfs["case_fold_is_load_bearing"],
            "case_fold_evidence": vfs["case_fold_evidence"],
            "ok": vfs["ok"],
        },
        "run": {
            "json": f"private/generated/s4/run/{args.name}.baseline.json",
            "ok": base["ok"],
            "import_calls": base["calls"]["total"],
            "distinct_imports_called": base["calls"]["distinct_imports"],
            "stop": base["trap"]["note"],
            "stop_pc": base["trap"]["exit_pc"],
            "files": {
                "stats": files["stats"],
                "opened": files["opened"],
                "resolutions": files["resolutions"],
                "guest_seek_to_end": files["guest_seek_to_end"],
                "config": files["config"],
                "escape_test": files["escape_test"],
            },
            "assert": base["assert"],
        },
        "matrix": {
            "json": f"private/generated/s4/run/{args.name}.matrix.json",
            "config_by_column": matrix["config_by_column"],
            "checks": matrix["checks"],
            "ok": matrix["ok"],
        },
        "deliberate_break": {
            "json": "private/generated/s4/run/fs.break.verdict.json",
            "targets": brk["targets"],
            "targets_failed": brk["targets_failed"],
            "expected_collateral": brk.get("expected_collateral"),
            "untouched_assertions_still_true": brk["untouched_assertions_still_true"],
            "sources_restored": brk["restored"],
            "unit_probe_rc": brk["vfs_check_rc"],
            "ok": brk["ok"],
        },
        "installation_untouched": {
            "json": "private/generated/s4/guard/install.compare.before-after-all.json",
            "compared": guard["compared"],
            "added": len(guard["added"]),
            "removed": len(guard["removed"]),
            "changed": len(guard["changed"]),
            "identical": guard["identical"],
            "method": "path, size, mode, mtime to the nanosecond, and the SHA-256 of every "
                      "file at or under 1 MiB, taken before the first run and after the last",
        },
        "imports": {
            "slots": imports["imports"],
            "implemented": imports["implemented"],
            "implemented_before_this_slice": prev_imports["implemented"] if prev_imports else None,
            "called_in_this_run": imports["called_in_this_run"],
            "newly_implemented": sorted(
                r["name"] for r in imports["rows"] if r["implemented"]
                and prev_imports and not any(p["name"] == r["name"] and p["implemented"]
                                             for p in prev_imports["rows"])),
        },
        "build_time": {
            "json": f"private/generated/s4/build_cost/build_cost.{args.tag}.json",
            "criterion_minutes_added": cost["delta"]["criterion_minutes_added"],
            "compile_seconds_added": cost["delta"]["compile_seconds_added"],
            "compile_seconds_added_by_file": cost["delta"].get("compile_seconds_added_by_file"),
            "link_seconds_added": cost["delta"]["link_seconds_added"],
            "binary_bytes_added": cost["delta"]["binary_bytes_added"],
            "modules_lifted_by_this_slice": 0,
            "module_total_unchanged": (linkst["link"]["objects_lifted"]
                                       + (extra.get("registered", 0) if extra else 0)),
            "why": cost["delta"]["why_zero"],
        },
        "regression": _regression(corpus, controls),
        "limits": [
            "FindFirstFile/FindNextFile are implemented but no guest code reached them in "
            "this run, so they are exercised only by the unit probe's directory scan.",
            "The overlay has no whiteout layer: deleting a file that exists only in the "
            "installation refuses by name rather than hiding it.",
            "HeapSize, the SA2_IMPORT_LOG_MAX ceiling and the other carried items from "
            "the CRT slice are unchanged.",
            "The k-th seek-to-end is matched to the k-th open by order, which holds because "
            "opens and their seeks are strictly interleaved on this path.",
        ],
    }
    # A receipt is a tracked record and belongs with the other receipts. It
    # was produced under private/generated while the slice ran, because that
    # slice could not write to Documentation/; the path moved when it landed.
    out = Path(args.out) if args.out else (
        C.ROOT / "Documentation" / "receipts" / "s4-guest-filesystem-v1.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"-> {out}")
    print(f"   run ok={doc['run']['ok']} matrix ok={doc['matrix']['ok']} "
          f"break ok={doc['deliberate_break']['ok']} unit ok={doc['unit_probe']['ok']} "
          f"install identical={doc['installation_untouched']['identical']}")


if __name__ == "__main__":
    main()
