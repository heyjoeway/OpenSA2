/* A recording no-op DirectSound 8 device. See sa2_dsound.h. */
#include "sa2_dsound.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

/* ------------------------------------------------------------ the rows */

enum {
    DF_OUT  = 1u << 0,   /* writes through a pointer the guest dereferences */
    DF_CAP  = 1u << 1,   /* a capability answer this runtime fabricates */
    DF_CLK  = 1u << 2,   /* an answer derived from the virtual clock */
};

typedef struct ds_ctx ds_ctx;
typedef uint32_t (*ds_method)(ds_ctx *c);

typedef struct {
    uint8_t     slot;
    uint8_t     argc;        /* stack dwords, NOT counting `this` */
    uint8_t     flags;
    const char *name;
    ds_method   fn;
} ds_row;

typedef struct {
    const char   *name;
    const ds_row *rows;
    uint32_t      vt;        /* the vtable's guest address, 0 until built */
} ds_type;

enum { T_DS8 = 0, T_DSBUF = 1 };

/* ------------------------------------------------------- object records */

typedef struct {
    uint32_t addr;
    uint16_t type;
    uint16_t alive;
    uint32_t refs;
    uint32_t buf;            /* index into g_buf, for T_DSBUF; 0xFFFFFFFF otherwise */
    uint32_t stale;          /* a call reached it after its final Release (obj_new) */
} ds_obj;

struct ds_ctx {
    void           *s;
    uint32_t        self;
    ds_obj         *obj;
    sa2_ds_buffer  *b;       /* the buffer record, when `self` is a buffer */
    const uint32_t *a;
    uint32_t        argc;
    uint32_t        caller;
    uint8_t         answered;
    uint32_t        out0, out1;
};

/* --------------------------------------------------------------- state */

_Static_assert(SA2_DS_OBJ_BASE + SA2_DS_MAX_OBJECTS * SA2_DS_OBJ_STRIDE
               <= SA2_DS_VT_BASE,
               "the dsound object table must not run into its own vtables");
_Static_assert(SA2_DS_VT_BASE + SA2_DS_MAX_TYPES * SA2_DS_VT_STRIDE
               <= SA2_GUEST_RT_ARENA_BASE,
               "the dsound vtables must not run into the runtime arena");
_Static_assert(SA2_DS_OBJ_BASE >= 0x03243000u,
               "the dsound object table must sit above sa2_d3d9.h's vtables");
_Static_assert(SA2_DS_THUNK_BASE >= 0x0021C000u && SA2_DS_THUNK_END <= 0x00400000u,
               "the dsound thunks must sit above sa2_d3d9.h's and below the image");

/* Deliberate breaks, so every claim this file makes can be shown failing.
 * `SA2_DSOUND_BREAK=<name>`. tools/s4/dsound_receipt.py runs `argc` beside its
 * run, harness/dsound_probe.c names what `frozen` and `nolead` break, and
 * harness/dsound_store_probe.c (Documentation/scripts/tests/
 * test_dsound_store_reuse.py) runs `noreuse`, `sharefree` and `sharefree-noguard`.
 *
 *   argc    IDirectSound8::CreateSoundBuffer is declared with two arguments
 *           instead of three, which is the exact shape of the two defects this
 *           project has already paid for (d3d9 slice 6, Steam slice 9). The
 *           pop below uses the declared count, so the caller's frame drifts
 *           four bytes and everything it reads afterwards is one slot out.
 *   frozen  the cursors never advance: anchor_us is re-read as the anchor on
 *           every call, so elapsed is always zero. This is the device as it
 *           would be without the clock derivation, and it is how "a frozen
 *           cursor makes a polling guest spin" is shown rather than asserted.
 *   noout   every method with an out-parameter answers DS_OK and writes
 *           nothing -- the previous slice's survey shape.
 *   nolead  the write cursor equals the play cursor (no lead), so "the guest
 *           needs a lead" is a testable claim rather than a courtesy.
 *   noreuse no backing store and no object slot is ever handed out again, which
 *           is how the device allocated until 2026-10-04 and ran the runtime
 *           arena out on a story-mode session's 80th stream buffer (the stores
 *           comment below). Only the allocation is the old one: the stops on a
 *           Lock, an Unlock or a DuplicateSoundBuffer after the final Release,
 *           and the Release past zero that changes nothing, stay as they are
 *           now, so a guest that does any of these still stops here where the
 *           old device went on. The build at commit development_snapshot is the old device
 *           whole.
 *   sharefree
 *           a final Release frees its buffer's store even while a duplicate
 *           still holds it, so the next buffer of that length is handed a block
 *           a live buffer is using; store_guard stops the run by name there.
 *   sharefree-noguard
 *           the same with store_guard off, which is the device as it would be
 *           with neither the users count nor the guard: two live buffers are
 *           handed one block and nothing here says so, so
 *           harness/dsound_store_probe.c's own audit is shown failing.
 */
enum { BRK_NONE = 0, BRK_ARGC, BRK_FROZEN, BRK_NOOUT, BRK_NOLEAD, BRK_NOREUSE, BRK_SHAREFREE,
       BRK_SHAREFREE_NOGUARD };
static int g_break;

static sa2_ds_stats     g_st;
static sa2_ds_call      g_log[SA2_DS_LOG_MAX];
static uint32_t         g_log_used, g_seq;
static sa2_ds_slot_stat g_slot[SA2_DS_MAX_TYPES][SA2_DS_MAX_SLOTS];
static ds_obj           g_obj[SA2_DS_MAX_OBJECTS];
static uint32_t         g_obj_used;
static sa2_ds_buffer    g_buf[SA2_DS_MAX_OBJECTS];
static uint32_t         g_buf_used;
/* The object slots of buffers whose final Release has come, oldest first: a ring of
 * g_obj_free_n indices from g_obj_free_head. obj_new takes from it only when every
 * slot has been used once (the comment there says why). */
static uint16_t         g_obj_free[SA2_DS_MAX_OBJECTS];
static uint32_t         g_obj_free_head, g_obj_free_n;
/* Each buffer row's backing store (the stores comment below), as its index + 1; 0 for
 * none. A side table rather than a field of sa2_ds_buffer, which is the record the
 * recorder copies field by field. */
static uint16_t         g_buf_store[SA2_DS_MAX_OBJECTS];
/* Volume, pan and frequency per buffer row (m_SetVolume and its neighbours). */
static struct { int32_t volume, pan; uint32_t freq; uint8_t vset, pset, fset; }
    g_scalar[SA2_DS_MAX_OBJECTS];
static int              g_enabled, g_strict_caps, g_types_bound;
static uint32_t         g_device_obj;
static uint32_t         g_write_lead_us = 15000u;   /* the documented ~15 ms */
static uint32_t         g_primary_ms    = 500u;     /* a named placeholder */
static uint32_t         g_coop_hwnd, g_coop_level;
static uint32_t         g_speaker_config = 0x00140004u; /* DSSPEAKER_COMBINED(STEREO, WIDE) */

/* ------------------------------------------------------------ plumbing */

static void gfault(uint32_t a, const char *what) {
    sa2_trap_note("dsound: %s at guest %#010x is outside the guest space", what, a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}
static void pk32(uint32_t a, uint32_t v) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_poke(a, 4, &v) != SA2_OK) gfault(a, "a write of 4 bytes");
}
static void pk16(uint32_t a, uint16_t v) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_poke(a, 2, &v) != SA2_OK) gfault(a, "a write of 2 bytes");
}
static uint32_t pe32(uint32_t a) {
    uint32_t v = 0;
    if (sa2_peek(a, 4, &v) != SA2_OK) gfault(a, "a read of 4 bytes");
    return v;
}
static uint16_t pe16(uint32_t a) {
    uint16_t v = 0;
    if (sa2_peek(a, 2, &v) != SA2_OK) gfault(a, "a read of 2 bytes");
    return v;
}

