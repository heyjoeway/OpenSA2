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
 * does not name, which is a test fixture; at most SA2_TWEAK_MAX (64) in all. A code-patch tweak in
 * the list (SA2_TWEAK_PATCH_LIST) that the game does not link is one it was built without.
 *
 * The value is split at commas, each name trimmed of spaces and tabs and matched exactly (case
 * counts); a name given twice is one, an empty one is skipped, and one that is no tweak is named in
 * the report line and ignored, with the list of the tweaks there are, and so is a code-patch tweak
 * the game was built without. Two tweaks that cannot both be on (SA2_TWEAK_EXCLUSIVE) are both
 * left off when the value names both, and the line names them. A host tweak's name
 * (SA2_TWEAK_HOST_LIST) is the host's: the line names it, with its home, as not the game's, turns
 * nothing on for it, and lists the host tweaks after the game's when it lists the tweaks there are.
 * In a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS set) the whole value is ignored, and the line
 * says so.
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

/* The report line's lists hold every tweak's name and report, SA2_TWEAK_MAX of them, each with its
 * ", " (list_add): a name is at most TWEAK_NAME_MAX characters and a report at most
 * TWEAK_REPORT_MAX, held here for the list and by tools/s8/variants/variants.py for a spec. */
#define TWEAK_NAME_MAX 40
#define TWEAK_REPORT_MAX 60
#define TWEAK_FITS(bit, name, report, summary) \
    _Static_assert(sizeof name - 1 <= TWEAK_NAME_MAX, "the tweak name " name " is longer than TWEAK_NAME_MAX"); \
    _Static_assert(sizeof report - 1 <= TWEAK_REPORT_MAX, "the report of " name " is longer than TWEAK_REPORT_MAX");
SA2_TWEAK_LIST(TWEAK_FITS)
#undef TWEAK_FITS

/* The host tweaks (sa2_tweaks.h, "Add a host tweak"), ended by a row with no name, which also
 * makes an empty list a valid array. */
static const struct { const char *name, *home; } g_host_rows[] = {
#define TWEAK_HOST_ROW(bit, name, report, summary, home) {name, home},
    SA2_TWEAK_HOST_LIST(TWEAK_HOST_ROW)
#undef TWEAK_HOST_ROW
    {NULL, NULL}
};
#define TWEAK_HOST_MAX 32      /* host/player/tweaks.h: the host's mask is a uint32_t */
_Static_assert(sizeof g_host_rows / sizeof g_host_rows[0] <= TWEAK_HOST_MAX + 1,
               "SA2_TWEAK_HOST_LIST holds more tweaks than the host's 32-bit mask has bits");
#define TWEAK_HOST_FITS(bit, name, report, summary, home) \
    _Static_assert(sizeof name - 1 <= TWEAK_NAME_MAX, "the host tweak name " name " is longer than TWEAK_NAME_MAX");
SA2_TWEAK_HOST_LIST(TWEAK_HOST_FITS)
#undef TWEAK_HOST_FITS

/* Whether the name in [a, b) is `name`. */
static int name_is(const char *a, const char *b, const char *name) {
    return strlen(name) == (size_t)(b - a) && memcmp(name, a, (size_t)(b - a)) == 0;
}

/* The code-patch tweaks a game built without tools/s8/variants/variants.py's file links: none. */
__attribute__((weak)) const sa2_tweak_patch *sa2_tweak_patches_linked(unsigned *count) {
    *count = 0;
    return NULL;
}

/* Every tweak this game has, by bit (the header comment); `has` is 0 for a code-patch tweak in the
 * list that the game does not link. g_patch_bit[i] is the bit of sa2_tweak_patches_linked's row i. */
static struct { const char *name, *report; int has; } g_known[SA2_TWEAK_MAX];
static unsigned g_nknown, g_npatch;
static uint64_t g_patch_bit[SA2_TWEAK_MAX];

static void known_read(void) {
    static int done;
    if (done++) return;
    for (unsigned i = 0; i < SA2_TWEAK_COUNT; i++) {
        g_known[i].name = g_tweak_rows[i].name;
        g_known[i].report = g_tweak_rows[i].report;
        g_known[i].has = !(SA2_TWEAK_PATCHES & (1ull << i));
    }
    g_nknown = SA2_TWEAK_COUNT;
    unsigned n = 0;
    const sa2_tweak_patch *rows = sa2_tweak_patches_linked(&n);
    g_npatch = n < SA2_TWEAK_MAX ? n : SA2_TWEAK_MAX;
    for (unsigned r = 0; r < g_npatch; r++) {
        unsigned i = 0;
        while (i < g_nknown && strcmp(g_known[i].name, rows[r].name) != 0) i++;
        if (i == g_nknown) {
            if (g_nknown == SA2_TWEAK_MAX) continue;   /* no bit left: variants.py refuses such a build */
            g_known[g_nknown].name = rows[r].name;
            g_known[g_nknown++].report = rows[r].report;
        }
        g_known[i].has = 1;
        g_patch_bit[r] = 1ull << i;
    }
}

/* SA2_TWEAK_EXCLUSIVE's pairs, by bit and by row, then an empty row that ends the table. */
static const struct { uint64_t a, b; unsigned ia, ib; } g_tweak_clashes[] = {
#define TWEAK_PAIR(a, b) {a, b, a##_INDEX, b##_INDEX},
    SA2_TWEAK_EXCLUSIVE(TWEAK_PAIR)
#undef TWEAK_PAIR
    {0, 0, 0, 0},
};

/* `s` (n bytes, between `quote`s) appended to the comma-separated list in buf[0, len); the new
 * length, cap once the list is cut short. */
