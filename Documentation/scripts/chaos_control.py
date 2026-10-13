#!/usr/bin/env python3
"""Chaos Control: audit and plan SA2 fidelity evidence dependencies.

The ledger is deliberately explicit. It does not infer authority from filenames,
rewrite receipts, or claim that unregistered evidence is covered.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from typing import Iterable


LEDGER_SCHEMA = "sa2-fidelity-ledger/v1"
GRAPH_SCHEMA = "sa2-fidelity-graph/v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
COMMAND_OPTION = re.compile(r"--?[A-Za-z0-9][A-Za-z0-9-]*\Z")
COMMAND_VARIABLE = re.compile(r"\$\{[A-Z][A-Z0-9_]*\}\Z")
AUTHORITY = {
    "statically-translated",
    "directly-traced",
    "community-guided",
    "shipped-data-derived",
    "observed",
    "integration-choice",
    "unknown",
}
@dataclass(frozen=True)
class FilePin:
    path: str
    sha256: str


@dataclass(frozen=True)
class Claim:
    id: str
    summary: str
    authority: str
    depends_on: tuple[str, ...]
    sources: tuple[FilePin, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class Marker:
    kind: str
    value: str


@dataclass(frozen=True)
class Suite:
    id: str
    summary: str
    claims: tuple[str, ...]
    depends_on: tuple[str, ...]
    checker: str
    files: tuple[FilePin, ...]
    command: tuple[str, ...]
    marker: Marker


@dataclass(frozen=True)
class Ledger:
    claims: tuple[Claim, ...]
    suites: tuple[Suite, ...]
    coverage_patterns: tuple[str, ...]


class LedgerInvalid(Exception):
    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(sorted(set(errors)))
        super().__init__("; ".join(self.errors))


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def git_root(start: Path) -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=start,
        text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise LedgerInvalid(["repository is not a Git worktree"])
    return Path(result.stdout.strip()).resolve()


def git_tracked_paths(repository: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=repository, capture_output=True,
    )
    if result.returncode != 0:
        raise LedgerInvalid(["git tracked-file query failed"])
    status = subprocess.run(
        [
            "git", "status", "--porcelain=v2", "-z",
            "--untracked-files=no",
        ],
        cwd=repository,
        capture_output=True,
    )
    if status.returncode != 0:
        raise LedgerInvalid(["git intent-to-add query failed"])
    try:
        tracked = {
            item.decode("utf-8")
            for item in result.stdout.split(b"\0")
            if item
        }
        for raw_record in status.stdout.split(b"\0"):
            if not raw_record.startswith(b"1 "):
                continue
            fields = raw_record.decode("utf-8").split(" ", 8)
            if len(fields) == 9 and fields[1] == ".A":
                tracked.discard(fields[8])
        return tracked
    except UnicodeDecodeError:
        raise LedgerInvalid(["Git-tracked path is not valid UTF-8"])


def valid_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or \
            "\0" in value or "\n" in value or "\r" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts and \
        "." not in path.parts


def absolute_without_symlink_resolution(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def registered_path_uses_symlink(repository: Path, relative: str) -> bool:
    current = repository
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def resolve_repository_file(repository: Path, relative: str) -> Path | None:
    if not valid_relative_path(relative):
        return None
    candidate = (repository / relative).resolve()
    try:
        candidate.relative_to(repository)
    except ValueError:
        return None
    return candidate


def read_file_pin(raw: object, owner: str, errors: list[str]) -> FilePin | None:
    if not isinstance(raw, dict):
        errors.append(f"{owner} file pin is not an object")
        return None
    path = raw.get("path")
    expected = raw.get("sha256")
    if not valid_relative_path(path):
        errors.append(f"{owner} has unsafe file path: {path!r}")
        return None
    if not isinstance(expected, str) or SHA256.fullmatch(expected) is None:
        errors.append(f"{owner} malformed sha256 for {path}")
        expected = ""
    return FilePin(path, expected)


def string_tuple(raw: object, owner: str, field: str,
                 errors: list[str], identifiers: bool = True) -> tuple[str, ...]:
    if not isinstance(raw, list):
        errors.append(f"{owner} {field} must be an array")
        return ()
    values: list[str] = []
    for value in raw:
        valid = isinstance(value, str) and bool(value)
        if identifiers:
            valid = valid and IDENTIFIER.fullmatch(value) is not None
        if not valid:
            errors.append(f"{owner} {field} has invalid value: {value!r}")
        else:
            values.append(value)
    if len(values) != len(set(values)):
        errors.append(f"{owner} {field} contains duplicates")
    return tuple(values)


def parse_claim(raw: object, index: int, errors: list[str]) -> Claim | None:
    owner = f"claim[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{owner} is not an object")
        return None
    identifier = raw.get("id")
    if not isinstance(identifier, str) or IDENTIFIER.fullmatch(identifier) is None:
        errors.append(f"{owner} has invalid id: {identifier!r}")
        return None
    owner = f"claim {identifier}"
    summary = raw.get("summary")
    authority = raw.get("authority")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{owner} has empty summary")
        summary = ""
    if authority not in AUTHORITY:
        errors.append(f"{owner} has invalid authority: {authority!r}")
        authority = "unknown"
    dependencies = string_tuple(
        raw.get("depends_on"), owner, "depends_on", errors)
    source_raw = raw.get("sources")
    if not isinstance(source_raw, list) or not source_raw:
        errors.append(f"{owner} sources must be a non-empty array")
        source_raw = []
    sources = tuple(
        pin for pin in
        (read_file_pin(value, owner, errors) for value in source_raw)
        if pin is not None
    )
    outputs = string_tuple(
        raw.get("outputs"), owner, "outputs", errors, identifiers=False)
    for output in outputs:
        if not valid_relative_path(output):
            errors.append(f"{owner} has unsafe output path: {output!r}")
    return Claim(identifier, summary, authority, dependencies, sources, outputs)


def parse_suite(raw: object, index: int, errors: list[str]) -> Suite | None:
    owner = f"suite[{index}]"
    if not isinstance(raw, dict):
        errors.append(f"{owner} is not an object")
        return None
    identifier = raw.get("id")
    if not isinstance(identifier, str) or IDENTIFIER.fullmatch(identifier) is None:
        errors.append(f"{owner} has invalid id: {identifier!r}")
        return None
    owner = f"suite {identifier}"
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        errors.append(f"{owner} has empty summary")
        summary = ""
    claims = string_tuple(raw.get("claims"), owner, "claims", errors)
    dependencies = string_tuple(
        raw.get("depends_on"), owner, "depends_on", errors)
    checker = raw.get("checker")
    if isinstance(checker, str) and checker.startswith("-"):
        errors.append(
            f"{owner} checker path must not start with '-': {checker}")
        checker = ""
    elif not valid_relative_path(checker):
        errors.append(f"{owner} has unsafe checker path: {checker!r}")
        checker = ""
    files_raw = raw.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        errors.append(f"{owner} files must be a non-empty array")
        files_raw = []
    files = tuple(
        pin for pin in
        (read_file_pin(value, owner, errors) for value in files_raw)
        if pin is not None
    )
    command = string_tuple(
        raw.get("command"), owner, "command", errors, identifiers=False)
    if not command:
        errors.append(f"{owner} command must be non-empty")
    elif any("\n" in word or "\0" in word for word in command):
        errors.append(f"{owner} command contains a control character")
    marker_raw = raw.get("marker")
    if not isinstance(marker_raw, dict):
        errors.append(f"{owner} marker is not an object")
        marker = Marker("text", "")
    else:
        kind = marker_raw.get("kind")
        value = marker_raw.get("value")
        if kind not in {"text", "sha256"}:
            errors.append(f"{owner} has invalid marker kind: {kind!r}")
            kind = "text"
        if not isinstance(value, str):
            errors.append(f"{owner} marker value is not text")
            value = ""
        if kind == "sha256" and SHA256.fullmatch(value) is None:
            errors.append(f"{owner} malformed sha256 marker")
        if kind == "text" and (not value or "\n" in value or "\0" in value):
            errors.append(f"{owner} malformed text marker")
        marker = Marker(kind, value)
    return Suite(identifier, summary, claims, dependencies, checker, files,
                 command, marker)


def find_cycle(graph: dict[str, tuple[str, ...]]) -> tuple[str, ...] | None:
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(node: str) -> tuple[str, ...] | None:
        if node in active_set:
            start = active.index(node)
            return tuple(active[start:] + [node])
        if node in visited:
            return None
        active.append(node)
        active_set.add(node)
        for dependency in sorted(graph.get(node, ())):
            if dependency not in graph:
                continue
            cycle = visit(dependency)
            if cycle is not None:
                return cycle
        active.pop()
        active_set.remove(node)
        visited.add(node)
        return None

    for node in sorted(graph):
        cycle = visit(node)
        if cycle is not None:
            return cycle
    return None


def transitive_dependencies(graph: dict[str, tuple[str, ...]],
                            start: str) -> set[str]:
    found: set[str] = set()
    pending = list(graph.get(start, ()))
    while pending:
        node = pending.pop()
        if node in found or node not in graph:
            continue
        found.add(node)
        pending.extend(graph[node])
    return found


def transitive_dependents(graph: dict[str, tuple[str, ...]],
                          initial: set[str]) -> set[str]:
    reverse = {node: set() for node in graph}
    for node, dependencies in graph.items():
        for dependency in dependencies:
            if dependency in reverse:
                reverse[dependency].add(node)
    found = set(initial)
    pending = list(initial)
    while pending:
        node = pending.pop()
        for dependent in reverse.get(node, ()):
            if dependent not in found:
                found.add(dependent)
                pending.append(dependent)
    return found


def load_ledger(repository: Path, ledger_path: Path,
                stale_hash_paths: set[str] | None = None) -> Ledger:
    ledger_path = absolute_without_symlink_resolution(ledger_path)
    try:
        ledger_relative = ledger_path.relative_to(repository).as_posix()
    except ValueError:
        raise LedgerInvalid([
            "ledger is outside repository: "
            + json.dumps(str(ledger_path), ensure_ascii=True)
        ])
    if registered_path_uses_symlink(repository, ledger_relative):
        raise LedgerInvalid([f"ledger path uses a symlink: {ledger_relative}"])
    tracked = git_tracked_paths(repository)
    if ledger_relative not in tracked:
        raise LedgerInvalid([f"ledger is not Git-tracked: {ledger_relative}"])

    errors: list[str] = []
    try:
        payload = json.loads(ledger_path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise LedgerInvalid([f"cannot read ledger: {error}"])
    if not isinstance(payload, dict):
        raise LedgerInvalid(["ledger root is not an object"])
    if payload.get("schema") != LEDGER_SCHEMA:
        errors.append(f"ledger schema must be {LEDGER_SCHEMA}")
    claims_raw = payload.get("claims")
    suites_raw = payload.get("suites")
    if not isinstance(claims_raw, list):
        errors.append("claims must be an array")
        claims_raw = []
    if not isinstance(suites_raw, list):
        errors.append("suites must be an array")
        suites_raw = []
    claims = tuple(
        claim for claim in
        (parse_claim(value, index, errors)
         for index, value in enumerate(claims_raw))
        if claim is not None
    )
    suites = tuple(
        suite for suite in
        (parse_suite(value, index, errors)
         for index, value in enumerate(suites_raw))
        if suite is not None
    )
    all_ids = [claim.id for claim in claims] + [suite.id for suite in suites]
    for identifier in sorted(set(all_ids)):
        if all_ids.count(identifier) > 1:
            errors.append(f"duplicate id: {identifier}")

    claim_by_id = {claim.id: claim for claim in claims}
    suite_by_id = {suite.id: suite for suite in suites}
    verified_claims = {
        claim_id for suite in suites for claim_id in suite.claims
    }
    for claim in claims:
        if claim.id not in verified_claims:
            errors.append(
                f"claim has no direct verification suite: {claim.id}")
        for dependency in claim.depends_on:
            if dependency not in claim_by_id:
                errors.append(
                    f"missing claim dependency: {claim.id} -> {dependency}")
    for suite in suites:
        for claim_id in suite.claims:
            if claim_id not in claim_by_id:
                errors.append(f"missing suite claim: {suite.id} -> {claim_id}")
        for dependency in suite.depends_on:
            if dependency not in suite_by_id:
                errors.append(
                    f"missing suite dependency: {suite.id} -> {dependency}")
        checker_pins = [
            pin for pin in suite.files if pin.path == suite.checker
        ]
        if suite.checker and len(checker_pins) != 1:
            errors.append(
                f"suite {suite.id} checker must have exactly one pin: "
                f"{suite.checker}")
        if suite.checker and suite.checker not in tracked:
            errors.append(
                f"suite {suite.id} checker is not Git-tracked: "
                f"{suite.checker}")
        if not suite.command or suite.command[0] != "python3":
            errors.append(
                f"suite {suite.id} command must start with python3")
        if len(suite.command) < 2:
            errors.append(
                f"suite {suite.id} command has no checker argument")
        else:
            command_checker = suite.command[1]
            if command_checker.startswith("-"):
                errors.append(
                    f"suite {suite.id} command checker must not start with "
                    f"'-': {command_checker}")
            if command_checker in {"-c", "-m"}:
                errors.append(
                    f"suite {suite.id} command uses forbidden interpreter "
                    f"mode: {command_checker}")
            if not valid_relative_path(command_checker):
                errors.append(
                    f"suite {suite.id} has unsafe command checker path: "
                    f"{command_checker}")
            if command_checker != suite.checker:
                errors.append(
                    f"suite {suite.id} command does not invoke declared "
                    f"checker: {suite.checker}")
        if suite.checker and suite.command.count(suite.checker) != 1:
            errors.append(
                f"suite {suite.id} command must invoke declared checker "
                f"exactly once: {suite.checker}")
        for argument in suite.command[2:]:
            if COMMAND_OPTION.fullmatch(argument) is None and \
                    COMMAND_VARIABLE.fullmatch(argument) is None:
                errors.append(
                    f"suite {suite.id} has unsupported command argument: "
                    f"{argument}")

    claim_graph = {claim.id: claim.depends_on for claim in claims}
    suite_graph = {suite.id: suite.depends_on for suite in suites}
    claim_cycle = find_cycle(claim_graph)
    suite_cycle = find_cycle(suite_graph)
    if claim_cycle:
        errors.append("claim dependency cycle: " + " -> ".join(claim_cycle))
    if suite_cycle:
        errors.append("suite dependency cycle: " + " -> ".join(suite_cycle))

    output_owner: dict[str, str] = {}
    for claim in claims:
        for output in claim.outputs:
            if output in output_owner:
                errors.append(
                    f"duplicate claim output: {output_owner[output]} and "
                    f"{claim.id} own {output}")
            else:
                output_owner[output] = claim.id
    for claim in claims:
        dependencies = transitive_dependencies(claim_graph, claim.id)
        for source in claim.sources:
            producer = output_owner.get(source.path)
            if producer == claim.id:
                errors.append(
                    f"circular claim provenance: {claim.id} sources its own "
                    f"output {source.path}")
            elif producer is not None and claim.id in transitive_dependencies(
                    claim_graph, producer):
                errors.append(
                    f"circular claim provenance: {claim.id} sources "
                    f"{source.path} from descendant {producer}")
            elif producer is not None and producer not in dependencies:
                errors.append(
                    f"undeclared claim provenance: {claim.id} sources "
                    f"{source.path} from {producer}")

    pins = [(f"claim {claim.id}", pin) for claim in claims
            for pin in claim.sources]
    pins.extend((f"suite {suite.id}", pin) for suite in suites
                for pin in suite.files)
    tolerated = stale_hash_paths or set()
    for owner, pin in pins:
        if pin.path not in tracked:
            errors.append(f"{owner} file is not Git-tracked: {pin.path}")
        if registered_path_uses_symlink(repository, pin.path):
            errors.append(
                f"{owner} registered path uses a symlink: {pin.path}")
            continue
        path = resolve_repository_file(repository, pin.path)
        if path is None or not path.is_file():
            errors.append(f"{owner} missing file: {pin.path}")
        elif pin.path not in tolerated and \
                SHA256.fullmatch(pin.sha256) is not None:
            actual = sha256(path)
            if actual != pin.sha256:
                errors.append(
                    f"{owner} stale sha256 for {pin.path}: "
                    f"{actual} != {pin.sha256}")
    for claim in claims:
        for output in claim.outputs:
            if output not in tracked:
                errors.append(
                    f"claim {claim.id} output is not Git-tracked: {output}")
            if registered_path_uses_symlink(repository, output):
                errors.append(
                    f"claim {claim.id} registered path uses a symlink: "
                    f"{output}")
                continue
            path = resolve_repository_file(repository, output)
            if path is None or not path.is_file():
                errors.append(f"claim {claim.id} missing file: {output}")

    coverage_raw = payload.get("coverage")
    patterns: tuple[str, ...] = ()
    if not isinstance(coverage_raw, dict):
        errors.append("coverage must be an object")
    else:
        patterns = string_tuple(
            coverage_raw.get("patterns"), "coverage", "patterns", errors,
            identifiers=False,
        )
        for pattern in patterns:
            if not valid_relative_path(pattern) or not any(
                    character in pattern for character in "*?["):
                errors.append(f"coverage has invalid glob pattern: {pattern!r}")

    if errors:
        raise LedgerInvalid(errors)
    return Ledger(
        tuple(sorted(claims, key=lambda item: item.id)),
        tuple(sorted(suites, key=lambda item: item.id)),
        tuple(sorted(patterns)),
    )


def registered_paths(ledger: Ledger) -> set[str]:
    paths = {pin.path for claim in ledger.claims for pin in claim.sources}
    paths.update(output for claim in ledger.claims for output in claim.outputs)
    paths.update(pin.path for suite in ledger.suites for pin in suite.files)
    return paths


def control_paths(repository: Path, ledger_path: Path) -> set[str]:
    paths: set[str] = set()
    for candidate in (ledger_path.resolve(), Path(__file__).resolve()):
        try:
            paths.add(candidate.relative_to(repository).as_posix())
        except ValueError:
            pass
    return paths


# Receipts imported from the predecessor and receipts this project writes.
# The predecessor's historical pins name commits of the predecessor's history,
# which this repository does not carry; those pins are counted, not checked.
# A receipt this project writes names commits of this repository, so a
# historical pin it declares must be checkable here.
PREDECESSOR_RECEIPT_DIRECTORY = "Documentation/native-evidence-receipts"
RECEIPT_DIRECTORY = "Documentation/receipts"
COMMIT = re.compile(r"[0-9a-f]{7,40}")


def receipt_pin_nodes(payload: object, trail: tuple[str, ...] = ()):
    """Every {file, sha256} node in a receipt, with the path that reached it."""
    if isinstance(payload, dict):
        if isinstance(payload.get("file"), str) and \
                isinstance(payload.get("sha256"), str):
            yield ".".join(trail) or "<root>", payload
        for key, value in payload.items():
            yield from receipt_pin_nodes(value, trail + (str(key),))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            yield from receipt_pin_nodes(value, trail + (str(index),))


def commit_known(repository: Path, commit: str) -> bool:
    """Whether the commit is in this repository's object store."""
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=repository, capture_output=True)
    return completed.returncode == 0


