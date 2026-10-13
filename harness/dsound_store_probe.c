/* The DirectSound device's backing stores and object slots, reused (runtime/sa2_dsound.c,
 * "the backing stores" and obj_new), held by a unit probe after a story-mode session ran
 * the runtime arena out on 2026-10-04.
 *
 * runtime/sa2_dsound.c and runtime/guest_memory.c, the production files, so the arena is
 * the real one (SA2_GUEST_RT_ARENA_BASE..SA2_GUEST_RT_ARENA_END) with the real allocator,
 * linked with stand-ins for the rest of the runtime (the function table, the register
 * file, the virtual clock, the trap). Guest memory is mapped here as sa2_guest_init maps
 * it, without the executable, which nothing in these two files reads:
 *
 *   zig cc -target x86_64-linux-musl -O2 -I runtime harness/dsound_store_probe.c \
 *          runtime/sa2_dsound.c runtime/guest_memory.c -o probe
 *   ./probe stream N ALIVE BYTES   -> one JSON object on stdout
 *   ./probe audit SEED STEPS
 *   ./probe slots N
 *   ./probe fixed
 *   ./probe uaf lock|dup
 *   ./probe staleunlock
 *   ./probe kept
 *   ./probe pastzero
 *   ./probe revive
 *   ./probe dirty
 *
 * Every call goes through the object's vtable to the thunk the device registered, with
 * `this` and the arguments on a guest stack, as lifted code makes it. SA2_DSOUND_BREAK
 * passes through to the device; SA2_DSOUND_DEVICE=1 is set here.
 *
 * stream  CRI's stream buffers, as the boot replay's call log shows them (seqs 1155..1236
 *         of private/generated/s4/run/replay-boot.ticks.json): CreateSoundBuffer,
 *         QueryInterface for IDirectSoundBuffer8 and its Release, Stop,
 *         SetCurrentPosition 0, a DSBLOCK_ENTIREBUFFER Lock cleared whole and Unlocked,
 *         Play looping; and once more than ALIVE are alive, the oldest retired the way
 *         CRI retires one: Lock whole, cleared, Unlock, Stop, Release. Each buffer's
 *         first store word is stamped with its creation number after the clear, so a
 *         store two live buffers shared would show the later stamp in the earlier.
 *         Before the first stream, the primary buffer and CRI's one 65 536-byte buffer
 *         (sound_init), as the game makes them.
 * audit   A seeded mix over three lengths: creations, duplicates (before and after the
 *         original's first Lock), Locks written and Unlocked, Locks left open and
 *         Unlocked later, final Releases (with a Lock open too, of the shortest length
 *         only). After every step the device's own buffer records are audited: no two
 *         buffers without their final Release name overlapping stores unless both are
 *         one store family (a buffer, and its duplicates made after its first Lock) at
 *         one address; every live buffer's stamp is still its family's; and no store a
 *         buffer left with a Lock open is named by any later family.
 * slots   N buffers of 64 bytes one at a time: each made, Locked, Unlocked and released.
 *         Once every slot has been used, each new buffer's address must be the oldest
 *         released buffer's not yet handed on.
 * fixed   A literal sequence whose addresses the test names (test_dsound_store_reuse.py).
 * uaf     A Lock (or DuplicateSoundBuffer) on a buffer after its final Release.
 * staleunlock
 *         An Unlock through a buffer after its final Release, once the next buffer of
 *         its length has been handed its store and holds a Lock open on it.
 * kept    A buffer released with a Lock open, then one of the same length locked.
 * pastzero One buffer released, then released 600 times more past zero; then every
 *         slot used once, each buffer released, and two buffers more. The two must
 *         take the oldest released slots that no call reached after their release:
 *         a Release past zero that put its slot on the ring again would fill the
 *         ring with that one slot and push every later one off it.
 * revive  A buffer released, released past zero, revived by an AddRef, released to
 *         zero again and asked a QueryInterface: its answers, the store the next two
 *         buffers of its length get, and whether its slot is ever handed on in 1100
 *         creations after (it must not be: calls reached it after its release).
 * dirty   A buffer that writes its store and does not clear it before its final
 *         Release, the next buffer of that length, which clears its store before its
 *         own, and a third: the bytes each is handed and the count of dirty handouts.
 *
 * Documentation/scripts/tests/test_dsound_store_reuse.py builds it and holds each. */
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_dsound.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

/* ------------------------------------------------------------ the stand-ins */

