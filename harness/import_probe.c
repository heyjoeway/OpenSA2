/* Do the image's imports dispatch, and how far does the guest get once they do?
 *
 *   usage: import_probe <sonic2app-unpacked.exe> [--va 0x00406520]
 *                       [--suffix-va 0x0089E264] [--timeout S] [--json PATH]
 *
 * The wall S4 inherits is the import table. All 251 IAT slots hold the value
 * the file has -- the guest address of the import's name-table entry, or an
 * ordinal with bit 31 set -- so `call dword ptr [slot]` dispatches on a value
 * the function table has no row for, and the first guest function anybody
 * drives stops at 0x0051525a, which is the name of KERNEL32!GetCurrentDirectoryA
 * rather than any code. `runtime/sa2_imports.c` binds the slots the way a
 * loader does; this drives a guest function through the result.
 *
 * The evidence is the guest's own work, not a program counter
 * -----------------------------------------------------------
 * "It got further" is easy to fake, so the assertion is a string only guest
 * code could have built. `FUN_00406520` calls GetCurrentDirectoryA into a
 * stack buffer and then calls the CRT's concatenation helper at 0x007A6273 to
 * append the image's own literal at --suffix-va ("\Config\UserConfig.cfg"):
 *
 *     00406567  push ecx                  ; lpBuffer
 *     00406568  push 100h                 ; nBufferLength
 *     00406578  call dword ptr [8730BCh]  ; GetCurrentDirectoryA
 *     0040657e  push 89E264h              ; "\Config\UserConfig.cfg"
 *     0040658d  call 007A6273h            ; the concatenation
 *
 * The runtime writes only the directory. The suffix comes out of the guest
 * image and the concatenation is guest code, so the probe recomputes
 * `cwd || guest_string(suffix_va)` on the host and requires the buffer the
 * guest was handed to hold exactly that. A run that never called the import
 * has no buffer and fails; a run whose guest code did not execute the append
 * holds the directory alone and fails; and `SA2_GUEST_CWD` changes both sides,
 * so a probe that had hard-coded the answer fails too.
 *
 * `SA2_IMPORT_BIND=0` runs the same binary with the binding switched off,
 * which is the before column: the same executable, the same objects, one
 * environment variable, no rebuild.
 *
 * Output is one JSON document on stdout, and on --json also written to that
 * path, because a probe that does not write its numbers cannot be cited.
 * Exit status is 0 only when every assertion held.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_trap.h"

#define DEFAULT_VA        0x00406520u
#define DEFAULT_SUFFIX_VA 0x0089E264u
#define STACK_TOP         0x03eefffcu   /* the arm probe's convention */
#define STACK_WORDS       8

static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}

/* A guest byte string into `out`; 1 when it terminated inside the guest space. */
static int gstr(uint32_t a, char *out, size_t cap) {
    if (!a) { out[0] = 0; return 0; }
    for (size_t i = 0; i + 1 < cap; i++) {
        uint8_t b;
        if (sa2_peek(a + (uint32_t)i, 1, &b) != SA2_OK) { out[i] = 0; return 0; }
        out[i] = (char)b;
        if (!b) return 1;
    }
    out[cap - 1] = 0;
    return 0;
}

static void jstr(FILE *f, const char *s) {
    fputc('"', f);
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c == '\n') fputs("\\n", f);
        else if (c < 0x20 || c >= 0x7F) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}

