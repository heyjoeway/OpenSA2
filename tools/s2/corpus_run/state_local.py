#!/usr/bin/env python3
"""State localization: a lifted function works on a local copy of the State fields it
touches, so -O2 keeps the x87 stack, the flags and the registers in SSA values.

Subtitle: the fourth speed pass ("local", `speed_rewrite.PASSES`), applied by
`internalize.rewrite` after the other three on every fold path. The recipe after it is
unchanged: `zig cc -target x86_64-linux-musl -O2 -w -c`.

Why
---
remill passes State by pointer, and LLVM never promotes memory behind an argument to SSA:
SROA and mem2reg work only on allocas. The eight-slot x87 shift is a chain of ten-byte
copies on State that GVN cannot forward through, and remill's `BarrierReorder()` (an empty
asm with a memory clobber) clobbers every escaped object, State included, since State is
passed to `__remill_function_return` and to every lifted callee. So every x87 operation
stores to State and loads back. The x87 research unit
(`private/generated/s5/perf/x87/DESIGN.md`) measured this rewrite on njCalcPoint
(sub_00426CC0), the CRI biquad (sub_00842500) and the 4x4 inverse (sub_00424FF0) at about
half the time per call, with the whole State and the output memory hashing identically over
normal inputs, special values and the three directed rounding modes; the landing measured
the same objects again (`private/generated/s5/x87land/IMPLEMENT.md`).

The rewrite
-----------
For a lifted function `define ptr @sub_X(ptr noalias %state, i32 %program_counter, ptr
noalias %memory)`:

  * the parameter becomes `%state.host` and `%state` becomes `alloca %struct.State` in the
    entry block, so every existing use of `%state` now reads and writes the local copy;
  * the State bytes the function can reach (below) are copied in at entry;
  * every call to a callee outside `SAFE_CALLEES` is a bracket point: the bytes are copied
    back before it, the call receives `%state.host` where it received `%state`, and they
    are copied in again after it, unless the next instruction is `ret` or `unreachable`;
  * every `ret` not directly after a bracket point copies the bytes back first.

A bracketed tail call stays `tail call ... ; ret`: the copy back goes before the call and
nothing goes between it and the `ret`, and the call still receives the host State, never a
pointer into the caller's frame, so the backend still emits it as a `jmp` (the constant-stack
continuation `__remill_error` relies on, runtime/remill_runtime.cpp).

Which bytes: every access through a pointer derived from `%state` by constant-index GEPs
(a load, a store, a `memcpy`/`memmove`/`memset` of constant length), widened to the State
units it overlaps -- a unit is the largest enclosing aggregate of at most 16 bytes: one x87
slot, one GPR with its padding, the arithmetic flags, one FPU status flag -- and merged where
adjacent. Offsets come from the module's own `%struct.*` definitions under
x86_64-linux-musl's data layout, the target every fold is compiled for (`Layout`; the lifted
text carries remill's i386 layout, which clang replaces).
`Documentation/scripts/tests/test_state_local.py` holds the engine to LLVM's own byte
offsets on real modules and on types with implicit padding.

Ordering guest memory
---------------------
remill declares its guest-memory intrinsics (`__remill_read_memory_*`,
`__remill_write_memory_*`) `readnone` and orders them only through the Memory token they
pass along, so a read is kept before a later write to the same address only by a use of
its value that must stay there -- with State in memory, the store of the value to State.
Once State is local that store is an SSA value and InstCombine sinks the read into the block
that uses it, past the write: the read returns the new value. (The S2 fixture corpus caught
it: `ground-executor-multi-v1`, through sub_0048CE40.) So in a module where a function is
localized, each intrinsic is given the effect it has (`intended`): a read reads the memory
behind its token (`memory(argmem: read)`); a write, and the ten-byte read that fills its
`out` operand, read and write it (`memory(argmem: readwrite)`).

  * Each guest-memory call in a localized function is pointed at a copy of its attribute
    group with every memory attribute taken out and the effect put in: `memory(...)` and
    the older `readnone`, `readonly`, `writeonly`, `argmemonly`, `inaccessiblememonly` and
    `inaccessiblemem_or_argmemonly`, which LLVM's parser turns into a memory effect that
    replaces any `memory(...)` beside them (`effect_of`).
  * Each guest-memory declaration loses `readnone`, `readonly` and `writeonly` on its
    pointer parameters. One whose own attributes do not allow the effect (remill's
    `readnone`) is pointed at a copy of its group made the same way; one whose attributes
    already allow it is left as it is -- the ten-byte accessors, which
    tools/spike/pc24pass/pc24pass.py `--fix-f80-accessors` freed of `readnone` in every
    fold.
  * A trailing comment after the group (the PC24 pass writes `   ; sa2-pc24pass f80` on
    those declarations and their groups) is read past and kept. A guest-memory call with
    anything else after its arguments -- attributes written out, an operand bundle,
    metadata -- refuses the function.

LLVM gives a call the effect its call site's attributes and its callee's declaration
allow together (it intersects them), so a guest-memory call has exactly its intrinsic's
effect only if both agree. The pass does not take that on trust: `ordering_problems`
computes the effect of every guest-memory call in each localized function of the output,
and a function with one that does not come out exactly, or with a memory attribute on a
pointer operand, is refused and the module made again without it. State never reaches these
calls (a State pointer given to one refuses the function), so the local copy stays SSA. A
call site in a function the pass did not localize keeps its own `readnone`, which still
decides its effect, so that function compiles as before. A localized function that calls a
lifted function this module defines and leaves as it is (which LLVM could inline, bringing
its unordered calls along) is refused too. The other direction is not refused: a kept
function that calls a localized one of its module, which -O2 inlines into it. That it still
compiles as before is measured, not built in: compiled, the shape gives the kept caller's
guest calls the order the build without the pass gives them
(test_state_local.py `test_a_kept_function_that_inlines_a_localized_one_compiles_as_before`),
and the census of 2026-10-02 found no such pair in the 15 modules that mix the two. The same
hazard exists without localization wherever a read's value stays in SSA, but there the store
to State that pins the read is almost always in its block; the risk register carries it.

Default deny
------------
A function is left exactly as it is, and counted in `local_refused`, when anything about it
is not accounted for: a use of `%state` or of a pointer derived from it other than a
constant-index GEP, a load, a store to it, a `mem*` call of constant length on a safe
callee whose result is not assigned, the argument `%state` itself to a bracketed call, or
remill's own `store ptr %state, ptr %STATE`; a derived pointer passed to a bracketed call,
stored, compared, or merged by a `phi` or `select`; any use of `%STATE` but that store; a
GEP with a variable index or outside State; a type the layout engine does not model (a
vector); a `tail` call given a pointer into State (the marker says the callee touches no
`alloca` of the caller, which the local copy is); a call it cannot parse, an `invoke`, a
`callbr` or a `musttail`; inline asm other than remill's empty barrier; a guest-memory call
it cannot order exactly (above); a name the rewrite would add already in use; a lifted
function's define line it does not know. `REFUSALS` lists every kind, and every reason the
pass gives starts with one of them. A callee it does not know is bracketed, never trusted: only
`SAFE_CALLEES` and the LLVM intrinsic families in `SAFE_INTRINSICS` are not, and each of
those takes no State and cannot reach it.

The selection rule
------------------
Copying the units in and out costs a load and a store per unit at every bracket point that
reloads, so a function with few x87 stack references and many calls gets slower. A function
is localized only when its x87 stack references (GEPs on `%state` into `State.st`) are at
least `K` (32) times its reloading bracket points plus one; otherwise it is left as it is
and counted in `local_kept`. The research unit chose K from compiled samples, where the
rule kept the integer, call-heavy code that localization makes bigger and selected the x87
kernels; the landing's stratified sample measured it again and kept it
(`private/generated/s5/x87land/IMPLEMENT.md`, "The selection rule").

Soundness
---------
The rewrite computes what the function computed if nothing reads or writes its State, while
it runs, except through the pointer it hands a bracketed call. The runtime's ways to State
all take it as a call argument and are bracketed (`__remill_function_call`, `__remill_jump`,
the hyper-calls, the dispatch stub, `__remill_error`, `__remill_missing_block`, every lifted
callee, which is how the import handlers and guest callbacks see it); one guest thread runs at
a time and hands off only inside imports, which are bracketed; the signal handlers read host
data only (DESIGN.md "Soundness", with file and line for each).

`SAFE_CALLEES` is tied to the runtime: `runtime_problems` reads the runtime compiled to LLVM
IR and names each safe callee whose definition, or anything that definition reaches inside
the runtime, refers to a symbol outside `RUNTIME_REFERENCES` -- guest memory and trap
reporting, none of which is given State -- or holds inline asm or an indirect call; and
`helper_problems` holds the spliced PC24 helpers (pc_round.ll, x87_int.ll) to code with no
memory access, no call outside the helpers, and no global.
`Documentation/scripts/tests/test_state_local.py` runs both against the runtime as the link
compiles it and the helpers as the pass splices them.

The floating-point environment is the exception to "nothing else reads State": the x87's
rounding control lives in the host's environment (`fesetround`), and LLVM does not model it.
So `fesetround`, `fegetround`, `fetestexcept`, `feraiseexcept`, `feclearexcept` and the rest
of `<fenv.h>` (`FENV_CALLS`) are never safe, and the libm calls are not safe either: each is
a bracket point, so the State bytes are written back before it and read again after it.
That orders State memory around these calls as the build without this pass did; it does not
keep arithmetic on State-carried values on its own side of a rounding change. LLVM does not
know these calls touch the environment, and State is a `noalias` argument: until the function
first passes the host State to a call, LLVM may take a plain `nounwind` `fesetround` not to
touch it, forward the copy back into the reload after it, and compute an operation on the
reloaded values from the one before the change; a call declared `readonly` (remill's
`fegetround`) writes no memory, so nothing is reloaded after it at all. The build without the
pass is exposed in the same way (`private/generated/s5/x87land/fold/fenv/`), and the risk
register's default-environment entry carries it. The rest of the default-environment
assumption is `speed_rewrite`'s ("The floating-point environment"): arithmetic between two
bracket points is folded as round-to-nearest with no flag observed, and localization lets
more of it meet.

What is not exact: after a trap (a fault in a memory intrinsic, a host fault, the timeout
alarm) the host State is the one the last bracket point wrote, not the one the trapping
instruction saw. The build before this pass was already imprecise there (LLVM sinks State
stores past the `readnone` memory intrinsics). A trapped State is read by `sa2_call`'s
report (the State it hands back), by `run_guest_thread` for the exit code of a thread that
trapped without calling ExitThread, and by the run record's `trap.esp` and `trap.stack`
(harness/crt_probe.c). A trap raised inside an import is at a bracket point, where State is
exact, and in the registered runs every trap is one: ExitThread, and ExitProcess for the run.
The risk register carries both this and the environment assumption.

The guest-memory fast path
--------------------------
`fast`, the fifth speed pass ("fast", `speed_rewrite.PASSES`), runs on the local pass's
output. In a localized function every guest-memory intrinsic call was an out-of-line call
into the runtime; the fast path makes it a load or a store in the function itself:

  * the entry block loads `@sa2_guest_fast_base` (runtime/guest_memory.h, "the inlined fast
    path") once, into `%sl.base`; the runtime sets it at guest init and never while lifted
    code runs, and leaves it NULL while the poison tier is on;
  * each of the twelve one-to-eight-byte intrinsic calls (`FAST_ACCESS`) becomes a call to an
    internal `alwaysinline` helper the pass splices into the module (`_fast_helper`): when
    the base is not NULL and the address lies in [GUEST_NULL_GUARD, GUEST_END - size] (one
    unsigned compare, the range `sa2_gp` accepts), the access goes through `base + address`;
    otherwise the helper asks the runtime's `sa2_guest_read_slow` or `sa2_guest_write_slow`
    for the pointer -- the intrinsics' own `checked_read` and `checked_write`, so a fault
    traps with the intrinsic's status, address and note, and the poison tier tests and marks
    as it did -- and makes the access through that. Either way the access is a plain `load`
    or `store` of the intrinsic's type and width, with `align 1`, as the runtime's `memcpy`.

Ordering. A fast-path function holds no guest-memory intrinsic call: its guest accesses are
real loads and stores, which LLVM orders by what they may alias, and nothing about them is
claimed by an attribute. The slow halves are declared with no memory attribute at all
(not `memory(none)`, which is the opposite): LLVM treats a call to one as reading and
writing any memory and as one that may not return, so it moves no guest access across one,
and guest memory at a fault is what the guest's program order left there. Every bracket
point that can reach guest memory -- lifted callees, the continuations, the dispatch stub,
the hyper-calls -- carries no memory attribute either, so a store before one is made before
it. `fast_problems` checks the output: no guest-memory or I/O-port intrinsic left in a
fast-path function, every helper given `%sl.base`, the base loaded once, where the pass puts
it.

At a fault the fast path is the intrinsics for every access that executes, which is not
quite what the localized build did. A read nothing uses is no longer deleted with its fault
(the localized build's `memory(argmem: read) willreturn` call could be), so it faults where
the guest's read would; and under SA2_GUEST_POISON=2 every read that executes is counted,
where -O2 used to merge or delete some, so the survey's counts can only rise. Both are
closer to the guest.

What it leaves alone, and why (`FAST_KEPT`; counted in `fast_kept`): a localized function
with a ten-byte access (`__remill_read_memory_f80`, `__remill_write_memory_f80`), whose
calls stay ordered only against their Memory token and would not be against real stores;
one with a guest-memory call it cannot read; one that calls a guest-memory or I/O-port
intrinsic it does not replace (the sixteen-byte forms and the port reads and writes, which
remill declares `readnone` and the runtime makes trap), which would stay ordered only by its
token beside real loads and stores; every localized function of a module that also defines a
lifted function the fast path does not rewrite, so that -O2 never inlines a function whose
guest accesses are calls into one whose accesses are not, or the reverse; and every
localized function of a module whose output `fast_problems` names. Such a module compiles as
it did after the local pass.

The bounds are the runtime's: `test_state_local.py` reads SA2_GUEST_NULL_GUARD and
SA2_GUEST_END from guest_memory.h and holds GUEST_NULL_GUARD and GUEST_END to them, and runs
the helpers against `sa2_gp` at every edge. An access the fast path makes inline is one
`sa2_gp` accepts; an access it does not make goes to the runtime, which decides as before.
That holds only while the runtime keeps its side (guest_memory.h, "the inlined fast path"):
the base NULL whenever the runtime wants every access, and the slow halves the intrinsics'
own checked path, which tests nothing but that range and the poison tier. The test builds
harness/guest_fast_probe.c over the runtime as the link compiles it and holds every access
through these helpers to the same access through the intrinsics, byte for byte, at the edges
of the guest space under each poison mode, and holds the base's lifecycle.

Floating point. A localized function's guest stores are now visible to LLVM, so a value it
stores to guest memory, a constant included, can reach arithmetic through a later load of
the same address. `speed_rewrite`'s "The floating-point environment" carries what that
widens and what was measured.

Determinism and idempotence
---------------------------
Text in, text out: lines and regular expressions, no clock, no environment, no unordered
container reaching the output. A localized function's define line names `%state.host`, so
it no longer matches the lifted signature and a second application leaves it alone; a kept
or refused function comes out as it went in. A fast-path function holds no intrinsic call
and loads `%sl.base`, so `fast` leaves it alone the second time, and decides a kept one the
same way again.

Usage:
  .venv/bin/python tools/s2/corpus_run/state_local.py <in.ll> <out.ll> [--k 32] [--fast]
"""
from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path

