"""host/d3d9gpu, the second host's Direct3D 9 (R2a), held without a GPU or a window.

Built here with g++ against the pinned DXVK Native and SDL3 headers
(private/s5-tools/install; a missing install fails, it does not skip):

  * gen_com.py's COM stubs follow the recorder's slot order: every interface's methods,
    read from the pinned d3d9.h, are host/player/names.h's, slot for slot;
  * the unit test (test/test_d3d9gpu.cpp) passes on the CPU model: formats and pitches
    against host/player/texcodec, the X8 fill, D3DCOLOR, declarations, the fan order, the
    shader version rules, lifetimes and the container rule, DXVK's answers, clears (full,
    partial, depth with the stencil kept, the faults), locks, draws and frames, and every
    shader translated at its creation by DXVK's compiler (R1: the reflection, the bytecode dump,
    a refusal the call's failure);
  * every case of test/clear_cases.json, each worked by hand from DXVK's Clear, is what
    the device's and the census's clear rule gives;
  * the clear matrix (test/clear_matrix.h) on the CPU model answers as DXVK does, and three
    of its images are the ones computed here pixel by pixel;
  * a scripted device session's report has its schema, its counts and its not-implemented
    names, and the SDL patch's capabilities as the CPU model reports them (neither, as a stock
    SDL3), with the fallback owed for both and no test run's override;
  * each player binary's expectations contract is its host's alone
    (host/player/generate_expectations.py --host): the DXVK player's is exactly the five keys
    test_bad_config_contract.py holds, the GPU player's SDL's three, made without DXVK's
    library or dxvk.conf; and host/player/player.cpp compiles in each configuration against
    its own contract and not against the other's, the DXVK configuration naming nothing of
    the second host's and the GPU one nothing of DXVK's expectations;
  * each build script's names check and contract check, cut from the script as it is: the
    paths a build embeds (the working directory as a whole string only) are no defect, a
    second-host name in sa2_player, DXVK's library name or pinned hash in sa2_player_gpu,
    or another contract is one, and each exits 2 as the build's other checks do;
  * host/d3d9gpu/build_before.sh refuses a reused clone whose host/ or tools/pins.json is
    not the commit's (a change, an untracked file, or one an ignore rule hides), or whose
    status git cannot read (a corrupt index), before it builds anything, and builds from
    the clone otherwise;
  * host/d3d9gpu/test_d3d9gpu.sh's step 7, cut from the script as it is and run with
    stand-ins for compare_hosts.py and build_before.sh, routes each dxvk-unchanged exit:
    UNCHANGED passes the step and runs nothing else, CHANGED builds the before (unless it
    has its record) and runs the five A/Bs, each of which must pass, and any other exit
    fails the step without an A/B.

The static GPU player itself (sa2_player_gpu, host/d3d9gpu/build.sh, with its build-time
checks) is built by test_s5_d3d9gpu_state.py's fixture. The GPU end (the clear matrix
through DXVK and through SDL_gpu, the synthetic stream on both hosts, headless, the boot
comparison and its controls) is host/d3d9gpu/test_d3d9gpu.sh.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
D9G = ROOT / "host" / "d3d9gpu"
PLAYER = ROOT / "host" / "player"
INSTALL = ROOT / "private" / "s5-tools" / "install"
GXX = shutil.which(os.environ.get("CXX", "g++"))
sys.path.insert(0, str(D9G))
sys.path.insert(0, str(PLAYER))
import gen_com  # noqa: E402
import generate_expectations as GE  # noqa: E402
import test_bad_config_contract as BCC  # noqa: E402

CORE = ["d9g_device_draw.cpp", "d9g_draw_state.cpp", "d9g_device.cpp", "d9g_resources.cpp", "d9g_format.cpp", "d9g_clear.cpp", "d9g_report.cpp", "d9g_pipeline.cpp",
        "d9g_sampler.cpp", "d9g_draw.cpp"]
# names.h's type numbers for gen_com.py's interfaces.
NAMES_TYPE = {"IDirect3D9": 0, "IDirect3DDevice9": 1, "IDirect3DTexture9": 2, "IDirect3DSurface9": 5,
              "IDirect3DVertexBuffer9": 7, "IDirect3DIndexBuffer9": 8, "IDirect3DVertexDeclaration9": 9,
              "IDirect3DVertexShader9": 10, "IDirect3DPixelShader9": 11,
              # appended by gen_com.py after the nine, so their indices keep the device report's
              "IDirect3DCubeTexture9": 3, "IDirect3DVolumeTexture9": 4}


def need_install() -> None:
    if not GXX:
        pytest.fail("no g++")
    if not (INSTALL / "include" / "dxvk" / "d3d9.h").is_file() or not (INSTALL / "lib" / "libSDL3.so.0").exists():
        pytest.fail(f"no pinned DXVK Native headers and SDL3 under {INSTALL}: run private/s5-tools/build.sh")


def flags(gen: Path) -> list[str]:
    return ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wno-unused-parameter", "-I", str(gen), "-I", str(D9G),
            "-I", str(D9G / "test"), "-I", str(PLAYER), "-I", str(INSTALL / "include" / "dxvk"),
            "-I", str(INSTALL / "include")]


def compile_(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]


@pytest.fixture(scope="session")
def build(tmp_path_factory) -> Path:
    need_install()
    out = tmp_path_factory.mktemp("d3d9gpu")
    gen_com.generate(INSTALL / "include" / "dxvk" / "d3d9.h", out / "gen")
    # the pinned SDL tarball's Khronos headers, for the library's one Vulkan property query
    r = subprocess.run([sys.executable, str(D9G / "test" / "extract_refs.py"), "--out", str(out / "gen" / "refs")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # the shader translator the device runs at every shader's creation (R1), with the pinned dxbc-spirv
    # archive it links (shader/build_translator.sh prints the archive's path on its last line)
    r = subprocess.run(["bash", str(D9G / "shader" / "build_translator.sh"), str(out / "shader")], capture_output=True,
                       text=True, env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 0 and "D3D9GPU_SHADER_BUILD_OK" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
    archive = r.stdout.strip().splitlines()[-1].split()[-1]
    compile_([GXX, *flags(out / "gen"), f'-DSA2_TEST_TMP="{out}"', "-o", str(out / "test_d3d9gpu"),
              str(D9G / "test" / "test_d3d9gpu.cpp"), *(str(D9G / c) for c in CORE), str(D9G / "d9g_gpu_cpu.cpp"),
              str(PLAYER / "texcodec.cpp"), str(PLAYER / "sha256.cpp"), str(out / "shader" / "libd9g_shader.a"), archive,
              "-lpng", "-ldl"])
    return out


def test_the_generated_stubs_follow_the_recorder_s_slot_order(build, tmp_path):
    slots = json.loads((build / "gen" / "d9g_slots.json").read_text())
    assert list(slots) == list(NAMES_TYPE)
    src = tmp_path / "names.cpp"
    src.write_text('#include "names.h"\n#include <cstdio>\nint main() {\n'
                   + "".join(f'  for (unsigned s = 0; s < 130; s++) std::printf("{t}\\t%u\\t%s\\n", s, '
                             f'names::method_name({t}, s));\n' for t in NAMES_TYPE.values()) + "}\n")
    compile_([GXX, "-std=c++17", "-I", str(PLAYER), "-o", str(tmp_path / "names"), str(src)])
    rows = subprocess.run([str(tmp_path / "names")], capture_output=True, text=True, check=True).stdout.splitlines()
    names: dict[int, list[str]] = {}
    for row in rows:
        t, s, n = row.split("\t")
        if n != "?":
            names.setdefault(int(t), []).append(n)
    for iface, t in NAMES_TYPE.items():
        assert slots[iface] == names[t], iface
    assert len(slots["IDirect3DDevice9"]) == 119 and slots["IDirect3DDevice9"][43] == "Clear"
    stubs = (build / "gen" / "d9g_com_stubs.h").read_text()
    assert "TestCooperativeLevel() override { ::d9g::not_implemented(1, 3); return D3DERR_INVALIDCALL; }" in stubs
    assert "PreLoad() override { ::d9g::not_implemented(2, 9); }" in stubs
    assert "GetVolumeLevel(UINT Level, IDirect3DVolume9** ppVolumeLevel) override { ::d9g::not_implemented(10, 18); " in stubs


def test_the_unit_test_passes_on_the_cpu_model(build):
    r = subprocess.run([str(build / "test_d3d9gpu")], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout[-3000:]
    last = r.stdout.splitlines()[-1]
    assert last.startswith("TEST_D3D9GPU_OK ") and int(last.split()[1]) > 3000


def test_every_clear_case_is_dxvk_s(build):
    cases = json.loads((D9G / "test" / "clear_cases.json").read_text())["cases"]
    assert len(cases) >= 20
    lines = []
    for c in cases:
        rects = c["rects"] or []
        lines.append(" ".join(str(v) for v in [*c["viewport"], *c["scissor"], int(c["scissor_on"]), c["count"],
                                               int(c["rects"] is not None), len(rects),
                                               *(x for r in rects for x in r), *c["image"]]))
    r = subprocess.run([str(build / "test_d3d9gpu"), "--clear-cases"], input="\n".join(lines) + "\n",
                       capture_output=True, text=True, check=True)
    got = [json.loads(x) for x in r.stdout.splitlines()]
    assert len(got) == len(cases)
    for c, g in zip(cases, got):
        assert (g["regions"], g["images"]) == (c["regions"], c["images"]), c["name"]


def fnv_constant_image(w: int, h: int, paint) -> str:
    """FNV-1a 64 of a w x h BGRA image whose pixel (x, y) is paint(x, y) (4 bytes)."""
    v = 0xcbf29ce484222325
    p = 0x100000001b3
    m = (1 << 64) - 1
    for y in range(h):
        for x in range(w):
            for b in paint(x, y):
                v = ((v ^ b) * p) & m
    return f"{v:016x}"


def test_the_clear_matrix_on_the_cpu_model_answers_as_dxvk(build):
    r = subprocess.run([str(build / "test_d3d9gpu"), "--clear-matrix"], capture_output=True, text=True, check=True)
    rows = {line.split("\t")[0]: line.split("\t")[1:] for line in r.stdout.splitlines()}
    assert len(rows) == 23
    inv = "8876086c"
    assert rows["z_without_depth_stencil"][0].split(",")[1] == inv
    assert rows["scene_answers"][0] == f"00000000,{inv},00000000,{inv}"
    assert rows["get_render_target_data_answers"][0] == f"{inv},{inv},00000000"
    assert rows["set_render_target_answers"][0] == f"{inv},{inv},00000000,00000000"
    # Nothing changed the back buffer in these: the base colour of every case.
    unchanged = {rows[k][1] for k in ("depth_only", "zero_count_with_rects", "z_without_depth_stencil",
                                      "viewport_past_image", "viewport_empty")}
    assert len(unchanged) == 1
    base = bytes((0x10, 0x10, 0x10, 0xFF))
    assert unchanged == {fnv_constant_image(1280, 720, lambda x, y: base)}
    # DXVK's scissor rule moves the offset and not the extent (clear_cases.json
    # scissor_moves_offset_not_extent): x 300..899, y 200..599.
    blue = bytes((0xFF, 0x00, 0x00, 0xFF))
    want = fnv_constant_image(1280, 720, lambda x, y: blue if 300 <= x < 900 and 200 <= y < 600 else base)
    assert rows["scissor_and_viewport"][1] == want


def test_a_device_session_s_report_has_its_shape(build, tmp_path):
    path = tmp_path / "private" / "d3d9gpu.json"
    path.parent.mkdir()
    r = subprocess.run([str(build / "test_d3d9gpu"), "--report", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    d = json.loads(path.read_text())
    assert (d["schema"], d["complete"], d["phase"], d["fault"], d["failure"]) == \
        ("sa2-d3d9gpu-report/v1", True, "destroyed", None, None)
    assert d["draws"]["unmapped"] == {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}}
    assert d["draws"]["encoded"] == d["draws"]["backend_failed"] == d["draws"]["fault_suppressed"] == {}
    assert (d["draws"]["accepted"], d["draws"]["unmapped_count"], d["draws"]["suppressed_count"]) == (1, 1, 0)
    assert d["draws"]["failures"] == ["FIXED_FUNCTION_SHADER"]
    assert d["draws"]["empty"] == {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}}
    assert d["draws"]["refused"] == {"DrawPrimitiveUP": {"TRIANGLEFAN": 1}}
    assert (d["draws"]["drawn"], d["draws"]["total"], d["draws"]["per_frame"]) == (0, 3, [[1, 1, 2], [2, 2, 1]])
    assert d["not_implemented"]["methods"] == {"Device::TestCooperativeLevel": 1}
    assert d["not_implemented"]["count"] == 1 and d["shaders"]["translated"] == 0
    # R1: the translator's profile, from the CPU model's test facts, and no dump asked for
    sh = d["shaders"]
    assert (sh["translate_failures"], sh["failure_reasons"], sh["dump"]) == (0, [], {"dir": None, "refused": None, "dumped": 0, "failures": 0})
    assert sh["profile"]["facts_ok"] and len(sh["profile"]["record_sha256"]) == 64 and "compile.semantic_io=1" in sh["profile"]["record"]
    assert {"SA2_D3D9GPU_SHADER_DUMP", "SA2_D3D9GPU_SHADER_FAULT"} <= set(d["env"])
    assert d["uploads"]["up_vertex_bytes"] == 4 * 16 and d["declarations"]["bgra_inputs"] == 1
    rows = {(m["type"], m["method"]): m for m in d["methods"]}
    assert rows[("Device", "GetRenderState")]["failed"] == 1
    assert rows[("Device", "DrawPrimitiveUP")] == {"type": "Device", "method": "DrawPrimitiveUP", "slot": 83,
                                                   "calls": 3, "failed": 1, "first_failed_hr": "0x8876086c"}
    assert d["objects"]["live_at_destroy"] == {} and d["objects"]["created"] == {"vertex_declaration": 1}
    # the SDL patch's capabilities (sdl-patch phase B): the CPU model's default reports neither, as
    # a stock SDL3 does, so both features are the shader route's fallback, owed; the report keeps
    # the detected ones beside the used ones, a test run's override (none here), and the two
    # variables that make one
    sg = d["sdl_gpu"]
    assert (sg["sampler_clamp_to_border"], sg["depth_clip_and_clamp"], sg["capability_override"]) == (False, False, None)
    assert sg["detected"] == {"sampler_clamp_to_border": False, "depth_clip_and_clamp": False}
    assert sg["fallback_owed"] == {"border": True, "depth_clamp": True}
    assert {"SA2_D3D9GPU_CAPS_OFF", "SA2_D3D9GPU_TEST_RUN"} <= set(d["env"])
    pl = d["pipeline"]
    assert set(pl["patch"]) == {"border_samplers", "depth_clamp_keys"}
    assert set(pl["fallback_owed"]) == {"border_samplers", "border_stage_draws", "depth_clamp_keys"}
    assert pl["undecided"] == {"rt_feedback_keys": 0} and pl["fallback_owed"]["depth_clamp_keys"] == 1


def test_the_dxvk_player_s_contract_is_dxvk_s_five_keys(tmp_path):
    need_install()
    rec = GE.generate(ROOT, INSTALL, PLAYER / "dxvk.conf", tmp_path / "expected")
    assert list(rec["contract"]) == ["sdl_sha256", "dxvk_sha256", "dxvk_config_sha256", "sdl_version", "sdl_revision"]
    assert set(rec["libraries"]) == {"sdl3", "dxvk"} and "host" not in rec
    header = (tmp_path / "expected" / "sa2_host_expectations.h").read_text()
    # test_synth.sh's isolated bad-config build copies this header: its contract check
    # knows exactly these keys, and the header is exactly the generated one.
    assert header == BCC.generated_header(rec["contract"])
    assert 'names = {"sdl_sha256", "dxvk_sha256", "dxvk_config_sha256", "sdl_version", "sdl_revision"}' in \
        (PLAYER / "test_bad_config_contract.py").read_text()
    assert "d3d9gpu" not in header


def test_the_gpu_player_s_contract_is_sdl_s_alone_and_reads_nothing_of_dxvk_s(tmp_path):
    """--host sdlgpu reads neither DXVK's library nor dxvk.conf: it is made in a repository
    whose DXVK pin names no file, where the DXVK contract cannot be."""
    need_install()
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "private").symlink_to(ROOT / "private")
    pins = json.loads((ROOT / "tools" / "pins.json").read_text())
    pins["tools"]["dxvk"]["library"] = "private/no-such-dxvk.so"
    (repo / "tools" / "pins.json").write_text(json.dumps(pins))
    rec = GE.generate(repo, INSTALL, None, tmp_path / "expected-gpu", "sdlgpu")
    assert list(rec["contract"]) == ["sdl_sha256", "sdl_version", "sdl_revision"]
    assert set(rec["libraries"]) == {"sdl3"} and rec["host"] == "sdlgpu"
    assert [Path(x["path"]).name for x in rec["sources"]] == ["pins.json", "SDL_version.h", "SDL_revision.h",
                                                              "generate_expectations.py"]
    header = (tmp_path / "expected-gpu" / "sa2_host_expectations.h").read_text()
    assert "dxvk" not in header and header == BCC.generated_header(rec["contract"])
    with pytest.raises(FileNotFoundError):
        GE.generate(repo, INSTALL, PLAYER / "dxvk.conf", tmp_path / "expected", "dxvk")
    for config, host in ((PLAYER / "dxvk.conf", "sdlgpu"), (None, "dxvk")):
        with pytest.raises(ValueError, match="--config names dxvk.conf"):
            GE.generate(ROOT, INSTALL, config, tmp_path / "x", host)


@pytest.fixture(scope="module")
def contracts(tmp_path_factory) -> dict[str, Path]:
    need_install()
    d = tmp_path_factory.mktemp("contracts")
    GE.generate(ROOT, INSTALL, PLAYER / "dxvk.conf", d / "expected", "dxvk")
    GE.generate(ROOT, INSTALL, None, d / "expected-gpu", "sdlgpu")
    return {"dxvk": d / "expected", "sdlgpu": d / "expected-gpu"}


def player_cpp(contract: Path, gpu: bool, *extra: str) -> subprocess.CompletedProcess:
    """host/player/player.cpp through g++ against `contract`, as host/player/build.sh (gpu
    False) or host/d3d9gpu/build.sh (gpu True) compiles it."""
    return subprocess.run([GXX, "-std=c++17", "-Wall", "-Wextra", "-Wno-unused-parameter", "-I", str(contract),
                           "-I", str(ROOT / "host"), "-I", str(INSTALL / "include" / "dxvk"),
                           "-I", str(INSTALL / "include"), *(["-DSA2_PLAYER_GPU=1"] if gpu else []), *extra,
                           str(PLAYER / "player.cpp")], capture_output=True, text=True)


@pytest.mark.parametrize("host,gpu,ok", [
    ("dxvk", False, True),          # sa2_player
    ("sdlgpu", True, True),         # sa2_player_gpu: no DXVK expectation named
    ("sdlgpu", False, False),       # the DXVK player needs DXVK's expectations
])
def test_each_player_configuration_compiles_against_its_own_contract(contracts, host, gpu, ok):
    r = player_cpp(contracts[host], gpu, "-fsyntax-only")
    assert (r.returncode == 0) == ok, r.stderr[-2000:]
    if not ok:
        assert "dxvk_sha256" in r.stderr


def test_neither_configuration_names_the_other_host(contracts):
    """Preprocessed, the DXVK configuration holds nothing of the second host's (its entry
    points, its names, a d9g header) and the GPU one nothing of DXVK's expectations."""
    dxvk = player_cpp(contracts["dxvk"], False, "-E", "-P")
    gpu = player_cpp(contracts["sdlgpu"], True, "-E", "-P")
    assert dxvk.returncode == 0 == gpu.returncode, (dxvk.stderr + gpu.stderr)[-2000:]
    for word in ("sa2_clear_matrix_main", "--clear-matrix", "sdlgpu", "d3d9gpu", "d9g"):
        assert word not in dxvk.stdout, word
        assert word in gpu.stdout or word == "d9g", word
    for word in ("sa2_host_expected::dxvk_sha256", "sa2_host_expected::dxvk_config_sha256", "dlsym(lib_",
                 "DXVK_WSI_DRIVER is"):
        assert word in dxvk.stdout and word not in gpu.stdout, word
    # (no header of host/d3d9gpu, even in a branch the preprocessor drops)
    assert not [line for line in (PLAYER / "player.cpp").read_text().splitlines()
                if line.startswith("#include") and ("d9g" in line or "d3d9gpu" in line or "clear_matrix" in line)]


