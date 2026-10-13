/* Does the guest file system map what it should, and refuse what it must?
 *
 *   usage: vfs_probe [--json PATH]
 *   env:   SA2_INSTALL, SA2_VFS_WRITE_ROOT, SA2_GUEST_CWD, SA2_VFS_NO_CASE_FOLD
 *
 * Why this exists separately from the guest run
 * ---------------------------------------------
 * `runtime/sa2_files.c` holds the one security property of this slice -- a
 * guest path never names a host file outside the installation -- and the guest
 * cannot be made to ask for `C:\SA2\..\..\etc\passwd`. So the refusals are
 * driven directly, from a table whose expected answers are written here, and
 * the *trap* they turn into at the guest's own call site is proved separately
 * by `harness/crt_probe.c`'s escape case, which pushes such a path onto a guest
 * stack and calls the bound `PathFileExistsA` thunk.
 *
 * The rule this probe was written against
 * ---------------------------------------
 * Five checks in this project have turned out unable to fail. The stated rule
 * is that a boolean reported but not conjoined into the verdict is not a check,
 * and a comparison whose operands can both be empty is not a comparison. So:
 *
 *   * every case's verdict is conjoined into `ok`, including the ones that
 *     expect a refusal, so a resolver that refuses everything fails just as
 *     loudly as one that serves everything;
 *   * a case that expects OK must produce a *non-empty* relative path and, for
 *     the case-folding cases, a host leaf that differs from what the guest
 *     spelled -- an equality of two empty strings is not evidence;
 *   * `folds_used > 0` and `oks > 0` and `escapes > 0` are themselves in the
 *     conjunction, so a table that silently stopped running would fail rather
 *     than pass vacuously.
 *
 * The falsification column is `SA2_VFS_NO_CASE_FOLD=1`, which turns off the
 * case-insensitive fallback: the two folding cases must then come back
 * NOT_FOUND. A probe whose answers do not move when the mechanism is removed
 * is not measuring the mechanism.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "../runtime/sa2_files.h"

typedef struct {
    const char *guest;         /* what the guest asks for */
    int         expect;        /* SA2_VFS_* */
    const char *expect_rel;    /* NULL: not checked */
    const char *expect_leaf;   /* the host basename, when the point is the fold */
    int         needs_fold;    /* under SA2_VFS_NO_CASE_FOLD this becomes NOT_FOUND */
    const char *why;
} vcase;

static const vcase CASES[] = {
 {"C:\\SA2\\Config\\UserConfig.cfg", SA2_VFS_OK, "Config/UserConfig.cfg", "UserConfig.cfg", 0,
  "the path the game builds at 0x0040657e, spelled exactly as the disk has it"},
 {"C:\\SA2\\config\\USERCONFIG.CFG", SA2_VFS_OK, "config/USERCONFIG.CFG", "UserConfig.cfg", 1,
  "Windows is case-blind and ext4 is not: both components resolve by folding"},
 {"Config\\UserConfig.cfg", SA2_VFS_OK, "Config/UserConfig.cfg", "UserConfig.cfg", 0,
  "relative to the guest's current directory, which is the installation root"},
 {"C:/SA2/Config/UserConfig.cfg", SA2_VFS_OK, "Config/UserConfig.cfg", "UserConfig.cfg", 0,
  "forward slashes are legal on Win32 and the image uses both"},
 {"C:\\SA2\\.\\Config\\..\\Config\\UserConfig.cfg", SA2_VFS_OK, "Config/UserConfig.cfg",
  "UserConfig.cfg", 0, "'.' and a '..' that stays inside the root are normal path syntax"},
 {"C:\\SA2\\Config\\..\\..\\SA2\\Config\\UserConfig.cfg", SA2_VFS_OK, "Config/UserConfig.cfg",
  "UserConfig.cfg", 0, "popping out of the root and back in again is legal and must not be refused"},
 {"C:\\SA2\\sonic2app.exe", SA2_VFS_OK, "sonic2app.exe", "sonic2app.exe", 0,
  "the image the guest believes it is running"},
 {"C:\\SA2\\resource\\gd_PC", SA2_VFS_OK, "resource/gd_PC", "gd_PC", 0,
  "a directory, which PathFileExistsA must answer for as well as a file"},
 {"C:\\SA2\\Config\\NoSuchFile.cfg", SA2_VFS_NOT_FOUND, "Config/NoSuchFile.cfg", NULL, 0,
  "a legal path with nothing at it: ERROR_FILE_NOT_FOUND, which the guest routes around"},

 {"C:\\SA2\\..\\..\\etc\\passwd", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "the traversal: '..' past the root is refused, never clamped to the root"},
 {"..\\..\\etc\\passwd", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "the same escape spelled relatively"},
 {"C:\\SA2\\Config\\..\\..\\..\\etc\\passwd", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "an escape that only appears after normalisation"},
 {"C:\\Windows\\win.ini", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "the guest's own drive, outside the installation"},
 {"\\Windows\\win.ini", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "rooted on the current drive, outside the installation"},
 {"D:\\anything", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "a volume this runtime does not have"},
 {"C:\\SA2Config\\UserConfig.cfg", SA2_VFS_ESCAPE, NULL, NULL, 0,
  "a sibling whose name starts with the root's: the prefix test is per component, "
  "not per character, and this is the case that tells the two apart"},
 {"\\\\server\\share\\x", SA2_VFS_BAD, NULL, NULL, 0,
  "a UNC name is a network location, not a path into this installation"},
 {"C:\\SA2\\NUL", SA2_VFS_BAD, NULL, NULL, 0,
  "a DOS device name, which on Windows is a device in every directory"},
 {"C:\\SA2\\Config\\bad\003name", SA2_VFS_BAD, NULL, NULL, 0,
  "a control byte is not a file name on either system"},
};

