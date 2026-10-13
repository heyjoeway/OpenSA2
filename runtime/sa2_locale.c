/* Wine's locale data and code page 936. See sa2_locale.h. */
#include "sa2_locale.h"

#include <stddef.h>

#include "sa2_localedata.h"

/* ------------------------------------------------------------------ locales */

int sa2_locale_valid(uint32_t lcid) {
    switch (lcid) {
    case 0x0000u:           /* LOCALE_NEUTRAL */
    case 0x0400u:           /* LOCALE_USER_DEFAULT */
    case 0x0800u:           /* LOCALE_SYSTEM_DEFAULT */
        return 0;
    case 0x0C00u:           /* LOCALE_CUSTOM_DEFAULT */
    case 0x1000u:           /* LOCALE_CUSTOM_UNSPECIFIED */
    case 0x1400u:           /* LOCALE_CUSTOM_UI_DEFAULT */
        return 1;           /* NlsValidateLocale's user (UI) locale */
    default:
        break;
    }
    uint32_t lo = 0, hi = SA2_NLS_LCIDS;
    while (lo < hi) {
        const uint32_t mid = lo + (hi - lo) / 2u;
        if (sa2_nls_lcids[mid] == lcid) return 1;
        if (sa2_nls_lcids[mid] < lcid) lo = mid + 1u;
        else hi = mid;
    }
    return 0;
}

uint32_t sa2_locale_installed_count(void) { return SA2_NLS_INSTALLED; }
uint32_t sa2_locale_installed_at(uint32_t i) {
    return i < SA2_NLS_INSTALLED ? sa2_nls_installed[i] : 0u;
}

int sa2_locale_data(uint32_t lcid, uint32_t lctype, sa2_locale_answer *out) {
    uint32_t lo = 0, hi = SA2_NLS_ANSWERS;
    while (lo < hi) {
        const uint32_t mid = lo + (hi - lo) / 2u;
        const sa2_nls_answer *r = &sa2_nls_answers[mid];
        if (r->lcid == lcid && r->lctype == lctype) {
            out->kind = r->kind;
            out->w = &sa2_nls_units[r->at];
            out->n = r->n;
            out->value = r->value;
            return 1;
        }
        if (r->lcid < lcid || (r->lcid == lcid && r->lctype < lctype)) lo = mid + 1u;
        else hi = mid;
    }
    return 0;
}

int sa2_locale_codepages(uint32_t lcid, uint32_t *acp, uint32_t *oemcp) {
    uint32_t lo = 0, hi = SA2_NLS_CODEPAGES;
    while (lo < hi) {
        const uint32_t mid = lo + (hi - lo) / 2u;
        if (sa2_nls_cps[mid].lcid == lcid) {
            *acp = sa2_nls_cps[mid].acp;
            *oemcp = sa2_nls_cps[mid].oemcp;
            return 1;
        }
        if (sa2_nls_cps[mid].lcid < lcid) lo = mid + 1u;
        else hi = mid;
    }
    return 0;
}

int sa2_locale_numeric(uint32_t lctype) {
    switch (lctype & 0xFFFFu) {
    case 0x0001u: case 0x0005u: case 0x0009u: case 0x000Au: case 0x000Bu:
    case 0x0019u: case 0x001Au:
    case 0x0052u: case 0x0053u: case 0x0054u: case 0x0055u: case 0x0056u: case 0x0057u:
    case 0x1004u: case 0x1009u:
        return 1;
    default:
        return 0;
    }
}

/* ------------------------------------------------------------ code page 936 */

int sa2_cp936_is_lead(uint8_t b) {
    return b >= SA2_CP936_LEAD_FIRST && b <= SA2_CP936_LEAD_LAST;
}

uint16_t sa2_cp936_single(uint8_t b) { return sa2_nls_936_single[b]; }

uint16_t sa2_cp936_default(void) { return SA2_CP936_DEFAULT; }

void sa2_cp936_lead_bytes(uint8_t out[12]) {
    for (unsigned i = 0; i < 12u; i++) out[i] = 0;
    out[0] = SA2_CP936_LEAD_FIRST;
    out[1] = SA2_CP936_LEAD_LAST;
}

uint16_t sa2_cp936_pair(uint8_t lead, uint8_t trail) {
    if (!sa2_cp936_is_lead(lead) || trail < SA2_CP936_TRAIL_FIRST || trail > SA2_CP936_TRAIL_LAST)
        return SA2_CP936_UNIDEFAULT;
    return sa2_nls_936_pairs[(uint32_t)(lead - SA2_CP936_LEAD_FIRST) * SA2_CP936_TRAILS +
                           (uint32_t)(trail - SA2_CP936_TRAIL_FIRST)];
}

/* WideCharTable, derived once from the two tables above: the first single byte,
 * then the first pair in (lead, trail) order, that maps to the unit, pairs that
 * map to the Unicode default character being Wine's invalid trail bytes; then
 * Wine's best fit over it; the default character where neither answers.
 * tools/s4/locale_check.py tables holds every unit of it to c_936.nls's own
 * table. */
static uint16_t g_wc[0x10000];
static int      g_wc_ready;

