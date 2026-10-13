"""tools/s7/host2_win_build.py: host 2's Windows build, offline on stubs.

DXVK's Linux flags become Windows ones (no -fPIC, no DXVK Native include directory, DXVK's own
Windows defines, the System V bitfield layout). A pinned tarball is taken only at its pin's
sha256. A depfile is read as make writes it. An object is compiled again only when its command
or a file its depfile names changes. No phase starts below 8 GB available. The player's host
contract is SDL3.dll's sha256 and the header's version and revision, the vendor information
held to the pin. The "both define" check refuses a symbol of host 2's own defined by two parts
and passes the standard library's and the named COMDAT. --jobs is 1 to 4.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
import host2_win_build as HB  # noqa: E402


def test_windows_flags_drop_native_and_pic_and_add_dxvk_windows_defines():
    linux = ["-I/b/dxvk/src/d3d9", "-I/s/dxvk/include/native", "-I/s/dxvk/include/native/windows",
             "-I/b/dxvk/include/native", "-D_GLIBCXX_ASSERTIONS=1", "-std=c++17", "-O3", "-msse", "-fPIC",
             "-DDXVK_WSI_SDL3", "-fdiagnostics-color=always", "-pthread"]
    assert HB.windows_flags(linux) == ["-I/b/dxvk/src/d3d9", "-D_GLIBCXX_ASSERTIONS=1", "-std=c++17", "-O3",
                                       "-msse", "-DDXVK_WSI_SDL3", "-DNOMINMAX", "-D_WIN32_WINNT=0xa00",
                                       "-mno-ms-bitfields"]


def _tarball(tmp: Path) -> tuple[Path, str]:
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as t:
        body = b"int x;\n"
        info = tarfile.TarInfo("zlib-9.9/adler32.c")
        info.size = len(body)
        t.addfile(info, io.BytesIO(body))
    path = tmp / "zlib-9.9.tar.gz"
    path.write_bytes(data.getvalue())
    return path, hashlib.sha256(data.getvalue()).hexdigest()


def test_pinned_archive_is_taken_only_at_its_pin(tmp_path):
    path, digest = _tarball(tmp_path)
    good = {"zlib": {"path": path.name, "sha256": digest}}
    assert HB.pinned_archive(good, "zlib", tmp_path) == path
    with pytest.raises(SystemExit, match="not its pin"):
        HB.pinned_archive({"zlib": {"path": path.name, "sha256": "0" * 64}}, "zlib", tmp_path)
    with pytest.raises(SystemExit, match="pins no zlib tarball"):
        HB.pinned_archive({}, "zlib", tmp_path)
    with pytest.raises(SystemExit, match="no zlib tarball"):
        HB.pinned_archive({"zlib": {"path": "absent.tar.gz", "sha256": digest}}, "zlib", tmp_path)


def test_depfile_inputs_reads_continuations_and_escaped_spaces(tmp_path):
    dep = tmp_path / "a.d"
    dep.write_text("/o/a.o: /s/a.cpp \\\n  /s/a\\ b.h /z/lib/c++/vector \\\n  /s/c.h\n")
    assert HB.depfile_inputs(dep) == ["/s/a.cpp", "/s/a b.h", "/z/lib/c++/vector", "/s/c.h"]


class FakeCompiler:
    """Stands in for zig: writes the object and a depfile naming the source and one header."""

    def __init__(self, header: Path):
        self.header = header
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd=None, capture_output=True, text=True, env=None, input=None):
        self.calls.append(cmd)
        obj = Path(cmd[cmd.index("-o") + 1])
        dep = Path(cmd[cmd.index("-MF") + 1])
        src = cmd[cmd.index("-c") + 1]
        obj.write_bytes(b"COFF")
        dep.write_text(f"{obj}: {src} \\\n  {self.header}\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _builder(tmp_path, run):
    tools = {"zig": {"path": "zig/zig", "version": "0.15.2", "clang": "20.1.2"}}
    return HB.Builder(tmp_path / "out", 1, tools, run=run, root=tmp_path)


def test_an_object_is_compiled_again_only_when_its_inputs_change(tmp_path):
    src, header = tmp_path / "a.cpp", tmp_path / "a.h"
    src.write_text("#include \"a.h\"\n")
    header.write_text("int a;\n")
    fake = FakeCompiler(header)
    b = _builder(tmp_path, fake)
    obj = tmp_path / "out/obj/a.o"
    assert b.compile_one(b.cxx, ["-O2"], src, obj) is True
    assert b.compile_one(b.cxx, ["-O2"], src, obj) is False
    assert len(fake.calls) == 1
    assert fake.calls[0][:4] == [str(tmp_path / "zig/zig"), "c++", "-target", "x86_64-windows-gnu"]
    header.write_text("int a, b;\n")
    b._hashes.clear()
    assert b.compile_one(b.cxx, ["-O2"], src, obj) is True
    assert b.compile_one(b.cxx, ["-O3"], src, obj) is True
    assert len(fake.calls) == 3


def test_no_phase_starts_below_eight_gigabytes():
    readings, slept = iter([4.0, 7.9, 8.0]), []
    HB.wait_for_memory("t", probe=lambda: next(readings), sleep=slept.append)
    assert slept == [10.0, 10.0]


def _sdl_install(out: Path, vendor: str) -> bytes:
    inc = out / "sdl3/include/SDL3"
    inc.mkdir(parents=True)
    (inc / "SDL_version.h").write_text("#define SDL_MAJOR_VERSION   3\n#define SDL_MINOR_VERSION   4\n"
                                       "#define SDL_MICRO_VERSION   16\n")
    (inc / "SDL_revision.h").write_text(f'#define SDL_VENDOR_INFO "{vendor}"\n'
                                        '#define SDL_REVISION "SDL-release-3.4.16-0-gfa2c02bb6"\n')
    (out / "sdl3/bin").mkdir(parents=True)
    dll = b"MZ stub SDL3.dll"
    (out / "sdl3/bin/SDL3.dll").write_bytes(dll)
    return dll


def test_the_players_contract_is_sdl3_dll_and_the_pinned_revision(tmp_path):
    b = _builder(tmp_path, FakeCompiler(tmp_path / "h"))
    b.tools["sdl3"] = {"version": "3.4.16", "vendor_info": "sa2-recomp sdl3-patches f7d5f0abeaa4"}
    dll = _sdl_install(b.out, "sa2-recomp sdl3-patches f7d5f0abeaa4")
    header = (b.expectations() / "sa2_host_expectations.h").read_text()
    assert header == ("#pragma once\nnamespace sa2_host_expected {\n"
                      f"inline constexpr char sdl_sha256[] = \"{hashlib.sha256(dll).hexdigest()}\";\n"
                      "inline constexpr int sdl_version = 3004016;\n"
                      "inline constexpr char sdl_revision[] = \"SDL-release-3.4.16-0-gfa2c02bb6 "
                      "(sa2-recomp sdl3-patches f7d5f0abeaa4)\";\n}\n")


def test_a_revision_without_the_pinned_vendor_is_refused(tmp_path):
    b = _builder(tmp_path, FakeCompiler(tmp_path / "h"))
    b.tools["sdl3"] = {"version": "3.4.16", "vendor_info": "sa2-recomp sdl3-patches f7d5f0abeaa4"}
    _sdl_install(b.out, "someone else")
    with pytest.raises(SystemExit, match="tools.sdl3.vendor_info"):
        b.expectations()


class FakeNm:
    """nm --defined-only per group of objects, and c++filt, from canned tables."""

    def __init__(self, by_dir: dict[str, list[str]]):
        self.by_dir = by_dir

    def __call__(self, cmd, cwd=None, capture_output=True, text=True, env=None, input=None):
        if cmd[0] == "c++filt":
            names = {"_ZTTNSt3__114basic_ifstreamIcNS_11char_traitsIcEEEE":
                     "VTT for std::__1::basic_ifstream<char, std::__1::char_traits<char> >"}
            out = "\n".join(names.get(n, n) for n in input.splitlines())
            return subprocess.CompletedProcess(cmd, 0, out + "\n", "")
        group = Path(cmd[2]).parent.name
        rows = "\n".join(f"0000000000000000 T {s}" for s in self.by_dir[group])
        return subprocess.CompletedProcess(cmd, 0, rows + "\n", "")


def _groups(tmp_path, player, d9g, translator):
    b = _builder(tmp_path, FakeNm({"player": player, "d9g": d9g, "translator": translator}))
    objs = {}
    for g in ("player", "d9g", "translator"):
        d = b.out / "obj" / g
        d.mkdir(parents=True, exist_ok=True)
        (d / "x.o").write_bytes(b"")
        objs[g] = [d / "x.o"]
    return b, objs["player"]


def test_both_define_refuses_host_2s_own_symbol_twice(tmp_path):
    b, player = _groups(tmp_path, ["_ZN3d9g6ReportC2Ev", "main"], ["_ZN3d9g6ReportC2Ev"], [])
    with pytest.raises(SystemExit, match=r"player and d9g both define \['_ZN3d9g6ReportC2Ev'\]"):
        b.both_define(player)


def test_both_define_passes_the_standard_librarys_and_the_named_comdat(tmp_path):
    std = ["_ZNSt3__16vectorIiNS_9allocatorIiEEED2Ev", "_ZTTNSt3__114basic_ifstreamIcNS_11char_traitsIcEEEE",
           ".text", "__clang_call_terminate", "_ZN3d9g6shader16DeviceFactsQueryD2Ev"]
    b, player = _groups(tmp_path, std, std, std)
    b.both_define(player)


def test_jobs_above_four_are_refused():
    with pytest.raises(SystemExit, match="--jobs is 1 to 4"):
        HB.main(["--jobs", "5", "--only", "deps"])


def test_an_output_is_installed_by_rename_and_only_when_its_bytes_change(tmp_path):
    src, dest = tmp_path / "new.exe", tmp_path / "player.exe"
    src.write_bytes(b"MZ one")
    src.chmod(0o755)
    HB.install(src, dest)
    assert dest.read_bytes() == b"MZ one" and dest.stat().st_mode & 0o111   # mode kept
    inode = dest.stat().st_ino
    HB.install(src, dest)                       # the same bytes: the file a run maps is kept
    assert dest.stat().st_ino == inode
    src.write_bytes(b"MZ two")
    HB.install(src, dest)                       # new bytes: a new file, the old inode untouched
    assert dest.read_bytes() == b"MZ two" and dest.stat().st_ino != inode
    assert not (tmp_path / "player.exe.tmp").exists()


def test_the_link_is_reproducible():
    assert HB.LINK_REPRO == ["-Wl,/Brepro"]
