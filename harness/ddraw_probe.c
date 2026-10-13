/* Does the recording DirectDraw answer the image's one DirectDraw routine the
 * way native did, from whatever device profile is active?
 *
 *   usage: ddraw_probe <sonic2app-unpacked.exe> --profiles DIR [--json PATH]
 *                      [--capture PROFILE [--require-match]]
 *
 * DIR holds the probe's own device profiles, which
 *
 *   .venv/bin/python tools/s4/ddraw_surface.py --write-profiles DIR
 *
 * writes with tools/s4/device_profile.py's writer, the Python writer the
 * readers are tested against, so they carry every record the grammar has. Each
 * case's JSON fragment and stderr land beside them.
 *
 * Why this exists rather than "the boot run reached it"
 * -----------------------------------------------------
 * The boot run makes one enumeration and one DirectDrawCreate, and under the
 * default profile (SA2_DEVICE_PROFILE unset) the enumeration offers no display
 * with a GUID. So the HMONITOR match the game makes at 0x00865C27 -- the one
 * thing that decides which display DirectDrawCreate is asked for -- is carried
 * by that run without being exercised by it. A claim whose falsification is
 * invisible is not yet a checked claim, and a capture's own profile is host
 * inventory that no committed test may hold. So the probe runs under profiles
 * of made-up displays as well as the default.
 *
 * How
 * ---
 * It drives every answer through the path the guest uses: LoadLibraryA,
 * GetProcAddress and FreeLibrary through their import thunks, the two entry
 * points through the thunks GetProcAddress hands back, the interfaces through
 * the vtables in guest memory, and the game's own lifted callback 0x00865C20
 * on a context laid out as the game lays it out (runtime/sa2_ddraw.h). Nothing
 * here drives runtime/sa2_ddraw.c through a C call; its records are read back
 * at the end. The expected values are the device profile's own records
 * (runtime/sa2_devprofile.h), which are the input the module is supposed to
 * reproduce, and the HMONITOR d3d9 hands the game, read through d3d9's own
 * vtable.
 *
 * The game's callback is not in the S3 function table: only the lifted extras
 * hold it, and linking them registers nothing until sa2_s4_extra_register
 * runs. The probe calls it, as harness/crt_probe.c does, and stops before any
 * case when nothing is registered at 0x00865C20, because every enumeration
 * through that callback would then trap SA2_TRAP_NOT_RUNNABLE and no case
 * would mean anything.
 *
 * The cases, one child process each
 * ---------------------------------
 * The runtime is initialised once; each case runs in a fork of that, so each
 * starts with no DirectDraw object and its own environment.
 *
 *   off                SA2_DDRAW_DEVICE unset, the default profile: legal, the
 *                      before measurement. LoadLibraryA("ddraw.dll") answers 0
 *                      with ERROR_MOD_NOT_FOUND, LoadLibraryA's own import row
 *                      counts it as one marked refusal, the refused load names
 *                      the switch, and the module says it is off. (Every case
 *                      with the device on requires the row to count none.)
 *   default            the device on, the default profile: the whole query,
 *                      then every refusal.
 *   match              SA2_DP_MAX_DDRAW made-up displays, two of them on
 *                      adapter 0's monitor, the last three with the longest
 *                      strings the grammar holds: the match the game makes,
 *                      the last display on adapter 0 kept, the records full.
 *   vidmem-failed      a made-up GetAvailableVidMem that failed: the failure
 *                      handed back with nothing written.
 *   capture            with --capture: that profile, the capture's own;
 *                      --require-match fails it when no entry is on adapter
 *                      0's monitor, so the match is exercised or the run says
 *                      it was not.
 *   brk-argc           SA2_DDRAW_BREAK=argc: the one wrong pop is
 *                      GetAvailableVidMem's, four bytes short.
 *   brk-noout          =noout: DirectDrawCreate answers DD_OK and writes no
 *                      IDirectDraw.
 *   brk-nomatch        =nomatch under `match`: every HMONITOR NULL, the game's
 *                      callback matching nothing, and DirectDrawCreate on the
 *                      zeroed context refused as not native's device.
 *   refuse-count       SA2_DP_MAX_DDRAW + 1 displays: refused at configuration
 *                      by the reader's count limit, in its own words, naming
 *                      the file, before any DirectDraw answer.
 *   refuse-device-off  `match` with SA2_DDRAW_DEVICE unset: refused at
 *                      configuration, naming the switch and the profile.
 *
 * Every device a capture's profile answers for is on in every case
 * (SA2_DINPUT_DEVICE, SA2_XAUDIO2_DEVICE, and SA2_D3D9_DEVICE for the
 * monitors), as a replay of a capture runs them, so the one switch a case
 * moves is SA2_DDRAW_DEVICE. SA2_XINPUT_PAD and SA2_GUEST_CPUS are cleared,
 * since the runtime refuses either beside a capture's profile.
 *
 * What the checks assert
 * ----------------------
 *  * every call pops exactly its own frame: an import thunk 4 + 4 * argc, a
 *    method 4 * (argc + 2), and the enumeration callback five arguments, which
 *    sa2_guest_stdcall checks on its side;
 *  * the enumeration hands its callback one call per profile entry in the
 *    profile's order: lpGUID NULL exactly for an entry with no GUID, the
 *    entry's 16 GUID bytes otherwise, its description and name byte for byte,
 *    and as HMONITOR NULL for an entry on no monitor and what d3d9's
 *    GetAdapterMonitor(k) answers for an entry on adapter k's;
 *  * a callback that returns FALSE ends the enumeration;
 *  * the game's callback, on the game's context, keeps the GUID of the last
 *    entry on adapter 0's monitor and sets the found flag, or leaves the
 *    context zeroed when no entry is on it -- and DirectDrawCreate on that
 *    context answers, naming native's device;
 *  * QueryInterface(IID_IDirectDraw7) hands back a second interface;
 *  * GetAvailableVidMem answers the profile's recorded HRESULT, and its total
 *    on success or nothing on failure;
 *  * Release takes IDirectDraw7's count to 0 and leaves IDirectDraw's at 1,
 *    which is native's end state (native-outcomes-v1a:29007, {0,0,0,0,1});
 *  * every argument the image never passes, DirectDrawCreate on every device
 *    that is not native's, the named slots the image never calls and an
 *    unnamed slot each refuse by name, and the note says which refusal it is.
 *
 * Nothing written to the JSON names a device or carries a measurement: it
 * holds entry indices, counts and booleans, never a GUID, a string, a
 * video-memory figure or a recorded HRESULT, because a receipt built from it
 * must not copy the host's inventory.
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_ddraw.h"
#include "../runtime/sa2_devprofile.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_trap.h"

#define STACK_TOP  0x03eeff00u
#define SCRATCH    0x03004000u    /* guest scratch, clear of every fabricated block */
#define CB_VA      0x03006000u    /* the recording callback's function-table address */

