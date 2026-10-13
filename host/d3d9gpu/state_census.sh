#!/bin/bash
# state_census.sh: the state check on the four stream censuses (R2b-prep,
# private/generated/s5/r2b/PLAN.md section 12.1). It needs a GPU: each stream is replayed
# headless through the second host (sa2_player_gpu, SDL_VIDEO_DRIVER=offscreen,
# SA2_D3D9GPU_PRESENT=none; no window, no sound: SDL_AUDIO_DRIVER=dummy), whose device writes
# every distinct draw-state tuple beside its report (<report>.keys.json). Then:
#
#   1  each run's player report through tools/s5/play_report.py check: exit 0, complete, the
#      device's not_implemented 0, no fault, and its keys file the one the device hashes;
#      default draws reconciled independently against the original full stream, or CAPS_OFF
#      draws.drawn 0 with its separate named fallback obligations
#   2  test/check_params.py: the device parameters, from DXVK's own log of a run on this machine
#      (DXVK_LOG, default: a DXVK run of the synthetic stream at stage 0 made here) and the
#      second host's report (the critic's M7); a DXVK configuration that sets an option the
#      extracted code reads refuses (the extraction's manifest)
#   3  test/shader_record.py: the streams' pixel shaders' sampler and output masks and vertex
#      shaders' input signatures, from their bytecode (next/D/scan/<stream>/{ps,vs}N.bin) and from
#      dxbc-spirv's disassembly (next/D/disasm), agreeing (the review's finding 2): with the device's
#      own reflection (R1's translator, each keys file's), three readings that the check holds equal
#   4  d3d9gpu_state_check --dumps (the four keys files) --shader-record --controls, with
#      --defaults, --audit, --draw-tests and --self-test: every pipeline and sampler tuple
#      compared with DXVK's, DXVK evaluated with the recorded reflection and the device's own held
#      to it, the vertex input by the vertex shader's signature (U9, the rule NULL_STREAM);
#      --stream-identity (the streams' sha256s) and --device-binary (sa2_player_gpu: every keys file
#      made in it); every pipeline tuple evaluated with a recorded pixel shader and a recorded vertex
#      shader (the confirm's residual 5), and no tuple's or created shader's device reflection other
#      than the record's; the ruling the check took is RULING's, the test leg it names is
#      CAPS_OFF's, and the fallback it owes is what the devices' own decisions owe
#   5  test/scan_projection.py: the dumps' tuples are the census's (next/D/scan/*.txt), tuple
#      for tuple (the critic's M9), and their declarations, targets and textures the census's
#
#   OUT=<build> [R=<run dir>] [RULING=<Documentation/decisions/...json>] [CAPS_OFF=<names>] \
#     bash host/d3d9gpu/state_census.sh
#
# The census is the four streams, boot menu story mh: STREAMS may name others for a look, and
# then the run ends STATE_CENSUS_FAIL (a subset is no census). RULING is the DEPTH_CLAMP ruling
# the check takes (state_check.cpp --residue-ruling), only as a decision record committed in the
# checkout the check was built from: by default ruling 3's,
# Documentation/decisions/depth-clamp-policy.json in this checkout (the SDL patch
# where the device has depth clip and clamp, the shader emulation where it does not); RULING=
# (empty) passes none.
#
# CAPS_OFF (border, depth-clamp, or both, comma-separated) runs the replays as a test run with
# those capabilities of the SDL patch forced off (SA2_D3D9GPU_TEST_RUN=1 SA2_D3D9GPU_CAPS_OFF;
# d9g_entry.cpp), the shader fallback's leg on a device that supports both: every device report
# must name exactly that override (and, without CAPS_OFF, none), and the check reads the keys
# files as a test leg, which never reads OK.
#
# Markers: STATE_CENSUS_OK only when every step passes and the check's verdict is a pass;
# STATE_CENSUS_PENDING_USER <items> when the only open items are the user's to rule on
# (state_check's STATE_CHECK_PENDING_USER: DEPTH_CLAMP; RT_FEEDBACK); STATE_CENSUS_FALLBACK_OWED
# <items> (exit 4) when every step passes and the check's
# verdict is STATE_CHECK_FALLBACK_OWED (ruling 3's shader fallback owed: BORDER, DEPTH_CLAMP; the
# items it names after it); STATE_CENSUS_FAIL otherwise.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
REPO=${SA2_REPO:-$ROOT}
PY=$REPO/.venv/bin/python
OUT=${OUT:?OUT must name the build that host/player/build.sh and host/d3d9gpu/build.sh made}
STREAMS=${STREAMS:-boot menu story mh}
R=${R:-$REPO/private/generated/s5/r2b/census/$(date -u +%Y%m%dT%H%M%SZ)}
SCAN=${SCAN:-$REPO/private/generated/s5/next/D/scan}
RULING=${RULING-$ROOT/Documentation/decisions/depth-clamp-policy.json}
CAPS_OFF=${CAPS_OFF:-}
# CAPS_OFF in the device's canonical form (d9g_report.cpp parse_caps_off), an unknown name refused
caps_border=0; caps_clamp=0
for c in ${CAPS_OFF//,/ }; do
  case $c in border) caps_border=1;; depth-clamp) caps_clamp=1;;
    *) echo "CAPS_OFF names $c: the capabilities are border and depth-clamp"; exit 2;; esac
