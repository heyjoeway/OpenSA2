#!/usr/bin/env python3
"""Per-fixture plans: how a registered fixture's rows become guest state and back.

One builder per fixture id. A builder returns a `Plan`: the guest address to call, the
per-row op stream (harness/plan_ops.h), the expected words the fixture's own columns
carry, and the names of the output columns so a divergence report can say which field
diverged.

The calling conventions are not invented here. Each one cites where it was read:
`harness/fixture_marshal.c` for the eleven the S2 wrap already drove, and, for a
function no harness had, the function's own disassembly at the address the registry
names (recorded in the `abi` string and in the journal).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# harness/fixture_shapes.h, verbatim: the arenas the fixture vectors were generated
# against, inside the runtime's scratch region.
A_ARGS = 0x03000000
A_MATRIX = A_ARGS + 0x000
A_VECTOR = A_ARGS + 0x100
A_POINT = A_ARGS + 0x200
A_OUT = A_ARGS + 0x300
A_ENTITY = A_ARGS + 0x400
A_OBJECT = A_ARGS + 0x500
A_MSTACK = A_ARGS + 0x100000
A_MSTACK_FRAMES = 16
A_STACK_TOP = 0x03F00000
ESP0 = A_STACK_TOP - 0x3C
G_MATRIX_STACK_TOP = 0x01A557FC
G_MATRIX_STACK_LIMIT = 0x02670588
G_MATRIX_STACK_FLOOR = 0x0267053C

# register indices, runtime/cpu_state.h
EAX, ECX, EDX, EBX, ESP, EBP, ESI, EDI = range(8)

OP_END, OP_POKE, OP_FILL, OP_REG, OP_CALL, OP_PEEK, OP_EMIT_REG, OP_EMIT_ST0, \
    OP_EMIT_WORD, OP_RESET, OP_SENTINEL, OP_EMIT_ST0_F64, OP_PEEK_PTR, OP_CW, \
    OP_EMIT_CW = range(15)


class Ops:
    def __init__(self) -> None:
        self.w: list[int] = []

    def poke(self, addr: int, words) -> "Ops":
        words = list(words)
        self.w += [OP_POKE, addr, len(words) * 4] + [x & 0xFFFFFFFF for x in words]
        return self

    def fill(self, addr: int, nbytes: int, value: int = 0) -> "Ops":
        self.w += [OP_FILL, addr, nbytes, value]
        return self

    def reg(self, idx: int, value: int) -> "Ops":
        self.w += [OP_REG, idx, value & 0xFFFFFFFF]
        return self

    def sentinel(self) -> "Ops":
        self.w += [OP_SENTINEL]
        return self

    def call(self, va: int = 0, timeout: int = 5) -> "Ops":
        self.w += [OP_CALL, va, timeout]
        return self

    def peek(self, addr: int, nbytes: int) -> "Ops":
        self.w += [OP_PEEK, addr, nbytes]
        return self

    def emit_reg(self, idx: int) -> "Ops":
        self.w += [OP_EMIT_REG, idx]
        return self

    def peek_ptr(self, ptr_at: int, nbytes: int) -> "Ops":
        self.w += [OP_PEEK_PTR, ptr_at, nbytes]
        return self

    def emit_word(self, value: int) -> "Ops":
        """A literal output word, for a column a row does not reach (a padded group)."""
        self.w += [OP_EMIT_WORD, value & 0xFFFFFFFF]
        return self

    def emit_st0(self) -> "Ops":
        self.w += [OP_EMIT_ST0]
        return self

    def cw(self, value: int) -> "Ops":
        """The row's x87 control word. Bits 10-11 are the rounding control, which
        reaches the arithmetic only through the host `fenv` (runtime/remill_runtime.cpp)."""
        self.w += [OP_CW, value & 0xFFFF]
        return self

    def emit_cw(self) -> "Ops":
        self.w += [OP_EMIT_CW]
        return self

    def bytes(self) -> bytes:
        return struct.pack(f"<{len(self.w) + 1}I", *self.w, OP_END)


def reset_arenas(o: Ops) -> Ops:
    """harness/fixture_marshal.c `reset_arenas`, op for op."""
    o.fill(A_ARGS, 0x10000)
    o.fill(A_MSTACK, 0x30 * A_MSTACK_FRAMES + 0x100)
    o.fill(A_STACK_TOP - 0x10000, 0x10000)
    o.poke(A_OUT, [0xDEADBEEF, 0xDEADBEEF, 0xDEADBEEF])
    return o


def matrix_stack(o: Ops, top: int, limit: int) -> Ops:
    o.poke(G_MATRIX_STACK_TOP, [A_MSTACK + 0x30 * top])
    o.poke(G_MATRIX_STACK_LIMIT, [A_MSTACK + 0x30 * limit])
    o.poke(G_MATRIX_STACK_FLOOR, [A_MSTACK])
    return o


@dataclass
class Plan:
    fixture: str
    entry_va: int
    out_words: int
    columns: tuple[str, ...]
    rows: list[bytes]
    expected: list[tuple[int, ...]]
    abi: str
    note: str = ""
    globals_ops: bytes = b""
    indirect_out: dict = field(default_factory=dict)
    # Registered rows this plan does not drive, one entry per row, each naming the row
    # and why. `run.py` fails the fixture when the registry's row count minus the driven
    # rows is anything other than the length of this list: a row that is neither driven
    # nor excluded by name is a silent gap, which is what let `native_rows_driven`
    # exceed `native_rows_registered` while four registered rows were never driven
    # (G2b finding 6).
    excluded: list[dict] = field(default_factory=list)

    def encode(self) -> bytes:
        head = b"SA2PLN1\0" + struct.pack("<5I", 1, self.entry_va, len(self.rows),
                                          self.out_words, len(self.globals_ops))
        body = b"".join(struct.pack("<I", len(r)) + r for r in self.rows)
        return head + self.globals_ops + body


BUILDERS: dict = {}


def builder(fixture_id: str):
    def deco(fn):
        BUILDERS[fixture_id] = fn
        return fn
    return deco


# ---------------------------------------------------------------- 0x0048B9F0
@builder("ninja-object-matrix-v1")
def _nom1(rows, header, sections=None) -> Plan:
    """BuildObjectTreePolygons' object-local matrix. Shape and ABI are
    harness/fixture_marshal.c's `FN_OBJECT_TREE`, unchanged."""
    out, exp = [], []
    for r in rows:
        o = Ops()
        reset_arenas(o)
        o.poke(A_MSTACK, r[0:12])
        o.poke(A_OBJECT + 8, r[12:21])
        # The fixture records no model data, and 0x0048B9F0 hands the object to
        # 0x0048B610 (now lifted) after building the matrix; that routine reads
        # object[+4]->[+0x14] as a polygon count. An empty model (count 0) makes it
        # return at once, which is the least state that lets the call run to its own
        # `ret`. With a null model pointer it faults at guest 0x14 -- as the native
        # would, since the game never passes one.
        o.poke(A_OBJECT + 4, [A_OBJECT + 0x100])
        matrix_stack(o, 0, A_MSTACK_FRAMES - 1)
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [A_OBJECT, 0, 0])
        o.call()
        # The object-local matrix is frame 1: the routine pushes a frame on entry and
        # pops it before returning, so the top pointer after the call names the parent.
        o.peek(A_MSTACK + 0x30, 48)
        out.append(o.bytes())
        exp.append(tuple(r[21:33]))
    return Plan("ninja-object-matrix-v1", 0x0048B9F0, 12,
                tuple(f"m{i}" for i in range(12)), out, exp,
                abi="harness/fixture_marshal.c FN_OBJECT_TREE (cdecl(object,0,0), matrix stack)",
                note="matrix-stack frame 1 holds the object-local matrix; harness/fixture_marshal.c "
                     "reads it through the top pointer, which only agrees while the call has not "
                     "returned (the S1 harness trapped at the first Ninja helper)")


# ---------------------------------------------------------------- 0x00459260
@builder("vector-to-angles-v1")
def _v2a1(rows, header, sections=None) -> Plan:
    """Direction -> two BAM angles. ABI from harness/fixture_natives.c's
    `native_vec_angles`, which is the S1 harness's oracle stub for this very
    function: cdecl(x, y, z) with edi and esi the two out pointers."""
    out, exp = [], []
    for r in rows:
        o = Ops()
        o.poke(A_OUT, [0xDEADBEEF, 0xDEADBEEF])
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [r[0], r[1], r[2]])
        o.reg(EDI, A_OUT).reg(ESI, A_OUT + 4)
        o.call()
        o.peek(A_OUT, 8)
        out.append(o.bytes())
        exp.append((r[3], r[4]))
    return Plan("vector-to-angles-v1", 0x00459260, 2, ("angle1", "angle2"), out, exp,
                abi="harness/fixture_natives.c native_vec_angles: cdecl(x,y,z), [edi]=angle1, [esi]=angle2")


# ---------------------------------------------------------------- 0x00468EF0
@builder("stairs-transform-v1")
def _stf1(rows, header, sections=None) -> Plan:
    """Stairs-branch matrix-stack direction transform.

    ABI read out of the disassembly at 0x00468EF0 (173 bytes): eax = &dir[3]
    (its three floats are pushed to 0x00459260), ecx = the Z angle, [esp+4] =
    &in[3], which 0x004273B0 overwrites in place with the transformed vector.
    """
    out, exp = [], []
    for r in rows:
        o = Ops()
        reset_arenas(o)
        o.poke(A_VECTOR, r[0:3])
        o.poke(A_POINT, r[4:7])
        matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [A_POINT])
        o.reg(EAX, A_VECTOR).reg(ECX, r[3])
        o.call()
        o.peek(A_POINT, 12)
        out.append(o.bytes())
        exp.append(tuple(r[7:10]))
    return Plan("stairs-transform-v1", 0x00468EF0, 3, ("out0", "out1", "out2"), out, exp,
                abi="disassembly at 0x00468EF0: eax=&dir[3], ecx=angle, [esp+4]=&in[3] transformed in place")


# ---------------------------------------------------------------- 0x00469050
# PResetPos. ABI and field offsets read out of the disassembly at 0x00469050
# (585 bytes): eax = the entity (its words 1..4 at +4/+8/+0xC/+0x10), ebx = the
# world velocity vector (words 0..2 at +0/+4/+8), [esp+4] = the player struct,
# whose word n sits at 4n: w31..33 at 0x7C..0x84, w34/w35 at 0x88/0x8C, w63 at
# 0xFC, and the results w25..27 at 0x64..0x6C and w13 at 0x34. Gravity is the
# guest global vector at 0x01DE94A0.
A_PLAYER = A_ARGS + 0x1000
A_WORLD = A_ARGS + 0x600
G_GRAVITY = 0x01DE94A0


def _preset_row(r, sel: int | None) -> bytes:
    o = Ops()
    reset_arenas(o)
    o.fill(A_PLAYER, 0x200)
    o.poke(A_ENTITY + 4, r[3:7])                 # entity words 1..4
    o.poke(A_WORLD, r[7:10])                     # world words 0..2
    o.poke(A_PLAYER + 0x88, [r[10], r[11]])      # player w34, w35
    o.poke(A_PLAYER + 0x7C, r[12:15])            # player w31..33
    o.poke(G_GRAVITY, r[15:18])                  # the gravity global
    o.poke(A_PLAYER + 0xFC, [r[18]])             # player w63
    matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_PLAYER])
    o.reg(EAX, A_ENTITY).reg(EBX, A_WORLD)
    o.call()
    o.peek(A_PLAYER + 0x64, 12)                  # player w25..27
    o.peek(A_PLAYER + 0x34, 4)                   # player w13
    return o.bytes()


def _preset_plan(fixture: str, rows, out_at: int) -> Plan:
    enc, exp = [], []
    for r in rows:
        enc.append(_preset_row(r, None))
        exp.append(tuple(r[out_at:out_at + 4]))
    return Plan(fixture, 0x00469050, 4, ("w25", "w26", "w27", "w13"), enc, exp,
                abi="disassembly at 0x00469050: eax=&entity, ebx=&world velocity, [esp+4]=&player; "
                    "gravity is the guest global at 0x01DE94A0")


# ---------------------------------------------------------------- 0x0048BAF0
# CL_ColPolListUpNear. ABI read out of the disassembly at 0x0048BAF0: one stack
# argument, a pointer to the seven-word query record the fixture's head words carry
# (`mov esi,[esp+20h]`, then `lea eax,[esi+10h]` for the motion vector whose length
# 0x0077FC30 takes, `[esi]` the radius, `[esi+4..0Ch]` the point the sphere test
# subtracts at 0x0048BC40). It returns the selected count in eax (`mov eax,ebp`) and
# writes that many node pointers from 0x0194F030 up, with the count mirrored into the
# guest global 0x0194715C.
#
# What it reads that the fixture does not carry is the point of the record below: the
# world. 0x0048BAF0 calls ListGroundForCollision (0x0047CD60) and then walks the group
# list at the guest global 0x01A5A400 -- each group's id at [+8] matched against the
# near-set table at [0x01A5A2DC]+4 (stride 0x0C, length the word at 0x01DE9484), its
# Z-sorted node chains at [+0x10] and [+0x14], its tolerance at [+0x28] -- and the
# sphere test runs over node fields [+8..+0x14]. None of the group list, the chain
# order, the tolerances or the near-set table is in SA2CSE1: the fixture records the
# polygon rows, not the runtime structure the routine walks.
A_QUERY = A_ARGS + 0x800
G_NEAR_SET_TABLE = 0x01A5A2DC
G_GROUP_LIST = 0x01A5A400
G_NEAR_SET_COUNT = 0x01DE9484
G_SELECTED_COUNT = 0x0194715C


A_NODES = A_ARGS + 0x210000
A_GROUP = A_ARGS + 0x218000
NODE_STRIDE = 0x40
GROUP_ID = 0x0BAF0001
A_CSE_COL = A_ARGS + 0x220000   # not A_COL: the query record already owns A_ARGS+0x800
G_SELECTED_LIST = 0x0194F030


@builder("candidate-enumeration-v1")
def _cse1(queries, header, sections=None) -> Plan:
    """SA2CSE1: the Z-chain walk and sphere test of CL_ColPolListUpNear.

    The corpus receipt recorded this fixture as driving and failing on "a guest world
    its format does not record". That was measured against a plan that poked a null
    group list; the format does record the world, in normalized form.

    `walk_cse1`'s `polygons` section is 72 records of 16 words whose first two are the
    forward and backward chain links **as ordinals** (0xFFFFFFFF for null), which the
    stager itself says it normalized from pointers
    (`stage_precise_candidate_enumeration_fixture.py`: `pointer_to_index`, then
    `normalized.append([next_index, previous_index] + words 2..15)`). The remaining
    fourteen are the node the routine reads: centre at +0x08/0x0C/0x10, squared radius
    at +0x14, three vertices and the polygon flags. Re-pointering the two links is the
    whole of the reconstruction; nothing is invented.

    The rest of the world, from the disassembly at 0x0048BAF0..0x0048BDE3:

      0x0048BB41  the routine calls 0x0047CD60 itself, so the near-set table it then
                  matches against is the one the gather leaves at [0x01A5A2DC], count
                  the u16 at 0x01DE9484 -- the plan hands the gather one COL record
                  whose payload word is this group's id and whose centre is the query
                  point, so the group is visited. That record is a *gate*: it decides
                  whether the chain is walked, not what the walk finds.
      0x0048BB46  ecx = [0x01A5A400], the group list; [ecx] is the next group
                  (0x0048BDCA), [ecx+8] the id matched at 0x0048BB80 against
                  [0x01A5A2DC]+4 with stride 0x0C, [ecx+0x10] and [ecx+0x14] the two
                  chain ends, [ecx+0x28] the tolerance -- which is SA2CSE1's own
                  `z_extent` header word, the value the stager's `replay` uses for
                  exactly that purpose.
      0x0048BCB5  each accepted node's pointer is written from 0x0194F030 up, the
                  count mirrored into 0x0194715C and returned in eax.
      0x0048BB1A  the expanded radius (`njScalor(motion) + radius`) is stored to the
                  frame word at (entry ESP) - 0x0C, which is SA2CSE1's `expanded`
                  column.
    """
    polys = sections["polygons"]
    max_sel = max(q[9] for q in queries)
    n_nodes = len(polys)

    def node(i: int) -> int:
        return A_NODES + NODE_STRIDE * i

    def link(v: int) -> int:
        return 0 if v == 0xFFFFFFFF else node(v)

    g = Ops()
    words: list[int] = []
    for i, p in enumerate(polys):
        words += [link(p[0]), link(p[1])] + list(p[2:16])
    g.poke(A_NODES, words)
    g.poke(A_GROUP, [0, 0, GROUP_ID, 0])                  # [+0] next, [+8] id
    g.poke(A_GROUP + 0x10, [node(0), node(n_nodes - 1)])  # the two chain ends
    g.poke(A_GROUP + 0x28, [header["z_extent"]])          # the group tolerance
    g.poke(G_GROUP_LIST, [A_GROUP])
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_ADMIT_DESC, [A_DESC])                        # the gather's own gate
    g.poke(G_FIRST_COUNT, [0])
    g.poke(G_FIRST_BASE, [A_DESC])
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)

    enc, exp = [], []
    for q in queries:
        o = Ops()
        reset_arenas(o)
        o.fill(G_SELECTED_LIST, 4 * (max_sel + 4))
        o.fill(A_BIG_NEAR, 0x100)
        o.poke(A_QUERY, q[1:8])                           # radius, point[3], motion[3]
        # One COL record, centred on the query point with a radius nothing can fail,
        # carrying this group's id: the gather's admission is a gate on the group, and
        # the fixture's outputs come from the chain walk behind it.
        o.fill(A_CSE_COL, COL_STRIDE)
        o.poke(A_CSE_COL, [q[2], q[3], q[4]])
        o.poke(A_CSE_COL + 0x0C, [FLT_MAX_BITS])
        o.poke(A_CSE_COL + 0x10, [GROUP_ID])
        o.poke(A_CSE_COL + 0x1C, [1])                     # 0x2003 set, 0x2002 clear
        o.poke(G_COL_TABLE, [A_CSE_COL])
        o.poke(G_COL_COUNT, [1])
        o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
        o.poke(G_NEAR_COUNT, [0])
        o.poke(A_GROUP + 0x1C, [0])
        o.poke(A_GROUP + 0x24, [0])
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [A_QUERY])
        o.call()
        o.emit_reg(EAX)
        o.peek(G_SELECTED_COUNT, 4)      # the mirror the routine keeps at 0x0194715C
        o.peek(A_GROUP + 0x24, 4)        # the group's own accepted counter
        o.peek(G_SELECTED_LIST, 4 * max_sel)
        enc.append(o.bytes())
        ordinals = q[12:12 + q[9]]
        exp.append((q[9], q[9], q[9]) + tuple(node(i) for i in ordinals)
                   + (0,) * (max_sel - len(ordinals)))
    return Plan("candidate-enumeration-v1", 0x0048BAF0, 3 + max_sel,
                ("selected_count", "count_mirror", "group_counter")
                + tuple(f"node{i}" for i in range(max_sel)), enc, exp,
                abi="disassembly at 0x0048BAF0: [esp+4] = &query[7] (radius, point[3], "
                    "motion[3]); eax = the selected count, mirrored into 0x0194715C, and "
                    "the selected node pointers are written from 0x0194F030 up; the group "
                    "list is [0x01A5A400] and the near-set table the [0x01A5A2DC] one the "
                    "call to 0x0047CD60 at 0x0048BB41 fills",
                note="the group list, the Z chain and the tolerance are rebuilt from the "
                     "fixture's own `polygons` section and `z_extent` header word; the one "
                     "COL record is a gate on the group, not an answer. SA2CSE1's "
                     "`expanded` and `visited` columns are not compared: `expanded` lives in "
                     "the frame word at (entry ESP)-0x0C, which the walk itself overwrites "
                     "with the Z upper bound at 0x0048BBB6, and no instruction keeps a visit "
                     "counter -- both are mid-call values, not boundary outputs. The count, "
                     "its mirror at 0x0194715C, the group's own counter at [group+0x24] and "
                     "every selected node pointer are compared.",
                globals_ops=g.bytes())


# ---------------------------------------------------------------- 0x00463EE0
# SA2_RejectedContactResponse. ABI read out of the disassembly at 0x00463EE0
# (the argument each instruction touches, with the fixture column beside it):
#
#   ecx              -> &contact point[3]      fixture w48..50, `fld [ecx]` at 0x00463F16
#   [esp+4]   arg0   -> entity                 `mov ebx,[esp+1Ch]`; status byte [ebx+4],
#                                              position [ebx+0x14..0x1C], kind [ebx+0]
#   [esp+8]   arg1   -> unread on this path
#   [esp+0Ch] arg2   -> player                 `mov eax,[esp+0Ch]`; speed [+0x64] = w25,
#                                              [+0xF0] = w60, result [+0x70..0x78] = w28..30
#   [esp+10h] arg3   -> work                   `mov ebp,[esp+2Ch]`; normal [+0x48] = w18..20,
#                                              [+0x60] = w24..26, result [+4] = w1..3
#   [esp+14h] arg4   -> &offset[3]             `mov eax,[esp+38h]` in the tail, w51..53
#   [esp+18h] arg5   -> &offset for entity kind 0x38 (`mov eax,[esp+3Ch]`); unused here
#   [esp+1Ch] arg6   -> the loop's accumulator, returned in eax (`mov eax,[esp+40h]`)
#   [esp+20h] arg7   -> the candidate attribute (`mov esi,[esp+44h]`; bits 0x1000, 0x400000)
#
# Column layout is the stager's docstring, quoted in
# Documentation/native-evidence-receipts/precise-rejected-contact-v1.json.
A_WORK = A_ARGS + 0x2000
A_CONTACT = A_ARGS + 0x700
A_OFFSET = A_ARGS + 0x720


@builder("rejected-contact-v1")
def _rc1(rows, header, sections=None) -> Plan:
    enc, exp = [], []
    for r in rows:
        o = Ops()
        reset_arenas(o)
        o.fill(A_PLAYER, 0x200)
        o.fill(A_WORK, 0x200)
        o.poke(A_ENTITY, r[4:12])            # entity words 0..7
        o.poke(A_PLAYER + 0x1C, [r[12]])     # player w7
        o.poke(A_PLAYER + 0x64, [r[13]])     # player w25
        o.poke(A_PLAYER + 0xF0, [r[14]])     # player w60
        o.poke(A_WORK, r[15:48])             # work words 0..32
        o.poke(A_CONTACT, r[48:51])          # the frame's contact point
        o.poke(A_OFFSET, r[51:54])           # the offset the tail subtracts
        matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [A_ENTITY, 0, A_PLAYER, A_WORK, A_OFFSET, A_OFFSET, r[3], r[2]])
        o.reg(ECX, A_CONTACT)
        o.call()
        o.peek(A_ENTITY + 0x14, 12)          # entity words 5..7
        o.peek(A_WORK + 4, 12)               # work words 1..3
        o.peek(A_PLAYER + 0x70, 12)          # player words 28..30
        o.emit_reg(EAX)                      # the accumulator it returns
        enc.append(o.bytes())
        exp.append(tuple(r[54:64]))
    return Plan("rejected-contact-v1", 0x00463EE0, 10,
                ("entity5", "entity6", "entity7", "work1", "work2", "work3",
                 "player28", "player29", "player30", "return"), enc, exp,
                abi="disassembly at 0x00463EE0: ecx=&contact point, [esp+4]=entity, [esp+0Ch]=player, "
                    "[esp+10h]=work, [esp+14h]=&offset, [esp+1Ch]=accumulator (returned), "
                    "[esp+20h]=attribute")


@builder("preset-position-v1")
def _prs1(rows, header, sections=None) -> Plan:
    return _preset_plan("preset-position-v1", rows, 19)


@builder("preset-position-corpus-v1")
def _prw1a(rows, header, sections=None) -> Plan:
    return _preset_plan("preset-position-corpus-v1", rows, 20)


@builder("preset-position-jump-v1")
def _prw1b(rows, header, sections=None) -> Plan:
    return _preset_plan("preset-position-jump-v1", rows, 20)


@builder("preset-position-stairs-v1")
def _prw1c(rows, header, sections=None) -> Plan:
    return _preset_plan("preset-position-stairs-v1", rows, 20)


# ---------------------------------------------------------------- 0x0047CD60
# ListGroundForCollision, entered at its own entry rather than at the admission
# branch inside it.
#
# The S2 corpus-run receipt declared `ground-admission-source-v1..v4` undrivable
# because the window at 0x0047D0EC reads `ah` -- the x87 condition codes from the
# `fcompp` two instructions earlier -- and the fixture does not record it. That is a
# property of where the boundary was drawn: at the containing entry the routine
# computes `ah` itself, and every remaining live-in is one of the fixture's own
# columns, one of its own header words, an image constant, or zero.
#
# ABI and offsets read out of the disassembly at 0x0047CD60 (1009 bytes), contiguous
# through 0x0047D150:
#
#   [esp+4..10h] cdecl(query_x, query_y, query_z, query_radius)
#   0x0047CFEA   gate on the guest word 0x0171CAAC
#   0x0047CFF9   gate on the guest pointer 0x019459DC
#   0x0047D020.. the second loop walks [0x01A5A2D4] for [0x01A5A2CC] records of 0x20
#                bytes: [+0]/[+4]/[+8] centre, [+0Ch] radius, [+10h] payload,
#                [+1Ch] attribute -- the same field offsets SA2GAS1's `cols` section
#                records (col_index, attribute, centre[3], radius)
#   0x0047D0DD   `fstp dword [esp+18h]` stores distance - query_radius, on both paths
#   0x0047D0E1.. `fld [esp+18h]; fld [esi+0Ch]; fcompp; fnstsw ax; test ah,41h`
#   admitted     three words written through [0x01A5A2DC] and the count at 0x01DE9484
#                incremented
#
# One COL record per row, so the count at 0x01DE9484 after the call is the fixture's
# `accepted` column directly. `distance_bits` is the callee's own [esp+18h], which is
# at a fixed guest address because the plan sets ESP: ESP0 (0x03EFFFC4) minus
# `sub esp,1Ch` and four pushes is 0x03EFFF98, and +0x18 is 0x03EFFFB0.
G_ADMIT_GATE = 0x0171CAAC
G_ADMIT_DESC = 0x019459DC
G_FIRST_COUNT = 0x01A5A2C8
G_FIRST_BASE = 0x01A5A2D0
G_COL_TABLE = 0x01A5A2D4
G_COL_COUNT = 0x01A5A2CC
G_NEAR_CURSOR = 0x01A5A2DC
G_NEAR_COUNT = 0x01DE9484
A_COL = A_ARGS + 0x800
A_DESC = A_ARGS + 0x900
A_NEAR = A_ARGS + 0xA00
ADMIT_DISTANCE_AT = ESP0 - 0x14          # the callee's [esp+18h]


