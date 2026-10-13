/* A recording DirectDraw, answered from the capture's device profile. See sa2_ddraw.h. */
#include "sa2_ddraw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_d3d9.h"
#include "sa2_devprofile.h"
#include "sa2_guest_call.h"
#include "sa2_trap.h"

/* ------------------------------------------------------------ the rows */

enum {
    DF_OUT     = 1u << 0,   /* writes through a pointer the guest dereferences */
    DF_PROFILE = 1u << 1,   /* answers with what the capture's device profile recorded */
};

typedef struct dd_ctx dd_ctx;
typedef uint32_t (*dd_method)(dd_ctx *c);

typedef struct {
    uint8_t     slot;
    uint8_t     argc;        /* stack dwords, NOT counting `this` */
    uint8_t     flags;
    const char *name;
    dd_method   fn;          /* NULL: named, and refused by name */
} dd_row;

typedef struct {
    const char *name;
    dd_row     *rows;
    uint32_t    vt;          /* the vtable's guest address, 0 until built */
} dd_type;

enum { T_DD = 0, T_DD7 = 1 };

typedef struct {
    uint32_t addr;
    uint16_t type;
    uint16_t inst;           /* index into g_inst */
} dd_obj;

struct dd_ctx {
    uint32_t         self, caller;
    dd_obj          *obj;
    sa2_dd_instance *in;
    const uint32_t  *a;
    uint8_t          answered;
    uint32_t         out0;
};

/* Where each string sits in an enumeration record. */
#define REC_GUID 0x000u
#define REC_DESC 0x010u
#define REC_NAME 0x114u

/* DDSCAPS_VIDEOMEMORY | DDSCAPS_LOCALVIDMEM, the capability word the image
 * writes at 0x00865DF4 and the only one it asks about. */
#define DD_CAPS_LOCAL_VIDEO 0x10004000u

/* {15E65EC0-3B9C-11D2-B92F-00609797EA5B}, as it sits in memory: the 16 bytes at
 * 0x0087354C, which 0x00865DC7 pushes as QueryInterface's riid. */
static const uint8_t IID_IDIRECTDRAW7[16] = {
    0xC0, 0x5E, 0xE6, 0x15, 0x9C, 0x3B, 0xD2, 0x11,
    0xB9, 0x2F, 0x00, 0x60, 0x97, 0x97, 0xEA, 0x5B,
};

/* --------------------------------------------------------------- state */

_Static_assert(SA2_DD_THUNK_BASE >= 0x00221000u && SA2_DD_THUNK_END <= SA2_DD_THUNK_LIMIT,
               "the ddraw thunks must sit above sa2_dsound.h's and inside their own block");
_Static_assert(SA2_DD_THUNK_LIMIT <= 0x00300000u,
               "the ddraw thunks must stay in the block this runtime keeps for invented "
               "entry points");
_Static_assert(SA2_DD_OBJ_BASE >= 0x03260400u,
               "the ddraw interfaces must sit above sa2_dsound.h's vtables");
_Static_assert(SA2_DD_OBJ_BASE + SA2_DD_MAX_OBJECTS * SA2_DD_OBJ_STRIDE <= SA2_DD_ENUM_BASE,
               "the ddraw interfaces must not run into the enumeration records");
_Static_assert(SA2_DD_ENUM_BASE + SA2_DD_MAX_DEVICES * SA2_DD_ENUM_STRIDE <= SA2_DD_OBJ_LIMIT,
               "the enumeration records must end inside the block reserved for them");
_Static_assert(REC_NAME + SA2_DD_STR_BYTES <= SA2_DD_ENUM_STRIDE,
               "a record's name must end inside the record");
_Static_assert(SA2_DD_STR_BYTES == sizeof(((sa2_dp_ddraw *)0)->description)
               && SA2_DD_STR_BYTES == sizeof(((sa2_dp_ddraw *)0)->name),
               "a record holds the profile's strings at the profile's own length");
/* The profile's readers refuse more than SA2_DP_MAX_DDRAW devices at
 * configuration, and the reader's own storage holds no more, so every profile
 * a run can load fits the records: the limit is enforced once, before the
 * guest starts, and never re-checked mid-boot. */
_Static_assert(SA2_DP_MAX_DDRAW <= SA2_DD_MAX_DEVICES,
               "the records must hold every DirectDraw device a profile's reader accepts");
_Static_assert(SA2_DD_VT_BASE >= SA2_DD_OBJ_LIMIT
               && SA2_DD_VT_BASE + SA2_DD_MAX_TYPES * SA2_DD_VT_STRIDE <= SA2_DD_VT_LIMIT,
               "the ddraw vtables must stay inside their own block");
_Static_assert(SA2_DD_VT_LIMIT <= SA2_GUEST_RT_ARENA_BASE,
               "the ddraw vtables must not run into the runtime arena");

