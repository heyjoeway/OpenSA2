/* The window the guest thinks it has, and the message pump the host drives.
 *
 * Design 3.5 asks for "presentation stubs as no-ops that record their calls";
 * design 5 S4 asks for "window creation stubbed, message pump virtualised,
 * main loop ticks under host control". This file is those three sentences.
 *
 * What the guest does with a window
 * ---------------------------------
 * `FUN_004019D0`, called from WinMain at 0x00401d40, fills a WNDCLASSW on its
 * own stack, registers it, creates one window, shows it and returns the HWND.
 * WinMain then hands that HWND to `FUN_004010D0` and, if that succeeds, runs
 * its main loop at 0x00401da4:
 *
 *     00401da4  PeekMessageW(&msg, 0, 0, 0, PM_NOREMOVE)
 *     00401db5  je 00401de0                 ; no message -> one game tick
 *     00401dc2  GetMessageW(&msg, 0, 0, 0)
 *     00401dc6  je 00401e07                 ; WM_QUIT   -> the guest's shutdown
 *     00401dcd  TranslateMessage(&msg)
 *     00401dd8  DispatchMessageW(&msg)
 *     00401dde  jmp 00401da4
 *
 * So the loop is driven entirely by what PeekMessageW answers, and the guest
 * has exactly one way to leave it that is not a crash: GetMessageW returning
 * FALSE, which on Windows means WM_QUIT was pulled from the queue.
 *
 * Why the handles look the way they do
 * ------------------------------------
 * A window handle is a token the runtime invents. It is invented with four
 * properties rather than picked:
 *
 *   * **Non-zero.** The guest tests the CreateWindowExW result and puts up
 *     "Window Creation Error." on zero, so zero is a value with a meaning.
 *   * **Outside the guest address space** (`SA2_GUEST_END` is 0x04000000), so
 *     a guest that dereferences a handle faults in `sa2_peek` with the handle
 *     in the trap, instead of silently reading whatever is at a fabricated
 *     guest pointer. This is the same rule `sa2_imports.c` applies to kernel
 *     handles, one region along.
 *   * **Typed by its high half**, so a diagnostic can say "that is an HICON"
 *     from the value alone, without a table lookup.
 *   * **Deterministic**: the n-th window of a run always gets the same token,
 *     because a replay that changed its handles would change any guest state
 *     derived from them.
 *
 * What is recorded, and why all of it
 * -----------------------------------
 * Every class registered, every window created and every windowing call is
 * kept with its arguments. Those arguments are the only statement anyone has
 * of what this game asks a window system for -- class name, style, extents,
 * the window procedure's address -- and they are the input to S5, which has to
 * build a real surface. They cost a fixed-size array to keep and cannot be
 * recovered once the run ends.
 *
 * How a guest that wants to block forever is stopped
 * --------------------------------------------------
 * Two structural facts, neither of them a timeout:
 *
 *   1. **`GetMessageW` never waits.** On Windows it blocks until a message
 *      arrives. Here there is no other thread, no input device and no window
 *      system, so nothing can ever arrive while it waits: blocking would be a
 *      hang with no exit. It is therefore only ever legal directly after a
 *      PeekMessageW that reported a message, and a call with an empty queue
 *      raises a trap that says so.
 *   2. **The host owns an iteration budget.** `sa2_window_pump_limit` is the
 *      number of times PeekMessageW may answer "no messages" before the
 *      runtime posts WM_QUIT. The guest then leaves its loop through its own
 *      code -- GetMessageW returns FALSE at 0x00401dc6 and it jumps to its own
 *      shutdown at 0x00401e07 -- rather than being stopped by the runtime.
 *      The host decides how long the game runs; the game decides how to stop.
 *
 * The queue is empty unless the host puts something in it. That is the honest
 * first implementation and it is also what a headless host with no input
 * devices actually has. `sa2_window_post` and `SA2_PUMP_INJECT` are how the
 * host says otherwise, and the injection path is what keeps "PeekMessageW
 * returns 0" from being a check that cannot fail.
 *
 * The rest of USER32, and the five calls a recording no-op would lie about
 * ---------------------------------------------------------------------------
 * The image imports 65 USER32 names. Design 3.5 puts the window and message
 * pump in group (b), presentation, and S4's work list says "presentation stubs
 * as no-ops that record calls" -- so the default for the rest of the surface
 * is a recording no-op success, `sa2_window_note_noop`. Five of them are not
 * that, because for each one a no-op success is a statement the guest acts on
 * and the statement is false:
 *
 *  1. **`SendMessageW` is synchronous control flow.** On Windows it runs the
 *     target window's procedure and returns what the procedure returned; a
 *     no-op success drops a call the guest believes has completed. It goes
 *     down the same `call_wndproc` path `DispatchMessageW` uses, and
 *     `sa2_window_send_enter` bounds the nesting: a window procedure that
 *     sends to itself would otherwise recurse until the guest stack runs out
 *     several thousand frames from the site that caused it.
 *
 *  2. **`GetWindowLongW` / `SetWindowLongW` are per-window state**, and this
 *     file already holds the record. Two of the indices are load-bearing in
 *     this image: 0x00401679 reads `GWL_STYLE` and branches on WS_POPUP
 *     (`and esi,80000000h; je`), so a fabricated 0 takes the other path; and
 *     0x00403e4a writes `GWL_WNDPROC` on the item `GetDlgItem(hDlg, 0x3E8)`
 *     returns, pointing it at 0x00402800 -- **that is window subclassing**,
 *     and a `SetWindowLongW` that answered and did nothing would leave every
 *     later dispatch going to the original procedure forever. `GWL_WNDPROC`
 *     is therefore implemented, not refused: it writes the same `wndproc`
 *     field `DispatchMessageW` and `SendMessageW` read, so the swap takes
 *     effect on the next message. The one index class this file refuses is a
 *     *positive* one past the window's own `cbWndExtra`, and it refuses by
 *     name rather than returning the 0 Windows returns, for the reason
 *     `sa2_window_metric` already gives: a plausible number nobody knows is
 *     wrong is worse than a run that stops.
 *
 *  3. **`SetTimer` / `KillTimer`.** A timer that is accepted and never fires
 *     is a hang, because the guest waits for the `WM_TIMER` it was promised.
 *     These are driven from the same virtual clock everything else here uses
 *     (`now_ms`, `sa2_virtual_us`), so a replay's timers fire at the same
 *     virtual instants. Two details are the real semantics rather than
 *     convenience: `WM_TIMER` is **synthesised when the queue is otherwise
 *     empty**, which is what makes it the low-priority message Windows
 *     documents; and **at most one is outstanding per timer**, with the next
 *     due time re-anchored past `now`, so a clock that jumps forward a second
 *     does not flood the queue with a thousand ticks that a real timer would
 *     have coalesced into one.
 *
 *  4. **`WaitMessage` blocks.** Returning at once turns the guest's wait into
 *     a spin, and 0x00404d91 is exactly that loop: PeekMessageW, and if it
 *     answered nothing, WaitMessage. So it is a scheduler hand-off, the same
 *     one `Sleep` and `WaitForSingleObject` use -- and it **terminates**. A
 *     live timer is a deadline a message can arrive at, so the wait blocks to
 *     it; with no timer it hands off to whatever else is runnable, and when
 *     nothing else is runnable and no deadline exists, nothing in this process
 *     can ever post a message and `sa2_window_wait` says so by name instead of
 *     spinning until the poll budget notices.
 *
 *  5. **`GetAsyncKeyState` / `GetKeyState` are input**, and design 3.5 gives
 *     input to S5 ("xinput/dinput stubs fed from the host"). For S4 the honest
 *     answer is that no key is down, which is 0 -- and 0 is also what a real
 *     keyboard reports for a key nobody is pressing, so this one invents
 *     nothing. What it does do is **record the virtual-key code**, per key,
 *     because the set of keys this game polls is the S5 hand-off and it is not
 *     recoverable afterwards. 0x00863447 and 0x0086349e already show two of
 *     them: VK_MENU (0x12) and VK_CONTROL (0x11).
 *
 * Invented answers, and the switch that proves whether they matter
 * ----------------------------------------------------------------
 * `sa2_dsound.h` rule 3: every fabricated answer is labelled, and a run can be
 * made to refuse them all so the guest's dependence on them is shown rather
 * than assumed. `sa2_window_invent` is that label here. It counts each
 * fabricated *value* and, under `SA2_WND_STRICT=1`, refuses it so the caller
 * traps by name. Four things go through it, and nothing else does:
 *
 *   * `GetSysColor` -- the classic 96-dpi system palette. There is no theme.
 *   * `GetDialogBaseUnits` -- 8 x 16, the classic MS Sans Serif 8 pt metrics.
 *     There is no font.
 *   * `GetCursorPos` -- there is no mouse. The default is the centre of the
 *     main window's client area in screen coordinates, so a guest that
 *     converts it (0x00863038 does, subtracting the window rect) gets a point
 *     inside its own window rather than one off the top-left corner.
 *     `SA2_WND_CURSOR=x,y` moves it, which is how a run shows the guest does
 *     or does not depend on where it is.
 *   * `DrawTextW` with DT_CALCRECT -- 0x00402dde passes format 0xC50, and
 *     0x00402de4 immediately reads `rect.bottom - rect.top` back out. There is
 *     no font, so the extent is derived from the same base units, and it is
 *     an out-parameter that must be written on every success path.
 *
 * A *handle* this file mints is not on that list. An HDC or an HMENU is an
 * invented identity, the same kind of thing `SA2_WND_HICON_BASE` has been
 * since the first slice, not an invented number the guest does arithmetic on.
 */
