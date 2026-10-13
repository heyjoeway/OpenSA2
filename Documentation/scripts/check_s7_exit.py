#!/usr/bin/env python3
"""S7's exit and the work the user's bar adds to it, as predicates over tracked evidence.

Design section 5, S7, as amended 2026-10-10, quoted:

    Exit: M8 = 3/3 targets green; every replay's arm64 divergence labelled.

G7's bar is the user's, set at G5's closing
(`Documentation/decisions/2026-10-10-G5-close-answers.md`, answer 1): that exit, all
gates green and the replay regression, each clause a criterion of its own with
M8's input generated from receipts, and the first two routes of the user's
installer order (`Documentation/decisions/2026-10-07-release-order-and-installer.md`),
on x86-64 Linux, then x86-64 Windows, with the DRM-unwrap measurement first
(`Documentation/decisions/2026-10-09-S5-user-rulings.md`, item 3). G7's criteria in
`Documentation/gate-criteria.json` call these, one each:

    m8             M8 = 3/3 targets green
    arm64          every replay's arm64 divergence labelled
    installer      the installer's three routes on x86-64 Linux, then x86-64
                   Windows, the DRM-unwrap measurement first
    targets_input  M8's input generated from a tracked receipt per target,
                   every one of the three targets with its receipt

The items the user carried from G5 are not here: by the same answer the stock-SDL
gate and the depth-clamp measurement and fallback go to the Android step after G7,
the Metal conversion check to the macOS work, and R6 to S6.

What these predicates are, and are not
--------------------------------------
They were written when S7 opened, in the commit that closed G5, before any of
the evidence existed. Each receipt contract below is the opening's statement of
what closes its item, taken from the design's work list and the user's words,
and each is the least the item's text asks for: its schema, the tool that
writes it (`generated_by`, which must exist), and the fields its promise names.
Where a receipt reports an outcome of its producer's own work (a route
installed and launched, an arm64 replay's labels) the predicate reads it as the
producer's, as `check_s5_exit.py` reads a player's `complete`; S5's predicates
went further and recompute their verdicts from evidence tables, and when an S7
item's writer lands, holding it to the same is a strengthening of its
criterion (AGENTS.md rule 10: the agent's, flagged to the user when it changes
the pass condition). A contract S7 finds wrong is changed here and in the
criterion's falsifiers in one commit.

Two of them were strengthened on 2026-10-11, when the DRM-unwrap measurement's
receipt and the first target receipt landed (S7's second wave; a rule-10 notice,
since each changes G7's pass condition), and `m8` reads METRICS.md as it always
has:

* `installer` reads the measurement from its own receipt, `DRM_RECEIPT`, held to
  its schema and to the tool that writes it, with the executables it measured
  equal to the ones `tools/pins.json` pins. `bytes_match` is the measurement's:
  an installer receipt's copy that disagrees reads unmet. When the bytes differ,
  the ruling cited is a decision record of the user's under
  Documentation/decisions/ (a `.md` file whose Date paragraph says
  `DRM_DECISION` as a sentence of its own, the form this project's decision
  records use) that names this question: the measurement's receipt and the
  ruling of 2026-10-09 it answers
  (`Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md` is that record).
  A confirm review of S7's first wave reproduced each of the three holes this
  closes: a typed `bytes_match` true over a measured false, any file under
  Documentation/decisions/ as the ruling, and a measurement receipt of any
  schema.
* `targets_input` recomputes each target's green from the clause verdicts its
  receipt holds, for the clauses `GREEN_MEANS`'s table lists for the target, and
  refuses a receipt whose `green` flag disagrees, one written under another
  version of the record, and one whose tracked evidence no longer has the hash
  it recorded. It does not take the verdicts as written either: it recomputes
  each receipt with `TARGET_TOOL` in the tree it reads (`TARGET_RECOMPUTE`), over
  the same evidence on this machine, and reads unmet when the receipt differs
  from its recomputation in anything but `RECOMPUTE_IGNORED`, so a typed or a
  stale receipt is never read green (the review of S7's second wave reproduced
  both). targets.json must be `TARGETS_TOOL`'s, under the same record, naming
  each receipt it was generated from by its sha256.

* Every predicate is unmet on a structural zero: no registered replay, no
  route run, no target receipt.
* Where evidence is absent it says what is missing and where it goes.
* It returns one line, which `gate_report.py` quotes beside the verdict.

M8's denominator is the design's three targets (section 1.2, D3; the S7 note
of 2026-10-10: "M8's denominator stays the design's three targets"). Android
gets its own step after G7 and does not count toward M8, and the installer is
not required on arm64 Linux (the user's closing answers, answer 1).

Usage: .venv/bin/python Documentation/scripts/check_s7_exit.py [--root PATH]
(prints every predicate; exits 0 only when all are met).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The design's three targets, M8's denominator; held equal to metrics.TARGETS.
TARGETS = ("x86_64-linux", "x86_64-windows", "arm64-linux")
M8_ROW = re.compile(r"^\| M8 targets \| (\d+)/(\d+) \([^)]*\): ([^|]*?) \| [^|]*\| ([^|]*?) \|$",
                    re.M)
M8_STATUS = "measured"
REPLAYS = "Documentation/replays.json"
MIN_REPLAYS = 1
RESIDUES_DIR = "Documentation/receipts/residues"

ARM64_RECEIPT = "Documentation/receipts/s7-arm64-replays-v1.json"
ARM64_SCHEMA = "sa2-s7-arm64-replays/v1"
LABELLED_RESULTS = ("no divergence", "residues only")

INSTALLER_RECEIPT = "Documentation/receipts/s7-installer-v1.json"
INSTALLER_SCHEMA = "sa2-s7-installer/v1"
INSTALLER_ROUTES = ("steam-autodiscovery", "folder", "zip")
# The user's G7 routes, in the user's order: x86-64 Linux, then x86-64 Windows.
INSTALLER_TARGETS = ("x86_64-linux", "x86_64-windows")
INSTALLER_DRM = "drm_unwrap"
# The DRM-unwrap measurement, read from its own receipt and nowhere else (the user's ruling
# of 2026-10-09, item 3, which DRM_QUESTION names): its schema, the tool that writes it, and
# each executable it measured, by role, held to the sha256 DRM_PINS pins for it.
DRM_RECEIPT = "Documentation/receipts/s7-drm-unwrap-v1.json"
DRM_SCHEMA = "sa2-s7-drm-unwrap/v1"
DRM_TOOL = "tools/s7/drm_unwrap.py"
DRM_PINS = "tools/pins.json"
DRM_INPUTS = (("shipped", "sonic2app.exe"), ("unpacked", "sonic2app-unpacked.exe"))
# A ruling on the options, when the bytes differ: a decision record of the user's naming
# the measurement's receipt and the ruling it answers.
DRM_QUESTION = "Documentation/decisions/2026-10-09-S5-user-rulings.md"
DRM_DECISION = "A decision of the user's"
DECISIONS = "Documentation/decisions/"
# A decision record's Date paragraph, from the line that starts `Date:` to the first blank
# line: check_replay_regression.DATE_PARAGRAPH, the paragraph a decision's maker is read from.
DECISION_DATE = re.compile(r"^Date:.*?(?:\n[ \t]*\n|\Z)", re.M | re.S)
INSTALLER_RUN = {"hash_checked": True, "installed": True, "manual_steps": 0, "launched": True}
# The instant a route first passed on a target, and the instant the DRM-unwrap measurement's
# receipt was written: "first" and "then" are read from them.
INSTALLER_FIRST = "first_passed"
INSTALLER_WRITTEN = "written"

TARGETS_FILE = "private/generated/targets.json"
TARGETS_TOOL = "tools/s7/targets.py"
TARGET_RECEIPT = "Documentation/receipts/s7-target-{target}-v1.json"
TARGET_SCHEMA = "sa2-s7-target/v1"
TARGET_TOOL = "tools/s7/target_receipt.py"
TARGET_MEANS = "green_means"
# The tracked record of what green means, and the header of its table of clauses by target.
GREEN_MEANS = "Documentation/s7-green-means.md"
GREEN_MEANS_HEADER = "| Target | Clause |"
# Each target receipt is recomputed with TARGET_TOOL's TARGET_RECOMPUTE(root, target) and must
# equal the tracked one in every field but these: when, and at which commit, it was written.
TARGET_RECOMPUTE = "build_receipt"
RECOMPUTE_IGNORED = ("written", "commit")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass
class Result:
    met: bool
    detail: str


class Unmet(Exception):
    """Evidence absent or not to its contract: the predicate reads unmet, saying why."""


# ------------------------------------------------------------------ reading


def load(root: Path, rel: str, what: str) -> dict:
    path = root / rel
    if not path.is_file():
        raise Unmet(f"{rel} is absent ({what})")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise Unmet(f"{rel} is not JSON: {error}") from None
    if not isinstance(doc, dict):
        raise Unmet(f"{rel} is not a JSON object")
    return doc


def receipt(root: Path, rel: str, schema: str, what: str, tool: str | None = None) -> dict:
    """A receipt to its contract's common part: its schema, and the tool that writes it,
    which must exist, and be `tool` when the contract names one."""
    doc = load(root, rel, what)
    if doc.get("schema") != schema:
        raise Unmet(f"{rel} has schema {doc.get('schema')!r}, not {schema!r}")
    named = doc.get("generated_by")
    if not isinstance(named, str) or not named or not (root / named).is_file():
        raise Unmet(f"{rel} names no generating tool that exists ({named!r})")
    if tool is not None and named != tool:
        raise Unmet(f"{rel} is written by {named}, not {tool}")
    return doc


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registered(root: Path) -> list[str]:
    """The registered replays' names, Documentation/replays.json's order; at least one."""
    doc = load(root, REPLAYS, "the replay registry")
    names = [r.get("name") for r in doc.get("replays") or [] if isinstance(r, dict)]
    if any(not isinstance(n, str) or not n for n in names):
        raise Unmet(f"{REPLAYS} holds a replay with no name")
    if len(names) < MIN_REPLAYS:
        raise Unmet(f"{REPLAYS} registers {len(names)} replays, fewer than {MIN_REPLAYS}")
    return names


def residue_class(root: Path, rid: object) -> str:
    """The class of the residue record `rid` names, which must exist under RESIDUES_DIR."""
    if not isinstance(rid, str) or not rid:
        raise Unmet(f"a residue named {rid!r} is no record id")
    doc = load(root, f"{RESIDUES_DIR}/{rid}.json", "a residue record")
    if doc.get("id") != rid or not isinstance(doc.get("class"), str):
        raise Unmet(f"{RESIDUES_DIR}/{rid}.json is not the record of {rid}")
    return doc["class"]


def count(value: object, what: str, least: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < least:
        raise Unmet(f"{what} is {value!r}, not an integer of at least {least}")
    return value


def entry(doc: dict, key: str, rel: str) -> dict:
    found = doc.get(key)
    if not isinstance(found, dict):
        raise Unmet(f"{rel} has no {key!r} object")
    return found


def predicate(function):
    """Unmet raised anywhere inside reads as unmet with its reason."""
    def run(root: Path | str = ".") -> Result:
        try:
            return function(Path(root))
        except Unmet as unmet:
            return Result(False, str(unmet))
    run.__name__ = function.__name__
    run.__doc__ = function.__doc__
    return run


# ------------------------------------------------------------------ the exit


def m8(path: Path | str = "METRICS.md") -> Result:
    """M8 = 3/3 targets green: METRICS.md's M8 row reads every one of the design's three
    targets green, by name, measured, over the denominator metrics.py counts, which must be
    the design's three (metrics.TARGETS equal to TARGETS)."""
    path = Path(path)
    try:
        # metrics.py as the caller's path finds it: a criterion's check puts
        # Documentation/scripts first, relative to the tree it runs in.
        import metrics  # noqa: PLC0415
    except ImportError as error:
        return Result(False, f"metrics.py is not importable: {error}")
    if tuple(getattr(metrics, "TARGETS", ())) != TARGETS:
        return Result(False, f"metrics.py counts M8 over {metrics.TARGETS}, not the design's "
                             f"three {TARGETS}")
    if not path.is_file():
        return Result(False, f"{path} is absent")
    match = M8_ROW.search(path.read_text(encoding="utf-8"))
    if not match:
        return Result(False, "no M8 targets row with a green/targets ratio")
    green, total = int(match.group(1)), int(match.group(2))
    listed = [t.strip() for t in match.group(3).split(",") if t.strip() and t.strip() != "none"]
    status = match.group(4).strip()
    value = f"{green}/{total}: {', '.join(listed) or 'none'} ({status})"
    if total != len(TARGETS):
        return Result(False, f"{value}: the denominator is not the design's {len(TARGETS)}")
    if green != total or sorted(listed) != sorted(TARGETS):
        return Result(False, f"{value}: not every target green")
    if status != M8_STATUS:
        return Result(False, f"{value}: the row is not {M8_STATUS!r}")
    return Result(True, value)


