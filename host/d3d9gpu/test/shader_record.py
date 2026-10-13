#!/usr/bin/env python3
"""shader_record.py: the recorded reflection of the streams' shaders that the second host's state
check takes (test/state_check.cpp --shader-record): each pixel shader's sampler and output masks,
in place of the superset stand-in for the sampler mask and against the device's oC0 stand-in for
the output mask (the R2b review's finding 2), and each vertex shader's input signature, with
which the check evaluates DXVK's vertex input in place of the declaration-order stand-in (the
review's checker-logic finding 1). KEEP OUT, OPEN-1: R1 replaces the stand-ins with reflection in
the device, and this record stays in the instrument.

Each is DXVK 3.1.1's (D3D9ShaderAnalysis, src/d3d9/d3d9_shader_analysis.cpp):

  * sampler mask: every sampler a dcl declares (HandleDcl), and the sampler operand of
    texld/texldl/texldd (HandleTextureSample); in shader model 2 and 3 these are the registers
    of type SAMPLER;
  * output mask: the index of every destination of type COLOROUT (HandleInstruction);
  * input signature: a vertex shader's dcl of each register of type INPUT, its semantic (usage,
    usage index), in dcl order (HandleDcl, :310-312, into D3D9InputSignature::add);

and is derived twice, from two independent readings, which must agree:

  * the bytecode's tokens (the census's extracted blobs, scan/<stream>/{ps,vs}N.bin), walked here;
  * dxbc-spirv's own SM3 disassembly of the same shaders (R0's disasm/{ps,vs}N.txt).

The blobs of index N must be byte-identical in every stream named (else the link from psN.txt to a
blob is ambiguous, and the record refuses). The state check links a keys file's shaders to the
record by the sha256 the device took of their bytecode ("shaders" in the keys file), never by
creation index alone.

  shader_record.py [--scan DIR] [--disasm DIR] [--streams boot menu story mh] --out RECORD.json

Exit 0 and SHADER_RECORD_OK when both readings agree for every pixel shader; 1 and
SHADER_RECORD_FAIL when one disagrees (the record is written, "agree": false, and the state check
refuses it); 2 and SHADER_RECORD_REFUSED when an input is missing or ambiguous. Shipped bytes stay
in private/: the record holds hashes and masks only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
D = REPO / "private" / "generated" / "s5" / "next" / "D"

# D3DSHADER_INSTRUCTION_OPCODE_TYPE values (d3d9types.h)
OP_DCL, OP_DEF, OP_DEFI, OP_DEFB = 31, 81, 82, 83
OP_COMMENT, OP_END = 0xFFFE, 0xFFFF
# D3DSHADER_PARAM_REGISTER_TYPE values
REG_INPUT, REG_COLOROUT, REG_SAMPLER = 1, 8, 10
# D3DDECLUSAGE values, by the names dxbc-spirv's disassembly prints (dcl_<usage><index>)
USAGES = {"position": 0, "blendweight": 1, "blendindices": 2, "normal": 3, "psize": 4, "texcoord": 5, "tangent": 6,
          "binormal": 7, "tessfactor": 8, "positiont": 9, "color": 10, "fog": 11, "depth": 12, "sample": 13}


class Refusal(Exception):
    pass


def token_masks(blob: bytes) -> dict:
    """The masks from the bytecode: every parameter token of type SAMPLER (a dcl's destination or
    a texture instruction's sampler operand) and every one of type COLOROUT (oC is write-only, so
    any is a destination). DEF/DEFI/DEFB immediates and a DCL's usage token are not parameters."""
    if len(blob) % 4:
        raise Refusal("a blob whose length is not a whole number of tokens")
    tok = struct.unpack(f"<{len(blob) // 4}I", blob)
    version = tok[0]
    if version >> 16 != 0xFFFF:
        raise Refusal(f"not pixel-shader bytecode (version token {version:#x})")
    major = (version >> 8) & 0xFF
    if major < 2:
        raise Refusal("shader model 1 bytecode (no instruction lengths; the device does not take it either)")
    samplers = rt = 0
    i = 1
    while i < len(tok):
        t = tok[i]
        op = t & 0xFFFF
        if t == 0x0000FFFF:
            return {"version": f"ps_{major}_{version & 0xFF}", "sampler_mask": samplers, "rt_mask": rt, "tokens": i + 1}
        if op == OP_COMMENT:
            i += 1 + ((t >> 16) & 0x7FFF)
            continue
        n = (t >> 24) & 0x0F
        params = list(tok[i + 1:i + 1 + n])
        if op == OP_DCL:
            params = params[1:]                 # the usage / texture-type token
        elif op in (OP_DEF, OP_DEFI, OP_DEFB):
            params = params[:1]                 # the register; the rest are immediates
        for p in params:
            if not p & 0x80000000:
                continue
            reg = ((p >> 28) & 0x7) | ((p >> 8) & 0x18)
            idx = p & 0x7FF
            if reg == REG_SAMPLER:
                samplers |= 1 << idx
            elif reg == REG_COLOROUT:
                rt |= 1 << idx
        i += 1 + n
    raise Refusal("no END token")


