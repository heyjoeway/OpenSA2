/* The instructions remill cannot lift, executed by the runtime.
 *
 * remill 5.0.7 has no semantics for seven instructions this image uses, and an
 * eighth below. S3's census found the seven, all in CRI's codec paths or the C
 * runtime's x87 environment code
 * (agents-journals/s3/2026-09-20-s3-whole-image-lift.md):
 *
 *   paddusb mm, mm/m64    0F DC /r      paddusw mm, mm/m64    0F DD /r
 *   cvtps2pi mm, xmm/m64  0F 2D /r
 *
 * The SSE2 xmm forms of the two adds (66 0F DC/DD) are not emulated: remill
 * lifts them, and S3 found only the MMX forms refused.
 *   fnstenv m28  D9 /6    fldenv m28  D9 /4    fnsave m108  DD /6    frstor m108  DD /4
 *
 * An eighth, in both forms, came with S4's address-taken functions
 * (tools/s4/address_taken.py): two IDCT routines of CRI's movie decoder, one
 * MMX and one SSE2, whose lifts end at their first psubsw (2026-09-23). remill
 * lifts paddsw and packssdw beside it and not this one:
 *
 *   psubsw mm, mm/m64     0F E9 /r      psubsw xmm, xmm/m128  66 0F E9 /r
 *
 * The xmm form's memory operand must be 16-byte aligned, as the hardware
 * requires; an unaligned one is refused by name, where the hardware would
 * raise #GP.
 *
 * For each, remill ends the trace: it calls __remill_sync_hyper_call with
 * kX86EmulateInstruction (256) with `gpr.rip` at the instruction, then
 * tail-calls __remill_error with the next instruction's address. S2 left the
 * hypercall a trap and wrote "S4 decides what each hypercall answers"; this is
 * the answer. The runtime decodes the instruction from guest memory, executes
 * it against remill's State, and __remill_error continues at the next
 * instruction through the function table, where tools/s4/lift_extra.py
 * --seed-continuations lifted an entry.
 *
 * The model is remill's own, so lifted code before and after sees what it
 * would have seen had remill lifted the instruction:
 *
 *   - MMX registers are `mmx.elems[i]`, apart from the x87 stack, as remill
 *     keeps them; an MMX instruction does not touch the x87 tags or TOP here,
 *     because remill models neither.
 *   - The rounding mode is the host's, as remill's FLDCW sets it and its
 *     conversions obey it, so cvtps2pi rounds by the host's mode and gives the
 *     integer indefinite where cvtps2dq would. psubsw and cvtps2pi are
 *     portable C (S7, for hosts without SSE2), compared over every input with
 *     the host instructions they replaced.
 *   - The x87 environment is remill's FXSAVE-shaped `x87.fxsave32`: the control
 *     word as remill's FNSTCW reports it after the PC24 pass (its rounding
 *     bits from the host), the status word as its FNSTSW builds it (condition
 *     codes and exception flags from `sw`, the rest from `swd`), and the stack
 *     in `st`, ST(0) first, which is FSAVE's register order. remill moves TOP
 *     in `swd` with every push and pop, so fldenv, and fnsave's FNINIT, which
 *     change TOP and not the registers, turn the logical stack as the hardware's
 *     view of its physical registers turns. The tag word and
 *     the instruction and operand pointers are stored and restored as remill
 *     holds them, which is unmaintained: no lifted instruction reads them.
 *
 * Guest memory is read and written through sa2_peek and sa2_poke only, so
 * harness/emulate_probe.cpp can drive this over a window it owns.
 */
#ifndef SA2_RUNTIME_EMULATE_H
#define SA2_RUNTIME_EMULATE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

enum {
    SA2_EMU_OK = 0,
    SA2_EMU_UNSUPPORTED = 1,   /* not one of the instructions above, or psubsw xmm with an
                                  unaligned m128; `why` names which */
    SA2_EMU_FAULT = 2,         /* a guest address could not be read or written: *fault_va */
};

/* Execute the instruction at `state->gpr.rip` (a remill `State *`). On
 * SA2_EMU_OK, *next_pc is the next instruction's address. On failure nothing
 * the instruction would change has been changed -- a store that would cross out
 * of guest memory writes nothing, as the host CPU writes nothing -- and
 * *fault_va is the first byte that is not guest memory. */
int sa2_emulate(void *state, uint32_t *next_pc, uint32_t *fault_va, char *why, size_t why_cap);

/* Instructions executed, by kind, in the order of the table in sa2_emulate.cpp. */
uint32_t    sa2_emulate_kinds(void);
const char *sa2_emulate_kind_name(uint32_t i);
uint64_t    sa2_emulate_kind_count(uint32_t i);

#ifdef __cplusplus
}
#endif

#endif
