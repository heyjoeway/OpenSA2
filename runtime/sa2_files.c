/* The guest's file system. See sa2_files.h for the four decisions and why. */
#define _GNU_SOURCE
#include "sa2_files.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>
#if defined(_WIN32)
#include "sa2_win32.h"     /* realpath, lstat, mkdir, O_CLOEXEC and fsync */
#endif

/* ------------------------------------------------------------- state */

static char g_guest_root[260] = "C:\\SA2";
static char g_read_root[SA2_VFS_PATH_MAX];
static char g_write_root[SA2_VFS_PATH_MAX];
static int  g_ready;
static int  g_case_fold = 1;
static int  g_hide_install_saves;

static sa2_vfs_stats_t g_st;

#define SA2_VFS_LOG_MAX 512u
static sa2_vfs_event g_log[SA2_VFS_LOG_MAX];
static uint32_t      g_log_used, g_log_seq;

#define SA2_VFS_OPEN_MAX 256u
static sa2_vfs_open_record g_opened[SA2_VFS_OPEN_MAX];
static uint32_t            g_opened_used;

/* One arena for every string an event points at, so an event stays valid for
 * the life of the process. The same shape `sa2_imports.c` uses and for the
 * same reason: nothing an accessor returns may point into a buffer a later
 * call reuses. */
#define SA2_STRING_CHUNK 65536u
static char  *g_strings;
static size_t g_strings_used, g_strings_cap;

static const char *intern(const char *s) {
    size_t n = s ? strlen(s) : 0;
    /* A full chunk is left where it is and a new one started: growing it
     * with realloc moved every string already handed out. The Metal Harbor
     * stage run opened enough files to move this arena, and its report then
     * read an opened file's name from freed memory (2026-09-23). */
    if (g_strings_used + n + 1 > g_strings_cap) {
        size_t want = n + 1 > SA2_STRING_CHUNK ? n + 1 : SA2_STRING_CHUNK;
        char *p = (char *)malloc(want);
        if (!p) return "";
        g_strings = p;
        g_strings_used = 0;
        g_strings_cap = want;
    }
    char *out = g_strings + g_strings_used;
    if (n) memcpy(out, s, n);
    out[n] = 0;
    g_strings_used += n + 1;
    return out;
}

const char *sa2_vfs_status_name(int s) {
    switch (s) {
    case SA2_VFS_OK:         return "ok";
    case SA2_VFS_NOT_FOUND:  return "not-found";
    case SA2_VFS_ESCAPE:     return "escape";
    case SA2_VFS_BAD:        return "bad-path";
    case SA2_VFS_AMBIGUOUS:  return "ambiguous-case";
    case SA2_VFS_TOO_LONG:   return "too-long";
    case SA2_VFS_NO_ROOT:    return "no-read-root";
    case SA2_VFS_HOST_ERROR: return "host-error";
    case SA2_VFS_NO_HANDLE:  return "no-handle";
    case SA2_VFS_READ_ONLY:  return "install-is-read-only";
    default:                 return "?";
    }
}

static void log_event(const char *guest, const char *rel, int status, int layer, int write) {
    g_log_seq++;
    g_st.resolves++;
    switch (status) {
    case SA2_VFS_ESCAPE:    g_st.escapes_refused++; break;
    case SA2_VFS_BAD:
    case SA2_VFS_TOO_LONG:  g_st.bad_refused++; break;
    case SA2_VFS_AMBIGUOUS: g_st.ambiguous_refused++; break;
    case SA2_VFS_NOT_FOUND: g_st.not_found++; break;
    default: break;
    }
    if (g_log_used >= SA2_VFS_LOG_MAX) return;
    sa2_vfs_event *e = &g_log[g_log_used++];
    e->guest = intern(guest ? guest : "");
    e->rel = intern(rel ? rel : "");
    e->status = (uint8_t)status;
    e->layer = (int8_t)layer;
    e->write = (uint8_t)(write ? 1 : 0);
    e->seq = g_log_seq;
}

/* ----------------------------------------------------------- the roots */

static int dir_exists(const char *p) {
    struct stat sb;
    return p && *p && stat(p, &sb) == 0 && S_ISDIR(sb.st_mode);
}

/* mkdir -p, on host paths only. */
static int mkparents(const char *path) {
    char tmp[SA2_VFS_PATH_MAX];
    size_t n = strlen(path);
    if (n >= sizeof tmp) return -1;
    memcpy(tmp, path, n + 1);
    for (char *p = tmp + 1; *p; p++) {
        if (*p != '/') continue;
        *p = 0;
        if (mkdir(tmp, 0777) != 0 && errno != EEXIST) { *p = '/'; return -1; }
        *p = '/';
    }
    return 0;
}

void sa2_vfs_config(const char *guest_root) {
    if (guest_root && *guest_root) snprintf(g_guest_root, sizeof g_guest_root, "%s", guest_root);
    const char *v = getenv("SA2_VFS_NO_CASE_FOLD");
    g_case_fold = !(v && *v && atoi(v) != 0);
    v = getenv("SA2_VFS_HIDE_INSTALL_SAVES");
    g_hide_install_saves = v && *v && atoi(v) != 0;

    const char *rr = getenv("SA2_INSTALL");
    if (!rr || !*rr) rr = "private/install";
    char buf[SA2_VFS_PATH_MAX];
    if (realpath(rr, buf)) snprintf(g_read_root, sizeof g_read_root, "%s", buf);
    else snprintf(g_read_root, sizeof g_read_root, "%s", rr);

    const char *wr = getenv("SA2_VFS_WRITE_ROOT");
    if (!wr || !*wr) wr = "private/generated/s4/vfsroot";
    /* The overlay is created on demand: a run that never writes leaves no
     * directory behind, and a run that does gets one before the first write. */
    if (!dir_exists(wr)) {
        char with_slash[SA2_VFS_PATH_MAX];
        snprintf(with_slash, sizeof with_slash, "%s/", wr);
        mkparents(with_slash);
    }
    if (realpath(wr, buf)) snprintf(g_write_root, sizeof g_write_root, "%s", buf);
    else snprintf(g_write_root, sizeof g_write_root, "%s", wr);

    g_ready = dir_exists(g_read_root);
}

int sa2_vfs_ready(void) { return g_ready; }
const char *sa2_vfs_guest_root(void) { return g_guest_root; }
const char *sa2_vfs_read_root(void) { return g_read_root; }
const char *sa2_vfs_write_root(void) { return g_write_root; }
int sa2_vfs_case_fold_enabled(void) { return g_case_fold; }

/* ------------------------------------------------------- normalisation */

static int lower(int c) { return (c >= 'A' && c <= 'Z') ? c + 32 : c; }

