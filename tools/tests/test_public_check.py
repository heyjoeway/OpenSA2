"""Exercise the public source check with synthetic checkout contents."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCANNER_PATH = Path(__file__).resolve().parents[1] / "public_check.py"
SPEC = importlib.util.spec_from_file_location("public_check_under_test", SCANNER_PATH)
assert SPEC is not None and SPEC.loader is not None
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


class PublicSourceCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="public-source-check-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.write(".gitignore", "/private\n/build\n")
        self.write_json("tools/pins.json", {
            "executables": {
                "synthetic-input": {
                    "sha256": hashlib.sha256(b"synthetic executable input").hexdigest(),
                },
            },
        })
        self.write_json("Documentation/receipts/install-hashes-v1.json", {"files": []})

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_json(self, relative: str, value: object) -> Path:
        return self.write(relative, json.dumps(value) + "\n")

    def check_file(self, path: Path, terms: list[str] | None = None) -> list[str]:
        return scanner.check(self.root, [path], terms or [])

    def test_clean_source_returns_success_and_reports_no_failures(self) -> None:
        self.write("hello.py", "def answer():\n    return 42\n")
        output = io.StringIO()
        with patch.object(sys, "argv", ["public_check.py", "--root", str(self.root)]):
            with redirect_stdout(output):
                result = scanner.main()
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "Public source check: 4 files, 0 failures.\n")

    def test_explicit_tracked_binary_is_rejected_even_when_ignored(self) -> None:
        self.write(".gitignore", "/private\n/build\n*.exe\n")
        path = self.write("accidental.exe", "synthetic build output\n")
        self.assertEqual(self.check_file(path), [
            "accidental.exe: private data, game asset, or build output",
        ])

    def test_explicit_tracked_private_file_is_rejected_even_when_ignored(self) -> None:
        path = self.write("private/capture.txt", "synthetic capture\n")
        self.assertEqual(self.check_file(path), [
            "private/capture.txt: private data, game asset, or build output",
        ])

    def test_symlink_is_rejected(self) -> None:
        target = self.write("ordinary.txt", "ordinary source input\n")
        link = self.root / "alias.txt"
        link.symlink_to(target.name)
        self.assertEqual(self.check_file(link), [
            "alias.txt: symlinks are not accepted in the public source",
        ])

    def test_synthetic_credential_shapes_are_rejected(self) -> None:
        shapes = [
            "".join(["gh", "p", "_"]) + "A" * 40,
            "".join(["github", "_pat", "_"]) + "B" * 45,
            "".join(["s", "k", "-"]) + "C" * 45,
            "".join(["-----BEGIN ", "PRIVATE", " KEY-----"]),
        ]
        for index, value in enumerate(shapes):
            with self.subTest(shape=index):
                path = self.write("example.txt", value + "\n")
                self.assertEqual(self.check_file(path), [
                    "example.txt: credential-shaped text",
                ])

    def test_supplied_identity_term_is_rejected_in_text_case_insensitively(self) -> None:
        term = "-".join(["synthetic", "maintainer"])
        path = self.write("example.txt", "Author: " + term.upper() + "\n")
        self.assertEqual(self.check_file(path), [])
        self.assertEqual(self.check_file(path, [term]), [
            "example.txt: private identity term",
        ])

    def test_supplied_identity_term_is_rejected_in_filename(self) -> None:
        term = "-".join(["synthetic", "identity"])
        path = self.write(term + ".txt", "ordinary source input\n")
        self.assertEqual(self.check_file(path, [term.upper()]), [
            term + ".txt: private identity term",
        ])

    def test_missing_document_link_is_rejected_and_existing_link_is_accepted(self) -> None:
        path = self.write("docs/start.md", "Read [the build guide](build.md).\n")
        self.assertEqual(self.check_file(path), [
            "docs/start.md: missing local link build.md",
        ])
        self.write("docs/build.md", "# Build\n")
        self.assertEqual(self.check_file(path), [])

    def test_shipped_checksum_is_rejected_under_an_ordinary_source_filename(self) -> None:
        content = b"synthetic installation asset\n"
        self.write_json("Documentation/receipts/install-hashes-v1.json", {
            "files": [{
                "path": "resource/synthetic.asset",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }],
        })
        path = self.write("ordinary.txt", content.decode("utf-8"))
        self.assertEqual(self.check_file(path), [
            "ordinary.txt: size boundary or shipped-file checksum",
        ])

    def test_non_utf8_file_is_rejected_under_an_ordinary_source_filename(self) -> None:
        path = self.root / "ordinary.txt"
        path.write_bytes(bytes([0, 255, 128]))
        self.assertEqual(self.check_file(path), [
            "ordinary.txt: binary file in source export",
        ])


if __name__ == "__main__":
    unittest.main()
