/* The dialog manager and the clipboard the guest thinks it has.
 *
 * Design 3.5 classifies USER32 as a presentation import and design 5's S4 work
 * list asks for "presentation stubs as no-ops that record calls". Ten USER32
 * imports in the dialog and clipboard groups had no stub at all, so the first
 * call to one raised a trap and stopped the guest. This file is those ten, and
 * `runtime/sa2_window.c` is the model it sits beside: that file owns the window
 * table, the handle tokens and the message queue, and this one owns everything
 * about dialogs and the clipboard that is not a window.
 *
 * What the guest does with a dialog, measured rather than assumed
 * ---------------------------------------------------------------
 * All ten sit in one cluster reached from the window procedure. `tools/s4/xrefs.py`
 * over `private/generated/function-map.json` finds every call site:
 *
 *     WndProc 0x00401810, WM_CLOSE arm
 *       -> 0x004015f0 at 0x0040184a
 *          -> 0x004026e0
 *             -> 0x00402870      three LoadStringW at 0x00402b20, 0x00402bf2, 0x00402c51
 *             -> MessageBeep     at 0x004027a4
 *             -> 0x00404a00 at 0x004027af
 *                -> CreateDialogIndirectParamW at 0x00404cba, DLGPROC 0x00403bd0
 *                -> the dialog's own nested pump at 0x00404d20
 *                   -> 0x00404730 at 0x00404d57  GetDlgItem 0x00404812, OpenClipboard 0x004049ba
 *                      -> 0x004046b0             EmptyClipboard 0x004046b4,
 *                                                SetClipboardData 0x0040470c,
 *                                                CloseClipboard 0x00404717
 *                -> DialogFunc 0x00403bd0        thirteen GetDlgItem, CheckDlgButton
 *                                                0x00403db3, WinHelpW 0x0040415d
 *     LoadStringW also at 0x00403b31, in 0x00403b30.
 *
 * So the cluster is the quit-confirmation dialog: WndProc's WM_CLOSE arm raises
 * it, compares the result against 7 (IDNO) at 0x00401850, and calls
 * PostQuitMessage at 0x00401857 for anything else.
 *
 * **Every clipboard call and thirteen of the fourteen GetDlgItem calls are
 * inside the branch that only runs when CreateDialogIndirectParamW succeeded.**
 * That one answer therefore decides whether the other nine are ever reached,
 * which is why it is the one this file thinks hardest about.
 *
 * Why CreateDialogIndirectParamW returns NULL
 * -------------------------------------------
 * On Windows it creates a modeless dialog *and calls the dialog procedure with
 * WM_INITDIALOG before it returns*. A fabricated HWND that never calls
 * 0x00403bd0 is a lie the guest immediately acts on: it would SetWindowPos and
 * EnableWindow against a window that does not exist and then enter its nested
 * PeekMessageW loop at 0x00404d20, whose only exits are WM_QUIT (0x12, tested
 * at 0x00404d37) and its own controls answering -- controls that were never
 * created because the procedure never ran. The run would end with the guest
 * inside a modal loop it never entered on Windows.
 *
 * NULL is what Windows itself returns for a template it cannot realise, and the
 * guest has the branch already: `test edi,edi / je 0x00404dd9` at 0x00404cc2
 * jumps to LocalUnlock/LocalFree of the template and returns the object's
 * result field, initialised to -1 at 0x004028dc. -1 is not 7, so WndProc goes
 * on to PostQuitMessage: a WM_CLOSE with no confirmation dialog closes the
 * window, which is the honest degradation of "there is no dialog manager".
 *
 * Nothing here runs guest code. Calling the DLGPROC would need the window
 * procedure machinery in `sa2_imports.c` and a control model underneath it,
 * and that is a dialog manager, not a stub -- S5's, if a capture ever shows the
 * guest needs one.
 *
 * Why LoadStringW is measured and not invented
 * --------------------------------------------
 * Unlike the rest of this list its true answer is *available*: the PE is mapped
 * into guest memory, so the image's own resource directory can be walked.
 * `sa2_dialog_load_string` walks it -- DataDirectory[2] of the mapped optional
 * header, the RT_STRING (type 6) subtree, bundle `id/16 + 1`, string `id % 16`
 * inside it -- and answers with the characters that are there.
 *
 * **This image has no RT_STRING resources at all.** Its resource directory
 * holds RT_ICON (7), RT_GROUP_ICON (1) and RT_MANIFEST (1) and nothing else, so
 * the walk answers 0 for every id, which is the *measured* answer and not a
 * cheap substitute for one. The guest is built for it: at 0x00402bf8 and
 * 0x00402c57 it tests the result, writes its own terminator on zero, and falls
 * back to a string it carries in its own data ([ebx+0x230], [ebx+0x43c]) via
 * the copy at 0x007a60c9. The strings it would have displayed are the dialog's
 * captions, and the dialog is not created.
 *
 * The walk is kept even though its answer is constant for this image, because
 * a constant 0 typed into a stub and a 0 that came from reading the resource
 * directory are the same number with different futures: the second one changes
 * by itself if the directory ever does.
 *
 * Why the clipboard no-ops are safe, which is a check and not an opinion
 * ----------------------------------------------------------------------
 * `SetClipboardData` takes ownership of the handle on Windows, and this runtime
 * drops it -- the guest's `GlobalAlloc` block is recorded and never freed.
 * That is safe only because nothing ever reads the clipboard back, and that is
 * checkable rather than arguable: `Documentation/receipts/import-classification.json`
 * lists all 251 imported symbols and **`GetClipboardData` is not among them**.
 * The clipboard group the image imports is exactly OpenClipboard,
 * CloseClipboard, EmptyClipboard and SetClipboardData: a write-only clipboard.
 * The guest also never frees the handle itself (0x0040470c is followed by
 * `mov ebx,1`, not by GlobalFree), so keeping it is what the guest expects and
 * there is no double free to arrange.
 *
 * The three kinds of answer, after runtime/sa2_dsound.c's rule
 * ------------------------------------------------------------
 * Every call is recorded with a label saying what kind of answer it got, so a
 * reader of the log never has to infer it:
 *
 *   * **measured** -- the answer is a fact about this image or this runtime.
 *     Every NULL handle here is one: there is no dialog, so there is no dialog
 *     item, so NULL is true. So is every LoadStringW count.
 *   * **no-op success** -- the runtime reports that something happened which it
 *     did not do. The four clipboard calls and MessageBeep are these, and they
 *     are the only ones. `SA2_DIALOG_STRICT=1` turns each of them into a
 *     refusal, so a run can prove whether the guest depends on them instead of
 *     the project asserting that it does not.
 *   * **invented** -- a value the runtime made up and the guest reads. There
 *     are none in this file, and the counter exists so that a later answer that
 *     does invent one cannot be added without being counted.
 *
 * Handle tokens: none are taken
 * -----------------------------
 * `sa2_window.c` explains why a handle is a token outside the guest address
 * space, typed by its high half and deterministic. This file needs no region:
 * the only two entry points that could return a handle, CreateDialogIndirectParamW
 * and GetDlgItem, both answer NULL, and a token region reserved for a handle
 * that is never handed out is scaffolding. Nothing here can collide with the
 * regions `sa2_window.h` (0x7e000000..0x7e3fffff), `sa2_d3d9.h` (0x7e400000)
 * and `sa2_imports.c` (0x7f000000) already name, or with any new one.
 *
 * Nothing here reads the host clock, allocates guest memory, or writes guest
 * memory: the adapters in `sa2_imports.c` do the guest reads and writes, and
 * the single exception is the resource walk, which reads the mapped image
 * because that read *is* the answer.
 */
