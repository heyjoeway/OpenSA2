/* Open-addressing address -> lifted function table. See function_table.h.
 *
 * The file is `function_table_impl.c` rather than `function_table.c` because
 * the boundary gate (Documentation/scripts/check_boundary.py, LIFTED_NAMES)
 * reserves the name `function_table.c` for the emitter's *generated* table,
 * which design 3.4 lists among the lifted outputs and which must never be
 * committed. This file is the hand-written container; the generated contents
 * live under private/generated only.
 */
#include "function_table.h"
#include "guest_memory.h"
#include "sa2_trap.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static sa2_ft_entry *g_slots;
static uint32_t g_capacity;      /* power of two */
static uint32_t g_mask;
static uint32_t g_shift;         /* 32 - log2(capacity); see home_slot */
static uint32_t g_count;
static uint32_t g_max_probe;
static uint32_t g_misses;

/* Insertion and lookup cost, kept apart because they are different questions.
 *
 * `g_max_probe` is an *insertion* high-water mark and it flatters the table: an
 * insertion stops at the first empty slot, while a lookup that misses has to
 * walk to one, and a lookup that hits has to walk to the entry. The whole-image
 * table reported max_probe 15 while its worst real lookup walked 22 occupied
 * slots, and nothing measured that because nothing counted it. */
static uint64_t g_add_probes;        /* extra probes summed over every insertion */
static uint64_t g_lookups;           /* sa2_ft_find calls against a live table */
static uint64_t g_lookup_probes;     /* occupied slots walked, summed over lookups */
static uint32_t g_max_lookup_probe;  /* the worst such walk */

/* The dispatch cache: `sa2_ft_dispatch`'s last answer for each of 4096 hashed addresses, so an
 * indirect call, a jump or a missing block's tail call does not walk the table (a lookup there
 * walks from its home slot in a table of 24,287 rows in a live session, the bind record's
 * table_rows_after, which an indirect call in a hot loop paid for every time: the thermal
 * unit, 2026-10-12). Every change to the table moves `g_gen`,
 * which every cached answer must equal, so a later registration (sa2_ft_add's replacement of
 * an entry) is seen at once. `sa2_ft_find` itself and its lookup counts are unchanged; a
 * dispatch the cache answers does not count as a lookup. */
typedef struct {
    uint32_t      va, gen;
    sa2_lifted_fn fn;
} ft_hot;
#define FT_HOT_BITS 12u
static ft_hot   g_hot[1u << FT_HOT_BITS];
static uint32_t g_gen = 1;           /* never 0, so a zeroed row never matches */

/* Fibonacci hashing: guest entry addresses are 4- to 16-byte aligned and
 * strongly clustered, so the low bits alone collide badly. */
static inline uint32_t hash_va(uint32_t va) { return va * 2654435761u; }

/* The home slot of `va`.
 *
 * This used to be `hash_va(va) & g_mask`, which threw the hash away. Bit k of a
 * product depends only on bits 0..k of the operands, so the low bits of
 * `va * 2654435761` carry no mixing at all: masking them is arithmetically close
 * to indexing on `va`'s low log2(capacity) bits directly. Measured over the
 * whole-image table's 12 683 rows it cost 2.689 mean extra probes against 2.682
 * for no hash whatsoever. Multiplicative hashing mixes into the *high* bits, so
 * the index is the top log2(capacity) of them -- which is why the capacity's
 * log has to be stored rather than only its mask. */
static inline uint32_t home_slot(uint32_t va) { return hash_va(va) >> g_shift; }

int sa2_ft_init(uint32_t expected_entries) {
    sa2_ft_shutdown();
    uint32_t cap = 16;
    while (cap < expected_entries * 2u + 2u) cap <<= 1;
    g_slots = (sa2_ft_entry *)calloc(cap, sizeof *g_slots);
    if (!g_slots) return -1;
    g_capacity = cap;
    g_mask = cap - 1;
    /* cap is a power of two in [16, 2^31], so the shift is in [1, 28] and
     * `hash_va(va) >> g_shift` is always a slot index. */
    g_shift = 32u - (uint32_t)__builtin_ctz(cap);
    g_count = g_max_probe = g_misses = 0;
    g_add_probes = g_lookups = g_lookup_probes = 0;
    g_max_lookup_probe = 0;
    g_gen++;
    return 0;
}

void sa2_ft_shutdown(void) {
    free(g_slots);
    g_slots = NULL;
    g_capacity = g_mask = g_shift = g_count = g_max_probe = g_misses = 0;
    g_add_probes = g_lookups = g_lookup_probes = 0;
    g_max_lookup_probe = 0;
    g_gen++;
}

int sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind) {
    if (!g_slots || !fn) return -1;
    g_gen++;
    if (g_count * 2u >= g_capacity) return -1;   /* keep the load factor under 1/2 */
    uint32_t i = home_slot(va), probe = 0;
    for (;;) {
        if (!g_slots[i].fn) {
            g_slots[i].va = va;
            g_slots[i].fn = fn;
            g_slots[i].name = name;
            g_slots[i].kind = kind;
            g_count++;
            if (probe > g_max_probe) g_max_probe = probe;
            g_add_probes += probe;
            return 0;
        }
        if (g_slots[i].va == va) {
            /* A later registration wins: the harness's natives deliberately
             * replace a lifted entry of the same address. */
            g_slots[i].fn = fn;
            g_slots[i].name = name;
            g_slots[i].kind = kind;
            return 1;
        }
        i = (i + 1) & g_mask;
        probe++;
    }
}

