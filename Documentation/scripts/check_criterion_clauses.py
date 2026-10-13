#!/usr/bin/env python3
"""Every clause a gate criterion promises is falsified, by running its check.

The defect this exists for
--------------------------

Twice, a criterion in `Documentation/gate-criteria.json` promised two things
and its check tested one. Both were found by a human reading a generated
report, a stage after the fact.

**C2 (G2 interrogation, found at G3).** The criterion read *"trap count
recorded and every trap opcode listed"*. Its check asserted that the Traps
row of `METRICS.md` did not read `not measured`, and nothing else. It would
have passed with no opcode anywhere in the project, and no opcode was
anywhere a gate could see. The word "and" joined two promises; the check
tested one.

**D2 (G3 interrogation, second round).** The criterion read *"M1 = 100%
emitted in the game region"* -- one promise, no conjunction. An interrogation
then added an instruction-level coverage companion to the M1 row, and the
check was never extended to look at it. Proven blind afterwards: it passed
with the companion deleted, with a stale figure restored, and with the value
set to `1/971553`.

D2 is why this gate does not work by reading the criterion's English. The
second promise never appeared in the criterion's text at all; it appeared in
the evidence the check reads. A rule that splits criterion text on "and"
catches C2 and is structurally blind to D2, which is half the incidents.

What a "clause" is here
-----------------------

A clause is **declared, not inferred**: a criterion lists its own clauses,
and each clause names the mutations of the evidence that must make its check
fail.

    {"criterion": "trap count recorded and every trap opcode listed",
     "clauses": [
       {"promise": "every trap opcode listed",
        "checked_as": "the opcode of the instruction reaching the unlifted block",
        "falsifiers": [{"file": "METRICS.md", "replace": "...", "with": ""}]}]}

Three rules considered and rejected:

  * *Split the text on "and".* Crude, as the brief says -- and worse, it is
    blind to D2, whose criterion has no conjunction. A gate that cannot see
    half of the incidents it was built for is not the gate.
  * *Parse the check and count its assertions.* `sys.exit(0 if a and b else 1)`
    has two assertions whether or not they cover the text. It measures the
    check's shape, not its reach, and C2's check had a conjunction too.
  * *Declared clauses, coverage by naming a substring of the check.* Better,
    but a substring only proves a fragment is present, not that it can fail.
    C2's check contained `'not measured' not in v` -- a fragment that is
    present, and true forever.

A falsifier is either a substitution (`replace` / `with`, and `occurrences`
when the text it anchors on is meant to appear more than once) or `absent`,
which leaves the named path out of the overlay altogether -- the mutation for
"this evidence was never delivered".

So a declared clause carries **falsifiers that this gate executes**. For each
one the check is run twice: once against an unmutated overlay of the
repository (the control, which must pass) and once against an overlay in
which the named file carries the mutation (which must fail). A clause whose
mutation leaves the check passing is C2, mechanically, at the moment it is
written rather than a stage later.

The falsification table stops being something an agent does by hand once and
writes into a journal -- G3's C2 and D2 folds each have one -- and becomes an
artifact the gate re-runs on every push (AGENTS.md rule 13).

Text is still audited, as a floor rather than as the mechanism: the criterion
is split on conjunctions and a criterion must declare at least as many
clauses as it has conjuncts. That is what makes C2 impossible to write in the
first place -- two promises, one clause, refused.

The `criterion_note` half of AGENTS.md rule 14
----------------------------------------------

A clause whose `promise` is not literally in the criterion's text is a
reinterpretation, and says so with `checked_as`. Any criterion carrying such
a clause must carry a `criterion_note` naming what was repointed and to what;
and a criterion carrying a note must carry such a clause, because a note
records a repointing and a repointing is a clause somebody has to falsify.
That is the link the G2 interrogation broke: it reinterpreted Traps
correctly, wrote no clause for the half it had repointed, and left it
pointing at nothing.

Evidence shape, which is the part that sees D2 coming
-----------------------------------------------------

A criterion also pins the **shape** of the evidence rows its check reads:
each row is located by a substring, its digits are replaced by `#`, its
whitespace is collapsed, and the digests are pinned. Numbers may move
freely -- that is what `METRICS.md` is for -- but a row that gains a clause,
loses one, or is reworded changes shape, and the gate fails naming the
criterion that reads it.

That is the D2 event exactly: the M1 row grew an instruction-level companion
and nothing anywhere had to notice. Under this gate the row's shape changes,
this gate fails, and the author must either extend the criterion's clauses or
repin the shape deliberately. A row substring that matches no line is also a
failure: the evidence a criterion reads cannot quietly disappear.

What is delegated, and what that costs
--------------------------------------

A criterion whose check is a script (`{python} Documentation/scripts/x.py`)
rather than an inline `-c` program delegates its clauses to that script,
which has its own gate and its own tests; running it here would mean running
`run_all_gates.py` from inside `run_all_gates.py`. Those criteria are checked
only for delegation being real: the script must be one the full suite also
runs, so a criterion cannot delegate to something nobody executes.

A criterion whose control run fails is **unmet**, not broken: during a stage,
a future gate's criteria are supposed to fail. Its falsifiers are deferred
with a `note:` line, and become enforced as soon as the criterion is met --
which is before its gate can be closed. The cost is that a check which can
*never* pass is indistinguishable here from one not yet satisfied; the
defect class this gate owns is the opposite one, a check that can never fail.

A criterion may shorten that wait by declaring a **met state** (`met_state`,
substitutions written like falsifiers, with a `met_state_note` saying what
they stand in for): when its control fails, the check is run once more on an
overlay carrying those substitutions, and if it passes there, every falsifier
is applied on top of them and must make it fail. So a clause added while its
criterion is unmet -- M5's identity rule, added while the boot replay still
diverges (Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md)
-- is shown able to fail from the push that adds it. A met state is only ever
a stage for the falsifiers: it never makes a criterion read as met, and when
the check fails on it too, the falsifiers are deferred as before, with a note
saying so, since the real evidence is then what keeps it from passing.

The project root is `git rev-parse --show-toplevel` from the current
directory, or `--root PATH`.

Usage: .venv/bin/python Documentation/scripts/check_criterion_clauses.py
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from gitlog import project_root
from run_all_gates import GATES

CRITERIA = "Documentation/gate-criteria.json"
GATE_KEY = re.compile(r"^G\d+$")
SUITE = "Documentation/scripts/run_all_gates.py"
SCRIPTS_DIR = "Documentation/scripts"
# A check gets its own process and a bounded one; the inline criteria in this
# repository read one file and exit.
CHECK_TIMEOUT_SECONDS = 300
MIN_NOTE_WORDS = 20
# What separates two promises in a criterion's English. Used as a floor on the
# number of declared clauses, never as the clause list itself.
CONJUNCTION = re.compile(r"\s+and\s+|\s+plus\s+|\s+or\s+|;\s+|,\s+and\s+")
PARENTHETICAL = re.compile(r"\([^)]*\)")
DIGITS = re.compile(r"\d+")


# ---------------------------------------------------------------- text


def conjuncts(criterion: str) -> list[str]:
    """The separately-promising segments of a criterion's text.

    Trailing citations (`(design 5, S3)`) are not promises, so parentheticals
    are dropped before splitting.
    """
    bare = PARENTHETICAL.sub(" ", criterion)
    parts = [segment.strip(" ,.;") for segment in CONJUNCTION.split(bare)]
    return [part for part in parts if part]


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


def shape(text: str, rows: list[str]) -> tuple[str, list[str], int]:
    """(digest, rows matching no line, number of lines pinned).

    The digest is over the matching lines with every digit run replaced by `#`
    and whitespace collapsed, so a metric that moves does not break the pin
    and a row that changes structure does.
    """
    lines = text.splitlines()
    picked = [line for line in lines if any(row in line for row in rows)]
    missing = [row for row in rows if not any(row in line for line in lines)]
    normal = "\n".join(
        " ".join(DIGITS.sub("#", line).split()) for line in picked)
    return hashlib.sha256(normal.encode("utf-8")).hexdigest(), missing, len(picked)


# ---------------------------------------------------------------- checks


def inline_code(check: str) -> str | None:
    """The body of a `{python} -c "<code>"` check, or None when it is not one."""
    try:
        words = shlex.split(check)
    except ValueError:
        return None
    for index, word in enumerate(words):
        if word == "-c" and index + 1 < len(words):
            return words[index + 1]
    return None


def script_path(check: str) -> str | None:
    """The script a `{python} <path> ...` check runs, or None."""
    try:
        words = shlex.split(check)
    except ValueError:
        return None
    if len(words) < 2 or words[0] != "{python}":
        return None
    return words[1] if not words[1].startswith("-") else None


def suite_scripts() -> set[str]:
    """Every script the full gate suite runs, as a repository-relative path."""
    paths = {SUITE}
    for _, command in GATES:
        head = command[0]
        if head == "-m":
            continue
        paths.add(head if "/" in head else f"{SCRIPTS_DIR}/{head}")
    return paths


def opened_files(code: str) -> list[str]:
    """Every literal path the inline check passes to `open(...)`."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "open"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            found.append(node.args[0].value)
    return found


