#!/usr/bin/env python3
"""Convert host 2's specialized pixel shaders to the Metal shading language and hold each texture's type.

Subtitle: check C11's texture-type clause (ruling 2: "Confirm the translated shaders convert for Metal
through SDL's shader tooling; macOS is a target"), a strengthening of the R1 plan's C11 the agent added
(I7b). SPIRV-Cross types an image that any depth-compare sample uses, anywhere in the module, as a
depth texture (spirv_cross.cpp's comparison images), so a module that keeps DXVK's dead depth-compare
sample on a colour register declares it depth2d<float> and samples one channel where Metal binds a
colour texture. Each sampling pixel shader is converted by the pinned shadercross (SDL's tool, the path
SDL's Metal backend takes, --msl-version 2.3.0: discard_fragment has demote semantics from MSL 2.3) and
by the pinned spirv-cross command line (--msl --msl-version 20300); both must convert, and every texture
argument of the entry point must be typed by its register's sampler mode in the variant's word: depth2d
or depthcube where the mode is 2 or 3 (depth compare), texture2d, texturecube or texture3d otherwise. A
texture argument is tied to its register by name: the SPIR-V variable of that name, its Binding
(SDL's compacted sampler slot) and the reflection's sampler_registers. Both converters must type every
argument alike. Its control, which must fail as required: the same shader's base module (the word
off: DXVK's uniform branch, every depth-compare sample kept) must give a depth texture on some register
whose mode is not a depth compare; a module whose every register is at mode 2 or 3 has no such register
and no control. Each texture is typed by its register's mode in the word the variant names, so the
check holds the module to that word, not to the bound texture: that the word is DXVK's for the draw is
C3's linked half (host 2's word equal to DXVK's pipeline's) and C13's (the word DXVK's rule gives each
texture, drawn under the validation layer).

  metal_check.py --batch SPEC_DIR [--base-dir BASE_DIR] [--expect N] [--out DIR]

SPEC_DIR holds d3d9gpu_shaderc's modules and reflections (<name>.spv, <name>.json); every pixel shader
whose reflection names a specialized variant (sampler_modes=<word>) and declares a sampler is
checked, and --expect N fails a count other than N. With --base-dir each one's control runs on
BASE_DIR/<name>.spv. --out keeps the MSL. Running the converted shaders on a Mac is not done here.

Exit status 0 and METAL_CHECK_OK only when every module holds and every control fails as required;
1 and METAL_CHECK_FAIL otherwise.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shader_tools  # noqa: E402
import spv_canon  # noqa: E402

ARG = re.compile(r"\b(texture2d|depth2d|texturecube|depthcube|texture3d|texture2d_array|depth2d_array)<\s*\w+\s*"
                 r"(?:,\s*access::\w+\s*)?>\s+(\w+)\s+\[\[\s*texture\(\s*(\d+)\s*\)\s*\]\]")
DEPTH_TYPES = {"depth2d", "depthcube", "depth2d_array"}


def word_of(variant):
    m = re.search(r"sampler_modes=([^;]+)", variant or "")
    return None if not m or m.group(1) == "none" else int(m.group(1), 0)


def bindings_by_name(spv):
    """{OpName: Binding} of the module's sampled images (set 2, SDL's fragment samplers)."""
    m = spv_canon.Module(spv, "host2")
    out = {}
    for i in m.insts:
        if i.op == "OpVariable" and m.storage(i) == "UniformConstant":
            s, b = m.deco(i.rid, "DescriptorSet"), m.deco(i.rid, "Binding")
            if s and b and int(s[0]) == 2 and i.rid in m.names:
                out[m.names[i.rid]] = int(b[0])
    return out


def convert(spv, out_dir, tag):
    """{converter: (ok, msl text or error)} from shadercross and spirv-cross."""
    res = {}
    sx = out_dir / f"{tag}.shadercross.metal"
    r = subprocess.run([str(shader_tools.pinned("shadercross")), str(spv), "-s", "SPIRV", "-d", "MSL", "-t", "fragment",
                        "--msl-version", "2.3.0", "-o", str(sx)], capture_output=True, text=True, env=shader_tools.tool_env())
    res["shadercross"] = (r.returncode == 0 and sx.is_file(), sx.read_text() if sx.is_file() else (r.stdout + r.stderr)[-300:])
    sc = out_dir / f"{tag}.spirv-cross.metal"
    r = subprocess.run([str(shader_tools.pinned("spirv-cross")), "--msl", "--msl-version", "20300", str(spv), "--output",
                        str(sc)], capture_output=True, text=True, env=shader_tools.tool_env())
    res["spirv-cross"] = (r.returncode == 0 and sc.is_file(), sc.read_text() if sc.is_file() else (r.stdout + r.stderr)[-300:])
    return res


def types_of(msl):
    """{argument name: texture type} of the entry point's texture arguments."""
    entry = msl[msl.find("fragment "):] if "fragment " in msl else ""
    return {name: t for t, name, _ in ARG.findall(entry[:entry.find("{")] if "{" in entry else entry)}