/* The game's callback and the layout of the context it is handed, from the
 * decode in runtime/sa2_ddraw.h: GUID at +0, HMONITOR at +0x10, found at +0x14. */
#define GAME_CALLBACK  0x00865C20u
#define CTX_GUID       0x00u
#define CTX_HMONITOR   0x10u
#define CTX_FOUND      0x14u
#define CAPS_LOCAL_VIDEO 0x10004000u   /* DDSCAPS_VIDEOMEMORY | DDSCAPS_LOCALVIDMEM */
#define ERROR_MOD_NOT_FOUND 126u
#define UNWRITTEN      0xBADBAD00u     /* an out slot's value before a call */

/* Generated by tools/s4/lift_extra.py; absent unless that file is linked. */
extern int sa2_s4_extra_register(void) __attribute__((weak));

static uint32_t g_fail;
static char g_first_fail[256];
static uint32_t g_checks;

static void check(int cond, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void check(int cond, const char *fmt, ...) {
    g_checks++;
    if (cond) return;
    g_fail++;
    if (!g_first_fail[0]) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(g_first_fail, sizeof g_first_fail, fmt, ap);
        va_end(ap);
    }
}

static void jstr(FILE *f, const char *s) {
    fputc('"', f);
    for (const char *p = s; *p; p++) {
        unsigned char c = (unsigned char)*p;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c < 0x20 || c >= 0x7F) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}

/* The case's facts, written as they are established: booleans, indices and
 * counts only (the header says why). */
static FILE *g_facts;
static uint32_t g_nfacts;

static void fact_key(const char *k) {
    fprintf(g_facts, "%s\n   ", g_nfacts++ ? "," : "");
    jstr(g_facts, k);
    fputs(": ", g_facts);
}
static void fb(const char *k, int v) {
    if (!g_facts) return;
    fact_key(k);
    fputs(v ? "true" : "false", g_facts);
}
static void fi(const char *k, long v) {
    if (!g_facts) return;
    fact_key(k);
    fprintf(g_facts, "%ld", v);
}

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}
static void gstr(uint32_t a, char *out, size_t cap) {
    size_t i = 0;
    for (; i + 1 < cap; i++) {
        uint8_t c = 0;
        if (sa2_peek(a + (uint32_t)i, 1, &c) != SA2_OK || !c) break;
        out[i] = (char)c;
    }
    out[i] = 0;
}

/* ------------------------------------------------------ driving the guest */

static sa2_call_result_t g_res;
/* Calls that answered, those that popped the wrong number of bytes, and the
 * first of those: the argc break's case asserts it is GetAvailableVidMem's. */
static uint32_t g_pops, g_pops_bad, g_bad_delta, g_bad_want;
static char g_bad_what[64];

/* A native returns by handing `memory` back, so driven straight from
 * `sa2_call` a call that *answered* comes back as SA2_TRAP_CONTROL_LEFT at the
 * thunk with sa2_call's own "returned with EIP" note, and a refusal comes back
 * with the trap's note. The note is the only thing that tells the two apart
 * for a slot whose thunk is SA2_DD_THUNK_BASE itself, which is where the
 * runtime raises its refusals. */
static int last_answered(void) {
    return g_res.status == SA2_TRAP_CONTROL_LEFT && !strncmp(g_res.note, "returned with EIP", 17);
}
static int answered(uint32_t thunk) { return last_answered() && g_res.exit_pc == thunk; }

/* Enter `thunk` with `argc` stdcall arguments after a return address, and
 * record whether the callee popped exactly `pop` bytes. */
static uint32_t enter(uint32_t thunk, uint32_t argc, const uint32_t *argv, uint32_t pop,
                      const char *what) {
    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    const uint32_t esp0 = STACK_TOP - 4u * (argc + 1u);
    st.gpr[SA2_ESP] = esp0;
    for (uint32_t i = 0; i < argc; i++) pk32(esp0 + 4u + 4u * i, argv[i]);
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    sa2_call(thunk, &st, 30, &g_res);
    if (answered(thunk)) {
        g_pops++;
        const uint32_t delta = st.gpr[SA2_ESP] - esp0;
        if (delta != pop) {
            if (!g_pops_bad) {
                snprintf(g_bad_what, sizeof g_bad_what, "%s", what);
                g_bad_delta = delta;
                g_bad_want = pop;
            }
            g_pops_bad++;
        }
    }
    return st.gpr[SA2_EAX];
}

static uint32_t call_import(uint32_t thunk, uint32_t argc, const uint32_t *argv,
                            const char *what) {
    return enter(thunk, argc, argv, 4u + 4u * argc, what);
}

/* A method: `this` and the arguments, the vtable read out of the object and
 * the thunk out of the vtable, exactly as a lifted `call [reg+N]` does. */
static uint32_t call_slot(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv,
                          const char *what, uint32_t *thunk_out) {
    uint32_t a[8] = {self};
    for (uint32_t i = 0; i < argc && i < 7u; i++) a[1 + i] = argv[i];
    const uint32_t thunk = peek32(peek32(self) + 4u * slot);
    if (thunk_out) *thunk_out = thunk;
    return enter(thunk, argc + 1u, a, 4u * (argc + 2u), what);
}

#define IMPORT(th, n, what, ...)       call_import(th, n, (const uint32_t[]){__VA_ARGS__}, what)
#define METHOD(self, slot, n, what, ...) \
    call_slot(self, slot, n, (const uint32_t[]){__VA_ARGS__}, what, NULL)

/* A refusal by name: the call trapped, and its note says `needle`, the words
 * of the one refusal the case means. */
static int refused_with(uint32_t thunk, const char *needle) {
    return !answered(thunk) && g_res.status == SA2_TRAP_CONTROL_LEFT
           && strstr(g_res.note, needle) != NULL;
}
static void expect_refusal(uint32_t thunk, const char *needle, const char *key) {
    const int ok = refused_with(thunk, needle);
    check(ok, "%s: no refusal naming \"%s\" (status %u at %#010x, note \"%s\")", key, needle,
          g_res.status, g_res.exit_pc, g_res.note);
    fb(key, ok);
}

/* ------------------------------------------------- the recording callback */

#define REC_MAX 8
static struct {
    uint32_t guid_ptr, desc_ptr, name_ptr, ctx, hm;
    uint8_t  guid[16];
    char     desc[SA2_DD_STR_BYTES], name[SA2_DD_STR_BYTES];
} g_rec[REC_MAX];
static uint32_t g_rec_n;
static uint32_t g_rec_stop_at;      /* return FALSE on this 1-based call; 0 never */

/* An LPDDENUMCALLBACKEXA registered as a native: it records what it was
 * handed, answers TRUE (or FALSE when told to) and pops its five arguments,
 * because sa2_guest_stdcall checks that the callee did. */
