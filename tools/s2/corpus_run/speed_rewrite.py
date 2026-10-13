#!/usr/bin/env python3
"""Five rewrites of folded lifted IR that make it compile to fast code and compute the same
thing, in LLVM's default floating-point environment ("The floating-point environment" below).

Subtitle: applied by `internalize.rewrite` after the stub fold, so every lifted
object the build links (the map's modules, S3's arm traces, S4's extras) and
every object the fixture corpus and the emitter determinism gate compile is
compiled from the rewritten text. The recipe after it is unchanged:
`zig cc -target x86_64-linux-musl -O2 -w -c`.

Why
---
The lifted build ran far below real time and was CPU-bound. The S5 profile
(`private/generated/s5/perf/PROFILE.md`) found a large share of the CPU in
calls the folded IR forces on -O2: remill's float80 spills calling musl's
`memcpy` and `memset`, intrinsics whose runtime definition is an identity or a
zero, and the per-module x87 rounding helper. Its A/B rebuilt every lifted
object with these three passes (`private/generated/s5/perf/heavy/ab/
ab_rewrite_lib2.py`, of which this is the tracked version) and ran the boot and
Metal Harbor several times faster with the run record, the tick capture and the
word log equal at every tick. This module reproduces that experiment's IR text
exactly on every module the link uses
(`private/generated/s5/speed/SPEED-CODE.md`). The fourth pass came after them: the
x87 research unit found the x87 stack, the flags and the registers going through
State memory at every lifted instruction, and measured a local copy of State much
faster on the hottest x87 functions with State and outputs bit-identical
(`private/generated/s5/perf/x87/DESIGN.md`; `state_local.py`). The fifth makes the guest
memory accesses of those functions loads and stores in the function itself
(`private/generated/s5/next/fast/NOTES.md`; `state_local.py`, "The guest-memory fast path").

The passes
----------
spill    remill's float80 conversions copy ten bytes with `memcpy` and clear
         sixteen with `memset` between local allocas, and each call site
         carries the attribute group `{ nobuiltin nounwind "no-builtins" }`
         from remill's freestanding semantics build. `nobuiltin` forbids LLVM
         to treat the call as the C library function, so every one stays a
         real call and the stack round trip around it stays too. The pass
         points exactly those call sites at `#90001 = { nounwind }`, defined
         once at the end of the module, and only in their two literal forms,
         with local operands and that group:
           call ptr @memcpy(ptr noundef nonnull %a, ptr noundef nonnull %b, i64 noundef 10)
           call ptr @memset(ptr noundef nonnull %a, i32 noundef 0, i64 noundef 16)
         The callee is the same function; LLVM may now treat it as
         `llvm.memcpy` or `llvm.memset` and remove the round trip. Any other
         `memcpy` or `memset` call (another size, a size or fill that is not a
         constant, a global or computed operand, another parameter attribute,
         another group) keeps its group, so a call form remill starts to emit
         later stays a real call until someone has read it. Making these
         copies transparent is also what lets x87 constants reach arithmetic
         they never reached before ("The floating-point environment").
intrin   a call to an intrinsic the runtime defines as returning its first
         argument becomes that argument (`%r = or i1 %v, false`), and one the
         runtime defines as returning zero becomes zero (`%r = or i8 0, 0`,
         `%r = fadd float 0.000000e+00, 0.000000e+00`). Only the names in
         `RETURNS_ARGUMENT` and `RETURNS_ZERO`, only a call whose result is
         assigned, and for a zero only at the IR type the table names; any
         other call, including `__remill_undefined_f80`, is left alone.
pcround  the helpers `tools/spike/pc24pass/pc24pass.py` splices into each module
         (`__sa2_pc_round`, `__sa2_x87_round_int`, `__sa2_fistp_i16/32/64`) get
         `alwaysinline`, and only on an internal define line that carries no
         other function attribute, so the marking can never contradict a
         `noinline` or an `optnone`.
local    `state_local.localize`, last, over the other three's output: a lifted
         function whose x87 stack references outweigh its calls (the selection
         rule, `state_local.K`) works on an `alloca` copy of the State bytes it
         touches, copied in at entry and back out around every call that can see
         State and before `ret`, and the guest-memory intrinsics in it are
         ordered by their real effects; a function it cannot account for in full
         is left as it is. Its counts are `local`, `local_kept` (left by the
         rule) and `local_refused`. `state_local.py`'s docstring has the rewrite,
         its default-deny rules and its soundness argument.
fast     `state_local.fast`, after "local": in each localized function, the one- to
         eight-byte guest-memory intrinsic calls become loads and stores through a guest
         base loaded once on entry, behind the bounds the runtime applies, with the
         runtime's own checked path for every access outside them and for every access
         while the runtime wants them all (the poison tier). A module is taken whole or not
         at all: one with a ten-byte access in a localized function, or with a lifted
         function "local" left as it is, keeps its intrinsic calls. Its counts are `fast`
         and `fast_kept`.

What it never touches
---------------------
remill's memory intrinsics (`__remill_read_memory_*`, `__remill_write_memory_*`)
and every call with an effect: no pass inlines or moves one ("local" adds copies of State
around the calls that can see it and hands them the host State it was given), and only
"fast" replaces one, with the access it makes, never with a definition under remill's
attributes. remill declares the memory intrinsics `readnone` and threads a
`Memory *` token through them, which is sound only while they stay opaque calls: a
definition inlined under that attribute lets LLVM delete guest stores. No table
here names one, and `runtime_problems` holds every name the tables do list to a
runtime body that is a single `ret`, which has no effect to lose. The one change to
them is "local"'s, in the modules it localizes: their calls in a localized function,
and the declarations that claim less, say what they do (`memory(argmem: ...)` in place
of `readnone`), because a `readnone` read whose value no longer passes through State
memory can be sunk past a write to the same address; a function whose calls do not
come out with exactly that effect is not localized (`state_local.py`, "Ordering guest
memory"). "fast" then takes those calls out of a localized function altogether: its guest
accesses become real loads and stores, which LLVM orders by what they may alias, and the
runtime's checked path they fall back on is declared with no memory attribute at all, so
LLVM takes a call to it to read and write any memory and moves no guest access across it.

The floating-point environment
------------------------------
"intrin" and "pcround" are exact: the runtime returns those values, and the
helpers are integer code. "spill" is exact about memory and not about
arithmetic. Before it, x87 values went from one lifted instruction to the next
through the opaque copies above, so -O2 could not carry a constant from one
instruction into the next one's arithmetic. After it, it can: FLD1's 1.0 and
FLDZ's 0.0 reach `fmul`, `fadd` and `fdiv` on `x86_fp80`. The lifted IR carries no
`strictfp` and no constrained intrinsics, so LLVM folds and simplifies that
arithmetic in its default environment: round to nearest, with no exception
flag observed. In sub_007444F0, `fld1` then `fmul` became `x*1.0`, then `x`.
"local" is exact about memory too, and widens the same window: with State in an
`alloca`, the x87 stack is SSA values from one bracket point to the next, across
remill's barriers, so arithmetic in several guest instructions can meet a constant or
a common operand, where with State in memory it met only inside one instruction.
Every call that reads or sets the environment is a bracket point
(`state_local.FENV_CALLS`, and the libm calls by default), so State is stored before
it and loaded after it: State memory is ordered around a rounding change or a flag
read as it was without "local", and no better. LLVM does not know those calls touch
the environment, and State is a `noalias` argument, so until a function first hands
State to a call LLVM may take a plain `fesetround` not to touch it, forward the store
into the reload and compute an operation after the change from one before it; a
`readonly` declaration (remill's `fegetround`) reloads nothing at all. Both builds do
that (`state_local.py`, "Soundness"). What remains is the assumption itself inside each
window, as for "spill".
"fast" is exact about memory as well, and opens one more way into those windows: in a
localized function a guest store becomes a plain store LLVM can see, so a value stored to
guest memory -- a constant FLD1 or FLDZ put there, or a result -- can reach arithmetic
through a later load of the same address, where the read was an opaque call before. It adds
no bracket point and moves none. The compile census of every module it changes found no
object with fewer floating-point operations than the localized build's, and the ones with
more hold identical operations on the paths the helpers' branches keep apart
(`private/generated/s5/next/fast/NOTES.md`, "Evidence"). It does change which x87 operations
LLVM compiles with a memory operand, and with a signalling NaN the x87 picks its result by
operand form; the risk register's NaN-selection entry carries that.

The guest's environment is not that one. remill applies the rounding field of
the guest's control word to the host's floating-point environment with
`fesetround`, reads it back with `fegetround`, and reads the sticky exception
flags with `fetestexcept` (all three are calls in the lifted IR). LLVM does not
model what those calls do to the arithmetic around them. So the result can
differ from the x87's in four places:

  * an inexact operation on two constants, or FRNDINT (`llvm.nearbyint.f80`)
    on a constant, rounded to nearest while the guest's rounding control
    chops or rounds up or down;
  * `x - 0.0` or `x + -0.0` folded to `x`: for x = +0.0 that is +0.0, and
    rounding down gives -0.0;
  * a signaling NaN, or an encoding the x87 does not support, passed through
    a folded `x*1.0` or `x/1.0` unchanged, where the x87 quiets it and
    raises invalid;
  * a folded operation raises no host flag, so a later `fetestexcept` sees
    fewer flags than the x87 would have set.

No pass can check this: it holds or fails per guest path and rounding mode.
The check is the registered replays (`Documentation/replays.json`). Each pass
landed on an A/B that ran the boot and Metal Harbor with the run record, the
tick capture and the word log equal at every tick to the build before it ("fast":
`private/generated/s5/next/fast/landing/replays_equal.py`, against copies taken
before the rebuild). Their results are tied to the reach receipt,
which `Documentation/scripts/check_run_reach.py` ties to the objects and the
fold code, so a rebuild that moves the objects cannot be pushed until both have
run again (`tools/s4/tick_replay.py`), and `check_replay_regression.py` keeps
every registered replay in the gates at every later stage. A float divergence there
that goes away with this pass off (`rewrite(text, passes=("intrin", "pcround"))`, or
`--passes intrin,pcround` on one module) is this assumption failing; one that goes away
with "local" off (`--passes spill,intrin,pcround`) is the wider window failing, and one that
goes away with only "fast" off (`--passes spill,intrin,pcround,local`) is a guest-memory
value reaching that window. The risk register carries them (`Documentation/risks.json`,
after "x87 model wrong at PC24 edges").

Tied to the runtime
-------------------
"intrin" is exact only while `runtime/remill_runtime.cpp` defines each listed
intrinsic as returning its first argument or zero. `runtime_problems` reads the
runtime compiled to LLVM IR and names every listed intrinsic whose body is not
exactly that one `ret`, or that the runtime does not define;
`Documentation/scripts/tests/test_speed_rewrite.py` compiles the runtime to LLVM IR
with the command the link compiles it with (read from `tools/s4/link_s4.py`
`phase_compile`, not typed) and fails on any, and fails rather than skips when
the compiler or remill's headers are missing, so a runtime change the rewrite
would silently bypass fails the gates instead. "local" trusts its safe callees to take
no State and reach none: `state_local.runtime_problems` holds each one the runtime
defines to code that refers to nothing but guest memory and trap reporting, and
`state_local.helper_problems` holds the spliced helpers to pure code
(`Documentation/scripts/tests/test_state_local.py`, the same compile). "fast" trusts the
runtime three times: its bounds are guest_memory.h's (the test reads them from the header
and runs the helpers against `sa2_gp` at each edge); the slow halves it calls unbracketed
are held to the same rule as the safe callees; and the base it reads is NULL whenever the
runtime wants every access, while the slow halves test nothing the inline path skips (the
same test runs harness/guest_fast_probe.c over the runtime as the link compiles it, every
access both ways under each poison mode, and fails on any difference).

Determinism and idempotence
---------------------------
Text in, text out: regular expressions over lines, fixed group numbers, no
clock, no environment, and no unordered container reaching the output, so the
same input bytes give the same output bytes (the emitter determinism gate pins
the result through `internalize.rewrite`). A second application changes
nothing: a rewritten call site carries #90001, which is not the nobuiltin
group; a replaced call is gone; a marked helper's define line no longer ends in
`) {`; a localized function's define line names `%state.host`, which is not the
lifted signature, and a kept or refused function is decided the same way again; a
fast-path function holds no intrinsic call left to replace.

Usage:
  .venv/bin/python tools/s2/corpus_run/speed_rewrite.py <in.ll> <out.ll> \\
      [--passes spill,intrin,pcround,local,fast]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import state_local  # tools/s2/corpus_run/state_local.py, beside this file

# runtime/remill_runtime.cpp defines each of these as returning its first argument.
RETURNS_ARGUMENT = (
    "__remill_flag_computation_zero", "__remill_flag_computation_sign",
    "__remill_flag_computation_carry", "__remill_flag_computation_overflow",
    "__remill_compare_eq", "__remill_compare_neq",
    "__remill_compare_sgt", "__remill_compare_sge",
    "__remill_compare_slt", "__remill_compare_sle",
    "__remill_compare_ugt", "__remill_compare_uge",
    "__remill_compare_ult", "__remill_compare_ule",
)

# runtime/remill_runtime.cpp defines each of these as returning zero; the value is
# the IR type the call returns, and a call at any other type is left alone.
RETURNS_ZERO = {
    "__remill_undefined_8": "i8",
    "__remill_undefined_16": "i16",
    "__remill_undefined_32": "i32",
    "__remill_undefined_64": "i64",
    "__remill_undefined_f32": "float",
    "__remill_undefined_f64": "double",
    "__remill_fpu_exception_test_and_clear": "i32",
}

FLOAT_TYPES = ("float", "double")

# The passes, by the names their counts carry; `rewrite` runs all of them unless told otherwise.
PASSES = ("spill", "intrin", "pcround", "local", "fast")

# The helpers pc24pass.py splices into a module (pc_round.ll and x87_int.ll).
INLINED_HELPERS = ("__sa2_pc_round", "__sa2_x87_round_int",
                   "__sa2_fistp_i16", "__sa2_fistp_i32", "__sa2_fistp_i64")

NOBUILTIN_GROUP = '{ nobuiltin nounwind "no-builtins" }'
SPILL_GROUP = "90001"
SPILL_GROUP_BODY = "{ nounwind }"

_LOCAL = r"%[-\w.$]+"
_ATTRIBUTE_GROUP = re.compile(r"^attributes #(\d+) = (\{[^\n]*\})$", re.M)


def _one_of(names) -> str:
    return "|".join(re.escape(n) for n in names)


# Each of these is matched against one whole line. The spill call is matched in its two
# literal forms only (the module docstring, "spill"), so any other form keeps `nobuiltin`.
_SPILL_CALL = re.compile(
    rf"(\s*(?:{_LOCAL} = )?(?:tail )?call ptr "
    rf"(?:@memcpy\(ptr noundef nonnull {_LOCAL}, ptr noundef nonnull {_LOCAL}, i64 noundef 10\)"
    rf"|@memset\(ptr noundef nonnull {_LOCAL}, i32 noundef 0, i64 noundef 16\))) #(\d+)")
_ARGUMENT_CALL = re.compile(
    rf"(\s*)({_LOCAL}) = (?:tail )?call zeroext i1 (?:\(i1, \.\.\.\) )?"
    rf"@({_one_of(RETURNS_ARGUMENT)})\(i1(?: [a-z]+)* ({_LOCAL}|true|false)[,)][^\n]*")
_ZERO_CALL = re.compile(
    rf"(\s*)({_LOCAL}) = (?:tail )?call (?:zeroext |signext )?(i8|i16|i32|i64|float|double) "
    rf"@({_one_of(RETURNS_ZERO)})\([^\n]*")
_HELPER_DEFINE = re.compile(
    rf"(define internal [^\n]*@(?:{_one_of(INLINED_HELPERS)})\([^\n]*\)) \{{")


def _attribute_groups(text: str) -> dict[str, str]:
    """The module's `attributes #N = { ... }` lines, by N. LLVM prints them after the
    functions, so the scan starts at the first one."""
    first = -1 if text.startswith("attributes #") else text.find("\nattributes #")
    return dict(_ATTRIBUTE_GROUP.findall(text, first + 1))


def _spill(line: str, nobuiltin: set[str]) -> str | None:
    """A `memcpy`/`memset` call site carrying the nobuiltin group, pointed at #90001."""
    m = _SPILL_CALL.fullmatch(line)
    if m and m.group(2) in nobuiltin:
        return f"{m.group(1)} #{SPILL_GROUP}"
    return None


