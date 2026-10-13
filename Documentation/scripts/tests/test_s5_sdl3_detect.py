"""The stock-SDL gate's detection test: host 2 reads the SDL patch's two capabilities at run time,
on lavapipe, headless.

The user's ruling of 2026-10-05 (Documentation/decisions/2026-10-05-S5-user-rulings-3.md, item 3):
"Add the detection test to the stock-SDL gate now, with both capabilities forced off. The named
residues come with the stock configuration once host 2 draws." The capabilities are the patch's
CLAMP_TO_BORDER and depth clamp beside depth clip (patches/sdl3/README.md). Host 2's device
(host/d3d9gpu/d9g_gpu_sdl.cpp) reads them from SDL, uses them less any a test run forces off
(SA2_D3D9GPU_TEST_RUN=1 with SA2_D3D9GPU_CAPS_OFF), and its report says what it detected, what it
used and which shader fallback it owes.

host/d3d9gpu/build.sh builds host 2 (sa2_player_gpu) twice here, once against each prefix
(SA2_S5_INSTALL): the patched install (tools.sdl3) and the stock one (tools.sdl3-stock). The player
loads its SDL by DT_RPATH and holds it to the contract its build generated, so host 2 runs the stock
SDL only as a stock build. Each replays state_census.sh's story stream, the census stream that
holds a BORDER sampler, at stage 3 on lavapipe (SDL_VIDEO_DRIVER=offscreen,
SA2_D3D9GPU_PRESENT=none); every replay's report must name the lavapipe device, and so must DXVK's
log, so a run that reached another GPU fails.

Each clause is a check that can fail:

  1. the patched install with both capabilities forced off by a test run
     (SA2_D3D9GPU_CAPS_OFF=border,depth-clamp): both detected, neither used, both fallbacks owed
     and the override named, in the report and the keys file; the pipeline block counts the
     BORDER sampler and every depth-clamp key as the fallback's, none as the patch's;
  2. the stock prefix, no forcing: the player loads the stock library (its report's
     files.libSDL3 is the stock pin) and prints the stock mark; the device reads an unmarked SDL
     with neither capability present or detected, uses neither, owes both fallbacks and names no
     override. Its keys file and pipeline block are clause 1's, but for the override and the
     binary, so the forced-off leg maps every draw as the stock prefix does. The state check on
     the stock prefix's own keys file reads STATE_CHECK_FALLBACK_OWED BORDER DEPTH_CLAMP with no
     test leg (exit 4, no failure), its census clause 1's tuple for tuple, its open items the
     control's. And the device is one build on both prefixes: each object of libsa2_d3d9gpu.a is
     byte-identical between the two builds once debug information and local symbols are stripped,
     so nothing in the device can choose by the prefix's headers (the patch's property macros
     are defined only in the patched install's): the detection is at run time;
  3. the state check (d3d9gpu_state_check) on clause 1's keys file, with check_params.py's
     parameters from DXVK's log on lavapipe and that run's report, and ruling 3's DEPTH_CLAMP
     record (Documentation/decisions/2026-10-05-S5-depth-clamp-ruling.json): exit 4,
     STATE_CHECK_FALLBACK_OWED BORDER DEPTH_CLAMP named a test leg (TEST_LEG=border,depth-clamp),
     no failure, never OK; its open items are the control's;
  4. the control: the same replay on the patched install without forcing: both detected and
     used, nothing owed, the BORDER sampler and every depth-clamp key the patch's; the state
     check's verdict owes nothing and names no test leg.

DXVK's log for the parameters comes from d3d9gpu_clear_matrix through the patched install's DXVK
on lavapipe (the clear matrix's DXVK leg, which build.sh builds), as state_census.sh step 2 takes
a DXVK run's log. As in state_census.sh, shader_record.py records the story shaders' masks and
input signatures from their bytecode and dxbc-spirv disassembly, requiring both readings to agree.
Every state check takes that independent record; clauses 2 and 3 hold any remaining OPEN-1 items
equal to the control's.

Host 2 does not draw yet: a replay maps every draw and draws none. Its own test
(test_host_2_draws_nothing_yet_the_stock_residues_are_not_due) fails on the first stock replay
that draws, naming the residues the ruling then makes due; the clauses above go on reporting
detection.

Not held here: the named BORDER and DEPTH_CLAMP residues of a stock-configuration replay, which
come once host 2 draws (the ruling); host 2's refusal when the SDL's mark and its device
properties disagree; and detection by the device properties rather than by the revision mark,
which lavapipe cannot tell apart, since it supports both capabilities (patches/sdl3/README.md
lists all three as owed).

A missing lavapipe ICD, install, pin or stream fails; nothing skips. The quiet window: each
library hashes to its pin before anything runs (a half-written or replaced install does not). The
repository is this file's own: every subprocess gets this checkout as SA2_REPO and its own
SA2_S5_INSTALL, and no inherited SA2_, SDL_, DXVK_ or VK_ variable, PYTHON, CC or CXX.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
D9G = ROOT / "host" / "d3d9gpu"
PLAYER_ENV = ROOT / "host" / "player" / "player_env.sh"
PINS = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]
PATCHED = ROOT / Path(PINS["sdl3"]["library"]).parent.parent
STOCK = ROOT / PINS["sdl3-stock"]["prefix"]
LVP = Path("/usr/share/vulkan/icd.d/lvp_icd.json")
# state_census.sh's story stream: the census stream with a BORDER sampler
STORY = ROOT / "private" / "generated" / "s5" / "ship3" / "threads-runs" / "story-default" / "stream.sa2s"
RULING = ROOT / "Documentation" / "decisions" / "2026-10-05-S5-depth-clamp-ruling.json"
BOTH = "border,depth-clamp"
PY = sys.executable

ENV = {k: v for k, v in os.environ.items()
       if not k.startswith(("SA2_", "SDL_", "DXVK_", "VK_")) and k not in ("SDL3_DYNAMIC_API", "PYTHON", "CC", "CXX")}
ENV.update(SA2_REPO=str(ROOT))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need_inputs() -> None:
    assert LVP.exists(), f"the lavapipe ICD {LVP} is missing: the detection test runs on it (it fails, it does not skip)"
    assert STORY.exists(), f"no story stream at {STORY}: the detection test replays it (it fails, it does not skip)"
    assert PINS["sdl3"].get("patches"), "tools/pins.json pins no SDL patch: the shared install is stock SDL"
    assert PINS["sdl3-stock"]["library"] == f"{PINS['sdl3-stock']['prefix']}/lib/libSDL3.so.0", PINS["sdl3-stock"]
    for prefix, pin in ((PATCHED, PINS["sdl3"]["library_sha256"]), (STOCK, PINS["sdl3-stock"]["library_sha256"])):
        lib = prefix / "lib" / "libSDL3.so.0"
        assert lib.exists() and (prefix / "include" / "dxvk" / "d3d9.h").exists(), f"no SDL3 and DXVK headers under {prefix}"
        assert sha256(lib.resolve()) == pin, f"{lib} is not the pinned build (mid-rebuild, or the pin moved): nothing runs against it"
    dxvk = PATCHED / "lib" / "x86_64-linux-gnu" / "libdxvk_d3d9.so.0"   # player_env.sh's SA2_DXVK_D3D9 there
    assert ROOT / PINS["dxvk"]["library"] == dxvk and sha256(dxvk.resolve()) == PINS["dxvk"]["library_sha256"], \
        f"{dxvk} is not the pinned DXVK: its log gives the state check's parameters"


def run(cmd, env, timeout=1800) -> subprocess.CompletedProcess:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env, timeout=timeout)


def lavapipe(prefix: Path, out: Path, build: Path, **extra) -> dict:
    """The environment of a headless lavapipe run, player_env.sh's under it."""
    return dict(ENV, SA2_S5_INSTALL=str(prefix), OUT=str(build), SA2_PLAY_OUT=str(out / "play-out"),
                SA2_DXVK_LOG_PATH=str(out / "dxvk-log"), SDL_VIDEO_DRIVER="offscreen", SDL_AUDIO_DRIVER="dummy",
                VK_DRIVER_FILES=str(LVP), VK_ICD_FILENAMES=str(LVP), **extra)


