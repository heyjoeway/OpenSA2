/* The Windows host's POSIX layer: the functions sa2_win32.h declares that need the Windows
 * API, and the process setup a POSIX host gets for free. Compiled only for an x86-64
 * Windows target (tools/s7/win_link.py adds it to the runtime's sources); a Linux build
 * never compiles it. The trap backend, alarm included, is sa2_trap.c's. */
#if !defined(_WIN32)
#error "sa2_win32.c is the Windows host's POSIX layer; a POSIX build does not compile it"
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "sa2_win32.h"

#include <string.h>
#include <wchar.h>

#include "sa2_files.h"

/* -------------------------------------------------------- process setup */

/* Every file binary, before main opens any. The CRT's default is text mode, which turns
 * "\n" into "\r\n" on every write and stops a read at 0x1A: a tick capture, a word log and
 * a run record would differ from Linux's byte for byte, and a game file read through the
 * VFS would be cut short. Also no error box for a crash: no window for a person (AGENTS.md
 * rule 11), so a fault outside a call ends the process with the trap's report alone. */
__attribute__((constructor(101))) static void sa2_win32_process(void) {
    _set_fmode(_O_BINARY);
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    _setmode(_fileno(stderr), _O_BINARY);
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
}

/* ------------------------------------------------------------ guest memory */

void *sa2_win32_mmap(void *addr, size_t len, int prot, int flags, int fd, int64_t off) {
    if (addr || fd != -1 || off != 0 || prot != (PROT_READ | PROT_WRITE)
        || (flags & ~MAP_NORESERVE) != (MAP_PRIVATE | MAP_ANONYMOUS) || len == 0) {
        errno = EINVAL;
        return MAP_FAILED;
    }
    /* Committed now: pages are zero-filled on first touch, as an anonymous mapping's are,
     * and charged against the commit limit rather than reserved lazily (MAP_NORESERVE has
     * no Windows equivalent short of committing on fault). */
    void *p = VirtualAlloc(NULL, len, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!p) {
        errno = ENOMEM;
        return MAP_FAILED;
    }
    return p;
}

int sa2_win32_munmap(void *addr, size_t len) {
    (void)len;              /* the whole allocation: the runtime unmaps what it mapped */
    if (!addr || !VirtualFree(addr, 0, MEM_RELEASE)) {
        errno = EINVAL;
        return -1;
    }
    return 0;
}

/* ------------------------------------------------------------------- files */

/* The code page a narrow path is read in: the one CreateFileA and the CRT's narrow file calls
 * use (AreFileApisANSI), so a path names here the file it names to fopen. UTF-8 under the
 * application manifest's activeCodePage (tools/s7/win_link.py). */
static UINT path_cp(void) {
    return AreFileApisANSI() ? GetACP() : GetOEMCP();
}

/* `path` in UTF-16, into `out` of `cap` wide characters; 0, or -1 with errno. */
static int path_wide(const char *path, wchar_t *out, int cap) {
    const int n = MultiByteToWideChar(path_cp(), MB_ERR_INVALID_CHARS, path, -1, out, cap);
    if (n > 0) return 0;
    errno = GetLastError() == ERROR_INSUFFICIENT_BUFFER ? ENAMETOOLONG : EILSEQ;
    return -1;
}

static int errno_of(DWORD e) {
    switch (e) {
    case ERROR_FILE_NOT_FOUND:
    case ERROR_PATH_NOT_FOUND:
    case ERROR_INVALID_NAME:
    case ERROR_BAD_NETPATH:
    case ERROR_BAD_PATHNAME:
    case ERROR_INVALID_DRIVE:
        return ENOENT;
    case ERROR_ACCESS_DENIED:
    case ERROR_SHARING_VIOLATION:
        return EACCES;
    case ERROR_FILENAME_EXCED_RANGE:
        return ENAMETOOLONG;
    case ERROR_CANT_RESOLVE_FILENAME:       /* a cycle of links, or too many of them */
        return ELOOP;
    default:
        return EIO;
    }
}

/* The file `path` names, opened for no access with every sharing mode (a file or a directory,
 * which FILE_FLAG_BACKUP_SEMANTICS allows): through every link with `flags` 0, or the link
 * itself with FILE_FLAG_OPEN_REPARSE_POINT. INVALID_HANDLE_VALUE with errno. */
static HANDLE path_open(const char *path, DWORD flags) {
    wchar_t w[SA2_VFS_PATH_MAX];
    if (path_wide(path, w, (int)(sizeof w / sizeof w[0]))) return INVALID_HANDLE_VALUE;
    HANDLE h = CreateFileW(w, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL,
                           OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | flags, NULL);
    if (h == INVALID_HANDLE_VALUE) errno = errno_of(GetLastError());
    return h;
}

