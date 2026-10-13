/* Harness level 2, lifted side: the recorded controller, written where the native replay writes it.
 *
 * Documentation/decisions/2026-09-23-S4-stage-route.md, item 4: a stage replay
 * feeds `ControllersRaw[0]` (0x01A52918, PDS_PERIPHERAL) at the point the Mod
 * Loader's OnInput runs, natively through harness/replay/native_input_probe.c
 * and lifted through this file. The loader hooks the only `ret` of the
 * controller update, 0x0077E897 at the end of 0x0077E780, so a link-time wrap
 * of `sub_77e780` that writes after the real body is the same point: the game
 * has read its devices and nothing has read the result.
 *
 * SA2_INPUT_SCRIPT=<csv> turns it on: a recording in native_input_probe.c's
 * format (`tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old`, values in hex),
 * which is what the native replay's generated header is made from. Each call
 * is keyed as natively: the tick is GameLoop's returns so far, counted by
 * harness/replay/wrap_tick_probe.c, and the ordinal is the call's number within
 * that tick. A call with no recorded row writes a neutral controller, as the
 * native replay does, so the lifted build's own device state -- it has none,
 * DirectInput8Create fails by design -- never reaches the game either way.
 *
 * The ordinal assumes the game calls the update a fixed number of times per
 * GameLoop call. On the test-spawn route it does; the title and memory-card
 * screens call it in loops (0x00519274.., 0x005F7437..) whose counts have not
 * been checked against the wall clock (the rule-5 review, 2026-09-23).
 *
 * SA2_INPUT_APPLIED=<csv> records every call, in the native replay's
 * `sa2-input-applied.csv` format, so the two sides' applied logs can be
 * compared line for line.
 *
 * Unset, the wrap passes through and writes nothing.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "guest_memory.h"

void *__real_sub_77e780(void *s, uint32_t pc, void *m);
unsigned long sa2_replay_gameloop_returns(void);   /* wrap_tick_probe.c */

#define RAW0 0x01A52918u

/* PDS_PERIPHERAL's value fields, in the order the file carries them: the
 * native recorder's table, which this must match field for field. */
enum { F_ON, F_OFF, F_PRESS, F_RELEASE, F_R, F_L, F_X1, F_Y1, F_X2, F_Y2, F_OLD, F_COUNT };
static const uint32_t off[F_COUNT] = {0x08, 0x0C, 0x10, 0x14, 0x18, 0x1A, 0x1C, 0x1E, 0x20, 0x22, 0x2C};
static const uint32_t width[F_COUNT] = {4, 4, 4, 4, 2, 2, 2, 2, 2, 2, 4};

typedef struct { unsigned long tick; unsigned ord; uint32_t v[F_COUNT]; } in_row;

static int state = -1;                  /* -1 unread, 0 off, 1 on */
static in_row *rows;
static unsigned count, next_row;
static FILE *applied;
static unsigned long last_tick = ~0ul;
static unsigned ordinal;

/* One column exactly as the recorder prints it -- `%lu` or `%u` for tick and
 * ord, `0x%x` for a value: no space, no sign, no leading zero, lowercase hex
 * -- and no larger than `max`; the character after it, or 0.
 *
 * strtoul, before this, took leading space, a sign and a value without its
 * 0x, so a row the recorder cannot have written replayed as some other
 * controller state. Its casts cut ord and every value to 32 bits, and nothing
 * cut a 2-byte field to its width: the row kept 32 bits, the 2-byte sa2_poke
 * wrote the low half, and the applied log recorded all 32, which is not what
 * the game read. A leading zero changes no value here, but
 * tools/oracle/record_stage.py copies tick and ord into the native replay's
 * C header as written, where 010 is eight. */
static const char *field(const char *c, int hex, unsigned long max, unsigned long *out) {
    if (hex) {
        if (c[0] != '0' || c[1] != 'x') return 0;
        c += 2;
    }
    const char *const digits = c;
    const unsigned base = hex ? 16 : 10;
    unsigned long v = 0;
    for (;; ++c) {
        unsigned d;
        if (*c >= '0' && *c <= '9') d = (unsigned)(*c - '0');
        else if (hex && *c >= 'a' && *c <= 'f') d = (unsigned)(*c - 'a' + 10);
        else break;
        if (c != digits && v == 0) return 0;        /* a digit after a leading 0 */
        if (v > (max - d) / base) return 0;
        v = v * base + d;
    }
    if (c == digits) return 0;
    *out = v;
    return c;
}

/* The whole script, parsed once; a malformed line or a failed read stops the
 * run with the line number rather than replaying a prefix of what was
 * recorded. */