def with_player_env(cmd: list) -> list:
    return ["bash", "-c", 'source "$1" && shift && exec "$@"', "player_env", PLAYER_ENV, *cmd]


@pytest.fixture(scope="module")
def builds(tmp_path_factory) -> dict[str, Path]:
    """host/d3d9gpu/build.sh against each prefix, the two at once."""
    need_inputs()
    base = tmp_path_factory.mktemp("detect-builds")
    procs = {}
    for name, prefix in (("patched", PATCHED), ("stock", STOCK)):
        log = (base / f"{name}.log").open("w")
        procs[name] = (subprocess.Popen(["bash", str(D9G / "build.sh")], stdout=log, stderr=subprocess.STDOUT,
                                        env=dict(ENV, SA2_S5_INSTALL=str(prefix), OUT=str(base / name))), log)
    out = {}
    for name, (p, log) in procs.items():
        rc = p.wait(timeout=3600)
        log.close()
        text = (base / f"{name}.log").read_text()
        assert rc == 0 and "D3D9GPU_BUILD_OK" in text, text[-4000:]
        contract = json.loads((base / name / "expected-gpu" / "expectations.json").read_text())
        assert contract["sdl3_configuration"] == name, contract
        out[name] = base / name
    return out


def revision(build: Path) -> str:
    """The SDL revision the build's contract holds its player to."""
    return json.loads((build / "expected-gpu" / "expectations.json").read_text())["contract"]["sdl_revision"]


