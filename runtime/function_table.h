/* Function table for runtime v0 (design 3.4 "Indirect calls": a hash table
 * from original address to lifted function pointer, built at startup from the
 * function map).
 *
 * Entries come from two sources. The generated table
 * (`private/generated/s2/wrap/function_table_*.c`, written by
 * `tools/s2/wrap/gen_function_table.py` from `private/generated/function-map.json`
 * and the set of lifted defines, never committed) registers every lifted
 * function linked into the program. The runtime and the harness register host
 * natives -- import stubs and the fixture's native callees -- through the same
 * table, so a caller cannot tell the two apart and dispatch is one lookup.
 *
 * `__remill_function_call` and `__remill_jump` dispatch through
 * `sa2_ft_dispatch`. A miss traps with the address, the nearest mapped
 * function and a diagnostic; it never calls a null pointer.
 */
#ifndef SA2_RUNTIME_FUNCTION_TABLE_H
#define SA2_RUNTIME_FUNCTION_TABLE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The signature every lifted remill function has:
 *   ptr @sub_<va>(ptr noalias state, i32 program_counter, ptr noalias memory) */
typedef void *(*sa2_lifted_fn)(void *state, uint32_t pc, void *memory);

enum {
    SA2_FN_LIFTED = 0,   /* a lifted define from the emitter/lifter */
    SA2_FN_NATIVE = 1,   /* a host implementation standing in for guest code */
};

typedef struct {
    uint32_t      va;
    sa2_lifted_fn fn;
    const char   *name;
    uint8_t       kind;
} sa2_ft_entry;

/* Descriptor array shape the generated file emits. */
typedef struct {
    uint32_t      va;
    sa2_lifted_fn fn;
    const char   *name;
} sa2_ft_desc;

int  sa2_ft_init(uint32_t expected_entries);
void sa2_ft_shutdown(void);
int  sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind);
int  sa2_ft_add_all(const sa2_ft_desc *descs, uint32_t count, uint8_t kind);

const sa2_ft_entry *sa2_ft_find(uint32_t va);
sa2_lifted_fn       sa2_ft_lookup(uint32_t va);        /* NULL on a miss */

/* Dispatch: look `va` up and call it. On a miss this traps through
 * `sa2_trap_raise` (status 1) with `va` and a diagnostic, and does not return. */
void *sa2_ft_dispatch(uint32_t va, void *state, uint32_t pc, void *memory);

/* The last guest functions dispatched, newest first.
 *
 * A bounds-check fault reports the *guest address the access was to* and not
 * where the guest was when it made it, so a null read at 0x00000008 names a
 * pointer and nothing else. That was enough for every earlier slice, because
 * every earlier fault was a few calls past a place a probe had just been. It
 * stopped being enough at 13 438 import calls deep inside the renderer's
 * initialisation, where the only thing a reader has to go on is the trap note.
 *
 * This is the cheapest thing that fixes it: one store per dispatch into a
 * fixed ring. It costs a write on a path that already does a hash lookup, it
 * needs no instrumentation of lifted code, and it turns "a null pointer
 * somewhere" into an ordered list of guest entry points ending at the one that
 * was executing. */
#define SA2_FT_RING 32u
uint32_t sa2_ft_recent_count(void);
uint32_t sa2_ft_recent_at(uint32_t i);      /* 0 is the most recent */

/* The landing pad `tools/s2/corpus_run/internalize.py` folds every module's
 * missing-block trampolines into: `sa2_ft_dispatch(pc, state, pc, memory)`.
 * Defined weakly in `function_table_impl.c` so that every program linking folded
 * IR resolves it, and so that the two probe programs carrying their own copies
 * (tools/s3/whole_image/link_probe.c, tools/s3/arms/arm_probe.c) still link. */
void *__sa2_dispatch_stub(void *state, uint32_t pc, void *memory);

uint32_t sa2_ft_count(void);
uint32_t sa2_ft_capacity(void);
uint32_t sa2_ft_misses(void);        /* dispatch misses seen so far */

/* What the index costs, insertion and lookup kept apart.
 *
 * `sa2_ft_max_probe` is an insertion high-water mark: an insertion stops at the
 * first empty slot, so it under-reports what a lookup pays. A lookup walks to
 * the entry or to an empty slot, whichever comes first, and `..._max_lookup_probe`
 * is the worst such walk actually performed. Mean cost is
 * `sa2_ft_add_probes() / sa2_ft_count()` and
 * `sa2_ft_lookup_probes() / sa2_ft_lookups()`. */
uint32_t sa2_ft_max_probe(void);          /* worst insertion, over registrations */
uint64_t sa2_ft_add_probes(void);         /* extra probes summed over insertions */
uint64_t sa2_ft_lookups(void);            /* sa2_ft_find calls against a live table */
uint64_t sa2_ft_lookup_probes(void);      /* occupied slots walked, summed over lookups */
uint32_t sa2_ft_max_lookup_probe(void);   /* worst lookup, hit or miss */

/* Provided by the generated file; both have weak defaults so a program that
 * links no generated table still builds. */
int      sa2_ft_register_generated(void);
uint32_t sa2_ft_generated_count(void);

#ifdef __cplusplus
}
#endif
#endif
