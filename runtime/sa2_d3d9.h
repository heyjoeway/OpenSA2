/* A recording no-op Direct3D 9 device.
 *
 * Design 5 S4's work list asks for "presentation stubs as no-ops that record
 * calls". This is that, for d3d9: `Direct3DCreate9` hands back a real object
 * in guest memory with a synthetic vtable, every method call is recorded, and
 * each method returns the least committal success that lets the guest walk on.
 * It is not a renderer and it is not a bid to be one -- S5 owns that choice
 * (`Documentation/S5_INPUTS.md`), and nothing here presumes its outcome.
 *
 * Why this is not `sa2_iface.c`'s survey
 * --------------------------------------
 * The survey answers 0 to a bounded number of methods and writes no
 * out-parameter, so the guest walks until it dereferences something the survey
 * did not fill. That collected six method names and then stopped. It also had
 * a defect this file exists partly to avoid repeating: a `__stdcall` COM
 * method whose declared argument count is wrong pops the wrong number of
 * dwords, and the caller's frame drifts silently -- every later `[esp+n]` in
 * that function reads the wrong local. `IDirect3D9::CheckDeviceMultiSampleType`
 * takes six arguments and the survey table said five, twice, so by the time the
 * guest reached `CreateDevice` its frame was eight bytes out and the
 * `DeviceType` argument the previous slice recorded (`0x02801b38`) is a stack
 * pointer, not `D3DDEVTYPE_HAL`.
 *
 * Three rules follow, and they are the whole of this file's safety story:
 *
 *  1. **Every slot this file answers is named and carries its own argument
 *     count**, taken from the published signature. A slot with no row is
 *     refused by name; it is never popped with a guessed count, because a
 *     guessed pop corrupts the caller rather than producing a wrong answer,
 *     and a corrupted frame is the failure this project is worst at
 *     diagnosing.
 *  2. **A method with an out-parameter and no handler refuses.** The guest
 *     dereferences what a real method would have written, so answering S_OK
 *     and writing nothing hands it whatever was on its stack. `DF_OUT` marks
 *     those rows and the dispatcher enforces it.
 *  3. **Every fabricated answer is labelled.** A value this runtime invented
 *     is flagged `invented` in the log with the reason; a value derived from
 *     something the guest itself supplied (the presentation parameters, a
 *     previous `Set`) is flagged `from-guest`. A reader of the log can always
 *     tell which of the two a number is, because a plausible-but-wrong
 *     capability is a divergence S5 would inherit blind.
 *
 * And a fourth, since the user's ruling of 2026-10-09 (Documentation/decisions/
 * 2026-10-09-S5-user-rulings.md, item 1):
 *
 *  4. **The world is closed.** A method answers only if it is on the M7 list,
 *     the `called` set of Documentation/receipts/d3d9-methods.json, which
 *     tools/s4/m7_device_list.py generates into runtime/sa2_d3d9_m7_list.h. Any
 *     other method traps by name with its caller, whatever its row's handler,
 *     in a gate or an automated run. In live play -- SA2_INPUT_DI_SOURCE naming
 *     a FIFO, the live keyboard source, in a session with a person at it (a
 *     play_live --synthetic-keys session, whose recorder counts go to
 *     counts.synthetic.json, is automated and traps) -- it is logged loudly to
 *     stderr once, counted, answered as before (so one with no handler still
 *     refuses), and reported in a summary line at exit and in the run record
 *     (sa2_d3d9_closed_world_json). The stderr lines start "*** d3d9 CLOSED
 *     WORLD"; tools/s4/loop_run.py copies them into its own output, a live
 *     session's game.log. sa2_d3d9.c's comment on the closed world has the
 *     three modes.
 *
 * What "least committal success" means here
 * -----------------------------------------
 * Capability queries cannot be answered honestly by a device that presents
 * nothing: there is no adapter to report and no format to test. This file does
 * not pretend otherwise. It answers them, marks every one `invented`, and
 * counts them separately, so the fabricated set is an enumerated list rather
 * than an assumption. `SA2_D3D9_STRICT_CAPS=1` refuses every one of them by
 * name instead, which is how the list is shown to be load-bearing rather than
 * asserted to be.
 *
 * Environment
 * -----------
 *   SA2_D3D9_DEVICE=1        Direct3DCreate9 returns this recording device
 *                            (default: it refuses by name, as before)
 *   SA2_D3D9_STRICT_CAPS=1   every fabricated capability answer refuses instead
 *   SA2_D3D9_TEXMEM=<bytes>  GetAvailableTextureMem's named placeholder
 *   SA2_D3D9_REFRESH=<hz>    the virtual display's named refresh rate
 *   SA2_D3DX_CONSTANT_TABLE=1 D3DXGetShaderConstantTable answers what native
 *                            answers: D3D_OK and an ID3DXConstantTable, for a
 *                            vertex or pixel shader whose bytecode carries a
 *                            CTAB comment. The table answers QueryInterface,
 *                            AddRef and Release as COM requires and refuses
 *                            every other method by name, because native calls
 *                            none of them; QueryInterface and AddRef are off the
 *                            M7 list, so the closed world traps them (rule 4).
 *                            Off by default: the import then refuses (E_FAIL,
 *                            no table).
 *   SA2_D3DX_STRICT=1        every d3dx9 entry point that answers S_OK without
 *                            producing the bytes a real D3DX would have
 *                            produced -- a filtered mip chain, a converted
 *                            surface, a file on the host's disk -- refuses by
 *                            name instead. It is SA2_D3D9_STRICT_CAPS for the
 *                            d3dx side: the list of no-op successes is shown
 *                            to be load-bearing rather than asserted to be.
 *   SA2_D3D9_CAPS=maximal    an experiment, off by default: a maximal
 *                            D3DCAPS9 profile instead of zeros (sa2_d3d9.c's
 *                            caps comment says why)
 *   SA2_D3D9_SHADER_MODEL=n  0..3; the vs/ps version pair D3DCAPS9 reports,
 *                            which is the only part of that struct this image
 *                            reads and it selects the game's renderer tier
 *   SA2_D3D9_BREAK=argc      IDirect3D9::CheckDeviceMultiSampleType pops five
 *                            arguments where it takes six, the survey's defect,
 *                            so CreateDevice's DeviceType comes from the wrong
 *                            stack slot
 *   SA2_D3D9_BREAK=out       a method with an out-parameter answers S_OK and
 *                            writes nothing through it
 *   SA2_D3D9_BREAK=noimpl    no handler runs: every method that has one answers
 *                            S_OK and writes nothing, as the survey did
 *   SA2_D3D9_BREAK=guestheap a locked resource's backing store is bumped from
 *                            SA2_GUEST_HEAP_BASE, bypassing the guest heap's
 *                            allocator, as it was before S4 slice 7
 *   SA2_D3D9_BREAK=texguess  the image loaders stop reading headers and
 *                            fabricate 256x256 A8R8G8B8 for every blob
 *   SA2_D3D9_BREAK=fakepixels the runtime fills a file-loaded texture's surface
 *                            with a pattern, i.e. pretends to have decoded it
 *   SA2_D3D9_BREAK=texunknown every blob reads as a container this runtime
 *                            cannot name, so the refusal path runs
 *   SA2_D3D9_BREAK=wideobj   objects 64 bytes apart, as they were until
 *                            2026-09-23, with a sentinel over words 4..15
 *   SA2_D3D9_BREAK=wideobjzero the same, with words 4..15 left zero: the layout
 *                            exactly as it was until 2026-09-23
 *   SA2_D3D9_BREAK=wideobjvt wideobj with every object's vtable pointer
 *                            overwritten by the sentinel as well, so the first
 *                            method call faults: the control that must differ
 *   SA2_D3D9_BREAK=neverfree the lifetime model as it was until 2026-09-23: a
 *                            fresh surface with its own count and store on
 *                            every GetSurfaceLevel, no reference for a binding,
 *                            and no backing store ever freed
 *   SA2_D3D9_BREAK=ownzero   the same model, each store freed when its own
 *                            object's count reaches zero
 *   SA2_D3D9_BREAK=keepall   the Direct3D 9 model with nothing freed at a final
 *                            release
 *   SA2_D3D9_BREAK=tableanswer ID3DXConstantTable::GetConstantByName answers a
 *                            null handle instead of refusing by name
 *   SA2_D3D9_BREAK=tablekeep a constant table's final Release answers 0 and
 *                            leaves the table alive and uncounted
 *   SA2_D3D9_BREAK=presentwait each frame's wait at Present and never at
 *                            BeginScene, as the device did until 2026-09-26
 *   SA2_D3D9_LIFE_DUMP=<path> a host-side text dump of every object, its count,
 *                            bindings, store and creating call, and of every
 *                            method's distinct guest callers, written when the
 *                            backing store cannot be allocated and at exit
 *                            (<path>.exit); it changes nothing the guest sees
 *   SA2_INPUT_DI_SOURCE=<FIFO> (runtime/sa2_dinput.c's live keyboard source) puts
 *                            the closed world in log mode instead of trap mode,
 *                            unless SA2_REC_COUNTS names a file counts.synthetic.json
 *                            (play_live's automated sessions), which keeps it in
 *                            trap mode. No variable opens this device's closed
 *                            world: only a unit build compiled with
 *                            SA2_D3D9_OPEN_WORLD_UNIT_BUILD answers an unlisted
 *                            method without logging it. (With SA2_D3D9_DEVICE off
 *                            this device is not the one the guest gets:
 *                            SA2_D3D9_SURVEY=<n> has Direct3DCreate9 return
 *                            runtime/sa2_iface.c's S4 survey object, which answers
 *                            its first n calls with 0 and is outside the closed
 *                            world; every reach, replay and play configuration
 *                            turns this device on.)
 *
 * argc, out, noimpl and guestheap are tools/s4/d3d9_break.py's, each checked
 * against a control run, and the three wideobj breaks are
 * tools/s4/d3d9_stride_check.py's, each compared with a run at the 16-byte
 * stride. The three texture breaks have no tool of their own; their runs are
 * recorded under `breaks` in Documentation/receipts/s4-texture-containers-v1.json.
 * The three lifetime breaks are tools/s4/d3d9_life_check.py's, each run from
 * boot through the attract demo beside the model's own run, and so is a short
 * fakepixels run that reads the byte count a released texture keeps; its
 * receipt, Documentation/receipts/s4-d3d9-life-v1.json, holds what each run did.
 * The two table breaks are harness/d3dx_table_probe.c's: under each, the
 * probe that passes without it must fail, and
 * Documentation/scripts/tests/test_d3dx_table_probe.py runs all three.
 * presentwait is tools/s4/frame_wait_check.py's: under it Sofdec's first
 * playback handle is served inside the tick that creates it, where every
 * registered native boot serves it in the next, and without it the lifted boot
 * follows them on that word (runtime/sa2_d3d9.c, frame_wait).
 */
