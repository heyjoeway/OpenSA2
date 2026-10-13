/* Harness level 2, native side: the live oracle's tick capture and word log.
 *
 * A read-only Mod Loader hook for the private copy of the user's game. Its
 * OnFrame runs at every exit of GameLoop (0x00433EE0). Each time it writes one
 * row of the named fields tick_fields.h lists, in tick_probe_core.h's format --
 * the same two files wrap_tick_probe.c includes, so the captures differ only
 * where the game's state does -- and then logs every word of `.dataa` that
 * changed since the tick before (word_log_core.h). It reads guest memory and
 * writes two files under mods/; it sends no input and patches nothing.
 *
 * The row carries the named fields alone (TP_NO_DIGEST). The digest columns a
 * replay compares are computed by the host from the log, by tick_probe_core.h's
 * own td_digest over the words the log rebuilds (tools/s4/wordlog.py), so the
 * game no longer spends its tick on them and the digest mask is not an input of
 * this probe: a new mask is not a new native configuration. The judge's run A
 * (private/generated/oracle/captures/wordobs-judge-boot-scanonly) showed the
 * columns so computed equal the registered boot capture's at every cell
 * (private/generated/s4/wordobs/reports/judge-plan.md).
 *
 * The CSV is opened in Init, which the Mod Loader calls once after loading
 * every mod and before WinMain reaches its loop, so a capture with a header
 * and no rows means the loop never turned rather than that the probe never
 * loaded. It is opened in binary mode: mingw's text mode would end every row
 * in CRLF where the lifted side writes LF, and the two files would differ on
 * every line for a reason that is not the game's.
 *
 * The word log is kept in memory and leaves the process once. Init allocates
 * its two blocks top down -- a shadow of the section and one buffer of
 * WL_BUFFER_BYTES, the same size in every run -- and records a region map and
 * the keyframe. From then on OnFrame only compares and appends: no handle is
 * opened and no OS call is made for the log while the game runs, because the
 * prototype that opened its log file at Init and wrote it every tick shifted
 * CRI's handle numbers and moved its audio state (design-in-probe-delta.md,
 * run 2). Its clock is the processor's time-stamp counter, an instruction, not
 * a call; QueryPerformanceCounter is read beside it at Init and at the end so
 * a reader can give the counter a rate. The log is written, to
 * mods/sa2-word-log.bin, once:
 *
 *   - TICK_LIMIT: at that tick, after its row and its scan, before the process
 *     ends, as the boot capture ends;
 *   - INPUT_END_DLL: on the tick before the one the input probe's
 *     SA2InputProbeEnd names, the last tick this probe captures (the input
 *     probe, loaded first, calls ExitProcess on its last tick before this probe
 *     writes that tick's row), as native_import_probe.c writes its log. This is
 *     the mode of every run tools/oracle/record_stage.py makes -- the
 *     recording, its replays, the re-takes and the preflight -- and in it this
 *     probe never ends the process: a replay's end is the input probe's, and a
 *     tick probe that ended one sooner cost the prototype's run its import log
 *     and the input probe's end file (design-in-probe-delta.md, run 3);
 *   - OnExit: if the game exits by itself first. A run the runner's timeout
 *     kills leaves no log, and a capture without one is refused.
 * If the input probe cannot be found at Init, a log naming that refusal is
 * written at once and none is kept. Both are one CreateFileA, WriteFile and
 * CloseHandle, with the end-of-run check (wl_verify: the shadow's hash for the
 * reader to hold its rebuild to, and a plain loop's count of words differing
 * from the shadow), a region map and a QueryPerformanceCounter before them.
 *
 * TICK_LIMIT ends the process from OnFrame once that many rows are on disk,
 * so a capture ends on a whole row at a known tick rather than wherever the
 * runner's timeout lands (see capture_check.py on torn rows).
 *
 * Built by tools/oracle/run_oracle_probe.py:
 *     --build tick_probe=harness/replay/native_tick_probe.c
 *     --define tick_probe:TICK_LIMIT=<n>                        (a boot capture)
 *     --define 'tick_probe:INPUT_END_DLL="input_probe.dll"'    (a run the input probe ends)
 *     --binary-capture sa2-word-log.bin
 */
