#!/bin/bash
# test_shaders.sh: the second host's shader translator on the GPU (R1, ruling 2's route:
# private/generated/s5/rulings/r1-shader-translator/PLAN.md section 4), on NVIDIA and on lavapipe.
# It needs a GPU; the DXVK player's runs open a window for a moment each, the second host's run
# headless. One GPU job at a time, and never while a ship's gates run.
#
#   OUT=<build> [R=<run dir>] [BUILT=1] [RECEIPT=Documentation/receipts/s5-host2-r1-v1.json] \
#     bash host/d3d9gpu/test_shaders.sh
#
# OUT is the build (host/player/build.sh and host/d3d9gpu/build.sh into one OUT, which this script
# runs first unless BUILT=1). Everything a run writes is under R (default
# private/generated/s5/r1/shaders/<UTC stamp>), which must not exist yet: a run never mixes its
# results with an earlier one's. RECEIPT names the receipt r1_receipt.py writes from the run (none
# without it). Every debug run of the second host loads the pinned Khronos validation layer with
# SDL_LOGGING=gpu=info, so SDL says in the log whether the layer was enabled, and with the layer's
# duplicate-message limit lifted, so each error is counted (layer_env). Nothing inherited can filter
# its messages: the run records every VK_* variable it inherited (vk-env.json, the receipt's
# validation_environment) and unsets them all, points VK_LAYER_SETTINGS_PATH at an empty directory of
# its own, and fails if the settings file the layer reads before that path ($XDG_DATA_HOME, or
# ~/.local/share, /vulkan/settings.d/vk_layer_settings.txt) exists. Each step is a check that can fail:
#
#   C1   the dxbc-spirv archive is DXVK's objects: Documentation/scripts/check_pins.py --only dxvk
#        (its sha256, a regular archive, each member DXVK's build's object)
#   C4   VS_SIGNATURE resolved (U9): host/d3d9gpu/state_census.sh on the default device, with the SDL
#        patch's capabilities (STATE_CENSUS_OK) and with both forced off (C4-caps-off: the stock SDL's
#        path, STATE_CENSUS_FALLBACK_OWED BORDER DEPTH_CLAMP TEST_LEG=border,depth-clamp), each with no
#        VS_SIGNATURE named, no RT_FEEDBACK, nothing unmapped or differing, every tuple evaluated with
#        the recorded reflection and the device's own equal to it, and every control failing as
#        required, the vertex-input ones and map-ps-sampler-superset by the census itself and
#        map-vi-declorder on exactly the tuples whose signature is not the declaration
#        (test/r1_receipt.py c4)
#   corpus  the four census streams (state_census.sh's: boot menu story mh) replayed through the
#        second host, headless, with SA2_D3D9GPU_SHADER_DUMP: every shader created is translated (no
#        failure), its bytecode dumped by sha256; r1_receipt.py plan turns the keys files and the
#        dump into the load plan: the objects (13 vertex and 13 pixel shaders), the BGRA variants the
#        census tuples need and every creation pair and drawn pair with a census state; every pixel
#        shader at the sampler-mode word DXVK's rule gives the census's sampler records (0 throughout:
#        colour textures, no fetch4; a record that would give another refuses the plan)
#   per driver (VK_DRIVER_FILES: nvidia, then lavapipe):
#   C2   d3d9gpu_shaderc load: every object (the base: DXVK's uniform branch on the sampler-mode word),
#        every pixel shader specialized at its word (d9g_shader.h Variant::sampler_modes, the module a
#        device is to hand the driver once it computes each draw's word, R2-draws; the device still
#        translates the base), BGRA variant, border fallback variant (ruling 3: every
#        pixel shader sampling a stage a census BORDER sampler is bound at, as a device without
#        CLAMP_TO_BORDER needs it; a BORDER case the fallback cannot take fails) and pair of the plan
#        through SDL_gpu's debug
#        mode with the pinned Khronos validation layer loaded (private/s5-tools/install-shader-tools;
#        the user's second rulings have host 2's debug runs use it, and the agent's amendment of that
#        ruling, RULINGS-2-AMENDMENTS.md, has them fail on any error): SHADERS_LOAD_OK, the layer
#        enabled and no "Validation Error"; the
#        capture layer on the same load: the modules SDL_gpu handed the driver are the modules the load
#        wrote (C3's host-2 side); its control, the same load on SDL's default device (no feature
#        chain), must print validation errors (the layer is loaded and its errors are seen)
#   C2 at Vulkan 1.1  the same load with --vk11: SDL_gpu's device made at API 1.1 with Vulkan 1.0 features,
#        the 1.1 profile, every module lowered to SPIR-V 1.3 (d9g_shader_abi.cpp lower_vk11); the same pass
#        conditions, every module written at SPIR-V 1.3, and every device the capture saw made at API 1.1
#        with no Vulkan 1.1, 1.2 or 1.3 feature structure; its control, the 1.3 profile's unlowered modules
#        on the same device (--vk11-unlowered), must print the version's and the capabilities' VUIDs
#        (08737, 08740) and no other (validation_run.py CONTROL_VUIDS); the layer's shader cache is off
#        (LAYER_SETTINGS), since its key ignores the target environment
#   C3   the canonical diff against DXVK, from two DXVK sources: the DXVK player over the four
#        streams with DXVK_SHADER_DUMP_PATH (the graphics-pipeline-library variant: spv_canon.py
#        --batch, every one of the 26 objects as the base, unspecialized) and the capture layer (vkcap)
#        over the same runs (the linked variant DXVK hands the driver: spv_canon.py --linked-batch,
#        every drawn pair, host 2's pixel shader specialized against DXVK's frozen at the word its
#        pipeline gives, which must be SpecId 5's default 0, and folded by the pinned spirv-opt, whose
#        result must be a pure deletion, test/spv_freeze.py, and every library module equal to the dump
#        but for its layout); its controls: each translator
#        fault (SA2_D3D9GPU_SHADER_FAULT, a test run) must fail the batch on exactly the objects it
#        changed, naming where, and every one but the linkage's must change one; lowerDot off must stop
#        the build (dxbc-spirv's SPIR-V builder has no FDot); the linkage fault runs through the load's
#        pairs and the linked batch, and where it changes no pair (no pair of the streams needing a
#        rewrite, which every clean pair's record must show) it is named as such and the synthetic
#        pair of test_s5_d3d9gpu_shader.py holds it; the sampler-mode faults (the word ignored, each
#        mode's depth bit flipped, each register reading the next one's mode) run the same way, each
#        failing the linked batch on exactly the drawn pairs it changed (the shift changes nothing at
#        the streams' word 0, and is named as such); R0's modules (next/D/r0/r0c, dxbc-spirv's
#        own options) in host 2's place must fail it on every object; and the mutation pass in the
#        removed region: four mutants of each sampling pixel shader's base inside the paths word 0
#        removes, each valid SPIR-V and each failed by the GPL half (the pure deletion, C12, cannot see
#        them, and is recorded not seeing them); every DXVK run that set the
#        capture layer must show it loaded, and the capture must hold exactly the records it counted
#   C7   the null stream: a vertex shader reading an input its declaration lacks, through DXVK
#        (test/null_probe.cpp: DXVK's null binding 16) and through SDL_gpu (d3d9gpu_shaderc
#        probe-null: a buffer of the second host's own on the device's null slot, at the per-instance
#        rate with pitch 0 as the device's vertex input gives it (d9g_pipeline.h kNullStreamSlot), holding the value the
#        DXVK leg read), one quad each: the two hosts' pixels equal, the complement value differs, no
#        leg reads the clear colour, and the SDL legs run under the layer with no validation error; the
#        SDL leg's pixels drawn again with the base pixel shader (no layer) are the specialized one's
#   C6   the shader cache on the 26 objects (18 distinct shaders): a miss for each distinct one, then a
#        hit for each with equal bytes, a corrupted entry detected and recompiled, a verify pass with
#        no difference, a root outside private/ refused and not created, and a BGRA variant compiled
#        after its base a miss that gives the variant's own module
#   C8   ruling 3's border fallback against the SDL patch's sampler (d3d9gpu_shaderc border, synthetic
#        shaders): point filtering bit-equal, linear's residue measured and below the emulation's
#        absence (the control), which must differ in every case; every leg's frame drawn again with the
#        base pixel shader (no layer) is the specialized one's
#   C13  the sampler-mode draws (d3d9gpu_shaderc modes, synthetic): a colour texture (mode 0), D16 and
#        D24S8 depth textures (2, or 3 where the device stores D24S8 as D32 float), D24S8 as D32 float
#        with its reference clamped (3), an L8 texture whose stage was given GET4 and then a plain bias
#        (1: DXVK's fetch4 switch is sticky) against the same after GET4 and GET1 (0), and a shader of
#        four samplers at mixed modes, each case's modes named literally and DXVK's rule giving them;
#        under the layer, every case as it must look and no validation error; every frame the base's
#        drawn with the same word; its must-fail control, the base leg under the layer too: one
#        VUID-vkCmdDraw-None-06479 per colour register drawn (draw-time errors are seen)
#   C12  every specialized module (the load's pixel shaders, pairs and border variants, and C13's)
#        valid SPIR-V (the pinned spirv-val) and a pure deletion of host 2's own base under its word
#        (test/spv_specialize.py; the bases from r1_receipt.py bases, no GPU, each built by the same
#        translator and profile from the same bytecode, and C13's from its base leg); the sweep: every
#        game pixel shader at each mode on each register, on all at once and at 12 mixed words, on the
#        CPU, each compiled, valid, a pure deletion and keeping exactly the depth-compare samples and
#        gathers its word takes; its controls, the loads under modes_skipped and modes_inverted, fail it
#        on every pixel shader that reads a mode
#   C11  the specialized sampling pixel shaders (the objects, the pairs' pixel shaders and the border
#        variants), and C13's, through the pinned shadercross and spirv-cross to MSL 2.3, every texture
#        typed by its register's mode (depth2d only at 2 or 3; test/metal_check.py); every base with a
#        colour register, and the load under modes_inverted, fail it as required
#   C9   the capture layer is transparent: the DXVK player's boot stream at stage 3 with the layer
#        loaded (its load line in the log) has the frames of a run without it (and, on NVIDIA, the
#        registered reference's), and the control build (every colour write mask zero, loaded too)
#        runs every frame and changes at least one; every leg exits 0
#   C10  the R2a host comparison (tools/s5/compare_hosts.py run --controls, the registered boot
#        stream) after the device-creation change: PASS, the controls as required
#
# C11 runs its conversion and texture-type clause here, its compile and run on a Mac carried forward. C5's table and the synthetic corpus are
# Documentation/scripts/tests/test_s5_d3d9gpu_shader.py and test_s5_d3d9gpu_specialize.py; this script
# records each driver's profile.
#
# Markers: R1_SHADERS_OK and exit 0 only when every check passes with no validation error;
# R1_SHADERS_FAIL (exit 1) otherwise, naming each failed check and each validation error by check
# (the user's second rulings have host 2's debug runs use the layers; that those runs fail on any
# validation error is the agent's amendment of that ruling, RULINGS-2-AMENDMENTS.md, not the user's
# words: no error is exempt unless the user rules it so).
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
REPO=${SA2_REPO:-$ROOT}
PY=$REPO/.venv/bin/python
OUT=${OUT:?OUT must name the build (host/player/build.sh and host/d3d9gpu/build.sh)}
R=${R:-$REPO/private/generated/s5/r1/shaders/$(date -u +%Y%m%dT%H%M%SZ)}
[ -e "$R" ] && { echo "the run directory $R exists: a run starts empty"; echo R1_SHADERS_FAIL; exit 1; }
TOOL=("$PY" "$HERE/test/r1_receipt.py")
CANON=("$PY" "$HERE/test/spv_canon.py")
OBSERVER=("$PY" "$HERE/test/validation_run.py" --root "$REPO" run --probe-dir "$R/validation-probe")
R0C=$REPO/private/generated/s5/next/D/r0/r0c/out
REF_TIMES=$REPO/private/generated/s5/pushB/live/player/frames.y4m.times.tsv
export SDL_AUDIO_DRIVER=dummy
declare -A STREAM=(
  [boot]=$REPO/private/generated/s5/pushC/record/trace/boot.sa2s
  [menu]=$REPO/private/generated/s5/ship3/threads-runs/menu-default/stream.sa2s
  [story]=$REPO/private/generated/s5/ship3/threads-runs/story-default/stream.sa2s
  [mh]=$REPO/private/generated/s5/pushC/record/trace/metalharbor.sa2s
)
declare -A ICD=([nvidia]=/usr/share/vulkan/icd.d/nvidia_icd.json [lvp]=/usr/share/vulkan/icd.d/lvp_icd.json)
FAULTS=(umin_dropped point_size_f32 fp16 nocontraction_stripped constants_swapped samplers_swapped denorm_ftz
        lower_dot_off float_emulation_enabled linkage_skipped modes_skipped modes_inverted modes_slot_shifted)
