#!/bin/bash
# build.sh: builds the second host's player and its tools (R2a, private/generated/s5/r2a/PLAN.md;
# R2b, private/generated/s5/r2b/PLAN.md) with the system g++ against the SDL3 and DXVK Native
# headers that private/s5-tools/build.sh installed, into $OUT. Nothing is written inside the
# repository. It is independent of host/player/build.sh (the DXVK player's), which reads
# nothing here; a comparison of the two hosts (tools/s5/compare_hosts.py) needs both in one OUT.
#
#   bash host/d3d9gpu/build.sh
#   OUT=/some/dir bash host/d3d9gpu/build.sh
#
# The repository is this checkout (SA2_REPO names another), and OUT defaults to
# private/generated/s5/play/bin inside it; a default that resolves outside the checkout (a
# clone whose private/ is a link to the main tree's) is refused: set OUT.
#
# Outputs:
#   sa2_player_gpu          the second host's player: host/player/player.cpp built with
#                           SA2_PLAYER_GPU=1 and libsa2_d3d9gpu.a linked in, so its renderer is
#                           its own file (the G5 host contract's "the GPU player itself"); it
#                           pins and verifies no DXVK file; --clear-matrix runs the clear matrix
#                           through its own Direct3DCreate9
#   sa2_player_gpu.json     its exact record: every file its compiles read (the compiler's
#                           depfiles), the generators and pins (host/player/build_record.py)
#   libsa2_d3d9gpu.a        the device on SDL_gpu (d9g's translation units), linked into it
#   shader/                 the shader translator (R1, shader/build_translator.sh): libd9g_shader.a
#                           (DXVK's analysis and lowering pass, compiled with DXVK's flags, and host 2's
#                           SDL half), linked into sa2_player_gpu and test_d3d9gpu with the pinned
#                           dxbc-spirv archive (tools/pins.json dxbc_spirv_archive); d3d9gpu_shaderc,
#                           the translator from the command line and its GPU load check
#                           (host/d3d9gpu/test_shaders.sh)
#   expected-gpu/           its expectations contract (host/player/generate_expectations.py
#                           --host sdlgpu: SDL's file, version and revision; nothing of DXVK's)
#   d3d9gpu_tools.json      the group record of the four binaries below and their sources
#   test_d3d9gpu            the device's unit test on the CPU model (no GPU)
#   sa2_frame_census        tools/s5/compare_hosts.py's per-frame census of a stream
#   d3d9gpu_clear_matrix    the clear matrix through a d3d9 library given by path: DXVK's leg (GPU)
#   d3d9gpu_state_check     the state check (R2b, test/state_check.cpp): the Vulkan state SDL
#                           builds from d9g's descriptions against the state DXVK builds, both
#                           computed by the libraries' own code (no GPU)
#   d3d9gpu-gen/            gen_com.py's COM stubs and slot table (generated)
#   d3d9gpu-gen/refs/       test/extract_refs.py's spans of the pinned DXVK checkout and SDL
#                           tarball, their manifest, the tarball's headers, and the check's
#                           DXVK objects (generated; nothing third-party is tracked)
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
PY=${PYTHON:-$REPO/.venv/bin/python}
[ -f "$I/lib/libSDL3.so.0" ] || { echo "no SDL3 under $I (run private/s5-tools/build.sh)"; exit 2; }
[ -f "$I/include/dxvk/d3d9.h" ] || { echo "no DXVK Native headers under $I/include/dxvk"; exit 2; }
# The install's physical path: the one the players' DT_RPATH embeds, whatever the working
# directory or a link on the way (a relative SA2_S5_INSTALL would embed a path relative to it).
I=$(cd "$I" && pwd -P)
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
# What the build reads, hashed before it compiles anything and again in its records at the
# end (host/player/build_record.py): a source that changes while the build runs refuses it.
P=$HOST/player
SOURCES=("$HERE" "$P/sha256.cpp" "$P/sha256.h" "$P/stream.cpp" "$P/stream.h" "$P/texcodec.cpp" "$P/texcodec.h"
         "$STREAM_H_DIR/sa2_stream.h")