/* Absolute, every symbolic link and junction resolved, with forward slashes, so sa2_files.c's
 * '/' joins and its escape check read it as they read a Linux path: a link inside the
 * installation that points out of it resolves out of it, as POSIX realpath's does. NULL with
 * ENOENT for a path that does not exist, a dangling link among them. `resolved`, when given,
 * holds SA2_VFS_PATH_MAX bytes: every caller's buffer is that size.
 *
 * The file's final path is the system's: "\\?\C:\dir\file", or "\\?\UNC\host\share\..."
 * for a network path, which is written "//host/share/...". */
char *realpath(const char *path, char *resolved) {
    if (!path || !*path) {
        errno = path ? ENOENT : EINVAL;
        return NULL;
    }
    HANDLE h = path_open(path, 0);
    if (h == INVALID_HANDLE_VALUE) return NULL;
    wchar_t fin[SA2_VFS_PATH_MAX + 8];
    const DWORD cap = (DWORD)(sizeof fin / sizeof fin[0]);
    const DWORD got = GetFinalPathNameByHandleW(h, fin, cap, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    const DWORD err = GetLastError();
    CloseHandle(h);
    if (!got || got >= cap) {
        errno = got ? ENAMETOOLONG : errno_of(err);
        return NULL;
    }
    const wchar_t *w = fin;
    wchar_t unc[SA2_VFS_PATH_MAX + 8];
    if (!wcsncmp(w, L"\\\\?\\UNC\\", 8)) {
        unc[0] = unc[1] = L'\\';
        wcscpy(unc + 2, w + 8);
        w = unc;
    } else if (!wcsncmp(w, L"\\\\?\\", 4)) {
        w += 4;
    }
    char buf[SA2_VFS_PATH_MAX];
    const UINT cp = path_cp();
    BOOL lossy = FALSE;
    const int n8 = WideCharToMultiByte(cp, cp == CP_UTF8 ? WC_ERR_INVALID_CHARS : 0, w, -1, buf,
                                       (int)sizeof buf, NULL, cp == CP_UTF8 ? NULL : &lossy);
    if (n8 <= 0 || lossy) {
        /* A name this code page cannot spell: no narrow path names it. */
        errno = n8 <= 0 && GetLastError() == ERROR_INSUFFICIENT_BUFFER ? ENAMETOOLONG : EILSEQ;
        return NULL;
    }
    for (char *p = buf; *p; p++)
        if (*p == '\\') *p = '/';
    size_t n = strlen(buf);
    /* "C:/dir/" names the same place as "C:/dir"; the root keeps its slash. */
    while (n > 3 && buf[n - 1] == '/') buf[--n] = 0;
    char *out = resolved ? resolved : (char *)malloc(n + 1);
    if (!out) {
        errno = ENOMEM;
        return NULL;
    }
    memcpy(out, buf, n + 1);
    return out;
}

int sa2_win32_lstat(const char *path, struct stat *sb) {
    HANDLE h = path_open(path, FILE_FLAG_OPEN_REPARSE_POINT);
    if (h == INVALID_HANDLE_VALUE) return -1;
    FILE_ATTRIBUTE_TAG_INFO ti;
    const BOOL ok = GetFileInformationByHandleEx(h, FileAttributeTagInfo, &ti, sizeof ti);
    CloseHandle(h);
    if (ok && (ti.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)
        && (ti.ReparseTag == IO_REPARSE_TAG_SYMLINK || ti.ReparseTag == IO_REPARSE_TAG_MOUNT_POINT)) {
        memset(sb, 0, sizeof *sb);
        sb->st_mode = (unsigned short)(S_IFLNK | 0777);
        sb->st_nlink = 1;
        return 0;
    }
    return stat(path, sb);
}

ssize_t getline(char **line, size_t *cap, FILE *f) {
    if (!line || !cap || !f) {
        errno = EINVAL;
        return -1;
    }
    size_t n = 0;
    int c;
    while ((c = fgetc(f)) != EOF) {
        if (n + 2 > *cap) {
            size_t want = *cap ? *cap * 2 : 128;
            char *p = (char *)realloc(*line, want);
            if (!p) {
                errno = ENOMEM;
                return -1;
            }
            *line = p;
            *cap = want;
        }
        (*line)[n++] = (char)c;
        if (c == '\n') break;
    }
    if (n == 0) return -1;
    (*line)[n] = 0;
    return (ssize_t)n;
}

/* ------------------------------------------------- the live session's keyboard */

int sa2_win32_is_pipe_name(const char *path) {
    if (!path || strlen(path) <= 9) return 0;
    for (int i = 0; i < 9; i++) {
        const char want = "\\\\.\\pipe\\"[i], got = path[i];
        if (want == '\\' ? (got != '\\' && got != '/') : got != want) return 0;
    }
    return 1;
}

/* The name as WaitNamedPipe and CreateFile parse it, every '/' a '\', since
 * sa2_win32_is_pipe_name accepts "//./pipe/" for "\\.\pipe\". A '/' after the prefix names the
 * same pipe either way: the system normalizes a \\.\ path's separators before the pipe's name
 * is read (CreateNamedPipe as well), so no pipe of the Win32 API has a '/' in its name. NULL
 * with ENAMETOOLONG when it does not fit. */
static const char *pipe_native(const char *path, char *buf, size_t cap) {
    const size_t n = strlen(path);
    if (n >= cap) {
        errno = ENAMETOOLONG;
        return NULL;
    }
    for (size_t i = 0; i <= n; i++) buf[i] = path[i] == '/' ? '\\' : path[i];
    return buf;
}

int sa2_win32_pipe_exists(const char *path) {
    char name[SA2_VFS_PATH_MAX];
    if (!pipe_native(path, name, sizeof name)) return 0;
    if (WaitNamedPipeA(name, 1)) return 1;
    const DWORD e = GetLastError();
    return e != ERROR_FILE_NOT_FOUND && e != ERROR_PATH_NOT_FOUND && e != ERROR_BAD_PATHNAME;
}

int sa2_win32_pipe_open(const char *path) {
    char name[SA2_VFS_PATH_MAX];
    if (!pipe_native(path, name, sizeof name)) return -1;
    HANDLE h = INVALID_HANDLE_VALUE;
    DWORD e = 0;                            /* CreateFile's own error, not the wait's after it */
    for (int tries = 0; tries < 2; tries++) {
        h = CreateFileA(name, GENERIC_READ, 0, NULL, OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE) break;
        e = GetLastError();
        if (e != ERROR_PIPE_BUSY || tries == 1) break;
        WaitNamedPipeA(name, 5000);
    }
    if (h == INVALID_HANDLE_VALUE) {
        errno = e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND ? ENOENT
              : e == ERROR_ACCESS_DENIED ? EACCES : e == ERROR_PIPE_BUSY ? EBUSY : EIO;
        return -1;
    }
    const int fd = _open_osfhandle((intptr_t)h, _O_RDONLY | _O_BINARY | _O_NOINHERIT);
    if (fd < 0) {
        CloseHandle(h);
        errno = EMFILE;
    }
    return fd;
}

/* The writer has gone: the server closed its end or disconnected the pipe. */
static int pipe_ended(DWORD e) {
    return e == ERROR_BROKEN_PIPE || e == ERROR_PIPE_NOT_CONNECTED;
}

ssize_t sa2_win32_pipe_read(int fd, void *buf, size_t n) {
    const HANDLE h = (HANDLE)_get_osfhandle(fd);
    if (h == INVALID_HANDLE_VALUE) {
        errno = EBADF;
        return -1;
    }
    DWORD avail = 0;
    if (!PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL)) {
        if (pipe_ended(GetLastError())) return 0;
        errno = EIO;
        return -1;
    }
    if (!avail) {
        errno = EAGAIN;
        return -1;
    }
    DWORD got = 0;
    const DWORD want = n < (size_t)avail ? (DWORD)n : avail;
    if (!ReadFile(h, buf, want, &got, NULL)) {
        const DWORD e = GetLastError();
        if (pipe_ended(e)) return 0;
        if (e != ERROR_MORE_DATA) {
            errno = EIO;
            return -1;
        }
    }
    return (ssize_t)got;
}