def _intrin(line: str) -> str | None:
    """An assigned call to a listed intrinsic, as the value its runtime body returns."""
    m = _ARGUMENT_CALL.fullmatch(line)
    if m:
        return f"{m.group(1)}{m.group(2)} = or i1 {m.group(4)}, false"
    m = _ZERO_CALL.fullmatch(line)
    if m and RETURNS_ZERO[m.group(4)] == m.group(3):
        ty = m.group(3)
        zero = f"fadd {ty} 0.000000e+00, 0.000000e+00" if ty in FLOAT_TYPES else f"or {ty} 0, 0"
        return f"{m.group(1)}{m.group(2)} = {zero}"
    return None


def _pcround(line: str) -> str | None:
    """A spliced helper's define line, marked `alwaysinline`."""
    m = _HELPER_DEFINE.fullmatch(line)
    return f"{m.group(1)} alwaysinline {{" if m else None


def rewrite(text: str, passes=PASSES) -> tuple[str, dict[str, int]]:
    """The passes named in `passes` (default all five, `PASSES`) over one folded module:
    the first three in one walk over its lines, then "local" over their output, then "fast"
    over its. Returns the text and the count each pass rewrote, 0 for a pass left out;
    "local" also counts the lifted functions its rule kept (`local_kept`) and those it
    refused (`local_refused`), and "fast" the localized functions it left with their
    intrinsic calls (`fast_kept`). Leaving one out is the experiment the risk register
    names (`passes=("intrin", "pcround")` for the spill pass, `("spill", "intrin",
    "pcround")` for "local" and "fast", `("spill", "intrin", "pcround", "local")` for
    "fast"); the build always runs all five. The substring test in front of each of
    the first three is implied by its pattern, so it only skips lines the pattern could
    not match (a regular expression over the whole text cost several times the stub fold
    itself)."""
    unknown = sorted(set(passes) - set(PASSES))
    if unknown:
        raise ValueError(f"no speed pass is named {unknown}; the passes are {list(PASSES)}")
    spill, intrin, pcround, local, fast = (name in passes for name in PASSES)
    groups = _attribute_groups(text)
    nobuiltin = {number for number, body in groups.items() if body == NOBUILTIN_GROUP}
    counts = {"spill": 0, "intrin": 0, "pcround": 0,
              "local": 0, "local_kept": 0, "local_refused": 0, "fast": 0, "fast_kept": 0}
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "call " in line:
            if spill and "@mem" in line and (new := _spill(line, nobuiltin)) is not None:
                lines[i] = new
                counts["spill"] += 1
            elif intrin and "@__remill_" in line and (new := _intrin(line)) is not None:
                lines[i] = new
                counts["intrin"] += 1
        if pcround and line.startswith("define internal ") and "@__sa2_" in line \
                and (new := _pcround(line)) is not None:
            lines[i] = new
            counts["pcround"] += 1
    text = "\n".join(lines)
    if counts["spill"]:
        have = groups.get(SPILL_GROUP)
        if have is None:
            text += f"\nattributes #{SPILL_GROUP} = {SPILL_GROUP_BODY}\n"
        elif have != SPILL_GROUP_BODY:
            raise ValueError(f"attribute group #{SPILL_GROUP} is already defined as {have}, "
                             f"so the spill pass cannot use it for {SPILL_GROUP_BODY}")
    if local:
        text, localized = state_local.localize(text)
        counts.update(localized)
    if fast:
        text, fasted = state_local.fast(text)
        counts.update(fasted)
    return text, counts