#include <stdint.h>
#include <stdio.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

#ifndef TICK_LIMIT
#define TICK_LIMIT 0        /* 0: capture until the runner's timeout */
#endif
#if defined(INPUT_END_DLL) && TICK_LIMIT
#error "INPUT_END_DLL and TICK_LIMIT each choose the tick the log is written at; give one"
#endif

/* STACK_PAD writes that many bytes of the guest thread's stack below the
 * probe's frame on every tick. OnFrame runs on the game's stack, under ESP,
 * where GameLoop's frame just was, so the probe itself overwrites dead stack
 * memory the lifted build leaves alone. A capture taken with a large pad and
 * compared against one without is the control for whether the game ever reads
 * that memory back (2026-09-22 review). */
#ifndef STACK_PAD
#define STACK_PAD 0
#endif

typedef struct {
    int32_t Version;
    void *Init, *Patches;
    int32_t PatchCount;
    void *Jumps;
    int32_t JumpCount;
    void *Calls;
    int32_t CallCount;
    void *Pointers;
    int32_t PointerCount;
} ModInfo;

EXPORT const ModInfo SA2ModInfo = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0};

static uint32_t tp_read(uint32_t va, unsigned width) {
    const volatile uint8_t *p = (const volatile uint8_t *)(uintptr_t)va;
    uint32_t value = 0;
    for (unsigned i = 0; i < width; ++i)
        value |= (uint32_t)p[i] << (8 * i);
    return value;
}

#define TP_NO_DIGEST 1
#include "tick_probe_core.h"

static const uint32_t *wl_section(void);
static uint64_t wl_clock(void);
#include "word_log_core.h"

static const uint32_t *wl_section(void) {
    return (const uint32_t *)(uintptr_t)WL_BASE;      /* the game's own mapped section */
}

static uint64_t wl_clock(void) {
    return __builtin_ia32_rdtsc();
}

static FILE *tick_out;
static unsigned long tick;

static WlLog wlog;
static int wlog_kept;       /* a log is in memory and has not been written */

#if TICK_LIMIT
#define WLOG_RULE WL_RULE_TICK_LIMIT
#elif defined(INPUT_END_DLL)
#define WLOG_RULE WL_RULE_INPUT_END
#else
#define WLOG_RULE WL_RULE_ON_EXIT
#endif

#ifdef INPUT_END_DLL
typedef unsigned long(__cdecl *end_fn)(void);
static end_fn input_end;    /* the input probe's SA2InputProbeEnd, found at Init */
#endif

/* The committed regions now, over the whole 32-bit space: VirtualQuery until it
 * fails or the next address wraps. native_snapshot_probe.c stops at 0x80000000,
 * but Proton runs 32-bit games large-address-aware, and this probe's own two
 * blocks, allocated top down, lie above 2 GB: the first boot with this log
 * (wordlog-u1-boot-a) recorded 644 regions at Init and neither block among
 * them, so a pointer test using that map would miss every value above 2 GB.
 * The Metal Harbor run with the whole walk (wordlog-u1-mh-a) found those two
 * blocks and nothing else above 2 GB. They are named in the log (wl_own), so a
 * pointer test can leave them out: the game holds no address inside them. */
static void wlog_regions(uint32_t at_tick) {
    MEMORY_BASIC_INFORMATION m;
    wl_regions_open(&wlog, at_tick);
    for (uintptr_t a = 0x10000;;) {
        if (!VirtualQuery((LPCVOID)a, &m, sizeof m) || !m.RegionSize)
            break;
        if (m.State == MEM_COMMIT)
            wl_region(&wlog, (uint32_t)(uintptr_t)m.BaseAddress, (uint32_t)m.RegionSize);
        const uintptr_t next = (uintptr_t)m.BaseAddress + m.RegionSize;
        if (next <= a)
            break;
        a = next;
    }
    wl_regions_close(&wlog);
}

