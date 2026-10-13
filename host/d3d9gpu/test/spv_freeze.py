#!/usr/bin/env python3
"""Freeze DXVK's sampler-mode word in its linked pixel shader and fold what it decides.

Subtitle: C3's linked half after the sampler-mode specialization (I7b). Host 2 specializes a pixel
shader at the draw's psSamplerModes word (host/d3d9gpu/d9g_shader.h Variant::sampler_modes; the device
hands the driver that module once it computes each draw's word, R2-draws); DXVK hands the driver the
module with psSamplerModes as OpSpecConstant SpecId 5 and lets the driver fold it with the pipeline's
VkSpecializationInfo, which for the streams' D3D9 pipelines omits SpecId 5 (the constant keeps its
default, 0). So the comparer turns DXVK's module
into what that specialization means, by its own means and none of host 2's: the word the pipeline
gives (its map entry for SpecId 5, or the default where it has none) replaces the spec constant; a
function whose one block computes its value from its parameters, constants and that word only (DXVK's
samplerMode) is evaluated per call with constant arguments and the call becomes the constant (spirv-opt
has no interprocedural propagation, and no folding rule for OpBitFieldUExtract); then the pinned
spirv-opt folds and removes what that decides (--ccp --eliminate-dead-branches --merge-blocks
--eliminate-dead-code-aggressive --eliminate-dead-functions). Two presentation steps follow, neither of
which changes what a function computes: each function's blocks back in DXVK's order (spirv-opt's
dead-branch pass re-sorts the blocks of functions it does not otherwise touch), and OpCapability
ImageQuery dropped when no image query is left (spirv-opt does not trim capabilities). The result must
be valid SPIR-V (the pinned spirv-val) and must only delete from the module the freeze handed spirv-opt:
every instruction of it is one of that module's, word for word (spirv-opt keeps ids), but an OpBranch
standing where a block's conditional branch or switch had that target among its edges; anything
spirv-opt added or rewrote is refused, so the instrument cannot normalise code the word does not decide.
A module that declares no SpecId 5 is returned unchanged.

  spv_freeze.py IN.spv OUT.spv [--entries JSON] [--data HEX]

--entries and --data are the stage's VkSpecializationInfo as the capture layer records it
(pipelines.jsonl: [[constantID, offset, size], ...] and the data in hex). Prints one JSON line: the
word, where it came from, the calls folded, and the module's sha256.
"""
import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shader_tools  # noqa: E402
import spv_canon  # noqa: E402
import spv_specialize  # noqa: E402  the integer folds (fold) and the constant reader

PASSES = ["--ccp", "--eliminate-dead-branches", "--merge-blocks", "--eliminate-dead-code-aggressive",
          "--eliminate-dead-functions"]
WORD_SPEC_ID = 5
OP_CONSTANT, OP_SPEC_CONSTANT, OP_DECORATE, OP_FUNCTION, OP_FUNCTION_END, OP_LABEL = 43, 50, 71, 54, 56, 248
OP_FUNCTION_CALL, OP_COPY_OBJECT, OP_CAPABILITY = 57, 83, 17
DECORATION_SPEC_ID, CAPABILITY_IMAGE_QUERY = 1, 50
IMAGE_QUERIES = {103, 104, 105, 106, 107}   # OpImageQuerySizeLod, SizeLod... Levels, Samples, Lod


class Refused(Exception):
    pass


def raw(data):
    if len(data) % 4 or len(data) < 20:
        raise Refused("not a whole SPIR-V module")
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    insts, pos = [], 5
    while pos < len(words):
        wc = words[pos] >> 16
        if wc == 0 or pos + wc > len(words):
            raise Refused(f"an instruction at word {pos} overruns the module")
        insts.append(words[pos:pos + wc])
        pos += wc
    return words[:5], insts


def pack(header, insts):
    flat = header + [w for i in insts for w in i]
    return struct.pack("<%dI" % len(flat), *flat)


def word_of(entries, data, default):
    """The word the pipeline gives SpecId 5: its map entry's bytes, or the default where it has none."""
    for e in entries or []:
        cid, off, size = e
        if cid == WORD_SPEC_ID:
            if size != 4:
                raise Refused(f"SpecId 5's map entry is {size} bytes, not 4")
            b = bytes.fromhex(data or "")
            if off + 4 > len(b):
                raise Refused("SpecId 5's map entry lies past the specialization data")
            return struct.unpack_from("<I", b, off)[0], "the pipeline's VkSpecializationInfo", "entry"
    return default, "the spec constant's default (the pipeline's VkSpecializationInfo omits SpecId 5)", "default"


