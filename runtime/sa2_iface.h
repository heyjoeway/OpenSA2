/* Fabricated COM-shaped interfaces: Steam's, and the survey of d3d9's.
 *
 * Why an object and not a handle
 * ------------------------------
 * Two places in this image return something the guest immediately
 * dereferences twice:
 *
 *     0040a63f  call SteamApps ; mov edx,[eax] ; mov eax,[edx] ; call eax
 *     00868599  call Direct3DCreate9 ; mov ecx,[eax] ; mov eax,[ecx+20h] ; call eax
 *
 * Neither checks for null first. A handle token would fault on the first load
 * and a null would fault on the second, so an interface has to be real bytes
 * in guest memory: an object word pointing at a vtable, and a vtable of
 * function pointers. The pointers are synthetic thunk addresses with one
 * function-table native behind them, which is the mechanism `sa2_imports.c`
 * already uses for the IAT.
 *
 * COM methods here are __stdcall with `this` as the first stack argument;
 * Steam's are __thiscall with `this` in ECX. The difference is one field in
 * the table, because getting it wrong corrupts the caller's frame rather than
 * producing a wrong answer, and a corrupted frame is the failure this project
 * is worst at diagnosing.
 *
 * Two policies, and the difference matters
 * ----------------------------------------
 *  * **REFUSE** (Steam). A slot with no implementation records the call and
 *    raises a trap naming the interface, the slot, the caller and the
 *    arguments. An unimplemented method is then a work item with an address on
 *    it, never a zero the guest acts on.
 *
 *  * **SURVEY** (d3d9, and off unless asked for). A slot with no
 *    implementation records the call, **returns 0**, and lets the guest walk
 *    on -- for a bounded number of calls, after which it refuses. This is not
 *    an implementation and is never on by default: it is how the *ordered*
 *    list of methods the game asks a device for gets collected, which is S5's
 *    denominator (design 5, M7) and cannot be recovered statically because
 *    every one of them is a vtable call. Every row the survey answered is
 *    marked `answered: "survey-placeholder"` in the log, so no reader can
 *    mistake a surveyed call for an implemented one.
 *
 * The survey ends when the guest uses something the survey did not give it --
 * typically an out-pointer a real method would have filled. That fault is the
 * end of one survey run, not a defect, and the log up to it is the result.
 */
#ifndef SA2_RUNTIME_IFACE_H
#define SA2_RUNTIME_IFACE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The fabricated objects live in guest scratch (0x03000000..0x03D00000), well
 * clear of the heap, the stack and the page `harness/crt_probe.c` uses. */
#define SA2_IFACE_BASE         0x03100000u
#define SA2_IFACE_STRIDE       0x400u
#define SA2_IFACE_VTABLE_OFF   0x10u
#define SA2_IFACE_SLOTS        64u
#define SA2_IFACE_MAX          16u
/* Method thunks, immediately above the IAT thunk region SA2_GUEST_IMPORT_END
 * (0x00202000), so every synthetic entry point this runtime invents sits in
 * one neighbourhood and a stray value is identifiable by its address alone. */
#define SA2_IFACE_THUNK_BASE   0x00202000u
#define SA2_IFACE_THUNK_STRIDE 16u
#define SA2_IFACE_THUNK_END    (SA2_IFACE_THUNK_BASE + \
                                SA2_IFACE_MAX * SA2_IFACE_SLOTS * SA2_IFACE_THUNK_STRIDE)

/* The default Steam ID: universe 1 (public), type 1 (individual), instance 1,
 * account 1 -- 76561197960265729. A placeholder with the right shape; not a
 * real account, and never the user's. `SA2_STEAM_ID` moves it. */
#define SA2_STEAM_ID_DEFAULT 0x0110000100000001ull

#define SA2_IFACE_LOG_MAX 256u

/* How a Steam method was answered. The distinction this slice exists to keep
 * is between codes 1 and 3: "the call did nothing and reported success" is
 * design 3.5(c)'s no-op stub, and "there is no such stat" is an answer too,
 * but neither of them is a *value*. Code 6 is the one place this runtime does
 * invent one, and it is labelled so a reader cannot mistake it. */
