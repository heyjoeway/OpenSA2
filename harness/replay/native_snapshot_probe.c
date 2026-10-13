/* Harness level 2, native side: the game's whole writable data section, at
 * sampled ticks, to choose what the tick replay compares.
 *
 * The boot replay compares the words GameLoop itself references, and over the
 * boot only three of them vary: it shows the same modes at the same frames and
 * little else. Widening the list by hand would be guessing. This takes the
 * evidence instead: every SNAP_EVERY ticks up to SNAP_LIMIT it writes all of
 * `.dataa` (0x00917000, the executable's only writable section) to
 * mods/sa2-snapshots.bin, and at the last snapshot the process's committed
 * memory regions, so tools/s4/snapshot_fields.py can tell which words vary,
 * which repeat between runs, and which hold values that point into memory the
 * game has mapped. The fields are chosen from native evidence alone.
 *
 * A Mod Loader mod loaded beside harness/replay/native_tick_probe.c, before it,
 * so its OnFrame runs first and the last snapshot is written before the tick
 * probe's ExitProcess on the same tick. It reads memory and writes one file;
 * the tick capture of the same run must still hash to the replay's registered
 * one, which is the evidence that the pauses to write changed nothing it
 * compares.
 *
 * SNAP_BLOCKS, a comma-separated list of 64 KiB block addresses, narrows each
 * snapshot to those blocks and SNAP_FROM sets the first tick, so a run can take
 * the blocks whose digests did not repeat at every tick (SNAP_EVERY=1) without
 * writing the whole section 1200 times: that is how the words that move with
 * the loading threads' timing are found and left out of the digests.
 *
 * File format, little-endian, a sequence of records:
 *   "SA2S" u32 tick, u32 base, u32 size, then size bytes of memory
 *   "SA2B" u32 tick, u32 base, u32 size, then size bytes: one SNAP_BLOCKS block
 *   "SA2R" u32 tick, u32 count, u32 0,  then count x {u32 base, u32 size}
 *          (committed regions, MEM_COMMIT, from VirtualQuery)
 *
 * Built by tools/oracle/run_oracle_probe.py, before the tick probe:
 *     --build snapshot_probe=harness/replay/native_snapshot_probe.c
 *     --define snapshot_probe:SNAP_EVERY=<n> --define snapshot_probe:SNAP_LIMIT=<the tick probe's>
 */
#include <stdint.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

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

#ifndef SNAP_EVERY
#define SNAP_EVERY 100
#endif
#ifndef SNAP_LIMIT
#define SNAP_LIMIT 1200
#endif
#ifndef SNAP_FROM
#define SNAP_FROM SNAP_EVERY
#endif

#define DATA_BASE 0x00917000u
#define DATA_SIZE 0x01D59598u
#define MAX_REGIONS 4096

static HANDLE out = INVALID_HANDLE_VALUE;
static unsigned long tick;
static uint32_t regions[MAX_REGIONS][2];

static void put(const void *p, DWORD n) {
    DWORD w;
    WriteFile(out, p, n, &w, NULL);
}

static void header(const char *magic, uint32_t a, uint32_t b, uint32_t c) {
    uint32_t h[3] = {a, b, c};
    put(magic, 4);
    put(h, sizeof h);
}

static void snapshot(void) {
#ifdef SNAP_BLOCKS
    static const uint32_t blocks[] = {SNAP_BLOCKS};
    for (unsigned i = 0; i < sizeof blocks / sizeof blocks[0]; ++i) {
        const uint32_t end = DATA_BASE + DATA_SIZE;
        const uint32_t n = end - blocks[i] < 0x10000u ? end - blocks[i] : 0x10000u;
        header("SA2B", (uint32_t)tick, blocks[i], n);
        put((const void *)(uintptr_t)blocks[i], n);
    }
#else
    header("SA2S", (uint32_t)tick, DATA_BASE, DATA_SIZE);
    put((const void *)(uintptr_t)DATA_BASE, DATA_SIZE);
#endif
}

static void region_map(void) {
    uint32_t n = 0;
    MEMORY_BASIC_INFORMATION mbi;
    for (uintptr_t a = 0x10000; a < 0x80000000u && n < MAX_REGIONS;
         a = (uintptr_t)mbi.BaseAddress + mbi.RegionSize) {
        if (!VirtualQuery((LPCVOID)a, &mbi, sizeof mbi) || !mbi.RegionSize)
            break;
        if (mbi.State == MEM_COMMIT) {
            regions[n][0] = (uint32_t)(uintptr_t)mbi.BaseAddress;
            regions[n][1] = (uint32_t)mbi.RegionSize;
            ++n;
        }
    }
    header("SA2R", (uint32_t)tick, n, 0);
    put(regions, n * 8);
}

EXPORT void __cdecl Init(const char *path, const void *helpers, unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
    out = CreateFileA("mods/sa2-snapshots.bin", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                      FILE_ATTRIBUTE_NORMAL, NULL);
}

EXPORT void __cdecl OnFrame(void) {
    if (out == INVALID_HANDLE_VALUE)
        return;
    ++tick;
    if (tick >= SNAP_FROM && (tick - SNAP_FROM) % SNAP_EVERY == 0 && tick <= SNAP_LIMIT)
        snapshot();
    if (tick == SNAP_LIMIT) {
        region_map();
        CloseHandle(out);
        out = INVALID_HANDLE_VALUE;
    }
}
