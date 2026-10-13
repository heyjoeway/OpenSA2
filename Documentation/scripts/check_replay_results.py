#!/usr/bin/env python3
"""Gate: M5 describes the tree as it stands, and a tracked receipt backs it.

What this gate is for
---------------------
M5 is read from `private/generated/replay-results.json`, which
`tools/s4/tick_replay.py` writes after running the lifted build against each
retained native capture. It is one of the three criteria that decide whether
S4 is finished. A replay result is worth nothing once the build it ran has
moved: the reach number fell from 369 iterations to 1 on 2026-09-21 with every
gate green, because nothing tied the number to the inputs that produced it.

It never re-runs the guest -- that is a minute, and a gate that costs a minute
on every push is one people learn to skip. It checks, in milliseconds:

1. **Coverage.** `Documentation/receipts/s4-tick-replay-v1.json` names exactly
   the replays `Documentation/replays.json` registers, and every replay the
   last committed receipt held is still registered or is listed under
   `retired` with a reason. Design 7.3: a replay never silently stops being run.
2. **Inputs.** Each replay's recorded inputs -- what its lifted capture was
   built and run from, and what its comparison read -- equal the current ones,
   and the registry has not changed since the receipt was written.
3. **Outputs.** Each recorded comparison is recomputed from the kept captures
   and must come out the same; the native capture must still hash to its pin
   and have been built from the probe now in the tree; its capture and repeats
   must share one oracle mode, the one the registry names, and a
   `"oracle": "vanilla"` entry's must be the vanilla oracle's as it stands in
   the tree (`tools/s4/tick_replay.py native_oracle`); each must record that
   it started from the pinned saves the lifted run starts from
   (`tools/s4/tick_replay.py native_start`); M5's records are
   derived from the recorded results, not trusted beside them; and M5's input,
   the private results file, is those records exactly. An entry with a
   `word_policy` is recomputed from both sides' word logs, as the replay
   compared it (`tick_replay.word_native`, `word_compare`): every native log
   and the lifted one must still be its pin, the repeats must judge what was
   recorded, and the identity comparison -- values the host chooses compared
   by what they name, the user's one rule on M5 -- must come out the same. That
   takes seconds rather than milliseconds, once per process: the reader keeps
   what each log gives by the log's sha256 and the reader's build
   (tools/s4/wordlog.py), and a recomputed result is kept by the entry, the
   recorded replay and the sha256 of every file they name (`recompute`), so a
   falsification recomputes only the replay it changed. With Metal Harbor
   registered a pass took 56 minutes before either (the U6 MANIFEST).
   A registered control (the boot's stack control) is held against the
   entry's mask and its recorded verdict recomputed (`control_problems`).
4. **The leash.** Every registered replay's native capture is shorter than the
   reach configuration's poll budget, since the host posts WM_QUIT at it and a
   replay would otherwise judge the harness rather than the build.
5. **M4's replay-reachable set.** `private/generated/import-status.json`'s
   `reached` is recomputed from the replays' two call logs -- each replay's
   registered native import log, verified by `tools/s4/native_imports.py`, and
   the lifted reach run's import table -- and must be exactly that, and its
   `implemented` and `not_counted` must be exactly what
   `tools/s4/import_status.py counted()` makes of the table and
   `Documentation/refusal-reasons.json`, and `reached` adds the traced boots'
   pre-Init imports only for a replay `native_imports.replay_profile` accepts.
   M4's second clause is "every import hit by a retained replay is
   implemented", and it read an empty set as the replays' until 2026-09-22;
   until the 2026-09-23 review nothing checked `implemented`, so an edited
   list reached METRICS.md unchallenged. A stage replay's lifted log is its
   own run's import table, which the receipt pins (`import_status.py
   lifted_logs`).
6. **Stage replays.** An entry with a `stage` block is held, on every pass,
   to what `tools/s4/tick_replay.py` holds it to before it runs
   (`stage_entry_problems`: its stage, its pins, one run per native capture,
   one goal tick) and to its kept files (`stage_recheck`: every file of both
   lifted runs is its pin and sits where its run wrote it, each run's reach
   and device profile are its own record's, the observed run is its baseline,
   the input it applied is the recording's, and its poll budget outlasted the
   capture). Each native capture must be shown to have replayed the registered
   recording (`native_input_problems`), and every stage entry is refused unless
   it compares through its own digest mask, harness/replay/masks/<name>.h,
   whose sources' build and configuration each native capture is
   (`stage_mask_problems`). Its recorded stage-trace
   comparison is recomputed with the tick
   capture's. Until a stage replay is registered, the falsifications aimed at
   these run in Documentation/scripts/tests/test_replay_results_stage.py
   against a synthetic one, and here they are counted as not applicable.
7. **Residue classes.** The user's direction of 2026-09-26 (decisions 2 and 3,
   Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md) made two
   classes of words residues. Every registered entry's classes must be records
   a comparison may apply -- no word a named field, a stage probe's source,
   a field of the input the input probe applies or a word compared by
   identity (`tick_replay.record_problems`) -- and each recorded result must
   have been compared under exactly the classes the registry names and the
   groups their records give that replay, with its class, its lifted rules,
   its open-shift bound and what it would read with no class all what it holds
   (`tick_replay.residue_problems`). The recompute in 3 holds the report
   itself: every listed word on both sides, and the repeats', and each
   native-relative rule evaluated again on both sides' logs.

Re-running it
-------------

    source tools/env.sh
    .venv/bin/python tools/s4/tick_replay.py --receipt

Falsifications run on every pass, on both sides: some mutate what was
recorded, and some move a live input -- a runtime byte, a hook byte, the reach
-- in a copied tree, because a gate whose every falsification edits the record
cannot tell a blind live check from a working one (2026-09-22 review).

Usage: .venv/bin/python Documentation/scripts/check_replay_results.py
"""

from __future__ import annotations

import contextlib
import copy
import json
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

RECEIPT_REL = "Documentation/receipts/s4-tick-replay-v1.json"
RECEIPT = ROOT / RECEIPT_REL
REGISTRY = ROOT / "Documentation" / "replays.json"
RESULTS = ROOT / "private" / "generated" / "replay-results.json"
IMPORT_STATUS = ROOT / "private" / "generated" / "import-status.json"
RERUN = "Re-run tools/s4/tick_replay.py --receipt."


def head_names() -> list[str]:
    """Replay names in the last committed receipt; none before the first commit."""
    p = subprocess.run(["git", "show", f"HEAD:{RECEIPT_REL}"], cwd=str(ROOT),
                       capture_output=True, text=True)
    if p.returncode:
        return []
    return [r.get("name") for r in json.loads(p.stdout).get("replays", [])]