static void *probe_callback(void *s, uint32_t pc, void *m) {
    (void)pc;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t a[5];
    for (uint32_t k = 0; k < 5u; k++) a[k] = peek32(esp + 4u + 4u * k);
    if (g_rec_n < REC_MAX) {
        g_rec[g_rec_n].guid_ptr = a[0];
        g_rec[g_rec_n].desc_ptr = a[1];
        g_rec[g_rec_n].name_ptr = a[2];
        g_rec[g_rec_n].ctx = a[3];
        g_rec[g_rec_n].hm = a[4];
        memset(g_rec[g_rec_n].guid, 0, 16);
        if (a[0]) sa2_peek(a[0], 16, g_rec[g_rec_n].guid);
        gstr(a[1], g_rec[g_rec_n].desc, sizeof g_rec[g_rec_n].desc);
        gstr(a[2], g_rec[g_rec_n].name, sizeof g_rec[g_rec_n].name);
    }
    g_rec_n++;
    const uint32_t ret = (g_rec_stop_at && g_rec_n == g_rec_stop_at) ? 0u : 1u;
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * 5u);
    return m;
}

/* -------------------------------------------------------- the one query */

static const uint8_t ZERO_GUID[16];

/* The profile, which is the expectation, and what the query saw. */
typedef struct {
    /* From the profile. The game's callback copies the GUID of every entry
     * whose HMONITOR matches adapter 0's, so the entry it keeps is the last
     * one on adapter 0's monitor; with none, the context stays zeroed, which
     * names the primary display driver, the first entry with no GUID. */
    uint32_t n, on_adapter0;
    int32_t  primary, display, native;
    const uint8_t *native_bytes;       /* what native's DirectDrawCreate was handed */
    /* What the query saw. */
    uint32_t hmon[4];
    uint32_t h, p_enum, p_create, p_free;
    int      enum_answered;
    uint32_t callbacks;
    int      ctx_ok, guid_ptrs_ok, guids_ok, hms_ok, hms_all_null, strings_ok;
    int      stop_ok;
    int      game_enum_answered, found, ctx_zero, kept_native;
    int      create_answered, create_wrote;
    int32_t  create_entry;             /* the instance's device; -2 when none was made */
    char     create_note[SA2_TRAP_NOTE_MAX];
    uint32_t dd, dd7;
    int      qi_ok, vidmem_answered, vidmem_hr_ok, vidmem_total_ok;
    int      release_ok, counts_ok, free_ok;
} query;

static void expect_from_profile(query *q) {
    q->n = sa2_dp_ddraw_count();
    q->primary = q->display = -1;
    for (uint32_t i = 0; i < q->n; i++) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        if (sa2_dp_ddraw_monitor_adapter(i) == 0) { q->display = (int32_t)i; q->on_adapter0++; }
        if (!d->has_guid && q->primary < 0) q->primary = (int32_t)i;
    }
    q->native = q->display >= 0 ? q->display : q->primary;
    q->native_bytes = q->display >= 0 ? sa2_dp_ddraw_device((uint32_t)q->display)->guid
                                      : ZERO_GUID;
}

#define Q_CTX    (SCRATCH + 0x100u)   /* the recording callback's context */
#define Q_GCTX   (SCRATCH + 0x200u)   /* the game's context, laid out as the game does */
#define Q_DD     (SCRATCH + 0x400u)
#define Q_IID    (SCRATCH + 0x500u)
#define Q_DD7    (SCRATCH + 0x520u)
#define Q_CAPS   (SCRATCH + 0x540u)
#define Q_TOTAL  (SCRATCH + 0x560u)

/* The query 0x00865D20 makes, step by step, recording what each step saw and
 * stopping where the next step has nothing to call. The case decides which
 * observations must be native's and which a break must have flipped. */