static void refuse(const char *iface, const char *method, uint32_t caller, const char *why) {
    sa2_trap_note("%s::%s called from %#010x: %s", iface, method, caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
}

/* A capability answer: fabricated, flagged, and refusable as a set. */
static uint32_t cap_answer(ds_ctx *c, const char *iface, const char *method, uint32_t v) {
    if (g_strict_caps)
        refuse(iface, method, c->caller,
               "SA2_DSOUND_STRICT_CAPS=1 refuses every fabricated capability answer, and "
               "this is one of them: no host audio device is open, so there is no "
               "capability to report");
    c->answered = SA2_DS_ANS_INVENT;
    return v;
}

/* --------------------------------------------------------- the objects */

static uint32_t type_count(void);
static uint32_t type_vtable(uint32_t t);

static ds_obj *obj_of(uint32_t addr) {
    if (addr < SA2_DS_OBJ_BASE) return NULL;
    const uint32_t i = (addr - SA2_DS_OBJ_BASE) / SA2_DS_OBJ_STRIDE;
    if (i >= g_obj_used) return NULL;
    if (g_obj[i].addr != addr) return NULL;
    return &g_obj[i];
}

/* A fresh object of `t`. Every slot is used once, in order, before any is used again,
 * so a run that makes fewer than SA2_DS_MAX_OBJECTS objects gets exactly the addresses
 * it got before slots were reused. After that a new object takes the slot of the buffer
 * whose final Release is oldest, and the device's own object never goes on the ring.
 * `*row`, for a buffer, is the record row the reused slot's buffer had, which the new
 * buffer takes over; 0xFFFFFFFF for a fresh slot.
 *
 * What reuse costs, and why it is taken anyway. A guest pointer to a buffer whose final
 * Release has come names a dead object until its slot is handed on, and from then on
 * names the new buffer: a call through it is a call on that buffer, and nothing here can
 * tell the two apart. A stale Release through it, for one, would end the new buffer and
 * free the store it holds while its owner goes on using it, and store_guard would not
 * see it, since the device then believes that buffer released. sa2_d3d9.h refuses slot
 * reuse for exactly this reason. Three things narrow it here:
 *  - the oldest-released slot goes first, so a released slot waits behind every slot
 *    released before it: with n buffers alive at once, about SA2_DS_MAX_OBJECTS - n
 *    creations, which for CRI's few streams at a time is nearly the whole pool;
 *  - every call that reaches a dead buffer is counted (calls_on_released) and marks it
 *    `stale`, and a stale slot is never handed on: once the guest is seen holding a
 *    pointer to a dead buffer, that pointer keeps naming a dead object. This also
 *    covers a slot revived by an AddRef or a QueryInterface after its final Release;
 *  - a Release past zero changes nothing (m_Release), so a slot goes on the ring once.
 * What remains is a stale pointer the guest first uses only after its slot has been
 * handed on, which nothing here can see. CRI, the one maker of buffers past the first
 * two, retires a stream buffer through a helper that calls Stop and then Release once
 * (0x007C7120) and clears its own pointer right after (0x007C7E73), and the part of
 * the boot replay's call log its run record keeps (private/generated/s4/run/
 * replay-boot.ticks.json, dsound.calls) has no call on a buffer after its final
 * Release. A run that relies on reuse should hold calls_on_released at 0: a
 * nonzero count says the guest does keep stale pointers, and those it used too late
 * to be counted are the ones this cannot rule out.
 *
 * A stale slot leaves the ring for good; the pool's own stop below counts them. */
static ds_obj *obj_new(uint32_t t, uint32_t *row) {
    uint32_t i = 0xFFFFFFFFu, old_row = 0xFFFFFFFFu;
    if (g_obj_used < SA2_DS_MAX_OBJECTS) {
        i = g_obj_used++;
    } else {
        while (g_obj_free_n) {
            const uint32_t k = g_obj_free[g_obj_free_head];
            g_obj_free_head = (g_obj_free_head + 1u) % SA2_DS_MAX_OBJECTS;
            g_obj_free_n--;
            /* `refs` is 0 on a dead object unless an AddRef or a QueryInterface reached
             * it after its final Release, which also made it stale; both are checked. */
            if (!g_obj[k].alive && !g_obj[k].refs && !g_obj[k].stale &&
                g_obj[k].type == T_DSBUF) {
                i = k;
                old_row = g_obj[k].buf;
                g_st.objects_reused++;
                break;
            }
        }
    }
    if (i == 0xFFFFFFFFu) {
        uint32_t alive = 0, stale = 0;
        for (uint32_t k = 0; k < g_obj_used; k++) {
            alive += g_obj[k].alive ? 1u : 0u;
            stale += (!g_obj[k].alive && g_obj[k].stale) ? 1u : 0u;
        }
        sa2_trap_note("dsound: the fabricated-object pool of %u is exhausted with %u of its "
                      "objects alive, %u released ones withheld after a call reached them, "
                      "and no released buffer's slot to hand on",
                      SA2_DS_MAX_OBJECTS, alive, stale);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_OBJ_BASE);
    }
    if (row) *row = old_row;
    ds_obj *o = &g_obj[i];
    memset(o, 0, sizeof *o);
    o->addr = SA2_DS_OBJ_BASE + i * SA2_DS_OBJ_STRIDE;
    o->type = (uint16_t)t;
    o->alive = 1;
    o->refs = 1;
    o->buf = 0xFFFFFFFFu;
    g_st.objects_built++;
    /* A vtable pointer, then this runtime's own tag so a stray pointer found
     * later in guest memory is identifiable by its bytes alone. */
    pk32(o->addr, type_vtable(t));
    pk32(o->addr + 4u, 0x44534E44u);          /* 'DSND' */
    pk32(o->addr + 8u, t);
    pk32(o->addr + 12u, i);
    return o;
}

/* A buffer's record row and object, for CreateSoundBuffer and DuplicateSoundBuffer:
 * `nb` is the record the method built, copied into the row once the object exists, so
 * a refusal before this point leaves no slot and no row taken. */
static uint32_t store_index(uint32_t row);
static sa2_ds_buffer *buf_new(const sa2_ds_buffer *nb, ds_obj **out) {
    uint32_t r;
    ds_obj *o = obj_new(T_DSBUF, &r);
    if (r == 0xFFFFFFFFu) {
        /* A fresh slot: the device's own object holds one, so the fresh rows stop one
         * short of SA2_DS_MAX_OBJECTS and this is unreachable while it does. */
        if (g_buf_used >= SA2_DS_MAX_OBJECTS) {
            sa2_trap_note("dsound: object %#010x has a fresh slot and the %u buffer rows are "
                          "all taken", o->addr, SA2_DS_MAX_OBJECTS);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_OBJ_BASE);
        }
        r = g_buf_used++;
    }
    sa2_ds_buffer *b = &g_buf[r];
    *b = *nb;
    b->addr = o->addr;
    o->buf = r;
    memset(&g_scalar[r], 0, sizeof g_scalar[r]);
    g_buf_store[r] = 0;
    g_st.buffers++;
    *out = o;
    return b;
}

/* ------------------------------------------------------- the backing stores
 *
 * The guest bytes a Lock hands back. From the runtime arena, never from the guest's
 * own heap: S4 slice 7 found two allocators sharing one address and six slices running
 * on a heap that had been overwritten.
 *
 * The arena's allocator (sa2_guest_alloc) never takes a block back. Until 2026-10-04
 * each buffer took a fresh block at its first Lock and held it past its final Release,
 * so the arena held every store the guest had ever locked. CRI makes a 131 072-byte
 * buffer for each stream and releases it when the stream ends, and a story-mode session
 * stopped itself after 29 minutes on its 80th of them: 79 of those and one of 65 536
 * bytes had taken 10 420 224 of the arena's 10 485 760, with 4 of the 80 still alive
 * (private/generated/s5/input/metalharbor/20261004T195443-3633836-71a2e769/run.json).
 *
 * So this device keeps the blocks it is given and hands them on:
 *  - `users` counts the buffers that name the block and have not had their final
 *    Release: the buffer whose first Lock took it and each duplicate made from that
 *    buffer after it. Only at zero does the block go free.
 *  - `open` counts Locks on it with no Unlock yet. A block whose last holder goes with
 *    a Lock open is *kept*: the guest still has the pointer that Lock gave it, so the
 *    block is never handed to anything again (stores_kept_locked).
 *  - A free block goes to the next buffer that asks for exactly its length, the one
 *    freed longest ago first. Exactly its length, because a store is its buffer's
 *    length (host/player/mixer.cpp sizes its copy of a store from the STATE and holds
 *    it to that); longest ago, so a stale pointer into a freed block finds it free for
 *    as long as possible, where the recorder's store watch still sees a write through
 *    it. A buffer whose length no free block has gets a fresh block, so the arena holds,
 *    for each length, no more blocks than the most stores of that length ever held at
 *    once, and the kept ones besides.
 *  - A fresh block is zero; a reused one keeps the bytes its last holder left. The
 *    recorder (host/sa2_present_rec.c, the store watch) and the mixer
 *    (host/player/mixer.cpp, `stores_`) both key a store by its guest address and
 *    carry its bytes from one holder to the next, so a block handed on with its bytes
 *    is the block both already describe; zeroing it here would show the watch a change
 *    no guest made and leave the mixer's copy stale. In this game the two are the same:
 *    CRI clears a stream buffer whole right after its first Lock (the memset called at
 *    0x007C9D1D) and again before its final Release (0x007C7E3F), each through a
 *    DSBLOCK_ENTIREBUFFER Lock, so a block goes free all zeros.
 *  - Every handout, fresh or reused, is checked first against every buffer record
 *    (store_guard): a block that a buffer without its final Release already names
 *    stops the run by name rather than being handed to a second one.
 *
 * Every choice here is a function of the guest's own call sequence, so the same calls
 * get the same blocks in every run. */
typedef struct {
    uint32_t addr, len;      /* the block, as sa2_guest_alloc gave it; never split or moved */
    uint32_t users;          /* buffers without their final Release that name it */
    uint32_t open;           /* Locks on it with no Unlock yet */
    uint32_t free_seq;       /* when it went free (oldest first); 0 while held or kept */
    uint32_t kept;           /* its last holder went with a Lock open: never handed out again */
} ds_store;
static ds_store g_store[SA2_DS_MAX_STORES];
static uint32_t g_store_used, g_store_free_seq;
_Static_assert(SA2_DS_MAX_STORES < 0xFFFFu, "a store index + 1 must fit g_buf_store");

static uint32_t store_index(uint32_t row) {
    return row < SA2_DS_MAX_OBJECTS ? g_buf_store[row] : 0u;
}

/* The block at `a` (`len` bytes) is about to be handed to buffer `to`: no record of a
 * buffer without its final Release may name any byte of it. */
