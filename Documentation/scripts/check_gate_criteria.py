#!/usr/bin/env python3
"""A gate criterion may print what it computed, and nothing it was told to say.

The defect this exists for
--------------------------

`Documentation/gate-criteria.json` held, at HEAD, an M3 criterion whose check
was

    print('M3', m.group(1)+'/'+m.group(2), '- no ceiling is established: the
    2026-09-20 fold drove 99 percent of registered rows after the reachability
    claim was falsified, and the remaining fixtures are unsurveyed, not
    unreachable')

The first half is measured. The second half is a sentence somebody typed on
2026-09-20, when the fold had driven 18 of 54 fixtures, and it printed
unconditionally. Four commits later the same line read `M3 54/54` -- nothing
remaining -- with the stale sentence still after it, and `gate_report.py`
quotes a check's last output line verbatim, so the generated G2 report
asserted both at once. A hand-typed conclusion in a criterion's output is a
number in a durable record that was not copied from `METRICS.md` (AGENTS.md
rule 7, "generated over typed"), smuggled in through the one file nothing
was checking.

The rule
--------

Every string constant a criterion's inline check prints must be a *label*:
it names what follows, it is not a finding. Mechanically, a printed literal

  * carries at most MAX_LABEL_WORDS words, and
  * contains no run of two or more digits, no `%`, and no `.` or `,`
    followed by a space.

A measurement, a date, a percentage or a sentence therefore cannot be typed
into a check; it has to come from `m.group(...)`, a computed variable or an
f-string over one. Labels such as `'M1 fixture-covered'`, `'s1 receipts
tracked:'` and `'lacking not_fixture_tested reason:'` pass unchanged.

There is no per-criterion escape hatch, because the defect was exactly a
criterion that looked like it had one.

Also checked, because this is the only gate that reads the file at all:

  * the document parses, is an object, and every key is `G<n>`;
  * every entry is an object with non-empty `criterion` and `check` strings;
  * every check names `{python}` (gate_report.py substitutes the interpreter
    there; a check that hard-codes `python3` runs under the wrong one);
  * an inline check (`{python} -c "<code>"`) parses as Python, so a criterion
    cannot fail at gate time for a syntax error;
  * a script check (`{python} <path>`) names a file that exists.

Output ends with `marker_sha256=` over the sorted `gate -> criterion ->
check` map, so a change to what any gate demands is visible in the gate
summary rather than only in a diff.

The project root is `git rev-parse --show-toplevel` from the current
directory, or `--root PATH`.

Usage: .venv/bin/python Documentation/scripts/check_gate_criteria.py
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path

from gitlog import project_root

CRITERIA = "Documentation/gate-criteria.json"
GATE_KEY = re.compile(r"^G\d+$")
MAX_LABEL_WORDS = 6
# A printed literal that carries a measurement rather than naming one.
DIGIT_RUN = re.compile(r"\d{2,}")
SENTENCE = re.compile(r"[.,] ")


def prose_reason(text: str) -> str | None:
    """Why this printed literal is a finding rather than a label, or None."""
    words = text.split()
    if len(words) > MAX_LABEL_WORDS:
        return f"{len(words)} words (a label carries at most {MAX_LABEL_WORDS})"
    if DIGIT_RUN.search(text):
        return "a run of two or more digits: a measurement, not a label"
    if "%" in text:
        return "a percent sign: a measurement, not a label"
    if SENTENCE.search(text):
        return "sentence punctuation: a finding, not a label"
    return None


def printed_literals(tree: ast.AST) -> list[str]:
    """Every string constant that reaches a `print(...)` call.

    Reached directly as an argument, through `+` concatenation, through a
    conditional expression, through `str.join`'s separator or iterable, and
    through an f-string's literal parts -- every shape the criteria in this
    repository actually use, so a sentence cannot be hidden one indirection
    deep inside the call.
    """
    found: list[str] = []

    def walk(node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                found.append(node.value)
            return
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    found.append(part.value)
            return
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            walk(node.left)
            walk(node.right)
            return
        if isinstance(node, ast.IfExp):
            walk(node.body)
            walk(node.orelse)
            return
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for element in node.elts:
                walk(element)
            return
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr == "join":
                walk(function.value)
                for argument in node.args:
                    walk(argument)
            return
        # Anything else (m.group(1), a name, a comprehension) is computed.

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        for argument in node.args:
            walk(argument)
    return found


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


def check_entry(root: Path, gate: str, index: int, entry: object,
                problems: list[str]) -> tuple[str, str] | None:
    """Validate one criterion; append problems; return (criterion, check)."""
    where = f"{gate}[{index}]"
    if not isinstance(entry, dict):
        problems.append(f"{where} is not an object")
        return None
    criterion = entry.get("criterion")
    check = entry.get("check")
    if not isinstance(criterion, str) or not criterion.strip():
        problems.append(f"{where} has no non-empty 'criterion' string")
        return None
    if not isinstance(check, str) or not check.strip():
        problems.append(f"{where} ({criterion}) has no non-empty 'check' string")
        return None
    if "{python}" not in check:
        problems.append(f"{where} ({criterion}) does not name {{python}}: "
                        f"gate_report.py substitutes the interpreter there")
    code = inline_code(check)
    if code is None:
        relative = script_path(check)
        if relative is not None and not (root / relative).is_file():
            problems.append(f"{where} ({criterion}) runs {relative}, which does not exist")
        return criterion, check
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        problems.append(f"{where} ({criterion}) inline check does not parse: {error}")
        return criterion, check
    for literal in printed_literals(tree):
        reason = prose_reason(literal)
        if reason is not None:
            problems.append(
                f"{where} ({criterion}) prints a literal that is not computed from its "
                f"inputs -- {reason}: {literal.strip()[:120]!r}")
    return criterion, check


def marker(pairs: dict[str, dict[str, str]]) -> str:
    return hashlib.sha256(
        json.dumps(pairs, sort_keys=True).encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None,
                        help="project root (default: git toplevel of cwd)")
    parser.add_argument("--criteria", type=Path, default=None,
                        help=f"criteria path (default: <root>/{CRITERIA})")
    args = parser.parse_args()

    root = project_root(args.root)
    path = args.criteria if args.criteria is not None else root / CRITERIA
    if not path.is_file():
        print(f"gate criteria FAIL: no criteria file at {path}", file=sys.stderr)
        return 1
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"gate criteria FAIL: cannot read {path}: {error}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print(f"gate criteria FAIL: {path} top level is not an object", file=sys.stderr)
        return 1

    problems: list[str] = []
    pairs: dict[str, dict[str, str]] = {}
    gates = 0
    criteria = 0
    for key, value in sorted(document.items()):
        if key == "schema":
            continue
        if not GATE_KEY.match(key):
            problems.append(f"key {key!r} is neither 'schema' nor a gate name like 'G2'")
            continue
        gates += 1
        if not isinstance(value, list) or not value:
            problems.append(f"{key} is not a non-empty array of criteria")
            continue
        for index, entry in enumerate(value):
            result = check_entry(root, key, index, entry, problems)
            if result is not None:
                pairs.setdefault(key, {})[result[0]] = result[1]
                criteria += 1

    for line in problems:
        print(f"  {line}")
    if problems:
        sys.stdout.flush()
        print(f"gate criteria FAIL: {len(problems)} problem(s) in "
              f"{path.name}", file=sys.stderr)
        return 1
    print(f"gate criteria OK: {criteria} criteria over {gates} gates, every printed literal "
          f"is a label of at most {MAX_LABEL_WORDS} words")
    print(f"marker_sha256={marker(pairs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