/* Deliberate breaks, so each claim this file makes can be shown failing.
 * `SA2_DDRAW_BREAK=<name>`; harness/ddraw_probe.c runs under each.
 *
 *   argc     IDirectDraw7::GetAvailableVidMem is declared with two arguments
 *            instead of three. The dispatcher pops the declared count, so the
 *            caller's frame is four bytes out when 0x00865E11 reads dd7 back to
 *            release it: it reads dd, one slot below, and the Release it calls
 *            is IDirectDraw's, which refuses by name. It is the defect class of
 *            d3d9 at slice 6 and Steam at slice 9. The probe's stack check and
 *            `tools/s4/ddraw_surface.py --argc` both see it, because the count
 *            patched is the published one.
 *   noout    every answer with an out-parameter -- DirectDrawCreate,
 *            QueryInterface, GetAvailableVidMem -- is DD_OK with nothing
 *            written. DirectDrawCreate's is the out-pointer the image reads
 *            without testing the HRESULT: 0x00865DB0 zeroes dd before the call
 *            and 0x00865DBE dereferences it after.
 *   nomatch  the enumeration hands every entry a NULL HMONITOR, so the game's
 *            compare at 0x00865C27 never matches and DirectDrawCreate is asked
 *            for the zeroed GUID instead of the display's, which is not what
 *            native was asked (native-outcomes-v1a:13919). Under a profile
 *            with an entry on adapter 0's monitor DirectDrawCreate then refuses
 *            by name, because the zeroed context is not native's device.
 */
enum { BRK_NONE = 0, BRK_ARGC, BRK_NOOUT, BRK_NOMATCH };
static int g_break;

static sa2_dd_stats     g_st;
static sa2_dd_call      g_log[SA2_DD_LOG_MAX];
static uint32_t         g_log_used, g_seq;
static sa2_dd_slot_stat g_slot[SA2_DD_MAX_TYPES][SA2_DD_MAX_SLOTS];
static dd_obj           g_obj[SA2_DD_MAX_OBJECTS];
static uint32_t         g_obj_used;
static sa2_dd_instance  g_inst[SA2_DD_MAX_INSTANCES];
static uint32_t         g_inst_used;
static sa2_dd_enum      g_enum[SA2_DD_MAX_ENUMS];
static uint32_t         g_enum_used;
static int              g_enabled, g_types_bound;

/* The method call being answered, reserved before its handler runs. */
static sa2_dd_call      *g_cur;
static sa2_dd_slot_stat *g_cur_slot;

/* ------------------------------------------------------------ plumbing */

static void gfault(uint32_t a, const char *what) {
    sa2_trap_note("ddraw: %s at guest %#010x is outside the guest space", what, a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}
static void pk32(uint32_t a, uint32_t v) {
    if (sa2_poke(a, 4, &v) != SA2_OK) gfault(a, "a write of 4 bytes");
}
static void pkbytes(uint32_t a, const void *p, uint32_t n) {
    if (sa2_poke(a, n, p) != SA2_OK) gfault(a, "a write");
}

static void refuse(const char *iface, const char *method, uint32_t caller, const char *why)
    __attribute__((noreturn));
static void refuse(const char *iface, const char *method, uint32_t caller, const char *why) {
    sa2_trap_note("%s::%s called from %#010x: %s", iface, method, caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_THUNK_BASE);
}

/* The entry points' refusals, named the way the import log names them. */
static void refuse_entry(const char *name, uint32_t caller, const char *why)
    __attribute__((noreturn));
static void refuse_entry(const char *name, uint32_t caller, const char *why) {
    sa2_trap_note("ddraw.dll!%s called from %#010x: %s", name, caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_THUNK_BASE);
}

static void bump(uint8_t answered, int by) {
    uint32_t *f;
    switch (answered) {
    case SA2_DD_ANS_REFUSED: f = &g_st.refused;      break;
    case SA2_DD_ANS_NOOP:    f = &g_st.noop;         break;
    case SA2_DD_ANS_GUEST:   f = &g_st.from_guest;   break;
    case SA2_DD_ANS_PROFILE: f = &g_st.from_profile; break;
    default:                 f = &g_st.objects;      break;
    }
    *f = (uint32_t)((int)*f + by);
}

/* An entry point counts as refused until it answers, so one that traps is on
 * the record as refused -- the same reservation the method log makes. */
static void entry_begin(void) { g_seq++; bump(SA2_DD_ANS_REFUSED, +1); }
static void entry_end(uint8_t answered) {
    bump(SA2_DD_ANS_REFUSED, -1);
    bump(answered, +1);
}

static void require_enabled(const char *what, uint32_t caller) {
    if (g_enabled) return;
    sa2_trap_note("ddraw.dll!%s called from %#010x with SA2_DDRAW_DEVICE unset: "
                  "LoadLibraryA(\"ddraw.dll\") answers 0 then, so no GetProcAddress can "
                  "have handed this out", what, caller);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_THUNK_BASE);
}

/* ----------------------------------------------------- the device profile */

static const uint8_t ZERO_GUID[16];

/* The profile entry sixteen GUID bytes name: the entry with those bytes, or,
 * for sixteen zeros that no entry has, the primary display driver -- the
 * context the image passes when its callback matched nothing
 * (0x00865D70..0x00865D7C). -1 when none does. */
