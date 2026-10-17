"""The Android layout editor shows the touch controls where the game draws them.

The editor (host/android/java/com/sa2recomp/app/Sa2LayoutActivity.java) lays the controls out
with GameSettings.touchLayout, a copy of host/player/touch_overlay.cpp's layout(): the control
area (the window, or the band under a portrait window's picture), the size and each group's
place. Both run here on the same cases, the overlay's through its control_centre and the editor's
through a small Java driver against android.jar, and every group's centre must agree to a
hundredth of a pixel: a change to one without the other fails here, not on a phone.

Needs the pinned JDK (tools/pins.json java), the Android SDK's android.jar and the SDL3 install
the player's tests build against; it is skipped without them.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GXX = shutil.which(os.environ.get("CXX", "g++"))
JDK = ROOT / json.loads((ROOT / "tools/pins.json").read_text())["tools"]["java"]["path"]
ANDROID_JAR = ROOT / "private/android-sdk/platforms/android-36/android.jar"
INSTALL = Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install")

GROUPS = ("STICK", "FACE", "START", "LT", "RT")
# window w, h; frame w, h (0: none yet); scale; SA2_TOUCH_LAYOUT; SA2_TOUCH_LAYOUT_BELOW
CASES = [
    (1280, 720, 0, 0, 1.0, "", ""),                               # before the first frame
    (2340, 1080, 2340, 1080, 1.0, "", ""),                        # full width, the default
    (2340, 1080, 1440, 1080, 1.4, "", ""),                        # 4:3 with bars, bigger
    (1560, 720, 1280, 720, 0.6, "STICK=0.8,0.65;FACE=0.2,0.6", ""),   # moved, smaller
    (1080, 2340, 1080, 810, 1.0, "", ""),                         # portrait: the band
    (1080, 2340, 1080, 810, 1.6, "", "START=0.5,0.5"),            # the band holds them back
    (720, 1280, 720, 540, 1.0, "STICK=0.9,0.9", ""),              # a short band shrinks them
    (1812, 2176, 1440, 1080, 1.0, "", "STICK=0.1,0.1"),           # too short a band: over the picture
]

NATIVE = r"""
#include "touch_overlay.h"
#include <cstdio>
#include <cstdlib>
#include <string>
using sa2_touch::Control;
int main(int argc, char** argv) {
    if (argc != 8) return 2;
    const uint32_t w = std::atoi(argv[1]), h = std::atoi(argv[2]);
    sa2_touch::Overlay* o = sa2_touch::create(nullptr, nullptr);
    sa2_touch::set_size(o, w, h);
    sa2_touch::set_frame(o, std::atoi(argv[3]), std::atoi(argv[4]));
    sa2_touch::Settings s;
    std::string why;
    if (!sa2_touch::parse_setting("SA2_TOUCH_SCALE", argv[5], &s, &why)) return 3;
    if (*argv[6] && !sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", argv[6], &s, &why)) return 4;
    if (*argv[7] && !sa2_touch::parse_setting("SA2_TOUCH_LAYOUT_BELOW", argv[7], &s, &why)) return 5;
    sa2_touch::set_settings(o, s);
    float x, y, ax, ay, yx, yy;
    sa2_touch::control_centre(o, w, h, Control::Stick, &x, &y);
    std::printf("%d %.3f %.3f\n", sa2_touch::below_picture(o, w, h) ? 1 : 0, x, y);
    sa2_touch::control_centre(o, w, h, Control::A, &ax, &ay);
    sa2_touch::control_centre(o, w, h, Control::Y, &yx, &yy);
    std::printf("%.3f %.3f\n", ax, (ay + yy) / 2);
    for (Control c : {Control::Start, Control::LT, Control::RT}) {
        sa2_touch::control_centre(o, w, h, c, &x, &y);
        std::printf("%.3f %.3f\n", x, y);
    }
    sa2_touch::destroy(o);
    return 0;
}
"""

JAVA = r"""
package com.sa2recomp.app;
public class LayoutDriver {
    public static void main(String[] a) {
        GameSettings.TouchLayout l = GameSettings.touchLayout(Float.parseFloat(a[0]), Float.parseFloat(a[1]),
                Integer.parseInt(a[2]), Integer.parseInt(a[3]), Float.parseFloat(a[4]), a[5], a[6]);
        System.out.printf(java.util.Locale.US, "%d %.3f %.3f%n", l.below ? 1 : 0, l.x[0], l.y[0]);
        for (int g = 1; g < 5; g++) System.out.printf(java.util.Locale.US, "%.3f %.3f%n", l.x[g], l.y[g]);
    }
}
"""


@pytest.fixture(scope="module")
def drivers(tmp_path_factory):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR, INSTALL / "include/SDL3/SDL.h") if not p.exists()]
    if missing or not GXX:
        pytest.skip(f"needs the JDK, android.jar and the SDL3 install: missing {missing or 'g++'}")
    d = tmp_path_factory.mktemp("touch_layout")
    (d / "native.cpp").write_text(NATIVE)
    here = ROOT / "host/player"
    subprocess.run([GXX, "-std=c++17", "-O1", f"-I{here}", f"-I{INSTALL / 'include'}", "-o", str(d / "native"),
                    str(d / "native.cpp"), str(here / "touch_overlay.cpp"), f"-L{INSTALL / 'lib'}",
                    "-Wl,--disable-new-dtags", f"-Wl,-rpath,{INSTALL / 'lib'}", "-lSDL3"],
                   check=True, capture_output=True)
    src = d / "java/com/sa2recomp/app"
    src.mkdir(parents=True)
    (src / "LayoutDriver.java").write_text(JAVA)
    shutil.copy(ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java", src)
    subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                    "-d", str(d / "classes"), *map(str, src.glob("*.java"))], check=True, capture_output=True)

    def native(*args):
        return subprocess.run([str(d / "native"), *map(str, args)], capture_output=True, text=True,
                              check=True).stdout

    def java(*args):
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{d / 'classes'}:{ANDROID_JAR}",
                               "com.sa2recomp.app.LayoutDriver", *map(str, args)], capture_output=True, text=True,
                              check=True).stdout
    return native, java


def centres(out: str) -> tuple[int, list[tuple[float, float]]]:
    rows = [line.split() for line in out.splitlines()]
    below = int(rows[0][0])
    rows[0] = rows[0][1:]
    return below, [(float(x), float(y)) for x, y in rows]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"{c[0]}x{c[1]}-frame{c[2]}x{c[3]}-scale{c[4]}")
def test_the_editor_places_every_group_where_the_overlay_does(drivers, case):
    native, java = drivers
    want_below, want = centres(native(*case))
    got_below, got = centres(java(*case))
    assert got_below == want_below
    for g, (n, j) in enumerate(zip(want, got)):
        assert abs(n[0] - j[0]) < 0.01 and abs(n[1] - j[1]) < 0.01, (GROUPS[g], n, j)


def test_the_cases_cover_both_areas(drivers):
    native, _ = drivers
    assert {centres(native(*c))[0] for c in CASES} == {0, 1}
