#!/bin/bash
# Builds S5's shader tools from their checkouts into private/s5-tools/install-shader-tools, and
# nowhere else: SPIRV-Tools (spirv-val, the independent validity check of every SPIR-V module
# host 2 creates, and spirv-as, which assembles the comparer's hand-built test pairs),
# SPIRV-Cross and SDL_shadercross (SDL's shader tool: SPIR-V to MSL, the Metal-conversion check
# of ruling 2), and the Khronos Vulkan validation layers.
#
#   tools/s5/build_shader_tools.sh                 check the checkouts against tools/pins.json,
#                                                  build, then check the installed files
#   tools/s5/build_shader_tools.sh --propose-pins OUT
#                                                  build without source pins, and write the
#                                                  pins.json entries the build would need to OUT
#
# The seven sources are shallow checkouts under private/s5-tools/src/shader-tools/, fetched at
# the versions the user approved by name on 2026-10-04
# (Documentation/decisions/2026-10-04-S5-shader-tool-downloads.md): SDL_shadercross at
# 1ff05bec, SPIRV-Cross at the commit shadercross records for its submodule, SPIRV-Tools and
# the validation layers at vulkan-sdk-1.4.309.0, and the three build dependencies asked for
# by name (SPIRV-Headers 09913f08, Vulkan-Utility-Libraries 50563f48, robin-hood-hashing
# 3.11.5). The script never fetches. Vulkan-Headers is the system's 1.4.309
# (/usr/share/cmake/VulkanHeaders), which the validation layers' known_good.json names.
#
# Before anything is built, each checkout must be a git checkout whose HEAD is the pinned
# commit, whose HEAD^{tree} is the pinned tree, and whose working tree is clean with nothing
# untracked or ignored (the builds are out of tree, so a build cannot dirty it). The pinned
# cmake, ninja and gcc are checked by the pin gate, and every build runs in a fixed environment
# (env -i with HOME, LC_ALL, PATH=/usr/bin:/bin, CC and CXX from the gcc pin), as
# tools/s5/build_host_deps.sh does. SDL_shadercross is configured with DirectXShaderCompiler
# off (SDLSHADERCROSS_DXC=OFF), not vendored, against the shared SPIRV-Cross C library built
# here and the pinned SDL3 in private/s5-tools/install, which is only read: the patched
# configuration's SDL (tools.sdl3), so the shadercross pins name that SDL as an input, and a
# rebuild after the install's SDL changes confirms them or moves them (rule 8). The log prints
# the SDL library it links against, with its sha256.
#
# Every run starts clean: every build directory is removed and configured anew, and the new
# install is assembled in install-shader-tools.partial and moved into place only when every
# build has installed. The install it replaces is removed only after that move. A build time a
# tool embeds is its checkout's commit time (SOURCE_DATE_EPOCH), so a rebuild is byte for byte the
# same; the pins depend on it. After the
# build the script prints the sha256 of each installed file the pins name and, unless
# --propose-pins, fails when one differs from its pin, so a deliberate rebuild moves the pins
# in the commit that moves the install (AGENTS.md rule 8).
set -euo pipefail

usage() {
  echo "usage: tools/s5/build_shader_tools.sh [--propose-pins OUT]" >&2
  exit 2
}
PROPOSE=""
case "$#:${1:-}" in
  0:) ;;
  2:--propose-pins) PROPOSE=$2 ;;
  *) echo "build_shader_tools.sh: unknown arguments: $*" >&2; usage ;;
esac

die() { echo "build_shader_tools.sh: $*" >&2; exit 1; }
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR \
  GIT_ALTERNATE_OBJECT_DIRECTORIES
ROOT=$(cd "$(dirname "$0")/../.." && pwd -P)
[ -f "$ROOT/tools/pins.json" ] && [ -f "$ROOT/AGENTS.md" ] || die "not a repository root: $ROOT"
B=$(cd "$ROOT/private/s5-tools" && pwd -P)
SRC=$B/src/shader-tools
IS=$B/install-shader-tools
HOST=$B/install            # the pinned SDL3 shadercross links against; read only
BUILD=$B/build/shader-tools
PY=$ROOT/.venv/bin/python
GATE=("$PY" "$ROOT/Documentation/scripts/check_pins.py" --root "$ROOT")

