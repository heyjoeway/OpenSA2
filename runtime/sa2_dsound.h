/* A recording no-op DirectSound 8 device.
 *
 * Design 5 S4's work list asks for "presentation stubs as no-ops that record
 * calls"; design 3.5 puts `dsound` in group (b), presentation, and gives S5
 * "dsound stubs to PCM streams", i.e. real audio. **This is the S4 half and
 * only the S4 half**: `DirectSoundCreate8` hands back a real object in guest
 * memory with a synthetic vtable, every method call is recorded, and each
 * method returns the least committal success that lets the guest walk on.
 *
 * **Nothing here is audible.** No host audio device is opened, no sample ever
 * leaves the process, and the only bytes that move are the guest's own: a
 * `Lock` hands back guest memory, so what the guest writes into a sound buffer
 * stays in guest memory and can be read afterwards. Making those bytes into
 * sound is S5's.
 *
 * Why the previous refusal had to go
 * ----------------------------------
 * `DirectSoundCreate8` returned E_FAIL on purpose, and the guest treats that
 * as a warning rather than an error -- but the *branch* it takes on the way
 * out skips more than sound. S4 slice 10 traced it: 0x00861260 is the sound
 * init, and CRI's file-system setup (0x007C5270, at 0x008613BF) sits past the
 * failure return at 0x008612AD. With no file system, no CRIFS worker thread
 * exists, a queued partition read is never started, and the guest polls
 * `adxf_GetPtStat` forever. The refusal was five call sites away from the
 * livelock.
 *
 * The three rules of runtime/sa2_d3d9.c, which this file also keeps
 * -----------------------------------------------------------------
 *  1. **Every slot this file answers is named and carries its own argument
 *     count**, and those counts are checked against the dwords the image
 *     actually pushes (`tools/s4/dsound_surface.py --argc`). A slot with no
 *     row is refused by name and never popped with a guessed count: a guessed
 *     pop corrupts the caller's frame rather than answering wrongly, which is
 *     the defect class this project has now been bitten by twice (d3d9 at
 *     slice 6, Steam at slice 9).
 *  2. **A method with an out-parameter and no handler refuses**, because
 *     answering DS_OK and writing nothing hands the guest whatever was on its
 *     stack. `DF_OUT` marks those rows and the dispatcher enforces it.
 *  3. **Every fabricated answer is labelled.** `invented` is a value this
 *     runtime made up; `from-guest` is a value the guest itself supplied;
 *     `clock` is the one class d3d9 did not need -- a value derived from the
 *     virtual clock and the buffer's own declared format. A reader of the log
 *     can always tell which of the three a number is.
 *
 * The cursors, which are the part most easily got subtly wrong
 * ------------------------------------------------------------
 * A polling guest that sees a frozen play cursor spins forever; one that sees
 * a cursor moving at the wrong rate desynchronises its own timing against the
 * frame clock. So the cursors are derived, exactly:
 *
 *     elapsed_us = sa2_virtual_us() - anchor_us            (uint64, virtual)
 *     advance    = elapsed_us * nAvgBytesPerSec / 1000000  (uint64, floored)
 *     pos        = anchor_pos + advance
 *     looping    ? pos %= dwBufferBytes
 *                : if (pos >= dwBufferBytes) { pos = last whole block; stop; }
 *     pos       -= pos % nBlockAlign                       (never mid-sample)
 *
 * Five properties, each of which a guest can tell apart from its neighbour:
 *
 *  - **64-bit arithmetic throughout.** `elapsed_us * nAvgBytesPerSec` at CD
 *    rates passes 2^32 after about 24 seconds of virtual time, so a 32-bit
 *    intermediate would wrap inside a single loading screen.
 *  - **Floored division**, never floating point: the same input gives the same
 *    byte on every host and in every replay.
 *  - **Rounded down to `nBlockAlign`.** A cursor landing mid-sample is a value
 *    no real device reports, and guest code that divides it to get a sample
 *    index would be wrong in a way that is very hard to trace.
 *  - **A fixed, block-aligned write-cursor lead**, computed once per buffer
 *    when its format is known and not re-derived per call -- a lead that moved
 *    from call to call would let a guest see the write cursor go backwards.
 *  - **A non-looping buffer halts at its end** and its DSBSTATUS_PLAYING bit
 *    clears. It does not wrap and it does not run past the end.
 *
 * `Play`, `Stop` and `SetCurrentPosition` each re-anchor: the cursor is
 * elapsed-since-anchor, never elapsed-since-process-start.
 *
 * The clock is `sa2_virtual_us()`, the same one `Present` advances at the
 * frame boundary. Nothing here reads host time.
 *
 * Environment
 * -----------
 *   SA2_DSOUND_DEVICE=1        DirectSoundCreate8 returns this recording
 *                              device (default: it returns E_FAIL and writes a
 *                              null out-pointer, exactly as before this file)
 *   SA2_DSOUND_STRICT_CAPS=1   every fabricated capability answer refuses
 *                              instead, so the invented list is shown to be
 *                              load-bearing rather than asserted to be
 *   SA2_DSOUND_WRITE_LEAD_US=n the write cursor's fixed lead over the play
 *                              cursor, in microseconds (default 15000)
 *   SA2_DSOUND_PRIMARY_MS=n    the primary buffer's named length in ms; a
 *                              primary buffer's desc carries dwBufferBytes 0
 *                              and a real device chooses the size (default 500)
 *   SA2_DSOUND_BREAK=<name>    argc | frozen | noout | nolead | noreuse |
 *                              sharefree | sharefree-noguard; see the source
 */
