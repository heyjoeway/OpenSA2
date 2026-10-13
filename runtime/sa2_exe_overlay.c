/* The shipped executable's loader and code-section overlay. See sa2_exe_overlay.h. */
#include "sa2_exe_overlay.h"

#include <stdarg.h>
#include <string.h>

#include "guest_memory.h"

static const sa2_exe_overlay_table_t *g_table;
static int  g_table_ok;            /* the installed table passed table_problem */
static char g_table_why[256];
static char g_verify_refusal[SA2_GUEST_REFUSAL_MAX];
static char g_loaded_path[512];

/* ---------------------------------------------------------------- sha256 */

typedef struct { uint32_t h[8]; uint64_t n; uint8_t buf[64]; uint32_t used; } sha_ctx;

static const uint32_t K[64] = {
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u,
    0xab1c5ed5u, 0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu,
    0x9bdc06a7u, 0xc19bf174u, 0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu,
    0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau, 0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
    0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u, 0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu,
    0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u, 0xa2bfe8a1u, 0xa81a664bu,
    0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u, 0x19a4c116u,
    0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u,
    0xc67178f2u,
};

static uint32_t ror(uint32_t x, unsigned r) { return (x >> r) | (x << (32u - r)); }

static void sha_block(sha_ctx *c, const uint8_t *p) {
    uint32_t w[64];
    for (unsigned i = 0; i < 16; i++)
        w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 |
               (uint32_t)p[4 * i + 2] << 8 | (uint32_t)p[4 * i + 3];
    for (unsigned i = 16; i < 64; i++) {
        const uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
        const uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    uint32_t a = c->h[0], b = c->h[1], cc = c->h[2], d = c->h[3];
    uint32_t e = c->h[4], f = c->h[5], g = c->h[6], h = c->h[7];
    for (unsigned i = 0; i < 64; i++) {
        const uint32_t t1 = h + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) +
                            K[i] + w[i];
        const uint32_t t2 = (ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & cc) ^ (b & cc));
        h = g, g = f, f = e, e = d + t1, d = cc, cc = b, b = a, a = t1 + t2;
    }
    c->h[0] += a, c->h[1] += b, c->h[2] += cc, c->h[3] += d;
    c->h[4] += e, c->h[5] += f, c->h[6] += g, c->h[7] += h;
}

