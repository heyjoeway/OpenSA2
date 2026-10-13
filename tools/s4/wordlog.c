/* The host reader of the word log (harness/replay/word_log_core.h).
 *
 * Built by tools/s4/wordlog.py, which is the way to run it: it compiles this
 * file against one digest mask, copied beside it as tick_digest.h, with
 * harness/replay's tick_probe_core.h, tick_fields.h and word_log_core.h, and
 * caches the program by their contents. The digest columns it writes are
 * computed by tick_probe_core.h's own td_digest and printed by its own
 * tp_write_row, over the words the log rebuilds for each tick, so they are what
 * the tick probe would have written had it computed them when its scan ran.
 *
 *   wordlog info    LOG                  what the log is and whether it is whole
 *   wordlog stats   LOG                  per tick: runs, words, bytes, the three clocks
 *   wordlog csv     LOG                  the tick capture, named fields and digest columns
 *   wordlog fields  LOG VA:WIDTH...      per tick, those bytes as tp_read reads them
 *   wordlog state   LOG TICK OUT         the section at TICK (0: the keyframe), raw
 *   wordlog hashes  LOG                  per tick, wl_hash of the whole section
 *   wordlog changes LOG LO HI            every change of a word in [LO, HI): tick,va,old,new
 *   wordlog diff    LOGA LOGB [LO HI] [TICKS]
 *                                        every word of [LO, HI) the two logs disagree on
 *   wordlog regions LOG                  the region maps
 *   wordlog touched LOG                  every word that changes after the keyframe, and on
 *                                        how many ticks
 *   wordlog pointers LOG [SMALL [BELOW [CUT_LO CUT_HI]]]   (BELOW "-": none)
 *                                        every word that ever holds a value of at least SMALL
 *                                        (0x10000) inside a region either map lists as
 *                                        committed (and starting below BELOW, if given), less
 *                                        the log's own blocks and, if given, the span
 *                                        [CUT_LO, CUT_HI), and the first tick it does
 *                                        (0: keyframe); data_tick: the first tick it
 *                                        holds a value neither zero nor an address by
 *                                        that test (empty: never)
 *
 * A log is read strictly: the header's line_words is WL_LINE_WORDS and every
 * field the format gives as 0 is 0; the ticks run 1, 2, 3... with no gap, each
 * record's runs ascend without touching, its trailer counts what it holds; the
 * end-of-run check, if there is one, names the last tick and its hash is the
 * hash of the words rebuilt there; and the end record is last, whole, where it
 * says it is, and names the last tick and, if any, an overflow at the tick
 * after it. Where the log stops being that, reading stops and `info` says where
 * and why; every mode prints what it read up to there and exits 3, so a caller
 * cannot take a part for the whole. A header, keyframe or own-block record
 * that is not the format's is refused at once (exit 2). A word a record names
 * with the value it already held is counted (noop_words): the writer never
 * logs one, so a count above 0 means its shadow went wrong. The prototype's
 * version-1 logs (design-in-probe-delta.md) are read too.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define WL_FORMAT_ONLY 1
#include "word_log_core.h"
#include "tick_digest.h"

/* ---------------------------------------------------------------- the words
 * The rebuilt section, inside zeros that cover every digest block, so
 * tick_probe_core.h reads the section's words where the game's would be. A
 * mask word outside the section, or a named field, is refused at start, never
 * read as a zero. */
static uint8_t *pad;
static uint32_t pad_lo, pad_hi;
static uint32_t *state;

static uint32_t tp_read(uint32_t va, unsigned width) {
    uint32_t value = 0;
    for (unsigned i = 0; i < width; ++i)
        value |= (uint32_t)pad[va + i - pad_lo] << (8 * i);
    return value;
}

static const uint32_t *tp_block(uint32_t va, uint32_t bytes) {
    if (va < pad_lo || va > pad_hi || bytes > pad_hi - va)
        return NULL;
    return (const uint32_t *)(const void *)(pad + (va - pad_lo));
}

#include "tick_probe_core.h"

/* ------------------------------------------------------------------ the log */
typedef struct {
    const char *path;
    const uint8_t *m, *p, *end;
    size_t bytes;
    uint32_t version, base, size, words, buffer_bytes, end_rule, tick_limit, line_words;
    uint64_t ref_hz, ref_init, clk_init;
    /* the end record */
    int ended;
    uint32_t end_last_tick, end_overflow, end_reason;
    uint64_t ref_end, clk_end, end_offset;
    /* why reading stopped short, and where */
    const char *problem;
    size_t problem_at;
    /* the tick just read */
    uint32_t tick, nruns, nwords;
    uint64_t t_enter, t_row, t_scan;
    size_t rec_bytes;
    uint32_t *touched;
    uint32_t ntouched;
    /* over the ticks read */
    uint32_t ticks, max_words;
    uint64_t total_words, total_runs, noop_words;
    size_t max_rec_bytes;
    /* region maps, and the blocks the log's writer allocated for itself (SA2O) */
    const uint8_t *reg_init, *reg_end;
    uint32_t nreg_init, more_init, nreg_end, more_end, reg_end_tick;
    uint32_t nown, own[8][2];
    /* the end-of-run check (SA2V), held to the words rebuilt at the last tick */
    int verified;
    uint32_t unscanned;
    uint32_t *st;       /* the section as of `tick` */
} Log;