# A function is localized when its x87 stack references are at least K times its reloading
# bracket points plus one (the module docstring, "The selection rule").
K = 32

# Callees that take no State and cannot reach it, so a call to one is not a bracket point.
# Each runtime definition is held to that by `runtime_problems`, each spliced helper by
# `helper_problems`; memcpy, memmove and memset are the C library's.
SAFE_CALLEES = frozenset({
    # guest memory: bounds-checked reads and writes of guest memory (remill_runtime.cpp)
    "__remill_read_memory_8", "__remill_read_memory_16", "__remill_read_memory_32",
    "__remill_read_memory_64", "__remill_read_memory_f32", "__remill_read_memory_f64",
    "__remill_read_memory_f80",
    "__remill_write_memory_8", "__remill_write_memory_16", "__remill_write_memory_32",
    "__remill_write_memory_64", "__remill_write_memory_f32", "__remill_write_memory_f64",
    "__remill_write_memory_f80",
    # identities and zeros (speed_rewrite's tables, and the ones it leaves as calls)
    "__remill_flag_computation_zero", "__remill_flag_computation_sign",
    "__remill_flag_computation_carry", "__remill_flag_computation_overflow",
    "__remill_compare_eq", "__remill_compare_neq", "__remill_compare_sgt",
    "__remill_compare_sge", "__remill_compare_slt", "__remill_compare_sle",
    "__remill_compare_ugt", "__remill_compare_uge", "__remill_compare_ult",
    "__remill_compare_ule",
    "__remill_undefined_8", "__remill_undefined_16", "__remill_undefined_32",
    "__remill_undefined_64", "__remill_undefined_f32", "__remill_undefined_f64",
    "__remill_undefined_f80", "__remill_fpu_exception_test_and_clear",
    "__remill_barrier_load_load", "__remill_barrier_load_store",
    "__remill_barrier_store_load", "__remill_barrier_store_store",
    "__remill_atomic_begin", "__remill_atomic_end",
    "__remill_delay_slot_begin", "__remill_delay_slot_end",
    # the helpers tools/spike/pc24pass/pc24pass.py splices into each module
    "__sa2_pc_round", "__sa2_x87_round_int",
    "__sa2_fistp_i16", "__sa2_fistp_i32", "__sa2_fistp_i64",
    # the C library's copies
    "memcpy", "memmove", "memset",
})

# The spliced helpers among SAFE_CALLEES (`helper_problems`), and the C library's.
HELPERS = ("__sa2_pc_round", "__sa2_x87_round_int",
           "__sa2_fistp_i16", "__sa2_fistp_i32", "__sa2_fistp_i64")
LIBC = ("memcpy", "memmove", "memset")

# remill's guest-memory intrinsics among SAFE_CALLEES: the calls the pass orders (the module
# docstring, "Ordering guest memory").
GUEST_MEMORY = frozenset(n for n in SAFE_CALLEES
                         if n.startswith(("__remill_read_memory_", "__remill_write_memory_")))

# LLVM intrinsics that are not bracket points, by family (the name up to its type suffix).
# None reads the floating-point environment; one given a State pointer must be a mem*
# transfer of constant length (accounted like a load or store) or the function is refused.
SAFE_INTRINSICS = ("llvm.lifetime.start", "llvm.lifetime.end", "llvm.memcpy",
                   "llvm.memmove", "llvm.memset", "llvm.ctpop", "llvm.ctlz", "llvm.cttz",
                   "llvm.fabs", "llvm.fshl", "llvm.fshr", "llvm.abs", "llvm.trunc",
                   "llvm.nearbyint", "llvm.bswap", "llvm.smax", "llvm.smin",
                   "llvm.umax", "llvm.umin", "llvm.expect", "llvm.assume")

# The C library's floating-point environment. Never safe: each is a bracket point (the
# module docstring, "Soundness").
FENV_CALLS = ("fesetround", "fegetround", "fetestexcept", "feraiseexcept", "feclearexcept",
              "fegetenv", "fesetenv", "feholdexcept", "feupdateenv", "fegetexceptflag",
              "fesetexceptflag")

# What a safe callee's runtime definition may refer to, besides other definitions in the
# runtime that keep to this list themselves, LLVM intrinsics and private string constants:
# the guest memory (guest_memory.h) and trap reporting (sa2_trap.h) the memory intrinsics
# use. None of them is given State, and the trap path does not return.
RUNTIME_REFERENCES = frozenset({
    "sa2_guest", "sa2_guest_fast_base", "sa2_guest_poison", "sa2_guest_poison_reads", "sa2_guest_poison_first",
    "sa2_guest_is_poison", "sa2_guest_note_write", "sa2_guest_region_of",
    "sa2_trap_note", "sa2_trap_raise",
})

