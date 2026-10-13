#!/bin/bash
# smoke.sh: r3 section 15's DXVK Native smoke test, three ways, under the
# pinned player environment (player_env.sh): SDL3's own choice of video driver,
# Xwayland (SDL_VIDEO_DRIVER=x11), and "nofpu" (no D3DCREATE_FPU_PRESERVE), which
# shows DXVK rewriting the calling thread's x87 control word.
#
#   bash host/player/smoke.sh        (after host/player/build.sh)
#
# Each run's stdout+stderr is kept in $SA2_PLAY_OUT/smoke/<way>.txt with its
# exit status and peak RSS; a window opens for about a second per run.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/player_env.sh"
BIN=${OUT:-$SA2_PLAY_OUT/bin}
S=$SA2_PLAY_OUT/smoke
mkdir -p "$S"
cd "$S" || exit 2
PY=$SA2_REPO/.venv/bin/python
run() {   # way, then the command; prints the lines that decide the check
  local way=$1; shift
  "$PY" - "$@" > "$way.txt" 2>&1 <<'EOF'
import resource, subprocess, sys
r = subprocess.run(sys.argv[1:])
print(f"exit status {r.returncode}", flush=True)
print(f"peak rss {resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024} MB", flush=True)
EOF
  echo "== $way ($*)"
  grep -E "sdl video driver|adapter 0|x87 control word|centre pixel|FAIL|exit status|peak rss|info:  (DXVK|Found config|NVIDIA|Vulkan)" "$way.txt"
}
echo "env: DXVK_WSI_DRIVER=$DXVK_WSI_DRIVER DXVK_CONFIG_FILE=$DXVK_CONFIG_FILE DXVK_LOG_PATH=$DXVK_LOG_PATH"
echo "     DXVK_SHADER_CACHE_PATH=$DXVK_SHADER_CACHE_PATH VK_LOADER_LAYERS_DISABLE=$VK_LOADER_LAYERS_DISABLE"
echo "     XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-} WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-}"
run default "$BIN/dxvk_smoke" "$SA2_DXVK_D3D9" frame-default.ppm
SDL_VIDEO_DRIVER=x11 run x11 "$BIN/dxvk_smoke" "$SA2_DXVK_D3D9" frame-x11.ppm
run nofpu "$BIN/dxvk_smoke" "$SA2_DXVK_D3D9" frame-nofpu.ppm nofpu
sha256sum frame-*.ppm
