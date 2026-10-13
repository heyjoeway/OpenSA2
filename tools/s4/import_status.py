#!/usr/bin/env python3
"""Publish M4's numerator from the runtime's own import table.

M4 has read `0/251 (stage not started)` since S0, because nothing implemented
an import. S4 slice 2 binds the import address table and implements fourteen
of them, so the metric is measurable for the first time and this writes its
input.

Three deliberate choices, because M4's denominator and its second clause are
easy to overstate:

* **The denominator stays 251.** It is the count of import-address-table
  slots. The binary also resolves 451 names through `GetProcAddress`, and
  `Documentation/receipts/s4-import-surface-v1.json` argues at length why
  those are a different question with their own metric row: binding a slot
  happens once at load, answering a name happens per call, and only a
  handful of the names are OS entry points at all.

* **`implemented` counts a refusal only when it reproduces the native
  outcome.** The user ruled on 2026-09-23 that "a refusal counts as
  implemented only if it reproduces the native outcome"
  (`Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md`). So an
  import with a stub is counted unless `Documentation/refusal-reasons.json`
  holds an entry for it whose `native.reproduces` is not true; each one left
  out is listed in `not_counted` with the native outcome it fails to
  reproduce, or with a null outcome where the native-outcomes receipt holds
  none (the entry's why may cite other native evidence, such as a trace).
  `counted()` is the one function that decides, and
  `Documentation/scripts/check_replay_results.py` recomputes with it.

  `reproduces` is the entry's own claim, and what holds it is
  `Documentation/scripts/check_refused_presentation.py claim_problems`,
  which M4's second clause also runs: a claim to reproduce is refused when
  the outcome it names answers success natively and the entry names no
  switch, or the reach run's counts do not show the import called and never
  refused (the counts are recomputed from the run's import table, which holds
  no row for an import resolved through GetProcAddress, and a missing row is
  no count), when a switch it names is off in the replay configuration, when a
  variable it requires unset is set there, or when it names no measured
  outcome. What that does not see is a refusal whose failure differs from
  native's failure (E_FAIL against native's 1167, say): the markers' codes
  are not all literals, so the entry's reason has to say why the codes
  agree. Until the 2026-09-23 review this docstring said the flag "cannot be
  flipped without a gate noticing" while the switch list was the only guard,
  and an entry that claimed true with its switches deleted was counted.

* **`reached` is the replays' call logs, both sides, and what the native game
  calls before the Mod Loader's Init.** Design section 9 names M4's input as
  "replay call logs". The lifted side's is each replay's own run's import
  table (`lifted_logs`): for boot the reach run's, since boot's hooked run
  reproduces that run's iterations, total import calls, frames and stop
  (`tools/s4/tick_replay.py` refuses to compare otherwise), which is not a
  per-import comparison; for a stage replay its observed run's, which the
  tick-replay receipt pins and whose baseline shows the observers changed no
  import's count. The native side's is
  `harness/replay/native_import_probe.c`'s count, registered per replay in
  `Documentation/replays.json` and verified by `tools/s4/native_imports.py
  replay_calls`. That probe cannot see calls before the Mod Loader's Init, and
  the game's device and audio set-up runs there; so `native_pre_init` adds what
  the traced native boots of `Documentation/receipts/s4-native-outcomes-v1.json`
  show the game calling before Init. That set depends on the host's devices,
  and `tools/s4/native_imports.py replay_profile` refuses a replay whose native
  runs and lifted run did not share one recorded device profile, or whose host
  is not the traced boots' host. `reached` is the union. The lifted build does
  not take every path the native game takes (it never dispatches a window
  message, 2026-09-22); the union is how neither side's blind spot shrinks the
  set.

Usage: .venv/bin/python tools/s4/import_status.py [--out PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[2]
# Each driven run dumps the runtime's import table as it stood for that run.
# The metric wants the runtime as it stands now, which is the table with the
# most implementations -- not the newest file, because a probe linked against
# a narrower runtime can be re-run at any time and would otherwise walk the
# metric backwards. Ties go to the larger exercised set.
RUN = ROOT / "private" / "generated" / "s4" / "run"


REACH = ROOT / "Documentation" / "receipts" / "s4-run-reach-v1.json"
REASONS = ROOT / "Documentation" / "refusal-reasons.json"
DECISION = "Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md"


def reach_table() -> tuple[Path, str]:
    """The import table the receipted reach run dumped, and its sha256.

    This picked "the dumped table describing the most capable runtime" out of
    every probe's dumps until 2026-09-22, and nothing re-ran it: M4 read 122
    implemented from a 2026-09-20 dump while a 2026-09-21 dump already showed
    151. The reach receipt is gated for staleness against the runtime's own
    sources, so reading its table ties M4 to the tree the gates see.
    """
    if not REACH.is_file():
        raise SystemExit(f"{REACH.relative_to(ROOT)} is absent; run tools/s4/reach_receipt.py --replay boot --write")
    rec = (json.loads(REACH.read_text()).get("reach") or {}).get("import_table") or {}
    path = ROOT / str(rec.get("path", ""))
    if not rec.get("path") or not path.is_file():
        raise SystemExit("the reach receipt records no import table on disk; re-run "
                         "tools/s4/reach_receipt.py --replay boot --write")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != rec.get("sha256"):
        raise SystemExit(f"{path.relative_to(ROOT)} is not the table the reach receipt recorded")
    return path, digest


OUT = ROOT / "private" / "generated" / "import-status.json"
REGISTRY = ROOT / "Documentation" / "replays.json"
NOT_COUNTED = ("an entry in Documentation/refusal-reasons.json records that the runtime's "
               "answer to this import does not reproduce the native outcome it names, or "
               "names no receipt outcome (native.reproduces is not true), and the user's "
               f"ruling ({DECISION}) counts such a refusal as not implemented")


def counted(rows: list[dict], reasons: dict) -> tuple[list[str], list[dict]]:
    """M4's implemented list under the 2026-09-23 ruling, and what it leaves out.

    An import-table row counts when the runtime implements it and no refusal
    entry for it says its answer does not reproduce the native outcome. The
    join is `tools/s4/refusal_audit.py`'s key, so `XINPUT1_3.dll!Ordinal#4`
    and an entry naming `XINPUT1_3` / `Ordinal#4` are one import. A missing or
    malformed `reproduces` counts as not reproducing: the gate refuses such an
    entry, and M4 must not count what the gate has not accepted.
    """
    import refusal_audit as RA
    refusals = {RA.key(str(r.get("dll", "")), str(r.get("name", ""))): r
                for r in reasons.get("reasons", []) if isinstance(r, dict)}
    implemented, left_out = [], []
    for row in rows:
        if not row.get("implemented"):
            continue
        name = f"{row['dll']}!{row['name']}"
        entry = refusals.get(RA.key(row["dll"], row["name"]))
        native = (entry or {}).get("native") or {}
        if entry is None or native.get("reproduces") is True:
            implemented.append(name)
        else:
            left_out.append({"import": name, "outcome": native.get("outcome"),
                             "reproduces": native.get("reproduces")})
    return sorted(implemented), sorted(left_out, key=lambda r: r["import"])


def counted_nonreproducing(status: str | Path, reasons: str | Path) -> list[str]:
    """Imports M4's implemented list counts although a refusal entry says their
    answer does not reproduce the native outcome; [] when there are none.

    G4's M4 criterion runs this in the clause gate's overlay, where the files it
    is handed are the mutated ones, so it reads exactly the paths it is given and
    no module-level root.
    """
    import refusal_audit as RA
    implemented = json.loads(Path(status).read_text()).get("implemented", [])
    doc = json.loads(Path(reasons).read_text())
    bad = {RA.key(str(r.get("dll", "")), str(r.get("name", "")))
           for r in doc.get("reasons", [])
           if isinstance(r, dict) and ((r.get("native") or {}).get("reproduces") is not True)}
    return sorted(i for i in implemented if "!" in i and RA.key(*i.split("!", 1)) in bad)


TICK_REPLAY = "Documentation/receipts/s4-tick-replay-v1.json"


def called(rows: list[dict]) -> list[str]:
    """The imports an import table's run called."""
    return sorted(f"{r['dll']}!{r['name']}" for r in rows if r.get("calls"))