class Replay:
    def __init__(self, d: Path, r: subprocess.CompletedProcess):
        self.dir = d
        self.proc = r
        self.device = json.loads((d / "d3d9gpu.json").read_text())
        self.keys = json.loads((d / "d3d9gpu.keys.json").read_text())
        self.player = json.loads((d / "report.json").read_text())


def replay(build: Path, out: Path, prefix: Path, caps_off: str | None) -> Replay:
    """The story stream at stage 3 through host 2, headless on lavapipe; a test run with `caps_off`
    forced off when given. Its keys file is out/story/d3d9gpu.keys.json (the state check names a
    keys file's stream by its directory)."""
    d = out / "story"
    d.mkdir(parents=True)
    env = lavapipe(prefix, out, build, SA2_D3D9GPU_PRESENT="none", SA2_D3D9GPU_REPORT=str(d / "d3d9gpu.json"))
    if caps_off:
        env.update(SA2_D3D9GPU_TEST_RUN="1", SA2_D3D9GPU_CAPS_OFF=caps_off)
    r = run(with_player_env([build / "sa2_player_gpu", STORY, "--stage", "3", "--report", d / "report.json"]), env)
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    rep = Replay(d, r)
    assert (rep.player["exit_status"], rep.player["complete"], rep.player["stream_records_read_to_end"]) == (0, True, True), \
        {k: rep.player.get(k) for k in ("exit_status", "complete", "phase", "stop", "errors")}
    assert rep.device["complete"] and rep.device["fault"] is None and rep.device["not_implemented"]["count"] == 0
    sg = rep.device["sdl_gpu"]
    assert (sg["driver"], sg["driver_name"]) == ("vulkan", "llvmpipe") and sg["device_name"].startswith("llvmpipe"), \
        f"the replay ran on {sg['device_name']!r} ({sg['driver_name']!r}), not lavapipe"
    assert rep.device["draws"]["total"] > 0
    assert rep.keys["complete"] and rep.keys["map_faults"] == 0
    assert rep.device["pipeline"]["keys_sha256"] == sha256(d / "d3d9gpu.keys.json")
    return rep


@pytest.fixture(scope="module")
def forced_off(builds, tmp_path_factory) -> Replay:
    return replay(builds["patched"], tmp_path_factory.mktemp("forced-off"), PATCHED, BOTH)


@pytest.fixture(scope="module")
def control(builds, tmp_path_factory) -> Replay:
    return replay(builds["patched"], tmp_path_factory.mktemp("control"), PATCHED, None)


@pytest.fixture(scope="module")
def stock(builds, tmp_path_factory) -> Replay:
    return replay(builds["stock"], tmp_path_factory.mktemp("stock"), STOCK, None)


@pytest.fixture(scope="module")
def dxvk_log(builds, tmp_path_factory) -> Path:
    """DXVK's log on lavapipe: the clear matrix through the patched install's DXVK."""
    out = tmp_path_factory.mktemp("dxvk")
    build = builds["patched"]
    r = run(with_player_env(["sh", "-c", 'exec "$1" "$SA2_DXVK_D3D9" --hidden', "matrix", build / "d3d9gpu_clear_matrix"]),
            lavapipe(PATCHED, out, build))
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    log = out / "dxvk-log" / "d3d9gpu_clear_matrix_d3d9.log"
    assert log.exists() and "Creating device" in log.read_text(), sorted(p.name for p in (out / "dxvk-log").iterdir())
    device = log.read_text().split("Creating device:", 1)[1].strip().splitlines()[0]
    assert "llvmpipe" in device, f"DXVK ran on {device!r}, not lavapipe"
    return log


