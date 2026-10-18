/* The guest's file system: `C:\SA2` backed by the user's own installation.
 *
 * The problem this file exists for
 * -------------------------------
 * S4 slice 3 left the guest inside `WinMain`, stopped at
 * `SHLWAPI!PathFileExistsA` called from 0x004064fb -- the game asking whether
 * its own `Config` directory is there. Every import past that point is a file
 * operation, and a file operation needs three decisions that a stub cannot
 * make one call at a time: what a guest path means, what happens when it means
 * something outside the installation, and where a write goes.
 *
 * Those decisions are here, in one place, rather than spread across fifteen
 * stubs in `sa2_imports.c`. A stub asks this file to turn a guest path into a
 * host path; it never builds one itself, and it has no way to name a host path
 * this file did not return.
 *
 * Decision 1: the mapping
 * -----------------------
 * The guest believes it lives at `C:\SA2` (`SA2_GUEST_CWD`, the named
 * placeholder from slice 2, which is also what `GetCurrentDirectoryA` returns
 * and the directory part of `GetModuleFileNameA`). That prefix maps to one
 * host directory, the read root, and nothing else does:
 *
 *     C:\SA2\Config\UserConfig.cfg   ->   <read root>/Config/UserConfig.cfg
 *
 * A guest path is normalised *lexically first*, before any host call:
 * separators fold (`/` is legal on Win32 and the image uses both), `.`
 * disappears, `..` pops, and a `..` that would pop past the root is refused
 * rather than clamped. The result is a root-relative path with `/`
 * separators, which is the only thing that is ever joined to a host root.
 *
 * Decision 2: an escape is a trap, not a miss
 * -------------------------------------------
 * `C:\SA2\..\..\etc\passwd`, `D:\anything`, `\\server\share`, and a bare
 * `\Windows\win.ini` all resolve to SA2_VFS_ESCAPE, and every stub turns that
 * into `sa2_trap_raise`. They are deliberately *not* ERROR_FILE_NOT_FOUND:
 * "not found" is an answer the guest is entitled to route around, and the one
 * thing this runtime must never do quietly is serve a byte from outside the
 * installation. A refusal that is loud is a work item; a refusal that looks
 * like a missing file is a hole nobody sees.
 *
 * The lexical check is not the only one. After the host path is built, the
 * deepest existing ancestor is `realpath`'d and checked to be inside the
 * `realpath`'d root, so a symbolic link *inside* the installation that points
 * out of it is refused too -- the lexical check cannot see one of those.
 *
 * Decision 3: reads from the install, writes to an overlay
 * -------------------------------------------------------
 * The installation is the user's own property and, by AGENTS.md rule 9, a
 * read-only input. The game will write: a `Config` directory, a saved
 * `UserConfig.cfg`, save files, a crash dump. So there are two roots:
 *
 *   * the **read root** (`SA2_INSTALL`, default `private/install`), opened
 *     `O_RDONLY` and never anything else, and
 *   * the **write root** (`SA2_VFS_WRITE_ROOT`, default
 *     `private/generated/s4/vfsroot`), which is git-ignored generated output.
 *
 * A read looks in the overlay first and falls through to the install. A write
 * always lands in the overlay, copying the install's copy up first when it is
 * opening an existing file without truncating. The guest therefore sees one
 * writable file system and reads back exactly what it wrote, while the
 * installation stays byte-identical -- which `tools/s4/install_guard.py`
 * checks rather than assumes.
 *
 * A delete lands in the overlay too. Deleting a save the installation holds (one the player
 * brought from the original game) leaves a marker, a *whiteout*, in the overlay's save
 * directory: an empty file named `.sa2-whiteout.<save>`. Every lookup (map, open, exists,
 * attributes, a search) then answers as if the installation had no such save; creating the
 * save again removes the marker, and deleting an overlay save the installation also holds
 * leaves one, so the older copy never shows through. The guest cannot name a marker (the
 * normaliser refuses the prefix as SA2_VFS_BAD) and no search lists one. Markers exist in the
 * save directory only: every other installation file is input, and deleting one refuses as
 * SA2_VFS_READ_ONLY. A search lists both layers merged, the overlay winning a name
 * case-blind, in the order Windows lists a directory (sa2_vfs_find_first).
 *
 * This is structural, not a convention. `resolve_under` takes the root as an
 * argument, and the only call that passes the read root is the one feeding the
 * `O_RDONLY` open; every host path a write syscall sees comes from
 * `map_write_host`, which joins `g_write_root` and nothing else, and passes
 * through `writable_host`, which re-checks it against the realpath'd write root
 * and refuses otherwise. There is no code path from "the guest asked to write"
 * to a host path under the installation.
 *
 * Decision 4: case
 * ----------------
 * Windows file names are case-insensitive; ext4's are not, and the image asks
 * for `AL_ENTRANCE_TEX.PRS` when the disk says `AL_ENTRANCE_TEX.prs` often
 * enough that ignoring this would look like a missing asset. Each path
 * component is resolved against its parent directory: the exact spelling
 * first, and only if that misses, a scan of the directory for an ASCII
 * case-folded match. Two host entries that differ only in case are
 * SA2_VFS_AMBIGUOUS and refuse, because Windows could not have produced that
 * directory and picking one would be a coin flip.
 *
 * Folding is ASCII-only on purpose, the same discipline `lstrcmpA` follows in
 * `sa2_imports.c`: this runtime has no locale, and a byte above 0x7F has no
 * case this code can claim to know.
 *
 * What never happens here
 * -----------------------
 * No file content leaves this translation unit except into guest memory. The
 * event log records path names, sizes, byte counts and a layer; a probe can
 * print all of it and a receipt can carry all of it without a single byte of
 * the user's installation crossing into anything tracked.
 */