def blob_sha256(repository: Path, commit: str, relative: str) -> str | None:
    """The sha256 the file had at a commit, or None if it was not there."""
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative}"],
        cwd=repository, capture_output=True)
    if completed.returncode != 0:
        return None
    return hashlib.sha256(completed.stdout).hexdigest()


def receipt_pins(repository: Path, tracked: set[str], directory_relative: str,
                 tolerate_foreign_commits: bool) -> tuple[list[str], int]:
    """Check every receipt pin that names a Git-tracked file.

    A receipt states what a milestone executed. Nothing validated those
    statements, so an in-place edit by a later milestone silently invalidated
    every predecessor's receipt: 76 pins were stale when this check was written,
    one file accounting for 18 of them.

    A pin may instead be declared historical, naming the commit it was taken
    at. That is how supersession is expressed, and it is checked rather than
    trusted: the file must have hashed to the pinned value at that commit.

    A historical pin naming a commit this repository does not have is foreign
    history. With ``tolerate_foreign_commits`` it is counted and skipped (the
    imported predecessor receipts); without it the pin is an error (this
    project's own receipts). Returns the errors and the foreign-pin count.
    """
    errors: list[str] = []
    foreign = 0
    directory = repository / directory_relative
    if not directory.is_dir():
        return errors, foreign
    for receipt in sorted(directory.glob("*.json")):
        name = receipt.relative_to(repository).as_posix()
        try:
            payload = json.loads(receipt.read_text())
        except (OSError, json.JSONDecodeError) as error:
            errors.append(f"receipt {name} cannot be read: {error}")
            continue
        for trail, node in receipt_pin_nodes(payload):
            relative = node["file"]
            # Only pins that name a file in this repository are checkable.
            # Private captures, build outputs and evidence-packet paths are
            # deliberately outside it and stay uncheckable here.
            if relative not in tracked:
                continue
            if SHA256.fullmatch(node["sha256"]) is None:
                errors.append(
                    f"receipt {name} pin {trail} has a malformed sha256")
                continue
            historical = node.get("historical")
            if historical is not None:
                if not isinstance(historical, dict):
                    errors.append(
                        f"receipt {name} pin {trail} historical must be an "
                        "object")
                    continue
                commit = historical.get("valid_at")
                if not isinstance(commit, str) or \
                        COMMIT.fullmatch(commit) is None:
                    errors.append(
                        f"receipt {name} pin {trail} historical needs a "
                        "valid_at commit")
                    continue
                if not isinstance(historical.get("note"), str):
                    errors.append(
                        f"receipt {name} pin {trail} historical needs a note "
                        "saying what superseded it")
                    continue
                if not commit_known(repository, commit):
                    if tolerate_foreign_commits:
                        foreign += 1
                        continue
                    errors.append(
                        f"receipt {name} pin {trail} names commit {commit}, "
                        "which this repository does not have")
                    continue
                was = blob_sha256(repository, commit, relative)
                if was is None:
                    errors.append(
                        f"receipt {name} pin {trail} names commit {commit}, "
                        f"where {relative} does not exist")
                elif was != node["sha256"]:
                    errors.append(
                        f"receipt {name} pin {trail} claims {relative} hashed "
                        f"to {node['sha256']} at {commit}, but it hashed to "
                        f"{was}")
                continue
            if registered_path_uses_symlink(repository, relative):
                errors.append(
                    f"receipt {name} pin {trail} uses a symlink: {relative}")
                continue
            path = resolve_repository_file(repository, relative)
            if path is None or not path.is_file():
                errors.append(
                    f"receipt {name} pin {trail} missing file: {relative}")
                continue
            actual = sha256(path)
            if actual != node["sha256"]:
                errors.append(
                    f"receipt {name} pin {trail} stale sha256 for {relative}: "
                    f"{actual} != {node['sha256']}")
            size = node.get("size")
            if isinstance(size, int) and size != path.stat().st_size:
                errors.append(
                    f"receipt {name} pin {trail} stale size for {relative}: "
                    f"{path.stat().st_size} != {size}")
    return errors, foreign


