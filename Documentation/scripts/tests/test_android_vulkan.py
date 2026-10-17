"""What the Android app says about Vulkan, held to literal versions.

The renderer needs Vulkan 1.1, and below 1.3 it runs its Vulkan 1.1 profile, which it chooses from the
driver's own version. host/android/java/com/sa2recomp/app/VulkanSupport.java holds the version
arithmetic and the texts the setup screen and the crash report show: only a declared version below 1.1
is warned about, and the crash report names the 1.1 profile only from the player's own record
(d3d9gpu.runrec's sdl_gpu "vk11" member), never from the version the system declares. A small driver
runs those functions on the JVM against android.jar for the versions 1.0.61, 1.1.128, 1.2.199, 1.3.0 and
none.

android.jar's org.json classes are stubs that throw on a desktop JVM, so the driver is compiled and run
with a test double of org.json.JSONObject first on the class path. It has the four methods
VulkanSupport reads, with Android's own semantics (libcore's JSONObject): opt-JSONObject returns the
value only when it is an object, isNull is true for a missing name or JSONObject.NULL, and optString
returns the fallback only for a missing name ("null" for JSONObject.NULL), so a reader must test isNull.

Needs the pinned JDK (tools/pins.json java) and the Android SDK's android.jar; it is skipped without them.
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

JSON_DOUBLE = r"""
package org.json;
public class JSONObject {
    public static final Object NULL = new Object() { @Override public String toString() { return "null"; } };
    private final java.util.Map<String, Object> values = new java.util.LinkedHashMap<>();
    public JSONObject put(String name, Object value) {
        if (value == null) values.remove(name); else values.put(name, value);
        return this;
    }
    public Object opt(String name) { return values.get(name); }
    public boolean isNull(String name) { Object v = opt(name); return v == null || v == NULL; }
    public JSONObject optJSONObject(String name) { Object v = opt(name); return v instanceof JSONObject ? (JSONObject) v : null; }
    public String optString(String name, String fallback) { Object v = opt(name); return v == null ? fallback : String.valueOf(v); }
}
"""

DRIVER = r"""
package com.sa2recomp.app;
import org.json.JSONObject;
public class VulkanDriver {
    static int version(String text) {
        if (text.equals("none")) return -1;
        String[] p = text.split("\\.");
        return (Integer.parseInt(p[0]) << 22) | (Integer.parseInt(p[1]) << 12) | Integer.parseInt(p[2]);
    }
    public static void main(String[] a) {
        if (a[0].equals("version")) {
            int v = version(a[1]), level = Integer.parseInt(a[2]);
            System.out.println(VulkanSupport.atLeast11(v));
            System.out.println(VulkanSupport.shortText(v));
            System.out.println(VulkanSupport.declaredText(v, level));
            System.out.print("[" + VulkanSupport.setupNote(v) + "]");
            return;
        }
        // profile [none | object] [device api version | -] [forced | null | -]
        JSONObject sdl = new JSONObject().put("driver", "vulkan");
        if (!a[1].equals("none")) {
            JSONObject vk11 = new JSONObject().put("api_version", "1.1").put("reason", "a reason");
            if (!a[2].equals("-")) vk11.put("device_api_version", a[2]);
            if (a[3].equals("null")) vk11.put("forced", JSONObject.NULL);
            else if (!a[3].equals("-")) vk11.put("forced", a[3]);
            sdl.put("vk11", vk11);
        }
        System.out.print("[" + VulkanSupport.profileText(sdl) + "]");
        System.out.print("[" + VulkanSupport.profileText(null) + "]");
    }
}
"""


@pytest.fixture(scope="module")
def java(tmp_path_factory):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR) if not p.exists()]
    if missing:
        pytest.skip(f"needs the JDK and android.jar: missing {missing}")
    d = tmp_path_factory.mktemp("android_vulkan")
    src = d / "java"
    (src / "com/sa2recomp/app").mkdir(parents=True)
    (src / "org/json").mkdir(parents=True)
    (src / "org/json/JSONObject.java").write_text(JSON_DOUBLE)
    (src / "com/sa2recomp/app/VulkanDriver.java").write_text(DRIVER)
    shutil.copy(ROOT / "host/android/java/com/sa2recomp/app/VulkanSupport.java", src / "com/sa2recomp/app")
    subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                    "-d", str(d / "classes"), *map(str, src.rglob("*.java"))], check=True, capture_output=True)

    def run(*args):
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{d / 'classes'}:{ANDROID_JAR}",
                               "com.sa2recomp.app.VulkanDriver", *map(str, args)], capture_output=True, text=True,
                              check=True).stdout
    return run


WARN = ". OpenSA2's renderer needs Vulkan 1.1 or later, so the game will probably not start on this phone."


@pytest.mark.parametrize("declared, level, at_least_11, short, text, note", [
    ("1.0.61", 0, "false", "1.0", "1.0.61, level 0 (the renderer needs 1.1)",
     "\n\nNote: this phone's graphics driver reports Vulkan 1.0" + WARN),
    ("1.1.128", 1, "true", "1.1", "1.1.128, level 1", ""),
    ("1.2.199", -1, "true", "1.2", "1.2.199", ""),
    ("1.3.0", 1, "true", "1.3", "1.3.0, level 1", ""),
    ("none", -1, "false", "none", "none declared (the renderer needs Vulkan 1.1)",
     "\n\nNote: this phone's graphics driver declares no Vulkan support" + WARN),
])
def test_only_a_version_below_1_1_is_warned_about(java, declared, level, at_least_11, short, text, note):
    out = java("version", declared, level)
    lines = out.split("\n", 3)
    assert lines[:3] == [at_least_11, short, text]
    assert lines[3] == f"[{note}]"


@pytest.mark.parametrize("vk11, device, forced, profile", [
    ("none", "-", "-", ""),                                                     # the 1.3 profile: the line as before
    ("object", "1.1.128", "null", "; Vulkan 1.1 profile (the device reports Vulkan 1.1.128)"),
    ("object", "1.4.329", "debug.sa2.vk11=1",
     "; Vulkan 1.1 profile (the device reports Vulkan 1.4.329; forced by debug.sa2.vk11=1)"),
    ("object", "-", "SA2_D3D9GPU_VK11=1", "; Vulkan 1.1 profile (forced by SA2_D3D9GPU_VK11=1)"),
    ("object", "-", "-", "; Vulkan 1.1 profile"),
])
def test_the_crash_report_names_the_profile_from_the_players_record(java, vk11, device, forced, profile):
    assert java("profile", vk11, device, forced) == f"[{profile}][]"