static void sha_hex(const uint8_t *p, size_t n, char out[65]) {
    static const uint32_t H0[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                                   0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
    sha_ctx c;
    memcpy(c.h, H0, sizeof H0);
    c.n = (uint64_t)n * 8u;
    size_t whole = n & ~(size_t)63u;
    for (size_t i = 0; i < whole; i += 64) sha_block(&c, p + i);
    c.used = (uint32_t)(n - whole);
    memcpy(c.buf, p + whole, c.used);
    c.buf[c.used++] = 0x80;
    if (c.used > 56u) {
        while (c.used < 64u) c.buf[c.used++] = 0;
        sha_block(&c, c.buf);
        c.used = 0;
    }
    while (c.used < 56u) c.buf[c.used++] = 0;
    for (unsigned i = 0; i < 8; i++) c.buf[56 + i] = (uint8_t)(c.n >> (56 - 8 * i));
    sha_block(&c, c.buf);
    for (unsigned i = 0; i < 8; i++) snprintf(out + 8 * i, 9, "%08x", c.h[i]);
}

/* ---------------------------------------------------------------- refusals */

/* Whose refusal a check's is: the load's, kept as init's (guest_memory.h sa2_guest_refuse), or
 * a verify's, kept apart so a verify never rewrites what the last init said. */
typedef enum { FOR_LOAD, FOR_VERIFY } purpose_t;

__attribute__((format(printf, 3, 4)))
static int refuse(purpose_t why, const char *code, const char *fmt, ...) {
    char msg[640], line[SA2_GUEST_REFUSAL_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    snprintf(line, sizeof line, "SA2_EXE_OVERLAY_REFUSED %s: %s", code, msg);
    if (why == FOR_LOAD) {
        sa2_guest_refuse(line);
    } else {
        snprintf(g_verify_refusal, sizeof g_verify_refusal, "%s", line);
        fprintf(stderr, "%s\n", line);
    }
    return SA2_E_OVERLAY;
}

const char *sa2_exe_overlay_refusal(void) { return sa2_guest_refusal(); }
const char *sa2_exe_overlay_verify_refusal(void) { return g_verify_refusal; }
int sa2_exe_overlay_loaded(void) { return sa2_guest_shipped_loaded; }
const sa2_exe_overlay_table_t *sa2_exe_overlay_table(void) { return g_table; }

/* ---------------------------------------------------------------- the table */

/* Why `t` cannot be used, or NULL: its version, its `.text` extent, its sections, and runs
 * that are sorted, disjoint, inside the extent and inside `bytes`. */
static const char *table_problem(const sa2_exe_overlay_table_t *t, char *why, size_t cap) {
    if (t->version != SA2_EXE_OVERLAY_TABLE_VERSION) {
        snprintf(why, cap, "table version %u, this runtime reads %u", t->version,
                 SA2_EXE_OVERLAY_TABLE_VERSION);
        return why;
    }
    if (!t->sections || t->section_count == 0 || t->section_count > SA2_EXE_OVERLAY_MAX_SECTIONS
        || t->section_count != t->shipped.sections_count) {
        snprintf(why, cap, "%u section entries for a header of %u", t->section_count,
                 t->shipped.sections_count);
        return why;
    }
    unsigned texts = 0;
    for (uint32_t i = 0; i < t->section_count; i++) {
        const sa2_exe_overlay_section_t *s = &t->sections[i];
        if (s->kind != SA2_EXE_SEC_UNMAPPED && !s->guest_name[0]) {
            snprintf(why, cap, "section %u at %#010x has no guest name", i, s->va);
            return why;
        }
        if (s->kind == SA2_EXE_SEC_TEXT) {
            texts++;
            if (s->va != t->text_va || s->mapped != t->text_mapped) {
                snprintf(why, cap, "the TEXT section %#010x+%#x is not the table's .text "
                         "%#010x+%#x", s->va, s->mapped, t->text_va, t->text_mapped);
                return why;
            }
        } else if (s->kind == SA2_EXE_SEC_DATA) {
            if (!s->sha256 || strlen(s->sha256) != 64) {
                snprintf(why, cap, "the DATA section at %#010x has no sha256", s->va);
                return why;
            }
        } else if (s->kind != SA2_EXE_SEC_UNMAPPED) {
            snprintf(why, cap, "section %u has kind %d", i, s->kind);
            return why;
        }
    }
    if (texts != 1) {
        snprintf(why, cap, "%u TEXT sections; the overlay is for exactly one", texts);
        return why;
    }
    uint64_t end_prev = 0;
    for (uint32_t i = 0; i < t->run_count; i++) {
        const sa2_exe_overlay_run_t *r = &t->runs[i];
        if (!r->len || r->va < t->text_va
            || (uint64_t)r->va + r->len > (uint64_t)t->text_va + t->text_mapped
            || (uint64_t)r->offset + r->len > t->byte_count || (i && r->va <= end_prev)) {
            snprintf(why, cap, "run %u (%#010x+%u at byte %u) is outside .text, outside the "
                     "table's bytes, or not after the run before it", i, r->va, r->len, r->offset);
            return why;
        }
        end_prev = (uint64_t)r->va + r->len;
    }
    return NULL;
}

static int load(const uint8_t *f, size_t n, const char *path);

int sa2_exe_overlay_install(const sa2_exe_overlay_table_t *t) {
    g_table = t;
    g_table_ok = t && !table_problem(t, g_table_why, sizeof g_table_why);
    if (!t) snprintf(g_table_why, sizeof g_table_why, "no table");
    sa2_guest_shipped_loader = load;
    if (!g_table_ok) {
        fprintf(stderr, "sa2_exe_overlay_install: the table is not well formed: %s\n", g_table_why);
        return SA2_E_OVERLAY;
    }
    return SA2_OK;
}

/* ---------------------------------------------------------------- the loader */

static uint16_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }

typedef struct {
    sa2_exe_overlay_header_t h;
    uint32_t sec_off;
} parsed_t;

/* The header fields steps 1 and 2 hold to the table, each where the file holds it: from the
 * file's start, the COFF header (after the PE signature) or the optional header. The MZ and PE
 * signatures are checked by `parse` itself. Every entry is read from the file and compared,
 * and `verified_header` marks exactly these bytes, so the census and the checks are one list.
 * tools/s7/text_readset.py HEADER_AT is the same list; a test flips each byte it names. */
enum { AT_FILE, AT_COFF, AT_OPT };
#define OFF(f) offsetof(sa2_exe_overlay_header_t, f)
static const struct { const char *name; int at; uint32_t rel, size; size_t off; } FIELDS[] = {
    {"e_lfanew",                AT_FILE, 0x3C, 4, OFF(e_lfanew)},
    {"sections_count",          AT_COFF, 2,    2, OFF(sections_count)},
    {"optional_header_size",    AT_COFF, 16,   2, OFF(optional_header_size)},
    {"magic",                   AT_OPT,  0,    2, OFF(magic)},
    {"image_base",              AT_OPT,  28,   4, OFF(image_base)},
    {"size_of_image",           AT_OPT,  56,   4, OFF(size_of_image)},
    {"size_of_headers",         AT_OPT,  60,   4, OFF(size_of_headers)},
    {"rva_count",               AT_OPT,  92,   4, OFF(rva_count)},
    {"import_directory.rva",    AT_OPT,  104,  4, OFF(import_directory)},
    {"import_directory.size",   AT_OPT,  108,  4, OFF(import_directory) + 4},
    {"resource_directory.rva",  AT_OPT,  112,  4, OFF(resource_directory)},
    {"resource_directory.size", AT_OPT,  116,  4, OFF(resource_directory) + 4},
    {"clr_directory.rva",       AT_OPT,  208,  4, OFF(clr_directory)},
    {"clr_directory.size",      AT_OPT,  212,  4, OFF(clr_directory) + 4},
};
#undef OFF
#define NFIELDS (sizeof FIELDS / sizeof FIELDS[0])
#define OPT_READ 216u               /* the optional header's bytes FIELDS reaches */
/* Of each 40-byte section entry: VirtualSize, VirtualAddress, SizeOfRawData; Characteristics. */
static const struct { uint32_t rel, size; } ENTRY_VERIFIED[] = {{8, 12}, {36, 4}};

static uint32_t at_base(int at, uint32_t nt) {
    return at == AT_FILE ? 0u : at == AT_COFF ? nt + 4u : nt + 24u;
}

/* The header fields, read with every offset bounded by the file. NULL, or why not. */
static const char *parse(const uint8_t *f, size_t n, parsed_t *out) {
    if (n < 0x40 || rd16(f) != 0x5A4Du) return "no MZ header";
    const uint32_t nt = rd32(f + 0x3C);
    if ((uint64_t)nt + 24u + OPT_READ > n) return "e_lfanew points past the file";
    if (rd32(f + nt) != 0x00004550u) return "no PE signature";
    sa2_exe_overlay_header_t *h = &out->h;
    memset(h, 0, sizeof *h);
    for (size_t i = 0; i < NFIELDS; i++) {
        const uint8_t *q = f + at_base(FIELDS[i].at, nt) + FIELDS[i].rel;
        const uint32_t v = FIELDS[i].size == 2 ? rd16(q) : rd32(q);
        memcpy((uint8_t *)h + FIELDS[i].off, &v, 4);
    }
    out->sec_off = nt + 24u + h->optional_header_size;
    if (h->sections_count > SA2_EXE_OVERLAY_MAX_SECTIONS
        || (uint64_t)out->sec_off + 40u * h->sections_count > n) return "the section table "
        "runs past the file";
    return NULL;
}

static uint32_t field(const sa2_exe_overlay_header_t *h, size_t off) {
    uint32_t v;
    memcpy(&v, (const uint8_t *)h + off, 4);
    return v;
}

static void mark(uint8_t *bits, uint64_t at, uint32_t len) {
    for (uint64_t x = at; x < at + len && x < SA2_GUEST_HEAD_CENSUS; x++)
        bits[x >> 3] |= (uint8_t)(1u << (x & 7u));
}

/* The header bytes steps 1 and 2 hold to the table, as file offsets in the census's page:
 * the signatures, FIELDS, and the verified parts of every section entry. */
static void verified_header(const sa2_exe_overlay_table_t *t, uint8_t *bits) {
    const uint32_t nt = t->shipped.e_lfanew;
    mark(bits, 0, 2);
    mark(bits, nt, 4);
    for (size_t i = 0; i < NFIELDS; i++)
        mark(bits, (uint64_t)at_base(FIELDS[i].at, nt) + FIELDS[i].rel, FIELDS[i].size);
    const uint64_t sec = (uint64_t)nt + 24u + t->shipped.optional_header_size;
    for (uint32_t i = 0; i < t->section_count; i++)
        for (size_t k = 0; k < sizeof ENTRY_VERIFIED / sizeof ENTRY_VERIFIED[0]; k++)
            mark(bits, sec + 40u * i + ENTRY_VERIFIED[k].rel, ENTRY_VERIFIED[k].size);
}

/* What `check` found: the header fields, and each section's mapped length and raw pointer. */
typedef struct {
    parsed_t p;
    uint32_t mapped[SA2_EXE_OVERLAY_MAX_SECTIONS], raw_ptr[SA2_EXE_OVERLAY_MAX_SECTIONS];
} checked_t;

/* Steps 1 to 3 of sa2_exe_overlay.h, and the guest layout: SA2_OK, or a refusal. Reads `f`
 * only; writes no guest memory. */
static int check(purpose_t use, const uint8_t *f, size_t n, const char *path, checked_t *c) {
    const sa2_exe_overlay_table_t *t = g_table;
    if (!t || !g_table_ok)
        return refuse(use, "table", "%s: the installed overlay table is not well formed: %s", path,
                      g_table_why);
    const char *why = parse(f, n, &c->p);
    if (why) return refuse(use, "not-pe", "%s: %s", path, why);
    const parsed_t *p = &c->p;
    uint32_t *mapped = c->mapped, *raw_ptr = c->raw_ptr;

    /* 1. The header fields the runtime and the guest read. */
    for (size_t i = 0; i < NFIELDS; i++) {
        const uint32_t got = field(&p->h, FIELDS[i].off), want = field(&t->shipped, FIELDS[i].off);
        if (got != want)
            return refuse(use, "header-field", "%s: header field %s is %#x, the shipped "
                          "executable's is %#x", path, FIELDS[i].name, got, want);
    }

    /* 2. The section entries, and what each maps. */
    for (uint32_t i = 0; i < t->section_count; i++) {
        const uint8_t *s = f + p->sec_off + 40u * i;
        const sa2_exe_overlay_section_t *w = &t->sections[i];
        const uint32_t va = p->h.image_base + rd32(s + 12), vsize = rd32(s + 8);
        const uint32_t raw = rd32(s + 16), chars = rd32(s + 36);
        raw_ptr[i] = rd32(s + 20);
        if (va != w->va || vsize != w->virtual_size || raw != w->raw_size
            || chars != w->characteristics)
            return refuse(use, "section-table", "%s: section %u is %#010x vsize %#x raw %#x "
                          "characteristics %#x; the shipped executable's (%s) is %#010x vsize %#x "
                          "raw %#x characteristics %#x", path, i, va, vsize, raw, chars, w->name,
                          w->va, w->virtual_size, w->raw_size, w->characteristics);
        /* guest_memory.c map_pe's length: the file bytes, at most the virtual size. */
        uint32_t m = raw;
        if (raw_ptr[i] >= n) m = 0;
        else if ((uint64_t)raw_ptr[i] + m > n) m = (uint32_t)(n - raw_ptr[i]);
        if (m > vsize && vsize != 0) m = vsize;
        mapped[i] = m;
        if (w->kind != SA2_EXE_SEC_UNMAPPED && m != w->mapped)
            return refuse(use, "section-table", "%s: section %s at %#010x maps %#x bytes, the "
                          "shipped executable's maps %#x (a truncated file?)", path, w->name,
                          w->va, m, w->mapped);
    }

    /* 3. Every DATA section's mapped bytes, by hash. */
    for (uint32_t i = 0; i < t->section_count; i++) {
        const sa2_exe_overlay_section_t *w = &t->sections[i];
        if (w->kind != SA2_EXE_SEC_DATA) continue;
        char hex[65];
        sha_hex(f + raw_ptr[i], mapped[i], hex);
        if (strcmp(hex, w->sha256) != 0)
            return refuse(use, "section-hash", "%s: section %s at %#010x (%#x bytes) hashes to %s, "
                          "the shipped executable's to %s", path, w->name, w->va, mapped[i], hex,
                          w->sha256);
    }

    /* The guest layout, as guest_memory.c map_pe holds the unpacked file to it. */
    if (t->unpacked.image_base != SA2_GUEST_IMAGE_BASE || p->h.image_base != SA2_GUEST_IMAGE_BASE
        || (uint64_t)t->unpacked.image_base + t->unpacked.size_of_image > SA2_GUEST_TSTACK_BASE)
        return refuse(use, "image", "%s: image %#x+%#x does not fit below the guest thread "
                      "stacks at %#x", path, t->unpacked.image_base, t->unpacked.size_of_image,
                      SA2_GUEST_TSTACK_BASE);
    for (uint32_t i = 0; i < t->section_count; i++) {
        const sa2_exe_overlay_section_t *w = &t->sections[i];
        if (w->kind != SA2_EXE_SEC_UNMAPPED && mapped[i] && !sa2_gp(w->va, mapped[i]))
            return refuse(use, "image", "%s: section %s at %#010x+%#x is outside the guest "
                          "space", path, w->name, w->va, mapped[i]);
    }
    return SA2_OK;
}

int sa2_exe_overlay_verify(const uint8_t *file, size_t n, const char *path) {
    checked_t c;
    g_verify_refusal[0] = 0;
    return check(FOR_VERIFY, file, n, path, &c);
}

/* guest_memory.c calls this from sa2_guest_init, which has reset the refusal and
 * sa2_guest_shipped_loaded. */
static int load(const uint8_t *f, size_t n, const char *path) {
    const sa2_exe_overlay_table_t *t = g_table;
    checked_t c;
    int rc = check(FOR_LOAD, f, n, path, &c);
    if (rc != SA2_OK) return rc;
    const parsed_t p = c.p;
    const uint32_t *mapped = c.mapped, *raw_ptr = c.raw_ptr;

    /* Every check passed: map. The header page and the DATA sections as-is, `.text` filled
     * and its pages written as map_pe writes them, `.bind` not at all. */
    sa2_guest.image_size = t->unpacked.size_of_image;
    uint32_t hdr = p.h.size_of_headers;
    if (hdr > n) hdr = (uint32_t)n;
    memcpy(sa2_guest.base + p.h.image_base, f, hdr);
    sa2_guest_note_write(p.h.image_base, hdr);
    unsigned k = 0;
    for (uint32_t i = 0; i < t->section_count; i++) {
        const sa2_exe_overlay_section_t *w = &t->sections[i];
        if (w->kind == SA2_EXE_SEC_UNMAPPED) continue;
        sa2_section_t *d = &sa2_guest.sections[k++];
        memset(d, 0, sizeof *d);
        memcpy(d->name, w->guest_name, 8);
        d->va = w->va;
        d->virtual_size = w->virtual_size;
        d->raw_size = mapped[i];
        d->characteristics = w->characteristics;
        d->readable   = (w->characteristics & 0x40000000u) != 0;
        d->writable   = (w->characteristics & 0x80000000u) != 0;
        d->executable = (w->characteristics & 0x20000000u) != 0;
        if (!mapped[i]) continue;
        if (w->kind == SA2_EXE_SEC_DATA)
            memcpy(sa2_guest.base + w->va, f + raw_ptr[i], mapped[i]);
        else
            memset(sa2_guest.base + w->va, (int)SA2_EXE_TEXT_FILL, mapped[i]);
        sa2_guest_note_write(w->va, mapped[i]);
    }
    sa2_guest.section_count = k;

    /* `.text` byte by byte for the poison tier, from here on: only the runs are written. */
    rc = sa2_guest_fine_begin(t->text_va, t->text_va + t->text_mapped);
    if (rc != SA2_OK)
        return refuse(FOR_LOAD, "image", "%s: the poison tier's byte range for .text "
                      "%#010x+%#x could not begin (%d)", path, t->text_va, t->text_mapped, rc);
    for (uint32_t i = 0; i < t->run_count; i++) {
        const sa2_exe_overlay_run_t *r = &t->runs[i];
        memcpy(sa2_guest.base + r->va, t->bytes + r->offset, r->len);
        sa2_guest_note_write(r->va, r->len);
    }
    snprintf(g_loaded_path, sizeof g_loaded_path, "%s", path);
    sa2_guest_shipped_loaded = 1;
    return SA2_OK;
}

/* ---------------------------------------------------------------- the report */

static void json_str(FILE *out, const char *s) {
    fputc('"', out);
    for (; s && *s; s++) {
        const unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') fprintf(out, "\\%c", c);
        else if (c < 0x20) fprintf(out, "\\u%04x", c);
        else fputc(c, out);
    }
    fputc('"', out);
}

/* The set bits of `bits` (one a byte of the census's page) as [[offset, length], ...]. */
static void json_ranges(FILE *out, const uint8_t *bits) {
    fputc('[', out);
    int first = 1;
    for (uint32_t x = 0; x < SA2_GUEST_HEAD_CENSUS;) {
        if (!((bits[x >> 3] >> (x & 7u)) & 1u)) { x++; continue; }
        uint32_t y = x;
        while (y < SA2_GUEST_HEAD_CENSUS && ((bits[y >> 3] >> (y & 7u)) & 1u)) y++;
        fprintf(out, "%s[\"0x%03x\", %u]", first ? "" : ", ", x, y - x);
        first = 0;
        x = y;
    }
    fputc(']', out);
}

void sa2_exe_overlay_report(FILE *out) {
    const sa2_exe_overlay_table_t *t = g_table;
    const int loaded = sa2_guest_shipped_loaded;
    uint64_t bytes = 0;
    if (t)
        for (uint32_t i = 0; i < t->run_count; i++) bytes += t->runs[i].len;
    fprintf(out, "{\"table\": %s", t ? "{" : "null");
    if (t) {
        fprintf(out, "\"well_formed\": %s, \"unpacked_sha256\": ", g_table_ok ? "true" : "false");
        json_str(out, t->unpacked_sha256);
        fprintf(out, ", \"readset_sha256\": ");
        json_str(out, t->readset_sha256);
        fprintf(out, ", \"bytes_sha256\": ");
        json_str(out, t->bytes_sha256);
        fprintf(out, ", \"omitted\": ");
        json_str(out, t->omitted);
        fprintf(out, ", \"runs\": %u, \"run_bytes\": %llu, \"text\": [\"0x%08x\", \"0x%08x\"]}",
                t->run_count, (unsigned long long)bytes, t->text_va, t->text_va + t->text_mapped);
    }
    fprintf(out, ", \"loaded\": %s, \"path\": ", loaded ? "true" : "false");
    json_str(out, loaded ? g_loaded_path : "");
    fprintf(out, ", \"refusal\": ");
    json_str(out, sa2_guest_refusal());
    fprintf(out, ", \"poison_mode\": %d", sa2_guest_poison);
    /* The read counts below are collected only under the poison tier: with it off they were
     * not measured, so they read null rather than a zero that looks clean. */
    if (sa2_guest_poison == SA2_POISON_OFF) {
        fputs(", \"text_unwritten_reads\": null, \"text_first\": null, \"text_unkept\": null, "
              "\"text_addresses\": null, \"poison_reads\": null, \"poison_first\": null, "
              "\"header_reads\": null, \"header_read\": null, \"header_unverified\": null}\n", out);
        return;
    }
    fprintf(out, ", \"text_unwritten_reads\": %llu, \"text_first\": "
            "\"0x%08x\", \"text_unkept\": %llu, \"text_addresses\": [",
            (unsigned long long)sa2_guest_fine.reads, sa2_guest_fine.first,
            (unsigned long long)sa2_guest_fine.unkept);
    for (unsigned i = 0; i < sa2_guest_fine.kept_count; i++)
        fprintf(out, "%s\"0x%08x\"", i ? ", " : "", sa2_guest_fine.kept[i]);
    fprintf(out, "], \"poison_reads\": %llu, \"poison_first\": \"0x%08x\"",
            (unsigned long long)sa2_guest_poison_reads, sa2_guest_poison_first);
    /* The header census, and -- for a shipped load -- the bytes of it no check verified. */
    fprintf(out, ", \"header_reads\": %llu, \"header_read\": ",
            (unsigned long long)sa2_guest_head_reads);
    json_ranges(out, sa2_guest_head_read);
    fprintf(out, ", \"header_unverified\": ");
    if (loaded && t) {
        uint8_t verified[SA2_GUEST_HEAD_CENSUS / 8] = {0}, left[SA2_GUEST_HEAD_CENSUS / 8];
        verified_header(t, verified);
        for (size_t i = 0; i < sizeof left; i++)
            left[i] = (uint8_t)(sa2_guest_head_read[i] & ~verified[i]);
        json_ranges(out, left);
    } else {
        fputs("null", out);
    }
    fputs("}\n", out);
}
