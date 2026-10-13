/* Calling lifted guest code from inside a native handler.
 *
 * Why a native ever has to
 * ------------------------
 * Two presentation interfaces hand the guest's own function pointer back to
 * the system and expect the system to call it:
 *
 *  - IDirectInput8A::EnumDevices(DI8DEVCLASS_GAMECTRL, 0x00864C90, pvRef,
 *    DIEDFL_ATTACHEDONLY), pushed at 0x00864E0B..0x00864E15, calls
 *    0x00864C90 once per attached game controller;
 *  - DirectDrawEnumerateExA(0x00865C20, ...) calls its callback once per
 *    display device.
 *
 * A native that answered those without calling back would be answering "no
 * device" whatever the capture's device profile says, which is the refusal
 * the 2026-09-23 ruling
 * (Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md) no longer
 * accepts when native enumerated something.
 *
 * How
 * ---
 * Exactly as `call_wndproc` in runtime/sa2_imports.c runs a window procedure,
 * because that is the one guest callback this runtime already makes and its
 * frame has been exercised by every pumped message since S4 slice 4:
 *
 *   - `sa2_call` cannot be used: it owns one remill State buffer per thread
 *     and re-entering it would overwrite the state the guest is running on.
 *     The call is made on the *live* state instead, the way lifted code makes
 *     one: build the frame on the guest's own stack, point EIP at the callee,
 *     dispatch through the function table.
 *   - The frame starts SA2_GUEST_CALL_SLACK bytes below the native's ESP, the
 *     same 0x40 `call_wndproc` leaves, so a callee that reads a little past its
 *     own arguments reads dead stack and not the native's frame. At the
 *     largest argc this helper takes, 0x1C bytes still separate the callee's
 *     last argument from the native's return address.
 *   - [base] is SA2_CALL_SENTINEL, [base+4..] the arguments in order.
 *   - **ESP is the check, not EIP.** A stdcall callee pops its own return
 *     address and arguments, so ESP must come back as base + 4 + 4*argc. That
 *     holds for a `ret 8` (0x00864CE3 and 0x00864CF7 in the EnumDevices
 *     callback) and for a tail jump into an import, after which EIP is a thunk
 *     and no `ret` ran in lifted code at all -- the case `call_wndproc`'s own
 *     comment records for DefWindowProcW. Anything else traps
 *     SA2_TRAP_CONTROL_LEFT naming the callee, because a callee that popped
 *     the wrong amount has already corrupted the frame the native returns
 *     through.
 *   - ESP and EIP are restored afterwards, so the native that made the call
 *     pops its own arguments from where they were.
 *
 * Re-entrancy is the caller's job. The callee runs lifted code, which calls
 * imports and COM methods of its own; a dispatcher that keeps a "current call"
 * record (runtime/sa2_dinput.c's g_cur, sa2_imports.c's g_current) must save
 * it before calling this and restore it after, or the nested calls overwrite
 * the record the outer call settles into.
 */
#ifndef SA2_RUNTIME_GUEST_CALL_H
#define SA2_RUNTIME_GUEST_CALL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_GUEST_CALL_SLACK    0x40u   /* call_wndproc's, in runtime/sa2_imports.c */
#define SA2_GUEST_CALL_MAX_ARGS 8u      /* 4 + 4*8 = 0x24 of the 0x40 */

/* Call the lifted stdcall function at guest address `fn` with `argc` dwords
 * from `argv`, on the live CPU state `s`, and return its EAX. Traps
 * SA2_TRAP_NOT_RUNNABLE when nothing is registered at `fn`, and
 * SA2_TRAP_CONTROL_LEFT naming `fn` when it does not pop exactly its return
 * address and `argc` arguments. */
uint32_t sa2_guest_stdcall(void *s, uint32_t fn, unsigned argc, const uint32_t *argv);

/* Calls made, and the deepest nesting of this helper seen (a callback that
 * enumerates again would show 2). */
uint32_t sa2_guest_stdcall_count(void);
uint32_t sa2_guest_stdcall_max_depth(void);

#ifdef __cplusplus
}
#endif
#endif
