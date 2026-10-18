/* Play's low-FPS speed fix: N game ticks per drawn frame in stages.
 *
 * Subtitle: two lifted functions, linked in every build with `-Wl,-wrap=` (tools/s4/link_s4.py
 * PRODUCTION_WRAPS): sub_43ce20, the level loop, and sub_441ba0, Control. With
 * SA2_PLAY_FRAMESKIP=2 or 3, each drawn frame of a stage runs that many game ticks and the virtual
 * clock moves that many frame periods, so the stage keeps real-time speed at 30 or 20 drawn
 * frames a second. With SA2_PLAY_FRAMESKIP=auto the count follows the device, 1 to 3. Unset,
 * empty, malformed, and in every replay, both wraps only call `__real_`: they read and write no
 * guest memory, and the clock moves one frame per drawn frame as before.
 *
 * Why: a frame the device cannot draw in 16.7 ms slows the whole game, because one game tick is
 * one drawn frame and the virtual clock moves one frame period per drawn frame. At 30 frames a
 * second a stage runs at half speed. The game already has the means to run several ticks per
 * drawn frame: its own PAL catch-up.
 *
 * The game's loop, read from the unpacked image:
 *
 *   0x00433EE0  GameLoop: UpdateControllers (0x0077E780) once, BeginScene (the runtime's frame
 *               boundary, runtime/sa2_d3d9.c frame_wait), GameModeHandler (which reaches the level
 *               loop through 0x0043A780), FUN_0040a8f0 (0x00433FCB, three more Control calls),
 *               `FrameCount += FrameIncrement` and `[[0x01A5A230] + 0x14] += FrameIncrement`
 *               (0x00433FE2 to 0x00433FED), Present. GameLoop is FrameCount's only writer.
 *   0x0043CE20  the level loop, of 1P and 2P battle play alike (while Pose2PStart_PlayerNum, byte
 *               0x0174B009, is set, it hands the frame to the 2P pose loop 0x0043CC20 from inside
 *               and returns, so the wrap covers that loop too). It sets FrameIncrement [0x01DEB50C]
 *               to 1 and the pass counter [0x01DEB510] to 0 (FrameIncrement becomes 2 on every 5th
 *               frame with the PAL flag), then runs `while (counter < FrameIncrement)`, reading
 *               both from memory on every pass. Each pass is Control, the objects, the collisions
 *               and StageTimerUpdate. After the passes it draws once (the display call 0x004704B0
 *               or 0x00470840).
 *   0x00441BA0  Control: copies ControllersRaw[i] (0x01A52918, 0x34 bytes each, the press edges
 *               at +0x10 and the release edges at +0x14) into Controllers[i] (0x01DEFC00) and
 *               derives the jump press, the menu buttons and pause from the copy.
 *
 * What the wraps do:
 *
 *   * At the frame's first Control (counter 0, FrameIncrement 1), the Control wrap writes N into
 *     FrameIncrement. The loop then runs N full ticks and draws once. A frame whose FrameIncrement
 *     is already above 1 is the game's own catch-up, and is left exactly as it is.
 *   * On passes 2 to N the press and release edges in ControllersRaw[0..3] that Control already
 *     delivered this frame are zeroed before it copies them again. UpdateControllers runs once per
 *     GameLoop, so without this every pass would see the same new press: one tap would be N jumps,
 *     or a jump and a homing attack. An edge written during the passes is delivered on the next
 *     one and zeroed after it. The held buttons and the sticks are left alone. When the loop
 *     returns, each zeroed pad whose edges are still zero gets its last edges back, so
 *     FUN_0040a8f0's Control calls after the loop read what they read at one tick per frame.
 *   * Each later pass also starts with FrameCount [0x0174B038] and the counter at
 *     [[0x01A5A230] + 0x14] one higher, so pass k of a frame reads the values tick k reads at one
 *     tick per frame. Left to GameLoop, both would hold still for N passes and then jump N, and a
 *     reader of FrameCount's low bit (jamaExecuter, 0x00620854) would see one parity for the whole
 *     stage at N=2. When the loop returns, FrameIncrement goes back to 1 (if it still holds the N
 *     written), so GameLoop's adds finish both counts at N per drawn frame and the frames after a
 *     stage, which no level loop resets, do not add N. Nothing else reads FrameIncrement outside
 *     the level loops: every other reference in the image is a module loader setting it.
 *   * When the loop returns, the frame's N goes to the scheduler (sa2_sched_owe_frames), and the
 *     next frame boundary, GameLoop's next BeginScene, moves the clock N frame periods. A frame
 *     boundary inside the passes (a frame the game draws and presents from inside the loop, as it
 *     does once around the post-goal save) moves it one, as before; the N waits for GameLoop's.
 *     The frame counter (sa2_sched_frames, and the per-frame tick row) still counts drawn frames.
 *   * The level loop's tail, after the passes, runs once per call: it adds one to two play-time
 *     counters of the save (frames; one is held against five hours) and, in 2P battle, counts
 *     down an overlay's 120 frames. Each step it took is taken N-1 more times when the loop
 *     returns (fs_tail_steps), so the save's play time and the overlay keep real time too. The
 *     game's own catch-up frames undercount them, as they always have.
 *
 * What stays at one tick per drawn frame: everything outside the level loop. Menus, cutscenes
 * (GameMode 8 has its own branch, 0x0077E460), movies and the game's other exec loops (0x00519220,
 * 0x005FB270) run as slowly as before on a slow device, and the clock moves one frame there, so
 * sound and movie pacing stay right. The pause menu (GameState 0x11) is outside the level loop too:
 * its frames run one tick. The pause takes effect when the level loop returns, so a Start pressed
 * on pass k lets the frame's other passes run first, at most N-1 ticks more (a stage run measured
 * the press registering on the same level tick at N=1, 2 and 3; the pause began after that frame's
 * remaining passes). The attract demos run through the level loop and stay in step, since their
 * input is read per pass.
 *
 * Measured (a debug build that wrote FrameIncrement alone, the Metal Harbor route and the attract
 * demos, N forced to 2 and 3): every frame drew once, after its passes; FrameCount and the stage
 * timer advanced N per drawn frame; the main thread read no guest time inside the passes, so moving
 * the clock N frames at the boundary is enough, and the worker threads' timers kept their rates per
 * virtual second; the stage time matched the wall clock within 0.2 % at 30 and 20 frames a second.
 * Three known departures: the level-up message window draws on every pass
 * (EfMsgWnd0Exec_LevelUpDai, 0x006B7170), so it is drawn N times over itself in one frame, which is
 * cosmetic; the frame the game draws inside a pass, above; and the 100-ring mission's win check.
 * The loop's head, before the passes and so once per call, calls AwardWin (0x0043E6D0, at
 * 0x0043CF4F) when MissionNum (0x0174AFE3) is 1, outside 2P battle, on a stage up to 0x3C or 0x46,
 * with RingCount[0] (0x0174B028) at 100 or more and byte 0x0174B002 clear. A 100th ring taken on
 * pass k is checked at the next frame's head, after the frame's other passes: the win comes up to
 * N-1 ticks late (the mission's time up to N-1 ticks longer), and a hit in those ticks that drops
 * the rings under 100 puts it off until they are collected again. The game's own catch-up frames
 * have the same gap.
 *
 * Auto (SA2_FRAMESKIP_AUTO): one sample per drawn level frame, taken at each level-loop entry: the
 * wall time since the previous entry and the game's busy time over it: the process's CPU time plus
 * the level loop's thread's run-queue wait (/proc/thread-self/schedstat), so a core shared with
 * another process counts as the game's own cost. A wait in the stream pipe's write(2), where the
 * game stands when the player is behind, or on the guest lock is neither. Where the schedstat
 * file does not open, busy is CPU time alone and a line says so; where it stops reading, the wait
 * stays at what it read last. At N > 1 the same busy clock is read at the first pass's Control and
 * at the last pass's: the difference is the frame's N-1 undrawn ticks (each a whole tick), and the
 * rest is the drawn tick, the draw and the frame's other work (two clock reads and one pread a
 * frame, Auto only). sa2_frameskip_auto_sample judges the samples over 0.5 s windows:
 *
 *   step up      the frames came more than 10 % later than N frame periods (the player's pace or
 *                the game's work, whichever is behind) in two windows running (1 s; one late
 *                window, a hitch or a spell of shader compiles, is not a pace; within 2 s of a step
 *                down one is enough: it failed), to at most 3, unless the spiral guard's hold is
 *                on;
 *   spiral guard the first window after a step up: when each tick cost the game at least 95 % of
 *                what it did before and the game's busy time still passes 110 % of the new budget,
 *                the frame is bound by game logic, which more ticks per frame only makes worse; N
 *                goes back and step ups wait 3 s, then 6, 12 and 30 s on repeats (reset by a step
 *                up that holds). The hold runs on wall time, into the next stage too. It does not
 *                hold the step up that ends a step down still being judged: that goes back to the
 *                N the step down left, which N could not pass in the hold, so a step down or a
 *                probe made in the hold that fails comes back within a window or two, not at the
 *                hold's end;
 *   step down    the frames on time and N-1's busy time, projected as this frame's less one undrawn
 *                tick, under 75 % of (N-1) frame periods for 3 s, and no refusal on N-1. (The
 *                whole N-tick frame against N-1 periods, as this was until the S25 Edge's alpha.4
 *                session, wanted two ticks under 12.5 ms at N=2: never met where a tick costs
 *                9 to 14 ms, so one late window kept N=2 for the rest of the stage.) Undrawn ticks
 *                not timed: the whole frame, as before. A window late while the game worked under
 *                90 % of it was the player's pace (or a wait); the player renders in its own process
 *                and frames at N+1 are paced to N+1 frame periods, so nothing there shows it get
 *                faster. Step downs to that N are then refused, 30 s at first. Each N keeps its own
 *                length: a step down to N that fails (a step up from N, late with the game idle,
 *                before the step down held for 30 s of level play, however long the frames queued
 *                toward the player took to show it) doubles N's (60, 120 s, up to 10 min); a step
 *                down to N that holds for 30 s of level play resets N's and every higher N's (a
 *                pause or a menu adds nothing to the 30 s: the frames queued toward the player
 *                drain there, so the first window back can be on time where N is not); a new stage
 *                (CurrentLevel changed) resets them all. The hold runs through a refusal. Before
 *                this a player-bound device stepped down after every hold and came back up within a
 *                window, about every 30 s for the whole stage; now its retries thin out (about 4 in
 *                10 min), and a short spell of lateness at an N whose step downs have not been
 *                failing costs about 30 s at N+1, whatever the retries of another N have built up.
 *                A step up within 2 s of a step down reverses it and doubles the 3 s hold (6, 12,
 *                then 30 s), which falls back to 3 s once a step down holds;
 *   probe        at N > 1 with the frames on time, no refusal on N-1, no step down to N still being
 *                judged (it holds after 30 s of level play, which a pause or a hitch puts behind
 *                wall time), and refuse_ns[N-1] passed since N last changed (30 s at first; the
 *                step-down hold after a new stage): one step down to N-1, whose first 150 ms are
 *                not judged. A phone's governor turns N's spare time into a lower clock and holds
 *                the busy time near its utilization target (the S25 Edge: 2650 to 1690 MHz, busy 63
 *                to 84 % of the 33.3 ms frame), so no projection made at N shows whether N-1 fits
 *                at the clock N-1 would get, and none gates the probe: running N-1 shows it. (A
 *                gate at 125 % of N-1's budget, tried first, never let a probe through at N=2 where
 *                the undrawn tick is the 7 to 15 % of a frame's busy time this game shows on a
 *                desktop and the emulator: N=2 held near 26.7 ms projects N=1 at 130 to 145 %.) A
 *                probe a step up ends before it held for 30 s of level play failed, idle or not,
 *                and doubles refuse_ns[N-1] (60, 120 s, up to 10 min); one that held resets it as a
 *                step down does. A device that cannot keep N-1 spends about 0.6 s there per probe,
 *                at about 30, 90, 210 and 450 s, then every 10 min.
 *
 * Each change of N and each step down that held print one line on stderr: why, the deciding
 * window's wall and busy time, its undrawn tick's busy time and N-1's projection, and the refusal
 * or the next probe that follows. A window that finds a step down held and also changes N prints
 * both lines, the held one first.
 *
 * N changes only at a level-loop entry. Frames outside the level loop do not count, and the next
 * stage starts at the N the last one ended with. Auto reads host time, so its choices are not
 * reproducible; the fixed modes are as reproducible as the input.
 *
 * The report, on stderr (the session's game.log): one line when the setting is read; a line every
 * 10 s of wall time while stages run, with the level frames by N, the undrawn ticks a second, the
 * frames' wall time and the game's busy time (p50, p90), the stage timer's advance against the wall
 * time it took (the speed the fix is for: 100 % is real time) and the edge rule's work; and at exit
 * the totals and three histograms (wall time and busy time per frame, busy time per tick). The
 * frameskip lines are the record that a session ran with the mode on; a session's tick tables
 * (SA2_LAUNCH_TICKS, SA2_PLAY_TICKS) still hold one row per drawn frame, which is N ticks in a
 * stage, so a per-frame latency read from them is N times coarser there.
 *
 * Documentation/scripts/tests/test_frameskip.py drives the wraps over a stand-in loop. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#if defined(__linux__)
#include <fcntl.h>
#include <unistd.h>
#endif

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_frameskip.h"
#include "sa2_threads.h"

void *__real_sub_43ce20(void *state, uint32_t pc, void *memory);
void *__real_sub_441ba0(void *state, uint32_t pc, void *memory);

#define FS_FRAME_INCREMENT 0x01DEB50Cu
#define FS_PASS_COUNTER    0x01DEB510u
#define FS_FRAME_COUNT     0x0174B038u
#define FS_TICK_OBJECT     0x01A5A230u  /* a pointer; GameLoop adds FrameIncrement at its +0x14 */
#define FS_STAGE_TIMER     0x0174AFDBu  /* TimerMinutes, TimerSeconds, TimerFrames: the stage's clock */
#define FS_CURRENT_LEVEL   0x01934B70u  /* CurrentLevel, int16: Auto's refusals end with the stage */
#define FS_PADS_RAW        0x01A52918u
#define FS_PAD_SIZE        0x34u
#define FS_PAD_EDGES       0x10u        /* press, then release */
#define FS_PADS            4
/* The level loop's tail, after the passes, once per call (0x0043D139 to 0x0043D228; in 1P and 2P
 * battle play alike, and the 2P pose loop returns before it): with byte 0x0174AFDE (TwoPlayerMode)
 * set, a positive word at 0x01A55938 (120 frames from 0x0043CBFA) draws its overlay (0x00440EC0)
 * and counts down one; and two play-time counters in frames, in the save block at 0x01DEC600, each
 * go up one below 0x7FFFFF00, the first only while byte 0x01DEC60E is under 180. 0x00665B56 holds
 * the second against 0x107AC0, five hours of frames. */