def _admission_plan(fixture: str, rows, sections, out_at: tuple[int, ...],
                    columns: tuple[str, ...]) -> Plan:
    cols = sections["cols"]
    g = Ops()
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_ADMIT_DESC, [A_DESC])
    g.poke(G_FIRST_COUNT, [0])           # the first loop walks nothing
    g.poke(G_FIRST_BASE, [A_DESC])       # non-zero so base-0xC does not wrap
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    enc, exp = [], []
    for r in rows:
        col = cols[r[2]]                 # case field 2 is `col_slot`
        o = Ops()
        reset_arenas(o)
        o.poke(A_COL, [col[2], col[3], col[4]])      # centre[3]
        o.poke(A_COL + 0x0C, [col[5]])               # radius
        o.poke(A_COL + 0x10, [col[0]])               # col_index, the payload word
        o.poke(A_COL + 0x1C, [col[1]])               # attribute
        o.poke(G_COL_TABLE, [A_COL])
        o.poke(G_COL_COUNT, [1])
        o.poke(G_NEAR_CURSOR, [A_NEAR])
        o.poke(G_NEAR_COUNT, [0])
        o.poke(ADMIT_DISTANCE_AT, [0xDEADBEEF])
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [r[3], r[4], r[5], r[6]])   # query[3], query_radius
        o.call()
        o.peek(G_NEAR_COUNT, 4)
        if len(out_at) > 1:
            o.peek(ADMIT_DISTANCE_AT, 4)
        enc.append(o.bytes())
        exp.append(tuple(r[i] for i in out_at))
    return Plan(fixture, 0x0047CD60, len(out_at), columns, enc, exp,
                abi="disassembly at 0x0047CD60: cdecl(query[3], query_radius); the COL table is "
                    "[0x01A5A2D4] for [0x01A5A2CC] records of 0x20 bytes (centre[3], radius at "
                    "+0Ch, payload at +10h, attribute at +1Ch); the near-set count is 0x01DE9484 "
                    "and the write cursor [0x01A5A2DC]",
                note="entered at the containing routine's own entry, not at the 0x0047D0EC window "
                     "the corpus receipt called undrivable: `ah` is computed here, not read",
                globals_ops=g.bytes())


@builder("ground-admission-source-v1")
def _gas1(rows, header, sections=None) -> Plan:
    return _admission_plan("ground-admission-source-v1", rows, sections, (1,), ("accepted",))


@builder("ground-admission-source-v2")
def _gas2(rows, header, sections=None) -> Plan:
    return _admission_plan("ground-admission-source-v2", rows, sections, (1, 7),
                           ("accepted", "distance_bits"))


@builder("ground-admission-source-v3")
def _gas3(rows, header, sections=None) -> Plan:
    return _admission_plan("ground-admission-source-v3", rows, sections, (1, 7),
                           ("accepted", "distance_bits"))


@builder("ground-admission-source-v4")
def _gas4(rows, header, sections=None) -> Plan:
    return _admission_plan("ground-admission-source-v4", rows, sections, (1, 7),
                           ("accepted", "distance_bits"))


# ---------------------------------------------------------------- 0x0047D160
# RefreshCollisionObjectSet, entered at its own entry rather than at the 0x0047D583
# window the receipt named. Same correction as the admission family: at the routine's
# entry the fixture's own columns are the machine state, because SA2GNS*'s
# (ref[3], centre[3], radius) are the reference vector and the COL record the routine
# walks, at the offsets it walks them.
#
# Offsets read out of the disassembly at 0x0047D160 (1351 bytes):
#   0x0047D562   `[esp+28h]` initialised from the guest float at 0x0171CAA4 (the reach,
#                which is this fixture's own `reach_bits` header word, 0x43C80000 = 400.0)
#   0x0047D583.. the descriptor at [0x019459DC]: u16[0] total, u16[1] start, [+10h] the
#                COL array; `[ebx+0..8]` centre, `[ebx+0Ch]` radius, `[ebx+1Ch]` attribute
#   0x0047D615   `[esp+28h]` holds distance - reach on the select path
#   0x0047D629   the reject path reloads [0x016D4F38] into `[esp+28h]`
# so `distance_bits` is read from a second call whose COL radius is forced large enough
# to take the select path; the distance arithmetic never reads `[ebx+0Ch]`.
#
# `[esp+28h]` is at a fixed guest address because the plan sets ESP: ESP0 (0x03EFFFC4),
# `push ebp` -> 0x03EFFFC0 (already 8-aligned, so the `and esp` is a no-op),
# `sub esp,48h` -> 0x03EFFF78, four pushes -> 0x03EFFF68, +0x28 = 0x03EFFF90.
G_PHASE1_COUNT = 0x019459D4
G_PHASE1_BASE = 0x01A5A2E0
G_REACH = 0x0171CAA4
G_REACH_2 = 0x016D4F38
G_REF_PTR = 0x01DD92B0
G_REF_A = 0x01DEA6C0
G_REF_B = 0x01DEA6C4
A_REF = A_ARGS + 0x600
A_SELECT = A_ARGS + 0xB00
NEAR_DISTANCE_AT = 0x03EFFF90
FLT_MAX_BITS = 0x7F7FFFFF


def _near_set_plan(fixture: str, rows, header, out_at: tuple[int, ...],
                   columns: tuple[str, ...]) -> Plan:
    reach = header["reach_bits"]
    g = Ops()
    g.poke(G_PHASE1_COUNT, [0])          # phase 1 walks nothing
    g.poke(G_PHASE1_BASE, [A_DESC])      # non-zero base for that skip
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_REACH, [reach])
    g.poke(G_REACH_2, [reach])
    g.poke(G_REF_A, [0])
    g.poke(G_REF_B, [0])                 # -> the reference count stays 1
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    want_distance = len(out_at) > 1
    enc, exp = [], []
    for r in rows:
        o = Ops()
        reset_arenas(o)
        o.poke(A_REF, [r[2], r[3], r[4]])            # ref[3]
        o.poke(G_REF_PTR, [A_REF])
        o.poke(A_COL, [r[5], r[6], r[7]])            # centre[3]
        o.poke(A_COL + 0x0C, [r[8]])                 # radius
        o.poke(A_COL + 0x10, [0])
        o.poke(A_COL + 0x1C, [1])                    # attribute: 0x2002 clear
        o.poke(A_DESC, [1])                          # u16[0] = 1 total, u16[1] = 0 start
        o.poke(A_DESC + 0x10, [A_COL])
        o.poke(G_ADMIT_DESC, [A_DESC])
        o.poke(G_COL_TABLE, [A_SELECT])
        o.poke(G_NEAR_CURSOR, [A_NEAR])
        o.poke(G_COL_COUNT, [0])
        o.poke(G_NEAR_COUNT, [0])
        o.poke(NEAR_DISTANCE_AT, [0xDEADBEEF])
        o.reg(ESP, ESP0).sentinel()
        o.call()
        o.peek(G_COL_COUNT, 4)
        if want_distance:
            # Second call, identical but for a radius large enough that the select
            # branch is taken, so [esp+28h] is the distance rather than the reject
            # path's reload of the reach.
            o.poke(A_COL + 0x0C, [FLT_MAX_BITS])
            o.poke(G_COL_COUNT, [0])
            o.poke(G_NEAR_COUNT, [0])
            o.reg(ESP, ESP0).sentinel()
            o.call()
            o.peek(NEAR_DISTANCE_AT, 4)
        enc.append(o.bytes())
        exp.append(tuple(r[i] for i in out_at))
    return Plan(fixture, 0x0047D160, len(out_at), columns, enc, exp,
                abi="disassembly at 0x0047D160: the reference vector is [0x01DD92B0], the COL "
                    "descriptor [0x019459DC] (u16 total, u16 start, [+10h] the array of 0x20-byte "
                    "records), the reach the guest float at 0x0171CAA4, the selected count "
                    "0x01A5A2CC; `distance_bits` is the callee's [esp+28h]",
                note="entered at the containing routine's own entry, not at the 0x0047D583 window; "
                     "the distance column needs a second call on the select path because the "
                     "reject path at 0x0047D629 overwrites [esp+28h] with the reach",
                globals_ops=g.bytes())


@builder("ground-near-set-v1")
def _gns1(rows, header, sections=None) -> Plan:
    return _near_set_plan("ground-near-set-v1", rows, header, (1,), ("selected",))


@builder("ground-near-set-v2")
def _gns2(rows, header, sections=None) -> Plan:
    return _near_set_plan("ground-near-set-v2", rows, header, (1, 9),
                          ("selected", "distance_bits"))


@builder("ground-near-set-v3")
def _gns3(rows, header, sections=None) -> Plan:
    return _near_set_plan("ground-near-set-v3", rows, header, (1, 9),
                          ("selected", "distance_bits"))


@builder("ground-near-set-v4")
def _gns4(rows, header, sections=None) -> Plan:
    return _near_set_plan("ground-near-set-v4", rows, header, (1, 9),
                          ("selected", "distance_bits"))


# ---------------------------------------------------------------- 0x0047D160, whole table
# The same routine the near-set family drives, given the *whole* shipped COL table
# instead of one record.
#
# SA2GFC1 carries 1331 COL records (attribute, centre[3], radius) and, per tick, the
# reference point and the COL indices the probe observed in the near set. Phase 2 of
# 0x0047D160, read at 0x0047D4E7..0x0047D6AF:
#
#   0x0047D4E7  ecx = [0x019459DC]; ebx = [ecx+10h] is the COL array;
#               movsx [ecx+2] is `start` and movsx [ecx] `total`; the walk runs
#               total-start records upward from array + start*0x20
#   0x0047D550  eax = [ebx+1Ch]; `test eax,2003h` is the attribute gate -- the
#               fixture's own `attribute_mask`
#   0x0047D587.. centre [ebx+0/4/8] minus the reference, squared, summed, 0x008290A0
#               (sqrt), minus the reach at [0x0171CAA4], compared against [ebx+0Ch]
#   0x0047D641  on select: eight dwords of the record are copied to [0x01A5A2D4],
#               [0x01A5A2CC] is incremented, and the near cursor [0x01A5A2DC] takes a
#               three-word entry (attribute, [ebx+10h], 0) before advancing 0x0C
#
# `[ebx+10h]` is the record's NJS_OBJECT pointer, which SA2GFC1 does not record -- and
# does not need to: the plan lays out the table, so it writes the COL index there and
# reads the index straight back out of the near cursor. That is the fixture's own
# `selected COL indices` column, in the order the routine walked (ascending index,
# because `start` is 0 and ebx rises by 0x20), which is the order the stager wrote.
#
# The walk stops early at 128 selected records (0x0047D67A) or 0x400 near entries
# (0x0047D68B); the largest set in SA2GFC1 is 23, so neither bound is reached.
A_BIG = A_ARGS + 0x200000        # big per-fixture tables, outside reset_arenas' fill
A_BIG_NEAR = A_ARGS + 0x180000   # the near cursor's own output arena
A_BIG_SEL = A_ARGS + 0x190000    # the selected-record output arena
COL_STRIDE = 0x20
INDEX_TAG = 0x00010000           # payload word = INDEX_TAG | index, so slot 0 is not 0


def _col_table_ops(o: Ops, cols, base: int = A_BIG) -> Ops:
    """Lay a fixture's `(attribute, centre[3], radius)` records out as the guest's own
    0x20-byte COL records, with the index in the payload word at +0x10."""
    words: list[int] = []
    for i, c in enumerate(cols):
        words += [c[1], c[2], c[3], c[4], INDEX_TAG | i, 0, 0, c[0]]
    o.poke(base, words)
    return o


@builder("ground-funnel-v1")
def _gfc1(rows, header, sections=None) -> Plan:
    cols = sections["cols"]
    max_sel = max(len(r[2]) for r in rows)
    reach = header["reach_bits"]
    g = Ops()
    g.poke(G_PHASE1_COUNT, [0])          # phase 1 walks nothing
    g.poke(G_PHASE1_BASE, [A_DESC])
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_REACH, [reach])
    g.poke(G_REACH_2, [reach])
    g.poke(G_REF_A, [0])
    g.poke(G_REF_B, [0])                 # -> the reference count stays 1
    _col_table_ops(g, cols)
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    enc, exp = [], []
    for _tick, ref, sel in rows:
        o = Ops()
        reset_arenas(o)
        o.fill(A_BIG_NEAR, 0x1000)
        o.fill(A_BIG_SEL, 0x2000)
        o.poke(A_REF, list(ref))
        o.poke(G_REF_PTR, [A_REF])
        o.poke(A_DESC, [len(cols)])      # u16[0] total, u16[1] start = 0
        o.poke(A_DESC + 0x10, [A_BIG])
        o.poke(G_ADMIT_DESC, [A_DESC])
        o.poke(G_COL_TABLE, [A_BIG_SEL])
        o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
        o.poke(G_COL_COUNT, [0])
        o.poke(G_NEAR_COUNT, [0])
        o.reg(ESP, ESP0).sentinel()
        o.call()
        o.peek(G_COL_COUNT, 4)
        for i in range(max_sel):
            o.peek(A_BIG_NEAR + 0x0C * i + 4, 4)     # the payload word of entry i
        enc.append(o.bytes())
        exp.append((len(sel),) + tuple(INDEX_TAG | i for i in sel)
                   + (0,) * (max_sel - len(sel)))
    return Plan("ground-funnel-v1", 0x0047D160, 1 + max_sel,
                ("selected",) + tuple(f"index{i}" for i in range(max_sel)), enc, exp,
                abi="disassembly at 0x0047D160: the COL descriptor is [0x019459DC] "
                    "(u16 total, u16 start, [+10h] the array of 0x20-byte records), the "
                    "reference vector [0x01DD92B0], the reach the guest float at "
                    "0x0171CAA4, the selected count 0x01A5A2CC and the near cursor "
                    "[0x01A5A2DC], whose per-entry payload word is the record's [+10h]",
                note="the whole 1331-record shipped table, one call per tick; the plan "
                     "writes the COL index into the record's payload word, so the near "
                     "cursor hands the selected indices back directly",
                globals_ops=g.bytes())


# ---------------------------------------------------------------- 0x0047D160 + 0x0047CD60
# The two routines composed, which is what the gather and chain fixtures record: the
# near set 0x0047D160 builds out of the whole shipped COL table is exactly the table
# 0x0047CD60 then walks. The chaining is the guest's own -- 0x0047D160 writes the
# selected records through [0x01A5A2D4] and counts them in [0x01A5A2CC] (0x0047D641),
# and 0x0047CD60 reads [0x01A5A2D4] for [0x01A5A2CC] records (0x0047D005..0x0047D00D)
# -- so a plan that issues both calls in order reproduces it without a capture.
#
# 0x0047CD60's gather output, read at 0x0047D0F1: `[ebp] = attribute`, `[ebp+4] =
# [esi+10h]` (the record's payload word, which the plan set to the COL index) and
# `[ebp+8] = 0`, with the count in the u16 at 0x01DE9484. `[esp+18h]` after 0x0047D0DD
# is `distance - query_radius` for the record the walk is on, on both the admit and the
# reject path, which is the fixture's `distance_bits`.
MAX_NEAR = 0x80


def _funnel_ops(o: Ops, ref, ncols: int) -> Ops:
    """Build the near set for one reference point out of the whole COL table."""
    o.fill(A_BIG_NEAR, 0x1000)
    o.fill(A_BIG_SEL, MAX_NEAR * COL_STRIDE)
    o.poke(A_REF, list(ref))
    o.poke(G_REF_PTR, [A_REF])
    o.poke(A_DESC, [ncols])              # u16[0] total, u16[1] start = 0
    o.poke(A_DESC + 0x10, [A_BIG])
    o.poke(G_ADMIT_DESC, [A_DESC])
    o.poke(G_COL_TABLE, [A_BIG_SEL])
    o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
    o.poke(G_COL_COUNT, [0])
    o.poke(G_NEAR_COUNT, [0])
    o.reg(ESP, ESP0).sentinel()
    o.call(0x0047D160)
    return o


def _funnel_globals(header, cols) -> bytes:
    reach = header["reach_bits"]
    g = Ops()
    g.poke(G_PHASE1_COUNT, [0])
    g.poke(G_PHASE1_BASE, [A_DESC])
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_REACH, [reach])
    g.poke(G_REACH_2, [reach])
    g.poke(G_REF_A, [0])
    g.poke(G_REF_B, [0])
    g.poke(G_FIRST_COUNT, [0])
    g.poke(G_FIRST_BASE, [A_DESC])
    _col_table_ops(g, cols)
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    return g.bytes()


def _held_reference(ticks) -> dict:
    """The reference point whose near set a tick's gather actually walks.

    The gather runs *before* the tick's own refresh, so every call in tick i sees the
    set 0x0047D160 built at tick i-1; the first tick in the window sees its own. That
    is the predecessor's own reading of its captures
    (check_precise_ground_funnel_chain_smoke.py: `held = sets[ticks[0]]` ... `held =
    sets[tick]` after each tick), and the corpus shows the two sets differing at
    exactly one tick, which is what makes the distinction measurable.
    """
    order = [t[0] for t in ticks]
    ref = {t[0]: t[1:4] for t in ticks}
    return {t: ref[order[max(i - 1, 0)]] for i, t in enumerate(order)}


@builder("ground-gather-output-v1")
def _ggo1(rows, header, sections=None) -> Plan:
    """SA2GGO1: the list the gather leaves in the globals, in table order.

    One driver row per recorded observation, paired with the query at the same ordinal
    inside its own tick.

    The stager could not pair an observation to a call, because the probe bumps its
    call ordinal in a hook that runs after the function returns, so entry rows carry
    the preceding call's number and the pairing it tried succeeded on 88 of 152. It
    therefore made the weaker existential claim -- each observation equals the admitted
    set of *some* query captured at that tick -- and this plan used to implement it by
    driving all 442 (observation, query) candidates and passing a group when any member
    matched. That relaxation was load-bearing: on 65 of the 152 observations the FIRST
    candidate does not reproduce the observation (G2b finding 7), so 290 of the 442
    driven rows were candidates the fixture's own claim never required to match, and
    `rows_driven` exceeded `rows_registered` by exactly that surplus.

    The pairing the FILE can support is positional, and it is not the stager's: the file
    carries each tick's queries in first-seen order after de-duplication, and each tick's
    observations in capture order. Observation j of a tick against query j of that tick
    reproduces the recorded list on 152 of 152, with no exception and no residue:

      ordinal 0, 2 queries  -> query 0 (radius 5.0)    14 observations
      ordinal 0, 3 queries  -> query 0 (radius 10.0)   69 observations
      ordinal 1, 3 queries  -> query 1 (radius 5.0)    69 observations

    65 of those pair with a query that is not the tick's first, and 8 (ticks 123..126)
    are ones where the tick's first two queries both reproduce the observation, so the
    positional rule resolves them rather than being resolved by them. The wide query at
    radius 200.0 is the last of every tick and is never paired, which is the stager's own
    "the wide query never appears" holding row for row.

    So the existential rule is gone: this fixture is now compared row for row like every
    other, and a run in which the gather writes a different list for a query fails
    instead of being rescued by a sibling candidate.
    """
    cols = sections["cols"]
    ticks = _held_reference(sections["ticks"])
    max_no = max(len(o[2]) for o in rows)
    ordinal: dict[int, int] = {}
    enc, exp = [], []
    for tick, queries, observed in rows:
        k = ordinal.get(tick, 0)
        ordinal[tick] = k + 1
        if k >= len(queries):
            raise SystemExit(f"SA2GGO1 tick {tick}: observation {k} has no query at that "
                             f"ordinal ({len(queries)} queries recorded)")
        q = queries[k]
        o = Ops()
        reset_arenas(o)
        _funnel_ops(o, ticks[tick], len(cols))
        o.fill(A_BIG_NEAR, 0x1000)                   # the funnel's own triples go away
        o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
        o.poke(G_NEAR_COUNT, [0])
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [q[0], q[1], q[2], q[3]])
        o.call(0x0047CD60)
        o.peek(G_NEAR_COUNT, 4)
        for i in range(max_no):
            o.peek(A_BIG_NEAR + 0x0C * i + 4, 4)
        enc.append(o.bytes())
        exp.append((len(observed),) + tuple(INDEX_TAG | i for i in observed)
                   + (0,) * (max_no - len(observed)))
    return Plan("ground-gather-output-v1", 0x0047CD60, 1 + max_no,
                ("observed",) + tuple(f"index{i}" for i in range(max_no)), enc, exp,
                abi="0x0047D160 then 0x0047CD60, chained through the guest's own globals: "
                    "the near set at [0x01A5A2D4]/[0x01A5A2CC], the gather output through "
                    "[0x01A5A2DC] with the u16 count at 0x01DE9484 and the record's "
                    "payload word at [+10h] in each entry's second word; 0x0047CD60 is "
                    "cdecl(query[3], query_radius)",
                note="the whole 1331-record shipped table; the runtime builds the near set "
                     "itself before every gather. One row per recorded observation, paired "
                     "with the query at the same ordinal inside its own tick: 152 of 152 "
                     "reproduce, 65 of them against a query that is not the tick's first. "
                     "The existential rule this fixture used to pass under is withdrawn.",
                globals_ops=_funnel_globals(header, cols))


@builder("ground-funnel-chain-v1")
def _gfn1(rows, header, sections=None) -> Plan:
    """SA2GFN1: near set and admission composed, entry by entry.

    A call's entries are `(accepted << 16 | col_index, distance_bits)` in the order
    0x0047CD60 walks the near set. One row per entry: the near set is built from the
    whole table for that tick, then truncated to the first k+1 records by writing
    [0x01A5A2CC], so the gather's walk ends on entry k and `[esp+18h]` (0x0047D0DD)
    holds that entry's `distance - query_radius`. The row reads back
      * the payload word of near-set record k, which is entry k's COL index and so
        checks the funnel's membership and its walk order;
      * the admitted count after k+1 records, which is the running sum of the
        fixture's own `accepted` bits -- a per-entry flag would need a per-entry call
        and the count is the same information;
      * `[esp+18h]`, entry k's distance.
    """
    cols = sections["cols"]
    ticks = _held_reference(sections["ticks"])
    enc, exp = [], []
    for tick, q, entries in rows:
        running = 0
        for k, (word, distance) in enumerate(entries):
            accepted, index = word >> 16, word & 0xFFFF
            running += accepted
            o = Ops()
            reset_arenas(o)
            _funnel_ops(o, ticks[tick], len(cols))
            o.poke(G_COL_COUNT, [k + 1])             # stop the gather on entry k
            o.fill(A_BIG_NEAR, 0x1000)
            o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
            o.poke(G_NEAR_COUNT, [0])
            o.poke(ADMIT_DISTANCE_AT, [0xDEADBEEF])
            o.reg(ESP, ESP0).sentinel()
            o.poke(ESP0 + 4, [q[0], q[1], q[2], q[3]])
            o.call(0x0047CD60)
            o.peek(A_BIG_SEL + COL_STRIDE * k + 0x10, 4)
            o.peek(G_NEAR_COUNT, 4)
            o.peek(ADMIT_DISTANCE_AT, 4)
            enc.append(o.bytes())
            exp.append((INDEX_TAG | index, running, distance))
    return Plan("ground-funnel-chain-v1", 0x0047CD60, 3,
                ("col_index", "accepted_so_far", "distance_bits"), enc, exp,
                abi="0x0047D160 then 0x0047CD60 over the guest's own globals; the gather's "
                    "input count is [0x01A5A2CC], its output cursor [0x01A5A2DC] and count "
                    "the u16 at 0x01DE9484, and `distance - query_radius` is the callee's "
                    "[esp+18h], at a fixed guest address because the plan sets ESP",
                note="one row per recorded decision; the near set is rebuilt from the whole "
                     "1331-record table for every row, so nothing about it is assumed",
                globals_ops=_funnel_globals(header, cols))