def implemented_rows(rows: list[dict]) -> list[str]:
    """The imports an import table's runtime implements."""
    return sorted(f"{r['dll']}!{r['name']}" for r in rows if r.get("implemented"))


def lifted_logs(replays: list[dict], rows: list[dict], root: Path = ROOT,
                tick_replay: dict | None = None) -> dict[str, list[str]]:
    """Each registered replay's lifted import log, by name.

    Boot's lifted side is the reach run, so its log is the reach table's. A
    stage entry (a `stage` block) runs its own lifted run
    (tools/s4/tick_replay.py run_stage_lifted), and its log is that run's
    import table, as the tick-replay receipt records it (`lifted.import_table`,
    path and sha256, checked here against the file). Its implemented list must
    be the reach table's: both come from one runtime, so a difference is a
    table from another build (the stage-entry spec's section 4).
    `tick_replay` is the receipt as a document, for a gate's falsification;
    None reads it from the tree."""
    if tick_replay is None:
        path = root / TICK_REPLAY
        tick_replay = json.loads(path.read_text()) if path.is_file() else {}
    docs = {d.get("name"): d for d in tick_replay.get("replays", [])}
    reach = called(rows)
    out: dict[str, list[str]] = {}
    for e in replays:
        if "stage" not in e:
            out[e["name"]] = reach
            continue
        rec = ((docs.get(e["name"]) or {}).get("lifted") or {}).get("import_table") or {}
        path = root / str(rec.get("path") or "")
        if not rec.get("path") or not path.is_file() or \
                hashlib.sha256(path.read_bytes()).hexdigest() != rec.get("sha256"):
            raise SystemExit(f"replay {e['name']}: its lifted run's import table "
                             f"({rec.get('path')!r}), as {TICK_REPLAY} records it, is missing "
                             "or does not hash to its pin; re-run tools/s4/tick_replay.py "
                             "--receipt")
        stage_rows = json.loads(path.read_text()).get("rows") or []
        if implemented_rows(stage_rows) != implemented_rows(rows):
            raise SystemExit(f"replay {e['name']}: its lifted run's import table implements "
                             "other imports than the reach run's, so the two tables come from "
                             "different runtimes; re-run tools/s4/reach_receipt.py and "
                             "tools/s4/tick_replay.py --receipt on one tree")
        out[e["name"]] = called(stage_rows)
    return out