#ifndef SA2_RUNTIME_WINDOW_H
#define SA2_RUNTIME_WINDOW_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Token regions. Every one of these is above SA2_GUEST_END and below the
 * kernel-handle region SA2_HANDLE_BASE (0x7f000000) that `sa2_imports.c` owns,
 * so the kinds never collide and a stray value is identifiable by inspection.
 *
 * `sa2_window_token_kind` reads the kind out of the top twelve bits, so a kind
 * base is a multiple of 0x00100000 and the whole 0x7e000000 band is one shared
 * numbering that four files draw from. **This file owns six slots of it, and
 * they are not contiguous**, which is the point of writing them all down:
 *
 *   0x7e000000  SA2_WND_HWND_BASE       this file
 *   0x7e100000  SA2_WND_HICON_BASE      this file
 *   0x7e200000  SA2_WND_HCURSOR_BASE    this file
 *   0x7e300000  SA2_WND_HBRUSH_BASE     this file
 *   0x7e400000  SA2_D3D9_HMONITOR_BASE  runtime/sa2_d3d9.h -- NOT this file
 *   0x7e500000  SA2_WND_HMENU_BASE      this file
 *   0x7e600000  SA2_WND_HDC_BASE        this file
 *   0x7e700000  SA2_WND_TOKEN_END       free; nothing here mints above it
 *   0x7e800000  SA2_GDI_HFONT_BASE      runtime/sa2_gdi.h  -- NOT this file
 *   0x7f000000  SA2_HANDLE_BASE         runtime/sa2_imports.c kernel objects
 *
 * The HDC is at 0x7e600000 and not at the 0x7e400000 that came next in order,
 * because 0x7e400000 was already the D3D9 monitor handle -- so an HMONITOR and
 * the first HDC would have been the same 32-bit value and
 * `sa2_window_token_kind` would have answered "HDC" for a monitor. A kind
 * decided by the value alone only works while the values are distinct, and the
 * band is now full enough that the table above is the only way to check. */
