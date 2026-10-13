/* The word log: every word of `.dataa` at every tick, as one keyframe and, per
 * tick, the words that changed since the tick before.
 *
 * Why it exists. A replay's tick capture compares 64 KiB digests of the words
 * a mask selects, and the user's rulings of 2026-09-24 need more than that: a
 * stage mask chosen word by word from runs configured exactly like the
 * comparison runs (Documentation/decisions/2026-09-24-S4-halt-220h.md), and the
 * handle and heap words compared by what they name
 * (Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md). Both
 * need every word at every tick, on both sides, observed the same way. The
 * design is private/generated/s4/wordobs/reports/judge-plan.md: the tick probe
 * keeps the log in memory and it leaves the process once, so the observation
 * opens no handle and makes no OS call while the game runs, and the digest
 * columns are computed afterwards, by the host, from the log.
 *
 * This file is the log's one definition: its format, and the writer that the
 * native tick probe (native_tick_probe.c) runs and that anything else observing
 * the section the same way runs too. tools/s4/wordlog.c reads the format from
 * here (WL_FORMAT_ONLY). The includer of the writer declares, before
 * including, and defines, anywhere after:
 *
 *     static const uint32_t *wl_section(void);   host pointer to WL_BASE's word
 *     static uint64_t wl_clock(void);            a counter that only goes up
 *
 * and supplies the memory: a shadow of WL_SIZE bytes and a buffer. The writer
 * copies, compares and appends; it calls nothing else, so every OS call, and
 * the moment the buffer leaves the process, are the includer's to decide and
 * to state. The native probe keeps the whole log in the buffer and writes it
 * once. An includer that must stream instead -- one whose run can end without
 * a last call, as a lifted run ending in a trap does -- writes out buf[0, len)
 * after any whole record and calls wl_drained; the bytes are the same either
 * way, and the end record's offset counts the drained ones.
 *
 * Format, little-endian, a sequence of records:
 *
 *   "SA2F"  u32 version (2), u32 base, u32 size, u32 buffer_bytes, u32 end_rule,
 *           u32 tick_limit, u32 line_words, u32 0, u64 ref_hz, u64 ref_init,
 *           u64 clk_init                                          (60 bytes)
 *   "SA2O"  u32 count, count x {u32 base, u32 size}: the blocks the writer's
 *           includer allocated for the log (the shadow and the buffer), which
 *           the region maps list with the game's memory; a reader's pointer test
 *           leaves them out, since the game holds no address inside them and a
 *           value that falls there is data (optional; sizes as requested)
 *   "SA2R"  u32 tick, u32 count, u32 more, count x {u32 base, u32 size}: the
 *           process's committed regions (native_snapshot_probe.c's record; `more`
 *           is 1 when there were more than WL_MAX_REGIONS)
 *   "SA2K"  u32 0, u32 base, u32 size, then size bytes: the section at Init
 *   "SA2D"  u32 tick, then runs {u32 first word, u32 count, count x u32 value}
 *           in ascending order, each a maximal stretch of changed words, then
 *           u32 0xFFFFFFFF, u32 nruns, u32 nwords, u64 t_enter, u64 t_row,
 *           u64 t_scan (wl_clock at the includer's tick entry, after its row,
 *           and after the scan)
 *   "SA2V"  u32 last_tick, u32 unscanned, u32 0, u64 shadow_hash: the
 *           end-of-run check (wl_verify). shadow_hash is wl_hash over the
 *           shadow, which a reader compares with the words it rebuilds at
 *           last_tick; unscanned counts the words where a plain loop over the
 *           whole section, lines and tail alike, finds the section differing
 *           from the shadow (other threads' writes since the scan, and any word
 *           the scan missed)                                     (24 bytes)
 *   "SA2E"  u32 last_tick, u32 overflow_tick, u32 reason, u32 0, u64 ref_end,
 *           u64 clk_end, u64 the offset of this record             (44 bytes)
 *
 * A log is the header, the writer's own blocks, a region map at Init (tick
 * 0), the keyframe, one SA2D per tick from 1, the end-of-run check, a region
 * map at the last tick, and the end record, which is last. The own blocks,
 * the maps and the check are each optional in the format; the native probe
 * writes all of them, and tools/s4/wordlog.py requires them of a native log.
 * The end-of-run check is not written after an overflow: the tick that did not
 * fit had already set some of the shadow's words when its record was dropped.
 * Every field shown as 0 is 0, and line_words is WL_LINE_WORDS; a reader
 * refuses anything else. An overflow_tick that is not 0 is last_tick + 1.
 * clk_* are wl_clock's counts; ref_* a reference clock's, at Init and at
 * the end, from which a reader gets wl_clock's rate. A word's value at tick t
 * is the keyframe's with every SA2D up to t applied: the value the scan read
 * after the includer's row for t, which natively is the value at the end of
 * GameLoop's return t, give or take what other threads wrote during the scan.
 *
 * The buffer is one size for every run, WL_BUFFER_BYTES, because its
 * reservation moves the addresses of what is allocated after it (13 top-down
 * words at the start of `.dataa` differed between two prototype runs with two
 * sizes; design-in-probe-delta.md). A tick that does not fit is not recorded:
 * the log ends at the tick before, its end record names the tick that did not
 * fit, and tools/s4/wordlog.py refuses the capture. The end records have a
 * reserve of their own, so an overflowing log still ends whole.
 */