# ---------------------------------------------------------------- 0x0048DBD7
# The selection and aggregation tail of CL_ColPolCheckTouchRe (0x0048CE40), lifted as
# its own window by `lift_set.py --block 0x0048DBD7:0x0048DDEF`. SA2SLC1 and SA2SLO1
# record exactly this window's inputs -- a record count and that many 22-word compact
# contact records -- and its outputs; the 4015-byte function around it walks the whole
# dynamic collision world to *produce* those records, which neither fixture carries.
#
# Read out of the disassembly at 0x0048DBD7..0x0048DDEE:
#   0x0048DBDC  ecx = [0x019471B0], the record count
#   0x0048DBFB  the running best is [0x0194A2C8] = record[0] word 21, and the scan runs
#               from [0x0194A364] = record[1] word 21 with stride 0x9C; `test ah,41h`
#               after `fcompp` replaces only when best > candidate, so the minimum wins
#               and ties keep the earliest
#   0x0048DC4B  each record is copied, 22 words, from 0x0194A274 + i*0x9C to the public
#               array 0x01DDB660 + i*0x58
#   0x0048DC72  `[ebx+2Ah] = dx` is csts.selected_nmb and `[ebx+28h] = cx` find_count
#   0x0048DC88.. record words 0..7 -> csts+0x34..0x50 and words 14..19 -> csts+0x6C..0x80
#   0x0048DCDE  `cmp ecx,1 / jne 0x0048DD17` -- one record takes the single-record exit
#               at 0x0048DD0B, more than one the averaging exit at 0x0048DDE4. That
#               branch is what SA2SLO1's `exit_site` column records.
#   0x0048DDA3  the averaging path computes `1.0 / count` with `fild/fld1/fdivrp` and
#               stores it as a float32 to `[esp+14h]` *after* popping edi and esi, so
#               it lands at (entry ESP) + 0x1C -- a word the single-record path never
#               writes. The plan seeds that word with a marker and reads it back, which
#               is how `exit_site` is *observed* rather than inferred from the count.
#
# The window ends `pop edi; pop esi; pop ebx; mov esp,ebp; pop ebp; ret`, so the
# sentinel goes at EBP+4 rather than at the entry ESP.
G_RECORD_COUNT = 0x019471B0
G_RECORDS = 0x0194A274
G_RECORDS_PUBLIC = 0x01DDB660
RECORD_STRIDE = 0x9C
RECORD_WORDS = 22
SELECT_WINDOW = 0x0048DBD7
A_CSTS = A_ARGS + 0x1800
SEL_EBP = ESP0 - 4               # `mov esp,ebp; pop ebp; ret` takes [EBP+4]
SEL_FRAME = ESP0 - 0x200         # the window's own entry ESP
SEL_RECIPROCAL_AT = SEL_FRAME + 0x1C
SEL_MARKER = 0xDEADBEEF


def _reciprocal_bits(count: int) -> int:
    return struct.unpack("<I", struct.pack("<f", 1.0 / count))[0]


def _selection_row(count: int, records) -> Ops:
    o = Ops()
    reset_arenas(o)
    o.fill(A_CSTS, 0x100)
    o.fill(G_RECORDS, RECORD_STRIDE * 8)
    o.fill(G_RECORDS_PUBLIC, 0x58 * 8)
    for i, rec in enumerate(records):
        o.poke(G_RECORDS + i * RECORD_STRIDE, list(rec))
    o.poke(G_RECORD_COUNT, [count])
    matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
    o.reg(ESP, ESP0).sentinel()          # the sentinel lands at EBP+4
    o.reg(EBP, SEL_EBP)
    o.poke(SEL_RECIPROCAL_AT, [SEL_MARKER])
    o.reg(ESP, SEL_FRAME)
    o.reg(EBX, A_CSTS)
    o.call(SELECT_WINDOW)
    return o


@builder("selection-corpus-v1")
def _slc1(rows, header, sections=None) -> Plan:
    enc, exp = [], []
    for head, recs in rows:
        _tick, count, selected = head
        o = _selection_row(count, recs)
        o.peek(A_CSTS + 0x28, 4)         # u16 find_count, u16 selected_nmb
        enc.append(o.bytes())
        exp.append(((selected << 16) | count,))
    return Plan("selection-corpus-v1", SELECT_WINDOW, 1, ("find_count|selected",), enc, exp,
                abi="window 0x0048DBD7..0x0048DDEE: ebx = &csts, [0x019471B0] = the record "
                    "count, the records at 0x0194A274 stride 0x9C; csts+0x28 is find_count "
                    "and csts+0x2A selected_nmb, both u16, written at 0x0048DC72/0x0048DC84",
                note="the selection decision only; the 4015-byte function that produces the "
                     "records is not entered, because neither SA2SLC1 nor SA2SLO1 records "
                     "the collision world it walks")


@builder("selection-output-v1")
def _slo1(rows, header, sections=None) -> Plan:
    enc, exp = [], []
    for head, recs in rows:
        _tick, count, selected, exit_site = head[:4]
        o = _selection_row(count, recs)
        o.peek(A_CSTS + 0x28, 4)
        o.peek(SEL_RECIPROCAL_AT, 4)
        o.peek(A_CSTS + 0x34, 20 * 4)    # csts_w13..w32
        enc.append(o.bytes())
        witness = SEL_MARKER if exit_site == 1 else _reciprocal_bits(count)
        exp.append(((selected << 16) | count, witness) + tuple(head[4:24]))
    return Plan("selection-output-v1", SELECT_WINDOW, 22,
                ("find_count|selected", "exit_witness")
                + tuple(f"csts_w{13 + i}" for i in range(20)), enc, exp,
                abi="window 0x0048DBD7..0x0048DDEE: ebx = &csts, [0x019471B0] = the record "
                    "count, the records at 0x0194A274 stride 0x9C; csts+0x28/0x2A the two "
                    "u16 counts and csts+0x34..0x80 the twenty words SA2SLO1 calls "
                    "csts_w13..w32",
                note="`exit_site` is a control-flow column, so it is observed rather than "
                     "inferred: the averaging exit stores float32(1/count) at (entry ESP)+0x1C "
                     "(0x0048DDB4, after the two pops at 0x0048DDA9/AC) and the single-record "
                     "exit never writes that word, so the row reads back either the marker "
                     "the plan seeded or the reciprocal the window itself computed")


# ---------------------------------------------------------------- 0x004974F0 / 0x00497560
# The candidate cache's Z bucketing. Two functions, one arena, all of it read out of
# the disassembly at 0x004974F0, 0x00497560 and the reset at 0x0048BDF0:
#
#   0x0048BDF0  the arena reset. [0x01A5A3F8] is the node free-list head, chained through
#               word 0 of each 0x40-byte node from 0x0194F330 (0x2000 of them, the last
#               with a null link); [0x019EDC00] is 128 (head, tail) bucket pairs.
#   0x004974F0  cdecl of one float32 (`fld dword ptr [esp+4]`). `call 0x008287C0` is the
#               CRT ftol, then `sar ecx,4` / `add ecx,40h` and a clamp to [0, 0x7F]
#               (0x00497501 / 0x0049750A) gives the bucket. The node is popped off the
#               free list (`mov eax,[1A5A3F8h]` / `mov edx,[eax]`; a null link returns 0
#               at 0x0049751C) and pushed on the bucket's head: `[eax] = old head`,
#               `[old head + 4] = eax`, `[bucket] = eax`, `[eax+4] = 0`; on an empty
#               bucket the other arm at 0x00497544 writes the tail word too. eax is the
#               node.
#   0x00497560  scans buckets 0..127 for the first non-empty one (`mov ecx,19EDC00h`,
#               the four-way unrolled loop at 0x00497570), writes its head to [edi+0x10],
#               then links every later non-empty bucket's head onto the previous
#               bucket's tail (`mov [esi],eax` / `mov [eax+4],esi` at 0x004975C7) and
#               writes the last tail to [edi+0x14].
#
# So the flattened chain is bucket-ascending, and LIFO inside a bucket. SA2CCB1's
# `order` section is exactly that chain as generation ordinals, its `head_generation`
# and `tail_generation` its two ends, and `payloads[i][14]` the bucket of record i.
G_FREE_HEAD = 0x01A5A3F8
G_BUCKETS = 0x019EDC00
BUCKET_PAIRS = 128
NODE_ARENA = 0x0194F330
CCB_INSERT = 0x004974F0
CCB_FLATTEN = 0x00497560
A_CCB_GROUP = A_ARGS + 0x230000


def _ccb_node(i: int) -> int:
    return NODE_ARENA + 0x40 * i


def _ccb_free_list(o: Ops, n: int) -> Ops:
    """The reset at 0x0048BE3B..0x0048BE51, for the `n` nodes a row needs: word 0 of
    node k points at node k+1 and node n's is null, so allocation k returns node k."""
    o.poke(G_FREE_HEAD, [NODE_ARENA])
    for k in range(n + 1):
        o.poke(_ccb_node(k), [_ccb_node(k + 1) if k < n else 0])
    return o


def _ccb_insert(o: Ops, delta_bits: int) -> Ops:
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [delta_bits])
    o.call(CCB_INSERT)
    return o


@builder("candidate-cache-builder-v1")
def _ccb1(rows, header, sections=None) -> Plan:
    """SA2CCB1: the bucket a record lands in, and the chain the flattening leaves.

    Two experiments per row, both on a freshly reset arena:

      (a) insert record i alone and read back all 256 bucket words. The expected words
          are node 0 at the pair the fixture records and zero everywhere else, so the
          bucket is *observed*: nothing else the plan does can put a node there. The
          argument is the one 0x0048B610 passes at 0x0048B74F -- `payloads[i][2]`, the
          record's own centre Z, minus the header's `root_center_z`, which is the word
          0x0048B741 reads from [0x01DDE264].
      (b) insert all 72 records in ordinal order, flatten into a scratch group, and read
          back the group's two chain ends and node i's own two links. Across the 72 rows
          that compares every link of SA2CCB1's `order` section, plus its
          `head_generation` and `tail_generation` on every row.

    `payloads[i][0..13]` -- the node body at +8..+0x3C -- is **not** compared here and
    is not written by either of these two functions: the centroid at +8/+0xC/+0x10 is
    stored at 0x0048B8C4..0x0048B8CA, the squared radius at +0x14 at 0x0048B974, the
    three vertices at +0x18..+0x38 at 0x0048B76C..0x0048B7D8 and the material word at
    +0x3C at 0x0048B991 -- all of them in BuildObjectPolygons, after the call at
    0x0048B752 returns the node. They are driven by `basic-strip-decoder-v1`, whose
    boundary is that loop.
    """
    payloads = sections["payloads"]
    order = [r[0] for r in sections["order"]]
    n = len(payloads)
    root_z = struct.unpack("<f", struct.pack("<I", header["root_center_z"]))[0]

    def delta_bits(i: int) -> int:
        cz = struct.unpack("<f", struct.pack("<I", payloads[i][2]))[0]
        return struct.unpack("<I", struct.pack("<f", cz - root_z))[0]

    pos = {g: p for p, g in enumerate(order)}
    enc, exp = [], []
    for i in range(n):
        o = Ops()
        # (a) one record into a fresh arena
        o.fill(G_BUCKETS, BUCKET_PAIRS * 8)
        _ccb_free_list(o, 2)
        _ccb_insert(o, delta_bits(i))
        o.peek(G_BUCKETS, BUCKET_PAIRS * 8)
        # (b) the whole generation, then the flattening
        o.fill(G_BUCKETS, BUCKET_PAIRS * 8)
        o.fill(A_CCB_GROUP, 0x40)
        _ccb_free_list(o, n)
        for k in range(n):
            _ccb_insert(o, delta_bits(k))
        o.reg(ESP, ESP0).sentinel()
        o.reg(EDI, A_CCB_GROUP)
        o.call(CCB_FLATTEN)
        o.peek(A_CCB_GROUP + 0x10, 8)
        o.peek(_ccb_node(i), 8)
        enc.append(o.bytes())

        buckets = [0] * (BUCKET_PAIRS * 2)
        b = payloads[i][14]
        buckets[2 * b] = buckets[2 * b + 1] = _ccb_node(0)
        p = pos[i]
        exp.append(tuple(buckets)
                   + (_ccb_node(header["head_generation"]), _ccb_node(header["tail_generation"]),
                      _ccb_node(order[p + 1]) if p + 1 < n else 0,
                      _ccb_node(order[p - 1]) if p else 0))
    cols = tuple(f"bucket{b}_{w}" for b in range(BUCKET_PAIRS) for w in ("head", "tail"))
    return Plan("candidate-cache-builder-v1", CCB_INSERT, BUCKET_PAIRS * 2 + 4,
                cols + ("chain_head", "chain_tail", "node_next", "node_prev"), enc, exp,
                abi="0x004974F0 is cdecl of one float32 at [esp+4] (0x004974F0 `fld dword "
                    "ptr [esp+4]`) returning the node in eax; 0x00497560 takes the group in "
                    "edi and writes the chain ends to [edi+0x10] and [edi+0x14]. The free "
                    "list head is [0x01A5A3F8] over 0x40-byte nodes from 0x0194F330 and the "
                    "128 bucket pairs are at 0x019EDC00, both as 0x0048BDF0 lays them out",
                note="the bucket is observed, not inferred: the row reads back all 256 bucket "
                     "words after a single insert into a zeroed array, so the fixture's own "
                     "bucket column is the only place node 0 may appear. The node body words "
                     "payloads[i][0..13] are written by BuildObjectPolygons after the insert "
                     "returns (0x0048B76C..0x0048B991), not by either function here; "
                     "basic-strip-decoder-v1 drives them")


# ---------------------------------------------------------------- 0x0048B6C0 window
# BuildObjectPolygons' triangle loop, 0x0048B6C0..0x0048B9EA. What the loop reads and
# writes, instruction by instruction (pre-`push ecx` frame offsets throughout; the
# `push ecx` at 0x0048B6C3 is the float argument slot the `add esp,4` at 0x0048B759
# releases):
#
#   esi          the transformed-vertex cursor. `lea esi,[esp+78h]` at 0x0048B6B9 puts
#                it on the third vertex, and the body reads v0 at [esi-0x18], v1 at
#                [esi-0x0C] and v2 at [esi]; `add esi,0Ch` at 0x0048B99E steps one
#                vertex per triangle, so the buffer is the strip's vertices in strip
#                order, starting at (entry ESP) + 0x60.
#   edi, ebx     the triangle index and the strip's triangle count (`cmp edi,ebx` /
#                `jl 0x0048B6C0` at 0x0048B9A1).
#   [esp+0x44]   the winding base: `mov eax,[esp+44h]` / `lea edx,[edi+eax]` /
#                `test dl,1` at 0x0048B768..0x0048B780 picks the vertex order, which is
#                SA2BSD1's own `(triangle + header >> 15) & 1`.
#   ebp          the NJS_MESHSET. Only [ebp] is read inside the loop, at 0x0048B981,
#                for the material index `(type_matId & 0x3FFF) * 5`.
#   [esp+0x3064] the caller's first argument: [[arg+4]+0x10] is the material array the
#                loop indexes at 0x0048B98D and stores to the node's +0x3C.
#   [0x01DDE264] the root centre Z the centroid is measured against (0x0048B741),
#                SA2BSD1's `root_center_z`.
#   [0x01DDE260] the running maximum Z span, raised at 0x0048B85A, SA2BSD1's
#                `max_z_span`.
#   [esp+0x38], [esp+0x3C]  the strip and meshset counters of the two enclosing loops.
#                Set to 1 so both fall through to the epilogue at 0x0048B9CE, which is
#                inside the lifted window: `pop edi/esi/ebp/ebx`, `add esp,3050h`, `ret`,
#                so the sentinel goes at (entry ESP) + 0x3060.
BSD_WINDOW = 0x0048B6C0
BSD_FRAME = ESP0 - 0x3060        # `add esp,3050h` + four pops before the `ret`
BSD_VERTS = BSD_FRAME + 0x60
A_BSD_MESH = A_ARGS + 0x240000
A_BSD_ARG = A_ARGS + 0x241000
A_BSD_ATTACH = A_ARGS + 0x241100
A_BSD_MATS = A_ARGS + 0x241200
G_ROOT_CENTER_Z = 0x01DDE264
G_MAX_Z_SPAN = 0x01DDE260


def _bsd_strip(o: Ops, verts, strip, material: int) -> Ops:
    """One call of the window: the strip's vertices in the frame, then its triangles."""
    head, indices = strip
    words: list[int] = []
    for j in indices:
        words += list(verts[j])
    o.fill(BSD_FRAME - 0x40, 0x3100)
    o.poke(BSD_VERTS, words)
    o.poke(BSD_FRAME + 0x38, [1])                 # the strip counter: one pass
    o.poke(BSD_FRAME + 0x3C, [1])                 # the meshset counter: one pass
    o.poke(BSD_FRAME + 0x44, [head >> 15])        # the winding base
    o.poke(BSD_FRAME + 0x3064, [A_BSD_ARG])
    o.poke(A_BSD_MESH, [0xC000])                  # type 3, material id 0
    o.poke(A_BSD_ARG + 4, [A_BSD_ATTACH])
    o.poke(A_BSD_ATTACH + 0x10, [A_BSD_MATS])
    o.poke(A_BSD_MATS + 0x10, [material])         # (0 * 5) * 4 + 0x10
    o.reg(ESP, ESP0).sentinel()
    o.reg(ESP, BSD_FRAME)
    # 0x0048B6C3 `push ecx` reads ECX before anything writes it, so ECX is a live-in of
    # this window -- the CFG backward-liveness census the runtime fold unit added
    # (private/generated/s2/corpus_run/livein/window_0048b6c0.livein.json) is what found
    # it, and no plan set it. The push is the compiler's one-dword frame allocation and
    # the value it pushes is never read back, so this drives 0xDEADBEEF rather than a
    # plausible zero: if any path did read the slot, the row would be conspicuously wrong
    # instead of quietly right. `pushed-ecx-is-dead` in controls.py drives another value
    # and measures it inert.
    o.reg(ECX, POISON)
    o.reg(ESI, BSD_FRAME + 0x78)
    o.reg(EDI, 0)
    o.reg(EBX, max(0, min(head & 0x7FFF, 0x400) - 2))
    o.reg(EBP, A_BSD_MESH)
    o.call(BSD_WINDOW)
    return o


@builder("basic-strip-decoder-v1")
def _bsd1(rows, header, sections=None) -> Plan:
    """SA2BSD1: the Basic strips, expanded into cache records and flattened.

    One row per triangle. Each row resets the cache arena (0x0048BDF0's layout), runs
    the window once per strip -- 72 triangles in generation order -- then calls the
    flattening at 0x00497560, and reads back:

      * the node body of *this* row's triangle, +8..+0x3C, which is the whole of
        SA2BSD1's `record_w2..w15`: centroid (0x0048B8C4), squared radius (0x0048B974),
        the three vertices in winding order (0x0048B76C..0x0048B7D8) and the material
        word (0x0048B991);
      * the flattened chain's two ends and this node's own two links, which is
        `head_generation`, `tail_generation` and `order`;
      * the running maximum Z span at [0x01DDE260], seeded to +0.0 by the row because
        the native reset does the same (0x0048BE1A `fld dword ptr ds:[8AF004h]` is the
        node radius, not this word) and every span in the corpus is positive.

    The per-record `bucket` column is not read back directly: it is what decides the
    chain, and the chain is compared link for link across the 72 rows. The material
    word is an input the fixture records (`strips`) placed in the shipped material array
    the loop indexes at 0x0048B98D, so the store at 0x0048B991 is observed and the value
    is the fixture's own.
    """
    verts = sections["vertices"]
    strips = sections["strips"]
    flags = sections["flags"]
    payloads = sections["payloads"]
    order = sections["order"]
    n = len(payloads)
    pos = {g: p for p, g in enumerate(order)}

    enc, exp = [], []
    for t in range(n):
        o = Ops()
        reset_arenas(o)
        o.fill(G_BUCKETS, BUCKET_PAIRS * 8)
        o.fill(A_CCB_GROUP, 0x40)
        _ccb_free_list(o, n)
        o.poke(G_ROOT_CENTER_Z, [header["root_center_z"]])
        o.poke(G_MAX_Z_SPAN, [0])
        for s, strip in enumerate(strips):
            _bsd_strip(o, verts, strip, flags[s])
        o.reg(ESP, ESP0).sentinel()
        o.reg(EDI, A_CCB_GROUP)
        o.call(CCB_FLATTEN)
        o.peek(_ccb_node(t) + 8, 14 * 4)
        o.peek(A_CCB_GROUP + 0x10, 8)
        o.peek(_ccb_node(t), 8)
        o.peek(G_MAX_Z_SPAN, 4)
        enc.append(o.bytes())
        p = pos[t]
        exp.append(tuple(payloads[t][:14])
                   + (_ccb_node(header["head_generation"]), _ccb_node(header["tail_generation"]),
                      _ccb_node(order[p + 1]) if p + 1 < n else 0,
                      _ccb_node(order[p - 1]) if p else 0,
                      header["max_z_span"]))
    return Plan("basic-strip-decoder-v1", BSD_WINDOW, 19,
                tuple(f"record_w{2 + i}" for i in range(14))
                + ("chain_head", "chain_tail", "node_next", "node_prev", "max_z_span"),
                enc, exp,
                abi="window 0x0048B6C0..0x0048B9EA inside BuildObjectPolygons 0x0048B610: "
                    "esi = &vertex[2] of the strip's transformed vertices at (entry "
                    "ESP)+0x60, edi = 0, ebx = the strip's triangle count, ebp = the "
                    "NJS_MESHSET, (entry ESP)+0x44 = the winding base, +0x38 and +0x3C = 1 "
                    "so both enclosing loops fall through to the epilogue, +0x3064 = the "
                    "caller's argument for the material array; the sentinel at (entry "
                    "ESP)+0x3060, which the `add esp,3050h` after four pops returns to",
                note="the half of SA2BSD1 that is NOT driven here is the vertex transform "
                     "0x0048B510: it dereferences the NJS_MODEL's raw point array "
                     "(0x0048B559 `mov ebp,[ebp]`) and the ninja matrix stack top "
                     "(0x0048B543 `mov eax,[1A557FCh]`), neither of which SA2BSD1 records "
                     "-- the fixture's 52 vertices are already transformed, which is why "
                     "they are this window's input")


# ---------------------------------------------------------------- the composition chain
# SA2ECC1..4 are one corpus at four input depths, and all four end at the same two
# guest calls. What each row does, and where every address comes from:
#
#   the builder   the 72 cache records are *built*, never copied: none of the four
#                 formats records a node body, and the first instruction that consumes
#                 one is 0x0048BBBA `fld dword ptr [edx+10h]`. v1 hands the triangle
#                 loop its own 72 input triples, v2 the strips over 52 transformed
#                 vertices, v3 and v4 the 53 source points through TransformMeshVertices
#                 0x0048B510 (`mov eax,ds:[1A557FCh]` at 0x0048B5CA, then
#                 `call 00426CC0h` per index), v4 building the matrix first with
#                 BuildObjectTreePolygons 0x0048B9F0.
#   the group     0x00497560 with edi = the group writes the flattened chain ends to
#                 [edi+0x10] and [edi+0x14]; the caller then stores the running Z span
#                 into the tolerance: 0x0048C1FD `call 00497560h`,
#                 0x0048C202 `fld dword ptr [1DDE260h]`, 0x0048C20C `fstp dword ptr
#                 [esi+28h]` -- so [group+0x28] is the fixture's own `max_z_span`, and
#                 the row compares [0x01DDE260] against that word as well.
#   0x0048BAF0    the enumeration, exactly as candidate-enumeration-v1 drives it: the
#                 near-set record is a gate on the group id (0x0048BB80 `cmp [edx],esi`),
#                 the accepted node pointers go to [group+0x1C] upward
#                 (0x0048BCAE/0x0048BD8D) and the count to [group+0x24] and eax.
#   0x0048CE40    the contact pass over what the enumeration selected. Its argument is
#                 the same seven-word query: `mov ebx,[ebp+8]` at 0x0048CE4D, then
#                 `fld dword ptr [ebx]` (the radius) at 0x0048CE71 and
#                 `lea eax,[ebx+10h]` / `call 0077FC30h` (njScalor of the motion) at
#                 0x0048CE53/0x0048CE65; eax on entry is the group id matched at
#                 0x0048CE86 `cmp [eax+8],esi`. It zeroes the record count at
#                 0x0048CE57, walks [group+0x1C] for [group+0x24] faces from the head
#                 at 0x0048CEE7, and falls into the selection window at 0x0048DBD7,
#                 which writes the twenty result words at (csts)+0x34 -- the same
#                 twenty `selection-output-v1` already drives.
ECC_ENUM = 0x0048BAF0
ECC_CONTACT = 0x0048CE40
ECC_TRANSFORM = 0x0048B510
ECC_OBJECT_MATRIX = 0x0048B9F0
A_ECC_CSTS = A_ARGS + 0x250000
A_ECC_FACE = A_ARGS + 0x250400
A_ECC_POINTS = A_ARGS + 0x251000
A_ECC_POINTS_PTR = A_ARGS + 0x250800
A_ECC_STRIP = A_ARGS + 0x252000
A_ECC_MATRIX = A_ARGS + 0x250900
ECC_ROOT_CENTER_Z = 0xC4D12000   # SA2ECC1/SA2ECC2's own header word; v3 and v4 drop it


def _ecc_csts(o: Ops, at: int, query) -> Ops:
    """The seven query words where 0x0048CE40 reads them: radius at [ebx], point at
    [ebx+4], motion at [ebx+0x10] (`lea eax,[ebx+10h]` into njScalor at 0x0048CE65)."""
    o.fill(at, 0x100)
    o.poke(at, [query[0]])
    o.poke(at + 4, list(query[1:4]))
    o.poke(at + 0x10, list(query[4:7]))
    return o


def _ecc_contact(o: Ops, at: int, query) -> Ops:
    _ecc_csts(o, at, query)
    o.fill(G_RECORDS, RECORD_STRIDE * 8)
    o.fill(G_RECORDS_PUBLIC, 0x58 * 8)
    o.poke(G_RECORD_COUNT, [0])
    matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [at])
    o.reg(EAX, GROUP_ID)
    o.call(ECC_CONTACT)
    return o


