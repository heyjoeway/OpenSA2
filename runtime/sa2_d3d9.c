/* A recording no-op Direct3D 9 device. See sa2_d3d9.h. */
#include "sa2_d3d9.h"

#include <fenv.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_d3d9_m7_list.h"
#include "sa2_imports.h"
#include "sa2_guest_call.h"
#include "sa2_threads.h"
#include "sa2_trap.h"
#include "sa2_window.h"
#if defined(_WIN32)
#include "sa2_win32.h"     /* the named pipe that is this host's FIFO */
#endif

/* ------------------------------------------------------------ the rows */

enum {
    DF_OUT  = 1u << 0,   /* writes through a pointer the guest dereferences */
    DF_CAP  = 1u << 1,   /* a capability answer this runtime fabricates */
    DF_VOID = 1u << 2,   /* returns void; the value in EAX means nothing */
};

typedef struct d3d_ctx d3d_ctx;
typedef uint32_t (*d3d_method)(d3d_ctx *c);

typedef struct {
    uint8_t     slot;
    uint8_t     argc;        /* stack dwords, NOT counting `this` */
    uint8_t     flags;
    const char *name;
    d3d_method  fn;
} d3d_row;

typedef struct {
    const char    *name;
    const d3d_row *rows;
    uint32_t       obj;      /* a singleton's guest address, 0 when per-instance */
    uint32_t       vt;       /* the vtable's guest address, 0 until built */
} d3d_type;

/* ------------------------------------------------------- object records */

/* How an object's lifetime is decided; see the backing store's comment below. */
enum {
    LIFE_OWN      = 0,   /* its own count and its own bindings decide */
    LIFE_LEVEL    = 1,   /* a texture's level surface: its container's count, its
                            container's bytes, destroyed with its container */
    LIFE_IMPLICIT = 2,   /* the device's own back buffer and depth buffer: never destroyed */
};
#define LEVELS_MAX 16u   /* a texture's level surfaces kept; 8192x8192 has 14 levels */

typedef struct {
    uint32_t addr;
    uint32_t idx;            /* the pool index: g_obj[idx] is this record, word 3 its value */
    uint16_t type;
    uint16_t alive;          /* 0 once destroyed: the true final release */
    uint32_t refs;           /* the count AddRef and Release return; a level surface's is
                                its container's, and this one stays 0 */
    uint32_t binds;          /* device state slots holding it: Direct3D's internal
                                reference, which AddRef and Release never report */
    uint32_t parent;         /* the guest address of the owner, 0 when none */
    uint8_t  life;           /* LIFE_* */
    uint8_t  level;          /* LIFE_LEVEL: the mip level it is */
    uint8_t  face;           /* LIFE_LEVEL: cube face, zero for a 2D texture */
    /* Face-major surfaces: each one's pool index + 1, 0 when not yet built.
     * Thirty-two bits, because the pool's indices run past 65535 (sa2_d3d9.h). */
    uint32_t lvl[6u * LEVELS_MAX];
    uint32_t d[10];          /* creation parameters, per type; see the creators */
    uint32_t mem, mem_len;   /* guest bytes a Lock hands back, 0 when none */
    uint32_t blk;            /* the heap block `mem` sits in; 0 when none or not the heap's */
    uint32_t handed;         /* 1 once a pointer into `mem` has been given to the guest */
    uint32_t src_idx;        /* 1-based row in g_tex, 0 when not from a blob */
    uint32_t locks;          /* LockRect calls on this object */
    /* Host-only history for SA2_D3D9_LIFE_DUMP; no guest byte depends on it. */
    uint32_t creator;        /* the guest address that called the creating method */
    uint32_t born_seq, dead_seq;   /* method sequence numbers; dead_seq 0 while alive */
    uint32_t lock_caller;    /* the guest address of the first Lock */
} d3d_obj;

struct d3d_ctx {
    void           *s;
    uint32_t        self;
    d3d_obj        *obj;
    const uint32_t *a;
    uint32_t        argc;
    uint32_t        caller;
    uint8_t         answered;
};

/* --------------------------------------------------------------- state */

/* The object table and the lock backing store live in different places and
 * must stay there. The objects are laid out in the pool's two blocks, from
 * SA2_D3D9_OBJ_BASE and then from SA2_D3D9_OBJ_OVF_BASE (sa2_d3d9.h). The
 * backing store comes from the guest heap's own allocator under
 * SA2_D3D9_OWNER (`sa2_heap_alloc_owned`): one block with a real header per
 * locked resource, which the guest cannot free.
 *
 * **It is freed at the resource's true final release** -- when neither a
 * count nor a binding holds it any longer -- and not before. That is Direct3D
 * 9's rule, and three parts of it are this file's to keep. Each is what Wine's
 * d3d9 does and what its conformance test `test_refcount`
 * (dlls/d3d9/tests/device.c), which also runs against Microsoft's runtime,
 * checks:
 *
 *  - **A level surface is part of its texture** (LIFE_LEVEL). GetSurfaceLevel
 *    hands back the same surface for the same level every time, AddRef'd, and
 *    that surface's AddRef and Release move the texture's count and return it
 *    ("the texture and surface refcount are identical"). A Lock of it is a
 *    Lock of the texture's level. It has no store and no lifetime of its own:
 *    it is destroyed with its texture.
 *  - **A binding keeps a resource alive and is not in its count.** SetTexture,
 *    SetStreamSource, SetIndices, SetRenderTarget, SetDepthStencilSurface,
 *    SetVertexShader, SetPixelShader and SetVertexDeclaration each hold a
 *    reference of the device's own until the slot is set to something else
 *    (Wine's stateblock takes the new one before dropping the old), and the
 *    count AddRef and Release return does not move ("SetTexture should not
 *    increase refcounts"). So `binds` is kept apart from `refs`, and a
 *    resource the guest has released to zero while it is still bound lives on
 *    until it is unbound.
 *  - **The device's own back buffer and depth buffer are never freed**
 *    (LIFE_IMPLICIT): "they are created with a refcount of 0" and "they are
 *    not freed if refcount reaches 0".
 *
 * The returned count is therefore Direct3D's for the resources: textures and
 * their level surfaces, surfaces, vertex and index buffers, declarations and
 * shaders. It is not Direct3D's for every object this device hands out:
 *
 *  - the device's own: a resource holds a reference on its device while its
 *    count is above zero, and this device's count leaves those out. The
 *    device holds no store, and the game's Release of it at 0x00868dc7, in its
 *    shutdown, discards the result: nothing reads EAX before the call at
 *    0x00868dcb replaces it. That no other caller reads a device's count is a
 *    reading of the image, not a check any run makes.
 *  - IDirect3D9's: Direct3DCreate9 hands back the one object on every call
 *    without counting the call (sa2_d3d9_make_d3d9), and the device takes no
 *    reference on it.
 *  - a swap chain's: GetSwapChain builds a new object on every call
 *    (m_GetSwapChain), each with a count of its own, where Direct3D hands back
 *    the device's implicit swap chain, AddRef'd.
 *  - what GetBackBuffer hands back, the device's and the swap chain's
 *    (m_GetBackBuffer, m_SwapGetBackBuffer): the current render target,
 *    AddRef'd, where Direct3D hands back the back buffer. They are one surface
 *    until the guest sets another render target; after that the count moves
 *    on the wrong surface.
 *
 * Until 2026-09-23 the store was never freed, because the model had none of
 * the three, and freeing at *that* model's count of zero could have freed
 * memory Direct3D keeps (the 2026-09-22 review). The model, not the leak, was
 * the wall. Before the first demo the game rewrites one managed texture, made
 * by D3DXCreateTexture at 0x00866a5c, frame after frame -- GetSurfaceLevel at
 * 0x008666f7, for the level its method at 0x00866680 is passed (the push is
 * from [ebp+0Ch]), LockRect at 0x0086672a, UnlockRect at 0x00866d65, then
 * Release at 0x00866d6d, whose result it discards -- and the old
 * GetSurfaceLevel built a new surface with a new store the size of the level
 * every time. Each call's address is its return address, the caller this file
 * records; 0x00866680 is the method's entry.
 * Those stores filled the guest heap arena before the first demo began.
 * Under this model that texture keeps one store, which its level surface hands
 * out every frame.
 *
 * tools/s4/d3d9_life_check.py runs the attract demo from boot under this model
 * and under each of the three lifetime breaks below, and
 * Documentation/receipts/s4-d3d9-life-v1.json holds each run's stop, rows,
 * lifetime counts and guest heap peak, and the verdicts it checks: this model
 * finishes with no call on a destroyed object, no release past zero and the
 * stage trace compared with the native capture on every native row and
 * matching it; its tick capture and stage trace equal the old model's
 * (neverfree) on every row the old one ran, and neverfree stops at the arena;
 * ownzero matches this model's tick capture and stage trace on every row it
 * ran; and keepall runs every poll this model ran, to the same stop, and
 * matches it on every row. ownzero, the old model freeing each store at its
 * own object's count of zero, is the 2026-09-22 worry run rather than argued.
 * It sees no early free, because the game writes a level surface only between
 * its Lock and its Release; what it cannot see is a resource that outlives its
 * last Release only through a binding, whose store ownzero would free while it
 * is bound. keepall, this model with nothing freed at a final release (a store
 * that grows still gives back a block never handed out, as obj_alloc says),
 * runs the whole demo where neverfree stops at the arena, which shows that the
 * level surfaces' identity is what moved the wall; freeing is what keeps level
 * loads from refilling the arena, and the two runs' heap peaks in the receipt
 * say by how much.
 *
 * Where the store came from before: `sa2_guest_alloc`, the runtime arena's 10
 * MiB bump allocator, until the game's first loading screen filled it
 * (agents-journals/s4/2026-09-22-data-dll-loaded.md); and before S4 slice 7 a
 * bump allocator laid straight over the guest heap's range, which erased the
 * guest's own objects -- the defect guest_memory.h's arena comment records.
 * The difference now is one allocator on the range, not two. */
_Static_assert(SA2_D3D9_OBJ_BASE + SA2_D3D9_OBJ_FIRST_COUNT * SA2_D3D9_OBJ_STRIDE
               <= SA2_GUEST_RT_ARENA_BASE,
               "the d3d9 object table must not run into the runtime arena");
_Static_assert(SA2_D3D9_OBJ_BASE + SA2_D3D9_OBJ_FIRST_COUNT * SA2_D3D9_OBJ_STRIDE
               <= SA2_GUEST_HEAP_BASE || SA2_D3D9_OBJ_BASE >= SA2_GUEST_HEAP_END,
               "the d3d9 object table must not sit in the guest's own heap");
/* The vtables start where the object table ends. A wideobj layout stays inside
 * the same bound, because its pool is this region's size divided by the stride
 * in force (sa2_d3d9_config_from_env), not a count of its own. */
_Static_assert(SA2_D3D9_OBJ_BASE + SA2_D3D9_OBJ_FIRST_COUNT * SA2_D3D9_OBJ_STRIDE
               <= SA2_D3D9_VT_BASE,
               "the d3d9 object table must not run into its own vtables");
/* The overflow block (sa2_d3d9.h): above the 64 KiB a null-ish pointer lands
 * in, and below the import thunks, which open the 0x00200000 block of entry
 * points every thunk region sits in (each module's own assertion holds its
 * thunks at or above the block's start, and check_handle_regions.py holds
 * every declared region apart), and so below the image. */
_Static_assert(SA2_D3D9_OBJ_OVF_BASE >= 0x00010000u && SA2_D3D9_OBJ_OVF_BASE >= SA2_GUEST_NULL_GUARD,
               "the d3d9 overflow block must stay above the low 64 KiB");
_Static_assert(SA2_D3D9_OBJ_OVF_BASE % SA2_D3D9_OBJ_STRIDE == 0u,
               "the d3d9 overflow block must start on an object boundary");
_Static_assert(SA2_D3D9_OBJ_OVF_BASE + SA2_D3D9_OBJ_OVF_COUNT * SA2_D3D9_OBJ_STRIDE
               <= SA2_GUEST_IMPORT_BASE,
               "the d3d9 overflow block must end below the import thunks");
_Static_assert(SA2_GUEST_IMPORT_BASE <= SA2_D3D9_THUNK_BASE &&
               SA2_GUEST_IMPORT_BASE <= SA2_GUEST_IMAGE_BASE,
               "the overflow block's upper bound must lie below the d3d9 thunks and the image");
_Static_assert(SA2_D3D9_POOL_OBJECTS == SA2_D3D9_OBJ_FIRST_COUNT + SA2_D3D9_OBJ_OVF_COUNT &&
               SA2_D3D9_POOL_OBJECTS >= 8u * SA2_D3D9_OBJ_FIRST_COUNT,
               "the pool is both blocks, at least eight times the first");
/* And the vtables end below the runtime arena, which nothing else holds:
 * check_handle_regions.py keeps them clear of every region
 * Documentation/handle-regions.json declares, and the arena is not one. */
_Static_assert(SA2_D3D9_VT_BASE + SA2_D3D9_MAX_TYPES * SA2_D3D9_VT_STRIDE
               <= SA2_GUEST_RT_ARENA_BASE,
               "the d3d9 vtables must not run into the runtime arena");
_Static_assert(SA2_D3D9_OBJ_STRIDE >= 16u,
               "obj_new writes four words into every object, so the stride must hold them");

/* Deliberate breaks, so every claim this file makes can be shown failing.
 * `SA2_D3D9_BREAK=<name>`. Which tool drives which:
 *
 *   argc, out, noimpl, guestheap      tools/s4/d3d9_break.py, each run against
 *                                     a control run
 *   texguess, fakepixels, texunknown  no tool of their own; their runs are
 *                                     recorded under `breaks` in
 *                                     Documentation/receipts/s4-texture-containers-v1.json,
 *                                     and fakepixels is also
 *                                     tools/s4/d3d9_life_check.py's, which
 *                                     reads the byte count a released
 *                                     texture keeps (tex_row_released)
 *   wideobj, wideobjzero, wideobjvt   tools/s4/d3d9_stride_check.py, each run
 *                                     compared with the 16-byte layout's
 *   neverfree, ownzero, keepall       tools/s4/d3d9_life_check.py, each run
 *                                     from boot through the attract demo
 *                                     beside the model's own
 *   tableanswer, tablekeep            harness/d3dx_table_probe.c, which must
 *                                     fail under each (run by
 *                                     Documentation/scripts/tests/
 *                                     test_d3dx_table_probe.py); the table's
 *                                     comment says what each breaks
 *   presentwait                       tools/s4/frame_wait_check.py, which
 *                                     runs the boot replay's lifted run with
 *                                     and without it and holds each to the
 *                                     registered native boots on the word the
 *                                     frame's wait moves (frame_wait below)
 *
 * Declared here rather than beside the other configuration because `obj_new`
 * and `obj_alloc`, near the top of the file, are among the things a break
 * changes. */
enum { BRK_NONE = 0, BRK_ARGC, BRK_OUT, BRK_NOIMPL, BRK_GUESTHEAP,
       BRK_TEXGUESS, BRK_FAKEPIXELS, BRK_TEXUNKNOWN,
       BRK_WIDEOBJ, BRK_WIDEOBJZERO, BRK_WIDEOBJVT,
       BRK_NEVERFREE, BRK_OWNZERO, BRK_KEEPALL,
       BRK_TABLEANSWER, BRK_TABLEKEEP, BRK_PRESENTWAIT };
static int        g_break;
static uint32_t   g_break_heap_next;     /* BRK_GUESTHEAP only; see obj_alloc */
/* neverfree and ownzero put back the lifetime model this file had until
 * 2026-09-23 -- a fresh surface object for every GetSurfaceLevel, each with
 * its own count and its own backing store, and no reference for a binding --
 * and differ only in whether that store is freed when the object's own count
 * reaches zero. */
static int life_old(void) { return g_break == BRK_NEVERFREE || g_break == BRK_OWNZERO; }

static sa2_d3d9_stats    g_st;
static sa2_d3d9_call     g_log[SA2_D3D9_LOG_MAX];
static uint32_t          g_log_used, g_seq;
static sa2_d3d9_slot_stat g_slot[SA2_D3D9_MAX_TYPES][SA2_D3D9_MAX_SLOTS];
/* One host record per pool index, both blocks (sa2_d3d9.h). The table is
 * zero until obj_new takes an index, so the pages behind indices no run
 * reaches are never touched. */
static d3d_obj           g_obj[SA2_D3D9_POOL_OBJECTS];
static uint32_t          g_obj_used;
/* The object layout in force: the stride, how many objects the pool then
 * holds, and what fills an object past its four words (SA2_D3D9_FILL_*).
 * At the 16-byte stride the pool is both blocks; under a wideobj break it is
 * the first block's 256 KiB over the wide stride (sa2_d3d9_config_from_env).
 *
 * The 16-byte stride is a claim that no guest code reads or writes past an
 * object's four words. There, +16 is the next object's vtable pointer: a read
 * is answered by it, or by zero until that object exists, and a write replaces
 * it, or is overwritten when obj_new builds that object. The wideobj breaks put
 * back the 64-byte stride this file had until 2026-09-23, under which words
 * 4..15 are the object's own, so tools/s4/d3d9_stride_check.py can compare a
 * run against each of them:
 *
 *   wideobj      words 4..15 hold SA2_D3D9_SENTINEL, a value no layout puts
 *                there, so a read of one that reaches compared state differs.
 *   wideobjzero  words 4..15 are left zero: the layout exactly as it was, since
 *                obj_new wrote four words then too and the guest space is an
 *                anonymous mapping whose slots this pool never reuses. It is
 *                needed beside wideobj because a read that only tests those
 *                words for zero sees the next object's non-zero vtable at
 *                stride 16 and the non-zero sentinel under wideobj -- the same
 *                branch both times -- where the layout the game ran on until
 *                2026-09-23 answered zero (the 2026-09-23 review).
 *   wideobjvt    wideobj with word 0, the vtable pointer, overwritten by the
 *                sentinel after obj_new writes it, so the first method call
 *                through any fabricated object reads its vtable from outside
 *                the guest space. It exists only so that
 *                tools/s4/d3d9_stride_check.py can show its comparison fails:
 *                a run that must differ from the 16-byte layout's.
 *
 * The layout is latched once an object exists; see sa2_d3d9_config_from_env. */
#define SA2_D3D9_WIDE_STRIDE 64u
#define SA2_D3D9_SENTINEL    0xD3D95E47u
/* At least the 16-byte stride, so the wide pool -- the first block over this
 * stride -- is never longer than g_obj[]; and whole words, because the fill
 * writes a word at a time and a partial last word would land in the next
 * object. */
_Static_assert(SA2_D3D9_WIDE_STRIDE >= SA2_D3D9_OBJ_STRIDE && SA2_D3D9_WIDE_STRIDE % 4u == 0u,
               "the wideobj stride must hold the 16-byte layout's objects in whole words");
static uint32_t          g_obj_stride = SA2_D3D9_OBJ_STRIDE;
static uint32_t          g_obj_max = SA2_D3D9_POOL_OBJECTS;
static uint32_t          g_obj_fill = SA2_D3D9_FILL_NONE;
static int               g_enabled, g_strict_caps;
static uint32_t          g_texmem  = 268435456u;   /* a named placeholder: 256 MiB */
static uint32_t          g_refresh = 60u;          /* a named placeholder: 60 Hz */
static int               g_types_bound;
static int               g_constant_table;
static int               g_strict_d3dx;

/* The device's own state, so a Get answers what a Set was given rather than a
 * number nobody chose. A Get with no matching Set answers D3D9's documented
 * default and is logged as SA2_D3D9_ANS_UNSET, which is a different claim. */
#define RS_MAX   256u
#define TSS_ST   8u
#define TSS_MAX  40u
#define SAMP_MAX 16u
#define SAMP_ST  16u
#define BLOB_MAX 96u
#define BLOB_LEN 128u
#define VSF_MAX  256u

typedef struct {
    uint8_t  used;
    uint8_t  kind;                  /* 1 transform, 2 light, 3 clipplane, 4 material,
                                       5 viewport, 6 scissor, 7 clipstatus */
    uint32_t key;
    uint32_t len;
    uint8_t  b[BLOB_LEN];
} d3d_blob;

static struct {
    uint32_t dev;                   /* the device object's guest address */
    uint32_t adapter, devtype, hfocus, behavior;
    uint32_t pp[14];                /* the presentation parameters, as passed */
    int      pp_valid;
    uint32_t rs[RS_MAX];   uint8_t rs_set[RS_MAX];
    uint32_t tss[TSS_ST][TSS_MAX]; uint8_t tss_set[TSS_ST][TSS_MAX];
    uint32_t ss[SAMP_MAX][SAMP_ST]; uint8_t ss_set[SAMP_MAX][SAMP_ST];
    uint32_t tex[SAMP_MAX];
    uint32_t stream[SAMP_MAX][3];
    uint32_t stream_freq[SAMP_MAX];
    uint32_t indices, fvf, vdecl, vshader, pshader;
    uint32_t rt[4], depth;
    uint32_t swvp, npatch, palette;
    int32_t  cursor_show;
    uint32_t vsf[VSF_MAX][4]; uint8_t vsf_set[VSF_MAX];
    uint32_t vsi[VSF_MAX][4]; uint8_t vsi_set[VSF_MAX];
    uint32_t vsb[VSF_MAX][4]; uint8_t vsb_set[VSF_MAX];
    uint32_t psf[VSF_MAX][4]; uint8_t psf_set[VSF_MAX];
    uint32_t psi[VSF_MAX][4]; uint8_t psi_set[VSF_MAX];
    uint32_t psb[VSF_MAX][4]; uint8_t psb_set[VSF_MAX];
    d3d_blob blob[BLOB_MAX];
} g_dev;

/* ------------------------------------------------------------ plumbing */

static void gfault(uint32_t a, const char *what) {
    sa2_trap_note("d3d9: %s at guest %#010x is outside the guest space", what, a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}
static void pk32(uint32_t a, uint32_t v) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_poke(a, 4, &v) != SA2_OK) gfault(a, "a write of 4 bytes");
}
static uint32_t pe32(uint32_t a) {
    uint32_t v = 0;
    if (sa2_peek(a, 4, &v) != SA2_OK) gfault(a, "a read of 4 bytes");
    return v;
}
static void pkbuf(uint32_t a, const void *p, uint32_t n) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_poke(a, n, p) != SA2_OK) gfault(a, "a bulk write");
}
static void pkzero(uint32_t a, uint32_t n) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_guest_fill(a, 0, n) != SA2_OK) gfault(a, "a bulk zero");
}
static void pkstr(uint32_t a, const char *s) {
    pkbuf(a, s, (uint32_t)strlen(s) + 1u);
}

/* ---------------------------------------------------- the lifetime dump
 *
 * SA2_D3D9_LIFE_DUMP=<host path>: a text dump of every object this device
 * built -- type, count, parent, the call that created it, the backing store it
 * holds -- and the distinct guest callers of every method, written when the
 * backing store cannot be allocated and again at process exit (<path>.exit).
 * It reads and writes host memory and a host file only, so nothing the guest
 * sees depends on it. */
#define LIFE_CALLERS 32u
typedef struct { uint32_t caller, calls; } life_caller;
static life_caller g_life_callers[SA2_D3D9_MAX_TYPES][SA2_D3D9_MAX_SLOTS][LIFE_CALLERS];
static uint32_t    g_life_overflow[SA2_D3D9_MAX_TYPES][SA2_D3D9_MAX_SLOTS];
static const char *g_life_path;
static uint32_t    g_call_caller;     /* the caller of the call in progress */
static void life_note(uint32_t t, uint32_t slot, uint32_t caller) {
    if (!g_life_path || t >= SA2_D3D9_MAX_TYPES || slot >= SA2_D3D9_MAX_SLOTS) return;
    life_caller *row = g_life_callers[t][slot];
    for (uint32_t i = 0; i < LIFE_CALLERS; i++) {
        if (row[i].caller == caller && row[i].calls) { row[i].calls++; return; }
        if (!row[i].calls) { row[i].caller = caller; row[i].calls = 1; return; }
    }
    g_life_overflow[t][slot]++;
}

static void refuse(const char *iface, const char *method, uint32_t caller, const char *why) {
    sa2_trap_note("%s::%s called from %#010x: %s", iface, method, caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
}

/* --------------------------------------------------------- the objects */

static uint32_t type_count(void);
static uint32_t g_d3d9_obj;       /* the one IDirect3D9 this runtime hands out */
/* A file-loaded texture counts the guest's locks in its own record row, and
 * the row keeps what its store held when the store is freed. The row table
 * lives with the d3dx entry point at the foot of this file. */
static void tex_row_lock(uint32_t idx1);
static void tex_row_released(const d3d_obj *o);

/* The guest address of pool index `i` in the layout in force: both blocks at
 * the 16-byte stride (sa2_d3d9_obj_addr, sa2_d3d9.h), the first block alone
 * at a wide one, whose g_obj_max keeps every index inside it. */
static uint32_t pool_addr(uint32_t i) {
    return g_obj_stride == SA2_D3D9_OBJ_STRIDE ? sa2_d3d9_obj_addr(i)
                                                : SA2_D3D9_OBJ_BASE + i * g_obj_stride;
}

/* The record of the object built at `addr`, NULL when none is: the index the
 * layout in force puts at that address, if any, and then the record's own
 * address, which an unaligned address or one past the objects built fails. */
static d3d_obj *obj_of(uint32_t addr) {
    uint32_t i;
    if (g_obj_stride == SA2_D3D9_OBJ_STRIDE) {
        const int32_t k = sa2_d3d9_obj_index(addr);
        if (k < 0) return NULL;
        i = (uint32_t)k;
    } else {
        if (addr < SA2_D3D9_OBJ_BASE) return NULL;
        i = (addr - SA2_D3D9_OBJ_BASE) / g_obj_stride;
    }
    if (i >= g_obj_used) return NULL;
    if (g_obj[i].addr != addr) return NULL;
    return &g_obj[i];
}

static uint32_t type_vtable(uint32_t t);

/* A fresh object of `t`, with word 0 pointing at that type's vtable. Objects
 * are per-instance rather than one singleton per interface, because the guest
 * keeps several textures and surfaces at once and a shared pointer would make
 * two different resources compare equal. */
static d3d_obj *obj_new(uint32_t t, uint32_t parent) {
    if (g_obj_used >= g_obj_max) {
        sa2_trap_note("d3d9: the fabricated-object pool of %u is exhausted; the guest has "
                      "created more resources than this stage's device holds",
                      g_obj_max);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_OBJ_BASE);
    }
    d3d_obj *o = &g_obj[g_obj_used];
    memset(o, 0, sizeof *o);
    o->idx = g_obj_used;
    o->addr = pool_addr(g_obj_used);
    o->type = (uint16_t)t;
    o->alive = 1;
    o->refs = 1;
    o->parent = parent;
    o->creator = g_call_caller;
    o->born_seq = g_seq;
    g_obj_used++;
    g_st.objects_built++;
    /* The object's bytes: a vtable pointer, then this runtime's own tag so a
     * stray pointer found later in guest memory is identifiable. */
    pk32(o->addr, type_vtable(t));
    pk32(o->addr + 4u, 0x44334439u);          /* 'D3D9' */
    pk32(o->addr + 8u, t);
    pk32(o->addr + 12u, o->idx);
    /* The wideobj breaks only; at the 16-byte stride the object ends here. The
     * zero fill writes nothing, which is what this function did until
     * 2026-09-23. */
    if (g_obj_fill == SA2_D3D9_FILL_SENTINEL || g_obj_fill == SA2_D3D9_FILL_VTABLE)
        for (uint32_t w = 16u; w < g_obj_stride; w += 4u)
            pk32(o->addr + w, SA2_D3D9_SENTINEL);
    if (g_obj_fill == SA2_D3D9_FILL_VTABLE)
        pk32(o->addr, SA2_D3D9_SENTINEL);         /* wideobjvt: the control that must differ */
    return o;
}

static void life_dump(const char *suffix, const char *why, uint32_t len, const d3d_obj *want);
static uint32_t gcopy(uint32_t dst, uint32_t src, uint32_t n);

/* Guest bytes a Lock can hand back. Allocated from the guest heap so what the
 * guest writes lands in guest memory and can be read afterwards. */
static uint32_t obj_alloc(d3d_obj *o, uint32_t len) {
    /* A destroyed object's bytes went back to the heap at its final release,
     * and a Lock through a pointer to it is a use after free in Direct3D too.
     * Answering it with a fresh block would hide the one event that says this
     * file's lifetime model is wrong, so it stops the run by name instead --
     * before the zero-length answer below, so an empty request is caught too. */
    if (!o->alive && !life_old()) {
        sa2_trap_note("d3d9: %s %#010x is asked for backing store by %#010x after its final "
                      "release (count %u, bindings %u, destroyed at call %u)",
                      sa2_d3d9_type_name(o->type), o->addr, g_call_caller, o->refs, o->binds,
                      o->dead_seq);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, o->addr);
    }
    if (!len) return 0;
    if (o->mem && o->mem_len >= len) return o->mem;
    /* SA2_D3D9_BREAK=guestheap puts the backing store back where it was before
     * S4 slice 7: bumped from SA2_GUEST_HEAP_BASE, on top of the guest's own
     * HeapAlloc arena. It is here so the separation the arenas now have is a
     * claim that can be shown failing -- under this break the heap walk goes
     * back to `consistent: false` and the run goes back to reading guest
     * 0x00000008 through a texture manager whose vtable pointer was zeroed. */
    uint32_t a, block = 0;
    if (g_break == BRK_GUESTHEAP) {
        a = g_break_heap_next ? g_break_heap_next : SA2_GUEST_HEAP_BASE;
        a = (a + 31u) & ~31u;
        g_break_heap_next = a + len;
        if (a + len > SA2_GUEST_HEAP_END) a = 0;
    } else {
        /* 32-byte aligned inside a zeroed block the heap hands out 8-aligned. */
        block = len + 31u > len ? sa2_heap_alloc_owned(SA2_D3D9_OWNER, len + 31u) : 0;
        a = block ? (block + 31u) & ~31u : 0;
    }
    if (!a) {
        life_dump("", "the backing store could not be allocated", len, o);
        sa2_trap_note("d3d9: the guest heap arena has no room for %u bytes of backing store "
                      "for a locked resource", len);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_OBJ_BASE);
    }
    if (!block) sa2_guest_fill(a, 0, len);     /* the heap zeroes its own blocks */
    /* A store that grows -- a lock asking for more than the resource was first
     * given, which is a texture D3DXCreateTexture or the fakepixels break
     * sized at level 0 and then locked for its chain, or a level past the
     * chain the record states -- is one store in Direct3D, so under its model
     * the bytes move with it, and the old block is freed when no pointer into
     * it was ever handed to the guest. One that was stays allocated and
     * unreferenced, as every grown block did before 2026-09-23: the guest may
     * still hold that pointer. `blk` names the block the final release frees. */
    if (o->mem && !life_old()) {
        gcopy(a, o->mem, o->mem_len < len ? o->mem_len : len);
        if (!o->handed && o->blk) {
            sa2_heap_free_owned(SA2_D3D9_OWNER, o->blk);
            g_st.guest_bytes_freed += o->mem_len;
        }
        o->handed = 0;
    }
    o->mem = a;
    o->mem_len = len;
    o->blk = block;
    if (!o->lock_caller) o->lock_caller = g_call_caller;
    g_st.guest_bytes_allocated += len;
    return a;
}

/* ------------------------------------------------------------ lifetimes
 *
 * Direct3D 9's rules, as the backing store's comment above states them and
 * cites them. AddRef, Release and the getters count through these; the three
 * places an object is born with a count of its own -- obj_new, a level surface
 * in GetSurfaceLevel, and CreateDevice's implicit surfaces -- set it there. */

/* The object whose count and bindings decide `o`'s lifetime: a level surface's
 * container, anything else itself. */
static d3d_obj *life_root(d3d_obj *o) {
    if (o && o->life == LIFE_LEVEL && !life_old()) {
        d3d_obj *t = obj_of(o->parent);
        if (!t) {
            sa2_trap_note("d3d9: level surface %#010x names container %#010x, which is not an "
                          "object this runtime built", o->addr, o->parent);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, o->addr);
        }
        return t;
    }
    return o;
}

/* The true final release: no count and no binding holds the object, so its
 * backing store goes back to the guest heap and a texture's level surfaces are
 * destroyed with it. The object's slot is not reused (the pool comment in
 * sa2_d3d9.h says why), so a stale guest pointer still names this record, and
 * the record says it is destroyed. */
static void life_destroy(d3d_obj *o) {
    o->alive = 0;
    if (!o->dead_seq) o->dead_seq = g_seq;
    g_st.objects_destroyed++;
    if (o->blk && g_break != BRK_NEVERFREE && g_break != BRK_KEEPALL) {
        tex_row_released(o);             /* what a file texture's store held, counted first */
        sa2_heap_free_owned(SA2_D3D9_OWNER, o->blk);
        g_st.guest_bytes_freed += o->mem_len;
        o->mem = 0;
        o->mem_len = 0;
        o->blk = 0;
    }
    for (uint32_t k = 0; k < 6u * LEVELS_MAX; k++) {
        if (!o->lvl[k]) continue;
        d3d_obj *s = &g_obj[o->lvl[k] - 1u];
        if (s->alive) {
            s->alive = 0;
            s->dead_seq = g_seq;
            g_st.objects_destroyed++;
        }
    }
}
static void life_settle(d3d_obj *o) {
    if (o->alive && !o->refs && !o->binds && o->life != LIFE_IMPLICIT) life_destroy(o);
}

/* The AddRef every COM getter performs on the interface it hands back. */
static void obj_ref(uint32_t addr) {
    d3d_obj *o = life_root(obj_of(addr));
    if (o) o->refs++;
}

/* A device state slot changing from what it held to `v`. Direct3D keeps a
 * bound resource alive with a reference of its own, and that reference is not
 * the one AddRef and Release report, so it is counted apart from `refs`. The
 * new binding is taken before the old one is dropped, so setting what is
 * already set never passes through zero.
 *
 * Binding a resource after its final release is a use after it, as a method
 * call on it is, so it is counted with those in calls_on_destroyed. The
 * binding is still taken and dropped as any other: the count is the host's,
 * and nothing the guest sees changes with it. */
static int g_recording;     /* between BeginStateBlock and EndStateBlock */
static void bind_set(uint32_t *slot, uint32_t v) {
    const uint32_t old = *slot;
    *slot = v;
    if (life_old() || old == v) return;
    d3d_obj *n = life_root(obj_of(v));
    if (n) {
        if (!n->alive && !g_st.calls_on_destroyed++)
            g_st.calls_on_destroyed_first_caller = g_call_caller;
        n->binds++;
    }
    /* While a state block records, Direct3D leaves the device's own state
     * alone and the block holds what it records. This device writes the Set
     * into its state all the same (it keeps no per-block state), so the
     * binding it replaces is kept rather than dropped: kept alive too long,
     * never freed too early. */
    if (g_recording) return;
    d3d_obj *o = life_root(obj_of(old));
    if (o && o->binds) {
        o->binds--;
        life_settle(o);
    }
}