#define FS_PLAY_TIME       0x01DEC628u  /* the two counters, 4 bytes each */
#define FS_PLAY_TIME_CAP   0x7FFFFF00
#define FS_OVERLAY_COUNT   0x01A55938u
#define FS_DEFAULT_TICK_NS 16666000ull
#define FS_REPORT_NS       10000000000ull

/* ------------------------------------------------------------------ the setting */

static int fs_word(const char *v, const char *w) {
    for (; *v && *w; v++, w++)
        if ((*v | 0x20) != *w) return 0;
    return !*v && !*w;
}

int sa2_frameskip_setting(const char *value, int replay, char *line, size_t n) {
    line[0] = 0;
    if (!value || !*value) return SA2_FRAMESKIP_OFF;
    if (replay) {
        snprintf(line, n, "frameskip: SA2_PLAY_FRAMESKIP=%s ignored in a replay (SA2_REPLAY_TICKS "
                 "or SA2_REPLAY_WORDS is set)", value);
        return SA2_FRAMESKIP_OFF;
    }
    if (fs_word(value, "off")) {
        snprintf(line, n, "frameskip: off (SA2_PLAY_FRAMESKIP=%s)", value);
        return SA2_FRAMESKIP_OFF;
    }
    if ((value[0] == '2' || value[0] == '3') && !value[1]) {
        const int k = value[0] - '0';
        snprintf(line, n, "frameskip: on (SA2_PLAY_FRAMESKIP=%s): stages run %d game ticks per "
                 "drawn frame and the clock moves %d frames per drawn frame there; menus, "
                 "cutscenes and movies stay at 1", value, k, k);
        return k;
    }
    if (fs_word(value, "auto")) {
        snprintf(line, n, "frameskip: on (SA2_PLAY_FRAMESKIP=%s): stages run 1 to %d game ticks "
                 "per drawn frame, chosen from the game's busy time and the frames' pace; menus, "
                 "cutscenes and movies stay at 1", value, SA2_FRAMESKIP_CAP);
        return SA2_FRAMESKIP_AUTO;
    }
    snprintf(line, n, "frameskip: SA2_PLAY_FRAMESKIP=%s is not 2, 3, auto or off; the mode stays "
             "off", value);
    return SA2_FRAMESKIP_OFF;
}

