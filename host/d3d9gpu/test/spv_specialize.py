#!/usr/bin/env python3
"""Hold a sampler-mode-specialized pixel shader to its base: a pure deletion under the word.

Subtitle: the specialization check (I7b). Host 2 bakes DXVK's psSamplerModes word into a pixel
shader (host/d3d9gpu/d9g_shader.h Variant::sampler_modes): each samplerMode(r) call becomes
(word >> 2r) & 3 and dxbc-spirv's legalisation removes what that makes dead. This tool decides, from
the two modules alone and by its own reading of SPIR-V (no dxbc-spirv, no SPIRV-Tools), that the
specialized module SPEC is its base BASE (the same shader, the same variant, the word off) with
deletions only, every deletion one the word licenses:

  * the word: BASE's state buffer (the uniform block at DescriptorSet 3, Binding 1, host 2's pixel
    state slot) member 5 at byte 20 is psSamplerModes; a function whose one block computes its return
    value from its parameters, constants and that member only is evaluated, per call with constant
    arguments, at the word: those calls are the word's values. Compares, logical operators, integer
    arithmetic, selects and phis over them fold; a conditional branch or switch on a folded value keeps
    one edge. The blocks reachable from each function's entry through the edges that remain are live;
    the rest are dead under the word;
  * deletion only: every instruction of SPEC is an instruction of BASE (the same opcode and literals,
    every id operand the same value modulo ids, a folded value or select or phi read as what it
    folded to), in BASE's order; SPEC's blocks are BASE's live blocks, a run of them merged where each
    has one live successor and it one live predecessor, and nothing BASE does that has an effect (a
    store, a call the word does not decide, a demote, a kill, a return, an image write, a barrier) is
    missing from a live block; SPEC's OpBranch stands where BASE's branch folded, its OpPhi keeps the
    live incoming pairs;
  * the removed blocks are exactly the dead ones: every block of BASE that SPEC lacks is dead, or
    merged into a live run, and SPEC has no dead block (the word left nothing it makes dead);
  * SPEC reads the word nowhere, and its resource interface (every descriptor's set and binding) is
    BASE's, but for a descriptor BASE reads only in blocks the word makes dead (a border variant's colour
    buffer where every border register's colour sample is dead), which SPEC may drop with them;
  * no decoration of a kept value is lost: every decoration BASE puts on ids SPEC kept is SPEC's too
    (NoContraction, Centroid, Flat and the rest change what a kept value computes or reads), and SPEC
    has none BASE lacks; a structured loop's OpLoopMerge goes only with the loop (its continue target
    dead under the word).

What a pure deletion cannot show (the removed paths of BASE themselves, DXVK's code) is C3's GPL
half, on the base; what the module draws is the frames' check (specialized against base, bit-equal).

  spv_specialize.py BASE.spv SPEC.spv --word W [--json]
  spv_specialize.py --batch BASE_DIR SPEC_DIR [--expect N] [--json]

The batch pairs each SPEC_DIR/<name>.spv whose reflection (<name>.json, d3d9gpu_shaderc's) names a
specialized variant (sampler_modes=<word>) with BASE_DIR/<name>.spv, whose reflection must name the
same shader, the same variant but for the word off, and the same profile and translator; anything it
cannot pair fails, and --expect N fails a count other than N.

Exit status 0 and SPV_SPECIALIZE_OK only when every module holds; 1 and SPV_SPECIALIZE_FAIL otherwise.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import spv_canon  # noqa: E402  the comparer's SPIR-V reader (the grammar-driven parse only)

STATE_SET, STATE_BINDING = 3, 1          # d9g_shader.h: fragment uniforms in set 3, kPsState = 1
WORD_MEMBER, WORD_OFFSET = 5, 20         # D3D9SpecData spec id 5, psSamplerModes, byte 20
TERMINATORS = {"OpBranch", "OpBranchConditional", "OpSwitch", "OpReturn", "OpReturnValue", "OpKill",
               "OpUnreachable", "OpTerminateInvocation"}
# what has an effect beyond its own value: never a deletion from a live block
EFFECTS = TERMINATORS | {"OpStore", "OpImageWrite", "OpDemoteToHelperInvocation", "OpControlBarrier",
                         "OpMemoryBarrier", "OpEmitVertex", "OpEndPrimitive", "OpCopyMemory", "OpFunctionCall",
                         "OpBeginInvocationInterlockEXT", "OpEndInvocationInterlockEXT"}
MERGES = {"OpSelectionMerge", "OpLoopMerge"}
U32 = 0xFFFFFFFF


class Fail(Exception):
    pass


def lit(l):
    return spv_canon.lit(l)


def const_value(m, cid):
    """The value of a scalar integer or boolean constant of the module, or None."""
    i = m.defs.get(cid)
    if i is None:
        return None
    if i.op == "OpConstantTrue":
        return True
    if i.op == "OpConstantFalse":
        return False
    if i.op == "OpConstant" and i.lits and isinstance(i.lits[0], tuple):
        t = m.defs.get(i.rtype)
        if t is not None and t.op == "OpTypeInt" and len(i.lits[0]) == 1:
            return i.lits[0][0]
    return None


class Mod:
    """A module read for this check: its instructions, the definitions, and per function its blocks."""

    def __init__(self, path, data=None):
        self.path = str(path)
        self.insts = spv_canon.parse_words(Path(path).read_bytes() if data is None else data, str(path))
        self.defs = {i.rid: i for i in self.insts if i.rid is not None}
        self.decos = {}
        for i in self.insts:
            if i.op in ("OpDecorate", "OpMemberDecorate"):
                self.decos.setdefault(i.ids[0], []).append(i)
        first_fn = next((k for k, i in enumerate(self.insts) if i.op == "OpFunction"), len(self.insts))
        self.globals = self.insts[:first_fn]
        self.functions = []   # [(OpFunction, [params], [blocks])], a block [label, instrs..., terminator]
        k = first_fn
        while k < len(self.insts):
            fn = self.insts[k]
            if fn.op != "OpFunction":
                raise Fail(f"{path}: {fn.op} between functions")
            k += 1
            params, blocks = [], []
            while self.insts[k].op == "OpFunctionParameter":
                params.append(self.insts[k])
                k += 1
            while self.insts[k].op != "OpFunctionEnd":
                if self.insts[k].op != "OpLabel":
                    raise Fail(f"{path}: a block that does not start with OpLabel")
                block = [self.insts[k]]
                k += 1
                while block[-1].op not in TERMINATORS:
                    block.append(self.insts[k])
                    k += 1
                blocks.append(block)
            self.functions.append((fn, params, blocks))
            k += 1

    def deco(self, vid, name, member=None):
        for d in self.decos.get(vid, []):
            if d.op == "OpDecorate" and member is None and lit(d.lits[0]) == name:
                return [x for x in d.lits[1:]] or [True]
            if d.op == "OpMemberDecorate" and member is not None and d.lits[0] == member and lit(d.lits[1]) == name:
                return [x for x in d.lits[2:]] or [True]
        return None

    def bindings(self):
        """{(set, binding): storage class} of every descriptor variable."""
        out = {}
        for i in self.globals:
            if i.op == "OpVariable":
                s, b = self.deco(i.rid, "DescriptorSet"), self.deco(i.rid, "Binding")
                if s and b:
                    out.setdefault((s[0], b[0]), []).append(lit(i.lits[0]))
        return {k: sorted(v) for k, v in out.items()}

    def state_variable(self):
        for i in self.globals:
            if i.op == "OpVariable" and lit(i.lits[0]) == "Uniform" and self.deco(i.rid, "DescriptorSet") == [STATE_SET] \
                    and self.deco(i.rid, "Binding") == [STATE_BINDING]:
                ptr = self.defs[i.rtype]
                block = ptr.ids[0]
                off = self.deco(block, "Offset", WORD_MEMBER)
                if off != [WORD_OFFSET]:
                    raise Fail(f"{self.path}: the state block's member {WORD_MEMBER} is at {off}, not byte {WORD_OFFSET}")
                return i.rid
        return None


class Eval:
    """BASE under the word: the values the word decides, what each select or phi folds to, and the
    live blocks and edges of every function."""

    def __init__(self, m, word):
        self.m, self.word = m, word
        self.state = m.state_variable()
        self.fn_by_id = {fn.rid: (fn, params, blocks) for fn, params, blocks in m.functions}
        self.fn_cache = {}
        self.value = {}      # id -> int or bool, decided by the word
        self.alias = {}      # id -> the id it folds to
        self.live, self.succ, self.pred, self.dead = {}, {}, {}, {}
        self.word_reads = []  # (function, id) of each read of the word
        for fn, params, blocks in m.functions:
            self.run(fn, params, blocks)

    def resolve(self, vid):
        seen = 0
        while vid in self.alias and seen < 64:
            vid, seen = self.alias[vid], seen + 1
        return vid

    def val(self, vid):
        vid = self.resolve(vid)
        if vid in self.value:
            return self.value[vid]
        return const_value(self.m, vid)

    def word_ptr(self, i):
        return (i.op in ("OpAccessChain", "OpInBoundsAccessChain") and self.state is not None and i.ids[0] == self.state
                and len(i.ids) == 2 and const_value(self.m, i.ids[1]) == WORD_MEMBER)

    def pure_call(self, fid, args):
        """The value a call returns when its function's one block computes it from its parameters,
        constants and the word only, else None."""
        key = (fid, tuple(args))
        if key in self.fn_cache:
            return self.fn_cache[key]
        self.fn_cache[key] = None
        fn, params, blocks = self.fn_by_id.get(fid, (None, [], []))
        if fn is None or len(blocks) != 1 or len(params) != len(args):
            return None
        env = {p.rid: a for p, a in zip(params, args)}
        ptrs, reads_word = set(), False

        def v(x):
            return env[x] if x in env else const_value(self.m, x)
        for i in blocks[0][1:]:
            if self.word_ptr(i):
                ptrs.add(i.rid)
                continue
            if i.op == "OpLoad" and i.ids[0] in ptrs:
                env[i.rid] = self.word
                reads_word = True
                continue
            if i.op == "OpReturnValue":
                r = v(i.ids[0])
                out = r if reads_word and r is not None else None
                self.fn_cache[key] = out
                return out
            r = fold(i.op, [v(x) for x in i.ids], i.lits)
            if r is None:
                return None
            env[i.rid] = r
        return None

    def run(self, fn, params, blocks):
        labels = {b[0].rid: b for b in blocks}
        for b in blocks:
            for i in b[1:]:
                if self.word_ptr(i):
                    for j in b[1:]:
                        if j.op == "OpLoad" and j.ids[0] == i.rid:
                            self.word_reads.append((fn.rid, j.rid))
        changed = True
        while changed:
            changed = False
            # live edges from the entry
            succ = {}
            for b in blocks:
                t = b[-1]
                if t.op == "OpBranch":
                    succ[b[0].rid] = [t.ids[0]]
                elif t.op == "OpBranchConditional":
                    c = self.val(t.ids[0])
                    succ[b[0].rid] = [t.ids[1] if c else t.ids[2]] if isinstance(c, bool) else [t.ids[1], t.ids[2]]
                elif t.op == "OpSwitch":
                    sel = self.val(t.ids[0])
                    targets = list(zip([x for x in t.lits], t.ids[2:]))
                    if isinstance(sel, int) and not isinstance(sel, bool):
                        hit = [tg for v, tg in targets if (v[0] if isinstance(v, tuple) else v) == sel]
                        succ[b[0].rid] = [hit[0] if hit else t.ids[1]]
                    else:
                        succ[b[0].rid] = [t.ids[1]] + [tg for _, tg in targets]
                else:
                    succ[b[0].rid] = []
            # a merge or continue block a live block declares for a branch the word leaves is live
            # even where no edge reaches it (an OpUnreachable merge after a switch whose every case
            # returns): structured control flow keeps it, in BASE and SPEC alike
            structural = {}
            for b in blocks:
                t = b[-1]
                folded = len(succ[b[0].rid]) == 1 and t.op in ("OpBranchConditional", "OpSwitch")
                if len(b) >= 2 and b[-2].op in MERGES and not folded:
                    structural[b[0].rid] = list(b[-2].ids)
            live, todo = set(), [blocks[0][0].rid]
            while todo:
                x = todo.pop()
                if x in live:
                    continue
                live.add(x)
                todo.extend(succ[x])
                todo.extend(structural.get(x, []))
            pred = {x: [] for x in labels}
            for x in live:
                for y in succ[x]:
                    if x not in pred[y]:
                        pred[y].append(x)
            for b in blocks:
                if b[0].rid not in live:
                    continue
                for i in b[1:]:
                    if i.rid is None or i.rid in self.value or i.rid in self.alias:
                        continue
                    r = self.fold_inst(i, b[0].rid, live, pred)
                    if r is not None:
                        kind, x = r
                        (self.value if kind == "value" else self.alias)[i.rid] = x
                        changed = True
            self.live[fn.rid], self.succ[fn.rid], self.pred[fn.rid] = live, succ, pred
        self.dead[fn.rid] = [b[0].rid for b in blocks if b[0].rid not in self.live[fn.rid]]

    def fold_inst(self, i, label, live, pred):
        """("value", v), ("alias", id) or None for one instruction of a live block."""
        if i.op == "OpFunctionCall":
            args = [self.val(a) for a in i.ids[1:]]
            if all(isinstance(a, int) and not isinstance(a, bool) for a in args):
                r = self.pure_call(i.ids[0], args)
                if r is not None:
                    return ("value", r)
            return None
        if i.op == "OpSelect":
            c = self.val(i.ids[0])
            if isinstance(c, bool):
                return ("alias", self.resolve(i.ids[1] if c else i.ids[2]))
            return None
        if i.op == "OpPhi":
            pairs = [(self.resolve(i.ids[k]), i.ids[k + 1]) for k in range(0, len(i.ids), 2)]
            incoming = {v for v, p in pairs if p in live and p in pred.get(label, [])}
            if len(incoming) == 1:
                return ("alias", incoming.pop())
            return None
        if i.op in ("OpLogicalAnd", "OpLogicalOr"):
            a, b = self.val(i.ids[0]), self.val(i.ids[1])
            absorb = i.op == "OpLogicalOr"   # true absorbs an or, false an and
            for x, other in ((a, i.ids[1]), (b, i.ids[0])):
                if isinstance(x, bool) and self.tainted(i):
                    return ("value", absorb) if x == absorb else ("alias", self.resolve(other))
            return None
        if not self.tainted(i):
            return None
        r = fold(i.op, [self.val(x) for x in i.ids], i.lits)
        return None if r is None else ("value", r)

    def tainted(self, i):
        """An operand the word decides (a folded value), so a fold here is the word's."""
        return any(self.resolve(x) in self.value for x in i.ids)


