#!/usr/bin/env python3
"""Write one M8 target's receipt, deriving each clause's verdict from its evidence.

The receipt is `Documentation/receipts/s7-target-<target>-v1.json`, schema
`sa2-s7-target/v1`. What green means for a target is the tracked record
`Documentation/s7-green-means.md`: its first table lists the clauses each target needs,
and its second gives each target's runner, triple and target passes. This tool reads both
tables from the record, so a target's clauses are never typed here, and it records the
record's sha256 beside them.

For every clause the record lists for the target, the receipt holds the files the clause
read (`evidence`, each with its sha256), what was read and derived from them (`facts`), a
`verdict` derived from them on every run, and one line saying why (`why`). `green` is the
conjunction of those verdicts. No verdict is copied from a field another tool wrote: a
replay's reading is derived from the run's own tick-replay result with
`check_replay_regression.py`'s functions, a build's module counts from its per-module log,
and the reference's host clause is G5's `check_s5_exit.identical`, which recomputes the
frame equality from the tables. Where a clause takes an outcome its producer reports (a
stage replay's goal, a route installed and launched), the record says so.

`x86_64-linux` is the reference: its evidence is the tracked receipts the record names.
The other two targets read one tracked evidence file per clause, at the path the record
gives (`evidence_path`), in the schema it defines, and the reference's receipt, which they
cite by a digest of the facts they use, so a rewrite of it that changes nothing they read
does not stale them. A clause whose evidence is absent reads false and says so.

The receipt takes no arguments beyond the target, so anyone can recompute it from the tree:
`tools/s7/targets.py` and `check_s7_exit.py`'s `targets_input` do, and refuse a receipt that
differs from its recomputation in anything but `written` and `commit`.

Usage:
    .venv/bin/python tools/s7/target_receipt.py --target x86_64-linux
    --check     recompute and compare with the written receipt; write nothing
    --out PATH  write there instead of the tracked receipt (scratch runs)
    --root PATH the repository (default: this file's)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SELF = "tools/s7/target_receipt.py"
SCHEMA = "sa2-s7-target/v1"
GREEN_MEANS = "Documentation/s7-green-means.md"
RECEIPT = "Documentation/receipts/s7-target-{target}-v1.json"
TARGETS = ("x86_64-linux", "x86_64-windows", "arm64-linux")
REFERENCE = "x86_64-linux"
RUNNERS = ("native", "wine", "qemu")

REGISTRY = "Documentation/replays.json"
TICK_REPLAY = "Documentation/receipts/s4-tick-replay-v1.json"
STAGE_BOOT = "Documentation/receipts/s4-stage-boot-v1.json"
BASELINE = "Documentation/receipts/replay-baseline-v1.json"
RESIDUES = "Documentation/receipts/residues"
REACH = "Documentation/receipts/s4-run-reach-v1.json"
BUILD_TIME = "Documentation/receipts/s4-build-time-v2.json"
EXTRAS = "Documentation/lifted-extras.json"
PINS = "tools/pins.json"
UNPACKED = "sonic2app-unpacked.exe"
HOST_COMPARE = "Documentation/receipts/s5-host-compare-v1.json"
REGRESSION = "Documentation/scripts/check_replay_regression.py"
S5_EXIT = "Documentation/scripts/check_s5_exit.py"
STAGE = "metalharbor"
POPULATIONS = ("map", "arms", "extras")
ROUTES = ("steam-autodiscovery", "folder", "zip")
# The reference link's modules: the staged whole-image link directory (the map and arm
# populations together) and the extras the link lists.
STAGED = "private/generated/s3/whole_image/build/next+allarms"
EXTRAS_LINKED = "private/generated/s4/link/extra_functions.next+allarms.json"
STAGED_OBJECT = re.compile(r"^ir_([0-9A-F]{8})\.o$")
# The reference's folded IR for one module, by the population name a build's log uses, and
# the population it counts toward.
FOLDED_IR = {"map": "private/generated/s3/whole_image/fold/next/sub_{va:08X}.pc24.ll",
             "arms": "private/generated/s3/arms/fold/next/sub_{va:08X}.pc24.ll",
             "extra": "private/generated/s4/lift/fold/sub_{va:08X}.pc24.ll"}
LOG_POPULATION = {"map": "map", "arms": "arms", "extra": "extras"}

# Where a target other than the reference keeps each clause's evidence, and its schema. The
# arm64 replays are the labels receipt check_s7_exit.py's arm64 predicate reads.
EVIDENCE = "Documentation/receipts/s7-{target}-{clause}-v1.json"
EVIDENCE_SCHEMAS = {"build": "sa2-s7-build-census/v1", "replays": "sa2-s7-target-replays/v1",
                    "host": "sa2-s7-target-host/v1"}
ARM64_REPLAYS = ("Documentation/receipts/s7-arm64-replays-v1.json", "sa2-s7-arm64-replays/v1")
# The user's native Windows run is the installer receipt G7's installer predicate reads: the
# user's records go to tools/s7/installer_receipt.py (Documentation/s7-windows-native-run.md,
# step 5), which keeps each Windows route's last run with its runner, so the run is one file.
NATIVE_RUN = ("Documentation/receipts/s7-installer-v1.json", "sa2-s7-installer/v1")
INSTALLER_TOOL = "tools/s7/installer_receipt.py"
NATIVE_PENDING = "pending: the user's native Windows run"
PLAYER = "player_sha256"
# What the receipt's pipeline pins: this tool, the record, and the two checkers it calls.
PIPELINE = (SELF, GREEN_MEANS, REGRESSION, S5_EXIT)
# The fields a recomputation does not compare: when, and at which commit, it was written.
UNCOMPARED = ("written", "commit")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SEPARATOR = re.compile(r"^\|(\s*:?-+:?\s*\|)+$")


class Fail(Exception):
    """A clause's evidence is absent or does not hold: its verdict is false, saying why."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def digest(value: object) -> str:
    """The sha256 of a value's canonical JSON form."""
    return sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def evidence_path(target: str, clause: str) -> tuple[str, str]:
    """(path, schema) of a non-reference target's evidence for one clause."""
    if (target, clause) == ("arm64-linux", "replays"):
        return ARM64_REPLAYS
    if clause == "native_run":
        return NATIVE_RUN
    return EVIDENCE.format(target=target, clause=clause), EVIDENCE_SCHEMAS[clause]


