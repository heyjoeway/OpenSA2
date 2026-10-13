/* Does a guest SEH prologue run? Driven, not argued.
 *
 *   usage: seh_probe <sonic2app-unpacked.exe> [--va 0x00406520]
 *
 * This is the falsification test for the `fs_base` defect. `sa2_cpu_state_reset`
 * used to memset the state and leave `fs_base` at 0, so `FS:[0]` was guest
 * address 0, inside the null guard, and every one of the image's 1098 decoded
 * `fs:[0]` accesses trapped SA2_TRAP_FAULT at 0x00000000. That is not a
 * theoretical cost: the arm-seeding experiment could only reach the switch it
 * was testing by stubbing `FUN_00406520` out, because its first fs-touching
 * instruction faults before the function does anything of its own.
 *
 * So the probe drives that function, in a program linked against the whole
 * image, and looks at three things:
 *
 *   0x00406527  mov eax, fs:[0]    the read, which is what faulted
 *   0x00406553  mov fs:[0], eax    the write, which is the registration
 *   0x00406578  call [8730BCh]     an unbound import, past both
 *
 * "It got further" is easy to fake, so the evidence is the guest's own write:
 * the TEB's chain head starts at SA2_SEH_END_OF_CHAIN and the prologue
 * overwrites it with a stack address. If that word still terminates the chain
 * after the call, the registration did not happen, whatever else the run
 * reports. The probe fails on that, on a fault at guest address 0, and on a TEB
 * that is not laid out before anything runs.
 *
 * Reaching the unbound import and stopping there is the *expected* end at S3:
 * 0 of the image's 251 IAT slots hold an address the function table has a row
 * for, and import binding is S4 (Documentation/S3_INPUTS.md section 3a). The
 * probe reports that address rather than calling it a pass or a failure.
 *
 * Output is one JSON document on stdout; exit status is 0 only if every
 * assertion held.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_trap.h"

/* The probe is built against the runtime *before* the fix as well, to show it
 * failing; that runtime has no TEB constants. Naming them here keeps one source
 * for both builds, and when the macros do exist they are checked against these
 * rather than shadowing them. */
#ifndef SA2_GUEST_TEB_BASE
#define SA2_GUEST_TEB_BASE   0x03F00000u
#define SA2_TEB_EXCEPTION_LIST 0x00u
#define SA2_TEB_SELF           0x18u
#define SA2_SEH_END_OF_CHAIN   0xFFFFFFFFu
#define SA2_PROBE_PREFIX_RUNTIME 1
#endif

#define DEFAULT_VA   0x00406520u
#define FS_READ_AT   0x00406527u
#define FS_WRITE_AT  0x00406553u
#define STACK_TOP    0x03eefffcu   /* the arm probe's convention: (STACK_TOP-4) 8-aligned */

static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    if (sa2_peek(a, 4, &v) != SA2_OK) return 0xDEADBEEFu;
    return v;
}

static void jstr(const char *key, const char *value, const char *tail) {
    printf("  \"%s\": \"", key);
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') putchar('\\');
        if (*p == '\n') { fputs("\\n", stdout); continue; }
        putchar(*p);
    }
    printf("\"%s\n", tail);
}

int main(int argc, char **argv) {
    const char *pe = NULL;
    uint32_t va = DEFAULT_VA;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--va") && i + 1 < argc) va = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!pe) pe = argv[i];
    }
    if (!pe) { fprintf(stderr, "usage: seh_probe <sonic2app-unpacked.exe> [--va VA]\n"); return 2; }

    if (sa2_runtime_init(pe, 16) != SA2_OK) {
        fprintf(stderr, "seh_probe: sa2_runtime_init(%s) failed\n", pe);
        return 2;
    }

    const uint32_t teb = SA2_GUEST_TEB_BASE;
    const uint32_t chain_before = peek32(teb + SA2_TEB_EXCEPTION_LIST);
    const uint32_t self_before  = peek32(teb + SA2_TEB_SELF);

    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    const uint32_t fs_base = st.fs_base;
    st.gpr[SA2_ESP] = STACK_TOP;
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);

    sa2_call_result_t res;
    const int rc = sa2_call(va, &st, 20, &res);

    const uint32_t chain_after = peek32(teb + SA2_TEB_EXCEPTION_LIST);

    /* Assertions, each named so a failure says which one. */
    const int teb_laid_out   = (chain_before == SA2_SEH_END_OF_CHAIN);
    const int self_ok        = (self_before == teb);
    const int fs_base_set    = (fs_base != 0u) && (fs_base == teb);
    const int not_null_fault = !(res.status == SA2_TRAP_FAULT && res.exit_pc == 0u);
    /* The prologue's own write. The chain head is no longer the terminator, and
     * what replaced it is a guest stack address. */
    const int registered     = (chain_after != chain_before)
                            && (chain_after >= SA2_GUEST_STACK_BASE)
                            && (chain_after < SA2_GUEST_STACK_TOP);
    const int ok = teb_laid_out && self_ok && fs_base_set && not_null_fault && registered;

    printf("{\n");
    printf("  \"va\": \"0x%08x\", \"rc\": %d,\n", va, rc);
    printf("  \"fs_base\": \"0x%08x\", \"teb\": \"0x%08x\",\n", fs_base, teb);
    printf("  \"teb_chain_before\": \"0x%08x\", \"teb_chain_after\": \"0x%08x\",\n",
           chain_before, chain_after);
    printf("  \"teb_self\": \"0x%08x\",\n", self_before);
    printf("  \"trap\": {\"status\": %u, \"exit_pc\": \"0x%08x\"},\n",
           res.status, res.exit_pc);
    jstr("note", res.note, ",");
    printf("  \"fs_read_at\": \"0x%08x\", \"fs_write_at\": \"0x%08x\",\n",
           FS_READ_AT, FS_WRITE_AT);
    printf("  \"assert\": {\"teb_laid_out\": %s, \"teb_self\": %s, \"fs_base_set\": %s, "
           "\"no_null_fault\": %s, \"seh_registration_written\": %s},\n",
           teb_laid_out ? "true" : "false", self_ok ? "true" : "false",
           fs_base_set ? "true" : "false", not_null_fault ? "true" : "false",
           registered ? "true" : "false");
#ifdef SA2_PROBE_PREFIX_RUNTIME
    printf("  \"runtime_has_teb_constants\": false,\n");
#else
    printf("  \"runtime_has_teb_constants\": true,\n");
#endif
    printf("  \"ok\": %s\n}\n", ok ? "true" : "false");

    sa2_runtime_shutdown();
    return ok ? 0 : 1;
}