static void drive(query *q) {
    memset(q, 0, sizeof *q);
    expect_from_profile(q);
    q->create_entry = -2;

    /* 1. the HMONITORs d3d9 hands the game, through its own vtable */
    const sa2_import_info *c9 = sa2_import_by_name("d3d9.dll", "Direct3DCreate9");
    const uint32_t d3d = c9 ? IMPORT(c9->thunk, 1, "Direct3DCreate9", 32u) : 0u;
    for (uint32_t k = 0; d3d && k < 4u; k++)
        q->hmon[k] = METHOD(d3d, 15, 1, "IDirect3D9::GetAdapterMonitor", k);

    /* 2. the module, the way 0x00865D4A..0x00865D9E gets it */
    const sa2_import_info *ll = sa2_import_by_name("KERNEL32.dll", "LoadLibraryA");
    const sa2_import_info *gp = sa2_import_by_name("KERNEL32.dll", "GetProcAddress");
    const sa2_import_info *fl = sa2_import_by_name("KERNEL32.dll", "FreeLibrary");
    if (fl) q->p_free = fl->thunk;
    sa2_poke(SCRATCH + 0x000u, 10, "ddraw.dll");
    sa2_poke(SCRATCH + 0x010u, 23, "DirectDrawEnumerateExA");
    sa2_poke(SCRATCH + 0x030u, 17, "DirectDrawCreate");
    if (ll) q->h = IMPORT(ll->thunk, 1, "LoadLibraryA", SCRATCH + 0x000u);
    if (q->h && gp) {
        q->p_enum = IMPORT(gp->thunk, 2, "GetProcAddress", q->h, SCRATCH + 0x010u);
        q->p_create = IMPORT(gp->thunk, 2, "GetProcAddress", q->h, SCRATCH + 0x030u);
    }
    if (!q->p_enum || !q->p_create) return;

    /* 3. the enumeration, through a callback that records what it is handed */
    g_rec_n = 0;
    g_rec_stop_at = 0;
    q->enum_answered = IMPORT(q->p_enum, 3, "DirectDrawEnumerateExA", CB_VA, Q_CTX, 1u) == 0
                       && answered(q->p_enum);
    q->callbacks = g_rec_n;
    q->ctx_ok = q->guid_ptrs_ok = q->guids_ok = q->hms_ok = q->hms_all_null = q->strings_ok = 1;
    for (uint32_t i = 0; i < q->n && i < g_rec_n && i < REC_MAX; i++) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        if (g_rec[i].ctx != Q_CTX) q->ctx_ok = 0;
        if (d->has_guid) {
            if (!g_rec[i].guid_ptr) q->guid_ptrs_ok = 0;
            if (memcmp(g_rec[i].guid, d->guid, 16)) q->guids_ok = 0;
        } else if (g_rec[i].guid_ptr) {
            q->guid_ptrs_ok = 0;
        }
        const int32_t k = sa2_dp_ddraw_monitor_adapter(i);
        const uint32_t want = k < 0 ? 0u : (k < 4 ? q->hmon[k] : 0xFFFFFFFFu);
        if (g_rec[i].hm != want) q->hms_ok = 0;
        if (g_rec[i].hm) q->hms_all_null = 0;
        if (strcmp(g_rec[i].desc, d->description) || strcmp(g_rec[i].name, d->name))
            q->strings_ok = 0;
    }

    /* 4. FALSE ends it */
    g_rec_n = 0;
    g_rec_stop_at = 1;
    IMPORT(q->p_enum, 3, "DirectDrawEnumerateExA", CB_VA, Q_CTX, 1u);
    q->stop_ok = g_rec_n == (q->n ? 1u : 0u);
    g_rec_stop_at = 0;

    /* 5. the game's own callback on the game's context */
    sa2_guest_fill(Q_GCTX, 0, 0x18u);
    pk32(Q_GCTX + CTX_HMONITOR, q->hmon[0]);
    q->game_enum_answered = IMPORT(q->p_enum, 3, "DirectDrawEnumerateExA", GAME_CALLBACK,
                                   Q_GCTX, 1u) == 0 && answered(q->p_enum);
    {
        uint8_t found = 0xFFu, g[16];
        sa2_peek(Q_GCTX + CTX_FOUND, 1, &found);
        sa2_peek(Q_GCTX + CTX_GUID, 16, g);
        q->found = found;
        q->ctx_zero = !memcmp(g, ZERO_GUID, 16);
        q->kept_native = !memcmp(g, q->native_bytes, 16);
    }

    /* 6. DirectDrawCreate on that context, as 0x00865DB8 calls it */
    pk32(Q_DD, 0);
    const uint32_t ni0 = sa2_ddraw_instance_count();
    q->create_answered = IMPORT(q->p_create, 3, "DirectDrawCreate", Q_GCTX + CTX_GUID, Q_DD, 0u)
                         == 0 && answered(q->p_create);
    if (!answered(q->p_create)) snprintf(q->create_note, sizeof q->create_note, "%s", g_res.note);
    q->dd = peek32(Q_DD);
    q->create_wrote = q->dd != 0 && q->dd != 0xDEADBEEFu;
    if (sa2_ddraw_instance_count() > ni0)
        q->create_entry = sa2_ddraw_instance_at(sa2_ddraw_instance_count() - 1u)->device;
    if (!q->create_wrote) return;

    /* 7. QueryInterface(IID_IDirectDraw7), as 0x00865DCD calls it */
    static const uint8_t IID_DD7[16] = {0xC0,0x5E,0xE6,0x15,0x9C,0x3B,0xD2,0x11,
                                        0xB9,0x2F,0x00,0x60,0x97,0x97,0xEA,0x5B};
    sa2_poke(Q_IID, 16, IID_DD7);
    pk32(Q_DD7, UNWRITTEN);
    const uint32_t qhr = METHOD(q->dd, 0, 2, "IDirectDraw::QueryInterface", Q_IID, Q_DD7);
    q->dd7 = peek32(Q_DD7);
    q->qi_ok = qhr == 0 && last_answered() && q->dd7 != UNWRITTEN && q->dd7 && q->dd7 != q->dd;
    if (!q->qi_ok) return;

    /* 8. GetAvailableVidMem, as 0x00865E03 calls it */
    sa2_guest_fill(Q_CAPS, 0, 16u);
    pk32(Q_CAPS, CAPS_LOCAL_VIDEO);
    pk32(Q_TOTAL, UNWRITTEN);
    const uint32_t vhr = METHOD(q->dd7, 23, 3, "IDirectDraw7::GetAvailableVidMem", Q_CAPS,
                                Q_TOTAL, 0u);
    const uint32_t total = peek32(Q_TOTAL);
    q->vidmem_answered = last_answered();
    q->vidmem_hr_ok = vhr == sa2_dp_ddraw_vidmem_hr();
    q->vidmem_total_ok = (sa2_dp_ddraw_vidmem_hr() & 0x80000000u)
                             ? total == UNWRITTEN
                             : total == sa2_dp_ddraw_vidmem_total();

    /* 9. Release, as 0x00865E1B calls it, and native's end state */
    q->release_ok = METHOD(q->dd7, 2, 0, "IDirectDraw7::Release", 0u) == 0 && last_answered();
    {
        const uint32_t ni = sa2_ddraw_instance_count();
        const sa2_dd_instance *in = ni ? sa2_ddraw_instance_at(ni - 1u) : NULL;
        q->counts_ok = in && in->ref7 == 0 && in->ref1 == 1;
    }
    if (q->p_free) q->free_ok = IMPORT(q->p_free, 1, "FreeLibrary", q->h) == 1u;
}

static void query_facts(const query *q) {
    fi("devices", (long)q->n);
    fi("on_adapter0_monitor", (long)q->on_adapter0);
    fi("primary_entry", q->primary);
    fi("native_entry", q->native);
    fb("hmonitor_adapter0_nonnull", q->hmon[0] != 0);
    fb("module_loaded", q->h != 0);
    fb("entry_points_resolved", q->p_enum && q->p_create);
    fb("enumeration_answered", q->enum_answered);
    fi("callbacks", (long)q->callbacks);
    fb("contexts_passed_through", q->ctx_ok);
    fb("guid_pointers_as_profile", q->guid_ptrs_ok);
    fb("guids_as_profile", q->guids_ok);
    fb("hmonitors_as_d3d9", q->hms_ok);
    fb("hmonitors_all_null", q->hms_all_null);
    fb("strings_as_profile", q->strings_ok);
    fb("stop_on_false", q->stop_ok);
    fb("stop_on_false_exercised", q->n >= 2u);
    fb("game_enumeration_answered", q->game_enum_answered);
    fi("found_flag", q->found);
    fb("context_zeroed", q->ctx_zero);
    fb("context_holds_natives_bytes", q->kept_native);
    fb("create_answered", q->create_answered);
    fb("create_wrote_interface", q->create_wrote);
    fi("create_entry", q->create_entry);
    fb("qi_second_interface", q->qi_ok);
    fb("vidmem_answered", q->vidmem_answered);
    fb("vidmem_hr_matches_profile", q->vidmem_hr_ok);
    fb("vidmem_total_matches_profile", q->vidmem_total_ok);
    fb("release_to_zero", q->release_ok);
    fb("counts_are_natives", q->counts_ok);
    fb("free_library_true", q->free_ok);
}

/* What the image never passes, the devices native's DirectDrawCreate was not
 * handed, and the slots the image never calls. */
