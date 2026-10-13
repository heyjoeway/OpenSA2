/* Guest memory for runtime v0. See guest_memory.h for the layout and the
 * reason the mapping is base-relative rather than identity. */
#include "guest_memory.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(_WIN32)
#include "sa2_win32.h"     /* mmap and munmap over VirtualAlloc */
#else
#include <sys/mman.h>
#endif

sa2_guest_t sa2_guest;

/* ------------------------------------------------------- the poison tier */
int      sa2_guest_poison;
uint64_t sa2_guest_poison_reads;
uint32_t sa2_guest_poison_first;
uint8_t  sa2_guest_page_written[SA2_PAGE_COUNT / 8];

/* The byte-granular range (guest_memory.h). `lo == hi` is no range, and every test below of
 * it is then one compare that fails. */
sa2_guest_fine_t sa2_guest_fine;

/* [addr, addr+len) clipped to the range, as [*a, *b); 0 when they do not meet. */
static int fine_clip(uint32_t addr, uint32_t len, uint32_t *a, uint32_t *b) {
    const uint64_t end = (uint64_t)addr + len;
    if (sa2_guest_fine.lo == sa2_guest_fine.hi || end <= sa2_guest_fine.lo
        || addr >= sa2_guest_fine.hi) return 0;
    *a = addr > sa2_guest_fine.lo ? addr : sa2_guest_fine.lo;
    *b = end < sa2_guest_fine.hi ? (uint32_t)end : sa2_guest_fine.hi;
    return 1;
}

static int fine_written(uint32_t x) {
    const uint32_t i = x - sa2_guest_fine.lo;
    return (sa2_guest_fine.bits[i >> 3] >> (i & 7u)) & 1u;
}

/* The count a test that met an unwritten byte at `x` adds (guest_memory.h). */
static void fine_count(uint32_t x) {
    sa2_guest_fine.reads++;
    if (!sa2_guest_fine.first) sa2_guest_fine.first = x;
    for (unsigned i = 0; i < sa2_guest_fine.kept_count; i++)
        if (sa2_guest_fine.kept[i] == x) return;
    if (sa2_guest_fine.kept_count < SA2_GUEST_FINE_KEPT)
        sa2_guest_fine.kept[sa2_guest_fine.kept_count++] = x;
    else
        sa2_guest_fine.unkept++;
}

void sa2_guest_note_write(uint32_t addr, uint32_t len) {
    if (!len) return;
    uint32_t first = addr >> SA2_PAGE_SHIFT;
    uint32_t last = (uint32_t)(((uint64_t)addr + len - 1) >> SA2_PAGE_SHIFT);
    if (last >= SA2_PAGE_COUNT) last = SA2_PAGE_COUNT - 1;
    for (uint32_t p = first; p <= last; p++)
        sa2_guest_page_written[p >> 3] |= (uint8_t)(1u << (p & 7u));
    uint32_t a, b;
    if (fine_clip(addr, len, &a, &b))
        for (uint32_t x = a; x < b; x++) {
            const uint32_t i = x - sa2_guest_fine.lo;
            sa2_guest_fine.bits[i >> 3] |= (uint8_t)(1u << (i & 7u));
        }
}

/* Pages outside the byte-granular range only: a page it covers in part is tested for the
 * part outside it. */
static int pages_poison(uint32_t addr, uint32_t len) {
    uint32_t first = addr >> SA2_PAGE_SHIFT;
    uint32_t last = (uint32_t)(((uint64_t)addr + len - 1) >> SA2_PAGE_SHIFT);
    if (last >= SA2_PAGE_COUNT) last = SA2_PAGE_COUNT - 1;
    for (uint32_t p = first; p <= last; p++)
        if (!(sa2_guest_page_written[p >> 3] & (uint8_t)(1u << (p & 7u)))) return 1;
    return 0;
}

/* The header census (guest_memory.h): the bytes of [image base, + one page) a test met. */
uint8_t  sa2_guest_head_read[SA2_GUEST_HEAD_CENSUS / 8];
uint64_t sa2_guest_head_reads;

static void head_census(uint32_t addr, uint32_t len) {
    const uint64_t lo = SA2_GUEST_IMAGE_BASE, hi = lo + SA2_GUEST_HEAD_CENSUS;
    const uint64_t end = (uint64_t)addr + len;
    if (end <= lo || addr >= hi) return;
    sa2_guest_head_reads++;
    for (uint64_t x = addr > lo ? addr : lo; x < end && x < hi; x++) {
        const uint32_t i = (uint32_t)(x - lo);
        sa2_guest_head_read[i >> 3] |= (uint8_t)(1u << (i & 7u));
    }
}