static uint32_t rd32(const uint8_t *p) {
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

static uint64_t rd64(const uint8_t *p) {
    uint64_t v;
    memcpy(&v, p, 8);
    return v;
}

static int stop(Log *L, const uint8_t *at, const char *why) {
    if (!L->problem) {
        L->problem = why;
        L->problem_at = (size_t)(at - L->m);
    }
    return 0;
}

static size_t left(const Log *L) { return (size_t)(L->end - L->p); }

static void die(const char *path, const char *why) {
    fprintf(stderr, "wordlog: %s: %s\n", path, why);
    exit(2);
}

/* A region map at L->p: `SA2R` tick, count, more, pairs. */
static int read_regions(Log *L, const uint8_t **where, uint32_t *n, uint32_t *more,
                        uint32_t *tick) {
    if (left(L) < 16)
        return stop(L, L->p, "a region map is cut short");
    const uint32_t count = rd32(L->p + 8);
    if (count > WL_MAX_REGIONS || left(L) - 16 < (size_t)count * 8u)
        return stop(L, L->p, "a region map is cut short");
    *tick = rd32(L->p + 4);
    *n = count;
    *more = rd32(L->p + 12);
    *where = L->p + 16;
    L->p += 16 + (size_t)count * 8u;
    return 1;
}

static void log_open(Log *L, const char *path, uint32_t *st) {
    memset(L, 0, sizeof *L);
    L->path = path;
    const int fd = open(path, O_RDONLY);
    if (fd < 0)
        die(path, "cannot be opened");
    struct stat sb;
    if (fstat(fd, &sb) || sb.st_size < 24)
        die(path, "is not a word log (too short for a header)");
    L->bytes = (size_t)sb.st_size;
    L->m = mmap(NULL, L->bytes, PROT_READ, MAP_PRIVATE, fd, 0);
    close(fd);
    if (L->m == MAP_FAILED)
        die(path, "cannot be mapped");
    L->p = L->m;
    L->end = L->m + L->bytes;
    if (memcmp(L->p, "SA2F", 4))
        die(path, "is not a word log (no SA2F header)");
    L->version = rd32(L->p + 4);
    L->base = rd32(L->p + 8);
    L->size = rd32(L->p + 12);
    if (L->base != WL_BASE || L->size != WL_SIZE)
        die(path, "is a log of another section than word_log_core.h's");
    L->words = L->size / 4u;
    if (L->version == 1) {
        L->ref_hz = rd64(L->p + 16);
        L->line_words = WL_LINE_WORDS;
        L->p += 24;
    } else if (L->version == WL_VERSION) {
        if (L->bytes < WL_HEADER_BYTES)
            die(path, "is cut short in its header");
        L->buffer_bytes = rd32(L->p + 16);
        L->end_rule = rd32(L->p + 20);
        L->tick_limit = rd32(L->p + 24);
        L->line_words = rd32(L->p + 28);
        if (L->line_words != WL_LINE_WORDS)
            die(path, "has a header whose line_words is not word_log_core.h's WL_LINE_WORDS");
        if (rd32(L->p + 32))
            die(path, "has a header whose reserved word is not 0");
        L->ref_hz = rd64(L->p + 36);
        L->ref_init = rd64(L->p + 44);
        L->clk_init = rd64(L->p + 52);
        L->p += WL_HEADER_BYTES;
        if (left(L) >= 8 && !memcmp(L->p, "SA2O", 4)) {
            const uint32_t n = rd32(L->p + 4);
            if (n > 8 || left(L) - 8 < (size_t)n * 8u)
                die(path, "is cut short in its own-block record, or names more than 8");
            for (uint32_t k = 0; k < n; ++k) {
                L->own[k][0] = rd32(L->p + 8 + 8u * k);
                L->own[k][1] = rd32(L->p + 12 + 8u * k);
            }
            L->nown = n;
            L->p += 8 + (size_t)n * 8u;
        }
        if (left(L) >= 4 && !memcmp(L->p, "SA2R", 4)) {
            uint32_t t;
            if (!read_regions(L, &L->reg_init, &L->nreg_init, &L->more_init, &t))
                die(path, "is cut short in its region map at Init");
        }
    } else {
        die(path, "is a word log of a version this reader does not know");
    }
    if (left(L) < 16 || memcmp(L->p, "SA2K", 4) || rd32(L->p + 8) != L->base ||
        rd32(L->p + 12) != L->size)
        die(path, "has no keyframe where one must be");
    if (rd32(L->p + 4))
        die(path, "has a keyframe whose reserved word is not 0");
    L->p += 16;
    if (left(L) < L->size)
        die(path, "is cut short in its keyframe");
    L->st = st;
    memcpy(L->st, L->p, L->size);
    L->p += L->size;
    L->touched = malloc(sizeof(uint32_t) * L->words);
    if (!L->touched)
        die(path, "no memory");
}

/* The end record, which must be the file's last bytes and say where it is. */
static int read_end(Log *L) {
    const uint8_t *at = L->p;
    if (L->version == 1) {
        if (left(L) < 16)
            return stop(L, at, "the end record is cut short");
        L->end_overflow = rd32(L->p + 4);
        L->end_last_tick = L->tick;
        L->p += 16;
    } else {
        if (left(L) < WL_END_BYTES)
            return stop(L, at, "the end record is cut short");
        L->end_last_tick = rd32(L->p + 4);
        L->end_overflow = rd32(L->p + 8);
        L->end_reason = rd32(L->p + 12);
        L->ref_end = rd64(L->p + 20);
        L->clk_end = rd64(L->p + 28);
        L->end_offset = rd64(L->p + 36);
        L->p += WL_END_BYTES;
        if (L->end_offset != (uint64_t)(at - L->m))
            return stop(L, at, "the end record names another offset than its own");
        if (L->end_last_tick != L->tick)
            return stop(L, at, "the end record names another last tick than the last record");
        if (rd32(at + 16))
            return stop(L, at, "the end record's reserved word is not 0");
        if (L->end_overflow && L->end_overflow != L->end_last_tick + 1)
            return stop(L, at, "the end record names an overflow that is not at the tick after its last");
    }
    if (L->p != L->end)
        return stop(L, L->p, "bytes follow the end record");
    L->ended = 1;
    return 0;
}

/* 1: the next tick is applied to L->st; 0: the log ended, or stopped (problem). */
static int log_next(Log *L) {
    L->ntouched = 0;
    if (L->ended || L->problem)
        return 0;
    for (;;) {
        if (left(L) < 4)
            return stop(L, L->p, L->p == L->end ? "the log ends without its end record"
                                                 : "a record is cut short");
        if (!memcmp(L->p, "SA2E", 4))
            return read_end(L);
        if (L->version != 1 && !memcmp(L->p, "SA2R", 4)) {
            if (L->reg_end)
                return stop(L, L->p, "a second region map after the ticks");
            if (!read_regions(L, &L->reg_end, &L->nreg_end, &L->more_end, &L->reg_end_tick))
                return 0;
            continue;
        }
        if (L->version != 1 && !memcmp(L->p, "SA2V", 4)) {
            /* the end-of-run check: once, before the end map, naming the last
             * tick, and its hash the hash of the words rebuilt there */
            if (L->verified || L->reg_end)
                return stop(L, L->p, "an end-of-run check out of its place");
            if (left(L) < WL_VERIFY_BYTES)
                return stop(L, L->p, "the end-of-run check is cut short");
            if (rd32(L->p + 4) != L->tick)
                return stop(L, L->p, "the end-of-run check names another tick than the last record");
            if (rd32(L->p + 12))
                return stop(L, L->p, "the end-of-run check's reserved word is not 0");
            if (rd64(L->p + 16) != wl_hash(L->st, L->words))
                return stop(L, L->p, "the words the log rebuilds at its last tick are not the "
                                     "writer's shadow (the end-of-run check's hash)");
            L->unscanned = rd32(L->p + 8);
            L->verified = 1;
            L->p += WL_VERIFY_BYTES;
            continue;
        }
        if (memcmp(L->p, "SA2D", 4))
            return stop(L, L->p, "a record of no known kind");
        break;
    }
    if (L->reg_end || L->verified)
        return stop(L, L->p, "a tick after the end-of-run check or the region map at the end");
    const uint8_t *start = L->p;
    if (left(L) < 8)
        return stop(L, start, "a tick record is cut short");
    const uint32_t tick = rd32(L->p + 4);
    if (tick != L->tick + 1)
        return stop(L, start, "the ticks do not run on from 1 without a gap");
    L->p += 8;
    /* The whole record is checked before any of it is applied, so the section
     * is always as of a whole tick, whatever the file holds after it. */
    const uint8_t *runs = L->p;
    uint32_t nruns = 0, nwords = 0, next_free = 0;
    for (;;) {
        if (left(L) < 4)
            return stop(L, start, "a tick record is cut short");
        const uint32_t first = rd32(L->p);
        if (first == 0xFFFFFFFFu) {
            L->p += 4;
            break;
        }
        if (left(L) < 8)
            return stop(L, start, "a tick record is cut short");
        const uint32_t count = rd32(L->p + 4);
        if (!count || first >= L->words || count > L->words - first)
            return stop(L, start, "a run lies outside the section or is empty");
        if (nruns && first <= next_free)
            return stop(L, start, "runs do not ascend apart from each other");
        L->p += 8;
        if (left(L) / 4u < count)
            return stop(L, start, "a tick record is cut short");
        L->p += 4u * (size_t)count;
        ++nruns;
        nwords += count;
        next_free = first + count;
    }
    const size_t trailer = L->version == 1 ? 32u : WL_TICK_TRAILER_BYTES - 4u;
    if (left(L) < trailer)
        return stop(L, start, "a tick record is cut short");
    if (rd32(L->p) != nruns || rd32(L->p + 4) != nwords)
        return stop(L, start, "a tick's trailer counts other runs or words than it holds");
    L->nruns = nruns;
    L->nwords = nwords;
    L->t_enter = rd64(L->p + 8);
    L->t_row = rd64(L->p + 16);
    L->t_scan = rd64(L->p + 24);
    L->p += trailer;
    for (const uint8_t *q = runs; rd32(q) != 0xFFFFFFFFu;) {
        const uint32_t first = rd32(q), count = rd32(q + 4);
        q += 8;
        for (uint32_t k = 0; k < count; ++k) {
            const uint32_t v = rd32(q + 4u * k);
            if (L->st[first + k] == v)
                ++L->noop_words;
            L->st[first + k] = v;
            L->touched[L->ntouched++] = first + k;
        }
        q += 4u * (size_t)count;
    }
    L->tick = tick;
    L->rec_bytes = (size_t)(L->p - start);
    ++L->ticks;
    L->total_words += nwords;
    L->total_runs += nruns;
    if (nwords > L->max_words)
        L->max_words = nwords;
    if (L->rec_bytes > L->max_rec_bytes)
        L->max_rec_bytes = L->rec_bytes;
    return 1;
}

/* ------------------------------------------------------------------ helpers */
static uint32_t num(const char *s) {
    char *e;
    const unsigned long v = strtoul(s, &e, 0);
    if (!*s || *e || v > 0xFFFFFFFFul) {
        fprintf(stderr, "wordlog: not a number: %s\n", s);
        exit(2);
    }
    return (uint32_t)v;
}

/* The words tick_probe_core.h reads must be the section's: every word the
 * mask selects and every byte of every named field. */
static void refuse_outside(void) {
    for (unsigned b = 0; b < TD_BLOCKS; ++b)
        for (unsigned k = 0; k < 512; ++k)
            for (unsigned i = 0; i < 32; ++i)
                if (td_mask[b][k] >> i & 1u) {
                    const uint32_t va = td_base[b] + 4u * (32u * k + i);
                    if (va < WL_BASE || va - WL_BASE >= WL_SIZE) {
                        fprintf(stderr, "wordlog: the mask selects 0x%08x, outside the logged "
                                        "section\n", va);
                        exit(2);
                    }
                }
#define TP_FIELD(name, va, width)                                                           \
    if ((va) < WL_BASE || (va) - WL_BASE > WL_SIZE - (width)) {                              \
        fprintf(stderr, "wordlog: the named field " #name " is outside the logged section\n"); \
        exit(2);                                                                             \
    }
#include "tick_fields.h"
#undef TP_FIELD
}

static void make_pad(void) {
    pad_lo = WL_BASE & ~0xFFFFu;
    pad_hi = (WL_BASE + WL_SIZE + 0xFFFFu) & ~0xFFFFu;
    for (unsigned b = 0; b < TD_BLOCKS; ++b) {
        if (td_base[b] < pad_lo)
            pad_lo = td_base[b];
        if (td_base[b] + 0x10000u > pad_hi)
            pad_hi = td_base[b] + 0x10000u;
    }
    pad = calloc(1, pad_hi - pad_lo);
    if (!pad) {
        fprintf(stderr, "wordlog: no memory\n");
        exit(2);
    }
    state = (uint32_t *)(void *)(pad + (WL_BASE - pad_lo));
}

static int finish(const Log *L) {
    return L->problem || !L->ended ? 3 : 0;
}

static const char *reason_name(uint32_t r) {
    switch (r) {
    case WL_END_TICK_LIMIT: return "tick_limit";
    case WL_END_INPUT_END: return "input_end";
    case WL_END_ON_EXIT: return "on_exit";
    case WL_END_REFUSED: return "refused";
    default: return "none";
    }
}

/* -------------------------------------------------------------------- modes */
static int mode_info(Log *L) {
    while (log_next(L))
        ;
    printf("path=%s\nversion=%u\nbase=0x%08x\nsize=0x%08x\nbuffer_bytes=%u\nend_rule=%u\n"
           "tick_limit=%u\nline_words=%u\nfile_bytes=%zu\nticks=%u\nlast_tick=%u\n"
           "ended=%d\nend_reason=%s\nend_last_tick=%u\noverflow_tick=%u\nproblem=%s\n"
           "problem_at=%zu\nnoop_words=%llu\ntotal_words=%llu\ntotal_runs=%llu\n"
           "max_tick_words=%u\nmax_tick_bytes=%zu\nref_hz=%llu\nref_init=%llu\nref_end=%llu\n"
           "clk_init=%llu\nclk_end=%llu\nregions_init=%u\nregions_init_more=%u\n"
           "regions_end=%u\nregions_end_more=%u\nregions_end_tick=%u\nown_blocks=%u\n"
           "regions_init_present=%d\nregions_end_present=%d\nshadow_checked=%d\n"
           "shadow_unscanned=%u\n",
           L->path, L->version, L->base, L->size, L->buffer_bytes, L->end_rule, L->tick_limit,
           L->line_words, L->bytes, L->ticks, L->tick, L->ended, reason_name(L->end_reason),
           L->end_last_tick, L->end_overflow, L->problem ? L->problem : "",
           L->problem ? L->problem_at : (size_t)0, (unsigned long long)L->noop_words,
           (unsigned long long)L->total_words, (unsigned long long)L->total_runs, L->max_words,
           L->max_rec_bytes, (unsigned long long)L->ref_hz, (unsigned long long)L->ref_init,
           (unsigned long long)L->ref_end, (unsigned long long)L->clk_init,
           (unsigned long long)L->clk_end, L->nreg_init, L->more_init, L->nreg_end,
           L->more_end, L->reg_end_tick, L->nown, L->reg_init != NULL, L->reg_end != NULL,
           L->verified, L->unscanned);
    return finish(L);
}

static int mode_stats(Log *L) {
    printf("tick,nruns,nwords,rec_bytes,t_enter,t_row,t_scan\n");
    while (log_next(L))
        printf("%u,%u,%u,%zu,%llu,%llu,%llu\n", L->tick, L->nruns, L->nwords, L->rec_bytes,
               (unsigned long long)L->t_enter, (unsigned long long)L->t_row,
               (unsigned long long)L->t_scan);
    return finish(L);
}

static int mode_csv(Log *L) {
    tp_write_header(stdout);
    while (log_next(L))
        tp_write_row(stdout, L->tick);
    return finish(L);
}

static int mode_fields(Log *L, int n, char **spec) {
    uint32_t *va = calloc((size_t)n, 4), *width = calloc((size_t)n, 4);
    printf("tick");
    for (int i = 0; i < n; ++i) {
        char *colon = strchr(spec[i], ':');
        if (!colon) {
            fprintf(stderr, "wordlog: a field is VA:WIDTH, not %s\n", spec[i]);
            return 2;
        }
        *colon = 0;
        va[i] = num(spec[i]);
        width[i] = num(colon + 1);
        if (width[i] < 1 || width[i] > 4 || va[i] < WL_BASE || va[i] - WL_BASE > WL_SIZE - width[i]) {
            fprintf(stderr, "wordlog: 0x%08x:%u is not 1 to 4 bytes of the section\n", va[i],
                    width[i]);
            return 2;
        }
        printf(",0x%08x:%u", va[i], width[i]);
    }
    printf("\n");
    while (log_next(L)) {
        printf("%u", L->tick);
        for (int i = 0; i < n; ++i)
            printf(",0x%08x", tp_read(va[i], width[i]));
        printf("\n");
    }
    return finish(L);
}

static int mode_state(Log *L, uint32_t want, const char *out) {
    if (want)
        while (L->tick < want && log_next(L))
            ;
    if (L->tick != want) {
        fprintf(stderr, "wordlog: %s holds no tick %u (read to %u: %s)\n", L->path, want, L->tick,
                L->problem ? L->problem : "it ended");
        return 3;
    }
    FILE *f = fopen(out, "wb");
    if (!f || fwrite(L->st, 1, L->size, f) != L->size || fclose(f)) {
        fprintf(stderr, "wordlog: cannot write %s\n", out);
        return 2;
    }
    return 0;
}

static int mode_hashes(Log *L) {
    printf("tick,hash\n0,0x%016llx\n", (unsigned long long)wl_hash(L->st, L->words));
    while (log_next(L))
        printf("%u,0x%016llx\n", L->tick, (unsigned long long)wl_hash(L->st, L->words));
    return finish(L);
}

static int mode_changes(Log *L, uint32_t lo_va, uint32_t hi_va) {
    if (lo_va < WL_BASE || hi_va < lo_va || hi_va - WL_BASE > WL_SIZE) {
        fprintf(stderr, "wordlog: [0x%08x, 0x%08x) is not within the section\n", lo_va, hi_va);
        return 2;
    }
    const uint32_t lo = (lo_va - WL_BASE) / 4u, hi = (hi_va - WL_BASE + 3u) / 4u;
    uint32_t *prev = malloc(4u * (size_t)(hi - lo + 1u));
    printf("tick,va,old,new\n");
    for (uint32_t i = lo; i < hi; ++i) {
        prev[i - lo] = L->st[i];
        if (L->st[i])
            printf("0,0x%08x,0x00000000,0x%08x\n", WL_BASE + 4u * i, L->st[i]);
    }
    while (log_next(L))
        for (uint32_t k = 0; k < L->ntouched; ++k) {
            const uint32_t i = L->touched[k];
            if (i < lo || i >= hi || L->st[i] == prev[i - lo])
                continue;
            printf("%u,0x%08x,0x%08x,0x%08x\n", L->tick, WL_BASE + 4u * i, prev[i - lo], L->st[i]);
            prev[i - lo] = L->st[i];
        }
    return finish(L);
}

/* Every word of [lo, hi) the logs disagree on at some tick from 1: the first
 * and last such tick, how many, and both values at the first. With a tick list
 * only those ticks are compared. The logs are compared through the ticks both
 * hold; `compared_through` (stderr) says to where. */
static int mode_diff(Log *A, Log *B, uint32_t lo_va, uint32_t hi_va, const char *ticks) {
    if (lo_va < WL_BASE || hi_va < lo_va || hi_va - WL_BASE > WL_SIZE) {
        fprintf(stderr, "wordlog: [0x%08x, 0x%08x) is not within the section\n", lo_va, hi_va);
        return 2;
    }
    const uint32_t lo = (lo_va - WL_BASE) / 4u, hi = (hi_va - WL_BASE + 3u) / 4u;
    const uint32_t n = hi - lo;
    uint32_t *first = calloc(n + 1u, 4), *last = calloc(n + 1u, 4), *count = calloc(n + 1u, 4);
    uint32_t *since = calloc(n + 1u, 4), *fa = calloc(n + 1u, 4), *fb = calloc(n + 1u, 4);
    uint8_t *differs = calloc(n + 1u, 1);
    uint8_t *want = NULL;
    uint32_t keyframe_differs = 0, t = 0;
    if (!first || !last || !count || !since || !fa || !fb || !differs)
        die(A->path, "no memory");
    for (uint32_t i = lo; i < hi; ++i)
        keyframe_differs += A->st[i] != B->st[i];
    if (ticks) {
        want = calloc(1u << 20, 1);
        char *s = strdup(ticks);
        for (char *tok = strtok(s, ","); tok; tok = strtok(NULL, ",")) {
            const uint32_t w = num(tok);
            if (w < 1 || w >= (1u << 20)) {
                fprintf(stderr, "wordlog: tick %u is not one this compares\n", w);
                return 2;
            }
            want[w] = 1;
        }
    }
    for (;;) {
        const int a = log_next(A), b = log_next(B);
        if (!a || !b)
            break;
        t = A->tick;
        if (want || t == 1) {
            /* every word of the range: a listed tick, or tick 1, where the
             * intervals start from what the keyframes and tick 1 left */
            if (want && !want[t])
                continue;
            for (uint32_t i = lo; i < hi; ++i) {
                const uint32_t j = i - lo;
                const uint8_t d = A->st[i] != B->st[i];
                if (d && !first[j]) {
                    first[j] = t;
                    fa[j] = A->st[i];
                    fb[j] = B->st[i];
                }
                if (want) {
                    if (d) {
                        last[j] = t;
                        ++count[j];
                    }
                } else {
                    differs[j] = d;
                    since[j] = 1;
                }
            }
            continue;
        }
        for (int side = 0; side < 2; ++side) {
            const Log *X = side ? B : A;
            for (uint32_t k = 0; k < X->ntouched; ++k) {
                const uint32_t i = X->touched[k];
                if (i < lo || i >= hi)
                    continue;
                const uint32_t j = i - lo;
                const uint8_t d = A->st[i] != B->st[i];
                if (d == differs[j])
                    continue;
                if (d) {
                    since[j] = t;
                    if (!first[j]) {
                        first[j] = t;
                        fa[j] = A->st[i];
                        fb[j] = B->st[i];
                    }
                } else {
                    count[j] += t - since[j];
                    last[j] = t - 1;
                }
                differs[j] = d;
            }
        }
    }
    if (!want)
        for (uint32_t j = 0; j < n; ++j)
            if (differs[j]) {
                count[j] += t + 1u - since[j];
                last[j] = t;
            }
    fprintf(stderr, "compared_through=%u keyframe_differs=%u a_problem=%s b_problem=%s\n", t,
            keyframe_differs, A->problem ? A->problem : "", B->problem ? B->problem : "");
    printf("va,first_tick,last_tick,ticks_differing,a,b\n");
    for (uint32_t j = 0; j < n; ++j)
        if (count[j])
            printf("0x%08x,%u,%u,%u,0x%08x,0x%08x\n", WL_BASE + 4u * (lo + j), first[j], last[j],
                   count[j], fa[j], fb[j]);
    return (A->problem || B->problem) ? 3 : 0;
}

static int mode_regions(Log *L) {
    while (log_next(L))
        ;
    printf("which,tick,base,size\n");
    for (uint32_t k = 0; k < L->nown; ++k)
        printf("own,0,0x%08x,0x%08x\n", L->own[k][0], L->own[k][1]);
    for (uint32_t k = 0; k < L->nreg_init; ++k)
        printf("init,0,0x%08x,0x%08x\n", rd32(L->reg_init + 8u * k), rd32(L->reg_init + 8u * k + 4));
    for (uint32_t k = 0; k < L->nreg_end; ++k)
        printf("end,%u,0x%08x,0x%08x\n", L->reg_end_tick, rd32(L->reg_end + 8u * k),
               rd32(L->reg_end + 8u * k + 4));
    return finish(L);
}

static int mode_touched(Log *L) {
    uint32_t *count = calloc(L->words, 4);
    if (!count)
        die(L->path, "no memory");
    while (log_next(L))
        for (uint32_t k = 0; k < L->ntouched; ++k)
            ++count[L->touched[k]];
    printf("va,ticks_changed\n");
    for (uint32_t i = 0; i < L->words; ++i)
        if (count[i])
            printf("0x%08x,%u\n", WL_BASE + 4u * i, count[i]);
    return finish(L);
}

/* The pointer test snapshot_fields.py applies (in_regions), over every value
 * every word takes: the regions are both maps', read after the whole log.
 * Refused for a log without both maps, or whose maps stopped at WL_MAX_REGIONS
 * (`more`): the test would silently narrow to what they hold. A span to cut
 * (`cut_lo` < `cut_hi`) is taken out of every region as the log's own blocks
 * are: snapshot_fields.py cuts the executable's image, whose addresses the host
 * does not choose, to tell a word that only ever holds an image value from one
 * that holds an address the host chose (the G4 interrogation, B1). */
typedef struct { uint32_t lo, hi; } Span;
static Span *spans;
static uint32_t nspans;

static int span_cmp(const void *a, const void *b) {
    const Span *x = a, *y = b;
    return x->lo < y->lo ? -1 : x->lo > y->lo;
}

static int in_spans(uint32_t v) {
    uint32_t lo = 0, hi = nspans;
    while (lo < hi) {
        const uint32_t mid = (lo + hi) / 2;
        if (v < spans[mid].lo)
            hi = mid;
        else if (v >= spans[mid].hi)
            lo = mid + 1;
        else
            return 1;
    }
    return 0;
}

static int mode_pointers(const char *path, uint32_t small, uint64_t below,
                         uint64_t cut_lo, uint64_t cut_hi) {
    Log L;
    uint32_t *st = malloc(WL_SIZE);
    if (!st)
        die(path, "no memory");
    log_open(&L, path, st);
    while (log_next(&L))
        ;
    if (L.problem || !L.ended)
        die(path, "does not read whole, so its region map at the end is not known");
    if (!L.reg_init || !L.reg_end)
        die(path, "has no region map at Init or at the end, so no value can be held to the "
                  "game's memory");
    if (L.more_init || L.more_end)
        die(path, "has a region map that stopped at WL_MAX_REGIONS regions, so the pointer test "
                  "would miss every value in the regions past them");
    if (cut_lo > cut_hi)
        die(path, "has a span to cut that ends before it starts");
    /* the cuts: the writer's own blocks, as VirtualAlloc gave them (whole
     * pages), which are not the game's memory, and the caller's span */
    uint64_t cuts[9][2];
    uint32_t ncut = 0;
    for (uint32_t o = 0; o < L.nown; ++o, ++ncut)
        cuts[ncut][0] = L.own[o][0], cuts[ncut][1] = L.own[o][0] + ((L.own[o][1] + 0xFFFull) & ~0xFFFull);
    if (cut_lo < cut_hi)
        cuts[ncut][0] = cut_lo, cuts[ncut][1] = cut_hi, ++ncut;
    spans = calloc(((size_t)L.nreg_init + L.nreg_end + 1u) * (ncut + 1u), sizeof *spans);
    if (!spans)
        die(path, "no memory");
    for (uint32_t k = 0; k < L.nreg_init + L.nreg_end; ++k) {
        const uint8_t *r = k < L.nreg_init ? L.reg_init + 8u * k : L.reg_end + 8u * (k - L.nreg_init);
        uint64_t lo = rd32(r), hi = lo + rd32(r + 4);
        if (lo >= below)
            continue;
        /* each cut out of the region (the cuts above): n cuts leave at most
         * n + 1 pieces */
        uint64_t pieces[10][2] = {{lo, hi}};
        uint32_t np = 1;
        for (uint32_t o = 0; o < ncut; ++o) {
            const uint64_t olo = cuts[o][0], ohi = cuts[o][1];
            uint64_t next[10][2];
            uint32_t nn = 0;
            for (uint32_t q = 0; q < np; ++q) {
                if (pieces[q][1] <= olo || pieces[q][0] >= ohi) {
                    next[nn][0] = pieces[q][0], next[nn][1] = pieces[q][1], ++nn;
                    continue;
                }
                if (pieces[q][0] < olo && nn < 10)
                    next[nn][0] = pieces[q][0], next[nn][1] = olo, ++nn;
                if (pieces[q][1] > ohi && nn < 10)
                    next[nn][0] = ohi, next[nn][1] = pieces[q][1], ++nn;
            }
            memcpy(pieces, next, sizeof next);
            np = nn;
        }
        for (uint32_t q = 0; q < np; ++q)   /* a region that ends at 4 GB is kept whole */
            spans[nspans++] = (Span){(uint32_t)pieces[q][0],
                                     pieces[q][1] > 0xFFFFFFFFu ? 0xFFFFFFFFu : (uint32_t)pieces[q][1]};
    }
    qsort(spans, nspans, sizeof *spans, span_cmp);
    uint32_t w = 0;                         /* merge overlaps, so the search is exact */
    for (uint32_t k = 0; k < nspans; ++k) {
        if (w && spans[k].lo <= spans[w - 1].hi) {
            if (spans[k].hi > spans[w - 1].hi)
                spans[w - 1].hi = spans[k].hi;
        } else {
            spans[w++] = spans[k];
        }
    }
    nspans = w;
    Log M;
    log_open(&M, path, st);
    uint32_t *first = malloc(sizeof(uint32_t) * M.words), *value = malloc(sizeof(uint32_t) * M.words);
    /* data: the first tick (+1; 0 = never) the word holds a value that is
     * neither zero nor an address by this test: a word flagged for one value
     * may hold data at every other tick, and a mask that drops it drops those
     * ticks too (the review of the G4 round-one fold, Reviewer B's finding 1) */
    uint32_t *data = calloc(M.words, sizeof(uint32_t));
    uint8_t *hit = calloc(M.words, 1);
    if (!first || !value || !data || !hit)
        die(path, "no memory");
    for (uint32_t i = 0; i < M.words; ++i) {
        if (M.st[i] >= small && in_spans(M.st[i]))
            hit[i] = 1, first[i] = 0, value[i] = M.st[i];
        else if (M.st[i])
            data[i] = 1;
    }
    while (log_next(&M))
        for (uint32_t k = 0; k < M.ntouched; ++k) {
            const uint32_t i = M.touched[k];
            const int addr = M.st[i] >= small && in_spans(M.st[i]);
            if (!hit[i] && addr)
                hit[i] = 1, first[i] = M.tick, value[i] = M.st[i];
            else if (!addr && M.st[i] && !data[i])
                data[i] = M.tick + 1u;
        }
    printf("va,first_tick,value,data_tick\n");
    for (uint32_t i = 0; i < M.words; ++i)
        if (hit[i]) {
            if (data[i])
                printf("0x%08x,%u,0x%08x,%u\n", WL_BASE + 4u * i, first[i], value[i], data[i] - 1u);
            else
                printf("0x%08x,%u,0x%08x,\n", WL_BASE + 4u * i, first[i], value[i]);
        }
    return finish(&M);
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: wordlog MODE LOG ... (see the top of wordlog.c)\n");
        return 2;
    }
    const char *mode = argv[1];
    refuse_outside();
    make_pad();
    if (!strcmp(mode, "pointers") && ((argc >= 3 && argc <= 5) || argc == 7))
        return mode_pointers(argv[2], argc >= 4 ? num(argv[3]) : 0x10000u,
                             argc >= 5 && strcmp(argv[4], "-") ? (uint64_t)num(argv[4])
                                                               : 0x100000000ull,
                             argc == 7 ? (uint64_t)num(argv[5]) : 0u,
                             argc == 7 ? (uint64_t)num(argv[6]) : 0u);
    Log L;
    log_open(&L, argv[2], state);
    if (!strcmp(mode, "info"))
        return mode_info(&L);
    if (!strcmp(mode, "stats"))
        return mode_stats(&L);
    if (!strcmp(mode, "csv"))
        return mode_csv(&L);
    if (!strcmp(mode, "fields"))
        return mode_fields(&L, argc - 3, argv + 3);
    if (!strcmp(mode, "state") && argc == 5)
        return mode_state(&L, num(argv[3]), argv[4]);
    if (!strcmp(mode, "hashes"))
        return mode_hashes(&L);
    if (!strcmp(mode, "changes") && argc == 5)
        return mode_changes(&L, num(argv[3]), num(argv[4]));
    if (!strcmp(mode, "regions"))
        return mode_regions(&L);
    if (!strcmp(mode, "touched"))
        return mode_touched(&L);
    if (!strcmp(mode, "diff") && argc >= 4) {
        uint32_t *other = malloc(WL_SIZE);
        if (!other)
            die(argv[3], "no memory");
        Log M;
        log_open(&M, argv[3], other);
        const uint32_t lo = argc >= 6 ? num(argv[4]) : WL_BASE;
        const uint32_t hi = argc >= 6 ? num(argv[5]) : WL_BASE + WL_SIZE;
        return mode_diff(&L, &M, lo, hi, argc >= 7 ? argv[6] : NULL);
    }
    fprintf(stderr, "wordlog: unknown mode or arguments: %s\n", mode);
    return 2;
}
