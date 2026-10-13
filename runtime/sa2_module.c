/* Modules the guest loads that are data: mapped, relocated, never run.
 * See sa2_module.h for what this is and the three rules it keeps. */
#include "sa2_module.h"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_files.h"
#include "sa2_imports.h"
#include "sa2_trap.h"

#define IMAGE_FILE_RELOCS_STRIPPED 0x0001u
#define IMAGE_FILE_DLL             0x2000u
#define IMAGE_SCN_CNT_CODE         0x00000020u
#define IMAGE_SCN_MEM_EXECUTE      0x20000000u
#define DIR_EXPORT    0
#define DIR_IMPORT    1
#define DIR_BASERELOC 5
#define DIR_TLS       9

#define ERROR_NOT_ENOUGH_MEMORY 8u
#define ERROR_MOD_NOT_FOUND     126u
#define ERROR_BAD_EXE_FORMAT    193u

static sa2_module g_mod[SA2_MODULE_MAX];
static uint8_t   *g_file[SA2_MODULE_MAX];     /* the backing file, kept for lookups and remaps */
static size_t     g_file_n[SA2_MODULE_MAX];
static char       g_host[SA2_MODULE_MAX][SA2_VFS_PATH_MAX];   /* resolved host path: the identity */
static char       g_code_name[SA2_MODULE_MAX][SA2_MODULE_CODE_MAX][96];
static uint32_t   g_mod_count;
static char       g_refusal[320];

/* ------------------------------------------------------ a PE, read with care
 *
 * Every offset and length is checked in 64 bits, because a malformed header
 * can put an RVA near 2^32 and `rva + len` would wrap in 32. */

typedef struct {
    const uint8_t *d;
    size_t         n;
    uint32_t       opt, sect, chars;
    uint16_t       nsect;
    uint32_t       image_base, size_of_image, size_of_headers, entry_rva, sect_align;
    uint32_t       ndirs, dir_rva[16], dir_size[16];
} pe_view;

static int rd16(const pe_view *v, uint64_t off, uint32_t *out) {
    if (off + 2u > v->n) return 0;
    *out = (uint32_t)v->d[off] | (uint32_t)v->d[off + 1] << 8;
    return 1;
}

static int rd32(const pe_view *v, uint64_t off, uint32_t *out) {
    if (off + 4u > v->n) return 0;
    *out = (uint32_t)v->d[off] | (uint32_t)v->d[off + 1] << 8
         | (uint32_t)v->d[off + 2] << 16 | (uint32_t)v->d[off + 3] << 24;
    return 1;
}

/* 1 for a valid i386 PE32 DLL, 0 with a reason otherwise. */
static int pe_parse(pe_view *v, const uint8_t *d, size_t n, char *why, size_t cap) {
    memset(v, 0, sizeof *v);
    v->d = d;
    v->n = n;
    uint32_t lfanew, sig, machine, nsect, optsz, magic;
    if (n < 0x40 || d[0] != 'M' || d[1] != 'Z' || !rd32(v, 0x3c, &lfanew)
        || !rd32(v, lfanew, &sig) || sig != 0x00004550u) {
        snprintf(why, cap, "not a PE file");
        return 0;
    }
    if (!rd16(v, (uint64_t)lfanew + 4u, &machine) || machine != 0x14cu) {
        snprintf(why, cap, "not an i386 image");
        return 0;
    }
    if (!rd16(v, (uint64_t)lfanew + 6u, &nsect) || !rd16(v, (uint64_t)lfanew + 20u, &optsz)
        || !rd16(v, (uint64_t)lfanew + 22u, &v->chars) || !(v->chars & IMAGE_FILE_DLL)) {
        snprintf(why, cap, "not a DLL");
        return 0;
    }
    v->opt = lfanew + 24u;
    if (!rd16(v, v->opt, &magic) || magic != 0x10bu) {
        snprintf(why, cap, "not a PE32 optional header");
        return 0;
    }
    v->nsect = (uint16_t)nsect;
    v->sect = v->opt + optsz;
    if (!rd32(v, (uint64_t)v->opt + 16u, &v->entry_rva) || !rd32(v, (uint64_t)v->opt + 28u, &v->image_base)
        || !rd32(v, (uint64_t)v->opt + 32u, &v->sect_align)
        || !rd32(v, (uint64_t)v->opt + 56u, &v->size_of_image)
        || !rd32(v, (uint64_t)v->opt + 60u, &v->size_of_headers)
        || !rd32(v, (uint64_t)v->opt + 92u, &v->ndirs)) {
        snprintf(why, cap, "a truncated optional header");
        return 0;
    }
    if (v->ndirs > 16u) v->ndirs = 16u;
    for (uint32_t i = 0; i < v->ndirs; i++) {
        if (!rd32(v, (uint64_t)v->opt + 96u + 8u * i, &v->dir_rva[i])
            || !rd32(v, (uint64_t)v->opt + 100u + 8u * i, &v->dir_size[i])) {
            snprintf(why, cap, "a truncated data directory");
            return 0;
        }
    }
    if ((uint64_t)v->sect + 40u * (uint64_t)nsect > n) {
        snprintf(why, cap, "a truncated section table");
        return 0;
    }
    if (!v->sect_align || (v->sect_align & (v->sect_align - 1u))) {
        snprintf(why, cap, "a section alignment that is not a power of two");
        return 0;
    }
    return 1;
}

