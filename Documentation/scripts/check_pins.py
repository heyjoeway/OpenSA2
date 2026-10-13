#!/usr/bin/env python3
"""Verify tools/pins.json against the tools on disk (gate criterion "tool versions pinned").

The criterion used to be `len(pins["tools"])`: a key count, met by any
non-empty object. This script reads every entry under "tools" and checks two
things.

Shape. Every entry carries "version" or "commit". An entry may instead carry
`"unpinned": true` with a non-empty "reason"; the check allows that, counts
it, and lists it. An entry with neither is a failure naming the tool.

Disk. For the tools this script knows, the pinned value is compared with what
the tool reports, so a pin that drifts from the installation fails instead of
sitting in the file unread. "Contains the version core" means the digits of
the pinned version (e.g. "2.44" of "GNU Binutils 2.44") appear with no digit
or dot before them and no digit or dot-and-digit after them, so a pin of
"16.6" or "6.6.0" does not match a tool reporting "16.6.0":

  python                  sys.version of the interpreter running this script
  pefile iced-x86 pytest  importlib.metadata.version
  gcc cmake ninja objdump meson pkg-config patch
                          `<path> --version`: the first line contains the version core
  java                    `<path>/bin/java -version` (all lines) contains the version core
  zig                     `<path> version` equals the pinned version
  ghidra                  `<path>/Ghidra/application.properties` has
                          application.version=<core> ("12.1.3" of "12.1.3_PUBLIC")
  proton                  `<path>/version` equals "prefix_version"
  unity                   `<path> --version` equals the pinned version
  sa-tools                `<path>/satoolsver.txt` equals the pinned version
  steamless               path exists
  sa2-mod-loader          `git -C <path> rev-parse HEAD` exits 0 and prints "commit"
  sdl2                    unverifiable: only the runtime library is installed
                          and no pinned path exists to query

S5's host dependencies, built by tools/s5/build_host_deps.sh. Every path their
pins name that this gate reads (the four fields "Not covered" lists below are
not read) must exist and resolve, symlinks followed, inside private/s5-tools,
and every file is read as a regular file, so a directory or an unreadable file
fails the line. A library's pin names the soname link the dynamic loader
follows, not the versioned file behind it: libSDL3.so.0, the name DXVK
dlopens, and libdxvk_d3d9.so.0, DXVK's SONAME. The file hashed is the one that
name reaches now, so a second build installed beside the pinned one, with the
link repointed at it, fails; the detail names both ("libSDL3.so.0 ->
libSDL3.so.0.4.16"). Each is checked in two parts, its sources and what was
built from them:

  sdl3     sources: the archive at "path" hashes to "sha256"; its member
           SDL3-<version>/.git-hash reads "commit" (40 hex digits) and its
           SDL3-<version>/REVISION.txt reads "release-<version>-0-g" and the
           commit's first 9 digits; the Debian package whose headers SDL's
           ALSA backend is built against, at "alsa_headers"."path", hashes to
           "alsa_headers"."sha256" and holds exactly "alsa_headers"."bytes"
           bytes; and the project's own patches (patches/sdl3, the user's
           second rulings of 2026-10-04): the files patches/sdl3/*.patch, in
           file-name order, are exactly the paths "patches" lists, each a
           regular file (not a link) hashing to its "sha256", each header (the
           text before its first "diff --git" line) saying "NOT FROM THE SDL
           PROJECT" and naming the pinned commit and the archive's sha256, and
           "vendor_info" is "sa2-recomp sdl3-patches " and the first 12 hex
           digits of the sha256 over each patch's sha256 and a newline, in
           order (tools/s5/sdl3_patches.py's identity). With no "patches",
           "vendor_info" must be absent and no patches/sdl3/*.patch may
           exist (a stock pin beside a tracked patch): the stock source
           built: the file "library" reaches hashes to "library_sha256" and
           holds the NUL-delimited string SDL's build stamps into it: with
           patches, "SDL-<that revision> (<vendor_info>)", the mark of an
           altered SDL (zlib licence, clause 2) naming its patch set, so a
           library built from another patch set or left unmarked fails, and
           the patch's property names ("sa2recomp"), so a stamped library
           built without the patch fails; with
           none, "SDL-<that revision>" and no "sa2-recomp" or "sa2recomp"
           bytes at all; and "libasound.so.2", the name its dynamic ALSA
           backend loads, so a library built without ALSA fails
  sdl3-stock
           the stock configuration (tools/s5/build_host_deps.sh --sdl3=stock):
           sources are sdl3's, and the entry carries no "dxvk_library_sha256",
           "glslang_binary_sha256" or "dxbc_spirv_archive_sha256" (those files
           are held to the pins named below, never to a hash of the entry's
           own); built: "prefix" is a directory of its own inside
           private/s5-tools (not private/s5-tools itself, and not one that
           holds sdl3's "library"), and every path below lies under it;
           "library" hashes to "library_sha256" and is held as sdl3's built
           check with no patches (its revision from sdl3's archive); and the
           files built against it, which are the patched configuration's bytes,
           to that configuration's pins, one pin per distinct file:
           "dxvk_library" to dxvk's "library_sha256" and "tag",
           "glslang_binary" to glslang's "binary_sha256" and version, and
           "dxbc_spirv_archive" to dxvk's "dxbc_spirv_archive_sha256", as
           dxvk's below, against "dxbc_spirv_build", which must be its own
           build's thin archive, not the one dxvk's "dxbc_spirv_build" names
  glslang  sources: the archive at "path" hashes to "sha256"
           built: "binary" hashes to "binary_sha256", and the first line of
           `<binary> --version` contains the version core
  dxvk     sources: "tag" is "v" + "version", and in the git checkout at
           "path" every git command below exits 0, and
           - HEAD and refs/tags/<tag>^{commit} are both "commit" (DXVK's tag
             is annotated, so the ref names a tag object and is peeled);
           - `git status --porcelain=v1 --ignore-submodules=none
             --untracked-files=all --ignored=matching` prints nothing, in the
             checkout and, through `git submodule foreach --recursive`, in
             every submodule: a submodule's own config (its
             status.showUntrackedFiles, its diff.ignoreSubmodules, its
             submodule.<name>.ignore) decides what the superproject's status
             sees of it, and an ignore rule (.gitignore, info/exclude) hides a
             file from status without --ignored;
           - `git ls-files -v` prints only "H" lines, in the checkout and in
             every submodule, since `git status` does not look at an entry
             marked assume-unchanged (a lowercase tag) or skip-worktree ("S");
           - every line of `git submodule status --recursive` reads
             " <40 hex digits> " (initialised, and checked out at the commit
             its superproject records)
           built: the file "library" reaches hashes to "library_sha256" and
           holds the NUL-delimited string "tag", which DXVK's build stamps
           from `git describe --dirty=+` (a build from a dirty tree holds
           "<tag>+", one from another commit "<tag>-<n>-g<hash>"); and
           "dxbc_spirv_archive" (R1's U1), the dxbc-spirv objects of DXVK's
           build copied into a real archive, hashes to
           "dxbc_spirv_archive_sha256", is a regular ar archive (not a thin
           one, whose members live elsewhere), and, when DXVK's build is on
           disk, holds exactly the members of the thin archive at
           "dxbc_spirv_build", by name and bytes and in its order (a member
           recompiled, missing or added fails); "dxbc_spirv_build" must lie
           inside private/s5-tools whether or not it is on disk
  validation-350-vulkan-headers validation-350-vulkan-utility-libraries
  validation-350-spirv-headers validation-350-spirv-tools
  validation-350-vulkan-validation-layers
           supplemental validation sources: exact approved commit, tree, keyed
           path and version, an index identical to HEAD, and actual raw files
           and executable modes matching the authoritative Git objects;
           built: the nonempty installed_files map, required stage roles and
           aliases, confined to one owned run install prefix. The shared
           tools/s5/validation_tools.py performs both full and source checks.
  spirv-headers spirv-tools spirv-cross sdl-shadercross vulkan-utility-libraries
  robin-hood-hashing vulkan-validation-layers
           S5's shader tools (tools/s5/build_shader_tools.sh), approved by
           name on 2026-10-04 (Documentation/decisions/
           2026-10-04-S5-shader-tool-downloads.md)
           sources: the git checkout at "path" (inside private/s5-tools) has
           HEAD "commit" and HEAD^{tree} "tree", a clean status (untracked
           and ignored files listed) and no index entry git status skips
           built: each of "binary", "disassembler", "assembler", "optimizer",
           "library" and "manifest" the entry pins hashes to its "<field>_sha256"

Git runs with --no-optional-locks, passed on to the git commands it starts in
the submodules, so no `git status` rewrites an index; and with LC_ALL=C, so
the "Entering '<submodule>'" lines by which a foreach line is attributed to
its submodule are not translated.

Not covered: settings in the checkout that change what git reports about the
files themselves (a clean filter, an fsmonitor hook, core.fileMode=false);
whether an extracted source tree still matches its archive (the build script
extracts afresh on every run); that a built file was produced from the pinned
sources (the sha256 holds its bytes and the stamp names its version); every
installed file other than the one each pin names (headers, pkg-config files,
the static libraries, and the development links libSDL3.so and
libdxvk_d3d9.so that a linker follows); which library file a process
maps, which the loader chooses at run time and the player is to check when it
starts (agents-journals/s5/2026-09-29-dxvk-native.md, "Next"); and four fields
of sdl3's "alsa_headers" that only the builder reads, each time it runs:
"headers_manifest_sha256" (which follows from the package's own hash), and
"runtime_path", "runtime_realpath" and "runtime_sha256", the system's
libasound.so.2 outside private/s5-tools, which an operating-system update may
replace and which should not fail unrelated pushes when it does.

Relative paths resolve against the repository root. A tool with no verifier
here is reported unverifiable, never passed silently. An operating-system
error in a verifier (a file that cannot be read, a directory that cannot be
searched) fails that tool's line, and the run goes on to the next tool. The
last line is `tool pins OK: N tools verified, K unverifiable (listed)` or a
FAIL line; exit 1 on any mismatch, missing path or missing required field.

    check_pins.py [--root PATH] [--pins PATH] [--only TOOL ...] [--sources-only]

`--only TOOL` (repeatable) checks the named tools alone; a name the pins do
not hold fails. `--sources-only`, which needs `--only`, checks the sources of
sdl3, sdl3-stock, glslang, dxvk and the shader tools without what was built
from them, and fails any other tool: it is the check tools/s5/build_host_deps.sh
runs before it builds, so the script and this gate hold the sources to one set
of conditions (the builder holds the ALSA package to more, listed under "Not
covered").
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gitlog import project_root  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "s5"))
import validation_tools  # noqa: E402

PINS = "tools/pins.json"
VERSION_CORE = re.compile(r"\d+(?:\.\d+)*(?:\+\d+)?")
# Where S5's host dependencies live: every path their pins name resolves inside it.
S5_TOOLS = "private/s5-tools"
# `git submodule status`: a leading space is a submodule initialised and checked
# out at the recorded commit; "-" uninitialised, "+" another commit, "U" conflicts.
SUBMODULE_AT_RECORD = re.compile(r"^ [0-9a-f]{40} ")
# The status a clean checkout prints nothing for, run in it and in every submodule. The
# flags override the repository's own status.showUntrackedFiles, diff.ignoreSubmodules
# and submodule.<name>.ignore; --ignored=matching lists what its ignore rules hide.
STATUS = ("status", "--porcelain=v1", "--ignore-submodules=none", "--untracked-files=all",
          "--ignored=matching")
# How `git submodule foreach` names the submodule it enters (git runs with LC_ALL=C).
ENTERING = re.compile(r"Entering '(.*)'")


class Mismatch(Exception):
    """A pin the installation contradicts."""


class Unverifiable(Exception):
    """A pin this script cannot compare with the installation; the reason."""


def core(version: str) -> str:
    match = VERSION_CORE.search(version)
    if match is None:
        raise Mismatch(f"pinned version {version!r} has no numeric core")
    return match.group(0)


def resolve(root: Path, entry: dict) -> Path:
    if "path" not in entry:
        raise Mismatch("entry has no path to verify against")
    path = Path(str(entry["path"]))
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise Mismatch(f"path missing: {path}")
    return path


def run(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Mismatch(f"cannot run {argv[0]}: {error}") from None
    return (result.stdout + result.stderr).strip()


def git(checkout: Path, *args: str) -> str:
    """stdout of `git <args>` in `checkout`; a non-zero exit is a Mismatch naming stderr.

    --no-optional-locks keeps `git status` from rewriting the checkout's index, and the
    submodules' (git passes it on to the commands it starts as GIT_OPTIONAL_LOCKS=0).
    LC_ALL=C keeps the messages `in_submodules` reads untranslated."""
    argv = ["git", "--no-optional-locks", "-C", str(checkout), *args]
    try:
        result = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace",
                                timeout=120, env=dict(os.environ, LC_ALL="C"))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Mismatch(f"cannot run git {' '.join(args)}: {error}") from None
    if result.returncode != 0:
        raise Mismatch(f"git {' '.join(args)} exited {result.returncode}: "
                       f"{result.stderr.strip()}")
    return result.stdout


def in_submodules(checkout: Path, *args: str) -> list[tuple[str, str]]:
    """(submodule, line) for each line `git <args>` prints in every submodule of `checkout`,
    nested ones included, the submodule named by its path ("s/inner")."""
    found: list[tuple[str, str]] = []
    where = "?"
    for line in git(checkout, "submodule", "foreach", "--recursive", "git", *args).splitlines():
        entering = ENTERING.fullmatch(line)
        if entering:
            where = entering.group(1)
        else:
            found.append((where, line))
    return found


def listed(lines: list[str]) -> str:
    more = f"; and {len(lines) - 5} more" if len(lines) > 5 else ""
    return f"{'; '.join(lines[:5])}{more}"


def first_line(text: str) -> str:
    return text.splitlines()[0].strip() if text.strip() else ""


def expect(observed: str, pinned: str, source: str) -> str:
    if observed != pinned:
        raise Mismatch(f"{source} reports {observed!r}, pinned {pinned!r}")
    return f"{source} reports {observed}"


def expect_contains(observed: str, pinned: str, source: str) -> str:
    wanted = core(pinned)
    if not re.search(r"(?<![0-9.])" + re.escape(wanted) + r"(?![0-9]|\.[0-9])", observed):
        raise Mismatch(f"{source} reports {observed!r}, pinned {pinned!r}")
    return f"{source} reports {observed!r}, contains {wanted}"


# --- verifiers: (root, entry) -> detail text ------------------------------


def verify_python(root: Path, entry: dict) -> str:
    return expect(sys.version.split()[0], entry["version"], "sys.version")


def verify_package(name: str) -> Callable[[Path, dict], str]:
    def verifier(root: Path, entry: dict) -> str:
        try:
            observed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise Mismatch(f"package {name} is not installed") from None
        return expect(observed, entry["version"], f"importlib.metadata({name})")
    return verifier


def verify_version_flag(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    return expect_contains(first_line(run([str(path), "--version"])), entry["version"],
                           f"{path.name} --version")


def verify_java(root: Path, entry: dict) -> str:
    home = resolve(root, entry)
    # The build suffix ("+1") is on the runtime line, not the first line.
    output = " / ".join(l.strip() for l in run([str(home / "bin" / "java"), "-version"]).splitlines())
    return expect_contains(output, entry["version"], "java -version")


def verify_zig(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    return expect(run([str(path), "version"]), entry["version"], "zig version")


def verify_ghidra(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    properties = path / "Ghidra" / "application.properties"
    if not properties.is_file():
        raise Mismatch(f"missing {properties}")
    wanted = f"application.version={core(entry['version'])}"
    lines = properties.read_text(encoding="utf-8", errors="replace").splitlines()
    observed = [l.strip() for l in lines if l.startswith("application.version=")]
    if wanted not in observed:
        raise Mismatch(f"{properties.name} has {observed}, pinned {wanted!r}")
    return f"{properties.name} has {wanted}"


def verify_proton(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    pinned = entry.get("prefix_version")
    if not isinstance(pinned, str):
        raise Unverifiable("entry carries no prefix_version to compare with the prefix's version file")
    version_file = path / "version"
    if not version_file.is_file():
        raise Mismatch(f"missing {version_file}")
    return expect(version_file.read_text(encoding="utf-8").strip(), pinned, "prefix version file")


def verify_unity(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    return expect(first_line(run([str(path), "--version"])), entry["version"], "unity --version")


def verify_sa_tools(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    version_file = path / "satoolsver.txt"
    if not version_file.is_file():
        raise Mismatch(f"missing {version_file}")
    return expect(version_file.read_text(encoding="utf-8").strip(), entry["version"],
                  "satoolsver.txt")


def verify_path_exists(root: Path, entry: dict) -> str:
    return f"path exists: {resolve(root, entry)}"


def verify_git_commit(root: Path, entry: dict) -> str:
    path = resolve(root, entry)
    if "commit" not in entry:
        raise Mismatch("entry pins no commit")
    return expect(git(path, "rev-parse", "HEAD").strip(), entry["commit"], "git rev-parse HEAD")


# --- S5's host dependencies: sources, then what was built from them ----------


def pinned(entry: dict, key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
        raise Mismatch(f"entry pins no {key}")
    return value


def in_s5_tools(root: Path, entry: dict, key: str) -> Path:
    """entry[key] as a path that exists and resolves, symlinks followed, inside private/s5-tools."""
    path = Path(pinned(entry, key))
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise Mismatch(f"path missing: {path}")
    real = path.resolve()
    if not real.is_relative_to((root / S5_TOOLS).resolve()):
        raise Mismatch(f"{key} {path} resolves to {real}, outside {S5_TOOLS}")
    return real


def file_bytes(path: Path) -> bytes:
    if not path.is_file():
        raise Mismatch(f"not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as error:
        raise Mismatch(f"cannot read {path}: {error.strerror or error}") from None


def held(root: Path, entry: dict, key: str, digest_key: str) -> tuple[Path, bytes, str]:
    """The file entry[key] reaches, symlinks followed, held to the sha256 entry[digest_key]
    pins. The detail names a link and the file it reaches: "libSDL3.so.0 -> libSDL3.so.0.4.16"."""
    path = in_s5_tools(root, entry, key)
    given = Path(pinned(entry, key))
    given = given if given.is_absolute() else root / given
    name = f"{given.name} -> {path.name}" if given.is_symlink() else path.name
    data = file_bytes(path)
    detail = expect(hashlib.sha256(data).hexdigest(), pinned(entry, digest_key),
                    f"sha256({name})")
    return path, data, detail


def holds_stamp(path: Path, data: bytes, stamp: str) -> str:
    if b"\0" + stamp.encode("utf-8") + b"\0" not in data:
        raise Mismatch(f"{path.name} does not hold the NUL-delimited string {stamp!r}")
    return f"{path.name} holds {stamp!r}"


def archive_member(archive: Path, member: str) -> str:
    """A tar archive's member, as stripped text."""
    try:
        with tarfile.open(archive, "r:*") as tar:
            handle = tar.extractfile(member)
            text = handle.read() if handle is not None else None
    except KeyError:
        raise Mismatch(f"{archive.name} has no member {member}") from None
    except (tarfile.TarError, OSError, EOFError, zlib.error) as error:
        raise Mismatch(f"cannot read {archive.name} as a tar archive: {error}") from None
    if text is None:
        raise Mismatch(f"{archive.name}'s {member} is not a regular file")
    return text.decode("utf-8", "replace").strip()


