#!/usr/bin/env python3
"""The R1 GPU script's Python side: the load check's plan, and the receipt.

Subtitle: host/d3d9gpu/test_shaders.sh runs the shader translator's GPU checks (the R1 plan's
section 4: C1 to C10, C4 the state census with U9's vertex input; C11's conversion and texture-type clause, Metal's run on a
Mac being carried forward; and the sampler-mode specialization's C12 and C13); this tool turns the second host's
own replays into the set of shaders, variants and pairs the load check must create (`plan`), and the
run's results into Documentation/receipts/s5-host2-r1-v1.json (`write`). It holds hashes, counts and
markers only: no SPIR-V, no bytecode, nothing derived from the game's bytes but their sha256.

  r1_receipt.py plan --keys BOOT.keys.json MENU.keys.json STORY.keys.json MH.keys.json
                     --bytecode DIR --reflections DIR --out PLAN.json
  r1_receipt.py write --run R --out RECEIPT.json [--build OUT]

`plan`: the objects are the vertex and pixel shaders the census streams create, by creation id
(the device's keys files' "shaders"; every stream must create the same list, or the plan refuses),
named vsN and psN with N the creation id less one (the scan's names, next/D/scan); their bytecode is
the device's dump (SA2_D3D9GPU_SHADER_DUMP: DIR/<sha256>.bin, each of which must be there). The
variants are the BGRA vertex-shader variants the census tuples need: for a tuple, signature location
i is in the mask when the declaration element of signature entry i's semantic (POSITIONT read as
POSITION, d3d9_device.cpp:7677-7678) is a D3DCOLOR, the rule DXVK's BindInputLayout keys its
B8G8R8A8 format on (the vertex shaders' signatures are DXVK's analysis's, from shaderc's reflections
in --reflections, each held to shader_record.py's reading of the bytecode's own tokens: the
signature and the pixel shaders' sampler and output masks must agree, or the plan refuses). The
pairs are every creation pair (vsN with psN: the game creates its shaders in
pairs) and every pair a census tuple draws, each with one census state: the targets' formats, the
depth target and the primitive type of the first tuple that draws it (a pair no tuple draws takes the
first tuple's of the first stream), one pair per distinct BGRA mask its tuples need. Every pixel shader,
every pair's pixel shader and every border variant is specialized at the sampler-mode word DXVK's rule
gives the census's sampler records (dxvk_sampler_mode, d3d9_device.cpp's slot tracking as
d3d9_state.h:277-296 packs it): 0 for every record the streams hold (colour textures, no fetch4). The
keys files hold sampler records apart from the draws that bind them, so a record whose mode could be
other than 0 (a depth texture DXVK samples with depth compare; a single-channel point-filtered texture
whose stage holds GET4, or may hold it, DXVK's fetch4 switch being sticky) is refused, never planned:
its draws' words are R2-draws' to compose.

`write`: reads test_shaders.sh's run directory R (checks/<name>.json, which each step writes) and
writes the receipt: every check's marker, the counts behind it, both drivers' profile records, the
archive's and the layers' hashes, the streams' identities and the explanation classes' hit counts. It
copies nothing it has not checked: the checks must be exactly EXPECTED's; each driver's profile
record must be the one every module of its load was compiled with, and the corpus's one of them; the
translator identity every reflection names must be the build's; with --build, every source the
build's records hashed must be the tree's; the validation layer's hash is of the library the run
loaded, which must be its pin; and the commit is the repository's HEAD.

Each step's check can fail on what it reads (test_s5_d3d9gpu_shader.py holds each one to synthetic
inputs): a debug run must show the validation layer loaded (SDL's "Validation layers enabled", never
"Validation layers not found") and its errors counted with the layer's duplicate-message limit
lifted; any validation error, in any check, fails the run (the user's second rulings have host 2's
debug runs use the layers: "Use the validation layers in host 2's debug runs, since Vulkan misuse
that a desktop driver tolerates is what breaks on phones." That those runs fail on any validation
error is the agent's amendment of that ruling, RULINGS-2-AMENDMENTS.md, not the user's words; only the
user can exempt an error from it); a capture must show the layer
loaded in every run that set it (its "vkcap: load=" line, no write failure) and account for every
record it wrote.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import shader_record  # noqa: E402  the state check's independent reading of the bytecode's tokens
import check_census_draws  # noqa: E402  default C4 replays held to original bytes again at receipt time

STREAMS = ("boot", "menu", "story", "mh")
D3DDECLTYPE_D3DCOLOR = 4
D3DDECLTYPE_UNUSED = 17
D3DDECLUSAGE_POSITION, D3DDECLUSAGE_POSITIONT = 0, 9
# D3DFORMAT -> the load check's names (shaderc.cpp load), for the formats the census's targets use
COLOUR_FORMATS = {21: "B8G8R8A8_UNORM", 22: "B8G8R8A8_UNORM"}     # A8R8G8B8, X8R8G8B8 (d9g_format.cpp)
DEPTH_FORMATS = {75: "D24S8"}                                     # D3DFMT_D24S8
PRIMITIVES = {1: "POINTLIST", 2: "LINELIST", 3: "LINESTRIP", 4: "TRIANGLELIST", 5: "TRIANGLESTRIP",
              6: "TRIANGLELIST"}                                  # a fan drawn as a list (d9g_draw)


class Refused(Exception):
    pass


def bgra_mask(signature: list, elems: list) -> int:
    """The BGRA location mask of a tuple: bit i where signature entry i's element is a D3DCOLOR. The
    element of an entry is DXVK's BindInputLayout's (d3d9_device.cpp:7674-7685,
    D3D9InputSignature::find): each element in declaration order to the first entry of its packed
    semantic (usage and usage index four bits each, POSITIONT read as POSITION), a later element of
    one semantic replacing an earlier one."""
    def packed(usage: int, index: int) -> int:
        return ((D3DDECLUSAGE_POSITION if usage == D3DDECLUSAGE_POSITIONT else usage) & 0xF) | ((index & 0xF) << 4)
    element = {}
    for e in elems:
        for i, (usage, index) in enumerate(signature):
            if packed(usage, index) == packed(e[4], e[5]):
                element[i] = e
                break
    return sum(1 << i for i, e in element.items() if e[2] == D3DDECLTYPE_D3DCOLOR)


D3DTADDRESS_BORDER = 4
D3DTEXF_POINT, D3DTEXF_LINEAR = 1, 2
D3DSAMP_ADDRESSU, D3DSAMP_ADDRESSV, D3DSAMP_MAGFILTER, D3DSAMP_MINFILTER, D3DSAMP_MIPMAPLODBIAS = 1, 2, 5, 6, 8
FOURCC_GET4 = 0x34544547   # MAKEFOURCC('G', 'E', 'T', '4'): fetch4 on (d3d9_device.cpp:4579-4588)
FOURCC_GET1 = 0x31544547   # MAKEFOURCC('G', 'E', 'T', '1'): fetch4 off


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode(), "little")


# DXVK's IsDepthFormat (d3d9_format.h:288-302), the shadow-map exceptions (DetermineShadowState,
# d3d9_common_texture.cpp:514-521) and the fetch4 formats (DetermineFetch4Compatibility, :524-532)
DEPTH_D3DFMTS = {70, 71, 73, 75, 77, 79, 80, 82, 83, 84, _fourcc("DF16"), _fourcc("DF24"), _fourcc("INTZ")}
NOT_SHADOW = {_fourcc("INTZ"), _fourcc("DF16"), _fourcc("DF24")}
FETCH4_D3DFMTS = {_fourcc("INTZ"), _fourcc("DF16"), _fourcc("DF24"), 111, 114, 28, 50, 81}   # R16F R32F A8 L8 L16


def dxvk_sampler_mode(sampler: dict) -> set:
    """The sampler modes DXVK can give a keys file's sampler record (updateSamplers, d3d9_state.h:277-296):
    {2, 3} for a texture it samples as a shadow map (3 where the device stores its format as D32 float,
    which the record does not say); for a single-channel format with point magnification, {1} where
    MIPMAPLODBIAS holds GET4, {0} where it holds GET1, and {0, 1} for any other value, because DXVK's
    fetch4 switch is sticky (d3d9_device.cpp:4579-4588, d9g_shader.h fetch4_after: only GET1 turns a GET4
    off) and one record does not show what the stage was given before; else {0}; an unbound stage {0}."""
    if not sampler.get("tex"):
        return {0}
    fmt, ss = sampler.get("fmt", 0), sampler["ss"]
    if fmt in DEPTH_D3DFMTS and fmt not in NOT_SHADOW:
        return {2, 3}
    if fmt in FETCH4_D3DFMTS and ss[D3DSAMP_MAGFILTER] == D3DTEXF_POINT:
        bias = ss[D3DSAMP_MIPMAPLODBIAS]
        return {1} if bias == FOURCC_GET4 else {0} if bias == FOURCC_GET1 else {0, 1}
    return {0}


def border_variant(sampler: dict, sampler_mask: int):
    """Ruling 3's border fallback variant a pixel shader needs where this sampler (a keys file's
    sampler record) is bound at a stage the shader samples, on a device without CLAMP_TO_BORDER
    (d9g_shader.h Variant::border_u/border_v/border_linear): None where the sampler has no BORDER axis
    or the shader samples none of its stages, the string reason where the fallback cannot apply (a
    cube or depth texture, fetch4, or filters the emulation does not take: the reads the emulation
    leaves, d9g_shader.h Reflection::border_unwrapped_*), else (border_u, border_v, border_linear) as
    register masks."""
    ss = sampler["ss"]
    u, v = ss[D3DSAMP_ADDRESSU] == D3DTADDRESS_BORDER, ss[D3DSAMP_ADDRESSV] == D3DTADDRESS_BORDER
    regs = sampler["stages"] & sampler_mask & 0xFFFF
    if not (u or v) or not regs:
        return None
    if sampler["cube"] or sampler["depth"]:
        return "a cube or depth texture"
    if ss[D3DSAMP_MIPMAPLODBIAS] == FOURCC_GET4 or 1 in dxvk_sampler_mode(sampler):
        return "fetch4 (D3DSAMP_MIPMAPLODBIAS GET4, or a single-channel point-filtered texture whose stage may hold it)"
    mag, mn = ss[D3DSAMP_MAGFILTER], ss[D3DSAMP_MINFILTER]
    if (mag, mn) not in ((D3DTEXF_POINT, D3DTEXF_POINT), (D3DTEXF_LINEAR, D3DTEXF_LINEAR)):
        return f"MAGFILTER {mag} and MINFILTER {mn}"
    return (regs if u else 0, regs if v else 0, regs if mag == D3DTEXF_LINEAR else 0)


def state_of(r: dict) -> dict:
    colour = []
    for t in r["rt"]:
        if t[6]:   # bound
            if t[0] not in COLOUR_FORMATS:
                raise Refused(f"a census target of D3DFORMAT {t[0]}, which the load check has no SDL format for")
            colour.append(COLOUR_FORMATS[t[0]])
    if r["ds"][6] and r["ds"][0] not in DEPTH_FORMATS:
        raise Refused(f"a census depth target of D3DFORMAT {r['ds'][0]}")
    if r["prim"] not in PRIMITIVES:
        raise Refused(f"a census primitive type {r['prim']}")
    return {"colour_formats": colour, "depth": DEPTH_FORMATS[r["ds"][0]] if r["ds"][6] else "none",
            "primitive": PRIMITIVES[r["prim"]]}


def plan(args) -> int:
    keys = [json.loads(Path(k).read_text()) for k in args.keys]
    if len(keys) != len(STREAMS):
        raise Refused(f"{len(keys)} keys files; the census is {len(STREAMS)} streams ({' '.join(STREAMS)})")
    first = keys[0]["shaders"]
    for path, k in zip(args.keys, keys):
        if k.get("schema") != "sa2-d3d9gpu-keys/v3" or not k.get("complete"):
            raise Refused(f"{path} is not a complete sa2-d3d9gpu-keys/v3 file")
        if k["shaders"] != first:
            raise Refused(f"{path} creates other shaders than {args.keys[0]}: the objects are not one list")
    dump = Path(args.bytecode)
    objects = []
    for stage, prefix in (("vertex", "vs"), ("pixel", "ps")):
        for i, sha in enumerate(first["vs" if stage == "vertex" else "ps"]):
            path = dump / f"{sha}.bin"
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise Refused(f"the dump holds no {sha}.bin with those bytes ({prefix}{i})")
            objects.append({"name": f"{prefix}{i}", "stage": stage, "sha256": sha, "bytecode": str(path.resolve())})
    refl = Path(args.reflections)
    signature, sampler_mask = {}, {}
    for o in objects:
        r = json.loads((refl / f"{o['name']}.json").read_text())
        if r.get("bytecode_sha256") != o["sha256"]:
            raise Refused(f"{refl}/{o['name']}.json is not the reflection of {o['sha256']}")
        # the translator's reflection (DXVK's analysis) against the bytecode's own tokens, read by the
        # state check's instrument: a reflection that lost a signature entry or a sampler would plan
        # fewer variants and pairs than the streams need, and the load check could not see it
        blob = Path(o["bytecode"]).read_bytes()
        try:
            tokens = shader_record.token_signature(blob) if o["stage"] == "vertex" else shader_record.token_masks(blob)
        except shader_record.Refusal as e:
            raise Refused(f"{o['name']}: the bytecode's tokens do not read: {e}")
        if o["stage"] == "vertex":
            if tokens["input_signature"] != r["input_signature"]:
                raise Refused(f"{o['name']}: the reflection's input signature {r['input_signature']} is not the bytecode's "
                              f"{tokens['input_signature']}")
            signature[o["name"]] = r["input_signature"]
        else:
            if (tokens["sampler_mask"], tokens["rt_mask"]) != (r["sampler_mask"], r["rt_mask"]):
                raise Refused(f"{o['name']}: the reflection's sampler and output masks {r['sampler_mask']}, {r['rt_mask']} "
                              f"are not the bytecode's {tokens['sampler_mask']}, {tokens['rt_mask']}")
            sampler_mask[o["name"]] = r["sampler_mask"]
    # the sampler-mode word (d9g_shader.h Variant::sampler_modes): every census sampler record a pixel
    # shader samples must be mode 0 by DXVK's rule, or the plan refuses (a record is not joined to its draws)
    records, nonzero = 0, []
    for k in keys:
        for smp in k["samplers"]:
            records += 1
            for name, mask in sampler_mask.items():
                if smp["stages"] & mask & 0xFFFF and dxvk_sampler_mode(smp) != {0}:
                    nonzero.append(f"{name}: sampler record {smp['id']} (D3DFORMAT {smp.get('fmt')}) gives mode "
                                   f"{sorted(dxvk_sampler_mode(smp))}")
    if nonzero:
        raise Refused("a census sampler record whose sampler mode is not 0, which the plan cannot join to its draws "
                      "(R2-draws composes those words): " + "; ".join(nonzero[:4]))
    word = 0
    # ruling 3's fallback: the border variants every pixel shader needs that samples a stage a BORDER
    # sampler of the census is bound at, as a device without CLAMP_TO_BORDER owes them
    border, border_unmapped = set(), []
    for k in keys:
        for smp in k["samplers"]:
            for name, mask in sampler_mask.items():
                bv = border_variant(smp, mask)
                if isinstance(bv, str):
                    border_unmapped.append({"shader": name, "sampler": smp["id"], "why": bv})
                elif bv:
                    border.add((name, *bv))
    pairs, variants, firsts = {}, set(), None
    for k in keys:
        for r in k["inputs"]:
            vs, ps = r["vs"], r["ps"]
            if not vs or not ps:
                raise Refused("a census tuple without both shaders (fixed function is not the streams')")
            vname, pname = f"vs{vs - 1}", f"ps{ps - 1}"
            mask = bgra_mask(signature[vname], r["elems"])
            if mask:
                variants.add((vname, mask))
            st = state_of(r)
            firsts = firsts or st
            key = (vname, pname, mask)
            if key not in pairs:
                pairs[key] = {"vs": vname, "ps": pname, "bgra": mask, "drawn": True, "sampler_modes": word, **st}
    n = len(first["vs"])
    if len(first["ps"]) != n:
        raise Refused(f"{n} vertex and {len(first['ps'])} pixel shaders: no creation pairs")
    for i in range(n):
        if not any(v == f"vs{i}" and p == f"ps{i}" for v, p, _ in pairs):
            pairs[(f"vs{i}", f"ps{i}", 0)] = {"vs": f"vs{i}", "ps": f"ps{i}", "bgra": 0, "drawn": False,
                                              "sampler_modes": word, **firsts}
    out = []
    for (v, p, m), d in sorted(pairs.items(), key=lambda kv: (int(kv[0][0][2:]), int(kv[0][1][2:]), kv[0][2])):
        name = f"pair{v[2:]}" if v[2:] == p[2:] else f"pair{v[2:]}x{p[2:]}"
        if m:
            name += f".bgra{m}"
        out.append({"name": name, **d})
    result = {"schema": "sa2-d3d9gpu-r1-load-plan/v1", "keys": [str(Path(k).resolve()) for k in args.keys],
              "keys_sha256": [hashlib.sha256(Path(k).read_bytes()).hexdigest() for k in args.keys],
              "shaders": objects, "distinct_bytecodes": len({o["sha256"] for o in objects}),
              "variants": [{"shader": v, "bgra": m} for v, m in sorted(variants, key=lambda x: (int(x[0][2:]), x[1]))],
              "pairs": out, "drawn_pairs": sum(1 for p in out if p["drawn"]),
              "border_variants": [{"shader": n, "border_u": bu, "border_v": bv, "border_linear": bl, "sampler_modes": word}
                                  for n, bu, bv, bl in sorted(border, key=lambda x: (int(x[0][2:]), x[1:]))],
              "border_unmapped": border_unmapped,
              "specialized": [{"shader": o["name"], "sampler_modes": word} for o in objects if o["stage"] == "pixel"],
              "sampler_modes": {"word": word, "sampler_records": records, "nonzero": len(nonzero),
                                "rule": "DXVK's updateSamplers (d3d9_state.h:277-296) on each census sampler record: "
                                        "depth compare for a shadow-map depth format, fetch4 for GET4 on a "
                                        "single-channel format with point magnification, else 0"}}
    Path(args.out).write_text(json.dumps(result, indent=1) + "\n")
    print(f"PLAN {sum(o['stage'] == 'vertex' for o in objects)} vertex and {sum(o['stage'] == 'pixel' for o in objects)} "
          f"pixel shaders ({result['distinct_bytecodes']} distinct bytecodes), {len(result['variants'])} BGRA variants, "
          f"{len(out)} pairs ({result['drawn_pairs']} drawn), {len(result['border_variants'])} border fallback variants "
          f"({len(border_unmapped)} cases the fallback cannot take), {len(result['specialized'])} pixel shaders "
          f"specialized at sampler-mode word {word:#x} ({records} sampler records, every one mode 0)")
    return 0


# ---- the GPU script's steps: each reads its step's output and writes checks/<name>.json
def put(run: Path, name: str, data: dict) -> int:
    (run / "checks").mkdir(parents=True, exist_ok=True)
    (run / "checks" / f"{name}.json").write_text(json.dumps(data, indent=1) + "\n")
    print(f"   {name}: {'pass' if data['pass'] else 'FAIL'}: {data.get('summary', '')}")
    return 0 if data["pass"] else 1


def last_json(text: str):
    """The JSON object a tool printed before its one-line marker."""
    body = text.strip().rsplit("\n", 1)[0]
    return json.loads(body[body.index("{"):])


def note(args) -> int:
    text = Path(args.marker_file).read_text()
    ok = args.want in text
    return put(Path(args.run), args.check, {"check": args.check, "pass": ok, "want": args.want,
                                            "summary": text.strip().splitlines()[-1] if text.strip() else "(no output)"})


def corpus(args) -> int:
    run = Path(args.run)
    out, ok = {}, True
    for s in args.streams:
        d = json.loads((run / "corpus" / s / "d3d9gpu.json").read_text())
        sh = d["shaders"]
        good = (d["complete"] and d["failure"] is None and d["not_implemented"]["count"] == 0 and sh["translate_failures"] == 0
                and sh["translated"] == sh["vs"] + sh["ps"] and sh["dump"]["failures"] == 0 and sh["dump"]["refused"] is None
                and sh["profile"]["facts_ok"])
        ok = ok and good
        out[s] = {"vs": sh["vs"], "ps": sh["ps"], "translated": sh["translated"], "failures": sh["translate_failures"],
                  "dumped": sh["dump"]["dumped"], "profile_sha256": sh["profile"]["record_sha256"],
                  "stream_sha256": json.loads((run / "corpus" / s / "report.json").read_text()).get("stream_sha256"),
                  "keys_sha256": hashlib.sha256((run / "corpus" / s / "d3d9gpu.keys.json").read_bytes()).hexdigest(),
                  "pass": good}
    dumped = sorted(p.name for p in (run / "bytecode").glob("*.bin"))
    return put(run, "corpus", {"check": "corpus", "pass": ok and bool(dumped), "streams": out, "distinct_bytecodes": len(dumped),
                               "summary": f"{sum(v['translated'] for v in out.values())} shaders translated in "
                                          f"{len(out)} replays, {sum(v['failures'] for v in out.values())} refused, "
                                          f"{len(dumped)} distinct bytecodes dumped"})


def name(args) -> int:
    k = json.loads(Path(args.plan_keys).read_text())
    out = Path(args.out)
    for stage, prefix in (("vs", "vs"), ("ps", "ps")):
        for i, sha in enumerate(k["shaders"][stage]):
            src = Path(args.bytecode) / f"{sha}.bin"
            (out / f"{prefix}{i}.bin").write_bytes(src.read_bytes())
    print(f"   named {len(k['shaders']['vs'])} vertex and {len(k['shaders']['ps'])} pixel shaders")
    return 0


def validation_errors(text: str) -> dict:
    """{VUID: count} of the Khronos layer's "Validation Error: [ VUID ]" lines."""
    out = {}
    for line in text.splitlines():
        if "Validation Error" in line:
            vuid = line.split("[", 1)[1].split("]", 1)[0].strip() if "[" in line else "?"
            out[vuid] = out.get(vuid, 0) + 1
    return out


