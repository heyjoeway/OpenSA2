"""runtime/sa2_frameskip.c: play's low-FPS speed fix (SA2_PLAY_FRAMESKIP=2|3|auto), N game ticks
per drawn stage frame, around the level loop (sub_43ce20) and Control (sub_441ba0).

The unit is built with the pinned zig, as tools/s4/link_s4.py compiles a runtime source (-O2,
x86_64-linux-musl), and linked with a driver that stands in for GameLoop. Each frame the driver
writes the frame's press and release edges into ControllersRaw (UpdateControllers), crosses a
frame boundary (BeginScene: the clock moves the periods the scheduler is owed, as
runtime/sa2_threads.c does), calls the level loop through its wrap, calls Control once more
outside it (FUN_0040a8f0), and adds FrameIncrement to FrameCount and to the counter at
[[0x01A5A230] + 0x14] (0x00433FE2). Its level loop sets FrameIncrement to 1 and the pass counter to
0 and runs `while (counter < FrameIncrement)`, each pass calling Control through its wrap; its
Control copies ControllersRaw into Controllers and records, per pass, which edges it saw and the
two counters. A case reads what each pass saw, the clock, and the counts, against literal values.

The scheduler's half is runtime/sa2_threads.c itself, linked with the poll-handoff probe's
stand-ins: an owed boundary moves the clock N periods and counts one frame.

Auto is a pure function (sa2_frameskip_auto_sample) driven here by a model device: each frame the
game is busy n*L + G, the player needs R, paced frames come no faster than n frame periods, and up
to Q frames can wait between the game and the player before the game waits on it.

Busy time's run-queue wait is read from /proc/thread-self/schedstat; a second build points that read
at a file the driver rewrites between frames, for the paths where it does not open or stops reading.

The rest are source checks: the wraps' place in PRODUCTION_WRAPS and every reader of that list,
the variable stripped from every registered run's environment and refused as a run's extra
setting, and the only places that may set it.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
import link_s4 as LS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_PLAY_FRAMESKIP", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
TICK_US = 16666

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_frameskip.h"

#define LOOP_VA 0x01DEB50Cu    /* FrameIncrement, then the pass counter */
#define FC_VA   0x0174B038u    /* FrameCount */
#define PTR_VA  0x01A5A230u    /* the pointer GameLoop adds FrameIncrement through, at +0x14 */
#define OBJ_VA  0x019457F8u    /* what main_gc_alloc puts there */
#define PADS_VA 0x01A52918u    /* ControllersRaw[4] */
#define TIMER_VA 0x0174AFDBu   /* TimerMinutes, TimerSeconds, TimerFrames */
#define PLAY_VA  0x01DEC628u   /* the tail's two play-time counters */
#define OVL_VA   0x01A55938u   /* the tail's overlay count */
static uint32_t loopw[2], framecount, objptr = OBJ_VA, obj[8], play[2];
static int16_t overlay;
static int emblems, overlay_on;
static uint8_t stage_timer[3];
static uint8_t pads[4 * 0x34];
static unsigned long long peeks, pokes;
static int fail_fc;
#ifdef SA2_FS_SCHEDSTAT
/* "sched=F:TEXT" (TEXT's '_' a space; "-" removes the file): the schedstat file before frame F */
static void sched_file(const char *spec, int f) {
    char *end;
    if (strtol(spec, &end, 10) != f || *end != ':') return;
    if (!strcmp(end + 1, "-")) { remove(SA2_FS_SCHEDSTAT); return; }
    FILE *o = fopen(SA2_FS_SCHEDSTAT, "w");
    for (const char *c = end + 1; *c; c++) fputc(*c == '_' ? ' ' : *c, o);
    fclose(o);
}
#endif

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= LOOP_VA && a + n <= LOOP_VA + sizeof loopw) return (uint8_t *)loopw + (a - LOOP_VA);
    if (!fail_fc && a == FC_VA && n == 4) return (uint8_t *)&framecount;
    if (a == PTR_VA && n == 4) return (uint8_t *)&objptr;
    if (a >= OBJ_VA && a + n <= OBJ_VA + sizeof obj) return (uint8_t *)obj + (a - OBJ_VA);
    if (a >= PADS_VA && a + n <= PADS_VA + sizeof pads) return pads + (a - PADS_VA);
    if (a >= TIMER_VA && a + n <= TIMER_VA + sizeof stage_timer) return stage_timer + (a - TIMER_VA);
    if (a >= PLAY_VA && a + n <= PLAY_VA + sizeof play) return (uint8_t *)play + (a - PLAY_VA);
    if (a == OVL_VA && n == 2) return (uint8_t *)&overlay;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    peeks++;
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    pokes++;
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}

/* The scheduler's clock, as runtime/sa2_threads.c moves it at a frame boundary. */
#define TICK_US 16666u
static uint64_t vclock, boundaries, owed = 1;
uint64_t sa2_virtual_us(void) { return vclock; }
uint64_t sa2_sched_frames(void) { return boundaries; }
uint64_t sa2_sched_frame_us(void) { return TICK_US; }
void sa2_sched_owe_frames(uint64_t n) { owed = n; }
static void boundary(void) { vclock = (vclock / TICK_US + owed) * TICK_US; owed = 1; boundaries++; }

static uint32_t rd(const uint8_t *b, int i, int off) { uint32_t v; memcpy(&v, b + i * 0x34 + off, 4); return v; }
static void wr(uint8_t *b, int i, int off, uint32_t v) { memcpy(b + i * 0x34 + off, &v, 4); }

/* The game. */
static uint8_t ctl[4 * 0x34];                 /* Controllers */
static int fno, in_level, pal_every, inner_at = -1, rewrite_at = -1, fi_change_at = -1;
static char seen[64], counts[512];
static uint32_t ticks;

void *__real_sub_441ba0(void *s, uint32_t pc, void *m) {      /* Control */
    (void)s; (void)pc;
    memcpy(ctl, pads, sizeof ctl);
    if (in_level && strlen(seen) < 60) {
        const uint32_t p = rd(ctl, 0, 0x10), r = rd(ctl, 0, 0x14), p2 = rd(ctl, 2, 0x10);
        char c[2] = {p && r ? 'B' : p ? 'P' : r ? 'R' : '.', 0};
        if (p2 && !p) c[0] = '?';
        strcat(seen, c);
        char t[32];
        snprintf(t, sizeof t, "%s%u/%u", counts[0] ? "," : "", framecount, obj[5]);
        if (strlen(counts) + strlen(t) < sizeof counts) strcat(counts, t);
    }
    return m;
}
void *__wrap_sub_441ba0(void *s, uint32_t pc, void *m);

void *__real_sub_43ce20(void *s, uint32_t pc, void *m) {      /* the level loop */
    loopw[0] = 1;
    loopw[1] = 0;
    if (pal_every && fno % pal_every == pal_every - 1) loopw[0] = 2;   /* the game's catch-up */
    in_level = 1;
    while (loopw[1] < loopw[0]) {
        __wrap_sub_441ba0(s, pc, m);
        if (fno == inner_at && loopw[1] == 0) boundary();             /* a frame drawn in a pass */
        if (fno == rewrite_at && loopw[1] == 1) wr(pads, 1, 0x10, 0x40);  /* a new edge in a pass */
        if (fno == fi_change_at && loopw[1] == 0) loopw[0] = 1;        /* a module load's write */
        if (++stage_timer[2] == 60) { stage_timer[2] = 0; stage_timer[1]++; }   /* StageTimerUpdate */
        ticks++;
        loopw[1]++;
    }
    in_level = 0;
    /* the tail, once per call (0x0043D139 to 0x0043D228) */
    if (overlay_on && overlay > 0) overlay--;
    if (emblems < 180 && (int32_t)play[0] < 0x7FFFFF00) play[0]++;
    if ((int32_t)play[1] < 0x7FFFFF00) play[1]++;
    return m;
}
void *__wrap_sub_43ce20(void *s, uint32_t pc, void *m);

static int listed(const char *list, int f) {
    for (const char *p = list; *p;) {
        char *end;
        const long v = strtol(p, &end, 10);
        if (end == p) break;
        if (*end == '-') {
            const long hi = strtol(end + 1, &end, 10);
            if (f >= v && f <= hi) return 1;
        } else if (v == f) {
            return 1;
        }
        p = *end == ',' ? end + 1 : end;
    }
    return 0;
}

/* Auto on a model device: phases "ms:R:L:G" (from ms on, the player needs R us a frame and the
 * game is busy n*L + G us), "hitch:ms" (one frame of 600 ms there), "stage:ms" (5 s outside the
 * level loop there, into another stage), "pause:ms" (5 s in the pause menu, the same stage) and
 * "queue:Q" (Q frames fit between the game and the player: the relay and the pipes; the game
 * waits on the player only once they are full), then the run's length in ms. With no queue a
 * frame takes max(n frame periods, R, busy). */
static int auto_main(int argc, char **argv) {
    long phase_at[16], R[16], L[16], G[16], hitch = -1, length = 0, gap_at[16], queue = 0;
    int phases = 0, gaps = 0, gap_next = 0, gap_stage[16];
    uint64_t backlog = 0;                         /* the player's work queued ahead of it, us */
    for (int i = 0; i < argc; i++) {
        if (!strncmp(argv[i], "hitch:", 6)) hitch = atol(argv[i] + 6);
        else if (!strncmp(argv[i], "queue:", 6)) queue = atol(argv[i] + 6);
        else if (!strncmp(argv[i], "stage:", 6) && gaps < 16) { gap_stage[gaps] = 1; gap_at[gaps++] = atol(argv[i] + 6); }
        else if (!strncmp(argv[i], "pause:", 6) && gaps < 16) { gap_stage[gaps] = 0; gap_at[gaps++] = atol(argv[i] + 6); }
        else if (strchr(argv[i], ':') && phases < 16) {
            sscanf(argv[i], "%ld:%ld:%ld:%ld", &phase_at[phases], &R[phases], &L[phases], &G[phases]);
            phases++;
        } else length = atol(argv[i]);
    }
    sa2_fs_auto a;
    sa2_frameskip_auto_init(&a, SA2_FRAMESKIP_CAP);
    const uint64_t tick = (uint64_t)TICK_US * 1000u;
    uint64_t now = 0, at_n[4] = {0, 0, 0, 0};
    int n = a.n, hitched = 0;
    while (now < (uint64_t)length * 1000000u) {
        int k = 0;
        for (int i = 0; i < phases; i++) if ((uint64_t)phase_at[i] * 1000000u <= now) k = i;
        const uint64_t busy = (uint64_t)n * (uint64_t)L[k] * 1000u + (uint64_t)G[k] * 1000u;
        uint64_t wall = (uint64_t)n * tick;
        if (busy > wall) wall = busy;
        {   /* the player takes R a frame; the game blocks once Q frames wait for it */
            const uint64_t r = (uint64_t)R[k] * 1000u, room = (uint64_t)queue * r;
            const uint64_t ahead = backlog + r > wall ? backlog + r - wall : 0;
            if (ahead > room) wall += ahead - room;
            backlog = ahead > room ? room : ahead;
        }
        if (!hitched && hitch >= 0 && now >= (uint64_t)hitch * 1000000u) { wall = 600000000u; hitched = 1; }
        if (gap_next < gaps && now >= (uint64_t)gap_at[gap_next] * 1000000u) {
            now += 5000000000u;
            sa2_frameskip_auto_gap(&a, gap_stage[gap_next]);
            printf("%llu ms: %s\n", (unsigned long long)(now / 1000000u), gap_stage[gap_next] ? "stage" : "pause");
            gap_next++;
        }
        now += wall;
        at_n[n] += wall;
        const int next = sa2_frameskip_auto_sample(&a, n, wall, busy, now, tick);
        if (next != n) printf("%llu ms: N %d -> %d\n", (unsigned long long)(now / 1000000u), n, next);
        n = next;
    }
    printf("end N=%d ups=%llu downs=%llu guards=%llu reversals=%llu refusals=%llu at1=%llu at2=%llu "
           "at3=%llu down_hold=%llu up_hold=%llu refuse=%llu refuse2=%llu\n", n, (unsigned long long)a.ups,
           (unsigned long long)a.downs, (unsigned long long)a.guards,
           (unsigned long long)a.reversals, (unsigned long long)a.refusals,
           (unsigned long long)(at_n[1] / 1000000u),
           (unsigned long long)(at_n[2] / 1000000u), (unsigned long long)(at_n[3] / 1000000u),
           (unsigned long long)(a.down_hold_ns / 1000000000u),
           (unsigned long long)(a.up_hold_ns / 1000000000u),
           (unsigned long long)(a.refuse_ns[1] / 1000000000u),
           (unsigned long long)(a.refuse_ns[2] / 1000000000u));
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "auto")) return auto_main(argc - 2, argv + 2);
    if (argc >= 3 && !strcmp(argv[1], "setting")) {          /* setting VALUE [replay] */
        char line[400];
        const int m = sa2_frameskip_setting(argv[2], argc > 3, line, sizeof line);
        printf("%d|%s\n", m, line);
        return 0;
    }
    const int frames = atoi(argv[1]);
    const char *press = "", *menu = "";
    for (int i = 2; i < argc; i++) {
        if (!strncmp(argv[i], "press=", 6)) press = argv[i] + 6;
        else if (!strncmp(argv[i], "menu=", 5)) menu = argv[i] + 5;
        else if (!strncmp(argv[i], "pal=", 4)) pal_every = atoi(argv[i] + 4);
        else if (!strncmp(argv[i], "inner=", 6)) inner_at = atoi(argv[i] + 6);
        else if (!strncmp(argv[i], "rewrite=", 8)) rewrite_at = atoi(argv[i] + 8);
        else if (!strncmp(argv[i], "fichange=", 9)) fi_change_at = atoi(argv[i] + 9);
        else if (!strcmp(argv[i], "failfc")) fail_fc = 1;
        else if (!strncmp(argv[i], "play=", 5)) play[0] = play[1] = (uint32_t)strtoul(argv[i] + 5, NULL, 0);
        else if (!strncmp(argv[i], "emblems=", 8)) emblems = atoi(argv[i] + 8);
        else if (!strncmp(argv[i], "overlay=", 8)) { overlay = (int16_t)atoi(argv[i] + 8); overlay_on = 1; }
    }
    int token;
    for (fno = 0; fno < frames; fno++) {
#ifdef SA2_FS_SCHEDSTAT
        for (int i = 2; i < argc; i++)
            if (!strncmp(argv[i], "sched=", 6)) sched_file(argv[i] + 6, fno);
#endif
        const uint32_t p = listed(press, fno) ? 0x100u : 0u, r = listed(press, fno - 1) ? 0x100u : 0u;
        wr(pads, 0, 0x10, p);                    /* UpdateControllers: pads 0 and 2 press */
        wr(pads, 0, 0x14, r);
        wr(pads, 2, 0x10, p);
        wr(pads, 1, 0x10, 0);
        boundary();                              /* BeginScene */
        seen[0] = counts[0] = 0;
        const uint32_t t0 = ticks;
        if (listed(menu, fno)) __wrap_sub_441ba0(&token, 0, &token);   /* a menu's Control */
        else __wrap_sub_43ce20(&token, 0, &token);
        __wrap_sub_441ba0(&token, 0, &token);   /* FUN_0040a8f0's Control, after the loop */
        framecount += loopw[0];                  /* GameLoop's two adds */
        obj[5] += loopw[0];
        printf("%d ticks=%u seen=%s counts=%s after=%x/%x add=%u clock=%llu play=%u/%u overlay=%d\n",
               fno, ticks - t0, seen[0] ? seen : "-", counts[0] ? counts : "-", rd(ctl, 0, 0x10),
               rd(ctl, 1, 0x10), loopw[0], (unsigned long long)vclock, play[0], play[1], overlay);
    }
    boundary();
    printf("end boundaries=%llu clock=%llu framecount=%u counter=%u ticks=%u peeks=%llu pokes=%llu\n",
           (unsigned long long)boundaries, (unsigned long long)vclock, framecount, obj[5], ticks,
           peeks, pokes);
    return 0;
}
"""