# Pin key, checkout directory under $SRC, and the version the approval names.
SOURCES=(
  "spirv-headers:SPIRV-Headers:09913f088a1197aba4aefd300a876b2ebbaa3391"
  "spirv-tools:SPIRV-Tools:vulkan-sdk-1.4.309.0"
  "spirv-cross:SPIRV-Cross:1a6169566c73d3da552748fc372fe2bbb856e46e"
  "sdl-shadercross:SDL_shadercross:1ff05bec573988a98ef9e0260b4da44f512b8367"
  "vulkan-utility-libraries:Vulkan-Utility-Libraries:50563f48368d75281bc2fb1c3407dc531ce28910"
  "robin-hood-hashing:robin-hood-hashing:3.11.5"
  "vulkan-validation-layers:Vulkan-ValidationLayers:vulkan-sdk-1.4.309.0"
)
# Installed files the pins hold, relative to $IS: pin key, pin field, path.
OUTPUTS=(
  "spirv-tools:binary:bin/spirv-val"
  "spirv-tools:disassembler:bin/spirv-dis"
  "spirv-tools:assembler:bin/spirv-as"
  "spirv-tools:optimizer:bin/spirv-opt"
  "spirv-cross:library:lib/libspirv-cross-c-shared.so.0"
  "spirv-cross:binary:bin/spirv-cross"
  "sdl-shadercross:binary:bin/shadercross"
  "sdl-shadercross:library:lib/libSDL3_shadercross.so.0"
  "vulkan-validation-layers:library:lib/libVkLayer_khronos_validation.so"
  "vulkan-validation-layers:manifest:share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json"
)

pin() {  # tool key: the pinned string, or empty when the pins hold none
  "$PY" - "$ROOT/tools/pins.json" "$1" "$2" <<'EOF'
import json, sys
value = json.load(open(sys.argv[1]))["tools"].get(sys.argv[2], {})
for key in sys.argv[3].split("."):
    value = value.get(key) if isinstance(value, dict) else None
print(value if isinstance(value, str) else "")
EOF
}

need_pin() {  # tool key: the pinned string, or the script stops
  local v
  v=$(pin "$1" "$2")
  [ -n "$v" ] || die "tools/pins.json pins no $1 $2 (build with --propose-pins OUT to get the entries)"
  echo "$v"
}

remove_tree() {  # a literal directory under $B, removed by Python, never through rm and a variable
  "$PY" - "$B" "$1" <<'EOF'
import os, shutil, sys
base, path = os.path.realpath(sys.argv[1]), os.path.realpath(sys.argv[2])
if not path.startswith(base + os.sep) or path == base:
    sys.exit(f"refusing to remove {path}: not under {base}")
if os.path.islink(sys.argv[2]):
    os.remove(sys.argv[2])
elif os.path.isdir(path):
    shutil.rmtree(path)
EOF
}

echo "== the checkouts"
declare -A COMMIT TREE
for entry in "${SOURCES[@]}"; do
  IFS=: read -r key dir version <<<"$entry"
  co=$SRC/$dir
  [ -d "$co/.git" ] || [ -f "$co/.git" ] || die "$co is not a git checkout"
  c=$(git -C "$co" rev-parse HEAD)
  t=$(git -C "$co" rev-parse 'HEAD^{tree}')
  dirty=$(git --no-optional-locks -C "$co" status --porcelain --ignored --untracked-files=all)
  [ -z "$dirty" ] || die "$co is not clean: $(echo "$dirty" | head -3 | tr '\n' ' ')"
  COMMIT[$key]=$c; TREE[$key]=$t
  if [ -z "$PROPOSE" ]; then
    [ "$c" = "$(need_pin "$key" commit)" ] || die "$co is at $c, not the pinned commit"
    [ "$t" = "$(need_pin "$key" tree)" ] || die "$co holds tree $t, not the pinned tree"
    [ "$(need_pin "$key" path)" = "private/s5-tools/src/shader-tools/$dir" ] \
      || die "$key's pinned path is not private/s5-tools/src/shader-tools/$dir"
  fi
  echo "$key $version commit $c tree $t"
