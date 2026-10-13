#!/usr/bin/env python3
"""Hold a compiled recorder object to what its code can reach by name and what its
instructions can touch.

Subtitle: an ELF64 reader (section headers, the symbol table, the relocations, the
executable sections) and iced-x86 over the code; no host binutils. The object's
undefined symbols must be exactly ALLOWED_UNDEFINED, its defined global symbols
exactly DEFINED (the __wrap_ of each of WRAPS). Every executable section, whatever
its type, is read, and one that is not PROGBITS is refused. Its code must hold no
x87, MMX or 3DNow! instruction, no instruction that names an MMX or x87 register,
no SSE/AVX floating-point arithmetic or conversion, no SSE/AVX instruction outside
SIMD_ALLOWED, no instruction that loads or saves the FP state or MXCSR, no system
call or software interrupt, no call or jump that leaves for a function (an indirect
jump included) reached with a vector-register count in AL, and no branch target,
symbol or relocation target inside an instruction of the linear sweep. The strings
of every allocated section, whatever its type, must hold no floating-point printf
conversion. SOURCE_RULES adds what only the source shows: no inline asm.

What it proves, and what it does not
------------------------------------
host/sa2_present_rec.c claims it only reads the guest, never changes an answer and
leaves the FPU as it found it. The presentation run's parity checks see only what
the registered run records (the .dataa words, the tick capture, the reach, the run
record), so a write the boot never reads back passes them (the parity review's poke
run). This check reads the object itself, and proves three things of it:

  * By name, it reaches only ALLOWED_UNDEFINED: host libc for its own file, the
    runtime's read-only getters, and the thirteen __real_ functions it wraps. So it
    calls no runtime writer (sa2_poke, sa2_guest_fill, the guest heap, obj_*) and
    takes no address of `sa2_guest`, which is what a write through runtime/
    guest_memory.h's inline sa2_gp leaves behind. It defines only the thirteen
    __wrap_ functions, so it replaces no other definition in the link, weak or
    strong.
  * Its own instructions do no floating point and leave the x87 unit, the MMX
    registers and MXCSR alone (the lifted x87 code computes in the host's long
    double, so any of those would move the guest's arithmetic), make no system call
    of their own, and hand libc's formatter no double and no float conversion, the
    two ways a printf reaches musl's x87 code (fmt_fp) on the object's behalf.
    The SSE instructions allowed are SIMD_ALLOWED's moves, shuffles, bitwise and
    integer operations through xmm registers: none computes floating point or
    touches MXCSR. Any other SSE, AVX, FMA, F16C, XOP or AMX instruction is refused,
    so a floating-point operation FP_ARITH does not name (AVX-512's VSCALEFSD) is
    refused as well.
  * Every executable section is PROGBITS and decodes as instructions end to end,
    and every place the object names as one control reaches -- a direct branch's
    target, a symbol, a relocation's target -- starts an instruction of that
    decode. A section of another type (a function placed in ".note.rec" by a
    section attribute is SHT_NOTE, and the linker maps it executable beside .text)
    is refused and read all the same.

It cannot see, and nothing here claims:

  * stores through a pointer the object is handed: rec_thunk gets the guest's
    register file (the CPU state) and could write it. The run-time checks catch
    such a store only where the guest's later behaviour shows it.
    tools/s5/tests/rec_harness.c holds such a store only as far as its stand-in
    for the register file reaches: nine 32-bit registers and the x87 control word,
    40 bytes laid out as the harness lays them out, not the runtime's State, for
    the calls the harness makes;
  * how often, and with what, it calls what it may call. Each native it keeps and
    each __real_ function writes guest memory, allocates guest heap or reaches the
    scheduler by design; that the recorder calls each exactly once per guest call,
    with the guest's own arguments, is held at run time by tools/s5/present_run.py
    (the recorder's per-method and per-entry counts against the run record and the
    import table, the whole run record, and the device-log walk over the first
    8,192 calls), not here;
  * code outside the object: libc, and the runtime getters it calls;
  * an instruction hidden from the linear sweep by bytes placed in the code and
    entered only through a register, where no branch, symbol or relocation names
    the place. Compilers put no data in .text; only inline asm can, and
    SOURCE_RULES refuses it in the recorder's source by its keyword
    (Documentation/scripts/tests/test_s5_stream.py, and tools/s5/present_run.py on
    the tree it builds). That rule reads the keyword as written, not one the
    preprocessor builds; hidden bytes that a branch, symbol or relocation enters
    are the boundary rule's either way.

Usage:
  .venv/bin/python tools/s5/objcheck.py OBJECT     # the verdict as JSON; exit 0 when it holds

Documentation/scripts/tests/test_s5_stream.py compiles host/sa2_present_rec.c with
tools/s4/link_s4.py's flags and holds it here, with a compiled falsifier for every
rule; tools/s5/present_run.py holds the object it actually linked.
"""
from __future__ import annotations

