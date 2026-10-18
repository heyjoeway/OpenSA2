"""The Android settings screen's tweaks: what GameSettings sends the session for them.

GameSettings (host/android/java/com/sa2recomp/app/GameSettings.java) turns the settings screen's
choices into the session's --setting values (sessionSettings); the session puts them in the game's
environment before game.env.extra's lines (host/android/native/sa2_android_session.cpp). The
tweaks go as one value, SA2_TWEAKS, which runtime/sa2_tweaks.c reads: absent at the defaults, so a
session with every setting at its default runs as before there were tweaks, and "no-event-bars"
when the cutscene bars switch is off. A small Java driver runs GameSettings here against
android.jar; the settings screen (Sa2SettingsActivity, with Ui and the layout editor it opens) is
compiled beside it, so a card that no longer builds fails here, not in the APK's build.

Needs the pinned JDK (tools/pins.json java) and the Android SDK's android.jar; it is skipped
without them.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JDK = ROOT / json.loads((ROOT / "tools/pins.json").read_text())["tools"]["java"]["path"]
ANDROID_JAR = ROOT / "private/android-sdk/platforms/android-36/android.jar"
APP = ROOT / "host/android/java/com/sa2recomp/app"

# argv: lng shrt [eventBars] -> each --setting on a line, then the SA2_TWEAKS value in brackets.
DRIVER = r"""
package com.sa2recomp.app;
public class SettingsDriver {
    public static void main(String[] a) {
        GameSettings s = new GameSettings();
        if (a.length > 2) s.eventBars = Boolean.parseBoolean(a[2]);
        for (String e : s.sessionSettings(Integer.parseInt(a[0]), Integer.parseInt(a[1]))) System.out.println(e);
        System.out.println("[" + s.tweaks() + "]");
    }
}
"""


@pytest.fixture(scope="module")
def java(tmp_path_factory):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR) if not p.exists()]
    if missing:
        pytest.skip(f"needs the JDK and android.jar: missing {missing}")
    d = tmp_path_factory.mktemp("settings")
    src = d / "java/com/sa2recomp/app"
    src.mkdir(parents=True)
    (src / "SettingsDriver.java").write_text(DRIVER)
    for name in ("GameSettings", "Sa2SettingsActivity", "Ui", "Sa2LayoutActivity"):
        shutil.copy(APP / f"{name}.java", src)
    done = subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                           "-d", str(d / "classes"), *map(str, src.glob("*.java"))], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr

    def run(*args) -> list[str]:
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{d / 'classes'}:{ANDROID_JAR}",
                               "com.sa2recomp.app.SettingsDriver", *map(str, args)], capture_output=True,
                              text=True, check=True).stdout.splitlines()
    return run


def test_the_defaults_send_no_tweak(java):
    assert java(2340, 1080) == ["[]"]
    assert java(2340, 1080, "true") == ["[]"]


def test_the_cutscene_bars_off_sends_no_event_bars(java):
    assert java(2340, 1080, "false") == ["SA2_TWEAKS=no-event-bars", "[no-event-bars]"]


def test_the_tweaks_card_is_on_the_settings_screen():
    """One Tweaks card with the cutscene bars switch, kept in the preferences like every other
    setting."""
    screen = (APP / "Sa2SettingsActivity.java").read_text()
    settings = (APP / "GameSettings.java").read_text()
    assert screen.count('card(col, "Tweaks")') == 1
    assert 'toggle(tw, "Cutscene black bars"' in screen and "mEventBars.setChecked(mS.eventBars);" in screen
    assert 'p.getBoolean("event_bars", s.eventBars)' in settings
    assert '.putBoolean("event_bars", eventBars)' in settings
