/* The window and the message pump. See sa2_window.h for the reasoning. */
#include "sa2_window.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "guest_memory.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

/* ------------------------------------------------------------- state */

static sa2_wnd_class  g_classes[SA2_WND_MAX_CLASSES];
static uint32_t       g_class_count;
static sa2_wnd_window g_windows[SA2_WND_MAX_WINDOWS];
static uint32_t       g_window_count;

static uint32_t g_token_used[6];          /* one counter per kind base */

static sa2_wnd_msg g_queue[SA2_WND_QUEUE_MAX];
static uint32_t    g_qhead, g_qtail, g_qdepth;

static sa2_wnd_event g_log[SA2_WND_LOG_MAX];
static uint32_t      g_log_used, g_log_seq;

static sa2_wnd_stats g_st;

static uint32_t g_poll_limit = SA2_WND_DEFAULT_POLLS;
static uint32_t g_screen_w = 1920, g_screen_h = 1080;

/* The rest of the USER32 surface; see the header comment for each. */
static int      g_strict;                  /* SA2_WND_STRICT */
static int      g_cursor_set;              /* SA2_WND_CURSOR overrode the default */
static int32_t  g_cursor_x, g_cursor_y;
static uint32_t g_send_depth, g_send_limit = SA2_WND_DEFAULT_SEND_DEPTH;
static uint32_t g_wait_yields = SA2_WND_DEFAULT_WAIT_YIELDS;
static sa2_wnd_timer g_timers[SA2_WND_TIMERS_MAX];
static sa2_wnd_key   g_keys[SA2_WND_KEYS_MAX];
static uint32_t      g_key_used;

/* Scheduled injections: "poll:msg[:wparam[:lparam]]". */
#define SA2_WND_INJECT_MAX 16u
typedef struct { uint32_t poll, msg, wparam, lparam; uint8_t fired; } inject_row;
static inject_row g_inject[SA2_WND_INJECT_MAX];
static uint32_t   g_inject_count;

/* The virtual clock the message timestamps come from, so a replay's messages
 * carry the same times its imports do. `sa2_virtual_us` is defined in
 * sa2_imports.c and declared weak here so a program that links this file
 * without that one still builds (the unit probe does). */
uint64_t sa2_virtual_us(void) __attribute__((weak));
static uint32_t now_ms(void) {
    return sa2_virtual_us ? (uint32_t)(sa2_virtual_us() / 1000ull) : 0u;
}
static uint64_t now_us(void) { return sa2_virtual_us ? sa2_virtual_us() : 0ull; }

/* The scheduler, for WaitMessage. Declared weak for the same reason the clock
 * is: the unit probe links this file without runtime/sa2_threads.c, and a
 * hard reference would make the probe carry the whole scheduler to test a
 * message queue. With no scheduler linked in there is exactly one thread, so
 * "hand off and see whether anybody posted" is answered correctly by "nobody
 * else could have".
 *
 * sa2_threads.h is included at the top for SA2_WAIT_SLEEP and SA2_WAKE_NEVER,
 * so those numbers are not copied; only the two declarations are repeated
 * here, to add `weak` to them. */
int sa2_sched_yield(const char *where) __attribute__((weak));
int sa2_sched_block(int reason, uint32_t object, uint64_t deadline_us,
                    const char *where) __attribute__((weak));

/* --------------------------------------------------------------- config */

static void parse_inject(const char *spec) {
    /* "poll:msg[:wparam[:lparam]]" items, comma separated. A malformed item is
     * refused rather than skipped: an injection that silently did not happen
     * would make the control run agree with the baseline for the wrong
     * reason, which is the exact failure mode this project keeps finding. */
    const char *p = spec;
    while (*p) {
        if (g_inject_count >= SA2_WND_INJECT_MAX) {
            sa2_trap_note("SA2_PUMP_INJECT names more than %u messages", SA2_WND_INJECT_MAX);
            sa2_trap_raise(SA2_TRAP_FAULT, 0);
        }
        char item[64];
        size_t n = 0;
        while (*p && *p != ',' && n + 1 < sizeof item) item[n++] = *p++;
        item[n] = 0;
        if (*p == ',') p++;
        if (!n) continue;
        unsigned long v[4] = {0, 0, 0, 0};
        int k = 0;
        char *q = item;
        while (k < 4) {
            char *end = NULL;
            v[k] = strtoul(q, &end, 0);
            if (end == q) break;
            k++;
            if (*end != ':') { q = end; break; }
            q = end + 1;
        }
        if (k < 2) {
            sa2_trap_note("SA2_PUMP_INJECT item \"%s\" is not poll:msg[:wparam[:lparam]]", item);
            sa2_trap_raise(SA2_TRAP_FAULT, 0);
        }
        g_inject[g_inject_count].poll = (uint32_t)v[0];
        g_inject[g_inject_count].msg = (uint32_t)v[1];
        g_inject[g_inject_count].wparam = (uint32_t)v[2];
        g_inject[g_inject_count].lparam = (uint32_t)v[3];
        g_inject[g_inject_count].fired = 0;
        g_inject_count++;
    }
}

