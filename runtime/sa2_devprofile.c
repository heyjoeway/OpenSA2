/* The device profile reader. The grammar, the refusals and why the profile
 * exists are in sa2_devprofile.h; tools/s4/device_profile.py is the same
 * reader in Python, message for message, and
 * Documentation/scripts/tests/test_device_profile.py holds the two together.
 *
 * Everything is read once, at configuration, into static storage bounded by
 * the header's limits; the accessors only read it. A problem traps with the
 * file and line, outside any sa2_call, so the run ends before the guest
 * starts: a profile half-read is a device set nobody recorded.
 */
#include "sa2_devprofile.h"
#include "sa2_trap.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ state */

typedef struct {
    uint32_t        processors, process_mask, system_mask;
    uint32_t        syskeyboard, sysmouse;
    uint32_t        gc_n;
    sa2_dp_didevice gc[SA2_DP_MAX_GAMECTRL];
    uint32_t        gc_create[SA2_DP_MAX_GAMECTRL];
    uint32_t        xinput[SA2_DP_XINPUT_SLOTS];
    uint32_t        coinit, blanket;
    uint32_t        pnp_n;
    char            pnp[SA2_DP_MAX_WMI_PNP][SA2_DP_DEVICEID_MAX];
    uint32_t        dd_n;
    sa2_dp_ddraw    dd[SA2_DP_MAX_DDRAW];
    int32_t         dd_mon[SA2_DP_MAX_DDRAW];
    uint32_t        vidmem_hr, vidmem_total;
    uint32_t        xa_flags, xa_channels, xa_rate;
    int             loc;                             /* the locale section is present */
    uint32_t        loc_lcid, loc_system, loc_acp, loc_oemcp;
    uint32_t        loc_error[SA2_DP_LOCALE_TYPES];  /* 0: answered */
    uint32_t        loc_n[SA2_DP_LOCALE_TYPES];      /* UTF-16 units, no NUL */
    uint16_t        loc_w[SA2_DP_LOCALE_TYPES][SA2_DP_LOCALE_VALUE_MAX];
    int             env;                             /* the environment section is present */
    uint32_t        env_strings, env_crt;
    uint32_t        env_crt_units;                   /* the first `crt` strings, NULs included */
    uint16_t        env_w[SA2_DP_ENV_MAX_UNITS];     /* every string, each with its NUL */
    int             std;                             /* the standard-handle section is present */
    uint32_t        std_handle[SA2_DP_STD_HANDLES], std_type[SA2_DP_STD_HANDLES];
} profile;

static const uint32_t LOCALE_LCTYPES[SA2_DP_LOCALE_TYPES] = {SA2_DP_LOCALE_LCTYPES};
_Static_assert(sizeof LOCALE_LCTYPES / sizeof LOCALE_LCTYPES[0] == SA2_DP_LOCALE_TYPES,
               "SA2_DP_LOCALE_TYPES is the length of SA2_DP_LOCALE_LCTYPES");

static profile g_p;
static int     g_default = 1;
static int     g_configured;
static char    g_source[512] = "default";
static char    g_sha[65];

/* The plain keyboard-and-mouse default (sa2_devprofile.h). The DirectDraw
 * default display's strings are the ones Wine's DirectDrawEnumerateExA hands
 * its NULL-GUID interface ("DirectDraw HAL", native-outcomes-v1a/
 * native-run.log:13916, and "display"); the game's callback reads neither. */
static void set_default(profile *p) {
    memset(p, 0, sizeof *p);
    p->processors = 1u;                 /* the runtime's one processor before profiles */
    p->process_mask = p->system_mask = 1u;
    for (unsigned s = 0; s < SA2_DP_XINPUT_SLOTS; s++) p->xinput[s] = SA2_DP_NOT_CONNECTED;
    p->dd_n = 1;
    snprintf(p->dd[0].description, sizeof p->dd[0].description, "%s", "DirectDraw HAL");
    snprintf(p->dd[0].name, sizeof p->dd[0].name, "%s", "display");
    p->dd_mon[0] = -1;
    p->vidmem_total = SA2_DP_DEFAULT_VIDMEM;
    p->xa_channels = SA2_DP_DEFAULT_CHANNELS;
    p->xa_rate = SA2_DP_DEFAULT_RATE;
}

/* ---------------------------------------------------------------- sha256 */

typedef struct { uint32_t h[8]; uint64_t n; uint8_t buf[64]; uint32_t used; } sha_ctx;

