/* What the function table's index costs, measured by running the real table.
 *
 *   usage: ft_probe <addresses.txt> [miss-addresses.txt] [--max-mean F]
 *
 * `tools/s3/whole_image/table_probe.py` simulates `sa2_ft_add`'s probe sequence
 * in Python. A simulation of an index is a second implementation of that index,
 * and a second implementation can agree with a wrong first one or disagree with
 * a right one; either way it measures itself. This links
 * `runtime/function_table_impl.c` and asks it.
 *
 * Two costs, kept apart because they differ:
 *
 *   * **Insertion.** `sa2_ft_max_probe` is an insertion high-water mark. An
 *     insertion stops at the first empty slot.
 *   * **Lookup.** A hit walks to its entry and a *miss* walks to an empty slot,
 *     so a miss can cost more than any insertion did. The whole-image table
 *     reported max_probe 15 while a real lookup walked further, and nothing
 *     counted it. The runtime counts it now and this reads it back.
 *
 * Per-address probe counts come from the deltas of the runtime's own cumulative
 * counters across one call, so nothing here re-derives the hash or the probe
 * sequence -- if it did, it would be the simulation again.
 *
 * Exit status is the point: `--max-mean F` fails the probe when the mean extra
 * probes per insertion is at least F. Run it with the shipped low-bit index and
 * it fails; that is the demonstration that it can. Every registered address
 * must also find back the entry it was registered with, which fails if an index
 * change makes `sa2_ft_add` and `sa2_ft_find` disagree -- the one way to make
 * the probe statistics beautiful and the table useless.
 *
 * Output is one JSON document on stdout.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/function_table.h"

/* Any non-null pointer registers; nothing here calls a lifted function. */
static void *stub_fn(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc; return memory;
}

static uint32_t *read_addresses(const char *path, uint32_t *count_out) {
    FILE *f = fopen(path, "r");
    if (!f) { perror(path); return NULL; }
    uint32_t cap = 1024, n = 0;
    uint32_t *a = (uint32_t *)malloc(cap * sizeof *a);
    char line[256];
    while (a && fgets(line, sizeof line, f)) {
        char *p = line;
        while (*p == ' ' || *p == '\t') p++;
        if (*p == '#' || *p == '\n' || *p == '\r' || !*p) continue;
        char *end = NULL;
        unsigned long v = strtoul(p, &end, 16);
        /* A header line such as "5817 4" carries no 0x and parses as one number
         * with trailing text; only accept a line that is wholly one address. */
        if (end == p) continue;
        while (*end == ' ' || *end == '\t') end++;
        if (*end && *end != '\n' && *end != '\r') continue;
        if (n == cap) {
            cap *= 2;
            uint32_t *bigger = (uint32_t *)realloc(a, cap * sizeof *a);
            if (!bigger) { free(a); a = NULL; break; }
            a = bigger;
        }
        a[n++] = (uint32_t)v;
    }
    fclose(f);
    *count_out = n;
    return a;
}

#define HIST 33   /* [HIST-1] is "that many or more" */

typedef struct {
    uint32_t n, max;
    uint64_t total;
    uint32_t hist[HIST];
} cost_t;

static void record(cost_t *c, uint32_t probe) {
    c->n++;
    c->total += probe;
    if (probe > c->max) c->max = probe;
    c->hist[probe < HIST ? probe : HIST - 1]++;
}

static void print_cost(const char *name, const cost_t *c, const char *tail) {
    printf("  \"%s\": {\"n\": %u, \"mean_extra_probes\": %.4f, \"max\": %u, "
           "\"at_home\": %u, \"collided\": %u, \"histogram\": [",
           name, c->n, c->n ? (double)c->total / (double)c->n : 0.0, c->max,
           c->hist[0], c->n - c->hist[0]);
    uint32_t last = 0;
    for (uint32_t i = 0; i < HIST; i++) if (c->hist[i]) last = i;
    for (uint32_t i = 0; i <= last; i++) printf("%s%u", i ? ", " : "", c->hist[i]);
    printf("]}%s\n", tail);
}

