/* The dialog manager and the clipboard. See sa2_dialog.h for the reasoning. */
#include "sa2_dialog.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "guest_memory.h"

/* ------------------------------------------------------------- state */

static sa2_dlg_event g_log[SA2_DLG_LOG_MAX];
static uint32_t      g_log_used, g_log_seq;

static sa2_dlg_clip  g_clip[SA2_DLG_CLIPBOARD_MAX];
static uint32_t      g_clip_used;

static sa2_dlg_stats g_st;
static int           g_strict;

/* The reason the last call refused, built where it is refused so the adapter
 * does not have to reconstruct it from the arguments. */
static char g_why[256];

/* --------------------------------------------------------------- config */

void sa2_dialog_config_from_env(void) {
    const char *v = getenv("SA2_DIALOG_STRICT");
    if (v && *v) g_strict = (int)strtol(v, NULL, 0);
}

void sa2_dialog_reset(void) {
    memset(g_log, 0, sizeof g_log);
    memset(g_clip, 0, sizeof g_clip);
    memset(&g_st, 0, sizeof g_st);
    g_log_used = g_log_seq = 0;
    g_clip_used = 0;
    g_why[0] = 0;
}

int  sa2_dialog_strict(void) { return g_strict; }
void sa2_dialog_set_strict(int on) { g_strict = on; }
const char *sa2_dialog_refusal(void) { return g_why; }

/* ------------------------------------------------------------------ log */

static void logit(const char *what, uint32_t kind, uint32_t caller,
                  uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3, uint32_t ret) {
    g_log_seq++;
    g_st.calls++;
    switch (kind) {
        case SA2_DLG_MEASURED: g_st.measured++;     break;
        case SA2_DLG_NOOP:     g_st.noop_success++; break;
        case SA2_DLG_INVENTED: g_st.invented++;     break;
        default:               g_st.refused++;      break;
    }
    if (g_log_used >= SA2_DLG_LOG_MAX) return;
    sa2_dlg_event *e = &g_log[g_log_used++];
    e->seq = g_log_seq;
    e->caller = caller;
    e->a[0] = a0; e->a[1] = a1; e->a[2] = a2; e->a[3] = a3;
    e->ret = ret;
    e->kind = kind;
    snprintf(e->what, sizeof e->what, "%s", what ? what : "?");
}

/* A no-op success, or its refusal under SA2_DIALOG_STRICT.
 *
 * The strict arm is the whole reason the two kinds are counted apart: a run
 * with it on either reaches the same place, which proves the guest does not
 * depend on the fabricated success, or stops at the exact call that it does
 * depend on. Asserting the first without being able to produce the second is
 * how a stub quietly becomes a divergence. */
static int noop_success(const char *what, uint32_t caller,
                        uint32_t a0, uint32_t a1, uint32_t a2, uint32_t a3,
                        uint32_t value, const char *did_not, uint32_t *out) {
    if (g_strict) {
        snprintf(g_why, sizeof g_why,
                 "%s would report success for something this runtime did not do (%s), "
                 "and SA2_DIALOG_STRICT=1 asks for the refusal instead", what, did_not);
        logit(what, SA2_DLG_REFUSED, caller, a0, a1, a2, a3, 0);
        return 0;
    }
    logit(what, SA2_DLG_NOOP, caller, a0, a1, a2, a3, value);
    *out = value;
    return 1;
}

/* ---------------------------------------------------------- the clipboard
 *
 * A model, not a counter: OpenClipboard/EmptyClipboard/SetClipboardData/
 * CloseClipboard have an order Windows enforces, and a runtime that answered
 * TRUE to all four in any order would hide a guest that got the order wrong.
 * The order is the one thing here that can be checked without a clipboard, so
 * it is checked. The handles are kept because they are the only statement of
 * what the guest wanted to publish, and S5 inherits them. */

int sa2_dialog_open_clipboard(uint32_t caller, uint32_t hwnd, uint32_t *out) {
    if (g_st.clipboard_open) {
        /* Windows fails a second OpenClipboard from the same thread, and the
         * image has exactly one call site (0x004049ba), so a second open means
         * the runtime misunderstood the path rather than the guest nesting. */
        g_st.clipboard_open_nested++;
        snprintf(g_why, sizeof g_why,
                 "OpenClipboard(%#010x) while this runtime already holds the clipboard: "
                 "the image opens it once, at 0x004049ba, so a nested open is a path "
                 "this file has not modelled", hwnd);
        logit("OpenClipboard", SA2_DLG_REFUSED, caller, hwnd, 0, 0, 0, 0);
        return 0;
    }
    uint32_t v = 0;
    if (!noop_success("OpenClipboard", caller, hwnd, 0, 0, 0, 1u,
                      "there is no system clipboard in this process", &v))
        return 0;
    g_st.clipboard_open = 1;
    g_st.clipboard_opens++;
    *out = v;
    return 1;
}