/* ASCII case-blind equality. A byte above 0x7F compares by value: this
 * runtime has no locale and no claim on its case. */
static int ieq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) if (lower((unsigned char)*a) != lower((unsigned char)*b)) return 0;
    return *a == *b;
}

static int ieqn(const char *a, const char *b, size_t n) {
    for (size_t i = 0; i < n; i++)
        if (lower((unsigned char)a[i]) != lower((unsigned char)b[i])) return 0;
    return 1;
}

/* The save directory, root-relative and case-blind: the game names its saves
 * .\resource\gd_PC\SAVEDATA\SONIC2B__S00 to S10 (and SONIC2B__ALF, SONIC2B__D01). */
#define SAVEDATA_DIR "resource/gd_pc/savedata"
#define SAVEDATA_DIR_LEN 23u

/* SA2_VFS_HIDE_INSTALL_SAVES=1: the installation's saves are not there. A
 * normalised `rel` (sa2_vfs_normalise: '/' separators, no '.' or empty
 * components) names one when it is an entry under the save directory. Only the
 * installation's copy is hidden; a save in the overlay is the run's own and
 * reads as usual.
 *
 * Why: the launcher's selftest (host/launch/sa2_launch.cpp) drives File Select
 * with keys measured on a folder with no save. With one, File Select lists it
 * selected, and Enter opens the file's own menu (sub-state 5, its cursor on
 * the middle choice, which goes back to the list), so the keys never reach
 * the main menu; and the game reopens every save each poll of its slots
 * (0x006897d0, every tenth frame), which outruns the bounded file log a
 * selftest must hold whole. A selftest is no session of the player's, so it
 * runs on no save of the player's, as it already writes none. */
static int install_save_hidden(const char *rel) {
    return g_hide_install_saves && ieqn(rel, SAVEDATA_DIR "/", SAVEDATA_DIR_LEN + 1)
           && rel[SAVEDATA_DIR_LEN + 1] != 0;
}

/* A deleted save's marker (whiteout): an empty overlay file beside where the save would be,
 * named WHITEOUT_PREFIX + the save's name. It hides the installation's copy from every
 * lookup and listing, and the guest can never name one (split_norm refuses the prefix), so it
 * is never a file the game sees. It lives in the save directory alone, the one place the game
 * deletes: everything else in the installation is input, and deleting it stays a refusal. */
#define WHITEOUT_PREFIX ".sa2-whiteout."
#define WHITEOUT_PREFIX_LEN 14u

static int is_whiteout_name(const char *name, size_t n) {
    return n >= WHITEOUT_PREFIX_LEN && ieqn(name, WHITEOUT_PREFIX, WHITEOUT_PREFIX_LEN);
}

/* `rel` names the save directory itself. */
static int is_save_dir(const char *rel) {
    return strlen(rel) == SAVEDATA_DIR_LEN && ieqn(rel, SAVEDATA_DIR, SAVEDATA_DIR_LEN);
}

/* `rel` is an entry directly in the save directory: the only paths a marker can hide. */
static int whiteout_scope(const char *rel) {
    if (!ieqn(rel, SAVEDATA_DIR "/", SAVEDATA_DIR_LEN + 1)) return 0;
    const char *name = rel + SAVEDATA_DIR_LEN + 1;
    return *name && !strchr(name, '/');
}

/* The DOS device names, which on Windows resolve to a device no matter what
 * directory they appear in. There is no device here and inventing one would be
 * a fabricated success, so they refuse. */
static int is_device_name(const char *comp, size_t n) {
    static const char *const dev[] = {"CON", "PRN", "AUX", "NUL", "CLOCK$", NULL};
    size_t base = n;
    for (size_t i = 0; i < n; i++) if (comp[i] == '.') { base = i; break; }
    if (!base) return 0;
    for (const char *const *d = dev; *d; d++)
        if (strlen(*d) == base && ieqn(comp, *d, base)) return 1;
    if (base == 4 && (ieqn(comp, "COM", 3) || ieqn(comp, "LPT", 3))
        && comp[3] >= '1' && comp[3] <= '9') return 1;
    return 0;
}

/* Split `path` (backslashes already folded to '/') into components, applying
 * '.' and '..'. `depth0` is how many components the stack starts with: a path
 * that pops past zero has left the root it was measured from. */
typedef struct { const char *p; size_t n; } comp_t;

static int split_norm(const char *path, comp_t *out, int cap, int *count) {
    int n = 0;
    const char *p = path;
    while (*p) {
        while (*p == '/') p++;
        if (!*p) break;
        const char *s = p;
        while (*p && *p != '/') p++;
        size_t len = (size_t)(p - s);
        if (len == 1 && s[0] == '.') continue;
        if (len == 2 && s[0] == '.' && s[1] == '.') {
            if (n == 0) return SA2_VFS_ESCAPE;
            n--;
            continue;
        }
        for (size_t i = 0; i < len; i++) {
            unsigned char c = (unsigned char)s[i];
            if (c < 0x20 || c == ':' || c == '"' || c == '|' || c == '<' || c == '>')
                return SA2_VFS_BAD;
        }
        /* Windows strips trailing dots and spaces from a component; a name
         * that is only those is not a name. */
        while (len && (s[len - 1] == '.' || s[len - 1] == ' ')) len--;
        if (!len) return SA2_VFS_BAD;
        if (is_device_name(s, len)) return SA2_VFS_BAD;
        /* The layer's own deletion markers are no name of the guest's. */
        if (is_whiteout_name(s, len)) return SA2_VFS_BAD;
        if (n >= cap) return SA2_VFS_TOO_LONG;
        out[n].p = s;
        out[n].n = len;
        n++;
    }
    *count = n;
    return SA2_VFS_OK;
}

#define SA2_VFS_COMP_MAX 64

