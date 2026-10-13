/* Whole-image link probe: prove the linked program's function table, without
 * executing one guest instruction.
 *
 * This is the `main` of the whole-image link (S3 slice 2, extended by slice 6
 * to the arms-seeded build). It links against the runtime and against every
 * lifted object, so the link itself is the first thing it proves. Then it
 * initialises the runtime exactly as a host would -- guest memory from the
 * user's own executable, the generated function table, the signal handlers --
 * and measures what design 3.4's hash table does at image scale: load factor,
 * what insertion and lookup each cost, that every registered address resolves
 * to the pointer it was registered with, and -- the question the stage
 * actually turns on -- what the table answers for every jump-table arm address
 * in the analyzer's map.
 *
 * It never calls a lifted function. Running guest code is `harness/seh_probe.c`
 * and `tools/s3/arms/arm_probe.c`; a link probe that ran one would be measuring
 * the runtime, not the link.
 *
 * ---- what this file used to do, and why it stopped -----------------------
 * Until slice 6 the probe carried its own copy of the runtime's index
 * (`hash_va(va) & mask`) so that it could locate the slot array and walk it,
 * because `sa2_ft_find` could not report how deep it had probed. Slice 5 made
 * the index the *high* bits of the same multiply and gave the runtime
 * `sa2_ft_lookups`, `sa2_ft_lookup_probes` and `sa2_ft_max_lookup_probe`. The
 * private copy then disagreed with the runtime and the instrument went dead --
 * `occupied_slots` read 12 292 against a table of 12 683 -- which is the right
 * failure and not a fix. The fix is this file: **the instrument is deleted**.
 * Every cost below is a delta of the runtime's own counters across the one
 * `sa2_ft_find` call it measures, so nothing here re-derives the index, and a
 * future change to the index cannot make these numbers mean something else.
 *
 * Output is one JSON document on stdout, so the tool that runs it records
 * numbers rather than prose.
 *
 * Usage: sa2_whole <pe-path> [arms-file]
 * The arms file is `private/generated/s3/link/arms.<tag>.txt`, written by
 * `link_all.py --phase arms`. Format v2:
 *
 *     <occurrences> <predicted hits> v2
 *     <arm address in hex> <1 if this build is predicted to resolve it, else 0>
 *     ...
 *
 * The per-address column is what makes the control falsifiable now that the
 * answer is no longer "4 for every build": it is computed by `link_all.py`
 * from the analyzer's map and the arm-lift log, neither of which is the
 * function table this file is asking. A v1 file (addresses only, one total)
 * is refused rather than read, because reading it would answer a question
 * about a different build.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "function_table.h"
#include "guest_memory.h"
#include "sa2_call.h"

const sa2_ft_desc *sa2_ft_generated_descs(void);

/* `__sa2_dispatch_stub` is no longer defined here. It has a weak definition in
 * `runtime/function_table_impl.c` (slice 5), which is why this file's strong
 * copy existed and why it does not any more. */

static double now_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + 1e-9 * (double)t.tv_nsec;
}

typedef struct {
    uint32_t va;
    uint8_t  expect_hit;
} arm_row;

static int cmp_arm(const void *a, const void *b) {
    uint32_t x = ((const arm_row *)a)->va, y = ((const arm_row *)b)->va;
    return (x < y) ? -1 : (x > y) ? 1 : 0;
}

#define HIST_MAX 64

typedef struct {
    uint32_t n;                  /* lookups measured */
    uint64_t probes_total;       /* occupied slots walked, from the runtime */
    uint32_t probes_max;
    uint32_t hist[HIST_MAX + 1]; /* [HIST_MAX] is "that many or more" */
} cost_stats;

static void record(cost_stats *m, uint32_t probes) {
    m->n++;
    m->probes_total += probes;
    if (probes > m->probes_max) m->probes_max = probes;
    m->hist[probes < HIST_MAX ? probes : HIST_MAX]++;
}

