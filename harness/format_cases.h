/* The cases the runtime's wsprintf formatter is checked against.
 *
 * One table, two drivers: harness/format_oracle.c hands each case to the
 * oracle's own USER32!wvsprintfA (Proton's, run by tools/s4/format_oracle.py)
 * and harness/format_probe.c hands it to runtime/sa2_format.c. The receipt the
 * tool writes records both outputs, and the pytest suite holds the runtime to
 * the oracle's, case by case, so a change here needs the receipt rerun: the
 * receipt carries this file's sha256 and the suite refuses a stale one.
 *
 * The first five are the format strings the image's one caller passes
 * (0x0089A254..0x0089A2B8); the rest walk the grammar's edges, which SA2 does
 * not reach but which decide whether the formatter is wsprintf's or merely
 * printf's with the same five cases right. Arguments are what a va_list holds:
 * dwords, of which a string argument is the address.
 *
 * No output is 512 or more hex digits or 2048 base64 characters in a row,
 * because the receipt holds the outputs as text and check_boundary.py reads
 * such a run as embedded bytes: the long cases use "Q." and spaces.
 */
#ifndef SA2_HARNESS_FORMAT_CASES_H
#define SA2_HARNESS_FORMAT_CASES_H

#include <stdint.h>

typedef struct {
    char            kind;   /* 'n' the dword itself, 's' a narrow string's address, 'w' a wide one's */
    uint32_t        n;
    const char     *s;
    const uint16_t *w;
} sa2_format_arg;

typedef struct {
    const char    *name;
    const char    *fmt;
    unsigned       nargs;
    sa2_format_arg arg[8];
} sa2_format_case;

#define N(v) {'n', (uint32_t)(v), 0, 0}
#define S(p) {'s', 0, (p), 0}
#define W(p) {'w', 0, 0, (p)}

#define R5(x)  x x x x x
#define R10(x) R5(x) R5(x)
/* 1100 characters, past the 1024-byte limit; 1020, just short of it. */
#define LONG_Q  R10(R10(R5("Q."))) R10(R5("Q."))
#define Q_1020  R10(R10(R5("Q."))) R5("Q.") R5("Q.")

static const uint16_t W_WIDE[] = {'w', 'i', 'd', 'e', 0};
static const uint16_t W_CAFE[] = {'c', 'a', 'f', 0xe9, 0};
static const uint16_t W_EURO[] = {0x20ac, 'x', 0};
static const uint16_t W_CJK[]  = {0x4e00, 'y', 0};

