/* Can the dispatcher enter a lifted function in the middle? Asked of the binary.
 *
 *   usage: owner_entry_probe <sonic2app-unpacked.exe>
 *
 * 343 of the image's 5 715 jump-table arms have no `sub_<arm>` symbol. Their
 * code is lifted -- remill's recursive descent reached them by direct flow from
 * the owner's entry -- but it lives inside the owner's `sub_<owner>` as an
 * unnamed basic block, so an indirect jump to one is a function-table miss.
 *
 * The cheap-looking fix is to give the arm a table row pointing at the *owner*:
 * the runtime calls `fn(state, va, memory)`, so the owner would be entered with
 * `%program_counter` equal to the arm's address, and a lifted function that
 * selected its entry block by the incoming PC would then start at the arm.
 *
 * remill's lifted functions do not select a block by `%program_counter`. The
 * entry block stores the incoming value into `%NEXT_PC` and then branches
 * unconditionally into the block that lifts the *entry* instruction. This probe
 * puts that to the linked binary rather than to the IR: it registers
 * `sub_439570` in the function table under the address of one of its own arms,
 * 0x00439592, and calls it through `sa2_call`.
 *
 *     00439570  inc eax
 *     00439571  cmp eax,7
 *     00439574  ja  00439592          ; the same arm, reached directly
 *     00439576  jmp dword [eax*4+439598h]
 *     ...
 *     00439592  or eax,0FFFFFFFFh     ; the arm: EAX = -1 for every input
 *     00439595  ret
 *
 * Entering at 0x00439592 must leave EAX = 0xFFFFFFFF whatever EAX came in as.
 * Entering at the owner runs the prologue, so EAX comes back as whatever the
 * table's arm for `EAX+1` computes. The observable is the returned EAX, which
 * only the guest code could have produced, and the expected value is read from
 * the shipped executable by `tools/s3/arms/switch_oracle.py`; this probe hard-
 * codes only the one value the arm itself cannot avoid producing.
 *
 * The result that matters is not how many cases are wrong. It is that **none of
 * them trap**: the mis-entry is silent, so this route would replace a trap that
 * names its address with a wrong answer that names nothing.
 *
 * `--as-linked` registers nothing and calls 0x00439592 as the linked image
 * resolves it. It is the control, and it answers differently in the two
 * binaries it can be linked against: with the arm unseeded every case fails to
 * dispatch, and with `sub_439592` linked every case returns 0xFFFFFFFF. That is
 * the probe seen reporting total disagreement and total agreement on the same
 * inputs, so "8 of 10 disagree" above cannot be the probe being broken.
 *
 * Output is one JSON document on stdout. Exit status is 0 when the run produced
 * a verdict, not when the verdict is good: this probe exists to record a
 * failure mode, so a non-zero status would be backwards.
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

#define OWNER      0x00439570u
#define ARM        0x00439592u   /* `or eax,-1; ret`, one of the 343 */
#define ARM_RESULT 0xFFFFFFFFu   /* what entering at ARM must produce */
#define FRAME_ESP  0x03eeff00u

/* The lifted owner, by the symbol `gen_table.py` gives it a row for. */
extern void *sub_439570(void *state, uint32_t pc, void *memory);

static const uint32_t kInputs[] = {0u, 1u, 2u, 3u, 4u, 5u, 6u, 7u, 8u, 0xFFFFFFFFu};
#define NIN ((int)(sizeof kInputs / sizeof kInputs[0]))

static void jstr(const char *value) {
    putchar('"');
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') putchar('\\');
        if (*p == '\n') { fputs("\\n", stdout); continue; }
        putchar(*p);
    }
    putchar('"');
}

