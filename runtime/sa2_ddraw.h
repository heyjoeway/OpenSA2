/* A recording DirectDraw, answered from the capture's device profile.
 *
 * Design 3.5 puts DirectDraw in group (b), presentation, and design 5 S4's
 * work list asks for "presentation stubs as no-ops that record calls". The
 * user ruled on 2026-09-23 that a refusal counts as implemented only if it
 * reproduces the native outcome
 * (Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md), and the
 * refusal this file replaces did not: natively ddraw.dll loads and the game's
 * display query runs to the end.
 *
 * What the image asks of DirectDraw, all of it
 * --------------------------------------------
 * One routine, 0x00865D20, called once from the device-creation method
 * 0x00865E70 at 0x00866007, once IDirect3D9::CreateDevice has succeeded
 * (0x00865FF0) and the capability read at 0x00866001 has run. It is the
 * only reference to the strings "ddraw.dll", "DirectDrawEnumerateExA" and
 * "DirectDrawCreate" and to IID_IDirectDraw7 (0x0087354C), so it is the whole
 * DirectDraw surface of the executable (tools/s4/ddraw_surface.py re-derives
 * every fact below from the image):
 *
 *   00865d39  IDirect3D9::GetAdapterMonitor(0)       -> the display's HMONITOR
 *   00865d4a  LoadLibraryA("ddraw.dll")              ; je 00865E24 when absent
 *   00865d96  DirectDrawEnumerateExA(0x00865C20, &ctx, DDENUM_ATTACHEDSECONDARYDEVICES)
 *   00865db8  DirectDrawCreate(&ctx.guid, &dd, NULL)  ; the result is not tested
 *   00865dcd  dd->QueryInterface(IID_IDirectDraw7, &dd7)
 *   00865e03  dd7->GetAvailableVidMem({VIDEOMEMORY|LOCALVIDMEM}, &total, NULL)
 *   00865e1b  dd7->Release()                          ; dd itself is never released
 *   00865e2f  this->[0x48] = total >> 20              ; video memory in MiB
 *
 * The context is 24 bytes on the stack: a GUID at +0, zeroed at 0x00865D70..
 * 0x00865D7C, the HMONITOR GetAdapterMonitor returned at +0x10 (0x00865D65),
 * and a found flag at +0x14 (zeroed at 0x00865D80) that nothing reads. Its
 * address is what 0x00865D8C..0x00865D90 pushes. The callback 0x00865C20 is
 * stdcall with five arguments (`ret 14h` at 0x00865C50); it reads the context
 * at once (0x00865C24), compares its hm argument with ctx+0x10 (0x00865C27)
 * and on a match sets the flag and copies the 16 bytes *lpGUID points at into
 * ctx (0x00865C2D..0x00865C48), over whatever an earlier match copied. It
 * returns TRUE on both paths (0x00865C4B). So the game asks DirectDraw one
 * question -- the local video memory of the display Direct3D is on -- and
 * finds that display by comparing HMONITORs; with two matches the last wins.
 *
 * What native answers (the two traced boots)
 * ------------------------------------------
 * private/generated/oracle/captures/native-outcomes-v1a/native-run.log, thread
 * 0024 (v1b's lines 301..332 and 39175 are the same sequence):
 *
 *   13825  ddraw.dll loads
 *   13826  DirectDrawEnumerateExA callback 00865C20, context 0287F8A8, flags 0x1
 *   13916  the default interface, "DirectDraw HAL"; the line names no GUID and
 *          no monitor for it, and the profile's record of it does
 *          (`ddraw_device ... guid=null monitor=null`, what Wine's
 *          DirectDrawEnumerateExA hands its callback for the primary display
 *          driver, and what the profile probe's own callback records)
 *   13917  one secondary device, whose GUID is host inventory and so only in
 *          the capture's profile
 *   13919  DirectDrawCreate on the secondary's GUID, ddraw 0287F890, outer 0
 *   13940  QueryInterface {15e65ec0-...} (IID_IDirectDraw7), object 0287F894
 *   13944  GetAvailableVidMem caps 0287F898, total 0287F88C, free 00000000
 *   13948  the total video memory
 *   13949  IDirectDraw7 released to 0
 *   29007  at exit the object's counts are {0, 0, 0, 0, 1}: IDirectDraw7's is 0
 *          and IDirectDraw's is still 1
 *
 * Lines 13920..13939 are inside Wine's own DirectDrawCreate: DDRAW_Create
 * queries the new object for IID_IDirectDraw {6c14db80-...} and calls
 * Initialize with the GUID. The game's first call through the object is 13940.
 * The trace's five stack addresses sit at the offsets the decode gives from
 * one frame base (0x0287F8C0): ctx -0x18, dd -0x30, dd7 -0x2C, caps -0x28,
 * total -0x34.
 *
 * The secondary's GUID reaching DirectDrawCreate is the evidence that the
 * HMONITORs matched natively: the context's GUID starts zeroed and only a
 * match at 0x00865C2B copies one in.
 *
 * Where each answer comes from
 * ----------------------------
 * Every answer is the capture's device profile (runtime/sa2_devprofile.h),
 * which harness/replay/native_device_profile.c records natively by asking the
 * same questions of the same host. Nothing about any host is in this file.
 *
 *  - **The device list**: one callback per sa2_dp_ddraw_device entry, in the
 *    recorded order, lpGUID NULL for an entry with no GUID and the entry's 16
 *    bytes otherwise, and the description and name byte for byte: the probe
 *    records the ANSI bytes its own A callback was handed.
 *  - **The HMONITOR**: the profile records which Direct3D adapter's monitor
 *    each device is on (sa2_dp_ddraw_monitor_adapter), and the value handed
 *    over is the token the d3d9 module's GetAdapterMonitor gives the game for
 *    that adapter (sa2_d3d9_adapter_monitor), because the game finds its
 *    display by comparing the two at 0x00865C27. An entry the profile records
 *    on no monitor (`monitor=null`) gets NULL. The token is never compared with
 *    native's value, which is a host handle; its equality with d3d9's answer
 *    is what the game tests and what is reproduced. The profile's readers
 *    accept only adapter 0's monitor or none, since Direct3D reports one
 *    adapter natively and in runtime/sa2_d3d9.c.
 *  - **The device DirectDrawCreate makes**: only native's. The profile probe
 *    hands its own DirectDrawCreate the bytes the game's callback keeps -- the
 *    GUID of the last entry on adapter 0's monitor, or sixteen zeros when none
 *    is -- and measures video memory on that object; the profile's readers
 *    hold the `ddraw_create` record to the same rule. Any other bytes are a
 *    device whose video memory nobody measured, so DirectDrawCreate refuses
 *    them by name rather than hand the game another device's figure. Sixteen
 *    zeros name the primary display driver, the entry with no GUID.
 *  - **The video memory**: GetAvailableVidMem answers the HRESULT and total
 *    the profile recorded (sa2_dp_ddraw_vidmem_hr and _total). That they are
 *    native's is a prediction, not a measurement: the profile probe asks at
 *    the Mod Loader's Init, after the game's own call (0x00865E03,
 *    native-outcomes-v1a:13944), and anything that changes the answer between
 *    the two moments -- a display-mode change, say -- separates them. The
 *    first capture taken with --device-profile and a +ddraw trace falsifies it
 *    if the profile's `ddraw_vidmem total` is not that trace's "Total video
 *    memory" figure.
 *  - **DirectDrawEnumerateExA, DirectDrawCreate and QueryInterface succeed**,
 *    which the profile's reader requires of what it accepts, because they did
 *    natively.
 *
 * With SA2_DEVICE_PROFILE unset the profile is the built-in default: one
 * device, the primary display driver on no monitor, so the game's callback
 * matches nothing and DirectDrawCreate is asked for the zeroed context, which
 * names that device; the video memory is the default's named placeholder.
 *
 * Arguments the image never passes -- another flag word, an outer unknown,
 * another IID, another capability mask, a free-memory pointer -- refuse by
 * name: reaching one means the decode above is wrong, and the run should stop
 * where it is wrong rather than walk on under an answer nobody checked. The
 * addresses it passes -- the context, the out-pointers, the callback -- are
 * refused only when NULL: any other address names the same answer, and the
 * callback, whichever lifted stdcall function it is, is handed the same five
 * arguments and held by sa2_guest_stdcall to popping exactly those.
 *
 * The objects
 * -----------
 * DirectDrawCreate hands back an IDirectDraw; QueryInterface hands back a
 * separate IDirectDraw7 of the same instance, and each interface keeps its own
 * reference count, as ddraw does (13943 and 13949 are IDirectDraw7's, 29007
 * reports both). Every slot of both vtables is named with its argument count.
 * Only the three slots the image reaches have handlers -- IDirectDraw's
 * QueryInterface, IDirectDraw7's GetAvailableVidMem and Release -- and every
 * other slot refuses by name, so reaching one is a work item with an address
 * on it. The argument counts of those three are checked against the dwords
 * the image pushes by `tools/s4/ddraw_surface.py --argc`.
 *
 * Environment
 * -----------
 *   SA2_DDRAW_DEVICE=1       ddraw.dll is an admitted module and its two
 *                            entry points answer from this file. Unset, with
 *                            the default profile: LoadLibraryA("ddraw.dll")
 *                            answers 0, which is not what native gets, and is
 *                            kept as the before measurement. Unset with a
 *                            capture's profile loaded: refused at
 *                            configuration, naming the profile, because that
 *                            absence would stand in for the capture's answers.
 *                            sa2_ddraw_config_from_env reads the profile, so it
 *                            runs after sa2_devprofile_config_from_env.
 *   SA2_DEVICE_PROFILE=path  the capture's device profile, which every answer
 *                            comes from (runtime/sa2_devprofile.h); unset, the
 *                            built-in default
 *   SA2_DDRAW_BREAK=<name>   argc | noout | nomatch; runtime/sa2_ddraw.c says
 *                            what each breaks, and harness/ddraw_probe.c shows
 *                            each failing the checks it targets
 */
