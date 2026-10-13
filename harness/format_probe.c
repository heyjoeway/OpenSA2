/* The runtime's wsprintf formatter (runtime/sa2_format.c) on its own, over a
 * stubbed guest window, fed the cases harness/format_oracle.c feeds the
 * oracle's user32. Driven by Documentation/scripts/tests/test_wsprintf.py.
 *
 *   format_probe                one JSON line per case, as format_oracle prints
 *   format_probe --faults       one JSON line per refusal the formatter must make
 *
 * The output buffer is prefilled with 0xCC so a write past the terminator
 * shows as `touched`.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/guest_memory.h"
#include "../runtime/sa2_format.h"
#include "format_cases.h"

#define GBASE 0x10000000u
#define GSIZE 0x00010000u
#define G_FMT  (GBASE + 0x0000u)
#define G_STR  (GBASE + 0x2000u)
#define G_ARGS (GBASE + 0x6000u)
#define G_OUT  (GBASE + 0x8000u)
#define OUT_BYTES 2048u

static uint8_t g_mem[GSIZE];

static int in_window(uint32_t a, uint32_t n) {
    return a >= GBASE && a - GBASE <= GSIZE && GSIZE - (a - GBASE) >= n;
}
int sa2_peek(uint32_t a, uint32_t n, void *o) {
    if (!in_window(a, n)) return SA2_E_RANGE;
    memcpy(o, g_mem + (a - GBASE), n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *i) {
    if (!in_window(a, n)) return SA2_E_RANGE;
    memcpy(g_mem + (a - GBASE), i, n);
    return SA2_OK;
}

static uint32_t g_str_at;

static uint32_t place_narrow(const char *s) {
    uint32_t at = g_str_at, n = (uint32_t)strlen(s) + 1u;
    memcpy(g_mem + (at - GBASE), s, n);
    g_str_at += (n + 3u) & ~3u;
    return at;
}

static uint32_t place_wide(const uint16_t *w) {
    uint32_t at = g_str_at, n = 0;
    do {
        g_mem[at - GBASE + 2u * n] = (uint8_t)(w[n] & 0xffu);
        g_mem[at - GBASE + 2u * n + 1u] = (uint8_t)(w[n] >> 8);
    } while (w[n++]);
    g_str_at += (2u * n + 3u) & ~3u;
    return at;
}

static void lay_out(const sa2_format_case *c) {
    memset(g_mem, 0, sizeof g_mem);
    memcpy(g_mem + (G_FMT - GBASE), c->fmt, strlen(c->fmt) + 1u);
    g_str_at = G_STR;
    for (unsigned k = 0; k < c->nargs; k++) {
        uint32_t v = c->arg[k].kind == 's' ? place_narrow(c->arg[k].s)
                   : c->arg[k].kind == 'w' ? place_wide(c->arg[k].w)
                   : c->arg[k].n;
        memcpy(g_mem + (G_ARGS - GBASE) + 4u * k, &v, 4);
    }
    memset(g_mem + (G_OUT - GBASE), 0xCC, OUT_BYTES);
}

static void print_result(unsigned i, int st, uint32_t ret, uint32_t fault) {
    const char *name = sa2_format_cases[i].name;
    if (st != SA2_OK) {
        printf("{\"case\":%u,\"name\":\"%s\",\"fault\":%u}\n", i, name, fault);
        return;
    }
    unsigned touched = OUT_BYTES;
    while (touched && g_mem[G_OUT - GBASE + touched - 1u] == 0xCC) touched--;
    printf("{\"case\":%u,\"name\":\"%s\",\"ret\":%d,\"touched\":%u,\"out\":\"",
           i, name, (int)ret, touched);
    for (unsigned k = 0; k < touched; k++) printf("%02x", g_mem[G_OUT - GBASE + k]);
    printf("\"}\n");
}

/* Each refusal: where the formatter was pointed, and the address it must name. */
static void faults(void) {
    static const char *names[] = {
        "format-outside", "string-outside", "string-unterminated",
        "wide-unterminated", "args-outside", "out-straddles",
    };
    for (unsigned i = 0; i < sizeof names / sizeof names[0]; i++) {
        uint32_t fmt = G_FMT, args = G_ARGS, out = G_OUT, expect = 0, ret = 0, fault = 0;
        memset(g_mem, 0, sizeof g_mem);
        const char *f = "%s";
        uint32_t arg = 0;
        switch (i) {
        case 0: fmt = GBASE + GSIZE + 0x100u; expect = fmt; break;
        case 1: arg = 0x00001000u; expect = arg; break;
        case 2: /* runs to the end of the window with no terminator */
            arg = GBASE + GSIZE - 16u;
            memset(g_mem + (arg - GBASE), 'x', 16);
            expect = GBASE + GSIZE;
            break;
        case 3:
            f = "%S";
            arg = GBASE + GSIZE - 16u;
            memset(g_mem + (arg - GBASE), 'x', 16);
            expect = GBASE + GSIZE;
            break;
        case 4: args = GBASE + GSIZE - 2u; expect = args; break;
        case 5: /* 9 characters and a terminator, 6 bytes of room */
            f = "abcdefghi";
            out = GBASE + GSIZE - 6u;
            expect = GBASE + GSIZE;
            break;
        }
        memcpy(g_mem + (G_FMT - GBASE), f, strlen(f) + 1u);
        memcpy(g_mem + (G_ARGS - GBASE), &arg, 4);
        int st = sa2_wvsprintf(out, fmt, args, &ret, &fault);
        char written[8] = {0};
        if (i == 5) memcpy(written, g_mem + (out - GBASE), 6);
        printf("{\"fault_case\":\"%s\",\"status\":%d,\"fault\":%u,\"expect\":%u,\"written\":\"%s\"}\n",
               names[i], st, fault, expect, written);
    }
}

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "--faults")) {
        faults();
        return 0;
    }
    for (unsigned i = 0; i < SA2_FORMAT_CASES; i++) {
        uint32_t ret = 0, fault = 0;
        lay_out(&sa2_format_cases[i]);
        int st = sa2_wvsprintf(G_OUT, G_FMT, G_ARGS, &ret, &fault);
        print_result(i, st, ret, fault);
    }
    return 0;
}