# ------------------------------------------------------------------ the build scripts' own checks
BUILD_CHECKS = {
    # the binary, its contract, and the start of its names check, in its build script
    "player": ("host/player/build.sh", "sa2_player", "expected", '"$PY" - "$OUT/sa2_player" '),
    "gpu": ("host/d3d9gpu/build.sh", "sa2_player_gpu", "expected-gpu", '"$PY" - "$OUT/sa2_player_gpu" '),
}
CONTRACT = {"player": ["sdl_sha256", "dxvk_sha256", "dxvk_config_sha256", "sdl_version", "sdl_revision"],
            "gpu": ["sdl_sha256", "sdl_version", "sdl_revision"]}
PIN = "ab" * 32       # DXVK's library hash in the checkout's tools/pins.json, as the GPU check reads it


def build_checks(tmp_path: Path, which: str, plant: bytes = b"", contract: list[str] | None = None,
                 parent: str = "sdlgpu-d3d9gpu-libdxvk_d3d9.so") -> subprocess.CompletedProcess:
    """A build script's names check and contract check, cut from the script as it is (from
    its names check to the end of its contract check, nothing rewritten) and run by bash
    under set -euo pipefail, from inside the checkout's host/d3d9gpu (the working directory
    -g embeds), on a binary holding the paths a build embeds (the working directory as a
    whole string, files under the checkout, the install and OUT) and `plant`. The checkout
    sits under a directory named for both hosts (`parent`), which no check may count."""
    script, binary, expected, start = BUILD_CHECKS[which]
    text = (ROOT / script).read_text()
    i = text.index(start)
    block = text[i:text.index('/expectations.json"\n', i) + len('/expectations.json"\n')]
    checkout = tmp_path / parent / "tree"
    cwd, install, out = checkout / "host" / "d3d9gpu", tmp_path / parent / "install", tmp_path / parent / "out"
    for d in (cwd, checkout / "tools", install, out / expected):
        d.mkdir(parents=True, exist_ok=True)
    (checkout / "tools" / "pins.json").write_text(json.dumps({"tools": {"dxvk": {"library_sha256": PIN}}}))
    (out / expected / "expectations.json").write_text(json.dumps(
        {"contract": {k: "x" for k in (CONTRACT[which] if contract is None else contract)}}))
    paths = [b"", str(cwd).encode(), f"{checkout}/host/player/player.cpp".encode(),
             f"{checkout}/host/d3d9gpu/d9g_entry.cpp".encode() if which == "gpu" else b"",
             f"{install}/include/SDL3/SDL.h".encode(), f"{out}/{expected}/expectations.h".encode(), b""]
    (out / binary).write_bytes(b"\x7fELF" + b"\0".join(paths) + plant + b"\0")
    env = dict(os.environ, PY=sys.executable, OUT=str(out), HOST=str(checkout / "host"),
               STREAM_H_DIR=str(checkout / "host"), I=str(install), REPO=str(checkout), PWD=str(cwd))
    return subprocess.run(["bash", "-c", "set -euo pipefail\n" + block], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=60)