#define SA2_WND_HWND_BASE     0x7e000000u
#define SA2_WND_HICON_BASE    0x7e100000u
#define SA2_WND_HCURSOR_BASE  0x7e200000u
#define SA2_WND_HBRUSH_BASE   0x7e300000u
/* GetSystemMenu's menu and BeginPaint's device context. Both are identities
 * rather than answers: the menu has no items and nothing is drawn through the
 * device context, and each is minted once per window so a run's handles are a
 * function of the program and not of how many times it painted. */
#define SA2_WND_HMENU_BASE    0x7e500000u
#define SA2_WND_HDC_BASE      0x7e600000u
/* The first address this file never mints, for the GDI unit to sit above.
 * It moved with the HDC, from 0x7e600000 to 0x7e700000, so a file that says
 * "above SA2_WND_TOKEN_END" by name still says the true thing. */
#define SA2_WND_TOKEN_END     0x7e700000u
#define SA2_WND_TOKEN_STRIDE  0x10u
/* RegisterClass returns an ATOM. Windows allocates class atoms out of the
 * 0xC000..0xFFFF range, so that is where these come from: the shape is the
 * documented one rather than an invented one. */
#define SA2_WND_ATOM_FIRST    0xC000u

#define SA2_WND_MAX_CLASSES   8u
#define SA2_WND_MAX_WINDOWS   8u
#define SA2_WND_MAX_TOKENS    32u
#define SA2_WND_QUEUE_MAX     64u
#define SA2_WND_LOG_MAX       512u
#define SA2_WND_NAME_MAX      64u
/* cbWndExtra, in dwords. The game's own class asks for none; a dialog asks
 * for DLGWINDOWEXTRA (30 bytes), so eight dwords covers every window this
 * image can create and an index past a window's own cbWndExtra is refused by
 * name either way. */
