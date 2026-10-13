/* Nine recording no-op GDI32 stubs. See sa2_gdi.h for the reasoning: which
 * guest code calls them, where the token regions came from, what the six
 * answer kinds mean, and why exactly one answer in this file is invented. */
#include "sa2_gdi.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* The only reason this module knows about the window module: CreateDCW
 * returns an HDC and CreateSolidBrush returns an HBRUSH, and `sa2_window.c`
 * already owns both namespaces and both counters. Sharing them is what makes
 * "no HDC or HBRUSH value is ever issued twice" a fact rather than a hope. */
#include "sa2_window.h"

/* ------------------------------------------------------------- state */

static sa2_gdi_dc     g_dcs[SA2_GDI_MAX_DCS];
static uint32_t       g_dc_count;
static sa2_gdi_object g_objs[SA2_GDI_MAX_OBJECTS];
static uint32_t       g_obj_count;

/* HDC and HBRUSH counters live in sa2_window.c, because the namespaces do. */
static uint32_t g_hfont_used;

static sa2_gdi_event g_log[SA2_GDI_LOG_MAX];
static uint32_t      g_log_used;
/* The sequence number. This file's own counter: nothing here reads the host
 * clock, so two runs of the same binary produce the same log. It counts every
 * call, including the ones past SA2_GDI_LOG_MAX that the array cannot hold. */
static uint32_t      g_seq;

static sa2_gdi_stats g_st;

static int      g_strict;
static uint32_t g_advance_px = SA2_GDI_DEFAULT_ADVANCE_PX;
static uint32_t g_line_px    = SA2_GDI_DEFAULT_LINE_PX;

static char g_refusal[160] = "no GDI call has been refused";

/* --------------------------------------------------------------- config */

static uint32_t env_u32(const char *name, uint32_t fallback) {
    const char *v = getenv(name);
    if (!v || !*v) return fallback;
    char *end = NULL;
    unsigned long n = strtoul(v, &end, 0);
    if (end == v) return fallback;
    return (uint32_t)n;
}

void sa2_gdi_config_from_env(void) {
    const char *v = getenv("SA2_GDI_STRICT");
    g_strict = (v && *v && *v != '0') ? 1 : 0;
    g_advance_px = env_u32("SA2_GDI_ADVANCE_PX", SA2_GDI_DEFAULT_ADVANCE_PX);
    g_line_px    = env_u32("SA2_GDI_LINE_PX", SA2_GDI_DEFAULT_LINE_PX);
}

/* The recorded state, not the configuration: `sa2_window_reset` leaves the
 * poll limit and the screen size where the environment put them and this does
 * the same with the strict flag and the two metric constants. A reset that
 * also cleared them would make a harness which configures once and resets
 * between runs lose SA2_GDI_STRICT without saying so, and a switch that
 * silently stops being set is worse than one that is not offered.
 *
 * The brush counter lives in `sa2_window.c` because the namespace does, so a
 * run that wants brush tokens to start from zero again calls
 * `sa2_window_reset` too -- which it has to do anyway, for the same reason. */
void sa2_gdi_reset(void) {
    memset(g_dcs, 0, sizeof g_dcs);
    memset(g_objs, 0, sizeof g_objs);
    memset(g_log, 0, sizeof g_log);
    memset(&g_st, 0, sizeof g_st);
    g_dc_count = g_obj_count = 0;
    g_hfont_used = 0;
    g_log_used = g_seq = 0;
    snprintf(g_refusal, sizeof g_refusal, "no GDI call has been refused");
}

int sa2_gdi_strict(void) { return g_strict; }
const char *sa2_gdi_refusal(void) { return g_refusal; }

/* ---------------------------------------------------------------- log */