/* ------------------------------------------------------------- formats */

/* Bytes per pixel, or 0 for a format this runtime does not know. A block
 * format answers through `fmt_block` instead. Refusing an unknown format is
 * the point: a wrong pitch is silent corruption of whatever the guest writes. */
static uint32_t fmt_bpp(uint32_t f) {
    switch (f) {
    case 20: return 3;                                  /* R8G8B8 */
    case 21: case 22: case 31: case 32: case 33: case 34: case 35:
    case 71: case 75: case 77: case 79: case 82: case 83:
    case 102: case 114: case 112: return 4;
    case 23: case 24: case 25: case 26: case 30: case 29:
    case 40: case 51: case 60: case 61: case 70: case 73: case 80:
    case 81: case 101: case 111: return 2;
    case 27: case 28: case 41: case 50: case 52: return 1;
    case 36: case 110: case 113: case 115: return 8;
    case 116: return 16;
    default: return 0;
    }
}
/* DXT1..DXT5 as four-character codes; the block size in bytes, or 0. */
static uint32_t fmt_block(uint32_t f) {
    if (f == 0x31545844u) return 8;                     /* DXT1 */
    if (f == 0x32545844u || f == 0x33545844u ||
        f == 0x34545844u || f == 0x35545844u) return 16;
    return 0;
}

/* ------------------------------------------------------- the blob store */