/* ------------------------------------------------------------------ Auto */

static uint64_t fs_next_hold(uint64_t hold) {
    /* 3, 6, 12, then 30 s */
    const uint64_t next = hold * 2u;
    return next > 4u * SA2_FS_HOLD_MIN_NS ? SA2_FS_HOLD_MAX_NS : next;
}

static uint64_t fs_doubled_refusal(uint64_t r) {
    return r * 2u > SA2_FS_REFUSE_MAX_NS ? SA2_FS_REFUSE_MAX_NS : r * 2u;
}

static void fs_window_reset(sa2_fs_auto *a) {
    a->frames = a->timed = 0;
    a->wall_sum = a->busy_sum = a->window_ns = a->undrawn_sum = 0;
}

static void fs_set_n(sa2_fs_auto *a, int n, uint64_t now_ns) {
    a->n = n;
    fs_window_reset(a);
    a->skip = 1;                 /* the first frame at a new N, while the pipe and the pace settle */
    a->down_ok_ns = 0;
    a->prev_late = 0;
    a->changed_at = now_ns;
    a->probe_at = n > 1 ? now_ns + a->refuse_ns[n - 1] : 0;
}

static void fs_decide(sa2_fs_auto *a, int why, int from, int to, uint64_t now_ns) {
    a->why = why;
    a->why_from = from;
    a->why_to = to;
    a->why_at = now_ns;
    a->why_age = now_ns - a->last_down;
    a->why_run = a->down_run_ns;
    a->why_span = now_ns - a->changed_at;
    a->why_seq++;
}

/* Refusals of `from` and every higher N end, and their lengths go back to the minimum. */
static void fs_refusals_end(sa2_fs_auto *a, int from) {
    for (int k = from; k <= SA2_FRAMESKIP_CAP; k++) {
        a->refuse_until[k] = 0;
        a->refuse_ns[k] = SA2_FS_REFUSE_MIN_NS;
    }
}

void sa2_frameskip_auto_init(sa2_fs_auto *a, int cap) {
    memset(a, 0, sizeof *a);
    a->cap = cap < 1 ? 1 : cap > SA2_FRAMESKIP_CAP ? SA2_FRAMESKIP_CAP : cap;
    a->n = 1;
    a->up_hold_ns = a->down_hold_ns = SA2_FS_HOLD_MIN_NS;
    fs_refusals_end(a, 0);
    a->skip = 1;
}

void sa2_frameskip_auto_gap(sa2_fs_auto *a, int new_stage, uint64_t now_ns) {
    fs_window_reset(a);
    a->skip = 1;
    a->down_ok_ns = 0;
    a->prev_late = 0;            /* late windows on either side of a gap are not two running */
    a->guard_pending = 0;        /* a step up that left the stage before its window: not judged */
    if (new_stage) {
        fs_refusals_end(a, 0);
        a->down_to = a->down_probe = 0;   /* a step down from the last stage is not judged in this one */
        if (a->n > 1) a->probe_at = now_ns + a->down_hold_ns;
    }
}

static void fs_step_down(sa2_fs_auto *a, int probe, uint64_t now_ns) {
    a->downs++;
    a->probes += (uint64_t)probe;
    a->last_down = now_ns;
    a->down_run_ns = 0;
    a->down_to = a->n - 1;
    a->down_probe = probe;
    fs_decide(a, probe ? SA2_FS_WHY_PROBE : SA2_FS_WHY_PROJECTION, a->n, a->n - 1, now_ns);
    fs_set_n(a, a->n - 1, now_ns);
    /* A probe's N-1 runs on the clock the device gave N's lighter load until its governor sees
     * the new one: its first 150 ms are not judged, so the window that can reverse it alone is
     * not made of that ramp. (The unit test's governor, half the way to its target each frame,
     * is up within a few frames: there the settle only moves a failed probe's step up about
     * 0.1 s later. A governor that waits longer before it raises the clock would fail a probe on
     * the ramp's frames.) */
    if (probe) a->settle_until = now_ns + SA2_FS_SETTLE_NS;
}

