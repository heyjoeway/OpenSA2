/* Harness level 2, lifted side: the Mod Loader's OnFrame, at the same point.
 *
 * The Mod Loader replaces every exit of GameLoop (0x00433EE0) with a jump to
 * its OnFrame dispatcher, which runs the mods' OnFrame and then performs the
 * exit (SA2ModLoader/Events.cpp, InitOnFrame). This file is that point in the
 * lifted build: linked with `-Wl,-wrap=sub_433ee0` (one dash; the pinned zig
 * refuses `--wrap`), every call to GameLoop -- lifted WinMain's direct call and
 * the function-table row alike -- lands here, runs the lifted body, and records
 * one row after it returns.
 *
 * The state is the same on both sides because the epilogue the Mod Loader
 * reproduces only pops callee-saved registers. One difference is known: the
 * native OnFrame runs on the guest stack below ESP, so it overwrites dead stack
 * memory the lifted build leaves alone. native_tick_probe.c's STACK_PAD is the
 * control for whether the game ever reads that memory.
 *
 * The capture is opened on the first *entry*, before the body runs, so a run
 * that traps inside the first call leaves a header and no rows -- "the run
 * ended at tick 1" -- rather than no file, which reads as "the hook never ran".
 *
 * SA2_REPLAY_TICKS=<path> turns it on; unset, the wrapper is a pass-through.
 * Its row keeps the digest columns (tick_digest.h), computed here over the
 * guest's memory: with the word log below on, they are the cross-check the
 * host's recompute from the log is held to (tools/s4/wordlog.py check_capture).
 *
 * SA2_STAGE_TRACE=<path> also writes, per tick, what M6's "booting" is judged
 * on (tools/s4/stage_boot.py): CurrentLevel, GameMode, GameState, the in-game
 * frame counter and the first player's object with its position. The names and addresses are the Mod
 * Loader's (SA2ModLoader/include/SA2Variables.h, SA2Structs.h). Lifted side
 * only: it is the recompiled game's own state being judged, not a comparison.
 *
 * SA2_REPLAY_WORDS=<path> also logs every word of `.dataa` at every tick: the
 * writer the native tick probe runs (word_log_core.h), in its format, so one
 * reader (tools/s4/wordlog.py) reads both sides and computes the digest columns
 * of any mask from either (private/generated/s4/wordobs/reports/judge-plan.md,
 * "Lifted side"). It replaces SA2_REPLAY_SNAPSHOTS, which wrote the section
 * every 100th tick and is retired. A tick is a GameLoop return, the count the
 * row, the stage trace and wrap_input_replay.c key by, and the scan runs after
 * the row, so tick t's words are the ones the row's digests read. Where it
 * differs from the native probe, and why:
 *
 *   - It streams: each record is written out as it is made (wl_drained), and
 *     the end record, reason on_exit, is written when the process exits
 *     (atexit; crt_probe.c's main returns after every run, the ones that end
 *     in a trap too). A lifted run can end inside GameLoop, where no last call
 *     comes. A run that dies without exiting (runtime/sa2_trap.c's _exit(3)
 *     outside any call), that exits while a tick's record is half made (a
 *     trap inside the scan itself), or in which GameLoop never returned (a
 *     log of no tick compares nothing, and natively only the planned end
 *     refuses one) leaves no end record, which the reader refuses. The end
 *     record reads no guest memory: harness/crt_probe.c's main has unmapped
 *     the guest (sa2_runtime_shutdown) before the process exits, which is
 *     also why the lifted log carries no end-of-run check (wl_verify reads
 *     the section); the row's digest columns are the lifted cross-check.
 *     Natively the log leaves the process once because an
 *     open file shifted the game's handle numbers; here the file is the host's:
 *     the runtime numbers the guest's handles itself and its time is virtual,
 *     so the lifted tick capture is the same bytes with the log and without
 *     it, which is what shows the log did not move the run.
 *   - The keyframe is taken at GameLoop's first entry, where the capture is
 *     opened, and natively at the Mod Loader's Init: the two sides' tick 0 are
 *     different instants, and every tick from 1 is the state at a GameLoop
 *     return on both. So states compare from tick 1 (wordlog.py diff), but
 *     tick 1's changes span different intervals on the two sides and are not
 *     comparable (touched, changes and stats count them).
 *   - No region maps and no SA2O: which words hold addresses is decided from
 *     native evidence alone, and the log's blocks are host memory, outside the
 *     guest's space.
 *   - The clock is the time-stamp counter, as natively; the reference beside
 *     it at the start and the end is CLOCK_MONOTONIC in nanoseconds.
 * If the log cannot be kept (the file does not open, or its memory is not
 * there), the wrapper says so on stderr and leaves no whole log: the file is
 * absent or has no end record, and the reader refuses it. A file an earlier
 * run left at the path is removed when the path does not open, so it cannot
 * pass for this run's log; if it cannot be removed either, stderr says so.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#include "guest_memory.h"

static uint32_t tp_read(uint32_t va, unsigned width) {
    uint32_t value = 0;
    if (sa2_peek(va, width, &value) != SA2_OK)
        return 0xFFFFFFFFu;         /* unreadable; globals never are */
    return value;
}