# the faults that change a pair's module: through the load's pairs and the linked batch
LOAD_FAULTS=" linkage_skipped modes_skipped modes_inverted modes_slot_shifted "
# the debug runs' layer settings: SDL says whether the layer was enabled (gpu=info), and the layer
# counts every repeat of an error (its default stops at 10 of a kind)
LAYER_SETTINGS=(SDL_LOGGING=gpu=info VK_LAYER_DUPLICATE_MESSAGE_LIMIT=1000000)
mkdir -p "$R/checks" "$R/layer-settings"
# a step the harness could not run is a failure the verdict reads (checks/harness.txt)
bad() { echo "   FAIL: $*"; echo "$*" >> "$R/checks/harness.txt"; }
echo "runs under $R"
selection=$("$PY" "$REPO/tools/s5/validation_tools.py" selected-layer --root "$REPO" --shell) || { echo R1_SHADERS_FAIL; exit 1; }
eval "$selection"
"$PY" "$REPO/tools/s5/validation_tools.py" selected-layer --root "$REPO" > "$R/selected-validation.json" || { echo R1_SHADERS_FAIL; exit 1; }
bash "$HERE/test/validation_probe/build_validation_probe.sh" "$R/validation-probe" > "$R/validation-probe-build.log" 2>&1 || { echo R1_SHADERS_FAIL; exit 1; }
# the Vulkan environment this run inherited, recorded and then cleared: no inherited VK_* variable (a
# message filter, a log file, a debug action, a layer list or path) reaches any leg; the layer's settings
# file is looked for where it is read before VK_LAYER_SETTINGS_PATH, which the debug runs point at an
# empty directory of the run's own (layer_env)
"${TOOL[@]}" vk-env --out "$R/vk-env.json" || bad "the Vulkan environment ($R/vk-env.json): a layer settings file is present"
for v in $(compgen -e | grep '^VK_'); do unset "$v"; done