static int32_t device_named_by(const uint8_t g[16]) {
    const uint32_t count = sa2_dp_ddraw_count();
    for (uint32_t i = 0; i < count; i++) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        if (d->has_guid && !memcmp(d->guid, g, 16)) return (int32_t)i;
    }
    if (memcmp(g, ZERO_GUID, sizeof ZERO_GUID)) return -1;
    for (uint32_t i = 0; i < count; i++)
        if (!sa2_dp_ddraw_device(i)->has_guid) return (int32_t)i;
    return -1;
}

/* The sixteen bytes native's DirectDrawCreate was handed, into `bytes`, and
 * the profile entry they name. The game's callback copies the GUID of every
 * entry on adapter 0's monitor into its context, each over the one before
 * (0x00865C2D..0x00865C48), so it keeps the last such entry's; with none, the
 * context keeps its zeros. The profile's readers hold the `ddraw_create`
 * record to the same rule (runtime/sa2_devprofile.c, `chosen`), and the
 * native probe measured video memory on the object it created from those bytes
 * (harness/replay/native_device_profile.c, ddraw), so they are the one device
 * whose GetAvailableVidMem answer the profile holds. The readers refuse an
 * entry on adapter 0's monitor with no GUID, so the last such entry has one. */
static int32_t native_choice(const uint8_t **bytes) {
    const uint32_t count = sa2_dp_ddraw_count();
    for (uint32_t i = count; i-- > 0;) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        if (sa2_dp_ddraw_monitor_adapter(i) == 0 && d->has_guid) {
            *bytes = d->guid;
            return (int32_t)i;
        }
    }
    *bytes = ZERO_GUID;
    return device_named_by(ZERO_GUID);
}

/* Entry `i`'s record: its GUID, then its description and name byte for byte.
 * The native probe records the bytes its own A callback was handed
 * (harness/replay/native_device_profile.c, ddraw_device), so they are the ANSI
 * strings native passed and are handed on unconverted. */
static uint32_t write_record(uint32_t i, const sa2_dp_ddraw *d) {
    const uint32_t rec = SA2_DD_ENUM_BASE + i * SA2_DD_ENUM_STRIDE;
    if (sa2_guest_fill(rec, 0, SA2_DD_ENUM_STRIDE) != SA2_OK) gfault(rec, "a fill");
    pkbytes(rec + REC_GUID, d->has_guid ? d->guid : ZERO_GUID, 16u);
    pkbytes(rec + REC_DESC, d->description, (uint32_t)strlen(d->description) + 1u);
    pkbytes(rec + REC_NAME, d->name, (uint32_t)strlen(d->name) + 1u);
    return rec;
}

/* --------------------------------------------------------- the objects */

static uint32_t type_vtable(uint32_t t);

static dd_obj *obj_of(uint32_t addr) {
    if (addr < SA2_DD_OBJ_BASE) return NULL;
    const uint32_t i = (addr - SA2_DD_OBJ_BASE) / SA2_DD_OBJ_STRIDE;
    if (i >= g_obj_used || g_obj[i].addr != addr) return NULL;
    return &g_obj[i];
}

static dd_obj *obj_new(uint32_t t, uint32_t inst) {
    if (g_obj_used >= SA2_DD_MAX_OBJECTS) {
        sa2_trap_note("ddraw: the pool of %u DirectDraw interfaces is exhausted; the image "
                      "makes two per DirectDrawCreate and one DirectDrawCreate per device "
                      "creation (0x00866007)", SA2_DD_MAX_OBJECTS);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_OBJ_BASE);
    }
    dd_obj *o = &g_obj[g_obj_used];
    o->addr = SA2_DD_OBJ_BASE + g_obj_used * SA2_DD_OBJ_STRIDE;
    o->type = (uint16_t)t;
    o->inst = (uint16_t)inst;
    g_obj_used++;
    g_st.objects_built++;
    /* The vtable pointer, then this runtime's tag, so a stray pointer found in
     * guest memory later is identifiable by its bytes alone. */
    pk32(o->addr, type_vtable(t));
    pk32(o->addr + 4u, 0x44445257u);          /* 'DDRW' */
    pk32(o->addr + 8u, t);
    pk32(o->addr + 12u, inst);
    return o;
}

/* ------------------------------------------------- the two entry points */

/* DirectDrawCreate handed bytes other than native's: a device whose video
 * memory nobody measured, so it is refused rather than handed another
 * device's figure. The note says which entry the bytes name, by index only:
 * a GUID is host inventory. */
static void refuse_not_native(uint32_t caller, const uint8_t g[16], int32_t want,
                              const uint8_t native[16]) __attribute__((noreturn));
static void refuse_not_native(uint32_t caller, const uint8_t g[16], int32_t want,
                              const uint8_t native[16]) {
    const int32_t named = device_named_by(g);
    char why[200];
    if (named < 0)
        snprintf(why, sizeof why, "lpGUID names no DirectDraw device the profile enumerated; "
                 "the image passes the GUID its callback kept or its zeroed context "
                 "(0x00865D70..0x00865D7C)");
    else if (!memcmp(native, ZERO_GUID, sizeof ZERO_GUID))
        snprintf(why, sizeof why, "lpGUID names profile entry %d, and native's DirectDrawCreate "
                 "was handed the zeroed context (the profile's ddraw_create), the one device "
                 "whose video memory it holds", (int)named);
    else
        snprintf(why, sizeof why, "lpGUID names profile entry %d, and native's DirectDrawCreate "
                 "was handed entry %d's GUID (the profile's ddraw_create), the one device whose "
                 "video memory it holds", (int)named, (int)want);
    refuse_entry("DirectDrawCreate", caller, why);
}