#define DS_N (SA2_DS_MAX_TYPES * SA2_DS_MAX_SLOTS)
static sa2_lifted_fn g_ds_fn[DS_N];
int sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind) {
    (void)name; (void)kind;
    if (va < SA2_DS_THUNK_BASE || va >= SA2_DS_THUNK_END) return -1;
    g_ds_fn[(va - SA2_DS_THUNK_BASE) / SA2_DS_THUNK_STRIDE] = fn;
    return 0;
}

typedef struct { uint32_t gpr[9]; } probe_state;
uint32_t sa2_rt_get_gpr(const void *state, int idx) { return ((const probe_state *)state)->gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t value) { ((probe_state *)state)->gpr[idx] = value; }

static uint64_t g_virtual_us = 1000000ull;
uint64_t sa2_virtual_us(void) { return g_virtual_us; }

static jmp_buf g_jmp;
static char    g_note[1024];
static int     g_trapped;
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_note, sizeof g_note, fmt, ap);
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    (void)status; (void)exit_pc;
    g_trapped = 1;
    longjmp(g_jmp, 1);
}

/* Guest memory as sa2_guest_init maps it: the whole guest space, base-relative. */
static void guest_up(void) {
    void *mem = mmap(NULL, SA2_GUEST_END, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (mem == MAP_FAILED) { fprintf(stderr, "probe: no guest space\n"); exit(2); }
    sa2_guest.base = (uint8_t *)mem;
    sa2_guest.size = SA2_GUEST_END;
    sa2_guest.image_base = SA2_GUEST_IMAGE_BASE;
    sa2_guest.rt_next = SA2_GUEST_RT_ARENA_BASE;
    sa2_guest.ready = 1;
}

/* ------------------------------------------------------------ calls */

#define CALLER   0x00401000u
#define STACK    (SA2_GUEST_STACK_TOP - 0x100u)
#define SCRATCH  (SA2_GUEST_SCRATCH_BASE + 0x4000u)
#define DESC     (SCRATCH + 0x000u)
#define WFX      (SCRATCH + 0x040u)
#define IID      (SCRATCH + 0x080u)
#define OUTS     (SCRATCH + 0x100u)

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t pe32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }

static uint32_t call_on(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv) {
    const uint32_t thunk = pe32(pe32(self) + 4u * slot);
    if (thunk < SA2_DS_THUNK_BASE || thunk >= SA2_DS_THUNK_END ||
        !g_ds_fn[(thunk - SA2_DS_THUNK_BASE) / SA2_DS_THUNK_STRIDE]) {
        fprintf(stderr, "probe: slot %u of %#010x names %#010x, which no thunk answers\n",
                slot, self, thunk);
        exit(2);
    }
    probe_state st;
    memset(&st, 0, sizeof st);
    const uint32_t esp = STACK - 4u * (argc + 2u);
    st.gpr[SA2_ESP] = esp;
    pk32(esp, CALLER);
    pk32(esp + 4u, self);
    for (uint32_t i = 0; i < argc; i++) pk32(esp + 8u + 4u * i, argv[i]);
    g_ds_fn[(thunk - SA2_DS_THUNK_BASE) / SA2_DS_THUNK_STRIDE](&st, thunk, NULL);
    return st.gpr[SA2_EAX];
}
#define CALL(self, slot, ...) \
    call_on(self, slot, (uint32_t)(sizeof((uint32_t[]){__VA_ARGS__}) / sizeof(uint32_t)), \
            (const uint32_t[]){__VA_ARGS__})
#define CALL0(self, slot) call_on(self, slot, 0, NULL)

enum { S_QI = 0, S_ADDREF = 1, S_RELEASE = 2, S_CREATE = 3, S_DUP = 5, S_COOP = 6,
       S_LOCK = 11, S_PLAY = 12, S_SETPOS = 13, S_SETFORMAT = 14, S_STOP = 18, S_UNLOCK = 19 };

static uint32_t g_ds;

static void device_up(void) {
    setenv("SA2_DSOUND_DEVICE", "1", 1);
    sa2_dsound_config_from_env();
    g_ds = sa2_dsound_make_device(CALLER);
    CALL(g_ds, S_COOP, 0x7e000000u, 2u);
    /* CRI's format: 48 kHz, 16-bit stereo (GetFormat's answer in the boot log). */
    const uint32_t wfx[5] = {0x00020001u, 48000u, 192000u, 0x00100004u, 0u};
    for (uint32_t i = 0; i < 5u; i++) pk32(WFX + 4u * i, wfx[i]);
    const uint8_t iid[16] = {0x49,0xA4,0x25,0x68,0x24,0x75,0x82,0x4D,
                             0x92,0x0F,0x50,0xE3,0x6A,0xB3,0xAB,0x1E};   /* IDirectSoundBuffer8 */
    sa2_poke(IID, 16, iid);
}

