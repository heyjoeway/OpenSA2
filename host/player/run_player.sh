#!/bin/bash
# run_player.sh: runs the built sa2_player under the pinned environment
# (player_env.sh). Every argument goes to the player.
#
#   bash host/player/run_player.sh STREAM --stage 3 --frames-out boot.y4m ...
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
source "$HERE/player_env.sh"
exec "${OUT:-$SA2_PLAY_OUT/bin}/sa2_player" "$@"