typedef struct { uint32_t va, vsize, raw_size, raw_off, chars; } pe_section;

static pe_section pe_sect(const pe_view *v, uint32_t i) {
    pe_section s;
    uint64_t o = v->sect + 40u * (uint64_t)i;
    rd32(v, o + 8u, &s.vsize);
    rd32(v, o + 12u, &s.va);
    rd32(v, o + 16u, &s.raw_size);
    rd32(v, o + 20u, &s.raw_off);
    rd32(v, o + 36u, &s.chars);
    return s;
}

/* The file offset of `len` bytes at `rva`, or 0 when they are not all backed
 * by the file (zero-fill, or outside every section). */
static int rva_off(const pe_view *v, uint32_t rva, uint32_t len, size_t *off) {
    const uint64_t end = (uint64_t)rva + len;
    if (end <= v->size_of_headers && end <= v->n) {
        *off = rva;
        return 1;
    }
    for (uint32_t i = 0; i < v->nsect; i++) {
        pe_section s = pe_sect(v, i);
        if (rva < s.va || end > (uint64_t)s.va + s.raw_size) continue;
        uint64_t o = (uint64_t)s.raw_off + (rva - s.va);
        if (o + len > v->n) return 0;
        *off = (size_t)o;
        return 1;
    }
    return 0;
}

static uint32_t sect_chars(const pe_view *v, uint32_t rva) {
    for (uint32_t i = 0; i < v->nsect; i++) {
        pe_section s = pe_sect(v, i);
        uint32_t span = s.vsize > s.raw_size ? s.vsize : s.raw_size;
        if (rva >= s.va && (uint64_t)rva < (uint64_t)s.va + span) return s.chars;
    }
    return 0;
}

static int cstr_rva(const pe_view *v, uint32_t rva, char *out, size_t cap) {
    size_t off;
    if (!cap || !rva_off(v, rva, 1, &off)) return 0;
    size_t i = 0;
    for (; i + 1 < cap && off + i < v->n && v->d[off + i]; i++) out[i] = (char)v->d[off + i];
    out[i] = 0;
    return off + i < v->n && v->d[off + i] == 0;
}

/* The export directory's own name: what the module calls itself. */
static int self_name(const pe_view *v, char *out, size_t cap) {
    size_t off;
    uint32_t name_rva;
    if (v->ndirs <= DIR_EXPORT || !v->dir_size[DIR_EXPORT]
        || !rva_off(v, v->dir_rva[DIR_EXPORT], 40u, &off) || !rd32(v, (uint64_t)off + 12u, &name_rva))
        return 0;
    return cstr_rva(v, name_rva, out, cap);
}