/* The one write: the end records, then the buffer to the file. A write cut
 * short leaves a file without its end record, which the reader refuses. The
 * end-of-run check comes first, nearest the last scan. */
static void wlog_write(uint32_t reason) {
    if (!wlog_kept)
        return;
    wlog_kept = 0;
    LARGE_INTEGER q;
    wl_close(&wlog);
    wl_verify(&wlog);
    wlog_regions((uint32_t)tick);
    QueryPerformanceCounter(&q);
    wl_end(&wlog, reason, (uint64_t)q.QuadPart, wl_clock());
    HANDLE h = CreateFileA(WL_FILE, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return;
    size_t off = 0;
    while (off < wlog.len) {
        const size_t left = wlog.len - off;
        const DWORD n = (DWORD)(left > (64u << 20) ? (64u << 20) : left);
        DWORD w = 0;
        if (!WriteFile(h, wlog.buf + off, n, &w, NULL) || !w)
            break;
        off += w;
    }
    CloseHandle(h);
}

static void wlog_init(void) {
    LARGE_INTEGER f, q;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&q);
    const WlStart start = {WLOG_RULE, (uint32_t)TICK_LIMIT, (uint64_t)f.QuadPart,
                           (uint64_t)q.QuadPart, wl_clock()};
    uint32_t *shadow = (uint32_t *)VirtualAlloc(NULL, WL_SIZE,
                                                MEM_RESERVE | MEM_COMMIT | MEM_TOP_DOWN,
                                                PAGE_READWRITE);
    uint8_t *buf = (uint8_t *)VirtualAlloc(NULL, WL_BUFFER_BYTES,
                                           MEM_RESERVE | MEM_COMMIT | MEM_TOP_DOWN,
                                           PAGE_READWRITE);
    if (!wl_begin(&wlog, buf, WL_BUFFER_BYTES, shadow, &start))
        return;                     /* no log: a capture without one is refused */
    wl_own(&wlog, shadow, WL_SIZE, buf, WL_BUFFER_BYTES);
    wlog_regions(0);
    wl_keyframe(&wlog);
    wlog_kept = 1;
#ifdef INPUT_END_DLL
    /* Init's lookup, as native_import_probe.c's: the Mod Loader calls every
     * mod's Init after loading every mod, so the input probe is loaded now
     * whatever the load order. */
    HMODULE m = GetModuleHandleA(INPUT_END_DLL);
    FARPROC fn = m ? GetProcAddress(m, "SA2InputProbeEnd") : NULL;
    if (!fn) {
        wlog_write(WL_END_REFUSED);
        return;
    }
    input_end = (end_fn)(void (*)(void))fn;
#endif
}

EXPORT void __cdecl Init(const char *path, const void *helpers,
                         unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
    tick_out = fopen("mods/sa2-tick-capture.csv", "wb");
    if (tick_out) {
        tp_write_header(tick_out);
        fflush(tick_out);
    }
    wlog_init();
}

EXPORT void __cdecl OnFrame(void) {
    const uint64_t t_enter = wl_clock();
#if STACK_PAD
    volatile unsigned char pad[STACK_PAD];
    for (unsigned i = 0; i < STACK_PAD; ++i)
        pad[i] = (unsigned char)(i * 131u + tick);
#endif
    if (!tick_out) return;
    tp_write_row(tick_out, ++tick);
    fflush(tick_out);
    if (wlog_kept) {
        wl_tick(&wlog, (uint32_t)tick, t_enter, wl_clock());
#ifdef INPUT_END_DLL
        const unsigned long end = input_end();
        if (end && tick + 1 >= end)
            wlog_write(WL_END_INPUT_END);
#endif
    }
    if (TICK_LIMIT && tick >= (unsigned long)TICK_LIMIT) {
        wlog_write(WL_END_TICK_LIMIT);
        fclose(tick_out);
        tick_out = 0;
        ExitProcess(0);
    }
}

EXPORT void __cdecl OnExit(void) {
    wlog_write(WL_END_ON_EXIT);
    if (tick_out) {
        fclose(tick_out);
        tick_out = 0;
    }
}
