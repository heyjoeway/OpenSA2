/* Harness level 2, native side: record or replay the controller state the game reads.
 *
 * A Mod Loader mod. Its OnInput runs where the loader hooks the `ret` of the
 * game's controller update (0x0077E897, the end of 0x0077E780), after the game
 * has read its devices into ControllersRaw (0x01A52918, PDS_PERIPHERAL, stride
 * 0x34) and before anything reads them. Documentation/decisions/
 * 2026-09-23-S4-stage-route.md explains why the raw level: every reader of the
 * controller, the message windows that wait for a button among them, sees it.
 *
 * INPUT_MODE=1 records: one row per OnInput call, keyed by the tick (OnFrame
 * returns so far) and the call's ordinal within it, holding port 0's value
 * fields, to mods/sa2-input.csv, flushed row by row.
 *
 * INPUT_MODE=2 replays: INPUT_SCRIPT names a header generated from such a file
 * (tools/oracle/record_stage.py), and each call writes the recorded fields for
 * its (tick, ordinal) over whatever the devices produced, so a key touched
 * during a replay changes nothing. A call with no recorded row writes a
 * neutral controller and is counted; mods/sa2-input-applied.csv records what
 * was applied and every miss.
 *
 * Both modes watch the goal latch, 0x0174B002, which only AwardWin sets
 * (0x0043E781), and end the process EXIT_AFTER ticks after it rises, so a
 * recording stops by itself at the goal and every replay stops at a known tick.
 * mods/sa2-input-end.csv then records the goal tick, the end tick and, for a
 * replay, how many calls had no recorded row.
 * TICK_LIMIT, if set, is a cap that ends the run regardless.
 *
 * SA2InputProbeEnd exports the tick the run will end on, as far as this probe
 * knows it: the one rule OnFrame ends the run by, or 0 while it knows none or
 * when it will not end the run because its file did not open.
 * native_import_probe.c built with INPUT_END_DLL asks it on every tick until
 * it has written its import log, which it writes on the tick before: the last
 * tick the tick probe captures, as this probe is loaded before the tick probe,
 * so a recording's log covers every tick its capture holds although its end is
 * decided while it is played. The import probe asks from its own OnFrame,
 * which runs before this one on each tick, so it sees the goal a tick late;
 * EXIT_AFTER of at least 2 leaves it the tick it needs. A TICK_LIMIT of 1 is
 * refused too: the run would end on tick 1, before the tick probe's first row,
 * and the import probe cannot count a log at tick 0.
 *
 * Built by tools/oracle/run_oracle_probe.py, loaded before the tick probe:
 *     --build input_probe=harness/replay/native_input_probe.c
 *     --define input_probe:INPUT_MODE=1                                  (record)
 *     --define input_probe:INPUT_MODE=2 --define 'input_probe:INPUT_SCRIPT="<path>"'  (replay)
 */
#include <stdint.h>
#include <stdio.h>
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

#ifndef INPUT_MODE
#define INPUT_MODE 1
#endif
#ifndef EXIT_AFTER
#define EXIT_AFTER 300
#endif
#ifndef TICK_LIMIT
#define TICK_LIMIT 0
#endif
#if EXIT_AFTER < 2
#error "EXIT_AFTER below 2 ends the run before native_import_probe.c can count its log"
#endif
#if TICK_LIMIT == 1
#error "a TICK_LIMIT of 1 ends the run before any row is captured or the import log counted"
#endif

#define RAW0 0x01A52918u
#define GOAL 0x0174B002u

/* PDS_PERIPHERAL's value fields, in the order the file carries them. */
enum { F_ON, F_OFF, F_PRESS, F_RELEASE, F_R, F_L, F_X1, F_Y1, F_X2, F_Y2, F_OLD, F_COUNT };
static const uint32_t off[F_COUNT] = {0x08, 0x0C, 0x10, 0x14, 0x18, 0x1A, 0x1C, 0x1E, 0x20, 0x22, 0x2C};
static const uint32_t width[F_COUNT] = {4, 4, 4, 4, 2, 2, 2, 2, 2, 2, 4};