def coverage_debt(repository: Path, ledger: Ledger) -> dict[str, list[str]]:
    registered = registered_paths(ledger)
    tracked = git_tracked_paths(repository)
    debt: dict[str, list[str]] = {}
    for pattern in ledger.coverage_patterns:
        matches = sorted(
            path for path in tracked
            if PurePosixPath(path).full_match(pattern)
        )
        debt[pattern] = [path for path in matches if path not in registered]
    return debt


def audit(repository: Path, ledger: Ledger) -> None:
    claim_edges = sum(len(claim.depends_on) for claim in ledger.claims)
    suite_edges = sum(len(suite.depends_on) + len(suite.claims)
                      for suite in ledger.suites)
    debt = coverage_debt(repository, ledger)
    tracked = git_tracked_paths(repository)
    receipt_errors, foreign = receipt_pins(
        repository, tracked, PREDECESSOR_RECEIPT_DIRECTORY,
        tolerate_foreign_commits=True)
    own_errors, _ = receipt_pins(
        repository, tracked, RECEIPT_DIRECTORY, tolerate_foreign_commits=False)
    receipt_errors.extend(own_errors)
    if receipt_errors:
        raise LedgerInvalid(receipt_errors)
    print(
        f"CHAOS_CONTROL_AUDIT_OK claims={len(ledger.claims)} "
        f"suites={len(ledger.suites)} edges={claim_edges + suite_edges} "
        f"coverage_debt={sum(len(paths) for paths in debt.values())} "
        f"foreign_historical_pins={foreign}"
    )
    for pattern, paths in sorted(debt.items()):
        print(
            "CHAOS_CONTROL_COVERAGE_DEBT pattern="
            f"{json.dumps(pattern, ensure_ascii=True)} count={len(paths)}"
        )