# remill's BarrierReorder(): an empty asm with a memory clobber. It emits nothing and orders
# accesses to escaped memory; the local copy is not escaped memory.
BARRIER = 'call void asm sideeffect "", "~{memory},~{dirflag},~{fpsr},~{flags}"()'

# The LLVM intrinsics among the safe callees that may be given a State pointer, with LIBC.
MEM_INTRINSICS = ("llvm.memcpy", "llvm.memmove", "llvm.memset")

# Why a function is left as it is and counted in `local_refused` (the module docstring,
# "Default deny"): every reason the pass gives starts with one of these, and
# Documentation/scripts/tests/test_state_local.py has a case for each.
REFUSALS = (
    "a lifted function's define line the rewrite does not know",
    "the layout engine cannot place State",
    "a name the rewrite adds is already in use",
    "an invoke or callbr",
    "a call the rewrite cannot read",
    "a musttail call",
    "inline asm other than remill's barrier",
    "State outside a call's arguments",
    "a guest-memory call with attributes, bundles or metadata after its arguments",
    "a guest-memory call carrying an attribute group the module does not define",
    "a tail call given a pointer into State",
    "State given to a safe callee",
    "a State copy of no constant length",
    "the result of a safe callee given State",
    "a pointer into State given to a bracketed call",
    "a use of %STATE other than remill's store",
    "State returned",
    "a GEP on State the layout engine cannot place",
    "a GEP on State with an index that is not a constant",
    "a GEP outside State",
    "a load from State the rewrite cannot read",
    "a load of a type the layout engine does not model",
    "a store the rewrite cannot read",
    "a State pointer stored",
    "a store of a type the layout engine does not model",
    "an access outside State",
    "a use of State the rewrite does not account for",
    "a call to a lifted function this module defines and leaves as it is",
    "a guest-memory call the ordering does not leave with exactly its effect",
)

# Memory effects, as LLVM 20 models them: one access per location, in this order, each of
# none (0), read (1), write (2) or readwrite (3). `memory(...)` names them; an attribute
# list with none of the memory attributes has the unknown effect.
LOCATIONS = ("argmem", "inaccessiblemem", "other")
ACCESSES = ("none", "read", "write", "readwrite")
UNKNOWN = (3, 3, 3)
READ = (1, 0, 0)
READ_WRITE = (3, 0, 0)
READ_EFFECT = "memory(argmem: read)"
WRITE_EFFECT = "memory(argmem: readwrite)"
# The older attributes LLVM's parser reads as a memory effect: each is intersected with the
# others, and when any is present the result replaces a `memory(...)` in the same list
# (LLParser::parseFnAttributeValuePairs, upgradeMemoryAttr).
LEGACY_MEMORY = {"readnone": (0, 0, 0), "readonly": (1, 1, 1), "writeonly": (2, 2, 2),
                 "argmemonly": (3, 0, 0), "inaccessiblememonly": (0, 3, 0),
                 "inaccessiblemem_or_argmemonly": (3, 3, 0)}
# Pointer-parameter attributes that limit what a callee does through that pointer.
PARAM_MEMORY = frozenset({"readnone", "readonly", "writeonly"})
# The attribute groups the ordering adds are numbered from here, after speed_rewrite's
# #90001.
FIRST_GROUP = 90002

_ATTRIBUTE_GROUP = re.compile(r"^attributes #(\d+) = \{(.*?)\}(\s*;[^\n]*)?$", re.M)
_ATTRIBUTE = re.compile(r'\s*("(?:[^"\\]|\\.)*"(?:="(?:[^"\\]|\\.)*")?'
                        r"|[A-Za-z_][\w.$-]*(?:\([^()]*\))?(?:=\d+)?)")
_CALL_TAIL = re.compile(r"^\)(?: #(?P<group>\d+))?(?P<comment>\s*;.*)?$")
_DECLARE = re.compile(r"^declare [^@\n]*@(__remill_(?:read|write)_memory_"
                      r"(?:8|16|32|64|f32|f64|f80))\(")
_DECLARE_TAIL = re.compile(r"^(?P<middle>(?: (?:local_)?unnamed_addr)?)(?: #(?P<group>\d+))?"
                           r"(?P<comment>\s*;.*)?$")
_GUEST_MEMORY_REF = re.compile(r"@__remill_(?:read|write)_memory_(?:8|16|32|64|f32|f64|f80)\b")

_TOKEN = re.compile(r"%[-\w.$]+")
_DEFINE = re.compile(r"^define (?P<head>(?:internal )?)ptr @(?P<name>sub_[0-9a-fA-F]+(?:\.\d+)?)"
                     r"\(ptr noalias %state, i32 %program_counter, ptr noalias %memory\)"
                     r"(?P<tail>(?: #\d+)?) \{$")
_LOCALIZED_DEFINE = re.compile(r"^define (?:internal )?ptr @(sub_[0-9a-fA-F]+(?:\.\d+)?)"
                               r"\(ptr noalias %state\.host, i32 %program_counter, "
                               r"ptr noalias %memory\)")
_TYPEDEF = re.compile(r"^(%[-\w.$]+) = type (\{.*\})$", re.M)
_CALL_HEAD = re.compile(r"^  (?:(%[-\w.$]+) = )?(?:(tail|musttail|notail) )?call ")
_CALLEE = re.compile(r"(?<![-\w.$])([@%][-\w.$]+)\(")
# The callee of a plain direct call (no parenthesis or `@` before it), for the rule's count;
# any other form goes through `parse_call`.
_CALLEE_FAST = re.compile(r"^  (?:%[-\w.$]+ = )?(?:(?:tail|notail) )?call [^@%()]*@([-\w.$]+)\(")
_ASSIGN = re.compile(r"^  (%[-\w.$]+) = (\w+) ")
_INT = re.compile(r"^i(?:8|16|32|64) (-?\d+)$")
_MEMCPY_DECLARE = re.compile(r"^declare [^\n@]*@llvm\.memcpy\.p0\.p0\.i64\(", re.M)
MEMCPY_DECLARATION = ("declare void @llvm.memcpy.p0.p0.i64(ptr noalias nocapture writeonly, "
                      "ptr noalias nocapture readonly, i64, i1 immarg)")


# ------------------------------------------------------------------------------- layout

class LayoutError(ValueError):
    """A type the layout engine does not model; the function using it is refused."""


# x86_64-linux-musl ("e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:
# 32:64-S128"): (allocation size, ABI alignment, store size) in bytes.
_SCALARS = {"i1": (1, 1, 1), "i8": (1, 1, 1), "i16": (2, 2, 2), "i32": (4, 4, 4),
            "i64": (8, 8, 8), "i128": (16, 16, 16), "float": (4, 4, 4), "double": (8, 8, 8),
            "x86_fp80": (16, 16, 10), "ptr": (8, 8, 8)}
UNIT = 16


def split_top(s: str) -> list[str]:
    """`s` split at the commas outside any bracket, each part stripped."""
    if "(" not in s and "[" not in s and "{" not in s and "<" not in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    out, depth, start = [], 0, 0
    for i, ch in enumerate(s):
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(s[start:i].strip())
            start = i + 1
    tail = s[start:].strip()
    if tail:
        out.append(tail)
    return out


def attribute_groups(text: str) -> dict[str, str]:
    """The module's `attributes #N = { ... }` lines: N -> the attributes between the braces,
    a trailing comment read past. LLVM prints them after the functions, so the scan starts
    at the first one."""
    first = 0 if text.startswith("attributes #") else text.find("\nattributes #") + 1
    if not first and not text.startswith("attributes #"):
        return {}
    return {m.group(1): m.group(2) for m in _ATTRIBUTE_GROUP.finditer(text, first)}


class Layout:
    """Sizes and field offsets of a module's types under x86_64-linux-musl, and the
    attribute groups the module defines."""

    def __init__(self, text: str):
        self.defs = {m.group(1): m.group(2) for m in _TYPEDEF.finditer(text)}
        self.group_attrs = attribute_groups(text)
        self.groups = set(self.group_attrs)
        self.size = lru_cache(maxsize=None)(self._size)
        self.fields = lru_cache(maxsize=None)(self._fields)
        self.geps: dict[tuple, tuple[int, str] | None] = {}

    def _size(self, t: str) -> tuple[int, int, int]:
        """(allocation size, alignment, store size) of type `t`."""
        t = t.strip()
        if t in _SCALARS:
            return _SCALARS[t]
        if t.startswith("%"):
            if t not in self.defs:
                raise LayoutError(f"type {t} is not defined in the module")
            return self.size(self.defs[t])
        m = re.fullmatch(r"\[(\d+) x (.+)\]", t)
        if m:
            size, align, _ = self.size(m.group(2))
            return size * int(m.group(1)), align, size * int(m.group(1))
        if t.startswith("{"):
            offsets, size, align = self.fields(t)
            return size, align, size
        raise LayoutError(f"type {t!r} is not modelled")

    def _fields(self, t: str) -> tuple[tuple[tuple[int, str], ...], int, int]:
        """A literal struct's (offset, type) per field, its size and its alignment."""
        if not (t.startswith("{") and t.endswith("}")):
            raise LayoutError(f"type {t!r} is not a struct")
        out, off, align = [], 0, 1
        for f in split_top(t[1:-1]):
            size, a, _ = self.size(f)
            off = (off + a - 1) // a * a
            out.append((off, f))
            off += size
            align = max(align, a)
        return tuple(out), (off + align - 1) // align * align, align

    def resolve(self, t: str) -> str:
        while t.startswith("%"):
            if t not in self.defs:
                raise LayoutError(f"type {t} is not defined in the module")
            t = self.defs[t]
        return t

    def gep(self, source: str, indices: list[int]) -> tuple[int, str]:
        """The byte offset and result type of a GEP with constant `indices` on `source`."""
        off = indices[0] * self.size(source)[0]
        t = source
        for i in indices[1:]:
            r = self.resolve(t)
            m = re.fullmatch(r"\[(\d+) x (.+)\]", r)
            if m:
                t = m.group(2)
                off += i * self.size(t)[0]
                continue
            fields = self.fields(r)[0]
            if not 0 <= i < len(fields):
                raise LayoutError(f"field {i} of {t}")
            o, t = fields[i]
            off += o
        return off, t

    def units(self, root: str) -> "Units":
        return Units(self, root)