void sa2_window_config_from_env(void) {
    const char *v = getenv("SA2_PUMP_POLLS");
    if (v && *v) g_poll_limit = (uint32_t)strtoul(v, NULL, 0);
    v = getenv("SA2_WND_SCREEN");
    if (v && *v) {
        unsigned long w = strtoul(v, (char **)&v, 0);
        if (*v == 'x' || *v == 'X' || *v == '*') {
            unsigned long h = strtoul(v + 1, NULL, 0);
            if (w && h) { g_screen_w = (uint32_t)w; g_screen_h = (uint32_t)h; }
        }
    }
    v = getenv("SA2_PUMP_INJECT");
    if (v && *v) parse_inject(v);
    v = getenv("SA2_WND_STRICT");
    if (v && *v) g_strict = (int)strtol(v, NULL, 0);
    v = getenv("SA2_WND_CURSOR");
    if (v && *v) {
        long x = strtol(v, (char **)&v, 0);
        if (*v == ',' || *v == 'x' || *v == ':') {
            g_cursor_x = (int32_t)x;
            g_cursor_y = (int32_t)strtol(v + 1, NULL, 0);
            g_cursor_set = 1;
        } else {
            sa2_trap_note("SA2_WND_CURSOR is \"x,y\" in screen coordinates");
            sa2_trap_raise(SA2_TRAP_FAULT, 0);
        }
    }
    v = getenv("SA2_WND_SEND_DEPTH");
    if (v && *v) {
        unsigned long d = strtoul(v, NULL, 0);
        /* Zero would make every SendMessageW a refusal, which is a break and
         * not a setting; the bound has to admit at least the outermost send. */
        if (!d) {
            sa2_trap_note("SA2_WND_SEND_DEPTH=0 would refuse every SendMessageW, "
                          "including the first; 1 is the smallest bound that means "
                          "\"no nesting\"");
            sa2_trap_raise(SA2_TRAP_FAULT, 0);
        }
        g_send_limit = (uint32_t)d;
    }
    v = getenv("SA2_WND_WAIT_YIELDS");
    if (v && *v) g_wait_yields = (uint32_t)strtoul(v, NULL, 0);
}

int sa2_window_strict(void) { return g_strict; }

void sa2_window_reset(void) {
    memset(g_classes, 0, sizeof g_classes);
    memset(g_windows, 0, sizeof g_windows);
    memset(g_queue, 0, sizeof g_queue);
    memset(g_log, 0, sizeof g_log);
    memset(&g_st, 0, sizeof g_st);
    memset(g_token_used, 0, sizeof g_token_used);
    memset(g_timers, 0, sizeof g_timers);
    memset(g_keys, 0, sizeof g_keys);
    g_class_count = g_window_count = 0;
    g_qhead = g_qtail = g_qdepth = 0;
    g_log_used = g_log_seq = 0;
    g_inject_count = 0;
    g_key_used = 0;
    g_send_depth = 0;
}

void     sa2_window_set_poll_limit(uint32_t polls) { g_poll_limit = polls; }
uint32_t sa2_window_poll_limit(void) { return g_poll_limit; }
uint32_t sa2_window_screen_w(void) { return g_screen_w; }
uint32_t sa2_window_screen_h(void) { return g_screen_h; }

/* ---------------------------------------------------------------- log */

void sa2_window_log(const char *what, uint32_t caller,
                    uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) {
    g_log_seq++;
    if (g_log_used >= SA2_WND_LOG_MAX) return;
    sa2_wnd_event *e = &g_log[g_log_used++];
    e->seq = g_log_seq;
    e->caller = caller;
    e->a[0] = a0; e->a[1] = a1; e->a[2] = a2; e->a[3] = a3;
    snprintf(e->what, sizeof e->what, "%s", what ? what : "?");
}
uint32_t             sa2_window_log_count(void) { return g_log_used; }
const sa2_wnd_event *sa2_window_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

/* -------------------------------------------------------------- tokens */

static int token_kind_index(uint32_t base) {
    switch (base) {
        case SA2_WND_HWND_BASE:    return 0;
        case SA2_WND_HICON_BASE:   return 1;
        case SA2_WND_HCURSOR_BASE: return 2;
        case SA2_WND_HBRUSH_BASE:  return 3;
        case SA2_WND_HDC_BASE:     return 4;
        case SA2_WND_HMENU_BASE:   return 5;
        default: return -1;
    }
}

uint32_t sa2_window_token(uint32_t kind_base, uint32_t hinstance, uint32_t resource) {
    (void)hinstance; (void)resource;
    int k = token_kind_index(kind_base);
    if (k < 0) return 0;
    if (g_token_used[k] >= SA2_WND_MAX_TOKENS) return 0;
    uint32_t t = kind_base + g_token_used[k] * SA2_WND_TOKEN_STRIDE;
    g_token_used[k]++;
    if (k == 1) g_st.icons++;
    if (k == 2) g_st.cursors++;
    if (k == 4) g_st.dcs++;
    if (k == 5) g_st.menus++;
    return t;
}

const char *sa2_window_token_kind(uint32_t token) {
    uint32_t base = token & 0xfff00000u;
    switch (base) {
        case SA2_WND_HWND_BASE:    return "HWND";
        case SA2_WND_HICON_BASE:   return "HICON";
        case SA2_WND_HCURSOR_BASE: return "HCURSOR";
        case SA2_WND_HBRUSH_BASE:  return "HBRUSH";
        case SA2_WND_HDC_BASE:     return "HDC";
        case SA2_WND_HMENU_BASE:   return "HMENU";
        default: return NULL;
    }
}

/* ------------------------------------------------------ classes, windows */

uint32_t sa2_window_register_class(const sa2_wnd_class *c) {
    if (!c || g_class_count >= SA2_WND_MAX_CLASSES) return 0;
    for (uint32_t i = 0; i < g_class_count; i++)
        if (!strcmp(g_classes[i].name, c->name)) return 0;   /* already registered */
    sa2_wnd_class *d = &g_classes[g_class_count];
    *d = *c;
    d->atom = SA2_WND_ATOM_FIRST + g_class_count;
    g_class_count++;
    g_st.classes = g_class_count;
    return d->atom;
}

static const sa2_wnd_class *class_by_name(const char *name) {
    for (uint32_t i = 0; i < g_class_count; i++)
        if (!strcmp(g_classes[i].name, name)) return &g_classes[i];
    return NULL;
}