static d3d_blob *blob_find(uint8_t kind, uint32_t key, int create) {
    for (uint32_t i = 0; i < BLOB_MAX; i++)
        if (g_dev.blob[i].used && g_dev.blob[i].kind == kind && g_dev.blob[i].key == key)
            return &g_dev.blob[i];
    if (!create) return NULL;
    for (uint32_t i = 0; i < BLOB_MAX; i++)
        if (!g_dev.blob[i].used) {
            g_dev.blob[i].used = 1;
            g_dev.blob[i].kind = kind;
            g_dev.blob[i].key = key;
            g_dev.blob[i].len = 0;
            return &g_dev.blob[i];
        }
    return NULL;
}
static uint32_t blob_set(d3d_ctx *c, uint8_t kind, uint32_t key, uint32_t src, uint32_t len) {
    if (!src) return SA2_D3DERR_INVALIDCALL;
    if (len > BLOB_LEN) len = BLOB_LEN;
    d3d_blob *b = blob_find(kind, key, 1);
    if (!b) return SA2_D3D_OK;              /* recorded; the store is full, nothing else */
    if (sa2_peek(src, len, b->b) != SA2_OK) gfault(src, "a read of a state block");
    b->len = len;
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t blob_get(d3d_ctx *c, uint8_t kind, uint32_t key, uint32_t dst, uint32_t len) {
    if (!dst) return SA2_D3DERR_INVALIDCALL;
    d3d_blob *b = blob_find(kind, key, 0);
    if (b && b->len) {
        pkbuf(dst, b->b, b->len < len ? b->len : len);
        c->answered = SA2_D3D9_ANS_GUEST;
        return SA2_D3D_OK;
    }
    pkzero(dst, len);
    c->answered = SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}

/* ---------------------------------------------------- shared IUnknown */

enum {
    T_D3D9 = 0, T_DEVICE, T_TEXTURE, T_CUBETEX, T_VOLTEX, T_SURFACE, T_VOLUME,
    T_VERTEXBUFFER, T_INDEXBUFFER, T_VERTEXDECL, T_VERTEXSHADER, T_PIXELSHADER,
    T_STATEBLOCK, T_SWAPCHAIN, T_QUERY, T_CONSTANTTABLE,
};

static uint32_t m_QueryInterface(d3d_ctx *c) {
    /* An honest no: this object is fabricated and implements nothing else.
     * Writing a pointer for an interface this runtime does not have would be
     * the one answer the guest could not recover from. */
    if (c->a[1]) pk32(c->a[1], 0);
    c->answered = SA2_D3D9_ANS_NOOP;
    return SA2_E_NOINTERFACE;
}
/* AddRef and Release return the count Direct3D returns: a level surface's is
 * its container's, and a binding is not in it. */
static uint32_t m_AddRef(d3d_ctx *c) {
    if (!c->obj) return 1;
    c->answered = SA2_D3D9_ANS_GUEST;
    return ++life_root(c->obj)->refs;
}
static uint32_t m_Release(d3d_ctx *c) {
    if (!c->obj) return 0;
    c->answered = SA2_D3D9_ANS_GUEST;
    d3d_obj *o = life_root(c->obj);
    if (o->refs) o->refs--;
    else if (!g_st.releases_past_zero++)  /* an over-release: undefined in Direct3D too */
        g_st.releases_past_zero_first_caller = c->caller;
    if (life_old()) {
        /* The model as it was: an object is dead at its own count of zero, and
         * ownzero frees its store there (neverfree never does). */
        if (!o->refs && o->alive) {
            if (g_break == BRK_OWNZERO) life_destroy(o);
            else { o->alive = 0; o->dead_seq = g_seq; g_st.objects_destroyed++; }
        }
        return o->refs;
    }
    life_settle(o);
    return o->refs;
}
static uint32_t m_ok(d3d_ctx *c) { (void)c; return SA2_D3D_OK; }
static uint32_t m_zero(d3d_ctx *c) { (void)c; return 0; }

static uint32_t m_GetDevice(d3d_ctx *c) {
    if (!g_dev.dev) return SA2_D3DERR_INVALIDCALL;
    pk32(c->a[0], g_dev.dev);
    obj_ref(g_dev.dev);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
static uint32_t m_GetContainer(d3d_ctx *c) {
    const uint32_t p = c->obj && c->obj->parent ? c->obj->parent : g_dev.dev;
    if (!p) return SA2_D3DERR_INVALIDCALL;
    pk32(c->a[1], p);
    obj_ref(p);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
/* Private data is the guest's own bytes; keeping none of them and saying so is
 * honest, but a Get that answered S_OK with nothing written would not be. */
static uint32_t m_SetPrivateData(d3d_ctx *c) { (void)c; return SA2_D3D_OK; }
static uint32_t m_FreePrivateData(d3d_ctx *c) { (void)c; return SA2_D3D_OK; }
static uint32_t m_GetPrivateData(d3d_ctx *c) {
    (void)c;
    return SA2_D3DERR_NOTAVAILABLE;   /* nothing was stored, which is the truth */
}
static uint32_t m_GetPriority(d3d_ctx *c) { (void)c; return 0; }
static uint32_t m_SetPriority(d3d_ctx *c) { (void)c; return 0; }

/* ------------------------------------------------- IDirect3D9's methods */

/* The virtual display. Width and height come from the same named virtual
 * screen `GetSystemMetrics` answers from, so the geometry the guest computed
 * for its window and the mode it is told about cannot disagree. */
static void fill_display_mode(uint32_t p, uint32_t format) {
    pk32(p + 0u, sa2_window_screen_w());
    pk32(p + 4u, sa2_window_screen_h());
    pk32(p + 8u, g_refresh);
    pk32(p + 12u, format ? format : 22u);              /* D3DFMT_X8R8G8B8 */
}

static uint32_t cap_answer(d3d_ctx *c, const char *iface, const char *method, uint32_t v) {
    if (g_strict_caps)
        refuse(iface, method, c->caller,
               "SA2_D3D9_STRICT_CAPS is set and this is a capability query a device that "
               "presents nothing cannot answer from measurement");
    c->answered = SA2_D3D9_ANS_INVENT;
    return v;
}

static uint32_t m_GetAdapterCount(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "GetAdapterCount", 1u);   /* one virtual adapter */
}
static uint32_t m_GetAdapterIdentifier(d3d_ctx *c) {
    /* D3DADAPTER_IDENTIFIER9: Driver[512], Description[512], DeviceName[32],
     * DriverVersion, VendorId, DeviceId, SubSysId, Revision, GUID, WHQLLevel.
     * Deliberately not any real adapter: naming a vendor here is how a wrong
     * capability gets inherited. The one use this image makes of it is a
     * substring test for "PerfHUD" against Description, which correctly
     * fails against this string. */
    const uint32_t p = c->a[2];
    if (g_strict_caps)
        refuse("IDirect3D9", "GetAdapterIdentifier", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and every field of this struct would be invented");
    if (!p) return SA2_D3DERR_INVALIDCALL;
    pkzero(p, 1100u);
    pkstr(p + 0u,    "sa2-recomp-null.dll");
    pkstr(p + 512u,  "SA2 Recompiled recording no-op device (not a GPU)");
    pkstr(p + 1024u, "\\\\.\\DISPLAY1");
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_GetAdapterModeCount(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "GetAdapterModeCount", 1u);  /* the one virtual mode */
}
static uint32_t m_EnumAdapterModes(d3d_ctx *c) {
    if (g_strict_caps)
        refuse("IDirect3D9", "EnumAdapterModes", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and the mode list is invented");
    if (c->a[2] != 0u) return SA2_D3DERR_INVALIDCALL;
    fill_display_mode(c->a[3], c->a[1]);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_GetAdapterDisplayMode(d3d_ctx *c) {
    if (g_strict_caps)
        refuse("IDirect3D9", "GetAdapterDisplayMode", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and the refresh rate and format are invented");
    fill_display_mode(c->a[1], 22u);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_CheckDeviceType(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "CheckDeviceType", SA2_D3D_OK);
}
static uint32_t m_CheckDeviceFormat(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "CheckDeviceFormat", SA2_D3D_OK);
}
static uint32_t m_CheckDeviceMultiSampleType(d3d_ctx *c) {
    /* The one capability query with a truthful answer: this device has no
     * multisampling, so every type but D3DMULTISAMPLE_NONE is unavailable.
     * pQualityLevels is optional and is written only when it is not null. */
    const uint32_t type = c->a[4], pq = c->a[5];
    if (type != 0u) {
        if (pq) pk32(pq, 0);
        c->answered = SA2_D3D9_ANS_NOOP;
        return SA2_D3DERR_NOTAVAILABLE;
    }
    if (pq) pk32(pq, 1);
    c->answered = SA2_D3D9_ANS_NOOP;
    return SA2_D3D_OK;
}
static uint32_t m_CheckDepthStencilMatch(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "CheckDepthStencilMatch", SA2_D3D_OK);
}
static uint32_t m_CheckDeviceFormatConversion(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "CheckDeviceFormatConversion", SA2_D3D_OK);
}
uint32_t sa2_d3d9_adapter_monitor(uint32_t adapter) {
    return SA2_D3D9_HMONITOR_BASE + adapter * 0x10u;
}
static uint32_t m_GetAdapterMonitor(d3d_ctx *c) {
    return cap_answer(c, "IDirect3D9", "GetAdapterMonitor", sa2_d3d9_adapter_monitor(c->a[0]));
}
static uint32_t m_CreateDevice(d3d_ctx *c) {
    /* The argument-sanity guard, at the one place this project knows a drifted
     * frame shows up.
     *
     * `D3DDEVTYPE` has three documented values: HAL 1, REF 2, SW 3. A
     * `__stdcall` COM method whose declared argument count is one dword short
     * leaves four bytes of the caller's arguments on the caller's stack, and
     * the guest calls `CheckDeviceMultiSampleType` twice before it gets here,
     * so a wrong count there arrives as a `DeviceType` read eight bytes off --
     * a guest heap pointer, 0x02801b38, which is exactly what the S4 slice-5
     * journal records. Refusing on a DeviceType outside 1..3 turns that from a
     * number nobody looked at into a run that stops and says why.
     *
     * A counter of "method calls whose `this` is not a fabricated object" was
     * tried here first and **is not a check**: under SA2_D3D9_BREAK=argc it
     * stays at zero, because the guest loads `this` from a register rather
     * than from the drifted stack. It is still reported, as a report. */
    if (c->a[1] < 1u || c->a[1] > 3u) {
        sa2_trap_note("IDirect3D9::CreateDevice from %#010x: DeviceType is %#010x, and "
                      "D3DDEVTYPE has three values (HAL 1, REF 2, SW 3). An argument in "
                      "the guest's heap here means the caller's frame has drifted, which "
                      "is what a __stdcall COM method with a wrong declared argument "
                      "count does to it -- not a device this runtime cannot build.",
                      c->caller, c->a[1]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    const uint32_t pp = c->a[4], ppret = c->a[5];
    if (!ppret) return SA2_D3DERR_INVALIDCALL;
    /* Direct3D 9 puts the calling thread's x87 unit into single precision,
     * round to nearest, every exception masked, unless the game passes
     * D3DCREATE_FPU_PRESERVE (0x2). The game does not, and it reads the
     * result: the native control word is 0x007F from the first frame on
     * (measured through a Mod Loader probe, 2026-09-23), where the lifted
     * build kept the CRT's 0x023F -- double precision, so every x87 operation
     * after device creation rounded where native did not, and the CRT's math
     * dispatchers chose other implementations. The arithmetic is Wine's
     * wined3d `setup_fpu`, `cw = (cw & ~0xF3F) | 0x3F`, which turns the CRT's
     * word into exactly the measured 0x007F; the rounding mode is the host's
     * (runtime/remill_runtime.cpp), so it is set there too. */
    if (!(c->a[3] & 0x00000002u)) {
        sa2_rt_set_cw(c->s, (uint16_t)((sa2_rt_get_cw(c->s) & ~0x0F3Fu) | 0x003Fu));
        sa2_rt_set_host_round(FE_TONEAREST);
    }
    d3d_obj *d = obj_new(T_DEVICE, c->self);
    g_dev.dev = d->addr;
    g_dev.adapter = c->a[0];
    g_dev.devtype = c->a[1];
    g_dev.hfocus = c->a[2];
    g_dev.behavior = c->a[3];
    if (pp) {
        for (uint32_t i = 0; i < 14; i++) g_dev.pp[i] = pe32(pp + 4u * i);
        g_dev.pp_valid = 1;
    }
    /* The render target and depth buffer a real CreateDevice makes. They are
     * the device's own: Direct3D creates them with a count of zero, a Get hands
     * one out at one and its Release takes it back to zero, and zero does not
     * free them (LIFE_IMPLICIT). */
    d3d_obj *rt = obj_new(T_SURFACE, d->addr);
    rt->d[0] = g_dev.pp_valid ? g_dev.pp[0] : sa2_window_screen_w();   /* width */
    rt->d[1] = g_dev.pp_valid ? g_dev.pp[1] : sa2_window_screen_h();   /* height */
    rt->d[2] = g_dev.pp_valid ? g_dev.pp[2] : 22u;                     /* format */
    rt->d[3] = 1u;                                                      /* D3DUSAGE_RENDERTARGET */
    rt->d[4] = 0u;                                                      /* D3DPOOL_DEFAULT */
    rt->d[5] = g_dev.pp_valid ? g_dev.pp[4] : 0u;                       /* multisample type */
    if (!life_old()) { rt->life = LIFE_IMPLICIT; rt->refs = 0; }
    bind_set(&g_dev.rt[0], rt->addr);
    if (g_dev.pp_valid && g_dev.pp[9]) {                                /* EnableAutoDepthStencil */
        d3d_obj *ds = obj_new(T_SURFACE, d->addr);
        ds->d[0] = rt->d[0];
        ds->d[1] = rt->d[1];
        ds->d[2] = g_dev.pp[10];
        ds->d[3] = 2u;                                                  /* D3DUSAGE_DEPTHSTENCIL */
        ds->d[5] = g_dev.pp[4];
        if (!life_old()) { ds->life = LIFE_IMPLICIT; ds->refs = 0; }
        bind_set(&g_dev.depth, ds->addr);
    }
    pk32(ppret, d->addr);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
static uint32_t m_RegisterSoftwareDevice(d3d_ctx *c) {
    refuse("IDirect3D9", "RegisterSoftwareDevice", c->caller,
           "there is no software rasteriser behind this object to register one with");
    return 0;
}


/* ------------------------------------------------------ D3DCAPS9
 *
 * A capability structure is the most dangerous thing a no-op device can hand
 * back, because the guest *branches* on it and the branch it takes is a code
 * path S5 then has to reproduce. This image's use of it is small enough to
 * name exactly. `FUN_00865CA0` calls GetDeviceCaps into its own object at
 * +0x54 and then reads two fields:
 *
 *     00865cb7  mov eax,[esi+118h]        ; caps+0xC4  VertexShaderVersion
 *     00865cbd  cmp eax,0FFFE0101h        ; D3DVS_VERSION(1,1)
 *     00865cc4  mov [esi+44h],0           ; tier 0
 *     00865cce  cmp eax,0FFFE0200h        ; vs_2_0     -> tier 1
 *     00865cdf  cmp eax,0FFFE0300h        ; vs_3_0
 *     00865ce6  cmp [esi+120h],0FFFF0300h ; caps+0xCC  PixelShaderVersion, ps_3_0
 *     00865cf2  mov [esi+44h],2           ; tier 2
 *     00865d0f  mov [esi+44h],3           ; tier 3
 *
 * so two dwords choose the renderer tier for the whole program. That makes the
 * shader model a *named* choice rather than a filled struct, and it is
 * configurable with SA2_D3D9_SHADER_MODEL so that both sides of the dependency
 * can be moved and the guest seen to follow.
 *
 * Every other field reads zero, and zero is a claim -- "this device has no
 * such capability" -- not an absence of one. The fields this runtime sets are
 * listed one by one in `sa2_d3d9_caps_field_at`, so what was invented is an
 * enumerated list a later stage can check against a capture rather than an
 * assumption nobody wrote down. A field is added to that list only when a run
 * shows the guest reading it, which means every entry has an address behind it.
 */
#define CAPS9_BYTES 304u

typedef struct { uint16_t off; const char *name; uint32_t value; uint8_t from_guest; } caps_field;
static caps_field g_caps[64];
static uint32_t   g_caps_used;
static uint32_t   g_shader_model = 3u;    /* vs_3_0 / ps_3_0 */
static int        g_caps_maximal;

static void caps_put(uint16_t off, const char *name, uint32_t v, uint8_t from_guest) {
    for (uint32_t i = 0; i < g_caps_used; i++)
        if (g_caps[i].off == off) { g_caps[i].value = v; return; }
    if (g_caps_used >= 64) return;
    g_caps[g_caps_used].off = off;
    g_caps[g_caps_used].name = name;
    g_caps[g_caps_used].value = v;
    g_caps[g_caps_used].from_guest = from_guest;
    g_caps_used++;
}

static uint32_t shader_vs(uint32_t model) {
    switch (model) {
    case 0: return 0u;                 /* below vs_1_1: the guest picks tier 0 */
    case 1: return 0xFFFE0101u;        /* vs_1_1 */
    case 2: return 0xFFFE0200u;        /* vs_2_0 */
    default: return 0xFFFE0300u;       /* vs_3_0 */
    }
}
static uint32_t shader_ps(uint32_t model) {
    switch (model) {
    case 0: return 0u;
    case 1: return 0xFFFF0101u;
    case 2: return 0xFFFF0200u;
    default: return 0xFFFF0300u;
    }
}

static uint32_t fill_caps(d3d_ctx *c, const char *iface, uint32_t p,
                          uint32_t devtype, uint32_t adapter) {
    if (g_strict_caps)
        refuse(iface, "GetDeviceCaps", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and every field of D3DCAPS9 would be invented; "
               "the two the guest reads, VertexShaderVersion and PixelShaderVersion, choose "
               "its renderer tier");
    if (!p) return SA2_D3DERR_INVALIDCALL;
    g_caps_used = 0;
    pkzero(p, CAPS9_BYTES);
    caps_put(0x00, "DeviceType", devtype, 1);
    caps_put(0x04, "AdapterOrdinal", adapter, 1);
    caps_put(0xC4, "VertexShaderVersion", shader_vs(g_shader_model), 0);
    caps_put(0xC8, "MaxVertexShaderConst", 256u, 0);
    caps_put(0xCC, "PixelShaderVersion", shader_ps(g_shader_model), 0);
    /* SA2_D3D9_CAPS=maximal: the experiment, off by default.
     *
     * Leaving every other field zero is not neutrality -- zero is the claim
     * "this device has no such capability", and an engine that picks a
     * renderer class from `MaxSimultaneousTextures` or `TextureCaps` will pick
     * the degenerate one. This profile says the opposite thing, so the two runs
     * bracket the question "does this image's path depend on D3DCAPS9 beyond
     * the shader version?" Every field is listed by name in the run's JSON, so
     * what was invented stays an enumerated list either way. It is an
     * experiment and not a default precisely because it is invented: a
     * capability answered wrongly is a divergence with no symptom until
     * something renders. */
    if (g_caps_maximal) {
        caps_put(0x08, "Caps", 0x00020000u, 0);
        caps_put(0x0C, "Caps2", 0x60020000u, 0);
        caps_put(0x10, "Caps3", 0x00000060u, 0);
        caps_put(0x14, "PresentationIntervals", 0x8000000Fu, 0);
        caps_put(0x1C, "DevCaps", 0x000F1CD0u, 0);
        caps_put(0x20, "PrimitiveMiscCaps", 0x003FCFB2u, 0);
        caps_put(0x24, "RasterCaps", 0x0F3F2181u, 0);
        caps_put(0x28, "ZCmpCaps", 0x000000FFu, 0);
        caps_put(0x2C, "SrcBlendCaps", 0x00003FFFu, 0);
        caps_put(0x30, "DestBlendCaps", 0x00003FFFu, 0);
        caps_put(0x34, "AlphaCmpCaps", 0x000000FFu, 0);
        caps_put(0x38, "ShadeCaps", 0x0008FFC0u, 0);
        /* POW2 (0x2) and SQUAREONLY (0x20) are *restrictions* and stay clear. */
        caps_put(0x3C, "TextureCaps", 0x0003BF01u, 0);
        caps_put(0x40, "TextureFilterCaps", 0x03030700u, 0);
        caps_put(0x44, "CubeTextureFilterCaps", 0x03030700u, 0);
        caps_put(0x48, "VolumeTextureFilterCaps", 0x03030700u, 0);
        caps_put(0x4C, "TextureAddressCaps", 0x0000003Fu, 0);
        caps_put(0x50, "VolumeTextureAddressCaps", 0x0000003Fu, 0);
        caps_put(0x54, "LineCaps", 0x0000001Fu, 0);
        caps_put(0x58, "MaxTextureWidth", 8192u, 0);
        caps_put(0x5C, "MaxTextureHeight", 8192u, 0);
        caps_put(0x60, "MaxVolumeExtent", 8192u, 0);
        caps_put(0x64, "MaxTextureRepeat", 8192u, 0);
        caps_put(0x68, "MaxTextureAspectRatio", 8192u, 0);
        caps_put(0x6C, "MaxAnisotropy", 16u, 0);
        caps_put(0x88, "StencilCaps", 0x000001FFu, 0);
        caps_put(0x8C, "FVFCaps", 0x00000008u, 0);
        caps_put(0x90, "TextureOpCaps", 0x03FFFFFFu, 0);
        caps_put(0x94, "MaxTextureBlendStages", 8u, 0);
        caps_put(0x98, "MaxSimultaneousTextures", 8u, 0);
        caps_put(0x9C, "VertexProcessingCaps", 0x0000003Bu, 0);
        caps_put(0xA0, "MaxActiveLights", 8u, 0);
        caps_put(0xA4, "MaxUserClipPlanes", 6u, 0);
        caps_put(0xA8, "MaxVertexBlendMatrices", 4u, 0);
        caps_put(0xAC, "MaxVertexBlendMatrixIndex", 8u, 0);
        caps_put(0xB0, "MaxPointSize", 0x43800000u, 0);      /* 256.0f */
        caps_put(0xB4, "MaxPrimitiveCount", 0x000FFFFFu, 0);
        caps_put(0xB8, "MaxVertexIndex", 0x00FFFFFFu, 0);
        caps_put(0xBC, "MaxStreams", 16u, 0);
        caps_put(0xC0, "MaxStreamStride", 256u, 0);
        caps_put(0xD0, "PixelShader1xMaxValue", 0x41000000u, 0);   /* 8.0f */
        caps_put(0xD4, "DevCaps2", 0x00000051u, 0);
        caps_put(0xEC, "DeclTypes", 0x000003FFu, 0);
        caps_put(0xF0, "NumSimultaneousRTs", 4u, 0);
        caps_put(0xF4, "StretchRectFilterCaps", 0x03000300u, 0);
    }
    for (uint32_t i = 0; i < g_caps_used; i++) pk32(p + g_caps[i].off, g_caps[i].value);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_D3D9GetDeviceCaps(d3d_ctx *c) {
    return fill_caps(c, "IDirect3D9", c->a[2], c->a[1], c->a[0]);
}
static uint32_t m_DevGetDeviceCaps(d3d_ctx *c) {
    return fill_caps(c, "IDirect3DDevice9", c->a[0], g_dev.devtype, g_dev.adapter);
}

static const d3d_row ROWS_D3D9[] = {
    {0,  2, 0, "QueryInterface", m_QueryInterface},
    {1,  0, 0, "AddRef", m_AddRef},
    {2,  0, 0, "Release", m_Release},
    {3,  1, 0, "RegisterSoftwareDevice", m_RegisterSoftwareDevice},
    {4,  0, DF_CAP, "GetAdapterCount", m_GetAdapterCount},
    {5,  3, DF_OUT | DF_CAP, "GetAdapterIdentifier", m_GetAdapterIdentifier},
    {6,  2, DF_CAP, "GetAdapterModeCount", m_GetAdapterModeCount},
    {7,  4, DF_OUT | DF_CAP, "EnumAdapterModes", m_EnumAdapterModes},
    {8,  2, DF_OUT | DF_CAP, "GetAdapterDisplayMode", m_GetAdapterDisplayMode},
    {9,  5, DF_CAP, "CheckDeviceType", m_CheckDeviceType},
    {10, 6, DF_CAP, "CheckDeviceFormat", m_CheckDeviceFormat},
    {11, 6, DF_OUT, "CheckDeviceMultiSampleType", m_CheckDeviceMultiSampleType},
    {12, 5, DF_CAP, "CheckDepthStencilMatch", m_CheckDepthStencilMatch},
    {13, 4, DF_CAP, "CheckDeviceFormatConversion", m_CheckDeviceFormatConversion},
    {14, 3, DF_OUT | DF_CAP, "GetDeviceCaps", m_D3D9GetDeviceCaps},
    {15, 1, DF_CAP, "GetAdapterMonitor", m_GetAdapterMonitor},
    {16, 6, DF_OUT, "CreateDevice", m_CreateDevice},
    {0, 0, 0, NULL, NULL},
};

/* ------------------------------------------- IDirect3DDevice9's methods */

static uint32_t m_TestCooperativeLevel(d3d_ctx *c) {
    c->answered = SA2_D3D9_ANS_NOOP;     /* this device never loses; that is true of it */
    return SA2_D3D_OK;
}
static uint32_t m_GetAvailableTextureMem(d3d_ctx *c) {
    return cap_answer(c, "IDirect3DDevice9", "GetAvailableTextureMem", g_texmem);
}
static uint32_t m_GetDirect3D(d3d_ctx *c) {
    const uint32_t p = g_d3d9_obj;
    if (!p) return SA2_D3DERR_INVALIDCALL;
    pk32(c->a[0], p);
    obj_ref(p);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
static uint32_t m_GetDisplayMode(d3d_ctx *c) {
    if (g_strict_caps)
        refuse("IDirect3DDevice9", "GetDisplayMode", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and the refresh rate and format are invented");
    fill_display_mode(c->a[1], 22u);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_GetCreationParameters(d3d_ctx *c) {
    /* Not invented: these are the four values the guest itself passed to
     * CreateDevice, handed straight back. */
    const uint32_t p = c->a[0];
    pk32(p + 0u, g_dev.adapter);
    pk32(p + 4u, g_dev.devtype);
    pk32(p + 8u, g_dev.hfocus);
    pk32(p + 12u, g_dev.behavior);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_ShowCursor(d3d_ctx *c) {
    const int32_t was = g_dev.cursor_show;
    g_dev.cursor_show = (int32_t)c->a[0];
    c->answered = SA2_D3D9_ANS_GUEST;
    return (uint32_t)was;
}
static uint32_t m_GetNumberOfSwapChains(d3d_ctx *c) {
    c->answered = SA2_D3D9_ANS_GUEST;   /* one device, one implicit swap chain */
    return 1u;
}
static uint32_t m_GetSwapChain(d3d_ctx *c) {
    if (c->a[0] != 0u) return SA2_D3DERR_INVALIDCALL;
    d3d_obj *o = obj_new(T_SWAPCHAIN, g_dev.dev);
    pk32(c->a[1], o->addr);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
static uint32_t m_Reset(d3d_ctx *c) {
    if (c->a[0]) {
        for (uint32_t i = 0; i < 14; i++) g_dev.pp[i] = pe32(c->a[0] + 4u * i);
        g_dev.pp_valid = 1;
        c->answered = SA2_D3D9_ANS_GUEST;
    }
    return SA2_D3D_OK;
}
/* The frame's wait: the frame boundary (sa2_threads.h), a scheduling point.
 * Without one the guest's loading loop holds the guest lock forever and the
 * threads it started never run an instruction.
 *
 * **It is taken at BeginScene, where the native oracle waits.** Under the
 * adopted configuration (option A, Documentation/decisions/2026-09-24-S4-110h-review.md)
 * the native game's pace is the Mod Loader's frame limiter, which runs in its
 * BeginScene wrapper after the device's own BeginScene: at least 1/60 s from
 * one BeginScene to the next, "the wait sits at the frame's start"
 * (Documentation/decisions/2026-09-23-S4-vanilla-oracle.md). The game's
 * Present is restored there. Whether the native Present itself waits under
 * option A is not measured: the one timing taken fits a Present that does not
 * wait as well as one paced by a vsync faster than the limiter, and either way
 * the limiter's wait at BeginScene sets the pace. GameLoop calls BeginScene before the frame's
 * game logic and Present after it, so where the wait sits decides whether
 * CRI's 60 Hz server threads (ADXM's vsync thread, which resumes Sofdec's idle
 * server, and its file-system thread) run after a frame's logic or before the
 * next frame's: in the lifted boot, with the wait at Present, Sofdec's first
 * playback handle was served inside the tick that created it (0x01d0c6b8 set
 * at tick 379) where every one of the 13 native boots holds it set from the
 * tick-380 sample (private/generated/s4/tick379/evidence/word-1d0c6b8-follow-boot-fix.json).
 *
 * A Present that no BeginScene came before since the last Present still waits,
 * so a loop that presents without drawing a scene keeps time moving; natively
 * such a loop would not be paced at all, which a virtual clock cannot copy.
 * SA2_D3D9_BREAK=presentwait takes every wait at Present, as before. */
static int g_scene_since_present;   /* a BeginScene since the last Present */
static void frame_wait(d3d_ctx *c, const char *where) {
    sa2_sched_frame_boundary();
    if (sa2_sched_budget_hit()) {
        sa2_trap_note("%s: the guest has rendered %llu frames and the virtual clock has "
                      "reached the %llu ms budget. This is the frame loop running, not a "
                      "wall; SA2_VIRTUAL_BUDGET_MS moves the bound.", where,
                      (unsigned long long)sa2_sched_frames(),
                      (unsigned long long)(sa2_sched_budget_us() / 1000ull));
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->caller);
    }
}
static uint32_t m_Present(d3d_ctx *c) {
    g_st.present_calls++;
    g_st.last_present_seq = g_seq;       /* this call's number, set by note_reserve */
    c->answered = SA2_D3D9_ANS_NOOP;
    const int scene = g_scene_since_present;
    g_scene_since_present = 0;
    if (scene && g_break != BRK_PRESENTWAIT) return SA2_D3D_OK;
    g_st.frame_waits_present++;
    frame_wait(c, "Present");
    return SA2_D3D_OK;
}
static uint32_t m_GetBackBuffer(d3d_ctx *c) {
    if (!g_dev.rt[0]) return SA2_D3DERR_INVALIDCALL;
    pk32(c->a[3], g_dev.rt[0]);
    obj_ref(g_dev.rt[0]);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
static uint32_t m_GetRasterStatus(d3d_ctx *c) {
    if (g_strict_caps)
        refuse("IDirect3DDevice9", "GetRasterStatus", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and there is no scan-out to report");
    pk32(c->a[1] + 0u, 0);     /* InVBlank: this device never is */
    pk32(c->a[1] + 4u, 0);     /* ScanLine */
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_GetGammaRamp(d3d_ctx *c) {
    /* 3 x 256 WORDs. An identity ramp, invented: no ramp was ever set. */
    if (g_strict_caps)
        refuse("IDirect3DDevice9", "GetGammaRamp", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and the identity ramp is invented");
    uint16_t ramp[768];
    for (uint32_t i = 0; i < 256; i++)
        ramp[i] = ramp[256 + i] = ramp[512 + i] = (uint16_t)(i * 257u);
    pkbuf(c->a[1], ramp, sizeof ramp);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}

/* --- resource creation.  d[0..] is the creation call, kept for GetDesc. --- */

static uint32_t create_into(d3d_ctx *c, uint32_t t, uint32_t out, const uint32_t *d,
                            uint32_t n, uint32_t bytes) {
    if (!out) return SA2_D3DERR_INVALIDCALL;
    d3d_obj *o = obj_new(t, g_dev.dev);
    for (uint32_t i = 0; i < n && i < 10; i++) o->d[i] = d[i];
    if (bytes) obj_alloc(o, bytes);
    pk32(out, o->addr);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}

static uint32_t surface_bytes(uint32_t w, uint32_t h, uint32_t fmt) {
    const uint32_t blk = fmt_block(fmt);
    if (blk) return ((w + 3u) / 4u) * ((h + 3u) / 4u) * blk;
    const uint32_t bpp = fmt_bpp(fmt);
    if (!bpp) return 0;
    return w * bpp * h;
}

static uint32_t level_dim(uint32_t v, uint32_t level) {
    while (level-- && v > 1u) v >>= 1;
    return v ? v : 1u;
}

/* A mip chain's bytes, and where one level starts inside it.
 *
 * Every level used to alias level 0: the backing store was sized for level 0
 * alone and `obj_alloc` hands the same address back for every lock, so a guest
 * that filled level 1 overwrote the top rows of level 0. Levels now get their
 * own bytes, laid out largest first, which is also what makes a complete chain
 * safe to report from `GetLevelCount`. */
static uint32_t chain_bytes(uint32_t w, uint32_t h, uint32_t fmt, uint32_t levels) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < (levels ? levels : 1u); i++)
        n += surface_bytes(level_dim(w, i), level_dim(h, i), fmt);
    return n;
}
static uint32_t chain_offset(uint32_t w, uint32_t h, uint32_t fmt, uint32_t level) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < level; i++)
        n += surface_bytes(level_dim(w, i), level_dim(h, i), fmt);
    return n;
}
/* The number of levels in a complete chain, which is what D3DX builds when the
 * caller passes D3DX_DEFAULT or 0. */
static uint32_t full_chain(uint32_t w, uint32_t h) {
    uint32_t n = 1;
    while (w > 1u || h > 1u) { if (w > 1u) w >>= 1; if (h > 1u) h >>= 1; n++; }
    return n;
}
/* The levels a texture's bytes are laid out for: its recorded count, bounded
 * by the complete chain. The bound matters because a record can exceed the
 * chain: a count the guest passes explicitly is recorded as given, and until
 * 2026-09-24 `sa2_d3d9_d3dx_create_texture` recorded a MipLevels of
 * D3DX_DEFAULT as it came, 0xFFFFFFFF, and a chain summed that far takes four
 * billion steps and wraps its byte count. What GetLevelCount answers is the
 * record, unchanged. */
static uint32_t tex_chain_levels(const d3d_obj *o) {
    const uint32_t full = full_chain(o->d[0], o->d[1]);
    const uint32_t n = o->d[2] ? o->d[2] : 1u;
    return n < full ? n : full;
}
/* How many bytes a Lock of any level reserves: the whole chain, taken on the
 * first lock (m_TexLockRect's comment says why). The old model sized it by the
 * record as it stands, and neverfree and ownzero still do; the record held
 * D3DX_DEFAULT for a 2D D3DX texture until 2026-09-24, and holds the chain
 * under every model since (d3dx_levels). */
static uint32_t tex_reserve(const d3d_obj *o) {
    return chain_bytes(o->d[0], o->d[1], o->d[4], life_old() ? o->d[2] : tex_chain_levels(o));
}


/* Cube storage has six face-major chains. Stored mips and exposed mips differ
 * for AUTOGEN: the latter is one, without claiming this device generated pixels. */
static uint32_t cube_stored_levels(const d3d_obj *o) {
    const uint32_t full = full_chain(o->d[0], o->d[1]);
    const uint32_t n = o->d[2];
    return (o->d[3] & 0x400u) || !n || n > full ? full : n;
}
static uint32_t cube_exposed_levels(const d3d_obj *o) {
    return o->d[3] & 0x400u ? 1u : cube_stored_levels(o);
}
typedef struct { uint32_t reserve, offset, bytes; } cube_span;

/* Every factor is widened before multiplication. A row/level/chain that
 * cannot fit the guest store refuses before any object or allocation changes;
 * this is a runtime capacity boundary, not a guessed native HRESULT. */
static cube_span cube_storage(d3d_obj *o, uint32_t face, uint32_t level, uint32_t caller) {
    const uint32_t levels = cube_stored_levels(o);
    const uint32_t block = fmt_block(o->d[4]), bpp = fmt_bpp(o->d[4]);
    if (!o->d[0] || !o->d[1] || (!block && !bpp))
        refuse("IDirect3DCubeTexture9", "surface storage", caller,
               "cube dimensions or format have no supported byte layout");
    if (face >= 6u || level >= levels)
        refuse("IDirect3DCubeTexture9", "surface storage", caller,
               "cube face or stored mip level is outside its resource");
    uint64_t chain = 0, offset = 0, bytes = 0;
    for (uint32_t i = 0; i < levels; i++) {
        const uint64_t w = level_dim(o->d[0], i), h = level_dim(o->d[1], i);
        const uint64_t pitch = block ? ((w + 3u) / 4u) * block : w * bpp;
        if (pitch > UINT32_MAX)
            refuse("IDirect3DCubeTexture9", "surface storage", caller,
                   "cube pitch is not representable in the guest store");
        const uint64_t n = pitch * (block ? (h + 3u) / 4u : h);
        if (n > UINT32_MAX || chain + n > UINT32_MAX)
            refuse("IDirect3DCubeTexture9", "surface storage", caller,
                   "cube mip chain is not representable in the guest store");
        if (i == level) { offset = chain; bytes = n; }
        chain += n;
    }
    const uint64_t reserve = 6u * chain;
    offset += (uint64_t)face * chain;
    if (reserve > UINT32_MAX - 31u || offset > reserve || bytes > reserve - offset)
        refuse("IDirect3DCubeTexture9", "surface storage", caller,
               "six-face cube storage is not representable in the guest store");
    return (cube_span){(uint32_t)reserve, (uint32_t)offset, (uint32_t)bytes};
}

static uint32_t m_CreateTexture(d3d_ctx *c) {
    const uint32_t d[6] = {c->a[0], c->a[1], c->a[2] ? c->a[2] : 1u, c->a[3], c->a[4], c->a[5]};
    return create_into(c, T_TEXTURE, c->a[6], d, 6, chain_bytes(d[0], d[1], d[4], d[2]));
}
static uint32_t m_CreateCubeTexture(d3d_ctx *c) {
    if (!c->a[5]) return SA2_D3DERR_INVALIDCALL;
    d3d_obj shape = {.d = {c->a[0], c->a[0], c->a[1], c->a[2], c->a[3], c->a[4]}};
    shape.d[2] = cube_stored_levels(&shape);
    const cube_span span = cube_storage(&shape, 0u, 0u, c->caller);
    return create_into(c, T_CUBETEX, c->a[5], shape.d, 6, span.bytes);
}
/* CreateVolumeTexture(Width, Height, Depth, Levels, Usage, Format, Pool, pp).
 *
 * The record keeps the *same six words in the same order* as every other
 * texture -- width, height, levels, usage, format, pool -- and puts the volume's
 * third dimension in d[6], rather than storing the creation call verbatim. It
 * used to store it verbatim, which put Depth in d[2]; `m_GetLevelCount` reads
 * d[2], so a volume texture answered its depth when the guest asked how many
 * mip levels it had, and `m_TexGetLevelDesc`'s `level_dim` walk was one field
 * out as well. Nothing observed that, because until this slice no volume
 * texture could be created at all: both D3DX volume creators raised a trap.
 * Extending the shared record by one word is what rule 1 of the d3dx work asks
 * for -- one table, one layout -- and it is why the cube texture needed no
 * change: a cube's Size is already stored as width *and* height. */
static uint32_t m_CreateVolumeTexture(d3d_ctx *c) {
    const uint32_t d[7] = {c->a[0], c->a[1], c->a[3] ? c->a[3] : 1u,
                           c->a[4], c->a[5], c->a[6], c->a[2] ? c->a[2] : 1u};
    return create_into(c, T_VOLTEX, c->a[7], d, 7,
                       surface_bytes(d[0], d[1], d[4]) * d[6]);
}
static uint32_t m_CreateVertexBuffer(d3d_ctx *c) {
    const uint32_t d[5] = {c->a[0], c->a[1], c->a[2], c->a[3], 0};
    return create_into(c, T_VERTEXBUFFER, c->a[4], d, 5, c->a[0]);
}
static uint32_t m_CreateIndexBuffer(d3d_ctx *c) {
    const uint32_t d[5] = {c->a[0], c->a[1], c->a[2], c->a[3], 0};
    return create_into(c, T_INDEXBUFFER, c->a[4], d, 5, c->a[0]);
}
/* Every surface record is w, h, format, usage, pool, multisample, whichever
 * creator made it, so `GetDesc` reads one layout rather than four. */
static uint32_t m_CreateRenderTarget(d3d_ctx *c) {
    const uint32_t d[6] = {c->a[0], c->a[1], c->a[2], 1u, 0u, c->a[3]};
    return create_into(c, T_SURFACE, c->a[6], d, 6, surface_bytes(d[0], d[1], d[2]));
}
static uint32_t m_CreateDepthStencilSurface(d3d_ctx *c) {
    const uint32_t d[6] = {c->a[0], c->a[1], c->a[2], 2u, 0u, c->a[3]};
    return create_into(c, T_SURFACE, c->a[6], d, 6, surface_bytes(d[0], d[1], d[2]));
}
static uint32_t m_CreateOffscreenPlainSurface(d3d_ctx *c) {
    const uint32_t d[6] = {c->a[0], c->a[1], c->a[2], 0u, c->a[3], 0u};
    return create_into(c, T_SURFACE, c->a[4], d, 6, surface_bytes(d[0], d[1], d[2]));
}
static uint32_t m_CreateVertexDeclaration(d3d_ctx *c) {
    const uint32_t d[1] = {c->a[0]};
    return create_into(c, T_VERTEXDECL, c->a[1], d, 1, 0);
}
static uint32_t m_CreateVertexShader(d3d_ctx *c) {
    const uint32_t d[1] = {c->a[0]};
    return create_into(c, T_VERTEXSHADER, c->a[1], d, 1, 0);
}
static uint32_t m_CreatePixelShader(d3d_ctx *c) {
    const uint32_t d[1] = {c->a[0]};
    return create_into(c, T_PIXELSHADER, c->a[1], d, 1, 0);
}
static uint32_t m_CreateStateBlock(d3d_ctx *c) {
    const uint32_t d[1] = {c->a[0]};
    return create_into(c, T_STATEBLOCK, c->a[1], d, 1, 0);
}
static uint32_t m_BeginStateBlock(d3d_ctx *c) {
    g_recording = 1;
    (void)c;
    return SA2_D3D_OK;
}
static uint32_t m_EndStateBlock(d3d_ctx *c) {
    g_recording = 0;
    const uint32_t d[1] = {0};
    return create_into(c, T_STATEBLOCK, c->a[0], d, 1, 0);
}
static uint32_t m_CreateQuery(d3d_ctx *c) {
    const uint32_t d[1] = {c->a[0]};
    return create_into(c, T_QUERY, c->a[1], d, 1, 0);
}
static uint32_t m_CreateAdditionalSwapChain(d3d_ctx *c) {
    const uint32_t d[1] = {c->a[0]};
    return create_into(c, T_SWAPCHAIN, c->a[1], d, 1, 0);
}

/* --- render targets, scene, draw --- */

static uint32_t m_SetRenderTarget(d3d_ctx *c) {
    if (c->a[0] < 4) bind_set(&g_dev.rt[c->a[0]], c->a[1]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetRenderTarget(d3d_ctx *c) {
    const uint32_t i = c->a[0];
    if (i >= 4 || !g_dev.rt[i]) return SA2_D3DERR_NOTAVAILABLE;
    pk32(c->a[1], g_dev.rt[i]);
    obj_ref(g_dev.rt[i]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_SetDepthStencilSurface(d3d_ctx *c) {
    bind_set(&g_dev.depth, c->a[0]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetDepthStencilSurface(d3d_ctx *c) {
    if (!g_dev.depth) return SA2_D3DERR_NOTAVAILABLE;
    pk32(c->a[0], g_dev.depth);
    obj_ref(g_dev.depth);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* The frame's wait, at every BeginScene as the loader's limiter waits at every
 * call of its wrapper (frame_wait above says why here and not at Present). */
static uint32_t m_BeginScene(d3d_ctx *c) {
    g_st.beginscene_calls++;
    g_scene_since_present = 1;
    if (g_break == BRK_PRESENTWAIT) return SA2_D3D_OK;
    g_st.frame_waits_beginscene++;
    frame_wait(c, "BeginScene");
    return SA2_D3D_OK;
}
static uint32_t m_EndScene(d3d_ctx *c)   { g_st.endscene_calls++;   (void)c; return SA2_D3D_OK; }
static uint32_t m_Clear(d3d_ctx *c)      { g_st.clear_calls++;      (void)c; return SA2_D3D_OK; }
static uint32_t m_Draw(d3d_ctx *c)       { g_st.draw_calls++;       (void)c; return SA2_D3D_OK; }

/* --- the Set/Get pairs, answered from what was Set --- */

static uint32_t m_SetTransform(d3d_ctx *c) { return blob_set(c, 1, c->a[0], c->a[1], 64); }
static uint32_t m_GetTransform(d3d_ctx *c) {
    d3d_blob *b = blob_find(1, c->a[0], 0);
    if (b && b->len) { pkbuf(c->a[1], b->b, 64); c->answered = SA2_D3D9_ANS_GUEST; return SA2_D3D_OK; }
    /* D3D9's own default for an unset transform is the identity. */
    float m[16] = {1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1};
    pkbuf(c->a[1], m, sizeof m);
    c->answered = SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_MultiplyTransform(d3d_ctx *c) {
    refuse("IDirect3DDevice9", "MultiplyTransform", c->caller,
           "this device keeps transforms but does not multiply them, and a stored product "
           "that was never computed is worse than a refusal");
    return 0;
}
static uint32_t m_SetViewport(d3d_ctx *c) { return blob_set(c, 5, 0, c->a[0], 24); }
static uint32_t m_GetViewport(d3d_ctx *c) {
    d3d_blob *b = blob_find(5, 0, 0);
    if (b && b->len) { pkbuf(c->a[0], b->b, 24); c->answered = SA2_D3D9_ANS_GUEST; return SA2_D3D_OK; }
    /* D3D9's default viewport is the whole render target, which this device
     * knows from the presentation parameters the guest passed. */
    const uint32_t w = g_dev.pp_valid ? g_dev.pp[0] : sa2_window_screen_w();
    const uint32_t h = g_dev.pp_valid ? g_dev.pp[1] : sa2_window_screen_h();
    const float zn = 0.0f, zf = 1.0f;
    pk32(c->a[0] + 0u, 0);
    pk32(c->a[0] + 4u, 0);
    pk32(c->a[0] + 8u, w);
    pk32(c->a[0] + 12u, h);
    pkbuf(c->a[0] + 16u, &zn, 4);
    pkbuf(c->a[0] + 20u, &zf, 4);
    c->answered = SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetMaterial(d3d_ctx *c)  { return blob_set(c, 4, 0, c->a[0], 68); }
static uint32_t m_GetMaterial(d3d_ctx *c)  { return blob_get(c, 4, 0, c->a[0], 68); }
static uint32_t m_SetLight(d3d_ctx *c)     { return blob_set(c, 2, c->a[0], c->a[1], 104); }
static uint32_t m_GetLight(d3d_ctx *c)     { return blob_get(c, 2, c->a[0], c->a[1], 104); }
static uint32_t m_SetClipPlane(d3d_ctx *c) { return blob_set(c, 3, c->a[0], c->a[1], 16); }
static uint32_t m_GetClipPlane(d3d_ctx *c) { return blob_get(c, 3, c->a[0], c->a[1], 16); }
static uint32_t m_SetScissorRect(d3d_ctx *c) { return blob_set(c, 6, 0, c->a[0], 16); }
static uint32_t m_GetScissorRect(d3d_ctx *c) { return blob_get(c, 6, 0, c->a[0], 16); }
static uint32_t m_SetClipStatus(d3d_ctx *c)  { return blob_set(c, 7, 0, c->a[0], 8); }
static uint32_t m_GetClipStatus(d3d_ctx *c)  { return blob_get(c, 7, 0, c->a[0], 8); }

static uint32_t g_light_on[64];
static uint32_t m_LightEnable(d3d_ctx *c) {
    if (c->a[0] < 64) g_light_on[c->a[0]] = c->a[1] ? 1u : 0u;
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetLightEnable(d3d_ctx *c) {
    pk32(c->a[1], c->a[0] < 64 ? g_light_on[c->a[0]] : 0u);
    c->answered = c->a[0] < 64 ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetRenderState(d3d_ctx *c) {
    if (c->a[0] < RS_MAX) { g_dev.rs[c->a[0]] = c->a[1]; g_dev.rs_set[c->a[0]] = 1; }
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetRenderState(d3d_ctx *c) {
    const uint32_t k = c->a[0];
    const int known = k < RS_MAX && g_dev.rs_set[k];
    pk32(c->a[1], known ? g_dev.rs[k] : 0u);
    c->answered = known ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetTextureStageState(d3d_ctx *c) {
    if (c->a[0] < TSS_ST && c->a[1] < TSS_MAX) {
        g_dev.tss[c->a[0]][c->a[1]] = c->a[2];
        g_dev.tss_set[c->a[0]][c->a[1]] = 1;
    }
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetTextureStageState(d3d_ctx *c) {
    const int known = c->a[0] < TSS_ST && c->a[1] < TSS_MAX && g_dev.tss_set[c->a[0]][c->a[1]];
    pk32(c->a[2], known ? g_dev.tss[c->a[0]][c->a[1]] : 0u);
    c->answered = known ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetSamplerState(d3d_ctx *c) {
    if (c->a[0] < SAMP_MAX && c->a[1] < SAMP_ST) {
        g_dev.ss[c->a[0]][c->a[1]] = c->a[2];
        g_dev.ss_set[c->a[0]][c->a[1]] = 1;
    }
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetSamplerState(d3d_ctx *c) {
    const int known = c->a[0] < SAMP_MAX && c->a[1] < SAMP_ST && g_dev.ss_set[c->a[0]][c->a[1]];
    pk32(c->a[2], known ? g_dev.ss[c->a[0]][c->a[1]] : 0u);
    c->answered = known ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetTexture(d3d_ctx *c) {
    if (c->a[0] < SAMP_MAX) bind_set(&g_dev.tex[c->a[0]], c->a[1]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetTexture(d3d_ctx *c) {
    const uint32_t t = c->a[0] < SAMP_MAX ? g_dev.tex[c->a[0]] : 0u;
    pk32(c->a[1], t);
    if (t) obj_ref(t);
    c->answered = t ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetStreamSource(d3d_ctx *c) {
    if (c->a[0] < SAMP_MAX) {
        bind_set(&g_dev.stream[c->a[0]][0], c->a[1]);
        g_dev.stream[c->a[0]][1] = c->a[2];
        g_dev.stream[c->a[0]][2] = c->a[3];
    }
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetStreamSource(d3d_ctx *c) {
    const uint32_t i = c->a[0];
    const int known = i < SAMP_MAX;
    pk32(c->a[1], known ? g_dev.stream[i][0] : 0u);
    pk32(c->a[2], known ? g_dev.stream[i][1] : 0u);
    pk32(c->a[3], known ? g_dev.stream[i][2] : 0u);
    if (known && g_dev.stream[i][0]) obj_ref(g_dev.stream[i][0]);   /* it AddRefs the buffer */
    c->answered = known ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetStreamSourceFreq(d3d_ctx *c) {
    if (c->a[0] < SAMP_MAX) g_dev.stream_freq[c->a[0]] = c->a[1];
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetStreamSourceFreq(d3d_ctx *c) {
    pk32(c->a[1], c->a[0] < SAMP_MAX ? g_dev.stream_freq[c->a[0]] : 1u);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* `iface`: the value is an interface, so the setter binds it (bind_set) and
 * the getter, like every COM getter, AddRefs what it returns, so the guest's
 * Get-then-Release leaves the count where it was. Four of these did not
 * until 2026-09-22 (review). */
#define SIMPLE_SET(field, fn_set, fn_get, dflt, iface)                    \
    static uint32_t fn_set(d3d_ctx *c) {                                  \
        if (iface) bind_set(&g_dev.field, c->a[0]);                       \
        else g_dev.field = c->a[0];                                       \
        c->answered = SA2_D3D9_ANS_GUEST;                                 \
        return SA2_D3D_OK; }                                              \
    static uint32_t fn_get(d3d_ctx *c) {                                  \
        pk32(c->a[0], g_dev.field ? g_dev.field : (uint32_t)(dflt));      \
        if ((iface) && g_dev.field) obj_ref(g_dev.field);                 \
        c->answered = g_dev.field ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET; \
        return SA2_D3D_OK; }
SIMPLE_SET(indices, m_SetIndices, m_GetIndices, 0, 1)
SIMPLE_SET(fvf, m_SetFVF, m_GetFVF, 0, 0)
SIMPLE_SET(vdecl, m_SetVertexDeclaration, m_GetVertexDeclaration, 0, 1)
SIMPLE_SET(vshader, m_SetVertexShader, m_GetVertexShader, 0, 1)
SIMPLE_SET(pshader, m_SetPixelShader, m_GetPixelShader, 0, 1)
SIMPLE_SET(palette, m_SetCurrentTexturePalette, m_GetCurrentTexturePalette, 0, 0)

static uint32_t m_SetSoftwareVertexProcessing(d3d_ctx *c) {
    g_dev.swvp = c->a[0];
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_GetSoftwareVertexProcessing(d3d_ctx *c) {
    c->answered = SA2_D3D9_ANS_GUEST;
    return g_dev.swvp;
}
static uint32_t m_SetNPatchMode(d3d_ctx *c) {
    g_dev.npatch = c->a[0];
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* GetNPatchMode returns a float, which on x86 comes back in ST(0) and not in
 * EAX. This runtime sets EAX, so answering here would put the value in the
 * wrong register and the guest would read whatever the FPU stack held. */
static uint32_t shader_const_set(d3d_ctx *c, uint32_t (*tab)[4], uint8_t *set,
                                 uint32_t stride_dwords) {
    uint32_t start = c->a[0], src = c->a[1], n = c->a[2];
    for (uint32_t i = 0; i < n && start + i < VSF_MAX; i++) {
        for (uint32_t k = 0; k < stride_dwords && k < 4; k++)
            tab[start + i][k] = pe32(src + 4u * (i * stride_dwords + k));
        set[start + i] = 1;
    }
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t shader_const_get(d3d_ctx *c, uint32_t (*tab)[4], const uint8_t *set,
                                 uint32_t stride_dwords) {
    uint32_t start = c->a[0], dst = c->a[1], n = c->a[2];
    int all = 1;
    for (uint32_t i = 0; i < n && start + i < VSF_MAX; i++) {
        if (!set[start + i]) all = 0;
        for (uint32_t k = 0; k < stride_dwords && k < 4; k++)
            pk32(dst + 4u * (i * stride_dwords + k), tab[start + i][k]);
    }
    c->answered = all ? SA2_D3D9_ANS_GUEST : SA2_D3D9_ANS_UNSET;
    return SA2_D3D_OK;
}
static uint32_t m_SetVertexShaderConstantF(d3d_ctx *c) { return shader_const_set(c, g_dev.vsf, g_dev.vsf_set, 4); }
static uint32_t m_GetVertexShaderConstantF(d3d_ctx *c) { return shader_const_get(c, g_dev.vsf, g_dev.vsf_set, 4); }
static uint32_t m_SetVertexShaderConstantI(d3d_ctx *c) { return shader_const_set(c, g_dev.vsi, g_dev.vsi_set, 4); }
static uint32_t m_GetVertexShaderConstantI(d3d_ctx *c) { return shader_const_get(c, g_dev.vsi, g_dev.vsi_set, 4); }
static uint32_t m_SetVertexShaderConstantB(d3d_ctx *c) { return shader_const_set(c, g_dev.vsb, g_dev.vsb_set, 1); }
static uint32_t m_GetVertexShaderConstantB(d3d_ctx *c) { return shader_const_get(c, g_dev.vsb, g_dev.vsb_set, 1); }
static uint32_t m_SetPixelShaderConstantF(d3d_ctx *c) { return shader_const_set(c, g_dev.psf, g_dev.psf_set, 4); }
static uint32_t m_GetPixelShaderConstantF(d3d_ctx *c) { return shader_const_get(c, g_dev.psf, g_dev.psf_set, 4); }
static uint32_t m_SetPixelShaderConstantI(d3d_ctx *c) { return shader_const_set(c, g_dev.psi, g_dev.psi_set, 4); }
static uint32_t m_GetPixelShaderConstantI(d3d_ctx *c) { return shader_const_get(c, g_dev.psi, g_dev.psi_set, 4); }
static uint32_t m_SetPixelShaderConstantB(d3d_ctx *c) { return shader_const_set(c, g_dev.psb, g_dev.psb_set, 1); }
static uint32_t m_GetPixelShaderConstantB(d3d_ctx *c) { return shader_const_get(c, g_dev.psb, g_dev.psb_set, 1); }

static uint32_t m_ValidateDevice(d3d_ctx *c) {
    if (g_strict_caps)
        refuse("IDirect3DDevice9", "ValidateDevice", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and a pass count for state nothing rasterises "
               "is invented");
    pk32(c->a[0], 1u);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_GetRenderTargetData(d3d_ctx *c) {
    refuse("IDirect3DDevice9", "GetRenderTargetData", c->caller,
           "nothing has been rendered, so the destination surface would be handed whatever "
           "its backing bytes already hold, presented as a frame");
    return 0;
}
static uint32_t m_GetFrontBufferData(d3d_ctx *c) {
    refuse("IDirect3DDevice9", "GetFrontBufferData", c->caller,
           "there is no front buffer behind this device to copy");
    return 0;
}

static const d3d_row ROWS_DEVICE[] = {
    {0,   2, 0, "QueryInterface", m_QueryInterface},
    {1,   0, 0, "AddRef", m_AddRef},
    {2,   0, 0, "Release", m_Release},
    {3,   0, 0, "TestCooperativeLevel", m_TestCooperativeLevel},
    {4,   0, DF_CAP, "GetAvailableTextureMem", m_GetAvailableTextureMem},
    {5,   0, 0, "EvictManagedResources", m_ok},
    {6,   1, DF_OUT, "GetDirect3D", m_GetDirect3D},
    {7,   1, DF_OUT | DF_CAP, "GetDeviceCaps", m_DevGetDeviceCaps},
    {8,   2, DF_OUT | DF_CAP, "GetDisplayMode", m_GetDisplayMode},
    {9,   1, DF_OUT, "GetCreationParameters", m_GetCreationParameters},
    {10,  3, 0, "SetCursorProperties", m_ok},
    {11,  3, DF_VOID, "SetCursorPosition", m_zero},
    {12,  1, 0, "ShowCursor", m_ShowCursor},
    {13,  2, DF_OUT, "CreateAdditionalSwapChain", m_CreateAdditionalSwapChain},
    {14,  2, DF_OUT, "GetSwapChain", m_GetSwapChain},
    {15,  0, 0, "GetNumberOfSwapChains", m_GetNumberOfSwapChains},
    {16,  1, 0, "Reset", m_Reset},
    {17,  4, 0, "Present", m_Present},
    {18,  4, DF_OUT, "GetBackBuffer", m_GetBackBuffer},
    {19,  2, DF_OUT | DF_CAP, "GetRasterStatus", m_GetRasterStatus},
    {20,  1, 0, "SetDialogBoxMode", m_ok},
    {21,  3, DF_VOID, "SetGammaRamp", m_zero},
    {22,  2, DF_OUT | DF_VOID | DF_CAP, "GetGammaRamp", m_GetGammaRamp},
    {23,  8, DF_OUT, "CreateTexture", m_CreateTexture},
    {24,  9, DF_OUT, "CreateVolumeTexture", m_CreateVolumeTexture},
    {25,  7, DF_OUT, "CreateCubeTexture", m_CreateCubeTexture},
    {26,  6, DF_OUT, "CreateVertexBuffer", m_CreateVertexBuffer},
    {27,  6, DF_OUT, "CreateIndexBuffer", m_CreateIndexBuffer},
    {28,  8, DF_OUT, "CreateRenderTarget", m_CreateRenderTarget},
    {29,  8, DF_OUT, "CreateDepthStencilSurface", m_CreateDepthStencilSurface},
    {30,  4, 0, "UpdateSurface", m_ok},
    {31,  2, 0, "UpdateTexture", m_ok},
    {32,  2, DF_OUT, "GetRenderTargetData", m_GetRenderTargetData},
    {33,  2, DF_OUT, "GetFrontBufferData", m_GetFrontBufferData},
    {34,  5, 0, "StretchRect", m_ok},
    {35,  3, 0, "ColorFill", m_ok},
    {36,  6, DF_OUT, "CreateOffscreenPlainSurface", m_CreateOffscreenPlainSurface},
    {37,  2, 0, "SetRenderTarget", m_SetRenderTarget},
    {38,  2, DF_OUT, "GetRenderTarget", m_GetRenderTarget},
    {39,  1, 0, "SetDepthStencilSurface", m_SetDepthStencilSurface},
    {40,  1, DF_OUT, "GetDepthStencilSurface", m_GetDepthStencilSurface},
    {41,  0, 0, "BeginScene", m_BeginScene},
    {42,  0, 0, "EndScene", m_EndScene},
    {43,  6, 0, "Clear", m_Clear},
    {44,  2, 0, "SetTransform", m_SetTransform},
    {45,  2, DF_OUT, "GetTransform", m_GetTransform},
    {46,  2, 0, "MultiplyTransform", m_MultiplyTransform},
    {47,  1, 0, "SetViewport", m_SetViewport},
    {48,  1, DF_OUT, "GetViewport", m_GetViewport},
    {49,  1, 0, "SetMaterial", m_SetMaterial},
    {50,  1, DF_OUT, "GetMaterial", m_GetMaterial},
    {51,  2, 0, "SetLight", m_SetLight},
    {52,  2, DF_OUT, "GetLight", m_GetLight},
    {53,  2, 0, "LightEnable", m_LightEnable},
    {54,  2, DF_OUT, "GetLightEnable", m_GetLightEnable},
    {55,  2, 0, "SetClipPlane", m_SetClipPlane},
    {56,  2, DF_OUT, "GetClipPlane", m_GetClipPlane},
    {57,  2, 0, "SetRenderState", m_SetRenderState},
    {58,  2, DF_OUT, "GetRenderState", m_GetRenderState},
    {59,  2, DF_OUT, "CreateStateBlock", m_CreateStateBlock},
    {60,  0, 0, "BeginStateBlock", m_BeginStateBlock},
    {61,  1, DF_OUT, "EndStateBlock", m_EndStateBlock},
    {62,  1, 0, "SetClipStatus", m_SetClipStatus},
    {63,  1, DF_OUT, "GetClipStatus", m_GetClipStatus},
    {64,  2, DF_OUT, "GetTexture", m_GetTexture},
    {65,  2, 0, "SetTexture", m_SetTexture},
    {66,  3, DF_OUT, "GetTextureStageState", m_GetTextureStageState},
    {67,  3, 0, "SetTextureStageState", m_SetTextureStageState},
    {68,  3, DF_OUT, "GetSamplerState", m_GetSamplerState},
    {69,  3, 0, "SetSamplerState", m_SetSamplerState},
    {70,  1, DF_OUT | DF_CAP, "ValidateDevice", m_ValidateDevice},
    {71,  2, 0, "SetPaletteEntries", m_ok},
    {72,  2, DF_OUT, "GetPaletteEntries", NULL},
    {73,  1, 0, "SetCurrentTexturePalette", m_SetCurrentTexturePalette},
    {74,  1, DF_OUT, "GetCurrentTexturePalette", m_GetCurrentTexturePalette},
    {75,  1, 0, "SetScissorRect", m_SetScissorRect},
    {76,  1, DF_OUT, "GetScissorRect", m_GetScissorRect},
    {77,  1, 0, "SetSoftwareVertexProcessing", m_SetSoftwareVertexProcessing},
    {78,  0, 0, "GetSoftwareVertexProcessing", m_GetSoftwareVertexProcessing},
    {79,  1, 0, "SetNPatchMode", m_SetNPatchMode},
    {80,  0, 0, "GetNPatchMode", NULL},
    {81,  3, 0, "DrawPrimitive", m_Draw},
    {82,  6, 0, "DrawIndexedPrimitive", m_Draw},
    {83,  4, 0, "DrawPrimitiveUP", m_Draw},
    {84,  8, 0, "DrawIndexedPrimitiveUP", m_Draw},
    {85,  6, 0, "ProcessVertices", m_ok},
    {86,  2, DF_OUT, "CreateVertexDeclaration", m_CreateVertexDeclaration},
    {87,  1, 0, "SetVertexDeclaration", m_SetVertexDeclaration},
    {88,  1, DF_OUT, "GetVertexDeclaration", m_GetVertexDeclaration},
    {89,  1, 0, "SetFVF", m_SetFVF},
    {90,  1, DF_OUT, "GetFVF", m_GetFVF},
    {91,  2, DF_OUT, "CreateVertexShader", m_CreateVertexShader},
    {92,  1, 0, "SetVertexShader", m_SetVertexShader},
    {93,  1, DF_OUT, "GetVertexShader", m_GetVertexShader},
    {94,  3, 0, "SetVertexShaderConstantF", m_SetVertexShaderConstantF},
    {95,  3, DF_OUT, "GetVertexShaderConstantF", m_GetVertexShaderConstantF},
    {96,  3, 0, "SetVertexShaderConstantI", m_SetVertexShaderConstantI},
    {97,  3, DF_OUT, "GetVertexShaderConstantI", m_GetVertexShaderConstantI},
    {98,  3, 0, "SetVertexShaderConstantB", m_SetVertexShaderConstantB},
    {99,  3, DF_OUT, "GetVertexShaderConstantB", m_GetVertexShaderConstantB},
    {100, 4, 0, "SetStreamSource", m_SetStreamSource},
    {101, 4, DF_OUT, "GetStreamSource", m_GetStreamSource},
    {102, 2, 0, "SetStreamSourceFreq", m_SetStreamSourceFreq},
    {103, 2, DF_OUT, "GetStreamSourceFreq", m_GetStreamSourceFreq},
    {104, 1, 0, "SetIndices", m_SetIndices},
    {105, 1, DF_OUT, "GetIndices", m_GetIndices},
    {106, 2, DF_OUT, "CreatePixelShader", m_CreatePixelShader},
    {107, 1, 0, "SetPixelShader", m_SetPixelShader},
    {108, 1, DF_OUT, "GetPixelShader", m_GetPixelShader},
    {109, 3, 0, "SetPixelShaderConstantF", m_SetPixelShaderConstantF},
    {110, 3, DF_OUT, "GetPixelShaderConstantF", m_GetPixelShaderConstantF},
    {111, 3, 0, "SetPixelShaderConstantI", m_SetPixelShaderConstantI},
    {112, 3, DF_OUT, "GetPixelShaderConstantI", m_GetPixelShaderConstantI},
    {113, 3, 0, "SetPixelShaderConstantB", m_SetPixelShaderConstantB},
    {114, 3, DF_OUT, "GetPixelShaderConstantB", m_GetPixelShaderConstantB},
    {115, 3, 0, "DrawRectPatch", m_Draw},
    {116, 3, 0, "DrawTriPatch", m_Draw},
    {117, 1, 0, "DeletePatch", m_ok},
    {118, 2, DF_OUT, "CreateQuery", m_CreateQuery},
    {0, 0, 0, NULL, NULL},
};

/* ----------------------------------------------------- the resources */

static uint32_t m_GetType(d3d_ctx *c) {
    /* D3DRESOURCETYPE: SURFACE 1, VOLUME 2, TEXTURE 3, VOLUMETEXTURE 4,
     * CUBETEXTURE 5, VERTEXBUFFER 6, INDEXBUFFER 7. */
    static const uint8_t k[] = {0, 0, 3, 5, 4, 1, 2, 6, 7};
    const uint32_t t = c->obj ? c->obj->type : 0u;
    c->answered = SA2_D3D9_ANS_GUEST;
    return t < sizeof k ? k[t] : 0u;
}
static uint32_t m_GetLevelCount(d3d_ctx *c) {
    c->answered = SA2_D3D9_ANS_GUEST;
    if (c->obj && c->obj->type == T_CUBETEX) return cube_exposed_levels(c->obj);
    return c->obj && c->obj->d[2] ? c->obj->d[2] : 1u;
}
/* D3DSURFACE_DESC: Format, Type, Usage, Pool, MultiSampleType,
 * MultiSampleQuality, Width, Height -- eight dwords, every one of them a value
 * the guest supplied when it created the resource. */
static void fill_surface_desc(d3d_obj *o, uint32_t p, uint32_t rtype,
                              uint32_t w, uint32_t h, uint32_t fmt,
                              uint32_t usage, uint32_t pool) {
    (void)o;
    pk32(p + 0u, fmt);
    pk32(p + 4u, rtype);
    pk32(p + 8u, usage);
    pk32(p + 12u, pool);
    pk32(p + 16u, 0);
    pk32(p + 20u, 0);
    pk32(p + 24u, w);
    pk32(p + 28u, h);
}
static uint32_t m_TexGetLevelDesc(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    fill_surface_desc(o, c->a[1], 3u, level_dim(o->d[0], c->a[0]), level_dim(o->d[1], c->a[0]),
                      o->d[4], o->d[3], o->d[5]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* A cube texture's level description is one face's level, which Direct3D 9 describes as a
 * surface: D3DSURFACE_DESC.Type is D3DRTYPE_SURFACE (DXVK's D3D9TextureCube::GetLevelDesc
 * hands the face's D3D9Surface::GetDesc; Wine's d3d9_texture_cube_GetLevelDesc writes
 * D3DRTYPE_SURFACE), not the D3DRTYPE_TEXTURE m_TexGetLevelDesc writes, and a level past the
 * exposed count is D3DERR_INVALIDCALL. No recorded run reaches a cube texture, so no run
 * changes; tools/s5/tests/rec_harness_m7.c's GetLevelDesc case holds both. */
static uint32_t m_CubeGetLevelDesc(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o || o->type != T_CUBETEX || !c->a[1] || c->a[0] >= cube_exposed_levels(o))
        return SA2_D3DERR_INVALIDCALL;
    fill_surface_desc(o, c->a[1], 1u, level_dim(o->d[0], c->a[0]), level_dim(o->d[1], c->a[0]),
                      o->d[4], o->d[3], o->d[5]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* D3DVOLUME_DESC: Format, Type, Usage, Pool, Width, Height, Depth -- seven
 * dwords, and unlike D3DSURFACE_DESC it carries no multisample pair, so the
 * two structures are not interchangeable and the volume path needs its own
 * fill. Every field is a value the guest supplied at creation.
 *
 * This slot had no handler until the D3DX volume creators were implemented,
 * and it is on their success path rather than beyond it: both call sites read
 * the volume back immediately --
 *
 *     00866b22  call eax                 ; GetLevelDesc(0, &desc)
 *     00866b27  mov ecx,[esp+50h]        ; desc+0x10  Width
 *     00866b2b  mov edx,[esp+54h]        ; desc+0x14  Height
 *     00866b2f  mov eax,[esp+58h]        ; desc+0x18  Depth
 *
 * -- so a creator that answered S_OK while this refused would have moved the
 * trap four instructions, not removed it. */
static uint32_t m_VolTexGetLevelDesc(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    const uint32_t lv = c->a[0], p = c->a[1];
    if (!p) return SA2_D3DERR_INVALIDCALL;
    pk32(p + 0u, o->d[4]);                      /* Format */
    pk32(p + 4u, 2u);                           /* D3DRTYPE_VOLUME */
    pk32(p + 8u, o->d[3]);                      /* Usage */
    pk32(p + 12u, o->d[5]);                     /* Pool */
    pk32(p + 16u, level_dim(o->d[0], lv));
    pk32(p + 20u, level_dim(o->d[1], lv));
    pk32(p + 24u, level_dim(o->d[6] ? o->d[6] : 1u, lv));
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* A texture's level surface is part of the texture, which is Direct3D 9's
 * model and the one the guest sees there: GetSurfaceLevel hands back the same
 * interface for the same level every time, AddRef'd; that interface's AddRef
 * and Release are its container's; and a Lock of it is a Lock of the
 * container's level, the same bytes IDirect3DTexture9::LockRect hands out. It
 * is built on the first request for its level and destroyed with its
 * container. neverfree and ownzero put back the model this file had until
 * 2026-09-23 -- a fresh surface with its own count and its own store on every
 * call -- which is what exhausted the arena behind the title screen. */
static uint32_t m_TexGetSurfaceLevel(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    const uint32_t lv = c->a[0];
    if (life_old()) {
        d3d_obj *s = obj_new(T_SURFACE, o->addr);
        s->d[0] = level_dim(o->d[0], lv);
        s->d[1] = level_dim(o->d[1], lv);
        s->d[2] = o->d[4];
        s->d[3] = o->d[3];
        s->d[4] = o->d[5];
        pk32(c->a[1], s->addr);
        c->answered = SA2_D3D9_ANS_OBJECT;
        return SA2_D3D_OK;
    }
    /* Direct3D refuses a level past the texture's own count. This record's
     * count can be short of it -- CreateTexture records a Levels of 0, which
     * asks for the complete chain, as 1 (the D3DX creators record the chain,
     * d3dx_levels) -- so the bound is the complete chain, past which no
     * texture has a level; the old model refused none. A Lock of a level past
     * the record grows the store. */
    if (!c->a[1] || lv >= full_chain(o->d[0], o->d[1])) return SA2_D3DERR_INVALIDCALL;
    if (lv >= LEVELS_MAX) {
        sa2_trap_note("IDirect3DTexture9::GetSurfaceLevel from %#010x: level %u of %#010x, and "
                      "this runtime keeps the surfaces of the first %u levels only",
                      c->caller, lv, o->addr, LEVELS_MAX);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    d3d_obj *s = o->lvl[lv] ? &g_obj[o->lvl[lv] - 1u] : NULL;
    if (!s) {
        s = obj_new(T_SURFACE, o->addr);
        s->d[0] = level_dim(o->d[0], lv);
        s->d[1] = level_dim(o->d[1], lv);
        s->d[2] = o->d[4];
        s->d[3] = o->d[3];
        s->d[4] = o->d[5];
        s->life = LIFE_LEVEL;
        s->level = (uint8_t)lv;
        s->refs = 0;                             /* its count is the container's */
        s->alive = o->alive;
        o->lvl[lv] = s->idx + 1u;
        g_st.level_surfaces++;
    }
    o->refs++;                                   /* the AddRef the surface carries out */
    pk32(c->a[1], s->addr);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}


static uint32_t m_CubeGetSurface(d3d_ctx *c) {
    const uint32_t face = c->a[0], level = c->a[1], out = c->a[2];
    if (!out) return SA2_D3DERR_INVALIDCALL;
    pk32(out, 0u);                               /* validates before building or AddRef */
    d3d_obj *o = c->obj;
    if (!o || o->type != T_CUBETEX || !o->alive ||
        face >= 6u || level >= cube_exposed_levels(o)) return SA2_D3DERR_INVALIDCALL;
    if (level >= LEVELS_MAX)
        refuse("IDirect3DCubeTexture9", "GetCubeMapSurface", c->caller,
               "cube mip level exceeds this runtime's surface cache capacity");
    (void)cube_storage(o, face, level, c->caller);
    const uint32_t key = face * LEVELS_MAX + level;
    d3d_obj *s = !life_old() && o->lvl[key] ? &g_obj[o->lvl[key] - 1u] : NULL;
    if (!s) {
        s = obj_new(T_SURFACE, o->addr);
        s->d[0] = level_dim(o->d[0], level);
        s->d[1] = level_dim(o->d[1], level);
        s->d[2] = o->d[4]; s->d[3] = o->d[3]; s->d[4] = o->d[5];
        s->face = (uint8_t)face; s->level = (uint8_t)level;
        if (!life_old()) {
            s->life = LIFE_LEVEL; s->refs = 0; s->alive = o->alive;
            o->lvl[key] = s->idx + 1u;
            g_st.level_surfaces++;
        }
    }
    if (!life_old()) o->refs++;                   /* legacy surfaces own their count */
    pk32(out, s->addr);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
/* D3DLOCKED_RECT { INT Pitch; void *pBits; }. The bytes are real guest heap so
 * whatever the guest writes into a texture is still there afterwards. `o` owns
 * the bytes: the resource itself, or a level surface's container. */
static uint32_t lock_rect(d3d_ctx *c, d3d_obj *o, uint32_t out, uint32_t w, uint32_t h,
                          uint32_t fmt, uint32_t reserve, uint32_t offset) {
    if (!out) return SA2_D3DERR_INVALIDCALL;
    const uint32_t blk = fmt_block(fmt);
    uint32_t pitch, bytes;
    if (blk) {
        pitch = ((w + 3u) / 4u) * blk;
        bytes = pitch * ((h + 3u) / 4u);
    } else {
        const uint32_t bpp = fmt_bpp(fmt);
        if (!bpp) {
            sa2_trap_note("d3d9: LockRect on format %#010x (%u): this runtime has no bytes "
                          "per pixel for it, and a guessed pitch silently corrupts whatever "
                          "the guest writes", fmt, fmt);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        pitch = w * bpp;
        bytes = pitch * h;
    }
    const uint32_t mem = obj_alloc(o, reserve > bytes + offset ? reserve : bytes + offset);
    o->handed = 1;
    o->locks++;
    tex_row_lock(o->src_idx);            /* a file-loaded texture counts its own locks */
    pk32(out + 0u, pitch);
    pk32(out + 4u, mem + offset);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* The whole chain is reserved on the first lock of any level, and each level
 * is handed its own slice of it. Reserving on the first lock rather than
 * growing per level matters: `obj_alloc` allocates a *new* block when the one
 * it has is too small, so a level-0 lock after a level-1 lock would otherwise
 * hand back a different address and lose what the guest had written. */
static uint32_t tex_lock_level(d3d_ctx *c, d3d_obj *o, uint32_t lv, uint32_t out) {
    return lock_rect(c, o, out, level_dim(o->d[0], lv), level_dim(o->d[1], lv), o->d[4],
                     tex_reserve(o), chain_offset(o->d[0], o->d[1], o->d[4], lv));
}
static uint32_t m_TexLockRect(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    return tex_lock_level(c, o, c->a[0], c->a[1]);
}
static uint32_t m_SurfLockRect(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    if (o->life == LIFE_LEVEL) {
        d3d_obj *t = life_root(o);
        if (t->type == T_CUBETEX) {
            const cube_span span = cube_storage(t, o->face, o->level, c->caller);
            return lock_rect(c, t, c->a[0], o->d[0], o->d[1], o->d[2],
                             span.reserve, span.offset);
        }
        return tex_lock_level(c, t, o->level, c->a[0]);
    }
    return lock_rect(c, o, c->a[0], o->d[0], o->d[1], o->d[2], 0, 0);
}
static uint32_t m_SurfGetDesc(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    fill_surface_desc(o, c->a[0], 1u, o->d[0], o->d[1], o->d[2], o->d[3], o->d[4]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
/* D3DVERTEXBUFFER_DESC { Format, Type, Usage, Pool, Size, FVF } */
static uint32_t m_BufGetDesc(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    const int vb = o->type == T_VERTEXBUFFER;
    pk32(c->a[0] + 0u, vb ? 100u : o->d[2]);      /* D3DFMT_VERTEXDATA, or the index format */
    pk32(c->a[0] + 4u, vb ? 6u : 7u);
    pk32(c->a[0] + 8u, o->d[1]);
    pk32(c->a[0] + 12u, o->d[3]);
    pk32(c->a[0] + 16u, o->d[0]);
    if (vb) pk32(c->a[0] + 20u, o->d[2]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_BufLock(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o) return SA2_D3DERR_INVALIDCALL;
    const uint32_t offset = c->a[0];
    const uint32_t size = c->a[1] ? c->a[1] : (o->d[0] > offset ? o->d[0] - offset : 0u);
    const uint32_t mem = obj_alloc(o, o->d[0] ? o->d[0] : size);
    o->handed = 1;
    pk32(c->a[2], mem + offset);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}

#define BASETEXTURE_ROWS \
    {11, 1, 0, "SetLOD", m_zero}, \
    {12, 0, 0, "GetLOD", m_zero}, \
    {13, 0, 0, "GetLevelCount", m_GetLevelCount}, \
    {14, 1, 0, "SetAutoGenFilterType", m_ok}, \
    {15, 0, 0, "GetAutoGenFilterType", m_zero}, \
    {16, 0, DF_VOID, "GenerateMipSubLevels", m_zero}

#define RESOURCE_ROWS \
    {0,  2, 0, "QueryInterface", m_QueryInterface}, \
    {1,  0, 0, "AddRef", m_AddRef}, \
    {2,  0, 0, "Release", m_Release}, \
    {3,  1, DF_OUT, "GetDevice", m_GetDevice}, \
    {4,  4, 0, "SetPrivateData", m_SetPrivateData}, \
    {5,  3, DF_OUT, "GetPrivateData", m_GetPrivateData}, \
    {6,  1, 0, "FreePrivateData", m_FreePrivateData}, \
    {7,  1, 0, "SetPriority", m_SetPriority}, \
    {8,  0, 0, "GetPriority", m_GetPriority}, \
    {9,  0, DF_VOID, "PreLoad", m_zero}, \
    {10, 0, 0, "GetType", m_GetType}


/* ---- shader bytecode and vertex declarations, handed back from the guest ----
 *
 * Neither of these is invented. `CreateVertexShader`, `CreatePixelShader` and
 * `CreateVertexDeclaration` are given a blob by the guest and this runtime
 * keeps the pointer; `GetFunction` and `GetDeclaration` copy those same bytes
 * back. The only thing measured is the length, and both formats carry their
 * own terminator -- the 0x0000FFFF END token and the D3DDECL_END element whose
 * Stream is 0xFF -- so the length is read out of the guest's data rather than
 * assumed. A blob with no terminator inside a sane bound refuses, because
 * guessing a length here would copy whatever follows it in guest memory. */
static uint32_t gcopy(uint32_t dst, uint32_t src, uint32_t n) {
    uint8_t buf[512];
    for (uint32_t off = 0; off < n; ) {
        const uint32_t k = (n - off) < sizeof buf ? (n - off) : (uint32_t)sizeof buf;
        if (sa2_peek(src + off, k, buf) != SA2_OK) gfault(src + off, "a read of a guest blob");
        if (sa2_poke(dst + off, k, buf) != SA2_OK) gfault(dst + off, "a write of a guest blob");
        off += k;
    }
    return n;
}
static uint32_t shader_blob_bytes(uint32_t p) {
    if (!p) return 0;
    for (uint32_t n = 0; n < 65536u; n++)
        if (pe32(p + 4u * n) == 0x0000FFFFu) return 4u * (n + 1u);
    return 0;
}
static uint32_t m_GetFunction(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o || !o->d[0]) return SA2_D3DERR_INVALIDCALL;
    const uint32_t n = shader_blob_bytes(o->d[0]);
    if (!n)
        refuse(sa2_d3d9_type_name(o->type), "GetFunction", c->caller,
               "the blob this shader was created from has no 0x0000FFFF end token within "
               "64K tokens, so its length cannot be read out of the guest's own data");
    if (!c->a[1]) return SA2_D3DERR_INVALIDCALL;
    if (!c->a[0]) { pk32(c->a[1], n); c->answered = SA2_D3D9_ANS_GUEST; return SA2_D3D_OK; }
    if (pe32(c->a[1]) < n) return SA2_D3DERR_INVALIDCALL;
    gcopy(c->a[0], o->d[0], n);
    pk32(c->a[1], n);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t decl_elements(uint32_t p) {
    if (!p) return 0;
    for (uint32_t n = 0; n < 256u; n++) {
        uint16_t stream = 0;
        if (sa2_peek(p + 8u * n, 2, &stream) != SA2_OK) return 0;
        if (stream == 0x00FFu) return n + 1u;          /* D3DDECL_END */
    }
    return 0;
}
static uint32_t m_GetDeclaration(d3d_ctx *c) {
    d3d_obj *o = c->obj;
    if (!o || !o->d[0]) return SA2_D3DERR_INVALIDCALL;
    const uint32_t n = decl_elements(o->d[0]);
    if (!n)
        refuse("IDirect3DVertexDeclaration9", "GetDeclaration", c->caller,
               "the element array this declaration was created from has no D3DDECL_END "
               "within 256 elements, so its length cannot be read out of the guest's data");
    if (!c->a[1]) return SA2_D3DERR_INVALIDCALL;
    if (!c->a[0]) { pk32(c->a[1], n); c->answered = SA2_D3D9_ANS_GUEST; return SA2_D3D_OK; }
    gcopy(c->a[0], o->d[0], 8u * n);
    pk32(c->a[1], n);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}

static const d3d_row ROWS_TEXTURE[] = {
    RESOURCE_ROWS, BASETEXTURE_ROWS,
    {17, 2, DF_OUT, "GetLevelDesc", m_TexGetLevelDesc},
    {18, 2, DF_OUT, "GetSurfaceLevel", m_TexGetSurfaceLevel},
    {19, 4, DF_OUT, "LockRect", m_TexLockRect},
    {20, 1, 0, "UnlockRect", m_ok},
    {21, 1, 0, "AddDirtyRect", m_ok},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_CUBETEX[] = {
    RESOURCE_ROWS, BASETEXTURE_ROWS,
    {17, 2, DF_OUT, "GetLevelDesc", m_CubeGetLevelDesc},
    {18, 3, DF_OUT, "GetCubeMapSurface", m_CubeGetSurface},
    {19, 5, DF_OUT, "LockRect", NULL},
    {20, 2, 0, "UnlockRect", m_ok},
    {21, 2, 0, "AddDirtyRect", m_ok},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_VOLTEX[] = {
    RESOURCE_ROWS, BASETEXTURE_ROWS,
    {17, 2, DF_OUT, "GetLevelDesc", m_VolTexGetLevelDesc},
    {18, 2, DF_OUT, "GetVolumeLevel", NULL},
    {19, 3, DF_OUT, "LockBox", NULL},
    {20, 1, 0, "UnlockBox", m_ok},
    {21, 1, 0, "AddDirtyBox", m_ok},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_SURFACE[] = {
    RESOURCE_ROWS,
    {11, 2, DF_OUT, "GetContainer", m_GetContainer},
    {12, 1, DF_OUT, "GetDesc", m_SurfGetDesc},
    {13, 3, DF_OUT, "LockRect", m_SurfLockRect},
    {14, 0, 0, "UnlockRect", m_ok},
    {15, 1, DF_OUT, "GetDC", NULL},
    {16, 1, 0, "ReleaseDC", NULL},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_VOLUME[] = {
    {0, 2, 0, "QueryInterface", m_QueryInterface},
    {1, 0, 0, "AddRef", m_AddRef},
    {2, 0, 0, "Release", m_Release},
    {3, 1, DF_OUT, "GetDevice", m_GetDevice},
    {4, 4, 0, "SetPrivateData", m_SetPrivateData},
    {5, 3, DF_OUT, "GetPrivateData", m_GetPrivateData},
    {6, 1, 0, "FreePrivateData", m_FreePrivateData},
    {7, 2, DF_OUT, "GetContainer", m_GetContainer},
    {8, 1, DF_OUT, "GetDesc", NULL},
    {9, 3, DF_OUT, "LockBox", NULL},
    {10, 0, 0, "UnlockBox", m_ok},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_VERTEXBUFFER[] = {
    RESOURCE_ROWS,
    {11, 4, DF_OUT, "Lock", m_BufLock},
    {12, 0, 0, "Unlock", m_ok},
    {13, 1, DF_OUT, "GetDesc", m_BufGetDesc},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_INDEXBUFFER[] = {
    RESOURCE_ROWS,
    {11, 4, DF_OUT, "Lock", m_BufLock},
    {12, 0, 0, "Unlock", m_ok},
    {13, 1, DF_OUT, "GetDesc", m_BufGetDesc},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_VERTEXDECL[] = {
    {0, 2, 0, "QueryInterface", m_QueryInterface},
    {1, 0, 0, "AddRef", m_AddRef},
    {2, 0, 0, "Release", m_Release},
    {3, 1, DF_OUT, "GetDevice", m_GetDevice},
    {4, 2, DF_OUT, "GetDeclaration", m_GetDeclaration},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_SHADER[] = {
    {0, 2, 0, "QueryInterface", m_QueryInterface},
    {1, 0, 0, "AddRef", m_AddRef},
    {2, 0, 0, "Release", m_Release},
    {3, 1, DF_OUT, "GetDevice", m_GetDevice},
    {4, 2, DF_OUT, "GetFunction", m_GetFunction},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_STATEBLOCK[] = {
    {0, 2, 0, "QueryInterface", m_QueryInterface},
    {1, 0, 0, "AddRef", m_AddRef},
    {2, 0, 0, "Release", m_Release},
    {3, 1, DF_OUT, "GetDevice", m_GetDevice},
    {4, 0, 0, "Capture", m_ok},
    {5, 0, 0, "Apply", m_ok},
    {0, 0, 0, NULL, NULL},
};
static uint32_t m_SwapPresent(d3d_ctx *c) {
    g_st.present_calls++;
    g_st.last_present_seq = g_seq;       /* this call's number, set by note_reserve */
    (void)c;
    return SA2_D3D_OK;
}
static uint32_t m_SwapGetDisplayMode(d3d_ctx *c) {
    if (g_strict_caps)
        refuse("IDirect3DSwapChain9", "GetDisplayMode", c->caller,
               "SA2_D3D9_STRICT_CAPS is set and the refresh rate and format are invented");
    fill_display_mode(c->a[0], 22u);
    c->answered = SA2_D3D9_ANS_INVENT;
    return SA2_D3D_OK;
}
static uint32_t m_SwapGetBackBuffer(d3d_ctx *c) {
    if (!g_dev.rt[0]) return SA2_D3DERR_INVALIDCALL;
    pk32(c->a[2], g_dev.rt[0]);
    obj_ref(g_dev.rt[0]);
    c->answered = SA2_D3D9_ANS_OBJECT;
    return SA2_D3D_OK;
}
static uint32_t m_SwapGetPresentParameters(d3d_ctx *c) {
    if (!g_dev.pp_valid) return SA2_D3DERR_INVALIDCALL;
    for (uint32_t i = 0; i < 14; i++) pk32(c->a[0] + 4u * i, g_dev.pp[i]);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static const d3d_row ROWS_SWAPCHAIN[] = {
    {0, 2, 0, "QueryInterface", m_QueryInterface},
    {1, 0, 0, "AddRef", m_AddRef},
    {2, 0, 0, "Release", m_Release},
    {3, 5, 0, "Present", m_SwapPresent},
    {4, 1, DF_OUT, "GetFrontBufferData", m_GetFrontBufferData},
    {5, 3, DF_OUT, "GetBackBuffer", m_SwapGetBackBuffer},
    {6, 1, DF_OUT | DF_CAP, "GetRasterStatus", NULL},
    {7, 1, DF_OUT | DF_CAP, "GetDisplayMode", m_SwapGetDisplayMode},
    {8, 1, DF_OUT, "GetDevice", m_GetDevice},
    {9, 1, DF_OUT, "GetPresentParameters", m_SwapGetPresentParameters},
    {0, 0, 0, NULL, NULL},
};
static const d3d_row ROWS_QUERY[] = {
    {0, 2, 0, "QueryInterface", m_QueryInterface},
    {1, 0, 0, "AddRef", m_AddRef},
    {2, 0, 0, "Release", m_Release},
    {3, 1, DF_OUT, "GetDevice", m_GetDevice},
    {4, 0, 0, "GetType", m_GetType},
    {5, 0, 0, "GetDataSize", m_zero},
    {6, 1, 0, "Issue", m_ok},
    {7, 3, DF_OUT, "GetData", NULL},
    {0, 0, 0, NULL, NULL},
};


/* ID3DXConstantTable: what D3DXGetShaderConstantTable hands back with
 * SA2_D3DX_CONSTANT_TABLE=1, and why it answers three methods and no others.
 *
 * What native gets, measured (Documentation/receipts/s4-native-outcomes-v1.json,
 * outcome d3dx-constant-table): success and a table on every call, all of them
 * during start-up, each for a shader whose bytecode carries a CTAB comment;
 * and then **no method on any table** -- not GetConstantByName, not a Set, not
 * even Release -- through boot, the title and the City Escape attract demo's
 * gameplay. The oracle's d3dx9_42 prints every method under its own
 * ID3DXConstantTableImpl_ name, and neither traced log has one.
 *
 * Why the refusal cannot stand in for that. Its answer is E_FAIL and a null
 * table, where native's is success and a table, and the guest stores what it
 * gets at [object+50h] untested (runtime/sa2_imports.c says where). Eight
 * virtual methods use the stored table: the two release methods (00866020,
 * 00866060) test it for null, and the six that look constants up or set them
 * (008673d0, and 00867240 through 00867380) call through it with no null test
 * -- the jne at 00867254 is of a cached constant handle, not of the table. So
 * under the refusal those six fault on the read through the null table, and
 * the two release methods skip the Release they make on a table.
 *
 * What this object is, then: exactly the part of a table the measured span
 * shows, and nothing it does not.
 *
 *  - **It exists, and the call answers D3D_OK**, for a vertex or pixel
 *    shader's bytecode that carries a well-formed CTAB comment, found the way
 *    the oracle's D3DXFindShaderComment finds it (the entry point at the foot
 *    of this file). Native's table rests on both. Any other bytecode is
 *    refused by name, since native's answer for it -- D3D_OK with a null table
 *    for a version token that is not a vertex or pixel shader's,
 *    D3DXERR_INVALIDDATA for a missing or malformed comment -- is one this
 *    runtime was never measured giving.
 *  - **QueryInterface, AddRef and Release behave as COM requires.** The count
 *    starts at 1; AddRef and a successful QueryInterface add one; Release
 *    takes one and answers the count left, and the final Release destroys the
 *    table (life_destroy frees its owned CTAB block and marks its object slot destroyed). QueryInterface answers S_OK with the table itself for the three
 *    interfaces the oracle's d3dx9_42 answers -- IUnknown, ID3DXBuffer, and
 *    ID3DXConstantTable under d3dx9_42's own IID, below -- and E_NOINTERFACE
 *    with a null *ppv for any other. (The oracle's QueryInterface leaves *ppv
 *    unwritten on that failure, 0x1002e4fe in its syswow64 d3dx9_42.dll; COM
 *    requires the null, and only a caller that reads *ppv after a failed
 *    QueryInterface could tell the two apart.)
 *  - GetConstantByName and GetConstantDesc use validated, owned reflection.
 *    Names/defaults point into a complete CTAB snapshot. Numeric and sampler
 *    forms, nested members and arrays have table-local stable opaque handles.
 *    Other methods retain named refusal. New parser and optional-output/name
 *    expectations are reference-guided; held-native conformance remains unmeasured.
 *  - Calls after final release refuse. The object's guest block is freed by
 *    life_destroy. Object slots are never reused, so stale handles cannot revive
 *    when the heap reuses an old block. tableanswer breaks successful lookup;
 *    tablekeep deliberately retains both object and storage at zero references.
 */

/* The interfaces the table answers, as GUID bytes in memory order.
 * d3dx9_42 predates the SDK that gave ID3DXConstantTable a new IID: SDK 42
 * and earlier use {9DCA3190-38B9-4FC3-92E3-39C6DDFB358B}, SDK 43
 * {AB3C758F-093E-4356-B762-4DB18F1B3A01} (the zig tree's
 * any-windows-any/d3dx9shader.h keeps both under `#if D3DX_SDK_VERSION < 43`),
 * and the oracle's d3dx9_42.dll holds the first and not the second. */
static const uint8_t IID_UNKNOWN_BYTES[16] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46};
static const uint8_t IID_D3DXBUFFER_BYTES[16] = {
    0x08, 0xFB, 0xA5, 0x8B, 0x95, 0x51, 0xE2, 0x40,
    0xAC, 0x58, 0x0D, 0x98, 0x9C, 0x3A, 0x01, 0x02};
static const uint8_t IID_D3DXCONSTANTTABLE_42_BYTES[16] = {
    0x90, 0x31, 0xCA, 0x9D, 0xB9, 0x38, 0xC3, 0x4F,
    0x92, 0xE3, 0x39, 0xC6, 0xDD, 0xFB, 0x35, 0x8B};

#define TABLE_SLOT_GETCONSTANTBYNAME 9u   /* the one slot SA2_D3D9_BREAK=tableanswer answers */

/* One row per table built (sa2_d3d9.h, sa2_d3d9_ctab). A table's record keeps
 * its row, 1-based, in d[5]; 0 once the rows ran out. */
static sa2_d3d9_ctab g_ctab[SA2_D3D9_CTAB_ROWS];
static uint32_t      g_ctab_used, g_ctab_dropped;

static sa2_d3d9_ctab *table_row(const d3d_obj *o) {
    return o->d[5] ? &g_ctab[o->d[5] - 1u] : NULL;
}

/* The table a method was called on, alive; every other `this` stops the run
 * by name. The dispatcher has already counted a destroyed one in
 * calls_on_destroyed. */
static d3d_obj *table_live(d3d_ctx *c, const char *method) {
    char why[200];
    if (!c->obj || c->obj->type != T_CONSTANTTABLE) {
        snprintf(why, sizeof why, "`this` %#010x is not a constant table this runtime built",
                 c->self);
        refuse("ID3DXConstantTable", method, c->caller, why);
    }
    if (!c->obj->alive) {
        snprintf(why, sizeof why, "table %#010x was destroyed by its final Release at call %u; "
                 "a call through it after that is a use after free", c->self, c->obj->dead_seq);
        refuse("ID3DXConstantTable", method, c->caller, why);
    }
    return c->obj;
}

static uint32_t m_TableQueryInterface(d3d_ctx *c) {
    d3d_obj *o = table_live(c, "QueryInterface");
    const uint32_t riid = c->a[0], ppv = c->a[1];
    uint8_t iid[16];
    if (!riid || sa2_peek(riid, 16, iid) != SA2_OK)
        refuse("ID3DXConstantTable", "QueryInterface", c->caller,
               "riid is not sixteen readable bytes, and both COM and the oracle's d3dx9_42 "
               "read the interface ID through it");
    if (!ppv)
        refuse("ID3DXConstantTable", "QueryInterface", c->caller,
               "ppvObject is null: COM documents E_POINTER, the oracle's d3dx9_42 writes "
               "through it for the three interfaces it answers, and the two disagree");
    if (!memcmp(iid, IID_UNKNOWN_BYTES, 16) || !memcmp(iid, IID_D3DXBUFFER_BYTES, 16) ||
        !memcmp(iid, IID_D3DXCONSTANTTABLE_42_BYTES, 16)) {
        o->refs++;                                /* the AddRef a QueryInterface carries out */
        sa2_d3d9_ctab *r = table_row(o);
        if (r) r->refs = o->refs;
        pk32(ppv, o->addr);
        g_st.constant_table_qi_answered++;
        c->answered = SA2_D3D9_ANS_OBJECT;
        return SA2_D3D_OK;
    }
    pk32(ppv, 0);
    g_st.constant_table_qi_declined++;
    c->answered = SA2_D3D9_ANS_NOOP;
    return SA2_E_NOINTERFACE;
}
static uint32_t m_TableAddRef(d3d_ctx *c) {
    d3d_obj *o = table_live(c, "AddRef");
    sa2_d3d9_ctab *r = table_row(o);
    o->refs++;
    if (r) r->refs = o->refs;
    c->answered = SA2_D3D9_ANS_GUEST;
    return o->refs;
}
static uint32_t m_TableRelease(d3d_ctx *c) {
    d3d_obj *o = table_live(c, "Release");
    sa2_d3d9_ctab *r = table_row(o);
    c->answered = SA2_D3D9_ANS_GUEST;
    if (!o->refs) {
        /* Only a table tablekeep left alive at zero gets here. */
        if (!g_st.releases_past_zero++) g_st.releases_past_zero_first_caller = c->caller;
        return 0;
    }
    o->refs--;
    if (r) r->refs = o->refs;
    if (!o->refs && g_break != BRK_TABLEKEEP) {
        life_destroy(o);                          /* never bound, so no binding can hold it */
        g_st.constant_tables_destroyed++;
        if (r) { r->released_by = c->caller; r->released_seq = g_seq; }
    }
    return o->refs;
}

/* Owned reflection records. All pointer fields are 32-bit guest addresses.
 * Only the first twelve words are the public x86 D3DXCONSTANT_DESC. */
typedef struct {
    uint32_t desc[12];
    uint32_t parent, element, raw_count;
} table_constant;
_Static_assert(sizeof(table_constant) == 60u, "table handle stride");
#define TABLE_DEPTH_MAX 32u
_Static_assert(SA2_GUEST_END <= 0x80000000u, "opaque CTAB tokens cannot alias readable guest names");
/* A constant's opaque handle is 0x80000000 | (its table's pool index << 16) |
 * (its id - 1), so it carries fifteen bits of the index: a table can take an
 * index below 32768 only. The first block (16384) and the overflow block's
 * first 16384 indices fit; D3DXGetShaderConstantTable refuses by name before
 * building a table past them, rather than handing out handles two tables
 * share. Every recorded run builds its 26 tables at boot, at indices below
 * 100. */
#define TABLE_HANDLE_INDICES 32768u
_Static_assert(SA2_D3D9_OBJ_FIRST_COUNT <= TABLE_HANDLE_INDICES,
               "every first-block table index fits the handle's fifteen bits");
#define TABLE_NODES_MAX 65536u

typedef struct {
    const uint8_t *data;
    uint32_t bytes, used, cap, work;
    table_constant *nodes;
    const char *why;
} table_parser;
typedef struct { uint32_t regs, bytes, nodes; } table_shape;
static uint16_t table_u16(const uint8_t *p) { return (uint16_t)(p[0] | (uint16_t)p[1] << 8); }
static uint32_t table_u32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static int table_bad(table_parser *p, const char *why) { p->why = why; return 0; }
static int table_range(table_parser *p, uint32_t off, uint32_t n) {
    return off <= p->bytes && n <= p->bytes - off;
}
static int table_string(table_parser *p, uint32_t off) {
    return off >= 28u && off < p->bytes && memchr(p->data + off, 0, p->bytes - off);
}
static int table_add(uint32_t a, uint32_t b, uint32_t *out) {
    if (b > UINT32_MAX - a) return 0;
    *out = a + b; return 1;
}
static int table_mul(uint32_t a, uint32_t b, uint32_t *out) {
    if (a && b > UINT32_MAX / a) return 0;
    *out = a * b; return 1;
}
/* Numeric types and OBJECT/SAMPLER are explicit supported forms. Other
 * object types are refused, not interpreted as a numeric layout. */
static int table_type_shape(table_parser *p, uint32_t off, uint32_t set,
                            uint32_t *stack, uint32_t depth, int single, table_shape *out) {
    if (++p->work > TABLE_NODES_MAX*32u) return table_bad(p, "CTAB type validation work bound exceeded");
    if (depth == TABLE_DEPTH_MAX) return table_bad(p, "CTAB type nesting exceeds 32 levels");
    for (uint32_t i = 0; i < depth; i++)
        if (stack[i] == off) return table_bad(p, "CTAB type layout is cyclic");
    if (off < 28u || !table_range(p, off, 16u)) return table_bad(p, "CTAB TypeInfo is outside the comment");
    stack[depth] = off;
    const uint8_t *t = p->data + off;
    uint32_t cl = table_u16(t), ty = table_u16(t+2), rows = table_u16(t+4), cols = table_u16(t+6);
    uint32_t elements = table_u16(t+8), members = table_u16(t+10), mi = table_u32(t+12);
    if (!elements || !rows || !cols || rows > 4u || cols > 4u)
        return table_bad(p, "CTAB type dimensions/elements are invalid");
    table_shape one = {0,4,1};
    if (cl == 5u) {
        if (ty != 0u || rows != 1u || cols != 1u || !members || mi < 28u ||
            !table_range(p, mi, 8u * members))
            return table_bad(p, "CTAB struct member layout is invalid");
        for (uint32_t i = 0; i < members; i++) {
            const uint8_t *m = p->data + mi + 8u*i;
            uint32_t name = table_u32(m);
            if (!table_string(p, name) || !p->data[name])
                return table_bad(p, "CTAB member name is not a terminated nonempty string");
            for (uint32_t j = 0; j < i; j++) {
                uint32_t old = table_u32(p->data + mi + 8u*j);
                if (!strcmp((const char *)p->data+old, (const char *)p->data+name))
                    return table_bad(p, "CTAB struct has duplicate member names");
            }
            table_shape child;
            if (!table_type_shape(p, table_u32(m+4), set, stack, depth+1u, 0, &child)) return 0;
            if (!table_add(one.regs, child.regs, &one.regs) ||
                !table_add(one.nodes, child.nodes, &one.nodes))
                return table_bad(p, "CTAB struct extent overflows");
        }
    } else {
        if (members || mi) return table_bad(p, "CTAB nonstruct carries struct members");
        if (cl == 4u) {
            if (ty < 10u || ty > 14u || set != 3u || rows != 1u || cols != 1u)
                return table_bad(p, "CTAB object form is unsupported; only samplers are reflected");
            one.regs = 1u; one.bytes = 4u;
        } else {
            if (cl > 3u || ty < 1u || ty > 3u || set != ty-1u ||
                (cl == 0u && (rows != 1u || cols != 1u)) || (cl == 1u && rows != 1u))
                return table_bad(p, "CTAB numeric class/type/register set is invalid");
            one.bytes = rows * cols * 4u;
            one.regs = set == 0u ? rows * cols : cl == 2u ? rows : cl == 3u ? cols : 1u;
        }
    }
    if (single || elements == 1u) { *out = one; return 1; }
    if (!table_mul(one.regs, elements, &out->regs) ||
        !table_mul(one.bytes, elements, &out->bytes) ||
        !table_mul(one.nodes, elements, &out->nodes) ||
        !table_add(out->nodes, 1u, &out->nodes))
        return table_bad(p, "CTAB array extent overflows");
    return 1;
}
static int table_node(table_parser *p, uint32_t type, uint32_t name, uint32_t set,
                      uint32_t reg, uint32_t count, uint32_t def, uint32_t parent,
                      uint32_t element, int single) {
    uint32_t stack[TABLE_DEPTH_MAX]; table_shape shape;
    if (!table_type_shape(p, type, set, stack, 0, single, &shape)) return 0;
    if (p->used >= p->cap) return table_bad(p, "CTAB expanded record count disagrees");
    const uint8_t *t = p->data+type;
    uint32_t cl=table_u16(t), elements=single ? 1u : table_u16(t+8), members=table_u16(t+10);
    uint32_t id = ++p->used;
    table_constant *node = &p->nodes[id-1u];
    const uint32_t allocated = count < shape.regs ? count : shape.regs;
    const uint32_t public_count = set == 1u && !parent ? count : allocated;
    const uint32_t d[12] = {name, set, reg, public_count, cl, table_u16(t+2), table_u16(t+4),
                            table_u16(t+6), elements, members, shape.bytes, def};
    memcpy(node->desc, d, sizeof d); node->parent=parent; node->element=element;
    node->raw_count=count;
    const uint32_t unit = set == 0u || set == 3u ? 4u : 16u;
    if (elements > 1u) {
        table_shape one;
        if (!table_type_shape(p, type, set, stack, 0, 1, &one)) return 0;
        uint32_t consumed = 0;
        for (uint32_t i=0; i<elements; i++) {
            const uint32_t child = p->used;
            if (!table_node(p, type, name, set, reg + consumed, allocated - consumed,
                            def ? def + i*one.regs*unit : 0u, id, i, 1)) return 0;
            consumed += p->nodes[child].desc[3];
        }
    } else if (cl == 5u) {
        uint32_t mi=table_u32(t+12), consumed=0, logical=0;
        for (uint32_t i=0; i<members; i++) {
            const uint8_t *m=p->data+mi+8u*i; table_shape child;
            const uint32_t child_id = p->used;
            if (!table_type_shape(p, table_u32(m+4), set, stack, 0, 0, &child) ||
                !table_node(p, table_u32(m+4), table_u32(m), set, reg+consumed, allocated-consumed,
                            def ? def+logical*unit : 0u, id, UINT32_MAX, 0)) return 0;
            consumed += p->nodes[child_id].desc[3];
            logical += child.regs;
        }
    }
    return 1;
}
/* No object is published or out-pointer touched until the whole layout and
 * allocation size have been validated. The temporary records are host-only. */
static int table_parse(table_parser *p) {
    if (p->bytes < 28u || table_u32(p->data) != 28u)
        return table_bad(p,"owned CTAB header Size is invalid");
    uint32_t roots=table_u32(p->data+12), infos=table_u32(p->data+16), nodes=0;
    uint32_t creator=table_u32(p->data+4), target=table_u32(p->data+24);
    if (roots && (infos < 28u || infos > p->bytes ||
                  roots > (p->bytes-infos)/20u))
        return table_bad(p,"owned CTAB ConstantInfo extent is invalid");
    if ((creator && !table_string(p, creator)) || (target && !table_string(p, target)))
        return table_bad(p, "CTAB creator/target string is outside the comment");
    for (uint32_t i=0; i<roots; i++) {
        const uint8_t *c=p->data+infos+20u*i;
        uint32_t name=table_u32(c), set=table_u16(c+4), reg=table_u16(c+6), count=table_u16(c+8);
        uint32_t type=table_u32(c+12), def=table_u32(c+16), span;
        if (!table_string(p,name) || !p->data[name])
            return table_bad(p, "CTAB constant name is not a terminated nonempty string");
        for (uint32_t j=0; j<i; j++) {
            uint32_t old=table_u32(p->data+infos+20u*j);
            if (!strcmp((const char *)p->data+old,(const char *)p->data+name))
                return table_bad(p, "CTAB has duplicate root names");
        }
        uint32_t stack[TABLE_DEPTH_MAX]; table_shape shape;
        if (!table_type_shape(p,type,set,stack,0,0,&shape)) return 0;
        if (reg+count > 65536u ||
            !table_mul(shape.regs,set == 0u || set == 3u ? 4u : 16u,&span) ||
            (def && (def < 28u || !table_range(p,def,span))))
            return table_bad(p, "CTAB register/default extent is invalid");
        if (!table_add(nodes,shape.nodes,&nodes) || nodes > TABLE_NODES_MAX)
            return table_bad(p, "CTAB expanded layout exceeds 65536 records");
    }
    p->cap=nodes;
    p->nodes=nodes ? calloc(nodes,sizeof *p->nodes) : NULL;
    if (nodes && !p->nodes) return table_bad(p,"CTAB temporary allocation failed");
    for (uint32_t i=0; i<roots; i++) {
        const uint8_t *c=p->data+infos+20u*i;
        if (!table_node(p,table_u32(c+12),table_u32(c),table_u16(c+4),table_u16(c+6),
                        table_u16(c+8),table_u32(c+16),0,UINT32_MAX,0)) return 0;
    }
    return p->used == p->cap || table_bad(p,"CTAB expanded record count disagrees");
}
/* Object slots are never reused; handles survive guest-heap address reuse. */
static uint32_t table_handle(d3d_obj *o, uint32_t id) {
    uint32_t slot=o->idx;
    return 0x80000000u | (slot << 16) | (id-1u);
}
static table_constant table_record(d3d_obj *o, uint32_t id) {
    table_constant n; memset(&n,0,sizeof n);
    if (!id || id > o->d[7] || sa2_peek(o->mem+o->d[6]+(id-1u)*sizeof n,sizeof n,&n) != SA2_OK)
        gfault(o->mem,"reading an owned CTAB constant");
    return n;
}
static uint32_t table_child(d3d_obj *o, uint32_t parent, const char *name, uint32_t len,
                            uint32_t element) {
    for (uint32_t i=1; i<=o->d[7]; i++) {
        table_constant n=table_record(o,i);
        if (n.parent != parent || n.element != element) continue;
        if (element != UINT32_MAX) return i;
        uint32_t j=0; uint8_t ch=0;
        for (;j<len;j++) {
            if (sa2_peek(n.desc[0]+j,1,&ch)!=SA2_OK || ch!=(uint8_t)name[j]) break;
        }
        if (j==len && sa2_peek(n.desc[0]+len,1,&ch)==SA2_OK && !ch) return i;
    }
    return 0;
}
static uint32_t table_lookup(d3d_obj *o, uint32_t parent, const char *name) {
    const char *s=name;
    if (!*s) return 0;
    for (;;) {
        const char *start=s;
        while (*s && *s!='.' && *s!='[') s++;
        if (s!=start) parent=table_child(o,parent,start,(uint32_t)(s-start),UINT32_MAX);
        else return 0;
        if (!parent) return 0;
        while (*s=='[') {
            uint32_t index=0; s++;
            if (*s<'0' || *s>'9') return 0;
            do { if (index > (UINT32_MAX-(uint32_t)(*s-'0'))/10u) return 0;
                 index=index*10u+(uint32_t)(*s++-'0'); } while (*s>='0' && *s<='9');
            if (*s++!=']') return 0;
            parent=table_child(o,parent,NULL,0,index);
            if (!parent) return 0;
        }
        if (!*s) return parent;
        if (*s++!='.' || !*s) return 0;
    }
}
static uint32_t table_resolve(d3d_obj *o, uint32_t handle) {
    if (handle & 0x80000000u) {
        uint32_t slot=o->idx;
        if ((handle >> 16) != (0x8000u | slot)) return 0;
        uint32_t id=(handle & 0xffffu)+1u;
        return id<=o->d[7] ? id : 0u;
    }
    if (!handle) return 0;
    char name[1024];
    for (uint32_t i=0;i<sizeof name;i++) {
        if (handle>UINT32_MAX-i || sa2_peek(handle+i,1,&name[i])!=SA2_OK) return 0;
        if (!name[i]) return table_lookup(o,0,name);
    }
    return 0;
}
static uint32_t m_TableGetConstantByName(d3d_ctx *c) {
    d3d_obj *o=table_live(c,"GetConstantByName");
    uint32_t parent=c->a[0] ? table_resolve(o,c->a[0]) : 0;
    if ((c->a[0] & 0x80000000u) && !parent) {
        c->answered=SA2_D3D9_ANS_GUEST;
        return 0;
    }
    /* The held DLL falls back to root lookup for an invalid readable parent. */
    char name[1024];
    for (uint32_t i=0;i<sizeof name;i++) {
        if (!c->a[1] || c->a[1]>UINT32_MAX-i || sa2_peek(c->a[1]+i,1,&name[i])!=SA2_OK) return 0;
        if (!name[i]) {
            uint32_t id=table_lookup(o,parent,name);
            c->answered=SA2_D3D9_ANS_GUEST;
            return id ? table_handle(o,id) : 0;
        }
    }
    return 0;
}
static uint32_t m_TableGetConstantDesc(d3d_ctx *c) {
    d3d_obj *o=table_live(c,"GetConstantDesc");
    uint32_t id=table_resolve(o,c->a[0]);
    if (!id) return SA2_D3DERR_INVALIDCALL;
    table_constant n=table_record(o,id);
    if (c->a[1]) pkbuf(c->a[1],n.desc,48u);
    if (c->a[2]) pk32(c->a[2],1u);
    o->handed=1;
    c->answered=SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}

/* Bounded setters: six retained authored Wine root forms, through the supplied
 * device's actual guest vtable. Other forms are named runtime boundaries. */
static uint32_t table_device_register(d3d_ctx *c, uint32_t device, uint32_t slot,
                                      uint32_t index, uint32_t scratch, uint32_t count);
enum { TABLE_BOOL, TABLE_INT, TABLE_FLOAT, TABLE_VECTOR, TABLE_MATRIX };

static float table_input_float(uint32_t word) {
    float value; memcpy(&value, &word, 4u); return value;
}
static uint32_t table_float_word(float value) {
    uint32_t word; memcpy(&word, &value, 4u); return word;
}
static uint32_t table_integer_word(d3d_ctx *c, uint32_t word) {
    const float value = table_input_float(word);
    if (!isfinite(value) || value < -2147483648.0f || value >= 2147483648.0f)
        refuse("ID3DXConstantTable", "setter conversion", c->caller,
               "float-to-int outside the supported finite signed 32-bit range");
    return (uint32_t)(int32_t)value;
}
static uint32_t table_boolean_word(d3d_ctx *c, uint32_t word) {
    const float value = table_input_float(word);
    if (!isfinite(value))
        refuse("ID3DXConstantTable", "setter conversion", c->caller,
               "nonfinite float-to-bool is unobserved by this bounded setter");
    return value != 0.0f;
}
static uint32_t table_set(d3d_ctx *c, uint32_t input_kind, const char *method) {
    d3d_obj *o = table_live(c, method);
    const uint32_t id = table_resolve(o, c->a[1]);
    if (!id) { c->answered = SA2_D3D9_ANS_GUEST; return SA2_D3DERR_INVALIDCALL; }
    const table_constant node = table_record(o, id);
    const uint32_t version = o->d[3], bytecode_version = o->d[8];
    const uint32_t *d = node.desc;
    const uint32_t device = c->a[0], set = d[1], index = d[2], cl = d[4];
    const int scalar = cl == 0u && d[6] == 1u && d[7] == 1u && d[3] == 1u && d[10] == 4u &&
        ((set == 2u && d[5] == 3u) || (set == 1u && d[5] == 2u) || (set == 0u && d[5] == 1u));
    const int vector = set == 2u && cl == 1u && d[5] == 3u && d[6] == 1u &&
        d[7] == 3u && d[3] == 1u && d[10] == 12u;
    const int rows = set == 2u && cl == 2u && d[5] == 3u && d[6] == 2u &&
        d[7] == 3u && d[3] == 2u && d[10] == 24u;
    const int columns = set == 2u && cl == 3u && d[5] == 3u && d[6] == 2u &&
        d[7] == 3u && d[3] == 3u && d[10] == 24u;
    if (node.parent || node.element != UINT32_MAX || node.raw_count != d[3] || d[8] != 1u || d[9] ||
        !(scalar || vector || rows || columns) || version != bytecode_version ||
        (version != 0xfffe0300u && version != 0xffff0300u))
        refuse("ID3DXConstantTable", method, c->caller,
               "setter supports only the six observed root shapes with matching VS/PS 3.0 versions");
    if (index >= VSF_MAX || d[3] > VSF_MAX - index)
        refuse("ID3DXConstantTable", method, c->caller,
               "constant register extent exceeds the supported device bank");
    d3d_obj *known_device = obj_of(device);
    if (known_device && (known_device->type != T_DEVICE || !known_device->alive))
        refuse("ID3DXConstantTable", method, c->caller,
               "supplied known runtime device is wrong type or has reached final release");
    uint32_t input[16] = {0};
    if (input_kind <= TABLE_FLOAT) input[0] = c->a[2];
    else if (sa2_peek(c->a[2], input_kind == TABLE_VECTOR ? 16u : 64u, input) != SA2_OK)
        gfault(c->a[2], "snapshotting CTAB setter vector/matrix input");
    if ((vector && input_kind < TABLE_VECTOR) || ((rows || columns) && input_kind != TABLE_MATRIX)) {
        c->answered = SA2_D3D9_ANS_NOOP; return SA2_D3D_OK;
    }
    uint32_t payload[3][4] = {{0}}, calls = 1u;
    if (scalar) {
        uint32_t value = input[0];
        if (set == 2u) {
            if (input_kind == TABLE_BOOL) value = value ? 0x3f800000u : 0u;
            else if (input_kind == TABLE_INT) value = table_float_word((float)(int32_t)value);
        } else if (set == 1u) {
            if (input_kind == TABLE_BOOL) value = !!value;
            else if (input_kind != TABLE_INT) value = table_integer_word(c, value);
            payload[0][2] = 1u;                   /* observed INT scalar padding */
        } else if (input_kind == TABLE_INT) value = !!value;
        else if (input_kind != TABLE_BOOL) value = table_boolean_word(c, value);
        payload[0][0] = value;
    } else if (vector) memcpy(payload[0], input, 12u);
    else {
        calls = rows ? 2u : 3u;
        for (uint32_t r = 0; r < calls; r++)
            for (uint32_t k = 0; k < (rows ? 3u : 2u); k++)
                payload[r][k] = input[rows ? 4u*r+k : 4u*k+r];
    }
    const uint32_t slot = (version == 0xfffe0300u ? 94u : 109u) +
                          (set == 1u ? 2u : set == 0u ? 4u : 0u);
    const int observed_failure = version == 0xfffe0300u && scalar && set == 2u &&
        input_kind == TABLE_FLOAT && index == 7u && input[0] == 0x3fa00000u;
    const uint32_t scratch = sa2_heap_alloc_owned(SA2_D3D9_OWNER, 16u);
    if (!scratch)
        refuse("ID3DXConstantTable", method, c->caller,
               "owned 16-byte setter scratch could not be allocated before dispatch");
    for (uint32_t r = 0; r < calls; r++) {
        pkbuf(scratch, payload[r], 16u);
        const uint32_t hr = table_device_register(c, device, slot, index+r, scratch, 1u);
        if (hr && !(observed_failure && hr == SA2_D3DERR_INVALIDCALL))
            refuse("ID3DXConstantTable", method, c->caller,
                   "unobserved nonzero device sink result after dispatch; completed effects retained");
    }
    sa2_heap_free_owned(SA2_D3D9_OWNER, scratch);
    c->answered = SA2_D3D9_ANS_GUEST;
    return SA2_D3D_OK;
}
static uint32_t m_TableSetBool(d3d_ctx *c) { return table_set(c, TABLE_BOOL, "SetBool"); }
static uint32_t m_TableSetInt(d3d_ctx *c) { return table_set(c, TABLE_INT, "SetInt"); }
static uint32_t m_TableSetFloat(d3d_ctx *c) { return table_set(c, TABLE_FLOAT, "SetFloat"); }
static uint32_t m_TableSetVector(d3d_ctx *c) { return table_set(c, TABLE_VECTOR, "SetVector"); }
static uint32_t m_TableSetMatrix(d3d_ctx *c) { return table_set(c, TABLE_MATRIX, "SetMatrix"); }

static const d3d_row ROWS_CONSTANTTABLE[] = {
    {0,  2, 0, "QueryInterface", m_TableQueryInterface},
    {1,  0, 0, "AddRef", m_TableAddRef},
    {2,  0, 0, "Release", m_TableRelease},
    {3,  0, DF_OUT, "GetBufferPointer", NULL},
    {4,  0, 0, "GetBufferSize", NULL},
    {5,  1, DF_OUT, "GetDesc", NULL},
    {6,  3, DF_OUT, "GetConstantDesc", m_TableGetConstantDesc},
    {7,  1, 0, "GetSamplerIndex", NULL},
    {8,  2, 0, "GetConstant", NULL},
    {9,  2, 0, "GetConstantByName", m_TableGetConstantByName},
    {10, 2, 0, "GetConstantElement", NULL},
    {11, 1, 0, "SetDefaults", NULL},
    {12, 4, 0, "SetValue", NULL},
    {13, 3, 0, "SetBool", m_TableSetBool},
    {14, 4, 0, "SetBoolArray", NULL},
    {15, 3, 0, "SetInt", m_TableSetInt},
    {16, 4, 0, "SetIntArray", NULL},
    {17, 3, 0, "SetFloat", m_TableSetFloat},
    {18, 4, 0, "SetFloatArray", NULL},
    {19, 3, 0, "SetVector", m_TableSetVector},
    {20, 4, 0, "SetVectorArray", NULL},
    {21, 3, 0, "SetMatrix", m_TableSetMatrix},
    {22, 4, 0, "SetMatrixArray", NULL},
    {23, 4, 0, "SetMatrixPointerArray", NULL},
    {24, 3, 0, "SetMatrixTranspose", NULL},
    {25, 4, 0, "SetMatrixTransposeArray", NULL},
    {26, 4, 0, "SetMatrixTransposePointerArray", NULL},
    {0, 0, 0, NULL, NULL},
};

static d3d_type g_types[] = {
    {"IDirect3D9",                  ROWS_D3D9,         0, 0},
    {"IDirect3DDevice9",            ROWS_DEVICE,       0, 0},
    {"IDirect3DTexture9",           ROWS_TEXTURE,      0, 0},
    {"IDirect3DCubeTexture9",       ROWS_CUBETEX,      0, 0},
    {"IDirect3DVolumeTexture9",     ROWS_VOLTEX,       0, 0},
    {"IDirect3DSurface9",           ROWS_SURFACE,      0, 0},
    {"IDirect3DVolume9",            ROWS_VOLUME,       0, 0},
    {"IDirect3DVertexBuffer9",      ROWS_VERTEXBUFFER, 0, 0},
    {"IDirect3DIndexBuffer9",       ROWS_INDEXBUFFER,  0, 0},
    {"IDirect3DVertexDeclaration9", ROWS_VERTEXDECL,   0, 0},
    {"IDirect3DVertexShader9",      ROWS_SHADER,       0, 0},
    {"IDirect3DPixelShader9",       ROWS_SHADER,       0, 0},
    {"IDirect3DStateBlock9",        ROWS_STATEBLOCK,   0, 0},
    {"IDirect3DSwapChain9",         ROWS_SWAPCHAIN,    0, 0},
    {"IDirect3DQuery9",             ROWS_QUERY,        0, 0},
    {"ID3DXConstantTable",          ROWS_CONSTANTTABLE, 0, 0},
};
static uint32_t type_count(void) { return (uint32_t)(sizeof g_types / sizeof g_types[0]); }

static const d3d_row *row_of(uint32_t t, uint32_t slot) {
    if (t >= type_count()) return NULL;
    for (const d3d_row *p = g_types[t].rows; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

/* --------------------------------------------------------- the native */

/* The record is *reserved before the handler runs* and settled after it.
 * A handler that refuses raises a trap and never comes back, so a record
 * written only on the way out would leave the one call a reader most wants --
 * the one that stopped the run -- missing from the log entirely. The reserved
 * record is provisionally a refusal, which is what it is until a handler
 * returns and says otherwise. */
static sa2_d3d9_call    *g_cur;
static sa2_d3d9_slot_stat *g_cur_slot;
static uint32_t          g_cur_seq;

static uint32_t table_device_register(d3d_ctx *c, uint32_t device, uint32_t slot,
                                      uint32_t index, uint32_t scratch, uint32_t count) {
    const uint32_t vtable = pe32(device);
    if (vtable > UINT32_MAX - 4u * slot) gfault(vtable, "reading CTAB device vtable slot");
    const uint32_t target = pe32(vtable + 4u * slot);
    sa2_d3d9_call *saved_cur = g_cur;
    sa2_d3d9_slot_stat *saved_slot = g_cur_slot;
    const uint32_t saved_seq = g_cur_seq, saved_caller = g_call_caller;
    const uint32_t args[4] = {device, index, scratch, count};
    const uint32_t hr = sa2_guest_stdcall(c->s, target, 4u, args);
    g_cur = saved_cur; g_cur_slot = saved_slot;
    g_cur_seq = saved_seq; g_call_caller = saved_caller;
    return hr;
}


static void bump(uint8_t answered, int by) {
    uint32_t *f;
    switch (answered) {
    case SA2_D3D9_ANS_REFUSED: f = &g_st.refused;    break;
    case SA2_D3D9_ANS_NOOP:    f = &g_st.noop;       break;
    case SA2_D3D9_ANS_GUEST:   f = &g_st.from_guest; break;
    case SA2_D3D9_ANS_INVENT:  f = &g_st.invented;   break;
    case SA2_D3D9_ANS_OBJECT:  f = &g_st.objects;    break;
    default:                   f = &g_st.unset;      break;
    }
    *f = (uint32_t)((int)*f + by);
}

static void note_reserve(uint32_t t, uint32_t slot, uint32_t self, uint32_t caller,
                         const uint32_t *argv, uint32_t argc) {
    g_seq++;
    g_cur_seq = g_seq;
    g_st.method_calls++;
    bump(SA2_D3D9_ANS_REFUSED, +1);
    g_cur_slot = NULL;
    if (t < SA2_D3D9_MAX_TYPES && slot < SA2_D3D9_MAX_SLOTS) {
        sa2_d3d9_slot_stat *st = &g_slot[t][slot];
        if (!st->used) {
            st->used = 1;
            st->first_seq = g_seq;
            st->first_caller = caller;
            st->answered = SA2_D3D9_ANS_REFUSED;
            g_st.distinct_methods++;
            g_cur_slot = st;              /* only the first call owns the class */
        }
        st->calls++;
    }
    g_cur = NULL;
    if (g_log_used >= SA2_D3D9_LOG_MAX) return;
    sa2_d3d9_call *r = &g_log[g_log_used++];
    memset(r, 0, sizeof *r);
    r->seq = g_seq;
    r->caller = caller;
    r->self = self;
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->answered = SA2_D3D9_ANS_REFUSED;
    r->argc = (uint8_t)(argc > 8u ? 8u : argc);
    for (uint32_t k = 0; k < r->argc; k++) r->arg[k] = argv[k];
    g_cur = r;
}

static void note_settle(uint32_t ret, uint8_t answered) {
    bump(SA2_D3D9_ANS_REFUSED, -1);
    bump(answered, +1);
    if (g_cur) { g_cur->answered = answered; g_cur->ret = ret; }
    if (g_cur_slot) g_cur_slot->answered = answered;
    g_cur = NULL;
    g_cur_slot = NULL;
}

/* ---------------------------------------------------- the closed world
 *
 * The user's ruling of 2026-10-09 (Documentation/decisions/2026-10-09-S5-user-rulings.md,
 * item 1): "every Direct3D method not on the M7 list traps by default in the device,
 * naming the method. Gates and automated runs trap; live play logs loudly and reports
 * at exit instead of ending my session. Take the do-nothing success handlers off the
 * default path."
 *
 * The list is the `called` set of Documentation/receipts/d3d9-methods.json, generated
 * into runtime/sa2_d3d9_m7_list.h by tools/s4/m7_device_list.py as (type, slot) rows of
 * this file's own table, with both names, which listed_build() checks against g_types
 * and the rows before the first call is answered: a header generated from another table
 * stops the run by name instead of opening a slot nobody listed. It also recomputes the
 * header's digest from the rows it was compiled with (list_rows_digest), so a row added
 * or dropped by hand stops the run too, and the digest the run record carries is the
 * rows' own.
 *
 * A listed method takes exactly the path it took before the ruling; the only work the
 * closed world adds to it is one read of host memory (g_listed). An unlisted one, in
 * the mode in force (sa2_d3d9_world_mode):
 *
 *   trap  the default. The call is recorded in the log and the method surface, as
 *         every refusal is, counted below, and trapped by name with its caller,
 *         whatever its row's handler: m_ok, m_zero and every other handler answer
 *         only a listed method.
 *   log   live play, which the runtime knows by its own keyboard source: the live
 *         one is SA2_INPUT_DI_SOURCE naming a FIFO (runtime/sa2_dinput.c reads it the
 *         same way; tools/s5/play_live.py makes the FIFO), with SA2_INPUT_DI_SCRIPT
 *         unset, in a session that is not an automated one (automated_session: a
 *         play_live session with no person, --synthetic-keys, reads the same FIFO and
 *         traps). The method's first call is logged loudly to stderr; every call is
 *         counted; the call is then answered exactly as before the ruling, so a method
 *         with no handler still refuses by name; and a summary line goes to stderr at
 *         exit. Every such line starts "*** d3d9 CLOSED WORLD", which is what the
 *         runner that starts the game copies into its own output: tools/s4/loop_run.py,
 *         whose output a live session keeps as its game.log. The rows are in the run
 *         record's d3d9 block too (sa2_d3d9_closed_world_json).
 *   open  a unit build only: compiled with SA2_D3D9_OPEN_WORLD_UNIT_BUILD, which no
 *         game build defines (tools/s4/link_s4.py's flags), so a harness can drive a
 *         handler the list does not hold. Calls are counted as in log mode, silently.
 *
 * An unnamed slot refuses in every mode, as it always has (d3d9_native), and is
 * counted here too. */
static uint8_t           g_listed[SA2_D3D9_MAX_TYPES][SA2_D3D9_MAX_SLOTS];
static int               g_listed_ready;
#ifdef SA2_D3D9_OPEN_WORLD_UNIT_BUILD
static int               g_world = SA2_D3D9_WORLD_OPEN;
#else
static int               g_world = SA2_D3D9_WORLD_TRAP;
#endif
static int               g_world_summary_armed;
static int               g_live_source;     /* live_keyboard_source() at configuration */
static int               g_automated;       /* automated_session() at configuration */
static char              g_list_digest[65]; /* list_rows_digest() of the compiled rows */
static sa2_d3d9_unlisted g_unl[SA2_D3D9_UNLISTED_MAX];
static uint16_t          g_unl_row[SA2_D3D9_MAX_TYPES][SA2_D3D9_MAX_SLOTS];  /* row + 1 */
static uint32_t          g_unl_used, g_unl_calls;

static void list_rows_digest(char hex[65]);   /* below, beside the SHA-256 */

static void listed_build(void) {
    if (g_listed_ready) return;
    for (uint32_t i = 0; i < SA2_D3D9_M7_LIST_ROWS; i++) {
        const sa2_d3d9_m7_row *e = &SA2_D3D9_M7_LIST[i];
        const d3d_row *r = row_of(e->type, e->slot);
        if (e->type >= type_count() || strcmp(g_types[e->type].name, e->iface) ||
            !r || strcmp(r->name, e->method)) {
            sa2_trap_note("d3d9 closed world: runtime/sa2_d3d9_m7_list.h row %u, %s::%s, is "
                          "type %u slot %u, which this device's table names otherwise; run "
                          "tools/s4/m7_device_list.py", i, e->iface, e->method, e->type,
                          e->slot);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        g_listed[e->type][e->slot] = 1;
    }
    list_rows_digest(g_list_digest);
    if (strcmp(g_list_digest, SA2_D3D9_M7_LIST_DIGEST)) {
        sa2_trap_note("d3d9 closed world: runtime/sa2_d3d9_m7_list.h's %u rows hash to %.16s, "
                      "not its SA2_D3D9_M7_LIST_DIGEST %.16s: the rows were edited by hand; run "
                      "tools/s4/m7_device_list.py", SA2_D3D9_M7_LIST_ROWS, g_list_digest,
                      SA2_D3D9_M7_LIST_DIGEST);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    g_listed_ready = 1;
}

/* Live play, as the keyboard source names it: SA2_INPUT_DI_SOURCE is a FIFO and no
 * script is set beside it (runtime/sa2_dinput.c refuses the pair). dinput also refuses
 * the FIFO with its device off (SA2_DINPUT_DEVICE) and under a break; either ends the
 * run at configuration, before this mode can matter, and harness/crt_probe.c's
 * d3d9_world_is_the_keyboard_sources holds the two readings to each other. */
static int live_keyboard_source(void) {
    const char *src = getenv("SA2_INPUT_DI_SOURCE"), *script = getenv("SA2_INPUT_DI_SCRIPT");
    if (!src || !*src || (script && *script)) return 0;
#if defined(_WIN32)
    /* This host's FIFO is the session's named pipe, read as sa2_dinput.c checks it. Never
     * stat(): on Windows that opens the pipe as a client and closes it, which leaves the
     * session's one instance closing, so the game's own open at its first keyboard poll
     * finds it busy (the user's native run of 2026-10-12; Wine lets the next client in). */
    return sa2_win32_is_pipe_name(src) && sa2_win32_pipe_exists(src);
#else
    struct stat sb;
    return stat(src, &sb) == 0 && S_ISFIFO(sb.st_mode);
#endif
}

/* An automated session on the live keyboard source: tools/s5/play_live.py with
 * --synthetic-keys (verification with no person) feeds the same FIFO, and names the
 * recorder's counts file counts.synthetic.json instead of counts.json (play_live
 * paths(), "synthetic_counts"; game_command sets SA2_REC_COUNTS to it), which no other
 * runner does. The ruling traps automated runs, so such a session traps. The name is
 * play_live's, held to it by Documentation/scripts/tests/test_d3d9_closed_world.py. */
#define SA2_D3D9_AUTOMATED_COUNTS "counts.synthetic.json"
static int automated_session(void) {
    const char *c = getenv("SA2_REC_COUNTS");
    if (!c || !*c) return 0;
    const char *base = strrchr(c, '/');
    return !strcmp(base ? base + 1 : c, SA2_D3D9_AUTOMATED_COUNTS);
}

static const char *world_name(int w) {
    return w == SA2_D3D9_WORLD_LOG ? "log" : w == SA2_D3D9_WORLD_OPEN ? "open" : "trap";
}
static const char *unlisted_outcome(uint8_t o) {
    switch (o) {
    case SA2_D3D9_UNL_TRAPPED:  return "trapped";
    case SA2_D3D9_UNL_ANSWERED: return "answered";
    case SA2_D3D9_UNL_REFUSED:  return "refused";
    default:                    return "unnamed-slot";
    }
}

/* One more call of an unlisted (t, slot): its row, made at the first call. There is
 * a row for every (interface, slot) pair the thunks can name, so none is ever dropped. */
_Static_assert(SA2_D3D9_UNLISTED_MAX == SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS &&
               SA2_D3D9_UNLISTED_MAX <= 65535u,
               "a row for every thunk, indexed by a 16-bit row number");
static sa2_d3d9_unlisted *unlisted_note(uint32_t t, uint32_t slot, uint8_t outcome,
                                        uint32_t seq, uint32_t caller, uint32_t self) {
    g_unl_calls++;
    uint16_t *ix = &g_unl_row[t][slot];
    if (*ix) {
        sa2_d3d9_unlisted *r = &g_unl[*ix - 1u];
        r->calls++;
        return r;
    }
    sa2_d3d9_unlisted *r = &g_unl[g_unl_used++];
    *ix = (uint16_t)g_unl_used;
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->calls = 1;
    r->first_seq = seq;
    r->first_caller = caller;
    r->first_self = self;
    r->outcome = outcome;
    return r;
}

static void world_summary_at_exit(void) {
    if (!g_unl_used) {
        fprintf(stderr, "*** d3d9 CLOSED WORLD summary (live play): no method off the M7 list "
                        "was called (%u listed, runtime/sa2_d3d9_m7_list.h) ***\n",
                SA2_D3D9_M7_LIST_ROWS);
        return;
    }
    fprintf(stderr, "*** d3d9 CLOSED WORLD summary (live play): %u method(s) off the M7 list "
                    "called %u time(s), each of which a gate or automated run would trap:",
            g_unl_used, g_unl_calls);
    for (uint32_t i = 0; i < g_unl_used; i++) {
        const sa2_d3d9_unlisted *r = &g_unl[i];
        const d3d_row *row = row_of(r->type, r->slot);
        if (row) fprintf(stderr, "%s %s::%s x%u first from %#010x (%s)", i ? ";" : "",
                         g_types[r->type].name, row->name, r->calls, r->first_caller,
                         unlisted_outcome(r->outcome));
        else fprintf(stderr, "%s %s slot %u x%u first from %#010x (%s)", i ? ";" : "",
                     g_types[r->type].name, r->slot, r->calls, r->first_caller,
                     unlisted_outcome(r->outcome));
    }
    fprintf(stderr, " ***\n");
}

/* The closed world's answer to a named, unlisted row. Traps in trap mode and never
 * returns; otherwise counts the call, logs its first loudly in log mode, and returns
 * so the caller answers it exactly as before the ruling. */
static void closed_world(uint32_t t, uint32_t slot, const d3d_row *row, uint32_t pc,
                         uint32_t caller, uint32_t self, const uint32_t *argv) {
    if (g_world == SA2_D3D9_WORLD_TRAP) {
        note_reserve(t, slot, self, caller, argv, row->argc);
        unlisted_note(t, slot, SA2_D3D9_UNL_TRAPPED, g_cur_seq, caller, self);
        sa2_trap_note("d3d9 closed world: %s::%s called from %#010x on %#010x is off the M7 "
                      "list (runtime/sa2_d3d9_m7_list.h); gates and automated runs trap here",
                      g_types[t].name, row->name, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    /* note_reserve, which the caller makes next on both of its paths, takes g_seq + 1. */
    const sa2_d3d9_unlisted *r =
        unlisted_note(t, slot, row->fn ? SA2_D3D9_UNL_ANSWERED : SA2_D3D9_UNL_REFUSED,
                      g_seq + 1u, caller, self);
    if (g_world == SA2_D3D9_WORLD_LOG && r->calls == 1u)
        fprintf(stderr, "\n*** d3d9 CLOSED WORLD: %s::%s called from %#010x on %#010x is off "
                        "the M7 list (runtime/sa2_d3d9_m7_list.h). A gate or automated run "
                        "traps here; live play %s. Logged once; every call is counted, in the "
                        "run record's d3d9.closed_world and in a summary at exit. ***\n\n",
                g_types[t].name, row->name, caller, self,
                row->fn ? "goes on and answers it as before"
                        : "stops here as before, since this runtime has no handler for it");
}

static void *d3d9_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE;
    const uint32_t t = idx / SA2_D3D9_MAX_SLOTS, slot = idx % SA2_D3D9_MAX_SLOTS;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    if (t >= type_count()) {
        sa2_trap_note("d3d9 method thunk %#010x is past the %u fabricated interfaces",
                      pc, type_count());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const d3d_row *row = row_of(t, slot);
    uint32_t raw[16];
    memset(raw, 0, sizeof raw);
    /* An unnamed slot is never popped with a guessed argument count. The
     * `this` pointer is still read, because naming the object in the trap is
     * the whole value of getting here. */
    uint32_t want = row ? (uint32_t)row->argc + 1u : 1u;
    /* BRK_ARGC reinstates the exact defect `sa2_iface.c`'s survey had until
     * 2026-09-21: IDirect3D9::CheckDeviceMultiSampleType declared five
     * arguments instead of six. The pop below uses the same count, so the
     * caller's frame drifts four bytes per call, and two calls later
     * CreateDevice's DeviceType argument is read from the wrong stack slot. */
    if (g_break == BRK_ARGC && t == T_D3D9 && slot == 11u && row) want = 6u;
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
        unlisted_note(t, slot, SA2_D3D9_UNL_UNNAMED, g_cur_seq, caller, self);
        sa2_trap_note("%s vtable slot %u called from %#010x on %#010x: this runtime does not "
                      "name that slot, and popping a guessed argument count would corrupt "
                      "the caller's frame rather than answer wrongly",
                      g_types[t].name, slot, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    /* The closed world, before any handler or break is consulted: a method off the
     * M7 list traps here in trap mode, whatever its row holds. */
    listed_build();
    if (!g_listed[t][slot]) closed_world(t, slot, row, pc, caller, self, raw + 1);
    /* SA2_D3D9_BREAK=tableanswer answers one handlerless slot instead of
     * refusing it, below: the table comment's falsifier. */
    const int table_answers = g_break == BRK_TABLEANSWER &&
                              t == T_CONSTANTTABLE && slot == TABLE_SLOT_GETCONSTANTBYNAME;
    if (!row->fn && !table_answers) {
        note_reserve(t, slot, self, caller, raw + 1, row->argc);
        sa2_trap_note("%s::%s called from %#010x on %#010x: %s", g_types[t].name, row->name,
                      caller, self,
                      (row->flags & DF_OUT)
                          ? "it writes through a pointer the guest then dereferences, and this "
                            "runtime has no honest value to put there"
                          : "this runtime has no implementation for it");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    d3d_ctx c;
    c.s = s;
    c.self = self;
    c.obj = obj_of(self);
    /* Both counters are the host's, under every lifetime model; under
     * neverfree and ownzero an object is destroyed at its own count of zero
     * (sa2_d3d9.h says what each counter means in each model). */
    if (!c.obj) g_st.unknown_this++;
    else if (!c.obj->alive && !g_st.calls_on_destroyed++)
        g_st.calls_on_destroyed_first_caller = caller;
    c.a = raw + 1;
    c.argc = row->argc;
    c.caller = caller;
    c.answered = (row->flags & DF_CAP) ? SA2_D3D9_ANS_INVENT : SA2_D3D9_ANS_NOOP;
    note_reserve(t, slot, self, caller, raw + 1, row->argc);
    g_call_caller = caller;
    life_note(t, slot, caller);
    uint32_t ret;
    if (g_break == BRK_NOIMPL
        || (g_break == BRK_OUT && (row->flags & DF_OUT))) {
        /* BRK_NOIMPL is the previous slice's survey: answer S_OK, write no
         * out-parameter, run no handler. BRK_OUT is the narrower half of the
         * same thing -- only the methods with an out-parameter stop writing --
         * which is the shape that makes "recorded and answered" and "recorded
         * and answered *correctly*" separable. */
        ret = SA2_D3D_OK;
        c.answered = SA2_D3D9_ANS_NOOP;
    } else if (table_answers) {
        ret = 0;                                  /* a null D3DXHANDLE nobody measured */
        c.answered = SA2_D3D9_ANS_INVENT;
    } else {
        ret = row->fn(&c);
    }
    note_settle(ret, c.answered);
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
            for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
                const uint32_t va = SA2_D3D9_THUNK_BASE +
                                    (i * SA2_D3D9_MAX_SLOTS + k) * SA2_D3D9_THUNK_STRIDE;
                if (sa2_ft_add(va, d3d9_native, "d3d9-method", SA2_FN_NATIVE) < 0) {
                    sa2_trap_note("the function table refused the d3d9 method thunks");
                    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
                }
            }
        g_types_bound = 1;
    }
    const uint32_t vt = SA2_D3D9_VT_BASE + t * SA2_D3D9_VT_STRIDE;
    for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++)
        pk32(vt + 4u * k, SA2_D3D9_THUNK_BASE +
                          (t * SA2_D3D9_MAX_SLOTS + k) * SA2_D3D9_THUNK_STRIDE);
    g_types[t].vt = vt;
    return vt;
}

uint32_t sa2_d3d9_make_d3d9(uint32_t caller) {
    if (!g_enabled) return 0;
    g_call_caller = caller;
    if (g_d3d9_obj) { g_st.created++; return g_d3d9_obj; }
    d3d_obj *o = obj_new(T_D3D9, 0);
    g_d3d9_obj = o->addr;
    g_st.created++;
    return o->addr;
}

/* D3DXCreateTexture is documented as "adjust the parameters to what the device
 * supports, then call IDirect3DDevice9::CreateTexture". A device that
 * rasterises nothing supports every size and every format it has a byte count
 * for, so nothing is adjusted and the call is a pass-through -- which is
 * recorded as such: the log shows the d3dx entry point and the device method
 * it produced, so a reader is never left guessing which layer answered.
 *
 * Except the level count, which is not an adjustment but D3DX's published
 * rule, and is recorded as the cube and volume creators record it
 * (d3dx_levels): a MipLevels of 0 or D3DX_DEFAULT asks for the complete chain.
 * Until 2026-09-24 this path recorded D3DX_DEFAULT as it came, so
 * GetLevelCount answered 0xFFFFFFFF where native answers the chain. The
 * image's one call site, 0x00866a57, passes D3DX_DEFAULT whenever its caller
 * asks for 0 levels (0x00866a2b-0x00866a37), and stores what GetLevelCount
 * then answers through its caller's pointer (0x00866b4e-0x00866b53). */
static uint32_t d3dx_levels(uint32_t want, uint32_t w, uint32_t h);
uint32_t sa2_d3d9_d3dx_create_texture(uint32_t device, uint32_t w, uint32_t h,
                                      uint32_t levels, uint32_t usage, uint32_t format,
                                      uint32_t pool, uint32_t out_pp, uint32_t caller) {
    if (!g_enabled) return SA2_D3DERR_INVALIDCALL;
    g_call_caller = caller;
    if (!device || device != g_dev.dev) {
        sa2_trap_note("d3dx9_42!D3DXCreateTexture called from %#010x with device %#010x, "
                      "which is not the device this runtime created (%#010x)",
                      caller, device, g_dev.dev);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    if (!out_pp) return SA2_D3DERR_INVALIDCALL;
    const uint32_t bytes = surface_bytes(w, h, format);
    if (!bytes) {
        sa2_trap_note("d3dx9_42!D3DXCreateTexture from %#010x: format %#010x has no byte "
                      "count in this runtime, so a texture of %ux%u cannot be sized",
                      caller, format, w, h);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    d3d_obj *o = obj_new(T_TEXTURE, g_dev.dev);
    o->d[0] = w; o->d[1] = h; o->d[2] = d3dx_levels(levels, w, h);
    o->d[3] = usage; o->d[4] = format; o->d[5] = pool;
    obj_alloc(o, bytes);
    pk32(out_pp, o->addr);
    note_reserve(T_TEXTURE, 255u, o->addr, caller, NULL, 0);
    note_settle(SA2_D3D_OK, SA2_D3D9_ANS_OBJECT);
    return SA2_D3D_OK;
}

/* ------------------------------------------ textures the guest hands over
 *
 * `D3DXCreateTextureFromFileInMemoryEx`: the container, the size and the
 * format come out of the guest's own blob, and nothing else is answered.
 *
 * The engine reads a file out of its installation -- or a member out of one of
 * its `.pak` archives -- and asks d3dx9 to turn those bytes into a texture.
 * Every parameter that matters (width, height, mip count, surface format) is
 * `D3DX_DEFAULT` in the call, which means "whatever the file says", so the only
 * way to answer without inventing anything is to read the file's own header.
 *
 * Design 3.5 asks this stage's d3dx stubs for a recording device that captures
 * "textures by hash"; S5 owns the renderer and the extracted asset pipeline.
 * So this reads headers and records, and it decodes nothing:
 *
 *  - the container is identified from its own magic, never from the call;
 *  - the dimensions are the header's, and which header field each recorded
 *    value came from is recorded beside it (`dim_from`, `fmt_from`,
 *    `levels_from`);
 *  - the surface is guest memory, zero-filled, and **no pixel of the blob is
 *    ever written into it**. Every `sa2_d3d9_texsrc` row says so, rather than
 *    leaving a reader to assume it: a guest that locks one of these surfaces
 *    and reads gets zeroes where a real D3DX would have put the image, and S5
 *    inherits that blind if it is not written down;
 *  - a container this runtime cannot name, or a header it cannot parse,
 *    **refuses by name and quotes the blob's magic bytes**, so the next format
 *    is a work item with an address rather than a silent wrong answer. A
 *    fabricated size flowing into the guest's own texture bookkeeping is a
 *    divergence nobody downstream could distinguish from a real one --
 *    `SA2_D3D9_BREAK=texguess` fabricates one on purpose, so the claim is shown
 *    failing rather than asserted.
 *
 * This image passes both: sixteen DDS blobs and then a PNG, all from the same
 * call site at 0x008667f9. The DDS-only version answered the sixteen and
 * stopped at the seventeenth, which is why the wall looked like a PNG problem
 * -- the counts are in the record now, so the next one will not.
 */
#define DDS_F_MIPCOUNT 0x00020000u
#define DDPF_ALPHAPIXELS 0x1u
#define DDPF_FOURCC      0x4u
#define DDPF_RGB         0x40u
#define D3DX_DEFAULT   0xFFFFFFFFu
/* DDSCAPS2, the header word that says whether a DDS is a cube map or a volume.
 * It is the only thing in the container that distinguishes the three creators'
 * inputs from each other, which is why the cube and volume entry points check
 * it instead of trusting the call. */
#define DDSCAPS2_CUBEMAP 0x00000200u
#define DDSCAPS2_VOLUME  0x00200000u

/* ------------------------------------------------- the d3dx9 entry points
 *
 * Eleven of this image's imports are `d3dx9_42`; three were implemented and
 * eight raised a trap, which stopped the guest at the first cube map, the
 * first volume texture, the first mip-chain filter, the first surface blit and
 * the first screenshot. They are all implemented here, and the rule that
 * shapes every one of them is the one `runtime/sa2_dsound.h` states for the
 * whole project: **a stub may answer what it can derive and must refuse what
 * it would have to invent**, because an out-parameter the runtime never wrote
 * hands the guest whatever was on its stack.
 *
 * Applied here that comes out as three groups:
 *
 *  - the four creators derive everything -- the guest supplies the size and
 *    the format, or the blob's own header does -- so they invent nothing and
 *    write only the object pointer the caller asked for;
 *  - `D3DXFilterTexture`, `D3DXLoadSurfaceFromSurface` and the two
 *    `D3DXSave...ToFileA` have no out-parameter at all. Their whole effect is
 *    bytes: a filtered mip chain, a converted surface, a file on disk. This
 *    runtime decodes no image and opens no host file, so each is a recorded
 *    no-op success and each is *counted* as one, in its own field of
 *    `sa2_d3d9_stats`, rather than disappearing into "implemented";
 *  - `SA2_D3DX_STRICT=1` refuses all four of those by name. That switch is to
 *    the d3dx side what `SA2_D3D9_STRICT_CAPS` is to D3DCAPS9: the set of
 *    no-op successes is shown to be load-bearing rather than asserted to be.
 *
 * Every argument count below was read off the call site's own pushes as well
 * as the published signature -- see `sa2_d3d9.h` for the list of sites -- and
 * where the two disagreed the image won. A `__stdcall` import whose declared
 * count is wrong pops the wrong number of dwords and drifts the caller's
 * frame, which is the defect this whole file was written around.
 *
 * The synthetic slot ids these entry points log under sit above
 * SA2_D3D9_MAX_SLOTS, so a d3dx call appears in the ordered log with its
 * interface and its arguments but claims no vtable slot in the method surface
 * M7 counts. They descend from 255 in the order the entry points were added. */
enum {
    DXS_CREATE_TEXTURE   = 255u,
    DXS_TEX_FROM_MEM     = 254u,
    DXS_CREATE_CUBE      = 253u,
    DXS_CUBE_FROM_MEM    = 252u,
    DXS_CREATE_VOLUME    = 251u,
    DXS_VOLUME_FROM_MEM  = 250u,
    DXS_FILTER_TEXTURE   = 249u,
    DXS_LOAD_SURFACE     = 248u,
    DXS_SAVE_SURFACE     = 247u,
    DXS_SAVE_TEXTURE     = 246u,
    DXS_CONSTANT_TABLE   = 245u,
};

/* A device pointer that is not the one this runtime handed out is not ignored.
 * It is the same continuous argument-count check `sa2_d3d9_stats.unknown_this`
 * is for the vtable side: if an import's declared count were wrong, the frame
 * would drift and the next call's first dword would be some stack value rather
 * than the device. */
static void d3dx_require_device(const char *entry, uint32_t device, uint32_t caller) {
    if (device && device == g_dev.dev) return;
    sa2_trap_note("d3dx9_42!%s called from %#010x with device %#010x, which is not the "
                  "device this runtime created (%#010x)", entry, caller, device, g_dev.dev);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
}
/* The same, for the entry points whose first argument is a resource rather
 * than a device. Returns the object; never returns when it is not one. */
static d3d_obj *d3dx_require_object(const char *entry, const char *what, uint32_t addr,
                                    uint32_t caller) {
    d3d_obj *o = obj_of(addr);
    if (o) {
        if (!o->alive && !g_st.calls_on_destroyed++)     /* every model's, as the dispatcher's */
            g_st.calls_on_destroyed_first_caller = caller;
        return o;
    }
    g_st.unknown_this++;
    sa2_trap_note("d3dx9_42!%s called from %#010x with %s %#010x, which is not an object "
                  "this runtime fabricated", entry, caller, what, addr);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    return NULL;
}
static void d3dx_strict(const char *entry, uint32_t caller, const char *what) {
    if (!g_strict_d3dx) return;
    sa2_trap_note("d3dx9_42!%s called from %#010x: SA2_D3DX_STRICT is set and %s",
                  entry, caller, what);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
}

static uint32_t d3dx_type_of(uint32_t kind) {
    return kind == SA2_TEXK_CUBE   ? (uint32_t)T_CUBETEX
         : kind == SA2_TEXK_VOLUME ? (uint32_t)T_VOLTEX
                                   : (uint32_t)T_TEXTURE;
}
static const char *d3dx_kind_name(uint32_t kind) {
    return kind == SA2_TEXK_CUBE ? "cube" : kind == SA2_TEXK_VOLUME ? "volume" : "2D";
}
/* D3DX's published rule for MipLevels: 0 and D3DX_DEFAULT both ask for a
 * complete chain down to 1x1. The caller's own number is used as given. */
static uint32_t d3dx_levels(uint32_t want, uint32_t w, uint32_t h) {
    return (want == D3DX_DEFAULT || !want) ? full_chain(w, h) : want;
}
/* Every byte a complete resource of this kind would occupy: one 2D chain, six
 * of them for a cube, and for a volume a chain whose every level carries its
 * own depth. It is *recorded* rather than allocated -- the backing store is
 * taken on the first Lock, for the reason the from-memory creator gives below,
 * and a cube's six faces make eager allocation six times worse than the case
 * that already exhausted the arena. */
static uint32_t d3dx_resource_bytes(uint32_t kind, uint32_t w, uint32_t h, uint32_t depth,
                                    uint32_t fmt, uint32_t levels) {
    if (kind == SA2_TEXK_CUBE) return 6u * chain_bytes(w, h, fmt, levels);
    if (kind != SA2_TEXK_VOLUME) return chain_bytes(w, h, fmt, levels);
    uint32_t n = 0;
    for (uint32_t i = 0; i < (levels ? levels : 1u); i++)
        n += surface_bytes(level_dim(w, i), level_dim(h, i), fmt) *
             level_dim(depth ? depth : 1u, i);
    return n;
}
/* One object, in the shared record layout: width, height, levels, usage,
 * format, pool, and a volume's depth in d[6]. A cube's Size is its width and
 * its height, so the cube path needs no extra word. */
static d3d_obj *d3dx_new_texture(uint32_t kind, uint32_t w, uint32_t h, uint32_t depth,
                                 uint32_t levels, uint32_t usage, uint32_t fmt,
                                 uint32_t pool) {
    d3d_obj *o = obj_new(d3dx_type_of(kind), g_dev.dev);
    o->d[0] = w; o->d[1] = h; o->d[2] = levels ? levels : 1u;
    o->d[3] = usage; o->d[4] = fmt; o->d[5] = pool;
    o->d[6] = kind == SA2_TEXK_VOLUME ? (depth ? depth : 1u) : 1u;
    return o;
}

/* The two creators that take no blob. Both are documented as "adjust the
 * parameters to what the device supports, then call the matching
 * IDirect3DDevice9 method", and a device that rasterises nothing supports
 * every size and every format it has a byte count for -- so, exactly as
 * `sa2_d3d9_d3dx_create_texture` records for the 2D case, nothing is adjusted
 * and the call is a pass-through. Nothing is invented: every number is the
 * guest's own, except a MipLevels of 0 or D3DX_DEFAULT, which D3DX's published
 * rule turns into a complete chain. Both write one out-parameter, the object
 * pointer, and no other. */
static uint32_t d3dx_create_plain(uint32_t kind, const char *entry, uint32_t slot,
                                  uint32_t device, uint32_t w, uint32_t h, uint32_t depth,
                                  uint32_t want_levels, uint32_t usage, uint32_t fmt,
                                  uint32_t pool, uint32_t out_pp, uint32_t caller) {
    if (!g_enabled) return SA2_D3DERR_INVALIDCALL;
    g_call_caller = caller;
    d3dx_require_device(entry, device, caller);
    if (!out_pp) return SA2_D3DERR_INVALIDCALL;
    if (!w || !h || (kind == SA2_TEXK_VOLUME && !depth)) {
        sa2_trap_note("d3dx9_42!%s from %#010x: a %s texture of %ux%ux%u has a zero "
                      "dimension, and D3DX would have chosen one from a file it was not "
                      "given", entry, caller, d3dx_kind_name(kind), w, h, depth);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    if (!surface_bytes(w, h, fmt)) {
        sa2_trap_note("d3dx9_42!%s from %#010x: format %#010x has no byte count in this "
                      "runtime, so a %s texture of %ux%ux%u cannot be sized",
                      entry, caller, fmt, d3dx_kind_name(kind), w, h, depth);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    const uint32_t levels = d3dx_levels(want_levels, w, h);
    d3d_obj *o = d3dx_new_texture(kind, w, h, depth, levels, usage, fmt, pool);
    pk32(out_pp, o->addr);
    note_reserve(d3dx_type_of(kind), slot, o->addr, caller, NULL, 0);
    note_settle(SA2_D3D_OK, SA2_D3D9_ANS_OBJECT);
    return SA2_D3D_OK;
}

/* d3dx9_42!D3DXCreateCubeTexture(pDevice, Size, MipLevels, Usage, Format,
 *                                  Pool, ppCubeTexture) -- seven, and the call
 * site at 0x00866b97 pushes seven. */
uint32_t sa2_d3d9_d3dx_create_cube_texture(const uint32_t *a, uint32_t caller) {
    return d3dx_create_plain(SA2_TEXK_CUBE, "D3DXCreateCubeTexture", DXS_CREATE_CUBE,
                             a[0], a[1], a[1], 1u, a[2], a[3], a[4], a[5], a[6], caller);
}
/* d3dx9_42!D3DXCreateVolumeTexture(pDevice, Width, Height, Depth, MipLevels,
 *                                    Usage, Format, Pool, ppVolumeTexture)
 * -- nine, and the call site at 0x00866af1 pushes nine. */
uint32_t sa2_d3d9_d3dx_create_volume_texture(const uint32_t *a, uint32_t caller) {
    return d3dx_create_plain(SA2_TEXK_VOLUME, "D3DXCreateVolumeTexture", DXS_CREATE_VOLUME,
                             a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], caller);
}

/* ------------------------------------------------------------- SHA-256
 *
 * A texture is recorded by the hash every other record in this project uses,
 * so a blob can be matched against a file on disk without either side holding
 * the bytes. It runs over the guest's own memory in chunks; nothing is copied
 * out of the process and no part of a blob is written anywhere. */
typedef struct { uint32_t h[8]; uint64_t n; uint8_t buf[64]; uint32_t used; } sha256_ctx;

static const uint32_t SHA_K[64] = {
    0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
    0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
    0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
    0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
    0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
    0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
    0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
    0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u};

static uint32_t ror32(uint32_t x, unsigned n) { return (x >> n) | (x << (32u - n)); }

static void sha256_block(sha256_ctx *c, const uint8_t *p) {
    uint32_t w[64];
    for (unsigned i = 0; i < 16; i++)
        w[i] = ((uint32_t)p[4*i] << 24) | ((uint32_t)p[4*i+1] << 16) |
               ((uint32_t)p[4*i+2] << 8) | (uint32_t)p[4*i+3];
    for (unsigned i = 16; i < 64; i++) {
        const uint32_t s0 = ror32(w[i-15], 7) ^ ror32(w[i-15], 18) ^ (w[i-15] >> 3);
        const uint32_t s1 = ror32(w[i-2], 17) ^ ror32(w[i-2], 19) ^ (w[i-2] >> 10);
        w[i] = w[i-16] + s0 + w[i-7] + s1;
    }
    uint32_t a = c->h[0], b = c->h[1], cc = c->h[2], d = c->h[3];
    uint32_t e = c->h[4], f = c->h[5], g = c->h[6], hh = c->h[7];
    for (unsigned i = 0; i < 64; i++) {
        const uint32_t S1 = ror32(e, 6) ^ ror32(e, 11) ^ ror32(e, 25);
        const uint32_t ch = (e & f) ^ (~e & g);
        const uint32_t t1 = hh + S1 + ch + SHA_K[i] + w[i];
        const uint32_t S0 = ror32(a, 2) ^ ror32(a, 13) ^ ror32(a, 22);
        const uint32_t maj = (a & b) ^ (a & cc) ^ (b & cc);
        const uint32_t t2 = S0 + maj;
        hh = g; g = f; f = e; e = d + t1; d = cc; cc = b; b = a; a = t1 + t2;
    }
    c->h[0] += a; c->h[1] += b; c->h[2] += cc; c->h[3] += d;
    c->h[4] += e; c->h[5] += f; c->h[6] += g; c->h[7] += hh;
}
static void sha256_init(sha256_ctx *c) {
    static const uint32_t iv[8] = {0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,
                                   0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u};
    memcpy(c->h, iv, sizeof iv);
    c->n = 0; c->used = 0;
}
static void sha256_update(sha256_ctx *c, const uint8_t *p, uint32_t n) {
    c->n += n;
    while (n) {
        const uint32_t k = 64u - c->used < n ? 64u - c->used : n;
        memcpy(c->buf + c->used, p, k);
        c->used += k; p += k; n -= k;
        if (c->used == 64u) { sha256_block(c, c->buf); c->used = 0; }
    }
}
static void sha256_final(sha256_ctx *c, uint8_t out[32]) {
    const uint64_t bits = c->n * 8u;
    static const uint8_t pad = 0x80u;
    uint8_t z = 0;
    sha256_update(c, &pad, 1);
    while (c->used != 56u) sha256_update(c, &z, 1);
    uint8_t len[8];
    for (unsigned i = 0; i < 8; i++) len[i] = (uint8_t)(bits >> (56u - 8u * i));
    sha256_update(c, len, 8);       /* `bits` was taken before any padding */
    for (unsigned i = 0; i < 8; i++) {
        out[4*i]   = (uint8_t)(c->h[i] >> 24);
        out[4*i+1] = (uint8_t)(c->h[i] >> 16);
        out[4*i+2] = (uint8_t)(c->h[i] >> 8);
        out[4*i+3] = (uint8_t)c->h[i];
    }
}

/* The closed world's list digest (listed_build): the sha256 of the compiled rows, each
 * as "<type> <slot> <interface>::<method>" and a newline, the text
 * tools/s4/m7_device_list.py rows_text hashes into SA2_D3D9_M7_LIST_DIGEST. */
static void list_rows_digest(char hex[65]) {
    sha256_ctx c;
    sha256_init(&c);
    for (uint32_t i = 0; i < SA2_D3D9_M7_LIST_ROWS; i++) {
        const sa2_d3d9_m7_row *e = &SA2_D3D9_M7_LIST[i];
        char line[160];
        const int n = snprintf(line, sizeof line, "%u %u %s::%s\n", (unsigned)e->type,
                               (unsigned)e->slot, e->iface, e->method);
        sha256_update(&c, (const uint8_t *)line,
                      n < 0 ? 0u : (uint32_t)n < sizeof line ? (uint32_t)n : sizeof line - 1u);
    }
    uint8_t d[32];
    sha256_final(&c, d);
    for (unsigned i = 0; i < 32u; i++) snprintf(hex + 2u * i, 3, "%02x", d[i]);
}

/* The published NIST vectors, hashed by the code above at every run.
 *
 * A wrong hash function is the perfect silent defect: every row would carry a
 * plausible 64-character constant, every run would agree with every other run,
 * and nothing downstream could tell. The vectors are the cheapest thing that
 * can fail -- the second one is 56 bytes, which is exactly the length that
 * makes the padding spill into a second block, the one case a hand-written
 * implementation gets wrong. `crt_probe.c` reports the result as a clause. */
int sa2_d3d9_sha256_selftest(void) {
    static const struct { const char *in; const char *want; } v[] = {
        {"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
        {"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
        {"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
         "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"},
    };
    for (unsigned i = 0; i < sizeof v / sizeof v[0]; i++) {
        sha256_ctx c;
        uint8_t out[32];
        char hex[65];
        sha256_init(&c);
        sha256_update(&c, (const uint8_t *)v[i].in, (uint32_t)strlen(v[i].in));
        sha256_final(&c, out);
        for (unsigned k = 0; k < 32; k++) snprintf(hex + 2 * k, 3, "%02x", out[k]);
        if (strcmp(hex, v[i].want) != 0) return 0;
    }
    return 1;
}

/* ----------------------------------------------------- the record table */

#define TEXSRC_MAX 2048u
#define FAKE_PIXEL_MAX 65536u       /* SA2_D3D9_BREAK=fakepixels; see below */
static sa2_d3d9_texsrc g_tex[TEXSRC_MAX];
static sa2_d3d9_texsrc g_tex_overflow;     /* rows past the table's capacity */
static uint32_t        g_tex_used;

/* `*idx1` is the row's 1-based index, or 0 when the table is full and the row
 * is the shared overflow one -- which an object must not point at, because a
 * later call would rewrite it. */
static sa2_d3d9_texsrc *tex_row_new(uint32_t *idx1) {
    sa2_d3d9_texsrc *r;
    if (g_tex_used < TEXSRC_MAX) { r = &g_tex[g_tex_used++]; *idx1 = g_tex_used; }
    else { r = &g_tex_overflow; *idx1 = 0; g_st.tex_rows_dropped++; }
    memset(r, 0, sizeof *r);
    r->seq = g_st.tex_from_file + g_st.tex_from_file_refused + 1u;
    return r;
}

const char *sa2_d3d9_container_name(uint32_t container) {
    switch (container) {
    case SA2_TEXC_DDS:  return "dds";
    case SA2_TEXC_PNG:  return "png";
    case SA2_TEXC_BMP:  return "bmp";
    case SA2_TEXC_JPEG: return "jpeg";
    case SA2_TEXC_GIF:  return "gif";
    case SA2_TEXC_TIFF: return "tiff";
    case SA2_TEXC_HDR:  return "radiance-hdr";
    case SA2_TEXC_PPM:  return "ppm";
    default:            return "unnamed";
    }
}
const char *sa2_d3d9_provenance_name(uint32_t p) {
    switch (p) {
    case SA2_TEXP_STATED:  return "the file's header states it";
    case SA2_TEXP_DERIVED: return "derived from the header by D3DX's published rule";
    case SA2_TEXP_CALLER:  return "the guest passed it explicitly";
    case SA2_TEXP_API:     return "D3DX's documented default for D3DX_DEFAULT";
    case SA2_TEXP_INVENT:  return "invented by SA2_D3D9_BREAK=texguess";
    default:               return "none";
    }
}
static void tex_row_lock(uint32_t idx1) {
    if (idx1 && idx1 <= g_tex_used) g_tex[idx1 - 1u].locks++;
}
static uint32_t count_nonzero(uint32_t a, uint32_t len) {
    uint8_t buf[4096];
    uint32_t off = 0, n = 0;
    while (len) {
        const uint32_t k = len > sizeof buf ? (uint32_t)sizeof buf : len;
        if (sa2_peek(a + off, k, buf) != SA2_OK) return n;
        for (uint32_t j = 0; j < k; j++) if (buf[j]) n++;
        off += k; len -= k;
    }
    return n;
}
/* A file texture's store is about to go back to the guest heap at its final
 * release, and the game releases nearly every texture before the probe reads
 * the rows, at shutdown. Counted after the free there would be nothing to
 * count, so `no_texture_surface_holds_bytes_this_runtime_wrote` (crt_probe.c)
 * would pass over every released texture whatever its store held. Counted
 * here, the row keeps the answer. It is taken for a locked texture too, though
 * the clause does not read one. It reads guest bytes and writes only the
 * host's row, so nothing the guest sees depends on it. The clause cannot show
 * that this count is read -- under fakepixels it fails on the break's own flag
 * first -- so tools/s4/d3d9_life_check.py's fakepixels run does: every file
 * texture holds bytes there, and a released one reads them only from here. */
static void tex_row_released(const d3d_obj *o) {
    if (!o->src_idx || o->src_idx > g_tex_used || !o->mem || !o->mem_len) return;
    sa2_d3d9_texsrc *r = &g_tex[o->src_idx - 1u];
    r->released_nonzero = count_nonzero(o->mem, o->mem_len);
    r->released = 1;
}
/* A verbatim D3DXLoadSurfaceFromSurface wrote into a file texture's store
 * without a Lock. */
static void tex_row_copied(uint32_t idx1) {
    if (idx1 && idx1 <= g_tex_used) g_tex[idx1 - 1u].guest_copied = 1;
}
uint32_t sa2_d3d9_texsrc_count(void) { return g_tex_used; }
/* The row, with the two fields the object owns refreshed from it. A texture's
 * backing store is taken on its first Lock and not before, so a row written at
 * creation would otherwise report the surface it had then -- none -- for the
 * rest of the run. Read from the object, so what the record says about the
 * surface is what the surface is. */
const sa2_d3d9_texsrc *sa2_d3d9_texsrc_at(uint32_t i) {
    if (i >= g_tex_used) return NULL;
    sa2_d3d9_texsrc *r = &g_tex[i];
    const d3d_obj *o = r->obj ? obj_of(r->obj) : NULL;
    if (o) { r->surface = o->mem; r->reserved = o->mem_len; }
    return r;
}
uint32_t sa2_d3d9_texsrc_nonzero(uint32_t i) {
    if (!sa2_d3d9_texsrc_at(i)) return 0;
    const sa2_d3d9_texsrc *r = &g_tex[i];
    if (r->surface && r->reserved) return count_nonzero(r->surface, r->reserved);
    return r->released ? r->released_nonzero : 0;
}

/* ----------------------------------------------------------- containers */

/* The container a blob's own first bytes name. A format named here but not
 * parsed below still refuses -- but it refuses saying *which* format it is,
 * which is the difference between a work item and a mystery. */
static uint8_t container_of(const uint8_t *h, uint32_t n) {
    /* The refusal below is the whole of this function's safety story, and every
     * blob this image passes happens to be one of the two containers that
     * parse -- so the refusal is code no run executes, which is code nobody
     * has checked. This break makes the first texture take it. */
    if (g_break == BRK_TEXUNKNOWN) return SA2_TEXC_UNKNOWN;
    if (n >= 4 && !memcmp(h, "DDS ", 4)) return SA2_TEXC_DDS;
    if (n >= 8 && !memcmp(h, "\x89PNG\r\n\x1a\n", 8)) return SA2_TEXC_PNG;
    if (n >= 2 && h[0] == 'B' && h[1] == 'M') return SA2_TEXC_BMP;
    if (n >= 3 && h[0] == 0xFFu && h[1] == 0xD8u && h[2] == 0xFFu) return SA2_TEXC_JPEG;
    if (n >= 6 && !memcmp(h, "GIF8", 4)) return SA2_TEXC_GIF;
    if (n >= 4 && (!memcmp(h, "II*\0", 4) || !memcmp(h, "MM\0*", 4))) return SA2_TEXC_TIFF;
    if (n >= 10 && !memcmp(h, "#?RADIANCE", 10)) return SA2_TEXC_HDR;
    if (n >= 2 && h[0] == 'P' && h[1] >= '1' && h[1] <= '6') return SA2_TEXC_PPM;
    return SA2_TEXC_UNKNOWN;
}
/* D3DXIMAGE_FILEFORMAT, as the guest reads it out of a D3DXIMAGE_INFO. */
static uint32_t d3dx_iff(uint32_t container) {
    switch (container) {
    case SA2_TEXC_BMP:  return 0u;
    case SA2_TEXC_JPEG: return 1u;
    case SA2_TEXC_PNG:  return 3u;
    case SA2_TEXC_DDS:  return 4u;
    case SA2_TEXC_PPM:  return 5u;
    case SA2_TEXC_HDR:  return 7u;
    default:            return 0xFFFFFFFFu;
    }
}

/* A DDS pixel-format block as a D3DFORMAT, or 0 when this runtime cannot name
 * it. Masks rather than bit counts alone, because 32-bit A8R8G8B8 and
 * X8R8G8B8 differ only in the alpha mask and the guest blends on it. */
static uint32_t dds_format(uint32_t pf_flags, uint32_t fourcc, uint32_t bits,
                           uint32_t rm, uint32_t gm, uint32_t bm, uint32_t am) {
    if (pf_flags & DDPF_FOURCC) return fmt_block(fourcc) ? fourcc : 0u;
    if (!(pf_flags & DDPF_RGB)) return 0u;
    if (bits == 32) {
        if (rm == 0x00FF0000u && gm == 0x0000FF00u && bm == 0x000000FFu)
            return (pf_flags & DDPF_ALPHAPIXELS) && am == 0xFF000000u ? 21u : 22u;
        if (rm == 0x000000FFu && gm == 0x0000FF00u && bm == 0x00FF0000u)
            return (pf_flags & DDPF_ALPHAPIXELS) && am == 0xFF000000u ? 32u : 33u;
        return 0u;
    }
    if (bits == 24 && rm == 0x00FF0000u) return 20u;                       /* R8G8B8 */
    if (bits == 16) {
        if (rm == 0xF800u && gm == 0x07E0u && bm == 0x001Fu) return 23u;   /* R5G6B5 */
        if (rm == 0x7C00u && gm == 0x03E0u && bm == 0x001Fu)
            return (pf_flags & DDPF_ALPHAPIXELS) && am == 0x8000u ? 25u : 24u;
        if (rm == 0x0F00u && gm == 0x00F0u && bm == 0x000Fu)
            return (pf_flags & DDPF_ALPHAPIXELS) && am == 0xF000u ? 26u : 30u;
        return 0u;
    }
    return 0u;
}

/* Does this PNG carry transparency? Colour types 4 and 6 do by construction;
 * types 0, 2 and 3 do only when a tRNS chunk is present, and D3DX picks
 * A8R8G8B8 or X8R8G8B8 on exactly that. Walking the chunk *headers* -- four
 * bytes of length and four of type, stopping at the first IDAT -- is reading
 * the container's own index, the same thing the DDS path does with its header,
 * and no compressed byte is touched. */
static int png_has_trns(uint32_t src, uint32_t src_len) {
    uint32_t off = 8;                       /* past the signature */
    for (unsigned guard = 0; guard < 64u && off + 12u <= src_len; guard++) {
        uint8_t ch[8];
        if (sa2_peek(src + off, 8, ch) != SA2_OK) return -1;
        const uint32_t len = ((uint32_t)ch[0] << 24) | ((uint32_t)ch[1] << 16) |
                             ((uint32_t)ch[2] << 8) | (uint32_t)ch[3];
        if (!memcmp(ch + 4, "tRNS", 4)) return 1;
        if (!memcmp(ch + 4, "IDAT", 4) || !memcmp(ch + 4, "IEND", 4)) return 0;
        if (len > src_len - off - 12u) return -1;   /* a length past the blob */
        off += 12u + len;                   /* length + type + data + crc */
    }
    return 0;
}

/* -------------------------------------------- the import's own entry point */

/* The three `FromFileInMemoryEx` creators differ only in how many dimensions
 * they carry, so they are one function and three argument maps rather than
 * three copies of a header parser. `kind` is what the guest asked for; the
 * blob's own header is what answers. */
typedef struct {
    uint32_t    kind;               /* SA2_TEXK_* */
    uint32_t    slot;               /* the synthetic slot the call logs under */
    const char *entry;              /* the import's name, for every trap note */
    uint32_t    device, src, src_len;
    uint32_t    want_w, want_h, want_d, want_levels;
    uint32_t    usage, want_fmt, pool;
    uint32_t    srcinfo, out_pp;
} d3dx_mem;

static uint32_t d3dx_from_memory(const d3dx_mem *p, uint32_t caller) {
    if (!g_enabled) return SA2_D3DERR_INVALIDCALL;
    g_call_caller = caller;
    const uint32_t kind = p->kind;
    const uint32_t src = p->src, src_len = p->src_len;
    const uint32_t want_w = p->want_w, want_h = p->want_h, want_levels = p->want_levels;
    const uint32_t usage = p->usage, want_fmt = p->want_fmt, pool = p->pool;
    const uint32_t srcinfo = p->srcinfo, out_pp = p->out_pp;
    d3dx_require_device(p->entry, p->device, caller);
    if (!out_pp) return SA2_D3DERR_INVALIDCALL;

    /* The row is taken first and filled as the call proceeds, so a refused
     * call leaves a record of what it refused and why. A run that ends in a
     * refusal is the run whose numbers are worth the most. */
    uint32_t row_idx = 0;
    sa2_d3d9_texsrc *r = tex_row_new(&row_idx);
    r->caller = caller;
    r->kind = (uint8_t)kind;
    r->src = src;
    r->src_len = src_len;
    r->want_w = want_w; r->want_h = want_h; r->want_d = p->want_d;
    r->want_levels = want_levels; r->want_fmt = want_fmt;

    uint8_t hdr[128];
    const uint32_t hdr_len = src_len < sizeof hdr ? src_len : (uint32_t)sizeof hdr;
    memset(hdr, 0, sizeof hdr);
    if (!src || src_len < 16u || sa2_peek(src, hdr_len, hdr) != SA2_OK) {
        g_st.tex_from_file_refused++;
        r->refused = 1;
        sa2_trap_note("d3dx9_42!%s from %#010x: the source blob at %#010x is %u bytes, too "
                      "short or unreadable for any image header",
                      p->entry, caller, src, src_len);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    memcpy(r->magic, hdr, 8);
    r->container = container_of(hdr, hdr_len);

    /* The hash covers every byte the guest passed, read in place. */
    {
        sha256_ctx sc;
        sha256_init(&sc);
        uint8_t chunk[4096];
        uint32_t off = 0;
        while (off < src_len) {
            const uint32_t k = src_len - off > sizeof chunk ? (uint32_t)sizeof chunk
                                                            : src_len - off;
            if (sa2_peek(src + off, k, chunk) != SA2_OK) {
                g_st.tex_from_file_refused++;
                r->refused = 1;
                r->hashed_bytes = off;
                sa2_trap_note("d3dx9_42!%s from %#010x: the %u-byte %s blob at %#010x stops "
                              "being readable %u bytes in, so it cannot be recorded by hash",
                              p->entry, caller, src_len,
                              sa2_d3d9_container_name(r->container), src, off);
                sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
            }
            sha256_update(&sc, chunk, k);
            off += k;
        }
        sha256_final(&sc, r->sha256);
        r->hashed_bytes = src_len;
        g_st.tex_bytes_hashed += src_len;
    }

    uint32_t file_w = 0, file_h = 0, file_depth = 1, file_levels = 1, fmt = 0;

    if (g_break == BRK_TEXGUESS) {
        /* The break this file's refusal exists to be measured against: no
         * header is read and a plausible size is fabricated. Every later
         * GetLevelDesc, LockRect pitch and mip walk is then wrong, and nothing
         * downstream can tell. */
        file_w = file_h = 256u; fmt = 21u;
        r->dim_from = SA2_TEXP_INVENT;
        r->fmt_from = SA2_TEXP_INVENT;
    } else if (r->container == SA2_TEXC_DDS) {
        uint32_t d[32];
        for (unsigned i = 0; i < 32; i++) memcpy(&d[i], hdr + 4u * i, 4);
        if (hdr_len < 128u || d[1] != 124u) {
            g_st.tex_from_file_refused++;
            r->refused = 1;
            sa2_trap_note("d3dx9_42!%s from %#010x: the %u-byte blob at %#010x is a DDS whose "
                          "header size field reads %u and not 124; this runtime will not read "
                          "a header it cannot trust",
                          p->entry, caller, src_len, src, hdr_len >= 128u ? d[1] : 0u);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        file_h = d[3]; file_w = d[4];
        file_depth = d[6] ? d[6] : 1u;
        file_levels = (d[2] & DDS_F_MIPCOUNT) && d[7] ? d[7] : 1u;
        fmt = dds_format(d[20], d[21], d[22], d[23], d[24], d[25], d[26]);
        r->file_caps2 = d[28];
        r->dim_from = SA2_TEXP_STATED;
        r->fmt_from = SA2_TEXP_STATED;
        if (!file_w || !file_h || !fmt) {
            g_st.tex_from_file_refused++;
            r->refused = 1;
            sa2_trap_note("d3dx9_42!%s from %#010x: the DDS at %#010x is %ux%u with "
                          "pixel-format flags %#010x, fourcc %#010x, %u bits -- this runtime "
                          "has no D3DFORMAT for it and will not guess a pitch",
                          p->entry, caller, src, file_w, file_h, d[20], d[21], d[22]);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        /* dwCaps2 is the container's own statement of what it holds, and it is
         * checked against the entry point the guest called rather than taken
         * on trust. A 2D DDS reaching the cube creator has five sixths of its
         * faces missing and a volume DDS has a depth the cube path would drop:
         * either way the size this runtime would report is a fabrication, and
         * fabricating a size is the one thing this function exists not to do.
         * The 2D path is deliberately not checked the other way -- it is the
         * path sixteen textures already take, and narrowing it here would be a
         * behaviour change dressed as a check. */
        if ((kind == SA2_TEXK_CUBE   && !(d[28] & DDSCAPS2_CUBEMAP)) ||
            (kind == SA2_TEXK_VOLUME && !(d[28] & DDSCAPS2_VOLUME))) {
            g_st.tex_from_file_refused++;
            r->refused = 1;
            sa2_trap_note("d3dx9_42!%s from %#010x: the DDS at %#010x is %ux%ux%u with "
                          "dwCaps2 %#010x, which does not declare it a %s map; this runtime "
                          "will not invent the faces or slices the caller asked for",
                          p->entry, caller, src, file_w, file_h, file_depth, d[28],
                          d3dx_kind_name(kind));
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        r->parsed = 1;
    } else if (kind != SA2_TEXK_2D) {
        /* Only DDS carries a cube map or a volume. D3DX can build a 2D texture
         * out of any of the containers `container_of` names, and the PNG path
         * below does; it cannot build six faces or a stack of slices out of
         * one flat image, and neither will this. */
        g_st.tex_from_file_refused++;
        r->refused = 1;
        sa2_trap_note("d3dx9_42!%s from %#010x: the %u-byte blob at %#010x reads as %s, and "
                      "a %s texture's extra dimension exists only in a DDS -- this runtime "
                      "will not invent it from a flat image",
                      p->entry, caller, src_len, src,
                      sa2_d3d9_container_name(r->container), d3dx_kind_name(kind));
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    } else if (r->container == SA2_TEXC_PNG) {
        /* IHDR is fixed and first: length, type, width, height, bit depth,
         * colour type, compression, filter, interlace. Reading it is reading a
         * header, which is what the DDS path already does; decoding the image
         * is S5's and nothing here does it. */
        if (hdr_len < 33u || memcmp(hdr + 12, "IHDR", 4) != 0) {
            g_st.tex_from_file_refused++;
            r->refused = 1;
            sa2_trap_note("d3dx9_42!%s from %#010x: the %u-byte PNG at %#010x does not begin "
                          "with an IHDR chunk (bytes 12..15 are %02x%02x%02x%02x), so its "
                          "size cannot be read",
                          p->entry, caller, src_len, src, hdr[12], hdr[13], hdr[14], hdr[15]);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        file_w = ((uint32_t)hdr[16] << 24) | ((uint32_t)hdr[17] << 16) |
                 ((uint32_t)hdr[18] << 8) | (uint32_t)hdr[19];
        file_h = ((uint32_t)hdr[20] << 24) | ((uint32_t)hdr[21] << 16) |
                 ((uint32_t)hdr[22] << 8) | (uint32_t)hdr[23];
        r->png_bit_depth = hdr[24];
        r->png_colour_type = hdr[25];
        r->png_interlace = hdr[28];
        r->dim_from = SA2_TEXP_STATED;
        const int trns = png_has_trns(src, src_len);
        if (trns < 0) {
            g_st.tex_from_file_refused++;
            r->refused = 1;
            sa2_trap_note("d3dx9_42!%s from %#010x: the %u-byte PNG at %#010x has a chunk "
                          "index this runtime cannot walk, so whether it carries transparency "
                          "is unknown and the surface format would be a guess",
                          p->entry, caller, src_len, src);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        r->png_trns = (uint32_t)trns;
        /* D3DX loads a PNG into the smallest 32-bit surface that keeps it:
         * A8R8G8B8 when the image has an alpha channel or a tRNS chunk,
         * X8R8G8B8 otherwise. Both are four bytes per pixel, so the pitch --
         * the number a wrong guess would corrupt -- is the same either way;
         * the choice only decides whether the guest's blending sees alpha. */
        switch (r->png_colour_type) {
        case 0: case 2: fmt = trns ? 21u : 22u; break;      /* grey, truecolour */
        case 3:         fmt = trns ? 21u : 22u; break;      /* palette */
        case 4: case 6: fmt = 21u; break;                   /* with alpha */
        default:        fmt = 0u; break;
        }
        r->fmt_from = SA2_TEXP_DERIVED;
        if (!file_w || !file_h || !fmt ||
            (r->png_bit_depth != 1u && r->png_bit_depth != 2u && r->png_bit_depth != 4u &&
             r->png_bit_depth != 8u && r->png_bit_depth != 16u)) {
            g_st.tex_from_file_refused++;
            r->refused = 1;
            sa2_trap_note("d3dx9_42!%s from %#010x: the PNG at %#010x is %ux%u, colour type "
                          "%u, bit depth %u -- this runtime has no D3DFORMAT for that "
                          "combination and will not guess one",
                          p->entry, caller, src, file_w, file_h,
                          r->png_colour_type, r->png_bit_depth);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
        r->parsed = 1;
    } else {
        g_st.tex_from_file_refused++;
        r->refused = 1;
        sa2_trap_note("d3dx9_42!%s from %#010x: the %u-byte blob at %#010x begins "
                      "%02x%02x%02x%02x%02x%02x%02x%02x, which this runtime reads as %s -- it "
                      "parses DDS and PNG headers and will not invent a size for a container "
                      "it does not read",
                      p->entry, caller, src_len, src, hdr[0], hdr[1], hdr[2], hdr[3],
                      hdr[4], hdr[5], hdr[6], hdr[7],
                      r->container == SA2_TEXC_UNKNOWN ? "no container it can name"
                                                       : sa2_d3d9_container_name(r->container));
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }

    r->file_w = file_w; r->file_h = file_h;
    r->file_depth = file_depth; r->file_levels = file_levels; r->file_fmt = fmt;

    /* D3DX_DEFAULT and 0 both mean "take it from the file"; an explicit value
     * is the caller's and is used as given, which is what D3DX does. */
    const int w_default = (want_w == D3DX_DEFAULT || !want_w);
    const int h_default = (want_h == D3DX_DEFAULT || !want_h);
    const int f_default = (want_fmt == D3DX_DEFAULT || !want_fmt);
    const int d_default = (p->want_d == D3DX_DEFAULT || !p->want_d);
    const uint32_t w = w_default ? file_w : want_w;
    const uint32_t h = h_default ? file_h : want_h;
    const uint32_t format = f_default ? fmt : want_fmt;
    /* A volume's third dimension follows the same rule as its first two. For a
     * 2D texture and a cube map it is 1 and there is nothing to take from
     * anywhere: a cube's six faces are faces, not depth. */
    const uint32_t depth = kind != SA2_TEXK_VOLUME ? 1u
                         : d_default ? (file_depth ? file_depth : 1u) : p->want_d;
    if (!w_default || !h_default || (kind == SA2_TEXK_VOLUME && !d_default))
        r->dim_from = SA2_TEXP_CALLER;
    if (!f_default) r->fmt_from = SA2_TEXP_CALLER;

    /* Mip levels. D3DX's published rule for `MipLevels` is that 0 or
     * D3DX_DEFAULT asks for a complete chain; a file that states its own mip
     * count keeps it, because that count is the file's and the chain would
     * only round it up. This is the one number here that the previous version
     * got from the file alone, and a DDS with no mip count was therefore given
     * one level where D3DX gives nine -- a divergence in the guest's own
     * `GetLevelCount`. No DDS had ever reached this function, so nothing
     * observed changes; the rule is recorded per texture either way. */
    uint32_t levels;
    if (want_levels == D3DX_DEFAULT || !want_levels) {
        if (file_levels > 1u) { levels = file_levels; r->levels_from = SA2_TEXP_STATED; }
        else                  { levels = full_chain(w, h); r->levels_from = SA2_TEXP_API; }
    } else {
        levels = want_levels;
        r->levels_from = SA2_TEXP_CALLER;
    }

    const uint32_t l0 = surface_bytes(w, h, format);
    if (!l0) {
        g_st.tex_from_file_refused++;
        r->refused = 1;
        sa2_trap_note("d3dx9_42!%s from %#010x: format %#010x has no byte count in this "
                      "runtime, so %ux%u cannot be sized", p->entry, caller, format, w, h);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    const uint32_t bytes = d3dx_resource_bytes(kind, w, h, depth, format, levels);

    d3d_obj *o = d3dx_new_texture(kind, w, h, depth, levels, usage, format, pool);
    o->src_idx = row_idx;
    /* No backing store is taken here, and that is the difference between
     * recording a texture and pretending to have decoded one. A real D3DX
     * would have filled `bytes` of surface with the image; this runtime has no
     * image, so the bytes are taken only if the guest locks the texture and
     * asks for a pointer -- `lock_rect` reserves the whole chain then. The
     * seventeen textures this run loads would otherwise reserve 8.4 MiB of a
     * 10 MiB arena for surfaces nothing ever reads, and the run stopped on
     * exactly that before the store was made lazy. */
    r->obj = o->addr;
    r->w = w; r->h = h; r->d = depth; r->levels = levels; r->fmt = format;
    r->chain_bytes = bytes;
    r->level0_bytes = l0;
    r->pixels_decoded = 0;          /* nothing in this runtime decodes an image */
    g_st.tex_from_file++;

    if (g_break == BRK_FAKEPIXELS) {
        /* The break the "no decoded pixels" record exists to be measured
         * against: the surface is filled with something that is not the image,
         * and the guest cannot tell it from a decode.
         *
         * It fills at most FAKE_PIXEL_MAX bytes per texture. A full-chain fill
         * for every texture exhausts the arena, and a break that stops the run
         * by running out of memory has changed the run for a reason that is
         * not the clause it targets -- which would make it useless as a
         * measurement of that clause. */
        const uint32_t n = bytes > FAKE_PIXEL_MAX ? FAKE_PIXEL_MAX : bytes;
        const uint32_t mem = obj_alloc(o, n);
        sa2_guest_fill(mem, 0xA5, n);
        r->runtime_wrote_pixels = 1;
        r->pixels_decoded = 1;
        g_st.tex_with_decoded_pixels++;
    }

    /* D3DXIMAGE_INFO: Width, Height, Depth, MipLevels, Format, ResourceType,
     * ImageFileFormat. Every field but the last two is the file's own; the
     * resource type is the one the guest's own entry point names, and the file
     * format is the container the magic named rather than a constant. */
    if (srcinfo) {
        const uint32_t rtype = kind == SA2_TEXK_CUBE   ? 5u    /* D3DRTYPE_CUBETEXTURE */
                             : kind == SA2_TEXK_VOLUME ? 4u    /* D3DRTYPE_VOLUMETEXTURE */
                                                       : 3u;   /* D3DRTYPE_TEXTURE */
        const uint32_t info[7] = {file_w, file_h, file_depth, file_levels, fmt,
                                  rtype, d3dx_iff(r->container)};
        pkbuf(srcinfo, info, sizeof info);
    }
    pk32(out_pp, o->addr);
    note_reserve(d3dx_type_of(kind), p->slot, o->addr, caller, NULL, 0);
    note_settle(SA2_D3D_OK, SA2_D3D9_ANS_GUEST);
    return SA2_D3D_OK;
}

/* d3dx9_42!D3DXCreateTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize,
 *   Width, Height, MipLevels, Usage, Format, Pool, Filter, MipFilter, ColorKey,
 *   pSrcInfo, pPalette, ppTexture) -- fifteen, and the call site at 0x008667f4
 * pushes fifteen. */
uint32_t sa2_d3d9_d3dx_texture_from_memory(const uint32_t *a, uint32_t caller) {
    const d3dx_mem p = {
        SA2_TEXK_2D, DXS_TEX_FROM_MEM, "D3DXCreateTextureFromFileInMemoryEx",
        a[0], a[1], a[2],
        a[3], a[4], 1u, a[5],
        a[6], a[7], a[8],
        a[12], a[14],
    };
    return d3dx_from_memory(&p, caller);
}

/* d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx(pDevice, pSrcData,
 *   SrcDataSize, Size, MipLevels, Usage, Format, Pool, Filter, MipFilter,
 *   ColorKey, pSrcInfo, pPalette, ppCubeTexture)
 *
 * **Fourteen, not thirteen.** A cube map replaces the 2D call's Width and
 * Height with one Size, so it is the 2D signature less one argument, and the
 * call site at 0x008668e2 pushes fourteen dwords. Size stands in for both
 * dimensions, which is also how the object records it. */
uint32_t sa2_d3d9_d3dx_cube_from_memory(const uint32_t *a, uint32_t caller) {
    const d3dx_mem p = {
        SA2_TEXK_CUBE, DXS_CUBE_FROM_MEM, "D3DXCreateCubeTextureFromFileInMemoryEx",
        a[0], a[1], a[2],
        a[3], a[3], 1u, a[4],
        a[5], a[6], a[7],
        a[11], a[13],
    };
    return d3dx_from_memory(&p, caller);
}

/* d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx(pDevice, pSrcData,
 *   SrcDataSize, Width, Height, Depth, MipLevels, Usage, Format, Pool, Filter,
 *   MipFilter, ColorKey, pSrcInfo, pPalette, ppVolumeTexture)
 *
 * **Sixteen, not fifteen.** A volume adds Depth to the 2D call, and the call
 * site at 0x00866848 pushes sixteen dwords -- the last three of them
 * D3DX_DEFAULT for Width, Height and Depth, so all three come out of the
 * blob's own header. */
uint32_t sa2_d3d9_d3dx_volume_from_memory(const uint32_t *a, uint32_t caller) {
    const d3dx_mem p = {
        SA2_TEXK_VOLUME, DXS_VOLUME_FROM_MEM, "D3DXCreateVolumeTextureFromFileInMemoryEx",
        a[0], a[1], a[2],
        a[3], a[4], a[5], a[6],
        a[7], a[8], a[9],
        a[13], a[15],
    };
    return d3dx_from_memory(&p, caller);
}

/* ------------------------------------- D3DXFilterTexture: the mip chain
 *
 * `D3DXFilterTexture(pBaseTexture, pPalette, SrcLevel, MipFilter)` -- four,
 * and the call site at 0x0086763e pushes four: the texture, a null palette,
 * level 0, and D3DX_DEFAULT for the filter.
 *
 * It has **no out-parameter**: its whole effect is to downsample level
 * SrcLevel into every level below it, inside surfaces the guest already owns.
 * This runtime decodes no image, so `tex_with_decoded_pixels` is 0 by
 * construction and there is nothing in level 0 to downsample; the lower levels
 * stay as the guest left them, which for a file-loaded texture is the zeroes
 * the lazy backing store hands out. So S_OK with nothing written is a *true*
 * statement about what happened, not a fabrication -- and the number of levels
 * a real D3DX would have filled is counted rather than left implicit, because
 * "implemented" would otherwise hide it from M7's reader.
 *
 * `SA2_D3DX_STRICT=1` refuses it, which is how that claim is shown to be
 * load-bearing rather than asserted. */
uint32_t sa2_d3d9_d3dx_filter_texture(const uint32_t *a, uint32_t caller) {
    if (!g_enabled) return SA2_D3DERR_INVALIDCALL;
    if (!a[0]) return SA2_D3DERR_INVALIDCALL;
    d3d_obj *o = d3dx_require_object("D3DXFilterTexture", "texture", a[0], caller);
    d3dx_strict("D3DXFilterTexture", caller,
                "generating a mip chain means downsampling an image this runtime never "
                "decoded, so the levels below the source would stay as they are");
    const uint32_t src_level = a[2] == D3DX_DEFAULT ? 0u : a[2];
    const uint32_t levels = o->d[2] ? o->d[2] : 1u;
    g_st.d3dx_filter_calls++;
    g_st.d3dx_filter_levels += levels > src_level + 1u ? levels - src_level - 1u : 0u;
    note_reserve(o->type, DXS_FILTER_TEXTURE, o->addr, caller, a, 4);
    note_settle(SA2_D3D_OK, SA2_D3D9_ANS_NOOP);
    return SA2_D3D_OK;
}

/* ------------------------------ D3DXLoadSurfaceFromSurface: the surface blit
 *
 * `D3DXLoadSurfaceFromSurface(pDestSurface, pDestPalette, pDestRect,
 * pSrcSurface, pSrcPalette, pSrcRect, Filter, ColorKey)` -- **eight, not
 * seven.** The three call sites (0x008676ad, 0x00867745, 0x008677fa) each push
 * eight dwords, with both rectangles null, Filter D3DX_DEFAULT and ColorKey 0,
 * and the published signature agrees. Every one of them is a whole-surface
 * copy between two surfaces this runtime fabricated, taken from
 * `GetSurfaceLevel(0, ...)` on two of its textures.
 *
 * There is no out-parameter here either; the effect is bytes in the
 * destination surface. Two cases, and the record says which one happened:
 *
 *  - **the guest's own bytes are moved verbatim** when both surfaces are this
 *    runtime's, the formats and dimensions match, neither rectangle is given,
 *    and the source has a backing store -- i.e. when the call is a copy and
 *    not a conversion. Copying guest memory to guest memory invents nothing,
 *    and *not* copying would lose whatever the guest had written through a
 *    Lock, which is data it can read back;
 *  - **nothing is copied** otherwise: a format conversion, a stretch or a
 *    sub-rectangle blit is image processing, which is S5's, and a source with
 *    no backing store has no bytes at all. S_OK still comes back, because a
 *    recording no-op device's answer to "move these pixels" is that the call
 *    happened; the count of such calls is `d3dx_surface_loads` less
 *    `d3dx_surface_copied`.
 *
 * `SA2_D3DX_STRICT=1` refuses the whole entry point. */
/* A surface's bytes and how many of them it has: a level surface's are its
 * container's at that level's offset, anything else's are its own. With
 * `reserve`, a surface that has none is given them -- `want` bytes of its own,
 * or its container's whole chain, which is what a Lock would reserve. */
static uint32_t surf_mem(d3d_obj *s, int reserve, uint32_t want, uint32_t *len) {
    *len = 0;
    if (s->life == LIFE_LEVEL && !life_old()) {
        d3d_obj *t = life_root(s);
        if (t->type == T_CUBETEX) {
            const cube_span span = cube_storage(t, s->face, s->level, g_call_caller);
            if (reserve) obj_alloc(t, span.reserve);
            if (!t->mem || t->mem_len < span.offset + span.bytes) return 0;
            *len = span.bytes;
            return t->mem + span.offset;
        }
        const uint32_t n = surface_bytes(s->d[0], s->d[1], s->d[2]);
        const uint32_t off = chain_offset(t->d[0], t->d[1], t->d[4], s->level);
        if (reserve) {
            const uint32_t r = tex_reserve(t);
            obj_alloc(t, r > off + n ? r : off + n);
        }
        if (!t->mem || t->mem_len < off + n) return 0;
        *len = n;
        return t->mem + off;
    }
    if (reserve) obj_alloc(s, want);
    *len = s->mem ? s->mem_len : 0;
    return s->mem;
}

uint32_t sa2_d3d9_d3dx_load_surface_from_surface(const uint32_t *a, uint32_t caller) {
    if (!g_enabled) return SA2_D3DERR_INVALIDCALL;
    if (!a[0] || !a[3]) return SA2_D3DERR_INVALIDCALL;
    d3d_obj *dst = d3dx_require_object("D3DXLoadSurfaceFromSurface", "destination surface",
                                       a[0], caller);
    d3d_obj *src = d3dx_require_object("D3DXLoadSurfaceFromSurface", "source surface",
                                       a[3], caller);
    d3dx_strict("D3DXLoadSurfaceFromSurface", caller,
                "converting or stretching a surface is image processing this runtime does "
                "not do, and a verbatim copy only moves bytes the guest wrote itself");
    g_st.d3dx_surface_loads++;
    note_reserve(dst->type, DXS_LOAD_SURFACE, dst->addr, caller, a, 8);

    /* A copy and not a conversion: no rectangles, the same dimensions, the
     * same format, a format this runtime can size, and a source that has
     * bytes at all. Anything else leaves the destination as it was. */
    uint32_t src_len = 0;
    const uint32_t src_mem = surf_mem(src, 0, 0, &src_len);
    const int verbatim = !a[2] && !a[5] &&
                         dst->d[0] == src->d[0] && dst->d[1] == src->d[1] &&
                         dst->d[2] == src->d[2] && src_mem && src_len &&
                         surface_bytes(dst->d[0], dst->d[1], dst->d[2]) != 0u;
    if (verbatim) {
        /* The destination is reserved at the size `lock_rect` would reserve --
         * its own surface size, or a level surface's container's whole chain
         * -- so a later Lock finds the bytes this copy filled instead of
         * `obj_alloc` handing back a fresh block and orphaning them. The
         * dimensions and the format match, so the surface size is also the
         * source's. */
        const uint32_t want = surface_bytes(dst->d[0], dst->d[1], dst->d[2]);
        uint32_t dst_len = 0;
        const uint32_t mem = surf_mem(dst, 1, want, &dst_len);
        /* The source is read again after the reserve. When both are the same
         * store -- a copy from a surface onto itself; two levels of one
         * texture never are, because no two levels have the same dimensions
         * and a copy between different ones is not verbatim -- a reserve that
         * grows it moves the bytes to a new block and may free the old one
         * (obj_alloc), so the address taken above can be freed memory. */
        const uint32_t from = surf_mem(src, 0, 0, &src_len);
        uint32_t n = want < src_len ? want : src_len;
        if (n > dst_len) n = dst_len;
        if (!from || !mem) n = 0;
        gcopy(mem, from, n);
        /* Into a file texture's level, that is a write into the texture's
         * store with no Lock counted, so its row says so: the bytes are the
         * guest's, moved from another surface, and crt_probe.c's check that
         * no texture holds this runtime's bytes leaves the row out rather
         * than read them as this runtime's. A copy from a surface onto
         * itself moves no byte in from anywhere else, so it leaves the row
         * as it was, and the count still reads what the store held. */
        const d3d_obj *root = life_root(dst);
        if (n && root && life_root(src) != root) tex_row_copied(root->src_idx);
        g_st.d3dx_surface_copied++;
        g_st.d3dx_surface_bytes += n;
        note_settle(SA2_D3D_OK, SA2_D3D9_ANS_GUEST);
    } else {
        note_settle(SA2_D3D_OK, SA2_D3D9_ANS_NOOP);
    }
    return SA2_D3D_OK;
}

/* ------------------------------------ D3DXSave*ToFileA: the files on disk
 *
 * The two entry points whose effect is outside the process, and the only ones
 * in this file where "no-op success" needed an argument rather than a
 * definition. What the image does with them:
 *
 *   00869540  push ecx                  ; a renderer method, vtable 0x008b70b0
 *   00869541  mov eax,[ecx+38h]         ; its IDirect3DDevice9
 *   00869551  mov eax,[ecx+48h]
 *   00869554  call eax                  ; GetBackBuffer(0, 0, 0, &surface)
 *   00869580  call 007A4AB2h            ; D3DXSaveSurfaceToFileA(path, fmt, surface, 0, 0)
 *   0086958e  call edx                  ; surface->Release()
 *
 * -- a screenshot -- and 0x008679b0, vtable 0x008b7124, which does nothing but
 * call `D3DXSaveTextureToFileA(path, fmt, texture, 0)` and return. **Neither
 * wrapper tests the HRESULT and neither opens the file again**; both are
 * virtual methods of the renderer interface at 0x008b7060, reached only
 * through that vtable, so a static search finds no direct caller for either.
 * Nothing in the image reads back a file written here.
 *
 * So the decision is: **record the call, write no host file, return S_OK.**
 * Three reasons, in order of weight.
 *
 *  1. S4 is a headless runtime that must not scatter files across the user's
 *     disk because the guest asked. The boundary rule (design 12.9) is about
 *     what leaves this repository, and a screenshot of a device that renders
 *     nothing is a black rectangle either way.
 *  2. There is no image to write. Every surface here is zero-filled guest
 *     memory the runtime never decoded into, so a file would be a fabrication
 *     with a filename on it -- the worst kind, because it looks like evidence.
 *  3. The success is not load-bearing for the guest: neither caller looks at
 *     it, and neither reads the file.
 *
 * **The filename is recorded whatever happens**, because the path the guest
 * chose is evidence about what the game does with these calls -- which build
 * it thinks it is, where it thinks it can write -- and it is the one thing a
 * no-op would otherwise throw away. `SA2_D3DX_STRICT=1` refuses both, so "no
 * file was written" is a claim a run can be made to fail on. */
#define SAVEFILE_MAX 256u
static sa2_d3d9_savefile g_save[SAVEFILE_MAX];
static uint32_t          g_save_used;

/* A guest NUL-terminated byte string into `out`. Returns 1 when it terminated
 * inside the guest space and inside `cap`, 0 otherwise -- and `out` is always
 * NUL-terminated either way, so a truncated path is still recorded. A path
 * this runtime cannot read is not a reason to trap: the call is being recorded
 * precisely because nobody knows yet what these paths look like. */
static int gstr_in(uint32_t a, char *out, uint32_t cap) {
    out[0] = 0;
    if (!a) return 0;
    for (uint32_t i = 0; i + 1u < cap; i++) {
        uint8_t ch = 0;
        if (sa2_peek(a + i, 1, &ch) != SA2_OK) { out[i] = 0; return 0; }
        out[i] = (char)ch;
        if (!ch) return 1;
    }
    out[cap - 1u] = 0;
    return 0;
}

const char *sa2_d3d9_iff_name(uint32_t iff) {
    switch (iff) {
    case 0:  return "bmp";
    case 1:  return "jpg";
    case 2:  return "tga";
    case 3:  return "png";
    case 4:  return "dds";
    case 5:  return "ppm";
    case 6:  return "dib";
    case 7:  return "hdr";
    case 8:  return "pfm";
    default: return "unnamed";
    }
}
uint32_t sa2_d3d9_savefile_count(void) { return g_save_used; }
const sa2_d3d9_savefile *sa2_d3d9_savefile_at(uint32_t i) {
    return i < g_save_used ? &g_save[i] : NULL;
}

/* One row, whether or not the table had room; `bytes_written` is zero in every
 * one of them and is a field rather than a comment for the same reason
 * `tex_with_decoded_pixels` is. */
static uint32_t save_record(const char *entry, uint32_t slot, int is_texture,
                            uint32_t path_addr, uint32_t iff, uint32_t obj_addr,
                            uint32_t caller) {
    if (!g_enabled) return SA2_D3DERR_INVALIDCALL;
    if (!obj_addr) return SA2_D3DERR_INVALIDCALL;
    d3d_obj *o = d3dx_require_object(entry, is_texture ? "texture" : "surface",
                                     obj_addr, caller);
    char path[SA2_D3D9_SAVE_PATH];
    const int ok = gstr_in(path_addr, path, SA2_D3D9_SAVE_PATH);
    g_st.d3dx_saves++;
    if (g_save_used < SAVEFILE_MAX) {
        sa2_d3d9_savefile *s = &g_save[g_save_used++];
        memset(s, 0, sizeof *s);
        s->seq = g_st.d3dx_saves;
        s->caller = caller;
        s->obj = o->addr;
        s->src_idx = o->src_idx;
        s->iff = iff;
        s->w = o->d[0];
        s->h = o->d[1];
        s->fmt = is_texture ? o->d[4] : o->d[2];
        s->bytes_written = 0;
        s->texture = (uint8_t)(is_texture ? 1 : 0);
        s->path_ok = (uint8_t)ok;
        memcpy(s->path, path, sizeof s->path);
    } else {
        g_st.d3dx_save_rows_dropped++;
    }
    /* The strict refusal comes *after* the row, so the run that stops on it
     * still says which file the guest was asking for. */
    d3dx_strict(entry, caller,
                "this call would have written a host file, and the only bytes this runtime "
                "could put in it are the zeroes of a surface it never decoded an image into");
    note_reserve(o->type, slot, o->addr, caller, NULL, 0);
    note_settle(SA2_D3D_OK, SA2_D3D9_ANS_NOOP);
    return SA2_D3D_OK;
}

/* d3dx9_42!D3DXSaveSurfaceToFileA(pDestFile, DestFormat, pSrcSurface,
 *   pSrcPalette, pSrcRect) -- **five, not four.** The call site at 0x00869580
 * pushes five dwords and the published signature has five parameters; a
 * four-dword pop would have left the caller's frame four bytes out, and the
 * next thing it does is dereference a surface pointer off that frame. */
uint32_t sa2_d3d9_d3dx_save_surface_to_file(const uint32_t *a, uint32_t caller) {
    return save_record("D3DXSaveSurfaceToFileA", DXS_SAVE_SURFACE, 0,
                       a[0], a[1], a[2], caller);
}
/* d3dx9_42!D3DXSaveTextureToFileA(pDestFile, DestFormat, pSrcTexture,
 *   pSrcPalette) -- four, and the two call sites at 0x008679d6 and 0x008679e6
 * push four. The two sites are the same call either side of the MSVC
 * `std::string` short-buffer test at 0x008679c8. */
uint32_t sa2_d3d9_d3dx_save_texture_to_file(const uint32_t *a, uint32_t caller) {
    return save_record("D3DXSaveTextureToFileA", DXS_SAVE_TEXTURE, 1,
                       a[0], a[1], a[2], caller);
}

int sa2_d3d9_device_enabled(void) { return g_enabled; }
int sa2_d3d9_constant_table_enabled(void) { return g_constant_table; }

/* ------------------------------------ d3dx9_42!D3DXGetShaderConstantTable
 *
 * With SA2_D3DX_CONSTANT_TABLE=1. The import refuses without it and never
 * calls this entry point, and this entry point refuses by name without it too,
 * so no second caller can build a table with the switch off. The call's one
 * site is 0x00867225, which passes the bytecode it has just read back with the
 * shader's GetFunction and its caller's out-pointer, and frees the bytecode as
 * soon as the call returns (0x0086722f).
 *
 * Native's answer rests on the shader's CTAB comment: the oracle's d3dx9_42
 * forwards the call to D3DXGetShaderConstantTableEx, which finds the comment
 * with D3DXFindShaderComment and prints the table's header, one per call (the
 * native-outcomes receipt's evidence lines). Before it builds a table, the Ex
 * function makes three checks (addresses in the oracle's syswow64
 * d3dx9_42.dll), and this entry point makes the same three, in its order:
 *
 *  1. the version token is a vertex or pixel shader's, its high fifteen bits
 *     all set (0x10015fe5-0x10015fee). Any other token -- the effect and
 *     texture-shader kinds D3DXFindShaderComment itself accepts included -- it
 *     answers D3D_OK, with the null table it has already written through the
 *     out-pointer (0x10015fd7, 0x100162ed);
 *  2. D3DXFindShaderComment finds a CTAB comment of at least the 28 bytes of a
 *     D3DXSHADER_CONSTANTTABLE (0x10016023-0x1001603b);
 *  3. that header's Size is the structure's 28 bytes (0x10016044-0x1001604a).
 *
 * The second and third it answers D3DXERR_INVALIDDATA (0x100163f9). One more
 * check is this runtime's, where the oracle leaves the bytes to its type
 * parser: the constant descriptions the header counts lie inside the comment.
 * A shader that fails any of them is refused by name, and the refusal says
 * what the oracle's code would do instead. None of that has been seen in a
 * traced run (the native-outcomes receipt counts no warn line from either
 * function), so this runtime does none of it.
 *
 * What the call leaves behind is the table (the ID3DXConstantTable comment
 * above) and a row of the header's numbers (sa2_d3d9_ctab), which is what the
 * oracle's trace prints for the same call, so a run can be held to native's
 * calls one by one. */
#define CTAB_FOURCC      0x42415443u   /* 'CTAB' */
#define CTAB_HEADER      28u           /* sizeof(D3DXSHADER_CONSTANTTABLE) */
#define CTAB_CONSTANT    20u           /* sizeof(D3DXSHADER_CONSTANTINFO) */
/* How far the walk reads, in DWORDs from the version token, a comment counted
 * by its length: GetFunction's own bound on a shader's length
 * (shader_blob_bytes), which the bytecode the game hands over has passed. */
#define SHADER_TOKENS_MAX 65536u

/* D3DXFindShaderComment's walk past the version token, as the oracle's
 * d3dx9_42 makes it (0x10014e60; Wine's dlls/d3dx9_36/shader.c): from the
 * token after the version token to the end token 0x0000FFFF, a comment token
 * (low word 0xFFFE) carries its length in DWORDs in bits 16..30 and is stepped
 * over whole, any other token is one DWORD, and the first comment whose first
 * DWORD is `fourcc` is the one. Answers the address of the data after the
 * fourcc and, through `bytes`, its length, which is the size the oracle's
 * trace prints; 0 when there is none, with `why` saying why and `native` what
 * the oracle would do instead.
 *
 * Two differences. D3DXFindShaderComment's own version test is not made here:
 * it accepts the effect and texture-shader kinds as well as a shader's, and
 * its one caller, like the oracle's Ex function, has already refused every
 * token but a vertex or pixel shader's, both of which the test accepts. And
 * the walk stops at SHADER_TOKENS_MAX and refuses, where the oracle's has no
 * bound and walks on. */
static uint32_t find_shader_comment(uint32_t code, uint32_t fourcc, uint32_t *bytes,
                                    const char **why, const char **native) {
    *native = "answer D3DXERR_INVALIDDATA (0x1001602f)";
    for (uint32_t off = 1; off < SHADER_TOKENS_MAX; off++) {
        const uint32_t p = code + 4u * off;
        uint32_t tok = 0;
        if (sa2_peek(p, 4, &tok) != SA2_OK) {
            *why = "it leaves the guest space before its end token";
            *native = "read on past the bytecode, unguarded";
            return 0;
        }
        if (tok == 0x0000FFFFu) {
            *why = "it has no CTAB comment before its end token";
            return 0;
        }
        if ((tok & 0xFFFFu) != 0xFFFEu) continue;
        const uint32_t len = (tok & 0x7FFF0000u) >> 16;
        uint32_t first = 0;
        if (sa2_peek(p + 4u, 4, &first) == SA2_OK && first == fourcc) {
            if (!len) {
                *why = "its CTAB comment's token gives it no length";
                *native = "take the comment's size as 0xFFFFFFFC (0x10014f88), pass its size "
                          "check and read a header after it";
                return 0;
            }
            *bytes = (len - 1u) * 4u;
            return p + 8u;
        }
        off += len;                               /* the loop steps past the comment's token */
    }
    *why = "it has no end token within its first 64K tokens";
    *native = "walk on with no bound";
    return 0;
}

uint32_t sa2_d3d9_d3dx_get_shader_constant_table(const uint32_t *a, uint32_t caller) {
    const uint32_t code = a[0], out = a[1];
    g_call_caller = caller;
    if (!g_constant_table) {
        sa2_trap_note("d3dx9_42!D3DXGetShaderConstantTable from %#010x: SA2_D3DX_CONSTANT_TABLE "
                      "is off, so the import refuses in runtime/sa2_imports.c and no caller "
                      "may build a table through this entry point", caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    if (!g_enabled) {
        sa2_trap_note("d3dx9_42!D3DXGetShaderConstantTable from %#010x: SA2_D3DX_CONSTANT_TABLE "
                      "is set and the d3d9 device is off (SA2_D3D9_DEVICE), and the table is "
                      "one of the device's objects", caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    if (!out) {
        sa2_trap_note("d3dx9_42!D3DXGetShaderConstantTable from %#010x: ppConstantTable is "
                      "null, which the image never passes and no traced run has answered",
                      caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    /* The three checks and this runtime's fourth, in the oracle's order; each
     * failure says why, and what the oracle would do instead. */
    const char *why = NULL, *native = "";
    char token[16] = "unread";
    uint32_t version = 0, bytes = 0, hdr[7] = {0}, ctab = 0;
    const int have_version = code && sa2_peek(code, 4, &version) == SA2_OK;
    if (have_version) snprintf(token, sizeof token, "%#010x", version);
    if (!code) {
        why = "pFunction is null";
        native = "answer D3DERR_INVALIDCALL (0x10015fdf)";
    } else if (!have_version) {
        why = "its version token is not in the guest space";
        native = "read through the pointer, unguarded (0x10015fe5)";
    } else if ((version & 0xFFFE0000u) != 0xFFFE0000u) {
        why = "its version token is not a vertex or pixel shader's";
        native = "answer D3D_OK with a null table (0x10015fee, 0x100162ed)";
    } else if (!(ctab = find_shader_comment(code, CTAB_FOURCC, &bytes, &why, &native))) {
        /* `why` and `native` are the walk's */
    } else if (bytes < CTAB_HEADER) {
        why = "its CTAB comment is shorter than a D3DXSHADER_CONSTANTTABLE";
        native = "answer D3DXERR_INVALIDDATA (0x1001603b)";
    } else if (sa2_peek(ctab, sizeof hdr, hdr) != SA2_OK) {
        why = "its CTAB header is not in the guest space";
        native = "read the header, unguarded";
    } else if (hdr[0] != CTAB_HEADER) {
        why = "its CTAB header's Size is not the 28 bytes of a D3DXSHADER_CONSTANTTABLE";
        native = "answer D3DXERR_INVALIDDATA (0x1001604a)";
    } else if (hdr[3] && (hdr[4] > bytes || hdr[3] > (bytes - hdr[4]) / CTAB_CONSTANT)) {
        why = "the constant descriptions its CTAB header counts run past the comment";
        native = "hand the bytes past the comment to its type parser";
    }
    if (why) {
        sa2_trap_note("d3dx9_42!D3DXGetShaderConstantTable from %#010x: the bytecode at %#010x "
                      "(version token %s) is refused because %s; the oracle's d3dx9_42 would "
                      "%s, which no traced run has shown", caller, code, token, why, native);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    uint8_t *copy=malloc(bytes);
    table_parser p={0}; p.data=copy; p.bytes=bytes;
    int parsed=copy && sa2_peek(ctab,bytes,copy)==SA2_OK;
    if (parsed && memcmp(copy,hdr,sizeof hdr))
        parsed=table_bad(&p,"CTAB header changed between initial read and owned snapshot");
    if (parsed) parsed=table_parse(&p);
    if (!parsed) {
        const char *parse_why=p.why ? p.why : "the complete CTAB is not readable/allocatable";
        free(p.nodes); free(copy);
        sa2_trap_note("d3dx9_42!D3DXGetShaderConstantTable from %#010x: %s; "
                      "this bounded reflection parser has not been compared to the held native DLL",
                      caller,parse_why);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,SA2_D3D9_THUNK_BASE);
    }
    uint32_t records=(bytes+3u)&~3u, total;
    if (!table_mul(p.cap,sizeof(table_constant),&total) || !table_add(records,total,&total) || total>UINT32_MAX-31u) {
        free(p.nodes); free(copy);
        refuse("ID3DXConstantTable","Create",caller,"owned CTAB allocation size overflows");
    }
    if (g_obj_used >= TABLE_HANDLE_INDICES) {
        free(p.nodes); free(copy);
        sa2_trap_note("d3dx9_42!D3DXGetShaderConstantTable from %#010x: the table would take "
                      "pool index %u, and a constant handle carries its table's index in "
                      "fifteen bits (below %u); a project guard, not a native answer",
                      caller, g_obj_used, TABLE_HANDLE_INDICES);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
    }
    d3d_obj *o = obj_new(T_CONSTANTTABLE, g_dev.dev);
    obj_alloc(o,total);
    pkbuf(o->mem,copy,bytes);
    for (uint32_t i=0;i<p.used;i++) {
        p.nodes[i].desc[0]+=o->mem;
        if (p.nodes[i].desc[11]) p.nodes[i].desc[11]+=o->mem;
    }
    if (p.used) pkbuf(o->mem+records,p.nodes,p.used*sizeof(table_constant));
    free(p.nodes); free(copy);
    o->d[0] = code;                               /* diagnostic input address only */
    o->d[1] = o->mem;                             /* complete owned CTAB */
    o->d[2] = bytes;
    o->d[3] = hdr[2];
    o->d[8] = version;                            /* immutable original bytecode Version */
    o->d[4] = hdr[3];
    o->d[6] = records;
    o->d[7] = p.used;
    if (g_ctab_used < SA2_D3D9_CTAB_ROWS) {
        sa2_d3d9_ctab *r = &g_ctab[g_ctab_used++];
        memset(r, 0, sizeof *r);
        r->seq = g_ctab_used;
        r->caller = caller;
        r->bytecode = code;
        r->table = o->addr;
        r->version = hdr[2];
        r->constants = hdr[3];
        r->ctab_bytes = bytes;
        r->refs = o->refs;
        o->d[5] = g_ctab_used;
    } else {
        g_ctab_dropped++;
    }
    pk32(out, o->addr);
    g_st.constant_tables_built++;
    const uint32_t argv[5] = {code, out, hdr[2], hdr[3], bytes};
    note_reserve(T_CONSTANTTABLE, DXS_CONSTANT_TABLE, o->addr, caller, argv, 5);
    note_settle(SA2_D3D_OK, SA2_D3D9_ANS_OBJECT);
    return SA2_D3D_OK;
}

uint32_t sa2_d3d9_ctab_count(void) { return g_ctab_used; }
const sa2_d3d9_ctab *sa2_d3d9_ctab_at(uint32_t i) { return i < g_ctab_used ? &g_ctab[i] : NULL; }
uint32_t sa2_d3d9_ctab_rows_dropped(void) { return g_ctab_dropped; }

/* ------------------------------------------------ SA2_D3D9_LIFE_DUMP's writer */
static void life_dump(const char *suffix, const char *why, uint32_t len, const d3d_obj *want) {
    if (!g_life_path) return;
    char path[1024];
    snprintf(path, sizeof path, "%s%s", g_life_path, suffix);
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "why: %s\nbreak: %d\nseq: %u\nobjects_built: %u\nguest_bytes_allocated: %u\n"
               "guest_bytes_freed: %u\nobjects_destroyed: %u\nlevel_surfaces: %u\n"
               "calls_on_destroyed: %u (first from %#010x)\n"
               "releases_past_zero: %u (first from %#010x)\n",
            why, g_break, g_seq, g_st.objects_built, g_st.guest_bytes_allocated,
            g_st.guest_bytes_freed, g_st.objects_destroyed, g_st.level_surfaces,
            g_st.calls_on_destroyed, g_st.calls_on_destroyed_first_caller,
            g_st.releases_past_zero, g_st.releases_past_zero_first_caller);
    if (want) {
        const d3d_obj *par = want->parent ? obj_of(want->parent) : NULL;
        fprintf(f, "request: %u bytes for object %#010x (%s) index %u, count %u, creator %#010x, "
                   "caller %#010x, d = %u %u %u %u %u %u %u\n",
                len, want->addr, g_types[want->type].name,
                want->idx, want->refs, want->creator,
                g_call_caller, want->d[0], want->d[1], want->d[2], want->d[3], want->d[4],
                want->d[5], want->d[6]);
        if (par)
            fprintf(f, "request parent: %#010x (%s) count %u creator %#010x mem %#010x len %u "
                       "d = %u %u %u %u %u %u %u\n",
                    par->addr, g_types[par->type].name, par->refs, par->creator, par->mem,
                    par->mem_len, par->d[0], par->d[1], par->d[2], par->d[3], par->d[4],
                    par->d[5], par->d[6]);
    }
    fprintf(f, "\n# per type: objects, count>0, count==0, with store, store bytes (count>0), "
               "store bytes (count==0), destroyed, bound\n");
    for (uint32_t t = 0; t < type_count(); t++) {
        uint32_t n = 0, live = 0, dead = 0, st = 0, gone = 0, bound = 0;
        uint64_t bl = 0, bd = 0;
        for (uint32_t i = 0; i < g_obj_used; i++) {
            const d3d_obj *o = &g_obj[i];
            if (o->type != t) continue;
            n++;
            if (o->refs) live++; else dead++;
            if (!o->alive) gone++;
            if (o->binds) bound++;
            if (o->mem) { st++; if (o->refs) bl += o->mem_len; else bd += o->mem_len; }
        }
        if (n)
            fprintf(f, "%-28s %6u %6u %6u %6u %12llu %12llu %6u %6u\n", g_types[t].name, n,
                    live, dead, st, (unsigned long long)bl, (unsigned long long)bd, gone,
                    bound);
    }
    fprintf(f, "\n# distinct callers per method: interface method calls caller...\n");
    for (uint32_t t = 0; t < type_count(); t++)
        for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
            const life_caller *row = g_life_callers[t][k];
            if (!row[0].calls) continue;
            const d3d_row *r = row_of(t, k);
            fprintf(f, "%s::%s", g_types[t].name, r ? r->name : "?");
            for (uint32_t i = 0; i < LIFE_CALLERS && row[i].calls; i++)
                fprintf(f, " %#010x:%u", row[i].caller, row[i].calls);
            if (g_life_overflow[t][k]) fprintf(f, " +%u-more", g_life_overflow[t][k]);
            fprintf(f, "\n");
        }
    fprintf(f, "\n# objects: index addr type count binds life level alive parent creator "
               "born_seq dead_seq mem mem_len locks lock_caller d0 d1 d2 d3 d4 d5 d6 src_idx\n");
    for (uint32_t i = 0; i < g_obj_used; i++) {
        const d3d_obj *o = &g_obj[i];
        fprintf(f, "%u %#010x %s %u %u %u %u %u %#010x %#010x %u %u %#010x %u %u %#010x "
                   "%u %u %u %u %u %u %u %u\n",
                i, o->addr, g_types[o->type].name, o->refs, o->binds, o->life, o->level,
                o->alive, o->parent, o->creator, o->born_seq, o->dead_seq, o->mem, o->mem_len,
                o->locks, o->lock_caller, o->d[0], o->d[1], o->d[2], o->d[3], o->d[4], o->d[5],
                o->d[6], o->src_idx);
    }
    fclose(f);
}
static void life_dump_at_exit(void) { life_dump(".exit", "process exit", 0, NULL); }

void sa2_d3d9_config_from_env(void) {
    const char *v = getenv("SA2_D3D9_DEVICE");
    g_enabled = v && *v && strcmp(v, "0") != 0;
    v = getenv("SA2_D3D9_STRICT_CAPS");
    g_strict_caps = v && *v && strcmp(v, "0") != 0;
    v = getenv("SA2_D3D9_TEXMEM");
    if (v && *v) g_texmem = (uint32_t)strtoul(v, NULL, 0);
    v = getenv("SA2_D3D9_REFRESH");
    if (v && *v) g_refresh = (uint32_t)strtoul(v, NULL, 0);
    const int was_old_model = life_old(), was_break = g_break;
    v = getenv("SA2_D3D9_BREAK");
    g_break = BRK_NONE;
    if (v && *v) {
        if (!strcmp(v, "argc")) g_break = BRK_ARGC;
        else if (!strcmp(v, "out")) g_break = BRK_OUT;
        else if (!strcmp(v, "noimpl")) g_break = BRK_NOIMPL;
        else if (!strcmp(v, "guestheap")) g_break = BRK_GUESTHEAP;
        else if (!strcmp(v, "texguess")) g_break = BRK_TEXGUESS;
        else if (!strcmp(v, "fakepixels")) g_break = BRK_FAKEPIXELS;
        else if (!strcmp(v, "texunknown")) g_break = BRK_TEXUNKNOWN;
        else if (!strcmp(v, "wideobj")) g_break = BRK_WIDEOBJ;
        else if (!strcmp(v, "wideobjzero")) g_break = BRK_WIDEOBJZERO;
        else if (!strcmp(v, "wideobjvt")) g_break = BRK_WIDEOBJVT;
        else if (!strcmp(v, "neverfree")) g_break = BRK_NEVERFREE;
        else if (!strcmp(v, "ownzero")) g_break = BRK_OWNZERO;
        else if (!strcmp(v, "keepall")) g_break = BRK_KEEPALL;
        else if (!strcmp(v, "tableanswer")) g_break = BRK_TABLEANSWER;
        else if (!strcmp(v, "tablekeep")) g_break = BRK_TABLEKEEP;
        else if (!strcmp(v, "presentwait")) g_break = BRK_PRESENTWAIT;
        else {
            sa2_trap_note("SA2_D3D9_BREAK=%s is not one of argc, out, noimpl, guestheap, "
                          "texguess, fakepixels, texunknown, wideobj, wideobjzero, wideobjvt, "
                          "neverfree, ownzero, keepall, tableanswer, tablekeep, presentwait", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_THUNK_BASE);
        }
    }
    /* The object layout, latched once an object exists. obj_of finds an object
     * by dividing by the stride in force, and obj_new places the next one at
     * the pool index times it, so under a stride changed beneath live objects
     * obj_of would stop finding every object but the first, and a narrower
     * stride would lay new objects over the words of those already built; a
     * fill changed under them would leave two layouts in one pool and a stats
     * record describing only one. Configuration read twice with an
     * SA2_D3D9_BREAK that asks for another stride or fill after the first
     * object refuses by name instead; a break that keeps the layout does not
     * touch it and is taken. */
    const uint32_t fill = g_break == BRK_WIDEOBJ     ? SA2_D3D9_FILL_SENTINEL
                        : g_break == BRK_WIDEOBJZERO ? SA2_D3D9_FILL_ZERO
                        : g_break == BRK_WIDEOBJVT   ? SA2_D3D9_FILL_VTABLE
                        :                              SA2_D3D9_FILL_NONE;
    const uint32_t stride = fill == SA2_D3D9_FILL_NONE ? SA2_D3D9_OBJ_STRIDE
                                                       : SA2_D3D9_WIDE_STRIDE;
    if (g_obj_used && (stride != g_obj_stride || fill != g_obj_fill)) {
        sa2_trap_note("d3d9: SA2_D3D9_BREAK=%s asks for objects %u bytes apart with fill %u, "
                      "but the %u already built are %u bytes apart with fill %u; %s",
                      (v && *v) ? v : "(unset)", stride, fill, g_obj_used, g_obj_stride,
                      g_obj_fill,
                      stride != g_obj_stride
                          ? "under a new stride obj_of would find only the first of them, "
                            "and a narrower one would lay new objects over their words"
                          : "a new fill would leave two layouts in one pool");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_OBJ_BASE);
    }
    /* The lifetime model is latched the same way: objects built under one
     * model carry that model's counts and level surfaces, and the other would
     * read them wrongly. */
    if (g_obj_used && life_old() != was_old_model) {
        const int asked_old = life_old();
        g_break = was_break;                /* the objects' model stays in force */
        sa2_trap_note("d3d9: SA2_D3D9_BREAK=%s asks for the %s lifetime model, but the %u "
                      "objects already built were counted under the other",
                      (v && *v) ? v : "(unset)", asked_old ? "old" : "Direct3D 9",
                      g_obj_used);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_D3D9_OBJ_BASE);
    }
    g_obj_stride = stride;
    g_obj_fill = fill;
    /* Both blocks at the 16-byte stride; under a wideobj break the first
     * block's bytes over the wide stride, 4096, as the pool held until
     * 2026-09-23 (sa2_d3d9.h). */
    g_obj_max = g_obj_stride == SA2_D3D9_OBJ_STRIDE
                    ? SA2_D3D9_POOL_OBJECTS
                    : SA2_D3D9_OBJ_FIRST_COUNT * SA2_D3D9_OBJ_STRIDE / g_obj_stride;
    g_st.object_stride = g_obj_stride;
    g_st.object_fill = g_obj_fill;
    v = getenv("SA2_D3D9_CAPS");
    g_caps_maximal = v && !strcmp(v, "maximal");
    v = getenv("SA2_D3DX_CONSTANT_TABLE");
    g_constant_table = v && *v && strcmp(v, "0") != 0;
    g_st.constant_table = (uint32_t)g_constant_table;
    v = getenv("SA2_D3DX_STRICT");
    g_strict_d3dx = v && *v && strcmp(v, "0") != 0;
    g_st.strict_d3dx = (uint32_t)g_strict_d3dx;
    v = getenv("SA2_D3D9_SHADER_MODEL");
    if (v && *v) g_shader_model = (uint32_t)strtoul(v, NULL, 0);
    v = getenv("SA2_D3D9_LIFE_DUMP");
    if (v && *v && !g_life_path) {
        g_life_path = v;
        atexit(life_dump_at_exit);
    }
    g_st.shader_model = g_shader_model;
    g_st.strict_caps = (uint32_t)g_strict_caps;
    /* The closed world: the list checked against the table now, so a stale header
     * stops the run before the first call, and the mode from the keyboard source. A
     * unit build stays open whatever the environment says. */
    listed_build();
    g_live_source = live_keyboard_source();
    g_automated = automated_session();
#ifndef SA2_D3D9_OPEN_WORLD_UNIT_BUILD
    g_world = g_live_source && !g_automated ? SA2_D3D9_WORLD_LOG : SA2_D3D9_WORLD_TRAP;
#endif
    if (g_world == SA2_D3D9_WORLD_LOG && !g_world_summary_armed) {
        g_world_summary_armed = 1;
        atexit(world_summary_at_exit);
    }
}

void sa2_d3d9_stats_get(sa2_d3d9_stats *out) { if (out) *out = g_st; }
uint32_t             sa2_d3d9_log_count(void) { return g_log_used; }
const sa2_d3d9_call *sa2_d3d9_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

uint32_t    sa2_d3d9_type_count(void) { return type_count(); }
const char *sa2_d3d9_type_name(uint32_t t) { return t < type_count() ? g_types[t].name : NULL; }
uint32_t    sa2_d3d9_type_slots(uint32_t t) {
    if (t >= type_count()) return 0;
    uint32_t n = 0;
    for (const d3d_row *p = g_types[t].rows; p->name; p++) n++;
    return n;
}
const char *sa2_d3d9_method_name(uint32_t t, uint32_t slot) {
    const d3d_row *r = row_of(t, slot);
    return r ? r->name : NULL;
}
int sa2_d3d9_method_argc(uint32_t t, uint32_t slot) {
    const d3d_row *r = row_of(t, slot);
    return r ? (int)r->argc : -1;
}
const char *sa2_d3d9_method_flags(uint32_t t, uint32_t slot) {
    static char buf[32];
    const d3d_row *r = row_of(t, slot);
    if (!r) return "";
    buf[0] = 0;
    if (r->flags & DF_OUT)  strcat(buf, "out");
    if (r->flags & DF_CAP)  strcat(buf, buf[0] ? ",cap" : "cap");
    if (r->flags & DF_VOID) strcat(buf, buf[0] ? ",void" : "void");
    return buf;
}

int sa2_d3d9_method_implemented(uint32_t t, uint32_t slot) {
    const d3d_row *r = row_of(t, slot);
    return r && r->fn ? 1 : 0;
}

int sa2_d3d9_world_mode(void) { return g_world; }
const char *sa2_d3d9_world_mode_name(void) { return world_name(g_world); }
int sa2_d3d9_method_listed(uint32_t t, uint32_t slot) {
    if (t >= SA2_D3D9_MAX_TYPES || slot >= SA2_D3D9_MAX_SLOTS) return 0;
    listed_build();
    return g_listed[t][slot];
}
int sa2_d3d9_world_live_source(void) { return g_live_source; }
int sa2_d3d9_world_automated(void) { return g_automated; }
uint32_t sa2_d3d9_listed_count(void) { return SA2_D3D9_M7_LIST_ROWS; }
const char *sa2_d3d9_list_digest(void) { listed_build(); return g_list_digest; }
uint32_t sa2_d3d9_unlisted_count(void) { return g_unl_used; }
uint32_t sa2_d3d9_unlisted_calls(void) { return g_unl_calls; }
const sa2_d3d9_unlisted *sa2_d3d9_unlisted_at(uint32_t i) {
    return i < g_unl_used ? &g_unl[i] : NULL;
}
const char *sa2_d3d9_unlisted_outcome_name(uint32_t outcome) {
    return unlisted_outcome((uint8_t)outcome);
}

void sa2_d3d9_closed_world_json(FILE *f) {
    if (!f) return;
    listed_build();
    fprintf(f, "\"closed_world\": {\"mode\": \"%s\", \"live_keyboard_source\": %s, "
               "\"automated_session\": %s, "
               "\"list\": \"runtime/sa2_d3d9_m7_list.h\", \"list_digest\": \"%s\", "
               "\"listed_methods\": %u,\n"
               "     \"unlisted_methods\": %u, \"unlisted_calls\": %u, \"unlisted\": [",
            world_name(g_world), g_live_source ? "true" : "false",
            g_automated ? "true" : "false", g_list_digest, SA2_D3D9_M7_LIST_ROWS, g_unl_used,
            g_unl_calls);
    for (uint32_t i = 0; i < g_unl_used; i++) {
        const sa2_d3d9_unlisted *r = &g_unl[i];
        const d3d_row *row = row_of(r->type, r->slot);
        /* Interface and method names are C identifiers from this file's table, so
         * they need no JSON escaping. */
        fprintf(f, "%s\n      {\"interface\": \"%s\", \"method\": ", i ? "," : "",
                g_types[r->type].name);
        if (row) fprintf(f, "\"%s\"", row->name);
        else fprintf(f, "null");
        fprintf(f, ", \"slot\": %u, \"calls\": %u, \"first_seq\": %u, "
                   "\"first_caller\": \"0x%08x\", \"first_this\": \"0x%08x\", "
                   "\"outcome\": \"%s\"}",
                r->slot, r->calls, r->first_seq, r->first_caller, r->first_self,
                unlisted_outcome(r->outcome));
    }
    fprintf(f, "]}");
}
const sa2_d3d9_slot_stat *sa2_d3d9_slot_stat_at(uint32_t t, uint32_t slot) {
    if (t >= SA2_D3D9_MAX_TYPES || slot >= SA2_D3D9_MAX_SLOTS) return NULL;
    return &g_slot[t][slot];
}

uint32_t sa2_d3d9_caps_field_count(void) { return g_caps_used; }
int sa2_d3d9_caps_field_at(uint32_t i, uint32_t *off, const char **name, uint32_t *value,
                           int *from_guest) {
    if (i >= g_caps_used) return 0;
    if (off) *off = g_caps[i].off;
    if (name) *name = g_caps[i].name;
    if (value) *value = g_caps[i].value;
    if (from_guest) *from_guest = g_caps[i].from_guest;
    return 1;
}