static const sa2_format_case sa2_format_cases[] = {
    /* the image's own */
    {"image-mblk",  "%05d: MBLK:%08xH(%08xH) ", 3, {N(1), N(0x0a1b2c3d), N(0x1a)}},
    {"image-ptr",   "PTR:%08xH(%08xH) ", 2, {N(0x0a1b2c57), N(0x40)}},
    {"image-kinds", "%s %s ", 2, {S("ALOC"), S("FIX ")}},
    {"image-name",  "\"%s\"\r\n", 1, {S("(no name)")}},
    {"image-total", "TotalMemory = %08xH CurrentMemory = %08xH PeakSize = %08xH\r\n\r\n", 3,
                    {N(0x00a00000), N(0x1234), N(0x56789)}},
    /* integers */
    {"signed",       "%d|%i|%d|%d", 4, {N(0), N(-1), N(0x7fffffff), N(0x80000000)}},
    {"unsigned",     "%u|%u", 2, {N(0), N(0xffffffff)}},
    {"hex",          "%x|%X|%x", 3, {N(0xdeadbeef), N(0xdeadbeef), N(0)}},
    {"hex-prefix",   "%#x|%#X|%#x|%#X", 4, {N(0x1f), N(0x1f), N(0), N(0)}},
    {"prefix-dec",   "%#d|%#u", 2, {N(5), N(5)}},
    {"width",        "[%5d]|[%-5d]|[%05d]|[%-05d]|[%5d]|[%05d]", 6,
                     {N(42), N(42), N(42), N(42), N(-42), N(-42)}},
    {"narrow-width", "[%2d]|[%1x]", 2, {N(12345), N(0xabc)}},
    {"precision",    "[%.3d]|[%.3d]|[%.0d]|[%.d]|[%.0x]|[%.3x]", 6,
                     {N(7), N(-7), N(0), N(0), N(0), N(0xa)}},
    {"width-prec",   "[%8.3d]|[%-8.3d]|[%08.3d]|[%8.3d]|[%08.3d]", 5,
                     {N(7), N(7), N(7), N(-7), N(-7)}},
    {"prefix-width", "[%#10x]|[%#010x]|[%-#10x]|[%#.4x]", 4,
                     {N(0x1f), N(0x1f), N(0x1f), N(0x1f)}},
    {"short",        "%hd|%hd|%hu|%hx|%hX", 5,
                     {N(0x12348000), N(0x7fff), N(0x12348000), N(0x1234abcd), N(0xffff0000)}},
    {"long",         "%ld|%li|%lu|%lx|%lX", 5, {N(-5), N(-6), N(5), N(0xab), N(0xab)}},
    {"pointer",      "%p|%p|[%10p]|[%-p]", 4, {N(0x1234), N(0xdeadbeef), N(0x1234), N(0x1234)}},
    {"pointer-sized", "[%I64p]|[%u]|[%#I64p]|[%lp]|[%hp]", 5,
                     {N(0x89abcdef), N(0x01234567), N(0xab), N(0xcd), N(0xef)}},
    {"wrap",         "[%4294967297d]|[%.4294967298d]|[%5.4294967296z]|[%.4294967297s]", 3,
                     {N(7), N(7), S("abc")}},
    {"int64",        "%I64d|%I64x|%u", 5, {N(2), N(1), N(0xffffffff), N(1), N(99)}},
    {"int-sized",    "%I32d|%Id|%u", 3, {N(-3), N(-4), N(5)}},
    {"int64-signed", "%I64d|%I64u|%I64d", 6,
                     {N(0xfffffffe), N(0xffffffff), N(0xfffffffe), N(0xffffffff), N(0), N(0x80000000)}},
    {"int-size-edge", "[%I6d]|[%I3x]|[%I]", 0, {{0}}},
    {"hex-upper",    "[%-8X]|[%08X]", 2, {N(0xabc), N(0xabc)}},
    {"percent",      "%%|100%%|[%5%]|[%-5%]", 0, {{0}}},
    /* strings */
    {"string",       "[%s]|[%10s]|[%-10s]|[%.2s]|[%.0s]|[%.s]|[%010s]", 7,
                     {S("abc"), S("abc"), S("abc"), S("abc"), S("abc"), S("abc"), S("abc")}},
    {"string-null",  "[%s]|[%10s]|[%.3s]|[%S]", 4, {N(0), N(0), N(0), N(0)}},
    {"string-wide",  "[%S]|[%ls]|[%ws]|[%hS]|[%hs]|[%lS]", 6,
                     {W(W_WIDE), W(W_WIDE), W(W_WIDE), S("narrow"), S("narrow"), W(W_WIDE)}},
    {"string-wide-fmt", "[%.3S]|[%8S]|[%-8S]", 3, {W(W_WIDE), W(W_WIDE), W(W_WIDE)}},
    {"string-wide-high", "[%S]|[%S]|[%S]", 3, {W(W_CAFE), W(W_EURO), W(W_CJK)}},
    {"string-sizes", "[%wS]|[%Is]|[%I64s]|[%I64c]|[%u]", 5, {S("ws"), S("Is"), S("I64s"), N('c'), N(9)}},
    {"string-high",  "[%s]", 1, {S("caf\xe9")}},
    /* characters */
    {"char",         "[%c]|[%3c]|[%-3c]|[%03c]|[%hc]|[%.0c]", 6,
                     {N('A'), N('B'), N('C'), N('D'), N('E'), N('F')}},
    {"char-wide",    "[%C]|[%lc]|[%hC]|[%wc]", 4, {N('G'), N('H'), N('I'), N('J')}},
    {"char-high",    "[%c]|[%C]|[%C]|[%C]", 4, {N(0x1e9), N(0xe9), N(0x20ac), N(0x4e00)}},
    {"char-zero",    "[%c]|[%hc]|[%lc]|[%C]", 4, {N(0), N(0), N(0), N(0)}},
    /* the grammar's edges */
    {"unknown",      "[%z]|[%5z]|[%-5z]|[%5.2z]|[%lz]|[%hz]", 0, {{0}}},
    {"no-float",     "[%f]|[%e]|[%*d]|[%.*d]", 0, {{0}}},
    {"flag-order",   "[%0-5d]|[%--5d]|[%#-08x]|[%-#08x]|[%00x]", 5,
                     {N(7), N(7), N(0x1f), N(0x1f), N(9)}},
    {"trailing-pct",   "abc%", 0, {{0}}},
    {"trailing-width", "abc%5", 0, {{0}}},
    {"trailing-flag",  "abc%-", 0, {{0}}},
    {"trailing-size",  "abc%l", 0, {{0}}},
    /* the 1024-byte limit */
    {"limit-under",  "%1022d", 1, {N(7)}},
    {"limit-exact",  "%1023d", 1, {N(7)}},
    {"limit-over",   "%1030d|", 1, {N(7)}},
    {"limit-text",   LONG_Q, 0, {{0}}},
    {"limit-string", "%s", 1, {S(LONG_Q)}},
    {"limit-prec",   "[%.1100d]", 1, {N(7)}},
    {"limit-number", Q_1020 "%d", 1, {N(12345)}},
    {"limit-prefix", Q_1020 "%#x", 1, {N(0xabcdef)}},
    {"limit-width",  Q_1020 "%-9d|", 1, {N(1)}},
};

#define SA2_FORMAT_CASES (sizeof sa2_format_cases / sizeof sa2_format_cases[0])

#endif