done
case $caps_border$caps_clamp in 11) CAPS_OFF=border,depth-clamp;; 10) CAPS_OFF=border;; 01) CAPS_OFF=depth-clamp;; *) CAPS_OFF=;; esac
export SDL_AUDIO_DRIVER=dummy
declare -A STREAM=(
  [boot]=$REPO/private/generated/s5/pushC/record/trace/boot.sa2s
  [menu]=$REPO/private/generated/s5/ship3/threads-runs/menu-default/stream.sa2s
  [story]=$REPO/private/generated/s5/ship3/threads-runs/story-default/stream.sa2s
  [mh]=$REPO/private/generated/s5/pushC/record/trace/metalharbor.sa2s
)
mkdir -p "$R"
fail=0
bad() { echo "   FAIL: $*"; fail=1; }
echo "runs under $R"
[ "$STREAMS" = "boot menu story mh" ] || bad "STREAMS is \"$STREAMS\": the census is boot menu story mh (a subset is no census)"

echo "== 1 the replays (headless, the second host)"
for s in $STREAMS; do
  mkdir -p "$R/$s"
  (
    export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/$s/dxvk-log OUT=$OUT SDL_VIDEO_DRIVER=offscreen
    export SA2_D3D9GPU_PRESENT=none SA2_D3D9GPU_REPORT=$R/$s/d3d9gpu.json
    unset SA2_D3D9GPU_CAPS_OFF SA2_D3D9GPU_TEST_RUN
    [ -n "$CAPS_OFF" ] && export SA2_D3D9GPU_TEST_RUN=1 SA2_D3D9GPU_CAPS_OFF=$CAPS_OFF
    mkdir -p "$SA2_DXVK_LOG_PATH"
    source "$ROOT/host/player/player_env.sh"
    "$OUT/sa2_player_gpu" "${STREAM[$s]}" --stage 3 --report "$R/$s/report.json" > "$R/$s/player.txt" 2>&1
  )
  rc=$?
  "$PY" "$ROOT/tools/s5/play_report.py" check --report "$R/$s/report.json" --exit $rc --out "$R/$s/check.json" \
      > "$R/$s/check.txt" 2>&1
  crc=$?
  "$PY" - "$R/$s/d3d9gpu.json" "$R/$s/d3d9gpu.keys.json" "$s" "$rc" "$crc" "$CAPS_OFF" <<'EOF' || bad "the $s replay"
import hashlib, json, sys
d = json.load(open(sys.argv[1]))
k = json.load(open(sys.argv[2]))
p = d["pipeline"]
keys_ok = hashlib.sha256(open(sys.argv[2], "rb").read()).hexdigest() == p["keys_sha256"]
if not keys_ok:
    print(f"   {sys.argv[3]}: the keys file is not the one the device's report hashes ({p['keys_sha256']})")
# the override the replay ran under is the one asked for (none without CAPS_OFF), in the report
# and in the keys file
want = sys.argv[6] or None
sg = d["sdl_gpu"]
caps_ok = sg["capability_override"] == want and k["device"]["capability_override"] == want
if not caps_ok:
    print(f"   {sys.argv[3]}: the device ran under the capability override {sg['capability_override']!r}, not {want!r}")
ok = (sys.argv[4] == "0" and sys.argv[5] == "0" and d["complete"] and d["not_implemented"]["count"] == 0
      and (want is None or d["draws"]["drawn"] == 0) and k["complete"] and p["keys_refused"] is None and d["fault"] is None and keys_ok
      and k["map_faults"] == 0 and caps_ok)
