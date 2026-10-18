#!/usr/bin/env python3
"""host2_win_build: build host 2 (the renderer on SDL3's GPU API) for x86-64 Windows with zig.

Subtitle: host/d3d9gpu/build.sh's sa2_player_gpu, and the shader translator's command line
(d3d9gpu_shaderc), for the triple x86_64-windows-gnu. Everything is compiled by the pinned zig
(tools/pins.json tools.zig: clang 20 with libc++ and mingw-w64's UCRT headers), from pinned
sources only, into --out (default private/generated/s7/host2-win):

  deps        zlib (tools.zlib), libpng (tools.libpng, its prebuilt pnglibconf.h) and zstd's
              decompressor (tools.zstd; the player reads zstd, it never writes it), each a
              static archive under deps/, their headers under deps/include
  sdl3        the pinned SDL3 tarball (tools.sdl3) with patches/sdl3 applied by
              tools/s5/sdl3_patches.py, configured by the pinned cmake and ninja through a zig
              toolchain file, SDL_VENDOR_INFO tools.sdl3.vendor_info as on Linux: SDL3.dll and
              its import library, installed under sdl3/
  dxbc        dxbc-spirv, every unit DXVK's own build compiled for its archive (DXVK's
              compile_commands.json, tools/s5/build_host_deps.sh), with that build's flags for
              Windows (windows_flags): lib/libdxbc_spv.a
  translator  host/d3d9gpu/shader/build_translator.sh's units for Windows: DXVK's lowering pass
              cut from the pinned checkout (extract_dxvk_shader.py lower), DXVK's analysis,
              constant copy and shader I/O units, and host 2's d9g_shader*.cpp, with DXVK's
              flags for Windows and D9G_TRANSLATOR_ID from extract_dxvk_shader.py identity
              (the compiler named as gen/zcxx, a wrapper of the pinned zig): lib/libd9g_shader.a
  d9g         host/d3d9gpu's device units against mingw-w64's windows.h and d3d9.h (no DXVK
              Native header), gen_com.py's COM stubs made from the pinned DXVK's d3d9.h as on
              Linux: lib/libsa2_d3d9gpu.a
  player      host/player's units with SA2_PLAYER_GPU=1 and the clear matrix's static leg,
              linked with the three archives, SDL3's import library and the deps:
              bin/sa2_player_gpu.exe, with SDL3.dll beside it; and bin/d3d9gpu_shaderc.exe
              (test/shaderc.cpp), the translator from the command line

The player's host contract (gen/expected-gpu/sa2_host_expectations.h) is written here, in the
format host/player/generate_expectations.py writes: SDL3.dll's sha256, the header's version
(held to tools.sdl3.version) and its revision with the vendor information (held to
tools.sdl3.vendor_info). generate_expectations.py itself reads the Linux install's pins.

Every C++ unit of host 2 (device, translator, DXVK's, dxbc-spirv's, player) is compiled with
-mno-ms-bitfields, the System V bitfield layout the Linux build has, so no struct differs between
the two builds; the C libraries keep mingw's default. DXVK's flags for Windows are its Linux
build's minus -fPIC and DXVK Native's include/native directories, plus DXVK's own Windows
defines (-DNOMINMAX -D_WIN32_WINNT=0xa00, its meson.build). -D_GLIBCXX_ASSERTIONS=1 stays and is
inert under libc++.

Each object is compiled with a depfile; an object whose command and every file its depfile
names hash as they did when it was made is not compiled again (obj/*.stamp). A source tarball is
extracted afresh when its directory is missing, and the SDL build is redone when its stamp (the
tarball, the patches, the vendor string and the configure line) differs.

Memory (AGENTS.md; the user's rule of 2026-10-11): at most --jobs compiles at once (default 4),
and no phase starts while less than 8 GB is available; it waits.

Usage:
  .venv/bin/python tools/s7/host2_win_build.py [--out DIR] [--jobs 4] [--only PHASE ...]
Writes --out/host2_win_build.runrec (JSON): the pinned inputs by sha256, each phase's seconds
and object counts, and the outputs by sha256. Prints HOST2_WIN_BUILD_OK and the player's sha256 on
success (HOST2_WIN_BUILD_PHASES_OK with --only).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
TRIPLE = "x86_64-windows-gnu"
PHASES = ("deps", "sdl3", "dxbc", "translator", "d9g", "player")
DEFAULT_OUT = "private/generated/s7/host2-win"
MIN_AVAILABLE_GB = 8.0
# A reproducible PE: /Brepro puts a hash of the image where the link time and the debug id
# were, so a relink of unchanged objects to the same name gives the same bytes (link() always
# links to one scratch name). Not -s: without debug information lld also drops and folds code.
LINK_REPRO = ["-Wl,/Brepro"]
H = ROOT / "host/d3d9gpu"
P = ROOT / "host/player"
CC_JSON_REL = "private/s5-tools/build/dxvk/compile_commands.json"
EXTRACT = H / "shader/extract_dxvk_shader.py"

ZLIB_SOURCES = ("adler32", "compress", "crc32", "deflate", "gzclose", "gzlib", "gzread", "gzwrite", "infback",
                "inffast", "inflate", "inftrees", "trees", "uncompr", "zutil")
PNG_SOURCES = ("png", "pngerror", "pngget", "pngmem", "pngpread", "pngread", "pngrio", "pngrtran", "pngrutil",
               "pngset", "pngtrans", "pngwio", "pngwrite", "pngwtran", "pngwutil")
# host/d3d9gpu/build.sh's CORE, then the SDL_gpu half, as libsa2_d3d9gpu.a holds them.
D9G_UNITS = ("d9g_device_draw", "d9g_draw_state", "d9g_device", "d9g_resources", "d9g_format", "d9g_clear",
             "d9g_report", "d9g_pipeline", "d9g_sampler", "d9g_draw", "d9g_gpu_sdl", "d9g_entry", "d9g_props")
# shader/build_translator.sh's TUS.
TRANSLATOR_UNITS = ("d9g_shader", "d9g_shader_abi", "d9g_shader_link", "d9g_shader_cache", "d9g_shader_shims",
                    "d9g_shader_profile", "d9g_shader_vulkan")
PLAYER_UNITS = ("player", "input", "mixer", "stream", "sha256", "texcodec", "touch_overlay", "tweaks")
SDL_CMAKE_OPTIONS = ("-DCMAKE_BUILD_TYPE=Release", "-DSDL_SHARED=ON", "-DSDL_STATIC=OFF", "-DSDL_TESTS=OFF",
                     "-DSDL_EXAMPLES=OFF", "-DSDL_TEST_LIBRARY=OFF")
# host/d3d9gpu/build.sh's FLAGS and PFLAGS less -g, with clang's warning about mingw-w64's COM
# declarations' exception specifications off, as DXVK's meson.build turns it off for clang.
WARN = ("-std=c++17", "-O2", "-mno-ms-bitfields", "-Wall", "-Wextra", "-Wno-unused-parameter",
        "-Wno-microsoft-exception-spec")
# DXVK's own compiler arguments on Windows (its meson.build, `platform == 'windows'`).
DXVK_WINDOWS_DEFINES = ("-DNOMINMAX", "-D_WIN32_WINNT=0xa00")


class Refused(SystemExit):
    def __init__(self, why: str):
        super().__init__(f"host2_win_build: {why}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pins(root: Path = ROOT) -> dict:
    return json.loads((root / "tools/pins.json").read_text())["tools"]


def pinned_archive(tools: dict, name: str, root: Path = ROOT) -> Path:
    """tools.<name>'s tarball, refused unless it is a regular file hashing to its pin."""
    entry = tools.get(name)
    if not isinstance(entry, dict) or not entry.get("path") or not entry.get("sha256"):
        raise Refused(f"tools/pins.json pins no {name} tarball (path and sha256)")
    path = root / entry["path"]
    if not path.is_file():
        raise Refused(f"no {name} tarball at {path}")
    got = sha256(path)
    if got != entry["sha256"]:
        raise Refused(f"{path} hashes to {got}, not its pin {entry['sha256']} (tools/pins.json {name})")
    return path