@predicate
def arm64(root: Path) -> Result:
    """Every replay's arm64 divergence labelled: each registered replay's arm64 run reads no
    divergence or residues only, every residue a record under RESIDUES_DIR, with the first
    divergent tick, the transcendental sites reached and the divergence's magnitude (design
    5, S7's work: "Residue behaviour on arm64 documented per replay (transcendental sites
    reached, divergence magnitude)")."""
    names = registered(root)
    doc = receipt(root, ARM64_RECEIPT, ARM64_SCHEMA, "each registered replay run on arm64")
    replays = entry(doc, "replays", ARM64_RECEIPT)
    said, problems = [], []
    for name in names:
        run = replays.get(name)
        if not isinstance(run, dict):
            problems.append(f"{name}: no arm64 run")
            continue
        result = run.get("result")
        residues = run.get("residues")
        try:
            if result not in LABELLED_RESULTS:
                raise Unmet(f"result {result!r} is not one of {LABELLED_RESULTS}")
            if not isinstance(residues, list):
                raise Unmet("no residues list")
            classes = [residue_class(root, rid) for rid in residues]
            tick = run.get("first_divergent_tick")
            if result == "no divergence":
                if residues or tick is not None:
                    raise Unmet("no divergence, yet a residue or a divergent tick")
            else:
                if not residues:
                    raise Unmet("residues only, with no residue named")
                count(tick, "the first divergent tick")
            count(run.get("transcendental_sites"), "the transcendental sites reached")
            if not isinstance(run.get("magnitude"), str) or not run["magnitude"].strip():
                raise Unmet("no divergence magnitude")
        except Unmet as unmet:
            problems.append(f"{name}: {unmet}")
            continue
        said.append(f"{name} {result}" + (f" ({', '.join(classes)})" if classes else ""))
    if problems:
        return Result(False, "; ".join(problems))
    return Result(True, "; ".join(said))