/* ------------------------------------------------------------ small helpers */

static int ieq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        char x = (*a >= 'A' && *a <= 'Z') ? (char)(*a + 32) : *a;
        char y = (*b >= 'A' && *b <= 'Z') ? (char)(*b + 32) : *b;
        if (x != y) return 0;
    }
    return *a == *b;
}

static const char *base_of(const char *p) {
    const char *b = p;
    for (const char *q = p; *q; q++) if (*q == '\\' || *q == '/') b = q + 1;
    return b;
}

static int ends_dll(const char *s) {
    size_t n = strlen(s);
    return n > 4 && ieq(s + n - 4, ".dll");
}

/* Two module names are one module when they match with path, case and a
 * ".dll" suffix ignored, which is how GetModuleHandle compares them. */
static int same_module_name(const char *a, const char *b) {
    char x[SA2_MODULE_PATH_MAX], y[SA2_MODULE_PATH_MAX];
    snprintf(x, sizeof x, "%s", base_of(a));
    snprintf(y, sizeof y, "%s", base_of(b));
    if (ends_dll(x)) x[strlen(x) - 4] = 0;
    if (ends_dll(y)) y[strlen(y) - 4] = 0;
    return ieq(x, y);
}

static uint8_t *slurp(const char *host, size_t *n) {
    FILE *f = fopen(host, "rb");
    if (!f) return NULL;
    uint8_t *d = NULL;
    if (fseek(f, 0, SEEK_END) == 0) {
        long len = ftell(f);
        if (len > 0 && len < 0x40000000L && fseek(f, 0, SEEK_SET) == 0
            && (d = malloc((size_t)len)) != NULL) {
            if (fread(d, 1, (size_t)len, f) != (size_t)len) { free(d); d = NULL; }
            else *n = (size_t)len;
        }
    }
    fclose(f);
    return d;
}

static uint32_t refuse(char *why, size_t cap, uint32_t *err, uint32_t code, const char *text) {
    snprintf(g_refusal, sizeof g_refusal, "%s", text);
    if (why && cap) snprintf(why, cap, "%s", g_refusal);
    if (err) *err = code;
    return 0;
}

/* A refusal that must not look like an absent file. */
static void refuse_loudly(const char *guest_path, const char *text) {
    snprintf(g_refusal, sizeof g_refusal, "%s", text);
    sa2_trap_note("LoadLibrary(\"%s\"): %s", guest_path, text);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
}

/* The `ret` a code export is. Nothing but the return address moves: EAX and
 * every other register are what the caller left, as they would be. */
static void *module_ret(void *s, uint32_t pc, void *m) {
    for (uint32_t i = 0; i < g_mod_count; i++)
        for (uint32_t k = 0; k < g_mod[i].code_count; k++)
            if (g_mod[i].code_va[k] == pc) g_mod[i].code_calls++;
    sa2_rt_set_gpr(s, SA2_ESP, sa2_rt_get_gpr(s, SA2_ESP) + 4u);
    return m;
}

/* ------------------------------------------------------------- the checks */

typedef struct {
    uint32_t nfunc, nname, ord_base, a_func, a_name, a_ord;
} export_dir;

static int exports_of(const pe_view *v, export_dir *e) {
    size_t off;
    memset(e, 0, sizeof *e);
    if (v->ndirs <= DIR_EXPORT || !v->dir_size[DIR_EXPORT]) return 1;    /* none: fine */
    return rva_off(v, v->dir_rva[DIR_EXPORT], 40u, &off)
        && rd32(v, (uint64_t)off + 16u, &e->ord_base) && rd32(v, (uint64_t)off + 20u, &e->nfunc)
        && rd32(v, (uint64_t)off + 24u, &e->nname) && rd32(v, (uint64_t)off + 28u, &e->a_func)
        && rd32(v, (uint64_t)off + 32u, &e->a_name) && rd32(v, (uint64_t)off + 36u, &e->a_ord)
        && e->nfunc < 0x10000u && e->nname < 0x10000u;
}

