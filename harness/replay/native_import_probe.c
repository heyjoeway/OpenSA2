/* Harness level 2, native side: which of the executable's imports a capture calls.
 *
 * M4's second clause is "every import hit by a replay is implemented", and
 * design section 9 names its input as the replays' call logs. The lifted side
 * logs every import call it makes (runtime/import_table.c); this is the native
 * side's log, so the set is not only what the lifted build happened to reach.
 *
 * A Mod Loader mod loaded beside harness/replay/native_tick_probe.c. In Init it
 * walks sonic2app's own import descriptors and points each import-address-table
 * slot at a thirteen-byte thunk that counts the call and jumps on through
 * whatever the slot held -- the system's function, or a hook the Mod Loader
 * installed first. A thunk pushes nothing and moves no register but EFLAGS,
 * which no import reads on entry, so the game's stack and registers are those
 * of the call it made. The tick capture taken in the same run must hash to the
 * boot replay's registered capture; that is the evidence this changed nothing
 * the replay compares.
 *
 * It writes mods/sa2-import-calls.csv from its own OnFrame at TICK_LIMIT, which
 * must be the tick probe's: loaded first, its OnFrame runs before the tick
 * probe's ExitProcess on the same tick, and no game code runs between the two.
 * One row per slot, in descriptor order: the slot's address, its calls,
 * whether the slot still held this probe's thunk, and the tick the counts were
 * read at, which tools/s4/native_imports.py requires to be the replay's
 * length. The names come from the executable itself, joined by address there,
 * so nothing here can misname a slot.
 *
 * INPUT_END_DLL, in place of TICK_LIMIT, is for a run whose last tick is not
 * known when it is built: a recording, which ends a fixed number of ticks after
 * the player reaches the goal. It names the DLL of harness/replay/
 * native_input_probe.c, which ends such a run and exports the tick it will end
 * on, SA2InputProbeEnd. tools/oracle/run_oracle_probe.py names each probe's DLL
 * after its mod id, and tools/oracle/record_stage.py builds the input probe as
 * mod input_probe, so the name is "input_probe.dll".
 *
 * Init looks the export up, once. The Mod Loader calls every mod's Init only
 * after it has loaded every mod (SA2ModLoader/dllmain.cpp, InitMods), so the
 * input probe is loaded by then whatever the load order, and the game loop
 * never takes the loader lock for it. OnFrame asks the export on every tick
 * until the file is written, and writes it on the tick before the one the
 * input probe names: the last tick the tick probe captures, because the input
 * probe, loaded before the tick probe, calls ExitProcess on its last tick
 * before the tick probe writes that tick's row. That is the tick TICK_LIMIT
 * names for a run that ends at a known tick, so the log is counted at the same
 * tick either way; it is written once, from OnFrame, however long the run. If
 * the export is not found, Init writes the file at once as refused, with the
 * reason, so the log says why rather than being absent, and the recorder's
 * preflight stops on it.
 *
 * DLL detach writes the file only if the tick was never reached, and then with
 * the tick it did reach, which the reader refuses. It is not the normal path:
 * at detach every other thread is gone, and a thread killed holding the heap
 * lock would hang CreateFileA until the runner's timeout (2026-09-23 review).
 * With INPUT_END_DLL it does not write at all: a run that ends there did not end
 * at the input probe's tick, and no log it could write would be read.
 * An OnExit writer was removed for the other half of the same review: the
 * Mod Loader calls OnExit while the game's threads still run, so it closed the
 * log before the exit path.
 *
 * What it cannot see, stated where the numbers are read:
 *   - calls made before Init, which is after the CRT's start-up and WinMain's
 *     entry: the lifted side's log covers those;
 *   - calls through a slot's value copied into a register or variable before
 *     Init (`mov esi, [__imp_X]` in a function still on the stack then).
 *
 * Built by tools/oracle/run_oracle_probe.py, before the tick probe:
 *     --build import_probe=harness/replay/native_import_probe.c
 *     --define import_probe:TICK_LIMIT=<the tick probe's>
 * or, for a run the input probe ends (built as mod input_probe, before the
 * tick probe):
 *     --define 'import_probe:INPUT_END_DLL="input_probe.dll"'
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

#ifndef TICK_LIMIT
#define TICK_LIMIT 0        /* 0: write only at detach, with the tick reached */
#endif
#if defined(INPUT_END_DLL) && TICK_LIMIT
#error "INPUT_END_DLL and TICK_LIMIT each choose the tick the log is written at; give one"
#endif

#define MAX_SLOTS 512
#define THUNK 16

static volatile LONG calls[MAX_SLOTS];
static void *next_fn[MAX_SLOTS];
static uint32_t *slot_at[MAX_SLOTS];
static unsigned slots;
static uint8_t *thunks;
static int state;          /* 0 not patched, 1 patched, -1 refused (the file says why) */
static const char *refused;
static int written;
static unsigned long tick;

static void put32(uint8_t *p, uint32_t v) {
    for (unsigned i = 0; i < 4; ++i)
        p[i] = (uint8_t)(v >> (8 * i));
}