int sa2_guest_is_poison(uint32_t addr, uint32_t len) {
    if (!len) return 0;
    if (addr < SA2_GUEST_IMAGE_BASE + SA2_GUEST_HEAD_CENSUS) head_census(addr, len);
    uint32_t a, b;
    if (!fine_clip(addr, len, &a, &b)) return pages_poison(addr, len);
    for (uint32_t x = a; x < b; x++)
        if (!fine_written(x)) { fine_count(x); return 1; }
    /* The parts of [addr, addr+len) on either side of the range, by page. */
    if (addr < a && pages_poison(addr, a - addr)) return 1;
    const uint64_t end = (uint64_t)addr + len;
    if (end > b && pages_poison(b, (uint32_t)(end - b))) return 1;
    return 0;
}

int sa2_guest_fine_begin(uint32_t lo, uint32_t hi) {
    if (!sa2_guest.base) return SA2_E_INIT;      /* mapped: a loader calls this before `ready` */
    if (sa2_guest_fine.lo != sa2_guest_fine.hi) return SA2_E_RANGE;
    if (lo >= hi || !sa2_gp(lo, hi - lo)) return SA2_E_RANGE;
    uint8_t *bits = (uint8_t *)calloc(((size_t)(hi - lo) + 7u) / 8u, 1);
    if (!bits) return SA2_E_NOMEM;
    memset(&sa2_guest_fine, 0, sizeof sa2_guest_fine);    /* one range per init */
    sa2_guest_fine.bits = bits;
    sa2_guest_fine.lo = lo;
    sa2_guest_fine.hi = hi;
    return SA2_OK;
}

/* The range ends with the guest space it describes; its counts stay readable until the next
 * init, as `sa2_guest_poison_reads` does, so a host can report them after shutdown. */
static void fine_end(void) {
    free(sa2_guest_fine.bits);
    sa2_guest_fine.bits = NULL;
    sa2_guest_fine.lo = sa2_guest_fine.hi = 0;
}

/* ------------------------------------------------ the inlined fast path */
uint8_t *sa2_guest_fast_base;

/* guest_memory.h: the base while guest memory is mapped and the poison tier is off. Called
 * by everything that changes either. */
static void fast_base_sync(void) {
    sa2_guest_fast_base = sa2_guest.ready && sa2_guest_poison == SA2_POISON_OFF
                        ? sa2_guest.base : NULL;
}

void sa2_guest_poison_from_env(void) {
    const char *v = getenv("SA2_GUEST_POISON");
    if (v && *v) sa2_guest_poison = atoi(v);
    fast_base_sync();
}

/* ------------------------------------------------ the shipped executable */
sa2_guest_loader_fn sa2_guest_shipped_loader;
static int g_shipped = -1;          /* guest_memory.h: 1, 0, or -1 for SA2_EXE_KIND */
static char g_refusal[SA2_GUEST_REFUSAL_MAX];
int sa2_guest_shipped_loaded;

void sa2_guest_refuse(const char *line) {
    snprintf(g_refusal, sizeof g_refusal, "%s", line ? line : "");
    fprintf(stderr, "%s\n", g_refusal);
}

const char *sa2_guest_refusal(void) { return g_refusal; }

void sa2_guest_select_shipped(int shipped) { g_shipped = shipped < 0 ? -1 : shipped != 0; }

int sa2_guest_shipped_selected(void) {
    if (g_shipped >= 0) return g_shipped;
    const char *v = getenv("SA2_EXE_KIND");
    if (!v || !*v || !strcmp(v, "unpacked")) return 0;
    return !strcmp(v, "shipped") ? 1 : -1;
}

/* ---------------------------------------------------------------- PE reader
 * Only the fields the mapping needs: e_lfanew, the COFF header's section
 * count and optional-header size, the optional header's ImageBase and
 * SizeOfImage, and one IMAGE_SECTION_HEADER per section. PE32 only, which is
 * what the lifter's input is. */
#define PE_DOS_MAGIC 0x5A4Du
#define PE_NT_MAGIC  0x00004550u
#define PE_OPT_PE32  0x010Bu