int sa2_vfs_normalise(const char *guest_path, char *rel, size_t cap) {
    if (rel && cap) rel[0] = 0;
    if (!guest_path || !*guest_path) return SA2_VFS_BAD;
    size_t n = strlen(guest_path);
    if (n + 1 >= SA2_VFS_PATH_MAX) return SA2_VFS_TOO_LONG;

    char buf[SA2_VFS_PATH_MAX];
    for (size_t i = 0; i <= n; i++) buf[i] = guest_path[i] == '\\' ? '/' : guest_path[i];

    /* `\\server\share` is a network name, not a path into this installation. */
    if (buf[0] == '/' && buf[1] == '/') return SA2_VFS_BAD;

    const char *body = buf;
    int absolute = 0;
    if (((buf[0] >= 'A' && buf[0] <= 'Z') || (buf[0] >= 'a' && buf[0] <= 'z')) && buf[1] == ':') {
        /* A drive letter that is not the guest's own drive names a volume this
         * runtime does not have. That is an escape, not a missing file. */
        if (!(g_guest_root[1] == ':' && lower((unsigned char)buf[0])
                                        == lower((unsigned char)g_guest_root[0])))
            return SA2_VFS_ESCAPE;
        body = buf + 2;
        absolute = body[0] == '/';
        if (absolute) body++;
    } else if (buf[0] == '/') {
        /* Rooted on the current drive, which is the guest's own. */
        body = buf + 1;
        absolute = 1;
    }

    comp_t comps[SA2_VFS_COMP_MAX];
    int nc = 0;
    int rc = split_norm(body, comps, SA2_VFS_COMP_MAX, &nc);
    if (rc != SA2_VFS_OK) return rc;

    int first = 0;
    if (absolute) {
        /* Strip the guest root's own components, case-blind. Anything that
         * does not begin with them is outside the installation. */
        char rbuf[260];
        const char *rp = g_guest_root;
        if (rp[0] && rp[1] == ':') rp += 2;
        size_t rn = strlen(rp);
        if (rn + 1 >= sizeof rbuf) return SA2_VFS_TOO_LONG;
        for (size_t i = 0; i <= rn; i++) rbuf[i] = rp[i] == '\\' ? '/' : rp[i];
        comp_t rc2[SA2_VFS_COMP_MAX];
        int rnc = 0;
        if (split_norm(rbuf, rc2, SA2_VFS_COMP_MAX, &rnc) != SA2_VFS_OK) return SA2_VFS_BAD;
        if (nc < rnc) return SA2_VFS_ESCAPE;
        for (int i = 0; i < rnc; i++) {
            if (comps[i].n != rc2[i].n || !ieqn(comps[i].p, rc2[i].p, comps[i].n))
                return SA2_VFS_ESCAPE;
        }
        first = rnc;
    }

    size_t used = 0;
    for (int i = first; i < nc; i++) {
        if (!rel || used + comps[i].n + 2 > cap) return SA2_VFS_TOO_LONG;
        if (used) rel[used++] = '/';
        memcpy(rel + used, comps[i].p, comps[i].n);
        used += comps[i].n;
    }
    if (!rel || used >= cap) return SA2_VFS_TOO_LONG;
    rel[used] = 0;
    return SA2_VFS_OK;
}

/* ------------------------------------------------- host-side resolution */

/* The escape check the lexical one cannot do: a symbolic link inside the
 * installation that points out of it. The deepest existing ancestor of `host`
 * is realpath'd and must still be inside `root`. */
static int under_root(const char *root, const char *host) {
    char work[SA2_VFS_PATH_MAX], real[SA2_VFS_PATH_MAX];
    snprintf(work, sizeof work, "%s", host);
    for (;;) {
        if (realpath(work, real)) break;
        char *slash = strrchr(work, '/');
        if (!slash || slash == work) return 0;
        *slash = 0;
    }
    size_t rn = strlen(root);
    if (strncmp(real, root, rn) != 0) return 0;
    return real[rn] == 0 || real[rn] == '/';
}

/* Resolve `rel` under `root`, one component at a time. On a miss the
 * remaining components are appended literally, so the caller can report where
 * it looked without a second join. */
static int resolve_under(const char *root, const char *rel, char *host, size_t cap, int *folded) {
    size_t used = strlen(root);
    if (used + 1 >= cap) return SA2_VFS_TOO_LONG;
    memcpy(host, root, used + 1);
    if (!*rel) return SA2_VFS_OK;

    const char *p = rel;
    while (*p) {
        const char *s = p;
        while (*p && *p != '/') p++;
        size_t len = (size_t)(p - s);
        if (*p == '/') p++;
        if (!len) continue;

        char comp[260];
        if (len >= sizeof comp) return SA2_VFS_TOO_LONG;
        memcpy(comp, s, len);
        comp[len] = 0;

        if (used + 1 + len + 1 >= cap) return SA2_VFS_TOO_LONG;
        size_t base = used;
        host[used++] = '/';
        memcpy(host + used, comp, len + 1);
        used += len;

        struct stat sb;
        if (lstat(host, &sb) == 0) continue;

        if (!g_case_fold) { /* the falsification column: keep the literal tail */
            if (*p) snprintf(host + used, cap - used, "/%s", p);
            return SA2_VFS_NOT_FOUND;
        }

        host[base] = 0;
        DIR *d = opendir(host[0] ? host : "/");
        if (!d) {
            host[base] = '/';
            if (*p) snprintf(host + used, cap - used, "/%s", p);
            return SA2_VFS_NOT_FOUND;
        }
        char hit[260];
        int hits = 0;
        struct dirent *de;
        while ((de = readdir(d)) != NULL) {
            if (!strcmp(de->d_name, ".") || !strcmp(de->d_name, "..")) continue;
            if (!ieq(de->d_name, comp)) continue;
            if (hits == 0) snprintf(hit, sizeof hit, "%s", de->d_name);
            hits++;
            if (hits > 1) break;
        }
        closedir(d);
        if (hits == 0) {
            host[base] = '/';
            if (*p) snprintf(host + used, cap - used, "/%s", p);
            return SA2_VFS_NOT_FOUND;
        }
        if (hits > 1) { host[base] = '/'; return SA2_VFS_AMBIGUOUS; }
        size_t hl = strlen(hit);
        if (base + 1 + hl + 1 >= cap) return SA2_VFS_TOO_LONG;
        host[base] = '/';
        memcpy(host + base + 1, hit, hl + 1);
        used = base + 1 + hl;
        if (folded) (*folded)++;
        g_st.case_folded_components++;
    }
    return SA2_VFS_OK;
}

/* `rel`'s marker, root-relative: the same directory, the prefixed name. */
static int whiteout_rel(const char *rel, char *out, size_t cap) {
    const char *slash = strrchr(rel, '/');
    const int dn = slash ? (int)(slash - rel) + 1 : 0;
    const int n = snprintf(out, cap, "%.*s%s%s", dn, rel, WHITEOUT_PREFIX, rel + dn);
    return n > 0 && (size_t)n < cap;
}

/* The overlay holds `rel`'s marker, under any case, as every lookup here reads a name. */
static int whited_out(const char *rel) {
    if (!whiteout_scope(rel) || !g_write_root[0]) return 0;
    char wrel[SA2_VFS_PATH_MAX], host[SA2_VFS_PATH_MAX];
    if (!whiteout_rel(rel, wrel, sizeof wrel)) return 0;
    const int s = resolve_under(g_write_root, wrel, host, sizeof host, NULL);
    return s == SA2_VFS_AMBIGUOUS || (s == SA2_VFS_OK && under_root(g_write_root, host));
}