int sa2_frameskip_auto_sample(sa2_fs_auto *a, int n, uint64_t wall_ns, uint64_t busy_ns,
                              uint64_t undrawn_ns, uint64_t now_ns, uint64_t tick_ns) {
    if (!tick_ns) tick_ns = FS_DEFAULT_TICK_NS;
    if (n != a->n) return a->n;  /* the game's own catch-up, or a frame from before a change */
    if (a->skip > 0) {
        a->skip--;
        return a->n;
    }
    if (now_ns < a->settle_until) return a->n;
    const uint64_t budget = (uint64_t)a->n * tick_ns;
    /* One long frame (a load's hitch) weighs at most two budgets, in the means and in the window's
     * length both, so it neither decides a window nor closes one early. */
    const uint64_t clamp = 2u * budget;
    const uint64_t wall_c = wall_ns < clamp ? wall_ns : clamp;
    const uint64_t busy_c = busy_ns < clamp ? busy_ns : clamp;
    a->frames++;
    a->wall_sum += wall_c;
    a->busy_sum += busy_c;
    a->window_ns += wall_c;
    if (a->n > 1) {
        if (undrawn_ns) {
            a->timed++;
            a->undrawn_sum += undrawn_ns < busy_c ? undrawn_ns : busy_c;
        } else {
            a->untimed++;
        }
    }
    if (a->frames < 4 || (a->window_ns < SA2_FS_WINDOW_NS && a->frames < SA2_FS_WINDOW_MAX))
        return a->n;
    const uint64_t span = a->window_ns;
    const uint64_t wall = a->wall_sum / (uint64_t)a->frames, busy = a->busy_sum / (uint64_t)a->frames;
    const uint64_t busy_tick = busy / (uint64_t)a->n;
    /* N-1's busy time, projected: this frame's less one undrawn tick (the drawn tick, the draw
     * and the rest of the frame stay). Undrawn ticks not timed: the whole frame, as before. */
    const uint64_t u = a->n > 1 && a->timed ? a->undrawn_sum / (uint64_t)a->timed / (uint64_t)(a->n - 1) : 0;
    const uint64_t proj = busy > u ? busy - u : 0;
    a->windows++;
    a->w_n = a->n;
    a->w_frames = a->frames;
    a->w_timed = a->timed;
    a->w_wall = wall;
    a->w_busy = busy;
    a->w_undrawn_tick = u;
    a->w_proj = proj;
    a->w_tick = tick_ns;
    fs_window_reset(a);
    if (a->down_to) a->down_run_ns += span;   /* level play since the step down */
    const int late = wall * 100u > budget * 110u;
    /* late frames the game's own work does not explain: the player's pace, or a wait */
    const int idle = busy * 100u < wall * 90u;
    a->w_idle = idle;
    if (a->guard_pending) {
        a->guard_pending = 0;
        if (busy_tick * 100u >= a->guard_busy_tick * 95u && busy * 100u > budget * 110u) {
            a->guards++;
            a->up_hold_until = now_ns + a->up_hold_ns;
            a->up_hold_ns = fs_next_hold(a->up_hold_ns);
            fs_decide(a, SA2_FS_WHY_GUARD, a->n, a->guard_from, now_ns);
            fs_set_n(a, a->guard_from, now_ns);
            return a->n;
        }
        a->up_hold_ns = SA2_FS_HOLD_MIN_NS;   /* the step up holds */
    }
    /* A step up takes two windows running late (1 s): one late window is a hitch or a spell of
     * shader compiles, not a pace. Within 2 s of a step down, one: it failed. */
    const int reversing = a->down_to && now_ns - a->last_down < SA2_FS_REVERSAL_NS;
    const int up = late && (a->prev_late || reversing);
    a->prev_late = late;
    /* The spiral guard's hold keeps N from rising past the N it went back to. A step down still
     * judged (down_to) left an N at or below that one, since N cannot rise in the hold, so the
     * step up that ends it, back to the N it left, is not held: a new stage's probe (3 s in, while
     * a hold from the last stage runs on) or a projection step down after a guard would otherwise
     * keep a late N-1 to the hold's end, up to 30 s, not the 0.6 s a failed probe takes. */
    if (up && a->n < a->cap && (now_ns >= a->up_hold_until || a->down_to)) {
        uint64_t *refuse = &a->refuse_ns[a->n];
        int why = SA2_FS_WHY_LATE;
        if (a->down_to) {
            /* The step down to this N failed before it held: however late the lateness showed (a
             * queue of frames between the game and the player can hide it for seconds), the
             * device did not keep this N's pace, and the next retry of it waits twice as long. A
             * failed probe doubles it whatever the window's busy time said: the probe is the
             * retry of an N the game's work at N could not clear. */
            a->down_to = 0;
            a->failed++;
            if (a->down_probe) {
                a->probes_failed++;
                *refuse = fs_doubled_refusal(*refuse);
                why = SA2_FS_WHY_PROBE_FAILED;
            } else {
                if (idle) *refuse = fs_doubled_refusal(*refuse);
                if (reversing) {
                    a->reversals++;
                    a->down_hold_ns = fs_next_hold(a->down_hold_ns);
                }
                why = SA2_FS_WHY_DOWN_FAILED;
            }
            a->down_probe = 0;
        }
        if (idle) a->refuse_until[a->n] = now_ns + *refuse;
        a->guard_pending = 1;
        a->guard_from = a->n;
        a->guard_busy_tick = busy_tick;
        a->ups++;
        fs_decide(a, why, a->n, a->n + 1, now_ns);
        fs_set_n(a, a->n + 1, now_ns);
        return a->n;
    }
    if (a->down_to && !late && a->down_run_ns >= SA2_FS_HOLD_RESET_NS) {
        /* The step down held: the device keeps this N's pace, so every higher N's too. Counted
         * on level play: a pause or a menu neither confirms nor fails it (the frames queued
         * toward the player drain there, so the first window back can be on time where N is
         * not). Its line is its own (held_seq): this window can also probe or step down. */
        a->down_hold_ns = SA2_FS_HOLD_MIN_NS;
        fs_refusals_end(a, a->down_to);
        a->held_n = a->n;
        a->held_at = now_ns;
        a->held_run = a->down_run_ns;
        a->held_age = now_ns - a->last_down;
        a->held_seq++;
        a->down_to = a->down_probe = 0;
    }
    const uint64_t lower = (uint64_t)(a->n - 1) * tick_ns;
    const int on_time = a->n > 1 && wall * 100u <= budget * 105u;
    const int could = on_time && proj * 100u < lower * SA2_FS_DOWN_PCT;
    if (could) {
        /* The hold runs through a refusal: the two measure different things (the game's work,
         * the player's pace), so a short spell of lateness costs the longer of them, not both. */
        a->down_ok_ns += span;
        if (now_ns < a->refuse_until[a->n - 1]) {
            a->refusals++;
        } else if (a->down_ok_ns >= a->down_hold_ns) {
            fs_step_down(a, 0, now_ns);
        }
    } else {
        a->down_ok_ns = 0;
        /* No projection gates the probe: one made at N, on the clock a governor lowered on N's
         * spare time, cannot show whether N-1 fits at the clock N-1 would get. A device that
         * cannot keep N-1 fails each probe in about 0.6 s, and the doubling waits bound them.
         * Nor does a probe come while the step down to N is judged (down_to): its hold counts
         * level play and probe_at wall time, so a hitch, the skipped frame or a pause would bring
         * the probe due first, and the probe's step down would end N's unjudged (no held line,
         * N's refusal length not reset). The window that finds it held clears down_to above, and
         * a probe already due comes in that window. */
        if (on_time && !a->down_to && now_ns >= a->probe_at && now_ns >= a->refuse_until[a->n - 1])
            fs_step_down(a, 1, now_ns);
    }
    return a->n;
}

/* ns as "x.y" (tenths of a millisecond, or of a second) */
#define FS_MS1(t) (unsigned long long)((t) / 1000000ull), (unsigned long long)((t) / 100000ull % 10ull)
#define FS_S1(t)  (unsigned long long)((t) / 1000000000ull), (unsigned long long)((t) / 100000000ull % 10ull)
#define FS_S(t)   (unsigned long long)((t) / 1000000000ull)

static unsigned long long fs_pct(uint64_t x, uint64_t of) { return of ? (unsigned long long)(x * 100u / of) : 0; }