def fold(op, a, lits):
    """The value of an integer or boolean op over known operands, else None."""
    if any(x is None for x in a):
        return None
    ints = [x for x in a if not isinstance(x, bool)]
    try:
        if op == "OpIAdd": return (a[0] + a[1]) & U32
        if op == "OpISub": return (a[0] - a[1]) & U32
        if op == "OpIMul": return (a[0] * a[1]) & U32
        if op == "OpShiftRightLogical": return (a[0] >> (a[1] & 31)) & U32
        if op == "OpShiftLeftLogical": return (a[0] << (a[1] & 31)) & U32
        if op == "OpBitwiseAnd": return a[0] & a[1]
        if op == "OpBitwiseOr": return a[0] | a[1]
        if op == "OpBitwiseXor": return a[0] ^ a[1]
        if op == "OpBitFieldUExtract":
            off, n = a[1], a[2]
            return (a[0] >> off) & ((1 << n) - 1) if 0 <= off < 32 and 0 < n <= 32 - off else None
        if op == "OpIEqual": return a[0] == a[1]
        if op == "OpINotEqual": return a[0] != a[1]
        if op == "OpUGreaterThan": return a[0] > a[1]
        if op == "OpUGreaterThanEqual": return a[0] >= a[1]
        if op == "OpULessThan": return a[0] < a[1]
        if op == "OpULessThanEqual": return a[0] <= a[1]
        if op == "OpLogicalNot": return not a[0]
        if op == "OpLogicalEqual": return a[0] == a[1]
        if op == "OpLogicalNotEqual": return a[0] != a[1]
        if op == "OpLogicalAnd": return a[0] and a[1]
        if op == "OpLogicalOr": return a[0] or a[1]
        if op in ("OpCopyObject", "OpBitcast") and ints: return a[0]
    except (TypeError, IndexError):
        return None
    return None