static void store_guard(uint32_t a, uint32_t len, uint32_t to) {
    if (g_break == BRK_SHAREFREE_NOGUARD) return;
    for (uint32_t r = 0; r < g_buf_used; r++) {
        const sa2_ds_buffer *h = &g_buf[r];
        if (h->released || !h->mem || !h->mem_len) continue;
        if (h->mem < a + len && a < h->mem + h->mem_len) {
            sa2_trap_note("dsound: the block at %#010x (%u bytes) is about to go to buffer "
                          "%#010x, and buffer %#010x, which has not had its final Release, "
                          "holds %#010x (%u bytes): two live buffers would share one store",
                          a, len, to, h->addr, h->mem, h->mem_len);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_OBJ_BASE);
        }
    }
}

/* Whether the `len` bytes at `a` are all zero. Through sa2_peek, as every guest read
 * here is; a block it cannot read is not called clear. */
static int store_is_clear(uint32_t a, uint32_t len) {
    uint8_t chunk[512];
    for (uint32_t at = 0; at < len; at += (uint32_t)sizeof chunk) {
        const uint32_t n = len - at < sizeof chunk ? len - at : (uint32_t)sizeof chunk;
        if (sa2_peek(a + at, n, chunk) != SA2_OK) return 0;
        for (uint32_t i = 0; i < n; i++)
            if (chunk[i]) return 0;
    }
    return 1;
}

static uint32_t buf_alloc(sa2_ds_buffer *b, uint32_t len) {
    if (!len) return 0;
    if (b->released) {
        /* The store went back at the final Release, and may be another buffer's by
         * now; a Lock through a pointer to a released buffer is a use after free in
         * DirectSound too. sa2_d3d9.c's obj_alloc stops on the same event. */
        sa2_trap_note("dsound: buffer %#010x is locked after its final release; its store "
                      "%#010x went back then and this runtime hands it to no one twice",
                      b->addr, b->mem);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, b->addr);
    }
    if (b->mem && b->mem_len >= len) return b->mem;
    if (b->mem) {
        /* dwBufferBytes is fixed once a store exists (only a primary buffer's is set
         * later, and only while it is 0), and Lock asks for exactly it, so this is
         * unreachable; a store that grew would no longer be its buffer's length. */
        sa2_trap_note("dsound: buffer %#010x asks for %u bytes of store and holds %u at "
                      "%#010x", b->addr, len, b->mem_len, b->mem);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, b->addr);
    }
    const uint32_t row = (uint32_t)(b - g_buf);
    if (g_break == BRK_NOREUSE) {
        const uint32_t a = sa2_guest_alloc(len, 32u);
        if (!a) {
            sa2_trap_note("dsound: the runtime arena has no room for %u bytes of sound-buffer "
                          "backing store", len);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_OBJ_BASE);
        }
        sa2_guest_fill(a, 0, len);
        b->mem = a;
        b->mem_len = len;
        g_st.guest_bytes_allocated += len;
        return a;
    }
    uint32_t k = 0xFFFFFFFFu;
    for (uint32_t i = 0; i < g_store_used; i++)
        if (g_store[i].free_seq && g_store[i].len == len &&
            (k == 0xFFFFFFFFu || g_store[i].free_seq < g_store[k].free_seq))
            k = i;
    if (k != 0xFFFFFFFFu) {
        store_guard(g_store[k].addr, len, b->addr);
        g_store[k].free_seq = 0;
        g_st.stores_reused++;
        /* Handed on with its bytes (the stores comment above): count the blocks that
         * are not all zero, so "CRI clears a store before its final Release" is a
         * figure in the run record and not only a reading of the image. */
        if (!store_is_clear(g_store[k].addr, len)) g_st.stores_reused_dirty++;
    } else {
        if (g_store_used >= SA2_DS_MAX_STORES) {
            sa2_trap_note("dsound: the store table of %u is full and none of its free blocks "
                          "is %u bytes long", SA2_DS_MAX_STORES, len);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_OBJ_BASE);
        }
        uint32_t live = 0, free_bytes = 0;
        for (uint32_t i = 0; i < g_store_used; i++) {
            if (g_store[i].users) live++;
            if (g_store[i].free_seq) free_bytes += g_store[i].len;
        }
        const uint32_t a = sa2_guest_alloc(len, 32u);
        if (!a) {
            sa2_trap_note("dsound: the runtime arena has no room for %u bytes of sound-buffer "
                          "backing store (%u stores held, %u bytes free in blocks of other "
                          "lengths)", len, live, free_bytes);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_OBJ_BASE);
        }
        store_guard(a, len, b->addr);
        sa2_guest_fill(a, 0, len);
        k = g_store_used++;
        memset(&g_store[k], 0, sizeof g_store[k]);
        g_store[k].addr = a;
        g_store[k].len = len;
        g_st.guest_bytes_allocated += len;
    }
    g_store[k].users = 1;
    g_buf_store[row] = (uint16_t)(k + 1u);
    b->mem = g_store[k].addr;
    b->mem_len = len;
    return b->mem;
}

/* A Lock opened (+1) or an Unlock closed (-1) on `b`'s store. */
static void store_open(const sa2_ds_buffer *b, int by) {
    const uint32_t si = store_index((uint32_t)(b - g_buf));
    if (!si || b->released) return;
    ds_store *s = &g_store[si - 1u];
    if (by > 0) s->open++;
    else if (s->open) s->open--;
}

/* `o`'s count reached zero: its buffer is released, its store loses a holder and goes
 * free (or is kept, with a Lock open) when that was the last, and its slot goes on the
 * ring for obj_new. */
static void buf_final_release(ds_obj *o, sa2_ds_buffer *b) {
    b->released = 1;
    const uint32_t row = o->buf;
    const uint32_t si = store_index(row);
    if (si) {
        ds_store *s = &g_store[si - 1u];
        if (g_break == BRK_SHAREFREE || g_break == BRK_SHAREFREE_NOGUARD) s->users = 1;
        if (s->users) s->users--;
        if (!s->users) {
            if (s->open) {
                s->kept = 1;
                g_st.stores_kept_locked++;
            } else {
                s->free_seq = ++g_store_free_seq;
            }
        }
        g_buf_store[row] = 0;
    }
    if (g_break != BRK_NOREUSE && g_obj_free_n < SA2_DS_MAX_OBJECTS) {
        g_obj_free[(g_obj_free_head + g_obj_free_n) % SA2_DS_MAX_OBJECTS] =
            (uint16_t)((o->addr - SA2_DS_OBJ_BASE) / SA2_DS_OBJ_STRIDE);
        g_obj_free_n++;
    }
}

/* ------------------------------------------------------- the cursors
 *
 * The whole of the derivation, in one place, so a reader never has to hunt for
 * a second copy that disagrees. Everything is uint64 until the final store.
 *
 * `bytes_at(b, now)` is the byte offset the play cursor has at virtual
 * instant `now`, and it is a pure function of (anchor_us, anchor_pos,
 * avg_bytes_per_sec, block_align, bytes, looping) and `now`. Nothing in it
 * reads host time, a random number, or a pointer value. */
static uint32_t cursor_align(uint32_t pos, uint32_t block) {
    if (block < 2u) return pos;          /* a block of 0 or 1 cannot misalign */
    return pos - (pos % block);
}

/* Advance `b`'s cursor to `now` and settle a non-looping buffer that has run
 * out. Returns the play cursor. `stopped_here` is set when this call is the
 * one that took the buffer out of DSBSTATUS_PLAYING. */
static uint32_t buf_play_cursor(sa2_ds_buffer *b, uint64_t now, int *stopped_here) {
    if (stopped_here) *stopped_here = 0;
    if (!b->bytes) return 0;
    const uint32_t block = b->block_align ? b->block_align : 1u;
    if (!b->playing || !b->avg_bytes_per_sec)
        return cursor_align(b->anchor_pos % b->bytes, block);

    uint64_t elapsed = 0;
    if (g_break != BRK_FROZEN && now > b->anchor_us) elapsed = now - b->anchor_us;
    /* 64-bit throughout. `elapsed * avg_bytes_per_sec` passes 2^32 after about
     * 24 seconds of virtual time at 176 400 bytes/s, so a 32-bit intermediate
     * would wrap inside one loading screen. The division is integer and floors,
     * never floating point, because a replay has to give the same byte. */
    const uint64_t advance = (elapsed * (uint64_t)b->avg_bytes_per_sec) / 1000000ull;
    uint64_t pos = (uint64_t)b->anchor_pos + advance;

    if (b->looping) {
        if (pos >= (uint64_t)b->bytes) {
            g_st.cursor_wraps++;
            pos %= (uint64_t)b->bytes;
        }
    } else if (pos >= (uint64_t)b->bytes) {
        /* Halt at the end: a non-looping buffer does not wrap and does not run
         * past its last sample. The last whole block is the largest offset a
         * device can report, because dwBufferBytes itself is one past the end. */
        pos = b->bytes > block ? (uint64_t)(b->bytes - block) : 0ull;
        b->playing = 0;
        b->halted = 1;
        b->anchor_us = now;
        b->anchor_pos = (uint32_t)pos;
        if (stopped_here) *stopped_here = 1;
    }
    return cursor_align((uint32_t)pos, block);
}

/* The fixed, block-aligned lead. Computed once, when the format becomes known,
 * and stored on the buffer -- not derived per call. A lead recomputed on every
 * call would move when the format changed under a running buffer, and a write
 * cursor that moves backwards relative to the play cursor is the one thing a
 * streaming decoder cannot recover from. */