# ------------------------------------------------------------------ the record


def tables(text: str) -> list[tuple[list[str], list[dict[str, str]]]]:
    """Every Markdown table in `text`: its header cells and its rows keyed by them."""
    found: list[tuple[list[str], list[dict[str, str]]]] = []
    lines = text.splitlines()
    rows: list[dict[str, str]] | None = None
    header: list[str] = []
    for index, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("|"):
            rows = None
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if rows is None:
            following = lines[index + 1].strip() if index + 1 < len(lines) else ""
            if SEPARATOR.match(following):
                header, rows = cells, []
                found.append((header, rows))
            continue
        if SEPARATOR.match(line):
            continue
        rows.append(dict(zip(header, cells)))
    return found


def code(cell: str, what: str) -> str:
    """A cell holding one `code` span, its content."""
    match = re.fullmatch(r"`([^`]+)`", cell or "")
    if not match:
        raise ValueError(f"{GREEN_MEANS}: {what} {cell!r} is not one `code` span")
    return match.group(1)


def codes(cell: str, what: str) -> list[str]:
    """A cell holding `none` or code spans separated by commas, their contents."""
    if (cell or "").strip() == "none":
        return []
    return [code(part.strip(), what) for part in (cell or "").split(",")]


@dataclass
class Means:
    """What the record says green is, per target."""
    sha256: str
    clauses: dict[str, list[str]]
    runner: dict[str, str]
    triple: dict[str, str]
    passes: dict[str, list[str]]


def green_means(root: Path) -> Means:
    """The record's two tables, held to their form: every target with its clauses, a runner,
    a triple and its target passes; every clause one this tool has a rule for; nothing listed
    twice."""
    path = root / GREEN_MEANS
    data = path.read_bytes()
    clauses: dict[str, list[str]] = {}
    runner: dict[str, str] = {}
    triple: dict[str, str] = {}
    passes: dict[str, list[str]] = {}
    for header, rows in tables(data.decode("utf-8")):
        if header[:2] == ["Target", "Clause"]:
            for row in rows:
                target = code(row.get("Target", ""), "target")
                clause = code(row.get("Clause", ""), "clause")
                clauses.setdefault(target, [])
                if clause in clauses[target]:
                    raise ValueError(f"{GREEN_MEANS}: {target} lists {clause} twice")
                clauses[target].append(clause)
        elif header[:4] == ["Target", "Runner", "Triple", "Target passes"]:
            for row in rows:
                target = code(row.get("Target", ""), "target")
                if target in runner:
                    raise ValueError(f"{GREEN_MEANS}: {target} has two runners")
                runner[target] = code(row.get("Runner", ""), "runner")
                triple[target] = code(row.get("Triple", ""), "triple")
                passes[target] = codes(row.get("Target passes", ""), "target pass")
    for target in TARGETS:
        if not clauses.get(target):
            raise ValueError(f"{GREEN_MEANS} lists no clause for {target}")
        if runner.get(target) not in RUNNERS or not triple.get(target):
            raise ValueError(f"{GREEN_MEANS} gives {target} no runner of {RUNNERS} and triple")
        rules = REFERENCE_RULES if target == REFERENCE else TARGET_RULES
        unknown = [c for c in clauses[target] if c not in rules]
        if unknown:
            raise ValueError(f"{GREEN_MEANS}: {target}'s clauses {unknown} have no rule here")
    if passes.get(REFERENCE):
        raise ValueError(f"{GREEN_MEANS}: the reference {REFERENCE} has target passes")
    extra = sorted((set(clauses) | set(runner)) - set(TARGETS))
    if extra:
        raise ValueError(f"{GREEN_MEANS} lists targets that are not M8's: {extra}")
    return Means(sha256_bytes(data), clauses, runner, triple, passes)


# ------------------------------------------------------------------ reading evidence