class Units:
    """The largest enclosing aggregate of at most UNIT bytes around each byte of `root`."""

    def __init__(self, layout: Layout, root: str):
        self.layout, self.root = layout, root
        self.size = layout.size(root)[0]
        self._cache: dict[int, tuple[int, int]] = {}

    def at(self, offset: int) -> tuple[int, int]:
        hit = self._cache.get(offset)
        if hit is None:
            hit = self._cache[offset] = self._at(offset)
        return hit

    def _at(self, offset: int) -> tuple[int, int]:
        t, base = self.root, 0
        while self.layout.size(t)[0] > UNIT:
            r = self.layout.resolve(t)
            m = re.fullmatch(r"\[(\d+) x (.+)\]", r)
            if m:
                esize = self.layout.size(m.group(2))[0]
                base += (offset - base) // esize * esize
                t = m.group(2)
                continue
            for o, f in self.layout.fields(r)[0]:
                if base + o <= offset < base + o + self.layout.size(f)[0]:
                    base, t = base + o, f
                    break
            else:                              # padding between fields
                return offset, offset + 1
        return base, base + self.layout.size(t)[0]

    def widen(self, lo: int, hi: int) -> tuple[int, int]:
        return self.at(lo)[0], max(self.at(hi - 1)[1], hi)


