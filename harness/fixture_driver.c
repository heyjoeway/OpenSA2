/* The runtime v0 fixture driver: read a vector file, run every row through
 * `sa2_call`, write the output file. This replaces the ad-hoc path in
 * `tools/spike/fixtures/run_fixtures.py`, which built one process per lifted
 * function around a stub runtime; here the rows go through the real guest
 * memory, the real function table and the real call entry.
 *
 *   usage: fixture_driver <sonic2app-unpacked.exe> <vectors.bin> <out.bin>
 *          fixture_driver <sonic2app-unpacked.exe> --selftest
 *          fixture_driver --intrinsics
 *
 * `--selftest` proves the parts the fixture rows do not reach: `sa2_peek`,
 * `sa2_poke` and the bounds checks, the guest section mapping, a function
 * table hit and a function table miss.
 * `--intrinsics` prints the runtime's intrinsic inventory as TSV and `--info`
 * the guest layout, the state size and the table shape, so the receipt's
 * inventory and layout are generated rather than typed.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fixture_shapes.h"

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_trap.h"

#define ROW_TIMEOUT_S 2u

typedef struct {
    uint32_t va, rows, in_words, out_words;
    uint32_t *in, *out;
} vectors_t;

static int vectors_read(const char *path, vectors_t *v) {
    FILE *f = fopen(path, "rb");
    if (!f) { perror(path); return -1; }
    char magic[8];
    uint32_t hdr[4];
    if (fread(magic, 1, 8, f) != 8 || memcmp(magic, "SA2FXV1\0", 8) != 0 || fread(hdr, 4, 4, f) != 4) {
        fprintf(stderr, "%s: bad vector header\n", path);
        fclose(f);
        return -1;
    }
    v->va = hdr[0]; v->rows = hdr[1]; v->in_words = hdr[2]; v->out_words = hdr[3];
    size_t n = (size_t)v->rows * v->in_words;
    v->in = (uint32_t *)calloc(n ? n : 1, 4);
    v->out = (uint32_t *)calloc((size_t)v->rows * (2 + v->out_words) + 1, 4);
    if (!v->in || !v->out || fread(v->in, 4, n, f) != n) {
        fprintf(stderr, "%s: short rows\n", path);
        fclose(f);
        return -1;
    }
    fclose(f);
    return 0;
}

static int vectors_write(const char *path, const vectors_t *v) {
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return -1; }
    uint32_t words = 2 + v->out_words;
    uint32_t hdr[3] = {v->va, v->rows, words};
    fwrite("SA2FXO1\0", 1, 8, f);
    fwrite(hdr, 4, 3, f);
    fwrite(v->out, 4, (size_t)v->rows * words, f);
    fclose(f);
    return 0;
}

static uint32_t float_word(sa2_f80 v) {
    float f = sa2_f80_to_f32(v);    /* fst m32 under the host's rounding mode, as (float) was */
    uint32_t w;
    memcpy(&w, &f, 4);
    return w;
}

static void print_intrinsics(void) {
    uint32_t n = 0;
    const sa2_intrinsic_info *xs = sa2_rt_intrinsics(&n);
    printf("name\tstatus\tnote\n");
    for (uint32_t i = 0; i < n; i++)
        printf("%s\t%s\t%s\n", xs[i].name,
               xs[i].status == SA2_INTRIN_IMPLEMENTED ? "implemented" : "stub_trap", xs[i].note);
}

