#!/usr/bin/env python3
"""Compare host 2's SPIR-V with DXVK's for one Direct3D 9 shader, and explain every difference.

Subtitle: R1's canonical diff (check C3, the R1 plan's section 4), promoted from unit R0's
private diff/spvdiff.py and hardened. A comparer: it decides whether host 2 computes what DXVK
computes, so it is held to two levels, and each can fail.

L1, the canonical instruction stream. Every value is labelled by what it is, not by its id:
an instruction's label is its opcode, its type's structure, its literals and its operands'
labels (value numbering), so an inserted instruction cannot shift the labels after it. A value
whose structure does not say which one it is gets its place as well, so two of them are never one
label: a function parameter its index, a phi its ordinal in its function, a Function or Private
variable its ordinal in its scope, and a load from memory a function can write (a Function,
Private, Workgroup or Output variable) its ordinal among those loads in its function. Debug
instructions are dropped. Then the only freedoms are the classes of spv_explained.json, each a
rewrite of a state fetch or a descriptor's place into one token both sides share: spec data,
push data, the sampler descriptor, descriptor sets and bindings, the dynamic float buffer's
declared length, the state blocks' place, the capabilities only one side's state fetch needs,
and the BGRA input swizzle of a variant. After those rewrites every function's instruction
sequence, every interface variable, every decoration of a non-descriptor (NoContraction,
Location, BuiltIn, ...), every execution mode and the static constant blocks' layout must be
equal, and each side's state blocks must match state_layout's table member by member (Offset
and type, by name); anything else is SPV_CANON_FAIL naming the shader, the function and the
instruction.

L2, the arithmetic (R0's two checks, kept exact): per function the sequence of float
instructions, and every value that leaves a function (output stores, returns, branch and switch
conditions, demotes) as an expression DAG in which state fetches are the same tokens.

  spv_canon.py DXVK.spv HOST2.spv [--bgra MASK] [--json] [--reference-side host2] [--linked]
  spv_canon.py --batch DXVK_DIR HOST2_DIR [--expect N]
  spv_canon.py --linked-batch CAPTURE_DIR PAIRS_DIR --gpl-dump DIR [--expect-drawn N]

The batch compares every host-2 shader (a reflection JSON beside its module) with the DXVK
module its debug name names, and fails on anything it cannot pair: an unreadable or foreign
reflection, a module without one, a DXVK module no host-2 shader names, or a count other than
--expect's.

The linked batch is C3's second DXVK source: the modules DXVK hands the driver for its optimised
(linked) pipelines, as the capture layer recorded them (host/d3d9gpu/test/vkcap: CAPTURE_DIR's
pipelines.jsonl and module-<sha256>.spv), against host 2's pair modules (d3d9gpu_shaderc load's
pairs/<pair>.json, .vs.spv and .ps.spv: the pixel shader linked against its vertex shader as
DXVK's getCode(bindings, &linkage) does). DXVK's linked variant keeps its spec constants as
OpSpecConstant (the driver specialises them with the pipeline's VkSpecializationInfo, which the
capture records and R3 bisects with); host 2 reads the same spec dword from its state buffer. So in
--linked mode an OpSpecConstant with SpecId N is the token STATE[spec:N], as host 2's load of
state member N is (spec_data), host 2's spec loads leave its canonical streams (DXVK's module has
no instruction where they stand), and DXVK's SpecId decorations are spec_data's. Every linked
pipeline's pair of modules must be matched to a host-2 pair by their debug names, every drawn
host-2 pair (--expect-drawn of them) must have a linked capture, and with --gpl-dump every module
of DXVK's pipeline-library pipelines must equal, canonically and but for where the pipeline's layout
puts its descriptors and push members, the module DXVK_SHADER_DUMP_PATH wrote for the same shader
(the dump the GPL batch compares is what DXVK handed the driver), and the library modules must cover
the dump exactly. A pair is matched by its two debug names and its BGRA mask: DXVK's from its
pipeline's vertex input (each location whose attribute is VK_FORMAT_B8G8R8A8_UNORM, the format
BindInputLayout gives a D3DCOLOR), host 2's from its variant, so the mask host 2 swizzles is the one
DXVK's vertex format implies. A host-2 pair whose pixel shader is specialized at a sampler-mode word
(its variant's sampler_modes=<word>, d9g_shader.h Variant::sampler_modes) is compared with
DXVK's linked pixel shader frozen at the word DXVK's pipeline gives (test/spv_freeze.py: SpecId 5 from
the stage's VkSpecializationInfo, or its default where the pipeline omits it, folded with the pinned
spirv-opt), and host 2's word must be that word; a pair whose pixel shader is the base is compared
with DXVK's module as it is. The report's sampler_modes names where each word came from (word_sources:
"default", "entry", or "no SpecId 5" for a module that declares none and is compared as it is) and the
pixel shaders compared unfrozen (not_frozen).

Exit status 0 and SPV_CANON_OK only when both levels pass; 1 and SPV_CANON_FAIL otherwise.
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import struct
import sys
import tempfile
from collections import Counter, OrderedDict
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
GRAMMAR_DIR = Path(os.environ.get("SA2_SPIRV_GRAMMAR", str(
    REPO / "private/s5-tools/src/dxvk-3.1.1/subprojects/dxbc-spirv/submodules/spirv_headers/include/spirv/unified1")))
EXPLAINED = Path(__file__).resolve().parent / "spv_explained.json"

_grammar = None


def grammar():
    global _grammar
    if _grammar is None:
        core = json.load(open(GRAMMAR_DIR / "spirv.core.grammar.json"))
        glsl = json.load(open(GRAMMAR_DIR / "extinst.glsl.std.450.grammar.json"))
        _grammar = ({i["opcode"]: i for i in core["instructions"]},
                    {k["kind"]: k for k in core["operand_kinds"]},
                    {i["opcode"]: i["opname"] for i in glsl["instructions"]})
    return _grammar


ID_KINDS = {"IdResultType", "IdResult", "IdRef", "IdMemorySemantics", "IdScope"}
DEBUG_OPS = {"OpName", "OpMemberName", "OpSource", "OpSourceContinued", "OpSourceExtension", "OpString",
             "OpLine", "OpNoLine", "OpModuleProcessed"}


class SpvError(Exception):
    pass


class Inst:
    __slots__ = ("op", "rtype", "rid", "ids", "lits", "index")

    def __init__(self, op, index):
        self.op, self.index = op, index
        self.rtype = self.rid = None
        self.ids, self.lits = [], []


def _enum_value(e):
    v = e["value"]
    return int(v, 0) if isinstance(v, str) else v


def _operand(kind, words, pos, inst):
    OPS, KINDS, _ = grammar()
    if pos >= len(words):
        raise SpvError(f"{inst.op}: operand {kind} past the instruction's end")
    if kind in ID_KINDS:
        if kind == "IdResultType":
            inst.rtype = words[pos]
        elif kind == "IdResult":
            inst.rid = words[pos]
        else:
            inst.ids.append(words[pos])
        return pos + 1
    if kind == "LiteralString":
        raw = b""
        while True:
            if pos >= len(words):
                raise SpvError(f"{inst.op}: unterminated string")
            chunk = struct.pack("<I", words[pos])
            pos += 1
            raw += chunk
            if 0 in chunk:
                break
        inst.lits.append(raw.split(b"\0", 1)[0].decode("utf-8", "replace"))
        return pos
    if kind == "LiteralContextDependentNumber":
        inst.lits.append(tuple(words[pos:]))
        return len(words)
    if kind == "PairLiteralIntegerIdRef":
        inst.lits.append(words[pos]); inst.ids.append(words[pos + 1])
        return pos + 2
    if kind == "PairIdRefLiteralInteger":
        inst.ids.append(words[pos]); inst.lits.append(words[pos + 1])
        return pos + 2
    if kind == "PairIdRefIdRef":
        inst.ids.extend(words[pos:pos + 2])
        return pos + 2
    if kind.startswith("Literal"):
        inst.lits.append(words[pos])
        return pos + 1
    k = KINDS.get(kind)
    if k is None:
        raise SpvError(f"unknown operand kind {kind}")
    value = words[pos]
    pos += 1
    if k["category"] == "ValueEnum":
        match = next((e for e in k.get("enumerants", []) if _enum_value(e) == value), None)
        inst.lits.append((kind, match["enumerant"] if match else value))
        for p in (match or {}).get("parameters", []):
            pos = _operand(p["kind"], words, pos, inst)
        return pos
    if k["category"] == "BitEnum":
        hits = [e for e in sorted(k.get("enumerants", []), key=_enum_value) if _enum_value(e) and value & _enum_value(e)]
        inst.lits.append((kind, "|".join(e["enumerant"] for e in hits) if hits else "None"))
        for e in hits:
            for p in e.get("parameters", []):
                pos = _operand(p["kind"], words, pos, inst)
        return pos
    raise SpvError(f"unhandled operand category for {kind}")


def parse_words(data, where):
    if len(data) % 4 or len(data) < 20:
        raise SpvError(f"{where}: not a whole SPIR-V module")
    words = list(struct.unpack("<%dI" % (len(data) // 4), data))
    if words[0] != 0x07230203:
        raise SpvError(f"{where}: bad magic")
    OPS, _, _ = grammar()
    insts, pos = [], 5
    while pos < len(words):
        wc, opc = words[pos] >> 16, words[pos] & 0xFFFF
        if wc == 0 or pos + wc > len(words):
            raise SpvError(f"{where}: instruction at word {pos} overruns the module")
        if opc not in OPS:
            raise SpvError(f"{where}: unknown opcode {opc}")
        g = OPS[opc]
        inst = Inst(g["opname"], len(insts))
        body = words[pos + 1:pos + wc]
        p = 0
        for spec in g.get("operands", []):
            q = spec.get("quantifier")
            if q == "?":
                if p < len(body):
                    p = _operand(spec["kind"], body, p, inst)
            elif q == "*":
                while p < len(body):
                    p = _operand(spec["kind"], body, p, inst)
            else:
                p = _operand(spec["kind"], body, p, inst)
        if inst.op == "OpSpecConstantOp" and p < len(body):
            # the embedded opcode's operands (the grammar names only the opcode): ids, but for
            # the composite and shuffle forms, whose trailing operands are literals
            inner = OPS.get(inst.lits[-1], {}).get("opname", "") if inst.lits else ""
            rest = body[p:]
            n_ids = {"OpCompositeExtract": 1, "OpCompositeInsert": 2, "OpVectorShuffle": 2}.get(inner, len(rest))
            inst.ids.extend(rest[:n_ids])
            inst.lits.extend(rest[n_ids:])
            p = len(body)
        if p != len(body):
            raise SpvError(f"{where}: {inst.op} has {len(body) - p} unread words")
        insts.append(inst)
        pos += wc
    return insts


def lit(l):
    return str(l[1]) if isinstance(l, tuple) and len(l) == 2 and isinstance(l[0], str) else str(l)


class Module:
    """One module, with the roles of its descriptors as one side lays them out."""

    def __init__(self, path, side, bgra_mask=0, role_names=None):
        self.path, self.side, self.bgra_mask = str(path), side, bgra_mask
        # A module DXVK handed the driver (the capture layer's): its descriptors sit where the
        # pipeline's layout puts them, so their roles are read by name from DXVK's own dump of the
        # same shader (dump_roles), and its push members sit at the layout's offsets (state_layout
        # holds them relative to their group). None: the dump's own layout (sets and bindings).
        self.role_names = role_names
        self.insts = parse_words(Path(path).read_bytes(), path)
        self.defs, self.names, self.member_names = {}, {}, {}
        self.decos, self.member_decos = {}, {}
        self.functions, self.func_name = OrderedDict(), {}
        self.capabilities, self.extensions, self.entry, self.exec_modes = set(), [], None, []
        self.linked = False   # --linked: DXVK's linked variant (OpSpecConstant spec data)
        self.debug_name = None   # dxbc-spirv's OpSource file string: "vs.<md5>" / "fs.<md5>"
        strings = {}
        cur = None
        for i in self.insts:
            if i.rid is not None:
                self.defs[i.rid] = i
            if i.op == "OpName":
                self.names[i.ids[0]] = i.lits[0]
            elif i.op == "OpMemberName":
                self.member_names[(i.ids[0], i.lits[0])] = i.lits[1]
            elif i.op == "OpDecorate":
                self.decos.setdefault(i.ids[0], []).append(i)
            elif i.op == "OpMemberDecorate":
                self.member_decos.setdefault(i.ids[0], []).append(i)
            elif i.op == "OpCapability":
                self.capabilities.add(lit(i.lits[0]))
            elif i.op in ("OpExtension",):
                self.extensions.append(i.lits[0])
            elif i.op == "OpString":
                strings[i.rid] = i.lits[0]
            elif i.op == "OpSource" and i.ids and i.ids[0] in strings and self.debug_name is None:
                self.debug_name = strings[i.ids[0]]
            elif i.op == "OpEntryPoint":
                self.entry = i
            elif i.op in ("OpExecutionMode", "OpExecutionModeId"):
                self.exec_modes.append(i)
            elif i.op == "OpFunction":
                cur = [i]
                self.functions[i.rid] = cur
                continue
            if cur is not None:
                cur.append(i)
                if i.op == "OpFunctionEnd":
                    cur = None
        if self.entry is None:
            raise SpvError(f"{path}: no entry point")
        self.stage = lit(self.entry.lits[0])
        for fid in self.functions:
            self.func_name[fid] = self.names.get(fid, "")
        self.entry_fn = self.entry.ids[0]

    def deco(self, vid, name):
        for d in self.decos.get(vid, []):
            if lit(d.lits[0]) == name:
                return [lit(x) for x in d.lits[1:]] or [True]
        return None

    def pointee(self, var):
        return self.defs[self.defs[var.rtype].ids[0]]

    def storage(self, var):
        return lit(var.lits[0])

    def array_length(self, tid):
        t = self.defs[tid]
        return int(self.defs[t.ids[1]].lits[0][0]) if t.op == "OpTypeArray" else None


# D3D9ShaderResourceMapping::CbvIndex (src/d3d9/d3d9_shader.h:53-63) and SDL's slots
# (host/d3d9gpu/d9g_shader.h UniformSlot), as roles.
DXVK_CBV = {"Vertex": {0: "clip_planes", 3: "static", 4: "dynamic"}, "Fragment": {5: "texture_stages", 6: "static"}}
HOST2_CBV = {"Vertex": (1, {0: "static", 1: "dynamic", 2: "clip_planes", 3: "state"}),
             "Fragment": (3, {0: "static", 1: "state", 2: "texture_stages"})}


def register_of(name):
    """The D3D9 sampler register of a dxbc-spirv resource name (sN_..., or sN)."""
    if name and name[0] == "s":
        digits = ""
        for ch in name[1:]:
            if not ch.isdigit():
                break
            digits += ch
        if digits:
            return int(digits)
    return None


class Labeller:
    """Value numbering of one module, with the explanation classes applied for its side."""

    def __init__(self, mod, hits):
        self.m, self.hits = mod, hits
        self.memo, self.absorbed, self.unexplained = {}, set(), []
        self.roles = {}
        self.bindings = {}
        # Every result's place in its function, so that a loop's back-edge operand (defined after
        # the OpPhi that reads it) is named by where it is defined rather than left unlabelled.
        self.pos = {}
        self.back_edges = {}
        for fid, body in mod.functions.items():
            for n, i in enumerate(body):
                if i.rid is not None:
                    self.pos[i.rid] = (fid, n)
        self._roles()

    def note(self, cls):
        self.hits[self.m.side if self.m.side in self.hits else "host2"][cls] += 1

    def _roles(self):
        m = self.m
        for i in m.insts:
            if i.op != "OpVariable":
                continue
            sc = m.storage(i)
            if sc not in ("Uniform", "UniformConstant", "PushConstant", "StorageBuffer"):
                continue
            ptype = m.pointee(i)
            name = m.names.get(i.rid, "")
            setd, bind = m.deco(i.rid, "DescriptorSet"), m.deco(i.rid, "Binding")
            setn = int(setd[0]) if setd else None
            bindn = int(bind[0]) if bind else None
            role = None
            if sc == "PushConstant":
                role = "push" if m.side == "dxvk" else None
            elif sc == "Uniform":
                if m.side == "dxvk" and m.role_names is not None:
                    role = m.role_names.get(name)
                elif m.side == "dxvk":
                    role = "spec" if setn == 7 else DXVK_CBV.get(m.stage, {}).get(bindn) if setn == 1 else None
                else:
                    want_set, slots = HOST2_CBV.get(m.stage, (None, {}))
                    role = slots.get(bindn) if setn == want_set else None
            elif sc == "UniformConstant":
                if ptype.op == "OpTypeRuntimeArray" and m.side == "dxvk":
                    role = "sampler_heap"
                elif ptype.op == "OpTypeSampler":
                    reg = register_of(name)
                    role = f"sampler:{reg}" if reg is not None else None
                elif ptype.op in ("OpTypeImage", "OpTypeSampledImage"):
                    reg = register_of(name)
                    role = f"image:{reg}" if reg is not None else None
            if role is None:
                self.unexplained.append(f"descriptor {name or i.rid} ({sc}, set {setn}, binding {bindn}) has no role on the {m.side} side")
                role = f"unknown:{i.rid}"
            self.roles[i.rid] = role
            self.bindings[i.rid] = (setn, bindn)
            if setn is not None:
                self.note("set_binding")
        # Host 2 binds a register's image and sampler at the register's rank among the shader's
        # registers (SDL's compacted slots); DXVK binds its images at the register
        # (computeTextureBinding). A binding that is not the register's place fails.
        # The set too: host 2's textures sit in SDL's texture set of the stage (vertex 0, fragment 2),
        # DXVK's dump puts its images in set 2 and its samplers in set 0 (setIndexForType,
        # dxvk_shader_ir.cpp:149-158); a captured module's sit where its pipeline's layout puts them.
        regs = sorted({int(r.split(":")[1]) for r in self.roles.values() if r.startswith(("sampler:", "image:"))})
        for vid, role in self.roles.items():
            if role.startswith(("sampler:", "image:")):
                reg = int(role.split(":")[1])
                want = regs.index(reg) if m.side == "host2" else (reg if m.stage == "Fragment" and m.role_names is None else None)
                if want is not None and self.bindings[vid][1] != want:
                    self.unexplained.append(f"{role} is bound at {self.bindings[vid][1]}, not at its register's place {want}")
                if m.side == "host2":
                    want_set = 0 if m.stage == "Vertex" else 2
                elif m.role_names is None:
                    want_set = 2 if role.startswith("image:") else 0
                else:
                    want_set = None
                if want_set is not None and self.bindings[vid][0] != want_set:
                    self.unexplained.append(f"{role} is bound in set {self.bindings[vid][0]}, not in the {m.side} side's "
                                            f"texture set {want_set}")

    # --- types ---------------------------------------------------------------------------
    def type_label(self, tid):
        key = ("T", tid)
        if key in self.memo:
            return self.memo[key]
        t = self.m.defs.get(tid)
        if t is None:
            raise SpvError(f"{self.m.path}: undefined type %{tid}")
        if t.op == "OpTypeArray":
            s = f"arr({self.type_label(t.ids[0])},{self.m.array_length(tid)})"
        elif t.op == "OpTypePointer":
            s = f"ptr({lit(t.lits[0])},{self.type_label(t.ids[0])})"
        elif t.op == "OpTypeStruct":
            s = "struct(" + ",".join(self.type_label(x) for x in t.ids) + ")"
        else:
            s = t.op[6:] + "(" + ",".join([self.type_label(x) for x in t.ids] + [lit(l) for l in t.lits]) + ")"
        self.memo[key] = s
        return s

    # --- values ----------------------------------------------------------------------------
    def chain(self, pid):
        """(root variable, [index ids]) of an access chain, or (None, [])."""
        idx = []
        seen = 0
        while seen < 64:
            d = self.m.defs.get(pid)
            if d is None:
                return None, []
            if d.op == "OpVariable":
                return d, list(reversed(idx))
            if d.op in ("OpAccessChain", "OpInBoundsAccessChain"):
                idx.extend(reversed(d.ids[1:]))
                pid = d.ids[0]
                seen += 1
                continue
            return None, []
        return None, []

    def const(self, cid):
        d = self.m.defs.get(cid)
        if d is not None and d.op == "OpConstant":
            return d.lits[0][0] if d.lits and d.lits[0] else 0
        return None

    def absorb_chain(self, pid):
        while True:
            d = self.m.defs.get(pid)
            if d is None or d.op not in ("OpAccessChain", "OpInBoundsAccessChain"):
                return
            self.absorbed.add(d.rid)
            pid = d.ids[0]

    def state_token(self, ld):
        """The shared token of a state load, or None when the load is not one."""
        m = self.m
        var, idx = self.chain(ld.ids[0])
        if var is None or var.rid not in self.roles:
            return None
        role = self.roles[var.rid]
        block = m.pointee(var)
        if role in ("push", "spec", "state"):
            if not idx or self.const(idx[0]) is None:
                self.unexplained.append(f"a {role} load whose member is not constant")
                return None
            k = int(self.const(idx[0]))
            members = block.ids if block.op == "OpTypeStruct" else [block.rid]
            rest = ",".join(self.value(x) for x in idx[1:])
            name = m.member_names.get((block.rid, k), "")
            if role == "spec":
                tok, cls = f"spec:{k}", "spec_data"
            elif role == "push":
                reg = register_of(name)
                if reg is not None and name.startswith("s"):
                    tok, cls = f"sampler_index:{reg}", None
                else:
                    tok, cls = f"push:{name}", "push_data"
            else:
                tok, cls = (f"spec:{k}", "spec_data") if k < 7 else (f"push:{name}", "push_data")
            if not name and role != "spec" and not (role == "state" and k < 7):
                self.unexplained.append(f"a {role} member {k} without a name")
            self.absorb_chain(ld.ids[0])
            if cls:
                self.note(cls)
            return f"STATE[{tok}{('|' + rest) if rest else ''}]:{self.type_label(ld.rtype)}"
        if role == "sampler_heap":
            # DXVK: the heap indexed by UConvert(the sN push member)
            if len(idx) != 1:
                self.unexplained.append("a sampler heap load without one index")
                return None
            ix = m.defs.get(idx[0])
            if ix is None or ix.op != "OpUConvert":
                self.unexplained.append("a sampler heap index that is not a converted push member")
                return None
            inner = m.defs.get(ix.ids[0])
            tok = self.value(inner.rid) if inner is not None and inner.op == "OpLoad" else ""
            if not tok.startswith("STATE[sampler_index:"):
                self.unexplained.append("a sampler heap index that is not a sampler push member")
                return None
            reg = tok[len("STATE[sampler_index:"):].split("]")[0]
            self.absorbed.update({ix.rid, inner.rid})
            self.absorb_chain(ld.ids[0])
            self.note("sampler_descriptor")
            return f"SAMPLER[{reg}]"
        if role.startswith("sampler:"):
            return f"SAMPLER[{role[8:]}]"
        if role.startswith("image:"):
            return f"IMAGE[{role[6:]}]:{self.type_label(ld.rtype)}"
        return None

    def var_label(self, v):
        m = self.m
        sc = m.storage(v)
        role = self.roles.get(v.rid)
        if role is not None:
            if role == "dynamic":
                return "VAR(dynamic)"  # its declared length is compared once, by dynamic_cf_length
            if role in ("static", "clip_planes", "texture_stages"):
                return f"VAR({role},{self.type_label(v.rtype)})"
            return f"VAR({role})"
        if sc in ("Input", "Output"):
            loc, bi = m.deco(v.rid, "Location"), m.deco(v.rid, "BuiltIn")
            comp = m.deco(v.rid, "Component")
            return f"IO({sc},{'BI:' + bi[0] if bi else 'LOC:' + loc[0] if loc else '?'},{comp[0] if comp else 0},{self.type_label(v.rtype)})"
        # any other variable: its ordinal among its scope's variables of its storage class (a Function
        # variable's function, the module for the rest), so two of one type are not one variable
        return f"VAR({sc},{self.type_label(v.rtype)},#{self.var_ordinal(v)})"

    def var_ordinal(self, v):
        m = self.m
        fid = self.pos.get(v.rid, (None, -1))[0]
        scope = m.functions[fid] if fid is not None else [i for i in m.insts if i.op == "OpVariable" and i.rid not in self.pos]
        same = [i.rid for i in scope if i.op == "OpVariable" and m.storage(i) == m.storage(v)]
        return same.index(v.rid)

    MUTABLE = ("Function", "Private", "Workgroup", "Output")

    def load_ordinal(self, d):
        """A load from memory a function can write, its ordinal among those loads in its function
        (None for any other load): two loads of one variable around a store are not one value."""
        var, _ = self.chain(d.ids[0])
        if var is None or var.rid in self.roles or self.m.storage(var) not in self.MUTABLE:
            return None
        fid, n = self.pos.get(d.rid, (None, -1))
        if fid is None:
            return None
        k = 0
        for x in self.m.functions[fid][:n]:
            if x.op == "OpLoad":
                xv, _ = self.chain(x.ids[0])
                if xv is not None and xv.rid not in self.roles and self.m.storage(xv) in self.MUTABLE:
                    k += 1
        return k

    def value(self, vid):
        if vid in self.memo:
            return self.memo[vid]
        self.memo[vid] = "CYCLE"
        d = self.m.defs.get(vid)
        if d is None:
            raise SpvError(f"{self.m.path}: undefined id %{vid}")
        r = self._value(d)
        self.memo[vid] = r
        return r

    def _value(self, d):
        m = self.m
        op = d.op
        if op.startswith("OpType"):
            return self.type_label(d.rid)
        if op == "OpSpecConstant" and m.side == "dxvk" and m.linked:
            spec_id = m.deco(d.rid, "SpecId")
            if spec_id is None:
                self.unexplained.append(f"an OpSpecConstant without a SpecId (%{d.rid})")
                return f"SPEC?({d.rid})"
            self.note("spec_data")
            return f"STATE[spec:{int(spec_id[0])}]:{self.type_label(d.rtype)}"
        if op in ("OpConstant", "OpConstantTrue", "OpConstantFalse", "OpConstantNull", "OpUndef"):
            return f"K({self.type_label(d.rtype)}:{op[10:]}{d.lits[0] if d.lits else ''})"
        if op == "OpConstantComposite":
            return f"K({self.type_label(d.rtype)}:{{{','.join(self.value(i) for i in d.ids)}}})"
        if op == "OpVariable":
            return self.var_label(d)
        if op == "OpFunction":
            return f"FN({m.func_name.get(d.rid, '') or ('entry' if d.rid == m.entry_fn else d.rid)})"
        if op == "OpExtInstImport":
            return f"IMPORT({d.lits[0]})"
        if op == "OpLoad":
            tok = self.state_token(d)
            if tok is not None:
                return tok
            k = self.load_ordinal(d)
            if k is not None:
                return f"Load#{k}({self.type_label(d.rtype)};{self.value(d.ids[0])})"
        if op in ("OpAccessChain", "OpInBoundsAccessChain"):
            var, idx = self.chain(d.rid)
            if var is not None and m.side == "host2" and self.m.bgra_mask and m.storage(var) == "Input":
                loc = m.deco(var.rid, "Location")
                if loc and (self.m.bgra_mask >> int(loc[0])) & 1 and len(idx) == 1:
                    c = self.const(idx[0])
                    if c is not None and c < 3:
                        self.note("bgra_input_swizzle")
                        ktype = self.type_label(self.m.defs[idx[0]].rtype)
                        return f"{op[2:]}({self.type_label(d.rtype)};{self.value(var.rid)};K({ktype}:({2 - c},)))"
        if op == "OpFunctionParameter":
            # its index among its function's parameters: two parameters of one type are not one value
            fid, n = self.pos.get(d.rid, (None, -1))
            k = sum(1 for x in self.m.functions[fid][:n] if x.op == "OpFunctionParameter") if fid is not None else -1
            return f"PARAM{k}({self.type_label(d.rtype)})"
        if op == "OpLabel":
            return f"LABEL@{self.pos.get(d.rid, ('', -1))[1]}"
        if op == "OpPhi":
            # A back-edge operand (defined after the OpPhi) is labelled after the phi itself, by
            # function_stream's PHIBACK entries, so a loop never labels through a cycle.
            here = self.pos.get(d.rid, (None, -1))
            ops = []
            for x in d.ids:
                px = self.pos.get(x)
                back = px is not None and px[0] == here[0] and px[1] >= here[1]
                ops.append("BACK" if back else self.value(x))
                if back:
                    self.back_edges.setdefault(d.rid, []).append(x)
            # its ordinal among its function's phis: two loop accumulators of one type, one initial
            # value and one preheader are not one value
            k = sum(1 for x in self.m.functions[here[0]][:here[1]] if x.op == "OpPhi") if here[0] is not None else -1
            return f"Phi{k}({self.type_label(d.rtype)};{';'.join(ops)})"
        parts = [self.type_label(d.rtype)] if d.rtype else []
        parts += [self.value(i) for i in d.ids]
        parts += [lit(l) for l in d.lits]
        s = f"{op[2:]}({';'.join(parts)})"
        return s if len(s) < 200 else f"{op[2:]}#" + hashlib.sha1(s.encode()).hexdigest()[:16]

    def function_stream(self, fid):
        out = []
        labels = {}
        for i in self.m.functions[fid]:
            if i.op == "OpLabel":
                labels[i.rid] = f"L{len(labels)}"
        for i in self.m.functions[fid]:
            if i.op in DEBUG_OPS or (i.rid is not None and i.rid in self.absorbed):
                continue
            if i.op == "OpLabel":
                out.append((f"LABEL {labels[i.rid]}", i))
                continue
            if i.rid is not None and i.op not in ("OpFunction", "OpFunctionParameter"):
                self.value(i.rid)
            if i.op in ("OpBranch", "OpBranchConditional", "OpSelectionMerge", "OpLoopMerge", "OpPhi", "OpSwitch"):
                ops = [labels.get(x) or self.value(x) for x in i.ids]
                out.append((f"{i.op[2:]}({';'.join(ops)};{';'.join(lit(l) for l in i.lits)})", i))
            elif i.rid is not None:
                out.append((self.value(i.rid) if i.op != "OpFunction" else f"FUNCTION({self.type_label(i.rtype)})", i))
            else:
                ops = [self.value(x) for x in i.ids]
                out.append((f"{i.op[2:]}({';'.join(ops)};{';'.join(lit(l) for l in i.lits)})", i))
        # Instructions absorbed after they were emitted (a chain seen before its load) go too.
        out = [(s, i) for s, i in out if i.rid is None or i.rid not in self.absorbed]
        # Each phi's back-edge operands, labelled now that every phi has its label.
        for s, i in list(out):
            if i.op == "OpPhi" and i.rid in self.back_edges:
                out.append((f"PHIBACK({';'.join(self.value(x) for x in self.back_edges[i.rid])})", i))
        return out


def function_key(mod, fid):
    return "entry" if fid == mod.entry_fn else (mod.func_name.get(fid) or f"#{list(mod.functions).index(fid)}")


def block_members(mod, lab, struct):
    """{member index: (Offset or None, type label, name)} of a block struct."""
    offs = {}
    for d in mod.member_decos.get(struct.rid, []):
        if lit(d.lits[1]) == "Offset":
            offs[d.lits[0]] = int(lit(d.lits[2]))
    return {k: (offs.get(k), lab.type_label(t), mod.member_names.get((struct.rid, k), "")) for k, t in enumerate(struct.ids)}


def state_layout(mod, lab, table, failures):
    """Hold every state block to state_layout's table (spv_explained.json): the spec dwords at
    Offset 4N, and every push member at its group's base plus its offset in DXVK's struct, with
    DXVK's type, found by name. The two sides' state reaches the shader differently; where each
    member sits in its block must not, or a value is read from another member's bytes."""
    groups = table["groups"]
    by_name = {m: (g, rel, ty) for g, spec in groups.items() for m, (rel, ty) in spec["members"].items()}
    spec = table["spec"]
    where = f"the {mod.side} module's"
    for vid, role in lab.roles.items():
        if role not in ("push", "spec", "state"):
            continue
        struct = mod.pointee(mod.defs[vid])
        if struct.op != "OpTypeStruct":
            failures.append(f"{where} {role} block is not a struct")
            continue
        if mod.deco(struct.rid, "Block") is None:
            failures.append(f"{where} {role} block is not decorated Block")
        members = block_members(mod, lab, struct)
        if any(off is None for off, _, _ in members.values()):
            failures.append(f"{where} {role} block has a member without an Offset")
            continue
        lab.note("state_layout")
        seen = Counter()
        group_base = {}
        for k, (off, ty, name) in sorted(members.items()):
            if role == "spec" or (role == "state" and k < spec["host2_dwords"]):
                if (off, ty) != (spec["stride"] * k, spec["type"]):
                    failures.append(f"{where} {role} dword {k} is {ty} at Offset {off}, not {spec['type']} at {spec['stride'] * k}")
                continue
            if role == "push" and (name == "sPad" or (register_of(name) is not None and name.startswith("s"))):
                # DXVK's sampler heap indices (sampler_descriptor's) and their padding word
                # (dxvk_shader_ir.cpp:803-822), after its shared data (in the dump's layout; a
                # captured module's sit where its pipeline's push ranges put them)
                if mod.role_names is None and off < table["dxvk_sampler_index_base"]:
                    failures.append(f"{where} sampler index {name} at Offset {off}, inside the shared push data")
                continue
            if name not in by_name:
                failures.append(f"{where} {role} member {k} ({name or 'unnamed'}) is in no push group of the table")
                continue
            g, rel, want_ty = by_name[name]
            seen[name] += 1
            base = groups[g]["dxvk_base" if role == "push" else "host2_base"]
            if role == "push" and mod.role_names is not None:
                # a captured module: the group may start anywhere the pipeline's push ranges put it,
                # but every member of it must sit at the group's one base plus its offset in DXVK's
                # struct (the first member seen fixes the base)
                base = group_base.setdefault(g, off - rel)
            if (off, ty) != (base + rel, want_ty):
                failures.append(f"{where} {role} member {name} is {ty} at Offset {off}, not {want_ty} at {base + rel}")
            if mod.stage not in groups[g]["stages"]:
                failures.append(f"{where} {role} block carries {name} in a {mod.stage} shader")
        if role == "state":
            # Host 2's block is the device's fixed layout: every dword and every member of the
            # stage's groups, each once (DXVK's carries only the members its shader reads).
            want = {m for g, s in groups.items() if mod.stage in s["stages"] for m in s["members"]}
            if set(seen) != want or any(n != 1 for n in seen.values()):
                failures.append(f"{where} state block's push members {sorted(seen.elements())} are not {sorted(want)}, each once")
            if sum(1 for k in members if k < spec["host2_dwords"]) != spec["host2_dwords"]:
                failures.append(f"{where} state block holds fewer than {spec['host2_dwords']} spec dwords")
        elif any(n != 1 for n in seen.values()):
            failures.append(f"{where} {role} block names a member twice")


