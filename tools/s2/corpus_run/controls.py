#!/usr/bin/env python3
"""Negative controls: prove a passing fixture compared something.

A plan that supplies an input the guest never reads, or peeks a word the guest never
writes, passes without comparing anything. The corpus has two ways to be vacuous and
this tool tests for both:

  break    perturb one input word the disassembly says a named output column depends on
           and re-drive. A control passes when the run *fails* AND at least one column
           it moves is one the lifted code decides. A `break` control that still passes
           the fixture means the column is not a function of that input -- either the
           plan is not feeding it or the column is not being compared.

  inert    perturb an input the disassembly says this window never reads. A control
           passes when the run still passes bit for bit. An `inert` control that fails
           means the window reads a word the survey said it does not.

  probe    no assertion; drive a variant and report which columns came back holding the
           0xDEADBEEF poison. This is how "the step writes these twelve words" was
           measured rather than assumed.

Each control names the instruction its claim rests on, and the report carries that
string, so a reader can check the claim against the image without re-reading this file.

Two things a control could be and must not be (G2b findings 3 and 4)
-------------------------------------------------------------------

**A no-op.** Eight of the 279 asserting controls encoded a plan byte-identical to the
baseline's: two written `player=lambda v: v`, two perturbing a word the plan never pokes,
and four naming a substitution that never fired because no recorded word held the value
it looked for. Each of them "held" without ever driving anything different. `run_controls`
now compares the control's encoded plan against the baseline's and raises, so this class
cannot come back.

**A break that moves nothing the lift produced.** A plan pokes a word and peeks it again;
perturb the poke and the peek diverges whether or not the call ran. That divergence is
about the plan, not about the lifter. Every `break` control is therefore driven twice --
once normally and once with every OP_CALL stripped from its own rows -- and its
divergences are split:

  call_dependent_divergences    the control's value differs from its own no-call value:
                                the lifted code responded to the perturbation
  call_independent_divergences  the control's value equals its own no-call value: the
                                plan poked it and read it straight back

A `break` holds when the run fails and at least one moved column is either
call-dependent, or one the baseline's own no-call run shows the lifted code writes
(`run.no_call_report`'s `informative_column_names`). The second clause is needed for the
controls whose break is "the code now writes nothing": perturbing the query point of
`ground-full-chain-v1` leaves the ordered slots holding the plan's own poison, which the
no-call run also leaves there -- but the baseline shows the block fills them, so the
control is about the lift after all.

The G2 interrogation reported six `break` controls as circular on the ground that every
column they move matches bit-for-bit with the call removed in the BASELINE. Measured with
the control's own no-call run, none of the six is: `support-pointer-drives-player_w44` on
`ground-tail-final-v5` comes back 0x03003600 with the call and 0x03003500 without it, and
0x03003600 is a value no op in that plan ever pokes into the player block, so
0x004637F2 `mov [eax+0B0h],ecx` did execute. What is true, and is now recorded per fixture
by `run.py` instead of argued, is that the BASELINE pass of those columns is uninformative:
the window stores the same value the plan handed it, so the control is the only evidence
for them. The stated mechanism -- "`_gtr_player` maps the recorded tokens on both the
input and the expected side, so the perturbation changes what the plan expects" -- is not
what those controls do: none of them touches `player` or the expected side.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/controls.py [--only FIXTURE ...] [--variant pc24]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import fixture_rows as FR   # noqa: E402
import plans as P           # noqa: E402
import run as R             # noqa: E402

OUT = ROOT / "private" / "generated" / "s2" / "corpus_run"

CONTROLS: dict[str, list[dict]] = {}


def control(fixture: str, name: str, kind: str, basis: str):
    """Register a control. The wrapped function takes (rows, header, sections) -> Plan."""
    def deco(fn):
        CONTROLS.setdefault(fixture, []).append(
            {"name": name, "kind": kind, "basis": basis, "build": fn})
        return fn
    return deco


def flip(word: int) -> int:
    """A perturbation that is a different float and a different integer, and never a NaN
    payload the comparison could confuse with a trap: toggle the low mantissa bit."""
    return word ^ 1


def bump(word: int) -> int:
    """A perturbation big enough to survive a float add. `flip` toggles the low mantissa
    bit, which rounds away when the perturbed operand is far smaller than the one it is
    added to -- `player_w21-drives-work_w6` held on 5 of 13 flat rows and on none of the
    41 slope rows for exactly that reason, because the recorded operands are -0.0. This
    one replaces the word with 3.14159f (or 3.14158f when it already is)."""
    return 0x4048F5C3 if word == 0x40490FDB else 0x40490FDB


# ---------------------------------------------------------------- SA2GQS3
def _gqs_override(**kw):
    """Build an override for `plans._gqs_row`: name a slot and a transform."""
    def over(entity, world, player, player_19_21, gravity, work):
        parts = {"entity": entity, "world": world, "player": player,
                 "player_19_21": player_19_21, "gravity": gravity, "work": work}
        for key, fn in kw.items():
            parts[key] = fn(list(parts[key]))
        return (parts["entity"], parts["world"], parts["player"], parts["player_19_21"],
                parts["gravity"], parts["work"])
    return over


def _set(index: int, transform=flip):
    def fn(values):
        values[index] = transform(values[index]) & 0xFFFFFFFF
        return values
    return fn


def _gqs_control(fixture: str):
    def make(name, kind, basis, **kw):
        @control(fixture, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _f=fixture):
            return P._gqs_plan(_f, rows, header, sections, override=_gqs_override(**_kw))
    return make


for _fx in ("precise-ground-query-seed-v1", "ground-query-seed-slope-v1"):
    _mk = _gqs_control(_fx)
    # entity word 5 is the query point's X source: 0x0046203B `fld dword ptr [eax+14h]`
    # (eax = [ebp+8], byte 0x14 = word 5) / 0x0046204D `fstp dword ptr [edx]` (edx = ebx+4).
    _mk("entity_w5-drives-work_w1", "break",
        "0x0046203B fld [eax+14h] / 0x0046204D fstp [ebx+4]", entity=_set(5))
    # player word 77 is the query radius source: 0x00462071 `fld dword ptr [esi+134h]`
    # (byte 0x134 = word 77) / 0x00462077 `fmul qword [904E98h]` / 0x0046207D `fstp [ebx]`.
    _mk("player_w77-drives-work_w0", "break",
        "0x00462071 fld [esi+134h] / 0x0046207D fstp [ebx]", player=_set(20))
    # world word 1 is the motion Y source: 0x00462083 `mov edi,[edx+4]` / 0x00462086
    # `mov [eax+4],edi` (eax = ebx+0x10), then 0x00462099 `fadd [eax+4]`.
    _mk("world_w1-drives-work_w5", "break",
        "0x00462083 mov edi,[edx+4] / 0x00462086 mov [ebx+14h],edi", world=_set(1))
    # player word 21 is the motion Z bias: 0x0046209F `fld dword ptr [esi+54h]`
    # (byte 0x54 = word 21) / 0x004620A5 `fstp dword ptr [eax+8]`.
    _mk("player_w21-drives-work_w6", "break",
        "0x0046209F fld [esi+54h] / 0x004620A5 fstp [ebx+18h]",
        player_19_21=_set(2, bump))
    # player word 49 is the first thing the centre-correction helper reads:
    # 0x00464683 `fld dword ptr [ecx+0C4h]`, with ecx = [ebp+10h] set at 0x0046201A.
    # This control was written as part of the "unread" set below and failed at 40/41,
    # which is how the read was found; the claim is now the other way round.
    _mk("player_w49-drives-work_w2", "break",
        "0x00464683 fld [ecx+0C4h], the first instruction of the helper 0x00462023 calls",
        player=_set(14, bump))
    # world words 3..9 are never read: the only load through [ebp+0Ch] in this call is
    # 0x0046207F/0x00462083/0x00462089 `mov edi,[edx]` / `[edx+4]` / `[edx+8]`.
    _mk("world_w3..9-unread", "inert",
        "the only reads through [ebp+0Ch] are 0x0046207F/83/89, words 0..2",
        world=lambda v: v[:3] + [flip(x) for x in v[3:]])
    # player words 59, 63, 70 and 78 (schema slots 16, 18, 19, 21): no instruction
    # reachable from 0x00461F10 on this path names [esi+0ECh], [esi+0FCh], [esi+118h]
    # or [esi+138h]. Measured inert on every row of both fixtures.
    _mk("player-movement-params-unread", "inert",
        "no instruction on the taken path names [esi+0ECh], [esi+0FCh], [esi+118h] "
        "or [esi+138h]",
        player=lambda v: [bump(x) if i in (16, 18, 19, 21) else x
                          for i, x in enumerate(v)])

# Player words 58 and 61 sit on a branch *inside* 0x00464680: 0x004648D0
# `fld dword ptr [ecx+0E8h]` and 0x004647FE / 0x00464906 `fld` / `fdiv [ecx+0F4h]`.
# Thirteen flat ticks never take it and two of the forty-one slope ticks do, which is
# the same word pair asserted both ways -- the clearest evidence that the plan feeds the
# player struct at the offsets the helper reads rather than at offsets nothing reads.
_gqs_control("precise-ground-query-seed-v1")(
    "player_w58_w61-branch-not-taken", "inert",
    "0x004648D0 fld [ecx+0E8h] and 0x004647FE/0x00464906 [ecx+0F4h], on a branch no flat "
    "tick takes", player=lambda v: [bump(x) if i in (15, 17) else x for i, x in enumerate(v)])
_gqs_control("ground-query-seed-slope-v1")(
    "player_w58_w61-branch-taken", "break",
    "0x004648D0 fld [ecx+0E8h] and 0x004647FE/0x00464906 [ecx+0F4h]; two of the forty-one "
    "slope ticks take that branch and their work_w2 moves",
    player=lambda v: [bump(x) if i in (15, 17) else x for i, x in enumerate(v)])


for _fx, _kind in (("precise-ground-query-seed-v1", "inert"),
                   ("ground-query-seed-slope-v1", "break")):
    # The two words the candidate tail rewrites (0x0046312F `mov [ebx+34h],eax` and
    # 0x00463180 `mov [ebx+38h],edx`, with 0x0046317D/0x004631CF/0x004631D9 beside them)
    # are dropped from the compared set by `plans.GQS_LATE`. Comparing them anyway is an
    # `inert` control on the flat fixture, whose native ticks all left the seed's write
    # standing, and a `break` control on the slope fixture, whose recorded head work
    # holds the tail's value on the six ticks where the entity word moved.
    @control(_fx, "compare-the-two-late-words", _kind,
             "0x00461F6E mov [ebx+34h],edx and 0x00461F78 mov [ebx+38h],eax are overwritten "
             "at 0x0046312F/0x0046317D/0x004631CF and 0x00463100/0x00463180/0x004631D9")
    def _gqs_late(rows, header, sections, _f=_fx):
        return P._gqs_plan(_f, rows, header, sections, compare_late=True)


@control("precise-ground-query-seed-v1", "poison-every-work-word", "probe",
         "which of the 33 work words does 0x00461F10 itself write?")
def _gqs_probe(rows, header, sections):
    return P._gqs_plan("precise-ground-query-seed-v1", rows, header, sections,
                       override=_gqs_override(work=lambda v: [P.POISON] * 33))


@control("ground-query-seed-slope-v1", "poison-every-work-word", "probe",
         "which of the 33 work words does 0x00461F10 itself write?")
def _gqs_probe_slope(rows, header, sections):
    return P._gqs_plan("ground-query-seed-slope-v1", rows, header, sections,
                       override=_gqs_override(work=lambda v: [P.POISON] * 33))


# ---------------------------------------------------------------- SA2GOO2
def _goo_override(**kw):
    def over(count, prior_task, prior_object, src):
        parts = {"count": count, "prior_task": prior_task, "prior_object": prior_object,
                 "src": src}
        for key, fn in kw.items():
            parts[key] = fn(parts[key])
        return parts["count"], parts["prior_task"], parts["prior_object"], parts["src"]
    return over


for _fx in ("ground-object-order-v2", "ground-object-order-slope-v1"):
    def _goo(name, kind, basis, _f=_fx, **kw):
        @control(_f, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _ff=_f):
            return P._goo_plan(_ff, rows, override=_goo_override(**_kw))

    # `prior_object` is the whole reason the reverse walk moves an entry to the end:
    # 0x004623FE `mov edx,[esi+0B0h]` / 0x00462404 `cmp edx,[eax+4]` / `jne 0046242Dh`.
    _goo("prior_object-zero", "break",
         "0x004623FE mov edx,[esi+0B0h] / 0x00462404 cmp edx,[eax+4]",
         prior_object=lambda v: 0)
    # `prior_task` picks the walk: 0x0046232C `cmp dword ptr [esi+0A0h],0` /
    # `je 004623CAh`. Every recorded call has it 0 and takes the reverse walk.
    _goo("prior_task-one", "break",
         "0x0046232C cmp dword ptr [esi+0A0h],0 / je 004623CAh",
         prior_task=lambda v: 1)
    # The fourth word of each recorded entry is the stager's own COL index, not part of
    # the triple: the block moves twelve bytes (0x0046242D..0x0046243A).
    # This was `source-fourth-word-unread`, which flipped word 3 of every source entry --
    # a word `_goo_plan` never pokes, because the guest's source stride is twelve bytes
    # and `o.poke(GOO_FRAME + GOO_SOURCE + 12 * i, list(e)[:3])` writes three. The encoded
    # plan was byte-identical to the baseline's and the control "held" without driving
    # anything different (G2b finding 3). The claim about the fourth word is untestable:
    # there is no fourth word in guest memory to move. What IS testable at the same place
    # is the walk's own bound, so that is what this control now asserts.
    _goo("source-past-the-count-unread", "inert",
         "0x004623CA `add ecx,0FFFFFFFFh` starts the reverse walk at count-1 and "
         "0x00462445 `sub eax,0Ch` walks it down to index 0, so the source triple at index "
         "`count` is never loaded; this control writes a fabricated triple there",
         src=lambda rows: [list(e) for e in rows] + [[0x40490FDB] * 3 + [0]])

    # The promotion flag is the one live-in neither fixture records. Driving it as 1 at
    # every slot index the lists can hold says what the unrecorded value would have to
    # be: only 0 reproduces the record.
    for _slot in range(P.GOO_SLOTS):
        @control(_fx, f"promotion-flag-1-slot-{_slot}", "break",
                 "0x004622F0 cmp dword ptr [esp+10h],1; the flag is the only live-in "
                 "SA2GOO2 does not record, and this drives the alternative")
        def _flagged(rows, header, sections, _ff=_fx, _s=_slot):
            return P._goo_plan(_ff, rows, flag=1, slot=_s)


# ---------------------------------------------------------------- SA2GPW1
_GPW_SLOTS = ("entity", "player", "world", "attribute", "support_matches", "frame_4c",
              "frame_58", "global244", "gravity", "work")


def _gpw_override(**kw):
    def over(*values):
        parts = dict(zip(_GPW_SLOTS, values))
        for key, fn in kw.items():
            parts[key] = fn(parts[key])
        return tuple(parts[k] for k in _GPW_SLOTS)
    return over


for _fx, _angle in (("ground-policy-word-v1", False),
                    ("ground-policy-word-slope-v1", True)):
    def _gpw(name, kind, basis, _f=_fx, _a=_angle, **kw):
        @control(_f, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _ff=_f, _aa=_a):
            return P._gpw_plan(_ff, rows, with_angle=_aa, override=_gpw_override(**_kw))

    # Every `break` below was chosen from a sweep that perturbs one recorded column at a
    # time; the sweep is what says which columns the window consumes, and these are the
    # ones it consumes on both fixtures.
    #
    #   work_w21  [ebx+54h] 0x0046254A / 0x004628E3's neighbours in the bit-8 block
    #   work_w22  [ebx+58h] 0x0046255A          work_w23  [ebx+5Ch] 0x00462567
    #   work_w27  [ebx+6Ch] 0x0046253F          entity_w5 [eax+14h] through [esp+30h]
    #   entity_w7 [eax+1Ch] 0x00462598          [esp+58h] / [esp+60h] case words 23 and 25
    for _name, _basis, _slot, _idx in (
            ("work_w21", "[ebx+54h], 0x0046254A fadd dword ptr [ebx+54h]", "work", 21),
            ("work_w22", "[ebx+58h], 0x0046255A fadd dword ptr [ebx+58h]", "work", 22),
            ("work_w23", "[ebx+5Ch], 0x00462567 fadd dword ptr [ebx+5Ch]", "work", 23),
            ("work_w27", "[ebx+6Ch], 0x0046253F fld dword ptr [ebx+6Ch]", "work", 27),
            ("entity_w5", "[eax+14h] through [esp+30h], 0x0046254D/0x00462551", "entity", 5),
            ("entity_w7", "[eax+1Ch], 0x00462598 fsub dword ptr [eax+1Ch]", "entity", 7),
            ("frame_58_w0", "[esp+58h], 0x004628E3 fadd dword ptr [esp+58h]", "frame_58", 0),
            ("frame_58_w2", "[esp+60h], 0x0046290B fadd dword ptr [esp+60h]", "frame_58", 2)):
        _gpw(f"{_name}-drives-the-policy", "break", _basis,
             **{_slot: (lambda v, _i=_idx: [bump(x) if k == _i else x
                                            for k, x in enumerate(v)])})

    # The field the registry calls missing. `[esp+74h]` is compared with `[edi+4]` at
    # 0x00462536 and the compare is reached only when `test dl,cl` at 0x0046252A falls
    # through -- entity word 1 odd, which is 0 of the 13 flat cases and 2 of the 43 slope
    # cases. Inverting the recorded relation leaves every compared word unchanged on both
    # fixtures, so the recorded output does not depend on the collapsed pointer pair at
    # all: not a dropped field, an unread one.
    _gpw("support_matches-inverted", "inert",
         "0x0046252A test dl,cl / je 00462665h guards 0x00462536; entity word 1 is even on "
         "13 of 13 flat and 41 of 43 slope cases, and on the two that reach it the recorded "
         "policy is the same either way",
         support_matches=lambda v: 0 if v else 1)
    # The same guard covers the attribute bit, the global and the [esp+4Ch] triple: every
    # one of them is read only inside the bit-1 block.
    _gpw("attribute-bit-0x100", "inert",
         "0x004625CC test dword ptr [edi],100h, inside the bit-1 block",
         attribute=lambda v: v ^ 0x100)
    _gpw("global-0x01A5A244-one", "inert",
         "0x00462542 cmp [1A5A244h],edx, inside the bit-1 block", global244=lambda v: 1)
    # [esp+4Ch..54h] is read at 0x00462573/0x00462583/0x00462594, inside the bit-1
    # block's second arm, which only the two slope cases whose entity word 1 is odd
    # reach -- and on those two the compared words do not move either way. Measured
    # inert under both perturbations this file has, `bump` and zero.
    _gpw("frame_4C-triple-zeroed", "inert",
         "[esp+4Ch], [esp+50h] and [esp+54h] at 0x00462573, 0x00462583 and 0x00462594, "
         "inside the bit-1 block's second arm",
         frame_4c=lambda v: [0, 0, 0])
    # The angle column has its own inputs: work words 8, 9, 19 and 20 move
    # `expected_angle` and nothing else. Only the slope fixture captures ESI, so these
    # two are registered there; on the flat fixture the angle is not a compared column.
    if _angle:
        for _i in (8, 19):
            _gpw(f"work_w{_i}-drives-the-angle", "break",
                 f"work word {_i} at [ebx+{0x20 + 4 * (_i - 8) if _i < 13 else 0x1C + 4 * _i:X}h]; "
                 "the angle comes out of the three 0x004469E0 calls in the tail block",
                 work=(lambda v, _j=_i: [bump(x) if k == _j else x for k, x in enumerate(v)]))

    # Work word 0 is the query radius; the window's lowest work offset is [ebx+0Ch].
    _gpw("work_w0-unread", "inert",
         "the window's lowest work-buffer offset is [ebx+0Ch]; nothing names [ebx]",
         work=lambda v: [bump(x) if k == 0 else x for k, x in enumerate(v)])


# ---------------------------------------------------------------- SA2GAC1
def _gac(name, kind, basis, **kw):
    @control("ground-acceptance-v1", name, kind, basis)
    def _build(rows, header, sections, _kw=kw):
        sib = P._gac_sibling(rows)
        cases = [P._gac_as_gpw(r, sib) for r in rows]
        return P._gpw_plan("ground-acceptance-v1", cases, with_angle=True,
                           override=_gpw_override(**_kw))


# The columns SA2GAC1 records itself.
_gac("work_w21-drives-the-policy", "break",
     "[ebx+54h], 0x0046254A; SA2GAC1's own case words 25..57",
     work=lambda v: [bump(x) if k == 21 else x for k, x in enumerate(v)])
_gac("entity_w7-drives-the-policy", "break",
     "[eax+1Ch], 0x00462598; SA2GAC1's own case words 14..21",
     entity=lambda v: [bump(x) if k == 7 else x for k, x in enumerate(v)])
_gac("work_w8-drives-the-angle", "break",
     "work word 8 at [ebx+20h]; the angle column",
     work=lambda v: [bump(x) if k == 8 else x for k, x in enumerate(v)])
# The three groups taken from `ground-policy-word-slope-v1`. Player word 77 is not free
# -- zeroing it loses bit 0 of the policy word on the two cases whose entity word 1 is
# odd, the two that enter the bit-1 block -- and the other two are measured inert, which
# is what lets them be supplied at all.
_gac("sibling-player_w77-zeroed", "break",
     "the supplied player word 77, reached through [ebp+10h] inside the bit-1 block; "
     "zeroed, the block runs on to 0x0046265D `mov dword ptr [esp+1Ch],0` and bit 0 of "
     "the policy word is lost on the two cases whose entity word 1 is odd",
     player=lambda v: [0 if k == 3 else x for k, x in enumerate(v)])
_gac("sibling-frame_4C-triple-zeroed", "inert",
     "the supplied [esp+4Ch] triple, read at 0x00462573/0x00462583/0x00462594 inside "
     "the bit-1 block's second arm; measured inert on every case",
     frame_4c=lambda v: [0, 0, 0])
_gac("sibling-gravity-zeroed", "inert",
     "the supplied gravity globals: the window's gravity reads are at 0x00462C7D, "
     "0x00462C93 and 0x00462CAC, past its end",
     gravity=lambda v: [0, 0, 0])


# ---------------------------------------------------------------- SA2GTR (0x00462F83)
def _gtr_override(**kw):
    """Build an override for `plans._gtr_row`: name a block and a transform.

    Blocks are the ones the case carries -- `entity`, `world`, `player` (the 24 schema
    words, already mapped from tokens to this driver's own addresses), `work`, `record`
    -- plus `globals_` ([0x01A5A248], [0x01A5A23C]) and `sm`, the semantic block by name.
    """
    def over(entity, world, player, work, record, globals_, sm):
        parts = {"entity": entity, "world": world, "player": player, "work": work,
                 "record": record, "globals_": globals_, "sm": sm}
        for key, fn in kw.items():
            parts[key] = fn(parts[key] if key == "sm" else list(parts[key]))
        return (parts["entity"], parts["world"], parts["player"], parts["work"],
                parts["record"], parts["globals_"], parts["sm"])
    return over


def _at(index: int, transform=bump):
    def fn(values):
        values[index] = transform(values[index]) & 0xFFFFFFFF
        return values
    return fn


def _at_many(indices, transform=bump):
    def fn(values):
        for i in indices:
            values[i] = transform(values[i]) & 0xFFFFFFFF
        return values
    return fn


def _sm(key, value):
    def fn(sm):
        sm = dict(sm)
        sm[key] = value(sm[key]) if callable(value) else value
        return sm
    return fn


_GTR_FIXTURES = ("ground-tail-v1", "ground-tail-v2", "ground-tail-v3", "ground-tail-v4",
                 "ground-tail-final-v5", "ground-tail-slope-v1")
_GTR_WIDE = ("ground-tail-v3", "ground-tail-v4", "ground-tail-final-v5",
             "ground-tail-slope-v1")      # the four whose EntityData2 block is 10 words


def _gtr_control(fixture: str):
    def make(name, kind, basis, **kw):
        @control(fixture, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _f=fixture):
            return P._gtr_plan(_f, rows, header, sections,
                               override=_gtr_override(**_kw))
    return make


for _fx in _GTR_FIXTURES:
    _g = _gtr_control(_fx)
    # ---- break: the three positions the window computes at 0x0046337B..0x004633A6
    # work[1] = work[27] + work[24], work[2] = work[28] + work[25],
    # work[3] = work[29] + work[26], then the plane projection adds [esp+4Ch..54h].
    _g("work_w24..26-drive-work_w1..3", "break",
       "0x00463382 fadd [ebx+60h] / 0x00463395 fadd [ebx+64h] / 0x0046339E fadd [ebx+68h]",
       work=_at_many((24, 25, 26)))
    _g("work_w27..29-drive-work_w1..3", "break",
       "0x0046337B fld [ebx+6Ch] / 0x00463392 fld [ebx+70h] / 0x0046339B fld [ebx+74h]",
       work=_at_many((27, 28, 29)))
    # ---- break: the compact contact record, reached only through the work buffer's own
    # half-word index. This is the control that proves the record array is laid out where
    # the window reads it and that its plane point reaches the compared positions.
    _g("record_w8..10-drive-the-plane-projection", "break",
       "0x00463385 movzx eax,word ptr [ebx+2Ah] / imul eax,eax,58h / add eax,1DDB660h, "
       "then 0x004633CF/0x004633D9/0x004633E3 fld [eax+20h/24h/28h] into 0x00780310",
       record=_at_many((8, 9, 10)))
    # ---- inert: the plane normal 0x00780310 projects along is *not* a live-in. Work
    # words 7, 8 and 9 are overwritten with (0, 1, 0) before any read of them, and the
    # values 0x004633B0/0x004633BB/0x004633C5 then load are what 0x00464680 left there.
    # This control was written as a `break` on the strength of those three loads and
    # failed on all six fixtures, which is how the three stores were found.
    _g("work_w7..9-overwritten-before-read", "inert",
       "0x004631C2 fst [ebx+24h] / 0x004631C5 fstp [eax] (eax = [esp+44h] = work+0x1C) / "
       "0x004631C9 fstp [ebx+20h] write work words 9, 7 and 8 before 0x004633B0, "
       "0x004633BB and 0x004633C5 read them",
       work=_at_many((7, 8, 9)))
    # ---- break: the floor normal the window copies out of work+0x48 into the player
    _g("work_w18..20-drive-player_w31..33", "break",
       "0x004632BA mov ecx,[esp+10h] (work+0x48) / 0x004632C7 mov edx,[ecx] / 0x004632CC "
       "mov [eax+7Ch],edx / 0x004632CF mov edx,[ecx+4] / 0x004632D2 mov [eax+80h],edx / "
       "0x004632D8 mov ecx,[ecx+8] / 0x004632DF mov [eax+84h],ecx",
       work=_at_many((18, 19, 20)))
    # ---- break: player word 77 scales the projection offset stored at [esp+50h]
    _g("player_w77-drives-work_w2", "break",
       "0x00463216 fld [eax+134h] / 0x00463220 fmul qword ptr [904E98h] / 0x00463234 "
       "fstp [esp+50h], added to the corrected Y at 0x00463414",
       player=lambda v: v[:20] + [bump(v[20])] + v[21:])

    # ---- break: the support pointer itself. [esp+74h] is the candidate triple's word 1
    # (0x00462F58/0x00462F5E/0x00462F77) and 0x004637F2 `mov [eax+0B0h],ecx` stores it
    # into player word 44. Pointing it at the other object makes the token-1 column come
    # back as the other address, which is what proves the column is the pointer the guest
    # stored rather than a constant this plan wrote.
    _g("support-pointer-drives-player_w44", "break",
       "0x004637EA mov ecx,[esp+74h] / 0x004637F2 mov [eax+0B0h],ecx",
       sm=_sm("support", P.A_GTR_OTHER))
    # ---- break: the accepted-tail flag chooses the exit
    _g("tail-flag-chooses-the-exit", "break",
       "0x00463698 cmp dword ptr [esp+0B4h],1 / 0x004636A0 jz 004637E7h -- with the flag "
       "clear the routine leaves by 0x004637A7 or 0x004637E6 and never stores the support",
       sm=_sm("tail_flag", 0))
    # ---- break: the supplied guest global that gates the travel-vector clear
    _g("global-0x01A5A248-set", "break",
       "0x00463359 cmp dword ptr [1A5A248h],0 / 0x0046336E fst [ebx+74h] / 0x00463371 "
       "fst [ebx+70h] / 0x00463374 fstp [ebx+6Ch]; supplied as 0, which is what "
       "0x00461F32 mov [1A5A248h],eax leaves at the routine's own entry",
       globals_=lambda v: [1, v[1]])

    # ---- probe: poison every compared column the window is claimed to write, and report
    # which ones came back still poisoned. None may.
    _g("written-columns-poisoned", "probe",
       "work words 1..6 (0x00463390 fstp [esi], 0x00463398 fstp [ebx+8h], 0x004633A6 "
       "fstp [ebx+0Ch], 0x0046336A fst [eax], 0x00463367 fst [ebx+14h], 0x00463360 fst "
       "[ebx+18h]) and player words 31..33 (0x004632CC/0x004632D2/0x004632DF); each is "
       "written before any read of it on this path",
       work=_at_many((1, 2, 3, 4, 5, 6), lambda _w: P.POISON),
       player=lambda v: [P.POISON if i in (7, 8, 9) else x for i, x in enumerate(v)])

# ---- inert: token 2 names "not the support and not null", nothing more. Moving the
# address the driver gives that token must not move any compared column.
#
# Registered on the four fixtures whose recorded player words actually carry token 2.
# It used to be registered on all six, and on ground-tail-final-v5 and -slope-v1 the
# substitution found nothing to replace, so the encoded plan was byte-identical to the
# baseline's and the control held without driving anything different (G2b finding 3).
for _fx in ("ground-tail-v1", "ground-tail-v2", "ground-tail-v3", "ground-tail-v4"):
    _gtr_control(_fx)(
        "token-2-address-is-a-name", "inert",
        "player words 40 and 44 reach the window only as stores (0x0046343C, 0x0046345D, "
        "0x004637F2); the token-2 address is never dereferenced",
        player=lambda v: [P.A_GTR_OTHER + 0x40 if x == P.A_GTR_OTHER else x for x in v])


# The entity position at [esp+30h] and the second supplied global are a pair of
# path-dependent live-ins, and the corpus splits on exactly the same branch. On every row
# of five of the six fixtures the window reaches 0x004635E4, which *overwrites* the
# entity position from [esp+38h..40h] -- so the recorded position is not a live-in there
# and [0x01A5A23C] never reaches a compared column. On the four `ground-tail-final-v5`
# rows whose candidate bound is 3 the window does not reach it, and both become
# load-bearing. Each is asserted both ways rather than left to one reading.
for _fx in ("ground-tail-v1", "ground-tail-v2", "ground-tail-v3", "ground-tail-v4",
            "ground-tail-slope-v1"):
    _g = _gtr_control(_fx)
    _g("entity_w5..7-overwritten-at-0x004635E4", "inert",
       "0x004635E4 mov eax,[esp+30h] / 0x004635F0 mov [eax],ecx / 0x004635F6 "
       "mov [eax+4],edx / 0x004635F9 mov [eax+8],ecx writes the entity position from "
       "[esp+38h..40h] on every row of this fixture, before 0x004637FC reads it",
       entity=_at_many((5, 6, 7)))
    _g("global-0x01A5A23C-unread-here", "inert",
       "0x004634AB mov ecx,[1A5A23Ch] gates 0x004634EC test ecx,ecx / jnz 004635CFh and "
       "0x004635DC test ecx,ecx / jz 004634F6h; on every row of this fixture the window "
       "reaches 0x004635E4 through 0x004635DA jz, so the value does not choose a path",
       globals_=lambda v: [v[0], 1])

_g = _gtr_control("ground-tail-final-v5")
_g("entity_w5..7-drive-the-exit-position", "break",
   "on the four bound-3 rows the window leaves by 0x004637E7 without reaching "
   "0x004635E4, so the exit position is 0x004637FC fld [eax] / 0x004637FF fadd [edx] / "
   "0x00463802 fstp [eax] over the live-in",
   entity=_at_many((5, 6, 7)))
_g("global-0x01A5A23C-set", "break",
   "0x004634AB mov ecx,[1A5A23Ch]; on the four bound-3 rows a nonzero value takes "
   "0x004634EC jnz 004635CFh and then 0x004635DC jz, reaching the position store at "
   "0x004635E4 the recorded rows do not. Supplied as 0, the image-initial value, the "
   "only store in the image being 0x0045A730 mov dword ptr [1A5A23Ch],1",
   globals_=lambda v: [v[0], 1])

for _fx in ("ground-tail-v3", "ground-tail-v4", "ground-tail-slope-v1"):
    _g = _gtr_control(_fx)
    # EntityData2 word 6 is the contact angle the window copies out of work word 13 --
    # on the rows where the orientation block at 0x004631CF does not overwrite it first.
    _g("work_w13-drives-world_w6", "break",
       "0x004632B7 mov edx,[ebx+34h] / 0x004632BE mov [esi+18h],edx",
       work=_at(13))

# Entity word 2, the rotation X, is the orientation block's own live-in at 0x004631CC
# and the source of work word 13 and EntityData2 word 6 -- except on the rows that reach
# 0x004631A5 `call 00464960h`, which writes entity+8 from the contact angles first. The
# two legacy ticks 401 and 402 are exactly those rows, which is why the three fixtures
# that hold only them assert it the other way.
for _fx in ("ground-tail-v4", "ground-tail-final-v5", "ground-tail-slope-v1"):
    _g = _gtr_control(_fx)
    _g("entity_w2-drives-work_w13", "break",
       "0x004631CC mov ecx,[edi+8h] / 0x004631CF mov [ebx+34h],ecx, then 0x004632B7 "
       "mov edx,[ebx+34h] / 0x004632BE mov [esi+18h],edx",
       entity=_at(2))

for _fx in ("ground-tail-v1", "ground-tail-v2", "ground-tail-v3"):
    _g = _gtr_control(_fx)
    _g("entity_w2-overwritten-by-0x00464960", "inert",
       "on ticks 401 and 402 the window reaches 0x004631A5 call 00464960h, the contact "
       "orientation helper, which writes entity+8 before 0x004631CC mov ecx,[edi+8h] "
       "reads it; the same perturbation is a `break` on ground-tail-v4, "
       "ground-tail-final-v5 and ground-tail-slope-v1",
       entity=_at(2))

for _fx in ("ground-tail-v1", "ground-tail-v2"):
    _g = _gtr_control(_fx)
    # SA2GTR1 and SA2GTR2 record three EntityData2 words; the driver zeroes the other
    # seven. The only loads through [ebp+0Ch] in the window are [esi] and [esi+8].
    _g("world_w3..9-unread", "inert",
       "the window's only EntityData2 reads are 0x00462FC6 fcom [esi] and 0x00462FCF "
       "fcomp [esi+8h] (and 0x00463081/0x0046308A on the mirror arm), words 0 and 2",
       world=lambda v: v[:3] + [bump(x) for x in v[3:]])

# SA2GTR1 does not record the helper result; it is supplied from `ground-tail-v2`.
_g = _gtr_control("ground-tail-v1")
_g("sibling-result-nonzero", "break",
   "the supplied helper result, tested at 0x00462F86 test eax,eax / jz 00462FA0h and "
   "stored into the accumulator at 0x00462F91, which 0x004631AD cmp [esp+28h],0 then "
   "uses to skip the whole orientation section",
   sm=_sm("result", 2))
_g("sibling-selector-as-0x5A", "inert",
   "the supplied stage selector at 0x01934B70; its only read on this path is "
   "0x004691DD cmp word ptr [1934B70h],5Ah inside PResetPosition, and neither the "
   "recorded 0xA nor 0x5A moves a compared column on these two rows",
   sm=_sm("selector", 0x5A))


# ---------------------------------------------------------------- SA2GEX1 (0x00462F83)
# The two ordered-executor fixtures drive at the same window with the same override
# hook, so they carry the same shape of control. The one that matters most here is
# `support-pointer-drives-player_w44`: `candidate_w1` is the word the SA2GEX blocker
# called a dropped field, and this is it being written into a compared column.
def _gex_control(fixture: str):
    def make(name, kind, basis, **kw):
        @control(fixture, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _f=fixture):
            return P._gex_plan(_f, header, sections, override=_gtr_override(**_kw))
    return make


for _fx in ("ground-candidate-executor-v1", "ground-executor-slope-v1"):
    _g = _gex_control(_fx)
    _g("work_w24..26-drive-work_w1..3", "break",
       "0x00463382 fadd [ebx+60h] / 0x00463395 fadd [ebx+64h] / 0x0046339E fadd [ebx+68h]",
       work=_at_many((24, 25, 26)))
    _g("work_w27..29-drive-work_w1..3", "break",
       "0x0046337B fld [ebx+6Ch] / 0x00463392 fld [ebx+70h] / 0x0046339B fld [ebx+74h]",
       work=_at_many((27, 28, 29)))
    _g("record_w8..10-drive-the-plane-projection", "break",
       "0x00463385 movzx eax,word ptr [ebx+2Ah] / imul eax,eax,58h / add eax,1DDB660h, "
       "then 0x004633CF/0x004633D9/0x004633E3 fld [eax+20h/24h/28h] into 0x00780310",
       record=_at_many((8, 9, 10)))
    _g("player_w77-drives-work_w2", "break",
       "0x00463216 fld [eax+134h] / 0x00463220 fmul qword ptr [904E98h] / 0x00463234 "
       "fstp [esp+50h], added to the corrected Y at 0x00463414",
       player=lambda v: v[:20] + [bump(v[20])] + v[21:])
    _g("support-pointer-drives-player_w44", "break",
       "the candidate triple's word 1, which 0x00462F58/0x00462F5E/0x00462F77 put at "
       "[esp+74h] and 0x004637EA mov ecx,[esp+74h] / 0x004637F2 mov [eax+0B0h],ecx "
       "stores into player word 44 -- the `candidate_w1` the old blocker called dropped",
       sm=_sm("support", P.A_GTR_OTHER))
    _g("tail-flag-chooses-the-exit", "break",
       "0x00463698 cmp dword ptr [esp+0B4h],1 / 0x004636A0 jz 004637E7h",
       sm=_sm("tail_flag", 0))
    _g("work_w18..20-drive-player_w31..33", "break",
       "0x004632BA mov ecx,[esp+10h] (work+0x48) / 0x004632C7 mov edx,[ecx] / 0x004632CC "
       "mov [eax+7Ch],edx / 0x004632D2 mov [eax+80h],edx / 0x004632DF mov [eax+84h],ecx",
       work=_at_many((18, 19, 20)))
    _g("global-0x01A5A248-set", "break",
       "0x00463359 cmp dword ptr [1A5A248h],0 / 0x0046336E fst [ebx+74h] / 0x00463371 "
       "fst [ebx+70h] / 0x00463374 fstp [ebx+6Ch]; supplied as 0",
       globals_=lambda v: [1, v[1]])
    # `token-2-address-is-a-name` used to be registered here too. On these two fixtures
    # and on ground-tail-final-v5 / -slope-v1 it was a no-op: the substitution looks for a
    # player word holding A_GTR_OTHER and none of their recorded words carries token 2, so
    # the encoded plan was byte-identical to the baseline's (G2b finding 3). The claim has
    # nothing to act on here and is not re-stated; it is still asserted on the four
    # ground-tail fixtures whose records do carry the token.
    _g("work_w7..9-overwritten-before-read", "inert",
       "0x004631C2 fst [ebx+24h] / 0x004631C5 fstp [eax] / 0x004631C9 fstp [ebx+20h] "
       "write work words 9, 7 and 8 before 0x004633B0/0x004633BB/0x004633C5 read them",
       work=_at_many((7, 8, 9)))
    _g("written-columns-poisoned", "probe",
       "work words 1..6 and player words 31..33, each written before any read of it",
       work=_at_many((1, 2, 3, 4, 5, 6), lambda _w: P.POISON),
       player=lambda v: [P.POISON if i in (7, 8, 9) else x for i, x in enumerate(v)])


# ---------------------------------------------------------------- SA2GPC1 / SA2GPE2 / SA2GPE3
# The selector-to-precise pair (0x0048BAF0 then 0x0048CE40) over three rebuilt object
# caches. Every basis below is an instruction in one of the two callees. Three claims of
# mine were false here and these controls are what corrected them; each correction is
# written into the control it replaced.
def _gpc_override(**kw):
    """Build an override for `plans._gpc_row`: name a part and a transform."""
    def over(parts):
        out = dict(parts)
        for key, fn in kw.items():
            out[key] = fn(list(out[key]))
        return out
    return over


def widen(_word: int) -> int:
    """400.0f. `bump` replaces a word with 3.14159f, which is *smaller* than this
    family's recorded query radius of 5.0 -- and shrinking the selector's sphere removes
    no node from a selection that is already strictly inside it, so the first version of
    `selector-radius-drives-pni_count` held on 30 of 30 rows and the control failed. The
    same defect class as the `flip`-versus-`bump` one the gates slice found."""
    return 0x43C80000


def zero(_word: int) -> int:
    return 0


def _poly(indices, transform=bump):
    """Perturb the named words of every polygon record of every object."""
    def fn(objects):
        out = []
        for ob in objects:
            polys = []
            for p in ob["polygons"]:
                q = list(p)
                for i in indices:
                    q[i] = transform(q[i]) & 0xFFFFFFFF
                polys.append(tuple(q))
            out.append(dict(ob, polygons=polys))
        return out
    return fn


def _z_extent(transform=bump):
    def fn(objects):
        return [dict(ob, z_extent=transform(ob["z_extent"]) & 0xFFFFFFFF) for ob in objects]
    return fn


def _other_group(parts):
    """Name the *other* object at 0x0048CE86: the token is an address the driver owns,
    so pointing it at a different one must move the contact columns. All three groups are
    in the list on every row -- the near-set table only decides which the *selector*
    walks -- so the next token round is always a real group, which a first version that
    picked from the admitted set only was not."""
    token = (parts["gid"][0] - P.GPC_ID + 1) % 3
    return dict(parts, gid=[P.GPC_ID + token])


def _swap_links(objects):
    """Exchange every node's forward and backward link without moving the group's two
    chain ends, so the walk from [group+10h] runs into the end it came from."""
    return [dict(ob, polygons=[(p[1], p[0]) + tuple(p[2:]) for p in ob["polygons"]])
            for ob in objects]


def _gpc_control(fixture: str):
    def make(name, kind, basis, obj=None, over=None, plan_kw=None, **kw):
        @control(fixture, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _f=fixture, _o=obj, _ov=over,
                   _p=plan_kw or {}):
            return P._gpc_plan(_f, rows, sections,
                               override=_ov or (_gpc_override(**_kw) if _kw else None),
                               obj_override=_o, **_p)
    return make


_GPC_FIXTURES = ("ground-contact-composition-v1",
                 "ground-source-cache-contact-expectations-v2",
                 "ground-all-source-contact-expectations-v3",
                 "ground-contact-expectations-slope-v1")

for _fx in _GPC_FIXTURES:
    _g = _gpc_control(_fx)
    # --- the selector's own query, which decides pni_count and selector_result
    _g("selector-point-drives-pni_count", "break",
       "0x0048BB17 lea eax,[esi+4] / 0x0048BC3D fld [esi-4] / fsub [edi-4] -- the query "
       "point the chain's sphere test subtracts from each node centre",
       selector=lambda v: v[:1] + [bump(x) for x in v[1:4]] + v[4:])
    _g("selector-radius-widened", "break",
       "0x0048BB12 fadd dword [esi] adds the query radius to njScalor(motion); the sum "
       "is squared at 0x0048BB22 and is the bound the walk compares at 0x0048BBC1",
       selector=_set(0, widen))
    _g("selector-motion-widened", "break",
       "0x0048BAFD lea eax,[esi+10h] / 0x0048BB0D call 0077FC30h (njScalor) feeds the "
       "same expanded radius",
       selector=lambda v: v[:4] + [widen(x) for x in v[4:7]])
    # --- the precise call's own query, which decides result, record_count and w13..32
    _g("precise-point-drives-the-contact", "break",
       "0x0048CF9B fld [ebx+4] / 0x0048CFB8 fsubr [ebx+8] / 0x0048CFBF fsubr [ebx+0Ch]",
       precise=lambda v: v[:1] + [bump(x) for x in v[1:4]] + v[4:])
    _g("precise-radius-drives-the-contact", "break",
       "0x0048CE71 fld dword [ebx] / 0x0048CE78 fmul qword [904E98h] / 0x0048CE7E "
       "fstp [esp+78h], the effective radius the face test uses",
       precise=_set(0, bump))
    _g("precise-motion-drives-the-contact", "break",
       "0x0048CE53 lea eax,[ebx+10h] / 0x0048CE65 call 0077FC30h, the sweep length "
       "stored at [esp+80h]",
       precise=lambda v: v[:4] + [bump(x) for x in v[4:7]])
    # --- the object token, which is a group id this driver chooses
    _g("object-token-names-the-group", "break",
       "0x0048CE86 cmp [eax+8],esi walks [0x01A5A400] for the group whose id is the eax "
       "argument -- the 'object token 0/1/2' the old blocker called an obstacle is that "
       "id, and pointing it at another object this plan laid down moves every contact "
       "column",
       over=_other_group)
    # --- the caches themselves
    _g("node-centres-drive-the-selection", "break",
       "each node's centre at [edx+8]/[edx+0Ch]/[edx+10h]: 0x0048BBBA fld [edx+10h] is "
       "the Z key the chain is walked on and 0x0048BC3D/0x0048BC62/0x0048BC85 are the "
       "sphere test -- polygon words 2, 3 and 4",
       obj=_poly((2, 3, 4)))
    _g("node-radius-drives-the-selection", "break",
       "0x0048BD03 fld [edi+14h] / fadd st3, the node's own squared radius -- polygon "
       "word 5",
       obj=_poly((5,)))
    _g("node-vertices-drive-the-contact-record", "break",
       "0x0048CEF0 fld [eax+18h] through 0x0048CF31 fld [eax+2Ch], the triangle the "
       "precise call subtracts the query point from -- polygon words 6..11",
       obj=_poly((6, 7, 8, 9, 10, 11)))
    _g("chain-links-drive-the-walk", "break",
       "polygon words 0 and 1 are the forward and backward links the stager normalised "
       "to ordinals and this plan re-pointers; 0x0048BBEA mov edx,[edx] and 0x0048BDB5 "
       "mov edi,[edi+4] are the two walks, from [group+10h] and [group+14h]",
       obj=_swap_links)
    _g("group-tolerance-drives-the-selection", "break",
       "0x0048BBA8 fsub dword [ecx+28h] / 0x0048BBB1 fadd dword [ecx+28h], the group "
       "tolerance this plan takes from the object's own z_extent word",
       obj=_z_extent())
    # --- what the pair does not read
    _g("work_w7..9,11,12-unread", "inert",
       "the only reads through ebx in 0x0048CE40 are [ebx] (0x0048CE71), [ebx+4] "
       "(0x0048CF9B), [ebx+8] (0x0048CFB8), [ebx+0Ch] (0x0048CFBF) and lea [ebx+10h] "
       "(0x0048CE53); words 7..9, 11 and 12 are never named",
       work=lambda v: [0 if i in (7, 8, 9, 11, 12) else x for i, x in enumerate(v)])
    _g("near-set-order-inert", "inert",
       "0x0048BB80 cmp [edx],esi scans the whole near-set table for each group, so the "
       "order the gather wrote it in cannot decide anything; the group list order at "
       "0x0048BDCA mov ecx,[ecx] is what orders the selected list",
       cols=lambda v: v[::-1])
    _g("selected-list-poisoned", "probe",
       "the selected list at 0x0194F030 and work words 7..32, seeded with 0xDEADBEEF "
       "before the pair runs: what comes back poisoned is what the pair did not write",
       seed=lambda v: [P.POISON])

# Two claims asserted both ways. Shrinking the selector's sphere -- zeroing the query
# radius, or zeroing the motion njScalor expands it by -- removes nothing from a
# selection that is already strictly inside it, which is why the first, `bump`-based
# version of the radius control held on all 30 flat rows and failed as a `break`. On the
# slope fixture's wider sweeps it does remove nodes. Measured, not assumed, on both.
for _fx, _kind in (("ground-contact-composition-v1", "inert"),
                   ("ground-source-cache-contact-expectations-v2", "inert"),
                   ("ground-all-source-contact-expectations-v3", "inert"),
                   ("ground-contact-expectations-slope-v1", "break")):
    _g = _gpc_control(_fx)
    _g("selector-radius-zeroed", _kind,
       "the same 0x0048BB12 fadd dword [esi]: on the 118..130 ticks every selected node "
       "is inside the unexpanded sphere, so removing the expansion removes nothing; on "
       "the 160..200 ticks it does",
       selector=_set(0, zero))
    _g("selector-motion-zeroed", _kind,
       "the same 0x0048BB0D call 0077FC30h over [esi+10h]",
       selector=lambda v: v[:4] + [0, 0, 0])
    # The near-set gate is an input -- 0x0048BB80 skips a group the table does not name --
    # and the fixtures record which objects the caller had that tick (`object_token` per
    # case, `caller_bound` of them). Admitting all three on every tick changes nothing on
    # the 30-case fixtures, because the object the caller had dropped selects nothing
    # there anyway; on the slope it over-counts `selector_result` on nine rows.
    _g("admit-every-object", _kind,
       "0x0048BB74 mov edx,[1A5A2DCh] / 0x0048BB80 cmp [edx],esi / 0x0048BB8C jmp "
       "0048BDCAh: a group the near-set table does not name contributes nothing, so "
       "which objects are admitted is an input, and the caller's own candidate list is "
       "what the fixture records it as",
       plan_kw={"admit_by_tick": False})

# ---------------------------------------------------------------- SA2GFL1 (0x004620CA)
# The whole chain: 0x0047CD60 over the shipped COL table, then the window that turns the
# near set into candidate triples and orders them. Three claims the old blocker made
# about the fill loop's live-ins are tested here rather than argued: the work buffer
# (`contact-gate-does-not-order`), the player status byte (`player-status-set`) and the
# refresh step the plan does not call (`reach-prefilter-inert`).
def _gfl_override(**kw):
    def over(parts):
        out = dict(parts)
        for key, fn in kw.items():
            out[key] = fn(list(out[key]))
        return out
    return over


def _col_words(indices, transform=bump):
    def fn(cols, _header, _rows):
        out = []
        for c in cols:
            q = list(c)
            for i in indices:
                q[i] = transform(q[i]) & 0xFFFFFFFF
            out.append(tuple(q))
        return out
    return fn


def _col_attribute_bit(bit: int):
    """Set one bit of every COL record's attribute word."""
    def fn(cols, _header, _rows):
        return [(c[0] | bit,) + tuple(c[1:]) for c in cols]
    return fn


def _reach_prefilter(cols, header, rows):
    """Drop every COL record farther than `reach` from any recorded reference point --
    what 0x0047D160 would have handed the gather. The plan skips that step and feeds the
    gather the whole 1331-record table; this measures whether the step could have
    mattered. It cannot: a record the gather admits is within the query radius (5.0) of
    a query point that is itself within a few units of the reference, and `reach` is
    400.0, so the pre-filter can only remove records the gather was going to reject."""
    def f32(w):
        return struct.unpack("<f", struct.pack("<I", w))[0]
    reach = f32(header["reach_bits"])
    refs = [(f32(r[1]), f32(r[2]), f32(r[3])) for r in rows]
    out = []
    for c in cols:
        cx, cy, cz, rad = f32(c[1]), f32(c[2]), f32(c[3]), f32(c[4])
        near = any((cx - rx) ** 2 + (cy - ry) ** 2 + (cz - rz) ** 2
                   <= (reach + rad) ** 2 for rx, ry, rz in refs)
        out.append(c if near else (c[0], c[1], c[2], c[3], c[4], c[5]))
        if not near:
            out[-1] = (0x80000000, c[1], c[2], c[3], c[4], c[5])
    return out


def _gfl_control(fixture: str):
    def make(name, kind, basis, col=None, plan_kw=None, **kw):
        @control(fixture, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _f=fixture, _c=col, _p=plan_kw or {}):
            return P._gfl_plan(_f, rows, header, sections,
                               override=(_gfl_override(**_kw) if _kw else None),
                               col_override=_c, **_p)
    return make


for _fx in ("ground-full-chain-v1", "ground-full-chain-slope-v1"):
    _g = _gfl_control(_fx)
    _g("query-point-drives-the-list", "break",
       "0x0047D031 fld [esi] / 0x0047D037 fld [esi+4] / 0x0047D04A fld [esi+8] subtract "
       "the COL centre from the query point the plan pushes at [esp+4..0Ch]",
       query=lambda v: [bump(x) for x in v[:3]] + v[3:])
    _g("query-radius-drives-the-list", "break",
       "0x0047D0E5 fld dword [esi+0Ch] / fcompp / 0x0047D0EC test ah,41h compares the "
       "COL radius against distance - query_radius (0x0047D0DD fstp [esp+18h])",
       query=lambda v: v[:3] + [widen(v[3])])
    _g("col-centres-drive-the-list", "break",
       "the same three subtractions, from the other side: each record's centre at "
       "[esi]/[esi+4]/[esi+8]",
       col=_col_words((1, 2, 3)))
    _g("col-radii-drive-the-list", "break",
       "0x0047D0E5 fld dword [esi+0Ch], the record's own radius",
       col=_col_words((4,)))
    _g("col-payload-drives-the-triple", "break",
       "0x0047D0FD mov edx,[esi+10h] / 0x0047D102 mov [ebp+4],edx puts the payload in "
       "the near entry, and 0x00462238 mov ecx,[edi+4] / 0x00462240 mov [eax+4],ecx "
       "copies it into the candidate triple -- the ord*_w1 column",
       col=_col_words((5,)))
    _g("col-attribute-filters-the-list", "break",
       "0x0046216E test eax,2002h / jne 00462274h: setting bit 1 of every attribute word "
       "makes the fill loop skip every near entry, so no triple is appended at all",
       col=_col_attribute_bit(0x2))
    _g("prior_object-drives-the-order", "break",
       "0x00462377 cmp edi,[esi+0B0h] and 0x004623FE, the demotion test SA2GOO2 records "
       "as `prior_object` -- byte 0xB0 is player word 44",
       prior=lambda v: [v[0], (v[1] ^ 0x1000) & 0xFFFFFFFF])
    _g("contact-gate-does-not-order", "inert",
       "0x004621BE fcomp dword [ebx] / 0x004621C5 je 00462232h gates the precise call "
       "at 0x004621CC, and both sides of it reach the same append at 0x00462232; "
       "[0x008AF004] is 0.0, so a work word 0 of 0.0 takes the skip and the recorded "
       "5.0 does not -- and the ordered list is the same either way",
       plan_kw={"work0": 0})
    _g("player-status-set", "inert",
       "0x00462179 test byte [esi+1Ch],3 / jne 0046218Ah only decides whether "
       "0x0046217F test eax,1000000h runs, and no attribute in this table's admitted set "
       "(0x40000001, 0x40400001, 0x20000001) has bit 24",
       plan_kw={"status": 3})
    _g("reach-prefilter-inert", "inert",
       "0x0047D160's reach test, applied to the table before the gather sees it: this "
       "plan skips the refresh step and hands 0x0047CD60 all 1331 records, and removing "
       "the ones beyond reach 400.0 of every recorded reference point changes nothing",
       col=_reach_prefilter)
    # `ordered-slots-poisoned` used to be registered here as `query=lambda v: v`. The
    # baseline plan already poisons the ordered list and the cursor spill, so the probe's
    # encoded plan was the baseline's and it reported what the run itself reports (G2b
    # finding 3, two beyond the eight the interrogation named, both probes). The same
    # measurement is now in the run log for every fixture, as `no_call.informative_columns`.

# ---------------------------------------------------------------- SA2GCL1 / SA2GEX2
# The candidate loop, entered at the tail 0x00462F83 and carried through the back edge
# at 0x00463692 into the lifted loop head.
#
# Four of these were written as `break` on both fixtures and failed on SA2GCL1. The
# reason is a property of its single case rather than of the plan, and it is the sharpest
# thing to say about what that row tests: tick 118 is bound 2 with the hit at index 0,
# the row is entered at index 0's tail, and index 1 is a candidate that misses. A miss
# leaves 0x0048CE40 at 0x0048CE91 or 0x0048DBF5 without touching the work buffer, so on
# that one row the object caches, the selector's face lists and the object token cannot
# move a compared column -- every one of them is *measured* inert here and break on
# SA2GEX2, whose two calls have three iterations each and two hits apiece.
def _gcl_override(**kw):
    def over(parts):
        out = dict(parts)
        for key, fn in kw.items():
            out[key] = fn(out[key])
        return out
    return over


def _reverse(values):
    return list(values)[::-1]


def _retoken(values):
    """Point every candidate at the next object round: the triple's word 1 is a group id
    the driver chose, so naming another object must move every column a hit decides."""
    return [(attribute, (token + 1) % 3) for attribute, token in values]


def _gcl_control(fixture: str):
    def make(name, kind, basis, obj=None, plan_kw=None, **kw):
        @control(fixture, name, kind, basis)
        def _build(rows, header, sections, _kw=kw, _f=fixture, _o=obj, _p=plan_kw or {}):
            return P.BUILDERS[_f](rows, header, sections,
                                  override=(_gcl_override(**_kw) if _kw else None),
                                  obj_override=_o, **_p)
    return make


_GCL_MISS_ONLY = "; on SA2GCL1's single case the one iteration that follows the entered "\
    "one is a miss (tick 118, bound 2, hit at index 0), and a miss returns from "\
    "0x0048CE40 without touching the work buffer, so the same perturbation is measured "\
    "inert there"

for _fx in ("ground-candidate-loop-v1", "ground-executor-multi-v1"):
    _g = _gcl_control(_fx)
    _g("candidate-order-drives-the-exit", "break",
       "0x004624C6 lea edi,[eax+eax*2] indexes the ordered list at (caller frame)+0x100 "
       "by the loop index, and 0x004624F0 mov [esp+18h],edi is what 0x004637EA reads "
       "back into player word 44, so the order the caller left decides both which object "
       "each iteration tests and which one the exit records",
       candidates=_reverse)
    _g("record_w8..10-drive-the-plane-projection", "break",
       "0x00463385 movzx eax,word ptr [ebx+2Ah] / imul eax,eax,58h / add eax,1DDB660h, "
       "then 0x004633CF/0x004633D9/0x004633E3 fld [eax+20h/24h/28h] into 0x00780310",
       record=lambda v: [bump(x) if i in (8, 9, 10) else x for i, x in enumerate(v)])
    _g("work_w24..26-drive-work_w1..3", "break",
       "0x00463382 fadd [ebx+60h] / 0x00463395 fadd [ebx+64h] / 0x0046339E fadd [ebx+68h]",
       work=lambda v: [bump(x) if i in (24, 25, 26) else x for i, x in enumerate(v)])
    # `token-2-address-is-a-name` used to be registered here as `player=lambda v: v` --
    # the identity, so the encoded plan was the baseline's and the control asserted
    # nothing at all (G2b finding 3). Removed rather than re-worded.
    _g("written-columns-poisoned", "probe",
       "work words 1..6, each written before any read of it inside the window",
       work=lambda v: [P.POISON if 1 <= i <= 6 else x for i, x in enumerate(v)])

for _fx, _kind in (("ground-candidate-loop-v1", "inert"),
                   ("ground-executor-multi-v1", "break")):
    _g = _gcl_control(_fx)
    _g("selector-fills-the-face-lists", _kind,
       "0x0048CE9A mov edx,[eax+24h] / mov eax,[eax+1Ch] reads the group's selected face "
       "list and 0x0048DBF5 cmp ecx,1 / jl 0048CE91h returns 0 when nothing was "
       "selected; only 0x0048BAF0 fills it (0x0048BCAE, 0x0048BCB1, 0x0048BCB5)"
       + _GCL_MISS_ONLY,
       plan_kw={"run_selector": False})
    _g("object-token-names-the-group", _kind,
       "0x004624D0 mov ecx,[esp+edi*4+104h] takes the candidate triple's word 1 and "
       "0x00462508/0x0046250A hand it to 0x0048CE40 in eax, where 0x0048CE86 "
       "cmp [eax+8],esi finds the group: the token is an address this driver owns"
       + _GCL_MISS_ONLY,
       candidates=_retoken)
    _g("node-vertices-drive-the-exit", _kind,
       "0x0048CEF0 fld [eax+18h] through 0x0048CF31 fld [eax+2Ch], the triangle each "
       "selected face gives 0x0048CE40 -- polygon words 6..11 of the caches the loop "
       "walks" + _GCL_MISS_ONLY,
       obj=_poly((6, 7, 8, 9, 10, 11)))
    _g("bound-drives-the-exit", _kind,
       "0x0046368A cmp eax,[esp+34h] / 0x00463692 jl 004624C0h: the bound is what makes "
       "the loop run the iterations after the entered one at all. Cutting it to 1 still "
       "reproduces SA2GCL1's exit -- its one following iteration is a miss and changes "
       "nothing a compared column sees -- and does not reproduce either of SA2GEX2's, "
       "whose calls hit twice. The loop head still has to be lifted and entered for "
       "SA2GCL1 to return at all: without it 0x00463692 reaches "
       "`__remill_missing_block` and the row traps.",
       frame=lambda f: dict(f, bound=1))
    _g("node-centres-drive-the-exit", _kind,
       "0x0048BBBA fld [edx+10h] and 0x0048BC3D/0x0048BC62/0x0048BC85: the centres "
       "decide which faces the selector puts in each group's list" + _GCL_MISS_ONLY,
       obj=_poly((2, 3, 4)))
    # The start position at (caller frame)+0xC4 is read on one path only: 0x0046366E
    # `lea ecx,[esp+0E4h]`, eight pushes below the frame, hands it to the
    # rejected-contact response 0x00463EE0. SA2GCL1 has no rejected iteration.
    _g("start-position-drives-the-rejected-response", _kind,
       "0x0046366E lea ecx,[esp+0E4h] / 0x00463675 call 00463EE0h, whose `fld [ecx]` at "
       "0x00463F16 is the only read of those three words in the window; SA2GCL1's call "
       "has no rejected iteration, so there it is measured inert",
       frame=lambda f: dict(f, start_position=[bump(x) for x in f["start_position"]]))

# ---------------------------------------------------------------- the rest of the corpus
# Thirty-one of the 54 passing fixtures carried no control at all (G2b finding 5), and
# every fixture that declares an exclusion was among them: `compact-contact-corpus-v1`
# supplies ten of the twenty-two record words, the four composition fixtures do not
# compare `expanded`, `direction` or `visited` and supply `root_center_z` on v3 and v4,
# and the four `preset-position-*` fixtures compare four of the words the routine writes.
# An exclusion nobody tests is a claim, and the corpus had five of them.
#
# These controls need no builder to grow an `override` hook: they perturb the encoded op
# stream itself, one OP_POKE block at a time, at a guest address the plan's own ABI string
# names. `_block` raises when no row pokes that address, which is the same guard
# `run_controls` applies to the whole plan, one level down -- the defect that produced
# `source-fourth-word-unread` was exactly a control naming a word `_goo_plan` never wrote.
#
# Which block to name was measured, not guessed: one block at a time was replaced with
# 3.14159f across every row and the columns that moved were recorded, keeping only blocks
# whose rows all still return (status 0) and at least one of whose moved columns is
# call-dependent. The column lists in the `basis` strings are that measurement.
def _block(plan: P.Plan, address: int, transform=bump) -> P.Plan:
    """The same plan with every word of the OP_POKE at `address` transformed, on every row."""
    rows, touched = [], 0
    for row in plan.rows:
        words = list(struct.unpack(f"<{len(row) // 4}I", row))
        for op, i, _length in R.walk_ops(row):
            if op == P.OP_POKE and words[i + 1] == address:
                for k in range((words[i + 2] + 3) // 4):
                    words[i + 3 + k] = transform(words[i + 3 + k]) & 0xFFFFFFFF
                touched += 1
        rows.append(struct.pack(f"<{len(words)}I", *words))
    if not touched:
        raise SystemExit(f"{plan.fixture}: no row pokes guest 0x{address:08x}, so a control "
                         f"perturbing it asserts nothing")
    plan.rows = rows
    return plan


def _reg(plan: P.Plan, index: int, value: int) -> P.Plan:
    """The same plan with every OP_REG that sets register `index` set to `value` instead.

    Raises when no row sets it, for the same reason `_block` does: a control that names a
    register the plan never sets asserts nothing about the plan.
    """
    rows, touched = [], 0
    for row in plan.rows:
        words = list(struct.unpack(f"<{len(row) // 4}I", row))
        for op, i, _length in R.walk_ops(row):
            if op == P.OP_REG and words[i + 1] == index:
                words[i + 2] = value & 0xFFFFFFFF
                touched += 1
        rows.append(struct.pack(f"<{len(words)}I", *words))
    if not touched:
        raise SystemExit(f"{plan.fixture}: no row sets register {index}, so a control "
                         f"perturbing it asserts nothing")
    plan.rows = rows
    return plan


# 0x0048B6C3 `push ecx` makes ECX a live-in of the 0x0048B6C0 window, which the CFG
# backward-liveness census found and no plan had set. The plans now set it to 0xDEADBEEF;
# these controls set it to something else and require the verdict not to move, which is
# the measurement that says the pushed dword is the compiler's frame allocation and not a
# value the window reads back.
for _fx in ("basic-strip-decoder-v1", "candidate-cache-composition-v1",
            "candidate-cache-strip-composition-v2", "candidate-cache-point-composition-v3",
            "candidate-cache-object-matrix-composition-v4"):
    @control(_fx, "pushed-ecx-is-dead", "inert",
             "0x0048B6C3 `push ecx` / 0x0048B6C4 `fstp dword ptr [esp+38h]`: ECX is read "
             "before it is written, so it is a live-in "
             "(private/generated/s2/corpus_run/livein/window_0048b6c0.livein.json), but the "
             "dword it pushes is the block's own one-word frame allocation and nothing "
             "reads it back")
    def _ecx_dead(rows, header, sections, _f=_fx):
        return _reg(P.BUILDERS[_f](rows, header, sections), P.ECX, 0x40490FDB)


def _block_control(fixture: str, name: str, kind: str, basis: str, address: int,
                   transform=bump) -> None:
    @control(fixture, name, kind, basis)
    def _build(rows, header, sections, _f=fixture, _a=address, _t=transform):
        return _block(P.BUILDERS[_f](rows, header, sections), _a, _t)


# (fixture, control name, guest address, what the address is and what moving it moves)
_BLOCK_CONTROLS: tuple[tuple[str, str, int, str], ...] = (
    # 0x0048B610's strip decoder and 0x004974F0's cache insert, composed.
    ("basic-strip-decoder-v1", "transformed-vertices-drive-the-record", P.BSD_VERTS,
     "the strip's transformed vertices at (entry ESP)+0x60, which 0x0048B6C0 reads through "
     "ESI = &vertex[2]; record words 2..7 move on all 72 rows"),
    ("basic-strip-decoder-v1", "winding-base-drives-the-triangle-order", P.BSD_FRAME + 0x44,
     "the winding base at (entry ESP)+0x44; record words 9..14 move on 48 to 59 rows"),
    ("candidate-cache-builder-v1", "insert-argument-drives-the-buckets", P.ESP0 + 4,
     "0x004974F0's own cdecl argument at (entry ESP)+4; the 128-bucket Z flattening moves "
     "the bucket heads and tails on 66 rows and the chain head on all 72"),
    # 0x0048BAF0 -> 0x0048CE40 over a cache the composition fixtures build themselves.
    ("candidate-cache-composition-v1", "cache-group-drives-the-selection", P.A_CCB_GROUP + 0x28,
     "the group header at [group+0x28] the selector walks; the selected count, its mirror, "
     "the group counter and the selected ordinals all move"),
    ("candidate-cache-composition-v1", "csts-block-drives-the-contact-result", P.A_ECC_CSTS,
     "the csts block 0x0048CE40 writes its result through; result words 12..19 move"),
    ("candidate-cache-strip-composition-v2", "cache-group-drives-the-selection",
     P.A_CCB_GROUP + 0x28,
     "the group header at [group+0x28] the selector walks; the selected count, its mirror, "
     "the group counter and the selected ordinals all move"),
    ("candidate-cache-strip-composition-v2", "csts-block-drives-the-contact-result",
     P.A_ECC_CSTS, "the csts block 0x0048CE40 writes its result through; result words "
     "12..19 move"),
    ("candidate-cache-point-composition-v3", "point-transform-matrix-drives-max_z_span",
     P.A_ECC_MATRIX,
     "0x0048B510's transform matrix, read through its control block at (window entry "
     "ESP)+0x50; `max_z_span` moves on all five rows -- this is the supplied "
     "`root_center_z` path the note names, tested rather than asserted"),
    ("candidate-cache-point-composition-v3", "strip-block-drives-the-winding-flags",
     P.A_ECC_STRIP,
     "the signed strip block 0x0048B6C0 decodes; all three `strip*_flip` columns move"),
    ("candidate-cache-object-matrix-composition-v4", "parent-matrix-drives-the-object-matrix",
     P.A_MSTACK,
     "the ninja matrix stack frame 0x0048B9F0 composes the object-local matrix against; "
     "all twelve `matrix_m*` columns move on all five rows"),
    ("candidate-cache-object-matrix-composition-v4", "object-block-drives-the-object-matrix",
     P.A_OBJECT + 8,
     "the object's own position/rotation/scale at [object+8], 0x0048B9F0's other input; "
     "all twelve `matrix_m*` columns move"),
    # 0x0048DA51: the per-face contact body, where ten of the twenty-two record words are
    # supplied rather than compared. These are the controls that exclusion needed.
    ("compact-contact-corpus-v1", "face-vertices-drive-the-record", P.A_CRI_FACE,
     "the face triangle the window projects onto; record words 8..19 move on all 2990 rows"),
    ("compact-contact-corpus-v1", "per-accept-matrix-drives-the-record", P.A_CRI_MATRIX,
     "the per-accept matrix on the ninja matrix stack top at [0x01A557FC]; record words "
     "8..19 move on all 2990 rows"),
    ("compact-contact-corpus-v1", "effective-radius-drives-the-record", P.CRI_FRAME + 0x18,
     "the effective radius at (frame)+0x18, one of the ten supplied words; record word 12 "
     "moves on 2912 rows and word 13 on 388"),
    ("compact-contact-corpus-v1", "sweep-drives-the-record", P.CRI_FRAME + 0x40,
     "the sweep at (frame)+0x40, supplied; record word 14 moves on all 2990 rows"),
    ("compact-contact-corpus-v1", "plane-local-origin-drives-the-record", P.CRI_FRAME + 0x60,
     "the plane-local origin at (frame)+0x60, the supplied word the note says SA2CRI1 "
     "records but the earlier boundary cannot produce; record words 8..13 move"),
    # 0x0047CD60 and 0x0047D160 and the two chained.
    ("candidate-enumeration-v1", "node-bodies-drive-the-selected-ordinals", P.A_CSE_COL + 0x10,
     "the 16-word polygon records the group's Z chains link; the selected count, its "
     "mirror, the group counter and every selected ordinal move on all five rows"),
    ("ground-admission-source-v1", "col-attribute-gates-admission", P.A_COL + 0x1C,
     "the COL record's attribute at [+1Ch], which 0x0047D0xx tests before admitting; "
     "`accepted` moves on 103 of the 782 rows"),
    ("ground-admission-source-v2", "col-centre-drives-the-distance", P.A_COL,
     "the COL record's centre at [+0..8]; `distance_bits`, the callee's own [esp+18h] at "
     "0x0047D0DD, moves on all 782 rows"),
    ("ground-admission-source-v3", "col-centre-drives-the-distance", P.A_COL,
     "the COL record's centre at [+0..8]; `distance_bits` moves on every row"),
    ("ground-admission-source-v4", "col-centre-drives-the-distance", P.A_COL,
     "the COL record's centre at [+0..8]; `distance_bits` moves on every row"),
    ("ground-near-set-v2", "reference-point-drives-the-distance", P.A_REF,
     "the reference vector at [0x01DD92B0], which 0x0047D160 measures the COL record "
     "against; `distance_bits` moves on all 26726 rows"),
    ("ground-near-set-v3", "reference-point-drives-the-distance", P.A_REF,
     "the reference vector at [0x01DD92B0]; `distance_bits` moves on every row"),
    ("ground-near-set-v4", "reference-point-drives-the-distance", P.A_REF,
     "the reference vector at [0x01DD92B0]; `distance_bits` moves on every row"),
    ("ground-funnel-v1", "reference-point-drives-the-selected-set", P.A_REF,
     "the reference vector the whole 1331-record table is filtered against; `selected` and "
     "every `index*` column move on all 83 rows"),
    ("ground-funnel-chain-v1", "table-count-drives-the-walk", P.G_COL_COUNT,
     "[0x01A5A2CC], the count 0x0047CD60 walks the near set for; `accepted_so_far` moves on "
     "4183 rows and `distance_bits` on 5166"),
    ("ground-gather-output-v1", "output-cursor-count-drives-the-list", P.G_NEAR_COUNT,
     "the u16 gather count at 0x01DE9484 the routine appends from; `observed` moves on all "
     "152 rows, so the count the window reports is the one it maintained rather than the "
     "one the plan wrote"),
    # 0x0048DBD7: the selection tail.
    ("selection-corpus-v1", "record-array-drives-the-selection", P.G_RECORDS,
     "the 22-word record array at 0x0194A274 the tail walks; the selected count moves on "
     "60 of the 1459 rows"),
    ("selection-output-v1", "record-array-drives-the-csts-output", P.G_RECORDS,
     "the 22-word record array at 0x0194A274; csts words 13..19 move on about 1390 rows each"),
    # The three the S1 spike already drove, through runtime v0.
    ("ninja-object-matrix-v1", "parent-matrix-drives-the-object-matrix", P.A_MSTACK,
     "the matrix-stack frame 0x0048B9F0 composes against; all twelve columns move"),
    ("ninja-object-matrix-v1", "object-block-drives-the-object-matrix", P.A_OBJECT + 8,
     "the object's own position/rotation/scale at [object+8]; all twelve columns move"),
    ("stairs-transform-v1", "direction-drives-the-transform", P.A_VECTOR,
     "the direction vector; all three output words move on 4698 to 5403 of the 5472 rows"),
    ("stairs-transform-v1", "point-drives-the-transform", P.A_POINT,
     "the point being transformed; all three output words move"),
    ("vector-to-angles-v1", "direction-drives-both-angles", P.ESP0 + 4,
     "0x00459260's cdecl(x, y, z); both BAM angles move on 4853 and 5136 of the 5305 rows"),
    ("rejected-contact-v1", "offset-drives-the-exit-position", P.A_OFFSET,
     "the rejection offset 0x00463EE0 applies; entity words 5..7 move"),
    # 0x00469050 PResetPosition: the four fixtures that compare four of its words.
    ("preset-position-v1", "entity-block-drives-the-reset", P.A_ENTITY + 4,
     "the entity block at [eax+4]; w25, w27 and w13 move"),
    ("preset-position-v1", "world-velocity-drives-the-reset", P.A_WORLD,
     "the world velocity block in EBX; w25, w26 and w27 move on every row"),
    ("preset-position-v1", "gravity-global-drives-w13", P.G_GRAVITY,
     "the gravity global at 0x01DE94A0; w13 moves"),
    ("preset-position-corpus-v1", "world-velocity-drives-the-reset", P.A_WORLD,
     "the world velocity block in EBX; w25 and w27 move on all 799 rows and w26 on 379"),
    ("preset-position-corpus-v1", "gravity-global-drives-w13", P.G_GRAVITY,
     "the gravity global at 0x01DE94A0; w13 moves on 420 of the 799 rows"),
    ("preset-position-jump-v1", "world-velocity-drives-the-reset", P.A_WORLD,
     "the world velocity block in EBX; w25 and w27 move on all 801 rows"),
    ("preset-position-jump-v1", "gravity-global-drives-w13", P.G_GRAVITY,
     "the gravity global at 0x01DE94A0; w13 moves on 743 of the 801 rows"),
    ("preset-position-stairs-v1", "world-velocity-drives-the-reset", P.A_WORLD,
     "the world velocity block in EBX; w25 and w27 move on all 437 rows"),
    ("preset-position-stairs-v1", "gravity-global-drives-w13", P.G_GRAVITY,
     "the gravity global at 0x01DE94A0; w13 moves on 396 of the 437 rows"),
)

for _fx, _name, _addr, _basis in _BLOCK_CONTROLS:
    _block_control(_fx, _name, "break", _basis, _addr)


# ---------------------------------------------------------------- drop-the-call
# The floor every driven fixture stands on: strip every OP_CALL out of the row op-streams
# and the fixture must fail. It is the one control whose claim is about the whole plan
# rather than one column -- "this record is not reproduced by the plan alone" -- and it is
# the rule G2b finding 2 asks for, enforced rather than asserted. `run.py` already applies
# it to every fixture and fails one that survives; registering it here puts it in the
# control census and the per-fixture report as well.
def _no_call_control(fixture: str) -> None:
    @control(fixture, "drop-the-call", "break",
             "every OP_CALL removed from the row op-stream (run.py `strip_calls`): the "
             "pokes, the register writes and the peeks without the lifted code between "
             "them. A fixture that still reproduces its record compares nothing the lift "
             "produced.")
    def _build(rows, header, sections, _f=fixture):
        plan = P.BUILDERS[_f](rows, header, sections)
        plan.rows = [R.strip_calls(row) for row in plan.rows]
        return plan


for _fx in sorted(P.BUILDERS):
    _no_call_control(_fx)


# ---------------------------------------------------------------- driver
def run_controls(fixture: str, binary: Path) -> list[dict]:
    header, rows, secs = FR.load(fixture)
    baseline = P.BUILDERS[fixture](rows, header, secs)
    baseline_bytes = baseline.encode()
    # Which of this fixture's columns the lifted code decides, by column INDEX: the
    # baseline's no-call run says so, and an index survives a builder that renames its
    # columns (`ground-acceptance-v1` names them after the boundary it is compared at,
    # and its controls build the underlying SA2GPW1 plan, which does not).
    _informative_names = set((R.no_call_report(baseline, binary, fixture)
                              or {}).get("informative_column_names") or ())
    informative = {k for k, name in enumerate(baseline.columns)
                   if name in _informative_names}
    out = []
    for c in CONTROLS.get(fixture, []):
        plan = c["build"](rows, header, secs)
        if plan.encode() == baseline_bytes:
            raise SystemExit(
                f"{fixture} control {c['name']}: its encoded plan is byte-identical to the "
                f"baseline's, so it drives nothing different and its verdict is vacuous. "
                f"Perturb something the plan actually writes, or delete the control.")
        plan.fixture = f"{fixture}.control.{c['name']}"
        r = R.drive(plan, binary, None, with_no_call=False)
        cols = _column_report(plan, r)
        entry = {"control": c["name"], "kind": c["kind"], "basis": c["basis"],
                 "rows": r["rows"], "rows_matching_native": r.get("rows_matching_native"),
                 "fixture_passed_under_control": bool(r.get("passed")),
                 "columns": cols, "status_counts": r.get("status_counts")}
        if c["kind"] == "break":
            split = _call_dependence(plan, binary)
            entry["columns"].update(split)
            moves_lifted_output = bool(split["call_dependent_divergences"]) or bool(
                set(cols.get("diverged_indices") or ()) & informative)
            entry["moves_a_column_the_lift_decides"] = moves_lifted_output
            entry["control_holds"] = (not r.get("passed")) and moves_lifted_output
        else:
            entry["control_holds"] = {"inert": bool(r.get("passed")), "probe": True}[c["kind"]]
        out.append(entry)
    return out


def _call_dependence(plan, binary: Path) -> dict:
    """Split this control's divergences into the ones the lifted code produced and the
    ones the plan produced on its own, by re-driving the same rows with every OP_CALL
    stripped (tools/s2/corpus_run/run.py `strip_calls`)."""
    with_call = OUT / "out" / f"{plan.fixture}.out.bin"
    empty = {"call_dependent_divergences": {}, "call_independent_divergences": {}}
    if not with_call.exists():
        return empty
    if not any(op == P.OP_CALL for row in plan.rows for op, _i, _n in R.walk_ops(row)):
        return empty                    # `drop-the-call` itself: it IS the no-call run
    _va, obs = R.read_out(with_call)
    probe = P.Plan(f"{plan.fixture}.nocall", plan.entry_va, plan.out_words, plan.columns,
                   [R.strip_calls(row) for row in plan.rows], plan.expected, plan.abi,
                   globals_ops=plan.globals_ops)
    rc, nocall, _secs, _proc = R.execute(probe, binary, probe.fixture)
    if rc != 0 or not nocall:
        return {"call_dependent_divergences": {}, "call_independent_divergences": {}}
    dependent: dict[str, int] = {}
    independent: dict[str, int] = {}
    for o, n, e in zip(obs, nocall, plan.expected):
        got, without = o[2:], n[2:]
        for k, name in enumerate(plan.columns):
            if k >= len(e) or k >= len(got) or k >= len(without) or got[k] == e[k]:
                continue
            bucket = dependent if got[k] != without[k] else independent
            bucket[name] = bucket.get(name, 0) + 1
    return {"call_dependent_divergences": dependent,
            "call_independent_divergences": independent}


def _column_report(plan, result) -> dict:
    """Per-column divergence and poison counts, read back out of the driver's output."""
    of = OUT / "out" / f"{plan.fixture}.out.bin"
    if not of.exists():
        return {}
    _va, obs = R.read_out(of)
    diverged: dict[str, int] = {}
    diverged_indices: set[int] = set()
    poisoned: dict[str, int] = {}
    for o, e in zip(obs, plan.expected):
        got = o[2:]
        for k, name in enumerate(plan.columns):
            if k >= len(got) or k >= len(e):
                continue
            if got[k] != e[k]:
                diverged[name] = diverged.get(name, 0) + 1
                diverged_indices.add(k)
            if got[k] == P.POISON:
                poisoned[name] = poisoned.get(name, 0) + 1
    return {"rows": len(obs), "diverged": diverged,
            "diverged_indices": sorted(diverged_indices),
            "still_poisoned": sorted(poisoned)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="pc24")
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    binary = OUT / "build" / f"corpus_driver_{args.variant}"
    if not binary.exists():
        raise SystemExit(f"{binary} is not built; run tools/s2/corpus_run/build.py")
    # Every fixture the corpus drives carries at least one `break` control: an assertion
    # that can fail for a reason about the lifter. Thirty-one of the 54 passing fixtures
    # carried none at all before this fold (G2b finding 5), and the two the wrap attests
    # rather than drives still carry none -- they are driven by tools/s2/wrap, which this
    # unit does not own, and the receipt says so instead of this check passing silently.
    uncontrolled = sorted(f for f in P.BUILDERS
                          if not any(c["kind"] == "break" for c in CONTROLS.get(f, [])))
    if uncontrolled and not args.only:
        raise SystemExit("fixtures with a plan and no asserting control: "
                         + ", ".join(uncontrolled))

    ids = args.only or sorted(CONTROLS)
    report = {}
    held = total = 0
    for fid in ids:
        report[fid] = run_controls(fid, binary)
        for c in report[fid]:
            total += 1 if c["kind"] != "probe" else 0
            held += 1 if (c["kind"] != "probe" and c["control_holds"]) else 0
            mark = "HOLDS" if c["control_holds"] else "FAILS"
            extra = (f"diverged {c['columns'].get('diverged')}" if c["kind"] != "inert"
                     else "")
            if c["kind"] == "break":
                extra = (f"call-dependent {c['columns'].get('call_dependent_divergences')}; "
                         f"plan-only {c['columns'].get('call_independent_divergences')}")
            if c["kind"] == "probe":
                extra = f"still poisoned: {c['columns'].get('still_poisoned')}"
                mark = "PROBE"
            print(f"  {mark} {fid} [{c['kind']}] {c['control']}: "
                  f"{c['rows_matching_native']}/{c['rows']} rows match; {extra}")
    js = args.out or (OUT / "controls.json")
    js.write_text(json.dumps(report, indent=1) + "\n")
    circular = [(f, c["control"]) for f, cs in report.items() for c in cs
                if c["kind"] == "break" and not c.get("moves_a_column_the_lift_decides")]
    print(f"{held}/{total} asserting controls hold over {len(report)} fixtures; "
          f"{len(circular)} break controls move nothing the lift decides -> {js}")
    for f, name in circular:
        print(f"  CIRCULAR {f} {name}")
    if held != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
