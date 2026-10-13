/* Drive one lifted function that contains a switch, through the whole-image
 * binary, once per arm.
 *
 * S3 slice 2 established that remill stops at an indirect branch, so a jump
 * table's arms are in no module and every switch is a trap. The proposed fix
 * is to re-lift each arm as its own trace with `--entry_address` and give the
 * function table a row per arm. This program is the falsification test: it
 * links the whole image plus the six re-lifted arms of `FUN_004015f0`, calls
 * `sub_4015f0` through `sa2_call` once per selector, and checks each run
 * against an oracle decoded from the shipped executable rather than from the
 * lift.
 *
 * The oracle: the six arms differ in exactly one instruction, `mov ebx,
 * imm32`, and the tail they share does not write EBX before `call
 * 0x004026e0`. So EBX at that call names the arm that executed, and
 * `tools/s3/arms/oracle.py` reads the expected value for each selector out of
 * `private/exe/sonic2app-unpacked.exe`. A run that reaches the witness with
 * the wrong EBX ran the wrong arm; a run that never reaches it did not reach
 * an arm at all.
 *
 * What is stubbed, and why each one is unavoidable rather than convenient:
 *
 *   0x00406520  the switch selector itself. The guest computes it with a call,
 *               not from an argument, so choosing which arm runs *is* choosing
 *               this function's return value. Nothing else in the run could
 *               select an arm.
 *   0x007a4b64, 0x007a5da1  called before the switch; their results are
 *               discarded or only gate a branch.
 *   the three USER32 imports  the IAT is unbound at S3 (0 of 251 slots hold a
 *               dispatchable address), so every import call is a miss. Each
 *               stub is registered at the *value the IAT slot actually holds*,
 *               read out of guest memory here, so nothing about the image is
 *               assumed.
 *   0x004026e0  the witness. Stubbed so it records EBX instead of running a
 *               window-creation routine whose own callees are unbound imports.
 *
 * Everything between the selector and the witness -- the bounds check, the
 * table read, the indirect branch, the dispatch, the arm, the shared tail --
 * is lifted code running for real.
 *
 * Usage: arm_probe <pe-path> <oracle.txt> [--no-arm-rows]
 *   --no-arm-rows skips registering the six arm addresses, which is the
 *   control: the same binary, the same calls, without the proposed fix.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"

/* ---------------------------------------------------------------- symbols */
#define OWNER        0x004015f0u
#define SEL_SOURCE   0x00406520u   /* call at 0x00401636; its EAX is the switch index */
#define PRE_CALL_A   0x007a4b64u   /* call at 0x00401605, result discarded */
#define PRE_CALL_B   0x007a5da1u   /* call at 0x0040161b, gated by the global below */
#define GLOBAL_GATE  0x01af19ecu   /* cmp dword [0x01af19ec], 0 at 0x0040160d */
#define WITNESS      0x004026e0u   /* call at 0x00401694, reached only through an arm */
#define IAT_A        0x00873344u   /* call dword [0x00873344] at 0x004015fa, 1 stdcall arg */
#define IAT_B        0x0087334cu   /* call dword [0x0087334c] at 0x00401679, 2 stdcall args */
#define IAT_C        0x00873358u   /* call dword [0x00873358] at 0x0040168c, 2 stdcall args */
#define JUMP_TABLE   0x004016b0u   /* jmp dword [eax*4 + 0x004016b0] at 0x00401640 */
#define DEFAULT_ARM  0x00401671u   /* the `ja` arm; direct flow reaches it, so it is lifted */

#define STACK_TOP    0x03eefffcu   /* stack arena; (STACK_TOP-4) is 8-aligned, so the
                                    * `and esp, 0xfffffff8` at 0x004015f3 is a no-op */
#define SEED_EDI     0x11223344u   /* the window handle the tail passes around */
#define WITNESS_EAX  0xc0de0000u   /* what the witness stub returns, per run */

/* The six arm traces, lifted by tools/s3/arms/lift_arms.py with
 * `--entry_address` and linked in beside the 12 683 whole-image objects. */
extern void *sub_401647(void *state, uint32_t pc, void *memory);
extern void *sub_40164e(void *state, uint32_t pc, void *memory);
extern void *sub_401655(void *state, uint32_t pc, void *memory);
extern void *sub_40165c(void *state, uint32_t pc, void *memory);
extern void *sub_401663(void *state, uint32_t pc, void *memory);
extern void *sub_40166a(void *state, uint32_t pc, void *memory);