static void buf_set_lead(sa2_ds_buffer *b) {
    const uint32_t block = b->block_align ? b->block_align : 1u;
    uint64_t lead = ((uint64_t)g_write_lead_us * (uint64_t)b->avg_bytes_per_sec) / 1000000ull;
    uint32_t v = cursor_align((uint32_t)(lead > 0xFFFFFFFFull ? 0xFFFFFFFFull : lead), block);
    if (v < block) v = block;
    if (b->bytes && v > b->bytes - block) v = b->bytes > block ? b->bytes - block : 0u;
    b->write_lead = v;
}

static uint32_t buf_write_cursor(const sa2_ds_buffer *b, uint32_t play) {
    if (g_break == BRK_NOLEAD) return play;
    if (!b->bytes) return 0;
    if (!b->playing) return play;    /* a stopped buffer's cursors sit together */
    uint64_t w = (uint64_t)play + (uint64_t)b->write_lead;
    if (w >= (uint64_t)b->bytes) w -= (uint64_t)b->bytes;
    return cursor_align((uint32_t)w, b->block_align ? b->block_align : 1u);
}

/* Re-anchor: the cursor is elapsed-since-anchor, never elapsed-since-start.
 * Play, Stop and SetCurrentPosition are the three calls that do this. */
static void buf_anchor(sa2_ds_buffer *b, uint32_t pos) {
    b->anchor_us = sa2_virtual_us();
    b->last_read_us = b->anchor_us;
    b->anchor_pos = b->bytes ? cursor_align(pos % b->bytes,
                                            b->block_align ? b->block_align : 1u) : 0u;
}

/* ---------------------------------------------------- shared IUnknown */

/* The IIDs this runtime recognises, little-endian as they sit in memory.
 * Everything else gets an honest E_NOINTERFACE and a null out-pointer -- the
 * one answer a guest can always recover from, and the one a fabricated
 * pointer would make unrecoverable. */
static const struct { const char *name; uint8_t b[16]; uint16_t type; } IIDS[] = {
    {"IID_IUnknown",
     {0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0xC0,0x00,0x00,0x00,0x00,0x00,0x00,0x46}, T_DS8},
    /* {279AFA83-4981-11CE-A521-0020AF0BE560} IID_IDirectSound */
    {"IID_IDirectSound",
     {0x83,0xFA,0x9A,0x27,0x81,0x49,0xCE,0x11,0xA5,0x21,0x00,0x20,0xAF,0x0B,0xE5,0x60}, T_DS8},
    /* {C50A7E93-F395-4834-9EF6-7FA99DE50966} IID_IDirectSound8 */
    {"IID_IDirectSound8",
     {0x93,0x7E,0x0A,0xC5,0x95,0xF3,0x34,0x48,0x9E,0xF6,0x7F,0xA9,0x9D,0xE5,0x09,0x66}, T_DS8},
    /* {279AFA85-4981-11CE-A521-0020AF0BE560} IID_IDirectSoundBuffer */
    {"IID_IDirectSoundBuffer",
     {0x85,0xFA,0x9A,0x27,0x81,0x49,0xCE,0x11,0xA5,0x21,0x00,0x20,0xAF,0x0B,0xE5,0x60}, T_DSBUF},
    /* {6825A449-7524-4D82-920F-50E36AB3AB1E} IID_IDirectSoundBuffer8 */
    {"IID_IDirectSoundBuffer8",
     {0x49,0xA4,0x25,0x68,0x24,0x75,0x82,0x4D,0x92,0x0F,0x50,0xE3,0x6A,0xB3,0xAB,0x1E}, T_DSBUF},
};

static const char *iid_name(uint32_t p, int *type) {
    uint8_t g[16];
    *type = -1;
    if (!p || sa2_peek(p, 16, g) != SA2_OK) return NULL;
    for (uint32_t i = 0; i < sizeof IIDS / sizeof IIDS[0]; i++)
        if (!memcmp(g, IIDS[i].b, 16)) { *type = (int)IIDS[i].type; return IIDS[i].name; }
    return NULL;
}

static uint32_t m_QueryInterface(ds_ctx *c) {
    int t = -1;
    const char *nm = iid_name(c->a[0], &t);
    if (nm && c->obj && (t == (int)c->obj->type || t == T_DS8 || t == T_DSBUF)) {
        /* The object really is that interface: this *is* an IDirectSound8, and
         * CRI's SetupSound at 0x007C6859 asks it exactly that and releases the
         * answer immediately. Handing back `this` with a reference is what a
         * real object does; it is not a fabrication. */
        if (t == (int)c->obj->type) {
            if (c->a[1]) pk32(c->a[1], c->self);
            c->obj->refs++;
            g_st.qi_answered++;
            c->out0 = c->self;
            c->answered = SA2_DS_ANS_OBJECT;
            return SA2_DS_OK;
        }
    }
    if (c->a[1]) pk32(c->a[1], 0);
    g_st.qi_declined++;
    c->answered = SA2_DS_ANS_NOOP;
    return SA2_DSERR_NOINTERFACE;
}
static uint32_t m_AddRef(ds_ctx *c) {
    if (!c->obj) return 1;
    c->answered = SA2_DS_ANS_GUEST;
    return ++c->obj->refs;
}
/* The final Release is the one that takes a live object's count to zero, and it
 * happens once: a Release past zero answers 0 and changes nothing, and a Release that
 * takes a revived dead object (an AddRef after its final Release) back to zero is not
 * a second final one. So a store is never given up twice and a slot never goes on the
 * ring twice: a slot on the ring twice would come off it twice, out of the
 * oldest-released order obj_new keeps, and enough of them would fill the ring and push
 * other released slots off it for good. */
static uint32_t m_Release(ds_ctx *c) {
    if (!c->obj) return 0;
    c->answered = SA2_DS_ANS_GUEST;
    if (!c->obj->refs) return 0;
    c->obj->refs--;
    if (!c->obj->refs && c->obj->alive) {
        c->obj->alive = 0;
        if (c->b) buf_final_release(c->obj, c->b);
    }
    return c->obj->refs;
}

/* --------------------------------------------- IDirectSound8's methods */

/* WAVEFORMATEX, read out of the guest's own memory. Nothing here invents a
 * field: if the guest did not supply a format, the buffer has none and the
 * cursor does not move, which is the truth rather than a guessed rate. */
static int read_wfx(uint32_t p, sa2_ds_buffer *b) {
    if (!p) return 0;
    b->format_tag       = pe16(p + 0u);
    b->channels         = pe16(p + 2u);
    b->samples_per_sec  = pe32(p + 4u);
    b->avg_bytes_per_sec= pe32(p + 8u);
    b->block_align      = pe16(p + 12u);
    b->bits_per_sample  = pe16(p + 14u);
    /* A format whose nAvgBytesPerSec disagrees with its own rate and block
     * alignment is refused rather than silently believed: the cursor rate is
     * the declared one, and the two fields declaring different rates is a bug
     * in whoever built the structure, not something to average. */
    if (b->block_align && b->samples_per_sec) {
        const uint64_t implied = (uint64_t)b->samples_per_sec * (uint64_t)b->block_align;
        if (b->avg_bytes_per_sec && implied != (uint64_t)b->avg_bytes_per_sec) {
            sa2_trap_note("dsound: a WAVEFORMATEX at %#010x declares nAvgBytesPerSec %u but "
                          "nSamplesPerSec %u x nBlockAlign %u = %llu. The cursor rate is the "
                          "declared one and these two declare different rates, so this "
                          "runtime refuses rather than choosing one of them",
                          p, b->avg_bytes_per_sec, b->samples_per_sec, b->block_align,
                          (unsigned long long)implied);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
        }
        if (!b->avg_bytes_per_sec && implied <= 0xFFFFFFFFull)
            b->avg_bytes_per_sec = (uint32_t)implied;
    }
    b->format_from_guest = 1;
    return 1;
}

static void write_wfx(uint32_t p, const sa2_ds_buffer *b) {
    pk16(p + 0u,  (uint16_t)b->format_tag);
    pk16(p + 2u,  (uint16_t)b->channels);
    pk32(p + 4u,  b->samples_per_sec);
    pk32(p + 8u,  b->avg_bytes_per_sec);
    pk16(p + 12u, (uint16_t)b->block_align);
    pk16(p + 14u, (uint16_t)b->bits_per_sample);
    pk16(p + 16u, 0u);                           /* cbSize: no extra bytes are kept */
}

static uint32_t m_CreateSoundBuffer(ds_ctx *c) {
    const uint32_t desc = c->a[0], out = c->a[1], outer = c->a[2];
    if (!desc || !out) return SA2_DSERR_INVALIDPARAM;
    if (outer) return SA2_DSERR_INVALIDPARAM;     /* aggregation is not supported: documented */
    const uint32_t size = pe32(desc + 0u);
    if (size != 24u && size != 36u) {
        sa2_trap_note("IDirectSound8::CreateSoundBuffer from %#010x: DSBUFFERDESC.dwSize is "
                      "%u, and this runtime knows only 24 (DX7) and 36 (DX8). Reading the "
                      "rest of a structure whose length it cannot name would be a guess",
                      c->caller, size);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
    }
    sa2_ds_buffer nb, *b = &nb;
    memset(b, 0, sizeof *b);
    b->create_caller = c->caller;
    b->flags  = pe32(desc + 4u);
    b->bytes  = pe32(desc + 8u);
    b->primary = (b->flags & 1u) ? 1u : 0u;       /* DSBCAPS_PRIMARYBUFFER */
    const uint32_t wfx = pe32(desc + 16u);
    if (wfx) read_wfx(wfx, b);

    if (b->primary) {
        if (b->bytes) return SA2_DSERR_INVALIDPARAM;   /* documented: must be 0 */
        /* A primary buffer's length is the device's to choose; a real one is a
         * fraction of a second of the mix format. This runtime has no device,
         * so the length is a *named placeholder* and is flagged invented.
         * It matters only if the guest ever reads the primary's cursor. */
        b->bytes = 0;    /* filled in once a format is known; see set_primary_len */
    }
    if (b->block_align && b->bytes % b->block_align) {
        sa2_trap_note("IDirectSound8::CreateSoundBuffer from %#010x: dwBufferBytes %u is not "
                      "a multiple of nBlockAlign %u, so no cursor in it can be both in range "
                      "and on a sample boundary", c->caller, b->bytes, b->block_align);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
    }
    buf_set_lead(b);

    ds_obj *o;
    buf_new(&nb, &o);
    if (nb.primary) g_st.primary_buffers++;
    pk32(out, o->addr);
    c->out0 = o->addr;
    c->answered = SA2_DS_ANS_OBJECT;
    return SA2_DS_OK;
}

