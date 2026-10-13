"""tools/s8/android_app.py: the Android app's build-time pieces that need no SDK.

The stream cut the device's renderer check plays, the portable copies of the player's and the
renderer's sources (each x86 or glibc site under a test, each anchor still found in the tree), and
the contract between the app's Java and its native entry point."""
from __future__ import annotations

import importlib.util
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())


def load():
    spec = importlib.util.spec_from_file_location("android_app", ROOT / "tools/s8/android_app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["android_app"] = mod
    spec.loader.exec_module(mod)
    return mod


A = load()


def stream_bytes(records: list[tuple[int, bytes]]) -> bytes:
    """An SA2D3D9S stream: the 16-byte file header (its last word the header's length, 16 + a
    4-padded JSON), then [kind, bytes, seq] records."""
    js = b'{"schema": "sa2-d3d9-stream/v1"}'
    js += b" " * (-len(js) % 4)
    out = b"SA2D3D9S" + struct.pack("<II", 1, 16 + len(js)) + js
    for seq, (kind, body) in enumerate(records):
        out += struct.pack("<III", kind, 12 + len(body), seq) + body
    return out


def test_cut_stream_keeps_the_header_and_records_through_the_nth_frame(tmp_path):
    frame = A.stream_kinds()["SA2S_FRAME"]
    call = A.stream_kinds()["SA2S_CALL"]
    recs = [(call, b"\x01" * 8), (frame, b""), (call, b"\x02" * 4), (call, b"\x03" * 4), (frame, b""),
            (call, b"\x04" * 4), (frame, b"")]
    src = tmp_path / "s.sa2s"
    src.write_bytes(stream_bytes(recs))
    dest = tmp_path / "cut.sa2s"
    r = A.cut_stream(src, dest, 2)
    assert r == {"frames": 2, "records": 5, "bytes": len(stream_bytes(recs[:5]))}
    assert dest.read_bytes() == stream_bytes(recs[:5])


def test_cut_stream_past_the_end_copies_the_whole_stream(tmp_path):
    frame = A.stream_kinds()["SA2S_FRAME"]
    src = tmp_path / "s.sa2s"
    src.write_bytes(stream_bytes([(frame, b""), (frame, b"")]))
    r = A.cut_stream(src, tmp_path / "cut.sa2s", 10)
    assert (r["frames"], r["records"]) == (2, 2)
    assert (tmp_path / "cut.sa2s").read_bytes() == src.read_bytes()


def test_cut_stream_refuses_a_file_that_is_no_stream(tmp_path):
    src = tmp_path / "x.bin"
    src.write_bytes(b"not a stream at all")
    with pytest.raises(SystemExit, match="no SA2D3D9S stream"):
        A.cut_stream(src, tmp_path / "cut.sa2s", 1)


@pytest.mark.parametrize("rel", sorted(A.PORTABLE))
def test_every_portable_site_still_matches_the_tree(rel):
    """Each listed file's copy compiles for arm64: every site is either ported by its owner (a
    marker present) or found exactly once and put under its test; none is silently skipped."""
    text = (ROOT / rel).read_text()
    out = A.arm_portable(rel, text)
    for site in A.PORTABLE[rel]:
        old, new = site[0], site[1]
        markers = site[2] if len(site) > 2 else A.X86_PORTED
        optional = len(site) > 3 and site[3]
        if any(m in text for m in markers) or (optional and old not in text):
            continue
        assert new in out, f"{rel}: {old.splitlines()[0]!r} was not rewritten"


def test_portable_copies_hide_every_x86_instruction_from_arm():
    """No fnstcw, fldcw, stmxcsr or _mm_[gs]etcsr is left outside an x86 test in the copies."""
    for rel in A.PORTABLE:
        out = A.arm_portable(rel, (ROOT / rel).read_text())
        depth, x86 = 0, []
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("#if"):
                x86.append("__x86_64__" in s or "__i386__" in s)
            elif s.startswith("#elif") or s.startswith("#else"):
                if x86:
                    x86[-1] = False
            elif s.startswith("#endif"):
                if x86:
                    x86.pop()
            elif re.search(r"fnstcw|fldcw|stmxcsr|_mm_getcsr|_mm_setcsr|cpuid\.h|immintrin", s):
                assert any(x86), f"{rel}: {s!r} outside an x86 test"


def test_a_moved_anchor_refuses_the_build():
    rel = "host/d3d9gpu/d9g_report.cpp"
    with pytest.raises(SystemExit, match="occurs 0 times"):
        A.arm_portable(rel, "uint16_t x87_cw() { return 0; }\n")


def test_an_owner_port_is_left_alone():
    rel = "host/d3d9gpu/d9g_report.cpp"
    text = (ROOT / rel).read_text() + "\n#if defined(__aarch64__)\n#endif\n"
    assert A.arm_portable(rel, text) == text


def test_java_entry_point_is_the_native_session_export():
    """Sa2Activity names sa2_android_main in libmain.so; the session defines it extern "C" with
    default visibility, and loads SDL3 then main (SDL's convention)."""
    java = (ROOT / "host/android/java/com/sa2recomp/app/Sa2Activity.java").read_text()
    native = (ROOT / "host/android/native/sa2_android_session.cpp").read_text()
    assert re.search(r'getMainFunction\(\)\s*\{\s*return "sa2_android_main";', java)
    assert re.search(r'return new String\[\] \{"SDL3", "main"\};', java)
    assert re.search(r'extern "C" __attribute__\(\(visibility\("default"\)\)\) int sa2_android_main\(int argc, char\*\* argv\)',
                     native)


def test_manifest_package_sdk_and_native_library_extraction():
    m = (ROOT / "host/android/AndroidManifest.xml").read_text()
    assert 'package="com.sa2recomp.app"' in m
    assert 'android:extractNativeLibs="true"' in m           # contract D: the game is exec'd from the library folder
    assert 'android:name=".Sa2Activity"' in m and 'android:process=":play"' in m
    assert 'android:screenOrientation="sensorLandscape"' in m
    assert (A.PACKAGE, A.MIN_SDK, A.TARGET_SDK) == ("com.sa2recomp.app", 29, 35)


def test_installer_and_session_agree_on_the_app_folders():
    """The setup screen runs the installer with the environment the session sets, so both read
    one config (host/install/sa2_platform_posix.cpp: $XDG_CONFIG_HOME/sa2recomp/sa2recomp.cfg)."""
    java = (ROOT / "host/android/java/com/sa2recomp/app/Sa2SetupActivity.java").read_text()
    native = (ROOT / "host/android/native/sa2_android_session.cpp").read_text()
    assert 'new File(getFilesDir(), "config/sa2recomp/sa2recomp.cfg")' in java
    for var, sub in (("HOME", None), ("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data")):
        assert f'env.put("{var}"' in java
        if sub:
            assert f'setenv("{var}", (a.files + "/{sub}").c_str(), 1);' in native
            assert f'new File(files, "{sub}")' in java
        else:
            assert f'setenv("{var}", a.files.c_str(), 1);' in native


# ------------------------------------------------------------------ the session's CPU placement

PLAN_DRIVER = r"""
#include "sa2_cpu_plan.h"
#include <cstdio>
#include <cstring>
int main(int argc, char** argv) {
    if (argc == 3 && !std::strcmp(argv[1], "parse")) {
        cpu_set_t s;
        if (!sa2cpu::parse_list(argv[2], s)) { std::puts("refused"); return 0; }
        std::vector<int> cpus;
        for (int c = 0; c < CPU_SETSIZE; c++) if (CPU_ISSET(c, &s)) cpus.push_back(c);
        std::puts(sa2cpu::list_of(cpus).c_str());
        return 0;
    }
    if (argc == 4 && !std::strcmp(argv[1], "clocks")) {
        std::printf("[%s]\n", sa2cpu::clocks_mhz(argv[2], argv[3]).c_str());
        return 0;
    }
    cpu_set_t allowed;
    if (argc != 3 || !sa2cpu::parse_list(argv[2], allowed)) return 2;
    const sa2cpu::Plan p = sa2cpu::plan(argv[1], allowed);
    std::printf("%s|%s|%s\n", p.guest.c_str(), p.player.c_str(), p.why.c_str());
    return 0;
}
"""


@pytest.fixture(scope="module")
def plan_driver(tmp_path_factory):
    import shutil
    import subprocess
    cxx = shutil.which("g++") or shutil.which("c++")
    if not cxx:
        pytest.fail("no host C++ compiler for host/android/native/sa2_cpu_plan.h")
    d = tmp_path_factory.mktemp("cpu_plan")
    (d / "driver.cpp").write_text(PLAN_DRIVER)
    exe = d / "driver"
    done = subprocess.run([cxx, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                           f"-I{ROOT / 'host/android/native'}", str(d / "driver.cpp"), "-o", str(exe)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr

    def run(*args: str) -> str:
        return subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True,
                              check=True).stdout.strip()
    return run


def fake_sysfs(root: Path, kind: str, values: dict[int, int]) -> Path:
    for cpu, v in values.items():
        f = root / f"cpu{cpu}" / kind
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"{v}\n")
    return root


@pytest.mark.parametrize("values, allowed, guest, player", [
    # the user's phone (Snapdragon 8 Elite): six cores to 3.53 GHz, two to 4.32 GHz; one prime
    # core each (the phone, 2026-10-12: the player's render thread 6.1 ms a frame on a prime core
    # against 11-13 ms on the performance cores)
    ({**{c: 3532800 for c in range(6)}, 6: 4320000, 7: 4320000}, "0-7", "7", "6"),
    # three clusters: the one fastest core is the guest's, the next tier the player's, the
    # slowest no one's
    ({0: 2265600, 1: 2265600, **{c: 3014400 for c in range(2, 7)}, 7: 3302400}, "0-7", "7", "2-6"),
    # a CPU the process may not run on is no one's
    ({**{c: 3532800 for c in range(6)}, 6: 4320000, 7: 4320000}, "0-6", "6", "0-5"),
])
def test_the_guest_gets_a_top_cpu_and_the_player_another_or_the_next_tier(
        plan_driver, tmp_path, values, allowed, guest, player):
    root = fake_sysfs(tmp_path / "cpu", "cpufreq/cpuinfo_max_freq", values)
    g, p, why = plan_driver(root, allowed).split("|")
    assert (g, p) == (guest, player), why
    assert why.startswith("cpufreq/cpuinfo_max_freq ") and why.endswith(f"the guest on {guest}, the player on {player}")


def test_cpu_capacity_answers_when_no_cpu_has_cpufreq(plan_driver, tmp_path):
    root = fake_sysfs(tmp_path / "cpu", "cpu_capacity", {0: 512, 1: 512, 2: 512, 3: 512, 4: 1024, 5: 1024})
    assert plan_driver(root, "0-5").split("|")[:2] == ["5", "4"]


def test_nothing_is_placed_when_every_cpu_tops_out_alike_or_none_reads(plan_driver, tmp_path):
    # the emulator's four CPUs all read 2
    alike = fake_sysfs(tmp_path / "alike", "cpufreq/cpuinfo_max_freq", {c: 2 for c in range(4)})
    assert plan_driver(alike, "0-3") == "||cpufreq/cpuinfo_max_freq 2 on 0-3; every CPU tops out alike, so nothing is placed"
    (tmp_path / "none").mkdir()
    assert plan_driver(tmp_path / "none", "0-3") == \
        "||no CPU's cpuinfo_max_freq or cpu_capacity reads, so nothing is placed"


def test_the_perf_lines_clocks_are_each_cpus_scaling_cur_freq_in_mhz(plan_driver, tmp_path):
    """The session's 30 s perf line reads the clock of every CPU the guest's threads and the
    player's may run on (sa2_cpu_plan.h clocks_mhz): kHz to the nearest MHz, in CPU order, "?"
    for a CPU whose file does not read, nothing for a list that does not read."""
    root = fake_sysfs(tmp_path / "cpu", "cpufreq/scaling_cur_freq",
                      {0: 1785600, 1: 384000, 2: 3532800, 4: 3532800, 6: 4320000, 7: 4319499})
    assert plan_driver("clocks", root, "6-7") == "[6:4320 7:4319]"
    assert plan_driver("clocks", root, "0-5") == "[0:1786 1:384 2:3533 3:? 4:3533 5:?]"
    assert plan_driver("clocks", root, "7,0") == "[0:1786 7:4319]"
    assert plan_driver("clocks", root, "") == "[]" and plan_driver("clocks", root, "6-") == "[]"
    assert plan_driver("clocks", tmp_path / "none", "0-1") == "[0:? 1:?]"


@pytest.mark.parametrize("text, read", [("6,7", "6-7"), ("0-5", "0-5"), ("0-3,6", "0-3,6"), ("7", "7"),
                                        ("", "refused"), ("7-6", "refused"), ("6,", "6"),
                                        ("a", "refused"), ("1-x", "refused")])
def test_a_cpu_list_reads_as_the_runtimes_knob_reads_it(plan_driver, text, read):
    assert plan_driver("parse", text) == read
