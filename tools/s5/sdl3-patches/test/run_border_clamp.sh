#!/bin/bash
# Builds and runs border_clamp.c, the behaviour check of the local SDL patch, on one Vulkan
# driver, against one SDL install. Offscreen (SDL_VIDEO_DRIVER=offscreen), no sound, implicit
# Vulkan layers off.
#
#   run_border_clamp.sh <sdl-prefix> <out-dir> <nv|lvp> [--neutral-only | --stock-detect]
#                       [--capture <vkcap-dir> [--validation <layer-prefix>]]
#
# <sdl-prefix> is the SDL install whose headers and libSDL3.so.0 the check builds and runs
# against (the patched build for the full check; a stock build for --neutral-only and
# --stock-detect, border_clamp.c's two stock modes).
# --capture loads the project's capture layer from <vkcap-dir> (the directory holding the
# vkcap.json that host/d3d9gpu/test/vkcap/build_vkcap.sh writes) and holds what the run asked of
# Vulkan to the rules check_vk_record.py states.
# --validation also loads the Khronos validation layer from <layer-prefix> (its
# share/vulkan/explicit_layer.d and lib) and fails on any "Validation Error". It needs --capture:
# the loader ignores a layer it cannot find, so the run proves each requested layer was inserted
# into every device the capture layer saw (VK_LOADER_DEBUG=layer), or it fails.
# The shaders are compiled from this directory by the pinned glslang, and the check by the pinned
# gcc (tools.gcc). The check runs in a fixed environment (env -i: HOME, PATH /usr/bin:/bin and the
# variables below), so none of the caller's SDL_*, VK_* or LD_PRELOAD reaches it. The output
# directory owns compiler/runtime temporary storage; compiler argv, logs and status stay there.
# Prints the check's JSON lines, then the library it ran on ({"sdl_library": {"prefix",
# "sha256"}}: the prefix and the sha256 of the file its libSDL3.so.0 reaches), and ends with
# BORDER_CLAMP_OK or BORDER_CLAMP_FAIL; the exit status follows it.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
usage() {
  echo "usage: run_border_clamp.sh <sdl-prefix> <out-dir> <nv|lvp> [--neutral-only | --stock-detect] [--capture <vkcap-dir> [--validation <layer-prefix>]]" >&2
  exit 2
}
[ $# -ge 3 ] || usage
SDL=$(cd "$1" && pwd -P); OUT=$2; DRV=$3; shift 3
NEUTRAL=(); LAYER=""; CAPTURE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --neutral-only|--stock-detect) NEUTRAL=("$1"); shift ;;
    --validation) [ $# -ge 2 ] || usage; LAYER=$2; shift 2 ;;
    --capture) [ $# -ge 2 ] || usage; CAPTURE=$2; shift 2 ;;
    *) echo "run_border_clamp.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
fail() { echo "BORDER_CLAMP_FAIL $*"; exit 1; }
[ -z "$LAYER" ] || [ -n "$CAPTURE" ] || fail "--validation needs --capture, which proves the layer was loaded"
[ -z "$CAPTURE" ] || [ ${#NEUTRAL[@]} -eq 0 ] || fail "--capture holds the full check's records; the stock modes draw none of them"
REPO=${SA2_REPO:-$(cd "$HERE/../../../.." && pwd -P)}
[ -f "$REPO/tools/pins.json" ] || fail "no repository at $REPO (set SA2_REPO)"
PY=$REPO/.venv/bin/python
GLSLANG=$REPO/private/s5-tools/install/bin/glslang
GCC=$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["tools"]["gcc"]["path"])' "$REPO/tools/pins.json")
case "$DRV" in
  nv) ICD=/usr/share/vulkan/icd.d/nvidia_icd.json ;;
  lvp) ICD=/usr/share/vulkan/icd.d/lvp_icd.json ;;
  *) echo "run_border_clamp.sh: driver must be nv or lvp" >&2; exit 2 ;;
esac
[ -f "$ICD" ] || fail "the $DRV ICD $ICD is absent"
[ -x "$GLSLANG" ] || fail "no pinned glslang at $GLSLANG"
[ -f "$SDL/lib/libSDL3.so.0" ] || fail "no libSDL3.so.0 under $SDL/lib"
LAYER_PATHS=(); LAYER_NAMES=(); MANIFESTS=()
if [ -n "$CAPTURE" ]; then
  CAPTURE=$(cd "$CAPTURE" && pwd -P)
  [ -f "$CAPTURE/vkcap.json" ] || fail "no capture layer manifest at $CAPTURE/vkcap.json"
  LAYER_PATHS+=("$CAPTURE"); LAYER_NAMES+=(VK_LAYER_SA2_vkcap); MANIFESTS+=("$CAPTURE/vkcap.json")
