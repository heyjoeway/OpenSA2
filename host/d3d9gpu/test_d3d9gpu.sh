#!/bin/bash
# test_d3d9gpu.sh: the second host's end-to-end test (R2a), the GPU half of what
# Documentation/scripts/tests/test_s5_d3d9gpu.py and test_s5_compare_hosts.py hold
# without one. It needs a GPU and a window (one opens for a moment per run); no sound
# is played (SDL_AUDIO_DRIVER=dummy, and the player plays sound only with --audio).
#
#   BEFORE_COMMIT=<commit> bash host/d3d9gpu/test_d3d9gpu.sh
#
# BEFORE_COMMIT names the commit whose DXVK player this build's is held to (step 7). BEFORE
# (default private/generated/s5/r2a/build/before-<commit>) is that commit's player, built by
# host/d3d9gpu/build_before.sh from a clone at that commit when step 7 needs it and it has
# no build record; compare_hosts.py dxvk-ab refuses a BEFORE whose record is not that
# commit's player. OUT (default private/generated/s5/r2a/build/gpu-test) receives this
# tree's build, both players; everything a run writes is under $T (default
# private/generated/s5/r2a/gpu-test).
#
# The two hosts are two binaries (host/player/player.cpp, "Two binaries"): sa2_player, the
# DXVK host, and sa2_player_gpu, the second host with host/d3d9gpu linked in.
#
# In order, each step a check that can fail:
#   1  host/player/build.sh (the DXVK player) and host/d3d9gpu/build.sh (the second host's
#      player and the tools), into one OUT
#   2  the clear matrix (test/clear_matrix.h) through DXVK, through sa2_player_gpu
#      --clear-matrix on SDL_gpu and on the CPU model: the three equal line for line (every
#      compare_hosts.py run below runs it again, as its check i, which the receipt records)
#   3  the synthetic stream at --stage 0 on both hosts with its 16 stage-0 pixel
#      expectations (an oracle independent of DXVK), and compare_hosts.py run --controls
#      at stage 0: every frame draw-free and equal; at stages 1-3 compare_hosts.py run:
#      checks a-d pass (the synthetic stream draws in every frame, so e has no frame to
#      compare and fails by design, by its named reason)
#   4  headless equals windowed: the synthetic stage 0 under SDL_VIDEO_DRIVER=offscreen
#      and SA2_D3D9GPU_PRESENT=none, every frame's bgrx_hash the windowed run's
#   5  SDL_gpu's debug mode once (SA2_D3D9GPU_DEBUG=1, SDL_ASSERT=abort) on the synthetic
#      stream at stage 3, with the pinned Khronos validation layer loaded
#      (private/s5-tools/install-shader-tools; the user's second rulings have host 2's debug runs
#      load it, and the agent's amendment of that ruling, RULINGS-2-AMENDMENTS.md, has them fail on
#      any validation error): SDL's own usage checks and the layer find nothing
#   6  lavapipe (informational: printed, never a failure): the synthetic stage 0 on the
#      second host with VK_DRIVER_FILES naming lavapipe
#   7  the DXVK path at BEFORE_COMMIT: compare_hosts.py dxvk-unchanged first. UNCHANGED
#      (the DXVK player's every source, its run_player.sh and player_env.sh are
#      BEFORE_COMMIT's, byte for byte, and BEFORE_COMMIT holds no file that could shadow an
#      include its compiles read) passes the step: the DXVK path is unchanged by
#      construction. CHANGED builds BEFORE (unless it has its record) from a clone at
#      BEFORE_COMMIT, its pins the clone's, and runs compare_hosts.py dxvk-ab, BEFORE
#      (under the clone's run_player.sh and player_env.sh) against this build, on boot at
#      stage 3 and on the synthetic stream at stages 0-3, each of which must pass: what
#      dxvk-unchanged holds is what the A/B's two sides differ in, so CHANGED always has an
#      A/B to run. A refusal fails the step
#   8  compare_hosts.py run --controls on the registered boot stream: R2a's verdict
#   9  the state check (R2b, d3d9gpu_state_check) on the boot keys file step 8's second-host
#      run wrote beside its device report, with the device parameters from step 8's DXVK log
#      and that report (test/check_params.py, the modelled options from this build's extraction
#      manifest, as the census reads them; the SDL patch's capabilities the device used,
#      cross-checked against DXVK's log), the recorded reflection of boot's pixel shaders
#      and vertex shaders (test/shader_record.py), which the device's own reflection must equal,
#      and ruling 3's DEPTH_CLAMP ruling of this checkout
#      (Documentation/decisions/depth-clamp-policy.json, --residue-ruling: the SDL
#      patch, so on a device with depth clip and clamp SDL must build clamp with clip): every
#      pipeline and sampler tuple equal to DXVK's but for the open items, the vertex input by the
#      vertex shader's signature (U9); a pass, or STATE_CHECK_PENDING_USER (an item only the user
#      rules on: RT_FEEDBACK), or STATE_CHECK_FALLBACK_OWED (ruling 3's shader fallback owed on a
#      device without a capability), never a failure
#  10  the shader translator on the GPU (host/d3d9gpu/test_shaders.sh, R1's checks on NVIDIA and
#      lavapipe and C4, the state census's vertex input by the signature, BUILT=1 on this OUT):
#      R1_SHADERS_OK, never R1_SHADERS_FAIL (any validation error fails it, by the agent's amendment
#      of the user's validation-layer ruling)
# D3D9GPU_OK and exit 0 only when every step passes; D3D9GPU_PENDING_USER <items> and exit 3 when
# every step passes but step 9's verdict waits on the user (never an OK marker on it: the
# critic's M8); D3D9GPU_FALLBACK_OWED <items> and exit 4 when every step passes but step 9's
# verdict is the shader fallback owed; D3D9GPU_FAIL and exit 1 otherwise.
#
# CMP (default tools/s5/compare_hosts.py) names the comparison tool; a pinned copy may be
# given (one that resolves the repository the same way: two directories below it).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
REPO=${SA2_REPO:-$ROOT}
PY=$REPO/.venv/bin/python
OUT=${OUT:-$REPO/private/generated/s5/r2a/build/gpu-test}
T=${T:-$REPO/private/generated/s5/r2a/gpu-test}
BOOT=${BOOT:-$REPO/private/generated/s5/pushC/record/trace/boot.sa2s}
CMP=${CMP:-$ROOT/tools/s5/compare_hosts.py}
export SDL_AUDIO_DRIVER=dummy
[ -n "${BEFORE_COMMIT:-}" ] || { echo "BEFORE_COMMIT must name the commit of the unchanged player"; exit 2; }
BC=$(git -C "$REPO" rev-parse --verify --quiet "$BEFORE_COMMIT^{commit}") || { echo "no commit $BEFORE_COMMIT"; exit 2; }
BEFORE=${BEFORE:-$REPO/private/generated/s5/r2a/build/before-$BC}
mkdir -p "$T"
fail=0
bad() { echo "   FAIL: $*"; fail=1; }
stamp=$(date -u +%Y%m%dT%H%M%SZ)
R=$T/$stamp
mkdir -p "$R"
echo "runs under $R"