@dataclass
class Reader:
    """Reads evidence under `root` and keeps the sha256 of every file read, in order, and
    the digest of every reference fact read."""
    root: Path
    read: dict[str, str] = field(default_factory=dict)
    reference: dict[str, str] = field(default_factory=dict)

    def bytes(self, rel: str, what: str, record: bool = True) -> bytes:
        if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in rel.split("/"):
            raise Fail(f"{what}: {rel!r} is not a repository path")
        path = self.root / rel
        if not path.is_file():
            raise Fail(f"{rel} is absent ({what})")
        data = path.read_bytes()
        if record:
            self.read[rel] = sha256_bytes(data)
        return data

    def json(self, rel: str, what: str, schema: str | None = None, record: bool = True) -> dict:
        try:
            doc = json.loads(self.bytes(rel, what, record))
        except ValueError as error:
            raise Fail(f"{rel} is not JSON: {error}") from None
        if not isinstance(doc, dict):
            raise Fail(f"{rel} is not a JSON object")
        if schema is not None and doc.get("schema") != schema:
            raise Fail(f"{rel} has schema {doc.get('schema')!r}, not {schema!r}")
        return doc

    def note(self, rel: str, what: str) -> None:
        """A file a called checker reads: hashed into the evidence, not parsed here."""
        self.bytes(rel, what)

    def cited(self, ref: object, what: str) -> bytes:
        """A file an evidence file cites as {path, sha256}: read, and held to that hash."""
        if not isinstance(ref, dict) or not isinstance(ref.get("path"), str):
            raise Fail(f"{what} names no file")
        data = self.bytes(ref["path"], what)
        if sha256_bytes(data) != ref.get("sha256"):
            raise Fail(f"{ref['path']} is not the file {what} hashed")
        return data

    def target_evidence(self, target: str, clause: str) -> tuple[str, dict]:
        """A non-reference target's evidence for `clause`, at the record's path: its schema,
        its writer (a file in the tree) and its target."""
        rel, schema = evidence_path(target, clause)
        if not (self.root / rel).is_file():
            raise Fail(f"no {clause} evidence for {target}: {rel} is absent")
        doc = self.json(rel, f"{target}'s {clause} evidence", schema)
        writer = doc.get("generated_by")
        if not isinstance(writer, str) or not writer or ".." in writer.split("/") \
                or not (self.root / writer).is_file():
            raise Fail(f"{rel} names no writer in the tree ({writer!r})")
        if doc.get("target") != target:
            raise Fail(f"{rel} is {doc.get('target')!r}'s evidence, not {target}'s")
        return rel, doc

    def evidence(self) -> list[dict[str, str]]:
        return [{"path": rel, "sha256": sha} for rel, sha in self.read.items()]


def at(doc: object, dotted: str, what: str) -> object:
    for part in dotted.split("."):
        if not isinstance(doc, dict) or part not in doc:
            raise Fail(f"{what} has no {dotted}")
        doc = doc[part]
    return doc


def scripts_importable(root: Path) -> None:
    for rel in ("Documentation/scripts", "tools/s4"):
        path = str(root / rel)
        if path not in sys.path:
            sys.path.insert(0, path)


def registry(reader: Reader) -> tuple[dict, dict[str, dict]]:
    """The registry and its replays by name, Documentation/replays.json's order."""
    doc = reader.json(REGISTRY, "the replay registry")
    entries = {r.get("name"): r for r in doc.get("replays") or [] if isinstance(r, dict)}
    if not entries or any(not isinstance(n, str) or not n for n in entries):
        raise Fail(f"{REGISTRY} registers no named replay")
    return doc, entries


def registered(reader: Reader) -> list[str]:
    return list(registry(reader)[1])


def residue_records(reader: Reader, residues: object, what: str) -> list[str]:
    """Each residue id named a record under RESIDUES whose id is that id."""
    if not isinstance(residues, list):
        raise Fail(f"{what}: residues is not a list")
    for rid in residues:
        if not isinstance(rid, str) or not rid:
            raise Fail(f"{what}: a residue named {rid!r} is no record id")
        doc = reader.json(f"{RESIDUES}/{rid}.json", f"{what}'s residue {rid}")
        if doc.get("id") != rid or not isinstance(doc.get("class"), str):
            raise Fail(f"{RESIDUES}/{rid}.json is not the record of {rid}")
    return list(residues)


def reference_clause(reader: Reader, clause: str, fact: str) -> object:
    """A fact of the reference receipt's clause, which must itself read true. The receipt is
    cited by the digest of that fact, not by its file's hash."""
    rel = RECEIPT.format(target=REFERENCE)
    doc = reader.json(rel, "the reference target's receipt", SCHEMA, record=False)
    if doc.get("target") != REFERENCE or doc.get("generated_by") != SELF:
        raise Fail(f"{rel} is not {REFERENCE}'s receipt written by {SELF}")
    entry = (doc.get("clauses") or {}).get(clause)
    if not isinstance(entry, dict) or entry.get("verdict") is not True:
        raise Fail(f"{rel}: the reference's {clause} clause does not read true")
    value = at(entry, f"facts.{fact}", f"{rel}'s {clause} clause")
    reader.reference[f"{clause}.{fact}"] = digest(value)
    return value


def binaries(reader: Reader, target: str) -> dict:
    """The binaries the target's build census names, by role."""
    rel, census = reader.target_evidence(target, "build")
    found = at(census, "link.binaries", rel)
    if not isinstance(found, dict):
        raise Fail(f"{rel}: link.binaries is not an object")
    return found


def ran(found: dict, role: str, sha: object, what: str) -> None:
    if not isinstance(sha, str) or not HEX64.match(sha) or found.get(role) != sha:
        raise Fail(f"{what} ran binary {sha!r}, not the {role} binary the build census names")


# ------------------------------------------------------------------ build