# ------------------------------------------------------------------ the user's added work


def instant(value: object, what: str) -> datetime:
    """An ISO 8601 instant with its UTC offset."""
    if not isinstance(value, str) or not value:
        raise Unmet(f"{what} is {value!r}, not an instant")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise Unmet(f"{what} is {value!r}, not an instant") from None
    if moment.tzinfo is None:
        raise Unmet(f"{what} is {value!r}, with no UTC offset")
    return moment


def drm_measurement(root: Path) -> tuple[bool, datetime]:
    """The DRM-unwrap measurement as its own receipt states it: (bytes_match, written).
    The receipt is DRM_SCHEMA, written by DRM_TOOL, and measured the executables DRM_PINS
    pins, each by its role."""
    doc = receipt(root, DRM_RECEIPT, DRM_SCHEMA, "the DRM-unwrap measurement", tool=DRM_TOOL)
    pins = load(root, DRM_PINS, "the pinned executables")
    executables = pins.get("executables") if isinstance(pins.get("executables"), dict) else {}
    inputs = doc.get("inputs") if isinstance(doc.get("inputs"), dict) else {}
    for role, name in DRM_INPUTS:
        want = (executables.get(name) or {}).get("sha256")
        if not isinstance(want, str) or not SHA256.match(want):
            raise Unmet(f"{DRM_PINS} pins no sha256 for {name}")
        got = inputs.get(role).get("sha256") if isinstance(inputs.get(role), dict) else None
        if got != want:
            raise Unmet(f"{DRM_RECEIPT} measured {role} executable {got!r}, not the "
                        f"{name} {DRM_PINS} pins")
    if not isinstance(doc.get("bytes_match"), bool):
        raise Unmet(f"{DRM_RECEIPT} states no boolean bytes_match")
    return doc["bytes_match"], instant(doc.get(INSTALLER_WRITTEN),
                                       f"{DRM_RECEIPT}'s {INSTALLER_WRITTEN}")