print(f"   {sys.argv[3]}: player exit {sys.argv[4]}, check exit {sys.argv[5]}; device complete {d['complete']}, "
      f"not implemented {d['not_implemented']['count']}, drawn {d['draws']['drawn']}; draws mapped {p['draws_mapped']}, "
      f"inputs {p['distinct_inputs']}, keys {p['distinct_keys']}, state keys {p['distinct_state_keys']}, "
      f"samplers {p['distinct_samplers']}, unmapped inputs {p['unmapped']['inputs']}; capabilities clamp to border "
      f"{sg['sampler_clamp_to_border']}, depth clip and clamp {sg['depth_clip_and_clamp']} (detected "
      f"{sg['detected']['sampler_clamp_to_border']}, {sg['detected']['depth_clip_and_clamp']}; override {want}); "
      f"the patch's: BORDER samplers {p['patch']['border_samplers']}, depth-clamp keys {p['patch']['depth_clamp_keys']}; "
      f"fallback owed: BORDER samplers {p['fallback_owed']['border_samplers']}, R_DEPTH_CLAMP keys "
      f"{p['fallback_owed']['depth_clamp_keys']}; recomputes {p['recomputes']}")
sys.exit(0 if ok else 1)
EOF
  if [ -z "$CAPS_OFF" ]; then
    "$PY" "$HERE/test/check_census_draws.py" --stream "${STREAM[$s]}" --player "$R/$s/report.json" \
        --device "$R/$s/d3d9gpu.json" --out "$R/$s/draw-audit.json" || bad "the $s original draw account"
  fi
done

# the streams, held by the sha256 each player report names (PLAN.md 9.3)
"$PY" - "$R" $STREAMS <<'EOF' > "$R/streams.json" || bad "the streams' identities"
import json, sys
from pathlib import Path
r = Path(sys.argv[1])
out = {}
for s in sys.argv[2:]:
    rep = json.loads((r / s / "report.json").read_text())
    out[s] = {"path": rep.get("stream"), "sha256": rep.get("stream_sha256"), "presents": rep.get("presents"),
              "read_to_end": rep.get("stream_records_read_to_end")}
    if not out[s]["sha256"]:
        sys.exit(f"{s}: the player report names no stream sha256")
print(json.dumps(out, indent=1))
EOF

echo "== 2 the device parameters"
DXVK_LOG=${DXVK_LOG:-}
if [ -z "$DXVK_LOG" ]; then
  (
    export SA2_PLAY_OUT=$R/play-out SA2_DXVK_LOG_PATH=$R/dxvk-params-log OUT=$OUT
    mkdir -p "$SA2_DXVK_LOG_PATH"
    source "$ROOT/host/player/player_env.sh"
    "$OUT/sa2_synth_stream" "$R/synth.sa2d3d9" "$R/synth.expect.tsv" > /dev/null &&
    "$OUT/sa2_player" "$R/synth.sa2d3d9" --allow-notes --stage 0 --report "$R/dxvk-params.json" > "$R/dxvk-params.txt" 2>&1
  ) || bad "the DXVK run for its log"
  DXVK_LOG=$R/dxvk-params-log/sa2_player_d3d9.log
fi
first=$(echo $STREAMS | cut -d' ' -f1)
"$PY" "$HERE/test/check_params.py" --dxvk-log "$DXVK_LOG" --device-report "$R/$first/d3d9gpu.json" \
  --extract-manifest "$OUT/d3d9gpu-gen/refs/extract_manifest.json" --out "$R/params.json" | sed 's/^/   /'
[ "${PIPESTATUS[0]}" = 0 ] || { bad "check_params"; echo "STATE_CENSUS_FAIL"; exit 1; }

echo "== 3 the recorded reflection"
"$PY" "$HERE/test/shader_record.py" --scan "$SCAN" --disasm "${DISASM:-$REPO/private/generated/s5/next/D/disasm}" \
    --streams $STREAMS --out "$R/shader-record.json" > "$R/shader-record.txt" 2>&1
[ $? = 0 ] || bad "the shader record ($R/shader-record.txt)"
tail -1 "$R/shader-record.txt" | sed 's/^/   /'

echo "== 4 the state check"
dumps=()
for s in $STREAMS; do dumps+=("$R/$s/d3d9gpu.keys.json"); done
ruling=()
[ -n "${RULING:-}" ] && ruling=(--residue-ruling "$RULING")
"$OUT/d3d9gpu_state_check" --params "$R/params.json" --defaults --audit "$OUT/d3d9gpu-gen/refs/render_state_audit.json" \
    --draw-tests --self-test --domain --dumps "${dumps[@]}" --shader-record "$R/shader-record.json" --controls \
    --stream-identity "$R/streams.json" --device-binary "$OUT/sa2_player_gpu" \
    "${ruling[@]}" --out "$R/state-check.json" > "$R/state-check.txt" 2>&1