static void count_answer(uint8_t answered) {
    switch (answered) {
        case SA2_GDI_ANS_REFUSED: g_st.refused++;  break;
        case SA2_GDI_ANS_NOOP:    g_st.noop++;     break;
        case SA2_GDI_ANS_TOKEN:   g_st.token++;    break;
        case SA2_GDI_ANS_STATE:   g_st.state++;    break;
        case SA2_GDI_ANS_DEFAULT: g_st.deflt++;    break;
        case SA2_GDI_ANS_FAIL:    g_st.fail++;     break;
        case SA2_GDI_ANS_INVENT:  g_st.invented++; break;
        default: break;
    }
}

/* One row. The counters above are exact whether or not the array had room, so
 * a run longer than SA2_GDI_LOG_MAX calls still counts its invented answers
 * correctly -- the failure mode `sa2_imports.h` records for `first_caller`. */
static void gdi_log(const char *what, uint32_t caller, uint8_t answered,
                    uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                    uint32_t ret, uint32_t out0, uint32_t out1, uint8_t outc) {
    g_seq++;
    g_st.calls++;
    count_answer(answered);
    if (g_log_used >= SA2_GDI_LOG_MAX) return;
    sa2_gdi_event *e = &g_log[g_log_used++];
    e->seq = g_seq;
    e->caller = caller;
    e->a[0] = a0; e->a[1] = a1; e->a[2] = a2; e->a[3] = a3;
    e->ret = ret;
    e->out0 = out0;
    e->out1 = out1;
    e->answered = answered;
    e->outc = outc;
    snprintf(e->what, sizeof e->what, "%s", what ? what : "?");
}

uint32_t             sa2_gdi_log_count(void) { return g_log_used; }
const sa2_gdi_event *sa2_gdi_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

/* -------------------------------------------------------------- tokens */

/* The complement to `sa2_window_token_kind`, which names the six USER32-side
 * kinds. HDC and HBRUSH are in both because the namespaces are shared; HFONT
 * is only here. */
const char *sa2_gdi_token_kind(uint32_t token) {
    switch (token & 0xfff00000u) {
        case SA2_WND_HDC_BASE:    return "HDC";
        case SA2_GDI_HFONT_BASE:  return "HFONT";
        case SA2_WND_HBRUSH_BASE: return "HBRUSH";
        default: return NULL;
    }
}

static uint32_t kind_of_handle(uint32_t h) {
    switch (h & 0xfff00000u) {
        case SA2_WND_HDC_BASE:    return SA2_GDI_OBJ_DC;
        case SA2_GDI_HFONT_BASE:  return SA2_GDI_OBJ_FONT;
        case SA2_WND_HBRUSH_BASE: return SA2_GDI_OBJ_BRUSH;
        default: return SA2_GDI_OBJ_UNKNOWN;
    }
}

static uint32_t hfont_token(void) {
    if (g_hfont_used >= SA2_GDI_MAX_TOKENS) return 0;
    return SA2_GDI_HFONT_BASE + (g_hfont_used++) * SA2_GDI_TOKEN_STRIDE;
}

/* ------------------------------------------------------------- tables */

static sa2_gdi_dc *dc_of(uint32_t hdc) {
    if (!hdc) return NULL;
    for (uint32_t i = 0; i < g_dc_count; i++)
        if (g_dcs[i].hdc == hdc) return &g_dcs[i];
    return NULL;
}

/* The same, but a handle whose high half says HDC and which this file has not
 * seen is adopted rather than rejected: it is a device context somewhere else
 * in the runtime minted -- BeginPaint's, for the dialog's WM_CTLCOLOR -- and
 * answering CLR_INVALID to a legitimate call would be inventing a failure.
 * Its colours start at the documented defaults and it has no stock objects,
 * because this file did not create it and does not know what is in it. */