def describe(falsifier: dict) -> str:
    """What a falsifier does to its file, for the failure message."""
    if falsifier.get("absent") is True:
        return "absent"
    replace = str(falsifier.get("replace", ""))
    replacement = str(falsifier.get("with", ""))
    became = f"{replacement[:40]!r}" if replacement.strip() else "removed"
    return f"mutated ({replace[:60]!r} -> {became})"


def build_overlay(root: Path, dest: Path, mutations: dict[str, str | None]) -> None:
    """A working directory that is `root`, with `mutations` applied.

    Every entry is a symlink to the real thing except along the paths being
    mutated, whose directories are mirrored and whose file is written (or, for
    a `None` content, left out). A check therefore sees the whole repository
    and one changed file, and nothing tracked is touched.
    """
    mutated = [Path(relative) for relative in mutations]

    def mirror(relative: Path | None) -> None:
        source = root if relative is None else root / relative
        target = dest if relative is None else dest / relative
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            child_relative = child.name if relative is None else relative / child.name
            child_relative = Path(str(child_relative))
            key = str(child_relative)
            if key in mutations:
                content = mutations[key]
                if content is not None:
                    (dest / child_relative).write_text(content, encoding="utf-8")
                continue
            if any(str(path).startswith(key + "/") for path in mutated):
                mirror(child_relative)
                continue
            (dest / child_relative).symlink_to(child)

    mirror(None)