#ifndef SA2_RUNTIME_DDRAW_H
#define SA2_RUNTIME_DDRAW_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Synthetic method thunks, above the dsound thunks (which end at 0x00221000)
 * and inside the 0x00200000..0x00300000 block this runtime keeps for entry
 * points it invents. Two interfaces of 32 slots: IDirectDraw7 has 30. */
#define SA2_DD_THUNK_BASE   0x00233000u
#define SA2_DD_THUNK_STRIDE 16u
#define SA2_DD_MAX_TYPES    2u
#define SA2_DD_MAX_SLOTS    32u
#define SA2_DD_THUNK_END    (SA2_DD_THUNK_BASE + \
                             SA2_DD_MAX_TYPES * SA2_DD_MAX_SLOTS * SA2_DD_THUNK_STRIDE)
#define SA2_DD_THUNK_LIMIT  0x00233800u   /* the end of the block reserved for these */

/* Fabricated interfaces, four words each (vtable, tag, type, instance): the
 * image reads only the first (0x00865DBE, 0x00865DFC, 0x00865E15). Two per
 * DirectDrawCreate, and the image makes one per device creation. */
#define SA2_DD_OBJ_BASE     0x03274000u
#define SA2_DD_OBJ_STRIDE   16u
#define SA2_DD_MAX_OBJECTS  32u

