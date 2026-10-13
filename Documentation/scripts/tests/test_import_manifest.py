"""Tests for check_import_manifest.py, the S0 suite checker.

Every test builds two throwaway git repositories under tmp_path: a
predecessor holding the imported files at a pinned commit, and a project
holding the imports, the manifest, adaptations.json and tools/pins.json.
The real checker runs as a subprocess with cwd set to the project; it
locates the project by `git rev-parse --show-toplevel` and the predecessor
by the path the manifest names. Nothing here reads or writes the live tree.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).resolve().parents[1] / "check_import_manifest.py"
MANIFEST = "Documentation/provenance/import-manifest.json"
ADAPTATIONS = "Documentation/provenance/adaptations.json"
PINS = "tools/pins.json"
IMPORTED = {
    "Documentation/scope/SCOPE.md": "scope\n",
    "Documentation/scripts/tool.py": "print('tool')\n",
}
TOOL = "Documentation/scripts/tool.py"

GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
})


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True, env=GIT_ENV,
        capture_output=True, text=True).stdout.strip()


def write(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_json(repo: Path, relative: str, payload: dict) -> None:
    write(repo, relative, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class Project:
    """A throwaway project repository with its throwaway predecessor."""

    def __init__(self, root: Path) -> None:
        self.predecessor = root / "predecessor"
        self.repo = root / "project"
        for repo in (self.predecessor, self.repo):
            repo.mkdir()
            git(repo, "init", "-q")
        for relative, text in IMPORTED.items():
            write(self.predecessor, relative, text)
        self.commit = commit(self.predecessor, "predecessor")
        for relative, text in IMPORTED.items():
            write(self.repo, relative, text)
        self.write_manifest(self.manifest())
        write_json(self.repo, ADAPTATIONS, self.adaptations())
        write_json(self.repo, PINS, {
            "schema": "sa2-recomp-pins/v1",
            "executables": {
                "sonic2app.exe": {"sha256": "a" * 64},
                "sonic2app-unpacked.exe": {"sha256": "b" * 64},
            },
        })
        commit(self.repo, "import")

    def manifest(self, **overrides: dict) -> dict:
        """The manifest of a clean import; `overrides` patch entries by destination."""
        entries = []
        for relative in sorted(IMPORTED):
            text = IMPORTED[relative]
            entry = {
                "class": "tracked", "source": relative, "destination": relative,
                "source_sha256": digest(text), "sha256": digest(text),
                "bytes": len(text.encode("utf-8")),
            }
            entry.update(overrides.get(relative, {}))
            entries.append(entry)
        entries.extend([
            {"class": "private-copy", "source": "/elsewhere/sonic2app.exe",
             "destination": "private/exe/sonic2app.exe",
             "sha256": "a" * 64, "bytes": 1},
            {"class": "private-copy", "source": "/elsewhere/unpacked.exe",
             "destination": "private/exe/sonic2app-unpacked.exe",
             "sha256": "b" * 64, "bytes": 1},
            {"class": "private-link", "source": "/elsewhere/install",
             "destination": "private/install"},
        ])
        return {
            "schema": "sa2-recomp-import-manifest/v2",
            "predecessor": {"path": str(self.predecessor), "commit": self.commit},
            "entries": entries,
        }

    @staticmethod
    def adaptations(**reasons: str) -> dict:
        return {
            "schema": "sa2-recomp-adaptations/v1",
            "adaptations": [{"destination": destination, "reason": reason}
                            for destination, reason in reasons.items()],
        }

    def write_manifest(self, payload: dict) -> None:
        write_json(self.repo, MANIFEST, payload)

    def edit_tool(self, text: str = "print('adapted')\n") -> dict:
        """Edit the imported tool; returns the manifest overrides recording it."""
        write(self.repo, TOOL, text)
        return {TOOL: {"sha256": digest(text), "bytes": len(text), "adapted": True}}

    def run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CHECKER), *arguments], cwd=self.repo,
            env=GIT_ENV, text=True, capture_output=True)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project(tmp_path)


def marker(payload: dict) -> str:
    lines = sorted(
        f"{entry['destination']} {entry['source_sha256']} {entry['sha256']}\n"
        for entry in payload["entries"] if entry["class"] == "tracked")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def failures(result: subprocess.CompletedProcess[str]) -> list[str]:
    assert result.returncode == 1, result.stdout + result.stderr
    assert "import_manifest OK" not in result.stdout
    return [line for line in result.stdout.splitlines()
            if line.startswith("import_manifest FAIL: ")]


def test_clean_import_passes(project: Project) -> None:
    result = project.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "import_manifest adapted=0",
        "import_manifest OK: 2 files, marker_sha256=" + marker(project.manifest()),
    ]


def test_edited_destination_without_adaptations_entry_fails(project: Project) -> None:
    project.write_manifest(project.manifest(**project.edit_tool()))
    commit(project.repo, "adapt tool, re-import")
    assert failures(project.run()) == [
        f"import_manifest FAIL: adapted without a reason in {ADAPTATIONS}: {TOOL}"]


def test_edited_destination_not_flagged_adapted_fails(project: Project) -> None:
    overrides = project.edit_tool()
    del overrides[TOOL]["adapted"]
    project.write_manifest(project.manifest(**overrides))
    write_json(project.repo, ADAPTATIONS, project.adaptations(**{TOOL: "changed"}))
    commit(project.repo, "adapt tool, hide it")
    assert failures(project.run()) == [
        f"import_manifest FAIL: differs from predecessor but not adapted: {TOOL}"]


def test_edited_destination_with_entry_passes_and_is_counted(project: Project) -> None:
    payload = project.manifest(**project.edit_tool())
    project.write_manifest(payload)
    write_json(project.repo, ADAPTATIONS,
               project.adaptations(**{TOOL: "prints a different word"}))
    commit(project.repo, "adapt tool, re-import, explain")
    result = project.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines() == [
        "import_manifest adapted=1",
        "import_manifest OK: 2 files, marker_sha256=" + marker(payload),
    ]
    assert marker(payload) != marker(project.manifest())


def test_adapted_flag_with_equal_hashes_fails(project: Project) -> None:
    project.write_manifest(project.manifest(**{TOOL: {"adapted": True}}))
    write_json(project.repo, ADAPTATIONS, project.adaptations(**{TOOL: "stale"}))
    commit(project.repo, "stale flag")
    assert failures(project.run()) == [
        f"import_manifest FAIL: adapted flag is stale, hashes are equal: {TOOL}",
        f"import_manifest FAIL: {ADAPTATIONS} names {TOOL}, which is not an adapted import",
    ]


def test_adaptations_entry_for_an_unadapted_import_fails(project: Project) -> None:
    write_json(project.repo, ADAPTATIONS, project.adaptations(**{TOOL: "no change"}))
    commit(project.repo, "pre-seeded reason")
    assert failures(project.run()) == [
        f"import_manifest FAIL: {ADAPTATIONS} names {TOOL}, which is not an adapted import"]


def test_missing_predecessor_commit_fails(project: Project) -> None:
    payload = project.manifest()
    payload["predecessor"]["commit"] = "0" * 40
    project.write_manifest(payload)
    commit(project.repo, "unknown predecessor commit")
    assert failures(project.run()) == [
        f"import_manifest FAIL: predecessor {project.predecessor} does not have "
        f"commit {'0' * 40}"]


def test_absent_predecessor_checkout_fails(project: Project) -> None:
    payload = project.manifest()
    payload["predecessor"]["path"] = str(project.predecessor.parent / "gone")
    project.write_manifest(payload)
    commit(project.repo, "predecessor moved")
    assert failures(project.run()) == [
        "import_manifest FAIL: predecessor checkout is absent: "
        + str(project.predecessor.parent / "gone")]


def test_predecessor_blob_not_matching_source_sha256_fails(project: Project) -> None:
    project.write_manifest(project.manifest(**{TOOL: {"source_sha256": "3" * 64}}))
    commit(project.repo, "misrecorded source")
    found = failures(project.run())
    assert found == [
        f"import_manifest FAIL: predecessor blob {project.commit[:12]}:{TOOL} "
        f"does not hash to source_sha256 {'3' * 64}",
        f"import_manifest FAIL: differs from predecessor but not adapted: {TOOL}",
        f"import_manifest FAIL: adapted without a reason in {ADAPTATIONS}: {TOOL}",
    ]


def test_committed_edit_without_reimport_fails(project: Project) -> None:
    write(project.repo, TOOL, "print('edited after import')\n")
    commit(project.repo, "edit without re-import")
    found = failures(project.run())
    assert found[0].startswith(f"import_manifest FAIL: sha256 differs from the manifest: {TOOL}: ")
    assert found[1].startswith(f"import_manifest FAIL: size differs from the manifest: {TOOL}: ")
    assert len(found) == 2


def test_uncommitted_edit_without_reimport_fails_and_warns(project: Project) -> None:
    write(project.repo, TOOL, "print('uncommitted')\n")
    result = project.run()
    assert result.returncode == 1, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"import_manifest WARNING: working tree differs from HEAD: {TOOL}"
    assert lines[1].startswith(f"import_manifest FAIL: sha256 differs from the manifest: {TOOL}: ")


def test_rejects_missing_untracked_and_disagreeing_records(project: Project) -> None:
    git(project.repo, "rm", "-q", "--cached", TOOL)
    (project.repo / "Documentation/scope/SCOPE.md").unlink()
    pins = json.loads((project.repo / PINS).read_text())
    pins["executables"]["sonic2app.exe"]["sha256"] = "c" * 64
    write_json(project.repo, PINS, pins)
    found = failures(project.run())
    assert "import_manifest FAIL: missing: Documentation/scope/SCOPE.md" in found
    assert f"import_manifest FAIL: not Git-tracked: {TOOL}" in found
    assert (f"import_manifest FAIL: {PINS} and {MANIFEST} disagree on "
            "sonic2app.exe: " + "c" * 64 + " != " + "a" * 64) in found


def test_rejects_the_previous_manifest_schema(project: Project) -> None:
    payload = project.manifest()
    payload["schema"] = "sa2-recomp-import-manifest/v1"
    project.write_manifest(payload)
    commit(project.repo, "old schema")
    assert failures(project.run()) == [
        f"import_manifest FAIL: {MANIFEST} schema is not sa2-recomp-import-manifest/v2"]


def test_root_argument_names_the_project(project: Project, tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(project.repo)],
        cwd=tmp_path, env=GIT_ENV, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "import_manifest OK: 2 files" in result.stdout