if [ "${BUILT:-}" != 1 ]; then
  echo "== build"
  OUT=$OUT bash "$ROOT/host/player/build.sh" > "$R/build.log" 2>&1 || { echo "the DXVK player's build failed ($R/build.log)"; echo R1_SHADERS_FAIL; exit 1; }
  OUT=$OUT bash "$ROOT/host/d3d9gpu/build.sh" > "$R/build-gpu.log" 2>&1 || { echo "the second host's build failed ($R/build-gpu.log)"; echo R1_SHADERS_FAIL; exit 1; }
fi
SHADERC=$OUT/shader/d3d9gpu_shaderc
[ -x "$SHADERC" ] && [ -x "$OUT/sa2_player_gpu" ] && [ -x "$OUT/sa2_player" ] || { echo "no complete build in $OUT"; echo R1_SHADERS_FAIL; exit 1; }
bash "$HERE/test/vkcap/build_vkcap.sh" "$R/vkcap" > "$R/vkcap-build.log" 2>&1 || bad "the capture layer's build ($R/vkcap-build.log)"
tail -1 "$R/vkcap-build.log"
# the null-stream probe's DXVK leg (C7), against the install's DXVK Native headers and SDL3
I=$(cd "${SA2_S5_INSTALL:-$REPO/private/s5-tools/install}" && pwd -P)
${CXX:-g++} -std=c++17 -O2 -Wall -Wextra -I"$I/include/dxvk" -I"$I/include" -o "$R/null_probe" "$HERE/test/null_probe.cpp" \
  -L"$I/lib" -Wl,--disable-new-dtags -Wl,-rpath,"$I/lib" -lSDL3 -ldl > "$R/null-probe-build.log" 2>&1 \
  || bad "the null probe's build ($R/null-probe-build.log)"
