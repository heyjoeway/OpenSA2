/* Does the lifted image execute a switch? Answered by the bytes it copies.
 *
 *   usage: memmove_probe <sonic2app-unpacked.exe> [--va 0x007a62f0]
 *
 * S3 slice 5 drove the first real guest function, `FUN_00406520`, and it
 * stopped at `SA2_TRAP_CONTROL_LEFT` at `0x007a645b`:
 *
 *     007a645b  ff249564647a00   jmp dword ptr [edx*4+7A6464h]
 *
 * a four-armed jump table inside the CRT's `_memmove` (`0x007a62f0`, the
 * analyzer's name, and what `memcpy` tail-calls). That is the whole image's
 * largest open problem in one instruction: remill's recursive descent stops at
 * an indirect branch, so the arms are in no module, the function table has no
 * row for them, and the dispatch misses.
 *
 * "It got further" is easy to fake and a program counter proves nothing, so
 * this probe does not look at where the guest stopped. It calls `_memmove`
 * itself -- cdecl, `dst`, `src`, `n` on the guest stack -- and then reads the
 * destination back out of guest memory and compares it with what memmove is
 * defined to produce. The arms of that table are precisely the tail-byte cases
 * (`edx = n & 3` at `0x007a6342`), so a copy whose length is not a multiple of
 * four cannot be correct unless the indirect branch dispatched and the arm ran.
 * The evidence is the copied bytes, which only the guest could have written,
 * and the oracle is memmove's own definition, computed here.
 *
 * Every case is run twice over: once as a plain copy and once with the two
 * ranges overlapping, because the overlap takes the backward path at
 * `0x007a64b4` and its own table. Lengths straddle every interesting boundary:
 * 0, the four residues of 4, the `cmp ecx, 8` at `0x007a6345`, and the
 * `cmp ecx, 100h` at `0x007a6310` that chooses the SSE2 path.
 *
 * The source pattern is a length-seeded LCG, so a destination that happens to
 * hold the right bytes already cannot pass: the destination is filled with
 * `0xCC` first and the expected bytes differ per case. A case where the guest
 * wrote nothing therefore fails, which is what the arms-free binary does --
 * run this against `private/generated/s3/link/next/sa2_whole`'s object set and
 * every case traps at the jump table with 0 bytes correct.
 *
 * Output is one JSON document on stdout; exit status is 0 only if every case
 * copied exactly the right bytes, returned through its own `ret`, returned
 * `dst` in EAX, and left ESP where it found it.
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

#define DEFAULT_VA   0x007a62f0u     /* _memmove, per private/generated/function-map.json */
#define SRC_BASE     0x03010000u     /* scratch: SA2_GUEST_SCRATCH_BASE .. _END */
#define DST_BASE     0x03020000u
#define OVERLAP_BASE 0x03030000u
#define FRAME_ESP    0x03eeff00u     /* stack arena; the frame is [esp .. esp+0x10) */
#define MAX_N        1024u

static const uint32_t kLengths[] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 15, 16, 17, 31, 32, 33,
    63, 64, 65, 127, 255, 256, 257, 511, 1023,
};
#define NLEN ((int)(sizeof kLengths / sizeof kLengths[0]))

/* A length-seeded LCG, so no two cases expect the same bytes and a stale
 * destination cannot pass for a fresh copy. */
static void pattern(uint8_t *out, uint32_t n, uint32_t seed) {
    uint32_t x = seed * 2654435761u + 0x9e3779b9u;
    for (uint32_t i = 0; i < n; i++) {
        x = x * 1103515245u + 12345u;
        out[i] = (uint8_t)(x >> 16);
    }
}

typedef struct {
    uint32_t n, dst, src, eax, esp_after, status, exit_pc;
    uint32_t bytes_right, first_wrong;
    int overlapping, returned, eax_ok, esp_ok, bytes_ok, ok;
    char note[SA2_TRAP_NOTE_MAX];
} case_result;

