#!/bin/bash
# play_stream.sh: plays one recorded stream at each player stage, the same way
# every time, keeps what each stage printed, counted and logged, and holds each
# stage to its report (tools/s5/play_report.py check), not to its exit status
# alone.
#
#   bash host/player/play_stream.sh STREAM OUTDIR [STAGES] [SNAPS] [VIDEO] [EXTRA]
#     STAGES  default "0 1 2 3"
#     SNAPS   frames to write as PNG at every stage, default "1,2,3"
#     VIDEO   an .mp4 path: stage 3 then also writes OUTDIR/frames.y4m and its
#             times, and tools/s5/frames_to_video.py turns them into VIDEO,
#             bound to stage 3's report, but only when stage 3 passed its check
#             (which encode runs again on the report, with stage 3's DXVK log)
#     EXTRA   more player options for every stage, as one word list (the
#             synthetic stream's "--allow-notes"); a recorded stream needs none
#
# Per stage N, each removed before the stage runs, so nothing an earlier run
# left can be taken for this one's: OUTDIR/stageN.txt (the player's stdout and
# stderr), OUTDIR/stageN.json (its report), OUTDIR/stageN.check.json (the
# check's verdict), OUTDIR/stageN/frame-*.png, and OUTDIR/stageN.dxvk/ (DXVK's
# own log at info: its version, the device and driver, the options it read).
# VIDEO and its receipt are removed at the start. A window opens for each
# stage. Exit 0 only when every stage and the video pass.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
STREAM=${1:?stream}
OUTDIR=${2:?output directory}
STAGES=${3:-0 1 2 3}
SNAPS=${4:-1,2,3}
VIDEO=${5:-}
read -r -a EXTRA <<< "${6:-}"
source "$HERE/player_env.sh"
BIN=${OUT:-$SA2_PLAY_OUT/bin}
TOOLS=$(cd "$HERE/../../tools/s5" && pwd)
PY=$SA2_REPO/.venv/bin/python
cleanup_output() {
  "$PY" "$SA2_REPO/tools/rmtree.py" "$@" || exit 2
}
mkdir -p "$OUTDIR"
[ -n "$VIDEO" ] && cleanup_output "$VIDEO" "$VIDEO.json"
status=0
stage3_ok=0
for stage in $STAGES; do
  cleanup_output "$OUTDIR/stage$stage" "$OUTDIR/stage$stage.dxvk"
  cleanup_output "$OUTDIR/stage$stage.txt" "$OUTDIR/stage$stage.json" "$OUTDIR/stage$stage.json.tmp" \
        "$OUTDIR/stage$stage.check.json"
  extra=()
  frames=()
  if [ "$stage" = 3 ] && [ -n "$VIDEO" ]; then
    cleanup_output "$OUTDIR/frames.y4m" "$OUTDIR/frames.y4m.times.tsv"
    extra=(--frames-out "$OUTDIR/frames.y4m")
    frames=(--frames-out "$OUTDIR/frames.y4m")
  fi
  mkdir -p "$OUTDIR/stage$stage" "$OUTDIR/stage$stage.dxvk"
  start=$(date +%s.%N)
  DXVK_LOG_PATH="$OUTDIR/stage$stage.dxvk" "$BIN/sa2_player" "$STREAM" --stage "$stage" --snap "$SNAPS" \
      --snap-dir "$OUTDIR/stage$stage" --report "$OUTDIR/stage$stage.json" "${extra[@]}" "${EXTRA[@]}" \
      > "$OUTDIR/stage$stage.txt" 2>&1
  rc=$?
  echo "== stage $stage: exit $rc, $(echo "$(date +%s.%N) - $start" | bc) s"
  grep -E "^device:|^sdl video|^records:|^entries:|^stage [0-9]:|^calls:|^host answers|^textures:|^recorder|^snap|^frames out|not_handled|failed |allowed |^verdict|failure:" \
      "$OUTDIR/stage$stage.txt" | sed 's/^/   /'
  if "$PY" "$TOOLS/play_report.py" check --report "$OUTDIR/stage$stage.json" --exit "$rc" \
       --dxvk-log "$OUTDIR/stage$stage.dxvk" --out "$OUTDIR/stage$stage.check.json" "${frames[@]}" \
       | sed 's/^/   /'; [ "${PIPESTATUS[0]}" = 0 ]; then
    [ "$stage" = 3 ] && stage3_ok=1
  else
    status=1
  fi
done
if [ -n "$VIDEO" ]; then
  if [ $stage3_ok = 1 ]; then
    "$PY" "$TOOLS/frames_to_video.py" encode --frames "$OUTDIR/frames.y4m" --report "$OUTDIR/stage3.json" \
        --dxvk-log "$OUTDIR/stage3.dxvk" --out "$VIDEO" | sed 's/^/   /'
    [ "${PIPESTATUS[0]}" = 0 ] || status=1
  else
    echo "   no video: stage 3 did not pass its check"
    status=1
  fi
fi
exit $status