@pytest.fixture(scope="module")
def shader_record(tmp_path_factory) -> Path:
    """The story shaders' independent token and disassembly readings, as in state_census.sh."""
    out = tmp_path_factory.mktemp("shader-record") / "shader-record.json"
    r = run([PY, D9G / "test" / "shader_record.py", "--streams", "story", "--out", out], ENV)
    assert r.returncode == 0 and last(r) == "SHADER_RECORD_OK", r.stdout + r.stderr
    return out


def state_check(build: Path, rep: Replay, log: Path, shader_record: Path) -> tuple[subprocess.CompletedProcess, dict]:
    """The state check on one replay's keys file, with its parameters from DXVK's log and that
    replay's device report and independent shader record, under ruling 3's DEPTH_CLAMP record."""
    params = rep.dir.parent / "params.json"
    r = run([PY, D9G / "test" / "check_params.py", "--dxvk-log", log, "--device-report", rep.dir / "d3d9gpu.json",
             "--extract-manifest", build / "d3d9gpu-gen" / "refs" / "extract_manifest.json", "--out", params], ENV)
    assert r.returncode == 0, r.stdout + r.stderr
    out = rep.dir.parent / "state-check.json"
    r = run([build / "d3d9gpu_state_check", "--params", params, "--dumps", rep.dir / "d3d9gpu.keys.json",
             "--device-binary", build / "sa2_player_gpu", "--shader-record", shader_record,
             "--residue-ruling", RULING, "--out", out], ENV)
    return r, json.loads(out.read_text())


@pytest.fixture(scope="module")
def forced_off_check(builds, forced_off, dxvk_log, shader_record):
    return state_check(builds["patched"], forced_off, dxvk_log, shader_record)


@pytest.fixture(scope="module")
def control_check(builds, control, dxvk_log, shader_record):
    return state_check(builds["patched"], control, dxvk_log, shader_record)


def last(r: subprocess.CompletedProcess) -> str:
    """A state check's marker, its last line."""
    return (r.stdout.splitlines() or [""])[-1]


def pending(marker: str) -> str:
    """The open items a marker names after PENDING_USER, "" for none."""
    m = re.search(r"(?:^STATE_CHECK_| )PENDING_USER((?: [A-Z_]+)+?)(?: TEST_LEG=\S+)?$", marker)
    return m.group(1) if m else ""


def controls_pending(control_check) -> str:
    """The control's open items, once its own state check has owed nothing and failed nothing."""
    rc, dc = control_check
    assert rc.returncode in (0, 3) and dc["fallback_owed"] == [] and dc["failures"] == [], \
        f"the control's state check (exit {rc.returncode}): {last(rc)!r}, owed {dc['fallback_owed']}, failures {dc['failures'][:3]}"
    return pending(last(rc))


def test_both_forced_off_on_the_patched_install_are_detected_unused_and_owed(builds, forced_off):
    sg = forced_off.device["sdl_gpu"]
    assert sg["sdl_revision"] == revision(builds["patched"]) and sg["sdl_revision"].endswith(f" ({PINS['sdl3']['vendor_info']})")
    assert (sg["sdl_patched"], sg["sdl_capabilities_present"]) == (True, True)
    assert sg["detected"] == {"sampler_clamp_to_border": True, "depth_clip_and_clamp": True}
    assert (sg["sampler_clamp_to_border"], sg["depth_clip_and_clamp"]) == (False, False)
    assert sg["fallback_owed"] == {"border": True, "depth_clamp": True}
    assert sg["capability_override"] == BOTH
    assert (forced_off.device["env"]["SA2_D3D9GPU_TEST_RUN"], forced_off.device["env"]["SA2_D3D9GPU_CAPS_OFF"]) == ("1", BOTH)
    assert f"CAPABILITY OVERRIDE (test run): {BOTH} forced off" in forced_off.proc.stderr
    dev = forced_off.keys["device"]
    assert (dev["sampler_clamp_to_border"], dev["depth_clip_and_clamp"], dev["capability_override"]) == (False, False, BOTH)
    p = forced_off.device["pipeline"]
    assert p["patch"] == {"border_samplers": 0, "depth_clamp_keys": 0}, p["patch"]
    assert p["fallback_owed"]["border_samplers"] >= 1, "the story stream holds no BORDER sampler"
    assert p["fallback_owed"]["depth_clamp_keys"] == p["distinct_keys"] > 0, p
    assert forced_off.player["files"]["libSDL3"]["sha256"] == PINS["sdl3"]["library_sha256"]


