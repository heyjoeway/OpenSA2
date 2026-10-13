#!/usr/bin/env python3
"""Behavior tests for Chaos Control's public command-line contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "chaos_control.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ChaosControlCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.invalid"],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Chaos Test"],
            cwd=self.repo, check=True,
        )
        for relative, content in {
            "evidence/base.txt": "base evidence\n",
            "products/base.out": "base output\n",
            "products/child.out": "child output\n",
            "checks/check_base.py": "print('base')\n",
            "checks/check_child.py": "print('child')\n",
            "evidence/unregistered.receipt": "coverage debt\n",
        }.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.ledger_path = self.repo / "ledger.json"
        self.write_ledger(self.valid_ledger())
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "baseline"],
            cwd=self.repo, check=True,
        )
        self.baseline = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            text=True, capture_output=True,
        ).stdout.strip()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def valid_ledger(self) -> dict:
        return {
            "schema": "sa2-fidelity-ledger/v1",
            "claims": [
                {
                    "id": "base",
                    "summary": "Base evidence",
                    "authority": "statically-translated",
                    "depends_on": [],
                    "sources": [
                        {
                            "path": "evidence/base.txt",
                            "sha256": digest(self.repo / "evidence/base.txt"),
                        }
                    ],
                    "outputs": ["products/base.out"],
                },
                {
                    "id": "child",
                    "summary": "Derived child",
                    "authority": "directly-traced",
                    "depends_on": ["base"],
                    "sources": [
                        {
                            "path": "products/base.out",
                            "sha256": digest(self.repo / "products/base.out"),
                        }
                    ],
                    "outputs": ["products/child.out"],
                },
            ],
            "suites": [
                {
                    "id": "base-suite",
                    "summary": "Verify base",
                    "claims": ["base"],
                    "depends_on": [],
                    "checker": "checks/check_base.py",
                    "files": [
                        {
                            "path": "checks/check_base.py",
                            "sha256": digest(self.repo / "checks/check_base.py"),
                        }
                    ],
                    "command": ["python3", "checks/check_base.py"],
                    "marker": {"kind": "text", "value": "base"},
                },
                {
                    "id": "child-suite",
                    "summary": "Verify child",
                    "claims": ["child"],
                    "depends_on": ["base-suite"],
                    "checker": "checks/check_child.py",
                    "files": [
                        {
                            "path": "checks/check_child.py",
                            "sha256": digest(self.repo / "checks/check_child.py"),
                        }
                    ],
                    "command": ["python3", "checks/check_child.py"],
                    "marker": {"kind": "sha256", "value": "a" * 64},
                },
            ],
            "coverage": {
                "patterns": [
                    "evidence/*.receipt",
                    "checks/check_*.py",
                ]
            },
        }

    def write_ledger(self, payload: dict) -> None:
        self.ledger_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli_with_ledger(self.ledger_path, *arguments)

    def run_cli_with_ledger(
            self, ledger_path: Path,
            *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repository",
                str(self.repo),
                "--ledger",
                str(ledger_path),
                *arguments,
            ],
            text=True,
            capture_output=True,
        )

    def test_audit_rejects_an_untracked_in_repository_ledger(self) -> None:
        untracked = self.repo / "untracked-ledger.json"
        untracked.write_text("")
        result = self.run_cli_with_ledger(untracked, "audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "ledger is not Git-tracked: untracked-ledger.json",
            result.stderr,
        )
        self.assertNotIn("cannot read ledger", result.stderr)

    def test_audit_rejects_an_intent_to_add_ledger(self) -> None:
        candidate = self.repo / "intent-ledger.json"
        candidate.write_bytes(self.ledger_path.read_bytes())
        subprocess.run(
            ["git", "add", "-N", "--", "intent-ledger.json"],
            cwd=self.repo,
            check=True,
        )
        result = self.run_cli_with_ledger(candidate, "audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "ledger is not Git-tracked: intent-ledger.json",
            result.stderr,
        )

    def test_audit_rejects_an_external_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory) / "ledger.json"
            external.write_text("")
            result = self.run_cli_with_ledger(external, "audit")
        self.assertEqual(2, result.returncode)
        self.assertIn("ledger is outside repository:", result.stderr)
        self.assertNotIn("cannot read ledger", result.stderr)

    def test_audit_rejects_a_tracked_ledger_removed_from_the_index(self) -> None:
        subprocess.run(
            ["git", "rm", "-q", "--cached", "ledger.json"],
            cwd=self.repo,
            check=True,
        )
        self.ledger_path.write_text("")
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "ledger is not Git-tracked: ledger.json",
            result.stderr,
        )
        self.assertNotIn("cannot read ledger", result.stderr)

    def test_audit_rejects_a_tracked_ledger_symlink(self) -> None:
        target = self.repo / "untracked-real-ledger.json"
        target.write_bytes(self.ledger_path.read_bytes())
        self.ledger_path.unlink()
        self.ledger_path.symlink_to(target.name)
        subprocess.run(
            ["git", "add", "ledger.json"], cwd=self.repo, check=True,
        )
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn("ledger path uses a symlink: ledger.json", result.stderr)
        self.assertNotIn("cannot read ledger", result.stderr)

    def test_audit_accepts_valid_ledger_and_reports_coverage_debt(self) -> None:
        result = self.run_cli("audit")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            "CHAOS_CONTROL_AUDIT_OK claims=2 suites=2 edges=4 "
            "coverage_debt=1 foreign_historical_pins=0",
            result.stdout,
        )
        self.assertIn(
            'CHAOS_CONTROL_COVERAGE_DEBT pattern="evidence/*.receipt" '
            "count=1",
            result.stdout,
        )
        self.assertIn(
            'CHAOS_CONTROL_COVERAGE_DEBT pattern="checks/check_*.py" '
            "count=0",
            result.stdout,
        )

    def test_audit_rejects_duplicate_ids_and_bad_markers(self) -> None:
        ledger = self.valid_ledger()
        ledger["suites"][1]["id"] = "base"
        ledger["suites"][1]["marker"]["value"] = "not-a-hash"
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn("duplicate id: base", result.stderr)
        self.assertIn("malformed sha256 marker", result.stderr)

    def test_audit_rejects_an_orphan_leaf_claim(self) -> None:
        ledger = self.valid_ledger()
        ledger["suites"][1]["claims"] = []
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "claim has no direct verification suite: child",
            result.stderr,
        )

    def test_plan_rejects_changed_output_for_an_orphan_leaf_claim(self) -> None:
        ledger = self.valid_ledger()
        ledger["suites"][1]["claims"] = []
        self.write_ledger(ledger)
        subprocess.run(
            ["git", "add", "ledger.json"], cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "-m", "orphan child claim"],
            cwd=self.repo,
            check=True,
        )
        baseline = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            text=True, capture_output=True,
        ).stdout.strip()
        (self.repo / "products/child.out").write_text("changed child output\n")
        result = self.run_cli("plan", "--changed-from", baseline)
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "claim has no direct verification suite: child",
            result.stderr,
        )
        self.assertNotIn("CHAOS_CONTROL_PLAN", result.stdout)

    def test_audit_rejects_missing_dependencies_files_and_malformed_hashes(self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][1]["depends_on"] = ["absent"]
        ledger["claims"][0]["sources"][0]["sha256"] = "bad"
        ledger["suites"][0]["files"][0]["path"] = "checks/missing.py"
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn("missing claim dependency: child -> absent", result.stderr)
        self.assertIn("malformed sha256", result.stderr)
        self.assertIn("missing file: checks/missing.py", result.stderr)

    def test_audit_rejects_an_unpinned_command_script(self) -> None:
        ledger = self.valid_ledger()
        ledger["suites"][0]["checker"] = "checks/other.py"
        ledger["suites"][0]["command"] = ["python3", "checks/other.py"]
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "suite base-suite checker must have exactly one pin: "
            "checks/other.py",
            result.stderr,
        )

    def test_audit_rejects_an_intent_to_add_checker(self) -> None:
        checker = self.repo / "checks/intent-checker.py"
        checker.write_text("print('intent only')\n")
        subprocess.run(
            ["git", "add", "-N", "--", "checks/intent-checker.py"],
            cwd=self.repo,
            check=True,
        )
        ledger = self.valid_ledger()
        ledger["suites"][0]["checker"] = "checks/intent-checker.py"
        ledger["suites"][0]["files"] = [{
            "path": "checks/intent-checker.py",
            "sha256": digest(checker),
        }]
        ledger["suites"][0]["command"] = [
            "python3", "checks/intent-checker.py",
        ]
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "suite base-suite checker is not Git-tracked: "
            "checks/intent-checker.py",
            result.stderr,
        )
        self.assertIn(
            "suite base-suite file is not Git-tracked: "
            "checks/intent-checker.py",
            result.stderr,
        )

    def test_audit_rejects_absolute_and_parent_checker_bypasses(self) -> None:
        for command_file in ("/tmp/checker", "../checker"):
            with self.subTest(command_file=command_file):
                ledger = self.valid_ledger()
                ledger["suites"][0]["command"] = ["python3", command_file]
                self.write_ledger(ledger)
                result = self.run_cli("audit")
                self.assertEqual(2, result.returncode)
                self.assertIn(
                    "suite base-suite has unsafe command checker path: "
                    + command_file,
                    result.stderr,
                )

    def test_audit_rejects_inline_interpreter_execution(self) -> None:
        ledger = self.valid_ledger()
        ledger["suites"][0]["command"] = [
            "python3", "-c", "exec(open('/tmp/checker').read())",
        ]
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "suite base-suite command uses forbidden interpreter mode: -c",
            result.stderr,
        )

    def test_audit_rejects_option_shaped_tracked_checkers_without_running_them(
            self) -> None:
        marker = self.repo / "checker-ran.marker"
        script = (
            "from pathlib import Path\n"
            "Path('checker-ran.marker').write_text('ran')\n"
        )
        for checker in ("-", "--help", "--version", "-B"):
            with self.subTest(checker=checker):
                marker.unlink(missing_ok=True)
                checker_path = self.repo / checker
                checker_path.write_text(script)
                subprocess.run(
                    ["git", "add", "--", checker],
                    cwd=self.repo,
                    check=True,
                )
                ledger = self.valid_ledger()
                ledger["suites"][0]["checker"] = checker
                ledger["suites"][0]["files"] = [{
                    "path": checker,
                    "sha256": digest(checker_path),
                }]
                ledger["suites"][0]["command"] = ["python3", checker]
                self.write_ledger(ledger)
                result = self.run_cli("audit")
                self.assertEqual(2, result.returncode)
                self.assertIn(
                    "suite base-suite checker path must not start with '-': "
                    + checker,
                    result.stderr,
                )
                self.assertIn(
                    "suite base-suite command checker must not start with "
                    "'-': " + checker,
                    result.stderr,
                )
                self.assertFalse(marker.exists())

    def test_audit_rejects_command_without_declared_checker_token(self) -> None:
        ledger = self.valid_ledger()
        ledger["suites"][0]["command"] = ["python3"]
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "suite base-suite command has no checker argument",
            result.stderr,
        )
        self.assertIn(
            "suite base-suite command must invoke declared checker exactly "
            "once: checks/check_base.py",
            result.stderr,
        )

    def test_audit_rejects_repository_when_git_tracks_nothing(self) -> None:
        subprocess.run(
            ["git", "rm", "-q", "--cached", "-r", "."],
            cwd=self.repo,
            check=True,
        )
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "ledger is not Git-tracked: ledger.json",
            result.stderr,
        )

    def test_audit_rejects_untracked_registered_files(self) -> None:
        subprocess.run(
            [
                "git", "rm", "-q", "--cached", "-r",
                "checks", "evidence", "products",
            ],
            cwd=self.repo,
            check=True,
        )
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "claim base file is not Git-tracked: evidence/base.txt",
            result.stderr,
        )
        self.assertIn(
            "claim child output is not Git-tracked: products/child.out",
            result.stderr,
        )
        self.assertIn(
            "suite base-suite file is not Git-tracked: checks/check_base.py",
            result.stderr,
        )

    def test_audit_rejects_registered_source_output_and_checker_symlinks(
            self) -> None:
        cases = (
            ("evidence/base.txt", "claim base"),
            ("products/base.out", "claim base"),
            ("checks/check_base.py", "suite base-suite"),
        )
        for relative, owner in cases:
            with self.subTest(relative=relative):
                path = self.repo / relative
                target = path.with_name("untracked-" + path.name)
                target.write_bytes(path.read_bytes())
                path.unlink()
                path.symlink_to(target.name)
                subprocess.run(
                    ["git", "add", "--", relative],
                    cwd=self.repo,
                    check=True,
                )
                ledger = self.valid_ledger()
                self.write_ledger(ledger)
                result = self.run_cli("audit")
                self.assertEqual(2, result.returncode)
                self.assertIn(
                    f"{owner} registered path uses a symlink: {relative}",
                    result.stderr,
                )

    def test_audit_rejects_dependency_cycles(self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][0]["depends_on"] = ["child"]
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn("claim dependency cycle: base -> child -> base", result.stderr)

    def test_audit_rejects_circular_claim_provenance(self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][0]["sources"] = [
            {
                "path": "products/child.out",
                "sha256": digest(self.repo / "products/child.out"),
            }
        ]
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "circular claim provenance: base sources products/child.out from descendant child",
            result.stderr,
        )

    def test_plan_selects_minimum_transitive_suite_closure(self) -> None:
        (self.repo / "evidence/base.txt").write_text("changed evidence\n")
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("changed=1 affected_claims=2 suites=2", result.stdout)
        lines = [line for line in result.stdout.splitlines()
                 if line.startswith("CHAOS_CONTROL_SUITE")]
        self.assertEqual(
            ["base-suite", "child-suite"],
            [line.split()[1].split("=", 1)[1] for line in lines],
        )

    def test_plan_includes_suite_prerequisites_for_direct_checker_change(self) -> None:
        (self.repo / "checks/check_child.py").write_text("print('changed')\n")
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("changed=1 affected_claims=0 suites=2", result.stdout)

    def test_plan_treats_the_ledger_as_a_control_plane_change(self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][0]["summary"] = "Reworded but structurally valid"
        self.write_ledger(ledger)
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("changed=1 affected_claims=0 suites=2", result.stdout)
        self.assertNotIn("CHAOS_CONTROL_UNREGISTERED_CHANGE", result.stdout)

    def test_plan_json_encodes_control_characters_in_unregistered_paths(
            self) -> None:
        relative = "rogue\nCHAOS_CONTROL_SUITE id=forged"
        (self.repo / relative).write_text("unregistered\n")
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn(
            "CHAOS_CONTROL_UNREGISTERED_CHANGE path="
            + json.dumps(relative),
            result.stdout,
        )
        self.assertNotIn("\nCHAOS_CONTROL_SUITE id=forged", result.stdout)
        self.assertTrue(all(
            line.startswith("CHAOS_CONTROL_")
            for line in result.stdout.splitlines()
        ))

    def test_plan_fails_cleanly_for_invalid_utf8_git_paths(self) -> None:
        relative = b"invalid-\xff-path"
        descriptor = os.open(
            os.fsencode(self.repo) + b"/" + relative,
            os.O_WRONLY | os.O_CREAT,
            0o600,
        )
        os.close(descriptor)
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "CHAOS_CONTROL_ERROR Git changed path is not valid UTF-8",
            result.stderr,
        )
        self.assertNotIn("Traceback", result.stderr)

    def test_plan_rejects_an_unchanged_stale_pin(self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][0]["sources"][0]["sha256"] = "0" * 64
        self.write_ledger(ledger)
        (self.repo / "checks/check_child.py").write_text("print('changed')\n")
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "claim base stale sha256 for evidence/base.txt",
            result.stderr,
        )

    def test_plan_tolerates_a_stale_pin_only_for_the_changed_path(self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][0]["sources"][0]["sha256"] = "0" * 64
        self.write_ledger(ledger)
        (self.repo / "evidence/base.txt").write_text("changed evidence\n")
        result = self.run_cli("plan", "--changed-from", self.baseline)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("changed=2 affected_claims=2 suites=2", result.stdout)

    def test_support_suite_is_only_selected_as_a_true_prerequisite(self) -> None:
        for relative, content in {
            "evidence/map.txt": "map evidence\n",
            "products/map.out": "map output\n",
            "checks/check_map.py": "print('map')\n",
        }.items():
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        ledger = self.valid_ledger()
        ledger["suites"][0]["claims"] = []
        ledger["suites"][1]["claims"] = ["base", "child"]
        ledger["claims"].append({
            "id": "source-map",
            "summary": "Independent source map",
            "authority": "shipped-data-derived",
            "depends_on": [],
            "sources": [{
                "path": "evidence/map.txt",
                "sha256": digest(self.repo / "evidence/map.txt"),
            }],
            "outputs": ["products/map.out"],
        })
        ledger["suites"].append({
            "id": "source-map-suite",
            "summary": "Pure source-map checker",
            "claims": ["source-map"],
            "depends_on": [],
            "checker": "checks/check_map.py",
            "files": [{
                "path": "checks/check_map.py",
                "sha256": digest(self.repo / "checks/check_map.py"),
            }],
            "command": ["python3", "checks/check_map.py"],
            "marker": {"kind": "text", "value": "map"},
        })
        self.write_ledger(ledger)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "support and map"],
            cwd=self.repo, check=True,
        )
        baseline = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            text=True, capture_output=True,
        ).stdout.strip()

        (self.repo / "products/map.out").write_text("changed map\n")
        mapped = self.run_cli("plan", "--changed-from", baseline)
        self.assertEqual(0, mapped.returncode, mapped.stdout + mapped.stderr)
        self.assertIn("changed=1 affected_claims=1 suites=1", mapped.stdout)
        self.assertIn("id=source-map-suite", mapped.stdout)
        self.assertNotIn("id=base-suite", mapped.stdout)

        subprocess.run(
            ["git", "restore", "products/map.out"], cwd=self.repo, check=True)
        (self.repo / "products/base.out").write_text("changed base\n")
        compiled = self.run_cli("plan", "--changed-from", baseline)
        self.assertEqual(0, compiled.returncode,
                         compiled.stdout + compiled.stderr)
        self.assertIn("changed=1 affected_claims=2 suites=2", compiled.stdout)
        suite_lines = [line for line in compiled.stdout.splitlines()
                       if line.startswith("CHAOS_CONTROL_SUITE")]
        self.assertEqual(
            ["base-suite", "child-suite"],
            [line.split()[1].split("=", 1)[1] for line in suite_lines],
        )

    def test_graph_json_is_deterministic_and_names_coverage_debt(self) -> None:
        first = self.repo / "first.json"
        second = self.repo / "second.json"
        a = self.run_cli("graph", "--json", str(first))
        b = self.run_cli("graph", "--json", str(second))
        self.assertEqual(0, a.returncode, a.stdout + a.stderr)
        self.assertEqual(0, b.returncode, b.stdout + b.stderr)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        graph = json.loads(first.read_text())
        self.assertEqual("sa2-fidelity-graph/v1", graph["schema"])
        self.assertEqual(
            ["evidence/unregistered.receipt"],
            graph["coverage_debt"]["evidence/*.receipt"],
        )
        self.assertEqual(
            ["base", "child"], [claim["id"] for claim in graph["claims"]]
        )

    def test_ignored_matching_files_do_not_change_coverage_or_graph(self) -> None:
        ignore = self.repo / ".gitignore"
        ignore.write_text("evidence/ignored.receipt\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "ignore local receipt"],
            cwd=self.repo,
            check=True,
        )
        first = Path(self.temporary.name).parent / (
            Path(self.temporary.name).name + "-before.json"
        )
        second = Path(self.temporary.name).parent / (
            Path(self.temporary.name).name + "-after.json"
        )
        try:
            a = self.run_cli("graph", "--json", str(first))
            (self.repo / "evidence/ignored.receipt").write_text(
                "ignored local evidence\n"
            )
            b = self.run_cli("graph", "--json", str(second))
            self.assertEqual(0, a.returncode, a.stdout + a.stderr)
            self.assertEqual(0, b.returncode, b.stdout + b.stderr)
            self.assertEqual(first.read_bytes(), second.read_bytes())
        finally:
            first.unlink(missing_ok=True)
            second.unlink(missing_ok=True)

    def test_audit_accepts_integration_choice_and_rejects_the_predecessor_label(
            self) -> None:
        ledger = self.valid_ledger()
        ledger["claims"][0]["authority"] = "integration-choice"
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        ledger["claims"][0]["authority"] = "unity-integration-choice"
        self.write_ledger(ledger)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "claim base has invalid authority: 'unity-integration-choice'",
            result.stderr,
        )

    def test_coverage_patterns_match_whole_tracked_paths_recursively(
            self) -> None:
        for relative in (
                "emitter/lift.c", "emitter/x86/decode.c", "vendor/emitter/copy.c"):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("code\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        ledger = self.valid_ledger()
        ledger["coverage"]["patterns"] = ["emitter/**", "emitter/*"]
        self.write_ledger(ledger)
        graph = self.repo / "graph.json"
        result = self.run_cli("graph", "--json", str(graph))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        debt = json.loads(graph.read_text())["coverage_debt"]
        self.assertEqual(
            ["emitter/lift.c", "emitter/x86/decode.c"], debt["emitter/**"])
        self.assertEqual(["emitter/lift.c"], debt["emitter/*"])

    def write_receipt(self, directory: str, pin: dict) -> Path:
        path = self.repo / directory / "receipt-v1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"checker": pin}, indent=2) + "\n")
        return path

    def test_audit_checks_receipt_pins_that_name_tracked_files(self) -> None:
        pin = {"file": "checks/check_base.py", "sha256": "0" * 64}
        for directory in (
                "Documentation/native-evidence-receipts",
                "Documentation/receipts"):
            with self.subTest(directory=directory):
                receipt = self.write_receipt(directory, pin)
                result = self.run_cli("audit")
                self.assertEqual(2, result.returncode)
                self.assertIn(
                    f"receipt {directory}/receipt-v1.json pin checker stale "
                    "sha256 for checks/check_base.py",
                    result.stderr,
                )
                receipt.unlink()

    def test_audit_verifies_historical_receipt_pins_at_their_commit(
            self) -> None:
        checker = self.repo / "checks/check_base.py"
        at_baseline = digest(checker)
        checker.write_text("print('base v2')\n")
        self.write_ledger(self.valid_ledger())
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "base v2"], cwd=self.repo, check=True)
        pin = {
            "file": "checks/check_base.py",
            "sha256": at_baseline,
            "historical": {
                "valid_at": self.baseline,
                "note": "superseded by base v2",
            },
        }
        self.write_receipt("Documentation/receipts", pin)
        result = self.run_cli("audit")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        pin["sha256"] = "1" * 64
        self.write_receipt("Documentation/receipts", pin)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "receipt Documentation/receipts/receipt-v1.json pin checker claims "
            f"checks/check_base.py hashed to {'1' * 64} at {self.baseline}, "
            f"but it hashed to {at_baseline}",
            result.stderr,
        )

    def test_audit_counts_foreign_historical_pins_only_in_predecessor_receipts(
            self) -> None:
        foreign = "f" * 40
        pin = {
            "file": "checks/check_base.py",
            "sha256": "2" * 64,
            "historical": {"valid_at": foreign, "note": "predecessor history"},
        }
        receipt = self.write_receipt(
            "Documentation/native-evidence-receipts", pin)
        result = self.run_cli("audit")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("foreign_historical_pins=1", result.stdout)
        receipt.unlink()
        self.write_receipt("Documentation/receipts", pin)
        result = self.run_cli("audit")
        self.assertEqual(2, result.returncode)
        self.assertIn(
            "receipt Documentation/receipts/receipt-v1.json pin checker names "
            f"commit {foreign}, which this repository does not have",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
