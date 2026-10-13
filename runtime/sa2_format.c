/* USER32's wsprintf format language, over guest memory. See sa2_format.h. */
#include "sa2_format.h"

#include <string.h>

#include "guest_memory.h"
#include "sa2_codepage.h"

#define CAP (SA2_WSPRINTF_MAX - 1u)     /* characters; the last byte is the terminator's */

typedef struct {
    char     buf[SA2_WSPRINTF_MAX];
    uint32_t n;                         /* characters stored, at most CAP */
    uint32_t fault;                     /* the first guest address a read refused */
    int      refused;
} out_t;

typedef struct {
    int      left, alt, zero;
    uint32_t width, prec;
    char     size;                      /* 0, 'h', 'l', 'w', 'I' (pointer-sized) or '6' (I64) */
    char     type;                      /* the conversion, or 0 when the character is not one */
} spec_t;

static void put(out_t *o, char c) {
    if (o->n < CAP) o->buf[o->n++] = c;
}

static void pad(out_t *o, char c, uint32_t count) {
    for (; count && o->n < CAP; count--) o->buf[o->n++] = c;
}

static int rd(out_t *o, uint32_t va, uint32_t len, void *dst) {
    if (sa2_peek(va, len, dst) == SA2_OK) return 1;
    if (!o->refused) { o->refused = 1; o->fault = va; }
    return 0;
}

/* 32-bit and wrapping, as the oracle's are: %4294967297d is a width of 1.
 * Every use is clamped to the room left, so a wrapped value is only a value. */
static uint32_t digits_into(uint32_t v, uint8_t c) {
    return v * 10u + (uint32_t)(c - '0');
}

/* Parses a conversion from `*f`, the character after '%', and leaves `*f` on
 * the type character -- which, when it is not one, is text the caller reads
 * again. Short-circuits the I64/I32 look-ahead as far as the characters go, so
 * nothing past a terminator is read. */
static int parse(out_t *o, uint32_t *f, spec_t *sp) {
    uint32_t at = *f;
    uint8_t c;
    memset(sp, 0, sizeof *sp);
    if (!rd(o, at, 1, &c)) return 0;
    if (c == '-') { sp->left = 1; if (!rd(o, ++at, 1, &c)) return 0; }
    if (c == '#') { sp->alt = 1;  if (!rd(o, ++at, 1, &c)) return 0; }
    if (c == '0') { sp->zero = 1; if (!rd(o, ++at, 1, &c)) return 0; }
    while (c >= '0' && c <= '9') {
        sp->width = digits_into(sp->width, c);
        if (!rd(o, ++at, 1, &c)) return 0;
    }
    if (c == '.') {
        if (!rd(o, ++at, 1, &c)) return 0;
        while (c >= '0' && c <= '9') {
            sp->prec = digits_into(sp->prec, c);
            if (!rd(o, ++at, 1, &c)) return 0;
        }
    }
    if (c == 'l' || c == 'h' || c == 'w') {
        sp->size = (char)c;
        if (!rd(o, ++at, 1, &c)) return 0;
    } else if (c == 'I') {
        uint8_t c1, c2 = 0;
        if (!rd(o, at + 1u, 1, &c1)) return 0;
        if ((c1 == '6' || c1 == '3') && !rd(o, at + 2u, 1, &c2)) return 0;
        if (c1 == '6' && c2 == '4') { sp->size = '6'; at += 3u; }
        else if (c1 == '3' && c2 == '2') at += 3u;
        else { sp->size = 'I'; at += 1u; }
        if (!rd(o, at, 1, &c)) return 0;
    }
    switch (c) {
    case 'p':
        /* Pointer-sized whatever its prefix: the oracle reads one dword for
         * %I64p, and so the arguments after it stay where they were. */
        sp->width = 8u;
        sp->zero = 1;
        if (sp->size == '6') sp->size = 'I';
        /* fall through */
    case 'c': case 'C': case 'd': case 'i': case 's': case 'S': case 'u': case 'x': case 'X':
        sp->type = (char)c;
        break;
    default:
        sp->type = 0;
        break;
    }
    *f = at;
    return 1;
}

static int next_dword(out_t *o, uint32_t *args, uint32_t *v) {
    if (!rd(o, *args, 4, v)) return 0;
    *args += 4u;
    return 1;
}

