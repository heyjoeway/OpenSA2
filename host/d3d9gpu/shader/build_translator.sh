#!/bin/bash
# Builds host 2's shader translator (R1): libd9g_shader.a and the d3d9gpu_shaderc tool, into OUT.
# host/d3d9gpu/build.sh runs it into $OUT/shader and links the archive into sa2_player_gpu and
# test_d3d9gpu; Documentation/scripts/tests/test_s5_d3d9gpu_shader.py runs it into a temporary
# directory.
#
#   host/d3d9gpu/shader/build_translator.sh OUT
#
# Every translator unit, and every DXVK unit it links (d3d9_shader_analysis.cpp,
# d3d9_constant_copy.cpp and dxvk_shader_io.cpp, compiled whole and unmodified from the pinned
# checkout), is compiled with the flags DXVK's own build used (its compile_commands.json, read by
# shader/extract_dxvk_shader.py): the d3d9 units' flags for everything on the d3d9 side, the dxvk
# units' for dxvk_shader_io.cpp. DXVK's legacy-input lowering pass is cut from the pinned
# d3d9_shader.cpp verbatim into a generated unit in OUT, never into the tree. Each compile writes its
# depfile (OUT/shader-obj/*.d), which the player's build record reads (host/player/build_record.py).
# The translator's inline standard-library code is compiled with DXVK's -D_GLIBCXX_ASSERTIONS=1 and
# the device's without it; the layouts are the same, the linker keeps one copy of each such weak
# definition, and host/d3d9gpu/build.sh's "both define" check exempts only the standard library's.
#
# dxbc-spirv is the pinned archive: tools/pins.json tools.dxvk.dxbc_spirv_archive, the objects of
# DXVK's own build materialised by tools/s5/build_host_deps.sh (R1 U1), so the translator runs the
# machine code DXVK runs. It is refused unless it hashes to dxbc_spirv_archive_sha256 and is a
# regular archive (never the build's thin archive). SDL3 (d3d9gpu_shaderc's GPU modes) is the
# install's (SA2_S5_INSTALL, default private/s5-tools/install), linked by DT_RPATH as the players are.
#
# The compiler is the one DXVK's compile_commands.json names (CXX may only repeat it). The
# translator's identity, the shader cache's key and D9G_TRANSLATOR_ID, is computed from contents
# only (extract_dxvk_shader.py identity): the archive's members, the compiler, every unit's flags
# and every file every unit's preprocessing reads, so it moves with any of them and with nothing
# else (not with the clone's path). The manifest is OUT/translator_identity.json.
#
# Two guards fail the build: a DXVK symbol the translator's objects leave unresolved that
# d9g_shader_shims.cpp does not define (so a new dependency is seen, never silently stubbed), and
# host 2's own objects (d9g_*.o) reaching DXVK anywhere but the shared half ruling 2 names: any
# symbol of D3D9ConstantBufferCopy (the point where API state becomes shader input, which stays
# the device's own code: ruling 2's line, the R1 plan's section 2.2), and any unresolved DXVK
# symbol outside DXVK_SHARED below. Prints D3D9GPU_SHADER_BUILD_OK.
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd -P)          # host/d3d9gpu
REPO=${SA2_REPO:-$(cd "$HERE/../.." && pwd -P)}
[ $# -eq 1 ] || { echo "usage: build_translator.sh OUT" >&2; exit 2; }
OUT=$1
I=${SA2_S5_INSTALL:-$REPO/private/s5-tools/install}
DXVK=${SA2_DXVK_SRC:-$REPO/private/s5-tools/src/dxvk-3.1.1}
CC_JSON=$REPO/private/s5-tools/build/dxvk/compile_commands.json
PY=${PYTHON:-$REPO/.venv/bin/python}
[ -f "$CC_JSON" ] || { echo "no DXVK compile_commands.json at $CC_JSON (tools/s5/build_host_deps.sh)" >&2; exit 2; }
[ -f "$I/lib/libSDL3.so.0" ] || { echo "no SDL3 under $I (tools/s5/build_host_deps.sh)" >&2; exit 2; }
I=$(cd "$I" && pwd -P)
# The pinned archive, by its pin: the path and the sha256 tools/pins.json holds, a regular archive.
ARCHIVE=$("$PY" - "$REPO" <<'EOF'
import hashlib, json, sys
from pathlib import Path
repo = Path(sys.argv[1])
pin = json.loads((repo / "tools" / "pins.json").read_text())["tools"]["dxvk"]
path = repo / pin["dxbc_spirv_archive"]
if not path.is_file():
    sys.exit(f"no dxbc-spirv archive at {path} (tools/s5/build_host_deps.sh)")
data = path.read_bytes()
if not data.startswith(b"!<arch>\n"):
    sys.exit(f"{path} is not a regular archive (a thin archive's members are DXVK's build directory, not the pin)")
got = hashlib.sha256(data).hexdigest()
if got != pin["dxbc_spirv_archive_sha256"]:
    sys.exit(f"{path} hashes to {got}, not its pin {pin['dxbc_spirv_archive_sha256']} (tools/pins.json)")
print(path.resolve())
EOF
) || { echo "build_translator.sh: the dxbc-spirv archive is refused" >&2; exit 2; }
DXVK_CXX=$("$PY" "$HERE/shader/extract_dxvk_shader.py" compiler --compile-commands "$CC_JSON" \
  --source src/d3d9/d3d9_shader.cpp --source src/d3d9/d3d9_shader_analysis.cpp --source src/dxvk/dxvk_shader_io.cpp)
if [ -n "${CXX:-}" ] && [ "$(realpath "$(command -v "$CXX")")" != "$DXVK_CXX" ]; then
  echo "CXX=$CXX is not the compiler DXVK's objects were built with ($DXVK_CXX)" >&2; exit 2
fi
CXX=$DXVK_CXX
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd -P)
OBJ=$OUT/shader-obj
mkdir -p "$OBJ"
# An earlier build's objects and depfiles would stand in for a compile that failed: start empty
# (find, not rm).
find "$OBJ" -maxdepth 1 -type f \( -name '*.o' -o -name '*.d' \) -delete
find "$OUT" -maxdepth 1 -type f \( -name libd9g_shader.a -o -name d3d9gpu_shaderc -o -name shaderc.o -o -name shaderc.d \) -delete
EXTRACT=("$PY" "$HERE/shader/extract_dxvk_shader.py")