def _ecc_triangle(o: Ops, verts, swap: int, material: int) -> Ops:
    """One triangle through the cache builder's loop: SA2ECC1's own input record."""
    o.fill(BSD_FRAME - 0x40, 0x3100)
    o.poke(BSD_VERTS, list(verts))
    o.poke(BSD_FRAME + 0x38, [1])
    o.poke(BSD_FRAME + 0x3C, [1])
    o.poke(BSD_FRAME + 0x44, [swap])
    o.poke(BSD_FRAME + 0x3064, [A_BSD_ARG])
    o.poke(A_BSD_MESH, [0xC000])
    o.poke(A_BSD_ARG + 4, [A_BSD_ATTACH])
    o.poke(A_BSD_ATTACH + 0x10, [A_BSD_MATS])
    o.poke(A_BSD_MATS + 0x10, [material])
    o.reg(ESP, ESP0).sentinel()
    o.reg(ESP, BSD_FRAME)
    # 0x0048B6C3 `push ecx` reads ECX before anything writes it, so ECX is a live-in of
    # this window -- the CFG backward-liveness census the runtime fold unit added
    # (private/generated/s2/corpus_run/livein/window_0048b6c0.livein.json) is what found
    # it, and no plan set it. The push is the compiler's one-dword frame allocation and
    # the value it pushes is never read back, so this drives 0xDEADBEEF rather than a
    # plausible zero: if any path did read the slot, the row would be conspicuously wrong
    # instead of quietly right. `pushed-ecx-is-dead` in controls.py drives another value
    # and measures it inert.
    o.reg(ECX, POISON)
    o.reg(ESI, BSD_FRAME + 0x78)
    o.reg(EDI, 0)
    o.reg(EBX, 1)
    o.reg(EBP, A_BSD_MESH)
    o.call(BSD_WINDOW)
    return o


def _ecc_transform_strip(o: Ops, strip, points) -> Ops:
    """TransformMeshVertices 0x0048B510 over one strip, into the triangle loop's own
    vertex buffer. The control block is where BuildObjectPolygons puts it, at (window
    entry ESP)+0x50: `lea esi,[esp+50h]` at 0x0048B69B and `lea edx,[esi+10h]` at
    0x0048B5C3 make the output buffer (entry ESP)+0x60, which is where the loop's
    `lea esi,[esp+78h]` then reads from. eax is the matrix-stack top the routine loads
    at 0x0048B5CA and hands to njCalcPoint."""
    head, indices = strip
    o.poke(A_ECC_POINTS, [w for p in points for w in p])
    o.poke(A_ECC_POINTS_PTR, [A_ECC_POINTS])
    o.poke(A_ECC_STRIP, _pack_u16([head] + list(indices)))
    o.fill(BSD_FRAME - 0x40, 0x3100)
    o.poke(BSD_FRAME + 0x50, [0xC000])            # NJD_MESHSET_3
    o.poke(BSD_FRAME + 0x58, [A_ECC_STRIP])       # the u16 strip stream
    o.poke(BSD_FRAME + 0x5C, [A_ECC_POINTS_PTR])  # `mov ebp,[esi+0Ch]` / `mov ebp,[ebp]`
    o.reg(ESP, ESP0).sentinel()
    o.reg(ESI, BSD_FRAME + 0x50)
    o.call(ECC_TRANSFORM)
    o.emit_reg(EAX)                               # the initial winding flip
    return o


def _pack_u16(values) -> list[int]:
    out = []
    for i in range(0, len(values), 2):
        lo = values[i] & 0xFFFF
        hi = values[i + 1] & 0xFFFF if i + 1 < len(values) else 0
        out.append(lo | (hi << 16))
    return out


def _ecc_window(o: Ops, head: int, material: int) -> Ops:
    """The triangle loop over the vertices already in the frame buffer."""
    o.poke(BSD_FRAME + 0x38, [1])
    o.poke(BSD_FRAME + 0x3C, [1])
    o.poke(BSD_FRAME + 0x44, [head >> 15])
    o.poke(BSD_FRAME + 0x3064, [A_BSD_ARG])
    o.poke(A_BSD_MESH, [0xC000])
    o.poke(A_BSD_ARG + 4, [A_BSD_ATTACH])
    o.poke(A_BSD_ATTACH + 0x10, [A_BSD_MATS])
    o.poke(A_BSD_MATS + 0x10, [material])
    o.reg(ESP, ESP0).sentinel()
    o.reg(ESP, BSD_FRAME)
    # 0x0048B6C3 `push ecx` reads ECX before anything writes it, so ECX is a live-in of
    # this window -- the CFG backward-liveness census the runtime fold unit added
    # (private/generated/s2/corpus_run/livein/window_0048b6c0.livein.json) is what found
    # it, and no plan set it. The push is the compiler's one-dword frame allocation and
    # the value it pushes is never read back, so this drives 0xDEADBEEF rather than a
    # plausible zero: if any path did read the slot, the row would be conspicuously wrong
    # instead of quietly right. `pushed-ecx-is-dead` in controls.py drives another value
    # and measures it inert.
    o.reg(ECX, POISON)
    o.reg(ESI, BSD_FRAME + 0x78)
    o.reg(EDI, 0)
    o.reg(EBX, max(0, min(head & 0x7FFF, 0x400) - 2))
    o.reg(EBP, A_BSD_MESH)
    o.call(BSD_WINDOW)
    return o


def _ecc_front_end(o: Ops, magic: str, header: dict, secs: dict) -> list[str]:
    """Build the 72 cache records from whatever depth this version records.
    Returns the extra output column names the front end emitted."""
    if magic == "SA2ECC1":
        for rec in secs["builder_inputs"]:
            _ecc_triangle(o, rec[3:12], rec[12], rec[13])
        return []
    if magic == "SA2ECC2":
        verts = secs["vertices"]
        for s, strip in enumerate(secs["strips"]):
            _bsd_strip(o, verts, strip, secs["flags"][s])
        return []
    # v3 and v4: the vertices are produced, not recorded.
    if magic == "SA2ECC4":
        # BuildObjectTreePolygons pushes the object-local matrix as frame 1 and pops it
        # before returning (harness/fixture_marshal.c FN_OBJECT_TREE, and the plan
        # ninja-object-matrix-v1 drives), so the matrix is left at A_MSTACK+0x30 and the
        # stack top is pointed back at it for the transform. The parent is the identity:
        # SA2ECC4 records nine transform words and no parent, and the object is the
        # collision root -- ninja-object-matrix-v1's case 0, whose parent is the identity
        # and whose expected matrix is SA2ECC3's matrix word for word.
        o.poke(A_MSTACK, [0x3F800000, 0, 0, 0, 0, 0x3F800000, 0, 0, 0, 0, 0x3F800000, 0])
        o.poke(A_OBJECT + 8, list(secs["transform"]))
        o.poke(A_OBJECT + 4, [A_OBJECT + 0x100])
        matrix_stack(o, 0, A_MSTACK_FRAMES - 1)
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [A_OBJECT, 0, 0])
        o.call(ECC_OBJECT_MATRIX)
        o.peek(A_MSTACK + 0x30, 48)
        o.poke(G_MATRIX_STACK_TOP, [A_MSTACK + 0x30])
        cols = [f"matrix_m{i}" for i in range(12)]
    else:
        o.poke(A_ECC_MATRIX, list(secs["matrix"]))
        o.poke(G_MATRIX_STACK_TOP, [A_ECC_MATRIX])
        cols = []
    o.poke(G_MATRIX_STACK_LIMIT, [A_MSTACK + 0x30 * (A_MSTACK_FRAMES - 1)])
    o.poke(G_MATRIX_STACK_FLOOR, [A_MSTACK])
    for s, strip in enumerate(secs["strips"]):
        _ecc_transform_strip(o, strip, secs["points"])
        cols.append(f"strip{s}_flip")
        _ecc_window(o, strip[0], secs["flags"][s])
    return cols


def _ecc_plan(fixture: str, magic: str, queries, header, secs) -> Plan:
    max_sel = max(q[0][9] for q in queries)
    root_z = header.get("root_center_z", ECC_ROOT_CENTER_Z)
    enc, exp, front_cols = [], [], []
    for head, gens, accepts, result in queries:
        query = head[1:8]
        o = Ops()
        reset_arenas(o)
        o.fill(G_BUCKETS, BUCKET_PAIRS * 8)
        o.fill(A_CCB_GROUP, 0x40)
        _ccb_free_list(o, header["records"])
        o.poke(G_ROOT_CENTER_Z, [root_z])
        o.poke(G_MAX_Z_SPAN, [0])
        front_cols = _ecc_front_end(o, magic, header, secs)
        o.reg(ESP, ESP0).sentinel()
        o.reg(EDI, A_CCB_GROUP)
        o.call(CCB_FLATTEN)
        o.peek(G_MAX_Z_SPAN, 4)
        # the group, as the builder's caller leaves it at 0x0048C1FD..0x0048C20C
        o.poke(A_CCB_GROUP, [0])
        o.poke(A_CCB_GROUP + 8, [GROUP_ID])
        o.poke(A_CCB_GROUP + 0x1C, [0])
        o.poke(A_CCB_GROUP + 0x24, [0])
        o.poke(A_CCB_GROUP + 0x28, [header["max_z_span"]])
        o.poke(G_GROUP_LIST, [A_CCB_GROUP])
        o.poke(G_ADMIT_GATE, [1])
        o.poke(G_ADMIT_DESC, [A_DESC])
        o.poke(G_FIRST_COUNT, [0])
        o.poke(G_FIRST_BASE, [A_DESC])
        matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
        o.fill(G_SELECTED_LIST, 4 * (max_sel + 4))
        o.fill(A_BIG_NEAR, 0x100)
        o.poke(A_QUERY, list(query))
        o.fill(A_CSE_COL, COL_STRIDE)
        o.poke(A_CSE_COL, [query[1], query[2], query[3]])
        o.poke(A_CSE_COL + 0x0C, [FLT_MAX_BITS])
        o.poke(A_CSE_COL + 0x10, [GROUP_ID])
        o.poke(A_CSE_COL + 0x1C, [1])
        o.poke(G_COL_TABLE, [A_CSE_COL])
        o.poke(G_COL_COUNT, [1])
        o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
        o.poke(G_NEAR_COUNT, [0])
        o.reg(ESP, ESP0).sentinel()
        o.poke(ESP0 + 4, [A_QUERY])
        o.call(ECC_ENUM)
        o.emit_reg(EAX)
        o.peek(G_SELECTED_COUNT, 4)
        o.peek(A_CCB_GROUP + 0x24, 4)
        o.peek(G_SELECTED_LIST, 4 * max_sel)
        # the contact pass over everything the enumeration selected
        _ecc_contact(o, A_ECC_CSTS, query)
        o.peek(G_RECORD_COUNT, 4)
        o.peek(A_ECC_CSTS + 0x34, 80)
        # and once per selected face on its own, which is how the per-face acceptance
        # bit is observed: the loop at 0x0048CEE7 walks [group+0x1C] for [group+0x24]
        # faces, so a one-face list makes [0x019471B0] that face's own bit.
        for i in range(max_sel):
            if i < head[9]:
                o.poke(A_ECC_FACE, [_ccb_node(gens[i])])
                o.poke(A_CCB_GROUP + 0x1C, [A_ECC_FACE])
                o.poke(A_CCB_GROUP + 0x24, [1])
                _ecc_contact(o, A_ECC_CSTS + 0x200, query)
                o.peek(G_RECORD_COUNT, 4)
            else:
                o.emit_word(0)
        enc.append(o.bytes())
        exp.append(tuple(_ecc_front_expected(magic, header, secs))
                   + (header["max_z_span"], head[9], head[9], head[9])
                   + tuple(_ccb_node(g) for g in gens) + (0,) * (max_sel - len(gens))
                   + (head[14],) + tuple(result)
                   + tuple(accepts) + (0,) * (max_sel - len(accepts)))
    cols = (tuple(front_cols) + ("max_z_span", "selected_count", "count_mirror",
                                 "group_counter")
            + tuple(f"node{i}" for i in range(max_sel))
            + ("accepted",) + tuple(f"result_w{i}" for i in range(20))
            + tuple(f"accept{i}" for i in range(max_sel)))
    return Plan(fixture, ECC_ENUM, len(cols), cols, enc, exp,
                abi="0x0048BAF0 [esp+4] = &query[7]; 0x0048CE40 eax = the group id, "
                    "[esp+4] = the same query block (radius at [ebx], point at [ebx+4], "
                    "motion at [ebx+0x10]); 0x00497560 edi = the group; the builder "
                    "window 0x0048B6C0 and, for v3/v4, 0x0048B510 with its control block "
                    "at (window entry ESP)+0x50 and 0x0048B9F0 for the object matrix",
                note="the chain is built, never copied: no SA2ECC format records a node "
                     "body. `expanded`, `direction` and `visited` are not compared -- "
                     "`expanded` is the frame word at (entry ESP)-0x0C that the walk "
                     "overwrites with the Z upper bound at 0x0048BBB6 "
                     "(`fstp dword ptr [esp+14h]`), and `direction` and `visited` are the "
                     "stager's own, as SA2CSE1's authority note says. v3 and v4 do not "
                     "record `root_center_z`, so 0x0048B741's [0x01DDE264] is supplied "
                     "from v1/v2's header word 0xC4D12000; the chain order it decides is "
                     "compared in full through the selected ordinals.")


def _ecc_front_expected(magic: str, header: dict, secs: dict) -> list[int]:
    if magic == "SA2ECC1" or magic == "SA2ECC2":
        return []
    out: list[int] = []
    if magic == "SA2ECC4":
        out += _ecc4_matrix()
    for strip in secs["strips"]:
        out.append(strip[0] >> 15)
    return out


def _ecc4_matrix() -> list[int]:
    """SA2ECC4 records nine transform words and no matrix, so the oracle for its matrix
    stage is read from the two fixtures that do record it and agree word for word:
    SA2ECC3's `matrix` section, and ninja-object-matrix-v1 case 0's `expected` -- whose
    `parent` is the identity and whose transform words are SA2ECC4's own. The registry's
    authority note for SA2NOM1 says the same thing from the other side: "case 0's parent
    is captured and its expected matrix is pinned to the captured matrix in SA2ECC3".
    """
    import fixture_rows as _FR
    _h, _rows, secs = _FR.load("candidate-cache-point-composition-v3")
    matrix = list(secs["matrix"])
    hn, sn = _FR.sections(_FR.find("ninja-point-transform-v1"))
    if list(sn["matrix"][0]) != matrix:
        raise SystemExit("SA2ECC3's matrix and ninja-point-transform-v1's disagree")
    return matrix


@builder("candidate-cache-composition-v1")
def _ecc1(rows, header, sections=None) -> Plan:
    return _ecc_plan("candidate-cache-composition-v1", "SA2ECC1", rows, header, sections)


@builder("candidate-cache-strip-composition-v2")
def _ecc2(rows, header, sections=None) -> Plan:
    return _ecc_plan("candidate-cache-strip-composition-v2", "SA2ECC2", rows, header, sections)


@builder("candidate-cache-point-composition-v3")
def _ecc3(rows, header, sections=None) -> Plan:
    return _ecc_plan("candidate-cache-point-composition-v3", "SA2ECC3", rows, header, sections)


@builder("candidate-cache-object-matrix-composition-v4")
def _ecc4(rows, header, sections=None) -> Plan:
    return _ecc_plan("candidate-cache-object-matrix-composition-v4", "SA2ECC4", rows,
                     header, sections)


# ---------------------------------------------------------------- 0x0048DA51 window
# The per-accept record assembly inside CL_ColPolCheckTouchRe, 0x0048DA51..0x0048DDEE.
# SA2CRI1's 58-word row is `frame_tick, matrix[12], origin[3], sweep[3],
# effective_radius, face[16], record[22]`, and its own stager says where it read each
# one: "the last per-accept transform call returns to 0x0048DB6D, where ESI holds the
# face block ... and the assembled record sits at [ESP+0xA0], with the plane-local
# origin and the sweep still at [ESP+0x60] and [ESP+0x40]". Those are frame words of
# this window, so this is the boundary the fixture's own columns define:
#
#   [esp+0x60..0x68]  the plane-local origin. 0x0048DA51 `fld dword ptr [esp+64h]`
#                     (still inside the `push ecx` of 0x0048DA4B, so (entry ESP)+0x60)
#                     and 0x0048DA7A `fld dword ptr [esp+68h]` feed sites 4 and 5.
#   [esp+0x40..0x48]  the plane-local sweep, read at 0x0048DADD..0x0048DB42 for sites
#                     6 and 7.
#   [esp+0x18]        the effective radius, 0x0048DAC6 `fld dword ptr [esp+18h]`.
#   ebx               the face cursor: 0x0048DA8A `mov esi,[ebx]` takes the face, whose
#                     vertex 0 is at +0x18 and whose flags are at +0x3C; 0x0048DBC1
#                     `add ebx,4` steps it.
#   [0x01A557FC]      the per-accept matrix. 0x00427040 built it just before this
#                     window and every `call 00426D50h` reads it from there
#                     (0x00426D53 `mov eax,ds:[1A557FCh]`), so the fixture's twelve
#                     matrix words go straight to the stack top.
#   [esp+0x30]        the record cursor, 0x0194A274; 0x0048DB95 `lea esi,[esp+0A0h]` /
#                     `rep movsd` of 0x16 words copies the staged record there and
#                     0x0048DB9E raises [0x019471B0].
#   [esp+0x4C]        the remaining face count. One face per row, so the loop leaves at
#                     0x0048DBCE and falls into the selection window at 0x0048DBD7,
#                     whose `pop edi/esi/ebx; mov esp,ebp; pop ebp; ret` ends the window
#                     -- the sentinel goes at EBP+4, as selection-output-v1's does.
#
# Record words 0..7 are **supplied, not compared**, and the instructions that write
# them are all before this boundary: word 0 at 0x0048DA09 `mov [esp+0A0h],eax` (or the
# two constant stores at 0x0048DA1A and 0x0048DA2F), word 1 at 0x0048D9EA
# `mov [esp+0A4h],eax` (or 0x0048DA3C), words 2..4 at 0x0048D820/0x0048D827/0x0048D82E
# and words 5..7 at 0x0048D8E0/0x0048D8EB/0x0048D8F4 (or 0x0048D90B..0x0048D919). The
# earliest boundary that would produce them is 0x0048D920, and it cannot be entered
# cold: 0x0048D978 `fcom st(5)` reads an x87 register pushed before it. The earliest
# that would produce the normal is the per-face head 0x0048CEE7, which reads the *world*
# origin and sweep -- and SA2CRI1 records the plane-local ones, which is the whole
# reason its boundary is here.
CRI_WINDOW = 0x0048DA51
CRI_EBP = ESP0 - 4                  # `mov esp,ebp; pop ebp; ret` takes [EBP+4]
CRI_FRAME = ESP0 - 0x400            # the window's own frame, before the `push ecx`
A_CRI_FACE = A_ARGS + 0x260000
A_CRI_FACE_PTR = A_ARGS + 0x260100
A_CRI_MATRIX = A_ARGS + 0x260200
A_CRI_CSTS = A_ARGS + 0x260400
G_CONTACT_SCRATCH = 0x01DDA46C      # `mov edi,1DDA46Ch` at 0x0048CED4


@builder("compact-contact-corpus-v1")
def _cri1(rows, header, sections=None) -> Plan:
    """SA2CRI1: one row per accepted contact, 2990 of them.

    Compared: record words 8..19, the twelve this window produces through the four
    transform calls at 0x0048DA85, 0x0048DAD1, 0x0048DB46 and 0x0048DB68 and the adds
    against the face's vertex 0 at 0x0048DA93/0x0048DAA4/0x0048DABC and
    0x0048DAE1/0x0048DAFB/0x0048DB22; and the record count [0x019471B0] the copy at
    0x0048DB9E raises, which is how the row proves the record was written at all.

    Ten of the twenty-two are supplied instead, each with the instruction that writes
    it: words 0..7 in the `note` below, and words 20 and 21 at 0x0048D7B8
    `fst dword ptr [esp+0F0h]` and 0x0048D7D4 `fstp dword ptr [esp+0F4h]` -- the block
    that writes them, 0x0048D7AF onward, also pops the ninja matrix stack
    (0x0048D7A7 `mov ds:[1A557FCh],esi`) and runs straight into the deep-x87 branch
    region at 0x0048D835, so it cannot be entered cold either.
    """
    enc, exp = [], []
    for r in rows:
        matrix = list(r[1:13])
        origin = list(r[13:16])
        sweep = list(r[16:19])
        radius = r[19]
        face = list(r[20:36])
        record = list(r[36:58])
        o = Ops()
        reset_arenas(o)
        o.fill(CRI_FRAME - 0x100, 0x600)
        o.fill(G_RECORDS, RECORD_STRIDE * 4)
        o.fill(G_RECORDS - 0x40, 0x40)
        o.fill(A_CRI_CSTS, 0x100)
        o.poke(G_RECORD_COUNT, [0])
        o.poke(A_CRI_FACE, face)
        o.poke(A_CRI_FACE_PTR, [A_CRI_FACE])
        o.poke(A_CRI_MATRIX, matrix)
        o.poke(G_MATRIX_STACK_TOP, [A_CRI_MATRIX])
        o.poke(G_MATRIX_STACK_LIMIT, [A_MSTACK + 0x30 * (A_MSTACK_FRAMES - 1)])
        o.poke(G_MATRIX_STACK_FLOOR, [A_MSTACK])
        o.poke(CRI_FRAME + 0x18, [radius])
        o.poke(CRI_FRAME + 0x20, [A_CRI_FACE_PTR])
        o.poke(CRI_FRAME + 0x30, [G_RECORDS])
        o.poke(CRI_FRAME + 0x40, sweep)
        o.poke(CRI_FRAME + 0x4C, [1])
        o.poke(CRI_FRAME + 0x5C, [G_CONTACT_SCRATCH])
        o.poke(CRI_FRAME + 0x60, origin)
        o.poke(CRI_FRAME + 0xA0, record[:8])     # the prefix this window does not write
        o.poke(CRI_FRAME + 0xF0, record[20:22])  # and the two words written at 0x0048D7B8/D4
        o.reg(ESP, ESP0).sentinel()
        o.reg(EBP, CRI_EBP)
        o.poke(CRI_EBP + 8, [A_CRI_CSTS])        # `mov ebx,[ebp+8]` at 0x0048DBD4
        o.reg(ESP, CRI_FRAME - 4)                # inside the `push ecx` of 0x0048DA4B
        o.reg(EBX, A_CRI_FACE_PTR)
        o.call(CRI_WINDOW)
        o.peek(G_RECORDS + 32, 12 * 4)
        o.peek(G_RECORD_COUNT, 4)
        enc.append(o.bytes())
        exp.append(tuple(record[8:20]) + (1,))
    return Plan("compact-contact-corpus-v1", CRI_WINDOW, 13,
                tuple(f"record_w{8 + i}" for i in range(12)) + ("record_count",), enc, exp,
                abi="window 0x0048DA51..0x0048DDEE inside CL_ColPolCheckTouchRe 0x0048CE40: "
                    "ESP = (frame)-4 because the window starts inside the `push ecx` of "
                    "0x0048DA4B, EBX = the face cursor, EBP+4 = the sentinel and EBP+8 the "
                    "csts the selection tail reads; the plane-local origin at (frame)+0x60, "
                    "the sweep at +0x40, the effective radius at +0x18, the record cursor "
                    "at +0x30, the remaining face count at +0x4C, and the per-accept matrix "
                    "on the ninja matrix stack top at [0x01A557FC]",
                note="record words 0..7 are supplied, not compared: every instruction that "
                     "writes one is before this boundary (0x0048D820/27/2E, "
                     "0x0048D8E0/EB/F4 or 0x0048D90B/12/19, 0x0048D9EA or 0x0048DA3C, and "
                     "0x0048DA09 or 0x0048DA1A/0x0048DA2F). SA2CRI1 records the "
                     "*plane-local* origin and sweep, which the per-face body computes from "
                     "the world query it does not record, so the earlier boundary "
                     "0x0048CEE7 the survey named cannot be fed from this fixture; and "
                     "0x0048D920, the first point that would produce words 0 and 1, reads "
                     "an x87 register pushed before it (0x0048D978 `fcom st(5)`).")


# ---------------------------------------------------------------- 0x00461F10 itself
# GroundCollisionStep's own entry. The registry called this undrivable because "nothing
# in the format carries what the instruction after the selector call reads: the gather's
# u16 count at 0x004620CA `mov dx,[1DE9484h]` and its list base at 0x00462133
# `mov edi,[1A5A2DCh]`". Neither is a dropped field. 0x004620CA reads a *guest global*
# that the corpus already pokes -- `ground-admission-source-v1..v4` set it by name
# (`G_NEAR_COUNT`) -- and it is the gate of a branch:
#
#   0x004620c5  call 0048BAF0h        ; the selector
#   0x004620ca  mov dx,ds:[1DE9484h]  ; the count
#   0x004620d4  test dx,dx
#   0x004620d7  jne short 00462133h   ; the candidate walk the fixture does not describe
#   0x004620d9  ...                   ; the empty-set tail, to the `ret` at 0x00462132
#
# 0x0047CD60 zeroes that count itself at its own entry (0x0047CD73 `mov [1DE9484h],ax`
# with eax = 0) and raises it once per admitted COL record; 0x0048BAF0 calls it at
# 0x0048BB41. So an empty COL table -- [0x01A5A2C8] = [0x01A5A2CC] = 0, which is what
# the admission family's own plan already writes for its first loop -- admits nothing,
# the count is zero, and the routine walks its own empty-set tail to a real `ret`. The
# window the fixture records *is* the whole call.
#
# ABI, read out of the prologue: cdecl(entity, world, player), and
# 0x00461F23 `mov ebx,[esi+90h]` takes the 33-word collision work buffer out of player
# byte 0x90 -- word 36, which is not in SA2GQS3's player schema because it is a pointer
# the driver owns. The twelve work words this step writes, each with its instruction:
#
#   w7  0x00461F60 fst [ecx]       (ecx = ebx+0x1C)   w8  0x00461F68 fstp [ebx+20h]
#   w9  0x00461F59 fst [ebx+24h]                      w13 0x00461F6E mov [ebx+34h],edx
#   w14 0x00461F78 mov [ebx+38h],eax                  w1  0x0046204D fstp [edx] (ebx+4)
#   w2  0x0046205D fstp [ebx+8]                       w3  0x0046206E fstp [ebx+0Ch]
#   w0  0x0046207D fstp [ebx]                         w4  0x00462081/0x00462094
#   w5  0x00462086/0x0046209C                         w6  0x0046208C/0x004620A5
#
# and the seven-word query block 0x0048BAF0 takes is [ebx] itself (0x0046204C `push ebx`),
# so SA2GQS3's `query_w0..6` and `tangent[3]` are work words 0..6 and 7..9 -- the same
# words, recorded twice. The other twenty-one work words enter the call and are not
# touched by it; the plan poisons the whole buffer with 0xDEADBEEF and writes back only
# those twenty-one, so every compared word that the step builds is compared against a
# slot the step had to write.
G_GROUP_LIST = 0x01A5A400
A_GWORK = A_ARGS + 0x1400
A_GWORLD = A_ARGS + 0x600
GQS_BUILT = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 13, 14)
POISON = 0xDEADBEEF