static const struct { uint32_t va; sa2_lifted_fn fn; const char *name; } kArms[] = {
    {0x00401647u, sub_401647, "FUN_004015f0.arm0"},
    {0x0040164eu, sub_40164e, "FUN_004015f0.arm1"},
    {0x00401655u, sub_401655, "FUN_004015f0.arm2"},
    {0x0040165cu, sub_40165c, "FUN_004015f0.arm3"},
    {0x00401663u, sub_401663, "FUN_004015f0.arm4"},
    {0x0040166au, sub_40166a, "FUN_004015f0.arm5"},
};
#define NARMS ((int)(sizeof kArms / sizeof kArms[0]))

/* The landing pad `tools/s2/corpus_run/internalize.py` folds every module's
 * missing-block trampolines into. It is defined only in `harness/plan_driver.c`
 * and in `tools/s3/whole_image/link_probe.c`, so every other driver carries its
 * own copy; see that file's note. This is that copy. */
void *__sa2_dispatch_stub(void *state, uint32_t pc, void *memory) {
    return sa2_ft_dispatch(pc, state, pc, memory);
}

/* The witness is reached by a *direct* call (`call 0x004026e0` at 0x00401694),
 * which remill lifts to `call @sub_4026e0` and the linker binds to the lifted
 * object. A function-table native therefore cannot intercept it: the table is
 * consulted for indirect calls, imports and `__remill_jump`, and for nothing
 * else. This weak thunk is the harness's own pattern
 * (`harness/fixture_natives.c`): when `ir_004026E0.o` is on the link line its
 * strong definition wins and the real lifted function runs; when that one
 * object is left off, this routes the same call through the table to the
 * witness stub. The two links are the same source and the same 12 682 other
 * objects, which is why both are reported. */
int g_witness_thunk_entered, g_selector_thunk_entered;
__attribute__((weak)) void *sub_4026e0(void *state, uint32_t pc, void *memory) {
    (void)pc;
    g_witness_thunk_entered++;
    return sa2_ft_dispatch(WITNESS, state, WITNESS, memory);
}

/* The same problem, and the same fix, for the switch selector. `call
 * 0x00406520` at 0x00401636 is direct, so the table cannot intercept it
 * either, and the real FUN_00406520 opens with an SEH prologue that reads
 * `FS:[0]` -- which faults, because nothing sets `fs_base` (S3 slice 2's
 * third runtime gap). With `ir_00406520.o` off the link line this thunk routes
 * the call through the table to the selector stub, which is the only way to
 * choose which arm the switch takes. */
__attribute__((weak)) void *sub_406520(void *state, uint32_t pc, void *memory) {
    (void)pc;
    g_selector_thunk_entered++;
    return sa2_ft_dispatch(SEL_SOURCE, state, SEL_SOURCE, memory);
}

/* ---------------------------------------------------------------- natives */
static uint32_t g_selector;
static uint32_t g_run;
static int      g_witness_hits;
static uint32_t g_witness_ebx, g_witness_ecx, g_witness_esp, g_witness_eip;
static int      g_pre_a_hits, g_pre_b_hits, g_iat_hits[3];

static void ret_and_pop(void *s, uint32_t args) {
    sa2_rt_set_gpr(s, SA2_ESP, sa2_rt_get_gpr(s, SA2_ESP) + 4u + 4u * args);
}

static void *n_selector(void *s, uint32_t pc, void *m) {
    (void)pc;
    sa2_rt_set_gpr(s, SA2_EAX, g_selector);
    ret_and_pop(s, 0);
    return m;
}
static void *n_pre_a(void *s, uint32_t pc, void *m) {
    (void)pc; g_pre_a_hits++;
    sa2_rt_set_gpr(s, SA2_EAX, 0u);
    ret_and_pop(s, 0);              /* cdecl: the caller's `add esp,4` cleans up */
    return m;
}
static void *n_pre_b(void *s, uint32_t pc, void *m) {
    (void)pc; g_pre_b_hits++;
    sa2_rt_set_gpr(s, SA2_EAX, 0u);
    ret_and_pop(s, 0);
    return m;
}
static void *n_iat_a(void *s, uint32_t pc, void *m) {   /* ShowCursor(TRUE) */
    (void)pc; g_iat_hits[0]++;
    sa2_rt_set_gpr(s, SA2_EAX, 0u);
    ret_and_pop(s, 1);
    return m;
}
static void *n_iat_b(void *s, uint32_t pc, void *m) {   /* GetWindowLongW(hwnd, -16) */
    (void)pc; g_iat_hits[1]++;
    sa2_rt_set_gpr(s, SA2_EAX, 0u);   /* 0 -> esi = 0 -> the tail skips both ShowWindow calls */
    ret_and_pop(s, 2);
    return m;
}
static void *n_iat_c(void *s, uint32_t pc, void *m) {   /* ShowWindow(hwnd, n) */
    (void)pc; g_iat_hits[2]++;
    sa2_rt_set_gpr(s, SA2_EAX, 0u);
    ret_and_pop(s, 2);
    return m;
}
static void *n_witness(void *s, uint32_t pc, void *m) {
    g_witness_hits++;
    g_witness_ebx = sa2_rt_get_gpr(s, SA2_EBX);
    g_witness_ecx = sa2_rt_get_gpr(s, SA2_ECX);
    g_witness_esp = sa2_rt_get_gpr(s, SA2_ESP);
    g_witness_eip = pc;
    sa2_rt_set_gpr(s, SA2_EAX, WITNESS_EAX | g_run);
    ret_and_pop(s, 0);
    return m;
}