"${EXTRACT[@]}" lower --dxvk "$DXVK" --repo "$REPO" --out "$OUT/d9g_dxvk_lower.gen.cpp" --record "$OUT/d9g_dxvk_lower.json"
mapfile -t D3D9FL < <("${EXTRACT[@]}" flags --compile-commands "$CC_JSON" --source src/d3d9/d3d9_shader.cpp --record "$OUT/flags.d3d9_shader.json")
mapfile -t DXVKFL < <("${EXTRACT[@]}" flags --compile-commands "$CC_JSON" --source src/dxvk/dxvk_shader_io.cpp --record "$OUT/flags.dxvk_shader_io.json")
[ ${#D3D9FL[@]} -gt 5 ] && [ ${#DXVKFL[@]} -gt 5 ] || { echo "no DXVK compile flags" >&2; exit 2; }

INC_DIRS=("$HERE" "$HERE/test" "$REPO/host/player")
INC=("${INC_DIRS[@]/#/-I}")
TUS=(d9g_shader d9g_shader_abi d9g_shader_link d9g_shader_cache d9g_shader_shims d9g_shader_profile d9g_shader_vulkan)
D3D9_UNITS=("$OUT/d9g_dxvk_lower.gen.cpp" "$DXVK/src/d3d9/d3d9_shader_analysis.cpp" "$DXVK/src/d3d9/d3d9_constant_copy.cpp")
for tu in "${TUS[@]}"; do D3D9_UNITS+=("$HERE/$tu.cpp"); done
UNITS=("${D3D9_UNITS[@]/#/--unit=$OUT/flags.d3d9_shader.json:}" "--unit=$OUT/flags.dxvk_shader_io.json:$DXVK/src/dxvk/dxvk_shader_io.cpp")
TRANSLATOR_ID=$("$PY" "$HERE/shader/extract_dxvk_shader.py" identity --compiler "$CXX" --archive "$ARCHIVE" \
  --repo "$REPO" --dxvk "$DXVK" --out "$OUT/translator_identity.json" "${UNITS[@]}" "${INC_DIRS[@]/#/--include=}" \
  --extra "$HERE/shader/build_translator.sh" --extra "$HERE/shader/extract_dxvk_shader.py")
echo "translator $TRANSLATOR_ID"

DEF=(-DD9G_TRANSLATOR_ID="\"$TRANSLATOR_ID\"")
SECT=(-ffunction-sections -fdata-sections)
compile_d3d9() { "$CXX" "${D3D9FL[@]}" "${SECT[@]}" "${INC[@]}" "${DEF[@]}" -MMD -MF "$OBJ/$2.d" -c "$1" -o "$OBJ/$2.o"; }
compile_d3d9 "$OUT/d9g_dxvk_lower.gen.cpp" dxvk_lower
compile_d3d9 "$DXVK/src/d3d9/d3d9_shader_analysis.cpp" dxvk_d3d9_shader_analysis
compile_d3d9 "$DXVK/src/d3d9/d3d9_constant_copy.cpp" dxvk_d3d9_constant_copy
"$CXX" "${DXVKFL[@]}" "${SECT[@]}" -MMD -MF "$OBJ/dxvk_shader_io.d" -c "$DXVK/src/dxvk/dxvk_shader_io.cpp" -o "$OBJ/dxvk_shader_io.o"
for tu in "${TUS[@]}"; do
  compile_d3d9 "$HERE/$tu.cpp" "$tu"
done
# The archive holds the translator alone: host/player/sha256.cpp, which d9g_shader.cpp and the cache
# call, is the player's object in sa2_player_gpu and test_d3d9gpu (linked once), and d3d9gpu_shaderc's
# own below.
ar rcsD "$OUT/libd9g_shader.a" "$OBJ"/*.o

# Guard 1: host 2's own objects reach DXVK only through the shared half: DXVK's analysis, its
# lowering pass and its stage-compatibility check (ruling 2). Never its constant copy, by any
# symbol kind (an inline call leaves a weak definition, not an undefined one), and no other
# unresolved DXVK symbol. Each nm output is read whole into a variable first: a pipe into grep -q
# can fail under pipefail.
DXVK_SHARED='dxvk::D3D9ShaderAnalysis::D3D9ShaderAnalysis
dxvk::DxvkShaderIo::DxvkShaderIo
dxvk::DxvkShaderIo::add
dxvk::DxvkShaderIo::checkStageCompatibility
dxvk::DxvkShaderIo::~DxvkShaderIo
dxvk::d9gDxvkLowerLegacyInput'
for o in "$OBJ"/d9g_*.o; do
  SYMS=$(nm -C "$o")
  case "$SYMS" in
    *D3D9ConstantBufferCopy*)
      echo "$(basename "$o") references D3D9ConstantBufferCopy::copyConstantData: the constant packing is the device's own" >&2
      exit 2 ;;
  esac
  USED=$(nm -C -u "$o" | sed -n 's/^ *U \(dxvk::[^(]*\).*/\1/p' | sort -u)
  OTHER=$(comm -23 <(echo "$USED") <(echo "$DXVK_SHARED" | sort -u) | sed '/^$/d' | tr '\n' ' ')
  [ -z "$OTHER" ] || { echo "$(basename "$o") reaches DXVK outside the shared half: $OTHER" >&2; exit 2; }
done

# d3d9gpu_shaderc: the translator from the command line, and its GPU modes on SDL_gpu (test/shaderc.cpp).
"$CXX" "${D3D9FL[@]}" "${INC[@]}" -I"$I/include" "${DEF[@]}" -MMD -MF "$OUT/shaderc.d" -c "$HERE/test/shaderc.cpp" -o "$OUT/shaderc.o"
"$CXX" "${D3D9FL[@]}" "${INC[@]}" -MMD -MF "$OUT/shaderc_sha256.d" -c "$REPO/host/player/sha256.cpp" -o "$OUT/shaderc_sha256.o"
"$CXX" -o "$OUT/d3d9gpu_shaderc" "$OUT/shaderc.o" "$OUT/shaderc_sha256.o" "$OUT/libd9g_shader.a" "$ARCHIVE" \
  -L"$I/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" -lSDL3 -Wl,--gc-sections -ldl

# Guard 2: the shims define DXVK names only where DXVK's units leave them unresolved, and nothing
# else of DXVK's is left unresolved.
# (Whole outputs are read into variables first: a pipe into grep -q fails under pipefail.)
SHIMMED=$(nm -C --defined-only "$OBJ/d9g_shader_shims.o" | grep -o 'dxvk::[A-Za-z0-9_:]*' | sort -u | tr '\n' ' ')
[ "$SHIMMED" = "dxvk::Logger::err " ] || { echo "the shims define more than dxvk::Logger::err: $SHIMMED" >&2; exit 2; }
# A symbol is its demangled name up to the parameter list.
DEFINED=$(nm -C --defined-only "$OBJ"/*.o | sed -n 's/^[0-9a-f]* [TtWwVvDdBbRr] \(dxvk::[^(]*\).*/\1/p' | sort -u)
WANTED=$(for o in "$OBJ"/dxvk_*.o; do nm -C -u "$o"; done | sed -n 's/^ *U \(dxvk::[^(]*\).*/\1/p' | sort -u)
UNDEF=$(comm -23 <(echo "$WANTED") <(echo "$DEFINED") | tr '\n' ' ')
[ -z "${UNDEF// /}" ] || { echo "DXVK symbols no unit defines: $UNDEF" >&2; exit 2; }
echo "D3D9GPU_SHADER_BUILD_OK $OUT/d3d9gpu_shaderc $ARCHIVE"