int main(int argc, char **argv) {
    const char *pe = NULL;
    int control = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--as-linked")) control = 1;
        else if (!pe) pe = argv[i];
    }
    if (!pe) {
        fprintf(stderr, "usage: owner_entry_probe <sonic2app-unpacked.exe> [--as-linked]\n");
        return 2;
    }
    if (sa2_runtime_init(pe, 16) != SA2_OK) {
        fprintf(stderr, "owner_entry_probe: sa2_runtime_init(%s) failed\n", pe);
        return 2;
    }

    const int arm_had_a_row = sa2_ft_lookup(ARM) != NULL;
    const uint32_t call_va = ARM;
    if (!control) {
        /* The whole point: a row for the arm that points at the owner. */
        if (sa2_ft_add(ARM, sub_439570, "route-B: owner registered at its arm",
                       SA2_FN_LIFTED) < 0) {
            fprintf(stderr, "owner_entry_probe: sa2_ft_add failed\n");
            return 2;
        }
    }

    printf("{\n");
    printf("  \"mode\": %s,\n",
           control ? "\"control: call the arm as the linked image resolves it\""
                   : "\"route B: the arm's row points at the owner\"");
    printf("  \"owner\": \"0x%08x\", \"arm\": \"0x%08x\",\n", OWNER, ARM);
    printf("  \"arm_had_a_table_row_before_this_probe\": %s,\n",
           arm_had_a_row ? "true" : "false");
    printf("  \"call_va\": \"0x%08x\",\n", call_va);
    printf("  \"expected_if_entry_were_honoured\": \"0x%08x\",\n", ARM_RESULT);
    printf("  \"cases\": [\n");

    int total = 0, agreed = 0, trapped = 0;
    const uint32_t misses_before = sa2_ft_misses();
    for (int i = 0; i < NIN; i++) {
        sa2_cpu_state_t st;
        sa2_cpu_state_reset(&st);
        st.gpr[SA2_ESP] = FRAME_ESP;
        st.gpr[SA2_EAX] = kInputs[i];
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);

        sa2_call_result_t res;
        sa2_call(call_va, &st, 20, &res);
        const int returned = res.status == SA2_TRAP_NONE &&
                             st.gpr[SA2_EIP] == SA2_CALL_SENTINEL;
        const int agree = returned && st.gpr[SA2_EAX] == ARM_RESULT;
        total++;
        agreed += agree;
        trapped += (res.status != SA2_TRAP_NONE);
        printf("    {\"eax_in\": \"0x%08x\", \"status\": %u, \"exit_pc\": \"0x%08x\", "
               "\"note\": ", kInputs[i], res.status, res.exit_pc);
        jstr(res.note);
        printf(", \"returned\": %s, \"eax_out\": \"0x%08x\", "
               "\"matches_the_arm\": %s}%s\n",
               returned ? "true" : "false", st.gpr[SA2_EAX],
               agree ? "true" : "false", i + 1 < NIN ? "," : "");
        fflush(stdout);
    }
    printf("  ],\n");
    printf("  \"summary\": {\"cases\": %d, \"agree_with_the_arm\": %d, "
           "\"disagree\": %d, \"trapped\": %d, \"dispatch_misses\": %u},\n",
           total, agreed, total - agreed, trapped,
           sa2_ft_misses() - misses_before);
    printf("  \"verdict\": ");
    if (control)
        jstr(agreed == total
             ? "control: the linked image enters at the arm itself and every case "
               "returns the arm's own answer"
             : trapped == total
             ? "control: the arm has no symbol in this binary, so every case fails to "
               "dispatch -- which is the trap the fix removes"
             : "control: mixed, which neither binary should produce");
    else
        jstr(trapped == 0 && agreed < total
             ? "route B as a bare table row is WRONG AND SILENT: the owner runs its "
               "prologue, the answer is the wrong arm's, and nothing traps"
             : trapped
             ? "route B trapped, which is not the failure mode this probe predicted"
             : "route B agreed on every case, which falsifies this probe's premise");
    printf("\n}\n");

    sa2_runtime_shutdown();
    return 0;
}
