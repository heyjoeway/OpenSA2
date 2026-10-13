/* cri_dsp_diff: runtime/sa2_cri_dsp.c's native bodies against the lifted double-mode bodies.
 *
 * Subtitle: links the lifted objects of 0x00842500, 0x00843400, 0x00843460 and 0x008418B0 (built
 * by tools/s8/perf_f80.py, or for Android by tools/s8/android_game.py, in the double mode) as
 * `__real_`, the runtime's native bodies as `__wrap_`, and runs each kernel on random and
 * edge-case States and guest memories: once with the native bodies off (`sa2_cri_dsp_mode` 0, so
 * every call, the comb's call of the biquad and the network's of the combs included, is the
 * lifted body), once on, from the same bytes and in the same host rounding mode. The whole State,
 * every guest byte the kernel can reach (and margins around them) and the return value must be
 * equal. tools/s8/native_kernels/cri_dsp_diff.py builds and runs it;
 * Documentation/scripts/tests/test_cri_dsp_native.py holds its answers.
 *
 *   cri_dsp_diff layout
 *   cri_dsp_diff biquad|comb|net|mix CASES SEED
 *   cri_dsp_diff bench biquad|comb|net|mix CALLS
 *   cri_dsp_diff calls
 *
 * Prints one JSON object. Per kernel, CASES ordinary cases (realistic values, values with edge
 * cases mixed in, and wild finite bit patterns; every precision control under every rounding
 * control, the host's mode the control word's RC or now and then another; segment bases zero and
 * not; random bytes in every register slot) and, beside them, CASES/16 cases of each guard, every
 * one of which must take the lifted body: an infinity or NaN among the inputs, an overlap the
 * native order of reads and writes does not hold for, an access outside the guest space, guest
 * memory off its fast path, and the bodies switched off. A build without the double mode's
 * marker (`--variant nomarker` in the driver) runs the same cases and must take the lifted body
 * every time. CRI_DSP_DIFF_MASK gives the second run's kernel mask (the runtime's SA2_CRI_DSP
 * bits), CRI_DSP_DIFF_TRACE prints each case, CRI_DSP_DIFF_DUMP every differing byte of a
 * mismatch.
 *
 * The network's overlap cases include outputs that overlap without being one float (misaligned
 * by 1 to 3 bytes), and, among the first SNAN_CASES of those, outputs laid out so that the tail's
 * `fld [eax]` reloads a float whose high half is the second output's low half: comb 0's delayed
 * sample is redrawn until the lifted body's reload is a signalling NaN (`snan_reloads`), the case
 * the lifted FLD m32 ORs into IE.
 *
 * `calls` makes one realistic call of each kernel (the biquad, the comb, the network, the mix)
 * with `sa2_cri_dsp_mode` left for the runtime to read from SA2_CRI_DSP, and prints each kernel's
 * native and lifted calls; with SA2_CRI_DSP_STATS set the runtime prints its own at exit.
 */
#define _GNU_SOURCE
#include <fenv.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

#define STATE_BYTES 3504u
#define GUEST_END 0x20000000u
#define NULL_GUARD 0x1000u

/* ------------------------------------------------------------------ what the objects need */

uint8_t *sa2_guest_fast_base;
static uint8_t *G;
static uint8_t scratch[0x10010];

/* The checked path, as the runtime's: the same memory, or (outside the guest space, where the
 * runtime would trap) a scratch page both runs see alike. */
static int scratch_touched;
static uint8_t *slow(uint32_t a, uint32_t len) {
    if (a >= NULL_GUARD && a <= GUEST_END - len) return G + a;
    scratch_touched = 1;
    return scratch + (a & 0xFFFFu);
}
uint8_t *sa2_guest_read_slow(uint32_t a, uint32_t len) { return slow(a, len); }
uint8_t *sa2_guest_write_slow(uint32_t a, uint32_t len) { return slow(a, len); }
void *__remill_function_return(void *s, uint32_t pc, void *m) { (void)s; (void)pc; return m; }
/* The network calls eight of its nine combs through the function table, whose row for 0x00843400
 * the production link's -wrap points at the wrapper; the harness's table is that one row. */
void *__wrap_sub_843400(void *, uint32_t, void *);
void *__sa2_dispatch_stub(void *s, uint32_t pc, void *m) {
    if (pc != 0x00843400u) { fprintf(stderr, "dispatch to %#x\n", pc); abort(); }
    return __wrap_sub_843400(s, pc, m);
}


/* ------------------------------------------------------------------ the runtime's side */

extern int sa2_cri_dsp_mode;
extern uint64_t sa2_cri_dsp_calls[][2];
struct field { const char *field; uint32_t offset; };
extern const struct field sa2_cri_dsp_layout[];
extern const uint32_t sa2_cri_dsp_layout_count;
int sa2_remill_offset(const char *field);
unsigned sa2_remill_state_size(void);
void *__wrap_sub_842500(void *, uint32_t, void *);
void *__wrap_sub_843400(void *, uint32_t, void *);
void *__wrap_sub_8418b0(void *, uint32_t, void *);
void *__wrap_sub_843460(void *, uint32_t, void *);

static uint32_t off(const char *name) {
    for (uint32_t i = 0; i < sa2_cri_dsp_layout_count; i++)
        if (!strcmp(sa2_cri_dsp_layout[i].field, name)) return sa2_cri_dsp_layout[i].offset;
    fprintf(stderr, "no field %s\n", name);
    exit(2);
}
static uint32_t O_CWD, O_SS, O_DS, O_ECX, O_ESP, O_ESI, O_ST0, O_SWD;

/* ------------------------------------------------------------------ random values */