@pytest.mark.parametrize("which,plant,contract,want", [
    # the paths a build embeds, under a checkout named for both hosts, are no defect
    ("player", b"", None, (0, "")),
    ("gpu", b"", None, (0, "")),
    # a second-host name anywhere else is, and exits 2 as every other check of the build does
    ("player", b"\0SA2_D3D9GPU_REPORT", None, (2, "sa2_player holds ['SA2_D3D9GPU']")),
    ("player", b"\0--d3d9 sdlgpu", None, (2, "sa2_player holds ['sdlgpu']")),
    # a path under the working directory (inside host/d3d9gpu) keeps what lies under it: the
    # directory is taken out only as a whole string (the confirm's names-check NIT)
    ("player", "\0{cwd}/d9g_entry.cpp", None, (2, "sa2_player holds ['d3d9gpu']")),
    # DXVK's library name and its pinned hash in the GPU player
    ("gpu", b"\0libdxvk_d3d9.so", None, (2, "sa2_player_gpu holds ['libdxvk_d3d9.so']")),
    ("gpu", ("\0" + PIN).encode(), None, (2, f"sa2_player_gpu holds ['{PIN}']")),
    # each contract its host's keys, in order; any other exits 2 as the names check does
    ("player", b"", [*CONTRACT["player"], "d3d9gpu_sha256"],
     (2, f"the DXVK contract is {[*CONTRACT['player'], 'd3d9gpu_sha256']}")),
    ("player", b"", CONTRACT["player"][::-1], (2, f"the DXVK contract is {CONTRACT['player'][::-1]}")),
    ("gpu", b"", [*CONTRACT["gpu"], "dxvk_sha256"], (2, f"the GPU contract is {[*CONTRACT['gpu'], 'dxvk_sha256']}")),
])
def test_each_build_script_s_names_and_contract_checks_exit_2_on_a_defect(tmp_path, which, plant, contract, want):
    if isinstance(plant, str):
        plant = plant.format(cwd=tmp_path / "sdlgpu-d3d9gpu-libdxvk_d3d9.so" / "tree" / "host" / "d3d9gpu").encode()
    p = build_checks(tmp_path, which, plant, contract)
    assert (p.returncode, p.stdout.strip()) == want, p.stderr