"$PY" "$P/build_record.py" --start --out "$OUT/d3d9gpu_tools.json" --tree "$HOST/.." --sources "${SOURCES[@]}"
# The pinned dxbc-spirv archive the translator links (R1 U1): tools/pins.json, a source of the
# player's record, holds its sha256, and shader/build_translator.sh refuses any other.
DXBC_ARCHIVE=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["tools"]["dxvk"]["dxbc_spirv_archive"])' \
  "$REPO/tools/pins.json")
DXBC_ARCHIVE=$REPO/$DXBC_ARCHIVE
# The player's start set: everything its compiles can read in the tree (the record keeps
# only what they did read).
PLAYER_SOURCES=("$HERE" "$P" "$STREAM_H_DIR/sa2_stream.h" "$REPO/tools/pins.json")
"$PY" "$P/build_record.py" --start --out "$OUT/sa2_player_gpu.json" --tree "$HOST/.." --sources "${PLAYER_SOURCES[@]}"
"$PY" "$P/generate_expectations.py" --host sdlgpu --repo "$REPO" --install "$I" --out "$OUT/expected-gpu"
GEN=$OUT/d3d9gpu-gen
"$PY" "$HERE/gen_com.py" --header "$I/include/dxvk/d3d9.h" --out "$GEN"
# The pinned DXVK and SDL sources the state check (and the library's one Vulkan header use)
# read, held to tools/pins.json before anything is read (test/extract_refs.py refuses otherwise).
REFS=$GEN/refs
DXVK=${SA2_DXVK_SRC:-$REPO/private/s5-tools/src/dxvk-3.1.1}
EXTRACT=(env SA2_REPO="$REPO" "$PY" "$HERE/test/extract_refs.py")
"${EXTRACT[@]}" --install "$I" --out "$REFS"
CXX=${CXX:-g++}
CC=${CC:-gcc}
FLAGS=(-std=c++17 -O2 -g -Wall -Wextra -Wno-unused-parameter -I"$GEN" -I"$HERE" -I"$HOST/player"
       -I"$I/include/dxvk" -I"$I/include")
CORE=("$HERE/d9g_device_draw.cpp" "$HERE/d9g_draw_state.cpp" "$HERE/d9g_device.cpp" "$HERE/d9g_resources.cpp" "$HERE/d9g_format.cpp" "$HERE/d9g_clear.cpp"
      "$HERE/d9g_report.cpp" "$HERE/d9g_pipeline.cpp" "$HERE/d9g_sampler.cpp" "$HERE/d9g_draw.cpp")

echo "== the shader translator (R1)"
# shader/build_translator.sh: its objects (with their depfiles, which the player's record reads),
# libd9g_shader.a and d3d9gpu_shaderc, under $OUT/shader; it refuses an archive other than the pin's.
SHADER_OUT=$OUT/shader
SA2_REPO="$REPO" SA2_S5_INSTALL="$I" PYTHON="$PY" bash "$HERE/shader/build_translator.sh" "$SHADER_OUT" > "$OUT/shader.log" 2>&1 \
  || { tail -20 "$OUT/shader.log"; echo "the shader translator's build failed (see $OUT/shader.log)"; exit 2; }
