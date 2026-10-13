"""Drive the FIFO observer through real fabricated DirectInput vtables."""
import os
from pathlib import Path
import re
import subprocess

import pytest

ROOT = Path(__file__).parents[3]
ZIG = ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig"


@pytest.fixture(scope="module")
def probes(tmp_path_factory):
    work = tmp_path_factory.mktemp("poll-observer-build")
    env = dict(os.environ, TMPDIR=str(work), ZIG_GLOBAL_CACHE_DIR=str(work / "cache"))
    binaries = {}
    for name, defines in [("observed", []), ("undefined", ["-DNO_OBSERVER"])]:
        binary = work / name
        argv = [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", *defines,
                "-I" + str(ROOT / "runtime"),
                str(ROOT / "harness/dinput_poll_observer_probe.c"),
                str(ROOT / "runtime/sa2_dinput.c"),
                str(ROOT / "runtime/sa2_guest_call.c"),
                str(ROOT / "runtime/sa2_devprofile.c"), "-o", str(binary)]
        child = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=60)
        (work / (name + "-compile-command.txt")).write_text(repr(argv))
        (work / (name + "-compile-stdout.txt")).write_text(child.stdout)
        (work / (name + "-compile-stderr.txt")).write_text(child.stderr)
        (work / (name + "-compile-exit.txt")).write_text(str(child.returncode))
        assert child.returncode == 0, child.stderr
        binaries[name] = binary
    return binaries


def drive(binary, mode, tmp_path):
    argv = [str(binary), mode, str(tmp_path / "input-source")]
    child = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    (tmp_path / "command.txt").write_text(repr(argv))
    (tmp_path / "stdout.txt").write_text(child.stdout)
    (tmp_path / "stderr.txt").write_text(child.stderr)
    (tmp_path / "exit.txt").write_text(str(child.returncode))
    assert child.returncode == 0, child.stdout + child.stderr
    result = re.search(r"^RESULT checks=(\d+) fails=(\d+) vtable_calls=(\d+)$", child.stdout, re.M)
    assert result and int(result[1]) > 800 and result[2] == "0"
    rows = [tuple(map(int, row)) for row in re.findall(r"^ROW (\d+) (\d+) (\d+) (\d+)$", child.stdout, re.M)]
    return rows, int(result[3])


@pytest.mark.parametrize("mode", ["fifo", "script", "unset"])
def test_host_records_observer(probes, tmp_path, mode):
    rows, calls = drive(probes["observed"], mode, tmp_path)
    if mode == "fifo":
        assert rows == [(1, 0, 0, 1), (2, 1, 2, 2), (4, 5, 5, 1), (5, 6, 6, 1), (6, 7, 7, 1)]
        assert calls == 12
    else:
        assert rows == [] and calls == 7


def test_undefined_observer_keeps_fifo_behavior(probes, tmp_path):
    rows, calls = drive(probes["undefined"], "fifo", tmp_path)
    assert rows == [] and calls == 12
