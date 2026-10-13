#!/usr/bin/env python3
"""Process-level checks for the SA2 native probe lease."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT_DIRECTORY = Path(__file__).resolve().parents[1]
PROJECT = SCRIPT_DIRECTORY.parents[1]
RUNNER_PATH = SCRIPT_DIRECTORY / "run_sa2_native_probe.py"
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import run_sa2_native_probe as runner  # noqa: E402


HOLDER_PROGRAM = r"""
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, sys.argv[1])
import run_sa2_native_probe as runner

record = {
    "schema": "sa2-native-probe-lock/v1",
    "owner_pid": os.getpid(),
    "hostname": "holder-host",
    "start_time_utc": "2026-09-18T00:00:00+00:00",
    "command": "holder-command",
    "probe": {"mod_id": "holder_probe", "source": "holder.c"},
}
with runner.native_probe_lease(Path(sys.argv[2]), 1.0, record):
    Path(sys.argv[3]).write_text("ready\n")
    time.sleep(0.8)
    os._exit(0)
"""


class NativeProbeLockTests(unittest.TestCase):
    def test_contention_release_and_stale_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock_path = root / "locks/native-probe.lock"
            ready_path = root / "holder-ready"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    HOLDER_PROGRAM,
                    str(SCRIPT_DIRECTORY),
                    str(lock_path),
                    str(ready_path),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 5.0
            while not ready_path.exists() and time.monotonic() < deadline:
                if holder.poll() is not None:
                    stdout, stderr = holder.communicate()
                    self.fail(
                        f"holder exited before acquiring lock: {stdout=} {stderr=}"
                    )
                time.sleep(0.02)
            self.assertTrue(ready_path.exists(), "holder did not acquire lock")

            active_record = json.loads(lock_path.read_text())
            self.assertEqual(active_record["owner_pid"], holder.pid)
            self.assertEqual(active_record["hostname"], "holder-host")
            self.assertEqual(active_record["command"], "holder-command")
            self.assertEqual(active_record["probe"]["mod_id"], "holder_probe")

            contender = runner.lock_record("contender_probe", Path("contender.c"))
            with self.assertRaises(runner.NativeProbeLockTimeout) as caught:
                with runner.native_probe_lease(lock_path, 0.15, contender):
                    self.fail("contender entered while holder owned the lease")
            self.assertIn(str(holder.pid), str(caught.exception))

            stdout, stderr = holder.communicate(timeout=3.0)
            self.assertEqual(holder.returncode, 0, f"{stdout=} {stderr=}")

            stale_record = dict(active_record)
            stale_record["owner_pid"] = 999_999_999
            lock_path.write_text(json.dumps(stale_record) + "\n")
            released_contender = runner.lock_record(
                "released_contender", Path("released.c")
            )
            with runner.native_probe_lease(lock_path, 0.0, released_contender):
                current_record = json.loads(lock_path.read_text())
                self.assertEqual(current_record["owner_pid"], os.getpid())
                self.assertEqual(
                    current_record["probe"]["mod_id"], "released_contender"
                )
            self.assertTrue(lock_path.exists(), "lease must not delete the lock record")

    def test_malformed_timeout_stops_before_private_state(self) -> None:
        for mode in ("cli", "environment"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                private_work = Path(directory)
                environment = os.environ.copy()
                environment["SA2_PRIVATE_WORK"] = str(private_work)
                command = [
                    sys.executable,
                    str(RUNNER_PATH),
                    "--source",
                    "Documentation/scripts/sa2_native_one_sided_oracle.c",
                    "--mod-id",
                    "lock_boundary_probe",
                    "--capture-dir",
                    str(private_work / "capture"),
                    "--capture",
                    "lock-boundary.csv",
                ]
                if mode == "cli":
                    command.extend(["--lock-timeout", "not-a-number"])
                else:
                    environment[
                        runner.LOCK_TIMEOUT_ENVIRONMENT_VARIABLE
                    ] = "not-a-number"

                completed = subprocess.run(
                    command,
                    cwd=PROJECT,
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("--lock-timeout", completed.stderr)
                self.assertFalse((private_work / "locks").exists())
                self.assertFalse((private_work / "native-run").exists())


if __name__ == "__main__":
    unittest.main()