uint32_t sa2_window_create(const sa2_wnd_window *w) {
    if (!w || g_window_count >= SA2_WND_MAX_WINDOWS) return 0;
    const sa2_wnd_class *c = class_by_name(w->class_name);
    if (!c) return 0;             /* Windows fails CreateWindowEx on an unregistered class */
    sa2_wnd_window *d = &g_windows[g_window_count];
    *d = *w;
    d->hwnd = sa2_window_token(SA2_WND_HWND_BASE, w->hinstance, 0);
    d->wndproc = c->wndproc;
    d->class_wndproc = c->wndproc;
    /* Windows creates a window enabled, and `EnableWindow` returns whether it
     * was previously *disabled* -- so a record that started at zero would have
     * the first EnableWindow(FALSE) answer "it was already disabled". */
    d->enabled = 1;
    /* cbWndExtra, clamped to what this file can hold. A class asking for more
     * would silently get fewer addressable indices, so it is refused here
     * rather than at the first GetWindowLongW that lands past the end. */
    if (c->wnd_extra > SA2_WND_EXTRA_MAX * 4u) {
        sa2_trap_note("RegisterClassW(\"%s\") asked for cbWndExtra %u, and this runtime "
                      "holds %u bytes per window", c->name, c->wnd_extra,
                      SA2_WND_EXTRA_MAX * 4u);
        sa2_trap_raise(SA2_TRAP_FAULT, 0);
    }
    d->extra_bytes = c->wnd_extra;
    if (!d->hwnd) return 0;
    g_window_count++;
    g_st.windows = g_window_count;
    return d->hwnd;
}

const sa2_wnd_class  *sa2_window_class_at(uint32_t i) { return i < g_class_count ? &g_classes[i] : NULL; }
const sa2_wnd_window *sa2_window_at(uint32_t i) { return i < g_window_count ? &g_windows[i] : NULL; }
uint32_t sa2_window_class_count(void) { return g_class_count; }
uint32_t sa2_window_count(void) { return g_window_count; }
uint32_t sa2_window_main(void) { return g_window_count ? g_windows[0].hwnd : 0u; }

sa2_wnd_window *sa2_window_mutable(uint32_t hwnd) {
    for (uint32_t i = 0; i < g_window_count; i++)
        if (g_windows[i].hwnd == hwnd) return &g_windows[i];
    return NULL;
}
const sa2_wnd_window *sa2_window_of(uint32_t hwnd) { return sa2_window_mutable(hwnd); }

/* --------------------------------------------------------- the queue */

int sa2_window_post(uint32_t hwnd, uint32_t msg, uint32_t wparam, uint32_t lparam) {
    if (g_qdepth >= SA2_WND_QUEUE_MAX) { g_st.dropped++; return 0; }
    sa2_wnd_msg *m = &g_queue[g_qtail];
    g_qtail = (g_qtail + 1u) % SA2_WND_QUEUE_MAX;
    g_qdepth++;
    memset(m, 0, sizeof *m);
    m->hwnd = hwnd;
    m->msg = msg;
    m->wparam = wparam;
    m->lparam = lparam;
    m->time = now_ms();
    g_st.posted++;
    g_st.queue_depth = g_qdepth;
    if (g_qdepth > g_st.queue_peak) g_st.queue_peak = g_qdepth;
    if (msg == SA2_WM_QUIT) { g_st.quit_posted = 1; g_st.quit_code = wparam; }
    return 1;
}

void sa2_window_post_quit(uint32_t code, int by_host) {
    if (g_st.quit_posted) return;
    sa2_window_post(0, SA2_WM_QUIT, code, 0);
    if (by_host) g_st.quit_by_host = 1; else g_st.quit_by_guest = 1;
}

static int queue_front(sa2_wnd_msg *out) {
    if (!g_qdepth) return 0;
    *out = g_queue[g_qhead];
    return 1;
}

static int queue_pop(sa2_wnd_msg *out) {
    if (!g_qdepth) return 0;
    *out = g_queue[g_qhead];
    g_qhead = (g_qhead + 1u) % SA2_WND_QUEUE_MAX;
    g_qdepth--;
    g_st.queue_depth = g_qdepth;
    g_st.delivered++;
    return 1;
}

/* --------------------------------------------------------- the timers
 *
 * `SetTimer`'s WM_TIMER, on the virtual clock. Two properties are the real
 * semantics rather than a simplification, and both matter here:
 *
 *  * **WM_TIMER is synthesised, not queued.** Windows generates it when the
 *    queue has nothing else in it, which is what makes it the low-priority
 *    message the documentation describes. So this runs at the point the pump
 *    is about to answer "no messages", and nowhere else.
 *  * **At most one is outstanding per timer.** A real timer whose ticks the
 *    application did not keep up with delivers one WM_TIMER, not the whole
 *    backlog. That is not only faithful, it is load-bearing here: this
 *    runtime's clock *jumps* -- `sa2_sched_frame_boundary` moves it a frame at
 *    a time and `advance_time` moves it straight to the next deadline -- so a
 *    timer that queued every missed tick would put thousands of messages in a
 *    64-entry queue the first time the clock skipped a second. The skipped
 *    ticks are counted in `timer_coalesced` rather than dropped silently.
 */
static uint32_t timer_period_us(const sa2_wnd_timer *t) {
    return t->elapse_ms * 1000u;
}

/* Post the WM_TIMER for every timer due at or before now. Returns how many
 * messages went into the queue. */
static int timers_fire_due(void) {
    const uint64_t now = now_us();
    int fired = 0;
    for (uint32_t i = 0; i < SA2_WND_TIMERS_MAX; i++) {
        sa2_wnd_timer *t = &g_timers[i];
        if (!t->used || t->next_due_us > now) continue;
        const uint64_t period = timer_period_us(t);
        /* How many ticks this one tick stands for. `period` is never zero:
         * sa2_window_set_timer clamps uElapse to USER_TIMER_MINIMUM. */
        const uint64_t missed = (now - t->next_due_us) / period;
        if (missed) g_st.timer_coalesced += (uint32_t)missed;
        t->next_due_us += (missed + 1ull) * period;
        t->fires++;
        g_st.timer_fires++;
        /* lParam is the TIMERPROC, which Windows passes and which is 0 for
         * every call site in this image (0x00403f59 pushes 0). */
        if (sa2_window_post(t->hwnd, SA2_WM_TIMER, t->id, t->proc)) fired++;
    }
    return fired;
}

