/* The data-module loader (runtime/sa2_module.c) on its own, over stubbed guest
 * memory, a stubbed function table and a guest file system rooted at a host
 * directory. Driven by Documentation/scripts/tests/test_module_loader.py with
 * synthetic PEs, one case per rule the loader keeps, so each rule is a check
 * that has been seen to fail rather than a sentence in a header.
 *
 *   module_probe <root> <guest-path> [--dirty-reload]
 *
 * Prints one JSON object. A refusal the loader makes loudly -- sa2_trap_raise
 * -- prints {"trap": "<note>"} and exits 3.
 */
#include <limits.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_files.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_module.h"
#include "../runtime/sa2_trap.h"

/* ----------------------------------------------------------- guest memory */

#define GBASE 0x06000000u
#define GSIZE 0x04000000u
static uint8_t *g_mem;

static int in_window(uint32_t a, uint32_t n) {
    if (!g_mem) g_mem = calloc(1, GSIZE);
    return g_mem && a >= GBASE && a - GBASE <= GSIZE && GSIZE - (a - GBASE) >= n;
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
int sa2_guest_fill(uint32_t a, int byte, uint32_t n) {
    if (!in_window(a, n)) return SA2_E_RANGE;
    memset(g_mem + (a - GBASE), byte, n);
    return SA2_OK;
}
/* Deliberately not 64 KiB aligned, so the loader's rounding is exercised. */
uint32_t sa2_heap_alloc_module(uint32_t bytes) {
    const uint32_t at = 0x0697F010u;
    if (!in_window(at, bytes)) return 0;
    memset(g_mem + (at - GBASE), 0, bytes);
    return at;
}

/* -------------------------------------------------------- the rest it uses */

static uint32_t g_ft_va[16];
static char     g_ft_name[16][128];
static int      g_ftn;
int sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind) {
    (void)fn; (void)kind;
    if (g_ftn < 16) { g_ft_va[g_ftn] = va; snprintf(g_ft_name[g_ftn], 128, "%s", name); g_ftn++; }
    return 0;
}
static char g_note[512];
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_note, sizeof g_note, fmt, ap);
    va_end(ap);
}
static void jstr(const char *s) {
    putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') { putchar('\\'); putchar(*s); }
        else if ((unsigned char)*s < 0x20) printf("\\u%04x", (unsigned char)*s);
        else putchar(*s);
    }
    putchar('"');
}
void sa2_trap_raise(uint32_t st, uint32_t pc) {
    (void)st; (void)pc;
    printf("{\"trap\": ");
    jstr(g_note);
    printf("}\n");
    exit(3);
}
static uint32_t g_gpr[16];
uint32_t sa2_rt_get_gpr(const void *s, int i) { (void)s; return g_gpr[i]; }
void     sa2_rt_set_gpr(void *s, int i, uint32_t v) { (void)s; g_gpr[i] = v; }

/* A guest file system over `root`: '\' folds to '/', a leading ".\" goes, and
 * a path whose real location is outside the root is an escape, which is the
 * property the loader relies on the real one for. */
static char g_root[PATH_MAX];
int sa2_vfs_map(const char *gp, char *host, size_t hc, char *rel, size_t rc, int *layer) {
    char r[1024];
    size_t k = 0;
    const char *p = gp;
    if (p[0] == '.' && (p[1] == '\\' || p[1] == '/')) p += 2;
    for (; *p && k + 1 < sizeof r; p++) r[k++] = (*p == '\\') ? '/' : *p;
    r[k] = 0;
    if (rel) snprintf(rel, rc, "%s", r);
    snprintf(host, hc, "%s/%s", g_root, r);
    char real[PATH_MAX];
    if (layer) *layer = 0;
    if (!realpath(host, real)) return SA2_VFS_NOT_FOUND;
    size_t n = strlen(g_root);
    if (strncmp(real, g_root, n) || (real[n] && real[n] != '/')) return SA2_VFS_ESCAPE;
    return SA2_VFS_OK;
}
const char *sa2_vfs_status_name(int s) { return s == SA2_VFS_ESCAPE ? "escape" : "not-found"; }

/* ------------------------------------------------------------------- main */

int main(int argc, char **argv) {
    if (argc < 3 || !realpath(argv[1], g_root)) {
        fprintf(stderr, "usage: module_probe <root> <guest-path> [--dirty-reload]\n");
        return 2;
    }
    char why[256] = "";
    uint32_t err = 0xffffffffu;
    const uint32_t b = sa2_module_load(argv[2], why, sizeof why, &err);
    printf("{\"base\": %u, \"win32\": %u, \"why\": ", b, err);
    jstr(why);
    if (b) {
        const sa2_module *m = sa2_module_by_base(b);
        uint32_t w0 = 0, w4 = 0, bss = 0;
        sa2_peek(b + 0x2000, 4, &w0);
        sa2_peek(b + 0x2004, 4, &w4);
        sa2_peek(b + 0x3000, 4, &bss);
        printf(", \"base_aligned\": %s, \"backing\": ", (b & 0xffffu) ? "false" : "true");
        jstr(m->backing);
        printf(", \"substituted\": %s, \"relocations\": %u, \"exports\": %u, "
               "\"tblA_points_at_tblB\": %s, \"tblB\": %u, \"word_at_0x3000\": %u, "
               "\"by_name_tblA\": %u, \"by_name_tblB\": %u, \"by_ordinal_1\": %u, "
               "\"by_name_missing\": %u, \"natives\": %d",
               m->substituted ? "true" : "false", m->relocations, m->exports,
               w0 == b + 0x2004u ? "true" : "false", w4, bss,
               sa2_module_export(b, "tblA", 0), sa2_module_export(b, "tblB", 0),
               sa2_module_export(b, NULL, 1), sa2_module_export(b, "nope", 0), g_ftn);
        if (argc > 3 && !strcmp(argv[3], "--dirty-reload")) {
            const uint32_t dirty = 0xdeadbeefu;
            sa2_poke(b + 0x2004, 4, &dirty);
            while (sa2_module_by_base(b)) sa2_module_free(b);
            const int gone = !sa2_module_by_base(b) && !sa2_module_export(b, "tblB", 0)
                             && !sa2_module_free(b);
            const uint32_t b2 = sa2_module_load(argv[2], why, sizeof why, &err);
            uint32_t again = 0;
            sa2_peek(b2 + 0x2004, 4, &again);
            printf(", \"freed_is_gone\": %s, \"reload_base\": %u, \"mappings\": %u, "
                   "\"reload_restored\": %s", gone ? "true" : "false", b2,
                   sa2_module_by_base(b2) ? sa2_module_by_base(b2)->mappings : 0u,
                   again == w4 ? "true" : "false");
        }
    }
    printf("}\n");
    return 0;
}