def run_check(check: str, cwd: Path) -> tuple[int, str]:
    """Run a criterion's check exactly as gate_report.py runs it."""
    command = check.replace("{python}", shlex.quote(sys.executable))
    try:
        finished = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                                  text=True, timeout=CHECK_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {CHECK_TIMEOUT_SECONDS}s"
    output = [line for line in (finished.stdout + finished.stderr).splitlines()
              if line.strip()]
    return finished.returncode, (output[-1].strip() if output else "(no output)")


# ---------------------------------------------------------------- criteria


class Auditor:
    def __init__(self, root: Path, scratch: Path, run: bool) -> None:
        self.root = root
        self.scratch = scratch
        self.run = run
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.declared = 0
        self.falsified = 0
        self.deferred = 0
        self.delegated = 0
        self.on_met_state = 0
        self.suite = suite_scripts()
        self.control_dir: Path | None = None
        self.serial = 0

    def fail(self, message: str) -> None:
        self.problems.append(message)

    def control_overlay(self) -> Path:
        if self.control_dir is None:
            self.control_dir = self.scratch / "control"
            build_overlay(self.root, self.control_dir, {})
        return self.control_dir

    # -- falsifier -----------------------------------------------------

    def mutation(self, where: str, falsifier: object,
                 base: dict[str, str | None] | None = None) -> tuple[str, str | None] | None:
        """(relative path, new content or None) for one falsifier, or None.
        `base` holds files a met state has already rewritten: a falsifier on
        one of them substitutes in that text, not the file's."""
        if not isinstance(falsifier, dict):
            self.fail(f"{where} falsifier is not an object")
            return None
        relative = falsifier.get("file")
        if not isinstance(relative, str) or not relative.strip():
            self.fail(f"{where} falsifier has no 'file'")
            return None
        path = self.root / relative
        if not path.exists():
            self.fail(f"{where} falsifier names {relative}, which does not exist")
            return None
        if falsifier.get("absent") is True:
            return relative, None
        replace = falsifier.get("replace")
        replacement = falsifier.get("with")
        if not isinstance(replace, str) or not replace:
            self.fail(f"{where} falsifier has neither 'absent' nor a non-empty 'replace'")
            return None
        if not isinstance(replacement, str):
            self.fail(f"{where} falsifier has no 'with' string")
            return None
        if replacement == replace:
            self.fail(f"{where} falsifier replaces text with itself: it mutates nothing")
            return None
        if not path.is_file():
            self.fail(f"{where} falsifier substitutes inside {relative}, which is not a file")
            return None
        expected = falsifier.get("occurrences", 1)
        if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
            self.fail(f"{where} falsifier's 'occurrences' is not a positive integer")
            return None
        if base is not None and relative in base:
            if base[relative] is None:
                self.fail(f"{where} falsifier substitutes inside {relative}, which the met "
                          f"state leaves out")
                return None
            text = base[relative]
        else:
            text = path.read_text(encoding="utf-8")
        count = text.count(replace)
        if count != expected:
            self.fail(f"{where} falsifier's 'replace' occurs {count} times in "
                      f"{relative}, not the declared {expected} (the author states the "
                      f"count so that drift in the evidence breaks the falsifier "
                      f"rather than quietly changing what it mutates): {replace[:80]!r}")
            return None
        return relative, text.replace(replace, replacement)

    def falsify(self, where: str, check: str, control_ok: bool,
                mutated: tuple[str, str | None], mutation: str,
                base: dict[str, str | None] | None = None) -> None:
        """Run the check with one falsifier applied -- on top of the met state
        `base` when the criterion's control passed only there -- and fail
        unless the check fails."""
        relative, content = mutated
        if not self.run:
            return
        if not control_ok:
            self.deferred += 1
            return
        self.serial += 1
        directory = self.scratch / f"mutant{self.serial:03d}"
        build_overlay(self.root, directory, {**(base or {}), relative: content})
        status, last = run_check(check, directory)
        if status == 0:
            stage = " on its declared met state" if base else ""
            self.fail(
                f"{where} the check still PASSES with {relative} {mutation}{stage}. "
                f"This clause has no check that can fail (AGENTS.md rule 14): it is "
                f"the C2/D2 defect -- a criterion promising two things whose check "
                f"tests one. Its last line under the mutation was: {last[:100]}")
            return
        self.falsified += 1
        if base:
            self.on_met_state += 1

    def met_state_declared(self, where: str, entry: dict) -> list | None:
        """A criterion's declared met state, held to its form whether or not
        it will be run: a non-empty list of substitutions (never a file left
        out: a met state substitutes, and a falsifier removes) and a
        `met_state_note` of MIN_NOTE_WORDS words or more. None when it
        declares none, or declares one that is refused."""
        subs = entry.get("met_state")
        if subs is None:
            return None
        note = entry.get("met_state_note")
        if not isinstance(subs, list) or not subs or not all(isinstance(s, dict) for s in subs):
            self.fail(f"{where} 'met_state' is not a non-empty list of substitutions")
            return None
        if any(s.get("absent") is True for s in subs):
            self.fail(f"{where} 'met_state' leaves a file out; a met state substitutes, and a "
                      f"falsifier removes")
            return None
        if not isinstance(note, str) or len(note.split()) < MIN_NOTE_WORDS:
            self.fail(f"{where} declares a met state without a 'met_state_note' of at least "
                      f"{MIN_NOTE_WORDS} words saying what its substitutions stand in for")
            return None
        return subs

    def met_state(self, where: str, subs: list, check: str) -> dict[str, str | None] | None:
        """The overlay mutations of a declared met state when the check passes
        on them, else None: the falsifiers are then deferred, since the real
        evidence is what keeps the check from passing."""
        base: dict[str, str | None] = {}
        for position, sub in enumerate(subs):
            mutated = self.mutation(f"{where} met_state[{position}]", sub, base)
            if mutated is None:
                return None
            base[mutated[0]] = mutated[1]
        self.serial += 1
        directory = self.scratch / f"met{self.serial:03d}"
        build_overlay(self.root, directory, base)
        status, last = run_check(check, directory)
        if status != 0:
            self.notes.append(f"note: {where} fails on its declared met state too "
                              f"({last[:80]}), so the real evidence keeps it from passing")
            return None
        return base

    # -- clause --------------------------------------------------------

    def clause(self, where: str, criterion: str, note: object, index: int,
               entry: object, check: str, control_ok: bool,
               base: dict[str, str | None] | None = None) -> bool:
        """Audit one clause; return whether it declared a reinterpretation.
        `base` is the met state the falsifiers run on, when the control passed
        only there."""
        place = f"{where} clause[{index}]"
        if not isinstance(entry, dict):
            self.fail(f"{place} is not an object")
            return False
        promise = entry.get("promise")
        if not isinstance(promise, str) or not promise.strip():
            self.fail(f"{place} has no non-empty 'promise'")
            return False
        reinterpreted = entry.get("checked_as")
        if reinterpreted is not None and (
                not isinstance(reinterpreted, str) or not reinterpreted.strip()):
            self.fail(f"{place} ({promise}) has an empty 'checked_as'")
            return False
        if reinterpreted is None and normalise(promise) not in normalise(criterion):
            self.fail(
                f"{place} promises {promise!r}, which is not in the criterion's own "
                f"text. A clause the text does not contain is a reinterpretation: "
                f"give it 'checked_as' and the criterion a 'criterion_note' "
                f"(AGENTS.md rule 14)")
            return False
        if reinterpreted is not None and not isinstance(note, str):
            self.fail(
                f"{place} ({promise}) is checked as something else and the criterion "
                f"carries no 'criterion_note'. Rule 14 wants the repointing named -- "
                f"what was repointed and to what -- so it is a record and not an "
                f"inference from the check's code")
            return False
        falsifiers = entry.get("falsifiers")
        if not isinstance(falsifiers, list) or not falsifiers:
            self.fail(f"{place} ({promise}) declares no 'falsifiers': a clause is a "
                      f"promise plus at least one mutation that must break the check")
            return False
        self.declared += 1
        for position, falsifier in enumerate(falsifiers):
            mutated = self.mutation(f"{place}.falsifiers[{position}]", falsifier, base)
            if mutated is not None:
                self.falsify(f"{place} ({promise}) falsifier[{position}]:", check,
                             control_ok, mutated, describe(falsifier), base)
        return reinterpreted is not None

    # -- evidence shape ------------------------------------------------

    def evidence(self, where: str, entry: dict) -> set[str]:
        """Audit `evidence_shape`; return the files it pins."""
        pinned: set[str] = set()
        shapes = entry.get("evidence_shape", [])
        if not isinstance(shapes, list):
            self.fail(f"{where} 'evidence_shape' is not an array")
            return pinned
        for index, item in enumerate(shapes):
            place = f"{where} evidence_shape[{index}]"
            if not isinstance(item, dict):
                self.fail(f"{place} is not an object")
                continue
            relative = item.get("file")
            rows = item.get("rows")
            digest = item.get("shape_sha256")
            if not isinstance(relative, str) or not (self.root / relative).is_file():
                self.fail(f"{place} names no readable file")
                continue
            if not isinstance(rows, list) or not rows or \
                    not all(isinstance(row, str) and row for row in rows):
                self.fail(f"{place} ({relative}) has no non-empty 'rows' list")
                continue
            pinned.add(relative)
            text = (self.root / relative).read_text(encoding="utf-8")
            found, missing, lines = shape(text, rows)
            if missing:
                self.fail(
                    f"{place} ({relative}) pins row(s) that match no line: {missing}. "
                    f"The evidence this criterion reads has been renamed or removed")
                continue
            if not isinstance(digest, str) or digest != found:
                self.fail(
                    f"{place} ({relative}) shape has changed: pinned "
                    f"{str(digest)[:12]}..., now {found[:12]}... over {lines} line(s). "
                    f"Digits are normalised, so a number moving cannot cause this -- "
                    f"a row gained, lost or reworded a clause. This is the D2 event: "
                    f"extend this criterion's clauses to cover the change, then repin "
                    f"'shape_sha256' to {found}")
        return pinned

    # -- criterion -----------------------------------------------------

    def criterion(self, gate: str, index: int, entry: dict) -> dict:
        where = f"{gate}[{index}]"
        criterion = entry["criterion"]
        check = entry["check"]
        note = entry.get("criterion_note")
        where = f"{where} ({criterion})"
        record: dict = {"check": check}

        if isinstance(note, str) and len(note.split()) < MIN_NOTE_WORDS:
            self.fail(f"{where} 'criterion_note' is {len(note.split())} words; a note "
                      f"that names what was repointed and to what needs at least "
                      f"{MIN_NOTE_WORDS}")
        code = inline_code(check)
        clauses = entry.get("clauses")
        if code is None:
            relative = script_path(check)
            if relative is not None and relative not in self.suite:
                self.fail(f"{where} delegates to {relative}, which the full gate suite "
                          f"does not run: its clauses are covered by nothing")
            if clauses:
                self.fail(f"{where} is a script check and declares clauses; a script "
                          f"check's clauses belong to the script and its own tests")
            if isinstance(note, str):
                self.fail(f"{where} is a script check and carries a 'criterion_note'; "
                          f"a reinterpretation belongs where its check is")
            if "met_state" in entry:
                self.fail(f"{where} is a script check and declares a met state; its "
                          f"falsifications belong to the script and its own tests")
            self.delegated += 1
            record["delegated_to"] = relative
            return record

        if not isinstance(clauses, list) or not clauses:
            self.fail(f"{where} has an inline check and declares no 'clauses'. "
                      f"Rule 14: every clause of a criterion needs a check that can "
                      f"fail, and this file is where the clauses are stated")
            return record
        segments = conjuncts(criterion)
        if len(clauses) < len(segments):
            self.fail(
                f"{where} declares {len(clauses)} clause(s) for text that promises "
                f"{len(segments)}: {segments}. This is C2 -- 'trap count recorded and "
                f"every trap opcode listed' with one check on the first half")
        declared = self.met_state_declared(where, entry)
        control_ok = True
        base = None
        if self.run:
            status, last = run_check(check, self.control_overlay())
            control_ok = status == 0
            if not control_ok:
                base = self.met_state(where, declared, check) if declared else None
                if base is not None:
                    control_ok = True
                    self.notes.append(
                        f"note: {where} control run fails ({last[:80]}); the criterion is "
                        f"not met yet, and its {len(clauses)} clause(s) are falsified on its "
                        f"declared met state")
                else:
                    self.notes.append(
                        f"note: {where} control run fails ({last[:80]}); the criterion is "
                        f"not met yet, so its {len(clauses)} clause(s) are deferred")
        pinned = self.evidence(where, entry)
        reinterpretations = 0
        for position, clause in enumerate(clauses):
            if self.clause(where, criterion, note, position, clause, check, control_ok, base):
                reinterpretations += 1
        if isinstance(note, str) and reinterpretations == 0:
            self.fail(f"{where} carries a 'criterion_note' and no clause says what it "
                      f"is checked as. A note records a repointing, and a repointing "
                      f"is a clause somebody has to falsify")
        substituted = {
            clause["falsifiers"][position]["file"]
            for clause in clauses
            if isinstance(clause, dict) and isinstance(clause.get("falsifiers"), list)
            for position in range(len(clause["falsifiers"]))
            if isinstance(clause["falsifiers"][position], dict)
            and isinstance(clause["falsifiers"][position].get("file"), str)
            and clause["falsifiers"][position].get("absent") is not True
        } | {s["file"] for s in (entry.get("met_state")
                                 if isinstance(entry.get("met_state"), list) else [])
             if isinstance(s, dict) and isinstance(s.get("file"), str)}
        for relative in sorted(substituted | set(opened_files(code))):
            if relative not in pinned:
                self.fail(
                    f"{where} reads or mutates {relative} and pins no shape for it. "
                    f"Add an 'evidence_shape' entry naming the rows this check reads, "
                    f"so a row that gains or loses a clause fails this gate")
        record["clauses"] = [
            {"promise": clause.get("promise"), "checked_as": clause.get("checked_as"),
             "falsifiers": clause.get("falsifiers")}
            for clause in clauses if isinstance(clause, dict)]
        record["evidence_shape"] = entry.get("evidence_shape", [])
        if entry.get("met_state") is not None:
            record["met_state"] = entry.get("met_state")
        if isinstance(note, str):
            record["criterion_note"] = note
        return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--criteria", type=Path, default=None,
                        help=f"criteria path (default: <root>/{CRITERIA})")
    parser.add_argument("--static-only", action="store_true",
                        help="audit the declarations without running any check "
                             "(for iterating; the push runs the falsifiers)")
    args = parser.parse_args()

    root = project_root(args.root)
    path = args.criteria if args.criteria is not None else root / CRITERIA
    if not path.is_file():
        print(f"criterion clauses FAIL: no criteria file at {path}", file=sys.stderr)
        return 1
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"criterion clauses FAIL: cannot read {path}: {error}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print(f"criterion clauses FAIL: {path} top level is not an object", file=sys.stderr)
        return 1

    if args.static_only:
        # Surfaced by run_all_gates.py's degraded-pass filter, so a suite that
        # ever adopted this flag would say so in its own summary.
        print("  note: --static-only: the declarations were audited and no "
              "falsifier was executed")

    with tempfile.TemporaryDirectory(prefix="criterion-clauses-") as scratch:
        auditor = Auditor(root, Path(scratch), run=not args.static_only)
        pairs: dict[str, dict[str, dict]] = {}
        for key, value in sorted(document.items()):
            if key == "schema" or not GATE_KEY.match(key):
                continue
            if not isinstance(value, list):
                continue
            for index, entry in enumerate(value):
                if not isinstance(entry, dict):
                    continue
                if not isinstance(entry.get("criterion"), str) or \
                        not isinstance(entry.get("check"), str):
                    continue
                record = auditor.criterion(key, index, entry)
                pairs.setdefault(key, {})[entry["criterion"]] = record

        for line in auditor.notes:
            print(f"  {line}")
        for line in auditor.problems:
            print(f"  {line}")
        marker = hashlib.sha256(
            json.dumps(pairs, sort_keys=True).encode("utf-8")).hexdigest()
        if auditor.problems:
            sys.stdout.flush()
            print(f"criterion clauses FAIL: {len(auditor.problems)} problem(s) in "
                  f"{path.name}", file=sys.stderr)
            return 1
        deferred = f"; {auditor.deferred} deferred (criterion not met)" \
            if auditor.deferred else ""
        if auditor.on_met_state:
            deferred += f"; {auditor.on_met_state} of the executed on a declared met state"
        skipped = " (static only: no check was run)" if args.static_only else ""
        print(f"criterion clauses OK: {auditor.declared} clause(s) over "
              f"{auditor.falsified} executed falsification(s), "
              f"{auditor.delegated} criteria delegated to suite scripts"
              f"{deferred}{skipped}")
        print(f"marker_sha256={marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