/* ---------------------------------------------------------------- helpers */
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0xffffffffu;
    if (sa2_peek(a, 4, &v) != SA2_OK) v = 0xffffffffu;
    return v;
}
static void poke32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }

/* ---- what the run wrote to guest memory ------------------------------
 * A byte-for-byte snapshot of the whole guest space before the call, diffed
 * after it. Cheap (64 MB memcpy plus one scan per run) and it answers the
 * question directly instead of naming the places an arm was expected to
 * write. Ranges are coalesced across gaps of up to 8 bytes. */
#define MAX_RANGES 12
typedef struct { uint32_t lo, hi; } range_t;

static uint32_t diff_ranges(const uint8_t *before, const uint8_t *after,
                            uint32_t size, range_t *out, uint32_t max) {
    uint32_t n = 0, i = 0;
    while (i < size) {
        if (before[i] == after[i]) { i++; continue; }
        uint32_t lo = i, last = i, gap = 0;
        for (i++; i < size && gap <= 8; i++) {
            if (before[i] != after[i]) { last = i; gap = 0; } else gap++;
        }
        if (n < max) { out[n].lo = lo; out[n].hi = last + 1; }
        n++;
    }
    return n;
}

static void json_escape(const char *in, char *out, size_t n) {
    size_t j = 0;
    for (size_t i = 0; in[i] && j + 2 < n; i++) {
        if (in[i] == '"' || in[i] == '\\') out[j++] = '\\';
        if ((unsigned char)in[i] < 0x20) { out[j++] = ' '; continue; }
        out[j++] = in[i];
    }
    out[j] = 0;
}