#ifndef SA2_RUNTIME_DSOUND_H
#define SA2_RUNTIME_DSOUND_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Synthetic method thunks. Above sa2_d3d9.h's region (0x00210000 ..
 * 0x0021C000) and below the image at 0x00400000, so every synthetic entry
 * point this runtime invents is identifiable by its address alone. */
#define SA2_DS_THUNK_BASE   0x00220000u
#define SA2_DS_THUNK_STRIDE 16u
#define SA2_DS_MAX_TYPES    8u
#define SA2_DS_MAX_SLOTS    32u    /* IDirectSoundBuffer8 has 24 */
#define SA2_DS_THUNK_END    (SA2_DS_THUNK_BASE + \
                             SA2_DS_MAX_TYPES * SA2_DS_MAX_SLOTS * SA2_DS_THUNK_STRIDE)

/* Fabricated objects and their vtables, in guest scratch, clear of
 * sa2_iface.h's 0x03100000 block and sa2_d3d9.h's 0x03200000..0x03243000. */
#define SA2_DS_OBJ_BASE     0x03250000u
#define SA2_DS_OBJ_STRIDE   64u
#define SA2_DS_MAX_OBJECTS  512u
#define SA2_DS_VT_BASE      0x03260000u
#define SA2_DS_VT_STRIDE    (SA2_DS_MAX_SLOTS * 4u)
/* Backing stores the device keeps, held or free (runtime/sa2_dsound.c, "the backing
 * stores"): a store is handed on to the next buffer of its length after its last
 * holder's final Release, so this bounds the distinct blocks, not the buffers made.
 * The recorder keeps rows for 512 of them (host/sa2_present_rec.c, AU_STORES, which
 * says what happens past that and why SA2 cannot get there). */
#define SA2_DS_MAX_STORES   1024u

/* HRESULTs this file returns, by their published values. */
#define SA2_DS_OK                    0x00000000u
#define SA2_DSERR_ALREADYINITIALIZED 0x88780005u
#define SA2_DSERR_INVALIDCALL        0x88780032u
#define SA2_DSERR_INVALIDPARAM       0x80070057u
#define SA2_DSERR_NOINTERFACE        0x80004002u
#define SA2_DSERR_UNSUPPORTED        0x80004001u
#define SA2_DSERR_OUTOFMEMORY        0x8007000Eu
#define SA2_DSERR_FAIL               0x80004005u