echo "== 1 build"
OUT=$OUT bash "$ROOT/host/player/build.sh" > "$R/build.log" 2>&1 || { bad "build (see $R/build.log)"; echo "D3D9GPU_FAIL"; exit 1; }
tail -1 "$R/build.log"
OUT=$OUT bash "$ROOT/host/d3d9gpu/build.sh" > "$R/build-gpu.log" 2>&1 \
  || { bad "the second host's build (see $R/build-gpu.log)"; echo "D3D9GPU_FAIL"; exit 1; }
tail -1 "$R/build-gpu.log"

echo "== 2 clear matrix"
(
  export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/matrix-dxvk-log OUT=$OUT
  mkdir -p "$SA2_DXVK_LOG_PATH"
  source "$ROOT/host/player/player_env.sh"
  "$OUT/d3d9gpu_clear_matrix" "$SA2_DXVK_D3D9" > "$R/matrix-dxvk.tsv" 2> "$R/matrix-dxvk.err" || exit 1
  SA2_D3D9GPU_REPORT=$R/matrix-d3d9gpu.json "$OUT/sa2_player_gpu" --clear-matrix \
      > "$R/matrix-sdlgpu.tsv" 2> "$R/matrix-sdlgpu.err" || exit 1
) || bad "a clear matrix run failed"
"$OUT/test_d3d9gpu" --clear-matrix > "$R/matrix-cpu.tsv" || bad "the clear matrix on the CPU model"
if [ -s "$R/matrix-dxvk.tsv" ] && cmp -s "$R/matrix-dxvk.tsv" "$R/matrix-sdlgpu.tsv" && cmp -s "$R/matrix-dxvk.tsv" "$R/matrix-cpu.tsv"; then
  echo "   $(wc -l < "$R/matrix-dxvk.tsv") cases: DXVK = SDL_gpu = CPU model"