def git_changed_paths(repository: Path, revision: str) -> list[str]:
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", revision + "^{commit}"],
        cwd=repository, text=True, capture_output=True,
    )
    if verify.returncode != 0:
        raise LedgerInvalid([f"unknown Git revision: {revision}"])
    changed = subprocess.run(
        ["git", "diff", "--name-only", "-z", revision, "--"],
        cwd=repository, capture_output=True,
    )
    if changed.returncode != 0:
        raise LedgerInvalid(["git diff failed"])
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repository, capture_output=True,
    )
    if untracked.returncode != 0:
        raise LedgerInvalid(["git untracked-file query failed"])
    try:
        paths = set(
            item.decode("utf-8")
            for item in changed.stdout.split(b"\0") if item
        )
        paths.update(
            item.decode("utf-8")
            for item in untracked.stdout.split(b"\0") if item
        )
    except UnicodeDecodeError:
        raise LedgerInvalid(["Git changed path is not valid UTF-8"])
    return sorted(paths)


def topological_suites(suites: dict[str, Suite], selected: set[str]) -> list[str]:
    ordered: list[str] = []
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visited:
            return
        for dependency in sorted(suites[identifier].depends_on):
            if dependency in selected:
                visit(dependency)
        visited.add(identifier)
        ordered.append(identifier)

    for identifier in sorted(selected):
        visit(identifier)
    return ordered


