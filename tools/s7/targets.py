#!/usr/bin/env python3
"""Write M8's input, private/generated/targets.json, from the tracked per-target receipts.

M8 (`Documentation/scripts/metrics.py`) counts the targets this file lists green. Design
5's S7 work asks that the list be generated from tracked per-target receipts, with what
green means written down: that record is `Documentation/s7-green-means.md`, and each
target's receipt is `Documentation/receipts/s7-target-<target>-v1.json`, written by
`tools/s7/target_receipt.py`.

A target is green here when its receipt is current and holds a true verdict for every clause
the record's table lists for it. The receipt's own `green` flag is not taken: it is recomputed
from the verdicts, and a receipt whose flag disagrees is refused. Nor are the verdicts taken as
written: each receipt is recomputed with the receipt tool beside this one, over the same
evidence on this machine, and a receipt that differs from its recomputation in anything but
`written` and `commit` is not current, so a typed or stale receipt is never counted. A target
with no receipt is not green and is listed with a null receipt, so the file says which
receipts exist.

A receipt that is not current is stale: its target is not green, and the file says why, in
that receipt's entry (`stale`). The file is still written, so M8 reads what the current
receipts hold between a change that moves a receipt's evidence (a runtime edit moves the reach
and replay receipts) and the milestone that re-runs the receipt
(tools/s7/refresh_after_runtime.py). A receipt is stale when it:
- was written under another version of the record (its `green_means_sha256`);
- cites a tracked evidence file that is absent or no longer has the sha256 it recorded;
- is not what the receipt tool writes now (`target_receipt.moved`), which is also how a typed
  receipt reads.

The tool refuses to write, and exits 1, when the record is absent or lists no clause for a
target, or when a receipt is not one it can read at all:
- not JSON, or not schema `sa2-s7-target/v1` written by `tools/s7/target_receipt.py`;
- naming another target, or another record of what green means;
- lacking a clause the record lists, or holding a clause without a boolean verdict;
- holding a true verdict with no evidence, or citing evidence without a path and sha256;
- reading green other than its clause verdicts do.

Usage: .venv/bin/python tools/s7/targets.py [--root PATH] [--out PATH] [--check]
  --check   compare with the written file (ignoring `written`) and write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = "tools/s7/targets.py"
OUT = "private/generated/targets.json"
TARGETS = ("x86_64-linux", "x86_64-windows", "arm64-linux")
GREEN_MEANS = "Documentation/s7-green-means.md"
RECEIPT = "Documentation/receipts/s7-target-{target}-v1.json"
RECEIPT_SCHEMA = "sa2-s7-target/v1"
RECEIPT_TOOL = "tools/s7/target_receipt.py"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")
CLAUSE_HEADER = re.compile(r"^\|\s*Target\s*\|\s*Clause\s*\|")


class Refused(Exception):
    """A record or receipt this tool cannot read: the message says which and why."""


class Stale(Refused):
    """A receipt that is no longer the tree's (see the module docstring): its target is not
    green, and the message is what the file says why."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def receipt_tool():
    """tools/s7/target_receipt.py, the tool beside this one, loaded once."""
    if "s7_target_receipt" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "s7_target_receipt", Path(__file__).resolve().parent / "target_receipt.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["s7_target_receipt"] = module
        spec.loader.exec_module(module)
    return sys.modules["s7_target_receipt"]


def listed_clauses(text: str) -> dict[str, list[str]]:
    """The record's clause table: target -> its clauses, in the record's order. Read on its
    own here rather than imported from the receipt tool, so the two read the record
    independently."""
    clauses: dict[str, list[str]] = {}
    inside = False
    for line in text.splitlines():
        line = line.strip()
        if CLAUSE_HEADER.match(line):
            inside = True
            continue
        if not line.startswith("|"):
            inside = False
            continue
        match = ROW.match(line) if inside else None
        if match:
            clauses.setdefault(match.group(1), []).append(match.group(2))
    return clauses