SAMPLER_RE = re.compile(r"(?<![\w.])s(\d+)\b")
OC_RE = re.compile(r"(?<![\w.])oC(\d+)\b")


def disasm_masks(text: str) -> dict:
    """The masks from dxbc-spirv's disassembly: sN operands anywhere (dcl_2d sN, texld ..., sN)
    and oCN operands anywhere."""
    lines = text.splitlines()
    head = lines[0].strip().rstrip(":") if lines else ""
    if not head.startswith("ps_"):
        raise Refusal(f"a disassembly that is not a pixel shader's ({head!r})")
    samplers = rt = 0
    for line in lines[1:]:
        m = re.match(r"\s*\d+:\s+(\S+)\s*(.*)$", line)
        if not m:
            continue
        operands = m.group(2)
        if m.group(1).startswith("def"):
            continue
        for s in SAMPLER_RE.findall(operands):
            samplers |= 1 << int(s)
        for o in OC_RE.findall(operands):
            rt |= 1 << int(o)
    return {"version": head, "sampler_mask": samplers, "rt_mask": rt}


def token_signature(blob: bytes) -> dict:
    """A vertex shader's input signature from its bytecode: each DCL whose destination is of type
    INPUT, as [usage, usage index] (the usage token's bits 0-4 and 16-19), in dcl order."""
    if len(blob) % 4:
        raise Refusal("a blob whose length is not a whole number of tokens")
    tok = struct.unpack(f"<{len(blob) // 4}I", blob)
    version = tok[0]
    if version >> 16 != 0xFFFE:
        raise Refusal(f"not vertex-shader bytecode (version token {version:#x})")
    major = (version >> 8) & 0xFF
    if major < 2:
        raise Refusal("shader model 1 bytecode (no instruction lengths; the device does not take it either)")
    sig = []
    i = 1
    while i < len(tok):
        t = tok[i]
        op = t & 0xFFFF
        if t == 0x0000FFFF:
            return {"version": f"vs_{major}_{version & 0xFF}", "input_signature": sig, "tokens": i + 1}
        if op == OP_COMMENT:
            i += 1 + ((t >> 16) & 0x7FFF)
            continue
        n = (t >> 24) & 0x0F
        if op == OP_DCL and n >= 2:
            usage_tok, dst = tok[i + 1], tok[i + 2]
            reg = ((dst >> 28) & 0x7) | ((dst >> 8) & 0x18)
            if reg == REG_INPUT:
                sig.append([usage_tok & 0x1F, (usage_tok >> 16) & 0xF])
        i += 1 + n
    raise Refusal("no END token")


DCL_RE = re.compile(r"^dcl_([a-z]+?)(\d+)$")


def disasm_signature(text: str) -> dict:
    """A vertex shader's input signature from dxbc-spirv's disassembly: each dcl_<usage><index>
    of a v register, in order."""
    lines = text.splitlines()
    head = lines[0].strip().rstrip(":") if lines else ""
    if not head.startswith("vs_"):
        raise Refusal(f"a disassembly that is not a vertex shader's ({head!r})")
    sig = []
    for line in lines[1:]:
        m = re.match(r"\s*\d+:\s+(\S+)\s*(.*)$", line)
        if not m or not m.group(1).startswith("dcl_"):
            continue
        reg = m.group(2).strip()
        if not re.fullmatch(r"v\d+", reg):
            continue
        d = DCL_RE.match(m.group(1))
        if not d or d.group(1) not in USAGES:
            raise Refusal(f"a vertex-shader input dcl this tool cannot read: {m.group(1)} {reg}")
        sig.append([USAGES[d.group(1)], int(d.group(2))])
    return {"version": head, "input_signature": sig}