def plan(repository: Path, ledger_path: Path, ledger: Ledger,
         changed: list[str]) -> None:
    changed_set = set(changed)
    suite_by_id = {suite.id: suite for suite in ledger.suites}
    direct_claims = {
        claim.id for claim in ledger.claims
        if changed_set.intersection(
            [pin.path for pin in claim.sources] + list(claim.outputs))
    }
    claim_graph = {claim.id: claim.depends_on for claim in ledger.claims}
    affected_claims = transitive_dependents(claim_graph, direct_claims)
    selected = {
        suite.id for suite in ledger.suites
        if affected_claims.intersection(suite.claims) or
        changed_set.intersection(pin.path for pin in suite.files)
    }
    controls = control_paths(repository, ledger_path)
    if changed_set.intersection(controls):
        selected.update(suite_by_id)
    pending = list(selected)
    while pending:
        identifier = pending.pop()
        for dependency in suite_by_id[identifier].depends_on:
            if dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    ordered = topological_suites(suite_by_id, selected)
    unregistered = sorted(
        changed_set - registered_paths(ledger) - controls)
    print(
        f"CHAOS_CONTROL_PLAN changed={len(changed)} "
        f"affected_claims={len(affected_claims)} suites={len(ordered)} "
        f"unregistered={len(unregistered)}"
    )
    for identifier in ordered:
        suite = suite_by_id[identifier]
        print(
            f"CHAOS_CONTROL_SUITE id={identifier} "
            f"command={json.dumps(list(suite.command), separators=(',', ':'))}"
        )
    for path in unregistered:
        print(
            "CHAOS_CONTROL_UNREGISTERED_CHANGE path="
            + json.dumps(path, ensure_ascii=True)
        )


