/* A recording no-op XAudio2 2.6 engine.
 *
 * Design 3.5 puts audio in group (b), presentation, and design 5 S4's work
 * list asks for "presentation stubs as no-ops that record calls". The user
 * ruled on 2026-09-23 that a refusal counts only if it reproduces the native
 * outcome (Documentation/decisions/2026-09-23-M4-refusal-native-outcome.md),
 * and this one does not: natively the game's
 *
 *     00784103  call [CoCreateInstance]   ; CLSID {3EDA9B49-...} = XAudio2 2.6
 *
 * succeeds (native-outcomes-v1a/native-run.log:15513, :15544 "Created XAudio
 * version 26"), and the game then streams through a source voice from a thread
 * of its own. The runtime answered REGDB_E_CLASSNOTREG, the guest took its
 * failure branch at 0x0078410B, and the sound system fell back to a different
 * output object (0x00785220, a timer-driven one) that native never builds. So
 * the lifted game ran a stream native does not run and skipped the one native
 * does. `agents-journals/s4/2026-09-23-native-outcomes.md` has the
 * measurement.
 *
 * **Nothing here is audible.** No endpoint is opened, no sample is read or
 * written, and the audio bytes a SubmitSourceBuffer names stay the guest's:
 * only their address and length are recorded. Making them into sound is S5's.
 *
 * What the image asks of it, decoded (every offset and push count is checked
 * by tools/s4/xaudio2_surface.py; the full list of sites is there)
 * ----------------------------------------------------------------------
 *   0x007841C0  CoInitializeEx(0, 0); CoCreateInstance; IXAudio2::Initialize
 *               (0, XAUDIO2_DEFAULT_PROCESSOR); CreateMasteringVoice(&[0x01AF0EDC],
 *               0, 48000, 0, 0, NULL); master->GetVoiceDetails(0x01AF0EE0)
 *   0x00784280  CreateSourceVoice(&obj+0x10, 6-channel float at 48 kHz, 0, 2.0,
 *               NULL callback, NULL sends, NULL effects); SetOutputMatrix(master,
 *               6, 2, 0x0089E4A8, 0) only when the master reports 2 channels
 *               (0x007843A6); the streaming thread; one buffer; Start(0, 0)
 *   0x00784760  the streaming thread: GetState, top the queue up to four
 *               buffers, Sleep(10), until the stop flag -- see sa2_xaudio2.c
 *   0x00784450  Stop(0, 0), FlushSourceBuffers, DestroyVoice
 *   0x0077ED20  the sound system's teardown: master DestroyVoice, Release
 *
 * No callback is ever registered (pCallback is the literal 0 pushed at
 * 0x00784328, and RegisterForCallbacks has no call site), so this device never
 * calls guest code.
 *
 * The answer that describes the host
 * ----------------------------------
 * CreateMasteringVoice passes XAUDIO2_DEFAULT_CHANNELS (the 0 pushed at
 * 00784200), so the master's channel count is the endpoint's, and the game
 * reads it back: GetVoiceDetails fills the details the game keeps at
 * 0x01AF0EE0, and 0x007843A6 calls SetOutputMatrix only when they say 2. The
 * count belongs to the host the capture was made on, so under the user's
 * ruling of 2026-09-23 it is the capture's: GetVoiceDetails answers the device
 * profile's `xaudio2_master flags= channels= rate=` (runtime/sa2_devprofile.h),
 * which harness/replay/native_device_profile.c reads natively out of the
 * game's own 0x01AF0EE0 after the game's call, and the answer is labelled
 * `from-profile`. A run with no capture gets the default profile's stereo
 * master at the game's 48000 Hz. Nothing here asks the machine a run is on.
 *
 * A capture's profile says its game had this engine: both profile readers
 * refuse `xaudio2_master voice=0`. So with a capture's profile loaded the
 * device has to be on. Refusing the class would send the guest down the
 * failure branch at 0x0078410B to the fallback output object 0x00785220, which
 * native never builds, and the capture's device set would be approximated
 * rather than reproduced. sa2_xaudio2_config_from_env therefore refuses a
 * capture's profile, by name, when SA2_XAUDIO2_DEVICE is off or
 * SA2_XAUDIO2_BREAK=refuse. The default profile with the device off stays
 * legal: it is the "before" of the switch-on comparison, and no capture stands
 * behind it.
 *
 * Environment
 * -----------
 *   SA2_XAUDIO2_DEVICE=1       CoCreateInstance for XAudio2 2.6's class builds
 *                              this engine (default: it refuses through
 *                              sa2_import_refused with REGDB_E_CLASSNOTREG and
 *                              a null out-pointer, exactly as before this file,
 *                              which only the default device profile allows)
 *   SA2_XAUDIO2_BREAK=<name>   refuse | noconsume | instant | argc; see the
 *                              source
 *   SA2_DEVICE_PROFILE         read by runtime/sa2_devprofile.c, whose
 *                              configuration has to run before this module's
 */
