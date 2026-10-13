#!/usr/bin/env python3
"""Behaviour tests for check_pins.py against throwaway pins files.

The script runs as a subprocess with `--pins` pointing at a temporary file
and `--root` at this repository, so the python and pytest verifiers compare
against the interpreter running the tests.

S5's host dependencies (sdl3, glslang, dxvk) are checked against a miniature
project root under tmp_path instead, shaped like the real one:
`private/s5-tools` holding source archives; a git checkout tagged with an
annotated tag, as DXVK's v3.1.1 is, with a submodule that has a nested
submodule of its own (DXVK's dxbc-spirv holds SPIRV-Headers the same way);
and stand-in built files carrying the version stamps the real builds carry,
each library installed as a versioned file behind its soname link and a
development link, with the pin naming the soname link; DXVK's build directory
holds a thin archive over three objects, which the install's dxbc-spirv archive
copies (made by GNU ar, as tools/s5/build_host_deps.sh makes them). The
template's sdl3 is the stock form; the project's own SDL patches (patches/sdl3)
and the stock configuration's prefix (sdl3-stock) are added by the tests that
hold them, and so is a shader tool's checkout. Every git command, the fixture's
and the gate's, runs with the user's and the system's git configuration shut
out, as the rest of the suite does.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
CHECK_PINS = SCRIPTS / "check_pins.py"
PYTHON_VERSION = sys.version.split()[0]


def run(tmp_path: Path, tools: dict) -> subprocess.CompletedProcess[str]:
    pins = tmp_path / "pins.json"
    pins.write_text(json.dumps({"schema": "sa2-recomp-pins/v1", "tools": tools}), encoding="utf-8")
    return subprocess.run([sys.executable, str(CHECK_PINS), "--root", str(ROOT), "--pins", str(pins)],
                          capture_output=True, text=True)


def test_missing_version_and_commit_fails_naming_the_tool(tmp_path: Path) -> None:
    result = run(tmp_path, {"python": {"version": PYTHON_VERSION},
                            "foo": {"path": "/", "use": "nothing"}})
    assert result.returncode == 1
    assert "foo: FAIL entry carries neither version nor commit" in result.stdout
    assert f"python: OK sys.version reports {PYTHON_VERSION}" in result.stdout
    assert result.stdout.splitlines()[-1] == "tool pins FAIL: foo; 1 tools verified, 0 unverifiable (none)"


def test_unpinned_needs_a_reason(tmp_path: Path) -> None:
    result = run(tmp_path, {"foo": {"unpinned": True}})
    assert result.returncode == 1
    assert "foo: FAIL unpinned without a reason" in result.stdout

    result = run(tmp_path, {"foo": {"unpinned": True, "reason": "fetched in S1"},
                            "python": {"version": PYTHON_VERSION}})
    assert result.returncode == 0, result.stdout
    assert "foo: UNPINNED fetched in S1" in result.stdout
    assert result.stdout.splitlines()[-1] == (
        "tool pins OK: 1 tools verified, 0 unverifiable (none), 1 unpinned (foo)")


def test_python_version_mismatch_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {"python": {"version": "0.0.0", "path": sys.executable}})
    assert result.returncode == 1
    assert f"python: FAIL sys.version reports '{PYTHON_VERSION}', pinned '0.0.0'" in result.stdout
    assert result.stdout.splitlines()[-1].startswith("tool pins FAIL: python;")


def test_verified_and_unverifiable_are_counted(tmp_path: Path) -> None:
    result = run(tmp_path, {
        "python": {"version": PYTHON_VERSION},
        "pytest": {"version": importlib.metadata.version("pytest")},
        "sdl2": {"version": "2.32.4 (runtime only)"},
        "mystery": {"version": "1.0"},
    })
    assert result.returncode == 0, result.stdout
    assert "sdl2: UNVERIFIABLE only the runtime library is installed" in result.stdout
    assert "mystery: UNVERIFIABLE no verifier for this tool in check_pins.py" in result.stdout
    assert result.stdout.splitlines()[-1] == "tool pins OK: 2 tools verified, 2 unverifiable (mystery, sdl2)"


def test_missing_path_fails(tmp_path: Path) -> None:
    result = run(tmp_path, {"zig": {"version": "0.15.2", "path": "private/does-not-exist/zig"}})
    assert result.returncode == 1
    assert "zig: FAIL path missing: " in result.stdout


@pytest.mark.parametrize("name", ["meson", "pkg-config"])
def test_meson_and_pkg_config_report_their_pinned_version(tmp_path: Path, name: str) -> None:
    """DXVK Native's configure tools: `<path> --version` on digit boundaries, like gcc's."""
    tool = tmp_path / name
    tool.write_text("#!/bin/sh\necho 1.7.0\n")
    tool.chmod(0o755)
    result = run(tmp_path, {name: {"version": "1.7.0", "path": str(tool)}})
    assert result.returncode == 0, result.stdout
    assert f"{name}: OK {name} --version reports '1.7.0', contains 1.7.0" in result.stdout

    result = run(tmp_path, {name: {"version": "1.7", "path": str(tool)}})
    assert result.returncode == 1
    assert f"{name}: FAIL {name} --version reports '1.7.0', pinned '1.7'" in result.stdout


# --- S5's host dependencies ---------------------------------------------------

S5 = "private/s5-tools"
TAG = "v3.1.1"
SDL_REVISION = "release-3.4.16-0-gfa2c02bb6"
SDL_COMMIT = "fa2c02bb6e21974a89ea9824bc53c9932abe5f9c"
# DXVK's build: the thin archive over dxbc-spirv's objects, and the install's real copy of it.
DXBC_BUILD = f"{S5}/build/dxvk/subprojects/dxbc-spirv/libdxbc_spv.a"
DXBC_OBJECTS = {"ir_ir_builder.cpp.o": b"\x7fELF builder\n", "sm3_sm3_parser.cpp.o": b"\x7fELF parser\n",
                "x.o": b"\x7fELF x\n"}
DXBC_ARCHIVE = f"{S5}/install/lib/libdxbc_spv.a"
GLSLANG_REPORTS = "Glslang Version: 11:16.6.0"
DXVK = f"{S5}/src/dxvk-3.1.1"
# A library's pin names the soname link the loader follows; the file behind it is versioned.
DXVK_LIBRARY = f"{S5}/install/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0"
DXVK_FILE = f"{DXVK_LIBRARY}.30101"
SDL3_LIBRARY = f"{S5}/install/lib/libSDL3.so.0"
SDL3_FILE = f"{SDL3_LIBRARY}.4.16"
GLSLANG_BINARY = f"{S5}/install/bin/glslang"
STATUS = ("git status --porcelain=v1 --ignore-submodules=none --untracked-files=all"
          " --ignored=matching")
# What the superproject's `git status` showed before the hidden-change checks.
PLAIN_STATUS = ("status", "--porcelain=v1", "--ignore-submodules=none", "--untracked-files=all")
SKIPPED = ("dxvk: FAIL index entries git status does not look at (lowercase: assume-unchanged;"
           " S: skip-worktree): ")


def hermetic_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    return env


def git(repo: Path, *args: str) -> str:
    # protocol.file.allow: the fixture's submodules are cloned from local paths.
    return subprocess.run(("git", "-c", "protocol.file.allow=always", "-C", str(repo)) + args,
                          env=hermetic_env(), capture_output=True, text=True,
                          check=True).stdout.strip()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


ALSA_DEB = "libasound2-dev_1.2.14-1+deb13u1_amd64.deb"
ALSA_BYTES = b"!<arch>\ndebian-binary   headers only\n"


def write_targz(path: Path, members: dict[str, bytes]) -> str:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())
    return sha(buffer.getvalue())


def write_file(path: Path, data: bytes, mode: int = 0o644) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)
    return sha(data)


def install_library(root: Path, link: str, file: str, data: bytes) -> str:
    """The versioned file, the soname link to it and the development link to that, as the
    real install has them (libSDL3.so -> libSDL3.so.0 -> libSDL3.so.0.4.16)."""
    digest = write_file(root / file, data)
    (root / link).symlink_to(Path(file).name)
    (root / link[:link.rindex(".so") + 3]).symlink_to(Path(link).name)
    return digest


def glslang_script(reports: str) -> bytes:
    return f"#!/bin/sh\necho '{reports}'\n".encode()


def ar(cwd: Path, *args: str) -> None:
    subprocess.run(["ar", *args], cwd=cwd, check=True, capture_output=True)


def dxbc_archives(root: Path, objects: dict[str, bytes] = DXBC_OBJECTS) -> str:
    """DXVK's thin archive over `objects` (as meson makes it) and the install's real archive of
    the same members (ar rcsD, as the builder makes it); the real one's sha256."""
    build = root / DXBC_BUILD
    (build.parent / "libdxbc_spv.a.p").mkdir(parents=True, exist_ok=True)
    for name, data in objects.items():
        (build.parent / "libdxbc_spv.a.p" / name).write_bytes(data)
    members = [f"libdxbc_spv.a.p/{n}" for n in objects]
    build.unlink(missing_ok=True)
    ar(build.parent, "rcT", build.name, *members)
    archive = root / DXBC_ARCHIVE
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.unlink(missing_ok=True)
    ar(build.parent, "rcsD", str(archive), *members)
    return sha(archive.read_bytes())


