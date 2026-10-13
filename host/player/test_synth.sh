#!/bin/bash
# test_synth.sh: the player's end-to-end test without the game. Runs the unit
# tests, writes the synthetic stream (synth_stream.cpp), plays it at every stage
# with the pixels each stage must show (--expect, counts asserted), holds every
# run to its report (tools/s5/play_report.py), reads it --stats-only and holds
# that report to stage 3's, plays stage 3 twice and compares the frames, turns
# them into an MP4 bound to the run's report, and then runs the negative
# controls: each broken stream, environment or argument must fail with its own
# exit status and named reason, and the report check must fail with it. Every
# broken stream is also read --stats-only, which must fail it the same way: the
# same exit status, the same errors and values not handled, the same counts
# (a stats read is a replay with no device; the variants from badtex on are one
# check each that needs no device). Then the cube and volume stream, written
# here: every cube and volume creator, query and face surface the player
# replays, at every stage and --stats-only, held to M7's per-method predicate,
# with each method left out and each of its answers made wrong (see "cube and
# volume textures" below). Last, the recorder's own harness
# (tools/s5/tests/rec_harness.c, built with the pinned zig) records its two
# scenarios, which between them call ten of the twelve D3DX entries, and the
# player reads both --stats-only. Then sound: the recorder's audio harness
# (Documentation/scripts/tests/test_s5_audio.py) records a version 2 stream on the
# runtime's own DirectSound and XAudio2 devices, and the player plays it --stats-only
# and at stage 3 with --audio on SDL's dummy audio driver (nothing is heard), with
# --audio-wav each time; see "sound" below. A live interactive run's window close (the
# player's own scripted close request) is, with --end-fd, an end request the run reads on
# past to the stream's END, and without it a stop; a key held at an end request, the
# caller's SIGUSR1 or a close, is let up with a record of its own before the player answers.
# Everything it writes is under $SA2_PLAY_OUT/synth.
#
#   bash host/player/test_synth.sh          (after host/player/build.sh)
#
# A window opens for a moment per run. Exit 0 and SYNTH_OK when every check passes.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)                # this checkout
source "$HERE/player_env.sh"
BIN=${OUT:-$SA2_PLAY_OUT/bin}
T=$SA2_PLAY_OUT/synth
TOOLS=$ROOT/tools/s5                            # this checkout's tools, not the main tree's
PY=$SA2_REPO/.venv/bin/python
cleanup_output() {
  "$PY" "$SA2_REPO/tools/rmtree.py" "$@" || exit 2
}
ZIG=$SA2_REPO/private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig
mkdir -p "$T"
fail=0
bad() { echo "   FAIL: $*"; fail=1; }
# The synthetic stream has NOTE records (frame 3's and frame 5's, see
# synth_stream.cpp), which fail a play unless it is asked to allow them.
A=(--allow-notes)

"$BIN/test_texcodec" > "$T/test_texcodec.txt" 2>&1 || bad "test_texcodec"
tail -1 "$T/test_texcodec.txt"
"$BIN/test_stream" "$T" > "$T/test_stream.txt" 2>&1 || bad "test_stream"
tail -1 "$T/test_stream.txt"
"$BIN/sa2_synth_stream" "$T/synth.sa2d3d9" "$T/synth.expect.tsv" || exit 2

# An original draw checkpoint is partial evidence, never a completed replay.
"$BIN/sa2_synth_stream" "$T/checkpoint-source.sa2d3d9" "$T/checkpoint-source.expect.tsv" --variant checkpoint || exit 2
for n in 1 2 3 4 5; do
  "$BIN/sa2_player" "$T/checkpoint-source.sa2d3d9" "${A[@]}" --stats-only --stop-after-draw "1:$n" \
      --report "$T/checkpoint-source-$n.json" > "$T/checkpoint-source-$n.txt" 2>&1
  rc=$?
  "$PY" - "$T/checkpoint-source-$n.json" "$n" "$rc" <<'PY_CHECKPOINT_SOURCE' || bad "checkpoint source identity $n"
import json, sys
d=json.load(open(sys.argv[1])); n=int(sys.argv[2]); c=d['draw_checkpoint']
assert int(sys.argv[3]) == (4 if n == 5 else 0)
assert c['scope'] == 'partial-original-draw' and c['reached'] and c['source_only'] and not c['usable']
assert c['target_frame'] == 1 and c['target_draw'] == n and c['original_slot'] == [83,84,81,82,83][n-1]
assert c['bgrx_hash'] is None and c['readback'] is None
assert d['presents'] == 0 and d['records']['frame'] == 0 and d['records']['end'] == 0
assert not d['stream_records_read_to_end'] and d['stream_sha256_after_stop']
if n == 5: assert d['errors'] > 0
PY_CHECKPOINT_SOURCE
done
cleanup_output "$T/checkpoint.dxvk" "$T/checkpoint.y4m" "$T/checkpoint.y4m.times.tsv"
mkdir -p "$T/checkpoint.dxvk"
DXVK_LOG_PATH="$T/checkpoint.dxvk" "$BIN/sa2_player" "$T/synth.sa2d3d9" "${A[@]}" --stage 3 \
    --stop-after-draw 1:1 --frames-out "$T/checkpoint.y4m" --report "$T/checkpoint.json" > "$T/checkpoint.txt" 2>&1
rc=$?
"$PY" - "$T/checkpoint.json" "$T/synth.sa2d3d9" "$T/checkpoint.y4m.times.tsv" "$rc" <<'PY_CHECKPOINT_PIXELS' \
    || bad "checkpoint readback before any Present"
import hashlib, json, re, sys
d=json.load(open(sys.argv[1])); c=d['draw_checkpoint']
assert int(sys.argv[4]) == 0 and c['reached'] and c['usable'] and c['readback'] and not c['source_only']
assert c['scope'] == 'partial-original-draw' and c['target_frame'] == 1 and c['target_draw'] == 1
assert c['original_slot'] == 83 and re.fullmatch(r'0x[0-9a-f]{16}',c['bgrx_hash'])
assert d['presents'] == 0 and d['records']['frame'] == 0 and d['records']['end'] == 0
assert d['stream_sha256'] == hashlib.sha256(open(sys.argv[2],'rb').read()).hexdigest()
assert not d['stream_records_read_to_end'] and d['stream_sha256_after_stop']
assert len(open(sys.argv[3]).read().splitlines()) == 1
PY_CHECKPOINT_PIXELS
"$PY" "$TOOLS/play_report.py" check --report "$T/checkpoint.json" --exit "$rc" \
    --dxvk-log "$T/checkpoint.dxvk" --out "$T/checkpoint.completed-refusal.json" > "$T/checkpoint.completed-refusal.txt" 2>&1
[ "$?" = 1 ] || bad "draw checkpoint accepted as a completed replay"

# Each stage: its expectations, all selected and all passing, and its report.
declare -A WANT=([0]=16 [1]=29 [2]=765 [3]=774)
for stage in 0 1 2 3; do
  extra=()
  cleanup_output "$T/stage$stage.dxvk" && mkdir -p "$T/stage$stage.dxvk"
  [ "$stage" = 3 ] && extra=(--frames-out "$T/synth.y4m" --snap 1,2,6,9 --snap-dir "$T/snaps")
  DXVK_LOG_PATH="$T/stage$stage.dxvk" "$BIN/sa2_player" "$T/synth.sa2d3d9" "${A[@]}" --stage "$stage" \
      --expect "$T/synth.expect.tsv" --report "$T/stage$stage.json" "${extra[@]}" > "$T/stage$stage.txt" 2>&1
  rc=$?
  echo "== stage $stage: exit $rc; $(grep -E '^expectations' "$T/stage$stage.txt")"
  grep -E "FAIL|^entries:|^calls:|^textures:|not_handled|failed |^verdict" "$T/stage$stage.txt" | sed 's/^/   /'
  [ $rc = 0 ] || bad "stage $stage exit $rc"
  grep -q "^expectations: ${WANT[$stage]} selected, ${WANT[$stage]} pass, 0 fail$" "$T/stage$stage.txt" \
      || bad "stage $stage: want ${WANT[$stage]} expectations selected and passing"
  check=(--report "$T/stage$stage.json" --exit $rc --dxvk-log "$T/stage$stage.dxvk")
  [ "$stage" = 3 ] && check+=(--frames-out "$T/synth.y4m")
  "$PY" "$TOOLS/play_report.py" check "${check[@]}" | sed 's/^/   /'
  [ "${PIPESTATUS[0]}" = 0 ] || bad "stage $stage report check"
  # Frame 7's eight target queries (GetBackBuffer once), each answer held to
  # the bindings the player followed, at every stage.
  grep -q "^targets: 8 GetBackBuffer, GetRenderTarget and GetDepthStencilSurface answers held to the bindings the player followed (GetBackBuffer 1)$" \
      "$T/stage$stage.txt" || bad "stage $stage: want 8 target queries held to the bindings"
done
# The entries every folded recording carries: Direct3DCreate9 twice (seq 0,
# and a second IDirect3D9 that is queried and released), the constant table
# and the two Save calls, each ignored by design, by name.
"$PY" - "$T/stage3.json" <<'EOF' || bad "the D3DX entries in stage 3's report"
import json, sys
rep = json.load(open(sys.argv[1]))
rows = {(m["type"], m["method"]): m for m in rep["methods"] if "entry" in m}
want = {("d3d9", "Direct3DCreate9"): 2, ("d3dx9_42", "D3DXGetShaderConstantTable"): 1,
        ("d3dx9_42", "D3DXSaveSurfaceToFileA"): 1, ("d3dx9_42", "D3DXSaveTextureToFileA"): 1}
got = {k: rows.get(k, {}).get("ignored_by_design") for k in want}
held = rep["direct3d_create9"]
print(f"   entries ignored by design: {got}; Direct3DCreate9 {held}")
sys.exit(0 if got == want and held["calls"] == 2 and len(held["objects"]) == 2 and held["direct3d9_calls_held"]
         and rep["stream_entries_table"] else 1)
EOF

# same_as_replay STATS.json REPLAY.json: a stats read holds what a stage 3
# replay of the same stream holds, wherever no host call failed: the exit
# status, the Presents and draws, every error and value not handled, and every
# method's and entry's count in each category.
same_as_replay() {
  "$PY" - "$1" "$2" <<'EOF'
import json, sys
s, r = (json.load(open(p)) for p in sys.argv[1:3])
cats = ("handled", "ignored_by_design", "skipped_by_stage", "not_handled")
rows = lambda rep: {(m["type"], m["method"]): [m.get(c, 0) for c in cats] for m in rep["methods"]}
keys = ("exit_status", "calls", "presents", "draws", "errors", "first_errors", "not_handled_values")
differ = [k for k in keys if s.get(k) != r.get(k)] + (["methods"] if rows(s) != rows(r) else [])
print(f"exit {s['exit_status']}, {s['calls']}, presents {s['presents']}, draws {s['draws']}, errors {s['errors']}; "
      f"{len(rows(s))} methods; " + (f"differs from the replay in {differ}" if differ else "as the replay"))
sys.exit(1 if differ else 0)
EOF
}

# A stats read of the same stream: a stage 3 replay with no device, so the same
# checks, errors and counts as the replay's.
"$BIN/sa2_player" "$T/synth.sa2d3d9" "${A[@]}" --stats-only --report "$T/stats.json" > "$T/stats.txt" 2>&1
rc=$?
"$PY" "$TOOLS/play_report.py" check --report "$T/stats.json" --exit $rc | sed 's/^/   /'
[ "${PIPESTATUS[0]}" = 0 ] && [ $rc = 0 ] || bad "--stats-only on the synthetic stream: exit $rc"
line=$(same_as_replay "$T/stats.json" "$T/stage3.json") || bad "--stats-only differs from stage 3: $line"
echo "   stats only against stage 3: $line"

# A second play of the same stream must read back the same picture, frame by frame.
"$BIN/sa2_player" "$T/synth.sa2d3d9" "${A[@]}" --stage 3 --frames-out "$T/synth-again.y4m" --report "$T/again.json" \
    > "$T/again.txt" 2>&1 || bad "second play"
"$PY" "$TOOLS/frames_to_video.py" compare "$T/synth.y4m.times.tsv" "$T/synth-again.y4m.times.tsv" \
    | sed 's/^/   second play: /'
[ "${PIPESTATUS[0]}" = 0 ] || bad "second play differs"
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/synth.y4m" --report "$T/stage3.json" \
    --out "$T/synth.mp4" > "$T/video.txt" 2>&1 || bad "encode"
