/* The GDI the guest thinks it has: nine recording no-ops with a memory.
 *
 * Design 3.5 classifies `gdi32` in group (b), presentation, and design 5's S4
 * work list asks for "presentation stubs as no-ops that record calls". This
 * file is that sentence for the nine GDI32 imports this image has. Nothing
 * here draws, measures a glyph, or opens a device: S5 owns anything that puts
 * a pixel anywhere. What this owns is the *bookkeeping* -- which device
 * contexts and objects exist, what is selected into what, which colours the
 * guest set -- so that each of the nine can answer the way Windows answers
 * without any of them inventing a number.
 *
 * What the guest does with GDI
 * ----------------------------
 * All nine sites sit under one path, and the path is a dialog rather than the
 * renderer. `FUN_00401810` (the window procedure) calls 0x004015f0, which
 * calls 0x004026e0, which calls the two functions that hold every GDI call in
 * the image:
 *
 *   0x00402870      measures the dialog: CreateDCW(L"DISPLAY") at 0x00402cbb,
 *                   SystemParametersInfoW(SPI_GETNONCLIENTMETRICS) at
 *                   0x00402ce3, CreateFontIndirectW on the message font at
 *                   0x00402cf1, SelectObject at 0x00402cff, then four
 *                   GetTextExtentPoint32W calls (0x00402d3d, 0x00402dfb,
 *                   0x0040328d, 0x0040344b), SelectObject back and DeleteDC at
 *                   0x00403595/0x0040359c.
 *   0x00403bd0      the DialogFunc the same code hands to
 *                   CreateDialogIndirectParamW at 0x00404cba: CreateSolidBrush
 *                   from a GetSysColor value at 0x00403fa5, FillRect,
 *                   DeleteObject at 0x00403fbf, and SetBkColor / SetTextColor
 *                   at 0x00404402 / 0x00404429 in its WM_CTLCOLOR handling.
 *
 * So the one number the guest *uses* is GetTextExtentPoint32W's width: at
 * 0x00402d43 it reads SIZE.cx straight back off its own stack, adds 20 and
 * keeps the larger of that and a running maximum. That is the dialog's
 * computed width, and it is the only place a value this file makes up can
 * reach guest state. Everything else is a handle the guest stores and hands
 * back, or a return value it discards.
 *
 * Why the handles look the way they do
 * ------------------------------------
 * The same four properties `sa2_window.c` invents HWND, HICON, HCURSOR and
 * HBRUSH with, for the same reasons, and they are repeated here because a
 * token scheme that is only documented in its neighbour is a token scheme
 * waiting to be reinvented differently:
 *
 *   * **Non-zero**, because zero is CreateDC's and CreateFontIndirect's
 *     failure answer and therefore a value with a meaning.
 *   * **Outside the guest address space** (`SA2_GUEST_END` is 0x20000000), so
 *     a guest that dereferences one faults in `sa2_peek` with the handle in
 *     the trap instead of quietly reading whatever is at a fabricated guest
 *     pointer.
 *   * **Typed by their high half**, so a diagnostic can say "that is an HDC"
 *     from the value alone.
 *   * **Deterministic**: the n-th HDC of a run is always the same value,
 *     because a replay whose handles moved would move any guest state derived
 *     from them.
 *
 * Where the one region came from, and why there is only one
 * ----------------------------------------------------------
 * The occupied high regions. **This list is no longer the record** -- it is a
 * reader's convenience, and the record is
 * `Documentation/handle-regions.json`, which
 * `Documentation/scripts/check_handle_regions.py` reads back out of the
 * headers on every gate pass. A comment cannot be checked; that file can, and
 * a prose list of addresses in one header is exactly the thing that goes
 * stale the moment another file moves. This one did, within the hour.
 *
 *     0x7c000000  SA2_METRICS_AGENT_TOKEN (runtime/sa2_iface.c)
 *     0x7d000000  SA2_VFS_HANDLE_BASE     (runtime/sa2_files.h)   -- see below
 *     0x7e000000  SA2_WND_HWND_BASE       (runtime/sa2_window.h)
 *     0x7e100000  SA2_WND_HICON_BASE
 *     0x7e200000  SA2_WND_HCURSOR_BASE
 *     0x7e300000  SA2_WND_HBRUSH_BASE
 *     0x7e400000  SA2_D3D9_HMONITOR_BASE  (runtime/sa2_d3d9.h)
 *     0x7e500000  SA2_WND_HMENU_BASE
 *     0x7e600000  SA2_WND_HDC_BASE        (runtime/sa2_window.h)
 *     0x7e700000  SA2_WND_TOKEN_END: the window file mints nothing above this
 *     0x7e800000  SA2_GDI_HFONT_BASE      (this file)
 *     0x7f000000  SA2_HANDLE_BASE         (runtime/sa2_imports.c)
 *
 * `sa2_window.h` leaves 0x7e700000 upwards to this file and suggests starting
 * at 0x7e800000 to leave a visible hole between the two schemes rather than
 * putting them back to back. **SA2_GDI_HFONT_BASE is therefore 0x7e800000**,
 * and it is the only region this file defines.
 *
 * The region is picked from a grep and not counted off the end of the window
 * kinds, because counting is how 0x7e400000 came to be both an HMONITOR and an
 * HDC: it was the obvious next slot twice, in two files, neither of which
 * could see the other. The HDC has since moved to 0x7e600000 and the file
 * handles to 0x7d000000, both for collisions this comment was written to
 * avoid and did not.
 *
 * **The other two kinds reuse namespaces that already exist.**
 * GDI32!CreateSolidBrush returns an HBRUSH and GDI32!CreateDCW returns an HDC,
 * and `sa2_window.c` already owns both namespaces and both counters -- the DC
 * one for `BeginPaint`. A second HBRUSH or HDC region would mean two kinds of
 * one Win32 type in one process, `sa2_window_token_kind` naming only one of
 * them, and a handle from USER32's side of the boundary being
 * indistinguishable-by-value from a handle from this side while still being a
 * different object. So `sa2_gdi_create_dc` and `sa2_gdi_create_solid_brush`
 * both call `sa2_window_token`, the counters are shared, and no HDC or HBRUSH
 * value is ever issued twice. Only HFONT, which nothing else mints, is this
 * file's own.
 *
 * Sharing the HDC namespace has a second consequence worth stating: an HDC
 * that arrives at `SetBkColor` may be one `BeginPaint` minted rather than one
 * `CreateDCW` did -- the dialog's WM_CTLCOLOR handler at 0x00404402 gets its
 * DC from the dialog manager, not from this file. A handle whose high half
 * says HDC is therefore **adopted** into this file's table on first sight, and
 * the row is marked `adopted` so the two origins stay distinguishable. The
 * alternative was answering CLR_INVALID to a call that is entirely legitimate,
 * which would have been this file inventing a failure.
 *
 * What is recorded, and why all of it
 * -----------------------------------
 * Every call, in order, with the import's name, the guest address that called
 * it, its arguments, what it answered, *how* it arrived at that answer, and a
 * sequence number from this file's own counter. A silent no-op is not
 * evidence: the record is what a probe prints and what a receipt cites, and
 * the argument list is the only statement anyone has of what this game asks a
 * GDI for. The per-object and per-DC tables outlive the log, so a run longer
 * than SA2_GDI_LOG_MAX calls still says what existed.
 *
 * Six kinds of answer, and the line between two of them
 * -----------------------------------------------------
 * `runtime/sa2_dsound.h` established the rule this file keeps: every
 * fabricated answer is labelled, and a reader of one row can tell what kind of
 * value it is looking at without consulting the run's environment.
 *
 *   NOOP     nothing to answer; TRUE. `DeleteDC` on a DC this runtime issued
 *            invents nothing -- there was nothing to release, and saying so
 *            is the whole truth.
 *   TOKEN    a handle. A handle is not a measurement: it is an opaque value
 *            whose only contract is that it is distinct, non-zero and
 *            recognisable, and all three are facts about it rather than
 *            claims about a device.
 *   STATE    a value the guest itself supplied earlier -- the previous
 *            background colour, the previously selected font.
 *   DEFAULT  the documented Win32 default for a fresh DC: white background,
 *            black text. Published, not invented, and labelled apart from
 *            STATE so a reader can see the guest never set it.
 *   FAIL     the documented failure value (FALSE, or CLR_INVALID), reached
 *            from this runtime's own tables: a handle it never issued, a DC
 *            it does not have. Also not an invention.
 *   INVENT   **a number this runtime made up.** Exactly one import can
 *            produce one, and it is the point of the next section.
 *
 * The one invented answer
 * -----------------------
 * `GetTextExtentPoint32W` has to write a SIZE, and there is no font, no
 * rasteriser and no capture to agree with, so any extent it writes is
 * fabricated. It is fabricated by a *formula*, stated here, rather than by a
 * magic constant:
 *
 *     cx = character count * SA2_GDI_ADVANCE_PX     (default 8)
 *     cy = SA2_GDI_LINE_PX                          (default 16)
 *
 * a fixed advance per character times the characters asked about, plus a fixed
 * line height. **It is a placeholder with no measurement behind it**, in the
 * same sense as `sa2_imports.c`'s virtual epoch and `sa2_window.c`'s caption
 * and frame metrics: the environment moves it, every value derived from it
 * moves with it, and that is how a run shows whether the guest depends on it
 * rather than asserting that it does not. The dependence is real and it is
 * visible at 0x00402d43, so moving `SA2_GDI_ADVANCE_PX` is expected to move
 * the dialog width the guest computes; a run in which it does not is a run in
 * which that code did not execute.
 *
 * `SA2_GDI_STRICT=1` turns the invented answer back into a refusal -- the call
 * is recorded, `sa2_gdi_text_extent` returns 0, and the adapter traps by name
 * -- so a run can *prove* whether the guest depends on the invented value
 * instead of arguing about it. It is the switch `SA2_DSOUND_STRICT_CAPS`
 * already is for fabricated capabilities. It deliberately does not cover the
 * DEFAULT answers: a documented Win32 default is not this runtime's invention,
 * and folding the two together would make the strict run's verdict ambiguous.
 *
 * Nothing here reads the host clock. The sequence number is this file's own
 * counter, so two runs of the same binary produce the same log.
 *
 * Guest memory
 * ------------
 * This module never touches it. It takes and returns plain host values; the
 * adapters in `runtime/sa2_imports.c` read the guest's LOGFONTW and string and
 * write the guest's SIZE, because that file owns `gpeek32`, `gpoke32` and
 * `gwstr` and owns the decision to trap on a bad guest address.
 *
 * Environment
 * -----------
 *   SA2_GDI_STRICT=1      every invented answer refuses instead
 *   SA2_GDI_ADVANCE_PX=n  the fabricated per-character advance (default 8)
 *   SA2_GDI_LINE_PX=n     the fabricated line height (default 16)
 */