#ifndef SA2_RUNTIME_XAUDIO2_H
#define SA2_RUNTIME_XAUDIO2_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Synthetic method thunks, in the block reserved for this device
 * (0x00232000..0x00232C00), above the dsound thunks and below the image. */
#define SA2_XA2_THUNK_BASE   0x00232000u
#define SA2_XA2_THUNK_STRIDE 16u
#define SA2_XA2_MAX_TYPES    3u      /* IXAudio2, the mastering voice, the source voice */
#define SA2_XA2_MAX_SLOTS    32u     /* IXAudio2SourceVoice 2.6 has 29 */
#define SA2_XA2_THUNK_END    (SA2_XA2_THUNK_BASE + \
                              SA2_XA2_MAX_TYPES * SA2_XA2_MAX_SLOTS * SA2_XA2_THUNK_STRIDE)

/* Fabricated objects and their vtables, in guest scratch, in the blocks
 * reserved for this device: objects 0x03273000..0x03274000, vtables
 * 0x03282000..0x03282600. */
#define SA2_XA2_OBJ_BASE     0x03273000u
#define SA2_XA2_OBJ_STRIDE   64u
#define SA2_XA2_MAX_OBJECTS  64u
#define SA2_XA2_VT_BASE      0x03282000u
#define SA2_XA2_VT_STRIDE    (SA2_XA2_MAX_SLOTS * 4u)

/* Host-side records. A voice is an object too, so these are bounded by the
 * object pool; the queue bound is XAUDIO2_MAX_QUEUED_BUFFERS. */
#define SA2_XA2_MAX_ENGINES  16u
#define SA2_XA2_MAX_VOICES   32u
#define SA2_XA2_MAX_QUEUED   64u

#define SA2_XA2_LOG_MAX      4096u

/* HRESULTs this file returns, by their published values. */
#define SA2_XA2_S_OK                0x00000000u
#define SA2_XA2_REGDB_E_CLASSNOTREG 0x80040154u

/* How an answer was arrived at: the classes of runtime/sa2_dsound.h, less the
 * two this device never needs (an invented value, and a Get with no matching
 * Set), plus the device profile's, as runtime/sa2_ddraw.h has it. */
enum {
    SA2_XA2_ANS_REFUSED = 0,   /* recorded, then trapped by name */
    SA2_XA2_ANS_NOOP    = 1,   /* recorded; nothing to answer, S_OK */
    SA2_XA2_ANS_GUEST   = 2,   /* answered from, or stored as, what the guest supplied */
    SA2_XA2_ANS_PROFILE = 3,   /* from the device profile: the mastering voice's details */
    SA2_XA2_ANS_OBJECT  = 4,   /* answered by fabricating an object */
    SA2_XA2_ANS_CLOCK   = 5,   /* derived from the virtual clock and the voice's own format */
};

enum { SA2_XA2_VOICE_MASTER = 1, SA2_XA2_VOICE_SOURCE = 2 };

typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t self;
    uint64_t virtual_us;        /* the clock this call saw, so a GetState is checkable */
    uint16_t type;
    uint16_t slot;
    uint8_t  answered;
    uint8_t  argc;
    uint8_t  thread;            /* the guest thread: the streaming thread is not thread 0 */
    uint32_t arg[8];
    uint32_t ret;
    uint32_t out0, out1;        /* what a Get wrote: GetState's BuffersQueued and SamplesPlayed */
} sa2_xa2_call;

/* One row per (interface, slot) ever called, exact past the log's end. */
typedef struct {
    uint32_t calls;
    uint32_t first_seq;
    uint32_t first_caller;
    uint8_t  answered;
    uint8_t  used;
} sa2_xa2_slot_stat;

typedef struct {
    uint32_t addr;              /* the guest pointer CoCreateInstance wrote */
    uint32_t create_caller;
    uint32_t initialized, init_flags, init_processor;
    uint32_t refs, released, release_calls;
    uint32_t master;            /* the mastering voice's guest pointer, 0 when none */
    uint32_t source_voices;     /* created on this engine */
} sa2_xa2_engine;

/* One row per voice, kept after DestroyVoice so a run that ends mid-stream
 * still says what existed. The clock fields are the inputs of the arithmetic
 * in sa2_xaudio2.c, published so a reader can redo it. */