# SDL_gpu's own account of its debug instance (SDL_LOGGING=gpu=info, SDL_gpu_vulkan.c): the layer it
# asked for was found and enabled, or not. A missing layer does not fail the instance, so a run
# under the layer is held to the first line and never shows the second.
LAYER_ENABLED = "Validation layers enabled"
LAYER_MISSING = "Validation layers not found"


def layer_loaded(text: str) -> bool:
    return LAYER_ENABLED in text and LAYER_MISSING not in text


def marker_of(text: str, prefix: str) -> str:
    """A tool's last line that starts with its marker's prefix: the capture layer prints its account
    when the process exits, after the tool's own marker, so a marker is never read as the last line."""
    return next((l.strip() for l in reversed(text.splitlines()) if l.startswith(prefix)), "")


# vkcap.c prints its account on stderr when the loader unloads it, which can land inside a line the
# process's buffered stdout wrote to the same file: it is found wherever it stands, not at a line's start
VKCAP_ACCOUNT = re.compile(r"vkcap: load=(\S+) records=(\d+) write_failures=(\d+)")


def capture_loads(text: str) -> list:
    """The capture layer's own account of each load in one log: {load, records, write_failures}. One
    process can load the layer more than once (the loader unloads it with its last instance), each
    load counting its own records."""
    return [{"load": m.group(1), "records": int(m.group(2)), "write_failures": int(m.group(3))}
            for m in VKCAP_ACCOUNT.finditer(text)]


def capture_account(capture: Path, logs: list) -> dict:
    """A capture directory against the loads that wrote it: every log must show the layer loaded (a
    "vkcap: load=" line) with no write failure, and the records the loads counted must be the
    records the directory holds (its modules, and the lines of its pipelines, devices and samplers
    files: vkcap.c counts each one it writes, and writes a module once)."""
    loads = [(Path(lg).name, capture_loads(Path(lg).read_text())) for lg in logs]
    modules = sorted(capture.glob("module-*.spv")) if capture.is_dir() else []
    lines = sum(len([x for x in (capture / f).read_text().splitlines() if x.strip()])
                for f in ("pipelines.jsonl", "devices.jsonl", "samplers.jsonl") if (capture / f).is_file())
    counted = sum(l["records"] for _, ls in loads for l in ls)
    problems = [f"{n}: the capture layer did not load" for n, ls in loads if not ls]
    problems += [f"{n}: {l['write_failures']} capture write failures" for n, ls in loads for l in ls if l["write_failures"]]
    if counted != len(modules) + lines:
        problems.append(f"the loads counted {counted} records; the capture holds {len(modules)} modules and {lines} lines")
    return {"loads": sum(len(ls) for _, ls in loads), "logs_without_a_load": [n for n, ls in loads if not ls],
            "records_counted": counted, "modules": len(modules), "lines": lines, "problems": problems,
            "ok": not problems and bool(loads)}


def load(args) -> int:
    run = Path(args.run)
    text, ctrl = Path(args.log).read_text(), Path(args.control_log).read_text()
    plan_ = json.loads(Path(args.plan).read_text())
    marker = marker_of(text, "SHADERS_LOAD_")
    counts = next((l for l in text.splitlines() if l.startswith("LOAD ")), "")
    ve, cve = validation_errors(text), validation_errors(ctrl)
    device = next((l[len("DEVICE "):] for l in text.splitlines() if l.startswith("DEVICE ")), None)
    nb, nm = len(plan_["border_variants"]), len(plan_["specialized"])
    want = f"LOAD shaders {len(plan_['shaders'])}/{len(plan_['shaders'])} variants {len(plan_['variants'])}/" \
           f"{len(plan_['variants'])} pairs {len(plan_['pairs'])}/{len(plan_['pairs'])} border {nb}/{nb} specialized {nm}/{nm}"
    # what SDL_gpu handed the driver (the capture layer on the load) must be what the load wrote, the
    # modules C3 compares: no module rewritten, added or lost on the way
    out = Path(args.out_dir)
    written = {hashlib.sha256(s.read_bytes()).hexdigest() for s in out.rglob("*.spv")}
    cap = Path(args.capture)
    handed = {s.name[len("module-"):-len(".spv")] for s in cap.glob("module-*.spv")} if cap.is_dir() else set()
    account = capture_account(cap, [args.log])
    handed_ok = account["ok"] and bool(written) and handed == written
    border_sites = []
    for line in text.splitlines():
        if line.startswith("BORDER ") and " unwrapped " in line:
            f = dict(x.split("=", 1) for x in line.split(" unwrapped ", 1)[1].split())
            border_sites.append({"variant": line.split()[1], **{k: int(v) for k, v in f.items()}})
    ok = (marker == "SHADERS_LOAD_OK" and counts == want and not ve and layer_loaded(text) and layer_loaded(ctrl)
          and sum(cve.values()) > 0 and not plan_["border_unmapped"] and handed_ok)
    return put(run, f"C2-{args.driver}", {
        "check": "C2", "driver": args.driver, "pass": ok, "device": device, "marker": marker, "counts": counts,
        "validation_layer_loaded": layer_loaded(text), "border_variants": plan_["border_variants"],
        "border_unmapped": plan_["border_unmapped"], "border_unwrapped": border_sites,
        "specialized": [l.split()[1] for l in text.splitlines() if l.startswith("SPECIALIZED ")],
        "validation_errors": ve, "control": {"what": "the load on SDL's default device (no SDL_GPUVulkanOptions)",
                                             "validation_layer_loaded": layer_loaded(ctrl),
                                             "validation_errors": cve, "seen": sum(cve.values()) > 0},
        "handed_to_the_driver": {"modules_written": len(written), "modules_captured": len(handed),
                                 "equal": handed == written, "capture": account},
        "summary": f"{marker}; {counts}; validation errors {sum(ve.values())} (layer loaded {layer_loaded(text)}); "
                   f"control {sum(cve.values())} seen; the driver received the {len(written)} modules written: "
                   f"{handed == written}"})