#ifndef SA2_WORD_LOG_CORE_H
#define SA2_WORD_LOG_CORE_H

#include <stdint.h>
#include <string.h>

#ifndef WL_BASE
#define WL_BASE 0x00917000u         /* .dataa, the executable's writable section */
#endif
#ifndef WL_SIZE
#define WL_SIZE 0x01D59598u
#endif
#define WL_WORDS (WL_SIZE / 4u)
#define WL_LINE_WORDS 16u           /* the scan compares 64 bytes at a time */
#define WL_VERSION 2u
#define WL_FILE "mods/sa2-word-log.bin"

/* The one buffer size (the comment above). Metal Harbor's log was 98.8 MB and
 * the boot's 38.9 MB in the prototype runs (judge-plan.md); 192 MiB is the
 * judge's figure. tools/s4/wordlog.py reads this line and refuses a log whose
 * header records another size. */
#ifndef WL_BUFFER_BYTES
#define WL_BUFFER_BYTES (192u << 20)
#endif

#define WL_MAX_REGIONS 4096u
#define WL_HEADER_BYTES 60u
#define WL_OWN_BYTES 24u            /* SA2O with its two blocks */
#define WL_REGIONS_BYTES (16u + 8u * WL_MAX_REGIONS)
#define WL_VERIFY_BYTES 24u
#define WL_END_BYTES 44u
#define WL_END_RESERVE (WL_VERIFY_BYTES + WL_REGIONS_BYTES + WL_END_BYTES)
#define WL_TICK_TRAILER_BYTES 36u   /* 0xFFFFFFFF, nruns, nwords, three clocks */

/* end_rule, in the header: what was to end the log. */
#define WL_RULE_ON_EXIT 0u          /* nothing but OnExit */
#define WL_RULE_TICK_LIMIT 1u       /* the includer's TICK_LIMIT */
#define WL_RULE_INPUT_END 2u        /* the tick before the input probe's SA2InputProbeEnd */

/* reason, in the end record: what did end it. */
#define WL_END_TICK_LIMIT 1u
#define WL_END_INPUT_END 2u
#define WL_END_ON_EXIT 3u
#define WL_END_REFUSED 4u           /* the includer could not keep the log; no tick is in it */

/* The 64-bit FNV-1a over whole words in address order, with the standard
 * offset basis and prime: SA2V's shadow_hash, and the reader's hash of the
 * section at a tick (tools/s4/wordlog.c `hashes`). */
