/* Opt-in play tweaks: the reader and the report.
 *
 * Subtitle: SA2_TWEAKS, read once at the process's start, turned into the mask sa2_tweak_on
 * answers from, and the run's one report line. runtime/sa2_tweaks.h holds the list of tweaks
 * (SA2_TWEAK_LIST), the rules every tweak keeps and the steps that add one; each tweak's wraps are
 * in runtime/tweaks/<name>.c. Nothing here names a tweak: a tweak added to the list is read,
 * reported and refused here as every other is.
 *
 * The tweaks a game has, by bit: SA2_TWEAK_LIST's, then each code-patch tweak the game links
 * (sa2_tweak_patches_linked, from the file tools/s8/variants/variants.py generates) that the list
 * does not name, which is a test fixture; at most 32 in all. A code-patch tweak in the list
 * (SA2_TWEAK_PATCH_LIST) that the game does not link is one it was built without.
 *
 * The value is split at commas, each name trimmed of spaces and tabs and matched exactly (case
 * counts); a name given twice is one, an empty one is skipped, and one that is no tweak is named in
 * the report line and ignored, with the list of the tweaks there are, and so is a code-patch tweak
 * the game was built without. In a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS set) the whole
 * value is ignored, and the line says so.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sa2_tweaks.h"

static const struct { const char *name, *report; } g_tweak_rows[SA2_TWEAK_COUNT] = {
#define TWEAK_ROW(bit, name, report, summary) {name, report},
    SA2_TWEAK_LIST(TWEAK_ROW)
#undef TWEAK_ROW
};

/* The code-patch tweaks a game built without tools/s8/variants/variants.py's file links: none. */
__attribute__((weak)) const sa2_tweak_patch *sa2_tweak_patches_linked(unsigned *count) {
    *count = 0;
    return NULL;
}

/* Every tweak this game has, by bit (the header comment); `has` is 0 for a code-patch tweak in the
 * list that the game does not link. g_patch_bit[i] is the bit of sa2_tweak_patches_linked's row i. */
static struct { const char *name, *report; int has; } g_known[32];
static unsigned g_nknown, g_npatch;
static uint32_t g_patch_bit[32];

static void known_read(void) {
    static int done;
    if (done++) return;
    for (unsigned i = 0; i < SA2_TWEAK_COUNT; i++) {
        g_known[i].name = g_tweak_rows[i].name;
        g_known[i].report = g_tweak_rows[i].report;
        g_known[i].has = !(SA2_TWEAK_PATCHES & (1u << i));
    }
    g_nknown = SA2_TWEAK_COUNT;
    unsigned n = 0;
    const sa2_tweak_patch *rows = sa2_tweak_patches_linked(&n);
    g_npatch = n < 32 ? n : 32;
    for (unsigned r = 0; r < g_npatch; r++) {
        unsigned i = 0;
        while (i < g_nknown && strcmp(g_known[i].name, rows[r].name) != 0) i++;
        if (i == g_nknown) {
            if (g_nknown == 32) continue;      /* no bit left: variants.py refuses such a build */
            g_known[g_nknown].name = rows[r].name;
            g_known[g_nknown++].report = rows[r].report;
        }
        g_known[i].has = 1;
        g_patch_bit[r] = 1u << i;
    }
}

/* `s` (n bytes, between `quote`s) appended to the comma-separated list in buf[0, len); the new
 * length, cap once the list is cut short. */
static size_t list_add(char *buf, size_t cap, size_t len, const char *quote, const char *s, size_t n) {
    if (len >= cap) return cap;
    const int w = snprintf(buf + len, cap - len, "%s%s%.*s%s", len ? ", " : "", quote, (int)n, s, quote);
    return w < 0 ? cap : len + (size_t)w;
}

uint32_t sa2_tweaks_parse(const char *value, int replay, char *line, size_t n) {
    line[0] = 0;
    if (!value || !*value) return 0;
    if (replay) {
        snprintf(line, n, "sa2 tweaks: SA2_TWEAKS=%s ignored in a replay (SA2_REPLAY_TICKS or "
                 "SA2_REPLAY_WORDS is set)", value);
        return 0;
    }
    known_read();
    uint32_t on = 0, without = 0;
    char unknown[256] = "", absent[256] = "", active[256] = "", known[256] = "";
    size_t ul = 0, bl = 0, al = 0, kl = 0;
    for (const char *p = value; *p;) {
        const char *end = strchr(p, ',');
        if (!end) end = p + strlen(p);
        const char *a = p, *b = end;
        while (a < b && (*a == ' ' || *a == '\t')) a++;
        while (b > a && (b[-1] == ' ' || b[-1] == '\t')) b--;
        unsigned i = 0;
        while (i < g_nknown && !(strlen(g_known[i].name) == (size_t)(b - a) &&
                                 memcmp(g_known[i].name, a, (size_t)(b - a)) == 0)) i++;
        if (i < g_nknown && g_known[i].has) on |= 1u << i;
        else if (i < g_nknown && !(without & (1u << i))) {
            without |= 1u << i;
            bl = list_add(absent, sizeof absent, bl, "\"", a, (size_t)(b - a));
        } else if (i == g_nknown && a < b) ul = list_add(unknown, sizeof unknown, ul, "\"", a, (size_t)(b - a));
        p = *end ? end + 1 : end;
    }
    for (unsigned i = 0; i < g_nknown; i++) {
        if (on & (1u << i))
            al = list_add(active, sizeof active, al, "", g_known[i].report, strlen(g_known[i].report));
        if (g_known[i].has) kl = list_add(known, sizeof known, kl, "", g_known[i].name, strlen(g_known[i].name));
    }
    char why[768] = "";
    if (absent[0])
        snprintf(why, sizeof why, "; this game was built without the code-patch tweak%s %s, so %s ignored",
                 strchr(absent, ',') ? "s" : "", absent, strchr(absent, ',') ? "they are" : "it is");
    if (unknown[0])
        snprintf(why + strlen(why), sizeof why - strlen(why), "; no tweak is named %s, so it is ignored; "
                 "the tweaks: %s", unknown, known);
    snprintf(line, n, "sa2 tweaks: %s (SA2_TWEAKS=%s%s)", on ? active : "none on", value, why);
    return on;
}

static uint32_t g_tweaks;           /* the active tweaks, read once at the start */

int sa2_tweak_on(unsigned bit) {
    return (g_tweaks & bit) != 0;
}

int sa2_tweak_patch_on(unsigned row) {
    return row < g_npatch && (g_tweaks & g_patch_bit[row]) != 0;
}

__attribute__((constructor)) static void sa2_tweaks_read(void) {
    char line[1024];
    const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
    g_tweaks = sa2_tweaks_parse(getenv("SA2_TWEAKS"), replay, line, sizeof line);
    if (line[0]) fprintf(stderr, "%s\n", line);
}