#ifndef SA2_RUNTIME_GDI_H
#define SA2_RUNTIME_GDI_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The one token region this file defines. Above SA2_GUEST_END (0x20000000),
 * above SA2_WND_TOKEN_END (0x7e700000) with the deliberate hole
 * `sa2_window.h` asks for, and below SA2_HANDLE_BASE (0x7f000000). The stride
 * matches SA2_WND_TOKEN_STRIDE so the whole 0x7exxxxxx scheme reads the same
 * way wherever a value comes from. HDCs and HBRUSHes are not here: they come
 * from SA2_WND_HDC_BASE and SA2_WND_HBRUSH_BASE. */
#define SA2_GDI_HFONT_BASE    0x7e800000u
#define SA2_GDI_TOKEN_STRIDE  0x10u
#define SA2_GDI_MAX_TOKENS    32u     /* as SA2_WND_MAX_TOKENS is */

#define SA2_GDI_MAX_DCS       8u
#define SA2_GDI_MAX_OBJECTS   64u
#define SA2_GDI_LOG_MAX       512u
#define SA2_GDI_NAME_MAX      40u

/* The fabricated text metrics. Placeholders; see the header comment. */
#define SA2_GDI_DEFAULT_ADVANCE_PX 8u
#define SA2_GDI_DEFAULT_LINE_PX    16u