static int func_rva(const pe_view *v, const export_dir *e, uint32_t idx, uint32_t *rva) {
    size_t off;
    return idx < e->nfunc && rva_off(v, e->a_func + 4u * idx, 4u, &off) && rd32(v, off, rva);
}

/* Every export that lands in code must be one `ret`, and every export must lie
 * inside the image. Fills `code` with the distinct code RVAs. */
static int inert(const pe_view *v, const export_dir *e, uint32_t *code, uint32_t *ncode,
                 uint32_t *code_exports, char *why, size_t cap) {
    *ncode = *code_exports = 0;
    if (v->ndirs > DIR_TLS && v->dir_size[DIR_TLS]) {
        snprintf(why, cap, "it has a TLS directory, whose callbacks would run at load");
        return 0;
    }
    for (uint32_t i = 0; i < e->nfunc; i++) {
        uint32_t rva;
        if (!func_rva(v, e, i, &rva)) { snprintf(why, cap, "its export table is truncated"); return 0; }
        if (!rva) continue;
        if (rva >= v->size_of_image) {
            snprintf(why, cap, "export %u at rva %#x lies outside the image", i, rva);
            return 0;
        }
        if (rva >= v->dir_rva[DIR_EXPORT] && rva - v->dir_rva[DIR_EXPORT] < v->dir_size[DIR_EXPORT]) {
            snprintf(why, cap, "export %u is a forwarder, which would load another module", i);
            return 0;
        }
        uint32_t ch = sect_chars(v, rva);
        if (!(ch & (IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE))) continue;
        size_t off;
        if (!rva_off(v, rva, 1u, &off) || v->d[off] != 0xC3u) {
            snprintf(why, cap, "export %u at rva %#x is code that is not a bare ret, and this "
                     "loader executes nothing", i, rva);
            return 0;
        }
        (*code_exports)++;
        uint32_t k = 0;
        while (k < *ncode && code[k] != rva) k++;
        if (k == *ncode) {
            if (*ncode >= SA2_MODULE_CODE_MAX) { snprintf(why, cap, "too many code exports"); return 0; }
            code[(*ncode)++] = rva;
        }
    }
    return 1;
}

/* The base relocations, validated and, when `apply`, written into guest memory
 * at `base`. Every entry must be ABSOLUTE or HIGHLOW inside the image, and the
 * blocks must tile the directory exactly. An image that must move and has no
 * relocations is refused: mapped anyway, every internal pointer would still
 * point at the preferred base, which here is inside the guest heap arena, so
 * the guest would read heap bytes instead of faulting (2026-09-22 review).
 * Returns the HIGHLOW count or -1. */
static long relocate(const pe_view *v, uint32_t base, int apply, char *why, size_t cap) {
    const uint32_t delta = base - v->image_base;
    const int have = v->ndirs > DIR_BASERELOC && v->dir_size[DIR_BASERELOC];
    if (!have || (v->chars & IMAGE_FILE_RELOCS_STRIPPED)) {
        if (delta && apply) {
            snprintf(why, cap, "it must move from %#x to %#x and carries no relocations",
                     v->image_base, base);
            return -1;
        }
        return 0;
    }
    uint64_t at = v->dir_rva[DIR_BASERELOC];
    const uint64_t end = at + v->dir_size[DIR_BASERELOC];
    long count = 0;
    while (at < end) {
        size_t off;
        uint32_t page, bsize;
        if (end - at < 8u || !rva_off(v, (uint32_t)at, 8u, &off) || !rd32(v, off, &page)
            || !rd32(v, (uint64_t)off + 4u, &bsize) || bsize < 8u || (bsize & 1u)
            || at + bsize > end || !rva_off(v, (uint32_t)at, bsize, &off)) {
            snprintf(why, cap, "a base relocation block at rva %#llx is malformed",
                     (unsigned long long)at);
            return -1;
        }
        for (uint32_t k = 8; k + 2u <= bsize; k += 2u) {
            uint32_t ent;
            rd16(v, (uint64_t)off + k, &ent);
            const uint32_t type = ent >> 12;
            const uint64_t where = (uint64_t)page + (ent & 0xfffu);
            if (type == 0u) continue;                          /* IMAGE_REL_BASED_ABSOLUTE: padding */
            if (type != 3u || where + 4u > v->size_of_image) {
                snprintf(why, cap, "relocation type %u at rva %#llx is not HIGHLOW inside the "
                         "image", type, (unsigned long long)where);
                return -1;
            }
            count++;
            if (apply && delta) {
                uint32_t w;
                if (sa2_peek(base + (uint32_t)where, 4, &w) != SA2_OK) return -1;
                w += delta;
                if (sa2_poke(base + (uint32_t)where, 4, &w) != SA2_OK) return -1;
            }
        }
        at += bsize;
    }
    return count;
}