static uint16_t rd16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
static uint32_t rd32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }

typedef struct {
    uint32_t image_base, size_of_image, size_of_headers;
    uint32_t sec_off;
    unsigned sec_count;
} pe_info_t;

static int pe_parse(const uint8_t *f, size_t n, pe_info_t *out) {
    if (n < 0x40 || rd16(f) != PE_DOS_MAGIC) return SA2_E_IMAGE;
    uint32_t nt = rd32(f + 0x3C);
    if (nt + 0x78 > n || rd32(f + nt) != PE_NT_MAGIC) return SA2_E_IMAGE;
    const uint8_t *coff = f + nt + 4;
    out->sec_count = rd16(coff + 2);
    uint16_t opt_size = rd16(coff + 16);
    const uint8_t *opt = coff + 20;
    if (opt_size < 0x60 || rd16(opt) != PE_OPT_PE32) return SA2_E_IMAGE;
    out->image_base     = rd32(opt + 28);
    out->size_of_image  = rd32(opt + 56);
    out->size_of_headers = rd32(opt + 60);
    out->sec_off = (uint32_t)((opt + opt_size) - f);
    if (out->sec_count == 0 || out->sec_count > SA2_MAX_SECTIONS) return SA2_E_IMAGE;
    if (out->sec_off + 40u * out->sec_count > n) return SA2_E_IMAGE;
    return SA2_OK;
}

/* ---------------------------------------------------------------- init */
/* The image as a loader maps it, from the whole file in `file`: the headers page, then each
 * section's file bytes up to its virtual size. The unpacked executable's loader, and every
 * build's unless the shipped executable is asked for (guest_memory.h). */
static int map_pe(const uint8_t *file, size_t n_file, const char *pe_path) {
    const uint32_t fsz = (uint32_t)n_file;
    pe_info_t pe;
    int rc = pe_parse(file, n_file, &pe);
    if (rc != SA2_OK) {
        fprintf(stderr, "sa2_guest_init: %s is not a PE32 image the runtime can map\n", pe_path);
        return rc;
    }
    if (pe.image_base != SA2_GUEST_IMAGE_BASE) {
        fprintf(stderr, "sa2_guest_init: image base %#x, expected %#x\n", pe.image_base, SA2_GUEST_IMAGE_BASE);
        return SA2_E_IMAGE;
    }
    /* Against the *first arena above the image*, whichever that currently is.
     * It was the scratch base until the guest thread stacks took the 8 MiB the
     * heap vacated; a bound naming a region that is no longer the nearest one
     * would pass an image that ran straight through the stacks. */
    if ((uint64_t)pe.image_base + pe.size_of_image > SA2_GUEST_TSTACK_BASE) {
        fprintf(stderr, "sa2_guest_init: image %#x..%#x overlaps the guest thread stacks at %#x\n",
                pe.image_base, pe.image_base + pe.size_of_image, SA2_GUEST_TSTACK_BASE);
        return SA2_E_IMAGE;
    }
    sa2_guest.image_size = pe.size_of_image;

    /* The headers page, as a loader maps it. */
    uint32_t hdr = pe.size_of_headers;
    if (hdr > fsz) hdr = fsz;
    memcpy(sa2_guest.base + pe.image_base, file, hdr);
    sa2_guest_note_write(pe.image_base, hdr);

    for (unsigned i = 0; i < pe.sec_count; i++) {
        const uint8_t *s = file + pe.sec_off + 40u * i;
        sa2_section_t *d = &sa2_guest.sections[i];
        memcpy(d->name, s, 8);
        d->name[8] = 0;
        d->virtual_size = rd32(s + 8);
        d->va = pe.image_base + rd32(s + 12);
        d->raw_size = rd32(s + 16);
        uint32_t raw_ptr = rd32(s + 20);
        d->characteristics = rd32(s + 36);
        d->readable   = (d->characteristics & 0x40000000u) != 0;
        d->writable   = (d->characteristics & 0x80000000u) != 0;
        d->executable = (d->characteristics & 0x20000000u) != 0;
        uint32_t n = d->raw_size;
        if (raw_ptr >= fsz) n = 0;
        else if (raw_ptr + n > fsz) n = fsz - raw_ptr;
        if (n > d->virtual_size && d->virtual_size != 0) n = d->virtual_size;
        if (n && sa2_gp(d->va, n)) {
            memcpy(sa2_guest.base + d->va, file + raw_ptr, n);
            /* Only the file-backed bytes count as initialised. The tail of a section past
             * its raw size is the `.bss` the loader zeroes, and a zero read from there is
             * exactly the case the poison tier exists to tell apart from an omission. */
            sa2_guest_note_write(d->va, n);
        }
        d->raw_size = n;
    }
    sa2_guest.section_count = pe.sec_count;
    return SA2_OK;
}