/* How an answer was arrived at. The distinction is the point of the log. */
enum {
    SA2_DS_ANS_REFUSED = 0,   /* recorded, then trapped by name */
    SA2_DS_ANS_NOOP    = 1,   /* recorded; nothing to answer, DS_OK */
    SA2_DS_ANS_GUEST   = 2,   /* answered from something the guest supplied */
    SA2_DS_ANS_INVENT  = 3,   /* answered with a value this runtime invented */
    SA2_DS_ANS_OBJECT  = 4,   /* answered by fabricating an object */
    SA2_DS_ANS_UNSET   = 5,   /* a Get with no matching Set; the documented default */
    SA2_DS_ANS_CLOCK   = 6,   /* derived from the virtual clock and the declared format */
};

#define SA2_DS_LOG_MAX 4096u

typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t self;
    uint64_t virtual_us;        /* the clock this call saw, so a cursor is checkable */
    uint16_t type;
    uint16_t slot;
    uint8_t  answered;
    uint8_t  argc;
    uint32_t arg[8];
    uint32_t ret;
    uint32_t out0, out1;        /* the two dwords a Get wrote, 0 when it wrote none */
} sa2_ds_call;

/* One row per (interface, slot) ever called, so the *counts* are exact even
 * when the ordered log fills. This is the dynamic method surface. */
typedef struct {
    uint32_t calls;
    uint32_t first_seq;
    uint32_t first_caller;
    uint8_t  answered;
    uint8_t  used;
} sa2_ds_slot_stat;

/* One row per sound buffer the guest created, kept after a Release so a run
 * that ends mid-stream still says what existed. Once every one of the
 * SA2_DS_MAX_OBJECTS object slots has been used, a new buffer takes the slot and
 * the row of the buffer whose final Release is oldest, of those no call reached
 * after it (runtime/sa2_dsound.c, obj_new), so from then on a row describes the
 * latest buffer to hold it. `mem` stays what the buffer held after its final
 * Release, and by then the device may have handed that block to another buffer
 * of the same length. */
typedef struct {
    uint32_t addr;              /* the guest pointer the guest was handed */
    uint32_t create_caller;
    uint32_t flags;             /* DSBUFFERDESC.dwFlags, as the guest passed it */
    uint32_t bytes;             /* dwBufferBytes; for a primary, the named length */
    uint32_t primary;
    uint32_t format_tag, channels, samples_per_sec, avg_bytes_per_sec;
    uint32_t block_align, bits_per_sample;
    uint32_t format_from_guest; /* 1 when SetFormat or the desc supplied it */
    uint32_t write_lead;        /* the fixed, block-aligned lead, in bytes */
    uint32_t playing, looping;
    uint32_t play_calls, stop_calls, lock_calls, unlock_calls, setpos_calls;
    uint32_t getpos_calls;
    uint32_t mem, mem_len;      /* the guest bytes a Lock hands back */
    uint64_t anchor_us;         /* virtual µs at the last Play/Stop/SetCurrentPosition */
    uint32_t anchor_pos;        /* the cursor's byte offset at that instant */
    uint32_t last_play, last_write;   /* the cursors the last GetCurrentPosition gave */
    uint64_t last_read_us;            /* the virtual instant of that reading */
    uint64_t bytes_locked;      /* guest bytes handed out through Lock */
    uint32_t halted;            /* a non-looping buffer that reached its end */
    uint32_t released;
} sa2_ds_buffer;