/* A call that needs the clipboard open, without one. Windows returns FALSE and
 * sets ERROR_CLIPBOARD_NOT_OPEN; answering TRUE would let a guest that lost
 * its own ordering carry on as though it had not. */
static int need_open(const char *what, uint32_t caller, uint32_t a0, uint32_t a1) {
    if (g_st.clipboard_open) return 1;
    g_st.clipboard_out_of_order++;
    logit(what, SA2_DLG_MEASURED, caller, a0, a1, 0, 0, 0);
    return 0;
}

int sa2_dialog_empty_clipboard(uint32_t caller, uint32_t *out) {
    if (!need_open("EmptyClipboard", caller, 0, 0)) { *out = 0; return 1; }
    uint32_t v = 0;
    if (!noop_success("EmptyClipboard", caller, 0, 0, 0, 0, 1u,
                      "there is nothing to empty", &v))
        return 0;
    g_st.clipboard_empties++;
    /* Windows frees every handle on the clipboard here. Ours are kept: the
     * table is the record of what the guest published and freeing it would
     * throw away the evidence, not memory -- the guest's own block is in the
     * guest heap and this table holds only its handle. */
    *out = v;
    return 1;
}

int sa2_dialog_set_clipboard_data(uint32_t caller, uint32_t format, uint32_t handle,
                                  uint32_t *out) {
    if (!need_open("SetClipboardData", caller, format, handle)) { *out = 0; return 1; }
    /* Windows returns the handle on success, so the value the guest reads is
     * its own and nothing is invented; the *success* is the no-op. */
    uint32_t v = 0;
    if (!noop_success("SetClipboardData", caller, format, handle, 0, 0, handle,
                      "no clipboard receives the data and the handle is never given back",
                      &v))
        return 0;
    g_st.clipboard_sets++;
    if (g_clip_used < SA2_DLG_CLIPBOARD_MAX) {
        g_clip[g_clip_used].format = format;
        g_clip[g_clip_used].handle = handle;
        g_clip[g_clip_used].seq = g_log_seq;
        g_clip_used++;
        g_st.clipboard_handles_kept = g_clip_used;
    } else {
        g_st.clipboard_handles_dropped++;
    }
    *out = v;
    return 1;
}

int sa2_dialog_close_clipboard(uint32_t caller, uint32_t *out) {
    if (!need_open("CloseClipboard", caller, 0, 0)) { *out = 0; return 1; }
    uint32_t v = 0;
    if (!noop_success("CloseClipboard", caller, 0, 0, 0, 0, 1u,
                      "there is no clipboard to release", &v))
        return 0;
    g_st.clipboard_open = 0;
    g_st.clipboard_closes++;
    *out = v;
    return 1;
}

/* ------------------------------------------------------------- dialogs */

int sa2_dialog_create_dialog_indirect_param(uint32_t caller, uint32_t hinstance,
                                            uint32_t tmpl, uint32_t parent,
                                            uint32_t dlgproc, uint32_t init_param,
                                            uint32_t *out) {
    /* NULL, and it is measured rather than refused: no dialog was created, so
     * "no dialog" is the true answer and the guest has the branch for it at
     * 0x00404cc2. See the header comment for why a fabricated HWND is worse
     * than this one. The template address and the DLGPROC are recorded because
     * they are the whole description of the dialog this runtime declined to
     * build, and S5 inherits them. */
    (void)init_param;
    logit("CreateDialogIndirectParamW", SA2_DLG_MEASURED, caller,
          hinstance, tmpl, parent, dlgproc, 0);
    g_st.dialogs_refused++;
    *out = 0;
    return 1;
}

int sa2_dialog_get_dlg_item(uint32_t caller, uint32_t hdlg, uint32_t id, uint32_t *out) {
    /* No dialog exists, so no dialog item exists, so NULL is a fact. It is also
     * what the one reachable call site expects to be able to handle: 0x00404812
     * hands the result straight to IsWindow at 0x00404821 and branches away at
     * 0x00404825 when it is not a window. */
    logit("GetDlgItem", SA2_DLG_MEASURED, caller, hdlg, id, 0, 0, 0);
    g_st.dlg_items_absent++;
    *out = 0;
    return 1;
}