# Every falsification recomputes every registered replay, and one Metal Harbor
# recompute reads a dozen native word logs of 15,585 ticks: 56 minutes a pass
# when it was first registered (the U6 MANIFEST). So within one run a result is
# kept by everything it is computed from -- the registry entry, the recorded
# replay, and the sha256 of every file either names and of the tree files the
# comparison reads (`recompute_inputs`) -- and a case recomputes only the replay
# it changed. Nothing is kept across runs, or outside one (`kept`: a test that
# calls problems() with a tool patched computes afresh), a moved byte is another
# key, and the logs themselves are kept by content one level down
# (tools/s4/wordlog.py).
_RECOMPUTED: dict[str, dict | str] | None = None


@contextlib.contextmanager
def kept():
    """Within this, recomputed results are kept (`recompute`); nests."""
    global _RECOMPUTED
    outer = _RECOMPUTED
    if outer is None:
        _RECOMPUTED = {}
    try:
        yield
    finally:
        if outer is None:
            _RECOMPUTED = None


def recompute_inputs(entry: dict, doc: dict) -> str:
    """What a recompute of `entry` against `doc` reads, as one digest: both
    documents, and the sha256 of every file they name and of the comparison's
    own tree files (the replay's mask, the identity record, the field list, and
    the residue class records the entry names, which it names by id, with the
    stage probes' sources their words are kept out of)."""
    import hashlib
    import tick_replay as T

    files: set[str] = set()

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str) and ("/" in v) and len(v) < 512:
            p = ROOT / v
            if p.is_file():
                files.add(str(p))
    walk(entry)
    walk(doc.get("lifted"))
    classes = entry.get(T.RESIDUE_FIELD)
    records = [f"Documentation/receipts/residues/{rid}.json"
               for rid in (classes if isinstance(classes, list) else []) if isinstance(rid, str)]
    for rel in ([str(T.mask_for(entry).relative_to(ROOT)), T.IDENTITY_RECORD, T.BOOT_MASK,
                 "harness/replay/tick_fields.h", *T.STAGE_SOURCES] + records):
        if (ROOT / rel).is_file():
            files.add(str(ROOT / rel))
    h = hashlib.sha256(json.dumps([entry, doc], sort_keys=True).encode())
    for f in sorted(files):
        h.update(f.encode() + b"\0" + T.sha256(Path(f)).encode() + b"\0")
    return h.hexdigest()


def recompute(entry: dict, doc: dict) -> dict | str:
    """The comparison again, from the kept captures; a string says why it cannot
    run. Kept within a gate run by what it reads (the comment above)."""
    if _RECOMPUTED is None:
        return compute(entry, doc)
    key = recompute_inputs(entry, doc)
    if key not in _RECOMPUTED:
        _RECOMPUTED[key] = compute(entry, doc)
    return copy.deepcopy(_RECOMPUTED[key])


def compute(entry: dict, doc: dict) -> dict | str:
    """The comparison again, from the kept captures, every time."""
    import tick_replay as T

    lifted = ROOT / doc["lifted"]["capture"]
    if T.sha256(lifted) != doc["lifted"]["sha256"]:
        return f"the lifted capture {doc['lifted']['capture']} is missing or changed"
    header, native = T.read_capture(ROOT / entry["native"]["capture"])
    stop = (doc["lifted"].get("stop") or {}).get("note", "")
    with tempfile.TemporaryDirectory() as td:
        if "word_policy" in entry:
            # From both sides' word logs, as the replay compared them: each
            # native log and the lifted one must still be its pin, and the
            # repeats' judgement is recomputed under the one rule too.
            ctx = T.word_native(entry, Path(td))
            if ctx["det"] != (doc.get("native") or {}).get("determinism"):
                return "the native repeats no longer judge what the recorded result says"
            result = T.word_compare(entry, ctx, doc["lifted"], Path(td))
        else:
            det = T.native_repeats(entry, header, native, Path(td))
            lheader, lrows = T.read_capture(lifted)
            result = T.compare(header, native, lheader, lrows, entry.get("field_policy", {}),
                               T.capture_labels(entry), Path(td), judged=det["judged_ticks"],
                               name=entry["name"], lifted_stop=stop)
        if T.is_stage(entry):
            trace = doc["lifted"].get("stage_trace") or {}
            path = ROOT / str(trace.get("path") or "")
            if not trace.get("path") or T.sha256(path) != trace.get("sha256"):
                return f"the lifted stage trace {trace.get('path')} is missing or changed"
            result["trace"] = T.compare_trace(entry, path, Path(td), lifted_stop=stop)
        return result