# ------------------------------------------------------------------ test_d3d9gpu.sh step 7
STAND_IN_PY = """#!/bin/bash
# $PY's stand-in: logs its arguments; compare_hosts.py's two subcommands exit as told
echo "py $*" >> "$LOG"
case "$2" in
  dxvk-unchanged) echo "dxvk-unchanged: the stand-in's verdict"; exit "$URC";;
  dxvk-ab) echo "dxvk-ab: the stand-in's verdict"; exit "$ARC";;
esac
exit 99
"""


def step7(tmp_path: Path, urc: int, arc: int = 0, brc: int = 0, record: bool = False) -> tuple[str, list[str]]:
    """host/d3d9gpu/test_d3d9gpu.sh's step 7 as the script holds it (cut between its "== 7"
    and "== 8" lines, nothing rewritten), run by bash with stand-ins: compare_hosts.py
    (dxvk-unchanged exits `urc`, dxvk-ab `arc`) and build_before.sh (exits `brc`), a BEFORE
    with its record when `record`. Its output, and the stand-ins' calls in order."""
    text = (D9G / "test_d3d9gpu.sh").read_text()
    block = text[text.index('echo "== 7'):text.index('echo "== 8')]
    py, here, before, r = tmp_path / "py", tmp_path / "here", tmp_path / "before", tmp_path / "r"
    for d in (here, before, r):
        d.mkdir()
    py.write_text(STAND_IN_PY)
    py.chmod(0o755)
    (here / "build_before.sh").write_text('echo "build_before $*" >> "$LOG"\necho "BEFORE_OK stand-in"\nexit "$BRC"\n')
    if record:
        (before / "sa2_player.json").write_text("{}\n")
    log = tmp_path / "calls"
    env = dict(os.environ, LOG=str(log), URC=str(urc), ARC=str(arc), BRC=str(brc), PY=str(py), CMP="CMP", OUT="OUT",
               BC="C", R=str(r), BEFORE=str(before), BOOT="BOOT", HERE=str(here))
    script = 'set -uo pipefail\nfail=0\nbad() { echo "   FAIL: $*"; fail=1; }\n' + block + 'echo "fail=$fail"\n'
    p = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    return p.stdout.replace(str(tmp_path), "T"), [c.replace(str(tmp_path), "T") for c in calls]


