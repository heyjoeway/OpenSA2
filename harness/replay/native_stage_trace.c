/* Harness level 2, native side: the stage trace, as the lifted side writes it.
 *
 * A read-only Mod Loader mod. Its OnFrame runs at every exit of GameLoop, the
 * point harness/replay/wrap_tick_probe.c's stage trace (SA2_STAGE_TRACE) is
 * written on the lifted side, and it writes the same columns in the same
 * format: CurrentLevel, GameMode, GameState, the in-game frame counter, the
 * first player's object and its position. The two traces compare line for
 * line in every column but `player`: that is a heap address, which two native
 * runs of one configuration share and the lifted build does not. Between the
 * two sides only whether it is null compares, with the position.
 *
 * Why it exists: a controller recording is keyed by GameLoop returns since the
 * process started (native_input_probe.c), so a recording made under one oracle
 * configuration replays correctly under another only if the level becomes
 * playable on the same tick under both. GameMode reaches GameMode_Level at the
 * same tick with the Mod Loader's texture code and with the game's own
 * (native_vanilla_probe.c), but the level is playable only when GameState
 * reaches GameStates_Ingame, after loading, and the tick capture does not
 * record GameState (2026-09-23).
 *
 * The addresses and layouts are the Mod Loader's (SA2Variables.h, SA2Structs.h):
 * CurrentLevel int16 0x01934B70, GameMode 0x0174B040, GameState int16
 * 0x01934BE0, FrameCountIngame 0x0174B03C, MainCharObj1[0] 0x01DEA6C0,
 * EntityData1.Position at +0x14.
 *
 * TICK_LIMIT ends the process from OnFrame once that many rows are on disk.
 *
 * Built by tools/oracle/run_oracle_probe.py:
 *     --build stage_trace=harness/replay/native_stage_trace.c
 *     --define stage_trace:TICK_LIMIT=<n>
 */
#include <stdint.h>
#include <stdio.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)

#ifndef TICK_LIMIT
#define TICK_LIMIT 0
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

#define CURRENT_LEVEL   0x01934B70u
#define GAME_MODE       0x0174B040u
#define GAME_STATE      0x01934BE0u
#define FRAMES_INGAME   0x0174B03Cu
#define MAIN_CHAR_OBJ1  0x01DEA6C0u
#define ENTITY_POSITION 0x14u

static uint32_t rd(uint32_t va, unsigned width) {
    const volatile uint8_t *p = (const volatile uint8_t *)(uintptr_t)va;
    uint32_t v = 0;
    for (unsigned i = 0; i < width; ++i) v |= (uint32_t)p[i] << (8 * i);
    return v;
}

/* Whether `len` bytes at `va` are committed and readable. The player's object
 * is a game heap pointer, the first this harness dereferences every frame; a
 * read of a stale one must not take the game down with the probe. */
static int readable(uint32_t va, uint32_t len) {
    MEMORY_BASIC_INFORMATION m;
    if (!VirtualQuery((const void *)(uintptr_t)va, &m, sizeof m) || m.State != MEM_COMMIT)
        return 0;
    if (m.Protect & (PAGE_NOACCESS | PAGE_GUARD))
        return 0;
    return (uintptr_t)m.BaseAddress + m.RegionSize >= (uintptr_t)va + len;
}

static FILE *out;
static unsigned long tick;

EXPORT void __cdecl Init(const char *path, const void *helpers, unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
    out = fopen("mods/sa2-stage-trace.csv", "wb");        /* binary: LF, as the lifted side */
    if (out) {
        fputs("tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z\n", out);
        fflush(out);
    }
}

EXPORT void __cdecl OnFrame(void) {
    if (!out) return;
    ++tick;
    const uint32_t player = rd(MAIN_CHAR_OBJ1, 4);
    uint32_t pos[3] = {0, 0, 0};
    if (player && player != 0xFFFFFFFFu) {
        /* The same condition as the lifted side's (wrap_tick_probe.c). One
         * difference remains, and it is this side's alone: a position the
         * process has not committed reads as 0xffffffff here, where the lifted
         * side's reads succeed anywhere in its guest space. */
        const int ok = readable(player + ENTITY_POSITION, 12u);
        for (int i = 0; i < 3; ++i)
            pos[i] = ok ? rd(player + ENTITY_POSITION + 4u * (uint32_t)i, 4) : 0xFFFFFFFFu;
    }
    fprintf(out, "%lu,0x%04x,0x%08x,0x%04x,0x%08x,0x%08x,0x%08x,0x%08x,0x%08x\n", tick,
            rd(CURRENT_LEVEL, 2), rd(GAME_MODE, 4), rd(GAME_STATE, 2), rd(FRAMES_INGAME, 4),
            player, pos[0], pos[1], pos[2]);
    fflush(out);
    if (TICK_LIMIT && tick >= (unsigned long)TICK_LIMIT) {
        fclose(out);
        out = NULL;
        ExitProcess(0);
    }
}