static uint64_t rs;
static uint64_t rnd64(void) {
    uint64_t z = (rs += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}
static uint32_t rnd32(void) { return (uint32_t)rnd64(); }
static uint32_t below(uint32_t n) { return (uint32_t)(((uint64_t)rnd32() * n) >> 32); }
static double unit(void) { return (double)(rnd64() >> 11) * 0x1p-53; }
static float fb(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }

static float f_audio(void) { return (float)(unit() * 2.0 - 1.0); }
static float f_coef(void) { return (float)(unit() * 4.0 - 2.0); }
static float f_gain(void) { return (float)(unit() * 1.98 - 0.99); }
static float f_finite_bits(void) {
    uint32_t b;
    do b = rnd32(); while ((b & 0x7F800000u) == 0x7F800000u);
    return fb(b);
}
static float f_edge(void) {
    static const uint32_t e[] = {
        0x00000000u, 0x80000000u, 0x00800000u, 0x80800000u, 0x00000001u, 0x80000001u,
        0x007FFFFFu, 0x807FFFFFu, 0x7F7FFFFFu, 0xFF7FFFFFu, 0x3F800000u, 0xBF800000u,
        0x3F000000u, 0x33800000u, 0x4B000000u, 0x5F000000u, 0x1F800000u, 0x0D800000u,
        0x3F7FFFFFu, 0x3F800001u, 0x34000000u, 0xB3800000u};
    const uint32_t r = below(4);
    if (r == 0) return fb(rnd32() & 0x807FFFFFu);                        /* a denormal or zero */
    if (r == 1) return fb((rnd32() & 0x807FFFFFu) | (below(2) ? 0x7F000000u : 0x00800000u));
    return fb(e[below(sizeof e / sizeof e[0])]);
}
static float f_nonfinite(void) {
    const uint32_t s = rnd32() & 0x80000000u, r = below(4);
    if (r == 0) return fb(s | 0x7F800000u);                               /* infinity */
    if (r == 1) return fb(s | 0x7FC00000u | (rnd32() & 0x3FFFFFu));       /* quiet NaN */
    return fb(s | 0x7F800000u | (1u + below(0x3FFFFFu)));                 /* signalling NaN */
}

/* A field's value in the case's class: realistic, realistic with edges, or wild. */
static int g_class;
static float f_val(float (*real)(void)) {
    const uint32_t r = below(100);
    if (g_class == 0) return real();
    if (g_class == 1) return r < 70 ? real() : r < 90 ? f_edge() : f_finite_bits();
    return r < 30 ? real() : r < 60 ? f_edge() : f_finite_bits();
}

static void put32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static void put16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
/* A guest write of the case's setup; past the guest space (a G_RANGE case's tail) there is no
 * guest byte, and the lifted body reads the scratch page there instead. */
static void putf(uint32_t a, float v) { if (a >= NULL_GUARD && a <= GUEST_END - 4) memcpy(G + a, &v, 4); }
static void fill_random(uint32_t a, uint32_t len) {
    for (uint32_t i = 0; i < len; i += 4) {
        const uint32_t v = rnd32();
        memcpy(G + a + i, &v, len - i < 4 ? len - i : 4);
    }
}

/* ------------------------------------------------------------------ the cases' shape */

enum { G_NONE, G_NONFINITE, G_OVERLAP, G_RANGE, G_NOBASE, G_OFF, G_KINDS };
static const char *const GUARD_NAME[G_KINDS] = {"none", "nonfinite_input", "overlap",
                                                "outside_guest_space", "memory_off_fast_path",
                                                "switched_off"};

/* A control word: every precision (24, the reserved 01, 53, 64 bits) under every rounding
 * control, the game's own words most often (0x027F a new thread's, 0x007F the main thread's,
 * 0x0E7F and 0x0C7F the streaming thread's truncating ones), the other bits random now and then. */
static uint16_t pick_cw(int guard) {
    (void)guard;
    static const uint16_t game[] = {0x027F, 0x007F, 0x0E7F, 0x0C7F, 0x0A7F, 0x067F, 0x037F,
                                    0x0F7F};
    const uint32_t r = below(100);
    if (r < 70) return game[below(sizeof game / sizeof game[0])];
    if (r < 90) return (uint16_t)(0x007Fu | (below(4) << 8) | (below(4) << 10));
    return (uint16_t)(rnd32() & 0xFFFFu);
}

/* The host's rounding mode for a case: the control word's RC, as the runtime keeps it, and now
 * and then another (the lifted code and the native body read the same host mode, so they must
 * agree whatever it is). */
static int host_mode(uint16_t cw) {
    static const int modes[4] = {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO};
    return modes[below(100) < 85 ? (cw >> 10) & 3u : below(4)];
}
static uint32_t pick_base(void) { return below(100) < 85 ? 0 : (below(0x40000) << (below(2) ? 2 : 0)); }

typedef struct { uint32_t lo, len; } win;
#define MAX_WIN 96
static win W[MAX_WIN];
static int nw;
static uint8_t *snap0[MAX_WIN], *snapA[MAX_WIN];
static uint32_t snap_cap[MAX_WIN];
static uint8_t scratchA[sizeof scratch];

static void add_win(uint32_t lo, uint32_t len) {
    if (lo < NULL_GUARD) { len = lo + len > NULL_GUARD ? lo + len - NULL_GUARD : 0; lo = NULL_GUARD; }
    if (lo >= GUEST_END) return;
    if (lo + len > GUEST_END || lo + len < lo) len = GUEST_END - lo;
    W[nw].lo = lo;
    W[nw].len = len;
    if (snap_cap[nw] < len) {
        snap0[nw] = realloc(snap0[nw], len);
        snapA[nw] = realloc(snapA[nw], len);
        snap_cap[nw] = len;
    }
    nw++;
}
static void save(uint8_t **to) { for (int i = 0; i < nw; i++) memcpy(to[i], G + W[i].lo, W[i].len); }
static void restore(uint8_t **from) { for (int i = 0; i < nw; i++) memcpy(G + W[i].lo, from[i], W[i].len); }

static uint8_t S0[STATE_BYTES] __attribute__((aligned(16)));
static uint8_t SA[STATE_BYTES] __attribute__((aligned(16)));
static uint8_t SB[STATE_BYTES] __attribute__((aligned(16)));

static void state_random(uint8_t *s, uint16_t cw, uint32_t ssb, uint32_t dsb) {
    for (uint32_t i = 0; i < STATE_BYTES; i += 8) {
        const uint64_t v = rnd64();
        memcpy(s + i, &v, 8);
    }
    put16(s + O_CWD, cw);
    put32(s + O_SS, ssb);
    put32(s + O_DS, dsb);
}

/* A field name for a State offset, for the report. */
static const char *where(uint32_t o, char *buf) {
    static const struct { const char *name; uint32_t width; } widths[] = {
        {"aflag.", 1}, {"seg.", 2}, {"addr.", 8}, {"gpr.", 8}, {"st.elems", 10}, {"sw.", 1},
        {"x87.fxsave.ip", 4}, {"x87.fxsave.dp", 4}, {"x87.fxsave.", 2}};
    for (uint32_t i = 0; i < sa2_cri_dsp_layout_count; i++) {
        uint32_t w = 1;
        for (unsigned k = 0; k < sizeof widths / sizeof widths[0]; k++)
            if (!strncmp(sa2_cri_dsp_layout[i].field, widths[k].name, strlen(widths[k].name))) {
                w = widths[k].width;
                break;
            }
        if (o >= sa2_cri_dsp_layout[i].offset && o < sa2_cri_dsp_layout[i].offset + w) {
            snprintf(buf, 64, "%s+%u", sa2_cri_dsp_layout[i].field, o - sa2_cri_dsp_layout[i].offset);
            return buf;
        }
    }
    if (o >= O_ST0 - 6 && o < O_ST0 - 6 + 128) {
        snprintf(buf, 64, "st.elems[%u]+%u", (o - (O_ST0 - 6)) / 16, (o - (O_ST0 - 6)) % 16);
        return buf;
    }
    snprintf(buf, 64, "state+%u", o);
    return buf;
}

/* ------------------------------------------------------------------ one case */

typedef void *(*kernel_fn)(void *, uint32_t, void *);

typedef struct {
    uint64_t cases, native, mismatches;
    uint64_t native_pc24, native_pc53;
} tally;

static tally T[G_KINDS];
/* The ordinary cases by their values' class: realistic, edges mixed in, wild finite bits. */
static uint64_t by_class[3][2];
static const char *const CLASS_NAME[3] = {"realistic", "edges", "wild"};
static char first[512];
static char info[256];
static uint64_t case_no;
/* The kernels whose native bodies the second run turns on (CRI_DSP_DIFF_MASK). */
static int cri_mask = 15;

static void report_mismatch(const char *what) {
    if (!first[0]) snprintf(first, sizeof first, "case %llu: %s", (unsigned long long)case_no, what);
}

/* Runs the prepared case both ways and compares. `no_base`: guest memory off its fast path for
 * both runs. The scratch page is all zero when a case starts; a run that reached it (an access
 * outside the guest space, which the runtime would trap) has it compared and cleared. */
/* `bench net`: the case is set up and not run; the bench calls it (see bench). */
static int bench_setup_only;
static kernel_fn bench_fn;
static uint32_t bench_pc;

/* The host mode the next case runs in, when a case fixed it beforehand (the sNaN reload's search
 * runs the lifted body in it); -1 to draw one. */
static int forced_mode = -1;

static void run_case(kernel_fn fn, int kernel, uint32_t pc, int guard, int no_base) {
    if (bench_setup_only) {
        bench_fn = fn;
        bench_pc = pc;
        (void)kernel; (void)guard; (void)no_base;
        return;
    }
    void *const MEM = (void *)(uintptr_t)0x5A2Du;
    tally *t = &T[guard];
    t->cases++;
    save(snap0);
    sa2_guest_fast_base = no_base ? NULL : G;
    uint16_t cw;
    memcpy(&cw, S0 + O_CWD, 2);
    const int mode = forced_mode >= 0 ? forced_mode : host_mode(cw);
    forced_mode = -1;

    memcpy(SA, S0, STATE_BYTES);
    sa2_cri_dsp_mode = 0;
    fesetround(mode);
    void *ra = fn(SA, pc, MEM);
    fesetround(FE_TONEAREST);
    save(snapA);
    const int touched_a = scratch_touched;
    if (touched_a) {
        memcpy(scratchA, scratch, sizeof scratch);
        memset(scratch, 0, sizeof scratch);
        scratch_touched = 0;
    }

    restore(snap0);
    memcpy(SB, S0, STATE_BYTES);
    sa2_cri_dsp_mode = guard == G_OFF ? 0 : cri_mask;
    const uint64_t before = sa2_cri_dsp_calls[kernel][0];
    uint64_t was[4][2];
    memcpy(was, sa2_cri_dsp_calls, sizeof was);
    fesetround(mode);
    void *rb = fn(SB, pc, MEM);
    fesetround(FE_TONEAREST);
    const int native = sa2_cri_dsp_calls[kernel][0] != before;
    sa2_guest_fast_base = G;

    char buf[128], name[64];
    int bad = 0;
    if (ra != MEM || rb != MEM) {
        report_mismatch("the return value is not the memory pointer");
        bad = 1;
    }
    if (!bad && memcmp(SA, SB, STATE_BYTES)) {
        uint32_t o = 0;
        while (SA[o] == SB[o]) o++;
        snprintf(buf, sizeof buf, "State %s: lifted %02x native %02x", where(o, name), SA[o], SB[o]);
        report_mismatch(buf);
        bad = 1;
    }
    for (int i = 0; !bad && i < nw; i++)
        if (memcmp(snapA[i], G + W[i].lo, W[i].len)) {
            uint32_t o = 0;
            while (snapA[i][o] == G[W[i].lo + o]) o++;
            snprintf(buf, sizeof buf, "guest %#010x: lifted %02x native %02x", W[i].lo + o,
                     snapA[i][o], G[W[i].lo + o]);
            report_mismatch(buf);
            bad = 1;
        }
    if (touched_a || scratch_touched) {
        if (!touched_a) memset(scratchA, 0, sizeof scratch);
        if (!bad && memcmp(scratchA, scratch, sizeof scratch)) {
            report_mismatch("the scratch page");
            bad = 1;
        }
        memset(scratch, 0, sizeof scratch);
        scratch_touched = 0;
    }
    if (bad && getenv("CRI_DSP_DIFF_DUMP")) {
        fprintf(stderr, "  setup: %s\n", info);
        fprintf(stderr, "case %llu guard %s native %d cw %04x mode %d; native/lifted biquad %llu/%llu comb %llu/%llu net %llu/%llu mix %llu/%llu\n",
                (unsigned long long)case_no, GUARD_NAME[guard], native, cw, mode,
                (unsigned long long)(sa2_cri_dsp_calls[0][0] - was[0][0]), (unsigned long long)(sa2_cri_dsp_calls[0][1] - was[0][1]),
                (unsigned long long)(sa2_cri_dsp_calls[1][0] - was[1][0]), (unsigned long long)(sa2_cri_dsp_calls[1][1] - was[1][1]),
                (unsigned long long)(sa2_cri_dsp_calls[2][0] - was[2][0]), (unsigned long long)(sa2_cri_dsp_calls[2][1] - was[2][1]),
                (unsigned long long)(sa2_cri_dsp_calls[3][0] - was[3][0]), (unsigned long long)(sa2_cri_dsp_calls[3][1] - was[3][1]));
        for (uint32_t o = 0; o < STATE_BYTES; o++)
            if (SA[o] != SB[o]) fprintf(stderr, "  %s: in %02x lifted %02x native %02x\n", where(o, name), S0[o], SA[o], SB[o]);
        for (int i = 0; i < nw; i++)
            for (uint32_t o = 0; o < W[i].len; o++)
                if (snapA[i][o] != G[W[i].lo + o])
                    fprintf(stderr, "  guest %#010x: in %02x lifted %02x native %02x\n", W[i].lo + o,
                            snap0[i][o], snapA[i][o], G[W[i].lo + o]);
    }
    t->mismatches += bad;
    if (guard == G_NONE) {
        by_class[g_class][0]++;
        by_class[g_class][1] += native;
    }
    if (native) {
        t->native++;
        if (S0[O_CWD + 1] & 0x03u) t->native_pc53++;
        else t->native_pc24++;
    }
}

static int pick_guard(uint64_t i, uint64_t ordinary) {
    if (i < ordinary) return G_NONE;
    return 1 + (int)((i - ordinary) % (G_KINDS - 1));
}

/* ------------------------------------------------------------------ the biquad */

static const uint32_t BQ_COEF[] = {0x08, 0x14, 0x18, 0x54, 0x58, 0x5C};
static const uint32_t BQ_Z[] = {0x34, 0x38, 0x78, 0x7C};

static void bq_fill(uint32_t o) {
    for (unsigned i = 0; i < 6; i++) putf(o + BQ_COEF[i], f_val(f_coef));
    for (unsigned i = 0; i < 4; i++) putf(o + BQ_Z[i], f_val(f_audio));
}
static void bq_poison(uint32_t o) {
    const uint32_t k = below(10);
    putf(o + (k < 6 ? BQ_COEF[k] : BQ_Z[k - 6]), f_nonfinite());
}

static uint32_t addr_in(uint32_t lo, uint32_t span) {
    uint32_t a = lo + (below(span >> 2) << 2);
    if (below(16) == 0) a += 1 + below(3);                 /* a misaligned one now and then */
    return a;
}

static void case_biquad(int guard) {
    g_class = below(3);
    const uint16_t cw = pick_cw(guard);
    const uint32_t ssb = pick_base(), dsb = pick_base();
    uint32_t o = addr_in(0x01000000u, 0x100000u), sp = addr_in(0x01200000u, 0x100000u);
    if (guard == G_OVERLAP) sp = o + 1 + below(0x7E);
    if (guard == G_RANGE) {
        if (below(2)) sp = GUEST_END - 4 - below(4);
        else o = GUEST_END - 0x80 + 4 * (1 + below(8));
    }
    nw = 0;
    add_win(o - 0x40, 0x100);
    if (guard != G_OVERLAP) add_win(sp - 0x40, 0x80);
    else add_win(o - 0x80, 0x200);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    bq_fill(o);
    if (sp + 8 <= GUEST_END) putf(sp + 4, f_val(f_audio));
    if (guard == G_NONFINITE) {
        if (below(11) == 0) putf(sp + 4, f_nonfinite());
        else bq_poison(o);
    }
    state_random(S0, cw, ssb, dsb);
    put32(S0 + O_ECX, o - dsb);
    put32(S0 + O_ESP, sp - ssb);
    run_case(__wrap_sub_842500, 0, 0x00842500u, guard, guard == G_NOBASE);
}

/* ------------------------------------------------------------------ the comb */

static void case_comb(int guard) {
    g_class = below(3);
    const uint16_t cw = pick_cw(guard);
    const uint32_t ssb = pick_base(), dsb = pick_base();
    const uint32_t o = addr_in(0x01000000u, 0x100000u);
    uint32_t e = addr_in(0x01200000u, 0x100000u), ring = 0x01400000u + (below(0x40000) << 2);
    uint32_t mask;
    const uint32_t mr = below(10);
    if (mr < 7) mask = (1u << below(13)) - 1u;
    else if (mr < 9) mask = rnd32() & 0xFFFu;
    else mask = 0;
    uint32_t delay, wi;
    switch (below(5)) {
    case 0: delay = below(mask + 1u); break;
    case 1: { const uint32_t d[] = {0, 1, mask, mask + 1u, 2}; delay = d[below(5)]; break; }
    case 2: delay = rnd32(); break;
    case 3: delay = below(4); break;
    default: delay = below(mask + 1u); break;
    }
    switch (below(4)) {
    case 0: wi = mask; break;               /* the write index at the wrap */
    case 1: wi = rnd32(); break;
    case 2: wi = 0; break;
    default: wi = below(mask + 1u); break;
    }
    const uint32_t ri = (wi - delay + 1u) & mask, wn = (wi + 1u) & mask;
    /* An overlap never covers the ring's pointer, delay, index or mask ([esi+04h, esi+18h)):
     * the guest reads those after its first push, and the case's windows are set from them. */
    if (guard == G_OVERLAP) {
        const uint32_t k = below(3);
        if (k == 0) e = o + 0x2C + 4 * below(0x1C);                       /* the frame in the object */
        else if (k == 1) ring = o + 0x1C + 4 * below(0x20) - 4 * ri;      /* the read cell in it */
        else ring = e - 16 - 4 * wn;                                       /* the write cell in the frame */
    }
    if (guard == G_RANGE) e = GUEST_END - 4 * below(2);   /* [esp+4] or [esp] past the end */
    nw = 0;
    add_win(o - 0x20, 0xE0);
    add_win(e - 0x40, 0x80);
    add_win(ring + 4 * ri - 0x10, 0x20);
    add_win(ring + 4 * wn - 0x10, 0x20);
    if (mask <= 0x1000u) add_win(ring - 0x10, 4 * (mask + 1u) + 0x20);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    put32(G + o + 0x04, ring - dsb);
    put32(G + o + 0x0C, delay);
    put32(G + o + 0x10, wi);
    put32(G + o + 0x14, mask);
    putf(o + 0x1C, f_val(f_gain));
    bq_fill(o + 0x20);
    if (ring + 4 * ri + 4 <= GUEST_END) putf(ring + 4 * ri, f_val(f_audio));
    if (e + 8 <= GUEST_END) putf(e + 4, f_val(f_audio));
    if (guard == G_NONFINITE) {
        const uint32_t k = below(4);
        if (k == 0) putf(ring + 4 * ri, f_nonfinite());
        else if (k == 1) putf(e + 4, f_nonfinite());
        else if (k == 2) putf(o + 0x1C, f_nonfinite());
        else bq_poison(o + 0x20);
    }
    /* The ring cells as the guest will find them, once every value above is in place (one of
     * those writes may have landed on a field), held too. */
    {
        uint32_t f[4];
        memcpy(f, G + o + 4, 4);
        memcpy(f + 1, G + o + 0xC, 8);
        memcpy(f + 3, G + o + 0x14, 4);
        const uint32_t ar = (f[2] - f[1] + 1u) & f[3], aw = (f[2] + 1u) & f[3];
        add_win(dsb + f[0] + 4u * ar - 0x10, 0x20);
        add_win(dsb + f[0] + 4u * aw - 0x10, 0x20);
    }
    state_random(S0, cw, ssb, dsb);
    put32(S0 + O_ECX, o - dsb);
    put32(S0 + O_ESP, e - ssb);
    snprintf(info, sizeof info, "o %#x e %#x ring %#x mask %#x delay %#x wi %#x ri %#x wn %#x ds %#x ss %#x; mem ring %#x delay %#x wi %#x mask %#x",
             o, e, ring, mask, delay, wi, ri, wn, dsb, ssb, *(uint32_t *)(G + o + 4), *(uint32_t *)(G + o + 0xC), *(uint32_t *)(G + o + 0x10), *(uint32_t *)(G + o + 0x14));
    run_case(__wrap_sub_843400, 1, 0x00843400u, guard, guard == G_NOBASE);
}

/* ------------------------------------------------------------------ the network */

/* The reverb network's object: six delay stages (write index, mask, ring, delay), their
 * coefficients, nine combs at +1Ch (each 0B4h), and the image's constants it reads. */
static const uint16_t NET_STAGE_H[6][4] = {
    {0x734, 0x738, 0x728, 0x730}, {0x680, 0x684, 0x674, 0x67C}, {0x6A4, 0x6A8, 0x698, 0x6A0},
    {0x6C8, 0x6CC, 0x6BC, 0x6C4}, {0x6EC, 0x6F0, 0x6E0, 0x6E8}, {0x710, 0x714, 0x704, 0x70C}};
static const uint16_t NET_COEF[] = {0x744, 0x68C, 0x690, 0x6B0, 0x6B4, 0x6D4, 0x6D8, 0x6F8,
                                    0x6FC, 0x71C, 0x720};
static const uint32_t NET_K64[] = {0x00905240u, 0x00905D88u, 0x00899278u, 0x00899270u,
                                   0x00905738u, 0x00899268u, 0x00905748u, 0x00899260u};
#define NET_K32 0x0093290Cu
#define NET_K_LO 0x00899260u
#define NET_K_END 0x00932910u

static double d_val(void) {
    const uint32_t r = below(100);
    if (g_class == 0 || r < 60) return (double)f_gain();
    if (r < 80) return (double)f_edge();
    uint64_t b;
    do b = rnd64(); while ((b & 0x7FF0000000000000ull) == 0x7FF0000000000000ull);
    double d;
    memcpy(&d, &b, 8);
    return d;
}
static void putd(uint32_t a, double v) { if (a >= NULL_GUARD && a <= GUEST_END - 8) memcpy(G + a, &v, 8); }

/* A ring's indices as a stage or a comb chooses them: the write index at the wrap, the delay a
 * whole ring, zero or one, or anything. */
static void ring_indices(uint32_t mask, uint32_t *delay, uint32_t *wi) {
    switch (below(5)) {
    case 0: *delay = below(mask + 1u); break;
    case 1: { const uint32_t d[] = {0, 1, mask, mask + 1u, 2}; *delay = d[below(5)]; break; }
    case 2: *delay = rnd32(); break;
    case 3: *delay = below(4); break;
    default: *delay = below(mask + 1u); break;
    }
    switch (below(4)) {
    case 0: *wi = mask; break;
    case 1: *wi = rnd32(); break;
    case 2: *wi = 0; break;
    default: *wi = below(mask + 1u); break;
    }
}

/* The overlap cases' tallies: outputs that overlap without being one float, and the sNaN reloads
 * sought and found. */
static uint64_t partial_outputs, snan_sought, snan_found;
#define SNAN_CASES 512
#define SNAN_TRIES 8192

static int snan32(uint32_t b) { return (b & 0x7FC00000u) == 0x7F800000u && (b & 0x003FFFFFu) != 0; }

/* Redraws comb 0's delayed sample (its read cell `rd`, which the matrix reads back as that comb's
 * output) until the lifted body, in the host mode the case will run in, reloads a signalling NaN
 * from the first output `p0` (whose high half the second output's low half overwrote). */
static void net_snan_search(uint32_t rd, uint32_t p0) {
    void *const MEM = (void *)(uintptr_t)0x5A2Du;
    uint16_t cw;
    memcpy(&cw, S0 + O_CWD, 2);
    forced_mode = host_mode(cw);
    snan_sought++;
    for (int t = 0; t < SNAN_TRIES; t++) {
        if (t) putf(rd, f_audio());
        save(snap0);
        memcpy(SA, S0, STATE_BYTES);
        sa2_cri_dsp_mode = 0;
        fesetround(forced_mode);
        __wrap_sub_843460(SA, 0x00843460u, MEM);
        fesetround(FE_TONEAREST);
        uint32_t b;
        memcpy(&b, G + p0, 4);
        restore(snap0);
        memset(scratch, 0, sizeof scratch);
        scratch_touched = 0;
        if (snan32(b)) {
            snan_found++;
            return;
        }
    }
}

static void case_net(int guard) {
    g_class = below(3);
    const uint16_t cw = pick_cw(guard);
    const uint32_t ssb = pick_base(), dsb = pick_base();
    uint32_t o = 0x01000000u + (below(0x10000) << 4), e = addr_in(0x01200000u, 0x100000u);
    uint32_t base[15], mask[15], P[4];
    for (int j = 0; j < 15; j++) {
        const uint32_t r = below(20);
        mask[j] = r == 0 ? (1u << (8 + below(5))) - 1u : r == 1 ? (rnd32() & 0x3Fu) : (1u << below(7)) - 1u;
        base[j] = 0x01400000u + 0x10000u * (uint32_t)j + (below(0x400) << 2);
    }
    for (int i = 0; i < 4; i++) P[i] = 0x01600000u + (below(0x4000) << 2);
    if (below(5) == 0) P[1 + below(3)] = P[below(4)];          /* outputs that meet */
    /* An overlap that lands on the object lands on comb 4's biquad (its coefficients and state,
     * [co+28h, co+0A0h)), never on a field the guest computes an address from: the case's
     * windows are set from those. */
    const uint32_t bq4 = o + 0x1C + 0xB4u * 4 + 0x28;
    int kind = -1;
    if (guard == G_OVERLAP) {
        kind = (int)below(8);
        if (kind == 7 && snan_sought >= SNAN_CASES) kind = 5;
        const uint32_t j = below(15), k = (j + 1 + below(14)) % 15;
        if (kind == 0) base[k] = base[j] + 4 * below(mask[j] + 1u);           /* two rings meet */
        else if (kind == 1) { mask[j] = 3; base[j] = bq4 + 4 * below(0x1A); } /* a ring in the object */
        else if (kind == 2) P[below(4)] = e - 0x48 + 4 * below(0x19);         /* an output on the frame */
        else if (kind == 3) P[0] = dsb + NET_K_LO + 4 * below((NET_K_END - NET_K_LO) / 4); /* in the constants */
        else if (kind == 4) e = bq4 + 0x48 + 4 * below(6);                    /* the frame on the object */
        else if (kind < 7) {        /* two outputs that overlap, neither one float nor apart */
            const uint32_t i = below(4), j = (i + 1 + below(3)) % 4, d = 1 + below(3);
            P[j] = below(2) ? P[i] + d : P[i] - d;
            partial_outputs++;
        } else {                    /* the sNaN reload: [eax]'s high half is [ecx]'s low half */
            g_class = 0;
            P[1] = P[0] + 2;
            P[2] = P[0] + 0x40;
            P[3] = P[0] + 0x80;
            partial_outputs++;
        }
    }
    /* Past the end: the object's last coefficient (+744h) on the guest space's last bytes, so
     * the guest reads it on the checked path (the scratch page) and everything else in place. */
    if (guard == G_RANGE) o = GUEST_END - 0x744 + 4 * below(1);
    nw = 0;
    add_win(o, 0x760);
    add_win(e - 0x80, 0xC0);
    for (int i = 0; i < 4; i++) add_win(P[i] - 8, 0x14);
    for (int j = 0; j < 15; j++)
        if (mask[j] < 0x100u) add_win(base[j] - 8, 4 * (mask[j] + 1u) + 0x10);
    const int filled = nw;
    for (int i = 0; i < filled; i++) fill_random(W[i].lo, W[i].len);
    /* the stages */
    for (int k = 0; k < 6; k++) {
        uint32_t delay, wi;
        ring_indices(mask[k], &delay, &wi);
        put32(G + o + NET_STAGE_H[k][0], wi);
        put32(G + o + NET_STAGE_H[k][1], mask[k]);
        put32(G + o + NET_STAGE_H[k][2], base[k] - dsb);
        put32(G + o + NET_STAGE_H[k][3], delay);
    }
    for (unsigned i = 0; i < sizeof NET_COEF / sizeof NET_COEF[0]; i++) putf(o + NET_COEF[i], f_val(f_gain));
    /* the combs */
    for (int k = 0; k < 9; k++) {
        const uint32_t co = o + 0x1C + 0xB4u * (uint32_t)k;
        uint32_t delay, wi;
        ring_indices(mask[6 + k], &delay, &wi);
        put32(G + co + 0x04, base[6 + k] - dsb);
        put32(G + co + 0x0C, delay);
        put32(G + co + 0x10, wi);
        put32(G + co + 0x14, mask[6 + k]);
        putf(co + 0x1C, f_val(f_gain));
        bq_fill(co + 0x20);
    }
    /* the frame: the return address, the object, the sample, the outputs */
    if (e + 0x1C <= GUEST_END) {
        put32(G + e + 4, o - dsb);
        putf(e + 8, f_val(f_audio));
        for (int i = 0; i < 4; i++) put32(G + e + 0x0C + 4 * i, P[i] - dsb);
    }
    /* the constants */
    putf(dsb + NET_K32, f_val(f_gain));
    for (int i = 0; i < 8; i++) putd(dsb + NET_K64[i], d_val());
    for (int i = 0; i < 8; i++) add_win(dsb + NET_K64[i] - 4, 0x10);
    add_win(dsb + NET_K32 - 4, 0xC);
    /* the cells each ring reads, once every field is in place (one may have landed on another) */
    uint32_t comb0_rd = 0;
    for (int j = 0; j < 15; j++) {
        uint32_t ring, m, d, w;
        if (j < 6) {
            memcpy(&w, G + o + NET_STAGE_H[j][0], 4); memcpy(&m, G + o + NET_STAGE_H[j][1], 4);
            memcpy(&ring, G + o + NET_STAGE_H[j][2], 4); memcpy(&d, G + o + NET_STAGE_H[j][3], 4);
        } else {
            const uint32_t co = o + 0x1C + 0xB4u * (uint32_t)(j - 6);
            memcpy(&ring, G + co + 4, 4); memcpy(&d, G + co + 0xC, 4);
            memcpy(&w, G + co + 0x10, 4); memcpy(&m, G + co + 0x14, 4);
        }
        const uint32_t rd = dsb + ring + 4u * ((w - d + 1u) & m), wr = dsb + ring + 4u * ((w + 1u) & m);
        putf(rd, f_val(f_audio));
        if (j == 6) comb0_rd = rd;
        add_win(rd - 8, 0x14);                  /* where the guest will find them, whatever moved */
        add_win(wr - 8, 0x14);
    }
    if (guard == G_NONFINITE) {
        const uint32_t k = below(6);
        if (k == 0) putf(e + 8, f_nonfinite());
        else if (k == 1) putf(o + NET_COEF[below(sizeof NET_COEF / sizeof NET_COEF[0])], f_nonfinite());
        else if (k == 2) {
            const uint32_t j = below(9), co = o + 0x1C + 0xB4u * j;
            if (below(2)) putf(co + 0x1C, f_nonfinite());
            else bq_poison(co + 0x20);
        } else if (k == 3) putf(dsb + NET_K32, f_nonfinite());
        else if (k == 4) {
            double inf;
            const uint64_t b = 0x7FF0000000000000ull | (below(2) ? (rnd64() & 0xFFFFFFFFFFFFFull) | 1 : 0)
                             | ((uint64_t)below(2) << 63);
            memcpy(&inf, &b, 8);
            putd(dsb + NET_K64[below(8)], inf);
        } else {
            /* a stage's read cell */
            const uint32_t j = below(6);
            uint32_t ring, m, d, w;
            memcpy(&w, G + o + NET_STAGE_H[j][0], 4); memcpy(&m, G + o + NET_STAGE_H[j][1], 4);
            memcpy(&ring, G + o + NET_STAGE_H[j][2], 4); memcpy(&d, G + o + NET_STAGE_H[j][3], 4);
            putf(dsb + ring + 4u * ((w - d + 1u) & m), f_nonfinite());
        }
    }
    state_random(S0, cw, ssb, dsb);
    put32(S0 + O_ESP, e - ssb);
    snprintf(info, sizeof info, "o %#x e %#x ds %#x ss %#x kind %d P %#x %#x %#x %#x", o, e, dsb, ssb, kind,
             P[0], P[1], P[2], P[3]);
    if (kind == 7 && !bench_setup_only) net_snan_search(comb0_rd, P[0]);
    run_case(__wrap_sub_843460, 2, 0x00843460u, guard, guard == G_NOBASE);
}

/* ------------------------------------------------------------------ the mix */

static void case_mix(int guard) {
    g_class = below(3);
    const uint16_t cw = pick_cw(guard);
    const uint32_t ssb = pick_base(), dsb = pick_base();
    uint32_t n;
    const uint32_t nr = below(100);
    if (guard == G_NONFINITE || guard == G_OVERLAP || guard == G_RANGE) n = 1 + below(64);
    else if (nr < 8) n = 0;
    else if (nr < 70) n = 1 + below(64);
    else if (nr < 90) n = 8 * (1 + below(32));
    else n = 1 + below(1024);
    const uint32_t blocks = n ? ((n - 1u) >> 3) + 1u : 0, count = 8 * blocks, bytes = 4 * count;
    uint32_t sp = addr_in(0x01200000u, 0x100000u);
    uint32_t d = addr_in(0x01000000u, 0x100000u), s = addr_in(0x01400000u, 0x100000u);
    const uint32_t lay = below(10);
    if (lay < 2) s = d;                                         /* in place */
    if (guard == G_OVERLAP) {                                   /* overlapping, not the same */
        const uint32_t k = 4 * (1 + below(count - 1));
        s = below(2) ? d + k : d - k;
    }
    if (guard == G_RANGE) d = GUEST_END - bytes + 4 * (1 + below(4));
    nw = 0;
    add_win(sp - 0x40, 0x80);
    add_win(d - 0x20, bytes + 0x40);
    if (s != d) add_win(s - 0x20, bytes + 0x40);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    for (uint32_t i = 0; i < count; i++) {
        if (d + 4 * i + 4 <= GUEST_END) putf(d + 4 * i, f_val(f_audio));
        if (s != d && s + 4 * i + 4 <= GUEST_END) putf(s + 4 * i, f_val(f_audio));
    }
    const float gain = f_val(f_gain);
    put32(G + sp + 0x04, d - dsb);
    put32(G + sp + 0x08, s - dsb);
    put32(G + sp + 0x0C, n);
    putf(sp + 0x10, gain);
    if (guard == G_NONFINITE) {
        const uint32_t k = below(3);
        if (k == 0) putf(sp + 0x10, f_nonfinite());
        else if (k == 1) putf(d + 4 * below(count), f_nonfinite());
        else putf(s + 4 * below(count), f_nonfinite());
    }
    state_random(S0, cw, ssb, dsb);
    put32(S0 + O_ESP, sp - ssb);
    run_case(__wrap_sub_8418b0, 3, 0x008418B0u, guard, guard == G_NOBASE);
}

/* ------------------------------------------------------------------ main */

/* `bench KERNEL CALLS SEED`: one realistic case, called CALLS times with the native bodies off and
 * then on (the State and memory evolving as the guest's would: the comb's ring index advances);
 * ns per call each way, and how many calls took the native body. */
static double now_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e9 + t.tv_nsec;
}
static void case_net(int guard);