void sa2_frameskip_auto_why(const sa2_fs_auto *a, uint64_t t_ns, char *line, size_t n) {
    static const char *const names[] = {"", "late", "step down failed", "probe failed", "spiral guard",
                                        "projection", "probe"};
    size_t k = 0;
    if (!n) return;
    line[0] = 0;
    if (a->why <= SA2_FS_WHY_NONE || a->why > SA2_FS_WHY_PROBE) return;
#define FS_PUT(...) do { if (k < n) { const int r_ = snprintf(line + k, n - k, __VA_ARGS__); \
                         if (r_ > 0) k += (size_t)r_; } } while (0)
    const int from = a->why_from, to = a->why_to;
    const uint64_t budget = (uint64_t)a->w_n * a->w_tick, lower = (uint64_t)(a->w_n - 1) * a->w_tick;
    FS_PUT("frameskip: auto N %d -> %d at %llu.%llu s (%s)", from, to, FS_S1(t_ns), names[a->why]);
    FS_PUT(": %d frames at N=%d, wall %llu.%llu ms (%llu%% of %llu.%llu), busy %llu.%llu ms%s",
           a->w_frames, a->w_n, FS_MS1(a->w_wall), fs_pct(a->w_wall, budget), FS_MS1(budget),
           FS_MS1(a->w_busy), a->w_idle && a->w_wall * 100u > budget * 110u ?
           " (late and idle: the player's pace or a wait)" : "");
    if (a->w_n > 1 && a->w_timed)
        FS_PUT(", undrawn tick %llu.%llu ms, so %llu.%llu ms at N=%d (%llu%% of its budget)",
               FS_MS1(a->w_undrawn_tick), FS_MS1(a->w_proj), a->w_n - 1, fs_pct(a->w_proj, lower));
    else if (a->w_n > 1)
        FS_PUT(", undrawn ticks not timed (judged on the whole frame)");
    switch (a->why) {
    case SA2_FS_WHY_GUARD:
        FS_PUT("; busy per tick %llu.%llu ms against %llu.%llu at N=%d: bound by game logic, step "
               "ups wait %llu s", FS_MS1(a->w_busy / (uint64_t)a->w_n), FS_MS1(a->guard_busy_tick),
               to, FS_S(a->up_hold_until - a->why_at));
        return;
    case SA2_FS_WHY_PROJECTION:
        FS_PUT("; under %d%% of N=%d's budget through %llu s", SA2_FS_DOWN_PCT, to, FS_S(a->down_hold_ns));
        return;
    case SA2_FS_WHY_PROBE:
        FS_PUT("; %llu s after N=%d began: a step up before %llu s of level play doubles N=%d's wait "
               "to %llu s", FS_S(a->why_span), from, FS_S(SA2_FS_HOLD_RESET_NS), to,
               FS_S(fs_doubled_refusal(a->refuse_ns[to])));
        return;
    case SA2_FS_WHY_LATE:
        FS_PUT("; late two windows running");
        break;
    case SA2_FS_WHY_DOWN_FAILED:
        FS_PUT("; %llu.%llu s after the step down to N=%d%s", FS_S1(a->why_age), from,
               a->why_age < SA2_FS_REVERSAL_NS ? ", within 2 s: the step-down hold doubles" : "");
        break;
    case SA2_FS_WHY_PROBE_FAILED:
        FS_PUT("; %llu.%llu s after the probe of N=%d", FS_S1(a->why_age), from);
        break;
    }
    if ((a->why == SA2_FS_WHY_DOWN_FAILED || a->why == SA2_FS_WHY_PROBE_FAILED) &&
        a->why_age >= a->why_run + 1000000000ull)   /* a pause or a menu in between */
        FS_PUT(", %llu.%llu s of it level play", FS_S1(a->why_run));
    if (a->refuse_until[from] > a->why_at)
        FS_PUT("; step downs to N=%d refused %llu s", from, FS_S(a->refuse_until[from] - a->why_at));
    if (a->probe_at > a->why_at)
        FS_PUT("; next probe of N=%d in %llu s", from, FS_S(a->probe_at - a->why_at));
#undef FS_PUT
}

void sa2_frameskip_auto_held(const sa2_fs_auto *a, uint64_t t_ns, char *line, size_t n) {
    if (!n) return;
    line[0] = 0;
    if (!a->held_seq) return;
    snprintf(line, n, "frameskip: auto N=%d held at %llu.%llu s: %llu.%llu s of level play since the "
             "step down (%llu.%llu s of wall time); step downs to N=%d and above are no longer refused",
             a->held_n, FS_S1(t_ns), FS_S1(a->held_run), FS_S1(a->held_age), a->held_n);
}

/* ------------------------------------------------------------------ the report's numbers */

#define FS_HIST 256                  /* 0.5 ms buckets: 0 to 128 ms, the last one everything above */

typedef struct {
    uint64_t level[SA2_FRAMESKIP_CAP + 1];   /* level frames by the N they ran ([1] to [3]) */
    uint64_t undrawn;                        /* ticks beyond one per drawn frame */
    uint64_t catchup;                        /* frames the game's own catch-up ran */
    uint64_t no_first;                       /* level frames with no first-pass Control seen */
    uint64_t zero_passes, zero_edges, kept;  /* edge zeroing: passes, passes with an edge, kept */
    uint64_t new_edges;                      /* edges written in the passes, delivered once */
    uint64_t clock_inside;                   /* the clock moved inside the level loop */
    uint64_t fi_kept;                        /* FrameIncrement changed in the passes: left as is */
    uint64_t tail;                           /* frames whose tail steps were taken N-1 more times */
    uint64_t boundaries;                     /* sa2_sched_frames() at the period's start */
    uint64_t start_ns, samples;
    uint64_t stage_ticks, stage_wall;        /* the stage timer's advance, and the wall time it took */
    uint32_t wall[FS_HIST], busy[FS_HIST], tick[FS_HIST];
} fs_stats;

static int g_mode = -2;              /* -2 unread; SA2_FRAMESKIP_OFF, 2, 3 or SA2_FRAMESKIP_AUTO */
static int g_in_loop, g_decided, g_catchup, g_frame_n, g_target, g_unreadable_said;
static int g_steps, g_step_failed;   /* the later passes' counter steps this frame */
static uint32_t g_edges[FS_PADS][2];  /* per pad, the press and release edges last delivered */
static int g_zeroed[FS_PADS];          /* the pad's edges are zeroed, to restore at the loop's return */
static sa2_fs_auto g_auto;
static fs_stats g_period, g_total;
static uint64_t g_prev_wall, g_prev_cpu, g_prev_frames;
static int g_have_prev, g_prev_n, g_prev_ok, g_prev_timer_ok;
/* Auto only: the busy clock at the frame's first Control, the busy time of its undrawn ticks (to
 * the last pass's Control; 0 until then), the previous level frame's, and the decisions printed. */
static uint64_t g_pass_busy0, g_undrawn, g_prev_undrawn, g_why_seen, g_held_seen;
static int g_untimed_said;
static uint32_t g_prev_timer;
static int g_level = -1;              /* CurrentLevel at the last level frame, -1 unread */
static uint64_t g_t0_ns;

static void fs_clocks(uint64_t *wall, uint64_t *cpu) {
    struct timespec t;
    *wall = *cpu = 0;
    if (clock_gettime(CLOCK_MONOTONIC, &t) == 0)
        *wall = (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
    if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t) == 0)
        *cpu = (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}

/* The level loop's thread's run-queue wait so far (ns): the time it was ready and another thread
 * or process had the core, the second field of /proc/thread-self/schedstat (the Android session
 * reads the game's threads' schedstat too). Opened on the first level frame, on that thread. When
 * it does not open (no procfs, Windows), 0 and one line: busy time is then CPU time alone, and a
 * core shared with another process looks like a slow player. When it stops reading after it has
 * (the fd names a thread, which can exit), the last wait read is returned from then on, and one
 * line: busy time is CPU time from there, and no frame's busy time loses the wait counted so far
 * (busy is a difference of two readings; a drop to 0 would make one sample wrap). The unit test
 * builds with SA2_FS_SCHEDSTAT naming a file of its own. */
#ifndef SA2_FS_SCHEDSTAT
#define SA2_FS_SCHEDSTAT "/proc/thread-self/schedstat"
#endif
static int g_sched_fd = -2;          /* -2 unopened, -1 unreadable */
static uint64_t g_sched_wait;        /* the last wait read */

static uint64_t fs_run_wait(void) {
#if defined(__linux__)
    if (g_sched_fd == -2) {
        g_sched_fd = open(SA2_FS_SCHEDSTAT, O_RDONLY | O_CLOEXEC);
        if (g_sched_fd < 0) {
            g_sched_fd = -1;
            fprintf(stderr, "frameskip: %s did not open; the game's busy time is its CPU time "
                    "alone, without the level loop's run-queue wait\n", SA2_FS_SCHEDSTAT);
        }
    }
    if (g_sched_fd >= 0) {
        char b[128];
        const ssize_t n = pread(g_sched_fd, b, sizeof b - 1, 0);
        char *end = b;
        if (n > 0) {
            b[n] = 0;
            (void)strtoull(b, &end, 10);               /* its time on a CPU */
            const char *at = end;
            const unsigned long long wait = strtoull(at, &end, 10);   /* its time waiting for one */
            if (end != at) return g_sched_wait = (uint64_t)wait;
        }
        close(g_sched_fd);
        g_sched_fd = -1;
        fprintf(stderr, "frameskip: %s did not read; the game's busy time is its CPU time alone "
                "from here (the run-queue wait stays at the %llu ms read last)\n", SA2_FS_SCHEDSTAT,
                (unsigned long long)(g_sched_wait / 1000000ull));
    }
#else
    if (g_sched_fd == -2) {
        g_sched_fd = -1;
        fprintf(stderr, "frameskip: no /proc here; the game's busy time is its CPU time alone, "
                "without the level loop's run-queue wait\n");
    }
#endif
    return g_sched_wait;
}