def freeze(src, dst, entries=None, data=None):
    header, insts = raw(Path(src).read_bytes())
    decoded = spv_canon.parse_words(Path(src).read_bytes(), str(src))
    assert len(decoded) == len(insts)
    target = None
    for i in insts:
        if i[0] & 0xFFFF == OP_DECORATE and i[2] == DECORATION_SPEC_ID and i[3] == WORD_SPEC_ID:
            if target is not None:
                raise Refused("two constants decorated SpecId 5")
            target = i[1]
    if target is None:
        Path(dst).write_bytes(Path(src).read_bytes())
        return {"frozen": False, "why": "the module declares no SpecId 5", "word_source": "no SpecId 5",
                "calls_folded": 0, "sha256": hashlib.sha256(Path(dst).read_bytes()).hexdigest()}
    spec = [i for i in insts if i[0] & 0xFFFF == OP_SPEC_CONSTANT and i[2] == target]
    if len(spec) != 1 or len(spec[0]) != 4:
        raise Refused("SpecId 5 is not one 32-bit OpSpecConstant")
    word, source, code = word_of(entries, data, spec[0][3])
    # the spec constant becomes the constant, its SpecId goes
    out = []
    for i in insts:
        if i[0] & 0xFFFF == OP_DECORATE and i[2] == DECORATION_SPEC_ID and i[3] == WORD_SPEC_ID:
            continue
        if i is spec[0]:
            i = [(4 << 16) | OP_CONSTANT, i[1], i[2], word]
        out.append(i)
    insts = out
    # the functions the word decides: evaluated with spv_specialize's integer folds, per call
    m = spv_specialize.Mod(src, data=pack(header, insts))
    consts = {}
    for i in m.insts:
        v = spv_specialize.const_value(m, i.rid) if i.rid is not None else None
        if v is not None and not isinstance(v, bool) and i.op == "OpConstant":
            consts.setdefault((i.rtype, v), i.rid)
    fns = {fn.rid: (fn, params, blocks) for fn, params, blocks in m.functions}

    def evaluate(fid, args):
        fn, params, blocks = fns[fid]
        if len(blocks) != 1 or len(params) != len(args):
            return None
        env = {p.rid: a for p, a in zip(params, args)}
        reads = False
        for i in blocks[0][1:]:
            if i.op == "OpReturnValue":
                v = env.get(i.ids[0], spv_specialize.const_value(m, i.ids[0]))
                return v if reads and isinstance(v, int) and not isinstance(v, bool) else None
            vals = []
            for x in i.ids:
                reads = reads or x == target
                vals.append(env[x] if x in env else spv_specialize.const_value(m, x))
            r = spv_specialize.fold(i.op, vals, i.lits)
            if r is None:
                return None
            env[i.rid] = r
        return None
    folded, replace = 0, {}
    for k, i in enumerate(m.insts):
        if i.op != "OpFunctionCall" or i.ids[0] not in fns:
            continue
        args = [spv_specialize.const_value(m, x) for x in i.ids[1:]]
        if not all(isinstance(a, int) and not isinstance(a, bool) for a in args):
            continue
        v = evaluate(i.ids[0], args)
        if v is None:
            continue
        replace[k] = (i.rtype, i.rid, v)
    bound = header[3]
    new_consts = []
    for k, (t, rid, v) in replace.items():
        if (t, v) not in consts:
            consts[(t, v)] = bound
            new_consts.append([(4 << 16) | OP_CONSTANT, t, bound, v])
            bound += 1
        insts[k] = [(4 << 16) | OP_COPY_OBJECT, t, rid, consts[(t, v)]]
        folded += 1
    if new_consts:
        first_fn = next(k for k, i in enumerate(insts) if i[0] & 0xFFFF == OP_FUNCTION)
        insts[first_fn:first_fn] = new_consts
        header = header[:3] + [bound] + header[4:]
    with tempfile.TemporaryDirectory() as td:
        frozen = Path(td) / "frozen.spv"
        frozen.write_bytes(pack(header, insts))
        opted = Path(td) / "opt.spv"
        r = subprocess.run([str(shader_tools.pinned("spirv-opt")), "--target-env=vulkan1.3", *PASSES, "-o", str(opted),
                            str(frozen)], capture_output=True, text=True, env=shader_tools.tool_env())
        if r.returncode:
            raise Refused(f"spirv-opt: {r.stderr.strip()[-400:]}")
        result = present(frozen.read_bytes(), opted.read_bytes())
        added = not_deleted(frozen.read_bytes(), result)
        if added:
            raise Refused(f"spirv-opt's result is no pure deletion of the frozen module: {len(added)} instructions "
                          f"added or rewritten, the first {added[:3]}")
        Path(dst).write_bytes(result)
    v = subprocess.run([str(shader_tools.pinned("spirv-val")), "--target-env", "vulkan1.3", "--scalar-block-layout",
                        str(dst)], capture_output=True, text=True, env=shader_tools.tool_env())
    if v.returncode:
        raise Refused(f"the frozen module is not valid SPIR-V: {(v.stdout + v.stderr).strip()[-400:]}")
    return {"frozen": True, "word": word, "word_from": source, "word_source": code, "calls_folded": folded,
            "sha256": hashlib.sha256(Path(dst).read_bytes()).hexdigest()}


