#!/bin/bash
# build_before.sh: builds host/player as it stood at an earlier commit, from a clone of
# this repository, with the build record (host/player/build_record.py) that
# tools/s5/compare_hosts.py dxvk-ab holds to that commit (--before-commit): the "before"
# side of the DXVK path's A/B in host/d3d9gpu/test_d3d9gpu.sh step 7, which builds it only
# when compare_hosts.py dxvk-unchanged finds the DXVK path changed since COMMIT.
# The before player is the DXVK host: sa2_player, which a commit from before the two
# binaries built with the second host's library beside it.
#
#   bash host/d3d9gpu/build_before.sh COMMIT OUT [CLONE]
#
# CLONE (default OUT.clone) is made when absent, checked out at COMMIT, and refused when it
# is at another commit or has changes, untracked files or ignored files under host/ or in
# tools/pins.json, or when git cannot read its status.
# The clone is the commit's DXVK path throughout: the build runs with SA2_REPO the clone,
# so it reads the commit's tools/pins.json (and dxvk-ab runs the player under the clone's
# run_player.sh and player_env.sh); the clone's private and .venv are links to this
# repository's (the installation the build compiles against, SA2_S5_INSTALL, and the Python
# it runs). The record names every source by its path in the clone. A commit whose
# host/player/build.sh writes its own record is built as it is. Nothing is written in this
# checkout but OUT and CLONE.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=${SA2_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}
PY=$REPO/.venv/bin/python
I=${SA2_S5_INSTALL:-$REPO/private/s5-tools/install}
[ $# -ge 2 ] || { echo "usage: build_before.sh COMMIT OUT [CLONE]"; exit 2; }
BC=$(git -C "$REPO" rev-parse --verify --quiet "$1^{commit}") || { echo "$1 is no commit in $REPO"; exit 2; }
OUT=$2
CLONE=${3:-$OUT.clone}
if [ ! -d "$CLONE/.git" ]; then
  git clone --quiet --no-checkout "$REPO" "$CLONE"
  git -C "$CLONE" checkout --quiet --detach "$BC"
fi
[ "$(git -C "$CLONE" rev-parse HEAD)" = "$BC" ] || { echo "$CLONE is not at $BC"; exit 2; }
# untracked files too, and ignored ones: a v1 record holds only the commit's files, so an
# untracked header under host/ in a reused clone would reach the before player unseen, and an
# ignore rule (the clone's own exclude file, an untracked .gitignore) hides one from a plain
# status. A git that fails (a corrupt index, say) has listed nothing, which is no clean
# clone: it is refused like the rest (exit 2), before anything is built
st=$(git -C "$CLONE" status --porcelain --untracked-files=all --ignored -- host tools/pins.json) \
  || { echo "git cannot read the status of $CLONE"; exit 2; }
[ -z "$st" ] || { echo "$CLONE has changes, untracked or ignored files under host/ or in tools/pins.json:"; echo "$st"; exit 2; }
for link in private .venv; do
  [ -e "$CLONE/$link" ] || [ -L "$CLONE/$link" ] || ln -s "$REPO/$link" "$CLONE/$link"
  [ "$(readlink "$CLONE/$link")" = "$REPO/$link" ] || { echo "$CLONE/$link is not a link to $REPO/$link"; exit 2; }
done
mkdir -p "$OUT"
SOURCES=("$CLONE/host/player" "$CLONE/host/sa2_stream.h" "$CLONE/host/dxvk_smoke.cpp" "$CLONE/tools/pins.json")
if grep -q build_record.py "$CLONE/host/player/build.sh"; then
  SA2_REPO=$CLONE SA2_S5_INSTALL=$I OUT=$OUT bash "$CLONE/host/player/build.sh"
else
  "$PY" "$HERE/../player/build_record.py" --start --out "$OUT/sa2_player.json" --tree "$CLONE" --sources "${SOURCES[@]}"
  SA2_REPO=$CLONE SA2_S5_INSTALL=$I OUT=$OUT bash "$CLONE/host/player/build.sh"
  "$PY" "$HERE/../player/build_record.py" --out "$OUT/sa2_player.json" --tree "$CLONE" --bin-dir "$OUT" \
    --binaries sa2_player --sources "${SOURCES[@]}" --generated "$OUT/expected"
fi
echo "BEFORE_OK $BC $(sha256sum "$OUT/sa2_player" | cut -c1-64)"