static const uint32_t *tp_block(uint32_t va, uint32_t bytes) {
    return (const uint32_t *)sa2_gp(va, bytes);
}

#include "tick_digest.h"      /* the digest columns: tick_probe_core.h wants the mask first */
#include "tick_probe_core.h"

static const uint32_t *wl_section(void);
static uint64_t wl_clock(void);

/* word_log_core.h is the native probe's writer too, and every native capture's
 * oracle receipt pins its bytes (tools/s4/tick_replay.py native_probe_current),
 * so it is not edited for other hosts. Its one host-specific function,
 * wl_line_differs, uses six SSE2 operations; on a host without SSE2 they are
 * defined here instead, with the same results, in clang's vector extensions so
 * the scan stays vectorised (on aarch64 the compare folds to one umaxv a line),
 * and the compiler's <emmintrin.h>, which refuses every host but x86, is kept out
 * by defining its include guard first. The guard is clang's (zig cc is clang); with another
 * compiler this stops here, and should clang's guard ever be renamed, its header's
 * own #error stops the build, so the stand-in is never half applied. */
#if !defined(__x86_64__) && !defined(__i386__)
#ifndef __clang__
#error "wrap_tick_probe.c: the stand-in for <emmintrin.h> relies on clang's include guard"
#endif
#include <string.h>
#define __EMMINTRIN_H 1
typedef uint8_t __m128i __attribute__((vector_size(16), aligned(1)));
static inline __m128i _mm_loadu_si128(const __m128i *p) {
    __m128i r;
    memcpy(&r, p, 16);
    return r;
}
static inline __m128i _mm_setzero_si128(void) { return (__m128i){0}; }
static inline __m128i _mm_xor_si128(__m128i a, __m128i b) { return a ^ b; }
static inline __m128i _mm_or_si128(__m128i a, __m128i b) { return a | b; }
static inline __m128i _mm_cmpeq_epi8(__m128i a, __m128i b) {     /* 0xFF where equal */
    return (__m128i)(a == b);
}
typedef int8_t wl_s8x16 __attribute__((vector_size(16)));
typedef _Bool wl_b16 __attribute__((ext_vector_type(16)));
static inline int _mm_movemask_epi8(__m128i a) {                 /* byte k's top bit -> bit k */
    return __builtin_bit_cast(uint16_t, __builtin_convertvector((wl_s8x16)a < 0, wl_b16));
}
/* and its `__attribute__((target("sse2")))` read as an empty attribute list,
 * which the backend would otherwise report as a feature it does not have */
#define target(features)
#endif
#include "word_log_core.h"
#if !defined(__x86_64__) && !defined(__i386__)
#undef target
#endif

/* The guest's `.dataa` in host memory: the guest space is one mapping, so the
 * section is WL_SIZE contiguous bytes at base + WL_BASE (guest_memory.h). */
static const uint32_t *wl_section(void) {
    return (const uint32_t *)sa2_gp(WL_BASE, WL_SIZE);
}

/* The host's free-running counter: the time-stamp counter on x86-64, as
 * natively, and on aarch64 the generic timer's virtual count (CNTVCT_EL0), which
 * Linux lets user space read and which counts at a fixed rate whatever the core's
 * clock, as an invariant TSC does. The log's reader gets the rate from the
 * CLOCK_MONOTONIC reference beside it (word_log_core.h, "SA2F"), so the counter's
 * unit is never assumed. Elsewhere, CLOCK_MONOTONIC itself in nanoseconds. */
static uint64_t wl_clock(void) {
#if defined(__x86_64__) || defined(__i386__)
    return __builtin_ia32_rdtsc();
#elif defined(__aarch64__)
    uint64_t v;
    __asm__ volatile("isb\n\tmrs %0, cntvct_el0" : "=r"(v) : : "memory");
    return v;
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
#endif
}

void *__real_sub_433ee0(void *state, uint32_t pc, void *memory);

static FILE *tick_out;
static int tick_off;
static unsigned long tick;

/* GameLoop's returns so far, counted whether or not a capture is being
 * written: the tick harness/replay/wrap_input_replay.c keys its rows by, as the
 * native input probe keys them by its OnFrame count. */
static unsigned long gameloop_returns;
unsigned long sa2_replay_gameloop_returns(void) { return gameloop_returns; }

#define CURRENT_LEVEL   0x01934B70u   /* int16 */
#define GAME_MODE       0x0174B040u
#define GAME_STATE      0x01934BE0u   /* int16 */
#define FRAMES_INGAME   0x0174B03Cu   /* FrameCountIngame, int: advances while a level plays */
#define MAIN_CHAR_OBJ1  0x01DEA6C0u   /* EntityData1 *[8] */
#define ENTITY_POSITION 0x14u         /* EntityData1.Position, NJS_VECTOR */

static FILE *stage_out;
static int stage_off;