# What a descriptor, its block, its block's members and its arrays may be decorated with: their
# place and layout only.
LAYOUT_DECORATIONS = {"variable": {"DescriptorSet", "Binding"}, "block": {"Block", "BufferBlock"},
                      "member": {"Offset"}, "array": {"ArrayStride"}}


def descriptor_decorations(mod, lab, failures):
    for vid, role in lab.roles.items():
        where = f"the {mod.side} module's {role} descriptor"
        for d in mod.decos.get(vid, []):
            if lit(d.lits[0]) not in LAYOUT_DECORATIONS["variable"]:
                failures.append(f"{where} is decorated {lit(d.lits[0])}")
        t = mod.pointee(mod.defs[vid])
        types = [t]
        while types:
            ty = types.pop()
            kind = "block" if ty.op == "OpTypeStruct" else "array" if ty.op in ("OpTypeArray", "OpTypeRuntimeArray") else None
            if kind is None:
                continue
            for d in mod.decos.get(ty.rid, []):
                if lit(d.lits[0]) not in LAYOUT_DECORATIONS[kind]:
                    failures.append(f"{where}'s {kind} type is decorated {lit(d.lits[0])}")
            if kind == "block":
                for d in mod.member_decos.get(ty.rid, []):
                    if lit(d.lits[1]) not in LAYOUT_DECORATIONS["member"]:
                        name = mod.member_names.get((ty.rid, d.lits[0]), str(d.lits[0]))
                        failures.append(f"{where}'s member {name} is decorated {lit(d.lits[1])}")
                types.extend(mod.defs[x] for x in ty.ids if x in mod.defs)
            else:
                types.append(mod.defs[ty.ids[0]])


