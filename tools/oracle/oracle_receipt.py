#!/usr/bin/env python3
"""Roll every oracle capture receipt up into one receipt an integrator can place.

Subtitle: one file that says what the live oracle produced and what it proved.

Reads `private/generated/oracle/captures/*/receipt.json`, the archaeology of
the lost captures and the fixture pin map if they are present, and writes
`private/generated/oracle/s4-live-oracle-v1.json`. Nothing here is typed: every
number comes from a receipt on disk.

It fails rather than reporting when a capture directory holds no receipt, and
when no capture is present at all — a roll-up over nothing would otherwise
report a clean run of zero captures.

Usage: .venv/bin/python tools/oracle/oracle_receipt.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "private" / "generated" / "oracle"
CAPTURES = OUT / "captures"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if not CAPTURES.is_dir():
        raise SystemExit(f"no captures directory: {CAPTURES}")
    directories = sorted(p for p in CAPTURES.iterdir() if p.is_dir())
    if not directories:
        raise SystemExit("no captures; refusing to roll up nothing")
    captures = []
    for directory in directories:
        receipt = directory / "receipt.json"
        if not receipt.is_file():
            raise SystemExit(f"capture directory without a receipt: {directory}")
        captures.append(json.loads(receipt.read_text()))
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip()
    tools = {
        path.name: sha256(path)
        for path in sorted(Path(__file__).resolve().parent.glob("*.py"))
    }
    artifacts = [a for c in captures for a in c["artifacts"]]
    accepted = [a for a in artifacts if a.get("present") and a.get("check", {}).get("ok")]
    rejected = [a for a in artifacts if a.get("present") and not a.get("check", {}).get("ok")]
    rollup = {
        "schema": "sa2-s4-live-oracle/v1",
        "private": True,
        "redistribute": False,
        "repository_head": head,
        "tools_sha256": tools,
        "captures": len(captures),
        "artifacts_total": len(artifacts),
        "artifacts_accepted": len(accepted),
        "artifacts_rejected": len(rejected),
        "artifacts_missing": len([a for a in artifacts if not a.get("present")]),
        "installation_untouched_every_run": all(c["installation_untouched"] for c in captures),
        "ran_on_live_desktop_session": all(
            c["session"]["live_desktop_session"] for c in captures
        ),
        "capture_index": [
            {
                "name": c["name"],
                "created_utc": c["created_utc"],
                "enabled_mods": c["inputs"]["enabled_mods"],
                "built_probes": c["inputs"].get("built_probes", []),
                "game_args": c["inputs"]["game_args"],
                "timeout_seconds": c["inputs"]["timeout_seconds"],
                "return_code": c["run"]["return_code"],
                "elapsed_seconds": c["run"]["elapsed_seconds"],
                "installation_untouched": c["installation_untouched"],
                "artifacts": [
                    {
                        "file": a["file"],
                        "present": a.get("present"),
                        "rows": a.get("check", {}).get("rows"),
                        "columns": a.get("check", {}).get("columns"),
                        "sha256": a.get("check", {}).get("sha256"),
                        "trimmed": a.get("trimmed"),
                        "problems": a.get("check", {}).get("problems", []),
                    }
                    for a in c["artifacts"]
                ],
            }
            for c in captures
        ],
    }
    for name, key in (
        ("lost-captures-archaeology.json", "lost_capture_archaeology"),
        ("fixture-pin-map.json", "fixture_pin_map"),
        ("determinism.json", "determinism"),
    ):
        path = OUT / name
        if path.is_file():
            rollup[key] = {"path": str(path), "sha256": sha256(path)}
    destination = OUT / "s4-live-oracle-v1.json"
    destination.write_text(json.dumps(rollup, indent=1, sort_keys=True) + "\n")
    print(
        f"SA2_ORACLE_RECEIPT captures={rollup['captures']} "
        f"artifacts={rollup['artifacts_total']} accepted={rollup['artifacts_accepted']} "
        f"rejected={rollup['artifacts_rejected']} "
        f"installation_untouched={rollup['installation_untouched_every_run']} "
        f"out={destination}"
    )


if __name__ == "__main__":
    main()