#ifndef SA2_RUNTIME_DIALOG_H
#define SA2_RUNTIME_DIALOG_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_DLG_LOG_MAX        256u
#define SA2_DLG_CLIPBOARD_MAX  8u
/* The longest resource string this runtime will copy. A string longer than
 * this is refused by name rather than truncated: a truncated caption is a
 * wrong answer that looks like a right one. */
#define SA2_DLG_STRING_MAX     1024u

/* Clipboard formats this file names. CF_UNICODETEXT is what 0x0040470c passes. */
#define SA2_CF_TEXT            1u
#define SA2_CF_UNICODETEXT     13u

/* Win32 codes the adapters set on the paths where Windows sets one. */
#define SA2_DLG_ERROR_CONTROL_ID_NOT_FOUND  1421u

/* What kind of answer a recorded call got. See the header comment. */
enum {
    SA2_DLG_MEASURED = 0,   /* a fact about this image or this runtime */
    SA2_DLG_NOOP     = 1,   /* success reported for something not done */
    SA2_DLG_INVENTED = 2,   /* a value made up and read by the guest (none yet) */
    SA2_DLG_REFUSED  = 3    /* strict mode, or a case with no defensible answer */
};

/* One dialog or clipboard call, in order, with the arguments that carry
 * meaning. Same shape as `sa2_wnd_event`, plus the answer's kind, because a
 * silent no-op is not evidence. */
typedef struct {
    uint32_t seq;
    uint32_t caller;        /* the guest address the call returns to */
    uint32_t a[4];
    uint32_t ret;
    uint32_t kind;          /* SA2_DLG_* */
    char     what[32];
} sa2_dlg_event;

/* A handle handed to SetClipboardData and never given back. */
typedef struct {
    uint32_t format;
    uint32_t handle;
    uint32_t seq;
} sa2_dlg_clip;