# The project's SDL patches (patches/sdl3; the user's second rulings of 2026-10-04) and the
# identity the patched build stamps into SDL's revision as SDL_VENDOR_INFO.
SDL_PATCHES = "patches/sdl3"
SDL_VENDOR = "sa2-recomp sdl3-patches "
SDL_NOT_UPSTREAM = "NOT FROM THE SDL PROJECT"
# Bytes no stock SDL holds: the vendor stamp's and the patch's property names' (a patched
# library built without its mark still holds the second).
SDL_PATCH_MARKS = (b"sa2-recomp", b"sa2recomp")


def sdl3_patches(root: Path, entry: dict) -> str:
    """The pinned patch set, held to the files under patches/sdl3 and to vendor_info: the
    detail, or "" for a stock source (no patches and no vendor_info)."""
    rows = entry.get("patches")
    if rows is None or rows == []:
        if "vendor_info" in entry:
            raise Mismatch("entry pins vendor_info with no patches")
        # A stock pin beside a tracked patch: the build would apply none of it.
        left = sorted(f"{SDL_PATCHES}/{p.name}" for p in (root / SDL_PATCHES).glob("*.patch"))
        if left:
            raise Mismatch(f"entry pins no patches, and {left} are under {SDL_PATCHES}/: a stock pin beside a "
                           "tracked patch")
        return ""
    if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get("path"), str)
                                             and isinstance(r.get("sha256"), str) for r in rows):
        raise Mismatch("patches is not a list of {path, sha256}")
    on_disk = sorted(f"{SDL_PATCHES}/{p.name}" for p in (root / SDL_PATCHES).glob("*.patch"))
    named = [r["path"] for r in rows]
    if named != on_disk:
        raise Mismatch(f"the patches under {SDL_PATCHES}/ are {on_disk}, the pins name {named}")
    commit, tarball = pinned(entry, "commit"), pinned(entry, "sha256")
    digests = []
    for row in rows:
        path = root / row["path"]
        if path.is_symlink() or not path.is_file():
            raise Mismatch(f"patch {row['path']} is not a regular file")
        data = file_bytes(path)
        digest = hashlib.sha256(data).hexdigest()
        if digest != row["sha256"]:
            raise Mismatch(f"sha256({path.name}) reports {digest!r}, pinned {row['sha256']!r}: not the "
                           f"pinned patch")
        text = data.decode("utf-8", "replace")
        at = re.search(r"^diff --git ", text, re.M)
        head = text if at is None else text[:at.start()]
        for words in (SDL_NOT_UPSTREAM, commit, tarball):
            if words not in head:
                raise Mismatch(f"{path.name}'s header does not name {words!r}")
        digests.append(digest)
    vendor = SDL_VENDOR + hashlib.sha256("".join(d + "\n" for d in digests).encode()).hexdigest()[:12]
    if pinned(entry, "vendor_info") != vendor:
        raise Mismatch(f"vendor_info {entry['vendor_info']!r} is not the pinned patches' identity {vendor!r}")
    return f"{len(rows)} patches under {SDL_PATCHES} at their pins, identity {vendor!r}"