def test_the_stock_prefix_detects_neither_capability_and_owes_both(builds, stock, forced_off):
    lib = stock.player["files"]["libSDL3"]
    assert lib["sha256"] == PINS["sdl3-stock"]["library_sha256"], lib
    assert Path(lib["real_path"]).resolve().is_relative_to(STOCK.resolve()), lib
    rev = revision(builds["stock"])
    assert f"SDL3: {rev}: stock" in stock.proc.stdout.splitlines()
    sg = stock.device["sdl_gpu"]
    assert sg["sdl_revision"] == rev and f"{rev} ({PINS['sdl3']['vendor_info']})" == revision(builds["patched"])
    assert (sg["sdl_patched"], sg["sdl_capabilities_present"]) == (False, False)
    assert sg["detected"] == {"sampler_clamp_to_border": False, "depth_clip_and_clamp": False}
    assert (sg["sampler_clamp_to_border"], sg["depth_clip_and_clamp"]) == (False, False)
    assert sg["fallback_owed"] == {"border": True, "depth_clamp": True}
    assert sg["capability_override"] is None
    assert (stock.device["env"]["SA2_D3D9GPU_TEST_RUN"], stock.device["env"]["SA2_D3D9GPU_CAPS_OFF"]) == ("", "")
    assert stock.keys["device"]["capability_override"] is None
    # the forced-off leg is the stock configuration's path: every draw mapped as the stock prefix
    # maps it, the keys file and the pipeline block equal but for the override and the binary
    def bare(keys: dict) -> dict:
        k = json.loads(json.dumps(keys))
        k["device"].pop("capability_override")
        k.pop("library_sha256")
        return k
    assert bare(stock.keys) == bare(forced_off.keys)
    assert stock.keys["library_sha256"] != forced_off.keys["library_sha256"]
    skip = ("keys_file", "keys_sha256")
    assert {k: v for k, v in stock.device["pipeline"].items() if k not in skip} == \
        {k: v for k, v in forced_off.device["pipeline"].items() if k not in skip}


def test_the_state_check_on_the_stock_prefix_s_keys_owes_border_and_depth_clamp_with_no_test_leg(
        builds, stock, dxvk_log, forced_off_check, control_check, shader_record):
    r, d = state_check(builds["stock"], stock, dxvk_log, shader_record)
    marker = last(r)
    assert r.returncode == 4 and re.fullmatch(
        r"STATE_CHECK_FALLBACK_OWED BORDER DEPTH_CLAMP(?: PENDING_USER(?: [A-Z_]+)+)?", marker), r.stdout[-3000:]
    assert "STATE_CHECK_OK" not in r.stdout
    assert d["fallback_owed"] == ["BORDER", "DEPTH_CLAMP"] and d["test_leg"] is None and d["failures"] == []
    assert d["ruling"] == json.loads(RULING.read_text())["ruling"] == "patch"
    c = d["census"]
    assert c["fallback_border_sampler_tuples"] >= 1 and c["border_mapped_sampler_tuples"] == 0, c
    assert c["depth_clamp_tuples"] == c["pipeline_tuples"] > 0 and c["depth_clamp_property_tuples"] == 0, c
    # the forced-off leg's census, tuple class for tuple class: the test leg names the marker and
    # changes nothing the check counts
    def counts(census: dict) -> dict:
        k = json.loads(json.dumps(census))
        for s in k["streams"].values():
            for f in ("path", "keys_sha256", "library_sha256"):
                s.pop(f)
        return k
    assert counts(c) == counts(forced_off_check[1]["census"])
    assert pending(marker) == controls_pending(control_check), marker


