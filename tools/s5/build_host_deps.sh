#!/bin/bash
# Builds S5's host dependencies from their pins into a private prefix under private/s5-tools,
# installing nothing system-wide: glslang (DXVK Native's shader compiler), SDL3 (the platform
# layer and DXVK Native's window system), DXVK Native's d3d9 against that SDL3, and the
# dxbc-spirv archive made of DXVK's own objects.
#
#   tools/s5/build_host_deps.sh                  the patched configuration (the default)
#   tools/s5/build_host_deps.sh --sdl3=stock     the stock configuration
#   tools/s5/build_host_deps.sh --sdl3=system    against a system SDL3
#   ... --fetch                                  first download any missing source archive or
#                                                checkout at its pin (never the ALSA headers
#                                                package, below)
#   SA2_BUILD_JOBS=N ...                         at most N parallel jobs (default: the CPUs, at
#                                                most 8); the bytes built do not depend on it
#
# The three SDL3 configurations (the user's second rulings of 2026-10-04,
# Documentation/decisions/2026-10-04-S5-user-rulings-2.md), each in its own prefix:
#
#   patched  private/s5-tools/install. SDL3 is the pinned tarball with the project's own patches
#            (patches/sdl3/, pinned under sdl3.patches) applied strictly by
#            tools/s5/sdl3_patches.py: the pinned GNU patch (tools.patch) in the C locale with
#            no fuzz, a dry run first, and the build stops with the patch's name and patch's own
#            words when a hunk does not apply exactly (or only at an offset), when an apply
#            changes nothing, or when a patch is not the pinned one. SDL is configured with SDL_VENDOR_INFO = sdl3.vendor_info, so
#            the library's revision string reads "SDL-<REVISION.txt> (sa2-recomp sdl3-patches
#            <id>)": marked as altered, as SDL's zlib licence asks, and naming its patch set.
#            Pins: sdl3, dxvk, glslang.
#   stock    the prefix sdl3-stock.prefix pins (private/s5-tools/install-sdl3-stock): the same
#            tarball with no patch and no vendor string. Its SDL reports neither of the patch's
#            device properties; host 2 detects that at run time and reports the shader-emulation
#            fallback as owed (its report's sdl_gpu.fallback_owed). The fallback itself is phase
#            B's work, not built yet.
#            Pins: sdl3-stock (its SDL library), and the dxvk and glslang pins for its DXVK,
#            glslang and dxbc-spirv files, the patched configuration's bytes.
#   system   private/s5-tools/install-sdl3-system: no SDL3 is built; DXVK is built against the
#            SDL3 pkg-config finds (SDL3_SYSTEM_PKG_CONFIG_PATH may name the directory holding
#            its sdl3.pc), refused below 3.4.0 or without SDL_GetGPUDeviceProperties, the entry
#            point the run-time detection reads. A system SDL3 is not pinned, so nothing here
#            holds it to a hash: the build prints what it found and writes it to
#            build-sdl3-system/sdl3-system.json, and the gates use the stock configuration. No
#            player is built on it: host/player/generate_expectations.py refuses an install no
#            pin names. This project's own SDL3 builds are refused as a system one: anything under
#            a private/s5-tools directory (this tree's or another checkout's: an install, the
#            stock prefix, or one kept aside), a library holding the patch's marks (sa2-recomp,
#            sa2recomp: a patched build from any checkout), and a library whose sha256 the sdl3
#            or sdl3-stock pin holds.
#
# One build runs at a time on a private/s5-tools: every configuration extracts and checks the
# ALSA headers in one place (src/alsa-headers, build/alsa-headers.json), so a second run while
# one holds private/s5-tools/.build_host_deps.lock stops before it writes anything.
#
# Each configuration works in its own directories: patched in src/, build/ and install/; the
# others in src-sdl3-<config>/, build-sdl3-<config>/ and their prefix. Every file a build
# installs must lie under its prefix (the CMake install manifests and meson's install log
# are read), so nothing replaces or shadows a system SDL3; the players link the prefix's
# libSDL3.so.0 by DT_RPATH, which LD_LIBRARY_PATH does not override (host/player/build.sh).
#
# Every URL, tag, commit, hash and path comes from tools/pins.json ("dxvk", "sdl3",
# "sdl3-stock", "glslang"), and so do the build tools ("cmake", "ninja", "meson",
# "pkg-config", "patch", and "gcc" with the g++ beside it). Before anything is built, the pin gate's own
# check of the sources runs (check_pins.py --sources-only): the three archives (SDL3's,
# glslang's and the ALSA headers package) hash to their pins, SDL3's records the pinned commit
# (its .git-hash, and its REVISION.txt), the patches are the pinned ones, and the DXVK checkout
# is at the pinned commit and tag, clean in itself and in every submodule, nested ones
# included, with nothing ignored, no index entry git status skips, and every submodule at the
# commit its superproject records. Then the gate checks the six build tools against their
# pins, and the script requires g++ to report the gcc pin's version. After the build the gate
# runs in full on the configuration's pins, and the script fails unless each installed file
# the pins name hashes to its pin. It prints those sha256s first, so a deliberate rebuild moves
# the pins in the commit that moves the install (AGENTS.md rule 8). A library's pin names the
# soname link the loader follows (libSDL3.so.0, libdxvk_d3d9.so.0), and what is hashed and
# printed is the file that link reaches.
#
# The dxbc-spirv archive (R1's U1): DXVK's build compiles dxbc-spirv into a meson thin archive
# (build/dxvk/subprojects/dxbc-spirv/libdxbc_spv.a) over the very objects linked into
# libdxvk_d3d9.so. The builder copies those objects, in the thin archive's order, into a real
# deterministic archive (ar rcsD: no timestamps, owners or modes) at <prefix>/lib/libdxbc_spv.a,
# and prints each member's sha256, so host 2's translator links the machine code DXVK runs.
#
# Every run starts clean: the source archives and the ALSA headers are extracted afresh; every
# build directory is removed and configured anew (cmake --fresh, a new meson directory),
# because a tree re-extracted with the archive's older timestamps would otherwise leave ninja
# keeping objects compiled from an edited tree; the previous install is moved aside, and put
# back only if the build does not finish; and the build tools run in a fixed environment (env
# -i with HOME, LC_ALL, PATH = <install>/bin:/usr/bin:/bin, CC and CXX from the gcc pin,
# PKG_CONFIG from its pin, and for DXVK PKG_CONFIG_PATH = <install>/lib/pkgconfig, or the
# system's for --sdl3=system), so no CC, CFLAGS, LDFLAGS, CMAKE_PREFIX_PATH or PKG_CONFIG_PATH
# of the caller's reaches the build. The log records each tool's version. A run whose final
# check fails leaves the new install in place and the one before it at <prefix>.previous, and
# the next run refuses to start until one of the two has been removed, so it never deletes an
# install that was kept aside. Stopped by a signal before the build has finished, the script
# puts the previous install back; it exits 128 plus the signal's number.
#
# --fetch downloads each missing archive with `curl -f` to a .part file, checks its sha256 and
# only then moves it into place; it clones DXVK without submodules into a .part directory,
# checks the commit, and only then fetches the submodules that commit records and moves the
# checkout into place. The downloads are the three the user approved on 2026-09-29
# (Documentation/decisions/2026-09-29-S5-host-downloads.md). An archive already on disk is
# never replaced: glslang's is GitHub's generated archive of the tag, which GitHub does not
# promise to keep byte for byte, so the copy under private/s5-tools/dl is the source of truth.
#
# SDL3's one sound backend is dynamic ALSA. Its exact Debian development-header archive and
# installed libasound.so.2 are pinned under sdl3.alsa_headers. The archive must already be
# staged under private/s5-tools/dl; --fetch still downloads only the three approved source
# dependencies. Fresh extraction validates the complete header manifest and records
# archive/header/runtime identities before the previous install moves aside. No system package
# is installed. The download was ratified by the user on 2026-10-02
# (Documentation/decisions/2026-10-02-S5-alsa-headers-download.md). PulseAudio, PipeWire, JACK
# and sndio stay off. X screensaver and XTest stay off because their development packages are
# absent. Other unavailable features are printed from configure.
set -euo pipefail