int main(int argc, char **argv) {
    const char *rows_path = NULL, *miss_path = NULL;
    double max_mean = -1.0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--max-mean") && i + 1 < argc) max_mean = atof(argv[++i]);
        else if (!rows_path) rows_path = argv[i];
        else if (!miss_path) miss_path = argv[i];
    }
    if (!rows_path) {
        fprintf(stderr, "usage: ft_probe <addresses.txt> [miss-addresses.txt] [--max-mean F]\n");
        return 2;
    }

    uint32_t n = 0;
    uint32_t *rows = read_addresses(rows_path, &n);
    if (!rows || !n) { fprintf(stderr, "ft_probe: no addresses in %s\n", rows_path); return 2; }

    if (sa2_ft_init(n) != 0) { fprintf(stderr, "ft_probe: sa2_ft_init(%u) failed\n", n); return 2; }

    /* Insertion, one address at a time, reading the runtime's own counter. */
    cost_t insert; memset(&insert, 0, sizeof insert);
    uint32_t duplicates = 0, add_failures = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint64_t before = sa2_ft_add_probes();
        int rc = sa2_ft_add(rows[i], stub_fn, "probe", SA2_FN_LIFTED);
        if (rc < 0) { add_failures++; continue; }
        if (rc == 1) { duplicates++; continue; }   /* replaced a row; no insertion */
        record(&insert, (uint32_t)(sa2_ft_add_probes() - before));
    }

    /* Lookup: every registered address (hits), then the miss set if given. */
    cost_t hit; memset(&hit, 0, sizeof hit);
    uint32_t wrong = 0;
    for (uint32_t i = 0; i < n; i++) {
        uint64_t before = sa2_ft_lookup_probes();
        const sa2_ft_entry *e = sa2_ft_find(rows[i]);
        record(&hit, (uint32_t)(sa2_ft_lookup_probes() - before));
        if (!e || e->va != rows[i] || e->fn != stub_fn) wrong++;
    }

    cost_t miss; memset(&miss, 0, sizeof miss);
    uint32_t miss_n = 0, miss_hits = 0;
    uint32_t *misses = NULL;
    if (miss_path) {
        misses = read_addresses(miss_path, &miss_n);
        for (uint32_t i = 0; i < miss_n; i++) {
            uint64_t before = sa2_ft_lookup_probes();
            const sa2_ft_entry *e = sa2_ft_find(misses[i]);
            record(&miss, (uint32_t)(sa2_ft_lookup_probes() - before));
            if (e) miss_hits++;
        }
    }

    const double mean_insert = insert.n ? (double)insert.total / (double)insert.n : 0.0;
    const uint32_t worst_lookup = hit.max > miss.max ? hit.max : miss.max;
    int ok = (wrong == 0) && (add_failures == 0);
    const char *why = wrong ? "an address did not find back its own entry"
                    : add_failures ? "sa2_ft_add refused an address" : "";
    if (max_mean >= 0.0 && mean_insert >= max_mean) {
        ok = 0;
        why = "mean extra probes per insertion is at or above --max-mean";
    }

    printf("{\n");
    printf("  \"rows_file\": \"%s\",\n", rows_path);
    printf("  \"miss_file\": \"%s\",\n", miss_path ? miss_path : "");
    printf("  \"addresses\": %u, \"duplicates\": %u, \"add_failures\": %u,\n",
           n, duplicates, add_failures);
    printf("  \"count\": %u, \"capacity\": %u, \"load_factor\": %.4f,\n",
           sa2_ft_count(), sa2_ft_capacity(),
           (double)sa2_ft_count() / (double)sa2_ft_capacity());
    print_cost("insertion", &insert, ",");
    print_cost("lookup_hit", &hit, ",");
    print_cost("lookup_miss", &miss, ",");
    printf("  \"runtime_reported\": {\"max_probe\": %u, \"max_lookup_probe\": %u, "
           "\"lookups\": %llu, \"lookup_probes\": %llu},\n",
           sa2_ft_max_probe(), sa2_ft_max_lookup_probe(),
           (unsigned long long)sa2_ft_lookups(), (unsigned long long)sa2_ft_lookup_probes());
    printf("  \"worst_lookup_walk\": %u, \"lookups_wrong\": %u, \"miss_set_hits\": %u,\n",
           worst_lookup, wrong, miss_hits);
    printf("  \"max_mean\": %.4f, \"ok\": %s, \"why\": \"%s\"\n",
           max_mean, ok ? "true" : "false", why);
    printf("}\n");

    /* The runtime's own high-water marks must agree with the per-call deltas;
     * if they do not, one of the two is not measuring what it says. */
    if (sa2_ft_max_probe() != insert.max || sa2_ft_max_lookup_probe() != worst_lookup) {
        fprintf(stderr, "ft_probe: the runtime's counters disagree with the per-call "
                        "deltas (max_probe %u vs %u, max_lookup %u vs %u)\n",
                sa2_ft_max_probe(), insert.max, sa2_ft_max_lookup_probe(), worst_lookup);
        ok = 0;
    }
    free(rows);
    free(misses);
    sa2_ft_shutdown();
    return ok ? 0 : 1;
}