static uint32_t count_imports(const pe_view *v) {
    if (v->ndirs <= DIR_IMPORT || !v->dir_size[DIR_IMPORT]) return 0;
    uint32_t n = 0;
    for (uint64_t at = v->dir_rva[DIR_IMPORT]; at < 0xffffffffu; at += 20u) {
        size_t off;
        uint32_t name;
        if (!rva_off(v, (uint32_t)at, 20u, &off) || !rd32(v, (uint64_t)off + 12u, &name) || !name) break;
        n++;
    }
    return n;
}

/* Lay the file's bytes down at `base` over a zeroed image and relocate them.
 * Used for the first mapping and for a reload after the count reached zero. */
static long lay_down(const pe_view *v, const uint8_t *d, size_t n, uint32_t base,
                     char *why, size_t cap) {
    if (sa2_guest_fill(base, 0, v->size_of_image) != SA2_OK) {
        snprintf(why, cap, "the image at %#x cannot be cleared", base);
        return -1;
    }
    uint32_t hdr = v->size_of_headers < n ? v->size_of_headers : (uint32_t)n;
    if (hdr > v->size_of_image) hdr = v->size_of_image;
    if (sa2_poke(base, hdr, d) != SA2_OK) { snprintf(why, cap, "the headers cannot be written"); return -1; }
    for (uint32_t i = 0; i < v->nsect; i++) {
        pe_section s = pe_sect(v, i);
        if (s.va >= v->size_of_image) continue;
        /* Raw data past the section's aligned virtual size is file padding and
         * belongs to no section; copying it would spill into the next one's
         * zero fill. */
        uint64_t len = s.raw_size;
        if (s.vsize) {
            uint64_t span = ((uint64_t)s.vsize + v->sect_align - 1u) & ~(uint64_t)(v->sect_align - 1u);
            if (len > span) len = span;
        }
        if (len > (uint64_t)v->size_of_image - s.va) len = (uint64_t)v->size_of_image - s.va;
        if ((uint64_t)s.raw_off >= n) len = 0;
        else if (len > n - s.raw_off) len = n - s.raw_off;
        if (len && sa2_poke(base + s.va, (uint32_t)len, d + s.raw_off) != SA2_OK) {
            snprintf(why, cap, "section %u cannot be written", i);
            return -1;
        }
    }
    return relocate(v, base, 1, why, cap);
}

/* ------------------------------------------------------------ the loader */

/* The file the guest named, resolved through the guest file system. OK and
 * NOT_FOUND come back; anything else -- an escape above all -- traps, as every
 * file stub in this runtime does (sa2_files.h, Decision 2). */
static int resolve(const char *guest_path, char *host, size_t hcap, char *rel, size_t rcap) {
    int layer = 0;
    int rc = sa2_vfs_map(guest_path, host, hcap, rel, rcap, &layer);
    if (rc != SA2_VFS_OK && rc != SA2_VFS_NOT_FOUND) {
        char text[200];
        snprintf(text, sizeof text, "the guest file system refuses this path (%s); nothing "
                 "outside the installation is served", sa2_vfs_status_name(rc));
        refuse_loudly(guest_path, text);
    }
    return rc;
}