static void wc_build(void) {
    for (uint32_t w = 0; w < 0x10000u; w++) g_wc[w] = 0;
    static uint8_t seen[0x10000];
    for (uint32_t w = 0; w < 0x10000u; w++) seen[w] = 0;
    for (uint32_t b = 0; b < 256u; b++) {
        if (sa2_cp936_is_lead((uint8_t)b)) continue;
        const uint16_t w = sa2_nls_936_single[b];
        if (!seen[w]) { seen[w] = 1; g_wc[w] = (uint16_t)b; }
    }
    for (uint32_t lead = SA2_CP936_LEAD_FIRST; lead <= SA2_CP936_LEAD_LAST; lead++) {
        for (uint32_t t = SA2_CP936_TRAIL_FIRST; t <= SA2_CP936_TRAIL_LAST; t++) {
            const uint16_t w = sa2_cp936_pair((uint8_t)lead, (uint8_t)t);
            if (w == SA2_CP936_UNIDEFAULT || seen[w]) continue;
            seen[w] = 1;
            g_wc[w] = (uint16_t)(lead << 8 | t);
        }
    }
    for (uint32_t w = 0; w < 0x10000u; w++)
        if (!seen[w]) g_wc[w] = SA2_CP936_DEFAULT;
    for (uint32_t i = 0; i < SA2_CP936_FITS; i++)
        g_wc[sa2_nls_936_fits[i].w] = sa2_nls_936_fits[i].mb;
    g_wc_ready = 1;
}

uint16_t sa2_cp936_table(uint16_t w) {
    if (!g_wc_ready) wc_build();
    return g_wc[w];
}

int sa2_cp936_valid_mapping(uint16_t w, int no_best_fit) {
    const uint16_t ch = sa2_cp936_table(w);
    if (no_best_fit || ch == SA2_CP936_DEFAULT) {
        if (ch >> 8) return sa2_cp936_pair((uint8_t)(ch >> 8), (uint8_t)(ch & 0xFFu)) == w;
        return sa2_nls_936_single[ch] == w;
    }
    return 1;
}

static int private_use(uint16_t w) { return w >= 0xE000u && w <= 0xF8FFu; }

int sa2_cp936_invalid_chars(const uint8_t *src, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) {
        if (sa2_cp936_is_lead(src[i])) {
            if (i + 1u == n) return 1;                     /* a partial character */
            if (sa2_cp936_pair(src[i], src[i + 1u]) == SA2_CP936_UNIDEFAULT &&
                (uint16_t)(src[i] << 8 | src[i + 1u]) != SA2_CP936_TRANSUNI)
                return 1;
            i++;
            continue;
        }
        const uint16_t w = sa2_nls_936_single[src[i]];
        if (w == SA2_CP936_UNIDEFAULT && src[i] != SA2_CP936_TRANSUNI) return 1;
        if (private_use(w)) return 1;
    }
    return 0;
}

uint32_t sa2_cp936_mbtowc(const uint8_t *src, uint32_t n, uint16_t *dst, uint32_t dstlen,
                          int *overflow) {
    *overflow = 0;
    if (!dstlen) {
        uint32_t count = 0;
        for (uint32_t i = 0; i < n; i++, count++)
            if (sa2_cp936_is_lead(src[i]) && i + 1u < n && src[i + 1u]) i++;
        return count;
    }
    uint32_t i = 0, k = 0;
    for (; i < n && k < dstlen; i++, k++) {
        if (sa2_cp936_is_lead(src[i])) {
            if (i + 1u < n && src[i + 1u]) {
                dst[k] = sa2_cp936_pair(src[i], src[i + 1u]);
                i++;
            } else {
                dst[k] = SA2_CP936_UNIDEFAULT;
            }
        } else {
            dst[k] = sa2_nls_936_single[src[i]];
        }
    }
    if (i < n) *overflow = 1;
    return k;
}

uint32_t sa2_cp936_wctomb(const uint16_t *src, uint32_t n, uint8_t *dst, uint32_t dstlen,
                          const uint8_t defchar[2], int no_best_fit, int *used, int *overflow) {
    const uint16_t def = defchar[1] ? (uint16_t)(defchar[0] << 8 | defchar[1]) : defchar[0];
    *used = 0;
    *overflow = 0;
    if (!dstlen) {
        uint32_t count = 0;
        for (uint32_t i = 0; i < n; i++, count++) {
            uint16_t res = sa2_cp936_table(src[i]);
            if (!sa2_cp936_valid_mapping(src[i], no_best_fit)) { res = def; *used = 1; }
            if (res & 0xFF00u) count++;
        }
        return count;
    }
    uint32_t i = 0, k = 0;
    for (; i < n && k < dstlen; i++) {
        uint16_t res = sa2_cp936_table(src[i]);
        if (!sa2_cp936_valid_mapping(src[i], no_best_fit)) { res = def; *used = 1; }
        if (res & 0xFF00u) {
            if (k + 1u == dstlen) break;                   /* no partial character */
            dst[k++] = (uint8_t)(res >> 8);
        }
        dst[k++] = (uint8_t)(res & 0xFFu);
    }
    if (i < n) *overflow = 1;
    return k;
}