class Matcher:
    """The id map from SPEC to BASE, built as instructions are matched."""

    def __init__(self, ev, s):
        self.ev, self.s, self.b = ev, s, ev.m
        self.fwd, self.back = {}, {}

    def unify(self, sid, bid, trial):
        bid = self.ev.resolve(bid)
        if bid in self.ev.value:
            return False   # a value the word folded is a constant in SPEC, never an id of BASE
        have = trial.get(sid, self.fwd.get(sid))
        if have is not None:
            return have == bid
        if bid in self.back or bid in trial.values():
            return False
        trial[sid] = bid
        return True

    def commit(self, trial):
        for k, v in trial.items():
            self.fwd[k] = v
            self.back[v] = k

    def try_match(self, si, bi, block_map=None):
        """The bindings that make SPEC's si BASE's bi, or None."""
        if si.op != bi.op or si.lits != bi.lits or (si.rtype is None) != (bi.rtype is None):
            return None
        trial = {}
        if si.rtype is not None and not self.unify(si.rtype, bi.rtype, trial):
            return None
        if si.rid is not None and not self.unify(si.rid, bi.rid, trial):
            return None
        if si.op == "OpPhi":
            return self.match_phi(si, bi, trial, block_map)
        if len(si.ids) != len(bi.ids):
            return None
        for x, y in zip(si.ids, bi.ids):
            if not self.unify_operand(x, y, trial):
                return None
        return trial

    def unify_operand(self, x, y, trial):
        """An operand of SPEC against BASE's: the same value, or the constant the word folded BASE's to."""
        v = self.ev.val(y)
        if self.ev.resolve(y) in self.ev.value:
            # a constant SPEC uses where BASE has a value the word folded: the same value, and a boolean
            # for a boolean (True is not the integer 1); its id binds to nothing of BASE's
            cv = const_value(self.s, x)
            return cv is not None and isinstance(cv, bool) == isinstance(v, bool) and cv == v
        return self.unify(x, y, trial)

    def match_phi(self, si, bi, trial, block_map):
        live, pred = block_map["live"], block_map["pred"]
        label = block_map["label"]
        want = [(bi.ids[k], bi.ids[k + 1]) for k in range(0, len(bi.ids), 2)
                if bi.ids[k + 1] in live and bi.ids[k + 1] in pred.get(label, [])]
        got = [(si.ids[k], si.ids[k + 1]) for k in range(0, len(si.ids), 2)]
        if len(want) != len(got):
            return None
        for (sv, sp), (bv, bp) in zip(got, want):
            if not self.unify_operand(sv, bv, trial):
                return None
            # SPEC's parent is the head of the run BASE's parent ends
            head = block_map["head_of"].get(bp)
            if head is None or not self.unify(sp, head, trial):
                return None
        return trial