static uint32_t create(uint32_t bytes) {
    const uint32_t desc[9] = {36u, 0x000180A8u, bytes, 0u, WFX, 0u, 0u, 0u, 0u};
    for (uint32_t i = 0; i < 9u; i++) pk32(DESC + 4u * i, desc[i]);
    pk32(OUTS, 0u);
    const uint32_t hr = CALL(g_ds, S_CREATE, DESC, OUTS, 0u);
    if (hr) { fprintf(stderr, "probe: CreateSoundBuffer answered %#x\n", hr); exit(2); }
    return pe32(OUTS);
}
static uint32_t dup(uint32_t src) {
    pk32(OUTS, 0u);
    const uint32_t hr = CALL(g_ds, S_DUP, src, OUTS);
    if (hr) { fprintf(stderr, "probe: DuplicateSoundBuffer answered %#x\n", hr); exit(2); }
    return pe32(OUTS);
}
/* A whole-buffer Lock: its pointer; *n its length. */
static uint32_t lock_all(uint32_t b, uint32_t *n) {
    const uint32_t hr = CALL(b, S_LOCK, 0u, 0u, OUTS + 4u, OUTS + 8u, 0u, 0u, 2u);
    if (hr) { fprintf(stderr, "probe: Lock answered %#x\n", hr); exit(2); }
    if (n) *n = pe32(OUTS + 8u);
    return pe32(OUTS + 4u);
}
static void unlock(uint32_t b, uint32_t p, uint32_t n) { CALL(b, S_UNLOCK, p, n, 0u, 0u); }
/* A whole-buffer Lock and its Unlock: the pointer it handed back. */
static uint32_t lk(uint32_t b) {
    uint32_t n = 0;
    const uint32_t p = lock_all(b, &n);
    unlock(b, p, n);
    return p;
}
static uint32_t release(uint32_t b) { return CALL0(b, S_RELEASE); }
static void qi_release(uint32_t b) {
    CALL(b, S_QI, IID, OUTS + 12u);
    release(b);
}

/* The device's record of buffer `addr`, the newest row naming it. */
static const sa2_ds_buffer *rec_of(uint32_t addr) {
    for (uint32_t i = sa2_dsound_buffer_count(); i-- > 0;) {
        const sa2_ds_buffer *b = sa2_dsound_buffer_at(i);
        if (b && b->addr == addr) return b;
    }
    return NULL;
}

static uint64_t fnv(uint64_t h, uint32_t v) {
    for (int i = 0; i < 4; i++) { h ^= (v >> (8 * i)) & 0xFFu; h *= 0x100000001b3ull; }
    return h;
}

/* ------------------------------------------------------------ the output */

static void put_common(void) {
    sa2_ds_stats st;
    sa2_dsound_stats_get(&st);
    printf("\"trapped\": %s, \"note\": \"", g_trapped ? "true" : "false");
    for (const char *p = g_note; *p; p++) {
        if (*p == '"' || *p == '\\') putchar('\\');
        putchar(*p);
    }
    printf("\", \"arena_used\": %u, \"arena_bytes\": %u, \"buffers\": %u, "
           "\"objects_built\": %u, \"guest_bytes_allocated\": %u",
           sa2_guest_rt_used(), SA2_GUEST_RT_ARENA_END - SA2_GUEST_RT_ARENA_BASE,
           st.buffers, st.objects_built, st.guest_bytes_allocated);
#ifdef SA2_DS_MAX_STORES
    printf(", \"stores_reused\": %u, \"stores_reused_dirty\": %u, \"objects_reused\": %u, "
           "\"stores_kept_locked\": %u, \"calls_on_released\": %u", st.stores_reused,
           st.stores_reused_dirty, st.objects_reused, st.stores_kept_locked,
           st.calls_on_released);
#endif
}

/* ------------------------------------------------------------ stream */

/* What CRI makes before its first stream, as the boot log shows it: the primary buffer
 * (0x0086130D: no length, given its format by SetFormat, never locked) and one 65 536-byte
 * buffer locked whole, cleared, Unlocked and played looping for the rest of the run
 * (0x007C8A6D). So the device's objects and the arena start where the game's do. */
static void sound_init(void) {
    const uint32_t pdesc[9] = {36u, 0x00000001u, 0u, 0u, 0u, 0u, 0u, 0u, 0u};   /* PRIMARYBUFFER */
    for (uint32_t i = 0; i < 9u; i++) pk32(DESC + 4u * i, pdesc[i]);
    CALL(g_ds, S_CREATE, DESC, OUTS, 0u);
    CALL(pe32(OUTS), S_SETFORMAT, WFX);
    const uint32_t b = create(65536u);
    uint32_t len = 0;
    const uint32_t p = lock_all(b, &len);
    sa2_guest_fill(p, 0, len);
    unlock(b, p, len);
    CALL(b, S_PLAY, 0u, 0u, 1u);
}