/* Guest layout, state size and table shape as TSV, for the receipt. */
static void print_info(void) {
    printf("kv\tremill_state_bytes\t%u\n", sa2_rt_state_size());
    printf("kv\tcpu_state_bytes\t%u\n", (unsigned)sizeof(sa2_cpu_state_t));
    printf("kv\tguest_allocation_bytes\t%u\n", sa2_guest.size);
    printf("kv\timage_base\t0x%08x\n", sa2_guest.image_base);
    printf("kv\timage_size\t0x%08x\n", sa2_guest.image_size);
    printf("kv\timage_end\t0x%08x\n", sa2_guest.image_base + sa2_guest.image_size);
    printf("kv\tsection_count\t%u\n", sa2_guest.section_count);
    printf("kv\tfunction_table_entries\t%u\n", sa2_ft_count());
    printf("kv\tfunction_table_capacity\t%u\n", sa2_ft_capacity());
    printf("kv\tfunction_table_max_probe\t%u\n", sa2_ft_max_probe());
    printf("kv\tcall_sentinel\t0x%08x\n", SA2_CALL_SENTINEL);
    for (unsigned i = 0; i < sa2_guest.section_count; i++) {
        const sa2_section_t *s = &sa2_guest.sections[i];
        printf("section\t%s\t0x%08x\t0x%08x\t%u\t%c%c%c\n", s->name, s->va, s->va + s->virtual_size,
               s->raw_size, s->readable ? 'r' : '-', s->writable ? 'w' : '-', s->executable ? 'x' : '-');
    }
    printf("region\tnull guard\t0x%08x\t0x%08x\tevery access rejected\n", 0u, SA2_GUEST_NULL_GUARD - 1u);
    printf("region\tlow free\t0x%08x\t0x%08x\tunused in v0\n", SA2_GUEST_NULL_GUARD, SA2_GUEST_IMAGE_BASE - 1u);
    printf("region\timage\t0x%08x\t0x%08x\tPE headers and sections at their original VAs\n",
           sa2_guest.image_base, sa2_guest.image_base + sa2_guest.image_size - 1u);
    printf("region\theap arena\t0x%08x\t0x%08x\tthe guest's own HeapAlloc arena (sa2_imports.c)\n",
           SA2_GUEST_HEAP_BASE, SA2_GUEST_HEAP_END - 1u);
    printf("region\tscratch arena\t0x%08x\t0x%08x\tthe fixture harness's argument arenas\n",
           SA2_GUEST_SCRATCH_BASE, SA2_GUEST_SCRATCH_END - 1u);
    printf("region\truntime arena\t0x%08x\t0x%08x\tbump allocator, sa2_guest_alloc\n",
           SA2_GUEST_RT_ARENA_BASE, SA2_GUEST_RT_ARENA_END - 1u);
    printf("region\tstack arena\t0x%08x\t0x%08x\tguest stack, grows down from 0x%08x\n",
           SA2_GUEST_STACK_BASE, SA2_GUEST_STACK_TOP - 1u, SA2_GUEST_STACK_TOP);
    printf("region\treserved\t0x%08x\t0x%08x\tTEB/FS base for S3's FS:[0] chain\n",
           SA2_GUEST_STACK_TOP, SA2_GUEST_RESERVED_END - 1u);
}

/* ------------------------------------------------------------- self-test */
static int fails;

static void check(int ok, const char *what) {
    printf("%s\t%s\n", ok ? "pass" : "FAIL", what);
    if (!ok) fails++;
}

static int selftest(void) {
    uint32_t w = 0;
    const uint8_t probe[4] = {0xDE, 0xAD, 0xBE, 0xEF};

    check(sa2_guest.ready, "guest memory initialised");
    check(sa2_guest.section_count >= 4, "PE section table parsed");
    check(sa2_guest.image_size > 0x2000000u, "SizeOfImage covers the mapped image");

    /* The first bytes at the image base are the PE headers a loader maps. */
    uint8_t hdr[2] = {0, 0};
    check(sa2_peek(SA2_GUEST_IMAGE_BASE, 2, hdr) == SA2_OK && hdr[0] == 'M' && hdr[1] == 'Z',
          "MZ header readable at the image base");

    /* Every section landed at its VA with content. */
    int text_ok = 0;
    for (unsigned i = 0; i < sa2_guest.section_count; i++)
        if (strcmp(sa2_guest.sections[i].name, ".text") == 0 && sa2_guest.sections[i].executable)
            text_ok = sa2_guest_valid(sa2_guest.sections[i].va, sa2_guest.sections[i].virtual_size);
    check(text_ok, ".text mapped at its VA and inside the guest space");

    /* The first instruction byte of a known function is the image's, not zero:
     * 0x00426D50 begins with `sub esp, ...` in the unpacked executable. */
    uint8_t op = 0;
    check(sa2_peek(FN_CALC_POINT_ACTIVE, 1, &op) == SA2_OK && op != 0,
          "code bytes present at 0x00426D50");

    check(sa2_poke(A_OUT, 4, probe) == SA2_OK, "sa2_poke into the scratch arena");
    check(sa2_peek(A_OUT, 4, &w) == SA2_OK && w == 0xEFBEADDEu, "sa2_peek reads what sa2_poke wrote");
    check(sa2_peek(0, 4, &w) == SA2_E_RANGE, "sa2_peek rejects the null guard");
    check(sa2_poke(SA2_GUEST_END - 2, 4, probe) == SA2_E_RANGE, "sa2_poke rejects a range past the guest end");
    check(sa2_peek(SA2_GUEST_END - 4, 4, &w) == SA2_OK, "the last guest word is readable");

    uint32_t heap = sa2_guest_alloc(64, 16);
    check(heap >= SA2_GUEST_RT_ARENA_BASE && heap < SA2_GUEST_RT_ARENA_END && (heap & 15u) == 0,
          "runtime arena allocates aligned guest memory");
    /* And it must not land in the guest's own heap: those were the same
     * address until S4 slice 7 and the d3d9 lock backing store erased the
     * guest's malloc'd objects. A bound alone would pass if both moved. */
    check(heap < SA2_GUEST_HEAP_BASE || heap >= SA2_GUEST_HEAP_END,
          "runtime arena is outside the guest's HeapAlloc arena");
    check(strcmp(sa2_guest_region_of(A_STACK_TOP - 16), "stack") == 0, "stack arena region lookup");

    check(sa2_ft_count() > 0, "function table has entries");
    check(sa2_ft_lookup(CALLEE_CISQRT) != NULL, "function table hit on a native entry");
    check(sa2_ft_lookup(0x00123456u) == NULL, "function table miss on an unmapped address");

    /* A table miss through the dispatch path must trap, not crash. The trap
     * needs a call window, which only sa2_call opens, so the miss is driven
     * through sa2_call on an address nothing registered. */
    sa2_cpu_state_t st;
    sa2_call_result_t r;
    sa2_cpu_state_reset(&st);
    st.gpr[SA2_ESP] = A_STACK_TOP - 0x40u;
    check(sa2_call(0x00123456u, &st, ROW_TIMEOUT_S, &r) != 0 && r.status == SA2_TRAP_NOT_RUNNABLE,
          "sa2_call on an unregistered address reports status 3 with a diagnostic");
    printf("#\t%s\n", r.note);

    printf("selftest\t%s\t%d failure(s)\n", fails ? "FAIL" : "pass", fails);
    return fails ? 1 : 0;
}