def problems(receipt: dict | None = None, registry: dict | None = None,
             results: dict | None = None, live: dict | None = None,
             registry_sha: str | None = None, committed: list[str] | None = None,
             outputs: bool = True, poll_budget: int | None = None,
             status: dict | None = None, probe: Path | None = None) -> list[str]:
    import tick_replay as T

    out: list[str] = []
    if receipt is None:
        if not RECEIPT.is_file():
            return [f"{RECEIPT_REL} does not exist; M5 has no tracked record. {RERUN}"]
        receipt = json.loads(RECEIPT.read_text())
    if registry is None:
        registry = json.loads(REGISTRY.read_text())
    if registry_sha is None:
        registry_sha = T.sha256(REGISTRY)
    if committed is None:
        committed = head_names()
    if receipt.get("id") != "s4-tick-replay-v1":
        out.append(f"receipt id is {receipt.get('id')!r}, expected 's4-tick-replay-v1'")

    entries = registry.get("replays", [])
    registered = [e["name"] for e in entries]
    recorded = [r.get("name") for r in receipt.get("replays", [])]
    if recorded != registered:
        out.append(f"the receipt covers {recorded} and the registry registers {registered}, "
                   f"in that order; a retained replay is never silently dropped. {RERUN}")
    retired = {r.get("name"): r for r in registry.get("retired", [])}
    for name in committed:
        if name in registered:
            continue
        why = str((retired.get(name) or {}).get("reason", "")).strip()
        if not why:
            out.append(f"replay {name!r} was in the last committed receipt and is neither "
                       "registered nor retired with a reason (design 7.3)")
    if receipt.get("registry_sha256") != registry_sha:
        out.append(f"Documentation/replays.json changed since the receipt was written. {RERUN}")

    # The lifted side of every replay is the reach run, which the host ends with
    # WM_QUIT at its poll budget. A budget no longer than a native capture makes
    # the replay judge the harness's leash: from development_snapshot the boot replay's
    # "first divergence at tick 600" was exactly that, against 1200 native ticks.
    budget = T.poll_budget() if poll_budget is None else poll_budget
    for entry in entries:
        if T.is_stage(entry):
            continue                 # its own budget, recorded and checked by stage_recheck
        cap = ROOT / entry["native"]["capture"]
        if budget and cap.is_file():
            ticks = len(T.read_capture(cap)[1])
            if budget <= ticks:
                out.append(f"replay {entry['name']}'s native capture has {ticks} ticks and the "
                           f"reach configuration's poll budget is {budget} (SA2_PUMP_POLLS): the "
                           f"lifted run is told to quit before the capture ends")

    by_name = {e["name"]: e for e in entries}
    for doc in receipt.get("replays", []):
        entry = by_name.get(doc.get("name"))
        if entry is None:
            continue
        now = live[doc["name"]] if live is not None else T.inputs(entry)
        was = doc.get("inputs") or {}
        for side in sorted(set(now) | set(was)):
            a, b = now.get(side) or {}, was.get(side) or {}
            moved = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            if moved:
                out.append(f"replay {doc['name']} is stale: {side} inputs "
                           f"{', '.join(moved)} differ from when it ran. {RERUN}")
        if outputs:
            if T.sha256(ROOT / entry["native"]["capture"]) != entry["native"]["sha256"]:
                out.append(f"replay {doc['name']}'s native capture {entry['native']['capture']} "
                           "is missing or no longer hashes to its pin")
            try:
                T.native_probe_current(entry)
            except SystemExit as e:
                out.append(f"replay {doc['name']}: {e}")
            # The replay tool refuses a capture whose oracle is not the one the
            # registry names before it compares; a recorded result must not
            # outlive that refusal (fold-profile's spec I, 2026-09-23).
            try:
                T.native_oracle(entry)
            except SystemExit as e:
                out.append(f"replay {doc['name']}: {e}")
            # The lifted run starts from the pinned saves, so the capture and
            # every repeat must have too; a result must not outlive a pin that
            # moved (P6's review, W1).
            try:
                T.native_start(entry)
            except SystemExit as e:
                out.append(f"replay {doc['name']}: {e}")
            # The lifted run ran in its capture's directory (2026-09-24): the CRT
            # parses the command line built from it, so another directory is
            # another run (the working-directory review, W1).
            try:
                T.lifted_guest(entry, doc.get("lifted") or {})
            except SystemExit as e:
                out.append(f"replay {doc['name']}: {e}")
            if T.is_stage(entry):
                out.extend(stage_problems(entry, doc))
            # A control (the boot's stack control) is held against the mask and
            # judges nothing; its recorded verdict must still be what it shows
            # (tick_replay.control_problems; the U6 reviews, masks W3).
            out.extend(f"replay {doc['name']}: {p}" for p in T.control_problems(entry))
            try:
                again = recompute(entry, doc)
            except SystemExit as e:
                again = str(e)
            if isinstance(again, str):
                out.append(f"replay {doc['name']}'s comparison cannot be recomputed: {again}")
            elif again != doc.get("result"):
                out.append(f"replay {doc['name']}'s recorded comparison is not what its kept "
                           f"captures give. {RERUN}")

    out.extend(m4_problems(registry, status, probe, receipt))

    # The residue classes (the user's direction of 2026-09-26): every registered entry's
    # records stand, each result was compared under the classes the registry names, and
    # its class, rules, bound and what it would read with none are what it holds
    # (tick_replay.residue_problems, which M5's own check runs too).
    out.extend(T.residue_problems(receipt, registry, ROOT))

    derived = [T.m5_record(d) for d in receipt.get("replays", [])]
    if receipt.get("m5") != derived:
        out.append("the receipt's m5 section is not derived from its own recorded results")
    if results is None:
        if not RESULTS.is_file():
            out.append(f"{RESULTS.relative_to(ROOT)} does not exist, so M5 cannot be the "
                       f"receipt's. {RERUN}")
            return out
        results = json.loads(RESULTS.read_text())
    if results.get("replays") != derived:
        out.append(f"M5's input ({RESULTS.relative_to(ROOT)}) is not the receipt's recorded "
                   f"results, so the metric says something the tracked record does not. {RERUN}")
    return out


def stage_problems(entry: dict, doc: dict) -> list[str]:
    """Check 6 for one stage entry: tools/s4/tick_replay.py's own checks of the
    entry and of its recorded runs' kept files."""
    import tick_replay as T

    name = entry["name"]
    out = [f"replay {name}: {p}" for p in T.stage_entry_problems(entry)]
    cap = ROOT / entry["native"]["capture"]
    if T.sha256(cap) != entry["native"].get("sha256"):
        return out                   # the pin check above names it; no rows to count
    rows = len(T.read_capture(cap)[1])
    out += [f"replay {name}: {p}" for p in T.stage_recheck(entry, doc.get("lifted") or {}, rows)]
    return out


def m4_problems(registry: dict, status: dict | None = None,
                probe: Path | None = None, receipt: dict | None = None) -> list[str]:
    """Check 5: M4's `reached` is the replays' call logs, recomputed; a stage
    replay's lifted log is read through `receipt`, the tick-replay receipt."""
    import import_status as IS
    import tick_replay as T

    rerun = "Re-run tools/s4/import_status.py."
    if status is None:
        if not IMPORT_STATUS.is_file():
            return [f"{IMPORT_STATUS.relative_to(ROOT)} does not exist, so M4 has no "
                    f"replay-reachable set. {rerun}"]
        status = json.loads(IMPORT_STATUS.read_text())
    table = ROOT / str(status.get("source", ""))
    if not status.get("source") or not table.is_file() or \
            T.sha256(table) != status.get("source_sha256"):
        return [f"the import table M4 was read from ({status.get('source')!r}) is missing or "
                f"changed. {rerun}"]
    rows = json.loads(table.read_text())["rows"]
    implemented, not_counted = IS.counted(rows, json.loads(IS.REASONS.read_text()))
    # Two sentences, so a mutation of one list is told apart from a status
    # whose other list is already stale (the gate fold's verification, N2).
    out = []
    if status.get("implemented") != implemented:
        out.append(f"M4's implemented list in {IMPORT_STATUS.relative_to(ROOT)} is not the "
                   f"import table's ({status.get('source')}) less the refusals that do not "
                   f"reproduce the native outcome ({IS.REASONS.relative_to(ROOT)}). {rerun}")
    if status.get("not_counted") != not_counted:
        out.append(f"M4's not_counted list in {IMPORT_STATUS.relative_to(ROOT)} is not the "
                   f"refusals {IS.REASONS.relative_to(ROOT)} says do not reproduce the native "
                   f"outcome, among the import table's ({status.get('source')}). {rerun}")
    if out:
        return out
    try:
        again = IS.replay_reach(registry, rows, root=ROOT, probe=probe, tick_replay=receipt)
    except SystemExit as e:
        return [f"M4's replay-reachable set cannot be recomputed: {e}"]
    if status.get("reached") != again["reached"] or status.get("replay_reach") != again:
        return [f"M4's replay-reachable set in {IMPORT_STATUS.relative_to(ROOT)} is not what "
                f"the replays' call logs give. {rerun}"]
    return []