/* The installation holds `rel` as the guest would see it with no overlay and no marker. */
static int install_has(const char *rel) {
    if (!g_ready || install_save_hidden(rel)) return 0;
    char host[SA2_VFS_PATH_MAX];
    return resolve_under(g_read_root, rel, host, sizeof host, NULL) == SA2_VFS_OK
           && under_root(g_read_root, host);
}

/* The read side: the overlay answers first, the installation second. */
static int map_read(const char *guest_path, char *host, size_t cap,
                    char *rel, size_t rel_cap, int *layer) {
    if (layer) *layer = SA2_VFS_LAYER_NONE;
    /* A refusal must leave no host path behind for a caller to use -- and,
     * before that, no *stale* one: `host` is the caller's buffer and every
     * early return below used to leave whatever was in it. `harness/vfs_probe.c`
     * asserts that a refused path produced no host path, and on an
     * uninitialised buffer that assertion was reading stack garbage. */
    if (host && cap) host[0] = 0;
    char rbuf[SA2_VFS_PATH_MAX];
    int rc = sa2_vfs_normalise(guest_path, rbuf, sizeof rbuf);
    if (rel && rel_cap) snprintf(rel, rel_cap, "%s", rc == SA2_VFS_OK ? rbuf : "");
    if (rc != SA2_VFS_OK) return rc;
    if (!g_ready) return SA2_VFS_NO_ROOT;

    int folded = 0;
    char cand[SA2_VFS_PATH_MAX];
    if (g_write_root[0] && dir_exists(g_write_root)) {
        int s = resolve_under(g_write_root, rbuf, cand, sizeof cand, &folded);
        if (s == SA2_VFS_OK && under_root(g_write_root, cand)) {
            snprintf(host, cap, "%s", cand);
            if (layer) *layer = SA2_VFS_LAYER_OVERLAY;
            return SA2_VFS_OK;
        }
        if (s == SA2_VFS_AMBIGUOUS) return s;
    }
    /* A hidden installation save is not found before the installation is asked anything, so
     * none of its own answers (two names differing in case, a link out of the folder) can win
     * over not found, and no installation path is handed back. */
    if (install_save_hidden(rbuf)) return SA2_VFS_NOT_FOUND;
    /* A deleted save is not found the same way, before the installation is asked. */
    if (whited_out(rbuf)) return SA2_VFS_NOT_FOUND;
    int s = resolve_under(g_read_root, rbuf, cand, sizeof cand, &folded);
    if (s != SA2_VFS_OK && s != SA2_VFS_NOT_FOUND) return s;
    if (!under_root(g_read_root, cand)) return SA2_VFS_ESCAPE;
    snprintf(host, cap, "%s", cand);
    if (s == SA2_VFS_OK && layer) *layer = SA2_VFS_LAYER_INSTALL;
    return s;
}

int sa2_vfs_map(const char *guest_path, char *host, size_t host_cap,
                char *rel, size_t rel_cap, int *layer) {
    char rbuf[SA2_VFS_PATH_MAX];
    int lay = SA2_VFS_LAYER_NONE;
    int rc = map_read(guest_path, host, host_cap, rbuf, sizeof rbuf, &lay);
    if (rel && rel_cap) snprintf(rel, rel_cap, "%s", rbuf);
    if (layer) *layer = lay;
    log_event(guest_path, rbuf, rc, lay, 0);
    return rc;
}

/* The write side. There is one join with `g_write_root` in this file and it is
 * here; nothing else can produce a host path that a write syscall sees. */
static int write_host_of(const char *rbuf, char *host, size_t cap) {
    if (!rbuf[0]) return SA2_VFS_BAD;
    if (!g_write_root[0]) return SA2_VFS_NO_ROOT;
    /* The overlay mirrors the *installation's* spelling where the
     * installation has one, so a copy-up and the file it came from occupy the
     * same relative path and the overlay stays diffable against the install. */
    char canon[SA2_VFS_PATH_MAX];
    snprintf(canon, sizeof canon, "%s", rbuf);
    char probe[SA2_VFS_PATH_MAX];
    if (g_ready && resolve_under(g_read_root, rbuf, probe, sizeof probe, NULL) == SA2_VFS_OK
        && under_root(g_read_root, probe)) {
        size_t rl = strlen(g_read_root);
        if (!strncmp(probe, g_read_root, rl) && probe[rl] == '/')
            snprintf(canon, sizeof canon, "%s", probe + rl + 1);
    }
    if (snprintf(host, cap, "%s/%s", g_write_root, canon) >= (int)cap) return SA2_VFS_TOO_LONG;
    return SA2_VFS_OK;
}

static int map_write_host(const char *guest_path, char *host, size_t cap,
                          char *rel, size_t rel_cap) {
    char rbuf[SA2_VFS_PATH_MAX];
    int rc = sa2_vfs_normalise(guest_path, rbuf, sizeof rbuf);
    if (rel && rel_cap) snprintf(rel, rel_cap, "%s", rc == SA2_VFS_OK ? rbuf : "");
    if (rc != SA2_VFS_OK) return rc;
    return write_host_of(rbuf, host, cap);
}

/* Every write syscall in this file goes through one of these two, and both
 * refuse a host path that is not under the write root. The counter is a guard
 * against a future edit passing the wrong root, not the evidence that the
 * installation was untouched -- that evidence is the before/after manifest
 * `tools/s4/install_guard.py` takes, outside this process. */
static int writable_host(const char *host) {
    if (under_root(g_write_root, host)) return 1;
    g_st.install_write_attempts++;
    return 0;
}

static int open_for_write(const char *host, int flags, mode_t mode) {
    if (!writable_host(host)) return -1;
    return open(host, flags | O_CLOEXEC, mode);
}

static int copy_up(const char *src_host, const char *dst_host) {
    if (!writable_host(dst_host)) return -1;
    int in = open(src_host, O_RDONLY | O_CLOEXEC);
    if (in < 0) return -1;
    mkparents(dst_host);
    int out = open(dst_host, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0666);
    if (out < 0) { close(in); return -1; }
    char buf[65536];
    ssize_t n;
    int rc = 0;
    while ((n = read(in, buf, sizeof buf)) > 0) {
        ssize_t off = 0;
        while (off < n) {
            ssize_t w = write(out, buf + off, (size_t)(n - off));
            if (w <= 0) { rc = -1; break; }
            off += w;
        }
        if (rc) break;
    }
    if (n < 0) rc = -1;
    close(in);
    close(out);
    return rc;
}

/* Leave `rel`'s marker in the overlay, spelled as the installation spells the save
 * (write_host_of), unless one is there already. */
