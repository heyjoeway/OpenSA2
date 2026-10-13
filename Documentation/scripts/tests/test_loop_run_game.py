"""tools/s4/loop_run.py's run_game: the game's stderr read while it runs.

The d3d9 device's closed world (runtime/sa2_d3d9.c; the user's ruling of 2026-10-09, item 1)
reports a method off the M7 list on the game's stderr. A live session keeps loop_run's own
output as its game.log (tools/s5/play_live.py), not the game's stderr, so run_game copies each
closed-world line to stdout as it arrives; without it live play's loud line and its summary at
exit would never reach game.log. These tests run run_game against a stand-in child, as play_live
runs the game: stdout redirected to a file, and SIGTERM sent to the child as stop_game sends it.
"""
from __future__ import annotations

import importlib.util
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("loop_run_under_test", ROOT / "tools/s4/loop_run.py")
LR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(LR)

ENV = {"PATH": "/usr/bin:/bin"}
LOUD = "*** d3d9 CLOSED WORLD: IDirect3DDevice9::GetScissorRect called from 0x00868a16 ***"
SUMMARY = "*** d3d9 CLOSED WORLD summary (live play): 1 method(s) off the M7 list ***"


def run_to_file(tmp_path: Path, child: str, timeout: float, during=None):
    """run_game in a process of its own whose stdout is a file (play_live's game.log); `during`
    is called with (the file, the game's pid) while the game runs."""
    log = tmp_path / "game.log"
    pidfile = tmp_path / "pid"
    driver = (
        "import importlib.util, os, sys, subprocess\n"
        f"spec = importlib.util.spec_from_file_location('lr', {str(ROOT / 'tools/s4/loop_run.py')!r})\n"
        "LR = importlib.util.module_from_spec(spec); spec.loader.exec_module(LR)\n"
        "real = subprocess.Popen\n"
        "def popen(*a, **k):\n"
        "    p = real(*a, **k)\n"
        f"    open({str(pidfile)!r}, 'w').write(str(p.pid))\n"
        "    return p\n"
        "LR.subprocess.Popen = popen\n"
        "try:\n"
        f"    rc, tail = LR.run_game([sys.executable, '-c', {child!r}], {ENV!r}, {timeout!r})\n"
        "except subprocess.TimeoutExpired:\n"
        "    print('== timeout', flush=True); sys.exit(9)\n"
        "print(f'== rc {rc} tail {len(tail)}', flush=True)\n"
        "sys.stdout.write('== tail ' + tail.replace(chr(10), '|') + chr(10))\n")
    with open(log, "w") as fh:
        p = subprocess.Popen([sys.executable, "-c", driver], stdout=fh, stderr=subprocess.PIPE, text=True)
        if during is not None:
            deadline = time.monotonic() + 20
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            during(log, int(pidfile.read_text()))
        _, err = p.communicate(timeout=60)
    return p.returncode, log.read_text(), err


def test_closed_world_lines_reach_the_log_while_the_game_runs_and_nothing_else_does(tmp_path: Path) -> None:
    child = ("import sys, time\n"
             "sys.stdout.write('x' * 200000 + '\\n'); sys.stdout.flush()\n"
             "sys.stderr.write('noise ' * 1000 + '\\n')\n"
             f"sys.stderr.write({LOUD!r} + '\\n'); sys.stderr.flush()\n"
             "time.sleep(3)\n"
             "for i in range(3000): sys.stderr.write(f'line {i}\\n')\n"
             f"sys.stderr.write({SUMMARY!r} + '\\n')\n"
             "sys.stderr.write('last line\\n')\n"
             "sys.exit(7)\n")
    seen_while_running = {}

    def during(log: Path, pid: int) -> None:
        deadline = time.monotonic() + 2.5
        while time.monotonic() < deadline:
            if LOUD in log.read_text():
                seen_while_running["loud"] = True
                break
            time.sleep(0.05)
        seen_while_running["alive"] = os.path.exists(f"/proc/{pid}")
    rc, text, err = run_to_file(tmp_path, child, 60, during)
    assert rc == 0, err
    assert seen_while_running == {"loud": True, "alive": True}
    lines = text.splitlines()
    assert lines[:2] == [LOUD, SUMMARY]
    assert lines[2] == f"== rc 7 tail {LR.STDERR_TAIL}"
    assert lines[3].startswith("== tail ") and lines[3].endswith("last line|")
    assert "noise" not in text and "xxxx" not in text and "line 5" not in text