/* The busy clock alone (the process's CPU time plus the level loop's thread's run-queue wait), as
 * fs_frame_enter reads it: Auto's per-pass stamps. */
static uint64_t fs_busy_clock(void) {
    struct timespec t;
    uint64_t cpu = 0;
    if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t) == 0)
        cpu = (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
    return cpu + fs_run_wait();
}

static uint64_t fs_tick_ns(void) {
    const uint64_t us = sa2_sched_frame_us();
    return us ? us * 1000ull : FS_DEFAULT_TICK_NS;
}

static void fs_hist_add(uint32_t *h, uint64_t ns) {
    const uint64_t b = ns / 500000ull;
    h[b < FS_HIST ? b : FS_HIST - 1]++;
}

/* The bucket holding the q-th per-mille sample, as tenths of a millisecond (its lower edge). */
static uint64_t fs_hist_q(const uint32_t *h, uint64_t total, unsigned q) {
    if (!total) return 0;
    const uint64_t want = (total * q + 999u) / 1000u;
    uint64_t seen = 0;
    for (int b = 0; b < FS_HIST; b++) {
        seen += h[b];
        if (seen >= want) return (uint64_t)b * 5u;
    }
    return (uint64_t)(FS_HIST - 1) * 5u;
}

static void fs_stats_start(fs_stats *s, uint64_t now_ns) {
    memset(s, 0, sizeof *s);
    s->start_ns = now_ns;
    s->boundaries = sa2_sched_frames();
}

static void fs_count_frame(fs_stats *s, int n, int decided) {
    s->level[n < 1 ? 1 : n > SA2_FRAMESKIP_CAP ? SA2_FRAMESKIP_CAP : n]++;
    s->undrawn += (uint64_t)(n - 1);
    if (!decided) s->no_first++;
}

static void fs_count_sample(fs_stats *s, int n, uint64_t wall, uint64_t busy) {
    s->samples++;
    fs_hist_add(s->wall, wall);
    fs_hist_add(s->busy, busy);
    fs_hist_add(s->tick, busy / (uint64_t)(n > 0 ? n : 1));
}

#define MS(t) (unsigned long long)((t) / 10u), (unsigned long long)((t) % 10u)

static void fs_line(const char *what, const fs_stats *s, uint64_t now_ns) {
    const uint64_t span_ms = (now_ns - s->start_ns) / 1000000ull;
    const uint64_t frames = s->level[1] + s->level[2] + s->level[3];
    const uint64_t boundaries = sa2_sched_frames() - s->boundaries;
    const uint64_t other = boundaries > frames ? boundaries - frames : 0;
    const uint64_t rate10 = span_ms ? s->undrawn * 10000ull / span_ms : 0;   /* tenths per s */
    const uint64_t stage_ms = s->stage_ticks * 1000ull / 60ull, stage_wall_ms = s->stage_wall / 1000000ull;
    const uint64_t speed = stage_wall_ms ? stage_ms * 1000ull / stage_wall_ms : 0;   /* per mille */
    char mode[96];
    if (g_mode == SA2_FRAMESKIP_AUTO)
        snprintf(mode, sizeof mode, "auto at N=%d (%llu up, %llu down, %llu guard)", g_auto.n,
                 (unsigned long long)g_auto.ups, (unsigned long long)g_auto.downs,
                 (unsigned long long)g_auto.guards);
    else
        snprintf(mode, sizeof mode, "N=%d", g_mode);
    fprintf(stderr, "frameskip: %s %llu.%llu s, %s: %llu level frames (N=1 %llu, N=2 %llu, N=3 %llu), "
            "%llu undrawn ticks (%llu.%llu/s); %llu other frames at N=1; frame wall p50 %llu.%llu "
            "p90 %llu.%llu ms, game busy p50 %llu.%llu p90 %llu.%llu ms, per tick p50 %llu.%llu ms "
            "(%llu samples); stage timer +%llu.%llu s in %llu.%llu s of wall time (%llu.%llu%%); "
            "edges zeroed on %llu of %llu later passes\n", what,
            (unsigned long long)(span_ms / 1000u), (unsigned long long)(span_ms % 1000u / 100u), mode,
            (unsigned long long)frames, (unsigned long long)s->level[1],
            (unsigned long long)s->level[2], (unsigned long long)s->level[3],
            (unsigned long long)s->undrawn, MS(rate10), (unsigned long long)other,
            MS(fs_hist_q(s->wall, s->samples, 500)), MS(fs_hist_q(s->wall, s->samples, 900)),
            MS(fs_hist_q(s->busy, s->samples, 500)), MS(fs_hist_q(s->busy, s->samples, 900)),
            MS(fs_hist_q(s->tick, s->samples, 500)), (unsigned long long)s->samples,
            (unsigned long long)(stage_ms / 1000u), (unsigned long long)(stage_ms % 1000u / 100u),
            (unsigned long long)(stage_wall_ms / 1000u), (unsigned long long)(stage_wall_ms % 1000u / 100u),
            MS(speed), (unsigned long long)s->zero_edges, (unsigned long long)s->zero_passes);
}

/* A histogram in 1 ms buckets, "lower edge:count", the empty ones left out. */
static void fs_hist_line(const char *what, const uint32_t *h) {
    fprintf(stderr, "frameskip: %s, ms:count", what);
    for (int b = 0; b + 1 < FS_HIST; b += 2) {
        const uint64_t c = (uint64_t)h[b] + h[b + 1];
        if (c) fprintf(stderr, " %d:%llu", b / 2, (unsigned long long)c);
    }
    fprintf(stderr, "\n");
}

static void fs_exit(void) {
    uint64_t now, cpu;
    fs_clocks(&now, &cpu);
    fs_line("total", &g_total, now);
    fprintf(stderr, "frameskip: total: %llu frames the game's own catch-up ran as it does, %llu "
            "level frames with no first-pass Control (left at 1), %llu edges the passes wrote "
            "(each delivered once), %llu zeroed edges kept as the passes left them, %llu frames "
            "with the clock moved inside the level loop, %llu whose counts GameLoop advanced N at "
            "once (FrameIncrement changed in the passes, or a step failed), %llu whose tail's "
            "play-time and overlay steps were taken N-1 more times",
            (unsigned long long)g_total.catchup, (unsigned long long)g_total.no_first,
            (unsigned long long)g_total.new_edges, (unsigned long long)g_total.kept,
            (unsigned long long)g_total.clock_inside, (unsigned long long)g_total.fi_kept,
            (unsigned long long)g_total.tail);
    if (g_mode == SA2_FRAMESKIP_AUTO)
        fprintf(stderr, "; auto: %llu windows, %llu step downs failed before they held (%llu "
                "within 2 s), %llu probes (%llu failed), %llu windows whose step "
                "down a refusal held back (refusal of N=1 %llu s, of N=2 %llu s), step-down hold %llu s, "
                "step-up hold %llu s, %llu samples at N>1 whose undrawn ticks were not timed",
                (unsigned long long)g_auto.windows, (unsigned long long)g_auto.failed,
                (unsigned long long)g_auto.reversals,
                (unsigned long long)g_auto.probes, (unsigned long long)g_auto.probes_failed,
                (unsigned long long)g_auto.refusals,
                (unsigned long long)(g_auto.refuse_ns[1] / 1000000000ull),
                (unsigned long long)(g_auto.refuse_ns[2] / 1000000000ull),
                (unsigned long long)(g_auto.down_hold_ns / 1000000000ull),
                (unsigned long long)(g_auto.up_hold_ns / 1000000000ull),
                (unsigned long long)g_auto.untimed);
    fprintf(stderr, "\n");
    fs_hist_line("frame wall time per level frame", g_total.wall);
    fs_hist_line("game busy time per level frame", g_total.busy);
    fs_hist_line("game busy time per tick", g_total.tick);
}