static sa2_gdi_dc *dc_for(uint32_t hdc) {
    sa2_gdi_dc *d = dc_of(hdc);
    if (d) return d;
    if (kind_of_handle(hdc) != SA2_GDI_OBJ_DC) return NULL;
    if (g_dc_count >= SA2_GDI_MAX_DCS) return NULL;
    d = &g_dcs[g_dc_count++];
    memset(d, 0, sizeof *d);
    d->hdc = hdc;
    d->adopted = 1;
    d->bk_color = SA2_GDI_DEFAULT_BK_COLOR;
    d->text_color = SA2_GDI_DEFAULT_TEXT_COLOR;
    g_st.dcs_adopted++;
    return d;
}

static sa2_gdi_object *obj_of(uint32_t h) {
    if (!h) return NULL;
    for (uint32_t i = 0; i < g_obj_count; i++)
        if (g_objs[i].handle == h) return &g_objs[i];
    return NULL;
}

static sa2_gdi_object *obj_new(uint32_t handle, uint32_t kind, uint32_t caller, uint32_t stock) {
    if (g_obj_count >= SA2_GDI_MAX_OBJECTS) return NULL;
    sa2_gdi_object *o = &g_objs[g_obj_count++];
    memset(o, 0, sizeof *o);
    o->handle = handle;
    o->kind = kind;
    o->create_caller = caller;
    o->stock = stock;
    return o;
}

static void copy_name(char *dst, size_t cap, const char *src) {
    snprintf(dst, cap, "%s", src ? src : "");
}

/* Variadic with the printf attribute, so every call site's format string is
 * checked by the compiler. The runtime is built with `-w` today, so a format
 * that did not match its arguments would otherwise reach a trap message as
 * garbage at exactly the moment somebody needed to read it. */
static void refuse(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void refuse(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_refusal, sizeof g_refusal, fmt, ap);
    va_end(ap);
}

/* ----------------------------------------------------------- the nine */

/* GDI32!CreateDCW(pwszDriver, pwszDevice, pszPort, pdm). Four arguments;
 * 0x00402cbb pushes four dwords, three of them zero and the fourth the guest's
 * L"DISPLAY". There is no display device to open: what the guest needs is a
 * distinct non-zero HDC it can select objects into, measure with, and delete,
 * so it gets a typed token and the request is recorded.
 *
 * The DC's font and brush slots are preloaded with stock tokens. See
 * sa2_gdi.h's `sa2_gdi_dc` comment: SelectObject returns the object it
 * replaced, and NULL is SelectObject's *failure* answer, so a fresh DC whose
 * slots were empty would make the guest's first SelectObject report an error
 * that did not happen. */
uint32_t sa2_gdi_create_dc(uint32_t caller, const uint32_t arg_va[4],
                           const char *driver, const char *device, const char *port) {
    const uint32_t a0 = arg_va ? arg_va[0] : 0u, a1 = arg_va ? arg_va[1] : 0u;
    const uint32_t a2 = arg_va ? arg_va[2] : 0u, a3 = arg_va ? arg_va[3] : 0u;
    if (g_dc_count >= SA2_GDI_MAX_DCS) {
        refuse("CreateDCW: the DC table holds %u and the guest asked for another",
               (unsigned)SA2_GDI_MAX_DCS);
        gdi_log("CreateDCW", caller, SA2_GDI_ANS_REFUSED, a0, a1, a2, a3, 0, 0, 0, 0);
        g_st.tokens_exhausted++;
        return 0;
    }
    const uint32_t h = sa2_window_token(SA2_WND_HDC_BASE, 0, 0);
    const uint32_t stock_font = h ? hfont_token() : 0u;
    const uint32_t stock_brush = h ? sa2_window_token(SA2_WND_HBRUSH_BASE, 0, 0) : 0u;
    if (!h || !stock_font || !stock_brush) {
        refuse("CreateDCW: the token table is full (hdc %#010x, stock font %#010x, "
               "stock brush %#010x)", h, stock_font, stock_brush);
        gdi_log("CreateDCW", caller, SA2_GDI_ANS_REFUSED, a0, a1, a2, a3, 0, 0, 0, 0);
        g_st.tokens_exhausted++;
        return 0;
    }

    sa2_gdi_object *f = obj_new(stock_font, SA2_GDI_OBJ_FONT, caller, 1);
    if (f) { copy_name(f->name, sizeof f->name, "<stock font>"); f->selected_into = h; }
    sa2_gdi_object *b = obj_new(stock_brush, SA2_GDI_OBJ_BRUSH, caller, 1);
    if (b) { copy_name(b->name, sizeof b->name, "<stock brush>"); b->selected_into = h; }

    sa2_gdi_dc *d = &g_dcs[g_dc_count++];
    memset(d, 0, sizeof *d);
    d->hdc = h;
    d->create_caller = caller;
    d->devmode_va = a3;
    d->font = stock_font;
    d->brush = stock_brush;
    d->bk_color = SA2_GDI_DEFAULT_BK_COLOR;
    d->text_color = SA2_GDI_DEFAULT_TEXT_COLOR;
    copy_name(d->driver, sizeof d->driver, driver);
    copy_name(d->device, sizeof d->device, device);
    copy_name(d->port, sizeof d->port, port);

    g_st.dcs_created++;
    gdi_log("CreateDCW", caller, SA2_GDI_ANS_TOKEN, a0, a1, a2, a3, h, 0, 0, 0);
    return h;
}