usage() {
  echo "usage: tools/s5/build_host_deps.sh [--fetch] [--sdl3=patched|stock|system]" >&2
  exit 2
}
FETCH=0
CONFIG=patched
for arg in "$@"; do
  case "$arg" in
    --fetch) FETCH=1 ;;
    --sdl3=patched|--sdl3=stock|--sdl3=system) CONFIG=${arg#--sdl3=} ;;
    *) echo "build_host_deps.sh: unknown argument: $arg" >&2; usage ;;
  esac
done

die() { echo "build_host_deps.sh: $*" >&2; exit 1; }
# The caller's repository variables would point git at another repository.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR \
  GIT_ALTERNATE_OBJECT_DIRECTORIES
ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
[ -f "$ROOT/tools/pins.json" ] && [ -f "$ROOT/AGENTS.md" ] || die "not a repository root: $ROOT"
# The physical directory: in a worktree whose private/ is a symlink to the main tree's, the
# build must run in, install into and embed the one real private/s5-tools, never a path
# through the worktree, which is deleted with it.
mkdir -p "$ROOT/private/s5-tools"
B=$(cd "$ROOT/private/s5-tools" && pwd -P)
exec 9> "$B/.build_host_deps.lock"
/usr/bin/flock -n 9 || die "another build_host_deps.sh is running on $B (it holds $B/.build_host_deps.lock):" \
  "the configurations share src/alsa-headers and build/alsa-headers.json, so one build runs at a time"
PY=$ROOT/.venv/bin/python
GATE=("$PY" "$ROOT/Documentation/scripts/check_pins.py" --root "$ROOT")
PATCHES=("$PY" "$ROOT/tools/s5/sdl3_patches.py")

pin() {  # tool key: the pinned string, or the script stops
  "$PY" - "$ROOT/tools/pins.json" "$1" "$2" <<'EOF'
import json, sys
value = json.load(open(sys.argv[1]))["tools"].get(sys.argv[2], {})
for key in sys.argv[3].split("."):
    value = value.get(key) if isinstance(value, dict) else None
if not isinstance(value, str) or not value:
    sys.exit(f"tools/pins.json: {sys.argv[2]} pins no {sys.argv[3]}")
print(value)
EOF
}