static int write_whiteout(const char *rel) {
    if (whited_out(rel)) return SA2_VFS_OK;
    char host[SA2_VFS_PATH_MAX], mark[SA2_VFS_PATH_MAX];
    int rc = write_host_of(rel, host, sizeof host);
    if (rc != SA2_VFS_OK) return rc;
    const char *slash = strrchr(host, '/');
    if (!slash) return SA2_VFS_BAD;
    const int dn = (int)(slash + 1 - host);
    if (snprintf(mark, sizeof mark, "%.*s%s%s", dn, host, WHITEOUT_PREFIX, slash + 1)
        >= (int)sizeof mark)
        return SA2_VFS_TOO_LONG;
    if (!writable_host(mark)) return SA2_VFS_ESCAPE;
    if (mkparents(mark) != 0) return SA2_VFS_HOST_ERROR;
    const int fd = open_for_write(mark, O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (fd < 0) return SA2_VFS_HOST_ERROR;
    close(fd);
    g_st.whiteouts++;
    return SA2_VFS_OK;
}

/* `rel` exists in the overlay again: its marker goes. Called after the overlay entry is made,
 * so no moment shows the installation's copy of a save the player deleted; a marker that
 * stays (the unlink failed) changes nothing, as the overlay's entry wins over it. */
static void clear_whiteout(const char *rel) {
    if (!whiteout_scope(rel)) return;
    char wrel[SA2_VFS_PATH_MAX], host[SA2_VFS_PATH_MAX];
    if (!whiteout_rel(rel, wrel, sizeof wrel)) return;
    if (resolve_under(g_write_root, wrel, host, sizeof host, NULL) == SA2_VFS_OK
        && writable_host(host))
        unlink(host);
}

int sa2_vfs_map_for_write(const char *guest_path, char *host, size_t host_cap,
                          char *rel, size_t rel_cap, int do_copy_up, int *copied_up) {
    if (copied_up) *copied_up = 0;
    char rbuf[SA2_VFS_PATH_MAX];
    int rc = map_write_host(guest_path, host, host_cap, rbuf, sizeof rbuf);
    if (rel && rel_cap) snprintf(rel, rel_cap, "%s", rbuf);
    log_event(guest_path, rbuf, rc, rc == SA2_VFS_OK ? SA2_VFS_LAYER_OVERLAY
                                                     : SA2_VFS_LAYER_NONE, 1);
    if (rc != SA2_VFS_OK) return rc;
    if (mkparents(host) != 0) return SA2_VFS_HOST_ERROR;
    struct stat sb;
    if (do_copy_up && !install_save_hidden(rbuf) && stat(host, &sb) != 0 && !whited_out(rbuf)) {
        char src[SA2_VFS_PATH_MAX];
        int lay = SA2_VFS_LAYER_NONE;
        if (g_ready && resolve_under(g_read_root, rbuf, src, sizeof src, NULL) == SA2_VFS_OK
            && under_root(g_read_root, src) && stat(src, &sb) == 0 && S_ISREG(sb.st_mode)) {
            if (copy_up(src, host) != 0) return SA2_VFS_HOST_ERROR;
            g_st.copy_ups++;
            if (copied_up) *copied_up = 1;
            (void)lay;
        }
    }
    return SA2_VFS_OK;
}

/* ------------------------------------------------------- simple queries */

int sa2_vfs_exists(const char *guest_path, int *is_dir, int *layer) {
    char host[SA2_VFS_PATH_MAX];
    int lay = SA2_VFS_LAYER_NONE;
    int rc = sa2_vfs_map(guest_path, host, sizeof host, NULL, 0, &lay);
    if (layer) *layer = lay;
    if (is_dir) *is_dir = 0;
    if (rc != SA2_VFS_OK) return rc;
    struct stat sb;
    if (stat(host, &sb) != 0) return SA2_VFS_NOT_FOUND;
    if (is_dir) *is_dir = S_ISDIR(sb.st_mode) ? 1 : 0;
    return SA2_VFS_OK;
}

#define FILE_ATTRIBUTE_READONLY  0x00000001u
#define FILE_ATTRIBUTE_DIRECTORY 0x00000010u
#define FILE_ATTRIBUTE_ARCHIVE   0x00000020u
#define FILE_ATTRIBUTE_NORMAL    0x00000080u

uint32_t sa2_vfs_attributes(const char *guest_path, int *status) {
    int is_dir = 0, lay = 0;
    int rc = sa2_vfs_exists(guest_path, &is_dir, &lay);
    if (status) *status = rc;
    if (rc != SA2_VFS_OK) return 0xFFFFFFFFu;
    /* Never FILE_ATTRIBUTE_READONLY. From the guest's side every path is
     * writable, because a write lands in the overlay; reporting read-only
     * would make the game refuse a write this runtime would have accepted. */
    return is_dir ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
}

int sa2_vfs_create_directory(const char *guest_path, int *already) {
    if (already) *already = 0;
    int is_dir = 0, lay = 0;
    if (sa2_vfs_exists(guest_path, &is_dir, &lay) == SA2_VFS_OK && is_dir) {
        if (already) *already = 1;
        return SA2_VFS_OK;
    }
    char host[SA2_VFS_PATH_MAX], rel[SA2_VFS_PATH_MAX];
    int rc = map_write_host(guest_path, host, sizeof host, rel, sizeof rel);
    log_event(guest_path, rel, rc, SA2_VFS_LAYER_OVERLAY, 1);
    if (rc != SA2_VFS_OK) return rc;
    if (!writable_host(host)) return SA2_VFS_ESCAPE;
    if (mkparents(host) != 0) return SA2_VFS_HOST_ERROR;
    if (mkdir(host, 0777) != 0 && errno != EEXIST) return SA2_VFS_HOST_ERROR;
    clear_whiteout(rel);
    g_st.dirs_created++;
    return SA2_VFS_OK;
}

int sa2_vfs_delete(const char *guest_path) {
    char host[SA2_VFS_PATH_MAX], rel[SA2_VFS_PATH_MAX];
    int lay = SA2_VFS_LAYER_NONE;
    int rc = sa2_vfs_map(guest_path, host, sizeof host, rel, sizeof rel, &lay);
    if (rc != SA2_VFS_OK) return rc;
    const int save = whiteout_scope(rel);
    if (lay == SA2_VFS_LAYER_OVERLAY) {
        if (!writable_host(host)) return SA2_VFS_ESCAPE;
        /* The installation's copy of the same save would show through once the overlay's
         * goes, so the marker is written first: a failure between the two leaves the save
         * there, never the older copy back. */
        if (save && install_has(rel) && (rc = write_whiteout(rel)) != SA2_VFS_OK) return rc;
        if (unlink(host) != 0) return SA2_VFS_HOST_ERROR;
        g_st.deletes++;
        return SA2_VFS_OK;
    }
    /* A save that exists only in the installation (one the player brought from the
     * original game) is deleted by a marker in the overlay; the installation is not
     * touched. Any other file there is input, not state, and refuses by name: a silent
     * success would tell the guest something false. */
    struct stat sb;
    if (!save || stat(host, &sb) != 0 || !S_ISREG(sb.st_mode)) return SA2_VFS_READ_ONLY;
    rc = write_whiteout(rel);
    if (rc != SA2_VFS_OK) return rc;
    g_st.deletes++;
    return SA2_VFS_OK;
}

/* ------------------------------------------------------------- handles */

typedef struct {
    uint8_t     used;
    uint8_t     kind;              /* 1 = file, 2 = find */
    int         fd;
    uint32_t    access;
    const char *guest;
    /* find state: the merged listing, taken whole when the search starts */
    sa2_vfs_dirent *ents;
    uint32_t    n_ents, next_ent;
} vfs_handle;

static vfs_handle g_h[SA2_VFS_HANDLE_MAX];

/* Defined with the other evidence accessors at the end of the file; declared
 * here because the two opens below are its only callers. */
static void record_open(const char *guest, const char *rel, int fd, int layer, int write);

static uint32_t handle_alloc(uint8_t kind) {
    for (uint32_t i = 0; i < SA2_VFS_HANDLE_MAX; i++) {
        if (g_h[i].used) continue;
        memset(&g_h[i], 0, sizeof g_h[i]);
        g_h[i].used = 1;
        g_h[i].kind = kind;
        g_h[i].fd = -1;
        return SA2_VFS_HANDLE_BASE + i * SA2_VFS_HANDLE_STRIDE;
    }
    return 0;
}

static vfs_handle *handle_of(uint32_t h) {
    if (h < SA2_VFS_HANDLE_BASE) return NULL;
    uint32_t off = h - SA2_VFS_HANDLE_BASE;
    if (off % SA2_VFS_HANDLE_STRIDE) return NULL;
    uint32_t i = off / SA2_VFS_HANDLE_STRIDE;
    if (i >= SA2_VFS_HANDLE_MAX || !g_h[i].used) return NULL;
    return &g_h[i];
}

int sa2_vfs_is_handle(uint32_t h) { vfs_handle *r = handle_of(h); return r && r->kind == 1; }
int sa2_vfs_is_find_handle(uint32_t h) { vfs_handle *r = handle_of(h); return r && r->kind == 2; }
const char *sa2_vfs_handle_guest_path(uint32_t h) {
    vfs_handle *r = handle_of(h);
    return r && r->guest ? r->guest : "";
}

uint32_t sa2_vfs_open(const char *guest_path, uint32_t access, uint32_t disposition,
                      int *status, int *existed) {
    if (status) *status = SA2_VFS_OK;
    if (existed) *existed = 0;

    char host[SA2_VFS_PATH_MAX], rel[SA2_VFS_PATH_MAX];
    int lay = SA2_VFS_LAYER_NONE;
    int rc = sa2_vfs_map(guest_path, host, sizeof host, rel, sizeof rel, &lay);
    if (rc != SA2_VFS_OK && rc != SA2_VFS_NOT_FOUND) { if (status) *status = rc; return 0; }
    const int present = (rc == SA2_VFS_OK);
    if (existed) *existed = present;

    if (!(access & SA2_VFS_ACCESS_WRITE)) {
        /* Read-only: the only open in this file that may name the install. */
        if (!present) {
            if (status) *status = SA2_VFS_NOT_FOUND;
            return 0;
        }
        if (disposition == SA2_VFS_CREATE_NEW) { if (status) *status = SA2_VFS_HOST_ERROR; return 0; }
        struct stat sb;
        if (stat(host, &sb) == 0 && S_ISDIR(sb.st_mode)) {
            if (status) *status = SA2_VFS_HOST_ERROR;
            return 0;
        }
        int fd = open(host, O_RDONLY | O_CLOEXEC);
        if (fd < 0) { if (status) *status = SA2_VFS_HOST_ERROR; return 0; }
        uint32_t h = handle_alloc(1);
        if (!h) { close(fd); if (status) *status = SA2_VFS_NO_HANDLE; return 0; }
        vfs_handle *r = handle_of(h);
        r->fd = fd;
        r->access = access;
        r->guest = intern(guest_path);
        g_st.opens++;
        if (lay == SA2_VFS_LAYER_OVERLAY) g_st.overlay_opens++; else g_st.install_opens++;
        record_open(guest_path, rel, fd, lay, 0);
        return h;
    }

    /* Write access. The host path is recomputed from the write root; the read
     * resolution above only decided whether the file already exists. */
    if (disposition == SA2_VFS_OPEN_EXISTING && !present) {
        if (status) *status = SA2_VFS_NOT_FOUND;
        return 0;
    }
    if (disposition == SA2_VFS_TRUNCATE_EXISTING && !present) {
        if (status) *status = SA2_VFS_NOT_FOUND;
        return 0;
    }
    if (disposition == SA2_VFS_CREATE_NEW && present) {
        if (status) *status = SA2_VFS_HOST_ERROR;
        return 0;
    }
    const int truncating = (disposition == SA2_VFS_CREATE_ALWAYS
                            || disposition == SA2_VFS_TRUNCATE_EXISTING);
    int copied = 0;
    char whost[SA2_VFS_PATH_MAX];
    int wrc = sa2_vfs_map_for_write(guest_path, whost, sizeof whost, NULL, 0,
                                    truncating ? 0 : 1, &copied);
    if (wrc != SA2_VFS_OK) { if (status) *status = wrc; return 0; }
    int flags = (access & SA2_VFS_ACCESS_READ) ? O_RDWR : O_WRONLY;
    flags |= O_CREAT;
    if (truncating) flags |= O_TRUNC;
    int fd = open_for_write(whost, flags, 0666);
    if (fd < 0) { if (status) *status = SA2_VFS_HOST_ERROR; return 0; }
    clear_whiteout(rel);                 /* a deleted save, made again */
    uint32_t h = handle_alloc(1);
    if (!h) { close(fd); if (status) *status = SA2_VFS_NO_HANDLE; return 0; }
    vfs_handle *r = handle_of(h);
    r->fd = fd;
    r->access = access;
    r->guest = intern(guest_path);
    g_st.opens++;
    g_st.overlay_opens++;
    if (!present) g_st.creates++;
    record_open(guest_path, rel, fd, SA2_VFS_LAYER_OVERLAY, 1);
    return h;
}

int sa2_vfs_close(uint32_t h) {
    vfs_handle *r = handle_of(h);
    if (!r) return SA2_VFS_NO_HANDLE;
    if (r->fd >= 0) close(r->fd);
    free(r->ents);
    memset(r, 0, sizeof *r);
    g_st.closes++;
    return SA2_VFS_OK;
}

int sa2_vfs_read(uint32_t h, void *buf, uint32_t want, uint32_t *got) {
    if (got) *got = 0;
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 1 || r->fd < 0) return SA2_VFS_NO_HANDLE;
    if (!(r->access & SA2_VFS_ACCESS_READ)) return SA2_VFS_HOST_ERROR;
    uint32_t done = 0;
    while (done < want) {
        ssize_t n = read(r->fd, (char *)buf + done, want - done);
        if (n < 0) { if (errno == EINTR) continue; return SA2_VFS_HOST_ERROR; }
        if (n == 0) break;
        done += (uint32_t)n;
    }
    if (got) *got = done;
    g_st.reads++;
    g_st.bytes_read += done;
    return SA2_VFS_OK;
}