import bisect
import json
import re
import struct
import sys
from pathlib import Path

# The functions a presentation build wraps (-Wl,-wrap) and the recorder defines as
# __wrap_<name>: every function-table registration, Direct3DCreate9 and the eleven
# d3dx9 entry points. tools/s5/present_run.py links with exactly these.
WRAPS = ("sa2_ft_add", "sa2_d3d9_make_d3d9",
         "sa2_d3d9_d3dx_texture_from_memory", "sa2_d3d9_d3dx_create_texture",
         "sa2_d3d9_d3dx_filter_texture", "sa2_d3d9_d3dx_load_surface_from_surface",
         "sa2_d3d9_d3dx_get_shader_constant_table", "sa2_d3d9_d3dx_cube_from_memory",
         "sa2_d3d9_d3dx_volume_from_memory", "sa2_d3d9_d3dx_create_cube_texture",
         "sa2_d3d9_d3dx_create_volume_texture", "sa2_d3d9_d3dx_save_surface_to_file",
         "sa2_d3d9_d3dx_save_texture_to_file")

# Every symbol the recorder may leave undefined, and why each is harmless.
ALLOWED_UNDEFINED = frozenset({
    # host libc: its own file, sidecar and counts, host memory, strings. The
    # formatters reach floating point only for a double argument or a %a/%e/%f/%g
    # conversion; the code rules below refuse both.
    "atexit", "calloc", "close", "__errno_location", "fclose", "fopen", "fprintf",
    "fputc", "fputs", "free", "fwrite", "getenv", "getpid", "malloc", "memcpy", "memset",
    "open", "realloc", "snprintf", "strcmp", "strerror", "strlen", "strstr", "vfprintf",
    "vsnprintf", "write",
    # the runtime, read only: a bounds check and a memcpy (sa2_peek), a register of
    # the call's state, the scheduler's and window's counters, the recording
    # device's tables, and its file-texture rows (sa2_d3d9_texsrc_at refreshes two
    # host fields every reader refreshes first; sa2_d3d9_method_flags formats into a
    # static buffer, read at start-up only)
    "sa2_peek", "sa2_rt_get_gpr", "sa2_sched_current", "sa2_sched_frames",
    "sa2_virtual_us", "sa2_window_stats", "sa2_d3d9_type_count", "sa2_d3d9_type_name",
    "sa2_d3d9_method_name", "sa2_d3d9_method_argc", "sa2_d3d9_method_flags",
    "sa2_d3d9_texsrc_count", "sa2_d3d9_texsrc_at",
    # the audio devices' tables (SA2_REC_AUDIO=1): their interface and method names
    # and argument counts, and a pointer to each buffer's and voice's record, which the
    # recorder copies field by field (runtime/sa2_dsound.c, runtime/sa2_xaudio2.c: each
    # returns a static table's row and writes nothing). The cursor helpers
    # sa2_dsound_buffer_cursors (it moves the device's wrap count) and
    # sa2_xaudio2_voice_now (it can trap) are not here.
    "sa2_dsound_type_count", "sa2_dsound_type_name", "sa2_dsound_method_name",
    "sa2_dsound_method_argc", "sa2_dsound_buffer_count", "sa2_dsound_buffer_at",
    "sa2_xaudio2_type_count", "sa2_xaudio2_type_name", "sa2_xaudio2_method_name",
    "sa2_xaudio2_method_argc", "sa2_xaudio2_voice_count", "sa2_xaudio2_voice_at",
}) | frozenset(f"__real_{w}" for w in WRAPS)   # the wrapped functions, called through
# The only global symbols the object may define: anything else would replace a
# definition of the same name elsewhere in the link (a weak runtime default, or
# another object's), which no check of the object's references would see.
DEFINED = frozenset(f"__wrap_{w}" for w in WRAPS)

