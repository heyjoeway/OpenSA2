#!/usr/bin/env python3
"""Behavior tests for Omochao Split."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT / "Documentation/scripts/compare_sa2_record_streams.py"
FIXTURES = PROJECT / "Documentation/tests/omochao-split"
IDENTITY = [
    "--identity", "tick",
    "--identity", "candidate",
    "--identity", "phase",
]


class OmochaoSplitTests(unittest.TestCase):
    def run_tool(self, left: Path, right: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *IDENTITY, *extra, str(left), str(right)],
            cwd=PROJECT,
            capture_output=True,
            text=True,
        )

    def test_identical_ground_query_streams(self) -> None:
        result = self.run_tool(
            FIXTURES / "ground-query-a.csv",
            FIXTURES / "ground-query-identical.csv",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "OMOCHAO_SPLIT_IDENTICAL records=3 compared_fields=7 "
            "identity=tick,candidate,phase\n",
        )

    def test_ground_fixture_preserves_exact_tick_118_query_words(self) -> None:
        with (FIXTURES / "ground-query-a.csv").open(newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows, [
            ["tick", "candidate", "phase", *[f"query_w{i}" for i in range(7)]],
            ["118", "0", "selector", "0x40a00000", "0x40cc97d2",
             "0xc3939e9e", "0xc4c3a9eb", "0x00000000", "0xbe734f82",
             "0xbecccccd"],
            ["118", "0", "precise", "0x40a00000", "0x40cc97d2",
             "0xc3939e9e", "0xc4c3a9eb", "0x00000000", "0xbe734f82",
             "0xbecccccd"],
            ["118", "1", "precise", "0x40a00000", "0x40cc97d0",
             "0xc3930000", "0xc4c3b6b8", "0x00000000", "0x00000000",
             "0x00000000"],
        ])
        with (FIXTURES / "ground-query-mutated.csv").open(newline="") as stream:
            mutated = list(csv.reader(stream))
        differences = [
            (row, column) for row in range(len(rows))
            for column in range(len(rows[row]))
            if rows[row][column] != mutated[row][column]
        ]
        self.assertEqual(differences, [(3, 4)])

    def test_reports_first_ground_query_mutation_as_words_and_floats(self) -> None:
        result = self.run_tool(
            FIXTURES / "ground-query-a.csv",
            FIXTURES / "ground-query-mutated.csv",
            "--format", "json",
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema"], "sa2-omochao-split/v1")
        self.assertEqual(report["status"], "different")
        self.assertEqual(
            report["identity"],
            {"tick": "118", "candidate": "1", "phase": "precise"},
        )
        self.assertEqual(report["field"], "query_w1")
        self.assertEqual(report["left"]["uint32"], 0x40CC97D0)
        self.assertEqual(report["left"]["hex"], "0x40CC97D0")
        self.assertEqual(report["left"]["float32"], "6.3935318")
        self.assertEqual(report["left"]["line"], 4)
        self.assertEqual(report["right"]["uint32"], 0x40CC97D2)
        self.assertEqual(report["right"]["float32"], "6.39353275")
        self.assertEqual(report["right"]["line"], 4)
        self.assertEqual(result.stderr, "")

    def test_rejects_each_empty_stream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            empty = root / "empty.csv"
            populated = root / "populated.csv"
            empty.write_text("tick,candidate,phase,value\n")
            populated.write_text(
                "tick,candidate,phase,value\n118,0,selector,0\n"
            )
            for left, right in ((empty, populated), (populated, empty)):
                with self.subTest(empty=empty == left and "left" or "right"):
                    result = self.run_tool(left, right, "--format", "json")
                    self.assertEqual(result.returncode, 2)
                    report = json.loads(result.stdout)
                    self.assertEqual(report["error"], "empty-stream")
                    self.assertEqual(report["path"], str(empty))

    def test_rejects_missing_identity_between_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            right = Path(directory) / "right.csv"
            right.write_text(
                "tick,candidate,phase,value\n"
                "118,0,before,0\n"
                "118,1,before,0\n"
            )
            left = Path(directory) / "left.csv"
            left.write_text(
                "tick,candidate,phase,value\n"
                "118,0,before,0\n"
                "118,0,after,0\n"
            )
            result = self.run_tool(left, right, "--format", "json")
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["error"], "identity-set-mismatch")
        self.assertEqual(report["missing_from_left"], [
            {"tick": "118", "candidate": "1", "phase": "before"}
        ])
        self.assertEqual(report["missing_from_right"], [
            {"tick": "118", "candidate": "0", "phase": "after"}
        ])

    def test_rejects_duplicate_identity_with_both_locations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            duplicate = Path(directory) / "duplicate.csv"
            duplicate.write_text(
                "tick,candidate,phase,value\n"
                "118,0,before,0\n"
                "118,0,before,1\n"
            )
            result = self.run_tool(duplicate, duplicate, "--format", "json")
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report["error"], "duplicate-identity")
        self.assertEqual(report["first_line"], 2)
        self.assertEqual(report["duplicate_line"], 3)

    def test_rejects_reordered_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            left = Path(directory) / "left.csv"
            right = Path(directory) / "right.csv"
            header = "tick,candidate,phase,value\n"
            left.write_text(header + "118,0,before,0\n118,1,before,1\n")
            right.write_text(header + "118,1,before,1\n118,0,before,0\n")
            result = self.run_tool(left, right, "--format", "json")
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report["error"], "reordered-identity")
        self.assertEqual(report["record_index"], 0)
        self.assertEqual(report["left"]["line"], 2)
        self.assertEqual(report["right"]["line"], 2)

    def test_rejects_missing_identity_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "malformed.csv"
            malformed.write_text("tick,candidate,value\n118,0,0\n")
            result = self.run_tool(malformed, malformed, "--format", "json")
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertEqual(report["error"], "missing-identity-field")
        self.assertEqual(report["field"], "phase")


if __name__ == "__main__":
    unittest.main()
