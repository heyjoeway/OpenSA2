/* Opt-in play tweaks: one setting, SA2_TWEAKS, read once at the process's start, and the wraps that
 * act on it.
 *
 * Subtitle: SA2_TWEAKS is a comma-separated list of tweak names. Each name turns on one change to
 * the game's own behaviour that a player may want and no replay ever sees. Unset or empty, no tweak
 * is on, nothing is printed, and every wrap here only calls its `__real_`. The tweaks (TWEAKS
 * below):
 *
 *   no-event-bars   the black bars that some in-engine cutscenes (events) draw at the sides of a
 *                   screen wider than 4:3 are not drawn, so the scene shows across the whole width.
 *
 * The rules every tweak keeps (a later one adds a row to TWEAKS and its wraps to this file):
 *
 *   - Play only. The list is ignored when SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set, with one
 *     line saying so; tools/s4/device_profile.py PLAY_ENV strips SA2_TWEAKS from every registered
 *     run's environment, and the launcher's selftest drops it (host/launch/sa2_launch.cpp).
 *   - One report line. At the start, when SA2_TWEAKS is set, the run prints one line to stderr (the
 *     session's game.log): the active tweaks, "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)",
 *     or why none is on. A name that is no tweak is named in that line and ignored. The launcher's
 *     record and the Android session's report carry the setting and this line.
 *   - Off is the lifted code alone: each wrap tests its tweak's bit first and otherwise only calls
 *     `__real_` and returns.
 *
 * Where the setting comes from: on the desktop, a line in the release's share/game.env (the
 * launcher passes none of its own environment's SA2_ variables to the game); on Android, the
 * settings screen's Tweaks card (host/android GameSettings.sessionSettings), or a game.env.extra
 * line, which wins.
 *
 * ---------------------------------------------------------------- no-event-bars
 *
 * Where the bars come from, read from the unpacked image. 0x005F8EC0 (28 bytes, unnamed) answers
 * "is this event shown at 4:3?": EAX = 1 when ECX, an event ID, is in a fixed table of 14 IDs at
 * 0x008A0ED8 (21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409), else 0. It has
 * two callers, each linked against it directly, and each asks it only after computing
 * k = (H/640)/(V/480) from HorizontalResolution and VerticalResolution and finding k > 1:
 *
 *   0x005FB503  the event mode's executor (the arm 0x005FB421 of 0x005FB270), ECX = EventID
 *               ([0x01DB0FCC]); return address 0x005FB508, then `test eax,eax; je 0x005FB5B9`.
 *               On 1, the block 0x005FB510-0x005FB5B7 sets render states, draws two black quads
 *               on the 640x480 canvas, x from -320(k-1) to 0 and from 641 to 641 + 320(k-1), each
 *               480 high (the screen's overhang on each side of the 4:3 column), and restores the
 *               states. The bars are pillarbox bars; no event draws bars at the top or bottom.
 *   0x00601943  the event movie player (0x00601690), ECX = its event ID; return address
 *               0x00601948, and the same bars beside the movie. Only 409 in the table is a movie.
 *
 * The wrap reads the return address at guest [ESP] on entry (the lifted CALL has pushed it), calls
 * the lifted body, and when the call came from the executor (0x005FB508) and the answer is 1, sets
 * EAX to 0. The executor then takes the branch it takes for every event outside the table, and for
 * every event on a 4:3 screen: no quads, no render-state changes, and it rejoins the bar path at
 * 0x005FB5BF with ECX the event ID, as the bar path leaves it. Nothing crops the scene, so nothing
 * needs widening: the event's screen is the full canvas (0x0173A128: 0, 0, 640, 480) and its 3D
 * view is Hor+ across the back buffer, as in gameplay, so the area the bars covered is cleared and
 * drawn already. The movie's bars stay: the movie is 4:3 footage, and without its bars the sides
 * would show only what is behind it. On a screen no wider than 4:3 the predicate is never asked,
 * and the tweak has nothing to do.
 *
 * The first time the tweak removes an event's bars, the run prints "sa2 tweaks: event-bars off:
 * event 361's side bars not drawn" (the first 16 events named, at most).
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"

/* X(bit, name, report): every tweak. `name` is its SA2_TWEAKS spelling, `report` what the report
 * line says of it when on. */
#define TWEAKS(X) X(SA2_TWEAK_NO_EVENT_BARS, "no-event-bars", "event-bars off")

enum {
#define TWEAK_ENUM(bit, name, report) bit##_INDEX,
    TWEAKS(TWEAK_ENUM)
#undef TWEAK_ENUM
    SA2_TWEAK_COUNT
};
enum {
#define TWEAK_BIT(bit, name, report) bit = 1u << bit##_INDEX,
    TWEAKS(TWEAK_BIT)
#undef TWEAK_BIT
};

static const struct { const char *name, *report; } g_tweak_rows[SA2_TWEAK_COUNT] = {
#define TWEAK_ROW(bit, name, report) {name, report},
    TWEAKS(TWEAK_ROW)
#undef TWEAK_ROW
};