# Floating point: an x87 or MMX instruction (the x87 register file is the lifted
# code's long double, and MMX aliases it), a 3DNow! one (the same), an SSE form that
# names an MMX register, a change of MXCSR or of the FP state, or SSE/AVX arithmetic
# and conversions. Moves, shuffles, bitwise and integer operations through xmm
# registers compute no floating point; SIMD_ALLOWED lists the ones allowed.
X87_OR_MMX = re.compile(r"^(FPU|CYRIX_FPU|MMX)")
THREE_DNOW = re.compile(r"D3NOW")
# The CPUID features of every instruction set that computes floating point in
# vector registers: SSE to SSE4, SSSE3, AVX to AVX-512, FMA, F16C, XOP and AMX.
SIMD = re.compile(r"^(SSE|SSSE3|AVX|FMA|F16C|XOP|AMX)")
# The SSE instructions the recorder may hold: the ones its compile uses, every SSE
# mnemonic of the production object (tools/s4/link_s4.py's flags, 2026-09-29), and
# no other. Each moves, rearranges or combines bits, or adds or compares integers;
# none rounds, converts or computes a floating-point value, or reads or writes
# MXCSR. An instruction of a SIMD feature that this list does not name is refused --
# the AVX (VEX) forms included, since the recorder is compiled for baseline x86-64.
# A compile that needs another is a change to this list, with its reason.
SIMD_ALLOWED = frozenset({
    # moves
    "MOVAPS", "MOVUPS", "MOVDQA", "MOVDQU", "MOVD", "MOVQ", "MOVSD", "MOVLPS",
    # shuffles and unpacks
    "PSHUFD", "PSHUFLW", "PUNPCKLBW", "PUNPCKLWD", "PUNPCKLDQ",
    # bitwise
    "PXOR", "PAND", "XORPS",
    # integer arithmetic and compares
    "PADDD", "PADDQ", "PSUBD", "PCMPEQD",
})
FP_STATE = frozenset({"LDMXCSR", "VLDMXCSR", "STMXCSR", "VSTMXCSR", "FXRSTOR", "FXRSTOR64",
                      "FXSAVE", "FXSAVE64", "XRSTOR", "XRSTOR64", "XSAVE", "XSAVE64",
                      "XSAVEOPT", "XSAVEOPT64", "XSAVEC", "XSAVEC64", "XSAVES", "XSAVES64",
                      "XRSTORS", "XRSTORS64", "EMMS", "FEMMS", "WAIT"})
FP_ARITH = re.compile(r"^V?(?:(?:ADD|SUB|MUL|DIV|SQRT|MIN|MAX|RCP\d*|RSQRT\d*|CMP|COMI|UCOMI|ROUND"
                      r"|DP|HADD|HSUB|ADDSUB|F?N?M(?:ADD|SUB)\w*)(?:PS|PD|SS|SD|SH|PH)|CVT\w*)$")
FP_REGISTER = re.compile(r"^(MM|ST)\d$")
# A system call or a software interrupt: the recorder's host effects are its own
# file and memory, through libc.
SYSTEM = frozenset({"SYSCALL", "SYSENTER", "SYSRET", "SYSEXIT", "INT", "INT1", "INT3", "INTO"})
# A printf conversion that formats a floating-point value (C11 7.21.6.1), with its
# flags, width, precision and length.
FLOAT_CONVERSION = re.compile(rb"%(?:\d+\$)?[-+ #0']*(?:\d+|\*(?:\d+\$)?)?"
                              rb"(?:\.(?:\d+|\*(?:\d+\$)?)?)?(?:hh|h|ll|l|L|j|z|t|q)?[aAeEfFgG]")