static void stage_row(unsigned long t) {
    if (!stage_out && !stage_off) {
        const char *path = getenv("SA2_STAGE_TRACE");
        if (!path || !*path || !(stage_out = fopen(path, "w"))) {
            stage_off = 1;
            return;
        }
        fputs("tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z\n", stage_out);
    }
    if (!stage_out) return;
    const uint32_t player = tp_read(MAIN_CHAR_OBJ1, 4);
    uint32_t pos[3] = {0, 0, 0};
    if (player && player != 0xFFFFFFFFu)
        for (int i = 0; i < 3; ++i) pos[i] = tp_read(player + ENTITY_POSITION + 4u * (uint32_t)i, 4);
    fprintf(stage_out, "%lu,0x%04x,0x%08x,0x%04x,0x%08x,0x%08x,0x%08x,0x%08x,0x%08x\n", t,
            tp_read(CURRENT_LEVEL, 2), tp_read(GAME_MODE, 4), tp_read(GAME_STATE, 2),
            tp_read(FRAMES_INGAME, 4), player, pos[0], pos[1], pos[2]);
    fflush(stage_out);          /* the run ends in a trap; every row must be on disk */
}

/* ------------------------------------------------------------ the word log */

static FILE *words_out;
static int words_off;       /* SA2_REPLAY_WORDS unset, or the log could not be kept */
static int words_in_tick;   /* a tick's record is being made and is not out yet */
static WlLog wlog;

static uint64_t words_ref(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

/* The log stops where it could not go on, and the file keeps no end record. */
static void words_stop(const char *why) {
    fprintf(stderr, "wrap_tick_probe: SA2_REPLAY_WORDS: %s; the log has no end record and "
                    "is refused\n", why);
    if (words_out)
        fclose(words_out);
    words_out = NULL;
    words_off = 1;
}

/* What the writer has made since the last drain, to the file. */
static void words_drain(void) {
    if (wlog.len && fwrite(wlog.buf, 1, wlog.len, words_out) != wlog.len) {
        words_stop("a write was cut short");
        return;
    }
    wl_drained(&wlog);
    if (fflush(words_out))
        words_stop("a write was cut short");
}

/* At the process's exit: the end record, which is last. Not after half a
 * record: a trap can land inside the scan itself (a host fault, the runtime's
 * wall-clock alarm), and main still returns. Not for a log of no tick either:
 * a trap inside GameLoop's first call leaves the keyframe alone, and a whole
 * log of nothing would pass every comparison vacuously. The guest is unmapped
 * by now: nothing here may read it. */
static void words_end(void) {
    if (!words_out)
        return;
    if (words_in_tick) {
        words_stop("the process exited inside a tick's scan");
        return;
    }
    if (!wlog.last_tick) {
        words_stop("GameLoop never returned, so the log holds no tick");
        return;
    }
    wl_close(&wlog);
    wl_end(&wlog, WL_END_ON_EXIT, words_ref(), wl_clock());
    words_drain();
    if (words_out && fclose(words_out))
        fprintf(stderr, "wrap_tick_probe: SA2_REPLAY_WORDS: closing the log failed; its end "
                        "record may not be on disk\n");
    words_out = NULL;
}

/* At GameLoop's first entry: the header and the keyframe. */
static void words_open(void) {
    const char *path = getenv("SA2_REPLAY_WORDS");
    if (!path || !*path) {
        words_off = 1;
        return;
    }
    if (!(words_out = fopen(path, "wb"))) {
        if (unlink(path) == 0)
            words_stop("the file does not open (a file an earlier run left there is removed)");
        else if (errno == ENOENT)
            words_stop("the file does not open");
        else
            words_stop("the file does not open, and the file already there could not be "
                       "removed: it is not this run's log");
        return;
    }
    uint32_t *shadow = (uint32_t *)malloc(WL_SIZE);
    uint8_t *buf = (uint8_t *)malloc(WL_BUFFER_BYTES);
    const WlStart start = {WL_RULE_ON_EXIT, 0, 1000000000ull, words_ref(), wl_clock()};
    if (!wl_begin(&wlog, buf, WL_BUFFER_BYTES, shadow, &start)) {
        free(shadow);
        free(buf);
        words_stop("its memory could not be allocated");
        return;
    }
    wl_keyframe(&wlog);
    words_drain();
    if (words_out && atexit(words_end))
        words_stop("no end record could be arranged at exit");
}

void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory) {
    if (!tick_out && !tick_off) {
        const char *path = getenv("SA2_REPLAY_TICKS");
        if (!path || !*path) {
            tick_off = 1;
        } else if (!(tick_out = fopen(path, "w"))) {
            tick_off = 1;
        } else {
            tp_write_header(tick_out);
            fflush(tick_out);
        }
    }
    if (!words_out && !words_off)
        words_open();
    void *r = __real_sub_433ee0(state, pc, memory);
    const uint64_t t_enter = words_out ? wl_clock() : 0;
    ++gameloop_returns;
    stage_row(gameloop_returns);
    if (tick_out) {
        tp_write_row(tick_out, ++tick);
        fflush(tick_out);           /* the run ends in a trap; every row must be on disk */
    }
    if (words_out) {
        words_in_tick = 1;
        wl_tick(&wlog, (uint32_t)gameloop_returns, t_enter, wl_clock());
        words_drain();
        words_in_tick = 0;
    }
    return r;
}