def sdl3_sources(root: Path, entry: dict) -> tuple[str, str]:
    """The archive's hash, the commit its .git-hash records and the revision its REVISION.txt
    names, the ALSA headers package's hash and size, and the patches: (detail, revision)."""
    version = pinned(entry, "version")
    commit = pinned(entry, "commit")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise Mismatch(f"commit {commit!r} is not 40 lowercase hex digits")
    archive, _, detail = held(root, entry, "path", "sha256")
    member = f"SDL3-{version}/REVISION.txt"
    stamp = archive_member(archive, f"SDL3-{version}/.git-hash")
    if stamp != commit:
        raise Mismatch(f"SDL3-{version}/.git-hash reads {stamp!r}, pinned commit {commit!r}")
    revision = archive_member(archive, member)
    if revision != f"release-{version}-0-g{commit[:9]}":
        raise Mismatch(f"{member} reads {revision!r}, not release-{version}-0-g{commit[:9]}")
    alsa = entry.get("alsa_headers")
    if not isinstance(alsa, dict):
        raise Mismatch("entry pins no alsa_headers")
    for key in ("path", "sha256"):
        if not isinstance(alsa.get(key), str) or not alsa[key]:
            raise Mismatch(f"entry pins no alsa_headers.{key}")
    _, package, alsa_detail = held(root, alsa, "path", "sha256")
    if type(alsa.get("bytes")) is not int or alsa["bytes"] != len(package):
        raise Mismatch(f"{Path(alsa['path']).name} holds {len(package)} bytes, "
                       f"not the pinned {alsa.get('bytes')!r}")
    patches = sdl3_patches(root, entry)
    detail = (f"{detail}; SDL3-{version}/.git-hash reads {commit}; {member} reads {revision}; "
              f"{alsa_detail}")
    return (f"{detail}; {patches}" if patches else detail), revision