def test_the_device_is_one_build_on_both_prefixes_so_detection_is_at_run_time(builds, tmp_path):
    """The patch's property macros are defined only in the patched install's SDL headers: a device
    that chose by them would read the capabilities on the patched build and not on the stock one,
    and pass every clause above. So each of the device's objects (libsa2_d3d9gpu.a's members) must
    be byte-identical between the two builds, once debug information (which names the prefix's
    headers) and local symbols (a switch table's numbered name) are stripped."""
    members = {}
    for name, build in builds.items():
        r = run(["ar", "t", build / "libsa2_d3d9gpu.a"], ENV)
        assert r.returncode == 0, r.stderr
        members[name] = r.stdout.split()
        assert sorted(members[name]) == sorted(o.name for o in (build / "obj" / "d9g").glob("*.o")), members[name]
    assert members["patched"] == members["stock"] and "d9g_gpu_sdl.o" in members["patched"], members
    differ = []
    for o in members["patched"]:
        stripped = []
        for name, build in builds.items():
            dst = tmp_path / name / o
            dst.parent.mkdir(exist_ok=True)
            r = run(["objcopy", "--strip-debug", "--discard-all", build / "obj" / "d9g" / o, dst], ENV)
            assert r.returncode == 0, r.stderr
            stripped.append(dst.read_bytes())
        if stripped[0] != stripped[1]:
            differ.append(o)
    assert differ == [], f"host 2's device differs between the patched and the stock builds in {differ}: it chooses by the prefix it was built against, not at run time"


def test_the_state_check_on_keys_made_with_both_forced_off_owes_border_and_depth_clamp_never_ok(
        forced_off_check, control_check):
    r, d = forced_off_check
    marker = last(r)
    assert r.returncode == 4 and re.fullmatch(
        rf"STATE_CHECK_FALLBACK_OWED BORDER DEPTH_CLAMP(?: PENDING_USER(?: [A-Z_]+)+)? TEST_LEG={BOTH}", marker), r.stdout[-3000:]
    assert "STATE_CHECK_OK" not in r.stdout
    assert d["fallback_owed"] == ["BORDER", "DEPTH_CLAMP"] and d["test_leg"] == BOTH and d["failures"] == []
    assert d["ruling"] == json.loads(RULING.read_text())["ruling"] == "patch"
    c = d["census"]
    assert c["fallback_border_sampler_tuples"] >= 1 and c["border_mapped_sampler_tuples"] == 0, c
    assert c["depth_clamp_tuples"] == c["pipeline_tuples"] > 0 and c["depth_clamp_property_tuples"] == 0, c
    # its open items are the control's: what the override changes is owed, and nothing else moves
    assert pending(marker) == controls_pending(control_check), marker


def test_the_control_without_forcing_owes_nothing(builds, control, control_check):
    sg = control.device["sdl_gpu"]
    assert sg["sdl_revision"] == revision(builds["patched"])
    assert (sg["sdl_patched"], sg["sdl_capabilities_present"]) == (True, True)
    assert sg["detected"] == {"sampler_clamp_to_border": True, "depth_clip_and_clamp": True}
    assert (sg["sampler_clamp_to_border"], sg["depth_clip_and_clamp"]) == (True, True)
    assert sg["fallback_owed"] == {"border": False, "depth_clamp": False}
    assert sg["capability_override"] is None and control.keys["device"]["capability_override"] is None
    assert "CAPABILITY OVERRIDE" not in control.proc.stderr
    p = control.device["pipeline"]
    assert p["fallback_owed"] == {"border_samplers": 0, "border_stage_draws": 0, "depth_clamp_keys": 0}, p["fallback_owed"]
    assert p["patch"]["border_samplers"] >= 1 and p["patch"]["depth_clamp_keys"] == p["distinct_keys"] > 0, p["patch"]
    r, d = control_check
    marker = last(r)
    assert (r.returncode, marker) == (0, "STATE_CHECK_OK") or (
        r.returncode == 3 and pending(marker) and marker == f"STATE_CHECK_PENDING_USER{pending(marker)}"), r.stdout[-3000:]
    assert d["fallback_owed"] == [] and d["test_leg"] is None and d["failures"] == []
    c = d["census"]
    assert c["fallback_border_sampler_tuples"] == 0 and c["border_mapped_sampler_tuples"] >= 1, c
    assert c["depth_clamp_tuples"] == 0 and c["depth_clamp_property_tuples"] == c["pipeline_tuples"] > 0, c


def test_host_2_draws_nothing_yet_the_stock_residues_are_not_due(stock):
    """The ruling's deferred half: the stock configuration's named BORDER and DEPTH_CLAMP residues
    come once host 2 draws. This test fails on the first stock replay that draws, so that half
    cannot pass unnoticed; the clauses above go on reporting detection meanwhile."""
    draws = stock.device["draws"]
    assert draws["drawn"] == 0, (
        f"host 2 drew {draws['drawn']} of {draws['total']} draws on the stock prefix: the stock configuration's named "
        "BORDER and DEPTH_CLAMP residues are now due in this gate (Documentation/decisions/2026-10-05-S5-user-rulings-3.md, "
        "item 3; patches/sdl3/README.md)")