/* How long the console's closing waits for the process to end on its own (below). Windows
 * ends a process about five seconds after the close event whatever its handler does. */
#define CONSOLE_CLOSE_WAIT_MS 4000u

static void (*volatile g_console_stop)(void);
static volatile LONG g_console_stops;

/* A stop asked for: fn sets the flag the next keyboard poll answers with WM_QUIT, and the
 * guest then leaves through its own exit path, which closes the input log (sa2_dinput.c
 * input_exit) and the CRT's streams. Ctrl+C and Ctrl+Break return at once and the process
 * carries on to that exit. The console closing is different: the system ends the process as
 * soon as this returns, so this waits, at most CONSOLE_CLOSE_WAIT_MS, for the process to end by
 * itself. The exit path's end is the process's end: ExitProcess stops every other thread, this
 * one among them, only after the atexit handlers and the CRT's final flush of its streams,
 * which an event set from inside the exit path would not wait for. */
static BOOL WINAPI console_stop(DWORD type) {
    if (type != CTRL_C_EVENT && type != CTRL_BREAK_EVENT && type != CTRL_CLOSE_EVENT)
        return FALSE;
    if (InterlockedIncrement(&g_console_stops) > 1) return FALSE;     /* the default action */
    if (g_console_stop) g_console_stop();
    if (type == CTRL_CLOSE_EVENT) Sleep(CONSOLE_CLOSE_WAIT_MS);
    return TRUE;
}

int sa2_win32_on_console_stop(void (*fn)(void)) {
    g_console_stop = fn;
    InterlockedExchange(&g_console_stops, 0);
    return SetConsoleCtrlHandler(console_stop, TRUE) ? 0 : -1;
}