_RUNTIME_DEFINE = re.compile(r"^define [^\n]*@([\w.$]+)\(([^\n]*?)\)[^\n()]*\{\n(.*?)^\}$",
                             re.M | re.S)
_METADATA = re.compile(r"(, ![\w.]+ ![\w.]+)+$")


def runtime_problems(runtime_ir: str) -> list[str]:
    """Every listed intrinsic whose definition in `runtime_ir` (the runtime compiled to
    LLVM IR) is not exactly `ret` of its first argument or of zero; [] when all are."""
    bodies = {}
    for m in _RUNTIME_DEFINE.finditer(runtime_ir):
        lines = [_METADATA.sub("", line.strip()) for line in m.group(3).splitlines()]
        bodies[m.group(1)] = (m.group(2), [line for line in lines
                                           if line and not line.startswith("#dbg_")])
    problems = []
    for name in RETURNS_ARGUMENT:
        if name not in bodies:
            problems.append(f"{name}: the runtime does not define it")
            continue
        params, body = bodies[name]
        first = params.split(",")[0].split()[-1] if params.strip() else None
        if body != [f"ret i1 {first}"]:
            problems.append(f"{name}: the rewrite replaces it with its first argument, and the "
                            f"runtime's body is {body}")
    for name, ty in RETURNS_ZERO.items():
        if name not in bodies:
            problems.append(f"{name}: the runtime does not define it")
            continue
        zero = "0.000000e+00" if ty in FLOAT_TYPES else "0"
        if bodies[name][1] != [f"ret {ty} {zero}"]:
            problems.append(f"{name}: the rewrite replaces it with {ty} {zero}, and the "
                            f"runtime's body is {bodies[name][1]}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply the speed rewrite to one folded module.")
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--passes", default=",".join(PASSES),
                    help=f"the passes to run, comma-separated (default {','.join(PASSES)})")
    args = ap.parse_args()
    text, counts = rewrite(args.src.read_text(errors="replace"),
                           passes=tuple(p for p in args.passes.split(",") if p))
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(text)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