/* GDI32!CreateFontIndirectW(lplf). One argument; 0x00402cf1 pushes one dword,
 * the address of the LOGFONTW that SystemParametersInfoW(SPI_GETNONCLIENTMETRICS)
 * filled. No font is loaded and no glyph is rasterised. The two LOGFONTW
 * fields kept here are the guest's own -- they are the closest thing anyone
 * has to a statement of what this game wants to draw text with, and they cost
 * two dwords to keep. */
uint32_t sa2_gdi_create_font(uint32_t caller, uint32_t logfont_va,
                             int32_t height, int32_t weight, const char *face) {
    const uint32_t h = hfont_token();
    if (!h) {
        refuse("CreateFontIndirectW(%#010x): the HFONT token table holds %u", logfont_va,
               (unsigned)SA2_GDI_MAX_TOKENS);
        gdi_log("CreateFontIndirectW", caller, SA2_GDI_ANS_REFUSED, logfont_va, 0, 0, 0, 0, 0, 0, 0);
        g_st.tokens_exhausted++;
        return 0;
    }
    sa2_gdi_object *o = obj_new(h, SA2_GDI_OBJ_FONT, caller, 0);
    if (o) {
        o->logfont_va = logfont_va;
        o->height = height;
        o->weight = weight;
        copy_name(o->name, sizeof o->name, face);
    }
    g_st.fonts_created++;
    gdi_log("CreateFontIndirectW", caller, SA2_GDI_ANS_TOKEN, logfont_va,
            (uint32_t)height, (uint32_t)weight, 0, h, 0, 0, 0);
    return h;
}

/* GDI32!CreateSolidBrush(COLORREF). One argument; 0x004029ca and 0x00403fa5
 * each push one dword, a GetSysColor result. The token comes from
 * `sa2_window.c`'s HBRUSH namespace -- see the header comment on why there is
 * no second brush region -- and the colour is the guest's own, kept because it
 * is what S5 has to fill with. */
uint32_t sa2_gdi_create_solid_brush(uint32_t caller, uint32_t color) {
    const uint32_t h = sa2_window_token(SA2_WND_HBRUSH_BASE, 0, 0);
    if (!h) {
        refuse("CreateSolidBrush(%#010x): the shared HBRUSH token table is full", color);
        gdi_log("CreateSolidBrush", caller, SA2_GDI_ANS_REFUSED, color, 0, 0, 0, 0, 0, 0, 0);
        g_st.tokens_exhausted++;
        return 0;
    }
    sa2_gdi_object *o = obj_new(h, SA2_GDI_OBJ_BRUSH, caller, 0);
    if (o) {
        o->color = color;
        copy_name(o->name, sizeof o->name, "<solid brush>");
    }
    g_st.brushes_created++;
    gdi_log("CreateSolidBrush", caller, SA2_GDI_ANS_TOKEN, color, 0, 0, 0, h, 0, 0, 0);
    return h;
}