#define SA2_WND_EXTRA_MAX     8u
#define SA2_WND_TIMERS_MAX    16u
#define SA2_WND_KEYS_MAX      32u

/* Windows messages this file names. */
#define SA2_WM_DESTROY        0x0002u
#define SA2_WM_SIZE           0x0005u
#define SA2_WM_ACTIVATE       0x0006u
#define SA2_WM_PAINT          0x000fu
#define SA2_WM_CLOSE          0x0010u
#define SA2_WM_QUIT           0x0012u
#define SA2_WM_KEYDOWN        0x0100u
#define SA2_WM_TIMER          0x0113u
#define SA2_PM_NOREMOVE       0x0000u
#define SA2_PM_REMOVE         0x0001u

/* GetWindowLongW / SetWindowLongW indices, as signed values. They arrive in
 * the argument array as 0xfffffffc and friends; the adapter casts once and
 * everything below is signed, so a positive window-extra offset and a
 * negative well-known index can never be confused. */
#define SA2_GWL_WNDPROC       (-4)
#define SA2_GWL_HINSTANCE     (-6)
#define SA2_GWL_HWNDPARENT    (-8)
#define SA2_GWL_ID            (-12)
#define SA2_GWL_STYLE         (-16)
#define SA2_GWL_EXSTYLE       (-20)
#define SA2_GWL_USERDATA      (-21)

/* SetWindowPos flags this file honours; every other bit is recorded only. */
#define SA2_SWP_NOSIZE        0x0001u
#define SA2_SWP_NOMOVE        0x0002u
/* RedrawWindow flags that make it synchronous on Windows. */
#define SA2_RDW_UPDATENOW     0x0100u
#define SA2_RDW_ERASENOW      0x0200u
/* DrawTextW format bits this file reads. */
#define SA2_DT_SINGLELINE     0x0020u
#define SA2_DT_CALCRECT       0x0400u

typedef struct {
    uint32_t atom;
    uint32_t style;
    uint32_t wndproc;
    uint32_t cls_extra, wnd_extra;
    uint32_t hinstance, hicon, hcursor, hbrush;
    uint32_t menu_name_va, class_name_va;
    char     name[SA2_WND_NAME_MAX];
} sa2_wnd_class;