static size_t list_add(char *buf, size_t cap, size_t len, const char *quote, const char *s, size_t n) {
    if (len >= cap) return cap;
    const int w = snprintf(buf + len, cap - len, "%s%s%.*s%s", len ? ", " : "", quote, (int)n, s, quote);
    return w < 0 ? cap : len + (size_t)w;
}

uint64_t sa2_tweaks_parse(const char *value, int replay, char *line, size_t n) {
    line[0] = 0;
    if (!value || !*value) return 0;
    if (replay) {
        snprintf(line, n, "sa2 tweaks: SA2_TWEAKS=%s ignored in a replay (SA2_REPLAY_TICKS or "
                 "SA2_REPLAY_WORDS is set)", value);
        return 0;
    }
    known_read();
    uint64_t on = 0, without = 0;
    uint32_t host = 0;
    char unknown[1024] = "", absent[1024] = "";
    char active[SA2_TWEAK_MAX * (TWEAK_REPORT_MAX + 2)] = "", known[SA2_TWEAK_MAX * (TWEAK_NAME_MAX + 2)] = "";
    size_t ul = 0, bl = 0, al = 0, kl = 0;
    for (const char *p = value; *p;) {
        const char *end = strchr(p, ',');
        if (!end) end = p + strlen(p);
        const char *a = p, *b = end;
        while (a < b && (*a == ' ' || *a == '\t')) a++;
        while (b > a && (b[-1] == ' ' || b[-1] == '\t')) b--;
        unsigned i = 0, h = 0;
        while (i < g_nknown && !name_is(a, b, g_known[i].name)) i++;
        while (i == g_nknown && g_host_rows[h].name && !name_is(a, b, g_host_rows[h].name)) h++;
        if (i == g_nknown && g_host_rows[h].name) host |= 1u << h;
        else if (i < g_nknown && g_known[i].has) on |= 1ull << i;
        else if (i < g_nknown && !(without & (1ull << i))) {
            without |= 1ull << i;
            bl = list_add(absent, sizeof absent, bl, "\"", a, (size_t)(b - a));
        } else if (i == g_nknown && a < b) ul = list_add(unknown, sizeof unknown, ul, "\"", a, (size_t)(b - a));
        p = *end ? end + 1 : end;
    }
    char clash[256] = "";
    size_t cl = 0;
    for (size_t k = 0; k + 1 < sizeof g_tweak_clashes / sizeof g_tweak_clashes[0]; k++) {
        const uint64_t pair = g_tweak_clashes[k].a | g_tweak_clashes[k].b;
        if ((on & pair) != pair) continue;
        on &= ~pair;
        if (cl < sizeof clash) {
            const int w = snprintf(clash + cl, sizeof clash - cl, "; %s and %s cannot both be on, so neither is",
                                   g_tweak_rows[g_tweak_clashes[k].ia].name, g_tweak_rows[g_tweak_clashes[k].ib].name);
            cl = w < 0 ? sizeof clash : cl + (size_t)w;
        }
    }
    for (unsigned i = 0; i < g_nknown; i++) {
        if (on & (1ull << i))
            al = list_add(active, sizeof active, al, "", g_known[i].report, strlen(g_known[i].report));
        if (g_known[i].has) kl = list_add(known, sizeof known, kl, "", g_known[i].name, strlen(g_known[i].name));
    }
    /* The host tweaks named, each with its home, and every host tweak, in the host list's order. */
    char hosts[TWEAK_HOST_MAX * (TWEAK_NAME_MAX + 24)] = "", host_list[TWEAK_HOST_MAX * (TWEAK_NAME_MAX + 2)] = "";
    char named[128];
    size_t hl = 0, hk = 0;
    for (unsigned h = 0; g_host_rows[h].name; h++) {
        if (host & (1u << h)) {
            snprintf(named, sizeof named, "\"%s\" (the %s's)", g_host_rows[h].name, g_host_rows[h].home);
            hl = list_add(hosts, sizeof hosts, hl, "", named, strlen(named));
        }
        hk = list_add(host_list, sizeof host_list, hk, "", g_host_rows[h].name, strlen(g_host_rows[h].name));
    }
    char why[sizeof clash + sizeof absent + sizeof hosts + sizeof unknown + sizeof known + sizeof host_list + 200] = "";
    snprintf(why, sizeof why, "%s", clash);
    if (absent[0])
        snprintf(why + strlen(why), sizeof why - strlen(why),
                 "; this game was built without the code-patch tweak%s %s, so %s ignored", strchr(absent, ',') ? "s" : "", absent, strchr(absent, ',') ? "they are" : "it is");
    if (hosts[0])
        snprintf(why + strlen(why), sizeof why - strlen(why), "; host tweak%s, not the game's: %s",
                 host & (host - 1) ? "s" : "", hosts);
    if (unknown[0])
        snprintf(why + strlen(why), sizeof why - strlen(why), "; no tweak is named %s, so it is ignored; "
                 "the tweaks: %s%s%s", unknown, known, host_list[0] ? "; the host tweaks: " : "", host_list);
    snprintf(line, n, "sa2 tweaks: %s (SA2_TWEAKS=%s%s)", on ? active : "none on", value, why);
    return on;
}

static uint64_t g_tweaks;           /* the active tweaks, read once at the start */

int sa2_tweak_on(uint64_t bit) {
    return (g_tweaks & bit) != 0;
}

int sa2_tweak_patch_on(unsigned row) {
    return row < g_npatch && (g_tweaks & g_patch_bit[row]) != 0;
}

__attribute__((constructor)) static void sa2_tweaks_read(void) {
    char line[16384];
    const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
    g_tweaks = sa2_tweaks_parse(getenv("SA2_TWEAKS"), replay, line, sizeof line);
    if (line[0]) fprintf(stderr, "%s\n", line);
}
