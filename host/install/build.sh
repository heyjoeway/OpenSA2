#!/usr/bin/env bash
# host/install/build.sh: build sa2-install, the installer's command-line front end.
#
# Subtitle: `host/install/build.sh` generates the hash table (tools/s7/install_table.py, into
# private/generated/s7/install/) and builds private/generated/s7/install/sa2-install with g++.
#
#   host/install/build.sh [--cxx g++|zig] [--target TRIPLE] [--out FILE] [--table FILE.cpp]
#                         [--src DIR] [--include DIR] [--extra FILE.cpp]
#
#   --cxx zig        zig c++ (clang 20) from tools/env.sh; with --target x86_64-windows-gnu it
#                    builds sa2-install.exe on sa2_platform_win32.cpp.
#   --table FILE     a table source made elsewhere (the tests' synthetic ones); otherwise the
#                    real table is regenerated from Documentation/receipts/install-hashes-v1.json.
#   --src DIR        build another copy of host/install (a snapshot); host/player/sha256.cpp is
#                    taken from DIR/../player.
#   --include, --extra   one more include directory or source file (repeatable).
#
# Every .cpp in the source directory is compiled except the other host's platform file and test
# drivers (test_*.cpp, *_test.cpp, *_probe.cpp, which carry their own main). The ZIP route's
# sa2_zip.h and its sources are host/install's too (the ZIP unit's files).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CXX_KIND=g++
TARGET=""
OUT=""
TABLE=""
SRC="$HERE"
INCLUDES=()
EXTRAS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --cxx) CXX_KIND="$2"; shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --table) TABLE="$2"; shift 2 ;;
        --src) SRC="$(cd "$2" && pwd)"; shift 2 ;;
        --include) INCLUDES+=("-I$2"); shift 2 ;;
        --extra) EXTRAS+=("$2"); shift 2 ;;
        *) echo "build.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done

if [ -z "$TABLE" ]; then
    "$ROOT/.venv/bin/python" "$ROOT/tools/s7/install_table.py" >/dev/null
    TABLE="$ROOT/private/generated/s7/install/sa2_install_table.cpp"
fi
[ -f "$TABLE" ] || { echo "build.sh: no table source at $TABLE" >&2; exit 1; }

WINDOWS=0
case "$TARGET" in *windows*) WINDOWS=1 ;; esac
if [ -z "$OUT" ]; then
    OUT="$ROOT/private/generated/s7/install/sa2-install"
    [ "$WINDOWS" = 1 ] && OUT="$OUT.exe"
fi
mkdir -p "$(dirname "$OUT")"

SOURCES=()
for f in "$SRC"/*.cpp; do
    case "$(basename "$f")" in
        test_*.cpp|*_test.cpp|*_probe.cpp) ;;
        sa2_platform_win32.cpp) [ "$WINDOWS" = 1 ] && SOURCES+=("$f") ;;
        sa2_platform_posix.cpp) [ "$WINDOWS" = 0 ] && SOURCES+=("$f") ;;
        *) SOURCES+=("$f") ;;
    esac
done
SOURCES+=("$SRC/../player/sha256.cpp" "$TABLE")
[ ${#EXTRAS[@]} -gt 0 ] && SOURCES+=("${EXTRAS[@]}")

FLAGS=(-std=c++17 -O2 -Wall -Wextra "-I$SRC")
[ ${#INCLUDES[@]} -gt 0 ] && FLAGS+=("${INCLUDES[@]}")
if [ "$CXX_KIND" = zig ]; then
    # shellcheck disable=SC1091
    command -v zig >/dev/null || source "$ROOT/tools/env.sh" >/dev/null
    CXX=(zig c++)
    [ -n "$TARGET" ] && CXX+=(-target "$TARGET")
else
    CXX=("$CXX_KIND")
fi
if [ "$WINDOWS" = 1 ]; then
    FLAGS+=(-static)
    # shell32: CommandLineToArgvW, for the UTF-8 command line; advapi32: the registry (Steam and
    # Sega keys); both sa2_platform_win32.cpp.
    "${CXX[@]}" "${FLAGS[@]}" "${SOURCES[@]}" -lshell32 -ladvapi32 -o "$OUT"
else
    "${CXX[@]}" "${FLAGS[@]}" -pthread "${SOURCES[@]}" -o "$OUT"
fi
echo "built $OUT"