#define MAXB 4096u
static uint32_t g_live[MAXB], g_live_id[MAXB], g_nlive;

static void stamp(uint32_t p, uint32_t id) { pk32(p, id); }

static int mode_stream(uint32_t n, uint32_t alive, uint32_t bytes) {
    static uint32_t stores[1u << 16];
    static uint32_t nstores;
    /* Static, as everything a mode reads after the trap's longjmp is. */
    static uint32_t done, max_live, violations, first_bad_it, retired;
    static uint64_t trace = 0xcbf29ce484222325ull;
    static uint32_t first[8][2];
    static char bad[256];
    if (setjmp(g_jmp) == 0) {
        device_up();
        sound_init();
        for (uint32_t it = 0; it < n; it++) {
            const uint32_t b = create(bytes);
            qi_release(b);
            CALL0(b, S_STOP);
            CALL(b, S_SETPOS, 0u);
            uint32_t len = 0;
            const uint32_t p = lock_all(b, &len);
            sa2_guest_fill(p, 0, len);          /* CRI's memset at 0x007C9D1D */
            stamp(p, it + 1u);
            unlock(b, p, len);
            CALL(b, S_PLAY, 0u, 0u, 1u);
            g_live[g_nlive] = b;
            g_live_id[g_nlive] = it + 1u;
            g_nlive++;
            if (it < 8u) { first[it][0] = b; first[it][1] = p; }
            trace = fnv(fnv(trace, b), p);
            uint32_t k = 0;
            for (; k < nstores && stores[k] != p; k++) {}
            if (k == nstores && nstores < (1u << 16)) stores[nstores++] = p;
            if (g_nlive > max_live) max_live = g_nlive;
            /* the audit: every live buffer's stamp is still its own */
            for (uint32_t j = 0; j < g_nlive; j++) {
                const sa2_ds_buffer *r = rec_of(g_live[j]);
                const uint32_t got = r ? pe32(r->mem) : 0u;
                if (!r || r->released || got != g_live_id[j]) {
                    if (!violations) {
                        first_bad_it = it;
                        snprintf(bad, sizeof bad, "buffer %#010x (made %u) reads stamp %u in its "
                                 "store %#010x", g_live[j], g_live_id[j], got, r ? r->mem : 0u);
                    }
                    violations++;
                }
            }
            if (g_nlive > alive) {
                const uint32_t old = g_live[0];
                uint32_t olen = 0;
                const uint32_t op = lock_all(old, &olen);
                sa2_guest_fill(op, 0, olen);    /* CRI's memset at 0x007C7E3F */
                unlock(old, op, olen);
                CALL0(old, S_STOP);
                if (release(old)) { fprintf(stderr, "probe: the final Release left a count\n"); exit(2); }
                memmove(g_live, g_live + 1, (g_nlive - 1u) * sizeof g_live[0]);
                memmove(g_live_id, g_live_id + 1, (g_nlive - 1u) * sizeof g_live_id[0]);
                g_nlive--;
                retired++;
            }
            g_virtual_us += 16666u;
            done = it + 1u;
        }
    }
    printf("{\"mode\": \"stream\", \"iterations\": %u, \"retired\": %u, \"max_live\": %u, "
           "\"distinct_stores\": %u, \"violations\": %u, \"first_violation_at\": %u, "
           "\"first_violation\": \"%s\", \"trace_fnv\": \"%016llx\", \"first\": [",
           done, retired, max_live, nstores, violations, first_bad_it, bad,
           (unsigned long long)trace);
    for (uint32_t i = 0; i < 8u && i < done; i++)
        printf("%s[\"0x%08x\", \"0x%08x\"]", i ? ", " : "", first[i][0], first[i][1]);
    printf("], ");
    put_common();
    printf("}\n");
    return 0;
}

/* ------------------------------------------------------------ audit */

typedef struct {
    uint32_t addr, fam, bytes, open_p, open_n;
    int      has_store;
} pb;
static pb       g_pb[MAXB];
static uint32_t g_npb, g_fams;
static uint32_t g_kept[MAXB], g_kept_fam[MAXB], g_nkept;
static uint32_t g_fam_open[1u << 16];       /* Locks a family has open on its store */
static uint32_t g_rng;

