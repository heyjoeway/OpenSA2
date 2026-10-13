"""Exercise the public source check with synthetic checkout contents."""
from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib


SCANNER_PATH = Path(__file__).resolve().parents[1] / "public_check.py"
SPEC = importlib.util.spec_from_file_location("public_check_under_test", SCANNER_PATH)
assert SPEC is not None and SPEC.loader is not None
scanner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(scanner)


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload)))


def screenshot_png(color: int = 2, extra_chunk: bytes = b"") -> bytes:
    width, height = 320, 180
    channels = 3 if color == 2 else 4
    row = b"\0" + bytes([40, 100, 200, 255][:channels]) * width
    return (b"\x89PNG\r\n\x1a\n" +
            png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)) +
            extra_chunk + png_chunk(b"IDAT", zlib.compress(row * height)) +
            png_chunk(b"IEND", b""))


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

    def write_png(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

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

    def test_canonical_rgb_and_rgba_screenshots_are_accepted(self) -> None:
        for color in (2, 6):
            with self.subTest(color=color):
                path = self.write_png("docs/screenshots/gameplay.png", screenshot_png(color))
                self.assertEqual(self.check_file(path), [])

    def test_png_outside_screenshot_directory_is_rejected(self) -> None:
        for relative in ("gameplay.png", "docs/assets/gameplay.png", "private/gameplay.png"):
            with self.subTest(path=relative):
                path = self.write_png(relative, screenshot_png())
                self.assertIn(relative + ": binary file in source export", self.check_file(path))

    def test_screenshots_reject_text_exif_and_other_ancillary_chunks(self) -> None:
        for kind in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME", b"gAMA"):
            with self.subTest(chunk=kind):
                path = self.write_png("docs/screenshots/gameplay.png",
                                      screenshot_png(extra_chunk=png_chunk(kind, b"synthetic metadata")))
                self.assertEqual(self.check_file(path), [
                    "docs/screenshots/gameplay.png: PNG contains metadata or an unsupported chunk",
                ])

    def test_screenshots_reject_corrupt_or_trailing_data(self) -> None:
        valid = screenshot_png()
        damaged = bytearray(valid)
        damaged[-1] ^= 1
        cases = {
            "signature": (b"not PNG" + valid[7:], "invalid PNG signature"),
            "checksum": (bytes(damaged), "invalid PNG chunk checksum"),
            "truncation": (valid[:-1], "truncated PNG chunk"),
            "trailing": (valid + b"synthetic identity", "PNG has trailing bytes"),
            "missing end": (valid[:-12], "PNG end chunk is missing"),
        }
        for name, (data, error) in cases.items():
            with self.subTest(case=name):
                path = self.write_png("docs/screenshots/gameplay.png", data)
                self.assertEqual(self.check_file(path), ["docs/screenshots/gameplay.png: " + error])

    def test_screenshots_keep_filename_and_shipped_checksum_checks(self) -> None:
        data = screenshot_png()
        term = "synthetic-identity"
        path = self.write_png("docs/screenshots/" + term + ".png", data)
        self.write_json("Documentation/receipts/install-hashes-v1.json", {
            "files": [{"path": "resource/example.png", "bytes": len(data),
                       "sha256": hashlib.sha256(data).hexdigest()}],
        })
        self.assertEqual(self.check_file(path, [term.upper()]), [
            "docs/screenshots/" + term + ".png: size boundary or shipped-file checksum",
            "docs/screenshots/" + term + ".png: private identity term",
        ])

    def test_screenshots_require_header_data_end_order_and_one_pixel_stream(self) -> None:
        signature = b"\x89PNG\r\n\x1a\n"
        header = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        compressed = zlib.compress(b"\0abc")
        data = png_chunk(b"IDAT", compressed)
        end = png_chunk(b"IEND", b"")
        split_data = png_chunk(b"IDAT", compressed[:3]) + png_chunk(b"IDAT", compressed[3:])
        path = self.write_png("docs/screenshots/gameplay.png", signature + header + split_data + end)
        self.assertEqual(self.check_file(path), [])
        cases = {
            "data first": (data + header + end, "PNG image data precedes its header"),
            "duplicate header": (header + header + data + end, "invalid PNG header order or length"),
            "missing data": (header + end, "invalid PNG end chunk"),
            "bad end": (header + data + png_chunk(b"IEND", b"x"), "invalid PNG end chunk"),
            "two streams": (header + png_chunk(b"IDAT", compressed + compressed) + end,
                            "invalid PNG pixel data length or stream"),
            "bad compression": (header + png_chunk(b"IDAT", b"invalid compression") + end,
                                "invalid PNG pixel compression"),
        }
        for name, (chunks, error) in cases.items():
            with self.subTest(case=name):
                path.write_bytes(signature + chunks)
                self.assertEqual(self.check_file(path), ["docs/screenshots/gameplay.png: " + error])

    def test_screenshots_reject_invalid_headers_and_pixel_streams(self) -> None:
        def image(header: tuple[int, ...], raw: bytes) -> bytes:
            return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", struct.pack(">IIBBBBB", *header)) +
                    png_chunk(b"IDAT", zlib.compress(raw)) + png_chunk(b"IEND", b""))

        cases = {
            "dimensions": (image((4097, 1, 8, 2, 0, 0, 0), b""),
                           "PNG dimensions must be between 1 and 4096"),
            "interlace": (image((1, 1, 8, 2, 0, 0, 1), b"\0abc"),
                          "PNG must use noninterlaced 8-bit RGB or RGBA"),
            "palette": (image((1, 1, 8, 3, 0, 0, 0), b"\0a"),
                        "PNG must use noninterlaced 8-bit RGB or RGBA"),
            "short pixels": (image((1, 1, 8, 2, 0, 0, 0), b"\0ab"),
                             "invalid PNG pixel data length or stream"),
            "long pixels": (image((1, 1, 8, 2, 0, 0, 0), b"\0abcd"),
                            "invalid PNG pixel data length or stream"),
            "filter": (image((1, 1, 8, 2, 0, 0, 0), b"\5abc"), "invalid PNG row filter"),
        }
        for name, (data, error) in cases.items():
            with self.subTest(case=name):
                path = self.write_png("docs/screenshots/gameplay.png", data)
                self.assertEqual(self.check_file(path), ["docs/screenshots/gameplay.png: " + error])


if __name__ == "__main__":
    unittest.main()