UNCHANGED_CALL = "py CMP dxvk-unchanged --after OUT --before-commit C --out T/r/dxvk-unchanged"
AB_CALLS = ["py CMP dxvk-ab --before T/before --before-commit C --after OUT --stream BOOT --stage 3 --out "
            "T/r/dxvk-ab-boot"] + [f"py CMP dxvk-ab --before T/before --before-commit C --after OUT --stream "
                                   f"T/r/synth.sa2d3d9 --stage {st} --allow-notes --out T/r/dxvk-ab-synth{st}"
                                   for st in range(4)]


@pytest.mark.parametrize("urc,arc,brc,record,calls,fails", [
    # UNCHANGED: the step passes by construction, nothing else runs
    (0, 0, 0, False, [UNCHANGED_CALL], []),
    # CHANGED: the before built (unless it has its record), then the five A/Bs, each of which must pass
    (1, 0, 0, False, [UNCHANGED_CALL, "build_before C T/before", *AB_CALLS], []),
    (1, 0, 0, True, [UNCHANGED_CALL, *AB_CALLS], []),
    (1, 1, 0, True, [UNCHANGED_CALL, *AB_CALLS],
     ["dxvk-ab on boot", *(f"dxvk-ab on the synthetic stream at stage {st}" for st in range(4))]),
    (1, 0, 1, False, [UNCHANGED_CALL, "build_before C T/before", *AB_CALLS],
     ["the before build (see T/r/before-build.log)"]),
    # refused, or any exit but 0 and 1: the step fails, and no A/B runs (the review's NIT: the
    # refusal path had never run)
    (2, 0, 0, False, [UNCHANGED_CALL], ["dxvk-unchanged refused (exit 2, T/r/dxvk-unchanged.txt)"]),
    (99, 0, 0, False, [UNCHANGED_CALL], ["dxvk-unchanged refused (exit 99, T/r/dxvk-unchanged.txt)"]),
])
def test_step_7_routes_each_dxvk_unchanged_verdict(tmp_path, urc, arc, brc, record, calls, fails):
    out, ran = step7(tmp_path, urc, arc, brc, record)
    assert ran == calls
    assert [line.removeprefix("   FAIL: ") for line in out.splitlines() if line.startswith("   FAIL: ")] == fails
    assert out.splitlines()[-1] == f"fail={int(bool(fails))}"
    assert ("UNCHANGED by construction: no A/B to run" in out) == (urc == 0)
    assert ("CHANGED: the A/B against C's player" in out) == (urc == 1)