/* The records one enumeration hands its callback, one per profile entry:
 * the GUID at +0, the description at +0x10 and the name at +0x114, each
 * string SA2_DD_STR_BYTES long like the profile's own fields. They sit after
 * the interfaces in the same 4 KiB block, which holds six at that length. The
 * profile's readers refuse more than SA2_DP_MAX_DDRAW devices at
 * configuration, and runtime/sa2_ddraw.c holds that limit to this capacity
 * with a static assertion, so a profile this cannot hold never reaches an
 * enumeration. Native enumerated two on the recording host
 * (native-outcomes-v1a:13916, 13917). */
#define SA2_DD_ENUM_BASE    0x03274200u
#define SA2_DD_ENUM_STRIDE  0x220u
#define SA2_DD_MAX_DEVICES  6u
#define SA2_DD_STR_BYTES    260u
#define SA2_DD_OBJ_LIMIT    0x03275000u   /* the end of the block reserved for both */

#define SA2_DD_VT_BASE      0x03283000u
#define SA2_DD_VT_STRIDE    (SA2_DD_MAX_SLOTS * 4u)
#define SA2_DD_VT_LIMIT     0x03283400u

/* HRESULTs this file returns, by their published values. */
#define SA2_DD_OK           0x00000000u

/* How an answer was arrived at. The distinction is the point of the log. */
enum {
    SA2_DD_ANS_REFUSED = 0,   /* recorded, then trapped by name */
    SA2_DD_ANS_NOOP    = 1,   /* recorded; nothing to answer (a break only) */
    SA2_DD_ANS_GUEST   = 2,   /* from something the guest did: a reference count */
    SA2_DD_ANS_PROFILE = 3,   /* from the capture's device profile */
    SA2_DD_ANS_OBJECT  = 4,   /* by fabricating an interface */
};

#define SA2_DD_LOG_MAX 256u

typedef struct {
    uint32_t seq;               /* shared with the entry-point records below */
    uint32_t caller;
    uint32_t self;
    uint16_t type;
    uint16_t slot;
    uint8_t  answered;
    uint8_t  argc;
    uint32_t arg[4];
    uint32_t ret;
    /* The dword written through an out-pointer, 0 when none. For an answer
     * from the profile (SA2_DD_ANS_PROFILE: GetAvailableVidMem's total) it is
     * a host measurement under a capture's profile, so a record built from the
     * log says whether it is the profile's, never what it is. */
    uint32_t out0;
} sa2_dd_call;

/* One row per (interface, slot) ever called, exact past the log's end. */
typedef struct {
    uint32_t calls;
    uint32_t first_seq;
    uint32_t first_caller;
    uint8_t  answered;
    uint8_t  used;
} sa2_dd_slot_stat;

/* One DirectDrawEnumerateExA call. The per-callback columns are what each
 * callback was handed and what it returned, so a reader can see the match the
 * game made (or did not make) without re-running it. */