static void refusals(const query *q) {
    IMPORT(q->p_enum, 3, "DirectDrawEnumerateExA", CB_VA, Q_CTX, 0u);
    expect_refusal(q->p_enum, "dwFlags", "refuses_flags_0");
    IMPORT(q->p_enum, 3, "DirectDrawEnumerateExA", 0u, Q_CTX, 1u);
    expect_refusal(q->p_enum, "lpCallback", "refuses_null_callback");
    /* The game's callback reads its context at once (0x00865C24): refused by
     * name before any callback runs, not a fault at 0x10. */
    IMPORT(q->p_enum, 3, "DirectDrawEnumerateExA", GAME_CALLBACK, 0u, 1u);
    expect_refusal(q->p_enum, "lpContext", "refuses_null_context");

    const uint32_t made = sa2_ddraw_instance_count();
    sa2_guest_fill(SCRATCH + 0x600u, 0x5A, 16u);
    IMPORT(q->p_create, 3, "DirectDrawCreate", SCRATCH + 0x600u, SCRATCH + 0x620u, 0u);
    expect_refusal(q->p_create, "names no DirectDraw device", "refuses_unknown_guid");
    IMPORT(q->p_create, 3, "DirectDrawCreate", Q_GCTX + CTX_GUID, SCRATCH + 0x620u, SCRATCH);
    expect_refusal(q->p_create, "pUnkOuter", "refuses_outer_unknown");
    IMPORT(q->p_create, 3, "DirectDrawCreate", Q_GCTX + CTX_GUID, 0u, 0u);
    expect_refusal(q->p_create, "lplpDD", "refuses_null_out");
    /* Every enumerated device whose bytes are not native's: the profile holds
     * no video memory for it (runtime/sa2_ddraw.c, refuse_not_native). The
     * primary display driver is asked for by the zeroed context. */
    uint32_t others = 0, others_ok = 0;
    for (uint32_t i = 0; i < q->n; i++) {
        const sa2_dp_ddraw *d = sa2_dp_ddraw_device(i);
        const uint8_t *bytes = d->has_guid ? d->guid : ZERO_GUID;
        if (!memcmp(bytes, q->native_bytes, 16)) continue;
        sa2_poke(SCRATCH + 0x600u, 16, bytes);
        IMPORT(q->p_create, 3, "DirectDrawCreate", SCRATCH + 0x600u, SCRATCH + 0x620u, 0u);
        others++;
        const int ok = refused_with(q->p_create, "native's DirectDrawCreate");
        others_ok += (uint32_t)ok;
        check(ok, "DirectDrawCreate on profile entry %u, which is not native's device, did not "
                  "refuse as such: %s", i, g_res.note);
    }
    fi("devices_not_natives", (long)others);
    fb("refuses_devices_not_natives", others_ok == others);
    check(sa2_ddraw_instance_count() == made,
          "a refused DirectDrawCreate still made an object (%u before, %u after)", made,
          sa2_ddraw_instance_count());
    fb("refused_creates_made_nothing", sa2_ddraw_instance_count() == made);

    if (q->create_wrote) {
        /* IID_IDirectDraw, the interface DirectDrawCreate already is. */
        static const uint8_t IID_DD[16] = {0x80,0xDB,0x14,0x6C,0x33,0xA7,0xCE,0x11,
                                           0xA5,0x21,0x00,0x20,0xAF,0x0B,0xE5,0x60};
        uint32_t th = 0;
        sa2_poke(SCRATCH + 0x640u, 16, IID_DD);
        call_slot(q->dd, 0, 2, (const uint32_t[]){SCRATCH + 0x640u, SCRATCH + 0x660u},
                  "IDirectDraw::QueryInterface(IID_IDirectDraw)", &th);
        expect_refusal(th, "riid", "refuses_other_iid");
        call_slot(q->dd, 2, 0, (const uint32_t[]){0u}, "IDirectDraw::Release", &th);
        expect_refusal(th, "never calls this slot", "refuses_directdraw_release");
    }
    if (q->qi_ok) {
        uint32_t th = 0;
        call_slot(q->dd7, 23, 3, (const uint32_t[]){Q_CAPS, Q_TOTAL, SCRATCH + 0x680u},
                  "IDirectDraw7::GetAvailableVidMem(lpdwFree)", &th);
        expect_refusal(th, "lpdwFree", "refuses_free_pointer");
        call_slot(q->dd7, 11, 2, (const uint32_t[]){SCRATCH + 0x700u, 0u},
                  "IDirectDraw7::GetCaps", &th);
        expect_refusal(th, "never calls this slot", "refuses_getcaps");
        call_slot(q->dd7, 30, 0, (const uint32_t[]){0u}, "IDirectDraw7 slot 30", &th);
        expect_refusal(th, "slot 30", "refuses_unnamed_slot");
    }
}

/* ------------------------------------------------------------- the cases */

typedef struct probe_case probe_case;
struct probe_case {
    const char *name;
    int         device;        /* SA2_DDRAW_DEVICE=1 */
    const char *profile;       /* a key of the probe's profiles, "capture", or NULL: the default */
    const char *brk;           /* SA2_DDRAW_BREAK, or NULL */
    void      (*run)(const probe_case *);   /* NULL: the configuration itself must refuse */
    const char *refusal;       /* words the configuration's note must hold, beside the path */
    int         want_match;    /* the profile must have an entry on adapter 0's monitor */
};

static int g_require_match;

static void pops_are_exact(void) {
    check(g_pops_bad == 0, "%u of %u calls popped the wrong number of bytes; the first, %s, "
                           "popped %u and wanted %u", g_pops_bad, g_pops, g_bad_what,
          g_bad_delta, g_bad_want);
}

/* The whole query answering as native's did, then every refusal. */
static void case_native(const probe_case *pc) {
    query q;
    drive(&q);
    query_facts(&q);
    check(q.n > 0, "the device profile %s lists no DirectDraw device", sa2_devprofile_source());
    if (pc->want_match || (pc->profile && !strcmp(pc->profile, "capture") && g_require_match))
        check(q.display >= 0, "the profile %s has no entry on adapter 0's monitor, so the "
                              "HMONITOR match is not exercised", sa2_devprofile_source());
    check(q.hmon[0] != 0, "IDirect3D9::GetAdapterMonitor(0) answered NULL");
    check(q.h != 0, "LoadLibraryA(\"ddraw.dll\") answered 0 with SA2_DDRAW_DEVICE=1");
    {
        const sa2_import_info *ll = sa2_import_by_name("KERNEL32.dll", "LoadLibraryA");
        const int unmarked = ll && ll->calls > 0u && ll->refused_calls == 0u;
        fb("loadlibrary_not_marked_refused", unmarked);
        check(unmarked, "with SA2_DDRAW_DEVICE=1 LoadLibraryA's import row counts %u calls and "
                        "%u marked refusals; the load must answer unmarked",
              ll ? ll->calls : 0u, ll ? ll->refused_calls : 0u);
    }
    check(q.p_enum && q.p_create, "GetProcAddress did not resolve both entry points");
    if (!q.p_enum || !q.p_create) return;
    check(q.enum_answered, "DirectDrawEnumerateExA did not answer DD_OK");
    check(q.callbacks == q.n, "the enumeration made %u callbacks and the profile records %u "
                              "devices", q.callbacks, q.n);
    check(q.ctx_ok, "a callback was handed another context than the caller's");
    check(q.guid_ptrs_ok && q.guids_ok, "a callback's lpGUID is not the profile's: NULL exactly "
                                        "for an entry with no GUID, its bytes otherwise");
    check(q.hms_ok, "a callback's HMONITOR is not what d3d9 answers for the profile's adapter");
    check(q.strings_ok, "a callback's description or name is not the profile's bytes");
    check(q.stop_ok, "a callback that answered FALSE was followed by more");
    check(q.game_enum_answered, "DirectDrawEnumerateExA with the game's callback did not answer");
    check(q.kept_native && q.found == (q.display >= 0 ? 1 : 0),
          "the game's callback left %s in its context with found %d; native's DirectDrawCreate "
          "was handed entry %d's", q.ctx_zero ? "zeros" : "other bytes", q.found, q.native);
    check(q.create_answered && q.create_wrote, "DirectDrawCreate on the game's context did not "
                                               "answer with an IDirectDraw: %s", q.create_note);
    check(q.create_entry == q.native, "DirectDrawCreate named profile entry %d, and native's "
                                      "was %d", q.create_entry, q.native);
    check(q.qi_ok, "QueryInterface(IID_IDirectDraw7) did not hand back a second interface");
    check(q.vidmem_answered && q.vidmem_hr_ok, "GetAvailableVidMem did not answer the profile's "
                                               "HRESULT");
    check(q.vidmem_total_ok, "GetAvailableVidMem's total is not the profile's, or a failure "
                             "wrote one");
    check(q.release_ok && q.counts_ok, "the counts are not native's end state: IDirectDraw7 at 0, "
                                       "IDirectDraw at 1");
    check(q.free_ok, "FreeLibrary(ddraw) did not answer TRUE");
    refusals(&q);
    pops_are_exact();
}

/* Each break, and the one observation it flips. */
static void brk_argc(const probe_case *pc) {
    (void)pc;
    query q;
    drive(&q);
    query_facts(&q);
    const int seen = g_pops_bad == 1u && !strcmp(g_bad_what, "IDirectDraw7::GetAvailableVidMem")
                     && g_bad_delta == 16u && g_bad_want == 20u;
    fb("the_one_wrong_pop_is_getavailablevidmems", seen);
    check(q.vidmem_answered, "GetAvailableVidMem did not answer under the argc break");
    check(seen, "the argc break was not seen where it was made: %u wrong pop(s), the first %s "
                "at %u of %u bytes", g_pops_bad, g_bad_what[0] ? g_bad_what : "none",
          g_bad_delta, g_bad_want);
}

static void brk_noout(const probe_case *pc) {
    (void)pc;
    query q;
    drive(&q);
    query_facts(&q);
    check(q.create_answered && !q.create_wrote,
          "under the noout break DirectDrawCreate %s", q.create_answered
              ? "still wrote an IDirectDraw" : "did not answer DD_OK");
    pops_are_exact();
}

static void brk_nomatch(const probe_case *pc) {
    (void)pc;
    query q;
    drive(&q);
    query_facts(&q);
    check(q.display >= 0, "brk-nomatch needs an entry on adapter 0's monitor, and %s has none",
          sa2_devprofile_source());
    check(q.hms_all_null && !q.hms_ok, "under the nomatch break a display was still handed its "
                                       "HMONITOR");
    check(q.found == 0 && q.ctx_zero && !q.kept_native, "under the nomatch break the game's "
                                                        "callback still matched");
    const int refused = !q.create_answered && strstr(q.create_note, "native's DirectDrawCreate");
    fb("create_refused_as_not_natives", refused);
    check(refused, "under the nomatch break DirectDrawCreate on the zeroed context did not "
                   "refuse as not native's device: %s", q.create_note);
    pops_are_exact();
}

/* The device off with the default profile: legal, and absent. */
static void case_off(const probe_case *pc) {
    (void)pc;
    const sa2_import_info *ll = sa2_import_by_name("KERNEL32.dll", "LoadLibraryA");
    const sa2_import_info *le = sa2_import_by_name("KERNEL32.dll", "GetLastError");
    check(ll && le, "LoadLibraryA or GetLastError is not bound");
    if (!ll || !le) return;
    sa2_poke(SCRATCH + 0x000u, 10, "ddraw.dll");
    const uint32_t h = IMPORT(ll->thunk, 1, "LoadLibraryA", SCRATCH + 0x000u);
    const int ll_answered = answered(ll->thunk);
    const uint32_t err = call_import(le->thunk, 0, NULL, "GetLastError");
    const uint32_t nref = sa2_loadlibrary_refused_count();
    const char *who = nref ? sa2_loadlibrary_refused_at(nref - 1u) : NULL;
    const char *why = nref ? sa2_loadlibrary_refused_why(nref - 1u) : NULL;
    sa2_dd_stats ds;
    sa2_ddraw_stats_get(&ds);
    /* Only the switch can show here: the case gets no ddraw.dll handle, so no
     * entry point or method is reachable and every count the module keeps
     * stays 0 whatever the module does. */
    const int off = !ds.enabled;
    /* The absence is marked against LoadLibraryA's own row, once, answering 0
     * (runtime/sa2_imports.c, imp_LoadLibraryA). */
    const int marked = ll->refused_calls == 1u && ll->first_refused_ret == 0u;
    fb("loadlibrary_answered_0", ll_answered && h == 0);
    fb("loadlibrary_marked_refused_once", marked);
    fb("last_error_mod_not_found", err == ERROR_MOD_NOT_FOUND);
    fb("refused_load_names_the_switch", who && !strcmp(who, "ddraw.dll") && why
                                        && strstr(why, "SA2_DDRAW_DEVICE"));
    fb("module_says_off", off);
    check(ll_answered && h == 0, "LoadLibraryA(\"ddraw.dll\") answered %#010x with "
                                 "SA2_DDRAW_DEVICE unset", h);
    check(err == ERROR_MOD_NOT_FOUND, "GetLastError after it is %u, not ERROR_MOD_NOT_FOUND "
                                      "(126)", err);
    check(who && !strcmp(who, "ddraw.dll") && why && strstr(why, "SA2_DDRAW_DEVICE"),
          "the refused load is not ddraw.dll's naming SA2_DDRAW_DEVICE: %s (%s)",
          who ? who : "none recorded", why ? why : "");
    check(off, "with SA2_DDRAW_DEVICE unset the module says it is on");
    check(marked, "LoadLibraryA's import row counts %u marked refusals, the first answering "
                  "%#x; the absence must be marked once, against LoadLibraryA, answering 0",
          ll->refused_calls, ll->first_refused_ret);
    pops_are_exact();
}

/* refuse-count's needle: the C reader's count refusal in its own words
 * (runtime/sa2_devprofile.c). "ddraw_device" alone is also in the grammar's
 * refusals of a malformed record, which would pass the case with the limit
 * never reached. Written by main before any case runs. */
static char g_count_refusal[64];

static const probe_case CASES[] = {
    {"off",               0, NULL,            NULL,      case_off,    NULL, 0},
    {"default",           1, NULL,            NULL,      case_native, NULL, 0},
    {"match",             1, "match",         NULL,      case_native, NULL, 1},
    {"vidmem-failed",     1, "vidmem-failed", NULL,      case_native, NULL, 0},
    {"capture",           1, "capture",       NULL,      case_native, NULL, 0},
    {"brk-argc",          1, NULL,            "argc",    brk_argc,    NULL, 0},
    {"brk-noout",         1, NULL,            "noout",   brk_noout,   NULL, 0},
    {"brk-nomatch",       1, "match",         "nomatch", brk_nomatch, NULL, 1},
    {"refuse-count",      1, "count",         NULL,      NULL,        g_count_refusal, 0},
    {"refuse-device-off", 0, "match",         NULL,      NULL,        "SA2_DDRAW_DEVICE", 0},
};

/* --------------------------------------------------------------- the runner */

static char g_dir[512];
static const char *g_capture;

static void profile_path(const char *key, char *out, size_t cap) {
    if (!strcmp(key, "capture")) snprintf(out, cap, "%s", g_capture ? g_capture : "");
    else snprintf(out, cap, "%s/ddraw-probe.%s.profile.txt", g_dir, key);
}

static char *slurp(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    static char buf[1 << 16];
    const size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    return buf;
}

/* One case, in this (forked) process: environment, configuration, binding,
 * the case, its record.
 *
 * A case the configuration must refuse gets sa2_imports_config_from_env and
 * nothing else, the one configuration call a real run makes
 * (harness/crt_probe.c), so the refusal it sees is that function's. Were the
 * ddraw module configured there before the profile, the module would still
 * see the default profile and not trap, and a second call made here would
 * trap in its place and pass the case. A case that runs then calls the
 * profile's configuration and the module's again (both are idempotent), in
 * the order the module needs, so it tests the module whatever that function
 * calls. */
static int run_case(const probe_case *pc, const char *frag) {
    if (pc->device) setenv("SA2_DDRAW_DEVICE", "1", 1); else unsetenv("SA2_DDRAW_DEVICE");
    if (pc->brk) setenv("SA2_DDRAW_BREAK", pc->brk, 1); else unsetenv("SA2_DDRAW_BREAK");
    if (pc->profile) {
        char p[600];
        profile_path(pc->profile, p, sizeof p);
        setenv("SA2_DEVICE_PROFILE", p, 1);
    } else {
        unsetenv("SA2_DEVICE_PROFILE");
    }
    sa2_imports_config_from_env();
    if (!pc->run) {
        fprintf(stderr, "the configuration accepted a profile it must refuse\n");
        return 1;
    }
    sa2_devprofile_config_from_env();
    sa2_ddraw_config_from_env();
    if (sa2_imports_bind() <= 0) { fprintf(stderr, "imports did not bind\n"); return 2; }
    /* The record is built in memory and written whole at the end, so a child
     * that dies mid-case leaves no half-written fragment for the parent to
     * splice into its JSON; it reports the exit code instead. */
    char *mem = NULL;
    size_t mem_n = 0;
    FILE *f = open_memstream(&mem, &mem_n);
    if (!f) return 2;
    fprintf(f, "{\"case\": ");
    jstr(f, pc->name);
    fprintf(f, ", \"break\": ");
    jstr(f, sa2_ddraw_break_name());
    fprintf(f, ", \"profile\": ");
    jstr(f, sa2_devprofile_source());
    fprintf(f, ", \"profile_sha256\": ");
    jstr(f, sa2_devprofile_sha256());
    fprintf(f, ",\n  \"facts\": {");
    g_facts = f;
    pc->run(pc);
    g_facts = NULL;
    fprintf(f, "},\n  \"pops\": {\"calls\": %u, \"wrong\": %u}, \"ok\": %s, \"checks\": %u, "
               "\"failures\": %u, \"first_failure\": ", g_pops, g_pops_bad,
            g_fail ? "false" : "true", g_checks, g_fail);
    jstr(f, g_first_fail);
    fprintf(f, "}");
    if (fclose(f)) return 2;
    FILE *out = fopen(frag, "w");
    if (!out) return 2;
    const int written = fwrite(mem, 1, mem_n, out) == mem_n;
    free(mem);
    if (fclose(out) || !written) return 2;
    return g_fail ? 1 : 0;
}

/* Every named slot with its declared count: what
 * `tools/s4/ddraw_surface.py --argc` compares with the image. Written by the
 * parent, which never configured a break, so the counts are the published
 * ones. */
static void publish_methods(FILE *f) {
    fprintf(f, " \"ddraw\": {\"methods\": [");
    uint32_t m = 0;
    for (uint32_t t = 0; t < sa2_ddraw_type_count(); t++)
        for (uint32_t k = 0; k < SA2_DD_MAX_SLOTS; k++) {
            const char *nm = sa2_ddraw_method_name(t, k);
            if (!nm) continue;
            fprintf(f, "%s\n  {\"interface\": ", m++ ? "," : "");
            jstr(f, sa2_ddraw_type_name(t));
            fprintf(f, ", \"name\": ");
            jstr(f, nm);
            fprintf(f, ", \"slot\": %u, \"argc\": %d, \"implemented\": %s, \"flags\": ", k,
                    sa2_ddraw_method_argc(t, k),
                    sa2_ddraw_method_implemented(t, k) ? "true" : "false");
            jstr(f, sa2_ddraw_method_flags(t, k));
            fprintf(f, "}");
        }
    fprintf(f, "]}");
}

/* The devices in the count profile: it must be one past the C reader's limit
 * exactly, or its refusal would show nothing about that limit. */
static long count_devices(const char *path) {
    const char *text = slurp(path);
    if (!text) return -1;
    long n = 0;
    for (const char *p = text; (p = strstr(p, "\nddraw_device ")) != NULL; p++) n++;
    return n;
}