def verify_sdl3_sources(root: Path, entry: dict) -> str:
    return sdl3_sources(root, entry)[0]


def sdl3_library(library: Path, data: bytes, revision: str, vendor: str | None) -> str:
    """The SDL library's own marks: patched, its revision with the vendor stamp and the patch's
    property names (a stamp on an unpatched build, the builder skipping the patch, fails here and
    not only on its hash); stock, the bare revision and no byte of the patch's marks."""
    if vendor:
        stamp = holds_stamp(library, data, f"SDL-{revision} ({vendor})")
        if SDL_PATCH_MARKS[1] not in data:
            raise Mismatch(f"{library.name} carries the vendor stamp but none of the patch's property names "
                           f"({SDL_PATCH_MARKS[1].decode()!r}): an unpatched SDL marked as patched")
        stamp += ", and the patch's property names"
    else:
        stamp = holds_stamp(library, data, f"SDL-{revision}")
        marked = [m.decode() for m in SDL_PATCH_MARKS if m in data]
        if marked:
            raise Mismatch(f"{library.name} holds {marked}: a patched SDL where the pins name a stock one")
        stamp += ", no patch marks"
    return f"{stamp}; {holds_stamp(library, data, 'libasound.so.2')}"


def verify_sdl3(root: Path, entry: dict) -> str:
    detail, revision = sdl3_sources(root, entry)
    library, data, hashed = held(root, entry, "library", "library_sha256")
    return f"{detail}; {hashed}; {sdl3_library(library, data, revision, entry.get('vendor_info'))}"