#if INPUT_MODE == 2
#include INPUT_SCRIPT   /* static const uint32_t in_rows[][2 + F_COUNT]; static const unsigned in_count; */
static unsigned next_row, misses;
#endif

static FILE *out;
static unsigned long tick, goal_tick;
static unsigned ordinal;

#if INPUT_MODE == 1
static uint32_t rd(uint32_t va, uint32_t w) {
    return w == 4 ? *(volatile uint32_t *)(uintptr_t)va : *(volatile uint16_t *)(uintptr_t)va;
}
#else
static void wr(uint32_t va, uint32_t w, uint32_t v) {
    if (w == 4) *(volatile uint32_t *)(uintptr_t)va = v;
    else *(volatile uint16_t *)(uintptr_t)va = (uint16_t)v;
}
#endif

EXPORT void __cdecl Init(const char *path, const void *helpers, unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
#if INPUT_MODE == 1
    out = fopen("mods/sa2-input.csv", "wb");
    if (out) fputs("tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old\n", out);
#else
    out = fopen("mods/sa2-input-applied.csv", "wb");
    if (out) fputs("tick,ord,matched,on,off,press,release,r,l,x1,y1,x2,y2,old\n", out);
#endif
    if (out) fflush(out);
}

EXPORT void __cdecl OnInput(void) {
    if (!out) return;
    const unsigned ord = ordinal++;
    uint32_t v[F_COUNT];
#if INPUT_MODE == 1
    for (unsigned i = 0; i < F_COUNT; ++i) v[i] = rd(RAW0 + off[i], width[i]);
    fprintf(out, "%lu,%u", tick, ord);
#else
    while (next_row < in_count && (in_rows[next_row][0] < tick ||
           (in_rows[next_row][0] == tick && in_rows[next_row][1] < ord)))
        ++next_row;
    const int matched = next_row < in_count && in_rows[next_row][0] == tick &&
                        in_rows[next_row][1] == ord;
    for (unsigned i = 0; i < F_COUNT; ++i) {
        v[i] = matched ? in_rows[next_row][2 + i] : 0u;
        wr(RAW0 + off[i], width[i], v[i]);
    }
    if (!matched) ++misses;
    fprintf(out, "%lu,%u,%d", tick, ord, matched);
#endif
    for (unsigned i = 0; i < F_COUNT; ++i) fprintf(out, ",0x%x", (unsigned)v[i]);
    fputc('\n', out);
    fflush(out);
}

/* The tick OnFrame ends the run on, while its file is open: EXIT_AFTER ticks
 * after the goal latch rose, or TICK_LIMIT, whichever comes first; 0 while
 * neither is known. */
static unsigned long planned_end(void) {
    unsigned long end = goal_tick ? goal_tick + EXIT_AFTER : 0;
    if (TICK_LIMIT && (!end || (unsigned long)TICK_LIMIT < end))
        end = (unsigned long)TICK_LIMIT;
    return end;
}

/* 0 when the file did not open: OnFrame then never ends the run, so there is
 * no end tick to count a log at. */
EXPORT unsigned long __cdecl SA2InputProbeEnd(void) {
    return out ? planned_end() : 0;
}

EXPORT void __cdecl OnFrame(void) {
    ++tick;
    ordinal = 0;
    if (!goal_tick && *(volatile uint8_t *)(uintptr_t)GOAL)
        goal_tick = tick;
    const unsigned long last = planned_end();
    const int done = last && tick >= last;
    if (done && out) {
        fclose(out);
        out = NULL;
        FILE *end = fopen("mods/sa2-input-end.csv", "wb");
        if (end) {
#if INPUT_MODE == 2
            fprintf(end, "goal_tick,end_tick,misses\n%lu,%lu,%u\n", goal_tick, tick, misses);
#else
            fprintf(end, "goal_tick,end_tick,misses\n%lu,%lu,0\n", goal_tick, tick);
#endif
            fclose(end);
        }
        ExitProcess(0);
    }
}