def compare_l1(A, B, la, lb, cfg, failures):
    # Capabilities (heap_capabilities)
    allowed = next(c for c in cfg["classes"] if c["id"] == "heap_capabilities")["pattern"]
    only_a, only_b = A.capabilities - B.capabilities, B.capabilities - A.capabilities
    same_side = A.side == B.side   # two DXVK modules (a captured one against the dump): no freedom here
    for cap in sorted(only_a):
        if cap in allowed["dxvk_only"] and not same_side:
            la.note("heap_capabilities")
        else:
            failures.append(f"capability {cap} only in DXVK's module")
    for cap in sorted(only_b):
        if cap in allowed["host2_only"] and not same_side:
            lb.note("heap_capabilities")
        else:
            failures.append(f"capability {cap} only in host 2's module")
    if sorted(A.extensions) != sorted(B.extensions):
        failures.append(f"extensions differ: {sorted(A.extensions)} / {sorted(B.extensions)}")
    if (A.stage, A.entry.lits[1] if len(A.entry.lits) > 1 else "") != (B.stage, B.entry.lits[1] if len(B.entry.lits) > 1 else ""):
        failures.append("entry points differ")
    ea = sorted(f"{lit(e.lits[0])}:{','.join(lit(x) for x in e.lits[1:])}" for e in A.exec_modes)
    eb = sorted(f"{lit(e.lits[0])}:{','.join(lit(x) for x in e.lits[1:])}" for e in B.exec_modes)
    if ea != eb:
        failures.append(f"execution modes differ: {ea} / {eb}")
    # Descriptor roles: the same set on both sides, except the state carriers.
    state_roles = {"push", "spec", "state", "sampler_heap"}
    ra = Counter(r for r in la.roles.values() if r not in state_roles and not r.startswith("sampler:"))
    rb = Counter(r for r in lb.roles.values() if r not in state_roles and not r.startswith("sampler:"))
    if ra != rb:
        failures.append(f"descriptor roles differ: {dict(ra)} / {dict(rb)}")
    # The dynamic float buffer's length (dynamic_cf_length): DXVK's count + 1, host 2's at most 256.
    def dyn_length(mod, lab):
        for v, role in lab.roles.items():
            if role == "dynamic":
                t = mod.pointee(mod.defs[v])
                if t.op != "OpTypeStruct" or len(t.ids) != 1 or mod.defs[t.ids[0]].op != "OpTypeArray":
                    failures.append(f"the {mod.side} dynamic float buffer is not a block of one array")
                    return None
                return mod.array_length(t.ids[0])
        return None
    na, nb = dyn_length(A, la), dyn_length(B, lb)
    if na != nb:
        if na == 257 and nb == 256 and not same_side:
            la.note("dynamic_cf_length")
            lb.note("dynamic_cf_length")
        else:
            failures.append(f"dynamic float buffer length {na} / {nb}")
    # Interface variables: equal as labelled.
    ia = sorted(la.value(x) for x in A.entry.ids[1:] if A.defs[x].op == "OpVariable" and A.storage(A.defs[x]) in ("Input", "Output"))
    ib = sorted(lb.value(x) for x in B.entry.ids[1:] if B.defs[x].op == "OpVariable" and B.storage(B.defs[x]) in ("Input", "Output"))
    if ia != ib:
        failures.append(f"interface variables differ: {sorted(set(ia) ^ set(ib))[:4]}")
    # Functions: the same keys, the same canonical streams.
    fa = {function_key(A, f): f for f in A.functions}
    fb = {function_key(B, f): f for f in B.functions}
    for k in sorted(set(fa) ^ set(fb)):
        failures.append(f"function {k} only in {'DXVK' if k in fa else 'host 2'}'s module")
    streams = {}
    for k in fa:
        if k not in fb:
            continue
        sa, sb = la.function_stream(fa[k]), lb.function_stream(fb[k])
        ta, tb = [s for s, _ in sa], [s for s, _ in sb]
        if A.linked:
            # DXVK's linked module reads a spec dword where it uses it (an OpSpecConstant); host 2
            # loads it from its state buffer first: the load is not an instruction DXVK has
            tb = [s for s in tb if not s.startswith("STATE[spec:")]
        streams[k] = len(ta)
        if ta != tb:
            sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    continue
                failures.append(f"function {k}: {tag} DXVK[{i1}:{i2}] {ta[i1:i1 + 1]} / host2[{j1}:{j2}] {tb[j1:j1 + 1]}")
                break
    # Decorations of non-descriptors (NoContraction, RelaxedPrecision, Location, BuiltIn, ...).
    def block_types(mod, lab):
        out = set()
        for v in lab.roles:
            t = mod.pointee(mod.defs[v])
            out.add(t.rid)
            out.update(x for x in t.ids if mod.defs[x].op in ("OpTypeArray", "OpTypeRuntimeArray", "OpTypeStruct"))
        return out
    def decos(mod, lab):
        out = Counter()
        skip = block_types(mod, lab)
        for vid, ds in mod.decos.items():
            if vid in lab.roles or vid in lab.absorbed or vid in skip:
                continue
            for d in ds:
                name = lit(d.lits[0])
                if name in ("DescriptorSet", "Binding", "Block", "BufferBlock"):
                    continue
                if name == "SpecId" and mod.side == "dxvk" and mod.linked and mod.defs.get(vid) is not None \
                        and mod.defs[vid].op == "OpSpecConstant":
                    lab.note("spec_data")
                    continue
                target = lab.value(vid) if mod.defs.get(vid) is not None else f"#{vid}"
                out[f"{name}({','.join(lit(x) for x in d.lits[1:])})@{target}"] += 1
        return out
    da, db = decos(A, la), decos(B, lb)
    if da != db:
        failures.append(f"decorations differ: {list((da - db).items())[:3]} / {list((db - da).items())[:3]}")
    # Member decorations: the static, clip-plane and texture-stage blocks must have one layout;
    # the state blocks are held to state_layout's table, on each side.
    def layouts(mod, lab):
        out = {}
        for vid, role in lab.roles.items():
            if role in ("static", "clip_planes", "texture_stages", "dynamic"):
                block = mod.pointee(mod.defs[vid])
                lay = [f"{d.lits[0]}:{','.join(lit(x) for x in d.lits[1:])}" for d in mod.member_decos.get(block.rid, [])]
                lay += [f"block:{lit(d.lits[0])}" for d in mod.decos.get(block.rid, [])]
                for x in block.ids:
                    lay += [f"member{block.ids.index(x)}:{lit(d.lits[0])}:{','.join(lit(y) for y in d.lits[1:])}"
                            for d in mod.decos.get(x, [])]
                out[role] = sorted(lay)
        return out
    if layouts(A, la) != layouts(B, lb):
        failures.append("the constant blocks' member layouts differ")
    # A descriptor's decorations, its block's and its block members' are left out of the comparison
    # above (each side lays its descriptors out its own way), so they are held to what a layout is:
    # anything else on either side (RelaxedPrecision on a state member, an image or a constant
    # buffer, say) changes what the shader reads and fails.
    descriptor_decorations(A, la, failures)
    descriptor_decorations(B, lb, failures)
    table = next(c for c in cfg["classes"] if c["id"] == "state_layout")["layout"]
    state_layout(A, la, table, failures)
    state_layout(B, lb, table, failures)
    failures.extend(la.unexplained)
    failures.extend(lb.unexplained)
    return streams