def verified(root: Path, target: str, means_sha: str, clauses: list[str]) -> tuple[bool, str]:
    """(green recomputed from the receipt's clause verdicts, the receipt's sha256). Raises
    Stale for a receipt that is not current, and Refused for one it cannot read."""
    rel = RECEIPT.format(target=target)
    path = root / rel
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise Refused(f"{rel} is not JSON: {error}") from None
    if not isinstance(doc, dict) or doc.get("schema") != RECEIPT_SCHEMA \
            or doc.get("generated_by") != RECEIPT_TOOL:
        raise Refused(f"{rel} is not a {RECEIPT_SCHEMA} receipt written by {RECEIPT_TOOL}")
    if doc.get("target") != target or doc.get("green_means") != GREEN_MEANS:
        raise Refused(f"{rel} is not {target}'s receipt under {GREEN_MEANS}")
    if doc.get("green_means_sha256") != means_sha:
        raise Stale(f"{rel} was written under another version of {GREEN_MEANS}; re-run "
                    f"{RECEIPT_TOOL} --target {target}")
    held = doc.get("clauses") if isinstance(doc.get("clauses"), dict) else {}
    verdicts = []
    # Evidence that moved makes the receipt stale, but only once the rest of it has been read:
    # a receipt that is also malformed is refused, whatever else moved.
    moved_evidence = None
    for name in clauses:
        entry = held.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("verdict"), bool):
            raise Refused(f"{rel} holds no verdict for the {name} clause")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or (entry["verdict"] and not evidence):
            raise Refused(f"{rel}: the {name} clause reads {entry['verdict']} with no evidence")
        for item in evidence:
            cited = item.get("path") if isinstance(item, dict) else None
            sha = item.get("sha256") if isinstance(item, dict) else None
            if not isinstance(cited, str) or not isinstance(sha, str) or not HEX64.match(sha):
                raise Refused(f"{rel}: the {name} clause cites evidence without a path and sha256")
            if cited.startswith("private/"):
                continue
            if moved_evidence is None and (not (root / cited).is_file()
                                           or sha256_file(root / cited) != sha):
                moved_evidence = (f"{rel}: {cited} is not the file its {name} clause read; "
                                  f"re-run {RECEIPT_TOOL} --target {target}")
        verdicts.append(entry["verdict"])
    green = bool(verdicts) and all(verdicts)
    if doc.get("green") is not green:
        raise Refused(f"{rel} reads green {doc.get('green')!r}, and its clause verdicts {green}")
    if moved_evidence is not None:
        raise Stale(moved_evidence)
    tool = receipt_tool()
    differ = tool.moved(doc, tool.build_receipt(root, target))
    if differ:
        raise Stale(f"{rel} is not what {RECEIPT_TOOL} writes from the evidence now (it "
                    f"differs in {', '.join(differ)}); re-run {RECEIPT_TOOL} --target {target}")
    return green, sha256_file(path)


def generate(root: Path) -> dict:
    means = root / GREEN_MEANS
    if not means.is_file():
        raise Refused(f"{GREEN_MEANS} is absent")
    means_sha = sha256_file(means)
    clauses = listed_clauses(means.read_text(encoding="utf-8"))
    receipts: dict[str, dict | None] = {}
    green = []
    for target in TARGETS:
        if not clauses.get(target):
            raise Refused(f"{GREEN_MEANS} lists no clause for {target}")
        rel = RECEIPT.format(target=target)
        if not (root / rel).is_file():
            receipts[target] = None
            continue
        try:
            is_green, sha = verified(root, target, means_sha, clauses[target])
        except Stale as stale:
            receipts[target] = {"path": rel, "sha256": sha256_file(root / rel), "green": False,
                                "stale": str(stale)}
            continue
        receipts[target] = {"path": rel, "sha256": sha, "green": is_green}
        if is_green:
            green.append(target)
    return {"schema": "sa2-s7-targets/v1", "generated_by": SELF, "green_means": GREEN_MEANS,
            "green_means_sha256": means_sha, "receipts": receipts, "green": green,
            "written": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out = args.out if args.out is not None else root / OUT
    try:
        doc = generate(root)
    except (Refused, OSError, ValueError) as error:
        print(f"targets REFUSED: {error}", file=sys.stderr)
        return 1
    absent = [t for t, r in doc["receipts"].items() if r is None]
    stale = [t for t, r in doc["receipts"].items() if r is not None and "stale" in r]
    said = (f"{len(doc['green'])}/{len(TARGETS)} green ({', '.join(doc['green']) or 'none'})"
            + (f"; no receipt for {', '.join(absent)}" if absent else "")
            + (f"; stale, so not green: {', '.join(stale)}" if stale else ""))
    for target in stale:
        print(f"targets STALE_RECEIPT {target}: {doc['receipts'][target]['stale']}")
    if args.check:
        try:
            held = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"targets STALE: {out} cannot be read: {error}")
            return 1
        if {k: v for k, v in held.items() if k != "written"} != \
                {k: v for k, v in doc.items() if k != "written"}:
            print(f"targets STALE: {out} is not what the receipts give: {said}")
            return 1
        print(f"targets CURRENT: {said}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"targets WROTE {out}: {said}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