int sa2_guest_init(const char *pe_path) {
    if (sa2_guest.ready) return SA2_OK;
    memset(&sa2_guest, 0, sizeof sa2_guest);
    memset(sa2_guest_page_written, 0, sizeof sa2_guest_page_written);
    sa2_guest_poison_reads = 0;
    sa2_guest_poison_first = 0;
    fine_end();
    memset(&sa2_guest_fine, 0, sizeof sa2_guest_fine);
    memset(sa2_guest_head_read, 0, sizeof sa2_guest_head_read);
    sa2_guest_head_reads = 0;
    g_refusal[0] = 0;
    sa2_guest_shipped_loaded = 0;
    /* Before anything is mapped, so a refusal costs nothing: the shipped executable needs
     * the build's overlay (guest_memory.h, "the shipped executable"). */
    const int shipped = sa2_guest_shipped_selected();
    char line[SA2_GUEST_REFUSAL_MAX];
    if (shipped < 0) {
        snprintf(line, sizeof line, "SA2_EXE_OVERLAY_REFUSED switch: SA2_EXE_KIND is \"%s\"; it "
                 "is shipped for the shipped executable, or unpacked or unset for the unpacked one",
                 getenv("SA2_EXE_KIND"));
        sa2_guest_refuse(line);
        return SA2_E_OVERLAY;
    }
    if (shipped && !sa2_guest_shipped_loader) {
        snprintf(line, sizeof line, "SA2_EXE_OVERLAY_REFUSED no-table: %s was named the "
                 "shipped executable (SA2_EXE_KIND=shipped or sa2_guest_select_shipped), and this "
                 "build links no overlay table (tools/s7/text_readset.py), so its encrypted .text "
                 "cannot be run", pe_path ? pe_path : "(null)");
        sa2_guest_refuse(line);
        return SA2_E_OVERLAY;
    }
    const sa2_guest_loader_fn load = shipped ? sa2_guest_shipped_loader : map_pe;

    void *mem = mmap(NULL, SA2_GUEST_END, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED) {
        fprintf(stderr, "sa2_guest_init: cannot reserve %u bytes of guest space\n", SA2_GUEST_END);
        return SA2_E_NOMEM;
    }
    sa2_guest.base = (uint8_t *)mem;
    sa2_guest.size = SA2_GUEST_END;
    sa2_guest.image_base = SA2_GUEST_IMAGE_BASE;
    sa2_guest.rt_next = SA2_GUEST_RT_ARENA_BASE;
    snprintf(sa2_guest.image_path, sizeof sa2_guest.image_path, "%s", pe_path ? pe_path : "");

    FILE *fh = fopen(pe_path, "rb");
    if (!fh) { perror(pe_path); munmap(mem, SA2_GUEST_END); return SA2_E_IMAGE; }
    fseek(fh, 0, SEEK_END);
    long fsz = ftell(fh);
    fseek(fh, 0, SEEK_SET);
    if (fsz <= 0) { fclose(fh); munmap(mem, SA2_GUEST_END); return SA2_E_IMAGE; }
    uint8_t *file = (uint8_t *)malloc((size_t)fsz);
    if (!file) { fclose(fh); munmap(mem, SA2_GUEST_END); return SA2_E_NOMEM; }
    if (fread(file, 1, (size_t)fsz, fh) != (size_t)fsz) {
        fprintf(stderr, "sa2_guest_init: short read of %s\n", pe_path);
        free(file); fclose(fh); munmap(mem, SA2_GUEST_END); return SA2_E_IMAGE;
    }
    fclose(fh);

    int rc = load(file, (size_t)fsz, pe_path);
    free(file);
    if (rc != SA2_OK) { fine_end(); munmap(mem, SA2_GUEST_END); return rc; }
    sa2_guest.ready = 1;
    /* Thread 0's TEB, before anything can run: `sa2_cpu_state_reset` points
     * `fs_base` here and the SEH prologue reads the chain head before writing
     * it, so an uninitialised TEB is a chain terminated by guest address 0. */
    if (sa2_guest_teb_init(0) != SA2_OK) {
        fprintf(stderr, "sa2_guest_init: cannot lay out thread 0's TEB at %#x\n",
                SA2_GUEST_TEB_BASE);
        sa2_guest_shutdown();
        sa2_guest_shipped_loaded = 0;
        return SA2_E_RANGE;
    }
    fast_base_sync();
    return SA2_OK;
}