/* Win32 values this file returns by their published names. */
#define SA2_GDI_CLR_INVALID   0xFFFFFFFFu
/* A fresh DC's documented colours: white background, black text. */
#define SA2_GDI_DEFAULT_BK_COLOR   0x00FFFFFFu
#define SA2_GDI_DEFAULT_TEXT_COLOR 0x00000000u

/* How an answer was arrived at. The distinction is the point of the log; see
 * "Six kinds of answer" above. */
enum {
    SA2_GDI_ANS_REFUSED = 0,   /* recorded, then trapped by name by the adapter */
    SA2_GDI_ANS_NOOP    = 1,   /* recorded; nothing to answer, TRUE */
    SA2_GDI_ANS_TOKEN   = 2,   /* a handle token, which is not a measurement */
    SA2_GDI_ANS_STATE   = 3,   /* a value the guest itself supplied earlier */
    SA2_GDI_ANS_DEFAULT = 4,   /* the documented Win32 default for a fresh DC */
    SA2_GDI_ANS_FAIL    = 5,   /* the documented failure value, from this runtime's tables */
    SA2_GDI_ANS_INVENT  = 6,   /* a number this runtime made up */
};

/* Object kinds, which are also what the token's high half says. */
enum {
    SA2_GDI_OBJ_UNKNOWN = 0,
    SA2_GDI_OBJ_DC      = 1,
    SA2_GDI_OBJ_FONT    = 2,
    SA2_GDI_OBJ_BRUSH   = 3,
};