static void jstr(const char *value) {
    putchar('"');
    for (const char *p = value; *p; p++) {
        if (*p == '"' || *p == '\\') putchar('\\');
        if (*p == '\n') { fputs("\\n", stdout); continue; }
        putchar(*p);
    }
    putchar('"');
}

static int one_case(uint32_t va, uint32_t n, int overlapping, case_result *out) {
    static uint8_t src[MAX_N], want[MAX_N], got[MAX_N];
    memset(out, 0, sizeof *out);
    out->n = n;
    out->overlapping = overlapping;

    /* Overlapping: dst is 3 bytes above src inside one buffer, which is the
     * case `_memmove` exists for and which takes the backward path. */
    const uint32_t src_va = overlapping ? OVERLAP_BASE : SRC_BASE;
    const uint32_t dst_va = overlapping ? OVERLAP_BASE + 3u : DST_BASE;
    out->src = src_va;
    out->dst = dst_va;

    pattern(src, n, n * 2u + (uint32_t)overlapping);
    if (overlapping) {
        /* One buffer: write the source, poison what is beyond it, and compute
         * the answer with the host's own memmove. */
        static uint8_t buf[MAX_N + 16];
        memset(buf, 0xCC, sizeof buf);
        memcpy(buf, src, n);
        if (sa2_poke(src_va, (uint32_t)sizeof buf, buf) != SA2_OK) return -1;
        memmove(buf + 3, buf, n);
        memcpy(want, buf + 3, n);
    } else {
        static uint8_t fill[MAX_N];
        memset(fill, 0xCC, sizeof fill);
        if (sa2_poke(src_va, n ? n : 1u, n ? src : fill) != SA2_OK) return -1;
        if (sa2_poke(dst_va, MAX_N, fill) != SA2_OK) return -1;
        memcpy(want, src, n);
    }

    /* cdecl frame: [esp] return address, [esp+4] dst, [esp+8] src, [esp+0xC] n. */
    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    st.gpr[SA2_ESP] = FRAME_ESP;
    sa2_poke(FRAME_ESP + 4u, 4, &dst_va);
    sa2_poke(FRAME_ESP + 8u, 4, &src_va);
    sa2_poke(FRAME_ESP + 12u, 4, &n);
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);

    sa2_call_result_t res;
    sa2_call(va, &st, 20, &res);

    out->status = res.status;
    out->exit_pc = res.exit_pc;
    snprintf(out->note, sizeof out->note, "%s", res.note);
    out->eax = st.gpr[SA2_EAX];
    out->esp_after = st.gpr[SA2_ESP];
    out->returned = (res.status == SA2_TRAP_NONE) && (st.gpr[SA2_EIP] == SA2_CALL_SENTINEL);
    out->eax_ok = out->returned && st.gpr[SA2_EAX] == dst_va;
    /* cdecl: the callee does not pop the arguments, so ESP comes back at the
     * slot above the return address. */
    out->esp_ok = out->returned && st.gpr[SA2_ESP] == FRAME_ESP + 4u;

    memset(got, 0, sizeof got);
    if (n && sa2_peek(dst_va, n, got) != SA2_OK) return -1;
    out->first_wrong = 0xffffffffu;
    for (uint32_t i = 0; i < n; i++) {
        if (got[i] == want[i]) out->bytes_right++;
        else if (out->first_wrong == 0xffffffffu) out->first_wrong = i;
    }
    out->bytes_ok = (out->bytes_right == n);
    out->ok = out->returned && out->eax_ok && out->esp_ok && out->bytes_ok;
    return 0;
}