/* The network's realistic setup, for its bench and `calls`. */
static void bench_net_setup(void) {
    rs = 7;
    do {
        bench_setup_only = 1;
        case_net(G_NONE);
        bench_setup_only = 0;
    } while (g_class != 0);
    uint32_t esp0, ss, ds;
    memcpy(&esp0, S0 + O_ESP, 4);
    memcpy(&ss, S0 + O_SS, 4);
    memcpy(&ds, S0 + O_DS, 4);
    const uint32_t e = ss + esp0;
    /* every ring cell a float in range, and the combs' filters stable, as the game's are */
    {
        uint32_t o;
        memcpy(&o, G + e + 4, 4);
        o += ds;
        for (int j = 0; j < 15; j++) {
            uint32_t ring, m;
            if (j < 6) { memcpy(&ring, G + o + NET_STAGE_H[j][2], 4); memcpy(&m, G + o + NET_STAGE_H[j][1], 4); }
            else { const uint32_t co = o + 0x1C + 0xB4u * (uint32_t)(j - 6); memcpy(&ring, G + co + 4, 4); memcpy(&m, G + co + 0x14, 4); }
            for (uint32_t i = 0; i <= m; i++) putf(ds + ring + 4 * i, f_audio());
        }
        for (int k = 0; k < 9; k++) {
            const uint32_t co = o + 0x1C + 0xB4u * (uint32_t)k;
            for (unsigned i = 0; i < 6; i++) putf(co + 0x20 + BQ_COEF[i], (float)(0.1 * f_audio()));
            putf(co + 0x1C, 0.5f);
        }
        for (unsigned i = 0; i < sizeof NET_COEF / sizeof NET_COEF[0]; i++) putf(o + NET_COEF[i], (float)(0.5 * f_audio()));
    }
}