int sa2_dialog_check_dlg_button(uint32_t caller, uint32_t hdlg, uint32_t id,
                                uint32_t check, uint32_t *out) {
    /* FALSE, for the same reason GetDlgItem answers NULL, and deliberately not
     * TRUE: a runtime that says it checked a button whose GetDlgItem it just
     * said does not exist contradicts itself inside one dialog procedure. The
     * one call site, 0x00403db3, discards the result. */
    logit("CheckDlgButton", SA2_DLG_MEASURED, caller, hdlg, id, check, 0, 0);
    g_st.buttons_checked++;
    *out = 0;
    return 1;
}

int sa2_dialog_message_beep(uint32_t caller, uint32_t type, uint32_t *out) {
    /* The only one of the ten that is a pure no-op success: there is no sound
     * device, Windows returns TRUE when the sound played, and the call site at
     * 0x004027a4 discards the result. Counted as a no-op rather than measured
     * because TRUE here does claim something happened. */
    uint32_t v = 0;
    if (!noop_success("MessageBeep", caller, type, 0, 0, 0, 1u,
                      "no sound device is opened by this runtime", &v))
        return 0;
    g_st.beeps++;
    *out = v;
    return 1;
}

int sa2_dialog_win_help(uint32_t caller, uint32_t hwnd, uint32_t help_file_va,
                        uint32_t command, uint32_t data, uint32_t *out) {
    /* FALSE, measured: WinHelp is a separate executable that no Windows since
     * Vista ships and that this runtime certainly does not, so "help was not
     * displayed" is true and TRUE would be the invention. The call site at
     * 0x0040415d discards the result and jumps to 0x0040468d. The help file's
     * *address* is recorded rather than its text: it is a constant in the
     * image, so the address is enough to find the string, and a fixed-width
     * log row is what keeps this module free of guest reads. */
    logit("WinHelpW", SA2_DLG_MEASURED, caller, hwnd, help_file_va, command, data, 0);
    g_st.helps++;
    *out = 0;
    return 1;
}

/* ------------------------------------------------- the resource directory
 *
 * The one place in this file that reads guest memory, because the read *is*
 * the answer: LoadStringW's result is a fact about the mapped image rather
 * than something a stub has to decide.
 *
 * Every read is bounds-checked through `sa2_peek` and every offset is checked
 * against the resource directory's own declared extent, so a malformed
 * directory refuses by name instead of walking off into the image. The reads
 * assume a little-endian host, which `sa2_imports.c`'s `gpeek32` already does
 * and which this project's only target is. */

static int rd32(uint32_t va, uint32_t *out) {
    return sa2_peek(va, 4, out) == SA2_OK;
}
static int rd16(uint32_t va, uint16_t *out) {
    return sa2_peek(va, 2, out) == SA2_OK;
}

/* The image's resource directory: guest address and byte length, or 0. */
static int resource_dir(uint32_t *rva_out, uint32_t *size_out) {
    const uint32_t ib = SA2_GUEST_IMAGE_BASE;
    uint32_t lfanew = 0, sig = 0, rva = 0, size = 0;
    uint16_t magic = 0;
    if (!sa2_guest.ready) return 0;
    if (!rd32(ib + 0x3Cu, &lfanew)) return 0;
    if (lfanew > sa2_guest.image_size) return 0;
    const uint32_t nt = ib + lfanew;
    if (!rd32(nt, &sig) || sig != 0x00004550u) return 0;
    const uint32_t opt = nt + 24u;               /* 4 signature + 20 COFF header */
    if (!rd16(opt, &magic) || magic != 0x010Bu) return 0;   /* PE32, not PE32+ */
    /* DataDirectory[2] is the resource table: 96 bytes of PE32 optional header
     * before the array, 8 bytes per entry. DataDirectory[1] at opt+104 is the
     * import table `sa2_imports_bind` reads, which is the same arithmetic one
     * entry down. */
    if (!rd32(opt + 112u, &rva) || !rd32(opt + 116u, &size)) return 0;
    if (!rva || !size) return 0;
    *rva_out = rva;
    *size_out = size;
    return 1;
}

/* One level of IMAGE_RESOURCE_DIRECTORY: find the entry whose id is `id` and
 * return its OffsetToData field, with `*is_dir` saying whether the high bit
 * was set. Named entries are skipped: a string table is always id-keyed. */