/* One GDI call, in order, with the arguments that carry meaning. This is the
 * S5 hand-off: what the guest asked a GDI for. */
typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t a[4];
    uint32_t ret;
    uint32_t out0, out1;       /* GetTextExtentPoint32W's SIZE.cx / SIZE.cy, else 0 */
    uint8_t  answered;         /* SA2_GDI_ANS_* */
    uint8_t  outc;             /* dwords written through an out-pointer: 0 or 2 */
    char     what[SA2_GDI_NAME_MAX];
} sa2_gdi_event;

/* One object this runtime issued a handle for, kept after a delete so a run
 * that ends mid-dialog still says what existed. */
typedef struct {
    uint32_t handle;
    uint32_t kind;             /* SA2_GDI_OBJ_* */
    uint32_t create_caller;
    uint32_t color;            /* CreateSolidBrush's COLORREF; 0 otherwise */
    uint32_t logfont_va;       /* CreateFontIndirectW's argument, as the guest passed it */
    int32_t  height, weight;   /* LOGFONTW.lfHeight / lfWeight, from the guest */
    uint32_t stock;            /* a DC's initial contents, not something the guest made */
    uint32_t selected_into;    /* the HDC it is selected into now, 0 when none */
    uint32_t selects;
    uint32_t deleted;
    char     name[SA2_GDI_NAME_MAX];   /* the face name, as the guest spelled it */
} sa2_gdi_object;

typedef struct {
    uint32_t hdc;
    uint32_t create_caller;
    uint32_t devmode_va;       /* CreateDCW's fourth argument, as the guest passed it */
    /* Currently selected, per kind. A fresh DC's font and brush are stock
     * tokens this file issues at creation, because SelectObject returns the
     * object it replaced and NULL is that function's failure answer: a first
     * SelectObject that answered 0 would be reporting an error that did not
     * happen. `other` has no stock object and starts at 0. */
    uint32_t font, brush, other;
    uint32_t bk_color, text_color;
    uint32_t bk_set, text_set; /* 0 while the colour is still the documented default */
    uint32_t selects, extents;
    uint32_t deleted;
    /* 1 when the handle arrived from somewhere else -- a BeginPaint DC the
     * dialog manager handed the guest -- rather than from CreateDCW here. An
     * adopted DC has no stock objects, because this file did not create it
     * and does not know what is in it. */
    uint32_t adopted;
    char     driver[SA2_GDI_NAME_MAX];
    char     device[SA2_GDI_NAME_MAX];
    char     port[SA2_GDI_NAME_MAX];
} sa2_gdi_dc;

typedef struct {
    uint32_t calls;
    /* One counter per answer kind. `invented` is the one that matters: it is
     * the count of numbers this runtime made up, and it is kept apart from
     * `noop` so "the guest called nine presentation stubs" and "the runtime
     * told the guest nine things it does not know" cannot be read off the same
     * number. */
    uint32_t refused, noop, token, state, deflt, fail, invented;
    uint32_t dcs_created, dcs_adopted, fonts_created, brushes_created;
    uint32_t dcs_deleted, objects_deleted;
    uint32_t selects, set_bk, set_text, text_extents;
    uint32_t text_extent_chars;      /* characters asked about, summed */
    /* A handle the guest passed that this runtime never issued. Reported, not
     * asserted away: a non-zero count means either another module's token
     * crossed into a GDI argument or the guest fabricated one, and both are
     * findings rather than values to paper over. */
    uint32_t unknown_handle, unknown_dc;
    /* Windows fails DeleteObject on an object that is still selected into a
     * DC. This runtime does not model that -- it has no capture to agree with
     * and refusing a delete the guest expects to succeed would be a divergence
     * invented to be faithful -- so the case is counted instead. It is zero on
     * both paths measured above. */
    uint32_t deleted_while_selected;
    uint32_t tokens_exhausted;
    uint32_t strict;
    uint32_t advance_px, line_px;
} sa2_gdi_stats;

