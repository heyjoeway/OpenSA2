/* Does the lifted image dispatch into an arm remill already decoded?
 *
 *   usage: switch_arm_probe <sonic2app-unpacked.exe> <switch_oracle.txt>
 *
 * 343 of the image's 5 715 jump-table arms had no `sub_<arm>` symbol: remill's
 * recursive descent reached them by direct flow from the owner's entry, so
 * their code was lifted into the owner's module as an unnamed basic block and
 * slice 4 gave them no trace of their own. A block is not a symbol, the
 * function table had no row, and an indirect jump to one missed.
 *
 * This drives two call-free guest functions whose jump tables select such an
 * arm, and checks the value each returns against
 * `tools/s3/arms/switch_oracle.py` -- a 32-bit x86 interpreter over the shipped
 * executable that shares nothing with remill. The observable is EAX, which only
 * the guest code could have computed:
 *
 *     00439570  inc eax / cmp eax,7 / ja 439592 / jmp dword [eax*4+439598h]
 *               arms return -1, 0, 2, 3 and 5; slot 0 is 0x00439592, one of
 *               the 343, and EAX = 0xFFFFFFFF selects it
 *     004529c0  a global selects between 0x004529D8 (returns 0) and
 *               0x004529DB (returns 1); 0x004529DB is one of the 343, and the
 *               odd selectors 1, 3 and 5 dispatch into it
 *
 * Three populations of case, and the point is that they answer differently:
 *
 *   through_table into a direct-flow arm   the 343. Misses before the fix.
 *   through_table into a seeded arm        already worked; the positive
 *                                          control, in the same function, on
 *                                          the same instruction
 *   direct branch to the same arm          reaches the identical guest code
 *                                          without consulting the table, so it
 *                                          must answer in every binary. If one
 *                                          of these ever fails, the fix broke
 *                                          something rather than adding to it
 *
 * Run it against the oracle's `--negative-control` file to watch the comparison
 * fail: one expected value is flipped and the probe reports the mismatch.
 *
 * Output is one JSON document on stdout. Exit status is 0 only when every case
 * returned through its own `ret` with the oracle's value.
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

#define FRAME_ESP 0x03eeff00u
#define MAX_CASES 512

typedef struct {
    uint32_t va;
    int      poke;              /* 1: write `poke_value` to `poke_addr` first */
    uint32_t poke_addr, poke_value;
    uint32_t eax_in;
    uint32_t expect;
    uint32_t arm;
    int      have_arm;
    int      through_table;
    int      direct_flow_arm;   /* one of the arms with no sub_<arm> before the fix */
} case_t;

static case_t g_case[MAX_CASES];
static int g_ncases;

static void jstr(const char *value) {
    putchar('"');
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') putchar('\\');
        if (*p == '\n') { fputs("\\n", stdout); continue; }
        putchar(*p);
    }
    putchar('"');
}