def lifted_inputs(manifest: dict) -> dict:
    """The parts of a build that do not depend on the target, from a build manifest."""
    generated = at(manifest, "generated", REACH)
    fold = at(generated, "fold_code", REACH)
    if not isinstance(fold, dict):
        raise Fail(f"{REACH}: fold_code is not an object")
    return {"tag": at(manifest, "tag", REACH), "exe_sha256": at(manifest, "exe_sha256", REACH),
            "fold_code": {k: v for k, v in sorted(fold.items())
                          if isinstance(v, str) and HEX64.match(v)},
            "lifted_extras_sha256": at(generated, "lifted_extras_sha256", REACH),
            "mb_edges_c_sha256": at(generated, "mb_edges_c_sha256", REACH)}


def names(vas: list[int]) -> dict:
    """A module set: its count and the digest of its sorted addresses."""
    return {"count": len(vas), "digest": digest(sorted(f"0x{va:08x}" for va in vas))}


def link_modules(reader: Reader) -> dict:
    """The reference link's modules: the staged directory's objects and the linked extras."""
    staged = reader.root / STAGED
    if not staged.is_dir():
        raise Fail(f"{STAGED} is absent (the reference link's objects)")
    whole = []
    for path in staged.iterdir():
        match = STAGED_OBJECT.match(path.name)
        if match:
            whole.append(int(match.group(1), 16))
    linked = reader.json(EXTRAS_LINKED, "the link's extras")
    objects = linked.get("objects")
    if not isinstance(objects, list):
        raise Fail(f"{EXTRAS_LINKED} lists no objects")
    extras = []
    for obj in objects:
        match = STAGED_OBJECT.match(Path(str(obj)).name)
        if not match:
            raise Fail(f"{EXTRAS_LINKED} lists {obj!r}, not an ir_<VA>.o object")
        extras.append(int(match.group(1), 16))
    return {"whole_image": names(whole), "extras": names(extras)}


def build_reference(reader: Reader, means: Means, target: str) -> dict:
    reach = reader.json(REACH, "the build manifest")
    built = reader.json(BUILD_TIME, "the build-time receipt")
    extras = reader.json(EXTRAS, "the lifted extras")
    tick = reader.json(TICK_REPLAY, "the replays' receipt")
    pins = reader.json(PINS, "the pins", record=False)
    manifest = at(reach, "manifest", REACH)
    inputs = lifted_inputs(manifest)
    triple = means.triple[target]
    facts: dict = {"triple": triple, "tag": inputs["tag"], "lifted_inputs": inputs,
                   "lifted_inputs_digest": digest(inputs)}
    pinned = ((pins.get("executables") or {}).get(UNPACKED) or {}).get("sha256")
    if not isinstance(pinned, str) or not HEX64.match(pinned):
        raise Fail(f"{PINS} pins no sha256 for {UNPACKED}")
    if inputs["exe_sha256"] != pinned:
        raise Fail(f"{REACH} built from executable {inputs['exe_sha256']}, not the pinned {UNPACKED}")
    facts["pinned_unpacked_sha256"] = pinned
    commands = [v for v in at(manifest, "generated.fold_code", REACH).values()
                if not (isinstance(v, str) and HEX64.match(v))]
    if not commands or any(not isinstance(c, str) or f"-target {triple} " not in c
                           for c in commands):
        raise Fail(f"{REACH}: not every compile command names -target {triple}")
    facts["compile_commands"] = len(commands)
    pipeline = at(built, "inputs.pipeline", BUILD_TIME)
    if not isinstance(pipeline, dict):
        raise Fail(f"{BUILD_TIME}: inputs.pipeline is not an object")
    shared = sorted(k for k in inputs["fold_code"] if k in pipeline)
    differ = [k for k in shared if pipeline[k] != inputs["fold_code"][k]]
    if EXTRAS in pipeline and pipeline[EXTRAS] != inputs["lifted_extras_sha256"]:
        differ.append(EXTRAS)
    if not shared or differ:
        raise Fail(f"{BUILD_TIME} rebuilt with other fold code than {REACH} records: "
                   f"{differ or 'no fold-code file in common'}")
    facts["fold_code_in_both"] = shared
    same = at(built, "objects_identical_to_the_build", BUILD_TIME)
    populations = {}
    for name in POPULATIONS:
        row = at(same, name, f"{BUILD_TIME} objects_identical_to_the_build")
        modules, compiled = row.get("shipped"), row.get("built")
        failed = row.get("failed") or []
        if (row.get("ok") is not True or row.get("differ_count") != 0 or row.get("differ")
                or not isinstance(modules, int) or modules < 1 or compiled != modules or failed
                or row.get("missing_from_build") or row.get("extra_in_build")):
            raise Fail(f"{BUILD_TIME}: the {name} population was not rebuilt whole and identical")
        populations[name] = {"modules": modules, "compiled": compiled, "failed": len(failed)}
    facts["populations"] = populations
    whole = populations["map"]["modules"] + populations["arms"]["modules"]
    linked = at(manifest, "generated.whole_image_module_count", REACH)
    if linked != whole:
        raise Fail(f"{REACH} links {linked} whole-image objects, not the map and arm populations")
    counts = at(extras, "counts", EXTRAS)
    if not (counts.get("rows") == counts.get("lifted") == counts.get("compiled")
            == populations["extras"]["modules"]):
        raise Fail(f"{EXTRAS}: not every registered extra lifted and compiled")
    modules = link_modules(reader)
    if modules["whole_image"]["count"] != whole \
            or modules["extras"]["count"] != populations["extras"]["modules"]:
        raise Fail(f"the link holds {modules['whole_image']['count']} whole-image objects and "
                   f"{modules['extras']['count']} extras, not the populations' {whole} and "
                   f"{populations['extras']['modules']}")
    facts["modules"] = modules
    if not isinstance(tick.get("commit"), str) or tick.get("commit") != reach.get("commit"):
        raise Fail(f"{TICK_REPLAY} (commit {tick.get('commit')}) did not run the build "
                   f"{REACH} describes (commit {reach.get('commit')})")
    facts["commit"] = reach["commit"]
    facts["build_time_commit"] = built.get("commit")
    facts["binary_sha256"] = {d.get("name"): at(d, "lifted.binary_sha256", TICK_REPLAY)
                              for d in tick.get("replays") or []}
    facts["why"] = (f"{triple}: map {populations['map']['modules']}, arms "
                    f"{populations['arms']['modules']}, extras {populations['extras']['modules']} "
                    f"compiled, none failed, linked at {reach['commit'][:12]}")
    return facts


