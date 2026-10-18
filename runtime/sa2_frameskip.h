/* Play's low-FPS speed fix: N game ticks per drawn frame in stages (runtime/sa2_frameskip.c).
 *
 * Off unless SA2_PLAY_FRAMESKIP is 2, 3 or auto, and always off in a replay. The two level-loop
 * wraps live in the .c file; this header holds what its unit test drives directly: the setting's
 * parse, the Auto policy (a pure function of the samples it is given) and the report's numbers. */
#ifndef SA2_FRAMESKIP_H
#define SA2_FRAMESKIP_H

#include <stddef.h>
#include <stdint.h>

#define SA2_FRAMESKIP_OFF  0
#define SA2_FRAMESKIP_AUTO (-1)
#define SA2_FRAMESKIP_CAP  3        /* the most ticks Auto runs per drawn frame */

/* What SA2_PLAY_FRAMESKIP asks: SA2_FRAMESKIP_OFF, 2, 3 or SA2_FRAMESKIP_AUTO. `line` gets the
 * one line the run prints, or "" when the variable is unset or empty. `replay` is 1 when
 * SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set: the mode is then off and the line says why. */
int sa2_frameskip_setting(const char *value, int replay, char *line, size_t n);

/* ------------------------------------------------------------------ Auto
 *
 * Fed one sample per drawn level frame: the N that frame ran, its wall time (level-loop entry to
 * the next entry: the game's work, its wait on the player through the stream pipe, and the frame
 * boundary) and the game's busy time over the same span (the process's CPU time plus the level
 * loop's thread's run-queue wait; a wait in write(2) or on the guest lock is neither). Returns the
 * N for the next level frame. Integer only:
 * the wraps run inside the lifted game, whose x87 state a floating-point operation could move. */
#define SA2_FS_WINDOW_NS     500000000ull   /* samples are judged over 0.5 s of wall time */
#define SA2_FS_WINDOW_MAX    128            /* or this many frames, whichever comes first */
#define SA2_FS_HOLD_MIN_NS   3000000000ull  /* qualifying time before a step down, at first */
#define SA2_FS_HOLD_MAX_NS   30000000000ull /* the longest hold after repeated reversals */
#define SA2_FS_REVERSAL_NS   2000000000ull  /* a step up this soon after a step down reverses it */
#define SA2_FS_HOLD_RESET_NS 30000000000ull /* a step down this long ago, with no step up since, held */
#define SA2_FS_REFUSE_MIN_NS 30000000000ull /* a step down to an N found late with the game idle waits */
#define SA2_FS_REFUSE_MAX_NS 600000000000ull /* that wait, doubled by each failed retry of that N */

typedef struct {
    int n;                       /* the N level frames run at now, 1 to SA2_FRAMESKIP_CAP */
    int cap;
    /* the window being filled: samples at the current N only */
    int frames;
    uint64_t wall_sum, busy_sum, window_ns;
    /* the spiral guard: set by a step up, judged on the first full window after it */
    int guard_pending, guard_from;
    uint64_t guard_busy_tick;    /* busy per tick before the step up */
    uint64_t up_hold_until, up_hold_ns;
    /* stepping down */
    uint64_t down_ok_ns, down_hold_ns, last_down;
    int down_to;                 /* the N last_down's step down went to while it is judged; 0 once
                                  * it held (SA2_FS_HOLD_RESET_NS with no step up) or a step up
                                  * from that N ended it */
    /* Step downs to an N whose window was late while the game worked under 90 % of it (the
     * player's pace: it renders in another process, and at N+1, paced to N+1 frame periods,
     * nothing shows whether it now keeps N's) are refused until refuse_until[N]. That N's refusal
     * lasts refuse_ns[N]: SA2_FS_REFUSE_MIN_NS, doubled each time a step down to N fails (a step up
     * from N, late with the game idle, before it held), up to SA2_FS_REFUSE_MAX_NS. A step down to
     * N that holds resets N's and every higher N's; a new stage resets them all. */
    uint64_t refuse_until[SA2_FRAMESKIP_CAP + 1], refuse_ns[SA2_FRAMESKIP_CAP + 1];
    int skip;                    /* samples to drop: after a change of N or a gap */
    /* counts for the report */
    uint64_t ups, downs, guards, reversals, windows;
    uint64_t failed;             /* step downs a step up from their N ended before they held */
    uint64_t refusals;           /* windows whose step down a refusal held back */
} sa2_fs_auto;

void sa2_frameskip_auto_init(sa2_fs_auto *a, int cap);
/* A sample: the frame ran `n` ticks, `wall_ns` and `busy_ns` long; `now_ns` the monotonic time;
 * `tick_ns` the period of one tick (the frame period, 16,666,000 ns at 60 Hz). */
int sa2_frameskip_auto_sample(sa2_fs_auto *a, int n, uint64_t wall_ns, uint64_t busy_ns,
                              uint64_t now_ns, uint64_t tick_ns);
/* Drawn frames passed outside the level loop (the pause menu, a menu, a cutscene, a load, a death's
 * restart): the window restarts and the next sample is dropped. N is kept. `new_stage` (CurrentLevel
 * changed, or did not read) ends every refusal and resets its length: the next stage may draw at
 * another pace. A pause or a restart of the same stage keeps them. */
void sa2_frameskip_auto_gap(sa2_fs_auto *a, int new_stage);

#endif