/* GDI32!DeleteDC(hdc) and GDI32!DeleteObject(ho). One argument each;
 * 0x0040359c, 0x00403c66, 0x00403fbf and the `call edi` reached from
 * 0x00403605 each push one dword.
 *
 * These are the two no-op successes that invent nothing at all. There was no
 * device context and no brush, so there is nothing to release, and TRUE is the
 * whole truth. The answer is not unconditional: a handle this runtime never
 * issued gets FALSE, which is what Windows answers for an invalid handle and
 * which is read off this file's own tables rather than made up. */
int sa2_gdi_delete_dc(uint32_t caller, uint32_t hdc) {
    /* `dc_of`, not `dc_for`: a DeleteDC is not a reason to adopt a device
     * context. Windows also fails DeleteDC on a DC that came from GetDC or
     * BeginPaint -- those are released with ReleaseDC and EndPaint -- so an
     * adopted row answers FALSE here for the reason Windows would. */
    sa2_gdi_dc *d = dc_of(hdc);
    if (!d || d->adopted) {
        if (!d) g_st.unknown_dc++;
        gdi_log("DeleteDC", caller, SA2_GDI_ANS_FAIL, hdc, d ? 1u : 0u, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    d->deleted++;
    /* Whatever is selected into it stops being selected: a DC that no longer
     * exists cannot hold an object, and leaving `selected_into` set would make
     * `deleted_while_selected` fire on an object nothing holds. */
    for (uint32_t i = 0; i < g_obj_count; i++)
        if (g_objs[i].selected_into == hdc) g_objs[i].selected_into = 0;
    g_st.dcs_deleted++;
    gdi_log("DeleteDC", caller, SA2_GDI_ANS_NOOP, hdc, 0, 0, 0, 1, 0, 0, 0);
    return 1;
}

int sa2_gdi_delete_object(uint32_t caller, uint32_t handle) {
    sa2_gdi_object *o = obj_of(handle);
    if (!o) {
        /* Includes DeleteObject(NULL), which Windows also fails. */
        if (handle) g_st.unknown_handle++;
        gdi_log("DeleteObject", caller, SA2_GDI_ANS_FAIL, handle, 0, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    if (o->selected_into) g_st.deleted_while_selected++;
    o->deleted++;
    g_st.objects_deleted++;
    gdi_log("DeleteObject", caller, SA2_GDI_ANS_NOOP, handle, o->kind, o->selected_into, 0,
            1, 0, 0, 0);
    return 1;
}

/* GDI32!SelectObject(hdc, h). Two arguments; 0x00402cff, 0x00403595 and
 * 0x00403c5f each push two dwords. Nothing is selected into anything, because
 * nothing draws -- but the *return value* is load-bearing: the guest stores it
 * at 0x00402d05 and hands it straight back at 0x00403595 to restore the DC's
 * previous font, so a runtime that answered anything other than what it was
 * given would hand the guest a handle it had never seen.
 *
 * The answer is therefore the object this runtime's own table says was in that
 * slot: SA2_GDI_ANS_STATE, a value the guest itself supplied, or the DC's
 * stock token the first time. */
uint32_t sa2_gdi_select_object(uint32_t caller, uint32_t hdc, uint32_t handle) {
    sa2_gdi_dc *d = dc_for(hdc);
    if (!d) {
        g_st.unknown_dc++;
        gdi_log("SelectObject", caller, SA2_GDI_ANS_FAIL, hdc, handle, 0, 0, 0, 0, 0, 0);
        return 0;
    }
    const uint32_t kind = kind_of_handle(handle);
    if (kind == SA2_GDI_OBJ_UNKNOWN && handle) g_st.unknown_handle++;

    uint32_t *slot = (kind == SA2_GDI_OBJ_FONT)  ? &d->font
                   : (kind == SA2_GDI_OBJ_BRUSH) ? &d->brush
                                                 : &d->other;
    const uint32_t prev = *slot;
    *slot = handle;
    d->selects++;
    g_st.selects++;

    sa2_gdi_object *o = obj_of(handle);
    if (o) { o->selected_into = hdc; o->selects++; }
    sa2_gdi_object *p = obj_of(prev);
    if (p && p->selected_into == hdc) p->selected_into = 0;

    /* `prev` is 0 only for the `other` slot, which has no stock object: the
     * documented failure answer, and counted as such rather than as a value. */
    const uint8_t how = prev ? (uint8_t)SA2_GDI_ANS_STATE : (uint8_t)SA2_GDI_ANS_FAIL;
    gdi_log("SelectObject", caller, how, hdc, handle, kind, 0, prev, 0, 0, 0);
    return prev;
}

/* GDI32!SetBkColor(hdc, color) and SetTextColor(hdc, color). Two arguments
 * each; 0x00404402 and 0x00404429 each push two dwords. Both return the
 * *previous* colour, or CLR_INVALID on failure.
 *
 * The previous colour is state the guest set, so after the first call the
 * answer is the guest's own value. The first call on a DC answers the
 * documented Win32 default for a fresh device context -- white background,
 * black text -- which is published rather than invented, and is labelled
 * SA2_GDI_ANS_DEFAULT so a reader can see the guest never set it. It is
 * deliberately not covered by SA2_GDI_STRICT: see the header comment. */
static uint32_t set_color(uint32_t caller, const char *what, uint32_t hdc, uint32_t color,
                          uint32_t *field, uint32_t *set_flag) {
    const uint32_t prev = *field;
    const uint8_t how = *set_flag ? (uint8_t)SA2_GDI_ANS_STATE : (uint8_t)SA2_GDI_ANS_DEFAULT;
    *field = color;
    *set_flag = 1;
    gdi_log(what, caller, how, hdc, color, 0, 0, prev, 0, 0, 0);
    return prev;
}

uint32_t sa2_gdi_set_bk_color(uint32_t caller, uint32_t hdc, uint32_t color) {
    sa2_gdi_dc *d = dc_for(hdc);
    if (!d) {
        g_st.unknown_dc++;
        gdi_log("SetBkColor", caller, SA2_GDI_ANS_FAIL, hdc, color, 0, 0, SA2_GDI_CLR_INVALID, 0, 0, 0);
        return SA2_GDI_CLR_INVALID;
    }
    g_st.set_bk++;
    return set_color(caller, "SetBkColor", hdc, color, &d->bk_color, &d->bk_set);
}

uint32_t sa2_gdi_set_text_color(uint32_t caller, uint32_t hdc, uint32_t color) {
    sa2_gdi_dc *d = dc_for(hdc);
    if (!d) {
        g_st.unknown_dc++;
        gdi_log("SetTextColor", caller, SA2_GDI_ANS_FAIL, hdc, color, 0, 0, SA2_GDI_CLR_INVALID, 0, 0, 0);
        return SA2_GDI_CLR_INVALID;
    }
    g_st.set_text++;
    return set_color(caller, "SetTextColor", hdc, color, &d->text_color, &d->text_set);
}

/* GDI32!GetTextExtentPoint32W(hdc, lpString, c, psizl). Four arguments;
 * 0x00402d3d, 0x00402dfb, 0x0040328d and 0x0040344b each push four dwords.
 *
 * **This is the one invented answer in this file.** There is no font, no
 * rasteriser and no capture to agree with, so the extent is a formula stated
 * in the header and repeated here because a reader of this function must not
 * have to go looking for it:
 *
 *     cx = chars * SA2_GDI_ADVANCE_PX     (default 8)
 *     cy = SA2_GDI_LINE_PX                (default 16)
 *
 * a fixed advance per character times the character count, plus a fixed line
 * height. It is a **placeholder with no measurement behind it** -- not a
 * metric of any font, not derived from anything the guest supplied, and not
 * checked against anything. The environment moves both constants so a run can
 * show what the guest derives from them; at 0x00402d43 the guest reads cx
 * back, adds 20 and keeps the maximum, which is the dialog width, so moving
 * SA2_GDI_ADVANCE_PX is expected to move a number the guest stores.
 *
 * SA2_GDI_STRICT=1 refuses instead, which is how a run *proves* the dependence
 * rather than asserting it. */
int sa2_gdi_text_extent(uint32_t caller, uint32_t hdc, uint32_t str_va,
                        int32_t chars, uint32_t size_va, const char *text,
                        int32_t *cx, int32_t *cy) {
    (void)text;   /* kept for a future row; the count is what the formula uses */
    sa2_gdi_dc *d = dc_for(hdc);
    if (!d || !size_va) {
        /* Windows fails both of these and writes nothing. A null SIZE pointer
         * is checked here rather than in the adapter so that the *reason* the
         * call answered FALSE is on this file's record. */
        if (!d) g_st.unknown_dc++;
        gdi_log("GetTextExtentPoint32W", caller, SA2_GDI_ANS_FAIL, hdc, str_va,
                (uint32_t)chars, size_va, 0, 0, 0, 0);
        return 0;
    }
    if (chars < 0) {
        refuse("GetTextExtentPoint32W(%#010x): character count %d is negative; this runtime "
               "has no answer for it", hdc, (int)chars);
        gdi_log("GetTextExtentPoint32W", caller, SA2_GDI_ANS_REFUSED, hdc, str_va,
                (uint32_t)chars, size_va, 0, 0, 0, 0);
        return -1;
    }
    if (g_strict) {
        refuse("GetTextExtentPoint32W(%#010x, %d chars): SA2_GDI_STRICT=1 and every extent "
               "this runtime would write is invented, so it refuses instead. Unset it to "
               "see what the guest does with the placeholder", hdc, (int)chars);
        gdi_log("GetTextExtentPoint32W", caller, SA2_GDI_ANS_REFUSED, hdc, str_va,
                (uint32_t)chars, size_va, 0, 0, 0, 0);
        return -1;
    }

    const int32_t w = (int32_t)((uint32_t)chars * g_advance_px);
    const int32_t h = (int32_t)g_line_px;
    if (cx) *cx = w;
    if (cy) *cy = h;

    d->extents++;
    g_st.text_extents++;
    g_st.text_extent_chars += (uint32_t)chars;
    gdi_log("GetTextExtentPoint32W", caller, SA2_GDI_ANS_INVENT, hdc, str_va,
            (uint32_t)chars, size_va, 1, (uint32_t)w, (uint32_t)h, 2);
    return 1;
}

/* ------------------------------------------------------------- inspection */

void sa2_gdi_stats_get(sa2_gdi_stats *out) {
    if (!out) return;
    *out = g_st;
    out->strict = (uint32_t)g_strict;
    out->advance_px = g_advance_px;
    out->line_px = g_line_px;
}

uint32_t              sa2_gdi_dc_count(void) { return g_dc_count; }
const sa2_gdi_dc     *sa2_gdi_dc_at(uint32_t i) { return i < g_dc_count ? &g_dcs[i] : NULL; }
uint32_t              sa2_gdi_object_count(void) { return g_obj_count; }
const sa2_gdi_object *sa2_gdi_object_at(uint32_t i) { return i < g_obj_count ? &g_objs[i] : NULL; }