else
  bad "the clear matrix differs: diff $R/matrix-dxvk.tsv $R/matrix-sdlgpu.tsv $R/matrix-cpu.tsv"
fi

echo "== 3 synthetic stream"
"$OUT/sa2_synth_stream" "$R/synth.sa2d3d9" "$R/synth.expect.tsv" > /dev/null || bad "sa2_synth_stream"
for host in dxvk sdlgpu; do
  (
    export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/stage0-$host-log OUT=$OUT
    mkdir -p "$SA2_DXVK_LOG_PATH"
    source "$ROOT/host/player/player_env.sh"
    player=$OUT/sa2_player
    [ $host = sdlgpu ] && player=$OUT/sa2_player_gpu && export SA2_D3D9GPU_REPORT=$R/stage0-$host.d3d9gpu.json
    "$player" "$R/synth.sa2d3d9" --allow-notes --stage 0 --expect "$R/synth.expect.tsv" \
        --report "$R/stage0-$host.json" > "$R/stage0-$host.txt" 2>&1
  )
  rc=$?
  line=$(grep -E '^expectations' "$R/stage0-$host.txt")
  echo "   stage 0 $host: exit $rc; $line"
  [ $rc = 0 ] && [ "$line" = "expectations: 16 selected, 16 pass, 0 fail" ] || bad "stage 0 on $host"
done
"$PY" "$CMP" run --stream "$R/synth.sa2d3d9" --stage 0 --scope r2a-clears --allow-notes --build "$OUT" --controls \
    --out "$R/compare-synth0" > "$R/compare-synth0.txt" 2>&1
rc=$?
"$PY" - "$R/compare-synth0/compare.json" <<'EOF' || bad "compare_hosts.py at synthetic stage 0"
import json, sys
d = json.load(open(sys.argv[1]))
e = next(c for c in d["checks"] if c["check"] == "e")
want = {"clear-colour": "as required", "count-draw": "not exercised", "scene-answer": "not exercised",
        "present-scribble": "as required", "present-scribble+clear-noop": "as required"}
got = {c["name"]: c["outcome"] for c in d["controls"]["results"]}
print(f"   stage 0 comparison: {d['verdict']}, every frame comparable {e['detail']['comparable_frames']}; controls {got}")
sys.exit(0 if d["verdict"] == "PASS" and e["detail"]["comparable"] == d["census"]["frames"] and got == want else 1)
EOF
# Below stage 3 the player deliberately omits full shader/asset replay. Geometry
# acceptance must refuse those stages rather than issue an R2 marker.
for st in 1 2; do
  "$PY" "$CMP" run --stream "$R/synth.sa2d3d9" --stage $st --scope r2-draws-full --allow-notes --build "$OUT" --out "$R/compare-synth$st" \
      > "$R/compare-synth$st.txt" 2>&1
  "$PY" - "$R/compare-synth$st/compare.json" $st <<'EOF' || bad "geometry scope accepted synthetic stage $st"