static int fs_config(void) {
    if (g_mode != -2) return g_mode;
    char line[320];
    const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
    g_mode = sa2_frameskip_setting(getenv("SA2_PLAY_FRAMESKIP"), replay, line, sizeof line);
    if (line[0]) fprintf(stderr, "%s\n", line);
    if (g_mode != SA2_FRAMESKIP_OFF) {
        uint64_t cpu;
        sa2_frameskip_auto_init(&g_auto, SA2_FRAMESKIP_CAP);
        fs_clocks(&g_t0_ns, &cpu);
        fs_stats_start(&g_period, g_t0_ns);
        fs_stats_start(&g_total, g_t0_ns);
        if (atexit(fs_exit) != 0)
            fprintf(stderr, "frameskip: atexit refused the report's handler; no totals at exit\n");
    }
    return g_mode;
}

/* ------------------------------------------------------------------ the wraps */

/* A guest word that did not read or write: said once, the first time (later ones are counted in
 * no line; each leaves that frame as the game runs it). */
static void fs_unreadable(const char *what) {
    if (!g_unreadable_said++) fprintf(stderr, "frameskip: %s (said once)\n", what);
}

static uint32_t fs_pad_edges(int i) { return FS_PADS_RAW + (uint32_t)i * FS_PAD_SIZE + FS_PAD_EDGES; }

/* The first pass: the edges its Control is about to copy, the ones the frame delivers. */
static void fs_note_edges(void) {
    for (int i = 0; i < FS_PADS; i++) {
        if (sa2_peek(fs_pad_edges(i), 8, g_edges[i]) != SA2_OK) g_edges[i][0] = g_edges[i][1] = 0;
        g_zeroed[i] = 0;
    }
}

/* Passes 2 to N: an edge Control already delivered this frame is zeroed before it copies the
 * pad again; one that differs (written since, in the passes) is left for this pass to deliver,
 * and zeroed on the passes after it. */
static void fs_zero_edges(void) {
    static const uint32_t zero[2] = {0, 0};
    int any = 0;
    for (int i = 0; i < FS_PADS; i++) {
        uint32_t now[2] = {0, 0};
        if (sa2_peek(fs_pad_edges(i), 8, now) != SA2_OK || !(now[0] | now[1])) continue;
        if (now[0] == g_edges[i][0] && now[1] == g_edges[i][1]) {
            if (sa2_poke(fs_pad_edges(i), 8, zero) == SA2_OK) any = g_zeroed[i] = 1;
        } else {
            g_edges[i][0] = now[0];
            g_edges[i][1] = now[1];
            g_zeroed[i] = 0;
            g_period.new_edges++;
            g_total.new_edges++;
        }
    }
    g_period.zero_passes++;
    g_total.zero_passes++;
    if (any) {
        g_period.zero_edges++;
        g_total.zero_edges++;
    }
}

/* FrameCount and the counter at [[0x01A5A230] + 0x14], both moved by `delta`; 0 (and neither
 * moved) when either word does not read or write. */
static int fs_step_counters(int32_t delta) {
    uint32_t obj = 0, fc = 0, oc = 0;
    if (sa2_peek(FS_TICK_OBJECT, 4, &obj) != SA2_OK || !obj ||
        sa2_peek(FS_FRAME_COUNT, 4, &fc) != SA2_OK || sa2_peek(obj + 0x14u, 4, &oc) != SA2_OK)
        return 0;
    fc += (uint32_t)delta;
    oc += (uint32_t)delta;
    return sa2_poke(FS_FRAME_COUNT, 4, &fc) == SA2_OK && sa2_poke(obj + 0x14u, 4, &oc) == SA2_OK;
}

/* The loop returned: each zeroed pad's edges back, where they are still the zero written. */
static void fs_restore_edges(void) {
    for (int i = 0; i < FS_PADS; i++) {
        if (!g_zeroed[i]) continue;
        uint32_t now[2] = {1, 1};
        if (sa2_peek(fs_pad_edges(i), 8, now) == SA2_OK && !(now[0] | now[1])) {
            sa2_poke(fs_pad_edges(i), 8, g_edges[i]);
        } else {
            g_period.kept++;
            g_total.kept++;
        }
    }
}

/* The tail's steps, before the loop runs: the play-time counters and the overlay's count. */
typedef struct {
    int ok;
    uint32_t play[2];
    int16_t overlay;
} fs_tail_words;

static void fs_tail_note(fs_tail_words *t) {
    t->ok = sa2_peek(FS_PLAY_TIME, 8, t->play) == SA2_OK &&
            sa2_peek(FS_OVERLAY_COUNT, 2, &t->overlay) == SA2_OK;
}

/* The loop returned after `ran` ticks: each step its tail took this once-per-call (the counter one
 * up from before the loop, the count one down) is taken ran-1 more times, as the tail would over
 * ran drawn frames, so play time counts the stage's real time and the overlay lasts its 2 s. A step
 * the tail did not take (a frame of the 2P pose loop, which returns before the tail; the overlay
 * outside 2P battle; a counter at its cap; the first counter at 180 emblems) is not taken. */
static void fs_tail_steps(const fs_tail_words *t, int ran) {
    if (!t->ok || ran < 2) return;
    const int64_t extra = ran - 1;
    uint32_t play[2];
    int16_t overlay;
    int stepped = 0;
    if (sa2_peek(FS_PLAY_TIME, 8, play) == SA2_OK) {
        int changed = 0;
        for (int i = 0; i < 2; i++) {
            if (play[i] != t->play[i] + 1u) continue;
            int64_t v = (int64_t)(int32_t)play[i] + extra;
            if (v > FS_PLAY_TIME_CAP) v = FS_PLAY_TIME_CAP;
            changed |= (uint32_t)v != play[i];
            play[i] = (uint32_t)v;
        }
        if (changed && sa2_poke(FS_PLAY_TIME, 8, play) != SA2_OK)
            fs_unreadable("the play-time counters could not be written; they count drawn frames");
        stepped |= changed;
    }
    if (t->overlay > 0 && sa2_peek(FS_OVERLAY_COUNT, 2, &overlay) == SA2_OK &&
        overlay == (int16_t)(t->overlay - 1) && overlay > 0) {
        const int16_t v = overlay > extra ? (int16_t)(overlay - extra) : 0;
        if (sa2_poke(FS_OVERLAY_COUNT, 2, &v) != SA2_OK)
            fs_unreadable("the overlay's count at 0x01A55938 could not be written");
        stepped = 1;
    }
    if (stepped) {
        g_period.tail++;
        g_total.tail++;
    }
}

/* The stage timer in ticks (60 a second), or -1 when it does not read. */
static int64_t fs_stage_timer(void) {
    uint8_t m[3];
    if (sa2_peek(FS_STAGE_TIMER, 3, m) != SA2_OK) return -1;
    return ((int64_t)m[0] * 60 + m[1]) * 60 + m[2];
}