def ruled(root: Path, rel: object) -> str:
    """The user's ruling on the options when the bytes differ: a `.md` decision record under
    DECISIONS whose Date paragraph says DRM_DECISION as a sentence of its own (so "not a
    decision of the user's", or the words quoted in its body, do not count), and that names
    this question, the measurement's receipt and the ruling of 2026-10-09 it answers."""
    if not isinstance(rel, str) or not rel.startswith(DECISIONS) or not rel.endswith(".md") \
            or ".." in rel.split("/") or not (root / rel).is_file():
        raise Unmet(f"the bytes differ and no ruling of the user's under {DECISIONS} is cited "
                    f"({rel!r})")
    text = (root / rel).read_text(encoding="utf-8")
    date = DECISION_DATE.search(text)
    said = " ".join(date.group(0).split()) if date else ""
    if not re.search(r"(?:^|[.!?;]\s+)" + re.escape(DRM_DECISION) + r"(?![\w'’])", said):
        raise Unmet(f"{rel} is not the user's ruling on this question: its Date paragraph does "
                    f"not say {DRM_DECISION!r}")
    missing = [words for words in (DRM_RECEIPT, DRM_QUESTION) if words not in text]
    if missing:
        raise Unmet(f"{rel} is not the user's ruling on this question: it does not say "
                    f"{', '.join(repr(w) for w in missing)}")
    return rel


