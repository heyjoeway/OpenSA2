# player_env.sh: the environment every DXVK smoke and player run pins.
# Source it (`source host/player/player_env.sh`); it only exports variables.
#
#   DXVK_WSI_DRIVER=SDL3      DXVK Native has no default WSI and throws out of
#                             Direct3DCreate9 without it (src/wsi/wsi_platform.cpp:30-38);
#                             the player checks the value before it calls DXVK
#   DXVK_CONFIG_FILE          host/player/dxvk.conf, never a stray ./dxvk.conf
#   DXVK_CONFIG (unset)       DXVK merges it over the config file (config.cpp:1751-1795)
#   DXVK_FILTER_DEVICE_* (unset)  an inherited filter would choose another adapter;
#                             DXVK's own order puts the discrete GPU first
#   SDL3_DYNAMIC_API (unset)  SDL's dynamic API would route every SDL call into the library it
#                             names, past the verified one (the player refuses it when set)
#   DXVK_LOG_PATH/LEVEL       DXVK's <exe>_d3d9.log goes under private/, not the cwd
#                             (SA2_DXVK_LOG_PATH chooses the directory; an inherited
#                             DXVK_LOG_PATH is not trusted); play_stream.sh gives each
#                             stage its own directory, at info, so every capture keeps
#                             DXVK's version, driver and options
#   DXVK_SHADER_CACHE=0       no shader cache: it was never filled here (a 0-byte file and
#                             "Failed to parse cache file header" on every run), and a cache
#                             is state carried from run to run in a directory DXVK opens
#                             exclusively, which concurrent runs would contend for
#   DXVK_SHADER_CACHE_PATH    under private/ all the same, should the cache be turned on
#   SDL_NO_SIGNAL_HANDLERS=1  SDL would turn SIGINT/SIGTERM into a quit event
#   VK_LOADER_LAYERS_DISABLE  no implicit Vulkan layer (Mesa device-select, the NVIDIA
#                             optimus layer, Steam's) in a capture, so which layers ran
#                             is fixed rather than whatever the desktop has installed
#   SA2_PLAY_OUT              the player's default --snap-dir is $SA2_PLAY_OUT/snaps
#
# SDL_VIDEO_DRIVER is left to the caller: unset, SDL3 picks its own (the smoke
# test prints which); `SDL_VIDEO_DRIVER=x11` forces Xwayland. The player's
# report records every one of these, and the Vulkan driver-selection variables.
# SA2_REPO, unless set, is the checkout this file is in, never a fixed path: a clone's runs take
# its own install and output directory.
_sa2_player_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SA2_REPO=${SA2_REPO:-$(cd "$_sa2_player_dir/../.." && pwd)}
SA2_S5_INSTALL=${SA2_S5_INSTALL:-$SA2_REPO/private/s5-tools/install}
export SA2_PLAY_OUT=${SA2_PLAY_OUT:-$SA2_REPO/private/generated/s5/play}
export DXVK_WSI_DRIVER=SDL3
export DXVK_CONFIG_FILE=$_sa2_player_dir/dxvk.conf
unset DXVK_CONFIG DXVK_FILTER_DEVICE_NAME DXVK_FILTER_DEVICE_UUID SDL3_DYNAMIC_API
export DXVK_LOG_PATH=${SA2_DXVK_LOG_PATH:-$SA2_PLAY_OUT/dxvk-log}
export DXVK_LOG_LEVEL=${DXVK_LOG_LEVEL:-info}
export DXVK_SHADER_CACHE=0
export DXVK_SHADER_CACHE_PATH=$SA2_PLAY_OUT/dxvk-cache
export SDL_NO_SIGNAL_HANDLERS=1
export VK_LOADER_LAYERS_DISABLE='~implicit~'
export SA2_DXVK_D3D9=${SA2_DXVK_D3D9:-$SA2_S5_INSTALL/lib/x86_64-linux-gnu/libdxvk_d3d9.so.0}
mkdir -p "$DXVK_LOG_PATH" "$DXVK_SHADER_CACHE_PATH"
unset _sa2_player_dir