typedef struct {
    uint32_t created;           /* DirectSoundCreate8 calls answered with an object */
    uint32_t method_calls;
    uint32_t refused, noop, from_guest, invented, objects, unset, clock;
    uint32_t objects_built;
    uint32_t distinct_methods;
    uint32_t buffers;           /* CreateSoundBuffer calls answered */
    uint32_t primary_buffers;
    uint32_t strict_caps;
    uint32_t guest_bytes_allocated;
    uint32_t cooperative_level;
    uint32_t qi_answered, qi_declined;
    uint32_t write_lead_us;
    /* Method calls whose `this` is not an object this runtime fabricated.
     * The same continuous check sa2_d3d9.h carries, and with the same caveat
     * recorded there: the guest often loads `this` from a register rather than
     * from a drifted stack, so this staying at zero is *reported*, not an
     * assertion that the argument counts are right. The argument counts are
     * checked against the image by tools/s4/dsound_surface.py --argc. */
    uint32_t unknown_this;
    /* Cursor reads that came back lower than the previous read of the same
     * buffer and could not have got there by wrapping. A play cursor that goes
     * backwards is the single most confusing thing a device can show a
     * streaming decoder, and this counts it where it happens.
     *
     * "Could not have got there by wrapping" is computed, not guessed. The
     * first version of this asked whether the previous reading was within a
     * quarter of a second of the end, and **it fired on a legitimate wrap**:
     * the probe reads a looping buffer at 41 376, jumps the virtual clock by
     * thirty seconds and reads 0, which is 46 824 bytes of travel plus 339
     * whole laps. A diagnostic that fires on the thing it exists to bless is
     * worse than none, so the rule is now the exact one -- the bytes that
     * could have been played since the previous reading against the bytes a
     * wrap would have had to cover. */
    uint32_t cursor_went_backwards;
    uint32_t cursor_reads, cursor_wraps;
    /* Reuse (runtime/sa2_dsound.c, "the backing stores" and obj_new). Locks that were
     * handed a block a released buffer had held, objects made in a released buffer's
     * slot, stores whose last holder went with a Lock open (never handed out again),
     * and method calls on a buffer after its final Release -- the use after free that
     * reuse could turn into a call on another buffer, counted where it happens; a
     * buffer it reaches keeps its slot for good. `stores_reused_dirty` counts the
     * reused blocks that were not all zero when handed on, which the next holder
     * sees as they were. `guest_bytes_allocated` above counts fresh blocks only:
     * the arena this device has taken. */
    uint32_t stores_reused, objects_reused, stores_kept_locked, calls_on_released;
    uint32_t stores_reused_dirty;
} sa2_ds_stats;

void sa2_dsound_config_from_env(void);
int  sa2_dsound_device_enabled(void);
/* dsound.dll!DirectSoundCreate8(pcGuidDevice, ppDS8, pUnkOuter): the guest
 * pointer to a fabricated IDirectSound8, or 0 when the device is not enabled
 * (the import then answers E_FAIL, as it did before this file existed). */
uint32_t sa2_dsound_make_device(uint32_t caller);

void sa2_dsound_stats_get(sa2_ds_stats *out);
uint32_t           sa2_dsound_log_count(void);
const sa2_ds_call *sa2_dsound_log_at(uint32_t i);

uint32_t sa2_dsound_type_count(void);
const char *sa2_dsound_type_name(uint32_t type);
const char *sa2_dsound_method_name(uint32_t type, uint32_t slot);   /* NULL when unnamed */
int         sa2_dsound_method_argc(uint32_t type, uint32_t slot);   /* -1 when unnamed */
int         sa2_dsound_method_implemented(uint32_t type, uint32_t slot);
const char *sa2_dsound_method_flags(uint32_t type, uint32_t slot);
const sa2_ds_slot_stat *sa2_dsound_slot_stat_at(uint32_t type, uint32_t slot);

uint32_t sa2_dsound_buffer_count(void);
const sa2_ds_buffer *sa2_dsound_buffer_at(uint32_t i);
/* The play and write cursors a GetCurrentPosition would return *now*, without
 * recording a call. For the probe's own end-of-run snapshot. */
int sa2_dsound_buffer_cursors(uint32_t i, uint32_t *play, uint32_t *write);

#ifdef __cplusplus
}
#endif
#endif
