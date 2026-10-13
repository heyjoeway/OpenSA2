/* The POSIX names the runtime uses, for an x86-64 Windows host (mingw-w64 with UCRT, as
 * `zig cc -target x86_64-windows-gnu` builds it).
 *
 * Read only under _WIN32: sa2_trap.h includes it, so every runtime source that includes
 * sa2_trap.h sees it, and guest_memory.c, sa2_files.c and sa2_dinput.c include it beside
 * their own POSIX headers. A Linux build never reads it, so no Linux object changes.
 *
 * It includes no <windows.h>. The runtime defines Win32's names for the guest (sa2_imports.c
 * and the device headers), and the host's own definitions of them would collide. What needs
 * the Windows API lives in sa2_win32.c and in sa2_trap.c's Windows backend.
 *
 * Each name keeps the POSIX meaning the runtime relies on, or says here where it cannot:
 *  - sigsetjmp/siglongjmp are sa2_trap.c's own register save and restore, with no SEH
 *    unwind, so they can leave a frame of lifted code from a fault the trap backend
 *    redirected (sa2_trap.c). There is no signal mask to save.
 *  - alarm is a watchdog thread (sa2_trap.c): when the deadline passes it stops the thread
 *    that armed it and resumes it in the trap's timeout path, as SIGALRM does on Linux.
 *  - mmap/munmap cover the one mapping the runtime makes, the guest range (guest_memory.c):
 *    anonymous, private, read-write. Anything else is refused with EINVAL.
 *  - realpath resolves every symbolic link and junction, as POSIX's does, through the file's
 *    final path (GetFinalPathNameByHandleW), with forward slashes, and fails for a path that
 *    does not exist (sa2_files.c's escape check walks up until it succeeds). lstat reports a
 *    symbolic link or junction itself (S_IFLNK), dangling or not, as POSIX's does.
 *  - the live session's keyboard FIFO is a named pipe, and SIGTERM a console control event
 *    (sa2_win32_pipe_*, sa2_win32_on_console_stop; sa2_win32.c).
 */
#ifndef SA2_RUNTIME_WIN32_H
#define SA2_RUNTIME_WIN32_H

#if !defined(_WIN32)
#error "sa2_win32.h is the Windows host's POSIX layer; a POSIX host includes its own headers"
#endif
#if !defined(__x86_64__)
#error "sa2_win32.h: the Windows host is x86-64 only"
#endif

#include <direct.h>
#include <errno.h>
#include <fcntl.h>
#include <io.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------- the trap's unwind */

/* sa2_trap.c's own setjmp and longjmp for this host (two short assembly routines there):
 * the registers the Windows x64 calling convention preserves (rbx, rbp, rsi, rdi, r12-r15,
 * xmm6-xmm15), the stack pointer and the resume address, restored with no unwind and no
 * signal mask, which is what Linux's sigsetjmp/siglongjmp do for the runtime. Neither the
 * MXCSR nor the x87 control word is restored, as on Linux: a trap's landing resets both to
 * what a Linux signal handler starts with (sa2_trap.c), and sa2_call puts the host's rounding
 * mode back itself. clang's __builtin_setjmp is not used: on this target it saves the frame
 * address where the frame register points (rbp sits above the frame's base here), so the
 * landing reads its locals at the wrong offsets (a toy run under Wine, 2026-10-11). */
typedef struct { unsigned long long w[32]; } __attribute__((aligned(16))) sa2_win_jmp_buf;
typedef sa2_win_jmp_buf sigjmp_buf[1];
int sa2_win_setjmp(sigjmp_buf env) __attribute__((returns_twice, nothrow));
void sa2_win_longjmp(sigjmp_buf env, int val) __attribute__((noreturn, nothrow));
#define sigsetjmp(env, savemask) sa2_win_setjmp(env)
#define siglongjmp(env, val)     sa2_win_longjmp((env), (val))

/* The wall-clock bound of sa2_call (sa2_trap.c). Returns the seconds left on the previous
 * deadline, 0 for none, as POSIX's does. */
unsigned int alarm(unsigned int seconds);

/* ------------------------------------------------------------ guest memory */