def check(base_path, spec_path, word):
    """{ok, problems, ...} for one base and its specialization at word."""
    b, s = Mod(base_path), Mod(spec_path)
    ev = Eval(b, word)
    problems = []
    if ev.state is None:
        problems.append("BASE has no state block (set 3, binding 1)")
    sev = Eval(s, word)
    if sev.word_reads:
        problems.append(f"SPEC still reads the word ({len(sev.word_reads)} loads of state member {WORD_MEMBER})")
    bb, sb = b.bindings(), s.bindings()
    dropped = {k for k in bb if k not in sb and dead_only(ev, b, k)}
    if {k: v for k, v in bb.items() if k not in dropped} != sb:
        problems.append(f"the resource interface differs: BASE {bb}, SPEC {sb}")
    mt = Matcher(ev, s)
    # globals: SPEC's in BASE's order; BASE's entry point and execution modes kept
    removed_globals = match_globals(mt, b, s, problems)
    stats = {"functions_removed": 0, "blocks_dead": 0, "blocks_merged": 0, "instructions_removed": 0,
             "word_calls_folded": sum(1 for k in ev.value if b.defs.get(k) is not None and b.defs[k].op == "OpFunctionCall"),
             "globals_removed": removed_globals}
    bfns = list(b.functions)
    k = 0
    for sfn, sparams, sblocks in s.functions:
        # SPEC's functions are BASE's in order, some removed
        while k < len(bfns):
            bfn, bparams, bblocks = bfns[k]
            trial = mt.try_match(sfn, bfn)
            if trial is not None and len(sparams) == len(bparams) and names_of(s).get(sfn.rid) == names_of(b).get(bfn.rid):
                break
            if bfn.rid in mt.back:
                problems.append(f"SPEC's function %{sfn.rid} is not BASE's next one")
                return result(problems, stats)
            stats["functions_removed"] += 1
            k += 1
        else:
            problems.append(f"SPEC's function %{sfn.rid} matches no function of BASE")
            return result(problems, stats)
        mt.commit(trial)
        for sp, bp in zip(sparams, bparams):
            t = mt.try_match(sp, bp)
            if t is None:
                problems.append(f"function %{sfn.rid}: a parameter differs")
                return result(problems, stats)
            mt.commit(t)
        match_function(mt, ev, bfn, bblocks, sblocks, problems, stats)
        k += 1
        if problems:
            return result(problems, stats)
    stats["functions_removed"] += len(bfns) - k
    match_annotations(mt, b, s, problems)
    return result(problems, stats)


