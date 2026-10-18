/* The stall watch: what the runtime says when the game stops making frames.
 *
 * Subtitle: SA2_STALL_DUMP_MS=<ms> (a live session's play sets it: host/launch, host/android)
 * starts one host thread at the process's start that is not a guest thread, never takes the guest
 * token and takes no signal. From the first frame on, once frames (sa2_sched_frames) have not
 * moved for that long it writes the scheduler's table to stderr, once for the stall however long
 * it lasts; a frame ends the stall, and the next stall gets its own table. Unset, 0 or not a
 * number: no thread. Linux and Android only (the table's host columns are /proc's); elsewhere
 * sa2_stall_start says nothing and starts nothing.
 *
 * A stall is not always a fault. While the player takes no frames (it is paused, as Android pauses
 * an app out of view or pause-unfocused holds a window out of focus, or it has stopped reading),
 * the relay stops handing frames on, and the game waits in its recorder (host/sa2_present_rec.c)
 * for it to read again: in its write of the stream FIFO (SA2_REC_TRACE) or its read of the pace
 * pipe (SA2_REC_PACE_FD). The game cannot see the player's window, so it cannot tell a pause from
 * a player that stopped; when a guest thread is in one of those two calls, the table says, on the
 * line after its first, that the game waits for the player and that a paused player, or an app in
 * the background, looks like this.
 *
 * The table (every line begins "sa2 stall: "): the frame count and the virtual clock, the guest
 * thread the token names (sa2_threads.c g_current), whether the guest lock is held (a guest thread
 * runs, or sleeps in a host call holding it) or free (every guest thread waits on the token), and
 * how many SIGURG nudges the runtime has taken (sa2_threads.c: the relay's stall watch,
 * host/launch/sa2_relay.h). Then one row per guest thread: its host thread id, start address, the
 * scheduler's state, suspend count, wait reason, object and deadline, the last hand-off point it
 * passed, its last import caller and counts (a finished thread's row is counted, not listed), and
 * the kernel's view of its host thread: state (R
 * running, S sleeping, D uninterruptible), wait channel, the system call it is inside with what
 * it is reading or writing (/proc/self/task/<tid>/syscall: a write of the stream FIFO is the
 * recorder's Present, a read of a pipe the pace pipe's wait, futex a wait on the guest token), its
 * allowed CPUs and its last CPU.
 * Then the file layer's read or write in progress (sa2_files.c sa2_vfs_io_now), or none.
 *
 * The table is read without the guest lock: a field a guest thread changes meanwhile can be
 * stale, which in a stall none does. sa2_stall_format, the text, is pure, for the tests.
 */
#ifndef SA2_STALL_H
#define SA2_STALL_H

#include <stddef.h>
#include <stdint.h>

#define SA2_STALL_ROWS 64          /* rows the table shows; the scheduler has fewer */

typedef struct {
    int         index;
    uint32_t    host_tid;          /* 0 before the host thread has started */
    uint32_t    start_va;
    const char *state;             /* the scheduler's: running, ready, blocked, finished */
    int         suspend_count;
    const char *wait;              /* sa2_sched_wait_reason_name */
    uint32_t    wait_object;
    uint64_t    deadline_us;
    const char *where;             /* the last hand-off point passed, "" for none */
    uint32_t    last_caller;
    uint64_t    guest_calls, blocks, wakes;
    /* The kernel's view of the host thread (Linux /proc), filled by the watch; '?' and "" when
     * it cannot be read. */
    char        kstate;
    char        wchan[40];
    char        call[96];          /* "write(5 game.fifo, 1120 bytes)", "" when in none it names */
    char        cpus[48];
    int         last_cpu;
    /* The recorder's wait on the player that the host thread is in (above): "write of the stream
     * FIFO" or "read of the pace pipe", filled by the watch; NULL for none. */
    const char *player_wait;
} sa2_stall_row;

typedef struct {
    double      no_frame_s;
    uint64_t    frames, virtual_us;
    int         current;           /* the guest thread the token names, -1 for none */
    int         lock_held;         /* 1 held, 0 free, -1 not looked at */
    uint64_t    nudges;
    int         rows;
    sa2_stall_row row[SA2_STALL_ROWS];
    /* The row whose thread waits for the player (row[].player_wait), the token's holder first;
     * -1 for none. */
    int         player_row;
    /* The file layer's host call in progress (sa2_vfs_io_now). */
    int         io_active, io_write;
    uint32_t    io_tid;
    const char *io_path;           /* the guest's path */
    uint64_t    io_bytes;
    double      io_for_s;
} sa2_stall_snapshot;

/* The table's text into out[cap], NUL-terminated, cut at a line's end when it does not fit;
 * the length written. */
size_t sa2_stall_format(const sa2_stall_snapshot *s, char *out, size_t cap);

/* Reads SA2_STALL_DUMP_MS and starts the watch's thread (above). Once; sa2_imports.c calls it
 * after sa2_threads_config_from_env. */
void sa2_stall_start(void);

/* The scheduler's part of the table (runtime/sa2_threads.c): every used row into out[max],
 * copied without the guest lock, with the kernel's columns left empty; *current the guest thread
 * the token names, *lock_held whether the guest lock was held (pthread_mutex_trylock, released at
 * once: 1 held, 0 free), *nudges the SIGURGs the runtime has taken. Returns the rows copied. */
int sa2_sched_stall_rows(sa2_stall_row *out, int max, int *current, int *lock_held, uint64_t *nudges);

#endif