def build_census(reader: Reader, means: Means, target: str) -> dict:
    rel, census = reader.target_evidence(target, "build")
    triple = means.triple[target]
    if census.get("triple") != triple:
        raise Fail(f"{rel} is a build for {census.get('triple')!r}, not {triple}")
    ref = reference_clause(reader, "build", "lifted_inputs")
    inputs = census.get("lifted_inputs")
    if not isinstance(inputs, dict) or inputs != ref:
        keys = sorted(k for k in set(ref) | set(inputs if isinstance(inputs, dict) else {})
                      if not isinstance(inputs, dict) or inputs.get(k) != ref.get(k))
        raise Fail(f"{rel}: the lifted inputs are not {REFERENCE}'s ({', '.join(keys)})")
    passes = census.get("target_passes")
    want = means.passes[target]
    if not isinstance(passes, dict) or sorted(passes) != sorted(want):
        raise Fail(f"{rel} names target passes {sorted(passes or {})}, not the record's {want}")
    for path in want:
        if sha256_bytes(reader.bytes(path, f"the target pass {path}")) != passes[path]:
            raise Fail(f"{rel}: the target pass {path} is not the tree's")
    ref_populations = reference_clause(reader, "build", "populations")
    ref_modules = reference_clause(reader, "build", "modules")
    log = reader.cited(census.get("log"), f"{rel}'s log")
    vas: dict[str, list[int]] = {name: [] for name in POPULATIONS}
    failed: dict[str, int] = {name: 0 for name in POPULATIONS}
    shas = []
    for number, line in enumerate(log.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            population = LOG_POPULATION[rec["population"]]
            va = int(rec["va"], 16)
        except (ValueError, KeyError, TypeError):
            raise Fail(f"{rel}'s log, line {number}: not a module record") from None
        if rec.get("ir") != FOLDED_IR[rec["population"]].format(va=va):
            raise Fail(f"{rel}'s log, line {number}: {rec.get('ir')!r} is not the reference's "
                       "folded IR for that module")
        vas[population].append(va)
        failed[population] += rec.get("compiled") is not True
        shas.append((rec["population"], f"0x{va:08x}", rec.get("ir_sha256")))
    populations = {}
    for name in POPULATIONS:
        modules = at(ref_populations, f"{name}.modules", f"{REFERENCE}'s populations")
        got = len(vas[name])
        if got != modules or failed[name]:
            raise Fail(f"{rel}: {name} compiled {got - failed[name]} of {got} with "
                       f"{failed[name]} failed; {REFERENCE} has {modules}")
        populations[name] = {"modules": got, "compiled": got - failed[name], "failed": 0}
    if names(vas["map"] + vas["arms"]) != ref_modules.get("whole_image") \
            or names(vas["extras"]) != ref_modules.get("extras"):
        raise Fail(f"{rel}: the modules compiled are not the {REFERENCE} link's")
    link = at(census, "link", rel)
    roles = registered(reader) + (["host"] if {"host", "native_run"} & set(means.clauses[target])
                                  else [])
    found = link.get("binaries") if isinstance(link, dict) else None
    if not isinstance(link, dict) or link.get("returncode") != 0 or not isinstance(found, dict) \
            or any(not HEX64.match(str(found.get(role))) for role in roles):
        raise Fail(f"{rel}: the link did not return zero with a binary for each of {roles}")
    return {"triple": triple, "lifted_inputs_digest": digest(inputs), "target_passes": passes,
            "populations": populations, "ir_digest": digest(sorted(shas)),
            "binaries": {role: found[role] for role in roles},
            "why": f"{triple}: the reference's lifted inputs and modules, every population "
                   "compiled, linked"}


# ------------------------------------------------------------------ replays


def regression_gate(root: Path) -> dict:
    """check_replay_regression.py's own result: its exit status and its output's sha256."""
    done = subprocess.run([sys.executable, str(root / REGRESSION), "--root", str(root)],
                          capture_output=True, text=True, cwd=root)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    return {"command": f".venv/bin/python {REGRESSION}", "returncode": done.returncode,
            "stdout_sha256": sha256_bytes(done.stdout.encode()),
            "said": next((line for line in lines if line.startswith("replay regression ")),
                         (done.stderr.strip().splitlines() or [""])[-1])}


def runner_here() -> dict:
    return {"kind": "native", "system": platform.system(), "machine": platform.machine(),
            "by": "tools/s4/tick_replay.py"}


def replays_reference(reader: Reader, means: Means, target: str) -> dict:
    scripts_importable(reader.root)
    import check_replay_regression as R  # noqa: PLC0415
    names_ = registered(reader)
    tick = reader.json(TICK_REPLAY, "the replays' receipt")
    baseline_text = reader.bytes(BASELINE, "the replay baseline").decode()
    reader.note(STAGE_BOOT, "the stage replays' goal verdicts")
    runner = runner_here()
    if (runner["kind"] != means.runner[target] or runner["system"] != "Linux"
            or runner["machine"] != "x86_64"):
        raise Fail(f"this run is {runner['system']} {runner['machine']}, not {target} natively")
    try:
        readings, stale = R.current_readings(reader.root)
        baseline = R.load_baseline(baseline_text)
    except (R.Unrunnable, ValueError, KeyError) as error:
        raise Fail(f"the regression gate's readings cannot be taken: {error}") from None
    if stale:
        raise Fail(f"{TICK_REPLAY} is not the tree's: {stale[0]}")
    worse, residues = [], {}
    records = {r.get("name"): r for r in tick.get("m5") or []}
    for name in names_:
        now, base = readings.get(name), baseline.get(name)
        if now is None or base is None or name not in records:
            raise Fail(f"{name}: not run, or not in {BASELINE}")
        worse += [f"{name}: {w}" for w in R.worse(base, now)]
        residues[name] = residue_records(reader, records[name].get("residues"), name)
    if worse:
        raise Fail(f"worse than the baseline: {'; '.join(worse)}")
    gate = regression_gate(reader.root)
    facts = {"runner": runner, "registry_sha256": reader.read[REGISTRY],
             "readings": {name: readings[name] for name in names_}, "residues": residues,
             "regression_gate": gate}
    if gate["returncode"] != 0:
        raise Fail(f"{REGRESSION} exits {gate['returncode']}: {gate['said']}")
    facts["why"] = "; ".join(readings[n]["m5"] for n in names_) + ", none worse than the baseline"
    return facts


def reading(R, T, root: Path, name: str, entry: dict, run: dict,
            own: list[str]) -> tuple[dict, dict]:
    """(One replay's reading, its M5 record), derived from its run's tick-replay result as
    check_replay_regression.current_readings derives the reference's."""
    result = run.get("tick_result")
    if not isinstance(result, dict):
        raise Fail(f"{name}: the run records no tick_result")
    rec = T.m5_record({"name": name, "result": result})
    part = R.render(rec)
    goal = None
    if T.is_stage(entry):
        goal = run.get("goal_complete")
        if not isinstance(goal, bool):
            raise Fail(f"{name}: a stage replay's run records no goal_complete verdict")
    return {"m5": part, "ticks": rec.get("ticks"),
            "first_divergent_tick": rec.get("first_divergent_tick"),
            "field": rec.get("field"), "class": rec.get("class"),
            "judged_ticks": result.get("judged_ticks"),
            "trace_judged_ticks": (result.get("trace") or {}).get("judged_ticks"),
            "compared_words": (result.get("mask") or {}).get("words"),
            "m5_met": R.part_meets_m5(part, root / RESIDUES) and not own,
            "goal_complete": goal}, rec


def replays_target(reader: Reader, means: Means, target: str) -> dict:
    scripts_importable(reader.root)
    import check_replay_regression as R  # noqa: PLC0415
    import tick_replay as T  # noqa: PLC0415
    rel, doc = reader.target_evidence(target, "replays")
    registry_doc, entries = registry(reader)
    runner = doc.get("runner") if isinstance(doc.get("runner"), dict) else {}
    if runner.get("kind") != means.runner[target]:
        raise Fail(f"{rel} is not {target}'s runs under {means.runner[target]}")
    if means.runner[target] == "qemu" and runner.get("headless") is not True:
        raise Fail(f"{rel}: the qemu runs are not recorded headless")
    if doc.get("registry_sha256") != reader.read[REGISTRY]:
        raise Fail(f"{rel} was run under another {REGISTRY}")
    runs = doc.get("replays") if isinstance(doc.get("replays"), dict) else {}
    missing = [name for name in entries if not isinstance(runs.get(name), dict)]
    if missing:
        raise Fail(f"{', '.join(missing)}: no run on {target} in {rel}")
    built = binaries(reader, target)
    ref = reference_clause(reader, "replays", "readings")
    try:
        ident = T.identity_problems(
            {"replays": [{"name": n, "result": runs[n].get("tick_result")} for n in entries]},
            registry_doc, reader.root)
    except SystemExit as error:
        ident = [f"the identity rule cannot be checked: {error}"]
    loose = [p for p in ident if not any(p.startswith(f"{n}: ") for n in entries)]
    worse, residues, readings = [], {}, {}
    for name, entry in entries.items():
        run = runs[name]
        ran(built, name, run.get("binary_sha256"), f"{name} on {target}")
        own = [p for p in ident if p.startswith(f"{name}: ")] + loose
        now, rec = reading(R, T, reader.root, name, entry, run, own)
        base = ref.get(name) if isinstance(ref, dict) else None
        if not isinstance(base, dict):
            raise Fail(f"{name}: no {REFERENCE} reading")
        worse += [f"{name}: {w}" for w in R.worse(base, now)]
        residues[name] = residue_records(reader, rec.get("residues", []), f"{name} on {target}")
        if target == "arm64-linux":
            labelled(rel, name, run, residues[name], rec)
        readings[name] = now
    if worse:
        raise Fail(f"worse than {REFERENCE}: {'; '.join(worse)}")
    return {"runner": runner, "registry_sha256": reader.read[REGISTRY], "residues": residues,
            "readings": readings,
            "why": f"{len(entries)} registered replays under {means.runner[target]}, none worse "
                   f"than {REFERENCE}"}


def labelled(rel: str, name: str, run: dict, residues: list[str], rec: dict) -> None:
    """The arm64 labels check_s7_exit.py's arm64 predicate reads agree with the run: the
    labelled residues are the derived ones, and 'no divergence' exactly when the derived
    reading has neither a residue nor a divergence."""
    if sorted(run.get("residues") or []) != sorted(residues):
        raise Fail(f"{rel}: {name} is labelled with residues {run.get('residues')!r}, and its "
                   f"run reads {residues}")
    clean = not residues and rec.get("first_divergent_tick") is None
    if (run.get("result") == "no divergence") is not clean:
        raise Fail(f"{rel}: {name} is labelled {run.get('result')!r}, and its run reads "
                   f"{'no divergence' if clean else 'a divergence or residues'}")


# ------------------------------------------------------------------ host


def refs(doc: object) -> list[str]:
    """Every {"path": ...} reference inside a receipt, in order."""
    found: list[str] = []
    if isinstance(doc, dict):
        if isinstance(doc.get("path"), str) and isinstance(doc.get("sha256"), str):
            found.append(doc["path"])
        for value in doc.values():
            found += refs(value)
    elif isinstance(doc, list):
        for value in doc:
            found += refs(value)
    return found


def host_reference(reader: Reader, means: Means, target: str) -> dict:
    scripts_importable(reader.root)
    import check_s5_exit as S5  # noqa: PLC0415
    names_ = registered(reader)
    doc = reader.json(HOST_COMPARE, "the host comparison")
    if doc.get("replay") != STAGE or STAGE not in names_:
        raise Fail(f"{HOST_COMPARE} compares {doc.get('replay')!r}, not the registered {STAGE}")
    for rel in refs({k: v for k, v in doc.items() if k != "inputs"}):
        if not rel.startswith("/"):
            reader.note(rel, "the host comparison's evidence")
    reader.note(S5.SELECTION, "the host selection")
    result = S5.identical(reader.root)
    if not result.met:
        raise Fail(f"check_s5_exit.identical: {result.detail}")
    frames = {h.get("host"): h.get("frames") for h in doc.get("hosts") or []}
    return {"replay": STAGE, "stream_sha256": doc.get("stream_sha256"), "frames": frames,
            "hosts": sorted(frames), "why": f"check_s5_exit.identical: {result.detail}"}


def host_target(reader: Reader, means: Means, target: str) -> dict:
    scripts_importable(reader.root)
    import check_s5_exit as S5  # noqa: PLC0415
    rel, doc = reader.target_evidence(target, "host")
    if doc.get("replay") != STAGE or STAGE not in registered(reader) \
            or (doc.get("runner") or {}).get("kind") != means.runner[target]:
        raise Fail(f"{rel} is not {target}'s registered {STAGE} host run under "
                   f"{means.runner[target]}")
    ran(binaries(reader, target), "host", doc.get("binary_sha256"), f"{rel}'s host")
    stream = reference_clause(reader, "host", "stream_sha256")
    if doc.get("stream_sha256") != stream:
        raise Fail(f"{rel} replays another stream than {REFERENCE}'s comparison")
    ref_frames = reference_clause(reader, "host", "frames")

    def rows(ref: object, what: str) -> list[dict]:
        reader.cited(ref, what)
        try:
            return S5.table(reader.root / ref["path"], S5.TIMES_HEADER, what)
        except S5.Refused as why:
            raise Fail(str(why)) from None

    mine = rows(doc.get("frames"), f"{rel}'s frames")
    first = sorted(ref_frames)[0] if isinstance(ref_frames, dict) and ref_frames else None
    theirs = rows((ref_frames or {}).get(first), f"{REFERENCE}'s {first} frames")
    if len(mine) != len(theirs):
        raise Fail(f"{target} presented {len(mine)} frames, {REFERENCE}'s {first} {len(theirs)}")
    for a, b in zip(mine, theirs):
        for column in S5.COMPARED:
            if a.get(column) != b.get(column):
                raise Fail(f"frame {b.get('frame')}: {column} {a.get(column)} on {target}, "
                           f"{b.get(column)} on {REFERENCE}")
    return {"replay": STAGE, "stream_sha256": stream, "frames": len(mine),
            "binary_sha256": doc["binary_sha256"],
            "why": f"{STAGE}: {len(mine)} frames equal to {REFERENCE}'s {first} host"}


# ------------------------------------------------------------------ the user's native run


def native_run(reader: Reader, means: Means, target: str) -> dict:
    """The user's own run on a Windows PC, as the installer receipt keeps it (NATIVE_RUN): a
    route's last x86_64-windows run whose runner is `native`, which only
    host/launch/windows_routes.py --runner native writes, on Windows, for the user's run. Its
    runner names no Wine, the route hash-checked, installed and launched, the run passed, and
    the player it launched is the host binary the build census names. While no route holds a
    native run, the clause is pending."""
    if target != "x86_64-windows":
        raise Fail(f"a run on {target} is not the user's own x86_64-windows run")
    rel, schema = evidence_path(target, "native_run")
    doc = reader.json(rel, "the installer receipt", schema)
    if doc.get("generated_by") != INSTALLER_TOOL or not (reader.root / INSTALLER_TOOL).is_file():
        raise Fail(f"{rel} is not {INSTALLER_TOOL}'s")
    routes = doc.get("routes") if isinstance(doc.get("routes"), dict) else {}
    native = []
    for route in ROUTES:
        entry = (routes.get(route) or {}).get(target)
        evidence = entry.get("evidence") if isinstance(entry, dict) else None
        runner = evidence.get("runner") if isinstance(evidence, dict) else None
        if isinstance(runner, dict) and runner.get("kind") == "native":
            native.append((route, entry, evidence, runner))
    if not native:
        raise Fail(NATIVE_PENDING)
    built = binaries(reader, target)
    why = []
    for route, entry, evidence, runner in native:
        player = (evidence.get("launch") or {}).get(PLAYER)
        if "wine" in runner or "wine_version" in runner \
                or not str(runner.get("os", "")).startswith("win32"):
            why.append(f"{route}: the run is not native Windows without Wine")
        elif not all(entry.get(k) is True for k in ("hash_checked", "installed", "launched")) \
                or (entry.get("last_run") or {}).get("passed") is not True:
            why.append(f"{route}: not hash-checked, installed and launched")
        elif not isinstance(player, str) or not HEX64.match(player) or built.get("host") != player:
            why.append(f"{route}: the launch ran player {player!r}, not the host binary the "
                       "build census names")
        else:
            return {"operator": "user", "runner": runner, "route": route, "binary_sha256": player,
                    "native_routes": [r for r, *_ in native],
                    "why": f"the user's run on Windows: {route} installed and launched"}
    raise Fail(f"{rel}: no native run hash-checked, installed and launched the host: "
               f"{'; '.join(why)}")


REFERENCE_RULES = {"build": build_reference, "replays": replays_reference, "host": host_reference}
TARGET_RULES = {"build": build_census, "replays": replays_target, "host": host_target,
                "native_run": native_run}


# ------------------------------------------------------------------ the receipt


def clause(root: Path, means: Means, target: str, name: str) -> dict:
    """One clause's entry. Malformed evidence reads false with the error, never a crash."""
    reader = Reader(root)
    rules = REFERENCE_RULES if target == REFERENCE else TARGET_RULES
    try:
        facts = rules[name](reader, means, target)
        verdict, why = True, facts.pop("why")
        if reader.reference:
            facts["reference"] = {"receipt": RECEIPT.format(target=REFERENCE),
                                  "facts_sha256": dict(sorted(reader.reference.items()))}
    except Fail as failed:
        facts, verdict, why = {}, False, str(failed)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        facts, verdict, why = {}, False, f"malformed evidence: {type(error).__name__}: {error}"
    return {"evidence": reader.evidence(), "facts": facts, "verdict": verdict, "why": why}


def commit(root: Path) -> str | None:
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                          text=True)
    return done.stdout.strip() if done.returncode == 0 else None