"${TOOL[@]}" synth --out "$R/synth" || bad "the synthetic shaders"

echo "== C1 the dxbc-spirv archive"
"$PY" "$REPO/Documentation/scripts/check_pins.py" --root "$REPO" --only dxvk > "$R/c1.txt" 2>&1
"${TOOL[@]}" note --run "$R" --check C1 --marker-file "$R/c1.txt" --want "tool pins OK" || bad "C1: check_pins.py --only dxvk"

echo "== corpus: the census streams through the second host"
mkdir -p "$R/bytecode"
for s in boot menu story mh; do
  mkdir -p "$R/corpus/$s"
  (
    export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/corpus/$s/log OUT=$OUT SDL_VIDEO_DRIVER=offscreen
    export SA2_D3D9GPU_PRESENT=none SA2_D3D9GPU_REPORT=$R/corpus/$s/d3d9gpu.json SA2_D3D9GPU_SHADER_DUMP=$R/bytecode
    unset SA2_D3D9GPU_CAPS_OFF SA2_D3D9GPU_TEST_RUN SA2_D3D9GPU_SHADER_FAULT
    mkdir -p "$SA2_DXVK_LOG_PATH"
    source "$ROOT/host/player/player_env.sh"
    "$OUT/sa2_player_gpu" "${STREAM[$s]}" --stage 3 --report "$R/corpus/$s/report.json" > "$R/corpus/$s/player.txt" 2>&1
  ) || bad "the $s replay through the second host"
done
"${TOOL[@]}" corpus --run "$R" --streams boot menu story mh || bad "corpus: a replay's translations"
mkdir -p "$R/named" "$R/refl"
"${TOOL[@]}" name --plan-keys "$R/corpus/boot/d3d9gpu.keys.json" --bytecode "$R/bytecode" --out "$R/named" || bad "corpus: naming the objects"
"$SHADERC" facts > "$R/facts-default.json" 2> "$R/facts-default.err" || true
for drv in nvidia lvp; do
  VK_DRIVER_FILES=${ICD[$drv]} VK_LOADER_LAYERS_DISABLE='~implicit~' "$SHADERC" facts > "$R/facts-$drv.json" 2> "$R/facts-$drv.err" \
    || bad "the $drv device's facts ($R/facts-$drv.err)"
  "$SHADERC" profile "$R/facts-$drv.json" > "$R/profile-$drv.txt" || bad "the $drv profile"
