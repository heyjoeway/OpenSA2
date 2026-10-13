"""tools/s8/android_emulator.py: the project's one headless Android emulator.

The AVD's config: the tool's hardware keys are set once each, avdmanager's placeholder values
(`<build>`, `<temp>`) are dropped, every other line is kept. With no recorded emulator, status
reports nothing running and stop stops nothing, without touching adb. A recorded process group
that no longer exists is not running. A stop by one client leaves an emulator another client
holds running (its lease kept, the caller's released), and expired leases do not count.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s8"))
import android_emulator as E  # noqa: E402


def test_avd_config_sets_the_hardware_and_drops_placeholders():
    old = ("avd.id=<build>\navd.name=<build>\ndisk.dataPartition.path=<temp>\nhw.ramSize=1536\n"
           "image.sysdir.1=system-images/android-35/google_apis/x86_64/\nhw.lcd.width=320\n")
    lines = E.avd_config_text(old).splitlines()
    assert "image.sysdir.1=system-images/android-35/google_apis/x86_64/" in lines
    assert not [l for l in lines if "<" in l]
    keys = [l.split("=", 1)[0] for l in lines]
    assert keys.count("hw.ramSize") == 1 and "hw.ramSize=3072" in lines
    assert "hw.lcd.width=1280" in lines and "disk.dataPartition.size=16G" in lines
    # A real value under a placeholder key is kept.
    assert "avd.name=sa2" in E.avd_config_text("avd.name=sa2\n").splitlines()


@pytest.fixture
def no_adb(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "STATE", tmp_path / "emulator.state")
    monkeypatch.setattr(E, "LOCK", tmp_path / "emulator.lock")
    monkeypatch.setattr(E, "LEASES", tmp_path / "emulator.leases")
    monkeypatch.setattr(E, "AVD_HOME", tmp_path)

    def refuse(*a, **k):
        raise AssertionError(f"adb was called: {a}")
    monkeypatch.setattr(E, "adb", refuse)
    return tmp_path


def test_status_without_an_emulator(no_adb, capsys):
    assert E.status(None) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["running"] is False and got["booted"] is False and got["pid"] is None


def test_stop_without_an_emulator(no_adb, capsys):
    (no_adb / "emulator.state").write_text(json.dumps({"pid": 2 ** 22 + 12345}))
    assert E.stop(None) == 0
    assert json.loads(capsys.readouterr().out) == {"running": False, "note": "nothing to stop"}
    assert not (no_adb / "emulator.state").exists()


def test_a_dead_process_group_is_not_running():
    assert E.group_alive(2 ** 22 + 12345) is False


def test_stop_keeps_an_emulator_another_client_holds(no_adb, monkeypatch, capsys):
    import argparse
    import time
    (no_adb / "emulator.state").write_text(json.dumps({"pid": 4242}))
    monkeypatch.setattr(E, "group_alive", lambda pid: True)
    now = time.time()
    (no_adb / "emulator.leases").write_text(json.dumps(
        {"android-app": now + 3600, "default": now + 3600, "gone": now - 1}))
    assert E.stop(argparse.Namespace(client="default", force=False)) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["running"] is True and got["kept_for"] == ["android-app"]
    assert sorted(json.loads((no_adb / "emulator.leases").read_text())) == ["android-app"]
    assert (no_adb / "emulator.state").exists()