@predicate
def installer(root: Path) -> Result:
    """The installer of 2026-10-07, its first two routes as the user's G7 bar: a Steam install
    found automatically, a selected unmodified game folder, or a ZIP of the user's legally
    obtained files, each hash-checked, installed with no manual rearrangement and launched, on
    x86-64 Linux, then x86-64 Windows (each route passed on Linux no later than on Windows); the
    DRM-unwrap measurement first (its receipt written before any route passed), and, when the
    bytes the runtime reads differ, the user's ruling on the options (the ruling of 2026-10-09,
    item 3). The measurement is read from its own receipt (`drm_measurement`), and the ruling
    is held to the question (`ruled`)."""
    if not set(INSTALLER_TARGETS) <= set(TARGETS):
        raise Unmet(f"the installer's targets {INSTALLER_TARGETS} are not all M8's {TARGETS}")
    doc = receipt(root, INSTALLER_RECEIPT, INSTALLER_SCHEMA, "the installer's routes")
    problems = []
    measured, bytes_match, ruling = None, None, None
    cited = doc.get(INSTALLER_DRM)
    if not isinstance(cited, dict):
        problems.append("no DRM-unwrap measurement cited")
    else:
        try:
            if cited.get("receipt") != DRM_RECEIPT:
                raise Unmet(f"{INSTALLER_RECEIPT} cites {cited.get('receipt')!r}, not the "
                            f"measurement's receipt {DRM_RECEIPT}")
            bytes_match, measured = drm_measurement(root)
            if "bytes_match" in cited and cited["bytes_match"] is not bytes_match:
                raise Unmet(f"{INSTALLER_RECEIPT}'s bytes_match reads {cited['bytes_match']!r}, "
                            f"and {DRM_RECEIPT} measured {bytes_match!r}")
            if not bytes_match:
                ruling = ruled(root, cited.get("ruling"))
        except Unmet as unmet:
            problems.append(f"DRM-unwrap measurement: {unmet}")
    routes = doc.get("routes") if isinstance(doc.get("routes"), dict) else {}
    for route in INSTALLER_ROUTES:
        per = routes.get(route) if isinstance(routes.get(route), dict) else {}
        passed = {}
        for target in INSTALLER_TARGETS:
            run = per.get(target)
            wrong = [key for key, want in INSTALLER_RUN.items()
                     if not isinstance(run, dict) or run.get(key) != want
                     or type(run.get(key)) is not type(want)]
            if wrong:
                problems.append(f"{route} on {target}: {', '.join(wrong)} not as required")
                continue
            try:
                passed[target] = instant(run.get(INSTALLER_FIRST),
                                         f"{route} on {target}'s {INSTALLER_FIRST}")
            except Unmet as unmet:
                problems.append(str(unmet))
                continue
            if measured is not None and passed[target] < measured:
                problems.append(f"{route} on {target} passed before the DRM-unwrap measurement")
        for earlier, later in zip(INSTALLER_TARGETS, INSTALLER_TARGETS[1:]):
            if earlier in passed and later in passed and passed[later] < passed[earlier]:
                problems.append(f"{route} passed on {later} before {earlier}")
    if problems:
        return Result(False, "; ".join(problems))
    return Result(True, f"{', '.join(INSTALLER_ROUTES)} on {', then '.join(INSTALLER_TARGETS)}; "
                        f"DRM unwrap {'not needed' if bytes_match else 'ruled in ' + ruling}, "
                        "measured first")


