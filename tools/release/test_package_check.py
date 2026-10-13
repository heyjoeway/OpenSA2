"""Exercise release checks through their command-line interface."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

CHECK = Path(__file__).with_name("check_package.py")


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def run_check(self, entries, *, deny=(), pins=None):
        package = self.root / "release.zip"
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in entries:
                    archive.writestr(name, data)
        deny_path = self.root / "deny.json"
        deny_path.write_text(json.dumps({"terms": list(deny)}))
        command = [sys.executable, str(CHECK), str(package), "--deny-file", str(deny_path)]
        if pins is not None:
            pin_path = self.root / "pins.json"
            pin_path.write_text(json.dumps(pins))
            command += ["--forbidden-hashes", str(pin_path)]
        return subprocess.run(command, capture_output=True, text=True)

    def test_clean_package_passes(self):
        result = self.run_check([("OpenSA2/README.txt", b"Experimental release")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 files, 0 failures", result.stdout)

    def test_private_string_in_compressed_native_library_fails(self):
        result = self.run_check([("lib/arm64-v8a/libmain.so", b"\x7fELF /home/private-user/source")],
                                deny=["/home/private-user"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Private term 1", result.stdout)

    def test_utf16_private_string_fails(self):
        result = self.run_check([("bin/player.exe", "Private Identity".encode("utf-16-le"))],
                                deny=["Private Identity"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Private term 1", result.stdout)

    def test_renamed_original_game_file_fails(self):
        data = b"synthetic game file"
        result = self.run_check([("share/renamed.dat", data)],
                                pins={"files": [{"sha256": hashlib.sha256(data).hexdigest()}]})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Original game file hash", result.stdout)

    def test_dependency_pin_is_not_a_game_file(self):
        data = b"project patch"
        result = self.run_check([("patches/project.patch", data)], pins={
            "executables": {"game.exe": {"sha256": "0" * 64}},
            "tools": {"patch": {"sha256": hashlib.sha256(data).hexdigest()}},
        })
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_original_executable_name_fails(self):
        result = self.run_check([("share/sonic2app.exe", b"synthetic")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Original executable filename", result.stdout)

    def test_duplicate_members_fail(self):
        result = self.run_check([("README.txt", b"first"), ("README.txt", b"second")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate ZIP entries", result.stderr)

    def test_parent_path_fails(self):
        result = self.run_check([("../outside", b"content")])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsafe archive path", result.stderr)


if __name__ == "__main__":
    unittest.main()