#ifndef SA2_RUNTIME_FILES_H
#define SA2_RUNTIME_FILES_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* How a resolution ended. Anything but OK/NOT_FOUND is a refusal, and every
 * caller in `sa2_imports.c` turns a refusal into a trap. */
enum {
    SA2_VFS_OK = 0,
    SA2_VFS_NOT_FOUND,     /* a legal path with nothing at it: ERROR_FILE_NOT_FOUND */
    SA2_VFS_ESCAPE,        /* leaves the installation root: refuse loudly */
    SA2_VFS_BAD,           /* malformed, a UNC name, or a DOS device name */
    SA2_VFS_AMBIGUOUS,     /* two host entries differ only by case */
    SA2_VFS_TOO_LONG,
    SA2_VFS_NO_ROOT,       /* the read root does not exist */
    SA2_VFS_HOST_ERROR,    /* the host refused the open/read/write */
    SA2_VFS_NO_HANDLE,     /* the handle table is full, or the handle is not ours */
    SA2_VFS_READ_ONLY      /* an installation file outside the save directory: input */
};
const char *sa2_vfs_status_name(int status);

/* Which root answered. */
enum { SA2_VFS_LAYER_NONE = -1, SA2_VFS_LAYER_INSTALL = 0, SA2_VFS_LAYER_OVERLAY = 1 };

#define SA2_VFS_PATH_MAX 1024u

/* SA2_INSTALL (host read root), SA2_VFS_WRITE_ROOT (host overlay), and
 * SA2_GUEST_CWD, which `sa2_imports.c` already reads and which this module
 * takes as the guest-side name of the read root. Idempotent; safe to call
 * before the roots exist, in which case `sa2_vfs_ready` stays 0 and every
 * resolution answers SA2_VFS_NO_ROOT.
 *
 * SA2_VFS_HIDE_INSTALL_SAVES=1: every entry under the installation's
 * resource/gd_PC/SAVEDATA reads as not found (no copy-up from it, an empty
 * listing of it), while the overlay's saves read as usual. The launcher's
 * selftest sets it so the game runs on no save of the player's
 * (sa2_files.c install_save_hidden has the why); play never does. */
void sa2_vfs_config(const char *guest_root);
int  sa2_vfs_ready(void);
const char *sa2_vfs_guest_root(void);
const char *sa2_vfs_read_root(void);
const char *sa2_vfs_write_root(void);

