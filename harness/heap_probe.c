/* Does the guest heap behave like a heap?
 *
 *   usage: heap_probe <sonic2app-unpacked.exe> [--json PATH]
 *
 * The entry-point run reaches WinMain with 20 HeapAllocs, 1 HeapFree and no
 * HeapReAlloc at all, so the allocator's split, coalesce and reallocate paths
 * are carried by that run without being exercised by it. An allocator nobody
 * tested is the kind of defect that surfaces four stages later as memory
 * corruption with no address attached to it.
 *
 * So this drives the heap through *exactly the path the guest uses*: the bound
 * IAT thunks. `sa2_call` on a thunk address dispatches into `import_native`,
 * which reads the arguments off the guest stack and applies the stdcall
 * cleanup, so every call here goes through the same argument marshalling, the
 * same guest-memory headers and the same refusals a lifted `call [slot]` does.
 * Nothing calls a C function directly.
 *
 * What it asserts, and why each one is here
 * -----------------------------------------
 *  * allocations are 8-aligned, inside the arena, and do not overlap -- the
 *    three things a boundary-tag allocator gets wrong when the split
 *    arithmetic is off by a header;
 *  * a block keeps what was written into it across other allocations;
 *  * HeapSize returns the size that was *asked for*, not the rounded one;
 *  * HeapReAlloc preserves contents when it grows in place and when it moves;
 *  * freeing everything and allocating the whole arena again succeeds, which
 *    is the only way to see that coalescing actually happened: without it the
 *    arena is left as a row of holes and the large request fails;
 *  * a double free and a free on the wrong heap *trap* rather than working.
 *    A heap that tolerates a bad free turns one defect into a corruption
 *    somewhere else, which is the failure this project can least afford.
 *
 * Every number is written to JSON, including a failing run's.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_trap.h"

#define STACK_TOP 0x03eeff00u
#define HEAP_ZERO 0x00000008u

static int g_fail;
static char g_first_fail[256];

static void check(int cond, const char *fmt, ...) {
    if (cond) return;
    g_fail++;
    if (!g_first_fail[0]) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(g_first_fail, sizeof g_first_fail, fmt, ap);
        va_end(ap);
    }
}

static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}

/* Call an import through its thunk, the way lifted code does. Returns EAX;
 * `status` receives the trap status, so a call that is *meant* to refuse can be
 * asserted on instead of ending the probe. */
static uint32_t call_import(const char *name, uint32_t argc, const uint32_t *argv,
                            uint32_t *status, const char **note) {
    const sa2_import_info *in = sa2_import_by_name("KERNEL32.dll", name);
    if (!in) { *status = 0xffffffffu; *note = "no such import"; return 0; }
    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    st.gpr[SA2_ESP] = STACK_TOP - 4u * (argc + 1u);
    for (uint32_t i = 0; i < argc; i++)
        sa2_poke(st.gpr[SA2_ESP] + 4u + 4u * i, 4, &argv[i]);
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    sa2_call_result_t res;
    sa2_call(in->thunk, &st, 30, &res);
    /* An import native returns by handing `memory` back to the lifted caller; it
     * never writes EIP, because on the real path `__remill_function_call` is what
     * resumes the caller. Driven straight from `sa2_call` there is no lifted
     * caller, so a *successful* call comes back as SA2_TRAP_CONTROL_LEFT at the
     * thunk's own address. That is the normal return here, and it is distinct
     * from a refusal, which is SA2_TRAP_FAULT at the offending pointer. */
    *status = (res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == in->thunk)
              ? SA2_TRAP_NONE : res.status;
    static char keep[SA2_TRAP_NOTE_MAX];
    snprintf(keep, sizeof keep, "%s", res.note);
    *note = keep;
    return st.gpr[SA2_EAX];
}

static uint32_t st_ignored;
static const char *note_ignored;
#define CALL(name, n, ...) \
    call_import(name, n, (const uint32_t[]){__VA_ARGS__}, &st_ignored, &note_ignored)

static void jstr(FILE *f, const char *s) {
    fputc('"', f);
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c < 0x20 || c >= 0x7F) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}

#define N 64