uint64_t sa2_window_timer_next_due_us(void) {
    uint64_t best = 0;
    for (uint32_t i = 0; i < SA2_WND_TIMERS_MAX; i++)
        if (g_timers[i].used && (!best || g_timers[i].next_due_us < best))
            best = g_timers[i].next_due_us;
    return best;
}

/* USER_TIMER_MINIMUM. Windows clamps uElapse to it, so a guest asking for 0
 * gets 10 ms rather than a timer that is due on every single pass -- which
 * here would be a WM_TIMER per poll and a pump that never runs the game. */
#define SA2_WND_TIMER_MIN_MS 10u

uint32_t sa2_window_set_timer(uint32_t hwnd, uint32_t id, uint32_t elapse_ms, uint32_t proc) {
    if (elapse_ms < SA2_WND_TIMER_MIN_MS) elapse_ms = SA2_WND_TIMER_MIN_MS;
    /* Windows replaces a timer that already exists with the same window and
     * id, and keeps the id. A second row would give the guest two WM_TIMERs
     * for one SetTimer. */
    sa2_wnd_timer *slot = NULL;
    for (uint32_t i = 0; i < SA2_WND_TIMERS_MAX; i++)
        if (g_timers[i].used && g_timers[i].hwnd == hwnd && g_timers[i].id == id)
            { slot = &g_timers[i]; break; }
    if (!slot)
        for (uint32_t i = 0; i < SA2_WND_TIMERS_MAX; i++)
            if (!g_timers[i].used) { slot = &g_timers[i]; break; }
    if (!slot) { g_st.timers_refused++; return 0; }
    const int reused = slot->used;
    slot->used = 1;
    slot->hwnd = hwnd;
    slot->id = id;
    slot->elapse_ms = elapse_ms;
    slot->proc = proc;
    slot->next_due_us = now_us() + (uint64_t)elapse_ms * 1000ull;
    if (!reused) slot->fires = 0;
    g_st.timers_set++;
    return id;
}

int sa2_window_kill_timer(uint32_t hwnd, uint32_t id) {
    for (uint32_t i = 0; i < SA2_WND_TIMERS_MAX; i++) {
        if (!g_timers[i].used || g_timers[i].hwnd != hwnd || g_timers[i].id != id) continue;
        g_timers[i].used = 0;
        g_st.timers_killed++;
        return 1;
    }
    return 0;
}

uint32_t sa2_window_timer_count(void) {
    uint32_t n = 0;
    for (uint32_t i = 0; i < SA2_WND_TIMERS_MAX; i++) if (g_timers[i].used) n++;
    return n;
}
const sa2_wnd_timer *sa2_window_timer_at(uint32_t i) {
    return i < SA2_WND_TIMERS_MAX ? &g_timers[i] : NULL;
}

/* --------------------------------------------------------- the pump */

int sa2_window_poll(sa2_wnd_msg *out) {
    const uint32_t poll = g_st.polls++;   /* 0-based: the index this call answers */

    /* The host's scheduled messages, before the answer they are meant to
     * change. Injecting after would make the control run behave like the
     * baseline for one extra iteration and report the same iteration count. */
    for (uint32_t i = 0; i < g_inject_count; i++) {
        if (g_inject[i].fired || g_inject[i].poll != poll) continue;
        g_inject[i].fired = 1;
        g_st.injected++;
        sa2_window_post(g_inject[i].msg == SA2_WM_QUIT ? 0u : sa2_window_main(),
                        g_inject[i].msg, g_inject[i].wparam, g_inject[i].lparam);
        if (g_inject[i].msg == SA2_WM_QUIT) g_st.quit_by_host = 1;
    }

    /* The budget. When it is spent the host posts WM_QUIT and the guest leaves
     * its loop through its own code, which is the difference between stopping
     * a program and ending one. */
    if (g_poll_limit && poll + 1u >= g_poll_limit && !g_st.quit_posted)
        sa2_window_post_quit(0, 1);

    /* WM_TIMER last and only into an empty queue: it is the message Windows
     * synthesises when there is nothing else to answer with. */
    if (!g_qdepth) timers_fire_due();

    const int have = queue_front(out);
    if (have) g_st.polls_message++; else g_st.polls_empty++;
    return have;
}

int sa2_window_get(sa2_wnd_msg *out) {
    g_st.gets++;
    if (!g_qdepth) {
        /* A real GetMessage would sleep here until a message arrived. Nothing
         * in this process can ever produce one while it sleeps, so sleeping is
         * a hang with no exit and the runtime says so instead. */
        g_st.blocking_get_refused++;
        return -1;
    }
    queue_pop(out);
    if (out->msg == SA2_WM_QUIT) { g_st.quit_delivered++; return 0; }
    return 1;
}

void sa2_window_note_translate(void) { g_st.translates++; }
void sa2_window_note_dispatch(uint32_t is_default) {
    g_st.dispatches++;
    if (is_default) g_st.defwndproc_calls++;
}
void sa2_window_note_wndproc_returned(void) { g_st.wndproc_calls++; }

static sa2_wnd_caller g_poll_callers[SA2_WND_CALLERS_MAX];
static uint32_t       g_poll_caller_used;

void sa2_window_note_poll_caller(uint32_t caller) {
    for (uint32_t i = 0; i < g_poll_caller_used; i++)
        if (g_poll_callers[i].caller == caller) { g_poll_callers[i].polls++; return; }
    if (g_poll_caller_used >= SA2_WND_CALLERS_MAX) return;
    g_poll_callers[g_poll_caller_used].caller = caller;
    g_poll_callers[g_poll_caller_used].polls = 1;
    g_poll_caller_used++;
}
uint32_t sa2_window_poll_caller_count(void) { return g_poll_caller_used; }
const sa2_wnd_caller *sa2_window_poll_caller_at(uint32_t i) {
    return i < g_poll_caller_used ? &g_poll_callers[i] : NULL;
}
uint32_t sa2_window_polls_from(uint32_t caller) {
    for (uint32_t i = 0; i < g_poll_caller_used; i++)
        if (g_poll_callers[i].caller == caller) return g_poll_callers[i].polls;
    return 0;
}