int main(int argc, char **argv) {
    const char *pe = argc > 1 ? argv[1] : "private/exe/sonic2app-unpacked.exe";
    const char *oracle_path = argc > 2 ? argv[2] : NULL;
    int register_arms = 1;
    for (int i = 3; i < argc; i++)
        if (!strcmp(argv[i], "--no-arm-rows")) register_arms = 0;

    if (sa2_runtime_init(pe, 32) != SA2_OK) {
        printf("{\"ok\":false,\"stage\":\"sa2_runtime_init\"}\n");
        return 1;
    }
    /* `SA2_MISSING_BLOCK_DISPATCH` in the environment, honoured rather than
     * assumed. It matters here for one edge that has nothing to do with the
     * arms: `FUN_007a4b64` is a five-instruction hot-patch thunk whose `jmp
     * 0x007a5da1` leaves its own lifted range, so the prologue's `call
     * 0x007a4b64` reaches a missing block before the switch is ever read. */
    sa2_mb_policy_from_env();
    const uint32_t rows_before = sa2_ft_count();

    /* The oracle, as written by tools/s3/arms/oracle.py from the executable. */
    uint32_t osel[16], oarm[16], oebx[16];
#define SEL_DEFAULT_ROW 0xffffffffu
    int on = 0;
    if (oracle_path) {
        FILE *f = fopen(oracle_path, "r");
        int n = 0;
        if (f && fscanf(f, "%d", &n) == 1) {
            while (on < n && on < 16 &&
                   fscanf(f, "%u %x %x", &osel[on], &oarm[on], &oebx[on]) == 3) on++;
        }
        if (f) fclose(f);
    }
    if (on == 0) {
        printf("{\"ok\":false,\"stage\":\"oracle\",\"why\":\"no oracle rows read\"}\n");
        return 1;
    }

    /* The six rows the recommendation asks for. Registered here rather than
     * generated into the table file so the same binary can run without them. */
    int arm_rows = 0;
    if (register_arms)
        for (int i = 0; i < NARMS; i++)
            if (sa2_ft_add(kArms[i].va, kArms[i].fn, kArms[i].name, SA2_FN_LIFTED) == 0)
                arm_rows++;

    /* The import stubs go at the addresses the IAT slots actually hold. */
    const uint32_t ia = peek32(IAT_A), ib = peek32(IAT_B), ic = peek32(IAT_C);
    sa2_ft_add(ia, n_iat_a, "USER32!ShowCursor (stub)", SA2_FN_NATIVE);
    sa2_ft_add(ib, n_iat_b, "USER32!GetWindowLongW (stub)", SA2_FN_NATIVE);
    sa2_ft_add(ic, n_iat_c, "USER32!ShowWindow (stub)", SA2_FN_NATIVE);
    sa2_ft_add(SEL_SOURCE, n_selector, "switch selector (stub)", SA2_FN_NATIVE);
    sa2_ft_add(PRE_CALL_A, n_pre_a, "0x007a4b64 (stub)", SA2_FN_NATIVE);
    sa2_ft_add(PRE_CALL_B, n_pre_b, "0x007a5da1 (stub)", SA2_FN_NATIVE);
    sa2_ft_add(WITNESS, n_witness, "0x004026e0 witness (stub)", SA2_FN_NATIVE);

    /* A chosen guest global, not a dropped one: non-zero takes the `jne` at
     * 0x00401614 and skips the allocation block before the switch. */
    poke32(GLOBAL_GATE, 1u);

    printf("{\n");
    printf("  \"owner\": \"0x%08x\",\n", OWNER);
    printf("  \"arm_rows_registered\": %d,\n", arm_rows);
    printf("  \"missing_block_policy\": \"%s\",\n",
           sa2_mb_policy_name(sa2_missing_block_dispatch));

    printf("  \"table_rows\": {\"before\": %u, \"after\": %u},\n", rows_before, sa2_ft_count());
    printf("  \"iat_slots\": [{\"slot\":\"0x%08x\",\"holds\":\"0x%08x\"},"
           "{\"slot\":\"0x%08x\",\"holds\":\"0x%08x\"},"
           "{\"slot\":\"0x%08x\",\"holds\":\"0x%08x\"}],\n",
           IAT_A, ia, IAT_B, ib, IAT_C, ic);
    printf("  \"runs\": [\n");

    int all_ok = 1, reached = 0, returned = 0, witness_right = 0;
    const int nruns = on;    /* one per oracle row; the last is the `ja` default */
    for (int r = 0; r < nruns; r++) {
        const int is_default = (osel[r] == SEL_DEFAULT_ROW);
        g_run = (uint32_t)r;
        g_selector = is_default ? 0x2au : osel[r];   /* > 5 takes the `ja` at 0x0040163e */
        const uint32_t want_arm = oarm[r];
        const uint32_t want_ebx = oebx[r];
        g_witness_hits = 0; g_witness_ebx = g_witness_ecx = g_witness_esp = 0;
        g_pre_a_hits = g_pre_b_hits = 0;
        g_witness_thunk_entered = g_selector_thunk_entered = 0;
        g_iat_hits[0] = g_iat_hits[1] = g_iat_hits[2] = 0;
        sa2_ft_misses();

        /* The arm this selector must reach, read where the guest reads it. */
        const uint32_t table_says = is_default ? want_arm
                                              : peek32(JUMP_TABLE + 4u * g_selector);

        sa2_cpu_state_t st;
        sa2_cpu_state_reset(&st);
        st.gpr[SA2_ESP] = STACK_TOP;
        st.gpr[SA2_EDI] = SEED_EDI;
        st.gpr[SA2_EBX] = 0xbbbbbbbbu;        /* poison: the arm must overwrite it */
        st.gpr[SA2_ESI] = 0x55555555u;
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);

        uint8_t *snapshot = (uint8_t *)malloc(sa2_guest.size);
        if (snapshot) memcpy(snapshot, sa2_guest.base, sa2_guest.size);

        sa2_call_result_t res;
        const int rc = sa2_call(OWNER, &st, 10, &res);

        range_t rngs[MAX_RANGES];
        uint32_t nrng = 0;
        if (snapshot)
            nrng = diff_ranges(snapshot, sa2_guest.base, sa2_guest.size, rngs, MAX_RANGES);

        /* EBX names the arm. Prefer the value the witness stub saw; when the
         * run stopped inside the real FUN_004026e0 instead, the state sa2_call
         * hands back still carries it, because nothing between 0x004026e0 and
         * its `FS:[0]` read writes EBX. Either way the value can only be one
         * of the six arm immediates if that arm executed: the run starts with
         * EBX poisoned and the six immediates are distinct. */
        const int got_witness = g_witness_hits > 0;
        const uint32_t ebx_evidence = got_witness ? g_witness_ebx : st.gpr[SA2_EBX];
        const int reached_arm = (ebx_evidence == want_ebx);
        const int ebx_ok = reached_arm;
        const int ret_ok = (res.status == SA2_TRAP_NONE) &&
                           (st.gpr[SA2_EIP] == SA2_CALL_SENTINEL);
        const int eax_ok = ret_ok && st.gpr[SA2_EAX] == (WITNESS_EAX | (uint32_t)r);
        const int esp_ok = ret_ok && st.gpr[SA2_ESP] == STACK_TOP + 4u;
        const int ok = got_witness && ebx_ok && ret_ok && eax_ok && esp_ok;
        reached += reached_arm; returned += ret_ok; witness_right += ebx_ok;
        all_ok &= ok;

        char note[SA2_TRAP_NOTE_MAX * 2];
        json_escape(res.note, note, sizeof note);
        printf("    {\"selector\": %u, \"default_arm\": %s, \"expected_arm\": \"0x%08x\", "
               "\"jump_table_says\": \"0x%08x\", \"expected_ebx_at_witness\": \"0x%08x\", "
               "\"rc\": %d, \"status\": %u, \"exit_pc\": \"0x%08x\", \"note\": \"%s\", "
               "\"reached_witness_stub\": %s, \"ebx_evidence\": \"0x%08x\", "
               "\"ebx_at_witness\": \"0x%08x\", "
               "\"ecx_at_witness\": \"0x%08x\", \"esp_at_witness\": \"0x%08x\", "
               "\"arm_identified\": %s, \"returned_to_sentinel\": %s, "
               "\"final\": {\"eip\": \"0x%08x\", \"eax\": \"0x%08x\", \"ebx\": \"0x%08x\", "
               "\"ecx\": \"0x%08x\", \"edx\": \"0x%08x\", \"esi\": \"0x%08x\", "
               "\"edi\": \"0x%08x\", \"esp\": \"0x%08x\", \"ebp\": \"0x%08x\"}, "
               "\"eax_is_witness_return\": %s, \"esp_restored\": %s, "
               "\"stubs\": {\"pre_a\": %d, \"pre_b\": %d, \"show_cursor\": %d, "
               "\"get_window_long\": %d, \"show_window\": %d}, "
               "\"dispatch_misses\": %u, \"witness_via_weak_thunk\": %d, "
               "\"selector_via_weak_thunk\": %d, \"ok\": %s, ",
               g_selector, is_default ? "true" : "false", want_arm, table_says, want_ebx,
               rc, res.status, res.exit_pc, note,
               got_witness ? "true" : "false", ebx_evidence,
               g_witness_ebx, g_witness_ecx, g_witness_esp,
               ebx_ok ? "true" : "false", ret_ok ? "true" : "false",
               st.gpr[SA2_EIP], st.gpr[SA2_EAX], st.gpr[SA2_EBX], st.gpr[SA2_ECX],
               st.gpr[SA2_EDX], st.gpr[SA2_ESI], st.gpr[SA2_EDI], st.gpr[SA2_ESP],
               st.gpr[SA2_EBP],
               eax_ok ? "true" : "false", esp_ok ? "true" : "false",
               g_pre_a_hits, g_pre_b_hits, g_iat_hits[0], g_iat_hits[1], g_iat_hits[2],
               sa2_ft_misses(), g_witness_thunk_entered, g_selector_thunk_entered,
               ok ? "true" : "false");
        printf("\"guest_memory_written\": {\"ranges\": %u, \"where\": [", nrng);
        for (uint32_t k = 0; k < nrng && k < MAX_RANGES; k++)
            printf("%s{\"from\": \"0x%08x\", \"to\": \"0x%08x\", \"bytes\": %u, "
                   "\"region\": \"%s\"}", k ? ", " : "", rngs[k].lo, rngs[k].hi,
                   rngs[k].hi - rngs[k].lo, sa2_guest_region_of(rngs[k].lo));
        printf("]}}%s\n", r + 1 < nruns ? "," : "");
        free(snapshot);
        fflush(stdout);
    }
    printf("  ],\n");
    printf("  \"summary\": {\"runs\": %d, \"reached_an_arm\": %d, "
           "\"arm_identified_by_the_oracle\": %d, \"returned_to_sentinel\": %d, "
           "\"all_ok\": %s}\n", nruns, reached, witness_right, returned,
           all_ok ? "true" : "false");
    printf("}\n");
    sa2_runtime_shutdown();
    return all_ok ? 0 : 1;
}