/* The network's bench: a realistic case, called again and again with the frame's arguments put
 * back (the network overwrites two of them) and ESP reset. */
static int bench_net(uint64_t calls) {
    double ns[2];
    uint64_t native = 0;
    for (int mode = 0; mode < 2; mode++) {
        bench_net_setup();
        uint32_t esp0, ss, ds;
        memcpy(&esp0, S0 + O_ESP, 4);
        memcpy(&ss, S0 + O_SS, 4);
        memcpy(&ds, S0 + O_DS, 4);
        const uint32_t e = ss + esp0;
        uint8_t args[8];
        memcpy(args, G + e + 4, 8);
        memcpy(SA, S0, STATE_BYTES);
        sa2_cri_dsp_mode = mode ? 15 : 0;
        const uint64_t before = sa2_cri_dsp_calls[2][0];
        uint16_t cw;
        memcpy(&cw, S0 + O_CWD, 2);
        fesetround(host_mode(cw));
        const double t0 = now_ns();
        for (uint64_t c = 0; c < calls; c++) {
            put32(SA + O_ESP, esp0);
            memcpy(G + e + 4, args, 8);
            bench_fn(SA, bench_pc, (void *)1);
        }
        ns[mode] = (now_ns() - t0) / (double)calls;
        fesetround(FE_TONEAREST);
        if (mode) native = sa2_cri_dsp_calls[2][0] - before;
    }
    printf("{\"kernel\": \"net\", \"calls\": %llu, \"ns_lifted\": %.1f, \"ns_native\": %.1f, \"native_calls\": %llu}\n",
           (unsigned long long)calls, ns[0], ns[1], (unsigned long long)native);
    return 0;
}