#ifndef SA2_RUNTIME_D3D9_H
#define SA2_RUNTIME_D3D9_H

#include <stdint.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Synthetic method thunks. Above `sa2_iface.c`'s region (0x00202000 ..
 * 0x00206000) and below the image at 0x00400000, so every synthetic entry
 * point this runtime invents is identifiable by its address alone. */
#define SA2_D3D9_THUNK_BASE   0x00210000u
#define SA2_D3D9_THUNK_STRIDE 16u
#define SA2_D3D9_MAX_TYPES    24u
#define SA2_D3D9_MAX_SLOTS    128u   /* IDirect3DDevice9 has 119 */
#define SA2_D3D9_THUNK_END    (SA2_D3D9_THUNK_BASE + \
                               SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS * SA2_D3D9_THUNK_STRIDE)

/* Fabricated objects and their vtables, in guest scratch and clear of
 * `sa2_iface.c`'s 0x03100000 block.
 *
 * An object's guest bytes are four words (vtable, tag, type, index), so the
 * stride is 16: the same 256 KiB holds 16384 objects where a 64-byte stride
 * held 4096. The pool is a bump allocator and a slot is never reused, even
 * after its object's final release frees the backing store (see that store's
 * comment in sa2_d3d9.c). Reusing one would be safe only if no guest word
 * still held the old address, and nothing here can show that: the guest keeps
 * these pointers in its own heap objects, and a stale one aimed at a reused
 * slot would silently call the new object. A destroyed object keeps its slot
 * and says it is destroyed; outside neverfree and ownzero, a Lock through it
 * stops the run by name. So every level load still takes new slots for the
 * resources it creates -- a texture's level surfaces are one slot per level,
 * taken once -- and a Metal Harbor run driven through two deaths and restarts
 * exhausted 4096 at its third load (2026-09-23).
 *
 * The pool is two blocks, and an object's pool index decides its block:
 *
 *   indices 0 .. 16383       SA2_D3D9_OBJ_BASE + index * 16, the first block,
 *                            0x03200000 .. 0x03240000. Every object at these
 *                            indices has the address and the four words it
 *                            had when the pool was this block alone, so a run
 *                            that builds no more than 16384 objects is the run
 *                            it was (harness/d3d9_pool_probe.c holds the block
 *                            to the runtime before the overflow block).
 *   indices 16384 .. 143359  SA2_D3D9_OBJ_OVF_BASE + (index - 16384) * 16,
 *                            the overflow block, 0x00010000 .. 0x00200000.
 *
 * A live Metal Harbor session played through menus and four loads of the
 * stage exhausted the first block alone at tick 30028 on 2026-10-07; one boot
 * and one Metal Harbor load played to the goal ring, the registered replay,
 * builds 2256 objects. The overflow block is the low free space between the
 * guest's 64 KiB null region and the import thunks (runtime/guest_memory.h's
 * layout): below SA2_GUEST_IMPORT_BASE, where the 0x00200000..0x00300000
 * block this runtime keeps for entry points begins, and so below every thunk
 * region and the image; nothing else in the runtime maps it, and
 * Documentation/handle-regions.json declares it beside the first block so
 * check_handle_regions.py holds it clear of every other region. It starts at
 * 0x00010000 rather than at the 4 KiB null guard, so that no object's address
 * is a value Win32 reads as an integer resource or atom (below 0x10000,
 * IS_INTRESOURCE) and a guest read through a small integer taken for a pointer
 * does not land in an object. A game that loads levels without end still runs
 * out of slots; the bytes behind a destroyed one go back to the guest heap.
 *
 * Only the 16-byte layout has the overflow block. The wideobj breaks put back
 * the layout of 2026-09-23, whose pool was the first block's 256 KiB at 64
 * bytes a slot, 4096 objects, and that stays their bound. */