RAN = 0
STAGE_RAN = 0
STAGE_INAPPLICABLE: list[str] = []


def live_tree(mutate) -> dict:
    """Current inputs computed over a copy of the tree with one thing moved."""
    import tick_replay as T

    registry = json.loads(REGISTRY.read_text())
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        files = ([p.relative_to(ROOT) for g in T.RUNTIME_GLOBS for p in ROOT.glob(g)]
                 + [Path(t) for t in T.pipeline_files(registry.get("replays", []))]
                 + [Path("Documentation/receipts/s4-run-reach-v1.json")])
        for rel in files:
            (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / rel, tmp / rel)
        mutate(tmp)
        return {e["name"]: T.inputs(e, tmp) for e in registry.get("replays", [])}


def moved_probe(scratch: list[Path]) -> Path:
    """The import probe with one byte more, in a directory of the falsifications'
    scratch (`scratch_dirs`), removed with it."""
    import native_imports as NI
    d = Path(tempfile.mkdtemp(prefix="replay-probe-"))
    scratch.append(d)
    probe = d / NI.PROBE.name
    probe.write_bytes(NI.PROBE.read_bytes() + b"\n")
    return probe


# What the falsifications write outside the tree -- the moved import probe, the
# runs copied for a case -- is made in directories of their scratch, by
# moved_probe and copied_run alone, which one `finally` removes however the
# pass ends: done, an exception while the cases are built or while they run, or
# SIGTERM, which main() raises as `Stopped` and turns into exit 143. The probe
# was a file of its own in the temporary directory, removed only once the cases
# had run, and the passes stopped by hand left theirs in /tmp (the U6 fold
# review). SIGKILL still leaves the scratch; nothing else does.
@contextlib.contextmanager
def scratch_dirs():
    made: list[Path] = []
    try:
        yield made
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)


def falsifications() -> list[str]:
    with kept(), scratch_dirs() as scratch:
        return _falsifications(scratch)


def _falsifications(scratch: list[Path]) -> list[str]:
    import tick_replay as T

    if not RECEIPT.is_file() or not RESULTS.is_file():
        return ["the receipt or M5's input is absent, so no mutation of them can be shown "
                "to fail; the falsifications cannot run"]
    receipt = json.loads(RECEIPT.read_text())
    registry = json.loads(REGISTRY.read_text())
    results = json.loads(RESULTS.read_text())
    if not receipt.get("replays"):
        return ["the receipt records no replay, so there is nothing to mutate"]
    if not IMPORT_STATUS.is_file():
        return ["M4's import status is absent, so no mutation of it can be shown to fail"]
    status = json.loads(IMPORT_STATUS.read_text())
    base = {"receipt": receipt, "registry": registry, "results": results,
            "live": {e["name"]: T.inputs(e) for e in registry.get("replays", [])},
            "registry_sha": T.sha256(REGISTRY), "committed": [], "outputs": False,
            "poll_budget": None, "status": status, "probe": None}

    def first(d):
        return d["replays"][0]

    def case(name, part, f=None, value=None, outputs=False, names=None):
        docs = copy.deepcopy(base)
        docs["outputs"] = outputs
        docs["names"] = names
        if f is not None:
            f(docs[part])
        else:
            docs[part] = value
        return name, docs

    def byte(rel):
        def flip(tmp):
            p = tmp / rel
            p.write_bytes(p.read_bytes() + b"\n")
        return flip

    def reach_moved(tmp):
        p = tmp / "Documentation" / "receipts" / "s4-run-reach-v1.json"
        d = json.loads(p.read_text())
        d["reach"]["iterations"] += 1
        p.write_text(json.dumps(d))

    def started_elsewhere(d):
        """A repeat's capture directory copied to a scratch directory with its
        receipt's start saves moved off the pin, and the registry pointed at
        the copy. The copy carries the same log, profile and probes, so of the
        checks here only native_start reads what changed. Copied by copied_run,
        so the copy is in the scratch (the second fold review, nit 1)."""
        native = first(d)["native"]
        cap = (native.get("repeats") or [native])[0]

        def other_saves(dst):
            rec = json.loads((dst / "receipt.json").read_text())
            saves = rec.setdefault("session", {}).setdefault("saves", {})
            saves["at_start"] = dict(saves.get("at_start") or {}, NOT_A_SAVE="0" * 64)
            (dst / "receipt.json").write_text(json.dumps(rec))
        dst = copied_run(str((ROOT / str(cap["receipt"])).parent), scratch, other_saves)
        cap["receipt"] = str(dst / "receipt.json")

    cases = [
        # The recorded side.
        case("the registry entry changed", "receipt",
             lambda d: first(d)["inputs"]["compare"].__setitem__("registry_entry_sha256", "0" * 64)),
        case("a native capture was re-pinned", "receipt",
             lambda d: first(d)["inputs"]["compare"].__setitem__("native_sha256", ["0" * 64])),
        case("the registry file changed", "registry_sha", value="0" * 64),
        case("a registered replay has no result", "registry",
             lambda d: d["replays"].append(dict(d["replays"][0], name="unrun"))),
        case("a committed replay vanished without retirement", "committed", value=["gone"]),
        case("M5's input disagrees with the receipt", "results",
             lambda d: d["replays"][0].__setitem__("first_divergent_tick", 1)),
        case("M5's input dropped a replay", "results", lambda d: d.__setitem__("replays", [])),
        case("the receipt's m5 is not derived from its results", "receipt",
             lambda d: d["m5"][0].__setitem__("class", "residue pc53-double-rounding-v1")),
        # A change that moves the result whatever it is: setting first_divergence
        # to None stopped being one when the boot replay agreed and it already was.
        case("a recorded result was edited", "receipt",
             lambda d: first(d)["result"].__setitem__("compared_ticks",
                                                      first(d)["result"]["compared_ticks"] - 1),
             outputs=True),
        # The live side, moved in a copy of the tree.
        case("a runtime source changed", "live",
             value=live_tree(byte("runtime/sa2_trap.c"))),
        case("the tick field list changed", "live",
             value=live_tree(byte("harness/replay/tick_fields.h"))),
        case("the digest mask changed", "live",
             value=live_tree(byte("harness/replay/tick_digest.h"))),
        case("the comparer changed", "live",
             value=live_tree(byte("tools/s4/tick_replay.py"))),
        case("the reach moved under the same manifest", "live", value=live_tree(reach_moved)),
        case("the poll budget no longer outlasts a capture", "poll_budget", value=600),
        # The registry's oracle flag turned over: a loader entry claims the
        # vanilla oracle, or a vanilla entry drops the flag. Either way the
        # captures show the other mode, so it builds its condition whichever
        # oracle the boot replay is registered with.
        case("a registry entry names an oracle its captures do not show", "registry",
             lambda d: first(d).pop("oracle") if first(d).get("oracle") == "vanilla"
             else first(d).__setitem__("oracle", "vanilla"), outputs=True),
        # The pinned saves: a registered capture whose receipt says it started
        # from other saves than the lifted run starts from.
        # Caught only by a problem that names the saves: moving the receipt path also
        # moves what M4's status records, so another check can fire on the same
        # mutation and would hide a blind native_start (the fold review's W1).
        case("a registered capture did not start from the pinned saves", "registry",
             started_elsewhere, outputs=True,
             names=("started from saves", "records no start saves")),
        # The lifted run's directory: the recorded run edited to C:\SA2 while its
        # capture ran in the mirror (the working-directory review, W1).
        case("the recorded lifted run ran in another guest directory", "receipt",
             lambda d: d["replays"][0]["lifted"].__setitem__(
                 "guest_paths", {"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"}),
             outputs=True, names=("guest directory",)),
        # Check 5, M4's replay-reachable set.
        case("M4's reached set dropped an import only the native game called", "status",
             lambda d: d["reached"].remove(d["replay_reach"]["native_only"][0])),
        # A name no import table holds, so the case builds its condition whatever
        # the runtime implements. It named OLEAUT32.dll!Ordinal#2 until the WMI
        # unit's stubs implemented that ordinal (2026-09-23).
        case("M4's implemented list claims what the table does not", "status",
             lambda d: d.__setitem__("implemented", sorted(set(d["implemented"])
                                                           | {"NOT_AN_IMPORT.dll!Nothing"}))),
        case("M4's not_counted list holds what the reasons file does not leave out", "status",
             lambda d: d.__setitem__("not_counted", d.get("not_counted", []) + [
                 {"import": "NOT_AN_IMPORT.dll!Nothing", "outcome": None, "reproduces": None}])),
        case("a replay registers no native import log", "registry",
             lambda d: first(d)["native"].pop("imports")),
        case("an import log has no repeat", "registry",
             lambda d: first(d)["native"]["imports"].__setitem__("repeats", [])),
        case("an import log was re-pinned", "registry",
             lambda d: first(d)["native"]["imports"].__setitem__("sha256", "0" * 64)),
        case("an import log's run captured other ticks than the replay's", "registry",
             lambda d: first(d)["native"].__setitem__("sha256", "0" * 64)),
        case("a repeat's receipt did not collect its log", "registry",
             lambda d: first(d)["native"]["imports"]["repeats"][0].__setitem__(
                 "receipt", first(d)["native"]["imports"]["receipt"])),
        case("the import probe in the tree is not the one that ran", "probe",
             value=moved_probe(scratch)),
        # One native run is one directory (tick_replay.native_repeats, every
        # entry): the registered capture listed again as a repeat, and a repeat
        # listed twice, each read as a repeat agreeing on every tick.
        case("the registered capture listed again as its repeat", "registry",
             lambda d: first(d)["native"]["repeats"].insert(0, {
                 k: first(d)["native"][k] for k in ("capture", "sha256", "receipt")}),
             outputs=True),
        case("a repeat listed twice", "registry",
             lambda d: first(d)["native"]["repeats"].append(
                 copy.deepcopy(first(d)["native"]["repeats"][0])), outputs=True),
    ]
    cases += word_cases(base, case, byte)
    res, res_na = residue_cases(base, case, byte)
    cases += res
    stage, inapplicable = stage_cases(base, scratch)
    inapplicable = inapplicable + res_na
    cases += stage
    global RAN, STAGE_RAN, STAGE_INAPPLICABLE
    RAN, STAGE_RAN, STAGE_INAPPLICABLE = len(cases), len(stage), inapplicable
    return run_cases(base, cases)