void sa2_window_stats(sa2_wnd_stats *out) {
    if (!out) return;
    *out = g_st;
    out->queue_depth = g_qdepth;
    /* Counted from the table rather than from a running total, for the same
     * reason `queue_depth` is: a total that is incremented in one place and
     * decremented in another can disagree with the thing it describes, and a
     * live-timer count that says 1 with an empty table is exactly the kind of
     * number a gate would read as evidence. */
    out->timers_live = sa2_window_timer_count();
    out->keys_distinct = g_key_used;
}

/* --------------------------------------------------------- metrics
 *
 * Five indices are read by the window-sizing code at 0x00401a67, and no other
 * index is reached by this image on this path. The frame and caption sizes are
 * *named placeholders*: no live-oracle capture exists to agree with, so they
 * are a constant the environment can move rather than a measurement pretending
 * to be one -- the same discipline `sa2_imports.c` applies to the virtual
 * epoch and the install path. The screen extents are the virtual display's,
 * which is a host decision and always was.
 *
 * Any other index is refused rather than answered with a plausible number: a
 * wrong metric here moves the window the guest computes, and a window at the
 * wrong place that nobody knows is wrong is worse than a run that stops. */
int sa2_window_metric(uint32_t index, uint32_t *out) {
    switch (index) {
        case 0:  *out = g_screen_w; return 1;   /* SM_CXSCREEN */
        case 1:  *out = g_screen_h; return 1;   /* SM_CYSCREEN */
        case 4:  *out = 19u; return 1;          /* SM_CYCAPTION, classic 96 dpi */
        case 32: *out = 4u;  return 1;          /* SM_CXFRAME / SM_CXSIZEFRAME */
        case 33: *out = 4u;  return 1;          /* SM_CYFRAME / SM_CYSIZEFRAME */
        default: return 0;
    }
}

/* ============================ the rest of USER32 ======================== */

/* ----------------------------------------------- no-ops and inventions */

void sa2_window_note_noop(const char *what, uint32_t caller,
                          uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3) {
    g_st.noops++;
    sa2_window_log(what, caller, a0, a1, a2, a3);
}

int sa2_window_invent(const char *what, uint32_t value, uint32_t *out) {
    if (g_strict) {
        g_st.invented_refused++;
        sa2_window_log("strict-refused", 0, value, 0, 0, 0);
        (void)what;
        return 0;
    }
    g_st.invented++;
    sa2_window_log(what, 0, value, 0, 0, 0);
    if (out) *out = value;
    return 1;
}

/* ------------------------------------------------------- 1. SendMessageW
 *
 * The call itself is the adapter's, because only it can reach lifted code.
 * What is here is the bound, and the bound is not a safety belt: a window
 * procedure that sends to its own window is an ordinary Win32 shape (a control
 * answering WM_SETTEXT by sending WM_SETTEXT to its parent, say), and with no
 * bound the recursion runs until the *guest* stack is exhausted -- which
 * surfaces as a stack-guard refusal in sa2_threads.c thousands of frames away
 * from the send that started it. Refusing at a named depth puts the trap at
 * the site. */
int sa2_window_send_enter(uint32_t hwnd, uint32_t msg) {
    if (g_send_depth >= g_send_limit) { g_st.send_refused++; return 0; }
    g_send_depth++;
    if (g_send_depth > g_st.send_depth_peak) g_st.send_depth_peak = g_send_depth;
    g_st.sends++;
    g_st.sends_dispatched++;
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (w) w->sends++;
    (void)msg;
    return 1;
}

void sa2_window_send_leave(void) { if (g_send_depth) g_send_depth--; }
uint32_t sa2_window_send_depth(void) { return g_send_depth; }
uint32_t sa2_window_send_depth_limit(void) { return g_send_limit; }

void sa2_window_note_send_default(uint32_t hwnd, uint32_t msg) {
    (void)hwnd; (void)msg;
    g_st.sends++;
    g_st.sends_default++;
}

/* --------------------------------------- 2. GetWindowLongW / SetWindowLongW
 *
 * Real per-window state. The two indices this image depends on are GWL_STYLE,
 * which 0x00401679 reads and branches on, and GWL_WNDPROC, which 0x00403e4a
 * writes to subclass a control -- so the write goes into the same `wndproc`
 * field DispatchMessageW and SendMessageW dispatch through, and the subclass
 * takes effect on the next message rather than never.
 *
 * A *positive* index addresses the window's cbWndExtra bytes. One past the
 * end is refused by name rather than answered with the 0 Windows returns:
 * this runtime's window model is partial, so a 0 from here means "no such
 * index" and "the runtime does not model this" at the same time, and the
 * guest cannot tell them apart. `sa2_window_metric` refuses for the same
 * reason two hundred lines up. */