static int res_find(uint32_t root, uint32_t dir_off, uint32_t dir_size,
                    uint32_t id, int want_first, uint32_t *off_out, int *is_dir) {
    uint16_t named = 0, ids = 0;
    if (dir_off + 16u > dir_size) return 0;
    if (!rd16(root + dir_off + 12u, &named)) return 0;
    if (!rd16(root + dir_off + 14u, &ids)) return 0;
    const uint32_t first = dir_off + 16u + (uint32_t)named * 8u;
    for (uint32_t i = 0; i < (uint32_t)ids; i++) {
        const uint32_t e = first + i * 8u;
        uint32_t name = 0, off = 0;
        if (e + 8u > dir_size) return 0;
        if (!rd32(root + e, &name) || !rd32(root + e + 4u, &off)) return 0;
        if (!want_first && name != id) continue;
        *is_dir = (off & 0x80000000u) != 0u;
        *off_out = off & 0x7FFFFFFFu;
        return 1;
    }
    return 0;
}

/* The RT_STRING bundle holding `id`, as a guest address and length, or 0 when
 * the image has no such bundle. `*fatal` is set when the directory itself is
 * malformed, which is a refusal rather than a miss. */
static int string_bundle(uint32_t id, uint32_t *data_va, uint32_t *data_len, int *fatal) {
    uint32_t rva = 0, size = 0;
    *fatal = 0;
    if (!resource_dir(&rva, &size)) return 0;   /* no resource directory: a miss */
    const uint32_t root = SA2_GUEST_IMAGE_BASE + rva;
    uint32_t off = 0;
    int is_dir = 0;
    /* type 6 = RT_STRING */
    if (!res_find(root, 0, size, 6u, 0, &off, &is_dir)) return 0;
    if (!is_dir) { *fatal = 1; return 0; }
    /* Bundle `id / 16 + 1` holds strings `id & ~15` .. `id | 15`. */
    if (!res_find(root, off, size, id / 16u + 1u, 0, &off, &is_dir)) return 0;
    if (!is_dir) { *fatal = 1; return 0; }
    /* Language: the first entry. This image has no string table at all, so
     * there is no measurement to prefer one language over another with, and
     * picking the first is what a resource loader does before it consults a
     * thread locale this runtime does not model. */
    if (!res_find(root, off, size, 0u, 1, &off, &is_dir)) return 0;
    if (is_dir) { *fatal = 1; return 0; }
    uint32_t leaf_rva = 0, leaf_size = 0;
    if (off + 8u > size) { *fatal = 1; return 0; }
    if (!rd32(root + off, &leaf_rva) || !rd32(root + off + 4u, &leaf_size)) { *fatal = 1; return 0; }
    if (!leaf_rva || leaf_size < 2u) { *fatal = 1; return 0; }
    if (leaf_rva < rva || leaf_rva - rva > size || size - (leaf_rva - rva) < leaf_size) {
        *fatal = 1;             /* the leaf leaves the resource directory */
        return 0;
    }
    *data_va = SA2_GUEST_IMAGE_BASE + leaf_rva;
    *data_len = leaf_size;
    return 1;
}

uint32_t sa2_dialog_string_cap(void) { return SA2_DLG_STRING_MAX; }