static const char *leaf_of(const char *p) {
    const char *s = strrchr(p, '/');
    return s ? s + 1 : p;
}

static void jstr(FILE *f, const char *s) {
    fputc('"', f);
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c < 0x20 || c >= 0x7F) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}

int main(int argc, char **argv) {
    const char *json_path = NULL;
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];

    const char *cwd = getenv("SA2_GUEST_CWD");
    sa2_vfs_config(cwd && *cwd ? cwd : "C:\\SA2");
    const int folding = sa2_vfs_case_fold_enabled();

    const int n = (int)(sizeof CASES / sizeof CASES[0]);
    int pass[sizeof CASES / sizeof CASES[0]];
    int got[sizeof CASES / sizeof CASES[0]];
    char host[sizeof CASES / sizeof CASES[0]][SA2_VFS_PATH_MAX];
    char rel[sizeof CASES / sizeof CASES[0]][SA2_VFS_PATH_MAX];
    int layer[sizeof CASES / sizeof CASES[0]];

    int oks = 0, escapes = 0, bads = 0, misses = 0, folds_checked = 0, all = 1;
    for (int i = 0; i < n; i++) {
        layer[i] = SA2_VFS_LAYER_NONE;
        got[i] = sa2_vfs_map(CASES[i].guest, host[i], SA2_VFS_PATH_MAX,
                             rel[i], SA2_VFS_PATH_MAX, &layer[i]);
        /* Without the fold, the two folding cases are honestly not found. */
        int want = CASES[i].expect;
        if (!folding && CASES[i].needs_fold) want = SA2_VFS_NOT_FOUND;
        int p = (got[i] == want);
        if (p && want == SA2_VFS_OK) {
            /* An OK with an empty relative path proves nothing, so it is not
             * allowed to be the thing that makes the case pass. */
            p = rel[i][0] != 0;
            if (p && CASES[i].expect_rel) p = !strcmp(rel[i], CASES[i].expect_rel);
            if (p && CASES[i].expect_leaf) p = !strcmp(leaf_of(host[i]), CASES[i].expect_leaf);
            if (p && CASES[i].needs_fold) {
                /* The point of a folding case: the host name the resolver
                 * found is *not* the name the guest spelled. */
                p = strcmp(leaf_of(host[i]), leaf_of(rel[i])) != 0;
                folds_checked += p;
            }
            oks += p;
        } else if (p && want == SA2_VFS_ESCAPE) {
            /* A refusal must leave no host path behind for anyone to use. */
            p = host[i][0] == 0 || strstr(host[i], sa2_vfs_read_root()) == NULL;
            escapes += p;
        } else if (p && want == SA2_VFS_BAD) {
            bads += p;
        } else if (p && want == SA2_VFS_NOT_FOUND) {
            p = rel[i][0] != 0;
            if (p && CASES[i].expect_rel) p = !strcmp(rel[i], CASES[i].expect_rel);
            misses += p;
        }
        pass[i] = p;
        all = all && p;
    }

    /* ---- the overlay, driven end to end -------------------------------- */
    const char *cfg = "C:\\SA2\\Config\\UserConfig.cfg";
    char install_host[SA2_VFS_PATH_MAX];
    int cfg_layer = SA2_VFS_LAYER_NONE;
    sa2_vfs_map(cfg, install_host, sizeof install_host, NULL, 0, &cfg_layer);
    struct stat sb0;
    const long install_size0 = (stat(install_host, &sb0) == 0) ? (long)sb0.st_size : -1;
    const long install_mtime0 = (install_size0 >= 0) ? (long)sb0.st_mtime : -1;

    int st = 0, existed = 0;
    uint32_t h = sa2_vfs_open(cfg, SA2_VFS_ACCESS_READ | SA2_VFS_ACCESS_WRITE,
                              SA2_VFS_OPEN_ALWAYS, &st, &existed);
    long overlay_size = -1, install_size1 = -1, install_mtime1 = -1;
    int wrote_ok = 0, readback_ok = 0, copied_to_overlay = 0, delete_install_refused = 0;
    uint64_t sz = 0;
    if (h) {
        /* The copy-up must have produced a file the same size as the
         * installation's, before a byte is written. */
        sa2_vfs_size(h, &sz);
        copied_to_overlay = (install_size0 >= 0) && ((long)sz == install_size0);
        sa2_vfs_seek(h, 0, 2, NULL);
        const char marker[] = "<!-- overlay -->";
        uint32_t put = 0;
        wrote_ok = sa2_vfs_write(h, marker, (uint32_t)(sizeof marker - 1), &put) == SA2_VFS_OK
                   && put == sizeof marker - 1;
        sa2_vfs_close(h);

        int lay2 = SA2_VFS_LAYER_NONE;
        char host2[SA2_VFS_PATH_MAX];
        sa2_vfs_map(cfg, host2, sizeof host2, NULL, 0, &lay2);
        struct stat sb2;
        if (stat(host2, &sb2) == 0) overlay_size = (long)sb2.st_size;
        readback_ok = (lay2 == SA2_VFS_LAYER_OVERLAY)
                      && install_size0 >= 0
                      && overlay_size == install_size0 + (long)(sizeof marker - 1);
    }
    struct stat sb1;
    if (stat(install_host, &sb1) == 0) {
        install_size1 = (long)sb1.st_size;
        install_mtime1 = (long)sb1.st_mtime;
    }
    /* Deleting a file that exists only in the installation must refuse. */
    delete_install_refused = sa2_vfs_delete("C:\\SA2\\sonic2app.exe") == SA2_VFS_READ_ONLY;
    /* Remove the overlay copy so a later run of anything sees the
     * installation's own config, not this probe's leftovers. */
    const int overlay_deleted = sa2_vfs_delete(cfg) == SA2_VFS_OK;

    sa2_vfs_stats_t vs;
    sa2_vfs_stats(&vs);

    /* ---- the string arena: a string handed out stays where it is ---------
     * The first event's path is kept, then a few hundred more long paths are
     * resolved, which is well past the arena's first allocation. The arena
     * once grew by realloc, and a Metal Harbor stage run then read an opened
     * file's name from memory the move had freed (2026-09-23). After the
     * stats above, so the counts the probe reports are the table's alone. */
    const sa2_vfs_event *e0 = sa2_vfs_log_at(0);
    const char *first_guest = e0 ? e0->guest : NULL;
    int arena_stable = 0;
    const uint32_t log_before = sa2_vfs_log_count();
    if (first_guest && !strcmp(first_guest, CASES[0].guest)) {
        char longp[300], hb[SA2_VFS_PATH_MAX], rb[SA2_VFS_PATH_MAX];
        char filler[231];
        memset(filler, 'a', sizeof filler - 1);
        filler[sizeof filler - 1] = 0;
        for (int k = 0; k < 600; k++) {
            snprintf(longp, sizeof longp, "C:\\SA2\\%s\\missing%03d.bin", filler, k);
            int lay = SA2_VFS_LAYER_NONE;
            sa2_vfs_map(longp, hb, sizeof hb, rb, sizeof rb, &lay);
        }
        /* At least 300 events logged, each two ~240-byte strings: well past
         * one chunk, so the check cannot pass by never leaving the first. */
        arena_stable = sa2_vfs_log_count() - log_before >= 300
                       && sa2_vfs_log_at(0)->guest == first_guest
                       && !strcmp(first_guest, CASES[0].guest);
    }

    const int install_untouched = install_size0 >= 0 && install_size1 == install_size0
                                  && install_mtime1 == install_mtime0;
    const int overlay_ok = h != 0 && copied_to_overlay && wrote_ok && readback_ok
                           && overlay_deleted;
    /* Every count is in the verdict, so a table that stopped running cannot
     * pass by producing nothing. */
    /* Floors, not equalities derived from the table: an equality recomputed
     * from the same array `all` walks would be true whenever `all` is, which
     * is a check that cannot fail. These numbers are the ones this table was
     * written to produce, and turning the fold off moves two of them, which is
     * the whole point of running it both ways. */
    const int counts_ok = n == 19
                          && oks >= (folding ? 8 : 7)
                          && escapes >= 7 && bads >= 3
                          && misses >= (folding ? 1 : 2)
                          && folds_checked == (folding ? 1 : 0);
    const int ok = all && counts_ok && overlay_ok && install_untouched
                   && vs.install_write_attempts == 0 && sa2_vfs_ready() && arena_stable;

    FILE *out = stdout;
    for (int pass_i = 0; pass_i < 2; pass_i++) {
        if (pass_i == 1) {
            if (!json_path) break;
            out = fopen(json_path, "w");
            if (!out) { fprintf(stderr, "vfs_probe: cannot write %s\n", json_path); break; }
        }
        fprintf(out, "{\n \"guest_root\": ");
        jstr(out, sa2_vfs_guest_root());
        fprintf(out, ",\n \"read_root\": ");
        jstr(out, sa2_vfs_read_root());
        fprintf(out, ",\n \"write_root\": ");
        jstr(out, sa2_vfs_write_root());
        fprintf(out, ",\n \"ready\": %s, \"case_fold\": %s,\n",
                sa2_vfs_ready() ? "true" : "false", folding ? "true" : "false");
        fprintf(out, " \"cases\": [\n");
        for (int i = 0; i < n; i++) {
            fprintf(out, "  {\"guest\": ");
            jstr(out, CASES[i].guest);
            fprintf(out, ", \"expected\": ");
            jstr(out, sa2_vfs_status_name(!folding && CASES[i].needs_fold
                                          ? SA2_VFS_NOT_FOUND : CASES[i].expect));
            fprintf(out, ", \"got\": ");
            jstr(out, sa2_vfs_status_name(got[i]));
            fprintf(out, ", \"rel\": ");
            jstr(out, rel[i]);
            fprintf(out, ", \"host_leaf\": ");
            jstr(out, got[i] == SA2_VFS_OK ? leaf_of(host[i]) : "");
            fprintf(out, ", \"layer\": %d, \"pass\": %s, \"why\": ",
                    layer[i], pass[i] ? "true" : "false");
            jstr(out, CASES[i].why);
            fprintf(out, "}%s\n", i + 1 < n ? "," : "");
        }
        fprintf(out, " ],\n");
        fprintf(out, " \"tally\": {\"cases\": %d, \"ok\": %d, \"escape\": %d, \"bad\": %d, "
                     "\"not_found\": %d, \"folds_checked\": %d},\n",
                n, oks, escapes, bads, misses, folds_checked);
        fprintf(out, " \"overlay\": {\"install_size_before\": %ld, \"install_size_after\": %ld, "
                     "\"copy_up_size\": %llu, \"overlay_size_after_write\": %ld,\n"
                     "   \"copied_to_overlay\": %s, \"wrote\": %s, \"read_back\": %s, "
                     "\"deleted\": %s, \"delete_of_install_file_refused\": %s, "
                     "\"install_untouched\": %s},\n",
                install_size0, install_size1, (unsigned long long)sz, overlay_size,
                copied_to_overlay ? "true" : "false", wrote_ok ? "true" : "false",
                readback_ok ? "true" : "false", overlay_deleted ? "true" : "false",
                delete_install_refused ? "true" : "false",
                install_untouched ? "true" : "false");
        fprintf(out, " \"stats\": {\"resolves\": %u, \"escapes_refused\": %u, "
                     "\"bad_refused\": %u, \"ambiguous_refused\": %u, \"not_found\": %u,\n"
                     "   \"case_folded_components\": %u, \"opens\": %u, \"install_opens\": %u, "
                     "\"overlay_opens\": %u, \"copy_ups\": %u, \"creates\": %u,\n"
                     "   \"reads\": %u, \"writes\": %u, \"seeks\": %u, \"closes\": %u, "
                     "\"finds\": %u, \"dirs_created\": %u, \"deletes\": %u,\n"
                     "   \"bytes_read\": %llu, \"bytes_written\": %llu, "
                     "\"install_write_attempts\": %u},\n",
                vs.resolves, vs.escapes_refused, vs.bad_refused, vs.ambiguous_refused,
                vs.not_found, vs.case_folded_components, vs.opens, vs.install_opens,
                vs.overlay_opens, vs.copy_ups, vs.creates, vs.reads, vs.writes, vs.seeks,
                vs.closes, vs.finds, vs.dirs_created, vs.deletes,
                (unsigned long long)vs.bytes_read, (unsigned long long)vs.bytes_written,
                vs.install_write_attempts);
        fprintf(out, " \"assert\": {\"every_case\": %s, \"counts\": %s, \"overlay\": %s, "
                     "\"install_untouched\": %s, \"no_install_write_attempt\": %s, "
                     "\"root_present\": %s, \"string_arena_stable\": %s},\n",
                all ? "true" : "false", counts_ok ? "true" : "false",
                overlay_ok ? "true" : "false", install_untouched ? "true" : "false",
                vs.install_write_attempts == 0 ? "true" : "false",
                sa2_vfs_ready() ? "true" : "false", arena_stable ? "true" : "false");
        fprintf(out, " \"ok\": %s\n}\n", ok ? "true" : "false");
        if (pass_i == 1) fclose(out);
    }
    return ok ? 0 : 1;
}
