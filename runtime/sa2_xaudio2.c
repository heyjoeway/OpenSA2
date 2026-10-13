/* A recording no-op XAudio2 2.6 engine. See sa2_xaudio2.h.
 *
 * The vtables are XAudio2 2.6's, which are not the ones a modern header
 * describes. Three sources agree on every row below, and none of them is a
 * header:
 *
 *  - **the image.** Every call site's slot and push count, decoded
 *    (tools/s4/xaudio2_surface.py): Initialize at +0x14, CreateSourceVoice at
 *    +0x20 and CreateMasteringVoice at +0x28 of IXAudio2, which has
 *    GetDeviceCount, GetDeviceDetails and Initialize where XAudio2 2.8 has
 *    none; GetState at +0x64 with one argument, where 2.8's takes two.
 *  - **Wine's xaudio2_6.dll**, the one the native run loaded
 *    (native-run.log:15522): its three vtables, named by the `__func__`
 *    strings its methods pass to the trace, and each method's stdcall pop
 *    read off its own `ret`. `xaudio2_surface.py --wine` redoes this.
 *  - **the trace's argument lists** (native-run.log:15546, :15547, :15556,
 *    :15558, :15565, :15566, :15569).
 *
 * The zig tree's any-windows-any/xaudio2.h is the 2.8 interface. Its voice
 * tables agree with 2.6 row for row; its IXAudio2 and its GetState do not, and
 * a device numbered from it would pop one dword too many at every GetState --
 * which is exactly `SA2_XAUDIO2_BREAK=argc`.
 *
 * The three rules of runtime/sa2_dsound.c hold here: every slot is named with
 * its argument count; a slot with no handler refuses by name and is never
 * popped with a guessed count; every answer is labelled. Two more are this
 * device's own:
 *
 *  4. **Only the slots the image calls have handlers** -- fourteen, listed by
 *     the surface tool. A call through any other slot is a call the decode
 *     says cannot happen, and it stops the run with the slot's name.
 *  5. **An argument the decoded sites never pass refuses by name.** The image
 *     passes literals almost everywhere (the flags, the rates, the channel
 *     counts, a null effect chain, and three addresses: the master's pointer
 *     global, the details beside it and the downmix matrix), so a different
 *     value means the decode is wrong, and answering it would hide that.
 */
#include "sa2_xaudio2.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_devprofile.h"
#include "sa2_dsound.h"
#include "sa2_imports.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

/* ------------------------------------------------------------- regions */

_Static_assert(SA2_XA2_THUNK_BASE >= SA2_DS_THUNK_END,
               "the xaudio2 thunks must sit above the dsound thunks");
_Static_assert(SA2_XA2_THUNK_BASE >= 0x00232000u && SA2_XA2_THUNK_END <= 0x00232C00u,
               "the xaudio2 thunks must stay inside the block reserved for them");
_Static_assert(SA2_XA2_THUNK_END <= 0x00300000u,
               "the xaudio2 thunks must stay below 0x00300000, which the probe universe owns");
_Static_assert(SA2_XA2_OBJ_BASE >= SA2_DS_VT_BASE + SA2_DS_MAX_TYPES * SA2_DS_VT_STRIDE,
               "the xaudio2 objects must sit above the dsound vtables");
_Static_assert(SA2_XA2_OBJ_BASE >= 0x03273000u &&
               SA2_XA2_OBJ_BASE + SA2_XA2_MAX_OBJECTS * SA2_XA2_OBJ_STRIDE <= 0x03274000u,
               "the xaudio2 objects must stay inside the block reserved for them");
_Static_assert(SA2_XA2_VT_BASE >= 0x03282000u &&
               SA2_XA2_VT_BASE + SA2_XA2_MAX_TYPES * SA2_XA2_VT_STRIDE <= 0x03282600u,
               "the xaudio2 vtables must stay inside the block reserved for them");
_Static_assert(SA2_XA2_VT_BASE + SA2_XA2_MAX_TYPES * SA2_XA2_VT_STRIDE <= SA2_GUEST_RT_ARENA_BASE,
               "the xaudio2 vtables must not run into the runtime arena");
_Static_assert(SA2_XA2_MAX_VOICES + SA2_XA2_MAX_ENGINES <= SA2_XA2_MAX_OBJECTS,
               "every host record needs a guest object to stand for it");

/* ------------------------------------------------------------ the rows */

enum {
    XF_OUT  = 1u << 0,   /* writes through a pointer the guest dereferences */
    XF_CLK  = 1u << 1,   /* an answer derived from the virtual clock */
    XF_HOST = 1u << 2,   /* an answer that describes the capture host's audio endpoint */
};

typedef struct xa_ctx xa_ctx;
typedef uint32_t (*xa_method)(xa_ctx *c);

typedef struct {
    uint8_t     slot;
    uint8_t     argc;        /* stack dwords, NOT counting `this` */
    uint8_t     flags;
    const char *name;
    xa_method   fn;
} xa_row;

typedef struct {
    const char *name;
    xa_row     *rows;
    uint32_t    vt;          /* the vtable's guest address, 0 until built */
} xa_type;

enum { T_ENGINE = 0, T_MASTER = 1, T_SOURCE = 2 };

typedef struct {
    uint32_t addr;
    uint16_t type;
    uint16_t alive;
    uint32_t rec;            /* index into g_eng for T_ENGINE, into g_voice otherwise */
} xa_obj;

struct xa_ctx {
    void           *s;
    uint32_t        pc;      /* the thunk: where a refusal is raised */
    uint32_t        type;
    const xa_row   *row;
    uint32_t        self;
    xa_obj         *obj;
    sa2_xa2_engine *e;       /* the engine record, for T_ENGINE */
    sa2_xa2_voice  *v;       /* the voice record, for a voice */
    uint32_t        vi;      /* its index */
    const uint32_t *a;
    uint32_t        caller;
    uint8_t         answered;
    uint32_t        out0, out1;
};

/* -------------------------------------------------------------- breaks
 *
 * `SA2_XAUDIO2_BREAK=<name>`, so each claim this file makes can be shown
 * failing:
 *
 *   refuse     the device is on and CoCreateInstance still refuses the class
 *              through the marker: today's answer, kept as the control a
 *              device-on run is compared against. Under a capture's device
 *              profile it is the device off by another name, and
 *              configuration refuses it as it refuses that
 *   noconsume  a started voice never consumes: GetState reports every buffer
 *              ever submitted as still queued. The streaming thread then
 *              submits four buffers in the whole run and only polls, which is
 *              how "a voice that does not advance starves the game's mixer"
 *              is shown rather than asserted
 *   instant    a started voice consumes whatever is queued at once, so every
 *              GetState reports none queued and the thread submits four
 *              buffers per 10 ms poll, about four times native's rate
 *   argc       GetState is declared with two arguments, XAudio2 2.8's
 *              signature: the dispatcher pops one dword more than the image
 *              pushed at 0x0078478E, which is the header mistake this file
 *              exists to avoid
 */
enum { BRK_NONE = 0, BRK_REFUSE, BRK_NOCONSUME, BRK_INSTANT, BRK_ARGC };
static int g_break;
static const char *const BREAK_NAMES[] = {"", "refuse", "noconsume", "instant", "argc"};

/* ---------------------------------------------------------------- state */

static sa2_xa2_stats     g_st;
static sa2_xa2_call      g_log[SA2_XA2_LOG_MAX];
static uint32_t          g_log_used, g_seq;
static sa2_xa2_slot_stat g_slot[SA2_XA2_MAX_TYPES][SA2_XA2_MAX_SLOTS];
static xa_obj            g_obj[SA2_XA2_MAX_OBJECTS];
static uint32_t          g_obj_used;
static sa2_xa2_engine    g_eng[SA2_XA2_MAX_ENGINES];
static uint32_t          g_eng_used;
static sa2_xa2_voice     g_voice[SA2_XA2_MAX_VOICES];
static uint32_t          g_voice_used;
static int               g_enabled, g_types_bound;

/* A source voice's queue. Buffers are contiguous in stream frames: each one
 * begins where the one before it ends, because a voice can never be past the
 * end of what it was given. So one number per buffer, where it ends, is the
 * whole layout. */
typedef struct { uint64_t end; uint32_t frames, bytes, data, context; } xa_qent;
static xa_qent  g_q[SA2_XA2_MAX_VOICES][SA2_XA2_MAX_QUEUED];
static uint32_t g_qhead[SA2_XA2_MAX_VOICES], g_qcount[SA2_XA2_MAX_VOICES];