# --- L2: the arithmetic, R0's two checks with the shared state tokens ---------------------------
FLOAT_ARITH = {
    "OpFAdd", "OpFSub", "OpFMul", "OpFDiv", "OpFNegate", "OpFMod", "OpFRem", "OpDot",
    "OpVectorTimesScalar", "OpMatrixTimesVector", "OpVectorTimesMatrix",
    "OpConvertUToF", "OpConvertSToF", "OpConvertFToU", "OpConvertFToS", "OpFConvert",
    "OpFOrdEqual", "OpFOrdNotEqual", "OpFOrdLessThan", "OpFOrdGreaterThan",
    "OpFOrdLessThanEqual", "OpFOrdGreaterThanEqual", "OpFUnordEqual", "OpFUnordNotEqual",
    "OpFUnordLessThan", "OpFUnordGreaterThan", "OpFUnordLessThanEqual",
    "OpFUnordGreaterThanEqual", "OpIsNan", "OpIsInf",
    "OpImageSampleImplicitLod", "OpImageSampleExplicitLod", "OpImageSampleDrefImplicitLod",
    "OpImageSampleDrefExplicitLod", "OpImageGather", "OpImageFetch", "OpDPdx", "OpDPdy",
}


def is_float(lab, tid):
    s = lab.type_label(tid) if tid else ""
    return "TypeFloat" in s