import json, sys
d = json.load(open(sys.argv[1]))
by = {c["check"]: c for c in d["checks"]}
ok = d["verdict"] == "FAIL" and d["marker"] == "R2_FAIL" and any("stage 3" in f for f in by["a"]["failures"])
print(f"   stage {sys.argv[2]} geometry acceptance refused: {ok}")
sys.exit(0 if ok else 1)
EOF
done
"$PY" "$CMP" run --stream "$R/synth.sa2d3d9" --stage 3 --scope r2-draws-full --allow-notes --build "$OUT" --controls --out "$R/compare-synth3" \
    > "$R/compare-synth3.txt" 2>&1
[ $? = 0 ] || bad "the complete synthetic geometry comparison"
"$PY" - "$R/compare-synth3/compare.json" <<'EOF' || bad "synthetic geometry marker"
import json, sys
d = json.load(open(sys.argv[1]))
ok = d["verdict_with_controls"] == "PASS" and d["marker"] == "R2_STREAM_GEOMETRY_OK" and d["controls"]["all_as_required"] is True
print(f"   synthetic complete geometry: {d['marker']}; controls {d['controls']['all_as_required']}")
sys.exit(0 if ok else 1)
EOF

echo "== 4 headless"
(
  export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/headless-log OUT=$OUT SDL_VIDEO_DRIVER=offscreen
  export SA2_D3D9GPU_PRESENT=none SA2_D3D9GPU_REPORT=$R/headless.d3d9gpu.json
  mkdir -p "$SA2_DXVK_LOG_PATH"
  source "$ROOT/host/player/player_env.sh"
  "$OUT/sa2_player_gpu" "$R/synth.sa2d3d9" --allow-notes --stage 0 --frames-out "$R/headless.y4m" \
      --report "$R/headless.json" > "$R/headless.txt" 2>&1
)
rc=$?
if [ $rc = 0 ] && cmp -s <(cut -f1-5,7 "$R/headless.y4m.times.tsv") <(cut -f1-5,7 "$R/compare-synth0/sdlgpu/frames.y4m.times.tsv"); then
  echo "   offscreen, no window claimed: exit 0, every frame the windowed run's"
else
  bad "headless: exit $rc, or frames other than the windowed run's"
fi
# Keep the private headless frame evidence with this run.

