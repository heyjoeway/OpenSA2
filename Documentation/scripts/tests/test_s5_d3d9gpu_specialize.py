"""host/d3d9gpu's sampler-mode specialization (I7b), held without a GPU.

Host 2 bakes DXVK's psSamplerModes word into a pixel shader (host/d3d9gpu/d9g_shader.h
Variant::sampler_modes): each samplerMode(r) call becomes (word >> 2r) & 3 before
legalisation, which folds the branches it decides, so a register at mode 0 keeps no depth-compare
sample and no gather (the Vulkan validation layer's VUID-vkCmdDraw-None-06479 on a colour view, and
SPIRV-Cross's depth2d typing of a colour texture). Built here (shader/build_translator.sh, into a
temporary directory) against the pinned DXVK checkout and dxbc-spirv archive; the shaders are synthetic
(host/d3d9gpu/test/sm3_asm.py) and DXVK's linked shape is hand-written SPIR-V (the pinned spirv-as).
Every assertion compares against a literal expected value:

  * DXVK's rule for each register's mode (d9g_shader.h fetch4_after and dxvk_sampler_mode, d3d9gpu_shaderc
    sampler-mode) after a sequence of D3DSAMP_MIPMAPLODBIAS writes (the fetch4 switch is sticky: only GET1
    turns a GET4 off), against the plan's own reading of one record (test/r1_receipt.py
    dxvk_sampler_mode, which cannot see the writes before it), case by case;
  * the variant key names the word, a base names none, and the cache keys on it (a miss, then a hit,
    and another word a miss); the reflection names the registers whose mode the shader reads; a word
    naming another register, and a vertex shader's specialization, are refused;
  * each mode keeps exactly its paths: at 0 no depth-compare sample and no gather, at 1 the gather,
    at 2 and 3 the depth-compare sample, on a shader of seven samplers at one mode and at mixed modes,
    and on 2D, cube and volume registers; every module is valid SPIR-V; a shader that samples nothing
    is the base byte for byte;
  * the pure-deletion check (test/spv_specialize.py) holds every one of them, and fails the base in the
    specialized module's place, a module at another word, each sampler-mode fault (the word ignored,
    each mode's depth bit flipped, a register reading the next one's mode) and each hand mutation of a
    specialized module (a branch's targets swapped, a store dropped, an operand replaced, a constant
    moved, a kept input's decoration dropped, a live loop's OpLoopMerge dropped);
  * a border variant at each mode: at 1, 2 and 3 the register keeps only its gather or depth-compare
    sample, recorded in the reflection for the device, and the module is a pure deletion of the base
    border variant (its unused border colour buffer gone with the colour sample); a border register
    the shader never samples is still refused;
  * the game sweep's words (r1_receipt.py sweep_words), literally;
  * the mutation pass in the removed region (r1_receipt.py removed_region_mutants and removed_region, run
    by C3's controls on the game's modules): four mutants of the base inside the paths the word removes
    (the depth-compare threshold, the clamp test, the depth result's broadcast, the gather's swizzle),
    each failed by the canonical diff on the base (C3's GPL half) and invisible to the pure deletion of
    the specialized module, which is why C3 keeps its GPL half on the unspecialized output;
  * the Metal check (test/metal_check.py): every texture typed by its register's mode through the pinned
    shadercross and spirv-cross; the base and the inverted fault fail it;
  * the freeze of DXVK's linked module (test/spv_freeze.py) at the word its pipeline gives: omitted, the
    depth-compare sample and gather go; given 2, the depth-compare sample stays and the rest goes; the
    unused ImageQuery capability goes; a module without SpecId 5 is left as it is; a malformed entry is
    refused; the word's source is named; the guard that spirv-opt only deleted names an added
    instruction and lets a branch folded to one of its targets through; and spv_canon.py reads a
    pipeline's SpecId entries and a variant's word;
  * the GPU script's new steps on modules this file builds: C12 (r1_receipt.py specialize) passes them,
    its sweep included, and fails a base in a specialized module's place, a pair that is not a pure
    deletion, a control that does not fail, bases from another translator or bytecode, one of C13's
    modules at another word and the sweep under a translator fault; C11 (r1_receipt.py metal) passes
    the objects, pairs, border variants and C13's modules and fails a base in their place, an inverted
    control that does not fail, a pair base that is no control and a missing case; C13 (r1_receipt.py
    modes) fails each wrong thing, its must-fail control (06479 at draw time, one per colour register)
    quiet, unloaded or short among them; the Vulkan environment step records what it inherited and
    fails on a layer settings file; and the plan specializes every pixel shader at word 0 and refuses a
    census sampler record whose mode is not 0.
"""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(os.environ.get("SA2_REPO") or Path(__file__).resolve().parents[3])
D9G = ROOT / "host" / "d3d9gpu"
TOOLS = ROOT / "private" / "s5-tools" / "install-shader-tools" / "bin"
sys.path.insert(0, str(D9G / "test"))
import metal_check  # noqa: E402
import r1_receipt  # noqa: E402
import sm3_asm  # noqa: E402
import spv_canon  # noqa: E402
import spv_freeze  # noqa: E402
import spv_specialize  # noqa: E402

VS_PASS = "vs_3_0\ndcl_position v0\ndcl_texcoord0 v1\ndcl_position o0\ndcl_texcoord0 o1\nmov o0, v0\nmov o1, v1\n"
PS_ONE = "ps_3_0\ndcl_texcoord0 v0\ndcl_2d s0\ntexld r0, v0, s0\nmov oC0, r0\n"
PS_NONE = "ps_3_0\ndcl_color v0\nmov oC0, v0\n"
# seven samplers at the registers the game's largest pixel shaders use (0, 1, 3-7)
PS_SEVEN = ("ps_3_0\ndcl_texcoord0 v0\n" + "".join(f"dcl_2d s{r}\n" for r in (0, 1, 3, 4, 5, 6, 7)) +
            "texld r0, v0, s0\n" + "".join(f"texld r1, v0, s{r}\nadd r0, r0, r1\n" for r in (1, 3, 4, 5, 6, 7)) +
            "mov oC0, r0\n")
PS_KINDS = ("ps_3_0\ndcl_texcoord0 v0\ndcl_2d s0\ndcl_cube s1\ndcl_volume s2\ntexld r0, v0, s0\ntexld r1, v0, s1\n"
            "texld r2, v0, s2\nadd r0, r0, r1\nadd oC0, r0, r2\n")
# a sample inside a loop: the loop stays whatever the word, so its OpLoopMerge must stay
PS_LOOP = ("ps_3_0\ndefi i0, 3, 0, 1, 0\ndef c0, 0, 0, 0, 0\ndcl_texcoord0 v0\ndcl_2d s0\nmov r1, c0\nloop aL, i0\n"
           "texld r0, v0, s0\nadd r1, r1, r0\nendloop\nmov oC0, r1\n")
SEVEN = (0, 1, 3, 4, 5, 6, 7)
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