static void patch(void) {
    uint8_t *base = (uint8_t *)GetModuleHandleA(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)base;
    const IMAGE_NT_HEADERS32 *nt = (const IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
    const IMAGE_DATA_DIRECTORY *dir =
        &nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    const IMAGE_IMPORT_DESCRIPTOR *d =
        (const IMAGE_IMPORT_DESCRIPTOR *)(base + dir->VirtualAddress);
    thunks = (uint8_t *)VirtualAlloc(NULL, MAX_SLOTS * THUNK, MEM_COMMIT | MEM_RESERVE,
                                     PAGE_EXECUTE_READWRITE);
    if (!thunks) {
        state = -1, refused = "VirtualAlloc for the thunks failed";
        return;
    }
    for (; d->Name; ++d) {
        for (uint32_t *s = (uint32_t *)(base + d->FirstThunk); *s; ++s) {
            if (slots == MAX_SLOTS) {
                state = -1, refused = "more import slots than MAX_SLOTS";
                return;
            }
            const unsigned i = slots++;
            slot_at[i] = s;
            next_fn[i] = (void *)(uintptr_t)*s;
            uint8_t *t = thunks + THUNK * i;
            t[0] = 0xF0, t[1] = 0xFF, t[2] = 0x05;               /* lock inc dword [calls+i] */
            put32(t + 3, (uint32_t)(uintptr_t)&calls[i]);
            t[7] = 0xFF, t[8] = 0x25;                           /* jmp dword [next_fn+i] */
            put32(t + 9, (uint32_t)(uintptr_t)&next_fn[i]);
            t[13] = t[14] = t[15] = 0xCC;
        }
    }
    FlushInstructionCache(GetCurrentProcess(), thunks, MAX_SLOTS * THUNK);
    for (unsigned i = 0; i < slots; ++i) {
        DWORD old;
        if (!VirtualProtect(slot_at[i], 4, PAGE_READWRITE, &old)) {
            state = -1, refused = "VirtualProtect on an import slot failed";
            return;
        }
        *slot_at[i] = (uint32_t)(uintptr_t)(thunks + THUNK * i);
        VirtualProtect(slot_at[i], 4, old, &old);
    }
    state = 1;
}

/* Hand-formatted: this runs under the loader lock at detach, so it uses
 * nothing but kernel32. */
static char *hex8(char *o, uint32_t v) {
    *o++ = '0', *o++ = 'x';
    for (int i = 7; i >= 0; --i)
        *o++ = "0123456789abcdef"[(v >> (4 * i)) & 15];
    return o;
}

static char *dec(char *o, uint32_t v) {
    char b[10];
    int n = 0;
    do b[n++] = (char)('0' + v % 10), v /= 10; while (v);
    while (n) *o++ = b[--n];
    return o;
}

static void put(HANDLE f, const char *s, char *e) {
    DWORD w;
    WriteFile(f, s, (DWORD)(e - s), &w, NULL);
}

static void write_calls(void) {
    if (written) return;
    written = 1;
    HANDLE f = CreateFileA("mods/sa2-import-calls.csv", GENERIC_WRITE, 0, NULL, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, NULL);
    if (f == INVALID_HANDLE_VALUE) return;
    static const char header[] = "slot,calls,ours,tick\n";
    DWORD w;
    WriteFile(f, header, sizeof header - 1, &w, NULL);
    if (state != 1) {
        static const char none[] = "refused,0,0,0\n";
        WriteFile(f, none, sizeof none - 1, &w, NULL);
        if (refused) {
            const char *e = refused;
            while (*e) ++e;
            put(f, refused, (char *)e);
        }
        CloseHandle(f);
        return;
    }
    for (unsigned i = 0; i < slots; ++i) {
        char line[48], *o = line;
        o = hex8(o, (uint32_t)(uintptr_t)slot_at[i]);
        *o++ = ',';
        o = dec(o, (uint32_t)calls[i]);
        *o++ = ',';
        *o++ = *slot_at[i] == (uint32_t)(uintptr_t)(thunks + THUNK * i) ? '1' : '0';
        *o++ = ',';
        o = dec(o, (uint32_t)tick);
        *o++ = '\n';
        put(f, line, o);
    }
    CloseHandle(f);
}

#ifdef INPUT_END_DLL
typedef unsigned long(__cdecl *end_fn)(void);
static end_fn input_end;    /* the input probe's SA2InputProbeEnd, once Init found it */

/* Init's lookup (the comment at the top). The reason is one CSV field, with no
 * comma, so the reader keeps it whole. */
static void find_input_end(void) {
    HMODULE m = GetModuleHandleA(INPUT_END_DLL);
    FARPROC f = m ? GetProcAddress(m, "SA2InputProbeEnd") : NULL;
    if (!f) {
        state = -1;
        refused = INPUT_END_DLL " is not loaded or does not export SA2InputProbeEnd; "
                  "the tick the run ends on is unknown";
        write_calls();
        return;
    }
    input_end = (end_fn)(void (*)(void))f;
}
#endif

EXPORT void __cdecl Init(const char *path, const void *helpers, unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
    patch();
#ifdef INPUT_END_DLL
    find_input_end();
#endif
}

#ifdef INPUT_END_DLL
/* The input probe's end tick is 0 while it knows none. */
EXPORT void __cdecl OnFrame(void) {
    ++tick;
    if (!written && input_end) {
        const unsigned long end = input_end();
        if (end && tick + 1 >= end)
            write_calls();
    }
}
#else
EXPORT void __cdecl OnFrame(void) {
    if (++tick == (unsigned long)TICK_LIMIT)
        write_calls();
}
#endif

BOOL WINAPI DllMain(HINSTANCE h, DWORD reason, LPVOID reserved) {
    (void)h;
    (void)reserved;
#ifndef INPUT_END_DLL
    if (reason == DLL_PROCESS_DETACH)
        write_calls();
#else
    (void)reason;
#endif
    return TRUE;
}