def canon(args) -> int:
    run = Path(args.run)
    gpl_text, lnk_text = Path(args.gpl).read_text(), Path(args.linked).read_text()
    gpl, lnk = last_json(gpl_text), last_json(lnk_text)
    account = capture_account(Path(args.capture_dir), args.captures)
    # the linked half compares every pair's pixel shader specialized at the pair's word with DXVK's frozen at
    # the word its pipeline gives: no pair may be compared as the base; every word DXVK's pipelines give is
    # the spec constant's default, 0 (each D3D9 pipeline's VkSpecializationInfo omits SpecId 5), or the
    # module declares no SpecId 5 and is compared as it is
    modes = lnk.get("sampler_modes") or {}
    ps_rows = sum(1 for k in lnk.get("failures", {}) if k.endswith(".ps")) if not lnk["ok"] else None
    sources = set(modes.get("word_sources") or [])
    linked_specialized = bool(modes) and modes.get("base_ps") == 0 and modes.get("specialized_ps", 0) > 0 \
        and modes.get("dxvk_words") == [0] and modes.get("frozen", 0) > 0 and "default" in sources \
        and sources <= {"default", "no SpecId 5"}
    ok = (gpl_text.strip().endswith("SPV_CANON_OK") and gpl["ok"] and gpl["shaders"] == 26 and gpl["dxvk_matched"] == gpl["dxvk_modules"]
          and lnk_text.strip().endswith("SPV_CANON_LINKED_OK") and lnk["ok"] and account["ok"] and linked_specialized
          and lnk["gpl"]["library_modules"] == gpl["dxvk_modules"] == lnk["gpl"]["equal_to_dump"])
    return put(run, f"C3-{args.driver}", {
        "check": "C3", "driver": args.driver, "pass": ok,
        "gpl": {"shaders": gpl["shaders"], "dxvk_modules": gpl["dxvk_modules"], "matched": gpl["dxvk_matched"],
                "classes": gpl["classes"], "failures": gpl["failures"]},
        "linked": {k: lnk[k] for k in ("linked_pipelines", "linked_module_pairs", "linked_keys", "linked_bgra_masks",
                                       "dxvk_internal_pipelines", "stageless_pipelines", "compared",
                                       "drawn_pair_entries", "drawn_shader_pairs", "gpl", "classes", "failures")},
        "capture": account,
        "sampler_modes": modes, "ps_failing": ps_rows,
        "criterion_notes": {
            "gpl": "The GPL half compares the base (the translator's output with the sampler-mode word read from "
                   "the state buffer, Variant{}) with DXVK's dump, as before the specialization: the identity at "
                   "the translator's output, which also holds DXVK's depth-compare and fetch4 paths that a "
                   "specialized module no longer carries.",
            "linked": "Repointed (I7b): from the module host 2 linked unspecialized to the pair's pixel shader "
                      "specialized at the pair's sampler-mode word (the module the device is to hand the driver "
                      "once it computes each draw's word, R2-draws; until then d9g_device.cpp translates the "
                      "base), compared with DXVK's linked module frozen at the word DXVK's pipeline gives and "
                      "folded by the pinned spirv-opt (test/spv_freeze.py, which refuses any result that is "
                      "not a pure deletion of its input). The check asserts that word is the spec constant's "
                      "default 0, SpecId 5 absent from every pipeline's VkSpecializationInfo (word_sources "
                      "'default'), or that DXVK's module declares no SpecId 5 and is compared unfrozen "
                      "(not_frozen); host 2's word must equal it. The two sides come from different "
                      "generators: dxbc-spirv's legalisation on host 2's, spirv-opt on DXVK's."},
        "summary": f"GPL {gpl['shaders']} shaders against {gpl['dxvk_modules']} DXVK modules: "
                   f"{'SPV_CANON_OK' if gpl['ok'] else 'SPV_CANON_FAIL'}; linked {lnk['linked_module_pairs']} module pairs "
                   f"({lnk['linked_keys']} with their BGRA masks), {lnk['compared']} compared: "
                   f"{'SPV_CANON_LINKED_OK' if lnk['ok'] else 'SPV_CANON_FAIL'} ({modes.get('specialized_ps')} pixel shaders "
                   f"specialized, {modes.get('frozen')} of DXVK's frozen at its words {modes.get('dxvk_words')} "
                   f"({', '.join(sorted(sources)) or 'none'}), unfrozen as DXVK declares no SpecId 5: "
                   f"{', '.join(modes.get('not_frozen') or []) or 'none'}; {modes.get('base_ps')} compared as the "
                   f"base); library modules "
                   f"{lnk['gpl']['equal_to_dump']} of {gpl['dxvk_modules']} dumped; capture records accounted "
                   f"{account['ok']}"})


UNBUILDABLE = {"lower_dot_off": "Invalid opcode FDot"}
# The one fault the game's corpus cannot exercise, and why: no pair of the streams needs a linkage
# rewrite (every pair's I/O is compatible), which the clean load's pairs must show. Any other fault
# that changes no module has stopped working, and fails.
UNEXERCISED_BY_CORPUS = {"linkage_skipped": "no pair of the streams needs a linkage rewrite",
                         "modes_slot_shifted": "every sampler-mode word of the streams is 0, which a shift of its "
                                               "registers leaves 0"}
# the faults that run through the load's pairs and the linked batch (test_shaders.sh: faults/<fault>/ and
# faults/<fault>.linked.txt): the linkage's, and the sampler-mode specialization's
LOAD_FAULTS = ("linkage_skipped", "modes_skipped", "modes_inverted", "modes_slot_shifted")
# what a failure line names: a function, a module property (capability, execution mode, decoration),
# a block member's place (Offset) or a descriptor's (bound at)
NAMES_A_PLACE = ("function", "capabilit", "execution mode", "decoration", "Offset", "is bound")


def canon_failing(text: str):
    try:
        rep = last_json(text)
        return rep, sorted(k for k in rep["failures"] if not k.startswith(("dxvk:", "host2:")))
    except (ValueError, KeyError):
        return None, None


def captured_pairs(text: str) -> set:
    """The '<pair>.<stage>' keys a linked batch reached (compared, or failed before it could compare)."""
    try:
        rep = last_json(text)
    except ValueError:
        return set()
    return set(rep.get("compared_keys", [])) | set(rep.get("failures", {}))


# ---- the mutation pass in the removed region (C3's controls): mutants of a sampling pixel shader's base
# inside the paths word 0 removes, which C3's GPL half (the canonical diff on the base) must fail and which
# the specialized module's pure deletion (C12) cannot see, since it holds none of them: the reason C3 keeps
# its GPL half on the unspecialized output
def spv_insts(data: bytes):
    import struct
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    insts, pos = [], 5
    while pos < len(words):
        wc = words[pos] >> 16
        if wc == 0:
            raise Refused("a zero-length instruction")
        insts.append(words[pos:pos + wc])
        pos += wc
    return words[:5], insts


def spv_pack(header, insts) -> bytes:
    import struct
    flat = header + [w for i in insts for w in i]
    return struct.pack("<%dI" % len(flat), *flat)


def removed_region_mutants(path) -> dict:
    """{name: the base module's bytes with one change inside the paths word 0 removes} for a base module
    that reads a sampler mode (DXVK's lowering: samplerMode(r) compared with 2 for the depth compare and
    with 3 for its clamp, the depth result broadcast to four components, the gather's D3D9 swizzle).
    Each pattern must be found: a base whose shape no longer has one raises Refused."""
    import spv_canon
    m = spv_canon.Module(path, "host2")
    const = {tuple(i.lits[0]): i.rid for i in m.insts if i.op == "OpConstant" and m.defs[i.rtype].op == "OpTypeInt"
             and i.lits and isinstance(i.lits[0], tuple)}
    fn = next((k for k, v in m.names.items() if v == "samplerMode"), None)
    mode = next((i.rid for i in m.insts if i.op == "OpFunctionCall" and i.ids[0] == fn), None)
    if fn is None or mode is None or not {(1,), (2,), (3,)} <= set(const):
        raise Refused(f"{path}: no samplerMode call to mutate around")
    u1, u2, u3 = const[(1,)], const[(2,)], const[(3,)]

    def op(i):
        return i[0] & 0xFFFF

    def at(insts, pred, what):
        k = next((k for k, i in enumerate(insts) if pred(i)), None)
        if k is None:
            raise Refused(f"{path}: no {what} to mutate")
        return k

    def threshold(insts):        # UGe(mode, 2) -> UGe(mode, 3): mode 2 no longer compares
        k = at(insts, lambda i: op(i) == 174 and i[3] == mode and i[4] == u2, "OpUGreaterThanEqual(mode, 2)")
        insts[k] = insts[k][:4] + [u3]

    def clamp_test(insts):       # IEqual(mode, 3) -> IEqual(mode, 1): the clamp taken at mode 1
        k = at(insts, lambda i: op(i) == 170 and i[3] == mode and i[4] == u3, "OpIEqual(mode, 3)")
        insts[k] = insts[k][:4] + [u1]

    def broadcast(insts):        # the depth result's .w from the comparison to the reference
        k = at(insts, lambda i: op(i) == 80 and len(i) == 7 and len(set(i[3:])) == 1, "a four-way broadcast")
        d = at(insts, lambda i: op(i) == 89, "OpImageSampleDrefImplicitLod")
        insts[k] = insts[k][:6] + [insts[d][5]]

    def swizzle(insts):          # the gather's D3D9 order z x y w -> y x y w
        k = at(insts, lambda i: op(i) == 81 and len(i) == 5 and i[4] == 2 and
               any(op(j) == 96 and j[2] == i[3] for j in insts), "an extract of the gather's .z")
        insts[k] = insts[k][:4] + [1]
    data = Path(path).read_bytes()
    out = {}
    for name, f in (("threshold", threshold), ("clamp_test", clamp_test), ("broadcast", broadcast), ("swizzle", swizzle)):
        header, insts = spv_insts(data)
        f(insts)
        out[name] = spv_pack(header, insts)
    return out


def removed_region(load_: Path, gpl: Path, reference_side: str = "dxvk") -> dict:
    """The mutation pass over every sampling pixel shader of the load (load/<ps>.json with a sampler whose
    mode it reads; the base load/<ps>.spv, its DXVK dump gpl/<debug name>.spv, its specialization
    load/specialized/<ps>.spv): four mutants each, every one valid SPIR-V (the pinned spirv-val), failed by
    the GPL comparison, and recorded whether the pure deletion sees it. reference_side "host2" reads the
    dump as a host-2 module (a test's stand-in for DXVK's)."""
    import tempfile
    import spv_canon
    import spv_specialize
    rows, problems, shaders = {}, [], 0
    with tempfile.TemporaryDirectory() as td:
        for refl in sorted(load_.glob("ps*.json")):
            r = json.loads(refl.read_text())
            if not r.get("sampler_registers") or not r.get("sampler_mode_slots"):
                continue
            shaders += 1
            base, spec, dump = load_ / f"{refl.stem}.spv", load_ / "specialized" / f"{refl.stem}.spv", gpl / f"{r['debug_name']}.spv"
            try:
                muts = removed_region_mutants(base)
            except Refused as e:
                problems.append(str(e))
                continue
            word = spv_specialize.specialized_word(json.loads((load_ / "specialized" / refl.name).read_text()).get("variant"))
            for name, data in muts.items():
                q = Path(td) / f"{refl.stem}.{name}.spv"
                q.write_bytes(data)
                invalid = spirv_val([q])
                gpl_ok = spv_canon.compare(dump, q, 0, reference_side=reference_side)["ok"]
                blind = spv_specialize.check(q, spec, word)["ok"] if word is not None else None
                rows[f"{refl.stem}.{name}"] = {"valid": not invalid, "gpl_half_fails": not gpl_ok, "pure_deletion_blind": blind}
                if invalid or gpl_ok:
                    problems.append(f"{refl.stem}.{name}: valid {not invalid}, failed by the GPL half {not gpl_ok}")
    return {"mutants": len(rows), "shaders": shaders, "ok": len(rows) == 4 * shaders and not problems, "problems": problems,
            "caught_by_the_gpl_half": sum(v["gpl_half_fails"] for v in rows.values()),
            "seen_by_the_pure_deletion": sum(1 for v in rows.values() if v["pure_deletion_blind"] is False),
            "rows": rows}


def controls(args) -> int:
    """C3's controls: each translator fault must fail the batch on exactly the objects whose module
    it changed, naming each, and every fault but the linkage's must change one; the linkage fault runs
    through the load's pairs (--linkage-load) and must fail the linked batch on exactly the pairs it
    changed, and where it changes none every clean pair must be compatible and unrewritten (the
    synthetic pair of test_s5_d3d9gpu_shader.py holds it then); R0's modules must fail every object."""
    run = Path(args.run)
    clean, faults = Path(args.clean), Path(args.faults)
    out, ok = {}, True
    for f in args.names:
        d = faults / f
        if f in UNBUILDABLE:
            # dxbc-spirv's SPIR-V builder has no instruction for the IR this fault leaves (lowerDot off
            # leaves FDot, spirv_builder.cpp's "Invalid opcode"): the translator stops loudly, it cannot
            # produce a module that differs silently
            log = (faults / f"{f}.compile.txt").read_text()
            caught = UNBUILDABLE[f] in log and "SHADERC_OK" not in log
            out[f] = {"changed": None, "failing": None, "caught": caught, "named": caught, "exercised_by_corpus": True,
                      "how": f"the build stops: {UNBUILDABLE[f]}"}
            ok = ok and caught
            continue
        if f in LOAD_FAULTS:
            ld = Path(args.linkage_load) if f == "linkage_skipped" else faults / f
            pairs = sorted((clean / "pairs").glob("*.json"))
            changed = sorted(f"{p.stem}.ps" for p in pairs
                             if not (ld / "pairs" / f"{p.stem}.ps.spv").is_file()
                             or (ld / "pairs" / f"{p.stem}.ps.spv").read_bytes() != (clean / "pairs" / f"{p.stem}.ps.spv").read_bytes())
            text = (Path(args.linkage_canon) if f == "linkage_skipped" else faults / f"{f}.linked.txt").read_text()
            rep, failing = canon_failing(text)
            failing = sorted(k for k in (failing or []) if not k.startswith(("capture", "gpl")))
            records = [json.loads(p.read_text()) for p in pairs]
            all_compatible = bool(records) and all(r["compatible"] and not r["io_rewritten"] for r in records)
            if changed:
                # a pair DXVK did not link (one no stream draws) is not in the linked batch: what it must
                # fail on is the changed pairs DXVK's capture holds
                captured = sorted(k for k in changed if k in captured_pairs(text))
                caught = text.strip().endswith("SPV_CANON_FAIL") and failing == captured and bool(captured)
                named = caught
            else:
                caught = named = text.strip().endswith("SPV_CANON_LINKED_OK") and (all_compatible or f != "linkage_skipped")
            out[f] = {"changed": changed, "failing": failing, "caught": caught, "named": named,
                      "exercised_by_corpus": bool(changed), "pairs": len(records), "pairs_compatible": all_compatible,
                      "how": "through the load's pairs and the linked batch"}
            ok = ok and caught and named
            continue
        changed = sorted(p.stem for p in clean.glob("*.spv")
                         if not (d / p.name).is_file() or (d / p.name).read_bytes() != p.read_bytes())
        text = (faults / f"{f}.canon.txt").read_text()
        rep, failing = canon_failing(text)
        caught = bool(changed) and text.strip().endswith("SPV_CANON_FAIL") and failing == changed
        named = caught and all(any(w in x for w in NAMES_A_PLACE for x in rep["failures"][k]) for k in failing)
        out[f] = {"changed": changed, "failing": failing, "caught": caught, "named": named,
                  "exercised_by_corpus": bool(changed)}
        ok = ok and caught and named
    r0 = (Path(args.r0c)).read_text()
    _, r0failing = canon_failing(r0)
    r0failing = r0failing or []
    r0ok = r0.strip().endswith("SPV_CANON_FAIL") and len(r0failing) == 26
    unexercised = sorted(f for f, v in out.items() if not v["exercised_by_corpus"])
    region = removed_region(clean, Path(args.gpl))
    ok = (ok and r0ok and set(unexercised) <= set(UNEXERCISED_BY_CORPUS) and set(UNEXERCISED_BY_CORPUS) <= set(out)
          and region["ok"])
    return put(run, f"C3-controls-{args.driver}", {
        "check": "C3 controls", "driver": args.driver, "pass": ok, "faults": out,
        "r0c": {"failing": len(r0failing), "pass": r0ok},
        "removed_region": {k: v for k, v in region.items() if k != "rows"},
        "criterion_note": "A strengthening (I7b): the mutation pass in the removed region, four mutants of each "
                          "sampling pixel shader's base inside the paths word 0 removes, each of which the GPL "
                          "half must fail; the pure deletion of the specialized module (C12) is recorded as not "
                          "seeing them, which is why the GPL half stays on the unspecialized output.",
        "unexercised_by_corpus": {f: UNEXERCISED_BY_CORPUS.get(f, "a fault that changed no module: it has stopped working")
                                  for f in unexercised},
        "summary": f"{sum(v['caught'] for v in out.values() if v['exercised_by_corpus'])} of "
                   f"{len(out) - len(unexercised)} corpus-exercised faults caught on exactly the objects they changed; "
                   f"not exercised by the corpus: {', '.join(unexercised) or 'none'}"
                   f"{' (every pair compatible, the linked batch unchanged)' if out.get('linkage_skipped', {}).get('caught') and 'linkage_skipped' in unexercised else ''}; "
                   f"R0's modules fail {len(r0failing)} of 26; the removed region's {region['mutants']} mutants: "
                   f"{region['caught_by_the_gpl_half']} failed by the GPL half, {region['seen_by_the_pure_deletion']} "
                   f"seen by the pure deletion"})


