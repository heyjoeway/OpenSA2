#!/usr/bin/env python3
"""Run the installer and launcher cases whose inputs exist in a source checkout.

Game-installation baseline comparisons, SDL3 dialog integration, and Windows
cross-compilation remain in their original test modules. This command reports
them as outside its scope instead of claiming a complete platform test pass.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    (ROOT / "private/generated").mkdir(parents=True, exist_ok=True)
    return subprocess.call([
        sys.executable, "-m", "pytest", "-q", "-p", "tools.source_tests",
        "Documentation/scripts/tests/test_installer_core.py",
        "Documentation/scripts/tests/test_installer_launch.py",
    ], cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