/* One oracle line: "<va> <setup> <expect> <arm|-> <through> <direct_flow_arm>". */
static int parse(const char *line, case_t *out) {
    char setup[64] = "", arm[32] = "";
    unsigned va = 0, expect = 0, through = 0, dfarm = 0;
    if (sscanf(line, "%x %63s %x %31s %u %u",
               &va, setup, &expect, arm, &through, &dfarm) != 6)
        return 0;
    memset(out, 0, sizeof *out);
    out->va = va;
    out->expect = expect;
    out->through_table = (int)through;
    out->direct_flow_arm = (int)dfarm;
    if (strcmp(arm, "-")) {
        out->arm = (uint32_t)strtoul(arm, NULL, 0);
        out->have_arm = 1;
    }
    if (!strncmp(setup, "eax:", 4)) {
        out->eax_in = (uint32_t)strtoul(setup + 4, NULL, 0);
    } else if (!strncmp(setup, "mem:", 4)) {
        char *end = NULL;
        out->poke_addr = (uint32_t)strtoul(setup + 4, &end, 0);
        if (!end || *end != ':') return 0;
        out->poke_value = (uint32_t)strtoul(end + 1, NULL, 0);
        out->poke = 1;
    } else {
        return 0;
    }
    return 1;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: switch_arm_probe <exe> <switch_oracle.txt>\n");
        return 2;
    }
    if (sa2_runtime_init(argv[1], 16) != SA2_OK) {
        fprintf(stderr, "switch_arm_probe: sa2_runtime_init(%s) failed\n", argv[1]);
        return 2;
    }
    FILE *f = fopen(argv[2], "r");
    if (!f) { fprintf(stderr, "switch_arm_probe: cannot open %s\n", argv[2]); return 2; }
    char line[512];
    while (fgets(line, sizeof line, f) && g_ncases < MAX_CASES) {
        if (line[0] == '#' || line[0] == '\n') continue;
        if (parse(line, &g_case[g_ncases])) g_ncases++;
    }
    fclose(f);
    if (!g_ncases) { fprintf(stderr, "switch_arm_probe: no cases in %s\n", argv[2]); return 2; }

    printf("{\n");
    printf("  \"oracle_file\": ");
    jstr(argv[2]);
    printf(",\n  \"table_rows\": %u,\n", sa2_ft_count());
    printf("  \"observable\": \"the EAX the guest function returns, against an "
           "interpreter over the shipped executable; a program counter is never "
           "compared\",\n");
    printf("  \"cases\": [\n");

    int total = 0, ok = 0, returned = 0;
    int tbl_direct_flow = 0, tbl_direct_flow_ok = 0;
    int tbl_seeded = 0, tbl_seeded_ok = 0;
    int no_table = 0, no_table_ok = 0;
    uint32_t first_stop_pc = 0, first_stop_status = 0;
    char first_stop_note[SA2_TRAP_NOTE_MAX] = {0};
    const uint32_t misses_before = sa2_ft_misses();

    for (int i = 0; i < g_ncases; i++) {
        const case_t *c = &g_case[i];
        /* Each case is independent: the arm's row is asked for fresh and the
         * selector is written fresh, so an earlier case cannot leave the answer
         * lying around. */
        if (c->poke && sa2_poke(c->poke_addr, 4, &c->poke_value) != SA2_OK) {
            fprintf(stderr, "switch_arm_probe: poke 0x%08x failed\n", c->poke_addr);
            return 2;
        }
        const int arm_resolves = c->have_arm && sa2_ft_lookup(c->arm) != NULL;

        sa2_cpu_state_t st;
        sa2_cpu_state_reset(&st);
        st.gpr[SA2_ESP] = FRAME_ESP;
        st.gpr[SA2_EAX] = c->eax_in;
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);

        sa2_call_result_t res;
        sa2_call(c->va, &st, 20, &res);

        const int ret_ok = res.status == SA2_TRAP_NONE &&
                           st.gpr[SA2_EIP] == SA2_CALL_SENTINEL;
        const int value_ok = ret_ok && st.gpr[SA2_EAX] == c->expect;
        /* cdecl-ish: these take their argument in a register, so ESP must come
         * back one slot above the return address and nowhere else. */
        const int esp_ok = ret_ok && st.gpr[SA2_ESP] == FRAME_ESP + 4u;
        const int case_ok = value_ok && esp_ok;

        total++;
        ok += case_ok;
        returned += ret_ok;
        /* Grouped by where the address came from, not by what the table
         * answers: a group defined by `arm_resolves` would empty itself the
         * moment the fix landed and could never be compared across binaries. */
        if (!c->through_table)       { no_table++; no_table_ok += case_ok; }
        else if (c->direct_flow_arm) { tbl_direct_flow++; tbl_direct_flow_ok += case_ok; }
        else                         { tbl_seeded++; tbl_seeded_ok += case_ok; }
        if (!case_ok && !first_stop_pc && res.status) {
            first_stop_pc = res.exit_pc;
            first_stop_status = res.status;
            snprintf(first_stop_note, sizeof first_stop_note, "%s", res.note);
        }

        printf("    {\"va\": \"0x%08x\", \"eax_in\": \"0x%08x\", ", c->va, c->eax_in);
        if (c->poke)
            printf("\"poked\": {\"addr\": \"0x%08x\", \"value\": \"0x%08x\"}, ",
                   c->poke_addr, c->poke_value);
        printf("\"arm\": \"0x%08x\", \"through_table\": %s, "
               "\"direct_flow_arm\": %s, \"arm_resolves\": %s, "
               "\"status\": %u, \"exit_pc\": \"0x%08x\", \"note\": ",
               c->arm, c->through_table ? "true" : "false",
               c->direct_flow_arm ? "true" : "false",
               arm_resolves ? "true" : "false", res.status, res.exit_pc);
        jstr(res.note);
        printf(", \"expect_eax\": \"0x%08x\", \"eax_out\": \"0x%08x\", "
               "\"returned\": %s, \"esp_ok\": %s, \"ok\": %s}%s\n",
               c->expect, st.gpr[SA2_EAX], ret_ok ? "true" : "false",
               esp_ok ? "true" : "false", case_ok ? "true" : "false",
               i + 1 < g_ncases ? "," : "");
        fflush(stdout);
    }

    printf("  ],\n");
    printf("  \"groups\": {\n");
    printf("    \"through_the_table_into_a_direct_flow_arm\": {\"cases\": %d, \"ok\": %d},\n",
           tbl_direct_flow, tbl_direct_flow_ok);
    printf("    \"through_the_table_into_an_arm_with_a_row\": {\"cases\": %d, \"ok\": %d},\n",
           tbl_seeded, tbl_seeded_ok);
    printf("    \"reached_by_a_direct_branch\": {\"cases\": %d, \"ok\": %d}\n",
           no_table, no_table_ok);
    printf("  },\n");
    printf("  \"summary\": {\"cases\": %d, \"returned_through_ret\": %d, "
           "\"matched_the_oracle\": %d, \"dispatch_misses\": %u, "
           "\"first_stop\": {\"status\": %u, \"exit_pc\": \"0x%08x\", \"note\": ",
           total, returned, ok, sa2_ft_misses() - misses_before,
           first_stop_status, first_stop_pc);
    jstr(first_stop_note);
    printf("}},\n");
    printf("  \"ok\": %s\n}\n", ok == total ? "true" : "false");

    sa2_runtime_shutdown();
    return ok == total ? 0 : 1;
}