PRINTF_PERCENT = re.compile(rb"%%")

SHT_PROGBITS, SHT_SYMTAB, SHT_RELA, SHT_NOBITS = 1, 2, 4, 8
SHF_ALLOC, SHF_EXECINSTR, SHN_UNDEF = 0x2, 0x4, 0
STB_LOCAL, STT_FUNC = 0, 2
SECTION_TYPES = {1: "PROGBITS", 7: "NOTE", 8: "NOBITS", 14: "INIT_ARRAY", 15: "FINI_ARRAY",
                 16: "PREINIT_ARRAY", 0x70000001: "X86_64_UNWIND"}


class ObjError(ValueError):
    """Not an ELF64 little-endian relocatable object this reader can read."""


def sections(data: bytes) -> list[dict]:
    if len(data) < 64 or data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ObjError("not a little-endian ELF64 file")
    shoff, = struct.unpack_from("<Q", data, 0x28)
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    if shentsize != 64 or not shnum or shoff + 64 * shnum > len(data) or shstrndx >= shnum:
        raise ObjError("the section header table does not read")
    out = []
    for i in range(shnum):
        name, typ, flags, _addr, off, size, link, info, _align, entsize = struct.unpack_from(
            "<IIQQQQIIQQ", data, shoff + 64 * i)
        out.append({"index": i, "name_off": name, "type": typ, "flags": flags, "offset": off,
                    "size": size, "link": link, "info": info, "entsize": entsize})
    strtab = out[shstrndx]
    for s in out:
        s["name"] = _cstr(data, strtab["offset"] + s["name_off"])
    return out


def _cstr(data: bytes, off: int) -> str:
    end = data.find(b"\0", off)
    return data[off:end if end >= 0 else len(data)].decode("utf-8", "replace")