#define SA2_D3D9_OBJ_BASE        0x03200000u
#define SA2_D3D9_OBJ_STRIDE      16u
#define SA2_D3D9_OBJ_FIRST_COUNT 16384u        /* objects in the first block */
#define SA2_D3D9_OBJ_OVF_BASE    0x00010000u
#define SA2_D3D9_OBJ_OVF_COUNT   126976u       /* objects in the overflow block */
#define SA2_D3D9_POOL_OBJECTS    (SA2_D3D9_OBJ_FIRST_COUNT + SA2_D3D9_OBJ_OVF_COUNT)
#define SA2_D3D9_VT_BASE         0x03240000u
#define SA2_D3D9_VT_STRIDE       (SA2_D3D9_MAX_SLOTS * 4u)

/* The pool index of the object a 16-byte-layout pool would place at guest
 * address `addr`, or -1 when no object of that layout sits there: outside
 * both blocks, or not on a 16-byte boundary inside one. It is arithmetic
 * only; whether an object has been built at that index is the runtime's to
 * say (its record carries the index as word 3). For readers outside the
 * runtime -- the recorder -- that hold one record per pool index. */
static inline int32_t sa2_d3d9_obj_index(uint32_t addr) {
    if (addr >= SA2_D3D9_OBJ_BASE &&
        addr - SA2_D3D9_OBJ_BASE < SA2_D3D9_OBJ_FIRST_COUNT * SA2_D3D9_OBJ_STRIDE) {
        const uint32_t off = addr - SA2_D3D9_OBJ_BASE;
        return off % SA2_D3D9_OBJ_STRIDE ? -1 : (int32_t)(off / SA2_D3D9_OBJ_STRIDE);
    }
    if (addr >= SA2_D3D9_OBJ_OVF_BASE &&
        addr - SA2_D3D9_OBJ_OVF_BASE < SA2_D3D9_OBJ_OVF_COUNT * SA2_D3D9_OBJ_STRIDE) {
        const uint32_t off = addr - SA2_D3D9_OBJ_OVF_BASE;
        return off % SA2_D3D9_OBJ_STRIDE
                   ? -1 : (int32_t)(SA2_D3D9_OBJ_FIRST_COUNT + off / SA2_D3D9_OBJ_STRIDE);
    }
    return -1;
}
/* The guest address of pool index `index` in the 16-byte layout, 0 past the
 * pool. The inverse of sa2_d3d9_obj_index. */