def commit_file(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"{name}: {text}")
    return git(repo, "rev-parse", "HEAD")


def build_template(base: Path) -> tuple[Path, dict]:
    """A project root holding S5's three dependencies as the pins describe them."""
    origins = base / "origins"
    origins.mkdir()
    git(origins, "init", "-q", "inner")
    commit_file(origins / "inner", "h", "one")
    git(origins, "init", "-q", "outer")
    commit_file(origins / "outer", "f", "one")
    git(origins / "outer", "submodule", "add", "-q", str(origins / "inner"), "inner")
    git(origins / "outer", "commit", "-q", "-m", "with inner")

    root = base / "root"
    (root / ".git").mkdir(parents=True)          # project_root only checks that it exists
    checkout = root / DXVK
    checkout.parent.mkdir(parents=True)
    git(checkout.parent, "init", "-q", checkout.name)
    commit_file(checkout, "meson.build", "project('dxvk')")
    git(checkout, "submodule", "add", "-q", str(origins / "outer"), "s")
    git(checkout, "submodule", "update", "-q", "--init", "--recursive")
    git(checkout, "commit", "-q", "-m", "with s")
    # annotated, as DXVK's v3.1.1 is: refs/tags/<tag> names the tag object, not the commit
    git(checkout, "tag", "-a", TAG, "-m", TAG)
    assert git(checkout, "cat-file", "-t", f"refs/tags/{TAG}") == "tag"

    s5 = root / S5
    return root, {
        "sdl3": {
            "version": "3.4.16",
            "path": f"{S5}/dl/SDL3-3.4.16.tar.gz",
            "commit": SDL_COMMIT,
            "sha256": write_targz(s5 / "dl/SDL3-3.4.16.tar.gz", {
                "SDL3-3.4.16/CMakeLists.txt": b"project(SDL3)\n",
                "SDL3-3.4.16/.git-hash": f"{SDL_COMMIT}\n".encode(),
                "SDL3-3.4.16/REVISION.txt": f"{SDL_REVISION}\n".encode()}),
            "library": SDL3_LIBRARY,
            "library_sha256": install_library(
                root, SDL3_LIBRARY, SDL3_FILE,
                b"\x7fELF\0SDL_RATE\0SDL-" + SDL_REVISION.encode() + b"\0libasound.so.2\0x"),
            "alsa_headers": {
                "path": f"{S5}/dl/{ALSA_DEB}",
                "bytes": len(ALSA_BYTES),
                "sha256": write_file(s5 / "dl" / ALSA_DEB, ALSA_BYTES),
            },
        },
        "glslang": {
            "version": "16.6.0",
            "path": f"{S5}/dl/glslang-16.6.0.tar.gz",
            "sha256": write_targz(s5 / "dl/glslang-16.6.0.tar.gz",
                                  {"glslang-16.6.0/CMakeLists.txt": b"project(glslang)\n"}),
            "binary": GLSLANG_BINARY,
            "binary_sha256": write_file(root / GLSLANG_BINARY, glslang_script(GLSLANG_REPORTS),
                                        0o755),
        },
        "dxvk": {
            "version": "3.1.1",
            "tag": TAG,
            "commit": git(checkout, "rev-parse", "HEAD"),
            "path": DXVK,
            "library": DXVK_LIBRARY,
            "library_sha256": install_library(root, DXVK_LIBRARY, DXVK_FILE,
                                              b"\x7fELF\0DXVK: \0" + TAG.encode() + b"\0Build: \0"),
            "dxbc_spirv_archive": DXBC_ARCHIVE,
            "dxbc_spirv_archive_sha256": dxbc_archives(root),
            "dxbc_spirv_build": DXBC_BUILD,
        },
    }


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    return build_template(tmp_path_factory.mktemp("s5-template"))


@pytest.fixture
def s5(template: tuple[Path, dict], tmp_path: Path) -> tuple[Path, dict]:
    """A fresh copy of the template's root and entries, for one test to break."""
    root = tmp_path / "root"
    shutil.copytree(template[0], root, symlinks=True)
    return root, json.loads(json.dumps(template[1]))