def green_clauses(root: Path) -> dict[str, list[str]]:
    """GREEN_MEANS's table of clauses by target, read here on its own: the rows under the
    header GREEN_MEANS_HEADER, each naming a target and a clause in code spans. Every one of
    M8's targets must have a clause, and no other target may."""
    path = root / GREEN_MEANS
    if not path.is_file():
        raise Unmet(f"{GREEN_MEANS} is absent (what green means for a target)")
    clauses: dict[str, list[str]] = {}
    inside = False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(GREEN_MEANS_HEADER):
            inside = True
            continue
        if not line.startswith("|"):
            inside = False
            continue
        row = re.match(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|", line) if inside else None
        if row:
            clauses.setdefault(row.group(1), []).append(row.group(2))
    absent = [t for t in TARGETS if not clauses.get(t)]
    if absent or set(clauses) - set(TARGETS):
        raise Unmet(f"{GREEN_MEANS}'s table lists clauses for {sorted(clauses)}, not for each "
                    f"of M8's {list(TARGETS)}")
    return clauses


def target_green(root: Path, target: str, means_sha: str, clauses: list[str]) -> tuple[bool, str]:
    """(target's green recomputed from its receipt's clause verdicts, the receipt's sha256).
    Each clause GREEN_MEANS lists for the target needs a boolean verdict, and a true one its
    evidence; every tracked evidence file must still hash as the receipt recorded (files under
    private/ are read again by `reproduced`, which recomputes the whole receipt); and the
    receipt's own green flag must equal the recomputed one."""
    rel = TARGET_RECEIPT.format(target=target)
    got = receipt(root, rel, TARGET_SCHEMA, f"target {target}", tool=TARGET_TOOL)
    if got.get("target") != target or not isinstance(got.get("green"), bool):
        raise Unmet(f"{rel} is not {target}'s green or not")
    if got.get(TARGET_MEANS) != GREEN_MEANS:
        raise Unmet(f"{rel} does not cite {GREEN_MEANS} for what green means")
    if got.get("green_means_sha256") != means_sha:
        raise Unmet(f"{rel} was written under another version of {GREEN_MEANS}")
    held = got.get("clauses") if isinstance(got.get("clauses"), dict) else {}
    verdicts = []
    for name in clauses:
        entry = held.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("verdict"), bool):
            raise Unmet(f"{rel} holds no verdict for its {name} clause")
        evidence = entry.get("evidence")
        if not isinstance(evidence, list) or (entry["verdict"] and not evidence):
            raise Unmet(f"{rel}: its {name} clause reads {entry['verdict']} on no evidence")
        for item in evidence:
            cited = item.get("path") if isinstance(item, dict) else None
            sha = item.get("sha256") if isinstance(item, dict) else None
            if not isinstance(cited, str) or not isinstance(sha, str) or not SHA256.match(sha):
                raise Unmet(f"{rel}: its {name} clause cites evidence with no path and sha256")
            if cited.startswith("private/"):
                continue
            if not (root / cited).is_file() or sha256_file(root / cited) != sha:
                raise Unmet(f"{rel}: {cited} is not the file its {name} clause read")
        verdicts.append(entry["verdict"])
    green = all(verdicts)
    if got["green"] is not green:
        raise Unmet(f"{rel} reads green {got['green']}, and its clause verdicts {green}")
    return green, sha256_file(root / rel)


def target_tool(root: Path):
    """TARGET_TOOL as the tree read holds it, loaded once per path."""
    path = root.resolve() / TARGET_TOOL
    key = "check_s7_exit_target_tool_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[key]
            raise
    return sys.modules[key]