#define PROT_READ      0x1
#define PROT_WRITE     0x2
#define MAP_PRIVATE    0x02
#define MAP_ANONYMOUS  0x20
#define MAP_NORESERVE  0x4000
#define MAP_FAILED     ((void *)-1)
void *sa2_win32_mmap(void *addr, size_t len, int prot, int flags, int fd, int64_t off);
int   sa2_win32_munmap(void *addr, size_t len);
#define mmap   sa2_win32_mmap
#define munmap sa2_win32_munmap

/* malloc's alignment on x86-64 Windows is 16 (MEMORY_ALLOCATION_ALIGNMENT), and the block
 * must stay free()-able, which an _aligned_malloc block is not. */
static inline int posix_memalign(void **out, size_t align, size_t n) {
    if (align == 0 || align > 16 || (align & (align - 1)) != 0) return EINVAL;
    void *p = malloc(n ? n : 1);
    if (!p) return ENOMEM;
    *out = p;
    return 0;
}

/* ------------------------------------------------------------------- files */

/* Paths are in the code page the narrow file API reads them in (AreFileApisANSI): UTF-8 under
 * the application manifest's activeCodePage (tools/s7/win_link.py), the system's ANSI code page
 * without one. */
char   *realpath(const char *path, char *resolved);
ssize_t getline(char **line, size_t *cap, FILE *f);
#ifndef S_IFLNK
#define S_IFLNK          0xA000
#endif
#ifndef S_ISLNK
#define S_ISLNK(m)       (((m) & S_IFMT) == S_IFLNK)
#endif
/* stat, except that a symbolic link or a junction is reported as itself: st_mode S_IFLNK and
 * nothing else, whether or not its target exists. */
int     sa2_win32_lstat(const char *path, struct stat *sb);
#define lstat(path, sb)  sa2_win32_lstat((path), (sb))
#define mkdir(path, mode) _mkdir(path)
#define fsync(fd)        _commit(fd)
#define O_CLOEXEC        _O_NOINHERIT
/* A regular file's open never waits on Windows. The one open that needs O_NONBLOCK for
 * itself, the keyboard FIFO's, is a named pipe on this host, opened and read without
 * waiting by sa2_win32_pipe_open and sa2_win32_pipe_read below (sa2_dinput.c). */
#define O_NONBLOCK       0
#define F_GETFL          3
#define F_SETFL          4
static inline int fcntl(int fd, int cmd, ...) {
    (void)fd;
    if (cmd == F_GETFL || cmd == F_SETFL) return 0;
    errno = EINVAL;
    return -1;
}

/* ------------------------------------------------- the live session's keyboard */

/* sa2_dinput.c's SA2_INPUT_DI_SOURCE on this host. POSIX's is a FIFO the session makes before
 * the run and holds open for writing; here it is a named pipe (\\.\pipe\<name>) the session
 * creates as its server before the run, and the game opens as its client at the first keyboard
 * poll. The records are the same bytes. */
/* \\.\pipe\ or //./pipe/, and a name; the other two take either, and open the pipe by its
 * \\.\pipe\ spelling, every '/' a '\'. */
int     sa2_win32_is_pipe_name(const char *path);
int     sa2_win32_pipe_exists(const char *path);    /* 1 when the session has created it */
int     sa2_win32_pipe_open(const char *path);      /* a read descriptor, or -1 with errno */
/* read() on a FIFO opened O_NONBLOCK: the bytes there are now, up to n; 0 when the writer has
 * gone (the server closed or disconnected the pipe); -1 with errno EAGAIN when none are
 * there yet, or EIO. */
ssize_t sa2_win32_pipe_read(int fd, void *buf, size_t n);
/* SIGTERM's part for a live session: a console control event (Ctrl+Break, Ctrl+C or the
 * console closing) calls fn once and is then left to the default action, as SA_RESETHAND
 * leaves a second SIGTERM. On the console closing, which ends the process when the handler
 * returns, the handler then waits up to 4 s for the process's own exit, so the exit path
 * (the input log's last line, the CRT's streams) runs whole. 0, or -1 when the handler cannot
 * be installed. */
int     sa2_win32_on_console_stop(void (*fn)(void));

#ifdef __cplusplus
}
#endif
#endif