tools_path() {  # tool key: a pinned path, which must be a plain path under private/s5-tools
  local rel
  rel=$(pin "$1" "$2")
  case "$rel" in private/s5-tools/?*) ;; *) die "$1's $2 ($rel) is not under private/s5-tools" ;; esac
  case "/$rel/" in */../*|*/./*|*//*) die "$1's $2 ($rel) is not a plain path" ;; esac
  echo "$B/${rel#private/s5-tools/}"
}

version() {  # tool: its pinned version, digits and dots only (it names a directory)
  local v
  v=$(pin "$1" version)
  case "$v" in *[!0-9.]*|.*) die "$1's version ($v) is not digits and dots" ;; esac
  echo "$v"
}

# The configuration's prefix (I), its own source and build directories, the pins its final
# check holds, and the pinned files it prints: "tool:field" pairs.
case "$CONFIG" in
  patched)
    I=$B/install; SRCW=$B/src; BW=$B/build
    FINAL=(--only dxvk --only sdl3 --only glslang)
    PRINTED=(dxvk:library sdl3:library glslang:binary dxvk:dxbc_spirv_archive)
    for pair in dxvk:library sdl3:library glslang:binary dxvk:dxbc_spirv_archive; do
      f=$(tools_path "${pair%%:*}" "${pair#*:}")
      case "$f" in "$I"/*) ;; *) die "${pair%%:*}'s ${pair#*:} ($f) is not in the patched prefix $I" ;; esac
    done ;;
  stock)
    I=$(tools_path sdl3-stock prefix); SRCW=$B/src-sdl3-stock; BW=$B/build-sdl3-stock
    [ "$I" != "$B/install" ] || die "sdl3-stock's prefix is the patched configuration's"
    FINAL=(--only sdl3-stock)
    PRINTED=(sdl3-stock:library sdl3-stock:dxvk_library sdl3-stock:glslang_binary sdl3-stock:dxbc_spirv_archive)
    for pair in "${PRINTED[@]}"; do
      f=$(tools_path "${pair%%:*}" "${pair#*:}")
      case "$f" in "$I"/*) ;; *) die "${pair%%:*}'s ${pair#*:} ($f) is not in the stock prefix $I" ;; esac
    done ;;
  system)
    I=$B/install-sdl3-system; SRCW=$B/src-sdl3-system; BW=$B/build-sdl3-system
    FINAL=(); PRINTED=() ;;
esac
CHECK_PINS=("${GATE[@]}" --only dxvk --only sdl3 --only glslang)
# The stock configuration's own entry is held before the build too: an entry carrying a hash of
# its own for a file another pin holds stops here, not after a whole build.
[ "$CONFIG" != stock ] || CHECK_PINS+=(--only sdl3-stock)
echo "== configuration: $CONFIG, prefix $I"
# A failed final check left the install before it here; which of the two to keep is a person's
# decision, so the script never deletes it.
[ ! -e "$I.previous" ] && [ ! -L "$I.previous" ] \
  || die "$I.previous exists: an earlier run's final check failed and left the install from" \
    "before it there. Keep one of $I and $I.previous, remove the other, and run again"

fetch_archive() {  # tool
  local path url sha
  path=$(tools_path "$1" path); url=$(pin "$1" url); sha=$(pin "$1" sha256)
  [ -e "$path" ] && return 0
  [ "$FETCH" = 1 ] || die "missing $path (run with --fetch)"
  mkdir -p "$(dirname "$path")"
  rm -f "$path.part"
  echo "fetching $url"
  curl -fsSL --proto '=https' --retry 3 --max-time 600 -o "$path.part" "$url" \
    || { rm -f "$path.part"; die "the download of $url failed"; }
  echo "$sha  $path.part" | sha256sum -c --quiet - \
    || { rm -f "$path.part"; die "$url is not the pinned $1 (sha256 $sha)"; }
  mv "$path.part" "$path"
}

fetch_checkout() {  # dxvk
  local dx url tag commit
  dx=$(tools_path dxvk path); url=$(pin dxvk url); tag=$(pin dxvk tag); commit=$(pin dxvk commit)
  [ -e "$dx" ] && return 0
  [ "$FETCH" = 1 ] || die "missing $dx (run with --fetch)"
  mkdir -p "$(dirname "$dx")"
  rm -rf "$dx.part"
  echo "fetching $url at $tag"
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "$tag" \
    --no-recurse-submodules "$url" "$dx.part"
  [ "$(git -C "$dx.part" rev-parse HEAD)" = "$commit" ] \
    || { rm -rf "$dx.part"; die "$url at $tag is not the pinned commit $commit"; }
  git -C "$dx.part" submodule --quiet update --init --recursive --depth 1
  mv "$dx.part" "$dx"
}

extract() {  # tool directory base: the archive, freshly extracted to base/directory
  local archive
  archive=$(tools_path "$1" path)
  mkdir -p "$3"
  rm -rf "${3:?}/${2:?}"
  tar -C "$3" -xzf "$archive"
  [ -d "$3/$2" ] || die "$archive did not extract to $2"
}

installed_under() {  # list-file what: every installed path the list names lies under $I
  "$PY" - "$1" "$I" "$2" <<'EOF'
import os, sys
listing, prefix, what = sys.argv[1], os.path.realpath(sys.argv[2]), sys.argv[3]
rows = [l.strip() for l in open(listing) if l.strip() and not l.startswith("#")]
if not rows:
    sys.exit(f"{what}: {listing} lists no installed file")
out = [r for r in rows if not os.path.realpath(r).startswith(prefix + os.sep)]
if out:
    sys.exit(f"{what} installed {len(out)} file(s) outside {prefix}: {out[:5]}")
print(f"{what}: {len(rows)} installed files, all under {prefix}")
EOF
}

fetch_archive glslang
fetch_archive sdl3
fetch_checkout
ALSA_ARCHIVE=$(tools_path sdl3 alsa_headers.path)
[ -f "$ALSA_ARCHIVE" ] || die "$ALSA_ARCHIVE is missing. It is staged by hand, never fetched:" \
  "$(pin sdl3 alsa_headers.url), ratified in" \
  "Documentation/decisions/2026-10-02-S5-alsa-headers-download.md"

echo "== the sources, by the pin gate (check_pins.py --sources-only)"
"${CHECK_PINS[@]}" --sources-only
if [ "$CONFIG" = patched ]; then
  echo "== the SDL3 patches (tools/s5/sdl3_patches.py check)"
  "${PATCHES[@]}" check --root "$ROOT"
fi

echo "== the build tools, by the pin gate"
"${GATE[@]}" --only cmake --only ninja --only meson --only pkg-config --only gcc --only patch
CMAKE=$(pin cmake path); NINJA=$(pin ninja path); MESON=$(pin meson path)
PKG_CONFIG=$(pin pkg-config path); CC=$(pin gcc path); CXX=$(dirname "$CC")/g++; PATCH=$(pin patch path)
for tool in "$CMAKE" "$NINJA" "$MESON" "$PKG_CONFIG" "$CC" "$CXX" "$PATCH" /usr/bin/ar; do
  [ -x "$tool" ] || die "build tool $tool is not an executable"
done
BUILD_ENV=(/usr/bin/env -i "HOME=$HOME" LC_ALL=C.UTF-8 "PATH=$I/bin:/usr/bin:/bin"
           "CC=$CC" "CXX=$CXX" "PKG_CONFIG=$PKG_CONFIG")
# g++ has no pin of its own: it is the gcc pin's C++ compiler, so it reports the same version.
CXX_VERSION=$("${BUILD_ENV[@]}" "$CXX" -dumpfullversion)
[ "$CXX_VERSION" = "$(pin gcc version)" ] \
  || die "$CXX reports version $CXX_VERSION, not the gcc pin's $(pin gcc version)"
echo "== build environment: ${BUILD_ENV[*]:2}"
for tool in "$CC" "$CXX" "$CMAKE" "$NINJA" "$MESON" "$PKG_CONFIG" "$PATCH" /usr/bin/ar; do
  echo "$tool: $("${BUILD_ENV[@]}" "$tool" --version | sed -n 1p)"
done
J=$(( $(nproc) > 8 ? 8 : $(nproc) ))
# SA2_BUILD_JOBS lowers the parallel jobs on a machine short of memory; the bytes built do not
# depend on it.
case "${SA2_BUILD_JOBS:-}" in "") ;; *[!0-9]*|0*) die "SA2_BUILD_JOBS must be a positive integer" ;;
  *) J=$SA2_BUILD_JOBS ;; esac
echo "== $J parallel jobs"

# --sdl3=system: the SDL3 pkg-config finds, held to what the run-time detection needs.
DXVK_PKG_PATH=$I/lib/pkgconfig
if [ "$CONFIG" = system ]; then
  SYS_ENV=("${BUILD_ENV[@]}")
  [ -z "${SDL3_SYSTEM_PKG_CONFIG_PATH:-}" ] || SYS_ENV+=("PKG_CONFIG_PATH=$SDL3_SYSTEM_PKG_CONFIG_PATH")
  SYS_VERSION=$("${SYS_ENV[@]}" "$PKG_CONFIG" --modversion sdl3 2>/dev/null) \
    || die "--sdl3=system: pkg-config finds no sdl3 (install SDL3's development files, or set" \
      "SDL3_SYSTEM_PKG_CONFIG_PATH to the directory holding sdl3.pc)"
  SYS_LIBDIR=$("${SYS_ENV[@]}" "$PKG_CONFIG" --variable=libdir sdl3)
  SYS_LIB=$SYS_LIBDIR/libSDL3.so.0
  [ -e "$SYS_LIB" ] || die "--sdl3=system: no $SYS_LIB (sdl3.pc's libdir)"
  # This project's own SDL3 builds are never a system SDL3: anything under a private/s5-tools
  # (this tree's, or another checkout's: an install, the stock prefix, one kept aside as
  # install.sdl-* or *.previous), a library holding the patch's marks (a patched build from any
  # checkout), or one whose bytes the sdl3 or sdl3-stock pin holds (a stock build from any).
  SYS_REAL=$(realpath -e "$SYS_LIB")
  case "$SYS_REAL" in "$B"/*|*/private/s5-tools/*)
    die "--sdl3=system: $SYS_LIB is $SYS_REAL, under $B or another private/s5-tools: one of this project's own" \
      "SDL3 builds, not a system SDL3" ;; esac
  if grep -qaF -e sa2-recomp -e sa2recomp "$SYS_REAL"; then
    die "--sdl3=system: $SYS_REAL holds the SDL patch's marks (sa2-recomp): one of this project's own SDL3" \
      "builds, not a system SDL3"
  fi
  SYS_SHA=$(sha256sum "$SYS_REAL" | cut -c1-64)
  for p in sdl3 sdl3-stock; do
    [ "$SYS_SHA" != "$(pin "$p" library_sha256)" ] \
      || die "--sdl3=system: $SYS_REAL hashes to the $p pin's library ($SYS_SHA): one of this project's own" \
        "SDL3 builds, not a system SDL3"
  done
  VERSION_RC=0
  "$PY" - "$SYS_VERSION" <<'EOF' || VERSION_RC=$?
import re, sys
m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", sys.argv[1])
sys.exit(3 if m is None else 0 if tuple(map(int, m.groups())) >= (3, 4, 0) else 1)
EOF
  case "$VERSION_RC" in
    0) ;;
    3) die "--sdl3=system: pkg-config reports SDL3's version as '$SYS_VERSION', not three numbers" ;;
    *) die "--sdl3=system: SDL3 $SYS_VERSION is older than 3.4.0, whose SDL_GetGPUDeviceProperties the run-time" \
         "detection reads" ;;
  esac
  SYMS=$(nm -D --defined-only "$(realpath -e "$SYS_LIB")")
  grep -qw SDL_GetGPUDeviceProperties <<< "$SYMS" \
    || die "--sdl3=system: $SYS_LIB does not export SDL_GetGPUDeviceProperties"
  echo "== system SDL3 $SYS_VERSION: $SYS_LIB -> $(realpath -e "$SYS_LIB")" \
    "sha256 $(sha256sum "$(realpath -e "$SYS_LIB")" | cut -c1-64) (not pinned)"
  DXVK_PKG_PATH=${SDL3_SYSTEM_PKG_CONFIG_PATH:-}
fi

prepare_alsa_headers() {
  "${BUILD_ENV[@]}" "$PY" - "$ROOT" "$B" "$ALSA_ARCHIVE" "$ALSA_HEADERS" "$ALSA_RUNTIME" <<'ALSA'
import hashlib, io, json, lzma, shutil, sys, tarfile
from pathlib import Path, PurePosixPath

MAX_TAR_BYTES = 2 * 1024 * 1024
MAX_MEMBERS = 256
MAX_HEADER_BYTES = 128 * 1024
MAX_HEADERS_BYTES = 1024 * 1024

def sha(data):
    return hashlib.sha256(data).hexdigest()

def header_manifest(headers):
    return [{'path': name, 'bytes': len(data), 'sha256': sha(data)}
            for name, data in sorted(headers.items())]

def output_layout(base, destination, receipt_parent):
    if base.resolve(strict=True) != base:
        raise ValueError('ALSA tools base is not its physical directory')
    if destination != base / 'src/alsa-headers' or receipt_parent != base / 'build':
        raise ValueError('ALSA output paths differ from their canonical layout')
    receipt_path = receipt_parent / 'alsa-headers.json'
    if destination.is_symlink() or receipt_parent.is_symlink() or receipt_path.is_symlink():
        raise ValueError('ALSA output leaf, receipt leaf or receipt parent is a symlink')
    if receipt_path.resolve(strict=False) != receipt_path:
        raise ValueError('ALSA receipt leaf does not bind its physical path')
    parents = {'source_parent': destination.parent, 'receipt_parent': receipt_parent}
    for name, parent in parents.items():
        resolved = parent.resolve(strict=False)
        if resolved != parent or not resolved.is_relative_to(base):
            raise ValueError('ALSA ' + name + ' does not bind inside the physical tools base')
    return {'base': str(base), 'receipt_path': str(receipt_path.resolve(strict=False)),
            **{name: str(parent.resolve(strict=False)) for name, parent in parents.items()}}


def unpack_headers(raw, meta):
    if len(raw) != meta['bytes'] or sha(raw) != meta['sha256']:
        raise ValueError('ALSA archive size or SHA-256 differs from its pin')
    if not raw.startswith(b'!<arch>\n'):
        raise ValueError('ALSA archive is not a Debian ar file')
    offset, members = 8, {}
    while offset < len(raw):
        h = raw[offset:offset + 60]
        if len(h) != 60 or h[58:60] != b'`\n':
            raise ValueError('invalid Debian ar header')
        name = h[:16].decode('ascii').strip().removesuffix('/')
        size = int(h[48:58].decode('ascii').strip())
        end = offset + 60 + size
        if size < 0 or end > len(raw) or name in members or len(members) >= 3:
            raise ValueError('invalid or duplicate Debian ar member')
        members[name] = raw[offset + 60:end]
        offset = end + size % 2
    if offset != len(raw) or set(members) != {'debian-binary', 'control.tar.xz', 'data.tar.xz'} or members['debian-binary'] != b'2.0\n':
        raise ValueError('unexpected Debian archive members')
    with lzma.LZMAFile(io.BytesIO(members['data.tar.xz'])) as compressed:
        data = compressed.read(MAX_TAR_BYTES + 1)
    if len(data) > MAX_TAR_BYTES:
        raise ValueError('ALSA data archive exceeds its extraction bound')
    headers, seen, total = {}, set(), 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as tar:
        for count, member in enumerate(tar, 1):
            path = PurePosixPath(member.name)
            if count > MAX_MEMBERS or path.is_absolute() or '..' in path.parts or str(path) in seen:
                raise ValueError('ALSA member path, count or duplicate is invalid')
            seen.add(str(path))
            if path.parts[:2] != ('usr', 'include') or member.isdir():
                continue
            if not member.isfile() or not 0 <= member.size <= MAX_HEADER_BYTES:
                raise ValueError('ALSA header is not a bounded regular file')
            total += member.size
            if total > MAX_HEADERS_BYTES:
                raise ValueError('ALSA headers exceed their extraction bound')
            handle = tar.extractfile(member)
            payload = handle.read(MAX_HEADER_BYTES + 1) if handle else None
            if payload is None or len(payload) != member.size:
                raise ValueError('ALSA header size differs from its member')
            headers[str(path)] = payload
    manifest = header_manifest(headers)
    digest = sha(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode())
    if digest != meta['headers_manifest_sha256']:
        raise ValueError('ALSA header manifest differs from its pin')
    return headers, manifest

root, base, archive, destination, runtime = map(Path, sys.argv[1:])
pins_path = root / 'tools/pins.json'
meta = json.loads(pins_path.read_text())['tools']['sdl3']['alsa_headers']
if not archive.resolve(strict=True).is_relative_to(base) or destination != base / 'src/alsa-headers':
    raise ValueError('ALSA preparation escaped private/s5-tools')
with archive.open('rb') as handle:
    raw = handle.read(meta['bytes'] + 1)
headers, manifest = unpack_headers(raw, meta)
if str(runtime.resolve(strict=True)) != meta['runtime_realpath'] or sha(runtime.read_bytes()) != meta['runtime_sha256']:
    raise ValueError('ALSA runtime differs from its pinned installed file')
receipt_parent = base / 'build'
# These checks bind current path resolution; they make no concurrent-race guarantee.
layout = output_layout(base, destination, receipt_parent)
if destination.exists():
    shutil.rmtree(destination)
destination.mkdir(parents=True)
for name, payload in sorted(headers.items()):
    target = destination / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
actual = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file()}
if actual != headers:
    raise ValueError('fresh ALSA extraction differs from verified header bytes')
if output_layout(base, destination, receipt_parent) != layout:
    raise ValueError('ALSA output directory binding changed during extraction')
receipt = {'scope': 'Fresh verified ALSA headers for canonical dynamic SDL audio; no device or audio run.',
           'directory_boundary': layout,
           'pins_sha256': sha(pins_path.read_bytes()),
           'builder_sha256': sha((root / 'tools/s5/build_host_deps.sh').read_bytes()),
           'archive': {'path': str(archive), 'bytes': len(raw), 'sha256': sha(raw)},
           'package': {k: meta[k] for k in ('package', 'version', 'architecture', 'depends')},
           'headers': manifest, 'headers_manifest_sha256': meta['headers_manifest_sha256'],
           'include': str(destination / 'usr/include'),
           'runtime': {'path': str(runtime), 'realpath': str(runtime.resolve()), 'sha256': sha(runtime.read_bytes())},
           'ok': True}
receipt_parent.mkdir(parents=True, exist_ok=True)
if output_layout(base, destination, receipt_parent) != layout:
    raise ValueError('ALSA receipt directory binding changed before its write')
receipt_path = receipt_parent / 'alsa-headers.json'
# Refuse the leaf again immediately before open; this is no concurrent-race claim.
if receipt_path.is_symlink() or str(receipt_path.resolve(strict=False)) != layout['receipt_path']:
    raise ValueError('ALSA receipt leaf binding changed before its write')
receipt_path.write_text(json.dumps(receipt, indent=2) + '\n')
print('ALSA_HEADERS_OK ' + str(base / 'build/alsa-headers.json'))
ALSA
}
# The ALSA headers stay in their canonical place (src/alsa-headers), whichever configuration:
# every run extracts and checks the same pinned bytes there.
ALSA_HEADERS=$B/src/alsa-headers
ALSA_RUNTIME=$(pin sdl3 alsa_headers.runtime_path)
[ "$CONFIG" = system ] || prepare_alsa_headers

# SDL3's source first, before the previous install moves aside: the pinned commit, and for the
# patched configuration the patches applied strictly, so a patch that does not apply stops the
# build with the install untouched.
SV=$(version sdl3)
VENDOR=()
if [ "$CONFIG" != system ]; then
  echo "== SDL3 $SV source ($CONFIG)"
  extract sdl3 "SDL3-$SV" "$SRCW"
  "${PATCHES[@]}" source --root "$ROOT" --tree "$SRCW/SDL3-$SV"
  if [ "$CONFIG" = patched ]; then
    "${PATCHES[@]}" apply --root "$ROOT" --tree "$SRCW/SDL3-$SV" \
      || die "tools/s5/sdl3_patches.py refused to apply the SDL3 patches under patches/sdl3 to the pinned SDL" \
        "$SV (commit $(pin sdl3 commit)): its reason is the message above (a hunk that does not apply" \
        "strictly, or a patch, patch set or GNU patch that is not the pinned one). Nothing was built and the" \
        "install is untouched"
    VENDOR=(-DSDL_VENDOR_INFO="$(pin sdl3 vendor_info)")
  fi
fi

# The previous install stays aside until this build has installed everything.
BUILT=0
put_back() {  # two renames first, so the install is missing for as short a time as possible
  if [ "$BUILT" = 0 ] && [ -e "$I.previous" ]; then
    rm -rf "$I.unfinished"
    [ -e "$I" ] && mv "$I" "$I.unfinished"
    mv "$I.previous" "$I"
    rm -rf "$I.unfinished"
    echo "build_host_deps.sh: the build did not finish; the previous install is back" >&2
  fi
}
[ -e "$I" ] && mv "$I" "$I.previous"
trap put_back EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir -p "$I" "$SRCW" "$BW"

GV=$(version glslang)
echo "== glslang $GV"
extract glslang "glslang-$GV" "$SRCW"
rm -rf "$BW/glslang"
"${BUILD_ENV[@]}" "$CMAKE" --fresh -S "$SRCW/glslang-$GV" -B "$BW/glslang" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$I" \
  -DENABLE_OPT=OFF -DGLSLANG_TESTS=OFF -DBUILD_SHARED_LIBS=OFF -DGLSLANG_ENABLE_INSTALL=ON \
  > "$BW/glslang-configure.log"
"${BUILD_ENV[@]}" "$NINJA" -C "$BW/glslang" -j"$J" install > "$BW/glslang-build.log"
installed_under "$BW/glslang/install_manifest.txt" glslang
"$I/bin/glslang" --version | sed -n 1p

if [ "$CONFIG" != system ]; then
  echo "== SDL3 $SV ($CONFIG)"
  rm -rf "$BW/sdl3"
  "${BUILD_ENV[@]}" "$CMAKE" --fresh -S "$SRCW/SDL3-$SV" -B "$BW/sdl3" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$NINJA" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$I" \
    -DSDL_TESTS=OFF -DSDL_EXAMPLES=OFF -DSDL_STATIC=OFF -DSDL_SHARED=ON \
    -DSDL_X11_XSCRNSAVER=OFF -DSDL_X11_XTEST=OFF \
    -DSDL_AUDIO=ON -DSDL_ALSA=ON -DSDL_ALSA_SHARED=ON \
    -DSDL_PULSEAUDIO=OFF -DSDL_PIPEWIRE=OFF -DSDL_JACK=OFF -DSDL_SNDIO=OFF \
    -DALSA_INCLUDE_DIR="$ALSA_HEADERS/usr/include" -DALSA_LIBRARY="$ALSA_RUNTIME" \
    -DASOUND_LIB="$ALSA_RUNTIME" "${VENDOR[@]}" > "$BW/sdl3-configure.log"
  WANT_VENDOR=""
  [ "$CONFIG" = patched ] && WANT_VENDOR=$(pin sdl3 vendor_info)
  "${BUILD_ENV[@]}" "$PY" - "$BW/sdl3" "$ALSA_HEADERS/usr/include" "$ALSA_RUNTIME" "$WANT_VENDOR" <<'ALSA_CONFIG'
import re, sys
from pathlib import Path
build, include, runtime = map(Path, sys.argv[1:4])
vendor = sys.argv[4]
cache = (build / 'CMakeCache.txt').read_text().splitlines()
expected = ['SDL_AUDIO:BOOL=ON', 'SDL_ALSA:BOOL=ON', 'SDL_ALSA_SHARED:BOOL=ON',
            'ALSA_INCLUDE_DIR:PATH=' + str(include), 'ALSA_LIBRARY:FILEPATH=' + str(runtime),
            'ASOUND_LIB:FILEPATH=' + str(runtime)]
if not all(cache.count(row) == 1 for row in expected):
    raise ValueError('SDL configuration did not retain the pinned dynamic ALSA settings')
config = (build / 'include-config-release/build_config/SDL_build_config.h').read_text().splitlines()
for row in ('#define SDL_AUDIO_DRIVER_ALSA 1', '#define SDL_AUDIO_DRIVER_ALSA_DYNAMIC "libasound.so.2"'):
    if config.count(row) != 1:
        raise ValueError('SDL configuration did not enable dynamic libasound.so.2: no ' + repr(row))
drivers = sorted(row.split()[1] for row in config
                 if row.startswith('#define SDL_AUDIO_DRIVER_') and len(row.split()) >= 3)
if drivers != ['SDL_AUDIO_DRIVER_ALSA', 'SDL_AUDIO_DRIVER_ALSA_DYNAMIC',
               'SDL_AUDIO_DRIVER_DISK', 'SDL_AUDIO_DRIVER_DUMMY']:
    raise ValueError('SDL configured sound drivers other than ALSA, disk and dummy: ' + repr(drivers))
# The altered-source mark (SDL's zlib licence, clause 2): the patched build's revision header
# defines SDL_VENDOR_INFO as the pin, the stock build's defines none.
revision = (build / 'include-revision/SDL3/SDL_revision.h').read_text()
found = re.findall(r'^#define SDL_VENDOR_INFO "([^"\n]*)"\s*$', revision, re.M)
if found != ([vendor] if vendor else []):
    raise ValueError(f'SDL_revision.h defines SDL_VENDOR_INFO {found}, wanted {[vendor] if vendor else "none"}')
print('SDL_VENDOR_INFO ' + (repr(vendor) if vendor else 'none (stock)'))
ALSA_CONFIG
  "${BUILD_ENV[@]}" "$NINJA" -C "$BW/sdl3" -j"$J" install > "$BW/sdl3-build.log"
  installed_under "$BW/sdl3/install_manifest.txt" SDL3
  echo "wanted by SDL3 and configured off (the backends and libraries for want of their development" \
    "packages; SDL_INSTALL, SDL_DEPS_SHARED and the *_LINK_SHARED options are not packages):"
  grep -E "^--   SDL_[A-Z0-9_]+ +\(Wanted: ON\): OFF" "$BW/sdl3-configure.log" || true
fi

DX=$(tools_path dxvk path)
echo "== DXVK Native $(pin dxvk version) (d3d9 only, SDL3 window system)"
rm -rf "$BW/dxvk"
DXVK_ENV=("${BUILD_ENV[@]}")
[ -z "$DXVK_PKG_PATH" ] || DXVK_ENV+=("PKG_CONFIG_PATH=$DXVK_PKG_PATH")
"${DXVK_ENV[@]}" "$MESON" setup "$BW/dxvk" "$DX" \
  --buildtype release --prefix "$I" \
  -Denable_d3d9=true -Denable_d3d8=false -Denable_d3d10=false -Denable_d3d11=false \
  -Denable_dxgi=false -Dnative_sdl3=enabled -Dnative_sdl2=disabled -Dnative_glfw=disabled \
  > "$BW/dxvk-configure.log"
grep -E "^((C|C\+\+) compiler for the host machine|Program glslang|Run-time dependency (SDL3|sdl3))" \
  "$BW/dxvk-configure.log" || true
"${DXVK_ENV[@]}" "$NINJA" -C "$BW/dxvk" -j"$J" install > "$BW/dxvk-build.log"
installed_under "$BW/dxvk/meson-logs/install-log.txt" DXVK

echo "== dxbc-spirv: DXVK's own objects, from its thin archive into a real one"
THIN=$BW/dxvk/subprojects/dxbc-spirv/libdxbc_spv.a
ARCHIVE=$I/lib/libdxbc_spv.a
[ "$(head -c 8 "$THIN")" = '!<thin>' ] || die "$THIN is not DXVK's thin archive"
# (ar names a thin archive's members by the path it was given for the archive: relative here.)
mapfile -t MEMBERS < <(cd "$(dirname "$THIN")" && /usr/bin/ar t "$(basename "$THIN")")
[ "${#MEMBERS[@]}" -gt 0 ] || die "$THIN has no members"
DUP=$(printf '%s\n' "${MEMBERS[@]##*/}" | sort | uniq -d)
[ -z "$DUP" ] || die "$THIN holds members with one name twice: $DUP (ar would keep one)"
OBJS=()
for m in "${MEMBERS[@]}"; do
  obj=$(dirname "$THIN")/$m
  [ -f "$obj" ] || die "$THIN's member $m is not on disk"
  OBJS+=("$obj")