enum {
    SA2_IFACE_ANS_REFUSED  = 0,  /* recorded, then trapped by name */
    SA2_IFACE_ANS_IMPL     = 1,  /* the no-op that returns success */
    SA2_IFACE_ANS_SURVEY   = 2,  /* survey placeholder; never a shipping answer */
    SA2_IFACE_ANS_DECLINED = 3,  /* "no such stat/achievement": false, out-params untouched */
    SA2_IFACE_ANS_RECORDED = 4,  /* a Set: recorded here, stored nowhere, success */
    SA2_IFACE_ANS_NONE     = 5,  /* "this runtime knows of none": a count of zero */
    SA2_IFACE_ANS_GUEST    = 6,  /* a value the guest itself supplied, handed back */
    SA2_IFACE_ANS_INVENT   = 7,  /* a value this runtime made up -- labelled, not hidden */
};

typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t self;
    uint16_t slot;
    uint8_t  answered;        /* SA2_IFACE_ANS_* */
    uint8_t  argc;
    uint32_t arg[6];
    uint32_t ret;
    char     iface[24];
    char     name[48];        /* the method's name, or "vtable[n]" */
} sa2_iface_call;

/* One row per (interface, slot) the guest ever called, with a count that does
 * not degrade when the 256-entry call log fills. Slice 8's third check defect
 * was a diagnostic that silently stopped recording past a limit and then
 * reported the absence as a fact; this is the shape that does not. */
#define SA2_IFACE_TALLY_MAX 96u
typedef struct {
    char     iface[24];
    char     method[48];
    uint32_t slot;
    uint32_t first_seq;
    uint32_t calls;
    uint32_t first_caller, last_caller;
    uint8_t  answered;
    int8_t   argc;
} sa2_iface_slot_stat;

/* Every distinct stat or achievement name the guest asked about, read out of
 * the guest's own .rdata through its own pointer. These strings are the
 * guest's; this runtime never supplies one. */
#define SA2_STEAM_NAMES_MAX 48u
typedef struct {
    char     name[64];
    uint32_t asks;          /* queries answered "there is no such thing" */
    uint32_t sets;          /* writes accepted and discarded */
    uint32_t first_caller;
} sa2_steam_name;

typedef struct {
    uint32_t init_calls, shutdown_calls, runcallbacks_calls;
    uint32_t register_callback, unregister_callback;
    uint32_t register_callresult, unregister_callresult;
    uint32_t interfaces_built, method_calls, methods_implemented;
    uint32_t methods_refused, methods_surveyed;
    uint32_t methods_declined, methods_recorded, methods_invented;
    /* Out-parameters a declined query deliberately did not write. The guest
     * supplied the buffer; this runtime had nothing true to put in it. */
    uint32_t out_params_unwritten;
    /* Callbacks the guest is owed and this runtime will never dispatch,
     * because dispatching one means inventing its payload: every
     * SteamAPI_RegisterCallback/RegisterCallResult, plus every
     * ISteamUserStats::RequestCurrentStats that promised a UserStatsReceived_t.
     * A guest that waits on one of these waits forever, so the number is the
     * size of the debt this decision takes on, stated rather than hidden. */
    uint32_t callbacks_never_delivered;
    uint32_t steam_id_lo, steam_id_hi;
    uint32_t d3d9_creates, d3d9_survey_depth;
    /* SteamMetricsSDK::MetricsAgent. Four entry points, eight static call
     * sites, and nothing the guest can learn from any of them. */
    uint32_t metrics_getinstance, metrics_initialize, metrics_logevent,
             metrics_cleaninstance;
    /* XInput. `pad_absent` counts the calls answered ERROR_DEVICE_NOT_CONNECTED
     * and `pad_present` the calls answered with the neutral pad that
     * SA2_XINPUT_PAD=1 fabricates; exactly one of them is non-zero in any run,
     * which is how a receipt says which policy the run was taken under. */
    uint32_t xinput_getstate, xinput_setstate, xinput_getcaps;
    uint32_t pad_absent, pad_present;
    /* Calls answered from a pad the live input source holds connected
     * (runtime/sa2_dinput.h, "The host gamepads"); 0 in every replay, and not
     * counting the pads at rest "Pads that connect later" stands in with. A
     * live session's run record writes it, with the source's gamepad
     * counters, in its "xinput" block, whose policy is then "live-pads"
     * (harness/crt_probe.c). */
    uint32_t pad_live;
} sa2_iface_stats;