def windows_flags(linux_flags: list[str]) -> list[str]:
    """DXVK's Linux compile flags (extract_dxvk_shader.py flags) made Windows ones: no -fPIC, no
    DXVK Native include directory (include/native: its windows.h and d3d9.h stand-ins; mingw-w64's
    are the real ones), DXVK's Windows defines, and the System V bitfield layout."""
    out = []
    for flag in linux_flags:
        if flag in ("-fPIC", "-pthread", "-Winvalid-pch") or flag.startswith("-fdiagnostics-color"):
            continue
        if flag.startswith("-I") and "/native" in flag[2:].replace("\\", "/"):
            continue
        out.append(flag)
    return out + [d for d in DXVK_WINDOWS_DEFINES if d not in out] + ["-mno-ms-bitfields"]


def available_gb() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1 << 20)
    except OSError:
        pass
    return float("inf")


def wait_for_memory(what: str, need_gb: float = MIN_AVAILABLE_GB, poll_s: float = 10.0,
                    probe=available_gb, sleep=time.sleep) -> None:
    while (gb := probe()) < need_gb:
        print(f"{what}: {gb:.1f} GB available, waiting for {need_gb:.0f} GB", flush=True)
        sleep(poll_s)


def install(src: Path, dest: Path) -> None:
    """dest becomes src's bytes, by rename, and only when they differ: a running player keeps
    the file it mapped (an in-place copy truncates it, and the run dies mid-stream)."""
    if dest.is_file() and sha256(dest) == sha256(src):
        return
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copy(src, tmp)
    os.replace(tmp, dest)