def word_cases(base: dict, case, byte) -> list[tuple[str, dict]]:
    """The falsifications of a word-policy entry's comparison, for the first
    registered replay when it has one (the boot, since 2026-09-25): the
    recorded identity events, the logs' pins, the identity record and the
    reader. Each must be caught by the recompute from both sides' logs or by
    the inputs; none applies to an entry compared from its captures alone."""
    import tick_replay as T

    first = base["registry"]["replays"][0]
    recorded = ((base["receipt"].get("replays") or [{}])[0].get("result") or {}).get("identity")
    if "word_policy" not in first or not isinstance(recorded, dict):
        return []                    # a receipt from before the rule is stale: problems() says so

    def edit_event(d):
        evs = d["replays"][0]["result"]["identity"]["lifted_events"]
        t, c, was, now = evs[0].split(" ")
        evs[0] = f"{int(t) + 1} {c} {was} {now}"          # the first lifted event, a tick later

    def swap_repeat_log(d):
        reps = d["replays"][0]["native"]["repeats"]
        reps[0]["word_log"], reps[-1]["word_log"] = reps[-1]["word_log"], reps[0]["word_log"]

    return [
        case("a recorded identity event was edited", "receipt", edit_event, outputs=True),
        case("the recorded identity checks were edited", "receipt",
             lambda d: d["replays"][0]["result"]["identity"]["checks"]["open"].__setitem__(
                 "mismatches", 1), outputs=True),
        # a repeat's recorded divergence moved while the judged ticks stay: only
        # the recomputed determinism can see it (the gate review of 2026-09-25, W4)
        case("the recorded native determinism was edited", "receipt",
             lambda d: d["replays"][0]["native"]["determinism"]["repeats"][0].__setitem__(
                 "first_divergent_tick", 1000), outputs=True,
             names=("native repeats no longer judge",)),
        case("a native word log was re-pinned", "registry",
             lambda d: d["replays"][0]["native"]["word_log"].__setitem__("sha256", "0" * 64),
             outputs=True, names=("word log",)),
        case("two repeats' word logs registered for each other", "registry", swap_repeat_log,
             outputs=True, names=("word log", "not beside")),
        case("the lifted word log was re-pinned", "receipt",
             lambda d: d["replays"][0]["lifted"]["words"].__setitem__("sha256", "0" * 64),
             outputs=True, names=("lifted word log",)),
        case("the identity record changed", "live",
             value=live_tree(byte("harness/replay/masks/identity-words.json"))),
        case("the word-log reader changed", "live", value=live_tree(byte("tools/s4/wordlog.c"))),
    ] + [
        # Each word-policy replay's own mask moved by a byte: the replay is stale
        # (the U6 registration review, W5: "the digest mask changed" above edits
        # tick_digest.h, which no word-policy replay compares under).
        case(f"{e['name']}'s own digest mask changed", "live",
             value=live_tree(byte(str(T.mask_for(e).relative_to(ROOT)))),
             names=(f"replay {e['name']} is stale",))
        for e in base["registry"]["replays"]
        if "word_policy" in e and T.mask_for(e).resolve().parent == (ROOT / T.MASKS_DIR).resolve()
    ] + [
        # A control's recorded verdict edited: only the recomputed verdict sees it.
        case(f"{e['name']}'s control verdict was edited", "registry",
             lambda d, k=k: d["replays"][k]["controls"][0]["verdict"].__setitem__(
                 "words", d["replays"][k]["controls"][0]["verdict"]["words"] + 1),
             outputs=True, names=("no longer shows what its recorded verdict",))
        for k, e in enumerate(base["registry"]["replays"]) if e.get("controls")
    ]