static inline uint32_t sa2_d3d9_obj_addr(uint32_t index) {
    if (index < SA2_D3D9_OBJ_FIRST_COUNT) return SA2_D3D9_OBJ_BASE + index * SA2_D3D9_OBJ_STRIDE;
    if (index < SA2_D3D9_POOL_OBJECTS)
        return SA2_D3D9_OBJ_OVF_BASE + (index - SA2_D3D9_OBJ_FIRST_COUNT) * SA2_D3D9_OBJ_STRIDE;
    return 0u;
}

/* What follows an object's four words, as `sa2_d3d9_stats.object_fill` reports
 * it. Only the wideobj breaks leave room for anything; runtime/sa2_d3d9.c says
 * why each exists. */
enum {
    SA2_D3D9_FILL_NONE     = 0,   /* the 16-byte stride: the next object follows */
    SA2_D3D9_FILL_SENTINEL = 1,   /* wideobj: 64 bytes apart, words 4..15 a sentinel */
    SA2_D3D9_FILL_ZERO     = 2,   /* wideobjzero: 64 apart, words 4..15 zero, as until
                                     2026-09-23 */
    SA2_D3D9_FILL_VTABLE   = 3,   /* wideobjvt: the sentinel fill, and word 0 overwritten
                                     by it too */
};

/* HMONITOR tokens. The window token kinds stop at HBRUSH (0x7e300000); this
 * continues the same scheme rather than inventing a second one. */
#define SA2_D3D9_HMONITOR_BASE 0x7e400000u

/* HRESULTs this file returns, by their published values. */
#define SA2_D3D_OK                0x00000000u
#define SA2_D3DERR_NOTAVAILABLE   0x8876086Au
#define SA2_D3DERR_INVALIDCALL    0x8876086Cu
#define SA2_E_NOINTERFACE         0x80004002u

/* How an answer was arrived at. The distinction is the point of the log. */
enum {
    SA2_D3D9_ANS_REFUSED = 0,   /* recorded, then trapped by name */
    SA2_D3D9_ANS_NOOP    = 1,   /* recorded; nothing to answer, S_OK */
    SA2_D3D9_ANS_GUEST   = 2,   /* answered from something the guest supplied */
    SA2_D3D9_ANS_INVENT  = 3,   /* answered with a value this runtime invented */
    SA2_D3D9_ANS_OBJECT  = 4,   /* answered by fabricating an object */
    SA2_D3D9_ANS_UNSET   = 5,   /* a Get with no matching Set; the D3D9 default */
};

#define SA2_D3D9_LOG_MAX 8192u

typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t self;
    uint16_t type;              /* index into the interface table */
    uint16_t slot;
    uint8_t  answered;          /* SA2_D3D9_ANS_* */
    uint8_t  argc;
    uint32_t arg[8];
    uint32_t ret;
} sa2_d3d9_call;

/* One row per (interface, slot) ever called, so the *counts* are exact even
 * when the ordered log fills. This is the dynamic method surface. */
typedef struct {
    uint32_t calls;
    uint32_t first_seq;
    uint32_t first_caller;
    uint8_t  answered;          /* the first answer's class */
    uint8_t  used;
} sa2_d3d9_slot_stat;