int sa2_vfs_write(uint32_t h, const void *buf, uint32_t want, uint32_t *put) {
    if (put) *put = 0;
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 1 || r->fd < 0) return SA2_VFS_NO_HANDLE;
    if (!(r->access & SA2_VFS_ACCESS_WRITE)) return SA2_VFS_HOST_ERROR;
    uint32_t done = 0;
    while (done < want) {
        ssize_t n = write(r->fd, (const char *)buf + done, want - done);
        if (n <= 0) { if (n < 0 && errno == EINTR) continue; return SA2_VFS_HOST_ERROR; }
        done += (uint32_t)n;
    }
    if (put) *put = done;
    g_st.writes++;
    g_st.bytes_written += done;
    return SA2_VFS_OK;
}

int sa2_vfs_seek(uint32_t h, int64_t offset, uint32_t method, uint64_t *newpos) {
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 1 || r->fd < 0) return SA2_VFS_NO_HANDLE;
    int whence = method == 0 ? SEEK_SET : method == 1 ? SEEK_CUR : method == 2 ? SEEK_END : -1;
    if (whence < 0) return SA2_VFS_BAD;
    off_t p = lseek(r->fd, (off_t)offset, whence);
    if (p == (off_t)-1) return SA2_VFS_HOST_ERROR;
    if (newpos) *newpos = (uint64_t)p;
    g_st.seeks++;
    return SA2_VFS_OK;
}