# ------------------------------------------------------------------ build_before.sh's clone
STAND_IN_BUILD = """#!/bin/bash
# the commit's host/player/build.sh, which writes its own record (build_record.py), as every
# commit's since the two binaries does: logs which checkout it built from, writes a player
echo "build SA2_REPO=$SA2_REPO" >> "$LOG"
mkdir -p "$OUT"
echo "the before player" > "$OUT/sa2_player"
"""


def before_repo(tmp_path: Path) -> tuple[Path, str]:
    """A repository (SA2_REPO) with one commit: host/player/build.sh (STAND_IN_BUILD),
    host/sa2_stream.h, tools/pins.json and a README outside host/. (repository, commit)"""
    repo = tmp_path / "repo"
    (repo / "host" / "player").mkdir(parents=True)
    (repo / "tools").mkdir()
    (repo / "host" / "player" / "build.sh").write_text(STAND_IN_BUILD)
    (repo / "host" / "sa2_stream.h").write_text("// stream\n")
    (repo / "tools" / "pins.json").write_text("{}\n")
    (repo / "README.md").write_text("the repository\n")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "before"]):
        subprocess.run(git + args, check=True, capture_output=True)
    return repo, subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                                text=True).stdout.strip()


def build_before(repo: Path, commit: str, out: Path, clone: Path, log: Path) -> subprocess.CompletedProcess:
    """host/d3d9gpu/build_before.sh as test_d3d9gpu.sh step 7 runs it, SA2_REPO `repo`."""
    env = dict(os.environ, SA2_REPO=str(repo), SA2_S5_INSTALL=str(repo / "install"), LOG=str(log))
    return subprocess.run(["bash", str(D9G / "build_before.sh"), commit, str(out), str(clone)], env=env,
                          capture_output=True, text=True, timeout=120)