int main(int argc, char **argv) {
    const char *pe = NULL, *json_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!pe) pe = argv[i];
    }
    if (!pe) { fprintf(stderr, "usage: heap_probe <exe> [--json PATH]\n"); return 2; }
    if (sa2_runtime_init(pe, 512) != SA2_OK) { fprintf(stderr, "runtime init failed\n"); return 2; }
    sa2_imports_config_from_env();
    if (sa2_imports_bind() <= 0) { fprintf(stderr, "imports did not bind\n"); return 2; }

    uint32_t status; const char *note;
    const uint32_t heap = call_import("HeapCreate", 3, (const uint32_t[]){0, 0x1000, 0},
                                      &status, &note);
    check(heap != 0 && status == SA2_TRAP_NONE, "HeapCreate returned %#x status %u", heap, status);

    sa2_heap_stats_t s0;
    sa2_heap_stats(&s0);

    /* 1. a spread of sizes, each written full of a distinct byte */
    uint32_t p[N], sz[N];
    for (uint32_t i = 0; i < N; i++) {
        sz[i] = 1u + i * 37u;
        p[i] = CALL("HeapAlloc", 3, heap, 0, sz[i]);
        check(p[i] != 0, "HeapAlloc(%u) returned 0", sz[i]);
        check((p[i] & 7u) == 0, "HeapAlloc(%u) returned %#010x, not 8-aligned", sz[i], p[i]);
        check(p[i] >= s0.arena_base && p[i] + sz[i] <= s0.arena_end,
              "HeapAlloc(%u) returned %#010x, outside the arena", sz[i], p[i]);
        uint8_t *host = sa2_gp(p[i], sz[i]);
        check(host != NULL, "block %u is not addressable", i);
        if (host) memset(host, (int)(i & 0xff), sz[i]);
    }
    /* no two live blocks overlap */
    for (uint32_t i = 0; i < N; i++)
        for (uint32_t k = 0; k < i; k++)
            check(p[i] + sz[i] <= p[k] || p[k] + sz[k] <= p[i],
                  "blocks %u and %u overlap: %#010x+%u and %#010x+%u", i, k,
                  p[i], sz[i], p[k], sz[k]);
    /* every block still holds what was written into it */
    for (uint32_t i = 0; i < N; i++) {
        uint8_t *host = sa2_gp(p[i], sz[i]);
        int ok = host != NULL;
        for (uint32_t b = 0; ok && b < sz[i]; b++) ok = host[b] == (uint8_t)(i & 0xff);
        check(ok, "block %u lost its contents", i);
    }
    /* HeapSize answers what was asked for */
    for (uint32_t i = 0; i < N; i++) {
        uint32_t got = CALL("HeapSize", 3, heap, 0, p[i]);
        check(got == sz[i], "HeapSize(block %u) = %u, asked for %u", i, got, sz[i]);
    }

    /* 2. HEAP_ZERO_MEMORY really zeroes */
    uint32_t z = CALL("HeapAlloc", 3, heap, HEAP_ZERO, 4096u);
    int zeroed = z != 0;
    for (uint32_t b = 0; zeroed && b < 4096u; b++) zeroed = sa2_gp(z, 1) && sa2_gp(z + b, 1)[0] == 0;
    check(zeroed, "HEAP_ZERO_MEMORY left non-zero bytes at %#010x", z);

    /* 3. HeapReAlloc: grow (in place or moved), shrink, and from NULL */
    uint32_t r = CALL("HeapAlloc", 3, heap, 0, 100u);
    memset(sa2_gp(r, 100), 0xA5, 100);
    uint32_t r2 = CALL("HeapReAlloc", 4, heap, 0, r, 4000u);
    check(r2 != 0, "HeapReAlloc grow returned 0");
    int kept = r2 != 0;
    for (uint32_t b = 0; kept && b < 100u; b++) kept = sa2_gp(r2 + b, 1)[0] == 0xA5;
    check(kept, "HeapReAlloc grow lost the first 100 bytes");
    check(CALL("HeapSize", 3, heap, 0, r2) == 4000u, "HeapSize after grow is wrong");
    uint32_t r3 = CALL("HeapReAlloc", 4, heap, 0, r2, 8u);
    check(r3 != 0 && CALL("HeapSize", 3, heap, 0, r3) == 8u, "HeapReAlloc shrink is wrong");
    uint32_t r4 = CALL("HeapReAlloc", 4, heap, 0, 0, 64u);
    check(r4 != 0 && CALL("HeapSize", 3, heap, 0, r4) == 64u,
          "HeapReAlloc from NULL did not allocate");

    /* 4. free everything, then ask for nearly the whole arena. Without
     *    coalescing this fails: the arena is a row of 64 holes. */
    for (uint32_t i = 0; i < N; i++) check(CALL("HeapFree", 3, heap, 0, p[i]) == 1,
                                           "HeapFree(block %u) failed", i);
    check(CALL("HeapFree", 3, heap, 0, z) == 1, "HeapFree(zeroed block) failed");
    check(CALL("HeapFree", 3, heap, 0, r3) == 1, "HeapFree(r3) failed");
    check(CALL("HeapFree", 3, heap, 0, r4) == 1, "HeapFree(r4) failed");
    check(CALL("HeapFree", 3, heap, 0, 0) == 1, "HeapFree(NULL) must succeed on Win32");

    sa2_heap_stats_t s1;
    sa2_heap_stats(&s1);
    check(s1.live_blocks == 0, "%u blocks still live after freeing them all", s1.live_blocks);
    check(s1.walk_inuse == 0, "the arena walk still sees %u in-use blocks", s1.walk_inuse);
    const uint32_t arena = s0.arena_end - s0.arena_base;
    check(s1.walk_blocks == 1,
          "the arena walk sees %u blocks after freeing everything; coalescing left holes",
          s1.walk_blocks);
    uint32_t big = CALL("HeapAlloc", 3, heap, 0, arena - 64u);
    check(big != 0, "allocating the whole arena (%u bytes) failed after freeing it", arena - 64u);
    if (big) check(CALL("HeapFree", 3, heap, 0, big) == 1, "freeing the big block failed");

    /* 5. the refusals. Each must trap, not return. */
    uint32_t q = CALL("HeapAlloc", 3, heap, 0, 32u);
    check(CALL("HeapFree", 3, heap, 0, q) == 1, "the first free of q failed");
    uint32_t st_double; const char *note_double;
    call_import("HeapFree", 3, (const uint32_t[]){heap, 0, q}, &st_double, &note_double);
    check(st_double != SA2_TRAP_NONE, "a double free returned instead of trapping");

    const uint32_t heap2 = call_import("HeapCreate", 3, (const uint32_t[]){0, 0x1000, 0},
                                       &status, &note);
    uint32_t w = CALL("HeapAlloc", 3, heap, 0, 32u);
    uint32_t st_wrong; const char *note_wrong;
    call_import("HeapFree", 3, (const uint32_t[]){heap2, 0, w}, &st_wrong, &note_wrong);
    check(st_wrong != SA2_TRAP_NONE, "freeing on the wrong heap returned instead of trapping");

    uint32_t st_bogus; const char *note_bogus;
    call_import("HeapFree", 3, (const uint32_t[]){heap, 0, 0x02900008u}, &st_bogus, &note_bogus);
    check(st_bogus != SA2_TRAP_NONE, "freeing a pointer the heap never returned did not trap");

    /* 6. an allocation larger than the arena fails rather than trapping */
    uint32_t st_big; const char *note_big;
    uint32_t huge = call_import("HeapAlloc", 3, (const uint32_t[]){heap, 0, 0xfffffff0u},
                                &st_big, &note_big);
    check(huge == 0 && st_big == SA2_TRAP_NONE,
          "a 0xfffffff0-byte request returned %#010x with status %u; Win32 returns NULL",
          huge, st_big);

    sa2_heap_stats_t s2;
    sa2_heap_stats(&s2);

    FILE *out = stdout;
    for (int pass = 0; pass < 2; pass++) {
        if (pass == 1) {
            if (!json_path) break;
            out = fopen(json_path, "w");
            if (!out) break;
        }
        fprintf(out, "{\n \"heap\": \"0x%08x\", \"arena\": [\"0x%08x\", \"0x%08x\"], "
                     "\"blocks_allocated\": %u,\n", heap, s0.arena_base, s0.arena_end, N);
        fprintf(out, " \"after_freeing_everything\": {\"live_blocks\": %u, \"walk_blocks\": %u, "
                     "\"walk_inuse\": %u, \"walk_largest_free\": %u},\n",
                s1.live_blocks, s1.walk_blocks, s1.walk_inuse, s1.walk_largest_free);
        fprintf(out, " \"whole_arena_realloc_after_coalescing\": \"0x%08x\",\n", big);
        fprintf(out, " \"refusals\": {\"double_free_status\": %u, \"double_free_note\": ",
                st_double);
        jstr(out, note_double);
        fprintf(out, ",\n   \"wrong_heap_status\": %u, \"wrong_heap_note\": ", st_wrong);
        jstr(out, note_wrong);
        fprintf(out, ",\n   \"never_allocated_status\": %u, \"never_allocated_note\": ", st_bogus);
        jstr(out, note_bogus);
        fprintf(out, "},\n");
        fprintf(out, " \"oversized_request\": {\"returned\": \"0x%08x\", \"status\": %u},\n",
                huge, st_big);
        fprintf(out, " \"final\": {\"allocs\": %u, \"frees\": %u, \"failures\": %u, "
                     "\"live_blocks\": %u, \"peak_bytes\": %u, \"walk_blocks\": %u},\n",
                s2.allocs, s2.frees, s2.failures, s2.live_blocks, s2.peak_bytes, s2.walk_blocks);
        fprintf(out, " \"failures\": %d, \"first_failure\": ", g_fail);
        jstr(out, g_first_fail);
        fprintf(out, ",\n \"ok\": %s\n}\n", g_fail == 0 ? "true" : "false");
        if (pass == 1) fclose(out);
    }
    sa2_runtime_shutdown();
    return g_fail == 0 ? 0 : 1;
}