typedef struct {
    uint32_t created;           /* Direct3DCreate9 calls answered with an object */
    uint32_t method_calls;
    uint32_t refused, noop, from_guest, invented, objects, unset;
    uint32_t objects_built;
    uint32_t object_stride;     /* bytes between objects: 16, or 64 under a wideobj break */
    uint32_t object_fill;       /* SA2_D3D9_FILL_*: what follows an object's four words */
    uint32_t distinct_methods;  /* distinct (interface, slot) pairs called */
    uint32_t present_calls;     /* IDirect3DDevice9::Present and IDirect3DSwapChain9::Present;
                                 * only the device's can take the frame's wait
                                 * (sa2_d3d9.c, `frame_wait`), and the boot calls no
                                 * swap chain's Present */
    uint32_t beginscene_calls, endscene_calls, draw_calls, clear_calls;
    uint32_t strict_caps;
    uint32_t guest_bytes_allocated;   /* every backing store handed out, freed or not */
    uint32_t shader_model;
    /* Method calls whose `this` is not an object this runtime fabricated.
     *
     * It is the cheapest continuous check that no method's declared argument
     * count is wrong. A `__stdcall` COM method pops `this` plus its arguments;
     * a count that is off drifts the caller's frame, and a drifted frame
     * pushes the wrong dword as the next call's `this`. That next `this` is
     * then some stack or heap value rather than one of these objects, and this
     * counter moves. It caught nothing on the run that shipped, and under
     * SA2_D3D9_BREAK=argc it moves -- which is the only way to know it can. */
    uint32_t unknown_this;
    /* Textures created from a blob the guest handed to d3dx9. */
    uint32_t tex_from_file;          /* calls answered with a texture */
    uint32_t tex_from_file_refused;  /* calls refused by name */
    uint32_t tex_bytes_hashed;       /* source bytes read to compute the hashes */
    /* Textures whose surface holds pixels decoded from the blob. It is zero by
     * construction -- nothing in this runtime decodes an image, and S5 owns the
     * asset pipeline -- and it is a counter rather than a comment so a reader of
     * the JSON is told rather than left to assume. SA2_D3D9_BREAK=fakepixels
     * moves it, which is the only way to know it can. */
    uint32_t tex_with_decoded_pixels;
    uint32_t tex_rows_dropped;       /* calls past the record table's capacity */
    /* The d3dx9 entry points whose whole effect is bytes -- a filtered mip
     * chain, a converted surface, a file on the host's disk. This runtime
     * produces none of those, so each one is a recorded no-op success and each
     * is counted separately; a reader of the JSON is told how many times the
     * guest asked for bytes it did not get, rather than left to infer it from
     * a method surface that says "implemented". `SA2_D3DX_STRICT=1` refuses
     * every one of them by name, which is how the list is shown to matter. */
    uint32_t d3dx_filter_calls;      /* D3DXFilterTexture: mip chains not generated */
    uint32_t d3dx_filter_levels;     /* levels those calls would have filled */
    uint32_t d3dx_surface_loads;     /* D3DXLoadSurfaceFromSurface calls */
    uint32_t d3dx_surface_copied;    /* ... that moved the guest's own bytes verbatim */
    uint32_t d3dx_surface_bytes;     /* bytes those moved */
    uint32_t d3dx_saves;             /* D3DXSave{Surface,Texture}ToFileA calls */
    uint32_t d3dx_save_bytes_written;/* 0 by construction: no host file is opened */
    uint32_t d3dx_save_rows_dropped; /* calls past the save record's capacity */
    uint32_t strict_d3dx;            /* SA2_D3DX_STRICT, echoed for the receipt */
    /* The lifetime model (sa2_d3d9.c, the backing store's comment). Each
     * counter is kept under every model, and reads as follows under the
     * Direct3D 9 model, which every break but neverfree and ownzero runs, and
     * under the old model those two put back, in which an object is
     * destroyed at its own count of zero whatever binds it:
     *
     *  objects_destroyed  true final releases, no count and no binding left,
     *                     a texture's level surfaces counted as they go with
     *                     it; under the old model, objects whose own count
     *                     reached zero.
     *  guest_bytes_freed  backing store given back to the guest heap: at a
     *                     true final release, and when a store that grows
     *                     leaves a block no pointer was handed out of. keepall
     *                     frees only the second; ownzero frees at an object's
     *                     own zero and never the second; neverfree frees
     *                     nothing.
     *  level_surfaces     texture level surfaces built, one per level asked
     *                     for; 0 under the old model, whose GetSurfaceLevel
     *                     builds a plain surface on every call. */
    uint32_t objects_destroyed;
    uint32_t guest_bytes_freed;
    uint32_t level_surfaces;
    /* Two things undefined in Direct3D. A model that frees too early shows up
     * here first, so the first caller of each is kept.
     *
     *  calls_on_destroyed  method calls whose `this` is destroyed and d3dx
     *                      calls naming a destroyed resource, under every
     *                      model -- under the old one, an object past its own
     *                      zero -- and, under the Direct3D 9 model, a Set that
     *                      binds a destroyed resource (the old model keeps no
     *                      bindings).
     *  releases_past_zero  Releases of a count already at zero; a level
     *                      surface's count is its texture's under the
     *                      Direct3D 9 model, and a surface's own under the old
     *                      one. */
    uint32_t calls_on_destroyed, calls_on_destroyed_first_caller;
    uint32_t releases_past_zero, releases_past_zero_first_caller;
    /* ID3DXConstantTable (SA2_D3DX_CONSTANT_TABLE; the table's comment in
     * sa2_d3d9.c). Zero with the switch off, when the import refuses instead.
     *
     *  constant_tables_built      D3DXGetShaderConstantTable calls answered
     *                             with a table, one per call
     *  constant_tables_destroyed  tables whose final Release destroyed them;
     *                             a table has no store, so this is the count
     *                             of the release, not of bytes
     *  constant_table_qi_answered QueryInterface calls answered S_OK
     *  constant_table_qi_declined QueryInterface calls answered E_NOINTERFACE
     *
     * Every other method refuses by name, so its count is in the method
     * surface (sa2_d3d9_slot_stat_at) and the run stops at the first. */
    uint32_t constant_table;          /* SA2_D3DX_CONSTANT_TABLE, echoed for the record */
    uint32_t constant_tables_built;
    uint32_t constant_tables_destroyed;
    uint32_t constant_table_qi_answered;
    uint32_t constant_table_qi_declined;
    /* The method sequence number of the last Present, the device's or a swap
     * chain's, so a record can say whether a call came after the game's last
     * frame: a table's final Release (sa2_d3d9_ctab.released_seq), say. */
    uint32_t last_present_seq;
    /* Where each frame's wait (sa2_sched_frame_boundary) was taken: at
     * IDirect3DDevice9::BeginScene, where the adopted oracle's pace is (the
     * Mod Loader's frame limiter runs in its BeginScene wrapper, option A of
     * Documentation/decisions/2026-09-24-S4-110h-review.md), or at a Present
     * no BeginScene came before since the last one, which keeps a loop that
     * presents without drawing a scene from holding the guest lock forever.
     * Under SA2_D3D9_BREAK=presentwait every wait is at Present, as it was
     * until 2026-09-26, and none at BeginScene. */
    uint32_t frame_waits_beginscene;
    uint32_t frame_waits_present;
} sa2_d3d9_stats;

/* ---------------------------------------------- textures loaded from blobs
 *
 * Design 3.5 asks this stage's d3dx stubs to record "textures by hash". One
 * row per `D3DX*TextureFromFileInMemoryEx` call, including the refused ones,
 * so a refusal is a work item with an address rather than a dead run.
 *
 * The cube and volume creators share this row rather than getting one of their
 * own. Everything a reader wants of them -- the blob, its hash, the container,
 * the header's own numbers, what was created -- is the same set of questions
 * the 2D row already answers, and two tables would make "how many textures did
 * this run load" a sum a reader has to know to compute. What the three do not
 * share is a third dimension, so the row carries `kind` and a depth, and
 * `file_caps2` records the DDS surface-complexity flags the cube and volume
 * entry points are checked against. */