int sa2_window_get_long(uint32_t hwnd, int32_t index, uint32_t *out) {
    const sa2_wnd_window *w = sa2_window_of(hwnd);
    if (!w) return SA2_WNDLONG_BADWINDOW;
    g_st.getlongs++;
    switch (index) {
        case SA2_GWL_WNDPROC:    *out = w->wndproc;   return SA2_WNDLONG_OK;
        case SA2_GWL_HINSTANCE:  *out = w->hinstance; return SA2_WNDLONG_OK;
        case SA2_GWL_HWNDPARENT: *out = w->parent;    return SA2_WNDLONG_OK;
        /* GWL_ID is the child-window identifier, which CreateWindowExW passes
         * in hMenu for a child. It is meaningless for a top-level window and
         * the record keeps whatever was passed, which is the honest answer. */
        case SA2_GWL_ID:         *out = w->menu;      return SA2_WNDLONG_OK;
        case SA2_GWL_STYLE:      *out = w->style;     return SA2_WNDLONG_OK;
        case SA2_GWL_EXSTYLE:    *out = w->exstyle;   return SA2_WNDLONG_OK;
        case SA2_GWL_USERDATA:   *out = w->userdata;  return SA2_WNDLONG_OK;
        default: break;
    }
    if (index >= 0 && (index & 3) == 0 && (uint32_t)index + 4u <= w->extra_bytes) {
        *out = w->extra[index / 4];
        return SA2_WNDLONG_OK;
    }
    g_st.long_index_refused++;
    return SA2_WNDLONG_BADINDEX;
}

int sa2_window_set_long(uint32_t hwnd, int32_t index, uint32_t value, uint32_t *prev) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (!w) return SA2_WNDLONG_BADWINDOW;
    uint32_t before = 0;
    const int rc = sa2_window_get_long(hwnd, index, &before);
    if (rc != SA2_WNDLONG_OK) return rc;   /* the read already counted the refusal */
    g_st.getlongs--;                       /* ... and this is a write, not a read */
    g_st.setlongs++;
    *prev = before;
    switch (index) {
        case SA2_GWL_WNDPROC:
            w->wndproc = value;
            if (value != before) { w->subclasses++; g_st.setlong_wndproc++; }
            return SA2_WNDLONG_OK;
        case SA2_GWL_HINSTANCE:  w->hinstance = value; return SA2_WNDLONG_OK;
        case SA2_GWL_HWNDPARENT: w->parent    = value; return SA2_WNDLONG_OK;
        case SA2_GWL_ID:         w->menu      = value; return SA2_WNDLONG_OK;
        case SA2_GWL_STYLE:      w->style     = value; return SA2_WNDLONG_OK;
        case SA2_GWL_EXSTYLE:    w->exstyle   = value; return SA2_WNDLONG_OK;
        case SA2_GWL_USERDATA:   w->userdata  = value; return SA2_WNDLONG_OK;
        default: break;
    }
    w->extra[index / 4] = value;           /* the read above proved the bound */
    return SA2_WNDLONG_OK;
}

/* ------------------------------------------------------- 4. WaitMessage
 *
 * **Why this terminates.** Each pass round the loop does exactly one of three
 * things, and each of the three is bounded:
 *
 *   * A message is in the queue: it returns. (The queue is non-empty, which
 *     is a state the guest's own pump then consumes.)
 *   * A timer is live: it blocks to that timer's due time. `sa2_sched_block`
 *     with a deadline either wakes at the deadline -- after which
 *     `timers_fire_due` puts WM_TIMER in the queue and the next pass returns
 *     -- or answers SA2_WAKE_NEVER, which is the scheduler saying the clock
 *     cannot be moved (its budget is spent), and that breaks out to a refusal.
 *   * No timer is live: it hands off once. `sa2_sched_yield` returns the
 *     number of other threads it ran. **Zero means nobody else was runnable**,
 *     and with no runnable thread and no deadline there is no agent left in
 *     the process that could post a message, so the wait can never end and it
 *     is refused by name on the spot.
 *
 * The remaining case -- other threads keep running and keep not posting -- is
 * the only one that could go round for ever, and it is bounded by
 * `g_wait_yields`. So every path leaves the loop after a finite number of
 * passes, and the ones that leave without a message leave through
 * SA2_WNDWAIT_NEVER, which the adapter turns into a trap naming WaitMessage.
 *
 * What a non-terminating version would look like, so the difference is
 * visible in a run: `waits` climbing with `waits_ready` flat, no trap, and the
 * poll count frozen -- WaitMessage does not poll, so the pump budget that
 * stops every other runaway loop in this file cannot see it. That is exactly
 * why this one has to stop itself. */
int sa2_window_wait(void) {
    g_st.waits++;
    if (g_qdepth) { g_st.waits_ready++; return SA2_WNDWAIT_READY; }
    if (timers_fire_due()) { g_st.waits_timer++; return SA2_WNDWAIT_READY; }

    for (uint32_t spin = 0; spin < g_wait_yields; spin++) {
        const uint64_t due = sa2_window_timer_next_due_us();
        if (due) {
            if (!sa2_sched_block) break;   /* no scheduler linked in: no clock to wait on */
            const int wake = sa2_sched_block(SA2_WAIT_SLEEP, 0, due, "WaitMessage");
            if (timers_fire_due() || g_qdepth) { g_st.waits_timer++; return SA2_WNDWAIT_READY; }
            /* The scheduler could neither run anybody nor move the clock. */
            if (wake == SA2_WAKE_NEVER) break;
            continue;
        }
        const int ran = sa2_sched_yield ? sa2_sched_yield("WaitMessage") : 0;
        if (g_qdepth) { g_st.waits_handoff++; return SA2_WNDWAIT_READY; }
        if (!ran) break;                   /* nobody else can run, and no deadline exists */
    }
    g_st.waits_refused++;
    return SA2_WNDWAIT_NEVER;
}

/* ------------------------------------------------------------- 5. input */

uint32_t sa2_window_key_state(uint32_t vkey, int async) {
    g_st.key_polls++;
    sa2_wnd_key *k = NULL;
    for (uint32_t i = 0; i < g_key_used; i++)
        if (g_keys[i].vkey == vkey) { k = &g_keys[i]; break; }
    if (!k && g_key_used < SA2_WND_KEYS_MAX) {
        k = &g_keys[g_key_used++];
        k->vkey = vkey;
    }
    if (k) { if (async) k->async_polls++; else k->sync_polls++; }
    g_st.keys_distinct = g_key_used;
    /* No key is down. This is not a fabricated answer: it is what a keyboard
     * reports for a key nobody is pressing, and design 3.5 gives feeding real
     * input from the host to S5. The vkey above is the S5 hand-off. */
    return 0u;
}