def residue_cases(base: dict, case, byte) -> tuple[list[tuple[str, dict]], list[str]]:
    """The falsifications of the residue classes (the user's direction of
    2026-09-26), for the first registered replay when it names classes and its
    recorded result carries their report: the registry's classes dropped, a
    record moved, the report, a rule's verdict, the open-shift bound, the
    class and, for each native-relative form the report holds, that form's
    observations (one-tick-scan: a second tick apart; context-permutation: a
    lifted decode that is not native's), each edited. Each must be caught by a
    problem that names what it moved; none applies to a replay compared without
    classes."""
    import tick_replay as T

    regs = base["registry"]["replays"]
    k = next((i for i, e in enumerate(regs) if e.get(T.RESIDUE_FIELD)), None)
    if k is None:
        return [], ["residue classes: no registered replay names one"]
    first = regs[k]
    docs = base["receipt"].get("replays") or []
    j = next((i for i, d in enumerate(docs) if d.get("name") == first["name"]), None)
    block = ((docs[j].get("result") or {}).get("residues")) if j is not None else None
    if not isinstance(block, dict):
        return [], []                # a receipt from before the classes is stale: problems() says so

    def result(d):
        return d["replays"][j]["result"]

    def edit_report(d):
        g = result(d)["residues"]["groups"][0]
        g["lifted"]["differing"] += 1

    def rule_lied(d):
        g = next(g for g in result(d)["residues"]["groups"] if g["rule"]["kind"] == "zero")
        g["rule"]["nonzero_words"] = 1                 # a lifted word non-zero, recorded held

    def bound_typed(d):
        r = result(d)
        for where in (r["residues"].get("open_shift"), r["identity"].get("open_shift")):
            if where is not None:
                where["bound"] += 1

    def typed_over(d):
        r = result(d)
        if r.get("class") != "residue":
            r["class"] = "residue"                     # typed over its judged divergence
        else:
            r["first_divergence"] = {"tick": 1, "field": "frame_count", "kind": "value",
                                     "native": "0x00000001", "lifted": "0x00000002"}
    rid = first[T.RESIDUE_FIELD][0]
    cases = [
        case("the registry's residue classes removed", "registry",
             lambda d: d["replays"][k].pop(T.RESIDUE_FIELD),
             names=("compared under residue classes",)),
        case(f"residue class {rid}'s record changed", "live",
             value=live_tree(byte(f"Documentation/receipts/residues/{rid}.json")),
             names=(f"replay {first['name']} is stale",)),
        case("a recorded residue report was edited", "receipt", edit_report, outputs=True,
             names=("recorded comparison is not what",)),
        case("a lifted rule recorded held over values that break it", "receipt", rule_lied,
             names=("lifted rule",)),
        case("the recorded class typed over a judged divergence", "receipt", typed_over,
             names=("its class is",)),
    ]
    if block.get("open_shift") is not None:
        cases.append(case("the open-shift bound typed", "receipt", bound_typed,
                          names=("the bound is computed",)))

    # One case per native-relative form the first replay's report holds, each on the
    # first group of that form and writing what that form's verdict reads (the race gate
    # review, finding 2: one case on whichever native group came first never reached the
    # context-permutation rule, and wrote a key its verdict no longer reads).
    def native_lied(form):
        def edit(d):
            g = next(g for g in result(d)["residues"]["groups"]
                     if (g.get("rule") or {}).get("form") == form)
            obs = g["rule"]["observed"]
            if form == "one-tick-scan":                      # a second tick apart, recorded held
                obs["apart_ticks"] = g["rule"]["params"]["max_ticks"] + 1
                obs["apart"] = [dict(tick=t, lifted=["0x00000001"] * len(obs["words"]),
                                     native=["0x00000000"] * len(obs["words"]),
                                     progress=["0x00000001", "0x00000002"])
                                for t in range(1, obs["apart_ticks"] + 1)]
            else:                                            # a lifted decode not native's
                obs["decode"] = {"ticks": 1, "first": {"tick": 1, "va": "0x00000000",
                                                       "value": "0x00000001",
                                                       "native": "0x00000000"}}
        return edit
    forms = [f for f in T.NATIVE_FORMS
             if any((g.get("rule") or {}).get("kind") == "native"
                    and (g.get("rule") or {}).get("form") == f for g in block.get("groups") or [])]
    for form in forms:
        cases.append(case(f"a native-relative rule ({form}) recorded held over observed values "
                          "that break it", "receipt", native_lied(form), names=("lifted rule",)))
    return cases, []


def run_docs(docs: dict) -> list[str]:
    return problems(docs["receipt"], docs["registry"], docs["results"], docs["live"],
                    docs["registry_sha"], docs["committed"], docs["outputs"],
                    docs["poll_budget"], docs["status"], docs["probe"])


def run_cases(base: dict, cases: list[tuple[str, dict]]) -> list[str]:
    """Each case's survival, as a sentence; [] when every one was caught.

    A mutation is caught only by a problem the unmutated record does not
    already have, as check_refused_presentation's caught() requires: against
    a live record that already fails, every mutation would otherwise read as
    caught (the gate fold's verification, N2)."""
    baseline: dict[bool, set[str]] = {}

    def base_problems(outputs):
        if outputs not in baseline:
            docs = copy.deepcopy(base)
            docs["outputs"] = outputs
            baseline[outputs] = set(run_docs(docs))
        return baseline[outputs]

    out = []
    for name, docs in cases:
        before = base_problems(docs["outputs"])
        new = set(run_docs(docs)) - before
        # A case that names what must catch it is caught only by a problem that
        # says so (the fold review's W1: another check firing on the same
        # mutation would hide a blind one).
        if docs.get("names"):
            new = {p for p in new if any(n in p for n in docs["names"])}
        if new:
            continue
        # A survivor against a record that already fails may be the live
        # failure hiding the mutation, not a blind check; say which.
        already = (f"; the unmutated record already fails with {len(before)} problem(s), "
                   "and the mutation added none" if before else "")
        out.append(f"falsification survived: {name} -- the gate accepted a replay result "
                   f"it should have refused{already}")
    return out