done
"$SHADERC" compile "$R/facts-nvidia.json" "$R/refl" "$R"/named/*.bin > "$R/refl.txt" 2>&1 || bad "the reflections the plan reads"
"${TOOL[@]}" plan --keys "$R"/corpus/{boot,menu,story,mh}/d3d9gpu.keys.json --bytecode "$R/bytecode" --reflections "$R/refl" \
  --out "$R/plan.json" || bad "the load plan"

echo "== C4 the state census: the vertex input by the vertex shader's signature (U9)"
mkdir -p "$R/c4"
for leg in C4 C4-caps-off; do
  caps=; [ $leg = C4-caps-off ] && caps=border,depth-clamp
  OUT=$OUT R=$R/c4/$leg CAPS_OFF=$caps bash "$HERE/state_census.sh" > "$R/c4/$leg.txt" 2>&1
  crc=$?
  "${TOOL[@]}" c4 --run "$R" --check $leg --census "$R/c4/$leg" --log "$R/c4/$leg.txt" --exit $crc || bad "$leg: the state census"
done

layer_env() {  # the pinned Khronos validation layer, for a debug run
  echo "VK_ADD_LAYER_PATH=${SA2_VALIDATION_MANIFEST%/*}" "VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation" \
       "LD_LIBRARY_PATH=${SA2_VALIDATION_LIBRARY%/*}" "VK_LAYER_SETTINGS_PATH=$R/layer-settings" "${LAYER_SETTINGS[@]}"
}
layer_env > "$R/layer-env.txt"
cap_layer_env() {  # the validation layer and the capture layer together, recording into $1
  echo "VK_ADD_LAYER_PATH=${SA2_VALIDATION_MANIFEST%/*}:$R/vkcap/vkcap" \
       "VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation:VK_LAYER_SA2_vkcap" "VKCAP_OUT=$1" \
       "LD_LIBRARY_PATH=${SA2_VALIDATION_LIBRARY%/*}" "VK_LAYER_SETTINGS_PATH=$R/layer-settings" "${LAYER_SETTINGS[@]}"
}
for drv in nvidia lvp; do
  D=$R/$drv
  mkdir -p "$D/load" "$D/load-control" "$D/gpl" "$D/capture" "$D/faults"
  GPUENV=(SDL_VIDEO_DRIVER=offscreen "VK_DRIVER_FILES=${ICD[$drv]}" "VK_LOADER_LAYERS_DISABLE=~implicit~")
  echo "== $drv: C2 every shader, variant and pair through SDL_gpu, under the validation layer"
  env "${GPUENV[@]}" $(cap_layer_env "$D/load-capture") "${OBSERVER[@]}" --record "$D/validation/load.json" -- "$SHADERC" load "$R/plan.json" "$D/load" > "$D/load.txt" 2>&1
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/load-control.json" -- "$SHADERC" load "$R/plan.json" "$D/load-control" --no-features > "$D/load-control.txt" 2>&1
  "${TOOL[@]}" load --run "$R" --driver $drv --log "$D/load.txt" --control-log "$D/load-control.txt" --plan "$R/plan.json" \
    --out-dir "$D/load" --capture "$D/load-capture" || bad "C2 on $drv"
  echo "== $drv: C2 at Vulkan 1.1: every shader lowered to SPIR-V 1.3 on SDL_gpu's 1.1 device, under the validation layer"
  mkdir -p "$D/load-vk11" "$D/load-vk11-control"
  env "${GPUENV[@]}" $(cap_layer_env "$D/load-vk11-capture") "${OBSERVER[@]}" --record "$D/validation/load-vk11.json" -- "$SHADERC" load "$R/plan.json" "$D/load-vk11" --vk11 > "$D/load-vk11.txt" 2>&1
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/load-vk11-control.json" -- "$SHADERC" load "$R/plan.json" "$D/load-vk11-control" --vk11-unlowered > "$D/load-vk11-control.txt" 2>&1
  "${TOOL[@]}" load --run "$R" --driver $drv-vk11 --vk11 --log "$D/load-vk11.txt" --control-log "$D/load-vk11-control.txt" \
    --plan "$R/plan.json" --out-dir "$D/load-vk11" --capture "$D/load-vk11-capture" || bad "C2 at Vulkan 1.1 on $drv"
  # host 2's base (the word off) of every specialized pair and border variant, for C12 (no GPU)
  "${TOOL[@]}" bases --plan "$R/plan.json" --shaderc "$SHADERC" --facts "$R/facts-$drv.json" --named "$R/named" \
    --out "$D/bases" > "$D/bases.txt" 2>&1 || bad "the bases of the specialized modules on $drv ($D/bases.txt)"

  echo "== $drv: C3 the canonical diff against DXVK's dump and DXVK's linked modules"
  for s in boot menu story mh; do
    mkdir -p "$D/dxvk/$s"
    (
      export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$D/dxvk/$s/log OUT=$OUT
      mkdir -p "$SA2_DXVK_LOG_PATH"
      source "$ROOT/host/player/player_env.sh"
      export VK_DRIVER_FILES=${ICD[$drv]} DXVK_SHADER_DUMP_PATH=$D/gpl VK_ADD_LAYER_PATH=$R/vkcap/vkcap \
             VK_INSTANCE_LAYERS=VK_LAYER_SA2_vkcap VKCAP_OUT=$D/capture
      "$OUT/sa2_player" "${STREAM[$s]}" --stage 3 --report "$D/dxvk/$s/report.json" > "$D/dxvk/$s/player.txt" 2>&1
    ) || bad "the DXVK player on $s ($drv)"
  done
  "${CANON[@]}" --batch "$D/gpl" "$D/load" --expect 26 > "$D/canon-gpl.txt" 2>&1
  "${CANON[@]}" --linked-batch "$D/capture" "$D/load/pairs" --gpl-dump "$D/gpl" --expect-drawn 7 > "$D/canon-linked.txt" 2>&1
  "${TOOL[@]}" canon --run "$R" --driver $drv --gpl "$D/canon-gpl.txt" --linked "$D/canon-linked.txt" \
    --captures "$D"/dxvk/{boot,menu,story,mh}/player.txt --capture-dir "$D/capture" || bad "C3 on $drv"
  for f in "${FAULTS[@]}"; do
    mkdir -p "$D/faults/$f"
    if [[ "$LOAD_FAULTS" == *" $f "* ]]; then
      # the linkage and the sampler-mode specialization are a pair's pixel shader's: the fault runs
      # through the load's pairs and the linked batch
      env "${GPUENV[@]}" SA2_D3D9GPU_TEST_RUN=1 SA2_D3D9GPU_SHADER_FAULT=$f "$SHADERC" load "$R/plan.json" "$D/faults/$f" \
        > "$D/faults/$f.load.txt" 2>&1
      "${CANON[@]}" --linked-batch "$D/capture" "$D/faults/$f/pairs" --gpl-dump "$D/gpl" --expect-drawn 7 \
        > "$D/faults/$f.linked.txt" 2>&1
      continue
    fi
    SA2_D3D9GPU_TEST_RUN=1 SA2_D3D9GPU_SHADER_FAULT=$f "$SHADERC" compile "$R/facts-$drv.json" "$D/faults/$f" "$R"/named/*.bin \
      > "$D/faults/$f.compile.txt" 2>&1
    "${CANON[@]}" --batch "$D/gpl" "$D/faults/$f" --expect 26 > "$D/faults/$f.canon.txt" 2>&1
  done
  mkdir -p "$D/r0c"
  "${TOOL[@]}" r0c --r0c "$R0C/$([ $drv = nvidia ] && echo nv || echo lvp)" --load "$D/load" --out "$D/r0c" || bad "R0's modules for the canonicaliser's control"
  "${CANON[@]}" --batch "$D/gpl" "$D/r0c" --expect 26 > "$D/r0c.canon.txt" 2>&1
  "${TOOL[@]}" controls --run "$R" --driver $drv --clean "$D/load" --faults "$D/faults" --names "${FAULTS[@]}" \
    --r0c "$D/r0c.canon.txt" --linkage-load "$D/faults/linkage_skipped" --linkage-canon "$D/faults/linkage_skipped.linked.txt" \
    --gpl "$D/gpl" || bad "C3's controls on $drv"

  echo "== $drv: C7 the null stream"
  mkdir -p "$D/null" "$D/null-control"
  (
    export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$D/null/log OUT=$OUT
    mkdir -p "$SA2_DXVK_LOG_PATH"
    source "$ROOT/host/player/player_env.sh"
    export VK_DRIVER_FILES=${ICD[$drv]}
    "$R/null_probe" "$SA2_DXVK_D3D9" "$R/synth/null_vs.bin" "$R/synth/null_ps.bin" > "$D/null-dxvk.txt" 2>&1
  )
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/null-sdl.json" -- "$SHADERC" probe-null "$D/null" "$R/synth/null_vs.bin" "$R/synth/null_ps.bin" \
    $("${TOOL[@]}" probe-value --dxvk "$D/null-dxvk.txt") > "$D/null-sdl.txt" 2>&1
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/null-control.json" -- "$SHADERC" probe-null "$D/null-control" "$R/synth/null_vs.bin" "$R/synth/null_ps.bin" \
    $("${TOOL[@]}" probe-value --dxvk "$D/null-dxvk.txt" --complement) > "$D/null-control.txt" 2>&1
  mkdir -p "$D/null-base"
  env "${GPUENV[@]}" "$SHADERC" probe-null "$D/null-base" "$R/synth/null_vs.bin" "$R/synth/null_ps.bin" \
    $("${TOOL[@]}" probe-value --dxvk "$D/null-dxvk.txt") --unspecialized > "$D/null-base.txt" 2>&1
  "${TOOL[@]}" nullprobe --run "$R" --driver $drv --dxvk "$D/null-dxvk.txt" --sdl "$D/null-sdl.txt" --control "$D/null-control.txt" \
    --sdl-base "$D/null-base.txt" || bad "C7 on $drv"

  echo "== $drv: C8 the border fallback"
  mkdir -p "$D/border"
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/border.json" -- "$SHADERC" border "$D/border" "$R/synth/border_vs.bin" "$R/synth/border_ps.bin" > "$D/border.txt" 2>&1
  mkdir -p "$D/border-base"
  env "${GPUENV[@]}" "$SHADERC" border "$D/border-base" "$R/synth/border_vs.bin" "$R/synth/border_ps.bin" --unspecialized \
    > "$D/border-base.txt" 2>&1
  "${TOOL[@]}" border --run "$R" --driver $drv --log "$D/border.txt" --json "$D/border/border.json" \
    --base-json "$D/border-base/border.json" || bad "C8 on $drv"

  echo "== $drv: C13 the sampler-mode draws, every mode by DXVK's rule"
  mkdir -p "$D/modes" "$D/modes-base"
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/modes.json" -- "$SHADERC" modes "$D/modes" "$R/synth/border_vs.bin" "$R/synth/border_ps.bin" \
    "$R/synth/modes_ps4.bin" > "$D/modes.txt" 2>&1
  # the base leg under the layer too: the frames' reference, and the must-fail control (06479 at draw time)
  env "${GPUENV[@]}" $(layer_env) "${OBSERVER[@]}" --record "$D/validation/modes-base.json" -- "$SHADERC" modes "$D/modes-base" "$R/synth/border_vs.bin" "$R/synth/border_ps.bin" \
    "$R/synth/modes_ps4.bin" --unspecialized > "$D/modes-base.txt" 2>&1
  "${TOOL[@]}" modes --run "$R" --driver $drv --log "$D/modes.txt" --json "$D/modes/modes.json" \
    --base-json "$D/modes-base/modes.json" --base-log "$D/modes-base.txt" || bad "C13 on $drv"

  echo "== $drv: C12 every specialized module a pure deletion of its base; C11 Metal's texture types"
  "${TOOL[@]}" specialize --run "$R" --driver $drv --plan "$R/plan.json" --load "$D/load" --bases "$D/bases" \
    --faults "$D/faults" --modes-modules "$D/modes/modules" --modes-base "$D/modes-base/modules" --shaderc "$SHADERC" \
    --facts "$R/facts-$drv.json" --named "$R/named" --sweep-out "$D/sweep" || bad "C12 on $drv"
  "${TOOL[@]}" metal --run "$R" --driver $drv --plan "$R/plan.json" --load "$D/load" --bases "$D/bases" \
    --modes-modules "$D/modes/modules" --modes-base "$D/modes-base/modules" --modes-json "$D/modes/modes.json" \
    --faults "$D/faults" || bad "C11 on $drv"

  echo "== $drv: C9 the capture layer is transparent"
  for leg in none layer control; do
    mkdir -p "$D/c9/$leg"
    (
      export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$D/c9/$leg/log OUT=$OUT
      mkdir -p "$SA2_DXVK_LOG_PATH"
      source "$ROOT/host/player/player_env.sh"
      export VK_DRIVER_FILES=${ICD[$drv]}
      [ $leg = layer ] && export VK_ADD_LAYER_PATH=$R/vkcap/vkcap VK_INSTANCE_LAYERS=VK_LAYER_SA2_vkcap VKCAP_OUT=$D/c9/$leg/capture
      [ $leg = control ] && export VK_ADD_LAYER_PATH=$R/vkcap/vkcap-control VK_INSTANCE_LAYERS=VK_LAYER_SA2_vkcap VKCAP_OUT=$D/c9/$leg/capture
      "$OUT/sa2_player" "${STREAM[boot]}" --stage 3 --frames-out "$D/c9/$leg/frames.y4m" --report "$D/c9/$leg/report.json" \
        > "$D/c9/$leg/player.txt" 2>&1
    )
    echo $? > "$D/c9/$leg/exit"   # every leg's exit, the control's included, is the frames step's to judge
  done
  "${TOOL[@]}" frames --run "$R" --driver $drv --none "$D/c9/none/frames.y4m.times.tsv" --layer "$D/c9/layer/frames.y4m.times.tsv" \
    --control "$D/c9/control/frames.y4m.times.tsv" $([ $drv = nvidia ] && echo --reference "$REF_TIMES") \
    --none-log "$D/c9/none/player.txt" --layer-log "$D/c9/layer/player.txt" --control-log "$D/c9/control/player.txt" \
    --none-exit "$D/c9/none/exit" --layer-exit "$D/c9/layer/exit" --control-exit "$D/c9/control/exit" \
    --truncate "$D/c9/none/frames.y4m" "$D/c9/layer/frames.y4m" "$D/c9/control/frames.y4m" || bad "C9 on $drv"
done

echo "== C6 the shader cache"
mkdir -p "$R/cache-out" "$R/cache-outside" "$R/cache-variant" "$R/cache-variant-clean"
CACHE=$R/cache
for pass in 1 2; do
  "$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-out" --cache "$CACHE" --repo "$REPO" "$R"/named/*.bin > "$R/cache-$pass.txt" 2>&1
done
"${TOOL[@]}" corrupt --cache "$CACHE" --out "$R/cache-corrupted.txt" || bad "C6: corrupting an entry"
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-out" --cache "$CACHE" --repo "$REPO" "$R"/named/*.bin > "$R/cache-3.txt" 2>&1
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-out" --cache "$CACHE" --repo "$REPO" --verify "$R"/named/*.bin > "$R/cache-4.txt" 2>&1
# a root outside private/ that does not exist: refused, and still not there after
OUTSIDE=${TMPDIR:-/tmp}/r1-cache-outside-$$-$RANDOM/cache
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-outside" --cache "$OUTSIDE" --repo "$REPO" "$R"/named/vs0.bin \
  > "$R/cache-5.txt" 2>&1
# a BGRA variant after its base (the plan's first): a miss, and the variant's own module
read -r VNAME VMASK < <("${TOOL[@]}" first-variant --plan "$R/plan.json") || bad "C6: the plan's first variant"
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-variant" --cache "$CACHE" --repo "$REPO" --bgra "${VMASK:-0}" \
  "$R/named/${VNAME:-vs0}.bin" > "$R/cache-6.txt" 2>&1
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-variant-clean" --bgra "${VMASK:-0}" "$R/named/${VNAME:-vs0}.bin" \
  > "$R/cache-6-clean.txt" 2>&1
# a pixel shader specialized at its sampler-mode word after its base: a miss, and the specialized module
mkdir -p "$R/cache-modes" "$R/cache-modes-clean"
read -r MNAME MWORD < <("${TOOL[@]}" first-specialized --plan "$R/plan.json" --reflections "$R/refl") || bad "C6: the plan's first specialized pixel shader"
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-modes" --cache "$CACHE" --repo "$REPO" --sampler-modes "${MWORD:-0}" \
  "$R/named/${MNAME:-ps0}.bin" > "$R/cache-7.txt" 2>&1
"$SHADERC" compile "$R/facts-nvidia.json" "$R/cache-modes-clean" --sampler-modes "${MWORD:-0}" "$R/named/${MNAME:-ps0}.bin" \
  > "$R/cache-7-clean.txt" 2>&1
"${TOOL[@]}" cache --run "$R" --logs "$R"/cache-{1,2,3,4,5}.txt --clean "$R/refl" --out-dir "$R/cache-out" \
  --outside-root "$OUTSIDE" --variant-log "$R/cache-6.txt" --variant-module "$R/cache-variant/${VNAME:-vs0}.spv" \
  --variant-clean "$R/cache-variant-clean/${VNAME:-vs0}.spv" --modes-log "$R/cache-7.txt" \
  --modes-module "$R/cache-modes/${MNAME:-ps0}.spv" --modes-clean "$R/cache-modes-clean/${MNAME:-ps0}.spv" \
  --modes-base "$R/cache-out/${MNAME:-ps0}.spv" || bad "C6"

echo "== C10 the R2a host comparison after the device-creation change"
"$PY" "$ROOT/tools/s5/compare_hosts.py" run --stream "${STREAM[boot]}" --stage 3 --scope r2-draws-full --build "$OUT" --controls \
  --out "$R/compare-boot" > "$R/compare-boot.txt" 2>&1
"${TOOL[@]}" compare --run "$R" --compare "$R/compare-boot/compare.json" || bad "C10"

"$PY" "$HERE/test/validation_run.py" --root "$REPO" audit-r1 --run "$R" --shaderc "$SHADERC" \
  > "$R/validation-audit.txt" 2>&1 || bad "live validation layer observation"

if [ -n "${RECEIPT:-}" ]; then
  "${TOOL[@]}" write --run "$R" --out "$RECEIPT" --build "$OUT" || bad "the receipt could not be written"
fi
"${TOOL[@]}" verdict --run "$R" | tee "$R/verdict.txt"
exit "${PIPESTATUS[0]}"