static void print_cost(const char *key, const cost_stats *m, const char *tail) {
    const double mean = m->n ? (double)m->probes_total / (double)m->n : 0.0;
    printf("      \"%s\": {\"lookups\": %u, \"mean_occupied_slots_walked\": %.4f, "
           "\"max_occupied_slots_walked\": %u, \"mean_slots_examined\": %.4f, "
           "\"max_slots_examined\": %u, \"histogram\": {",
           key, m->n, mean, m->probes_max, mean + 1.0, m->probes_max + 1);
    int first = 1;
    for (int d = 0; d <= HIST_MAX; d++) {
        if (!m->hist[d]) continue;
        printf("%s\"%s%d\": %u", first ? "" : ", ", d == HIST_MAX ? ">=" : "", d,
               m->hist[d]);
        first = 0;
    }
    printf("}}%s\n", tail);
}

/* One lookup, priced by the runtime rather than by a copy of it. */
static const sa2_ft_entry *timed_find(uint32_t va, uint32_t *probes) {
    const uint64_t before = sa2_ft_lookup_probes();
    const sa2_ft_entry *e = sa2_ft_find(va);
    *probes = (uint32_t)(sa2_ft_lookup_probes() - before);
    return e;
}

int main(int argc, char **argv) {
    const char *pe = argc > 1 ? argv[1] : "private/exe/sonic2app-unpacked.exe";
    const char *arms_path = argc > 2 ? argv[2] : NULL;
    const double t0 = now_s();
    const int rc = sa2_runtime_init(pe, 0);
    const double init_s = now_s() - t0;
    if (rc != SA2_OK) {
        printf("{\"ok\":false,\"stage\":\"sa2_runtime_init\",\"rc\":%d}\n", rc);
        return 1;
    }

    const uint32_t count = sa2_ft_count();
    const uint32_t capacity = sa2_ft_capacity();
    const sa2_ft_desc *descs = sa2_ft_generated_descs();
    const uint32_t n = sa2_ft_generated_count();

    /* Insertion cost, straight out of the registrations that already happened
     * inside `sa2_runtime_init`. Nothing re-runs them. */
    const uint64_t add_probes = sa2_ft_add_probes();
    const uint32_t max_add_probe = sa2_ft_max_probe();

    /* Every registered address must resolve to the pointer it was registered
     * with. A table that answers a lookup with the wrong function is worse
     * than one that misses, and nothing else in the program would notice.
     *
     * Note what this can and cannot fail on: it queries the table with the
     * same descriptor array the table was built from, so it tests the lookup
     * path against the insertion path -- collision handling, the index, the
     * capacity rule -- and not whether the table's contents are the right
     * contents. For that, the addresses have to come from somewhere other than
     * the table's own input; the arm probe below is where they do. */
    uint32_t resolved = 0, wrong = 0, missing = 0;
    uint32_t first_bad = 0;
    cost_stats hit_cost = {0};
    for (uint32_t i = 0; i < n; i++) {
        uint32_t probes = 0;
        const sa2_ft_entry *e = timed_find(descs[i].va, &probes);
        record(&hit_cost, probes);
        if (!e) {
            if (!missing++) first_bad = descs[i].va;
        } else if (e->fn != descs[i].fn) {
            if (!wrong++) first_bad = descs[i].va;
        } else {
            resolved++;
        }
    }
    /* The worst lookup the table has served so far, all of them hits. Kept
     * apart from the figure at the end of the run, which includes the misses:
     * a miss walks to an empty slot and a hit stops at the entry, so the two
     * are different questions and slice 5's header says so. */
    const uint32_t max_probe_hits_only = sa2_ft_max_lookup_probe();

    /* Lookup wall cost: every registered address, five times, so the
     * measurement is of the table and not of the loop around it. Measured here,
     * before the arm probe, so it stays comparable with earlier runs. */
    volatile uint64_t sink = 0;
    const double t1 = now_s();
    for (int pass = 0; pass < 5; pass++)
        for (uint32_t i = 0; i < n; i++)
            sink += (uint64_t)(uintptr_t)sa2_ft_lookup(descs[i].va);
    const double lookup_s = now_s() - t1;
    (void)sink;

    /* ---- the control: every jump-table arm the analyzer's map lists -------
     * Whether an arm dispatch is a miss is the claim this stage rests on, and
     * the answer changed when the arms were seeded and given table rows. So the
     * arms file carries a *per-address* prediction, computed from the map and
     * the arm-lift log, and this asks the linked program each one. The probe
     * fails on any address where the table and the prediction disagree -- in
     * either direction, which is what makes it able to fail: run an arms-free
     * binary against a seeded prediction and thousands of rows mismatch; run a
     * seeded binary against a map-only prediction and the same thousands
     * mismatch the other way.
     *
     * This replaced a control that could not fail: three hand-picked addresses
     * far outside the image, looked up in a table that has no deletions, where
     * linear probing cannot produce a false hit. It reported "0 of 3" forever. */
    uint32_t arm_n = 0, arm_expect_hits = 0, arm_hits = 0, arm_misses = 0;
    uint32_t arm_distinct = 0, arm_distinct_hits = 0;
    uint32_t mismatch_hit_not_predicted = 0, mismatch_predicted_not_hit = 0;
    int arms_ok = 0, arms_read = 0, arms_version_ok = 0;
    cost_stats by_occurrence = {0}, by_distinct = {0};
    /* `hits` is exact; this names the first 16 of them, and the first 8
     * mismatches, which is what a failure needs to be diagnosable. */
    uint32_t hit_va[16];
    const char *hit_name[16];
    uint32_t hit_shown = 0;
    uint32_t bad_va[8];
    uint8_t  bad_expected[8];
    uint32_t bad_shown = 0;
    arm_row *arms = NULL;
    if (arms_path) {
        FILE *f = fopen(arms_path, "r");
        char ver[8] = {0};
        if (f && fscanf(f, "%u %u %7s", &arm_n, &arm_expect_hits, ver) == 3 && arm_n) {
            arms_version_ok = !strcmp(ver, "v2");
            if (arms_version_ok) {
                arms = (arm_row *)malloc((size_t)arm_n * sizeof *arms);
                uint32_t have = 0, va = 0, want = 0;
                while (have < arm_n && fscanf(f, "%x %u", &va, &want) == 2) {
                    arms[have].va = va;
                    arms[have].expect_hit = (uint8_t)(want != 0);
                    have++;
                }
                arms_read = (have == arm_n);
                for (uint32_t i = 0; i < have; i++) {
                    uint32_t probes = 0;
                    const sa2_ft_entry *e = timed_find(arms[i].va, &probes);
                    if (e) {
                        arm_hits++;
                        if (hit_shown < 16) {
                            hit_va[hit_shown] = e->va;
                            hit_name[hit_shown] = e->name;
                            hit_shown++;
                        }
                    } else {
                        arm_misses++;
                        record(&by_occurrence, probes);
                    }
                    if ((e != NULL) != (arms[i].expect_hit != 0)) {
                        if (e) mismatch_hit_not_predicted++;
                        else mismatch_predicted_not_hit++;
                        if (bad_shown < 8) {
                            bad_va[bad_shown] = arms[i].va;
                            bad_expected[bad_shown] = arms[i].expect_hit;
                            bad_shown++;
                        }
                    }
                }
                /* The same question over distinct addresses: the map lists some
                 * arms in more than one table, and an occurrence-weighted mean
                 * is not the mean over the address set. */
                qsort(arms, have, sizeof *arms, cmp_arm);
                for (uint32_t i = 0; i < have; i++) {
                    if (i && arms[i].va == arms[i - 1].va) continue;
                    arm_distinct++;
                    uint32_t probes = 0;
                    if (timed_find(arms[i].va, &probes)) arm_distinct_hits++;
                    else record(&by_distinct, probes);
                }
                free(arms);
                arms_ok = arms_read && arm_hits == arm_expect_hits &&
                          mismatch_hit_not_predicted == 0 &&
                          mismatch_predicted_not_hit == 0;
            }
        }
        if (f) fclose(f);
    }

    const uint32_t max_probe_all = sa2_ft_max_lookup_probe();
    const uint64_t lookups = sa2_ft_lookups();
    const uint64_t lookup_probes = sa2_ft_lookup_probes();

    const int ok = (missing == 0 && wrong == 0 && count == n && arms_ok);
    printf("{\n");
    printf("  \"ok\": %s,\n", ok ? "true" : "false");
    printf("  \"image\": \"%s\",\n", sa2_guest.image_path);
    printf("  \"image_size\": %u,\n", sa2_guest.image_size);
    printf("  \"sections\": %u,\n", sa2_guest.section_count);
    printf("  \"runtime_init_seconds\": %.4f,\n", init_s);
    printf("  \"table\": {\n");
    printf("    \"generated_rows\": %u,\n", n);
    printf("    \"entries\": %u,\n", count);
    printf("    \"capacity\": %u,\n", capacity);
    printf("    \"load_factor\": %.6f,\n", capacity ? (double)count / (double)capacity : 0.0);
    printf("    \"max_probe\": %u,\n", max_add_probe);
    printf("    \"insertion\": {\"registrations\": %u, \"extra_probes\": %llu, "
           "\"mean_extra_probes\": %.4f, \"max_probe\": %u, "
           "\"note\": \"an insertion stops at the first empty slot; a lookup does not\"},\n",
           count, (unsigned long long)add_probes,
           count ? (double)add_probes / (double)count : 0.0, max_add_probe);
    printf("    \"lookup\": {\"lookups\": %llu, \"occupied_slots_walked\": %llu, "
           "\"mean\": %.4f, \"max_over_hits_only\": %u, \"max_over_all\": %u, "
           "\"source\": \"runtime counters (sa2_ft_lookups / _lookup_probes / "
           "_max_lookup_probe); the probe keeps no index of its own\"},\n",
           (unsigned long long)lookups, (unsigned long long)lookup_probes,
           lookups ? (double)lookup_probes / (double)lookups : 0.0,
           max_probe_hits_only, max_probe_all);
    printf("    \"bytes\": %zu\n", (size_t)capacity * sizeof(sa2_ft_entry));
    printf("  },\n");
    printf("  \"resolution\": {\"resolved\": %u, \"wrong_pointer\": %u, \"missing\": %u, "
           "\"first_bad\": \"0x%08x\", \"note\": \"queried with the descriptors the "
           "table was built from: it tests the lookup path, not the contents\",\n",
           resolved, wrong, missing, first_bad);
    print_cost("hit_cost", &hit_cost, "");
    printf("  },\n");
    printf("  \"arm_dispatch\": {\n");
    printf("    \"source\": \"%s\",\n", arms_path ? arms_path : "");
    printf("    \"arms_file_version_ok\": %s,\n", arms_version_ok ? "true" : "false");
    printf("    \"arms_read\": %s,\n", arms_read ? "true" : "false");
    printf("    \"arm_occurrences\": %u,\n", arm_n);
    printf("    \"distinct_arms\": %u,\n", arm_distinct);
    printf("    \"distinct_arms_that_hit\": %u,\n", arm_distinct_hits);
    printf("    \"predicted_hits\": %u,\n", arm_expect_hits);
    printf("    \"hits\": %u,\n", arm_hits);
    printf("    \"misses\": %u,\n", arm_misses);
    printf("    \"mismatches\": {\"hit_but_predicted_a_miss\": %u, "
           "\"missed_but_predicted_a_hit\": %u, \"first\": [",
           mismatch_hit_not_predicted, mismatch_predicted_not_hit);
    for (uint32_t i = 0; i < bad_shown; i++)
        printf("%s{\"arm\": \"0x%08x\", \"predicted\": \"%s\"}", i ? ", " : "",
               bad_va[i], bad_expected[i] ? "hit" : "miss");
    printf("]},\n");
    printf("    \"agrees_with_the_prediction\": %s,\n", arms_ok ? "true" : "false");
    printf("    \"hit_entries\": [");
    for (uint32_t i = 0; i < hit_shown; i++)
        printf("%s{\"entry_va\": \"0x%08x\", \"entry_name\": \"%s\"}",
               i ? ", " : "", hit_va[i], hit_name[i] ? hit_name[i] : "");
    printf("],\n");
    printf("    \"miss_cost\": {\n");
    print_cost("by_occurrence", &by_occurrence, ",");
    print_cost("by_distinct_address", &by_distinct, "");
    printf("    }\n");
    printf("  },\n");

    printf("  \"lookup\": {\"lookups\": %llu, \"seconds\": %.4f, \"ns_each\": %.2f}\n",
           (unsigned long long)n * 5ull, lookup_s,
           n ? lookup_s * 1e9 / ((double)n * 5.0) : 0.0);
    printf("}\n");

    sa2_runtime_shutdown();
    return ok ? 0 : 1;
}