/* The lexical half, exposed because it is the half with the security property
 * and a probe must be able to drive it without a guest. `rel` receives the
 * root-relative path with '/' separators; it is written even when the status
 * is NOT_FOUND, and left empty on a refusal. */
int sa2_vfs_normalise(const char *guest_path, char *rel, size_t cap);

/* The whole mapping. On OK, `host` is a host path that exists and `*layer`
 * says which root it came from. On NOT_FOUND, `host` is where a *read* would
 * have looked and `*layer` is NONE. `rel` (optional) receives the normalised
 * guest-relative path. */
int sa2_vfs_map(const char *guest_path, char *host, size_t host_cap,
                char *rel, size_t rel_cap, int *layer);

/* Where a write goes: always under the write root, never under the install.
 * Creates the parent directories. `*copied_up` is set when an existing install
 * file was copied into the overlay first. */
int sa2_vfs_map_for_write(const char *guest_path, char *host, size_t host_cap,
                          char *rel, size_t rel_cap, int copy_up, int *copied_up);

/* Directories. `sa2_vfs_create_directory` always lands in the overlay; it
 * answers OK when it made one and SA2_VFS_EXISTS_ALREADY (as a separate
 * out-flag) when either root already has it. */
int sa2_vfs_exists(const char *guest_path, int *is_dir, int *layer);
int sa2_vfs_create_directory(const char *guest_path, int *already);

/* Win32 file attributes for a guest path, or 0xFFFFFFFF with the status set. */
uint32_t sa2_vfs_attributes(const char *guest_path, int *status);

/* ------------------------------------------------------------- handles
 *
 * File and find handles live in their own address range, outside the guest
 * address space and distinct from `sa2_imports.c`'s object handles, so a
 * handle of the wrong kind is a recognisable value rather than a plausible
 * one.
 *
 * **The base moved from 0x7e000000 to 0x7d000000 on 2026-09-21, because it
 * was not in fact its own range.** `SA2_WND_HWND_BASE` is 0x7e000000 too, and
 * with a window-token stride of 0x10 against this file's 0x1000 the two
 * allocators issue the *same* first value: the first open file handle and the
 * first window handle were both exactly 0x7e000000. `IsWindow` answered true
 * for a file handle, `sa2_window_of` found window zero for one, and this
 * file's own row lookup accepted the main HWND as file handle zero -- three
 * ways for a handle of the wrong kind to be a plausible value, which is the
 * one thing the comment above says it must never be.
 *
 * It was found by a unit adding a *fourth* set of tokens and enumerating what
 * was already taken, not by anything that could have caught it on purpose.
 * `Documentation/scripts/check_handle_regions.py` is the check that would
 * have, and now does.
 *
 * This base moved rather than the window one because no receipt records a
 * file-handle value, while `s4-window-and-pump-v1.json` pins 0x7e000000 as
 * the main HWND in ten places. Moving the pinned side would have rewritten
 * evidence to accommodate a bug. */
#define SA2_VFS_HANDLE_BASE   0x7d000000u
#define SA2_VFS_HANDLE_STRIDE 0x1000u
#define SA2_VFS_HANDLE_MAX    64u

enum { SA2_VFS_ACCESS_READ = 1, SA2_VFS_ACCESS_WRITE = 2 };
enum { SA2_VFS_CREATE_NEW = 1, SA2_VFS_CREATE_ALWAYS = 2, SA2_VFS_OPEN_EXISTING = 3,
       SA2_VFS_OPEN_ALWAYS = 4, SA2_VFS_TRUNCATE_EXISTING = 5 };

/* CreateFileA's semantics, minus sharing modes and security descriptors.
 * Returns a handle, or 0 with `*status` set. */
uint32_t sa2_vfs_open(const char *guest_path, uint32_t access, uint32_t disposition,
                      int *status, int *existed);