def under_prefix(root: Path, entry: dict, keys: tuple[str, ...]) -> Path:
    """The entry's "prefix" inside private/s5-tools, and each path `keys` names inside it."""
    prefix = in_s5_tools(root, entry, "prefix")
    for key in keys:
        path = Path(pinned(entry, key))
        path = path if path.is_absolute() else root / path
        if not path.resolve().is_relative_to(prefix):
            raise Mismatch(f"{key} {path} is not under the prefix {prefix}")
    return prefix


STOCK_FILES = ("library", "dxvk_library", "glslang_binary", "dxbc_spirv_archive")
# The stock prefix's files held to another entry's pin (one pin per distinct file); the entry
# must not carry a hash of its own for them, which this gate would never read.
STOCK_HELD_BY = {"dxvk_library_sha256": "dxvk.library_sha256", "glslang_binary_sha256": "glslang.binary_sha256",
                 "dxbc_spirv_archive_sha256": "dxvk.dxbc_spirv_archive_sha256"}


def verify_sdl3_stock_sources(root: Path, entry: dict, tools: dict) -> str:
    own = [f"{k} (held to {STOCK_HELD_BY[k]})" for k in STOCK_HELD_BY if k in entry]
    if own:
        raise Mismatch(f"entry pins {own}: the stock prefix's DXVK, glslang and archive are held to the patched "
                       "configuration's pins, never to a hash of its own")
    base = tools.get("sdl3")
    if not isinstance(base, dict):
        raise Mismatch("the pins hold no sdl3 entry, whose sources the stock build uses")
    expect(pinned(entry, "version"), pinned(base, "version"), "sdl3-stock's version (held to sdl3's)")
    return f"sdl3's sources: {sdl3_sources(root, base)[0]}"