#define WL_HASH_BASIS 14695981039346656037ull
#define WL_HASH_PRIME 1099511628211ull
__attribute__((unused)) static uint64_t wl_hash(const uint32_t *w, uint32_t n) {
    uint64_t h = WL_HASH_BASIS;
    for (uint32_t i = 0; i < n; ++i)
        h = (h ^ w[i]) * WL_HASH_PRIME;
    return h;
}

#ifndef WL_FORMAT_ONLY

#include <emmintrin.h>

typedef struct {
    uint32_t end_rule, tick_limit;
    uint64_t ref_hz, ref_init, clk_init;
} WlStart;

typedef struct {
    uint8_t *buf;
    size_t size;            /* bytes of buf */
    size_t cap;             /* bytes everything before the end records may use */
    size_t len;             /* bytes written and still in buf */
    uint64_t drained;       /* bytes the includer took out of buf before them (wl_drained) */
    uint32_t *shadow;       /* the section as the log last recorded it */
    uint32_t last_tick;     /* the last tick recorded whole; 0 before the first */
    uint32_t overflow_tick; /* the first tick that did not fit; 0 if none */
    size_t region_at;       /* the open SA2R record */
    uint32_t region_count, region_more;
    int closing;            /* the end records are being written: the reserve is open */
} WlLog;

typedef struct {
    size_t at;              /* the open run's first-word field; 0 if no run is open */
    uint32_t next;          /* the word that would extend it */
    uint32_t nruns, nwords;
} WlRun;

static int wl_fits(const WlLog *L, size_t n) {
    const size_t limit = L->closing ? L->size : L->cap;
    return L->len <= limit && n <= limit - L->len;
}

static void wl_put(WlLog *L, const void *p, size_t n) {
    memcpy(L->buf + L->len, p, n);
    L->len += n;
}

static void wl_put32(WlLog *L, uint32_t v) { wl_put(L, &v, 4); }
static void wl_put64(WlLog *L, uint64_t v) { wl_put(L, &v, 8); }

/* The header. 0, and nothing written, if the buffer cannot hold what Init
 * writes -- the header, the own blocks, a whole region map and the keyframe
 * (wl_keyframe writes without asking, on the strength of this) -- and the
 * end reserve. */
#define WL_MIN_BUFFER_BYTES \
    ((size_t)WL_HEADER_BYTES + WL_OWN_BYTES + WL_REGIONS_BYTES + 16u + WL_SIZE + WL_END_RESERVE)
static int wl_begin(WlLog *L, uint8_t *buf, size_t size, uint32_t *shadow, const WlStart *s) {
    memset(L, 0, sizeof *L);
    if (!buf || !shadow || size < WL_MIN_BUFFER_BYTES)
        return 0;
    L->buf = buf;
    L->size = size;
    L->cap = size - WL_END_RESERVE;
    L->shadow = shadow;
    wl_put(L, "SA2F", 4);
    wl_put32(L, WL_VERSION);
    wl_put32(L, WL_BASE);
    wl_put32(L, WL_SIZE);
    wl_put32(L, (uint32_t)size);
    wl_put32(L, s->end_rule);
    wl_put32(L, s->tick_limit);
    wl_put32(L, WL_LINE_WORDS);
    wl_put32(L, 0);
    wl_put64(L, s->ref_hz);
    wl_put64(L, s->ref_init);
    wl_put64(L, s->clk_init);
    return 1;
}

/* A region map: wl_regions_open, wl_region per committed region, then
 * wl_regions_close. Regions past WL_MAX_REGIONS are counted as `more`. The
 * maps are optional, as wl_drained is: marked unused so an includer that has
 * no regions to give, or never streams, still builds with -Werror. */
__attribute__((unused)) static void wl_regions_open(WlLog *L, uint32_t tick) {
    L->region_count = L->region_more = 0;
    L->region_at = 0;
    if (!wl_fits(L, 16))
        return;
    L->region_at = L->len;
    wl_put(L, "SA2R", 4);
    wl_put32(L, tick);
    wl_put32(L, 0);
    wl_put32(L, 0);
}