def r0c(args) -> int:
    """R0's modules (next/D/r0/r0c: dxbc-spirv's own options, fp16 and DenormFlushToZero) in host 2's
    place: each object's module replaced by R0's, beside host 2's own reflection of the object."""
    src, load_, out = Path(args.r0c), Path(args.load), Path(args.out)
    n = 0
    for refl in sorted(load_.glob("*.json")):
        spv = src / f"{refl.stem}.spv"
        if not spv.is_file():
            raise Refused(f"R0 has no module {spv}")
        (out / f"{refl.stem}.spv").write_bytes(spv.read_bytes())
        (out / refl.name).write_bytes(refl.read_bytes())
        n += 1
    print(f"   R0's modules in host 2's place: {n}")
    return 0 if n == 26 else 1


VS_BORDER = "vs_3_0\ndcl_position v0\ndcl_texcoord0 v1\ndcl_position o0\ndcl_texcoord0 o1\nmov o0, v0\nmov o1, v1\n"
PS_BORDER = "ps_3_0\ndcl_texcoord0 v0\ndcl_2d s0\ntexld r0, v0, s0\nmov oC0, r0\n"


# the sampler-mode draws (C13): four samplers, each one's first channel into one channel of the output
PS_MODES4 = ("ps_3_0\ndcl_texcoord0 v0\ndcl_2d s0\ndcl_2d s1\ndcl_2d s2\ndcl_2d s3\ntexld r0, v0, s0\n"
             "texld r1, v0, s1\ntexld r2, v0, s2\ntexld r3, v0, s3\nmov r4.x, r0.x\nmov r4.y, r1.x\nmov r4.z, r2.x\n"
             "mov r4.w, r3.x\nmov oC0, r4\n")

# the null-stream probe (C7): position, and a texcoord the declaration lacks passed out as a colour
VS_NULL = "vs_3_0\ndcl_position v0\ndcl_texcoord1 v1\ndcl_position o0\ndcl_color o1\nmov o0, v0\nmov o1, v1\n"
PS_NULL = "ps_3_0\ndcl_color v0\nmov oC0, v0\n"


def synth(args) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sm3_asm
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in (("border_vs", VS_BORDER), ("border_ps", PS_BORDER), ("null_vs", VS_NULL), ("null_ps", PS_NULL),
                       ("modes_ps4", PS_MODES4)):
        (out / f"{name}.bin").write_bytes(sm3_asm.assemble(text))
    return 0


def probe_value(args) -> int:
    """The value DXVK's null binding read, as the SDL leg's buffer takes it: each channel of the
    DXVK leg's centre pixel over 255, in R, G, B, A order (the pixel is B, G, R, A); --complement
    gives 1 less each, the control's value."""
    for line in Path(args.dxvk).read_text().splitlines():
        if line.startswith("{"):
            b, g, r, a = json.loads(line)["bgra"]
            v = [x / 255 for x in (r, g, b, a)]
            if args.complement:
                v = [1 - x for x in v]
            print(" ".join(repr(x) for x in v))
            return 0
    raise Refused(f"{args.dxvk} holds no probe line")


# null_probe.cpp's and shaderc probe-null's clear colour (R 0x7F, G 0x3F, B 0x1F, A 1) as the
# B, G, R, A bytes the probes print: a leg that reads it drew nothing
PROBE_CLEAR_BGRA = [31, 63, 127, 255]


def nullprobe(args) -> int:
    run = Path(args.run)

    def read(path):
        text = Path(path).read_text()
        rows = [json.loads(l) for l in text.splitlines() if l.startswith("{")]
        return (rows[0] if rows else None), marker_of(text, "NULL_PROBE_") == "NULL_PROBE_OK", text
    dx, dok, _ = read(args.dxvk)
    sd, sok, stext = read(args.sdl)
    ct, cok, ctext = read(args.control)
    # the second host's leg drawn with the base pixel shader (no validation layer): the same pixels
    bs, bok, _ = read(args.sdl_base) if args.sdl_base else (None, False, "")
    specialized = bool(sd) and "sampler_modes=none" not in sd.get("pixel_shader_variant", "sampler_modes=none")
    base_is_base = bool(bs) and "sampler_modes=none" in bs.get("pixel_shader_variant", "")
    frames_equal = bool(sd and bs and bok) and sd["bgra"] == bs["bgra"] and sd["uniform"] == bs["uniform"]
    # the SDL legs run under the validation layer (the user's second rulings have host 2's debug runs use
    # it): it must be loaded, and quiet by the agent's amendment of that ruling (RULINGS-2-AMENDMENTS.md)
    ve = validation_errors(stext + ctext)
    layer = layer_loaded(stext) and layer_loaded(ctext)
    drew = bool(dx and sd and ct) and PROBE_CLEAR_BGRA not in (dx["bgra"], sd["bgra"], ct["bgra"])
    ok = (bool(dx and sd and ct and dok and sok and cok) and dx["bgra"] == sd["bgra"] and ct["bgra"] != dx["bgra"]
          and drew and layer and not ve and specialized and base_is_base and frames_equal)
    return put(run, f"C7-{args.driver}", {
        "check": "C7", "driver": args.driver, "pass": ok, "dxvk": dx, "sdlgpu": sd, "control": ct,
        "measured_rgba": None if not dx else [dx["bgra"][2], dx["bgra"][1], dx["bgra"][0], dx["bgra"][3]],
        "every_leg_drew": drew, "validation_layer_loaded": layer, "validation_errors": ve,
        "sampler_modes": {"specialized": specialized, "base_leg": bs, "base_is_the_base": base_is_base,
                          "frames_equal_to_the_base": frames_equal},
        "summary": f"DXVK's null binding reads B,G,R,A {dx and dx['bgra']}; the second host's buffer holding it gives "
                   f"{sd and sd['bgra']}; the control (its complement) gives {ct and ct['bgra']}; every leg drew {drew}; "
                   f"validation errors {sum(ve.values())} (layer loaded {layer}); the specialized pixel shader's pixels "
                   f"equal the base's {frames_equal}"})


def border(args) -> int:
    run = Path(args.run)
    text = Path(args.log).read_text()
    data = json.loads(Path(args.json).read_text()) if Path(args.json).is_file() else {"cases": [], "pass": False}
    ve = validation_errors(text)
    point = [c for c in data["cases"] if c["filter"] == "point"]
    linear = [c for c in data["cases"] if c["filter"] == "linear"]
    # point filtering is exact; linear filtering's residue must be smaller than the emulation's absence
    # (the control): fewer differing channels and a smaller largest difference, case by case
    point_exact = len(point) == 2 and all(c["emulation_differing_channels"] == 0 for c in point)
    linear_bounded = len(linear) == 2 and all(c["emulation_differing_channels"] < c["control_differing_channels"] and
                                              c["emulation_max_delta"] < c["control_max_delta"] for c in linear)
    controls_seen = len(data["cases"]) == 4 and all(c["control_differing_channels"] > 0 for c in data["cases"])
    # every leg's frame drawn with the base pixel shader (no validation layer) equals the specialized one's
    base = json.loads(Path(args.base_json).read_text()) if args.base_json and Path(args.base_json).is_file() else {"cases": []}
    specialized = "sampler_modes=none" not in data.get("pixel_shader_variant", "sampler_modes=none")
    base_is_base = "sampler_modes=none" in base.get("pixel_shader_variant", "")
    legs = ("patch_sha256", "emulation_sha256", "control_sha256")
    pairs = {(c["filter"], c["colour"]): c for c in base["cases"]}
    frames_equal = len(base["cases"]) == len(data["cases"]) == 4 and all(
        (c["filter"], c["colour"]) in pairs and all(c.get(k) and c.get(k) == pairs[(c["filter"], c["colour"])].get(k) for k in legs)
        for c in data["cases"])
    ok = (marker_of(text, "BORDER_FALLBACK_") == "BORDER_FALLBACK_OK" and data["pass"] and point_exact and linear_bounded
          and controls_seen and layer_loaded(text) and specialized and base_is_base and frames_equal)
    return put(run, f"C8-{args.driver}", {
        "check": "C8", "driver": args.driver, "pass": ok, "cases": data["cases"], "validation_errors": ve,
        "validation_layer_loaded": layer_loaded(text),
        "sampler_modes": {"specialized": data.get("pixel_shader_variant"), "base": base.get("pixel_shader_variant"),
                          "frames_equal_to_the_base": frames_equal, "legs_compared": len(base["cases"]) * len(legs)},
        "residue": {"linear_differing_channels": [c["emulation_differing_channels"] for c in linear],
                    "linear_max_delta": max((c["emulation_max_delta"] for c in linear), default=None),
                    "below_the_control": linear_bounded},
        "summary": f"point: {sum(c['emulation_differing_channels'] for c in point)} channels differ from the patch's sampler; "
                   f"linear: {[c['emulation_differing_channels'] for c in linear]} differ, max delta "
                   f"{max((c['emulation_max_delta'] for c in linear), default=None)} (below the control's "
                   f"{[c['control_differing_channels'] for c in linear]}, max {max((c['control_max_delta'] for c in linear), default=None)}: "
                   f"{linear_bounded}); control differs {[c['control_differing_channels'] > 0 for c in data['cases']]}; "
                   f"validation errors {ve} (layer loaded {layer_loaded(text)}); every frame of the specialized pixel "
                   f"shader equals the base's {frames_equal}"})



# ---- the sampler-mode specialization (I7b): C12 (each specialized module against its base), C11 (Metal's
# texture types) and C13 (the GPU draws at every mode)
SPECIALIZATION_FAULTS = ("modes_skipped", "modes_inverted")   # each must fail C12 on every sampling shader


def border_label(v: dict) -> str:
    """shaderc load's name of a border variant (variants/<label>.spv)."""
    return f"{v['shader']}.border-{v['border_u']}-{v['border_v']}-{v['border_linear']}"


def bases(args) -> int:
    """Host 2's base (the word off) of every specialized module the load wrote but its objects: each pair's
    pixel shader linked against its vertex shader (OUT/<pair>.spv) and each border variant
    (OUT/<label>.spv), built by d3d9gpu_shaderc on the driver's facts, no GPU."""
    plan_ = json.loads(Path(args.plan).read_text())
    out, named = Path(args.out), Path(args.named)
    out.mkdir(parents=True, exist_ok=True)
    bad = []
    for p in plan_["pairs"]:
        name = pair_name(p)
        r = subprocess.run([args.shaderc, "link", args.facts, str(out), str(named / f"{p['vs']}.bin"),
                            str(named / f"{p['ps']}.bin"), name], capture_output=True, text=True)
        if r.returncode:
            bad.append(f"{name}: {r.stdout.strip()} {r.stderr.strip()}")
    for v in plan_["border_variants"]:
        d = out / "border" / border_label(v)
        d.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([args.shaderc, "compile", args.facts, str(d), "--border-u", str(v["border_u"]), "--border-v",
                            str(v["border_v"]), "--border-linear", str(v["border_linear"]), str(named / f"{v['shader']}.bin")],
                           capture_output=True, text=True)
        if r.returncode:
            bad.append(f"{border_label(v)}: {r.stdout.strip()} {r.stderr.strip()}")
    for b in bad:
        print(f"   base: {b}", file=sys.stderr)
    print(f"   bases: {len(plan_['pairs'])} pairs' pixel shaders linked, {len(plan_['border_variants'])} border variants, "
          f"{len(bad)} failed")
    return 1 if bad else 0


def pair_name(p: dict) -> str:
    """The plan's name of a pair (plan() names them so)."""
    if "name" in p:
        return p["name"]
    v, ps = p["vs"][2:], p["ps"][2:]
    return (f"pair{v}" if v == ps else f"pair{v}x{ps}") + (f".bgra{p['bgra']}" if p["bgra"] else "")


def spirv_val(paths) -> dict:
    """{module: the pinned spirv-val's complaint} of the modules it refuses (Vulkan 1.3, scalar layout)."""
    import shader_tools
    tool = shader_tools.pinned("spirv-val")
    bad = {}
    for q in paths:
        r = subprocess.run([str(tool), "--target-env", "vulkan1.3", "--scalar-block-layout", str(q)], capture_output=True,
                           text=True, env=shader_tools.tool_env())
        if r.returncode:
            bad[str(q)] = (r.stdout + r.stderr).strip()[-300:]
    return bad