crc=$?
grep -vE "^dxvk (warn|info)" "$R/state-check.txt" | sed 's/^/   /' | tail -40
marker=$(tail -1 "$R/state-check.txt")
# every pipeline tuple evaluated with a recorded pixel shader and a recorded vertex shader (the
# confirm's residual 5; the check fails it too)
"$PY" - "$R/state-check.json" <<'EOF' || bad "the census's reflection does not cover every pipeline tuple"
import json, sys
c = json.load(open(sys.argv[1]))["census"]
n = c["pipeline_tuples"]
print(f"   reflection: {c['tuples_with_recorded_reflection']} of {n} pipeline tuples with a recorded pixel shader, "
      f"{c['tuples_with_recorded_vs_signature']} with a recorded vertex shader; the device's own the record's on "
      f"{c['shaders_held_to_the_record']} created shaders, {c['reflection_differs_tuples']} tuples differ")
sys.exit(0 if n and c["tuples_with_recorded_reflection"] == n and c["tuples_with_recorded_vs_signature"] == n
         and c["reflection_differs_tuples"] == 0 and c["shaders_held_to_the_record"] > 0 else 1)
EOF
case "$marker" in
  STATE_CHECK_OK) ;;
  STATE_CHECK_PENDING_USER*) [ $crc = 3 ] || bad "the state check's exit $crc";;
  STATE_CHECK_FALLBACK_OWED*) [ $crc = 4 ] || bad "the state check's exit $crc";;
  *) bad "the state check: $marker";;
esac
# a test leg is the check's to name: with CAPS_OFF its marker must name that one, and without it
# must name none
case "$marker" in
  *" TEST_LEG="*) [ -n "$CAPS_OFF" ] && [ "${marker##* TEST_LEG=}" = "$CAPS_OFF" ] ||
                  bad "the check names the test leg ${marker##* TEST_LEG=}, and CAPS_OFF is '$CAPS_OFF'";;
  *) [ -z "$CAPS_OFF" ] || bad "CAPS_OFF=$CAPS_OFF, and the check's verdict is no test leg: $marker";;
esac
# the ruling the check took is the one passed (none without RULING), and what it owes is what the
# devices' own decisions owe: BORDER where a keys file holds a fallback BORDER sampler, DEPTH_CLAMP
# where a device report counts R_DEPTH_CLAMP keys and the ruling is "patch" (the I6 census review's
# NIT 6: the marker's prefix alone would let either drift)
"$PY" - "$R/state-check.json" "${RULING:-}" $STREAMS <<'EOF' || bad "the check's ruling or owed items"
import json, sys
from pathlib import Path
c = json.load(open(sys.argv[1]))
want_ruling = json.load(open(sys.argv[2]))["ruling"] if sys.argv[2] else None
r = Path(sys.argv[1]).parent
border = any(x["fallback_border"] for s in sys.argv[3:] for x in json.load(open(r / s / "d3d9gpu.keys.json"))["samplers"])
clamp = any(json.load(open(r / s / "d3d9gpu.json"))["pipeline"]["fallback_owed"]["depth_clamp_keys"] for s in sys.argv[3:])
want_owed = (["BORDER"] if border else []) + (["DEPTH_CLAMP"] if clamp and want_ruling == "patch" else [])
print(f"   ruling: {c['ruling']} ({sys.argv[2] or 'none passed'}); fallback owed: {c['fallback_owed']}, the devices' "
      f"decisions owe {want_owed}")
sys.exit(0 if c["ruling"] == want_ruling and c["fallback_owed"] == want_owed else 1)
EOF

echo "== 5 the projection onto the census"
proj=()
for s in $STREAMS; do proj+=("$s=$R/$s/d3d9gpu.keys.json"); done
"$PY" "$HERE/test/scan_projection.py" --dumps "${proj[@]}" --scan "$SCAN" --out "$R/scan-projection.json" | sed 's/^/   /'
[ "${PIPESTATUS[0]}" = 0 ] || bad "the scan projection"

if [ $fail = 0 ] && [ "$marker" = STATE_CHECK_OK ]; then echo "STATE_CENSUS_OK"; exit 0; fi
if [ $fail = 0 ] && [ "${marker#STATE_CHECK_PENDING_USER}" != "$marker" ]; then
  echo "STATE_CENSUS_PENDING_USER${marker#STATE_CHECK_PENDING_USER}"
  exit 3
fi
if [ $fail = 0 ] && [ "${marker#STATE_CHECK_FALLBACK_OWED}" != "$marker" ]; then
  echo "STATE_CENSUS_FALLBACK_OWED${marker#STATE_CHECK_FALLBACK_OWED}"
  exit 4
fi
echo "STATE_CENSUS_FAIL"
exit 1