def verify_sdl3_stock(root: Path, entry: dict, tools: dict) -> str:
    detail = verify_sdl3_stock_sources(root, entry, tools)
    _, revision = sdl3_sources(root, tools["sdl3"])
    prefix = under_prefix(root, entry, STOCK_FILES)
    # A prefix of its own: not private/s5-tools whole, nor a directory holding the patched
    # configuration's library (tools/s5/build_host_deps.sh refuses both before it builds).
    if prefix == (root / S5_TOOLS).resolve():
        raise Mismatch(f"prefix {prefix} is {S5_TOOLS} itself, not a prefix of its own")
    base_library = Path(pinned(tools.get("sdl3") or {}, "library"))
    base_library = (base_library if base_library.is_absolute() else root / base_library).resolve()
    if base_library.is_relative_to(prefix):
        raise Mismatch(f"prefix {prefix} holds sdl3's library {base_library}: the patched configuration's prefix")
    # Its own build's thin archive: the patched build's would compare the stock archive with
    # objects the stock build did not make.
    builds = {key: Path(pinned(e, "dxbc_spirv_build")) for key, e in (("stock", entry), ("dxvk", tools.get("dxvk") or {}))}
    builds = {key: (b if b.is_absolute() else root / b).resolve() for key, b in builds.items()}
    if builds["stock"] == builds["dxvk"]:
        raise Mismatch(f"dxbc_spirv_build {builds['stock']} is dxvk's: the patched build's thin archive, not the "
                       "stock build's own")
    library, data, hashed = held(root, entry, "library", "library_sha256")
    out = [detail, f"prefix {prefix.name}", hashed, sdl3_library(library, data, revision, None)]
    dxvk, glslang = tools.get("dxvk") or {}, tools.get("glslang") or {}
    # The stock prefix's other files, held to the patched configuration's pins (their bytes).
    held_by = dict(entry, **{key: pinned(tools.get(by.split(".")[0]) or {}, by.split(".")[1])
                             for key, by in STOCK_HELD_BY.items()})
    lib, dx_data, dx_hashed = held(root, held_by, "dxvk_library", "dxvk_library_sha256")
    out += [f"{dx_hashed} (dxvk's pin)", holds_stamp(lib, dx_data, pinned(dxvk, "tag"))]
    binary, _, gl_hashed = held(root, held_by, "glslang_binary", "glslang_binary_sha256")
    out += [f"{gl_hashed} (glslang's pin)", expect_contains(first_line(run([str(binary), "--version"])),
                                                           pinned(glslang, "version"), f"{binary.name} --version")]
    out.append(f"{dxbc_archive(root, held_by)} (dxvk's pin)")
    return "; ".join(out)


def verify_glslang_sources(root: Path, entry: dict) -> str:
    pinned(entry, "version")
    return held(root, entry, "path", "sha256")[2]


def verify_glslang(root: Path, entry: dict) -> str:
    detail = verify_glslang_sources(root, entry)
    binary, _, hashed = held(root, entry, "binary", "binary_sha256")
    reported = expect_contains(first_line(run([str(binary), "--version"])), entry["version"],
                               f"{binary.name} --version")
    return f"{detail}; {hashed}; {reported}"


def verify_dxvk_sources(root: Path, entry: dict) -> str:
    version, tag, commit = (pinned(entry, key) for key in ("version", "tag", "commit"))
    if tag != f"v{version}":
        raise Mismatch(f"tag {tag!r} is not 'v' + version {version!r}")
    checkout = in_s5_tools(root, entry, "path")
    expect(git(checkout, "rev-parse", "HEAD").strip(), commit, "git rev-parse HEAD")
    try:
        tagged = git(checkout, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}")
    except Mismatch:
        raise Mismatch(f"the checkout has no tag {tag}") from None
    expect(tagged.strip(), commit, f"refs/tags/{tag}")
    # The checkout's own status first: `submodule foreach` itself fails on a broken .gitmodules.
    dirty = git(checkout, *STATUS).splitlines()
    if not dirty:
        dirty = [f"{where}: {line}" for where, line in in_submodules(checkout, *STATUS)]
    if dirty:
        raise Mismatch(f"the working tree is not clean: {listed(dirty)}")
    skipped = [line for line in git(checkout, "ls-files", "-v").splitlines()
               if not line.startswith("H ")]
    skipped += [f"{where}: {line}" for where, line in in_submodules(checkout, "ls-files", "-v")
                if not line.startswith("H ")]
    if skipped:
        raise Mismatch("index entries git status does not look at (lowercase: assume-unchanged;"
                       f" S: skip-worktree): {listed(skipped)}")
    submodules = git(checkout, "submodule", "status", "--recursive").splitlines()
    moved = [line for line in submodules if not SUBMODULE_AT_RECORD.match(line)]
    if moved:
        raise Mismatch(f"submodules not at their recorded commits: {moved}")
    return (f"HEAD and refs/tags/{tag} are {commit}; working tree clean in the checkout and "
            f"every submodule, nothing ignored, no index entry skipped; "
            f"{len(submodules)} submodules at their recorded commits")


def ar_members(path: Path, data: bytes) -> tuple[bool, list[tuple[str, bytes | None]]]:
    """A GNU ar archive's members in order, (name, bytes): (thin, members). A thin archive's
    members hold no bytes here (None); its names are the paths it stores."""
    if data.startswith(b"!<thin>\n"):
        thin = True
    elif data.startswith(b"!<arch>\n"):
        thin = False
    else:
        raise Mismatch(f"{path.name} is not an ar archive")
    offset, longnames, members = 8, b"", []
    while offset < len(data):
        head = data[offset:offset + 60]
        if len(head) != 60 or head[58:60] != b"`\n":
            raise Mismatch(f"{path.name}: a member header at byte {offset} is not ar's")
        name, size = head[:16].decode("ascii", "replace").rstrip(), int(head[48:58].decode("ascii").strip())
        body = offset + 60
        if name == "//":                          # the long-name table, always stored
            longnames = data[body:body + size]
            offset = body + size + size % 2
            continue
        stored = not thin or name in ("/", "/SYM64/")
        if name.startswith("/") and name[1:].isdigit():
            start = int(name[1:])
            name = longnames[start:longnames.index(b"\n", start)].decode("utf-8", "replace")
        name = name[:-1] if name.endswith("/") and name not in ("/", "/SYM64/") else name
        if name not in ("/", "/SYM64/"):
            members.append((name, data[body:body + size] if stored else None))
        offset = body + (size + size % 2 if stored else 0)
    return thin, members