uint32_t sa2_module_load(const char *guest_path, char *why, size_t why_cap,
                         uint32_t *win32_error) {
    const char *asked = base_of(guest_path);
    char host[SA2_VFS_PATH_MAX], rel[SA2_VFS_PATH_MAX], reason[256] = "", text[320];
    if (resolve(guest_path, host, sizeof host, rel, sizeof rel) != SA2_VFS_OK) {
        snprintf(text, sizeof text, "%s: no such file under the installation", guest_path);
        return refuse(why, why_cap, win32_error, ERROR_MOD_NOT_FOUND, text);
    }

    size_t n = 0;
    uint8_t *d = slurp(host, &n);
    pe_view v;
    char self[64] = "";
    int substituted = 0;
    if (!d) {
        snprintf(text, sizeof text, "%s: cannot read %s", guest_path, host);
        return refuse(why, why_cap, win32_error, ERROR_MOD_NOT_FOUND, text);
    }
    if (!pe_parse(&v, d, n, reason, sizeof reason)) {
        /* Not a DLL at all is ERROR_BAD_EXE_FORMAT, as Windows says. It is not a
         * reason to go looking for another file. */
        free(d);
        snprintf(text, sizeof text, "%s: %s", guest_path, reason);
        return refuse(why, why_cap, win32_error, ERROR_BAD_EXE_FORMAT, text);
    }
    if (self_name(&v, self, sizeof self) && !same_module_name(self, asked)) {
        /* A valid DLL that calls itself something else. The one sibling that
         * calls itself by the asked name backs the handle instead, each
         * candidate resolved through the guest file system like any file. */
        free(d);
        d = NULL;
        char rel_dir[SA2_VFS_PATH_MAX], host_dir[SA2_VFS_PATH_MAX];
        char pick_host[SA2_VFS_PATH_MAX] = "", pick_rel[SA2_VFS_PATH_MAX] = "";
        snprintf(rel_dir, sizeof rel_dir, "%s", rel);
        snprintf(host_dir, sizeof host_dir, "%s", host);
        char *rs = strrchr(rel_dir, '/'), *hs = strrchr(host_dir, '/');
        if (rs) *rs = 0; else rel_dir[0] = 0;
        if (hs) *hs = 0;
        uint32_t candidates = 0;
        DIR *dh = hs ? opendir(host_dir) : NULL;
        struct dirent *ent;
        while (dh && (ent = readdir(dh)) != NULL) {
            if (!ends_dll(ent->d_name) || !strcmp(ent->d_name, base_of(host))) continue;
            char cguest[SA2_VFS_PATH_MAX], chost[SA2_VFS_PATH_MAX], crel[SA2_VFS_PATH_MAX];
            snprintf(cguest, sizeof cguest, "%s%s%s", rel_dir, rel_dir[0] ? "/" : "", ent->d_name);
            if (resolve(cguest, chost, sizeof chost, crel, sizeof crel) != SA2_VFS_OK) continue;
            size_t cn = 0;
            uint8_t *cd = slurp(chost, &cn);
            pe_view cv;
            char cself[64] = "", cwhy[128];
            if (cd && pe_parse(&cv, cd, cn, cwhy, sizeof cwhy)
                && self_name(&cv, cself, sizeof cself) && same_module_name(cself, asked)) {
                candidates++;
                snprintf(pick_host, sizeof pick_host, "%s", chost);
                snprintf(pick_rel, sizeof pick_rel, "%s", crel);
            }
            free(cd);
        }
        if (dh) closedir(dh);
        if (candidates != 1) {
            snprintf(text, sizeof text, "%s calls itself %s, and the directory holds %s other "
                     "file that calls itself %s (one is needed)", guest_path, self,
                     candidates ? "more than one" : "no", asked);
            return refuse(why, why_cap, win32_error, ERROR_MOD_NOT_FOUND, text);
        }
        d = slurp(pick_host, &n);
        if (!d || !pe_parse(&v, d, n, reason, sizeof reason) || !self_name(&v, self, sizeof self)) {
            free(d);
            snprintf(text, sizeof text, "%s: the substitute %s could not be read back",
                     guest_path, pick_rel);
            return refuse(why, why_cap, win32_error, ERROR_BAD_EXE_FORMAT, text);
        }
        snprintf(host, sizeof host, "%s", pick_host);
        snprintf(rel, sizeof rel, "%s", pick_rel);
        substituted = 1;
    }

    /* The same file is the same module, however the guest spelled its path. */
    for (uint32_t i = 0; i < g_mod_count; i++) {
        if (strcmp(g_host[i], host)) continue;
        free(d);
        sa2_module *m = &g_mod[i];
        if (!m->refs) {
            /* Freed to zero: Windows would unmap it and map the file afresh, so
             * whatever the guest wrote into the old image is gone. Same base. */
            pe_view fv;
            char r2[256] = "";
            if (!pe_parse(&fv, g_file[i], g_file_n[i], r2, sizeof r2)
                || lay_down(&fv, g_file[i], g_file_n[i], m->base, r2, sizeof r2) < 0) {
                snprintf(text, sizeof text, "%s: remapping a freed image failed: %s", guest_path, r2);
                refuse_loudly(guest_path, text);
            }
            m->mappings++;
        }
        m->refs++;
        if (win32_error) *win32_error = 0;
        return m->base;
    }
    if (g_mod_count >= SA2_MODULE_MAX) {
        free(d);
        snprintf(text, sizeof text, "%s: more modules than this runtime keeps", guest_path);
        return refuse(why, why_cap, win32_error, ERROR_NOT_ENOUGH_MEMORY, text);
    }

    export_dir e;
    uint32_t code[SA2_MODULE_CODE_MAX], ncode = 0, code_exports = 0;
    if (!exports_of(&v, &e)) snprintf(reason, sizeof reason, "its export directory is malformed");
    else if (!v.size_of_image || v.size_of_image > 0x08000000u)
        snprintf(reason, sizeof reason, "SizeOfImage %#x is outside what this loader maps", v.size_of_image);
    if (reason[0] || !inert(&v, &e, code, &ncode, &code_exports, reason, sizeof reason)
        || relocate(&v, v.image_base, 0, reason, sizeof reason) < 0) {
        free(d);
        snprintf(text, sizeof text, "%s: refused, %s", guest_path, reason);
        refuse_loudly(guest_path, text);
    }

    /* Map: a zeroed block, 64 KiB aligned as Windows places images. */
    uint32_t block = sa2_heap_alloc_module(v.size_of_image + 0x10000u);
    if (!block) {
        free(d);
        snprintf(text, sizeof text, "%s: the guest heap arena cannot hold a %u-byte image",
                 guest_path, v.size_of_image);
        return refuse(why, why_cap, win32_error, ERROR_NOT_ENOUGH_MEMORY, text);
    }
    const uint32_t base = (block + 0xffffu) & ~0xffffu;
    long fixups = lay_down(&v, d, n, base, reason, sizeof reason);
    if (fixups < 0) {
        free(d);
        snprintf(text, sizeof text, "%s: mapping at %#010x failed: %s", guest_path, base, reason);
        refuse_loudly(guest_path, text);
    }

    sa2_module *m = &g_mod[g_mod_count];
    memset(m, 0, sizeof *m);
    snprintf(m->asked, sizeof m->asked, "%s", guest_path);
    snprintf(m->backing, sizeof m->backing, "%s", rel);
    snprintf(m->self_name, sizeof m->self_name, "%s", self);
    snprintf(g_host[g_mod_count], sizeof g_host[0], "%s", host);
    m->substituted = substituted;
    m->base = base;
    m->size = v.size_of_image;
    m->image_base = v.image_base;
    m->file_bytes = (uint32_t)n;
    m->relocations = (uint32_t)fixups;
    m->exports = e.nname;
    m->code_exports = code_exports;
    m->imports_unresolved = count_imports(&v);
    m->entry_rva = v.entry_rva;
    m->refs = 1;
    m->mappings = 1;
    for (uint32_t k = 0; k < ncode; k++) {
        /* Name the native after every export that shares its address. */
        char *label = g_code_name[g_mod_count][k];
        snprintf(label, sizeof g_code_name[0][0], "%s!", self);
        for (uint32_t i = 0; i < e.nname; i++) {
            size_t off;
            uint32_t nrva, ord, rva;
            char nm[64];
            if (!rva_off(&v, e.a_name + 4u * i, 4u, &off) || !rd32(&v, off, &nrva)
                || !rva_off(&v, e.a_ord + 2u * i, 2u, &off) || !rd16(&v, off, &ord)
                || !func_rva(&v, &e, ord, &rva) || rva != code[k] || !cstr_rva(&v, nrva, nm, sizeof nm))
                continue;
            size_t used = strlen(label);
            snprintf(label + used, sizeof g_code_name[0][0] - used, "%s%s",
                     label[used - 1] == '!' ? "" : "/", nm);
        }
        if (sa2_ft_add(base + code[k], module_ret, label, SA2_FN_NATIVE) < 0) {
            snprintf(text, sizeof text, "the function table refused a row for %s at %#010x",
                     label, base + code[k]);
            refuse_loudly(guest_path, text);
        }
        m->code_va[m->code_count++] = base + code[k];
    }
    g_file[g_mod_count] = d;
    g_file_n[g_mod_count] = n;
    g_mod_count++;
    g_refusal[0] = 0;
    if (win32_error) *win32_error = 0;
    return base;
}