def blobs_by_index(scan: Path, streams: list[str], kind: str) -> tuple[dict[int, dict], dict[str, list[str]]]:
    by_index: dict[int, dict] = {}
    record_streams: dict[str, list[str]] = {}
    for s in streams:
        d = scan / s
        if not d.is_dir():
            raise Refusal(f"no stream directory {d}")
        blobs = sorted(d.glob(f"{kind}*.bin"), key=lambda p: int(p.stem[2:]))
        if not blobs:
            raise Refusal(f"no {kind} blob in {d}")
        record_streams[s] = []
        for b in blobs:
            n = int(b.stem[2:])
            data = b.read_bytes()
            h = hashlib.sha256(data).hexdigest()
            record_streams[s].append(h)
            e = by_index.setdefault(n, {"sha256": h, "data": data, "streams": []})
            if e["sha256"] != h:
                raise Refusal(f"{kind}{n}: the blobs differ between streams ({e['streams'][0]} and {s}): {kind}N.txt's link is ambiguous")
            e["streams"].append(s)
    return by_index, record_streams


def build(scan: Path, disasm: Path, streams: list[str]) -> dict:
    by_index, record_streams = blobs_by_index(scan, streams, "ps")
    out, agree = [], True
    for n in sorted(by_index):
        e = by_index[n]
        dpath = disasm / f"ps{n}.txt"
        if not dpath.is_file():
            raise Refusal(f"no disassembly {dpath}")
        dtext = dpath.read_text()
        tm = token_masks(e["data"])
        dm = disasm_masks(dtext)
        ok = (tm["sampler_mask"], tm["rt_mask"], tm["version"]) == (dm["sampler_mask"], dm["rt_mask"], dm["version"])
        agree &= ok
        out.append({"label": f"ps{n}", "index": n, "sha256": e["sha256"], "bytes": len(e["data"]), "streams": e["streams"],
                    "version": tm["version"], "sampler_mask": tm["sampler_mask"], "rt_mask": tm["rt_mask"],
                    "from_tokens": tm, "from_disassembly": {**dm, "path": str(dpath),
                                                            "sha256": hashlib.sha256(dtext.encode()).hexdigest()},
                    "agree": ok})
    vs_index, vs_streams = blobs_by_index(scan, streams, "vs")
    vs_out = []
    for n in sorted(vs_index):
        e = vs_index[n]
        dpath = disasm / f"vs{n}.txt"
        if not dpath.is_file():
            raise Refusal(f"no disassembly {dpath}")
        dtext = dpath.read_text()
        ts = token_signature(e["data"])
        ds = disasm_signature(dtext)
        ok = (ts["input_signature"], ts["version"]) == (ds["input_signature"], ds["version"])
        agree &= ok
        vs_out.append({"label": f"vs{n}", "index": n, "sha256": e["sha256"], "bytes": len(e["data"]), "streams": e["streams"],
                       "version": ts["version"], "input_signature": ts["input_signature"],
                       "from_tokens": ts, "from_disassembly": {**ds, "path": str(dpath),
                                                               "sha256": hashlib.sha256(dtext.encode()).hexdigest()},
                       "agree": ok})
    return {
        "schema": "sa2-d3d9gpu-shader-record/v2",
        "tool": "host/d3d9gpu/test/shader_record.py",
        "rule": "DXVK 3.1.1 D3D9ShaderAnalysis (src/d3d9/d3d9_shader_analysis.cpp): sampler mask = the dcl'd samplers and "
                "the texture instructions' sampler operands; output mask = the destinations of type COLOROUT; input "
                "signature = a vertex shader's dcl'd INPUT registers' semantics, in dcl order",
        "scan": str(scan), "disasm": str(disasm), "streams": record_streams, "vs_streams": vs_streams,
        "ps": out, "vs": vs_out, "agree": agree,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scan", default=str(D / "scan"))
    ap.add_argument("--disasm", default=str(D / "disasm"))
    ap.add_argument("--streams", nargs="+", default=["boot", "menu", "story", "mh"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        rec = build(Path(a.scan), Path(a.disasm), a.streams)
    except Refusal as e:
        print(f"SHADER_RECORD_REFUSED: {e}", file=sys.stderr)
        return 2
    Path(a.out).write_text(json.dumps(rec, indent=1) + "\n")
    for p in rec["ps"]:
        print(f"{p['label']}: sampler mask {p['sampler_mask']:#x}, output mask {p['rt_mask']:#x}"
              f"{'' if p['agree'] else ' DISAGREE: tokens ' + repr(p['from_tokens']) + ', disassembly ' + repr(p['from_disassembly'])}")
    for v in rec["vs"]:
        print(f"{v['label']}: input signature {v['input_signature']}"
              f"{'' if v['agree'] else ' DISAGREE: tokens ' + repr(v['from_tokens']) + ', disassembly ' + repr(v['from_disassembly'])}")
    print("SHADER_RECORD_OK" if rec["agree"] else "SHADER_RECORD_FAIL")
    return 0 if rec["agree"] else 1


if __name__ == "__main__":
    sys.exit(main())