def dead_only(ev, m, binding):
    """True when every variable of BASE at (set, binding) is used only in blocks the word makes dead."""
    ids = {i.rid for i in m.globals if i.op == "OpVariable" and (lambda s_, b_: s_ and b_ and (s_[0], b_[0]) == binding)(
        m.deco(i.rid, "DescriptorSet"), m.deco(i.rid, "Binding"))}
    for fn, params, blocks in m.functions:
        live = ev.live[fn.rid]
        for blk in blocks:
            if blk[0].rid in live and any(x in ids for i in blk for x in i.ids):
                return False
    return bool(ids)


def result(problems, stats):
    return {"ok": not problems, "problems": problems, **stats}


# what refers to ids rather than defines them: matched as sets once every definition is matched,
# so a decoration or name of a removed value never binds an id
DEFERRED = {"OpName", "OpMemberName", "OpDecorate", "OpMemberDecorate", "OpDecorateString", "OpMemberDecorateString",
            "OpDecorateId", "OpEntryPoint", "OpExecutionMode", "OpExecutionModeId", "OpSource", "OpSourceContinued",
            "OpSourceExtension", "OpString", "OpModuleProcessed", "OpLine", "OpNoLine"}
DECORATIONS = {"OpDecorate", "OpMemberDecorate", "OpDecorateId", "OpDecorateString", "OpMemberDecorateString"}
KEPT = ("OpEntryPoint", "OpExecutionMode", "OpExecutionModeId", "OpMemoryModel")