typedef struct {
    uint32_t hwnd;
    uint32_t exstyle, style;
    int32_t  x, y, w, h;
    uint32_t parent, menu, hinstance, param;
    uint32_t wndproc;          /* the class's, until SetWindowLongW(GWL_WNDPROC) */
    uint32_t class_wndproc;    /* what the class registered, kept for the record */
    uint32_t visible;          /* ShowWindow's last nCmdShow, 0 until shown */
    uint32_t enabled;          /* EnableWindow; 1 from creation, as Windows has it */
    uint32_t userdata;         /* GWL_USERDATA. 0x00403c3b stores an HICON here */
    uint32_t hdc;              /* BeginPaint's token, minted once */
    uint32_t hmenu_system;     /* GetSystemMenu's token, minted once */
    uint32_t extra_bytes;      /* the class's cbWndExtra, the bound on a positive index */
    uint32_t extra[SA2_WND_EXTRA_MAX];
    uint32_t shows, focuses, foregrounds;
    uint32_t sends;            /* SendMessageW calls that reached this window's procedure */
    uint32_t moves, poses;     /* MoveWindow / SetWindowPos calls that changed the record */
    uint32_t subclasses;       /* SetWindowLongW(GWL_WNDPROC) writes that changed it */
    uint32_t paints;           /* BeginPaint calls */
    char     class_name[SA2_WND_NAME_MAX];
    char     title[SA2_WND_NAME_MAX];
} sa2_wnd_window;

typedef struct {
    uint32_t hwnd, msg, wparam, lparam, time;
    int32_t  pt_x, pt_y;
} sa2_wnd_msg;

/* One windowing call, in order, with the arguments that carry meaning. This is
 * the S5 hand-off: what the guest asked a window system for. */
typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t a[4];
    char     what[40];
} sa2_wnd_event;

typedef struct {
    uint32_t classes, windows, icons, cursors;
    uint32_t polls;            /* PeekMessageW calls: one per main-loop iteration */
    uint32_t polls_empty;      /* ... that answered "no messages" */
    uint32_t polls_message;    /* ... that answered with one */
    uint32_t gets, translates, dispatches, wndproc_calls, defwndproc_calls;
    uint32_t posted, delivered, dropped;
    uint32_t quit_posted, quit_code, quit_by_host, quit_by_guest;
    /* A GetMessageW that pulled WM_QUIT off the queue and answered FALSE. It
     * is the only exit from the guest's loop that is not a trap, so a run that
     * claims the guest left its loop has to show one of these. */
    uint32_t quit_delivered;
    uint32_t injected;
    uint32_t queue_depth, queue_peak;
    uint32_t blocking_get_refused;
    uint32_t cursor_count;
    int32_t  cursor_show;

    /* The rest of the USER32 surface. Every one of these counts something
     * that happened rather than something that was asked for, which is the
     * rule `sa2_threads.h` states and the reason `noops` and `invented` are
     * two numbers and not one: a recording no-op changed nothing, and a
     * fabricated value did. */
    uint32_t noops;            /* recording no-op successes */
    uint32_t invented;         /* fabricated values handed to the guest */
    uint32_t invented_refused; /* ... refused instead, under SA2_WND_STRICT=1 */
    uint32_t menus, dcs;       /* HMENU and HDC tokens minted */

    uint32_t sends;            /* SendMessageW calls */
    uint32_t sends_dispatched; /* ... that reached a guest window procedure */
    uint32_t sends_default;    /* ... to a handle this runtime does not own */
    uint32_t send_depth_peak;
    uint32_t send_refused;     /* ... refused for depth */

    uint32_t getlongs, setlongs, setlong_wndproc, long_index_refused;

    uint32_t timers_set, timers_killed, timers_live, timers_refused;
    uint32_t timer_fires;      /* WM_TIMER messages synthesised */
    uint32_t timer_coalesced;  /* ticks a real timer would have merged into one */

    uint32_t waits;            /* WaitMessage calls */
    uint32_t waits_ready;      /* ... that found a message already queued */
    uint32_t waits_handoff;    /* ... that ran another guest thread and then found one */
    uint32_t waits_timer;      /* ... that ended at a timer's deadline */
    uint32_t waits_refused;    /* ... that could never have ended */

    uint32_t key_polls;        /* GetAsyncKeyState + GetKeyState calls */
    uint32_t keys_distinct;    /* distinct virtual-key codes polled */

    uint32_t paints_begun, paints_ended;
} sa2_wnd_stats;