__attribute__((unused)) static void wl_region(WlLog *L, uint32_t base, uint32_t size) {
    if (!L->region_at)
        return;
    if (L->region_count >= WL_MAX_REGIONS || !wl_fits(L, 8)) {
        L->region_more = 1;
        return;
    }
    wl_put32(L, base);
    wl_put32(L, size);
    ++L->region_count;
}

__attribute__((unused)) static void wl_regions_close(WlLog *L) {
    if (!L->region_at)
        return;
    memcpy(L->buf + L->region_at + 8, &L->region_count, 4);
    memcpy(L->buf + L->region_at + 12, &L->region_more, 4);
    L->region_at = 0;
}

/* The blocks the log lives in, as the includer allocated them (SA2O): right
 * after the header. Optional, as the region maps are. */
__attribute__((unused)) static void wl_own(WlLog *L, const void *shadow, uint32_t shadow_bytes,
                                           const void *buf, uint32_t buf_bytes) {
    if (!wl_fits(L, WL_OWN_BYTES))
        return;
    wl_put(L, "SA2O", 4);
    wl_put32(L, 2);
    wl_put32(L, (uint32_t)(uintptr_t)shadow);
    wl_put32(L, shadow_bytes);
    wl_put32(L, (uint32_t)(uintptr_t)buf);
    wl_put32(L, buf_bytes);
}

/* The keyframe: the section now, which the shadow starts as. */
static void wl_keyframe(WlLog *L) {
    memcpy(L->shadow, (const void *)wl_section(), WL_SIZE);
    wl_put(L, "SA2K", 4);
    wl_put32(L, 0);
    wl_put32(L, WL_BASE);
    wl_put32(L, WL_SIZE);
    wl_put(L, L->shadow, WL_SIZE);
}

/* Whether 64 bytes differ: the prototype's SSE2 compare, as measured
 * (design-in-probe-delta.md: 0.63 to 0.75 ms a tick over the section). */
__attribute__((target("sse2")))
static int wl_line_differs(const uint32_t *a, const uint32_t *b) {
    __m128i x = _mm_xor_si128(_mm_loadu_si128((const __m128i *)(const void *)a),
                              _mm_loadu_si128((const __m128i *)(const void *)b));
    x = _mm_or_si128(x, _mm_xor_si128(_mm_loadu_si128((const __m128i *)(const void *)(a + 4)),
                                      _mm_loadu_si128((const __m128i *)(const void *)(b + 4))));
    x = _mm_or_si128(x, _mm_xor_si128(_mm_loadu_si128((const __m128i *)(const void *)(a + 8)),
                                      _mm_loadu_si128((const __m128i *)(const void *)(b + 8))));
    x = _mm_or_si128(x, _mm_xor_si128(_mm_loadu_si128((const __m128i *)(const void *)(a + 12)),
                                      _mm_loadu_si128((const __m128i *)(const void *)(b + 12))));
    return _mm_movemask_epi8(_mm_cmpeq_epi8(x, _mm_setzero_si128())) != 0xFFFF;
}

static void wl_run_close(WlLog *L, WlRun *r) {
    if (!r->at)
        return;
    uint32_t first;
    memcpy(&first, L->buf + r->at, 4);
    const uint32_t count = r->next - first;
    memcpy(L->buf + r->at + 4, &count, 4);
    r->at = 0;
}

/* One word as the scan read it: appended, and the shadow set to it, if it is
 * not what the shadow holds. 0 if the log is full. */
static int wl_word(WlLog *L, WlRun *r, uint32_t i, uint32_t v) {
    if (v == L->shadow[i])
        return 1;
    if (r->at && i == r->next) {
        if (!wl_fits(L, 4))
            return 0;
    } else {
        if (!wl_fits(L, 12))
            return 0;
        wl_run_close(L, r);
        r->at = L->len;
        wl_put32(L, i);
        wl_put32(L, 0);             /* the count, set when the run closes */
        ++r->nruns;
    }
    wl_put32(L, v);
    r->next = i + 1;
    ++r->nwords;
    L->shadow[i] = v;               /* the shadow update */
    return 1;
}