/* ------------------------------------------------------------------ main */
int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--intrinsics") == 0) {
        print_intrinsics();
        return 0;
    }
    if (argc < 3) {
        fprintf(stderr, "usage: %s <image.exe> <vectors.bin> <out.bin> | %s <image.exe> --selftest | %s --intrinsics\n",
                argv[0], argv[0], argv[0]);
        return 2;
    }
    if (sa2_runtime_init(argv[1], 8) != SA2_OK) return 1;
    sa2_fixture_register_natives();

    if (strcmp(argv[2], "--selftest") == 0) return selftest();
    if (strcmp(argv[2], "--info") == 0) { print_info(); return 0; }
    if (argc != 4) {
        fprintf(stderr, "usage: %s <image.exe> <vectors.bin> <out.bin>\n", argv[0]);
        return 2;
    }

    vectors_t v;
    if (vectors_read(argv[2], &v) != 0) return 1;
    const sa2_fixture_shape *sh = sa2_fixture_shape_for(v.va);
    if (!sh || sh->in_words != v.in_words || sh->out_words != v.out_words) {
        fprintf(stderr, "vector shape mismatch for %#x\n", v.va);
        return 1;
    }

    uint32_t words = 2 + v.out_words;
    uint32_t traps = 0;
    char first_note[SA2_TRAP_NOTE_MAX] = {0};
    uint32_t first_trap_row = 0;
    sa2_fixture_call call;
    sa2_cpu_state_t st;
    sa2_call_result_t res;

    for (uint32_t r = 0; r < v.rows; r++) {
        uint32_t *out = v.out + (size_t)r * words;
        sa2_fixture_marshal_in(v.va, v.in + (size_t)r * v.in_words, &call);
        sa2_fixture_set_current(&call);

        sa2_cpu_state_reset(&st);          /* x87 cw 0x007F, design 2.3 */
        st.gpr[SA2_EAX] = call.eax;
        st.gpr[SA2_ECX] = call.ecx;
        st.gpr[SA2_EDX] = call.edx;
        st.gpr[SA2_ESI] = call.esi;
        st.gpr[SA2_EDI] = call.edi;
        st.gpr[SA2_ESP] = call.esp0;
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);

        sa2_call(v.va, &st, ROW_TIMEOUT_S, &res);
        out[0] = res.status;
        out[1] = res.exit_pc;
        if (res.status != SA2_TRAP_NONE) {
            if (!traps) { first_trap_row = r; snprintf(first_note, sizeof first_note, "%s", res.note); }
            traps++;
        }
        sa2_fixture_marshal_out(v.va, &call, res.status, st.gpr[SA2_EAX], float_word(st.st80[0]), out + 2);
    }

    fprintf(stderr, "%s: %u rows, %u trapped, table %u/%u entries, max probe %u, dispatch misses %u\n",
            sh->name, v.rows, traps, sa2_ft_count(), sa2_ft_capacity(), sa2_ft_max_probe(), sa2_ft_misses());
    if (traps) fprintf(stderr, "  first trap at row %u: %s\n", first_trap_row, first_note);
    return vectors_write(argv[3], &v);
}