def _gqs_globals() -> bytes:
    """The guest world outside the player: an empty COL table and no candidate groups.

    Every address here is one another driven fixture already pokes by name. The COL
    table is `ground-admission-source-*`'s (`G_COL_COUNT`/`G_COL_TABLE`,
    `G_FIRST_COUNT`/`G_FIRST_BASE`), the near-set cursor and count are
    `ground-funnel-chain-v1`'s, and the group-list head is `candidate-enumeration-v1`'s
    -- which pokes it non-null to walk a chain. Zero is the same choice made the other
    way: 0x0048BB46 `mov ecx,[1A5A400h]` / `cmp ecx,edi` / `je 0048BCCEh` returns with
    nothing selected.
    """
    g = Ops()
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_ADMIT_DESC, [A_DESC])
    g.poke(G_FIRST_COUNT, [0])
    g.poke(G_FIRST_BASE, [A_DESC])
    g.poke(G_COL_COUNT, [0])
    g.poke(G_COL_TABLE, [A_COL])
    g.poke(G_NEAR_CURSOR, [A_NEAR])
    g.poke(G_GROUP_LIST, [0])
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    return g.bytes()


def _gqs_row(r, schema, poison_work: bool, keep, override=None) -> Ops:
    """One SA2GQS3 case as guest state.

    The 93 words are (sequence, tick, entity[8], world[10], 24 schema-indexed player
    words, player_w19..21, gravity[3], query[7], tangent[3], head work[33]); the layout
    is the format's own section field list in tools/s2/fixtures/corpus.py.
    """
    entity = list(r[2:10])
    world = list(r[10:20])
    player = list(r[20:44])
    player_19_21 = list(r[44:47])
    gravity = list(r[47:50])
    work = list(r[60:93])
    if override:
        entity, world, player, player_19_21, gravity, work = override(
            entity, world, player, player_19_21, gravity, work)
    o = Ops()
    reset_arenas(o)
    o.fill(A_PLAYER, 0x200)
    o.fill(A_COL, 0x100)
    o.fill(A_DESC, 0x100)
    o.fill(A_NEAR, 0x400)
    o.poke(G_NEAR_COUNT, [0])
    o.poke(A_ENTITY, entity)
    o.poke(A_GWORLD, world)
    for (idx,), value in zip(schema, player):
        o.poke(A_PLAYER + 4 * idx, [value])
    o.poke(A_PLAYER + 0x4C, player_19_21)        # player words 19..21, not in the schema
    o.poke(A_PLAYER + 0x90, [A_GWORK])            # player word 36: the work-buffer pointer
    o.poke(G_GRAVITY, gravity)
    o.poke(A_GWORK, [POISON] * 33)
    if not poison_work:
        o.poke(A_GWORK, work)
    else:
        for i, value in enumerate(work):
            if i not in GQS_BUILT:
                o.poke(A_GWORK + 4 * i, [value])
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_ENTITY, A_GWORLD, A_PLAYER])
    o.call(0x00461F10)
    for i in keep:
        o.peek(A_GWORK + 4 * i, 4)
    return o


# Work words 13 and 14 are the two the seed is *not* the last writer of. Inside
# 0x00461F10 the only other stores into the work buffer are 0x00463100, 0x0046312F,
# 0x0046317D/0x00463180 and 0x004631CF/0x004631D9 -- all `mov [ebx+34h]` / `[ebx+38h]`,
# all in the candidate tail, which only the non-empty path reaches. SA2GQS3 snapshots
# the buffer at the call's exit and records no column saying which path the native call
# took, so those two words are comparable only on the ticks where the tail did not run.
# The rule is read out of the disassembly and applied to both SA2GQS3 fixtures rather
# than to the rows that happen to fail: compare a work word only where the seed is its
# last writer inside this function.
GQS_LATE = (13, 14)


def _gqs_plan(fixture: str, rows, header, sections, poison_work: bool = True,
              override=None, note_extra: str = "", compare_late: bool = False) -> Plan:
    schema = sections["player_schema"]
    keep = [i for i in range(33) if compare_late or i not in GQS_LATE]
    enc = [_gqs_row(r, schema, poison_work, keep, override).bytes() for r in rows]
    exp = [tuple(r[60 + i] for i in keep) for r in rows]
    return Plan(fixture, 0x00461F10, len(keep), tuple(f"work_w{i}" for i in keep), enc, exp,
                abi="disassembly at 0x00461F10: cdecl(entity, world, player); the 33-word "
                    "collision work buffer is [player+0x90] (player word 36, the one word of "
                    "the struct SA2GQS3's schema omits because it is a pointer); the seven-word "
                    "query 0x0048BAF0 takes is that buffer's own words 0..6 (0x0046204C "
                    "`push ebx`), so `query_w0..6` and `tangent[3]` are work words 0..6 and 7..9",
                note="the whole call runs, because an empty COL table makes the gather admit "
                     "nothing and 0x004620D4 `test dx,dx` / `jne 00462133h` falls through to the "
                     "empty-set tail and the `ret` at 0x00462132. The buffer enters poisoned "
                     "with 0xDEADBEEF and only the twenty-one words the step does not write are "
                     "written back, so each of the twelve it builds is compared against a slot "
                     "it had to fill." + note_extra,
                globals_ops=_gqs_globals())


@builder("precise-ground-query-seed-v1")
def _gqs3a(rows, header, sections=None) -> Plan:
    return _gqs_plan("precise-ground-query-seed-v1", rows, header, sections)


@builder("ground-query-seed-slope-v1")
def _gqs3b(rows, header, sections=None) -> Plan:
    return _gqs_plan("ground-query-seed-slope-v1", rows, header, sections)


# ---------------------------------------------------------------- 0x004622EE window
# The candidate-triple ordering block, 0x004622EE..0x0046244B, reached by the one branch
# 0x0046228E `jne short 004622EEh` with ECX = [esp+34h], the fill loop's count.
#
# The registry called this undrivable because the block "reads a promotion flag at
# [esp+10h] and a slot index at [esp+18h]" that SA2GOO2 does not record, and priced the
# entry at the fill loop's own live-ins (the 33-word work buffer at [ebx], the player
# status byte [esi+1Ch], each object's polygon cache). None of that is read between
# 0x004622EE and 0x0046244B. What the block does read, and where SA2GOO2 records it:
#
#   [esp+34h]     the count                                   `count`
#   ecx           the same count (0x0046228E `mov ecx,[esp+34h]` before the branch)
#   [esi+0A0h]    0x0046232C `cmp dword ptr [esi+0A0h],0`      `prior_task`, player word 40
#   [esi+0B0h]    0x00462377 / 0x004623FE `mov edi,[esi+0B0h]` `prior_object`, player word 44
#   [esp+280h]..  the source triples, 12 bytes each            the `src` entries
#   [esp+10h]     the promotion flag                          -- not recorded; 0
#   [esp+18h]     the slot index                              -- not recorded; read only
#                 under `cmp dword ptr [esp+10h],1` (0x00462360, 0x004623E7), so with the
#                 flag clear it is not a live-in of this block at all
#
# Byte 0xA0 is word 40 and byte 0xB0 is word 44. The two fields the blocker calls
# missing are the two SA2GOO2 names, at the offsets the block reads them.
#
# Outputs: the ordered list at [esp+100h], twelve bytes per entry, and the write cursor
# spilled to [esp+2Ch] at 0x004623B7 / 0x00462440. The plan poisons four slots and
# compares all four, so the slots past the list have to come back untouched.
GOO_WINDOW = 0x004622EE
GOO_FRAME = ESP0 - 0x1000
GOO_SLOTS = 4                       # max count across both fixtures is 3
GOO_LIST = 0x100
GOO_SOURCE = 0x280
A_GOO_PLAYER = A_ARGS + 0x3000


def _goo_row(call, flag: int = 0, slot: int = 0, override=None) -> Ops:
    tick, count, prior_task, prior_object, cursor, src, ordered = call
    if override:
        count, prior_task, prior_object, src = override(count, prior_task, prior_object,
                                                        [list(e) for e in src])
    o = Ops()
    reset_arenas(o)
    o.fill(GOO_FRAME, 0x500)
    o.fill(A_GOO_PLAYER, 0x200)
    o.poke(A_GOO_PLAYER + 0xA0, [prior_task])
    o.poke(A_GOO_PLAYER + 0xB0, [prior_object])
    o.poke(GOO_FRAME + 0x10, [flag])
    o.poke(GOO_FRAME + 0x18, [slot])
    o.poke(GOO_FRAME + 0x2C, [POISON])
    o.poke(GOO_FRAME + 0x34, [count])
    o.poke(GOO_FRAME + GOO_LIST, [POISON] * (3 * GOO_SLOTS))
    for i, e in enumerate(src):
        o.poke(GOO_FRAME + GOO_SOURCE + 12 * i, list(e)[:3])
    o.reg(ESP, GOO_FRAME).sentinel()
    o.reg(ECX, count).reg(ESI, A_GOO_PLAYER)
    o.call(GOO_WINDOW)
    o.peek(GOO_FRAME + GOO_LIST, 12 * GOO_SLOTS)
    o.peek(GOO_FRAME + 0x2C, 4)
    return o


def _goo_expected(call) -> tuple[int, ...]:
    _tick, count, _pt, _po, cursor, _src, ordered = call
    words: list[int] = []
    for i in range(GOO_SLOTS):
        words += list(ordered[i])[:3] if i < count else [POISON] * 3
    words.append(GOO_FRAME + GOO_LIST + 12 * cursor)
    return tuple(words)


def _goo_plan(fixture: str, rows, flag: int = 0, slot: int = 0, override=None) -> Plan:
    enc = [_goo_row(c, flag, slot, override).bytes() for c in rows]
    exp = [_goo_expected(c) for c in rows]
    cols = tuple(f"ord{i}_w{j}" for i in range(GOO_SLOTS) for j in range(3)) + ("cursor",)
    return Plan(fixture, GOO_WINDOW, 3 * GOO_SLOTS + 1, cols, enc, exp,
                abi="window 0x004622EE..0x0046244B inside GroundCollisionStep 0x00461F10, "
                    "entered the way 0x0046228E `jne short 004622EEh` enters it: ECX = the "
                    "count, ESI = the player, and the block's own frame at ESP with the count "
                    "at +0x34, the promotion flag at +0x10, the source triples at +0x280 and "
                    "the ordered list at +0x100",
                note="the promotion flag at [esp+10h] is 0, which SA2GOO2 does not record; the "
                     "slot index at [esp+18h] is read only under `cmp dword ptr [esp+10h],1` "
                     "(0x00462360 and 0x004623E7) and is therefore not a live-in with the flag "
                     "clear. The list is poisoned with 0xDEADBEEF to four slots and all four "
                     "are compared, so the slots past the count have to come back untouched "
                     "and the cursor has to be the address the block spilled, not a default.")


@builder("ground-object-order-v2")
def _goo2a(rows, header, sections=None) -> Plan:
    return _goo_plan("ground-object-order-v2", rows)


@builder("ground-object-order-slope-v1")
def _goo2b(rows, header, sections=None) -> Plan:
    return _goo_plan("ground-object-order-slope-v1", rows)


# ---------------------------------------------------------------- 0x00462526 window
# The policy-word window, 0x00462526..0x00462C18, whose four blocks build [ESP+0x1C]
# and leave the contact angle in ESI. Every path out reaches 0x00462C19, so the block
# is lifted `--block 0x00462526:0x00462C19+ret`.
#
# The registry called this undrivable because the window "collapses the two pointers its
# first comparison needs into one boolean": `0x00462532 mov ecx,[esp+74h]` /
# `0x00462536 cmp ecx,[edi+4]` / `je 00462665h`. That comparison is a bare `cmp` of two
# dwords and its only consumer is the branch -- `[esp+74h]` is loaded at that one site
# and nowhere else in the 1779 bytes, and `edi` is dereferenced only there and at
# `0x004625CC test dword ptr [edi],100h`, which reads the attribute the fixture records.
# So the two addresses do not have to be the native ones; they have to have the recorded
# relation. The plan lays down one support object and points `[edi+4]` at it when
# `support_matches` is 1 and at a different address when it is 0.
#
# The rest of the window's live-ins are the fixture's own columns, at the offsets the
# window reads them (the stager's own case layout is quoted in the `note`):
#
#   eax, [ebp+8], [esp+30h]   the entity: `0x0046251A mov eax,[ebp+8]`, and [esp+30h] is
#                             entity+0x14 (`0x0046203E lea ecx,[eax+14h]` /
#                             `0x00462056 mov [esp+34h],ecx` at ESP-4)
#   ecx                       `0x0046251D movzx ecx,word ptr [eax+4]`, entity word 1
#   edx                       1, from `0x00462521 mov edx,1` -- an image constant
#   ebx, [esp+24h]            the work buffer (`0x00461F5C mov [esp+24h],ebx`)
#   [esp+44h]                 work+0x1C (`0x00461F62 mov [esp+44h],ecx`)
#   [esp+68h]                 work+4 (`0x00462048 mov [esp+68h],edx`)
#   edi, [esp+18h]            the candidate triple (`0x004624F0 mov [esp+18h],edi`)
#   [esp+4Ch..54h]            case words 20..22      [esp+58h..60h]  case words 23..25
#   [0x01A5A244]              case word 26           [0x01DE94A0..A8] the gravity globals
#   [ebp+0Ch], [ebp+10h]      the world vector and the player
GPW_WINDOW = 0x00462526
GPW_FRAME = ESP0 - 0x1000
A_TRIPLE = A_ARGS + 0x3200
A_SUPPORT = A_ARGS + 0x3300
A_NOT_SUPPORT = A_ARGS + 0x3400
G_POLICY_GLOBAL = 0x01A5A244
GPW_PLAYER_WORDS = (7, 25, 35, 77)      # the stager's own four: case words 11..14


def _gpw_row(r, override=None) -> Ops:
    entity = list(r[3:11])
    player = list(r[11:15])
    world = list(r[15:18])
    attribute, support_matches = r[18], r[19]
    frame_4c = list(r[20:23])
    frame_58 = list(r[23:26])
    global244 = r[26]
    gravity = list(r[27:30])
    work = list(r[30:43]) + list(r[63:83])      # head words 0..12, precise words 13..32
    if override:
        (entity, player, world, attribute, support_matches, frame_4c, frame_58,
         global244, gravity, work) = override(entity, player, world, attribute,
                                              support_matches, frame_4c, frame_58,
                                              global244, gravity, work)
    o = Ops()
    reset_arenas(o)
    o.fill(GPW_FRAME - 0x200, 0x400)
    o.fill(A_PLAYER, 0x200)
    o.poke(A_ENTITY, entity)
    o.poke(A_GWORLD, world)
    for idx, value in zip(GPW_PLAYER_WORDS, player):
        o.poke(A_PLAYER + 4 * idx, [value])
    o.poke(A_GWORK, work)
    o.poke(A_TRIPLE, [attribute, A_SUPPORT if support_matches else A_NOT_SUPPORT, 0])
    o.poke(G_POLICY_GLOBAL, [global244])
    o.poke(G_GRAVITY, gravity)
    matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
    o.poke(ESP0 + 4, [A_ENTITY, A_GWORLD, A_PLAYER])
    o.reg(EBP, ESP0 - 4)                         # [ebp+8/0Ch/10h] are the three arguments
    o.poke(GPW_FRAME + 0x18, [A_TRIPLE])
    o.poke(GPW_FRAME + 0x1C, [POISON])
    o.poke(GPW_FRAME + 0x24, [A_GWORK])
    o.poke(GPW_FRAME + 0x30, [A_ENTITY + 0x14])
    o.poke(GPW_FRAME + 0x44, [A_GWORK + 0x1C])
    o.poke(GPW_FRAME + 0x4C, frame_4c)
    o.poke(GPW_FRAME + 0x58, frame_58)
    o.poke(GPW_FRAME + 0x68, [A_GWORK + 4])
    o.poke(GPW_FRAME + 0x74, [A_SUPPORT])
    o.reg(ESP, GPW_FRAME).sentinel()             # the synthetic `ret` at 0x00462C19 takes [ESP]
    o.reg(EAX, A_ENTITY).reg(ECX, entity[1] & 0xFFFF).reg(EDX, 1)
    o.reg(EBX, A_GWORK).reg(EDI, A_TRIPLE)
    o.call(GPW_WINDOW)
    o.peek(GPW_FRAME + 0x1C, 4)
    return o


def _gpw_plan(fixture: str, rows, with_angle: bool, override=None) -> Plan:
    enc, exp = [], []
    for r in rows:
        o = _gpw_row(r, override)
        if with_angle:
            o.emit_reg(ESI)
        enc.append(o.bytes())
        exp.append((r[83], r[84]) if with_angle else (r[83],))
    cols = ("expected_policy", "expected_angle") if with_angle else ("expected_policy",)
    return Plan(fixture, GPW_WINDOW, len(cols), cols, enc, exp,
                abi="window 0x00462526..0x00462C18 inside GroundCollisionStep 0x00461F10, "
                    "entered as 0x00462521 leaves it: EAX = the entity, ECX = entity word 1's "
                    "low half, EDX = 1, EBX = the work buffer, EDI = the candidate triple, "
                    "EBP = the caller frame whose +8/+0xC/+0x10 are entity, world and player, "
                    "and the window's own frame at ESP",
                note="`support_matches` is driven as a relation, not a pointer: [esp+74h] is "
                     "read once (0x00462532) and [edi+4] once (0x00462536), and the only "
                     "consumer of the compare is `je 00462665h`, so the plan points [edi+4] at "
                     "the support object when the column is 1 and at a different address when "
                     "it is 0. The case layout is the stager's own: words 11..14 are player "
                     "words 7, 25, 35 and 77, 15..17 the velocity, 20..25 the two frame "
                     "triples at [esp+4Ch] and [esp+58h], 26 the global 0x01A5A244, 30..62 the "
                     "work buffer at the loop head and 63..82 work words 13..32 after the "
                     "precise call.")


@builder("ground-policy-word-v1")
def _gpw1a(rows, header, sections=None) -> Plan:
    # flags bit 0 is clear on all thirteen flat cases: the stager records that ESI is not
    # captured at the flat window's tail, so the angle column is 0 and is not compared.
    return _gpw_plan("ground-policy-word-v1", rows, with_angle=False)


@builder("ground-policy-word-slope-v1")
def _gpw1b(rows, header, sections=None) -> Plan:
    return _gpw_plan("ground-policy-word-slope-v1", rows, with_angle=True)


# ---------------------------------------------------------------- SA2GAC1 at 0x00462526
# `ground-acceptance-v1` records the acceptance block at 0x00462C19, and that boundary
# cannot be entered cold: `0x00462C55 fcom st,dword ptr [eax*8+1DEFBA4h]` and
# `0x00462CCC fcomp st,st(4)` read x87 registers the block never pushes, and no SA2GAC1
# column carries them. The registry's four named fields are a different matter and the
# brief's reading of them is right: the three gravity globals at 0x01DE94A0/A4/A8 are
# guest globals `preset-position-v1` already pokes by name, and the player byte
# `0x00462C52 movsx eax,byte ptr [esi]` is player word 0, which SA2GQS3's own player
# schema carries for these same ticks. What defeats the fixture at *its own* boundary is
# the x87 state, which none of that helps with.
#
# One boundary earlier does work. SA2GAC1's 43 cases are, column for column, the 43
# cases of `ground-policy-word-slope-v1`: every shared column -- entity words 0..7, the
# velocity, work words 0..32, [ESP+0x58..0x60], the attribute, the global, player words
# 7, 25 and 35, the policy word and the angle -- agrees on all 43 rows. So this fixture
# drives at the policy window 0x00462526, where the x87 stack is empty, and three of the
# words its own schema calls *inputs* become compared outputs:
#
#   case word 2   the policy word at the acceptance block's entry
#   case word 10  the angle, ESI at that entry
#   case word 58  bit 8 expected, which is `(word 2 >> 3) & 1` on all 43 rows, so
#                 comparing word 2 bit for bit compares it and more
#
# Case words 59 and 60 (the verdict and the helper return) are **not compared**: they
# are decided past 0x00462C19 by two different exits -- the rejection at 0x00463644 and
# the helper call `0x00462F7E call 00465DB0h` -- so a window ending in one synthetic
# `ret` cannot carry both. On this corpus they are also a function of word 58 (verdict 4
# and helper 0 exactly when bit 8 is set, 3 and 0xFFFFFFFF otherwise, on all 43 rows).
#
# Three inputs the window reads and SA2GAC1 does not record -- the [ESP+0x4C] triple,
# player word 77 and the gravity globals -- come from the sibling fixture
# `ground-policy-word-slope-v1`, matched by (tick, iteration). That is not free and it
# is not the answer: they are *inputs*, the sibling passes on its own, and every one of
# the eleven column groups the two fixtures share agrees on all 43 rows, which is
# checked here rather than assumed (`_gac_sibling` raises otherwise). Player word 77 is
# what makes it necessary, and it is the one of the three that is not free: on the two
# cases whose entity word 1 is odd the bit-1 block runs, it reaches the player through
# [ebp+10h], and with that word zeroed both cases come back 6 where the record says 7 --
# the block runs to 0x0046265D `mov dword ptr [esp+1Ch],0` instead of leaving at
# 0x004625BB, and bit 0 is lost. The [ESP+0x4C] triple and the gravity globals are
# measured inert on every case of every SA2GPW1 and SA2GAC1 row
# (`tools/s2/corpus_run/controls.py`); they are taken from the sibling anyway rather
# than invented.
_GAC_SHARED = ((14, 22, 3, 11), (11, 14, 23, 26), (25, 58, 30, 43), (22, 25, 15, 18))


def _gac_sibling(rows) -> dict:
    """{(tick, iteration): the matching `ground-policy-word-slope-v1` case}, verified."""
    import fixture_rows as _FR
    _h, sib, _s = _FR.load("ground-policy-word-slope-v1")
    by_key = {(p[1], p[2]): p for p in sib}
    for r in rows:
        p = by_key.get((r[0], r[1]))
        if p is None:
            raise SystemExit(f"ground-acceptance-v1 tick {r[0]} iteration {r[1]} has no "
                             "ground-policy-word-slope-v1 case")
        agree = (list(r[14:22]) == list(p[3:11])                    # entity words 0..7
                 and list(r[22:25]) == list(p[15:18])               # the velocity
                 and list(r[11:14]) == list(p[23:26])               # [ESP+0x58..0x60]
                 and list(r[25:58]) == list(p[30:43]) + list(p[63:83])
                 and (r[5], r[6], r[8]) == (p[11], p[12], p[13])    # player words 7, 25, 35
                 and (r[3], r[4]) == (p[18], p[26])                 # attribute, global
                 and (r[2], r[10]) == (p[83], p[84]))               # the policy word, the angle
        if not agree:
            raise SystemExit(f"ground-acceptance-v1 tick {r[0]} iteration {r[1]} disagrees "
                             "with its ground-policy-word-slope-v1 case")
    return by_key


def _gac_as_gpw(r, sibling) -> list[int]:
    """One SA2GAC1 case as an SA2GPW1-shaped case, so one plan builder serves both."""
    p = sibling[(r[0], r[1])]
    c = [0] * 86
    c[3:11] = list(r[14:22])                 # entity words 0..7
    c[11] = r[5]                             # player word 7
    c[12] = r[6]                             # player word 25
    c[13] = r[8]                             # player word 35
    c[14] = p[14]                            # player word 77, from the sibling
    c[15:18] = list(r[22:25])                # the velocity, [ebp+0Ch]
    c[18] = r[3]                             # the candidate attribute
    c[19] = r[3] and 0                       # support_matches: measured inert, driven as 0
    c[20:23] = list(p[20:23])                # [ESP+0x4C..0x54], from the sibling
    c[23:26] = list(r[11:14])                # [ESP+0x58..0x60]
    c[26] = r[4]                             # the global 0x01A5A244
    c[27:30] = list(p[27:30])                # the gravity globals, from the sibling
    c[30:43] = list(r[25:38])                # work words 0..12
    c[63:83] = list(r[38:58])                # work words 13..32
    c[83], c[84] = r[2], r[10]               # the policy word and the angle, as outputs
    return c