enum {                              /* which D3DX creator produced the row */
    SA2_TEXK_2D     = 0,
    SA2_TEXK_CUBE   = 1,
    SA2_TEXK_VOLUME = 2,
};
enum {                              /* containers this runtime can name */
    SA2_TEXC_UNKNOWN = 0,
    SA2_TEXC_DDS     = 1,
    SA2_TEXC_PNG     = 2,
    SA2_TEXC_BMP     = 3,
    SA2_TEXC_JPEG    = 4,
    SA2_TEXC_GIF     = 5,
    SA2_TEXC_TIFF    = 6,
    SA2_TEXC_HDR     = 7,
    SA2_TEXC_PPM     = 8,
};
enum {                              /* where a recorded value came from */
    SA2_TEXP_NONE    = 0,
    SA2_TEXP_STATED  = 1,   /* the file's header states it */
    SA2_TEXP_DERIVED = 2,   /* from the header by a published D3DX rule */
    SA2_TEXP_CALLER  = 3,   /* the guest passed an explicit value */
    SA2_TEXP_API     = 4,   /* D3DX's documented default for D3DX_DEFAULT */
    SA2_TEXP_INVENT  = 5,   /* SA2_D3D9_BREAK=texguess only */
};

typedef struct {
    uint32_t seq;                   /* 1-based call index */
    uint32_t caller;                /* the guest address that called d3dx9 */
    uint32_t obj;                   /* the texture's guest address, 0 when refused */
    uint32_t src, src_len;          /* the blob, as the guest passed it */
    uint8_t  sha256[32];            /* over all src_len bytes */
    uint32_t hashed_bytes;
    uint8_t  magic[8];              /* the container signature, for a refusal */
    uint8_t  container;             /* SA2_TEXC_* */
    uint8_t  kind;                  /* SA2_TEXK_*: which creator the guest called */
    uint8_t  parsed;                /* the header was read */
    uint8_t  refused;               /* the call refused instead of answering */
    uint8_t  pixels_decoded;        /* 0; see sa2_d3d9_stats.tex_with_decoded_pixels */
    uint8_t  runtime_wrote_pixels;  /* SA2_D3D9_BREAK=fakepixels only */
    uint8_t  dim_from, fmt_from, levels_from;   /* SA2_TEXP_* */
    uint32_t file_w, file_h, file_depth, file_levels, file_fmt;
    uint32_t file_caps2;            /* DDS dwCaps2: the cubemap and volume bits */
    uint32_t png_bit_depth, png_colour_type, png_interlace, png_trns;
    uint32_t want_w, want_h, want_d, want_levels, want_fmt;  /* as the guest passed them */
    uint32_t w, h, d, levels, fmt;  /* the texture as created; d is 1 unless volume */
    uint32_t surface;               /* the backing store, 0 until the guest locks */
    uint32_t reserved;              /* guest bytes actually taken for it */
    uint32_t chain_bytes;           /* guest bytes a complete chain would need */
    uint32_t level0_bytes;
    uint32_t locks;                 /* LockRect calls on this texture */
    /* The store's non-zero bytes, counted at the texture's final release just
     * before the store goes back to the guest heap, when `released` is 1: after
     * it, `surface` is 0 and there is nothing left in guest memory to count. */
    uint32_t released_nonzero;
    uint8_t  released;
    /* 1 once a verbatim D3DXLoadSurfaceFromSurface copied into one of this
     * texture's levels: bytes moved from another surface without a Lock, which
     * are the guest's and not this runtime's (sa2_d3d9.c says when it copies). */
    uint8_t  guest_copied;
} sa2_d3d9_texsrc;

/* 1 when this runtime's SHA-256 reproduces the published test vectors. A hash
 * function that is wrong is invisible: every row would carry a plausible
 * constant and every run would agree with every other run. */
int                      sa2_d3d9_sha256_selftest(void);
uint32_t                 sa2_d3d9_texsrc_count(void);
const sa2_d3d9_texsrc   *sa2_d3d9_texsrc_at(uint32_t i);
const char              *sa2_d3d9_container_name(uint32_t container);
const char              *sa2_d3d9_provenance_name(uint32_t p);
/* Bytes of that texture's surface that are not zero, counted from guest memory
 * at the moment of the call -- or, once the texture's final release has given
 * its store back to the guest heap, counted at that release (`released`). The
 * surface is handed over zero-filled and this runtime never writes a pixel
 * into it, so a non-zero count means the guest wrote through a Lock (`locks`
 * says how many it took), a verbatim D3DXLoadSurfaceFromSurface moved the
 * guest's bytes in (`guest_copied`), or the fakepixels break wrote them. It is
 * read out of guest memory rather than from a flag so that the claim is
 * checked and not merely declared. */
uint32_t sa2_d3d9_texsrc_nonzero(uint32_t i);

/* ------------------------------------------- files the guest asked us to write
 *
 * `D3DXSaveSurfaceToFileA` and `D3DXSaveTextureToFileA` are the only two d3dx9
 * entry points whose effect is outside the process, and this runtime writes no
 * host file for either (see runtime/sa2_d3d9.c for why, and for the two call
 * sites). A no-op success with nothing recorded would erase the one thing the
 * call is evidence of -- **the path the guest chose** -- so every call leaves a
 * row here with that path, whether or not the runtime could read it. This is a
 * different event from creating a texture, not a second texture table: there is
 * no blob, no hash and no container, and folding it into `sa2_d3d9_texsrc`
 * would leave two thirds of every row empty. */
#define SA2_D3D9_SAVE_PATH 160u

typedef struct {
    uint32_t seq;                   /* 1-based call index across both entry points */
    uint32_t caller;                /* the guest address that called d3dx9 */
    uint32_t obj;                   /* the surface or texture it asked to save */
    uint32_t src_idx;               /* 1-based row in the texture record, 0 when none */
    uint32_t iff;                   /* D3DXIMAGE_FILEFORMAT, as the guest passed it */
    uint32_t w, h, fmt;             /* the source, as this runtime recorded it */
    uint32_t bytes_written;         /* 0 by construction; see d3dx_save_bytes_written */
    uint8_t  texture;               /* 1 SaveTextureToFileA, 0 SaveSurfaceToFileA */
    uint8_t  path_ok;               /* the path was readable out of guest memory */
    char     path[SA2_D3D9_SAVE_PATH];
} sa2_d3d9_savefile;