/* Both mirror sa2_window.c's shapes. `reset` clears the recorded state -- the
 * tables, the log, the counters and the token counters -- and, exactly as
 * `sa2_window_reset` does with the poll limit, leaves the configuration where
 * the environment put it. */
void sa2_gdi_config_from_env(void);
void sa2_gdi_reset(void);

int  sa2_gdi_strict(void);

/* -------------------------------------------------------------- the nine
 *
 * Every one of these takes `caller`, the guest address that called the import,
 * and records the call before it answers. None of them touches guest memory:
 * the adapter in `runtime/sa2_imports.c` reads and writes it.
 *
 * A 0 from one of the three creators means the token table is full, which the
 * adapter turns into a trap by name -- never into a handle the guest could
 * use. */
/* `arg_va` is CreateDCW's four arguments exactly as the guest pushed them, so
 * the log row says what was passed; the three narrowed strings are the same
 * three pointers read, which the adapter does because it owns guest memory. */
uint32_t sa2_gdi_create_dc(uint32_t caller, const uint32_t arg_va[4],
                           const char *driver, const char *device, const char *port);
uint32_t sa2_gdi_create_font(uint32_t caller, uint32_t logfont_va,
                             int32_t height, int32_t weight, const char *face);
uint32_t sa2_gdi_create_solid_brush(uint32_t caller, uint32_t color);

int      sa2_gdi_delete_dc(uint32_t caller, uint32_t hdc);       /* BOOL */
int      sa2_gdi_delete_object(uint32_t caller, uint32_t handle);/* BOOL */

/* The object being replaced, which is what Windows returns, or 0 for a DC this
 * runtime does not have. */
uint32_t sa2_gdi_select_object(uint32_t caller, uint32_t hdc, uint32_t handle);

/* The previous colour, or SA2_GDI_CLR_INVALID for a DC this runtime does not
 * have. The first call on a DC answers the documented default. */
uint32_t sa2_gdi_set_bk_color(uint32_t caller, uint32_t hdc, uint32_t color);
uint32_t sa2_gdi_set_text_color(uint32_t caller, uint32_t hdc, uint32_t color);

/* GetTextExtentPoint32W. Three outcomes, kept apart because two of them look
 * the same to a caller that only tests for zero and they are not the same
 * fact:
 *
 *    1  `*cx` and `*cy` are filled and the adapter writes the guest's SIZE.
 *    0  the documented FALSE: a DC this runtime does not have, or a null
 *       SIZE pointer. Windows answers the same and writes nothing.
 *   -1  **refused.** SA2_GDI_STRICT is set, or the character count is
 *       negative. The adapter traps by name with `sa2_gdi_refusal()`; it must
 *       not turn a refusal into a FALSE, because a guest that carried on
 *       after one would be carrying on without the number it asked for.
 *
 * `text` is the narrowed string, kept for the record only; `chars` is what
 * the formula uses, because it is the argument the guest passed and the
 * string need not be terminated. */
int sa2_gdi_text_extent(uint32_t caller, uint32_t hdc, uint32_t str_va,
                        int32_t chars, uint32_t size_va, const char *text,
                        int32_t *cx, int32_t *cy);

/* Why the last call refused, for the adapter's trap message. Never NULL. */
const char *sa2_gdi_refusal(void);

/* ------------------------------------------------------------- inspection */

void sa2_gdi_stats_get(sa2_gdi_stats *out);
uint32_t             sa2_gdi_log_count(void);
const sa2_gdi_event *sa2_gdi_log_at(uint32_t i);

uint32_t              sa2_gdi_dc_count(void);
const sa2_gdi_dc     *sa2_gdi_dc_at(uint32_t i);
uint32_t              sa2_gdi_object_count(void);
const sa2_gdi_object *sa2_gdi_object_at(uint32_t i);

/* "HDC", "HFONT", "HBRUSH", or NULL: the companion to
 * `sa2_window_token_kind`, which names the four USER32-side kinds. */
const char *sa2_gdi_token_kind(uint32_t token);

#ifdef __cplusplus
}
#endif
#endif