uint32_t sa2_ddraw_enumerate_ex_a(void *s, const uint32_t *a, uint32_t caller) {
    const uint32_t cb = a[0], ctx = a[1], flags = a[2];
    require_enabled("DirectDrawEnumerateExA", caller);
    entry_begin();
    if (flags != 1u)
        refuse_entry("DirectDrawEnumerateExA", caller,
               "dwFlags is not DDENUM_ATTACHEDSECONDARYDEVICES (1), the only value the image "
               "pushes (0x00865D8A); the profile's device list answers that value and no "
               "other");
    if (!cb)
        refuse_entry("DirectDrawEnumerateExA", caller,
               "lpCallback is NULL; the image passes 0x00865C20 (pushed at 0x00865D91)");
    if (!ctx)
        refuse_entry("DirectDrawEnumerateExA", caller,
               "lpContext is NULL; the image passes its context's address "
               "(0x00865D8C..0x00865D90), and its callback reads through it at once "
               "(0x00865C24)");

    const uint32_t count = sa2_dp_ddraw_count();
    sa2_dd_enum *e = NULL;
    if (g_enum_used < SA2_DD_MAX_ENUMS) {
        e = &g_enum[g_enum_used++];
        memset(e, 0, sizeof *e);
        e->seq = g_seq;
        e->caller = caller;
        e->callback = cb;
        e->context = ctx;
        e->flags = flags;
        e->offered = count;
    } else {
        g_st.enums_dropped++;
    }
    g_st.enumerations++;

    /* Every record first, so each callback is pointed at memory that already
     * holds what it describes. */
    for (uint32_t i = 0; i < count; i++) write_record(i, sa2_dp_ddraw_device(i));

    for (uint32_t i = 0; i < count; i++) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        const uint32_t rec = SA2_DD_ENUM_BASE + i * SA2_DD_ENUM_STRIDE;
        const uint32_t gp = d->has_guid ? rec + REC_GUID : 0u;
        /* The profile records which Direct3D adapter's monitor each device is
         * on, and the HMONITOR handed over is the one d3d9's GetAdapterMonitor
         * gives the game for that adapter, because the game finds its display
         * by comparing the two (0x00865C27). An entry the profile records on
         * no monitor (`monitor=null`, the primary display driver's) gets NULL. */
        const int32_t k = sa2_dp_ddraw_monitor_adapter(i);
        const uint32_t hm = (k < 0 || g_break == BRK_NOMATCH)
                                ? 0u : sa2_d3d9_adapter_monitor((uint32_t)k);
        const uint32_t argv[5] = {gp, rec + REC_DESC, rec + REC_NAME, ctx, hm};
        /* The callback is guest code and could call back into a DirectDraw
         * method, which reserves a record of its own; the one in flight here is
         * saved around the call so neither overwrites the other. */
        sa2_dd_call *cur = g_cur;
        sa2_dd_slot_stat *cur_slot = g_cur_slot;
        const uint32_t r = sa2_guest_stdcall(s, cb, 5u, argv);
        g_cur = cur;
        g_cur_slot = cur_slot;
        g_st.callbacks++;
        if (e) {
            e->guid_ptr[i] = gp;
            e->hmonitor[i] = hm;
            e->ret[i] = r;
            e->callbacks++;
        }
        /* FALSE ends the enumeration, as it does natively. The image's callback
         * returns TRUE on both of its paths (0x00865C4B), so it never does. */
        if (!r) {
            if (e) e->stopped_by = i + 1u;
            break;
        }
    }
    if (e) e->hr = SA2_DD_OK;
    entry_end(SA2_DD_ANS_PROFILE);
    return SA2_DD_OK;
}

uint32_t sa2_ddraw_create(const uint32_t *a, uint32_t caller) {
    const uint32_t gp = a[0], out = a[1], outer = a[2];
    require_enabled("DirectDrawCreate", caller);
    entry_begin();
    if (outer)
        refuse_entry("DirectDrawCreate", caller,
               "pUnkOuter is not NULL; the image pushes 0 (0x00865DA4), and ddraw does not "
               "aggregate");
    if (!out)
        refuse_entry("DirectDrawCreate", caller,
               "lplpDD is NULL; the image passes the address of its own local (0x00865DA6)");
    if (gp < SA2_GUEST_NULL_GUARD)
        refuse_entry("DirectDrawCreate", caller,
               "lpGUID is NULL, DDCREATE_EMULATIONONLY or DDCREATE_HARDWAREONLY; the image "
               "passes the address of its context's GUID field (0x00865DAB)");
    uint8_t g[16];
    if (sa2_peek(gp, 16, g) != SA2_OK) gfault(gp, "a read of 16 GUID bytes");
    const uint8_t *native = NULL;
    const int32_t dev = native_choice(&native);
    if (memcmp(g, native, 16)) refuse_not_native(caller, g, dev, native);
    if (g_inst_used >= SA2_DD_MAX_INSTANCES) {
        sa2_trap_note("ddraw.dll!DirectDrawCreate from %#010x: %u DirectDraw objects exist, "
                      "and the image makes one per device creation (0x00866007)",
                      caller, SA2_DD_MAX_INSTANCES);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_OBJ_BASE);
    }
    sa2_dd_instance *in = &g_inst[g_inst_used];
    memset(in, 0, sizeof *in);
    in->seq = g_seq;
    in->caller = caller;
    in->guid_ptr = gp;
    memcpy(in->guid, g, sizeof g);
    in->device = dev;
    /* One count per interface, as ddraw keeps them: DirectDrawCreate leaves
     * IDirectDraw's at 1 (native-outcomes-v1a:13935), and the image never
     * releases it, so it is still 1 at exit (29007). */
    in->ref1 = 1u;
    in->dd = obj_new(T_DD, g_inst_used)->addr;
    g_inst_used++;
    g_st.creates++;
    if (g_break == BRK_NOOUT) {
        entry_end(SA2_DD_ANS_NOOP);
        return SA2_DD_OK;
    }
    pk32(out, in->dd);
    entry_end(SA2_DD_ANS_OBJECT);
    return SA2_DD_OK;
}