/* The bench's realistic setup of the biquad (ki 0), the comb (1) or the mix (3), at the kernel's
 * own shape, PC53: the object at BENCH_O, the stack at BENCH_SP. */
#define BENCH_O 0x01000100u
#define BENCH_SP 0x01200100u
static void bench_setup(int ki) {
    rs = 99;
    g_class = 0;
    const uint32_t o = BENCH_O, sp = BENCH_SP, ring = 0x01400000u;
    nw = 0;
    add_win(o - 0x40, 0x200);
    add_win(sp - 0x40, 0x80);
    add_win(ring, 0x1000);
    for (int i = 0; i < nw; i++) fill_random(W[i].lo, W[i].len);
    for (uint32_t i = 0; i < 0x400; i++) putf(ring + 4 * i, f_audio());
    state_random(S0, 0x027F, 0, 0);
    /* a stable filter: small coefficients, so the state stays finite over the calls */
    for (unsigned i = 0; i < 6; i++) putf((ki == 1 ? o + 0x20 : o) + BQ_COEF[i], (float)(0.1 * f_audio()));
    for (unsigned i = 0; i < 4; i++) putf((ki == 1 ? o + 0x20 : o) + BQ_Z[i], f_audio());
    if (ki == 0) { putf(sp + 4, f_audio()); put32(S0 + O_ECX, o); put32(S0 + O_ESP, sp); }
    else if (ki == 1) {
        put32(G + o + 0x04, ring); put32(G + o + 0x0C, 300); put32(G + o + 0x10, 5);
        put32(G + o + 0x14, 0x3FF); putf(o + 0x1C, 0.5f);
        putf(sp + 4, f_audio()); put32(S0 + O_ECX, o); put32(S0 + O_ESP, sp);
    } else if (ki == 3) {
        put32(G + sp + 4, ring); put32(G + sp + 8, ring + 0x800); put32(G + sp + 0xC, 256);
        putf(sp + 0x10, 0.5f); put32(S0 + O_ESP, sp);
    }
}

