#!/bin/bash
# build.sh: builds the DXVK player, its synthetic-stream writer, the unit tests
# (texture codec; stream reader, header JSON and SHA-256; the touch overlay), the sound renderer
# (mixer.cpp with no device: a stream's sound as a WAV) and r3's DXVK smoke test
# with the system g++ 14 against the SDL3 and DXVK Native that
# private/s5-tools/build.sh installed. Nothing is written inside the repository:
# binaries go to $OUT, with the records of what they were built from
# (build_record.py): sa2_player.json, sa2_player's exact record (every file its
# compiles read, from the compiler's depfiles, and the files that made its
# expectations), and player_tools.json, the other binaries' group record. Nothing
# under host/d3d9gpu is read: the second host's player is host/d3d9gpu/build.sh's.
#
#   bash host/player/build.sh              build all of them into $OUT
#   OUT=/some/dir bash host/player/build.sh
#
# The repository is this checkout (SA2_REPO names another), and OUT defaults to
# private/generated/s5/play/bin inside it; a default that resolves outside the checkout (a
# clone whose private/ is a link to the main tree's) is refused: set OUT.
#
# SA2_S5_INSTALL names the install to build against: private/s5-tools/install (the patched SDL3,
# the default) or the stock configuration's prefix (tools/s5/build_host_deps.sh --sdl3=stock);
# generate_expectations.py takes the pins of the one it is. The player loads libSDL3.so.0 from
# that prefix by DT_RPATH, which the loader searches before LD_LIBRARY_PATH, so no other SDL3 on
# the loader's path is loaded first (the user's second rulings of 2026-10-04: the patched SDL
# never replaces or shadows a system SDL3, nor a system one it).
#
# The stream format header is host/sa2_stream.h, the recorder's
# (host/sa2_present_rec.c); SA2_STREAM_H_DIR names another directory holding it,
# for a checkout that does not have it yet.
#
# The player's host tweaks (tweaks.h): tweaks.cpp, the reader, with runtime/sa2_tweaks.h, the list
# it reads, and each host/player/tweaks/<name>.cpp by glob (runtime/sa2_tweaks.h, "Add a host
# tweak"), each object named tweak_<name>.o.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
HOST=$(dirname "$HERE")
# The repository is this script's own checkout unless SA2_REPO names another, never a fixed
# path: a clone's build reads its own pins and sources. The default OUT must stay inside it: in a
# clone or worktree whose private/ (or private/generated) is a link to the main tree's,
# $REPO/private/generated/s5/play/bin is the main tree's shared player builds, which only the main
# tree's own build writes; OUT names any other place.
REPO=${SA2_REPO:-$(cd "$HOST/.." && pwd)}
[ -f "$REPO/tools/pins.json" ] || { echo "no repository at $REPO (no tools/pins.json; set SA2_REPO)"; exit 2; }
I=${SA2_S5_INSTALL:-$REPO/private/s5-tools/install}
if [ -z "${OUT:-}" ]; then
  OUT=$REPO/private/generated/s5/play/bin
  case "$(realpath -m "$OUT")/" in "$(realpath -m "$REPO")"/*) ;; *)
    echo "the default OUT $OUT resolves to $(realpath -m "$OUT"), outside this checkout $(realpath -m "$REPO")" \
      "(a linked private/): set OUT"; exit 2 ;; esac
fi
STREAM_H_DIR=${SA2_STREAM_H_DIR:-$HOST}
PY=$REPO/.venv/bin/python
[ -f "$STREAM_H_DIR/sa2_stream.h" ] || { echo "no sa2_stream.h in $STREAM_H_DIR (set SA2_STREAM_H_DIR)"; exit 2; }
[ -f "$I/lib/libSDL3.so.0" ] || { echo "no SDL3 under $I (run private/s5-tools/build.sh)"; exit 2; }
[ -d "$I/include/dxvk" ] || { echo "no DXVK headers under $I/include/dxvk"; exit 2; }
# The install's physical path: the one the players' DT_RPATH embeds, whatever the working
# directory or a link on the way (a relative SA2_S5_INSTALL would embed a path relative to it).
I=$(cd "$I" && pwd -P)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# What the build reads, hashed before it compiles anything and again in its records at the
# end (build_record.py): a source that changes while the build runs refuses the record.
SOURCES=("$HERE" "$STREAM_H_DIR/sa2_stream.h" "$HOST/dxvk_smoke.cpp" "$REPO/tools/pins.json"
         "$REPO/runtime/sa2_tweaks.h")
"$PY" "$HERE/build_record.py" --start --out "$OUT/player_tools.json" --tree "$HOST/.." --sources "${SOURCES[@]}"
"$PY" "$HERE/build_record.py" --start --out "$OUT/sa2_player.json" --tree "$HOST/.." --sources "${SOURCES[@]}"
"$PY" "$HERE/generate_expectations.py" --host dxvk \
  --repo "$REPO" --install "$I" --config "$HERE/dxvk.conf" --out "$OUT/expected"
CXX=${CXX:-g++}
FLAGS=(-std=c++17 -O2 -g -Wall -Wextra -Wno-unused-parameter -I"$OUT/expected")
DX=(-I"$I/include/dxvk" -I"$I/include")
SDL=(-L"$I/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" -lSDL3)

echo "== test_texcodec"
$CXX "${FLAGS[@]}" -o "$OUT/test_texcodec" "$HERE/test_texcodec.cpp" "$HERE/texcodec.cpp" -lpng

echo "== test_touch_overlay"
# The touch overlay alone (touch_overlay.h): its pad from finger events, and what it draws on a
# Vulkan SDL_gpu device with no window, with its cost per frame.
$CXX "${FLAGS[@]}" -I"$I/include" -o "$OUT/test_touch_overlay" "$HERE/test_touch_overlay.cpp" \
  "$HERE/touch_overlay.cpp" "$HERE/texcodec.cpp" "${SDL[@]}" -lpng

echo "== test_stream"
$CXX "${FLAGS[@]}" -I"$STREAM_H_DIR" -o "$OUT/test_stream" "$HERE/test_stream.cpp" "$HERE/stream.cpp" \
  "$HERE/sha256.cpp" -lzstd

echo "== sa2_synth_stream"
$CXX "${FLAGS[@]}" -I"$STREAM_H_DIR" -o "$OUT/sa2_synth_stream" "$HERE/synth_stream.cpp" \
  "$HERE/texcodec.cpp" -lpng

echo "== sa2_audio_render"
$CXX "${FLAGS[@]}" -I"$STREAM_H_DIR" -DSA2_MIXER_MAIN -o "$OUT/sa2_audio_render" "$HERE/mixer.cpp" \
  "$HERE/stream.cpp" "$HERE/sha256.cpp" -lzstd

echo "== sa2_player"
# Each translation unit with its depfile (-MMD), which sa2_player.json is made from; the
# flags and the link order are the one-command build's.
POBJ=$OUT/obj/sa2_player
mkdir -p "$POBJ"
find "$POBJ" -maxdepth 1 \( -type f -o -type l \) \( -name '*.o' -o -name '*.d' \) -delete
OBJS=()
for tu in player input mixer stream sha256 texcodec touch_overlay tweaks; do
  $CXX "${FLAGS[@]}" -I"$STREAM_H_DIR" "${DX[@]}" -MMD -MF "$POBJ/$tu.d" -c "$HERE/$tu.cpp" -o "$POBJ/$tu.o"
  OBJS+=("$POBJ/$tu.o")
done
for src in "$HERE"/tweaks/*.cpp; do
  [ -e "$src" ] || continue
  tu=tweak_$(basename "$src" .cpp)
  $CXX "${FLAGS[@]}" -I"$STREAM_H_DIR" "${DX[@]}" -MMD -MF "$POBJ/$tu.d" -c "$src" -o "$POBJ/$tu.o"
  OBJS+=("$POBJ/$tu.o")
done
$CXX -o "$OUT/sa2_player" "${OBJS[@]}" "${SDL[@]}" -lpng -lzstd -ldl

echo "== dxvk_smoke"
$CXX -std=c++17 -O1 -g -Wall -o "$OUT/dxvk_smoke" "$HOST/dxvk_smoke.cpp" "${DX[@]}" "${SDL[@]}" -ldl

# The DXVK player carries nothing of the second host: no d9g code, no Direct3DCreate9 of its
# own, none of its names, and an expectations contract of DXVK's five keys. (The symbol
# tables are read whole before they are searched: a pipe into grep -q fails under pipefail.
# The names are searched for in the binary with the paths it embeds of where it was built
# taken out (the checkout, OUT, the install: -g and assert() hold them, as prefixes of the
# paths under them; and the working directory, -g's compile directory, as a whole string
# only, so that a build run from inside the checkout takes nothing out of the paths under
# it), so a checkout under a directory named for the second host is not a defect.)
# SDL3 from the install alone: DT_RPATH, no DT_RUNPATH (which LD_LIBRARY_PATH would override).
for bin in sa2_player dxvk_smoke; do
  DYN=$(readelf -d "$OUT/$bin")
  if ! grep -qF "Library rpath: [$I/lib]" <<< "$DYN" || grep -q "(RUNPATH)" <<< "$DYN"; then
    echo "$bin does not load SDL3 from $I/lib by DT_RPATH alone"; exit 2
  fi
done
SYMS=$(nm -C "$OUT/sa2_player")
DEFINED=$(nm --defined-only "$OUT/sa2_player")
if grep -q 'd9g::' <<< "$SYMS"; then echo "sa2_player links d9g code"; exit 2; fi
if grep -qw Direct3DCreate9 <<< "$DEFINED"; then echo "sa2_player defines Direct3DCreate9"; exit 2; fi
"$PY" - "$OUT/sa2_player" "$PWD" "$HOST/.." "$STREAM_H_DIR" "$OUT" "$I" <<'EOF_NAMES'
import os, re, sys
data = open(sys.argv[1], "rb").read()
for cwd in {os.path.abspath(sys.argv[2]), os.path.realpath(sys.argv[2])}:
    data = re.sub(rb"(?<=\x00)" + re.escape(cwd.encode()) + rb"(?=\x00)", b"", data)
for path in sorted({q for p in sys.argv[3:] for q in (os.path.abspath(p), os.path.realpath(p))}, key=len, reverse=True):
    data = data.replace(path.encode(), b"")
held = [w for w in ("d3d9gpu", "sdlgpu", "SA2_D3D9GPU") if w.encode() in data]
if held:
    print(f"sa2_player holds {held}")
    sys.exit(2)
EOF_NAMES
"$PY" -c '
import json, sys
c = list(json.load(open(sys.argv[1]))["contract"])
if c != ["sdl_sha256", "dxvk_sha256", "dxvk_config_sha256", "sdl_version", "sdl_revision"]:
    print(f"the DXVK contract is {c}")
    sys.exit(2)' "$OUT/expected/expectations.json"

# The build's identity beside the sources it was built from (build_record.py): what
# tools/s5/compare_hosts.py holds to the tree it pins before it runs this build.
"$PY" "$HERE/build_record.py" --out "$OUT/sa2_player.json" --tree "$HOST/.." --bin-dir "$OUT" \
  --binaries sa2_player --deps "$POBJ"/*.d --compiler "$CXX" \
  --sources "$HERE/build.sh" "$HERE/generate_expectations.py" "$HERE/dxvk.conf" "$REPO/tools/pins.json" \
  --generated "$OUT/expected"
"$PY" "$HERE/build_record.py" --out "$OUT/player_tools.json" --tree "$HOST/.." --bin-dir "$OUT" \
  --binaries test_texcodec test_touch_overlay test_stream sa2_synth_stream sa2_audio_render dxvk_smoke \
  --sources "${SOURCES[@]}" --generated "$OUT/expected"

echo "stream header: $STREAM_H_DIR/sa2_stream.h $(sha256sum "$STREAM_H_DIR/sa2_stream.h" | cut -c1-16)"
ls -l "$OUT"
echo "BUILD_OK"
