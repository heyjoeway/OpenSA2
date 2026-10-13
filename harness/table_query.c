/* Ask a linked whole-image binary what its function table answers for a set of
 * addresses, grouped by where the addresses came from.
 *
 *   usage: table_query <sonic2app-unpacked.exe> <addresses-file>
 *
 * `tools/s3/whole_image/link_probe.c` asks one question of one set, the
 * jump-table arms, and checks it against a prediction. This asks the same
 * question of every kind of dispatch target the analyzer can name -- arms,
 * import-address-table slots, missing-block edge targets, the entry points
 * themselves -- so that "the remaining trap surface" is a measurement against a
 * binary rather than a count of static sites.
 *
 * The distinction that makes the number mean something: a *dispatch site* is a
 * place in the code that will consult the table, and a *dispatch target* is an
 * address the table is asked about. 2 087 `__remill_jump` sites can share a few
 * hundred targets, or reach thousands. Only the targets can be resolved or not,
 * and only some of them are knowable before the program runs -- an indirect
 * call through a computed pointer is not in any list. The output reports the
 * knowable ones as measured and the rest as what they are: unknown until a run
 * produces the value.
 *
 * Input format, one per line, `#` comments ignored:
 *
 *     <hex address> <group>
 *
 * A group is any short name; the totals are reported per group and overall,
 * over occurrences and over distinct addresses.
 *
 * Output is one JSON document on stdout. Exit status is 0 if the file was read.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/sa2_call.h"

#define MAX_GROUPS 32
#define NAME_MAX 48
#define SAMPLE 8

typedef struct {
    char     name[NAME_MAX];
    uint32_t occurrences, hits, misses;
    uint32_t distinct, distinct_hits, distinct_misses;
    uint32_t sample_miss[SAMPLE];
    uint32_t sample_n;
} group_t;

static group_t g_group[MAX_GROUPS];
static int g_ngroups;

static int group_of(const char *name) {
    for (int i = 0; i < g_ngroups; i++)
        if (!strcmp(g_group[i].name, name)) return i;
    if (g_ngroups == MAX_GROUPS) return -1;
    snprintf(g_group[g_ngroups].name, NAME_MAX, "%s", name);
    return g_ngroups++;
}

typedef struct { uint32_t va; int16_t group; } row_t;

static int cmp_row(const void *a, const void *b) {
    const row_t *x = a, *y = b;
    if (x->va != y->va) return x->va < y->va ? -1 : 1;
    return x->group - y->group;
}

int main(int argc, char **argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: table_query <exe> <addresses-file>\n");
        return 2;
    }
    if (sa2_runtime_init(argv[1], 0) != SA2_OK) {
        fprintf(stderr, "table_query: sa2_runtime_init(%s) failed\n", argv[1]);
        return 2;
    }
    FILE *f = fopen(argv[2], "r");
    if (!f) { fprintf(stderr, "table_query: cannot open %s\n", argv[2]); return 2; }

    size_t cap = 1 << 16, n = 0;
    row_t *rows = malloc(cap * sizeof *rows);
    char line[256];
    while (fgets(line, sizeof line, f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        unsigned va = 0;
        char name[NAME_MAX] = "";
        if (sscanf(line, "%x %47s", &va, name) != 2) continue;
        const int gi = group_of(name);
        if (gi < 0) continue;
        if (n == cap) { cap *= 2; rows = realloc(rows, cap * sizeof *rows); }
        rows[n].va = va;
        rows[n].group = (int16_t)gi;
        n++;
    }
    fclose(f);

    /* Occurrences. */
    for (size_t i = 0; i < n; i++) {
        group_t *g = &g_group[rows[i].group];
        g->occurrences++;
        if (sa2_ft_find(rows[i].va)) g->hits++;
        else g->misses++;
    }
    /* Distinct, per group and overall. The overall figure is not the sum of the
     * per-group ones: an address can be in two groups, and the whole point of
     * "the remaining trap surface" is a count of addresses, not of listings. */
    qsort(rows, n, sizeof *rows, cmp_row);
    uint32_t distinct = 0, distinct_hits = 0, distinct_misses = 0;
    for (size_t i = 0; i < n; i++) {
        group_t *g = &g_group[rows[i].group];
        const int new_in_group = !(i && rows[i].va == rows[i - 1].va &&
                                   rows[i].group == rows[i - 1].group);
        const int new_overall = !(i && rows[i].va == rows[i - 1].va);
        if (!new_in_group && !new_overall) continue;
        const int hit = sa2_ft_find(rows[i].va) != NULL;
        if (new_in_group) {
            g->distinct++;
            if (hit) g->distinct_hits++;
            else {
                g->distinct_misses++;
                if (g->sample_n < SAMPLE) g->sample_miss[g->sample_n++] = rows[i].va;
            }
        }
        if (new_overall) {
            distinct++;
            if (hit) distinct_hits++;
            else distinct_misses++;
        }
    }

    printf("{\n");
    printf("  \"input\": \"%s\",\n", argv[2]);
    printf("  \"table_rows\": %u,\n", sa2_ft_count());
    printf("  \"rows_read\": %zu,\n", n);
    printf("  \"groups\": [\n");
    for (int i = 0; i < g_ngroups; i++) {
        const group_t *g = &g_group[i];
        printf("    {\"group\": \"%s\", \"occurrences\": %u, \"hits\": %u, \"misses\": %u, "
               "\"distinct\": %u, \"distinct_hits\": %u, \"distinct_misses\": %u, "
               "\"miss_sample\": [", g->name, g->occurrences, g->hits, g->misses,
               g->distinct, g->distinct_hits, g->distinct_misses);
        for (uint32_t k = 0; k < g->sample_n; k++)
            printf("%s\"0x%08x\"", k ? ", " : "", g->sample_miss[k]);
        printf("]}%s\n", i + 1 < g_ngroups ? "," : "");
    }
    printf("  ],\n");
    printf("  \"distinct_over_all_groups\": {\"addresses\": %u, \"resolved\": %u, "
           "\"unresolved\": %u},\n", distinct, distinct_hits, distinct_misses);
    printf("  \"ok\": true\n}\n");
    free(rows);
    sa2_runtime_shutdown();
    return 0;
}