uint32_t                 sa2_d3d9_savefile_count(void);
const sa2_d3d9_savefile *sa2_d3d9_savefile_at(uint32_t i);
/* D3DXIMAGE_FILEFORMAT by name, so a recorded row says "dds" and not "4". */
const char              *sa2_d3d9_iff_name(uint32_t iff);

void sa2_d3d9_config_from_env(void);
int  sa2_d3d9_device_enabled(void);
/* d3d9.dll!Direct3DCreate9(SDKVersion): the guest pointer to a fabricated
 * IDirect3D9, or 0 when the device is not enabled (the caller then refuses). */
uint32_t sa2_d3d9_make_d3d9(uint32_t caller);

/* The display a d3d9 adapter presents, for a module that has to name the same
 * display: runtime/sa2_ddraw.c hands the game, for each DirectDraw device on
 * adapter k's monitor, what GetAdapterMonitor(k) answers, because the game
 * finds its display by comparing the two (0x00865C27). It returns what the
 * method answers, without recording a call, so the answer has one copy. */
uint32_t sa2_d3d9_adapter_monitor(uint32_t adapter);  /* IDirect3D9::GetAdapterMonitor */

/* d3dx9_42's texture creators are thin wrappers over IDirect3DDevice9, so
 * they are answered by the same fabricated object pool rather than by a second
 * one. Returns an HRESULT and writes the new texture's guest pointer to
 * `out_pp`. `device` is the guest pointer the guest passed; a pointer that is
 * not this runtime's device is refused rather than ignored. */
uint32_t sa2_d3d9_d3dx_create_texture(uint32_t device, uint32_t w, uint32_t h,
                                      uint32_t levels, uint32_t usage, uint32_t format,
                                      uint32_t pool, uint32_t out_pp, uint32_t caller);

/* d3dx9_42!D3DXCreateTextureFromFileInMemoryEx. `a` is the import's fifteen
 * arguments in order; the container is identified from the blob's own magic,
 * the size comes out of that container's header, and a blob whose container
 * this runtime cannot name or cannot parse is refused with its magic bytes,
 * never given an invented size. Writes the texture to `a[14]` and, when `a[12]`
 * is non-null, a D3DXIMAGE_INFO the file supplied. Every call, answered or
 * refused, leaves a `sa2_d3d9_texsrc` row. */
uint32_t sa2_d3d9_d3dx_texture_from_memory(const uint32_t *a, uint32_t caller);

/* ------------------------------------------------ the other eight d3dx9 entry
 * points the image imports. Each takes the import's arguments in order, so the
 * adapter in runtime/sa2_imports.c is one line and the argument count lives in
 * one place -- the `g_stubs` row -- rather than being restated as a C signature
 * that can drift from it. Every count below was read off the *call site's own
 * pushes* as well as the published signature; the two disagreed for
 * `D3DXSaveSurfaceToFileA`, `D3DXLoadSurfaceFromSurface`, and both `...Ex`
 * creators in the note that reached this runtime, and the image won.
 *
 *   D3DXCreateCubeTexture                      7   0x00866b97
 *   D3DXCreateVolumeTexture                    9   0x00866af1
 *   D3DXCreateCubeTextureFromFileInMemoryEx   14   0x008668e2
 *   D3DXCreateVolumeTextureFromFileInMemoryEx 16   0x00866848
 *   D3DXFilterTexture                          4   0x0086763e
 *   D3DXLoadSurfaceFromSurface                 8   0x008676ad, 0x00867745, 0x008677fa
 *   D3DXSaveSurfaceToFileA                     5   0x00869580
 *   D3DXSaveTextureToFileA                     4   0x008679d6, 0x008679e6
 */
