/* Code page 1252 both ways, and the character data of its characters. See
 * sa2_codepage.h. */
#include "sa2_codepage.h"

#include <stddef.h>

#include "sa2_chardata.h"

/* 0x80..0x9F, the 27 assigned positions plain Latin-1 does not have and the
 * five unassigned ones, which map to themselves. */
static const uint16_t k_cp1252_high[32] = {
    0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
    0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
    0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
    0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178,
};

uint16_t sa2_cp1252_to_wide(uint8_t b) {
    return (b >= 0x80u && b < 0xA0u) ? k_cp1252_high[b - 0x80u] : (uint16_t)b;
}

int sa2_cp1252_from_wide(uint16_t w, uint8_t *out) {
    if (w < 0x80u || (w >= 0xA0u && w < 0x100u)) { *out = (uint8_t)w; return 1; }
    for (uint32_t i = 0; i < 32; i++)
        if (k_cp1252_high[i] == w) { *out = (uint8_t)(0x80u + i); return 1; }
    *out = (uint8_t)'?';
    return 0;
}

int sa2_cp1252_best_fit(uint16_t w, uint8_t *out) {
    if (sa2_cp1252_from_wide(w, out)) return 1;
    size_t lo = 0, hi = sizeof sa2_best_fit_rows / sizeof sa2_best_fit_rows[0];
    while (lo < hi) {
        const size_t mid = lo + (hi - lo) / 2;
        if (sa2_best_fit_rows[mid].w < w) lo = mid + 1;
        else hi = mid;
    }
    if (lo < sizeof sa2_best_fit_rows / sizeof sa2_best_fit_rows[0]
        && sa2_best_fit_rows[lo].w == w) {
        *out = sa2_best_fit_rows[lo].b;
        return 2;
    }
    *out = (uint8_t)'?';
    return 0;
}

/* The row of `w` in sa2_char_rows, or NULL: U+0000..U+00FF sit at their own
 * index, the rest are searched. */
static const sa2_char_row *char_row(uint16_t w) {
    const size_t n = sizeof sa2_char_rows / sizeof sa2_char_rows[0];
    if (w < 0x100u) return &sa2_char_rows[w];
    size_t lo = 0x100u, hi = n;
    while (lo < hi) {
        const size_t mid = lo + (hi - lo) / 2;
        if (sa2_char_rows[mid].w < w) lo = mid + 1;
        else hi = mid;
    }
    return lo < n && sa2_char_rows[lo].w == w ? &sa2_char_rows[lo] : NULL;
}

uint16_t sa2_char_ctype1(uint16_t w) {
    const sa2_char_row *r = char_row(w);
    return r ? r->ctype1 : 0u;
}

int sa2_char_case(uint16_t w, int upper, uint16_t *out) {
    const sa2_char_row *r = char_row(w);
    *out = r ? (upper ? r->upper : r->lower) : w;
    return r != NULL;
}