@builder("ground-acceptance-v1")
def _gac1(rows, header, sections=None) -> Plan:
    sibling = _gac_sibling(rows)
    plan = _gpw_plan("ground-acceptance-v1", [_gac_as_gpw(r, sibling) for r in rows],
                     with_angle=True)
    plan.columns = ("policy_word_at_0x00462C19", "angle_at_0x00462C19")
    plan.note += (" Driven at 0x00462526 rather than at SA2GAC1's own 0x00462C19, which "
                  "reads x87 registers it never pushes (0x00462C55 `fcom st,dword ptr "
                  "[eax*8+1DEFBA4h]`, 0x00462CCC `fcomp st,st(4)`). Case words 59 and 60 "
                  "are not compared: the verdict is decided past 0x00462C19 by two "
                  "different exits, the rejection at 0x00463644 and the helper call "
                  "0x00462F7E.")
    return plan


# ---------------------------------------------------------------- 0x00462F83 window
# The GroundCollisionStep accepted tail, 0x00462F83..0x00463821, which the six SA2GTR*
# fixtures record. `lift_set.py --block 0x00462F83:0x00463822`; every path out of the
# window is one of the routine's own three `ret`s, so no synthetic return is needed.
#
# The registry called this undrivable because "the window reads nine caller-frame
# pointers none of which the fixture carries" and because the schema replaces the
# support pointer at [esp+74h] with a token 0/1/2 in player words 40 and 44. Both parts
# are answerable, and the answer is the predecessor's own static reduction
# (Documentation/predecessor-side GROUND_TAIL_STATIC_REDUCTION.md, reproduced here from
# the image): the window's read-before-written caller-frame set is exactly fourteen
# words, and thirteen of the fourteen are either a recorded value or an address of a
# block the driver lays out itself.
#
#   +0x10  work+0x48     0x00463290 `mov eax,[esp+14h]` (after one push)
#   +0x18  the candidate triple, CF+0x100+12*i   0x00462FA0 `mov ecx,[esp+18h]`
#   +0x1C  the contact policy flags              semantic word 1
#   +0x24  the work buffer                       0x00463269 `mov eax,[esp+24h]`
#   +0x28  the response accumulator              semantic word 2
#   +0x2C  the moving-support pointer            semantic word 5, a recorded boolean
#   +0x30  entity+0x14                           0x004637F8 `mov eax,[esp+30h]`
#   +0x34  the candidate loop bound              semantic word 6
#   +0x44  work+0x1C                             0x00463389 `mov ecx,[esp+44h]`
#   +0x64  the candidate loop index              semantic word 7
#   +0x68  work+4                                0x0046337E `mov esi,[esp+68h]`
#   +0x6C  work+0x10                             0x004637EE `mov edx,[esp+6Ch]`
#   +0x74  the support object pointer            = the candidate triple's word 1
#   +0xB4  the accepted-tail flag                semantic word 8
#
# The support pointer is not a fourteenth independent unknown: the two instructions that
# set it up are in the image, at 0x00462F58 `mov eax,[esp+18h]` / 0x00462F5E
# `mov ecx,[eax+4]` / 0x00462F77 `mov [esp+88h],ecx` (five pushes deep, so caller-frame
# +0x74). So [esp+74h] *is* the candidate triple's second word, and the driver that lays
# the triple out chooses it.
#
# That is what makes the stager's normalisation invertible. `stage_precise_ground_tail_
# fixture.py`'s `normalized_player` maps player words 40 and 44 to
# `0 if v == 0 else (1 if v == current_support else 2)` with
# `current_support = pre_stack_w29` = [esp+74h]. The driver knows which address it put
# at [esp+74h] by construction, so it maps the recorded token back: 0 -> 0,
# 1 -> the support it laid down, 2 -> a different object it also laid down. Nothing is
# taken from the expected output; the token is a name for an address the driver owns,
# exactly as `candidate-enumeration-v1` maps guest node pointers back to fixture
# ordinals. Across all six fixtures the *output* token for word 44 is 1 on every one of
# the 86 cases and for word 40 is 0 on every one, so the inverse is single-valued.
#
# Register live-ins, each read out of the image rather than assumed:
#   eax  the helper's result, tested at 0x00462F86        semantic word 3
#   ebx  the work buffer (0x00461F23 `mov ebx,[esi+90h]`, player word 36)
#   esi  the EntityData2 block (0x00462F64 `mov esi,[ebp+0Ch]`, immediately before the
#        helper call, so it is live across it)
#   edi  the entity (0x00462C19 `mov edi,[ebp+8]`, which dominates the helper call)
#   ebp  the frame whose +8/+0Ch/+10h are entity, world and player
#
# Two guest globals the window reads are **supplied and named**, in the category the
# registry itself put them in beside `root_center_z`:
#   [0x01A5A248]  0x00463359 `cmp dword ptr [1A5A248h],0`. GroundCollisionStep's own
#                 prologue zeroes it (0x00461F32 `mov [1A5A248h],eax`, eax = 0) and the
#                 only three stores of 1 in the whole image are 0x004651FC, 0x00465657
#                 and 0x00465C78, inside the contact helpers 0x00464E30, 0x00465390 and
#                 0x004659A0. Driven as 0; the control `tail-global-1A5A248` perturbs it
#                 and names the three work words it moves.
#   [0x01A5A23C]  0x004634AB `mov ecx,[1A5A23Ch]`, gating 0x004634EC and 0x004635DC.
#                 Its image-initial value is 0 and the only store in the image is
#                 0x0045A730 `mov dword ptr [1A5A23Ch],1`. Driven as 0; the control
#                 `tail-global-1A5A23C` perturbs it.
# Neither is recovered from a compared column, and each carries a control that says what
# it buys.
GTR_WINDOW = 0x00462F83
GTR_CF = A_STACK_TOP - 0x8000          # the "post-cleanup caller frame" the reduction uses
GTR_EBP = GTR_CF + 0x400               # [ebp+8/0Ch/10h] = entity, world, player
A_GTR_SUPPORT = A_ARGS + 0x3500
A_GTR_OTHER = A_ARGS + 0x3600
G_TAIL_CONTACT = 0x01A5A248
G_TAIL_SURFACE = 0x01A5A23C
G_STAGE_SELECTOR = 0x01934B70
GTR_PRESET = 0x00469050


def _gtr_shape(header: dict) -> tuple[int, int]:
    """(semantic words, EntityData2 words) for one SA2GTR file, from its own header.

    The case is `sequence, tick, semantic[S], (entity, world, player, work, record),
    (entity, world, player, work), (entity, world, player)` -- the stager's own order.
    Everything but S and the world width is in the header, so
    `S + 3*W = case_words - 2 - 3*entity - 3*player - 2*work - record`. S is the one
    number the file does not carry; `tools/s2/fixtures/corpus.py` records it per magic
    ("superseded by SA2GTR2..5; 13 semantic words" on SA2GTR1, "16 semantic words" on
    SA2GTR2), and the predecessor stagers set `SEMANTIC_WORDS = 16` from v2 on.
    """
    sem = 13 if header["version"] == 1 else 16
    rest = (header["case_words"] - 2 - 3 * header["entity_words"]
            - 3 * header["player_fields"] - 2 * header["work_words"]
            - header["record_words"] - sem)
    if rest < 0 or rest % 3:
        raise SystemExit(f"SA2GTR case width {header['case_words']} does not split")
    return sem, rest // 3


def _gtr_case(row, header: dict, sem: int, world: int) -> dict:
    """One case as named blocks."""
    ew, pf, ww, rw = (header["entity_words"], header["player_fields"],
                      header["work_words"], header["record_words"])
    c = {"sequence": row[0], "tick": row[1], "semantic": list(row[2:2 + sem])}
    p = 2 + sem
    for tag, with_work, with_record in (("in", True, True), ("ground", True, False),
                                        ("reset", False, False)):
        c[tag + "_entity"] = list(row[p:p + ew]); p += ew
        c[tag + "_world"] = list(row[p:p + world]); p += world
        c[tag + "_player"] = list(row[p:p + pf]); p += pf
        if with_work:
            c[tag + "_work"] = list(row[p:p + ww]); p += ww
        if with_record:
            c["record"] = list(row[p:p + rw]); p += rw
    if p != header["case_words"]:
        raise SystemExit(f"SA2GTR case walk ended at {p} of {header['case_words']}")
    return c


def _gtr_semantic(c: dict, sem: int) -> dict:
    """The semantic block by name. SA2GTR1 drops three of the sixteen -- the helper
    result, the zero attestation word and the stage selector -- and keeps the other
    thirteen in order, which is what the two files' own values show."""
    s = c["semantic"]
    if sem == 16:
        return {"attribute": s[0], "policy": s[1], "accumulator": s[2], "result": s[3],
                "moving_support": s[5], "bound": s[6], "index": s[7], "tail_flag": s[8],
                "gravity": s[10:13], "selector": s[15]}
    return {"attribute": s[0], "policy": s[1], "accumulator": s[2], "result": None,
            "moving_support": s[3], "bound": s[4], "index": s[5], "tail_flag": s[6],
            "gravity": s[8:11], "selector": None}


_GTR_TOKEN = {0: 0, 1: A_GTR_SUPPORT, 2: A_GTR_OTHER}


def _gtr_player(values, schema) -> list[int]:
    """The 24 recorded player words with the two normalised ones mapped back to the
    addresses this driver laid down. `schema` is the fixture's own index list."""
    out = []
    for idx, value in zip(schema, values):
        out.append(_GTR_TOKEN[value] if idx in (40, 44) else value)
    return out


def _gtr_row(c: dict, sm: dict, schema, world: int, override=None) -> Ops:
    entity = list(c["in_entity"])
    wvec = list(c["in_world"]) + [0] * (10 - world)
    player = _gtr_player(c["in_player"], schema)
    work = list(c["in_work"])
    record = list(c["record"])
    globals_ = [0, 0]                       # [0x01A5A248], [0x01A5A23C]
    sm = dict(sm)
    sm.setdefault("support", A_GTR_SUPPORT)
    if override:
        entity, wvec, player, work, record, globals_, sm = override(
            entity, wvec, player, work, record, globals_, sm)
    o = Ops()
    reset_arenas(o)
    o.fill(A_PLAYER, 0x400)
    o.fill(A_GTR_SUPPORT, 0x200)
    o.poke(A_ENTITY, entity)
    o.poke(A_GWORLD, wvec)
    for idx, value in zip(schema, player):
        o.poke(A_PLAYER + 4 * idx, [value])
    o.poke(A_PLAYER + 0x90, [A_GWORK])          # player word 36, the work-buffer pointer
    o.poke(A_GWORK, work)
    # The accepted compact record, where 0x00463385 `movzx eax,word ptr [ebx+2Ah]` /
    # `imul eax,eax,58h` / `add eax,1DDB660h` reads it: the public 22-word record array
    # `selection-output-v1` already drives, indexed by the work buffer's own half-word.
    o.poke(G_RECORDS_PUBLIC + 0x58 * ((work[10] >> 16) & 0xFFFF), record)
    o.poke(G_GRAVITY, list(sm["gravity"]))
    o.poke(G_TAIL_CONTACT, [globals_[0]])
    o.poke(G_TAIL_SURFACE, [globals_[1]])
    o.poke(G_STAGE_SELECTOR, [sm["selector"]])
    matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
    triple = GTR_CF + 0x100 + 12 * sm["index"]
    o.poke(GTR_CF + 0x10, [A_GWORK + 0x48])
    o.poke(GTR_CF + 0x18, [triple])
    o.poke(GTR_CF + 0x1C, [sm["policy"]])
    o.poke(GTR_CF + 0x24, [A_GWORK])
    o.poke(GTR_CF + 0x28, [sm["accumulator"]])
    o.poke(GTR_CF + 0x2C, [0 if sm["moving_support"] == 0 else A_GTR_OTHER])
    o.poke(GTR_CF + 0x30, [A_ENTITY + 0x14])
    o.poke(GTR_CF + 0x34, [sm["bound"]])
    o.poke(GTR_CF + 0x44, [A_GWORK + 0x1C])
    o.poke(GTR_CF + 0x64, [sm["index"]])
    o.poke(GTR_CF + 0x68, [A_GWORK + 4])
    o.poke(GTR_CF + 0x6C, [A_GWORK + 0x10])
    o.poke(GTR_CF + 0x74, [sm["support"]])
    o.poke(GTR_CF + 0xB4, [sm["tail_flag"]])
    o.poke(triple, [sm["attribute"], sm["support"], 0])
    o.poke(GTR_EBP + 8, [A_ENTITY, A_GWORLD, A_PLAYER])
    o.reg(ESP, GTR_EBP + 4).sentinel()          # `mov esp,ebp; pop ebp; ret` takes [ebp+4]
    o.reg(ESP, GTR_CF - 0x14).reg(EBP, GTR_EBP)  # the window's first op is `add esp,14h`
    o.reg(EAX, sm["result"]).reg(EBX, A_GWORK)
    o.reg(ESI, A_GWORLD).reg(EDI, A_ENTITY)
    o.call(GTR_WINDOW)
    o.peek(A_ENTITY, 4 * len(entity))
    o.peek(A_GWORLD, 4 * world)
    for idx in schema:
        o.peek(A_PLAYER + 4 * idx, 4)
    o.peek(A_GWORK, 4 * len(work))
    # The PReset seam: the same three blocks through PResetPosition, whose ABI is the
    # one `preset-position-v1` drives (eax = entity, ebx = the world velocity block,
    # [esp+4] = the player, gravity at 0x01DE94A0).
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_PLAYER])
    o.reg(EAX, A_ENTITY).reg(EBX, A_GWORLD)
    o.call(GTR_PRESET)
    o.peek(A_ENTITY, 4 * len(entity))
    o.peek(A_GWORLD, 4 * world)
    for idx in schema:
        o.peek(A_PLAYER + 4 * idx, 4)
    return o


def _gtr_expected(c: dict, schema, world: int) -> tuple[int, ...]:
    return tuple(list(c["ground_entity"]) + list(c["ground_world"])
                 + _gtr_player(c["ground_player"], schema) + list(c["ground_work"])
                 + list(c["reset_entity"]) + list(c["reset_world"])
                 + _gtr_player(c["reset_player"], schema))


def _gtr_columns(schema, entity_words: int, world: int, work_words: int) -> tuple[str, ...]:
    cols = [f"entity_w{i}" for i in range(entity_words)]
    cols += [f"world_w{i}" for i in range(world)]
    cols += [f"player_w{i}" for i in schema]
    cols += [f"work_w{i}" for i in range(work_words)]
    cols += [f"reset_entity_w{i}" for i in range(entity_words)]
    cols += [f"reset_world_w{i}" for i in range(world)]
    cols += [f"reset_player_w{i}" for i in schema]
    return tuple(cols)


def _gtr_sibling(fixture: str, cases, sem: int) -> dict:
    """SA2GTR1 records neither the helper result nor the stage selector; SA2GTR2 is the
    same two ticks with three more semantic words. Take them from there, after checking
    every block the two files share agrees word for word."""
    import fixture_rows as _FR
    sh, srows, _ss = _FR.load(fixture)
    ssem, sworld = _gtr_shape(sh)
    by_key = {}
    for row in srows:
        sc = _gtr_case(row, sh, ssem, sworld)
        by_key[(sc["sequence"], sc["tick"])] = sc
    out = {}
    for c in cases:
        sc = by_key.get((c["sequence"], c["tick"]))
        if sc is None:
            raise SystemExit(f"no {fixture} case for sequence {c['sequence']} tick {c['tick']}")
        for block in ("in_entity", "in_player", "in_work", "record", "ground_entity",
                      "ground_player", "ground_work", "reset_entity", "reset_player"):
            if list(c[block]) != list(sc[block]):
                raise SystemExit(f"{fixture} disagrees on {block} at tick {c['tick']}")
        if list(c["in_world"]) != list(sc["in_world"][:len(c["in_world"])]):
            raise SystemExit(f"{fixture} disagrees on in_world at tick {c['tick']}")
        ssm = _gtr_semantic(sc, ssem)
        out[(c["sequence"], c["tick"])] = {"result": ssm["result"], "selector": ssm["selector"]}
    return out


def _gtr_plan(fixture: str, rows, header, sections, override=None, note_extra: str = "") -> Plan:
    schema = [s[0] for s in sections["player_schema"]]
    sem, world = _gtr_shape(header)
    cases = [_gtr_case(r, header, sem, world) for r in rows]
    supplied = _gtr_sibling("ground-tail-v2", cases, sem) if sem == 13 else {}
    enc, exp = [], []
    for c in cases:
        sm = _gtr_semantic(c, sem)
        if sm["result"] is None:
            sm.update(supplied[(c["sequence"], c["tick"])])
        enc.append(_gtr_row(c, sm, schema, world, override).bytes())
        exp.append(_gtr_expected(c, schema, world))
    note = ("the fourteen read-before-written caller-frame words are laid out at the "
            "offsets the window reads them; nine of them are addresses of blocks this "
            "plan owns (work+0, +4, +0x10, +0x1C, +0x48, entity+0x14, the candidate "
            "triple, the support object and the null moving support) and five are the "
            "fixture's own semantic words. The support pointer at [esp+74h] is the "
            "candidate triple's word 1, which 0x00462F58/0x00462F5E/0x00462F77 put "
            "there, so the stager's `0 / 1 == current_support / 2` normalisation of "
            "player words 40 and 44 inverts on the driver's side. Two guest globals are "
            "supplied and named: [0x01A5A248] = 0 and [0x01A5A23C] = 0, each with a "
            "control." + note_extra)
    if sem == 13:
        note += (" SA2GTR1 does not record the helper result or the stage selector; both "
                 "come from `ground-tail-v2`, the same two ticks, after every shared "
                 "block is checked equal word for word (`_gtr_sibling` raises otherwise).")
    return Plan(fixture, GTR_WINDOW,
                2 * (header["entity_words"] + world + header["player_fields"])
                + header["work_words"],
                _gtr_columns(schema, header["entity_words"], world, header["work_words"]),
                enc, exp,
                abi="window 0x00462F83..0x00463821 inside GroundCollisionStep 0x00461F10, "
                    "entered as 0x00462F7E `call 00465DB0h` returns into it: EAX = the "
                    "helper result, EBX = the 33-word collision work buffer, ESI = the "
                    "EntityData2 block, EDI = the entity, EBP = the frame whose "
                    "+8/+0Ch/+10h are entity, world and player, and ESP = the caller "
                    "frame minus 0x14 because the window's own first instruction is "
                    "`add esp,14h`. The PReset seam is a second call in the same row, to "
                    "PResetPosition 0x00469050 with `preset-position-v1`'s ABI.",
                note=note)


@builder("ground-tail-v1")
def _gtr1(rows, header, sections=None) -> Plan:
    return _gtr_plan("ground-tail-v1", rows, header, sections)


@builder("ground-tail-v2")
def _gtr2(rows, header, sections=None) -> Plan:
    return _gtr_plan("ground-tail-v2", rows, header, sections)


@builder("ground-tail-v3")
def _gtr3(rows, header, sections=None) -> Plan:
    return _gtr_plan("ground-tail-v3", rows, header, sections)


@builder("ground-tail-v4")
def _gtr4(rows, header, sections=None) -> Plan:
    return _gtr_plan("ground-tail-v4", rows, header, sections)


@builder("ground-tail-final-v5")
def _gtr5(rows, header, sections=None) -> Plan:
    return _gtr_plan("ground-tail-final-v5", rows, header, sections)


@builder("ground-tail-slope-v1")
def _gtr5s(rows, header, sections=None) -> Plan:
    return _gtr_plan("ground-tail-slope-v1", rows, header, sections)


# ---------------------------------------------------------------- SA2GEX1 at 0x00462F83
# `ground-candidate-executor-v1` and `ground-executor-slope-v1` are the ordered candidate
# loop: a `topology` row per call and a `payloads` row per *supported* iteration. The
# registry called them undrivable because "the candidate loop's identity word is the
# candidate object pointer, `candidate_w1`, consumed at 0x004624D0 and dereferenced at
# 0x00462508". That word is in the raw-pointer exclusion set, and it is the same object
# the tail stores into player word 44 -- so the same inversion the SA2GTR plan uses
# applies here: the driver lays the candidate triple out and knows which address it put
# in it.
#
# What makes these two drivable without the loop at all is their own stager
# (`stage_precise_ground_candidate_executor_fixture.py`): a payload is
# `sequence, tick, semantic[16], input_state[75], record[22], accepted_state[75],
# expected_final[75], expected_reset[42]`, its `input_state` is asserted equal to the
# accepted iteration's own head state (`require(input_state == first_state ==
# hit_head_state)`), and `semantic[6]`/`semantic[7]` are the loop bound and the hit
# index. On every payload but one the hit *is* the final iteration
# (`semantic[7] == semantic[6] - 1`) and the stager itself requires
# `accepted_state == expected_final`, so the recorded call is the accepted tail this
# corpus already drives, entered at 0x00462F83 with that payload's own semantic block.
#
# The one exception is `ground-candidate-executor-v1`'s tick 118: bound 2, hit 0, and
# `accepted_state != expected_final`, because after the accepted iteration the loop runs
# a second candidate that misses. 0x00463685 `mov eax,[esp+64h]` / `inc eax` /
# `cmp eax,[esp+34h]` / `0x00463692 jl 004624C0h` then jumps *out* of this window, so the
# state the fixture records at the function exit is decided outside 0x00462F83..0x00463821
# and this plan does not compare it. That row is named in the receipt and excluded; the
# `executor-non-final-iteration` control drives it anyway and shows the trap.
#
# The non-`ordinary` calls (topology word 3 != 1) carry no payload at all -- the stager
# emits none (`if not ordinary: continue`) -- so the driven row count is the payload
# count, not the call count.
def _gex_case(p) -> dict:
    return {"sequence": p[0], "tick": p[1], "semantic": list(p[2:18]),
            "in_entity": list(p[18:26]), "in_world": list(p[26:36]),
            "in_player": list(p[36:60]), "in_work": list(p[60:93]),
            "record": list(p[93:115]),
            "accepted": list(p[115:190]),
            "ground_entity": list(p[190:198]), "ground_world": list(p[198:208]),
            "ground_player": list(p[208:232]), "ground_work": list(p[232:265]),
            "reset_entity": list(p[265:273]), "reset_world": list(p[273:283]),
            "reset_player": list(p[283:307])}


def _gex_plan(fixture: str, header, sections, override=None) -> Plan:
    schema = [s[0] for s in sections["player_schema"]]
    world = header["world_words"]
    # SA2GEX1's registered row unit is `calls`, not `payloads`: the stager emits no
    # payload at all for a call whose topology word 3 is not 1. The old note counted
    # only the payloads it skipped, so `ground-executor-slope-v1` read "Rows not driven:
    # none" while it drove 39 of the 41 rows the registry counts (G2b finding 8). Both
    # kinds of undriven row are now declared, one entry each, and `run.py` fails the
    # fixture if the registry's count minus the driven rows is anything else.
    payload_keys = {(p[0], p[1]) for p in sections["payloads"]}
    no_payload = [(t[0], t[1]) for t in sections["topology"]
                  if (t[0], t[1]) not in payload_keys]
    enc, exp, driven, skipped = [], [], [], []
    for p in sections["payloads"]:
        c = _gex_case(p)
        sm = _gtr_semantic(c, 16)
        final = (list(c["accepted"])
                 == list(c["ground_entity"]) + list(c["ground_world"])
                 + list(c["ground_player"]) + list(c["ground_work"]))
        if sm["index"] != sm["bound"] - 1 or not final:
            skipped.append((c["sequence"], c["tick"]))
            continue
        driven.append((c["sequence"], c["tick"]))
        enc.append(_gtr_row(c, sm, schema, world, override).bytes())
        exp.append(_gtr_expected(c, schema, world))
    plan = Plan(fixture, GTR_WINDOW,
                2 * (header["entity_words"] + world + header["player_fields"])
                + header["work_words"],
                _gtr_columns(schema, header["entity_words"], world, header["work_words"]),
                enc, exp,
                abi="window 0x00462F83..0x00463821 inside GroundCollisionStep 0x00461F10, "
                    "the same entry the six SA2GTR fixtures use: EAX = the helper result, "
                    "EBX = the work buffer, ESI = EntityData2, EDI = the entity, EBP = the "
                    "frame whose +8/+0Ch/+10h are entity, world and player, ESP = the "
                    "caller frame minus 0x14",
                note="one row per `payloads` record whose accepted iteration is the final "
                     "one (`semantic[7] == semantic[6] - 1`) and whose `accepted_state` "
                     "the stager already requires equal to `expected_final`; the "
                     "accepted, final and reset blocks are therefore compared once each "
                     "over the 75 + 42 words they name. Registered rows not driven ("
                     + str(len(skipped) + len(no_payload)) + " of " + str(header["calls"])
                     + "): "
                     + (", ".join(f"sequence {s} tick {t}" for s, t in skipped + no_payload)
                        or "none") + ".")
    plan.excluded = (
        [{"row": f"sequence {s} tick {t}", "reason":
          "the accepted iteration is not the final one (`semantic[7] != semantic[6] - 1`), "
          "so 0x00463692 `jl 004624C0h` re-enters the loop head and `accepted_state` is not "
          "`expected_final`; the same call is the whole of ground-candidate-loop-v1, which "
          "drives it"} for s, t in skipped]
        + [{"row": f"sequence {s} tick {t}", "reason":
            "topology word 3 (`kind`) is not 1, so the stager emits no payload for this "
            "call at all and the fixture records nothing to compare"} for s, t in no_payload])
    plan.note += (" The candidate triple's word 1 -- `candidate_w1`, the pointer the "
                  "schema drops -- is the support object this plan lays down at "
                  "[esp+74h], because 0x00462F58 `mov eax,[esp+18h]` / 0x00462F5E "
                  "`mov ecx,[eax+4]` / 0x00462F77 `mov [esp+88h],ecx` is what puts it "
                  "there; player words 40 and 44 invert through the same token map.")
    return plan