def float_ops(lab, fid):
    _, _, GLSL = grammar()
    out = []
    for i in lab.m.functions[fid]:
        if i.op == "OpExtInst":
            if i.rtype and is_float(lab, i.rtype):
                out.append("GLSL." + GLSL.get(i.lits[0], str(i.lits[0])))
        elif i.op in FLOAT_ARITH:
            out.append(i.op[2:] + (":precise" if lab.m.deco(i.rid, "NoContraction") else ""))
        elif i.op in ("OpSelect", "OpPhi") and i.rtype and is_float(lab, i.rtype):
            out.append(i.op[2:] + ".f")
        elif i.op == "OpFunctionCall" and i.rtype and is_float(lab, i.rtype):
            out.append("Call." + function_key(lab.m, i.ids[0]))
    return out


def sinks(lab, fid):
    m = lab.m
    out = []
    for i in m.functions[fid]:
        if i.op == "OpStore":
            var, idx = lab.chain(i.ids[0])
            dst = lab.value(var.rid) + "".join(f"[{lab.value(x)}]" for x in idx) if var is not None else "STORE"
            out.append(f"{dst} = {lab.value(i.ids[1])}")
        elif i.op == "OpReturnValue":
            out.append("RET = " + lab.value(i.ids[0]))
        elif i.op == "OpBranchConditional":
            out.append(f"BR({lab.value(i.ids[0])})")
        elif i.op == "OpSwitch":
            out.append(f"SW({lab.value(i.ids[0])};{','.join(lit(x) for x in i.lits)})")
        elif i.op in ("OpDemoteToHelperInvocation", "OpDemoteToHelperInvocationEXT", "OpKill", "OpTerminateInvocation"):
            out.append("DEMOTE")
    return out