def depfile_inputs(dep: Path) -> list[str]:
    """The files a make-style depfile names (the target's prerequisites)."""
    text = dep.read_text().replace("\\\n", " ")
    _, _, rest = text.partition(": ")
    if not rest:
        _, _, rest = text.partition(":\n")
    names, cur, esc = [], [], False
    for ch in rest:
        if esc:
            cur.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch.isspace():
            if cur:
                names.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        names.append("".join(cur))
    return names


class Builder:
    def __init__(self, out: Path, jobs: int, tools: dict, run=subprocess.run, root: Path = ROOT):
        self.root = root
        self.out = out
        self.jobs = jobs
        self.tools = tools
        self.run = run
        self.zig = str(root / tools["zig"]["path"])
        self.cc = [self.zig, "cc", "-target", TRIPLE]
        self.cxx = [self.zig, "c++", "-target", TRIPLE]
        self.record: dict = {"schema": "sa2-recomp-s7-host2-win-build/v1", "triple": TRIPLE,
                             "zig": {"path": self.zig, "version": tools["zig"].get("version"),
                                     "clang": tools["zig"].get("clang")},
                             "inputs": {}, "phases": {}, "outputs": {}}
        self._hashes: dict[str, str] = {}
        for d in ("src", "obj", "lib", "bin", "gen", "deps/include"):
            (out / d).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ plumbing
    def sh(self, cmd: list[str], *, cwd: Path | None = None, log: Path | None = None, env: dict | None = None,
           input_text: str | None = None) -> str:
        p = self.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                     env=env, input=input_text)
        if log is not None:
            log.write_text(" ".join(shlex.quote(str(c)) for c in cmd) + "\n\n" + (p.stdout or "") + (p.stderr or ""))
        if p.returncode != 0:
            tail = ((p.stderr or "") + (p.stdout or ""))[-2000:]
            raise Refused(f"{Path(str(cmd[0])).name} {' '.join(map(str, cmd[1:4]))} ... failed ({p.returncode}):\n{tail}")
        return p.stdout or ""

    def file_hash(self, path: str) -> str:
        if path not in self._hashes:
            try:
                self._hashes[path] = sha256(Path(path))
            except OSError:
                self._hashes[path] = "missing"
        return self._hashes[path]

    def stamp_of(self, cmd: list[str], dep: Path) -> str | None:
        if not dep.is_file():
            return None
        h = hashlib.sha256(json.dumps(cmd).encode())
        for name in depfile_inputs(dep):
            h.update(f"{name}\0{self.file_hash(name)}\n".encode())
        return h.hexdigest()

    def compile_one(self, base: list[str], flags: list[str], src: Path, obj: Path, cwd: Path | None = None) -> bool:
        """Compile src to obj with a depfile; False when the object was current (not compiled)."""
        dep, stamp = obj.with_suffix(".d"), obj.with_suffix(".stamp")
        cmd = [*base, *flags, "-MD", "-MF", str(dep), "-c", str(src), "-o", str(obj)]
        if obj.is_file() and stamp.is_file() and stamp.read_text().strip() == self.stamp_of(cmd, dep):
            return False
        for stale in (obj, dep, stamp):
            if stale.exists():
                stale.unlink()
        self.sh(cmd, cwd=cwd, log=obj.with_suffix(".log"))
        stamp.write_text((self.stamp_of(cmd, dep) or "") + "\n")
        return True

    def compile_many(self, phase: str, units: list[tuple[list[str], list[str], Path, Path, Path | None]]) -> dict:
        wait_for_memory(phase)
        t0 = time.monotonic()
        with cf.ThreadPoolExecutor(self.jobs) as ex:
            done = list(ex.map(lambda u: self.compile_one(*u), units))
        return {"objects": len(units), "compiled": sum(done), "seconds": round(time.monotonic() - t0, 2)}

    def archive(self, lib: Path, objects: list[Path]) -> None:
        if lib.exists():
            lib.unlink()
        self.sh([self.zig, "ar", "rcsD", str(lib), *map(str, objects)])

    def extract(self, name: str) -> Path:
        tarball = pinned_archive(self.tools, name, self.root)
        self.record["inputs"][name] = {"path": str(tarball.relative_to(self.root)), "sha256": self.tools[name]["sha256"]}
        with tarfile.open(tarball) as t:
            top = {m.name.split("/", 1)[0] for m in t.getmembers()}
            if len(top) != 1:
                raise Refused(f"{tarball} has {len(top)} top-level entries")
            tree = self.out / "src" / top.pop()
            marker = tree / ".sa2-extracted"
            if marker.is_file() and marker.read_text().strip() == self.tools[name]["sha256"]:
                return tree
            if tree.exists():
                raise Refused(f"{tree} exists but is not this tarball's extraction: delete it "
                              f"(.venv/bin/python tools/rmtree.py {tree})")
            t.extractall(self.out / "src", filter="data")
        marker.write_text(self.tools[name]["sha256"] + "\n")
        return tree

    # ------------------------------------------------------------------ phases
    def deps(self) -> dict:
        inc = self.out / "deps/include"
        zlib = self.extract("zlib")
        png = self.extract("libpng")
        zstd = self.extract("zstd")
        o = self.out / "obj/deps"
        o.mkdir(parents=True, exist_ok=True)
        units = [(self.cc, ["-O2", "-DNDEBUG", f"-I{zlib}"], zlib / f"{s}.c", o / f"zlib_{s}.o", None)
                 for s in ZLIB_SOURCES]
        conf = self.out / "gen/pnglibconf"
        conf.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png / "scripts/pnglibconf.h.prebuilt", conf / "pnglibconf.h")
        units += [(self.cc, ["-O2", "-DNDEBUG", f"-I{conf}", f"-I{png}", f"-I{zlib}"], png / f"{s}.c",
                   o / f"png_{s}.o", None) for s in PNG_SOURCES]
        zs = sorted((zstd / "lib/common").glob("*.c")) + sorted((zstd / "lib/decompress").glob("*.c"))
        units += [(self.cc, ["-O2", "-DNDEBUG", "-DZSTD_DISABLE_ASM", "-DZSTD_LEGACY_SUPPORT=0",
                             f"-I{zstd / 'lib'}", f"-I{zstd / 'lib/common'}"], s,
                   o / f"zstd_{s.parent.name}_{s.stem}.o", None) for s in zs]
        stats = self.compile_many("deps", units)
        self.archive(self.out / "deps/libz.a", [u[3] for u in units if u[3].name.startswith("zlib_")])
        self.archive(self.out / "deps/libpng.a", [u[3] for u in units if u[3].name.startswith("png_")])
        self.archive(self.out / "deps/libzstd.a", [u[3] for u in units if u[3].name.startswith("zstd_")])
        for f in (zlib / "zlib.h", zlib / "zconf.h", png / "png.h", png / "pngconf.h", conf / "pnglibconf.h",
                  zstd / "lib/zstd.h", zstd / "lib/zstd_errors.h"):
            shutil.copyfile(f, inc / f.name)
        return stats

    def wrappers(self) -> Path:
        """gen/z{cc,cxx,ar,ranlib,rc}: the pinned zig for the triple, for cmake and for the
        translator's identity (which runs and hashes its compiler as one program)."""
        d = self.out / "gen"
        # zig's driver has no -dumpfullversion (the identity's compiler-version query): the
        # compilers answer it with the pinned clang version (tools.zig.clang).
        version = (f'if [ "$#" = 1 ] && [ "$1" = -dumpfullversion ]; then echo {self.tools["zig"]["clang"]}; '
                   f'exit 0; fi\n')
        for name, args in (("zcc", f"cc -target {TRIPLE}"), ("zcxx", f"c++ -target {TRIPLE}"), ("zar", "ar"),
                           ("zranlib", "ranlib"), ("zrc", "rc")):
            w = d / name
            text = f'#!/bin/sh\n{version if name in ("zcc", "zcxx") else ""}exec "{self.zig}" {args} "$@"\n'
            if not w.is_file() or w.read_text() != text:
                w.write_text(text)
            w.chmod(0o755)
        return d

    def sdl3(self) -> dict:
        sdl = self.tools["sdl3"]
        tarball = pinned_archive(self.tools, "sdl3", self.root)
        patches = [self.root / p["path"] for p in sdl.get("patches", [])]
        for p, pin in zip(patches, sdl.get("patches", [])):
            if sha256(p) != pin["sha256"]:
                raise Refused(f"{p} does not hash to its pin (tools/pins.json sdl3.patches)")
        w = self.wrappers()
        tool = self.out / "gen/sdl3-toolchain.cmake"
        tool.write_text("\n".join([
            "set(CMAKE_SYSTEM_NAME Windows)", "set(CMAKE_SYSTEM_PROCESSOR x86_64)",
            f"set(CMAKE_C_COMPILER {w / 'zcc'})", f"set(CMAKE_CXX_COMPILER {w / 'zcxx'})",
            f"set(CMAKE_AR {w / 'zar'})", f"set(CMAKE_RANLIB {w / 'zranlib'})", f"set(CMAKE_RC_COMPILER {w / 'zrc'})",
            "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)", "set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)",
            "set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)", ""]))
        install = self.out / "sdl3"
        build = self.out / "build/sdl3"
        configure = [self.tools["cmake"]["path"], "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={self.tools['ninja']['path']}",
                     f"-DCMAKE_TOOLCHAIN_FILE={tool}", f"-DCMAKE_INSTALL_PREFIX={install}", *SDL_CMAKE_OPTIONS,
                     f"-DSDL_VENDOR_INFO={sdl['vendor_info']}"]
        stamp_text = json.dumps({"tarball": sdl["sha256"], "patches": [p["sha256"] for p in sdl.get("patches", [])],
                                 "configure": configure[1:], "toolchain": tool.read_text(),
                                 "wrappers": [(w / n).read_text() for n in ("zcc", "zcxx")]}, sort_keys=True)
        stamp = install / ".sa2-build"
        self.record["inputs"]["sdl3"] = {"path": str(tarball.relative_to(self.root)), "sha256": sdl["sha256"],
                                         "patches": {str(p.relative_to(self.root)): sha256(p) for p in patches},
                                         "vendor_info": sdl["vendor_info"]}
        if stamp.is_file() and stamp.read_text() == stamp_text and (install / "bin/SDL3.dll").is_file():
            return {"rebuilt": False}
        wait_for_memory("sdl3")
        t0 = time.monotonic()
        tree = self.out / "src" / f"SDL3-{sdl['version']}"
        for d in (tree, build, install):
            if d.exists():
                raise Refused(f"{d} is left from an earlier SDL build: delete it "
                              f"(.venv/bin/python tools/rmtree.py {d}) and run again")
        with tarfile.open(tarball) as t:
            t.extractall(self.out / "src", filter="data")
        py = str(self.root / ".venv/bin/python")
        self.sh([py, str(self.root / "tools/s5/sdl3_patches.py"), "source", "--root", str(self.root), "--tree", str(tree)])
        self.sh([py, str(self.root / "tools/s5/sdl3_patches.py"), "apply", "--root", str(self.root), "--tree", str(tree)],
                log=self.out / "gen/sdl3-patch.log")
        build.mkdir(parents=True)
        self.sh([*configure, "-S", str(tree), "-B", str(build)], log=self.out / "gen/sdl3-configure.log")
        self.sh([self.tools["cmake"]["path"], "--build", str(build), "-j", str(self.jobs)],
                log=self.out / "gen/sdl3-build.log")
        self.sh([self.tools["cmake"]["path"], "--install", str(build)], log=self.out / "gen/sdl3-install.log")
        config = (build / "include-config-release/build_config/SDL_build_config.h").read_text()
        drivers = sorted(set(re.findall(r"^#define SDL_GPU_(D3D12|VULKAN|METAL) 1", config, re.M)))
        stamp.write_text(stamp_text)
        return {"rebuilt": True, "seconds": round(time.monotonic() - t0, 2), "gpu_drivers": drivers}

    def dxvk_flags(self, source: str, record: Path) -> list[str]:
        """extract_dxvk_shader.py flags for one of DXVK's units, made Windows ones, and written as
        the record identity reads (the Linux flags kept beside them)."""
        cc_json = self.root / CC_JSON_REL
        linux_record = record.with_name(record.stem + ".linux.record")
        out = self.sh([str(self.root / ".venv/bin/python"), str(EXTRACT), "flags", "--compile-commands", str(cc_json),
                       "--source", source, "--record", str(linux_record)])
        flags = windows_flags([l for l in out.splitlines() if l])
        record.write_text(json.dumps({"source": source, "linux_record": str(linux_record), "flags": flags},
                                     indent=1) + "\n")
        return flags

    def dxbc(self) -> dict:
        cc_json = self.root / CC_JSON_REL
        if not cc_json.is_file():
            raise Refused(f"no DXVK compile_commands.json at {cc_json} (tools/s5/build_host_deps.sh)")
        dxvk = self.root / self.tools["dxvk"]["path"]
        entries = [e for e in json.loads(cc_json.read_text()) if "/subprojects/dxbc-spirv/" in e["file"]]
        o = self.out / "obj/dxbc"
        f = self.out / "gen/dxbc-flags"
        o.mkdir(parents=True, exist_ok=True)
        f.mkdir(parents=True, exist_ok=True)
        units = []
        for e in entries:
            src = Path(os.path.normpath(os.path.join(e["directory"], e["file"])))
            rel = src.relative_to(dxvk / "subprojects/dxbc-spirv").as_posix()
            name = rel.replace("/", "_")
            flags = self.dxvk_flags(f"subprojects/dxbc-spirv/{rel}", f / f"{name}.flags")
            units.append((self.cxx, ["-w", *flags], src, o / f"{name}.o", None))
        if len(units) != 50:
            raise Refused(f"DXVK's build compiled {len(units)} dxbc-spirv units, not the archive's 50")
        stats = self.compile_many("dxbc", units)
        self.archive(self.out / "lib/libdxbc_spv.a", [u[3] for u in units])
        return stats

    def translator(self) -> dict:
        dxvk = self.root / self.tools["dxvk"]["path"]
        g = self.out / "gen/translator"
        o = self.out / "obj/translator"
        g.mkdir(parents=True, exist_ok=True)
        o.mkdir(parents=True, exist_ok=True)
        py = str(self.root / ".venv/bin/python")
        lower = g / "d9g_dxvk_lower.gen.cpp"
        self.sh([py, str(EXTRACT), "lower", "--dxvk", str(dxvk), "--repo", str(self.root), "--out", str(lower),
                 "--record", str(g / "d9g_dxvk_lower.record")])
        d3d9 = self.dxvk_flags("src/d3d9/d3d9_shader.cpp", g / "flags.d3d9_shader.flags")
        dxvkio = self.dxvk_flags("src/dxvk/dxvk_shader_io.cpp", g / "flags.dxvk_shader_io.flags")
        inc_dirs = [H, H / "test", P]
        d3d9_units = [lower, dxvk / "src/d3d9/d3d9_shader_analysis.cpp", dxvk / "src/d3d9/d3d9_constant_copy.cpp",
                      *[H / f"{t}.cpp" for t in TRANSLATOR_UNITS]]
        unit_args = [f"--unit={g / 'flags.d3d9_shader.flags'}:{u}" for u in d3d9_units]
        unit_args.append(f"--unit={g / 'flags.dxvk_shader_io.flags'}:{dxvk / 'src/dxvk/dxvk_shader_io.cpp'}")
        wait_for_memory("translator identity")
        tid = self.sh([py, str(EXTRACT), "identity", "--compiler", str(self.wrappers() / "zcxx"),
                       "--archive", str(self.out / "lib/libdxbc_spv.a"), "--repo", str(self.root), "--dxvk", str(dxvk),
                       "--out", str(g / "translator_identity.record"), *unit_args,
                       *[f"--include={d}" for d in inc_dirs],
                       "--extra", str(Path(__file__).resolve()), "--extra", str(EXTRACT)]).strip().splitlines()[-1]
        if not re.fullmatch(r"dxbc-spirv:[0-9a-f]{16}\+translator:[0-9a-f]{16}", tid):
            raise Refused(f"the translator identity reads {tid!r}")
        inc = [f"-I{d}" for d in inc_dirs]
        sect = ["-ffunction-sections", "-fdata-sections"]
        define = [f'-DD9G_TRANSLATOR_ID="{tid}"']
        units = [(self.cxx, ["-w", *d3d9, *sect, *inc, *define], lower, o / "dxvk_lower.o", None),
                 (self.cxx, ["-w", *d3d9, *sect, *inc, *define], dxvk / "src/d3d9/d3d9_shader_analysis.cpp",
                  o / "dxvk_d3d9_shader_analysis.o", None),
                 (self.cxx, ["-w", *d3d9, *sect, *inc, *define], dxvk / "src/d3d9/d3d9_constant_copy.cpp",
                  o / "dxvk_d3d9_constant_copy.o", None),
                 (self.cxx, ["-w", *dxvkio, *sect], dxvk / "src/dxvk/dxvk_shader_io.cpp", o / "dxvk_shader_io.o", None)]
        units += [(self.cxx, ["-w", *d3d9, *sect, *inc, *define], H / f"{t}.cpp", o / f"{t}.o", None)
                  for t in TRANSLATOR_UNITS]
        stats = self.compile_many("translator", units)
        self.archive(self.out / "lib/libd9g_shader.a", [u[3] for u in units])
        self.translator_id = tid
        self.d3d9_flags = d3d9
        stats["translator_id"] = tid
        return stats

    def sdl_include(self) -> list[str]:
        sdl = self.out / "sdl3"
        if not (sdl / "include/SDL3/SDL.h").is_file():
            raise Refused(f"no SDL3 headers under {sdl} (the sdl3 phase)")
        khronos = self.out / "src" / f"SDL3-{self.tools['sdl3']['version']}" / "src/video/khronos"
        return [f"-I{sdl / 'include'}", f"-I{khronos}"]

    def d9g(self) -> dict:
        gen = self.out / "gen/d3d9gpu-gen"
        dxvk_d3d9 = self.root / "private/s5-tools/install/include/dxvk/d3d9.h"
        self.sh([str(self.root / ".venv/bin/python"), str(H / "gen_com.py"), "--header", str(dxvk_d3d9),
                 "--out", str(gen)])
        self.record["inputs"]["gen_com_header"] = {"path": str(dxvk_d3d9.relative_to(self.root)),
                                                   "sha256": sha256(dxvk_d3d9)}
        o = self.out / "obj/d9g"
        o.mkdir(parents=True, exist_ok=True)
        flags = [*WARN, f"-I{gen}", f"-I{H}", f"-I{P}", *self.sdl_include()]
        units = [(self.cxx, flags, H / f"{t}.cpp", o / f"{t}.o", None) for t in D9G_UNITS]
        stats = self.compile_many("d9g", units)
        self.archive(self.out / "lib/libsa2_d3d9gpu.a", [u[3] for u in units])
        return stats

    def expectations(self) -> Path:
        """gen/expected-gpu/sa2_host_expectations.h: the GPU player's contract (SDL's three keys) for
        this build's SDL3.dll, in generate_expectations.py's format."""
        sdl = self.tools["sdl3"]
        inc = self.out / "sdl3/include/SDL3"
        version_src = (inc / "SDL_version.h").read_text()
        parts = []
        for name in ("MAJOR", "MINOR", "MICRO"):
            found = re.findall(r"^#define SDL_" + name + r"_VERSION\s+(\d+)\s*$", version_src, re.M)
            if len(found) != 1:
                raise Refused("ambiguous SDL version header")
            parts.append(int(found[0]))
        if ".".join(map(str, parts)) != sdl["version"]:
            raise Refused(f"SDL header version {parts} is not tools.sdl3.version {sdl['version']}")
        rev_src = (inc / "SDL_revision.h").read_text()
        revisions = re.findall(r'^#define SDL_REVISION "([^"\n]*)"\s*$', rev_src, re.M)
        vendors = re.findall(r'^#define SDL_VENDOR_INFO "([^"\n]*)"\s*$', rev_src, re.M)
        if len(revisions) != 1 or vendors != [sdl["vendor_info"]]:
            raise Refused(f"the SDL revision header reads {revisions} with vendor {vendors}, not one revision with "
                          f"tools.sdl3.vendor_info")
        dll = self.out / "sdl3/bin/SDL3.dll"
        values = {"sdl_sha256": sha256(dll), "sdl_version": parts[0] * 1000000 + parts[1] * 1000 + parts[2],
                  "sdl_revision": f"{revisions[0]} ({vendors[0]})"}
        d = self.out / "gen/expected-gpu"
        d.mkdir(parents=True, exist_ok=True)
        header = d / "sa2_host_expectations.h"
        text = ("#pragma once\nnamespace sa2_host_expected {\n"
                f"inline constexpr char sdl_sha256[] = \"{values['sdl_sha256']}\";\n"
                f"inline constexpr int sdl_version = {values['sdl_version']};\n"
                f"inline constexpr char sdl_revision[] = \"{values['sdl_revision']}\";\n}}\n")
        if not header.is_file() or header.read_text() != text:
            header.write_text(text)
        (d / "expectations.record").write_text(json.dumps({"contract": values, "library": str(dll),
                                                           "host": "sdlgpu", "triple": TRIPLE}, indent=1) + "\n")
        return d

    def player(self) -> dict:
        exp = self.expectations()
        sdl_inc = self.sdl_include()
        dep_inc = f"-I{self.out / 'deps/include'}"
        gen = self.out / "gen/d3d9gpu-gen"
        o = self.out / "obj/player"
        o.mkdir(parents=True, exist_ok=True)
        pflags = [*WARN, f"-I{exp}", "-DSA2_PLAYER_GPU=1", f"-I{ROOT / 'host'}", *sdl_inc, dep_inc]
        dflags = [*WARN, f"-I{gen}", f"-I{H}", f"-I{P}", *sdl_inc]
        units = [(self.cxx, pflags, P / f"{t}.cpp", o / f"{t}.o", None) for t in PLAYER_UNITS]
        # Each host tweak of the player's (runtime/sa2_tweaks.h, "Add a host tweak"), by glob.
        units += [(self.cxx, pflags, s, o / f"tweak_{s.stem}.o", None) for s in sorted((P / "tweaks").glob("*.cpp"))]
        units.append((self.cxx, [*dflags, f"-I{H / 'test'}", "-DSA2_CLEAR_MATRIX_STATIC"], H / "test/clear_matrix.cpp",
                      o / "clear_matrix.o", None))
        tid = getattr(self, "translator_id", None) or json.loads(
            (self.out / "gen/translator/translator_identity.record").read_text())["id"]
        d3d9 = getattr(self, "d3d9_flags", None) or json.loads(
            (self.out / "gen/translator/flags.d3d9_shader.flags").read_text())["flags"]
        sflags = ["-w", *d3d9, f"-I{H}", f"-I{H / 'test'}", f"-I{P}", *sdl_inc, f'-DD9G_TRANSLATOR_ID="{tid}"']
        units.append((self.cxx, sflags, H / "test/shaderc.cpp", o / "shaderc.o", None))
        stats = self.compile_many("player", units)
        bin_ = self.out / "bin"
        libs = [self.out / "lib/libsa2_d3d9gpu.a", self.out / "lib/libd9g_shader.a", self.out / "lib/libdxbc_spv.a"]
        sdl_lib = [str(self.out / "sdl3/lib/libSDL3.dll.a")]   # SDL3.dll's import library
        deps = [self.out / "deps/libpng.a", self.out / "deps/libz.a", self.out / "deps/libzstd.a"]
        player_objs = [u[3] for u in units if u[3].stem not in ("shaderc",)]
        self.both_define(player_objs)
        wait_for_memory("link")
        self.link(bin_ / "sa2_player_gpu.exe", [*map(str, player_objs), *map(str, libs), *sdl_lib,
                                               *map(str, deps)], self.out / "gen/link-player.log")
        self.link(bin_ / "d3d9gpu_shaderc.exe", [str(o / "shaderc.o"), str(o / "sha256.o"),
                  str(self.out / "lib/libd9g_shader.a"), str(self.out / "lib/libdxbc_spv.a"), *sdl_lib],
                  self.out / "gen/link-shaderc.log")
        install(self.out / "sdl3/bin/SDL3.dll", bin_ / "SDL3.dll")
        for pdb in bin_.glob("*.pdb"):
            pdb.unlink()
        return stats

    def link(self, exe: Path, inputs: list[str], log: Path) -> None:
        """Link to a scratch name and install it: the same inputs give the same bytes
        (LINK_REPRO), and a binary a run is using is never rewritten in place."""
        scratch = exe.with_name(exe.stem + ".new.exe")
        self.sh([*self.cxx, *LINK_REPRO, "-o", str(scratch), *inputs], log=log)
        install(scratch, exe)
        if scratch.exists():
            scratch.unlink()

    def both_define(self, player_objs: list[Path]) -> None:
        """host/d3d9gpu/build.sh's check: no symbol but the standard library's template code is
        defined by two of the player's, d9g's and the translator's objects (the linker would keep
        one copy of a weak definition silently). libc++'s names are std::__1 (_ZNSt3__1)."""
        groups = {"player": player_objs, "d9g": sorted((self.out / "obj/d9g").glob("*.o")),
                  "translator": sorted((self.out / "obj/translator").glob("*.o"))}
        defined = {}
        for name, objs in groups.items():
            out = self.sh(["nm", "--defined-only", *map(str, objs)])
            defined[name] = {f[2] for f in (l.split() for l in out.splitlines()) if len(f) == 3 and f[1].isupper()}
        # COFF adds its section symbols (".text", ".debug$S"), the standard library's typeinfo,
        # vtables, VTTs and thunks in every unit that uses them (COMDATs: libc++ is linked
        # statically, nothing is imported), and clang's per-unit __clang_call_terminate. A
        # mangled name is the standard library's when it demangles to std:: (after any special
        # name's prefix: "typeinfo for ", "VTT for ", "virtual thunk to " ...).
        # Two of host 2's own, named: d9g_shader_profile.h's DeviceFactsQuery's implicit destructor
        # (a bool, four std::strings and a struct of scalars), which clang emits as a COMDAT in
        # d9g_gpu_sdl.o and d9g_shader_vulkan.o alike where g++ inlines the translator's copy; and
        # the host tweaks' inline mask and its test (host/player/tweaks.h, sa2tw::g_on and
        # sa2tw::on), which the player and a host tweak's change in d9g share by design.
        section_or_helper = re.compile(r"^(\.|DW\.ref\.|__clang_call_terminate$|_ZNSt|_ZNKSt|_ZSt|"
                                       r"_ZN3d9g6shader16DeviceFactsQueryD[12]Ev$|"
                                       r"_ZN5sa2tw4g_onE$|_ZN5sa2tw2onENS_3BitE$)")
        std = re.compile(r"^(?:[A-Za-z -]+ (?:for|to) )?std::")
        for a, b in (("player", "d9g"), ("player", "translator"), ("d9g", "translator")):
            both = sorted(s for s in defined[a] & defined[b] if not section_or_helper.match(s))
            if not both:
                continue
            plain = self.sh(["c++filt"], input_text="\n".join(both)).splitlines() if both else []
            left = [m for m, d in zip(both, plain) if not std.match(d)]
            if left:
                raise Refused(f"{a} and {b} both define {left[:10]}")