int sa2_dialog_load_string(uint32_t caller, uint32_t hinstance, uint32_t id,
                           uint32_t cch, uint16_t *buf, uint32_t cap, uint32_t *out) {
    g_st.strings_asked++;
    if (!buf || !cap) {
        snprintf(g_why, sizeof g_why, "LoadStringW: the runtime passed no buffer");
        logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
        return 0;
    }
    if (!cch) {
        /* cchBufferMax == 0 asks for a pointer to the resource itself, written
         * into the buffer argument. Answering 0 would look like "no such
         * string" to a caller that is in fact asking for a pointer, and
         * fabricating a pointer into the image is worse. No site in this image
         * uses it (0x00402b2f passes 0xFFFF, 0x00402bea 0x103, 0x00402c4c
         * 0x63), so a call that does is a path nobody has read. */
        snprintf(g_why, sizeof g_why,
                 "LoadStringW(%#010x, %u, cchBufferMax 0): the pointer-to-resource mode "
                 "writes a pointer into the buffer argument and this runtime does not "
                 "implement it; no call site in this image uses it", hinstance, id);
        logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
        return 0;
    }
    if (hinstance && hinstance != SA2_GUEST_IMAGE_BASE) {
        /* Only the main image is mapped. A handle naming anything else is a
         * module whose resources this runtime does not have, and answering 0
         * would say "that module has no such string" when the truth is "that
         * module is not here". */
        snprintf(g_why, sizeof g_why,
                 "LoadStringW(%#010x, %u): only the mapped image (%#010x) has a resource "
                 "directory in this runtime; this handle names another module",
                 hinstance, id, SA2_GUEST_IMAGE_BASE);
        logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
        return 0;
    }

    uint32_t data_va = 0, data_len = 0;
    int fatal = 0;
    if (!string_bundle(id, &data_va, &data_len, &fatal)) {
        if (fatal) {
            snprintf(g_why, sizeof g_why,
                     "LoadStringW(%#010x, %u): the image's resource directory is not "
                     "walkable to the RT_STRING bundle %u", hinstance, id, id / 16u + 1u);
            logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
            return 0;
        }
        /* The measured answer for this image: it carries RT_ICON, RT_GROUP_ICON
         * and RT_MANIFEST and no string table, so every id is absent. The guest
         * tests this at 0x00402bf8 and 0x00402c57 and uses its own fallback. */
        g_st.strings_absent++;
        logit("LoadStringW", SA2_DLG_MEASURED, caller, hinstance, id, cch, 0, 0);
        *out = 0;
        return 1;
    }

    /* Sixteen counted UTF-16 strings, in order, from the start of the bundle. */
    uint32_t p = 0;
    uint16_t len = 0;
    for (uint32_t i = 0; i <= (id & 15u); i++) {
        if (p + 2u > data_len) {
            snprintf(g_why, sizeof g_why,
                     "LoadStringW(%#010x, %u): RT_STRING bundle %u ends after %u of the "
                     "sixteen strings it must hold", hinstance, id, id / 16u + 1u, i);
            logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
            return 0;
        }
        if (!rd16(data_va + p, &len)) {
            snprintf(g_why, sizeof g_why,
                     "LoadStringW(%#010x, %u): the bundle at %#010x left the guest space",
                     hinstance, id, data_va);
            logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
            return 0;
        }
        p += 2u;
        if (i != (id & 15u)) p += (uint32_t)len * 2u;
    }
    if (!len) {                       /* present bundle, absent string */
        g_st.strings_absent++;
        logit("LoadStringW", SA2_DLG_MEASURED, caller, hinstance, id, cch, 0, 0);
        *out = 0;
        return 1;
    }
    if (p + (uint32_t)len * 2u > data_len) {
        snprintf(g_why, sizeof g_why,
                 "LoadStringW(%#010x, %u): the string is %u code units but its bundle has "
                 "%u bytes left", hinstance, id, (unsigned)len, data_len - p);
        logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
        return 0;
    }
    /* Windows copies at most cchBufferMax - 1 units and terminates. A string
     * that does not fit the runtime's own staging buffer is refused rather than
     * truncated: a caption cut in half is a wrong answer that reads like a
     * right one, and the caller has no way to tell. */
    uint32_t room = cch - 1u;
    if (room > cap) {
        if ((uint32_t)len > cap) {
            snprintf(g_why, sizeof g_why,
                     "LoadStringW(%#010x, %u): the string is %u code units and this "
                     "runtime stages at most %u", hinstance, id, (unsigned)len, cap);
            logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
            return 0;
        }
        room = cap;
    }
    uint32_t n = (uint32_t)len < room ? (uint32_t)len : room;
    for (uint32_t i = 0; i < n; i++) {
        if (!rd16(data_va + p + i * 2u, &buf[i])) {
            snprintf(g_why, sizeof g_why,
                     "LoadStringW(%#010x, %u): the string at %#010x left the guest space",
                     hinstance, id, data_va + p);
            logit("LoadStringW", SA2_DLG_REFUSED, caller, hinstance, id, cch, 0, 0);
            return 0;
        }
    }
    g_st.strings_found++;
    g_st.string_chars += n;
    logit("LoadStringW", SA2_DLG_MEASURED, caller, hinstance, id, cch, n, n);
    *out = n;
    return 1;
}

/* ----------------------------------------------------------- inspection */

void sa2_dialog_stats(sa2_dlg_stats *out) {
    if (!out) return;
    *out = g_st;
}
uint32_t             sa2_dialog_log_count(void) { return g_log_used; }
const sa2_dlg_event *sa2_dialog_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }
uint32_t             sa2_dialog_clipboard_count(void) { return g_clip_used; }
const sa2_dlg_clip  *sa2_dialog_clipboard_at(uint32_t i) {
    return i < g_clip_used ? &g_clip[i] : NULL;
}