def graph_payload(repository: Path, ledger_path: Path,
                  ledger: Ledger) -> dict:
    claims = [
        {
            "id": claim.id,
            "summary": claim.summary,
            "authority": claim.authority,
            "depends_on": list(claim.depends_on),
            "sources": [
                {"path": pin.path, "sha256": pin.sha256}
                for pin in sorted(claim.sources, key=lambda item: item.path)
            ],
            "outputs": sorted(claim.outputs),
        }
        for claim in ledger.claims
    ]
    suites = [
        {
            "id": suite.id,
            "summary": suite.summary,
            "claims": list(suite.claims),
            "depends_on": list(suite.depends_on),
            "checker": suite.checker,
            "files": [
                {"path": pin.path, "sha256": pin.sha256}
                for pin in sorted(suite.files, key=lambda item: item.path)
            ],
            "command": list(suite.command),
            "marker": {
                "kind": suite.marker.kind,
                "value": suite.marker.value,
            },
        }
        for suite in ledger.suites
    ]
    edges = []
    for claim in ledger.claims:
        edges.extend(
            {"from": dependency, "to": claim.id, "kind": "claim"}
            for dependency in claim.depends_on
        )
    for suite in ledger.suites:
        edges.extend(
            {"from": claim, "to": suite.id, "kind": "verification"}
            for claim in suite.claims
        )
        edges.extend(
            {"from": dependency, "to": suite.id, "kind": "suite"}
            for dependency in suite.depends_on
        )
    try:
        ledger_name = ledger_path.relative_to(repository).as_posix()
    except ValueError:
        ledger_name = ledger_path.name
    return {
        "schema": GRAPH_SCHEMA,
        "ledger": ledger_name,
        "control_paths": sorted(control_paths(repository, ledger_path)),
        "claims": claims,
        "suites": suites,
        "edges": sorted(
            edges, key=lambda edge: (edge["kind"], edge["from"], edge["to"])
        ),
        "coverage_debt": coverage_debt(repository, ledger),
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repository", type=Path)
    value.add_argument("--ledger", type=Path)
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("audit")
    plan_parser = commands.add_parser("plan")
    plan_parser.add_argument("--changed-from", required=True)
    graph_parser = commands.add_parser("graph")
    graph_parser.add_argument("--json", required=True, type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    try:
        repository = (args.repository.resolve() if args.repository else
                      git_root(Path(__file__).resolve().parent))
        if not (repository / ".git").exists():
            # Linked worktrees use a .git control file, which exists too.
            raise LedgerInvalid([f"repository is not a Git worktree: {repository}"])
        ledger_path = (args.ledger if args.ledger else
                       repository / "Documentation/fidelity-ledger.json")
        changed = (git_changed_paths(repository, args.changed_from)
                   if args.command == "plan" else [])
        ledger = load_ledger(
            repository, ledger_path,
            stale_hash_paths=set(changed) if args.command == "plan" else None,
        )
        if args.command == "audit":
            audit(repository, ledger)
        elif args.command == "plan":
            plan(repository, ledger_path, ledger, changed)
        else:
            payload = graph_payload(repository, ledger_path, ledger)
            write_json_atomic(args.json, payload)
            print(
                f"CHAOS_CONTROL_GRAPH_OK claims={len(ledger.claims)} "
                f"suites={len(ledger.suites)} output="
                f"{json.dumps(str(args.json.resolve()), ensure_ascii=True)}"
            )
        return 0
    except LedgerInvalid as error:
        for message in error.errors:
            print(f"CHAOS_CONTROL_ERROR {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