# runtime/sa2_threads.c with harness/poll_handoff_probe.c's stand-ins: the frame boundary's clock.
THREADS_DRIVER = r"""
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

int sa2_peek(uint32_t a, uint32_t n, void *out) { (void)a; memset(out, 0, n); return SA2_OK; }
int sa2_poke(uint32_t a, uint32_t n, const void *in) { (void)a; (void)n; (void)in; return SA2_OK; }
int sa2_guest_fill(uint32_t a, int b, uint32_t n) { (void)a; (void)b; (void)n; return SA2_OK; }
uint32_t sa2_guest_teb(unsigned index) { return 0x00180000u + index * 0x100u; }
int sa2_guest_teb_init_stack(unsigned i, uint32_t lo, uint32_t hi) { (void)i; (void)lo; (void)hi; return SA2_OK; }
int sa2_guest_tstack(unsigned slot, uint32_t *low, uint32_t *top) {
    *low = 0x00200000u + slot * 0x10000u;
    *top = *low + 0x10000u;
    return SA2_OK;
}
void sa2_cpu_state_reset(sa2_cpu_state_t *s) { memset(s, 0, sizeof *s); }
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fprintf(stderr, "\n");
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) { fprintf(stderr, "trap %u %#x\n", status, exit_pc); _exit(3); }
void sa2_trap_thread_init(void) {}
void sa2_trap_thread_fini(void) {}
void sa2_call_thread_fini(void) {}
static uint64_t now_us;
uint64_t sa2_virtual_us(void) { return now_us; }
void sa2_virtual_advance_to_us(uint64_t us) { if (us > now_us) now_us = us; }
int sa2_event_signal(uint32_t h, int pulse) { (void)h; (void)pulse; return 0; }
uint32_t sa2_fls_callback(uint32_t i) { (void)i; return 0; }
uint32_t sa2_import_calls(void) { return 0; }
uint32_t sa2_rt_get_gpr(const void *s, int i) { (void)s; (void)i; return 0; }
int sa2_call(uint32_t va, sa2_cpu_state_t *s, uint32_t t, sa2_call_result_t *r) {
    (void)va; (void)s; (void)t; (void)r; return 0;
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        const int n = atoi(argv[i]);
        if (n) sa2_sched_owe_frames((uint64_t)n);
        sa2_sched_frame_boundary();
        printf("%llu %llu\n", (unsigned long long)sa2_sched_frames(), (unsigned long long)sa2_virtual_us());
    }
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("frameskip")
    (d / "driver.c").write_text(DRIVER)
    out = d / "fs"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_frameskip.c"),
                           "-o", str(out)], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


@pytest.fixture(scope="module")
def sched_exe(tmp_path_factory) -> tuple[Path, Path]:
    """The unit with its schedstat read pointed at a file the driver writes (sched=F:TEXT)."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("frameskip-sched")
    (d / "driver.c").write_text(DRIVER)
    out, stat = d / "fs", d / "schedstat"
    done = subprocess.run([str(ZIG), "cc", *CC, f'-DSA2_FS_SCHEDSTAT="{stat}"', str(d / "driver.c"),
                           str(RUNTIME / "sa2_frameskip.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out, stat


@pytest.fixture(scope="module")
def threads_exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("frameskip-threads")
    (d / "driver.c").write_text(THREADS_DRIVER)
    out = d / "th"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_threads.c"),
                           "-o", str(out)], capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *args: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS and k != "SA2_FRAME_US"}
    p = subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=60,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


def frames(lines: list[str]) -> list[dict]:
    out = []
    for line in lines[:-1]:
        f, *kv = line.split()
        out.append({"frame": int(f), **dict(x.split("=", 1) for x in kv)})
    return out


def end(lines: list[str]) -> dict:
    assert lines[-1].startswith("end ")
    return {k: int(v) for k, v in (x.split("=") for x in lines[-1].split()[1:])}


# ---------------------------------------------------------------- off, and the setting


def test_off_runs_one_tick_a_frame_and_touches_no_guest_word(exe):
    for env in ({}, {"SA2_PLAY_FRAMESKIP": ""}):
        lines, err = run(exe, 4, "press=1", **env)
        assert err == ""
        assert [f["seen"] for f in frames(lines)] == [".", "P", "R", "."]
        assert [f["ticks"] for f in frames(lines)] == ["1"] * 4
        assert end(lines) == {"boundaries": 5, "clock": 5 * TICK_US, "framecount": 4, "counter": 4,
                              "ticks": 4, "peeks": 0, "pokes": 0}


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    lines, err = run(exe, 3, "press=1", SA2_PLAY_FRAMESKIP="3", **{replay: "/dev/null"})
    assert err == ("frameskip: SA2_PLAY_FRAMESKIP=3 ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
    assert end(lines) == {"boundaries": 4, "clock": 4 * TICK_US, "framecount": 3, "counter": 3,
                          "ticks": 3, "peeks": 0, "pokes": 0}


@pytest.mark.parametrize("value", ["1", "4", "0", "two", " 2", "2 ", "22", "-2", "auto3", "on", "yes"])
def test_a_malformed_value_leaves_it_off_with_one_line(exe, value):
    lines, err = run(exe, 2, SA2_PLAY_FRAMESKIP=value)
    assert err == f"frameskip: SA2_PLAY_FRAMESKIP={value} is not 2, 3, auto or off; the mode stays off\n"
    assert end(lines)["peeks"] == 0 and end(lines)["ticks"] == 2


@pytest.mark.parametrize("value", ["off", "OFF", "Off"])
def test_off_said_as_off(exe, value):
    lines, err = run(exe, 2, SA2_PLAY_FRAMESKIP=value)
    assert err == f"frameskip: off (SA2_PLAY_FRAMESKIP={value})\n"
    assert end(lines)["pokes"] == 0


def test_the_settings_parse(exe):
    def setting(*a):
        return run(exe, "setting", *a)[0][0].split("|", 1)
    assert setting("2") == ["2", "frameskip: on (SA2_PLAY_FRAMESKIP=2): stages run 2 game ticks per "
                            "drawn frame and the clock moves 2 frames per drawn frame there; menus, "
                            "cutscenes and movies stay at 1"]
    assert setting("3")[0] == "3"
    assert setting("auto")[0] == setting("AUTO")[0] == "-1"
    assert setting("auto")[1].startswith("frameskip: on (SA2_PLAY_FRAMESKIP=auto): stages run 1 to 3 ")
    assert setting("2", "replay")[0] == "0"


# ---------------------------------------------------------------- fixed N


def test_two_ticks_a_frame_one_press_and_the_clock_two_frames(exe):
    lines, err = run(exe, 4, "press=1", SA2_PLAY_FRAMESKIP="2")
    assert err.splitlines()[0].startswith("frameskip: on (SA2_PLAY_FRAMESKIP=2)")
    got = frames(lines)
    # The press reaches the first pass alone, the release (the next frame) likewise.
    assert [f["seen"] for f in got] == ["..", "P.", "R.", ".."]
    assert [f["ticks"] for f in got] == ["2"] * 4
    # Each pass reads the counts tick k reads at one tick a frame, and GameLoop adds 1.
    assert [f["counts"] for f in got] == ["0/0,1/1", "2/2,3/3", "4/4,5/5", "6/6,7/7"]
    assert [f["add"] for f in got] == ["1"] * 4
    # The edges are back for FUN_0040a8f0's Control after the loop.
    assert got[1]["after"] == "100/0"
    # BeginScene k+1 moves the clock the 2 periods frame k ran.
    assert [int(f["clock"]) for f in got] == [TICK_US * c for c in (1, 3, 5, 7)]
    assert {k: end(lines)[k] for k in ("boundaries", "clock", "framecount", "counter", "ticks")} == {
        "boundaries": 5, "clock": 9 * TICK_US, "framecount": 8, "counter": 8, "ticks": 8}


def test_three_ticks_a_frame(exe):
    lines, _ = run(exe, 3, "press=0", SA2_PLAY_FRAMESKIP="3")
    got = frames(lines)
    assert [f["seen"] for f in got] == ["P..", "R..", "..."]
    assert [f["counts"] for f in got] == ["0/0,1/1,2/2", "3/3,4/4,5/5", "6/6,7/7,8/8"]
    assert end(lines)["clock"] == (1 + 3 * 3) * TICK_US and end(lines)["framecount"] == 9


def test_every_pad_s_edges_go_and_come_back(exe):
    # Pad 2's press too: '?' would be a pass that saw pad 2's press without pad 0's.
    lines, _ = run(exe, 2, "press=0,1", SA2_PLAY_FRAMESKIP="3")
    assert [f["seen"] for f in frames(lines)] == ["P..", "B.."]
    assert frames(lines)[1]["after"] == "100/0"


def test_frames_outside_the_level_loop_stay_at_one(exe):
    lines, _ = run(exe, 5, "menu=2-3", SA2_PLAY_FRAMESKIP="2")
    got = frames(lines)
    assert [f["ticks"] for f in got] == ["2", "2", "0", "0", "2"]
    # FrameIncrement went back to 1, so the menu frames add 1, not 2.
    assert [f["add"] for f in got] == ["1"] * 5
    assert [int(f["clock"]) for f in got] == [TICK_US * c for c in (1, 3, 5, 6, 7)]
    assert end(lines)["framecount"] == 2 + 2 + 1 + 1 + 2


def test_the_game_s_own_catch_up_is_left_as_it_is(exe):
    # Every 3rd frame the loop sets FrameIncrement 2 itself: not written, no edge rule, the
    # clock one frame, as at one tick a frame.
    lines, err = run(exe, 3, "press=2", "pal=3", SA2_PLAY_FRAMESKIP="3")
    got = frames(lines)
    assert [f["ticks"] for f in got] == ["3", "3", "2"]
    assert got[2]["seen"] == "PP" and got[2]["counts"] == "6/6,6/6" and got[2]["add"] == "2"
    assert end(lines)["clock"] == (1 + 3 + 3 + 1) * TICK_US
    assert "2 frames" not in err


def test_a_frame_drawn_inside_a_pass_moves_the_clock_one_and_the_n_waits(exe):
    lines, _ = run(exe, 3, "inner=1", SA2_PLAY_FRAMESKIP="2")
    got = frames(lines)
    # frame 1's inner boundary: +1 inside the pass; frame 1's 2 at frame 2's BeginScene.
    assert [int(f["clock"]) for f in got] == [TICK_US * c for c in (1, 4, 6)]
    assert end(lines)["boundaries"] == 5 and end(lines)["clock"] == 8 * TICK_US


def test_an_edge_written_inside_the_passes_is_delivered_once_and_kept(exe):
    # Pad 1's press written during pass 2 of frame 1: pass 3 copies it, and it is still in
    # ControllersRaw for the Control after the loop.
    lines, err = run(exe, 3, "rewrite=1", SA2_PLAY_FRAMESKIP="3")
    assert frames(lines)[1]["after"] == "0/40"
    assert "1 edges the passes wrote (each delivered once)" in err


def test_a_frame_whose_passes_reset_frame_increment_owes_what_it_ran(exe):
    # A module load in pass 1 writes FrameIncrement 1: the loop ends after one pass; the frame's
    # counter step is taken back and GameLoop adds what FrameIncrement holds.
    lines, err = run(exe, 3, "fichange=1", SA2_PLAY_FRAMESKIP="2")
    got = frames(lines)
    assert [f["ticks"] for f in got] == ["2", "1", "2"]
    assert got[1]["counts"] == "2/2" and got[1]["add"] == "1"
    assert [int(f["clock"]) for f in got] == [TICK_US * c for c in (1, 3, 4)]
    assert end(lines)["framecount"] == 5 and "1 whose counts GameLoop advanced N at once" in err


def test_an_unreadable_frame_count_says_so_once_and_gameloop_adds_n(exe):
    lines, err = run(exe, 3, "failfc", SA2_PLAY_FRAMESKIP="2")
    got = frames(lines)
    assert [f["add"] for f in got] == ["2"] * 3
    assert err.count("did not read") == 1
    assert "FrameCount or the counter at [0x01A5A230] + 0x14 did not read" in err


def test_the_tail_s_play_time_and_overlay_steps_keep_real_time(exe):
    # The tail runs once per drawn frame: at N=2 its steps are taken twice, as 8 ticks would.
    lines, err = run(exe, 4, "overlay=5", SA2_PLAY_FRAMESKIP="2")
    got = frames(lines)
    assert [f["play"] for f in got] == ["2/2", "4/4", "6/6", "8/8"]
    # 5 frames of overlay: 3 drawn frames (6 ticks, the last cut at 0), not 5.
    assert [f["overlay"] for f in got] == ["3", "1", "0", "0"]
    assert "4 whose tail's play-time and overlay steps were taken N-1 more times" in err
    lines, _ = run(exe, 4, "overlay=5", SA2_PLAY_FRAMESKIP="3")
    assert [f["play"] for f in frames(lines)][-1] == "12/12"
    assert [f["overlay"] for f in frames(lines)] == ["2", "0", "0", "0"]


def test_a_tail_step_the_game_did_not_take_is_not_taken(exe):
    # Past 180 the first counter stops; both stop at 0x7FFFFF00; off, they count drawn frames.
    lines, _ = run(exe, 3, "emblems=180", "play=0x7FFFFEFE", SA2_PLAY_FRAMESKIP="3")
    assert [f["play"] for f in frames(lines)] == [
        f"{0x7FFFFEFE}/{0x7FFFFF00}", f"{0x7FFFFEFE}/{0x7FFFFF00}", f"{0x7FFFFEFE}/{0x7FFFFF00}"]
    lines, _ = run(exe, 3, "overlay=5")
    assert [f["play"] for f in frames(lines)] == ["1/1", "2/2", "3/3"]
    assert [f["overlay"] for f in frames(lines)] == ["4", "3", "2"]
    # Frames outside the level loop and the game's own catch-up take none.
    lines, _ = run(exe, 5, "menu=2-3", "pal=5", SA2_PLAY_FRAMESKIP="2")
    assert [f["play"] for f in frames(lines)] == ["2/2", "4/4", "4/4", "4/4", "5/5"]


def test_the_report_at_exit(exe):
    lines, err = run(exe, 6, "press=1", "menu=4", SA2_PLAY_FRAMESKIP="2")
    total = [x for x in err.splitlines() if x.startswith("frameskip: total")]
    assert len(total) == 2
    assert re.search(r", N=2: 5 level frames \(N=1 0, N=2 5, N=3 0\), 5 undrawn ticks \(\d+\.\d/s\); "
                     r"1 other frames at N=1; frame wall p50 ", total[0]), total[0]
    # frame 1's press and frame 2's release, each zeroed on its frame's second pass
    assert "edges zeroed on 2 of 5 later passes" in total[0]
    # A sample is taken at a level frame's entry, for the level frame just before it: frames 0, 1
    # and 2 (a menu's frame follows frame 3, and frame 5 follows the menu's), 3 x 2 ticks.
    assert re.search(r"; stage timer \+0\.1 s in 0\.0 s of wall time \(\d+\.\d%\); ", total[0]), total[0]
    assert total[1].startswith("frameskip: total: 0 frames the game's own catch-up ran as it does, "
                               "0 level frames with no first-pass Control (left at 1), 0 edges the "
                               "passes wrote (each delivered once), 0 zeroed edges kept")
    # Busy time takes the level loop's run-queue wait from /proc/thread-self/schedstat, which
    # opens and reads on this host: no line says busy time fell back to CPU time alone.
    assert "schedstat" not in err
    hists = [x for x in err.splitlines() if ", ms:count" in x]
    assert [h.split(",")[0] for h in hists] == [
        "frameskip: frame wall time per level frame", "frameskip: game busy time per level frame",
        "frameskip: game busy time per tick"]


def busy_hist(err: str) -> dict[int, int]:
    line = next(x for x in err.splitlines() if x.startswith("frameskip: game busy time per level frame"))
    return {int(k): int(v) for k, v in (x.split(":") for x in line.split("ms:count")[1].split())}


def test_the_run_queue_wait_counts_as_busy_time(sched_exe):
    # 40 ms of run-queue wait read at frame 1's entry: frame 0's busy sample holds it.
    exe, stat = sched_exe
    lines, err = run(exe, 4, "sched=0:9_0_1", "sched=1:9_40000000_1", SA2_PLAY_FRAMESKIP="2")
    assert busy_hist(err) == {0: 2, 40: 1}
    assert "schedstat" not in err


def test_a_schedstat_that_stops_reading_keeps_the_wait_it_read(sched_exe):
    # The fd names a thread, which can exit: its reads then fail (ESRCH). Busy time is a
    # difference of two readings, so a wait that fell to 0 would wrap one sample past 2^64 into
    # the last bucket; the last wait read is kept instead, and one line says so.
    exe, stat = sched_exe
    lines, err = run(exe, 6, "sched=0:9_0_1", "sched=1:9_40000000_1", "sched=3:123", SA2_PLAY_FRAMESKIP="2")
    assert busy_hist(err) == {0: 4, 40: 1}
    assert err.count(f"frameskip: {stat} did not read; the game's busy time is its CPU time alone "
                     "from here (the run-queue wait stays at the 40 ms read last)\n") == 1
    assert err.count("schedstat") == 1
    assert end(lines)["ticks"] == 12


def test_a_schedstat_that_does_not_open_or_parse_leaves_cpu_time(sched_exe):
    exe, stat = sched_exe
    lines, err = run(exe, 4, "sched=0:-", SA2_PLAY_FRAMESKIP="2")
    assert err.count(f"frameskip: {stat} did not open; the game's busy time is its CPU time alone, "
                     "without the level loop's run-queue wait\n") == 1
    assert busy_hist(err) == {0: 3}
    lines, err = run(exe, 4, "sched=0:123", SA2_PLAY_FRAMESKIP="2")
    assert err.count(f"frameskip: {stat} did not read; the game's busy time is its CPU time alone "
                     "from here (the run-queue wait stays at the 0 ms read last)\n") == 1
    assert busy_hist(err) == {0: 3}
    # Off, it is never opened.
    _, err = run(exe, 4, "sched=0:-")
    assert err == ""


def test_the_unit_holds_no_floating_point(exe):
    """The wraps run inside the lifted game, whose x87 state a floating-point instruction or a
    float conversion handed to printf would move (the recorder's rule, tools/s5/objcheck.py)."""
    source = (RUNTIME / "sa2_frameskip.c").read_text()
    assert not re.search(r"\b(float|double)\b", source)
    assert not re.search(r'"[^"]*%[-+ #0]*\d*(\.\d+)?[aAeEfFgG][^"]*"', source)


# ---------------------------------------------------------------- the scheduler


def test_an_owed_boundary_moves_the_clock_n_periods_and_counts_one_frame(threads_exe):
    lines, _ = run(threads_exe, 0, 2, 0, 3, 0, 1, 17, 0)
    rows = [tuple(map(int, x.split())) for x in lines]
    assert rows == [(1, 1 * TICK_US), (2, 3 * TICK_US), (3, 4 * TICK_US), (4, 7 * TICK_US),
                    (5, 8 * TICK_US), (6, 9 * TICK_US), (7, 10 * TICK_US), (8, 11 * TICK_US)]


# ---------------------------------------------------------------- Auto


def auto(exe: Path, *args) -> tuple[list[str], dict]:
    lines, _ = run(exe, "auto", *args)
    return lines[:-1], {k: int(v) for k, v in (x.split("=") for x in lines[-1].split()[1:])}


def test_auto_stays_at_one_on_a_fast_device(exe):
    changes, got = auto(exe, "0:8000:2000:3000", "hitch:20000", 60000)
    assert changes == [] and got["ups"] == 0 and got["N"] == 1


def test_auto_takes_a_render_bound_30_fps_device_to_two_and_keeps_it(exe):
    changes, got = auto(exe, "0:30000:2000:4000", 120000)
    assert changes[0].endswith("N 1 -> 2") and int(changes[0].split()[0]) < 1500
    # N=1's late frames had the game idle: the player's pace, which N=2's paced frames never show
    # getting faster. Step downs wait 30 s, then 60 s after a retry the player reverses.
    assert [c.split(": ")[1] for c in changes] == ["N 1 -> 2", "N 2 -> 1", "N 1 -> 2", "N 2 -> 1", "N 1 -> 2"]
    retries = [int(c.split()[0]) for c in changes if c.endswith("N 2 -> 1")]
    assert 30000 < retries[0] < 36000 and 60000 < retries[1] - retries[0] < 70000
    assert got["guards"] == 0 and got["N"] == 2 and got["downs"] == got["reversals"] == 2
    assert got["at2"] >= 0.98 * (got["at1"] + got["at2"]) and got["refuse"] == 120


def test_auto_s_retries_on_a_player_bound_device_thin_out(exe):
    # A player that needs 25 ms a frame, a game 5 ms a tick and 2 ms. A fixed 3 to 30 s hold made
    # N go 1 -> 2 -> 1 about every 30 s here: 24 ups, 23 downs, 23 reversals in 662 s.
    changes, got = auto(exe, "0:25000:5000:2000", 662000)
    retries = [int(c.split()[0]) for c in changes if c.endswith("N 2 -> 1")]
    assert len(retries) == 4 and (got["ups"], got["downs"], got["reversals"], got["N"]) == (5, 4, 4, 2)
    gaps = [b - a for a, b in zip(retries, retries[1:])]
    assert all(2 * g0 - 10000 < g1 < 2 * g0 + 10000 for g0, g1 in zip(gaps, gaps[1:])), gaps
    assert got["at1"] < 3000 and got["refuse"] == 480
    # Between 2 and 3 likewise: the player needs 40 ms.
    changes, got = auto(exe, "0:40000:2000:4000", 300000)
    assert [c.split(": ")[1] for c in changes][:2] == ["N 1 -> 2", "N 2 -> 3"]
    assert (got["downs"], got["reversals"], got["N"]) == (3, 3, 3) and got["at3"] >= 0.98 * 300000


def test_auto_s_retry_that_fails_slowly_still_doubles_the_refusal(exe):
    # 8 frames queued toward a player that needs 18.6 ms: after a step down to 1 the queue fills
    # for about 1.3 s before the game waits on the player, so the late window comes over 2 s after
    # the step down. Counted only within 2 s, the failure never doubled the refusal, and 30 s
    # later the step down passed for one that held: a retry every ~33 s, 19 ups in 662 s.
    changes, got = auto(exe, "0:18600:4000:2000", "queue:8", 662000)
    downs = [int(c.split()[0]) for c in changes if c.endswith("N 2 -> 1")]
    ups = [int(c.split()[0]) for c in changes if c.endswith("N 1 -> 2")]
    assert all(u - d > 2000 for d, u in zip(downs, ups[1:])), (downs, ups)
    assert len(downs) == 4 and got["reversals"] == 0 and got["refuse"] == 480 and got["N"] == 2
    gaps = [b - a for a, b in zip(downs, downs[1:])]
    assert all(2 * g0 - 10000 < g1 < 2 * g0 + 10000 for g0, g1 in zip(gaps, gaps[1:])), gaps


def test_auto_s_refusal_lengths_are_per_n(exe):
    # The 1 <-> 2 retries of a player-bound device double N=1's refusal. A 4 s spell at 45 ms then
    # takes N to 3: with one length for every N, that refusal of 2 took the 1 <-> 2 backoff (N=3
    # for 270 s) and the step down to 2 that held then reset it (retries every 30 s again).
    plain, base = auto(exe, "0:25000:5000:2000", 1000000)
    spell, got = auto(exe, "0:25000:5000:2000", "300000:45000:5000:2000", "304000:25000:5000:2000", 1000000)
    at = lambda cs, what: [int(c.split()[0]) for c in cs if c.endswith(what)]
    assert at(spell, "N 2 -> 3")[0] - 300000 < 1000
    assert at(spell, "N 3 -> 2")[0] - at(spell, "N 2 -> 3")[0] < 32000 and got["at3"] < 32000
    # N=1's retries come when they would have without the spell.
    later = lambda cs: at(cs, "N 2 -> 1")[3:]
    assert len(later(spell)) == len(later(plain)) == 2
    assert all(abs(a - b) < 1000 for a, b in zip(later(spell), later(plain))), (spell, plain)
    assert got["refuse"] == base["refuse"] == 600 and got["refuse2"] == 30


def test_auto_s_step_down_that_holds_resets_the_higher_n_s_refusal(exe):
    # A 40 ms player builds N=2's refusal over the 2 <-> 3 retries; at 8 ms the next retry holds,
    # then the step to 1 holds, and a later spell at 45 ms costs 30 s at 3, not N=2's backoff.
    changes, got = auto(exe, "0:40000:2000:4000", "200000:8000:2000:4000", "400000:45000:2000:4000",
                        "404000:8000:2000:4000", 600000)
    at = lambda what: [int(c.split()[0]) for c in changes if c.endswith(what)]
    # retries of 2 at about 31, 92 and 213 s: N=2's refusal was 30, 60, then 120 s
    r = at("N 3 -> 2")[:3]
    assert 55000 < r[1] - r[0] < 65000 and 115000 < r[2] - r[1] < 125000, changes
    spell_up, spell_down = [t for t in at("N 2 -> 3") if t > 400000], [t for t in at("N 3 -> 2") if t > 400000]
    assert spell_up and spell_down and spell_down[0] - spell_up[0] < 32000, changes
    assert (got["N"], got["refuse"], got["refuse2"]) == (1, 30, 30)


def test_auto_s_refusal_ends_with_the_stage_and_outlives_a_pause(exe):
    # A new stage may draw at another pace: its first retry comes within the step-down hold.
    changes, got = auto(exe, "0:25000:5000:2000", "stage:60000", 120000)
    stage = [int(c.split()[0]) for c in changes if c.endswith(": stage")][0]
    after = [int(c.split()[0]) for c in changes if c.endswith("N 2 -> 1") and int(c.split()[0]) > stage]
    assert after and after[0] - stage < 7000
    # A pause (the pause menu is outside the level loop) keeps it: the retries come when they would.
    plain, _ = auto(exe, "0:25000:5000:2000", 120000)
    paused, got = auto(exe, "0:25000:5000:2000", "pause:10000", "pause:60000", 120000)
    downs = lambda cs: [int(c.split()[0]) for c in cs if c.endswith("N 2 -> 1")]
    assert len(downs(paused)) == len(downs(plain)) == 2
    assert all(abs(a - b) < 1000 for a, b in zip(downs(paused), downs(plain)))


def test_auto_takes_a_20_fps_device_to_three(exe):
    changes, got = auto(exe, "0:45000:2000:4000", 60000)
    assert changes[:2] == [c for c in changes[:2] if "-> 2" in c or "-> 3" in c]
    assert got["N"] == 3 and got["at3"] >= 0.9 * 60000


def test_auto_s_spiral_guard_keeps_a_logic_bound_device_at_one(exe):
    # 20 ms of game logic a tick: more ticks a frame only drop frames.
    changes, got = auto(exe, "0:5000:20000:2000", 120000)
    assert got["guards"] >= 1 and got["N"] == 1
    assert got["at1"] >= 0.9 * (got["at1"] + got["at2"])
    assert got["guards"] <= 7 and got["up_hold"] == 30


def test_auto_keeps_two_where_the_draw_side_was_the_cost(exe):
    # 10 ms a tick and 12 ms a frame: 22 ms at N=1, 32 ms at N=2, inside 33.3.
    changes, got = auto(exe, "0:10000:10000:12000", 60000)
    assert got["guards"] == 0 and got["N"] == 2 and got["at2"] >= 0.95 * 60000


def test_auto_steps_down_when_the_game_s_work_falls(exe):
    # Late at N=1 on the game's own work (10 ms a tick and 12 ms), then a lighter scene: the busy
    # time shows it, and N=1 is tried after the 3 s hold, and kept.
    changes, got = auto(exe, "0:10000:10000:12000", "20000:8000:2000:4000", 80000)
    assert [c.split(": ")[1] for c in changes] == ["N 1 -> 2", "N 2 -> 1"]
    assert 20000 + 2500 < int(changes[1].split()[0]) < 20000 + 4500
    assert (got["N"], got["reversals"], got["guards"]) == (1, 0, 0)


def test_auto_steps_down_after_the_player_catches_up(exe):
    # The player at 30 ms, then 8 ms from 20 s: nothing at N=2 shows it, and the retry at the
    # refusal's end finds it and keeps N=1.
    changes, got = auto(exe, "0:30000:2000:4000", "20000:8000:2000:4000", 80000)
    assert [c.split(": ")[1] for c in changes] == ["N 1 -> 2", "N 2 -> 1"]
    assert 30000 < int(changes[1].split()[0]) < 36000
    assert (got["N"], got["downs"], got["reversals"]) == (1, 1, 0)


# ---------------------------------------------------------------- the links and the runs


def test_every_link_wraps_the_level_loop_and_control():
    assert "sa2_frameskip.c" in LS.RUNTIME_C
    w = LS.PRODUCTION_WRAPS
    assert {"sub_43ce20", "sub_441ba0"} <= set(w)
    # Before ReadConfig: the HUD sites stay the list's tail (test_widescreen.py).
    assert w.index("sub_43ce20") < w.index("sub_405310") and w.index("sub_441ba0") < w.index("sub_405310")
    source = (RUNTIME / "sa2_frameskip.c").read_text()
    for name in ("sub_43ce20", "sub_441ba0"):
        assert f"void *__wrap_{name}(void *state, uint32_t pc, void *memory)" in source


def test_every_reader_of_the_lists_reads_them_whole():
    sys.path.insert(0, str(ROOT / "tools" / "s7"))
    import arm64_link as A  # noqa: E402  ast.literal_eval of link_s4.py's literals
    lists = A.link_s4_lists(ROOT)
    assert lists["PRODUCTION_WRAPS"] == LS.PRODUCTION_WRAPS and lists["RUNTIME_C"] == LS.RUNTIME_C
    import build_cost as BC  # noqa: E402  a regex over the same literals
    assert BC.production_wraps() == LS.PRODUCTION_WRAPS
    assert "sa2_frameskip.c" in BC.s4_added_c()


def test_a_shell_s_setting_never_reaches_a_registered_run(monkeypatch):
    monkeypatch.setenv("SA2_PLAY_FRAMESKIP", "3")
    import device_profile as DP  # noqa: E402
    import reach_receipt as RR  # noqa: E402
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    import present_run as PR  # noqa: E402
    import play_live as PLV  # noqa: E402
    assert "SA2_PLAY_FRAMESKIP" in DP.OWNED
    for clean in (DP.clean_environ, RR.clean_environ, PR.clean_env, PLV.game_env):
        assert "SA2_PLAY_FRAMESKIP" not in clean(), clean
    # play_live passes it only from its own --frameskip.
    assert PLV.session_game_env({"frameskip": "2"})["SA2_PLAY_FRAMESKIP"] == "2"
    assert "SA2_PLAY_FRAMESKIP" not in PLV.session_game_env({"frameskip": None})


def test_a_run_s_extra_settings_cannot_carry_it():
    """stage_run, demo_run and tick_replay refuse an extra setting that is one of DP.OWNED."""
    for tool in ("tools/s4/stage_run.py", "tools/s4/demo_run.py", "tools/s4/tick_replay.py"):
        assert "DP.OWNED" in (ROOT / tool).read_text(), tool


SETTERS = {
    "runtime/sa2_frameskip.c", "runtime/sa2_frameskip.h", "tools/s4/device_profile.py",
    "tools/s5/play_live.py", "host/android/java/com/sa2recomp/app/GameSettings.java",
    "tools/s4/link_s4.py",      # PRODUCTION_WRAPS' comment
}


def test_only_play_and_the_android_settings_set_it():
    """Every source that names SA2_PLAY_FRAMESKIP, outside the tests and the docs: the unit, the
    strip list, play_live's --frameskip, the Android settings and link_s4's comment. A tool that
    set it in a run of its own would run that run at N ticks a frame with no replay variable to
    force it off, so a new name here is reviewed before it is listed."""
    found = set()
    for top in ("runtime", "tools", "host", "harness"):
        for p in (ROOT / top).rglob("*"):
            if p.is_file() and p.suffix in {".c", ".h", ".cpp", ".py", ".java", ".sh", ".kt"} \
                    and "SA2_PLAY_FRAMESKIP" in p.read_text(errors="replace"):
                found.add(str(p.relative_to(ROOT)))
    assert found == SETTERS


def play_plan(*argv: str) -> dict:
    sys.path.insert(0, str(ROOT / "tools" / "s5"))
    import play_live as PLV  # noqa: E402
    return PLV, PLV.plan(PLV.parser().parse_args(["--dry-run", *argv]))


def test_play_live_passes_its_frameskip_on_purpose():
    PLV, pl = play_plan("--frameskip", "2")
    assert pl["frameskip"] == "2"
    assert "frameskip: SA2_PLAY_FRAMESKIP=2 in the game's environment (runtime/sa2_frameskip.c: stage " \
           "frames run 2 game ticks)" in PLV.show(pl, False)
    _, pl = play_plan("--frameskip", "auto")
    assert "frameskip: SA2_PLAY_FRAMESKIP=auto in the game's environment (runtime/sa2_frameskip.c: " \
           "stage frames run 1 to 3 game ticks)" in PLV.show(pl, False)
    _, pl = play_plan()
    assert pl["frameskip"] is None and "frameskip: off" in PLV.show(pl, False)


def test_play_live_refuses_frameskip_with_the_receipt():
    with pytest.raises(SystemExit, match="--frameskip and --receipt do not go together"):
        play_plan("--scene", "metalharbor", "--receipt", "--frameskip", "3")
    with pytest.raises(SystemExit):
        play_plan("--frameskip", "4")          # argparse: not a choice