uint32_t sa2_guest_teb(unsigned index) {
    if (index >= SA2_GUEST_TEB_SLOTS) return 0;
    return SA2_GUEST_TEB_BASE + index * SA2_GUEST_TEB_SIZE;
}

int sa2_guest_teb_init(unsigned index) {
    return sa2_guest_teb_init_stack(index, SA2_GUEST_STACK_BASE, SA2_GUEST_STACK_TOP);
}

int sa2_guest_tstack(unsigned slot, uint32_t *low, uint32_t *top) {
    if (slot >= SA2_GUEST_TSTACK_SLOTS) return SA2_E_RANGE;
    uint32_t base = SA2_GUEST_TSTACK_BASE + slot * SA2_GUEST_TSTACK_SIZE;
    if (low) *low = base + SA2_GUEST_TSTACK_GUARD;
    if (top) *top = base + SA2_GUEST_TSTACK_SIZE;
    return SA2_OK;
}

int sa2_guest_teb_init_stack(unsigned index, uint32_t stack_limit, uint32_t stack_base) {
    if (!sa2_guest.ready) return SA2_E_INIT;
    uint32_t teb = sa2_guest_teb(index);
    if (!teb || !sa2_gp(teb, SA2_GUEST_TEB_SIZE)) return SA2_E_RANGE;
    int rc = sa2_guest_fill(teb, 0, SA2_GUEST_TEB_SIZE);
    if (rc != SA2_OK) return rc;
    /* Terminated, not empty: the prologue pushes whatever is here as the next
     * record, and 0 would be a next record at guest address 0. */
    const uint32_t fields[][2] = {
        {SA2_TEB_EXCEPTION_LIST, SA2_SEH_END_OF_CHAIN},
        {SA2_TEB_STACK_BASE,     stack_base},
        {SA2_TEB_STACK_LIMIT,    stack_limit},
        {SA2_TEB_SELF,           teb},
    };
    for (unsigned i = 0; i < sizeof fields / sizeof fields[0]; i++) {
        rc = sa2_poke(teb + fields[i][0], 4, &fields[i][1]);
        if (rc != SA2_OK) return rc;
    }
    return SA2_OK;
}

void sa2_guest_shutdown(void) {
    if (!sa2_guest.ready) return;
    munmap(sa2_guest.base, sa2_guest.size);
    memset(&sa2_guest, 0, sizeof sa2_guest);
    fine_end();
    fast_base_sync();
}

/* ---------------------------------------------------------------- C ABI */
int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    if (!sa2_guest.ready) return SA2_E_INIT;
    uint8_t *p = sa2_gp(addr, len);
    if (!p) return SA2_E_RANGE;
    memcpy(out, p, len);
    return SA2_OK;
}

int sa2_poke(uint32_t addr, uint32_t len, const void *in) {
    if (!sa2_guest.ready) return SA2_E_INIT;
    uint8_t *p = sa2_gp(addr, len);
    if (!p) return SA2_E_RANGE;
    memcpy(p, in, len);
    sa2_guest_note_write(addr, len);
    return SA2_OK;
}

int sa2_guest_fill(uint32_t addr, int byte, uint32_t len) {
    if (!sa2_guest.ready) return SA2_E_INIT;
    uint8_t *p = sa2_gp(addr, len);
    if (!p) return SA2_E_RANGE;
    memset(p, byte, len);
    sa2_guest_note_write(addr, len);
    return SA2_OK;
}

int sa2_guest_valid(uint32_t addr, uint32_t len) {
    return sa2_guest.ready && sa2_gp(addr, len) != NULL;
}

/* The separation this allocator depends on, checked by the compiler rather
 * than asserted in a comment. The arena must be above the guest's own heap
 * (whose arena `runtime/sa2_imports.c` bases at SA2_GUEST_HEAP_BASE and whose
 * string arena runs to SA2_GUEST_HEAP_END), must end at or below the stack,
 * and must be non-empty. `runtime/sa2_d3d9.c` carries the companion assertion
 * for its own object table, which sits below it in the same region. */