static uint32_t rnd(uint32_t n) {
    g_rng ^= g_rng << 13; g_rng ^= g_rng >> 17; g_rng ^= g_rng << 5;
    return g_rng % n;
}

static uint32_t g_viol;
static char     g_viol_text[512];
static void violation(uint32_t step, const char *fmt, ...) {
    if (!g_viol) {
        char t[400];
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(t, sizeof t, fmt, ap);
        va_end(ap);
        snprintf(g_viol_text, sizeof g_viol_text, "step %u: %s", step, t);
    }
    g_viol++;
}

/* The audit over the device's own records and the probe's own view of the families. */
static void audit(uint32_t step) {
    for (uint32_t i = 0; i < g_npb; i++) {
        const sa2_ds_buffer *a = rec_of(g_pb[i].addr);
        if (!a || a->released) { violation(step, "live buffer %#010x has no live record", g_pb[i].addr); continue; }
        if (!a->mem) continue;
        for (uint32_t j = i + 1u; j < g_npb; j++) {
            const sa2_ds_buffer *b = rec_of(g_pb[j].addr);
            if (!b || !b->mem) continue;
            const int overlap = a->mem < b->mem + b->mem_len && b->mem < a->mem + a->mem_len;
            if (overlap && !(g_pb[i].fam == g_pb[j].fam && a->mem == b->mem))
                violation(step, "live buffers %#010x (family %u) and %#010x (family %u) hold "
                          "%#010x+%u and %#010x+%u", g_pb[i].addr, g_pb[i].fam, g_pb[j].addr,
                          g_pb[j].fam, a->mem, a->mem_len, b->mem, b->mem_len);
        }
        if (g_pb[i].has_store && pe32(a->mem) != g_pb[i].fam)
            violation(step, "buffer %#010x's store %#010x reads stamp %u, not its family's %u",
                      g_pb[i].addr, a->mem, pe32(a->mem), g_pb[i].fam);
        for (uint32_t k = 0; k < g_nkept; k++)
            if (a->mem == g_kept[k] && g_pb[i].fam != g_kept_fam[k])
                violation(step, "buffer %#010x (family %u) holds %#010x, which family %u left "
                          "with a Lock open", g_pb[i].addr, g_pb[i].fam, a->mem, g_kept_fam[k]);
    }
}

static int mode_audit(uint32_t seed, uint32_t steps) {
    static const uint32_t LENS[3] = {1920u, 4800u, 131072u};
    g_rng = seed ? seed : 1u;
    if (steps > 30000u) steps = 30000u;       /* families stay inside g_fam_open */
    static uint32_t done, made, dups, released, released_open, max_live;
    if (setjmp(g_jmp) == 0) {
        device_up();
        for (uint32_t s = 0; s < steps; s++) {
            const uint32_t op = rnd(100);
            if ((op < 30 || g_npb == 0) && g_npb < 48u) {                   /* create */
                const uint32_t bytes = LENS[rnd(3)];
                g_pb[g_npb] = (pb){create(bytes), ++g_fams, bytes, 0u, 0u, 0};
                g_npb++;
                made++;
            } else if (op < 40 && g_npb < 48u) {                           /* duplicate */
                const pb *src = &g_pb[rnd(g_npb)];
                const sa2_ds_buffer *r = rec_of(src->addr);
                const uint32_t d = dup(src->addr);
                /* one store family only when the original already has its store */
                g_pb[g_npb] = (pb){d, (r && r->mem) ? src->fam : ++g_fams, src->bytes, 0u, 0u,
                                   (r && r->mem) ? src->has_store : 0};
                g_npb++;
                dups++;
            } else if (op < 70) {                                          /* Lock, write, Unlock */
                pb *x = &g_pb[rnd(g_npb)];
                if (!x->open_p) {
                    uint32_t n = 0;
                    const uint32_t p = lock_all(x->addr, &n);
                    pk32(p, x->fam);
                    unlock(x->addr, p, n);
                    for (uint32_t k = 0; k < g_npb; k++)
                        if (g_pb[k].fam == x->fam) g_pb[k].has_store = 1;
                }
            } else if (op < 78) {                                          /* Lock left open */
                pb *x = &g_pb[rnd(g_npb)];
                if (!x->open_p && x->bytes == LENS[0]) {
                    uint32_t n = 0;
                    const uint32_t p = lock_all(x->addr, &n);
                    pk32(p, x->fam);
                    x->open_p = p;
                    x->open_n = n;
                    g_fam_open[x->fam]++;
                    for (uint32_t k = 0; k < g_npb; k++)
                        if (g_pb[k].fam == x->fam) g_pb[k].has_store = 1;
                }
            } else if (op < 84) {                                          /* Unlock it later */
                pb *x = &g_pb[rnd(g_npb)];
                if (x->open_p) {
                    unlock(x->addr, x->open_p, x->open_n);
                    x->open_p = 0;
                    g_fam_open[x->fam]--;
                }
            } else {                                                       /* final Release */
                const uint32_t i = rnd(g_npb);
                const pb x = g_pb[i];
                const sa2_ds_buffer *r = rec_of(x.addr);
                if (x.open_p) released_open++;
                int last = 1;
                for (uint32_t k = 0; k < g_npb; k++)
                    if (k != i && g_pb[k].fam == x.fam) last = 0;
                /* the family's last live member goes while the family has a Lock open on
                 * its store: that store must never go to another family */
                if (last && g_fam_open[x.fam] && r && r->mem && g_nkept < MAXB) {
                    g_kept[g_nkept] = r->mem;
                    g_kept_fam[g_nkept++] = x.fam;
                }
                if (release(x.addr)) { fprintf(stderr, "probe: the final Release left a count\n"); exit(2); }
                g_pb[i] = g_pb[--g_npb];
                released++;
            }
            if (g_npb > max_live) max_live = g_npb;
            g_virtual_us += 1000u;
            audit(s);
            done = s + 1u;
        }
    }
    printf("{\"mode\": \"audit\", \"steps\": %u, \"made\": %u, \"duplicates\": %u, "
           "\"released\": %u, \"released_with_lock_open\": %u, \"max_live\": %u, "
           "\"violations\": %u, \"first_violation\": \"%s\", ",
           done, made, dups, released, released_open, max_live, g_viol, g_viol_text);
    put_common();
    printf("}\n");
    return 0;
}