def reproduced(root: Path, target: str) -> None:
    """The tracked receipt of `target` is what TARGET_TOOL derives from the evidence now, on
    this machine: every field but RECOMPUTE_IGNORED equal, the clause verdicts among them."""
    rel = TARGET_RECEIPT.format(target=target)
    held = load(root, rel, f"target {target}")
    try:
        fresh = getattr(target_tool(root), TARGET_RECOMPUTE)(root, target)
    except (Exception, SystemExit) as error:  # noqa: BLE001 - a tool that cannot run is unmet
        raise Unmet(f"{TARGET_TOOL} cannot recompute {rel}: {type(error).__name__}: "
                    f"{error}") from None
    differ = sorted(k for k in set(fresh) | set(held)
                    if k not in RECOMPUTE_IGNORED and fresh.get(k) != held.get(k))
    if differ:
        now = fresh.get("clauses") if isinstance(fresh.get("clauses"), dict) else {}
        was = held.get("clauses") if isinstance(held.get("clauses"), dict) else {}
        verdicts = [f"{name} recomputed {entry.get('verdict')}: {entry.get('why')}"
                    for name, entry in now.items()
                    if (was.get(name) or {}).get("verdict") != entry.get("verdict")]
        raise Unmet(f"{rel} is not what {TARGET_TOOL} derives from the evidence now: it "
                    f"differs in {', '.join(differ)}" + (f" ({'; '.join(verdicts)})"
                                                        if verdicts else ""))


@predicate
def targets_input(root: Path) -> Result:
    """M8's input generated, not typed: private/generated/targets.json is TARGETS_TOOL's,
    citing GREEN_MEANS, the tracked record of what green means for a target, at its current
    sha256; every one of M8's targets has its tracked receipt, TARGET_TOOL's under the same
    record; each target's green is recomputed from its receipt's verdicts for the clauses the
    record lists for it, never taken from the receipt's flag; each receipt is what TARGET_TOOL
    derives from its evidence now (`reproduced`), so no verdict is taken as typed; targets.json
    names each receipt it was generated from by its sha256; and the green list is exactly the
    targets so recomputed green. A target with no receipt reads unmet, so zero receipts never
    read met."""
    doc = load(root, TARGETS_FILE, "M8's input")
    tool = doc.get("generated_by")
    if tool != TARGETS_TOOL or not (root / tool).is_file():
        raise Unmet(f"{TARGETS_FILE} is not written by {TARGETS_TOOL} ({tool!r})")
    means = doc.get(TARGET_MEANS)
    if means != GREEN_MEANS or not (root / means).is_file():
        raise Unmet(f"{TARGETS_FILE} cites {means!r}, not the tracked record {GREEN_MEANS} of "
                    "what green means")
    means_sha = sha256_file(root / GREEN_MEANS)
    if doc.get("green_means_sha256") != means_sha:
        raise Unmet(f"{TARGETS_FILE} was generated under another version of {GREEN_MEANS}")
    required = green_clauses(root)
    listed = doc.get("green")
    if not isinstance(listed, list) or not set(listed) <= set(TARGETS):
        raise Unmet(f"{TARGETS_FILE}'s green list is not a list of M8's targets")
    sources = doc.get("receipts") if isinstance(doc.get("receipts"), dict) else {}
    green, problems = set(), []
    for target in TARGETS:
        rel = TARGET_RECEIPT.format(target=target)
        if not (root / rel).is_file():
            problems.append(f"{rel} is absent (target {target}'s receipt)")
            continue
        try:
            is_green, sha = target_green(root, target, means_sha, required[target])
            reproduced(root, target)
            source = sources.get(target)
            if not isinstance(source, dict) or source.get("path") != rel \
                    or source.get("sha256") != sha or source.get("green") is not is_green:
                raise Unmet(f"{TARGETS_FILE} was not generated from {rel} as it stands")
        except Unmet as unmet:
            problems.append(str(unmet))
            continue
        if is_green:
            green.add(target)
    if set(listed) != green:
        problems.append(f"{TARGETS_FILE} lists {sorted(listed)} green; the receipts read "
                        f"{sorted(green)}")
    if problems:
        return Result(False, "; ".join(problems))
    return Result(True, f"{len(green)} of {len(TARGETS)} green by their receipts' clause "
                        f"verdicts, written by {tool}")


PREDICATES = {"M8": lambda root: m8(Path(root) / "METRICS.md"), "arm64": arm64,
              "installer": installer, "targets-input": targets_input}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    unmet = 0
    for name, function in PREDICATES.items():
        result = function(args.root)
        unmet += not result.met
        print(f"  [{'met' if result.met else 'not met'}] {name}: {result.detail}")
    print(f"S7 exit: {len(PREDICATES) - unmet} of {len(PREDICATES)} met")
    return 0 if unmet == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