done
SC_CROSS=$(git -C "$SRC/SDL_shadercross" ls-tree HEAD external/SPIRV-Cross | awk '{print $3}')
[ "$SC_CROSS" = "${COMMIT[spirv-cross]}" ] \
  || die "SDL_shadercross records SPIRV-Cross $SC_CROSS, the checkout is ${COMMIT[spirv-cross]}"
for f in VulkanHeadersConfig.cmake VulkanHeadersConfigVersion.cmake; do
  [ -f "/usr/share/cmake/VulkanHeaders/$f" ] || die "the system's Vulkan headers have no $f"
done
grep -q '^#define VK_HEADER_VERSION 309$' /usr/include/vulkan/vulkan_core.h \
  || die "the system's Vulkan headers are not 1.4.309"
[ -f "$HOST/lib/cmake/SDL3/SDL3Config.cmake" ] || die "no pinned SDL3 in $HOST (tools/s5/build_host_deps.sh)"
# Which SDL3 SDL_shadercross is built against is an input of its pins: the log names it (the
# patched install's since the I4 rebuild; a stock one before it).
SDL_LINKED=$(realpath -e "$HOST/lib/libSDL3.so.0")
SDL_VENDOR=$(sed -n 's/^#define SDL_VENDOR_INFO "\(.*\)"$/\1/p' "$HOST/include/SDL3/SDL_revision.h")
echo "== SDL3 for SDL_shadercross: $HOST/lib/libSDL3.so.0 -> $(basename "$SDL_LINKED")," \
  "sha256 $(sha256sum "$SDL_LINKED" | cut -c1-64), SDL_VENDOR_INFO ${SDL_VENDOR:-none (stock)}"

echo "== the build tools, by the pin gate"
"${GATE[@]}" --only cmake --only ninja --only gcc
CMAKE=$(need_pin cmake path); NINJA=$(need_pin ninja path); CC=$(need_pin gcc path)
CXX=$(dirname "$CC")/g++
BUILD_ENV=(/usr/bin/env -i "HOME=$HOME" LC_ALL=C.UTF-8 "PATH=/usr/bin:/bin" "CC=$CC" "CXX=$CXX")
CXX_VERSION=$("${BUILD_ENV[@]}" "$CXX" -dumpfullversion)
[ "$CXX_VERSION" = "$(need_pin gcc version)" ] \
  || die "$CXX reports version $CXX_VERSION, not the gcc pin's"
for tool in "$CC" "$CXX" "$CMAKE" "$NINJA"; do
  echo "$tool: $("${BUILD_ENV[@]}" "$tool" --version | sed -n 1p)"
done
J=$(( $(nproc) > 8 ? 8 : $(nproc) ))
# The validation layers' translation units each take more than a gigabyte to compile.
JV=$(( J > 6 ? 6 : J ))

[ ! -e "$IS.partial" ] || remove_tree "$IS.partial"
mkdir -p "$IS.partial" "$BUILD"
P=$IS.partial
COMMON=(-G Ninja -DCMAKE_MAKE_PROGRAM="$NINJA" -DCMAKE_BUILD_TYPE=Release
        -DCMAKE_INSTALL_PREFIX="$P" -DCMAKE_INSTALL_LIBDIR=lib
        -DCMAKE_PREFIX_PATH="$P;$HOST" -DCMAKE_INSTALL_RPATH="$IS/lib"
        -DCMAKE_BUILD_RPATH_USE_ORIGIN=OFF)

