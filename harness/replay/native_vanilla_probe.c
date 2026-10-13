/* Harness level 2, native side: run the game's own code, not the Mod Loader's.
 *
 * The live oracle loads the game through the SA2 Mod Loader, and the loader
 * rewrites game code before any mod runs: it hooks the texture loaders (PAK,
 * PRS, event PRS, LoadTextureList_NoName), the sound-bank enumeration, the
 * window procedure, the Direct3D frame wrappers and more -- 23 ranges in a
 * probe-only run, measured by diffing `.text` and PSFD00 against the
 * executable at a mod's Init (2026-09-23). Its texture loader reads PAK files
 * through its own C++ runtime, so the game's CRT never sees those opens: the
 * boot replay's `_cflush` (0x01751BB0) read 24 natively and 27 lifted at tick
 * 4. With this probe loaded the two agree (agents-journals/s4/
 * 2026-09-23-vanilla-oracle.md). The lifted build runs the game's code, so the
 * oracle does too.
 *
 * At Init this compares both code sections with sonic2app-unpacked.exe on
 * disk and writes the file's bytes back over every range the loader changed,
 * except the one KEEP range. It serves the runner's profile, StretchToWindow
 * and FramerateLimiter on, in which the loader creates no child window over
 * the game's (adopted by the user on 2026-09-24 as option A of the addendum to
 * Documentation/decisions/2026-09-23-S4-vanilla-oracle.md):
 *
 *   - the Direct3D BeginScene wrapper (0x00867B10): the device call, then the
 *     loader's frame limiter, then an OnRenderSceneStart list no probe fills
 *     (the runner refuses a probe that exports it). The limiter is the pace:
 *     each frame waits until 1/60 s of wall clock has passed since the last
 *     frame's BeginScene, and a slow frame is never made up (SA2ModLoader
 *     direct3d.cpp 27-50). Without it the game runs at several times 60 Hz
 *     on this 240 Hz display (stretch-true-v1a, v1b).
 *
 * The Present wrapper (0x00867AE0) is restored, because the harness does not
 * need it. With no child window it made the game's own call, Present(NULL,
 * NULL, NULL, NULL), and timed it so the limiter could skip its wait after a
 * present of 1/60 s or more. Restored, the limiter waits on its own clock
 * alone, which comes to the same wait: a present that long already puts the
 * next BeginScene 1/60 s past the last. Measured on 2026-09-24 with Present
 * restored and BeginScene kept, against both kept
 * (private/generated/s4/addendum-native/RESULTS.md, steps 2b and 2c): Metal
 * Harbor's test spawn for 600 ticks took 13.181 s against 13.180 s, its stage
 * trace byte-identical and its tick capture differing only in d_01af0000,
 * where two runs with both kept differ too; a 1200-tick boot matched in every
 * column on every tick.
 *
 * Frame-counted state does not depend on the pace. State the game drives from
 * real time does: CRI's streaming blocks and Sofdec's movies. Captures that
 * kept other ranges are another oracle mode (tools/oracle/oracle_mode.py reads
 * a capture's mode from its log, the ranges kept among it): those that kept
 * Present alone, which paced the game at 58.5 to 58.8 Hz through the loader's
 * child window until the evening of 2026-09-23, and those that kept Present
 * with BeginScene from then until this change.
 *
 * The other two ranges this probe once kept are restored, because the harness
 * needs neither (measured one at a time, 2026-09-23: boot, the test spawn and
 * scripted input injection identical with each restored). They are the window
 * procedure's jmp at 0x00401810, which now serves no window at all, and the
 * EndScene wrapper at 0x00867B00, which raises OnRenderSceneEnd for no probe
 * and makes the device call.
 *
 * What restoring turns off, which the header owes a reader:
 *   - the loader's exit detour at 0x007ACA2E: no mod's OnExit runs, so a probe
 *     must not rely on OnExit to flush (the tick probe flushes every row);
 *   - PrintDebug at 0x00426740, whose game version is a no-op: the Mod
 *     Loader's log loses everything after the first Init, including the test
 *     spawn's "Loading level" lines, so the stage trace's current_level column
 *     is the evidence that the spawn happened;
 *   - any mod's load-time patches (SA2ModInfo Patches, Jumps, Calls,
 *     Pointers), which the loader applies before the first Init; no probe uses
 *     them.
 * What the loader writes after its mods' Init is untouched, because it does
 * not exist yet: the harness's own hooks (OnFrame at GameLoop's exits, OnInput,
 * OnControl) and the test spawn's patches.
 *
 * What stays the loader's and no range covers (the interrogation of the
 * vanilla-oracle proposal, 2026-09-23): the main window's procedure, which the
 * loader sets with SetWindowLongW (window.cpp). On WM_ACTIVATE it sets
 * WindowActive and calls RestoreSounds, and in this profile it never clears
 * WindowActive.
 *
 * Import-table slots the loader redirects are restored. HookTheAPI points
 * CreateFileA at MyCreateFileA before any Init, and that consults a map of
 * 290 redirects the loader builds itself from the installation's PRS folders.
 * The table is in `.rdata`, which the section diff does not cover, so every
 * slot is compared with what GetProcAddress resolves for its name, and each
 * one that differs is restored and logged "iat-restored" ("iat-kept" when
 * built with RESTORE_IAT=0, the experiment). Restoring it was measured neutral
 * at boot and at Metal Harbor's stage load, the game thread's opened files
 * identical with it and without; the redirects are inert only because the
 * game opens a pack before its PRS twin, and restoring removes the risk that
 * leaves on routes nobody traced.
 *
 * The first OnFrame compares every restored range with the file again. A
 * write after all mods' Init would make "restored" untrue, and the capture
 * could not show it; each range is logged "tick1-intact" or "tick1-rewritten".
 *
 * mods/sa2-vanilla.csv records every range found, its first bytes before and
 * after, and what was done: "kept", "restored" (the copy made and the bytes
 * verified), "protect-failed" or "verify-failed". Every condition under which
 * the diff cannot be trusted is an "error" row, so a capture always says which
 * code ran: an executable that cannot be opened, a seek or a read that falls
 * short, a section larger than the buffer, an image not at its preferred base,
 * and a kept run that is not exactly one KEEP range.
 *
 * Built by tools/oracle/run_oracle_probe.py, first among the probes:
 *     --build vanilla_probe=harness/replay/native_vanilla_probe.c
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

#ifndef RESTORE_IAT
#define RESTORE_IAT 1
#endif

/* Which KEEP ranges are in force, one bit per row of KEEP, every row by
 * default. Anything else is an experiment that measures what a kept range is
 * for by restoring it, and every tool that checks the vanilla oracle refuses
 * a capture built so (tools/oracle/oracle_mode.py vanilla_problems). */