/* ------------------------------------------------------------ slots */

static int mode_slots(uint32_t n) {
    static uint32_t freed[1u << 16];
    static uint32_t nfreed, head, done, distinct, fifo_bad, fresh;
    static uint32_t seen[SA2_DS_MAX_OBJECTS * 2u];
    static char bad[200];
    if (setjmp(g_jmp) == 0) {
        device_up();
        for (uint32_t it = 0; it < n; it++) {
            const uint32_t b = create(64u);
            uint32_t k = 0;
            for (; k < distinct && seen[k] != b; k++) {}
            if (k == distinct) {
                if (distinct < SA2_DS_MAX_OBJECTS * 2u) seen[distinct++] = b;
                fresh++;
            } else {
                /* a slot handed on: it must be the oldest released one not handed on yet */
                if (head >= nfreed || freed[head] != b) {
                    if (!fifo_bad)
                        snprintf(bad, sizeof bad, "creation %u got %#010x, and the oldest released "
                                 "slot not handed on is %#010x", it, b, head < nfreed ? freed[head] : 0u);
                    fifo_bad++;
                }
                head++;
            }
            uint32_t len = 0;
            const uint32_t p = lock_all(b, &len);
            unlock(b, p, len);
            if (release(b)) { fprintf(stderr, "probe: the final Release left a count\n"); exit(2); }
            if (nfreed < (1u << 16)) freed[nfreed++] = b;
            done = it + 1u;
        }
    }
    printf("{\"mode\": \"slots\", \"made\": %u, \"distinct_addresses\": %u, \"fresh\": %u, "
           "\"fifo_violations\": %u, \"first_fifo_violation\": \"%s\", ",
           done, distinct, fresh, fifo_bad, bad);
    put_common();
    printf("}\n");
    return 0;
}

/* ------------------------------------------------------------ fixed */

static int mode_fixed(void) {
    static uint32_t v[24], nv;
    if (setjmp(g_jmp) == 0) {
        device_up();
        v[nv++] = g_ds;
        uint32_t n;
        const uint32_t a = create(131072u);  v[nv++] = a;
        v[nv++] = lock_all(a, &n);            unlock(a, v[nv - 1], n);
        const uint32_t b = create(65536u);   v[nv++] = b;
        v[nv++] = lock_all(b, &n);            unlock(b, v[nv - 1], n);
        release(a);                                           /* A's store goes free */
        const uint32_t c = create(131072u);  v[nv++] = c;
        v[nv++] = lock_all(c, &n);            unlock(c, v[nv - 1], n);   /* A's store */
        const uint32_t d = create(131072u);  v[nv++] = d;
        v[nv++] = lock_all(d, &n);            unlock(d, v[nv - 1], n);   /* a fresh one */
        const uint32_t e = dup(d);           v[nv++] = e;                /* shares D's */
        v[nv++] = rec_of(e)->mem;
        release(d);                                           /* E still holds D's store */
        const uint32_t f = create(131072u);  v[nv++] = f;
        v[nv++] = lock_all(f, &n);            unlock(f, v[nv - 1], n);   /* not D's: fresh */
        release(e);                                           /* now D's store goes free */
        const uint32_t g = create(131072u);  v[nv++] = g;
        v[nv++] = lock_all(g, &n);            unlock(g, v[nv - 1], n);   /* D's store */
        release(g);                           /* D's store free again, 131 072 bytes */
        const uint32_t h = create(65536u);   v[nv++] = h;
        v[nv++] = lock_all(h, &n);            unlock(h, v[nv - 1], n);   /* not D's: fresh */
    }
    printf("{\"mode\": \"fixed\", \"addresses\": [");
    for (uint32_t i = 0; i < nv; i++) printf("%s\"0x%08x\"", i ? ", " : "", v[i]);
    printf("], ");
    put_common();
    printf("}\n");
    return 0;
}