@builder("ground-candidate-executor-v1")
def _gex1a(rows, header, sections=None) -> Plan:
    return _gex_plan("ground-candidate-executor-v1", header, sections)


@builder("ground-executor-slope-v1")
def _gex1b(rows, header, sections=None) -> Plan:
    return _gex_plan("ground-executor-slope-v1", header, sections)


# ---------------------------------------------------------------- 0x0048BAF0 -> 0x0048CE40
# SA2GPC1 / SA2GPE2 / SA2GPE3: the selector-to-precise pair GroundCollisionStep calls at
# 0x004620C5 and 0x0046250A, driven as the pair itself rather than as a window inside the
# 6418-byte caller. Both callees are already lifted and already driven by the SA2ECC
# family; what is new here is that the *three* object caches are rebuilt from the
# fixture's own `objects` section, one guest group each, chained in token order.
#
# Read out of the image, not assumed:
#
#   0x0048BB46  ecx = [0x01A5A400], the group list; 0x0048BDCA `mov ecx,[ecx]` walks it,
#               so three chained groups are three iterations of the outer loop.
#   0x0048BB68  `mov [ecx+1Ch],edi` / `mov [ecx+24h],edi` zero each group's selected-list
#               head and counter at the top of its own iteration, so `pni_count` is the
#               per-object count and eax is their sum.
#   0x0048BB7A  esi = [ecx+8], matched at 0x0048BB80 against the near-set table
#               [0x01A5A2DC]+4, stride 0x0C: the group id is the COL record payload the
#               gather 0x0047CD60 (called by the selector itself at 0x0048BB41) copied
#               there, so one COL record per object admits all three groups.
#   0x0048BB98  edx = [ecx+10h], edi = [ecx+14h]: the two ends of the Z chain, whose
#               nodes are linked by [edx] (next) and [edi+4] (previous) and sorted on
#               [edx+10h] -- exactly SA2CSE1's 16-word node, which this fixture's
#               `polygons` records are, links normalised to ordinals by its own stager.
#   0x0048BBA8  `fsub dword [ecx+28h]` / 0x0048BBB1 `fadd [ecx+28h]`: the group tolerance
#               is the object's own `z_extent` word.
#   0x0048BCB5  each accepted node's pointer is written from 0x0194F030 up, in group
#               order, and the running total is mirrored into 0x0194715C and returned.
#   0x0048CE86  `cmp [eax+8],esi` with esi = the eax argument: the precise call names its
#               object by the same group id, which is the whole of the "object token"
#               the old blocker called an obstacle. `TOKENS = {address: token}` in the
#               stager inverts by construction -- the driver lays the three objects out.
#   0x0048CE57  `mov dword [19471B0h],0` -- the record count is reset by the routine.
#   0x0048CE4D  ebx = [ebp+8], the work buffer: radius at [ebx], point at [ebx+4], motion
#               at [ebx+10h] (`lea eax,[ebx+10h]` into njScalor at 0x0048CE65). Those five
#               are the *only* reads through ebx in the whole routine; everything else it
#               does with the buffer is a store.
#   0x0048DC72  `mov [ebx+2Ah],dx` / 0x0048DC84 `mov [ebx+28h],cx` write work word 10 as
#               two half-words -- the best-record index and the record count.
#   0x0048DC8A.. `mov [ebx+34h..80h]` write work words 13..32, the block the fixture
#               records as `output_w13..32`.
#   0x0048DBF5  `cmp ecx,1 / jl 0048CE91h` -- with no record the routine returns 0 and
#               touches nothing at all through ebx. So on a miss row work words 10 and
#               13..32 are *not* this window's outputs; the plan poisons them and expects
#               the poison back, which compares more than the recorded zeros would.
A_GPC_NODES = A_ARGS + 0x270000
A_GPC_GROUP = A_ARGS + 0x268000
A_GPC_COL = A_ARGS + 0x269000
A_GPC_WORK = A_ARGS + 0x2600
GPC_GROUP_STRIDE = 0x40
GPC_ID = 0x0BAF0100
GPC_SELECTOR = 0x0048BAF0
GPC_PRECISE = 0x0048CE40
GPC_WORK_WORDS = 33


def _gpc_objects(fixture: str, secs: dict, cases) -> list[dict]:
    """The three object caches. SA2GPC1 carries them; SA2GPE2 carries one and SA2GPE3
    none, so those three take SA2GPC1's -- and each one is matched before it is taken.

      * `ground-source-cache-contact-expectations-v2` and
        `-all-source-contact-expectations-v3` carry SA2GPC1's own 30 cases, word for word
        on all 46 case words; SA2GPE2's single cache is additionally checked equal to
        SA2GPC1's object of the same token, field for field.
      * `ground-contact-expectations-slope-v1` is a different capture -- 91 cases over
        ticks 160..200, sharing no (sequence, tick, caller_iteration) with SA2GPC1's
        118..130 -- so its supply is checked the only way a different capture can be:
        every token its cases name exists in SA2GPC1, and every case's own `attribute`
        word equals that object's recorded attribute (0x40000001, 0x40400001,
        0x20000001). It is the same three collision objects, later in the same run.
        That the caches are the right ones is not asserted here but *measured*: all 91
        rows, `pni_count` and the twenty contact words included, are reproduced from
        them, and the `slope-objects-swapped` control shows that the geometry is what
        does it.
    """
    import fixture_rows as _FR
    own = secs.get("objects", [])
    if len(own) == 3:
        return own
    _h, sib_cases, sib = _FR.load("ground-contact-composition-v1")
    by_token = {o["token"]: o for o in sib["objects"]}
    if [tuple(c) for c in cases] == [tuple(c) for c in sib_cases]:
        for o in own:
            if by_token.get(o["token"]) != o:
                raise SystemExit(f"{fixture}: its own cache for token {o['token']} "
                                 f"is not ground-contact-composition-v1's")
        return sib["objects"]
    for c in cases:
        case = _gpc_case(c)
        obj = by_token.get(case["token"])
        if obj is None:
            raise SystemExit(f"{fixture}: token {case['token']} is not in "
                             f"ground-contact-composition-v1")
        if obj["attribute"] != case["attribute"]:
            raise SystemExit(f"{fixture}: token {case['token']} attribute "
                             f"0x{case['attribute']:08x} is not the sibling's "
                             f"0x{obj['attribute']:08x}")
    return sib["objects"]


def _gpc_world(objects: list[dict]) -> tuple[Ops, dict]:
    """One guest group per object, its nodes re-pointered from the recorded ordinals.

    The ordinal -> address map is the driver's, exactly as `candidate-enumeration-v1`
    builds SA2CSE1's chain; the chain ends are read from the links themselves (the node
    with no previous, and the node with no next), not assumed to be the first and last.
    """
    g = Ops()
    meta: dict = {"base": {}, "id": {}, "group": {}, "nodes": 0}
    at = A_GPC_NODES
    for i, obj in enumerate(objects):
        polys = obj["polygons"]
        base, n = at, len(polys)

        def node(k: int, _b=base) -> int:
            return _b + NODE_STRIDE * k

        def link(v: int, _b=base) -> int:
            return 0 if v == 0xFFFFFFFF else _b + NODE_STRIDE * v

        words: list[int] = []
        for p in polys:
            words += [link(p[0]), link(p[1])] + list(p[2:16])
        g.poke(base, words)
        head = next(k for k, p in enumerate(polys) if p[1] == 0xFFFFFFFF)
        tail = next(k for k, p in enumerate(polys) if p[0] == 0xFFFFFFFF)
        grp = A_GPC_GROUP + GPC_GROUP_STRIDE * i
        gid = GPC_ID + obj["token"]
        nxt = A_GPC_GROUP + GPC_GROUP_STRIDE * (i + 1) if i + 1 < len(objects) else 0
        g.poke(grp, [nxt, 0, gid, 0])                 # [+0] next, [+8] the id
        g.poke(grp + 0x10, [node(head), node(tail)])  # the two chain ends
        g.poke(grp + 0x28, [obj["z_extent"]])         # the group tolerance
        meta["base"][obj["token"]] = base
        meta["id"][obj["token"]] = gid
        meta["group"][obj["token"]] = grp
        meta["nodes"] += n
        at += NODE_STRIDE * n
    g.poke(G_GROUP_LIST, [A_GPC_GROUP])
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_ADMIT_DESC, [A_DESC])
    g.poke(G_FIRST_COUNT, [0])
    g.poke(G_FIRST_BASE, [A_DESC])
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    return g, meta


def _gpc_case(c) -> dict:
    return {"sequence": c[0], "tick": c[1], "iteration": c[2], "bound": c[3],
            "token": c[4], "attribute": c[5], "selector_sequence": c[6],
            "selector_result": c[7], "pni_count": c[8], "result": c[9],
            "record_count": c[10], "work_w10": c[11],
            "selector": list(c[12:19]), "precise": list(c[19:26]),
            "output": list(c[26:46])}


def _gpc_row(c: dict, objects, meta, max_total: int, nodes: int, admitted,
             override=None) -> Ops:
    o = Ops()
    reset_arenas(o)
    parts = {"selector": list(c["selector"]), "precise": list(c["precise"]),
             "cols": [[GPC_ID + t] for t in admitted],
             "work": [POISON] * GPC_WORK_WORDS,
             "gid": [meta["id"][c["token"]]],
             "seed": [0]}                             # the word the selected list is cleared to
    if override is not None:
        parts = override(parts)
    sel, pre, cols, work = (parts["selector"], parts["precise"], parts["cols"],
                            parts["work"])
    o.poke(G_SELECTED_LIST, parts["seed"] * (max_total + 4))
    o.fill(A_BIG_NEAR, 0x100)
    # One COL record per object, centred on the selector's own query point with a radius
    # nothing can fail and carrying that object's group id. The gather's admission is a
    # gate on the group, not an answer: what the fixture compares comes from the chain
    # walk behind it. (`candidate-enumeration-v1` uses the same one-record gate.)
    o.fill(A_GPC_COL, COL_STRIDE * max(len(cols), 1))
    for i, payload in enumerate(cols):
        rec = A_GPC_COL + COL_STRIDE * i
        o.poke(rec, [sel[1], sel[2], sel[3]])
        o.poke(rec + 0x0C, [FLT_MAX_BITS])
        o.poke(rec + 0x10, payload)
        o.poke(rec + 0x1C, [1])
    o.poke(G_COL_TABLE, [A_GPC_COL])
    o.poke(G_COL_COUNT, [len(cols)])
    o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
    o.poke(G_NEAR_COUNT, [0])
    o.poke(A_QUERY, sel)
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_QUERY])
    o.call(GPC_SELECTOR)
    o.emit_reg(EAX)                                   # selector_result
    o.peek(meta["group"][c["token"]] + 0x24, 4)       # pni_count
    if nodes:
        o.peek(G_SELECTED_LIST, 4 * nodes)            # the selected nodes, in group order
    # The precise call over the work buffer the caller hands it. Words 7..32 are poisoned
    # so every compared output word has to be one the window wrote.
    o.poke(A_GPC_WORK, pre + work[7:])
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_GPC_WORK])
    o.reg(EAX, parts["gid"][0])
    o.call(GPC_PRECISE)
    o.emit_reg(EAX)                                   # result
    o.peek(G_RECORD_COUNT, 4)                         # record_count
    o.peek(A_GPC_WORK + 0x28, 4)                      # work_w10
    o.peek(A_GPC_WORK + 0x34, 80)                     # output_w13..32
    return o


def _gpc_admitted(cases) -> dict[int, tuple[int, ...]]:
    """Which objects the near-set gate admits on each tick.

    The selector walks every group in [0x01A5A400] and skips the ones the near-set table
    does not name (0x0048BB80 `cmp [edx],esi` over [0x01A5A2DC]+4, falling through to
    0x0048BDCA when no entry matches), so which objects are admitted is an *input*, and
    the fixture records it: a tick's cases are its caller's candidate list, one case per
    `caller_iteration`, and `caller_bound` says how many there were. This reads the two
    recorded input columns and cross-checks them against each other; it takes nothing
    from an output column. Driving all three objects on every tick instead reproduces
    82 of the slope fixture's 91 rows and over-counts `selector_result` on the nine
    where the caller had dropped an object -- which is the measurement behind
    `admit-every-object`.
    """
    out: dict[int, tuple[int, ...]] = {}
    for c in cases:
        case = _gpc_case(c)
        out.setdefault(case["tick"], set()).add(case["token"])
    for c in cases:
        case = _gpc_case(c)
        if len(out[case["tick"]]) != case["bound"]:
            raise SystemExit(f"tick {case['tick']}: {len(out[case['tick']])} tokens "
                             f"but caller_bound {case['bound']}")
    return {t: tuple(sorted(v)) for t, v in out.items()}


def _gpc_selected(objects, meta) -> list[int]:
    """The selected node pointers the selector should leave at 0x0194F030, in group
    order: each object's own recorded ordinals, re-pointered into the arena this plan
    laid the object out in."""
    out: list[int] = []
    for ob in objects:
        base = meta["base"][ob["token"]]
        out += [base + NODE_STRIDE * k for k in ob["selected"]]
    return out


def _gpc_expected(c: dict, objects, meta, nodes: int, admitted) -> tuple[int, ...]:
    sel = (tuple(_gpc_selected([ob for ob in objects if ob["token"] in admitted],
                               meta)[:nodes]) if nodes else ())
    wrote = c["record_count"] >= 1
    tail = ((c["work_w10"],) + tuple(c["output"]) if wrote
            else (POISON,) * 21)
    return ((c["selector_result"], c["pni_count"]) + sel
            + (c["result"], c["record_count"]) + tail)


def _gpc_plan(fixture: str, cases, secs, override=None, obj_override=None,
              force_nodes: int | None = None, admit_by_tick: bool = True) -> Plan:
    objects = _gpc_objects(fixture, secs, cases)
    laid = obj_override(objects) if obj_override is not None else objects
    g, meta = _gpc_world(laid)
    max_total = max(_gpc_case(c)["selector_result"] for c in cases)
    # The `selected` ordinals are recorded once per object, not once per case, so they
    # are the selection on every recorded tick only when every case's own
    # `selector_result` equals their total. That holds on SA2GPC1's 30 cases (14 on all
    # thirty) and fails on SA2GPE3's slope 91, whose selector result runs 5..16 -- there
    # the fixture records no per-tick selection at all, so the node columns are dropped
    # and `pni_count`, which *is* per-case, carries the count. `selected-nodes-dropped`
    # registers the measurement rather than the assertion.
    admit = _gpc_admitted(cases)
    total = sum(len(ob["selected"]) for ob in objects)
    nodes = (total if all(_gpc_case(c)["selector_result"] == total for c in cases) else 0)
    if force_nodes is not None:
        nodes = force_nodes
    enc, exp = [], []
    for c in cases:
        case = _gpc_case(c)
        adm = admit[case["tick"]] if admit_by_tick else tuple(ob["token"] for ob in objects)
        enc.append(_gpc_row(case, objects, meta, max_total, nodes, adm, override).bytes())
        exp.append(_gpc_expected(case, objects, meta, nodes, adm))
    misses = sum(1 for c in cases if _gpc_case(c)["record_count"] < 1)
    cols = (("selector_result", "pni_count")
            + tuple(f"node{i}" for i in range(nodes))
            + ("result", "record_count", "work_w10")
            + tuple(f"output_w{i}" for i in range(13, 33)))
    return Plan(fixture, GPC_SELECTOR, len(cols), cols, enc, exp,
                abi="0x0048BAF0 [esp+4] = &query[7] (radius, point[3], motion[3]); the "
                    "group list is [0x01A5A400] chained by [group], each group's id at "
                    "[group+8], chain ends at [group+10h]/[group+14h] and tolerance at "
                    "[group+28h]; the near-set table is the [0x01A5A2DC] one the "
                    "selector's own call to 0x0047CD60 at 0x0048BB41 fills from the COL "
                    "table [0x01A5A2D4]. 0x0048CE40 eax = the group id matched at "
                    "0x0048CE86, [esp+4] = the 33-word work buffer (radius at [ebx], "
                    "point at [ebx+4], motion at [ebx+10h]); the record count is "
                    "[0x019471B0] and the outputs are [ebx+28h] and [ebx+34h..80h]",
                note="the three object caches are rebuilt from the fixture's own "
                     "`objects` section: 16-word SA2CSE1 nodes whose first two words the "
                     "stager already normalised to ordinals, re-pointered by the driver, "
                     "one group per object chained in token order. The object token is "
                     "the group id this plan chooses, which is what makes the stager's "
                     "`TOKENS = {address: token}` invertible; nothing is taken from the "
                     "expected output. The 1025-word table section is not poked -- the "
                     "image carries it, and the `shipped-table-is-the-image's` control "
                     "says so. `attribute`, `caller_bound` and `caller_iteration` are "
                     "recorded metadata this pair never reads; `attribute-unread` "
                     "measures the first. Work words 7..32 are poisoned before the "
                     "precise call, so on the "
                     f"{misses} rows whose recorded `record_count` is 0 -- where "
                     "0x0048DBF5 `cmp ecx,1 / jl 0048CE91h` returns without touching the "
                     "buffer -- the expectation is the poison, not the recorded zeros.",
                globals_ops=g.bytes())


@builder("ground-contact-composition-v1")
def _gpc1(rows, header, sections=None) -> Plan:
    return _gpc_plan("ground-contact-composition-v1", rows, sections)


@builder("ground-source-cache-contact-expectations-v2")
def _gpe2(rows, header, sections=None) -> Plan:
    return _gpc_plan("ground-source-cache-contact-expectations-v2", rows, sections)


@builder("ground-all-source-contact-expectations-v3")
def _gpe3(rows, header, sections=None) -> Plan:
    return _gpc_plan("ground-all-source-contact-expectations-v3", rows, sections)


@builder("ground-contact-expectations-slope-v1")
def _gpe3s(rows, header, sections=None) -> Plan:
    return _gpc_plan("ground-contact-expectations-slope-v1", rows, sections)


# ---------------------------------------------------------------- 0x004620CA window
# SA2GFL1: the whole chain from the shipped COL table to the ordered candidate list.
# The ordering block at its end is already driven as `ground-object-order-v2`; what was
# missing was the *source* list, and the gates slice judged it to need "the fill loop at
# 0x00462133 ... with the work buffer, the status byte and the polygon cache in it".
# Two of those three are not live-ins at all, and the third is one word:
#
#   0x00462133  edi = [0x01A5A2DC], the near-set table -- *the window loads it itself*,
#               so the fill loop's source is whatever 0x0047CD60 last left there, and
#               0x0047CD60 is a function three fixture families already drive.
#   0x004620CA  dx = the u16 at [0x01DE9484], the near-set count, likewise loaded by the
#               window: entering four instructions earlier than the fill loop makes the
#               count a runtime value instead of one the plan would have to supply, and
#               the fixture does not record it. The only cost is 0x004620D1 `add esp,4`,
#               so the block's own frame is (window entry ESP) + 4.
#   0x0046216C  eax = [edi], the near entry's attribute word; 0x0046216E `test eax,2002h`
#               and 0x0046217F `test eax,1000000h` are the two filters, and 0x00462179
#               `test byte [esi+1Ch],3` gates the second on the player status byte --
#               one byte of one struct, not a struct.
#   0x0046218A  eax = [edi+8], the near entry's third word. 0x0047D0FA `mov [ebp+8],ebx`
#               writes it with ebx, which 0x0047CFF7 `xor ebx,ebx` zeroed and nothing in
#               the gather changes, so it is **0 on every entry this gather produces** and
#               0x0046218F `je 004621A3h` skips the polygon-cache test at 0x00462191
#               entirely. The cache the old blocker priced is not read on this path; the
#               `object-pointer-is-zero` control drives it the other way.
#   0x004621BE  `fld dword [8AF004h]` (the image constant 0.0) / `fcomp dword [ebx]` /
#               `test ah,1` / `je 00462232h`: the *only* read of the work buffer in the
#               whole window, and it gates the precise contact call at 0x004621CC. Both
#               sides of that branch reach the same append at 0x00462232, so the
#               candidate list cannot depend on it -- which is what
#               `contact-gate-does-not-order` measures rather than assumes.
#   0x00462232  the append: ([edi], [edi+4], [edi+8]) copied to [esp+2Ch], which
#               0x00462161 `lea eax,[esp+280h]` started at the block frame + 0x280 --
#               SA2GOO2's own `GOO_SOURCE`. The count goes to [esp+34h], SA2GOO2's own
#               count slot, and 0x0046228E `jne short 004622EEh` is the entry
#               `ground-object-order-v2` already drives.
#
# So the compared columns are SA2GOO2's: the ordered triples at block frame + 0x100 and
# the write cursor spilled to + 0x2C. The triples are not recorded by SA2GFL1 as triples;
# it records COL indices, and the triple a COL record becomes is (attribute, payload, 0),
# the three words 0x00462236..0x00462247 copy -- so the expectation is built from the
# fixture's own `cols` section at the index the fixture names, and nothing is taken from
# an output.
GFL_WINDOW = 0x004620CA
GFL_FRAME = ESP0 - 0x2000
GFL_BLOCK = GFL_FRAME + 4               # 0x004620D1 `add esp,4`
GFL_SLOTS = 4                           # max count across both fixtures is 3
A_GFL_PLAYER = A_ARGS + 0x3800
A_GFL_EBP = A_ARGS + 0x3900
A_GFL_WORK = A_ARGS + 0x2800
GFL_NEAR_BYTES = 0x400 * 0x0C           # the gather's own cap, 0x0047D105 `mov ecx,400h`


def _gfl_globals(header, cols) -> Ops:
    g = Ops()
    g.poke(G_ADMIT_GATE, [1])
    g.poke(G_ADMIT_DESC, [A_DESC])
    g.poke(G_FIRST_COUNT, [0])          # the gather's first loop walks nothing
    g.poke(G_FIRST_BASE, [A_DESC])
    delta = header["relocation_delta"]
    words: list[int] = []
    for c in cols:
        words += [c[1], c[2], c[3], c[4], (c[5] + delta) & 0xFFFFFFFF, 0, 0, c[0]]
    g.poke(A_BIG, words)
    g.poke(G_COL_TABLE, [A_BIG])
    g.poke(G_COL_COUNT, [len(cols)])
    matrix_stack(g, 2, A_MSTACK_FRAMES - 1)
    return g


def _gfl_row(r, header, status: int = 0, work0=None, override=None) -> Ops:
    tick, rx, ry, rz, qx, qy, qz, qr, prior_task, prior_object, _cursor, count, _idx = (
        r[0], *r[1:12], r[12])
    o = Ops()
    reset_arenas(o)
    o.fill(A_BIG_NEAR, GFL_NEAR_BYTES)
    o.fill(A_GFL_PLAYER, 0x200)
    o.fill(A_GFL_EBP, 0x100)
    o.fill(A_GFL_WORK, 4 * 33)
    query = [qx, qy, qz, qr]
    parts = {"query": query, "prior": [prior_task, prior_object],
             "work0": [qr if work0 is None else work0], "status": [status]}
    if override is not None:
        parts = override(parts)
    query, prior = parts["query"], parts["prior"]
    o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
    o.poke(G_NEAR_COUNT, [0])
    # The gather, at its own entry: the same call 0x0048BB41 makes inside the selector,
    # with the same four arguments (0x0048BB28..0x0048BB3E push point[3] then radius).
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, query)
    o.call(0x0047CD60)
    # then the window, over whatever the gather left in [0x01A5A2DC]/[0x01DE9484]
    o.poke(A_GFL_PLAYER + 0x1C, [parts["status"][0]])
    o.poke(A_GFL_PLAYER + 0xA0, [prior[0]])
    o.poke(A_GFL_PLAYER + 0xB0, [prior[1]])
    o.poke(A_GFL_WORK, [parts["work0"][0]])
    o.poke(A_GFL_EBP + 8, [A_GFL_EBP + 0x40, A_GFL_EBP + 0x60, A_GFL_PLAYER])
    o.fill(GFL_FRAME - 0x100, 0x600)
    o.poke(GFL_BLOCK + 0x28, [0])                              # the accumulator
    o.poke(GFL_BLOCK + 0x2C, [POISON])                         # the cursor spill
    o.poke(GFL_BLOCK + 0x100, [POISON] * (3 * GFL_SLOTS))
    # 0x004620D1 `add esp,4` moves the frame before the block runs, so the synthetic
    # `ret` at 0x0046244C pops [GFL_BLOCK], not [GFL_FRAME]: the sentinel goes there.
    o.reg(ESP, GFL_BLOCK).sentinel()
    o.reg(ESP, GFL_FRAME)
    o.reg(ESI, A_GFL_PLAYER).reg(EBX, A_GFL_WORK).reg(EBP, A_GFL_EBP)
    o.call(GFL_WINDOW)
    o.peek(GFL_BLOCK + 0x100, 12 * GFL_SLOTS)
    o.peek(GFL_BLOCK + 0x2C, 4)
    return o


def _gfl_expected(r, header, cols) -> tuple[int, ...]:
    cursor, count, idx = r[10], r[11], r[12]
    delta = header["relocation_delta"]
    words: list[int] = []
    for i in range(GFL_SLOTS):
        if i < count:
            c = cols[idx[i]]
            words += [c[0], (c[5] + delta) & 0xFFFFFFFF, 0]
        else:
            words += [POISON] * 3
    words.append(GFL_BLOCK + 0x100 + 12 * cursor)
    return tuple(words)