def compare(dxvk_path, host2_path, bgra_mask=0, cfg=None, reference_side="dxvk", linked=False, roles=None,
            second_side="host2", second_roles=None):
    """reference_side "host2" compares two host-2 modules (a variant against its base, a fault
    against a clean build): both are labelled as host 2 lays them out."""
    cfg = cfg or json.load(open(EXPLAINED))
    hits = {"dxvk": Counter(), "host2": Counter()}
    failures = []
    la = lb = None
    try:
        A = Module(dxvk_path, reference_side, role_names=roles)
        B = Module(host2_path, second_side, bgra_mask, role_names=second_roles)
        A.linked = linked and reference_side == "dxvk"
        if reference_side == "host2":
            A.side = "host2"
            hits["host2_reference"] = Counter()
        la, lb = Labeller(A, hits), Labeller(B, hits)
        streams = compare_l1(A, B, la, lb, cfg, failures)
        l2 = {}
        fa = {function_key(A, f): f for f in A.functions}
        fb = {function_key(B, f): f for f in B.functions}
        for k in fa:
            if k not in fb:
                continue
            oa, ob = float_ops(la, fa[k]), float_ops(lb, fb[k])
            sa, sb = sinks(la, fa[k]), sinks(lb, fb[k])
            l2[k] = {"float_ops": len(oa), "sinks": len(sa), "float_ops_equal": oa == ob, "sinks_equal": sa == sb}
            if oa != ob:
                failures.append(f"L2 function {k}: the float instruction sequences differ")
            if sa != sb:
                idx = next((n for n, (x, y) in enumerate(zip(sa, sb)) if x != y), min(len(sa), len(sb)))
                failures.append(f"L2 function {k}: sink {idx} differs")
    except SpvError as e:
        failures.append(f"unreadable: {e}")
        streams, l2 = {}, {}
    if any("CYCLE" in str(v) for lab in (la, lb) if lab for v in lab.memo.values()):
        failures.append("a value is labelled through a cycle that no OpPhi breaks")
    known = {c["id"] for c in cfg["classes"]}
    for side in hits:
        for cls in hits[side]:
            if cls not in known:
                failures.append(f"internal: class {cls} is not in spv_explained.json")
    return {"ok": not failures, "failures": failures, "l1_functions": streams, "l2": l2,
            "classes": {s: dict(sorted(c.items())) for s, c in hits.items()}}


REFLECTION_SCHEMA = "sa2-d3d9gpu-shader-reflection/v1"


def spec_id_value(spec_json, spec_id):
    """The value a stage's recorded VkSpecializationInfo gives spec_id (a 4-byte entry), or None."""
    spec = json.loads(spec_json) if spec_json else {}
    for cid, off, size in spec.get("entries") or []:
        if cid == spec_id and size == 4:
            data = bytes.fromhex(spec.get("data") or "")
            return struct.unpack_from("<I", data, off)[0] if off + 4 <= len(data) else -1
    return None


def sampler_modes_of(variant):
    """The sampler-mode word a variant key names (sampler_modes=<word>), or None for the base."""
    m = re.search(r"sampler_modes=([^;]+)", variant or "")
    return None if not m or m.group(1) == "none" else int(m.group(1), 0)


