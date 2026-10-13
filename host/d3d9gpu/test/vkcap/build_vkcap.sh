#!/bin/bash
# Builds vkcap, the R1 capture layer, and its control build into OUT, each with its own manifest:
#   OUT/vkcap/vkcap.json           the layer (VK_LAYER_SA2_vkcap)
#   OUT/vkcap-control/vkcap.json   the control build, which zeroes colour write masks
# against the pinned DXVK checkout's Vulkan headers. Load one with
#   VK_ADD_LAYER_PATH=OUT/vkcap VK_INSTANCE_LAYERS=VK_LAYER_SA2_vkcap VKCAP_OUT=<dir>
#
#   host/d3d9gpu/test/vkcap/build_vkcap.sh OUT
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd -P)
REPO=${SA2_REPO:-$(cd "$HERE/../../../.." && pwd -P)}
[ $# -eq 1 ] || { echo "usage: build_vkcap.sh OUT" >&2; exit 2; }
mkdir -p "$1"
OUT=$(cd "$1" && pwd -P)
VKINC=$REPO/private/s5-tools/src/dxvk-3.1.1/include/vulkan/include
[ -f "$VKINC/vulkan/vk_layer.h" ] || { echo "no pinned Vulkan headers at $VKINC" >&2; exit 2; }
CC=${CC:-gcc}
for variant in vkcap vkcap-control; do
  mkdir -p "$OUT/$variant"
  extra=()
  [ "$variant" = vkcap-control ] && extra=(-DVKCAP_CONTROL_ZERO_WRITE_MASK)
  # no -g: the library's sha256 (the R1 receipt's capture_layer_sha256) depends on its source and
  # flags only, not on where the checkout sits
  "$CC" -std=c11 -O2 -Wall -Wextra -Werror -fPIC -shared -fvisibility=hidden -D_GNU_SOURCE "${extra[@]}" \
    -I"$VKINC" -o "$OUT/$variant/libVkLayer_sa2_vkcap.so" "$HERE/vkcap.c" -lpthread
  sed "s#@LIBRARY@#$OUT/$variant/libVkLayer_sa2_vkcap.so#" "$HERE/vkcap.json.in" > "$OUT/$variant/vkcap.json"
done
echo "VKCAP_BUILD_OK $OUT/vkcap/vkcap.json $(sha256sum "$OUT/vkcap/libVkLayer_sa2_vkcap.so" | cut -c1-64)"