static const uint32_t SHA_K[64] = {
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
                            SHA_K[i] + w[i];
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
    c.used = 0;
    for (size_t i = 0; i < n; i++) {
        c.buf[c.used++] = p[i];
        if (c.used == 64u) { sha_block(&c, c.buf); c.used = 0; }
    }
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

/* ---------------------------------------------------------------- reader */

typedef struct {
    const char    *src;              /* the path, as the environment gave it */
    const uint8_t *buf;
    size_t         len, pos;         /* the file; pos is the next line's first byte */
    unsigned       line;
    const char    *s;                /* the current line, without its LF */
    size_t         n, k;             /* its length, and the cursor in it */
} rd;

__attribute__((noreturn, format(printf, 2, 3)))
static void refuse(rd *r, const char *fmt, ...) {
    char msg[768];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    sa2_trap_note("%s:%u: %s", r->src, r->line, msg);
    sa2_trap_raise(SA2_TRAP_FAULT, 0);
}

static void next_line(rd *r) {
    r->line++;
    if (r->pos >= r->len) refuse(r, "the file ends before `end`");
    const uint8_t *p = r->buf + r->pos;
    const size_t rest = r->len - r->pos;
    const uint8_t *nl = memchr(p, '\n', rest);
    const size_t n = nl ? (size_t)(nl - p) : rest;
    for (size_t i = 0; i < n; i++)
        if (p[i] < 0x20 || p[i] > 0x7e) refuse(r, "a byte outside 0x20-0x7e (0x%02x)", p[i]);
    if (!nl) refuse(r, "the last line does not end in LF");
    if (!n) refuse(r, "an empty line");
    r->s = (const char *)p;
    r->n = n;
    r->k = 0;
    r->pos += n + 1;
}

enum { V_HEX, V_DEC, V_GUID, V_GUIDN, V_STR, V_STRN, V_MON };

typedef struct { const char *key; int type; uint32_t max; } fdef;   /* max: a string's bytes */
typedef struct { const char *kind; unsigned nf; fdef f[10]; } rdef;

static const rdef RECORDS[] = {
    {"system", 3, {{"processors", V_DEC, 0}, {"process_mask", V_HEX, 0},
                   {"system_mask", V_HEX, 0}}},
    {"dinput8_create", 1, {{"hr", V_HEX, 0}}},
    {"dinput8_syskeyboard", 1, {{"hr", V_HEX, 0}}},
    {"dinput8_sysmouse", 1, {{"hr", V_HEX, 0}}},
    {"dinput8_gamectrl", 10, {{"index", V_DEC, 0}, {"instance", V_GUID, 0},
                              {"product", V_GUID, 0}, {"ffdriver", V_GUID, 0},
                              {"devtype", V_HEX, 0}, {"usage_page", V_HEX, 0},
                              {"usage", V_HEX, 0},
                              {"instance_name", V_STR, SA2_DP_NAME_MAX - 1},
                              {"product_name", V_STR, SA2_DP_NAME_MAX - 1},
                              {"create", V_HEX, 0}}},
    {"dinput8_enum", 2, {{"hr", V_HEX, 0}, {"count", V_DEC, 0}}},
    {"xinput_caps", 2, {{"slot", V_DEC, 0}, {"code", V_HEX, 0}}},
    {"wmi_coinit", 1, {{"hr", V_HEX, 0}}},
    {"wmi_locator", 1, {{"hr", V_HEX, 0}}},
    {"wmi_connect", 1, {{"hr", V_HEX, 0}}},
    {"wmi_blanket", 1, {{"hr", V_HEX, 0}}},
    {"wmi_enum", 1, {{"hr", V_HEX, 0}}},
    {"wmi_next", 2, {{"hr", V_HEX, 0}, {"returned", V_DEC, 0}}},
    {"wmi_pnp", 4, {{"index", V_DEC, 0}, {"get", V_HEX, 0}, {"vt", V_HEX, 0},
                    {"deviceid", V_STRN, SA2_DP_DEVICEID_MAX - 1}}},
    {"wmi_entities", 1, {{"count", V_DEC, 0}}},
    {"d3d9_adapters", 1, {{"count", V_DEC, 0}}},
    {"ddraw_device", 5, {{"index", V_DEC, 0}, {"guid", V_GUIDN, 0}, {"monitor", V_MON, 0},
                         {"description", V_STR, SA2_DP_NAME_MAX - 1},
                         {"name", V_STR, SA2_DP_NAME_MAX - 1}}},
    {"ddraw_enum", 2, {{"hr", V_HEX, 0}, {"count", V_DEC, 0}}},
    {"ddraw_create", 2, {{"guid", V_GUID, 0}, {"hr", V_HEX, 0}}},
    {"ddraw_query", 1, {{"hr", V_HEX, 0}}},
    {"ddraw_vidmem", 2, {{"hr", V_HEX, 0}, {"total", V_HEX, 0}}},
    {"xaudio2_master", 4, {{"voice", V_DEC, 0}, {"flags", V_HEX, 0}, {"channels", V_DEC, 0},
                           {"rate", V_DEC, 0}}},
    {"locale_user", 5, {{"lcid", V_HEX, 0}, {"system", V_HEX, 0}, {"acp", V_DEC, 0},
                        {"oemcp", V_DEC, 0}, {"valid", V_DEC, 0}}},
    {"locale_info", 3, {{"lctype", V_HEX, 0}, {"error", V_HEX, 0},
                        {"value", V_STRN, SA2_DP_LOCALE_VALUE_MAX}}},
    {"environment", 2, {{"strings", V_DEC, 0}, {"crt", V_DEC, 0}}},
    {"env", 2, {{"index", V_DEC, 0}, {"value", V_STR, SA2_DP_ENV_VALUE_MAX}}},
    {"std_handle", 3, {{"which", V_DEC, 0}, {"handle", V_HEX, 0}, {"type", V_DEC, 0}}},
    {"end", 0, {{NULL, 0, 0}}},
    {"error", 2, {{"step", V_STR, SA2_DP_MAX_BYTES}, {"code", V_HEX, 0}}},
};

/* The digits SA2_DP_MAX_BYTES spells each index and count with
 * (sa2_devprofile.h): enough for the largest an accepted file holds there, or
 * the derived bound would refuse a file the grammar accepts. */
_Static_assert(SA2_DP_MAX_GAMECTRL <= 99u,
               "dinput8_gamectrl's index and dinput8_enum's count are spelled in two digits");
_Static_assert(SA2_DP_MAX_WMI_PNP <= 999u,
               "wmi_pnp's index and wmi_entities' count are spelled in three digits");
_Static_assert(SA2_DP_MAX_DDRAW <= 9u,
               "ddraw_device's index and ddraw_enum's count are spelled in one digit");
_Static_assert(SA2_DP_WMI_BATCH <= 99u, "wmi_next's returned is spelled in two digits");
_Static_assert(SA2_DP_XINPUT_SLOTS <= 10u, "xinput_caps' slot is spelled in one digit");
_Static_assert(SA2_DP_MAX_PROCESSORS <= 99u, "system's processors is spelled in two digits");
_Static_assert(SA2_DP_ENV_MAX_STRINGS <= 256u,
               "environment's counts and env's index are spelled in three digits");
_Static_assert(SA2_DP_STD_HANDLES == 3u, "std_handle's which is spelled in one digit");

static const rdef *rdef_of(const char *kind) {
    for (size_t i = 0; i < sizeof RECORDS / sizeof RECORDS[0]; i++)
        if (!strcmp(RECORDS[i].kind, kind)) return &RECORDS[i];
    return NULL;
}

/* One parsed field. A string's bytes land in `str` (NUL-terminated, at most
 * the field's max); `raw`/`raw_n` is its spelling in the file, quotes included. */
typedef struct {
    uint32_t    u;           /* hex, dec; for a mon: the adapter, with `mon` its kind */
    int         null;        /* guid?, str? or mon: `null` */
    int         mon;         /* 0 null, 1 adapter, 2 other */
    uint8_t     guid[16];
    char        str[SA2_DP_ENV_VALUE_MAX + 1u > SA2_DP_DEVICEID_MAX ? SA2_DP_ENV_VALUE_MAX + 1u
                                                                    : SA2_DP_DEVICEID_MAX];
    const char *raw;
    size_t      raw_n;
} fval;

static int hexd(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}
static int hexu(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* The text of a non-string value: up to the next space or the end of the line. */
static size_t token(rd *r, const char **t) {
    *t = r->s + r->k;
    size_t n = 0;
    while (r->k + n < r->n && r->s[r->k + n] != ' ') n++;
    r->k += n;
    return n;
}

static int parse_dec(const char *t, size_t n, uint32_t *out) {
    if (!n || n > 10 || (n > 1 && t[0] == '0')) return 0;
    uint64_t v = 0;
    for (size_t i = 0; i < n; i++) {
        if (t[i] < '0' || t[i] > '9') return 0;
        v = v * 10u + (uint64_t)(t[i] - '0');
    }
    if (v > 0xFFFFFFFFull) return 0;
    *out = (uint32_t)v;
    return 1;
}

static int parse_guid(const char *t, size_t n, uint8_t g[16]) {
    /* {XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}: Data1, Data2, Data3 little-endian
     * in memory, Data4 as written. */
    if (n != 38 || t[0] != '{' || t[37] != '}' || t[9] != '-' || t[14] != '-' ||
        t[19] != '-' || t[24] != '-')
        return 0;
    static const unsigned at[16] = {1, 3, 5, 7, 10, 12, 15, 17, 20, 22, 25, 27, 29, 31, 33, 35};
    uint8_t b[16];
    for (unsigned i = 0; i < 16; i++) {
        const int hi = hexu(t[at[i]]), lo = hexu(t[at[i] + 1]);
        if (hi < 0 || lo < 0) return 0;
        b[i] = (uint8_t)(hi << 4 | lo);
    }
    const uint8_t order[16] = {3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15};
    for (unsigned i = 0; i < 16; i++) g[i] = b[order[i]];
    return 1;
}

static void parse_str(rd *r, const fdef *f, fval *v) {
    if (r->k >= r->n || r->s[r->k] != '"') refuse(r, "field `%s`: not a quoted string", f->key);
    v->raw = r->s + r->k;
    size_t i = r->k + 1, len = 0;
    for (;;) {
        if (i >= r->n) refuse(r, "field `%s`: the string does not end", f->key);
        const char c = r->s[i];
        if (c == '"') break;
        unsigned char byte = (unsigned char)c;
        if (c == '\\') {
            if (i + 1 >= r->n) refuse(r, "field `%s`: the string does not end", f->key);
            const char e = r->s[i + 1];
            if (e == '"' || e == '\\') {
                byte = (unsigned char)e;
                i += 2;
            } else if (e == 'x') {
                const int hi = i + 2 < r->n ? hexd(r->s[i + 2]) : -1;
                const int lo = i + 3 < r->n ? hexd(r->s[i + 3]) : -1;
                if (hi < 0 || lo < 0)
                    refuse(r, "field `%s`: \\x is not followed by two lower-case hex digits",
                           f->key);
                byte = (unsigned char)(hi << 4 | lo);
                if (!byte) refuse(r, "field `%s`: \\x00 in a string", f->key);
                if (byte >= 0x20 && byte <= 0x7e)
                    refuse(r, "field `%s`: \\x%02x is not that byte's spelling", f->key, byte);
                i += 4;
            } else {
                refuse(r, "field `%s`: an escape other than \\\", \\\\ or \\xhh", f->key);
            }
        } else {
            i += 1;
        }
        if (len < sizeof v->str - 1) v->str[len] = (char)byte;
        len++;
    }
    if (len > f->max)
        refuse(r, "field `%s`: %zu bytes, more than the %u the runtime holds", f->key, len,
               (unsigned)f->max);
    v->str[len < sizeof v->str ? len : sizeof v->str - 1] = 0;
    i += 1;                                   /* the closing quote */
    v->raw_n = (size_t)(r->s + i - v->raw);
    r->k = i;
    if (r->k < r->n && r->s[r->k] != ' ')
        refuse(r, "field `%s`: text after the closing quote", f->key);
}

static void parse_value(rd *r, const fdef *f, fval *v) {
    memset(v, 0, sizeof *v);
    const char *t;
    size_t n;
    switch (f->type) {
    case V_HEX:
        n = token(r, &t);
        if (n != 10 || t[0] != '0' || t[1] != 'x')
            refuse(r, "field `%s`: not 0x and eight lower-case hex digits", f->key);
        for (unsigned i = 2; i < 10; i++) {
            const int d = hexd(t[i]);
            if (d < 0) refuse(r, "field `%s`: not 0x and eight lower-case hex digits", f->key);
            v->u = v->u << 4 | (uint32_t)d;
        }
        return;
    case V_DEC:
        n = token(r, &t);
        if (!parse_dec(t, n, &v->u)) refuse(r, "field `%s`: not a decimal number", f->key);
        return;
    case V_GUID:
    case V_GUIDN:
        n = token(r, &t);
        if (f->type == V_GUIDN && n == 4 && !memcmp(t, "null", 4)) { v->null = 1; return; }
        if (!parse_guid(t, n, v->guid))
            refuse(r, "field `%s`: not a GUID in registry form", f->key);
        return;
    case V_MON: {
        n = token(r, &t);
        if (n == 4 && !memcmp(t, "null", 4)) { v->null = 1; v->mon = 0; return; }
        if (n == 5 && !memcmp(t, "other", 5)) { v->mon = 2; return; }
        if (n > 7 && !memcmp(t, "adapter", 7) && parse_dec(t + 7, n - 7, &v->u)) {
            v->mon = 1;
            return;
        }
        refuse(r, "field `%s`: not null, other or adapter<n>", f->key);
    }
    case V_STR:
    case V_STRN:
        if (f->type == V_STRN && r->k + 4 <= r->n && !memcmp(r->s + r->k, "null", 4) &&
            (r->k + 4 == r->n || r->s[r->k + 4] == ' ')) {
            r->k += 4;
            v->null = 1;
            return;
        }
        parse_str(r, f, v);
        return;
    }
}

/* "`a`", "`a` or `b`", "`a`, `b` or `c`": the kinds a record may be. */
static const char *list_kinds(const char *const *kinds, unsigned nk, char *buf, size_t cap) {
    size_t at = 0;
    buf[0] = 0;
    for (unsigned i = 0; i < nk && at < cap; i++) {
        const char *sep = i == 0 ? "" : i + 1 == nk ? " or " : ", ";
        const int w = snprintf(buf + at, cap - at, "%s`%s`", sep, kinds[i]);
        if (w < 0) break;
        at += (size_t)w;
    }
    return buf;
}

/* The next record, which must be one of `kinds` (one or two); its fields
 * land in `v`. Returns the index in `kinds` of the kind found. An `error`
 * record in its place refuses. */
static unsigned record(rd *r, const char *const *kinds, unsigned nk, fval *v) {
    next_line(r);
    size_t kn = 0;
    while (kn < r->n && r->s[kn] != ' ') kn++;
    char kind[48];
    snprintf(kind, sizeof kind, "%.*s", (int)(kn < 40 ? kn : 40), r->s);
    const rdef *d = kn < 40 ? rdef_of(kind) : NULL;
    unsigned which = nk;
    for (unsigned i = 0; i < nk; i++)
        if (d && !strcmp(kinds[i], d->kind)) which = i;
    if (which == nk && !(d && !strcmp(d->kind, "error"))) {
        char want[160];
        refuse(r, "expected %s, found `%.32s`", list_kinds(kinds, nk, want, sizeof want), kind);
    }
    r->k = kn;
    for (unsigned i = 0; i < d->nf; i++) {
        const fdef *f = &d->f[i];
        const size_t kl = strlen(f->key);
        if (r->k + 1 + kl + 1 > r->n || r->s[r->k] != ' ' ||
            memcmp(r->s + r->k + 1, f->key, kl) || r->s[r->k + 1 + kl] != '=')
            refuse(r, "record `%s`: expected ` %s=` at column %zu", d->kind, f->key, r->k + 1);
        r->k += kl + 2;
        parse_value(r, f, &v[i]);
    }
    if (r->k != r->n) refuse(r, "record `%s`: text after its last field", d->kind);
    if (which == nk)                            /* an error record */
        refuse(r, "the probe could not measure %.*s (code 0x%08x)", (int)v[0].raw_n, v[0].raw,
               v[1].u);
    return which;
}

static uint32_t one(rd *r, const char *kind, fval *v) {
    const char *k[1] = {kind};
    record(r, k, 1, v);
    return v[0].u;
}

/* Whether s[0..n) is UTF-8 as Unicode's Table 3-7 defines it: no overlong
 * form, no surrogate, nothing past U+10FFFF and no sequence cut short -- the
 * set Python's strict decoder accepts, which tools/s4/device_profile.py uses.
 * runtime/sa2_wbem.c converts every DeviceID to UTF-16 and refuses the rest. */
static int utf8_valid(const unsigned char *s, size_t n) {
    size_t i = 0;
    while (i < n) {
        const unsigned c = s[i];
        size_t need;
        unsigned lo = 0x80u, hi = 0xBFu;           /* the second byte's range */
        if (c < 0x80u) { i++; continue; }
        if (c >= 0xC2u && c <= 0xDFu) need = 1;
        else if (c == 0xE0u) { need = 2; lo = 0xA0u; }
        else if ((c >= 0xE1u && c <= 0xECu) || c == 0xEEu || c == 0xEFu) need = 2;
        else if (c == 0xEDu) { need = 2; hi = 0x9Fu; }
        else if (c == 0xF0u) { need = 3; lo = 0x90u; }
        else if (c >= 0xF1u && c <= 0xF3u) need = 3;
        else if (c == 0xF4u) { need = 3; hi = 0x8Fu; }
        else return 0;
        if (n - i - 1u < need || s[i + 1] < lo || s[i + 1] > hi) return 0;
        for (size_t k = 2; k <= need; k++)
            if ((s[i + k] & 0xC0u) != 0x80u) return 0;
        i += need + 1u;
    }
    return 1;
}

/* Valid UTF-8 (utf8_valid) into UTF-16 units; 0 when a character lies
 * outside the Basic Multilingual Plane, which a GetLocaleInfoW answer of the
 * game's LCTYPEs never holds and the runtime would need a surrogate pair for. */
static int utf8_to_utf16(const unsigned char *s, size_t n, uint16_t *w, uint32_t *wn) {
    uint32_t k = 0;
    for (size_t i = 0; i < n;) {
        uint32_t c = s[i];
        size_t need = c < 0x80u ? 0 : c < 0xE0u ? 1 : c < 0xF0u ? 2 : 3;
        if (need == 3) return 0;
        c = need == 0 ? c : need == 1 ? (c & 0x1Fu) : (c & 0x0Fu);
        for (size_t j = 1; j <= need; j++) c = c << 6 | (s[i + j] & 0x3Fu);
        i += need + 1;
        w[k++] = (uint16_t)c;
    }
    *wn = k;
    return 1;
}

/* Whether code page 1252 has a byte for the character, as the runtime's
 * GetLocaleInfoA and WideCharToMultiByte need (runtime/sa2_codepage.c; the
 * table is repeated here so this file stays the reader alone, and
 * Documentation/scripts/tests/test_device_profile.py holds the two to each
 * other through the Python reader's copy). */
static int in_cp1252(uint16_t w) {
    static const uint16_t high[32] = {
        0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
        0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
        0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
        0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178,
    };
    if (w < 0x80u || (w >= 0xA0u && w < 0x100u)) return 1;
    for (unsigned i = 0; i < 32u; i++)
        if (high[i] == w) return 1;
    return 0;
}

/* The system devices' GUIDs, as the image holds them (0x0087360C, 0x008735FC). */
static const uint8_t GUID_SYSKEYBOARD[16] = {0x61, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                             0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};
static const uint8_t GUID_SYSMOUSE[16] = {0x60, 0x2B, 0x1D, 0x6F, 0xA0, 0xD5, 0xCF, 0x11,
                                          0xBF, 0xC7, 0x44, 0x45, 0x53, 0x54, 0x00, 0x00};

/* The locale section, its `locale_user` record read into v. */
static void locale_section(rd *r, profile *p, fval *v) {
    if (!v[0].u) refuse(r, "field `lcid`: 0 names no locale");
    if (v[4].u > 1u) refuse(r, "field `valid`: %u is neither 0 nor 1", v[4].u);
    if (!v[4].u)
        refuse(r, "IsValidLocale answered 0 for the user's locale 0x%08x natively, and "
                  "the runtime's answers 1", v[0].u);
    if (v[2].u != SA2_DP_LOCALE_ACP)
        refuse(r, "GetACP answered %u natively, and the runtime's code-page tables are "
                  "%u's", v[2].u, SA2_DP_LOCALE_ACP);
    p->loc = 1;
    p->loc_lcid = v[0].u;
    p->loc_system = v[1].u;
    p->loc_acp = v[2].u;
    p->loc_oemcp = v[3].u;
    for (uint32_t i = 0; i < SA2_DP_LOCALE_TYPES; i++) {
        one(r, "locale_info", v);
        if (v[0].u != LOCALE_LCTYPES[i])
            refuse(r, "LCTYPE 0x%08x where 0x%08x was next", v[0].u, LOCALE_LCTYPES[i]);
        if (!v[1].u != !v[2].null)
            refuse(r, "LCTYPE 0x%08x: a value is null exactly when its error is not 0",
                   v[0].u);
        p->loc_error[i] = v[1].u;
        if (v[1].u) continue;
        const unsigned char *u = (const unsigned char *)v[2].str;
        const size_t un = strlen(v[2].str);
        if (!utf8_valid(u, un)) refuse(r, "LCTYPE 0x%08x: not valid UTF-8", v[0].u);
        if (!utf8_to_utf16(u, un, p->loc_w[i], &p->loc_n[i]))
            refuse(r, "LCTYPE 0x%08x: a character past U+FFFF natively, and the "
                      "runtime's answers hold none", v[0].u);
        for (uint32_t c = 0; c < p->loc_n[i]; c++)
            if (!in_cp1252(p->loc_w[i][c]))
                refuse(r, "LCTYPE 0x%08x: U+%04X has no byte of its own in code page %u, "
                          "and a reader takes no value GetLocaleInfoA would answer through "
                          "the best fit", v[0].u, (unsigned)p->loc_w[i][c], SA2_DP_LOCALE_ACP);
        if (v[0].u == 0x1004u) {
            char want[16];
            snprintf(want, sizeof want, "%u", p->loc_acp);
            if (strcmp(v[2].str, want))
                refuse(r, "LOCALE_IDEFAULTANSICODEPAGE is \"%s\" and GetACP answered "
                          "%u, and the runtime's C runtime would install a code page "
                          "its conversions refuse", v[2].str, p->loc_acp);
        }
    }
}

/* The environment section, its `environment` record read into v. */
static void environment_section(rd *r, profile *p, fval *v) {
    const uint32_t strings = v[0].u, crt = v[1].u;
    if (!strings) refuse(r, "field `strings`: 0, and a process's block holds at least one");
    if (strings > SA2_DP_ENV_MAX_STRINGS)
        refuse(r, "%u strings in the environment block, more than the %u the runtime holds",
               strings, SA2_DP_ENV_MAX_STRINGS);
    if (!crt)
        refuse(r, "field `crt`: 0, and the C runtime keeps every string of a block it is "
                  "handed");
    if (crt > strings)
        refuse(r, "field `crt`: %u strings the C runtime kept, more than the block's %u", crt,
               strings);
    static uint16_t one_w[SA2_DP_ENV_VALUE_MAX];
    uint32_t units = 0;
    for (uint32_t i = 0; i < strings; i++) {
        one(r, "env", v);
        if (v[0].u != i) refuse(r, "index %u where %u was next", v[0].u, i);
        const unsigned char *u = (const unsigned char *)v[1].str;
        const size_t un = strlen(v[1].str);
        if (!un) refuse(r, "string %u: empty, and an empty string ends the block", i);
        if (!utf8_valid(u, un)) refuse(r, "string %u: not valid UTF-8", i);
        uint32_t n = 0;
        if (!utf8_to_utf16(u, un, one_w, &n))
            refuse(r, "string %u: a character past U+FFFF natively, and the runtime's block "
                      "holds none", i);
        if (units + n + 2u > SA2_DP_ENV_MAX_UNITS)
            refuse(r, "string %u: the block passes the %u units the runtime holds", i,
                   SA2_DP_ENV_MAX_UNITS);
        memcpy(p->env_w + units, one_w, n * sizeof one_w[0]);
        units += n;
        p->env_w[units++] = 0;
        if (i + 1u == crt) p->env_crt_units = units;
    }
    p->env = 1;
    p->env_strings = strings;
    p->env_crt = crt;
}

/* The standard-handle section, its first `std_handle` record read into v. */
static void std_section(rd *r, profile *p, fval *v) {
    static const char *const NAME[SA2_DP_STD_HANDLES] = {"stdin", "stdout", "stderr"};
    for (uint32_t i = 0; i < SA2_DP_STD_HANDLES; i++) {
        if (i) one(r, "std_handle", v);
        if (v[0].u != i) refuse(r, "standard handle %u where %u was next", v[0].u, i);
        const uint32_t h = v[1].u, t = v[2].u;
        if (!h || h == 0xFFFFFFFFu) {
            if (t) refuse(r, "%s: type %u for no handle, which GetFileType is not asked for",
                          NAME[i], t);
        } else if (t == 1u) {
            refuse(r, "%s was a disk file (FILE_TYPE_DISK) natively, and the runtime's "
                      "standard handles hold no file", NAME[i]);
        } else if (t != 0u && t != 2u && t != 3u) {
            refuse(r, "%s: type %u is not FILE_TYPE_UNKNOWN, FILE_TYPE_CHAR or FILE_TYPE_PIPE",
                   NAME[i], t);
        }
        p->std_handle[i] = h;
        p->std_type[i] = t;
    }
    p->std = 1;
}

/* --------------------------------------------------------------- the file */

static fval g_v[10];   /* one record's fields; static, as a string field is 512 bytes */

static void parse(rd *r, profile *p) {
    fval *v = g_v;
    memset(p, 0, sizeof *p);
    next_line(r);
    static const char header[] = "sa2-device-profile 1";
    if (r->n != sizeof header - 1 || memcmp(r->s, header, r->n))
        refuse(r, "the first line is not `sa2-device-profile 1`");

    /* The processors, as GetSystemInfo and GetProcessAffinityMask report them */
    one(r, "system", v);
    const uint32_t cpus = v[0].u;
    if (!cpus) refuse(r, "field `processors`: 0, and a process runs on at least one processor");
    if (cpus > SA2_DP_MAX_PROCESSORS)
        refuse(r, "GetSystemInfo reported %u processors natively, and the runtime's affinity "
                  "masks, a 32-bit process's, hold %u", cpus, SA2_DP_MAX_PROCESSORS);
    const uint32_t all = cpus == 32u ? 0xFFFFFFFFu : (1u << cpus) - 1u;
    static const char *const MASK[2] = {"process_mask", "system_mask"};
    for (unsigned m = 0; m < 2u; m++) {
        if (!v[1 + m].u) refuse(r, "field `%s`: 0x%08x names no processor", MASK[m], v[1 + m].u);
        if (v[1 + m].u & ~all)
            refuse(r, "field `%s`: 0x%08x names a processor past the %u GetSystemInfo reported",
                   MASK[m], v[1 + m].u, cpus);
    }
    if (v[1].u & ~v[2].u)
        refuse(r, "field `process_mask`: 0x%08x names a processor the system mask 0x%08x does "
                  "not, which Windows never answers", v[1].u, v[2].u);
    p->processors = cpus;
    p->process_mask = v[1].u;
    p->system_mask = v[2].u;

    /* DirectInput */
    uint32_t hr = one(r, "dinput8_create", v);
    if (hr)
        refuse(r, "DirectInput8Create answered 0x%08x natively, and the runtime's answers "
                  "S_OK", hr);
    p->syskeyboard = one(r, "dinput8_syskeyboard", v);
    p->sysmouse = one(r, "dinput8_sysmouse", v);
    for (;;) {
        static const char *const k[2] = {"dinput8_gamectrl", "dinput8_enum"};
        if (record(r, k, 2, v) == 1) {
            if (v[1].u != p->gc_n)
                refuse(r, "count %u where %u records came before it", v[1].u, p->gc_n);
            if (v[0].u)
                refuse(r, "EnumDevices answered 0x%08x natively, and the runtime's answers DI_OK",
                       v[0].u);
            break;
        }
        if (v[0].u != p->gc_n) refuse(r, "index %u where %u was next", v[0].u, p->gc_n);
        if (p->gc_n == SA2_DP_MAX_GAMECTRL)
            refuse(r, "more than %u `dinput8_gamectrl` records, the most the runtime holds",
                   SA2_DP_MAX_GAMECTRL);
        if (v[5].u > 0xFFFFu)
            refuse(r, "field `usage_page`: 0x%08x does not fit in 16 bits", v[5].u);
        if (v[6].u > 0xFFFFu)
            refuse(r, "field `usage`: 0x%08x does not fit in 16 bits", v[6].u);
        /* CreateDevice finds a device by its GUID, the system ones included, so
         * a controller that shares one could not be told apart. */
        const int kbd = !memcmp(v[1].guid, GUID_SYSKEYBOARD, 16);
        if (kbd || !memcmp(v[1].guid, GUID_SYSMOUSE, 16))
            refuse(r, "controller %u has %s as its instance GUID, and the runtime's CreateDevice "
                      "tells devices apart by that GUID", p->gc_n,
                   kbd ? "GUID_SysKeyboard" : "GUID_SysMouse");
        for (uint32_t e = 0; e < p->gc_n; e++)
            if (!memcmp(p->gc[e].guid_instance, v[1].guid, 16))
                refuse(r, "controller %u has controller %u's instance GUID, and the runtime's "
                          "CreateDevice tells devices apart by that GUID", p->gc_n, e);
        sa2_dp_didevice *d = &p->gc[p->gc_n];
        memcpy(d->guid_instance, v[1].guid, 16);
        memcpy(d->guid_product, v[2].guid, 16);
        memcpy(d->guid_ffdriver, v[3].guid, 16);
        d->dev_type = v[4].u;
        d->usage_page = (uint16_t)v[5].u;
        d->usage = (uint16_t)v[6].u;
        snprintf(d->instance_name, sizeof d->instance_name, "%s", v[7].str);
        snprintf(d->product_name, sizeof d->product_name, "%s", v[8].str);
        p->gc_create[p->gc_n++] = v[9].u;
    }

    /* XInput */
    for (uint32_t slot = 0; slot < SA2_DP_XINPUT_SLOTS; slot++) {
        one(r, "xinput_caps", v);
        if (v[0].u != slot) refuse(r, "slot %u where %u was next", v[0].u, slot);
        if (v[1].u != SA2_DP_NOT_CONNECTED)
            refuse(r, "XInput slot %u answered %u natively, and the runtime answers only 1167 "
                      "(not connected): a pad's input is part of no recording", slot, v[1].u);
        p->xinput[slot] = v[1].u;
    }

    /* WMI, as IsXInputDevice (0x00864680) meets it. CoInitialize's answer at
     * Init is kept for information and never refused: it is the apartment the
     * main thread was in by Init, not the game's answer (sa2_devprofile.h). */
    p->coinit = one(r, "wmi_coinit", v);
    if ((hr = one(r, "wmi_locator", v)))
        refuse(r, "CoCreateInstance(CLSID_WbemLocator) answered 0x%08x natively, and the "
                  "runtime's answers S_OK", hr);
    if ((hr = one(r, "wmi_connect", v)))
        refuse(r, "ConnectServer answered 0x%08x natively, and the runtime's answers S_OK", hr);
    p->blanket = one(r, "wmi_blanket", v);
    if ((hr = one(r, "wmi_enum", v)))
        refuse(r, "CreateInstanceEnum answered 0x%08x natively, and the runtime's answers S_OK",
               hr);
    int tail = 0;
    for (;;) {
        one(r, "wmi_next", v);
        const uint32_t nhr = v[0].u, got = v[1].u;
        if (got > SA2_DP_WMI_BATCH)
            refuse(r, "Next returned %u objects of the %u asked for", got, SA2_DP_WMI_BATCH);
        if (tail && got)
            refuse(r, "Next returned %u objects after a short batch, and the runtime's returns "
                      "none", got);
        const uint32_t want = got == SA2_DP_WMI_BATCH ? 0u : 1u;
        if (nhr != want)
            refuse(r, "Next answered 0x%08x with %u objects, and the runtime's answers 0x%08x",
                   nhr, got, want);
        for (uint32_t i = 0; i < got; i++) {
            one(r, "wmi_pnp", v);
            if (v[0].u != p->pnp_n) refuse(r, "index %u where %u was next", v[0].u, p->pnp_n);
            if (p->pnp_n == SA2_DP_MAX_WMI_PNP)
                refuse(r, "more than %u `wmi_pnp` records, the most the runtime holds",
                       SA2_DP_MAX_WMI_PNP);
            if (v[2].u > 0xFFFFu) refuse(r, "field `vt`: 0x%08x does not fit in 16 bits", v[2].u);
            if (v[1].u)
                refuse(r, "Get(DeviceID) on entity %u answered 0x%08x natively, and the runtime's "
                          "answers S_OK", p->pnp_n, v[1].u);
            if (v[2].u != 8u)
                refuse(r, "entity %u's DeviceID has vt 0x%04x natively, and the runtime's is a "
                          "BSTR (8)", p->pnp_n, v[2].u);
            if (v[3].null)
                refuse(r, "entity %u's DeviceID is a null BSTR natively, and the runtime's is a "
                          "string", p->pnp_n);
            if (!utf8_valid((const unsigned char *)v[3].str, strlen(v[3].str)))
                refuse(r, "entity %u's DeviceID is not valid UTF-8", p->pnp_n);
            snprintf(p->pnp[p->pnp_n++], SA2_DP_DEVICEID_MAX, "%s", v[3].str);
        }
        if (!got) break;
        if (got < SA2_DP_WMI_BATCH) tail = 1;
    }
    one(r, "wmi_entities", v);
    if (v[0].u != p->pnp_n) refuse(r, "count %u where %u records came before it", v[0].u, p->pnp_n);

    /* Direct3D's adapters, then DirectDraw as 0x00865D20 meets it */
    const uint32_t adapters = one(r, "d3d9_adapters", v);
    if (adapters != 1u)
        refuse(r, "Direct3D reported %u adapters natively, and the runtime's IDirect3D9 reports 1",
               adapters);
    uint8_t chosen[16] = {0};           /* the game's callback context starts zeroed */
    for (;;) {
        static const char *const k[2] = {"ddraw_device", "ddraw_enum"};
        if (record(r, k, 2, v) == 1) {
            if (v[1].u != p->dd_n)
                refuse(r, "count %u where %u records came before it", v[1].u, p->dd_n);
            if (v[0].u)
                refuse(r, "DirectDrawEnumerateExA answered 0x%08x natively, and the runtime's "
                          "answers DD_OK", v[0].u);
            break;
        }
        if (v[0].u != p->dd_n) refuse(r, "index %u where %u was next", v[0].u, p->dd_n);
        if (p->dd_n == SA2_DP_MAX_DDRAW)
            refuse(r, "more than %u `ddraw_device` records, the most the runtime holds",
                   SA2_DP_MAX_DDRAW);
        if (v[2].mon == 1 && v[2].u >= adapters)
            refuse(r, "monitor adapter%u, and Direct3D reported %u adapters", v[2].u, adapters);
        if (v[2].mon == 2)
            refuse(r, "ddraw device %u's monitor is no Direct3D adapter's natively, and the "
                      "runtime's monitors are its adapters'", p->dd_n);
        if (v[2].mon == 1 && v[2].u == 0) {
            if (v[1].null)
                refuse(r, "ddraw device %u is on adapter 0's monitor with no GUID, which the "
                          "game's callback 0x00865c20 would read through", p->dd_n);
            memcpy(chosen, v[1].guid, 16);
        }
        sa2_dp_ddraw *d = &p->dd[p->dd_n];
        d->has_guid = !v[1].null;
        memcpy(d->guid, v[1].guid, 16);
        snprintf(d->description, sizeof d->description, "%s", v[3].str);
        snprintf(d->name, sizeof d->name, "%s", v[4].str);
        p->dd_mon[p->dd_n++] = v[2].mon == 1 ? (int32_t)v[2].u : -1;
    }
    one(r, "ddraw_create", v);
    if (memcmp(v[0].guid, chosen, 16))
        refuse(r, "DirectDrawCreate's GUID is not the one the game's callback 0x00865c20 "
                  "keeps from the devices above");
    if (v[1].u)
        refuse(r, "DirectDrawCreate answered 0x%08x natively, and the runtime's answers DD_OK",
               v[1].u);
    if ((hr = one(r, "ddraw_query", v)))
        refuse(r, "QueryInterface(IID_IDirectDraw7) answered 0x%08x natively, and the runtime's "
                  "answers S_OK", hr);
    one(r, "ddraw_vidmem", v);
    p->vidmem_hr = v[0].u;
    p->vidmem_total = v[1].u;

    /* XAudio2's mastering voice, as the game's own globals hold it */
    one(r, "xaudio2_master", v);
    if (v[0].u > 1u) refuse(r, "field `voice`: %u is neither 0 nor 1", v[0].u);
    if (!v[0].u)
        refuse(r, "the game had no XAudio2 mastering voice natively (0x01af0edc was null), and "
                  "the runtime's engine makes one");
    p->xa_flags = v[1].u;
    p->xa_channels = v[2].u;
    p->xa_rate = v[3].u;

    /* The optional sections, each whole or absent, in this order
     * (sa2_devprofile.h, "The locale", "The environment", "The standard handles") */
    static const char *const TAIL[4] = {"locale_user", "environment", "std_handle", "end"};
    for (unsigned at = 0;;) {
        const unsigned w = at + record(r, TAIL + at, 4u - at, v);
        if (w == 3u) break;
        if (w == 0u) locale_section(r, p, v);
        else if (w == 1u) environment_section(r, p, v);
        else std_section(r, p, v);
        at = w + 1u;
    }
    if (r->pos != r->len) {
        r->line++;
        refuse(r, "text after `end`");
    }
}

/* ----------------------------------------------------------------- config */

void sa2_devprofile_config_from_env(void) {
    g_configured = 1;
    set_default(&g_p);
    g_default = 1;
    snprintf(g_source, sizeof g_source, "%s", "default");
    g_sha[0] = 0;
    const char *path = getenv(SA2_DP_ENV);
    if (!path) return;
    rd r;
    memset(&r, 0, sizeof r);
    r.src = path;
    if (!*path) {
        sa2_trap_note("%s is set and empty; leave it unset for the default profile", SA2_DP_ENV);
        sa2_trap_raise(SA2_TRAP_FAULT, 0);
    }
    /* One message for every I/O failure: fopen of a directory succeeds here and
     * its read fails, where Python's open fails first, and the two readers must
     * say the same words either way (the 2026-09-23 review). */
    FILE *f = fopen(path, "rb");
    if (!f) {
        sa2_trap_note("%s:0: the device profile cannot be read", path);
        sa2_trap_raise(SA2_TRAP_FAULT, 0);
    }
    uint8_t *buf = malloc(SA2_DP_MAX_BYTES + 1u);
    if (!buf) {
        fclose(f);
        sa2_trap_note("%s:0: no memory to read the device profile", path);
        sa2_trap_raise(SA2_TRAP_FAULT, 0);
    }
    const size_t n = fread(buf, 1, SA2_DP_MAX_BYTES + 1u, f);
    const int bad = ferror(f);
    fclose(f);
    if (bad || n > SA2_DP_MAX_BYTES) {
        free(buf);
        if (bad) sa2_trap_note("%s:0: the device profile cannot be read", path);
        else
            sa2_trap_note("%s:0: the device profile is larger than %u bytes", path,
                          (unsigned)SA2_DP_MAX_BYTES);
        sa2_trap_raise(SA2_TRAP_FAULT, 0);
    }
    r.buf = buf;
    r.len = n;
    static profile parsed;             /* 150 KiB: not on the stack */
    parse(&r, &parsed);
    g_p = parsed;
    sha_hex(buf, n, g_sha);
    free(buf);
    g_default = 0;
    snprintf(g_source, sizeof g_source, "%s", path);
}

/* ---------------------------------------------------------------- readers */

int sa2_devprofile_is_default(void) { return g_default; }
const char *sa2_devprofile_source(void) { return g_source; }
const char *sa2_devprofile_sha256(void) { return g_sha; }

/* The profile the getters answer from: the default until configuration reads
 * one, so a caller that reaches a getter before sa2_imports_config_from_env
 * gets the default's answers and never zeros (the review of 2026-09-23 built
 * such a caller: it read 0 processors and slot 0 connected). */
static const profile *P(void) {
    static int ready;
    if (!ready && g_default && !g_configured) {
        set_default(&g_p);
        ready = 1;
    }
    return &g_p;
}

uint32_t sa2_dp_processors(void) { return P()->processors; }
uint32_t sa2_dp_process_mask(void) { return P()->process_mask; }
uint32_t sa2_dp_system_mask(void) { return P()->system_mask; }

uint32_t sa2_dp_syskeyboard_hr(void) { return P()->syskeyboard; }
uint32_t sa2_dp_sysmouse_hr(void) { return P()->sysmouse; }
uint32_t sa2_dp_gamectrl_count(void) { return P()->gc_n; }
const sa2_dp_didevice *sa2_dp_gamectrl(uint32_t i) { return i < P()->gc_n ? &P()->gc[i] : NULL; }
uint32_t sa2_dp_gamectrl_create_hr(uint32_t i) { return i < P()->gc_n ? P()->gc_create[i] : 0u; }

/* A slot past the four the game scans (0x00864EEB) is not a pad slot at all;
 * XInput answers ERROR_BAD_ARGUMENTS for it, and so does this. */
uint32_t sa2_dp_xinput_caps_hr(uint32_t slot) {
    return slot < SA2_DP_XINPUT_SLOTS ? P()->xinput[slot] : 160u;
}

uint32_t sa2_dp_wmi_pnp_count(void) { return P()->pnp_n; }
const char *sa2_dp_wmi_pnp_deviceid(uint32_t i) { return i < P()->pnp_n ? P()->pnp[i] : NULL; }
uint32_t sa2_dp_wmi_coinit_hr(void) { return P()->coinit; }
uint32_t sa2_dp_wmi_blanket_hr(void) { return P()->blanket; }

uint32_t sa2_dp_ddraw_count(void) { return P()->dd_n; }
const sa2_dp_ddraw *sa2_dp_ddraw_device(uint32_t i) { return i < P()->dd_n ? &P()->dd[i] : NULL; }
int32_t sa2_dp_ddraw_monitor_adapter(uint32_t i) { return i < P()->dd_n ? P()->dd_mon[i] : -1; }
uint32_t sa2_dp_ddraw_vidmem_hr(void) { return P()->vidmem_hr; }
uint32_t sa2_dp_ddraw_vidmem_total(void) { return P()->vidmem_total; }

int      sa2_dp_locale_recorded(void) { return P()->loc; }
uint32_t sa2_dp_locale_lcid(void) { return P()->loc_lcid; }
uint32_t sa2_dp_locale_system(void) { return P()->loc_system; }
uint32_t sa2_dp_locale_acp(void) { return P()->loc_acp; }
uint32_t sa2_dp_locale_oemcp(void) { return P()->loc_oemcp; }
int sa2_dp_locale_info(uint32_t lctype, const uint16_t **w, uint32_t *n, uint32_t *error) {
    const profile *p = P();
    if (!p->loc) return -1;
    for (uint32_t i = 0; i < SA2_DP_LOCALE_TYPES; i++) {
        if (LOCALE_LCTYPES[i] != lctype) continue;
        *error = p->loc_error[i];
        *w = p->loc_w[i];
        *n = p->loc_n[i];
        return p->loc_error[i] ? 0 : 1;
    }
    return -1;
}

int             sa2_dp_env_recorded(void) { return P()->env; }
uint32_t        sa2_dp_env_strings(void) { return P()->env_strings; }
uint32_t        sa2_dp_env_crt(void) { return P()->env_crt; }
const uint16_t *sa2_dp_env_block(uint32_t *units) {
    *units = P()->env_crt_units;
    return P()->env_w;
}

int      sa2_dp_std_recorded(void) { return P()->std; }
uint32_t sa2_dp_std_handle(unsigned which) {
    return which < SA2_DP_STD_HANDLES ? P()->std_handle[which] : 0u;
}
uint32_t sa2_dp_std_type(unsigned which) {
    return which < SA2_DP_STD_HANDLES ? P()->std_type[which] : 0u;
}

uint32_t sa2_dp_xaudio2_master_flags(void) { return P()->xa_flags; }
uint32_t sa2_dp_xaudio2_master_channels(void) { return P()->xa_channels; }
uint32_t sa2_dp_xaudio2_master_rate(void) { return P()->xa_rate; }