/* ------------------------------------------------------------ the methods */

/* IDirectDraw::QueryInterface, slot 0: the image's only call on IDirectDraw
 * (0x00865DCD), for IDirectDraw7. Native answers with a second interface of
 * the same object and raises that interface's own count to 1
 * (native-outcomes-v1a:13940..13943). */
static uint32_t m_QueryInterface(dd_ctx *c) {
    const uint32_t riid = c->a[0], ppv = c->a[1];
    uint8_t g[16];
    if (!ppv)
        refuse("IDirectDraw", "QueryInterface", c->caller,
               "ppvObj is NULL; the image passes the address of its own local (0x00865DC2)");
    if (riid < SA2_GUEST_NULL_GUARD || sa2_peek(riid, 16, g) != SA2_OK
        || memcmp(g, IID_IDIRECTDRAW7, sizeof g))
        refuse("IDirectDraw", "QueryInterface", c->caller,
               "riid is not IID_IDirectDraw7, the one interface the image asks for (the 16 "
               "bytes at 0x0087354C, pushed at 0x00865DC7)");
    sa2_dd_instance *in = c->in;
    if (!in->dd7) in->dd7 = obj_new(T_DD7, c->obj->inst)->addr;
    in->ref7++;
    pk32(ppv, in->dd7);
    c->out0 = in->dd7;
    c->answered = SA2_DD_ANS_OBJECT;
    return SA2_DD_OK;
}

/* IDirectDraw7::GetAvailableVidMem, slot 23 (0x00865E00 reads [vtable+0x5C]).
 *
 * The one question the image asks DirectDraw. The answer is the profile's:
 * the HRESULT and total the native probe got asking the same question of the
 * same device (sa2_dp_ddraw_vidmem_hr and _total), which DirectDrawCreate
 * makes sure this instance is. The probe asks after the game did (at the Mod
 * Loader's Init; the game's own call is native-outcomes-v1a:13944), so their
 * being native's is the prediction sa2_ddraw.h states with its falsifier. The
 * image keeps `total >> 20` at +0x48 of its device wrapper (0x00865E2B,
 * 0x00865E2F), the renderer copies it at 0x008698E6, and the renderer's slots
 * 6 (0x00868C10) and 105 (0x008684B0) read it back. A failure is handed back
 * as the failure the probe got, with nothing written: the image zeroes its own
 * local then (0x00865E09). */
static uint32_t m_GetAvailableVidMem(dd_ctx *c) {
    const uint32_t caps = c->a[0], total = c->a[1], freep = c->a[2];
    uint32_t w[4];
    if (!total)
        refuse("IDirectDraw7", "GetAvailableVidMem", c->caller,
               "lpdwTotal is NULL; the image passes the address of its own local "
               "(0x00865DDB)");
    if (freep)
        refuse("IDirectDraw7", "GetAvailableVidMem", c->caller,
               "lpdwFree is not NULL; the image pushes 0 (0x00865DD5), and the profile "
               "records no free-memory figure");
    if (caps < SA2_GUEST_NULL_GUARD || sa2_peek(caps, sizeof w, w) != SA2_OK)
        refuse("IDirectDraw7", "GetAvailableVidMem", c->caller,
               "lpDDSCaps2 does not point at a readable DDSCAPS2");
    if (w[0] != DD_CAPS_LOCAL_VIDEO || w[1] || w[2] || w[3])
        refuse("IDirectDraw7", "GetAvailableVidMem", c->caller,
               "the DDSCAPS2 is not {DDSCAPS_VIDEOMEMORY | DDSCAPS_LOCALVIDMEM, 0, 0, 0}, the "
               "one the image builds (0x00865DD7..0x00865DF4)");
    const uint32_t hr = sa2_dp_ddraw_vidmem_hr();
    c->answered = SA2_DD_ANS_PROFILE;
    c->in->vidmem_answers++;
    if (hr & 0x80000000u) return hr;
    const uint32_t v = sa2_dp_ddraw_vidmem_total();
    c->in->last_total = v;
    pk32(total, v);
    c->out0 = v;
    return hr;
}

