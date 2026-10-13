#!/bin/bash
# Check A5 of the local SDL patch: the D3D12 backend, and the files it shares, compiled for
# Windows by the pinned zig (zig cc, x86_64-windows-gnu, its bundled mingw-w64 headers), stock
# and patched, with -Wall. No Windows SDK is needed and nothing runs. The patched files'
# diagnostics must be a subset of the stock files' (equal sets, normally both empty): a gap in
# zig's headers then cannot block the check, and any diagnostic the patch adds fails it. Each
# object must also hold the D3D12 sampler code, so a backend compiled out cannot pass.
#
#   d3d12_compile.sh STOCK_TREE PATCHED_TREE OUT
#
# STOCK_TREE is the extracted pinned SDL tarball; PATCHED_TREE the same with patches/sdl3 applied.
# Prints D3D12_COMPILE_OK or D3D12_COMPILE_FAIL.
set -euo pipefail
[ $# -eq 3 ] || { echo "usage: d3d12_compile.sh STOCK_TREE PATCHED_TREE OUT" >&2; exit 2; }
STOCK=$(cd "$1" && pwd -P); PATCHED=$(cd "$2" && pwd -P); OUT=$3
ZIG=${SA2_ZIG:?source tools/env.sh first: SA2_ZIG names the pinned zig}
FILES=(src/gpu/d3d12/SDL_gpu_d3d12.c src/gpu/SDL_gpu.c)
mkdir -p "$OUT"
compile() {  # tree tag file
  local base; base=$(basename "$3")
  # (zig cc 0.15.2's -fsyntax-only reports FileNotFound for the source, so the check compiles.)
  "$ZIG" cc -target x86_64-windows-gnu -std=gnu11 -Wall -O1 -I"$1/include" -I"$1/include/build_config" -I"$1/src" \
    -c "$1/$3" -o "$OUT/$2-$base.o" > "$OUT/$2-$base.raw" 2>&1 || echo "compile failed: $?" >> "$OUT/$2-$base.raw"
  # Diagnostics name their files; without the tree's own path the two sets compare line for line.
  sed "s#$1/##g" "$OUT/$2-$base.raw" > "$OUT/$2-$base.diag"
}
status=0
for f in "${FILES[@]}"; do
  compile "$STOCK" stock "$f"
  compile "$PATCHED" patched "$f"
  b=$(basename "$f")
  extra=$(comm -13 <(sort -u "$OUT/stock-$b.diag") <(sort -u "$OUT/patched-$b.diag") | sed '/^$/d' | wc -l)
  echo "{\"file\": \"$f\", \"stock_diagnostic_lines\": $(wc -l < "$OUT/stock-$b.diag"), \"patched_diagnostic_lines\": $(wc -l < "$OUT/patched-$b.diag"), \"patched_only\": $extra}"
  [ "$extra" = 0 ] || status=1
done
SYMS=$(nm "$OUT/patched-SDL_gpu_d3d12.c.o" 2>/dev/null || true)
case "$SYMS" in *D3D12_CreateSampler*) ;; *) echo "the patched D3D12 object holds no D3D12_CreateSampler: the backend was compiled out"; status=1 ;; esac
case "$(strings "$OUT/patched-SDL_gpu_d3d12.c.o")" in *SDL.gpu.sampler.create.sa2recomp.border_color*) ;;
  *) echo "the patched D3D12 object does not read the border colour property"; status=1 ;; esac
[ $status = 0 ] && echo D3D12_COMPILE_OK || echo D3D12_COMPILE_FAIL
exit $status