def output_hashes(out: Path) -> dict:
    files = [out / "bin/sa2_player_gpu.exe", out / "bin/d3d9gpu_shaderc.exe", out / "bin/SDL3.dll",
             out / "lib/libdxbc_spv.a", out / "lib/libd9g_shader.a", out / "lib/libsa2_d3d9gpu.a",
             out / "deps/libz.a", out / "deps/libpng.a", out / "deps/libzstd.a",
             out / "gen/expected-gpu/sa2_host_expectations.h"]
    return {str(f.relative_to(out)): sha256(f) for f in files if f.is_file()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / DEFAULT_OUT))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", action="append", choices=PHASES)
    a = ap.parse_args(argv)
    if not 1 <= a.jobs <= 4:
        raise Refused("--jobs is 1 to 4 (the machine's memory rule)")
    out = Path(a.out).resolve()
    tools = pins()
    b = Builder(out, a.jobs, tools)
    for phase in a.only or PHASES:
        t0 = time.monotonic()
        print(f"== {phase}", flush=True)
        result = getattr(b, phase)()
        result.setdefault("seconds", round(time.monotonic() - t0, 2))
        b.record["phases"][phase] = result
        print(f"   {json.dumps(result)}", flush=True)
    b.record["outputs"] = output_hashes(out)
    b.record["only"] = a.only
    (out / "host2_win_build.runrec").write_text(json.dumps(b.record, indent=1) + "\n")
    if a.only:
        print("HOST2_WIN_BUILD_PHASES_OK", " ".join(a.only))
    else:
        print("HOST2_WIN_BUILD_OK", out / "bin/sa2_player_gpu.exe", b.record["outputs"]["bin/sa2_player_gpu.exe"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