/* IDirectDraw7::Release, slot 2 (0x00865E17 reads [vtable+8]). Native takes
 * IDirectDraw7's count to 0 (native-outcomes-v1a:13949); IDirectDraw's own
 * count is untouched, so the object lives on, as it does natively (29007). */
static uint32_t m_Release7(dd_ctx *c) {
    sa2_dd_instance *in = c->in;
    if (!in->ref7)
        refuse("IDirectDraw7", "Release", c->caller,
               "its count is already 0; the image releases dd7 once (0x00865E1B), after the "
               "one QueryInterface that referenced it (0x00865DCD)");
    in->ref7--;
    c->answered = SA2_DD_ANS_GUEST;
    return in->ref7;
}

/* ------------------------------------------------------------ the tables */

/* Every slot named with its published argument count; only the slots the
 * image reaches have a handler. Not `const`: SA2_DDRAW_BREAK=argc patches
 * GetAvailableVidMem's count in place, so the count the dispatcher pops with
 * and the count the probe publishes are the same number. */
static dd_row ROWS_DD[] = {
    {0,  2, DF_OUT, "QueryInterface", m_QueryInterface},
    {1,  0, 0,      "AddRef", NULL},
    {2,  0, 0,      "Release", NULL},
    {3,  0, 0,      "Compact", NULL},
    {4,  3, DF_OUT, "CreateClipper", NULL},
    {5,  4, DF_OUT, "CreatePalette", NULL},
    {6,  3, DF_OUT, "CreateSurface", NULL},
    {7,  2, DF_OUT, "DuplicateSurface", NULL},
    {8,  4, 0,      "EnumDisplayModes", NULL},
    {9,  4, 0,      "EnumSurfaces", NULL},
    {10, 0, 0,      "FlipToGDISurface", NULL},
    {11, 2, DF_OUT, "GetCaps", NULL},
    {12, 1, DF_OUT, "GetDisplayMode", NULL},
    {13, 2, DF_OUT, "GetFourCCCodes", NULL},
    {14, 1, DF_OUT, "GetGDISurface", NULL},
    {15, 1, DF_OUT, "GetMonitorFrequency", NULL},
    {16, 1, DF_OUT, "GetScanLine", NULL},
    {17, 1, DF_OUT, "GetVerticalBlankStatus", NULL},
    {18, 1, 0,      "Initialize", NULL},
    {19, 0, 0,      "RestoreDisplayMode", NULL},
    {20, 2, 0,      "SetCooperativeLevel", NULL},
    {21, 3, 0,      "SetDisplayMode", NULL},          /* IDirectDraw's takes three */
    {22, 2, 0,      "WaitForVerticalBlank", NULL},
    {0, 0, 0, NULL, NULL},
};

static dd_row ROWS_DD7[] = {
    {0,  2, DF_OUT, "QueryInterface", NULL},
    {1,  0, 0,      "AddRef", NULL},
    {2,  0, 0,      "Release", m_Release7},
    {3,  0, 0,      "Compact", NULL},
    {4,  3, DF_OUT, "CreateClipper", NULL},
    {5,  4, DF_OUT, "CreatePalette", NULL},
    {6,  3, DF_OUT, "CreateSurface", NULL},
    {7,  2, DF_OUT, "DuplicateSurface", NULL},
    {8,  4, 0,      "EnumDisplayModes", NULL},
    {9,  4, 0,      "EnumSurfaces", NULL},
    {10, 0, 0,      "FlipToGDISurface", NULL},
    {11, 2, DF_OUT, "GetCaps", NULL},
    {12, 1, DF_OUT, "GetDisplayMode", NULL},
    {13, 2, DF_OUT, "GetFourCCCodes", NULL},
    {14, 1, DF_OUT, "GetGDISurface", NULL},
    {15, 1, DF_OUT, "GetMonitorFrequency", NULL},
    {16, 1, DF_OUT, "GetScanLine", NULL},
    {17, 1, DF_OUT, "GetVerticalBlankStatus", NULL},
    {18, 1, 0,      "Initialize", NULL},
    {19, 0, 0,      "RestoreDisplayMode", NULL},
    {20, 2, 0,      "SetCooperativeLevel", NULL},
    {21, 5, 0,      "SetDisplayMode", NULL},          /* IDirectDraw2 on take five */
    {22, 2, 0,      "WaitForVerticalBlank", NULL},
    {23, 3, DF_OUT | DF_PROFILE, "GetAvailableVidMem", m_GetAvailableVidMem},
    {24, 2, DF_OUT, "GetSurfaceFromDC", NULL},
    {25, 0, 0,      "RestoreAllSurfaces", NULL},
    {26, 0, 0,      "TestCooperativeLevel", NULL},
    {27, 2, DF_OUT, "GetDeviceIdentifier", NULL},
    {28, 3, 0,      "StartModeTest", NULL},
    {29, 2, DF_OUT, "EvaluateMode", NULL},
    {0, 0, 0, NULL, NULL},
};

static dd_type g_types[] = {
    {"IDirectDraw",  ROWS_DD,  0},
    {"IDirectDraw7", ROWS_DD7, 0},
};
_Static_assert(sizeof g_types / sizeof g_types[0] <= SA2_DD_MAX_TYPES,
               "every interface needs a thunk block");