#ifndef KEEP_MASK
#define KEEP_MASK 0x1u
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

/* [start, end): a range the loader patched that overlaps one of these is kept
 * whole, so a patch whose bytes happen to match the original in places cannot
 * be kept in part and restored in part. */
static const uint32_t KEEP[][2] = {
    {0x00867B10u, 0x00867B15u},     /* BeginScene: the loader's frame limiter, the pace */
};

static unsigned char file_bytes[0x470000];

/* The restored ranges and the file's bytes for them, for the first OnFrame's
 * second look. Every range the loader patches is a few bytes long (at most 7
 * in every capture so far); a longer one is re-checked on its first 32. */
#define MAX_RESTORED 64u
#define RESTORED_BYTES 32u
static struct { uint32_t va, len; unsigned char bytes[RESTORED_BYTES]; } restored[MAX_RESTORED];
static unsigned restored_count, restored_dropped;

/* 0: no KEEP range overlaps; 1: exactly one KEEP range; -1: overlaps one but
 * is not exactly it, so the loader patched more (or less) than the range
 * this probe declares, and what is kept is not what was reasoned about. */
static int kept(uint32_t va, uint32_t len) {
    for (unsigned i = 0; i < sizeof KEEP / sizeof KEEP[0]; ++i)
        if ((KEEP_MASK >> i & 1u) && va < KEEP[i][1] && KEEP[i][0] < va + len)
            return va == KEEP[i][0] && va + len == KEEP[i][1] ? 1 : -1;
    return 0;
}