static void load(void) {
    const char *path = getenv("SA2_INPUT_SCRIPT");
    state = 0;
    if (!path || !*path) return;
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "wrap_input_replay: cannot read %s\n", path); exit(4); }
    char line[512];
    unsigned cap = 0, lineno = 0;
    while (fgets(line, sizeof line, f)) {
        ++lineno;
        if (lineno == 1) {
            static const char header[] = "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old";
            line[strcspn(line, "\r\n")] = 0;
            if (strcmp(line, header)) {
                fprintf(stderr, "wrap_input_replay: %s: not a recorder file\n", path);
                exit(4);
            }
            continue;
        }
        /* A line the buffer cannot hold with its newline would be read as two
         * rows. No recorder row comes near it, so it is refused rather than
         * split. A line with no newline is whole only when nothing follows
         * it, which the peek tells and feof does not: the last row may end
         * without one, and at 511 characters it fills the buffer before
         * fgets reaches the end of the file. */
        if (!strchr(line, '\n')) {
            if (getc(f) != EOF) {
                fprintf(stderr, "wrap_input_replay: %s:%u: line too long\n", path, lineno);
                exit(4);
            }
            if (ferror(f)) {        /* the peek's own read failed: this line's continuation */
                fprintf(stderr, "wrap_input_replay: %s:%u: read error\n", path, lineno);
                exit(4);
            }
        }
        if (count == cap) {
            cap = cap ? cap * 2 : 4096;
            in_row *p = realloc(rows, cap * sizeof *rows);
            if (!p) { fprintf(stderr, "wrap_input_replay: out of memory\n"); exit(4); }
            rows = p;
        }
        /* tick and ord in decimal, then each value as 0x and hex digits. Every
         * column fits 32 bits -- the recorder is a 32-bit Windows DLL, whose
         * `%lu` prints a 32-bit unsigned long where this build's is 64 -- and
         * a value fits its field's width (width[]: 0xffffffff or 0xffff). */
        unsigned long col[2 + F_COUNT];
        const char *c = line;
        unsigned i = 0;
        for (; i < 2 + F_COUNT; ++i) {
            const unsigned long max = 0xffffffffu >> (i < 2 ? 0 : 32 - 8 * width[i - 2]);
            if (!(c = field(c, i >= 2, max, &col[i]))) break;
            /* A comma after every column but the last, and nothing after that:
             * a row with extra columns is not the recorder's and is refused. */
            if (i + 1 < 2 + F_COUNT ? *c++ != ','
                                    : *c && strcmp(c, "\n") && strcmp(c, "\r\n")) break;
        }
        if (i < 2 + F_COUNT) {
            fprintf(stderr, "wrap_input_replay: %s:%u: malformed row at column %u\n",
                    path, lineno, i + 1);
            exit(4);
        }
        in_row *r = &rows[count];
        r->tick = col[0];
        r->ord = (unsigned)col[1];
        for (i = 0; i < F_COUNT; ++i) r->v[i] = (uint32_t)col[2 + i];
        if (count && (r->tick < rows[count - 1].tick ||
                      (r->tick == rows[count - 1].tick && r->ord <= rows[count - 1].ord))) {
            fprintf(stderr, "wrap_input_replay: %s:%u: rows out of order\n", path, lineno);
            exit(4);
        }
        ++count;
    }
    /* fgets ends the loop on a read error as it does at the end of the file;
     * the rows read by then are a prefix, so the run stops here instead. The
     * failed read was fgets's, of the next line; a failed peek is refused
     * where it happens, above. */
    if (ferror(f)) {
        fprintf(stderr, "wrap_input_replay: %s:%u: read error\n", path, lineno + 1);
        exit(4);
    }
    fclose(f);
    if (lineno == 0) {              /* an empty file never reached the header check */
        fprintf(stderr, "wrap_input_replay: %s is empty\n", path);
        exit(4);
    }
    const char *ap = getenv("SA2_INPUT_APPLIED");
    if (ap && *ap) {
        if (!(applied = fopen(ap, "w"))) {
            fprintf(stderr, "wrap_input_replay: cannot write %s\n", ap);
            exit(4);
        }
        fputs("tick,ord,matched,on,off,press,release,r,l,x1,y1,x2,y2,old\n", applied);
    }
    state = 1;
}

void *__wrap_sub_77e780(void *s, uint32_t pc, void *m) {
    m = __real_sub_77e780(s, pc, m);
    if (state < 0) load();
    if (!state) return m;
    const unsigned long tick = sa2_replay_gameloop_returns();
    if (tick != last_tick) { last_tick = tick; ordinal = 0; }
    const unsigned ord = ordinal++;
    while (next_row < count && (rows[next_row].tick < tick ||
           (rows[next_row].tick == tick && rows[next_row].ord < ord)))
        ++next_row;
    const int matched = next_row < count && rows[next_row].tick == tick && rows[next_row].ord == ord;
    uint32_t v[F_COUNT];
    for (unsigned i = 0; i < F_COUNT; ++i) {
        v[i] = matched ? rows[next_row].v[i] : 0u;
        /* little-endian: the low half for width 2 */
        if (sa2_poke(RAW0 + off[i], width[i], &v[i]) != SA2_OK) {
            fprintf(stderr, "wrap_input_replay: ControllersRaw[0] is not writable\n");
            exit(4);
        }
    }
    if (applied) {
        fprintf(applied, "%lu,%u,%d", tick, ord, matched);
        for (unsigned i = 0; i < F_COUNT; ++i) fprintf(applied, ",0x%x", (unsigned)v[i]);
        fputc('\n', applied);
        fflush(applied);            /* the run ends in a trap; every row must be on disk */
    }
    return m;
}