build() {  # name source-dir jobs [cmake options...]
  local name=$1 src=$2 jobs=$3 epoch
  shift 3
  echo "== $name"
  [ ! -e "$BUILD/$name" ] || remove_tree "$BUILD/$name"
  # A build time in the output (SPIRV-Cross's string(TIMESTAMP)) is the checkout's commit time,
  # so two builds of one checkout give the same bytes.
  epoch=$(git -C "$src" log -1 --format=%ct)
  "${BUILD_ENV[@]}" "SOURCE_DATE_EPOCH=$epoch" "$CMAKE" --fresh -S "$src" -B "$BUILD/$name" "${COMMON[@]}" "$@" \
    > "$BUILD/$name-configure.log"
  "${BUILD_ENV[@]}" "SOURCE_DATE_EPOCH=$epoch" "$NINJA" -C "$BUILD/$name" -j"$jobs" install > "$BUILD/$name-build.log"
}

build spirv-headers "$SRC/SPIRV-Headers" "$J" -DSPIRV_HEADERS_ENABLE_TESTS=OFF
build spirv-tools "$SRC/SPIRV-Tools" "$J" \
  -DSPIRV-Headers_SOURCE_DIR="$SRC/SPIRV-Headers" -DSPIRV_SKIP_TESTS=ON \
  -DSPIRV_WERROR=OFF -DSPIRV_SKIP_EXECUTABLES=OFF -DBUILD_SHARED_LIBS=OFF
build spirv-cross "$SRC/SPIRV-Cross" "$J" \
  -DSPIRV_CROSS_SHARED=ON -DSPIRV_CROSS_STATIC=ON -DSPIRV_CROSS_CLI=ON \
  -DSPIRV_CROSS_ENABLE_TESTS=OFF -DSPIRV_CROSS_FORCE_PIC=ON
build sdl-shadercross "$SRC/SDL_shadercross" "$J" \
  -DSDLSHADERCROSS_DXC=OFF -DSDLSHADERCROSS_VENDORED=OFF -DSDLSHADERCROSS_SPIRVCROSS_SHARED=ON \
  -DSDLSHADERCROSS_SHARED=ON -DSDLSHADERCROSS_STATIC=OFF -DSDLSHADERCROSS_CLI=ON \
  -DSDLSHADERCROSS_INSTALL=ON -DSDLSHADERCROSS_TESTS=OFF -DSDLSHADERCROSS_WERROR=OFF \
  -DCMAKE_INSTALL_RPATH="$IS/lib;$HOST/lib"
grep -q '^SDLSHADERCROSS_DXC:BOOL=OFF$' "$BUILD/sdl-shadercross/CMakeCache.txt" \
  || die "SDL_shadercross was not configured with DirectXShaderCompiler off"
build vulkan-utility-libraries "$SRC/Vulkan-Utility-Libraries" "$J" \
  -DUPDATE_DEPS=OFF -DBUILD_TESTS=OFF
build robin-hood-hashing "$SRC/robin-hood-hashing" "$J" -DRH_STANDALONE_PROJECT=OFF
build vulkan-validation-layers "$SRC/Vulkan-ValidationLayers" "$JV" \
  -DUPDATE_DEPS=OFF -DBUILD_TESTS=OFF -DBUILD_WERROR=OFF \
  -DSPIRV_HEADERS_INSTALL_DIR="$P" -DSPIRV_TOOLS_INSTALL_DIR="$P" \
  -DVULKAN_UTILITY_LIBRARIES_INSTALL_DIR="$P" -DROBIN_HOOD_HASHING_INSTALL_DIR="$P"

for entry in "${SOURCES[@]}"; do
  IFS=: read -r key dir version <<<"$entry"
  [ -z "$(git --no-optional-locks -C "$SRC/$dir" status --porcelain --ignored --untracked-files=all)" ] \
    || die "the build changed the checkout $SRC/$dir"
done

echo "== the install moves into place"
if [ -e "$IS" ]; then
  [ ! -e "$IS.replaced" ] || remove_tree "$IS.replaced"
  mv "$IS" "$IS.replaced"