def names_of(m):
    return {i.ids[0]: i.lits[0] for i in m.globals if i.op == "OpName"}


def match_globals(mt, b, s, problems):
    """SPEC's definitions (capabilities, imports, the memory model, types, constants, variables) as
    BASE's, some removed. Their order is not held: dxbc-spirv's builder declares a type or constant
    where its first use is emitted, so removing a path can move one. A definition matches BASE's of the
    same opcode and literals whose operands are the matched ones (SPIR-V defines a type or constant
    before its uses); a variable is matched to one with the same name."""
    sdefs = [i for i in s.globals if i.op not in DEFERRED]
    bdefs = [i for i in b.globals if i.op not in DEFERRED]
    sn, bn = names_of(s), names_of(b)
    used = set()
    for si in sdefs:
        found = None
        for k, bi in enumerate(bdefs):
            if k in used or bi.op != si.op:
                continue
            found = mt.try_match(si, bi)
            if found is not None and si.op == "OpVariable" and sn.get(si.rid) != bn.get(bi.rid):
                found = None
            if found is not None:
                used.add(k)
                break
        if found is None:
            problems.append(f"SPEC's {si.op} %{si.rid} {si.lits} is no definition of BASE's")
            return len(bdefs) - len(used)
        mt.commit(found)
    for k, bi in enumerate(bdefs):
        if k not in used and bi.op in KEPT:
            problems.append(f"BASE's {bi.op} is missing from SPEC")
    return len(bdefs) - len(used)


def match_annotations(mt, b, s, problems):
    """Every name, decoration and execution mode of SPEC is BASE's for the mapped ids, BASE's entry
    point is SPEC's (its interface a subsequence of BASE's, in order) and so is every execution mode."""
    def key(i, m, mapped):
        ids = []
        for x in i.ids:
            if mapped:
                if x not in mt.fwd:
                    return None
                x = mt.fwd[x]
            ids.append(x)
        return (i.op, tuple(ids), repr(i.lits))
    bkeys = {}
    for i in b.globals:
        if i.op in DEFERRED and i.op != "OpEntryPoint":
            k = key(i, b, False)
            bkeys[k] = bkeys.get(k, 0) + 1
    for i in s.globals:
        if i.op not in DEFERRED or i.op in ("OpEntryPoint", "OpSource", "OpString", "OpSourceContinued",
                                             "OpSourceExtension", "OpModuleProcessed", "OpLine", "OpNoLine"):
            continue
        k = key(i, s, True)
        if k is None:
            problems.append(f"SPEC's {i.op} {i.lits} names an id SPEC defines nowhere BASE does")
            continue
        if not bkeys.get(k):
            problems.append(f"SPEC's {i.op} {i.lits} on %{i.ids[0]} is not BASE's on %{k[1][0]}")
            continue
        bkeys[k] -= 1
    # the reverse inclusion: every decoration BASE puts on ids SPEC kept is SPEC's too; a decoration of a
    # kept id is no deletion the word licenses (NoContraction, Centroid, Flat, ...)
    skeys = {}
    for i in s.globals:
        if i.op in DECORATIONS:
            k = key(i, s, True)
            if k is not None:
                skeys[k] = skeys.get(k, 0) + 1
    for i in b.globals:
        if i.op in DECORATIONS and all(x in mt.back for x in i.ids):
            k = key(i, b, False)
            if not skeys.get(k):
                problems.append(f"BASE's {i.op} {i.lits} on %{i.ids[0]} (kept as %{mt.back[i.ids[0]]}) is missing from SPEC")
            else:
                skeys[k] -= 1
    for i in b.globals:
        if i.op in ("OpExecutionMode", "OpExecutionModeId"):
            want = (i.op, tuple(mt.back.get(x, -1) for x in i.ids), repr(i.lits))
            if not any(key(j, s, False) == want for j in s.globals if j.op == i.op):
                problems.append(f"BASE's execution mode {i.lits} is missing from SPEC")
    se = [i for i in s.globals if i.op == "OpEntryPoint"]
    be = [i for i in b.globals if i.op == "OpEntryPoint"]
    if len(se) != 1 or len(be) != 1 or se[0].lits != be[0].lits or mt.fwd.get(se[0].ids[0]) != be[0].ids[0]:
        problems.append("the entry point differs")
        return
    k = 1
    for x in se[0].ids[1:]:
        y = mt.fwd.get(x)
        while k < len(be[0].ids) and be[0].ids[k] != y:
            k += 1
        if y is None or k == len(be[0].ids):
            problems.append(f"SPEC's entry point interface %{x} is not BASE's, in BASE's order")
            return
        k += 1