def test_the_summary_a_game_writes_when_it_is_stopped_reaches_the_log(tmp_path: Path) -> None:
    """play_live's stop_game sends SIGTERM to the game; the device's summary printed then (at
    exit) must still land in game.log, before loop_run's own line."""
    child = ("import signal, sys, time\n"
             "def stop(*_):\n"
             f"    sys.stderr.write({SUMMARY!r} + '\\n'); sys.stderr.flush(); sys.exit(0)\n"
             "signal.signal(signal.SIGTERM, stop)\n"
             f"sys.stderr.write({LOUD!r} + '\\n'); sys.stderr.flush()\n"
             "time.sleep(30)\n")

    def during(log: Path, pid: int) -> None:
        deadline = time.monotonic() + 10
        while LOUD not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        os.kill(pid, signal.SIGTERM)
    rc, text, err = run_to_file(tmp_path, child, 60, during)
    assert rc == 0, err
    lines = text.splitlines()
    assert lines[:3] == [LOUD, SUMMARY, "== rc 0 tail " + str(len(LOUD) + len(SUMMARY) + 2)]


def test_the_timeout_kills_the_game_and_raises_within_its_bound(tmp_path: Path) -> None:
    """subprocess.TimeoutExpired, as subprocess.run(timeout=) gave it, even when a process the
    game started still holds its pipes (the readers are waited for at most READER_JOIN_S)."""
    child = ("import subprocess, sys, time\n"
             "subprocess.Popen(['sleep', '20'])\n"
             "time.sleep(30)\n")
    started = time.monotonic()
    rc, text, err = run_to_file(tmp_path, child, 1.0)
    assert rc == 9 and text.splitlines() == ["== timeout"], (rc, text, err)
    assert time.monotonic() - started < 1.0 + LR.READER_JOIN_S + 5


def test_the_label_line_shows_the_closed_world_block() -> None:
    assert LR.closed_world_shown(None) == ""
    assert LR.closed_world_shown({"mode": "trap", "unlisted_methods": 0, "unlisted_calls": 0,
                                  "unlisted": []}) == " closed_world=trap: none off the M7 list"
    assert LR.closed_world_shown({"mode": "log", "unlisted_methods": 2, "unlisted_calls": 37, "unlisted": [
        {"interface": "IDirect3DDevice9", "method": "GetScissorRect", "slot": 76, "calls": 36,
         "outcome": "answered"},
        {"interface": "IDirect3DDevice9", "method": None, "slot": 120, "calls": 1,
         "outcome": "unnamed-slot"}]}) == (
        " closed_world=log: 2 method(s) off the M7 list, 37 call(s): IDirect3DDevice9::GetScissorRect "
        "x36 (answered); IDirect3DDevice9 slot 120 x1 (unnamed-slot)")


def test_each_run_record_is_indexed_for_the_census_as_it_is_written() -> None:
    """loop_run deletes a label's JSON before it runs that label again, so it indexes each record
    into M7's census of every game run as soon as it is written (m7_inputs.index_run_record)."""
    text = (ROOT / "tools/s4/loop_run.py").read_text()
    body = text[text.index("        else:\n            index_for_m7(out)"):]
    assert body.index("index_for_m7(out)") < body.index("json.loads(out.read_text())")
    assert "m7_inputs.index_run_record(C.ROOT, out" in text