/* One virtual-key code the guest polled, and how it asked. This is the S5
 * hand-off for input: the set of keys is not recoverable once the run ends,
 * and `GetAsyncKeyState` and `GetKeyState` are kept apart because the first
 * asks about the physical key now and the second about the key at the message
 * being processed, and S5 has to feed them differently. */
typedef struct { uint32_t vkey, async_polls, sync_polls; } sa2_wnd_key;

/* One live SetTimer timer. `next_due_us` is virtual microseconds. */
typedef struct {
    uint32_t hwnd, id, elapse_ms, proc;
    uint64_t next_due_us;
    uint32_t fires;
    uint8_t  used;
} sa2_wnd_timer;

/* Configure from the environment:
 *   SA2_PUMP_POLLS   how many empty polls before the host posts WM_QUIT
 *                    (default SA2_WND_DEFAULT_POLLS; 0 means "never", which is
 *                    only safe under sa2_call's wall-clock alarm)
 *   SA2_PUMP_INJECT  "poll:msg[:wparam[:lparam]]" items, comma separated, each
 *                    posted to the main window just before that poll answers
 *   SA2_WND_SCREEN   "WxH" for the virtual display GetSystemMetrics reports
 *   SA2_WND_STRICT   1 turns every fabricated value (see the header comment)
 *                    into a refusal, so a run proves whether the guest depends
 *                    on it instead of asserting that it does not
 *   SA2_WND_CURSOR   "x,y" for GetCursorPos, in screen coordinates; the
 *                    default is the centre of the main window's client area
 *   SA2_WND_SEND_DEPTH   how deep SendMessageW may nest before it refuses
 *   SA2_WND_WAIT_YIELDS  how many hand-offs WaitMessage may make that produce
 *                        no message before it declares the wait endless
 */
#define SA2_WND_DEFAULT_POLLS 600u
#define SA2_WND_DEFAULT_SEND_DEPTH  8u
#define SA2_WND_DEFAULT_WAIT_YIELDS 64u
void sa2_window_config_from_env(void);
void sa2_window_reset(void);

/* The host's own controls, so a driver does not have to go through getenv. */
void     sa2_window_set_poll_limit(uint32_t polls);
uint32_t sa2_window_poll_limit(void);
int      sa2_window_post(uint32_t hwnd, uint32_t msg, uint32_t wparam, uint32_t lparam);

/* Class and window registry, driven by the import stubs. */
uint32_t sa2_window_register_class(const sa2_wnd_class *c);   /* the atom, or 0 */
uint32_t sa2_window_create(const sa2_wnd_window *w);          /* the HWND, or 0 */
const sa2_wnd_class  *sa2_window_class_at(uint32_t i);
const sa2_wnd_window *sa2_window_at(uint32_t i);
const sa2_wnd_window *sa2_window_of(uint32_t hwnd);
sa2_wnd_window       *sa2_window_mutable(uint32_t hwnd);
uint32_t sa2_window_class_count(void);
uint32_t sa2_window_count(void);
uint32_t sa2_window_main(void);          /* the first window created, or 0 */

/* Tokens. `kind_base` is one of the SA2_WND_*_BASE values above. */
uint32_t    sa2_window_token(uint32_t kind_base, uint32_t hinstance, uint32_t resource);
const char *sa2_window_token_kind(uint32_t token);   /* "HWND", "HICON", ... or NULL */

/* The pump. `poll` is what PeekMessageW calls: it advances the host's
 * iteration count, runs any scheduled injection, posts WM_QUIT when the budget
 * is spent, and reports whether a message is waiting. */