uint32_t sa2_window_key_count(void) { return g_key_used; }
const sa2_wnd_key *sa2_window_key_at(uint32_t i) {
    return i < g_key_used ? &g_keys[i] : NULL;
}

/* ------------------------------------ window state, answered from the record */

uint32_t sa2_window_enable(uint32_t hwnd, uint32_t enable, int *known) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (known) *known = w ? 1 : 0;
    if (!w) return 0;
    /* EnableWindow returns non-zero when the window was *previously
     * disabled*, which is the opposite of what it reads like. */
    const uint32_t was_disabled = w->enabled ? 0u : 1u;
    w->enabled = enable ? 1u : 0u;
    return was_disabled;
}

int sa2_window_move(uint32_t hwnd, int32_t x, int32_t y, int32_t w_, int32_t h_) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (!w) return 0;
    w->x = x; w->y = y; w->w = w_; w->h = h_;
    w->moves++;
    /* GetClientRect and GetWindowRect read these fields, so a MoveWindow that
     * recorded and did not move would make those two disagree with the guest's
     * own last instruction to the window system. */
    return 1;
}

int sa2_window_set_pos(uint32_t hwnd, int32_t x, int32_t y,
                       int32_t cx, int32_t cy, uint32_t flags) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (!w) return 0;
    if (!(flags & SA2_SWP_NOMOVE)) { w->x = x; w->y = y; }
    if (!(flags & SA2_SWP_NOSIZE)) { w->w = cx; w->h = cy; }
    w->poses++;
    /* Z-order, SWP_SHOWWINDOW and the rest are recorded by the caller and not
     * modelled: there is one window and nothing composites it. 0x00404cd7
     * passes flags 3, which is SWP_NOSIZE|SWP_NOMOVE -- a pure Z-order call --
     * so the two flags that are honoured are the two that would have moved a
     * window this runtime reports the position of. */
    return 1;
}

int sa2_window_set_title(uint32_t hwnd, const char *title) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (!w) return 0;
    snprintf(w->title, sizeof w->title, "%s", title ? title : "");
    return 1;
}

const char *sa2_window_title_of(uint32_t hwnd) {
    const sa2_wnd_window *w = sa2_window_of(hwnd);
    return w ? w->title : NULL;
}
const char *sa2_window_class_name_of(uint32_t hwnd) {
    const sa2_wnd_window *w = sa2_window_of(hwnd);
    return w ? w->class_name : NULL;
}
uint32_t sa2_window_parent_of(uint32_t hwnd) {
    const sa2_wnd_window *w = sa2_window_of(hwnd);
    return w ? w->parent : 0u;
}

/* GetWindow(hWnd, uCmd), over the flat creation-order table. A 0 from a
 * command this runtime models is a real answer -- there is no such relative --
 * and `known_cmd` is what separates that from a command it does not model. */
uint32_t sa2_window_related(uint32_t hwnd, uint32_t cmd, int *known_cmd) {
    if (known_cmd) *known_cmd = 1;
    uint32_t self = SA2_WND_MAX_WINDOWS;
    for (uint32_t i = 0; i < g_window_count; i++)
        if (g_windows[i].hwnd == hwnd) { self = i; break; }
    if (self == SA2_WND_MAX_WINDOWS) return 0;
    switch (cmd) {
        case 0:  /* GW_HWNDFIRST */
            return g_window_count ? g_windows[0].hwnd : 0u;
        case 1:  /* GW_HWNDLAST */
            return g_window_count ? g_windows[g_window_count - 1].hwnd : 0u;
        case 2:  /* GW_HWNDNEXT */
            return self + 1u < g_window_count ? g_windows[self + 1].hwnd : 0u;
        case 3:  /* GW_HWNDPREV */
            return self ? g_windows[self - 1].hwnd : 0u;
        case 4:  /* GW_OWNER */
            return g_windows[self].parent;
        case 5:  /* GW_CHILD */
            for (uint32_t i = 0; i < g_window_count; i++)
                if (g_windows[i].parent == hwnd) return g_windows[i].hwnd;
            return 0u;
        default:
            /* GW_ENABLEDPOPUP and anything later. There are no popups here, so
             * an answer would be a guess about a window set this runtime does
             * not have. */
            if (known_cmd) *known_cmd = 0;
            return 0u;
    }
}

/* BeginPaint / EndPaint. Nothing is drawn -- S5 owns drawing -- but the HDC is
 * a real identity and the PAINTSTRUCT is a real out-parameter, and the guest
 * at 0x00402800 takes BeginPaint's return straight to DrawIcon. The paint
 * rectangle is the whole client area, which is what a window with nothing
 * validated has. */
uint32_t sa2_window_begin_paint(uint32_t hwnd, int32_t *l, int32_t *t, int32_t *r, int32_t *b) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (l) *l = 0;
    if (t) *t = 0;
    if (r) *r = 0;
    if (b) *b = 0;
    if (!w) return 0;
    if (!w->hdc) w->hdc = sa2_window_token(SA2_WND_HDC_BASE, w->hinstance, 0);
    w->paints++;
    g_st.paints_begun++;
    if (r) *r = w->w;
    if (b) *b = w->h;
    return w->hdc;
}

int sa2_window_end_paint(uint32_t hwnd) {
    if (!sa2_window_of(hwnd)) return 0;
    g_st.paints_ended++;
    return 1;
}

/* GetSystemMenu. Windows returns NULL for bRevert, and the menu it returns
 * otherwise has the standard items. This one has none, which is why
 * EnableMenuItem answers -1 ("no such item") rather than a previous state it
 * would have had to invent. */
uint32_t sa2_window_system_menu(uint32_t hwnd, uint32_t revert) {
    sa2_wnd_window *w = sa2_window_mutable(hwnd);
    if (!w || revert) return 0;
    if (!w->hmenu_system) w->hmenu_system = sa2_window_token(SA2_WND_HMENU_BASE, w->hinstance, 0);
    return w->hmenu_system;
}