int      sa2_vfs_is_handle(uint32_t h);
int      sa2_vfs_is_find_handle(uint32_t h);
const char *sa2_vfs_handle_guest_path(uint32_t h);
int      sa2_vfs_close(uint32_t h);
int      sa2_vfs_read(uint32_t h, void *buf, uint32_t want, uint32_t *got);
int      sa2_vfs_write(uint32_t h, const void *buf, uint32_t want, uint32_t *put);
/* The host read or write sa2_vfs_read or sa2_vfs_write is inside, for the stall watch
 * (runtime/sa2_stall.h): 1 with *write (0 for a read), its host thread's id, the guest's path,
 * the bytes asked and when the call began (CLOCK_MONOTONIC, ns); 0 when none is, and always on
 * hosts other than Linux. Read without a lock, from another thread. */
int      sa2_vfs_io_now(int *write, uint32_t *tid, const char **path, uint64_t *bytes, int64_t *since_ns);
/* `method` is Win32's FILE_BEGIN/FILE_CURRENT/FILE_END (0/1/2). */
int      sa2_vfs_seek(uint32_t h, int64_t offset, uint32_t method, uint64_t *newpos);
int      sa2_vfs_size(uint32_t h, uint64_t *size);
int      sa2_vfs_set_end_of_file(uint32_t h);
int      sa2_vfs_flush(uint32_t h);
/* OK, NOT_FOUND, or READ_ONLY for an installation file outside the save directory. A save
 * the installation holds is deleted by a whiteout (Decision 3); the installation is never
 * written. */
int      sa2_vfs_delete(const char *guest_path);

/* FindFirstFile/FindNextFile. One entry at a time; the caller fills the
 * guest's WIN32_FIND_DATAA from `name`, `attributes` and `size`. The entries are
 * both layers' merged when the search starts: the overlay's, then each of the
 * installation's whose name (case-blind) the overlay lacks and no whiteout hides,
 * sorted as Windows sorts a directory (upper-cased, then byte for byte). '.' and
 * '..' are not listed. */
typedef struct {
    char     name[260];
    uint32_t attributes;
    uint64_t size;
} sa2_vfs_dirent;
uint32_t sa2_vfs_find_first(const char *guest_pattern, sa2_vfs_dirent *out, int *status);
int      sa2_vfs_find_next(uint32_t h, sa2_vfs_dirent *out);

/* ------------------------------------------------------------- evidence */

typedef struct {
    uint32_t resolves, escapes_refused, bad_refused, ambiguous_refused, not_found;
    uint32_t case_folded_components;
    uint32_t opens, install_opens, overlay_opens, copy_ups, creates;
    uint32_t reads, writes, seeks, closes, finds, dirs_created, deletes;
    uint64_t bytes_read, bytes_written;
    uint32_t install_write_attempts;   /* must stay 0: see the module comment */
    uint32_t whiteouts;                /* deletion markers written (sa2_vfs_delete) */
} sa2_vfs_stats_t;
void sa2_vfs_stats(sa2_vfs_stats_t *out);

/* One resolution, kept so a probe can print what the guest asked for. Path
 * names only: no file content is recorded anywhere in this module. */
typedef struct {
    const char *guest;     /* the path as the guest spelled it */
    const char *rel;       /* the normalised root-relative path, or "" */
    uint8_t     status;
    int8_t      layer;
    uint8_t     write;     /* the resolution was for a write */
    uint32_t    seq;
} sa2_vfs_event;
uint32_t             sa2_vfs_log_count(void);
const sa2_vfs_event *sa2_vfs_log_at(uint32_t i);

/* One successful open, with the size the *host* reported for it at the moment
 * it was opened. This exists so a probe can compare the guest's own
 * seek-to-end against a number the guest had no part in producing: the guest
 * asks the file how long it is, the host asks the file system, and the two
 * have to agree. Neither side is a constant in any source file. */
typedef struct {
    const char *guest;
    const char *rel;
    uint64_t    size;
    int8_t      layer;
    uint8_t     write;
} sa2_vfs_open_record;
uint32_t                   sa2_vfs_opened_count(void);
const sa2_vfs_open_record *sa2_vfs_opened_at(uint32_t i);

/* For the falsification column: break the case-insensitive fallback, so a run
 * that only passes because the disk happens to agree with the guest's spelling
 * can be told from one that needed the fold. `SA2_VFS_NO_CASE_FOLD=1`. */
int sa2_vfs_case_fold_enabled(void);

#ifdef __cplusplus
}
#endif
#endif