def merge(ranges) -> list[tuple[int, int]]:
    """Ranges merged where they overlap or touch, in order."""
    out: list[list[int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


# ------------------------------------------------------------------------ memory effects

def _attribute_tokens(attrs: str) -> list[str] | None:
    """The attributes of one list (an attribute group's braces), or None when this module
    cannot read the list."""
    out, pos = [], 0
    while pos < len(attrs):
        m = _ATTRIBUTE.match(attrs, pos)
        if not m:
            return out if not attrs[pos:].strip() else None
        out.append(m.group(1))
        pos = m.end()
    return out


def _memory(token: str) -> tuple[int, int, int] | None:
    """The effect `memory(...)` names, or None when it names a location or an access LLVM 20
    does not have."""
    m = re.fullmatch(r"memory\(([^()]*)\)", token)
    if not m:
        return None
    default, given = None, {}
    for item in (x.strip() for x in m.group(1).split(",")):
        loc, _, access = item.rpartition(":")
        loc, access = loc.strip(), access.strip()
        if access not in ACCESSES:
            return None
        if not loc:
            if default is not None:
                return None
            default = ACCESSES.index(access)
        elif loc in LOCATIONS[:2]:
            given[LOCATIONS.index(loc)] = ACCESSES.index(access)
        else:
            return None
    effect = [default or 0] * len(LOCATIONS)
    for i, a in given.items():
        effect[i] = a
    return tuple(effect)


def effect_of(attrs: str) -> tuple[int, int, int] | None:
    """The memory effect of an attribute list (an attribute group's braces) as LLVM 20's
    parser reads it: the older memory attributes intersected, in place of any `memory(...)`
    when one is present; else the last `memory(...)`; else the unknown effect. None when
    this module cannot read the list."""
    tokens = _attribute_tokens(attrs)
    if tokens is None:
        return None
    legacy = [LEGACY_MEMORY[t] for t in tokens if t in LEGACY_MEMORY]
    if legacy:
        effect = UNKNOWN
        for e in legacy:
            effect = tuple(a & b for a, b in zip(effect, e))
        return effect
    explicit = [t for t in tokens if t.startswith("memory(")]
    return _memory(explicit[-1]) if explicit else UNKNOWN


def render(effect: tuple[int, int, int]) -> str:
    """`effect` as LLVM prints it (Attribute::getAsString): the access of `other` first as
    the default, then each location that differs; the unknown effect prints as nothing."""
    if effect == UNKNOWN:
        return ""
    other = effect[LOCATIONS.index("other")]
    parts = []
    union = 0
    for a in effect:
        union |= a
    if other or union == other:
        parts.append(ACCESSES[other])
    parts += [f"{loc}: {ACCESSES[a]}" for loc, a in zip(LOCATIONS[:2], effect) if a != other]
    return f"memory({', '.join(parts)})"


def covers(effect: tuple[int, int, int], want: tuple[int, int, int]) -> bool:
    """Whether `effect` allows every access `want` makes."""
    return all(a & w == w for a, w in zip(effect, want))


def intended(callee: str) -> tuple[int, int, int]:
    """The effect a guest-memory intrinsic is given (the module docstring, "Ordering guest
    memory"): a read reads through its token; a write, and the ten-byte read, which fills its
    `out` operand, read and write."""
    if callee.startswith("__remill_read_memory_") and callee != "__remill_read_memory_f80":
        return READ
    return READ_WRITE


def _with_effect(attrs: str, effect: tuple[int, int, int]) -> str | None:
    """`attrs` (an attribute group's braces) with every memory attribute taken out and
    `effect` put where the first one was, or at the end; None when the list cannot be read."""
    tokens = _attribute_tokens(attrs)
    if tokens is None:
        return None
    out, put = [], False
    for t in tokens:
        if t in LEGACY_MEMORY or t.startswith("memory("):
            if not put:
                out.append(render(effect))
                put = True
            continue
        out.append(t)
    if not put:
        out.append(render(effect))
    return f" {' '.join(out)} "


class _Declaration:
    """A guest-memory intrinsic's declaration: the text up to its parameters, the
    parameters, what follows them, its attribute group and a trailing comment."""

    __slots__ = ("name", "head", "params", "middle", "group", "comment")

    def __init__(self, name, head, params, middle, group, comment):
        self.name, self.head, self.params = name, head, params
        self.middle, self.group, self.comment = middle, group, comment

    def text(self, params: list[str], group: str | None) -> str:
        return (f"{self.head}{', '.join(params)}){self.middle}"
                + (f" #{group}" if group else "") + self.comment)


def _declaration(line: str) -> _Declaration | None:
    """The guest-memory declaration on `line`, or None when the line is not one this module
    can read (anything after the parameters but `unnamed_addr`, one group and a comment)."""
    m = _DECLARE.match(line)
    if not m:
        return None
    depth, start = 1, m.end()
    for j in range(start, len(line)):
        if line[j] in "([{<":
            depth += 1
        elif line[j] in ")]}>":
            depth -= 1
            if depth == 0:
                break
    else:
        return None
    t = _DECLARE_TAIL.match(line[j + 1:])
    if not t:
        return None
    return _Declaration(m.group(1), line[:start], split_top(line[start:j]), t.group("middle"),
                        t.group("group"), t.group("comment") or "")


def _pointer_memory(operand: str) -> bool:
    """Whether a `ptr` operand or parameter carries an attribute that limits access."""
    return operand.startswith("ptr") and not PARAM_MEMORY.isdisjoint(operand.split())


# ------------------------------------------------------------------------------- calls

def safe_callee(name: str) -> bool:
    """A callee that is not a bracket point. The floating-point environment's calls never
    are, whatever the tables say (the module docstring, "Soundness")."""
    if name in FENV_CALLS:
        return False
    if name in SAFE_CALLEES:
        return True
    if name.startswith("llvm."):
        return any(name == f or name.startswith(f + ".") for f in SAFE_INTRINSICS)
    return False


class Call:
    """One `call` line: its kind (`tail`, `musttail`, `notail` or None), callee (`asm` for
    inline asm), arguments as written, and the text before and after them."""

    __slots__ = ("kind", "callee", "args", "before", "after")

    def __init__(self, kind, callee, args, before, after):
        self.kind, self.callee = kind, callee
        self.args, self.before, self.after = args, before, after


def parse_call(line: str) -> Call | None:
    """The call on `line`, or None when the line is not a call this module can read."""
    m = _CALL_HEAD.match(line)
    if not m:
        return None
    rest = line[m.end():]
    if " asm " in f" {rest}" and re.match(r"^[^@%]*\basm\b", rest):
        return Call(m.group(2), "asm", "", line, "")
    depth = 0
    for i, ch in enumerate(rest):
        if ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch in "@%" and depth == 0:
            c = _CALLEE.match(rest, i)
            if not c:
                return None
            start = c.end()                  # just after the '(' of the argument list
            depth2 = 1
            for j in range(start, len(rest)):
                if rest[j] in "([{<":
                    depth2 += 1
                elif rest[j] in ")]}>":
                    depth2 -= 1
                    if depth2 == 0:
                        callee = c.group(1)[1:] if c.group(1)[0] == "@" else c.group(1)
                        return Call(m.group(2), callee, rest[start:j], line[:m.end() + start],
                                    rest[j:])
            return None
    return None


# --------------------------------------------------------------------- one function's plan

class Plan:
    """What the rewrite would do to one lifted function, or why it cannot."""

    def __init__(self, name: str):
        self.name = name
        self.refused: list[str] = []
        self.st_refs = 0
        self.reloads = 0
        self.ranges: list[tuple[int, int]] = []       # merged, unit-widened byte ranges
        self.brackets: dict[int, bool] = {}           # body line -> reload after it
        self.rets: list[int] = []                     # body lines of rets that copy back
        self.bracketed: dict[str, int] = {}           # callee -> bracketed sites


def _next_instruction(body: list[str], k: int) -> str:
    for j in range(k + 1, len(body)):
        s = body[j].strip()
        if s and not s.startswith(";"):
            return s
    return ""


def _terminal(next_instruction: str) -> bool:
    """Whether a bracket point followed by this instruction needs no copy in after it."""
    return next_instruction.startswith("ret ") or next_instruction == "unreachable"


def _st_range(layout: Layout) -> tuple[int, int]:
    """Byte range of `State.st` (remill's X87Stack), the x87 stack the rule counts."""
    x86 = layout.resolve("%struct.State")
    first = layout.fields(x86)[0][0][1]         # State = { X86State }
    for off, f in layout.fields(layout.resolve(first))[0]:
        if f == "%struct.X87Stack":
            return off, off + layout.size(f)[0]
    raise LayoutError("State has no %struct.X87Stack")


def _gep(rest: str, layout: Layout) -> tuple[str, int, str] | None:
    """`getelementptr ...` operands: base, byte offset, result type; None when an index
    is not a constant or the base is not a pointer."""
    parts = split_top(rest)
    if len(parts) < 3 or not parts[1].startswith("ptr "):
        return None
    key = (parts[0], tuple(parts[2:]))
    if key not in layout.geps:
        layout.geps[key] = _place(parts[0], parts[2:], layout)
    placed = layout.geps[key]
    return None if placed is None else (parts[1][4:], placed[0], placed[1])


def _place(source: str, indices: list[str], layout: Layout) -> tuple[int, str] | None:
    source = re.sub(r"^(?:(?:inbounds|nuw|nusw) )+", "", source)
    idx = []
    for p in indices:
        m = _INT.match(p)
        if not m:
            return None
        idx.append(int(m.group(1)))
    return layout.gep(source, idx)


def plan(name: str, body: list[str], layout: Layout, k: int = K, full: bool = False) -> Plan:
    """The plan for one lifted function's body (the lines between its define and `}`).

    The selection rule is decided first, from the x87 stack references and the bracket
    points; a function it keeps is not analysed further unless `full` (the census)."""
    p = Plan(name)
    try:
        st_lo, st_hi = _st_range(layout)
        state_size = layout.size("%struct.State")[0]
    except LayoutError as e:
        # No State type the engine can place: no x87 stack reference either, so the rule
        # keeps the function (the census still names why it could not be localized).
        if full:
            p.refused.append(f"the layout engine cannot place State ({e})")
        return p
    # Pass 1: the rule's two numbers.
    for i, line in enumerate(body):
        if "call " in line:
            fast = _CALLEE_FAST.match(line)
            callee = fast.group(1) if fast else None
            if callee is None:
                c = parse_call(line)
                callee = None if c is None else c.callee
            if callee is None or callee == "asm" or safe_callee(callee):
                continue
            if not _terminal(_next_instruction(body, i)):
                p.reloads += 1
        elif "getelementptr" in line and "ptr %state," in line:
            m = _ASSIGN.match(line)
            if not m or m.group(2) != "getelementptr":
                continue
            try:
                g = _gep(line[m.end():], layout)
            except LayoutError:
                continue
            if g and g[0] == "%state" and st_lo <= g[1] < st_hi:
                p.st_refs += 1
    if not selected(p.st_refs, p.reloads, k) and not full:
        return p
    _account(p, body, layout, state_size)
    return p


def selected(st_refs: int, reloads: int, k: int = K) -> bool:
    """The selection rule (the module docstring)."""
    return st_refs > 0 and st_refs >= k * (reloads + 1)


def _derived(body: list[str], layout: Layout, state_size: int) -> tuple[dict[str, int],
                                                                         dict[int, str]]:
    """Every pointer derived from `%state` by constant-index GEPs, with its byte offset in
    State, and the GEP lines on a derived base the layout cannot place. A GEP may name a
    value defined further down the text (blocks are not in dominance order), so this runs
    to a fixed point before any use is read."""
    geps = []
    for i, line in enumerate(body):
        if "getelementptr" in line:
            m = _ASSIGN.match(line)
            if m and m.group(2) == "getelementptr":
                parts = split_top(line[m.end():])
                if len(parts) >= 2 and parts[1].startswith("ptr "):
                    geps.append((i, m.group(1), parts[1][4:], line[m.end():]))
    derived: dict[str, int] = {"%state": 0}
    problems: dict[int, str] = {}
    changed = True
    while changed:
        changed = False
        for i, res, base, rest in geps:
            if base not in derived or res in derived or i in problems:
                continue
            try:
                g = _gep(rest, layout)
            except LayoutError as e:
                problems[i] = f"a GEP on State the layout engine cannot place ({e})"
                continue
            if g is None:
                problems[i] = "a GEP on State with an index that is not a constant"
                continue
            off = derived[base] + g[1]
            if not 0 <= off <= state_size:
                problems[i] = "a GEP outside State"
                continue
            derived[res] = off
            changed = True
    return derived, problems


def _account(p: Plan, body: list[str], layout: Layout, state_size: int) -> None:
    """Every use of State in `body`, or the reasons the function is refused."""
    text = "\n".join(body)
    if "%state.host" in text or "%sl." in text:
        p.refused.append("a name the rewrite adds is already in use")
        return
    units = layout.units("%struct.State")
    derived, gep_problems = _derived(body, layout, state_size)
    accesses: list[tuple[int, int]] = []

    def refuse(why: str, line: str) -> None:
        p.refused.append(f"{why}: {line.strip()[:160]}")

    def access(off: int, size: int, line: str) -> None:
        if off < 0 or size <= 0 or off + size > state_size:
            refuse("an access outside State", line)
        else:
            accesses.append((off, off + size))

    for i, line in enumerate(body):
        s = line.strip()
        if not s or s.startswith(";") or not line.startswith("  "):
            continue                                   # blank, comment, label
        toks = _TOKEN.findall(line)
        state = [t for t in toks if t in derived]
        if s.startswith(("invoke ", "callbr ")) or " = invoke " in s or " = callbr " in s:
            refuse("an invoke or callbr", line)
            continue
        if "call " in line and _CALL_HEAD.match(line):
            c = parse_call(line)
            if c is None:
                refuse("a call the rewrite cannot read", line)
                continue
            if c.kind == "musttail":
                refuse("a musttail call", line)
                continue
            if c.callee == "asm":
                if not re.fullmatch(re.escape(BARRIER) + r"(?: #\d+)?", s):
                    refuse("inline asm other than remill's barrier", line)
                continue
            if [t for t in _TOKEN.findall(c.before + c.after) if t in derived]:
                refuse("State outside a call's arguments", line)
                continue
            args = split_top(c.args)
            if c.callee in GUEST_MEMORY:
                tail = _CALL_TAIL.match(c.after)
                if not tail:
                    refuse("a guest-memory call with attributes, bundles or metadata after its "
                           "arguments", line)
                    continue
                if tail.group("group") and tail.group("group") not in layout.groups:
                    refuse("a guest-memory call carrying an attribute group the module does "
                           "not define", line)
                    continue
            if safe_callee(c.callee):
                given = [(n, a, ts) for n, a in enumerate(args)
                         if (ts := [t for t in _TOKEN.findall(a) if t in derived])]
                if not given:
                    continue
                if c.kind == "tail":
                    refuse("a tail call given a pointer into State", line)
                    continue
                if not all(_mem_family(c.callee) and n < 2 and a.startswith("ptr ")
                           and len(_TOKEN.findall(a)) == 1 and a.split()[-1] == ts[0]
                           for n, a, ts in given):
                    refuse("State given to a safe callee", line)
                    continue
                length = _mem_length(args)
                if length is None:
                    refuse("a State copy of no constant length", line)
                    continue
                if _CALL_HEAD.match(line).group(1):
                    # memcpy, memmove and memset return their destination: a State pointer
                    # the accounting would not follow.
                    refuse("the result of a safe callee given State", line)
                    continue
                for n, a, ts in given:
                    access(derived[ts[0]], length, line)
                continue
            # A bracket point: it may receive %state itself, which becomes %state.host.
            for a in args:
                ts = [t for t in _TOKEN.findall(a) if t in derived]
                if ts and (ts != ["%state"] or a.split()[-1] != "%state"):
                    refuse("a pointer into State given to a bracketed call", line)
                    break
            else:
                p.brackets[i] = not _terminal(_next_instruction(body, i))
                p.bracketed[c.callee] = p.bracketed.get(c.callee, 0) + 1
            continue
        if "%STATE" in toks:
            if not re.fullmatch(r"(?:%STATE = alloca ptr(?:, align \d+)?"
                                r"|store ptr %state, ptr %STATE(?:, align \d+)?)", s):
                refuse("a use of %STATE other than remill's store", line)
            continue
        if s.startswith("ret "):
            if state:
                refuse("State returned", line)
                continue
            if not _after_terminal_bracket(body, i, p.brackets):
                p.rets.append(i)
            continue
        if not state:
            continue
        m = _ASSIGN.match(line)
        op = m.group(2) if m else s.split()[0]
        if op == "getelementptr":
            if m.group(1) not in derived or len([t for t in state if t != m.group(1)]) != 1:
                refuse(gep_problems.get(i, "a use of State the rewrite does not account for"),
                       line)
            continue
        if op == "load":
            parts = split_top(line[m.end():])
            if (len(parts) < 2 or parts[0].startswith(("atomic", "volatile"))
                    or len(state) != 1 or parts[1] != f"ptr {state[0]}"):
                refuse("a load from State the rewrite cannot read", line)
                continue
            try:
                access(derived[state[0]], layout.size(parts[0])[2], line)
            except LayoutError as e:
                refuse(f"a load of a type the layout engine does not model ({e})", line)
            continue
        if s.startswith("store "):
            parts = split_top(s[len("store "):])
            if (len(parts) < 2 or parts[0].startswith(("atomic", "volatile"))
                    or not parts[1].startswith("ptr ") or len(parts[1].split()) != 2):
                refuse("a store the rewrite cannot read", line)
                continue
            ptr = parts[1].split()[1]
            if [t for t in _TOKEN.findall(parts[0]) if t in derived]:
                refuse("a State pointer stored", line)
                continue
            if ptr not in derived:
                refuse("a store the rewrite cannot read", line)
                continue
            try:
                access(derived[ptr], layout.size(parts[0].rsplit(" ", 1)[0])[2], line)
            except LayoutError as e:
                refuse(f"a store of a type the layout engine does not model ({e})", line)
            continue
        refuse("a use of State the rewrite does not account for", line)
    if not p.refused:
        p.ranges = merge(units.widen(lo, hi) for lo, hi in accesses)


def _mem_family(callee: str) -> bool:
    """A safe callee that copies or sets memory: the C library's, or LLVM's intrinsic."""
    return callee in LIBC or any(callee == f or callee.startswith(f + ".") for f in MEM_INTRINSICS)


def _mem_length(args: list[str]) -> int | None:
    """The constant length a mem* call copies or sets, or None."""
    if len(args) < 3:
        return None
    m = re.fullmatch(r"i(?:32|64)(?: (?:noundef|immarg))* (\d+)", args[2])
    return int(m.group(1)) if m else None


def _after_terminal_bracket(body: list[str], k: int, brackets: dict[int, bool]) -> bool:
    """Whether the instruction before `ret` on line k is a bracket point with no reload."""
    for j in range(k - 1, -1, -1):
        s = body[j].strip()
        if not s or s.startswith(";"):
            continue
        return j in brackets and not brackets[j]
    return False


# ------------------------------------------------------------------------------ rewrite

class _Ordering:
    """The attribute groups that order the guest-memory intrinsics in one module: each is a
    copy of a group the module has with every memory attribute taken out and the
    intrinsic's effect put in (`_with_effect`), numbered from FIRST_GROUP in the order first
    needed."""

    def __init__(self, groups: dict[str, str]):
        self.groups = dict(groups)
        self.made: dict[tuple[str, tuple], str | None] = {}
        self.order: list[str] = []
        self.next = FIRST_GROUP

    def group(self, number: str, effect: tuple[int, int, int]) -> str | None:
        if (number, effect) not in self.made:
            new = _with_effect(self.groups[number], effect)
            name = None
            if new is not None:
                while str(self.next) in self.groups:
                    self.next += 1
                name = str(self.next)
                self.groups[name] = new
                self.order.append(f"attributes #{name} = {{{new}}}")
            self.made[(number, effect)] = name
        return self.made[(number, effect)]

    def call(self, c: Call) -> str:
        """A guest-memory call in a localized function with its intrinsic's effect, the
        comment after it kept. A call carrying no group, or one whose group cannot be read,
        is left as it is: `ordering_problems` then decides."""
        line = f"{c.before}{c.args}{c.after}"
        t = _CALL_TAIL.match(c.after)
        if not t or not t.group("group") or t.group("group") not in self.groups:
            return line
        new = self.group(t.group("group"), intended(c.callee))
        return line if new is None else f"{c.before}{c.args}) #{new}{t.group('comment') or ''}"

    def declare(self, line: str) -> str:
        """A guest-memory declaration with no memory attribute on a pointer parameter, and
        pointed at a group with its intrinsic's effect unless its own group allows it."""
        d = _declaration(line)
        if d is None:
            return line
        params = [" ".join(w for w in p.split() if w not in PARAM_MEMORY)
                  if p.startswith("ptr") else p for p in d.params]
        group = d.group
        if group is not None and group in self.groups:
            effect = effect_of(self.groups[group])
            if effect is None or not covers(effect, intended(d.name)):
                group = self.group(group, intended(d.name)) or group
        return d.text(params, group)


def _copies(site: int, ranges: list[tuple[int, int]], to_host: bool) -> list[str]:
    out = []
    for r, (lo, hi) in enumerate(ranges):
        local = (f"%sl.{site}.{r}.l", f"getelementptr inbounds i8, ptr %state, i64 {lo}")
        host = (f"%sl.{site}.{r}.h", f"getelementptr inbounds i8, ptr %state.host, i64 {lo}")
        dst, src = (host, local) if to_host else (local, host)
        out += [f"  {dst[0]} = {dst[1]}", f"  {src[0]} = {src[1]}",
                f"  call void @llvm.memcpy.p0.p0.i64(ptr align 1 {dst[0]}, ptr align 1 {src[0]}, "
                f"i64 {hi - lo}, i1 false)"]
    return out


def _emit(define: re.Match, body: list[str], p: Plan, ordering: _Ordering) -> list[str]:
    out = [f"define {define.group('head')}ptr @{define.group('name')}(ptr noalias %state.host, "
           f"i32 %program_counter, ptr noalias %memory){define.group('tail')} {{"]
    k = 0
    if body and not body[0].startswith("  ") and body[0].strip():
        out.append(body[0])                          # the entry block's label
        k = 1
    out.append("  %state = alloca %struct.State, align 16")
    out += _copies(0, p.ranges, to_host=False)
    site = 0
    rets = set(p.rets)
    for i in range(k, len(body)):
        line = body[i]
        if i in rets:
            site += 1
            out += _copies(site, p.ranges, to_host=True)
            out.append(line)
            continue
        if i in p.brackets:
            site += 1
            out += _copies(site, p.ranges, to_host=True)
            c = parse_call(line)
            args = re.sub(r"%state(?![-\w.$])", "%state.host", c.args)
            out.append(f"{c.before}{args}{c.after}")
            if p.brackets[i]:
                site += 1
                out += _copies(site, p.ranges, to_host=False)
            continue
        if "@__remill_" in line and _CALL_HEAD.match(line):
            c = parse_call(line)
            if c is not None and c.callee in GUEST_MEMORY:
                line = ordering.call(c)
        out.append(line)
    out.append("}")
    return out


def _lifted_functions(lines: list[str]):
    """(first line, closing line, define match or None, name) of every lifted function:
    each define with remill's signature, and each other define naming `%state` the rewrite
    has not made (a signature it does not know)."""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("define "):
            m = _DEFINE.match(line)
            if m or ("%state" in line and "%state.host" not in line):
                j = i + 1
                while j < len(lines) and lines[j] != "}":
                    j += 1
                if m:
                    name = m.group("name")
                else:
                    n = re.search(r"@([-\w.$]+)\(", line)
                    name = n.group(1) if n else f"line {i + 1}"
                yield i, j, m, name
                i = j + 1
                continue
        i += 1


def _plan_all(lines: list[str], functions: list, layout: Layout | None, k: int,
              full: bool) -> list[Plan]:
    plans = []
    for i, j, m, name in functions:
        if m is None:
            p = Plan(name)
            p.refused.append(f"a lifted function's define line the rewrite does not know: "
                             f"{lines[i][:160]}")
        else:
            try:
                p = plan(name, lines[i + 1:j], layout, k, full=full)
            except LayoutError as e:
                p = Plan(name)
                p.refused.append(f"the layout engine cannot place State ({e})")
        plans.append(p)
    return plans


def _localize_once(text: str, k: int, forced: dict[str, str]):
    """One pass over the module with the refusals in `forced` added: the text, the counts,
    the plans, and the names of the functions it localized."""
    counts = {"local": 0, "local_kept": 0, "local_refused": 0}
    lines = text.split("\n")
    functions = list(_lifted_functions(lines))
    if not functions:
        return text, counts, [], set()
    layout = Layout(text) if any(m for _, _, m, _ in functions) else None
    plans = _plan_all(lines, functions, layout, k, full=False)
    for p in plans:
        if p.name in forced:
            p.refused.append(forced[p.name])
    local = {p.name for p in plans if not p.refused and selected(p.st_refs, p.reloads, k)
             and p.ranges}
    # A localized function calling a lifted function this module defines and leaves as it
    # is: LLVM may inline the callee, and its guest-memory calls are not ordered.
    defined = {name for _, _, _, name in functions}
    changed = True
    while changed:
        changed = False
        for p in plans:
            if p.name in local:
                left = sorted(c for c in p.bracketed if c in defined and c not in local)
                if left:
                    p.refused.append("a call to a lifted function this module defines and "
                                     f"leaves as it is: {left[0]}")
                    local.discard(p.name)
                    changed = True
    for p in plans:
        counts["local" if p.name in local else "local_refused" if p.refused else
               "local_kept"] += 1
    if not local:
        return text, counts, plans, local
    ordering = _Ordering(layout.group_attrs)
    out: list[str] = []
    start = {i: (j, m, p) for (i, j, m, _), p in zip(functions, plans)}
    i = 0
    while i < len(lines):
        if i not in start:
            out.append(lines[i])
            i += 1
            continue
        j, m, p = start[i]
        if p.name in local:
            out += _emit(m, lines[i + 1:j], p, ordering)
        else:
            out += lines[i:j + 1]
        i = j + 1
    out = [ordering.declare(line) if line.startswith("declare ") and _DECLARE.match(line)
           else line for line in out]
    text = "\n".join(out)
    if not _MEMCPY_DECLARE.search(text):
        text += f"\n{MEMCPY_DECLARATION}\n"
    if ordering.order:
        text += "\n" + "\n".join(ordering.order) + "\n"
    return text, counts, plans, local


def _localize(text: str, k: int):
    """The module localized, with every function whose guest-memory calls do not come out
    with exactly their effect refused and the module made again without it."""
    forced: dict[str, str] = {}
    while True:
        out, counts, plans, local = _localize_once(text, k, forced)
        if not local:
            return out, counts, plans
        new = {name: why for name, why in ordering_problems(out).items()
               if name in local and name not in forced}
        if not new:
            return out, counts, plans
        for name, why in new.items():
            forced[name] = ("a guest-memory call the ordering does not leave with exactly its "
                            f"effect: {why[0]}")


def localize(text: str, k: int = K) -> tuple[str, dict[str, int]]:
    """Every lifted function in a folded module localized, kept or refused (the module
    docstring): the text, and how many functions went each way."""
    if "%state" not in text:
        return text, {"local": 0, "local_kept": 0, "local_refused": 0}
    out, counts, _ = _localize(text, k)
    return out, counts


def refusals(text: str, k: int = K) -> dict[str, list[str]]:
    """Each function `localize` refuses in `text`, with its reasons."""
    if "%state" not in text:
        return {}
    return {p.name: p.refused for p in _localize(text, k)[2] if p.refused}


def plans(text: str, k: int = K, full: bool = True) -> list[Plan]:
    """Each lifted function's plan, analysed in full whatever the rule decides: the census.
    The refusals that follow from what else the module localizes (a callee left as it is,
    the ordering) are `refusals`'."""
    lines = text.split("\n")
    functions = list(_lifted_functions(lines))
    layout = Layout(text) if any(m for _, _, m, _ in functions) else None
    return _plan_all(lines, functions, layout, k, full=full)


def ordering_problems(text: str) -> dict[str, list[str]]:
    """Every guest-memory call in each localized function of `text` (a module as `localize`
    returns it) whose effect -- its call site's attribute group and its declaration's
    together, intersected as LLVM does -- is not exactly its intrinsic's (`intended`), or
    that carries a memory attribute on a pointer operand or parameter; and every other use
    of a guest-memory intrinsic there: {function: [why]}, empty when there is none."""
    lines = text.split("\n")
    groups = attribute_groups(text)
    decls: dict[str, _Declaration | None] = {}
    for line in lines:
        if line.startswith("declare ") and "@__remill_" in line:
            m = _DECLARE.match(line)
            if m:
                decls[m.group(1)] = _declaration(line)
    out: dict[str, list[str]] = {}
    current = None
    for line in lines:
        if line.startswith("define "):
            m = _LOCALIZED_DEFINE.match(line)
            current = m.group(1) if m else None
            continue
        if current is None:
            continue
        if line == "}":
            current = None
            continue
        if "@__remill_" not in line or line.lstrip().startswith(";") \
                or not _GUEST_MEMORY_REF.search(line):
            continue
        c = parse_call(line) if _CALL_HEAD.match(line) else None
        if c is None or c.callee not in GUEST_MEMORY:
            why = "a guest-memory intrinsic used other than as the callee of a call"
        else:
            why = _call_problem(c, groups, decls)
        if why:
            out.setdefault(current, []).append(f"{why}: {line.strip()[:160]}")
    return out


def _call_problem(c: Call, groups: dict[str, str],
                  decls: dict[str, _Declaration | None]) -> str | None:
    want = intended(c.callee)
    t = _CALL_TAIL.match(c.after)
    if not t:
        return "attributes, a bundle or metadata after the arguments"
    site = UNKNOWN
    if t.group("group"):
        if t.group("group") not in groups:
            return f"its attribute group #{t.group('group')} is not defined"
        site = effect_of(groups[t.group("group")])
        if site is None:
            return f"its attribute group #{t.group('group')} cannot be read"
    if c.callee not in decls:
        return "no declaration of its intrinsic"
    d = decls[c.callee]
    if d is None:
        return "its intrinsic's declaration cannot be read"
    declared = UNKNOWN
    if d.group:
        if d.group not in groups:
            return f"its declaration's attribute group #{d.group} is not defined"
        declared = effect_of(groups[d.group])
        if declared is None:
            return f"its declaration's attribute group #{d.group} cannot be read"
    effect = tuple(a & b for a, b in zip(site, declared))
    if effect != want:
        return (f"its effect is {render(effect) or 'unknown'} where its intrinsic's is "
                f"{render(want)}")
    if any(_pointer_memory(a) for a in split_top(c.args)):
        return "a memory attribute on a pointer operand"
    if any(_pointer_memory(p) for p in d.params):
        return "a memory attribute on a pointer parameter of its declaration"
    return None


# ------------------------------------------------------------ the guest-memory fast path

# The guest range the runtime accepts (runtime/guest_memory.h `sa2_gp`): an access of n bytes
# at address a is valid when GUEST_NULL_GUARD <= a <= GUEST_END - n. test_state_local.py holds
# both to guest_memory.h's SA2_GUEST_NULL_GUARD and SA2_GUEST_END.
GUEST_NULL_GUARD = 0x00001000
GUEST_END = 0x20000000

# The runtime's side of the fast path (runtime/guest_memory.h, remill_runtime.cpp).
FAST_BASE = "sa2_guest_fast_base"
SLOW_HALVES = {"read": "sa2_guest_read_slow", "write": "sa2_guest_write_slow"}

# Each intrinsic the fast path replaces: read or write, its IR type, and its width in bytes.
FAST_ACCESS = {
    "__remill_read_memory_8": ("read", "i8", 1),
    "__remill_read_memory_16": ("read", "i16", 2),
    "__remill_read_memory_32": ("read", "i32", 4),
    "__remill_read_memory_64": ("read", "i64", 8),
    "__remill_read_memory_f32": ("read", "float", 4),
    "__remill_read_memory_f64": ("read", "double", 8),
    "__remill_write_memory_8": ("write", "i8", 1),
    "__remill_write_memory_16": ("write", "i16", 2),
    "__remill_write_memory_32": ("write", "i32", 4),
    "__remill_write_memory_64": ("write", "i64", 8),
    "__remill_write_memory_f32": ("write", "float", 4),
    "__remill_write_memory_f64": ("write", "double", 8),
}

# Why a localized function keeps its intrinsic calls (the module docstring, "The guest-memory
# fast path"): every reason `fast` gives starts with one of these.
FAST_KEPT = (
    "a ten-byte guest access",
    "a guest-memory call the fast path cannot read",
    "a guest-memory intrinsic used other than as the callee of a call",
    "no State alloca where the local pass puts it",
    "a name the fast path adds is already in use",
    "a lifted function of this module keeps its intrinsic calls",
    "a guest-memory or I/O-port intrinsic the fast path does not replace",
    "the fast path's own check named a problem in this module's output",
)

# The line that loads the base, after the State alloca the local pass puts first.
_ALLOCA = "  %state = alloca %struct.State, align 16"
_BASE_LOAD = f"  %sl.base = load ptr, ptr @{FAST_BASE}, align 8"
_FAST_NAMES = re.compile(rf"@(?:__sa2_fast_\w+|{FAST_BASE}|{'|'.join(SLOW_HALVES.values())})\b")
_FAST_OPERAND = re.compile(r"^(ptr|i8|i16|i32|i64|float|double)(?: (?:noundef|zeroext|signext))* "
                           r"(%[-\w.$]+|-?\d+|0x[0-9A-Fa-f]+|null)$")
_FAST_CALL = re.compile(r"^  %[-\w.$]+ = call [^@%]*@(__sa2_fast_\w+)\((.*)\)(?:\s*;.*)?$")
# Every remill guest-memory and I/O-port intrinsic, the ones the fast path does not replace
# (the sixteen-byte forms, the port accesses) included: a fast-path function holds none.
_MEMORY_OR_PORT_REF = re.compile(r"@(__remill_(?:read|write)_(?:memory|io_port)_\w+)")
_EXPECT_DECLARE = re.compile(r"^declare [^\n@]*@llvm\.expect\.i1\(", re.M)


def _helper_name(callee: str) -> str:
    kind, _, _ = FAST_ACCESS[callee]
    return f"__sa2_fast_{kind}_{callee.rsplit('_', 1)[1]}"


def _fast_helper(callee: str) -> str:
    """The helper that replaces one intrinsic: the bounds `sa2_gp` applies, against a base
    that is NULL whenever the runtime wants every access (`GUEST_NULL_GUARD` <= a <=
    `GUEST_END` - n is `a - GUEST_NULL_GUARD < GUEST_END - GUEST_NULL_GUARD - n + 1`, one
    unsigned compare, since an address below the guard wraps above the bound), then the
    access through the pointer the fast or the slow half gives."""
    kind, ty, size = FAST_ACCESS[callee]
    name, bound = _helper_name(callee), GUEST_END - GUEST_NULL_GUARD - size + 1
    if kind == "read":
        head = f"define internal {ty} @{name}(ptr %base, i32 %addr) alwaysinline {{"
        tail = [f"  %v = load {ty}, ptr %p, align 1", f"  ret {ty} %v"]
    else:
        head = (f"define internal ptr @{name}(ptr %base, ptr %mem, i32 %addr, {ty} %val) "
                f"alwaysinline {{")
        tail = [f"  store {ty} %val, ptr %p, align 1", "  ret ptr %mem"]
    return "\n".join([
        head,
        "entry:",
        f"  %off = add i32 %addr, -{GUEST_NULL_GUARD}",
        f"  %inside = icmp ult i32 %off, {bound}",
        "  %mapped = icmp ne ptr %base, null",
        "  %ok = and i1 %mapped, %inside",
        "  %likely = call i1 @llvm.expect.i1(i1 %ok, i1 true)",
        "  br i1 %likely, label %fast, label %slow",
        "",
        "fast:",
        "  %index = zext i32 %addr to i64",
        "  %at = getelementptr inbounds i8, ptr %base, i64 %index",
        "  br label %access",
        "",
        "slow:",
        f"  %checked = call ptr @{SLOW_HALVES[kind]}(i32 %addr, i32 {size})",
        "  br label %access",
        "",
        "access:",
        "  %p = phi ptr [ %at, %fast ], [ %checked, %slow ]",
        *tail,
        "}",
    ])


def _fast_call(line: str, c: Call) -> str | None:
    """A guest-memory call as a call to its helper, or None when the call is not in the form
    the fast path reads: assigned, plain operands of the intrinsic's types, and nothing
    after the arguments but one attribute group and a comment (which is kept)."""
    kind, ty, _ = FAST_ACCESS[c.callee]
    head = _CALL_HEAD.match(line)
    tail = _CALL_TAIL.match(c.after)
    if not head or not head.group(1) or head.group(2) == "musttail" or not tail:
        return None
    args = split_top(c.args)
    want = ["ptr", "i32"] if kind == "read" else ["ptr", "i32", ty]
    if len(args) != len(want):
        return None
    values = []
    for arg, t in zip(args, want):
        m = _FAST_OPERAND.match(arg)
        if not m or m.group(1) != t:
            return None
        values.append(m.group(2))
    if kind == "read":
        call = f"call {ty} @{_helper_name(c.callee)}(ptr %sl.base, i32 {values[1]})"
    else:
        call = (f"call ptr @{_helper_name(c.callee)}(ptr %sl.base, ptr {values[0]}, "
                f"i32 {values[1]}, {ty} {values[2]})")
    return f"  {head.group(1)} = {call}{tail.group('comment') or ''}"


def _fast_body(body: list[str]) -> tuple[list[str], set[str], str | None]:
    """A localized function's body with its guest accesses through the fast path: the new
    body, the intrinsics it replaced, and None; or the body as it was, an empty set and why."""
    if _ALLOCA not in body:
        return body, set(), FAST_KEPT[3]
    out, used = [], set()
    for line in body:
        if "@__remill_" in line and any(n not in GUEST_MEMORY
                                        for n in _MEMORY_OR_PORT_REF.findall(line)):
            return body, set(), f"{FAST_KEPT[6]}: {line.strip()[:160]}"
        if "@__remill_" in line and _GUEST_MEMORY_REF.search(line):
            c = parse_call(line) if _CALL_HEAD.match(line) else None
            if c is None or c.callee not in GUEST_MEMORY:
                return body, set(), f"{FAST_KEPT[2]}: {line.strip()[:160]}"
            if c.callee not in FAST_ACCESS:
                return body, set(), f"{FAST_KEPT[0]}: {line.strip()[:160]}"
            new = _fast_call(line, c)
            if new is None:
                return body, set(), f"{FAST_KEPT[1]}: {line.strip()[:160]}"
            out.append(new)
            used.add(c.callee)
            continue
        out.append(line)
    if used:
        at = out.index(_ALLOCA) + 1
        out[at:at] = [_BASE_LOAD]
    return out, used, None


def _localized_functions(lines: list[str]):
    """(define line, closing line, name) of every function the local pass made."""
    i = 0
    while i < len(lines):
        m = _LOCALIZED_DEFINE.match(lines[i]) if lines[i].startswith("define ") else None
        if m:
            j = i + 1
            while j < len(lines) and lines[j] != "}":
                j += 1
            yield i, j, m.group(1)
            i = j + 1
            continue
        i += 1


def fast(text: str) -> tuple[str, dict[str, int]]:
    """Every localized function's guest accesses through the fast path (the module
    docstring), or none of a module's: the text, and how many functions went each way
    (`fast`, and `fast_kept` for those left with their intrinsic calls)."""
    counts = {"fast": 0, "fast_kept": 0}
    if "%state.host" not in text:
        return text, counts
    out, counts, _ = _fast(text)
    return out, counts


def fast_reasons(text: str) -> dict[str, str]:
    """Each localized function `fast` leaves with its intrinsic calls, with the reason."""
    if "%state.host" not in text:
        return {}
    return _fast(text)[2]


def _fast(text: str) -> tuple[str, dict[str, int], dict[str, str]]:
    counts = {"fast": 0, "fast_kept": 0}
    lines = text.split("\n")
    rewritten: dict[int, tuple[int, list[str]]] = {}
    used: set[str] = set()
    kept: dict[str, str] = {}
    for i, j, name in _localized_functions(lines):
        body = lines[i + 1:j]
        if _BASE_LOAD in body:
            continue                                   # already through the fast path
        new, calls, why = _fast_body(body)
        if why:
            kept[name] = why
        elif calls:
            rewritten[i] = (j, new)
            used |= calls
    if not rewritten:
        counts["fast_kept"] = len(kept)
        return text, counts, kept
    # All or none in a module: no function whose guest accesses are calls may meet, through
    # -O2's inliner, one whose accesses are loads and stores (the module docstring).
    others = [name for _, _, m, name in _lifted_functions(lines)]
    why = (f"{FAST_KEPT[5]}: {(sorted(kept) or others)[0]}" if kept or others else
           FAST_KEPT[4] if _FAST_NAMES.search(text) else None)
    if why:
        for i, (j, _) in rewritten.items():
            kept[_LOCALIZED_DEFINE.match(lines[i]).group(1)] = why
        counts["fast_kept"] = len(kept)
        return text, counts, kept
    out: list[str] = []
    i = 0
    while i < len(lines):
        if i in rewritten:
            j, new = rewritten[i]
            out += [lines[i]] + new + ["}"]
            i = j + 1
            continue
        out.append(lines[i])
        i += 1
    tail = [_fast_helper(callee) for callee in FAST_ACCESS if callee in used]
    tail += [f"declare dso_local ptr @{SLOW_HALVES[kind]}(i32, i32) cold nounwind"
             for kind in ("read", "write")
             if any(FAST_ACCESS[callee][0] == kind for callee in used)]
    tail.append(f"@{FAST_BASE} = external dso_local global ptr, align 8")
    result = "\n".join(out)
    if not _EXPECT_DECLARE.search(result):
        tail.append("declare i1 @llvm.expect.i1(i1, i1)")
    result += "\n" + "\n\n".join(tail) + "\n"
    problems = fast_problems(result)
    if problems:
        # Not an output the check can vouch for: every function the pass rewrote goes back
        # as it came, and each is counted (`kept` is empty here: a kept function stops the
        # module above).
        first = sorted(problems)[0]
        for i in rewritten:
            kept[_LOCALIZED_DEFINE.match(lines[i]).group(1)] = \
                f"{FAST_KEPT[7]}: {first}: {problems[first][0]}"
        counts["fast_kept"] = len(kept)
        return text, counts, kept
    counts["fast"], counts["fast_kept"] = len(rewritten), len(kept)
    return result, counts, kept


def fast_problems(text: str) -> dict[str, list[str]]:
    """Every fast-path function of `text` (one that loads `%sl.base`) that still refers to
    a guest-memory or I/O-port intrinsic, calls a helper with anything but `%sl.base` first
    or a helper the module does not define, or does not load the base exactly once, right
    after the State alloca: {function: [why]}, empty when there is none."""
    lines = text.split("\n")
    defined = set(re.findall(r"^define internal [^\n@]*@(__sa2_fast_\w+)\(", text, re.M))
    out: dict[str, list[str]] = {}
    for i, j, name in _localized_functions(lines):
        body = lines[i + 1:j]
        if _BASE_LOAD not in body:
            continue
        why = []
        if body.count(_BASE_LOAD) != 1 or _ALLOCA not in body \
                or body.index(_BASE_LOAD) != body.index(_ALLOCA) + 1:
            why.append("the base is not loaded once, right after the State alloca")
        for line in body:
            if _MEMORY_OR_PORT_REF.search(line):
                why.append(f"a guest-memory or I/O-port intrinsic left: {line.strip()[:160]}")
            if "@__sa2_fast_" in line:
                m = _FAST_CALL.match(line)
                if not m or not m.group(2).startswith("ptr %sl.base, ") \
                        or m.group(1) not in defined:
                    why.append(f"a helper call the check cannot read: {line.strip()[:160]}")
        if why:
            out[name] = why
    return out


# ------------------------------------------------------------------- tied to the runtime

_RUNTIME_DEFINE = re.compile(r"^define [^\n]*?@([\w.$]+)\(([^\n]*?)\)[^\n()]*\{\n(.*?)^\}$",
                             re.M | re.S)
_GLOBAL_REF = re.compile(r"@([-\w.$]+)")
_INLINE_ASM = re.compile(r"\b(?:call|invoke|callbr)\b[^@%\n]*\basm\b")
_INDIRECT_CALL = re.compile(r"\b(?:call|invoke|callbr) [^@%\n]*%[-\w.$]+\(")


def _bodies(ir: str) -> dict[str, str]:
    return {m.group(1): m.group(3) for m in _RUNTIME_DEFINE.finditer(ir)}


def runtime_problems(runtime_ir: str) -> list[str]:
    """Each safe callee the runtime (`runtime_ir`, remill_runtime.cpp compiled to LLVM IR)
    defines, and each slow half of the fast path (`SLOW_HALVES`, which a fast-path function
    calls unbracketed), whose definition or anything it reaches inside the runtime refers to
    a symbol outside RUNTIME_REFERENCES or holds inline asm or an indirect call: [] when none
    does. A `__remill_*` safe callee or a slow half the runtime does not define is a problem
    too, since nothing then says what it does."""
    bodies = _bodies(runtime_ir)
    problems = []
    for name in sorted(SAFE_CALLEES) + sorted(SLOW_HALVES.values()):
        if name in HELPERS or name in LIBC:
            continue
        if name not in bodies:
            problems.append(f"{name}: the runtime does not define it")
            continue
        seen, todo, bad = {name}, [name], []
        while todo:
            here = todo.pop()
            body = bodies[here]
            if _INLINE_ASM.search(body):
                bad.append(f"inline asm in {here}")
            if _INDIRECT_CALL.search(body):
                bad.append(f"an indirect call in {here}")
            for ref in _GLOBAL_REF.findall(body):
                if ref in seen:
                    continue
                seen.add(ref)
                if ref.startswith(("llvm.", ".str")) or ref in RUNTIME_REFERENCES:
                    continue
                if ref in bodies:
                    todo.append(ref)
                    continue
                bad.append(ref)
        if bad:
            problems.append(f"{name}: localization treats it as touching no State, and its "
                            f"runtime definition reaches {sorted(bad)}")
    return problems


def helper_problems(helper_ir: str) -> list[str]:
    """Each spliced helper defined in `helper_ir` (pc_round.ll or x87_int.ll) that is not
    pure: a pointer parameter, a load, a store, an alloca, a call to anything but another
    helper or an LLVM intrinsic, or a reference to a global. [] when every helper the text
    defines is pure; a text defining none is a problem."""
    problems, found = [], 0
    for m in _RUNTIME_DEFINE.finditer(helper_ir):
        name = m.group(1)
        if name not in HELPERS:
            continue
        found += 1
        why = []
        if re.search(r"\bptr\b", m.group(2)):
            why.append("a pointer parameter")
        for line in m.group(3).splitlines():
            s = line.strip()
            if re.match(r"(?:%[-\w.$]+ = )?(?:load|store|alloca|atomicrmw|cmpxchg)\b", s):
                why.append(s[:80])
            for ref in _GLOBAL_REF.findall(s):
                if ref not in HELPERS and not ref.startswith("llvm."):
                    why.append(f"@{ref}")
            if re.match(r"(?:%[-\w.$]+ = )?(?:(?:tail|musttail|notail) )?(?:call|invoke) ", s) \
                    and not re.search(r"\bcall [^@]*@(?:llvm\.|" + "|".join(HELPERS) + r")\b", s):
                why.append(s[:80])
        if why:
            problems.append(f"{name}: localization treats it as pure, and it has {why[:3]}")
    if not found:
        problems.append("the text defines none of the spliced helpers")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Localize State in one folded module.")
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--k", type=int, default=K, help=f"the selection rule's K (default {K})")
    ap.add_argument("--fast", action="store_true",
                    help="then the guest-memory fast path (the fifth speed pass)")
    args = ap.parse_args()
    text, counts = localize(args.src.read_text(errors="replace"), args.k)
    if args.fast:
        text, fasted = fast(text)
        counts.update(fasted)
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    args.dst.write_text(text)
    print(json.dumps(counts))


if __name__ == "__main__":
    main()
