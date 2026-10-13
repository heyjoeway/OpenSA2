#!/usr/bin/env python3
"""Write the M1..M8 inputs that record S1's state: no emitter has run.

Design 4.7 asks the standing questions at every gate and does not pass a
gate on "not measured". At G1 the measured state is that nothing the
metrics count exists yet: there is no emitter (M1 is "functions with a
lifted C body", design 8; the ten spike functions were lifted by remill
outside any emitter and are recorded, with their fixture verdicts, in
Documentation/receipts/s1-lifter-spike-v1.json), so nothing is emitted and
nothing compiled; no fixture is registered (S2 registers the inherited
corpus, design 3.7: the 36 kernels' fixtures; the spike rows stay in the
spike receipt); no import is implemented; no replay exists; no stage boots;
no target is green. This script writes those zeros into the files
metrics.py reads. They are the measured state, not literals pretending to
be measurements: each file records the absence of the stage that would
fill it, and metrics.py renders them as "0/12683 (0.0%)", "0/0: no emitted
functions", "0/0: no fixtures registered" with the status
"measured (stage not started)".

  private/generated/emit-log.json       emitted = [], traps = []; note names the
                                        spike receipt and the count of functions
                                        it lifted outside the emitter
  private/generated/build-log.json      compiled = [], failed = []
  Documentation/receipts/fixture-registry.json
                                        the M3 denominator; seeded empty here only
                                        when absent, because S2 registers the
                                        inherited corpus into it and re-running this
                                        script must not erase that
                                        with the note that S2 registers the corpus
  private/generated/fixture-results.json   likewise seeded only when absent
                                        passed = [], failed = [], regressions = []
  private/generated/import-status.json  implemented = [], reached = []
  private/generated/replay-results.json replays = []
  Documentation/receipts/stage-list.json
                                        the stage numbers in Documentation/scope/install.csv
                                        (the one value here that is derived, not zero)
  private/generated/stage-results.json  booting = [], replay_complete = []
  private/generated/targets.json        green = []

M7's inputs (d3d9 methods) are not derivable from S1 artifacts and stay
absent. Re-running is idempotent. S2 replaces the generated files with its
own emitter's logs and fills the registry with the corpus.

Usage: .venv/bin/python Documentation/scripts/s1_metrics_inputs.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from gitlog import project_root

SPIKE = "Documentation/receipts/s1-lifter-spike-v1.json"
INSTALL = "Documentation/scope/install.csv"
SELF = "Documentation/scripts/s1_metrics_inputs.py"


def seed_if_absent(root: Path, rel: str, data: dict) -> None:
    """Write `data` only when the file does not exist; never clobber a later
    stage's version of it."""
    if (root / rel).exists():
        print(f"  kept (owned by a later stage): {rel}")
        return
    write(root, rel, data)


def write(root: Path, rel: str, data: dict) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main() -> None:
    root = project_root(None)
    spike = json.loads((root / SPIKE).read_text())
    outside = sum(1 for f in spike["lifters"]["remill"]["functions"] if f["lifted"])
    stages = sorted({r["stage"] for r in csv.DictReader((root / INSTALL).open()) if r["stage"]})

    write(root, "private/generated/emit-log.json",
          {"note": f"no emitter exists at S1; {outside} functions were lifted by remill outside "
                   f"the emitter and are recorded in {SPIKE}, not counted here",
           "emitted": [], "traps": []})
    write(root, "private/generated/build-log.json",
          {"note": "no emitter exists at S1, so nothing was compiled", "compiled": [], "failed": []})
    # The fixture registry and its results belonged to S1 only while no fixture
    # was registered. S2 registers the inherited corpus (design 3.7) with
    # tools/s2/fixtures/register_corpus.py, and the harness writes the results.
    # Re-running this script must not erase that work, so both files are
    # written here only when they do not exist yet.
    seed_if_absent(root, "Documentation/receipts/fixture-registry.json",
                   {"schema": "sa2-recomp-fixture-registry/v1", "generated_by": SELF,
                    "note": "S1: no fixture registered. S2 registers the inherited corpus "
                            "(design 3.7) with tools/s2/fixtures/register_corpus.py; the "
                            f"spike's rows stay in {SPIKE}.",
                    "fixtures": []})
    seed_if_absent(root, "private/generated/fixture-results.json",
                   {"note": "no fixture run yet", "passed": [], "failed": [], "regressions": []})
    write(root, "private/generated/import-status.json", {"implemented": [], "reached": []})
    write(root, "private/generated/replay-results.json", {"replays": []})
    write(root, "Documentation/receipts/stage-list.json",
          {"schema": "sa2-recomp-receipt/v1", "id": "stage-list-v1", "source": INSTALL,
           "generated_by": SELF,
           "question": "Which stage numbers carry stage, camera or SET files in the installation?",
           "authority": "shipped-data-derived", "stages": stages})
    write(root, "private/generated/stage-results.json", {"booting": [], "replay_complete": []})
    # M8's input belongs to S7's generator (tools/s7/targets.py), and metrics.py refuses one it did
    # not write, so the S1 seed never clobbers it.
    seed_if_absent(root, "private/generated/targets.json", {"green": []})
    print(f"S1_METRICS_INPUTS emitted=0 compiled=0 fixtures=0 (spike functions lifted outside "
          f"the emitter: {outside}) stages={len(stages)}")


if __name__ == "__main__":
    main()