typedef struct {
    uint32_t addr;
    uint32_t engine;            /* the engine's guest pointer */
    uint32_t create_caller;
    uint32_t kind;              /* SA2_XA2_VOICE_* */
    uint32_t flags;             /* the creation Flags, as the guest passed them */
    uint32_t destroyed, destroy_caller;
    /* a mastering voice: what GetVoiceDetails reports, which is the device
     * profile's `xaudio2_master` latched when the voice was created */
    uint32_t details_flags, input_channels, input_sample_rate, details_calls;
    /* a source voice's WAVEFORMATEXTENSIBLE, as the guest passed it */
    uint32_t format_tag, channels, samples_per_sec, avg_bytes_per_sec;
    uint32_t block_align, bits_per_sample, cb_size, valid_bits, channel_mask;
    uint32_t max_ratio_bits;    /* MaxFrequencyRatio, float32 bits */
    uint32_t ratio_bits;        /* the frequency ratio in force, float32 bits */
    uint32_t output;            /* the voice SetOutputMatrix named */
    uint32_t matrix_src, matrix_dst, matrix_calls;
    uint32_t matrix[12];        /* the level matrix, float32 bits; not audio data */
    uint32_t running;
    uint64_t anchor_us;         /* virtual us at the last re-anchor */
    uint64_t anchor_frame;      /* the stream frame the voice was at then */
    uint64_t played;            /* frames consumed: GetState's SamplesPlayed */
    uint64_t queued_end;        /* the stream frame at which the last queued buffer ends */
    uint32_t queued, max_queued;
    uint32_t submits, completed, flushed, dropped_at_destroy;
    uint64_t submitted_bytes;
    uint32_t start_calls, stop_calls, flush_calls, getstate_calls, ratio_calls;
    uint32_t starvations;       /* times a started voice ran out of queued buffers */
    uint32_t last_queued;       /* the last GetState answer, and when it was given */
    uint64_t last_samples, last_state_us;
} sa2_xa2_voice;

typedef struct {
    uint32_t cocreate_calls;    /* CoCreateInstance with XAudio2 2.6's CLSID */
    uint32_t cocreate_refused;  /* ...answered REGDB_E_CLASSNOTREG: device off, or `refuse` */
    uint32_t engines;
    uint32_t method_calls, distinct_methods, objects_built;
    uint32_t refused, noop, from_guest, from_profile, objects, clock;
    /* A method whose `this` is no object of that interface this runtime made,
     * or one already destroyed or released. Each refuses by name as well. */
    uint32_t unknown_this, dead_this;
    uint32_t mastering_voices, source_voices, voices_destroyed;
    uint32_t submits, buffers_completed, getstate_calls, starvations;
    uint64_t submitted_bytes;
    uint64_t frames_played;     /* every source voice's position when this was read */
    uint32_t log_dropped;       /* calls past SA2_XA2_LOG_MAX: counted, not logged */
} sa2_xa2_stats;

/* Reads SA2_XAUDIO2_DEVICE and SA2_XAUDIO2_BREAK, and traps by name, before
 * the guest starts, when a capture's device profile is loaded and the device
 * is off or the `refuse` break is set (see "The answer that describes the
 * host"). It asks runtime/sa2_devprofile.c which profile is loaded, so it
 * runs after sa2_devprofile_config_from_env. Idempotent. */
void        sa2_xaudio2_config_from_env(void);
int         sa2_xaudio2_device_enabled(void);
const char *sa2_xaudio2_break_name(void);          /* "" when no break is set */

/* ole32!CoCreateInstance's hook, called from the import with the class and
 * interface GUIDs it read and the ppv it was passed. `s` is the guest CPU
 * state of that import call: ESP still points at its return address, so
 * pUnkOuter and dwClsContext are read from [esp+8] and [esp+12].
 *
 * For any class but XAudio2 2.6's, *handled is 0 and nothing is touched. For
 * that class *handled is 1 and the return value is the whole answer: the
 * engine and S_OK when the device is on, or REGDB_E_CLASSNOTREG through
 * sa2_import_refused, with *ppv zeroed, when it is off under the default
 * device profile. Under a capture's profile a device that is off traps by
 * name instead. Every refusal by name it raises names the
 * ole32!CoCreateInstance import's thunk as its exit_pc. */
uint32_t sa2_xaudio2_cocreate(void *s, const uint8_t clsid[16], const uint8_t iid[16],
                              uint32_t ppv, uint32_t *handled);

void                sa2_xaudio2_stats_get(sa2_xa2_stats *out);
uint32_t            sa2_xaudio2_log_count(void);
const sa2_xa2_call *sa2_xaudio2_log_at(uint32_t i);

uint32_t    sa2_xaudio2_type_count(void);
const char *sa2_xaudio2_type_name(uint32_t type);
const char *sa2_xaudio2_method_name(uint32_t type, uint32_t slot);   /* NULL when unnamed */
int         sa2_xaudio2_method_argc(uint32_t type, uint32_t slot);   /* -1 when unnamed */
int         sa2_xaudio2_method_implemented(uint32_t type, uint32_t slot);
const char *sa2_xaudio2_method_flags(uint32_t type, uint32_t slot);
const sa2_xa2_slot_stat *sa2_xaudio2_slot_stat_at(uint32_t type, uint32_t slot);

uint32_t              sa2_xaudio2_engine_count(void);
const sa2_xa2_engine *sa2_xaudio2_engine_at(uint32_t i);
uint32_t              sa2_xaudio2_voice_count(void);
const sa2_xa2_voice  *sa2_xaudio2_voice_at(uint32_t i);
/* What a GetState on voice `i` would answer *now*, without recording a call or
 * settling anything: for the probe's own end-of-run snapshot. 0 for a voice
 * that is not a source voice. */
int sa2_xaudio2_voice_now(uint32_t i, uint32_t *queued, uint64_t *samples_played);

#ifdef __cplusplus
}
#endif
#endif