def replay_reach(registry: dict, rows: list[dict], table: dict[int, str] | None = None,
                 root: Path = ROOT, probe: Path | None = None,
                 tick_replay: dict | None = None) -> dict:
    """M4's replay-reachable set from its logs, and how each side contributed.

    Every registered replay must carry a native import log and a device profile
    both sides ran with; one that does not is refused rather than counted from
    the lifted side alone. The lifted log is each replay's own (lifted_logs):
    the reach run's table for boot, whose lifted side is the reach run, and a
    stage entry's own run's table. `lifted` is their union, and
    `lifted_by_replay` keeps each. `native_pre_init` is the native-outcomes
    receipt's, which `native_imports.replay_profile` has tied to each replay's
    host and devices.
    """
    import native_imports as NI
    import native_outcomes as NO
    replays = registry.get("replays", [])
    if not replays:
        raise SystemExit("no replay is registered, so no import is reachable on one")
    receipt = root / NO.RECEIPT_REL
    outcomes = NO.load(receipt)
    profiles = {e["name"]: NI.replay_profile(e, root, outcomes) for e in replays}
    by_replay = lifted_logs(replays, rows, root, tick_replay)
    lifted = sorted(set().union(*by_replay.values()))
    native = {e["name"]: NI.replay_calls(e, table, root, probe or NI.PROBE) for e in replays}
    pre = NO.pre_init_imports(outcomes)
    both = set().union(*native.values()) | set(pre)
    return {
        "reached": sorted(both | set(lifted)),
        "lifted": lifted,
        "lifted_by_replay": by_replay,
        "native": native,
        "native_pre_init": pre,
        "native_pre_init_source": {"receipt": NO.RECEIPT_REL,
                                   "sha256": hashlib.sha256(receipt.read_bytes()).hexdigest()},
        "device_profiles": profiles,
        "native_only": sorted(both - set(lifted)),
        "lifted_only": sorted(set(lifted) - both),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=OUT,
                    help=f"where to write the status (default {OUT.relative_to(ROOT)}, M4's input)")
    a = ap.parse_args(argv)
    table, digest = reach_table()
    rows = json.loads(table.read_text())["rows"]
    implemented, not_counted = counted(rows, json.loads(REASONS.read_text()))
    exercised = sorted(f"{r['dll']}!{r['name']}" for r in rows if r.get("calls"))

    reach = replay_reach(json.loads(REGISTRY.read_text()), rows)
    reached = reach["reached"]
    note = ("reached is the union of the replays' native import logs, what the traced native "
            "boots show the game calling before the Mod Loader's Init (native_pre_init, from "
            "Documentation/receipts/s4-native-outcomes-v1.json), and the lifted reach run's "
            "table (tools/s4/import_status.py, docstring). native_only lists what the native "
            "game called and the lifted build did not. lifted_only is what lies outside the "
            "native window: the lifted run continues through the game's exit path after the "
            "host's WM_QUIT, which makes the set larger, the safe direction. No log sees the "
            "game's window procedure, which the Mod Loader replaces natively; "
            "Documentation/replays.json names it. implemented leaves out not_counted, for "
            "not_counted_reason.")
    a.out.write_text(json.dumps({
        "note": note,
        "source": str(table.relative_to(ROOT)),
        "source_sha256": digest,
        "implemented": implemented,
        "not_counted": not_counted,
        "not_counted_reason": NOT_COUNTED,
        "reached": reached,
        "replay_reach": reach,
        "exercised_by_a_driven_probe": exercised,
    }, indent=1, sort_keys=True) + "\n")
    shown = a.out.relative_to(ROOT) if a.out.is_relative_to(ROOT) else a.out
    print(f"import status: implemented {len(implemented)} ({len(not_counted)} not counted: "
          f"refusals that do not reproduce native), reached {len(reached)} "
          f"({len(reach['native_only'])} native only, {len(reach['lifted_only'])} lifted only, "
          f"{len(reach['native_pre_init'])} native before Init), of them implemented "
          f"{len(set(reached) & set(implemented))}, from {table.relative_to(ROOT)} and "
          f"{len(reach['native'])} replay log(s) -> {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