int  sa2_window_poll(sa2_wnd_msg *out);              /* 1 when a message is waiting */
int  sa2_window_get(sa2_wnd_msg *out);               /* 0 on WM_QUIT, -1 when empty */
void sa2_window_note_translate(void);
/* The pump dispatched a message. `is_default` means it went nowhere the guest
 * owns -- an unknown window handle -- rather than to a window procedure. */
void sa2_window_note_dispatch(uint32_t is_default);
/* A guest window procedure *returned*. Kept apart from the dispatch count
 * because the two answer different questions and a break found that out: with
 * DispatchMessageW counting the guest's procedure before calling it, a runtime
 * that skipped the call entirely produced counts identical to a correct run in
 * every field. This one cannot be incremented without the guest having run. */
void sa2_window_note_wndproc_returned(void);
void sa2_window_post_quit(uint32_t code, int by_host);

/* Where the polls came from, counted exactly rather than read back out of the
 * bounded import log.
 *
 * The question this answers is the one the previous slice could not: an
 * iteration count alone cannot tell a loop driven from a synthetic mid-function
 * entry apart from the same loop reached by `WinMain` running its own path,
 * because both execute the same bytes at 0x00401da4. The *return address*
 * PeekMessageW sees can, and the import log that used to hold it is capped at
 * 4096 calls -- so a run long enough to matter would have lost it. */
#define SA2_WND_CALLERS_MAX 8u
typedef struct { uint32_t caller, polls; } sa2_wnd_caller;
void                  sa2_window_note_poll_caller(uint32_t caller);
uint32_t              sa2_window_poll_caller_count(void);
const sa2_wnd_caller *sa2_window_poll_caller_at(uint32_t i);
uint32_t              sa2_window_polls_from(uint32_t caller);

void sa2_window_stats(sa2_wnd_stats *out);
void sa2_window_log(const char *what, uint32_t caller,
                    uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3);
uint32_t              sa2_window_log_count(void);
const sa2_wnd_event  *sa2_window_log_at(uint32_t i);

/* GetSystemMetrics. Returns 1 and the value for an index with a defensible
 * answer, 0 for one that has none -- the caller then refuses by name rather
 * than inventing a number a later capture would contradict. */
int sa2_window_metric(uint32_t index, uint32_t *out);
uint32_t sa2_window_screen_w(void);
uint32_t sa2_window_screen_h(void);

/* --------------------------------------------- the rest of the surface
 *
 * Everything below takes and returns plain host values. Guest memory is read
 * and written in the adapters in `sa2_imports.c`, which is where the guest's
 * structure layouts belong; this module computes what goes into them. The one
 * exception is SendMessageW, whose adapter has to call lifted code, because
 * `call_wndproc` already crosses that line for DispatchMessageW.
 */

/* A recording no-op success: logged, counted, and nothing in the model moves.
 * This is the S4 default for a presentation call, and the counter is what
 * separates "the runtime did nothing on purpose" from "the runtime did
 * nothing because a stub was missing". */
void sa2_window_note_noop(const char *what, uint32_t caller,
                          uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3);

/* A value this runtime made up. Returns 1 and writes `value`; returns 0 under
 * SA2_WND_STRICT=1, and the caller then refuses by name. `what` is for the
 * log, so a reader can tell which fabricated value a run leaned on. */
int sa2_window_invent(const char *what, uint32_t value, uint32_t *out);
int sa2_window_strict(void);

/* 1. SendMessageW. The depth guard; the call itself is the adapter's, because
 * only it can reach lifted code. `sa2_window_send_enter` returns 0 when the
 * nesting bound is reached and the caller must refuse rather than recurse. */
int      sa2_window_send_enter(uint32_t hwnd, uint32_t msg);
void     sa2_window_send_leave(void);
uint32_t sa2_window_send_depth(void);
uint32_t sa2_window_send_depth_limit(void);
/* A send to a handle this runtime does not own: counted, never dispatched. */
void     sa2_window_note_send_default(uint32_t hwnd, uint32_t msg);