def word(modes: dict) -> int:
    return sum((m & 3) << (2 * r) for r, m in modes.items())


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    for need in (ROOT / "private/s5-tools/src/dxvk-3.1.1", ROOT / "private/s5-tools/build/dxvk/compile_commands.json",
                 TOOLS / "spirv-val", TOOLS / "spirv-opt", TOOLS / "spirv-as", TOOLS / "shadercross", TOOLS / "spirv-cross"):
        assert need.exists(), f"{need} is missing: this test needs it (it fails, it does not skip)"
    out = tmp_path_factory.mktemp("d9g_specialize")
    r = subprocess.run(["bash", str(D9G / "shader" / "build_translator.sh"), str(out)], capture_output=True, text=True,
                       env={**os.environ, "SA2_REPO": str(ROOT)})
    assert r.returncode == 0 and "D3D9GPU_SHADER_BUILD_OK" in r.stdout, r.stdout[-2000:] + r.stderr[-2000:]
    shaders = {}
    for name, text in (("vs_pass", VS_PASS), ("ps_one", PS_ONE), ("ps_none", PS_NONE), ("ps_seven", PS_SEVEN),
                       ("ps_kinds", PS_KINDS), ("ps_loop", PS_LOOP)):
        path = out / f"{name}.bin"
        path.write_bytes(sm3_asm.assemble(text))
        shaders[name] = path
    fpath = out / "facts.json"
    fpath.write_text(json.dumps(NVIDIA_FACTS))
    return {"dir": out, "shaderc": out / "d3d9gpu_shaderc", "shaders": shaders, "facts": fpath}


