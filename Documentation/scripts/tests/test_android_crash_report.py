"""The Android crash report's kernel line (host/android/java/com/sa2recomp/app/CrashReport.java).

The report a player copies names the device's kernel (os.version, uname's release) under the
Android line: a pipe between the game and the relay could lose a writer's wakeup on Linux 5.8 to
6.7 without the fix of early 2024 (host/launch/sa2_relay.h, "Pace"), which stalled an AYN Thor (a
5.15 kernel) at the SEGA logo. A small Java driver runs CrashReport.kernelLine here against
android.jar, with a stand-in for the one Sa2SetupActivity method CrashReport calls, so CrashReport
itself is compiled as the APK compiles it.

Needs the pinned JDK (tools/pins.json java) and the Android SDK's android.jar; it is skipped
without them.
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JDK = ROOT / json.loads((ROOT / "tools/pins.json").read_text())["tools"]["java"]["path"]
ANDROID_JAR = ROOT / "private/android-sdk/platforms/android-36/android.jar"
APP = ROOT / "host/android/java/com/sa2recomp/app"

STAND_IN = """
package com.sa2recomp.app;
final class Sa2SetupActivity {
    static String readText(java.io.File f) { return null; }
}
"""
# argv: last-session.txt texts; prints Outcome.of's clean flag and text, one line each.
OUTCOME_DRIVER = r"""
package com.sa2recomp.app;
public class OutcomeDriver {
    public static void main(String[] a) {
        for (String v : a) {
            CrashReport.Outcome o = CrashReport.Outcome.of(v);
            System.out.println((o.clean ? "clean" : "banner") + " | " + o.what);
        }
    }
}
"""
# argv: values for kernelLine; "-" is null, "@" this JVM's os.version.
DRIVER = r"""
package com.sa2recomp.app;
public class KernelDriver {
    public static void main(String[] a) {
        for (String v : a)
            System.out.println(CrashReport.kernelLine(v.equals("-") ? null : v.equals("@") ? System.getProperty("os.version") : v));
    }
}
"""


@pytest.fixture(scope="module")
def kernel_line(tmp_path_factory):
    missing = [str(p) for p in (JDK / "bin/javac", ANDROID_JAR) if not p.exists()]
    if missing:
        pytest.skip(f"needs the JDK and android.jar: missing {missing}")
    d = tmp_path_factory.mktemp("crash-report")
    src = d / "java/com/sa2recomp/app"
    src.mkdir(parents=True)
    (src / "Sa2SetupActivity.java").write_text(STAND_IN)
    (src / "KernelDriver.java").write_text(DRIVER)
    (src / "OutcomeDriver.java").write_text(OUTCOME_DRIVER)
    for name in ("CrashReport", "Tombstone", "VulkanSupport"):
        shutil.copy(APP / f"{name}.java", src)
    done = subprocess.run([str(JDK / "bin/javac"), "--release", "11", "-nowarn", "-classpath", str(ANDROID_JAR),
                           "-d", str(d / "classes"), *map(str, src.glob("*.java"))], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr

    def run(*values: str, driver: str = "KernelDriver") -> list[str]:
        return subprocess.run([str(JDK / "bin/java"), "-cp", f"{d / 'classes'}:{ANDROID_JAR}",
                               f"com.sa2recomp.app.{driver}", *values], capture_output=True, text=True,
                              check=True).stdout.splitlines()
    return run


def test_the_kernel_line_is_the_release_uname_gives(kernel_line):
    assert kernel_line("@") == [f"kernel: {platform.release()}"]


def test_a_missing_or_blank_version_says_so_and_spaces_are_trimmed(kernel_line):
    assert kernel_line("-", "  ", " 5.15.78-android13-8-g0123abc ") == [
        "kernel: (not given)", "kernel: (not given)", "kernel: 5.15.78-android13-8-g0123abc"]


def test_the_report_puts_the_kernel_under_the_android_line():
    text = (APP / "CrashReport.java").read_text()
    android = text.index('out.append("android: ")')
    kernel = text.index('out.append(kernelLine(System.getProperty("os.version"))).append(\'\\n\');')
    gpu = text.index('out.append("gpu: ")')
    assert android < kernel < gpu


# How a Back exit reads (2026-10-18): some Back exits on an Android emulator (1 of 16 on build 10015) ended
# with the game killed by SIGSEGV after it had closed every record. The session now says so on its "ended:"
# line, but the banner still shows. The stall watch caused that signal, and it ends before the exit now
# (runtime/sa2_stall.h): a build that writes the note cannot have that cause, so a signal there is another
# fault, to be reported.
ENDED = "ended: the player asked for the end; the game stopped by {how} (status {status}); the player exited {rc}{note}"
NOTE = "; the game had closed its records"


def outcome(kernel_line, how="SIGTERM", status=0, rc=0, note="", asked=True):
    line = ENDED.format(how=how, status=status, rc=rc, note=note)
    if not asked:
        line = line.replace("the player asked for the end", "the player ended")
    return kernel_line("perf over the session: 10 frames\n" + line + "\nOK the session ended", driver="OutcomeDriver")


def test_a_back_exit_with_status_0_is_clean(kernel_line):
    assert outcome(kernel_line) == ["clean | ended with Back"]


def test_a_sigsegv_after_the_records_were_closed_still_shows_the_banner(kernel_line):
    crash = ["banner | the game was killed by signal 11 (SIGSEGV)"]
    assert outcome(kernel_line, status=-11, note=NOTE) == crash
    assert outcome(kernel_line, status=-11) == crash
    assert outcome(kernel_line, status=-11, note=NOTE, rc=4) == crash
    assert outcome(kernel_line, status=-11, note=NOTE, asked=False) == crash


def test_other_ends_after_the_records_were_closed_keep_their_reading(kernel_line):
    assert outcome(kernel_line, status=-15, note=NOTE) == ["clean | ended with Back"]
    assert outcome(kernel_line, status=3, note=NOTE) == ["banner | the game crashed (exit 3, a fault outside the game's code)"]
    assert outcome(kernel_line, how="SIGKILL", status=-9, note=NOTE)[0].startswith(
        "banner | the game stopped responding and had to be killed")