def check_one(spv, refl, word, out_dir, tag):
    """(problems, the types by register) of one specialized module."""
    names = bindings_by_name(spv)
    regs = refl["sampler_registers"]
    problems, typed = [], {}
    conv = convert(spv, out_dir, tag)
    for tool, (ok, text) in conv.items():
        if not ok:
            problems.append(f"{tool} did not convert it: {text}")
            continue
        args = types_of(text)
        if len(args) != len(regs):
            problems.append(f"{tool}: {len(args)} texture arguments, {len(regs)} sampler registers")
        for name, t in sorted(args.items()):
            if name not in names or names[name] >= len(regs):
                problems.append(f"{tool}: the texture argument {name} is no sampled image of the module")
                continue
            r = regs[names[name]]
            mode = (word >> (2 * r)) & 3
            want_depth = mode >= 2
            if (t in DEPTH_TYPES) != want_depth:
                problems.append(f"{tool}: register {r} (mode {mode}) is {t}, a {'depth' if want_depth else 'colour'} "
                                f"texture expected")
            prev = typed.setdefault(r, t)
            if prev != t:
                problems.append(f"register {r}: shadercross types it {prev}, spirv-cross {t}")
    return problems, typed


def control_fails(base, refl, word, out_dir, tag):
    """True when the base module gives a depth texture on a register whose mode is no depth compare."""
    names = bindings_by_name(base)
    regs = refl["sampler_registers"]
    ok, text = convert(base, out_dir, tag)["shadercross"]
    if not ok:
        return False
    for name, t in types_of(text).items():
        if name in names and names[name] < len(regs):
            r = regs[names[name]]
            if t in DEPTH_TYPES and ((word >> (2 * r)) & 3) < 2:
                return True
    return False


def entries_of(spec_dir, base_dir=None):
    """[(name, module, reflection, word, base module or None)] of every specialized sampling pixel shader
    in spec_dir (its reflection names a specialized variant and a sampler)."""
    out = []
    for refl_path in sorted(Path(spec_dir).glob("*.json")):
        refl = json.loads(refl_path.read_text())
        if refl.get("schema") != "sa2-d3d9gpu-shader-reflection/v1" or refl.get("stage") != "pixel":
            continue
        word = word_of(refl.get("variant"))
        if word is None or not refl.get("sampler_registers"):
            continue
        name = refl_path.stem
        out.append((name, Path(spec_dir) / f"{name}.spv", refl, word,
                    None if base_dir is None else Path(base_dir) / f"{name}.spv"))
    return out


def batch(spec_dir, base_dir=None, expect=None, out=None):
    return batch_entries(entries_of(spec_dir, base_dir), expect, out, str(spec_dir))


def batch_entries(entries, expect=None, out=None, where="the batch"):
    """Each entry (name, module, reflection, word, base module or None) checked; a base, where given, is
    the control, which must fail as required wherever the word leaves a register below mode 2 (a module
    whose every register is a depth compare has no colour register for a dead depth-compare sample to
    mistype: its control is not applicable)."""
    rows, problems = {}, []
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(out) if out else Path(td)
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, spv, refl, word, base in entries:
            try:
                p, typed = check_one(spv, refl, word, out_dir, name)
            except (shader_tools.ToolRefused, spv_canon.SpvError, OSError) as e:
                p, typed = [f"refused: {e}"], {}
            row = {"word": word, "types": {str(k): v for k, v in sorted(typed.items())}, "problems": p}
            if base is not None:
                colour = any(((word >> (2 * r)) & 3) < 2 for r in refl["sampler_registers"])
                if not colour:
                    row["control_fails_as_required"] = None
                else:
                    try:
                        row["control_fails_as_required"] = control_fails(base, refl, word, out_dir, f"{name}.base")
                    except (shader_tools.ToolRefused, spv_canon.SpvError, OSError) as e:
                        row["control_fails_as_required"] = False
                        p.append(f"control refused: {e}")
                    if not row["control_fails_as_required"]:
                        p.append("the control (the base module) gives no depth texture on a colour register")
            rows[name] = row
            problems += [f"{name}: {x}" for x in p]
    if expect is not None and len(rows) != expect:
        problems.append(f"{len(rows)} sampling pixel shaders checked, not {expect}")
    if not rows:
        problems.append(f"no specialized sampling pixel shader in {where}")
    return {"ok": not problems, "checked": len(rows), "modules": rows, "problems": problems,
            "controls": sum(1 for r in rows.values() if r.get("control_fails_as_required")),
            "converters": {"shadercross": "--msl-version 2.3.0", "spirv-cross": "--msl --msl-version 20300"},
            "carried_forward": "compiling and running the converted shaders on a Mac"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--batch", required=True, metavar="SPEC_DIR")
    ap.add_argument("--base-dir")
    ap.add_argument("--expect", type=int)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    rep = batch(a.batch, a.base_dir, a.expect, a.out)
    print(json.dumps(rep, indent=1))
    print("METAL_CHECK_OK" if rep["ok"] else "METAL_CHECK_FAIL")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