static uint32_t m_SetCooperativeLevel(ds_ctx *c) {
    g_coop_hwnd = c->a[0];
    g_coop_level = c->a[1];
    g_st.cooperative_level = c->a[1];
    /* There is no window to make exclusive and no device to share, so there is
     * nothing to do and nothing to report: recorded, DS_OK. */
    c->answered = SA2_DS_ANS_NOOP;
    return SA2_DS_OK;
}

static uint32_t m_GetSpeakerConfig(ds_ctx *c) {
    /* CRI's SetupSound reads this at 0x007C68AB and turns it into a channel
     * count at 0x007C6470, which accepts 1 (headphone), 2 (mono), 4 (stereo),
     * 5 (surround) and 6..8. Stereo is the configuration the image's own
     * 48 kHz two-channel primary format asks for, so answering anything else
     * would make the device disagree with the format the guest sets on it --
     * but it is still a value this runtime made up, and it is flagged. */
    const uint32_t v = cap_answer(c, "IDirectSound8", "GetSpeakerConfig", g_speaker_config);
    pk32(c->a[0], v);
    c->out0 = v;
    return SA2_DS_OK;
}
static uint32_t m_SetSpeakerConfig(ds_ctx *c) {
    g_speaker_config = c->a[0];
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

/* DSCAPS. Only the fields a device with no hardware can state honestly are
 * written; the rest are zero, and zero is recorded here as a claim ("this
 * device has no hardware mixing and no hardware buffers") rather than as an
 * absence. dwSize is checked against the guest's own value. */
static uint32_t m_DsGetCaps(ds_ctx *c) {
    const uint32_t p = c->a[0];
    if (!p) return SA2_DSERR_INVALIDPARAM;
    const uint32_t size = pe32(p + 0u);
    if (size < 0x68u) {
        sa2_trap_note("IDirectSound8::GetCaps from %#010x: DSCAPS.dwSize is %u and the "
                      "structure is 0x68 bytes; writing 0x68 into a shorter one would "
                      "corrupt whatever follows it", c->caller, size);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
    }
    if (g_strict_caps)
        refuse("IDirectSound8", "GetCaps", c->caller,
               "SA2_DSOUND_STRICT_CAPS=1 refuses every fabricated capability answer");
    sa2_guest_fill(p + 4u, 0, 0x68u - 4u);
    /* DSCAPS_PRIMARYSTEREO | DSCAPS_PRIMARY16BIT | DSCAPS_CONTINUOUSRATE */
    pk32(p + 4u, 0x00000001u | 0x00000008u | 0x00000010u);
    pk32(p + 8u, 100u);           /* dwMinSecondarySampleRate: the documented minimum */
    pk32(p + 12u, 200000u);       /* dwMaxSecondarySampleRate: the documented maximum */
    pk32(p + 16u, 1u);            /* dwPrimaryBuffers */
    /* Everything from dwMaxHwMixingAllBuffers on stays zero: no hardware. */
    c->answered = SA2_DS_ANS_INVENT;
    return SA2_DS_OK;
}

static uint32_t m_DsInitialize(ds_ctx *c) {
    (void)c;
    /* DirectSoundCreate8 already initialised this object, which is exactly
     * what the documented return says. */
    return SA2_DSERR_ALREADYINITIALIZED;
}
static uint32_t m_Compact(ds_ctx *c) { (void)c; return SA2_DS_OK; }

static uint32_t m_DuplicateSoundBuffer(ds_ctx *c) {
    ds_obj *src = obj_of(c->a[0]);
    if (!src || src->type != T_DSBUF || src->buf >= g_buf_used) return SA2_DSERR_INVALIDPARAM;
    if (!c->a[1]) return SA2_DSERR_INVALIDPARAM;
    const sa2_ds_buffer *s = &g_buf[src->buf];
    if (s->released) {
        sa2_trap_note("IDirectSound8::DuplicateSoundBuffer from %#010x: buffer %#010x has had "
                      "its final release, and a duplicate of it would share a store that went "
                      "back then", c->caller, c->a[0]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->a[0]);
    }
    const uint32_t src_store = store_index(src->buf);
    /* A duplicate shares the original's sample data and has its own cursors,
     * which is what the documentation says and what this copy does: the `mem`
     * pointer is shared, the anchor is fresh. One made before the original's
     * first Lock has no `mem` yet and gets a store of its own at its own first
     * Lock, as it always has. */
    sa2_ds_buffer nb = *s, *b = &nb;
    b->create_caller = c->caller;
    b->playing = 0; b->looping = 0; b->halted = 0;
    b->anchor_us = sa2_virtual_us(); b->anchor_pos = 0;
    b->play_calls = b->stop_calls = b->lock_calls = 0;
    b->unlock_calls = b->setpos_calls = b->getpos_calls = 0;
    b->bytes_locked = 0; b->released = 0;
    ds_obj *o;
    buf_new(&nb, &o);
    if (src_store) {
        g_buf_store[o->buf] = (uint16_t)src_store;
        g_store[src_store - 1u].users++;
    }
    pk32(c->a[1], o->addr);
    c->out0 = o->addr;
    c->answered = SA2_DS_ANS_OBJECT;
    return SA2_DS_OK;
}

static uint32_t m_VerifyCertification(ds_ctx *c) {
    /* DS_CERTIFIED is 0 and DS_UNCERTIFIED is 1. A device that is not a WHQL
     * driver is uncertified, and that is the honest answer for a device that
     * is not a driver at all. */
    const uint32_t v = cap_answer(c, "IDirectSound8", "VerifyCertification", 1u);
    pk32(c->a[0], v);
    c->out0 = v;
    return SA2_DS_OK;
}

/* ---------------------------------------- IDirectSoundBuffer8's methods */

static void set_primary_len(sa2_ds_buffer *b) {
    if (!b->primary || b->bytes || !b->avg_bytes_per_sec) return;
    uint64_t n = ((uint64_t)g_primary_ms * (uint64_t)b->avg_bytes_per_sec) / 1000ull;
    const uint32_t block = b->block_align ? b->block_align : 1u;
    b->bytes = cursor_align((uint32_t)n, block);
    if (!b->bytes) b->bytes = block;
    buf_set_lead(b);
}

static uint32_t m_BufSetFormat(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (!b->primary) return SA2_DSERR_INVALIDCALL;   /* documented: primary only */
    if (!c->a[0]) return SA2_DSERR_INVALIDPARAM;
    read_wfx(c->a[0], b);
    set_primary_len(b);
    buf_set_lead(b);
    buf_anchor(b, 0);
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

static uint32_t m_BufGetFormat(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    const uint32_t p = c->a[0], alloc = c->a[1], written = c->a[2];
    if (!b->format_from_guest) {
        /* Nothing set a format on this buffer, so there is nothing to hand
         * back. Answering DS_OK into an unwritten WAVEFORMATEX is exactly the
         * failure `DF_OUT` exists to stop. */
        sa2_trap_note("IDirectSoundBuffer8::GetFormat from %#010x on %#010x: no format has "
                      "ever been supplied for this buffer, and this runtime invents none",
                      c->caller, c->self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
    }
    if (p) {
        if (alloc < 18u) return SA2_DSERR_INVALIDPARAM;
        write_wfx(p, b);
    }
    if (written) pk32(written, 18u);
    c->out0 = b->samples_per_sec;
    c->out1 = b->avg_bytes_per_sec;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

/* DSBCAPS { dwSize, dwFlags, dwBufferBytes, dwUnlockTransferRate,
 *           dwPlayCpuOverhead }. The first three are the guest's own; the last
 *  two describe a device and are invented. */
static uint32_t m_BufGetCaps(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b || !c->a[0]) return SA2_DSERR_INVALIDPARAM;
    const uint32_t p = c->a[0];
    if (pe32(p) < 20u) return SA2_DSERR_INVALIDPARAM;
    /* DSBCAPS_LOCSOFTWARE: this device has no hardware, and saying so is true. */
    pk32(p + 4u, b->flags | 0x00000008u);
    pk32(p + 8u, b->bytes);
    pk32(p + 12u, cap_answer(c, "IDirectSoundBuffer8", "GetCaps.dwUnlockTransferRate", 0u));
    pk32(p + 16u, cap_answer(c, "IDirectSoundBuffer8", "GetCaps.dwPlayCpuOverhead", 0u));
    c->out0 = b->bytes;
    return SA2_DS_OK;
}

static uint32_t m_GetCurrentPosition(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (!c->a[0] && !c->a[1]) return SA2_DSERR_INVALIDPARAM;
    const uint64_t now = sa2_virtual_us();
    const uint32_t prev = b->last_play;
    const uint32_t was_playing = b->playing;
    const uint64_t since = now > b->last_read_us ? now - b->last_read_us : 0ull;
    int stopped_here = 0;
    const uint32_t play = buf_play_cursor(b, now, &stopped_here);
    const uint32_t write = buf_write_cursor(b, play);
    /* A play cursor that goes backwards without a wrap, a Stop or a
     * SetCurrentPosition is the one thing a streaming decoder cannot recover
     * from. Counted where it is detected, not asserted away -- and the wrap
     * test is the exact arithmetic rather than a threshold: the bytes that
     * could have been played since the previous reading against the bytes a
     * wrap from `prev` to `play` would have had to cover. */
    g_st.cursor_reads++;
    if (was_playing && b->getpos_calls && play < prev && !stopped_here && b->bytes) {
        const uint64_t could_have = (since * (uint64_t)b->avg_bytes_per_sec) / 1000000ull;
        const uint64_t a_wrap_needs = (uint64_t)(b->bytes - prev) + (uint64_t)play;
        if (could_have < a_wrap_needs) g_st.cursor_went_backwards++;
    }
    b->getpos_calls++;
    b->last_play = play;
    b->last_write = write;
    b->last_read_us = now;
    if (c->a[0]) pk32(c->a[0], play);
    if (c->a[1]) pk32(c->a[1], write);
    c->out0 = play;
    c->out1 = write;
    c->answered = SA2_DS_ANS_CLOCK;
    return SA2_DS_OK;
}

static uint32_t m_SetCurrentPosition(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (b->primary) return SA2_DSERR_INVALIDCALL;   /* documented: secondary only */
    if (b->bytes && c->a[0] >= b->bytes) return SA2_DSERR_INVALIDPARAM;
    buf_anchor(b, c->a[0]);                          /* re-anchors: see sa2_dsound.h */
    b->halted = 0;
    b->setpos_calls++;
    b->last_play = b->anchor_pos;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

static uint32_t m_Play(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (!b->avg_bytes_per_sec) {
        sa2_trap_note("IDirectSoundBuffer8::Play from %#010x on %#010x: this buffer has no "
                      "format, so there is no rate to advance its cursor at, and a buffer "
                      "that reports itself playing with a frozen cursor makes a polling "
                      "guest spin forever", c->caller, c->self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
    }
    /* Play resumes from where the cursor is now, and re-anchors there. */
    const uint32_t at = buf_play_cursor(b, sa2_virtual_us(), NULL);
    b->looping = (c->a[2] & 1u) ? 1u : 0u;           /* DSBPLAY_LOOPING */
    b->playing = 1;
    b->halted = 0;
    buf_anchor(b, at);
    b->play_calls++;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

static uint32_t m_Stop(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    const uint32_t at = buf_play_cursor(b, sa2_virtual_us(), NULL);
    b->playing = 0;
    buf_anchor(b, at);                               /* re-anchors: see sa2_dsound.h */
    b->stop_calls++;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

static uint32_t m_GetStatus(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b || !c->a[0]) return SA2_DSERR_INVALIDPARAM;
    buf_play_cursor(b, sa2_virtual_us(), NULL);       /* settle a halted buffer first */
    uint32_t v = 0;
    if (b->playing) v |= 0x00000001u;                 /* DSBSTATUS_PLAYING */
    if (b->playing && b->looping) v |= 0x00000004u;   /* DSBSTATUS_LOOPING */
    pk32(c->a[0], v);
    c->out0 = v;
    c->answered = SA2_DS_ANS_CLOCK;
    return SA2_DS_OK;
}

/* Lock hands back the guest's own memory: whatever it writes stays written and
 * can be read after the run. Nothing is decoded and nothing is played.
 * DSBLOCK_ENTIREBUFFER (2) ignores dwBytes; DSBLOCK_FROMWRITECURSOR (1) starts
 * at the write cursor, which is the clock-derived one. */
static uint32_t m_Lock(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (b->primary && g_coop_level != 4u)
        return SA2_DSERR_INVALIDCALL;   /* documented: primary locks need DSSCL_WRITEPRIMARY */
    if (!b->bytes) return SA2_DSERR_INVALIDCALL;
    const uint32_t flags = c->a[6];
    uint32_t off = c->a[0], len = c->a[1];
    if (flags & 2u) { off = 0; len = b->bytes; }
    else if (flags & 1u) off = buf_write_cursor(b, buf_play_cursor(b, sa2_virtual_us(), NULL));
    if (off >= b->bytes) return SA2_DSERR_INVALIDPARAM;
    if (len > b->bytes) return SA2_DSERR_INVALIDPARAM;
    const uint32_t mem = buf_alloc(b, b->bytes);
    const uint32_t first = (off + len > b->bytes) ? b->bytes - off : len;
    const uint32_t second = len - first;
    if (c->a[2]) pk32(c->a[2], mem + off); else return SA2_DSERR_INVALIDPARAM;
    if (c->a[3]) pk32(c->a[3], first);    else return SA2_DSERR_INVALIDPARAM;
    /* The wrap half: a real device hands back the start of the buffer. The
     * guest may pass null pointers for it only when it knows there is none. */
    if (c->a[4]) pk32(c->a[4], second ? mem : 0u);
    if (c->a[5]) pk32(c->a[5], second);
    else if (second) {
        sa2_trap_note("IDirectSoundBuffer8::Lock from %#010x: the request wraps by %u bytes "
                      "and the guest passed no pdwAudioBytes2 to put the second length in",
                      c->caller, second);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
    }
    store_open(b, +1);
    b->lock_calls++;
    b->bytes_locked += len;
    c->out0 = mem + off;
    c->out1 = first;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}

static uint32_t m_Unlock(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (b->released) {
        /* A released buffer's STATE still names the store it held, which may be another
         * buffer's by now: a duplicate's, or the next holder's once it is handed on. The
         * recorder (au_store_unlock), tools/s5/stream_dump.py and the mixer close a Lock
         * on the store a DS_UNLOCK's STATE names, so this call would close that buffer's
         * open Lock in all three. A use after free in DirectSound too, and it stops here
         * by name, as a Lock (buf_alloc) and a DuplicateSoundBuffer do. */
        sa2_trap_note("IDirectSoundBuffer8::Unlock from %#010x: buffer %#010x has had its "
                      "final release, and its store %#010x may be another buffer's by now, "
                      "whose open Lock this Unlock would close", c->caller, b->addr, b->mem);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, b->addr);
    }
    /* Nothing to copy: Lock handed back the buffer's own guest memory, so the
     * guest wrote straight into it and there is no staging area to flush. */
    store_open(b, -1);
    b->unlock_calls++;
    c->answered = SA2_DS_ANS_NOOP;
    return SA2_DS_OK;
}

static uint32_t m_Restore(ds_ctx *c) {
    /* No buffer is ever lost: there is no device to lose it to. */
    c->answered = SA2_DS_ANS_NOOP;
    return SA2_DS_OK;
}
static uint32_t m_BufInitialize(ds_ctx *c) {
    (void)c;
    return SA2_DSERR_ALREADYINITIALIZED;
}

/* Volume, pan and frequency: stored, handed back, and audible to nobody. A Get
 * with no matching Set answers the documented default (silence at full volume,
 * centre pan, the format's own rate) and is logged as SA2_DS_ANS_UNSET, which
 * is a different claim from "the guest set this".
 *
 * They live in their own array rather than on `sa2_ds_buffer` because that
 * structure is the published record and these three are not part of what a
 * reader of the run needs; they are per buffer and indexed the same way
 * (g_scalar, with the rest of the state at the head of this file). */

static uint32_t scalar_index(const ds_ctx *c) {
    return c->obj && c->obj->buf < g_buf_used ? c->obj->buf : 0u;
}
static uint32_t m_SetVolume(ds_ctx *c) {
    if (!c->b) return SA2_DSERR_INVALIDCALL;
    if ((int32_t)c->a[0] > 0 || (int32_t)c->a[0] < -10000) return SA2_DSERR_INVALIDPARAM;
    const uint32_t i = scalar_index(c);
    g_scalar[i].volume = (int32_t)c->a[0]; g_scalar[i].vset = 1;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}
static uint32_t m_GetVolume(ds_ctx *c) {
    if (!c->b || !c->a[0]) return SA2_DSERR_INVALIDPARAM;
    const uint32_t i = scalar_index(c);
    pk32(c->a[0], (uint32_t)g_scalar[i].volume);
    c->out0 = (uint32_t)g_scalar[i].volume;
    c->answered = g_scalar[i].vset ? SA2_DS_ANS_GUEST : SA2_DS_ANS_UNSET;
    return SA2_DS_OK;
}
static uint32_t m_SetPan(ds_ctx *c) {
    if (!c->b) return SA2_DSERR_INVALIDCALL;
    if ((int32_t)c->a[0] > 10000 || (int32_t)c->a[0] < -10000) return SA2_DSERR_INVALIDPARAM;
    const uint32_t i = scalar_index(c);
    g_scalar[i].pan = (int32_t)c->a[0]; g_scalar[i].pset = 1;
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}
static uint32_t m_GetPan(ds_ctx *c) {
    if (!c->b || !c->a[0]) return SA2_DSERR_INVALIDPARAM;
    const uint32_t i = scalar_index(c);
    pk32(c->a[0], (uint32_t)g_scalar[i].pan);
    c->out0 = (uint32_t)g_scalar[i].pan;
    c->answered = g_scalar[i].pset ? SA2_DS_ANS_GUEST : SA2_DS_ANS_UNSET;
    return SA2_DS_OK;
}
/* SetFrequency changes the rate the cursor advances at, because the cursor is
 * derived from the format's bytes per second and the frequency scales it. A
 * device that accepted the call and kept the old rate would desynchronise the
 * guest's own timing, which is exactly the failure this slice is about. */
static uint32_t m_SetFrequency(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b) return SA2_DSERR_INVALIDCALL;
    if (b->primary) return SA2_DSERR_INVALIDCALL;
    uint32_t hz = c->a[0];
    if (hz == 0u) hz = b->samples_per_sec;           /* DSBFREQUENCY_ORIGINAL */
    if (!b->samples_per_sec || !b->block_align) return SA2_DSERR_INVALIDCALL;
    if (hz < 100u || hz > 200000u) return SA2_DSERR_INVALIDPARAM;
    const uint32_t at = buf_play_cursor(b, sa2_virtual_us(), NULL);
    const uint32_t i = scalar_index(c);
    g_scalar[i].freq = hz; g_scalar[i].fset = 1;
    b->avg_bytes_per_sec = (uint32_t)(((uint64_t)hz * (uint64_t)b->block_align));
    buf_set_lead(b);
    buf_anchor(b, at);                               /* the rate changed: re-anchor */
    c->answered = SA2_DS_ANS_GUEST;
    return SA2_DS_OK;
}
static uint32_t m_GetFrequency(ds_ctx *c) {
    sa2_ds_buffer *b = c->b;
    if (!b || !c->a[0]) return SA2_DSERR_INVALIDPARAM;
    const uint32_t i = scalar_index(c);
    const uint32_t v = g_scalar[i].fset ? g_scalar[i].freq : b->samples_per_sec;
    pk32(c->a[0], v);
    c->out0 = v;
    c->answered = g_scalar[i].fset ? SA2_DS_ANS_GUEST : SA2_DS_ANS_UNSET;
    return SA2_DS_OK;
}

/* ------------------------------------------------------------ the tables */

/* Not `const`: `SA2_DSOUND_BREAK=argc` patches slot 3's count in place, so the
 * declared count the dispatcher pops with and the declared count the probe
 * publishes for `tools/s4/dsound_surface.py --argc` are the *same* number. A
 * break that changed only the pop would leave the static check green over a
 * broken build, which is the shape of unfalsifiable check this project keeps
 * finding. */
static ds_row ROWS_DS8[] = {
    {0,  2, DF_OUT, "QueryInterface", m_QueryInterface},
    {1,  0, 0, "AddRef", m_AddRef},
    {2,  0, 0, "Release", m_Release},
    {3,  3, DF_OUT, "CreateSoundBuffer", m_CreateSoundBuffer},
    {4,  1, DF_OUT | DF_CAP, "GetCaps", m_DsGetCaps},
    {5,  2, DF_OUT, "DuplicateSoundBuffer", m_DuplicateSoundBuffer},
    {6,  2, 0, "SetCooperativeLevel", m_SetCooperativeLevel},
    {7,  0, 0, "Compact", m_Compact},
    {8,  1, DF_OUT | DF_CAP, "GetSpeakerConfig", m_GetSpeakerConfig},
    {9,  1, 0, "SetSpeakerConfig", m_SetSpeakerConfig},
    {10, 1, 0, "Initialize", m_DsInitialize},
    {11, 1, DF_OUT | DF_CAP, "VerifyCertification", m_VerifyCertification},
    {0, 0, 0, NULL, NULL},
};

/* IDirectSoundBuffer8. Slots 21..23 are the DX8 additions and they are named
 * with **no handler**: SetFX and AcquireResources hand back per-effect result
 * codes and GetObjectInPath hands back an interface pointer, and this runtime
 * has no effects and no objects in any path. They refuse by name, so reaching
 * one is a work item with an address on it rather than a silence. */
static const ds_row ROWS_DSBUF[] = {
    {0,  2, DF_OUT, "QueryInterface", m_QueryInterface},
    {1,  0, 0, "AddRef", m_AddRef},
    {2,  0, 0, "Release", m_Release},
    {3,  1, DF_OUT, "GetCaps", m_BufGetCaps},
    {4,  2, DF_OUT | DF_CLK, "GetCurrentPosition", m_GetCurrentPosition},
    {5,  3, DF_OUT, "GetFormat", m_BufGetFormat},
    {6,  1, DF_OUT, "GetVolume", m_GetVolume},
    {7,  1, DF_OUT, "GetPan", m_GetPan},
    {8,  1, DF_OUT, "GetFrequency", m_GetFrequency},
    {9,  1, DF_OUT | DF_CLK, "GetStatus", m_GetStatus},
    {10, 2, 0, "Initialize", m_BufInitialize},
    {11, 7, DF_OUT, "Lock", m_Lock},
    {12, 3, 0, "Play", m_Play},
    {13, 1, 0, "SetCurrentPosition", m_SetCurrentPosition},
    {14, 1, 0, "SetFormat", m_BufSetFormat},
    {15, 1, 0, "SetVolume", m_SetVolume},
    {16, 1, 0, "SetPan", m_SetPan},
    {17, 1, 0, "SetFrequency", m_SetFrequency},
    {18, 0, 0, "Stop", m_Stop},
    {19, 4, 0, "Unlock", m_Unlock},
    {20, 0, 0, "Restore", m_Restore},
    {21, 3, DF_OUT, "SetFX", NULL},
    {22, 3, DF_OUT, "AcquireResources", NULL},
    {23, 4, DF_OUT, "GetObjectInPath", NULL},
    {0, 0, 0, NULL, NULL},
};

static ds_type g_types[] = {
    {"IDirectSound8",        ROWS_DS8,   0},
    {"IDirectSoundBuffer8",  ROWS_DSBUF, 0},
};
static uint32_t type_count(void) { return (uint32_t)(sizeof g_types / sizeof g_types[0]); }

static const ds_row *row_of(uint32_t t, uint32_t slot) {
    if (t >= type_count()) return NULL;
    for (const ds_row *p = g_types[t].rows; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

/* --------------------------------------------------------- the native */

static sa2_ds_call      *g_cur;
static sa2_ds_slot_stat *g_cur_slot;

static void bump(uint8_t answered, int by) {
    uint32_t *f;
    switch (answered) {
    case SA2_DS_ANS_REFUSED: f = &g_st.refused;    break;
    case SA2_DS_ANS_NOOP:    f = &g_st.noop;       break;
    case SA2_DS_ANS_GUEST:   f = &g_st.from_guest; break;
    case SA2_DS_ANS_INVENT:  f = &g_st.invented;   break;
    case SA2_DS_ANS_OBJECT:  f = &g_st.objects;    break;
    case SA2_DS_ANS_CLOCK:   f = &g_st.clock;      break;
    default:                 f = &g_st.unset;      break;
    }
    *f = (uint32_t)((int)*f + by);
}

/* The record is reserved *before* the handler runs and settled after it: a
 * handler that refuses raises a trap and never comes back, so a record written
 * only on the way out would leave the one call a reader most wants -- the one
 * that stopped the run -- missing from the log entirely. */
static void note_reserve(uint32_t t, uint32_t slot, uint32_t self, uint32_t caller,
                         const uint32_t *argv, uint32_t argc) {
    g_seq++;
    g_st.method_calls++;
    bump(SA2_DS_ANS_REFUSED, +1);
    g_cur_slot = NULL;
    if (t < SA2_DS_MAX_TYPES && slot < SA2_DS_MAX_SLOTS) {
        sa2_ds_slot_stat *st = &g_slot[t][slot];
        if (!st->used) {
            st->used = 1;
            st->first_seq = g_seq;
            st->first_caller = caller;
            st->answered = SA2_DS_ANS_REFUSED;
            g_st.distinct_methods++;
            g_cur_slot = st;              /* only the first call owns the class */
        }
        st->calls++;
    }
    g_cur = NULL;
    if (g_log_used >= SA2_DS_LOG_MAX) return;
    sa2_ds_call *r = &g_log[g_log_used++];
    memset(r, 0, sizeof *r);
    r->seq = g_seq;
    r->caller = caller;
    r->self = self;
    r->virtual_us = sa2_virtual_us();
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->answered = SA2_DS_ANS_REFUSED;
    r->argc = (uint8_t)(argc > 8u ? 8u : argc);
    for (uint32_t k = 0; k < r->argc; k++) r->arg[k] = argv[k];
    g_cur = r;
}

static void note_settle(uint32_t ret, const ds_ctx *c) {
    bump(SA2_DS_ANS_REFUSED, -1);
    bump(c->answered, +1);
    if (g_cur) {
        g_cur->answered = c->answered;
        g_cur->ret = ret;
        g_cur->out0 = c->out0;
        g_cur->out1 = c->out1;
    }
    if (g_cur_slot) g_cur_slot->answered = c->answered;
    g_cur = NULL;
    g_cur_slot = NULL;
}

static void *dsound_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_DS_THUNK_BASE) / SA2_DS_THUNK_STRIDE;
    const uint32_t t = idx / SA2_DS_MAX_SLOTS, slot = idx % SA2_DS_MAX_SLOTS;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    if (t >= type_count()) {
        sa2_trap_note("dsound method thunk %#010x is past the %u fabricated interfaces",
                      pc, type_count());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const ds_row *row = row_of(t, slot);
    uint32_t raw[16];
    memset(raw, 0, sizeof raw);
    /* An unnamed slot is never popped with a guessed argument count. The
     * `this` pointer is still read, because naming the object in the trap is
     * the whole value of getting here. */
    uint32_t want = row ? (uint32_t)row->argc + 1u : 1u;
    for (uint32_t k = 0; k < want && k < 16u; k++) {
        if (sa2_peek(esp + 4u + 4u * k, 4, &raw[k]) != SA2_OK) {
            sa2_trap_note("%s::%s: argument %u at %#010x is outside the guest space",
                          g_types[t].name, row ? row->name : "an unnamed slot", k,
                          esp + 4u + 4u * k);
            sa2_trap_raise(SA2_TRAP_FAULT, esp + 4u + 4u * k);
        }
    }
    const uint32_t self = raw[0];

    if (!row) {
        note_reserve(t, slot, self, caller, raw + 1, 0);
        sa2_trap_note("%s vtable slot %u called from %#010x on %#010x: this runtime does not "
                      "name that slot, and popping a guessed argument count would corrupt "
                      "the caller's frame rather than answer wrongly",
                      g_types[t].name, slot, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    if (!row->fn) {
        note_reserve(t, slot, self, caller, raw + 1, row->argc);
        sa2_trap_note("%s::%s called from %#010x on %#010x: %s", g_types[t].name, row->name,
                      caller, self,
                      (row->flags & DF_OUT)
                          ? "it writes through a pointer the guest then dereferences, and "
                            "this runtime has no honest value to put there"
                          : "this runtime has no implementation for it");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    ds_ctx c;
    memset(&c, 0, sizeof c);
    c.s = s;
    c.self = self;
    c.obj = obj_of(self);
    if (!c.obj) g_st.unknown_this++;
    else if (!c.obj->alive && c.obj->type == T_DSBUF) {
        g_st.calls_on_released++;
        c.obj->stale = 1;                    /* obj_new: never handed on */
    }
    c.b = (c.obj && c.obj->type == T_DSBUF && c.obj->buf < g_buf_used)
              ? &g_buf[c.obj->buf] : NULL;
    c.a = raw + 1;
    c.argc = row->argc;
    c.caller = caller;
    c.answered = (row->flags & DF_CAP) ? SA2_DS_ANS_INVENT
               : (row->flags & DF_CLK) ? SA2_DS_ANS_CLOCK
                                       : SA2_DS_ANS_NOOP;
    note_reserve(t, slot, self, caller, raw + 1, row->argc);
    uint32_t ret;
    if (g_break == BRK_NOOUT && (row->flags & DF_OUT)) {
        ret = SA2_DS_OK;
        c.answered = SA2_DS_ANS_NOOP;
    } else {
        ret = row->fn(&c);
    }
    note_settle(ret, &c);
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    /* The callee pops: the return address plus `this` plus the arguments. */
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * want);
    return m;
}

/* --------------------------------------------------------- the build */

static uint32_t type_vtable(uint32_t t) {
    if (g_types[t].vt) return g_types[t].vt;
    if (!g_types_bound) {
        /* Every slot of every type gets a table row, so an unnamed slot lands
         * in this runtime and refuses by name rather than jumping to a value
         * nothing registered. */
        for (uint32_t i = 0; i < type_count(); i++)
            for (uint32_t k = 0; k < SA2_DS_MAX_SLOTS; k++) {
                const uint32_t va = SA2_DS_THUNK_BASE +
                                    (i * SA2_DS_MAX_SLOTS + k) * SA2_DS_THUNK_STRIDE;
                if (sa2_ft_add(va, dsound_native, "dsound-method", SA2_FN_NATIVE) < 0) {
                    sa2_trap_note("the function table refused the dsound method thunks");
                    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
                }
            }
        g_types_bound = 1;
    }
    const uint32_t vt = SA2_DS_VT_BASE + t * SA2_DS_VT_STRIDE;
    for (uint32_t k = 0; k < SA2_DS_MAX_SLOTS; k++)
        pk32(vt + 4u * k, SA2_DS_THUNK_BASE +
                          (t * SA2_DS_MAX_SLOTS + k) * SA2_DS_THUNK_STRIDE);
    g_types[t].vt = vt;
    return vt;
}

uint32_t sa2_dsound_make_device(uint32_t caller) {
    (void)caller;
    if (!g_enabled) return 0;
    if (g_device_obj) return g_device_obj;
    ds_obj *o = obj_new(T_DS8, NULL);
    g_device_obj = o->addr;
    g_st.created++;
    return o->addr;
}

int sa2_dsound_device_enabled(void) { return g_enabled; }

void sa2_dsound_config_from_env(void) {
    const char *v = getenv("SA2_DSOUND_DEVICE");
    g_enabled = v && *v && *v != '0';
    v = getenv("SA2_DSOUND_STRICT_CAPS");
    g_strict_caps = v && *v && *v != '0';
    g_st.strict_caps = (uint32_t)g_strict_caps;
    v = getenv("SA2_DSOUND_WRITE_LEAD_US");
    if (v && *v) {
        const unsigned long n = strtoul(v, NULL, 0);
        if (n <= 1000000ul) g_write_lead_us = (uint32_t)n;
    }
    g_st.write_lead_us = g_write_lead_us;
    v = getenv("SA2_DSOUND_PRIMARY_MS");
    if (v && *v) {
        const unsigned long n = strtoul(v, NULL, 0);
        if (n && n <= 60000ul) g_primary_ms = (uint32_t)n;
    }
    v = getenv("SA2_DSOUND_BREAK");
    g_break = BRK_NONE;
    /* Restore the published count first, so config_from_env is idempotent. */
    for (ds_row *r = ROWS_DS8; r->name; r++) if (r->slot == 3u) r->argc = 3u;
    if (v && *v) {
        if      (!strcmp(v, "argc")) {
            g_break = BRK_ARGC;
            /* IDirectSound8::CreateSoundBuffer declared with two arguments
             * instead of three: the exact shape of the two defects this
             * project has paid for. The dispatcher pops the declared count, so
             * the caller's frame drifts four bytes and every later [esp+n] in
             * the sound init reads the wrong local. */
            for (ds_row *r = ROWS_DS8; r->name; r++) if (r->slot == 3u) r->argc = 2u;
        }
        else if (!strcmp(v, "frozen")) g_break = BRK_FROZEN;
        else if (!strcmp(v, "noout"))  g_break = BRK_NOOUT;
        else if (!strcmp(v, "nolead")) g_break = BRK_NOLEAD;
        else if (!strcmp(v, "noreuse")) g_break = BRK_NOREUSE;
        else if (!strcmp(v, "sharefree")) g_break = BRK_SHAREFREE;
        else if (!strcmp(v, "sharefree-noguard")) g_break = BRK_SHAREFREE_NOGUARD;
        else {
            sa2_trap_note("SA2_DSOUND_BREAK=%s is not a break this runtime has; the names "
                          "are argc, frozen, noout, nolead, noreuse, sharefree and "
                          "sharefree-noguard", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DS_THUNK_BASE);
        }
    }
}

/* ------------------------------------------------------------ readers */

void sa2_dsound_stats_get(sa2_ds_stats *out) { if (out) *out = g_st; }
uint32_t           sa2_dsound_log_count(void) { return g_log_used; }
const sa2_ds_call *sa2_dsound_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

uint32_t    sa2_dsound_type_count(void) { return type_count(); }
const char *sa2_dsound_type_name(uint32_t t) { return t < type_count() ? g_types[t].name : NULL; }
const char *sa2_dsound_method_name(uint32_t t, uint32_t slot) {
    const ds_row *r = row_of(t, slot);
    return r ? r->name : NULL;
}
int sa2_dsound_method_argc(uint32_t t, uint32_t slot) {
    const ds_row *r = row_of(t, slot);
    return r ? (int)r->argc : -1;
}
int sa2_dsound_method_implemented(uint32_t t, uint32_t slot) {
    const ds_row *r = row_of(t, slot);
    return r && r->fn ? 1 : 0;
}
const char *sa2_dsound_method_flags(uint32_t t, uint32_t slot) {
    static char buf[32];
    const ds_row *r = row_of(t, slot);
    buf[0] = 0;
    if (!r) return buf;
    if (r->flags & DF_OUT) strcat(buf, "out");
    if (r->flags & DF_CAP) { if (buf[0]) strcat(buf, ","); strcat(buf, "cap"); }
    if (r->flags & DF_CLK) { if (buf[0]) strcat(buf, ","); strcat(buf, "clock"); }
    return buf;
}
const sa2_ds_slot_stat *sa2_dsound_slot_stat_at(uint32_t t, uint32_t slot) {
    if (t >= SA2_DS_MAX_TYPES || slot >= SA2_DS_MAX_SLOTS) return NULL;
    return &g_slot[t][slot];
}

uint32_t sa2_dsound_buffer_count(void) { return g_buf_used; }
const sa2_ds_buffer *sa2_dsound_buffer_at(uint32_t i) {
    return i < g_buf_used ? &g_buf[i] : NULL;
}
int sa2_dsound_buffer_cursors(uint32_t i, uint32_t *play, uint32_t *write) {
    if (i >= g_buf_used) return 0;
    /* A *read-only* snapshot: it must not settle a halt or move an anchor,
     * because a probe that changes what it measures is not a probe. */
    sa2_ds_buffer copy = g_buf[i];
    const uint32_t p = buf_play_cursor(&copy, sa2_virtual_us(), NULL);
    if (play) *play = p;
    if (write) *write = buf_write_cursor(&copy, p);
    return 1;
}