def build_receipt(root: Path, target: str) -> dict:
    means = green_means(root)
    clauses = {name: clause(root, means, target, name) for name in means.clauses[target]}
    return {
        "schema": SCHEMA,
        "generated_by": SELF,
        "target": target,
        "green_means": GREEN_MEANS,
        "green_means_sha256": means.sha256,
        "runner": means.runner[target],
        "triple": means.triple[target],
        "command": f".venv/bin/python {SELF} --target {target}",
        "commit": commit(root),
        "written": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "inputs": {"pipeline": {rel: sha256_file(root / rel) for rel in PIPELINE
                                if (root / rel).is_file()}},
        "clauses": clauses,
        "green": all(entry["verdict"] for entry in clauses.values()),
    }


def moved(held: dict, doc: dict) -> list[str]:
    """The fields in which a written receipt differs from its recomputation."""
    return sorted(k for k in set(doc) | set(held) if k not in UNCOMPARED
                  and doc.get(k) != held.get(k))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--target", required=True, choices=TARGETS)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        doc = build_receipt(root, args.target)
    except (OSError, ValueError) as error:
        print(f"target receipt FAIL: {error}", file=sys.stderr)
        return 1
    out = args.out if args.out is not None else root / RECEIPT.format(target=args.target)
    for name, entry in doc["clauses"].items():
        print(f"  [{'true' if entry['verdict'] else 'false'}] {name}: {entry['why']}")
    if args.check:
        try:
            held = json.loads(out.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"target receipt STALE: {out} cannot be read: {error}")
            return 1
        differ = moved(held, doc) if isinstance(held, dict) else ["the whole receipt"]
        if differ:
            print(f"target receipt STALE: {out} differs in {', '.join(differ)}; re-run without "
                  "--check")
            return 1
        print(f"target receipt CURRENT: {args.target} green={doc['green']}")
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"target receipt WROTE {out}: {args.target} green={doc['green']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