echo "== 5 SDL_gpu debug mode, under the validation layer"
"$PY" "$HERE/test/r1_receipt.py" vk-env --out "$R/debug-vk-env.json" || { bad "debug validation environment"; echo D3D9GPU_FAIL; exit 1; }
for v in $(compgen -e | grep '^VK_'); do unset "$v"; done
selection=$("$PY" "$REPO/tools/s5/validation_tools.py" selected-layer --root "$REPO" --shell) || { echo D3D9GPU_FAIL; exit 1; }
eval "$selection"
bash "$HERE/test/validation_probe/build_validation_probe.sh" "$R/debug-validation-probe" > "$R/debug-validation-probe-build.log" 2>&1 || { echo D3D9GPU_FAIL; exit 1; }
(
  export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/debug-log OUT=$OUT SA2_D3D9GPU_DEBUG=1 SDL_ASSERT=abort
  export SA2_D3D9GPU_REPORT=$R/debug.d3d9gpu.json
  mkdir -p "$SA2_DXVK_LOG_PATH"
  source "$ROOT/host/player/player_env.sh"
  # the layer by name (VK_INSTANCE_LAYERS), its library beside its manifest's directory
  # (LD_LIBRARY_PATH). A layer the loader cannot find does not fail the instance: SDL's own log
  # (SDL_LOGGING=gpu=info) says "Validation layers enabled" when it was, and the step requires it.
  # The layer counts every repeat of an error (VK_LAYER_DUPLICATE_MESSAGE_LIMIT).
  export VK_ADD_LAYER_PATH=${SA2_VALIDATION_MANIFEST%/*} VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation \
         LD_LIBRARY_PATH=${SA2_VALIDATION_LIBRARY%/*} SDL_LOGGING=gpu=info VK_LAYER_DUPLICATE_MESSAGE_LIMIT=1000000
  "$PY" "$HERE/test/validation_run.py" --root "$REPO" run --probe-dir "$R/debug-validation-probe" \
    --record "$R/debug-validation.json" -- "$OUT/sa2_player_gpu" "$R/synth.sa2d3d9" --allow-notes --stage 3 --report "$R/debug.json" > "$R/debug.txt" 2>&1
)
rc=$?
verrors=$(grep -c "Validation Error" "$R/debug.txt")
grep -q "Validation layers enabled" "$R/debug.txt" && ! grep -q "Validation layers not found" "$R/debug.txt" \
  || bad "debug mode: SDL's log does not show the validation layer enabled ($R/debug.txt)"
"$PY" -c "
import json, sys
d = json.load(open('$R/debug.d3d9gpu.json'))
sys.exit(0 if d['sdl_gpu']['debug_mode'] and d['complete'] and d['not_implemented']['count'] == 0
         and d['env']['VK_INSTANCE_LAYERS'] == 'VK_LAYER_SA2_validation_probe:VK_LAYER_KHRONOS_validation' else 1)" \
  && [ $rc = 0 ] && [ "$verrors" = 0 ] && echo "   debug mode: exit 0, SDL's checks and the validation layer quiet" \
  || bad "debug mode: exit $rc, $verrors validation errors ($R/debug.txt)"

echo "== 6 lavapipe (informational)"
(
  export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/lvp-log OUT=$OUT SA2_D3D9GPU_REPORT=$R/lvp.d3d9gpu.json
  export VK_DRIVER_FILES=/usr/share/vulkan/icd.d/lvp_icd.json VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.json
  mkdir -p "$SA2_DXVK_LOG_PATH"
  source "$ROOT/host/player/player_env.sh"
  "$OUT/sa2_player_gpu" "$R/synth.sa2d3d9" --allow-notes --stage 0 --expect "$R/synth.expect.tsv" \
      --report "$R/lvp.json" > "$R/lvp.txt" 2>&1
)
echo "   lavapipe: exit $?; $(grep -E '^expectations' "$R/lvp.txt"); device $("$PY" -c "
import json; d = json.load(open('$R/lvp.d3d9gpu.json')); print(d['sdl_gpu']['device_name'], d['sdl_gpu']['driver_info'])" 2>&1)"

echo "== 7 the DXVK path at $BC"
"$PY" "$CMP" dxvk-unchanged --after "$OUT" --before-commit "$BC" --out "$R/dxvk-unchanged" \
    > "$R/dxvk-unchanged.txt" 2>&1
urc=$?
sed 's/^/   /' "$R/dxvk-unchanged.txt" | head -12
case $urc in
  0) echo "   UNCHANGED by construction: no A/B to run";;
  1)
    echo "   CHANGED: the A/B against $BC's player"
    if [ -f "$BEFORE/sa2_player.json" ]; then
      echo "   $BEFORE has its record (compare_hosts.py dxvk-ab holds it to $BC)"
    else
      bash "$HERE/build_before.sh" "$BC" "$BEFORE" > "$R/before-build.log" 2>&1 \
        || bad "the before build (see $R/before-build.log)"
      tail -1 "$R/before-build.log" | sed 's/^/   /'
    fi
    "$PY" "$CMP" dxvk-ab --before "$BEFORE" --before-commit "$BC" --after "$OUT" --stream "$BOOT" --stage 3 \
        --out "$R/dxvk-ab-boot" | sed 's/^/   /'
    [ "${PIPESTATUS[0]}" = 0 ] || bad "dxvk-ab on boot"
    for st in 0 1 2 3; do
      "$PY" "$CMP" dxvk-ab --before "$BEFORE" --before-commit "$BC" --after "$OUT" --stream "$R/synth.sa2d3d9" \
          --stage $st --allow-notes --out "$R/dxvk-ab-synth$st" | head -1 | sed 's/^/   /'
      [ "${PIPESTATUS[0]}" = 0 ] || bad "dxvk-ab on the synthetic stream at stage $st"
    done;;
  *) bad "dxvk-unchanged refused (exit $urc, $R/dxvk-unchanged.txt)";;
esac

echo "== 8 the boot comparison"
"$PY" "$CMP" run --stream "$BOOT" --stage 3 --scope r2-draws-full --build "$OUT" --controls --out "$R/compare-boot" | sed 's/^/   /'
[ "${PIPESTATUS[0]}" = 0 ] || bad "the boot comparison"
"$PY" "$HERE/test/r1_receipt.py" compare --run "$R/boot-identity" --compare "$R/compare-boot/compare.json" \
    | sed 's/^/   /'
[ "${PIPESTATUS[0]}" = 0 ] || bad "the registered full-boot geometry identity"

echo "== 9 the state check on boot's tuples"
pending=""
owed=""
RULING=$ROOT/Documentation/decisions/depth-clamp-policy.json
"$PY" "$HERE/test/check_params.py" --dxvk-log "$R/compare-boot/dxvk/dxvk-log/sa2_player_d3d9.log" \
    --device-report "$R/compare-boot/sdlgpu/d3d9gpu.json" \
    --extract-manifest "$OUT/d3d9gpu-gen/refs/extract_manifest.json" --out "$R/state-params.json" | sed 's/^/   /'
if [ "${PIPESTATUS[0]}" != 0 ]; then
  bad "the state check's parameters"
else
  "$PY" "$HERE/test/shader_record.py" --streams boot --out "$R/shader-record.json" > "$R/shader-record.txt" 2>&1 \
    || bad "the shader record ($R/shader-record.txt)"
  "$OUT/d3d9gpu_state_check" --params "$R/state-params.json" --dumps "$R/compare-boot/sdlgpu/d3d9gpu.keys.json" \
      --shader-record "$R/shader-record.json" --residue-ruling "$RULING" --out "$R/state-check.json" > "$R/state-check.txt" 2>&1
  src=$?
  grep -E "^(census|capabilities|  FAIL|  PENDING|STATE_CHECK)" "$R/state-check.txt" | sed 's/^/   /'
  marker=$(tail -1 "$R/state-check.txt")
  # the check took the ruling passed, visibly (a ruling it ignored would leave a capable device's
  # marker unchanged: the I6 census review's NIT 6)
  "$PY" - "$R/state-check.json" "$RULING" <<'EOF' || bad "the state check did not take the ruling passed"
import json, sys
took, passed = json.load(open(sys.argv[1]))["ruling"], json.load(open(sys.argv[2]))["ruling"]
print(f"   ruling: {took} (passed: {passed})")
sys.exit(took != passed)
EOF
  case "$marker" in
    STATE_CHECK_OK) ;;
    STATE_CHECK_PENDING_USER*) [ $src = 3 ] && pending=${marker#STATE_CHECK_PENDING_USER} || bad "the state check's exit $src";;
    STATE_CHECK_FALLBACK_OWED*) [ $src = 4 ] && owed=${marker#STATE_CHECK_FALLBACK_OWED} || bad "the state check's exit $src";;
    *) bad "the state check on boot: $marker";;
  esac
fi

echo "== 10 the shader translator on the GPU"
BUILT=1 OUT=$OUT R=$R/shaders bash "$HERE/test_shaders.sh" > "$R/shaders.txt" 2>&1
trc=$?
grep -E "^(C[0-9]|corpus|harness|R1_SHADERS)" "$R/shaders.txt" | sed 's/^/   /'
tmarker=$(tail -1 "$R/shaders.txt")
case "$tmarker" in
  R1_SHADERS_OK) [ $trc = 0 ] || bad "test_shaders.sh's exit $trc";;
  *) bad "the shader translator on the GPU: $tmarker ($R/shaders.txt)";;
esac

if [ $fail != 0 ]; then echo "D3D9GPU_FAIL"; exit 1; fi
if [ -n "$owed" ]; then echo "D3D9GPU_FALLBACK_OWED$owed"; exit 4; fi
if [ -n "$pending" ]; then echo "D3D9GPU_PENDING_USER$pending"; exit 3; fi
echo "D3D9GPU_OK"
exit 0