int sa2_vfs_size(uint32_t h, uint64_t *size) {
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 1 || r->fd < 0) return SA2_VFS_NO_HANDLE;
    struct stat sb;
    if (fstat(r->fd, &sb) != 0) return SA2_VFS_HOST_ERROR;
    if (size) *size = (uint64_t)sb.st_size;
    return SA2_VFS_OK;
}

int sa2_vfs_set_end_of_file(uint32_t h) {
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 1 || r->fd < 0) return SA2_VFS_NO_HANDLE;
    if (!(r->access & SA2_VFS_ACCESS_WRITE)) return SA2_VFS_HOST_ERROR;
    off_t p = lseek(r->fd, 0, SEEK_CUR);
    if (p == (off_t)-1 || ftruncate(r->fd, p) != 0) return SA2_VFS_HOST_ERROR;
    return SA2_VFS_OK;
}

int sa2_vfs_flush(uint32_t h) {
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 1 || r->fd < 0) return SA2_VFS_NO_HANDLE;
    return fsync(r->fd) == 0 ? SA2_VFS_OK : SA2_VFS_HOST_ERROR;
}

/* --------------------------------------------------------------- find
 *
 * Win32 wildcards, the two the image uses: '*' spans any run including empty,
 * '?' is one character. Case-blind, ASCII, the same rule as everywhere else
 * here. */
static int wild_match(const char *pat, const char *name) {
    const char *star = NULL, *back = NULL;
    while (*name) {
        if (*pat == '?' || (*pat && lower((unsigned char)*pat) == lower((unsigned char)*name))) {
            pat++; name++;
        } else if (*pat == '*') {
            star = pat++;
            back = name;
        } else if (star) {
            pat = star + 1;
            name = ++back;
        } else {
            return 0;
        }
    }
    while (*pat == '*') pat++;
    return !*pat;
}

static int fill_dirent(const char *dir_host, const char *name, sa2_vfs_dirent *out) {
    snprintf(out->name, sizeof out->name, "%s", name);
    char full[SA2_VFS_PATH_MAX];
    snprintf(full, sizeof full, "%s/%s", dir_host, name);
    struct stat sb;
    if (stat(full, &sb) != 0) return 0;
    out->attributes = S_ISDIR(sb.st_mode) ? FILE_ATTRIBUTE_DIRECTORY : FILE_ATTRIBUTE_ARCHIVE;
    out->size = S_ISDIR(sb.st_mode) ? 0u : (uint64_t)sb.st_size;
    return 1;
}

/* A search's entries, gathered from both layers before the first is handed out. */
typedef struct {
    sa2_vfs_dirent *e;
    uint32_t        n, cap;
} listing;

static int listing_add(listing *l, const sa2_vfs_dirent *d) {
    if (l->n == l->cap) {
        const uint32_t cap = l->cap ? l->cap * 2u : 16u;
        sa2_vfs_dirent *e = (sa2_vfs_dirent *)realloc(l->e, cap * sizeof *e);
        if (!e) return 0;
        l->e = e;
        l->cap = cap;
    }
    l->e[l->n++] = *d;
    return 1;
}

static int listing_names(const listing *l, uint32_t n, const char *name) {
    for (uint32_t i = 0; i < n; i++) if (ieq(l->e[i].name, name)) return 1;
    return 0;
}

/* The order Windows lists a directory in (NTFS's file-name collation, and Wine's, which sorts
 * what it reads from the host): names compared upper-cased, then byte for byte. A host's
 * readdir order is its own (ext4's is a hash with a per-volume seed), so the guest would
 * otherwise see one machine's order on one machine. */
static int upper(int c) { return (c >= 'a' && c <= 'z') ? c - 32 : c; }

static int listing_order(const void *a, const void *b) {
    const unsigned char *x = (const unsigned char *)((const sa2_vfs_dirent *)a)->name;
    const unsigned char *y = (const unsigned char *)((const sa2_vfs_dirent *)b)->name;
    for (size_t i = 0;; i++) {
        const int cx = upper(x[i]), cy = upper(y[i]);
        if (cx != cy) return cx - cy;
        if (!cx) break;
    }
    return strcmp((const char *)x, (const char *)y);
}

/* The entries `pattern` matches in `dir_host`, into `l`. An install entry (`shadow` set) is
 * left out when one of the overlay's first `shadow` entries has its name, case-blind, or a
 * marker in `gone` does. A marker is never an entry: the overlay's go into `gone` when
 * `gone` is given (the save directory, where markers hide), and are skipped otherwise. */