/* ------------------------------------------------------------ uaf, kept */

/* Static, not automatic: they are read after the trap's longjmp. */
static uint32_t g_ua, g_up, g_uanswer;
static int mode_uaf(const char *how) {
    uint32_t *const a = &g_ua, *const p = &g_up, *const answer = &g_uanswer;
    if (setjmp(g_jmp) == 0) {
        device_up();
        uint32_t n;
        *a = create(4800u);
        *p = lock_all(*a, &n);
        unlock(*a, *p, n);
        release(*a);
        if (!strcmp(how, "dup")) *answer = dup(*a);
        else *answer = lock_all(*a, &n);
    }
    printf("{\"mode\": \"uaf\", \"how\": \"%s\", \"buffer\": \"0x%08x\", \"store\": \"0x%08x\", "
           "\"answer\": \"0x%08x\", ", how, *a, *p, *answer);
    put_common();
    printf("}\n");
    return 0;
}

/* A is released, B of A's length is handed A's block and holds a Lock open on it, and
 * the guest calls Unlock through its pointer to A. A's STATE still names the block, so
 * the recorder, tools/s5/stream_dump.py and the mixer would read the call as closing
 * B's Lock (m_Unlock says so); the device stops on it by name instead. */
static int mode_staleunlock(void) {
    static uint32_t a, pa, b, pb_, answer;
    if (setjmp(g_jmp) == 0) {
        device_up();
        uint32_t n;
        a = create(4800u);
        pa = lock_all(a, &n);
        unlock(a, pa, n);
        release(a);
        b = create(4800u);
        pb_ = lock_all(b, &n);                /* B's Lock, open */
        answer = CALL(a, S_UNLOCK, pa, n, 0u, 0u);
    }
    printf("{\"mode\": \"staleunlock\", \"a\": [\"0x%08x\", \"0x%08x\"], "
           "\"b\": [\"0x%08x\", \"0x%08x\"], \"answer\": \"0x%08x\", ", a, pa, b, pb_, answer);
    put_common();
    printf("}\n");
    return 0;
}

static int mode_kept(void) {
    static uint32_t a, pa, b, pb_, c, pc;
    if (setjmp(g_jmp) == 0) {
        device_up();
        uint32_t n;
        a = create(4800u);
        pa = lock_all(a, &n);                 /* never Unlocked */
        release(a);
        b = create(4800u);
        pb_ = lock_all(b, &n);
        unlock(b, pb_, n);
        release(b);                           /* B's store goes free; A's stays kept */
        c = create(4800u);
        pc = lock_all(c, &n);
        unlock(c, pc, n);
    }
    printf("{\"mode\": \"kept\", \"a\": [\"0x%08x\", \"0x%08x\"], \"b\": [\"0x%08x\", \"0x%08x\"], "
           "\"c\": [\"0x%08x\", \"0x%08x\"], ", a, pa, b, pb_, c, pc);
    put_common();
    printf("}\n");
    return 0;
}

/* ------------------------------------------------------------ pastzero, revive, dirty */

