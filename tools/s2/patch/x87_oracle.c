/* S2 patch: the native x87 oracle for the probe functions.
 *
 * Every claim about what x87 does on an out-of-range `fistp`, on `fsqrt` under a
 * precision-control setting, or on a basic op after an `fldcw` is settled here by
 * executing the instruction itself, in inline assembly, on this host, under the
 * same control word the row carries. That is native evidence (authority
 * directly-traced): the lifted remill output is compared against it, never
 * against a model of it.
 *
 * It reads and writes the S2 harness's vector files (tools/s2/patch/s2_harness.cpp)
 * so a row compares field by field with the lifted run. The output prefix is the
 * same: status, exit_pc, eax, st(0) as three words, the control word after the
 * row, then the output-arena words. status is always 0 and exit_pc 0 (nothing can
 * trap here); eax is 0.
 *
 * The host's own control word is saved and restored around every row, so a row
 * cannot leak its rounding mode into the next one or into the C library.
 *
 * Usage: x87_oracle <probe-name> vectors.bin out.bin
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define A_IN_OFF 0

typedef struct {
    char magic[8];
    uint32_t va, rows, in_words, out_words, cw, stack_args;
    uint32_t reg_eax, reg_ecx, reg_edx, reg_esi, reg_edi, flags;
} header_t;

#define PREFIX 7

static uint16_t host_cw(void) { uint16_t c; __asm__ volatile("fnstcw %0" : "=m"(c)); return c; }
static void set_cw(uint16_t c) { __asm__ volatile("fldcw %0" : : "m"(c)); }

static float word_to_float(uint32_t w) { float f; memcpy(&f, &w, 4); return f; }
static double words_to_double(const uint32_t *w) { double d; uint64_t b = (uint64_t)w[0] | ((uint64_t)w[1] << 32); memcpy(&d, &b, 8); return d; }
static void float_to_word(float f, uint32_t *w) { memcpy(w, &f, 4); }
static void double_to_words(double d, uint32_t *w) { uint64_t b; memcpy(&b, &d, 8); w[0] = (uint32_t)b; w[1] = (uint32_t)(b >> 32); }
static void st_to_words(long double v, uint32_t *o) {
    uint8_t raw[10]; memcpy(raw, &v, 10);
    memcpy(&o[0], raw, 4); memcpy(&o[1], raw + 4, 4);
    o[2] = (uint32_t)(raw[8] | (raw[9] << 8));
}

/* Each probe below is the same instruction stream the hand-encoded probe holds
 * (tools/s2/patch/probes.py), written as inline assembly so the host executes it.
 * `fistp` stores are done with an explicit memory operand of the right width. */