def make_case(base: dict, name: str, part: str, f=None, value=None,
              outputs: bool = False, names=None) -> tuple[str, dict]:
    docs = copy.deepcopy(base)
    docs["outputs"] = outputs
    docs["names"] = names
    if f is not None:
        f(docs[part])
    else:
        docs[part] = value
    return name, docs


def copied_run(rel_dir: str, scratch: list[Path], change) -> Path:
    """A run's directory copied to a scratch directory, `change` applied to
    the copy (it gets the copy's path), and the copy's path returned. The
    records a case points at the copy name it absolutely, which the tools read
    as it stands (ROOT / an absolute path is that path)."""
    src = ROOT / rel_dir
    dst = Path(tempfile.mkdtemp(prefix="replay-stage-")) / src.name
    scratch.append(dst.parent)
    shutil.copytree(src, dst)
    change(dst)
    return dst


def stage_cases(base: dict, scratch: list[Path]) -> tuple[list[tuple[str, dict]], list[str]]:
    """The falsifications aimed at check 6 and at M4's per-entry lifted log,
    for each registered stage entry (the stage-entry spec's section 7), and the
    ones its data cannot build, by name. Each is aimed so that only the check
    it names can catch it; the synthetic stage replay in
    Documentation/scripts/tests/test_replay_results_stage.py runs every one
    while no stage replay is registered."""
    import tick_replay as T

    cases: list[tuple[str, dict]] = []
    inapplicable: list[str] = []
    for i, entry in enumerate(base["registry"].get("replays", [])):
        if not T.is_stage(entry):
            continue
        name = entry["name"]
        j = next((k for k, d in enumerate(base["receipt"].get("replays", []))
                  if d.get("name") == name), None)
        if j is None:
            inapplicable.append(f"{name}: every case (no recorded result)")
            continue
        lifted = base["receipt"]["replays"][j]["lifted"]
        rows = len(T.read_capture(ROOT / entry["native"]["capture"])[1])

        def doc(d, i=j):
            return d["replays"][i]

        def ent(d, i=i):
            return d["replays"][i]

        def moved_log(change):
            """The observed run's directory copied with its applied-input log
            changed and repinned, every lifted file pointed at the copy, so
            only the input check can see it."""
            def edit(d):
                cap_dir = T.run_dir(lifted["capture"])
                applied = Path(lifted["input_applied"]["path"]).name
                dst = copied_run(cap_dir, scratch,
                                 lambda c: (c / applied).write_text(change((c / applied)
                                                                           .read_text())))
                L = doc(d)["lifted"]
                for key in ("goal_latch", "stage_trace", "input_applied", "import_table"):
                    L[key]["path"] = str(dst / Path(L[key]["path"]).name)
                L["capture"] = str(dst / Path(L["capture"]).name)
                L["baseline"]["import_table"]["path"] = str(
                    dst / "baseline" / Path(L["baseline"]["import_table"]["path"]).name)
                L["input_applied"]["sha256"] = T.sha256(Path(L["input_applied"]["path"]))
            return edit

        def flip_first_match(text):
            lines = text.splitlines(keepends=True)
            for k, line in enumerate(lines[1:], 1):
                parts = line.split(",")
                if parts[2] == "1":
                    parts[2] = "0"
                    lines[k] = ",".join(parts)
                    break
            return "".join(lines)

        def drop_first_match(text):
            lines = text.splitlines(keepends=True)
            k = next(k for k, line in enumerate(lines[1:], 1) if line.split(",")[2] == "1")
            return "".join(lines[:k] + lines[k + 1:])

        def other_goal(d):
            rep = ent(d)["native"]["repeats"][0]
            end_name = Path(rep["end"]["file"]).name

            def bump(c):
                g, e_, m = (c / end_name).read_text().splitlines()[1].split(",")
                (c / end_name).write_text(f"goal_tick,end_tick,misses\n{int(g) + 1},{e_},{m}\n")
            dst = copied_run(T.run_dir(rep["capture"]), scratch, bump)
            repoint(rep, dst)
            rep["end"]["sha256"] = T.sha256(dst / end_name)

        def repoint(rep, dst):
            """A repeat's records pointed at its run's copy in `dst`."""
            for key in ("capture", "receipt"):
                rep[key] = str(dst / Path(rep[key]).name)
            for key in ("end", "stage_trace", "input_applied"):
                rep[key]["file"] = str(dst / Path(rep[key]["file"]).name)

        def other_recording(d):
            """The repeat's run copied with its receipt naming another
            recording's input header, every pin its own: only the check that the
            native side replayed the registered recording can see it (the M6
            plumbing review, W2)."""
            rep = ent(d)["native"]["repeats"][0]

            def elsewhere(c):
                r = json.loads((c / "receipt.json").read_text())
                for b in r["inputs"]["built_probes"]:
                    if b.get("mod_id") == "input_probe":
                        b["defines"] = [x if not x.startswith("INPUT_SCRIPT=") else
                                        'INPUT_SCRIPT="/x/input-scripts/another-recording.h"'
                                        for x in b["defines"]]
                (c / "receipt.json").write_text(json.dumps(r))
            repoint(rep, copied_run(T.run_dir(rep["capture"]), scratch, elsewhere))

        def other_rows(d):
            """The repeat's applied input changed to another recording's values
            and re-pinned in the registry and in its run's receipt: only the check
            that the log applied the registered input's rows can see it (the
            re-take review, W6)."""
            rep = ent(d)["native"]["repeats"][0]
            name_ = Path(rep["input_applied"]["file"]).name

            def rows(c):
                lines = (c / name_).read_text().splitlines(keepends=True)
                k = next(k for k, line in enumerate(lines[1:], 1) if line.split(",")[2] == "1")
                parts = lines[k].split(",")
                parts[3] = hex(int(parts[3], 16) + 1)
                lines[k] = ",".join(parts)
                (c / name_).write_text("".join(lines))
                r = json.loads((c / "receipt.json").read_text())
                for a in r["artifacts"]:
                    if a.get("file") == name_:
                        a["check"]["sha256"] = T.sha256(c / name_)
                (c / "receipt.json").write_text(json.dumps(r))
            dst = copied_run(T.run_dir(rep["capture"]), scratch, rows)
            repoint(rep, dst)
            rep["input_applied"]["sha256"] = T.sha256(dst / name_)

        def both_elsewhere(d):
            """Both runs' recorded directory edited to the runtime's placeholders while
            their run records say the capture's (the working-directory review, W1)."""
            there = {"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"}
            doc(d)["lifted"]["guest_paths"] = dict(there)
            doc(d)["lifted"]["baseline"]["guest_paths"] = dict(there)

        boot_like = next((e for e in base["registry"].get("replays", [])
                          if not T.is_stage(e)), None)
        if "word_policy" in entry:
            # stage_mask_problems on the live registry (the U6 registration review,
            # W5), each caught by the check that names it. Every registered stage
            # entry has a word policy, which that check requires; the synthetic
            # stage replay of test_replay_results_stage.py has none, and answers it.
            cases += [
                make_case(base, f"{name}: its word_policy dropped", "registry",
                          lambda d: ent(d).pop("word_policy"), outputs=True,
                          names=("has no word_policy",)),
                make_case(base, f"{name}: its word_policy emptied", "registry",
                          lambda d: ent(d).__setitem__("word_policy", {}), outputs=True,
                          names=("its word_policy must name the identity record",)),
            ] + ([
                make_case(base, f"{name}: a boot capture appended as a repeat", "registry",
                          lambda d: ent(d)["native"]["repeats"].append(
                              copy.deepcopy(boot_like["native"]["repeats"][0])),
                          outputs=True, names=("not configured like",)),
            ] if boot_like and boot_like["native"].get("repeats") else [])
        cases += [
            make_case(base, f"{name}: both runs' recorded guest directory edited", "receipt",
                      both_elsewhere, outputs=True),
            make_case(base, f"{name}: a stage capture longer than its own budget", "receipt",
                      lambda d: doc(d)["lifted"].__setitem__("polls", rows), outputs=True),
            make_case(base, f"{name}: the baseline's import calls moved by one", "receipt",
                      lambda d: doc(d)["lifted"]["baseline"]["reach"].__setitem__(
                          "import_calls", doc(d)["lifted"]["baseline"]["reach"]["import_calls"]
                          + 1), outputs=True),
            make_case(base, f"{name}: the baseline's import table re-pinned", "receipt",
                      lambda d: doc(d)["lifted"]["baseline"]["import_table"].__setitem__(
                          "sha256", "0" * 64), outputs=True),
            make_case(base, f"{name}: its recorded trace comparison edited", "receipt",
                      lambda d: doc(d)["result"]["trace"].__setitem__(
                          "compared_ticks", doc(d)["result"]["trace"]["compared_ticks"] - 1),
                      outputs=True),
            make_case(base, f"{name}: the lifted stage trace moved and nothing re-pinned",
                      "receipt", lambda d: doc(d)["lifted"]["stage_trace"].__setitem__(
                          "sha256", "0" * 64), outputs=True),
            make_case(base, f"{name}: the goal-latch column moved and nothing re-pinned",
                      "receipt", lambda d: doc(d)["lifted"]["goal_latch"].__setitem__(
                          "sha256", "0" * 64), outputs=True),
            make_case(base, f"{name}: an applied input row flipped to a miss", "receipt",
                      moved_log(flip_first_match), outputs=True),
            make_case(base, f"{name}: a recorded input row dropped from the applied log",
                      "receipt", moved_log(drop_first_match), outputs=True),
            make_case(base, f"{name}: a repeat's end record re-pinned", "registry",
                      lambda d: ent(d)["native"]["repeats"][0]["end"].__setitem__(
                          "sha256", "0" * 64), outputs=True),
            make_case(base, f"{name}: two end records naming different goal ticks", "registry",
                      other_goal, outputs=True),
            make_case(base, f"{name}: the registered capture listed again as a repeat",
                      "registry", lambda d: ent(d)["native"]["repeats"].__setitem__(0, {
                          k: copy.deepcopy(ent(d)["native"][k])
                          for k in ("capture", "sha256", "receipt", "end", "stage_trace",
                                    "input_applied")}),
                      outputs=True),
            make_case(base, f"{name}: a repeat that replayed another recording", "registry",
                      other_recording, outputs=True),
            make_case(base, f"{name}: a repeat's applied input from another recording, "
                            "re-pinned", "registry", other_rows, outputs=True),
            make_case(base, f"{name}: M4's lifted log from its table re-pinned", "receipt",
                      lambda d: doc(d)["lifted"]["import_table"].__setitem__("sha256", "0" * 64)),
        ]
        # An import only this entry's lifted run called: dropped from `reached`,
        # only a gate that reads the entry's own table can miss it.
        rr = base["status"].get("replay_reach") or {}
        others = set(rr.get("native_pre_init") or [])
        for v in (rr.get("native") or {}).values():
            others |= set(v)
        for k, v in (rr.get("lifted_by_replay") or {}).items():
            if k != name:
                others |= set(v)
        only = sorted(set((rr.get("lifted_by_replay") or {}).get(name) or []) - others)
        if only:
            cases.append(make_case(base, f"{name}: M4's reached set dropped an import only its "
                                         "lifted run called", "status",
                                   lambda d, x=only[0]: d["reached"].remove(x)))
        else:
            inapplicable.append(f"{name}: M4's reached set dropped an import only its lifted run "
                                "called (no import is called by its lifted run alone)")
    return cases, inapplicable