def match_function(mt, ev, bfn, bblocks, sblocks, problems, stats):
    live, succ, pred = ev.live[bfn.rid], ev.succ[bfn.rid], ev.pred[bfn.rid]
    by_label = {blk[0].rid: blk for blk in bblocks}
    order = [blk[0].rid for blk in bblocks]
    # SPEC's blocks, each a run of BASE's live blocks: its head first (SPEC's labels in BASE's order),
    # then each next block while the run's last has one live successor that has one live predecessor
    # and SPEC has no block for it
    runs, j = [], 0
    for sb in sblocks:
        while j < len(order) and (order[j] not in live or mt.try_match(sb[0], by_label[order[j]][0]) is None):
            j += 1
        if j == len(order):
            problems.append(f"function %{bfn.rid}: SPEC's block %{sb[0].rid} is no live block of BASE")
            return
        mt.commit(mt.try_match(sb[0], by_label[order[j]][0]))
        runs.append([order[j]])
        j += 1
    heads = {r[0] for r in runs}
    head_of = {}
    for r in runs:
        while True:
            last = r[-1]
            nxt = succ[last]
            if len(nxt) == 1 and nxt[0] not in heads and len(pred[nxt[0]]) == 1 and nxt[0] in live:
                r.append(nxt[0])
                continue
            break
        for x in r:
            head_of[x] = r[0]
    covered = {x for r in runs for x in r}
    missing = [x for x in order if x in live and x not in covered]
    if missing:
        problems.append(f"function %{bfn.rid}: live blocks of BASE missing from SPEC: {['%' + str(x) for x in missing]}")
        return
    stats["blocks_dead"] += sum(1 for x in order if x not in live)
    stats["blocks_merged"] += sum(len(r) - 1 for r in runs)
    block_map = {"live": live, "pred": pred, "head_of": head_of}
    for sb, run in zip(sblocks, runs):
        block_map["label"] = run[0]
        body = []
        for x in run:
            blk = by_label[x]
            for i in blk[1:]:
                body.append((x, i))
        last = run[-1]
        # SPEC's body against the run's instructions: BASE's terminators inside the run are its merges
        sbody = sb[1:]
        pos = 0
        for si in sbody:
            matched = False
            while pos < len(body):
                lab, bi = body[pos]
                block_map["label"] = lab
                bi_eff = folded_terminator(ev, bi) if bi.op in TERMINATORS else bi
                t = mt.try_match(si, bi_eff, block_map)
                if t is not None and (bi.op not in TERMINATORS or lab == last):
                    mt.commit(t)
                    pos += 1
                    matched = True
                    break
                why = deletable(ev, bi, lab, last, succ, live)
                if why is None:
                    problems.append(f"function %{bfn.rid}, block %{run[0]}: SPEC's {si.op} %{si.rid} matches no "
                                    f"instruction of BASE before BASE's {bi.op} %{bi.rid}, which cannot be deleted")
                    return
                stats["instructions_removed"] += 1
                pos += 1
            if not matched:
                problems.append(f"function %{bfn.rid}, block %{run[0]}: SPEC's {si.op} %{si.rid} matches nothing of BASE")
                return
        for lab, bi in body[pos:]:
            if deletable(ev, bi, lab, last, succ, live) is None:
                problems.append(f"function %{bfn.rid}, block %{run[0]}: BASE's {bi.op} %{bi.rid} is missing from SPEC")
                return
            stats["instructions_removed"] += 1