def spv_freeze():
    """test/spv_freeze.py, imported where a specialized pair needs it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import spv_freeze as module
    return module


def bgra_of(variant):
    """The BGRA mask of a variant key ("bgra=N;border_u=...;...", d9g_shader.cpp Variant::key)."""
    for part in str(variant).split(";"):
        if part.startswith("bgra="):
            return int(part[5:], 0)
    raise ValueError(f"variant {variant!r} has no bgra= field")


def batch(dx, h2, expect=None):
    """Every host-2 shader in H2 (a reflection JSON and its module) against the DXVK module its
    debug name names in DX. Nothing is skipped: an unreadable or foreign reflection, a module
    without a reflection, a reflection without a module, a DXVK module no host-2 shader names, an
    empty side, and (with --expect) a shader count other than the expected one each fail the
    batch by name."""
    rows, matched = {}, set()

    def fail(key, why):
        rows[key] = {"ok": False, "failures": [why]}

    dxvk_modules = {p.stem for p in dx.glob("*.spv")} if dx.is_dir() else set()
    reflections = sorted(h2.glob("*.json")) if h2.is_dir() else []
    for refl in reflections:
        try:
            r = json.loads(refl.read_text())
        except (OSError, ValueError) as e:
            fail(refl.stem, f"unreadable reflection {refl.name}: {e}")
            continue
        if not isinstance(r, dict) or r.get("schema") != REFLECTION_SCHEMA:
            fail(refl.stem, f"{refl.name} is not a {REFLECTION_SCHEMA} reflection")
            continue
        name, spv = r.get("debug_name"), refl.with_suffix(".spv")
        if not isinstance(name, str) or not name:
            fail(refl.stem, f"{refl.name} names no debug_name")
            continue
        if not spv.is_file():
            fail(refl.stem, f"{refl.name} has no module {spv.name}")
            continue
        try:
            mask = bgra_of(r.get("variant", ""))
        except ValueError as e:
            fail(refl.stem, str(e))
            continue
        if name not in dxvk_modules:
            fail(refl.stem, f"no DXVK module {name}.spv")
            continue
        matched.add(name)
        rows[refl.stem] = compare(dx / f"{name}.spv", spv, mask)
    for stem in sorted({p.stem for p in h2.glob("*.spv")} - {p.stem for p in reflections} if h2.is_dir() else []):
        fail(stem, f"module {stem}.spv has no reflection")
    for name in sorted(dxvk_modules - matched):
        fail(f"dxvk:{name}", f"DXVK module {name}.spv is named by no host-2 shader")
    shaders = sum(1 for k in rows if not k.startswith("dxvk:"))
    if not dxvk_modules:
        fail("dxvk:", f"no DXVK modules in {dx}")
    if not shaders:
        fail("host2:", f"no host-2 shaders in {h2}")
    if expect is not None and shaders != expect:
        fail("host2:count", f"{shaders} host-2 shaders, expected {expect}")
    ok = all(r["ok"] for r in rows.values())
    totals = Counter()
    for r in rows.values():
        for side in ("dxvk", "host2"):
            totals.update({f"{side}.{k}": v for k, v in r.get("classes", {}).get(side, {}).items()})
    print(json.dumps({"ok": ok, "shaders": shaders, "dxvk_modules": len(dxvk_modules), "dxvk_matched": len(matched),
                      "classes": dict(sorted(totals.items())),
                      "failures": {k: v["failures"] for k, v in rows.items() if not v["ok"]}}, indent=1))
    print("SPV_CANON_OK" if ok else "SPV_CANON_FAIL")
    return 0 if ok else 1


PAIR_SCHEMA = "sa2-d3d9gpu-shader-pair/v1"
VK_PIPELINE_CREATE_LIBRARY_BIT_KHR = 0x800   # in VkPipelineCreateFlags, and in VkPipelineCreateFlags2, where DXVK sets it
VK_FORMAT_B8G8R8A8_UNORM = 44                # D3DDECLTYPE_D3DCOLOR's format (d3d9_util.cpp:262, BindInputLayout)


def vertex_bgra_mask(record):
    """The BGRA location mask of a captured pipeline: bit L where its vertex input's attribute at
    location L is VK_FORMAT_B8G8R8A8_UNORM; None where the record carries no vertex input of its own."""
    vi = record.get("vertex_input")
    if not isinstance(vi, dict):
        return None
    mask = 0
    for location, _binding, fmt, _offset in vi.get("attributes", []):
        if fmt == VK_FORMAT_B8G8R8A8_UNORM:
            mask |= 1 << location
    return mask


def capture_records(cap):
    """The capture layer's pipeline records (pipelines.jsonl), each line one JSON object."""
    out = []
    path = cap / "pipelines.jsonl"
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError as e:
                raise SpvError(f"{path}:{n}: {e}")
    return out


D3D9_DEBUG_NAME = re.compile(r"^(vs|fs)\.[0-9a-f]{32}$")


def dump_roles(path):
    """{uniform variable name: role} of DXVK's dump of one shader, whose descriptors sit in DXVK's
    default layout (DXVK_CBV's sets and bindings): the names a captured module of the same shader
    carries, read as the dump places them."""
    m = Module(path, "dxvk")
    lab = Labeller(m, {"dxvk": Counter(), "host2": Counter()})
    return {m.names.get(v, ""): r for v, r in lab.roles.items() if m.storage(m.defs[v]) == "Uniform"}