void sa2_iface_config_from_env(void);
/* The guest pointer for one interface by name, building it on first use. */
uint32_t sa2_iface_get(const char *name);

void sa2_iface_stats_get(sa2_iface_stats *out);
uint32_t              sa2_iface_log_count(void);
const sa2_iface_call *sa2_iface_log_at(uint32_t i);

uint32_t                   sa2_iface_slot_count(void);
const sa2_iface_slot_stat *sa2_iface_slot_at(uint32_t i);
uint32_t              sa2_steam_name_count(void);
const sa2_steam_name *sa2_steam_name_at(uint32_t i);

/* The declared stack-argument count for one named interface slot, or -1 when
 * this runtime does not name that slot. `tools/s4/steam_surface.py --argc`
 * compares it against the pushes the image actually makes at each call site:
 * a __thiscall COM method pops its own arguments, so a count that is one dword
 * short leaves the caller's frame drifting and every later [esp+n] in that
 * function reads the wrong local. Three of the d3d9 counts were wrong that way
 * for six slices and nothing noticed. */
int sa2_iface_declared_argc(const char *iface, uint32_t slot);
const char *sa2_iface_declared_name(const char *iface, uint32_t slot);

/* The flat `steam_api` entry points, reached through the IAT. */
uint32_t sa2_steam_api(const char *name, void *state, const uint32_t *argv, uint32_t caller);

/* SteamMetricsSDK!MetricsAgent, by decorated name.
 *
 * Four imports, all of them telemetry: `GetInstance`, `Initialize(const char*,
 * int, const char*)`, `LogEvent(const char*, const char*, const char*)` and
 * `CleanInstance`. Design 3.5 puts `SteamMetricsSDK` in the Steam class and
 * says that class is "no-op stubs returning success", and the user's direction
 * of 2026-09-21 was that Steam is not to be implemented at all: no-op the
 * surface so the guest walks past it.
 *
 * Two of the four are `QAE` in their decoration -- public non-virtual
 * `__thiscall`. That needs no new calling convention: a thiscall callee pops
 * its *stack* arguments exactly as stdcall does, and differs only in taking
 * `this` in ECX, which these stubs never read. So the rows are
 * SA2_IMP_STDCALL with argc counting the stack arguments alone, and getting
 * that count right is the whole of the ABI here.
 *
 * `GetInstance` must return something the guest can put in ECX and call
 * through. It returns a token **above `SA2_GUEST_END`**, so a guest that
 * *dereferences* the agent -- reading a field rather than calling a method --
 * faults with the token in the trap instead of silently reading whatever lies
 * at a fabricated guest address. Nothing in the four is virtual, so no vtable
 * is owed.
 *
 * That sentence was true of the intent and false of the code until review
 * caught it: the token was 0x03180000, inside the guest scratch region, where
 * `sa2_gp` accepts it and a dereference reads zeroes instead of faulting. A
 * comment asserting a safety property the address does not have is worse than
 * no comment, because it stops the next reader checking. */
uint32_t sa2_steam_metrics(const char *decorated, const uint32_t *argv, uint32_t caller);
uint32_t sa2_steam_metrics_agent(void);