tail -1 "$OUT/shader.log"
TOBJS=("$SHADER_OUT"/shader-obj/*.o)
SHADER_LIBS=("$SHADER_OUT/libd9g_shader.a" "$DXBC_ARCHIVE")

echo "== libsa2_d3d9gpu.a"
# The device on SDL_gpu, each translation unit with its depfile (-MMD), which
# sa2_player_gpu.json is made from. host/player/sha256.cpp, which d9g calls, is the player's
# object, linked once.
DOBJ=$OUT/obj/d9g
mkdir -p "$DOBJ"
find "$DOBJ" -maxdepth 1 \( -type f -o -type l \) \( -name '*.o' -o -name '*.d' \) -delete
find "$OUT" -maxdepth 1 \( -type f -o -type l \) -name libsa2_d3d9gpu.a -delete
DOBJS=()
for src in "${CORE[@]}" "$HERE/d9g_gpu_sdl.cpp" "$HERE/d9g_entry.cpp" "$HERE/d9g_props.cpp"; do
  tu=$(basename "$src" .cpp)
  $CXX "${FLAGS[@]}" -I"$REFS/sdl/khronos" -MMD -MF "$DOBJ/$tu.d" -c "$src" -o "$DOBJ/$tu.o"
  DOBJS+=("$DOBJ/$tu.o")
done
ar rcs "$OUT/libsa2_d3d9gpu.a" "${DOBJS[@]}"

echo "== sa2_player_gpu"
# host/player/player.cpp and its units with the GPU contract (expected-gpu, and no -I of
# this directory: the player includes no d9g header), the clear matrix's SDL_gpu leg, and
# the archive; the DXVK player's flags and libraries otherwise.
GOBJ=$OUT/obj/sa2_player_gpu
mkdir -p "$GOBJ"
find "$GOBJ" -maxdepth 1 \( -type f -o -type l \) \( -name '*.o' -o -name '*.d' \) -delete
PFLAGS=(-std=c++17 -O2 -g -Wall -Wextra -Wno-unused-parameter -I"$OUT/expected-gpu" -DSA2_PLAYER_GPU=1)
GOBJS=()
for tu in player input mixer stream sha256 texcodec touch_overlay; do
  $CXX "${PFLAGS[@]}" -I"$STREAM_H_DIR" -I"$I/include/dxvk" -I"$I/include" -MMD -MF "$GOBJ/$tu.d" \
    -c "$P/$tu.cpp" -o "$GOBJ/$tu.o"
  GOBJS+=("$GOBJ/$tu.o")
done
$CXX "${FLAGS[@]}" -I"$HERE/test" -DSA2_CLEAR_MATRIX_STATIC -MMD -MF "$GOBJ/clear_matrix.d" \
  -c "$HERE/test/clear_matrix.cpp" -o "$GOBJ/clear_matrix.o"
GOBJS+=("$GOBJ/clear_matrix.o")
$CXX -o "$OUT/sa2_player_gpu" "${GOBJS[@]}" "$OUT/libsa2_d3d9gpu.a" "${SHADER_LIBS[@]}" -L"$I/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" -lSDL3 \
  -lpng -lzstd -ldl
# Three parts linked into one binary may define one symbol twice, and the linker keeps one
# copy of a weak definition silently: no symbol but the standard library's template code
# (mangled in std:: or __gnu_cxx::) may be defined by two of the player's objects, d9g's and the
# translator's (whose standard-library code is compiled with DXVK's -D_GLIBCXX_ASSERTIONS=1:
# shader/build_translator.sh).
defined_by() { nm --defined-only "$@" | awk 'NF == 3 && $2 ~ /^[A-Z]$/ {print $3}' | sort -u; }
for pair in "GOBJS DOBJS" "GOBJS TOBJS" "DOBJS TOBJS"; do
  set -- $pair
  declare -n first=$1 second=$2
  both=$(comm -12 <(defined_by "${first[@]}") <(defined_by "${second[@]}") \
         | grep -vE '^(_ZNSt|_ZNKSt|_ZSt|_ZN9__gnu_cxx|DW\.ref\.)' || true)
  unset -n first second
  [ -z "$both" ] || { echo "the player, d9g and the translator: $pair both define: $(c++filt <<< "$both" | tr '\n' ' ')"; exit 2; }
done

echo "== test_d3d9gpu"
$CXX "${FLAGS[@]}" -I"$HERE/test" -DSA2_TEST_TMP="\"$OUT\"" -o "$OUT/test_d3d9gpu" "$HERE/test/test_d3d9gpu.cpp" \
  "${CORE[@]}" "$HERE/d9g_gpu_cpu.cpp" "$HOST/player/texcodec.cpp" "$HOST/player/sha256.cpp" "${SHADER_LIBS[@]}" -lpng -ldl

echo "== d3d9gpu_state_check"
# DXVK's translation units compiled unmodified with DXVK's own flags (its compile_commands.json,
# minus output and include paths: test/extract_refs.py dxvk-flags), against the checkout's
# headers only: no dxbc-spirv path. Every header a compile read is held to its pinned blob.
DXINC=(-I"$DXVK/src/d3d9" -I"$DXVK/src/dxvk" -I"$DXVK/include" -I"$DXVK/include/vulkan/include"
       -I"$DXVK/include/spirv/include" -I"$DXVK/include/native" -I"$DXVK/include/native/windows"
       -I"$DXVK/include/native/directx")
OBJ=$REFS/obj
mkdir -p "$OBJ"
# An earlier build's depfile would stand in for a compile that wrote none (verify-deps reads
# what is here, and refuses anything but one SDL-side file).
find "$OBJ" -maxdepth 1 \( -type f -o -type l \) \( -name '*.o' -o -name '*.d' \) -delete
mapfile -t D3D9FL < <("${EXTRACT[@]}" dxvk-flags --source src/d3d9/d3d9_util.cpp --record "$REFS/flags.d3d9.json")
mapfile -t DXVKFL < <("${EXTRACT[@]}" dxvk-flags --source src/dxvk/dxvk_util.cpp --record "$REFS/flags.dxvk.json")
mapfile -t VKFL < <("${EXTRACT[@]}" dxvk-flags --source src/vulkan/vulkan_names.cpp --record "$REFS/flags.vulkan.json")
[ ${#D3D9FL[@]} -gt 5 ] && [ ${#DXVKFL[@]} -gt 5 ] && [ ${#VKFL[@]} -gt 5 ] || { echo "no DXVK compile flags"; exit 2; }
SECT=(-ffunction-sections -fdata-sections)
for tu in src/d3d9/d3d9_util src/d3d9/d3d9_format; do
  $CXX "${D3D9FL[@]}" "${SECT[@]}" "${DXINC[@]}" -MD -MF "$OBJ/$(basename $tu).d" -c "$DXVK/$tu.cpp" -o "$OBJ/$(basename $tu).o"
done
for tu in src/dxvk/dxvk_constant_state src/dxvk/dxvk_format src/dxvk/dxvk_util; do
  $CXX "${DXVKFL[@]}" "${SECT[@]}" "${DXINC[@]}" -MD -MF "$OBJ/$(basename $tu).d" -c "$DXVK/$tu.cpp" -o "$OBJ/$(basename $tu).o"
done
$CXX "${VKFL[@]}" "${SECT[@]}" "${DXINC[@]}" -MD -MF "$OBJ/vulkan_names.d" -c "$DXVK/src/vulkan/vulkan_names.cpp" -o "$OBJ/vulkan_names.o"
for f in ref_dxvk ref_link_shims; do
  $CXX "${D3D9FL[@]}" "${SECT[@]}" "${DXINC[@]}" -I"$REFS" -I"$HERE" -I"$HERE/test" -MD -MF "$OBJ/$f.d" \
    -c "$HERE/test/$f.cpp" -o "$OBJ/$f.o"
done
$CC -std=c11 -O2 -Wall -Wextra -Wno-unused-parameter -I"$REFS" -I"$REFS/sdl/include" -I"$REFS/sdl/khronos" -I"$HERE/test" \
  -MD -MF "$OBJ/ref_sdl.d" -c "$HERE/test/ref_sdl.c" -o "$OBJ/ref_sdl.o"
"${EXTRACT[@]}" verify-deps --deps "$OBJ"/*.d
# d9g_props.cpp, d9g's maker of a description's SDL properties, calls SDL's property functions,
# which the check's SDL side (ref_sdl.o) defines as its model: no libSDL3 is linked.
$CXX "${FLAGS[@]}" -I"$HERE/test" -o "$OUT/d3d9gpu_state_check" "$HERE/test/state_check.cpp" "$HERE/d9g_pipeline.cpp" \
  "$HERE/d9g_sampler.cpp" "$HERE/d9g_props.cpp" "$HERE/d9g_draw.cpp" "$HERE/d9g_format.cpp" "$HOST/player/sha256.cpp" \
  "$OBJ"/{ref_dxvk,ref_link_shims,ref_sdl}.o \
  "$OBJ"/{d3d9_util,d3d9_format,dxvk_constant_state,dxvk_format,dxvk_util,vulkan_names}.o -Wl,--gc-sections -ldl
STATE_SYMS=$(nm "$OUT/d3d9gpu_state_check")   # (read whole: a pipe into grep -q fails under pipefail)
if grep -q dxbc_spv <<< "$STATE_SYMS"; then echo "the state check links dxbc-spirv code"; exit 2; fi

echo "== sa2_frame_census"
$CXX "${FLAGS[@]}" -I"$STREAM_H_DIR" -o "$OUT/sa2_frame_census" "$HERE/census.cpp" "$HERE/d9g_clear.cpp" \
  "$HERE/d9g_format.cpp" "$HOST/player/stream.cpp" "$HOST/player/sha256.cpp" -lzstd

echo "== d3d9gpu_clear_matrix"
$CXX "${FLAGS[@]}" -I"$HERE/test" -o "$OUT/d3d9gpu_clear_matrix" "$HERE/test/clear_matrix.cpp" -L"$I/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" \
  -lSDL3 -ldl

echo "== d3d9gpu_draw_matrix"
DMOBJ=$OUT/obj/draw_matrix
mkdir -p "$DMOBJ"
DMOBJS=()
for src in "$HERE/test/draw_matrix.cpp" "$P/sha256.cpp" "$P/texcodec.cpp"; do
  tu=$(basename "$src" .cpp)
  $CXX "${FLAGS[@]}" -MMD -MF "$DMOBJ/$tu.d" -c "$src" -o "$DMOBJ/$tu.o"
  DMOBJS+=("$DMOBJ/$tu.o")
done
$CXX -o "$OUT/d3d9gpu_draw_matrix" "${DMOBJS[@]}" "$OUT/libsa2_d3d9gpu.a" \
  "${SHADER_LIBS[@]}" -L"$I/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" -lSDL3 -lpng -ldl

# The second host's player is its own renderer and nothing of DXVK's: it defines
# Direct3DCreate9, needs no DXVK library, holds neither DXVK's library name nor its pinned
# hash, and its contract is SDL's three keys.
# (The tables are read whole before they are searched: a pipe into grep -q fails under pipefail.
# The names are searched for with the paths the binary embeds of where it was built taken
# out, as host/player/build.sh searches sa2_player: the working directory as a whole string
# only.)
# SDL3 from the install alone (host/player/build.sh's rule): DT_RPATH, no DT_RUNPATH.
for bin in sa2_player_gpu d3d9gpu_clear_matrix d3d9gpu_draw_matrix; do
  DYN=$(readelf -d "$OUT/$bin")
  if ! grep -qF "Library rpath: [$I/lib]" <<< "$DYN" || grep -q "(RUNPATH)" <<< "$DYN"; then
    echo "$bin does not load SDL3 from $I/lib by DT_RPATH alone"; exit 2
  fi
done
DEFINED=$(nm --defined-only "$OUT/sa2_player_gpu")
NEEDED=$(readelf -d "$OUT/sa2_player_gpu" | grep NEEDED || true)
grep -qE ' T Direct3DCreate9$' <<< "$DEFINED" || { echo "sa2_player_gpu does not define Direct3DCreate9"; exit 2; }
if grep -qi dxvk <<< "$NEEDED"; then echo "sa2_player_gpu needs a DXVK library"; exit 2; fi
"$PY" - "$OUT/sa2_player_gpu" "$REPO/tools/pins.json" "$PWD" "$HOST/.." "$STREAM_H_DIR" "$OUT" "$I" <<'EOF_NAMES'
import json, os, re, sys
data = open(sys.argv[1], "rb").read()
pin = json.load(open(sys.argv[2]))["tools"]["dxvk"]["library_sha256"]
for cwd in {os.path.abspath(sys.argv[3]), os.path.realpath(sys.argv[3])}:
    data = re.sub(rb"(?<=\x00)" + re.escape(cwd.encode()) + rb"(?=\x00)", b"", data)
for path in sorted({q for p in sys.argv[4:] for q in (os.path.abspath(p), os.path.realpath(p))}, key=len, reverse=True):
    data = data.replace(path.encode(), b"")
held = [w for w in (pin, "libdxvk_d3d9.so") if w.encode() in data]
if held:
    print(f"sa2_player_gpu holds {held}")
    sys.exit(2)
EOF_NAMES
"$PY" -c '
import json, sys
c = list(json.load(open(sys.argv[1]))["contract"])
if c != ["sdl_sha256", "sdl_version", "sdl_revision"]:
    print(f"the GPU contract is {c}")
    sys.exit(2)' "$OUT/expected-gpu/expectations.json"

# The build's identity beside the sources it was built from (the critic's A17), by path in
# the checkout the build read, unchanged since the build started: the player's exact record
# and the tools' group record. tools/s5/compare_hosts.py refuses a build whose records do not
# match the tree.
"$PY" "$P/build_record.py" --out "$OUT/sa2_player_gpu.json" --tree "$HOST/.." --bin-dir "$OUT" \
  --binaries sa2_player_gpu --deps "$DOBJ"/*.d "$GOBJ"/*.d "$SHADER_OUT"/shader-obj/*.d --compiler "$CXX" \
  --sources "$HERE/build.sh" "$HERE/gen_com.py" "$HERE/test/extract_refs.py" "$P/generate_expectations.py" \
  "$HERE/shader/build_translator.sh" "$HERE/shader/extract_dxvk_shader.py" "$REPO/tools/pins.json" \
  --generated "$OUT/expected-gpu" "$OUT/libsa2_d3d9gpu.a" "$SHADER_OUT/libd9g_shader.a" "$SHADER_OUT/translator_identity.json"
# The tools keep their complete source inventory alongside the native probe's depfiles.
# Exact records require concrete files; expand the start inventory with the same owner.
"$PY" - "$P" "$OUT/d3d9gpu_tools.json.start" "$HOST/.." "${SOURCES[@]}" > "$OUT/d3d9gpu-tools-sources.bin" <<'EOF_TOOL_SOURCES'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from build_record import changed, expand, hash_sources
files = expand([Path(p) for p in sys.argv[4:]])
started = json.loads(Path(sys.argv[2]).read_text())
moved = changed(started, hash_sources(Path(sys.argv[3]), files))
if moved:
    raise SystemExit("tools source inventory changed during the build:\n  " + "\n  ".join(moved))
for path in files:
    sys.stdout.buffer.write(str(path).encode() + b'\0')
EOF_TOOL_SOURCES
mapfile -d '' -t TOOL_SOURCES < "$OUT/d3d9gpu-tools-sources.bin"
[ "${#TOOL_SOURCES[@]}" -gt 0 ]
"$PY" "$P/build_record.py" --out "$OUT/d3d9gpu_tools.json" --tree "$HOST/.." --bin-dir "$OUT" \
  --binaries test_d3d9gpu sa2_frame_census d3d9gpu_clear_matrix d3d9gpu_state_check d3d9gpu_draw_matrix --deps "$DMOBJ"/*.d \
  --sources "${TOOL_SOURCES[@]}" --generated "$GEN" "$SHADER_OUT" "$OUT/libsa2_d3d9gpu.a" "$OUT/d3d9gpu-tools-sources.bin"
echo "sa2_player_gpu $(sha256sum "$OUT/sa2_player_gpu" | cut -c1-64)"
echo "D3D9GPU_BUILD_OK"