def linked_batch(cap, pairs_dir, gpl_dump, expect_drawn=None):
    """C3's linked half: every linked pipeline DXVK created for a D3D9 shader pair (no library
    flag in either flags word, no libraries, no graphics-pipeline-library stage flags, VK_SUCCESS, a
    vertex and a fragment stage whose modules carry D3D9 debug names, its own vertex input; DXVK's
    own presenter and HUD pipelines, and the stageless parts of its pipeline libraries, are counted
    and left) against the host-2 pair its two debug names and its BGRA mask name, in --linked mode,
    roles read by name from DXVK's dump of the same shader; every drawn host-2 pair (names and
    mask) must have one; a pipeline of D3D9 shaders that is neither linked nor a library fails; and
    the pipeline-library modules must be exactly the dump's shaders, each equal to DXVK's dump of it
    but for where the pipeline's layout puts its descriptors and push members."""
    rows, failures = {}, {}

    def fail(key, why):
        failures.setdefault(key, []).append(why)

    try:
        records = capture_records(cap)
    except (OSError, SpvError) as e:
        records = []
        fail("capture", f"unreadable capture: {e}")
    pairs = {}
    for pj in sorted(pairs_dir.glob("*.json")) if pairs_dir.is_dir() else []:
        try:
            p = json.loads(pj.read_text())
        except (OSError, ValueError) as e:
            fail(pj.stem, f"unreadable pair {pj.name}: {e}")
            continue
        if not isinstance(p, dict) or p.get("schema") != PAIR_SCHEMA:
            fail(pj.stem, f"{pj.name} is not a {PAIR_SCHEMA} pair")
            continue
        try:
            mask = bgra_of(p["vs"].get("variant", "bgra=0"))
        except ValueError as e:
            fail(pj.stem, str(e))
            continue
        pairs.setdefault((p["vs"]["debug_name"], p["ps"]["debug_name"], mask), []).append(p)
    if not pairs:
        fail("host2:", f"no host-2 pairs in {pairs_dir}")
    modules, roles = {}, {}

    def module(sha):
        if sha not in modules:
            modules[sha] = Module(cap / f"module-{sha}.spv", "dxvk")
        return modules[sha]

    def roles_of(debug):
        if debug not in roles:
            roles[debug] = dump_roles(gpl_dump / f"{debug}.spv")
        return roles[debug]

    linked, library, internal, stageless = [], set(), 0, 0
    for r in records:
        if r.get("result") != 0:
            continue
        stages = {s["stage"]: s["module"] for s in r.get("stages", [])}
        if not stages:
            stageless += 1   # a library part without shaders (vertex input, output) or a fast link
            continue
        try:
            names = {st: module(sha).debug_name for st, sha in stages.items()}
        except (OSError, SpvError) as e:
            fail(f"capture:{r.get('seq')}", f"unreadable captured module: {e}")
            continue
        if not all(n and D3D9_DEBUG_NAME.match(n) for n in names.values()):
            internal += 1
            continue
        is_library = bool((r.get("flags", 0) | r.get("flags2", 0)) & VK_PIPELINE_CREATE_LIBRARY_BIT_KHR)
        if r.get("libraries") == 0 and r.get("gpl_flags") == 0 and not is_library and set(stages) == {"vertex", "fragment"}:
            mask = vertex_bgra_mask(r)
            if mask is None:
                fail(f"capture:{r.get('seq')}", "a linked pipeline whose record carries no vertex input")
                continue
            frag = next((s.get("spec") for s in r.get("stages", []) if s["stage"] == "fragment"), None) or {}
            linked.append((stages, mask, json.dumps(frag, sort_keys=True)))
        elif r.get("gpl_flags") and is_library:
            library.update(stages.values())
        else:
            fail(f"capture:{r.get('seq')}", f"a pipeline of D3D9 shaders ({', '.join(sorted(names.values()))}) that is "
                                            f"neither linked nor a pipeline library (flags {r.get('flags')}, flags2 "
                                            f"{r.get('flags2')}, gpl_flags {r.get('gpl_flags')}, libraries {r.get('libraries')})")
    # one comparison per pair of modules, BGRA mask and sampler-mode word DXVK's pipeline passes (SpecId 5's
    # map entry, None where it passes none): the other spec data (alpha test, sampler types) does not
    # change what the comparison reads
    seen, frozen = set(), {}
    modes = {"specialized_ps": 0, "base_ps": 0, "frozen": 0, "dxvk_words": set(), "word_from": set(),
             "word_sources": set(), "not_frozen": set()}
    scratch = tempfile.TemporaryDirectory()
    for st, dx_mask, frag_spec in linked:
        vs_sha, ps_sha = st["vertex"], st["fragment"]
        key4 = (vs_sha, ps_sha, dx_mask, spec_id_value(frag_spec, 5))
        if key4 in seen:
            continue
        seen.add(key4)
        key = (module(vs_sha).debug_name, module(ps_sha).debug_name, dx_mask)
        if key not in pairs:
            fail(f"dxvk:{key[0]}+{key[1]}.bgra{dx_mask}", "DXVK's linked pipeline of this pair and BGRA mask has no host-2 pair")
            continue
        for p in pairs[key]:
            p["captured"] = True
            for stage, sha, debug in (("vs", vs_sha, key[0]), ("ps", ps_sha, key[1])):
                host2 = pairs_dir / p[stage]["module"]
                mask = dx_mask if stage == "vs" else 0
                dxvk_module = cap / f"module-{sha}.spv"
                word = sampler_modes_of(p[stage].get("variant")) if stage == "ps" else None
                if stage == "ps":
                    modes["specialized_ps" if word is not None else "base_ps"] += 1
                if word is not None:
                    # DXVK's module frozen at its pipeline's word (spv_freeze.py), which must be host 2's
                    spec = json.loads(frag_spec)
                    out = Path(scratch.name) / f"{sha}.{key4[3]}.spv"
                    try:
                        if (sha, key4[3]) not in frozen:
                            frozen[(sha, key4[3])] = spv_freeze().freeze(dxvk_module, out, spec.get("entries"), spec.get("data"))
                        info = frozen[(sha, key4[3])]
                    except Exception as e:   # a refusal of the freeze, or of a tool not at its pin
                        fail(f"{p['name']}.ps", f"DXVK's module could not be frozen at its word: {e}")
                        continue
                    dxvk_word = info["word"] if info["frozen"] else 0
                    modes["word_from"].add(info.get("word_from", info.get("why")))
                    modes["word_sources"].add(info["word_source"])
                    modes["dxvk_words"].add(dxvk_word)
                    modes["frozen"] += int(info["frozen"])
                    if not info["frozen"]:
                        modes["not_frozen"].add(f"{p['name']}.ps ({debug})")
                    if dxvk_word != word:
                        fail(f"{p['name']}.ps", f"host 2's pixel shader is specialized at {word:#x}, DXVK's pipeline "
                                                f"gives psSamplerModes {dxvk_word:#x}")
                        continue
                    dxvk_module = out if info["frozen"] else dxvk_module
                try:
                    r = compare(dxvk_module, host2, mask, linked=True, roles=roles_of(debug))
                except (OSError, SpvError) as e:
                    r = {"ok": False, "failures": [f"no dump of {debug}: {e}"], "classes": {}}
                rows[f"{p['name']}.{stage}"] = r
                if not r["ok"]:
                    fail(f"{p['name']}.{stage}", "; ".join(r["failures"][:3]))
    drawn = [p for ps in pairs.values() for p in ps if p.get("drawn")]
    for p in drawn:
        if not p.get("captured"):
            fail(p["name"], "a drawn pair with no linked pipeline of DXVK's (its two shaders and BGRA mask) in the capture")
    drawn_pairs = {(p["vs"]["name"], p["ps"]["name"]) for p in drawn}
    if expect_drawn is not None and len(drawn_pairs) != expect_drawn:
        fail("host2:drawn", f"{len(drawn_pairs)} drawn host-2 shader pairs, expected {expect_drawn}")
    if not linked:
        fail("capture", "the capture holds no linked pipeline of a D3D9 shader pair")
    gpl = {"library_modules": len(library), "equal_to_dump": 0}
    for sha in sorted(library):
        debug = module(sha).debug_name
        try:
            r = compare(cap / f"module-{sha}.spv", gpl_dump / f"{debug}.spv", roles=roles_of(debug), second_side="dxvk")
        except (OSError, SpvError) as e:
            r = {"ok": False, "failures": [f"no dump of {debug}: {e}"]}
        if r["ok"]:
            gpl["equal_to_dump"] += 1
        else:
            fail(f"gpl:{debug}", "a pipeline-library module that is not DXVK's dump: " + "; ".join(r["failures"][:2]))
    if not library:
        fail("gpl", "the capture holds no pipeline-library module to hold to the dump")
    # The library modules are the dump's shaders, every one: a library part the recorder lost (its
    # flags dropped, say) leaves a dumped shader no library module covers.
    lib_names = {module(sha).debug_name for sha in library}
    dump_names = {q.stem for q in gpl_dump.glob("*.spv")} if gpl_dump.is_dir() else set()
    for n in sorted(dump_names - lib_names):
        fail(f"gpl:{n}", "a shader DXVK dumped that no pipeline-library module of the capture carries")
    for n in sorted(lib_names - dump_names):
        fail(f"gpl:{n}", "a pipeline-library module whose shader DXVK did not dump")
    totals = Counter()
    for r in rows.values():
        for side in ("dxvk", "host2"):
            totals.update({f"{side}.{k}": v for k, v in r.get("classes", {}).get(side, {}).items()})
    ok = not failures
    scratch.cleanup()
    modes = {k: sorted(v) if isinstance(v, set) else v for k, v in modes.items()}
    print(json.dumps({"ok": ok, "linked_pipelines": len(linked), "linked_module_pairs": len({s[:2] for s in seen}),
                      "linked_keys": len({s[:3] for s in seen}), "linked_bgra_masks": sorted({s[2] for s in seen}),
                      "sampler_modes": modes,
                      "dxvk_internal_pipelines": internal, "stageless_pipelines": stageless,
                      "compared": len(rows), "compared_keys": sorted(rows), "drawn_pair_entries": len(drawn),
                      "drawn_shader_pairs": len(drawn_pairs), "gpl": gpl, "classes": dict(sorted(totals.items())),
                      "failures": failures}, indent=1))
    print("SPV_CANON_LINKED_OK" if ok else "SPV_CANON_FAIL")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dxvk", nargs="?")
    ap.add_argument("host2", nargs="?")
    ap.add_argument("--bgra", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--batch", nargs=2, metavar=("DXVK_DIR", "HOST2_DIR"))
    ap.add_argument("--linked-batch", nargs=2, metavar=("CAPTURE_DIR", "PAIRS_DIR"))
    ap.add_argument("--gpl-dump", help="--linked-batch: DXVK_SHADER_DUMP_PATH's directory")
    ap.add_argument("--expect-drawn", type=int, help="--linked-batch: the number of drawn host-2 pairs there must be")
    ap.add_argument("--linked", action="store_true", help="DXVK.spv is DXVK's linked variant (OpSpecConstant spec data)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--expect", type=int, help="--batch: the number of host-2 shaders there must be")
    ap.add_argument("--reference-side", choices=("dxvk", "host2"), default="dxvk",
                    help="host2: the first module is host 2's too (a variant's base, a clean build)")
    args = ap.parse_args(argv)
    if args.batch:
        return batch(Path(args.batch[0]), Path(args.batch[1]), args.expect)
    if args.linked_batch:
        if not args.gpl_dump:
            ap.error("--linked-batch needs --gpl-dump: a captured module's roles are read from DXVK's dump")
        return linked_batch(Path(args.linked_batch[0]), Path(args.linked_batch[1]), Path(args.gpl_dump), args.expect_drawn)
    if not (args.dxvk and args.host2):
        ap.error("two modules, or --batch")
    r = compare(args.dxvk, args.host2, args.bgra, reference_side=args.reference_side, linked=args.linked)
    if args.json:
        print(json.dumps(r, indent=1))
    else:
        for f in r["failures"]:
            print("  " + f)
        print(json.dumps(r["classes"]))
    print("SPV_CANON_OK" if r["ok"] else "SPV_CANON_FAIL")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