static int bench(const char *k, uint64_t calls) {
    if (!strcmp(k, "net")) return bench_net(calls);
    kernel_fn fn;
    uint32_t pc;
    int ki;
    if (!strcmp(k, "biquad")) { fn = __wrap_sub_842500; pc = 0x00842500u; ki = 0; }
    else if (!strcmp(k, "comb")) { fn = __wrap_sub_843400; pc = 0x00843400u; ki = 1; }
    else { fn = __wrap_sub_8418b0; pc = 0x008418B0u; ki = 3; }
    const uint32_t o = BENCH_O, sp = BENCH_SP;
    double ns[2];
    uint64_t native = 0;
    for (int mode = 0; mode < 2; mode++) {
        bench_setup(ki);
        sa2_cri_dsp_mode = mode ? 15 : 0;
        const uint64_t before = sa2_cri_dsp_calls[ki][0];
        const double t0 = now_ns();
        memcpy(SA, S0, STATE_BYTES);
        const uint32_t esp0 = sp;
        for (uint64_t c = 0; c < calls; c++) {
            put32(SA + O_ESP, esp0);            /* the call's `ret` moved it; the rest carries on */
            put32(SA + O_ECX, o);               /* the comb leaves ecx at its biquad */
            fn(SA, pc, (void *)1);
        }
        ns[mode] = (now_ns() - t0) / (double)calls;
        if (mode) native = sa2_cri_dsp_calls[ki][0] - before;
    }
    printf("{\"kernel\": \"%s\", \"calls\": %llu, \"ns_lifted\": %.1f, \"ns_native\": %.1f, \"native_calls\": %llu}\n",
           k, (unsigned long long)calls, ns[0], ns[1], (unsigned long long)native);
    return 0;
}