done
[ ! -e "$ARCHIVE" ] || die "$ARCHIVE exists in the fresh install: something else wrote it"
(cd "$(dirname "$THIN")" && /usr/bin/ar rcsD "$ARCHIVE" "${MEMBERS[@]}")
[ "$(head -c 8 "$ARCHIVE")" = '!<arch>' ] || die "$ARCHIVE is not a regular archive"
[ "$(/usr/bin/ar t "$ARCHIVE" | tr '\n' ' ')" = "$(printf '%s ' "${MEMBERS[@]##*/}")" ] \
  || die "$ARCHIVE does not hold $THIN's members in its order"
for obj in "${OBJS[@]}"; do echo "  $(sha256sum "$obj" | cut -c1-64) $(basename "$obj")"; done
echo "dxbc-spirv archive: ${#MEMBERS[@]} members, $(sha256sum "$ARCHIVE" | cut -c1-64) $ARCHIVE"
BUILT=1

if [ "$CONFIG" = system ]; then
  "$PY" - "$BW/sdl3-system.json" "$I" "$SYS_VERSION" "$SYS_LIB" <<'EOF'
import hashlib, json, os, sys
out, prefix, version, lib = sys.argv[1:]
def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()
files = {rel: os.path.join(prefix, rel) for rel in ("lib/x86_64-linux-gnu/libdxvk_d3d9.so.0", "bin/glslang",
                                                     "lib/libdxbc_spv.a")}
doc = {"schema": "sa2-s5-sdl3-system/v1", "configuration": "system", "pinned": False,
       "note": "A system SDL3 is not pinned, so no gate holds this prefix; the gates use the stock configuration.",
       "sdl3": {"version": version, "library": lib, "reaches": os.path.realpath(lib), "sha256": sha(os.path.realpath(lib))},
       "files": {rel: {"reaches": os.path.realpath(p), "sha256": sha(os.path.realpath(p))} for rel, p in files.items()}}
json.dump(doc, open(out, "w"), indent=1)
print("system configuration recorded in " + out)
EOF
  rm -rf "$I.previous"
  echo "BUILD_OK (system SDL3, not pinned)"
  exit 0
fi

echo "== sha256 of the built files the pins name (a library's soname link followed)"
for pair in "${PRINTED[@]}"; do
  f=$(tools_path "${pair%%:*}" "${pair#*:}")
  [ -f "$f" ] || die "$f was not built, or does not reach a regular file"
  reached=$(realpath -e "$f")
  shown=$(pin "${pair%%:*}" "${pair#*:}")
  [ -L "$f" ] && shown="$shown -> $(basename "$reached")"
  # (the stock prefix's DXVK, glslang and archive are held to the dxvk and glslang pins)
  case "$pair" in
    sdl3-stock:dxvk_library) label="dxvk library_sha256 (in the stock prefix)" ;;
    sdl3-stock:glslang_binary) label="glslang binary_sha256 (in the stock prefix)" ;;
    sdl3-stock:dxbc_spirv_archive) label="dxvk dxbc_spirv_archive_sha256 (in the stock prefix)" ;;
    *) label="${pair%%:*} ${pair#*:}_sha256" ;;
  esac
  echo "$label $(sha256sum "$reached" | cut -d' ' -f1) $shown"
done

echo "== what was built, by the pin gate"
if "${GATE[@]}" "${FINAL[@]}"; then
  rm -rf "$I.previous"
  echo "BUILD_OK ($CONFIG)"
else
  die "the install does not match tools/pins.json. If the change is meant, move the pins to the" \
    "sha256s above in the commit that moves the install (AGENTS.md rule 8); the previous" \
    "install is at $I.previous"
fi