static void restore(FILE *out, FILE *exe, uint32_t va, uint32_t raw, uint32_t size) {
    if (size > sizeof file_bytes) {
        fprintf(out, "0x%08x,%u,,,error: the section is larger than the %u-byte buffer\n",
                va, size, (unsigned)sizeof file_bytes);
        return;
    }
    if (fseek(exe, (long)raw, SEEK_SET)) {
        fprintf(out, "0x%08x,%u,,,error: cannot seek to %#x in the executable\n", va, size, raw);
        return;
    }
    const size_t got = fread(file_bytes, 1, size, exe);
    if (got < size)
        fprintf(out, "0x%08x,%u,,,error: read %u of %u bytes from the executable\n",
                va, size, (unsigned)got, size);
    unsigned char *mem = (unsigned char *)(uintptr_t)va;
    uint32_t i = 0;
    while (i < got) {
        if (mem[i] == file_bytes[i]) { ++i; continue; }
        uint32_t j = i;
        while (j < got && (mem[j] != file_bytes[j] ||
                           (j + 1 < got && mem[j + 1] != file_bytes[j + 1])))
            ++j;
        fprintf(out, "0x%08x,%u,", va + i, j - i);
        for (uint32_t k = i; k < j && k < i + 16; ++k) fprintf(out, "%02x", mem[k]);
        fputc(',', out);
        for (uint32_t k = i; k < j && k < i + 16; ++k) fprintf(out, "%02x", file_bytes[k]);
        const int k = kept(va + i, j - i);
        const char *action = k > 0 ? "kept" : "error: kept but not exactly one KEEP range";
        if (!k) {
            /* Recorded after the attempt, and only once the bytes are seen to
             * be the file's: a capture that says "restored" ran the game's code. */
            DWORD old;
            if (!VirtualProtect(mem + i, j - i, PAGE_EXECUTE_READWRITE, &old)) {
                action = "protect-failed";
            } else {
                memcpy(mem + i, file_bytes + i, j - i);
                VirtualProtect(mem + i, j - i, old, &old);
                FlushInstructionCache(GetCurrentProcess(), mem + i, j - i);
                action = memcmp(mem + i, file_bytes + i, j - i) ? "verify-failed" : "restored";
                if (restored_count < MAX_RESTORED) {
                    const uint32_t n = j - i < RESTORED_BYTES ? j - i : RESTORED_BYTES;
                    restored[restored_count].va = va + i;
                    restored[restored_count].len = n;
                    memcpy(restored[restored_count].bytes, file_bytes + i, n);
                    ++restored_count;
                } else {
                    ++restored_dropped;
                }
            }
        }
        fprintf(out, ",%s\n", action);
        i = j;
    }
}

/* The image's own import table, walked in memory: every slot compared with
 * what GetProcAddress resolves for the name (or ordinal) the lookup table
 * names. At this probe's Init only the loader has touched the table: the
 * import probe, which wraps every slot, loads after this one. */
