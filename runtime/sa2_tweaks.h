/* Opt-in play tweaks: the list of tweaks, and what a tweak's own file uses.
 *
 * Subtitle: SA2_TWEAKS is a comma-separated list of tweak names, read once at the process's start
 * by runtime/sa2_tweaks.c (the reader and the report). Each name turns on one change to the game's
 * own behaviour that a player may want and no replay ever sees. Unset or empty, no tweak is on,
 * nothing is printed, and every tweak's wrap only calls its `__real_`. SA2_TWEAK_LIST below names
 * every tweak; each tweak's wraps, and the reading of the image they rest on, are in a file of its
 * own, runtime/tweaks/<name>.c.
 *
 * The rules every tweak keeps:
 *
 *   - Play only. The list is ignored when SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set, with one
 *     line saying so; tools/s4/device_profile.py PLAY_ENV strips SA2_TWEAKS from every registered
 *     run's environment, and the launcher's selftest drops it (host/launch/sa2_launch.cpp).
 *   - One report line. At the start, when SA2_TWEAKS is set, the run prints one line to stderr (the
 *     session's game.log): the active tweaks, "sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)",
 *     or why none is on. A name that is no tweak is named in that line and ignored. The launcher's
 *     record and the Android session's report carry the setting and this line. A tweak's own lines
 *     after it begin "sa2 tweaks: <report>: ".
 *   - Off is the lifted code alone: each wrap tests sa2_tweak_on(its bit) first and otherwise only
 *     calls `__real_` and returns.
 *   - By name, never silently: a case a tweak leaves alone on purpose, or cannot act on (a guest
 *     read that fails), is said once in its own line.
 *
 * Where the setting comes from: on the desktop, a line in the release's share/game.env (the
 * launcher passes none of its own environment's SA2_ variables to the game); on Android, the
 * settings screen's Tweaks card (host/android GameSettings.TWEAKS and sessionSettings), or a
 * game.env.extra line, which wins.
 *
 * Add a tweak. Each step adds lines and edits none, so tweaks written at once in separate
 * worktrees merge cleanly, or with a conflict between two added lines that keeps both:
 *
 *   1. One line in SA2_TWEAK_LIST: X(SA2_TWEAK_<NAME>, "<name>", "<report>", "<summary>"). <name> is
 *      its SA2_TWEAKS spelling (lower case, words joined by hyphens), <report> what the report line
 *      says of it when on, and <summary> its line in the release's README.txt
 *      (tools/s7/release_build.py player_readme). The lines go in name order, each ending in a
 *      backslash. A tweak's bit is its place in the list, never stored or sent, so where a line
 *      goes changes nothing outside the process; there is room for 32.
 *   2. runtime/tweaks/<name>.c: its wraps, and a header comment that says what they do and the
 *      reading of the image it rests on. One line of that comment names the lifted functions the
 *      file wraps, for example
 *
 *          TWEAK_WRAPS: sub_5f8ec0
 *
 *      Every link that compiles the runtime picks the file up by glob and passes -wrap for each
 *      (tools/s4/tweak_sources.py: link_s4.py, and through it the Windows and Android links;
 *      tools/s7/arm64_link.py and tools/s4/build_cost.py). Each wrap is
 *      `__attribute__((used, retain))`, declares its `__real_`, and begins
 *      `if (!sa2_tweak_on(SA2_TWEAK_<NAME>)) return __real_<sub>(state, pc, memory);`. A function
 *      the runtime or another tweak already wraps cannot be wrapped twice, and the tools refuse it
 *      by name: the tweak then goes into the wrap that exists, behind its own sa2_tweak_on test.
 *   3. One line in host/android GameSettings.TWEAKS: new Tweak("<name>", the switch's label, the
 *      note under it). The settings screen's Tweaks card makes a switch of each line, and
 *      sessionSettings sends the ones turned on.
 *   4. Its tests. Documentation/scripts/tests/test_tweaks.py holds the list, the files and the
 *      Android lines to one another, and each file's TWEAK_WRAPS line to the wraps it defines, so a
 *      step left out fails there by name. What the tweak does goes in a test file of its own, which
 *      compiles the tweak's file beside sa2_tweaks.c with a driver that stands in for the lifted
 *      functions and the runtime's accessors, as test_tweaks.py does for no-event-bars.
 *   5. A row in the player's guide (OpenSA2's docs/downloads.md, "Tweaks").
 */
#ifndef SA2_TWEAKS_H
#define SA2_TWEAKS_H

#include <stddef.h>
#include <stdint.h>

/* X(bit, name, report, summary): every tweak, one line each, in name order (step 1 above). */
#define SA2_TWEAK_LIST(X) \
    X(SA2_TWEAK_NO_EVENT_BARS, "no-event-bars", "event-bars off", "no black bars at the sides of most cutscenes on a wide screen") \
    /* the end of the list */

enum {
#define SA2_TWEAK_INDEX(bit, name, report, summary) bit##_INDEX,
    SA2_TWEAK_LIST(SA2_TWEAK_INDEX)
#undef SA2_TWEAK_INDEX
    SA2_TWEAK_COUNT
};
_Static_assert(SA2_TWEAK_COUNT <= 32, "SA2_TWEAK_LIST holds more tweaks than a 32-bit mask has bits");

enum {
#define SA2_TWEAK_BIT(bit, name, report, summary) bit = 1u << bit##_INDEX,
    SA2_TWEAK_LIST(SA2_TWEAK_BIT)
#undef SA2_TWEAK_BIT
};

/* Whether the tweak with this bit is on in this run: SA2_TWEAKS names it and no replay is set. */
int sa2_tweak_on(unsigned bit);

/* The tweaks `value` turns on (a mask of the bits above); `line` gets the run's one report line, or
 * "" when the value is unset or empty. The reader calls it once at the start; exposed for the unit
 * test (Documentation/scripts/tests/test_tweaks.py). */
uint32_t sa2_tweaks_parse(const char *value, int replay, char *line, size_t n);

#endif