def ignored_by_the_clone(clone: Path) -> None:
    """An ignore rule of the clone's own (no commit holds it) over host/cerrno."""
    (clone / ".git" / "info").mkdir(exist_ok=True)
    (clone / ".git" / "info" / "exclude").write_text("cerrno\n")
    (clone / "host" / "cerrno").write_text("#error a header the clone's exclude file hides\n")


def hidden_by_a_gitignore(clone: Path) -> None:
    """An untracked .gitignore that ignores itself and everything beside it."""
    (clone / "host" / ".gitignore").write_text("*\n")
    (clone / "host" / "cerrno").write_text("#error a header an untracked .gitignore hides\n")


@pytest.mark.parametrize("change,listed", [
    (None, None),
    # outside host/ and tools/pins.json: nothing the before player reads
    (lambda c: (c / "notes.txt").write_text("a note\n"), None),
    # an untracked header the before player's compile would read (-I host/ ahead of the
    # system's <cerrno>), which a pre-split record, holding only the commit's files, would
    # not show: the confirm's F1, which no test held
    (lambda c: (c / "host" / "cerrno").write_text("#error untracked\n"), ["?? host/cerrno"]),
    (lambda c: (c / "host" / "SDL3").mkdir() or (c / "host" / "SDL3" / "SDL.h").write_text("#error\n"),
     ["?? host/SDL3/SDL.h"]),
    # ... or one an ignore rule hides from git status (the clone's own exclude file, or an
    # untracked .gitignore that ignores itself)
    (ignored_by_the_clone, ["!! host/cerrno"]),
    (hidden_by_a_gitignore, ["!! host/.gitignore", "!! host/cerrno"]),
    # a tracked file changed, staged or not, and the pins
    (lambda c: (c / "host" / "sa2_stream.h").write_text("// changed\n"), [" M host/sa2_stream.h"]),
    (lambda c: (c / "tools" / "pins.json").write_text("{\"changed\": 1}\n"), [" M tools/pins.json"]),
    (lambda c: (c / "host" / "sa2_stream.h").unlink(), [" D host/sa2_stream.h"]),
])
def test_build_before_refuses_a_reused_clone_that_is_not_the_commit_s(tmp_path, change, listed):
    """build_before.sh builds the before player from its clone only while the clone's host/
    and tools/pins.json are the commit's: a reused clone holding anything else there, a file
    git status lists as untracked or as ignored included, is refused (exit 2, the files
    named, no build run and nothing written to OUT); anything else in the clone is not
    read, and the build runs from the clone (SA2_REPO the clone) and says BEFORE_OK."""
    repo, commit = before_repo(tmp_path)
    clone, log = tmp_path / "before.clone", tmp_path / "calls"
    first = build_before(repo, commit, tmp_path / "first", clone, log)
    assert first.returncode == 0, first.stdout + first.stderr
    assert log.read_text().splitlines() == [f"build SA2_REPO={clone}"]
    log.unlink()
    if change:
        change(clone)
    out = tmp_path / "before"
    p = build_before(repo, commit, out, clone, log)
    if listed is None:
        assert p.returncode == 0, p.stdout + p.stderr
        assert p.stdout.splitlines()[-1] == f"BEFORE_OK {commit} {hashlib.sha256((out / 'sa2_player').read_bytes()).hexdigest()}"
        assert log.read_text().splitlines() == [f"build SA2_REPO={clone}"]
        return
    assert p.returncode == 2, p.stdout + p.stderr
    assert p.stdout.splitlines() == [f"{clone} has changes, untracked or ignored files under host/ or in tools/pins.json:",
                                     *listed]
    assert not out.exists() and not log.exists()