/* This tick's record: every word of the section that is not what the shadow
 * holds, read once each. 1 if it was recorded; 0 if it did not fit, and then
 * the log keeps only the ticks before it and records no later one. */
static int wl_tick(WlLog *L, uint32_t tick, uint64_t t_enter, uint64_t t_row) {
    if (!L->buf || L->closing || L->overflow_tick)
        return 0;
    const volatile uint32_t *game = (const volatile uint32_t *)wl_section();
    const uint32_t lines = WL_WORDS / WL_LINE_WORDS;
    const size_t start = L->len;
    WlRun r = {0, 0, 0, 0};
    if (!wl_fits(L, 8))
        goto full;
    wl_put(L, "SA2D", 4);
    wl_put32(L, tick);
    for (uint32_t l = 0; l < lines; ++l) {
        const uint32_t i0 = l * WL_LINE_WORDS;
        if (!wl_line_differs((const uint32_t *)game + i0, L->shadow + i0))
            continue;
        for (uint32_t i = i0; i < i0 + WL_LINE_WORDS; ++i)
            if (!wl_word(L, &r, i, game[i]))
                goto full;
    }
    /* the tail: the words after the last whole line */
    for (uint32_t i = lines * WL_LINE_WORDS; i < WL_WORDS; ++i)
        if (!wl_word(L, &r, i, game[i]))
            goto full;
    if (!wl_fits(L, WL_TICK_TRAILER_BYTES))
        goto full;
    wl_run_close(L, &r);
    wl_put32(L, 0xFFFFFFFFu);
    wl_put32(L, r.nruns);
    wl_put32(L, r.nwords);
    wl_put64(L, t_enter);
    wl_put64(L, t_row);
    wl_put64(L, wl_clock());
    L->last_tick = tick;
    return 1;
full:
    L->len = start;
    L->overflow_tick = tick;
    return 0;
}

/* The includer has written buf[0, len) out: the buffer starts again, and the
 * log's offsets go on from where it left off. Only between records. */
__attribute__((unused)) static void wl_drained(WlLog *L) {
    L->drained += L->len;
    L->len = 0;
}

/* From here on only the end records are written, into the reserve: the
 * optional end-of-run check and region map, then wl_end. */
static void wl_close(WlLog *L) {
    L->closing = 1;
}

/* The end-of-run check (SA2V), after wl_close and before the end map: the
 * shadow's hash, and the words a plain loop over the whole section finds
 * differing from the shadow -- a loop that shares nothing with the scan's
 * lines and tail, so a word the scan never looks at shows here if the game
 * changed it. Not written after an overflow (the format comment says why).
 * It reads the section and hashes the shadow: two passes over WL_SIZE, once. */
__attribute__((unused)) static void wl_verify(WlLog *L) {
    if (!L->buf || L->overflow_tick || !wl_fits(L, WL_VERIFY_BYTES))
        return;
    const volatile uint32_t *game = (const volatile uint32_t *)wl_section();
    uint32_t unscanned = 0;
    for (uint32_t i = 0; i < WL_WORDS; ++i)
        unscanned += game[i] != L->shadow[i];
    wl_put(L, "SA2V", 4);
    wl_put32(L, L->last_tick);
    wl_put32(L, unscanned);
    wl_put32(L, 0);
    wl_put64(L, wl_hash(L->shadow, WL_WORDS));
}

static void wl_end(WlLog *L, uint32_t reason, uint64_t ref_end, uint64_t clk_end) {
    L->closing = 1;
    const uint64_t at = L->drained + L->len;
    wl_put(L, "SA2E", 4);
    wl_put32(L, L->last_tick);
    wl_put32(L, L->overflow_tick);
    wl_put32(L, reason);
    wl_put32(L, 0);
    wl_put64(L, ref_end);
    wl_put64(L, clk_end);
    wl_put64(L, at);
}

#endif /* WL_FORMAT_ONLY */
#endif