int main(int argc, char **argv) {
    const char *pe = NULL;
    uint32_t va = DEFAULT_VA;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--va") && i + 1 < argc) va = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!pe) pe = argv[i];
    }
    if (!pe) { fprintf(stderr, "usage: memmove_probe <sonic2app-unpacked.exe> [--va VA]\n"); return 2; }

    if (sa2_runtime_init(pe, 16) != SA2_OK) {
        fprintf(stderr, "memmove_probe: sa2_runtime_init(%s) failed\n", pe);
        return 2;
    }
    /* Nothing is stubbed and nothing is registered. `_memmove` calls no import
     * and no callee on any path this probe drives, so every instruction between
     * the entry and the `ret` is lifted guest code; the only runtime service it
     * uses is the function table, for the indirect branch this probe is about. */
    const uint32_t rows = sa2_ft_count();
    const uint32_t entry_resolves = sa2_ft_lookup(va) != NULL;

    printf("{\n");
    printf("  \"va\": \"0x%08x\",\n", va);
    printf("  \"table_rows\": %u,\n", rows);
    printf("  \"entry_resolves\": %s,\n", entry_resolves ? "true" : "false");
    printf("  \"oracle\": \"the host's own memmove over the same bytes; the source is a "
           "length-seeded LCG and the destination is filled with 0xCC first, so a case "
           "the guest did not write cannot pass\",\n");
    printf("  \"cases\": [\n");

    int total = 0, ok = 0, copied = 0, returned = 0;
    uint64_t bytes_right = 0, bytes_asked = 0;
    uint32_t first_stop_pc = 0;
    char first_stop_note[SA2_TRAP_NOTE_MAX] = {0};
    for (int overlapping = 0; overlapping < 2; overlapping++) {
        for (int i = 0; i < NLEN; i++) {
            case_result r;
            if (one_case(va, kLengths[i], overlapping, &r) != 0) continue;
            total++;
            ok += r.ok;
            copied += r.bytes_ok;
            returned += r.returned;
            bytes_right += r.bytes_right;
            bytes_asked += r.n;
            if (!r.ok && !first_stop_pc && r.status) {
                first_stop_pc = r.exit_pc;
                snprintf(first_stop_note, sizeof first_stop_note, "%s", r.note);
            }
            printf("    {\"n\": %u, \"overlapping\": %s, \"dst\": \"0x%08x\", "
                   "\"src\": \"0x%08x\", \"status\": %u, \"exit_pc\": \"0x%08x\", "
                   "\"note\": ", r.n, r.overlapping ? "true" : "false", r.dst, r.src,
                   r.status, r.exit_pc);
            jstr(r.note);
            printf(", \"returned_to_sentinel\": %s, \"eax\": \"0x%08x\", "
                   "\"eax_is_dst\": %s, \"esp_after\": \"0x%08x\", \"esp_ok\": %s, "
                   "\"bytes_right\": %u, \"first_wrong_byte\": %d, \"bytes_ok\": %s, "
                   "\"ok\": %s}%s\n",
                   r.returned ? "true" : "false", r.eax, r.eax_ok ? "true" : "false",
                   r.esp_after, r.esp_ok ? "true" : "false", r.bytes_right,
                   r.first_wrong == 0xffffffffu ? -1 : (int)r.first_wrong,
                   r.bytes_ok ? "true" : "false", r.ok ? "true" : "false",
                   (overlapping == 1 && i == NLEN - 1) ? "" : ",");
            fflush(stdout);
        }
    }
    printf("  ],\n");
    printf("  \"summary\": {\"cases\": %d, \"returned_through_ret\": %d, "
           "\"copied_exactly\": %d, \"all_checks\": %d, \"bytes_asked\": %llu, "
           "\"bytes_correct\": %llu, \"dispatch_misses\": %u, "
           "\"first_stop\": {\"exit_pc\": \"0x%08x\", \"note\": ",
           total, returned, copied, ok, (unsigned long long)bytes_asked,
           (unsigned long long)bytes_right, sa2_ft_misses(), first_stop_pc);
    jstr(first_stop_note);
    printf("}},\n");
    printf("  \"ok\": %s\n}\n", (total && ok == total) ? "true" : "false");

    sa2_runtime_shutdown();
    return (total && ok == total) ? 0 : 1;
}