class Stopped(BaseException):
    """SIGTERM, raised as something no check catches. Not SystemExit: the checks
    catch SystemExit, the tools' refusal, and would record the stop as a problem
    and go on with the pass."""


def stopped(signum, frame):
    """SIGTERM as an exception, so a pass stopped with kill or timeout unwinds
    through the falsifications' `finally` (`scratch_dirs`) and ends."""
    raise Stopped(128 + signum)


def main() -> int:
    # Both, always: when the live record already fails, every falsification
    # can survive for that reason alone, and printing only the survivors hid
    # the problems that made them survive.
    before = signal.signal(signal.SIGTERM, stopped)
    try:
        with kept():
            failed = falsifications()
            probs = problems()
    except Stopped as e:
        return e.args[0]
    finally:
        signal.signal(signal.SIGTERM, before)
    if failed or probs:
        for f in failed:
            print(f"  {f}")
        for p in probs:
            if p not in failed:
                print(f"  {p}")
        print(f"REPLAY_RESULTS_FAILED falsifications={len(failed)} problems={len(probs)}")
        return 1
    m5 = json.loads(RECEIPT.read_text())["m5"]
    for x in STAGE_INAPPLICABLE:
        print(f"  not applicable: {x}")
    print(f"REPLAY_RESULTS_OK replays={len(m5)} stale=0 recomputed={len(m5)} "
          f"falsifications={RAN} stage_falsifications={STAGE_RAN} "
          f"stage_inapplicable={len(STAGE_INAPPLICABLE)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
