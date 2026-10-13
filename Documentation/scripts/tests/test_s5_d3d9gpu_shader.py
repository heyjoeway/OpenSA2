"""host/d3d9gpu's shader translator (R1), held without a GPU.

Built here (host/d3d9gpu/shader/build_translator.sh, into a temporary directory, never the shared
install) against the pinned DXVK checkout and the pinned dxbc-spirv archive (tools/pins.json
dxbc_spirv_archive, DXVK's own objects); a missing checkout, build, archive or tool fails, it does not
skip. The file builds the translator four times (the fixture, a
second directory for the identity, and two mutated trees), about 12 s each on this machine, and
runs in about a minute. It starts no GPU work, but it reads DXVK's build and the shader tools
under private/s5-tools, so it must not run while tools/s5/build_host_deps.sh rebuilds them (the
I4 landing's quiet window). The shaders are synthetic, assembled in the test by
host/d3d9gpu/test/sm3_asm.py, so nothing here holds a shipped byte. Every assertion compares
against a literal expected value:

  * reflection: a vertex shader's input signature in dcl order, a pixel shader's sampler and
    render-target masks, the SDL sampler slots in register order, the uniform slot table
    (static 0, dynamic 1, clip planes 2, state 3 in vertex shaders; static 0, state 1 in pixel
    shaders) and the state buffer's fixed size;
  * determinism: two compiles give the same bytes; and a compile from a caller whose MXCSR
    flushes denormals (FTZ and DAZ) gives them too. A statically indexed def value is an immediate
    the SPIR-V carries as its bits, unfolded because the operations are precise, so the module
    cannot show the guard; the translator's host-side IR text can: a shader with denormal defs
    disassembles the same under FTZ and DAZ, and differently with the guard's control
    (no_fp_guard), so the check can fail;
  * the descriptor sets and bindings of the SPIR-V, read back with spv_canon.py's parser;
  * every module is valid SPIR-V (the pinned spirv-val, Vulkan 1.3);
  * the BGRA variant reads .zyxw at the masked location and nowhere else;
  * the shader cache: a miss, a hit, a corrupted entry detected and recompiled, a verified hit,
    a root outside private/ refused and left uncreated, a variant (BGRA, border) after its base a
    miss that gives the variant's module, a cache refused under a translator fault (a faulted
    module would be kept for a clean key), and every cache refused by a translator built without
    its identity;
  * the translator's identity: from contents only (two build directories give one identity) and
    moved by any change to a unit it compiles;
  * sample-rate shading (d3d9.forceSampleRateShading), which host 2 does not mirror, refused for
    pixel shaders;
  * ruling 3's border fallback (R1 U11): a border variant adds the colour buffer at the fourth
    fragment uniform slot, is valid SPIR-V, changes only the registers it names, records the reads
    of its registers it leaves unwrapped (the sampling function's depth-compare sample and gather),
    and is refused for a vertex shader, a register the shader never samples, a linear bit without a
    BORDER axis, and the depth-clamp emulation (built only if the measurement finds a pixel); its
    fault leaves the base;
  * the canonical diff's linked half (spv_canon.py --linked, --linked-batch) on hand-built modules in
    the shape the capture layer records (test/spv_pairs.py): DXVK's linked variant's spec constants
    are host 2's state loads only in --linked mode, a moved SpecId or push member fails, a captured
    module's roles come from DXVK's dump of the same shader, a drawn pair without a capture fails, a
    pair is matched by its BGRA mask too (DXVK's from its pipeline's vertex input), a linked record
    without a vertex input fails, a library module that is not the dump fails (an extra capability
    included), a dumped shader no library module covers fails, a D3D9 pipeline that is neither
    linked nor a library fails, the library bit is read from either flags word, DXVK's own pipelines
    are left;
  * the canonical diff's labels: two values of one shape are two labels (loop phis, a helper's
    parameters, Function variables, loads around a store), shown on the translator's own module with
    the swaps a reviewer made by hand, each still valid SPIR-V, and on hand-built modules;
  * every explanation class of spv_explained.json is hit by the clean pairs, so a class nothing
    exercises cannot land;
  * the GPU script's steps (test/r1_receipt.py), each on synthetic inputs that pass and on the one
    wrong thing each must fail on: the plan (the BGRA location mask by signature, the reflections
    held to the bytecode's own tokens, the border fallback variant a BORDER sampler needs and where
    the fallback cannot take one: a cube or depth texture, fetch4), the load (the validation layer
    loaded, its control's errors seen, the modules the driver received), the canonical diff's
    capture account, the controls (a fault that changes nothing fails, but the linkage's where every
    pair is compatible), the null stream, the border residue, the capture layer's transparency, the
    cache, the verdict (any validation error fails it, in whichever check saw it), C4 (U9: the state
    census's marker, nothing unmapped, differing or pending, no VS_SIGNATURE, the device's reflection
    the record's, every control as required and the vertex-input ones by the census itself,
    map-vi-declorder on exactly the tuples whose signature is not the declaration)
    and the receipt writer (only EXPECTED's checks, the profiles, the translator, the build's
    sources and the validation layer's hash held to what ran);
  * the profile: DXVK's shader-option rules for NVIDIA before and after 580, lavapipe, RADV,
    ANV before and after Mesa 25.0.3, and AMD's driver, flag by flag;
  * the canonical diff's controls: a clean build compared with itself passes; each translator
    fault the corpus exercises fails it; a state buffer whose spec dwords or push members sit at
    other offsets, or under other names, fails it;
  * the comparer's DXVK side, on hand-built SPIR-V pairs (test/spv_pairs.py, the pinned spirv-as):
    every class it explains (spec data, push data, the sampler heap, sets and bindings, the
    dynamic float length, the state layout, the capabilities) passes a clean pair and fails the
    pair with that rule broken on either side, and so does each rule no class relaxes (a dropped
    execution mode, RelaxedPrecision on a value, a state member or an image, a texture in another
    set); the batch mode fails a lost reflection, a module without one, an unmatched DXVK module and
    a wrong count;
  * the linkage: a pair whose semantics sit at different locations links to the literal mapping
    DXVK's linkage gives (the pixel shader's tangent moved to the vertex shader's location 13,
    its unwritten texcoord0 removed), and the linkage fault leaves the pair unlinked;
  * the build's guards: a host-2 unit that reaches DXVK's constant copy fails the build, and so does
    a dxbc-spirv archive other than the pin's (the build's thin archive, or a file the pin's sha256
    does not name);
  * host/player/dxvk.conf still pins floatEmulation = Strict and useFP16 = False, which the
    profile assumes.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("SA2_REPO") or Path(__file__).resolve().parents[3])
D9G = ROOT / "host" / "d3d9gpu"
TOOLS = ROOT / "private" / "s5-tools" / "install-shader-tools" / "bin"
sys.path.insert(0, str(D9G / "test"))
import sm3_asm  # noqa: E402
import spv_canon  # noqa: E402

VS_FULL = """vs_3_0
dcl_position v0
dcl_color v1
dcl_texcoord0 v2
dcl_normal v3
def c200, 1.0, 0.5, 0.25, 0.0
defi i0, 4, 0, 1, 0
dcl_position o0
dcl_color o1
dcl_texcoord0 o2
dcl_psize o3.x
dp4 o0.x, v0, c0
dp4 o0.y, v0, c1
dp4 o0.z, v0, c2
dp4 o0.w, v0, c3
mov r0, c200.w
loop aL, i0
add r0, r0, c70[aL]
endloop
mul r1, v1, c200.y
slt r2, v3, c200
lrp r3, r2, r1, v1
rcp r4.x, v3.x
dp3 r5.x, v3, v3
rsq r5.y, r5.x
exp r6.x, r5.x
log r6.y, r5.y
min r7, r3, c200.x
max r7, r7, -c200.x
mad o1, r7, c200.y, r0
if_ne r4.x, c200.w
mov o2, v2
else
mov o2, c200
endif
mov_pp o3.x, c200.x
"""

PS_FULL = """ps_3_0
dcl_color v0
dcl_texcoord0 v1
dcl_2d s0
dcl_2d s3
dcl_2d s7
def c10, 1.0, 0.5, 0.0, 2.0
texld r0, v1, s0
texld r1, v1, s3
texld_pp r2, v1, s7
mul r0, r0, v0
cmp r1, -r1, r1, c10.z
nrm r3.xyz, r2
mad_sat r0, r1, c10.y, r0
if_lt r3.x, c10.z
mov r0.w, c10.x
endif
add oC0, r0, r3
"""

# Denormal def values (below FLT_MIN, 1.17549e-38): the guard's control. The module carries their
# bits; the translator's IR text prints them, and a host that flushes denormals prints zeros.
VS_DENORM = """vs_3_0
dcl_position v0
dcl_position o0
def c10, 1e-40, 3e-40, 0.5, 1.0
add r0, c10, c10.y
add o0, v0, r0
"""

# Static constants c0 and c1 (no def): the static buffer holds two members.
PS_STATIC = """ps_3_0
dcl_texcoord0 v0
mul r0, v0, c0
add oC0, r0, c1
"""

# A pair DXVK's linkage must resolve. dxbc-spirv gives the fixed-function semantics fixed
# locations (sm3_io_map.cpp:12-26: normal 0, texcoord0-7 1-8, color 9-10, fog 11) and every other
# semantic the next location from 12 in declaration order (:291-312). So the vertex shader's
# binormal and tangent sit at 12 and 13, the pixel shader's tangent at 12: resolveSemanticIo moves
# it to 13. Nothing writes the pixel shader's texcoord0 (location 1): resolveMismatchedIo turns its
# loads into zero (ir_pass_lower_io.cpp:1301-1303) and the input goes.
VS_MISMATCH = """vs_3_0
dcl_position v0
dcl_position o0
dcl_binormal o1
dcl_tangent o2
mov o0, v0
mov o1, v0
mov o2, -v0
"""
PS_MISMATCH = """ps_3_0
dcl_tangent v0
dcl_texcoord0 v1
add oC0, v0, v1
"""

NVIDIA_FACTS = {
    "schema": "sa2-d3d9gpu-shader-facts/v1", "device": "synthetic NVIDIA", "driver_name": "NVIDIA",
    "driver_info": "", "driver_id": 4, "driver_version_raw": (595 << 22) | (91 << 14) | (7 << 6),
    "min_storage_buffer_offset_alignment": 16, "max_uniform_buffer_range": 65536,
    "max_per_stage_descriptor_uniform_buffers": 1048576, "shader_float16": True,
    "storage_push_constant16": True, "storage_push_constant8": True, "r32_storage_read_without_format": True,
    "nv_raw_access_chains": True, "sz_inf_nan_preserve": [True, True, True], "rte": [True, True, True],
    "rtz": [False, True, True], "denorm_flush": [False, False, False], "denorm_preserve": [True, False, False],
    "rounding_mode_independence": 1, "denorm_behavior_independence": 1, "float_controls2": True,
    "descriptor_heap": True, "resource_indexing": True,
}


def facts(**changes):
    f = dict(NVIDIA_FACTS)
    f.update(changes)
    return f


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    for need in (ROOT / "private/s5-tools/src/dxvk-3.1.1", ROOT / "private/s5-tools/build/dxvk/compile_commands.json",
                 TOOLS / "spirv-val"):
        assert need.exists(), f"{need} is missing: the translator's test needs it (it fails, it does not skip)"
    out = tmp_path_factory.mktemp("d9g_shader")
    r = subprocess.run(["bash", str(D9G / "shader" / "build_translator.sh"), str(out)], capture_output=True, text=True,
                       env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 0 and "D3D9GPU_SHADER_BUILD_OK" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
    identity = identity_line(r.stdout)
    shaders = {}
    for name, text in (("vs_full", VS_FULL), ("ps_full", PS_FULL), ("ps_static", PS_STATIC),
                       ("vs_mismatch", VS_MISMATCH), ("ps_mismatch", PS_MISMATCH), ("vs_denorm", VS_DENORM)):
        path = out / f"{name}.bin"
        path.write_bytes(sm3_asm.assemble(text))
        shaders[name] = path
    fpath = out / "facts.json"
    fpath.write_text(json.dumps(NVIDIA_FACTS))
    return {"dir": out, "shaderc": out / "d3d9gpu_shaderc", "shaders": shaders, "facts": fpath, "identity": identity}


def shaderc(build, *args, env=None, check=True):
    r = subprocess.run([str(build["shaderc"]), *map(str, args)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def compile_into(build, out, names, *opts, env=None):
    out.mkdir(parents=True, exist_ok=True)
    return shaderc(build, "compile", build["facts"], out, *opts, *[build["shaders"][n] for n in names], env=env)


def reflection(out, name):
    return json.loads((out / f"{name}.json").read_text())


def test_reflection_is_dxvks_analysis_and_sdls_slots(build, tmp_path):
    compile_into(build, tmp_path, ["vs_full", "ps_full", "ps_static"])
    vs, ps, pst = reflection(tmp_path, "vs_full"), reflection(tmp_path, "ps_full"), reflection(tmp_path, "ps_static")
    # D3DDECLUSAGE_POSITION 0, COLOR 10, TEXCOORD 5, NORMAL 3, each index 0, in dcl order
    assert vs["input_signature"] == [[0, 0], [10, 0], [5, 0], [3, 0]]
    assert vs["stage"] == "vertex" and vs["version"] == [3, 0]
    assert vs["sampler_registers"] == [] and vs["num_samplers"] == 0
    assert vs["floats_dynamic"] is True
    assert [(u["slot"], u["kind"], u["bytes"]) for u in vs["uniforms"]] == [
        (1, "dynamic", 4096), (2, "clip_planes", 96), (3, "state", 52)]
    assert vs["num_uniform_buffers"] == 4
    assert ps["sampler_mask"] == (1 << 0) | (1 << 3) | (1 << 7) and ps["rt_mask"] == 1
    assert ps["sampler_registers"] == [0, 3, 7] and ps["num_samplers"] == 3
    assert [(u["slot"], u["kind"], u["bytes"]) for u in ps["uniforms"]] == [(1, "state", 44)]
    assert ps["num_uniform_buffers"] == 2
    assert [(u["slot"], u["kind"], u["bytes"]) for u in pst["uniforms"]] == [(0, "static", 32), (1, "state", 44)]
    assert pst["float_ranges"] == [[0, 0, 2, False, False]]


def test_compiles_are_deterministic_and_the_ftz_daz_regression_guard_holds(build, tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    compile_into(build, a, ["vs_full", "ps_full", "vs_denorm"])
    compile_into(build, b, ["vs_full", "ps_full", "vs_denorm"])
    compile_into(build, c, ["vs_full", "ps_full", "vs_denorm"], "--ftz-daz")
    for name in ("vs_full", "ps_full", "vs_denorm"):
        first = (a / f"{name}.spv").read_bytes()
        assert struct.unpack("<I", first[:4])[0] == 0x07230203 and len(first) > 1000
        assert first == (b / f"{name}.spv").read_bytes() == (c / f"{name}.spv").read_bytes()
        assert (a / f"{name}.ir.txt").read_text() == (c / f"{name}.ir.txt").read_text()
    # the guard's control: the translator under the caller's FTZ and DAZ prints the denormal defs as zeros
    plain = (a / "vs_denorm.ir.txt").read_text()
    assert "FAdd %[f32(9.99995e-41)] %[f32(3e-40)]" in plain
    off = tmp_path / "noguard"
    compile_into(build, off, ["vs_denorm"], "--ftz-daz", env={"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": "no_fp_guard"})
    unguarded = (off / "vs_denorm.ir.txt").read_text()
    assert unguarded != plain and "FAdd %[f32(0)] %[f32(0)]" in unguarded


def descriptor_bindings(path):
    m = spv_canon.Module(path, "host2")
    out = {}
    for i in m.insts:
        if i.op == "OpVariable" and m.storage(i) in ("Uniform", "UniformConstant"):
            out[m.names.get(i.rid, str(i.rid))] = (int(m.deco(i.rid, "DescriptorSet")[0]), int(m.deco(i.rid, "Binding")[0]))
    return out


def test_descriptors_take_sdls_sets_and_compacted_slots(build, tmp_path):
    compile_into(build, tmp_path, ["vs_full", "ps_full", "ps_static"])
    vs = descriptor_bindings(tmp_path / "vs_full.spv")
    assert vs == {"cF": (1, 1), "clipPlanes": (1, 2), "specData": (1, 3)}
    ps = descriptor_bindings(tmp_path / "ps_full.spv")
    assert {k: v for k, v in ps.items() if k.startswith("s")} == {
        "s0": (2, 0), "s0_2d": (2, 0), "s3": (2, 1), "s3_2d": (2, 1), "s7": (2, 2), "s7_2d": (2, 2), "specData": (3, 1)}
    assert descriptor_bindings(tmp_path / "ps_static.spv") == {"c": (3, 0), "specData": (3, 1)}


def test_every_module_is_valid_spirv(build, tmp_path):
    compile_into(build, tmp_path, ["vs_full", "ps_full", "ps_static", "vs_mismatch", "ps_mismatch"])
    for spv in sorted(tmp_path.glob("*.spv")):
        r = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", str(spv)], capture_output=True, text=True)
        assert r.returncode == 0, f"{spv.name}: {r.stdout}{r.stderr}"


def test_bgra_variant_swizzles_only_the_masked_location(build, tmp_path):
    base, bgra = tmp_path / "base", tmp_path / "bgra"
    compile_into(build, base, ["vs_full"])
    compile_into(build, bgra, ["vs_full"], "--bgra", "2")  # signature location 1, the color
    a, b = base / "vs_full.spv", bgra / "vs_full.spv"
    assert a.read_bytes() != b.read_bytes()
    assert spv_canon.compare(a, b, 2, reference_side="host2")["ok"]
    assert spv_canon.compare(a, b, 2, reference_side="host2")["classes"]["host2"]["bgra_input_swizzle"] == 3
    assert not spv_canon.compare(a, b, 0, reference_side="host2")["ok"]
    assert not spv_canon.compare(a, b, 4, reference_side="host2")["ok"]
    r = shaderc(build, "compile", build["facts"], tmp_path, "--bgra", "1", build["shaders"]["ps_full"], check=False)
    assert r.returncode == 1 and "vertex shaders only" in r.stdout


def test_cache_hits_detects_corruption_and_stays_in_private(build, tmp_path):
    root = ROOT / "private" / "generated" / "s5" / "r1" / "test-cache" / tmp_path.name
    try:
        args = ["--cache", root, "--repo", ROOT]
        r1 = compile_into(build, tmp_path / "1", ["ps_full"], *args)
        assert "CACHE hits=0 misses=1 verified=0 corrupt=0" in r1.stdout
        r2 = compile_into(build, tmp_path / "2", ["ps_full"], *args)
        assert "CACHE hits=1 misses=0 verified=0 corrupt=0" in r2.stdout
        assert (tmp_path / "1/ps_full.spv").read_bytes() == (tmp_path / "2/ps_full.spv").read_bytes()
        entry = next(root.rglob("*.spv"))
        data = bytearray(entry.read_bytes())
        data[-5] ^= 0xFF
        entry.write_bytes(bytes(data))
        r3 = compile_into(build, tmp_path / "3", ["ps_full"], *args)
        assert "CACHE hits=0 misses=1 verified=0 corrupt=1" in r3.stdout
        assert (tmp_path / "3/ps_full.spv").read_bytes() == (tmp_path / "1/ps_full.spv").read_bytes()
        r4 = compile_into(build, tmp_path / "4", ["ps_full"], *args, "--verify")
        assert "CACHE hits=1 misses=0 verified=1 corrupt=0" in r4.stdout
        # A variant after its base is a miss, and its module is the variant's (the key holds the variant).
        for name, opts in (("vs_full", ("--bgra", "2")), ("ps_full", ("--border-u", "1"))):
            compile_into(build, tmp_path / f"{name}-base", [name], *args)
            rv = compile_into(build, tmp_path / f"{name}-variant", [name], *args, *opts)
            assert "CACHE hits=0 misses=1 verified=0 corrupt=0" in rv.stdout, (name, rv.stdout)
            compile_into(build, tmp_path / f"{name}-uncached", [name], *opts)
            got = (tmp_path / f"{name}-variant" / f"{name}.spv").read_bytes()
            assert got == (tmp_path / f"{name}-uncached" / f"{name}.spv").read_bytes()
            assert got != (tmp_path / f"{name}-base" / f"{name}.spv").read_bytes()
        # A translator fault may not use a cache: its module would be kept for the clean key, or a clean
        # entry served to the fault's control. Refused, nothing written, and the clean entry still a hit.
        before = sorted(root.rglob("*.spv"))
        rf = shaderc(build, "compile", build["facts"], tmp_path / "1", "--cache", root, "--repo", ROOT,
                     build["shaders"]["vs_full"], env={"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": "umin_dropped"},
                     check=False)
        assert rf.returncode == 2 and "REFUSED" in rf.stderr and "translator fault" in rf.stderr, rf.stderr
        assert sorted(root.rglob("*.spv")) == before
        rc = compile_into(build, tmp_path / "after-fault", ["vs_full"], *args)
        assert "CACHE hits=1 misses=0 verified=0 corrupt=0" in rc.stdout
        assert (tmp_path / "after-fault/vs_full.spv").read_bytes() == (tmp_path / "vs_full-base/vs_full.spv").read_bytes()
        # A root known to be outside private/: under the system temporary directory, whatever --basetemp
        # is. Refused, and not created on the way to the refusal.
        outside = Path(tempfile.mkdtemp(prefix="d9g-cache-outside-"))
        try:
            private = (ROOT / "private").resolve()
            assert private not in outside.resolve().parents and outside.resolve() != private, outside
            r5 = shaderc(build, "compile", build["facts"], tmp_path, "--cache", outside / "made" / "cache", "--repo", ROOT,
                         build["shaders"]["ps_full"], check=False)
            assert r5.returncode == 2 and "REFUSED" in r5.stderr and "is not inside" in r5.stderr
            assert not (outside / "made").exists()
        finally:
            shutil.rmtree(outside)
    finally:
        if root.exists():
            shutil.rmtree(root)


def profile(build, tmp_path, f):
    p = tmp_path / "f.json"
    p.write_text(json.dumps(f))
    lines = shaderc(build, "profile", p).stdout.splitlines()[1:]
    return dict(l.split("=") for l in lines)


def test_profile_follows_dxvks_rules(build, tmp_path):
    nv = profile(build, tmp_path, facts())
    assert (nv["compile.lower_itof"], nv["compile.lower_constant_arrays"], nv["compile.lower_ftoi"],
            nv["compile.lower_in_bounds_cbv_to_bda"], nv["spirv.supports_descriptor_heap"],
            nv["spirv.supports_nv_raw_access_chains"], nv["compile.supports_16bit_arithmetic"],
            nv["compile.semantic_io"], nv["limits.max_uniform_buffer_count"]) == ("0", "1", "1", "0", "1", "1", "0", "1", "-1")
    old_nv = profile(build, tmp_path, facts(driver_version_raw=(575 << 22) | (64 << 14)))
    assert (old_nv["compile.lower_itof"], old_nv["spirv.supports_descriptor_heap"]) == ("1", "0")
    lvp = profile(build, tmp_path, facts(driver_id=13, driver_version_raw=(25 << 22) | 7, descriptor_heap=False,
                                         nv_raw_access_chains=False))
    assert (lvp["compile.lower_constant_arrays"], lvp["compile.lower_itof"], lvp["spirv.supports_descriptor_heap"],
            lvp["compile.lower_ftoi"]) == ("0", "0", "0", "1")
    radv = profile(build, tmp_path, facts(driver_id=3, driver_version_raw=(25 << 22)))
    assert (radv["compile.lower_ftoi"], radv["compile.lower_f32_to_f16"], radv["compile.lower_in_bounds_cbv_to_bda"],
            radv["compile.lower_constant_arrays"], radv["spirv.supports_descriptor_heap"]) == ("0", "0", "1", "0", "1")
    anv = profile(build, tmp_path, facts(driver_id=6, driver_version_raw=(25 << 22) | 2, descriptor_heap=False))
    assert (anv["compile.lower_sin_cos"], anv["spirv.export_point_size"], anv["compile.lower_constant_arrays"]) == ("1", "1", "0")
    anv3 = profile(build, tmp_path, facts(driver_id=6, driver_version_raw=(25 << 22) | 3, descriptor_heap=False))
    assert anv3["spirv.export_point_size"] == "0"
    amd = profile(build, tmp_path, facts(driver_id=1, driver_version_raw=(2 << 22)))
    assert (amd["compile.lower_in_bounds_cbv_to_bda"], amd["compile.lower_constant_arrays"],
            amd["spirv.supports_descriptor_heap"]) == ("1", "1", "1")
    small = profile(build, tmp_path, facts(max_per_stage_descriptor_uniform_buffers=15))
    assert small["limits.max_uniform_buffer_count"] == "15"
    # host 2's own line, printed only when set: a device without 8- and 16-bit access
    assert "host2.state_words" not in nv
    words = profile(build, tmp_path, facts(sub_dword_access=False, shader_float16=True))
    assert (words["host2.state_words"], words["compile.supports_16bit_arithmetic"]) == ("1", "0")
    assert {k: v for k, v in words.items() if k != "host2.state_words"} == nv
    # and a device without shaderClipDistance (Mali): its own line, printed only when set
    assert "host2.no_clip_distance" not in nv
    noclip = profile(build, tmp_path, facts(clip_distance=False))
    assert noclip["host2.no_clip_distance"] == "1"
    assert {k: v for k, v in noclip.items() if k != "host2.no_clip_distance"} == nv


def test_without_sub_dword_access_the_state_is_read_as_32_bit_words(build, tmp_path):
    """A device without shaderInt8, shaderInt16 and 8- and 16-bit buffer access (SwiftShader, many
    low-end GPUs: VulkanFeatures(false)): every module is valid SPIR-V that declares no 8- or 16-bit
    type, its state buffer the same bytes as 32-bit words (member 7 at byte 28 holds the fog colour
    and the alpha reference, members 11 and 12 at bytes 44 and 48 the vertex block's four u16), and
    each sub-dword read is a bit extract of its word: the alpha reference bits 24-31 of member 7, the
    dynamic float count bits 0-15 of member 11, the point size bounds bits 0-15 and 16-31 of member
    12. The device's default leaves the 8- and 16-bit members (the control)."""
    f = tmp_path / "facts-words.json"
    f.write_text(json.dumps(facts(sub_dword_access=False)))
    full, words = tmp_path / "full", tmp_path / "words"
    names = ("vs_full", "ps_full", "ps_static")
    compile_into(build, full, list(names))
    words.mkdir()
    shaderc(build, "compile", f, words, *[build["shaders"][n] for n in names])
    dis = {}
    for side in (full, words):
        for name in names:
            r = subprocess.run([str(SPIRV_DIS), str(side / f"{name}.spv")], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            dis[side.name, name] = r.stdout
    for name in names:
        r = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", str(words / f"{name}.spv")],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stdout}{r.stderr}"
        assert reflection(words, name)["uniforms"] == reflection(full, name)["uniforms"]
        low, base = dis["words", name], dis["full", name]
        for text in ("OpCapability Int8", "OpCapability Int16", "OpTypeInt 8 0", "OpTypeInt 16 0"):
            assert text not in low, f"{name}: {text}"
        assert "OpCapability Int8" in base and "%v3uchar %uchar %float %float %float" in base
        assert "OpMemberDecorate %specData_buf 7 Offset 28" in low and "OpMemberDecorate %specData_buf 8 Offset 32" in low
    vs, ps = dis["words", "vs_full"], dis["words", "ps_full"]
    assert "OpCapability Int16" in dis["full", "vs_full"]
    assert ("%specData_buf = OpTypeStruct %uint %uint %uint %uint %uint %uint %uint %uint %float %float %float "
            "%uint %uint\n") in vs
    assert "OpMemberDecorate %specData_buf 11 Offset 44" in vs and "OpMemberDecorate %specData_buf 12 Offset 48" in vs
    assert "%specData_buf = OpTypeStruct %uint %uint %uint %uint %uint %uint %uint %uint %float %float %float\n" in ps

    def extracts(text, member):
        """The (offset, count) of every bit extract of a load of state member `member`."""
        lines, out = text.splitlines(), []
        chains = {l.split()[0] for l in lines if f"OpAccessChain %_ptr_Uniform_uint %specData %uint_{member}" in l}
        loads = {l.split()[0] for l in lines if " OpLoad %uint " in l and l.split()[-1] in chains}
        for l in lines:
            w = l.split()
            if len(w) == 7 and w[2] == "OpBitFieldUExtract" and w[4] in loads:
                out.append((w[5], w[6]))
        return sorted(out)
    assert extracts(ps, 7) == [("%uint_24", "%uint_8")]
    assert extracts(vs, 11) == [("%uint_0", "%uint_16")]
    assert extracts(vs, 12) == [("%uint_0", "%uint_16"), ("%uint_16", "%uint_16")]


def test_without_clip_distance_no_vertex_module_writes_it(build, tmp_path):
    """A device without shaderClipDistance (Mali-G715, the user's Pixel 9 Pro: VulkanFeatures(.., false)):
    every vertex module is valid SPIR-V that neither declares the ClipDistance capability nor
    decorates anything BuiltIn ClipDistance, and reads no clip-plane buffer (the distances, the buffer
    and the plane count go with the output), its other outputs and uniforms the full module's; the
    pixel modules are the full build's, byte for byte. The device's default (the control) declares
    the capability and writes the output in the vertex module."""
    f = tmp_path / "facts-noclip.json"
    f.write_text(json.dumps(facts(clip_distance=False)))
    full, noclip = tmp_path / "full", tmp_path / "noclip"
    names = ("vs_full", "vs_mismatch", "ps_full", "ps_static")
    compile_into(build, full, list(names))
    noclip.mkdir()
    shaderc(build, "compile", f, noclip, *[build["shaders"][n] for n in names])
    dis = {}
    for side in (full, noclip):
        for name in names:
            r = subprocess.run([str(SPIRV_DIS), str(side / f"{name}.spv")], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            dis[side.name, name] = r.stdout
    for name in names:
        r = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", str(noclip / f"{name}.spv")],
                           capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stdout}{r.stderr}"
        assert "ClipDistance" not in dis["noclip", name], name
    for name in ("vs_full", "vs_mismatch"):
        assert "OpCapability ClipDistance" in dis["full", name] and "BuiltIn ClipDistance" in dis["full", name]
        rf, rn = reflection(full, name), reflection(noclip, name)
        assert [o for o in rf["outputs"] if o["builtin"] != "ClipDistance"] == rn["outputs"]
        assert [o["builtin"] for o in rf["outputs"] if o["builtin"] == "ClipDistance"] == ["ClipDistance"]
        assert [u for u in rf["uniforms"] if u["kind"] != "clip_planes"] == rn["uniforms"]
        assert [u["kind"] for u in rf["uniforms"]].count("clip_planes") == 1
    for name in ("ps_full", "ps_static"):
        assert (full / f"{name}.spv").read_bytes() == (noclip / f"{name}.spv").read_bytes()


FAULTS = {
    "umin_dropped": "vs_full", "point_size_f32": "vs_full", "nocontraction_stripped": "vs_full",
    "fp16": "ps_full", "samplers_swapped": "ps_full", "constants_swapped": "ps_static",
    "denorm_ftz": "ps_full", "float_emulation_enabled": "vs_full",
}


def test_canonical_diff_passes_a_clean_build_and_fails_each_fault(build, tmp_path):
    clean = tmp_path / "clean"
    compile_into(build, clean, sorted(set(FAULTS.values())))
    for name in set(FAULTS.values()):
        assert spv_canon.compare(clean / f"{name}.spv", clean / f"{name}.spv", reference_side="host2")["ok"]
    for fault, name in FAULTS.items():
        out = tmp_path / fault
        compile_into(build, out, [name], env={"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": fault})
        r = spv_canon.compare(clean / f"{name}.spv", out / f"{name}.spv", reference_side="host2")
        assert not r["ok"], f"{fault} on {name} was not caught"
        assert r["failures"], fault
    r = shaderc(build, "compile", build["facts"], tmp_path, build["shaders"]["ps_full"],
                env={"SA2_D3D9GPU_SHADER_FAULT": "fp16"}, check=False)
    assert r.returncode == 1 and "only in a test run" in r.stdout


def test_lower_dot_off_cannot_build_a_module(build, tmp_path):
    """DXVK sets lowerDot (dxvk_shader_ir.cpp:2222-2264); dxbc-spirv's SPIR-V builder has no
    instruction for the FDot it would leave, so a translator without it stops loudly
    (spirv_builder.cpp: "Invalid opcode") rather than build a module that differs silently."""
    r = shaderc(build, "compile", build["facts"], tmp_path, build["shaders"]["vs_full"],
                env={"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": "lower_dot_off"}, check=False)
    assert r.returncode != 0 and "Invalid opcode FDot" in r.stdout + r.stderr and not (tmp_path / "vs_full.spv").exists()


def io_vars(path, storage):
    """(name, Location, Component) of every non-built-in variable of one storage class."""
    m = spv_canon.Module(path, "host2")
    return sorted((m.names.get(v.rid), int(m.deco(v.rid, "Location")[0]), int(m.deco(v.rid, "Component")[0]))
                  for v in m.insts if v.op == "OpVariable" and m.storage(v) == storage and m.deco(v.rid, "BuiltIn") is None)


def test_linkage_maps_the_pair_as_dxvks_linkage_does_and_its_fault_does_not(build, tmp_path):
    vs, ps = build["shaders"]["vs_mismatch"], build["shaders"]["ps_mismatch"]
    compile_into(build, tmp_path, ["vs_mismatch", "ps_mismatch"])
    assert io_vars(tmp_path / "vs_mismatch.spv", "Output") == [("o1_binormal", 12, 0), ("o2_tangent", 13, 0)]
    assert io_vars(tmp_path / "ps_mismatch.spv", "Input") == [("v0_tangent", 12, 0), ("v1_texcoord0", 1, 0)]
    r = shaderc(build, "link", build["facts"], tmp_path, vs, ps, "linked")
    assert "LINKED linked io_rewritten=1 compatible=0" in r.stdout
    assert io_vars(tmp_path / "linked.spv", "Input") == [("v0_tangent", 13, 0)]
    assert io_vars(tmp_path / "linked.spv", "Output") == io_vars(tmp_path / "ps_mismatch.spv", "Output") == [("oC0_color", 0, 0)]
    skipped = tmp_path / "skipped"
    skipped.mkdir()
    r = shaderc(build, "link", build["facts"], skipped, vs, ps, "linked",
                env={"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": "linkage_skipped"})
    assert "io_rewritten=0" in r.stdout
    assert (skipped / "linked.spv").read_bytes() == (tmp_path / "ps_mismatch.spv").read_bytes()
    v = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", str(tmp_path / "linked.spv")],
                       capture_output=True, text=True)
    assert v.returncode == 0, v.stdout + v.stderr
    assert not spv_canon.compare(tmp_path / "linked.spv", skipped / "linked.spv", reference_side="host2")["ok"]


def test_a_host2_unit_reaching_dxvks_constant_copy_fails_the_build(build, tmp_path):
    tree = tmp_path / "host" / "d3d9gpu"
    shutil.copytree(D9G, tree, ignore=shutil.ignore_patterns("__pycache__"))
    cache = tree / "d9g_shader_cache.cpp"
    cache.write_text(cache.read_text() + """
#include "d3d9_shader.h"
void d9g_mutant_constant_copy(const dxvk::D3D9ConstantBufferCopy& c, const dxvk::D3D9ConstantBufferCopyArgs& a) {
  c.copyConstantData(a);
}
""")
    r = subprocess.run(["bash", str(tree / "shader" / "build_translator.sh"), str(tmp_path / "out")],
                       capture_output=True, text=True, env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 2 and "copyConstantData" in r.stderr, r.stdout[-1000:] + r.stderr[-1000:]
    # The mutated unit moved the identity the build printed before its guard refused it.
    assert identity_line(r.stdout) != build["identity"]


def identity_line(stdout):
    return next(l for l in stdout.splitlines() if l.startswith("translator "))


def test_the_translator_links_only_the_pinned_archive(tmp_path):
    """build_translator.sh takes dxbc-spirv from tools/pins.json alone (dxbc_spirv_archive, its sha256):
    the build's thin archive under the pin's name, or a pin that does not hash to its file, is refused
    before anything is compiled (R1 C1, the build's side; check_pins.py --only dxvk is the gate's)."""
    pins = json.loads((ROOT / "tools" / "pins.json").read_text())
    for case in ("thin", "other_sha"):
        repo = tmp_path / case
        (repo / "tools").mkdir(parents=True)
        (repo / "private").mkdir()
        os.symlink(ROOT / "private" / "s5-tools", repo / "private" / "s5-tools")
        p = json.loads(json.dumps(pins))
        if case == "thin":
            p["tools"]["dxvk"]["dxbc_spirv_archive"] = p["tools"]["dxvk"]["dxbc_spirv_build"]
            data = (repo / p["tools"]["dxvk"]["dxbc_spirv_build"]).read_bytes()
            assert data.startswith(b"!<thin>\n")
            p["tools"]["dxvk"]["dxbc_spirv_archive_sha256"] = __import__("hashlib").sha256(data).hexdigest()
            want = "is not a regular archive"
        else:
            p["tools"]["dxvk"]["dxbc_spirv_archive_sha256"] = "0" * 64
            want = "not its pin"
        (repo / "tools" / "pins.json").write_text(json.dumps(p))
        r = subprocess.run(["bash", str(D9G / "shader" / "build_translator.sh"), str(tmp_path / f"out-{case}")],
                           capture_output=True, text=True, env={**os.environ, "SA2_REPO": str(repo), "PYTHON": sys.executable})
        assert r.returncode == 2 and want in r.stderr and "the dxbc-spirv archive is refused" in r.stderr, (case, r.stderr[-800:])
        assert not (tmp_path / f"out-{case}").exists()


def test_the_identity_is_the_contents_not_the_path(build, tmp_path):
    other = tmp_path / "elsewhere" / "build"
    r = subprocess.run(["bash", str(D9G / "shader" / "build_translator.sh"), str(other)], capture_output=True, text=True,
                       env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 0, r.stderr[-1000:]
    assert identity_line(r.stdout) == build["identity"]
    manifest = json.loads((other / "translator_identity.json").read_text())
    assert manifest["id"] == build["identity"][len("translator "):]
    assert "build:d9g_dxvk_lower.gen.cpp" in manifest["files"]
    assert {"dxvk:src/d3d9/d3d9_shader.h", "dxvk:src/d3d9/d3d9_constant_copy.h", "repo:host/d3d9gpu/d9g_shader_link.cpp"} <= set(manifest["files"])
    assert not [f for f in manifest["files"] if str(tmp_path) in f or str(ROOT) in f]


def test_a_translator_without_its_identity_may_not_use_a_cache(build, tmp_path):
    tree = tmp_path / "host" / "d3d9gpu"
    shutil.copytree(D9G, tree, ignore=shutil.ignore_patterns("__pycache__"))
    script = tree / "shader" / "build_translator.sh"
    text = script.read_text()
    assert text.count('DEF=(-DD9G_TRANSLATOR_ID="\\"$TRANSLATOR_ID\\"")') == 1
    script.write_text(text.replace('DEF=(-DD9G_TRANSLATOR_ID="\\"$TRANSLATOR_ID\\"")', "DEF=()"))
    out = tmp_path / "out"
    r = subprocess.run(["bash", str(script), str(out)], capture_output=True, text=True, env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 0, r.stderr[-1000:]
    root = ROOT / "private" / "generated" / "s5" / "r1" / "test-cache" / tmp_path.name
    try:
        c = subprocess.run([str(out / "d3d9gpu_shaderc"), "compile", str(build["facts"]), str(tmp_path), "--cache", str(root),
                            "--repo", str(ROOT), str(build["shaders"]["ps_full"])], capture_output=True, text=True)
        assert c.returncode == 2 and "REFUSED" in c.stderr and '"unbuilt"' in c.stderr, c.stdout + c.stderr
        assert not root.exists() or not any(root.rglob("*.spv"))
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_sample_rate_shading_is_refused_not_ignored(build, tmp_path):
    r = shaderc(build, "compile", build["facts"], tmp_path, "--force-sample-rate-shading", build["shaders"]["ps_full"],
                check=False)
    assert r.returncode == 1
    assert "FAIL ps_full: unmapped: sample-rate shading (d3d9.forceSampleRateShading, dxvk_shader_ir.cpp:2079-2082) is not mirrored" in r.stdout
    r = shaderc(build, "compile", build["facts"], tmp_path, "--force-sample-rate-shading", build["shaders"]["vs_full"])
    assert "SHADERC_OK 1 translated" in r.stdout


def test_dxvk_conf_pins_what_the_profile_assumes():
    conf = (ROOT / "host" / "player" / "dxvk.conf").read_text().splitlines()
    assert "d3d9.floatEmulation = Strict" in conf
    assert "d3d9.useFP16 = False" in conf


# --- the state buffer's layout (spv_canon.py state_layout) ------------------------------------
OP_MEMBER_NAME, OP_MEMBER_DECORATE, DECORATION_OFFSET = 6, 72, 35


def words_of(path):
    data = path.read_bytes()
    return list(struct.unpack("<%dI" % (len(data) // 4), data))


def write_words(path, words):
    path.write_bytes(struct.pack("<%dI" % len(words), *words))


def state_block(path):
    m = spv_canon.Module(path, "host2")
    var = next(i for i in m.insts if i.op == "OpVariable" and m.names.get(i.rid) == "specData")
    block = m.pointee(var).rid
    return block, {n: k for (s, k), n in m.member_names.items() if s == block}


def swap_offsets(src, dst, a, b):
    block, _ = state_block(src)
    w, pos, at = words_of(src), 5, {}
    while pos < len(w):
        if w[pos] & 0xFFFF == OP_MEMBER_DECORATE and w[pos + 1] == block and w[pos + 3] == DECORATION_OFFSET:
            at[w[pos + 2]] = pos + 4
        pos += w[pos] >> 16
    w[at[a]], w[at[b]] = w[at[b]], w[at[a]]
    write_words(dst, w)


def swap_names(src, dst, a, b):
    block, names = state_block(src)
    ka, kb = names[a], names[b]
    w, out, pos = words_of(src), None, 5
    out = w[:5]
    while pos < len(w):
        wc, inst = w[pos] >> 16, w[pos:pos + (w[pos] >> 16)]
        if inst[0] & 0xFFFF == OP_MEMBER_NAME and inst[1] == block and inst[2] in (ka, kb):
            raw = (b if inst[2] == ka else a).encode() + b"\0"
            raw += b"\0" * (-len(raw) % 4)
            body = [inst[1], inst[2], *struct.unpack("<%dI" % (len(raw) // 4), raw)]
            inst = [((len(body) + 1) << 16) | OP_MEMBER_NAME, *body]
        out += inst
        pos += wc
    write_words(dst, out)


def test_a_state_buffer_at_other_offsets_or_names_fails_the_diff(build, tmp_path):
    compile_into(build, tmp_path, ["vs_full", "ps_full"])
    for name in ("vs_full", "ps_full"):
        clean = tmp_path / f"{name}.spv"
        assert spv_canon.compare(clean, clean, reference_side="host2")["ok"]
        _, names = state_block(clean)
        mutants = {
            "spec dwords 0 and 1 swapped": lambda d: swap_offsets(clean, d, 0, 1),
            "fogDistanceScale and fogDensity swapped": lambda d: swap_offsets(clean, d, names["fogDistanceScale"], names["fogDensity"]),
            "fogDistanceScale and fogDensity renamed": lambda d: swap_names(clean, d, "fogDistanceScale", "fogDensity"),
        }
        if name == "vs_full":
            mutants["pointSize and pointSizeMax swapped"] = lambda d: swap_offsets(clean, d, names["pointSize"], names["pointSizeMax"])
        for what, make in mutants.items():
            dst = tmp_path / f"{name}-{len(what)}.spv"
            make(dst)
            v = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", "--scalar-block-layout", str(dst)],
                               capture_output=True, text=True)
            assert v.returncode == 0, (name, what, v.stdout + v.stderr)  # valid SPIR-V: only the comparer can see it
            r = spv_canon.compare(clean, dst, reference_side="host2")
            assert not r["ok"], (name, what)
            assert any("state" in f and "Offset" in f for f in r["failures"]), (name, what, r["failures"])


# --- the comparer's DXVK side, on hand-built pairs (test/spv_pairs.py) ---------------------------
import spv_pairs  # noqa: E402

SPIRV_AS = TOOLS / "spirv-as"


def assemble(text, path):
    path.with_suffix(".spvasm").write_text(text)
    r = subprocess.run([str(SPIRV_AS), "--target-env", "spv1.6", "-o", str(path), str(path.with_suffix(".spvasm"))],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return path


@pytest.fixture(scope="module")
def pairs(tmp_path_factory):
    assert SPIRV_AS.exists(), f"{SPIRV_AS} is missing: the comparer's pairs need it (it fails, it does not skip)"
    d = tmp_path_factory.mktemp("spv_pairs")
    out = {}
    for kind, gen, faults in (("fs", spv_pairs.fragment, spv_pairs.FRAGMENT_FAULTS),
                              ("vs", spv_pairs.vertex, spv_pairs.VERTEX_FAULTS)):
        out[kind] = {"dxvk": assemble(gen("dxvk"), d / f"{kind}.dxvk.spv"),
                     "host2": assemble(gen("host2"), d / f"{kind}.host2.spv"), "faults": {}}
        for fault, side in faults.items():
            out[kind]["faults"][fault] = (side, assemble(gen(side, fault), d / f"{kind}.{side}.{fault}.spv"))
    return out


def test_clean_pairs_pass_with_every_dxvk_side_class_seen(pairs):
    fs = spv_canon.compare(pairs["fs"]["dxvk"], pairs["fs"]["host2"])
    vs = spv_canon.compare(pairs["vs"]["dxvk"], pairs["vs"]["host2"])
    assert fs["ok"] and vs["ok"], (fs["failures"], vs["failures"])
    assert fs["classes"]["dxvk"] == {"heap_capabilities": 1, "push_data": 2, "sampler_descriptor": 1, "set_binding": 3,
                                     "spec_data": 1, "state_layout": 2}
    assert fs["classes"]["host2"] == {"push_data": 2, "set_binding": 3, "spec_data": 1, "state_layout": 1}
    assert vs["classes"]["dxvk"] == vs["classes"]["host2"] == {"dynamic_cf_length": 1, "set_binding": 1}


EXPECTED_FAILURE = {
    "push_member_renamed": "STATE[push:fogDensity]",
    "spec_dword_moved": "STATE[spec:1]",
    "sampler_register_moved": "descriptor roles differ",
    "extra_capability_on_host2": "capability Float16 only in host 2's module",
    "extra_capability_on_dxvk": "capability ImageQuery only in DXVK's module",
    "state_offset_moved": "the host2 module's state member alphaRef is Int(8,0) at Offset 30, not Int(8,0) at 31",
    "push_offset_moved": "the dxvk module's push member alphaRef is Int(8,0) at Offset 2, not Int(8,0) at 3",
    "heap_index_not_push": "a sampler heap index that is not a sampler push member",
    "spec_offset_moved": "the dxvk module's spec dword 0 is Int(32,0) at Offset 4, not Int(32,0) at 0",
    "dynamic_length_255": "dynamic float buffer length 257 / 255",
    "dynamic_length_258": "dynamic float buffer length 258 / 256",
    "execution_mode_dropped": "execution modes differ",
    "relaxed_precision_on_value": "decorations differ",
    "relaxed_precision_on_state_member": "the host2 module's state descriptor's member alphaRef is decorated RelaxedPrecision",
    "relaxed_precision_on_image": "the host2 module's image:0 descriptor is decorated RelaxedPrecision",
    "texture_in_vertex_set": "image:0 is bound in set 0, not in the host2 side's texture set 2",
    "image_in_sampler_set": "image:0 is bound in set 0, not in the dxvk side's texture set 2",
}


@pytest.mark.parametrize("kind,fault", [("fs", f) for f in spv_pairs.FRAGMENT_FAULTS] + [("vs", f) for f in spv_pairs.VERTEX_FAULTS])
def test_each_broken_rule_fails_the_pair(pairs, kind, fault):
    side, broken = pairs[kind]["faults"][fault]
    dxvk = broken if side == "dxvk" else pairs[kind]["dxvk"]
    host2 = broken if side == "host2" else pairs[kind]["host2"]
    r = spv_canon.compare(dxvk, host2)
    assert not r["ok"], fault
    assert any(EXPECTED_FAILURE[fault] in f for f in r["failures"]), (fault, r["failures"])


def batch_dirs(pairs, root):
    dx, h2 = root / "dxvk", root / "host2"
    dx.mkdir(parents=True)
    h2.mkdir()
    for kind, name in (("fs", "fs.pair"), ("vs", "vs.pair")):
        shutil.copy(pairs[kind]["dxvk"], dx / f"{name}.spv")
        shutil.copy(pairs[kind]["host2"], h2 / f"{kind}0.spv")
        (h2 / f"{kind}0.json").write_text(json.dumps({"schema": spv_canon.REFLECTION_SCHEMA, "debug_name": name,
                                                     "variant": "bgra=0;border_u=0;border_v=0;border_linear=0;depth_clamp=0"}))
    return dx, h2


def run_batch(dx, h2, *extra):
    r = subprocess.run([sys.executable, str(D9G / "test" / "spv_canon.py"), "--batch", str(dx), str(h2), *extra],
                       capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    report = json.loads("\n".join(lines[:-1]))
    return r.returncode, lines[-1], report["failures"]


def test_the_batch_fails_on_anything_it_cannot_pair(pairs, tmp_path):
    rc, marker, failures = run_batch(*batch_dirs(pairs, tmp_path / "clean"), "--expect", "2")
    assert (rc, marker, failures) == (0, "SPV_CANON_OK", {})
    cases = {
        "truncated": (lambda dx, h2: (h2 / "fs0.json").write_text('{"schema": "sa2-d3d9gpu'),
                      "fs0", "unreadable reflection fs0.json"),
        "no_reflection": (lambda dx, h2: (h2 / "vs0.json").unlink(), "vs0", "module vs0.spv has no reflection"),
        "foreign": (lambda dx, h2: (h2 / "vs0.json").write_text(json.dumps({"schema": "something-else/v1", "debug_name": "vs.pair"})),
                    "vs0", "vs0.json is not a sa2-d3d9gpu-shader-reflection/v1 reflection"),
        "no_module": (lambda dx, h2: (h2 / "fs0.spv").unlink(), "fs0", "fs0.json has no module fs0.spv"),
        "unmatched": (lambda dx, h2: shutil.copy(pairs["fs"]["dxvk"], dx / "fs.other.spv"),
                      "dxvk:fs.other", "DXVK module fs.other.spv is named by no host-2 shader"),
    }
    for case, (make, key, why) in cases.items():
        dx, h2 = batch_dirs(pairs, tmp_path / case)
        make(dx, h2)
        rc, marker, failures = run_batch(dx, h2)
        assert (rc, marker) == (1, "SPV_CANON_FAIL") and any(why in f for f in failures.get(key, [])), (case, failures)
    rc, marker, failures = run_batch(*batch_dirs(pairs, tmp_path / "count"), "--expect", "3")
    assert (rc, marker) == (1, "SPV_CANON_FAIL") and failures == {"host2:count": ["2 host-2 shaders, expected 3"]}


# --- ruling 3's border fallback, the shader side (R1 U11) ------------------------------------------
def test_border_variants_add_the_colour_buffer_and_are_refused_where_they_cannot_apply(build, tmp_path):
    base, point, linear = tmp_path / "base", tmp_path / "point", tmp_path / "linear"
    compile_into(build, base, ["ps_full"])
    compile_into(build, point, ["ps_full"], "--border-u", "1", "--border-v", "8")
    compile_into(build, linear, ["ps_full"], "--border-u", "0x88", "--border-v", "0x80", "--border-linear", "0x80")
    b, p, l = (reflection(d, "ps_full") for d in (base, point, linear))
    assert [(u["slot"], u["kind"], u["bytes"]) for u in b["uniforms"]] == [(1, "state", 44)] and b["num_uniform_buffers"] == 2
    for r in (p, l):
        assert [(u["slot"], u["kind"], u["bytes"]) for u in r["uniforms"]] == [(1, "state", 44), (3, "border_colours", 256)]
        assert r["num_uniform_buffers"] == 4 and r["sampler_registers"] == [0, 3, 7]
    assert p["variant"] == "bgra=0;border_u=0x1;border_v=0x8;border_linear=0;depth_clamp=0;sampler_modes=none"
    assert l["variant"] == "bgra=0;border_u=0x88;border_v=0x80;border_linear=0x80;depth_clamp=0;sampler_modes=none"
    # the reads the emulation leaves: dxbc-spirv's sampling function's depth-compare sample and gather on
    # every border register (taken where the spec data's sampler mode says so), which the device must
    # leave unmapped on a fallback device (R2-draws)
    assert b["border_unwrapped"] == {"depth_compare": 0, "gather": 0, "other": 0}
    assert p["border_unwrapped"] == {"depth_compare": 0b1001, "gather": 0b1001, "other": 0}
    assert l["border_unwrapped"] == {"depth_compare": 0x88, "gather": 0x88, "other": 0}
    for d in (point, linear):
        spv = d / "ps_full.spv"
        assert spv.read_bytes() != (base / "ps_full.spv").read_bytes()
        v = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", "--scalar-block-layout", str(spv)],
                           capture_output=True, text=True)
        assert v.returncode == 0, v.stdout + v.stderr
        assert descriptor_bindings(spv)["borderColours"] == (3, 3)
    # the IR: point filtering selects, linear filtering weights by the level-0 extent
    assert (point / "ps_full.ir.txt").read_text().count("BufferLoad") > (base / "ps_full.ir.txt").read_text().count("BufferLoad")
    assert "ImageQuerySize" in (linear / "ps_full.ir.txt").read_text()
    refusals = {
        ("vs_full", "--border-u", "1"): "pixel shaders only",
        ("ps_full", "--border-u", "2"): "sampler register 1, which the shader never samples in 2D",
        ("ps_full", "--border-linear", "1"): "border_linear names a register with no BORDER axis",
        ("ps_full", "--depth-clamp"): "the depth-clamp emulation is built only if",
    }
    for (shader, *opts), why in refusals.items():
        r = shaderc(build, "compile", build["facts"], tmp_path, *opts, build["shaders"][shader], check=False)
        assert r.returncode == 1 and why in r.stdout, (opts, r.stdout)
    off = tmp_path / "off"
    compile_into(build, off, ["ps_full"], "--border-u", "1", env={"SA2_D3D9GPU_TEST_RUN": "1", "SA2_D3D9GPU_SHADER_FAULT": "border_off"})
    # the emulation's control: the fault leaves the variant's module without the blend, and its colour buffer unread
    assert "borderColours" not in descriptor_bindings(off / "ps_full.spv")


# --- the canonical diff's linked half (spv_canon.py --linked, --linked-batch) ----------------------
@pytest.fixture(scope="module")
def linked(tmp_path_factory, pairs):
    d = tmp_path_factory.mktemp("linked")
    out = {"fs": assemble(spv_pairs.fragment_linked(), d / "fs.linked.spv"),
           "vs": assemble(spv_pairs.vertex_linked(side="dxvk_captured"), d / "vs.captured.spv"),
           "vs_dump": assemble(spv_pairs.vertex_linked(side="dxvk"), d / "vs.dump.spv"),
           "vs_host2": assemble(spv_pairs.vertex_linked(side="host2"), d / "vs.host2.spv"),
           "fs_library": assemble(spv_pairs.with_debug(spv_pairs.fragment("dxvk"), spv_pairs.FS_DEBUG), d / "fs.library.spv"),
           "fs_library_bad": assemble(spv_pairs.with_debug(spv_pairs.fragment("dxvk", "spec_offset_moved"), spv_pairs.FS_DEBUG),
                                      d / "fs.library-bad.spv"),
           # a library module with a capability the dump lacks: two DXVK modules allow no capability freedom,
           # neither outside heap_capabilities' list (ImageQuery) nor inside it (Int16)
           "fs_library_cap": assemble(spv_pairs.with_debug(spv_pairs.fragment("dxvk", "extra_capability_on_dxvk"),
                                                           spv_pairs.FS_DEBUG), d / "fs.library-cap.spv"),
           "vs_library_cap": assemble(spv_pairs.vertex_linked(side="dxvk").replace(
               "               OpCapability Shader\n", "               OpCapability Shader\n               OpCapability Int16\n", 1),
               d / "vs.library-cap.spv"),
           "faults": {f: assemble(spv_pairs.fragment_linked(f), d / f"fs.{f}.spv") for f in spv_pairs.LINKED_FAULTS}}
    return out


def test_dxvks_linked_variant_is_host_2s_only_in_linked_mode(pairs, linked):
    roles = spv_canon.dump_roles(pairs["fs"]["dxvk"])
    assert roles == {"commonState": "spec"}
    r = spv_canon.compare(linked["fs"], pairs["fs"]["host2"], linked=True, roles=roles)
    assert r["ok"], r["failures"]
    assert r["classes"]["dxvk"]["spec_data"] == 2 and r["classes"]["host2"]["spec_data"] == 1
    assert not spv_canon.compare(linked["fs"], pairs["fs"]["host2"], roles=roles)["ok"]   # not in --linked mode
    for fault, want in (("linked_spec_id_moved", "STATE[spec:1]"), ("linked_push_member_moved", "alphaRef is Int(8,0) at Offset 18")):
        r = spv_canon.compare(linked["faults"][fault], pairs["fs"]["host2"], linked=True, roles=roles)
        assert not r["ok"] and any(want in f for f in r["failures"]), (fault, r["failures"])
    # a captured vertex module: its dynamic float buffer at another set and binding, its role from the dump
    vr = spv_canon.dump_roles(linked["vs_dump"])
    assert vr == {"cF": "dynamic"}
    assert spv_canon.compare(linked["vs"], linked["vs_host2"], linked=True, roles=vr)["ok"]
    assert not spv_canon.compare(linked["vs"], linked["vs_host2"], linked=True)["ok"]   # without the dump's roles


VK_FORMAT_R32G32B32A32_SFLOAT, VK_FORMAT_B8G8R8A8_UNORM = 109, 44


def capture_dir(root, linked, pairs, records, host2_mask=0, extra_dump=False):
    """A capture as vkcap writes it: records are (kind, stages) or (kind, stages, overrides), kind
    linked (a monolithic pipeline with its vertex input: location 0 R32G32B32A32, or B8G8R8A8 where the
    overrides' "bgra" is set), library (the library bit in flags, or in flags2 with "flags2"), or
    internal; the host-2 pair's vertex shader carries host2_mask; extra_dump adds a dumped shader no
    library module carries."""
    import hashlib as h
    cap, dump, pd = root / "capture", root / "dump", root / "pairs"
    for d in (cap, dump, pd):
        d.mkdir(parents=True)
    mods = {}
    for key, path in (("vs", linked["vs"]), ("fs", linked["fs"]), ("fs_dump", linked["fs_library"]), ("vs_dump", linked["vs_dump"]),
                      ("fs_bad", linked["fs_library_bad"]), ("fs_cap", linked["fs_library_cap"]),
                      ("vs_cap", linked["vs_library_cap"]),
                      ("internal", pairs["fs"]["faults"]["spec_offset_moved"][1])):
        data = path.read_bytes()
        mods[key] = h.sha256(data).hexdigest()
        (cap / f"module-{mods[key]}.spv").write_bytes(data)
    shutil.copy(pairs["fs"]["dxvk"], dump / f"{spv_pairs.FS_DEBUG}.spv")
    shutil.copy(linked["vs_dump"], dump / f"{spv_pairs.VS_DEBUG}.spv")
    if extra_dump:
        shutil.copy(linked["vs_dump"], dump / "vs.00000000000000000000000000000000.spv")
    shutil.copy(linked["vs_host2"], pd / "pair0.vs.spv")
    shutil.copy(pairs["fs"]["host2"], pd / "pair0.ps.spv")
    (pd / "pair0.json").write_text(json.dumps({
        "schema": spv_canon.PAIR_SCHEMA, "name": "pair0", "drawn": True,
        "vs": {"name": "vs0", "debug_name": spv_pairs.VS_DEBUG, "variant": f"bgra={host2_mask}", "module": "pair0.vs.spv"},
        "ps": {"name": "ps0", "debug_name": spv_pairs.FS_DEBUG, "module": "pair0.ps.spv"}}))
    lines = []
    for n, record in enumerate(records):
        kind, stages = record[:2]
        over = record[2] if len(record) > 2 else {}
        flags = {"linked": (0, 0, 0), "library": (0x800, 4, 0), "internal": (0, 0, 0)}[kind]
        flags2 = 0
        if kind == "library" and over.get("flags2"):
            flags, flags2 = (0, 4, 0), 0x1000000800
        if over.get("lost_flags"):
            flags = (0, 0, 0)
        vi = None
        if kind == "linked" and not over.get("no_vertex_input"):
            fmt = VK_FORMAT_B8G8R8A8_UNORM if over.get("bgra") else VK_FORMAT_R32G32B32A32_SFLOAT
            vi = {"attributes": [[0, 0, fmt, 0]], "bindings": [[0, 16, 0]]}
        lines.append(json.dumps({"load": "t", "seq": n, "device": 0, "result": 0, "flags": flags[0], "flags2": flags2,
                                 "gpl_flags": flags[1], "libraries": flags[2], "raster": None, "vertex_input": vi,
                                 "stages": [{"stage": st, "module": mods[m], "inline": True, "entry": "main", "spec": None}
                                            for st, m in stages]}))
    (cap / "pipelines.jsonl").write_text("\n".join(lines) + "\n")
    return cap, pd, dump


def run_linked(cap, pd, dump, *extra):
    r = subprocess.run([sys.executable, str(D9G / "test" / "spv_canon.py"), "--linked-batch", str(cap), str(pd),
                        "--gpl-dump", str(dump), *extra], capture_output=True, text=True)
    lines = r.stdout.strip().splitlines()
    return r.returncode, lines[-1], json.loads("\n".join(lines[:-1]))


def test_the_linked_batch_fails_on_anything_it_cannot_hold(pairs, linked, tmp_path):
    good = [("linked", [("vertex", "vs"), ("fragment", "fs")]), ("library", [("fragment", "fs_dump")]),
            ("library", [("vertex", "vs_dump")]),
            ("internal", [("vertex", "internal")]),   # no D3D9 debug name: DXVK's own pipeline, left
            ("internal", [])]                         # a stageless library part or a fast link, left
    rc, marker, rep = run_linked(*capture_dir(tmp_path / "good", linked, pairs, good), "--expect-drawn", "1")
    assert (rc, marker) == (0, "SPV_CANON_LINKED_OK"), rep["failures"]
    assert (rep["linked_module_pairs"], rep["linked_keys"], rep["linked_bgra_masks"], rep["compared"],
            rep["dxvk_internal_pipelines"], rep["stageless_pipelines"], rep["gpl"]) == \
        (1, 1, [0], 2, 1, 1, {"library_modules": 2, "equal_to_dump": 2})
    # the library bit where DXVK sets it, in VkPipelineCreateFlags2
    flags2 = good[:1] + [("library", [("fragment", "fs_dump")], {"flags2": True}), ("library", [("vertex", "vs_dump")])]
    rc, marker, rep = run_linked(*capture_dir(tmp_path / "flags2", linked, pairs, flags2), "--expect-drawn", "1")
    assert (rc, marker) == (0, "SPV_CANON_LINKED_OK"), rep["failures"]
    # a pair matched by its mask: DXVK's vertex input says B8G8R8A8 at location 0, and so does host 2's variant
    bgra = [("linked", [("vertex", "vs"), ("fragment", "fs")], {"bgra": True})] + good[1:3]
    rc, marker, rep = run_linked(*capture_dir(tmp_path / "bgra", linked, pairs, bgra, host2_mask=1), "--expect-drawn", "1")
    assert (rc, marker, rep["linked_bgra_masks"]) == (0, "SPV_CANON_LINKED_OK", [1]), rep["failures"]
    libs = good[1:3]
    cases = {
        "no_capture": ([("library", [("fragment", "fs_dump")]), ("library", [("vertex", "vs_dump")])], {}, "pair0",
                       "a drawn pair with no linked pipeline"),
        "library_not_dump": (good[:1] + [("library", [("fragment", "fs_bad")]), libs[1]], {}, None,
                             "a pipeline-library module that is not"),
        "library_extra_capability": (good[:1] + [("library", [("fragment", "fs_cap")]), libs[1]], {}, None,
                                     "capability ImageQuery only in DXVK's module"),
        "library_extra_heap_capability": (good[:1] + [libs[0], ("library", [("vertex", "vs_cap")])], {}, None,
                                          "capability Int16 only in DXVK's module"),
        "no_library": (good[:1], {}, "gpl", "no pipeline-library module"),
        "library_lost": (good[:1] + libs[:1], {}, f"gpl:{spv_pairs.VS_DEBUG}", "a shader DXVK dumped that no pipeline-library"),
        "library_flags_lost": (good[:1] + libs[:1] + [("library", [("vertex", "vs_dump")], {"lost_flags": True})], {}, None,
                               "neither linked nor a pipeline library"),
        "extra_dump": (good[:3], {"extra_dump": True}, "gpl:vs.00000000000000000000000000000000",
                       "a shader DXVK dumped that no pipeline-library"),
        "no_vertex_input": ([("linked", [("vertex", "vs"), ("fragment", "fs")], {"no_vertex_input": True})] + libs, {}, None,
                            "carries no vertex input"),
        # DXVK's vertex format says a D3DCOLOR at location 0; host 2 planned the pair without the swizzle
        "mask_mismatch": ([("linked", [("vertex", "vs"), ("fragment", "fs")], {"bgra": True})] + libs, {}, None,
                          "this pair and BGRA mask has no host-2 pair"),
        "mask_mismatch_other_way": (good[:3], {"host2_mask": 1}, "pair0", "a drawn pair with no linked pipeline"),
    }
    for case, (records, opts, key, why) in cases.items():
        rc, marker, rep = run_linked(*capture_dir(tmp_path / case, linked, pairs, records, **opts))
        hits = [f for k, v in rep["failures"].items() if key is None or k == key for f in v]
        assert (rc, marker) == (1, "SPV_CANON_FAIL") and any(why in f for f in hits), (case, rep["failures"])
    rc, marker, rep = run_linked(*capture_dir(tmp_path / "count", linked, pairs, good), "--expect-drawn", "2")
    assert (rc, marker) == (1, "SPV_CANON_FAIL") and rep["failures"]["host2:drawn"] == ["1 drawn host-2 shader pairs, expected 2"]


# --- the GPU script's plan and verdict (test/r1_receipt.py) ------------------------------------------
import r1_receipt  # noqa: E402


def test_the_bgra_mask_follows_the_signature_not_the_declaration():
    # signature: POSITION 0, COLOR 0, TEXCOORD 0; the declaration lists TEXCOORD first and COLOR as D3DCOLOR
    sig = [[0, 0], [10, 0], [5, 0]]
    elems = [[0, 16, 1, 0, 5, 0], [0, 0, 2, 0, 0, 0], [0, 12, 4, 0, 10, 0]]
    assert r1_receipt.bgra_mask(sig, elems) == 0b010
    # POSITIONT reads as POSITION; an element the signature does not read adds nothing
    assert r1_receipt.bgra_mask([[0, 0]], [[0, 0, 4, 0, 9, 0], [0, 4, 4, 0, 10, 0]]) == 0b1
    assert r1_receipt.bgra_mask([[5, 1]], [[0, 0, 4, 0, 5, 0]]) == 0


def test_the_border_fallback_variant_follows_the_sampler_and_the_shader():
    # D3DSAMP_ADDRESSU/V BORDER (4), MAG/MIN LINEAR (2): the census's BORDER sampler at stages 5 and 6
    smp = {"ss": [0, 4, 4, 3, 0, 2, 2, 2, 0, 0, 1, 0, 0, 0], "stages": 0b1100000, "cube": False, "depth": False}
    assert r1_receipt.border_variant(smp, 0b1110000) == (0b1100000, 0b1100000, 0b1100000)
    assert r1_receipt.border_variant(smp, 0b0000001) is None                       # the shader samples none of its stages
    assert r1_receipt.border_variant({**smp, "ss": [0, 4, 1, 3, 0, 1, 1, 2, 0, 0, 1, 0, 0, 0]}, 0b100000) == (0b100000, 0, 0)
    assert r1_receipt.border_variant({**smp, "ss": [0, 3, 3, 3, 0, 2, 2, 2, 0, 0, 1, 0, 0, 0]}, 0b100000) is None
    assert r1_receipt.border_variant({**smp, "depth": True}, 0b100000) == "a cube or depth texture"
    assert r1_receipt.border_variant({**smp, "ss": [0, 4, 4, 3, 0, 2, 1, 2, 0, 0, 1, 0, 0, 0]}, 0b100000) == \
        "MAGFILTER 2 and MINFILTER 1"


def test_the_verdict_is_ok_only_with_every_check_passed_and_no_validation_error(tmp_path):
    def run_with(mutate):
        d = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
        (d / "checks").mkdir(parents=True)
        checks = {c: {"check": c, "pass": True} for c in r1_receipt.EXPECTED}
        mutate(checks, d)
        for c, v in checks.items():
            (d / "checks" / f"{c}.json").write_text(json.dumps(v))
        return r1_receipt.decide(d)[:2]
    assert run_with(lambda c, d: None) == ("R1_SHADERS_OK", 0)
    assert run_with(lambda c, d: c.pop("C9-lvp")) == ("R1_SHADERS_FAIL C9-lvp:missing", 1)
    assert run_with(lambda c, d: c["C3-nvidia"].update(**{"pass": False})) == ("R1_SHADERS_FAIL C3-nvidia", 1)
    assert run_with(lambda c, d: c["C8-nvidia"].update(validation_errors={"VUID-x": 2})) == \
        ("R1_SHADERS_FAIL VALIDATION C8-nvidia:VUID-x", 1)
    assert run_with(lambda c, d: (d / "checks" / "harness.txt").write_text("a step\n")) == ("R1_SHADERS_FAIL harness", 1)


# --- the canonical diff's labels: values of one shape (review BLOCKER of 2026-10-05) ----------------
SPIRV_DIS = TOOLS / "spirv-dis"


def raw_text(path):
    r = subprocess.run([str(SPIRV_DIS), "--raw-id", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def reassemble(lines, path):
    path.with_suffix(".spvasm").write_text("\n".join(lines) + "\n")
    r = subprocess.run([str(SPIRV_AS), "--preserve-numeric-ids", "--target-env", "vulkan1.3", "-o", str(path),
                        str(path.with_suffix(".spvasm"))], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    v = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", "--scalar-block-layout", str(path)],
                       capture_output=True, text=True)
    assert v.returncode == 0, v.stdout + v.stderr   # valid SPIR-V: only the comparer can see the change
    return path


def uses(line, vid):
    return vid in line.split("=", 1)[-1].split()[1:]


def replace_use(line, old, new):
    lhs, eq, rhs = line.partition("=") if " = " in line else ("", "", line)
    toks = rhs.split()
    head, rest = toks[:1], [new if x == old else x for x in toks[1:]]
    return (lhs + eq + " " if eq else "") + " ".join(head + rest)


def loop_phis(lines):
    """(phis of the first loop header as (id, type), the line of its OpLoopMerge, its merge label)."""
    for n, l in enumerate(lines):
        if "OpLoopMerge" in l:
            k, phis = n - 1, []
            while "OpPhi" in lines[k]:
                lhs, rhs = lines[k].split("=", 1)
                phis.insert(0, (lhs.strip(), rhs.split()[1]))
                k -= 1
            return phis, n, l.split()[1]
    raise AssertionError("no loop in the module")


def swap_in(lines, start, stop, a, b):
    """lines with the first instruction in [start, stop) that reads a and not b reading b instead."""
    for n in range(start, stop):
        l = lines[n]
        if "OpPhi" not in l and uses(l, a) and not uses(l, b):
            out = list(lines)
            out[n] = replace_use(l, a, b)
            return out
    return None


def label_mutants(lines):
    """The swaps of the review's counterexamples, found in the module rather than by number: a loop
    accumulator read after the loop and inside it in place of another phi of its type, and a helper
    function reading one parameter in place of another of its type."""
    phis, merge_at, merge = loop_phis(lines)
    merge_line = next(n for n, l in enumerate(lines) if l.strip().startswith(f"{merge} = OpLabel"))
    out = {}
    for a, ta in phis:
        for b, tb in phis:
            if a == b or ta != tb:
                continue
            if "after_the_loop" not in out:
                m = swap_in(lines, merge_line, len(lines), a, b)
                if m:
                    out["after_the_loop"] = m
            if "inside_the_loop" not in out:
                m = swap_in(lines, merge_at, merge_line, a, b)
                if m:
                    out["inside_the_loop"] = m
    params, fn = [], None
    for n, l in enumerate(lines):
        if "OpFunction " in l or l.strip().endswith("OpFunctionEnd"):
            params, fn = [], n
        elif "OpFunctionParameter" in l:
            lhs, rhs = l.split("=", 1)
            params.append((lhs.strip(), rhs.split()[1]))
            same = [p for p in params if p[1] == params[-1][1]]
            if len(same) >= 2 and "a_helpers_parameter" not in out:
                end = next(k for k in range(n, len(lines)) if lines[k].strip().endswith("OpFunctionEnd"))
                m = swap_in(lines, n + 1, end, same[0][0], same[1][0])
                if m:
                    out["a_helpers_parameter"] = m
    return out


def test_two_values_of_one_shape_are_two_labels(build, tmp_path):
    compile_into(build, tmp_path, ["vs_full"])
    clean = tmp_path / "vs_full.spv"
    lines = raw_text(clean)
    rt = reassemble(lines, tmp_path / "roundtrip.spv")
    assert spv_canon.compare(clean, rt, reference_side="host2")["ok"]
    mutants = label_mutants(lines)
    assert sorted(mutants) == ["a_helpers_parameter", "after_the_loop", "inside_the_loop"], sorted(mutants)
    for what, m in mutants.items():
        assert sum(1 for x, y in zip(lines, m) if x != y) == 1
        r = spv_canon.compare(clean, reassemble(m, tmp_path / f"{what}.spv"), reference_side="host2")
        assert not r["ok"], (what, "a value swapped for another of its shape was not seen")


FN_VARS = """               OpCapability Shader
               OpMemoryModel Logical GLSL450
               OpEntryPoint Fragment %main "main" %oC0
               OpExecutionMode %main OriginUpperLeft
               OpDecorate %oC0 Location 0
       %void = OpTypeVoid
         %fn = OpTypeFunction %void
      %float = OpTypeFloat 32
    %v4float = OpTypeVector %float 4
      %f_one = OpConstant %float 1
      %f_two = OpConstant %float 2
        %one = OpConstantComposite %v4float %f_one %f_one %f_one %f_one
        %two = OpConstantComposite %v4float %f_two %f_two %f_two %f_two
    %ptr_out = OpTypePointer Output %v4float
     %ptr_fn = OpTypePointer Function %v4float
        %oC0 = OpVariable %ptr_out Output
       %main = OpFunction %void None %fn
      %entry = OpLabel
          %a = OpVariable %ptr_fn Function
          %b = OpVariable %ptr_fn Function
               OpStore %a %one
               OpStore %b %two
         %la = OpLoad %v4float {first}
               OpStore %a %two
         %lb = OpLoad %v4float %a
          %d = OpFSub %v4float {minuend} {subtrahend}
               OpStore %oC0 %d
               OpReturn
               OpFunctionEnd
"""


def test_function_variables_and_loads_around_a_store_are_told_apart(tmp_path):
    def module(name, **kw):
        return assemble(FN_VARS.format(**{"first": "%a", "minuend": "%la", "subtrahend": "%lb", **kw}), tmp_path / f"{name}.spv")
    clean = module("clean")
    assert spv_canon.compare(clean, clean, reference_side="host2")["ok"]
    for what, kw in (("the other variable", {"first": "%b"}), ("the loads swapped", {"minuend": "%lb", "subtrahend": "%la"})):
        assert not spv_canon.compare(clean, module(what.replace(" ", "_"), **kw), reference_side="host2")["ok"], what


def test_a_capability_only_host_2s_state_needs_is_the_class_and_no_other(pairs, tmp_path):
    """heap_capabilities' host-2 list, entry by entry: a DXVK module without Int8 (no sub-dword push
    member) against host 2's state buffer, whose u8 members need it, is the class; Float16 is not."""
    dxvk = assemble(spv_pairs.fragment("dxvk").replace("               OpCapability Int8\n", "", 1), tmp_path / "fs.no-int8.spv")
    r = spv_canon.compare(dxvk, pairs["fs"]["host2"])
    assert r["ok"] and r["classes"]["host2"].get("heap_capabilities") == 1, r["failures"]
    host2 = assemble(spv_pairs.fragment("host2").replace("               OpCapability Int8\n",
                                                         "               OpCapability Int8\n               OpCapability Float16\n", 1),
                     tmp_path / "fs.float16.spv")
    assert any("capability Float16 only in host 2's module" in f for f in spv_canon.compare(pairs["fs"]["dxvk"], host2)["failures"])


def test_every_explanation_class_is_hit(pairs, build, tmp_path):
    """A class of spv_explained.json nothing exercises could land untested: the clean pairs (both
    sides) and the BGRA variant (host 2 against host 2) must hit every one."""
    hit = set()
    for kind in ("fs", "vs"):
        r = spv_canon.compare(pairs[kind]["dxvk"], pairs[kind]["host2"])
        assert r["ok"], r["failures"]
        hit |= set(r["classes"]["dxvk"]) | set(r["classes"]["host2"])
    compile_into(build, tmp_path / "base", ["vs_full"])
    compile_into(build, tmp_path / "bgra", ["vs_full"], "--bgra", "2")
    r = spv_canon.compare(tmp_path / "base/vs_full.spv", tmp_path / "bgra/vs_full.spv", 2, reference_side="host2")
    hit |= set(r["classes"]["host2"])
    ids = {c["id"] for c in json.loads(spv_canon.EXPLAINED.read_text())["classes"]}
    assert hit == ids == {"spec_data", "push_data", "sampler_descriptor", "set_binding", "dynamic_cf_length",
                          "state_layout", "heap_capabilities", "bgra_input_swizzle"}


# --- the GPU script's steps, each on synthetic inputs (test/r1_receipt.py) --------------------------
LAYER_ON = "Validation layers enabled, expect debug level performance!\n"
LAYER_OFF = "WARNING: Validation layers not found, continuing without validation\n"


def verror(vuid):
    return f"Validation Error: [ {vuid} ] | MessageID = 0x1\n"


def step(run, *argv):
    """(exit status, the check it wrote) of one r1_receipt.py step."""
    rc = r1_receipt.main([argv[0], "--run", str(run), *map(str, argv[1:])])
    written = sorted((run / "checks").glob("*.json"), key=lambda q: q.stat().st_mtime_ns)
    return rc, (json.loads(written[-1].read_text()) if written else None)


def test_c10_requires_registered_full_geometry_and_a_fresh_offline_check(tmp_path, monkeypatch):
    original = tmp_path / "original-run"
    original.mkdir()
    path = original / "compare.json"
    healthy = {
        "verdict": "PASS", "verdict_with_controls": "PASS", "marker": "R2_DRAW_OK",
        "acceptance_scope": "r2-draws-full", "stage": 3, "max_frames": 0,
        "stream": {"sha256": "37ff5dd7756d8411d7c12161397d2cca8d3a4bc9f58af78644abcc0ef5f4d555"},
        "controls": {"run": True, "all_as_required": True},
    }
    calls = []
    recheck_exit = 0

    def offline_check(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, recheck_exit, "", "")

    monkeypatch.setattr(r1_receipt.subprocess, "run", offline_check)
    path.write_text(json.dumps(healthy))
    rc, check = step(tmp_path / "healthy", "compare", "--compare", path)
    assert rc == 0 and check["pass"] is True
    cases = {
        "synthetic": {"marker": "R2_STREAM_GEOMETRY_OK", "stream": {"sha256": "a" * 64}},
        "partial": {"marker": "R2_PARTIAL_GEOMETRY_OK", "acceptance_scope": "r2-draws-partial", "max_frames": 1},
        "stale_controls": {"verdict_with_controls": "FAIL"},
        "bool_stage": {"stage": True},
        "bool_bound": {"max_frames": False},
        "string_controls": {"controls": {"run": "true", "all_as_required": "true"}},
    }
    for name, changes in cases.items():
        path.write_text(json.dumps({**healthy, **changes}))
        rc, check = step(tmp_path / name, "compare", "--compare", path)
        assert rc == 1 and check["pass"] is False, name
    path.write_text(json.dumps(healthy))
    recheck_exit = 2
    rc, check = step(tmp_path / "changed_original_artifacts", "compare", "--compare", path)
    assert rc == 1 and check["pass"] is False and check["recheck_exit"] == 2
    assert calls[0] == [sys.executable, str(ROOT / "tools/s5/compare_hosts.py"), "check", "--run", str(original)]
def spv_bytes(tag):
    return struct.pack("<5I", 0x07230203, 0x10600, 0, 1, 0) + tag.encode().ljust(8, b"\0")


def test_the_load_step_holds_the_layer_and_what_the_driver_received(tmp_path):
    import hashlib
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"shaders": [{}, {}], "variants": [], "pairs": [{}], "border_variants": [], "border_unmapped": [],
                                "specialized": [{}]}))
    counts = "LOAD shaders 2/2 variants 0/0 pairs 1/1 border 0/0 specialized 1/1\n"

    def case(name, log=None, ctrl=None, captured=("vs0", "ps0"), written=("vs0", "ps0"), capline="records=3 write_failures=0"):
        d = tmp_path / name
        (d / "load").mkdir(parents=True)
        (d / "cap").mkdir()
        for w in written:
            (d / "load" / f"{w}.spv").write_bytes(spv_bytes(w))
        for c in captured:
            (d / "cap" / f"module-{hashlib.sha256(spv_bytes(c)).hexdigest()}.spv").write_bytes(spv_bytes(c))
        (d / "cap" / "devices.jsonl").write_text("{}\n")
        (d / "load.txt").write_text(log if log is not None else
                                    LAYER_ON + "DEVICE x\n" + counts + "SHADERS_LOAD_OK\n" + f"vkcap: load=1.2.3 {capline}\n")
        (d / "ctrl.txt").write_text(ctrl if ctrl is not None else LAYER_ON + verror("VUID-a") + "SHADERS_LOAD_FAIL\n")
        return step(d, "load", "--driver", "x", "--log", d / "load.txt", "--control-log", d / "ctrl.txt", "--plan", plan,
                    "--out-dir", d / "load", "--capture", d / "cap")
    rc, c = case("clean")
    assert rc == 0 and c["pass"] and c["handed_to_the_driver"]["equal"], c
    # two loads of the layer in one process (the device facts' throwaway instance first), the second's
    # account written into the middle of a line of stdout: both read
    rc, c = case("an_account_inside_a_line", log=LAYER_ON + "vkcap: load=1 records=0 write_failures=0\nDEVICE x\n" + counts +
                 "PAIR pair6 vvkcap: load=2 records=3 write_failures=0\ns ps0\nSHADERS_LOAD_OK\n")
    assert rc == 0 and c["pass"] and c["handed_to_the_driver"]["capture"]["loads"] == 2, c
    bad = {
        "layer_not_found": {"log": LAYER_OFF + "DEVICE x\n" + counts + "SHADERS_LOAD_OK\nvkcap: load=1 records=3 write_failures=0\n"},
        "no_layer_line": {"log": "DEVICE x\n" + counts + "SHADERS_LOAD_OK\nvkcap: load=1 records=3 write_failures=0\n"},
        "a_validation_error": {"log": LAYER_ON + verror("VUID-b") + counts + "SHADERS_LOAD_OK\nvkcap: load=1 records=3 write_failures=0\n"},
        "control_quiet": {"ctrl": LAYER_ON + "SHADERS_LOAD_OK\n"},
        "control_without_layer": {"ctrl": LAYER_OFF + verror("VUID-a")},
        "a_module_rewritten": {"captured": ("vs0", "ps0-other")},
        "a_module_never_handed": {"captured": ("vs0",), "capline": "records=2 write_failures=0"},
        "capture_not_loaded": {"capline": None},
        "capture_write_failure": {"capline": "records=3 write_failures=1"},
        "records_unaccounted": {"capline": "records=4 write_failures=0"},
        "counts_short": {"log": LAYER_ON + "LOAD shaders 1/2 variants 0/0 pairs 1/1 border 0/0 specialized 1/1\nSHADERS_LOAD_OK\nvkcap: load=1 records=3 write_failures=0\n"},
        "specialized_short": {"log": LAYER_ON + "LOAD shaders 2/2 variants 0/0 pairs 1/1 border 0/0 specialized 0/1\nSHADERS_LOAD_OK\nvkcap: load=1 records=3 write_failures=0\n"},
    }
    for name, kw in bad.items():
        if kw.get("capline", "") is None:
            kw = {"log": LAYER_ON + "DEVICE x\n" + counts + "SHADERS_LOAD_OK\n"}
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def canon_texts(d, gpl_ok=True, lib=18, lnk_ok=True, modes=None):
    gpl = {"ok": gpl_ok, "shaders": 26, "dxvk_modules": 18, "dxvk_matched": 18, "classes": {}, "failures": {}}
    lnk = {"ok": lnk_ok, "linked_pipelines": 3, "linked_module_pairs": 1, "linked_keys": 1, "linked_bgra_masks": [0],
           "dxvk_internal_pipelines": 0, "stageless_pipelines": 0, "compared": 2, "drawn_pair_entries": 1,
           "drawn_shader_pairs": 1, "gpl": {"library_modules": lib, "equal_to_dump": lib}, "classes": {}, "failures": {},
           "sampler_modes": modes or {"specialized_ps": 2, "base_ps": 0, "frozen": 1, "dxvk_words": [0],
                                      "word_from": ["the spec constant's default"],
                                      "word_sources": ["default", "no SpecId 5"], "not_frozen": ["pair1.ps (fs.x)"]}}
    (d / "gpl.txt").write_text(json.dumps(gpl) + ("\nSPV_CANON_OK\n" if gpl_ok else "\nSPV_CANON_FAIL\n"))
    (d / "lnk.txt").write_text(json.dumps(lnk) + ("\nSPV_CANON_LINKED_OK\n" if lnk_ok else "\nSPV_CANON_FAIL\n"))


def test_the_canon_step_accounts_for_the_capture(tmp_path):
    def case(name, logs=("records=2 write_failures=0", "records=1 write_failures=0"), **kw):
        d = tmp_path / name
        (d / "cap").mkdir(parents=True)
        (d / "cap" / "module-aa.spv").write_bytes(b"x")
        (d / "cap" / "pipelines.jsonl").write_text("{}\n{}\n")
        canon_texts(d, **kw)
        paths = []
        for n, l in enumerate(logs):
            (d / f"p{n}.txt").write_text(f"frames out\nvkcap: load={n} {l}\n" if l else "frames out\n")
            paths.append(d / f"p{n}.txt")
        return step(d, "canon", "--driver", "x", "--gpl", d / "gpl.txt", "--linked", d / "lnk.txt", "--captures", *paths,
                    "--capture-dir", d / "cap")
    rc, c = case("clean")
    assert rc == 0 and c["pass"], c
    for name, kw in {"a_run_without_the_layer": {"logs": ("records=3 write_failures=0", None)},
                     "records_unaccounted": {"logs": ("records=2 write_failures=0", "records=2 write_failures=0")},
                     "a_write_failure": {"logs": ("records=2 write_failures=0", "records=1 write_failures=1")},
                     "library_short": {"lib": 13}, "gpl_failed": {"gpl_ok": False}, "linked_failed": {"lnk_ok": False},
                     # the linked half compares host 2's specialized pixel shader, never its base, at DXVK's word 0
                     "a_pixel_shader_compared_as_the_base": {"modes": {"specialized_ps": 1, "base_ps": 1, "frozen": 1,
                                                                       "dxvk_words": [0]}},
                     "no_pixel_shader_specialized": {"modes": {"specialized_ps": 0, "base_ps": 0, "frozen": 0,
                                                               "dxvk_words": []}},
                     "a_word_dxvk_passed": {"modes": {"specialized_ps": 1, "base_ps": 0, "frozen": 1, "dxvk_words": [2],
                                                      "word_sources": ["entry"]}},
                     # DXVK's pipelines must omit SpecId 5 (the word its default 0): a word from a map entry is
                     # not that, even at 0, and neither is a batch that froze nothing
                     "a_word_from_a_map_entry": {"modes": {"specialized_ps": 1, "base_ps": 0, "frozen": 1, "dxvk_words": [0],
                                                           "word_sources": ["default", "entry"]}},
                     "nothing_frozen": {"modes": {"specialized_ps": 1, "base_ps": 0, "frozen": 0, "dxvk_words": [0],
                                                  "word_sources": ["no SpecId 5"]}},
                     "no_word_source_recorded": {"modes": {"specialized_ps": 1, "base_ps": 0, "frozen": 1,
                                                           "dxvk_words": [0]}},
                     }.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def test_the_controls_step_fails_a_fault_that_stopped_working(tmp_path):
    names = ["umin_dropped", "lower_dot_off", "linkage_skipped", "modes_skipped", "modes_slot_shifted"]

    def case(name, umin_changes=True, extra_failing=False, pair_compatible=True, linkage_changes=False, r0=26,
             modes_caught=True, shift_changes=False):
        d = tmp_path / name
        clean, faults = d / "clean", d / "faults"
        (clean / "pairs").mkdir(parents=True)
        for o in ("vs0", "ps0"):
            (clean / f"{o}.spv").write_bytes(spv_bytes(o))
        (clean / "pairs" / "pair0.ps.spv").write_bytes(spv_bytes("p0"))
        (clean / "pairs" / "pair0.json").write_text(json.dumps({"compatible": pair_compatible, "io_rewritten": False}))
        (faults / "umin_dropped").mkdir(parents=True)
        (faults / "umin_dropped" / "vs0.spv").write_bytes(spv_bytes("vs0-faulted" if umin_changes else "vs0"))
        (faults / "umin_dropped" / "ps0.spv").write_bytes(spv_bytes("ps0"))
        fails = {"vs0": ["function entry: the float instruction sequences differ"]} if umin_changes else {}
        if extra_failing:
            fails["ps0"] = ["function entry: an extra failure"]
        (faults / "umin_dropped.canon.txt").write_text(json.dumps({"failures": fails}) +
                                                       ("\nSPV_CANON_FAIL\n" if fails else "\nSPV_CANON_OK\n"))
        (faults / "lower_dot_off.compile.txt").write_text("FAIL vs0: Invalid opcode FDot\n")
        (faults / "linkage_skipped" / "pairs").mkdir(parents=True)
        (faults / "linkage_skipped" / "pairs" / "pair0.ps.spv").write_bytes(spv_bytes("p0-unlinked" if linkage_changes else "p0"))
        lt = d / "linkage.txt"
        lt.write_text(json.dumps({"failures": {"pair0.ps": ["x"]}}) + "\nSPV_CANON_FAIL\n" if linkage_changes
                      else json.dumps({"failures": {}}) + "\nSPV_CANON_LINKED_OK\n")
        (d / "r0c.txt").write_text(json.dumps({"failures": {f"o{i}": ["x"] for i in range(r0)}}) + "\nSPV_CANON_FAIL\n")
        # the sampler-mode faults through the load's pairs: the word ignored changes pair0's pixel shader and
        # must fail the linked batch on it; the register shift changes nothing at word 0
        for f, changes, caught in (("modes_skipped", True, modes_caught), ("modes_slot_shifted", shift_changes, False)):
            (faults / f / "pairs").mkdir(parents=True)
            (faults / f / "pairs" / "pair0.ps.spv").write_bytes(spv_bytes("p0-" + f if changes else "p0"))
            (faults / f"{f}.linked.txt").write_text(
                json.dumps({"failures": {"pair0.ps": ["x"]}, "compared_keys": ["pair0.ps", "pair0.vs"]}) + "\nSPV_CANON_FAIL\n"
                if caught else json.dumps({"failures": {}, "compared_keys": ["pair0.ps", "pair0.vs"]}) + "\nSPV_CANON_LINKED_OK\n")
        (d / "gpl").mkdir()
        return step(d, "controls", "--driver", "x", "--clean", clean, "--faults", faults, "--names", *names, "--r0c", d / "r0c.txt",
                    "--linkage-load", faults / "linkage_skipped", "--linkage-canon", lt, "--gpl", d / "gpl")
    rc, c = case("clean")
    assert rc == 0 and c["pass"] and list(c["unexercised_by_corpus"]) == ["linkage_skipped", "modes_slot_shifted"], c
    assert c["faults"]["modes_skipped"]["failing"] == ["pair0.ps"], c
    rc, c = case("linkage_exercised_and_caught", linkage_changes=True)
    assert rc == 0 and c["pass"] and list(c["unexercised_by_corpus"]) == ["modes_slot_shifted"], c
    for name, kw in {"a_fault_that_changes_nothing": {"umin_changes": False},
                     "a_failure_on_an_unchanged_object": {"extra_failing": True},
                     "linkage_unexercised_but_a_pair_needs_it": {"pair_compatible": False},
                     "r0_not_failing_everywhere": {"r0": 25},
                     "the_word_ignored_and_not_caught": {"modes_caught": False},
                     "the_shift_changes_a_pair_not_caught": {"shift_changes": True}}.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


SPEC0 = "bgra=0;border_u=0;border_v=0;border_linear=0;depth_clamp=0;sampler_modes=0"
BASE = "bgra=0;border_u=0;border_v=0;border_linear=0;depth_clamp=0;sampler_modes=none"


def test_the_null_stream_step(tmp_path):
    def row(bgra, variant=SPEC0):
        return json.dumps({"bgra": bgra, "uniform": True, "pixel_shader_variant": variant}) + "\n"

    def case(name, dx=(0, 0, 0, 0), sd=(0, 0, 0, 0), ct=(255, 255, 255, 255), sdl_extra="", ctrl_layer=LAYER_ON,
             bs=(0, 0, 0, 0), sd_variant=SPEC0, bs_variant=BASE):
        d = tmp_path / name
        d.mkdir()
        (d / "dx.txt").write_text(json.dumps({"bgra": list(dx)}) + "\nNULL_PROBE_OK\n")
        (d / "sd.txt").write_text(LAYER_ON + sdl_extra + row(list(sd), sd_variant) + "NULL_PROBE_OK\n")
        (d / "ct.txt").write_text(ctrl_layer + row(list(ct)) + "NULL_PROBE_OK\n")
        (d / "bs.txt").write_text(row(list(bs), bs_variant) + "NULL_PROBE_OK\n")
        return step(d, "nullprobe", "--driver", "x", "--dxvk", d / "dx.txt", "--sdl", d / "sd.txt", "--control", d / "ct.txt",
                    "--sdl-base", d / "bs.txt")
    rc, c = case("clean")
    assert rc == 0 and c["pass"] and c["measured_rgba"] == [0, 0, 0, 0], c
    clear = tuple(r1_receipt.PROBE_CLEAR_BGRA)
    for name, kw in {"hosts_differ": {"sd": (1, 0, 0, 0)}, "control_equal": {"ct": (0, 0, 0, 0)},
                     "a_validation_error": {"sdl_extra": verror("VUID-c")}, "layer_not_loaded": {"ctrl_layer": LAYER_OFF},
                     "the_dxvk_leg_drew_nothing": {"dx": clear, "sd": clear, "bs": clear},
                     # the specialized pixel shader's pixels against the base's
                     "the_base_draws_otherwise": {"bs": (0, 0, 1, 0)},
                     "the_sdl_leg_not_specialized": {"sd_variant": BASE},
                     "the_base_leg_specialized": {"bs_variant": SPEC0}}.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def border_case(filt, em, em_max, ctrl, ctrl_max, colour="c"):
    return {"filter": filt, "colour": colour, "emulation_differing_channels": em, "emulation_max_delta": em_max,
            "control_differing_channels": ctrl, "control_max_delta": ctrl_max, "pass": True,
            "patch_sha256": f"p{filt}{colour}", "emulation_sha256": f"e{filt}{colour}", "control_sha256": f"c{filt}{colour}"}


def test_the_border_step_bounds_the_linear_residue(tmp_path):
    def case(name, point=(0, 0), linear=(500, 2), log=LAYER_ON + "BORDER_FALLBACK_OK\n", base_leg_differs=None,
             variant=SPEC0, base_variant=BASE):
        d = tmp_path / name
        d.mkdir()
        cases = [border_case("point", point[0], point[1], 9000, 200), border_case("point", 0, 0, 9000, 200, "d"),
                 border_case("linear", linear[0], linear[1], 9000, 200), border_case("linear", 10, 1, 9000, 200, "d")]
        base = [dict(c) for c in cases]
        if base_leg_differs:
            base[0][base_leg_differs] = "other"
        (d / "border.json").write_text(json.dumps({"cases": cases, "pass": True, "pixel_shader_variant": variant}))
        (d / "base.json").write_text(json.dumps({"cases": base, "pass": True, "pixel_shader_variant": base_variant}))
        (d / "log.txt").write_text(log)
        return step(d, "border", "--driver", "x", "--log", d / "log.txt", "--json", d / "border.json",
                    "--base-json", d / "base.json")
    rc, c = case("clean")
    assert rc == 0 and c["pass"] and c["residue"]["below_the_control"], c
    for name, kw in {"point_differs": {"point": (3, 1)}, "linear_as_wide_as_the_control": {"linear": (9000, 2)},
                     "linear_as_deep_as_the_control": {"linear": (500, 200)},
                     "no_layer": {"log": "BORDER_FALLBACK_OK\n"},
                     # every leg's frame drawn with the base pixel shader is the specialized one's
                     "the_patch_leg_differs_from_the_base": {"base_leg_differs": "patch_sha256"},
                     "the_emulation_leg_differs_from_the_base": {"base_leg_differs": "emulation_sha256"},
                     "the_control_leg_differs_from_the_base": {"base_leg_differs": "control_sha256"},
                     "not_specialized": {"variant": BASE}, "the_base_specialized": {"base_variant": SPEC0}}.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def times_file(path, hashes):
    rows = ["frame\tseq\tvirtual_us\tguest_frames\tpolls\tbgrx_hash"] + [f"{i}\t{i}\t{i}\t1\t0\t{h}" for i, h in enumerate(hashes)]
    path.write_text("\n".join(rows) + "\n")
    return path


def test_the_frames_step_needs_the_layer_loaded_and_a_full_control(tmp_path):
    frames_ = ["0xa", "0xb", "0xc"]
    loaded = "vkcap: load=1.2.3 records=79 write_failures=0\n"

    def case(name, control=("0x0", "0x0", "0x0"), layer_log=loaded, control_log=loaded, none_log="", exits=(0, 0, 0),
             reference=None):
        d = tmp_path / name
        d.mkdir()
        args = ["--reference", times_file(d / "reference.tsv", reference)] if reference else []
        for leg, hashes, log, ex in (("none", frames_, none_log, exits[0]), ("layer", frames_, layer_log, exits[1]),
                                     ("control", control, control_log, exits[2])):
            args += [f"--{leg}", times_file(d / f"{leg}.tsv", hashes), f"--{leg}-log", d / f"{leg}.txt", f"--{leg}-exit", d / f"{leg}.exit"]
            (d / f"{leg}.txt").write_text("frames out\n" + log)
            (d / f"{leg}.exit").write_text(f"{ex}\n")
        return step(d, "frames", "--driver", "x", *args)
    rc, c = case("clean")
    assert rc == 0 and c["pass"] and c["control_frames_changed"] == 3, c
    rc, c = case("clean_with_a_reference", reference=frames_)
    assert rc == 0 and c["pass"] and c["layer_equals_reference"], c
    for name, kw in {"the_layer_not_loaded": {"layer_log": ""}, "a_reference_that_differs": {"reference": ["0xa", "0xb", "0xd"]}, "the_control_not_loaded": {"control_log": ""},
                     "a_write_failure": {"layer_log": "vkcap: load=1 records=79 write_failures=2\n"},
                     "the_layer_recorded_nothing": {"layer_log": "vkcap: load=1 records=0 write_failures=0\n"},
                     "the_layer_in_the_run_without_it": {"none_log": loaded},
                     "a_control_of_one_frame": {"control": ("0x0",)}, "a_control_of_no_frame": {"control": ()},
                     "a_control_equal": {"control": tuple(frames_)}, "the_control_failed": {"exits": (0, 0, 1)},
                     "the_layer_leg_failed": {"exits": (0, 139, 0)}}.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def test_the_cache_step_needs_a_miss_for_a_variant_after_its_base(tmp_path):
    def case(name, first=None, outside_left=False, variant="CACHE hits=0 misses=1 verified=0 corrupt=0", variant_same=True,
             modes="CACHE hits=0 misses=1 verified=0 corrupt=0", modes_same=True, modes_is_base=False):
        d = tmp_path / name
        clean, out = d / "clean", d / "out"
        clean.mkdir(parents=True)
        out.mkdir()
        for i in range(26):
            (clean / f"o{i}.spv").write_bytes(spv_bytes(f"o{i}"))
            (out / f"o{i}.spv").write_bytes(spv_bytes(f"o{i}"))
            (clean / f"o{i}.json").write_text(json.dumps({"bytecode_sha256": f"s{i % 18}"}))
        stats = [first or "CACHE hits=8 misses=18 verified=0 corrupt=0", "CACHE hits=26 misses=0 verified=0 corrupt=0",
                 "CACHE hits=25 misses=1 verified=0 corrupt=1", "CACHE hits=26 misses=0 verified=26 corrupt=0"]
        logs = []
        for n, s in enumerate(stats):
            (d / f"c{n}.txt").write_text(s + "\nSHADERC_OK\n")
            logs.append(d / f"c{n}.txt")
        (d / "c4.txt").write_text("REFUSED the cache root x is not inside y\n")
        outside = d / "outside"
        if outside_left:
            outside.mkdir()
        (d / "v.txt").write_text(variant + "\n")
        (d / "v.spv").write_bytes(spv_bytes("v"))
        (d / "v-clean.spv").write_bytes(spv_bytes("v" if variant_same else "base"))
        (d / "m.txt").write_text(modes + "\n")
        (d / "m.spv").write_bytes(spv_bytes("o0" if modes_is_base else "m"))
        (d / "m-clean.spv").write_bytes(spv_bytes(("o0" if modes_is_base else "m") if modes_same else "x"))
        return step(d, "cache", "--logs", *logs, d / "c4.txt", "--clean", clean, "--out-dir", out, "--outside-root", outside,
                    "--variant-log", d / "v.txt", "--variant-module", d / "v.spv", "--variant-clean", d / "v-clean.spv",
                    "--modes-log", d / "m.txt", "--modes-module", d / "m.spv", "--modes-clean", d / "m-clean.spv",
                    "--modes-base", out / "o0.spv")
    rc, c = case("clean")
    assert rc == 0 and c["pass"], c
    for name, kw in {"a_warm_first_pass": {"first": "CACHE hits=26 misses=0 verified=0 corrupt=0"},
                     "the_outside_root_made": {"outside_left": True},
                     "the_variant_served_the_base": {"variant": "CACHE hits=1 misses=0 verified=0 corrupt=0"},
                     "the_variant_module_is_not_the_variants": {"variant_same": False},
                     # the sampler-mode word is the cache key's: a hit would serve the base
                     "the_word_served_the_base": {"modes": "CACHE hits=1 misses=0 verified=0 corrupt=0"},
                     "the_specialized_module_is_not_a_clean_compiles": {"modes_same": False},
                     "the_specialized_module_is_the_base": {"modes_is_base": True}}.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def test_the_plan_s_bgra_mask_is_dxvk_s_attribute_map():
    """r1_receipt.bgra_mask, the BGRA variant a tuple needs, by DXVK's BindInputLayout: the colour at its
    signature location, a later element of one semantic replacing an earlier, POSITIONT read as
    POSITION and a usage index packed into four bits (the device's map_pipeline, U9)."""
    sig = [[10, 0], [0, 0], [3, 0]]
    decl = [[0, 0, 2, 0, 0, 0], [0, 12, 4, 0, 10, 0], [0, 16, 1, 0, 5, 0]]
    assert r1_receipt.bgra_mask(sig, decl) == 0b1
    assert r1_receipt.bgra_mask(sig, decl + [[0, 20, 3, 0, 10, 0]]) == 0
    assert r1_receipt.bgra_mask([[0, 0], [10, 0]], [[0, 0, 2, 0, 9, 0], [0, 16, 4, 0, 10, 16]]) == 0b10


def test_the_plan_holds_the_reflections_to_the_bytecode(tmp_path):
    import hashlib
    vs = sm3_asm.assemble(VS_FULL)
    ps = sm3_asm.assemble(PS_FULL)
    dump, refl = tmp_path / "dump", tmp_path / "refl"
    dump.mkdir()
    refl.mkdir()
    sha = {k: hashlib.sha256(b).hexdigest() for k, b in (("vs", vs), ("ps", ps))}
    (dump / f"{sha['vs']}.bin").write_bytes(vs)
    (dump / f"{sha['ps']}.bin").write_bytes(ps)
    keys = {"schema": "sa2-d3d9gpu-keys/v3", "complete": True, "shaders": {"vs": [sha["vs"]], "ps": [sha["ps"]]},
            "samplers": [], "inputs": [{"vs": 1, "ps": 1, "elems": [[0, 0, 2, 0, 0, 0], [0, 12, 4, 0, 10, 0]],
                                        "rt": [[21, 0, 0, 0, 0, 0, 1]], "ds": [75, 0, 0, 0, 0, 0, 0], "prim": 4}]}
    kpaths = []
    for s in r1_receipt.STREAMS:
        (tmp_path / f"{s}.keys.json").write_text(json.dumps(keys))
        kpaths.append(tmp_path / f"{s}.keys.json")

    def run(vs_sig, ps_masks):
        (refl / "vs0.json").write_text(json.dumps({"bytecode_sha256": sha["vs"], "input_signature": vs_sig}))
        (refl / "ps0.json").write_text(json.dumps({"bytecode_sha256": sha["ps"], "sampler_mask": ps_masks[0], "rt_mask": ps_masks[1]}))
        return r1_receipt.main(["plan", "--keys", *map(str, kpaths), "--bytecode", str(dump), "--reflections", str(refl),
                                "--out", str(tmp_path / "plan.json")])
    assert run([[0, 0], [10, 0], [5, 0], [3, 0]], (0b10001001, 1)) == 0
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["variants"] == [{"shader": "vs0", "bgra": 2}]   # the COLOR at signature location 1 is a D3DCOLOR
    assert run([], (0b10001001, 1)) == 2                         # a signature lost
    assert run([[0, 0], [5, 0], [10, 0], [3, 0]], (0b10001001, 1)) == 2   # entries out of dcl order
    assert run([[0, 0], [10, 0], [5, 0], [3, 0]], (0b1001, 1)) == 2      # a sampler lost


def test_the_border_fallback_cannot_take_fetch4():
    fetch4 = "fetch4 (D3DSAMP_MIPMAPLODBIAS GET4, or a single-channel point-filtered texture whose stage may hold it)"
    smp = {"ss": [0, 4, 4, 3, 0, 1, 1, 2, r1_receipt.FOURCC_GET4, 0, 1, 0, 0, 0], "stages": 1, "cube": False, "depth": False}
    assert r1_receipt.border_variant(smp, 1) == fetch4
    plain = {**smp, "ss": smp["ss"][:8] + [0] + smp["ss"][9:]}
    assert r1_receipt.border_variant(plain, 1) == (1, 1, 0)
    # DXVK's switch is sticky: an L8 texture, point-filtered, whose stage now holds a bias of 0 may still
    # have GET4 on from an earlier write; GET1 turns it off
    assert r1_receipt.border_variant({**plain, "tex": True, "fmt": 50}, 1) == fetch4
    get1 = {**plain, "tex": True, "fmt": 50, "ss": plain["ss"][:8] + [r1_receipt.FOURCC_GET1] + plain["ss"][9:]}
    assert r1_receipt.border_variant(get1, 1) == (1, 1, 0)


def test_every_validation_error_fails_the_run_whichever_check_saw_it(tmp_path):
    # 06479 was C8's error at 06d9a0a: "fail on any validation error", the agent's amendment of the user's
    # validation-layer ruling, holds it a failure; no table exempts it (the sampler-mode specialization
    # removed the dead depth-compare sample that raised it)
    def run_with(mutate):
        d = tmp_path / f"run{len(list(tmp_path.iterdir()))}"
        (d / "checks").mkdir(parents=True)
        checks = {c: {"check": c, "pass": True} for c in r1_receipt.EXPECTED}
        mutate(checks)
        for c, v in checks.items():
            (d / "checks" / f"{c}.json").write_text(json.dumps(v))
        return r1_receipt.decide(d)[:2]
    vuid = "VUID-vkCmdDraw-None-06479"
    assert run_with(lambda c: c["C8-lvp"].update(validation_errors={vuid: 12})) == \
        (f"R1_SHADERS_FAIL VALIDATION C8-lvp:{vuid}", 1)
    assert run_with(lambda c: (c["C8-nvidia"].update(validation_errors={vuid: 12}),
                               c["C8-lvp"].update(validation_errors={vuid: 12}))) == \
        (f"R1_SHADERS_FAIL VALIDATION C8-lvp:{vuid} VALIDATION C8-nvidia:{vuid}", 1)
    assert run_with(lambda c: c["C2-nvidia"].update(validation_errors={vuid: 1})) == \
        (f"R1_SHADERS_FAIL VALIDATION C2-nvidia:{vuid}", 1)
    assert run_with(lambda c: c["C8-nvidia"].update(validation_errors={"VUID-other": 1, vuid: 2})) == \
        (f"R1_SHADERS_FAIL VALIDATION C8-nvidia:VUID-other VALIDATION C8-nvidia:{vuid}", 1)
    assert run_with(lambda c: c["C7-lvp"].update(validation_errors={"VUID-other": 1})) == \
        ("R1_SHADERS_FAIL VALIDATION C7-lvp:VUID-other", 1)
    assert run_with(lambda c: c["C8-lvp"].update(validation_errors={})) == ("R1_SHADERS_OK", 0)


def c4_census(tmp_path: Path, name: str, **change) -> tuple[Path, Path]:
    """A state_census.sh run directory as test/r1_receipt.py c4 reads it: four streams' keys files (one
    vertex shader whose signature reorders the declaration, one that reads it in order), the shader
    record, and the state check's result and output; `change` edits the result."""
    import hashlib
    d = tmp_path / name
    decl = [[0, 0, 2, 0, 0, 0], [0, 12, 4, 0, 10, 0]]
    sig = {"a" * 64: [[0, 0], [10, 0]], "b" * 64: [[10, 0], [0, 0], [3, 0]]}
    identities = {}
    for s in r1_receipt.STREAMS:
        (d / s).mkdir(parents=True)
        from test_s5_census_draws import write_draw_case
        original, _, _ = write_draw_case(d / s)
        identities[s] = {"path": str(original), "sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                         "presents": 1, "read_to_end": True}
        keys = {"shaders": {"vs": list(sig), "ps": ["c" * 64]}, "samplers": [{}],
                "inputs": [{"vs": 1, "ps": 1, "elems": decl}, {"vs": 2, "ps": 1, "elems": decl}]}
        (d / s / "d3d9gpu.keys.json").write_text(json.dumps(keys))
    (d / "shader-record.json").write_text(json.dumps({"vs": [{"sha256": h, "input_signature": v} for h, v in sig.items()]}))
    # state_check.cpp controls(), kept literal so an omitted control in the receipt reader fails.
    names = ("map-blend-swap map-zfunc-swap map-normalize-off map-writemask map-address-swap map-border-clamp "
             "map-border-colour map-depth-clamp-off map-depth-bias-push map-depth-clamp-extra map-border-swizzled-view "
             "map-border-fallback map-lod-unquantised map-bgra-mask map-fan-order map-depth-clip-off map-alpha-op "
             "map-ds-bind map-cull-swap map-vi-offset map-bgra-extra map-fan-on-list map-vi-declorder map-vi-no-null "
             "map-null-vertex-rate map-vi-unread-attr map-bgra-decl-index map-ps-sampler-superset").split()
    ctl = {n: {"name": n, "outcome": "fails as required (census)", "fields": "x", "census_tuples_failed": 4}
           for n in names}
    ctl["map-zfunc-swap"] = {"name": "map-zfunc-swap", "outcome": "not exercised by census; fails as required (domain sweep)",
                             "fields": "ds_depthCompareOp", "census_tuples_failed": 0}
    # The per-stream fields emitted by state_check.cpp run_census().
    streams = {s: {"pipeline_tuples": 2, "sampler_tuples": 1, "shaders_held_to_the_record": 3,
                   "tuples_with_recorded_reflection": 2, "tuples_with_recorded_vs_signature": 2,
                   "tuples_without_recorded_reflection": 0, "tuples_without_recorded_vs_signature": 0,
                   "reflection_differs": 0, "undecided": 0, "rt_feedback": 0,
                   "unmapped": 0, "sampler_unmapped": 0, "differ": 0, "sampler_differ": 0}
               for s in r1_receipt.STREAMS}
    result = {"census": {"stream_names": list(r1_receipt.STREAMS), "streams": streams, "pipeline_tuples": 8, "sampler_tuples": 4,
                         "null_stream_tuples": 4, "rt_feedback_tuples": 0, "undecided_tuples": 0,
                         "tuples_with_recorded_reflection": 8, "tuples_with_recorded_vs_signature": 8,
                         "reflection_differs_tuples": 0, "shaders_held_to_the_record": 12},
              "controls": list(ctl.values())}
    identity_path = (d / "streams.json").absolute()
    identity_path.write_text(json.dumps(identities))
    result["stream_identity"] = {"path": str(identity_path), "sha256": hashlib.sha256(identity_path.read_bytes()).hexdigest(),
                                 "streams": identities}
    for k, v in change.items():
        if k == "controls":
            v(ctl)
            result["controls"] = list(ctl.values())
        else:
            result["census"][k] = v
    (d / "state-check.json").write_text(json.dumps(result))
    (d / "state-check.txt").write_text("census: 4 streams, 8 pipeline tuples\nSTATE_CHECK_OK\n")
    log = tmp_path / f"{name}.txt"
    log.write_text("== 4 the state check\nSTATE_CENSUS_OK\n")
    return d, log


def test_the_c4_step_holds_the_census_to_u9(tmp_path, capsys):
    """test/r1_receipt.py c4 (test_shaders.sh C4): the census's marker, nothing unmapped, differing or
    pending, no VS_SIGNATURE named, the device's reflection the record's, every control failing as
    required with U9's and R2b's vertex-input ones and map-ps-sampler-superset by the census itself,
    and map-vi-declorder failing exactly the tuples whose recorded signature is not the declaration
    (here the second vertex shader's, one per stream: 4)."""
    def c4(name, check="C4", rc=0, **change):
        d, log = c4_census(tmp_path, name, **change)
        run = tmp_path / f"run-{name}"
        r1_receipt.main(["c4", "--run", str(run), "--check", check, "--census", str(d), "--log", str(log), "--exit", str(rc)])
        return json.loads((run / "checks" / f"{check}.json").read_text())
    good = c4("good")
    assert good["pass"], good["summary"]
    assert good["declorder"] == {"tuples_whose_signature_is_not_the_declaration": 4, "failed_by_map_vi_declorder": 4}
    assert "map-vi-declorder fails 4 tuples, the 4 of 8 whose signature is not the declaration" in good["summary"]
    for name, kw in {"an_exit_other_than_the_marker_s": {"rc": 3},
                     "the_stock_leg_s_marker_on_the_patched_leg": {"check": "C4-caps-off"},
                     "a_tuple_unmapped": {"streams": None},
                     "rt_feedback": {"rt_feedback_tuples": 1},
                     "a_reflection_other_than_the_record_s": {"reflection_differs_tuples": 1},
                     "a_tuple_without_the_record": {"tuples_with_recorded_vs_signature": 7},
                     "no_shader_held_to_the_record": {"shaders_held_to_the_record": 0},
                     "a_vertex_input_control_only_the_sweep_fails": {"controls": lambda c: c["map-vi-no-null"].update(
                         outcome="not exercised by census; fails as required (domain sweep)")},
                     "the_superset_control_only_the_sweep_fails": {"controls": lambda c: c["map-ps-sampler-superset"].update(
                         outcome="not exercised by census; fails as required (domain sweep)")},
                     "a_control_that_passes": {"controls": lambda c: c["map-zfunc-swap"].update(outcome="PASSES (the control is broken)")},
                     "declorder_on_other_tuples": {"controls": lambda c: c["map-vi-declorder"].update(census_tuples_failed=5)},
                     "a_control_missing": {"controls": lambda c: c.pop("map-null-vertex-rate")}}.items():
        if name == "a_tuple_unmapped":
            d, log = c4_census(tmp_path, name)
            res = json.loads((d / "state-check.json").read_text())
            res["census"]["streams"]["mh"]["unmapped"] = 1
            (d / "state-check.json").write_text(json.dumps(res))
            run = tmp_path / f"run-{name}"
            r1_receipt.main(["c4", "--run", str(run), "--check", "C4", "--census", str(d), "--log", str(log), "--exit", "0"])
            got = json.loads((run / "checks" / "C4.json").read_text())
        else:
            got = c4(name, **kw)
        assert not got["pass"], name
    # VS_SIGNATURE named anywhere in the check's output fails it
    d, log = c4_census(tmp_path, "named")
    (d / "state-check.txt").write_text("census: 8 pipeline tuples (8 undecided: 8 VS_SIGNATURE)\nSTATE_CHECK_OK\n")
    run = tmp_path / "run-named"
    r1_receipt.main(["c4", "--run", str(run), "--check", "C4", "--census", str(d), "--log", str(log), "--exit", "0"])
    assert not json.loads((run / "checks" / "C4.json").read_text())["pass"]
    capsys.readouterr()


@pytest.mark.parametrize("case", [
    "no_stream_rows", "missing_stream_row", "extra_stream_row", "missing_zero_field",
    "wrong_input_count", "wrong_sampler_count", "wrong_created_shader_count", "aggregate_sampler_count",
    "aggregate_shader_count", "stream_lacks_reflection", "stream_reflection_differs",
    "zero_failed_census_tuples", "missing_nonvertex_control", "duplicate_control", "unknown_control",
])
def test_the_c4_step_reconciles_every_stream_and_control(tmp_path, capsys, case):
    d, log = c4_census(tmp_path, case)
    path = d / "state-check.json"
    result = json.loads(path.read_text())
    row = result["census"]["streams"]["mh"]
    control = next(v for v in result["controls"] if v["name"] == "map-vi-no-null")
    changes = {
        "no_stream_rows": lambda: result["census"].update(streams={}),
        "missing_stream_row": lambda: result["census"]["streams"].pop("mh"),
        "extra_stream_row": lambda: result["census"]["streams"].update(extra=dict(row)),
        "missing_zero_field": lambda: row.pop("differ"),
        "wrong_input_count": lambda: row.update(pipeline_tuples=1),
        "wrong_sampler_count": lambda: row.update(sampler_tuples=0),
        "wrong_created_shader_count": lambda: row.update(shaders_held_to_the_record=1),
        "aggregate_sampler_count": lambda: result["census"].update(sampler_tuples=0),
        "aggregate_shader_count": lambda: result["census"].update(shaders_held_to_the_record=1),
        "stream_lacks_reflection": lambda: row.update(tuples_with_recorded_vs_signature=1),
        "stream_reflection_differs": lambda: row.update(reflection_differs=1),
        "zero_failed_census_tuples": lambda: control.update(census_tuples_failed=0),
        "missing_nonvertex_control": lambda: result["controls"].remove(next(v for v in result["controls"]
                                                                        if v["name"] == "map-zfunc-swap")),
        "duplicate_control": lambda: result["controls"].append(dict(control)),
        "unknown_control": lambda: result["controls"].append(dict(control, name="map-unknown")),
    }
    changes[case]()
    path.write_text(json.dumps(result))
    run = tmp_path / f"run-{case}"
    r1_receipt.main(["c4", "--run", str(run), "--check", "C4", "--census", str(d), "--log", str(log), "--exit", "0"])
    assert not json.loads((run / "checks/C4.json").read_text())["pass"], case
    capsys.readouterr()


def test_the_c4_step_accepts_the_forced_off_leg_s_domain_controls(tmp_path, capsys):
    d, log = c4_census(tmp_path, "caps-off")
    path = d / "state-check.json"
    result = json.loads(path.read_text())
    # state_check.cpp runs these controls again with the corresponding capability on when
    # the forced-off census cannot exercise them. Their census counts remain zero.
    for name, capability in (("map-border-colour", "CLAMP_TO_BORDER"), ("map-depth-clamp-off", "depth clip and clamp")):
        control = next(v for v in result["controls"] if v["name"] == name)
        control.update(outcome=f"not exercised by census; fails as required (domain sweep, with {capability} on)",
                       census_tuples_failed=0)
    path.write_text(json.dumps(result))
    log.write_text("STATE_CENSUS_FALLBACK_OWED BORDER DEPTH_CLAMP TEST_LEG=border,depth-clamp\n")
    run = tmp_path / "run-caps-off"
    r1_receipt.main(["c4", "--run", str(run), "--check", "C4-caps-off", "--census", str(d), "--log", str(log), "--exit", "4"])
    result = json.loads((run / "checks/C4-caps-off.json").read_text())
    assert result["pass"] and result["draw_scope"] == "capability-off-state-only"
    assert result["original_draw_audits"] == {}
    result = json.loads(path.read_text()); result.pop("stream_identity"); path.write_text(json.dumps(result))
    r1_receipt.main(["c4", "--run", str(tmp_path / "caps-without-geometry-identity"), "--check", "C4-caps-off",
                     "--census", str(d), "--log", str(log), "--exit", "4"])
    assert json.loads((tmp_path / "caps-without-geometry-identity/checks/C4-caps-off.json").read_text())["pass"]
    capsys.readouterr()


@pytest.mark.parametrize("bad", ["missing-stream", "missing-player", "missing-device", "wrong-total", "wrong-hash", "partial", "root"])
def test_the_c4_step_recomputes_original_draws_and_refuses_saved_verdicts(tmp_path, capsys, bad):
    d, log = c4_census(tmp_path, bad)
    stream, player, device = d / "mh/original.sa2s", d / "mh/report.json", d / "mh/d3d9gpu.json"
    if bad == "missing-stream":
        p = json.loads(player.read_text()); p["stream"] = str(d / "absent"); player.write_text(json.dumps(p))
    elif bad == "missing-player":
        player.rename(d / "mh/retained-player.json")
    elif bad == "missing-device":
        device.rename(d / "mh/retained-device.json")
    elif bad == "root":
        player.write_text("[]")
    elif bad == "wrong-total":
        p = json.loads(device.read_text()); p["draws"]["total"] = 3; device.write_text(json.dumps(p))
    else:
        p = json.loads(player.read_text())
        p.update(stream_sha256="0" * 64) if bad == "wrong-hash" else p["options"].update(max_frames=1)
        player.write_text(json.dumps(p))
    (d / "mh/draw-audit.json").write_text('{"pass": true}')
    run = tmp_path / "receipt"
    r1_receipt.main(["c4", "--run", str(run), "--check", "C4", "--census", str(d), "--log", str(log), "--exit", "0"])
    result = json.loads((run / "checks/C4.json").read_text())
    assert not result["pass"] and not result["original_draw_audits"]["mh"]["pass"]
    assert result["draw_scope"] == "default-full-geometry"
    capsys.readouterr()


@pytest.mark.parametrize("geometry", [False, True], ids=["copied-original-path", "coherent-original-geometry-and-hash"])
def test_the_c4_receipt_binds_original_bytes_to_held_census_identity(tmp_path, capsys, geometry):
    from test_s5_census_draws import substitute_player_original
    d, log = c4_census(tmp_path, "held")
    held = (d / "state-check.json").read_bytes()
    original = (d / "mh/original.sa2s").read_bytes()
    replacement = substitute_player_original(d / "mh", geometry=geometry)
    assert (replacement.read_bytes() != original) is geometry
    assert (d / "state-check.json").read_bytes() == held
    (d / "mh/draw-audit.json").write_text('{"pass": true}')
    run = tmp_path / "receipt"
    r1_receipt.main(["c4", "--run", str(run), "--check", "C4", "--census", str(d), "--log", str(log), "--exit", "0"])
    result = json.loads((run / "checks/C4.json").read_text())
    assert not result["pass"] and not result["original_draw_audits"]["mh"]["pass"]
    assert "held census stream identity" in result["original_draw_audits"]["mh"]["failures"][0]
    capsys.readouterr()


@pytest.mark.parametrize("bad", ["missing-table", "wrong-table-type", "missing-row", "extra-row", "wrong-row-type",
    "missing-path", "relative-path", "missing-hash", "wrong-hash", "no-end", "missing-end", "wrong-presents", "bool-presents"])
def test_the_c4_receipt_requires_exact_held_original_identities(tmp_path, capsys, bad):
    d, log = c4_census(tmp_path, bad)
    path = d / "state-check.json"
    state = json.loads(path.read_text())
    streams = state["stream_identity"]["streams"]
    row = streams["mh"]
    changes = {"missing-table": lambda: state.pop("stream_identity"),
        "wrong-table-type": lambda: state["stream_identity"].update(streams=[]),
        "missing-row": lambda: streams.pop("mh"), "extra-row": lambda: streams.update(extra=dict(row)),
        "wrong-row-type": lambda: streams.update(mh=[]), "missing-path": lambda: row.pop("path"),
        "relative-path": lambda: row.update(path="mh/original.sa2s"), "missing-hash": lambda: row.pop("sha256"),
        "wrong-hash": lambda: row.update(sha256="0" * 64), "no-end": lambda: row.update(read_to_end=False),
        "missing-end": lambda: row.pop("read_to_end"), "wrong-presents": lambda: row.update(presents=2),
        "bool-presents": lambda: row.update(presents=True)}
    changes[bad]()
    path.write_text(json.dumps(state))
    run = tmp_path / "receipt"
    r1_receipt.main(["c4", "--run", str(run), "--check", "C4", "--census", str(d), "--log", str(log), "--exit", "0"])
    assert not json.loads((run / "checks/C4.json").read_text())["pass"]
    capsys.readouterr()


def _receipt_validation_fixture(root, run, build, mutant=None):
    """Authored receipt-boundary records. Live mapping is exercised by the protocol suite."""
    import validation_run
    selected = validation_run.load_helper(root).selected_layer(root)
    (run / "selected-validation.json").write_text(json.dumps(selected))
    subprocess.run(["bash", str(root / "host/d3d9gpu/test/validation_probe/build_validation_probe.sh"),
                    str(run / "validation-probe")], env={**os.environ, "SA2_REPO": str(root)}, check=True,
                   capture_output=True)
    probe = validation_run.probe_identity(run / "validation-probe", root)
    producer = build / "shader/d3d9gpu_shaderc"
    producer.write_text("authored receipt fixture producer\n")
    mapping = selected["expected_mapping"]
    map_line = (f"1000-2000 r-xp 00000000 {os.major(mapping['device']):x}:{os.minor(mapping['device']):x} "
                f"{mapping['inode']} {mapping['realpath']}")
    for driver in ("nvidia", "lvp"):
        for job, mode in validation_run.R1_JOBS.items():
            path = run / driver / "validation" / (job + ".json")
            path.parent.mkdir(parents=True, exist_ok=True)
            settings = path.parent / (job + "-settings") / "vk_layer_settings.txt"
            settings.parent.mkdir()
            settings.write_text(validation_run.LAYER_SETTINGS)
            stderr = path.with_suffix(".stderr")
            stderr.write_text("".join("Validation Error: [ " + vuid + " ] authored expected control\n"
                for vuid in sorted(validation_run.CONTROL_VUIDS.get(job, set()))))
            observed = dict(mapping, schema="sa2-validation-live/v1", pid=123456,
                            instance="0x1234", verified=True, selected_key=selected["key"], matched_map_lines=[map_line])
            record = {"schema":"sa2-validation-run/v1", "pass":True, "pid":123456, "producer_exit":0,
                "command":[str(producer),mode], "producer_sha256":validation_run.sha(producer),
                "selected":selected, "observer":probe, "observations":[observed],
                "stderr_sha256":validation_run.sha(stderr), "stderr_bytes":stderr.stat().st_size,
                "icd_inputs":{"VK_DRIVER_FILES":{"path":validation_run.ICDS[driver],"sha256":validation_run.sha(validation_run.ICDS[driver])}},
                "settings_file":str(settings), "settings_sha256":validation_run.sha(settings),
                "environment":{**validation_run.selection_environment(selected),"SA2_VALIDATION_OBSERVER_FD":"4",
                    "VK_INSTANCE_LAYERS":":".join([validation_run.PROBE_NAME,validation_run.LAYER_NAME] +
                    (["VK_LAYER_SA2_vkcap"] if job == "load" else [])),"VK_LAYER_SETTINGS_PATH":str(settings.parent),
                    "VK_ADD_LAYER_PATH":":".join([str(Path(probe["manifest"]).parent),str(Path(selected["manifest"]).parent)] +
                        ([str(run/"vkcap/vkcap")] if job=="load" else [])),
                    "LD_LIBRARY_PATH":selected["loader_environment"]["LD_LIBRARY_PATH"],
                    "VK_LAYER_DUPLICATE_MESSAGE_LIMIT":"1000000","VK_LOADER_LAYERS_DISABLE":"~implicit~",
                    "VK_DRIVER_FILES":validation_run.ICDS[driver],"SDL_LOGGING":"gpu=info","SDL_VIDEO_DRIVER":"offscreen"}}
            if job=="load":record["environment"]["VKCAP_OUT"]=str(run/driver/"load-capture")
            path.write_text(json.dumps(record))
            (run / driver / (job + ".txt")).write_bytes(validation_run.LAYER_STARTUP.encode() + b"\n" + stderr.read_bytes())
    target = run / "nvidia/validation/load.json"
    data = json.loads(target.read_text())
    if mutant == "missing_record": target.unlink()
    elif mutant == "wrong_mapping":
        data["observations"][0]["matched_map_lines"] = ["1000-2000 r-xp 00000000 0:0 1 /other.so"]
    elif mutant == "wrong_api": data["observations"][0]["api_version"] = "1.4.309"
    elif mutant == "wrong_record_selection": data["selected"] = dict(selected,key="vulkan-validation-layers")
    elif mutant == "wrong_selection":
        (run / "selected-validation.json").write_text(json.dumps(dict(selected,key="vulkan-validation-layers")))
    elif mutant == "missing_callback": (run / "nvidia/load.txt").write_text("")
    elif mutant == "filtered_settings":
        settings = Path(data["settings_file"]); settings.write_text("khronos_validation.debug_action = 0\n")
        data["settings_sha256"] = validation_run.sha(settings)
    if mutant != "missing_record": target.write_text(json.dumps(data))


def test_the_receipt_writer_copies_nothing_it_has_not_checked(tmp_path, capsys):
    import hashlib
    pins = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]
    assert (ROOT / pins["validation-350-vulkan-validation-layers"]["library"]).is_file(), "the pinned validation layer is the writer's input"

    def make(name, extra_check=False, lvp_used=None, translator="t1", corpus="nv", source_sha=None,
             vk_env={"inherited": {}, "settings_files_present": []}, validation_case=None):
        run = tmp_path / name
        (run / "checks").mkdir(parents=True)
        if vk_env is not None:
            (run / "vk-env.json").write_text(json.dumps(vk_env))
        for c in r1_receipt.EXPECTED + (["C5"] if extra_check else []):
            body = {"check": c, "pass": True}
            if c == "corpus":
                body["streams"] = {s: {"profile_sha256": None} for s in r1_receipt.STREAMS}
            (run / "checks" / f"{c}.json").write_text(json.dumps(body))
        shas = {}
        for drv, rec in (("nvidia", "a=1\nb=2\n"), ("lvp", "a=1\nb=3\n")):
            (run / f"profile-{drv}.txt").write_text(f"# {drv} device\n" + rec)
            shas[drv] = hashlib.sha256(rec.encode()).hexdigest()
            (run / drv / "load" / "pairs").mkdir(parents=True)
            used = shas[drv] if not (drv == "lvp" and lvp_used) else lvp_used
            for f in (run / drv / "load" / "vs0.json", run / drv / "load" / "pairs" / "pair0.json"):
                f.write_text(json.dumps({"profile_sha256": used, "translator": translator}))
        body = json.loads((run / "checks" / "corpus.json").read_text())
        for s in body["streams"].values():
            s["profile_sha256"] = shas["nvidia"] if corpus == "nv" else "0" * 64
        (run / "checks" / "corpus.json").write_text(json.dumps(body))
        build = run / "build"
        (build / "shader").mkdir(parents=True)
        rel = "host/d3d9gpu/d9g_shader.h"
        sha = source_sha or hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
        (build / "shader" / "translator_identity.json").write_text(json.dumps({"id": "t1", "files": {f"repo:{rel}": sha}}))
        for rec in ("sa2_player_gpu.json", "sa2_player.json"):
            (build / rec).write_text(json.dumps({"sources": {rel: sha}}))
        _receipt_validation_fixture(ROOT, run, build, validation_case)
        return run, build

    def write(run, build):
        return r1_receipt.main(["write", "--run", str(run), "--out", str(run / "receipt.json"), "--build", str(build)])
    run, build = make("clean")
    assert write(run, build) == 0
    rec = json.loads((run / "receipt.json").read_text())
    assert rec["validation_layer_sha256"] == pins["validation-350-vulkan-validation-layers"]["library_sha256"]
    assert rec["corpus_profile"] == "nvidia" and rec["build_sources_checked"] == 1 and sorted(rec["checks"]) == sorted(r1_receipt.EXPECTED)
    assert rec["validation_environment"] == {"inherited": {}, "settings_files_present": []}
    assert "C10 checks full original registered-boot geometry and controls" in rec["scope"]
    assert "no game shader is drawn under the layer here" in rec["scope"]
    assert set(rec["shader_tools_sha256"]) == {
        "spirv-val", "spirv-opt", "spirv-cross", "shadercross", "shadercross's sdl-shadercross",
        "shadercross's spirv-cross", "shadercross's sdl3"}
    assert rec["commit"] == subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    for name, (kw, why) in {"a_check_never_run": ({"extra_check": True}, "not EXPECTED's"),
                            "a_profile_not_its_loads": ({"lvp_used": "f" * 64}, "its load's modules were compiled with"),
                            "another_translator": ({"translator": "t2"}, "the build's is t1"),
                            "a_corpus_on_neither_profile": ({"corpus": "other"}, "neither driver's"),
                            "a_build_of_other_sources": ({"source_sha": "0" * 64}, "other sources than the tree's"),
                            # the Vulkan environment the run inherited is recorded, and no layer settings file read
                            "no_live_record": ({"validation_case": "missing_record"}, "missing or extra R1"),
                            "wrong_live_mapping": ({"validation_case": "wrong_mapping"}, "retained mapping does not match loaded identity"),
                            "wrong_live_api": ({"validation_case": "wrong_api"}, "loaded identity differs"),
                            "wrong_selected_layer": ({"validation_case": "wrong_selection"}, "selected another"),
                            "wrong_observation_selection": ({"validation_case": "wrong_record_selection"}, "selected layer or observer differs"),
                            "no_unfiltered_callback": ({"validation_case": "missing_callback"}, "callback was not initialized"),
                            "filtered_layer_settings": ({"validation_case": "filtered_settings"}, "owned layer settings changed"),
                            "no_vulkan_environment_record": ({"vk_env": None}, "Vulkan environment record is missing"),
                            "a_layer_settings_file": ({"vk_env": {"inherited": {}, "settings_files_present": ["/h/vk_layer_settings.txt"]}},
                                                      "names a layer settings file")}.items():
        run, build = make(name, **kw)
        capsys.readouterr()
        assert write(run, build) == 2 and not (run / "receipt.json").exists(), name
        assert why in capsys.readouterr().err, name
    # the validation layer the run loaded is hashed and held to its pin: a repository whose pin names
    # another library refuses (the tree is this one's, by links, with its own pins and HEAD)
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    (repo / "Documentation" / "scripts").mkdir(parents=True)
    for rel in ("host", "private", "tools/s5", "Documentation/scripts/check_pins.py"):
        os.symlink(ROOT / rel, repo / rel)
    other = json.loads((ROOT / "tools" / "pins.json").read_text())
    other["tools"]["validation-350-vulkan-validation-layers"]["library_sha256"] = "0" * 64
    (repo / "tools" / "pins.json").write_text(json.dumps(other))
    for cmd in (["git", "init", "-q"], ["git", "add", "tools/pins.json"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "pins"]):
        assert subprocess.run(cmd, cwd=repo, capture_output=True).returncode == 0, cmd
    run, build = make("a_layer_not_its_pin")
    saved = r1_receipt.REPO
    try:
        r1_receipt.REPO = repo
        capsys.readouterr()
        assert write(run, build) == 2 and not (run / "receipt.json").exists()
        assert "library alias digest/map mismatch" in capsys.readouterr().err
    finally:
        r1_receipt.REPO = saved


def _identity_layout(base: Path, nested: bool) -> dict:
    """The identity manifest of a synthetic tree whose private/s5-tools is a real directory inside the
    repository (the main tree) or a symlink out of it (a clone, a worktree): a two-line unit, an empty
    regular archive and the host's g++, no DXVK."""
    repo = base / "repo"
    (repo / "src").mkdir(parents=True)
    tools = repo / "private" / "s5-tools" if nested else base / "elsewhere" / "s5-tools"
    (tools / "src" / "dxvk" / "inc").mkdir(parents=True)
    (tools / "install" / "include").mkdir(parents=True)
    if not nested:
        (repo / "private").mkdir()
        os.symlink(tools, repo / "private" / "s5-tools")
    (tools / "src" / "dxvk" / "inc" / "d.h").write_text("int d;\n")
    (tools / "install" / "include" / "s.h").write_text("int s;\n")
    (repo / "src" / "u.cpp").write_text('#include "../private/s5-tools/src/dxvk/inc/d.h"\n'
                                        '#include "../private/s5-tools/install/include/s.h"\n')
    build = repo / "private" / "generated" / "build"
    build.mkdir(parents=True)
    (build / "gen.cpp").write_text('#include "../../../src/u.cpp"\n')
    (base / "flags.json").write_text(json.dumps({"flags": ["-std=c++17"]}))
    (base / "lib.a").write_bytes(b"!<arch>\n")
    out = build / "translator_identity.json"
    r = subprocess.run([sys.executable, str(D9G / "shader" / "extract_dxvk_shader.py"), "identity",
                        "--compiler", "g++", "--archive", str(base / "lib.a"), "--repo", str(repo),
                        "--dxvk", str(repo / "private" / "s5-tools" / "src" / "dxvk"), "--out", str(out),
                        f"--unit={base / 'flags.json'}:{repo / 'src' / 'u.cpp'}",
                        f"--unit={base / 'flags.json'}:{build / 'gen.cpp'}"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(out.read_text())


def test_both_layouts_name_each_file_by_its_deepest_root(tmp_path):
    """The main tree (DXVK and the tool tree inside the repository) and a clone (private/s5-tools a
    symlink out of it) give one manifest and one identity: each file is named by its deepest root,
    never repo:private/..., so the property is held from any tree, not only the main one."""
    inside = _identity_layout(tmp_path / "inside", nested=True)
    outside = _identity_layout(tmp_path / "outside", nested=False)
    want = {"repo:src/u.cpp", "dxvk:inc/d.h", "s5-tools:install/include/s.h", "build:gen.cpp"}
    assert want <= set(inside["files"]), sorted(inside["files"])
    assert want <= set(outside["files"]), sorted(outside["files"])
    assert not [k for k in inside["files"] if k.startswith("repo:private/")]
    assert inside["id"] == outside["id"]