/* 2. GetWindowLongW / SetWindowLongW. */
enum {
    SA2_WNDLONG_OK        =  1,
    SA2_WNDLONG_BADWINDOW =  0,   /* the caller returns 0 and ERROR_INVALID_WINDOW_HANDLE */
    SA2_WNDLONG_BADINDEX  = -1,   /* the caller refuses by name */
};
int sa2_window_get_long(uint32_t hwnd, int32_t index, uint32_t *out);
int sa2_window_set_long(uint32_t hwnd, int32_t index, uint32_t value, uint32_t *prev);

/* 3. SetTimer / KillTimer, on the virtual clock. `sa2_window_set_timer`
 * returns the timer id, or 0 when the table is full -- which is the answer
 * Windows documents for "the timer could not be created" and which the guest
 * is entitled to see. */
uint32_t sa2_window_set_timer(uint32_t hwnd, uint32_t id, uint32_t elapse_ms, uint32_t proc);
int      sa2_window_kill_timer(uint32_t hwnd, uint32_t id);      /* 1 on success */
uint32_t sa2_window_timer_count(void);
const sa2_wnd_timer *sa2_window_timer_at(uint32_t i);
uint64_t sa2_window_timer_next_due_us(void);                     /* 0 when none is live */

/* 4. WaitMessage. */
enum {
    SA2_WNDWAIT_NEVER = 0,   /* no message can ever arrive; the caller refuses by name */
    SA2_WNDWAIT_READY = 1,   /* a message is waiting; the caller returns TRUE */
};
int sa2_window_wait(void);

/* 5. Input. Always 0 -- no key is down, which is what a real keyboard reports
 * for a key nobody is pressing -- and always recorded with its vkey. */
uint32_t sa2_window_key_state(uint32_t vkey, int async);
uint32_t sa2_window_key_count(void);
const sa2_wnd_key *sa2_window_key_at(uint32_t i);

/* Window state the no-op group still has to answer truthfully. */
uint32_t    sa2_window_enable(uint32_t hwnd, uint32_t enable, int *known);
int         sa2_window_move(uint32_t hwnd, int32_t x, int32_t y, int32_t w, int32_t h);
int         sa2_window_set_pos(uint32_t hwnd, int32_t x, int32_t y,
                               int32_t cx, int32_t cy, uint32_t flags);
int         sa2_window_set_title(uint32_t hwnd, const char *title);
const char *sa2_window_title_of(uint32_t hwnd);        /* NULL for an unknown handle */
const char *sa2_window_class_name_of(uint32_t hwnd);
uint32_t    sa2_window_parent_of(uint32_t hwnd);
/* GetWindow. `known_cmd` is 0 for a uCmd this runtime does not model, and the
 * caller then refuses by name; a modelled command answering 0 means "there is
 * no such window", which is a real answer. */
uint32_t    sa2_window_related(uint32_t hwnd, uint32_t cmd, int *known_cmd);
/* BeginPaint: the HDC, and the paint rectangle the adapter puts in the
 * PAINTSTRUCT. 0 for a handle this runtime does not own. */
uint32_t    sa2_window_begin_paint(uint32_t hwnd, int32_t *l, int32_t *t, int32_t *r, int32_t *b);
int         sa2_window_end_paint(uint32_t hwnd);
/* GetSystemMenu. 0 for bRevert, which is what Windows returns, and 0 for a
 * handle this runtime does not own. */
uint32_t    sa2_window_system_menu(uint32_t hwnd, uint32_t revert);
/* The fabricated values, each through `sa2_window_invent`. Every one returns
 * 0 under SA2_WND_STRICT=1 and the caller refuses by name. */
int         sa2_window_sys_color(uint32_t index, uint32_t *out);
int         sa2_window_dialog_base_units(uint32_t *out);
int         sa2_window_cursor_pos(int32_t *x, int32_t *y);
/* DrawTextW. `chars` is the text length in characters and `lines` the line
 * count the caller counted; the extent comes back in `w`/`h`, and the return
 * is the text height Windows returns. */
int         sa2_window_text_extent(uint32_t chars, uint32_t lines, int32_t *w, int32_t *h);

#ifdef __cplusplus
}
#endif
#endif
