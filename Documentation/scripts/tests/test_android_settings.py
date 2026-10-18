"""The Android settings screen's tweaks: what GameSettings sends the session for them.

GameSettings (host/android/java/com/sa2recomp/app/GameSettings.java) turns the settings screen's
choices into the session's --setting values (sessionSettings); the session puts them in the game's
environment before game.env.extra's lines (host/android/native/sa2_android_session.cpp). The
tweaks go as one value, SA2_TWEAKS, which runtime/sa2_tweaks.c reads: absent at the defaults, so a
session with every setting at its default runs as before there were tweaks, and the names of the
tweaks turned on, in GameSettings.TWEAKS's order, otherwise. TWEAKS is the one place a tweak is
named on Android: the Tweaks card makes a switch of each line (test_tweaks.py holds the lines to
runtime/sa2_tweaks.h's list). A small Java driver runs GameSettings here against android.jar; the
settings screen (Sa2SettingsActivity, with Ui and the layout editor it opens) is compiled beside
it, so a card that no longer builds fails here, not in the APK's build.

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

# argv: lng shrt [tweak names to turn on...] -> each --setting on a line, then the SA2_TWEAKS value
# in brackets; argv "list" -> each TWEAKS line as name|label|description.
DRIVER = r"""
package com.sa2recomp.app;
public class SettingsDriver {
    public static void main(String[] a) {
        if (a[0].equals("list")) {
            for (GameSettings.Tweak t : GameSettings.TWEAKS) System.out.println(t.name + "|" + t.label + "|" + t.description);
            return;
        }
        GameSettings s = new GameSettings();
        for (int i = 2; i < a.length; i++) s.tweaksOn.add(a[i]);
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


def test_the_remove_cutscene_bars_switch_sends_no_event_bars(java):
    assert java(2340, 1080, "no-event-bars") == ["SA2_TWEAKS=no-event-bars", "[no-event-bars]"]


def test_the_tweaks_turned_on_go_in_the_lists_order(java):
    """Every line turned on, given in reverse: SA2_TWEAKS names them all once, in TWEAKS's order;
    a name that is no line's is not sent."""
    names = [line.split("|")[0] for line in java("list")]
    assert java(2340, 1080, *reversed(names), "not-a-tweak") == [
        "SA2_TWEAKS=" + ",".join(names), "[" + ",".join(names) + "]"]


def test_each_tweak_line_has_a_label_and_a_note(java):
    lines = [line.split("|") for line in java("list")]
    assert ["no-event-bars", "Remove cutscene black bars"] in [line[:2] for line in lines]
    assert all(len(line) == 3 and all(line) for line in lines)


def test_the_vsync_note_says_android_does_not_tear_either_way(java):
    """Android's swapchain offers no IMMEDIATE, so with vsync off host 2 already presents through
    MAILBOX, tear-free; the switch's note names the trade it makes there, FIFO for up to a frame of
    delay, and does not promise to stop tearing."""
    assert [line for line in java("list") if line.startswith("vsync|")] == [
        "vsync|Vsync|Each frame waits for the screen's refresh (FIFO) instead of mailbox presentation. Android "
        "does not tear either way, so on a phone this only adds up to a frame of delay. Leave it off unless "
        "you are testing it."]


def test_the_tweaks_card_is_made_from_the_list():
    """One Tweaks card, a switch and its note for each TWEAKS line, and the switches kept in the
    preferences like every other setting: no tweak is named on the screen itself."""
    screen = (APP / "Sa2SettingsActivity.java").read_text()
    settings = (APP / "GameSettings.java").read_text()
    assert screen.count('card(col, "Tweaks")') == 1
    assert "mTweaks = new Switch[GameSettings.TWEAKS.length];" in screen
    assert "mTweaks[i] = toggle(tw, tweak.label," in screen and "note(tw, tweak.description);" in screen
    assert "mTweaks[i].setChecked(mS.tweaksOn.contains(GameSettings.TWEAKS[i].name));" in screen
    assert "no-event-bars" not in screen and "eventBars" not in screen + settings
    assert 'p.getBoolean("tweak_" + t.name, false)' in settings
    assert 'e.putBoolean("tweak_" + t.name, tweaksOn.contains(t.name));' in settings