_Static_assert(SA2_GUEST_RT_ARENA_END <= SA2_GUEST_HEAP_BASE
               || SA2_GUEST_RT_ARENA_BASE >= SA2_GUEST_HEAP_END,
               "the runtime arena must not overlap the guest's HeapAlloc arena");
_Static_assert(SA2_GUEST_RT_ARENA_END <= SA2_GUEST_STACK_BASE,
               "the runtime arena must not overlap the guest stack");
_Static_assert(SA2_GUEST_RT_ARENA_BASE < SA2_GUEST_RT_ARENA_END,
               "the runtime arena must have room in it");

/* The guest-thread stack arena is the third allocator in this address space, and
 * the lesson of S4 slice 7 is that two of them at one address is a defect nobody
 * sees for six slices. These make it a compile error instead. */
_Static_assert(SA2_GUEST_TSTACK_END <= SA2_GUEST_SCRATCH_BASE,
               "the guest thread stacks must not overlap the scratch region");
_Static_assert(SA2_GUEST_TSTACK_END <= SA2_GUEST_HEAP_BASE
               || SA2_GUEST_TSTACK_BASE >= SA2_GUEST_HEAP_END,
               "the guest thread stacks must not overlap the guest's HeapAlloc arena");
_Static_assert(SA2_GUEST_TSTACK_BASE >= SA2_GUEST_IMPORT_END,
               "the guest thread stacks must not overlap the import thunks");
_Static_assert(SA2_GUEST_TSTACK_SLOTS >= 1,
               "the guest thread stack arena must hold at least one stack");
_Static_assert(SA2_GUEST_TSTACK_GUARD < SA2_GUEST_TSTACK_SIZE,
               "a guard page that is the whole slot leaves no stack");

uint32_t sa2_guest_alloc(uint32_t len, uint32_t align) {
    if (!sa2_guest.ready || align == 0) return 0;
    uint32_t a = (sa2_guest.rt_next + (align - 1)) & ~(align - 1);
    if (a < SA2_GUEST_RT_ARENA_BASE) return 0;
    if (len > SA2_GUEST_RT_ARENA_END || a > SA2_GUEST_RT_ARENA_END - len) return 0;
    sa2_guest.rt_next = a + len;
    sa2_guest_note_write(a, len);
    return a;
}

void sa2_guest_rt_reset(void) { sa2_guest.rt_next = SA2_GUEST_RT_ARENA_BASE; }
uint32_t sa2_guest_rt_used(void) {
    return sa2_guest.rt_next > SA2_GUEST_RT_ARENA_BASE
           ? sa2_guest.rt_next - SA2_GUEST_RT_ARENA_BASE : 0u;
}

const sa2_section_t *sa2_guest_section_of(uint32_t addr) {
    for (unsigned i = 0; i < sa2_guest.section_count; i++) {
        const sa2_section_t *s = &sa2_guest.sections[i];
        uint32_t n = s->virtual_size ? s->virtual_size : s->raw_size;
        if (addr >= s->va && addr < s->va + n) return s;
    }
    return NULL;
}

const char *sa2_guest_region_of(uint32_t addr) {
    if (addr < SA2_GUEST_NULL_GUARD) return "null-guard";
    if (addr >= SA2_GUEST_IMPORT_BASE && addr < SA2_GUEST_IMPORT_END) return "import-thunk";
    if (addr >= SA2_GUEST_IMAGE_BASE && addr < SA2_GUEST_IMAGE_BASE + sa2_guest.image_size) {
        const sa2_section_t *s = sa2_guest_section_of(addr);
        return s ? s->name : "image";
    }
    if (addr >= SA2_GUEST_HEAP_BASE && addr < SA2_GUEST_HEAP_END) return "heap";
    if (addr >= SA2_GUEST_TSTACK_BASE && addr < SA2_GUEST_TSTACK_END) return "thread-stack";
    if (addr >= SA2_GUEST_SCRATCH_BASE && addr < SA2_GUEST_SCRATCH_END) return "scratch";
    if (addr >= SA2_GUEST_STACK_BASE && addr < SA2_GUEST_STACK_TOP) return "stack";
    if (addr >= SA2_GUEST_STACK_TOP && addr < SA2_GUEST_RESERVED_END) return "reserved";
    return "free";
}