fi
mv "$P" "$IS"
[ ! -e "$IS.replaced" ] || remove_tree "$IS.replaced"
# The RPATHs name $IS, the final place. Only CMake and pkg-config files may still name the
# staging directory; no consumer builds against them.
STAGED=$(grep -rlF "$IS.partial" "$IS/bin" "$IS/lib" 2>/dev/null | grep -v '\.cmake$\|\.pc$' || true)
[ -z "$STAGED" ] || die "installed files name the staging directory $IS.partial: $STAGED"

echo "== sha256 of the installed files the pins name (a link followed to its file)"
FAIL=0
declare -A SHA
for entry in "${OUTPUTS[@]}"; do
  IFS=: read -r key field rel <<<"$entry"
  f=$IS/$rel
  [ -e "$f" ] || die "$f was not installed"
  reached=$(realpath -e "$f")
  s=$(sha256sum "$reached" | cut -d' ' -f1)
  SHA[$key:$field]=$s
  echo "$key ${field}_sha256 $s private/s5-tools/install-shader-tools/$rel$([ -L "$f" ] && echo " -> $(basename "$reached")")"
  if [ -z "$PROPOSE" ] && [ "$s" != "$(need_pin "$key" "${field}_sha256")" ]; then
    echo "  differs from the pin" >&2; FAIL=1
  fi
done
"$IS/bin/spirv-val" --version | sed -n 1p
"$IS/bin/shadercross" --help 2>&1 | sed -n 1p || true

if [ -n "$PROPOSE" ]; then
  args=()
  for entry in "${SOURCES[@]}"; do
    IFS=: read -r key dir version <<<"$entry"
    args+=("$key|$dir|$version|${COMMIT[$key]}|${TREE[$key]}")
  done
  outs=()
  for entry in "${OUTPUTS[@]}"; do
    IFS=: read -r key field rel <<<"$entry"
    outs+=("$key|$field|$rel|${SHA[$key:$field]}")
  done
  "$PY" - "$PROPOSE" "${#args[@]}" "${args[@]}" "${outs[@]}" <<'EOF'
import json, sys
out, n = sys.argv[1], int(sys.argv[2])
sources, outputs = sys.argv[3:3 + n], sys.argv[3 + n:]
url = {"SPIRV-Headers": "https://github.com/KhronosGroup/SPIRV-Headers.git",
       "SPIRV-Tools": "https://github.com/KhronosGroup/SPIRV-Tools.git",
       "SPIRV-Cross": "https://github.com/KhronosGroup/SPIRV-Cross.git",
       "SDL_shadercross": "https://github.com/libsdl-org/SDL_shadercross.git",
       "Vulkan-Utility-Libraries": "https://github.com/KhronosGroup/Vulkan-Utility-Libraries.git",
       "robin-hood-hashing": "https://github.com/martinus/robin-hood-hashing.git",
       "Vulkan-ValidationLayers": "https://github.com/KhronosGroup/Vulkan-ValidationLayers.git"}
pins = {}
for row in sources:
    key, d, version, commit, tree = row.split("|")
    pins[key] = {"version": version, "commit": commit, "tree": tree, "url": url[d],
                 "path": "private/s5-tools/src/shader-tools/" + d}
for row in outputs:
    key, field, rel, sha = row.split("|")
    pins[key][field] = "private/s5-tools/install-shader-tools/" + rel
    pins[key][field + "_sha256"] = sha
json.dump(pins, open(out, "w"), indent=2)
open(out, "a").write("\n")
print("PROPOSED_PINS " + out)
EOF
  echo "BUILD_OK (unpinned: --propose-pins)"
  exit 0
fi
[ "$FAIL" = 0 ] || die "the install does not match tools/pins.json. If the change is meant, move" \
  "the pins to the sha256s above in the commit that moves the install (AGENTS.md rule 8)"
echo "BUILD_OK"