#define SA2_DD_MAX_ENUMS 8u
typedef struct {
    uint32_t seq, caller;
    uint32_t callback, context, flags;
    uint32_t offered;           /* profile entries there were to hand over */
    uint32_t callbacks;         /* callbacks made */
    uint32_t stopped_by;        /* 1-based callback that returned FALSE, 0 when none did */
    uint32_t hr;
    uint32_t guid_ptr[SA2_DD_MAX_DEVICES];
    uint32_t hmonitor[SA2_DD_MAX_DEVICES];
    uint32_t ret[SA2_DD_MAX_DEVICES];
} sa2_dd_enum;

/* One DirectDrawCreate, and the interfaces it grew. Kept after the counts reach
 * zero, so a run that ends says what existed and what leaked. */
#define SA2_DD_MAX_INSTANCES (SA2_DD_MAX_OBJECTS / 2u)
typedef struct {
    uint32_t seq, caller;
    uint32_t guid_ptr;          /* lpGUID as the guest passed it */
    uint8_t  guid[16];          /* the bytes it pointed at */
    /* The profile entry those bytes name, which is always native's choice,
     * since DirectDrawCreate refuses any other; -1 when native's bytes were
     * the zeroed context and the profile lists no primary display driver for
     * them to name. */
    int32_t  device;
    uint32_t dd, dd7;           /* IDirectDraw and IDirectDraw7, 0 until made */
    uint32_t ref1, ref7;        /* each interface's own count, as ddraw keeps them */
    uint32_t vidmem_answers;
    /* The last GetAvailableVidMem total written, 0 when none was. A host
     * measurement under a capture's profile: kept for a comparison with the
     * profile, never written into a record. */
    uint32_t last_total;
} sa2_dd_instance;

typedef struct {
    uint32_t enabled;
    uint32_t enumerations, callbacks, creates;
    uint32_t method_calls, distinct_methods, objects_built;
    /* Method calls whose `this` is not an interface this file made. Each one
     * also traps, because the image calls only through pointers this file
     * handed out, and a foreign `this` is a drifted frame. */
    uint32_t unknown_this;
    /* Every call answered, entry points included, by how it was answered. */
    uint32_t refused, noop, from_guest, from_profile, objects;
    uint32_t log_dropped;       /* method calls past SA2_DD_LOG_MAX; counts stay exact */
    uint32_t enums_dropped;     /* enumerations past SA2_DD_MAX_ENUMS */
} sa2_dd_stats;

/* Reads SA2_DDRAW_DEVICE and SA2_DDRAW_BREAK, and refuses a capture's profile
 * with the device off (see Environment above). Idempotent. */
void sa2_ddraw_config_from_env(void);
int  sa2_ddraw_device_enabled(void);

/* ddraw.dll's two entry points, each taking the import's three arguments in
 * order, so the adapters in runtime/sa2_imports.c are one line and the
 * argument count lives only in their g_dyn_table rows.
 *
 *   DirectDrawEnumerateExA(lpCallback, lpContext, dwFlags) -- calls back into
 *                          the guest, so it needs the live CPU state
 *   DirectDrawCreate(lpGUID, lplpDD, pUnkOuter)
 *
 * Both trap by name when SA2_DDRAW_DEVICE is not set: GetProcAddress cannot
 * reach them then, because LoadLibraryA("ddraw.dll") answers 0. */
uint32_t sa2_ddraw_enumerate_ex_a(void *s, const uint32_t *a, uint32_t caller);
uint32_t sa2_ddraw_create(const uint32_t *a, uint32_t caller);

void sa2_ddraw_stats_get(sa2_dd_stats *out);
const char *sa2_ddraw_break_name(void);             /* "" when no break is set */
uint32_t           sa2_ddraw_log_count(void);
const sa2_dd_call *sa2_ddraw_log_at(uint32_t i);
uint32_t           sa2_ddraw_enum_count(void);
const sa2_dd_enum *sa2_ddraw_enum_at(uint32_t i);
uint32_t               sa2_ddraw_instance_count(void);
const sa2_dd_instance *sa2_ddraw_instance_at(uint32_t i);

uint32_t    sa2_ddraw_type_count(void);
const char *sa2_ddraw_type_name(uint32_t type);
const char *sa2_ddraw_method_name(uint32_t type, uint32_t slot);   /* NULL when unnamed */
int         sa2_ddraw_method_argc(uint32_t type, uint32_t slot);   /* -1 when unnamed */
int         sa2_ddraw_method_implemented(uint32_t type, uint32_t slot);
const char *sa2_ddraw_method_flags(uint32_t type, uint32_t slot);
const sa2_dd_slot_stat *sa2_ddraw_slot_stat_at(uint32_t type, uint32_t slot);

#ifdef __cplusplus
}
#endif
#endif