/* `calls`: one realistic call of each kernel, `sa2_cri_dsp_mode` left at -1 so that the first
 * call reads SA2_CRI_DSP; the counters, per kernel, as [native, lifted]. */
static int calls(void) {
    static const char *const name[4] = {"biquad", "comb", "net", "mix"};
    static kernel_fn const fn[4] = {__wrap_sub_842500, __wrap_sub_843400, __wrap_sub_843460,
                                    __wrap_sub_8418b0};
    static const uint32_t pc[4] = {0x00842500u, 0x00843400u, 0x00843460u, 0x008418B0u};
    void *const MEM = (void *)(uintptr_t)0x5A2Du;
    int ok = 1;
    for (int ki = 0; ki < 4; ki++) {
        if (ki == 2) bench_net_setup();
        else bench_setup(ki);
        memcpy(SA, S0, STATE_BYTES);
        uint16_t cw;
        memcpy(&cw, S0 + O_CWD, 2);
        fesetround(host_mode(cw));
        ok &= fn[ki](SA, pc[ki], MEM) == MEM;
        fesetround(FE_TONEAREST);
    }
    printf("{\"mode\": %d, \"ok\": %s, \"calls\": {", sa2_cri_dsp_mode, ok ? "true" : "false");
    for (int k = 0; k < 4; k++)
        printf("%s\"%s\": [%llu, %llu]", k ? ", " : "", name[k],
               (unsigned long long)sa2_cri_dsp_calls[k][0], (unsigned long long)sa2_cri_dsp_calls[k][1]);
    printf("}}\n");
    return 0;
}