static int convert(out_t *o, spec_t *sp, uint32_t *args) {
    const uint32_t room = CAP - o->n;
    if (sp->left) sp->zero = 0;
    if (sp->width > room) sp->width = room;

    if (sp->type == 'c' || sp->type == 'C') {
        uint32_t v;
        if (!next_dword(o, args, &v)) return 0;
        const int wide = sp->type == 'c' ? sp->size == 'l' : sp->size != 'h';
        uint8_t ch = (uint8_t)v;
        if (wide) sa2_cp1252_from_wide((uint16_t)v, &ch);
        const uint32_t fill = sp->width > 1u ? sp->width - 1u : 0u;
        if (!sp->left) pad(o, ' ', fill);
        put(o, (char)ch);
        if (sp->left) pad(o, ' ', fill);
        return 1;
    }

    if (sp->type == 's' || sp->type == 'S') {
        uint32_t p;
        if (!next_dword(o, args, &p)) return 0;
        const int wide = sp->type == 's' ? (sp->size == 'l' || sp->size == 'w')
                                         : !(sp->size == 'h' || sp->size == 'w');
        char text[SA2_WSPRINTF_MAX];
        uint32_t len = 0;
        if (!p) {
            static const char null_text[] = "(null)";
            while (null_text[len] && (!sp->prec || len < sp->prec)) { text[len] = null_text[len]; len++; }
        } else {
            /* Measured to its terminator or the precision, as USER32 does before
             * it clamps to the room, so an unterminated string faults here too. */
            for (uint32_t i = 0; !sp->prec || i < sp->prec; i++) {
                uint16_t w;
                if (wide) {
                    if (!rd(o, p + 2u * i, 2, &w)) return 0;
                } else {
                    uint8_t b;
                    if (!rd(o, p + i, 1, &b)) return 0;
                    w = b;
                }
                if (!w) break;
                if (len < room) {
                    uint8_t b = (uint8_t)w;
                    if (wide) sa2_cp1252_from_wide(w, &b);
                    text[len] = (char)b;
                }
                len++;
            }
        }
        if (len > room) len = room;
        const uint32_t fill = sp->width > len ? sp->width - len : 0u;
        if (!sp->left) pad(o, ' ', fill);
        for (uint32_t i = 0; i < len; i++) put(o, text[i]);
        if (sp->left) pad(o, ' ', fill);
        return 1;
    }

    /* d i u x X p */
    uint32_t lo, hi = 0;
    if (!next_dword(o, args, &lo)) return 0;
    if (sp->size == '6' && !next_dword(o, args, &hi)) return 0;
    const int is_signed = sp->type == 'd' || sp->type == 'i';
    const int hex = sp->type == 'x' || sp->type == 'X' || sp->type == 'p';
    const char *const digit = (sp->type == 'X' || sp->type == 'p') ? "0123456789ABCDEF"
                                                                   : "0123456789abcdef";
    uint64_t mag = sp->size == '6' ? ((uint64_t)hi << 32) | lo : (uint64_t)lo;
    int neg = 0;
    if (is_signed) {
        const int64_t sv = sp->size == '6' ? (int64_t)mag : (int64_t)(int32_t)lo;
        if (sv < 0) { neg = 1; mag = 0u - (uint64_t)sv; }
    }
    char num[24];                               /* a sign and 20 digits at most */
    uint32_t len = 0, k = 0;
    char rev[20];
    do { rev[k++] = digit[mag % (hex ? 16u : 10u)]; mag /= hex ? 16u : 10u; } while (mag);
    if (neg) num[len++] = '-';
    while (k) num[len++] = rev[--k];
    if (len > room) len = room;
    /* The oracle's arithmetic: the precision counts the sign, `0` raises it to
     * the width, and the width never counts the prefix. */
    uint32_t prec = sp->prec < len ? len : sp->prec;
    if (prec > room) prec = room;
    if (sp->zero && sp->width > prec) prec = sp->width;
    const uint32_t fill = sp->width > prec ? sp->width - prec : 0u;
    if (!sp->left) pad(o, ' ', fill);
    if (sp->alt && hex) { put(o, '0'); put(o, sp->type == 'x' ? 'x' : 'X'); }
    uint32_t from = 0;
    if (neg && len) { put(o, '-'); from = 1; }
    pad(o, '0', prec > len ? prec - len : 0u);
    for (uint32_t i = from; i < len; i++) put(o, num[i]);
    if (sp->left) pad(o, ' ', fill);
    return 1;
}

int sa2_wvsprintf(uint32_t out, uint32_t fmt, uint32_t args, uint32_t *ret, uint32_t *fault) {
    out_t o;
    o.n = 0;
    o.refused = 0;
    o.fault = 0;
    uint32_t f = fmt;
    while (o.n < CAP) {
        uint8_t c;
        if (!rd(&o, f, 1, &c)) break;
        if (!c) break;
        f++;
        if (c != '%') { put(&o, (char)c); continue; }
        if (!rd(&o, f, 1, &c)) break;
        if (c == '%') { put(&o, '%'); f++; continue; }
        spec_t sp;
        if (!parse(&o, &f, &sp)) break;
        if (!sp.type) {
            /* Not a conversion: what was parsed is dropped, the width is padded
             * out unless left-aligned, and the character that ended it is read
             * again as text -- which, when it is '%', begins a conversion. */
            const uint32_t room = CAP - o.n;
            const uint32_t width = sp.width > room ? room : sp.width;
            if (!sp.left && width > sp.prec) pad(&o, ' ', width - sp.prec);
            continue;
        }
        f++;
        if (!convert(&o, &sp, &args)) break;
    }
    if (o.refused) {
        *fault = o.fault;
        return SA2_E_RANGE;
    }
    o.buf[o.n] = 0;
    if (sa2_poke(out, o.n + 1u, o.buf) != SA2_OK) {
        /* The first byte that cannot be written is where USER32 would fault. */
        uint32_t i = 0;
        while (i <= o.n && sa2_poke(out + i, 1, &o.buf[i]) == SA2_OK) i++;
        *fault = out + i;
        return SA2_E_RANGE;
    }
    *ret = o.n >= CAP ? SA2_WSPRINTF_MAX : o.n;
    return SA2_OK;
}