fi
if [ -n "$LAYER" ]; then
  LAYER=$(cd "$LAYER" && pwd -P)
  M=$LAYER/share/vulkan/explicit_layer.d/VkLayer_khronos_validation.json
  [ -f "$M" ] || fail "no validation layer manifest at $M"
  LAYER_PATHS+=("$LAYER/share/vulkan/explicit_layer.d"); LAYER_NAMES+=(VK_LAYER_KHRONOS_validation); MANIFESTS+=("$M")
fi
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd -P)
mkdir -p "$OUT/tmp"
export TMPDIR=$OUT/tmp
for s in quad.vert textured.frag solid.frag solid_depth.frag; do
  "$GLSLANG" -V --target-env vulkan1.0 -o "$OUT/$s.spv" "$HERE/$s" > "$OUT/$s.log"
done
COMPILE=(env -i "HOME=$HOME" PATH=/usr/bin:/bin "TMPDIR=$TMPDIR" "$GCC" -std=c11 -O1 -g -Wall -Wextra -Werror
         -Wno-missing-field-initializers -o "$OUT/border_clamp" "$HERE/border_clamp.c" -I"$SDL/include" -L"$SDL/lib" -lSDL3)
"$PY" -c 'import json, sys; json.dump(sys.argv[2:], open(sys.argv[1], "w"), indent=2)' "$OUT/compiler.argv.json" "${COMPILE[@]}"
set +e
"${COMPILE[@]}" > "$OUT/compiler.stdout" 2> "$OUT/compiler.stderr"
COMPILE_STATUS=$?
set -e
printf '%s\n' "$COMPILE_STATUS" > "$OUT/compiler.exit"
if [ "$COMPILE_STATUS" != 0 ]; then
  cat "$OUT/compiler.stdout"
  cat "$OUT/compiler.stderr" >&2
  exit "$COMPILE_STATUS"
fi
ENV=(env -i "HOME=$HOME" PATH=/usr/bin:/bin "TMPDIR=$TMPDIR" SDL_VIDEO_DRIVER=offscreen SDL_AUDIO_DRIVER=dummy SDL_NO_SIGNAL_HANDLERS=1
     "VK_DRIVER_FILES=$ICD" VK_LOADER_LAYERS_DISABLE='~implicit~' "LD_LIBRARY_PATH=$SDL/lib${LAYER:+:$LAYER/lib}")
RECORDS=$OUT/vkcap-records
if [ ${#LAYER_NAMES[@]} -gt 0 ]; then
  [ ! -e "$RECORDS" ] || fail "$RECORDS exists: the capture's accounting needs a fresh directory"
  mkdir -p "$RECORDS"
  ENV+=("VK_ADD_LAYER_PATH=$(IFS=:; echo "${LAYER_PATHS[*]}")" "VK_INSTANCE_LAYERS=$(IFS=:; echo "${LAYER_NAMES[*]}")"
        VK_LOADER_DEBUG=layer "VKCAP_OUT=$RECORDS")
fi
set +e
"${ENV[@]}" "$OUT/border_clamp" "$OUT" "${NEUTRAL[@]}" > "$OUT/result.jsonl" 2> "$OUT/stderr.txt"
STATUS=$?
set -e
cat "$OUT/result.jsonl"
echo "{\"sdl_library\": {\"prefix\": \"$SDL\", \"sha256\": \"$(sha256sum "$(realpath -e "$SDL/lib/libSDL3.so.0")" | cut -c1-64)\"}}"
if [ -n "$LAYER" ]; then
  N=$(cat "$OUT/result.jsonl" "$OUT/stderr.txt" | grep -c "Validation Error" || true)
  echo "{\"validation_layer\": \"$LAYER\", \"validation_errors\": $N}"
  [ "$N" = 0 ] || fail "validation errors"
fi
if [ -n "$CAPTURE" ]; then
  set +e
  "$PY" "$HERE/check_vk_record.py" --records "$RECORDS" --loader-log "$OUT/stderr.txt" \
    --layers "$(IFS=:; echo "${LAYER_NAMES[*]}")" "${MANIFESTS[@]/#/--manifest=}"
  CHECK=$?
  set -e
  [ "$CHECK" = 0 ] || fail "the Vulkan record check"
fi
[ "$STATUS" = 0 ] || fail "border_clamp exited $STATUS"
echo BORDER_CLAMP_OK