class _Branch:
    __slots__ = ("op", "rtype", "rid", "ids", "lits", "index")

    def __init__(self, target):
        self.op, self.rtype, self.rid, self.ids, self.lits, self.index = "OpBranch", None, None, [target], [], -1


def folded_terminator(ev, bi):
    """BASE's terminator as the word leaves it: a conditional branch or switch on a folded value is a
    branch to its one live edge."""
    if bi.op == "OpBranchConditional":
        c = ev.val(bi.ids[0])
        if isinstance(c, bool):
            return _Branch(bi.ids[1] if c else bi.ids[2])
    if bi.op == "OpSwitch":
        sel = ev.val(bi.ids[0])
        if isinstance(sel, int) and not isinstance(sel, bool):
            hit = [tg for v, tg in zip(bi.lits, bi.ids[2:]) if (v[0] if isinstance(v, tuple) else v) == sel]
            return _Branch(hit[0] if hit else bi.ids[1])
    return bi


def deletable(ev, bi, lab, last, succ, live):
    """Why BASE's instruction may be absent from SPEC's block, or None."""
    if bi.op in TERMINATORS:
        # a terminator inside a run is a merge: its block has one live successor, the run's next
        return "merged" if lab != last and len(succ[lab]) == 1 else None
    if bi.op == "OpLoopMerge":
        # a loop's header keeps its merge while the loop does: only a loop whose continue target the
        # word made dead (no back edge left) is no loop
        return "the word removed its loop" if bi.ids[1] not in live else None
    if bi.op in MERGES:
        # the structured merge of a branch the word folded, or of a block merged into its run
        return "the word folded its branch" if len(succ[lab]) <= 1 or lab != last else None
    if bi.op == "OpFunctionCall":
        return "the word decides it" if ev.resolve(bi.rid) in ev.value else None
    if bi.op in EFFECTS:
        return None
    return "no effect"


def specialized_word(variant):
    m = re.search(r"sampler_modes=([^;]+)", variant or "")
    if not m or m.group(1) == "none":
        return None
    return int(m.group(1), 0)


def batch(base_dir, spec_dir, expect):
    reps, problems = {}, []
    for refl in sorted(Path(spec_dir).glob("*.json")):
        r = json.loads(refl.read_text())
        if r.get("schema") != "sa2-d3d9gpu-shader-reflection/v1":
            continue
        word = specialized_word(r.get("variant"))
        if word is None:
            continue
        name = refl.stem
        base_refl = Path(base_dir) / f"{name}.json"
        if not base_refl.is_file():
            problems.append(f"{name}: no base reflection in {base_dir}")
            continue
        br = json.loads(base_refl.read_text())
        want_variant = re.sub(r"sampler_modes=[^;]+", "sampler_modes=none", r["variant"])
        if (br.get("bytecode_sha256"), br.get("variant"), br.get("profile_sha256"), br.get("translator")) != \
                (r.get("bytecode_sha256"), want_variant, r.get("profile_sha256"), r.get("translator")):
            problems.append(f"{name}: the base is not the same shader, variant, profile and translator with the word off")
            continue
        try:
            rep = check(Path(base_dir) / f"{name}.spv", Path(spec_dir) / f"{name}.spv", word)
        except (Fail, spv_canon.SpvError, KeyError, IndexError) as e:
            rep = {"ok": False, "problems": [f"unreadable: {e}"]}
        rep["word"] = word
        reps[name] = rep
        if not rep["ok"]:
            problems.append(f"{name}: {'; '.join(rep['problems'])}")
    if expect is not None and len(reps) != expect:
        problems.append(f"{len(reps)} specialized modules, not {expect}")
    return {"ok": not problems and bool(reps), "modules": len(reps), "problems": problems, "checked": reps}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("base", nargs="?")
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--word", type=lambda x: int(x, 0))
    ap.add_argument("--batch", nargs=2, metavar=("BASE_DIR", "SPEC_DIR"))
    ap.add_argument("--expect", type=int)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.batch:
        rep = batch(a.batch[0], a.batch[1], a.expect)
    else:
        if not (a.base and a.spec) or a.word is None:
            ap.error("BASE SPEC --word W, or --batch BASE_DIR SPEC_DIR")
        try:
            rep = check(a.base, a.spec, a.word)
        except (Fail, spv_canon.SpvError) as e:
            rep = {"ok": False, "problems": [str(e)]}
    if a.json or a.batch:
        print(json.dumps(rep, indent=1, default=str))
    else:
        for p in rep["problems"]:
            print(f"  {p}")
    print("SPV_SPECIALIZE_OK" if rep["ok"] else "SPV_SPECIALIZE_FAIL")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