static void imports(FILE *out) {
    const unsigned char *img = (const unsigned char *)(uintptr_t)0x00400000u;
    const IMAGE_NT_HEADERS32 *nt =
        (const IMAGE_NT_HEADERS32 *)(img + ((const IMAGE_DOS_HEADER *)img)->e_lfanew);
    const IMAGE_DATA_DIRECTORY *dir =
        &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    unsigned differing = 0;
    for (const IMAGE_IMPORT_DESCRIPTOR *d =
             (const IMAGE_IMPORT_DESCRIPTOR *)(img + dir->VirtualAddress);
         d->Name; ++d) {
        const char *dll = (const char *)(img + d->Name);
        HMODULE m = GetModuleHandleA(dll);
        if (!d->OriginalFirstThunk) {
            fprintf(out, "0x%08x,0,,,error: %s has no lookup table and its slots are not "
                    "compared\n", (unsigned)(0x00400000u + d->FirstThunk), dll);
            continue;
        }
        const IMAGE_THUNK_DATA32 *names = (const IMAGE_THUNK_DATA32 *)(img + d->OriginalFirstThunk);
        uint32_t *slots = (uint32_t *)(uintptr_t)(0x00400000u + d->FirstThunk);
        for (unsigned i = 0; names[i].u1.AddressOfData; ++i) {
            const uint32_t entry = names[i].u1.AddressOfData;
            const char *name = entry & IMAGE_ORDINAL_FLAG32
                ? (const char *)(uintptr_t)(entry & 0xFFFFu)
                : (const char *)((const IMAGE_IMPORT_BY_NAME *)(img + entry))->Name;
            const uint32_t want = m ? (uint32_t)(uintptr_t)GetProcAddress(m, name) : 0u;
            if (!want) {
                fprintf(out, "0x%08x,4,%08x,,error: %s's import %u does not resolve\n",
                        (uint32_t)(uintptr_t)&slots[i], slots[i], dll, i);
                continue;
            }
            if (slots[i] == want) continue;
            ++differing;
            const uint32_t was = slots[i];
            const char *action = "iat-kept";
#if RESTORE_IAT
            DWORD old;
            if (!VirtualProtect(&slots[i], 4, PAGE_READWRITE, &old)) {
                action = "protect-failed";
            } else {
                slots[i] = want;
                VirtualProtect(&slots[i], 4, old, &old);
                action = slots[i] == want ? "iat-restored" : "verify-failed";
            }
#endif
            fprintf(out, "0x%08x,4,%08x,%08x,%s\n",
                    (uint32_t)(uintptr_t)&slots[i], was, want, action);
        }
    }
    (void)differing;
}

EXPORT void __cdecl Init(const char *path, const void *helpers, unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
    FILE *out = fopen("mods/sa2-vanilla.csv", "wb");
    if (!out) return;
    fputs("va,length,loader_bytes,file_bytes,action\n", out);
    /* Every address below is the image's own; they are only right where the
     * executable says it loads, which it does because it has no dynamic base. */
    if (GetModuleHandleA(NULL) != (HMODULE)(uintptr_t)0x00400000u) {
        fprintf(out, "0x00000000,0,,,error: the image is not at 0x00400000 and nothing "
                "was compared or restored\n");
        fclose(out);
        return;
    }
    FILE *exe = fopen("sonic2app-unpacked.exe", "rb");
    if (!exe) {
        fputs("0x00000000,0,,,error: the executable could not be opened\n", out);
        fclose(out);
        return;
    }
    restore(out, exe, 0x00401000u, 0x00000400u, 0x0046F591u);   /* .text */
    restore(out, exe, 0x00871000u, 0x0046FA00u, 0x00001D78u);   /* PSFD00 */
    fclose(exe);
    if (restored_dropped)
        fprintf(out, "0x00000000,0,,,error: %u restored ranges beyond %u are not re-checked "
                "at tick 1\n", restored_dropped, MAX_RESTORED);
    imports(out);
    fclose(out);
}

/* The second look, once: a restored range the loader (or anything) rewrote
 * after the mods' Init would otherwise still read "restored". */
EXPORT void __cdecl OnFrame(void) {
    static int done;
    if (done) return;
    done = 1;
    FILE *out = fopen("mods/sa2-vanilla.csv", "ab");
    if (!out) return;
    for (unsigned r = 0; r < restored_count; ++r) {
        const unsigned char *mem = (const unsigned char *)(uintptr_t)restored[r].va;
        fprintf(out, "0x%08x,%u,,,%s\n", restored[r].va, restored[r].len,
                memcmp(mem, restored[r].bytes, restored[r].len) ? "tick1-rewritten"
                                                                 : "tick1-intact");
    }
    fclose(out);
}