/* `s` (n bytes, between `quote`s) appended to the comma-separated list in buf[0, len); the new
 * length, cap once the list is cut short. */
static size_t list_add(char *buf, size_t cap, size_t len, const char *quote, const char *s, size_t n) {
    if (len >= cap) return cap;
    const int w = snprintf(buf + len, cap - len, "%s%s%.*s%s", len ? ", " : "", quote, (int)n, s, quote);
    return w < 0 ? cap : len + (size_t)w;
}

/* The tweaks SA2_TWEAKS turns on (a mask of the bits above); `line` gets the run's one line, or ""
 * when the value is unset or empty. Exposed for the unit test (Documentation/scripts/tests/
 * test_tweaks.py). */
uint32_t sa2_tweaks_parse(const char *value, int replay, char *line, size_t n) {
    line[0] = 0;
    if (!value || !*value) return 0;
    if (replay) {
        snprintf(line, n, "sa2 tweaks: SA2_TWEAKS=%s ignored in a replay (SA2_REPLAY_TICKS or "
                 "SA2_REPLAY_WORDS is set)", value);
        return 0;
    }
    uint32_t on = 0;
    char unknown[160] = "", active[160] = "", known[160] = "";
    size_t ul = 0, al = 0, kl = 0;
    for (const char *p = value; *p;) {
        const char *end = strchr(p, ',');
        if (!end) end = p + strlen(p);
        const char *a = p, *b = end;
        while (a < b && (*a == ' ' || *a == '\t')) a++;
        while (b > a && (b[-1] == ' ' || b[-1] == '\t')) b--;
        int i = 0;
        while (i < SA2_TWEAK_COUNT && !(strlen(g_tweak_rows[i].name) == (size_t)(b - a) &&
                                        memcmp(g_tweak_rows[i].name, a, (size_t)(b - a)) == 0)) i++;
        if (i < SA2_TWEAK_COUNT) on |= 1u << i;
        else if (a < b) ul = list_add(unknown, sizeof unknown, ul, "\"", a, (size_t)(b - a));
        p = *end ? end + 1 : end;
    }
    for (int i = 0; i < SA2_TWEAK_COUNT; i++) {
        if (on & (1u << i))
            al = list_add(active, sizeof active, al, "", g_tweak_rows[i].report, strlen(g_tweak_rows[i].report));
        kl = list_add(known, sizeof known, kl, "", g_tweak_rows[i].name, strlen(g_tweak_rows[i].name));
    }
    if (unknown[0])
        snprintf(line, n, "sa2 tweaks: %s (SA2_TWEAKS=%s; no tweak is named %s, so it is ignored; the "
                 "tweaks: %s)", on ? active : "none on", value, unknown, known);
    else
        snprintf(line, n, "sa2 tweaks: %s (SA2_TWEAKS=%s)", on ? active : "none on", value);
    return on;
}

static uint32_t g_tweaks;           /* the active tweaks, read once at the start */

__attribute__((constructor)) static void sa2_tweaks_read(void) {
    char line[512];
    const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
    g_tweaks = sa2_tweaks_parse(getenv("SA2_TWEAKS"), replay, line, sizeof line);
    if (line[0]) fprintf(stderr, "%s\n", line);
}

/* ---------------------------------------------------------------- no-event-bars */

#define EVENT_BARS_EXECUTOR_RET 0x005FB508u   /* after the executor's call at 0x005FB503 */
#define EVENT_BARS_MOVIE_RET    0x00601948u   /* after the movie player's call; left alone */

static uint32_t g_event_bars_named[16];
static unsigned g_event_bars_nnamed;
static int g_event_bars_stack_said;

void *__real_sub_5f8ec0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_5f8ec0(void *state, uint32_t pc, void *memory) {
    if (!(g_tweaks & SA2_TWEAK_NO_EVENT_BARS)) return __real_sub_5f8ec0(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_event_bars_stack_said++)
            fprintf(stderr, "sa2 tweaks: event-bars off: the guest stack at 0x%08X does not read, so "
                    "the caller is unknown and the event keeps its bars\n", esp);
        return __real_sub_5f8ec0(state, pc, memory);
    }
    const uint32_t event = sa2_rt_get_gpr(state, SA2_ECX);
    void *ret = __real_sub_5f8ec0(state, pc, memory);
    if (site != EVENT_BARS_EXECUTOR_RET || sa2_rt_get_gpr(state, SA2_EAX) == 0) return ret;
    sa2_rt_set_gpr(state, SA2_EAX, 0);
    unsigned i = 0;
    while (i < g_event_bars_nnamed && g_event_bars_named[i] != event) i++;
    if (i == g_event_bars_nnamed && i < sizeof g_event_bars_named / sizeof g_event_bars_named[0]) {
        g_event_bars_named[g_event_bars_nnamed++] = event;
        fprintf(stderr, "sa2 tweaks: event-bars off: event %u's side bars not drawn\n", event);
    }
    return ret;
}