static int index_of(uint32_t base) {
    for (uint32_t i = 0; i < g_mod_count; i++)
        if (g_mod[i].base == base && g_mod[i].refs) return (int)i;
    return -1;
}

const sa2_module *sa2_module_by_base(uint32_t base) {
    int i = index_of(base);
    return i < 0 ? NULL : &g_mod[i];
}

uint32_t sa2_module_by_name(const char *name) {
    for (uint32_t i = 0; i < g_mod_count; i++)
        if (g_mod[i].refs && same_module_name(g_mod[i].asked, name)) return g_mod[i].base;
    return 0;
}

uint32_t sa2_module_export(uint32_t base, const char *name, uint32_t ordinal) {
    int i = index_of(base);
    if (i < 0) return 0;
    pe_view v;
    char reason[128];
    export_dir e;
    if (!pe_parse(&v, g_file[i], g_file_n[i], reason, sizeof reason) || !exports_of(&v, &e))
        return 0;
    uint32_t idx = 0xffffffffu, rva = 0;
    if (!name) {
        idx = ordinal - e.ord_base;          /* ordinal - Base, as Windows computes it */
    } else {
        for (uint32_t k = 0; k < e.nname && idx == 0xffffffffu; k++) {
            size_t off;
            uint32_t nrva, ord;
            char nm[256];
            if (!rva_off(&v, e.a_name + 4u * k, 4u, &off) || !rd32(&v, off, &nrva)
                || !cstr_rva(&v, nrva, nm, sizeof nm) || strcmp(nm, name))
                continue;
            if (rva_off(&v, e.a_ord + 2u * k, 2u, &off) && rd16(&v, off, &ord)) idx = ord;
        }
    }
    if (!func_rva(&v, &e, idx, &rva) || !rva || rva >= v.size_of_image) {
        g_mod[i].misses++;
        return 0;
    }
    g_mod[i].lookups++;
    return base + rva;
}

int sa2_module_free(uint32_t base) {
    int i = index_of(base);
    if (i < 0) return 0;
    g_mod[i].refs--;
    return 1;
}

uint32_t          sa2_module_count(void) { return g_mod_count; }
const sa2_module *sa2_module_at(uint32_t i) { return i < g_mod_count ? &g_mod[i] : NULL; }
const char       *sa2_module_last_refusal(void) { return g_refusal; }