/* A level-loop entry: the previous level frame's sample, Auto's N for this one, the report. */
static void fs_frame_enter(void) {
    uint64_t wall, cpu;
    fs_clocks(&wall, &cpu);
    cpu += fs_run_wait();                /* busy: on a CPU, or ready and waiting for one */
    const uint64_t frames = sa2_sched_frames();
    const int64_t timer = fs_stage_timer();
    uint16_t lv = 0;
    const int level = sa2_peek(FS_CURRENT_LEVEL, 2, &lv) == SA2_OK ? (int)lv : -1;
    if (g_have_prev) {
        if (frames == g_prev_frames + 1u && g_prev_ok) {
            const uint64_t w = wall - g_prev_wall, b = cpu - g_prev_cpu;
            fs_count_sample(&g_period, g_prev_n, w, b);
            fs_count_sample(&g_total, g_prev_n, w, b);
            /* The stage's own clock over the frame, while it runs (not paused, not stopped at the
             * goal, not reset): the speed the fix is for, stage time over wall time. */
            if (timer >= 0 && g_prev_timer_ok && timer > (int64_t)g_prev_timer &&
                timer - (int64_t)g_prev_timer <= 3 * SA2_FRAMESKIP_CAP) {
                const uint64_t d = (uint64_t)(timer - (int64_t)g_prev_timer);
                g_period.stage_ticks += d;
                g_total.stage_ticks += d;
                g_period.stage_wall += w;
                g_total.stage_wall += w;
            }
            if (g_mode == SA2_FRAMESKIP_AUTO) {
                if (g_prev_n > 1 && g_prev_n == g_auto.n && !g_prev_undrawn && !g_untimed_said++)
                    fprintf(stderr, "frameskip: a level frame at N=%d ran without its last pass's "
                            "Control at counter %d, so its undrawn ticks were not timed; its window "
                            "is judged on the whole frame's busy time (said once; counted at exit)\n",
                            g_prev_n, g_prev_n - 1);
                sa2_frameskip_auto_sample(&g_auto, g_prev_n, w, b, g_prev_undrawn, wall, fs_tick_ns());
            }
        } else if (g_mode == SA2_FRAMESKIP_AUTO) {
            sa2_frameskip_auto_gap(&g_auto, level < 0 || level != g_level, wall);   /* frames outside
                                                                         the loop, or one in a pass */
        }
        if (g_mode == SA2_FRAMESKIP_AUTO && g_auto.held_seq != g_held_seen) {
            char line[256];
            g_held_seen = g_auto.held_seq;
            sa2_frameskip_auto_held(&g_auto, g_auto.held_at - g_t0_ns, line, sizeof line);
            fprintf(stderr, "%s\n", line);
        }
        if (g_mode == SA2_FRAMESKIP_AUTO && g_auto.why_seq != g_why_seen) {
            char line[768];
            g_why_seen = g_auto.why_seq;
            sa2_frameskip_auto_why(&g_auto, wall - g_t0_ns, line, sizeof line);
            fprintf(stderr, "%s\n", line);
        }
    }
    g_level = level;
    g_prev_wall = wall;
    g_prev_cpu = cpu;
    g_prev_frames = frames;
    g_prev_timer_ok = timer >= 0;
    g_prev_timer = timer >= 0 ? (uint32_t)timer : 0;
    g_have_prev = 1;
    g_target = g_mode == SA2_FRAMESKIP_AUTO ? g_auto.n : g_mode;
    if (wall - g_period.start_ns >= FS_REPORT_NS) {
        fs_line("last", &g_period, wall);
        fs_stats_start(&g_period, wall);
    }
}

__attribute__((used, retain))
void *__wrap_sub_43ce20(void *state, uint32_t pc, void *memory) {
    if (fs_config() == SA2_FRAMESKIP_OFF || g_in_loop) return __real_sub_43ce20(state, pc, memory);
    fs_frame_enter();
    g_in_loop = 1;
    g_decided = g_catchup = g_steps = g_step_failed = 0;
    g_frame_n = 1;
    g_undrawn = 0;
    memset(g_zeroed, 0, sizeof g_zeroed);
    fs_tail_words tail;
    fs_tail_note(&tail);
    const uint64_t vus = sa2_virtual_us();
    void *ret = __real_sub_43ce20(state, pc, memory);
    g_in_loop = 0;
    if (g_frame_n > 1) fs_restore_edges();
    if (sa2_virtual_us() != vus) {
        g_period.clock_inside++;
        g_total.clock_inside++;
    }
    uint32_t passes = 0;
    if (sa2_peek(FS_PASS_COUNTER, 4, &passes) != SA2_OK || passes < 1 || passes > 16)
        passes = (uint32_t)g_frame_n;
    const int ran = g_frame_n > 1 ? (int)passes : 1;    /* the ticks this frame ran, if written */
    if (g_frame_n > 1) {
        /* GameLoop adds FrameIncrement next: 1, after the N-1 steps the later passes made. When
         * the steps did not all happen, or the passes changed FrameIncrement, the steps are taken
         * back and GameLoop adds what FrameIncrement holds, as the game's own catch-up does. */
        uint32_t fi = 0;
        const uint32_t one = 1;
        if (!g_step_failed && g_steps == g_frame_n - 1 &&
            sa2_peek(FS_FRAME_INCREMENT, 4, &fi) == SA2_OK && fi == (uint32_t)g_frame_n &&
            sa2_poke(FS_FRAME_INCREMENT, 4, &one) == SA2_OK) {
            /* the frame's counts finish at N */
        } else {
            if (g_steps && !fs_step_counters(-g_steps))
                fs_unreadable("FrameCount's steps could not be taken back");
            g_period.fi_kept++;
            g_total.fi_kept++;
        }
        sa2_sched_owe_frames((uint64_t)ran);
        fs_tail_steps(&tail, ran);
    }
    fs_count_frame(&g_period, ran, g_decided);
    fs_count_frame(&g_total, ran, g_decided);
    g_prev_n = ran;
    g_prev_ok = g_decided && !g_catchup;    /* a sample is a frame that ran the N it says */
    g_prev_undrawn = ran == g_frame_n ? g_undrawn : 0;
    return ret;
}

__attribute__((used, retain))
void *__wrap_sub_441ba0(void *state, uint32_t pc, void *memory) {
    if (g_mode == -2) fs_config();
    if (!g_in_loop) return __real_sub_441ba0(state, pc, memory);
    uint32_t counter = 0, fi = 0;
    if (sa2_peek(FS_PASS_COUNTER, 4, &counter) != SA2_OK || sa2_peek(FS_FRAME_INCREMENT, 4, &fi) != SA2_OK) {
        fs_unreadable("FrameIncrement or the pass counter did not read; the frame keeps the game's "
                      "own pass count");
        return __real_sub_441ba0(state, pc, memory);
    }
    if (counter == 0) {
        if (!g_decided) {
            g_decided = 1;
            if (fi == 1u && g_target > 1) {
                const uint32_t n = (uint32_t)g_target;
                if (sa2_poke(FS_FRAME_INCREMENT, 4, &n) == SA2_OK) {
                    g_frame_n = g_target;
                    fs_note_edges();
                    if (g_mode == SA2_FRAMESKIP_AUTO) g_pass_busy0 = fs_busy_clock();
                } else {
                    fs_unreadable("FrameIncrement could not be written; the frame runs one tick");
                }
            } else if (fi > 1u) {
                g_catchup = 1;
                g_period.catchup++;
                g_total.catchup++;
            }
        }
    } else if (g_frame_n > 1) {
        if (g_mode == SA2_FRAMESKIP_AUTO && counter == (uint32_t)(g_frame_n - 1) && !g_undrawn) {
            /* the frame's first N-1 passes ran from the first pass's Control to here: its undrawn
             * ticks, each a whole tick (Control, the objects, the collisions, the stage timer) */
            const uint64_t b = fs_busy_clock();
            g_undrawn = b > g_pass_busy0 ? b - g_pass_busy0 : 1;
        }
        fs_zero_edges();
        if (g_step_failed) {
            /* this frame's counters are GameLoop's */
        } else if (fs_step_counters(1)) {
            g_steps++;
        } else {
            g_step_failed = 1;
            fs_unreadable("FrameCount or the counter at [0x01A5A230] + 0x14 did not read; this "
                          "frame's counts advance at GameLoop, N at once");
        }
    }
    return __real_sub_441ba0(state, pc, memory);
}