def symbols(data: bytes) -> list[dict]:
    """Every symbol-table entry: name, binding, type, section index and value."""
    out = []
    secs = sections(data)
    for s in secs:
        if s["type"] != SHT_SYMTAB:
            continue
        strtab = secs[s["link"]]
        for k in range(s["size"] // 24):
            name, info, _other, shndx, value, _size = struct.unpack_from(
                "<IBBHQQ", data, s["offset"] + 24 * k)
            out.append({"name": _cstr(data, strtab["offset"] + name) if name else "",
                        "bind": info >> 4, "type": info & 0xF, "shndx": shndx, "value": value})
    return out


def undefined_symbols(data: bytes) -> set[str]:
    """Every symbol the object references and does not define, weak ones included."""
    out = set()
    secs = sections(data)
    for s in secs:
        if s["type"] != SHT_SYMTAB:
            continue
        strtab = secs[s["link"]]
        for k in range(s["size"] // 24):
            name, _info, _other, shndx, _value, _size = struct.unpack_from(
                "<IBBHQQ", data, s["offset"] + 24 * k)
            if shndx == SHN_UNDEF and name:
                out.add(_cstr(data, strtab["offset"] + name))
    return out


def defined_globals(data: bytes) -> set[str]:
    """Every symbol the object defines that the link can see: global or weak binding,
    in any section, common and absolute ones included."""
    return {s["name"] for s in symbols(data)
            if s["shndx"] != SHN_UNDEF and s["bind"] != STB_LOCAL and s["name"]}


R_X86_64_PC32, R_X86_64_PLT32, R_X86_64_PC64 = 2, 4, 24
PC_RELATIVE = frozenset({R_X86_64_PC32, R_X86_64_PLT32, R_X86_64_PC64})


def section_type(typ: int) -> str:
    return f"{SECTION_TYPES.get(typ, 'type')} ({typ:#x})"


def _executable(data: bytes, secs: list[dict]) -> dict[int, list]:
    """Every executable section that holds bytes, whatever its type, decoded by a
    linear sweep from its start: {section index: instructions}."""
    from iced_x86 import Decoder
    return {s["index"]: list(Decoder(64, data[s["offset"]:s["offset"] + s["size"]], ip=0))
            for s in secs
            if s["flags"] & SHF_EXECINSTR and s["type"] != SHT_NOBITS and s["size"]}


def _instruction_at(code, starts: list[int], off: int):
    """The instruction of a sweep whose bytes hold offset `off`, or None."""
    k = bisect.bisect_right(starts, off) - 1
    return code[k] if k >= 0 and off < code[k].next_ip else None


def _code_labels(data: bytes, secs: list[dict], syms: list[dict], decoded: dict[int, list],
                 index: int) -> set[int]:
    """Offsets in executable section `index` that control can reach other than from
    the instruction before: every direct branch's target, every symbol defined there
    (a function's entry), and every relocation's target there (a jump table's
    entries, a function whose address is taken, the unwind and debug tables'
    addresses). A relocation's target is its symbol plus its addend, and, for a
    PC-relative field inside an instruction, plus the distance from the field to the
    instruction's end, where the PC is when the CPU adds the field."""
    import iced_x86 as I
    out = set()
    for ins in decoded[index]:
        if ins.flow_control in (I.FlowControl.CONDITIONAL_BRANCH,
                                I.FlowControl.UNCONDITIONAL_BRANCH, I.FlowControl.CALL) \
                and ins.op0_kind in (I.OpKind.NEAR_BRANCH64, I.OpKind.NEAR_BRANCH32):
            out.add(ins.near_branch_target)
    out.update(s["value"] for s in syms if s["shndx"] == index)
    for r in secs:
        if r["type"] != SHT_RELA:
            continue
        site = decoded.get(r["info"])            # the code the relocations patch, if code
        starts = [ins.ip for ins in site] if site else []
        for k in range(r["size"] // 24):
            off, info, addend = struct.unpack_from("<QQq", data, r["offset"] + 24 * k)
            sym, typ = info >> 32, info & 0xFFFFFFFF
            if sym >= len(syms) or syms[sym]["shndx"] != index:
                continue
            target = syms[sym]["value"] + addend
            ins = _instruction_at(site, starts, off) if site and typ in PC_RELATIVE else None
            if ins is not None:
                target += ins.next_ip - off
            out.add(target)
    return out


def code_findings(data: bytes) -> list[str]:
    """What the code rules refuse in the object's executable sections: each executable
    section that is not PROGBITS, as 'section: why'; each branch target, symbol or
    relocation target inside an instruction, as 'section+offset inside MNEMONIC at
    section+offset: why'; and each instruction a rule refuses, as 'section+offset
    MNEMONIC: why'."""
    import iced_x86 as I
    from iced_x86 import CpuidFeature, Mnemonic, Register
    mnemonic = {v: k for k, v in vars(Mnemonic).items() if isinstance(v, int) and not k.startswith("_")}
    feature = {v: k for k, v in vars(CpuidFeature).items()
               if isinstance(v, int) and not k.startswith("_")}
    register = {v: k for k, v in vars(Register).items() if isinstance(v, int) and not k.startswith("_")}
    rax = {Register.AL, Register.AX, Register.EAX, Register.RAX}
    writes = {I.OpAccess.WRITE, I.OpAccess.READ_WRITE, I.OpAccess.COND_WRITE,
              I.OpAccess.READ_COND_WRITE}
    reads = {I.OpAccess.READ, I.OpAccess.READ_WRITE, I.OpAccess.COND_READ,
             I.OpAccess.READ_COND_WRITE}
    info = I.InstructionInfoFactory()
    found = []
    secs = sections(data)
    syms = symbols(data)
    for s in secs:
        if s["flags"] & SHF_EXECINSTR and s["type"] != SHT_PROGBITS:
            found.append(f"{s['name']}: an executable section of type {section_type(s['type'])}, "
                         f"not PROGBITS: code outside the sections a compiler writes code to")
    decoded = _executable(data, secs)
    for index, code in decoded.items():
        s = secs[index]
        labels = _code_labels(data, secs, syms, decoded, index)
        starts = [ins.ip for ins in code]
        for x in sorted(labels):
            ins = _instruction_at(code, starts, x) if 0 <= x < s["size"] else None
            if ins is not None and ins.ip != x:
                found.append(f"{s['name']}+{x:#x} inside {mnemonic.get(ins.mnemonic, '?')} at "
                             f"{s['name']}+{ins.ip:#x}: a branch target, symbol or relocation "
                             f"target that starts no instruction of the linear sweep, which is "
                             f"what bytes placed in the code look like")
        entries = {y["value"] for y in syms if y["shndx"] == index and y["type"] == STT_FUNC}
        relocated = _relocated_offsets(data, secs, index)
        for i, ins in enumerate(code):
            name = mnemonic.get(ins.mnemonic, "?")
            feats = [feature.get(f, "?") for f in ins.cpuid_features()]
            regs = [register.get(ins.op_register(k), "?") for k in range(ins.op_count)
                    if ins.op_kind(k) == I.OpKind.REGISTER]
            why = None
            if ins.code == I.Code.INVALID:
                why = "does not decode: the linear sweep has lost the instruction stream"
            elif any(X87_OR_MMX.match(f) or THREE_DNOW.search(f) for f in feats):
                why = f"an x87, MMX or 3DNow! instruction ({', '.join(feats)})"
            elif any(FP_REGISTER.match(r) for r in regs):
                why = f"names an MMX or x87 register ({', '.join(regs)})"
            elif name in FP_STATE:
                why = "loads, saves or waits on the FP state or MXCSR"
            elif FP_ARITH.match(name) and any(SIMD.match(f) for f in feats):
                why = "SSE/AVX floating-point arithmetic or conversion"
            elif any(SIMD.match(f) for f in feats) and name not in SIMD_ALLOWED:
                why = (f"an SSE/AVX instruction ({', '.join(feats)}) that SIMD_ALLOWED does not "
                       f"list as a move, shuffle, bitwise or integer operation")
            elif name in SYSTEM:
                why = "a system call or software interrupt"
            elif _leaves_for_a_function(I, ins, entries, relocated):
                n = _vector_count(code, i, labels, info, rax, writes, reads)
                if n:
                    why = (f"reached with AL = {n}: a variadic call that passes arguments "
                           f"in vector registers, which is how a double reaches printf")
            if why:
                found.append(f"{s['name']}+{ins.ip:#x} {name}: {why}")
    return found


def _relocated_offsets(data: bytes, secs: list[dict], index: int) -> set[int]:
    """The offsets in section `index` a relocation writes: where an instruction names a
    symbol the assembler could not resolve, such as a function elsewhere."""
    out = set()
    for r in secs:
        if r["type"] == SHT_RELA and r["info"] == index:
            for k in range(r["size"] // 24):
                out.add(struct.unpack_from("<Q", data, r["offset"] + 24 * k)[0])
    return out


def _leaves_for_a_function(I, ins, entries: set[int], relocated: set[int]) -> bool:
    """A call; an indirect jump, whose target nothing here knows, so it is held as a
    tail call; or a direct jump that enters a function (a tail call): its target a
    relocated symbol or a function's entry in the section."""
    if ins.flow_control in (I.FlowControl.CALL, I.FlowControl.INDIRECT_CALL,
                            I.FlowControl.INDIRECT_BRANCH):
        return True
    if ins.flow_control not in (I.FlowControl.UNCONDITIONAL_BRANCH,
                                I.FlowControl.CONDITIONAL_BRANCH):
        return False
    return (any(r in relocated for r in range(ins.ip + 1, ins.next_ip))
            or (ins.op0_kind in (I.OpKind.NEAR_BRANCH64, I.OpKind.NEAR_BRANCH32)
                and ins.near_branch_target in entries))


def _vector_count(code, i: int, labels: set[int], info, rax, writes, reads) -> int:
    """The SysV vector-register count in AL at the call code[i], when an immediate
    set it in the call's own straight-line run and nothing read RAX since; else 0
    (a variadic call's AL is always set that way, by `mov al, n` or `xor eax, eax`)."""
    import iced_x86 as I
    j = i - 1
    while j >= 0 and code[j + 1].ip not in labels:
        p = code[j]
        if p.flow_control != I.FlowControl.NEXT:
            return 0
        used = info.info(p).used_registers()
        if any(u.register in rax and u.access in writes for u in used):
            if p.mnemonic == I.Mnemonic.MOV and p.op_count == 2 and p.op1_kind in (
                    I.OpKind.IMMEDIATE8, I.OpKind.IMMEDIATE16, I.OpKind.IMMEDIATE32,
                    I.OpKind.IMMEDIATE64, I.OpKind.IMMEDIATE32TO64, I.OpKind.IMMEDIATE8TO32,
                    I.OpKind.IMMEDIATE8TO64):
                return p.immediate(1) & 0xFF
            return 0
        if any(u.register in rax and u.access in reads for u in used):
            return 0
        j -= 1
    return 0


def float_conversions(data: bytes) -> list[str]:
    """Each string literal in the object's allocated data that holds a floating-point
    printf conversion, as 'section+offset "text"'. Every allocated section that holds
    bytes and no code is read, whatever its type: a section attribute puts a format in
    an SHT_NOTE section as easily as in .rodata."""
    out = []
    for s in sections(data):
        if (not s["flags"] & SHF_ALLOC or s["flags"] & SHF_EXECINSTR or s["type"] == SHT_NOBITS
                or not s["size"]):
            continue
        blob = data[s["offset"]:s["offset"] + s["size"]]
        for m in re.finditer(rb"[\x20-\x7e\t\n]{2,}", blob):
            text = PRINTF_PERCENT.sub(b"", m.group(0))
            if FLOAT_CONVERSION.search(text):
                out.append(f"{s['name']}+{m.start():#x} {m.group(0)[:60].decode()!r}")
    return out


# What the recorder's source may not hold: inline assembly, the one way to put bytes
# in the code that the linear sweep would read as other instructions.
SOURCE_RULES = {"inline asm": re.compile(r"\b(?:__asm__|__asm|asm)\b")}


def source_problems(text: str) -> list[str]:
    """Where the C source `text` breaks SOURCE_RULES, comments and string literals
    aside."""
    code = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    code = re.sub(r"//[^\n]*", " ", code)
    code = re.sub(r'"(?:\\.|[^"\\\n])*"', '""', code)
    out = []
    for why, rule in SOURCE_RULES.items():
        for m in rule.finditer(code):
            line = code.count("\n", 0, m.start()) + 1
            out.append(f"line {line}: {why} ({m.group(0)})")
    return out


def check(path: Path) -> dict:
    """The object at `path` against ALLOWED_UNDEFINED, DEFINED and the code rules:
    {ok, ...}."""
    data = Path(path).read_bytes()
    try:
        undef = undefined_symbols(data)
        defined = defined_globals(data)
        found = code_findings(data)
        floats = float_conversions(data)
    except ObjError as e:
        return {"ok": False, "why": str(e)}
    extra = sorted(undef - ALLOWED_UNDEFINED)
    missing = sorted(ALLOWED_UNDEFINED - undef)
    defined_extra = sorted(defined - DEFINED)
    defined_missing = sorted(DEFINED - defined)
    return {"ok": not extra and not missing and not defined_extra and not defined_missing
            and not found and not floats,
            "undefined": len(undef), "not_allowed": extra, "allowed_but_unused": missing,
            "defined": len(defined), "defined_not_allowed": defined_extra,
            "wraps_not_defined": defined_missing,
            "code_findings": found[:20], "code_finding_count": len(found),
            "float_conversions": floats[:20]}


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: objcheck.py OBJECT", file=sys.stderr)
        return 2
    got = check(Path(args[0]))
    print(json.dumps(got, indent=1))
    return 0 if got["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