def variant_fields(key: str) -> dict:
    """A variant key (d9g_shader.cpp Variant::key: "bgra=0;border_u=0x60;...;sampler_modes=none") as
    {field: int, or None for none}."""
    out = {}
    for part in (key or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = None if v == "none" else int(v, 0)
    return out


def pair_records(load_: Path) -> dict:
    """{pair name: its sa2-d3d9gpu-shader-pair/v1 record} of a load's pairs directory."""
    out = {}
    for pj in sorted((load_ / "pairs").glob("*.json")):
        r = json.loads(pj.read_text())
        if r.get("schema") == "sa2-d3d9gpu-shader-pair/v1":
            out[pj.stem] = r
    return out


def same_build(want: dict, got: dict, what: str) -> list:
    """The problems where a base was not built from the same translator and profile as its module."""
    return [f"{what}: its base's {k} is {got.get(k)}, the module's {want.get(k)}"
            for k in ("translator", "profile_sha256") if got.get(k) != want.get(k)]


def sweep_words(slots: list, seed: str) -> list:
    """The words the game's sweep specializes a pixel shader at, over the registers whose mode it reads:
    0; each of modes 1, 2 and 3 on each register alone; each mode on every register at once; and, for a
    shader reading more than one register, 12 more words drawn from a generator seeded by its name (as many
    as there are, where its registers leave fewer)."""
    import random
    words = {0}
    for r in slots:
        for m in (1, 2, 3):
            words.add(m << 2 * r)
    for m in (1, 2, 3):
        words.add(sum(m << 2 * r for r in slots))
    rng = random.Random(seed)
    mixed = set()
    want = min(12, 4 ** len(slots) - len(words)) if len(slots) > 1 else 0   # two registers leave 6 words
    while len(mixed) < want:
        w = sum(rng.randrange(4) << 2 * r for r in slots)
        if w not in words:
            mixed.add(w)
    return sorted(words | mixed)


def sweep(shaderc: str, facts: str, named: Path, names: list, out: Path) -> dict:
    """The game's pixel shaders specialized at every sweep_words word on the CPU (d3d9gpu_shaderc compile on
    the driver's facts): each compiles (the translator's own post-condition), is valid SPIR-V, is a pure
    deletion of its base compiled the same way, and keeps a depth-compare sample on exactly the registers
    at mode 2 or 3 and a gather on exactly those at 1."""
    import spv_specialize
    base = out / "base"
    base.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([shaderc, "compile", facts, str(base), *[str(named / f"{n}.bin") for n in names]],
                       capture_output=True, text=True)
    if r.returncode:
        return {"ok": False, "rows": 0, "problems": [f"the bases did not compile: {r.stdout[-300:]} {r.stderr[-300:]}"]}
    rows, problems, words_by_shader = 0, [], {}
    for n in names:
        b = json.loads((base / f"{n}.json").read_text())
        slots = [r for r in range(16) if b["sampler_mode_slots"] >> r & 1]
        words = sweep_words(slots, n)
        words_by_shader[n] = len(words)
        for w in words:
            d = out / n / f"{w:#x}"
            d.mkdir(parents=True, exist_ok=True)
            c = subprocess.run([shaderc, "compile", facts, str(d), "--sampler-modes", hex(w), str(named / f"{n}.bin")],
                               capture_output=True, text=True)
            rows += 1
            if c.returncode:
                problems.append(f"{n} at {w:#x}: the translator refused it: {c.stdout.strip()[-200:]}")
                continue
            spv = d / f"{n}.spv"
            bad = spirv_val([spv])
            if bad:
                problems.append(f"{n} at {w:#x}: spirv-val: {next(iter(bad.values()))[-200:]}")
            rep = spv_specialize.check(base / f"{n}.spv", spv, w)
            if not rep["ok"]:
                problems.append(f"{n} at {w:#x}: not a pure deletion: {'; '.join(rep['problems'])[:300]}")
            sr = json.loads((d / f"{n}.json").read_text())
            mode = {r: (w >> 2 * r) & 3 for r in slots}
            want_d = sum(1 << r for r in slots if mode[r] >= 2 and b["depth_compare_registers"] >> r & 1)
            want_g = sum(1 << r for r in slots if mode[r] == 1 and b["gather_registers"] >> r & 1)
            if (sr["depth_compare_registers"], sr["gather_registers"]) != (want_d, want_g):
                problems.append(f"{n} at {w:#x}: depth compare {sr['depth_compare_registers']:#x} and gather "
                                f"{sr['gather_registers']:#x}, the word's {want_d:#x} and {want_g:#x}")
    return {"ok": not problems and rows > 0, "rows": rows, "words_per_shader": words_by_shader, "problems": problems}


def specialize(args) -> int:
    """C12: every specialized module is valid SPIR-V (the pinned spirv-val) and a pure deletion of host 2's
    own base under its word (test/spv_specialize.py): the load's specialized pixel shaders against its
    objects, every pair's pixel shader against its base linked the same way, every border variant against
    the variant with the word off (each base built by the same translator and profile from the same
    bytecode), and C13's modules (words with every mode, mixed ones too) against C13's bases. The game's
    pixel shaders at words with every mode (sweep: each mode on each register alone, on all at once, and
    12 mixed words per shader reading more than one register, on the CPU with the driver's facts) hold the
    same, and keep exactly the depth-compare samples and gathers their words take. Its controls: the load
    under each SPECIALIZATION_FAULTS fault (the word ignored; each mode's depth bit flipped) must fail the
    batch on every pixel shader that reads a mode, and only those."""
    import spv_specialize
    run, load_, base = Path(args.run), Path(args.load), Path(args.bases)
    plan_ = json.loads(Path(args.plan).read_text())
    modes_mods = sorted(Path(args.modes_modules).glob("*.spv"))
    modules = sorted((load_ / "specialized").glob("*.spv")) + sorted((load_ / "pairs").glob("*.ps.spv")) + \
        [load_ / "variants" / f"{border_label(v)}.spv" for v in plan_["border_variants"]] + modes_mods
    invalid = spirv_val(modules)
    objects = spv_specialize.batch(load_, load_ / "specialized", len(plan_["specialized"]))
    rows, problems = {}, []
    for name, pr in pair_records(load_).items():
        word = spv_specialize.specialized_word(pr["ps"].get("variant"))
        if word is None:
            problems.append(f"{name}: its pixel shader is not specialized")
            continue
        link = json.loads((base / f"{name}.json").read_text()) if (base / f"{name}.json").is_file() else {}
        obj = json.loads((load_ / f"{pr['ps']['name']}.json").read_text())
        problems += same_build(pr, link, name)
        if link.get("ps_bytecode_sha256") != obj.get("bytecode_sha256") or \
                variant_fields(link.get("ps_variant")).get("sampler_modes", 0) is not None:
            problems.append(f"{name}: its base is not {pr['ps']['name']}'s bytecode with the word off")
        rows[name] = spv_specialize.check(base / f"{name}.spv", load_ / "pairs" / f"{name}.ps.spv", word)
    for v in plan_["border_variants"]:
        lab = border_label(v)
        bref = json.loads((base / "border" / lab / f"{v['shader']}.json").read_text())
        obj = json.loads((load_ / f"{v['shader']}.json").read_text())
        f = variant_fields(bref.get("variant"))
        problems += same_build(obj, bref, lab)
        if bref.get("bytecode_sha256") != obj.get("bytecode_sha256") or f.get("sampler_modes", 0) is not None or \
                (f.get("border_u"), f.get("border_v"), f.get("border_linear")) != (v["border_u"], v["border_v"], v["border_linear"]):
            problems.append(f"{lab}: its base is not {v['shader']}'s border variant with the word off")
        rows[lab] = spv_specialize.check(base / "border" / lab / f"{v['shader']}.spv", load_ / "variants" / f"{lab}.spv",
                                         v["sampler_modes"])
    problems += [f"{k}: {'; '.join(r['problems'])}" for k, r in rows.items() if not r["ok"]]
    # C13's modules against C13's bases (the --unspecialized leg's modules: the same bytecode, the device's profile)
    synthetic = spv_specialize.batch(Path(args.modes_base), Path(args.modes_modules), len(modes_mods))
    sampling = sorted(k for k, r in objects["checked"].items() if r.get("word_calls_folded"))
    controls = {}
    for f in SPECIALIZATION_FAULTS:
        d = Path(args.faults) / f / "specialized"
        rep = spv_specialize.batch(load_, d, len(plan_["specialized"])) if d.is_dir() else {"checked": {}, "ok": True}
        failing = sorted(k for k, r in rep["checked"].items() if not r["ok"])
        controls[f] = {"failing": failing, "fails_as_required": not rep["ok"] and failing == sampling and bool(sampling)}
    pixel = [o["name"] for o in plan_["shaders"] if o["stage"] == "pixel"]
    sw = sweep(args.shaderc, args.facts, Path(args.named), pixel, Path(args.sweep_out))
    ok = (not invalid and objects["ok"] and not problems and len(rows) == len(plan_["pairs"]) + len(plan_["border_variants"])
          and synthetic["ok"] and bool(modes_mods) and sw["ok"] and all(c["fails_as_required"] for c in controls.values()))
    synthetic_words = sorted({r["word"] for r in synthetic["checked"].values()})
    return put(run, f"C12-{args.driver}", {
        "check": "C12", "driver": args.driver, "pass": ok, "modules_validated": len(modules), "spirv_val_refused": invalid,
        "objects": {"checked": objects["modules"], "problems": objects["problems"],
                    "sampling": sampling, "dead_blocks": sum(r.get("blocks_dead", 0) for r in objects["checked"].values()),
                    "calls_folded": sum(r.get("word_calls_folded", 0) for r in objects["checked"].values())},
        "pairs_and_border_variants": {"checked": len(rows), "problems": problems},
        "synthetic": {"checked": synthetic["modules"], "words": synthetic_words, "problems": synthetic["problems"]},
        "sweep": {"rows": sw["rows"], "words_per_shader": sw.get("words_per_shader"), "problems": sw["problems"][:20],
                  "problem_count": len(sw["problems"])},
        "controls": controls,
        "criterion_note": "A strengthening (I7b): host 2's sampler-mode specialization is held to its own base, "
                          "not to DXVK's module stripped the same way, which hides a fault the strip shares; and "
                          "at words with every mode, on the game's pixel shaders (the sweep) and on C13's.",
        "summary": f"{len(modules)} specialized modules, {len(invalid)} refused by spirv-val; {objects['modules']} pixel "
                   f"shaders, {len(rows)} pairs and border variants and {synthetic['modules']} of C13's (words "
                   f"{', '.join(hex(w) for w in synthetic_words)}) a pure deletion of their base: "
                   f"{objects['ok'] and not problems and synthetic['ok']}; the sweep: {len(pixel)} game pixel shaders at "
                   f"{sw['rows']} words with every mode, {len(sw['problems'])} problems; controls "
                   f"{', '.join(f'{k} fails {len(v['failing'])}' for k, v in controls.items())} of the {len(sampling)} "
                   f"that read a mode"})


def metal(args) -> int:
    """C11's conversion and texture-type clause (test/metal_check.py): every specialized sampling pixel
    shader of the load (the objects, each pair's pixel shader and each border variant, typed by the
    object's reflection at the word its variant names), and the sampler-mode draws' modules (C13: words with
    every mode, every one of its cases), convert through shadercross and spirv-cross with each texture typed
    by its register's mode; every base fails it as required where its word leaves a colour register (the
    objects', the pairs' and border variants' bases, C13's --unspecialized modules), and so does the load
    under modes_inverted."""
    import metal_check
    run = Path(args.run)
    load_, base = Path(args.load), Path(args.bases)
    plan_ = json.loads(Path(args.plan).read_text())
    sampling = sum(1 for o in plan_["shaders"] if o["stage"] == "pixel" and
                   json.loads((load_ / "specialized" / f"{o['name']}.json").read_text()).get("sampler_registers"))
    entries = metal_check.entries_of(load_ / "specialized", load_)
    for name, pr in pair_records(load_).items():
        refl = json.loads((load_ / "specialized" / f"{pr['ps']['name']}.json").read_text())
        word = metal_check.word_of(pr["ps"].get("variant"))
        if refl.get("sampler_registers") and word is not None:
            entries.append((f"{name}.ps", load_ / "pairs" / f"{name}.ps.spv", refl, word, base / f"{name}.spv"))
    for v in plan_["border_variants"]:
        lab = border_label(v)
        refl = json.loads((load_ / "specialized" / f"{v['shader']}.json").read_text())
        entries.append((lab, load_ / "variants" / f"{lab}.spv", refl, v["sampler_modes"],
                        base / "border" / lab / f"{v['shader']}.spv"))
    sampling_pairs = sum(1 for e in entries if e[0].endswith(".ps"))
    game = metal_check.batch_entries(entries, sampling + sampling_pairs + len(plan_["border_variants"]), None, str(load_))
    cases = len(json.loads(Path(args.modes_json).read_text()).get("cases", [])) if Path(args.modes_json).is_file() else 0
    synthetic = metal_check.batch(args.modes_modules, args.modes_base, cases or None)
    depth_typed = sum(1 for m in synthetic["modules"].values() for t in m["types"].values() if t in metal_check.DEPTH_TYPES)
    inverted = Path(args.faults) / "modes_inverted" / "specialized"
    ctrl = metal_check.batch(inverted, None, sampling) if inverted.is_dir() else {"ok": True, "modules": {}}
    ctrl_fails = not ctrl["ok"] and all(m["problems"] for m in ctrl["modules"].values()) and len(ctrl["modules"]) == sampling
    controls_ok = all(m.get("control_fails_as_required") is not False for m in list(game["modules"].values()) +
                      list(synthetic["modules"].values()))
    ok = game["ok"] and synthetic["ok"] and bool(cases) and depth_typed > 0 and ctrl_fails and controls_ok
    return put(run, f"C11-{args.driver}", {
        "check": "C11", "driver": args.driver, "pass": ok,
        "game": {"checked": game["checked"], "problems": game["problems"], "controls_failing_as_required": game["controls"],
                 "objects": sampling, "pairs": sampling_pairs, "border_variants": len(plan_["border_variants"]),
                 "types": sorted({t for m in game["modules"].values() for t in m["types"].values()})},
        "synthetic": {"checked": synthetic["checked"], "cases": cases, "problems": synthetic["problems"],
                      "depth_typed": depth_typed, "controls_failing_as_required": synthetic["controls"],
                      "controls_not_applicable": sorted(k for k, m in synthetic["modules"].items()
                                                        if m.get("control_fails_as_required") is None)},
        "control_modes_inverted": {"fails_as_required": ctrl_fails, "checked": len(ctrl["modules"])},
        "converters": game["converters"], "carried_forward": game["carried_forward"],
        "criterion_note": "A strengthening (I7b) of the R1 plan's C11 (conversion and reflection counts): every "
                          "texture argument typed by its register's sampler mode, since SPIRV-Cross makes any image a "
                          "dead depth-compare sample touches depth2d. The mode is the word the variant names, not the "
                          "bound texture's: that the word is DXVK's is C3's linked half (host 2's word equal to "
                          "DXVK's pipeline's) and C13 (DXVK's rule on each texture, drawn under the validation "
                          "layer). Compiling and running on a Mac is carried forward.",
        "summary": f"{game['checked']} specialized sampling pixel shaders of the load ({sampling} objects, {sampling_pairs} "
                   f"pairs' pixel shaders, {len(plan_['border_variants'])} border variants) and {synthetic['checked']} of "
                   f"C13's {cases} cases converted by shadercross and spirv-cross, every texture typed by its mode: "
                   f"{game['ok'] and synthetic['ok']} ({depth_typed} depth-typed where the mode is a depth compare); "
                   f"bases failing as required {game['controls'] + synthetic['controls']} (not applicable, every "
                   f"register a depth compare: {sum(1 for m in synthetic['modules'].values() if m.get('control_fails_as_required') is None)}); "
                   f"modes_inverted fails {ctrl_fails}"})


DRAW_VUID = "VUID-vkCmdDraw-None-06479"   # a depth-compare sample of a view whose format cannot compare


def modes(args) -> int:
    """C13: the sampler-mode draws (d3d9gpu_shaderc modes) under the validation layer, every case as it
    must look (its modes the literal ones it names, DXVK's rule giving them), each mode 0 to 3 drawn and a
    shader of mixed modes; every case's frame equal to the base's drawn with the same word
    (--unspecialized); no validation error. Its must-fail control: the base leg, under the layer too, keeps
    DXVK's depth-compare sample on every colour register, which the layer must report at draw time
    (VUID-vkCmdDraw-None-06479), once for each register a case binds a colour texture to: the run's proof
    that draw-time errors are seen, which C2's control (pipeline creation) cannot give."""
    run = Path(args.run)
    text, btext = Path(args.log).read_text(), Path(args.base_log).read_text()
    data = json.loads(Path(args.json).read_text()) if Path(args.json).is_file() else {"cases": [], "pass": False}
    base = json.loads(Path(args.base_json).read_text()) if Path(args.base_json).is_file() else {"cases": []}
    ve, bve = validation_errors(text), validation_errors(btext)
    by = {c["name"]: c for c in base["cases"]}
    frames_equal = bool(data["cases"]) and len(base["cases"]) == len(data["cases"]) and all(
        c["name"] in by and c["frame_sha256"] == by[c["name"]]["frame_sha256"] and c["word"] == by[c["name"]]["word"]
        for c in data["cases"])
    seen = {sl["mode"] for c in data["cases"] for sl in c["slots"]}
    mixed = [c for c in data["cases"] if len({sl["mode"] for sl in c["slots"]}) >= 3]
    named = all(c.get("modes_as_named") is True for c in data["cases"])
    colour_slots = sum(1 for c in data["cases"] for sl in c["slots"] if sl["mode"] < 2)
    control_seen = layer_loaded(btext) and colour_slots > 0 and bve.get(DRAW_VUID) == colour_slots
    ok = (marker_of(text, "SAMPLER_MODES_") == "SAMPLER_MODES_OK" and data["pass"] and all(c["pass"] for c in data["cases"])
          and data.get("specialized") is True and base.get("specialized") is False and frames_equal and named
          and seen == {0, 1, 2, 3} and bool(mixed) and layer_loaded(text) and not ve and control_seen)
    return put(run, f"C13-{args.driver}", {
        "check": "C13", "driver": args.driver, "pass": ok, "cases": data["cases"], "validation_errors": ve,
        "validation_layer_loaded": layer_loaded(text), "modes_drawn": sorted(seen), "d24s8_format": data.get("d24s8_format"),
        "frames_equal_to_the_base": frames_equal, "modes_as_named": named,
        "control": {"what": "the base leg (--unspecialized) under the layer: DXVK's depth-compare sample on every "
                            "colour register, reported at draw time", "validation_layer_loaded": layer_loaded(btext),
                    "validation_errors": bve, "colour_registers": colour_slots, "seen": control_seen},
        "summary": f"{len(data['cases'])} cases, modes {sorted(seen)} drawn ({len(mixed)} of mixed modes), the modes "
                   f"each names {named}, every case as it must look {all(c['pass'] for c in data['cases'])}, every "
                   f"frame the base's {frames_equal}; validation errors {sum(ve.values())} (layer loaded "
                   f"{layer_loaded(text)}); control: the base under the layer reports {bve.get(DRAW_VUID, 0)} x "
                   f"{DRAW_VUID} for {colour_slots} colour registers: {control_seen}"})


TIMES_FIELDS = ("frame", "seq", "virtual_us", "guest_frames", "polls", "bgrx_hash")   # compare_hosts.py's


def times(path: Path):
    rows = path.read_text().splitlines()
    head = rows[0].split("\t")
    idx = [head.index(f) for f in TIMES_FIELDS]
    return [tuple(r.split("\t")[i] for i in idx) for r in rows[1:] if r.strip()]


def leg_exit(path) -> int | None:
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def frames(args) -> int:
    """C9: the capture layer is transparent. The layer's leg must have loaded it (its "vkcap:
    load=" line, records written, no write failure) and give the frames of the leg without it (and
    the registered reference's); the control build must have loaded too and run every frame, with at
    least one frame changed; the leg without the layer must not have loaded it; every leg exits 0."""
    run = Path(args.run)
    none, layer, ctrl = times(Path(args.none)), times(Path(args.layer)), times(Path(args.control))
    ref = times(Path(args.reference)) if args.reference else None
    logs = {leg: Path(p).read_text() for leg, p in (("none", args.none_log), ("layer", args.layer_log), ("control", args.control_log))}
    loads = {leg: capture_loads(text) for leg, text in logs.items()}
    loaded = {leg: bool(ls) and all(l["records"] > 0 and l["write_failures"] == 0 for l in ls) for leg, ls in loads.items()}
    exits = {leg: leg_exit(p) for leg, p in (("none", args.none_exit), ("layer", args.layer_exit), ("control", args.control_exit))}
    same = layer == none
    ref_same = ref is None or layer == ref
    ctrl_diff = sum(1 for a, b in zip(ctrl, none) if a[5] != b[5])
    ok = (same and ref_same and len(layer) > 0 and len(ctrl) == len(none) and ctrl_diff > 0
          and loaded["layer"] and loaded["control"] and not loads["none"] and all(e == 0 for e in exits.values()))
    for y in args.truncate or []:
        # the frames themselves are not kept (their hashes are): emptied, not deleted
        if Path(y).is_file():
            with open(y, "w"):
                pass
    return put(run, f"C9-{args.driver}", {
        "check": "C9", "driver": args.driver, "pass": ok, "frames": len(layer), "layer_equals_no_layer": same,
        "layer_equals_reference": None if ref is None else layer == ref, "reference": args.reference,
        "control_frames": len(ctrl), "control_frames_changed": ctrl_diff, "layer_loaded": loaded,
        "capture_loads": {leg: len(ls) for leg, ls in loads.items()}, "exits": exits,
        "summary": f"{len(layer)} frames with the layer (loaded {loaded['layer']}): equal to a run without it {same}"
                   f"{'' if ref is None else f', to the registered reference {layer == ref}'}; the control (loaded "
                   f"{loaded['control']}) ran {len(ctrl)} frames and changed {ctrl_diff}; exits {exits}"})


def corrupt(args) -> int:
    entries = sorted(Path(args.cache).rglob("*.spv"))
    if not entries:
        raise Refused(f"no cache entry under {args.cache}")
    e = entries[0]
    data = bytearray(e.read_bytes())
    data[-5] ^= 0xFF
    e.write_bytes(bytes(data))
    Path(args.out).write_text(f"corrupted {e}\n")
    return 0


def vk_env(args) -> int:
    """The Vulkan environment test_shaders.sh inherited (every VK_* variable, recorded before the script
    unsets them) and the validation layer's settings files where the layer reads one before
    VK_LAYER_SETTINGS_PATH (Vulkan-Utility-Libraries layer_settings_manager.cpp: $XDG_DATA_HOME, or
    $HOME/.local/share, then /vulkan/settings.d/vk_layer_settings.txt; the debug runs set
    VK_LAYER_SETTINGS_PATH to an empty directory of the run's own, so the working directory's, the layer's
    last resort, is never read): one present fails (exit 1), since a settings file can filter, redirect or
    mute the layer's messages."""
    import os
    # each file by the variable it is found through (a record names no one's home directory)
    if os.environ.get("XDG_DATA_HOME"):
        files = {"$XDG_DATA_HOME/vulkan/settings.d/vk_layer_settings.txt":
                 os.environ["XDG_DATA_HOME"] + "/vulkan/settings.d/vk_layer_settings.txt"}
    elif os.environ.get("HOME"):
        files = {"$HOME/.local/share/vulkan/settings.d/vk_layer_settings.txt":
                 os.environ["HOME"] + "/.local/share/vulkan/settings.d/vk_layer_settings.txt"}
    else:
        files = {}
    present = [k for k, f in files.items() if Path(f).is_file()]
    rec = {"inherited": {k: v for k, v in sorted(os.environ.items()) if k.startswith("VK_")},
           "cleared": "every inherited VK_* variable is unset before the first leg runs",
           "settings_files_checked": sorted(files), "settings_files_present": present}
    Path(args.out).write_text(json.dumps(rec, indent=1) + "\n")
    print(f"   Vulkan environment: {len(rec['inherited'])} VK_* variables inherited and cleared; settings files "
          f"present: {present or 'none'}")
    return 1 if present else 0


def first_specialized(args) -> int:
    """The first pixel shader whose reflection (--reflections) reads a sampler mode, and the plan's word
    for it, as "<pixel shader> <word>" (C6 compiles it after its base)."""
    plan_ = json.loads(Path(args.plan).read_text())
    for s in plan_["specialized"]:
        r = json.loads((Path(args.reflections) / f"{s['shader']}.json").read_text())
        if r.get("sampler_mode_slots"):
            print(s["shader"], s["sampler_modes"])
            return 0
    raise Refused("no pixel shader of the plan reads a sampler mode")


def first_variant(args) -> int:
    """The plan's first BGRA variant as "<vertex shader> <mask>" (C6 compiles it after its base)."""
    v = json.loads(Path(args.plan).read_text())["variants"]
    if not v:
        raise Refused("the plan has no BGRA variant")
    print(v[0]["shader"], v[0]["bgra"])
    return 0


def cache(args) -> int:
    """C6: the 26 objects' first pass misses once per distinct shader and hits the rest, the second
    hits all, a corrupted entry is detected and recompiled, a verify pass finds no difference, a root
    outside private/ is refused and left uncreated, and a variant compiled after its base (the plan's
    first BGRA variant) misses and gives the variant's module, not the base's: the key holds the
    variant."""
    run = Path(args.run)
    logs = [Path(p).read_text() for p in args.logs]
    stat = [next((l for l in t.splitlines() if l.startswith("CACHE ")), "") for t in logs[:4]]
    clean, out = Path(args.clean), Path(args.out_dir)
    equal = all((out / p.name).read_bytes() == p.read_bytes() for p in clean.glob("*.spv"))
    # the cache keys on the bytecode's sha256 (d9g_shader.h CacheKey): the 26 objects are fewer
    # distinct shaders, so the first pass misses once per distinct one and hits the rest
    n = len(list(clean.glob("*.spv")))
    distinct = len({json.loads(p.read_text())["bytecode_sha256"] for p in clean.glob("*.json")})
    want = [f"CACHE hits={n - distinct} misses={distinct} verified=0 corrupt=0", f"CACHE hits={n} misses=0 verified=0 corrupt=0",
            f"CACHE hits={n - 1} misses=1 verified=0 corrupt=1", f"CACHE hits={n} misses=0 verified={n} corrupt=0"]
    refused = "REFUSED" in logs[4] and "is not inside" in logs[4]
    left = Path(args.outside_root).exists()
    vstat = next((l for l in Path(args.variant_log).read_text().splitlines() if l.startswith("CACHE ")), "")
    vmod, vclean = Path(args.variant_module), Path(args.variant_clean)
    variant_ok = (vstat == "CACHE hits=0 misses=1 verified=0 corrupt=0" and vmod.is_file() and vclean.is_file()
                  and vmod.read_bytes() == vclean.read_bytes())
    # the sampler-mode word is the key's too: the pixel shader specialized after its base is a miss that
    # gives the specialized module, which is not the base's
    mstat = next((l for l in Path(args.modes_log).read_text().splitlines() if l.startswith("CACHE ")), "")
    mmod, mclean, mbase = Path(args.modes_module), Path(args.modes_clean), Path(args.modes_base)
    modes_ok = (mstat == "CACHE hits=0 misses=1 verified=0 corrupt=0" and mmod.is_file() and mclean.is_file()
                and mbase.is_file() and mmod.read_bytes() == mclean.read_bytes() and mmod.read_bytes() != mbase.read_bytes())
    ok = stat == want and refused and not left and equal and n == 26 and variant_ok and modes_ok
    return put(run, "C6", {"check": "C6", "pass": ok, "passes": stat, "want": want, "objects": n, "distinct": distinct,
                           "outside_refused": refused, "outside_root_left": left, "modules_equal_uncached": equal,
                           "variant_after_base": {"cache": vstat, "module_is_the_variants": variant_ok},
                           "sampler_modes_after_base": {"cache": mstat, "module_is_the_specialized_one": modes_ok},
                           "summary": "; ".join(stat) + f"; outside refused {refused}, left {left}; a variant after its "
                                                        f"base: {vstat or '(none)'}, its own module {variant_ok}; a "
                                                        f"sampler-mode word after its base: {mstat or '(none)'}, the "
                                                        f"specialized module {modes_ok}"})


def compare(args) -> int:
    run = Path(args.run)
    path = Path(args.compare)
    if path.name != "compare.json":
        raise Refused("C10 requires the original run's compare.json")
    # Recompute acceptance from the pinned original stream, build and control files.
    # A saved aggregate verdict cannot substitute for those inputs.
    checked = subprocess.run(
        [sys.executable, str(REPO / "tools/s5/compare_hosts.py"), "check", "--run", str(path.parent)],
        capture_output=True, text=True,
    )
    d = json.loads(path.read_text())
    ctrl = d.get("controls", {})
    registered = (
        d.get("marker") == "R2_DRAW_OK"
        and d.get("acceptance_scope") == "r2-draws-full"
        and type(d.get("stage")) is int and d["stage"] == 3
        and type(d.get("max_frames")) is int and d["max_frames"] == 0
        and d.get("stream", {}).get("sha256") ==
        "37ff5dd7756d8411d7c12161397d2cca8d3a4bc9f58af78644abcc0ef5f4d555"
    )
    ok = (checked.returncode == 0 and registered and d.get("verdict") == "PASS"
          and d.get("verdict_with_controls") == "PASS" and ctrl.get("run") is True
          and ctrl.get("all_as_required") is True)
    return put(run, "C10", {
        "check": "C10", "pass": ok, "verdict": d.get("verdict"),
        "marker": d.get("marker"), "acceptance_scope": d.get("acceptance_scope"),
        "stream_sha256": d.get("stream", {}).get("sha256"),
        "recheck_exit": checked.returncode, "registered_full_boot": registered,
        "controls_as_required": ctrl.get("all_as_required"),
        "summary": f"registered full-boot geometry: {'PASS' if ok else 'FAIL'}; "
                   f"marker {d.get('marker')}; offline check {checked.returncode}; "
                   f"controls as required {ctrl.get('all_as_required')}",
    })

# C4 (U9, the R1 plan's section 2.4 and section 4): the state census with the vertex input by the
# vertex shader's signature. The controls the plan names, each of which must fail the census itself
# (never only the sweep): its five new ones, U9's BGRA-by-location one, and the three vertex-input ones
# R2b already held to the census.
C4_CONTROLS = ("map-vi-declorder", "map-vi-no-null", "map-null-vertex-rate", "map-vi-unread-attr", "map-ps-sampler-superset",
               "map-bgra-decl-index", "map-bgra-mask", "map-vi-offset", "map-bgra-extra")
# state_check.cpp controls(): the complete set, including controls legitimately exercised only by
# the domain sweep. A missing, repeated or unknown name is incomplete evidence.
C4_ALL_CONTROLS = (
    "map-blend-swap", "map-zfunc-swap", "map-normalize-off", "map-writemask", "map-address-swap", "map-border-clamp",
    "map-border-colour", "map-depth-clamp-off", "map-depth-bias-push", "map-depth-clamp-extra", "map-border-swizzled-view",
    "map-border-fallback", "map-lod-unquantised", "map-bgra-mask", "map-fan-order", "map-depth-clip-off", "map-alpha-op",
    "map-ds-bind", "map-cull-swap", "map-vi-offset", "map-bgra-extra", "map-fan-on-list", "map-vi-declorder", "map-vi-no-null",
    "map-null-vertex-rate", "map-vi-unread-attr", "map-bgra-decl-index", "map-ps-sampler-superset",
)
C4_MARKERS = {"C4": "STATE_CENSUS_OK",
              "C4-caps-off": "STATE_CENSUS_FALLBACK_OWED BORDER DEPTH_CLAMP TEST_LEG=border,depth-clamp"}


def c4_declorder_tuples(census: Path, streams) -> tuple[int, int]:
    """(the census's pipeline tuples, those whose vertex shader's recorded input signature is not its
    declaration's semantics in declaration order): the tuples R2b's declaration-order stand-in
    (map-vi-declorder) must fail, counted from the keys files and the shader record alone, without the
    state check (POSITIONT read as POSITION, UNUSED elements left out, as the stand-in reads them)."""
    record = json.loads((census / "shader-record.json").read_text())
    sig = {v["sha256"]: [list(x) for x in v["input_signature"]] for v in record["vs"]}
    total = differ = 0
    for s in streams:
        keys = json.loads((census / s / "d3d9gpu.keys.json").read_text())
        for r in keys["inputs"]:
            total += 1
            decl = [[D3DDECLUSAGE_POSITION if e[4] == D3DDECLUSAGE_POSITIONT else e[4], e[5]] for e in r["elems"]
                    if e[2] != D3DDECLTYPE_UNUSED]
            if sig[keys["shaders"]["vs"][r["vs"] - 1]] != decl:
                differ += 1
    return total, differ


def c4(args) -> int:
    """C4 from a state_census.sh run (--census, its directory; --log, its output; --exit, its status):
    the marker C4_MARKERS names for the leg; the census with no VS_SIGNATURE anywhere, no RT_FEEDBACK,
    nothing unmapped and nothing differing; every pipeline tuple evaluated with a recorded pixel and
    vertex shader, and the device's own reflection the record's on every tuple and created shader; every
    control failing as required, C4_CONTROLS each by the census itself, and map-vi-declorder on exactly
    the tuples whose signature is not the declaration (c4_declorder_tuples)."""
    run, census = Path(args.run), Path(args.census)
    log = Path(args.log).read_text()
    marker = log.strip().splitlines()[-1] if log.strip() else ""
    want = C4_MARKERS[args.check]
    d = json.loads((census / "state-check.json").read_text())
    draw_audits = {}
    if args.check == "C4":
        held = d.get("stream_identity")
        held_streams = held.get("streams") if isinstance(held, dict) else None
        for s in STREAMS:
            player_path = census / s / "report.json"
            try:
                if not isinstance(held_streams, dict) or set(held_streams) != set(STREAMS):
                    raise ValueError("held census stream identity must name exactly the four original streams")
                identity = held_streams[s]
                original = identity.get("path") if isinstance(identity, dict) else None
                if not isinstance(original, str) or not original or not Path(original).is_absolute():
                    raise ValueError("held census stream identity names no absolute original path")
                draw_audits[s] = check_census_draws.check_paths(Path(original), player_path, census / s / "d3d9gpu.json",
                                                             identity=identity)
            except (OSError, ValueError, TypeError) as error:
                draw_audits[s] = {"pass": False, "failures": [f"cannot read original draw inputs: {error}"]}
    draws_ok = args.check != "C4" or all(row["pass"] for row in draw_audits.values())
    c = d.get("census", {})
    streams = c.get("stream_names", [])
    per = c.get("streams", {})
    keys_counts = {}
    for s in STREAMS:
        keys = json.loads((census / s / "d3d9gpu.keys.json").read_text())
        keys_counts[s] = {"pipeline_tuples": len(keys["inputs"]), "sampler_tuples": len(keys["samplers"]),
                          "shaders_held_to_the_record": len(keys["shaders"]["vs"]) + len(keys["shaders"]["ps"])}
    totals = {k: sum(v[k] for v in keys_counts.values()) for k in ("pipeline_tuples", "sampler_tuples", "shaders_held_to_the_record")}
    counts_match = sorted(streams) == sorted(STREAMS) and set(per) == set(STREAMS)
    for s, counts in keys_counts.items():
        row = per.get(s, {})
        count = counts["pipeline_tuples"]
        counts_match &= (count > 0 and all(row.get(k) == v for k, v in counts.items())
                         and row.get("tuples_with_recorded_reflection") == count
                         and row.get("tuples_with_recorded_vs_signature") == count
                         and all(row.get(k) == 0 for k in ("tuples_without_recorded_reflection", "tuples_without_recorded_vs_signature",
                                                           "reflection_differs", "undecided", "rt_feedback", "unmapped", "differ",
                                                           "sampler_unmapped", "sampler_differ")))
    counts_match &= all(c.get(k) == v for k, v in totals.items())
    n = c.get("pipeline_tuples", 0)
    unmapped = sum(v.get("unmapped", 0) + v.get("sampler_unmapped", 0) for v in per.values())
    differ = sum(v.get("differ", 0) + v.get("sampler_differ", 0) for v in per.values())
    text = (census / "state-check.txt").read_text()
    vs_signature = "VS_SIGNATURE" in text or "VS_SIGNATURE" in json.dumps(d)
    controls = d.get("controls", [])
    ctl = {x["name"]: x for x in controls}
    every_control = len(controls) == len(ctl) and set(ctl) == set(C4_ALL_CONTROLS)
    for x in ctl.values():
        failed = x.get("census_tuples_failed")
        counted = type(failed) is int and 0 <= failed <= totals["pipeline_tuples"] + totals["sampler_tuples"]
        every_control &= counted and ((x["outcome"] == "fails as required (census)" and failed > 0)
                                      or (x["outcome"].startswith("not exercised by census; fails as required (domain sweep")
                                          and x["outcome"].endswith(")") and failed == 0))
    by_census = {k: k in ctl and ctl[k]["outcome"] == "fails as required (census)"
                    and type(ctl[k].get("census_tuples_failed")) is int and ctl[k]["census_tuples_failed"] > 0
                 for k in C4_CONTROLS}
    total, declorder_want = c4_declorder_tuples(census, STREAMS)
    declorder_got = ctl.get("map-vi-declorder", {}).get("census_tuples_failed")
    reflection = (n > 0 and c.get("tuples_with_recorded_reflection") == n and c.get("tuples_with_recorded_vs_signature") == n
                  and c.get("reflection_differs_tuples") == 0
                  and c.get("shaders_held_to_the_record") == totals["shaders_held_to_the_record"])
    ok = (marker == want and int(args.exit) == (0 if want == "STATE_CENSUS_OK" else 4) and sorted(streams) == sorted(STREAMS)
          and not vs_signature and c.get("rt_feedback_tuples") == 0 and c.get("undecided_tuples") == 0 and unmapped == 0
          and differ == 0 and counts_match and reflection and every_control and all(by_census.values()) and total == n
          and declorder_got == declorder_want and declorder_want > 0 and draws_ok)
    return put(run, args.check, {
        "check": args.check, "pass": bool(ok), "marker": marker, "want": want, "exit": int(args.exit),
        "census_line": next((l for l in text.splitlines() if l.startswith("census:")), None),
        "pipeline_tuples": n, "sampler_tuples": c.get("sampler_tuples"), "null_stream_tuples": c.get("null_stream_tuples"),
        "counts_match_keys": bool(counts_match), "keys_counts": keys_counts,
        "draw_scope": "default-full-geometry" if args.check == "C4" else "capability-off-state-only",
        "original_draw_audits": draw_audits,
        "unmapped": unmapped, "differ": differ, "rt_feedback": c.get("rt_feedback_tuples"), "vs_signature_named": vs_signature,
        "reflection": {k: c.get(k) for k in ("tuples_with_recorded_reflection", "tuples_with_recorded_vs_signature",
                                             "reflection_differs_tuples", "shaders_held_to_the_record")},
        "controls": {k: {"outcome": v["outcome"], "census_tuples_failed": v.get("census_tuples_failed")} for k, v in ctl.items()},
        "controls_by_the_census": by_census,
        "declorder": {"tuples_whose_signature_is_not_the_declaration": declorder_want, "failed_by_map_vi_declorder": declorder_got},
        "summary": f"{marker}; {n} pipeline tuples ({c.get('null_stream_tuples')} with the null stream), {c.get('sampler_tuples')} "
                   f"sampler tuples: {unmapped} unmapped, {differ} differ, {c.get('rt_feedback_tuples')} RT_FEEDBACK, VS_SIGNATURE named "
                   f"{vs_signature}; per-stream counts match the keys {counts_match}; the device's reflection the record's on "
                   f"every tuple {reflection}; {len(ctl)} controls fail as "
                   f"required {every_control}, the vertex-input ones by the census itself {all(by_census.values())}; "
                   f"map-vi-declorder fails {declorder_got} tuples, the {declorder_want} of {total} whose signature is not the "
                   f"declaration; original default draw accounts {draws_ok}"})


EXPECTED = ["C1", "corpus", "C4", "C4-caps-off", "C2-nvidia", "C2-lvp", "C3-nvidia", "C3-lvp", "C3-controls-nvidia",
            "C3-controls-lvp", "C6", "C7-nvidia", "C7-lvp", "C8-nvidia", "C8-lvp", "C9-nvidia", "C9-lvp", "C10",
            "C11-nvidia", "C11-lvp", "C12-nvidia", "C12-lvp", "C13-nvidia", "C13-lvp"]


def decide(run: Path):
    """(marker, exit status, the checks by name, the harness's failures) of a run's checks. Every
    validation error fails the run, whichever check saw it: no table exempts one. "Fail on any
    validation error" is the agent's amendment (RULINGS-2-AMENDMENTS.md) of the user's validation-layer
    ruling ("Use the validation layers in host 2's debug runs, since Vulkan misuse that a desktop driver
    tolerates is what breaks on phones."); exempting an error would loosen it, which is the user's to
    rule on."""
    harness = (run / "checks" / "harness.txt").read_text().splitlines() if (run / "checks" / "harness.txt").is_file() else []
    got = {p.stem: json.loads(p.read_text()) for p in (run / "checks").glob("*.json")}
    missing = [c for c in EXPECTED if c not in got]
    failed = [c for c in EXPECTED if c in got and not got[c]["pass"]]
    invalid = [f"{c}:{v}" for c in sorted(got) for v in sorted(got[c].get("validation_errors") or {})]
    if harness or missing or failed or invalid:
        items = failed + [m + ":missing" for m in missing] + [f"VALIDATION {u}" for u in invalid] + (["harness"] if harness else [])
        return "R1_SHADERS_FAIL " + " ".join(items), 1, got, harness
    return "R1_SHADERS_OK", 0, got, harness


def verdict(args) -> int:
    marker, rc, got, harness = decide(Path(args.run))
    for c in EXPECTED:
        print(f"{c}: {'pass' if c in got and got[c]['pass'] else 'MISSING' if c not in got else 'FAIL'}: "
              f"{got.get(c, {}).get('summary', '')}")
    for h in harness:
        print(f"harness: {h}")
    print(marker)
    return rc


# ---- the receipt
def repo_relative(value, root: str):
    """The value with every path string under the repository made relative to it: a receipt names
    files the way the repository does, never by where one checkout of it sat."""
    if isinstance(value, dict):
        return {k: repo_relative(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [repo_relative(v, root) for v in value]
    if isinstance(value, str) and value.startswith(root + "/"):
        return value[len(root) + 1:]
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(args) -> int:
    run = Path(args.run)
    names = sorted(p.stem for p in (run / "checks").glob("*.json"))
    if names != sorted(EXPECTED):
        extra, missing = sorted(set(names) - set(EXPECTED)), sorted(set(EXPECTED) - set(names))
        raise Refused(f"the run's checks are not EXPECTED's: extra {extra}, missing {missing}")
    raw = {n: json.loads((run / "checks" / f"{n}.json").read_text()) for n in names}
    got = {n: repo_relative(v, str(REPO)) for n, v in raw.items()}
    pins = json.loads((REPO / "tools" / "pins.json").read_text())["tools"]
    commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if not commit:
        raise Refused(f"{REPO} has no HEAD to name the receipt's commit")
    # Each driver's profile record (shaderc profile of its facts) must be the one every module of its
    # load was compiled with (the load's reflections and pairs), and the corpus's (the second host's
    # replays, on the default device) must be one of the two.
    profiles = {}
    for drv in ("nvidia", "lvp"):
        lines = (run / f"profile-{drv}.txt").read_text().splitlines()
        rec = "".join(l + "\n" for l in lines[1:])
        sha = hashlib.sha256(rec.encode()).hexdigest()
        used = {json.loads(q.read_text()).get("profile_sha256") for q in (run / drv / "load").glob("*.json")}
        used |= {json.loads(q.read_text()).get("profile_sha256") for q in (run / drv / "load" / "pairs").glob("*.json")}
        if used != {sha}:
            raise Refused(f"the {drv} profile record is {sha}, its load's modules were compiled with {sorted(map(str, used))}")
        profiles[drv] = {"device": lines[0][2:] if lines and lines[0].startswith("# ") else None,
                         "record_sha256": sha, "record": lines[1:]}
    corpus_profiles = {s["profile_sha256"] for s in raw["corpus"]["streams"].values()}
    corpus_driver = [d for d, v in profiles.items() if {v["record_sha256"]} == corpus_profiles]
    if not corpus_driver:
        raise Refused(f"the corpus's replays used profile {sorted(corpus_profiles)}, neither driver's")
    # The translator every reflection names must be the build's; with --build, every source the build's
    # records hashed (the players' and the translator's) must be the tree's, so the receipt's pipeline
    # below is what ran.
    ident = json.loads((Path(args.build) / "shader" / "translator_identity.json").read_text()) if args.build else {}
    named = {json.loads(q.read_text()).get("translator") for drv in profiles
             for q in list((run / drv / "load").glob("*.json")) + list((run / drv / "load" / "pairs").glob("*.json"))}
    if args.build and named != {ident.get("id")}:
        raise Refused(f"the run's modules name translators {sorted(map(str, named))}, the build's is {ident.get('id')}")
    built = {}
    if args.build:
        stale = []
        for record in ("sa2_player_gpu.json", "sa2_player.json"):
            for rel, sha in json.loads((Path(args.build) / record).read_text())["sources"].items():
                built[rel] = sha
                if not (REPO / rel).is_file() or sha256_file(REPO / rel) != sha:
                    stale.append(rel)
        for key, sha in ident.get("files", {}).items():
            if key.startswith("repo:"):
                rel = key[len("repo:"):]
                built[rel] = sha
                if not (REPO / rel).is_file() or sha256_file(REPO / rel) != sha:
                    stale.append(rel)
        if stale:
            raise Refused(f"the build in {args.build} was made from other sources than the tree's: {sorted(set(stale))}")
    # the layers the run loaded, hashed where they were loaded from
    layer = run / "vkcap" / "vkcap" / "libVkLayer_sa2_vkcap.so"
    import validation_run
    validation = validation_run.load_helper(REPO)
    try:
        selected_validation = validation.selected_layer(REPO)
        validation_inputs = validation_run.accepted_build_inputs(REPO, selected_validation)
        if not args.build:
            raise Refused("live validation receipt needs the actual producer build")
        validation_observation = validation_run.audit_r1(run, selected_validation,
            Path(args.build) / "shader/d3d9gpu_shaderc", REPO)
        if json.loads((run / "selected-validation.json").read_text()) != selected_validation:
            raise Refused("the run selected another validation layer")
    except (validation.ValidationRefused, validation_run.ObservationRefused, OSError, ValueError) as error:
        raise Refused(str(error)) from error
    vsha = selected_validation["library_sha256"]
    layer_env = (run / "layer-env.txt").read_text().split() if (run / "layer-env.txt").is_file() else []
    # the shader tools the instruments ran, each held to its pin (test/shader_tools.py)
    import shader_tools
    try:
        tool_shas = {name: sha256_file(shader_tools.pinned(name, REPO)) for name in ("spirv-val", "spirv-opt", "spirv-cross",
                                                                                 "shadercross")}
        tool_shas.update({f"shadercross's {k}": sha256_file(q) for k, q in shader_tools.libraries("shadercross", REPO).items()})
    except shader_tools.ToolRefused as e:
        raise Refused(str(e))
    # the Vulkan environment the run inherited and cleared, and the layer settings files it looked for
    # (test_shaders.sh vk-env.json): nothing filters or redirects the validation layer's messages
    vk_env = json.loads((run / "vk-env.json").read_text()) if (run / "vk-env.json").is_file() else None
    if vk_env is None or vk_env.get("settings_files_present"):
        raise Refused(f"the run's Vulkan environment record is missing or names a layer settings file: {vk_env}")
    # what produced the run, by repository path and sha256 (Documentation/scripts/check_receipt_pins.py
    # holds each to the tree at the commit that writes this receipt): the second host and its tools,
    # the player's environment, the pins
    pipeline = {str(p.relative_to(REPO)): sha256_file(p)
                for p in sorted((REPO / "host" / "d3d9gpu").rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    for extra in ("host/player/player_env.sh", "host/player/dxvk.conf", "tools/pins.json",
                  "Documentation/scripts/check_pins.py", "tools/s5/compare_hosts.py", "tools/s5/stream_dump.py",
                  "tools/s5/play_report.py", "host/player/build_record.py",
                  "tools/s5/validation_tools.py", "tools/s5/validation-sources-350.json",
                  "tools/s5/fetch_validation_sources.py", "tools/s5/build_validation_tools.py"):
        pipeline[extra] = sha256_file(REPO / extra)
    receipt = {
        "schema": "sa2-s5-host2-r1/v1",
        "tool": "host/d3d9gpu/test_shaders.sh (host/d3d9gpu/test/r1_receipt.py write)",
        "commit": commit,
        "inputs": {"pipeline": dict(sorted(pipeline.items())),
                   "run": "the run directory is private (it holds the game's shaders): only hashes and counts are here"},
        "plan": "the R1 plan's section 4 (private/generated/s5/rulings/r1-shader-translator/PLAN.md); C4 is "
                "VS_SIGNATURE's resolution (U9): the state census with the vertex input by the vertex shader's "
                "signature, on the default device with the SDL patch's capabilities and with both forced off; C11 "
                "runs its conversion with the texture-type clause here, compiling and running on a Mac carried "
                "forward; C12 and C13 are the sampler-mode specialization's (I7b)",
        "scope": "No validation error in any check covers the creation of every game shader, variant, pair and "
                 "specialized module (C2: nothing is drawn) and the synthetic draws (C7, C8, C13); no game shader "
                 "is drawn under the layer here. C10 checks full original registered-boot geometry and controls "
                 "without the layer, recomputing acceptance from the pinned run artifacts. The corpus load uses "
                 "its census sampler-mode word; the Device specializes each draw at its actual sampler-mode word.",
        "archive": {"path": pins["dxvk"]["dxbc_spirv_archive"], "sha256": pins["dxvk"]["dxbc_spirv_archive_sha256"]},
        "translator": ident.get("id"),
        "build_sources_checked": len(built),
        "capture_layer_sha256": sha256_file(layer) if layer.is_file() else None,
        "validation_layer_sha256": vsha,
        "validation_layer": repo_relative(selected_validation, str(REPO)),
        "validation_layer_observations": repo_relative(validation_observation, str(REPO)),
        "validation_build_inputs": repo_relative(validation_inputs, str(REPO)),
        "validation_layer_settings": [e.replace(str(run), "<run>") for e in layer_env if e.startswith("VK_LAYER_")],
        "validation_environment": vk_env,
        "shader_tools_sha256": tool_shas,
        "profiles": profiles,
        "corpus_profile": corpus_driver[0],
        "checks": got,
        "marker": decide(run)[0],
    }
    text = json.dumps(receipt, indent=1) + "\n"
    Path(args.out).write_text(text)
    print(f"RECEIPT {args.out} {hashlib.sha256(text.encode()).hexdigest()[:16]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--keys", nargs="+", required=True)
    p.add_argument("--bytecode", required=True)
    p.add_argument("--reflections", required=True)
    p.add_argument("--out", required=True)
    w = sub.add_parser("write")
    w.add_argument("--run", required=True)
    w.add_argument("--out", required=True)
    w.add_argument("--build")
    n = sub.add_parser("note")
    n.add_argument("--run", required=True); n.add_argument("--check", required=True)
    n.add_argument("--marker-file", required=True); n.add_argument("--want", required=True)
    c = sub.add_parser("corpus")
    c.add_argument("--run", required=True); c.add_argument("--streams", nargs="+", required=True)
    nm = sub.add_parser("name")
    nm.add_argument("--plan-keys", required=True); nm.add_argument("--bytecode", required=True); nm.add_argument("--out", required=True)
    ld = sub.add_parser("load")
    ld.add_argument("--run", required=True); ld.add_argument("--driver", required=True); ld.add_argument("--log", required=True)
    ld.add_argument("--control-log", required=True); ld.add_argument("--plan", required=True)
    ld.add_argument("--out-dir", required=True); ld.add_argument("--capture", required=True)
    cn = sub.add_parser("canon")
    cn.add_argument("--run", required=True); cn.add_argument("--driver", required=True); cn.add_argument("--gpl", required=True)
    cn.add_argument("--linked", required=True); cn.add_argument("--captures", nargs="+", required=True)
    cn.add_argument("--capture-dir", required=True)
    ct = sub.add_parser("controls")
    ct.add_argument("--run", required=True); ct.add_argument("--driver", required=True); ct.add_argument("--clean", required=True)
    ct.add_argument("--faults", required=True); ct.add_argument("--names", nargs="+", required=True); ct.add_argument("--r0c", required=True)
    ct.add_argument("--linkage-load", required=True); ct.add_argument("--linkage-canon", required=True)
    ct.add_argument("--gpl", required=True, help="DXVK's dump (DXVK_SHADER_DUMP_PATH), for the removed region's mutants")
    r0 = sub.add_parser("r0c")
    r0.add_argument("--r0c", required=True); r0.add_argument("--load", required=True); r0.add_argument("--out", required=True)
    sy = sub.add_parser("synth")
    sy.add_argument("--out", required=True)
    bo = sub.add_parser("border")
    bo.add_argument("--run", required=True); bo.add_argument("--driver", required=True); bo.add_argument("--log", required=True)
    bo.add_argument("--json", required=True)
    bo.add_argument("--base-json", help="the border legs drawn with the base pixel shader (--unspecialized)")
    fr = sub.add_parser("frames")
    fr.add_argument("--run", required=True); fr.add_argument("--driver", required=True); fr.add_argument("--none", required=True)
    fr.add_argument("--layer", required=True); fr.add_argument("--control", required=True); fr.add_argument("--reference")
    for leg in ("none", "layer", "control"):
        fr.add_argument(f"--{leg}-log", required=True); fr.add_argument(f"--{leg}-exit", required=True)
    fr.add_argument("--truncate", nargs="*")
    co = sub.add_parser("corrupt")
    co.add_argument("--cache", required=True); co.add_argument("--out", required=True)
    ca = sub.add_parser("cache")
    ca.add_argument("--run", required=True); ca.add_argument("--logs", nargs=5, required=True); ca.add_argument("--clean", required=True)
    ca.add_argument("--out-dir", required=True); ca.add_argument("--outside-root", required=True)
    ca.add_argument("--variant-log", required=True); ca.add_argument("--variant-module", required=True)
    ca.add_argument("--variant-clean", required=True)
    ca.add_argument("--modes-log", required=True); ca.add_argument("--modes-module", required=True)
    ca.add_argument("--modes-clean", required=True); ca.add_argument("--modes-base", required=True)
    fv = sub.add_parser("first-variant")
    fv.add_argument("--plan", required=True)
    fs = sub.add_parser("first-specialized")
    fs.add_argument("--plan", required=True); fs.add_argument("--reflections", required=True)
    bs = sub.add_parser("bases")
    bs.add_argument("--plan", required=True); bs.add_argument("--shaderc", required=True); bs.add_argument("--facts", required=True)
    bs.add_argument("--named", required=True); bs.add_argument("--out", required=True)
    sp = sub.add_parser("specialize")
    sp.add_argument("--run", required=True); sp.add_argument("--driver", required=True); sp.add_argument("--plan", required=True)
    sp.add_argument("--load", required=True); sp.add_argument("--bases", required=True); sp.add_argument("--faults", required=True)
    sp.add_argument("--modes-modules", required=True, help="C13's specialized modules (modes/modules)")
    sp.add_argument("--modes-base", required=True, help="C13's bases (the --unspecialized leg's modules)")
    sp.add_argument("--shaderc", required=True); sp.add_argument("--facts", required=True); sp.add_argument("--named", required=True)
    sp.add_argument("--sweep-out", required=True, help="where the sweep's modules are compiled")
    mt = sub.add_parser("metal")
    mt.add_argument("--run", required=True); mt.add_argument("--driver", required=True); mt.add_argument("--plan", required=True)
    mt.add_argument("--load", required=True); mt.add_argument("--modes-modules", required=True)
    mt.add_argument("--faults", required=True); mt.add_argument("--bases", required=True)
    mt.add_argument("--modes-base", required=True, help="C13's bases (the --unspecialized leg's modules)")
    mt.add_argument("--modes-json", required=True, help="C13's modes.json, for its case count")
    md = sub.add_parser("modes")
    md.add_argument("--run", required=True); md.add_argument("--driver", required=True); md.add_argument("--log", required=True)
    md.add_argument("--json", required=True); md.add_argument("--base-json", required=True)
    md.add_argument("--base-log", required=True, help="the base leg's log, run under the layer (the must-fail control)")
    cm = sub.add_parser("compare")
    cm.add_argument("--run", required=True); cm.add_argument("--compare", required=True)
    pv = sub.add_parser("probe-value")
    pv.add_argument("--dxvk", required=True); pv.add_argument("--complement", action="store_true")
    c4p = sub.add_parser("c4")
    c4p.add_argument("--run", required=True); c4p.add_argument("--check", required=True, choices=sorted(C4_MARKERS))
    c4p.add_argument("--census", required=True); c4p.add_argument("--log", required=True); c4p.add_argument("--exit", required=True)
    npb = sub.add_parser("nullprobe")
    npb.add_argument("--run", required=True); npb.add_argument("--driver", required=True); npb.add_argument("--dxvk", required=True)
    npb.add_argument("--sdl", required=True); npb.add_argument("--control", required=True)
    npb.add_argument("--sdl-base", help="the second host's leg drawn with the base pixel shader (--unspecialized)")
    ve_ = sub.add_parser("vk-env")
    ve_.add_argument("--out", required=True)
    vd = sub.add_parser("verdict")
    vd.add_argument("--run", required=True)
    args = ap.parse_args(argv)
    try:
        return {"plan": plan, "write": write, "note": note, "corpus": corpus, "name": name, "load": load, "canon": canon,
                "controls": controls, "r0c": r0c, "synth": synth, "border": border, "frames": frames, "corrupt": corrupt,
                "cache": cache, "compare": compare, "verdict": verdict, "probe-value": probe_value,
                "nullprobe": nullprobe, "first-variant": first_variant, "first-specialized": first_specialized,
                "bases": bases, "specialize": specialize, "metal": metal, "modes": modes, "vk-env": vk_env, "c4": c4}[args.cmd](args)
    except (Refused, OSError, KeyError, ValueError) as e:
        print(f"r1_receipt.py: REFUSED {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