/* XINPUT1_3 ordinals 2, 3 and 4: XInputGetState, XInputSetState and
 * XInputGetCapabilities. The image imports them by ordinal, so the names here
 * come from the public export ordinals of that DLL and not from the input.
 *
 * **Each slot the image asks about answers what the capture's device profile
 * recorded for it** (runtime/sa2_devprofile.h, sa2_dp_xinput_caps_hr):
 * ERROR_DEVICE_NOT_CONNECTED (1167) for slots 0 to 3 in every profile a reader
 * accepts; with no capture, the default profile answers 1167 too. 1167 is the
 * native outcome on the recording host: the game thread called
 * XInputGetCapabilities 40 times in the traced boot, ten per slot, and
 * XInputGetState and XInputSetState never
 * (agents-journals/s4/2026-09-23-native-outcomes.md). A profile that records
 * an attached pad is refused when it is read, because a pad's input is part
 * of no recording. A slot past 3 is refused by name rather than answered:
 * both of the image's scans count the slot from 0 to 3 (0x00864EEB,
 * 0x00864A70), so such a call means the decode was wrong.
 *
 * The game builds a pad object only when XInputGetCapabilities answers 0 (the
 * test after the start-up scan's call and after the rescan's), and calls
 * XInputGetState and XInputSetState only through that object's methods, so
 * under every accepted profile neither is reached. Input in S4 comes through
 * neither XInput nor DirectInput's device state: the replays write
 * ControllersRaw after UpdateControllers, natively in
 * harness/replay/native_input_probe.c and lifted in
 * harness/replay/wrap_input_replay.c.
 *
 * `SA2_XINPUT_PAD=1` fabricates a connected pad with every control at rest.
 * It is the falsifier, and only for runs on the default profile: set beside a
 * capture's profile it is refused when the run is configured, because that
 * capture had no such pad. With the recording DirectInput device on
 * (SA2_DINPUT_DEVICE=1) the scan runs, and a pad answer must move the lifted
 * run -- a pad object built and XInputGetState called on every poll. A run it
 * moves nothing in has not reached the scan.
 *
 * **A live session's pads.** With a FIFO input source (SA2_INPUT_DI_SOURCE),
 * a slot the source's latest gamepad record holds connected answers before
 * any of the above (runtime/sa2_dinput.h, "The host gamepads"): 0 for all
 * three; XInputGetCapabilities a wired Xbox 360 pad's capabilities (type and
 * subtype gamepad, flags XINPUT_CAPS_VOICE_SUPPORTED, buttons 0xF3FF, 8-bit
 * triggers, 10-bit sticks, both motors); XInputGetState the record's state
 * with the slot's packet number; XInputSetState nothing, the vibration
 * dropped. Counted in `pad_live`. Every other slot answers as above, so a run
 * whose source sent no gamepad record, and every replay, answers as before.
 * The game makes its pad objects at its start-up scan and nowhere else, which
 * is why the source's first XInput call waits for the sender's pads, and why,
 * for a pad that connects later, that scan, each new object's first poll and
 * the rescan answer for a slot apart from the records (runtime/sa2_dinput.h,
 * "Pads that connect later"; its pads at rest are not counted in `pad_live`).
 * And a player reads its
 * pad only when its InputWay says so, which the game sets from the pads when the
 * guest's command line is "-r" (SA2_GUEST_CMDLINE=-r; runtime/sa2_dinput.h, "The
 * host gamepads").
 *
 * Out-parameters are zeroed on the not-connected path. Native XInput returns
 * before writing them; the game reads the buffer only after a 0 answer, so the
 * zeroes are never read, and a caller that reads a buffer this runtime never
 * wrote would read its own stack. */
uint32_t sa2_xinput(uint32_t ordinal, const uint32_t *argv, uint32_t caller,
                    uint32_t *out_bytes, uint32_t *out_len);
int      sa2_xinput_pad_present(void);

/* d3d9.dll!Direct3DCreate9(SDKVersion). Refuses by name unless
 * SA2_D3D9_SURVEY is set, in which case it returns the survey object. */
uint32_t sa2_d3d9_create(void *state, uint32_t sdk_version, uint32_t caller);
int      sa2_d3d9_survey_enabled(void);
/* SA2_STEAM_SURVEY: the Steam interfaces answer 0 to any slot instead of
 * refusing by name. -1 when off, which is the default and the only setting a
 * shipping verdict is taken under. It exists so that determinism can be
 * measured over a run long enough to be a measurement. */
int      sa2_iface_steam_survey(void);
/* The SA2_STEAM_BREAK in force, or "" -- so a run can never be read as a
 * control when it was a break. */
const char *sa2_steam_break_name(void);
/* Distinct stat/achievement names this runtime saw but had no room to
 * record. A table that silently stops recording reports an absence that
 * is its own and not the guest's. */
uint32_t sa2_steam_names_dropped(void);

#ifdef __cplusplus
}
#endif
#endif