static int list_layer(const char *dir_host, const char *pattern, listing *l, uint32_t shadow,
                      listing *gone, int install) {
    DIR *d = opendir(dir_host);
    if (!d) return 1;
    int ok = 1;
    struct dirent *de;
    sa2_vfs_dirent e;
    while (ok && (de = readdir(d)) != NULL) {
        const char *name = de->d_name;
        if (!strcmp(name, ".") || !strcmp(name, "..")) continue;
        if (is_whiteout_name(name, strlen(name))) {
            if (!install && gone) {
                memset(&e, 0, sizeof e);
                snprintf(e.name, sizeof e.name, "%s", name + WHITEOUT_PREFIX_LEN);
                ok = listing_add(gone, &e);
            }
            continue;
        }
        if (!wild_match(pattern, name)) continue;
        if (install && (listing_names(l, shadow, name) || (gone && listing_names(gone, gone->n, name))))
            continue;
        if (!fill_dirent(dir_host, name, &e)) continue;
        ok = listing_add(l, &e);
    }
    closedir(d);
    return ok;
}

uint32_t sa2_vfs_find_first(const char *guest_pattern, sa2_vfs_dirent *out, int *status) {
    if (status) *status = SA2_VFS_OK;
    /* Split the pattern into a directory and a leaf pattern, on the guest
     * side, so the wildcard never reaches the path normaliser's component
     * rules as if it were a name to resolve. */
    char pat[SA2_VFS_PATH_MAX];
    snprintf(pat, sizeof pat, "%s", guest_pattern ? guest_pattern : "");
    char *leaf = pat;
    for (char *p = pat; *p; p++) if (*p == '\\' || *p == '/') leaf = p + 1;
    char dirpart[SA2_VFS_PATH_MAX];
    size_t dn = (size_t)(leaf - pat);
    if (dn == 0) {
        snprintf(dirpart, sizeof dirpart, ".");
    } else {
        memcpy(dirpart, pat, dn);
        dirpart[dn - 1] = 0;                 /* drop the separator itself */
        if (!dirpart[0]) snprintf(dirpart, sizeof dirpart, "\\");
        /* "C:\\*.prs" leaves "C:", which on Win32 means the drive's current
         * directory and here would silently become the root. Say the drive
         * root instead, and let the escape check answer it. */
        else if (dirpart[1] == ':' && !dirpart[2]) { dirpart[2] = '\\'; dirpart[3] = 0; }
    }

    char host[SA2_VFS_PATH_MAX], rel[SA2_VFS_PATH_MAX];
    int lay = SA2_VFS_LAYER_NONE;
    int rc = sa2_vfs_map(dirpart, host, sizeof host, rel, sizeof rel, &lay);
    if (rc != SA2_VFS_OK) { if (status) *status = rc; return 0; }
    /* The installation's save directory lists nothing when its saves are
     * hidden: every entry in it is one (install_save_hidden). */
    const int save_dir = is_save_dir(rel);
    const int install_hidden = g_hide_install_saves && save_dir;
    if (install_hidden && lay == SA2_VFS_LAYER_INSTALL) {
        if (status) *status = SA2_VFS_NOT_FOUND;
        return 0;
    }
    /* The save directory lists both layers: the overlay's directory when the map found it
     * there, and the installation's whenever it has one, which the map does not ask once the
     * overlay answers. The overlay wins a name, and a marker hides one. Every other directory
     * lists the one layer the map chose, in the host's order, as before: the game searches its
     * own folder at boot, and a merge there would hand it the game folder's root files
     * (installscript.vdf, steam_appid.txt), which it then opens. */
    const char *odir = NULL, *idir = NULL;
    char ihost[SA2_VFS_PATH_MAX];
    if (lay == SA2_VFS_LAYER_OVERLAY) {
        odir = host;
        if (save_dir && !install_hidden && g_ready
            && resolve_under(g_read_root, rel, ihost, sizeof ihost, NULL) == SA2_VFS_OK
            && under_root(g_read_root, ihost) && dir_exists(ihost))
            idir = ihost;
    } else {
        idir = host;
    }
    if (!dir_exists(odir) && !dir_exists(idir)) {
        if (status) *status = SA2_VFS_NOT_FOUND;
        return 0;
    }
    const char *leafpat = leaf[0] ? leaf : "*";
    listing l = {0}, gone = {0};
    int ok = 1;
    if (odir) ok = list_layer(odir, leafpat, &l, 0, save_dir ? &gone : NULL, 0);
    if (ok && idir) ok = list_layer(idir, leafpat, &l, l.n, save_dir ? &gone : NULL, 1);
    free(gone.e);
    if (!ok) {
        free(l.e);
        if (status) *status = SA2_VFS_HOST_ERROR;
        return 0;
    }
    if (save_dir && l.n > 1) qsort(l.e, l.n, sizeof *l.e, listing_order);
    uint32_t h = handle_alloc(2);
    if (!h) { free(l.e); if (status) *status = SA2_VFS_NO_HANDLE; return 0; }
    vfs_handle *r = handle_of(h);
    r->ents = l.e;
    r->n_ents = l.n;
    r->guest = intern(guest_pattern ? guest_pattern : "");
    g_st.finds++;
    if (!sa2_vfs_find_next(h, out)) {
        sa2_vfs_close(h);
        if (status) *status = SA2_VFS_NOT_FOUND;
        return 0;
    }
    return h;
}

int sa2_vfs_find_next(uint32_t h, sa2_vfs_dirent *out) {
    vfs_handle *r = handle_of(h);
    if (!r || r->kind != 2 || r->next_ent >= r->n_ents) return 0;
    *out = r->ents[r->next_ent++];
    return 1;
}

/* ------------------------------------------------------------ evidence */

/* Recorded after the descriptor exists, so the size is the host's answer for
 * the file the guest actually got, not for a path that might have resolved
 * somewhere else between the two calls. */
static void record_open(const char *guest, const char *rel, int fd, int layer, int write) {
    if (g_opened_used >= SA2_VFS_OPEN_MAX) return;
    struct stat sb;
    sa2_vfs_open_record *r = &g_opened[g_opened_used++];
    r->guest = intern(guest ? guest : "");
    r->rel = intern(rel ? rel : "");
    r->size = fstat(fd, &sb) == 0 ? (uint64_t)sb.st_size : 0u;
    r->layer = (int8_t)layer;
    r->write = (uint8_t)(write ? 1 : 0);
}

uint32_t sa2_vfs_opened_count(void) { return g_opened_used; }
const sa2_vfs_open_record *sa2_vfs_opened_at(uint32_t i) {
    return i < g_opened_used ? &g_opened[i] : NULL;
}

void sa2_vfs_stats(sa2_vfs_stats_t *out) { if (out) *out = g_st; }
uint32_t sa2_vfs_log_count(void) { return g_log_used; }
const sa2_vfs_event *sa2_vfs_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }
