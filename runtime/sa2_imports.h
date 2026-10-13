/* Import binding and Win32 stubs for runtime v0 (design 3.5 "Import stubs").
 *
 * The problem this file exists for
 * -------------------------------
 * The image's 251 IAT slots are mapped from the file exactly as the file has
 * them, so every slot holds the guest address of its IMAGE_IMPORT_BY_NAME
 * entry (245 of them) or an ordinal with bit 31 set (5 of them, e.g.
 * 0x8000000B). Guest code calls an import as `call dword ptr [slot]`, remill
 * lifts that to a load followed by `__remill_function_call(loaded_value)`, and
 * the function table has no row for any of those values, so all 845 import
 * call sites end in a table miss. That is the first wall a driven guest
 * function hits: `FUN_00406520` stops at 0x0051525a, which is the *name-table*
 * address of KERNEL32!GetCurrentDirectoryA, not code.
 *
 * What this does about it: bind the IAT, the way a loader does
 * ----------------------------------------------------------
 * At init each slot is overwritten in guest memory with a distinct synthetic
 * *thunk address* in a reserved region no real code occupies, and one native
 * is registered in the function table at each thunk address. `call [slot]`
 * then loads a thunk address, dispatches through the table, and lands in the
 * runtime. The guest's own instruction stream is untouched and no call site is
 * special-cased: to lifted code an import is a function pointer that resolves,
 * which is exactly what it is on Windows.
 *
 * The rejected alternative was to leave guest memory alone and register the
 * natives at the *unbound* values the slots already hold. Three measurements
 * kill it, all of them from the shipped executable rather than from principle:
 *
 *   1. The values are not unique. 251 slots hold 250 distinct values:
 *      XINPUT1_3!Ordinal#2 and OLEAUT32!SysAllocString are both imported by
 *      ordinal 2 and both slots hold 0x80000002. One table row cannot be two
 *      imports, and whichever registered second would silently answer for the
 *      other -- `sa2_ft_add` documents that "a later registration wins".
 *   2. Five of the values are not guest addresses at all (0x80000002 ..
 *      0x8000000B). They work as table keys, but every diagnostic the runtime
 *      prints would report a region and an address that mean nothing, and
 *      anything that later *reads* a bound slot -- GetProcAddress returning a
 *      pointer, the guest caching one in a register, a save state -- would see
 *      a value no loader would ever have written.
 *   3. 245 of them point into `.text`. They are in no mapped function today,
 *      which is luck rather than design: the same address space holds the
 *      12 683 lifted entries, and a registration that collided with one would
 *      replace a real lifted function with an import stub, silently.
 *
 * Binding also makes the *fix* for the above a one-line change rather than a
 * redesign, because the guest sees the same shape Windows gives it.
 *
 * What a caller sees
 * ------------------
 * All 251 rows share one native, `import_native`, which identifies the import
 * from the thunk address it was dispatched on. An import with no stub does not
 * return a plausible value: it records the call and raises a trap naming the
 * DLL, the symbol and the guest call site. Before this file, that same call
 * trapped as "function table miss at 0x0051525a"; the difference between an
 * address and `KERNEL32!GetCurrentDirectoryA called from 0x00406578` is the
 * difference between a wall and a work list.
 *
 * Every call is recorded -- arguments, return value, call site, sequence --
 * whether it was stubbed or trapped, because design 3.5 asks presentation
 * stubs to be "no-ops that record their calls" and a silent no-op is not
 * evidence. The record is what a probe prints and what a receipt cites.
 */
#ifndef SA2_RUNTIME_IMPORTS_H
#define SA2_RUNTIME_IMPORTS_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The thunk region -- SA2_GUEST_IMPORT_BASE and friends -- is part of the
 * guest address-space layout, so it is declared with the rest of it in
 * `guest_memory.h` and `sa2_guest_region_of` names it. */
#include "guest_memory.h"

/* Twelve, because USER32!CreateWindowExW takes twelve and the four it would
 * have lost -- hWndParent, hMenu, hInstance, lpParam -- are exactly the ones
 * that say what kind of window this is. It was 8 while the largest stub was
 * WideCharToMultiByte; the *pop* has always used `argc` and so was always
 * right, but the arguments past the eighth were read as zero, which is a stub
 * answering about arguments it never saw. Sixteen leaves room without another
 * edit of this kind. */
#define SA2_IMPORT_MAX_ARGS   16u
#define SA2_IMPORT_LOG_MAX    4096u   /* recorded calls kept; the counters are exact regardless */

/* OutputDebugStringA/W, kept: the first and the last SA2_DEBUG_KEPT strings,
 * each to SA2_DEBUG_TEXT_MAX bytes with its ordinal among every call, and the
 * count of every call. The kept ones are in call order, the first ones first. */
#define SA2_DEBUG_KEPT       32u
#define SA2_DEBUG_TEXT_MAX  256u
uint32_t    sa2_debug_string_count(void);
uint32_t    sa2_debug_strings_kept(void);
const char *sa2_debug_string_at(uint32_t i, uint32_t *ordinal, int *truncated);

/* Win32 status codes the stubs below return through SetLastError. */
#define SA2_ERROR_SUCCESS            0u
#define SA2_ERROR_MOD_NOT_FOUND    126u
#define SA2_ERROR_INVALID_PARAMETER 87u
#define SA2_ERROR_INSUFFICIENT_BUFFER 122u
#define SA2_ERROR_INVALID_HANDLE      6u
#define SA2_ERROR_NOT_ENOUGH_MEMORY   8u

/* Calling convention of an import, which decides who pops the arguments.
 * Everything in KERNEL32 and USER32 the image imports is stdcall, wvsprintfA
 * included: it takes a va_list, and its one call site (0x00851DE1) leaves the
 * popping to it. The exceptions are Steam's and SteamMetricsSDK's, which are
 * cdecl, so the field exists rather than being assumed away. */
enum { SA2_IMP_STDCALL = 0, SA2_IMP_CDECL = 1 };

/* What one import is, after binding. */
typedef struct {
    const char *dll;          /* "KERNEL32.dll", as the import descriptor spells it */
    const char *name;         /* "GetCurrentDirectoryA", or "Ordinal#2" */
    uint16_t    ordinal;      /* the ordinal when imported by one, else 0 */
    uint32_t    slot;         /* guest address of the IAT slot */
    uint32_t    unbound;      /* the value the file had there, kept for the receipt */
    uint32_t    thunk;        /* guest address now in the slot */
    uint8_t     implemented;  /* a stub answered, rather than the loud default */
    uint8_t     argc;         /* arguments the stub reads and (stdcall) pops */
    uint8_t     conv;         /* SA2_IMP_* */
    uint32_t    calls;        /* exact call count, not bounded by the log */
    /* The first call's return address and first argument, kept here rather
     * than read back out of the 4096-entry call log. Turning threads on took
     * the run past 15 000 import calls, and two of `crt_probe`'s clauses --
     * "WinMain's init returned true" among them -- were reading the log for
     * calls that happen at call 15 4xx and therefore are not in it. They read
     * false for a run in which the guest had done exactly what they asked
     * about. That is the same class as slice 7's `caller_of_current()`
     * returning 0 past the log cap: **a diagnostic that degrades silently past
     * a limit is worse than one that refuses.** These two fields do not
     * degrade. */
    uint32_t    first_caller;
    uint32_t    first_arg0;
    uint8_t     first_seen;
    /* Answers the stub gave through sa2_import_refused: a refusal by policy,
     * counted exactly, with the first one's answer and caller. */
    uint32_t    refused_calls;
    uint32_t    first_refused_ret;
    uint32_t    first_refused_caller;
} sa2_import_info;

/* One recorded call. `caller` is the guest return address the call pushed, so
 * it is the instruction after the call site -- the same evidence
 * `harness/catch_probe.c` reads off the stack. */
typedef struct {
    uint32_t seq;
    uint16_t index;
    uint8_t  implemented;
    uint8_t  argc;
    uint32_t caller;
    uint32_t arg[SA2_IMPORT_MAX_ARGS];
    uint32_t ret;
    /* What the stub wrote *through* an out-pointer, declared by the stub rather
     * than guessed from the arguments. Without this, a probe that wants to know
     * what GetSystemTimeAsFileTime returned has to read the guest buffer after
     * the run -- and by then the CRT has reused that stack slot, so the value
     * it reads is some later frame's. That is exactly how this probe's first
     * cookie oracle failed: it recomputed the right arithmetic over the wrong
     * inputs and reported a mismatch the guest had not made. */
    uint32_t out[2];
    uint8_t  outc;
    /* The presentation survey answered this call with 0 rather than any stub.
     * It is on the record rather than only in a counter because a reader of
     * one row has to be able to tell a fabricated answer from an implemented
     * one without consulting the run's environment. */
    uint8_t  surveyed;
    /* Which guest thread made this call. The sound worker's own import calls
     * are the observable that says a second guest thread executed lifted code,
     * and nothing but the guest's own instructions can put them in this log. */
    uint8_t  thread;
} sa2_import_call;

/* Parse the image's import directory out of *guest memory*, assign a thunk to
 * each import, write it into the slot, and register one function-table row per
 * import. Requires sa2_guest_init and sa2_ft_init to have run, which
 * `sa2_runtime_init` does. Returns the number of imports bound, or a negative
 * SA2_E_* on failure. Idempotent. */
int sa2_imports_bind(void);

/* Honour SA2_IMPORT_BIND=0 (do not bind: the falsification column), and the
 * virtual path values SA2_GUEST_CWD and SA2_GUEST_EXE_PATH. */
void sa2_imports_config_from_env(void);
int  sa2_imports_bound(void);

uint32_t               sa2_import_count(void);
const sa2_import_info *sa2_import_at(uint32_t i);
const sa2_import_info *sa2_import_by_name(const char *dll, const char *name);
/* The import a thunk address belongs to, or NULL. */
const sa2_import_info *sa2_import_for_thunk(uint32_t thunk);
/* The import a *call record* names: `sa2_import_call.index` is a thunk index,
 * which is a static import below SA2_GUEST_IMPORT_DYN_FIRST and a
 * GetProcAddress row at or above it. `sa2_import_at` answers only for the
 * first, so anything reading the log must use this. */
const sa2_import_info *sa2_import_of_index(uint32_t index);

/* GetProcAddress rows resolved so far, and the names asked for on a module the
 * runtime admits that it could not answer -- the next slice's work list, kept
 * because a silent 0 from GetProcAddress is indistinguishable from a symbol
 * that genuinely is not exported. */
uint32_t               sa2_dynimport_count(void);
const sa2_import_info *sa2_dynimport_at(uint32_t i);
uint32_t               sa2_dynimport_missing_count(void);
const char            *sa2_dynimport_missing_at(uint32_t i);
/* Modules LoadLibrary refused, in call order: the data-DLL slice's input. */
/* Calls to SystemParametersInfoW(SPI_GETNONCLIENTMETRICS) that fabricated a
 * desktop metric. Every number that call writes is invented; the counter is
 * how a receipt says so, and SA2_WND_STRICT=1 refuses instead of inventing. */
uint32_t sa2_spi_invented(void);

uint32_t               sa2_loadlibrary_refused_count(void);
const char            *sa2_loadlibrary_refused_at(uint32_t i);
const char            *sa2_loadlibrary_refused_why(uint32_t i);   /* the rule that refused it */

/* The guest heap, both as the allocator counted it and as a walk of the arena
 * reads it back. The two disagreeing means the guest corrupted a header. */
typedef struct {
    uint32_t arena_base, arena_end, ready;
    uint32_t allocs, frees, failures;
    uint32_t live_blocks, live_bytes, peak_bytes;
    uint32_t walk_blocks, walk_inuse, walk_largest_free, walk_inuse_bytes;
    /* A refused request is the only heap event with no trace in the arena
     * afterwards, and the guest turns it into `std::bad_alloc` several frames
     * away from the allocator. `failures` alone says a request was refused and
     * not what it asked for, which is the number that says whether the arena
     * is too small or the caller is wrong. */
    uint32_t fail_last_bytes, fail_max_bytes;
} sa2_heap_stats_t;
void sa2_heap_stats(sa2_heap_stats_t *out);

/* A zeroed block of the guest heap arena for a mapped module image
 * (runtime/sa2_module.c). It is owned by no heap the guest holds -- its header
 * carries SA2_MODULE_OWNER where a heap handle would be -- so a HeapFree of it
 * on any heap the guest was given traps rather than freeing a DLL out from under
 * the pointers into it. Kernel handles live at 0x7f000000 and up, so the owner
 * can never be one. Returns the payload address, or 0 when the arena cannot fit
 * it; LoadLibrary then fails with ERROR_NOT_ENOUGH_MEMORY, which
 * runtime/sa2_module.c reports and load_library sets. */
#define SA2_MODULE_OWNER 0x4d4f4455u    /* "UDOM" */
uint32_t sa2_heap_alloc_module(uint32_t bytes);

/* The same, for any runtime owner: a zeroed block of the guest heap arena from
 * the guest heap's own allocator, owned by `owner` -- an id that is no heap
 * handle the guest holds -- and freed only by `sa2_heap_free_owned` with the
 * same owner, which traps on any other block. This is what keeps runtime-owned
 * guest memory from being a second allocator on the guest's range, which is
 * the defect guest_memory.h's arena comment records: here there is one
 * allocator and every block has its header. */
#define SA2_D3D9_OWNER 0x39443344u      /* "D3D9" */
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes);
void     sa2_heap_free_owned(uint32_t owner, uint32_t payload);

/* The tail of the import call stream. `sa2_import_call_at` walks the first
 * SA2_IMPORT_LOG_MAX calls; this walks the last SA2_IMPORT_RECENT, which is
 * what a reader needs when a run stops fourteen thousand calls in. */
#define SA2_IMPORT_RECENT 32u
typedef struct {
    uint32_t seq;
    uint16_t index;
    uint8_t  ret_valid;
    uint8_t  thread;
    uint32_t caller;
    uint32_t arg0;
    uint32_t ret;
} sa2_import_recent;
uint32_t                 sa2_import_recent_count(void);
const sa2_import_recent *sa2_import_recent_at(uint32_t i);   /* 0 is the oldest kept */
const sa2_import_info   *sa2_import_at_index(uint32_t index);

/* Threads and multimedia timers the guest asked for and this runtime refused
 * (see imp_CreateThread and imp_timeSetEvent). */
uint32_t sa2_import_threads_refused(void);
const char *sa2_import_thread_refused_why(void);   /* "" until one is refused */
uint32_t sa2_import_thread_refused_start(void);
uint32_t sa2_import_timers_refused(void);
/* ...and the ones it created (G1 section 3, runtime/sa2_threads.c). Both
 * counters exist so "the guest asked for two threads" and "the guest got two
 * threads" cannot be read off the same number. */
uint32_t sa2_import_threads_created(void);
uint32_t sa2_import_timers_created(void);
/* Waits that actually handed the guest lock to another thread, and Enters that
 * found the section held by another guest thread. */
uint64_t sa2_import_waits_blocked(void);

/* The event census. One row per event object this runtime created, in creation
 * order, with the guest address that created it and both ends of its traffic --
 * because "an event nothing signals" and "an event signalled on a path nobody
 * took" produce the same blocked thread and are not the same defect. */
typedef struct {
    uint32_t handle, create_caller;
    uint32_t sets, resets, waits, waits_blocked;
    uint32_t manual_reset, signalled;
} sa2_event_row;
unsigned sa2_import_event_count(void);
int      sa2_import_event_at(unsigned i, sa2_event_row *out);
uint64_t sa2_import_cs_contentions(void);
/* The scheduler's index for a guest thread HANDLE, or -1. */
int      sa2_thread_index_of_handle(uint32_t h);
/* RaiseException(0x406D1388), the MSVC SetThreadName idiom: how many times the
 * guest named a thread, and the name it gave each one. Both are the guest's own
 * strings; the runtime supplies neither. */
uint32_t    sa2_import_setthreadname_calls(void);
const char *sa2_import_thread_name(int idx);

/* COM classes the guest asked CoCreateInstance for and this runtime refused,
 * with the raw 16-byte CLSID and IID it passed and the call site. */
uint32_t       sa2_import_cocreate_count(void);
const uint8_t *sa2_import_cocreate_clsid(uint32_t i);
const uint8_t *sa2_import_cocreate_iid(uint32_t i);
uint32_t       sa2_import_cocreate_caller(uint32_t i);

/* The refusal marker. A stub that answers with failure by policy returns
 * through this, naming its import with literals, so the refusal is counted on
 * the import (refused_calls) and tools/s4/refusal_audit.py can find every such
 * answer in the source rather than take a declaration's word for it. Called
 * outside that import's own call it traps: a marker that named another import
 * would count a refusal against the wrong one. Returns `code`. */
uint32_t       sa2_import_refused(const char *dll, const char *name, uint32_t code);

/* OLEAUT32's BSTR, on the process heap GetProcessHeap answers: a 4-byte byte
 * count, `n` UTF-16 units and a NUL unit. The pointer is to the first unit,
 * and 0 when the heap is full. SysAllocString (OLEAUT32 ordinal 2) is this, and
 * so is the BSTR runtime/sa2_wbem.c's IWbemClassObject::Get hands the game.
 * sa2_bstr_free(0) does nothing, as SysFreeString(NULL) does; a pointer that is
 * no live BSTR of the process heap traps naming SysFreeString. */
uint32_t sa2_bstr_alloc(const uint16_t *units, uint32_t n);
void     sa2_bstr_free(uint32_t bstr);
uint32_t sa2_bstr_allocs(void);
uint32_t sa2_bstr_frees(void);

/* MessageBoxA calls, recorded and never displayed. A headless run that shows a
 * message box has almost always just told you why it failed. */
/* Named kernel objects the guest asked for, in call order. WinMain's first
 * instruction group creates one: the single-instance mutex. */
/* Stop the run the (after_n+1)-th time this import is called, before the stub
 * runs. The only breakpoint available on directly-called guest code. */
void        sa2_import_stop_on(const char *name, uint32_t after_n);
int         sa2_import_stopped_on_request(void);

uint32_t    sa2_named_object_count(void);
const char *sa2_named_object_at(uint32_t i);
/* The command tail SA2_GUEST_CMDLINE supplied, which the guest's own parser
 * turns into WinMain's lpCmdLine. */
const char *sa2_import_cmdline_tail(void);

uint32_t    sa2_msgbox_count(void);
const char *sa2_msgbox_text(uint32_t i);
const char *sa2_msgbox_caption(uint32_t i);

uint32_t               sa2_import_calls(void);          /* total calls, exact */
/* SA2_IMPORT_BUDGET: stop the run at this many import calls. 0 when off. It is
 * a *deterministic* bound, unlike the wall-clock alarm, which stops two runs of
 * the same binary at different points and so cannot be used in a run whose
 * determinism is being measured. */
uint32_t               sa2_import_budget(void);
/* End the budget. The harness calls this once, after the guest's entry point
 * has returned or trapped, so its own control probes are not refused by a
 * bound that exists to stop the guest. Deterministic: it happens at the same
 * point in every run. */
void                   sa2_import_budget_release(void);
int                    sa2_import_budget_released(void);
uint32_t               sa2_import_calls_logged(void);   /* min(calls, SA2_IMPORT_LOG_MAX) */
const sa2_import_call *sa2_import_call_at(uint32_t i);
void                   sa2_import_log_reset(void);

/* Win32 state the stubs share, exposed so a probe can assert on it. */
/* The presentation survey (design 3.5 group b, S5's work). `..._on` says the
 * driver asked for it; `..._surveyed` is how many presentation imports were
 * answered with a fabricated 0. A run with a non-zero count is a survey of
 * what the game asks for, not a run of the game. */
int         sa2_presentation_survey_on(void);
uint32_t    sa2_presentation_surveyed(void);

uint32_t    sa2_import_last_error(void);
/* Did the guest call ExitProcess, and with what code? A run that ends this way
 * ended where the guest meant it to, which is not the same fact as a run that
 * stopped at an import with no stub. */
int         sa2_import_exited(void);
uint32_t    sa2_import_exit_code(void);
uint64_t    sa2_import_virtual_ms(void);   /* the virtual clock Sleep advances */
const char *sa2_import_cwd(void);
const char *sa2_import_exe_path(void);

#ifdef __cplusplus
}
#endif
#endif