def shaderc(build, *args, env=None, check=True):
    r = subprocess.run([str(build["shaderc"]), *map(str, args)], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def compile_into(build, out, names, *opts, env=None, check=True):
    out.mkdir(parents=True, exist_ok=True)
    return shaderc(build, "compile", build["facts"], out, *opts, *[build["shaders"][n] for n in names], env=env, check=check)


def refl(d, name):
    return json.loads((d / f"{name}.json").read_text())


def valid(spv):
    r = subprocess.run([str(TOOLS / "spirv-val"), "--target-env", "vulkan1.3", "--scalar-block-layout", str(spv)],
                       capture_output=True, text=True)
    return r.returncode == 0, r.stdout + r.stderr


FAULT = {"SA2_D3D9GPU_TEST_RUN": "1"}


# --- DXVK's rule ---------------------------------------------------------------------------------------
GET4, GET1, POINT, LINEAR = 0x34544547, 0x31544547, 1, 2
INTZ, DF16 = int.from_bytes(b"INTZ", "little"), int.from_bytes(b"DF16", "little")


def test_dxvks_rule_for_each_register_matches_the_plans_reading(build):
    # (D3DFORMAT, upgraded to D32 float, MAGFILTER, the stage's MIPMAPLODBIAS writes in order) ->
    # (DXVK's mode, the plan's reading of one record holding the last write)
    cases = {(0, 0, POINT, ()): (0, {0}),                     # nothing bound
             (21, 0, POINT, ()): (0, {0}),                    # A8R8G8B8
             (21, 0, POINT, (GET4,)): (0, {0}),               # fetch4 asked of a four-channel format
             (80, 0, POINT, ()): (2, {2, 3}),                 # D16: a shadow map
             (80, 1, POINT, ()): (3, {2, 3}),                 # stored as D32 float: the reference clamped
             (75, 0, LINEAR, ()): (2, {2, 3}),                # D24S8
             (75, 0, POINT, (GET4,)): (2, {2, 3}),            # depth compare wins over fetch4
             (INTZ, 0, POINT, ()): (0, {0, 1}),               # INTZ: a depth format DXVK does not compare
             (INTZ, 0, POINT, (GET4,)): (1, {1}),             # but it fetches four
             (DF16, 0, LINEAR, (GET4,)): (0, {0}),            # fetch4 needs point magnification
             (50, 0, POINT, (GET4,)): (1, {1}),               # L8
             (114, 0, POINT, (GET4,)): (1, {1}),              # R32F
             (28, 0, POINT, (GET4,)): (1, {1}),               # A8
             # DXVK's switch is sticky (d3d9_device.cpp:4579-4588): only GET1 turns a GET4 off, so the
             # bias a stage holds now does not say whether fetch4 is on, and one record cannot either
             (50, 0, POINT, (GET4, 0)): (1, {0, 1}),          # GET4, then a bias of 0: still fetch4
             (50, 0, POINT, (GET4, 0x3F800000)): (1, {0, 1}),  # then a bias of 1.0: still fetch4
             (50, 0, POINT, (GET4, GET1)): (0, {0}),          # GET1 turns it off
             (50, 0, POINT, (GET4, GET1, 0, GET4)): (1, {1}),  # and GET4 on again
             (50, 0, POINT, (0,)): (0, {0, 1})}               # never on, though the record cannot say so
    for (fmt, up, mag, writes), (want, plans) in cases.items():
        got = int(shaderc(build, "sampler-mode", fmt, up, mag, *writes).stdout)
        assert got == want, (fmt, up, mag, writes, got)
        ss = [0] * 14
        ss[r1_receipt.D3DSAMP_MIPMAPLODBIAS], ss[r1_receipt.D3DSAMP_MAGFILTER] = (writes or (0,))[-1], mag
        got_plans = r1_receipt.dxvk_sampler_mode({"tex": fmt != 0, "fmt": fmt, "ss": ss})
        assert got_plans == plans and want in got_plans, (fmt, writes, got_plans)


# --- the variant, its key and the cache ----------------------------------------------------------------
def test_the_key_names_the_word_and_what_cannot_be_specialized_is_refused(build, tmp_path):
    base, spec = tmp_path / "base", tmp_path / "spec"
    compile_into(build, base, ["ps_seven", "ps_kinds", "ps_none"])
    w = word({0: 2, 1: 1, 3: 3})
    compile_into(build, spec, ["ps_seven"], "--sampler-modes", hex(w))
    assert refl(base, "ps_seven")["variant"] == "bgra=0;border_u=0;border_v=0;border_linear=0;depth_clamp=0;sampler_modes=none"
    assert refl(spec, "ps_seven")["variant"] == "bgra=0;border_u=0;border_v=0;border_linear=0;depth_clamp=0;sampler_modes=0xc6"
    assert refl(base, "ps_seven")["sampler_mode_slots"] == 0b11111011
    # DXVK's lowering reads every sampled register's mode, a volume register's too (its sampler-state load,
    # d3d9_shader.cpp:926-927), though nothing of a volume sample depends on it (sm3_resources.cpp:607, :667)
    assert refl(base, "ps_kinds")["sampler_mode_slots"] == 0b111
    assert refl(base, "ps_none")["sampler_mode_slots"] == 0
    refusals = {("ps_seven", hex(word({2: 2}))): "names register 2, whose mode the shader never reads",
                ("ps_kinds", hex(word({5: 1}))): "names register 5, whose mode the shader never reads",
                ("ps_none", "1"): "names register 0, whose mode the shader never reads",
                ("vs_pass", "0"): "pixel shaders only"}
    for (name, wd), why in refusals.items():
        r = compile_into(build, tmp_path / f"refused_{name}", [name], "--sampler-modes", wd, check=False)
        assert r.returncode == 1 and why in r.stdout, (name, r.stdout)


def test_the_cache_keys_on_the_word(build, tmp_path):
    root = ROOT / "private" / "generated" / "s5" / "r1" / "test-cache" / f"specialize-{tmp_path.name}"
    try:
        def run(*opts):
            out = tmp_path / f"o{len(list(tmp_path.iterdir()))}"
            r = compile_into(build, out, ["ps_seven"], *opts, "--cache", root, "--repo", ROOT)
            return next(l for l in r.stdout.splitlines() if l.startswith("CACHE ")), out / "ps_seven.spv"
        assert run()[0] == "CACHE hits=0 misses=1 verified=0 corrupt=0"
        stat, m0 = run("--sampler-modes", "0")
        assert stat == "CACHE hits=0 misses=1 verified=0 corrupt=0"     # the word is a miss after the base
        stat, again = run("--sampler-modes", "0")
        assert stat == "CACHE hits=1 misses=0 verified=0 corrupt=0" and again.read_bytes() == m0.read_bytes()
        stat, m2 = run("--sampler-modes", hex(word({0: 2})))
        assert stat == "CACHE hits=0 misses=1 verified=0 corrupt=0" and m2.read_bytes() != m0.read_bytes()
    finally:
        if root.is_dir():
            shutil.rmtree(root)


# --- each mode keeps its paths, as a pure deletion --------------------------------------------------
@pytest.mark.parametrize("modes", [{}, {r: 1 for r in SEVEN}, {r: 2 for r in SEVEN}, {r: 3 for r in SEVEN},
                                   {0: 2, 1: 1, 3: 3, 4: 0, 5: 1, 6: 2, 7: 0}])
def test_each_mode_keeps_exactly_its_paths(build, tmp_path, modes):
    base, spec = tmp_path / "base", tmp_path / "spec"
    compile_into(build, base, ["ps_seven"])
    w = word(modes)
    compile_into(build, spec, ["ps_seven"], "--sampler-modes", hex(w))
    b, s = refl(base, "ps_seven"), refl(spec, "ps_seven")
    every = sum(1 << r for r in SEVEN)
    assert (b["depth_compare_registers"], b["gather_registers"]) == (every, every)
    assert s["depth_compare_registers"] == sum(1 << r for r, m in modes.items() if m >= 2)
    assert s["gather_registers"] == sum(1 << r for r, m in modes.items() if m == 1)
    assert s["sampler_registers"] == b["sampler_registers"] == list(SEVEN)
    ok, why = valid(spec / "ps_seven.spv")
    assert ok, why
    rep = spv_specialize.check(base / "ps_seven.spv", spec / "ps_seven.spv", w)
    assert rep["ok"] and rep["word_calls_folded"] == 7 and rep["blocks_dead"] >= 7, rep


def test_cube_and_volume_registers_and_a_shader_that_samples_nothing(build, tmp_path):
    base, spec, none = tmp_path / "base", tmp_path / "spec", tmp_path / "none"
    compile_into(build, base, ["ps_kinds", "ps_none"])
    w = word({0: 1, 1: 2})
    compile_into(build, spec, ["ps_kinds"], "--sampler-modes", hex(w))
    b, s = refl(base, "ps_kinds"), refl(spec, "ps_kinds")
    assert (b["depth_compare_registers"], b["gather_registers"]) == (0b011, 0b011)   # the volume register has neither
    assert (s["depth_compare_registers"], s["gather_registers"]) == (0b010, 0b001)
    assert valid(spec / "ps_kinds.spv")[0]
    assert spv_specialize.check(base / "ps_kinds.spv", spec / "ps_kinds.spv", w)["ok"]
    # the volume register's mode changes nothing
    vol = tmp_path / "volume"
    compile_into(build, vol, ["ps_kinds"], "--sampler-modes", hex(word({0: 1, 1: 2, 2: 2})))
    assert (vol / "ps_kinds.spv").read_bytes() == (spec / "ps_kinds.spv").read_bytes()
    compile_into(build, none, ["ps_none"], "--sampler-modes", "0")
    assert (none / "ps_none.spv").read_bytes() == (base / "ps_none.spv").read_bytes()
    assert spv_specialize.check(base / "ps_none.spv", none / "ps_none.spv", 0)["ok"]


# --- the pure-deletion check fails what is not one --------------------------------------------------
def raw(path):
    data = Path(path).read_bytes()
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    insts, pos = [], 5
    while pos < len(words):
        wc = words[pos] >> 16
        insts.append(words[pos:pos + wc])
        pos += wc
    return words[:5], insts


def write_raw(path, header, insts):
    flat = header + [w for i in insts for w in i]
    Path(path).write_bytes(struct.pack("<%dI" % len(flat), *flat))


def mutate(src, dst, fn):
    """Write src with fn(insts) applied to its instruction list (each a list of words)."""
    header, insts = raw(src)
    fn(insts)
    write_raw(dst, header, insts)
    return dst


def op(i):
    return i[0] & 0xFFFF


def test_the_pure_deletion_check_fails_what_is_not_one(build, tmp_path):
    base, spec = tmp_path / "base", tmp_path / "spec"
    compile_into(build, base, ["ps_seven"])
    w = word({0: 2, 1: 1, 3: 3})
    compile_into(build, spec, ["ps_seven"], "--sampler-modes", hex(w))
    b, s = base / "ps_seven.spv", spec / "ps_seven.spv"
    assert spv_specialize.check(b, s, w)["ok"]
    assert not spv_specialize.check(b, b, w)["ok"]                      # the base in the specialized module's place
    assert not spv_specialize.check(b, s, 0)["ok"]                      # a module at another word
    for fault in ("modes_skipped", "modes_inverted", "modes_slot_shifted"):
        d = tmp_path / fault
        compile_into(build, d, ["ps_seven"], "--sampler-modes", hex(w), env={**FAULT, "SA2_D3D9GPU_SHADER_FAULT": fault})
        assert not spv_specialize.check(b, d / "ps_seven.spv", w)["ok"], fault

    def swap_branch(insts):
        k = next(k for k, i in enumerate(insts) if op(i) == 250)      # OpBranchConditional
        insts[k] = insts[k][:2] + [insts[k][3], insts[k][2]] + insts[k][4:]

    def drop_store(insts):
        k = max(k for k, i in enumerate(insts) if op(i) == 62)        # OpStore (the output's)
        del insts[k]

    def replace_operand(insts):
        k = next(k for k, i in enumerate(insts) if op(i) == 87)        # OpImageSampleImplicitLod: its coordinate
        other = next(i[2] for i in insts if op(i) == 80 and i[2] != insts[k][4])   # another OpCompositeConstruct's value
        insts[k] = insts[k][:4] + [other] + insts[k][5:]

    def move_constant(insts):
        k = next(k for k, i in enumerate(insts) if op(i) == 43 and len(i) == 4 and i[3] == 0x3F800000)   # float 1.0
        insts[k] = insts[k][:3] + [0x3F000000]
    for name, fn in (("a_branch_swapped", swap_branch), ("a_store_dropped", drop_store),
                     ("an_operand_replaced", replace_operand), ("a_constant_moved", move_constant)):
        m = mutate(s, tmp_path / f"{name}.spv", fn)
        assert not spv_specialize.check(b, m, w)["ok"], name


def test_the_pure_deletion_check_keeps_every_decoration_and_a_live_loop(build, tmp_path):
    base, spec = tmp_path / "base", tmp_path / "spec"
    compile_into(build, base, ["ps_one", "ps_loop"])
    compile_into(build, spec, ["ps_one", "ps_loop"], "--sampler-modes", "0")
    for n in ("ps_one", "ps_loop"):
        assert spv_specialize.check(base / f"{n}.spv", spec / f"{n}.spv", 0)["ok"], n

    def drop_component(insts):   # OpDecorate %input Component 0 of a kept input
        del insts[next(k for k, i in enumerate(insts) if op(i) == 71 and i[2] == 31)]
    m = mutate(spec / "ps_one.spv", tmp_path / "no_component.spv", drop_component)
    assert valid(m)[0]                                       # spirv-val accepts it: only this check sees it
    rep = spv_specialize.check(base / "ps_one.spv", m, 0)
    assert not rep["ok"] and "Component" in rep["problems"][0] and "missing from SPEC" in rep["problems"][0], rep
    # the loop's header ends in an OpBranch, so it has one live successor like a folded branch: its
    # OpLoopMerge goes only with the loop (its continue target dead)
    _, insts = raw(spec / "ps_loop.spv")
    k = next(k for k, i in enumerate(insts) if op(i) == 246)   # OpLoopMerge
    assert op(insts[k + 1]) == 249                              # OpBranch
    m = mutate(spec / "ps_loop.spv", tmp_path / "no_loop_merge.spv", lambda insts: insts.pop(
        next(k for k, i in enumerate(insts) if op(i) == 246)))
    assert not spv_specialize.check(base / "ps_loop.spv", m, 0)["ok"]


@pytest.mark.parametrize("mode,unwrapped", [(0, {"depth_compare": 0, "gather": 0, "other": 0}),
                                            (1, {"depth_compare": 0, "gather": 1, "other": 0}),
                                            (2, {"depth_compare": 1, "gather": 0, "other": 0}),
                                            (3, {"depth_compare": 1, "gather": 0, "other": 0})])
def test_a_border_variant_at_each_mode(build, tmp_path, mode, unwrapped):
    """A border register at mode 1, 2 or 3 keeps only its gather or depth-compare sample, recorded for the
    device to leave unmapped by name, as the base records both; it is not refused."""
    base, spec = tmp_path / "base", tmp_path / "spec"
    compile_into(build, base, ["ps_one"], "--border-u", "1", "--border-v", "1")
    compile_into(build, spec, ["ps_one"], "--border-u", "1", "--border-v", "1", "--sampler-modes", str(mode))
    assert refl(base, "ps_one")["border_unwrapped"] == {"depth_compare": 1, "gather": 1, "other": 0}
    assert refl(spec, "ps_one")["border_unwrapped"] == unwrapped
    assert valid(spec / "ps_one.spv")[0]
    assert spv_specialize.check(base / "ps_one.spv", spec / "ps_one.spv", mode)["ok"]
    # a border register the shader never samples is still refused
    r = compile_into(build, tmp_path / "refused", ["ps_one"], "--border-u", "2", "--border-v", "2", "--sampler-modes",
                     str(mode), check=False)
    assert r.returncode == 1 and "register 1, which the shader never samples in 2D" in r.stdout, r.stdout


def test_the_sweep_words():
    assert r1_receipt.sweep_words([0], "ps0") == [0, 1, 2, 3]
    assert r1_receipt.sweep_words([], "ps1") == [0]
    two = r1_receipt.sweep_words([0, 3], "ps2")       # two registers have 16 words: all of them
    assert two == sorted(a | b << 6 for a in range(4) for b in range(4))
    seven = r1_receipt.sweep_words(list(SEVEN), "ps8")
    fixed = {0} | {m << 2 * r for r in SEVEN for m in (1, 2, 3)} | {sum(m << 2 * r for r in SEVEN) for m in (1, 2, 3)}
    assert fixed < set(seven) and len(seven) == len(fixed) + 12
    assert seven == r1_receipt.sweep_words(list(SEVEN), "ps8")   # seeded by the shader's name


def test_the_mutation_pass_in_the_removed_region(build, tmp_path):
    """Mutants of the base inside the paths word 0 removes (r1_receipt.py removed_region_mutants, which C3's
    controls run on the game's sampling pixel shaders): C3's GPL half (the canonical diff on the base)
    fails each, and the specialized module's pure deletion cannot see one (it holds no removed path): the
    reason the identity stays on the unspecialized output."""
    base, spec = tmp_path / "base", tmp_path / "spec"
    compile_into(build, base, ["ps_one"])
    compile_into(build, spec, ["ps_one"], "--sampler-modes", "0")
    b, s = base / "ps_one.spv", spec / "ps_one.spv"
    muts = r1_receipt.removed_region_mutants(b)
    assert sorted(muts) == ["broadcast", "clamp_test", "swizzle", "threshold"]
    for name, data in muts.items():
        mutant = tmp_path / f"{name}.spv"
        mutant.write_bytes(data)
        assert data != b.read_bytes()
        assert valid(mutant)[0], name
        assert not spv_canon.compare(b, mutant, 0, reference_side="host2")["ok"], name   # C3's GPL half sees it
        assert spv_specialize.check(mutant, s, 0)["ok"], name                            # a pure deletion cannot
    # the controls step's pass over a load: the base in DXVK's dump's place (a host-2 reading of it)
    load, gpl = tmp_path / "load", tmp_path / "gpl"
    compile_into(build, load, ["ps_one", "ps_none"])
    compile_into(build, load / "specialized", ["ps_one", "ps_none"], "--sampler-modes", "0")
    gpl.mkdir()
    shutil.copy(load / "ps_one.spv", gpl / f"{refl(load, 'ps_one')['debug_name']}.spv")
    rep = r1_receipt.removed_region(load, gpl, reference_side="host2")
    assert rep["ok"] and (rep["shaders"], rep["mutants"], rep["caught_by_the_gpl_half"], rep["seen_by_the_pure_deletion"]) \
        == (1, 4, 4, 0), rep
    # a base without the shape the mutants need is refused, never skipped
    with pytest.raises(r1_receipt.Refused):
        r1_receipt.removed_region_mutants(load / "ps_none.spv")


# --- Metal --------------------------------------------------------------------------------------------
def test_metal_types_every_texture_by_its_mode(build, tmp_path):
    base, spec, inv = tmp_path / "base", tmp_path / "spec", tmp_path / "inverted"
    compile_into(build, base, ["ps_seven", "ps_one", "ps_kinds"])
    w = word({0: 2, 1: 1, 3: 3})
    compile_into(build, spec, ["ps_seven"], "--sampler-modes", hex(w))
    compile_into(build, spec, ["ps_one"], "--sampler-modes", "0")
    compile_into(build, spec, ["ps_kinds"], "--sampler-modes", hex(word({1: 2})))
    rep = metal_check.batch(spec, base, 3)
    assert rep["ok"], rep["problems"]
    assert rep["modules"]["ps_seven"]["types"] == {"0": "depth2d", "1": "texture2d", "3": "depth2d", "4": "texture2d",
                                                   "5": "texture2d", "6": "texture2d", "7": "texture2d"}
    assert rep["modules"]["ps_kinds"]["types"] == {"0": "texture2d", "1": "depthcube", "2": "texture3d"}
    assert all(m["control_fails_as_required"] for m in rep["modules"].values())
    compile_into(build, inv, ["ps_seven", "ps_one"], "--sampler-modes", "0",
                 env={**FAULT, "SA2_D3D9GPU_SHADER_FAULT": "modes_inverted"})
    bad = metal_check.batch(inv, None, 2)
    assert not bad["ok"] and all(m["problems"] for m in bad["modules"].values())
    # the base itself, read as if specialized at 0: every colour register comes out depth2d
    fake = tmp_path / "fake"
    fake.mkdir()
    shutil.copy(base / "ps_one.spv", fake / "ps_one.spv")
    r = refl(base, "ps_one")
    r["variant"] = r["variant"].replace("sampler_modes=none", "sampler_modes=0")
    (fake / "ps_one.json").write_text(json.dumps(r))
    assert not metal_check.batch(fake, None, 1)["ok"]


# --- DXVK's linked module frozen at its word -----------------------------------------------------------
DXVK_LINKED = """
               OpCapability Shader
               OpCapability ImageQuery
               OpMemoryModel Logical GLSL450
               OpEntryPoint Fragment %main "main" %uv %out %tex %smp
               OpExecutionMode %main OriginUpperLeft
       %file = OpString "fs.0123456789abcdef0123456789abcdef"
               OpSource Unknown 0 %file
               OpName %samplerMode "samplerMode"
               OpDecorate %uv Location 0
               OpDecorate %out Location 0
               OpDecorate %tex DescriptorSet 0
               OpDecorate %tex Binding 0
               OpDecorate %smp DescriptorSet 0
               OpDecorate %smp Binding 1
               OpDecorate %modes SpecId 5
       %void = OpTypeVoid
     %fnvoid = OpTypeFunction %void
       %uint = OpTypeInt 32 0
      %float = OpTypeFloat 32
        %v2f = OpTypeVector %float 2
        %v2u = OpTypeVector %uint 2
        %v4f = OpTypeVector %float 4
       %bool = OpTypeBool
     %fnmode = OpTypeFunction %uint %uint
      %modes = OpSpecConstant %uint 0
     %uint_0 = OpConstant %uint 0
     %uint_1 = OpConstant %uint 1
     %uint_2 = OpConstant %uint 2
    %float_1 = OpConstant %float 1
        %img = OpTypeImage %float 2D 2 0 0 1 Unknown
       %pimg = OpTypePointer UniformConstant %img
        %sam = OpTypeSampler
       %psam = OpTypePointer UniformConstant %sam
       %simg = OpTypeSampledImage %img
        %tex = OpVariable %pimg UniformConstant
        %smp = OpVariable %psam UniformConstant
        %pin = OpTypePointer Input %v2f
       %pout = OpTypePointer Output %v4f
         %uv = OpVariable %pin Input
        %out = OpVariable %pout Output
%samplerMode = OpFunction %uint None %fnmode
      %index = OpFunctionParameter %uint
         %l0 = OpLabel
         %sh = OpIMul %uint %index %uint_2
         %bf = OpBitFieldUExtract %uint %modes %sh %uint_2
               OpReturnValue %bf
               OpFunctionEnd
       %main = OpFunction %void None %fnvoid
         %l1 = OpLabel
          %c = OpLoad %v2f %uv
          %i = OpLoad %img %tex
          %s = OpLoad %sam %smp
          %m = OpFunctionCall %uint %samplerMode %uint_0
          %d = OpUGreaterThanEqual %bool %m %uint_2
               OpSelectionMerge %l3 None
               OpBranchConditional %d %l2 %l3
         %l2 = OpLabel
        %si2 = OpSampledImage %simg %i %s
         %dr = OpImageSampleDrefImplicitLod %float %si2 %c %float_1
         %dv = OpCompositeConstruct %v4f %dr %dr %dr %dr
               OpStore %out %dv
               OpReturn
         %l3 = OpLabel
          %g = OpIEqual %bool %m %uint_1
               OpSelectionMerge %l5 None
               OpBranchConditional %g %l4 %l5
         %l4 = OpLabel
         %qs = OpImageQuerySizeLod %v2u %i %uint_0
         %qf = OpConvertUToF %v2f %qs
         %c2 = OpFAdd %v2f %c %qf
        %si4 = OpSampledImage %simg %i %s
         %ga = OpImageGather %v4f %si4 %c2 %uint_0
               OpStore %out %ga
               OpReturn
         %l5 = OpLabel
        %si5 = OpSampledImage %simg %i %s
         %cs = OpImageSampleImplicitLod %v4f %si5 %c
               OpStore %out %cs
               OpReturn
               OpFunctionEnd
"""


def assemble(tmp_path, text, name):
    src, out = tmp_path / f"{name}.spvasm", tmp_path / f"{name}.spv"
    src.write_text(text)
    r = subprocess.run([str(TOOLS / "spirv-as"), "--target-env", "vulkan1.3", "-o", str(out), str(src)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert valid(out)[0], valid(out)[1]
    return out


def counts(path):
    ops = [i.op for i in spv_canon.parse_words(Path(path).read_bytes(), str(path))]
    caps = [spv_canon.lit(i.lits[0]) for i in spv_canon.parse_words(Path(path).read_bytes(), str(path))
            if i.op == "OpCapability"]
    return {"dref": ops.count("OpImageSampleDrefImplicitLod"), "gather": ops.count("OpImageGather"),
            "plain": ops.count("OpImageSampleImplicitLod"), "calls": ops.count("OpFunctionCall"),
            "image_query": "ImageQuery" in caps}


def test_the_freeze_folds_dxvks_word_by_its_pipelines_specialization(tmp_path):
    src = assemble(tmp_path, DXVK_LINKED, "dxvk")
    assert counts(src) == {"dref": 1, "gather": 1, "plain": 1, "calls": 1, "image_query": True}
    omitted = spv_freeze.freeze(src, tmp_path / "omitted.spv", [[0, 0, 4], [4, 4, 4]], "07000000fcffffff")
    assert omitted["frozen"] and omitted["word"] == 0 and omitted["calls_folded"] == 1 and "default" in omitted["word_from"]
    assert counts(tmp_path / "omitted.spv") == {"dref": 0, "gather": 0, "plain": 1, "calls": 0, "image_query": False}
    given = spv_freeze.freeze(src, tmp_path / "given.spv", [[5, 0, 4]], "02000000")
    assert given["word"] == 2 and "VkSpecializationInfo" in given["word_from"]
    assert counts(tmp_path / "given.spv") == {"dref": 1, "gather": 0, "plain": 0, "calls": 0, "image_query": False}
    fetch4 = spv_freeze.freeze(src, tmp_path / "fetch4.spv", [[5, 4, 4]], "0000000001000000")
    assert fetch4["word"] == 1
    assert counts(tmp_path / "fetch4.spv") == {"dref": 0, "gather": 1, "plain": 0, "calls": 0, "image_query": True}
    assert (omitted["word_source"], given["word_source"]) == ("default", "entry")
    with pytest.raises(spv_freeze.Refused):
        spv_freeze.freeze(src, tmp_path / "bad.spv", [[5, 0, 2]], "0000")
    # the freeze holds spirv-opt's result to a pure deletion of what it was handed: an instruction added or
    # rewritten is named; a conditional branch become a branch to one of its own targets is not
    before = src.read_bytes()
    header, insts = raw(src)
    assert spv_freeze.not_deleted(before, before) == []
    added = [list(i) for i in insts]
    k = next(k for k, i in enumerate(added) if op(i) == 43)                       # an OpConstant ...
    added.insert(k, [added[k][0], added[k][1], header[3], 7])                     # ... and a new one beside it
    write_raw(tmp_path / "added.spv", header[:3] + [header[3] + 1] + header[4:], added)
    assert spv_freeze.not_deleted(before, (tmp_path / "added.spv").read_bytes()) == [[43, added[k][1], header[3], 7]]
    k = next(k for k, i in enumerate(insts) if op(i) == 250)                      # the first OpBranchConditional
    cond = insts[k]
    for target, folded in ((cond[2], True), (cond[3], True), (cond[1], False)):   # its two targets; not a target
        out = [list(i) for i in insts]
        out[k] = [(2 << 16) | 249, target]
        write_raw(tmp_path / "branch.spv", header, out)
        assert (spv_freeze.not_deleted(before, (tmp_path / "branch.spv").read_bytes()) == []) == folded, target
    plain = assemble(tmp_path, DXVK_LINKED.replace("               OpDecorate %modes SpecId 5\n", "")
                     .replace("%modes = OpSpecConstant %uint 0", "%modes = OpConstant %uint 0"), "plain")
    kept = spv_freeze.freeze(plain, tmp_path / "kept.spv", [], "")
    assert not kept["frozen"] and (tmp_path / "kept.spv").read_bytes() == plain.read_bytes()
    # spv_canon.py's readings for the linked batch
    assert spv_canon.spec_id_value(json.dumps({"entries": [[0, 0, 4], [5, 4, 4]], "data": "0700000003000000"}), 5) == 3
    assert spv_canon.spec_id_value(json.dumps({"entries": [[0, 0, 4], [4, 4, 4]], "data": "07000000fcffffff"}), 5) is None
    assert spv_canon.sampler_modes_of("bgra=0;depth_clamp=0;sampler_modes=0xc6") == 0xC6
    assert spv_canon.sampler_modes_of("bgra=0;depth_clamp=0;sampler_modes=none") is None
    assert spv_canon.sampler_modes_of("bgra=0;depth_clamp=0") is None


# --- the GPU script's steps on modules built here ----------------------------------------------------
MODES_WORDS = {"seven_mixed": {0: 2, 1: 1, 3: 3}, "seven_depth": {r: 2 for r in SEVEN}}


def fake_load(build, d, pair_word=0):
    """A load directory as d3d9gpu_shaderc load writes it, built on the CPU: the base objects, every
    pixel shader specialized at word 0, one pair, one border variant, and their bases; and C13's modules
    (modes/modules: ps_seven at a mixed word and at a word of depth compares only) with their bases
    (modes-base/modules), named by case as d3d9gpu_shaderc modes names them."""
    load, bases = d / "load", d / "bases"
    compile_into(build, load, ["vs_pass", "ps_one", "ps_seven", "ps_none"])
    compile_into(build, load / "specialized", ["ps_one", "ps_seven", "ps_none"], "--sampler-modes", "0")
    (load / "pairs").mkdir()
    (bases / "border").mkdir(parents=True)
    vs, ps = build["shaders"]["vs_pass"], build["shaders"]["ps_seven"]
    (d / "linked").mkdir()
    shaderc(build, "link", build["facts"], d / "linked", vs, ps, "pair0.ps", "--sampler-modes", hex(pair_word))
    shutil.copy(d / "linked" / "pair0.ps.spv", load / "pairs" / "pair0.ps.spv")
    shaderc(build, "link", build["facts"], bases, vs, ps, "pair0")
    link = json.loads((d / "linked" / "pair0.ps.json").read_text())
    (load / "pairs" / "pair0.json").write_text(json.dumps({
        "schema": "sa2-d3d9gpu-shader-pair/v1", "name": "pair0", "ps": {"name": "ps_seven", "variant": link["ps_variant"]},
        "profile_sha256": link["profile_sha256"], "translator": link["translator"]}))
    v = {"shader": "ps_one", "border_u": 1, "border_v": 1, "border_linear": 0, "sampler_modes": 0}
    lab = r1_receipt.border_label(v)
    compile_into(build, d / "bv", ["ps_one"], "--border-u", "1", "--border-v", "1", "--sampler-modes", "0")
    (load / "variants").mkdir()
    shutil.copy(d / "bv" / "ps_one.spv", load / "variants" / f"{lab}.spv")
    compile_into(build, bases / "border" / lab, ["ps_one"], "--border-u", "1", "--border-v", "1")
    plan = d / "plan.json"
    plan.write_text(json.dumps({"shaders": [{"name": n, "stage": "pixel"} for n in ("ps_one", "ps_seven", "ps_none")],
                                "specialized": [{"shader": n, "sampler_modes": 0} for n in ("ps_one", "ps_seven", "ps_none")],
                                "pairs": [{"name": "pair0"}], "border_variants": [v]}))
    for f in ("modes_skipped", "modes_inverted"):
        compile_into(build, d / "faults" / f / "specialized", ["ps_one", "ps_seven", "ps_none"], "--sampler-modes", "0",
                     env={**FAULT, "SA2_D3D9GPU_SHADER_FAULT": f})
    for case, modes in MODES_WORDS.items():
        for leg, opts in (("modes", ("--sampler-modes", hex(word(modes)))), ("modes-base", ())):
            out = d / leg / case
            compile_into(build, out, ["ps_seven"], *opts)
            (d / leg / "modules").mkdir(exist_ok=True)
            for ext in ("spv", "json"):
                shutil.copy(out / f"ps_seven.{ext}", d / leg / "modules" / f"{case}.{ext}")
    (d / "modes" / "modes.json").write_text(json.dumps({"cases": [{"name": c} for c in MODES_WORDS]}))
    return load, bases, plan


def c12(build, d, plan, load, bases):
    return run_step(d, "specialize", "--driver", "x", "--plan", plan, "--load", load, "--bases", bases, "--faults", d / "faults",
                    "--modes-modules", d / "modes" / "modules", "--modes-base", d / "modes-base" / "modules",
                    "--shaderc", build["shaderc"], "--facts", build["facts"], "--named", build["dir"], "--sweep-out", d / "sweep")


def c11(d, plan, load, bases):
    return run_step(d, "metal", "--driver", "x", "--plan", plan, "--load", load, "--bases", bases,
                    "--modes-modules", d / "modes" / "modules", "--modes-base", d / "modes-base" / "modules",
                    "--modes-json", d / "modes" / "modes.json", "--faults", d / "faults")


def run_step(run, *argv):
    rc = r1_receipt.main([argv[0], "--run", str(run), *map(str, argv[1:])])
    written = sorted((run / "checks").glob("*.json"), key=lambda q: q.stat().st_mtime_ns)
    return rc, json.loads(written[-1].read_text())


def test_the_specialize_step_C12(build, tmp_path, monkeypatch):
    d = tmp_path / "clean"
    load, bases, plan = fake_load(build, d)
    rc, c = c12(build, d, plan, load, bases)
    assert rc == 0 and c["pass"], c
    assert c["objects"]["sampling"] == ["ps_one", "ps_seven"] and c["modules_validated"] == 5 + len(MODES_WORDS)
    assert {k: v["failing"] for k, v in c["controls"].items()} == {"modes_skipped": ["ps_one", "ps_seven"],
                                                                  "modes_inverted": ["ps_one", "ps_seven"]}
    assert c["synthetic"]["checked"] == 2 and c["synthetic"]["words"] == sorted(word(m) for m in MODES_WORDS.values())
    # the sweep: ps_none at 0; ps_one at 0-3; ps_seven at 0, 21 single-register words, 3 uniform ones, 12 mixed
    assert c["sweep"]["words_per_shader"] == {"ps_one": 4, "ps_seven": 37, "ps_none": 1} and c["sweep"]["rows"] == 42, c["sweep"]
    # the base in a specialized module's place
    d2 = tmp_path / "a_base_in_its_place"
    load, bases, plan = fake_load(build, d2)
    shutil.copy(load / "ps_seven.spv", load / "specialized" / "ps_seven.spv")
    rc, c = c12(build, d2, plan, load, bases)
    assert rc == 1 and not c["pass"]
    # a pair specialized at another word than its record names
    d3 = tmp_path / "a_pair_at_another_word"
    load, bases, plan = fake_load(build, d3, pair_word=2)
    rec = json.loads((load / "pairs" / "pair0.json").read_text())
    rec["ps"]["variant"] = rec["ps"]["variant"].replace("sampler_modes=0x2", "sampler_modes=0")
    (load / "pairs" / "pair0.json").write_text(json.dumps(rec))
    rc, c = c12(build, d3, plan, load, bases)
    assert rc == 1 and not c["pass"] and c["pairs_and_border_variants"]["problems"]
    # a control that no longer fails (the fault's modules replaced by clean ones)
    d4 = tmp_path / "a_control_that_does_not_fail"
    load, bases, plan = fake_load(build, d4)
    for q in (d4 / "faults" / "modes_inverted" / "specialized").glob("*.spv"):
        shutil.copy(load / "specialized" / q.name, q)
    rc, c = c12(build, d4, plan, load, bases)
    assert rc == 1 and not c["controls"]["modes_inverted"]["fails_as_required"]
    # a pair's base built by another translator, and a border base from other bytecode
    d5 = tmp_path / "bases_not_the_modules"
    load, bases, plan = fake_load(build, d5)
    link = json.loads((bases / "pair0.json").read_text())
    (bases / "pair0.json").write_text(json.dumps({**link, "translator": "another"}))
    lab = r1_receipt.border_label(json.loads(plan.read_text())["border_variants"][0])
    br = json.loads((bases / "border" / lab / "ps_one.json").read_text())
    (bases / "border" / lab / "ps_one.json").write_text(json.dumps({**br, "bytecode_sha256": "0" * 64}))
    rc, c = c12(build, d5, plan, load, bases)
    probs = " ".join(c["pairs_and_border_variants"]["problems"])
    assert rc == 1 and "pair0: its base's translator" in probs and f"{lab}: its base is not" in probs, probs
    # one of C13's modules at another word than its reflection names
    d6 = tmp_path / "a_synthetic_module_at_another_word"
    load, bases, plan = fake_load(build, d6)
    shutil.copy(d6 / "modes" / "modules" / "seven_depth.spv", d6 / "modes" / "modules" / "seven_mixed.spv")
    rc, c = c12(build, d6, plan, load, bases)
    assert rc == 1 and c["synthetic"]["problems"]
    # the sweep under a translator fault: the word's paths are not the ones kept
    d7 = tmp_path / "the_sweep_under_a_fault"
    load, bases, plan = fake_load(build, d7)
    monkeypatch.setenv("SA2_D3D9GPU_TEST_RUN", "1")
    monkeypatch.setenv("SA2_D3D9GPU_SHADER_FAULT", "modes_inverted")
    rc, c = c12(build, d7, plan, load, bases)
    assert rc == 1 and c["sweep"]["problem_count"] > 0


def test_the_metal_step_C11(build, tmp_path):
    d = tmp_path / "clean"
    load, bases, plan = fake_load(build, d)
    rc, c = c11(d, plan, load, bases)
    # ps_one and ps_seven, pair0's pixel shader and the border variant; C13's two cases, one of depth
    # compares only (no colour register: its base is no control)
    assert rc == 0 and c["pass"], c
    assert (c["game"]["checked"], c["game"]["objects"], c["game"]["pairs"], c["game"]["border_variants"]) == (4, 2, 1, 1)
    assert c["game"]["controls_failing_as_required"] == 4 and c["synthetic"]["controls_not_applicable"] == ["seven_depth"]
    assert c["synthetic"]["depth_typed"] == 2 + 7 and c["synthetic"]["checked"] == c["synthetic"]["cases"] == 2
    shutil.copy(load / "ps_one.spv", load / "specialized" / "ps_one.spv")      # the base in its place
    rc, c = c11(d, plan, load, bases)
    assert rc == 1 and not c["pass"]
    d2 = tmp_path / "inverted_not_failing"
    load, bases, plan = fake_load(build, d2)
    for q in (d2 / "faults" / "modes_inverted" / "specialized").glob("*.spv"):
        shutil.copy(load / "specialized" / q.name, q)
    rc, c = c11(d2, plan, load, bases)
    assert rc == 1 and not c["control_modes_inverted"]["fails_as_required"]
    # a pair's base that is no control: the specialized pair in its place
    d3 = tmp_path / "a_pair_base_that_is_no_control"
    load, bases, plan = fake_load(build, d3)
    shutil.copy(load / "pairs" / "pair0.ps.spv", bases / "pair0.spv")
    rc, c = c11(d3, plan, load, bases)
    assert rc == 1 and c["game"]["controls_failing_as_required"] == 3
    # a case of C13 missing from its modules
    d4 = tmp_path / "a_case_missing"
    load, bases, plan = fake_load(build, d4)
    (d4 / "modes" / "modules" / "seven_mixed.json").unlink()
    rc, c = c11(d4, plan, load, bases)
    assert rc == 1 and not c["pass"]


LAYER_ON = "INFO: Validation layers enabled\n"


DRAW_ERROR = "Validation Error: [ VUID-vkCmdDraw-None-06479 ] | MessageID = 0x1\n"


def test_the_modes_step_C13(tmp_path):
    def cases(shas):
        out = []
        for name, slots in (("colour", [0]), ("d16", [2]), ("clamp", [3]), ("fetch4", [1]), ("mixed", [0, 2, 1, 3])):
            out.append({"name": name, "word": sum(m << (2 * r) for r, m in enumerate(slots)), "pass": True,
                        "modes_as_named": True, "slots": [{"register": r, "mode": m} for r, m in enumerate(slots)],
                        "frame_sha256": shas.get(name, name)})
        return out

    # the base leg under the layer: one 06479 per colour register drawn (colour, fetch4, mixed's two)
    def case(name, log=LAYER_ON + "SAMPLER_MODES_OK\n", base_log=LAYER_ON + DRAW_ERROR * 4 + "SAMPLER_MODES_OK\n",
             spec_shas=None, base_shas=None, mutate=None, base_specialized=False):
        d = tmp_path / name
        d.mkdir()
        spec = {"specialized": True, "cases": cases(spec_shas or {}), "pass": True}
        base = {"specialized": base_specialized, "cases": cases(base_shas or {}), "pass": True}
        if mutate:
            mutate(spec)
        (d / "spec.json").write_text(json.dumps(spec))
        (d / "base.json").write_text(json.dumps(base))
        (d / "log.txt").write_text(log)
        (d / "base-log.txt").write_text(base_log)
        return run_step(d, "modes", "--driver", "x", "--log", d / "log.txt", "--json", d / "spec.json", "--base-json",
                        d / "base.json", "--base-log", d / "base-log.txt")
    rc, c = case("clean")
    assert rc == 0 and c["pass"] and c["modes_drawn"] == [0, 1, 2, 3], c
    assert c["control"]["seen"] and c["control"]["colour_registers"] == 4 and c["validation_errors"] == {}
    for name, kw in {"a_frame_not_the_bases": {"base_shas": {"d16": "other"}},
                     "a_case_wrong": {"mutate": lambda s: s["cases"][1].update({"pass": False})},
                     "a_mode_not_as_named": {"mutate": lambda s: s["cases"][3].update({"modes_as_named": False})},
                     "no_mode_1": {"mutate": lambda s: s.update({"cases": [c for c in s["cases"] if c["name"] != "fetch4"]})},
                     "no_mixed_case": {"mutate": lambda s: s.update({"cases": [c for c in s["cases"] if c["name"] != "mixed"]})},
                     "the_base_specialized": {"base_specialized": True},
                     "a_validation_error": {"log": LAYER_ON + "Validation Error: [ VUID-x ] | MessageID = 0x1\nSAMPLER_MODES_OK\n"},
                     "no_layer": {"log": "SAMPLER_MODES_OK\n"},
                     "a_failed_marker": {"log": LAYER_ON + "SAMPLER_MODES_FAIL\n"},
                     # the must-fail control: draw-time errors are seen (an inherited message filter, a layer
                     # that did not load, or one error short would all read as a quiet run)
                     "the_control_quiet": {"base_log": LAYER_ON + "SAMPLER_MODES_OK\n"},
                     "the_control_without_the_layer": {"base_log": DRAW_ERROR * 4 + "SAMPLER_MODES_OK\n"},
                     "the_control_one_short": {"base_log": LAYER_ON + DRAW_ERROR * 3 + "SAMPLER_MODES_OK\n"}}.items():
        rc, c = case(name, **kw)
        assert rc == 1 and not c["pass"], name


def test_the_vulkan_environment_step(tmp_path, monkeypatch):
    """test_shaders.sh records the VK_* variables it inherited before it clears them, and fails where the
    layer's settings file (read before VK_LAYER_SETTINGS_PATH) is present."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VK_LAYER_MESSAGE_ID_FILTER", "VUID-vkCmdDraw-None-06479")
    out = tmp_path / "vk-env.json"
    assert r1_receipt.main(["vk-env", "--out", str(out)]) == 0
    rec = json.loads(out.read_text())
    settings = tmp_path / "xdg" / "vulkan" / "settings.d" / "vk_layer_settings.txt"
    assert rec["inherited"]["VK_LAYER_MESSAGE_ID_FILTER"] == "VUID-vkCmdDraw-None-06479"
    named = "$XDG_DATA_HOME/vulkan/settings.d/vk_layer_settings.txt"
    assert rec["settings_files_checked"] == [named] and rec["settings_files_present"] == []
    settings.parent.mkdir(parents=True)
    settings.write_text("khronos_validation.message_id_filter = VUID-vkCmdDraw-None-06479\n")
    assert r1_receipt.main(["vk-env", "--out", str(out)]) == 1
    assert json.loads(out.read_text())["settings_files_present"] == [named]
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert r1_receipt.main(["vk-env", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["settings_files_checked"] == ["$HOME/.local/share/vulkan/settings.d/vk_layer_settings.txt"]


def test_the_plan_specializes_every_pixel_shader_and_refuses_a_record_it_cannot_join(tmp_path):
    import hashlib
    vs, ps = sm3_asm.assemble(VS_PASS), sm3_asm.assemble(PS_ONE)
    dump, refl_dir = tmp_path / "dump", tmp_path / "refl"
    dump.mkdir()
    refl_dir.mkdir()
    sha = {k: hashlib.sha256(b).hexdigest() for k, b in (("vs", vs), ("ps", ps))}
    (dump / f"{sha['vs']}.bin").write_bytes(vs)
    (dump / f"{sha['ps']}.bin").write_bytes(ps)
    (refl_dir / "vs0.json").write_text(json.dumps({"bytecode_sha256": sha["vs"], "input_signature": [[0, 0], [5, 0]]}))
    (refl_dir / "ps0.json").write_text(json.dumps({"bytecode_sha256": sha["ps"], "sampler_mask": 1, "rt_mask": 1}))

    def run(sampler):
        keys = {"schema": "sa2-d3d9gpu-keys/v3", "complete": True, "shaders": {"vs": [sha["vs"]], "ps": [sha["ps"]]},
                "samplers": [sampler], "inputs": [{"vs": 1, "ps": 1, "elems": [[0, 0, 2, 0, 0, 0], [0, 16, 2, 0, 5, 0]],
                                                   "rt": [[21, 0, 0, 0, 0, 0, 1]], "ds": [75, 0, 0, 0, 0, 0, 0], "prim": 4}]}
        kp = []
        for s in r1_receipt.STREAMS:
            (tmp_path / f"{s}.keys.json").write_text(json.dumps(keys))
            kp.append(tmp_path / f"{s}.keys.json")
        return r1_receipt.main(["plan", "--keys", *map(str, kp), "--bytecode", str(dump), "--reflections", str(refl_dir),
                                "--out", str(tmp_path / "plan.json")])
    ss = [0, 3, 3, 3, 0, 2, 2, 2, 0, 0, 1, 0, 0, 0]
    colour = {"id": 0, "ss": ss, "tex": True, "cube": False, "depth": False, "fmt": 21, "stages": 1}
    assert run(colour) == 0
    plan = json.loads((tmp_path / "plan.json").read_text())
    assert plan["specialized"] == [{"shader": "ps0", "sampler_modes": 0}]
    assert [p["sampler_modes"] for p in plan["pairs"]] == [0]
    assert plan["sampler_modes"]["word"] == 0 and plan["sampler_modes"]["nonzero"] == 0
    assert run({**colour, "fmt": 75, "depth": True}) == 2                                   # a depth texture: 2 or 3
    fetch4 = list(ss)
    fetch4[r1_receipt.D3DSAMP_MIPMAPLODBIAS], fetch4[r1_receipt.D3DSAMP_MAGFILTER] = GET4, POINT
    assert run({**colour, "fmt": 50, "ss": fetch4}) == 2                                     # L8 with fetch4: 1
    assert run({**colour, "fmt": 50, "ss": fetch4, "stages": 2}) == 0                        # at a stage ps0 does not sample