sed 's/^/   /' "$T/video.txt"
mapping=$("$PY" -c "
import sys; sys.path.insert(0, '$TOOLS')
import frames_to_video as f
rows = f.read_times(__import__('pathlib').Path('$T/synth.y4m.times.tsv'))
print(f.resample([r['virtual_us'] for r in rows], (60, 1)))")
echo "   frame mapping: $mapping"
# Frame 5 is three periods after frame 4 (its Present has a NOTE before its FRAME).
[ "$mapping" = "[0, 1, 2, 3, 3, 3, 4, 5, 6, 7, 8]" ] || bad "mapping"

# ------------------------------------------------------------ the SDL3 the player runs on
# The altered-source mark (patches/sdl3/README.md; SDL's zlib licence, clause 2): every run that
# verified its SDL3 says which, patched or stock, in the one line host/player/sdl_mark.h makes
# from the revision the build expects.
"$PY" - "$BIN/expected/expectations.json" "$T/stage0.txt" "$T/stage3.txt" <<'EOF' || bad "the SDL3 mark line"
import json, sys
rev = json.load(open(sys.argv[1]))["contract"]["sdl_revision"]
want = f"SDL3: {rev}: " + ("patched by sa2-recomp (patches/sdl3), not the SDL project's release"
                           if " (sa2-recomp sdl3-patches " in rev else "stock")
for path in sys.argv[2:]:
    lines = [l for l in open(path).read().splitlines() if l.startswith("SDL3: ")]
    if lines != [want]:
        print(f"   {path}: SDL3 lines {lines}, want [{want!r}]")
        sys.exit(1)
print(f"   mark: {want}")
EOF
# Isolation: an SDL3 earlier on LD_LIBRARY_PATH is never loaded, because the player takes the
# install's by DT_RPATH (host/player/build.sh). The stand-in defines SDL_Init alone, so a player
# that loaded it would not start; one that ignores it runs and verifies the install's SDL3.
SHADOW=$T/shadow-sdl3
mkdir -p "$SHADOW"
printf 'int SDL_Init(unsigned flags) { (void)flags; return 0; }\n' > "$SHADOW/shadow.c"
if gcc -shared -fPIC -Wl,-soname,libSDL3.so.0 -o "$SHADOW/libSDL3.so.0" "$SHADOW/shadow.c"; then
  mkdir -p "$T/shadow.dxvk"
  LD_LIBRARY_PATH=$SHADOW DXVK_LOG_PATH="$T/shadow.dxvk" "$BIN/sa2_player" "$T/synth.sa2d3d9" "${A[@]}" --stage 0 \
      --hidden --report "$T/shadow.json" > "$T/shadow.txt" 2>&1
  rc=$?
  "$PY" - "$T/shadow.json" "$BIN/expected/expectations.json" "$SHADOW" $rc <<'EOF' || bad "isolation from an SDL3 on LD_LIBRARY_PATH"
import json, os, sys
rc = int(sys.argv[4])
try:
    rep = json.load(open(sys.argv[1]))
except (OSError, ValueError) as e:
    print(f"   LD_LIBRARY_PATH={sys.argv[3]}: exit {rc}, no report ({e}): NOT held")
    sys.exit(1)
want = json.load(open(sys.argv[2]))["contract"]["sdl_sha256"]
f = rep["files"]["libSDL3"] or {}
real = os.path.realpath(f.get("real_path") or "")
ok = (rc == 0 and rep["host_identity"]["sdl_file_verified"] is True and f.get("sha256") == want
      and not real.startswith(os.path.realpath(sys.argv[3])))
print(f"   LD_LIBRARY_PATH={sys.argv[3]}: exit {rc}, loaded {real} ({(f.get('sha256') or '')[:16]}): "
      f"{'held' if ok else 'NOT held'}")
sys.exit(0 if ok else 1)
EOF
else
  bad "the shadow SDL3 stand-in did not build"
fi

# ------------------------------------------------------------ negative controls
# expect_fail NAME WANT_EXIT TEXT -- player args...: the run must exit
# WANT_EXIT, say TEXT, and fail the report check. Each wanted status is kept
# for the stats reads below.
declare -A NEG_EXIT=()
expect_fail() {
  local name=$1 want=$2 text=$3; shift 4
  NEG_EXIT[$name]=$want
  "$@" --report "$T/neg-$name.json" > "$T/neg-$name.txt" 2>&1
  local rc=$?
  local line
  line=$("$PY" "$TOOLS/play_report.py" check --report "$T/neg-$name.json" --exit $rc 2>&1 | head -1)
  local crc=${PIPESTATUS[0]}
  if [ $rc = "$want" ] && grep -qF -- "$text" "$T/neg-$name.txt" && [ "$crc" != 0 ]; then
    echo "   negative $name: exit $rc as wanted; says \"$text\"; ${line#check $T/}"
  else
    bad "negative $name: exit $rc (want $want), text \"$text\" $(grep -cF -- "$text" "$T/neg-$name.txt") times, check exit $crc"
  fi
}
P=("$BIN/sa2_player" "${A[@]}")
VARIANTS="cut v2 schema seqgap argc afterend noframe badrect create9 entryname entryargc d3dxfirst
          badtex noblob noendtok clearnull nolock badlevel badsurf ownupload badtarget bbrtt refused"
for v in $VARIANTS cube; do
  "$BIN/sa2_synth_stream" "$T/neg-$v.sa2d3d9" "$T/neg-$v.expect.tsv" --variant $v > /dev/null || bad "variant $v"
done
expect_fail cut 3 "no END record" -- "${P[@]}" "$T/neg-cut.sa2d3d9" --hidden
expect_fail v2 3 "stream version 2" -- "${P[@]}" "$T/neg-v2.sa2d3d9" --hidden
expect_fail schema 3 "schema \"sa2-d3d9-stream/v9\"" -- "${P[@]}" "$T/neg-schema.sa2d3d9" --hidden
expect_fail seqgap 3 "record seq 41 where seq 40 was due" -- "${P[@]}" "$T/neg-seqgap.sa2d3d9" --hidden
expect_fail argc 3 "argc 3; the header's table says 2" -- "${P[@]}" "$T/neg-argc.sa2d3d9" --hidden
expect_fail afterend 3 "after END" -- "${P[@]}" "$T/neg-afterend.sa2d3d9" --hidden
expect_fail noframe 3 "has no FRAME record" -- "${P[@]}" "$T/neg-noframe.sa2d3d9" --hidden
expect_fail badrect 4 "lock rectangle (40,0)-(8,64) is not inside level 0" -- "${P[@]}" "$T/neg-badrect.sa2d3d9" --hidden
# The cube variant's D3DXCreateCubeTexture (entry 8), once a call the player did not handle, is replayed: the
# variant plays at stage 3 with its pixels and the entry handled, and its stats read holds the same.
"${P[@]}" "$T/neg-cube.sa2d3d9" --hidden --expect "$T/neg-cube.expect.tsv" --report "$T/cube.json" > "$T/cube.txt" 2>&1
rc=$?
"${P[@]}" "$T/neg-cube.sa2d3d9" --stats-only --report "$T/cube.stats.json" > "$T/cube.stats.txt" 2>&1
src=$?
if [ $rc = 0 ] && [ $src = 0 ] && grep -q "^expectations: [1-9][0-9]* selected, [1-9][0-9]* pass, 0 fail$" "$T/cube.txt" &&
   "$PY" -c "
import json, sys
rows = [m for m in json.load(open('$T/cube.json'))['methods'] if m.get('entry') == 8]
sys.exit(0 if rows == [{'type': 'd3dx9_42', 'method': 'D3DXCreateCubeTexture', 'entry': 8, 'handled': 1}] else 1)" &&
   line=$(same_as_replay "$T/cube.stats.json" "$T/cube.json"); then
  echo "   the cube variant: exit 0, D3DXCreateCubeTexture handled 1; stats read: $line"
else
  bad "the cube variant: exit $rc and $src (want 0), $(grep '^expectations' "$T/cube.txt")"
fi
expect_fail create9 3 "is no IDirect3D9 a Direct3DCreate9 record answered" -- "${P[@]}" "$T/neg-create9.sa2d3d9" --hidden
expect_fail entryname 3 "the header's entries table gives entry 3 as D3DXFilterTextureEx" -- \
    "${P[@]}" "$T/neg-entryname.sa2d3d9" --hidden
expect_fail entryargc 3 "d3d9::Direct3DCreate9: argc 1; the header's entries table says 0" -- \
    "${P[@]}" "$T/neg-entryargc.sa2d3d9" --hidden
expect_fail d3dxfirst 4 "a d3dx call before CreateDevice" -- "${P[@]}" "$T/neg-d3dxfirst.sa2d3d9" --hidden
# One check each that needs no device (the stats reads below must make it too).
expect_fail badtex 4 "Device::SetTexture: unknown texture 0x0bad0000" -- "${P[@]}" "$T/neg-badtex.sa2d3d9" --hidden
expect_fail noblob 4 "Device::SetViewport: no IN blob for argument 0 (pViewport)" -- \
    "${P[@]}" "$T/neg-noblob.sa2d3d9" --hidden
expect_fail noendtok 4 "Device::CreateVertexShader: shader blob does not end with the END token" -- \
    "${P[@]}" "$T/neg-noendtok.sa2d3d9" --hidden
expect_fail clearnull 4 "Device::Clear: Count 1 with pRects NULL" -- "${P[@]}" "$T/neg-clearnull.sa2d3d9" --hidden
expect_fail nolock 4 "Surface::UnlockRect: UnlockRect without a LockRect" -- "${P[@]}" "$T/neg-nolock.sa2d3d9" --hidden
expect_fail badlevel 4 "Texture::LockRect: LockRect level 3 of a 1-level texture" -- \
    "${P[@]}" "$T/neg-badlevel.sa2d3d9" --hidden
expect_fail badsurf 4 "Device::GetBackBuffer: answered 0x0bad0010, a surface the player does not know" -- \
    "${P[@]}" "$T/neg-badsurf.sa2d3d9" --hidden
expect_fail ownupload 4 "an upload into a surface that is not a texture level" -- \
    "${P[@]}" "$T/neg-ownupload.sa2d3d9" --hidden
expect_fail badtarget 4 "the bindings the player followed hold" -- "${P[@]}" "$T/neg-badtarget.sa2d3d9" --hidden
expect_fail bbrtt 4 "where Direct3D 9 answers the back buffer" -- "${P[@]}" "$T/neg-bbrtt.sa2d3d9" --hidden
expect_fail refused 4 "refused (0x8876086a), but the bindings the player followed hold 0x03200020" -- \
    "${P[@]}" "$T/neg-refused.sa2d3d9" --hidden
# Thirty errors: the report lists them all, and still parses.
"$PY" -c "
import json, sys
r = json.load(open('$T/neg-d3dxfirst.json'))
sys.exit(0 if r['errors'] == 30 and len(r['first_errors']) == 30 else 1)" \
    && echo "   negative d3dxfirst: the report lists all 30 errors and is JSON" || bad "negative d3dxfirst: report"
expect_fail notes 3 "NOTE record(s), each something the writer did not record" -- \
    "$BIN/sa2_player" "$T/synth.sa2d3d9" --hidden
expect_fail noicd 4 "device creation failed" -- env VK_DRIVER_FILES=/nonexistent/icd.json \
    VK_ICD_FILENAMES=/nonexistent/icd.json "${P[@]}" "$T/synth.sa2d3d9" --hidden
expect_fail bogus 4 "DXVK_WSI_DRIVER is \"BOGUS\"" -- env DXVK_WSI_DRIVER=BOGUS "${P[@]}" "$T/synth.sa2d3d9" --hidden
# SDL's dynamic API: SDL3_DYNAMIC_API would route every SDL call into the library it names, while
# the file the player verifies and SDL_GetRevision still read as the install's. Each player
# refuses to start with it set, before any SDL call (player_env.sh unsets it); here it names the
# install's own library, so only the refusal can fail the run.
expect_fail dynapi 4 "SDL3_DYNAMIC_API is set" -- env SDL3_DYNAMIC_API="$SA2_S5_INSTALL/lib/libSDL3.so.0" \
    "${P[@]}" "$T/synth.sa2d3d9" --hidden
if [ -x "$BIN/sa2_player_gpu" ]; then
  expect_fail dynapi-gpu 4 "SDL3_DYNAMIC_API is set" -- env SDL3_DYNAMIC_API="$SA2_S5_INSTALL/lib/libSDL3.so.0" \
      "$BIN/sa2_player_gpu" "${A[@]}" "$T/synth.sa2d3d9" --hidden
else
  echo "   negative dynapi-gpu: no sa2_player_gpu in $BIN (host/d3d9gpu/build.sh has not built into it): not run"
fi
head -1 "$T/synth.expect.tsv" > "$T/neg-empty.expect.tsv"
expect_fail noexpect 1 "selects no expectation for stage 3" -- "${P[@]}" "$T/synth.sa2d3d9" --hidden \
    --expect "$T/neg-empty.expect.tsv"
# DXVK aborts the process when it finds no adapter (here, a device filter that
# matches nothing: DxvkInstance throws "Failed to initialize DXVK." inside
# Direct3DCreate9): the report written before that call is the receipt.
expect_fail abort 134 "" -- env DXVK_FILTER_DEVICE_NAME=SA2-no-such-device "${P[@]}" "$T/synth.sa2d3d9" --hidden
abort_check=$("$PY" "$TOOLS/play_report.py" check --report "$T/neg-abort.json" --exit 134)
if grep -q '"phase": "Direct3DCreate9"' "$T/neg-abort.json" && grep -q '"complete": false' "$T/neg-abort.json" &&
   grep -q "device creation aborted: signal 6 in phase Direct3DCreate9" <<< "$abort_check"; then
  echo "   negative abort: the report says phase Direct3DCreate9, incomplete; the check names the abort"
else
  bad "negative abort: no receipt of the abort"
fi

# Every broken stream read --stats-only: a replay with no device, so it fails
# each one as the stage 3 replay above did, report for report, and the check
# fails the read.
for v in $VARIANTS notes; do
  stream=$T/neg-$v.sa2d3d9
  args=("${A[@]}")
  if [ "$v" = notes ]; then stream=$T/synth.sa2d3d9; args=(); fi
  "$BIN/sa2_player" "$stream" "${args[@]}" --stats-only --report "$T/stats-$v.json" > "$T/stats-$v.txt" 2>&1
  rc=$?
  "$PY" "$TOOLS/play_report.py" check --report "$T/stats-$v.json" --exit $rc > "$T/stats-$v.check.txt" 2>&1
  crc=$?
  if line=$(same_as_replay "$T/stats-$v.json" "$T/neg-$v.json") && [ $rc = "${NEG_EXIT[$v]}" ] && [ $crc = 1 ]; then
    echo "   stats read $v: $line"
  else
    bad "stats read $v: exit $rc (the replay's ${NEG_EXIT[$v]}), check exit $crc; $line"
  fi
done
# A stats read takes stage 3 only: it is the whole replay with no device.
"${P[@]}" "$T/synth.sa2d3d9" --stats-only --stage 1 --report "$T/neg-statsstage.json" > "$T/neg-statsstage.txt" 2>&1
rc=$?
[ $rc = 2 ] && grep -q "usage: --stats-only replays at stage 3" "$T/neg-statsstage.txt" \
    && echo "   negative --stats-only --stage 1: exit 2, refused" || bad "negative --stats-only --stage 1: exit $rc"

# The check is a second layer, not a copy of the player's verdict: a report whose
# counts contradict its own "pass", an exit status the report does not give, a
# widened allow rule, failed answers that do not add up, or a rule excusing
# another method's failure must fail it.
"$PY" - "$T/stage3.json" "$T" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
t = sys.argv[2]
f = dict(r, errors=1)
json.dump(f, open(f"{t}/neg-forged.json", "w"))
f = json.loads(json.dumps(r))
f["allowed_host_failures"]["rules"]["scene order: anything goes"] = 1
f["host_failures"]["failed_like_guest"] -= 1
json.dump(f, open(f"{t}/neg-rule.json", "w"))
f = json.loads(json.dumps(r))
f["host_failures"]["failed_like_guest"] += 1
json.dump(f, open(f"{t}/neg-sum.json", "w"))
# The EndScene rule excusing a failed Clear, the sum kept whole.
rule = "scene order: EndScene with no scene open"
f = json.loads(json.dumps(r))
f["allowed_host_failures"] = {"rules": {rule: 1},
                              "calls": [f"seq 5 Device::Clear: host 0x8876086c, guest 0x00000000 ({rule})"]}
for m in f["methods"]:
    if (m["type"], m["method"]) == ("Device", "Clear"):
        m["failed"] = m.get("failed", 0) + 1
json.dump(f, open(f"{t}/neg-bind.json", "w"))
EOF
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-forged.json" --exit 0 > "$T/neg-forged.txt" 2>&1
rc=$?
[ $rc = 1 ] && grep -q "its counts say exit 4" "$T/neg-forged.txt" \
    && echo "   negative check (counts contradict the verdict): FAIL as wanted" || bad "negative check (forged counts): exit $rc"
"$PY" "$TOOLS/play_report.py" check --report "$T/stage3.json" --exit 1 > "$T/neg-exit.txt" 2>&1
rc=$?
[ $rc = 1 ] && grep -q "the report says exit 0; the process exited 1" "$T/neg-exit.txt" \
    && echo "   negative check (exit status the report does not give): FAIL as wanted" || bad "negative check (exit): exit $rc"
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-rule.json" --exit 0 > "$T/neg-rule.txt" 2>&1
rc=$?
[ $rc = 1 ] && grep -q "scene order: anything goes" "$T/neg-rule.txt" \
    && echo "   negative check (an allow rule the player does not define): FAIL as wanted" || bad "negative check (rule): exit $rc"
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-sum.json" --exit 0 > "$T/neg-sum.txt" 2>&1
rc=$?
[ $rc = 1 ] && grep -q "failed answers sum to" "$T/neg-sum.txt" \
    && echo "   negative check (failed answers that do not add up): FAIL as wanted" || bad "negative check (sum): exit $rc"
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-bind.json" --exit 0 > "$T/neg-bind.txt" 2>&1
rc=$?
[ $rc = 1 ] && grep -q "Device::EndScene failed 0 time(s)" "$T/neg-bind.txt" \
    && grep -q "is not a 0x8876086c answer to the one method its rule covers" "$T/neg-bind.txt" \
    && echo "   negative check (the EndScene rule excusing a failed Clear): FAIL as wanted" \
    || bad "negative check (a rule on another method): exit $rc"

# A changed config file or inherited overlay is refused before any SDL call,
# window or DXVK device. Empty output files contain no earlier frames.
startup_refused() {
  "$PY" - "$1" "$2" "$3" "$4" <<'EOF'
import json, sys
from pathlib import Path
report, frames, reason, verified = sys.argv[1:]
r = json.loads(Path(report).read_text())
h, life, dev = r["host_identity"], r["lifecycle"], r["device"]
ok = (r["complete"] is True and r["pass"] is False and r["exit_status"] == 4
      and dev["created"] is False and dev["failure"] == reason
      and life["finalized"] is True and life["windows_created"] == 0 and life["window_reused"] is False
      and h["sdl_file_verified"] is True and h["dxvk_file_verified"] is True
      and h["config_file_verified"] is (verified == "true") and h["sdl_version"] == 0 and h["sdl_revision"] == ""
      and r["presents"] == 0 and r["draws"] == 0 and r["methods"] == []
      and r["adapter"] == {"description": "", "driver": ""}
      and r["dxvk"]["log_bytes_read"] == 0 and r["dxvk"]["log_sha256"] is None
      and r["window"]["flags_at_present"] == [] and r["window"]["log"] == []
      and r["frames_out"]["frames"] == 0 and r["frames_out"]["y4m_bytes"] == 0
      and r["frames_out"]["times_rows"] == 0 and Path(frames).stat().st_size == 0
      and len(Path(frames + ".times.tsv").read_text().splitlines()) == 1
      and not list(Path(r["env"]["DXVK_LOG_PATH"]).glob("*_d3d9.log")))
print(f"   startup refusal {'PASS' if ok else 'FAIL'}: {reason}")
sys.exit(0 if ok else 1)
EOF
}
sed 's/^d3d9.floatEmulation = Strict$/d3d9.floatEmulation = True/' "$HERE/dxvk.conf" > "$T/neg-float.conf"
cleanup_output "$T/neg-float.dxvk" && mkdir -p "$T/neg-float.dxvk"
cleanup_output "$T/neg-float.y4m" "$T/neg-float.y4m.times.tsv" "$T/neg-float.mp4" "$T/neg-float.mp4.json"
config_reason="DXVK configuration SHA-256 differs from the build expectation"
env -u DXVK_CONFIG DXVK_CONFIG_FILE="$T/neg-float.conf" DXVK_LOG_PATH="$T/neg-float.dxvk" \
    "${P[@]}" "$T/synth.sa2d3d9" --hidden --stage 0 --frames-out "$T/neg-float.y4m" \
    --report "$T/neg-float.json" > "$T/neg-float.txt" 2>&1
rc=$?
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-float.json" --exit $rc --dxvk-log "$T/neg-float.dxvk" \
    > "$T/neg-float.check.txt" 2>&1
crc=$?
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/neg-float.y4m" --report "$T/neg-float.json" \
    --out "$T/neg-float.mp4" > "$T/neg-float-encode.txt" 2>&1
erc=$?
if [ $rc = 4 ] && [ $crc = 1 ] && [ $erc = 2 ] && [ ! -e "$T/neg-float.mp4" ] &&
   grep -qF "$config_reason" "$T/neg-float.txt" && grep -qF "$config_reason" "$T/neg-float.check.txt" &&
   startup_refused "$T/neg-float.json" "$T/neg-float.y4m" "$config_reason" false; then
  echo "   negative dxvk.conf: exit 4 before SDL/DXVK; check and encode refuse it, no video"
else
  bad "negative dxvk.conf: player exit $rc, check exit $crc, encode exit $erc"
fi
cleanup_output "$T/neg-cfgenc.dxvk" && mkdir -p "$T/neg-cfgenc.dxvk"
cleanup_output "$T/neg-cfgenc.y4m" "$T/neg-cfgenc.y4m.times.tsv" "$T/neg-cfgenc.mp4" "$T/neg-cfgenc.mp4.json"
overlay_reason="DXVK_CONFIG would override the verified configuration file"
DXVK_CONFIG="d3d9.floatEmulation = True" DXVK_CONFIG_FILE="$HERE/dxvk.conf" DXVK_LOG_PATH="$T/neg-cfgenc.dxvk" \
    "${P[@]}" "$T/synth.sa2d3d9" --hidden \
    --frames-out "$T/neg-cfgenc.y4m" --report "$T/neg-cfgenc.json" > "$T/neg-cfgenc.txt" 2>&1
rc=$?
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-cfgenc.json" --exit $rc --dxvk-log "$T/neg-cfgenc.dxvk" \
    > "$T/neg-cfgenc.check.txt" 2>&1
crc=$?
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/neg-cfgenc.y4m" --report "$T/neg-cfgenc.json" \
    --out "$T/neg-cfgenc.mp4" > "$T/neg-cfgenc-encode.txt" 2>&1
erc=$?
if [ $rc = 4 ] && [ $crc = 1 ] && [ $erc = 2 ] && [ ! -e "$T/neg-cfgenc.mp4" ] &&
   grep -qF "$overlay_reason" "$T/neg-cfgenc.txt" && grep -qF "$overlay_reason" "$T/neg-cfgenc.check.txt" &&
   startup_refused "$T/neg-cfgenc.json" "$T/neg-cfgenc.y4m" "$overlay_reason" true; then
  echo "   negative DXVK_CONFIG: exit 4 before SDL/DXVK; check and encode refuse it, no video"
else
  bad "negative DXVK_CONFIG: player exit $rc, check exit $crc, encode exit $erc"
fi

# Preserve the independent option/encoder controls with an isolated test build.
# Its unchanged production source trusts the bad config only because one copied
# generated config digest changes. SDL/DXVK expectations remain the production
# values. The report, actual DXVK log and pixels come from real runs.
CFG_BUILD=$T/bad-config-contract
cleanup_output "$CFG_BUILD"
STREAM_H_DIR=${SA2_STREAM_H_DIR:-$ROOT/host}
if "$PY" "$HERE/test_bad_config_contract.py" prepare --repo "$ROOT" --player-dir "$HERE" --bin "$BIN" \
    --stream-header "$STREAM_H_DIR/sa2_stream.h" --config "$T/neg-float.conf" --out "$CFG_BUILD"; then
  cfg_command=("${CXX:-g++}" -std=c++17 -O2 -g -Wall -Wextra -Wno-unused-parameter
      -I"$CFG_BUILD/expected" -I"$STREAM_H_DIR" -I"$SA2_S5_INSTALL/include/dxvk" -I"$SA2_S5_INSTALL/include"
      -o "$CFG_BUILD/sa2_player" "$HERE/player.cpp" "$HERE/input.cpp" "$HERE/mixer.cpp" "$HERE/stream.cpp" "$HERE/sha256.cpp" "$HERE/texcodec.cpp"
      -L"$SA2_S5_INSTALL/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$SA2_S5_INSTALL/lib" -lSDL3 -lpng -lzstd -ldl)
  printf '%q ' "${cfg_command[@]}" > "$CFG_BUILD/build.command"
  printf '\n' >> "$CFG_BUILD/build.command"
  if "${cfg_command[@]}" > "$CFG_BUILD/build.log" 2>&1 &&
     "$PY" "$HERE/test_bad_config_contract.py" verify --out "$CFG_BUILD"; then
    sha256sum "$CFG_BUILD/sa2_player" > "$CFG_BUILD/binary.sha256"
    # its SDL3 from the install alone, as host/player/build.sh holds the production players
    CFG_DYN=$(readelf -d "$CFG_BUILD/sa2_player")
    if ! grep -qF "Library rpath: [$SA2_S5_INSTALL/lib]" <<< "$CFG_DYN" || grep -q "(RUNPATH)" <<< "$CFG_DYN"; then
      bad "the isolated bad-config player does not load SDL3 from $SA2_S5_INSTALL/lib by DT_RPATH alone"
    fi
    for stage in 0 3; do
      name=neg-float-isolated
      extra=()
      if [ "$stage" = 3 ]; then
        name=neg-cfgenc-isolated
        extra=(--frames-out "$T/$name.y4m")
      fi
      cleanup_output "$T/$name.dxvk" && mkdir -p "$T/$name.dxvk"
      cleanup_output "$T/$name.y4m" "$T/$name.y4m.times.tsv" "$T/$name.mp4" "$T/$name.mp4.json"
      env -u DXVK_CONFIG DXVK_CONFIG_FILE="$T/neg-float.conf" DXVK_LOG_PATH="$T/$name.dxvk" \
          "$CFG_BUILD/sa2_player" "$T/synth.sa2d3d9" "${A[@]}" --hidden --stage "$stage" \
          --report "$T/$name.json" "${extra[@]}" > "$T/$name.txt" 2>&1
      rc=$?
      check_extra=()
      [ "$stage" = 3 ] && check_extra=(--frames-out "$T/$name.y4m")
      "$PY" "$TOOLS/play_report.py" check --report "$T/$name.json" --exit $rc --dxvk-log "$T/$name.dxvk" \
          --out "$T/$name.check.json" "${check_extra[@]}" > "$T/$name.check.txt" 2>&1
      crc=$?
      "$PY" - "$T/$name.json" "$T/$name.check.json" "$CFG_BUILD/contract.json" "$stage" "$T/stage$stage.json" <<'EOF'
import hashlib, json, sys
from pathlib import Path
r, check, contract = (json.loads(Path(p).read_text()) for p in sys.argv[1:4])
stage = int(sys.argv[4])
baseline = json.loads(Path(sys.argv[5]).read_text())
log = Path(r["dxvk"]["log"])
head = log.read_bytes()[:r["dxvk"]["log_bytes_read"]]
h = r["host_identity"]
failures = check["failures"]
ok = (r["complete"] is True and r["pass"] is True and r["exit_status"] == 0
      and r["stage"] == stage and r["device"]["created"] is True
      and r["expectations"]["file"] is None and r["expectations"]["selected"] == r["expectations"]["pass"] == r["expectations"]["fail"] == 0
      and all(r[key] == baseline[key] for key in ("stream_sha256", "records", "calls", "presents", "draws", "errors", "targets"))
      and h["sdl_file_verified"] is True and h["dxvk_file_verified"] is True and h["config_file_verified"] is True
      and h["expected_sdl_sha256"] == contract["original_contract"]["sdl_sha256"]
      and h["expected_dxvk_sha256"] == contract["original_contract"]["dxvk_sha256"]
      and h["expected_config_sha256"] == r["files"]["dxvk_conf"]["sha256"] == contract["change"]["new"]
      and r["env"]["DXVK_CONFIG"] == "" and log.name == "sa2_player_d3d9.log"
      and hashlib.sha256(head).hexdigest() == r["dxvk"]["log_sha256"]
      and check["pass"] is False and len(failures) == 2
      and failures[0].startswith("dxvk: DXVK applied ") and "d3d9.floatEmulation = True" in failures[0]
      and failures[1].startswith("dxvk: ") and " holds " in failures[1]
      and "d3d9.floatEmulation = True" in failures[1]
      and (stage != 3 or check["frames"]["rows"] == r["presents"] == r["frames_out"]["frames"] > 0))
print(f"   isolated bad config stage {stage}: {'PASS' if ok else 'FAIL'} for actual replay counts, log and independent option refusals")
sys.exit(0 if ok else 1)
EOF
      proof_rc=$?
      [ $rc = 0 ] && [ $crc = 1 ] && [ $proof_rc = 0 ] \
          || bad "isolated bad config stage $stage: player exit $rc, check exit $crc, proof exit $proof_rc"
    done
    "$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/neg-cfgenc-isolated.y4m" \
        --report "$T/neg-cfgenc-isolated.json" --out "$T/neg-cfgenc-isolated.mp4" > "$T/neg-cfgenc-isolated-encode.txt" 2>&1
    erc=$?
    if [ $erc = 2 ] && [ ! -e "$T/neg-cfgenc-isolated.mp4" ] &&
       grep -q "refused: the check: dxvk: DXVK applied .*'d3d9.floatEmulation = True'" "$T/neg-cfgenc-isolated-encode.txt"; then
      echo "   negative encode (isolated bad config): the actual run passes; encode refuses its bad options (exit 2)"
    else
      bad "negative encode (isolated bad config): encode exit $erc"
    fi
    "$PY" "$HERE/test_bad_config_contract.py" verify --out "$CFG_BUILD" || bad "isolated bad-config source identity after runs"
  else
    bad "isolated bad-config player did not build with unchanged inputs: $CFG_BUILD/build.log"
  fi
else
  bad "isolated bad-config contract preparation"
fi

# A production stage-3 baseline passes with its own log. Production stage 0
# then overwrites that same selected basename log, whose new bytes must fail
# the old report's hash. No differently named binary supplies this control.
cleanup_output "$T/neg-otherlog.dxvk" && mkdir -p "$T/neg-otherlog.dxvk"
DXVK_LOG_PATH="$T/neg-otherlog.dxvk" "${P[@]}" "$T/synth.sa2d3d9" --hidden --stage 3 \
    --report "$T/neg-otherlog.json" > "$T/neg-otherlog-baseline.txt" 2>&1
rc=$?
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-otherlog.json" --exit $rc --dxvk-log "$T/neg-otherlog.dxvk" \
    > "$T/neg-otherlog-baseline.check.txt" 2>&1
crc=$?
[ $rc = 0 ] && [ $crc = 0 ] || bad "other-log positive baseline: player exit $rc, check exit $crc"
DXVK_LOG_PATH="$T/neg-otherlog.dxvk" "${P[@]}" "$T/synth.sa2d3d9" --hidden --stage 0 \
    --report "$T/neg-otherlog-run.json" > "$T/neg-otherlog-run.txt" 2>&1
rc=$?
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-otherlog-run.json" --exit $rc --dxvk-log "$T/neg-otherlog.dxvk" \
    > "$T/neg-otherlog-run.check.txt" 2>&1
crc=$?
[ $rc = 0 ] && [ $crc = 0 ] || bad "other-log replacement run: player exit $rc, check exit $crc"
"$PY" - "$T/neg-otherlog.json" "$T/neg-otherlog-run.json" <<'EOF' || bad "other-log did not replace the selected basename log"
import hashlib, json, sys
from pathlib import Path
old, new = (json.loads(Path(p).read_text()) for p in sys.argv[1:])
pinned, fresh = old["dxvk"], new["dxvk"]
log = Path(fresh["log"])
head = log.read_bytes()[:pinned["log_bytes_read"]]
ok = (old["stage"] == 3 and new["stage"] == 0 and pinned["log"] == fresh["log"]
      and log.name == Path(new["files"]["player"]["real_path"]).name + "_d3d9.log"
      and len(list(log.parent.glob("*_d3d9.log"))) == 1
      and (len(head) != pinned["log_bytes_read"] or hashlib.sha256(head).hexdigest() != pinned["log_sha256"]))
sys.exit(0 if ok else 1)
EOF
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-otherlog.json" --exit 0 --dxvk-log "$T/neg-otherlog.dxvk" \
    > "$T/neg-otherlog.txt" 2>&1
rc=$?
[ $rc = 1 ] && grep -q "is not the log this run read" "$T/neg-otherlog.txt" \
    && echo "   negative check (another run's DXVK log in the directory): FAIL as wanted" \
    || bad "negative check (another run's DXVK log): exit $rc"
cleanup_output "$T/neg-float.y4m" "$T/neg-float.y4m.times.tsv" "$T/neg-cfgenc.y4m" "$T/neg-cfgenc.y4m.times.tsv"

# A player that dies in the replay is recorded as dying there, after the
# Presents its report counts and at most --report-every more (the report is
# rewritten after every --report-every'th Present). kill_after NAME PRESENTS
# ARGS...: SIGKILL once the report counts PRESENTS in phase replay.
kill_after() {
  local name=$1 at=$2; shift 2
  "${P[@]}" "$T/synth.sa2d3d9" --hidden --stage 3 --pace --speed 0.01 "$@" --report "$T/$name.json" \
      > "$T/$name.txt" 2>&1 &
  local pid=$!
  for _ in $(seq 1 100); do
    "$PY" -c "import json, sys; r = json.load(open('$T/$name.json'))
sys.exit(0 if r['phase'] == 'replay' and r['presents'] >= $at else 1)" 2> /dev/null && break
    sleep 0.2
  done
  kill -KILL $pid 2> /dev/null
  wait $pid 2> /dev/null
}
kill_after neg-kill 2 --report-every 1
rc=$?
kill_check=$("$PY" "$TOOLS/play_report.py" check --report "$T/neg-kill.json" --exit $rc)
said=$(grep -oE "the player died: signal 9 in phase replay after [0-9]+ to [0-9]+ presents \(the report is rewritten after every Present\)" <<< "$kill_check")
k=$(grep -oE "after [0-9]+ to" <<< "$said" | grep -oE "[0-9]+")
if [ $rc = 137 ] && [ -n "$said" ] && [ "$k" -ge 2 ] && grep -qE "after $k to $((k + 1)) presents" <<< "$said"; then
  echo "   negative kill: exit 137; $said"
else
  bad "negative kill: exit $rc; $kill_check"
fi
# With the default --report-every (100), a kill early in the replay reads 0 to 100.
kill_after neg-kill100 0
rc=$?
kill_check=$("$PY" "$TOOLS/play_report.py" check --report "$T/neg-kill100.json" --exit $rc)
if [ $rc = 137 ] && grep -qF "the player died: signal 9 in phase replay after 0 to 100 presents (the report is rewritten every 100 Presents)" <<< "$kill_check"; then
  echo "   negative kill (--report-every 100): exit 137; after 0 to 100 presents"
else
  bad "negative kill (--report-every 100): exit $rc; $kill_check"
fi

# Stopped early by choice, the run still pins the whole stream.
"${P[@]}" "$T/synth.sa2d3d9" --hidden --max-frames 3 --report "$T/max3.json" > "$T/max3.txt" 2>&1 || bad "--max-frames 3"
want_sha=$(sha256sum "$T/synth.sa2d3d9" | cut -d' ' -f1)
"$PY" -c "
import json, sys
r = json.load(open('$T/max3.json'))
sys.exit(0 if r['stream_sha256'] == '$want_sha' and r['stream_sha256_after_stop'] and r['presents'] == 3 else 1)" \
    && echo "   --max-frames 3: 3 presents; the stream pinned by the rest of the file, hashed after the stop" \
    || bad "--max-frames 3: no stream pin"

# A window's close in a live interactive run (tools/s5/play_live.py's): with --end-fd it is an
# end request, one byte for the caller, and the run reads the stream on to its END, so the
# report is final and passes; without --end-fd the close stops the run, cancelling the live
# reader, and the report is not final. The close is the player's own (its --synthetic-keys
# script's "0 0 close", pushed into SDL's queue after the first Present; no OS input), on the
# synthetic stream fed through a pipe as the relay feeds it, paced so the close comes between
# two frames.
ED=$T/end-request
cleanup_output "$ED"
mkdir -p "$ED"
printf '0 0 close\n' > "$ED/close.tsv"
for mode in end-fd no-end-fd; do
  mkfifo "$ED/$mode.keys.fifo" "$ED/$mode.cancel.fifo"
  end_args=()
  [ "$mode" = end-fd ] && end_args=(--end-fd 42)
  (
    exec 40< <(cat "$T/synth.sa2d3d9") 41<> "$ED/$mode.cancel.fifo" 42> "$ED/$mode.end.bytes"
    exec env DXVK_LOG_PATH="$ED" "$BIN/sa2_player" "${A[@]}" --interactive --video-fd 40 --cancel-fd 41 \
        "${end_args[@]}" --input-path "$ED/$mode.keys.fifo" --key-log "$ED/$mode.keys.tsv" \
        --present-log "$ED/$mode.presents.tsv" --synthetic-keys "$ED/close.tsv" --max-frames 100 --pace \
        --stage 3 --report "$ED/$mode.json"
  ) > "$ED/$mode.txt" 2>&1
  echo "$?" > "$ED/$mode.exit"
done
"$PY" - "$ED" <<'PY_END_REQUEST' || bad "the window's close as an end request (--end-fd)"
import json, sys
d = sys.argv[1]
end, plain = (json.load(open(f"{d}/{m}.json")) for m in ("end-fd", "no-end-fd"))
rc = {m: int(open(f"{d}/{m}.exit").read()) for m in ("end-fd", "no-end-fd")}
e = end["end_request"]
print(f"   --end-fd: exit {rc['end-fd']}, complete {end['complete']}, pass {end['pass']}, "
      f"end_request {e}; without: exit {rc['no-end-fd']}, complete {plain['complete']}, "
      f"stop {plain['stop'] if 'stop' in plain else plain['lifecycle']['reader_status']}")
assert rc["end-fd"] == 0 and end["complete"] is True and end["pass"] is True
assert (e["fd"], e["requested"], e["by"], e["written"], e["error"]) == (
    True, True, "the window's close", True, None)
assert open(f"{d}/end-fd.end.bytes", "rb").read() == b"\x01"
assert end["stream_records_read_to_end"] is True and end["records"]["end"] == 1
assert end["host_input"]["open"] is False and end["synthetic_keys"]["closes"] == 1
assert end["events"]["close_requests"] == 1
assert rc["no-end-fd"] != 0 and plain["complete"] is False and plain["end_request"]["fd"] is False
assert plain["end_request"]["requested"] is False
assert open(f"{d}/no-end-fd.end.bytes", "rb").read() == b""
PY_END_REQUEST

# A key held at the end request, the path the goal's own end takes (tools/s5/play_live.py's
# SIGUSR1 at the goal, a person's key still down) and a close with a key down: the press went
# out as a record of its own, and the end request lets the key up with another, written
# before the player answers on --end-fd. play_live stops the game on that answer, so a record
# written later is one the game never reads, which the play clause refuses (every record the
# player wrote is read), and a key left down. A reader stands in for the runtime's FIFO and
# for play_live's end: it reads the records, and once the answer comes it takes what the FIFO
# then holds and stops, as the game does at its last poll. The stream is fed in two parts
# 1.5 s apart, as a live game's comes, so the end request falls inside the run, and the player
# reads on to the stream's END. The signal is sent once the reader has the press: to the
# player alone, this script's own child.
KH=$T/key-held
cleanup_output "$KH"
mkdir -p "$KH"
printf '0 13 down\n' > "$KH/signal.script.tsv"            # J (SDL scancode 13), never let up
printf '0 13 down\n400 0 close\n' > "$KH/close.script.tsv"
for mode in signal close; do
  mkfifo "$KH/$mode.keys.fifo" "$KH/$mode.cancel.fifo" "$KH/$mode.end.fifo"
  "$PY" - "$KH/$mode.keys.fifo" "$KH/$mode.end.fifo" "$KH/$mode" <<'PY_KEY_READER' &
import json, os, struct, sys, time
keys_path, end_path, out = sys.argv[1:4]
keys = os.open(keys_path, os.O_RDONLY | os.O_NONBLOCK)
end = os.open(end_path, os.O_RDONLY | os.O_NONBLOCK)
state = {"data": b"", "records": []}

def take():
    """Every whole record the FIFO holds now."""
    while True:
        try:
            got = os.read(keys, 4096)
        except BlockingIOError:              # a writer, and nothing more written
            return
        if not got:                          # no writer: none yet, or the sender closed
            return
        state["data"] += got
        while len(state["data"]) >= 264:
            record, state["data"] = state["data"][:264], state["data"][264:]
            magic, seq = struct.unpack_from("<II", record)
            state["records"].append({"magic": hex(magic), "seq": seq,
                                     "held": [hex(i) for i, v in enumerate(record[8:]) if v]})
            if len(state["records"]) == 1:
                open(out + ".first", "w").close()

answer, deadline = b"", time.monotonic() + 60
while time.monotonic() < deadline:
    take()
    try:
        answer = os.read(end, 16)
    except BlockingIOError:
        answer = b""
    if answer:
        take()                               # the FIFO as the game's last poll finds it
        break
    time.sleep(0.005)
json.dump({"records": state["records"], "left": len(state["data"]), "answer": answer.hex()},
          open(out + ".records.json", "w"))
PY_KEY_READER
  reader=$!
  (
    exec 40< <("$PY" -c 'import sys, time
d = open(sys.argv[1], "rb").read(); o = sys.stdout.buffer
o.write(d[:len(d) // 2]); o.flush(); time.sleep(1.5); o.write(d[len(d) // 2:]); o.flush()' \
        "$T/synth.sa2d3d9") 41<> "$KH/$mode.cancel.fifo" 42> "$KH/$mode.end.fifo"
    exec env DXVK_LOG_PATH="$KH" "$BIN/sa2_player" "${A[@]}" --interactive --video-fd 40 --cancel-fd 41 \
        --end-fd 42 --input-path "$KH/$mode.keys.fifo" --key-log "$KH/$mode.keys.tsv" \
        --present-log "$KH/$mode.presents.tsv" --synthetic-keys "$KH/$mode.script.tsv" --max-frames 100 \
        --pace --stage 3 --report "$KH/$mode.json"
  ) > "$KH/$mode.txt" 2>&1 &
  player=$!
  if [ "$mode" = signal ]; then
    for _ in $(seq 400); do [ -e "$KH/$mode.first" ] && break; sleep 0.025; done
    kill -USR1 "$player"
  fi
  wait "$player"
  echo "$?" > "$KH/$mode.exit"
  wait "$reader"
done
"$PY" - "$KH" <<'PY_KEY_HELD' || bad "a key held at the end request (--end-fd)"
import json, sys
d = sys.argv[1]
for mode, by in (("signal", "the caller's request"), ("close", "the window's close")):
    r = json.load(open(f"{d}/{mode}.json"))
    rc = int(open(f"{d}/{mode}.exit").read())
    got = json.load(open(f"{d}/{mode}.records.json"))
    rows = [line.split("\t") for line in open(f"{d}/{mode}.keys.tsv").read().splitlines()[1:]]
    h, e = r["host_input"], r["end_request"]
    print(f"   key held at {by}: exit {rc}, complete {r['complete']}, pass {r['pass']}, "
          f"records written {h['records_written']}, read by the answer "
          f"{[x['held'] for x in got['records']]}, held after {h['held_now']}, answer "
          f"{got['answer']}")
    assert rc == 0 and r["complete"] is True and r["pass"] is True, mode
    assert (e["requested"], e["by"], e["written"], e["error"]) == (True, by, True, None), mode
    assert r["stream_records_read_to_end"] is True and r["records"]["end"] == 1, mode
    assert (h["records_written"], h["last_seq"], h["held_now"], h["owed"], h["open"],
            h["write_errors"]) == (2, 1, 0, False, False, 0), (mode, h)
    # What the game's FIFO held by the answer: the press (J, DIK 0x24), then every key up.
    assert got == {"records": [{"magic": "0x4b324153", "seq": 0, "held": ["0x24"]},
                               {"magic": "0x4b324153", "seq": 1, "held": []}],
                   "left": 0, "answer": "01"}, (mode, got)
    # The sender's log: the press and the end request's release, each sent as its record.
    assert [(row[2], row[3], row[7], row[9]) for row in rows] == [
        ("36", "1", "0", "sent"), ("36", "0", "1", "sent")], (mode, rows)
    assert h["key_log"]["rows"] == 2 and h["key_log"]["sent"] == 2, mode
PY_KEY_HELD

# A stream cut short plays without failing only when asked to.
"${P[@]}" "$T/neg-cut.sa2d3d9" --hidden --allow-truncated --report "$T/neg-cut-allowed.json" > "$T/neg-cut-allowed.txt" 2>&1
rc=$?
"$PY" "$TOOLS/play_report.py" check --report "$T/neg-cut-allowed.json" --exit $rc > /dev/null \
    && [ $rc = 0 ] && echo "   --allow-truncated: the cut stream plays, exit 0, check PASS" \
    || bad "--allow-truncated: exit $rc"
# An output path inside the working tree but outside private/ is refused before
# anything is written, and the report (its own path allowed) says so.
"${P[@]}" "$T/synth.sa2d3d9" --hidden --snap 2 --snap-dir "$HERE/snaps-must-not-exist" --report "$T/neg-path.json" \
    > "$T/neg-path.txt" 2>&1
rc=$?
if [ $rc = 2 ] && grep -q "^refused: " "$T/neg-path.txt" && [ ! -e "$HERE/snaps-must-not-exist" ] &&
   grep -q '"exit_status": 2' "$T/neg-path.json" && grep -q 'i/o: refused: ' "$T/neg-path.json"; then
  echo "   negative path: exit 2, refused, nothing written but the report, which says so"
else
  bad "negative path: exit $rc"
fi
"${P[@]}" "$T/synth.sa2d3d9" --hidden --report "$HERE/report-must-not-exist.json" > "$T/neg-reportpath.txt" 2>&1
rc=$?
if [ $rc = 2 ] && grep -q "^refused: .*no report is written" "$T/neg-reportpath.txt" &&
   [ ! -e "$HERE/report-must-not-exist.json" ] && [ ! -e "$HERE/report-must-not-exist.json.tmp" ]; then
  echo "   negative report path: exit 2, refused, nothing written"
else
  bad "negative report path: exit $rc"
fi
# A run refused at open leaves no earlier frames behind, and encode refuses them.
cp "$T/synth.y4m" "$T/neg-stale.y4m" && cp "$T/synth.y4m.times.tsv" "$T/neg-stale.y4m.times.tsv"
"${P[@]}" "$T/neg-v2.sa2d3d9" --hidden --frames-out "$T/neg-stale.y4m" --report "$T/neg-stale.json" > "$T/neg-stale.txt" 2>&1
rc=$?
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/neg-stale.y4m" --report "$T/neg-stale.json" \
    --out "$T/neg-stale.mp4" > "$T/neg-stale-encode.txt" 2>&1
erc=$?
if [ $rc = 3 ] && [ ! -s "$T/neg-stale.y4m" ] && [ "$(wc -l < "$T/neg-stale.y4m.times.tsv")" = 1 ] && [ $erc = 2 ] &&
   [ ! -e "$T/neg-stale.mp4" ]; then
  echo "   negative stale frames: the refused run emptied them (exit 3); encode refuses them (exit 2)"
else
  bad "negative stale frames: player exit $rc, encode exit $erc"
fi
# encode refuses frames that are not the report's run's, or whose count differs
# from their times, and any output inside the working tree.
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/synth-again.y4m" --report "$T/stage3.json" \
    --out "$T/neg-encode.mp4" > "$T/neg-encode.txt" 2>&1
rc=$?
[ $rc = 2 ] && grep -q "refused:" "$T/neg-encode.txt" && echo "   negative encode (another run's report): exit 2, refused" \
    || bad "negative encode (another run's report): exit $rc"
head -c -100 "$T/synth.y4m" > "$T/neg-short.y4m" && cp "$T/synth.y4m.times.tsv" "$T/neg-short.y4m.times.tsv"
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/neg-short.y4m" --report "$T/stage3.json" \
    --out "$T/neg-short.mp4" > "$T/neg-short.txt" 2>&1
rc=$?
[ $rc = 2 ] && grep -q "not a whole frame" "$T/neg-short.txt" && echo "   negative encode (a frame cut short): exit 2, refused" \
    || bad "negative encode (a frame cut short): exit $rc"
"$PY" "$TOOLS/frames_to_video.py" encode --frames "$T/synth.y4m" --report "$T/stage3.json" \
    --out "$HERE/synth-in-tree.mp4" > "$T/neg-intree.txt" 2>&1
rc=$?
if [ $rc = 2 ] && grep -q "refused: .*outside its private/" "$T/neg-intree.txt" && [ ! -e "$HERE/synth-in-tree.mp4" ] &&
   [ ! -e "$HERE/synth-in-tree.mp4.resampled.y4m" ]; then
  echo "   negative encode (an output inside the working tree): exit 2, refused, nothing written"
else
  bad "negative encode (in the tree): exit $rc"
fi
cleanup_output "$T/neg-short.y4m" "$T/neg-short.y4m.times.tsv" "$T/neg-stale.y4m" "$T/neg-stale.y4m.times.tsv" "$T/synth-again.y4m"
# play_stream.sh after a passing run: a failing run must fail and leave neither the old frames nor a video.
PS=$T/ps
cleanup_output "$PS"
bash "$HERE/play_stream.sh" "$T/synth.sa2d3d9" "$PS" "3" "2" "$PS/v.mp4" "--allow-notes" > "$T/ps-a.txt" 2>&1 \
    || bad "play_stream.sh on the synthetic stream"
[ -s "$PS/v.mp4" ] || bad "play_stream.sh made no video"
VK_DRIVER_FILES=/nonexistent/icd.json VK_ICD_FILENAMES=/nonexistent/icd.json \
    bash "$HERE/play_stream.sh" "$T/synth.sa2d3d9" "$PS" "3" "2" "$PS/v.mp4" "--allow-notes" > "$T/ps-b.txt" 2>&1
rc=$?
if [ $rc != 0 ] && [ ! -e "$PS/v.mp4" ] && [ ! -s "$PS/frames.y4m" ] && [ ! -e "$PS/stage3/frame-00002.png" ]; then
  echo "   negative play_stream.sh (no Vulkan driver after a good run): exit $rc, no video, no stale frames"
else
  bad "negative play_stream.sh: exit $rc; video $(ls "$PS" 2>/dev/null | tr '\n' ' ')"
fi

# ------------------------------------------------------------ cube and volume textures
# The cube and volume stream, written here: a 64x64 device, IDirect3DDevice9::CreateCubeTexture and the four D3DX
# cube and volume creators (two DDS cube maps, two DDS volumes, one of each with its mips built, and a render-target
# cube as the game's creator makes one), every GetLevelCount, GetLevelDesc and GetCubeMapSurface on what they made,
# with Direct3D 9's answers (runtime/sa2_d3d9.c's, a cube level described as a surface), refusals past the count
# among them, a face filled through its surface's lock, another made the render target and cleared, and one frame:
# a cube and a volume bound and unbound, a green clear and a red quad. Played at every stage with its pixels, and read --stats-only, which must hold what stage 3 holds.
# cubevol_check holds a report to the stream's ten methods as M7's predicate does (each call counted, none not
# handled, skipped, failed or differing, no error, exit 0); a stream with one method's calls left out (a creator's
# with every call on what it made) still plays, and the check fails on that method. Each stream that answers one
# call wrongly (a level count, a field of a level's description, a face surface, a creation, a DESC row, the DDS
# itself), and a draw while a cube or a volume is bound, must fail the replay and the stats read alike, by name.
CV=$T/cubevol
cleanup_output "$CV" && mkdir -p "$CV"
"$PY" - "$CV" <<'PY_CUBEVOL_STREAM' || bad "the cube and volume stream"
import json, struct, sys
from pathlib import Path

# The cube and volume stream: every cube and volume creator, query and face surface the player
# replays, in the recorder's format (host/sa2_stream.h), with the answers the recording device
# gives (runtime/sa2_d3d9.c), one frame of a clear and a quad, and its variants. Writes OUT/<name>.sa2d3d9,
# OUT/cubevol.expect.tsv and OUT/cubevol.json (each stream's targets, wants and reasons).
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
CALL, D3DX, FRAME, END = 1, 2, 3, 5
IN, OUTB, LOCKED, FILE, DESC, LOCK = 1, 2, 3, 4, 5, 6
NOARG, DEFAULT = 0xFFFF, 0xFFFFFFFF
T_D3D9, T_DEV, T_CUBE, T_VOL, T_SURF = 0, 1, 3, 4, 5
A8R8G8B8, DXT1 = 21, 0x31545844
BASE, STRIDE = 0x03200000, 16
D3D9, DEV, RT, DS = (BASE + STRIDE * i for i in range(4))
u32 = lambda *v: struct.pack(f"<{len(v)}I", *v)
METHODS = [(0, 16, "CreateDevice", 6), (1, 17, "Present", 4), (1, 41, "BeginScene", 0), (1, 42, "EndScene", 0),
           (1, 43, "Clear", 6), (1, 25, "CreateCubeTexture", 7), (1, 37, "SetRenderTarget", 2),
           (1, 57, "SetRenderState", 2),
           (1, 65, "SetTexture", 2), (1, 67, "SetTextureStageState", 3), (1, 89, "SetFVF", 1),
           (1, 83, "DrawPrimitiveUP", 4), (3, 13, "GetLevelCount", 0), (3, 17, "GetLevelDesc", 2),
           (3, 18, "GetCubeMapSurface", 3), (4, 13, "GetLevelCount", 0), (4, 17, "GetLevelDesc", 2),
           (5, 13, "LockRect", 3), (5, 14, "UnlockRect", 0)]
ENTRIES = [(1, "D3DXCreateTextureFromFileInMemoryEx", 15, "file 1 2; out 14 4; out 12 28; out 13 1024; file_desc 14"),
           (2, "D3DXCreateTexture", 8, "out 7 4; new_tex 7 1 2 5"), (3, "D3DXFilterTexture", 4, "in 1 1024"),
           (4, "D3DXLoadSurfaceFromSurface", 8, "in 1 1024; in 2 16; in 4 1024; in 5 16"),
           (5, "D3DXGetShaderConstantTable", 2, "shader 0; out 1 4"),
           (6, "D3DXCreateCubeTextureFromFileInMemoryEx", 14, "file 1 2; out 13 4; out 11 28; out 12 1024; file_desc 13"),
           (7, "D3DXCreateVolumeTextureFromFileInMemoryEx", 16,
            "file 1 2; out 15 4; out 13 28; out 14 1024; file_desc 15"),
           (8, "D3DXCreateCubeTexture", 7, "out 6 4; new_cube 6 1 4"),
           (9, "D3DXCreateVolumeTexture", 9, "out 8 4; new_vol 8 1 2 6"),
           (10, "D3DXSaveSurfaceToFileA", 5, "-"), (11, "D3DXSaveTextureToFileA", 4, "-"),
           (12, "Direct3DCreate9", 0, "object_eax")]
INTERFACES = ["IDirect3D9", "Device", "Texture", "CubeTexture", "VolumeTexture", "Surface", "Volume", "VertexBuffer",
              "IndexBuffer", "VertexDeclaration", "VertexShader", "PixelShader", "StateBlock", "SwapChain", "Query",
              "ID3DXConstantTable"]
# The ten methods this stream holds, by the report's (type, method).
TARGETS = {"IDirect3DDevice9::CreateCubeTexture": ("Device", "CreateCubeTexture"),
           "IDirect3DCubeTexture9::GetLevelCount": ("CubeTexture", "GetLevelCount"),
           "IDirect3DCubeTexture9::GetLevelDesc": ("CubeTexture", "GetLevelDesc"),
           "IDirect3DCubeTexture9::GetCubeMapSurface": ("CubeTexture", "GetCubeMapSurface"),
           "IDirect3DVolumeTexture9::GetLevelCount": ("VolumeTexture", "GetLevelCount"),
           "IDirect3DVolumeTexture9::GetLevelDesc": ("VolumeTexture", "GetLevelDesc"),
           "d3dx9_42!D3DXCreateCubeTexture": ("d3dx9_42", "D3DXCreateCubeTexture"),
           "d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx": ("d3dx9_42", "D3DXCreateCubeTextureFromFileInMemoryEx"),
           "d3dx9_42!D3DXCreateVolumeTexture": ("d3dx9_42", "D3DXCreateVolumeTexture"),
           "d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx": ("d3dx9_42",
                                                                  "D3DXCreateVolumeTextureFromFileInMemoryEx")}


def chain(n):
    k = 1
    while n > 1:
        n //= 2
        k += 1
    return k


def dim(d, lv):
    return max(1, d >> lv)


def level_bytes(w, h, fmt):
    return ((w + 3) // 4) * ((h + 3) // 4) * 8 if fmt == DXT1 else w * h * 4


def solid(w, h, fmt, argb):
    if fmt == DXT1:                     # one 565 colour in both endpoints, every index 0
        a, r, g, b = argb >> 24, (argb >> 16) & 255, (argb >> 8) & 255, argb & 255
        c = (r >> 3) << 11 | (g >> 2) << 5 | b >> 3
        return struct.pack("<HHI", c, c, 0) * (((w + 3) // 4) * ((h + 3) // 4))
    return u32(argb) * (w * h)


def dds(w, h, fmt, payload, caps2, depth=0, mips=1):
    d = [0] * 32
    d[0], d[1] = 0x20534444, 124
    d[2] = 0x1007 | (0x20000 if mips > 1 else 0) | (0x800000 if depth else 0)
    d[3], d[4], d[6], d[7] = h, w, depth, mips if mips > 1 else 0
    d[19] = 32
    if fmt == DXT1:
        d[20], d[21] = 0x4, DXT1
    else:
        d[20], d[22], d[23], d[24], d[25], d[26] = 0x41, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000
    d[27], d[28] = 0x1000 | (0x400008 if mips > 1 else 0), caps2
    return u32(*d) + payload


class Stream:
    """Records with tags; a variant drops or edits them before writing."""

    def __init__(self):
        self.recs, self.next_obj, self.heap = [], 4, 0x04100000

    def obj(self):
        a = BASE + STRIDE * self.next_obj
        self.next_obj += 1
        return a

    def ptr(self, n=16):
        p = self.heap
        self.heap += (n + 15) & ~15
        return p

    def add(self, kind, tags=(), **f):
        self.recs.append(dict(kind=kind, tags=set(tags), **f))
        return self.recs[-1]

    def call(self, type_, slot, self_, args, eax=0, blobs=(), tags=()):
        return self.add(CALL, tags, type=type_, slot=slot, this=self_, args=list(args), eax=eax, blobs=list(blobs))

    def d3dx(self, entry, args, eax=0, blobs=(), tags=()):
        return self.add(D3DX, tags, entry=entry, args=list(args), eax=eax, blobs=list(blobs))

    def write(self, path, recs, entries=ENTRIES):
        hdr = json.dumps({"schema": "sa2-d3d9-stream/v1", "writer": "host/player/test_synth.sh (the cube and volume "
                          "stream)", "meta": {"build": "synthetic", "note": "a test stream; no game data"},
                          "object_base": BASE, "object_stride": STRIDE, "interfaces": INTERFACES,
                          "methods": [list(m) for m in METHODS], "entries": [list(e) for e in entries]})
        while (16 + len(hdr)) % 4:
            hdr += " "
        body = bytearray(b"SA2D3D9S" + u32(1, 16 + len(hdr)) + hdr.encode())
        for seq, r in enumerate(recs):
            if r["kind"] in (CALL, D3DX):
                p = (struct.pack("<HHII", r["type"], r["slot"], r["this"], 0) if r["kind"] == CALL
                     else u32(r["entry"]))
                p += u32(len(r["args"]), *r["args"]) + u32(r["eax"], len(r["blobs"]))
                for role, arg, data in r["blobs"]:
                    p += struct.pack("<HHI", role, arg, len(data)) + data + b"\0" * (-len(data) % 4)
            elif r["kind"] == FRAME:
                p = struct.pack("<QII", r["us"], r["frames"], 0)
            else:
                p = b""
            body += u32(r["kind"], 12 + len(p), seq) + p
        Path(path).write_bytes(bytes(body))


s = Stream()
s.d3dx(12, [], D3D9)                                                        # Direct3DCreate9
pp = u32(64, 64, A8R8G8B8, 1, 0, 0, 1, 0, 1, 1, 75, 0, 0, 0)
s.call(T_D3D9, 16, D3D9, [0, 1, 0x7E000000, 0x40, s.ptr(56), s.ptr(4)], 0,
       [(IN, 4, pp), (OUTB, 5, u32(DEV)), (DESC, NOARG, u32(DEV, RT, DS))])
objs = {}


def surfdesc(fmt, w, h):                 # D3DSURFACE_DESC, Type D3DRTYPE_SURFACE (a cube level is a surface)
    return u32(fmt, 1, 0, 1, 0, 0, w, h)


def voldesc(fmt, w, h, d):
    return u32(fmt, 2, 0, 1, w, h, d)


def file_desc(obj, kind, w, h, d, levels, fmt, fw, fh, fd, flevels, ffmt):
    return u32(obj, 1, kind, w, h, d, levels, fmt, fw, fh, fd, flevels, ffmt, 4 if flevels <= 1 else 1, 1, 1,
               0, 0, 0, 0, 0, level_bytes(w, h, fmt))


# A: IDirect3DDevice9::CreateCubeTexture(8, 0, 0, A8R8G8B8, MANAGED): the complete chain, 4 levels. Its face
# -Y level 1 (4x4) is filled through the surface GetCubeMapSurface answers.
A = s.obj()
c = "IDirect3DDevice9::CreateCubeTexture"
s.call(T_DEV, 25, DEV, [8, 0, 0, A8R8G8B8, 1, s.ptr(), 0], 0, [(OUTB, 5, u32(A))], tags=["A", c, "creator"])
s.call(T_CUBE, 13, A, [], 4, tags=["A", "IDirect3DCubeTexture9::GetLevelCount", "A.lc"])
dp = s.ptr(32)
s.call(T_CUBE, 17, A, [2, dp], 0, [(OUTB, 1, surfdesc(A8R8G8B8, 2, 2))],
       tags=["A", "IDirect3DCubeTexture9::GetLevelDesc"])
SA = s.obj()
s.call(T_CUBE, 18, A, [3, 1, s.ptr()], 0, [(OUTB, 2, u32(SA))],
       tags=["A", "IDirect3DCubeTexture9::GetCubeMapSurface", "A.cms"])
bits = 0x04800000
s.call(T_SURF, 13, SA, [s.ptr(8), 0, 0], 0, [(OUTB, 0, u32(16, bits))],
       tags=["A", "IDirect3DCubeTexture9::GetCubeMapSurface", "SA"])
face = solid(4, 4, A8R8G8B8, 0xFF102030)
lock = u32(SA, A, 1, bits, 16, 4, len(face), A8R8G8B8, 4, 4, 0, 0, 0, 4, 4, 0, 0)
s.call(T_SURF, 14, SA, [], 0, [(LOCK, NOARG, lock), (LOCKED, NOARG, face)],
       tags=["A", "IDirect3DCubeTexture9::GetCubeMapSurface", "SA"])
s.call(T_CUBE, 18, A, [3, 1, s.ptr()], 0, [(OUTB, 2, u32(SA))],
       tags=["A", "IDirect3DCubeTexture9::GetCubeMapSurface"])
s.call(T_CUBE, 18, A, [6, 0, s.ptr()], 0x8876086C, [(OUTB, 2, u32(0))],
       tags=["A", "IDirect3DCubeTexture9::GetCubeMapSurface", "A.refused"])
# F: a render-target cube as the game's creator makes one (16, one level, RENDERTARGET, DEFAULT); its +X face
# made the render target and cleared (blue, which no pixel shows), then the back buffer bound again.
F = s.obj()
s.call(T_DEV, 25, DEV, [16, 1, 1, A8R8G8B8, 0, s.ptr(), 0], 0, [(OUTB, 5, u32(F))], tags=["F", c, "creator"])
s.call(T_CUBE, 13, F, [], 1, tags=["F", "IDirect3DCubeTexture9::GetLevelCount"])
s.call(T_CUBE, 17, F, [0, s.ptr(32)], 0, [(OUTB, 1, u32(A8R8G8B8, 1, 1, 0, 0, 0, 16, 16))],
       tags=["F", "IDirect3DCubeTexture9::GetLevelDesc"])
SF = s.obj()
s.call(T_CUBE, 18, F, [0, 0, s.ptr()], 0, [(OUTB, 2, u32(SF))], tags=["F", "IDirect3DCubeTexture9::GetCubeMapSurface"])
s.call(T_DEV, 37, DEV, [0, SF], tags=["F", "IDirect3DCubeTexture9::GetCubeMapSurface", "SF"])
s.call(T_DEV, 43, DEV, [0, 0, 1, 0xFF0000FF, 0x3F800000, 0], tags=["F", "IDirect3DCubeTexture9::GetCubeMapSurface", "SF"])
s.call(T_DEV, 37, DEV, [0, RT], tags=["F", "IDirect3DCubeTexture9::GetCubeMapSurface", "SF"])
objs["A"] = A
# B: D3DXCreateCubeTexture(16, 0, 0, DXT1, MANAGED): 5 levels.
B = s.obj()
s.d3dx(8, [DEV, 16, 0, 0, DXT1, 1, s.ptr()], 0, [(OUTB, 6, u32(B))],
       tags=["B", "d3dx9_42!D3DXCreateCubeTexture", "creator"])
s.call(T_CUBE, 13, B, [], 5, tags=["B", "IDirect3DCubeTexture9::GetLevelCount"])
s.call(T_CUBE, 17, B, [4, s.ptr(32)], 0, [(OUTB, 1, surfdesc(DXT1, 1, 1))],
       tags=["B", "IDirect3DCubeTexture9::GetLevelDesc"])
SB = s.obj()
s.call(T_CUBE, 18, B, [0, 0, s.ptr()], 0, [(OUTB, 2, u32(SB))],
       tags=["B", "IDirect3DCubeTexture9::GetCubeMapSurface", "B.cms"])
# C: a DDS cube map, 4x4 A8R8G8B8, one level in the file, MipLevels D3DX_DEFAULT: 3 levels, two built per face.
CUBE_COLOURS = [0xFFFF0000, 0xFF00FF00, 0xFF0000FF, 0xFFFFFF00, 0xFF00FFFF, 0xFFFF00FF]
f = "d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx"


def cube_load(s, tag, file, levels_arg, desc):
    """desc: the recording device's row, (w, h, depth, levels, format, file w, h, depth, levels, format)."""
    o = s.obj()
    src, info = s.ptr(len(file)), s.ptr(28)
    s.d3dx(6, [DEV, src, len(file), DEFAULT, levels_arg, 0, DEFAULT, 1, DEFAULT, DEFAULT, 0, info, 0, s.ptr()], 0,
           [(FILE, 1, file), (OUTB, 13, u32(o)), (OUTB, 11, u32(*desc[5:], 5, 4)),
            (DESC, NOARG, file_desc(o, 1, *desc))], tags=[tag, f, "creator"])
    return o


C_FILE = dds(4, 4, A8R8G8B8, b"".join(solid(4, 4, A8R8G8B8, c) for c in CUBE_COLOURS), 0xFE00)
C = cube_load(s, "C", C_FILE, DEFAULT, (4, 4, 1, 3, A8R8G8B8, 4, 4, 1, 1, A8R8G8B8))
s.call(T_CUBE, 13, C, [], 3, tags=["C", "IDirect3DCubeTexture9::GetLevelCount", "C.lc"])
s.call(T_CUBE, 17, C, [0, s.ptr(32)], 0, [(OUTB, 1, surfdesc(A8R8G8B8, 4, 4))],
       tags=["C", "IDirect3DCubeTexture9::GetLevelDesc", "C.ld"])
s.call(T_CUBE, 17, C, [3, s.ptr(32)], 0x8876086C, [(OUTB, 1, b"\xee" * 32)],     # past the count: refused
       tags=["C", "IDirect3DCubeTexture9::GetLevelDesc", "C.ldpast"])
SC = s.obj()
s.call(T_CUBE, 18, C, [5, 2, s.ptr()], 0, [(OUTB, 2, u32(SC))],
       tags=["C", "IDirect3DCubeTexture9::GetCubeMapSurface"])
# C2: a DDS cube map, 8x8 DXT1 with its two levels in the file, MipLevels 1: each face's second level skipped.
C2_FILE = dds(8, 8, DXT1, b"".join(solid(8, 8, DXT1, c) + solid(4, 4, DXT1, 0xFF000000) for c in CUBE_COLOURS),
              0xFE00, mips=2)
C2 = cube_load(s, "C2", C2_FILE, 1, (8, 8, 1, 1, DXT1, 8, 8, 1, 2, DXT1))
s.call(T_CUBE, 13, C2, [], 1, tags=["C2", "IDirect3DCubeTexture9::GetLevelCount"])
# D: D3DXCreateVolumeTexture(8, 4, 2, 0, 0, A8R8G8B8, MANAGED): the chain of 8x4, 4 levels.
D = s.obj()
s.d3dx(9, [DEV, 8, 4, 2, 0, 0, A8R8G8B8, 1, s.ptr()], 0, [(OUTB, 8, u32(D))],
       tags=["D", "d3dx9_42!D3DXCreateVolumeTexture", "creator"])
s.call(T_VOL, 13, D, [], 4, tags=["D", "IDirect3DVolumeTexture9::GetLevelCount"])
s.call(T_VOL, 17, D, [1, s.ptr(28)], 0, [(OUTB, 1, voldesc(A8R8G8B8, 4, 2, 1))],
       tags=["D", "IDirect3DVolumeTexture9::GetLevelDesc", "D.ld"])
# E: a DDS volume 4x4x4 A8R8G8B8, one level in the file, D3DX_DEFAULT levels: 3, two built in three dimensions.
v = "d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx"


def volume_load(s, tag, file, desc):
    o = s.obj()
    src, info = s.ptr(len(file)), s.ptr(28)
    s.d3dx(7, [DEV, src, len(file), DEFAULT, DEFAULT, DEFAULT, DEFAULT, 0, DEFAULT, 1, DEFAULT, DEFAULT, 0, info, 0,
               s.ptr()], 0,
           [(FILE, 1, file), (OUTB, 15, u32(o)), (OUTB, 13, u32(*desc[5:], 4, 4)),
            (DESC, NOARG, file_desc(o, 2, *desc))], tags=[tag, v, "creator"])
    return o


E_FILE = dds(4, 4, A8R8G8B8, b"".join(solid(4, 4, A8R8G8B8, 0xFF000000 | (0x40 * z) << 8) for z in range(4)),
             0x200000, depth=4)
E = volume_load(s, "E", E_FILE, (4, 4, 4, 3, A8R8G8B8, 4, 4, 4, 1, A8R8G8B8))
s.call(T_VOL, 13, E, [], 3, tags=["E", "IDirect3DVolumeTexture9::GetLevelCount", "E.lc"])
s.call(T_VOL, 17, E, [2, s.ptr(28)], 0, [(OUTB, 1, voldesc(A8R8G8B8, 1, 1, 1))],
       tags=["E", "IDirect3DVolumeTexture9::GetLevelDesc"])
# E2: a DDS volume 4x4x2 DXT1 with its three levels in the file (4x4x2, 2x2x1, 1x1x1).
E2_FILE = dds(4, 4, DXT1, solid(4, 4, DXT1, 0xFFFF0000) * 2 + solid(2, 2, DXT1, 0xFF00FF00) +
              solid(1, 1, DXT1, 0xFF0000FF), 0x200000, depth=2, mips=3)
E2 = volume_load(s, "E2", E2_FILE, (4, 4, 2, 3, DXT1, 4, 4, 2, 3, DXT1))
s.call(T_VOL, 17, E2, [1, s.ptr(28)], 0, [(OUTB, 1, voldesc(DXT1, 2, 2, 1))],
       tags=["E2", "IDirect3DVolumeTexture9::GetLevelDesc"])
# The frame: A and E bound and unbound again, then a clear (green) and a quad over the left half (red,
# pretransformed, the diffuse colour with stage 0 disabled).
s.call(T_DEV, 65, DEV, [0, A], tags=["A", "bind"])
s.call(T_DEV, 65, DEV, [1, E], tags=["E", "bind"])
s.call(T_DEV, 65, DEV, [0, 0], tags=["unbind0"])
s.call(T_DEV, 65, DEV, [1, 0], tags=["unbind1"])
s.call(T_DEV, 57, DEV, [22, 1])                                             # CULLMODE NONE
s.call(T_DEV, 67, DEV, [0, 1, 1])                                           # stage 0 COLOROP DISABLE
s.call(T_DEV, 41, DEV, [])
s.call(T_DEV, 43, DEV, [0, 0, 3, 0xFF00FF00, 0x3F800000, 0])
s.call(T_DEV, 89, DEV, [0x44])                                              # XYZRHW | DIFFUSE
quad = b"".join(struct.pack("<ffffI", x, y, 0.5, 1.0, 0xFFFF0000) for x, y in ((0, 0), (32, 0), (0, 64), (32, 64)))
s.call(T_DEV, 83, DEV, [5, 2, s.ptr(len(quad)), 20], 0, [(IN, 2, quad)], tags=["draw"])
s.call(T_DEV, 42, DEV, [])
s.call(T_DEV, 17, DEV, [0, 0, 0, 0])
s.add(FRAME, us=16666, frames=1)
s.add(END)
Path(out / "cubevol.expect.tsv").write_text(
    "frame\tx\ty\tstage_lo\tstage_hi\tb\tg\tr\ttol\twhat\n"
    "1\t48\t32\t0\t3\t0\t255\t0\t0\tthe clear (green) right of the quad\n"
    "1\t16\t32\t0\t0\t0\t255\t0\t0\tstage 0: the clear (green) where the quad would be\n"
    "1\t16\t32\t1\t3\t0\t0\t255\t0\tthe quad (red), drawn once the cube and volume are unbound\n")


def counts(recs):
    n = {k: 0 for k in TARGETS}
    for r in recs:
        for t in r["tags"]:
            if t in TARGETS and "SA" not in r["tags"] and "SF" not in r["tags"]:   # not the faces' own calls
                n[t] += 1
    return n


def edit(tag, fn):
    recs = [dict(r, args=list(r.get("args", [])), blobs=list(r.get("blobs", []))) if r["kind"] in (CALL, D3DX)
            else r for r in s.recs]
    hit = [r for r in recs if tag in r["tags"]]
    assert hit, tag
    fn(hit[0])
    return recs


def blob(r, role, arg, data):
    r["blobs"] = [(ro, a, data if (ro, a) == (role, arg) else d) for ro, a, d in r["blobs"]]


def word(data, i, v):
    return data[:4 * i] + u32(v) + data[4 * i + 4:]


manifest = {"targets": TARGETS, "objects": dict(A=A, B=B, C=C, C2=C2, D=D, E=E, E2=E2, F=F, SA=SA, SB=SB, SC=SC, SF=SF),
            "positive": counts(s.recs), "lock_uploads": 1, "streams": {}}
s.write(out / "cubevol.sa2d3d9", s.recs)
# Omitted: each target's calls left out (a creator with every call on what it made): the run passes, and the
# check of the positive stream's counts fails on that method.
for name in TARGETS:
    recs = [r for r in s.recs if name not in r["tags"]]
    for o in [t for r in s.recs if name in r["tags"] and "creator" in r["tags"] for t in r["tags"] if len(t) <= 2]:
        recs = [r for r in recs if o not in r["tags"]]
    key = "omit-" + name.split("!")[-1].replace("::", "-")
    s.write(out / f"cubevol-{key}.sa2d3d9", recs)
    manifest["streams"][key] = {"omitted": name, "exit": 0}
# Answered wrongly: each a stream the player must fail, by name.
wrong = {
    "lc": (edit("A.lc", lambda r: r.update(eax=3)), 4,
           f"CubeTexture::GetLevelCount: answered 3 levels; the cube texture's creation gives 4"),
    "ld": (edit("C.ld", lambda r: blob(r, OUTB, 1, word(surfdesc(A8R8G8B8, 4, 4), 6, 8))), 4,
           "CubeTexture::GetLevelDesc: answered Width 8 for level 0; the cube texture's creation gives 4"),
    "ldtype": (edit("C.ld", lambda r: blob(r, OUTB, 1, word(surfdesc(A8R8G8B8, 4, 4), 1, 3))), 4,
               "CubeTexture::GetLevelDesc: answered Type 3 for level 0; the cube texture's creation gives 1"),
    "ldpast": (edit("C.ldpast", lambda r: (r.update(eax=0), blob(r, OUTB, 1, surfdesc(A8R8G8B8, 1, 1)))), 4,
               "CubeTexture::GetLevelDesc: answered level 3 of a 3-level cube texture, which Direct3D 9 refuses"),
    "ldrefused": (edit("C.ld", lambda r: r.update(eax=0x8876086C)), 4,
                  "refused (0x8876086c) level 0 of a 3-level cube texture, which Direct3D 9 describes"),
    "vlc": (edit("E.lc", lambda r: r.update(eax=4)), 4,
            "VolumeTexture::GetLevelCount: answered 4 levels; the volume texture's creation gives 3"),
    "vld": (edit("D.ld", lambda r: blob(r, OUTB, 1, voldesc(A8R8G8B8, 4, 2, 2))), 4,
            "VolumeTexture::GetLevelDesc: answered Depth 2 for level 1; the volume texture's creation gives 1"),
    "cmsother": (edit("B.cms", lambda r: blob(r, OUTB, 2, u32(SA))), 4,
                 f"answered 0x{SA:08x}, which the player knows as another surface"),
    "cmsrange": (edit("A.refused", lambda r: (r.update(eax=0), blob(r, OUTB, 2, u32(s.next_obj * STRIDE + BASE)))), 4,
                 "for face 6 level 0, outside the 4-level cube"),
    "cmsrefused": (edit("A.cms", lambda r: r.update(eax=0x8876086C)), 4,
                   "refused (0x8876086c) face 3 level 1 of a 4-level cube, which the recording device answers"),
    "createknown": (edit("creator", lambda r: blob(r, OUTB, 5, u32(RT))), 4,
                    f"answered 0x{RT:08x}, which the player already knows as a surface"),
    "refused": (edit("B", lambda r: r.update(eax=0x8876086C)), 4,
                "a creation the recording device refused (0x8876086c): nothing to make"),
    "desc": (edit("C", lambda r: blob(r, DESC, NOARG, file_desc(C, 1, 4, 4, 1, 2, A8R8G8B8, 4, 4, 1, 1, A8R8G8B8))), 4,
             "the recording device made"),
    "vdesc": (edit("E", lambda r: blob(r, DESC, NOARG, file_desc(E, 2, 4, 4, 2, 3, A8R8G8B8, 4, 4, 4, 1, A8R8G8B8))),
              4, "the recording device made"),
    "desckind": (edit("C", lambda r: blob(r, DESC, NOARG, file_desc(C, 2, 4, 4, 1, 3, A8R8G8B8, 4, 4, 1, 1, A8R8G8B8))),
                 4, f"the recording device made 0x{C:08x} kind 2 4x4x1"),
    "notcube": (edit("C", lambda r: blob(r, FILE, 1, C_FILE[:112] + u32(0) + C_FILE[116:])), 4,
                "does not declare a cube map, which the recording device refuses"),
    "fivefaces": (edit("C", lambda r: blob(r, FILE, 1, C_FILE[:112] + u32(0x7E00) + C_FILE[116:])), 4,
                  "holds fewer than six faces: the player invents no face"),
    "cubedraw": ([r for r in s.recs if "unbind0" not in r["tags"]], 4,
                 f"a draw with the cube texture 0x{A:08x} bound at sampler 0: the player draws no cube or volume sampling"),
    "voldraw": ([r for r in s.recs if "unbind1" not in r["tags"]], 4,
                f"a draw with the volume texture 0x{E:08x} bound at sampler 1"),
}
for key, (recs, want, text) in wrong.items():
    s.write(out / f"cubevol-{key}.sa2d3d9", recs)
    manifest["streams"][key] = {"exit": want, "text": text}
# An entry the player does not know (the header's table names it): not handled, by name.
recs = [r for r in s.recs]
recs.insert(2, dict(kind=D3DX, tags=set(), entry=13, args=[DEV], eax=0, blobs=[]))
s.write(out / "cubevol-unknownentry.sa2d3d9", recs, ENTRIES + [(13, "D3DXCreateTextureFromResourceA", 1, "-")])
manifest["streams"]["unknownentry"] = {"exit": 4, "text": "a d3dx entry this player does not know"}
(out / "cubevol.json").write_text(json.dumps(manifest, indent=1))
print(f"cube and volume stream: {len(s.recs)} records, {len(manifest['streams'])} variants; targets "
      + ", ".join(f"{k.split('::')[-1].split('!')[-1]} {n}" for k, n in manifest["positive"].items()))
PY_CUBEVOL_STREAM
cubevol_check() {   # REPORT EXIT replay|stats
  "$PY" - "$CV/cubevol.json" "$1" "$2" "$3" <<'PY_CUBEVOL_CHECK'
import json, sys
man, rep, rc, mode = json.load(open(sys.argv[1])), json.load(open(sys.argv[2])), int(sys.argv[3]), sys.argv[4]
rows = {(m["type"], m["method"]): m for m in rep["methods"]}
cats = ("handled", "ignored_by_design", "skipped_by_stage", "not_handled")
problems = []
for name, key in man["targets"].items():
    row = rows.get(tuple(key)) or {}
    total = sum(int(row.get(c) or 0) for c in cats)
    if total != man["positive"][name] or not row.get("handled"):
        problems.append(f"{name}: {total} call(s) by category, {row.get('handled', 0)} handled, the stream makes "
                        f"{man['positive'][name]}")
    problems += [f"{name}: {c} {row[c]}" for c in ("not_handled", "skipped_by_stage", "failed",
                                                   "hr_differs_from_guest") if row.get(c)]
if rc != 0 or rep["exit_status"] != 0 or rep["errors"]:
    problems.append(f"exit {rc} (the report's {rep['exit_status']}), errors {rep['errors']} {rep['first_errors'][:2]}")
# A replay's host objects and uploads: one of each plain creation (two of the device's) and two of each DDS load; C's six faces two levels
# each and E's two levels built (14); C 6 x (64 + 16 + 4), C2 6 x 32, E 256 + 32 + 4, E2 16 + 8 + 8 and the face's 64
# bytes (1084); the face's upload the one lock upload.
want = {"host_created": {"cube-d3dx-create": 1, "cube-device-create": 2, "cube-file-dds": 2, "volume-d3dx-create": 1,
                         "volume-file-dds": 2}, "levels_generated": 14, "bytes_uploaded": 1084, "lock_uploads": 1}
if mode == "replay":
    problems += [f"textures.{k} {rep['textures'][k]!r}, want {v!r}" for k, v in want.items() if rep["textures"][k] != v]
print("; ".join(problems) if problems else
      "held: " + ", ".join(f"{t}::{m} {man['positive'][n]}" for n, (t, m) in man["targets"].items()))
sys.exit(1 if problems else 0)
PY_CUBEVOL_CHECK
}
for stage in 0 1 2 3; do
  cleanup_output "$CV/stage$stage.dxvk" && mkdir -p "$CV/stage$stage.dxvk"
  DXVK_LOG_PATH="$CV/stage$stage.dxvk" "$BIN/sa2_player" "$CV/cubevol.sa2d3d9" --stage "$stage" --hidden \
      --expect "$CV/cubevol.expect.tsv" --report "$CV/stage$stage.json" > "$CV/stage$stage.txt" 2>&1
  rc=$?
  grep -q "^expectations: 2 selected, 2 pass, 0 fail$" "$CV/stage$stage.txt" && [ $rc = 0 ] \
      || bad "cube and volume stream, stage $stage: exit $rc; $(grep '^expectations' "$CV/stage$stage.txt")"
  "$PY" "$TOOLS/play_report.py" check --report "$CV/stage$stage.json" --exit $rc --dxvk-log "$CV/stage$stage.dxvk" \
      > "$CV/stage$stage.check.txt" 2>&1 || bad "cube and volume stream, stage $stage: the report check"
done
line=$(cubevol_check "$CV/stage3.json" 0 replay) && echo "   cube and volume stream, stage 3: $line" \
    || bad "cube and volume stream, stage 3: $line"
"$BIN/sa2_player" "$CV/cubevol.sa2d3d9" --stats-only --report "$CV/stats.json" > "$CV/stats.txt" 2>&1
rc=$?
line=$(cubevol_check "$CV/stats.json" $rc stats) || bad "cube and volume stream, stats read: $line"
line=$(same_as_replay "$CV/stats.json" "$CV/stage3.json") && echo "   cube and volume stream, stats read: $line" \
    || bad "cube and volume stream, stats read differs from stage 3: $line"
# Each target left out: the stream plays, and the check fails, naming that target.
for name in $("$PY" -c "import json; print(' '.join(json.load(open('$CV/cubevol.json'))['targets']))"); do
  key=omit-$(sed 's/.*!//; s/::/-/' <<< "$name")
  DXVK_LOG_PATH="$CV/stage3.dxvk" "$BIN/sa2_player" "$CV/cubevol-$key.sa2d3d9" --stage 3 --hidden \
      --report "$CV/$key.json" > "$CV/$key.txt" 2>&1
  rc=$?
  line=$(cubevol_check "$CV/$key.json" $rc replay)
  if [ $? = 1 ] && [ $rc = 0 ] && [[ "; $line" == *"; $name: "* ]]; then
    echo "   cube and volume stream without $name: exit 0; the check fails: $(tr ';' '\n' <<< "$line" | grep -F "$name: " | sed 's/^ //')"
  else
    bad "cube and volume stream without $name: exit $rc; the check says: $line"
  fi
done
# Each wrong answer, and a draw with a cube or a volume bound: refused by name by the replay and the stats read.
for key in $("$PY" -c "import json; print(' '.join(k for k in json.load(open('$CV/cubevol.json'))['streams'] if not k.startswith('omit-')))"); do
  want=$("$PY" -c "import json; print(json.load(open('$CV/cubevol.json'))['streams']['$key']['exit'])")
  text=$("$PY" -c "import json; print(json.load(open('$CV/cubevol.json'))['streams']['$key']['text'])")
  DXVK_LOG_PATH="$CV/stage3.dxvk" "$BIN/sa2_player" "$CV/cubevol-$key.sa2d3d9" --stage 3 --hidden \
      --expect "$CV/cubevol.expect.tsv" --report "$CV/$key.json" > "$CV/$key.txt" 2>&1
  rc=$?
  "$BIN/sa2_player" "$CV/cubevol-$key.sa2d3d9" --stats-only --report "$CV/$key.stats.json" > "$CV/$key.stats.txt" 2>&1
  src=$?
  "$PY" "$TOOLS/play_report.py" check --report "$CV/$key.json" --exit $rc > "$CV/$key.check.txt" 2>&1
  crc=$?
  if [ $rc = "$want" ] && [ $src = "$want" ] && [ $crc = 1 ] && grep -qF -- "$text" "$CV/$key.txt" &&
     grep -qF -- "$text" "$CV/$key.stats.txt" && line=$(same_as_replay "$CV/$key.stats.json" "$CV/$key.json") &&
     ! cubevol_check "$CV/$key.json" $rc replay > /dev/null; then
    echo "   negative cube and volume $key: exit $rc in both; says \"$text\""
  else
    bad "negative cube and volume $key: exit $rc and $src (want $want), check exit $crc, text \"$text\" $(grep -cF -- "$text" "$CV/$key.txt") times; $line"
  fi
done
# ------------------------------------------ the recorder's harness, stats read
# Its main scenario calls entries 1, 2, 3 and 12; its edge scenario 2 and 6 to
# 12 (the synthetic stream adds 4 and 5). The player must name every one, plan
# 1 to 4 and 6 to 9 (the cube and volume creators, the edge scenario's DDS cube
# map and volume among them) as replayed and 5 and 10 to 12 as ignored by
# design, and fail the edge stream for its NOTE unless allowed. Both streams
# must hold every other check that needs no device: no error in either stats
# read.
if [ -x "$ZIG" ]; then
  H=$T/harness
  cleanup_output "$H" && mkdir -p "$H"
  wraps=$("$PY" -c "import sys; sys.path.insert(0, '$TOOLS'); import present_run; print(' '.join('-Wl,-wrap=' + w for w in present_run.WRAPS))")
  # The recorder references the audio devices' table getters (SA2_REC_AUDIO=1 only);
  # this harness has no audio device, so it links test_s5_stream.py's inert stand-ins.
  "$PY" -c "import sys; sys.path.insert(0, '$ROOT/Documentation/scripts/tests'); import test_s5_stream as t; open('$H/audio_getters.c', 'w').write(t.AUDIO_GETTERS_C)" \
      || bad "the audio getters' stand-ins"
  # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open: its edge scenario calls IDirect3DVertexDeclaration9::GetDeclaration.
  # shellcheck disable=SC2086
  if ZIG_LOCAL_CACHE_DIR=$H/zc ZIG_GLOBAL_CACHE_DIR=$T/zig-cache "$ZIG" cc -target x86_64-linux-musl -O1 -Wall -Wextra \
       -Werror -DSA2_D3D9_OPEN_WORLD_UNIT_BUILD -I "$ROOT/runtime" -I "$ROOT/host" "$ROOT/tools/s5/tests/rec_harness.c" "$ROOT/runtime/sa2_d3d9.c" \
       "$ROOT/runtime/sa2_guest_call.c" "$ROOT/host/sa2_present_rec.c" "$H/audio_getters.c" $wraps -o "$H/rec_harness" > "$H/build.txt" 2>&1; then
    for sc in main edge; do
      env SA2_REC_TRACE="$H/$sc.sa2s" SA2_REC_COUNTS="$H/$sc.counts.json" SA2_REC_META='{"build": "rec_harness"}' \
          "$H/rec_harness" --out "$H/$sc.json" --scenario $sc > "$H/$sc.harness.txt" 2>&1 || bad "rec_harness --scenario $sc"
      "$BIN/sa2_player" "$H/$sc.sa2s" --stats-only --allow-notes --report "$H/$sc.stats.json" > "$H/$sc.stats.txt" 2>&1
      echo "$?" > "$H/$sc.stats.exit"
    done
    "$BIN/sa2_player" "$H/edge.sa2s" --stats-only --report "$H/edge.strict.json" > "$H/edge.strict.txt" 2>&1
    echo "$?" > "$H/edge.strict.exit"
    "$PY" - "$H" <<'EOF' || bad "the recorder's harness streams"
import json, sys
h = sys.argv[1]
plan = {1: "handled", 2: "handled", 3: "handled", 4: "handled", 5: "ignored_by_design", 6: "handled",
        7: "handled", 8: "handled", 9: "handled", 10: "ignored_by_design",
        11: "ignored_by_design", 12: "ignored_by_design"}
seen, ok = {}, True
for sc, want_exit in (("main", 0), ("edge", 0)):
    rep = json.load(open(f"{h}/{sc}.stats.json"))
    code = int(open(f"{h}/{sc}.stats.exit").read())
    rows = [m for m in rep["methods"] if "entry" in m]
    for m in rows:
        cats = [c for c in ("handled", "ignored_by_design", "not_handled") if m.get(c)]
        seen[m["entry"]] = (f"{m['type']}::{m['method']}", cats)
        ok &= cats == [plan[m["entry"]]] and "?" not in m["type"]
    ok &= code == want_exit and rep["stream_entries_table"] and rep["direct3d_create9"]["direct3d9_calls_held"]
    ok &= rep["errors"] == 0
    print(f"   harness {sc}: stats read exit {code} (want {want_exit}), errors {rep['errors']} {rep['first_errors'][:2]}; "
          f"entries {sorted((e, n) for e, (n, _) in seen.items() if any(r['entry'] == e for r in rows))}")
strict = int(open(f"{h}/edge.strict.exit").read())
print(f"   harness edge without --allow-notes: exit {strict} (want 3: its NOTE)")
ok &= strict == 3 and sorted(seen) == [1, 2, 3, 6, 7, 8, 9, 10, 11, 12]
print(f"   the player names {len(seen)} entries from the recorder's own streams: "
      f"{', '.join(n + ' ' + c[0] for e, (n, c) in sorted(seen.items()))}")
sys.exit(0 if ok else 1)
EOF
  else
    bad "rec_harness did not build: $(tail -3 "$H/build.txt")"
  fi
else
  bad "the pinned zig is not at $ZIG: the recorder's harness cannot be built"
fi
# ------------------------------------------------------------ sound
# The recorder's audio harness (test_s5_audio.py's AUDIO_HARNESS_C, built with the
# pinned zig from runtime/sa2_d3d9.c, sa2_dsound.c, sa2_xaudio2.c and the recorder)
# records its scenario with SA2_REC_AUDIO=1 (a version 2 stream) and without (version
# 1). The renderer (sa2_audio_render) and the player --stats-only --audio-wav must
# write the same WAV, the player's report must hold no audio problem, every cursor,
# status and voice state the devices answered held to the mixer's clock, every store
# hashed to the guest's at the Unlocks that closed its last Lock, and pass
# play_report.py; the version 1 stream with --audio-wav must fail with exit 4 (it
# holds no sound) and the check with it. Last, a stage 3 replay of the version 2
# stream with --audio on SDL's dummy audio driver (SDL_AUDIO_DRIVER=dummy: the device
# consumes the sound in real time and nothing is heard) and --audio-wav must pass
# with the device open, the lead given it, its blocks queued or counted as dropped,
# and the same WAV.
if [ -x "$ZIG" ]; then
  AU=$T/audio
  cleanup_output "$AU" && mkdir -p "$AU"
  aw=$("$PY" -c "import sys; sys.path.insert(0, '$TOOLS'); import present_run; print(' '.join('-Wl,-wrap=' + w for w in present_run.WRAPS))")
  "$PY" -c "import sys; sys.path.insert(0, '$ROOT/Documentation/scripts/tests'); import test_s5_audio as t; open('$AU/rec_audio_harness.c', 'w').write(t.AUDIO_HARNESS_C)" \
      || bad "the audio harness source"
  # shellcheck disable=SC2086
  if ZIG_LOCAL_CACHE_DIR=$AU/zc ZIG_GLOBAL_CACHE_DIR=$T/zig-cache "$ZIG" cc -target x86_64-linux-musl -O1 -Wall -Wextra \
       -Werror -I "$ROOT/runtime" -I "$ROOT/host" "$AU/rec_audio_harness.c" "$ROOT/runtime/sa2_d3d9.c" \
       "$ROOT/runtime/sa2_guest_call.c" "$ROOT/runtime/sa2_dsound.c" "$ROOT/runtime/sa2_xaudio2.c" "$ROOT/host/sa2_present_rec.c" $aw \
       -o "$AU/rec_audio_harness" > "$AU/build.txt" 2>&1; then
    env SA2_REC_AUDIO=1 SA2_REC_TRACE="$AU/audio.sa2s" SA2_REC_COUNTS="$AU/audio.counts.json" \
        SA2_REC_META='{"build": "rec_audio_harness"}' "$AU/rec_audio_harness" --out "$AU/audio.json" \
        > "$AU/audio.harness.txt" 2>&1 || bad "rec_audio_harness with SA2_REC_AUDIO=1"
    env -u SA2_REC_AUDIO SA2_REC_TRACE="$AU/plain.sa2s" SA2_REC_COUNTS="$AU/plain.counts.json" \
        SA2_REC_META='{"build": "rec_audio_harness"}' "$AU/rec_audio_harness" --out "$AU/plain.json" \
        > "$AU/plain.harness.txt" 2>&1 || bad "rec_audio_harness without audio"
    "$BIN/sa2_audio_render" "$AU/audio.sa2s" --wav "$AU/render.wav" --json "$AU/render.json" > "$AU/render.txt" 2>&1 \
        || bad "sa2_audio_render on the audio harness: $(tail -2 "$AU/render.txt")"
    "$BIN/sa2_player" "$AU/audio.sa2s" --stats-only --audio-wav "$AU/stats.wav" --report "$AU/stats.json" \
        > "$AU/stats.txt" 2>&1
    rc=$?
    "$PY" "$TOOLS/play_report.py" check --report "$AU/stats.json" --exit $rc | sed 's/^/   /'
    [ "${PIPESTATUS[0]}" = 0 ] && [ $rc = 0 ] || bad "the audio stream --stats-only --audio-wav: exit $rc"
    cmp -s "$AU/stats.wav" "$AU/render.wav" || bad "the player's WAV is not the renderer's"
    "$BIN/sa2_player" "$AU/plain.sa2s" --stats-only --audio-wav "$AU/v1.wav" --report "$AU/v1.json" > "$AU/v1.txt" 2>&1
    rc=$?
    "$PY" "$TOOLS/play_report.py" check --report "$AU/v1.json" --exit $rc > "$AU/v1.check.txt" 2>&1
    crc=$?
    echo "   version 1 with --audio-wav: exit $rc (want 4); check exit $crc (want 1): $(grep -m1 'failure:' "$AU/v1.check.txt")"
    [ $rc = 4 ] && [ $crc = 1 ] && grep -q "holds no audio" "$AU/v1.check.txt" \
        || bad "a version 1 stream with --audio-wav: exit $rc, check $crc"
    cleanup_output "$AU/live.dxvk" && mkdir -p "$AU/live.dxvk"
    SDL_AUDIO_DRIVER=dummy DXVK_LOG_PATH="$AU/live.dxvk" "$BIN/sa2_player" "$AU/audio.sa2s" --stage 3 --hidden \
        --audio --audio-wav "$AU/live.wav" --report "$AU/live.json" > "$AU/live.txt" 2>&1
    rc=$?
    "$PY" "$TOOLS/play_report.py" check --report "$AU/live.json" --exit $rc --dxvk-log "$AU/live.dxvk" | sed 's/^/   /'
    [ "${PIPESTATUS[0]}" = 0 ] && [ $rc = 0 ] || bad "the audio stream at stage 3 with --audio (dummy driver): exit $rc"
    cmp -s "$AU/live.wav" "$AU/render.wav" || bad "the stage 3 replay's WAV is not the renderer's"
    "$PY" - "$AU" <<'EOF' || bad "the audio reports"
import json, sys
a = sys.argv[1]
stats, live, render = (json.load(open(f"{a}/{n}.json")) for n in ("stats", "live", "render"))
ok = True
for name, au in (("stats", stats["audio"]), ("live", live["audio"])):
    mix = {k: au[k] for k in ("records", "samples", "origin_us")}
    checks = (au["ds"]["cursor_checks"], au["ds"]["status_checks"], au["xa"]["state_checks"])
    print(f"   audio {name}: {mix}; checks {checks}; problems {au['problems']['count']}; "
          f"ds peak {au['ds']['level']['peak']}, xa peak {au['xa']['level']['peak']}")
    ok &= au["problems"]["count"] == 0 and checks == (6, 2, 2) and au["samples"] == render["audio"]["samples"]
    ok &= (au["ds"]["store_checks"], au["ds"]["store_mismatches"]) == (2, 0)
    ok &= au["wav"]["frames"] == au["samples"] and au["first_frame_us"] == render["first_frame_us"]
dev = live["audio"]["device"]
print(f"   device: {dev}")
# every Present but the first closes a block for the device; the first's, the sound before
# it (none here: the mix's origin is the first FRAME), is the WAV's, and the device gets
# the lead in its place
ok &= dev["open"] is True and dev["driver"] == "dummy" and dev["failure"] is None
ok &= dev["blocks_queued"] + dev["blocks_dropped"] == live["presents"] - 1
ok &= dev["lead_ms"] == 60 and dev["pre_first_frame_samples"] == 0 and dev["tail_samples"] == 0
ok &= stats["audio"]["device"] is None and live["options"]["audio"] is True
sys.exit(0 if ok else 1)
EOF
  else
    bad "rec_audio_harness did not build: $(tail -3 "$AU/build.txt")"
  fi
fi
[ $fail = 0 ] && echo "SYNTH_OK" || echo "SYNTH_FAIL"
exit $fail