static uint32_t type_count(void) { return (uint32_t)(sizeof g_types / sizeof g_types[0]); }

static const dd_row *row_of(uint32_t t, uint32_t slot) {
    if (t >= type_count()) return NULL;
    for (const dd_row *p = g_types[t].rows; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

/* ------------------------------------------------------------ the native */

/* The record is reserved before the handler runs and settled after it: a
 * handler that refuses raises a trap and never comes back, and the call that
 * stopped the run is the one a reader most wants in the log. */
static void note_reserve(uint32_t t, uint32_t slot, uint32_t self, uint32_t caller,
                         const uint32_t *argv, uint32_t argc) {
    g_seq++;
    g_st.method_calls++;
    bump(SA2_DD_ANS_REFUSED, +1);
    g_cur_slot = NULL;
    if (t < SA2_DD_MAX_TYPES && slot < SA2_DD_MAX_SLOTS) {
        sa2_dd_slot_stat *st = &g_slot[t][slot];
        if (!st->used) {
            st->used = 1;
            st->first_seq = g_seq;
            st->first_caller = caller;
            st->answered = SA2_DD_ANS_REFUSED;
            g_st.distinct_methods++;
            g_cur_slot = st;              /* only the first call owns the class */
        }
        st->calls++;
    }
    g_cur = NULL;
    if (g_log_used >= SA2_DD_LOG_MAX) { g_st.log_dropped++; return; }
    sa2_dd_call *r = &g_log[g_log_used++];
    memset(r, 0, sizeof *r);
    r->seq = g_seq;
    r->caller = caller;
    r->self = self;
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->answered = SA2_DD_ANS_REFUSED;
    r->argc = (uint8_t)(argc > 4u ? 4u : argc);
    for (uint32_t k = 0; k < r->argc; k++) r->arg[k] = argv[k];
    g_cur = r;
}

static void note_settle(uint32_t ret, const dd_ctx *c) {
    bump(SA2_DD_ANS_REFUSED, -1);
    bump(c->answered, +1);
    if (g_cur) {
        g_cur->answered = c->answered;
        g_cur->ret = ret;
        g_cur->out0 = c->out0;
    }
    if (g_cur_slot) g_cur_slot->answered = c->answered;
    g_cur = NULL;
    g_cur_slot = NULL;
}

static void *ddraw_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_DD_THUNK_BASE) / SA2_DD_THUNK_STRIDE;
    const uint32_t t = idx / SA2_DD_MAX_SLOTS, slot = idx % SA2_DD_MAX_SLOTS;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    if (t >= type_count()) {
        sa2_trap_note("ddraw method thunk %#010x is past the %u fabricated interfaces",
                      pc, type_count());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const dd_row *row = row_of(t, slot);
    uint32_t raw[16];
    memset(raw, 0, sizeof raw);
    /* An unnamed slot is never popped with a guessed count; `this` is still
     * read, because naming the object in the trap is the value of getting here. */
    const uint32_t want = row ? (uint32_t)row->argc + 1u : 1u;
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
        sa2_trap_note("%s::%s called from %#010x on %#010x: the image never calls this slot "
                      "(tools/s4/ddraw_surface.py), so this runtime has no answer for it",
                      g_types[t].name, row->name, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    dd_obj *o = obj_of(self);
    if (!o || o->type != t || o->inst >= g_inst_used) {
        g_st.unknown_this++;
        note_reserve(t, slot, self, caller, raw + 1, row->argc);
        sa2_trap_note("%s::%s called from %#010x on %#010x, which is not an %s this runtime "
                      "made: the image calls only through what DirectDrawCreate and "
                      "QueryInterface hand out, so a foreign `this` is a drifted frame",
                      g_types[t].name, row->name, caller, self, g_types[t].name);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    dd_ctx c;
    memset(&c, 0, sizeof c);
    c.self = self;
    c.caller = caller;
    c.obj = o;
    c.in = &g_inst[o->inst];
    c.a = raw + 1;
    c.answered = SA2_DD_ANS_NOOP;
    note_reserve(t, slot, self, caller, raw + 1, row->argc);
    uint32_t ret;
    if (g_break == BRK_NOOUT && (row->flags & DF_OUT)) {
        ret = SA2_DD_OK;
        c.answered = SA2_DD_ANS_NOOP;
    } else {
        ret = row->fn(&c);
    }
    note_settle(ret, &c);
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    /* The callee pops: the return address plus `this` plus the arguments. */
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * want);
    return m;
}

/* ------------------------------------------------------------ the build */

static uint32_t type_vtable(uint32_t t) {
    if (g_types[t].vt) return g_types[t].vt;
    if (!g_types_bound) {
        /* Every slot of every type gets a table row, registered on the first
         * object and never in configuration, so an unnamed slot lands here and
         * refuses by name rather than jumping to a value nothing registered. */
        for (uint32_t i = 0; i < type_count(); i++)
            for (uint32_t k = 0; k < SA2_DD_MAX_SLOTS; k++) {
                const uint32_t va = SA2_DD_THUNK_BASE +
                                    (i * SA2_DD_MAX_SLOTS + k) * SA2_DD_THUNK_STRIDE;
                if (sa2_ft_add(va, ddraw_native, "ddraw-method", SA2_FN_NATIVE) < 0) {
                    sa2_trap_note("the function table refused the ddraw method thunks");
                    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_THUNK_BASE);
                }
            }
        g_types_bound = 1;
    }
    const uint32_t vt = SA2_DD_VT_BASE + t * SA2_DD_VT_STRIDE;
    for (uint32_t k = 0; k < SA2_DD_MAX_SLOTS; k++)
        pk32(vt + 4u * k, SA2_DD_THUNK_BASE +
                          (t * SA2_DD_MAX_SLOTS + k) * SA2_DD_THUNK_STRIDE);
    g_types[t].vt = vt;
    return vt;
}

int sa2_ddraw_device_enabled(void) { return g_enabled; }

void sa2_ddraw_config_from_env(void) {
    const char *v = getenv("SA2_DDRAW_DEVICE");
    g_enabled = v && *v && *v != '0';
    g_st.enabled = (uint32_t)g_enabled;
    /* A run on a capture's profile reproduces that capture's device set (the
     * user's ruling of 2026-09-23), and natively ddraw.dll loads and the
     * display query runs (native-outcomes-v1a:13825..13949). With the device
     * off LoadLibraryA("ddraw.dll") answers 0 instead, an absence standing in
     * for the capture's answers, so the run is refused here, before the guest
     * starts. The default profile with the device off stays legal: it is the
     * before measurement of turning the device on. */
    if (!g_enabled && !sa2_devprofile_is_default()) {
        sa2_trap_note("ddraw: SA2_DDRAW_DEVICE is off and the device profile %s is a capture's; "
                      "LoadLibraryA(\"ddraw.dll\") would answer 0, which native did not get: "
                      "refused, not approximated", sa2_devprofile_source());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_THUNK_BASE);
    }
    /* Restore the published count first, so this is idempotent. */
    for (dd_row *r = ROWS_DD7; r->name; r++) if (r->slot == 23u) r->argc = 3u;
    g_break = BRK_NONE;
    v = getenv("SA2_DDRAW_BREAK");
    if (v && *v) {
        if (!strcmp(v, "argc")) {
            g_break = BRK_ARGC;
            for (dd_row *r = ROWS_DD7; r->name; r++) if (r->slot == 23u) r->argc = 2u;
        }
        else if (!strcmp(v, "noout"))   g_break = BRK_NOOUT;
        else if (!strcmp(v, "nomatch")) g_break = BRK_NOMATCH;
        else {
            sa2_trap_note("SA2_DDRAW_BREAK=%s is not a break this runtime has; the names are "
                          "argc, noout and nomatch", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_DD_THUNK_BASE);
        }
    }
}