def gate(root: Path, tools: dict, *flags: str,
         env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """check_pins.py against a fixture root; the pins file sits beside it, under tmp_path."""
    pins = root.parent / "pins.json"
    pins.write_text(json.dumps({"schema": "sa2-recomp-pins/v1", "tools": tools}), encoding="utf-8")
    return subprocess.run([sys.executable, str(CHECK_PINS), "--root", str(root), "--pins", str(pins),
                           *flags], capture_output=True, text=True, env=dict(hermetic_env(), **(env or {})))


def line(result: subprocess.CompletedProcess[str], name: str) -> str:
    found = [text for text in result.stdout.splitlines() if text.startswith(f"{name}: ")]
    assert len(found) == 1, result.stdout + result.stderr
    return found[0]


def fails(root: Path, tools: dict, name: str, *flags: str) -> str:
    """The FAIL line for `name`, after checking the run failed on it alone and did not crash."""
    result = gate(root, tools, *flags)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Traceback" not in result.stderr, result.stderr
    assert result.stdout.splitlines()[-1].startswith(f"tool pins FAIL: {name};"), result.stdout
    text = line(result, name)
    assert text.startswith(f"{name}: FAIL "), text
    return text


def items(text: str, prefix: str) -> list[str]:
    """The lines a FAIL line lists after `prefix`."""
    assert text.startswith(prefix), text
    return text[len(prefix):].split("; ")


DXVK_CLEAN = ("working tree clean in the checkout and every submodule, nothing ignored, "
              "no index entry skipped; 2 submodules at their recorded commits")
SDL_SOURCES = (f"SDL3-3.4.16/.git-hash reads {SDL_COMMIT}; SDL3-3.4.16/REVISION.txt reads {SDL_REVISION}")
DXBC_HELD = "3 members, each DXVK's build's object (libdxbc_spv.a, thin)"


def test_the_three_pass_as_built(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    result = gate(root, entries)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[-1] == "tool pins OK: 3 tools verified, 0 unverifiable (none)"
    assert line(result, "dxvk") == (
        f"dxvk: OK HEAD and refs/tags/{TAG} are {entries['dxvk']['commit']}; {DXVK_CLEAN}; "
        f"sha256(libdxvk_d3d9.so.0 -> libdxvk_d3d9.so.0.30101) reports "
        f"{entries['dxvk']['library_sha256']}; libdxvk_d3d9.so.0.30101 holds '{TAG}'; "
        f"sha256(libdxbc_spv.a) reports {entries['dxvk']['dxbc_spirv_archive_sha256']}; {DXBC_HELD}")
    assert line(result, "sdl3") == (
        f"sdl3: OK sha256(SDL3-3.4.16.tar.gz) reports {entries['sdl3']['sha256']}; {SDL_SOURCES}; "
        f"sha256({ALSA_DEB}) reports {entries['sdl3']['alsa_headers']['sha256']}; "
        f"sha256(libSDL3.so.0 -> libSDL3.so.0.4.16) reports {entries['sdl3']['library_sha256']}; "
        f"libSDL3.so.0.4.16 holds 'SDL-{SDL_REVISION}', no patch marks; libSDL3.so.0.4.16 holds 'libasound.so.2'")
    assert line(result, "glslang") == (
        f"glslang: OK sha256(glslang-16.6.0.tar.gz) reports {entries['glslang']['sha256']}; "
        f"sha256(glslang) reports {entries['glslang']['binary_sha256']}; "
        f"glslang --version reports '{GLSLANG_REPORTS}', contains 16.6.0")


# --- the files: held by sha256, regular, readable, inside private/s5-tools ------


@pytest.mark.parametrize("name, key, file", [
    ("sdl3", "sha256", "SDL3-3.4.16.tar.gz"),
    ("sdl3", "library_sha256", "libSDL3.so.0 -> libSDL3.so.0.4.16"),
    ("glslang", "sha256", "glslang-16.6.0.tar.gz"),
    ("glslang", "binary_sha256", "glslang"),
    ("dxvk", "library_sha256", "libdxvk_d3d9.so.0 -> libdxvk_d3d9.so.0.30101"),
])
def test_a_file_that_does_not_hash_to_its_pin_fails(s5: tuple[Path, dict], name: str, key: str,
                                                     file: str) -> None:
    root, entries = s5
    broken = dict(entries, **{name: dict(entries[name], **{key: "0" * 64})})
    assert fails(root, broken, name) == (
        f"{name}: FAIL sha256({file}) reports '{entries[name][key]}', pinned '{'0' * 64}'")


@pytest.mark.parametrize("name, link, other, stamp", [
    ("sdl3", SDL3_LIBRARY, "libSDL3.so.0.4.18", b"SDL-" + SDL_REVISION.encode()),
    ("dxvk", DXVK_LIBRARY, "libdxvk_d3d9.so.0.30102", TAG.encode()),
])
def test_the_loader_s_link_repointed_at_another_build_fails(s5: tuple[Path, dict], name: str,
                                                             link: str, other: str,
                                                             stamp: bytes) -> None:
    """Another build installed beside the pinned one repoints the soname link and the
    development link, and leaves the pinned file where it was. The loader follows the link,
    so the gate hashes what the link reaches: other bytes, even under the pinned stamp."""
    root, entries = s5
    directory = (root / link).parent
    built = write_file(directory / other, b"\x7fELF another build\0" + stamp + b"\0")
    for each in (link, link[:link.rindex(".so") + 3]):
        (root / each).unlink()
        (root / each).symlink_to(other)
    assert fails(root, entries, name) == (
        f"{name}: FAIL sha256({Path(link).name} -> {other}) reports '{built}', "
        f"pinned '{entries[name]['library_sha256']}'")


@pytest.mark.parametrize("name, key", [
    ("sdl3", "sha256"), ("sdl3", "library_sha256"), ("glslang", "binary_sha256"),
    ("dxvk", "library_sha256"), ("dxvk", "tag"),
])
def test_a_field_the_check_reads_must_be_pinned(s5: tuple[Path, dict], name: str, key: str) -> None:
    root, entries = s5
    broken = dict(entries, **{name: {k: v for k, v in entries[name].items() if k != key}})
    assert fails(root, broken, name) == f"{name}: FAIL entry pins no {key}"


def test_a_directory_or_an_unreadable_file_fails_its_line(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    library = root / SDL3_FILE                     # what the soname link reaches
    built = library.read_bytes()
    library.unlink()
    library.mkdir()
    assert fails(root, entries, "sdl3") == f"sdl3: FAIL not a regular file: {library}"
    library.rmdir()
    write_file(library, built, 0o000)
    try:
        assert fails(root, entries, "sdl3") == f"sdl3: FAIL cannot read {library}: Permission denied"
    finally:
        library.chmod(0o644)

    archive = root / entries["glslang"]["path"]
    archive.unlink()
    archive.mkdir()
    assert fails(root, entries, "glslang") == f"glslang: FAIL not a regular file: {archive}"


def test_an_error_no_verifier_expects_fails_its_line_and_the_run_goes_on(
        s5: tuple[Path, dict], tmp_path: Path) -> None:
    root, entries = s5
    locked = root / S5 / "dl"                      # Path.exists raises inside it
    locked.chmod(0o000)
    try:
        result = gate(root, dict(entries, python={"version": PYTHON_VERSION}))
    finally:
        locked.chmod(0o755)
    assert result.returncode == 1 and "Traceback" not in result.stderr, result.stderr
    assert line(result, "sdl3").startswith("sdl3: FAIL [Errno 13] Permission denied: ")
    assert line(result, "python") == f"python: OK sys.version reports {PYTHON_VERSION}"

    properties = tmp_path / "ghidra" / "Ghidra" / "application.properties"
    write_file(properties, b"application.version=12.1.3\n", 0o000)
    try:
        result = gate(root, {"ghidra": {"version": "12.1.3_PUBLIC", "path": str(tmp_path / "ghidra")},
                             "python": {"version": PYTHON_VERSION}})
    finally:
        properties.chmod(0o644)
    assert result.returncode == 1 and "Traceback" not in result.stderr, result.stderr
    assert line(result, "ghidra").startswith("ghidra: FAIL [Errno 13] Permission denied: ")
    assert result.stdout.splitlines()[-1] == (
        "tool pins FAIL: ghidra; 1 tools verified, 0 unverifiable (none)")


def test_a_link_is_followed_and_must_stay_inside_s5_tools(s5: tuple[Path, dict],
                                                          tmp_path: Path) -> None:
    root, entries = s5
    link = root / SDL3_LIBRARY
    outside = tmp_path / "elsewhere" / "libSDL3.so.0.4.16"
    write_file(outside, (root / SDL3_FILE).read_bytes())   # the same bytes, outside the tree
    link.unlink()
    link.symlink_to(outside)
    assert fails(root, entries, "sdl3") == (
        f"sdl3: FAIL library {link} resolves to {outside}, outside {S5}")

    link.unlink()
    link.symlink_to(tmp_path / "gone")
    assert fails(root, entries, "sdl3") == f"sdl3: FAIL path missing: {link}"


def test_a_checkout_outside_s5_tools_fails(s5: tuple[Path, dict], tmp_path: Path) -> None:
    root, entries = s5
    checkout = root / DXVK
    moved = tmp_path / "dxvk-elsewhere"
    checkout.rename(moved)
    checkout.symlink_to(moved)
    assert fails(root, entries, "dxvk") == (
        f"dxvk: FAIL path {checkout} resolves to {moved}, outside {S5}")


# --- the versions ------------------------------------------------------------------


def test_sdl3_is_held_to_its_revision_in_the_archive_and_the_library(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    archive = root / entries["sdl3"]["path"]

    def with_archive(members: dict[str, bytes], **changes: str) -> dict:
        return dict(entries, sdl3=dict(entries["sdl3"], sha256=write_targz(archive, members),
                                       **changes))

    stamp = {"SDL3-3.4.16/.git-hash": f"{SDL_COMMIT}\n".encode()}
    assert fails(root, with_archive({**stamp, "SDL3-3.4.16/REVISION.txt": b"release-3.4.15-0-g0123456\n"}),
                 "sdl3") == ("sdl3: FAIL SDL3-3.4.16/REVISION.txt reads 'release-3.4.15-0-g0123456', "
                             "not release-3.4.16-0-gfa2c02bb6")
    assert fails(root, with_archive({**stamp, "SDL3-3.4.16/CMakeLists.txt": b"project(SDL3)\n"}), "sdl3") == (
        "sdl3: FAIL SDL3-3.4.16.tar.gz has no member SDL3-3.4.16/REVISION.txt")
    revision = {**stamp, "SDL3-3.4.16/REVISION.txt": f"{SDL_REVISION}\n".encode()}
    assert fails(root, with_archive(revision, version="3.4.15"), "sdl3") == (
        "sdl3: FAIL SDL3-3.4.16.tar.gz has no member SDL3-3.4.15/.git-hash")
    # the commit: the archive's own .git-hash, and REVISION.txt's abbreviation of it
    other = "0123456789" * 4
    assert fails(root, with_archive(revision, commit=other), "sdl3") == (
        f"sdl3: FAIL SDL3-3.4.16/.git-hash reads '{SDL_COMMIT}', pinned commit '{other}'")
    assert fails(root, with_archive({"SDL3-3.4.16/.git-hash": f"{other}\n".encode(),
                                     "SDL3-3.4.16/REVISION.txt": f"{SDL_REVISION}\n".encode()}, commit=other),
                 "sdl3") == (f"sdl3: FAIL SDL3-3.4.16/REVISION.txt reads '{SDL_REVISION}', not "
                             f"release-3.4.16-0-g{other[:9]}")
    assert fails(root, with_archive(revision, commit=SDL_COMMIT[:9]), "sdl3") == (
        "sdl3: FAIL commit 'fa2c02bb6' is not 40 lowercase hex digits")

    restored = with_archive(revision)
    dirty = dict(restored["sdl3"], library_sha256=write_file(
        root / SDL3_FILE, b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b"-dirty\0"))
    assert fails(root, dict(restored, sdl3=dirty), "sdl3") == (
        f"sdl3: FAIL libSDL3.so.0.4.16 does not hold the NUL-delimited string 'SDL-{SDL_REVISION}'")


def test_glslang_reports_its_version_on_digit_boundaries(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    for pin in ("6.6.0", "16.6", "1", "16.6.1"):
        broken = dict(entries, glslang=dict(entries["glslang"], version=pin))
        assert fails(root, broken, "glslang") == (
            f"glslang: FAIL glslang --version reports '{GLSLANG_REPORTS}', pinned '{pin}'")

    other = dict(entries["glslang"], binary_sha256=write_file(
        root / GLSLANG_BINARY, glslang_script("Glslang Version: 11:16.7.0"), 0o755))
    assert fails(root, dict(entries, glslang=other), "glslang") == (
        "glslang: FAIL glslang --version reports 'Glslang Version: 11:16.7.0', pinned '16.6.0'")

    (root / GLSLANG_BINARY).unlink()
    assert fails(root, entries, "glslang") == f"glslang: FAIL path missing: {root / GLSLANG_BINARY}"


def test_dxvk_is_held_to_its_tag_in_the_pin_and_the_library(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    broken = dict(entries, dxvk=dict(entries["dxvk"], version="9.9.9"))
    assert fails(root, broken, "dxvk") == "dxvk: FAIL tag 'v3.1.1' is not 'v' + version '9.9.9'"

    for stamp in (b"v3.1.1+", b"v3.1.1-2-g0123456", b"v3.1.0"):   # dirty, a later commit, older
        other = dict(entries["dxvk"], library_sha256=write_file(
            root / DXVK_FILE, b"\x7fELF\0DXVK: \0" + stamp + b"\0Build: \0"))
        assert fails(root, dict(entries, dxvk=other), "dxvk") == (
            "dxvk: FAIL libdxvk_d3d9.so.0.30101 does not hold the NUL-delimited string 'v3.1.1'")


# --- the checkout: HEAD, the tag, a clean tree, submodules, git's exit status -----


def test_head_and_the_tag_must_be_the_pinned_commit(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    checkout = root / DXVK
    head = entries["dxvk"]["commit"]
    broken = dict(entries, dxvk=dict(entries["dxvk"], commit="0" * 40))
    assert fails(root, broken, "dxvk") == (
        f"dxvk: FAIL git rev-parse HEAD reports '{head}', pinned '{'0' * 40}'")

    later = commit_file(checkout, "meson.build", "project('dxvk', version: 'later')")
    git(checkout, "tag", "-f", "-a", TAG, "-m", "moved", later)
    git(checkout, "checkout", "-q", head)
    assert fails(root, entries, "dxvk") == (
        f"dxvk: FAIL refs/tags/{TAG} reports '{later}', pinned '{head}'")

    git(checkout, "tag", "-d", TAG)
    assert fails(root, entries, "dxvk") == f"dxvk: FAIL the checkout has no tag {TAG}"


def drift_tracked_file(checkout: Path) -> None:
    (checkout / "meson.build").write_text("project('dxvk', version: 'edited')")


def drift_untracked_file(checkout: Path) -> None:
    (checkout / "stray.c").write_text("int x;\n")


def drift_untracked_file_in_submodule(checkout: Path) -> None:
    (checkout / "s" / "stray.c").write_text("int x;\n")


def drift_edit_in_submodule(checkout: Path) -> None:
    (checkout / "s" / "f").write_text("edited")


def drift_edit_in_nested_submodule(checkout: Path) -> None:
    (checkout / "s" / "inner" / "h").write_text("edited")


def drift_submodule_moved_and_staged(checkout: Path) -> None:
    commit_file(checkout / "s", "f", "moved")
    git(checkout, "add", "s")


def drift_submodule_moved(checkout: Path) -> None:
    commit_file(checkout / "s", "f", "moved")


def drift_submodule_removed_from_index(checkout: Path) -> None:
    git(checkout, "rm", "-q", "--cached", "s")


def drift_submodule_ignored_by_config(checkout: Path) -> None:
    git(checkout, "config", "submodule.s.ignore", "all")
    (checkout / "s" / "f").write_text("edited")


def drift_gitmodules_emptied(checkout: Path) -> None:
    (checkout / ".gitmodules").write_text("")


def drift_untracked_file_hidden_by_config(checkout: Path) -> None:
    git(checkout, "config", "status.showUntrackedFiles", "no")
    (checkout / "src").mkdir()
    (checkout / "src" / "stray.c").write_text("int x;\n")


@pytest.mark.parametrize("drift, shown", [
    (drift_tracked_file, " M meson.build"),
    (drift_untracked_file, "?? stray.c"),
    (drift_untracked_file_hidden_by_config, "?? src/stray.c"),
    (drift_untracked_file_in_submodule, " M s"),
    (drift_edit_in_submodule, " M s"),
    (drift_edit_in_nested_submodule, " M s"),
    (drift_submodule_moved_and_staged, "M  s"),
    (drift_submodule_moved, " M s"),
    (drift_submodule_removed_from_index, "D  s"),
    (drift_submodule_ignored_by_config, " M s"),
    (drift_gitmodules_emptied, " M .gitmodules"),
])
def test_a_checkout_that_is_not_clean_fails(s5: tuple[Path, dict], drift, shown: str) -> None:
    root, entries = s5
    drift(root / DXVK)
    assert shown in items(fails(root, entries, "dxvk"), "dxvk: FAIL the working tree is not clean: ")


# Changes the superproject's `git status` does not show (review-fold finding 3): an ignore
# rule, and a submodule's own config deciding what its superproject sees of it.

def exclude(git_dir: Path, pattern: str) -> None:
    (git_dir / "info").mkdir(exist_ok=True)
    with (git_dir / "info" / "exclude").open("a") as rules:
        rules.write(pattern + "\n")


def drift_untracked_file_excluded(checkout: Path) -> None:
    exclude(checkout / ".git", "stray.c")
    drift_untracked_file(checkout)


def drift_untracked_file_excluded_in_submodule(checkout: Path) -> None:
    exclude(checkout / ".git" / "modules" / "s", "stray.c")
    drift_untracked_file_in_submodule(checkout)


def drift_untracked_file_hidden_by_submodule_config(checkout: Path) -> None:
    git(checkout / "s", "config", "status.showUntrackedFiles", "no")
    drift_untracked_file_in_submodule(checkout)


def drift_nested_edit_ignored_by_submodule_config(checkout: Path) -> None:
    git(checkout / "s", "config", "submodule.inner.ignore", "all")
    drift_edit_in_nested_submodule(checkout)


def drift_nested_edit_hidden_by_submodule_diff_config(checkout: Path) -> None:
    git(checkout / "s", "config", "diff.ignoreSubmodules", "all")
    drift_edit_in_nested_submodule(checkout)


@pytest.mark.parametrize("drift, shown", [
    (drift_untracked_file_excluded, "!! stray.c"),
    (drift_untracked_file_excluded_in_submodule, "s: !! stray.c"),
    (drift_untracked_file_hidden_by_submodule_config, "s: ?? stray.c"),
    (drift_nested_edit_ignored_by_submodule_config, "s:  M inner"),
    (drift_nested_edit_hidden_by_submodule_diff_config, "s/inner:  M h"),
])
def test_a_change_the_superproject_s_status_does_not_show_fails(s5: tuple[Path, dict], drift,
                                                                 shown: str) -> None:
    root, entries = s5
    checkout = root / DXVK
    drift(checkout)
    assert git(checkout, *PLAIN_STATUS) == ""
    assert shown in items(fails(root, entries, "dxvk"), "dxvk: FAIL the working tree is not clean: ")


def drift_assume_unchanged(checkout: Path) -> None:
    git(checkout, "update-index", "--assume-unchanged", "meson.build")
    drift_tracked_file(checkout)


def drift_skip_worktree(checkout: Path) -> None:
    git(checkout, "update-index", "--skip-worktree", "meson.build")
    drift_tracked_file(checkout)


def drift_skip_worktree_in_submodule(checkout: Path) -> None:
    git(checkout / "s", "update-index", "--skip-worktree", "f")
    drift_edit_in_submodule(checkout)


def drift_assume_unchanged_in_nested_submodule(checkout: Path) -> None:
    git(checkout / "s" / "inner", "update-index", "--assume-unchanged", "h")
    drift_edit_in_nested_submodule(checkout)


@pytest.mark.parametrize("drift, shown", [
    (drift_assume_unchanged, "h meson.build"),
    (drift_skip_worktree, "S meson.build"),
    (drift_skip_worktree_in_submodule, "s: S f"),
    (drift_assume_unchanged_in_nested_submodule, "s/inner: h h"),
])
def test_an_index_entry_git_status_does_not_look_at_fails(s5: tuple[Path, dict], drift,
                                                           shown: str) -> None:
    """An edit under an assume-unchanged or skip-worktree entry is invisible to `git status`,
    here or in the submodule; `git ls-files -v` shows the flag."""
    root, entries = s5
    checkout = root / DXVK
    drift(checkout)
    assert git(checkout, *PLAIN_STATUS) == ""
    assert git(checkout, "submodule", "--quiet", "foreach", "--recursive", "git", *STATUS.split()[1:]) == ""
    assert shown in items(fails(root, entries, "dxvk"), SKIPPED)


def test_a_submodule_s_lines_are_attributed_under_any_locale(s5: tuple[Path, dict]) -> None:
    """`submodule foreach` names the submodule it enters in a translated message; git runs
    with LC_ALL=C, so a caller's German locale does not break the attribution (where git's
    German messages are installed; elsewhere git prints English anyway)."""
    root, entries = s5
    drift_nested_edit_hidden_by_submodule_diff_config(root / DXVK)
    result = gate(root, entries, env={"LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8",
                                      "LANGUAGE": "de"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert items(line(result, "dxvk"), "dxvk: FAIL the working tree is not clean: ") == [
        "s:  M inner", "s/inner:  M h"]


def test_a_deinitialised_submodule_fails(s5: tuple[Path, dict]) -> None:
    """`git status` does not report an uninitialised submodule; `submodule status` does."""
    root, entries = s5
    checkout = root / DXVK
    git(checkout, "submodule", "deinit", "-q", "--force", "s")
    assert git(checkout, *STATUS.split()[1:]) == ""
    text = fails(root, entries, "dxvk")
    assert text.startswith("dxvk: FAIL submodules not at their recorded commits: ['-"), text
    assert text.endswith(" s']"), text


def test_a_nested_submodule_left_uninitialised_fails(s5: tuple[Path, dict]) -> None:
    """Only `submodule status --recursive` sees a submodule's own submodule."""
    root, entries = s5
    checkout = root / DXVK
    git(checkout / "s", "submodule", "deinit", "-q", "--force", "inner")
    assert git(checkout, *STATUS.split()[1:]) == ""
    assert "-" not in [status[:1] for status in git(checkout, "submodule", "status").splitlines()]
    text = fails(root, entries, "dxvk")
    assert text.startswith("dxvk: FAIL submodules not at their recorded commits: ['-"), text
    assert text.endswith(" s/inner']"), text


def test_a_git_command_that_exits_non_zero_fails(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    checkout = root / DXVK
    shutil.rmtree(checkout / ".git" / "modules" / "s" / "modules" / "inner")   # nested gitdir
    text = fails(root, entries, "dxvk")
    assert text.startswith(f"dxvk: FAIL {STATUS} exited 128: fatal: "), text

    shutil.rmtree(checkout / ".git" / "modules" / "s")          # the submodule's git directory
    text = fails(root, entries, "dxvk")
    assert text.startswith(f"dxvk: FAIL {STATUS} exited 128: fatal: "), text

    shutil.rmtree(checkout / ".git")
    text = fails(root, entries, "dxvk")
    assert text.startswith("dxvk: FAIL git rev-parse HEAD exited 128: fatal: not a git repository"), text


def test_git_is_read_from_stdout_and_leaves_the_checkout_as_it_was(s5: tuple[Path, dict]) -> None:
    """A warning git prints on stderr is not part of the value compared, and no `git status`,
    the checkout's or a submodule's, rewrites an index (the copied fixture's indexes are
    stale by inode and time, which a status without --no-optional-locks refreshes)."""
    root, entries = s5
    checkout = root / DXVK
    git(checkout, "tag", "HEAD")                    # `git rev-parse HEAD` now warns on stderr
    indexes = [checkout / ".git" / "index", checkout / ".git" / "modules" / "s" / "index",
               checkout / ".git" / "modules" / "s" / "modules" / "inner" / "index"]
    before = [index.read_bytes() for index in indexes]
    result = gate(root, entries)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [index.read_bytes() for index in indexes] == before


def test_the_mod_loader_checkout_goes_through_the_same_exit_check(s5: tuple[Path, dict],
                                                                  tmp_path: Path) -> None:
    root, _ = s5
    (tmp_path / "not-a-checkout").mkdir()
    result = gate(root, {"sa2-mod-loader": {"commit": "0" * 40, "path": str(tmp_path / "not-a-checkout")}})
    assert result.returncode == 1
    assert line(result, "sa2-mod-loader").startswith(
        "sa2-mod-loader: FAIL git rev-parse HEAD exited 128: fatal: not a git repository")


# --- --only and --sources-only: what tools/s5/build_host_deps.sh runs ---------------


def test_only_checks_the_named_tools_and_fails_an_unknown_name(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = dict(entries, zig={"version": "0.15.2", "path": "private/does-not-exist/zig"})
    result = gate(root, tools, "--only", "sdl3")
    assert result.returncode == 0, result.stdout
    assert [text.split(":")[0] for text in result.stdout.splitlines()] == ["sdl3", "tool pins OK"]
    assert result.stdout.splitlines()[-1] == "tool pins OK: 1 tools verified, 0 unverifiable (none)"

    result = gate(root, tools, "--only", "sdl3", "--only", "nosuch")
    assert result.returncode == 1
    assert f"nosuch: FAIL no such tool in {root.parent / 'pins.json'}" in result.stdout


def test_sources_only_checks_the_sources_and_not_the_built_files(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    flags = ("--only", "dxvk", "--only", "sdl3", "--only", "glslang", "--sources-only")
    for file in (DXVK_FILE, SDL3_FILE, GLSLANG_BINARY, DXBC_ARCHIVE):
        (root / file).unlink()
    result = gate(root, entries, *flags)
    assert result.returncode == 0, result.stdout
    assert line(result, "dxvk") == (
        f"dxvk: OK HEAD and refs/tags/{TAG} are {entries['dxvk']['commit']}; {DXVK_CLEAN}")
    assert line(result, "sdl3") == (f"sdl3: OK sha256(SDL3-3.4.16.tar.gz) reports "
                                    f"{entries['sdl3']['sha256']}; {SDL_SOURCES}; sha256({ALSA_DEB}) reports "
                                    f"{entries['sdl3']['alsa_headers']['sha256']}")
    assert line(result, "glslang") == (
        f"glslang: OK sha256(glslang-16.6.0.tar.gz) reports {entries['glslang']['sha256']}")

    broken = dict(entries, sdl3=dict(entries["sdl3"], sha256="0" * 64))
    assert fails(root, broken, "sdl3", *flags).startswith("sdl3: FAIL sha256(SDL3-3.4.16.tar.gz) reports")
    drift_skip_worktree(root / DXVK)
    assert fails(root, entries, "dxvk", *flags).startswith(SKIPPED)
    drift_untracked_file(root / DXVK)
    assert fails(root, entries, "dxvk", *flags).startswith("dxvk: FAIL the working tree is not clean: ")


def test_sources_only_refuses_a_tool_without_sources_and_needs_only(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = dict(entries, python={"version": PYTHON_VERSION})
    assert fails(root, tools, "python", "--only", "python", "--sources-only") == (
        "python: FAIL no check of its sources in check_pins.py (--sources-only)")
    result = gate(root, tools, "--sources-only")
    assert result.returncode == 2
    assert "--sources-only needs --only" in result.stderr


def test_sdl3_holds_the_alsa_headers_package_to_its_hash_and_size(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    alsa = entries["sdl3"]["alsa_headers"]

    def with_alsa(**changes: object) -> dict:
        return dict(entries, sdl3=dict(entries["sdl3"], alsa_headers=dict(alsa, **changes)))

    for flags in ((), ("--only", "sdl3", "--sources-only")):
        assert fails(root, with_alsa(sha256="0" * 64), "sdl3", *flags) == (
            f"sdl3: FAIL sha256({ALSA_DEB}) reports '{alsa['sha256']}', pinned '{'0' * 64}'")
        assert fails(root, with_alsa(bytes=len(ALSA_BYTES) + 1), "sdl3", *flags) == (
            f"sdl3: FAIL {ALSA_DEB} holds {len(ALSA_BYTES)} bytes, not the pinned {len(ALSA_BYTES) + 1}")
        assert fails(root, with_alsa(bytes=float(len(ALSA_BYTES))), "sdl3", *flags) == (
            f"sdl3: FAIL {ALSA_DEB} holds {len(ALSA_BYTES)} bytes, not the pinned {float(len(ALSA_BYTES))}")
        for key in ("path", "sha256"):
            missing = dict(entries, sdl3=dict(entries["sdl3"], alsa_headers={
                k: v for k, v in alsa.items() if k != key}))
            assert fails(root, missing, "sdl3", *flags) == f"sdl3: FAIL entry pins no alsa_headers.{key}"
        unpinned = dict(entries, sdl3={k: v for k, v in entries["sdl3"].items() if k != "alsa_headers"})
        assert fails(root, unpinned, "sdl3", *flags) == "sdl3: FAIL entry pins no alsa_headers"
    (root / alsa["path"]).unlink()
    assert fails(root, entries, "sdl3") == f"sdl3: FAIL path missing: {root / alsa['path']}"
    assert fails(root, entries, "sdl3", "--only", "sdl3", "--sources-only") == (
        f"sdl3: FAIL path missing: {root / alsa['path']}")


def test_a_library_built_without_dynamic_alsa_fails(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    library = root / SDL3_FILE
    without = dict(entries["sdl3"], library_sha256=write_file(
        library, b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b"\0x"))
    assert fails(root, dict(entries, sdl3=without), "sdl3") == (
        "sdl3: FAIL libSDL3.so.0.4.16 does not hold the NUL-delimited string 'libasound.so.2'")


# --- the project's SDL patches (patches/sdl3) and the mark of an altered SDL ----------------

PATCH = "patches/sdl3/0001-local.patch"
PATCH_TEXT = (f"sa2-recomp local patch 0001: NOT FROM THE SDL PROJECT. Target commit {SDL_COMMIT}.\n"
              "Tarball sha256 {tarball}.\n\ndiff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")


def identity(*digests: str) -> str:
    return "sa2-recomp sdl3-patches " + sha("".join(d + "\n" for d in digests).encode())[:12]


def patched(root: Path, entries: dict, text: str | None = None, marked: bool = True) -> dict:
    """The entries with sdl3 patched as the project builds it: one patch under patches/sdl3,
    pinned with its identity, and a library stamped with it (or, unmarked, without)."""
    data = (text if text is not None else PATCH_TEXT.format(tarball=entries["sdl3"]["sha256"])).encode()
    digest = write_file(root / PATCH, data)
    vendor = identity(digest)
    stamp = f"SDL-{SDL_REVISION} ({vendor})" if marked else f"SDL-{SDL_REVISION}"
    library = write_file(root / SDL3_FILE, b"\x7fELF\0" + stamp.encode() + b"\0libasound.so.2\0sa2recomp\0")
    return dict(entries, sdl3=dict(entries["sdl3"], patches=[{"path": PATCH, "sha256": digest}],
                                   vendor_info=vendor, library_sha256=library))


def test_sdl3_with_the_project_s_patches_passes_and_holds_their_mark(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = patched(root, entries)
    result = gate(root, tools)
    assert result.returncode == 0, result.stdout + result.stderr
    vendor = tools["sdl3"]["vendor_info"]
    assert line(result, "sdl3") == (
        f"sdl3: OK sha256(SDL3-3.4.16.tar.gz) reports {entries['sdl3']['sha256']}; {SDL_SOURCES}; "
        f"sha256({ALSA_DEB}) reports {entries['sdl3']['alsa_headers']['sha256']}; "
        f"1 patches under patches/sdl3 at their pins, identity '{vendor}'; "
        f"sha256(libSDL3.so.0 -> libSDL3.so.0.4.16) reports {tools['sdl3']['library_sha256']}; "
        f"libSDL3.so.0.4.16 holds 'SDL-{SDL_REVISION} ({vendor})', and the patch's property names; "
        "libSDL3.so.0.4.16 holds 'libasound.so.2'")
    assert gate(root, tools, "--only", "sdl3", "--sources-only").returncode == 0


def test_a_stamped_library_built_without_the_patch_fails(s5: tuple[Path, dict]) -> None:
    """The builder skipping the patch while still passing SDL_VENDOR_INFO: the stamp is there, the
    patch's property names are not (the plan's mutant, held here as well as by the hash)."""
    root, entries = s5
    tools = patched(root, entries)
    vendor = tools["sdl3"]["vendor_info"]
    data = b"\x7fELF\0SDL-" + SDL_REVISION.encode() + f" ({vendor})".encode() + b"\0libasound.so.2\0"
    tools = dict(tools, sdl3=dict(tools["sdl3"], library_sha256=write_file(root / SDL3_FILE, data)))
    assert fails(root, tools, "sdl3") == (
        "sdl3: FAIL libSDL3.so.0.4.16 carries the vendor stamp but none of the patch's property names "
        "('sa2recomp'): an unpatched SDL marked as patched")


def test_a_patched_library_unmarked_or_built_from_another_patch_set_fails(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = patched(root, entries, marked=False)
    vendor = tools["sdl3"]["vendor_info"]
    want = f"sdl3: FAIL libSDL3.so.0.4.16 does not hold the NUL-delimited string 'SDL-{SDL_REVISION} ({vendor})'"
    assert fails(root, tools, "sdl3") == want
    other = b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b" (sa2-recomp sdl3-patches 000000000000)\0libasound.so.2\0"
    tools = dict(tools, sdl3=dict(tools["sdl3"], library_sha256=write_file(root / SDL3_FILE, other)))
    assert fails(root, tools, "sdl3") == want


def test_a_stock_pin_refuses_a_library_with_the_patch_s_marks(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    data = b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b"\0libasound.so.2\0SDL.gpu.device.sa2recomp.x\0"
    tools = dict(entries, sdl3=dict(entries["sdl3"], library_sha256=write_file(root / SDL3_FILE, data)))
    assert fails(root, tools, "sdl3") == (
        "sdl3: FAIL libSDL3.so.0.4.16 holds ['sa2recomp']: a patched SDL where the pins name a stock one")


def test_a_patch_that_is_not_the_pinned_one_fails(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = patched(root, entries)
    pin = tools["sdl3"]["patches"][0]["sha256"]
    for flags in ((), ("--only", "sdl3", "--sources-only")):
        data = (root / PATCH).read_bytes()
        write_file(root / PATCH, data.replace(b"+b", b"+c"))
        assert fails(root, tools, "sdl3", *flags) == (
            f"sdl3: FAIL sha256(0001-local.patch) reports '{sha(data.replace(b'+b', b'+c'))}', pinned '{pin}': "
            "not the pinned patch")
        write_file(root / PATCH, data)
    extra = root / "patches/sdl3/0002-unpinned.patch"
    write_file(extra, b"x")
    assert fails(root, tools, "sdl3") == (
        f"sdl3: FAIL the patches under patches/sdl3/ are ['{PATCH}', 'patches/sdl3/0002-unpinned.patch'], "
        f"the pins name ['{PATCH}']")
    extra.unlink()
    data = (root / PATCH).read_bytes()
    (root / PATCH).unlink()
    assert fails(root, tools, "sdl3") == f"sdl3: FAIL the patches under patches/sdl3/ are [], the pins name ['{PATCH}']"
    (root / PATCH).symlink_to(root / "elsewhere.patch")
    write_file(root / "elsewhere.patch", data)
    assert fails(root, tools, "sdl3") == f"sdl3: FAIL patch {PATCH} is not a regular file"


def test_the_vendor_info_is_the_patches_identity_and_needs_them(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = patched(root, entries)
    vendor = tools["sdl3"]["vendor_info"]
    wrong = dict(tools, sdl3=dict(tools["sdl3"], vendor_info="sa2-recomp sdl3-patches 000000000000"))
    assert fails(root, wrong, "sdl3") == (
        f"sdl3: FAIL vendor_info 'sa2-recomp sdl3-patches 000000000000' is not the pinned patches' identity '{vendor}'")
    bare = dict(entries, sdl3=dict(entries["sdl3"], vendor_info=vendor))
    assert fails(root, bare, "sdl3") == "sdl3: FAIL entry pins vendor_info with no patches"


def test_a_stock_pin_beside_a_tracked_patch_fails(s5: tuple[Path, dict]) -> None:
    """No patches pinned (absent or empty) while patches/sdl3 holds one: the build would apply
    none of it, so the pin is refused in the sources check and the full one."""
    root, entries = s5
    patched(root, entries)
    want = (f"sdl3: FAIL entry pins no patches, and ['{PATCH}'] are under patches/sdl3/: a stock pin beside a "
            "tracked patch")
    for sdl3 in (entries["sdl3"], dict(entries["sdl3"], patches=[])):
        for flags in ((), ("--only", "sdl3", "--sources-only")):
            assert fails(root, dict(entries, sdl3=sdl3), "sdl3", *flags) == want


@pytest.mark.parametrize("claim", ["NOT FROM THE SDL PROJECT", SDL_COMMIT, "{tarball}"])
def test_a_patch_header_without_its_claims_fails(s5: tuple[Path, dict], claim: str) -> None:
    root, entries = s5
    text = PATCH_TEXT.replace(claim, "x").format(tarball=entries["sdl3"]["sha256"])
    tools = patched(root, entries, text=text)
    named = entries["sdl3"]["sha256"] if claim == "{tarball}" else claim
    assert fails(root, tools, "sdl3") == f"sdl3: FAIL 0001-local.patch's header does not name '{named}'"


# --- the dxbc-spirv archive (R1's U1) ---------------------------------------------------------


def test_the_dxbc_archive_must_be_a_real_copy_of_dxvk_s_objects(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    dx = entries["dxvk"]
    thin = dict(entries, dxvk=dict(dx, dxbc_spirv_archive=DXBC_BUILD,
                                   dxbc_spirv_archive_sha256=sha((root / DXBC_BUILD).read_bytes())))
    assert fails(root, thin, "dxvk").endswith(
        "libdxbc_spv.a is a thin archive: its members are files elsewhere, not held by its hash")
    # a member recompiled after the copy: the build's object no longer the archive's
    (root / DXBC_BUILD).parent.joinpath("libdxbc_spv.a.p", "x.o").write_bytes(b"\x7fELF x, recompiled\n")
    assert fails(root, entries, "dxvk").endswith("libdxbc_spv.a's members ['x.o'] are not DXVK's build's objects")
    # a member missing from the copy
    fewer = dict(DXBC_OBJECTS)
    fewer.pop("sm3_sm3_parser.cpp.o")
    digest = dxbc_archives(root, fewer)
    ar(root / DXBC_BUILD.rsplit("/", 1)[0], "rcT", "libdxbc_spv.a", *[f"libdxbc_spv.a.p/{n}" for n in DXBC_OBJECTS])
    (root / DXBC_BUILD).unlink()
    ar(root / DXBC_BUILD.rsplit("/", 1)[0], "rcT", "libdxbc_spv.a", *[f"libdxbc_spv.a.p/{n}" for n in DXBC_OBJECTS])
    tools = dict(entries, dxvk=dict(dx, dxbc_spirv_archive_sha256=digest))
    text = fails(root, tools, "dxvk")
    assert "libdxbc_spv.a holds members ['ir_ir_builder.cpp.o', 'x.o']..., DXVK's build" in text and "(2 and 3)" in text
    # not an archive at all
    tools = dict(entries, dxvk=dict(dx, dxbc_spirv_archive_sha256=write_file(root / DXBC_ARCHIVE, b"not ar\n")))
    assert fails(root, tools, "dxvk").endswith("libdxbc_spv.a is not an ar archive")


def test_the_dxbc_archive_without_dxvk_s_build_is_held_to_its_hash(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    shutil.rmtree(root / S5 / "build")
    result = gate(root, entries)
    assert result.returncode == 0, result.stdout
    assert line(result, "dxvk").endswith(
        f"sha256(libdxbc_spv.a) reports {entries['dxvk']['dxbc_spirv_archive_sha256']}; 3 members; DXVK's build is "
        f"not on disk ({root / DXBC_BUILD}): members not compared")
    broken = dict(entries, dxvk=dict(entries["dxvk"], dxbc_spirv_archive_sha256="0" * 64))
    assert fails(root, broken, "dxvk").startswith("dxvk: FAIL sha256(libdxbc_spv.a) reports")


# --- the stock configuration's prefix (sdl3-stock) ------------------------------------------

STOCK = f"{S5}/install-sdl3-stock"
# The stock build's own thin archive over its own copies of the objects (its own build directory,
# as tools/s5/build_host_deps.sh --sdl3=stock makes it).
STOCK_BUILD = f"{S5}/build-sdl3-stock/dxvk/subprojects/dxbc-spirv/libdxbc_spv.a"


def thin_archive(root: Path, build: str, objects: dict[str, bytes] = DXBC_OBJECTS) -> None:
    path = root / build
    (path.parent / "libdxbc_spv.a.p").mkdir(parents=True, exist_ok=True)
    for name, data in objects.items():
        (path.parent / "libdxbc_spv.a.p" / name).write_bytes(data)
    path.unlink(missing_ok=True)
    ar(path.parent, "rcT", path.name, *[f"libdxbc_spv.a.p/{n}" for n in objects])


def with_stock(root: Path, entries: dict, library: bytes | None = None) -> dict:
    """The entries with sdl3-stock: its prefix holding a stock SDL, and DXVK, glslang and the
    dxbc-spirv archive, the patched configuration's bytes (held to its pins), as
    tools/s5/build_host_deps.sh --sdl3=stock installs them, and its own build's thin archive."""
    lib = library if library is not None else b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b"\0libasound.so.2\0"
    shutil.rmtree(root / STOCK, ignore_errors=True)       # a test may make the prefix twice
    thin_archive(root, STOCK_BUILD)
    stock = {
        "version": "3.4.16", "prefix": STOCK,
        "library": f"{STOCK}/lib/libSDL3.so.0",
        "library_sha256": install_library(root, f"{STOCK}/lib/libSDL3.so.0", f"{STOCK}/lib/libSDL3.so.0.4.16", lib),
        "dxvk_library": f"{STOCK}/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0",
        "glslang_binary": f"{STOCK}/bin/glslang",
        "dxbc_spirv_archive": f"{STOCK}/lib/libdxbc_spv.a",
        "dxbc_spirv_build": STOCK_BUILD,
    }
    install_library(root, f"{STOCK}/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0",
                    f"{STOCK}/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0.30101", (root / DXVK_FILE).read_bytes())
    write_file(root / STOCK / "bin/glslang", (root / GLSLANG_BINARY).read_bytes(), 0o755)
    write_file(root / STOCK / "lib/libdxbc_spv.a", (root / DXBC_ARCHIVE).read_bytes())
    return dict(entries, **{"sdl3-stock": stock})


def test_the_stock_configuration_passes_as_built(s5: tuple[Path, dict]) -> None:
    root, entries = s5
    tools = with_stock(root, patched(root, entries))
    result = gate(root, tools, "--only", "sdl3-stock")
    assert result.returncode == 0, result.stdout + result.stderr
    stock = tools["sdl3-stock"]
    text = line(result, "sdl3-stock")
    assert text.startswith(f"sdl3-stock: OK sdl3's sources: sha256(SDL3-3.4.16.tar.gz) reports "
                           f"{entries['sdl3']['sha256']}; {SDL_SOURCES}; "), text
    assert text.endswith(
        f"prefix install-sdl3-stock; sha256(libSDL3.so.0 -> libSDL3.so.0.4.16) reports {stock['library_sha256']}; "
        f"libSDL3.so.0.4.16 holds 'SDL-{SDL_REVISION}', no patch marks; libSDL3.so.0.4.16 holds 'libasound.so.2'; "
        f"sha256(libdxvk_d3d9.so.0 -> libdxvk_d3d9.so.0.30101) reports {tools['dxvk']['library_sha256']} "
        f"(dxvk's pin); libdxvk_d3d9.so.0.30101 holds '{TAG}'; sha256(glslang) reports "
        f"{tools['glslang']['binary_sha256']} (glslang's pin); glslang --version reports '{GLSLANG_REPORTS}', "
        f"contains 16.6.0; sha256(libdxbc_spv.a) reports {tools['dxvk']['dxbc_spirv_archive_sha256']}; {DXBC_HELD} "
        "(dxvk's pin)"), text
    sources = gate(root, tools, "--only", "sdl3-stock", "--sources-only")
    assert sources.returncode == 0 and line(sources, "sdl3-stock").startswith("sdl3-stock: OK sdl3's sources: ")


def test_the_stock_configuration_refuses_a_patched_sdl_and_a_file_outside_its_prefix(
        s5: tuple[Path, dict]) -> None:
    root, entries = s5
    marked = b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b" (sa2-recomp sdl3-patches 0123456789ab)\0libasound.so.2\0"
    assert fails(root, with_stock(root, entries, marked), "sdl3-stock", "--only", "sdl3-stock") == (
        f"sdl3-stock: FAIL libSDL3.so.0.4.16 does not hold the NUL-delimited string 'SDL-{SDL_REVISION}'")
    unmarked = b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b"\0libasound.so.2\0sa2recomp\0"
    assert fails(root, with_stock(root, entries, unmarked), "sdl3-stock", "--only", "sdl3-stock") == (
        "sdl3-stock: FAIL libSDL3.so.0.4.16 holds ['sa2recomp']: a patched SDL where the pins name a stock one")
    tools = with_stock(root, entries)
    outside = dict(tools["sdl3-stock"], dxvk_library=DXVK_LIBRARY)
    assert fails(root, dict(tools, **{"sdl3-stock": outside}), "sdl3-stock", "--only", "sdl3-stock") == (
        f"sdl3-stock: FAIL dxvk_library {root / DXVK_LIBRARY} is not under the prefix {(root / STOCK).resolve()}")
    older = dict(tools["sdl3-stock"], version="3.4.15")
    assert fails(root, dict(tools, **{"sdl3-stock": older}), "sdl3-stock", "--only", "sdl3-stock") == (
        "sdl3-stock: FAIL sdl3-stock's version (held to sdl3's) reports '3.4.15', pinned '3.4.16'")
    # a stock DXVK that is not the patched configuration's bytes: one pin per distinct file
    other = root / STOCK / "lib/x86_64-linux-gnu/libdxvk_d3d9.so.0.30101"
    write_file(other, other.read_bytes() + b"rebuilt")
    assert fails(root, tools, "sdl3-stock", "--only", "sdl3-stock") == (
        f"sdl3-stock: FAIL sha256(libdxvk_d3d9.so.0 -> libdxvk_d3d9.so.0.30101) reports '{sha(other.read_bytes())}', "
        f"pinned '{tools['dxvk']['library_sha256']}'")


def test_the_stock_prefix_and_its_dxbc_build_are_its_own(s5: tuple[Path, dict]) -> None:
    """The second review's probes: a stock entry naming the patched build's thin archive (the same
    objects, so only its path tells), private/s5-tools whole as its prefix, a prefix holding the
    patched library, and a build path outside private/s5-tools that is not on disk, each refused;
    and a stock library holding the vendor stamp's bytes alone (sa2-recomp, none of the patch's
    property names) refused as patched."""
    root, entries = s5
    tools = with_stock(root, patched(root, entries))
    stock = tools["sdl3-stock"]

    def stock_fails(tools: dict = tools, **changes: str) -> str:
        return fails(root, dict(tools, **{"sdl3-stock": dict(stock, **changes)}), "sdl3-stock", "--only", "sdl3-stock")

    assert stock_fails(dxbc_spirv_build=DXBC_BUILD) == (
        f"sdl3-stock: FAIL dxbc_spirv_build {(root / DXBC_BUILD).resolve()} is dxvk's: the patched build's thin "
        "archive, not the stock build's own")
    assert stock_fails(prefix=S5) == (
        f"sdl3-stock: FAIL prefix {(root / S5).resolve()} is {S5} itself, not a prefix of its own")
    moved = dict(tools, sdl3=dict(tools["sdl3"], library=stock["library"]))
    assert stock_fails(moved) == (
        f"sdl3-stock: FAIL prefix {(root / STOCK).resolve()} holds sdl3's library "
        f"{(root / stock['library']).resolve()}: the patched configuration's prefix")
    nowhere = root.parent / "nowhere" / "libdxbc_spv.a"
    assert stock_fails(dxbc_spirv_build=str(nowhere)) == (
        f"sdl3-stock: FAIL dxbc_spirv_build {nowhere} resolves to {nowhere}, outside {S5}")
    elsewhere = root.parent / "elsewhere" / "libdxbc_spv.a"
    thin_archive(root.parent, "elsewhere/libdxbc_spv.a")
    assert stock_fails(dxbc_spirv_build=str(elsewhere)) == (
        f"sdl3-stock: FAIL dxbc_spirv_build {elsewhere} resolves to {elsewhere}, outside {S5}")
    assert fails(root, dict(entries, dxvk=dict(entries["dxvk"], dxbc_spirv_build=str(nowhere))), "dxvk",
                 "--only", "dxvk") == (
        f"dxvk: FAIL dxbc_spirv_build {nowhere} resolves to {nowhere}, outside {S5}")
    vendor_only = (b"\x7fELF\0SDL-" + SDL_REVISION.encode() + b"\0libasound.so.2\0"
                   b"sa2-recomp sdl3-patches 0123456789ab\0")
    assert fails(root, with_stock(root, patched(root, entries), vendor_only), "sdl3-stock", "--only",
                 "sdl3-stock") == (
        "sdl3-stock: FAIL libSDL3.so.0.4.16 holds ['sa2-recomp']: a patched SDL where the pins name a stock one")


@pytest.mark.parametrize("key,by", [("dxvk_library_sha256", "dxvk.library_sha256"),
                                    ("glslang_binary_sha256", "glslang.binary_sha256"),
                                    ("dxbc_spirv_archive_sha256", "dxvk.dxbc_spirv_archive_sha256")])
def test_the_stock_entry_holds_no_hash_of_its_own_for_files_another_pin_holds(
        s5: tuple[Path, dict], key: str, by: str) -> None:
    """The stock prefix's DXVK, glslang and archive are held to the patched configuration's pins.
    A hash of the entry's own for one of them would never be read here while the players'
    expectations could read it: refused, even when it equals the pin it shadows."""
    root, entries = s5
    tools = with_stock(root, patched(root, entries))
    tool, field = by.split(".")
    own = dict(tools["sdl3-stock"], **{key: tools[tool][field]})
    for flags in (("--only", "sdl3-stock"), ("--only", "sdl3-stock", "--sources-only")):
        assert fails(root, dict(tools, **{"sdl3-stock": own}), "sdl3-stock", *flags) == (
            f"sdl3-stock: FAIL entry pins ['{key} (held to {by})']: the stock prefix's DXVK, glslang and archive are "
            "held to the patched configuration's pins, never to a hash of its own")


# --- S5's shader tools: a checkout at its commit and tree, and what was built from it ---------


def test_a_shader_tool_is_held_to_its_commit_tree_and_built_files(s5: tuple[Path, dict]) -> None:
    root, _ = s5
    checkout = root / S5 / "src/shader-tools/SPIRV-Tools"
    checkout.parent.mkdir(parents=True)
    git(checkout.parent, "init", "-q", checkout.name)
    commit = commit_file(checkout, "CMakeLists.txt", "project(spirv-tools)")
    tree = git(checkout, "rev-parse", "HEAD^{tree}")
    binary = f"{S5}/install-shader-tools/bin/spirv-val"
    optimizer = f"{S5}/install-shader-tools/bin/spirv-opt"
    entry = {"version": "vulkan-sdk-1.4.309.0", "commit": commit, "tree": tree,
             "path": f"{S5}/src/shader-tools/SPIRV-Tools", "binary": binary,
             "binary_sha256": write_file(root / binary, b"#!/bin/sh\n", 0o755),
             "optimizer": optimizer, "optimizer_sha256": write_file(root / optimizer, b"#!/bin/sh\n# opt\n", 0o755)}
    result = gate(root, {"spirv-tools": entry})
    assert result.returncode == 0, result.stdout
    assert line(result, "spirv-tools") == (
        f"spirv-tools: OK HEAD {commit}, tree {tree}; working tree clean, nothing ignored, no index entry skipped; "
        f"sha256(spirv-val) reports {entry['binary_sha256']}; sha256(spirv-opt) reports {entry['optimizer_sha256']}")
    # the comparer's spirv-opt (C3's linked half) is held to its pin like the validator
    assert fails(root, {"spirv-tools": dict(entry, optimizer_sha256="0" * 64)}, "spirv-tools") == (
        f"spirv-tools: FAIL sha256(spirv-opt) reports '{entry['optimizer_sha256']}', pinned '{'0' * 64}'")
    assert fails(root, {"spirv-tools": dict(entry, tree="0" * 40)}, "spirv-tools") == (
        f"spirv-tools: FAIL git rev-parse HEAD^{{tree}} reports '{tree}', pinned '{'0' * 40}'")
    assert fails(root, {"spirv-tools": dict(entry, binary_sha256="0" * 64)}, "spirv-tools") == (
        f"spirv-tools: FAIL sha256(spirv-val) reports '{entry['binary_sha256']}', pinned '{'0' * 64}'")
    (root / binary).unlink()
    sources = gate(root, {"spirv-tools": entry}, "--only", "spirv-tools", "--sources-only")
    assert sources.returncode == 0, sources.stdout
    (checkout / "stray.c").write_text("int x;\n")
    assert fails(root, {"spirv-tools": entry}, "spirv-tools", "--only", "spirv-tools", "--sources-only") == (
        "spirv-tools: FAIL the working tree is not clean: ?? stray.c")