def test_build_before_refuses_a_clone_at_another_commit_and_no_commit(tmp_path):
    repo, commit = before_repo(tmp_path)
    clone, log = tmp_path / "before.clone", tmp_path / "calls"
    assert build_before(repo, commit, tmp_path / "first", clone, log).returncode == 0
    subprocess.run(["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "-m", "another"], check=True)
    log.unlink()
    p = build_before(repo, commit, tmp_path / "before", clone, log)
    assert (p.returncode, p.stdout.strip()) == (2, f"{clone} is not at {commit}")
    p = build_before(repo, "no-such-commit", tmp_path / "before", tmp_path / "other.clone", log)
    assert (p.returncode, p.stdout.strip()) == (2, f"no-such-commit is no commit in {repo}")
    assert not (tmp_path / "before").exists() and not (tmp_path / "other.clone").exists() and not log.exists()


@pytest.mark.parametrize("corrupt,said", [
    (lambda index: index[:20], "index file smaller than expected"),
    (lambda index: b"XXXX" + index[4:], "index file corrupt"),
])
def test_build_before_stops_when_git_cannot_read_the_clone(tmp_path, corrupt, said):
    """A reused clone whose index git cannot read (the clone is still at the commit: its
    HEAD reads) makes git status fail and list nothing; build_before.sh takes that for no
    clean clone and refuses it (exit 2) before it builds anything, never for an empty
    status (the S confirm's K6: `|| true` on the status ran the build)."""
    repo, commit = before_repo(tmp_path)
    clone, log = tmp_path / "before.clone", tmp_path / "calls"
    assert build_before(repo, commit, tmp_path / "first", clone, log).returncode == 0
    log.unlink()
    index = clone / ".git" / "index"
    index.write_bytes(corrupt(index.read_bytes()))
    assert subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip() == commit
    out = tmp_path / "before"
    p = build_before(repo, commit, out, clone, log)
    assert (p.returncode, p.stdout.splitlines()) == (2, [f"git cannot read the status of {clone}"]), \
        p.stdout + p.stderr
    assert said in p.stderr, p.stderr
    assert not out.exists() and not log.exists()