/* ------------------------------------------------------------ readers */

void sa2_ddraw_stats_get(sa2_dd_stats *out) { if (out) *out = g_st; }
const char *sa2_ddraw_break_name(void) {
    switch (g_break) {
    case BRK_ARGC:    return "argc";
    case BRK_NOOUT:   return "noout";
    case BRK_NOMATCH: return "nomatch";
    default:          return "";
    }
}
uint32_t           sa2_ddraw_log_count(void) { return g_log_used; }
const sa2_dd_call *sa2_ddraw_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }
uint32_t           sa2_ddraw_enum_count(void) { return g_enum_used; }
const sa2_dd_enum *sa2_ddraw_enum_at(uint32_t i) { return i < g_enum_used ? &g_enum[i] : NULL; }
uint32_t               sa2_ddraw_instance_count(void) { return g_inst_used; }
const sa2_dd_instance *sa2_ddraw_instance_at(uint32_t i) {
    return i < g_inst_used ? &g_inst[i] : NULL;
}

uint32_t    sa2_ddraw_type_count(void) { return type_count(); }
const char *sa2_ddraw_type_name(uint32_t t) { return t < type_count() ? g_types[t].name : NULL; }
const char *sa2_ddraw_method_name(uint32_t t, uint32_t slot) {
    const dd_row *r = row_of(t, slot);
    return r ? r->name : NULL;
}
int sa2_ddraw_method_argc(uint32_t t, uint32_t slot) {
    const dd_row *r = row_of(t, slot);
    return r ? (int)r->argc : -1;
}
int sa2_ddraw_method_implemented(uint32_t t, uint32_t slot) {
    const dd_row *r = row_of(t, slot);
    return r && r->fn ? 1 : 0;
}
const char *sa2_ddraw_method_flags(uint32_t t, uint32_t slot) {
    static char buf[16];
    const dd_row *r = row_of(t, slot);
    buf[0] = 0;
    if (!r) return buf;
    if (r->flags & DF_OUT) strcat(buf, "out");
    if (r->flags & DF_PROFILE) { if (buf[0]) strcat(buf, ","); strcat(buf, "profile"); }
    return buf;
}
const sa2_dd_slot_stat *sa2_ddraw_slot_stat_at(uint32_t t, uint32_t slot) {
    if (t >= SA2_DD_MAX_TYPES || slot >= SA2_DD_MAX_SLOTS) return NULL;
    return &g_slot[t][slot];
}