def dxbc_archive(root: Path, entry: dict) -> str:
    """The dxbc-spirv archive the entry pins: its hash, a regular archive, and, while DXVK's
    build is on disk, exactly the thin archive's members in order, by name and bytes."""
    path, data, hashed = held(root, entry, "dxbc_spirv_archive", "dxbc_spirv_archive_sha256")
    thin, members = ar_members(path, data)
    if thin:
        raise Mismatch(f"{path.name} is a thin archive: its members are files elsewhere, not held by its hash")
    if not members:
        raise Mismatch(f"{path.name} holds no member")
    build = Path(pinned(entry, "dxbc_spirv_build"))
    build = build if build.is_absolute() else root / build
    # Inside private/s5-tools whether or not it is on disk: a path elsewhere is never DXVK's build
    # here, and a missing one would otherwise pass unconfined.
    if not build.resolve().is_relative_to((root / S5_TOOLS).resolve()):
        raise Mismatch(f"dxbc_spirv_build {build} resolves to {build.resolve()}, outside {S5_TOOLS}")
    if not build.exists():
        return f"{hashed}; {len(members)} members; DXVK's build is not on disk ({build}): members not compared"
    build = in_s5_tools(root, entry, "dxbc_spirv_build")
    built_thin, built = ar_members(build, file_bytes(build))
    if not built_thin:
        raise Mismatch(f"{build} is not DXVK's thin archive")
    want = [(Path(n).name, file_bytes(build.parent / n)) for n, _ in built]
    have = [(n, b) for n, b in members]
    if [n for n, _ in have] != [n for n, _ in want]:
        raise Mismatch(f"{path.name} holds members {[n for n, _ in have][:6]}..., DXVK's build "
                       f"{[n for n, _ in want][:6]}... ({len(have)} and {len(want)})")
    differ = [n for (n, b), (_, w) in zip(have, want) if b != w]
    if differ:
        raise Mismatch(f"{path.name}'s members {differ[:5]} are not DXVK's build's objects")
    return f"{hashed}; {len(members)} members, each DXVK's build's object ({build.name}, thin)"


def verify_dxvk(root: Path, entry: dict) -> str:
    detail = verify_dxvk_sources(root, entry)
    library, data, hashed = held(root, entry, "library", "library_sha256")
    return f"{detail}; {hashed}; {holds_stamp(library, data, entry['tag'])}; {dxbc_archive(root, entry)}"


# S5's shader tools: a checkout at its commit and tree, and the files built from it.
SHADER_TOOLS = ("spirv-headers", "spirv-tools", "spirv-cross", "sdl-shadercross", "vulkan-utility-libraries",
                "robin-hood-hashing", "vulkan-validation-layers")
SHADER_OUTPUTS = ("binary", "disassembler", "assembler", "optimizer", "library", "manifest")


def verify_checkout_sources(root: Path, entry: dict) -> str:
    commit, tree = pinned(entry, "commit"), pinned(entry, "tree")
    checkout = in_s5_tools(root, entry, "path")
    expect(git(checkout, "rev-parse", "HEAD").strip(), commit, "git rev-parse HEAD")
    expect(git(checkout, "rev-parse", "HEAD^{tree}").strip(), tree, "git rev-parse HEAD^{tree}")
    dirty = git(checkout, *STATUS).splitlines()
    if dirty:
        raise Mismatch(f"the working tree is not clean: {listed(dirty)}")
    skipped = [line for line in git(checkout, "ls-files", "-v").splitlines() if not line.startswith("H ")]
    if skipped:
        raise Mismatch(f"index entries git status does not look at: {listed(skipped)}")
    return f"HEAD {commit}, tree {tree}; working tree clean, nothing ignored, no index entry skipped"


def verify_checkout(root: Path, entry: dict) -> str:
    out = [verify_checkout_sources(root, entry)]
    for field in SHADER_OUTPUTS:
        if field in entry or f"{field}_sha256" in entry:
            out.append(held(root, entry, field, f"{field}_sha256")[2])
    return "; ".join(out)


# The supplemental validation instrument has explicit full and source verifiers.
VALIDATION_TOOLS = (
    "validation-350-vulkan-headers",
    "validation-350-vulkan-utility-libraries",
    "validation-350-spirv-headers",
    "validation-350-spirv-tools",
    "validation-350-vulkan-validation-layers",
)


def verify_validation_pin(key: str, *, sources_only: bool) -> Callable[[Path, dict], str]:
    def verify(root: Path, entry: dict) -> str:
        try:
            return validation_tools.verify_pin(root, key, entry, sources_only=sources_only)
        except validation_tools.ValidationRefused as reason:
            raise Mismatch(str(reason)) from reason
    return verify


def verify_sdl2(root: Path, entry: dict) -> str:
    raise Unverifiable("only the runtime library is installed and no pinned path exists to query")



def verify_files(root: Path, entry: dict) -> str:
    """A downloaded file pinned by hash: "path" hashes to "sha256", and, when the entry names the
    package it was unpacked from, "package" hashes to "package_sha256"."""
    out = []
    for key, digest in (("path", "sha256"), ("package", "package_sha256")):
        if key == "package" and "package" not in entry:
            continue
        given = Path(pinned(entry, key))
        path = given if given.is_absolute() else root / given
        out.append(expect(hashlib.sha256(file_bytes(path)).hexdigest(), pinned(entry, digest),
                          f"sha256({path.name})"))
    return "; ".join(out)


# Downloads the user approved for S7, each pinned by its file's hash
# (Documentation/decisions/2026-10-11-S7-user-answers.md, answer 2: qemu-user;
# Documentation/decisions/2026-10-11-S7-host2-windows-downloads.md: libpng, zlib, zstd).
FILE_PINS = ("qemu-user", "libpng", "zlib", "zstd")