def _gfl_plan(fixture: str, rows, header, sections, status: int = 0, work0=None,
              override=None, col_override=None) -> Plan:
    cols = sections["cols"]
    laid = col_override(cols, header, rows) if col_override is not None else cols
    g = _gfl_globals(header, laid)
    enc = [_gfl_row(r, header, status, work0, override).bytes() for r in rows]
    exp = [_gfl_expected(r, header, cols) for r in rows]
    counts = {len(r[12]) for r in rows}
    cols_names = (tuple(f"ord{i}_w{j}" for i in range(GFL_SLOTS) for j in range(3))
                  + ("cursor",))
    return Plan(fixture, GFL_WINDOW, 3 * GFL_SLOTS + 1, cols_names, enc, exp,
                abi="0x0047CD60 cdecl(query[3], query_radius) over the COL table at "
                    "[0x01A5A2D4] for [0x01A5A2CC] records of 0x20 bytes, writing three "
                    "words per admission through [0x01A5A2DC] and counting at "
                    "[0x01DE9484]; then the window 0x004620CA..0x0046244C inside "
                    "GroundCollisionStep 0x00461F10 with ESI = the player, EBX = the "
                    "work buffer, EBP = the frame and ESP = the caller frame, its own "
                    "block frame at ESP+4 (0x004620D1 add esp,4) holding the source "
                    "triples at +0x280, the ordered list at +0x100, the count at +0x34 "
                    "and the cursor at +0x2C",
                note="the near-set table and its count are not supplied: the window "
                     "loads both out of the globals the gather filled, at 0x00462133 and "
                     "0x004620CA. The COL payload word is the fixture's own `object_va` "
                     "plus its `relocation_delta`, which is also what `prior_object` is "
                     "recorded as, so the ordering block's only dereference-free compare "
                     "(0x0046232C `cmp edi,[esi+0A0h]` and 0x00462377/0x004623FE against "
                     "[esi+0B0h]) sees the recorded relation. The list is poisoned to "
                     f"{GFL_SLOTS} slots and all {GFL_SLOTS} are compared, so the slots "
                     "past the count have to come back untouched; recorded counts are "
                     + ", ".join(str(c) for c in sorted(counts)) + ". The player status "
                     "byte at [esi+1Ch] is 0, which SA2GFL1 does not record; "
                     "`player-status-set` drives it the other way. The work buffer's "
                     "word 0 is the recorded query radius, and the contact call it gates "
                     "at 0x004621CC returns 0 at 0x0048CE91 because no group list is laid "
                     "down -- both sides of that branch append, which "
                     "`contact-gate-does-not-order` measures.",
                globals_ops=g.bytes())


@builder("ground-full-chain-v1")
def _gfl1a(rows, header, sections=None) -> Plan:
    return _gfl_plan("ground-full-chain-v1", rows, header, sections)


@builder("ground-full-chain-slope-v1")
def _gfl1b(rows, header, sections=None) -> Plan:
    return _gfl_plan("ground-full-chain-slope-v1", rows, header, sections)


# ---------------------------------------------------------------- 0x004624C0 window
# SA2GCL1 and SA2GEX2: the candidate loop itself, 0x004624C0..0x00463821, entered at its
# own head. The tail half (0x00462F83..) is what the six SA2GTR fixtures and the two
# SA2GEX1 executors already drive; what this window adds is the per-iteration decision,
# and that decision is one call:
#
#   0x004624C0  `fld dword [8AF004h]` -- the image constant 0.0 -- and nothing else is on
#               the x87 stack, so the head is clean.
#   0x004624C6  edi = eax*3, edx = [esp+edi*4+108h] and ecx = [esp+edi*4+104h]: the
#               candidate triple at (caller frame)+0x100 + 12*index, words 2 and 1. EAX
#               is the loop index and the only register live-in besides EBX, EBP and ESP
#               (`window_entry.py` census: rax, rsp, rbx, rbp).
#   0x004624F4  `fcomp dword [ebx]` / `test ah,1` / `je 00463685h`: work word 0 against
#               0.0, the same gate the fill loop has at 0x004621BE.
#   0x0046250A  `mov eax,ecx` / `call 0048CE40h` with [esp+4] = ebx -- the candidate's
#               group id and the work buffer, exactly the pair SA2GPC1 drives. A group id
#               that names no group returns 0 at 0x0048CE91 and the iteration is a miss;
#               a group whose chain contains the face returns 1 and writes the record.
#   0x00463692  `jl 004624C0h` closes the loop, and 0x00463698 `cmp [esp+0B4h],1` picks
#               the finaliser -- both inside this window, which is why the states these
#               two fixtures record *after* the accepted iteration are boundary outputs
#               here and were not at 0x00462F83.
#
# The world the loop walks is SA2GPC1's three object caches, and the join is not
# asserted -- it is checked: SA2GCL1's own tick 118 input work words 0..6 are
# `ground-contact-composition-v1`'s tick 118 `precise_w0..6` for object token 0, and its
# `accepted_state` work words 13..32 are that case's `output_w13..32`, word for word.
# SA2GEX2's two calls are ticks 170 and 171, which is the slope window
# `ground-contact-expectations-slope-v1` and `ground-full-chain-slope-v1` cover.
GCL_WINDOW = 0x004624C0
GCL_STATE = 75
GCL_RESET = 42


def _gcl_state(words, header) -> dict:
    ew, pf, ww = header["entity_words"], header["player_fields"], header["work_words"]
    world = GCL_STATE - ew - pf - ww
    return {"entity": list(words[:ew]), "world": list(words[ew:ew + world]),
            "player": list(words[ew + world:ew + world + pf]),
            "work": list(words[ew + world + pf:])}


def _gcl_token_map(support: int, other: int) -> dict:
    return {0: 0, 1: support, 2: other}


def _gcl_player(values, schema, tokens) -> list[int]:
    return [tokens[v] if i in (40, 44) else v for i, v in zip(schema, values)]


def _gcl_row(state: dict, schema, header, meta, candidates, index: int, bound: int,
             frame: dict, tokens, record, override=None,
             run_selector: bool = True) -> Ops:
    """One call, entered at the *tail* window 0x00462F83 the SA2GTR family already
    drives, and carried through the loop's back edge into the iterations that follow.

    0x00463692 `jl 004624C0h` leaves the 0x00462F83 window. remill emits
    `__remill_missing_block` for that target and `tools/s2/corpus_run/internalize.py`
    folds it into `__sa2_dispatch_stub`, which is `sa2_ft_dispatch(0x004624C0, ...)`:
    once the loop head is itself lifted and in the function table -- which is what
    `lift_set.py --block 0x004624C0:0x00463822` put there -- the back edge *reaches* it
    with the machine state the tail left, and the remaining iterations run. Nothing is
    re-injected between them; the only inputs are this row's own pokes.
    """
    entity, wvec = list(state["entity"]), list(state["world"])
    player = _gcl_player(state["player"], schema, tokens)
    work = list(state["work"])
    cands = list(candidates)
    frame = dict(frame)
    record = list(record)
    if override is not None:
        parts = override({"entity": entity, "world": wvec, "player": player,
                          "work": work, "candidates": cands, "frame": frame,
                          "record": record})
        entity, wvec, player, work = (parts["entity"], parts["world"], parts["player"],
                                      parts["work"])
        cands, frame, record = parts["candidates"], parts["frame"], parts["record"]
    bound = frame.get("bound", bound)
    o = Ops()
    reset_arenas(o)
    o.fill(A_PLAYER, 0x400)
    o.fill(G_RECORDS, RECORD_STRIDE * 8)
    o.fill(G_RECORDS_PUBLIC, 0x58 * 8)
    o.poke(G_RECORD_COUNT, [0])
    o.poke(A_ENTITY, entity)
    o.poke(A_GWORLD, wvec + [0] * (10 - len(wvec)))
    for idx, value in zip(schema, player):
        o.poke(A_PLAYER + 4 * idx, [value])
    o.poke(A_PLAYER + 0x90, [A_GWORK])          # player word 36, the work-buffer pointer
    o.poke(A_GWORK, work)
    # the accepted compact record, where 0x00463385 `movzx eax,word ptr [ebx+2Ah]` /
    # `imul eax,eax,58h` / `add eax,1DDB660h` reads it
    o.poke(G_RECORDS_PUBLIC + 0x58 * ((work[10] >> 16) & 0xFFFF), record)
    o.poke(G_GRAVITY, list(frame["gravity"]))
    o.poke(G_TAIL_CONTACT, [0])
    o.poke(G_TAIL_SURFACE, [0])
    o.poke(G_POLICY_GLOBAL, [0])
    o.poke(G_STAGE_SELECTOR, [frame["selector"]])
    matrix_stack(o, 2, A_MSTACK_FRAMES - 1)
    # the caller's ordered candidate list, at the offset 0x004624C9 indexes it by
    o.poke(GTR_CF + 0x100, [POISON] * (3 * max(len(cands), 4)))
    for i, (attribute, token) in enumerate(cands):
        o.poke(GTR_CF + 0x100 + 12 * i, [attribute, meta["id"][token], 0])
    triple = GTR_CF + 0x100 + 12 * index
    o.poke(GTR_CF + 0x10, [A_GWORK + 0x48])
    o.poke(GTR_CF + 0x18, [triple])
    o.poke(GTR_CF + 0x1C, [frame["policy"]])
    o.poke(GTR_CF + 0x24, [A_GWORK])
    o.poke(GTR_CF + 0x28, [frame["accumulator"]])
    o.poke(GTR_CF + 0x2C, [0 if frame["moving_support"] == 0 else meta["id"][cands[index][1]]])
    o.poke(GTR_CF + 0x30, [A_ENTITY + 0x14])
    o.poke(GTR_CF + 0x34, [bound])
    o.poke(GTR_CF + 0x44, [A_GWORK + 0x1C])
    o.poke(GTR_CF + 0x4C, list(frame["frame_4c"]))
    o.poke(GTR_CF + 0x58, list(frame["frame_58"]))
    o.poke(GTR_CF + 0x64, [index])
    o.poke(GTR_CF + 0x68, [A_GWORK + 4])
    o.poke(GTR_CF + 0x6C, [A_GWORK + 0x10])
    o.poke(GTR_CF + 0x74, [tokens[frame["support"]]])
    o.poke(GTR_CF + 0xB4, [frame["accepted"]])
    # Caller-frame words 49..51, the start position the rejected-contact response reads
    # through `0x0046366E lea ecx,[esp+0E4h]` (eight pushes below CF, so CF+0xC4).
    # 0x004620A8..0x004620BE `mov [esp+0C8h],eax` / `[esp+0CCh],edx` / `[esp+0D0h],eax`
    # write it once before the loop -- at ESP = CF-4, so CF+0xC4/0xC8/0xCC -- and nothing
    # in this window writes it again, so it is a live-in of the loop and SA2GEX2 records
    # it as the rejected iteration's own `stack_w49..51`. It is read on no other path:
    # `start-position-unread-without-a-rejection` measures that.
    o.poke(GTR_CF + 0xC4, list(frame["start_position"]))
    # 0x0048CE9A `mov edx,[eax+24h]` / `mov eax,[eax+1Ch]` reads each group's selected
    # face list, and only 0x0048BAF0 fills it: 0x0048BCB5 writes the pointers from
    # 0x0194F030 up, 0x0048BCAE sets [group+1Ch] and 0x0048BCB1 counts at [group+24h].
    # GroundCollisionStep calls the selector once per call at 0x004620C5 with the work
    # buffer as the query block, so this row runs it first, with the entered state's own
    # work words 0..6 -- which are the selector's query, unchanged at this boundary
    # (SA2GPC1's and SA2GPE3's `selector_w0..6` for these ticks are those same seven
    # words). Without it every group's list is empty and every iteration misses at
    # 0x0048DBF5, which is what `selector-fills-the-face-lists` shows.
    o.poke(G_SELECTED_LIST, [0] * 0xC0)
    o.fill(A_BIG_NEAR, 0x100)
    if not run_selector:
        cands_for_selector = []
    else:
        cands_for_selector = cands
    o.fill(A_GPC_COL, COL_STRIDE * max(len(cands_for_selector), 1))
    for i, (_attribute, token) in enumerate(cands_for_selector):
        rec = A_GPC_COL + COL_STRIDE * i
        o.poke(rec, work[1:4])
        o.poke(rec + 0x0C, [FLT_MAX_BITS])
        o.poke(rec + 0x10, [meta["id"][token]])
        o.poke(rec + 0x1C, [1])
    o.poke(G_COL_TABLE, [A_GPC_COL])
    o.poke(G_COL_COUNT, [len(cands_for_selector)])
    o.poke(G_NEAR_CURSOR, [A_BIG_NEAR])
    o.poke(G_NEAR_COUNT, [0])
    o.poke(A_QUERY, work[:7])
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_QUERY])
    o.call(GPC_SELECTOR)
    o.poke(GTR_EBP + 8, [A_ENTITY, A_GWORLD, A_PLAYER])
    o.reg(ESP, GTR_EBP + 4).sentinel()          # `mov esp,ebp; pop ebp; ret` takes [ebp+4]
    o.reg(ESP, GTR_CF - 0x14).reg(EBP, GTR_EBP)  # the window's first op is `add esp,14h`
    o.reg(EAX, frame["result"]).reg(EBX, A_GWORK)
    o.reg(ESI, A_GWORLD).reg(EDI, A_ENTITY)
    o.call(GTR_WINDOW)
    o.peek(A_ENTITY, 4 * header["entity_words"])
    o.peek(A_GWORLD, 4 * len(wvec))
    for idx in schema:
        o.peek(A_PLAYER + 4 * idx, 4)
    o.peek(A_GWORK, 4 * header["work_words"])
    o.reg(ESP, ESP0).sentinel()
    o.poke(ESP0 + 4, [A_PLAYER])
    o.reg(EAX, A_ENTITY).reg(EBX, A_GWORLD)
    o.call(GTR_PRESET)
    o.peek(A_ENTITY, 4 * header["entity_words"])
    o.peek(A_GWORLD, 4 * len(wvec))
    for idx in schema:
        o.peek(A_PLAYER + 4 * idx, 4)
    return o


def _gcl_expected(final: dict, reset: dict, schema, tokens) -> tuple[int, ...]:
    return tuple(final["entity"] + final["world"]
                 + _gcl_player(final["player"], schema, tokens) + final["work"]
                 + reset["entity"] + reset["world"]
                 + _gcl_player(reset["player"], schema, tokens))


def _gcl_columns(schema, header, world: int) -> tuple[str, ...]:
    cols = [f"entity_w{i}" for i in range(header["entity_words"])]
    cols += [f"world_w{i}" for i in range(world)]
    cols += [f"player_w{i}" for i in schema]
    cols += [f"work_w{i}" for i in range(header["work_words"])]
    cols += [f"reset_entity_w{i}" for i in range(header["entity_words"])]
    cols += [f"reset_world_w{i}" for i in range(world)]
    cols += [f"reset_player_w{i}" for i in schema]
    return tuple(cols)


def _gcl_objects(obj_override=None) -> tuple[Ops, dict, dict]:
    """SA2GPC1's three object caches as three guest groups, and the attribute -> token
    map the loop's candidate triples are read through. The attributes are distinct
    (0x40000001, 0x40400001, 0x20000001), so the map is a bijection and the fixture's own
    per-iteration attribute word names the object without anything being assumed."""
    import fixture_rows as _FR
    _h, _cases, secs = _FR.load("ground-contact-composition-v1")
    objects = secs["objects"]
    by_attribute = {ob["attribute"]: ob["token"] for ob in objects}
    if len(by_attribute) != len(objects):
        raise SystemExit("the object attributes are not distinct")
    g, meta = _gpc_world(obj_override(objects) if obj_override is not None else objects)
    return g, meta, by_attribute


def _gcl_frame_triples(tick: int, entity, attribute: int) -> tuple[list, list]:
    """The two frame triples at (caller frame)+0x4C and +0x58.

    0x00461F74 `fst [esp+54h]` / 0x00461F85 `fstp [esp+4Ch]` / 0x00461FA5 `fstp [esp+50h]`
    write the first in the routine's prologue, 0x00461F99/0x00461FA1/0x00461FAD copy it
    to the second and 0x00461FD3 `call 00468FA0h` rewrites the copy -- all of it *before*
    0x004624C0, so both are live-ins at the loop head and neither SA2GCL1 nor SA2GEX2
    records them. `ground-policy-word-v1` and `-slope-v1` do, as their own case words
    20..25, on exactly these ticks; this takes them from there, matched by (tick,
    attribute) and checked against the eight entity words the two fixtures both record.
    """
    import fixture_rows as _FR
    for fid in ("ground-policy-word-v1", "ground-policy-word-slope-v1"):
        _h, rows, _s = _FR.load(fid)
        for r in rows:
            if r[1] != tick or r[18] != attribute:
                continue
            if list(r[3:11]) != list(entity):
                raise SystemExit(f"{fid} tick {tick}: its entity is not the loop's")
            return list(r[20:23]), list(r[23:26])
    raise SystemExit(f"no SA2GPW1 case for tick {tick} attribute 0x{attribute:08x}")


def _gcl_candidates(tick: int, by_attribute: dict) -> list[tuple[int, int]]:
    """The caller's ordered candidate list for one tick, as (attribute, object token).

    SA2GCL1 records only the *accepted* candidate's attribute, so the rest of the list
    comes from the sibling that records the whole of it on the same ticks:
    `ground-full-chain-v1` for the 118..130 window and `ground-full-chain-slope-v1` for
    160..200, whose `ordered COL indices` are the very list the window indexes at
    0x004624C9 -- and which this corpus now drives at 0x004620CA. SA2GEX2 records each
    iteration's own attribute and needs no sibling; `loop-candidate-order` drives it from
    the sibling anyway and shows the two agree.
    """
    import fixture_rows as _FR
    for fid in ("ground-full-chain-v1", "ground-full-chain-slope-v1"):
        header, rows, secs = _FR.load(fid)
        for r in rows:
            if r[0] != tick:
                continue
            return [(secs["cols"][i][0], by_attribute[secs["cols"][i][0]]) for i in r[12]]
    raise SystemExit(f"no SA2GFL1 tick {tick}")


@builder("ground-candidate-loop-v1")
def _gcl1(rows, header, sections=None, override=None, obj_override=None,
          run_selector: bool = True) -> Plan:
    schema = [s[0] for s in sections["player_schema"]]
    g, meta, by_attribute = _gcl_objects(obj_override)
    world = GCL_STATE - header["entity_words"] - header["player_fields"] - header["work_words"]
    enc, exp = [], []
    for row in rows:
        sem = list(row[2:18])
        state = _gcl_state(row[18:93], header)
        final = _gcl_state(row[344:419], header)
        reset_words = list(row[419:461])
        reset = {"entity": reset_words[:header["entity_words"]],
                 "world": reset_words[header["entity_words"]:header["entity_words"] + world],
                 "player": reset_words[header["entity_words"] + world:]}
        bound, index = sem[6], sem[7]
        cands = _gcl_candidates(row[1], by_attribute)
        if len(cands) != bound:
            raise SystemExit(f"tick {row[1]}: {len(cands)} candidates, bound {bound}")
        if cands[index][0] != sem[0]:
            raise SystemExit(f"tick {row[1]}: candidate {index} is not the accepted "
                             f"attribute 0x{sem[0]:08x}")
        support = meta["id"][cands[index][1]]
        other = next(meta["id"][t] for _a, t in cands if meta["id"][t] != support)
        tokens = _gcl_token_map(support, other)
        f4c, f58 = _gcl_frame_triples(row[1], state["entity"], sem[0])
        frame = {"policy": sem[1], "accumulator": sem[2], "result": sem[3],
                 "moving_support": sem[5], "accepted": sem[8], "support": sem[9],
                 "gravity": sem[10:13], "selector": sem[15],
                 "frame_4c": f4c, "frame_58": f58, "start_position": [0, 0, 0]}
        enc.append(_gcl_row(state, schema, header, meta, cands, index, bound, frame,
                            tokens, list(row[93:115]), override, run_selector).bytes())
        exp.append(_gcl_expected(final, reset, schema, tokens))
    return Plan("ground-candidate-loop-v1", GTR_WINDOW,
                GCL_STATE + GCL_RESET, _gcl_columns(schema, header, world), enc, exp,
                abi="window 0x004624C0..0x00463821 inside GroundCollisionStep 0x00461F10, "
                    "entered at the loop head the way 0x00463692 `jl 004624C0h` enters "
                    "it: EAX = the loop index, EBX = the work buffer, EBP = the frame "
                    "whose +8/+0Ch/+10h are entity, world and player, ESP = the caller "
                    "frame, whose +0x34 is the bound, +0x64 the index and +0x100 the "
                    "ordered candidate triples; the three object caches are guest groups "
                    "in [0x01A5A400] and each triple's word 1 is its group's id, which "
                    "0x0046250A hands 0x0048CE40 in eax",
                note="the compared columns are the state at the window's own exits "
                     "(`final_state`) and the same three blocks after PResetPosition "
                     "(`reset_state`). `accepted_state`, `continued_state` and "
                     "`missed_state` are mid-loop snapshots -- they are produced at "
                     "0x00463685 and 0x004637E7, which are inside the window, not at an "
                     "exit -- so they are not compared here; they are the same words the "
                     "six SA2GTR fixtures and the two SA2GEX1 executors already compare "
                     "at 0x00462F83. The loop's initial frame (policy, accumulator, "
                     "accepted flag, support) is 0/0/0/0: 0x00462140..0x00462157 zero "
                     "[esp+0B4h], [esp+10h], [esp+74h], [esp+18h], [esp+34h] and "
                     "[esp+64h] with eax = 0 before the loop starts, and SA2GEX2 records "
                     "exactly that quadruple for both of its calls.",
                globals_ops=g.bytes())


@builder("ground-executor-multi-v1")
def _gex2(rows, header, sections=None, override=None, obj_override=None,
          run_selector: bool = True) -> Plan:
    """SA2GEX2: the two three-iteration calls the SA2GEX1 executors exclude.

    Each call is entered at its first iteration's tail -- both are accepted hits, so both
    carry a tail payload -- and the loop's own back edge runs the other two iterations
    against SA2GPC1's three object caches. What the fixture's `exit_state` words say
    about those iterations is not fed back in; only `final_state` and `reset_state`, the
    two blocks at the call's own exit, are compared.
    """
    schema = [s[0] for s in sections["player_schema"]]
    g, meta, by_attribute = _gcl_objects(obj_override)
    world = GCL_STATE - header["entity_words"] - header["player_fields"] - header["work_words"]
    enc, exp, note_calls = [], [], []
    for call in sections["calls"]:
        first = call["iterations"][0]
        if first["kind"] != 1:
            raise SystemExit(f"tick {call['tick']}: the first iteration is not an "
                             "accepted hit, so it carries no tail payload")
        sem = first["semantic"]
        state = _gcl_state(first["input_state"], header)
        final = _gcl_state(call["final_state"], header)
        reset_words = call["reset_state"]
        reset = {"entity": reset_words[:header["entity_words"]],
                 "world": reset_words[header["entity_words"]:header["entity_words"] + world],
                 "player": reset_words[header["entity_words"] + world:]}
        cands = [(it["attribute"], by_attribute[it["attribute"]])
                 for it in call["iterations"]]
        index, bound = sem[7], sem[6]
        if bound != call["bound"] or len(cands) != bound:
            raise SystemExit(f"tick {call['tick']}: bound {bound} is not {call['bound']}")
        last_accepted = max(i for i, it in enumerate(call["iterations"]) if it["kind"] == 1)
        support = meta["id"][cands[last_accepted][1]]
        entered = meta["id"][cands[index][1]]
        if sem[9] != (1 if entered == support else 2):
            raise SystemExit(f"tick {call['tick']}: support token {sem[9]} does not "
                             "match the entered candidate")
        other = entered if entered != support else next(
            meta["id"][t] for _a, t in cands if meta["id"][t] != support)
        tokens = _gcl_token_map(support, other)
        f4c, f58 = _gcl_frame_triples(call["tick"], state["entity"], sem[0])
        frame = {"policy": sem[1], "accumulator": sem[2], "result": sem[3],
                 "moving_support": sem[5], "accepted": sem[8], "support": sem[9],
                 "gravity": sem[10:13], "selector": sem[15],
                 "frame_4c": f4c, "frame_58": f58,
                 "start_position": next((it["response"][5:8]
                                         for it in call["iterations"] if it["kind"] == 2),
                                        [0, 0, 0])}
        enc.append(_gcl_row(state, schema, header, meta, cands, index, bound, frame,
                            tokens, first["record"], override, run_selector).bytes())
        exp.append(_gcl_expected(final, reset, schema, tokens))
        note_calls.append(f"tick {call['tick']}: "
                          + "/".join({0: "miss", 1: "accepted", 2: "rejected"}[it["kind"]]
                                     for it in call["iterations"]))
    return Plan("ground-executor-multi-v1", GTR_WINDOW,
                GCL_STATE + GCL_RESET, _gcl_columns(schema, header, world), enc, exp,
                abi="the same entry as the six SA2GTR fixtures, 0x00462F83 with ESP = "
                    "the caller frame minus 0x14, carried through 0x00463692 "
                    "`jl 004624C0h` into the lifted loop head: the ordered candidate "
                    "triples are at (caller frame)+0x100, each one's word 1 the group id "
                    "0x0046250A hands 0x0048CE40 in eax",
                note="one row per call, entered at its first iteration -- an accepted hit "
                     "in both, so both carry a tail payload -- with the other iterations "
                     "run by the guest: " + "; ".join(note_calls) + ". The per-iteration "
                     "`head_state`, `acceptance`, `response` and `exit_state` blocks are "
                     "not fed in and not compared: they are mid-loop, at 0x00463685 and "
                     "0x00462C19, and the two blocks this window does end on are "
                     "`final_state` and `reset_state`. The candidate order is the "
                     "fixture's own, one attribute per iteration, mapped to SA2GPC1's "
                     "three caches through their distinct attribute words; the two frame "
                     "triples at +0x4C and +0x58 come from `ground-policy-word-slope-v1` "
                     "on the same ticks, matched on the eight entity words.",
                globals_ops=g.bytes())