static int layout(void) {
    int bad = 0;
    printf("{\"state_bytes\": %u, \"fields\": {", sa2_remill_state_size());
    for (uint32_t i = 0; i < sa2_cri_dsp_layout_count; i++) {
        const int want = sa2_remill_offset(sa2_cri_dsp_layout[i].field);
        printf("%s\"%s\": [%u, %d]", i ? ", " : "", sa2_cri_dsp_layout[i].field,
               sa2_cri_dsp_layout[i].offset, want);
        bad |= want < 0 || (uint32_t)want != sa2_cri_dsp_layout[i].offset;
    }
    printf("}, \"ok\": %s}\n", !bad && sa2_remill_state_size() == STATE_BYTES ? "true" : "false");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: cri_dsp_diff layout | biquad|comb|net|mix CASES SEED | bench KERNEL CALLS | calls\n");
        return 2;
    }
    O_CWD = off("x87.fxsave.cwd"); O_SWD = off("x87.fxsave.swd"); O_SS = off("addr.ss_base");
    O_DS = off("addr.ds_base"); O_ECX = off("gpr.rcx"); O_ESP = off("gpr.rsp");
    O_ESI = off("gpr.rsi"); O_ST0 = off("st.elems[0].val");
    if (!strcmp(argv[1], "layout")) return layout();
    if (argc < 4 && strcmp(argv[1], "calls")) return 2;
    /* The guest space and 64 KiB past it, where a case that reaches past the end has its setup
     * written (the guest's accesses there take the checked path, to the scratch page). */
#ifdef _WIN32
    G = VirtualAlloc(NULL, GUEST_END + 0x10000u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!G) { fprintf(stderr, "VirtualAlloc failed\n"); return 2; }
#else
    G = mmap(NULL, GUEST_END + 0x10000u, PROT_READ | PROT_WRITE,
             MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (G == MAP_FAILED) { perror("mmap"); return 2; }
#endif
    sa2_guest_fast_base = G;
    fesetround(FE_TONEAREST);
    if (!strcmp(argv[1], "calls")) return calls();
    if (!strcmp(argv[1], "bench")) return bench(argv[2], strtoull(argv[3], NULL, 0));
    const uint64_t ordinary = strtoull(argv[2], NULL, 0);
    rs = strtoull(argv[3], NULL, 0);
    void (*one)(int);
    if (!strcmp(argv[1], "biquad")) one = case_biquad;
    else if (!strcmp(argv[1], "comb")) one = case_comb;
    else if (!strcmp(argv[1], "mix")) one = case_mix;
    else if (!strcmp(argv[1], "net")) one = case_net;
    else return 2;
    const uint64_t guards = ordinary / 16 * (G_KINDS - 1);
    const int trace = getenv("CRI_DSP_DIFF_TRACE") != NULL;
    if (getenv("CRI_DSP_DIFF_MASK")) cri_mask = (int)strtol(getenv("CRI_DSP_DIFF_MASK"), NULL, 0);
    for (case_no = 0; case_no < ordinary + guards; case_no++) {
        const int guard = pick_guard(case_no, ordinary);
        if (trace) fprintf(stderr, "case %llu %s\n", (unsigned long long)case_no, GUARD_NAME[guard]);
        one(guard);
    }
    uint64_t mism = 0;
    printf("{\"kernel\": \"%s\", \"seed\": %s, \"classes\": {", argv[1], argv[3]);
    for (int k = 0; k < G_KINDS; k++) {
        mism += T[k].mismatches;
        printf("%s\"%s\": {\"cases\": %llu, \"native\": %llu, \"native_pc24\": %llu, "
               "\"native_pc53_64\": %llu, \"mismatches\": %llu}", k ? ", " : "", GUARD_NAME[k],
               (unsigned long long)T[k].cases, (unsigned long long)T[k].native,
               (unsigned long long)T[k].native_pc24, (unsigned long long)T[k].native_pc53,
               (unsigned long long)T[k].mismatches);
    }
    printf("}, \"ordinary_by_values\": {");
    for (int c = 0; c < 3; c++)
        printf("%s\"%s\": {\"cases\": %llu, \"native\": %llu}", c ? ", " : "", CLASS_NAME[c],
               (unsigned long long)by_class[c][0], (unsigned long long)by_class[c][1]);
    printf("}, \"partial_outputs\": %llu, \"snan_reloads\": {\"sought\": %llu, \"found\": %llu}",
           (unsigned long long)partial_outputs, (unsigned long long)snan_sought,
           (unsigned long long)snan_found);
    printf(", \"mismatches\": %llu, \"first_mismatch\": \"%s\"}\n", (unsigned long long)mism, first);
    return 0;
}