int main(int argc, char **argv) {
    const char *pe = NULL, *json_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!strcmp(argv[i], "--profiles") && i + 1 < argc)
            snprintf(g_dir, sizeof g_dir, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--capture") && i + 1 < argc) g_capture = argv[++i];
        else if (!strcmp(argv[i], "--require-match")) g_require_match = 1;
        else if (!pe) pe = argv[i];
    }
    if (!pe || !g_dir[0] || (g_require_match && !g_capture)) {
        fprintf(stderr, "usage: ddraw_probe <exe> --profiles DIR [--json PATH] "
                        "[--capture PROFILE [--require-match]]\n");
        return 2;
    }
    const uint32_t n = (uint32_t)(sizeof CASES / sizeof CASES[0]);
    for (uint32_t i = 0; i < n; i++) {
        if (!CASES[i].profile || !strcmp(CASES[i].profile, "capture")) continue;
        char p[600];
        profile_path(CASES[i].profile, p, sizeof p);
        if (access(p, R_OK)) {
            fprintf(stderr, "ddraw_probe: %s is missing; write the probe's profiles with "
                            ".venv/bin/python tools/s4/ddraw_surface.py --write-profiles %s\n",
                    p, g_dir);
            return 2;
        }
    }
    /* Every device a capture's profile answers for is on, as a replay of a
     * capture runs them; each case moves SA2_DDRAW_DEVICE alone. */
    setenv("SA2_D3D9_DEVICE", "1", 1);
    setenv("SA2_DINPUT_DEVICE", "1", 1);
    setenv("SA2_XAUDIO2_DEVICE", "1", 1);
    /* Their breaks are cleared: some are refused beside a capture's profile,
     * and d3d9's GetAdapterMonitor is this probe's expectation. */
    unsetenv("SA2_D3D9_BREAK");
    unsetenv("SA2_DINPUT_BREAK");
    unsetenv("SA2_XAUDIO2_BREAK");
    unsetenv("SA2_XINPUT_PAD");
    unsetenv("SA2_GUEST_CPUS");
    if (sa2_runtime_init(pe, 4096) != SA2_OK) { fprintf(stderr, "runtime init failed\n"); return 2; }
    /* The lifted extras hold the game's callback; registered after the
     * generated table, as harness/crt_probe.c registers them. */
    const int extra_rows = sa2_s4_extra_register ? sa2_s4_extra_register() : 0;
    const int game_cb = sa2_ft_lookup(GAME_CALLBACK) != NULL;
    if (sa2_ft_add(CB_VA, probe_callback, "ddraw-probe-callback", SA2_FN_NATIVE) < 0) {
        fprintf(stderr, "the function table refused the probe's callback\n");
        return 2;
    }
    char count_path[600];
    profile_path("count", count_path, sizeof count_path);
    const long count_n = count_devices(count_path);

    FILE *out = json_path ? fopen(json_path, "w") : NULL;
    if (out) {
        fprintf(out, "{\n \"extras\": {\"rows\": %d, \"game_callback_registered\": %s},\n",
                extra_rows, game_cb ? "true" : "false");
        fprintf(out, " \"profiles\": {\"dir\": ");
        jstr(out, g_dir);
        fprintf(out, ", \"count_devices\": %ld, \"reader_limit\": %u},\n", count_n,
                SA2_DP_MAX_DDRAW);
        fprintf(out, " \"cases\": [");
    }
    snprintf(g_count_refusal, sizeof g_count_refusal, "more than %u `ddraw_device` records",
             SA2_DP_MAX_DDRAW);
    uint32_t cases_ok = 0, cases_run = 0;
    char first[512] = "";
    if (!game_cb)
        snprintf(first, sizeof first, "nothing is registered at the game's callback %#010x: it "
                 "is lifted only in the extras, so link this probe with them "
                 "(tools/s4/loop_run.py link_args); %d extra rows were registered",
                 GAME_CALLBACK, extra_rows);
    else if (count_n != (long)SA2_DP_MAX_DDRAW + 1)
        snprintf(first, sizeof first, "%s holds %ld DirectDraw devices, and the C reader's limit "
                 "is %u: the count profile must be one past it", count_path, count_n,
                 SA2_DP_MAX_DDRAW);
    for (uint32_t i = 0; i < n && !first[0]; i++) {
        const probe_case *pc = &CASES[i];
        if (pc->profile && !strcmp(pc->profile, "capture") && !g_capture) continue;
        char frag[700], err[700];
        snprintf(frag, sizeof frag, "%s/ddraw-probe.%s.json", g_dir, pc->name);
        snprintf(err, sizeof err, "%s/ddraw-probe.%s.stderr", g_dir, pc->name);
        remove(frag);
        fflush(NULL);
        const pid_t pid = fork();
        if (pid == 0) {
            if (!freopen(err, "w", stderr)) _exit(2);
            _exit(run_case(pc, frag));
        }
        int status = 0;
        waitpid(pid, &status, 0);
        const int code = WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
        int ok;
        if (pc->run) {
            ok = code == 0;
            const char *body = slurp(frag);
            if (out) {
                fprintf(out, "%s\n  ", cases_run ? "," : "");
                if (body && body[0]) fprintf(out, "%s", body);
                else { fprintf(out, "{\"case\": "); jstr(out, pc->name);
                       fprintf(out, ", \"ok\": false, \"exit\": %d}", code); }
            }
        } else {
            /* The configuration must refuse: sa2_trap_raise outside any call
             * prints the note and exits 3 (runtime/sa2_trap.c), and the note
             * names the profile's file as well as what it refuses. */
            char p[600];
            profile_path(pc->profile, p, sizeof p);
            const char *text = slurp(err);
            const int named = text && strstr(text, pc->refusal) && strstr(text, p);
            ok = code == 3 && named;
            if (out) {
                fprintf(out, "%s\n  {\"case\": ", cases_run ? "," : "");
                jstr(out, pc->name);
                fprintf(out, ", \"exit\": %d, \"wanted_exit\": 3, \"note_names\": ", code);
                jstr(out, pc->refusal);
                fprintf(out, ", \"note_names_the_file\": %s, \"ok\": %s}",
                        named ? "true" : "false", ok ? "true" : "false");
            }
        }
        cases_run++;
        cases_ok += (uint32_t)ok;
        if (!ok && !first[0]) snprintf(first, sizeof first, "case %s (exit %d)", pc->name, code);
        printf("ddraw_probe: %-18s %s (exit %d)\n", pc->name, ok ? "ok" : "FAILED", code);
    }
    const int all_ok = !first[0] && cases_ok == cases_run;
    if (out) {
        fprintf(out, "],\n \"ok\": %s, \"cases_ok\": %u, \"cases_run\": %u, \"capture\": ",
                all_ok ? "true" : "false", cases_ok, cases_run);
        if (g_capture) jstr(out, g_capture); else fputs("null", out);
        fprintf(out, ", \"require_match\": %s, \"first_failure\": ",
                g_require_match ? "true" : "false");
        jstr(out, first);
        fprintf(out, ",\n");
        publish_methods(out);
        fprintf(out, "\n}\n");
        fclose(out);
    }
    printf("ddraw_probe: %u of %u cases ok%s%s\n", cases_ok, cases_run,
           first[0] ? " -- first: " : "", first);
    return all_ok ? 0 : 1;
}