int main(int argc, char **argv) {
    const char *pe = NULL, *json_path = NULL, *dump_path = NULL;
    uint32_t va = DEFAULT_VA, suffix_va = DEFAULT_SUFFIX_VA, timeout = 60;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--va") && i + 1 < argc) va = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--suffix-va") && i + 1 < argc) suffix_va = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--timeout") && i + 1 < argc) timeout = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!strcmp(argv[i], "--dump-imports") && i + 1 < argc) dump_path = argv[++i];
        else if (!pe) pe = argv[i];
    }
    if (!pe) { fprintf(stderr, "usage: import_probe <sonic2app-unpacked.exe> [--va VA]\n"); return 2; }

    /* 512 extra rows: 251 imports plus room, so a table that refuses a
     * registration says so rather than the run failing somewhere else. */
    if (sa2_runtime_init(pe, 512) != SA2_OK) {
        fprintf(stderr, "import_probe: sa2_runtime_init(%s) failed\n", pe);
        return 2;
    }
    const uint32_t rows_before = sa2_ft_count();

    sa2_imports_config_from_env();
    const int bind_rc = sa2_imports_bind();
    const uint32_t bound = sa2_import_count();
    const uint32_t rows_after = sa2_ft_count();

    /* Did the binding actually reach guest memory? Every slot must now read
     * back its own thunk, and no two thunks may be equal. */
    uint32_t slots_holding_thunk = 0, slots_still_unbound = 0, thunk_collisions = 0;
    uint32_t first_unbound_slot = 0;
    for (uint32_t i = 0; i < bound; i++) {
        const sa2_import_info *in = sa2_import_at(i);
        uint32_t live = peek32(in->slot);
        if (live == in->thunk) slots_holding_thunk++;
        else { slots_still_unbound++; if (!first_unbound_slot) first_unbound_slot = in->slot; }
        for (uint32_t k = 0; k < i; k++)
            if (sa2_import_at(k)->thunk == in->thunk) thunk_collisions++;
    }
    /* The measurement that decided the mechanism, recomputed here from the
     * image rather than quoted: how many *distinct* values did the 251 slots
     * hold before binding, and how many were not guest addresses at all? */
    uint32_t unbound_outside_guest = 0, unbound_duplicates = 0;
    for (uint32_t i = 0; i < bound; i++) {
        const sa2_import_info *a = sa2_import_at(i);
        if (a->unbound >= SA2_GUEST_END) unbound_outside_guest++;
        for (uint32_t k = 0; k < i; k++)
            if (sa2_import_at(k)->unbound == a->unbound) { unbound_duplicates++; break; }
    }

    char suffix[256];
    const int suffix_ok = gstr(suffix_va, suffix, sizeof suffix);

    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    st.gpr[SA2_ESP] = STACK_TOP;
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);

    sa2_call_result_t res;
    const int rc = sa2_call(va, &st, timeout, &res);

    /* Where it stopped, in the terms the next slice needs: if the stop is a
     * thunk, name the import; and show the guest words the call pushed. */
    const sa2_import_info *stop_import = sa2_import_for_thunk(res.exit_pc);
    uint32_t stop_stack[STACK_WORDS];
    for (int i = 0; i < STACK_WORDS; i++) stop_stack[i] = peek32(st.gpr[SA2_ESP] + 4u * (uint32_t)i);

    /* The oracle: the guest's own concatenation. */
    const sa2_import_call *cwd_call = NULL;
    for (uint32_t i = 0; i < sa2_import_calls_logged(); i++) {
        const sa2_import_call *c = sa2_import_call_at(i);
        const sa2_import_info *in = sa2_import_at(c->index);
        if (in && !strcmp(in->name, "GetCurrentDirectoryA")) { cwd_call = c; break; }
    }
    char expected[520], observed[520];
    observed[0] = 0;
    snprintf(expected, sizeof expected, "%s%s", sa2_import_cwd(), suffix_ok ? suffix : "");
    int buffer_read = 0;
    if (cwd_call) buffer_read = gstr(cwd_call->arg[1], observed, sizeof observed);

    const int imports_bound       = (bind_rc > 0) && (bound > 0);
    const int slots_all_bound     = imports_bound && (slots_still_unbound == 0) && (thunk_collisions == 0);
    const int import_was_called   = (cwd_call != NULL);
    const int guest_concatenated  = buffer_read && suffix_ok && (strcmp(observed, expected) == 0)
                                    && (strlen(observed) > strlen(sa2_import_cwd()));
    const int table_grew          = (rows_after == rows_before + bound);
    /* The run has to *end* somewhere it was meant to: through the entry's own
     * `ret`, or at an import -- implemented (ExitProcess) or not (the work
     * list). A fault, a timeout or a stop at an address that is neither is a
     * derailed run, and the assertions above cannot see one, because they all
     * read state the guest produced before it derailed.
     *
     * This exists because a deliberate mutation found the hole: setting
     * KERNEL32!Sleep's argument count to 0, so the stdcall cleanup leaks four
     * bytes per call, drove the guest's ESP to 0x0000d6ec and faulted on a read
     * of guest address 0 -- and every assertion above still held, so the probe
     * printed `"ok": true` over a wreck. */
    const int ended_where_intended = (res.status == SA2_TRAP_NONE)
                                  || (res.status == SA2_TRAP_CONTROL_LEFT && stop_import != NULL);
    const int ok = imports_bound && slots_all_bound && import_was_called
                   && guest_concatenated && table_grew && ended_where_intended;

    FILE *out = stdout;
    for (int pass = 0; pass < 2; pass++) {
        if (pass == 1) {
            if (!json_path) break;
            out = fopen(json_path, "w");
            if (!out) { fprintf(stderr, "import_probe: cannot write %s\n", json_path); break; }
        }
        fprintf(out, "{\n");
        fprintf(out, " \"va\": \"0x%08x\", \"rc\": %d, \"bind_enabled\": %s,\n",
                va, rc, sa2_imports_bound() ? "true" : "false");
        fprintf(out, " \"bind\": {\"rc\": %d, \"imports\": %u, \"table_rows_before\": %u, "
                     "\"table_rows_after\": %u, \"thunk_base\": \"0x%08x\", \"stride\": %u,\n",
                bind_rc, bound, rows_before, rows_after, SA2_GUEST_IMPORT_BASE, SA2_GUEST_IMPORT_STRIDE);
        fprintf(out, "   \"slots_holding_thunk\": %u, \"slots_still_unbound\": %u, "
                     "\"first_unbound_slot\": \"0x%08x\", \"thunk_collisions\": %u,\n",
                slots_holding_thunk, slots_still_unbound, first_unbound_slot, thunk_collisions);
        fprintf(out, "   \"unbound_values_outside_guest_space\": %u, "
                     "\"unbound_values_duplicated\": %u},\n",
                unbound_outside_guest, unbound_duplicates);
        fprintf(out, " \"virtual\": {\"cwd\": ");
        jstr(out, sa2_import_cwd());
        fprintf(out, ", \"exe_path\": ");
        jstr(out, sa2_import_exe_path());
        fprintf(out, ", \"sleep_ms\": %llu, \"last_error\": %u, "
                     "\"guest_called_exitprocess\": %s, \"exit_code\": %u},\n",
                (unsigned long long)sa2_import_virtual_ms(), sa2_import_last_error(),
                sa2_import_exited() ? "true" : "false", sa2_import_exit_code());
        fprintf(out, " \"trap\": {\"status\": %u, \"exit_pc\": \"0x%08x\", \"note\": ",
                res.status, res.exit_pc);
        jstr(out, res.note);
        fprintf(out, ",\n   \"import\": ");
        if (stop_import) {
            fprintf(out, "{\"dll\": ");
            jstr(out, stop_import->dll);
            fprintf(out, ", \"name\": ");
            jstr(out, stop_import->name);
            fprintf(out, ", \"slot\": \"0x%08x\", \"implemented\": %s}",
                    stop_import->slot, stop_import->implemented ? "true" : "false");
        } else {
            fprintf(out, "null");
        }
        fprintf(out, ",\n   \"esp\": \"0x%08x\", \"stack\": [", st.gpr[SA2_ESP]);
        for (int i = 0; i < STACK_WORDS; i++)
            fprintf(out, "%s\"0x%08x\"", i ? ", " : "", stop_stack[i]);
        fprintf(out, "]},\n");
        fprintf(out, " \"oracle\": {\"suffix_va\": \"0x%08x\", \"suffix\": ", suffix_va);
        jstr(out, suffix_ok ? suffix : "");
        fprintf(out, ",\n   \"buffer_va\": \"0x%08x\", \"expected\": ",
                cwd_call ? cwd_call->arg[1] : 0u);
        jstr(out, expected);
        fprintf(out, ", \"observed\": ");
        jstr(out, observed);
        fprintf(out, "},\n");
        fprintf(out, " \"calls\": {\"total\": %u, \"logged\": %u, \"records\": [\n",
                sa2_import_calls(), sa2_import_calls_logged());
        for (uint32_t i = 0; i < sa2_import_calls_logged(); i++) {
            const sa2_import_call *c = sa2_import_call_at(i);
            const sa2_import_info *in = sa2_import_at(c->index);
            fprintf(out, "   {\"seq\": %u, \"dll\": ", c->seq);
            jstr(out, in ? in->dll : "?");
            fprintf(out, ", \"name\": ");
            jstr(out, in ? in->name : "?");
            fprintf(out, ", \"implemented\": %s, \"caller\": \"0x%08x\", \"args\": [",
                    c->implemented ? "true" : "false", c->caller);
            for (uint32_t k = 0; k < c->argc; k++)
                fprintf(out, "%s\"0x%08x\"", k ? ", " : "", c->arg[k]);
            fprintf(out, "], \"ret\": \"0x%08x\"}%s\n",
                    c->ret, i + 1 < sa2_import_calls_logged() ? "," : "");
        }
        fprintf(out, " ]},\n");
        fprintf(out, " \"assert\": {\"imports_bound\": %s, \"every_slot_holds_its_thunk\": %s, "
                     "\"import_was_called\": %s, \"guest_concatenated_the_path\": %s, "
                     "\"table_grew_by_the_import_count\": %s, \"ended_where_intended\": %s},\n",
                imports_bound ? "true" : "false", slots_all_bound ? "true" : "false",
                import_was_called ? "true" : "false", guest_concatenated ? "true" : "false",
                table_grew ? "true" : "false", ended_where_intended ? "true" : "false");
        fprintf(out, " \"ok\": %s\n}\n", ok ? "true" : "false");
        if (pass == 1) fclose(out);
    }

    /* Every bound import, with whether a stub answers it. This is M4's
     * numerator and denominator read off the runtime that ships, rather than
     * grepped out of a table in a source file. */
    if (dump_path) {
        FILE *d = fopen(dump_path, "w");
        if (!d) {
            fprintf(stderr, "import_probe: cannot write %s\n", dump_path);
        } else {
            uint32_t impl = 0, called = 0;
            for (uint32_t i = 0; i < bound; i++) {
                impl += sa2_import_at(i)->implemented ? 1u : 0u;
                called += sa2_import_at(i)->calls ? 1u : 0u;
            }
            fprintf(d, "{\n \"imports\": %u, \"implemented\": %u, \"called_in_this_run\": %u,\n",
                    bound, impl, called);
            fprintf(d, " \"rows\": [\n");
            for (uint32_t i = 0; i < bound; i++) {
                const sa2_import_info *in = sa2_import_at(i);
                fprintf(d, "  {\"dll\": ");
                jstr(d, in->dll);
                fprintf(d, ", \"name\": ");
                jstr(d, in->name);
                fprintf(d, ", \"slot\": \"0x%08x\", \"unbound\": \"0x%08x\", "
                           "\"thunk\": \"0x%08x\", \"implemented\": %s, \"argc\": %u, "
                           "\"calls\": %u}%s\n",
                        in->slot, in->unbound, in->thunk,
                        in->implemented ? "true" : "false", in->argc, in->calls,
                        i + 1 < bound ? "," : "");
            }
            fprintf(d, " ]\n}\n");
            fclose(d);
        }
    }

    sa2_runtime_shutdown();
    return ok ? 0 : 1;
}