int sa2_ft_add_all(const sa2_ft_desc *descs, uint32_t count, uint8_t kind) {
    for (uint32_t i = 0; i < count; i++)
        if (sa2_ft_add(descs[i].va, descs[i].fn, descs[i].name, kind) < 0) return -1;
    return 0;
}

const sa2_ft_entry *sa2_ft_find(uint32_t va) {
    if (!g_slots) return NULL;
    uint32_t i = home_slot(va);
    g_lookups++;
    for (uint32_t probe = 0; probe <= g_capacity; probe++) {
        if (!g_slots[i].fn || g_slots[i].va == va) {
            /* `probe` is the number of *occupied* slots walked past, which is the
             * cost of this lookup whether it hit or missed. */
            g_lookup_probes += probe;
            if (probe > g_max_lookup_probe) g_max_lookup_probe = probe;
            return g_slots[i].fn ? &g_slots[i] : NULL;
        }
        i = (i + 1) & g_mask;
    }
    g_lookup_probes += g_capacity;
    g_max_lookup_probe = g_capacity;
    return NULL;
}

sa2_lifted_fn sa2_ft_lookup(uint32_t va) {
    const sa2_ft_entry *e = sa2_ft_find(va);
    return e ? e->fn : NULL;
}

static uint32_t g_ring[SA2_FT_RING];
static uint32_t g_ring_n;          /* total dispatches, not the ring's length */

uint32_t sa2_ft_recent_count(void) {
    return g_ring_n < SA2_FT_RING ? g_ring_n : SA2_FT_RING;
}
uint32_t sa2_ft_recent_at(uint32_t i) {
    if (i >= sa2_ft_recent_count()) return 0;
    return g_ring[(g_ring_n - 1u - i) % SA2_FT_RING];
}

void *sa2_ft_dispatch(uint32_t va, void *state, uint32_t pc, void *memory) {
    g_ring[g_ring_n % SA2_FT_RING] = va;
    g_ring_n++;
    ft_hot *h = &g_hot[(va * 2654435761u) >> (32u - FT_HOT_BITS)];
    if (h->va == va && h->gen == g_gen) return h->fn(state, pc, memory);
    const sa2_ft_entry *e = sa2_ft_find(va);
    if (e) {
        h->va = va;
        h->fn = e->fn;
        h->gen = g_gen;
        return e->fn(state, pc, memory);
    }
    g_misses++;
    sa2_trap_note("function table miss at %#010x (region %s, table %u/%u entries)",
                  va, sa2_guest_region_of(va), g_count, g_capacity);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, va);
    return memory;   /* not reached: sa2_trap_raise does not return */
}

/* The landing pad `tools/s2/corpus_run/internalize.py` folds every module's
 * missing-block trampolines into. remill passes the *call target* as `pc`, so
 * dispatching on it is exactly the indirect-call path the function table exists
 * for.
 *
 * It lives here because it had no home: it was defined only in
 * `harness/plan_driver.c`, so every other program that linked folded IR failed
 * with one undefined reference per folded call site -- 18 259 of them at whole
 * image scale -- and two probe programs grew private copies to work around it.
 *
 * It is **weak** rather than strong for those two copies' sake:
 * `tools/s3/whole_image/link_probe.c` and `tools/s3/arms/arm_probe.c` define the
 * same three lines strongly, and a strong definition here would turn their links
 * into duplicate-symbol errors. Weak is not free -- a program that defines a
 * *different* stub silently wins, which is a real hazard and the reason this
 * comment names the two that do -- but it is the only shape that gives the
 * symbol a home without breaking a caller this file is not allowed to edit. When
 * those copies are deleted, this should become a strong definition. */
__attribute__((weak))
void *__sa2_dispatch_stub(void *state, uint32_t pc, void *memory) {
    return sa2_ft_dispatch(pc, state, pc, memory);
}

uint32_t sa2_ft_count(void)     { return g_count; }
uint32_t sa2_ft_capacity(void)  { return g_capacity; }
uint32_t sa2_ft_max_probe(void) { return g_max_probe; }
uint32_t sa2_ft_misses(void)    { return g_misses; }

uint64_t sa2_ft_add_probes(void)       { return g_add_probes; }
uint64_t sa2_ft_lookups(void)          { return g_lookups; }
uint64_t sa2_ft_lookup_probes(void)    { return g_lookup_probes; }
uint32_t sa2_ft_max_lookup_probe(void) { return g_max_lookup_probe; }

/* The generated file overrides these; a program that links no generated table
 * still builds and runs with natives only. */
__attribute__((weak)) int sa2_ft_register_generated(void) { return 0; }
__attribute__((weak)) uint32_t sa2_ft_generated_count(void) { return 0; }