uint32_t sa2_d3d9_d3dx_create_cube_texture(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_create_volume_texture(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_cube_from_memory(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_volume_from_memory(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_filter_texture(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_load_surface_from_surface(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_save_surface_to_file(const uint32_t *a, uint32_t caller);
uint32_t sa2_d3d9_d3dx_save_texture_to_file(const uint32_t *a, uint32_t caller);

/* d3dx9_42!D3DXGetShaderConstantTable(pFunction, ppConstantTable), with
 * SA2_D3DX_CONSTANT_TABLE=1 (sa2_d3d9_constant_table_enabled; with it off the
 * import refuses in runtime/sa2_imports.c and never calls this, and this
 * refuses by name if it is called). `a` is the import's two arguments in
 * order. For a vertex or pixel shader's bytecode, finds its CTAB comment the
 * way D3DXFindShaderComment does, and answers D3D_OK with a fresh
 * ID3DXConstantTable written to `a[1]`. Any other version token, a shader with
 * no well-formed CTAB, a null pFunction or out-pointer, or the d3d9 device off
 * refuses by name. The table answers QueryInterface, AddRef and Release as COM
 * requires and implements owned GetConstantByName/GetConstantDesc for the bounded
 * numeric/sampler layouts documented in sa2_d3d9.c. Other methods refuse by name.
 * Native reflection conformance and setters remain separate work. */
int      sa2_d3d9_constant_table_enabled(void);
uint32_t sa2_d3d9_d3dx_get_shader_constant_table(const uint32_t *a, uint32_t caller);

/* One row per table the entry point built, in the order it built them, so a
 * run's record can be held to the native trace's table headers call by call.
 * `version`, `constants` and `ctab_bytes` are the numbers Wine's d3dx9 prints
 * for each call (the CTAB header's Version and Constants, and the comment's
 * data bytes after its fourcc, which D3DXFindShaderComment returns as the
 * size), read out of the guest's bytecode during the call: the guest frees
 * that buffer as soon as the call returns (0x0086722f), so each live object owns a full CTAB snapshot independently of these
 * diagnostic rows. These rows retain numbers and input addresses only. */
#define SA2_D3D9_CTAB_ROWS 256u
typedef struct {
    uint32_t seq;                   /* 1-based creation index */
    uint32_t caller;                /* the guest address that called d3dx9 */
    uint32_t bytecode;              /* pFunction, as the guest passed it */
    uint32_t table;                 /* the table's guest address */
    uint32_t version;               /* the CTAB header's Version (the shader's token) */
    uint32_t constants;             /* the CTAB header's Constants */
    uint32_t ctab_bytes;            /* the comment's data bytes after its fourcc */
    uint32_t refs;                  /* the table's count as it stands */
    uint32_t released_by;           /* the caller of its final Release; 0 while alive */
    uint32_t released_seq;          /* that Release's method sequence number; 0 while alive */
} sa2_d3d9_ctab;
uint32_t             sa2_d3d9_ctab_count(void);
const sa2_d3d9_ctab *sa2_d3d9_ctab_at(uint32_t i);
uint32_t             sa2_d3d9_ctab_rows_dropped(void);   /* tables past the rows kept */

void sa2_d3d9_stats_get(sa2_d3d9_stats *out);
uint32_t             sa2_d3d9_log_count(void);
const sa2_d3d9_call *sa2_d3d9_log_at(uint32_t i);

/* The dynamic surface, for a probe that writes it out. */
uint32_t    sa2_d3d9_type_count(void);
const char *sa2_d3d9_type_name(uint32_t type);
uint32_t    sa2_d3d9_type_slots(uint32_t type);           /* named slots in the vtable */
const char *sa2_d3d9_method_name(uint32_t type, uint32_t slot);   /* NULL when unnamed */
int         sa2_d3d9_method_argc(uint32_t type, uint32_t slot);   /* -1 when unnamed */
/* 1 when this runtime answers that slot without refusing (the M7 numerator). */
int         sa2_d3d9_method_implemented(uint32_t type, uint32_t slot);
/* "out" (writes through a pointer the guest dereferences), "cap" (an answer
 * this runtime fabricates), "void" (the return value means nothing), in a
 * comma-separated string; "" when none. */
const char *sa2_d3d9_method_flags(uint32_t type, uint32_t slot);
const sa2_d3d9_slot_stat *sa2_d3d9_slot_stat_at(uint32_t type, uint32_t slot);

/* ---------------------------------------------------- the closed world
 *
 * Rule 4 above; runtime/sa2_d3d9.c's comment on the closed world. The mode is set by
 * sa2_d3d9_config_from_env: trap unless the keyboard source is live and the session
 * is not an automated one, open only in a unit build. Every call of a method off the
 * list is counted in every mode, in one row per (interface, slot) in the order each
 * was first called; in trap mode the first is also the last. `sa2_d3d9_method_implemented` still says whether a row has
 * a handler; `sa2_d3d9_method_listed` says whether the closed world lets it answer. */
enum { SA2_D3D9_WORLD_TRAP = 0, SA2_D3D9_WORLD_LOG = 1, SA2_D3D9_WORLD_OPEN = 2 };
enum {                                /* what became of an unlisted method's first call */
    SA2_D3D9_UNL_TRAPPED  = 0,        /* trap mode: trapped by the closed world */
    SA2_D3D9_UNL_ANSWERED = 1,        /* log or open mode: its handler answered */
    SA2_D3D9_UNL_REFUSED  = 2,        /* log or open mode: it has no handler and refused */
    SA2_D3D9_UNL_UNNAMED  = 3,        /* a slot the table does not name: refused in every mode */
};
#define SA2_D3D9_UNLISTED_MAX (SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS)
typedef struct {
    uint16_t type, slot;
    uint32_t calls;
    uint32_t first_seq;               /* the device's method sequence number */
    uint32_t first_caller;            /* the guest return address of the first call */
    uint32_t first_self;              /* its `this` */
    uint8_t  outcome;                 /* SA2_D3D9_UNL_* */
} sa2_d3d9_unlisted;
int         sa2_d3d9_world_mode(void);              /* SA2_D3D9_WORLD_* */
const char *sa2_d3d9_world_mode_name(void);         /* "trap", "log" or "open" */
int         sa2_d3d9_method_listed(uint32_t type, uint32_t slot);
int         sa2_d3d9_world_live_source(void);       /* the keyboard source is the live FIFO */
int         sa2_d3d9_world_automated(void);         /* an automated session (trap even so) */
uint32_t    sa2_d3d9_listed_count(void);            /* device methods on the list */
const char *sa2_d3d9_list_digest(void);             /* the compiled rows' sha256, checked
                                                       against SA2_D3D9_M7_LIST_DIGEST */
uint32_t                 sa2_d3d9_unlisted_count(void);   /* distinct methods */
uint32_t                 sa2_d3d9_unlisted_calls(void);   /* every call of them */
const sa2_d3d9_unlisted *sa2_d3d9_unlisted_at(uint32_t i);
const char              *sa2_d3d9_unlisted_outcome_name(uint32_t outcome);
/* Writes `"closed_world": {...}` (no surrounding comma) for the run record's d3d9
 * block: the mode, whether the keyboard source is live and whether the session is an
 * automated one, the list's path, digest and size, and every unlisted row with its interface, method (null for an unnamed slot),
 * slot, calls, first call's sequence number, caller and `this`, and outcome. */
void        sa2_d3d9_closed_world_json(FILE *f);

/* The D3DCAPS9 fields this runtime wrote, one at a time, so what was invented
 * is a list and not an assumption. Valid after the first GetDeviceCaps. */
uint32_t sa2_d3d9_caps_field_count(void);
int sa2_d3d9_caps_field_at(uint32_t i, uint32_t *off, const char **name, uint32_t *value,
                           int *from_guest);

#ifdef __cplusplus
}
#endif
#endif