VERIFIERS: dict[str, Callable[[Path, dict], str]] = {
    "python": verify_python,
    "pefile": verify_package("pefile"),
    "iced-x86": verify_package("iced-x86"),
    "pytest": verify_package("pytest"),
    "gcc": verify_version_flag,
    "cmake": verify_version_flag,
    "ninja": verify_version_flag,
    "objdump": verify_version_flag,
    "meson": verify_version_flag,
    "pkg-config": verify_version_flag,
    "patch": verify_version_flag,
    "java": verify_java,
    "zig": verify_zig,
    "ghidra": verify_ghidra,
    "proton": verify_proton,
    "unity": verify_unity,
    "sa-tools": verify_sa_tools,
    "steamless": verify_path_exists,
    "sa2-mod-loader": verify_git_commit,
    "sdl2": verify_sdl2,
    "sdl3": verify_sdl3,
    "glslang": verify_glslang,
    "dxvk": verify_dxvk,
    **{name: verify_checkout for name in SHADER_TOOLS},
    **{name: verify_validation_pin(name, sources_only=False) for name in VALIDATION_TOOLS},
    **{name: verify_files for name in FILE_PINS},
}
# --sources-only: the same checks of the sources, without what was built from them.
SOURCE_VERIFIERS: dict[str, Callable[[Path, dict], str]] = {
    "sdl3": verify_sdl3_sources,
    "glslang": verify_glslang_sources,
    "dxvk": verify_dxvk_sources,
    **{name: verify_checkout_sources for name in SHADER_TOOLS},
    **{name: verify_validation_pin(name, sources_only=True) for name in VALIDATION_TOOLS},
    **{name: verify_files for name in FILE_PINS},
}
# Verifiers that read other entries as well (root, entry, every tool's entry).
WITH_TOOLS: dict[str, Callable[[Path, dict, dict], str]] = {"sdl3-stock": verify_sdl3_stock}
WITH_TOOLS_SOURCES: dict[str, Callable[[Path, dict, dict], str]] = {"sdl3-stock": verify_sdl3_stock_sources}


def check(root: Path, pins_path: Path, only: list[str] | None = None,
          sources_only: bool = False) -> tuple[list[str], bool]:
    """Report lines and whether the pins pass."""
    lines: list[str] = []
    try:
        doc = json.loads(pins_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [f"tool pins FAIL: cannot read {pins_path}: {error}"], False
    tools = doc.get("tools") if isinstance(doc, dict) else None
    if not isinstance(tools, dict) or not tools:
        return [f"tool pins FAIL: {pins_path} has no non-empty 'tools' object"], False

    failures: list[str] = []
    verified: list[str] = []
    unverifiable: list[str] = []
    unpinned: list[str] = []
    selected = sorted(tools.items())
    if only is not None:
        for name in sorted(set(only) - set(tools)):
            lines.append(f"{name}: FAIL no such tool in {pins_path}")
            failures.append(name)
        selected = [(name, entry) for name, entry in selected if name in only]
    table = SOURCE_VERIFIERS if sources_only else VERIFIERS
    for name, entry in selected:
        if not isinstance(entry, dict):
            lines.append(f"{name}: FAIL entry is not an object")
            failures.append(name)
            continue
        if entry.get("unpinned") is True and name in VALIDATION_TOOLS:
            lines.append(f"{name}: FAIL supplemental validation source must be pinned")
            failures.append(name)
            continue
        if entry.get("unpinned") is True:
            reason = entry.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                lines.append(f"{name}: FAIL unpinned without a reason")
                failures.append(name)
            else:
                lines.append(f"{name}: UNPINNED {reason.strip()}")
                unpinned.append(name)
            continue
        if "version" not in entry and "commit" not in entry:
            lines.append(f"{name}: FAIL entry carries neither version nor commit")
            failures.append(name)
            continue
        verifier = table.get(name)
        with_tools = (WITH_TOOLS_SOURCES if sources_only else WITH_TOOLS).get(name)
        if with_tools is not None:
            verifier = (lambda f: lambda r, e: f(r, e, tools))(with_tools)
        if verifier is None and sources_only:
            lines.append(f"{name}: FAIL no check of its sources in check_pins.py (--sources-only)")
            failures.append(name)
            continue
        if verifier is None:
            lines.append(f"{name}: UNVERIFIABLE no verifier for this tool in check_pins.py")
            unverifiable.append(name)
            continue
        try:
            lines.append(f"{name}: OK {verifier(root, entry)}")
            verified.append(name)
        except Unverifiable as reason:
            lines.append(f"{name}: UNVERIFIABLE {reason}")
            unverifiable.append(name)
        except Mismatch as reason:
            lines.append(f"{name}: FAIL {reason}")
            failures.append(name)
        except OSError as error:
            lines.append(f"{name}: FAIL {error}")
            failures.append(name)

    listed = ", ".join(unverifiable) if unverifiable else "none"
    summary = f"{len(verified)} tools verified, {len(unverifiable)} unverifiable ({listed})"
    if unpinned:
        summary += f", {len(unpinned)} unpinned ({', '.join(unpinned)})"
    if failures:
        lines.append(f"tool pins FAIL: {', '.join(failures)}; {summary}")
        return lines, False
    lines.append(f"tool pins OK: {summary}")
    return lines, True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=None,
                        help="repository root (default: git toplevel of cwd)")
    parser.add_argument("--pins", type=Path, default=None,
                        help=f"pins file to check (default: <root>/{PINS})")
    parser.add_argument("--only", action="append", default=None, metavar="TOOL",
                        help="check this tool alone; repeatable")
    parser.add_argument("--sources-only", action="store_true",
                        help="with --only: check the sources of sdl3, sdl3-stock, glslang, dxvk "
                             "and the shader and validation tools, not what was built from them")
    args = parser.parse_args(argv)
    if args.sources_only and not args.only:
        parser.error("--sources-only needs --only")
    root = project_root(args.root)
    pins_path = args.pins if args.pins is not None else root / PINS
    lines, ok = check(root, pins_path, args.only, args.sources_only)
    print("\n".join(lines))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