/* The GUIDs, little-endian as they sit in memory, and as the image holds them
 * at 0x00902200 (the class), 0x00902210 (the interface) and 0x00902220 (the
 * source format's SubFormat). */
static const uint8_t CLSID_XAUDIO2_26[16] = {   /* {3EDA9B49-2085-498B-9BB2-39A6778493DE} */
    0x49,0x9B,0xDA,0x3E,0x85,0x20,0x8B,0x49,0x9B,0xB2,0x39,0xA6,0x77,0x84,0x93,0xDE};
static const uint8_t IID_IXAUDIO2_27[16] = {    /* {8BCF1F58-9FE7-4583-8AC6-E2ADC465C8BB} */
    0x58,0x1F,0xCF,0x8B,0xE7,0x9F,0x83,0x45,0x8A,0xC6,0xE2,0xAD,0xC4,0x65,0xC8,0xBB};
static const uint8_t SUBTYPE_IEEE_FLOAT[16] = { /* {00000003-0000-0010-8000-00AA00389B71} */
    0x03,0x00,0x00,0x00,0x00,0x00,0x10,0x00,0x80,0x00,0x00,0xAA,0x00,0x38,0x9B,0x71};

/* The three addresses the image pushes, required literally (rule 5):
 * ppMasteringVoice, the global the game reads its master back out of (pushed at
 * 00784202); pVoiceDetails, the details the game keeps beside it (00784217);
 * and pLevelMatrix, the six-to-two downmix in .rdata (007843BB). */
#define XA_PP_MASTER    0x01AF0EDCu
#define XA_DETAILS      0x01AF0EE0u
#define XA_LEVEL_MATRIX 0x0089E4A8u

/* Float32 bit patterns this file compares against. */
#define F32_ONE          0x3F800000u
#define F32_TWO          0x40000000u   /* MaxFrequencyRatio, loaded from 0x012848D4 */
#define F32_MIN_RATIO    0x3A800000u   /* XAUDIO2_MIN_FREQ_RATIO, 1/1024 */

/* ------------------------------------------------------------ plumbing */

