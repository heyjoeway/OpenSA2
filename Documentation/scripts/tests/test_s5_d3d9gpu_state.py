"""The second host's state check (R2b-prep, private/generated/s5/r2b/PLAN.md section 12.3), held
without a GPU: host/d3d9gpu/build.sh builds d3d9gpu_state_check from the pinned DXVK checkout and
SDL tarball (a missing install, checkout or tarball fails, it does not skip), and then:

  * the extractor (test/extract_refs.py) refuses a DXVK file that is not its pinned blob and a
    tarball that is not the pinned one, finds every span once, names every substitution with its
    count and every cut, marks every generated file with its source's licence and the
    alteration, holds ref_dxvk.cpp's copy of DXVK's notice to the LICENSE, cuts no statement that
    a trailing comment or an unbraced condition would corrupt, and nothing third-party is tracked;
  * the build reads no dxbc-spirv file (the compile flags, the dependency files) and links no
    dxbc-spirv symbol; every DXVK header a compile read is at its pinned blob, and verify-deps
    refuses an installed Vulkan header, /usr/local, a host file outside each side's set, an
    SDL-side compile that read DXVK, and a run with no SDL-side dependency file or two (the
    build clears an earlier build's dependency files and checks all nine compiles, eight DXVK
    side and the SDL one); the two bundled vulkan_core.h give the same value for every
    enumerant the facts use;
  * the domain sweep, on three device axes and every texture format d9g makes: d9g never maps a
    value DXVK does not build; BORDER is the SDL patch's CLAMP_TO_BORDER on the axes whose device
    has it (NVIDIA-like and AMD-like), equal by the BORDER_SWIZZLE rule where DXVK chains its
    component mapping, and the shader route's fallback everywhere else (every X8 or A8 texture, every
    custom colour, every device without it), never DIFFERS; mapped on a view DXVK swizzles
    (map-border-swizzled-view: X8 and A8) it DIFFERS on every axis, by the named reason and, where
    DXVK chains the mapping, by sm_pNextBorderSwizzle (and for A8 by the custom colour DXVK
    chains); the unmapped list is the named one;
    DXVK's read set is inside the key; key sufficiency; the defaults; the render-state audit; the
    draw tests (vertex counts, the fan cache, the UP arena, draw_args); the comparator's self-test,
    on a plain, a mapped BORDER and a fallback BORDER sampler and the BORDER_SWIZZLE rule's
    DXVK-side clauses;
  * every mapping control fails by its field, on a CPU-model session's keys file (or, not
    exercised there, on the sweep), and none passes; the vertex-input controls (U9's five beside
    R2b's three) fail the census itself; the census's classes are disjoint;
  * the vertex input by the vertex shader's signature (U9; the R1 plan's section 2.4): the session's
    keys file carries the device's own reflection (each shader's and each tuple's), a census without
    the shader record is refused, one whose record is not the device's reflection fails, by shader
    and by tuple, and DXVK is evaluated with the record; the null stream is equal by its rule
    NULL_STREAM; the four stand-ins left are named, each with the facts it decides; no VS_SIGNATURE
    class remains;
  * ruling 3 (sdl-patch phase B), on CPU-model sessions with the patch's capabilities as a test
    parameter: with both, DEPTH_CLAMP is 0 and every key sets the depth-clamp property, BORDER is
    mapped, and map-border-colour, map-depth-clamp-off, map-depth-bias-push, map-depth-clamp-extra
    and map-border-fallback fail the census by their fields; with neither, every key carries
    R_DEPTH_CLAMP and BORDER is the fallback, which reads STATE_CHECK_FALLBACK_OWED, never OK, and
    a failure there, test leg or not, reads STATE_CHECK_FAIL; a keys file made under a test run's
    capability override is a test leg that never reads OK; the keys files' capabilities, override
    and BORDER decisions are the parameters' and the check's; the project's DEPTH_CLAMP ruling
    record says what the check takes it to say;
  * the verdict markers: pending, never OK, while DEPTH_CLAMP has no ruling; only a ruling
    committed in the checkout the check was built from (a scratch repository's own build of
    it here), under Documentation/decisions/, read as committed, decided by the user, naming a
    decision that names DEPTH_CLAMP, moves it, with its path and hash in the result; no flag
    does, no other checkout's ruling does, and a run in which no mode ran is no pass;
  * the recorded reflection (test/shader_record.py): both readings of a pixel shader's masks
    (its tokens, the disassembly) must agree; a hazard tuple is RT_FEEDBACK only where the pixel
    shader's reflection samples the stage, the device's and the record's alike, and
    map-ps-sampler-superset (R2b's superset stand-in) fails the census there;
  * the stand-ins' census names, per stand-in, the facts its choice decides (finding 4);
  * the CPU-model session's report and keys file (schema, ids reproducing the counts, BORDER in
    undecided and not in not_implemented, a mapping fault in the report's fault field);
  * scan_projection.py on a synthetic dump with a known projection: equal passes, a missing
    census tuple is unchecked and fails, an extra one fails, and so do a declaration, a target
    extent and a texture format the census does not record;
  * check_params.py refuses a log without a feature the check reads, naming another device,
    naming a driver of another vendor than the device's query (N9), applying a built-in
    configuration, or setting an option the check's shims model; it takes the SDL patch's
    capabilities from the report, and refuses a device whose detected depth clip and clamp is not
    what DXVK's log of the same driver reports, or a report from before phase B.

The GPU half (the four stream censuses through the device, and the DXVK path unchanged) is
host/d3d9gpu/state_census.sh and host/d3d9gpu/test_d3d9gpu.sh step 9.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
D9G = ROOT / "host" / "d3d9gpu"
TEST = D9G / "test"
INSTALL = ROOT / "private" / "s5-tools" / "install"
PINS = json.loads((ROOT / "tools" / "pins.json").read_text())["tools"]
DXVK = ROOT / PINS["dxvk"]["path"]
TARBALL = ROOT / PINS["sdl3"]["path"]
PY = sys.executable
AXES = ("nvidia-like", "amd-like", "intel-like")
CONTROLS = ("map-blend-swap", "map-zfunc-swap", "map-normalize-off", "map-writemask", "map-address-swap",
            "map-border-clamp", "map-border-colour", "map-depth-clamp-off", "map-depth-bias-push", "map-depth-clamp-extra",
            "map-border-swizzled-view", "map-border-fallback", "map-lod-unquantised",
            "map-bgra-mask", "map-fan-order", "map-depth-clip-off", "map-alpha-op", "map-ds-bind", "map-cull-swap",
            "map-vi-offset", "map-bgra-extra", "map-fan-on-list", "map-vi-declorder", "map-vi-no-null",
            "map-null-vertex-rate", "map-vi-unread-attr", "map-bgra-decl-index", "map-ps-sampler-superset")
# the SDL patch's capabilities per sweep axis (check_params.py AXIS_CAPABILITIES)
AXIS_CAPS = {"nvidia-like": True, "amd-like": True, "intel-like": False}
RULING = ROOT / "Documentation" / "decisions" / "2026-10-05-S5-depth-clamp-ruling.json"
# the texture formats d9g makes (d9g_format.cpp kFormats) as the sweep's tables name them, X8R8G8B8
# (22) and A8 (28) the ones whose DXVK view swizzles; DXT1, DXT3, DXT5 by FOURCC
IDENTITY_VIEW_TEXTURES = ("levels 6 format 21", "levels 6 format 23", "levels 6 format 827611204",
                          "levels 6 format 861165636", "levels 6 format 894720068", "levels 6 format 75 depth", "none")
SWIZZLED_VIEW_TEXTURES = ("levels 6 format 22", "levels 6 format 28")
SWIZZLED_REASON = "BORDER mapped where the patch cannot build DXVK's border: DXVK's view of the texture swizzles"
FALLBACK_REASON = ("fallback BORDER where the patch builds DXVK's border (a standard colour on an identity view, on a device "
                   "with CLAMP_TO_BORDER)")
VERTEX_INPUT_CONTROLS = ("map-bgra-mask", "map-vi-offset", "map-bgra-extra", "map-vi-declorder", "map-vi-no-null",
                         "map-null-vertex-rate", "map-vi-unread-attr", "map-bgra-decl-index")
# the stand-ins left once the shaders' reflection is the device's own (U9)
STANDINS = {"fs_sample_rate_shading", "fs_exports_sample_mask", "gp_base_pipeline", "can_use_sample_locations"}
# the session's shaders' reflection, as the device's translator gives it (test_d3d9gpu.cpp kVs,
# kVsReorder, kPs): the position; the colour, the position and a normal; oC0 and no sampler
SESSION_VS = [[[0, 0]], [[10, 0], [0, 0], [3, 0]]]
SESSION_PS = [[0, 1]]
# the facts no self-test base can compare raw (state_check.cpp kNeverRaw): the depth-bias values,
# the minimum sample shading and the logic op, each off on every tuple d9g maps
NEVER_RAW = ["rs_depthBiasConstantFactor", "rs_depthBiasClamp", "rs_depthBiasSlopeFactor", "rs_depthBiasRepresentation",
             "rs_depthBiasExact", "ms_minSampleShading", "cb_logicOp"]
sys.path.insert(0, str(TEST))
import extract_refs  # noqa: E402


def need_inputs() -> None:
    for p, what in ((INSTALL / "include" / "dxvk" / "d3d9.h", "the pinned DXVK Native headers"),
                    (DXVK / "src" / "d3d9" / "d3d9_device.cpp", "the pinned DXVK checkout"),
                    (TARBALL, "the pinned SDL tarball"), (shutil.which("g++") or "", "g++")):
        if not p or not Path(p).exists():
            pytest.fail(f"no {what} ({p}): run private/s5-tools/build.sh")


# Every subprocess runs on this checkout and its own install, nothing inherited: a caller's SA2_*
# variable (SA2_REPO, SA2_S5_INSTALL, SA2_DXVK_SRC, ...), PYTHON, CC or CXX would point the build
# and the extractor at another tree's install, checkout or tools (a clone's run reached the main
# tree's shared install that way).
ENV = {k: v for k, v in os.environ.items() if not k.startswith("SA2_") and k not in ("PYTHON", "CC", "CXX")}
ENV.update(SA2_REPO=str(ROOT), SA2_S5_INSTALL=str(INSTALL))


def run(cmd, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("env", ENV)
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


@pytest.fixture(scope="session")
def build(tmp_path_factory) -> Path:
    need_inputs()
    out = tmp_path_factory.mktemp("d3d9gpu-state")
    # an earlier build's depfile, which would stand in for an SDL compile that wrote none: the
    # build clears it (were it read, two SDL-side files would refuse)
    stale = out / "d3d9gpu-gen" / "refs" / "obj" / "ref_sdl_earlier.d"
    stale.parent.mkdir(parents=True)
    stale.write_text(f"ref_sdl.o: {TEST / 'ref_sdl.c'} {TEST / 'ref_facts.h'} /usr/include/stdint.h\n")
    r = run(["bash", D9G / "build.sh"], env=dict(ENV, OUT=str(out)), timeout=3600)
    assert r.returncode == 0 and "D3D9GPU_BUILD_OK" in r.stdout, (r.stdout + r.stderr)[-4000:]
    # every compile the state check links is checked by its own dependency file, the SDL one too
    assert "9 dependency files (8 DXVK side, 1 SDL side)" in r.stdout, r.stdout[-2000:]
    assert not stale.exists()
    # the extraction names the install's SDL configuration (patched: its revision header carries
    # the pinned vendor string), and the patches it applied for it, none for a stock install
    manifest = json.loads((out / "d3d9gpu-gen" / "refs" / "extract_manifest.json").read_text())
    revision_h = (INSTALL / "include" / "SDL3" / "SDL_revision.h").read_text()
    patched = f'#define SDL_VENDOR_INFO "{PINS["sdl3"].get("vendor_info")}"' in revision_h
    assert manifest["sdl"]["configuration"] == ("patched" if patched else "stock"), manifest["sdl"]
    assert manifest["sdl"]["patches"] == (PINS["sdl3"]["patches"] if patched else []), manifest["sdl"]
    return out


@pytest.fixture(scope="session")
def params(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("params")
    out = {}
    for a in AXES:
        p = d / f"{a}.json"
        r = run([PY, TEST / "check_params.py", "--axis", a, "--out", p])
        assert r.returncode == 0, r.stderr
        out[a] = p
    return out


def check(build: Path, *args, timeout=1800) -> subprocess.CompletedProcess:
    return run([build / "d3d9gpu_state_check", *args], timeout=timeout)


def caps_params(src: Path, out: Path, border: bool, clamp: bool, override: str | None = None) -> Path:
    """An axis's parameters with the SDL patch's capabilities (and a test run's override) set: the
    CPU-model sessions' devices report them as the session was made."""
    p = json.loads(src.read_text())
    p["device"].update(sampler_clamp_to_border=border, depth_clip_and_clamp=clamp, capability_override=override)
    out.write_text(json.dumps(p))
    return out


# ------------------------------------------------------------------ the extractor
def test_the_extractor_refuses_a_dxvk_file_that_is_not_its_pinned_blob(tmp_path):
    need_inputs()
    src = DXVK / "src" / "d3d9" / "d3d9_device.cpp"
    bad = tmp_path / "d3d9_device.cpp"
    bad.write_text(src.read_text().replace("m_dirty.clr(D3D9DeviceDirtyFlag::BlendState);",
                                           "m_dirty.clr(D3D9DeviceDirtyFlag::BlendState); // altered", 1))
    r = run([PY, TEST / "extract_refs.py", "--out", tmp_path / "out", "--override", f"src/d3d9/d3d9_device.cpp={bad}"])
    assert r.returncode == 2 and "EXTRACT_REFUSED" in r.stderr and "not its blob" in r.stderr, r.stderr


def test_the_extractor_refuses_a_tarball_that_is_not_the_pinned_one(tmp_path):
    need_inputs()
    copy = tmp_path / "SDL3.tar.gz"
    copy.write_bytes(TARBALL.read_bytes() + b"\0")
    r = run([PY, TEST / "extract_refs.py", "--out", tmp_path / "out", "--sdl-tarball", copy])
    assert r.returncode == 2 and "not the pinned" in r.stderr, r.stderr


def test_every_span_is_found_once_and_every_alteration_is_named(build):
    refs = build / "d3d9gpu-gen" / "refs"
    m = json.loads((refs / "extract_manifest.json").read_text())
    assert m["dxvk"]["commit"] == PINS["dxvk"]["commit"] and m["sdl"]["sha256"] == PINS["sdl3"]["sha256"]
    names = [s["name"] for s in m["spans"]]
    assert len(names) == len(set(names)) and len(names) >= 90
    for want in ("BindBlendState", "BindDepthStencilState", "BindRasterizerState", "BindDepthBias", "BindSampler",
                 "BindMultiSampleState", "UpdateAlphaToCoverangeAndAlphaTest", "ResetState.renderStates",
                 "DxvkGraphicsPipelineFragmentOutputState", "DxvkGraphicsPipelinePreRasterizationState", "DxvkSampler",
                 "updateDynamicState.DepthTest", "updateDynamicState.StencilTest", "DxvkContext.setBlendMode",
                 "VULKAN_CreateGraphicsPipeline.state", "VULKAN_CreateSampler.state", "SDLToVK_BlendFactor"):
        assert want in names, want
    for s in m["spans"]:
        assert s["lines"][0] <= s["lines"][1] and len(s["source_sha256"]) == 64
        for sub in s["substitutions"]:
            assert sub["count"] >= 1 and sub["old"] and sub["new"]
    # OPEN-1 stand-ins and the device features the spans read are named
    assert {"fs_output_mask", "fs_sample_rate_shading", "fs_exports_sample_mask"} <= set(m["standins_open1"])
    assert "extDepthBiasControl.depthBiasExact" in m["device_features_read"]
    for inc, lic in (("dxvk_extract.inc", "zlib/libpng license"), ("sdl_extract.inc", "Permission is granted")):
        head = (refs / inc).read_text()[:6000]
        assert lic in head and "Altered from the original" in head, inc
        assert re.search(r'#line \d+ "', (refs / inc).read_text())
    # the SDL side's context (the redefined assertion, the shims) is listed beside its spans
    assert "SDL_assert_release" in (refs / "sdl_extract.inc").read_text()[:8000]
    # what the sampler substitution leaves out is named (the extraction re-review's NIT 4)
    sampler = next(s for s in m["spans"] if s["name"] == "DxvkSampler")
    assert any("DxvkSamplerDescriptorHeap::createSampler" in c for c in sampler["cuts"])
    # ref_dxvk.cpp carries DXVK's LICENSE verbatim; every span's section is included, and every
    # included section is a span's
    assert m["dxvk_notice"]["equals"] == "LICENSE" and m["dxvk_notice"]["line"] > 0
    assert "SHIM" in m["sections"]["dxvk"] and "PIPELINE_VALIDATE" in m["sections"]["sdl"]
    # every file the extraction wrote is held at its hash, the SDL headers among them
    assert "sdl/include/SDL3/SDL_gpu.h" in m["outputs"] and any(k.startswith("sdl/khronos/") for k in m["outputs"])
    # nothing third-party is tracked
    tracked = run(["git", "-C", ROOT, "ls-files", "host/d3d9gpu"]).stdout.split()
    assert not [t for t in tracked if t.endswith((".inc", "vulkan_core.h")) or "khronos" in t]


def test_the_span_cutter_refuses_a_statement_it_would_corrupt():
    good = "  int a = 1;\n  rs[D3DRS_ZENABLE] = 1;\n"
    span = extract_refs.Span("t", "x", "rs[D3DRS_ZENABLE] = 1;", end="line")
    assert extract_refs.cut(span, good)[2] == ["  rs[D3DRS_ZENABLE] = 1;"]
    # a statement whose last line has a trailing comment would swallow the next statement
    multi = extract_refs.Span("t", "x", "rs[D3DRS_ZENABLE] =", end="line", match="prefix")
    assert extract_refs.cut(multi, "  rs[D3DRS_ZENABLE] =\n      1;\n  Bad();\n")[2] == ["  rs[D3DRS_ZENABLE] =", "      1;"]
    with pytest.raises(extract_refs.Refusal, match="trailing comment"):
        extract_refs.cut(multi, "  rs[D3DRS_ZENABLE] =\n      1; // x\n  Bad();\n")
    # a statement under an unbraced condition would lose it
    with pytest.raises(extract_refs.Refusal, match="unbraced"):
        extract_refs.cut(span, "  if (x)\n  rs[D3DRS_ZENABLE] = 1;\n")
    with pytest.raises(extract_refs.Refusal, match="unbraced"):
        extract_refs.statements(["{", "  if (x)", "    rs[D3DRS_ZENABLE] = 1;"], 1, r"^\s*rs\[D3DRS_\w+\]\s*=", "t")
    assert extract_refs.statements(["{", "  rs[D3DRS_ZENABLE] = 1;", "}"], 1, r"^\s*rs\[D3DRS_\w+\]\s*=", "t") == \
        [(2, ["  rs[D3DRS_ZENABLE] = 1;"])]


# ------------------------------------------------------------------ the build
def test_verify_deps_refuses_what_is_not_the_pinned_sources(build, tmp_path):
    refs = build / "d3d9gpu-gen" / "refs"
    dxvk_d = refs / "obj" / "ref_dxvk.d"
    base = dxvk_d.read_text().rstrip().rstrip("\\").rstrip()

    def verify(*deps: Path) -> subprocess.CompletedProcess:
        return run([PY, TEST / "extract_refs.py", "verify-deps", "--refs", refs, "--deps", *deps])

    def with_extra(name: str, extra: str) -> Path:
        d = tmp_path / name
        d.write_text(base + f" \\\n {extra}\n")
        return d

    # the SDL side: its own files and the system's, and never a DXVK file
    sdl_inc = refs / "sdl" / "include" / "SDL3" / "SDL.h"
    sdl_ok = tmp_path / "ref_sdl.d"
    sdl_ok.write_text(f"ref_sdl.o: {TEST / 'ref_sdl.c'} {refs / 'sdl_extract.inc'} {sdl_inc} {TEST / 'ref_facts.h'} "
                      f"/usr/include/stdint.h\n")
    r = verify(dxvk_d, sdl_ok)
    assert r.returncode == 0 and "2 dependency files (1 DXVK side, 1 SDL side)" in r.stdout, r.stdout + r.stderr
    # the SDL compile unchecked (no -MD, or verify-deps run before it) or checked twice refuses
    for deps, n in (((dxvk_d,), 0), ((dxvk_d, sdl_ok, sdl_ok), 2)):
        r = verify(*deps)
        assert r.returncode == 2 and f"{n} SDL-side dependency files, not 1" in r.stderr, (n, r.stdout + r.stderr)
    for name, extra, why in (("vk.d", "/usr/include/vulkan/vulkan_core.h", "installed header"),
                             ("local.d", "/usr/local/include/foo.h", "installed header"),
                             ("sdl3.d", "/usr/include/SDL3/SDL.h", "installed header"),
                             ("host.d", str(D9G / "d9g_gpu.h"), "not one of its files")):
        r = verify(with_extra(name, extra), sdl_ok)
        assert r.returncode == 2 and why in r.stderr, (name, r.stderr)
    sdl_dx = tmp_path / "ref_sdl_dx.d"
    sdl_dx.write_text(sdl_ok.read_text().rstrip() + f" {DXVK / 'include' / 'vulkan' / 'include' / 'vulkan' / 'vulkan_core.h'}\n")
    r = verify(dxvk_d, sdl_dx)
    assert r.returncode == 2 and "SDL side's compile read the DXVK file" in r.stderr, r.stderr
    sdl_vk = tmp_path / "ref_sdl_vk.d"
    sdl_vk.write_text(sdl_ok.read_text().rstrip() + " /usr/include/vulkan/vulkan_core.h\n")
    r = verify(dxvk_d, sdl_vk)
    assert r.returncode == 2 and "installed header" in r.stderr, r.stderr
    # an SDL file the extraction did not write at that hash
    altered = tmp_path / "refs-copy"
    shutil.copytree(refs, altered, ignore=shutil.ignore_patterns("obj"))
    (altered / "sdl" / "include" / "SDL3" / "SDL.h").write_text("/* altered */\n")
    sdl_alt = tmp_path / "ref_sdl_alt.d"
    sdl_alt.write_text(f"ref_sdl.o: {TEST / 'ref_sdl.c'} {altered / 'sdl' / 'include' / 'SDL3' / 'SDL.h'}\n")
    dxvk_alt = tmp_path / "ref_dxvk_alt.d"
    dxvk_alt.write_text(dxvk_d.read_text().replace(str(refs), str(altered)))
    r = run([PY, TEST / "extract_refs.py", "verify-deps", "--refs", altered, "--deps", dxvk_alt, sdl_alt])
    assert r.returncode == 2 and "differs from what the extraction wrote" in r.stderr, r.stderr


def test_the_check_reads_and_links_no_dxbc_spirv(build):
    refs = build / "d3d9gpu-gen" / "refs"
    for f in ("flags.d3d9.json", "flags.dxvk.json", "flags.vulkan.json"):
        rec = json.loads((refs / f).read_text())
        assert not [k for k in rec["kept"] if "dxbc" in k or k.startswith("-I")]
    deps = list((refs / "obj").glob("*.d"))
    assert sorted(d.name for d in deps) == ["d3d9_format.d", "d3d9_util.d", "dxvk_constant_state.d", "dxvk_format.d",
                                            "dxvk_util.d", "ref_dxvk.d", "ref_link_shims.d", "ref_sdl.d",
                                            "vulkan_names.d"]
    for d in deps:
        assert "dxbc-spirv" not in d.read_text(), d
    nm = run(["nm", build / "d3d9gpu_state_check"])
    assert nm.returncode == 0 and "dxbc_spv" not in nm.stdout


def test_the_two_vulkan_headers_agree_on_every_enumerant_the_facts_use(build):
    r = check(build, "--enum-values")
    rows = [line.split() for line in r.stdout.splitlines()]
    # 27, and phase B's two standard border colours beside transparent black (BORDER_SWIZZLE's rule)
    assert r.returncode == 0 and len(rows) == 29
    assert all(a == b for a, b in rows), rows


# ------------------------------------------------------------------ the sweep and its companions
@pytest.mark.parametrize("axis", AXES)
def test_the_domain_sweep_never_maps_a_value_dxvk_does_not_build(build, params, axis, tmp_path):
    out = tmp_path / "domain.json"
    r = check(build, "--params", params[axis], "--domain", "--defaults", "--draw-tests", "--self-test",
              "--audit", build / "d3d9gpu-gen" / "refs" / "render_state_audit.json", "--out", out)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK", r.stdout[-3000:]
    d = json.loads(out.read_text())
    dom = d["domain"]
    assert dom["mapped_but_differs"] == 0 and dom["sampler_differs"] == 0 and dom["differing_fields"] == []
    assert dom["pipeline_tuples"] > 50000 and dom["sampler_tuples"] > 100000
    assert dom["mapped_equal"] > 10000 and dom["equal_by_rule"] > 0 and dom["sampler_fallback_border"] > 0
    assert dom["sampler_tuples"] == (dom["sampler_equal"] + dom["sampler_equal_by_rule"] + dom["sampler_fallback_border"] +
                                     dom["sampler_unmapped"] + dom["sampler_differs"])
    assert dom["pipeline_tuples"] == (dom["mapped_equal"] + dom["equal_by_rule"] + dom["unmapped"] +
                                      dom["undecided_rt_feedback"] + dom["mapped_but_differs"])
    # the sweep is the check that holds d9g's RT_FEEDBACK decision (rt_unresolvableHazard): the
    # census's recorded reflection refutes every census hazard, so a d9g that never decided
    # RT_FEEDBACK would pass the census and fail here (the checker-logic re-review's NIT 11)
    assert dom["undecided_rt_feedback"] > 0
    # the read set is inside the key, and no state outside it moves DXVK's facts
    assert dom["reads_outside_key"] == [] and dom["key_insufficient"] == []
    assert "D3DRS_ZENABLE" in dom["read_set"] and "D3DRS_SRCBLEND" in dom["read_set"]
    # the unmapped values are named; none is a census class
    unm = dom["unmapped_values"]
    for want in ("D3DTADDRESS_MIRRORONCE", "D3DFILL_POINT", "X8R8G8B8 render target", "SRCCOLOR2", "BLENDFACTOR as an alpha factor",
                 "a non-zero depth bias", "an indexed triangle fan", "NVDB", "driver hack value",
                 "a vertex shader with a POSITIONT element", "a vertex shader with no declaration",
                 "more than one render target bound as a texture", "neither samples it nor writes the target",
                 "a depth-stencil surface set while its texture is bound at a stage"):
        assert any(want in u for u in unm), want
    for census_class in ("D3DDECLTYPE_D3DCOLOR", "TRIANGLEFAN", "D3DTADDRESS_BORDER", "A8R8G8B8"):
        assert not any(u.startswith(census_class) for u in unm), census_class
    # a sampled depth-stencil surface is unmapped on every base, bound or not (the transcription
    # re-review's finding 2: the rt512-ds256 base binds none)
    assert set(dom["classification"]["depth-stencil surface sampled"]) == {"unmapped"}
    assert d["defaults"]["differ"] == [] and d["defaults"]["dxvk_sets"] == 103
    assert d["draw_tests"]["failed"] == [] and d["draw_tests"]["cases"] > 500
    # every fact the bases can compare raw is perturbed and named alone; the ones they cannot are
    # exactly these (the checker-logic re-review's NIT 10)
    st = d["self_test"]
    assert st["failed"] == [] and st["never_compared_raw_on_the_self_test_bases"] == NEVER_RAW
    # every vertex-input fact (the count, 16 attributes of three, the binding count, four bindings of
    # four, 16 locations) perturbed and named on a base with the null stream (the rule NULL_STREAM)
    assert st["null_stream_base_perturbed"] == 1 + 16 * 3 + 1 + 4 * 4 + 16
    # the vertex input by the shader's signature (U9): reordered, an input the declaration lacks (the
    # null stream, once or fifteen times), an element unread, a semantic declared twice (the last
    # element wins), a usage index DXVK's packed semantic aliases: compared, never DIFFERS; read from an
    # UNUSED element, POSITIONT, more than 16 inputs: unmapped; read from another stream (stream 1, its
    # stride and frequency in the input since SetStreamSourceFreq's instancing): compared
    vi = {v: o for o, vs in dom["classification"]["vertex input: declaration x signature"].items() for v in vs}
    assert all("DIFFERS" not in o for o in vi.values()), vi
    for case in ("census x NTPC", "census x PN", "census x CTPNW", "census x P + 15 texcoords", "colour twice x TCP",
                 "texcoord16 x PT1", "colour on stream 1 x PN", "colour on stream 1 x CP", "unused colour x PP"):
        assert vi[case] == "equal by rule", (case, vi[case])
    for case in ("census x P", "census x none", "colour twice x CP", "colour on stream 1 x P", "unused colour x P"):
        assert vi[case] == "equal", (case, vi[case])
    for case in ("census x more than 16 inputs", "unused colour x TCP", "positiont x P"):
        assert vi[case] == "unmapped", (case, vi[case])
    # stream 1 per vertex and per instance at divisor 1 (SDL's instance rate): compared, never DIFFERS;
    # INSTANCEDATA with divisor 2, which SDL cannot step by: unmapped
    sf = {v: o for o, vs in dom["classification"]["stream 1 frequency"].items() for v in vs}
    assert set(sf) == {"0x1", "0x2", "0x80000001", "0x80000002"} and all("DIFFERS" not in o for o in sf.values()), sf
    assert sf["0x80000002"] == "unmapped" and all(sf[v] != "unmapped" for v in ("0x1", "0x2", "0x80000001")), sf
    assert "an instanced stream with divisor 2 (SDL_gpu steps per instance by one)" in unm
    assert "a D3DDECLTYPE_UNUSED element the vertex shader reads" in unm and "a vertex shader with more than 16 inputs" in unm
    # the sampler facts on a plain sampler, again on a mapped BORDER one where the device has
    # CLAMP_TO_BORDER (with the BORDER_SWIZZLE rule's two DXVK-side clauses where DXVK chains the
    # mapping), and on a fallback BORDER one on every device, which absorbs the three address
    # modes, the colour and its two chains and names every other fact (the I6 reviews' finding 1)
    assert st["perturbed"] == st["facts"] - len(NEVER_RAW)
    assert st["sampler_perturbed"] == st["sampler_facts"] == 19
    assert st["border_sampler_perturbed"] == (st["sampler_facts"] if AXIS_CAPS[axis] else 0)
    assert st["border_swizzle_clauses_perturbed"] == (2 if axis == "nvidia-like" else 0)
    assert (st["fallback_sampler_perturbed"], st["fallback_sampler_absorbed"]) == (st["sampler_facts"] - 6, 6)
    assert d["audit"]["unclassified"] == [] and d["audit"]["magic_missing"] == []
    # BORDER (D3DTADDRESS_BORDER, 4): the patch's CLAMP_TO_BORDER where the axis's device has it,
    # equal by the BORDER_SWIZZLE rule where DXVK chains the mapping (NVIDIA-like: borderColorSwizzle
    # without FromImage) and raw where it does not (AMD-like: FromImage), the fallback elsewhere,
    # unmapped only beside another axis's MIRRORONCE, never DIFFERS
    mapped_as = {"nvidia-like": "equal by rule BORDER_SWIZZLE", "amd-like": "equal", "intel-like": None}[axis]
    for s in ("D3DSAMP_ADDRESSU", "D3DSAMP_ADDRESSV", "D3DSAMP_ADDRESSW"):
        border = [o.split("+") for o, values in dom["classification"][s].items() if "0x4" in values]
        assert len(border) == 1 and "fallback BORDER" in border[0] and "DIFFERS" not in border[0], (s, border)
        assert set(border[0]) == {"fallback BORDER", "unmapped"} | ({mapped_as} if mapped_as else set()), (s, border)
    by_tex = dom["classification"]["sampler texture with a BORDER axis"]
    outcomes = {t: set(o.split("+")) for o, ts in by_tex.items() for t in ts}
    # every format d9g makes is swept (d9g_format.h all_formats), and none
    assert set(outcomes) == {*SWIZZLED_VIEW_TEXTURES, *IDENTITY_VIEW_TEXTURES}, sorted(outcomes)
    # an X8 texture's view swizzles A to 1 in DXVK, and an A8 texture's is (0,0,0,R): never mapped,
    # never equal by the rule (the plan's B1)
    for t in SWIZZLED_VIEW_TEXTURES:
        assert outcomes[t] == {"fallback BORDER", "unmapped"}, (t, outcomes)
    if mapped_as:
        for t in IDENTITY_VIEW_TEXTURES:
            assert mapped_as in outcomes[t], (t, outcomes)
        assert dom["sampler_border_mapped"] > 0
    else:
        assert all(o <= {"fallback BORDER", "unmapped"} for o in outcomes.values()) and dom["sampler_border_mapped"] == 0
    # a custom colour is the fallback on every axis; with depth compare DXVK matches red alone, so
    # 0x00ffffff (white, alpha 0) is opaque white there and the fallback without it
    by_colour = {c: set(o.split("+")) for o, cs in dom["classification"]["D3DSAMP_BORDERCOLOR with a BORDER axis"].items()
                 for c in cs}
    assert by_colour["0x80402010"] == {"fallback BORDER", "unmapped"} and by_colour["0xffffff"] == {"fallback BORDER", "unmapped"}
    if mapped_as:
        assert mapped_as in by_colour["0xffffff depth"] and mapped_as in by_colour["0x0"], by_colour
    # BORDER mapped on X8 (map-border-swizzled-view): DIFFERS on every capable axis by the reason
    # the check decides from DXVK's sampler key, and where DXVK chains its mapping (NVIDIA-like) by
    # the BORDER_SWIZZLE rule's identity clause too (the I6 reviews: neither d9g's own X8 rule nor
    # the rule's clause may be the only thing that sees it)
    if mapped_as:
        out = tmp_path / "swizzled.json"
        r = check(build, "--params", params[axis], "--domain", "--fault", "map-border-swizzled-view", "--out", out)
        assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL", r.stdout[-2000:]
        sw = json.loads(out.read_text())["domain"]
        assert sw["wrong_reasons"] == [SWIZZLED_REASON], sw["wrong_reasons"]
        assert ("sm_pNextBorderSwizzle" in sw["differing_fields"]) == (axis == "nvidia-like"), sw["differing_fields"]
        sw_tex = {t: set(o.split("+")) for o, ts in sw["classification"]["sampler texture with a BORDER axis"].items() for t in ts}
        assert all("DIFFERS" in sw_tex[t] for t in SWIZZLED_VIEW_TEXTURES), sw_tex
        assert all("DIFFERS" not in sw_tex[t] for t in IDENTITY_VIEW_TEXTURES), sw_tex
        # and every standard colour d9g refuses where the patch builds it (map-border-fallback)
        r = check(build, "--params", params[axis], "--domain", "--fault", "map-border-fallback", "--out", out)
        fb = json.loads(out.read_text())["domain"]
        assert r.returncode == 1 and fb["wrong_reasons"] == [FALLBACK_REASON] and fb["differing_fields"] == [], fb["wrong_reasons"]


def test_a_run_in_which_no_mode_ran_is_no_pass(build, params):
    r = check(build, "--params", params["nvidia-like"])
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL" and "no mode ran" in r.stdout


def test_depth_clamp_is_the_patch_s_with_the_capability_and_the_residue_without(build, params, session, session_clamp_off,
                                                                               tmp_path):
    # with depth clip and clamp: every key sets the property, SDL builds DXVK's clamp with clip,
    # and no tuple is DEPTH_CLAMP (ruling 3's patch)
    out = tmp_path / "d.json"
    keys = session / "d3d9gpu.keys.json"
    check(build, "--params", params["nvidia-like"], "--domain", "--dumps", keys, "--shader-record",
          device_record(keys, tmp_path / "rec.json"), "--out", out)
    d = json.loads(out.read_text())
    for label, outs in d["domain"]["classification"].items():
        for outcome in outs:
            assert "DIFFERS" not in outcome, (label, outcome)
    s = d["census"]["streams"]["session"]
    assert (s["depth_clamp"], s["clamp_with_clip_missing"]) == (0, 0) and "residue DEPTH_CLAMP" not in s["fact_classes"]
    assert s["depth_clamp_property"] == s["pipeline_tuples"] - s["unmapped"] == 4
    # without it: every mapped tuple carries R_DEPTH_CLAMP, the residue class (the shader route's)
    out = tmp_path / "r.json"
    keys = session_clamp_off / "d3d9gpu.keys.json"
    check(build, "--params", caps_params(params["nvidia-like"], tmp_path / "p.json", True, False), "--dumps", keys,
          "--shader-record", device_record(keys, tmp_path / "rec-off.json"), "--out", out)
    s = json.loads(out.read_text())["census"]["streams"]["session"]
    assert s["depth_clamp"] == s["pipeline_tuples"] - s["unmapped"] == 4 and s["fact_classes"]["residue DEPTH_CLAMP"] == 4
    assert s["depth_clamp_property"] == 0


# ------------------------------------------------------------------ the CPU-model session
def make_session(build: Path, tmp_path_factory, caps: str) -> Path:
    """The CPU-model session (test_d3d9gpu --pipeline-session) with the SDL patch's capabilities
    `caps` (its keys file's stream is named "session")."""
    d = tmp_path_factory.mktemp("session") / "private" / "session"
    d.mkdir(parents=True)
    r = run([build / "test_d3d9gpu", "--pipeline-session", d / "d3d9gpu.json", f"--caps={caps}"])
    assert r.returncode == 0, r.stdout + r.stderr
    return d


@pytest.fixture(scope="session")
def session(build, tmp_path_factory) -> Path:
    """Both capabilities, as the patched SDL3 here reports them."""
    return make_session(build, tmp_path_factory, "border,depth-clamp")


@pytest.fixture(scope="session")
def session_clamp_off(build, tmp_path_factory) -> Path:
    """CLAMP_TO_BORDER without depth clip and clamp: the DEPTH_CLAMP residue's session."""
    return make_session(build, tmp_path_factory, "border")


@pytest.fixture(scope="session")
def session_stock(build, tmp_path_factory) -> Path:
    """Neither capability, as a stock SDL3 reports them."""
    return make_session(build, tmp_path_factory, "none")


def device_record(keys: Path, out: Path, sampler_mask: int | None = None, rt_mask: int | None = None, agree: bool = True,
                  signatures: dict[int, list] | None = None) -> Path:
    """A shader record (shader_record.py's schema) for a keys file's shaders, made from the device's own
    reflection in it (the keys file's "shaders"): each pixel shader with its masks, or these; each vertex
    shader with its input signature, or signatures[i] for the vertex shader of index i (creation id - 1)."""
    k = json.loads(keys.read_text())
    sh = k["shaders"]
    out.write_text(json.dumps({"schema": "sa2-d3d9gpu-shader-record/v2", "agree": agree,
                               "ps": [{"label": f"ps{i}", "sha256": h,
                                       "sampler_mask": m[0] if sampler_mask is None else sampler_mask,
                                       "rt_mask": m[1] if rt_mask is None else rt_mask}
                                      for i, (h, m) in enumerate(zip(sh["ps"], sh["ps_masks"]))],
                               "vs": [{"label": f"vs{i}", "sha256": h, "input_signature": (signatures or {}).get(i, sig)}
                                      for i, (h, sig) in enumerate(zip(sh["vs"], sh["vs_input_signature"]))]}))
    return out


def test_the_session_s_report_and_keys_file_have_their_shape(session, session_stock, build, tmp_path):
    rep = json.loads((session / "d3d9gpu.json").read_text())
    keys = json.loads((session / "d3d9gpu.keys.json").read_text())
    p = rep["pipeline"]
    assert rep["schema"] == "sa2-d3d9gpu-report/v1" and rep["complete"] and rep["not_implemented"]["count"] == 0
    assert (p["draws_mapped"], p["distinct_inputs"], p["distinct_keys"]) == (6, 4, 4)
    # with both capabilities: the patch's BORDER and depth clamp, nothing owed
    assert p["patch"] == {"border_samplers": 1, "depth_clamp_keys": 4}
    assert p["fallback_owed"] == {"border_samplers": 0, "border_stage_draws": 0, "depth_clamp_keys": 0}
    assert p["undecided"] == {"rt_feedback_keys": 0}
    sg = rep["sdl_gpu"]
    assert (sg["sampler_clamp_to_border"], sg["depth_clip_and_clamp"], sg["capability_override"]) == (True, True, None)
    assert sg["detected"] == {"sampler_clamp_to_border": True, "depth_clip_and_clamp": True}
    assert sg["fallback_owed"] == {"border": False, "depth_clamp": False}
    assert keys["device"]["sampler_clamp_to_border"] and keys["device"]["depth_clip_and_clamp"]
    assert keys["device"]["capability_override"] is None
    assert [(r["fallback_border"], r["border_colour"]) for r in keys["samplers"] if r["tex"]] == [(False, 0)]
    # with neither: the shader route's obligations
    srep = json.loads((session_stock / "d3d9gpu.json").read_text())
    assert srep["pipeline"]["patch"] == {"border_samplers": 0, "depth_clamp_keys": 0}
    assert srep["pipeline"]["fallback_owed"] == {"border_samplers": 1, "border_stage_draws": 3, "depth_clamp_keys": 4}
    assert srep["sdl_gpu"]["fallback_owed"] == {"border": True, "depth_clamp": True}
    skeys = json.loads((session_stock / "d3d9gpu.keys.json").read_text())
    assert [(r["fallback_border"], r["border_colour"]) for r in skeys["samplers"] if r["tex"]] == [(True, -1)]
    # a name the CPU model's capabilities do not have is refused
    r = run([build / "test_d3d9gpu", "--pipeline-session", tmp_path / "private" / "x.json", "--caps=border,stencil"])
    assert r.returncode == 2 and "stencil" in r.stderr
    assert p["unmapped"] == {"inputs": 0, "samplers": 0, "values": []}
    assert p["keys_file"] == str(session / "d3d9gpu.keys.json") and p["keys_refused"] is None
    assert p["keys_sha256"] == hashlib.sha256((session / "d3d9gpu.keys.json").read_bytes()).hexdigest()
    assert keys["schema"] == "sa2-d3d9gpu-keys/v3" and keys["complete"] and keys["map_faults"] == 0
    # the shaders' reflection, the device's own (U9): each created shader's, and each tuple's
    assert (keys["shaders"]["vs_input_signature"], keys["shaders"]["ps_masks"]) == (SESSION_VS, SESSION_PS)
    assert [(r["vs"], r["vs_inputs"], r["vs_inputs_overflow"], r["ps_sampler_mask"], r["ps_rt_mask"]) for r in keys["inputs"]] == \
        [(1, SESSION_VS[0], False, 0, 1)] * 3 + [(2, SESSION_VS[1], False, 0, 1)]
    # the keys file names the device it was made on: the CPU model's own name (the fold's
    # checker-logic finding 1), which a sweep axis's parameters may check
    assert keys["device"]["device_name"] == "d9g CPU model"
    assert len(keys["inputs"]) == keys["counts"]["inputs"] == p["distinct_inputs"]
    assert len({r["key"] for r in keys["inputs"]}) == keys["counts"]["keys"] == p["distinct_keys"]
    assert sum(r["draws"] for r in keys["inputs"]) == p["draws_mapped"]
    assert len(keys["samplers"]) == p["distinct_samplers"]
    assert rep["uploads"]["up_vertex_bytes"] == 6 * 4 * 24 and rep["uploads"]["up_index_bytes"] == 12


def test_a_mapping_fault_reaches_the_report_s_fault_field(build, params, tmp_path):
    d = tmp_path / "private"
    d.mkdir()
    r = run([build / "test_d3d9gpu", "--pipeline-session", d / "f.json", "map-blend-swap"])
    assert r.returncode == 0
    rep = json.loads((d / "f.json").read_text())
    assert rep["fault"] == "map-blend-swap" and rep["pipeline"]["map_faults"] == "map-blend-swap"
    assert json.loads((d / "f.keys.json").read_text())["map_faults"] != 0
    # a keys file made under a mapping fault is a negative control's run, never a verdict's input
    r = check(build, "--params", params["nvidia-like"], "--dumps", d / "f.keys.json")
    assert r.returncode == 1 and "made under mapping fault" in r.stdout, r.stdout[-2000:]


def test_a_census_holds_the_device_s_reflection_to_the_record(build, params, session, tmp_path):
    keys = session / "d3d9gpu.keys.json"
    nv = ["--params", params["nvidia-like"]]
    # no census without the record: the device's reflection would be held to nothing
    r = check(build, *nv, "--dumps", keys)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL" and "a census needs --shader-record" in r.stdout
    # the record equal to the device's own: a pass, the null stream equal by its rule, no VS_SIGNATURE
    out = tmp_path / "v.json"
    r = check(build, *nv, "--dumps", keys, "--shader-record", device_record(keys, tmp_path / "own.json"), "--out", out)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK", r.stdout[-3000:]
    assert "VS_SIGNATURE" not in r.stdout + out.read_text()
    d = json.loads(out.read_text())
    s = d["census"]["streams"]["session"]
    assert (s["pipeline_tuples"], s["equal"], s["equal_by_rule"], s["undecided"], s["null_stream"]) == (4, 2, 2, 0, 1)
    assert (s["tuples_with_recorded_vs_signature"], s["reflection_differs"], s["shaders_held_to_the_record"]) == (4, 0, 3)
    # the normal kVsReorder reads: its attribute's binding, its binding's number and its divisor
    assert s["fact_classes"]["rule NULL_STREAM"] == 3 and s["fact_classes"]["rule BGRA"] == 1
    # the stand-ins left are varied alone, and the facts each decides are named (the review's finding 4)
    by = s["facts_decided_by_each_standin"]
    assert set(by) == STANDINS and by["fs_sample_rate_shading"] == {"ms_minSampleShading": 4, "ms_sampleShadingEnable": 4}
    assert s["standin_decided"] == 4 and by["fs_exports_sample_mask"] == {}
    # a record whose signature is not the device's: the shader fails, every tuple drawing it fails, and
    # DXVK, evaluated with the record, builds another vertex input there
    rec = device_record(keys, tmp_path / "sig.json", signatures={1: [[0, 0], [10, 0], [3, 0]]})
    r = check(build, *nv, "--dumps", keys, "--shader-record", rec)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL"
    assert ("session vertex shader 2: the device's input signature [[10, 0], [0, 0], [3, 0]] is not the recorded "
            "[[0, 0], [10, 0], [3, 0]] of vs1") in r.stdout, r.stdout[-3000:]
    assert "session pipeline tuple 3: the device's reflection (signature [[10, 0], [0, 0], [3, 0]]" in r.stdout
    assert "session pipeline tuple 3: vi_attr0_format" in r.stdout
    # the pixel shader's masks, the same
    r = check(build, *nv, "--dumps", keys, "--shader-record", device_record(keys, tmp_path / "ps.json", sampler_mask=0x20))
    assert r.returncode == 1 and ("session pixel shader 1: the device's sampler and output masks 0x0, 0x1 are not the "
                                  "recorded 0x20, 0x1 of ps0") in r.stdout, r.stdout[-3000:]
    # a tuple whose signature is not its vertex shader's (an edited keys file): refused by name
    k = json.loads(keys.read_text())
    k["inputs"][3]["vs_inputs"] = [[0, 0]]
    (tmp_path / "edited").mkdir()
    (tmp_path / "edited" / "d3d9gpu.keys.json").write_text(json.dumps(k))
    r = check(build, *nv, "--dumps", tmp_path / "edited" / "d3d9gpu.keys.json", "--shader-record", device_record(keys, tmp_path / "e.json"))
    assert r.returncode == 1 and "edited pipeline tuple 3: its vertex shader's signature is not the keys file's for vertex shader 2" \
        in r.stdout, r.stdout[-3000:]
    # a keys file before U9's (v2: no reflection) is refused
    k = json.loads(keys.read_text())
    k["schema"] = "sa2-d3d9gpu-keys/v2"
    (tmp_path / "v2").mkdir()
    (tmp_path / "v2" / "d3d9gpu.keys.json").write_text(json.dumps(k))
    r = check(build, *nv, "--dumps", tmp_path / "v2" / "d3d9gpu.keys.json", "--shader-record", device_record(keys, tmp_path / "v2.json"))
    assert r.returncode == 1 and "not sa2-d3d9gpu-keys/v3" in r.stdout


@pytest.mark.parametrize("stage,field", [("ps", "sampler_mask"), ("ps", "rt_mask"), ("vs", "input_signature")])
@pytest.mark.parametrize("conflict_first", [False, True])
def test_conflicting_shader_reflections_are_refused_in_either_order(build, params, session, tmp_path, stage, field,
                                                                  conflict_first):
    keys = session / "d3d9gpu.keys.json"
    rec = device_record(keys, tmp_path / "record.json")
    record = json.loads(rec.read_text())
    duplicate = json.loads(json.dumps(record[stage][-1]))
    duplicate["label"] = "another creation of the same bytecode"
    if field == "input_signature":
        duplicate[field].reverse()
    else:
        duplicate[field] ^= 1
    record[stage].insert(0 if conflict_first else len(record[stage]), duplicate)
    rec.write_text(json.dumps(record))
    r = check(build, "--params", params["nvidia-like"], "--dumps", keys, "--shader-record", rec)
    kind = "vertex" if stage == "vs" else "pixel"
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL", r.stdout[-3000:]
    assert f"conflicting {kind}-shader reflection for bytecode {duplicate['sha256']}" in r.stdout


@pytest.mark.parametrize("stage", ["ps", "vs"])
@pytest.mark.parametrize("duplicate_first", [False, True])
def test_consistent_shader_reflections_with_different_labels_pass(build, params, session, tmp_path, stage, duplicate_first):
    keys = session / "d3d9gpu.keys.json"
    rec = device_record(keys, tmp_path / "record.json")
    record = json.loads(rec.read_text())
    duplicate = dict(record[stage][-1], label="another creation of the same bytecode")
    record[stage].insert(0 if duplicate_first else len(record[stage]), duplicate)
    rec.write_text(json.dumps(record))
    out = tmp_path / "state-check.json"
    r = check(build, "--params", params["nvidia-like"], "--dumps", keys, "--shader-record", rec, "--out", out)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK", r.stdout[-3000:]
    result = json.loads(out.read_text())
    assert result["failures"] == [] and result["census"]["shaders_held_to_the_record"] == 3


@pytest.mark.parametrize("stage", ["vs", "ps"])
def test_every_created_shader_needs_an_independent_record(build, params, session, tmp_path, stage):
    keys = session / "d3d9gpu.keys.json"
    original_record = device_record(keys, tmp_path / "record.json")
    k = json.loads(keys.read_text())
    k["shaders"][stage].append("f" * 64)
    field, reflection = ("vs_input_signature", [[0, 0]]) if stage == "vs" else ("ps_masks", [0, 1])
    k["shaders"][field].append(reflection)
    changed = tmp_path / "d3d9gpu.keys.json"
    changed.write_text(json.dumps(k))
    r = check(build, "--params", params["nvidia-like"], "--dumps", changed, "--shader-record", original_record)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL", r.stdout[-3000:]
    kind = "vertex" if stage == "vs" else "pixel"
    assert f"{kind} shader {len(k['shaders'][stage])}: no independent recorded reflection" in r.stdout


@pytest.mark.parametrize("where", ["tuple", "created", "record"])
@pytest.mark.parametrize("component,value,why", [
    (0, 266, "outside a byte"), (1, 256, "outside a byte"),
    (0, 4294967306, "outside a byte"),
    (0, 10.5, "not an unsigned integer"), (1, 0.5, "not an unsigned integer"),
    (1, False, "not an unsigned integer"), (1, "0", "not an unsigned integer"),
])
def test_signature_readings_cannot_wrap_at_a_byte(build, params, session, tmp_path, where, component, value, why):
    keys = session / "d3d9gpu.keys.json"
    rec = device_record(keys, tmp_path / "record.json")
    k = json.loads(keys.read_text())
    if where == "tuple":
        k["inputs"][3]["vs_inputs"][0][component] = value
    elif where == "created":
        k["shaders"]["vs_input_signature"][1][0][component] = value
    else:
        recorded = json.loads(rec.read_text())
        recorded["vs"][1]["input_signature"][0][component] = value
        rec.write_text(json.dumps(recorded))
    changed = tmp_path / "d3d9gpu.keys.json"
    changed.write_text(json.dumps(k))
    r = check(build, "--params", params["nvidia-like"], "--dumps", changed, "--shader-record", rec)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL", r.stdout[-3000:]
    assert "signature usage or index is " + why in r.stdout


def test_the_session_passes_with_both_capabilities_and_every_control_fails_by_its_field(build, params, session, tmp_path):
    out = tmp_path / "c.json"
    rec = device_record(session / "d3d9gpu.keys.json", tmp_path / "record.json")
    r = check(build, "--params", params["nvidia-like"], "--dumps", session / "d3d9gpu.keys.json", "--shader-record", rec,
              "--controls", "--out", out)
    lines = r.stdout.splitlines()
    # both capabilities, the shaders' reflection recorded: nothing pending, nothing owed
    assert lines[-1] == "STATE_CHECK_OK" and r.returncode == 0, r.stdout[-3000:]
    d = json.loads(out.read_text())
    assert d["verdict"] == "pass" and d["failures"] == [] and d["fallback_owed"] == [] and d["test_leg"] is None
    got = {c["name"]: c["outcome"] for c in d["controls"]}
    assert set(got) == set(CONTROLS)
    for name, outcome in got.items():
        assert "fails as required" in outcome and "PASSES" not in outcome and "OTHER" not in outcome, (name, outcome)
    # the census exercises the blend, BGRA, fan and BORDER rules of this session itself, the
    # vertex-input controls among them (they must fail the census: the checker-logic re-review's
    # finding 2; U9's five on kVsReorder's tuple), the obligations by name (finding 3), and phase B's
    # three: a wrong border colour, the depth-clamp property dropped, the depth bias pushed
    for name in ("map-blend-swap", "map-bgra-mask", "map-fan-order", "map-border-clamp", "map-depth-clip-off",
                 "map-fan-on-list", "map-border-colour", "map-depth-clamp-off", "map-depth-bias-push", "map-depth-clamp-extra",
                 "map-border-fallback", *VERTEX_INPUT_CONTROLS):
        assert got[name].startswith("fails as required (census)"), (name, got[name])
    # the session has no X8 BORDER sampler, and no render target bound as a texture: the sweep, with
    # the session's capabilities, fails those
    assert got["map-border-swizzled-view"] == "not exercised by census; fails as required (domain sweep)"
    assert got["map-ps-sampler-superset"] == "not exercised by census; fails as required (domain sweep)"
    # U9's: the declaration-order stand-in, no null stream, the null stream per vertex, an unread
    # element given an attribute, BGRA by the declaration's index, each on kVsReorder's tuple (the
    # declaration-order stand-in and the unread attribute on kVs's too: it reads the position alone)
    cs = {c["name"]: c for c in d["controls"]}
    assert {n: cs[n]["census_tuples_failed"] for n in ("map-vi-declorder", "map-vi-no-null", "map-null-vertex-rate",
                                                         "map-vi-unread-attr", "map-bgra-decl-index")} == \
        {"map-vi-declorder": 4, "map-vi-no-null": 1, "map-null-vertex-rate": 1, "map-vi-unread-attr": 4, "map-bgra-decl-index": 1}
    assert cs["map-null-vertex-rate"]["fields"] == "vi_bind1_inputRate"
    assert "vi_attr2_location" in cs["map-vi-no-null"]["fields"] and "obligation BGRA" in cs["map-bgra-decl-index"]["fields"]
    fields = {c["name"]: c["fields"] for c in d["controls"]}
    assert "obligation BGRA" in fields["map-bgra-extra"] and "obligation FAN" in fields["map-fan-on-list"]
    assert fields["map-border-clamp"].startswith("reason: a BORDER axis mapped to another address mode, neither "
                                                 "CLAMP_TO_BORDER nor the shader route's fallback"), fields["map-border-clamp"]
    assert fields["map-border-colour"] == "sm_borderColor, sm_pNextBorderSwizzle"
    assert fields["map-depth-clamp-off"] == "rs_depthClampEnable"
    assert fields["map-depth-clamp-extra"] == "obligation DEPTH_CLAMP, rs_depthClampEnable"
    assert fields["map-border-fallback"] == "reason: " + FALLBACK_REASON
    # on this NVIDIA-like axis the X8 and A8 cells also break the BORDER_SWIZZLE rule's identity
    # clause, and A8's swizzle turns DXVK's standard colour into a custom one, which DXVK chains
    assert fields["map-border-swizzled-view"] == (f"reason: {SWIZZLED_REASON}, sm_borderColor, sm_pNextBorderSwizzle, "
                                                  "sm_pNextCustomBorder")
    assert "rs_depthBiasEnable" in fields["map-depth-bias-push"]
    s = d["census"]["streams"]["session"]
    assert s["pipeline_tuples"] == 4 and s["depth_clamp"] == 0 and s["depth_clamp_property"] == 4 and s["differ"] == 0
    # the BORDER sampler is the patch's CLAMP_TO_BORDER, equal by the BORDER_SWIZZLE rule on this
    # axis (NVIDIA-like: DXVK chains the identity mapping)
    assert (s["sampler_border_mapped"], s["sampler_equal_by_rule"], s["sampler_fallback_border"]) == (1, 1, 0)
    assert s["fact_classes"].get("rule BORDER_SWIZZLE") is None   # a sampler class, not a pipeline fact's
    assert s["null_stream"] == 1 and s["equal"] + s["equal_by_rule"] == 4
    # the classes are disjoint (the checker-logic re-review's finding 7)
    assert s["pipeline_tuples"] == s["equal"] + s["equal_by_rule"] + s["undecided"] + s["unmapped"] + s["differ"]
    assert s["sampler_tuples"] == (s["sampler_equal"] + s["sampler_equal_by_rule"] + s["sampler_equal_history"] +
                                   s["sampler_fallback_border"] + s["sampler_unmapped"] + s["sampler_differ"])
    # the stage with no texture: its compare bit is the last texture's (history), never counted equal
    assert (s["sampler_equal"], s["sampler_equal_history"]) == (0, 1)
    assert d["census"]["reads_outside_the_keys_file"] == []


def test_a_fault_fails_the_census_by_its_field(build, params, session, tmp_path):
    keys = session / "d3d9gpu.keys.json"
    base = ["--params", params["nvidia-like"], "--dumps", keys, "--shader-record", device_record(keys, tmp_path / "rec.json")]
    r = check(build, *base, "--fault", "map-zfunc-swap")
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL"
    assert "ds_depthCompareOp" in r.stdout
    # an obligation that fails is named, not shown as two equal values
    r = check(build, *base, "--fault", "map-bgra-extra")
    assert r.returncode == 1 and "the BGRA obligation d9g's description carries does not hold" in r.stdout
    # R2b's declaration-order stand-in, back in d9g: kVsReorder's tuple and kVs's differ from DXVK's
    r = check(build, *base, "--fault", "map-vi-declorder")
    assert r.returncode == 1 and "session pipeline tuple 3: vi_attr0_format" in r.stdout, r.stdout[-2000:]
    # R_DEPTH_CLAMP on a device with depth clip and clamp is one, whatever the ruling (none here):
    # never the residue's pending verdict (the I6 census review's finding 2)
    r = check(build, *base, "--fault", "map-depth-clamp-extra")
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL", r.stdout[-2000:]
    assert "the DEPTH_CLAMP obligation d9g's description carries does not hold" in r.stdout


def test_the_keys_files_are_held_to_the_device_binary(build, params, session, tmp_path):
    keys = session / "d3d9gpu.keys.json"
    r = check(build, "--params", params["nvidia-like"], "--dumps", keys, "--shader-record", device_record(keys, tmp_path / "r.json"),
              "--device-binary", build / "test_d3d9gpu")
    assert r.returncode == 1 and "not --device-binary" in r.stdout, r.stdout[-2000:]


def git(repo: Path, *args) -> None:
    r = run(["git", "-C", repo, "-c", "user.name=test", "-c", "user.email=test@example.invalid", *args])
    assert r.returncode == 0, r.stderr


@pytest.fixture(scope="session")
def ruling_check(build, tmp_path_factory) -> tuple[Path, Path]:
    """The state check built again from a scratch repository's copy of state_check.cpp (never the
    project's checkout): that repository is then the checkout it was built from, the one place it
    takes a ruling. Its other units and the DXVK objects are the build's."""
    repo = tmp_path_factory.mktemp("ruling") / "repo"
    src = repo / "host" / "d3d9gpu" / "test" / "state_check.cpp"
    src.parent.mkdir(parents=True)
    shutil.copy(TEST / "state_check.cpp", src)
    git(repo, "init", "-q")
    gen = build / "d3d9gpu-gen"
    obj = gen / "refs" / "obj"
    r = run(["g++", "-std=c++17", "-O0", "-w", f"-I{gen}", f"-I{D9G}", f"-I{ROOT / 'host' / 'player'}",
             f"-I{INSTALL / 'include' / 'dxvk'}", f"-I{INSTALL / 'include'}", f"-I{TEST}", "-o", repo / "check", src,
             *(D9G / f for f in ("d9g_pipeline.cpp", "d9g_sampler.cpp", "d9g_props.cpp", "d9g_draw.cpp", "d9g_format.cpp")),
             ROOT / "host" / "player" / "sha256.cpp",
             *(obj / f"{o}.o" for o in ("ref_dxvk", "ref_link_shims", "ref_sdl", "d3d9_util", "d3d9_format",
                                        "dxvk_constant_state", "dxvk_format", "dxvk_util", "vulkan_names")),
             "-Wl,--gc-sections", "-ldl"], timeout=1800)
    assert r.returncode == 0, r.stderr[-3000:]
    return repo, repo / "check"


def commit_ruling(repo: Path, name: str, ruling: str, decided_by: str = "user",
                  decision: str = "The user rules on DEPTH_CLAMP.\n") -> Path:
    d = repo / "Documentation" / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.md").write_text(decision)
    f = d / f"{name}.json"
    f.write_text(json.dumps({"schema": "sa2-residue-ruling/v1", "residue": "DEPTH_CLAMP", "ruling": ruling,
                             "decided_by": decided_by, "decision": f"Documentation/decisions/{name}.md"}))
    git(repo, "add", "Documentation/decisions")
    git(repo, "commit", "-q", "-m", name)
    return f


def test_only_a_committed_user_ruling_of_the_check_s_own_checkout_moves_the_pending_verdict(
        build, params, session, session_clamp_off, ruling_check, tmp_path):
    repo, rcheck = ruling_check
    # the DEPTH_CLAMP residue lives on a device without depth clip and clamp (ruling 3's fallback
    # devices): the session without it, BORDER mapped
    keys = session_clamp_off / "d3d9gpu.keys.json"
    nv = caps_params(params["nvidia-like"], tmp_path / "clamp-off.json", True, False)
    base = ["--params", nv, "--dumps", keys, "--shader-record", device_record(keys, tmp_path / "rec.json")]

    def rc(*args) -> subprocess.CompletedProcess:
        return run([rcheck, *base, *args], timeout=1800)

    r = check(build, *base, "--expect-depth-clamp-residue")
    assert r.returncode == 2   # no such flag: an OK under a flag would loosen the marker (M8)
    r = rc()
    assert r.returncode == 3 and r.stdout.splitlines()[-1] == "STATE_CHECK_PENDING_USER DEPTH_CLAMP", r.stdout[-2000:]
    # committed, read as committed: the verdict moves, and the result names the file, its hash and commit
    f = commit_ruling(repo, "residue", "residue")
    head = run(["git", "-C", repo, "rev-parse", "HEAD"]).stdout.strip()
    out = tmp_path / "ok.json"
    r = rc("--residue-ruling", f, "--out", out)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK", r.stdout[-2000:]
    d = json.loads(out.read_text())
    rf = d["ruling_file"]
    assert d["ruling"] == "residue" and rf["path"] == "Documentation/decisions/residue.json"
    assert rf["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest() and rf["commit"] == head == rf["head"]
    assert rf["repository"] == str(repo.resolve()) + "/" and rf["decision"]["path"] == "Documentation/decisions/residue.md"
    # --repo may only name that checkout (the checker-logic re-review's finding 4)
    r = rc("--residue-ruling", f, "--repo", ROOT)
    assert r.returncode == 1 and "is not the checkout this check was built from" in r.stdout
    # another checkout's ruling is refused by the project's build of the check
    r = check(build, *base, "--residue-ruling", f, "--repo", repo)
    assert r.returncode == 1 and "is not the checkout this check was built from" in r.stdout
    r = check(build, *base, "--residue-ruling", f)
    assert r.returncode == 1 and "is not under" in r.stdout
    # a ruling anywhere but a decision record is refused
    loose = tmp_path / "loose.json"
    loose.write_text(f.read_text())
    r = rc("--residue-ruling", loose)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL" and "is not under" in r.stdout
    # changed after its commit, also where an index flag hides the change from git diff
    committed = f.read_text()
    f.write_text(committed.replace('"residue", "decided_by"', '"shader", "decided_by"'))
    r = rc("--residue-ruling", f)
    assert r.returncode == 1 and "differs from its committed version" in r.stdout
    git(repo, "update-index", "--assume-unchanged", "Documentation/decisions/residue.json")
    r = rc("--residue-ruling", f)
    assert r.returncode == 1 and "differs from its committed version" in r.stdout
    git(repo, "update-index", "--no-assume-unchanged", "Documentation/decisions/residue.json")
    f.write_text(committed)
    # untracked; added but not committed
    untracked = repo / "Documentation" / "decisions" / "other.json"
    untracked.write_text(committed)
    r = rc("--residue-ruling", untracked)
    assert r.returncode == 1 and "is not tracked" in r.stdout
    git(repo, "add", "Documentation/decisions/other.json")
    r = rc("--residue-ruling", untracked)
    assert r.returncode == 1 and "has no committed version at HEAD" in r.stdout
    git(repo, "rm", "-q", "--cached", "Documentation/decisions/other.json")
    # tracked, but not a decision record
    outside = repo / "ruling.json"
    outside.write_text(committed)
    git(repo, "add", "ruling.json")
    git(repo, "commit", "-q", "-m", "outside")
    r = rc("--residue-ruling", outside)
    assert r.returncode == 1 and "is not under" in r.stdout
    # not the user's
    r = rc("--residue-ruling", commit_ruling(repo, "agent", "residue", decided_by="agent"))
    assert r.returncode == 1 and "not decided by the user" in r.stdout
    # a decision that does not name the residue
    r = rc("--residue-ruling", commit_ruling(repo, "unrelated", "residue", decision="Something else entirely.\n"))
    assert r.returncode == 1 and "does not name DEPTH_CLAMP" in r.stdout
    # "patch" (ruling 3): on this device, without depth clip and clamp, the R_DEPTH_CLAMP tuples are
    # the shader route's fallback, owed: never OK
    patch = commit_ruling(repo, "patch", "patch")
    out = tmp_path / "patch.json"
    r = rc("--residue-ruling", patch, "--out", out)
    assert r.returncode == 4 and r.stdout.splitlines()[-1] == "STATE_CHECK_FALLBACK_OWED DEPTH_CLAMP", r.stdout[-2000:]
    assert json.loads(out.read_text())["fallback_owed"] == ["DEPTH_CLAMP"]
    # on a device with it, SDL builds clamp with clip on every tuple and the ruling holds it...
    caps = ["--params", params["nvidia-like"], "--dumps", session / "d3d9gpu.keys.json", "--shader-record",
            device_record(session / "d3d9gpu.keys.json", tmp_path / "rec2.json")]
    r = run([rcheck, *caps, "--residue-ruling", patch], timeout=1800)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK", r.stdout[-2000:]
    # ...so the property dropped (map-depth-clamp-off) fails the run by its field and by the
    # ruling's own rule (the plan's B1)
    r = run([rcheck, *caps, "--residue-ruling", patch, "--fault", "map-depth-clamp-off"], timeout=1800)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL"
    assert "rs_depthClampEnable" in r.stdout and "SDL must build depthClampEnable with depth clip, and it does not" in r.stdout
    # "shader" moves it with the obligation named: R_DEPTH_CLAMP in every DEPTH_CLAMP tuple's key
    out = tmp_path / "shader.json"
    r = rc("--residue-ruling", commit_ruling(repo, "shader", "shader"), "--out", out)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK"
    ob = json.loads(out.read_text())["obligations"]
    assert ob[0]["key_bit"] == "R_DEPTH_CLAMP" and ob[0]["tuples"] == 4


# ------------------------------------------------------------------ the recorded reflection (finding 2)
def hazard_keys(session: Path, tmp_path: Path, sampler_mask: int = 0, name: str = "hazard") -> Path:
    """The session's keys file with its first input's render target made a texture bound at stage 5,
    and the pixel shader's reflection (the device's: the shader's and every tuple's) sampling
    `sampler_mask`."""
    keys = json.loads((session / "d3d9gpu.keys.json").read_text())
    rec = keys["inputs"][0]
    rec["rt"][0][7] = True
    rec["rt_tex_sampled"], rec["rt_tex_stages"] = 1, 0x20
    keys["shaders"]["ps_masks"] = [[sampler_mask, m[1]] for m in keys["shaders"]["ps_masks"]]
    for r in keys["inputs"]:
        r["ps_sampler_mask"] = sampler_mask
    p = tmp_path / name / "d3d9gpu.keys.json"
    p.parent.mkdir()
    p.write_text(json.dumps(keys))
    return p


def test_the_pixel_shader_s_reflection_decides_rt_feedback(build, params, session, tmp_path):
    keys = hazard_keys(session, tmp_path)
    base = ["--params", params["nvidia-like"], "--dumps", keys]
    # a pixel shader that samples nothing (the device's reflection and the record's): no hazard
    out = tmp_path / "none.json"
    r = check(build, *base, "--shader-record", device_record(keys, tmp_path / "r0.json"), "--out", out)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "STATE_CHECK_OK", r.stdout[-2000:]
    c = json.loads(out.read_text())["census"]
    assert (c["rt_feedback_tuples"], c["tuples_with_recorded_reflection"], c["reflection_differs_tuples"]) == (0, 4, 0)
    # R2b's superset stand-in back in d9g (map-ps-sampler-superset): the hazard tuple differs by it
    r = check(build, *base, "--shader-record", device_record(keys, tmp_path / "r0s.json"), "--fault", "map-ps-sampler-superset")
    assert r.returncode == 1 and "hazard pipeline tuple 0: rt_unresolvableHazard" in r.stdout, r.stdout[-2000:]
    # one that samples stage 5: the unresolvable hazard, the user's (the critic's M4)
    keys20 = hazard_keys(session, tmp_path, 0x20, "hazard20")
    out = tmp_path / "20.json"
    r = check(build, "--params", params["nvidia-like"], "--dumps", keys20, "--shader-record", device_record(keys20, tmp_path / "r20.json"),
              "--out", out)
    assert r.returncode == 3 and r.stdout.splitlines()[-1] == "STATE_CHECK_PENDING_USER RT_FEEDBACK", r.stdout[-2000:]
    assert "RT_FEEDBACK (a written render target's texture is bound at stages 0x20, sampler mask 0x20)" in r.stdout
    assert json.loads(out.read_text())["census"]["rt_feedback_tuples"] == 1
    # a device without the feedback-loop layout: rt_feedbackLoop is 0 on both sides, and the
    # hazard is still the user's (classified on d9g's decision, held to DXVK's: N10). The
    # nvidia-like axis with the feature off, since the keys file's device (the CPU model, D24S8)
    # must be the parameters' (the intel-like axis has no D24S8)
    nofb = json.loads(params["nvidia-like"].read_text())
    nofb["dxvk"]["features"]["extAttachmentFeedbackLoopLayout.attachmentFeedbackLoopLayout"] = False
    (tmp_path / "nofb-params.json").write_text(json.dumps(nofb))
    out = tmp_path / "nofb.json"
    r = check(build, "--params", tmp_path / "nofb-params.json", "--dumps", keys20, "--shader-record",
              device_record(keys20, tmp_path / "r20b.json"), "--out", out)
    assert r.returncode == 3 and r.stdout.splitlines()[-1] == "STATE_CHECK_PENDING_USER RT_FEEDBACK", r.stdout[-2000:]
    hz = json.loads(out.read_text())["census"]["streams"]["hazard20"]
    assert hz["fact_classes"]["equal"] > 0 and hz["rt_feedback"] == 1
    # the device's reflection held to the record's: the sampler mask, the output mask
    r = check(build, *base, "--shader-record", device_record(keys, tmp_path / "r20c.json", 0x20))
    assert r.returncode == 1 and "is not the record's" in r.stdout and "are not the recorded 0x20, 0x1" in r.stdout, r.stdout[-2000:]
    r = check(build, *base, "--shader-record", device_record(keys, tmp_path / "rt3.json", rt_mask=3))
    assert r.returncode == 1 and "are not the recorded 0x0, 0x3" in r.stdout
    r = check(build, *base, "--shader-record", device_record(keys, tmp_path / "dis.json", agree=False))
    assert r.returncode == 1 and "disagree" in r.stdout
    # a record that holds none of a tuple's shaders: every tuple must be linked (residual 5)
    other = json.loads(device_record(keys, tmp_path / "o.json").read_text())
    for i, x in enumerate(other["ps"] + other["vs"]):
        x["sha256"] = f"{i:064x}"
    (tmp_path / "o.json").write_text(json.dumps(other))
    r = check(build, *base, "--shader-record", tmp_path / "o.json")
    assert r.returncode == 1 and "have no recorded pixel shader" in r.stdout
    bare = json.loads(keys.read_text())
    del bare["shaders"]
    (tmp_path / "bare").mkdir()
    (tmp_path / "bare" / "d3d9gpu.keys.json").write_text(json.dumps(bare))
    r = check(build, "--params", params["nvidia-like"], "--dumps", tmp_path / "bare" / "d3d9gpu.keys.json",
              "--shader-record", device_record(keys, tmp_path / "r0b.json"))
    assert r.returncode == 1 and "names no shader bytecode" in r.stdout


# ------------------------------------------------------------------ ruling 3's fallback (sdl-patch phase B)
def test_without_either_capability_border_and_depth_clamp_are_owed_never_ok(build, params, session_stock, ruling_check,
                                                                            tmp_path):
    keys = session_stock / "d3d9gpu.keys.json"
    stock = caps_params(params["nvidia-like"], tmp_path / "stock.json", False, False)
    base = ["--params", stock, "--dumps", keys, "--shader-record", device_record(keys, tmp_path / "rec.json")]
    out = tmp_path / "o.json"
    r = check(build, *base, "--controls", "--out", out)
    # no ruling: BORDER owed by the shader route, DEPTH_CLAMP the user's
    assert r.returncode == 4 and r.stdout.splitlines()[-1] == "STATE_CHECK_FALLBACK_OWED BORDER PENDING_USER DEPTH_CLAMP", \
        r.stdout[-3000:]
    d = json.loads(out.read_text())
    assert d["fallback_owed"] == ["BORDER"] and d["failures"] == []
    s = d["census"]["streams"]["session"]
    assert (s["sampler_fallback_border"], s["sampler_border_mapped"], s["depth_clamp"], s["depth_clamp_property"]) == (1, 0, 4, 0)
    # the capability controls are exercised by the sweep with the capability on, and say so
    got = {c["name"]: c["outcome"] for c in d["controls"]}
    assert got["map-border-colour"] == "not exercised by census; fails as required (domain sweep, with CLAMP_TO_BORDER on)"
    assert got["map-depth-clamp-off"] == "not exercised by census; fails as required (domain sweep, with depth clip and clamp on)"
    # ruling 3: both owed, never OK
    repo, rcheck = ruling_check
    patch = commit_ruling(repo, "patch3", "patch")
    r = run([rcheck, *base, "--residue-ruling", patch], timeout=1800)
    assert r.returncode == 4 and r.stdout.splitlines()[-1] == "STATE_CHECK_FALLBACK_OWED BORDER DEPTH_CLAMP", r.stdout[-2000:]
    # a failure outranks what is owed: a fault there reads STATE_CHECK_FAIL, ruled or not (the I6
    # state-check review's finding 2: state_census.sh turns exit 4 into FALLBACK_OWED)
    for extra in ([], ["--residue-ruling", str(patch)]):
        r = run([rcheck, *base, *extra, "--fault", "map-zfunc-swap"], timeout=1800)
        assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL" and "ds_depthCompareOp" in r.stdout, \
            r.stdout[-2000:]


def test_a_test_leg_never_reads_ok_and_the_keys_files_name_the_parameters_capabilities(build, params, session, tmp_path):
    src = json.loads((session / "d3d9gpu.keys.json").read_text())
    rec = device_record(session / "d3d9gpu.keys.json", tmp_path / "rec.json")

    def keys(name: str, border: bool, clamp: bool, override: str | None, drop_border: bool = False) -> Path:
        k = json.loads(json.dumps(src))
        k["device"].update(sampler_clamp_to_border=border, depth_clip_and_clamp=clamp, capability_override=override)
        if not border:    # the device's own BORDER decision without the capability: the fallback
            for x in k["samplers"]:
                if x["border_colour"] != -1:
                    x.update(fallback_border=True, border_colour=-1)
        if drop_border:   # no BORDER sampler: nothing to owe for it
            k["samplers"] = [x for x in k["samplers"] if 4 not in x["ss"][1:4]]
        f = tmp_path / name / "d3d9gpu.keys.json"
        f.parent.mkdir()
        f.write_text(json.dumps(k))
        return f

    nv = params["nvidia-like"]
    # both forced off by a test run: what a stock SDL3 owes, named a test leg
    k = keys("leg", False, False, "border,depth-clamp")
    r = check(build, "--params", caps_params(nv, tmp_path / "leg.json", False, False, "border,depth-clamp"), "--dumps", k,
              "--shader-record", rec)
    assert r.returncode == 4 and r.stdout.splitlines()[-1] == \
        "STATE_CHECK_FALLBACK_OWED BORDER PENDING_USER DEPTH_CLAMP TEST_LEG=border,depth-clamp", r.stdout[-2000:]
    assert "a test run's override: border,depth-clamp forced off" in r.stdout
    # ...and a failure on a test leg is a failure
    r = check(build, "--params", caps_params(nv, tmp_path / "leg1f.json", False, False, "border,depth-clamp"), "--dumps",
              tmp_path / "leg" / "d3d9gpu.keys.json", "--shader-record", rec, "--fault", "map-zfunc-swap")
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "STATE_CHECK_FAIL", r.stdout[-2000:]
    # a test leg that owes nothing still never reads OK
    k = keys("leg2", False, True, "border", drop_border=True)
    r = check(build, "--params", caps_params(nv, tmp_path / "leg2.json", False, True, "border"), "--dumps", k,
              "--shader-record", rec)
    assert r.returncode == 4 and r.stdout.splitlines()[-1] == "STATE_CHECK_FALLBACK_OWED TEST_LEG=border", r.stdout[-2000:]
    # a keys file made under an override the parameters do not name, or with other capabilities,
    # is refused
    r = check(build, "--params", nv, "--dumps", keys("leg3", True, True, "border"), "--shader-record", rec)
    assert r.returncode == 1 and "are not the parameters'" in r.stdout, r.stdout[-2000:]
    r = check(build, "--params", caps_params(nv, tmp_path / "s.json", False, False), "--dumps", session / "d3d9gpu.keys.json",
              "--shader-record", rec)
    assert r.returncode == 1 and "are not the parameters'" in r.stdout, r.stdout[-2000:]
    # the device's BORDER decision per sampler is the check's (the I6 state-check review's NIT 9)
    k = json.loads(json.dumps(src))
    [b] = [x for x in k["samplers"] if x["tex"] and 4 in x["ss"][1:4]]
    b["border_colour"] = 2
    (tmp_path / "colour").mkdir()
    (tmp_path / "colour" / "d3d9gpu.keys.json").write_text(json.dumps(k))
    r = check(build, "--params", nv, "--dumps", tmp_path / "colour" / "d3d9gpu.keys.json", "--shader-record", rec)
    assert r.returncode == 1 and "the device's BORDER decision (fallback 0, colour 2) is not the check's mapping" in r.stdout, \
        r.stdout[-2000:]
    # parameters without the capabilities are no parameters (no default: M7)
    p = json.loads(nv.read_text())
    del p["device"]["depth_clip_and_clamp"]
    (tmp_path / "nocap.json").write_text(json.dumps(p))
    r = check(build, "--params", tmp_path / "nocap.json", "--defaults")
    assert r.returncode == 2 and "depth_clip_and_clamp" in r.stderr


def test_the_project_s_depth_clamp_ruling_says_what_the_check_takes_it_to_say():
    ruling = json.loads(RULING.read_text())
    assert {k: ruling[k] for k in ("schema", "residue", "decided_by", "ruling")} == \
        {"schema": "sa2-residue-ruling/v1", "residue": "DEPTH_CLAMP", "decided_by": "user", "ruling": "patch"}
    decision = (ROOT / ruling["decision"]).read_text()
    # the decision names the class (state_check.cpp load_ruling's rule), and quotes the user's
    # ruling 3, which the record copies word for word
    assert "DEPTH_CLAMP" in decision and ruling["decision"].startswith("Documentation/decisions/")
    quoted = " ".join(line[2:].strip() for line in decision.splitlines() if line.startswith("> "))
    assert ruling["the_user"] in quoted, ruling["the_user"]
    assert (ROOT / ruling["amended_by"]).is_file()


def sm3(*tokens: int) -> bytes:
    import struct
    return struct.pack(f"<{len(tokens)}I", *tokens)


# ps_3_0: def c0 (an immediate that reads as an s0 register token), a comment whose payload reads
# as "mov s5, ..." if walked as instructions, dcl_2d s3, texld r0, v0, s3, mov oC0, r0, end
PS_BLOB = sm3(0xFFFF0300, 0x05000051, 0xA00F0000, 0x3F800000, 0, 0, 0xA0000800, 0x0002FFFE, 0x02000001, 0xA00F0805,
              0x0200001F, 0x90000000, 0xA00F0803, 0x03000042, 0x800F0000, 0x90E40000, 0xA0E40803,
              0x02000001, 0x800F0800, 0x80E40000, 0x0000FFFF)
PS_DISASM = """ps_3_0:
     1:   def c0, (1.000000f, 0.000000f, 0.000000f, -0.000000f)
     2:   dcl_2d s3
     3:   texld r0, v0, s3
     4:   mov oC0, r0
     5:   end
"""
# vs_3_0: dcl_position0 v0, dcl_texcoord0 v1, dcl_position0 o0 (an output: no input), mov o0, v0, end
VS_BLOB = sm3(0xFFFE0300, 0x0200001F, 0x80000000, 0x900F0000, 0x0200001F, 0x80000005, 0x900F0001,
              0x0200001F, 0x80000000, 0xE00F0000, 0x02000001, 0xE00F0000, 0x90E40000, 0x0000FFFF)
VS_DISASM = """vs_3_0:
     1:   dcl_position0 v0
     2:   dcl_texcoord0 v1
     3:   dcl_position0 o0
     4:   mov o0, v0
     5:   end
"""


def scan_tree(tmp_path: Path, blobs: dict[str, bytes], disasm: str, vs_disasm: str = VS_DISASM) -> tuple[Path, Path]:
    scan, dis = tmp_path / "scan", tmp_path / "disasm"
    for s, b in blobs.items():
        (scan / s).mkdir(parents=True)
        (scan / s / "ps0.bin").write_bytes(b)
        (scan / s / "vs0.bin").write_bytes(VS_BLOB)
    dis.mkdir()
    (dis / "ps0.txt").write_text(disasm)
    (dis / "vs0.txt").write_text(vs_disasm)
    return scan, dis


def test_the_shader_record_needs_both_readings_to_agree(tmp_path):
    scan, dis = scan_tree(tmp_path / "a", {"x": PS_BLOB, "y": PS_BLOB}, PS_DISASM)
    out = tmp_path / "r.json"
    r = run([PY, TEST / "shader_record.py", "--scan", scan, "--disasm", dis, "--streams", "x", "y", "--out", out])
    assert r.returncode == 0 and "SHADER_RECORD_OK" in r.stdout, r.stdout + r.stderr
    rec = json.loads(out.read_text())
    assert rec["schema"] == "sa2-d3d9gpu-shader-record/v2"
    ps = rec["ps"][0]
    assert (ps["sampler_mask"], ps["rt_mask"], ps["sha256"]) == (0x8, 0x1, hashlib.sha256(PS_BLOB).hexdigest())
    assert ps["from_tokens"]["sampler_mask"] == ps["from_disassembly"]["sampler_mask"] == 0x8 and rec["agree"]
    vs = rec["vs"][0]
    assert (vs["input_signature"], vs["sha256"]) == ([[0, 0], [5, 0]], hashlib.sha256(VS_BLOB).hexdigest())
    # the disassembly names another sampler: the readings disagree
    scan, dis = scan_tree(tmp_path / "b", {"x": PS_BLOB}, PS_DISASM.replace("s3", "s4"))
    r = run([PY, TEST / "shader_record.py", "--scan", scan, "--disasm", dis, "--streams", "x", "--out", tmp_path / "b.json"])
    assert r.returncode == 1 and "SHADER_RECORD_FAIL" in r.stdout and not json.loads((tmp_path / "b.json").read_text())["agree"]
    # the disassembly names another vertex-shader input: the readings disagree
    scan, dis = scan_tree(tmp_path / "v", {"x": PS_BLOB}, PS_DISASM, VS_DISASM.replace("dcl_texcoord0 v1", "dcl_texcoord1 v1"))
    r = run([PY, TEST / "shader_record.py", "--scan", scan, "--disasm", dis, "--streams", "x", "--out", tmp_path / "v.json"])
    assert r.returncode == 1 and not json.loads((tmp_path / "v.json").read_text())["vs"][0]["agree"]
    # ps0 differs between streams: which blob psN.txt reads is ambiguous
    scan, dis = scan_tree(tmp_path / "c", {"x": PS_BLOB, "y": PS_BLOB[:-8] + sm3(0x0000FFFF, 0x0000FFFF)}, PS_DISASM)
    r = run([PY, TEST / "shader_record.py", "--scan", scan, "--disasm", dis, "--streams", "x", "y", "--out", tmp_path / "c.json"])
    assert r.returncode == 2 and "ambiguous" in r.stderr


def test_the_check_refuses_parameters_without_a_source(build, params, tmp_path):
    p = json.loads(params["nvidia-like"].read_text())
    del p["dxvk"]["features"]["extDepthBiasControl.depthBiasExact"]
    bad = tmp_path / "p.json"
    bad.write_text(json.dumps(p))
    r = check(build, "--params", bad, "--defaults")
    assert r.returncode == 2 and "no recorded value" in r.stderr
    assert check(build, "--defaults").returncode == 2   # no --params at all


# ------------------------------------------------------------------ check_params
LOG = """info:  DXVK: v3.1.1
info:  Effective configuration:
info:    d3d9.maxFrameRate = 0
info:  D3D9: VK_FORMAT_D16_UNORM_S8_UINT -> VK_FORMAT_D24_UNORM_S8_UINT
info:  Creating device:
info:  Test GPU:
info:    Driver   : test
info:  Enabled features:
info:    depthBounds                    : 1
info:    depthClamp                     : 1
info:    fillModeNonSolid               : 1
info:  VK_EXT_attachment_feedback_loop_layout:
info:    attachmentFeedbackLoopLayout   : 1
info:  VK_EXT_border_color_swizzle:
info:    borderColorSwizzle             : 1
info:    borderColorSwizzleFromImage    : 0
info:  VK_EXT_custom_border_color:
info:    customBorderColors             : 1
info:    customBorderColorWithoutFormat : 1
info:  VK_EXT_depth_clip_enable:
info:    depthClipEnable                : 1
info:  VK_EXT_depth_bias_control:
info:    depthBiasControl               : 1
info:    leastRepresentableValueForceUnormRepresentation : 1
info:    floatRepresentation            : 0
info:    depthBiasExact                 : 1
info:  VK_EXT_extended_dynamic_state3:
info:    extendedDynamicState3AlphaToCoverageEnable : 1
info:    extendedDynamicState3DepthClipEnable : 1
info:    extendedDynamicState3RasterizationSamples : 1
info:    extendedDynamicState3SampleMask : 1
info:  VK_EXT_non_seamless_cube_map:
info:    nonSeamlessCubeMap             : 1
"""


def test_check_params_reads_dxvk_s_log_and_refuses_a_gap_or_another_device(tmp_path):
    rep = {"sdl_gpu": {"device_name": "Test GPU", "vk_query_ok": True, "vk_query": "x", "vk_vendor_id": 4318,
                       "vk_point_size_max": 2047.9375, "depth_format": "D24_UNORM_S8_UINT", "sdl_capabilities_present": True,
                       "detected": {"sampler_clamp_to_border": True, "depth_clip_and_clamp": True},
                       "capability_override": None, "sampler_clamp_to_border": True, "depth_clip_and_clamp": True}}
    (tmp_path / "r.json").write_text(json.dumps(rep))
    (tmp_path / "d.log").write_text(LOG)

    def params_of(log: str, report: str = "r.json", *extra) -> subprocess.CompletedProcess:
        return run([PY, TEST / "check_params.py", "--dxvk-log", tmp_path / log, "--device-report", tmp_path / report,
                    *extra, "--out", tmp_path / f"{log}.{report}.json"])

    r = params_of("d.log")
    assert r.returncode == 0, r.stderr
    p = json.loads((tmp_path / "d.log.r.json.json").read_text())
    assert p["dxvk"]["vendor_id"] == 4318 and p["dxvk"]["d24s8_supported"] and len(p["dxvk"]["features"]) == 15
    assert p["dxvk"]["d16s8_supported"] is False and p["device_name"] == "Test GPU" and p["axis"] is None
    assert p["dxvk"]["features"]["extDepthBiasControl.floatRepresentation"] is False
    assert p["dxvk_effective_configuration"] == {"d3d9.maxFrameRate": "0"}
    assert "d3d9.samplerAnisotropy" in p["dxvk_modelled_options"]["options"]
    (tmp_path / "d2.log").write_text(LOG.replace("info:    depthBiasExact                 : 1\n", ""))
    r = params_of("d2.log")
    assert r.returncode == 2 and "depthBiasExact" in r.stderr
    assert p["checks"]["vendor_id"].startswith("not cross-checked")
    # an effective configuration that sets an option the shims model, by the span table or by a manifest
    (tmp_path / "opt.log").write_text(LOG.replace("info:    d3d9.maxFrameRate = 0\n",
                                                  "info:    d3d9.maxFrameRate = 0\ninfo:    d3d9.samplerLodBias = 0.5\n"))
    r = params_of("opt.log")
    assert r.returncode == 2 and "d3d9.samplerLodBias" in r.stderr, r.stderr
    (tmp_path / "m.json").write_text(json.dumps({"dxvk_conf": {"modelled_options": ["d3d9.maxFrameRate"]}}))
    r = params_of("d.log", "r.json", "--extract-manifest", tmp_path / "m.json")
    assert r.returncode == 2 and "d3d9.maxFrameRate" in r.stderr, r.stderr
    # a built-in (per-application) configuration
    (tmp_path / "bi.log").write_text(LOG.replace("info:  Effective configuration:", "info:  Found built-in config:\ninfo:  Effective configuration:"))
    r = params_of("bi.log")
    assert r.returncode == 2 and "built-in configuration" in r.stderr
    # a driver DXVK names is held to the device's vendor ID (N9)
    (tmp_path / "nv.log").write_text(LOG.replace("Driver   : test", "Driver   : NVIDIA 595.91.7"))
    r = params_of("nv.log")
    assert r.returncode == 0 and "as the device's query says" in json.loads((tmp_path / "nv.log.r.json.json").read_text())["checks"]["vendor_id"]
    rep["sdl_gpu"]["vk_vendor_id"] = 0x1002
    (tmp_path / "amd.json").write_text(json.dumps(rep))
    r = params_of("nv.log", "amd.json")
    assert r.returncode == 2 and "names driver" in r.stderr
    rep["sdl_gpu"]["vk_vendor_id"] = 4318
    rep["sdl_gpu"]["device_name"] = "Other GPU"
    (tmp_path / "r2.json").write_text(json.dumps(rep))
    r = params_of("d.log", "r2.json")
    assert r.returncode == 2 and "not the second host" in r.stderr
    rep["sdl_gpu"]["device_name"] = "Test GPU"


def test_check_params_holds_the_sdl_patch_s_capabilities_to_dxvk_s_log(tmp_path):
    base = {"device_name": "Test GPU", "vk_query_ok": True, "vk_query": "x", "vk_vendor_id": 4318,
            "vk_point_size_max": 2047.9375, "depth_format": "D24_UNORM_S8_UINT", "sdl_capabilities_present": True,
            "detected": {"sampler_clamp_to_border": True, "depth_clip_and_clamp": True},
            "capability_override": None, "sampler_clamp_to_border": True, "depth_clip_and_clamp": True}
    (tmp_path / "d.log").write_text(LOG)

    def params_of(sg: dict, log: str = "d.log") -> tuple[subprocess.CompletedProcess, dict | None]:
        (tmp_path / "r.json").write_text(json.dumps({"sdl_gpu": sg}))
        out = tmp_path / "p.json"
        if out.exists():
            out.unlink()
        r = run([PY, TEST / "check_params.py", "--dxvk-log", tmp_path / log, "--device-report", tmp_path / "r.json",
                 "--out", out])
        return r, json.loads(out.read_text()) if out.exists() else None

    r, p = params_of(base)
    assert r.returncode == 0, r.stderr
    assert p["device"] == {"d24s8_supported": True, "point_size_max": 2047.9375, "sampler_clamp_to_border": True,
                           "depth_clip_and_clamp": True, "capability_override": None}
    assert p["checks"]["depth_clip_and_clamp"].startswith("the device detected depth clip and clamp True, as DXVK's log")
    # a test run's override: the used capabilities are the override's, the detected ones still DXVK's
    r, p = params_of(dict(base, capability_override="depth-clamp", depth_clip_and_clamp=False))
    assert r.returncode == 0 and p["device"]["depth_clip_and_clamp"] is False and p["device"]["capability_override"] == "depth-clamp"
    # detected other than DXVK's log of the same driver
    r, _ = params_of(dict(base, detected={"sampler_clamp_to_border": True, "depth_clip_and_clamp": False},
                          depth_clip_and_clamp=False))
    assert r.returncode == 2 and "DXVK's log of the same driver reports" in r.stderr
    (tmp_path / "noclip.log").write_text(LOG.replace("info:    depthClipEnable                : 1\n",
                                                     "info:    depthClipEnable                : 0\n"))
    r, _ = params_of(base, "noclip.log")
    assert r.returncode == 2 and "depthClipEnable 0" in r.stderr
    (tmp_path / "gap.log").write_text(LOG.replace("info:    depthClamp                     : 1\n", ""))
    r, _ = params_of(base, "gap.log")
    assert r.returncode == 2 and "depthClamp" in r.stderr
    # used capabilities other than the detected ones, under no override
    r, _ = params_of(dict(base, sampler_clamp_to_border=False))
    assert r.returncode == 2 and "under no override" in r.stderr
    # a stock SDL: neither capability, not cross-checked; one it claims is refused
    stock = dict(base, sdl_capabilities_present=False, detected={"sampler_clamp_to_border": False, "depth_clip_and_clamp": False},
                 sampler_clamp_to_border=False, depth_clip_and_clamp=False)
    r, p = params_of(stock)
    assert r.returncode == 0 and p["checks"]["depth_clip_and_clamp"].startswith("not cross-checked: a stock SDL3")
    r, _ = params_of(dict(stock, detected={"sampler_clamp_to_border": True, "depth_clip_and_clamp": False},
                          sampler_clamp_to_border=True))
    assert r.returncode == 2 and "stock SDL" in r.stderr
    # a report from before phase B
    old = {k: v for k, v in base.items() if k not in ("detected", "capability_override")}
    r, _ = params_of(old)
    assert r.returncode == 2 and "before the SDL patch's phase B" in r.stderr


# ------------------------------------------------------------------ the projection (an instrument: S12)
SCAN_CPP = "static const int pk_states[] = {7,8,174};\n"
DECL = [[0, 0, 2, 0, 0, 0], [0, 12, 4, 0, 10, 0], [0, 16, 1, 0, 5, 0]]


def census_txt(pkeys: list[str], skeys: list[str]) -> str:
    return ("frames 1 draws 1\nmax per frame: x\n" +
            f"vs 1 ps 1 decls 1 pipeline keys {len(pkeys)} sampler keys {len(skeys)}\n" +
            "== set render target (0)\n== set depth stencil (0)\n" +
            "== bound texture formats at draws (1)\n       5  stage0 fmt=21 usage=0x0 src=file\n" +
            "== texture creation (1)\n       1  d3dxcreate fmt=21 usage=0 pool=1 levels=4294967295 256x256\n" +
            "== misc (1)\n       1  pp bb=1280x720 fmt=21 count=1 ms=0 q=0 swap=1 windowed=1 autods=1 dsfmt=75 flags=0x0 "
            "refresh=0 interval=0x0 behavior=0x40 devtype=1\n" +
            "== sampler keys (addrU addrV addrW border mag min mip lodbias maxmip aniso srgb)\n" +
            "".join(f"  {s}  5\n" for s in skeys) +
            "== pipeline keys: vs ps decl prim stride up | states | rtfmt ds\n" +
            "".join(f"  {p}  9\n" for p in pkeys) +
            "== decls\n  decl0:" + "".join(f" [s{e[0]} o{e[1]} t{e[2]} m{e[3]} u{e[4]} i{e[5]}]" for e in DECL) + " END\n")


def keys_json(inputs: list[dict], samplers: list[dict]) -> dict:
    order = [7, 8]
    return {"schema": "sa2-d3d9gpu-keys/v2", "complete": True, "pipeline_render_states": order,
            "census_extra_render_states": [174], "inputs": inputs, "samplers": samplers}


def rec(zenable: int, scissor: int) -> dict:
    t = [21, 1280, 720, 0, 0, 0, True, False]
    none = [0, 0, 0, 0, 0, 0, False, False]
    return {"id": 0, "rs": [zenable, 3], "extra": [scissor], "method": 2, "prim": 5, "stride0": 24, "vs": 1, "ps": 1,
            "decl": 1, "elems": [list(e) for e in DECL], "rt": [t, none, none, none],
            "ds": [75, 1280, 720, 0, 0, 0, True, False]}


def test_the_projection_reproduces_a_known_census_and_fails_an_unchecked_tuple(tmp_path):
    scan = tmp_path / "scan"
    scan.mkdir()
    (scan / "scan.cpp").write_text(SCAN_CPP)
    p1 = "vs0 ps0 d0 p5 s24 up1 | 7=1 8=3 174=0 | 21 0 0 0 75"
    p2 = "vs0 ps0 d0 p5 s24 up1 | 7=0 8=3 174=1 | 21 0 0 0 0"   # scan's rule: no DS format with Z and stencil off
    sk = "1 1 1 0 2 2 2 0 0 1 0"
    (scan / "s.txt").write_text(census_txt([p1, p2], [sk]))
    samp = [{"id": 0, "ss": [0, 1, 1, 1, 0, 2, 2, 2, 0, 0, 1, 0, 0, 0], "tex": True, "levels": 9, "cube": False,
             "depth": False, "fmt": 21, "stages": 1},
            {"id": 1, "ss": [0, 4, 4, 4, 0, 2, 2, 2, 0, 0, 1, 0, 0, 0], "tex": False, "levels": 0, "cube": False,
             "depth": False, "fmt": 0, "stages": 2}]   # no texture: not in the census

    def project(name: str, inputs: list[dict], samplers: list[dict] = samp) -> tuple[subprocess.CompletedProcess, dict]:
        f = tmp_path / f"{name}.json"
        f.write_text(json.dumps(keys_json(inputs, samplers)))
        r = run([PY, TEST / "scan_projection.py", "--dumps", f"s={f}", "--scan", scan, "--out", tmp_path / f"{name}.out.json"])
        return r, json.loads((tmp_path / f"{name}.out.json").read_text())["streams"]["s"]

    r, o = project("good", [rec(1, 0), rec(0, 1)])
    assert r.returncode == 0 and "SCAN_PROJECTION_OK" in r.stdout, r.stdout + r.stderr
    assert o["resources_compared"]["declarations"] == 2 and o["resources_compared"]["sampler_levels"] == 1
    r, o = project("missing", [rec(1, 0)])
    assert r.returncode == 1 and len(o["unchecked_census_pipeline_tuples"]) == 1
    r, o = project("extra", [rec(1, 0), rec(0, 1), rec(1, 1)])
    assert r.returncode == 1 and len(o["extra_dump_pipeline_tuples"]) == 1
    # what the projection's key leaves out is held to the census's records too (the
    # checker-logic re-review's finding 8): the declaration, a target's extent, a texture
    wrong_decl = rec(0, 1)
    wrong_decl["elems"][1][1] = 16
    r, o = project("decl", [rec(1, 0), wrong_decl])
    assert r.returncode == 1 and any("are not the census's decl0" in x for x in o["problems"])
    wrong_rt = rec(0, 1)
    wrong_rt["rt"][0][1] = 640
    r, o = project("rt", [rec(1, 0), wrong_rt])
    assert r.returncode == 1 and any("is not a target the census sets" in x for x in o["problems"])
    wrong_ds = rec(0, 1)
    wrong_ds["ds"][2] = 512
    r, o = project("ds", [rec(1, 0), wrong_ds])
    assert r.returncode == 1 and any("is not one the census creates" in x for x in o["problems"])
    for field, value, why in (("fmt", 22, "is not a format the census binds"), ("levels", 3, "which no texture the census creates"),
                              ("cube", True, "the census creates none")):
        s = [dict(samp[0], **{field: value}), samp[1]]
        r, o = project(f"s-{field}", [rec(1, 0), rec(0, 1)], s)
        assert r.returncode == 1 and any(why in x for x in o["problems"]), (field, o["problems"])