/* ------------------------------------------------- the fabricated values */

/* The classic 96-dpi system palette, as 0x00bbggrr. These are the Windows
 * Classic defaults, which is a documented scheme rather than a guess -- but
 * there is no theme and no display here, so every one of them goes through
 * `sa2_window_invent` and SA2_WND_STRICT=1 turns each into a refusal. The
 * guest reads index 5 (COLOR_WINDOW) at 0x004029b3 and hands it to
 * GDI32!CreateSolidBrush. */
int sa2_window_sys_color(uint32_t index, uint32_t *out) {
    static const uint32_t classic[] = {
        0x00c0c0c0u, /*  0 COLOR_SCROLLBAR        */ 0x00000000u, /*  1 BACKGROUND        */
        0x00800000u, /*  2 ACTIVECAPTION          */ 0x00808080u, /*  3 INACTIVECAPTION   */
        0x00c0c0c0u, /*  4 MENU                   */ 0x00ffffffu, /*  5 WINDOW            */
        0x00000000u, /*  6 WINDOWFRAME            */ 0x00000000u, /*  7 MENUTEXT          */
        0x00000000u, /*  8 WINDOWTEXT             */ 0x00ffffffu, /*  9 CAPTIONTEXT       */
        0x00c0c0c0u, /* 10 ACTIVEBORDER           */ 0x00c0c0c0u, /* 11 INACTIVEBORDER    */
        0x00808080u, /* 12 APPWORKSPACE           */ 0x00800000u, /* 13 HIGHLIGHT         */
        0x00ffffffu, /* 14 HIGHLIGHTTEXT          */ 0x00c0c0c0u, /* 15 BTNFACE           */
        0x00808080u, /* 16 BTNSHADOW              */ 0x00808080u, /* 17 GRAYTEXT          */
        0x00000000u, /* 18 BTNTEXT                */ 0x00c0c0c0u, /* 19 INACTIVECAPTIONTEXT */
        0x00ffffffu, /* 20 BTNHIGHLIGHT           */ 0x00000000u, /* 21 3DDKSHADOW        */
        0x00c0c0c0u, /* 22 3DLIGHT                */ 0x00000000u, /* 23 INFOTEXT          */
        0x00e1ffffu, /* 24 INFOBK                 */ 0x00000000u, /* 25 (unused)          */
        0x00000000u, /* 26 HOTLIGHT               */ 0x00800000u, /* 27 GRADIENTACTIVECAPTION */
        0x00808080u, /* 28 GRADIENTINACTIVECAPTION*/ 0x00c0c0c0u, /* 29 MENUHILIGHT       */
        0x00c0c0c0u, /* 30 MENUBAR                */
    };
    if (index >= sizeof classic / sizeof classic[0]) return 0;
    return sa2_window_invent("GetSysColor", classic[index], out);
}

/* GetDialogBaseUnits: LOWORD is the average character width and HIWORD the
 * height of the system font. 8 x 16 is MS Sans Serif 8 pt at 96 dpi, the
 * classic pair -- and there is no font here, so it is fabricated and
 * SA2_WND_STRICT=1 refuses it. The dialog layout at 0x00402870 multiplies
 * these, so a run under strict mode says at once whether the layout is
 * load-bearing or only computed. */
#define SA2_WND_BASE_X 8u
#define SA2_WND_BASE_Y 16u
int sa2_window_dialog_base_units(uint32_t *out) {
    return sa2_window_invent("GetDialogBaseUnits", (SA2_WND_BASE_Y << 16) | SA2_WND_BASE_X, out);
}

/* GetCursorPos. There is no mouse. The default is the centre of the main
 * window's client area in screen coordinates, because 0x00863038 subtracts the
 * window rectangle from it to get a client position -- a screen origin would
 * put the cursor outside the window, and "outside" is a state some guest code
 * treats specially. It is a *named placeholder* in the sense sa2_imports.c
 * uses for the virtual epoch: SA2_WND_CURSOR moves it, and a run whose
 * behaviour moves with it is a run that depends on it. */
int sa2_window_cursor_pos(int32_t *x, int32_t *y) {
    int32_t px = g_cursor_x, py = g_cursor_y;
    if (!g_cursor_set) {
        const sa2_wnd_window *w = sa2_window_count() ? sa2_window_at(0) : NULL;
        if (w) { px = w->x + w->w / 2; py = w->y + w->h / 2; }
        else   { px = (int32_t)(g_screen_w / 2u); py = (int32_t)(g_screen_h / 2u); }
    }
    uint32_t vx = 0, vy = 0;
    if (!sa2_window_invent("GetCursorPos.x", (uint32_t)px, &vx)) return 0;
    if (!sa2_window_invent("GetCursorPos.y", (uint32_t)py, &vy)) return 0;
    if (x) *x = (int32_t)vx;
    if (y) *y = (int32_t)vy;
    return 1;
}

/* DrawTextW's extent. 0x00402dde passes format 0xC50, which carries
 * DT_CALCRECT, and 0x00402de4 reads `rect.bottom - rect.top` straight back --
 * so this is an out-parameter the guest consumes, not a decoration. There is
 * no font, so the extent comes from the same base units the dialog layout
 * already uses, which at least keeps the two fabrications consistent with each
 * other instead of independently wrong. */
int sa2_window_text_extent(uint32_t chars, uint32_t lines, int32_t *w, int32_t *h) {
    if (!lines) lines = 1u;
    uint32_t vw = 0, vh = 0;
    if (!sa2_window_invent("DrawTextW.cx", chars * SA2_WND_BASE_X, &vw)) return 0;
    if (!sa2_window_invent("DrawTextW.cy", lines * SA2_WND_BASE_Y, &vh)) return 0;
    if (w) *w = (int32_t)vw;
    if (h) *h = (int32_t)vh;
    return 1;
}