static int mode_pastzero(void) {
    static uint32_t x0, x1, x2, y1, y2, r_final, r_past_max, past, made;
    if (setjmp(g_jmp) == 0) {
        device_up();
        x0 = create(64u);
        lk(x0);
        r_final = release(x0);
        for (uint32_t i = 0; i < 600u; i++) {
            const uint32_t r = release(x0);
            if (r > r_past_max) r_past_max = r;
            past++;
        }
        /* the device holds slot 0 and x0 slot 1: 510 more fill the pool */
        for (uint32_t i = 1; i < SA2_DS_MAX_OBJECTS - 1u; i++) {
            const uint32_t b = create(64u);
            lk(b);
            release(b);
            if (i == 1u) x1 = b;
            if (i == 2u) x2 = b;
            made++;
        }
        y1 = create(64u);
        lk(y1);
        release(y1);
        y2 = create(64u);
    }
    printf("{\"mode\": \"pastzero\", \"x0\": \"0x%08x\", \"final_release\": %u, "
           "\"past_zero\": %u, \"past_zero_answer_max\": %u, \"made\": %u, "
           "\"x1\": \"0x%08x\", \"x2\": \"0x%08x\", \"y1\": \"0x%08x\", \"y2\": \"0x%08x\", ",
           x0, r_final, past, r_past_max, made, x1, x2, y1, y2);
    put_common();
    printf("}\n");
    return 0;
}

static int mode_revive(void) {
    static uint32_t a, pa, r[4], b, pb_, c, pc, made, hits;
    if (setjmp(g_jmp) == 0) {
        device_up();
        a = create(131072u);
        pa = lk(a);
        r[0] = release(a);                    /* the final Release */
        r[1] = release(a);                    /* past zero */
        r[2] = CALL0(a, S_ADDREF);            /* revived */
        r[3] = release(a);                    /* back to zero: not a second final one */
        qi_release(a);
        b = create(131072u);
        pb_ = lk(b);                          /* A's store, given up once */
        c = create(131072u);
        pc = lk(c);                           /* a fresh one */
        release(b);
        release(c);
        /* twice round the pool: A's slot must never come back */
        for (uint32_t i = 0; i < 1100u; i++) {
            const uint32_t x = create(4800u);
            lk(x);
            release(x);
            if (x == a) hits++;
            made++;
        }
    }
    printf("{\"mode\": \"revive\", \"a\": [\"0x%08x\", \"0x%08x\"], \"answers\": [%u, %u, %u, %u], "
           "\"b\": [\"0x%08x\", \"0x%08x\"], \"c\": [\"0x%08x\", \"0x%08x\"], \"made\": %u, "
           "\"a_slot_handed_on\": %u, ", a, pa, r[0], r[1], r[2], r[3], b, pb_, c, pc, made, hits);
    put_common();
    printf("}\n");
    return 0;
}

static int mode_dirty(void) {
    static uint32_t a, pa, b, pb_, wb, c, pc, wc;
    if (setjmp(g_jmp) == 0) {
        device_up();
        uint32_t n;
        a = create(4800u);
        pa = lock_all(a, &n);
        pk32(pa + 100u, 0xDEADBEEFu);         /* written, never cleared */
        unlock(a, pa, n);
        release(a);
        b = create(4800u);
        pb_ = lock_all(b, &n);
        wb = pe32(pb_ + 100u);
        sa2_guest_fill(pb_, 0, n);            /* cleared whole, as CRI clears a store */
        unlock(b, pb_, n);
        release(b);
        c = create(4800u);
        pc = lock_all(c, &n);
        wc = pe32(pc + 100u);
        unlock(c, pc, n);
    }
    printf("{\"mode\": \"dirty\", \"a\": \"0x%08x\", \"b\": \"0x%08x\", \"b_word\": \"0x%08x\", "
           "\"c\": \"0x%08x\", \"c_word\": \"0x%08x\", ", pa, pb_, wb, pc, wc);
    put_common();
    printf("}\n");
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: see the head of harness/dsound_store_probe.c\n"); return 2; }
    guest_up();
    const char *m = argv[1];
    if (!strcmp(m, "stream") && argc == 5)
        return mode_stream((uint32_t)strtoul(argv[2], NULL, 0), (uint32_t)strtoul(argv[3], NULL, 0),
                           (uint32_t)strtoul(argv[4], NULL, 0));
    if (!strcmp(m, "audit") && argc == 4)
        return mode_audit((uint32_t)strtoul(argv[2], NULL, 0), (uint32_t)strtoul(argv[3], NULL, 0));
    if (!strcmp(m, "slots") && argc == 3) return mode_slots((uint32_t)strtoul(argv[2], NULL, 0));
    if (!strcmp(m, "fixed")) return mode_fixed();
    if (!strcmp(m, "uaf") && argc == 3) return mode_uaf(argv[2]);
    if (!strcmp(m, "staleunlock")) return mode_staleunlock();
    if (!strcmp(m, "kept")) return mode_kept();
    if (!strcmp(m, "pastzero")) return mode_pastzero();
    if (!strcmp(m, "revive")) return mode_revive();
    if (!strcmp(m, "dirty")) return mode_dirty();
    fprintf(stderr, "probe: unknown mode or arguments\n");
    return 2;
}