typedef struct {
    uint32_t calls;
    uint32_t measured;        /* answers that are facts */
    uint32_t noop_success;    /* successes reported for work not done */
    uint32_t invented;        /* fabricated values the guest read; 0 by construction */
    uint32_t refused;
    /* the clipboard */
    uint32_t clipboard_opens, clipboard_empties, clipboard_sets, clipboard_closes;
    uint32_t clipboard_open_nested;    /* OpenClipboard while already open */
    uint32_t clipboard_out_of_order;   /* a call that needs an open clipboard, without one */
    uint32_t clipboard_handles_kept;   /* handles taken and never freed */
    uint32_t clipboard_handles_dropped;/* ... that did not fit the table */
    uint32_t clipboard_open;           /* 1 while the guest holds the clipboard */
    /* dialogs */
    uint32_t dialogs_refused;          /* CreateDialogIndirectParamW answered NULL */
    uint32_t dlg_items_absent;         /* GetDlgItem answered NULL */
    uint32_t buttons_checked;
    uint32_t beeps;
    uint32_t helps;
    /* strings */
    uint32_t strings_asked, strings_found, strings_absent;
    uint32_t string_chars;             /* total code units copied out of the image */
} sa2_dlg_stats;

/* Configure from the environment:
 *   SA2_DIALOG_STRICT=1   every no-op success refuses instead, so a run shows
 *                         whether the guest depends on one
 */
void sa2_dialog_config_from_env(void);
void sa2_dialog_reset(void);
int  sa2_dialog_strict(void);
void sa2_dialog_set_strict(int on);

/* ------------------------------------------------------------ the calls
 *
 * Every entry point has the shape `sa2_window_metric` established: it returns
 * 1 with `*out` set to the value the guest gets, or 0 when it refuses, and the
 * caller then refuses by name with `sa2_dialog_refusal()` as the reason.
 * `*out` is written on every path that returns 1; nothing leaves an
 * out-parameter holding whatever the caller had on its stack.
 *
 * `caller` is the guest address the import returns to, for the record. */

int sa2_dialog_open_clipboard(uint32_t caller, uint32_t hwnd, uint32_t *out);
int sa2_dialog_empty_clipboard(uint32_t caller, uint32_t *out);
int sa2_dialog_set_clipboard_data(uint32_t caller, uint32_t format, uint32_t handle,
                                  uint32_t *out);
int sa2_dialog_close_clipboard(uint32_t caller, uint32_t *out);

int sa2_dialog_create_dialog_indirect_param(uint32_t caller, uint32_t hinstance,
                                            uint32_t tmpl, uint32_t parent,
                                            uint32_t dlgproc, uint32_t init_param,
                                            uint32_t *out);
int sa2_dialog_get_dlg_item(uint32_t caller, uint32_t hdlg, uint32_t id, uint32_t *out);
int sa2_dialog_check_dlg_button(uint32_t caller, uint32_t hdlg, uint32_t id,
                                uint32_t check, uint32_t *out);
int sa2_dialog_message_beep(uint32_t caller, uint32_t type, uint32_t *out);
int sa2_dialog_win_help(uint32_t caller, uint32_t hwnd, uint32_t help_file_va,
                        uint32_t command, uint32_t data, uint32_t *out);

/* USER32!LoadStringW(hInstance, id, lpBuffer, cchBufferMax), out of the image's
 * own mapped resource directory.
 *
 * `cch` is the guest's cchBufferMax. `buf` receives up to min(cch - 1, cap)
 * UTF-16 code units, **not** terminated here, and `*out` is the count Windows
 * would return. The caller copies `*out` units into the guest buffer and
 * always writes the terminator after them, including when `*out` is 0: Windows
 * terminates the buffer even when the string is missing, and a caller that
 * skipped it would leave the guest reading its own stack.
 *
 * Refuses, rather than answering, in three cases that have no honest answer:
 * `cch == 0` (the pointer-to-resource mode, which writes a pointer into the
 * buffer argument and this runtime does not implement), an `hInstance` that is
 * not the mapped image, and a resource string longer than `cap`. */
uint32_t sa2_dialog_string_cap(void);   /* SA2_DLG_STRING_MAX, for the adapter */
int sa2_dialog_load_string(uint32_t caller, uint32_t hinstance, uint32_t id,
                           uint32_t cch, uint16_t *buf, uint32_t cap, uint32_t *out);

/* Why the last call refused. Never NULL; "" before the first refusal. */
const char *sa2_dialog_refusal(void);

/* ----------------------------------------------------------- inspection */

void                 sa2_dialog_stats(sa2_dlg_stats *out);
uint32_t             sa2_dialog_log_count(void);
const sa2_dlg_event *sa2_dialog_log_at(uint32_t i);
uint32_t             sa2_dialog_clipboard_count(void);
const sa2_dlg_clip  *sa2_dialog_clipboard_at(uint32_t i);

#ifdef __cplusplus
}
#endif
#endif