static void gfault(uint32_t a, const char *what) {
    sa2_trap_note("xaudio2: %s at guest %#010x is outside the guest space", what, a);
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
static uint16_t pe16(uint32_t a) {
    uint16_t v = 0;
    if (sa2_peek(a, 2, &v) != SA2_OK) gfault(a, "a read of 2 bytes");
    return v;
}

static const char *type_name_of(uint32_t t);

/* A refusal by name: the interface, the method, the caller, and why. Trap
 * notes are SA2_TRAP_NOTE_MAX bytes, so the name comes first. */
static void refuse(const xa_ctx *c, const char *fmt, ...)
    __attribute__((noreturn, format(printf, 2, 3)));
static void refuse(const xa_ctx *c, const char *fmt, ...) {
    char why[SA2_TRAP_NOTE_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(why, sizeof why, fmt, ap);
    va_end(ap);
    sa2_trap_note("xaudio2 %s::%s from %#010x: %s", type_name_of(c->type),
                  c->row ? c->row->name : "?", c->caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
}

/* --------------------------------------------------------- the objects */

static uint32_t type_vtable(uint32_t t);

static xa_obj *obj_of(uint32_t addr) {
    if (addr < SA2_XA2_OBJ_BASE) return NULL;
    const uint32_t i = (addr - SA2_XA2_OBJ_BASE) / SA2_XA2_OBJ_STRIDE;
    if (i >= g_obj_used || g_obj[i].addr != addr) return NULL;
    return &g_obj[i];
}

static xa_obj *obj_new(uint32_t t, uint32_t rec) {
    if (g_obj_used >= SA2_XA2_MAX_OBJECTS) {
        sa2_trap_note("xaudio2: the fabricated-object pool of %u is exhausted; the guest has "
                      "created more engines and voices than this device holds",
                      SA2_XA2_MAX_OBJECTS);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_XA2_OBJ_BASE);
    }
    xa_obj *o = &g_obj[g_obj_used];
    memset(o, 0, sizeof *o);
    o->addr = SA2_XA2_OBJ_BASE + g_obj_used * SA2_XA2_OBJ_STRIDE;
    o->type = (uint16_t)t;
    o->alive = 1;
    o->rec = rec;
    g_obj_used++;
    g_st.objects_built++;
    /* A vtable pointer, then this runtime's own tag, so a stray pointer found
     * later in guest memory is identifiable by its bytes alone. The image
     * reads nothing past +0 of any XAudio2 object. */
    pk32(o->addr, type_vtable(t));
    pk32(o->addr + 4u, t == T_ENGINE ? 0x58413245u : t == T_MASTER ? 0x5841324Du
                                                                    : 0x58413253u);  /* 'XA2E' 'XA2M' 'XA2S' */
    pk32(o->addr + 8u, t);
    pk32(o->addr + 12u, g_obj_used - 1u);
    return o;
}

/* --------------------------------------------------------- the clock
 *
 * A source voice consumes its queue at nSamplesPerSec x ratio frames per
 * second of virtual time, from an anchor:
 *
 *     frames = anchor_frame + floor(elapsed_us * nSamplesPerSec * ratio / 1e6)
 *     frames = min(frames, the end of the last queued buffer)
 *
 * which is runtime/sa2_dsound.c's `anchor_pos + elapsed_us * nAvgBytesPerSec /
 * 1e6` counted in frames rather than bytes. At ratio 1 the two are the same
 * number of bytes: floor(floor(24x) / 24) = floor(x), so rounding dsound's
 * byte count down to nBlockAlign gives exactly 24 x this frame count, and a
 * position in frames is block-aligned by construction.
 *
 * The same five disciplines as dsound's cursor:
 *  - integer arithmetic only, floored: the ratio is a float32 and so exactly
 *    mant x 2^exp with a 24-bit mant, and the product is formed in 128 bits;
 *    elapsed x 48000 passes 2^32 after 90 ms of virtual time, so a 32-bit
 *    product would be wrong inside the first ten polls of the streaming thread;
 *  - the clock is sa2_virtual_us(), the one Present and Sleep move; nothing
 *    here reads host time;
 *  - Start, Stop and SetFrequencyRatio re-anchor, and so does a submit to a
 *    voice that has run dry: consumption is elapsed-since-anchor, never
 *    elapsed-since-start, and a voice with nothing queued banks no time;
 *  - GetState never re-anchors, so polling does not lose fractional frames
 *    and the answer at an instant does not depend on how often it was asked;
 *  - a voice stops at the end of what it was given. It does not run ahead.
 *
 * Native FAudio consumes in 10 ms processing passes on a thread of its own
 * (1250 XA2ECB_OnProcessingPassStart lines on 01cc in native-run.log). This
 * device consumes continuously. The two answers differ by one buffer only
 * when a poll lands within one pass of a buffer's end. Native's own polls
 * drift by more than that: its inferred answers include 32 twos, and polls
 * exactly 10 ms apart never produce a two (harness/xaudio2_probe.c lists
 * both). */
static uint64_t frames_after(const xa_ctx *c, const sa2_xa2_voice *v, uint64_t elapsed_us) {
    const uint32_t e = (v->ratio_bits >> 23) & 0xFFu;
    const uint64_t mant = (uint64_t)((v->ratio_bits & 0x7FFFFFu) | 0x800000u);
    /* The ratio is clamped to [1/1024, 2.0] before it is stored, so it is a
     * normal number with an exponent in [-10, 1] and `shift` is 22..33. */
    const uint32_t shift = 150u - e;
    const unsigned __int128 n = (unsigned __int128)elapsed_us
                              * (unsigned __int128)v->samples_per_sec * mant;
    const unsigned __int128 q = n >> shift;   /* floor(floor(n/2^k)/1e6) = floor(n/(2^k*1e6)) */
    if (q >> 64) refuse(c, "%llu us of virtual time since the voice's anchor is past what the "
                           "frame arithmetic holds", (unsigned long long)elapsed_us);
    return (uint64_t)q / 1000000u;
}

/* Where a source voice is at `now`, without changing anything. */
static uint64_t voice_position(const xa_ctx *c, const sa2_xa2_voice *v, uint32_t vi, uint64_t now) {
    if (!v->running || !g_qcount[vi]) return v->played;
    if (g_break == BRK_NOCONSUME) return v->played;
    if (g_break == BRK_INSTANT) return v->queued_end;
    const uint64_t el = now > v->anchor_us ? now - v->anchor_us : 0u;
    const uint64_t at = v->anchor_frame + frames_after(c, v, el);
    return at < v->queued_end ? at : v->queued_end;
}

static uint32_t queued_after(uint32_t vi, uint64_t pos) {
    uint32_t n = 0;
    for (uint32_t k = 0; k < g_qcount[vi]; k++)
        if (g_q[vi][(g_qhead[vi] + k) % SA2_XA2_MAX_QUEUED].end > pos) n++;
    return n;
}

static void anchor(sa2_xa2_voice *v, uint64_t now) {
    v->anchor_us = now;
    v->anchor_frame = v->played;
}

/* Advance to `now` and retire the buffers that have been played. */
static void settle(const xa_ctx *c, sa2_xa2_voice *v, uint32_t vi, uint64_t now) {
    const uint32_t had = g_qcount[vi];
    v->played = voice_position(c, v, vi, now);
    while (g_qcount[vi] && g_q[vi][g_qhead[vi]].end <= v->played) {
        g_qhead[vi] = (g_qhead[vi] + 1u) % SA2_XA2_MAX_QUEUED;
        g_qcount[vi]--;
        v->completed++;
        g_st.buffers_completed++;
    }
    /* Ran dry. Nothing moves until the next submit re-anchors, so where the
     * anchor sits meanwhile changes no answer. */
    if (v->running && had && !g_qcount[vi]) {
        v->starvations++;
        g_st.starvations++;
        anchor(v, now);
    }
    v->queued = g_qcount[vi];
}

static void drop_queue(sa2_xa2_voice *v, uint32_t vi, uint32_t keep) {
    const uint32_t n = g_qcount[vi] > keep ? g_qcount[vi] - keep : 0u;
    g_qcount[vi] -= n;
    v->queued_end = g_qcount[vi] ? g_q[vi][g_qhead[vi]].end : v->played;
    v->queued = g_qcount[vi];
    v->flushed += n;
}

/* ------------------------------------------------------ IXAudio2 */

static uint32_t m_Initialize(xa_ctx *c) {
    /* 0x0078411B pushes (0, 0xFFFFFFFF): no flags, XAUDIO2_DEFAULT_PROCESSOR.
     * Native: native-run.log:15546, and CreateMasteringVoice at :15547 is
     * reached only past the failure test at 0x00784125. */
    if (c->a[0] != 0u || c->a[1] != 0xFFFFFFFFu)
        refuse(c, "Initialize(%#x, %#x); the image passes (0, XAUDIO2_DEFAULT_PROCESSOR) and "
                  "nothing else", c->a[0], c->a[1]);
    if (c->e->initialized)
        refuse(c, "a second Initialize on one engine; the image initialises each engine once, "
                  "straight after CoCreateInstance");
    c->e->initialized = 1;
    c->e->init_flags = c->a[0];
    c->e->init_processor = c->a[1];
    c->answered = SA2_XA2_ANS_NOOP;
    return SA2_XA2_S_OK;
}

static sa2_xa2_voice *voice_new(xa_ctx *c, uint32_t kind, uint32_t *vi) {
    if (g_voice_used >= SA2_XA2_MAX_VOICES)
        refuse(c, "the voice records (%u) are exhausted", SA2_XA2_MAX_VOICES);
    *vi = g_voice_used;
    sa2_xa2_voice *v = &g_voice[g_voice_used++];
    memset(v, 0, sizeof *v);
    v->engine = c->self;
    v->create_caller = c->caller;
    v->kind = kind;
    v->ratio_bits = F32_ONE;
    g_qhead[*vi] = g_qcount[*vi] = 0;
    return v;
}

/* CreateMasteringVoice(ppMasteringVoice, InputChannels, InputSampleRate,
 *                      Flags, DeviceIndex, pEffectChain), XAudio2 2.7 and
 * earlier: a device index where 2.8 takes a device id string. 0x00784208 pushes
 * (0x01AF0EDC, 0, 48000, 0, 0, 0); native-run.log:15547-15548 is the same call
 * with "device index 0", and GetVoiceDetails at :15553 is reached only past
 * the failure test at 0x0078420C.
 *
 * The details the voice will report are latched here from the device profile.
 * InputChannels is XAUDIO2_DEFAULT_CHANNELS, the 0 pushed at 00784200, so the
 * count is the capture host's endpoint's (sa2_xaudio2.h, "The answer that
 * describes the host"). The guest's own Flags stay in `flags`, where the
 * literal check put them. */
static uint32_t m_CreateMasteringVoice(xa_ctx *c) {
    sa2_xa2_engine *e = c->e;
    if (!e->initialized)
        refuse(c, "CreateMasteringVoice before Initialize; 0x007840F0 initialises the engine "
                  "before 0x007841C0 can reach this call");
    if (e->master)
        refuse(c, "a second mastering voice on one engine; the image creates one per engine, "
                  "behind the init count at 0x007841CE");
    if (c->a[0] != XA_PP_MASTER || c->a[1] != 0u || c->a[2] != 48000u || c->a[3] != 0u
        || c->a[4] != 0u || c->a[5] != 0u)
        refuse(c, "(%#010x, %u, %u, %#x, %u, %#010x); the image passes (0x01AF0EDC, 0, 48000, 0, "
                  "0, NULL) and nothing else", c->a[0], c->a[1], c->a[2], c->a[3], c->a[4],
               c->a[5]);
    uint32_t vi;
    sa2_xa2_voice *v = voice_new(c, SA2_XA2_VOICE_MASTER, &vi);
    v->flags = c->a[3];
    v->details_flags = sa2_dp_xaudio2_master_flags();
    v->input_channels = sa2_dp_xaudio2_master_channels();
    v->input_sample_rate = sa2_dp_xaudio2_master_rate();
    const xa_obj *o = obj_new(T_MASTER, vi);
    v->addr = o->addr;
    e->master = o->addr;
    g_st.mastering_voices++;
    pk32(c->a[0], o->addr);
    c->out0 = o->addr;
    c->answered = SA2_XA2_ANS_OBJECT;
    return SA2_XA2_S_OK;
}

/* The source format 0x00784280 builds on its own stack, 0x007842EC..0x0078438E:
 * WAVE_FORMAT_EXTENSIBLE, six channels of 32-bit float at 48 kHz, channel mask
 * 0x3F, KSDATAFORMAT_SUBTYPE_IEEE_FLOAT. Any other format is one the image
 * never passes. The first field that differs is named. */
static void read_format(xa_ctx *c, sa2_xa2_voice *v, uint32_t p) {
    v->format_tag        = pe16(p + 0u);
    v->channels          = pe16(p + 2u);
    v->samples_per_sec   = pe32(p + 4u);
    v->avg_bytes_per_sec = pe32(p + 8u);
    v->block_align       = pe16(p + 12u);
    v->bits_per_sample   = pe16(p + 14u);
    v->cb_size           = pe16(p + 16u);
    v->valid_bits        = pe16(p + 18u);
    v->channel_mask      = pe32(p + 20u);
    uint8_t sub[16];
    if (sa2_peek(p + 24u, 16, sub) != SA2_OK) gfault(p + 24u, "a read of 16 bytes");
    static const struct { const char *name; uint32_t want; } F[] = {
        {"wFormatTag", 0xFFFEu}, {"nChannels", 6u}, {"nSamplesPerSec", 48000u},
        {"nAvgBytesPerSec", 1152000u}, {"nBlockAlign", 24u}, {"wBitsPerSample", 32u},
        {"cbSize", 22u}, {"wValidBitsPerSample", 32u}, {"dwChannelMask", 0x3Fu},
    };
    const uint32_t got[9] = {v->format_tag, v->channels, v->samples_per_sec,
                             v->avg_bytes_per_sec, v->block_align, v->bits_per_sample,
                             v->cb_size, v->valid_bits, v->channel_mask};
    for (uint32_t k = 0; k < 9u; k++)
        if (got[k] != F[k].want)
            refuse(c, "the source format's %s is %u and the image builds %u; this device answers "
                      "only the format the image passes", F[k].name, got[k], F[k].want);
    if (memcmp(sub, SUBTYPE_IEEE_FLOAT, 16))
        refuse(c, "the source format's SubFormat is not KSDATAFORMAT_SUBTYPE_IEEE_FLOAT, which "
                  "the image copies from 0x00902220");
}

/* CreateSourceVoice(ppSourceVoice, pSourceFormat, Flags, MaxFrequencyRatio,
 *                   pCallback, pSendList, pEffectChain). 0x007843A0 pushes
 * (obj+0x10, the format, 0, 2.0f, NULL, NULL, NULL); native-run.log:15556
 * prints the same seven, and :15557 "Created source voice". */
static uint32_t m_CreateSourceVoice(xa_ctx *c) {
    sa2_xa2_engine *e = c->e;
    if (!e->initialized)
        refuse(c, "CreateSourceVoice before Initialize");
    /* A null send list means "to the mastering voice", so there must be one;
     * 0x0077EC62 builds it before 0x0077EC67 builds this. */
    if (!e->master)
        refuse(c, "a source voice with no mastering voice to send to; the image creates the "
                  "mastering voice first");
    if (!c->a[0] || !c->a[1] || c->a[2] != 0u || c->a[3] != F32_TWO || c->a[4] != 0u
        || c->a[5] != 0u || c->a[6] != 0u)
        refuse(c, "(%#010x, %#010x, %#x, ratio bits %#010x, %#010x, %#010x, %#010x); the image "
                  "passes (&voice, &format, 0, 2.0f, NULL, NULL, NULL) and nothing else",
               c->a[0], c->a[1], c->a[2], c->a[3], c->a[4], c->a[5], c->a[6]);
    /* Read and checked before a record exists, so a refused format leaves none. */
    sa2_xa2_voice fmt;
    memset(&fmt, 0, sizeof fmt);
    read_format(c, &fmt, c->a[1]);
    uint32_t vi;
    sa2_xa2_voice *v = voice_new(c, SA2_XA2_VOICE_SOURCE, &vi);
    v->format_tag = fmt.format_tag;
    v->channels = fmt.channels;
    v->samples_per_sec = fmt.samples_per_sec;
    v->avg_bytes_per_sec = fmt.avg_bytes_per_sec;
    v->block_align = fmt.block_align;
    v->bits_per_sample = fmt.bits_per_sample;
    v->cb_size = fmt.cb_size;
    v->valid_bits = fmt.valid_bits;
    v->channel_mask = fmt.channel_mask;
    v->flags = c->a[2];
    v->max_ratio_bits = c->a[3];
    v->output = e->master;
    const xa_obj *o = obj_new(T_SOURCE, vi);
    v->addr = o->addr;
    e->source_voices++;
    g_st.source_voices++;
    pk32(c->a[0], o->addr);
    c->out0 = o->addr;
    c->answered = SA2_XA2_ANS_OBJECT;
    return SA2_XA2_S_OK;
}

/* Release. The image releases each engine once, after destroying its voices:
 * 0x0077EE31 after 0x0077EE1A in the sound system's teardown, 0x00784269 in
 * the init's own undo, and 0x00784137 when Initialize failed, before any voice
 * exists. A final release with a voice still alive is a teardown order the
 * image does not have. */
static uint32_t m_Release(xa_ctx *c) {
    sa2_xa2_engine *e = c->e;
    e->release_calls++;
    if (e->refs == 1u) {
        for (uint32_t i = 0; i < g_voice_used; i++)
            if (g_voice[i].engine == c->self && !g_voice[i].destroyed)
                refuse(c, "the final Release with voice %#010x still alive; the image destroys "
                          "every voice before it releases the engine", g_voice[i].addr);
        e->released = 1;
        c->obj->alive = 0;
    }
    e->refs--;
    c->answered = SA2_XA2_ANS_GUEST;
    return e->refs;
}

/* ------------------------------------------------ the mastering voice */

/* GetVoiceDetails(pVoiceDetails). The 2.7 structure is 12 bytes --
 * CreationFlags, InputChannels, InputSampleRate -- and 2.8 added a fourth
 * field; Wine's get_voice_details, which answered natively
 * (native-run.log:15553), stores exactly three dwords. A 16-byte write here
 * would land on 0x01AF0EEC, which BasicTest_Init owns. The three are the device
 * profile's, latched when the voice was created: the profile probe read them
 * natively out of these same 12 bytes after the game's own call. */
static uint32_t m_MasterGetVoiceDetails(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    if (c->a[0] != XA_DETAILS)
        refuse(c, "pVoiceDetails %#010x; the image passes 0x01AF0EE0, the details it keeps "
                  "beside the voice, and nothing else", c->a[0]);
    pk32(c->a[0] + 0u, v->details_flags);
    pk32(c->a[0] + 4u, v->input_channels);
    pk32(c->a[0] + 8u, v->input_sample_rate);
    v->details_calls++;
    c->out0 = v->input_channels;
    c->out1 = v->input_sample_rate;
    c->answered = SA2_XA2_ANS_PROFILE;
    return SA2_XA2_S_OK;
}

/* ---------------------------------------------------- the source voice
 *
 * Who asks, and how it waits. The streaming thread is 0x00784760, started
 * suspended by _beginthreadex at 0x007843D9 with the stream object as its
 * parameter and resumed at 0x00784420:
 *
 *     00784779  mov ebx,[873238h]        ; Sleep
 *     00784780  mov eax,[esi+10h]        ; the source voice
 *     0078478e  call eax                 ; GetState(&state), [ecx+64h]
 *     00784790  mov eax,[esp+14h]        ; state.BuffersQueued
 *     00784794  cmp eax,4
 *     00784797  jae 007847AAh
 *     00784799  mov edi,4 ; sub edi,eax  ; 4 - BuffersQueued times:
 *     007847a0  call 007845C0h           ;   ask the mixer for 512 frames and
 *                                        ;   submit what it made
 *     007847aa  push 0Ah ; call ebx      ; Sleep(10)
 *     007847ae  mov ecx,[esi+33038h]     ; the stop flag, set by 0x00784450
 *     007847b4  test ecx,ecx
 *     007847b6  je 00784780h
 *
 * It sleeps in Sleep(10) and nowhere else. The one other place it can block
 * is the stream object's critical section, entered at 0x007845CB, which the
 * main thread takes only in the object's three setters (0x00784550,
 * 0x007844F0, 0x00784520) and once for the first buffer (0x00784426); this
 * device takes no lock. There is no event and no callback: none is
 * registered. Every iteration gives the scheduler 10 ms of virtual time
 * whatever GetState answers, so no answer this device gives can spin it. An
 * answer stuck at four (`noconsume`) leaves it polling and never submitting;
 * one stuck at zero (`instant`) makes it mix four buffers per iteration. Only
 * an answer that falls at the voice's own rate makes it mix at native's,
 * which is why the rate is derived and not chosen. It ends when 0x00784450
 * sets the flag and waits for it (WaitForSingleObject, INFINITE, at
 * 0x0078446F), before Stop, FlushSourceBuffers and DestroyVoice. */

/* SetOutputMatrix(pDestinationVoice, SourceChannels, DestinationChannels,
 *                 pLevelMatrix, OperationSet). 0x007843C9 pushes (master, 6, 2,
 * 0x0089E4A8, 0), and only when the master reported two channels
 * (0x007843A6); native-run.log:15558. The twelve levels are recorded: they
 * are the downmix, not audio. */
static uint32_t m_SetOutputMatrix(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    const xa_obj *d = obj_of(c->a[0]);
    if (!d || d->type != T_MASTER || !d->alive || g_voice[d->rec].engine != v->engine)
        refuse(c, "destination %#010x is not this engine's live mastering voice, and the image "
                  "passes the one at [0x01AF0EDC]", c->a[0]);
    const sa2_xa2_voice *m = &g_voice[d->rec];
    if (c->a[1] != v->channels || c->a[2] != m->input_channels || c->a[1] * c->a[2] > 12u
        || c->a[3] != XA_LEVEL_MATRIX || c->a[4] != 0u)
        refuse(c, "(%u, %u, %#010x, %#x) against a %u-channel source and a %u-channel master; the "
                  "image passes (6, 2, 0x0089E4A8, 0)", c->a[1], c->a[2], c->a[3], c->a[4],
               v->channels, m->input_channels);
    for (uint32_t k = 0; k < c->a[1] * c->a[2]; k++) v->matrix[k] = pe32(c->a[3] + 4u * k);
    v->output = c->a[0];
    v->matrix_src = c->a[1];
    v->matrix_dst = c->a[2];
    v->matrix_calls++;
    c->answered = SA2_XA2_ANS_GUEST;
    return SA2_XA2_S_OK;
}

/* Start(Flags, OperationSet) and Stop(Flags, OperationSet): 0x00784437 and
 * 0x0078449D push (0, 0); native-run.log:15566 is the Start. FAudio's Start
 * (0x100069C0 in Wine's xaudio2_6.dll) only sets the voice active, so a
 * second Start leaves the anchor where the first put it. */
static uint32_t m_Start(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    if (c->a[0] != 0u || c->a[1] != 0u)
        refuse(c, "Start(%#x, %#x); the image passes (0, XAUDIO2_COMMIT_NOW)", c->a[0], c->a[1]);
    const uint64_t now = sa2_virtual_us();
    settle(c, v, c->vi, now);
    if (!v->running) {
        v->running = 1;
        anchor(v, now);
    }
    v->start_calls++;
    c->answered = SA2_XA2_ANS_GUEST;
    return SA2_XA2_S_OK;
}

static uint32_t m_Stop(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    if (c->a[0] != 0u || c->a[1] != 0u)
        refuse(c, "Stop(%#x, %#x); the image passes (0, XAUDIO2_COMMIT_NOW), and no "
                  "XAUDIO2_PLAY_TAILS", c->a[0], c->a[1]);
    const uint64_t now = sa2_virtual_us();
    settle(c, v, c->vi, now);
    v->running = 0;
    anchor(v, now);
    v->stop_calls++;
    c->answered = SA2_XA2_ANS_GUEST;
    return SA2_XA2_S_OK;
}

/* SubmitSourceBuffer(pBuffer, pBufferWMA). The one filling site, 0x007845C0,
 * builds a 36-byte XAUDIO2_BUFFER on its stack (0x007846F1..0x00784723):
 * Flags 0, AudioBytes = frames x 24, pAudioData one of its four ring slots,
 * every play and loop field 0, pContext 0, and no WMA buffer. So no buffer
 * ever carries XAUDIO2_END_OF_STREAM, which is what keeps SamplesPlayed a
 * plain running total. The audio bytes are named, never read. */
static uint32_t m_Submit(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    const uint32_t p = c->a[0];
    if (!p || c->a[1] != 0u)
        refuse(c, "(%#010x, %#010x); the image passes a buffer and no WMA buffer", p, c->a[1]);
    uint32_t b[9];
    for (uint32_t k = 0; k < 9u; k++) b[k] = pe32(p + 4u * k);
    /* Flags, AudioBytes, pAudioData, PlayBegin, PlayLength, LoopBegin,
     * LoopLength, LoopCount, pContext */
    if (b[0] || b[3] || b[4] || b[5] || b[6] || b[7] || b[8])
        refuse(c, "flags %#x, play %u+%u, loop %u+%u x%u, context %#010x; the image submits "
                  "every one of these as 0", b[0], b[3], b[4], b[5], b[6], b[7], b[8]);
    if (!b[1] || b[1] % v->block_align || !b[2])
        refuse(c, "AudioBytes %u at %#010x; the image submits whole frames of %u bytes, and "
                  "never an empty buffer (0x007846DF)", b[1], b[2], v->block_align);
    if (!sa2_guest_valid(b[2], b[1]))
        gfault(b[2], "a submitted audio buffer");
    const uint64_t now = sa2_virtual_us();
    settle(c, v, c->vi, now);
    if (g_qcount[c->vi] >= SA2_XA2_MAX_QUEUED)
        refuse(c, "a %uth queued buffer; the streaming thread tops the queue up to four "
                  "(0x00784797)", SA2_XA2_MAX_QUEUED + 1u);
    /* A running voice that had run dry starts on this buffer now. */
    if (v->running && !g_qcount[c->vi]) anchor(v, now);
    const uint32_t frames = b[1] / v->block_align;
    xa_qent *q = &g_q[c->vi][(g_qhead[c->vi] + g_qcount[c->vi]) % SA2_XA2_MAX_QUEUED];
    v->queued_end += frames;
    q->end = v->queued_end;
    q->frames = frames;
    q->bytes = b[1];
    q->data = b[2];
    q->context = b[8];
    g_qcount[c->vi]++;
    v->queued = g_qcount[c->vi];
    if (v->queued > v->max_queued) v->max_queued = v->queued;
    v->submits++;
    v->submitted_bytes += b[1];
    g_st.submits++;
    g_st.submitted_bytes += b[1];
    c->out0 = v->queued;
    c->answered = SA2_XA2_ANS_GUEST;
    return SA2_XA2_S_OK;
}

/* FlushSourceBuffers(). 0x007844A8, after Stop. FAudio's, decoded from
 * Wine's xaudio2_6.dll (FAudioSourceVoice_FlushSourceBuffers at 0x10005D10):
 * the head buffer survives only when the voice is active and the head has
 * started playing (its `newBuffer` byte, +0xC4, clear); otherwise every
 * buffer goes and the head's offset is reset. SamplesPlayed is not touched.
 * Here "has started playing" is "at least one of its frames consumed". */
static uint32_t m_Flush(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    settle(c, v, c->vi, sa2_virtual_us());
    uint32_t keep = 0;
    if (v->running && g_qcount[c->vi]) {
        const xa_qent *h = &g_q[c->vi][g_qhead[c->vi]];
        keep = v->played > h->end - h->frames ? 1u : 0u;
    }
    drop_queue(v, c->vi, keep);
    v->flush_calls++;
    c->answered = SA2_XA2_ANS_GUEST;
    return SA2_XA2_S_OK;
}

/* GetState(pVoiceState): one argument in 2.6, pushed at 0x0078478E. It writes
 * XAUDIO2_VOICE_STATE, 16 bytes: pCurrentBufferContext, BuffersQueued, and
 * SamplesPlayed as a UINT64 at +8. The streaming thread reads only
 * BuffersQueued ([esp+14h] at 0x00784790). */
static uint32_t m_GetState(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    const uint32_t p = c->a[0];
    if (!p) refuse(c, "a null XAUDIO2_VOICE_STATE; the image passes a stack slot");
    const uint64_t now = sa2_virtual_us();
    settle(c, v, c->vi, now);
    const uint32_t ctx = g_qcount[c->vi] ? g_q[c->vi][g_qhead[c->vi]].context : 0u;
    pk32(p + 0u, ctx);
    pk32(p + 4u, g_qcount[c->vi]);
    pk32(p + 8u, (uint32_t)v->played);
    pk32(p + 12u, (uint32_t)(v->played >> 32));
    v->getstate_calls++;
    v->last_queued = g_qcount[c->vi];
    v->last_samples = v->played;
    v->last_state_us = now;
    g_st.getstate_calls++;
    c->out0 = g_qcount[c->vi];
    c->out1 = (uint32_t)v->played;
    c->answered = SA2_XA2_ANS_CLOCK;
    return SA2_XA2_S_OK;
}

/* SetFrequencyRatio(Ratio, OperationSet). Its one site, 0x007845AF, is in the
 * stream object's vtable slot 5 (0x00784580), which no decoded call reaches:
 * every call through the stream pointer takes slots 0..4. It is answered
 * because the site exists. FAudio's answer is decoded from Wine's
 * xaudio2_6.dll: the ratio is clamped to [XAUDIO2_MIN_FREQ_RATIO,
 * MaxFrequencyRatio] and the call returns S_OK. The rate changes, so the voice
 * re-anchors: one that kept the old rate would desynchronise the game's own
 * timing, which is the failure this device exists to avoid. */
static uint32_t m_SetFrequencyRatio(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    if (c->a[1] != 0u)
        refuse(c, "OperationSet %u; the image passes XAUDIO2_COMMIT_NOW", c->a[1]);
    float r, lo, hi;
    const uint32_t lo_bits = F32_MIN_RATIO;
    memcpy(&r, &c->a[0], 4);
    memcpy(&lo, &lo_bits, 4);
    memcpy(&hi, &v->max_ratio_bits, 4);
    if (r != r)
        refuse(c, "a NaN ratio, which FAudio would store and this device cannot run a clock at");
    const float clamped = r > hi ? hi : (r > lo ? r : lo);
    const uint64_t now = sa2_virtual_us();
    settle(c, v, c->vi, now);
    memcpy(&v->ratio_bits, &clamped, 4);
    anchor(v, now);
    v->ratio_calls++;
    c->answered = SA2_XA2_ANS_GUEST;
    return SA2_XA2_S_OK;
}

/* DestroyVoice(), both kinds. The image destroys the source voice first
 * (0x007844B3, reached from the teardown at 0x0077EDF2) and the master after
 * (0x0077EE1A). XAudio2 will not destroy a voice another voice still sends
 * to, and the image never asks it to. */
static uint32_t m_DestroyVoice(xa_ctx *c) {
    sa2_xa2_voice *v = c->v;
    if (v->kind == SA2_XA2_VOICE_MASTER) {
        for (uint32_t i = 0; i < g_voice_used; i++)
            if (g_voice[i].kind == SA2_XA2_VOICE_SOURCE && !g_voice[i].destroyed
                && g_voice[i].output == c->self)
                refuse(c, "the mastering voice while source voice %#010x still sends to it; the "
                          "image destroys its source voice first", g_voice[i].addr);
        for (uint32_t i = 0; i < g_eng_used; i++)
            if (g_eng[i].master == c->self) g_eng[i].master = 0;
    } else {
        settle(c, v, c->vi, sa2_virtual_us());
        v->dropped_at_destroy = g_qcount[c->vi];
        g_qcount[c->vi] = 0;
        v->queued = 0;
        v->running = 0;
    }
    v->destroyed = 1;
    v->destroy_caller = c->caller;
    c->obj->alive = 0;
    g_st.voices_destroyed++;
    c->answered = SA2_XA2_ANS_GUEST;
    return 0;   /* void natively; the image reads nothing after it */
}

/* ------------------------------------------------------------ the tables
 *
 * Counts from Wine's xaudio2_6.dll, each method's own `ret`, and the image's
 * pushes wherever it calls the slot. Not `const`: `SA2_XAUDIO2_BREAK=argc`
 * patches GetState's count in place, so the count the dispatcher pops and the
 * count the probe publishes for `xaudio2_surface.py --argc` are one number. */
static xa_row ROWS_ENGINE[] = {
    {0,  2, XF_OUT, "QueryInterface", NULL},
    {1,  0, 0, "AddRef", NULL},
    {2,  0, 0, "Release", m_Release},
    {3,  1, XF_OUT | XF_HOST, "GetDeviceCount", NULL},
    {4,  2, XF_OUT | XF_HOST, "GetDeviceDetails", NULL},
    {5,  2, 0, "Initialize", m_Initialize},
    {6,  1, 0, "RegisterForCallbacks", NULL},
    {7,  1, 0, "UnregisterForCallbacks", NULL},
    {8,  7, XF_OUT, "CreateSourceVoice", m_CreateSourceVoice},
    {9,  7, XF_OUT, "CreateSubmixVoice", NULL},
    {10, 6, XF_OUT, "CreateMasteringVoice", m_CreateMasteringVoice},
    {11, 0, 0, "StartEngine", NULL},
    {12, 0, 0, "StopEngine", NULL},
    {13, 1, 0, "CommitChanges", NULL},
    {14, 1, XF_OUT, "GetPerformanceData", NULL},
    {15, 2, 0, "SetDebugConfiguration", NULL},
    {0, 0, 0, NULL, NULL},
};

/* IXAudio2Voice's nineteen slots begin both voice tables. The master's are
 * the whole of IXAudio2MasteringVoice in 2.6. */
static xa_row ROWS_MASTER[] = {
    {0,  1, XF_OUT | XF_HOST, "GetVoiceDetails", m_MasterGetVoiceDetails},
    {1,  1, 0, "SetOutputVoices", NULL},
    {2,  1, 0, "SetEffectChain", NULL},
    {3,  2, 0, "EnableEffect", NULL},
    {4,  2, 0, "DisableEffect", NULL},
    {5,  2, XF_OUT, "GetEffectState", NULL},
    {6,  4, 0, "SetEffectParameters", NULL},
    {7,  3, XF_OUT, "GetEffectParameters", NULL},
    {8,  2, 0, "SetFilterParameters", NULL},
    {9,  1, XF_OUT, "GetFilterParameters", NULL},
    {10, 3, 0, "SetOutputFilterParameters", NULL},
    {11, 2, XF_OUT, "GetOutputFilterParameters", NULL},
    {12, 2, 0, "SetVolume", NULL},
    {13, 1, XF_OUT, "GetVolume", NULL},
    {14, 3, 0, "SetChannelVolumes", NULL},
    {15, 2, XF_OUT, "GetChannelVolumes", NULL},
    {16, 5, 0, "SetOutputMatrix", NULL},
    {17, 4, XF_OUT, "GetOutputMatrix", NULL},
    {18, 0, 0, "DestroyVoice", m_DestroyVoice},
    {0, 0, 0, NULL, NULL},
};

static xa_row ROWS_SOURCE[] = {
    {0,  1, XF_OUT, "GetVoiceDetails", NULL},
    {1,  1, 0, "SetOutputVoices", NULL},
    {2,  1, 0, "SetEffectChain", NULL},
    {3,  2, 0, "EnableEffect", NULL},
    {4,  2, 0, "DisableEffect", NULL},
    {5,  2, XF_OUT, "GetEffectState", NULL},
    {6,  4, 0, "SetEffectParameters", NULL},
    {7,  3, XF_OUT, "GetEffectParameters", NULL},
    {8,  2, 0, "SetFilterParameters", NULL},
    {9,  1, XF_OUT, "GetFilterParameters", NULL},
    {10, 3, 0, "SetOutputFilterParameters", NULL},
    {11, 2, XF_OUT, "GetOutputFilterParameters", NULL},
    {12, 2, 0, "SetVolume", NULL},
    {13, 1, XF_OUT, "GetVolume", NULL},
    {14, 3, 0, "SetChannelVolumes", NULL},
    {15, 2, XF_OUT, "GetChannelVolumes", NULL},
    {16, 5, 0, "SetOutputMatrix", m_SetOutputMatrix},
    {17, 4, XF_OUT, "GetOutputMatrix", NULL},
    {18, 0, 0, "DestroyVoice", m_DestroyVoice},
    {19, 2, 0, "Start", m_Start},
    {20, 2, 0, "Stop", m_Stop},
    {21, 2, 0, "SubmitSourceBuffer", m_Submit},
    {22, 0, 0, "FlushSourceBuffers", m_Flush},
    {23, 0, 0, "Discontinuity", NULL},
    {24, 1, 0, "ExitLoop", NULL},
    {25, 1, XF_OUT | XF_CLK, "GetState", m_GetState},
    {26, 2, 0, "SetFrequencyRatio", m_SetFrequencyRatio},
    {27, 1, XF_OUT, "GetFrequencyRatio", NULL},
    {28, 1, 0, "SetSourceSampleRate", NULL},
    {0, 0, 0, NULL, NULL},
};

#define GETSTATE_SLOT 25u

static xa_type g_types[] = {
    {"IXAudio2",               ROWS_ENGINE, 0},
    {"IXAudio2MasteringVoice", ROWS_MASTER, 0},
    {"IXAudio2SourceVoice",    ROWS_SOURCE, 0},
};
_Static_assert(sizeof g_types / sizeof g_types[0] == SA2_XA2_MAX_TYPES,
               "one thunk block per type");

static uint32_t type_count(void) { return (uint32_t)(sizeof g_types / sizeof g_types[0]); }

static const xa_row *row_of(uint32_t t, uint32_t slot) {
    if (t >= type_count()) return NULL;
    for (const xa_row *p = g_types[t].rows; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

/* ------------------------------------------------------- the log */

static sa2_xa2_call      *g_cur;
static sa2_xa2_slot_stat *g_cur_slot;

static void bump(uint8_t answered, int by) {
    uint32_t *f;
    switch (answered) {
    case SA2_XA2_ANS_REFUSED: f = &g_st.refused;    break;
    case SA2_XA2_ANS_NOOP:    f = &g_st.noop;       break;
    case SA2_XA2_ANS_GUEST:   f = &g_st.from_guest; break;
    case SA2_XA2_ANS_PROFILE: f = &g_st.from_profile; break;
    case SA2_XA2_ANS_OBJECT:  f = &g_st.objects;    break;
    default:                  f = &g_st.clock;      break;
    }
    *f = (uint32_t)((int)*f + by);
}

/* Reserved before the handler runs and settled after it, as in
 * runtime/sa2_dsound.c: a handler that refuses never comes back, and the call
 * that stopped the run is the one a reader most wants in the log. */
static void note_reserve(uint32_t t, uint32_t slot, uint32_t self, uint32_t caller,
                         const uint32_t *argv, uint32_t argc) {
    g_seq++;
    g_st.method_calls++;
    bump(SA2_XA2_ANS_REFUSED, +1);
    g_cur_slot = NULL;
    if (t < SA2_XA2_MAX_TYPES && slot < SA2_XA2_MAX_SLOTS) {
        sa2_xa2_slot_stat *st = &g_slot[t][slot];
        if (!st->used) {
            st->used = 1;
            st->first_seq = g_seq;
            st->first_caller = caller;
            st->answered = SA2_XA2_ANS_REFUSED;
            g_st.distinct_methods++;
            g_cur_slot = st;
        }
        st->calls++;
    }
    g_cur = NULL;
    if (g_log_used >= SA2_XA2_LOG_MAX) {
        g_st.log_dropped++;
        return;
    }
    sa2_xa2_call *r = &g_log[g_log_used++];
    memset(r, 0, sizeof *r);
    r->seq = g_seq;
    r->caller = caller;
    r->self = self;
    r->virtual_us = sa2_virtual_us();
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->answered = SA2_XA2_ANS_REFUSED;
    r->thread = (uint8_t)sa2_sched_current();
    r->argc = (uint8_t)(argc > 8u ? 8u : argc);
    for (uint32_t k = 0; k < r->argc; k++) r->arg[k] = argv[k];
    g_cur = r;
}

static void note_settle(uint32_t ret, const xa_ctx *c) {
    bump(SA2_XA2_ANS_REFUSED, -1);
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

/* -------------------------------------------------------- the native */

static const char *type_name_of(uint32_t t) { return t < type_count() ? g_types[t].name : "?"; }

static void *xaudio2_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_XA2_THUNK_BASE) / SA2_XA2_THUNK_STRIDE;
    const uint32_t t = idx / SA2_XA2_MAX_SLOTS, slot = idx % SA2_XA2_MAX_SLOTS;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    if (t >= type_count()) {
        sa2_trap_note("xaudio2 method thunk %#010x is past the %u fabricated interfaces",
                      pc, type_count());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const xa_row *row = row_of(t, slot);
    uint32_t raw[16];
    memset(raw, 0, sizeof raw);
    /* An unnamed slot is never popped with a guessed count; `this` is still
     * read, because naming the object in the trap is the point of getting here. */
    const uint32_t want = row ? (uint32_t)row->argc + 1u : 1u;
    for (uint32_t k = 0; k < want && k < 16u; k++) {
        if (sa2_peek(esp + 4u + 4u * k, 4, &raw[k]) != SA2_OK) {
            sa2_trap_note("xaudio2 %s::%s: argument %u at %#010x is outside the guest space",
                          g_types[t].name, row ? row->name : "an unnamed slot", k,
                          esp + 4u + 4u * k);
            sa2_trap_raise(SA2_TRAP_FAULT, esp + 4u + 4u * k);
        }
    }
    const uint32_t self = raw[0];

    if (!row) {
        note_reserve(t, slot, self, caller, raw + 1, 0);
        sa2_trap_note("xaudio2 %s vtable slot %u called from %#010x on %#010x: 2.6 has no such "
                      "slot, and popping a guessed argument count would corrupt the caller's "
                      "frame", g_types[t].name, slot, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    if (!row->fn) {
        note_reserve(t, slot, self, caller, raw + 1, row->argc);
        sa2_trap_note("xaudio2 %s::%s called from %#010x on %#010x: the image has no call site "
                      "for this slot, so this device does not answer it", g_types[t].name,
                      row->name, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    xa_ctx c;
    memset(&c, 0, sizeof c);
    c.s = s;
    c.pc = pc;
    c.type = t;
    c.row = row;
    c.self = self;
    c.a = raw + 1;
    c.caller = caller;
    c.answered = (row->flags & XF_CLK) ? SA2_XA2_ANS_CLOCK : SA2_XA2_ANS_NOOP;
    note_reserve(t, slot, self, caller, raw + 1, row->argc);
    c.obj = obj_of(self);
    /* The thunk says which interface was called through; `this` has to be an
     * object of that interface this device made, and still alive. A handler
     * with no record to answer from would have to invent one. */
    if (!c.obj || c.obj->type != t) {
        g_st.unknown_this++;
        refuse(&c, "`this` %#010x is not an %s this device made", self, type_name_of(t));
    }
    if (!c.obj->alive) {
        g_st.dead_this++;
        refuse(&c, "`this` %#010x was already %s", self,
               t == T_ENGINE ? "released" : "destroyed");
    }
    if (t == T_ENGINE) c.e = &g_eng[c.obj->rec];
    else { c.vi = c.obj->rec; c.v = &g_voice[c.vi]; }
    const uint32_t ret = row->fn(&c);
    note_settle(ret, &c);
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    /* The callee pops: the return address plus `this` plus the arguments. */
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * want);
    return m;
}

/* ---------------------------------------------------------- the build */

static uint32_t type_vtable(uint32_t t) {
    if (g_types[t].vt) return g_types[t].vt;
    if (!g_types_bound) {
        /* Every slot of every type gets a function-table row, so an unnamed
         * slot lands in this runtime and refuses by name rather than jumping
         * to a value nothing registered. Registered on the first object and
         * not in config_from_env, so binding the imports adds no rows. */
        for (uint32_t i = 0; i < type_count(); i++)
            for (uint32_t k = 0; k < SA2_XA2_MAX_SLOTS; k++) {
                const uint32_t va = SA2_XA2_THUNK_BASE +
                                    (i * SA2_XA2_MAX_SLOTS + k) * SA2_XA2_THUNK_STRIDE;
                if (sa2_ft_add(va, xaudio2_native, "xaudio2-method", SA2_FN_NATIVE) < 0) {
                    sa2_trap_note("the function table refused the xaudio2 method thunks");
                    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_XA2_THUNK_BASE);
                }
            }
        g_types_bound = 1;
    }
    const uint32_t vt = SA2_XA2_VT_BASE + t * SA2_XA2_VT_STRIDE;
    for (uint32_t k = 0; k < SA2_XA2_MAX_SLOTS; k++)
        pk32(vt + 4u * k, SA2_XA2_THUNK_BASE + (t * SA2_XA2_MAX_SLOTS + k) * SA2_XA2_THUNK_STRIDE);
    g_types[t].vt = vt;
    return vt;
}

/* A refusal of the CoCreateInstance call itself. It is raised at the
 * ole32!CoCreateInstance import's own thunk, the call being answered, as
 * import_native raises its own refusals; SA2_XA2_THUNK_BASE would name
 * IXAudio2::QueryInterface, a method of an object that does not exist yet.
 * The import is bound whenever this runs through it; the region's base stands
 * in otherwise, as imp_ExitThread's does. */
static void cocreate_refuse(uint32_t caller, const char *fmt, ...)
    __attribute__((noreturn, format(printf, 2, 3)));
static void cocreate_refuse(uint32_t caller, const char *fmt, ...) {
    char why[SA2_TRAP_NOTE_MAX];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(why, sizeof why, fmt, ap);
    va_end(ap);
    sa2_trap_note("xaudio2 CoCreateInstance from %#010x: %s", caller, why);
    const sa2_import_info *imp = sa2_import_by_name("ole32.dll", "CoCreateInstance");
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, imp ? imp->thunk : SA2_GUEST_IMPORT_BASE);
}

/* The one creation site is 0x007840F0:
 *
 *     007840f5  push 902210h      ; riid: IID_IXAudio2 (2.7 and earlier)
 *     007840fa  push 1            ; CLSCTX_INPROC_SERVER
 *     007840fc  push 0            ; pUnkOuter
 *     007840fe  push 902200h      ; rclsid: XAudio2 2.6
 *     00784103  call [CoCreateInstance]
 *     0078410b  jl 0078413Ch      ; failure: return the HRESULT
 *
 * Native: native-run.log:15513 is this call, :15544 "Created XAudio version
 * 26: 02938AB8". The engine comes back with one reference (the class
 * factory's own QueryInterface, AddRef and Release at :15541-15543 are Wine's,
 * not the image's), which the image's one Release per engine gives back. */
uint32_t sa2_xaudio2_cocreate(void *s, const uint8_t clsid[16], const uint8_t iid[16],
                              uint32_t ppv, uint32_t *handled) {
    if (handled) *handled = 0;
    if (!clsid || memcmp(clsid, CLSID_XAUDIO2_26, 16)) return 0;
    if (handled) *handled = 1;
    g_st.cocreate_calls++;
    if (!g_enabled || g_break == BRK_REFUSE) {
        /* Configuration refuses a capture's profile with the device off. One
         * loaded after this module's configuration still reaches here, and the
         * answer below would approximate that capture, so it traps instead. */
        if (!sa2_devprofile_is_default()) {
            uint32_t caller = 0;
            if (s) (void)sa2_peek(sa2_rt_get_gpr(s, SA2_ESP), 4, &caller);
            cocreate_refuse(caller, "the class is refused under device profile %s, a capture's, "
                                    "whose game had this engine", sa2_devprofile_source());
        }
        /* Today's answer, unchanged: no class, and a null out-pointer. */
        g_st.cocreate_refused++;
        if (ppv) pk32(ppv, 0u);
        return sa2_import_refused("ole32.dll", "CoCreateInstance", SA2_XA2_REGDB_E_CLASSNOTREG);
    }
    uint32_t caller = 0, outer = 0, ctx = 0, ppv_on_stack = 0;
    if (s) {
        const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
        caller = pe32(esp);
        outer = pe32(esp + 8u);
        ctx = pe32(esp + 12u);
        ppv_on_stack = pe32(esp + 20u);
    }
    if (!s || ppv_on_stack != ppv)
        cocreate_refuse(caller, "called outside the CoCreateInstance import frame (ppv %#010x, "
                                "stack %#010x), so pUnkOuter and dwClsContext cannot be read",
                        ppv, ppv_on_stack);
    if (outer != 0u || ctx != 1u || !ppv)
        cocreate_refuse(caller, "(outer %#010x, context %#x, ppv %#010x); the image passes (NULL, "
                                "CLSCTX_INPROC_SERVER, &local)", outer, ctx, ppv);
    if (!iid || memcmp(iid, IID_IXAUDIO2_27, 16))
        cocreate_refuse(caller, "an interface other than IID_IXAudio2 {8BCF1F58-...}, the only "
                                "one the image asks XAudio2 2.6 for");
    if (g_eng_used >= SA2_XA2_MAX_ENGINES)
        cocreate_refuse(caller, "the engine records (%u) are exhausted", SA2_XA2_MAX_ENGINES);
    sa2_xa2_engine *e = &g_eng[g_eng_used];
    memset(e, 0, sizeof *e);
    const xa_obj *o = obj_new(T_ENGINE, g_eng_used);
    g_eng_used++;
    e->addr = o->addr;
    e->create_caller = caller;
    e->refs = 1;
    g_st.engines++;
    pk32(ppv, o->addr);
    return SA2_XA2_S_OK;
}

int sa2_xaudio2_device_enabled(void) { return g_enabled; }
const char *sa2_xaudio2_break_name(void) { return BREAK_NAMES[g_break]; }

void sa2_xaudio2_config_from_env(void) {
    const char *v = getenv("SA2_XAUDIO2_DEVICE");
    g_enabled = v && *v && *v != '0';
    /* Restore the published count first, so this is idempotent. */
    for (xa_row *r = ROWS_SOURCE; r->name; r++) if (r->slot == GETSTATE_SLOT) r->argc = 1u;
    g_break = BRK_NONE;
    v = getenv("SA2_XAUDIO2_BREAK");
    if (v && *v) {
        int found = 0;
        for (int k = 1; k < (int)(sizeof BREAK_NAMES / sizeof BREAK_NAMES[0]); k++)
            if (!strcmp(v, BREAK_NAMES[k])) { g_break = k; found = 1; }
        if (!found) {
            sa2_trap_note("SA2_XAUDIO2_BREAK=%s is not a break this runtime has; the names are "
                          "refuse, noconsume, instant and argc", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_XA2_THUNK_BASE);
        }
        if (g_break == BRK_ARGC)
            for (xa_row *r = ROWS_SOURCE; r->name; r++) if (r->slot == GETSTATE_SLOT) r->argc = 2u;
    }
    /* A capture's profile with the class refused: its game had this engine
     * (both readers refuse `xaudio2_master voice=0`), and without it the guest
     * builds the fallback output object 0x00785220 native never builds. That
     * approximates the capture's device set, so the profile is refused here,
     * before the guest starts (sa2_xaudio2.h, "The answer that describes the
     * host"). The default profile with the device off is the before
     * measurement, and stays legal. Configuration has no guest pc, so the trap
     * names the module by its thunk block, as the break check above does. */
    if (!sa2_devprofile_is_default() && (!g_enabled || g_break == BRK_REFUSE)) {
        sa2_trap_note("xaudio2: %s under device profile %s, a capture's, whose game had an "
                      "XAudio2 engine; refused, not approximated",
                      g_enabled ? "SA2_XAUDIO2_BREAK=refuse refuses the class"
                                : "SA2_XAUDIO2_DEVICE is off",
                      sa2_devprofile_source());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_XA2_THUNK_BASE);
    }
}

/* ------------------------------------------------------------ readers */

void sa2_xaudio2_stats_get(sa2_xa2_stats *out) {
    if (!out) return;
    *out = g_st;
    /* Where every source voice is now, read without settling anything. */
    out->frames_played = 0;
    for (uint32_t i = 0; i < g_voice_used; i++) {
        uint64_t played = 0;
        if (sa2_xaudio2_voice_now(i, NULL, &played)) out->frames_played += played;
    }
}
uint32_t            sa2_xaudio2_log_count(void) { return g_log_used; }
const sa2_xa2_call *sa2_xaudio2_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

uint32_t    sa2_xaudio2_type_count(void) { return type_count(); }
const char *sa2_xaudio2_type_name(uint32_t t) { return t < type_count() ? g_types[t].name : NULL; }
const char *sa2_xaudio2_method_name(uint32_t t, uint32_t slot) {
    const xa_row *r = row_of(t, slot);
    return r ? r->name : NULL;
}
int sa2_xaudio2_method_argc(uint32_t t, uint32_t slot) {
    const xa_row *r = row_of(t, slot);
    return r ? (int)r->argc : -1;
}
int sa2_xaudio2_method_implemented(uint32_t t, uint32_t slot) {
    const xa_row *r = row_of(t, slot);
    return r && r->fn ? 1 : 0;
}
const char *sa2_xaudio2_method_flags(uint32_t t, uint32_t slot) {
    static char buf[32];
    const xa_row *r = row_of(t, slot);
    buf[0] = 0;
    if (!r) return buf;
    if (r->flags & XF_OUT) strcat(buf, "out");
    if (r->flags & XF_CLK) { if (buf[0]) strcat(buf, ","); strcat(buf, "clock"); }
    if (r->flags & XF_HOST) { if (buf[0]) strcat(buf, ","); strcat(buf, "host"); }
    return buf;
}
const sa2_xa2_slot_stat *sa2_xaudio2_slot_stat_at(uint32_t t, uint32_t slot) {
    if (t >= SA2_XA2_MAX_TYPES || slot >= SA2_XA2_MAX_SLOTS) return NULL;
    return &g_slot[t][slot];
}

uint32_t              sa2_xaudio2_engine_count(void) { return g_eng_used; }
const sa2_xa2_engine *sa2_xaudio2_engine_at(uint32_t i) { return i < g_eng_used ? &g_eng[i] : NULL; }
uint32_t              sa2_xaudio2_voice_count(void) { return g_voice_used; }
const sa2_xa2_voice  *sa2_xaudio2_voice_at(uint32_t i) {
    return i < g_voice_used ? &g_voice[i] : NULL;
}

int sa2_xaudio2_voice_now(uint32_t i, uint32_t *queued, uint64_t *samples_played) {
    if (i >= g_voice_used || g_voice[i].kind != SA2_XA2_VOICE_SOURCE) return 0;
    /* Read-only: a probe that settled the voice would change what it measures. */
    xa_ctx c;
    memset(&c, 0, sizeof c);
    c.type = T_SOURCE;
    c.pc = SA2_XA2_THUNK_BASE;
    const uint64_t pos = voice_position(&c, &g_voice[i], i, sa2_virtual_us());
    if (queued) *queued = g_voice[i].destroyed ? 0u : queued_after(i, pos);
    if (samples_played) *samples_played = pos;
    return 1;
}