OP_BRANCH, OP_BRANCH_CONDITIONAL, OP_SWITCH = 249, 250, 251


def not_deleted(before, after):
    """The instructions of `after` (as [opcode, words]) that are not instructions of `before`: each must be
    one of before's, word for word and at most as often, or an OpBranch to a label one of before's
    conditional branches or switches had among its targets in the block of the same label."""
    _, b = raw(before)
    _, a = raw(after)
    pool = {}
    for i in b:
        pool[tuple(i)] = pool.get(tuple(i), 0) + 1
    edges, label = {}, None
    for i in b:
        op = i[0] & 0xFFFF
        if op == OP_LABEL:
            label = i[1]
        elif op == OP_BRANCH_CONDITIONAL:
            edges[label] = {i[2], i[3]}
        elif op == OP_SWITCH:
            edges[label] = {i[2]} | set(i[4::2])
    out, label = [], None
    for i in a:
        op = i[0] & 0xFFFF
        if op == OP_LABEL:
            label = i[1]
        if pool.get(tuple(i)):
            pool[tuple(i)] -= 1
            continue
        if op == OP_BRANCH and i[1] in edges.get(label, ()):
            continue   # a branch the word folded to one of its edges
        out.append([op] + i[1:])
    return out


def present(before, after):
    """spirv-opt's result with each function's blocks in the order they had before it, and
    OpCapability ImageQuery dropped where no image query is left."""
    _, orig = raw(before)
    order = {i[1]: k for k, i in enumerate(orig) if i[0] & 0xFFFF == OP_LABEL}
    header, opt = raw(after)
    queries = any(i[0] & 0xFFFF in IMAGE_QUERIES for i in opt)
    out, k = [], 0
    while k < len(opt):
        i = opt[k]
        op = i[0] & 0xFFFF
        if op == OP_CAPABILITY and i[1] == CAPABILITY_IMAGE_QUERY and not queries:
            k += 1
            continue
        if op != OP_FUNCTION:
            out.append(i)
            k += 1
            continue
        out.append(i)
        k += 1
        while opt[k][0] & 0xFFFF == 55:   # OpFunctionParameter
            out.append(opt[k])
            k += 1
        blocks = []
        while opt[k][0] & 0xFFFF != OP_FUNCTION_END:
            b = [opt[k]]
            k += 1
            while opt[k][0] & 0xFFFF not in (OP_LABEL, OP_FUNCTION_END):
                b.append(opt[k])
                k += 1
            blocks.append(b)
        if any(b[0][1] not in order for b in blocks):
            raise Refused("spirv-opt made a block the module did not have")
        blocks.sort(key=lambda b: order[b[0][1]])
        for b in blocks:
            out.extend(b)
        out.append(opt[k])
        k += 1
    return pack(header, out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--entries", type=json.loads, default=None)
    ap.add_argument("--data", default=None)
    a = ap.parse_args(argv)
    try:
        print(json.dumps(freeze(a.src, a.dst, a.entries, a.data)))
    except (Refused, shader_tools.ToolRefused, spv_canon.SpvError, OSError) as e:
        print(f"spv_freeze.py: REFUSED {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