static void run_row(const char *probe, const uint32_t *in, uint32_t in_words, uint32_t out_words, uint32_t cw, uint32_t *o) {
    uint16_t saved = host_cw();
    long double st0 = 0.0L;
    uint32_t arena[8];
    memset(arena, 0, sizeof arena);
    uint16_t use_cw = (uint16_t)cw;

    if (!strcmp(probe, "fsqrt_f32")) {
        float a = word_to_float(in[0]); float r;
        set_cw(use_cw);
        __asm__ volatile("flds %1\n\tfsqrt\n\tfstps %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        float_to_word(r, &arena[0]);
    } else if (!strcmp(probe, "fsqrt_f64")) {
        double a = words_to_double(in); double r;
        set_cw(use_cw);
        __asm__ volatile("fldl %1\n\tfsqrt\n\tfstpl %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        double_to_words(r, &arena[0]);
    } else if (!strcmp(probe, "fsqrt_cw")) {
        uint16_t rcw = (uint16_t)in[0];
        float a = word_to_float(in[1]); float r;
        set_cw(rcw);
        __asm__ volatile("flds %1\n\tfsqrt\n\tfstps %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        float_to_word(r, &arena[0]);
    } else if (!strcmp(probe, "fistpl_f64") || !strcmp(probe, "fistl_f64")) {
        double a = words_to_double(in); int32_t r = 0;
        set_cw(use_cw);
        __asm__ volatile("fldl %1\n\tfistpl %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        arena[0] = (uint32_t)r;
    } else if (!strcmp(probe, "fistpll_f64")) {
        double a = words_to_double(in); int64_t r = 0;
        set_cw(use_cw);
        __asm__ volatile("fldl %1\n\tfistpll %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        arena[0] = (uint32_t)(uint64_t)r; arena[1] = (uint32_t)((uint64_t)r >> 32);
    } else if (!strcmp(probe, "fistps_f64")) {
        double a = words_to_double(in); int16_t r = 0;
        set_cw(use_cw);
        __asm__ volatile("fldl %1\n\tfistps %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        arena[0] = (uint32_t)(uint16_t)r;
    } else if (!strcmp(probe, "fistpl_f32")) {
        float a = word_to_float(in[0]); int32_t r = 0;
        set_cw(use_cw);
        __asm__ volatile("flds %1\n\tfistpl %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        arena[0] = (uint32_t)r;
    } else if (!strcmp(probe, "fistpl_cw")) {
        uint16_t rcw = (uint16_t)in[0];
        double a = words_to_double(in + 1); int32_t r = 0;
        set_cw(rcw);
        __asm__ volatile("fldl %1\n\tfistpl %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        arena[0] = (uint32_t)r;
    } else if (!strcmp(probe, "fistpll_cw")) {
        uint16_t rcw = (uint16_t)in[0];
        double a = words_to_double(in + 1); int64_t r = 0;
        set_cw(rcw);
        __asm__ volatile("fldl %1\n\tfistpll %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        arena[0] = (uint32_t)(uint64_t)r; arena[1] = (uint32_t)((uint64_t)r >> 32);
    } else if (!strcmp(probe, "fldcw_fnstcw")) {
        uint16_t rcw = (uint16_t)in[0], got = 0;
        set_cw(rcw);
        __asm__ volatile("fnstcw %0" : "=m"(got));
        set_cw(saved);
        arena[0] = got;
    } else if (!strcmp(probe, "fadd_cw") || !strcmp(probe, "fsub_cw") || !strcmp(probe, "fsubr_cw") ||
               !strcmp(probe, "fmul_cw") || !strcmp(probe, "fdiv_cw") || !strcmp(probe, "fsubp_cw")) {
        uint16_t rcw = (uint16_t)in[0];
        float a = word_to_float(in[1]), b = word_to_float(in[2]), r;
        set_cw(rcw);
        if (!strcmp(probe, "fadd_cw"))       __asm__ volatile("flds %1\n\tfadds %2\n\tfstps %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fsub_cw"))  __asm__ volatile("flds %1\n\tfsubs %2\n\tfstps %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fsubr_cw")) __asm__ volatile("flds %1\n\tfsubrs %2\n\tfstps %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fmul_cw"))  __asm__ volatile("flds %1\n\tfmuls %2\n\tfstps %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fdiv_cw"))  __asm__ volatile("flds %1\n\tfdivs %2\n\tfstps %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        /* the probe's bytes are DE E9. GAS assembles `fsubp %st,%st(1)` to DE E1
         * (fsubrp) -- the historical AT&T operand reversal -- so the oracle emits
         * the opcode directly and executes exactly what the probe holds. */
        else                                  __asm__ volatile("flds %1\n\tflds %2\n\t.byte 0xde,0xe9\n\tfstps %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        set_cw(saved);
        float_to_word(r, &arena[0]);
    } else if (!strncmp(probe, "fsqrt_cw80", 10)) {
        uint16_t rcw = (uint16_t)in[0];
        float a = word_to_float(in[1]); long double r;
        set_cw(rcw);
        __asm__ volatile("flds %1\n\tfsqrt\n\tfstpt %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        st_to_words(r, &arena[0]);
    } else if (!strcmp(probe, "fadd_cw80") || !strcmp(probe, "fsub_cw80") || !strcmp(probe, "fsubr_cw80") ||
               !strcmp(probe, "fmul_cw80") || !strcmp(probe, "fdiv_cw80") || !strcmp(probe, "fsubp_cw80")) {
        uint16_t rcw = (uint16_t)in[0];
        float a = word_to_float(in[1]), b = word_to_float(in[2]);
        long double r;
        set_cw(rcw);
        if (!strcmp(probe, "fadd_cw80"))       __asm__ volatile("flds %1\n\tfadds %2\n\tfstpt %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fsub_cw80"))  __asm__ volatile("flds %1\n\tfsubs %2\n\tfstpt %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fsubr_cw80")) __asm__ volatile("flds %1\n\tfsubrs %2\n\tfstpt %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fmul_cw80"))  __asm__ volatile("flds %1\n\tfmuls %2\n\tfstpt %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else if (!strcmp(probe, "fdiv_cw80"))  __asm__ volatile("flds %1\n\tfdivs %2\n\tfstpt %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        else                                    __asm__ volatile("flds %1\n\tflds %2\n\t.byte 0xde,0xe9\n\tfstpt %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        set_cw(saved);
        st_to_words(r, &arena[0]);
    } else if (!strcmp(probe, "crt_sqrt_body")) {
        /* the CRT square-root body takes a double on the x87 stack and loads
         * (cw & 0x300) | 0x7F, so the root is taken at the guest's precision with
         * round-to-nearest; the result stays in st(0) as an x86_fp80. */
        uint16_t rcw = (uint16_t)((cw & 0x300u) | 0x7Fu);
        double a = words_to_double(in); long double r;
        set_cw(rcw);
        __asm__ volatile("fldl %1\n\tfsqrt\n\tfstpt %0" : "=m"(r) : "m"(a) : "st");
        set_cw(saved);
        st0 = r;                       /* this probe's result stays in st(0), not in memory */
    } else if (!strcmp(probe, "fdiv_cw_f64")) {
        uint16_t rcw = (uint16_t)in[0];
        double a = words_to_double(in + 1), b = words_to_double(in + 3), r;
        set_cw(rcw);
        __asm__ volatile("fldl %1\n\tfdivl %2\n\tfstpl %0" : "=m"(r) : "m"(a), "m"(b) : "st");
        set_cw(saved);
        double_to_words(r, &arena[0]);
    } else {
        fprintf(stderr, "x87_oracle: unknown probe %s\n", probe);
        exit(2);
    }
    (void)in_words;
    o[0] = 0; o[1] = 0; o[2] = 0;
    st_to_words(st0, o + 3);
    o[6] = use_cw;
    for (uint32_t i = 0; i < out_words; i++) o[PREFIX + i] = arena[i];
}

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: %s <probe> vectors.bin out.bin\n", argv[0]); return 2; }
    FILE *f = fopen(argv[2], "rb");
    if (!f) { perror(argv[2]); return 1; }
    header_t h;
    if (fread(&h, sizeof h, 1, f) != 1 || memcmp(h.magic, "SA2S2V1", 8) != 0) { fprintf(stderr, "bad vector file\n"); return 1; }
    size_t nin = (size_t)h.rows * h.in_words;
    uint32_t *in = calloc(nin ? nin : 1, 4);
    if (fread(in, 4, nin, f) != nin) { fprintf(stderr, "short vector file\n"); return 1; }
    fclose(f);
    uint32_t words = PREFIX + h.out_words;
    uint32_t *out = calloc((size_t)h.rows * words, 4);
    for (uint32_t r = 0; r < h.rows; r++)
        run_row(argv[1], in + (size_t)r * h.in_words, h.in_words, h.out_words, h.cw, out + (size_t)r * words);
    FILE *g = fopen(argv[3], "wb");
    if (!g) { perror(argv[3]); return 1; }
    uint32_t hdr[3] = {h.va, h.rows, words};
    fwrite("SA2S2O1\0", 1, 8, g);
    fwrite(hdr, 4, 3, g);
    fwrite(out, 4, (size_t)h.rows * words, g);
    fclose(g);
    return 0;
}
