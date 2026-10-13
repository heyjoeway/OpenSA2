/* Do the recording DirectInput device and the WMI objects behind it answer as
 * the native game's did?
 *
 *   usage: dinput_probe <sonic2app-unpacked.exe> [--json PATH] [--scratch DIR]
 *
 * Why a probe and not "the replay reached it"
 * -------------------------------------------
 * A replay under the default profile never enumerates a controller, so the
 * callback, IsXInputDevice and every WMI object are carried by it without being
 * exercised; and a replay under the recording host's profile exercises them
 * only with that host's inventory, which no committed test may hold. So this
 * drives both modules the way lifted code does -- the vtable read out of the
 * fabricated object, the thunk read out of the vtable, `this` and the
 * arguments pushed on a guest stack, entry through `sa2_call` -- under
 * profiles it writes itself: the default, one with a controller and two WMI
 * entities, and the ones each claim below needs. Nothing calls a C function in
 * runtime/sa2_dinput.c or runtime/sa2_wbem.c directly except the readers.
 *
 * **The stack pointer after every call is measured**: a COM method pops its
 * return address, `this` and its own arguments, and an import pops its
 * return address and its arguments. That is the check that sees the defect
 * class that corrupted a caller's frame twice in this project (d3d9 at S4
 * slice 6, Steam at slice 9).
 *
 * **Every answer a profile carries is asserted as a literal**: the number the
 * probe wrote into the file, never the profile getter the device itself calls.
 * CreateDevice for the keyboard, the mouse and each controller, and
 * CoSetProxyBlanket, pass the capture's answer through; a device that ignored
 * those fields and answered S_OK would agree with a getter wherever the
 * profile said S_OK, which is every profile but `hr`.
 *
 * The cases, one child process each
 * ---------------------------------
 * The runtime is initialised once; each case runs in a fork of that, so each
 * starts with no DirectInput or WMI object and its own environment.
 *
 *   off                  SA2_DINPUT_DEVICE unset under the default profile:
 *                        E_FAIL and a null pointer, as before the device
 *                        existed. The one configuration where off is legal.
 *   default              the keyboard and mouse lifecycle with every native
 *                        error answer, EnumDevices enumerating nothing, the
 *                        four XInput slots; then every refusal by name,
 *                        XInput's slot 4 among them.
 *   profile              one controller, two WMI entities (one of them with a
 *                        two-byte and a four-byte UTF-8 character): the
 *                        DIDEVICEINSTANCEA a stand-in callback at 0x00864C90
 *                        receives, the enumerated device's Unacquire and
 *                        Release, and the whole WMI sequence IsXInputDevice
 *                        makes, driven call by call; then the WMI refusals.
 *   profile-guest        a controller and two entities with no "IG_", through
 *                        the game's own callback and IsXInputDevice, lifted: the
 *                        call sequence native made (native-outcomes-v1a:14782
 *                        -15244). An "IG_" entity sends IsXInputDevice into the
 *                        CRT's swscanf (0x00828484), which needs the locale and
 *                        per-thread data the CRT startup sets up and this probe
 *                        never runs; the replays run that path under the real
 *                        startup, and tools/s4/dinput_surface.py's claim
 *                        xinput-branch-is-a-no-op covers the branch it takes.
 *   profile-hr           a profile whose every pass-through answer is a
 *                        failure: CreateDevice 0x80040154 for the keyboard,
 *                        0x80070005 for the mouse and for the controller,
 *                        CoSetProxyBlanket 0x80004005. Each must come back as
 *                        that literal, with NULL written for a device.
 *   profile-hr-guest     the same profile through the game's own callback: the
 *                        controller's CreateDevice fails, the jl at 0x00864CB2
 *                        returns 1 at once, and IsXInputDevice never runs -- no
 *                        OLE import, no WMI object, no BSTR.
 *   batch                21 entities: WBEM_S_NO_ERROR with 20, then
 *                        WBEM_S_FALSE with 1, then WBEM_S_FALSE with 0.
 *   stop                 two controllers and a callback answering DIENUM_STOP:
 *                        one callback.
 *   brk-<name>           each SA2_DINPUT_BREAK, and the observation it flips.
 *   refuse-<profile>     a profile the runtime cannot reproduce: a reader
 *                        refuses it when the run is configured, naming the file
 *                        and the line of the record that is wrong, and the run
 *                        never starts. The refusals are the reader's
 *                        (runtime/sa2_devprofile.h), where the limits live once.
 *   refuse-device-off    a capture's profile with SA2_DINPUT_DEVICE off, which
 *                        runtime/sa2_dinput.c refuses rather than answer E_FAIL
 *                        where the capture's game got a device.
 *   refuse-<break>-capture  a capture's profile with the bare-ok or the
 *                        nodevice break, refused by name for the same reason:
 *                        bare-ok takes the E_FAIL path with the device on, and
 *                        nodevice replaces the system devices the profile
 *                        recorded. Under the default profile both run (brk-).
 *   refuse-xinput-pad    a capture's profile with SA2_XINPUT_PAD set, which
 *                        runtime/sa2_iface.c refuses: the pad is a falsifier for
 *                        default-profile runs, not a device a capture had.
 *
 * The host keyboard (runtime/sa2_dinput.h, "The host keyboard"), each case's
 * files under the scratch directory, made by its setup before the
 * configuration reads them:
 *
 *   input-unset          none of SA2_INPUT_DI_SCRIPT, _SOURCE or _LOG: every
 *                        keyboard and mouse answer, and every counter the run
 *                        record prints, is the literal the device gave before
 *                        the host keyboard existed, and its counters stay 0.
 *   input-script         a script with an overlap (one key in both lines), a
 *                        comment, a blank line and lower-case hex: eight
 *                        polls, each poll's 256 bytes exactly its keys at
 *                        0x80, a mouse poll between that takes no keyboard
 *                        index, and the log, line by line.
 *   input-script-replay  that log as the script: the same eight answers.
 *   input-script-empty   a script of comments and blanks: the input-unset
 *                        answers and counters, and a log with no key line.
 *   input-script-exit    a run still open when the process exits (exit, not
 *                        _exit, in a child): the exit handler writes its line.
 *   input-fifo           records through a real FIFO: a partial record carried
 *                        to the next poll; a poll holding every key its
 *                        records held (a key let go before the poll still held
 *                        at it); the latest record's keys holding at a poll
 *                        that read none; byte 0, no key's, ignored; a seq gap
 *                        counted; a record read with end of file applied at
 *                        its poll, and every key up after; a second writer;
 *                        and a partial record dropped at its end.
 *   input-fifo-stop      SIGTERM: no WM_QUIT until the next keyboard poll, then
 *                        one, posted as the host's; a second SIGTERM kills.
 *   input-fifo-stop-posted  SIGTERM after the guest posted WM_QUIT: none
 *                        posted or counted, and the log's closing line says so.
 *   input-fifo-badmagic  a record with another magic stops the run by name.
 *   input-fifo-replaced  the FIFO replaced by a regular file after the
 *                        configuration: the first poll stops the run by name.
 *   refuse-input-<why>   each configuration the runtime refuses: both sources,
 *                        a log alone, the device off, a break beside a source,
 *                        six malformed scripts (a NUL byte and the code 0x00
 *                        among them), a log already holding a line, a log
 *                        that is a FIFO (refused, not waited on), a script
 *                        that is not there, a source that is not there, and
 *                        a source that is a regular file.
 *
 * The profiles
 * ------------
 * write_profile writes each row of PROFILES in runtime/sa2_devprofile.h's
 * grammar. Built with -DSA2_DINPUT_PROBE_PROFILES_ONLY this file is nothing
 * but that: Documentation/scripts/tests/test_dinput_surface.py builds it so,
 * writes every profile and reads each with tools/s4/device_profile.py, which
 * must accept the ones the cases run and refuse each other one at the line and
 * with the words this file expects. A grammar change the profiles do not
 * follow then fails without linking the probe. `xaudio2_master`, added to the
 * grammar after the profiles were written, would have made every profile case
 * exit 3; a review found it by reading, before any run could.
 *
 * Every number goes to the JSON, a failing run's included.
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../runtime/sa2_devprofile.h"

/* ---------------------------------------------------------- the profiles
 *
 * Nothing here is a host's inventory: every GUID, name and DeviceID is
 * invented for the probe. */

typedef struct {
    const char *instance, *product;   /* registry form */
    uint32_t    create;               /* CreateDevice(instance) natively */
} gc_spec;

/* The probe controller's GUIDs. Data1 of a HID product GUID is
 * MAKELONG(VID, PID): IsXInputDevice compares exactly that at 0x0086489F. */
#define GC_INSTANCE     "{50524F42-0000-0000-0000-000000000001}"
#define GC_INSTANCE_2   "{50524F42-0000-0000-0000-000000000002}"
#define GC_PRODUCT      "{00000000-0000-0000-0000-000000000000}"   /* VID 1234 PID 5678 */
#define GC_SYSKEYBOARD  "{6F1D2B61-D5A0-11CF-BFC7-444553540000}"
#define GC_SYSMOUSE     "{6F1D2B60-D5A0-11CF-BFC7-444553540000}"
#define GC_DEVTYPE      0x0001021Cu
#define GC_NAME         "SA2 probe controller"

/* The failures profile `hr` records: values the default never holds. */
#define HR_CLASSNOTREG  0x80040154u   /* REGDB_E_CLASSNOTREG, as DIERR_DEVICENOTREG */
#define HR_ACCESSDENIED 0x80070005u   /* E_ACCESSDENIED */
#define HR_FAIL         0x80004005u   /* E_FAIL */

static const gc_spec GC_ONE[]   = {{GC_INSTANCE, GC_PRODUCT, 0u}};
static const gc_spec GC_TWO[]   = {{GC_INSTANCE, GC_PRODUCT, 0u}, {GC_INSTANCE_2, GC_PRODUCT, 0u}};
static const gc_spec GC_FAILS[] = {{GC_INSTANCE, GC_PRODUCT, HR_ACCESSDENIED}};
static const gc_spec GC_KBD[]   = {{GC_SYSKEYBOARD, GC_PRODUCT, 0u}};
static const gc_spec GC_MOUSE[] = {{GC_SYSMOUSE, GC_PRODUCT, 0u}};
static const gc_spec GC_DUP[]   = {{GC_INSTANCE, GC_PRODUCT, 0u}, {GC_INSTANCE, GC_PRODUCT, 0u}};

/* Entity 0 has no IG_; entity 1 has IG_ with VID ABCD PID 1234 and a U+00E9
 * and a U+1F600 in UTF-8, so the conversion to UTF-16 is exercised. The probe
 * reads these back through Get; it never runs the game's parse of them. */
static const char *const IDS_TWO[] = {
    "HID\\VID_1234&PID_5678\\7&0&0000",
    "HID\\VID_ABCD&PID_1234&IG_00\\caf\xc3\xa9&\xf0\x9f\x98\x80",
};
/* The lifted-callback case's entities: no "IG_", so IsXInputDevice's only CRT
 * call is wcsstr, which reads its arguments and nothing else. */
static const char *const IDS_GUEST[] = {
    "HID\\VID_1234&PID_5678\\7&0&0000",
    "USB\\VID_1234&PID_5678\\5&caf\xc3\xa9&0&1",
};
/* 0xFF begins no UTF-8 sequence. */
static const char *const IDS_BAD[] = {"ROOT\\SA2PROBE\\\xff"};
/* ROOT\SA2PROBE\0000 onward, one more than a reader accepts; write_profiles
 * fills them. */
static char        g_seq_text[SA2_DP_MAX_WMI_PNP + 1u][24];
static const char *g_seq_ids[SA2_DP_MAX_WMI_PNP + 1u];

enum { DEFECT_NONE = 0, DEFECT_GAMECTRL = 1, DEFECT_PNP = 2 };

typedef struct {
    const char        *key;           /* the file is dinput-probe.<key>.profile.txt */
    uint32_t           syskeyboard;   /* CreateDevice(GUID_SysKeyboard) natively */
    uint32_t           sysmouse;      /* CreateDevice(GUID_SysMouse) natively */
    const gc_spec     *gc;
    unsigned           ngc;
    uint32_t           blanket;       /* CoSetProxyBlanket natively */
    const char *const *ids;           /* the entities' DeviceIDs, UTF-8 */
    unsigned           nids;
    int                defect;        /* the kind of the record a reader refuses, or none */
    unsigned           defect_index;  /* which record of that kind, from 0 */
    const char        *refusal;       /* the reader's words for it (runtime/sa2_devprofile.c) */
} probe_profile;

static const probe_profile PROFILES[] = {
    {"two", 0u, 0u, GC_ONE, 1, 0u, IDS_TWO, 2, DEFECT_NONE, 0, NULL},
    {"guest", 0u, 0u, GC_ONE, 1, 0u, IDS_GUEST, 2, DEFECT_NONE, 0, NULL},
    {"hr", HR_CLASSNOTREG, HR_ACCESSDENIED, GC_FAILS, 1, HR_FAIL, IDS_TWO, 2, DEFECT_NONE, 0,
     NULL},
    {"batch", 0u, 0u, NULL, 0, 0u, g_seq_ids, 21, DEFECT_NONE, 0, NULL},
    {"stop", 0u, 0u, GC_TWO, 2, 0u, NULL, 0, DEFECT_NONE, 0, NULL},
    {"entities", 0u, 0u, NULL, 0, 0u, g_seq_ids, SA2_DP_MAX_WMI_PNP + 1u, DEFECT_PNP,
     SA2_DP_MAX_WMI_PNP, "`wmi_pnp` records, the most the runtime holds"},
    {"utf8", 0u, 0u, NULL, 0, 0u, IDS_BAD, 1, DEFECT_PNP, 0, "DeviceID is not valid UTF-8"},
    {"kbdctrl", 0u, 0u, GC_KBD, 1, 0u, NULL, 0, DEFECT_GAMECTRL, 0,
     "has GUID_SysKeyboard as its instance GUID"},
    {"mousectrl", 0u, 0u, GC_MOUSE, 1, 0u, NULL, 0, DEFECT_GAMECTRL, 0,
     "has GUID_SysMouse as its instance GUID"},
    {"dup", 0u, 0u, GC_DUP, 2, 0u, NULL, 0, DEFECT_GAMECTRL, 1,
     "has controller 0's instance GUID"},
};
#define NPROFILES ((unsigned)(sizeof PROFILES / sizeof PROFILES[0]))

/* The line, from 1, of each profile's defect record as write_profile wrote it;
 * 0 for a profile both readers accept. */
static unsigned g_defect_line[NPROFILES];
static char     g_scratch[512];

static void put_str(FILE *f, const char *s) {
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') { fputc('\\', f); fputc(*p, f); }
        else if (*p >= 0x20 && *p <= 0x7e) fputc(*p, f);
        else fprintf(f, "\\x%02x", *p);
    }
    fputc('"', f);
}

/* One profile, record by record in the grammar's order; the line of its
 * defect record, if it has one, lands in *defect_line. */
static int write_profile(const char *path, const probe_profile *pp, unsigned *defect_line) {
    FILE *f = fopen(path, "wb");
    if (!f) return 0;
    unsigned line = 0;
    *defect_line = 0;
#define REC(...) (fprintf(f, __VA_ARGS__), ++line)
    REC("sa2-device-profile 1\n");
    /* One processor in both masks, the default's: nothing here reads them. */
    REC("system processors=1 process_mask=0x00000001 system_mask=0x00000001\n");
    REC("dinput8_create hr=0x00000000\n");
    REC("dinput8_syskeyboard hr=0x%08x\n", pp->syskeyboard);
    REC("dinput8_sysmouse hr=0x%08x\n", pp->sysmouse);
    for (unsigned i = 0; i < pp->ngc; i++) {
        fprintf(f, "dinput8_gamectrl index=%u instance=%s product=%s "
                   "ffdriver={00000000-0000-0000-0000-000000000000} devtype=0x%08x "
                   "usage_page=0x00000001 usage=0x00000006 instance_name=",
                i, pp->gc[i].instance, pp->gc[i].product, GC_DEVTYPE);
        put_str(f, GC_NAME);
        fprintf(f, " product_name=");
        put_str(f, GC_NAME);
        REC(" create=0x%08x\n", pp->gc[i].create);
        if (pp->defect == DEFECT_GAMECTRL && pp->defect_index == i) *defect_line = line;
    }
    REC("dinput8_enum hr=0x00000000 count=%u\n", pp->ngc);
    for (unsigned s = 0; s < 4; s++) REC("xinput_caps slot=%u code=0x0000048f\n", s);
    REC("wmi_coinit hr=0x00000000\n");
    REC("wmi_locator hr=0x00000000\n");
    REC("wmi_connect hr=0x00000000\n");
    REC("wmi_blanket hr=0x%08x\n", pp->blanket);
    REC("wmi_enum hr=0x00000000\n");
    /* Batches as native's enumerator gives them: 20 while 20 remain with S_OK,
     * then the short one with WBEM_S_FALSE, then an empty WBEM_S_FALSE. */
    unsigned i = 0;
    for (;;) {
        const unsigned k = pp->nids - i >= 20u ? 20u : pp->nids - i;
        REC("wmi_next hr=0x%08x returned=%u\n", k == 20u ? 0u : 1u, k);
        for (unsigned j = 0; j < k; j++, i++) {
            fprintf(f, "wmi_pnp index=%u get=0x00000000 vt=0x00000008 deviceid=", i);
            put_str(f, pp->ids[i]);
            REC("\n");
            if (pp->defect == DEFECT_PNP && pp->defect_index == i) *defect_line = line;
        }
        if (k < 20u) {
            if (k) REC("wmi_next hr=0x00000001 returned=0\n");
            break;
        }
    }
    REC("wmi_entities count=%u\n", pp->nids);
    REC("d3d9_adapters count=1\n");
    REC("ddraw_device index=0 guid=null monitor=null description=\"DirectDraw HAL\" "
        "name=\"display\"\n");
    REC("ddraw_enum hr=0x00000000 count=1\n");
    REC("ddraw_create guid={00000000-0000-0000-0000-000000000000} hr=0x00000000\n");
    REC("ddraw_query hr=0x00000000\n");
    REC("ddraw_vidmem hr=0x00000000 total=0x10000000\n");
    REC("xaudio2_master voice=1 flags=0x00000000 channels=2 rate=48000\n");
    REC("end\n");
#undef REC
    return fclose(f) == 0;
}

static void profile_path(const char *key, char *out, size_t cap) {
    snprintf(out, cap, "%s/dinput-probe.%s.profile.txt", g_scratch, key);
}

static int write_profiles(void) {
    for (unsigned i = 0; i <= SA2_DP_MAX_WMI_PNP; i++) {
        snprintf(g_seq_text[i], sizeof g_seq_text[i], "ROOT\\SA2PROBE\\%04u", i);
        g_seq_ids[i] = g_seq_text[i];
    }
    int ok = 1;
    for (unsigned i = 0; i < NPROFILES; i++) {
        char p[600];
        profile_path(PROFILES[i].key, p, sizeof p);
        ok &= write_profile(p, &PROFILES[i], &g_defect_line[i]);
    }
    return ok;
}

#ifdef SA2_DINPUT_PROBE_PROFILES_ONLY

/* The profiles alone, for Documentation/scripts/tests/test_dinput_surface.py:
 * one line per profile, tab-separated -- its key, its path, the line a reader
 * must refuse it at (0: both readers accept it) and the reader's words there. */
int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "usage: %s DIR\n", argv[0]); return 2; }
    snprintf(g_scratch, sizeof g_scratch, "%s", argv[1]);
    if (!write_profiles()) { fprintf(stderr, "cannot write the profiles in %s\n", g_scratch); return 1; }
    for (unsigned i = 0; i < NPROFILES; i++) {
        char p[600];
        profile_path(PROFILES[i].key, p, sizeof p);
        printf("%s\t%s\t%u\t%s\n", PROFILES[i].key, p, g_defect_line[i],
               PROFILES[i].refusal ? PROFILES[i].refusal : "");
    }
    return 0;
}

#else

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_dinput.h"
#include "../runtime/sa2_guest_call.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_trap.h"
#include "../runtime/sa2_wbem.h"
#include "../runtime/sa2_window.h"

#define STACK_TOP  0x03eeff00u
#define SCRATCH    0x03004000u    /* guest scratch, clear of every fabricated block */

/* The image's own constants the probe passes, so the arguments are the
 * image's bytes and not a copy of them (tools/s4/dinput_surface.py checks
 * each against the image). */
#define IMG_CALLBACK   0x00864C90u
#define IMG_GUID_KBD   0x0087360Cu
#define IMG_GUID_MOUSE 0x008735FCu
#define IMG_IID_DI8A   0x0087355Cu
#define IMG_FMT_KBD    0x00875D6Cu
#define IMG_FMT_MOUSE  0x00875F74u
#define IMG_CLSID_WBEM 0x008FFF88u
#define IMG_IID_WBEM   0x008FFF98u
#define IMG_NS         0x0089F068u    /* "\\.\root\cimv2" */
#define IMG_CLASS      0x0089F088u    /* "Win32_PNPEntity" */
#define IMG_PROP       0x0089F0A8u    /* "DeviceID" */

#define SENTINEL 0xFEEDFACEu

/* GC_INSTANCE and GC_PRODUCT as the 16 bytes a GUID is in memory. */
static const uint8_t GC_INSTANCE_B[16] = {0x42,0x4F,0x52,0x50,0,0,0,0,0,0,0,0,0,0,0,0x01};
static const uint8_t GC_PRODUCT_B[16]  = {0x34,0x12,0x78,0x56,0,0,0,0,0,0,0x50,0x49,0x44,0x56,
                                          0x49,0x44};
/* IDS_TWO[1] in UTF-16. */
static const uint16_t IDS_TWO_U16_1[] = {
    'H','I','D','\\','V','I','D','_','A','B','C','D','&','P','I','D','_','1','2','3','4',
    '&','I','G','_','0','0','\\','c','a','f',0x00E9,'&',0xD83D,0xDE00,
};

/* ------------------------------------------------------ one case's record */

static int      g_fail;
static uint32_t g_checks;
static char     g_first[256];
static FILE    *g_facts;           /* this case's "facts" object, being written */
static int      g_nfacts;

static void check(int cond, const char *fmt, ...) {
    g_checks++;
    if (cond) return;
    g_fail++;
    if (!g_first[0]) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(g_first, sizeof g_first, fmt, ap);
        va_end(ap);
    }
}

static void jstr(FILE *f, const char *s) {
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') { fputc('\\', f); fputc(*p, f); }
        else if (*p < 0x20 || *p >= 0x7F) fprintf(f, "\\u%04x", *p);
        else fputc(*p, f);
    }
    fputc('"', f);
}

static void fact(const char *key, uint32_t v) {
    if (!g_facts) return;
    fprintf(g_facts, "%s\"%s\": %u", g_nfacts++ ? ", " : "", key, v);
}

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}
static uint16_t peek16(uint32_t a) {
    uint16_t v = 0;
    return sa2_peek(a, 2, &v) == SA2_OK ? v : 0xBEEFu;
}
static int all_bytes(uint32_t a, uint32_t n, uint8_t v) {
    for (uint32_t i = 0; i < n; i++) {
        uint8_t b = 0;
        if (sa2_peek(a + i, 1, &b) != SA2_OK || b != v) return 0;
    }
    return 1;
}
static void fill(uint32_t a, uint32_t n, uint8_t v) { sa2_guest_fill(a, v, n); }

/* ------------------------------------------------------------- the calls */

/* The last call. A native hands `memory` back rather than writing EIP, so a
 * call that returned normally comes back from `sa2_call` as CONTROL_LEFT at
 * the thunk with sa2_call's own "returned with EIP" note; a trap comes back
 * with the trap's note. */
static struct {
    uint32_t eax, esp_delta, status, exit_pc;
    int      ok;
    char     note[SA2_TRAP_NOTE_MAX];
} g_r;
static uint32_t g_pops, g_pops_bad, g_bad_pop_delta;

static uint32_t call_at(uint32_t thunk, uint32_t n, const uint32_t *words, uint32_t want_pop) {
    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    const uint32_t esp0 = STACK_TOP - 4u * (n + 1u);
    st.gpr[SA2_ESP] = esp0;
    for (uint32_t i = 0; i < n; i++) pk32(esp0 + 4u + 4u * i, words[i]);
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    sa2_call_result_t res;
    sa2_call(thunk, &st, 60, &res);
    g_r.status = res.status;
    g_r.exit_pc = res.exit_pc;
    snprintf(g_r.note, sizeof g_r.note, "%s", res.note);
    g_r.ok = res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == thunk
             && !strncmp(res.note, "returned with EIP", 17);
    g_r.esp_delta = st.gpr[SA2_ESP] - esp0;
    g_r.eax = st.gpr[SA2_EAX];
    if (g_r.ok) {
        g_pops++;
        if (g_r.esp_delta != want_pop) { g_pops_bad++; g_bad_pop_delta = g_r.esp_delta; }
    }
    return g_r.eax;
}

/* Slot `slot` of the fabricated object at `self`, exactly as `call [reg+N]`. */
static uint32_t call_slot(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv) {
    uint32_t w[12];
    w[0] = self;
    for (uint32_t i = 0; i < argc && i < 11u; i++) w[1 + i] = argv[i];
    const uint32_t vt = peek32(self);
    return call_at(peek32(vt + 4u * slot), argc + 1u, w, 4u * (argc + 2u));
}

/* An import, through the thunk its IAT slot was bound to. */
static uint32_t call_imp(const char *dll, const char *name, uint32_t argc, const uint32_t *argv) {
    const sa2_import_info *in = sa2_import_by_name(dll, name);
    check(in != NULL, "%s!%s is not an import this runtime bound", dll, name);
    if (!in) { g_r.ok = 0; g_r.eax = 0xFFFFFFFFu; return g_r.eax; }
    return call_at(in->thunk, argc, argv, 4u * (argc + 1u));
}

static uint32_t import_calls(const char *dll, const char *name) {
    const sa2_import_info *in = sa2_import_by_name(dll, name);
    return in ? in->calls : 0xFFFFFFFFu;
}

#define SLOT(self, slot, n, ...) call_slot(self, slot, n, (const uint32_t[]){__VA_ARGS__})
#define SLOT0(self, slot)        call_slot(self, slot, 0, NULL)
#define IMP(dll, name, n, ...)   call_imp(dll, name, n, (const uint32_t[]){__VA_ARGS__})

/* A call that must have returned normally with `want`. */
static void answered(const char *what, uint32_t want) {
    check(g_r.ok, "%s did not return: status %u note %s", what, g_r.status, g_r.note);
    check(g_r.eax == want, "%s answered %#010x, wanted %#010x", what, g_r.eax, want);
}

/* A call that must have refused, its note naming both `what` refused (the
 * interface and method, or the import) and `why` (the reason's own words), so
 * a refusal of the right call for another reason does not pass. */
static void refused(const char *call, const char *what, const char *why) {
    const int named = strstr(g_r.note, what) != NULL && strstr(g_r.note, why) != NULL;
    check(!g_r.ok && g_r.status != SA2_TRAP_NONE && named,
          "%s did not refuse naming \"%s\" and \"%s\": status %u note %s", call, what, why,
          g_r.status, g_r.note);
}

/* --------------------------------------------- the stand-in for 0x00864C90
 *
 * Registered over the lifted callback's own table row in the cases that
 * examine what EnumDevices hands the callback. It records the first two
 * DIDEVICEINSTANCEAs and returns `ret`, popping its two arguments as the
 * image's `ret 8` does (0x00864CE3). */
static struct {
    uint32_t calls, ret, lpddi, ref;
    uint8_t  ddi[2][0x244];
} g_cb;

static void *standin_callback(void *s, uint32_t pc, void *m) {
    (void)pc;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    g_cb.lpddi = peek32(esp + 4u);
    g_cb.ref = peek32(esp + 8u);
    if (g_cb.calls < 2u) sa2_peek(g_cb.lpddi, 0x244u, g_cb.ddi[g_cb.calls]);
    g_cb.calls++;
    sa2_rt_set_gpr(s, SA2_EAX, g_cb.ret);
    sa2_rt_set_gpr(s, SA2_ESP, esp + 12u);
    return m;
}

static void install_standin(uint32_t ret) {
    memset(&g_cb, 0, sizeof g_cb);
    g_cb.ret = ret;
    check(sa2_ft_add(IMG_CALLBACK, standin_callback, "dinput-probe-callback",
                     SA2_FN_NATIVE) >= 0, "the stand-in callback could not be registered");
}

static uint32_t make_window(void) {
    sa2_wnd_class c;
    memset(&c, 0, sizeof c);
    snprintf(c.name, sizeof c.name, "%s", "SA2DinputProbe");
    c.hinstance = 0x00400000u;
    sa2_window_register_class(&c);
    sa2_wnd_window w;
    memset(&w, 0, sizeof w);
    snprintf(w.class_name, sizeof w.class_name, "%s", "SA2DinputProbe");
    w.hinstance = 0x00400000u;
    const uint32_t hwnd = sa2_window_create(&w);
    check(hwnd != 0 && sa2_window_of(hwnd) != NULL, "the probe's window was not created");
    return hwnd;
}

/* ------------------------------------------------------------ shared steps */

static uint32_t make_di8(void) {
    const uint32_t out = SCRATCH + 0x000u;
    pk32(out, SENTINEL);
    IMP("DINPUT8.dll", "DirectInput8Create", 5, 0x00400000u, 0x800u, IMG_IID_DI8A, out, 0u);
    answered("DirectInput8Create", SA2_DI_OK);
    const uint32_t di8 = peek32(out);
    check(di8 != 0 && di8 != SENTINEL, "DirectInput8Create wrote %#010x", di8);
    return di8 == SENTINEL ? 0u : di8;
}

static uint32_t create(uint32_t di8, uint32_t guid, uint32_t out) {
    pk32(out, SENTINEL);
    return SLOT(di8, 3, 3, guid, out, 0u);
}

/* A keyboard or mouse, created, formatted, cooperative and acquired. */
static uint32_t ready_device(uint32_t di8, uint32_t guid, uint32_t fmt, uint32_t hwnd,
                             const char *what) {
    const uint32_t out = SCRATCH + 0x010u;
    create(di8, guid, out);
    answered(what, SA2_DI_OK);
    const uint32_t dev = peek32(out);
    SLOT(dev, 11, 1, fmt);
    answered("SetDataFormat", SA2_DI_OK);
    SLOT(dev, 13, 2, hwnd, 6u);
    answered("SetCooperativeLevel", SA2_DI_OK);
    SLOT0(dev, 7);
    answered("Acquire", SA2_DI_OK);
    return dev;
}

/* The WMI sequence IsXInputDevice makes, up to the enumerator; `*svc_out` and
 * `*loc_out` receive the objects, the three BSTRs land in `names`, and
 * CoSetProxyBlanket must answer `blanket`, the value the profile recorded. */
static uint32_t wmi_open(uint32_t *loc_out, uint32_t *svc_out, uint32_t names[3],
                         uint32_t blanket) {
    names[0] = IMP("OLEAUT32.dll", "Ordinal#2", 1, IMG_NS);
    names[1] = IMP("OLEAUT32.dll", "Ordinal#2", 1, IMG_CLASS);
    names[2] = IMP("OLEAUT32.dll", "Ordinal#2", 1, IMG_PROP);
    const uint32_t lo = SCRATCH + 0xB00u, so = SCRATCH + 0xB04u, eo = SCRATCH + 0xB08u;
    pk32(lo, SENTINEL); pk32(so, SENTINEL); pk32(eo, SENTINEL);
    IMP("ole32.dll", "CoCreateInstance", 5, IMG_CLSID_WBEM, 0u, 1u, IMG_IID_WBEM, lo);
    answered("CoCreateInstance(CLSID_WbemLocator)", 0u);
    *loc_out = peek32(lo);
    SLOT(*loc_out, 3, 8, names[0], 0u, 0u, 0u, 0u, 0u, 0u, so);
    answered("ConnectServer", 0u);
    *svc_out = peek32(so);
    IMP("ole32.dll", "CoSetProxyBlanket", 8, *svc_out, 10u, 0u, 0u, 3u, 3u, 0u, 0u);
    answered("CoSetProxyBlanket", blanket);
    SLOT(*svc_out, 18, 4, names[1], 0u, 0u, eo);
    answered("CreateInstanceEnum", 0u);
    return peek32(eo);
}

/* A BSTR at `b` holding exactly `n` UTF-16 units `u`. */
static int bstr_is(uint32_t b, const uint16_t *u, uint32_t n) {
    if (!b || peek32(b - 4u) != 2u * n || peek16(b + 2u * n) != 0) return 0;
    for (uint32_t i = 0; i < n; i++) if (peek16(b + 2u * i) != u[i]) return 0;
    return 1;
}
static int bstr_is_ascii(uint32_t b, const char *s) {
    uint16_t u[128];
    const uint32_t n = (uint32_t)strlen(s);
    for (uint32_t i = 0; i < n && i < 128u; i++) u[i] = (uint8_t)s[i];
    return bstr_is(b, u, n);
}

/* XInputGetCapabilities for the four slots the image scans, each of which a
 * reader accepts only as 1167. */
static void xinput_slots(void) {
    for (uint32_t slot = 0; slot < 4u; slot++) {
        IMP("XINPUT1_3.dll", "Ordinal#4", 3, slot, 1u, SCRATCH + 0x400u);
        answered("XInputGetCapabilities(slot 0 to 3)", 1167u);
    }
}

/* ---------------------------------------------------------------- the cases */

static void case_off(void) {
    check(sa2_devprofile_is_default(), "the device is off under profile %s, which the "
          "configuration must have refused", sa2_devprofile_source());
    const uint32_t out = SCRATCH;
    pk32(out, SENTINEL);
    IMP("DINPUT8.dll", "DirectInput8Create", 5, 0x00400000u, 0x800u, IMG_IID_DI8A, out, 0u);
    answered("DirectInput8Create with the device off", 0x80004005u);
    check(peek32(out) == 0, "DirectInput8Create with the device off left %#010x", peek32(out));
    fact("device_enabled", (uint32_t)sa2_dinput_device_enabled());
}

static void case_default(void) {
    check(sa2_devprofile_is_default() && !strcmp(sa2_devprofile_source(), "default"),
          "the profile is %s, not the default", sa2_devprofile_source());
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t hwnd = make_window();

    /* The keyboard, with each native error answer on the way. */
    const uint32_t out = SCRATCH + 0x010u, buf = SCRATCH + 0x100u;
    create(di8, IMG_GUID_KBD, out);
    answered("CreateDevice(GUID_SysKeyboard)", SA2_DI_OK);
    const uint32_t kbd = peek32(out);
    SLOT(kbd, 11, 1, IMG_FMT_KBD);
    answered("SetDataFormat(c_dfDIKeyboard)", SA2_DI_OK);
    SLOT(kbd, 13, 2, 0x12345678u, 6u);
    answered("SetCooperativeLevel(a handle that is no window)", SA2_DI_E_HANDLE);
    SLOT(kbd, 13, 2, hwnd, 6u);
    answered("SetCooperativeLevel", SA2_DI_OK);
    fill(buf, 272u, 0xA5);
    SLOT(kbd, 9, 2, 256u, buf);
    answered("GetDeviceState before Acquire", SA2_DIERR_NOTACQUIRED);
    check(all_bytes(buf, 272u, 0xA5), "GetDeviceState before Acquire wrote the buffer");
    SLOT0(kbd, 7);
    answered("Acquire", SA2_DI_OK);
    SLOT0(kbd, 7);
    answered("a second Acquire", SA2_DI_NOEFFECT);
    SLOT(kbd, 9, 2, 256u, buf);
    answered("GetDeviceState(256)", SA2_DI_OK);
    check(all_bytes(buf, 256u, 0), "the keyboard state is not 256 zero bytes");
    check(all_bytes(buf + 256u, 16u, 0xA5), "GetDeviceState(256) wrote past 256 bytes");
    SLOT(kbd, 9, 2, 255u, buf);
    answered("GetDeviceState(255)", SA2_DIERR_INVALIDPARAM);
    SLOT(kbd, 11, 1, IMG_FMT_KBD);
    answered("SetDataFormat while acquired", SA2_DIERR_ACQUIRED);
    SLOT0(kbd, 8);
    answered("Unacquire", SA2_DI_OK);
    SLOT0(kbd, 8);
    answered("a second Unacquire", SA2_DI_NOEFFECT);
    SLOT(kbd, 9, 2, 256u, buf);
    answered("GetDeviceState after Unacquire", SA2_DIERR_NOTACQUIRED);

    /* The mouse, straight through. */
    const uint32_t mouse = ready_device(di8, IMG_GUID_MOUSE, IMG_FMT_MOUSE, hwnd,
                                        "CreateDevice(GUID_SysMouse)");
    fill(buf, 32u, 0xA5);
    SLOT(mouse, 9, 2, 16u, buf);
    answered("GetDeviceState(16)", SA2_DI_OK);
    check(all_bytes(buf, 16u, 0) && all_bytes(buf + 16u, 16u, 0xA5),
          "the mouse state is not 16 zero bytes");

    /* No controller: the callback is never called. */
    install_standin(1u);
    const uint32_t ref = SCRATCH + 0x800u;
    SLOT(di8, 4, 4, 4u, IMG_CALLBACK, ref, 1u);
    answered("EnumDevices under the default profile", SA2_DI_OK);
    check(g_cb.calls == 0, "EnumDevices called back %u times with no controller", g_cb.calls);

    xinput_slots();

    sa2_di_stats st;
    sa2_dinput_stats_get(&st);
    check(st.created == 1 && st.unknown_this == 0 && st.state_answers_nonidle == 0
          && st.enumerations == 1 && st.devices_enumerated == 0,
          "stats: created %u unknown_this %u nonidle %u enumerations %u enumerated %u",
          st.created, st.unknown_this, st.state_answers_nonidle, st.enumerations,
          st.devices_enumerated);
    fact("method_calls", st.method_calls);
    fact("distinct_methods", st.distinct_methods);
    fact("state_bytes_written", (uint32_t)st.state_bytes_written);
    fact("devices", sa2_dinput_device_count());

    /* Every refusal by name, last, because each one is a trap. */
    const uint32_t unknown = SCRATCH + 0x900u;
    sa2_poke(unknown, 16, GC_INSTANCE_B);
    create(di8, unknown, out);
    refused("CreateDevice of a GUID nothing enumerated", "IDirectInput8A::CreateDevice called from",
            "is neither system GUID");
    SLOT(di8, 3, 3, IMG_GUID_KBD, out, 1u);
    refused("CreateDevice with an outer", "IDirectInput8A::CreateDevice called from",
            "pUnkOuter is not 0");
    SLOT(di8, 4, 4, 0u, IMG_CALLBACK, ref, 1u);
    refused("EnumDevices(DI8DEVCLASS_ALL)", "IDirectInput8A::EnumDevices called from",
            "class 0 flags 0x1;");
    SLOT(di8, 4, 4, 4u, IMG_CALLBACK, ref, 0u);
    refused("EnumDevices(DIEDFL_ALLDEVICES)", "IDirectInput8A::EnumDevices called from",
            "class 4 flags 0;");
    SLOT(di8, 4, 4, 4u, 0x00401000u, ref, 1u);
    refused("EnumDevices with another callback", "IDirectInput8A::EnumDevices called from",
            "the callback is not 0x00864C90");
    SLOT(mouse, 13, 2, hwnd, 5u);
    refused("SetCooperativeLevel(5)", "IDirectInputDevice8A::SetCooperativeLevel called from",
            "flags 0x5;");
    const uint32_t fmt = SCRATCH + 0x600u;
    pk32(fmt + 0u, 0x18u); pk32(fmt + 4u, 0x10u); pk32(fmt + 8u, 2u);
    pk32(fmt + 12u, 0x80u); pk32(fmt + 16u, 256u); pk32(fmt + 20u, 0x007C0CF0u);
    SLOT(kbd, 11, 1, fmt);
    refused("SetDataFormat of a 128-byte keyboard format",
            "IDirectInputDevice8A::SetDataFormat called from",
            "dwDataSize 0x80, dwNumObjs 256}, not c_dfDIKeyboard");
    SLOT0(kbd, 25);
    refused("Poll", "IDirectInputDevice8A::Poll called from", "no site in the image calls it");
    SLOT0(di8, 2);
    refused("IDirectInput8A::Release, which the image never calls",
            "IDirectInput8A::Release called from", "no site in the image calls it");
    {
        const uint32_t vt = peek32(di8);
        call_at(peek32(vt + 4u * 20u), 1u, (const uint32_t[]){di8}, 8u);
        refused("IDirectInput8A slot 20", "IDirectInput8A vtable slot 20 called from",
                "this runtime does not name that slot");
    }
    /* A slot past the four the image scans (0x00864EEB, 0x00864A70) is no
     * slot at all: refused by name, not answered ERROR_BAD_ARGUMENTS. */
    IMP("XINPUT1_3.dll", "Ordinal#4", 3, 4u, 1u, SCRATCH + 0x400u);
    refused("XInputGetCapabilities(4)", "XINPUT1_3!Ordinal#4 called from",
            "for slot 4: the image scans slots 0 to 3 only");
}

static void check_instance(const uint8_t *ddi, const uint8_t *instance, const uint8_t *product) {
    uint32_t size, devtype;
    uint16_t page, usage;
    memcpy(&size, ddi, 4);
    memcpy(&devtype, ddi + 0x24, 4);
    memcpy(&page, ddi + 0x240, 2);
    memcpy(&usage, ddi + 0x242, 2);
    static const uint8_t zero[16];
    check(size == 0x244u, "DIDEVICEINSTANCEA.dwSize is %#x", size);
    check(!memcmp(ddi + 4, instance, 16), "guidInstance is not the profile's");
    check(!memcmp(ddi + 0x14, product, 16), "guidProduct is not the profile's");
    check(devtype == GC_DEVTYPE, "dwDevType is %#x", devtype);
    check(!strcmp((const char *)ddi + 0x28, GC_NAME) && !strcmp((const char *)ddi + 0x12C, GC_NAME),
          "the names are not the profile's");
    check(!memcmp(ddi + 0x230, zero, 16), "guidFFDriver is not the profile's null GUID");
    check(page == 1u && usage == 6u, "wUsagePage %u wUsage %u", page, usage);
}

static void case_profile(void) {
    check(!sa2_devprofile_is_default() && sa2_dp_gamectrl_count() == 1
          && sa2_dp_wmi_pnp_count() == 2, "the probe's profile did not load: %s",
          sa2_devprofile_source());
    const uint32_t di8 = make_di8();
    if (!di8) return;

    /* EnumDevices hands the stand-in the profile's controller. */
    install_standin(1u);
    const uint32_t ref = SCRATCH + 0x800u;
    SLOT(di8, 4, 4, 4u, IMG_CALLBACK, ref, 1u);
    answered("EnumDevices", SA2_DI_OK);
    check(g_cb.calls == 1, "EnumDevices called back %u times, wanted 1", g_cb.calls);
    check(g_cb.ref == ref && g_cb.lpddi == SA2_DI_SCRATCH_BASE,
          "the callback got lpddi %#x pvRef %#x", g_cb.lpddi, g_cb.ref);
    check_instance(g_cb.ddi[0], GC_INSTANCE_B, GC_PRODUCT_B);

    /* The callback's CreateDevice, Unacquire and Release. */
    const uint32_t g = SCRATCH + 0x900u, out = SCRATCH + 0x910u;
    sa2_poke(g, 16, g_cb.ddi[0] + 4);
    create(di8, g, out);
    answered("CreateDevice(the enumerated instance)", SA2_DI_OK);
    const uint32_t dev = peek32(out);
    SLOT0(dev, 8);
    answered("the enumerated device's Unacquire", SA2_DI_NOEFFECT);
    SLOT0(dev, 2);
    answered("the enumerated device's Release", 0u);

    /* A capture's profile answers every scanned slot 1167 too. */
    xinput_slots();

    /* IsXInputDevice's WMI sequence, call by call. */
    sa2_heap_stats_t h0, h1;
    sa2_heap_stats(&h0);
    uint32_t loc = 0, svc = 0, names[3];
    const uint32_t en = wmi_open(&loc, &svc, names, 0u);
    check(bstr_is_ascii(names[0], "\\\\.\\root\\cimv2") && bstr_is_ascii(names[1], "Win32_PNPEntity")
          && bstr_is_ascii(names[2], "DeviceID"), "SysAllocString did not build the three BSTRs");
    check(loc == sa2_wbem_object_addr(SA2_WBEM_SLOT_LOCATOR)
          && svc == sa2_wbem_object_addr(SA2_WBEM_SLOT_SERVICES)
          && en == sa2_wbem_object_addr(SA2_WBEM_SLOT_ENUM),
          "the WMI objects are not at their role slots: %#x %#x %#x", loc, svc, en);
    const uint32_t objs = SCRATCH + 0xC00u, n = SCRATCH + 0xB0Cu, var = SCRATCH + 0xD00u;
    fill(objs, 80u, 0xCC);
    pk32(n, SENTINEL);
    SLOT(en, 4, 4, 10000u, 20u, objs, n);
    answered("Next with two entities left", SA2_WBEM_S_FALSE);
    check(peek32(n) == 2 && all_bytes(objs + 8u, 72u, 0xCC),
          "Next returned %u and wrote past them", peek32(n));
    for (uint32_t i = 0; i < 2; i++) {
        const uint32_t obj = peek32(objs + 4u * i);
        fill(var, 16u, 0xEE);
        SLOT(obj, 4, 5, names[2], 0u, var, 0u, 0u);
        answered("Get(DeviceID)", 0u);
        check(peek16(var) == 8u, "entity %u's VARIANT has vt %u", i, peek16(var));
        check(all_bytes(var + 2u, 6u, 0xEE) && all_bytes(var + 12u, 4u, 0xEE),
              "Get wrote VARIANT bytes Wine's set_variant does not");
        const uint32_t b = peek32(var + 8u);
        if (i == 0) check(bstr_is_ascii(b, IDS_TWO[0]), "entity 0's BSTR is not its DeviceID");
        else check(bstr_is(b, IDS_TWO_U16_1, sizeof IDS_TWO_U16_1 / 2u),
                   "entity 1's BSTR is not its DeviceID in UTF-16");
        SLOT0(obj, 2);
        answered("the class object's Release", 0u);
    }
    pk32(n, SENTINEL);
    SLOT(en, 4, 4, 10000u, 20u, objs, n);
    answered("Next at the end", SA2_WBEM_S_FALSE);
    check(peek32(n) == 0, "Next at the end returned %u", peek32(n));
    SLOT0(en, 2);
    answered("the enumerator's Release", 0u);
    SLOT0(loc, 2);
    answered("the locator's Release", 0u);
    SLOT0(svc, 2);
    answered("the services object's Release", 0u);
    for (uint32_t k = 0; k < 3; k++) IMP("OLEAUT32.dll", "Ordinal#6", 1, names[k]);
    IMP("OLEAUT32.dll", "Ordinal#6", 1, 0u);
    check(g_r.ok, "SysFreeString(NULL) did not return: %s", g_r.note);
    sa2_heap_stats(&h1);
    /* The three names are freed; the two DeviceIDs are not, as natively. */
    check(h1.live_blocks == h0.live_blocks + 2u, "live heap blocks went %u -> %u, wanted +2",
          h0.live_blocks, h1.live_blocks);

    sa2_wbem_stats ws;
    sa2_wbem_stats_get(&ws);
    check(ws.cocreate_calls == 1 && ws.connects == 1 && ws.blanket_calls == 1
          && ws.qi_client_security == 1 && ws.client_security_release == 1
          && ws.enumerators == 1 && ws.next_calls == 2 && ws.objects_returned == 2
          && ws.gets == 2 && ws.bstrs_allocated == 2 && ws.unknown_this == 0,
          "wbem stats: cocreate %u connect %u blanket %u enum %u next %u returned %u gets %u",
          ws.cocreate_calls, ws.connects, ws.blanket_calls, ws.enumerators, ws.next_calls,
          ws.objects_returned, ws.gets);
    fact("wbem_method_calls", ws.method_calls);
    fact("bstr_bytes", ws.bstr_bytes);

    /* The WMI refusals, last. */
    uint32_t loc2 = 0, svc2 = 0, names2[3];
    const uint32_t en2 = wmi_open(&loc2, &svc2, names2, 0u);
    IMP("ole32.dll", "CoCreateInstance", 5, IMG_CLSID_WBEM, 0u, 1u, IMG_IID_WBEM, SCRATCH + 0xB10u);
    refused("a second locator while the first is alive", "wbem: the IWbemLocator in slot 0",
            "is still alive with 1 references");
    IMP("ole32.dll", "CoCreateInstance", 5, IMG_CLSID_WBEM, 0u, 1u, IMG_IID_DI8A, SCRATCH + 0xB10u);
    refused("CoCreateInstance(CLSID_WbemLocator) for another IID",
            "ole32::CoCreateInstance(CLSID_WbemLocator) called from",
            "riid is not IID_IWbemLocator");
    SLOT(loc2, 3, 8, names2[1], 0u, 0u, 0u, 0u, 0u, 0u, SCRATCH + 0xB14u);
    refused("ConnectServer to another namespace", "IWbemLocator::ConnectServer called from",
            "strNetworkResource is not");
    SLOT(svc2, 18, 4, names2[0], 0u, 0u, SCRATCH + 0xB18u);
    refused("CreateInstanceEnum of another class", "IWbemServices::CreateInstanceEnum called from",
            "strFilter is not Win32_PNPEntity");
    SLOT(en2, 4, 4, 10000u, 10u, objs, n);
    refused("Next(10)", "IEnumWbemClassObject::Next called from", "lTimeout 10000 uCount 10;");
    SLOT(en2, 4, 4, 10000u, 20u, objs, n);
    answered("Next", SA2_WBEM_S_FALSE);
    SLOT(peek32(objs), 4, 5, names2[1], 0u, var, 0u, 0u);
    refused("Get of another property", "IWbemClassObject::Get called from",
            "wszName is not DeviceID");
    IMP("ole32.dll", "CoSetProxyBlanket", 8, loc2, 10u, 0u, 0u, 3u, 3u, 0u, 0u);
    refused("CoSetProxyBlanket on the locator", "ole32::CoSetProxyBlanket called from",
            "pProxy is not the live IWbemServices");
    IMP("ole32.dll", "CoSetProxyBlanket", 8, svc2, 9u, 0u, 0u, 3u, 3u, 0u, 0u);
    refused("CoSetProxyBlanket with another service", "ole32::CoSetProxyBlanket called from",
            "the blanket is not (10, 0, NULL, 3, 3, NULL, 0)");
    SLOT(svc2, 0, 2, IMG_IID_WBEM, SCRATCH + 0xB1Cu);
    refused("IWbemServices::QueryInterface, which the image never calls",
            "IWbemServices::QueryInterface called from", "no site in the image calls it");
    {
        const uint32_t vt = peek32(loc2);
        call_at(peek32(vt + 4u * 5u), 1u, (const uint32_t[]){loc2}, 8u);
        refused("IWbemLocator slot 5", "IWbemLocator vtable slot 5 called from",
                "this runtime does not name that slot");
    }
    SLOT0(dev, 8);
    refused("a released device's Unacquire", "IDirectInputDevice8A::Unacquire called from",
            "which is no live IDirectInputDevice8A");
}

/* Every pass-through answer of profile `hr` is a failure, and each comes back
 * as the literal the file holds, with NULL written where a device would be. */
static void case_profile_hr(void) {
    check(!sa2_devprofile_is_default() && sa2_dp_gamectrl_count() == 1,
          "the probe's profile did not load: %s", sa2_devprofile_source());
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t out = SCRATCH + 0x010u;
    create(di8, IMG_GUID_KBD, out);
    answered("CreateDevice(GUID_SysKeyboard) where the capture recorded 0x80040154",
             HR_CLASSNOTREG);
    check(peek32(out) == 0, "the failed keyboard CreateDevice left %#010x, not NULL", peek32(out));
    create(di8, IMG_GUID_MOUSE, out);
    answered("CreateDevice(GUID_SysMouse) where the capture recorded 0x80070005",
             HR_ACCESSDENIED);
    check(peek32(out) == 0, "the failed mouse CreateDevice left %#010x, not NULL", peek32(out));

    install_standin(1u);
    SLOT(di8, 4, 4, 4u, IMG_CALLBACK, SCRATCH + 0x800u, 1u);
    answered("EnumDevices", SA2_DI_OK);
    check(g_cb.calls == 1, "EnumDevices called back %u times, wanted 1", g_cb.calls);
    const uint32_t g = SCRATCH + 0x900u;
    sa2_poke(g, 16, g_cb.ddi[0] + 4);
    create(di8, g, out);
    answered("CreateDevice(the enumerated instance) where the capture recorded 0x80070005",
             HR_ACCESSDENIED);
    check(peek32(out) == 0, "the failed controller CreateDevice left %#010x, not NULL",
          peek32(out));
    check(sa2_dinput_device_count() == 0, "three failed CreateDevice calls built %u devices",
          sa2_dinput_device_count());

    /* The game ignores CoSetProxyBlanket's answer (0x00864771 reloads the
     * services pointer), so the sequence goes on past the failure. */
    uint32_t loc = 0, svc = 0, names[3];
    const uint32_t en = wmi_open(&loc, &svc, names, HR_FAIL);
    check(en == sa2_wbem_object_addr(SA2_WBEM_SLOT_ENUM),
          "CreateInstanceEnum after a failed blanket answered %#010x", en);
}

/* EnumDevices through the game's own callback 0x00864C90 and, where the
 * controller's CreateDevice succeeds, its lifted IsXInputDevice. `nested` is
 * the DirectInput calls the callback must make, with their answers; `ole` how
 * many times each of IsXInputDevice's imports must be called; `live` how many
 * heap blocks must stay allocated -- the DeviceID BSTRs, which the game never
 * frees. */
static const char *const OLE[6][2] = {
    {"ole32.dll", "CoInitialize"}, {"ole32.dll", "CoUninitialize"},
    {"ole32.dll", "CoCreateInstance"}, {"ole32.dll", "CoSetProxyBlanket"},
    {"OLEAUT32.dll", "Ordinal#2"}, {"OLEAUT32.dll", "Ordinal#6"},
};

static void through_the_game(const char *nested, const uint32_t ole[6], uint32_t live) {
    const sa2_ft_entry *e = sa2_ft_find(IMG_CALLBACK);
    check(e && e->kind == SA2_FN_LIFTED, "0x00864C90 is not a lifted function in this program");
    const uint32_t di8 = make_di8();
    if (!di8 || !e) return;
    const uint32_t ref = SCRATCH + 0x800u;
    fill(ref, 0x80u, 0);
    pk32(ref + 4u, di8);                 /* InputImpl+4, which the callback reads at 0x00864C9A */
    uint32_t before[6];
    for (int i = 0; i < 6; i++) before[i] = import_calls(OLE[i][0], OLE[i][1]);
    sa2_heap_stats_t h0, h1;
    sa2_heap_stats(&h0);
    const uint32_t log0 = sa2_dinput_log_count();

    SLOT(di8, 4, 4, 4u, IMG_CALLBACK, ref, 1u);
    answered("EnumDevices through the game's callback", SA2_DI_OK);

    for (int i = 0; i < 6; i++) {
        const uint32_t d = import_calls(OLE[i][0], OLE[i][1]) - before[i];
        check(d == ole[i], "%s!%s was called %u times, wanted %u", OLE[i][0], OLE[i][1], d, ole[i]);
    }
    sa2_heap_stats(&h1);
    check(h1.live_blocks == h0.live_blocks + live, "live heap blocks went %u -> %u, wanted +%u",
          h0.live_blocks, h1.live_blocks, live);
    char seq[160] = "";
    uint32_t n = 0;
    for (uint32_t i = log0; i < sa2_dinput_log_count(); i++) {
        const sa2_di_call *c = sa2_dinput_log_at(i);
        if (!c || c->depth != 2) continue;
        n++;
        const char *nm = sa2_dinput_method_name(c->type, c->slot);
        char one[48];
        snprintf(one, sizeof one, "%s%s=0x%08x", seq[0] ? "," : "", nm ? nm : "?", c->ret);
        strncat(seq, one, sizeof seq - strlen(seq) - 1);
    }
    check(!strcmp(seq, nested), "the callback's DirectInput calls were %s, wanted %s", seq, nested);
    const sa2_di_enum *en = sa2_dinput_enum_at(0);
    check(en && en->offered == 1u && en->last_callback_ret == 1u,
          "the enumeration offered %u controllers and the callback last answered %u, wanted 1 "
          "and 1 (0x00864CDC, 0x00864CF1)", en ? en->offered : 0u, en ? en->last_callback_ret : 0u);
    check(sa2_guest_stdcall_count() == 1 && sa2_guest_stdcall_max_depth() == 1,
          "guest stdcalls %u, depth %u", sa2_guest_stdcall_count(), sa2_guest_stdcall_max_depth());
    fact("nested_dinput_calls", n);
}

/* The call sequence native made (native-outcomes-v1a:14782, :15241, :15243):
 * CreateDevice, IsXInputDevice's whole WMI query, and for a device that is not
 * XInput, Unacquire and Release. */
static void case_profile_guest(void) {
    static const uint32_t ONCE[6] = {1, 1, 1, 1, 3, 3};
    through_the_game("CreateDevice=0x00000000,Unacquire=0x00000001,Release=0x00000000", ONCE, 2u);
    const sa2_di_device *dev = sa2_dinput_device_at(0);
    check(dev && dev->kind == SA2_DI_KIND_ENUMERATED && dev->released == 1u,
          "the enumerated device is %s", dev && dev->released ? "released" : "alive");
    sa2_wbem_stats ws;
    sa2_wbem_stats_get(&ws);
    check(ws.cocreate_calls == 1 && ws.connects == 1 && ws.blanket_calls == 1
          && ws.enumerators == 1 && ws.gets == 2 && ws.bstrs_allocated == 2
          && ws.next_calls == 2u && ws.objects_returned == 2,
          "wbem stats: cocreate %u connect %u blanket %u enum %u next %u gets %u",
          ws.cocreate_calls, ws.connects, ws.blanket_calls, ws.enumerators, ws.next_calls,
          ws.gets);
    for (uint32_t s = 0; s < SA2_WBEM_FIRST_ENTITY + 2u; s++)
        check(!sa2_wbem_object_alive(s), "WMI object slot %u is still alive after "
                                         "IsXInputDevice returned", s);
    fact("wbem_method_calls", ws.method_calls);
}

/* CreateDevice answers the capture's 0x80070005 inside the callback, and the
 * jl at 0x00864CB2 goes straight to the return: no IsXInputDevice, so no OLE
 * import, no WMI object and no BSTR -- the branch a host whose controller
 * failed to open takes natively. */
static void case_profile_hr_guest(void) {
    static const uint32_t NONE[6] = {0, 0, 0, 0, 0, 0};
    through_the_game("CreateDevice=0x80070005", NONE, 0u);
    check(sa2_dinput_device_count() == 0, "a failed CreateDevice built %u devices",
          sa2_dinput_device_count());
    sa2_wbem_stats ws;
    sa2_wbem_stats_get(&ws);
    check(ws.cocreate_calls == 0 && ws.method_calls == 0 && ws.blanket_calls == 0,
          "IsXInputDevice ran after a failed CreateDevice: cocreate %u, methods %u, blanket %u",
          ws.cocreate_calls, ws.method_calls, ws.blanket_calls);
}

static void case_batch(void) {
    check(sa2_dp_wmi_pnp_count() == 21, "the batch profile has %u entities", sa2_dp_wmi_pnp_count());
    uint32_t loc = 0, svc = 0, names[3];
    const uint32_t en = wmi_open(&loc, &svc, names, 0u);
    const uint32_t objs = SCRATCH + 0xC00u, n = SCRATCH + 0xB0Cu;
    static const uint32_t WANT_HR[] = {SA2_WBEM_S_NO_ERROR, SA2_WBEM_S_FALSE, SA2_WBEM_S_FALSE};
    static const uint32_t WANT_N[] = {20, 1, 0};
    uint32_t entity = 0;
    for (int k = 0; k < 3; k++) {
        pk32(n, SENTINEL);
        SLOT(en, 4, 4, 10000u, 20u, objs, n);
        answered("Next", WANT_HR[k]);
        check(peek32(n) == WANT_N[k], "Next %d returned %u, wanted %u", k, peek32(n), WANT_N[k]);
        for (uint32_t i = 0; i < peek32(n) && i < 20u; i++, entity++) {
            const uint32_t obj = peek32(objs + 4u * i);
            check(obj == sa2_wbem_object_addr(SA2_WBEM_FIRST_ENTITY + entity),
                  "entity %u's object is %#x", entity, obj);
            SLOT0(obj, 2);
            answered("Release", 0u);
        }
    }
    fact("entities", entity);
}

static void case_stop(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    install_standin(0u);                  /* DIENUM_STOP */
    SLOT(di8, 4, 4, 4u, IMG_CALLBACK, SCRATCH + 0x800u, 1u);
    answered("EnumDevices", SA2_DI_OK);
    check(g_cb.calls == 1, "a callback answering DIENUM_STOP was called %u times", g_cb.calls);
    const sa2_di_enum *e = sa2_dinput_enum_at(0);
    check(e && e->stopped == 1 && e->offered == 1, "the enumeration record does not show the stop");
}

/* ------------------------------------------------------------ the breaks */

static void brk_argc(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t bad0 = g_pops_bad;
    create(di8, IMG_GUID_KBD, SCRATCH + 0x010u);
    /* Declared with two arguments, the dispatcher pops 16 of the 20 bytes. */
    check(g_pops_bad == bad0 + 1 && g_bad_pop_delta == 16u,
          "CreateDevice popped %u bytes under the argc break", g_r.esp_delta);
    check(sa2_dinput_method_argc(0, 3) == 2, "the published argc is %d", sa2_dinput_method_argc(0, 3));
    g_pops_bad = bad0;                    /* the flip is the point, not a failure */
}

static void brk_bare_ok(void) {
    const uint32_t out = SCRATCH;
    pk32(out, SENTINEL);
    IMP("DINPUT8.dll", "DirectInput8Create", 5, 0x00400000u, 0x800u, IMG_IID_DI8A, out, 0u);
    answered("DirectInput8Create under bare-ok", SA2_DI_OK);
    check(peek32(out) == SENTINEL, "bare-ok wrote %#010x", peek32(out));
}

static void brk_noout(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    create(di8, IMG_GUID_KBD, SCRATCH + 0x010u);
    answered("CreateDevice under noout", SA2_DI_OK);
    check(peek32(SCRATCH + 0x010u) == SENTINEL, "noout wrote the out-pointer");
}

static void brk_nodevice(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    create(di8, IMG_GUID_KBD, SCRATCH + 0x010u);
    answered("CreateDevice(GUID_SysKeyboard) under nodevice", SA2_DIERR_DEVICENOTREG);
    check(peek32(SCRATCH + 0x010u) == 0, "nodevice left %#x", peek32(SCRATCH + 0x010u));
    create(di8, IMG_GUID_MOUSE, SCRATCH + 0x010u);
    answered("CreateDevice(GUID_SysMouse) under nodevice", SA2_DIERR_DEVICENOTREG);
}

static uint32_t g_brk_hwnd;
static uint32_t brk_keyboard(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return 0;
    g_brk_hwnd = make_window();
    return ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, g_brk_hwnd, "CreateDevice(keyboard)");
}

static void brk_notacquired(void) {
    const uint32_t kbd = brk_keyboard();
    if (!kbd) return;
    SLOT(kbd, 9, 2, 256u, SCRATCH + 0x100u);
    answered("GetDeviceState under notacquired", SA2_DIERR_NOTACQUIRED);
}

static void brk_inputlost(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t hwnd = make_window();
    const uint32_t out = SCRATCH + 0x010u;
    create(di8, IMG_GUID_KBD, out);
    const uint32_t kbd = peek32(out);
    SLOT(kbd, 11, 1, IMG_FMT_KBD);
    SLOT(kbd, 13, 2, hwnd, 6u);
    SLOT0(kbd, 7);
    answered("Acquire under inputlost", SA2_DIERR_INPUTLOST);
    uint32_t n = 1;
    while (n < SA2_DI_INPUTLOST_SPIN + 4u) {
        SLOT0(kbd, 7);
        if (!g_r.ok) break;
        n++;
    }
    check(n == SA2_DI_INPUTLOST_SPIN - 1u, "the spin stopped after %u answers, wanted %u",
          n, SA2_DI_INPUTLOST_SPIN - 1u);
    refused("the Acquire spin", "IDirectInputDevice8A::Acquire:",
            "DIERR_INPUTLOST answers in a row");
}

static void brk_allkeys(void) {
    const uint32_t kbd = brk_keyboard();
    if (!kbd) return;
    SLOT(kbd, 9, 2, 256u, SCRATCH + 0x100u);
    answered("GetDeviceState under allkeys", SA2_DI_OK);
    check(all_bytes(SCRATCH + 0x100u, 256u, 0x80), "allkeys did not hold every key");
    sa2_di_stats st;
    sa2_dinput_stats_get(&st);
    check(st.state_answers_nonidle == 1, "nonidle answers %u", st.state_answers_nonidle);
}

static void brk_mousemove(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t mouse = ready_device(di8, IMG_GUID_MOUSE, IMG_FMT_MOUSE, make_window(),
                                        "CreateDevice(mouse)");
    SLOT(mouse, 9, 2, 16u, SCRATCH + 0x100u);
    answered("GetDeviceState under mousemove", SA2_DI_OK);
    check(peek32(SCRATCH + 0x100u) == 5u && peek32(SCRATCH + 0x104u) == 3u
          && all_bytes(SCRATCH + 0x108u, 8u, 0), "mousemove did not move the mouse");
}

/* ------------------------------------------------------ the host keyboard
 *
 * Each case's files are <scratch>/dinput-probe.<case>.<what>; INPUT_ROWS says
 * what its setup makes and which variables it sets, before the configuration
 * reads them. */

typedef struct {
    const char *name;          /* the case */
    const char *script;        /* SA2_INPUT_DI_SCRIPT: this text, in <case>.script.txt */
    const char *script_of;     /* ... or the log the case of this name wrote */
    int         fifo;          /* SA2_INPUT_DI_SOURCE: 1 a FIFO made afresh, 2 a regular file,
                                  3 a path with nothing there */
    int         log;           /* SA2_INPUT_DI_LOG: <case>.log.txt, removed first; 2 a FIFO
                                  with no reader made there */
    const char *log_before;    /* ... and holding this when the run starts */
    size_t      script_bytes;  /* the script's length when it holds a NUL; 0: strlen */
} input_row;

/* Polls 0 to 7 hold: -, -, 1C C8, 1C 2D C8, 1C 2D, -, 1C, -. 0x1C is listed by
 * two lines that overlap at poll 3, and stays held at poll 4, after the first
 * of them ends. */
#define SCRIPT_TEXT "# the probe's keys\n2 3 0x1C,0xc8\n\n3 4 0x2D,0x1C   # overlaps poll 3\n" \
                    "6 6 0x1c\n"
#define SCRIPT_LOG  "2 2 0x1C,0xC8\n3 3 0x1C,0x2D,0xC8\n4 4 0x1C,0x2D\n6 6 0x1C\n"
/* A NUL inside a key line, which a parse of the C string would stop at. */
#define NUL_SCRIPT  "1 2 0x1C\0,0x1D\n"

static const input_row INPUT_ROWS[] = {
    {"input-script", SCRIPT_TEXT, NULL, 0, 1, NULL, 0},
    {"input-script-replay", NULL, "input-script", 0, 0, NULL, 0},
    {"input-script-empty", "# nothing held\n\n  \t\n# at any poll\n", NULL, 0, 1, NULL, 0},
    {"input-script-exit", "1 4294967295 0x39\n", NULL, 0, 1, NULL, 0},
    {"input-fifo", NULL, NULL, 1, 1, NULL, 0},
    {"input-fifo-stop", NULL, NULL, 1, 0, NULL, 0},
    {"input-fifo-stop-posted", NULL, NULL, 1, 1, NULL, 0},
    {"input-fifo-badmagic", NULL, NULL, 1, 0, NULL, 0},
    {"input-fifo-replaced", NULL, NULL, 1, 0, NULL, 0},
    {"refuse-input-both", "1 1 0x1C\n", NULL, 1, 0, NULL, 0},
    {"refuse-input-log-alone", NULL, NULL, 0, 1, NULL, 0},
    {"refuse-input-device-off", NULL, NULL, 1, 0, NULL, 0},
    {"refuse-input-break", "1 1 0x1C\n", NULL, 0, 0, NULL, 0},
    {"refuse-input-order", "5 3 0x1C\n", NULL, 0, 0, NULL, 0},
    {"refuse-input-dik", "# fine\n1 2 0x1C,1D\n", NULL, 0, 0, NULL, 0},
    {"refuse-input-fields", "1 2\n", NULL, 0, 0, NULL, 0},
    {"refuse-input-range", "1 2 0x100\n", NULL, 0, 0, NULL, 0},
    {"refuse-input-log-held", "1 1 0x1C\n", NULL, 0, 1, "0 0 0x1C\n", 0},
    {"refuse-input-script-missing", NULL, "no-such-case", 0, 0, NULL, 0},
    {"refuse-input-nul", NUL_SCRIPT, NULL, 0, 0, NULL, sizeof NUL_SCRIPT - 1u},
    {"refuse-input-zero", "1 2 0x1C,0x00\n", NULL, 0, 0, NULL, 0},
    {"refuse-input-log-fifo", "1 1 0x1C\n", NULL, 0, 2, NULL, 0},
    {"refuse-input-notfifo", NULL, NULL, 2, 0, NULL, 0},
    {"refuse-input-fifo-missing", NULL, NULL, 3, 0, NULL, 0},
};

static void in_path(const char *name, const char *what, char *out, size_t cap) {
    snprintf(out, cap, "%s/dinput-probe.%s.%s", g_scratch, name, what);
}

static int put_bytes(const char *path, const char *text, size_t n) {
    FILE *f = fopen(path, "wb");
    if (!f) return 0;
    const int ok = fwrite(text, 1, n, f) == n;
    return (fclose(f) == 0) && ok;
}

static int put_file(const char *path, const char *text) { return put_bytes(path, text, strlen(text)); }

/* A case's files and variables, before the configuration. */
static void input_setup(const char *name) {
    const input_row *r = NULL;
    for (size_t i = 0; i < sizeof INPUT_ROWS / sizeof INPUT_ROWS[0]; i++)
        if (!strcmp(INPUT_ROWS[i].name, name)) r = &INPUT_ROWS[i];
    if (!r) { fprintf(stderr, "case %s has no input row\n", name); _exit(2); }
    char p[600];
    if (r->script || r->script_of) {
        if (r->script) {
            in_path(name, "script.txt", p, sizeof p);
            if (!put_bytes(p, r->script, r->script_bytes ? r->script_bytes : strlen(r->script))) {
                fprintf(stderr, "cannot write %s\n", p);
                _exit(2);
            }
        } else {
            in_path(r->script_of, "log.txt", p, sizeof p);
        }
        setenv("SA2_INPUT_DI_SCRIPT", p, 1);
    }
    if (r->fifo) {
        in_path(name, "fifo", p, sizeof p);
        unlink(p);
        if (r->fifo == 1 ? mkfifo(p, 0600) != 0 : r->fifo == 2 ? !put_file(p, "") : 0) {
            fprintf(stderr, "cannot make %s: %s\n", p, strerror(errno));
            _exit(2);
        }
        setenv("SA2_INPUT_DI_SOURCE", p, 1);
    }
    if (r->log) {
        in_path(name, "log.txt", p, sizeof p);
        unlink(p);
        if (r->log_before && !put_file(p, r->log_before)) { fprintf(stderr, "cannot write %s\n", p); _exit(2); }
        if (r->log == 2) {
            if (mkfifo(p, 0600) != 0) { fprintf(stderr, "cannot make %s: %s\n", p, strerror(errno)); _exit(2); }
            alarm(20);                /* a configuration that waits on it fails, not hangs */
        }
        setenv("SA2_INPUT_DI_LOG", p, 1);
    }
}

/* What a case's log holds past its first line, which must be a comment. */
static int log_holds(const char *name, const char *want) {
    char p[600];
    in_path(name, "log.txt", p, sizeof p);
    FILE *f = fopen(p, "rb");
    if (!f) return 0;
    static char buf[4096];
    const size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = 0;
    const char *nl = strchr(buf, '\n');
    return buf[0] == '#' && nl && !strcmp(nl + 1, want);
}

/* Every byte of the 256 at `buf` is 0x80 for the `n` keys and 0 otherwise. */
static int keys_are(uint32_t buf, const uint8_t *keys, uint32_t n) {
    for (uint32_t i = 0; i < 256u; i++) {
        uint8_t b = 0xFF, want = 0;
        for (uint32_t j = 0; j < n; j++) if (keys[j] == i) want = 0x80;
        if (sa2_peek(buf + i, 1, &b) != SA2_OK || b != want) return 0;
    }
    return 1;
}

static uint8_t last_answer(void) {
    const sa2_di_call *c = sa2_dinput_log_count() ? sa2_dinput_log_at(sa2_dinput_log_count() - 1u)
                                                  : NULL;
    return c ? c->answered : 0xFFu;
}

/* One keyboard poll into a buffer of 0xA5: its 256 bytes are `keys`, nothing
 * past them moved, and the call was answered from the host exactly when a key
 * was held. */
static void poll_is(uint32_t kbd, uint32_t poll, const uint8_t *keys, uint32_t n) {
    const uint32_t buf = SCRATCH + 0x100u;
    fill(buf, 260u, 0xA5);
    SLOT(kbd, 9, 2, 256u, buf);
    answered("GetDeviceState(256)", SA2_DI_OK);
    check(keys_are(buf, keys, n), "poll %u: the keyboard bytes are not the %u key(s) wanted", poll, n);
    check(all_bytes(buf + 256u, 4u, 0xA5), "poll %u wrote past 256 bytes", poll);
    const uint8_t want = n ? SA2_DI_ANS_HOST : SA2_DI_ANS_IDLE;
    check(last_answer() == want, "poll %u was answered as class %u, wanted %u", poll, last_answer(),
          want);
}

typedef struct { uint32_t n; uint8_t k[3]; } poll_keys;
static const poll_keys SCRIPT_POLLS[8] = {
    {0, {0}}, {0, {0}}, {2, {0x1C, 0xC8}}, {3, {0x1C, 0x2D, 0xC8}}, {2, {0x1C, 0x2D}}, {0, {0}},
    {1, {0x1C}}, {0, {0}},
};

/* A keyboard and a mouse, ready; the keyboard's eight polls hold SCRIPT_POLLS,
 * with a mouse poll after the third, which must answer a still mouse and take
 * no keyboard poll index. */
static void script_polls(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t hwnd = make_window();
    const uint32_t kbd = ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, hwnd, "CreateDevice(keyboard)");
    const uint32_t mouse = ready_device(di8, IMG_GUID_MOUSE, IMG_FMT_MOUSE, hwnd,
                                        "CreateDevice(mouse)");
    for (uint32_t p = 0; p < 8u; p++) {
        poll_is(kbd, p, SCRIPT_POLLS[p].k, SCRIPT_POLLS[p].n);
        if (p != 2u) continue;
        fill(SCRATCH + 0x300u, 16u, 0xA5);
        SLOT(mouse, 9, 2, 16u, SCRATCH + 0x300u);
        answered("the mouse's GetDeviceState(16) beside a host keyboard", SA2_DI_OK);
        check(all_bytes(SCRATCH + 0x300u, 16u, 0) && last_answer() == SA2_DI_ANS_IDLE,
              "the mouse was not still beside a host keyboard");
    }
}

/* The calls and counters of plain_polls with no host keyboard: the literals
 * the device answered before the host keyboard existed. */
static void plain_polls(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t hwnd = make_window();
    const uint32_t kbd = ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, hwnd, "CreateDevice(keyboard)");
    for (uint32_t p = 0; p < 3u; p++) poll_is(kbd, p, NULL, 0);
    const uint32_t mouse = ready_device(di8, IMG_GUID_MOUSE, IMG_FMT_MOUSE, hwnd,
                                        "CreateDevice(mouse)");
    fill(SCRATCH + 0x300u, 16u, 0xA5);
    SLOT(mouse, 9, 2, 16u, SCRATCH + 0x300u);
    answered("GetDeviceState(16)", SA2_DI_OK);
    check(all_bytes(SCRATCH + 0x300u, 16u, 0), "the mouse state is not 16 zero bytes");
    sa2_di_stats st;
    sa2_dinput_stats_get(&st);
    check(st.method_calls == 12u && st.object == 2u && st.from_guest == 4u && st.noop == 2u
          && st.idle == 4u && st.host == 0u && st.brk == 0u && st.refused == 0u
          && st.invalid == 0u && st.profile == 0u && st.state_bytes_written == 784u
          && st.state_answers_nonidle == 0u && st.unknown_this == 0u,
          "counters: calls %u object %u guest %u noop %u idle %u host %u break %u refused %u "
          "invalid %u profile %u bytes %u nonidle %u", st.method_calls, st.object, st.from_guest,
          st.noop, st.idle, st.host, st.brk, st.refused, st.invalid, st.profile,
          (uint32_t)st.state_bytes_written, st.state_answers_nonidle);
    fact("method_calls", st.method_calls);
    fact("state_bytes_written", (uint32_t)st.state_bytes_written);
}

static void case_input_unset(void) {
    plain_polls();
    sa2_di_input_stats in;
    sa2_dinput_input_stats_get(&in);
    const uint64_t moved = (uint64_t)in.polls + in.polls_held + in.keys_written + in.script_lines
                           + in.records + in.last_seq + in.seq_gaps + in.polls_without_writer
                           + in.partial_dropped + in.log_lines + in.stop_requested
                           + in.quit_posted + in.quit_poll;
    check(!strcmp(sa2_dinput_input_source(), "") && moved == 0u,
          "with no host keyboard the source is \"%s\" and its counters sum to %llu",
          sa2_dinput_input_source(), (unsigned long long)moved);
}

static void case_input_script(void) {
    check(!strcmp(sa2_dinput_input_source(), "script"), "the source is \"%s\"",
          sa2_dinput_input_source());
    script_polls();
    sa2_di_input_stats in;
    sa2_dinput_input_stats_get(&in);
    check(in.polls == 8u && in.polls_held == 4u && in.keys_written == 8u && in.script_lines == 3u
          && in.log_lines == 4u && in.records == 0u,
          "input counters: polls %u held %u keys %u lines %u log %u records %u", in.polls,
          in.polls_held, (uint32_t)in.keys_written, in.script_lines, in.log_lines, in.records);
    sa2_di_stats st;
    sa2_dinput_stats_get(&st);
    check(st.host == 4u && st.idle == 5u && st.brk == 0u && st.state_answers_nonidle == 0u,
          "answers: host %u idle %u break %u nonidle %u", st.host, st.idle, st.brk,
          st.state_answers_nonidle);
    check(log_holds("input-script", SCRIPT_LOG), "the log is not one line per run of polls");
    fact("log_lines", in.log_lines);
}

static void case_input_script_replay(void) {
    script_polls();
    sa2_di_input_stats in;
    sa2_dinput_input_stats_get(&in);
    check(in.script_lines == 4u && in.polls == 8u && in.polls_held == 4u && in.keys_written == 8u,
          "the log replayed as %u lines, %u polls, %u holding, %u keys", in.script_lines, in.polls,
          in.polls_held, (uint32_t)in.keys_written);
}

static void case_input_script_empty(void) {
    plain_polls();
    sa2_di_input_stats in;
    sa2_dinput_input_stats_get(&in);
    check(in.polls == 3u && in.polls_held == 0u && in.keys_written == 0u && in.script_lines == 0u
          && in.log_lines == 0u, "an empty script held keys: polls %u held %u lines %u log %u",
          in.polls, in.polls_held, in.script_lines, in.log_lines);
    check(log_holds("input-script-empty", ""), "the empty script's log holds a key line");
}

/* The run still open at exit: a child polls three times (Space held from
 * poll 1 on) and leaves through exit(), whose handler writes the line. */
static void case_input_script_exit(void) {
    fflush(NULL);                     /* nothing buffered here is written twice by the child */
    const pid_t pid = fork();
    if (pid == 0) {
        const uint32_t di8 = make_di8();
        const uint32_t kbd = di8 ? ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, make_window(),
                                                "CreateDevice(keyboard)") : 0u;
        static const uint8_t space[1] = {0x39};
        if (kbd) {
            poll_is(kbd, 0, NULL, 0);
            poll_is(kbd, 1, space, 1);
            poll_is(kbd, 2, space, 1);
        }
        /* Before exit the run is open, so the log holds its first line alone. */
        if (g_fail || !kbd || !log_holds("input-script-exit", "")) _exit(4);
        exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    check(WIFEXITED(status) && WEXITSTATUS(status) == 0, "the polling child ended with status %#x",
          status);
    check(log_holds("input-script-exit", "1 2 0x39\n# closed at exit: 3 keyboard polls, 2 holding "
                    "keys, 1 lines; script of 1 lines\n"),
          "the exit handler did not write the open run and the counters");
}

/* One record, bytes [from, to) of it, into the FIFO. */
static void put_record(int fd, uint32_t magic, uint32_t seq, const uint8_t *keys, uint32_t n,
                       uint32_t from, uint32_t to) {
    uint8_t r[SA2_DI_KEYREC_BYTES];
    memset(r, 0, sizeof r);
    for (uint32_t i = 0; i < 4u; i++) {
        r[i] = (uint8_t)(magic >> (8u * i));
        r[4u + i] = (uint8_t)(seq >> (8u * i));
    }
    for (uint32_t j = 0; j < n; j++) r[8u + keys[j]] = 0x80;
    check(write(fd, r + from, to - from) == (ssize_t)(to - from), "a record's write to the FIFO "
          "was short");
}

static int input_fifo_fd(const char *name, int flags) {
    char p[600];
    in_path(name, "fifo", p, sizeof p);
    const int fd = open(p, flags | O_NONBLOCK);
    check(fd >= 0, "the probe cannot open %s: %s", p, strerror(errno));
    return fd;
}

static void case_input_fifo(void) {
    check(!strcmp(sa2_dinput_input_source(), "fifo"), "the source is \"%s\"",
          sa2_dinput_input_source());
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t kbd = ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, make_window(),
                                      "CreateDevice(keyboard)");
    /* O_RDWR: a writer the stub never sees end of file through, and a reader,
     * so the probe's own open never waits. */
    const int w = input_fifo_fd("input-fifo", O_RDWR);
    if (w < 0) return;
    static const uint8_t enter[1] = {0x1C}, up[1] = {0xC8}, zx[2] = {0x2C, 0x2D},
                         zxup[3] = {0x2C, 0x2D, 0xC8}, esc[1] = {0x01}, space[1] = {0x39},
                         space0[2] = {0x00, 0x39};
    poll_is(kbd, 0, NULL, 0);                                        /* nothing written yet */
    put_record(w, SA2_DI_KEYREC_MAGIC, 0, enter, 1, 0, SA2_DI_KEYREC_BYTES);
    put_record(w, SA2_DI_KEYREC_MAGIC, 1, up, 1, 0, 100);
    poll_is(kbd, 1, enter, 1);                                       /* the partial one waits */
    put_record(w, SA2_DI_KEYREC_MAGIC, 1, up, 1, 100, SA2_DI_KEYREC_BYTES);
    put_record(w, SA2_DI_KEYREC_MAGIC, 2, zx, 2, 0, SA2_DI_KEYREC_BYTES);
    poll_is(kbd, 2, zxup, 3);              /* every key the poll's records held: Up, let go in
                                              the second, is held at this poll */
    poll_is(kbd, 3, zx, 2);                /* no record: the latest's keys, not Up */
    put_record(w, SA2_DI_KEYREC_MAGIC, 5, NULL, 0, 0, SA2_DI_KEYREC_BYTES);   /* a seq gap */
    poll_is(kbd, 4, NULL, 0);
    put_record(w, SA2_DI_KEYREC_MAGIC, 6, esc, 1, 0, SA2_DI_KEYREC_BYTES);
    close(w);                                                        /* no writer: end of file */
    poll_is(kbd, 5, esc, 1);               /* read with the end of file: held at its poll */
    const int w2 = input_fifo_fd("input-fifo", O_WRONLY);            /* a second writer */
    if (w2 < 0) return;
    put_record(w2, SA2_DI_KEYREC_MAGIC, 7, space0, 2, 0, SA2_DI_KEYREC_BYTES);   /* byte 0 too */
    put_record(w2, SA2_DI_KEYREC_MAGIC, 8, enter, 1, 0, 10);
    poll_is(kbd, 6, space, 1);             /* every key up after the end of file; byte 0 not held */
    close(w2);
    poll_is(kbd, 7, NULL, 0);                                        /* its partial record dropped */
    sa2_di_input_stats in;
    sa2_dinput_input_stats_get(&in);
    check(in.polls == 8u && in.polls_held == 5u && in.keys_written == 8u && in.records == 6u
          && in.last_seq == 7u && in.seq_gaps == 1u && in.polls_without_writer == 2u
          && in.partial_dropped == 10u && in.log_lines == 5u && in.stop_requested == 0u,
          "input counters: polls %u held %u keys %u records %u seq %u gaps %u no-writer %u "
          "dropped %u log %u", in.polls, in.polls_held, (uint32_t)in.keys_written, in.records,
          in.last_seq, in.seq_gaps, in.polls_without_writer, in.partial_dropped, in.log_lines);
    check(log_holds("input-fifo", "1 1 0x1C\n2 2 0x2C,0x2D,0xC8\n3 3 0x2C,0x2D\n5 5 0x01\n"
                                  "6 6 0x39\n"),
          "the FIFO's log is not one line per run of polls");
}

static void case_input_fifo_stop(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t kbd = ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, make_window(),
                                      "CreateDevice(keyboard)");
    const int w = input_fifo_fd("input-fifo-stop", O_RDWR);
    if (w < 0) return;
    sa2_wnd_stats ws;
    poll_is(kbd, 0, NULL, 0);
    raise(SIGTERM);
    sa2_window_stats(&ws);
    check(!ws.quit_posted, "WM_QUIT was posted before a keyboard poll");
    poll_is(kbd, 1, NULL, 0);
    sa2_window_stats(&ws);
    sa2_di_input_stats in;
    sa2_dinput_input_stats_get(&in);
    check(ws.quit_posted && ws.quit_by_host && !ws.quit_by_guest && in.stop_requested
          && in.quit_posted && in.quit_poll == 1u,
          "after SIGTERM and a poll: quit posted %u by host %u, stop %u posted %u at poll %u",
          ws.quit_posted, ws.quit_by_host, in.stop_requested, in.quit_posted, in.quit_poll);
    fflush(NULL);
    const pid_t pid = fork();
    if (pid == 0) {
        raise(SIGTERM);               /* the handler reset itself: the default action */
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    check(WIFSIGNALED(status) && WTERMSIG(status) == SIGTERM,
          "a second SIGTERM did not end the process: status %#x", status);
    close(w);
}

/* SIGTERM after the guest posted WM_QUIT itself: the poll posts none and
 * counts none, and the log's closing line, written at exit in a child, says
 * the SIGTERM came after it. */
static void case_input_fifo_stop_posted(void) {
    fflush(NULL);
    const pid_t pid = fork();
    if (pid == 0) {
        const uint32_t di8 = make_di8();
        const uint32_t kbd = di8 ? ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, make_window(),
                                                "CreateDevice(keyboard)") : 0u;
        const int w = kbd ? input_fifo_fd("input-fifo-stop-posted", O_RDWR) : -1;
        if (w < 0) _exit(4);
        poll_is(kbd, 0, NULL, 0);
        sa2_window_post_quit(0, 0);                       /* the guest's own WM_QUIT */
        raise(SIGTERM);
        poll_is(kbd, 1, NULL, 0);
        sa2_wnd_stats ws;
        sa2_window_stats(&ws);
        sa2_di_input_stats in;
        sa2_dinput_input_stats_get(&in);
        check(ws.quit_posted && ws.quit_by_guest && !ws.quit_by_host && in.stop_requested
              && !in.quit_posted && in.quit_poll == 0u,
              "a SIGTERM after the guest's WM_QUIT: quit posted %u by guest %u by host %u, "
              "stop %u, posted %u at poll %u", ws.quit_posted, ws.quit_by_guest, ws.quit_by_host,
              in.stop_requested, in.quit_posted, in.quit_poll);
        if (g_fail || g_pops_bad) {
            fprintf(stderr, "the polling child: %s; %u calls popped the wrong number of bytes\n",
                    g_first, g_pops_bad);
            _exit(4);
        }
        exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    check(WIFEXITED(status) && WEXITSTATUS(status) == 0, "the polling child ended with status %#x",
          status);
    check(log_holds("input-fifo-stop-posted", "# closed at exit: 2 keyboard polls, 0 holding keys, "
                    "0 lines; 0 records, last seq 0, 0 seq gaps, 0 polls with no writer, 0 bytes "
                    "of an unfinished record dropped; SIGTERM at poll 1, WM_QUIT posted already\n"),
          "the closing line does not say the SIGTERM came after a WM_QUIT");
}

static void case_input_fifo_badmagic(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t kbd = ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, make_window(),
                                      "CreateDevice(keyboard)");
    const int w = input_fifo_fd("input-fifo-badmagic", O_RDWR);
    if (w < 0) return;
    poll_is(kbd, 0, NULL, 0);
    put_record(w, SA2_DI_KEYREC_MAGIC + 1u, 0, NULL, 0, 0, SA2_DI_KEYREC_BYTES);
    SLOT(kbd, 9, 2, 256u, SCRATCH + 0x100u);
    refused("GetDeviceState after a record of another magic",
            "IDirectInputDevice8A::GetDeviceState called from",
            "SA2_INPUT_DI_SOURCE record 0 has magic 0x4b324154, not 0x4b324153");
    close(w);
}

/* A FIFO when the run was configured, a regular file by the first poll: the
 * open checks it again. */
static void case_input_fifo_replaced(void) {
    const uint32_t di8 = make_di8();
    if (!di8) return;
    const uint32_t kbd = ready_device(di8, IMG_GUID_KBD, IMG_FMT_KBD, make_window(),
                                      "CreateDevice(keyboard)");
    char p[600];
    in_path("input-fifo-replaced", "fifo", p, sizeof p);
    check(unlink(p) == 0 && put_file(p, ""), "the probe cannot replace %s: %s", p, strerror(errno));
    SLOT(kbd, 9, 2, 256u, SCRATCH + 0x100u);
    refused("GetDeviceState with the source replaced by a regular file",
            "IDirectInputDevice8A::GetDeviceState called from",
            "SA2_INPUT_DI_SOURCE is not a FIFO");
}

/* ------------------------------------------------------------- the runner */

typedef struct {
    const char *name;
    int         device;        /* SA2_DINPUT_DEVICE=1 */
    const char *profile;       /* a key of PROFILES, or NULL for the default */
    const char *brk;           /* SA2_DINPUT_BREAK, or NULL */
    const char *env;           /* one more NAME=VALUE for this case, or NULL */
    void      (*run)(void);    /* NULL: the configuration itself must refuse */
    const char *refusal;       /* a module's words for that refusal; NULL: the profile's reader's */
    void      (*setup)(const char *name);   /* files and variables before the configuration */
} probe_case;

static const probe_case CASES[] = {
    {"off", 0, NULL, NULL, NULL, case_off, NULL},
    {"default", 1, NULL, NULL, NULL, case_default, NULL},
    {"profile", 1, "two", NULL, NULL, case_profile, NULL},
    {"profile-guest", 1, "guest", NULL, NULL, case_profile_guest, NULL},
    {"profile-hr", 1, "hr", NULL, NULL, case_profile_hr, NULL},
    {"profile-hr-guest", 1, "hr", NULL, NULL, case_profile_hr_guest, NULL},
    {"batch", 1, "batch", NULL, NULL, case_batch, NULL},
    {"stop", 1, "stop", NULL, NULL, case_stop, NULL},
    {"brk-argc", 1, NULL, "argc", NULL, brk_argc, NULL},
    {"brk-bare-ok", 1, NULL, "bare-ok", NULL, brk_bare_ok, NULL},
    {"brk-noout", 1, NULL, "noout", NULL, brk_noout, NULL},
    {"brk-nodevice", 1, NULL, "nodevice", NULL, brk_nodevice, NULL},
    {"brk-notacquired", 1, NULL, "notacquired", NULL, brk_notacquired, NULL},
    {"brk-inputlost", 1, NULL, "inputlost", NULL, brk_inputlost, NULL},
    {"brk-allkeys", 1, NULL, "allkeys", NULL, brk_allkeys, NULL},
    {"brk-mousemove", 1, NULL, "mousemove", NULL, brk_mousemove, NULL},
    {"refuse-entities", 1, "entities", NULL, NULL, NULL, NULL},
    {"refuse-utf8", 1, "utf8", NULL, NULL, NULL, NULL},
    {"refuse-keyboard-controller", 1, "kbdctrl", NULL, NULL, NULL, NULL},
    {"refuse-mouse-controller", 1, "mousectrl", NULL, NULL, NULL, NULL},
    {"refuse-duplicate-instance", 1, "dup", NULL, NULL, NULL, NULL},
    {"refuse-device-off", 0, "two", NULL, NULL, NULL,
     "dinput: SA2_DINPUT_DEVICE is off and device profile"},
    {"refuse-bare-ok-capture", 1, "two", "bare-ok", NULL, NULL,
     "dinput: SA2_DINPUT_BREAK=bare-ok is refused under device profile"},
    {"refuse-nodevice-capture", 1, "two", "nodevice", NULL, NULL,
     "dinput: SA2_DINPUT_BREAK=nodevice is refused under device profile"},
    {"refuse-xinput-pad", 1, "two", NULL, "SA2_XINPUT_PAD=1", NULL,
     "SA2_XINPUT_PAD is set and device profile"},
    {"input-unset", 1, NULL, NULL, NULL, case_input_unset, NULL},
    {"input-script", 1, NULL, NULL, NULL, case_input_script, NULL, input_setup},
    {"input-script-replay", 1, NULL, NULL, NULL, case_input_script_replay, NULL, input_setup},
    {"input-script-empty", 1, NULL, NULL, NULL, case_input_script_empty, NULL, input_setup},
    {"input-script-exit", 1, NULL, NULL, NULL, case_input_script_exit, NULL, input_setup},
    {"input-fifo", 1, NULL, NULL, NULL, case_input_fifo, NULL, input_setup},
    {"input-fifo-stop", 1, NULL, NULL, NULL, case_input_fifo_stop, NULL, input_setup},
    {"input-fifo-stop-posted", 1, NULL, NULL, NULL, case_input_fifo_stop_posted, NULL, input_setup},
    {"input-fifo-badmagic", 1, NULL, NULL, NULL, case_input_fifo_badmagic, NULL, input_setup},
    {"input-fifo-replaced", 1, NULL, NULL, NULL, case_input_fifo_replaced, NULL, input_setup},
    {"refuse-input-both", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT and SA2_INPUT_DI_SOURCE are both set", input_setup},
    {"refuse-input-log-alone", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_LOG is set and neither SA2_INPUT_DI_SCRIPT nor SA2_INPUT_DI_SOURCE is",
     input_setup},
    {"refuse-input-device-off", 0, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SOURCE is set and SA2_DINPUT_DEVICE is off", input_setup},
    {"refuse-input-break", 1, NULL, "allkeys", NULL, NULL,
     "SA2_INPUT_DI_SCRIPT is set beside SA2_DINPUT_BREAK=allkeys", input_setup},
    {"refuse-input-order", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT line 1: the last poll 3 is before the first, 5", input_setup},
    {"refuse-input-dik", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT line 2: \"1D\" is not a DIK code", input_setup},
    {"refuse-input-fields", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT line 1: 2 fields, not three", input_setup},
    {"refuse-input-range", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT line 1: \"0x100\" is not a DIK code", input_setup},
    {"refuse-input-log-held", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_LOG is not an empty file", input_setup},
    {"refuse-input-script-missing", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT does not open: No such file or directory", input_setup},
    {"refuse-input-nul", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT line 1: it holds a NUL byte", input_setup},
    {"refuse-input-zero", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SCRIPT line 1: \"0x00\" is not a DIK code, 0x01 to 0xFF", input_setup},
    {"refuse-input-log-fifo", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_LOG is not a regular file", input_setup},
    {"refuse-input-notfifo", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SOURCE is not a FIFO", input_setup},
    {"refuse-input-fifo-missing", 1, NULL, NULL, NULL, NULL,
     "SA2_INPUT_DI_SOURCE: No such file or directory; it must be a FIFO made before the run",
     input_setup},
};

static const probe_profile *profile_named(const char *key, unsigned *index) {
    for (unsigned i = 0; i < NPROFILES; i++)
        if (!strcmp(PROFILES[i].key, key)) { if (index) *index = i; return &PROFILES[i]; }
    return NULL;
}

/* What a refusing case's note must hold: where -- the profile's file, and for
 * a reader's refusal the line of its defect record, as the reader's
 * "file:line: " prefix spells it -- and why, in the refusing code's words. */
static void refusal_of(const probe_case *pc, char *where, size_t cap, const char **why) {
    unsigned i = 0;
    const probe_profile *pp = pc->profile ? profile_named(pc->profile, &i) : NULL;
    /* No profile: the refusal's own words name where (the host keyboard's
     * variable, and the script's line), so the case must give them; one that
     * gives none wants a text no note holds, and fails. */
    if (!pp) {
        where[0] = 0;
        *why = pc->refusal && pc->refusal[0] ? pc->refusal
                                             : "\001a refusing case with no profile names no refusal";
        return;
    }
    if (pc->refusal) {
        snprintf(where, cap, "dinput-probe.%s.profile.txt", pp->key);
        *why = pc->refusal;
    } else {
        snprintf(where, cap, "dinput-probe.%s.profile.txt:%u: ", pp->key, g_defect_line[i]);
        *why = pp->refusal ? pp->refusal : "";
    }
}

static void publish_methods(FILE *f) {
    fprintf(f, " \"dinput\": {\"methods\": [");
    uint32_t n = 0;
    for (uint32_t t = 0; t < sa2_dinput_type_count(); t++)
        for (uint32_t k = 0; k < SA2_DI_MAX_SLOTS; k++) {
            const char *nm = sa2_dinput_method_name(t, k);
            if (!nm) continue;
            fprintf(f, "%s\n  {\"interface\": ", n++ ? "," : "");
            jstr(f, sa2_dinput_type_name(t));
            fprintf(f, ", \"name\": ");
            jstr(f, nm);
            fprintf(f, ", \"slot\": %u, \"argc\": %d, \"implemented\": %s}", k,
                    sa2_dinput_method_argc(t, k),
                    sa2_dinput_method_implemented(t, k) ? "true" : "false");
        }
    fprintf(f, "]},\n \"wbem\": {\"methods\": [");
    n = 0;
    for (uint32_t t = 0; t < sa2_wbem_type_count(); t++)
        for (uint32_t k = 0; k < SA2_WBEM_MAX_SLOTS; k++) {
            const char *nm = sa2_wbem_method_name(t, k);
            if (!nm) continue;
            fprintf(f, "%s\n  {\"interface\": ", n++ ? "," : "");
            jstr(f, sa2_wbem_type_name(t));
            fprintf(f, ", \"name\": ");
            jstr(f, nm);
            fprintf(f, ", \"slot\": %u, \"argc\": %d, \"implemented\": %s}", k,
                    sa2_wbem_method_argc(t, k),
                    sa2_wbem_method_implemented(t, k) ? "true" : "false");
        }
    fprintf(f, "]}");
}

/* What sa2_imports_config_from_env must have done for the case: configured
 * the device profile, then this device and the WMI objects from it. Each
 * module's count of the profile's records is the order check: a module
 * configured before the profile counts the default's none. Checked before the
 * case runs, so a lifted run whose configuration leaves a module out, or runs
 * it before the profile, fails here rather than passing on this probe's own
 * calls. */
static void configured(const probe_case *pc) {
    check(sa2_dinput_device_enabled() == pc->device,
          "sa2_imports_config_from_env left the DirectInput device %s, and the case sets "
          "SA2_DINPUT_DEVICE %s", sa2_dinput_device_enabled() ? "on" : "off",
          pc->device ? "on" : "off");
    check(!strcmp(sa2_dinput_break_name(), pc->brk ? pc->brk : ""),
          "sa2_imports_config_from_env left the DirectInput break \"%s\", and the case sets "
          "\"%s\"", sa2_dinput_break_name(), pc->brk ? pc->brk : "");
    check(sa2_devprofile_is_default() == !pc->profile,
          "sa2_imports_config_from_env loaded profile %s for a case whose profile is %s",
          sa2_devprofile_source(), pc->profile ? pc->profile : "the default");
    sa2_di_stats ds;
    sa2_dinput_stats_get(&ds);
    check(ds.profile_gamectrl == sa2_dp_gamectrl_count(),
          "the DirectInput device counted %u controllers and the profile lists %u: it was "
          "configured before the profile", ds.profile_gamectrl, sa2_dp_gamectrl_count());
    sa2_wbem_stats ws;
    sa2_wbem_stats_get(&ws);
    check(ws.profile_entities == sa2_dp_wmi_pnp_count(),
          "the WMI objects counted %u entities and the profile lists %u: they were "
          "configured before the profile", ws.profile_entities, sa2_dp_wmi_pnp_count());
}

/* One case, in this (forked) process: environment, configuration, binding,
 * the case, its record. The configuration is sa2_imports_config_from_env's
 * alone, as in a lifted run: this probe calls no module's configuration
 * itself, so it tests what that function calls and in what order
 * (configured(), above), and a refusing case passes only if that function
 * refuses.
 *
 * The environment is the case's alone, whatever the shell had. Under a
 * capture's profile the DirectDraw and XAudio2 devices are on as well,
 * because every device module refuses a capture's profile with its own device
 * off, and their breaks are cleared, because some of them are refused there
 * too; SA2_XINPUT_PAD and SA2_GUEST_CPUS are cleared, because the runtime
 * refuses either beside a capture's profile, unless the case sets one to see
 * that refusal. */
static int run_case(const probe_case *pc, const char *frag) {
    if (pc->device) setenv("SA2_DINPUT_DEVICE", "1", 1); else unsetenv("SA2_DINPUT_DEVICE");
    if (pc->brk) setenv("SA2_DINPUT_BREAK", pc->brk, 1); else unsetenv("SA2_DINPUT_BREAK");
    unsetenv("SA2_XAUDIO2_BREAK");
    unsetenv("SA2_DDRAW_BREAK");
    unsetenv("SA2_XINPUT_PAD");
    unsetenv("SA2_GUEST_CPUS");
    unsetenv("SA2_INPUT_DI_SCRIPT");
    unsetenv("SA2_INPUT_DI_SOURCE");
    unsetenv("SA2_INPUT_DI_LOG");
    if (pc->profile) {
        char p[600];
        profile_path(pc->profile, p, sizeof p);
        setenv("SA2_DEVICE_PROFILE", p, 1);
        setenv("SA2_DDRAW_DEVICE", "1", 1);
        setenv("SA2_XAUDIO2_DEVICE", "1", 1);
    } else {
        unsetenv("SA2_DEVICE_PROFILE");
        unsetenv("SA2_DDRAW_DEVICE");
        unsetenv("SA2_XAUDIO2_DEVICE");
    }
    if (pc->env) {
        char name[64];
        const char *eq = strchr(pc->env, '=');
        const size_t n = eq ? (size_t)(eq - pc->env) : 0u;
        if (!n || n >= sizeof name) { fprintf(stderr, "case %s: bad env %s\n", pc->name, pc->env); return 2; }
        memcpy(name, pc->env, n);
        name[n] = 0;
        setenv(name, eq + 1, 1);
    }
    if (pc->setup) pc->setup(pc->name);
    sa2_imports_config_from_env();
    if (!pc->run) {
        fprintf(stderr, "the configuration accepted a case it must refuse\n");
        return 1;
    }
    if (sa2_imports_bind() <= 0) { fprintf(stderr, "imports did not bind\n"); return 2; }
    FILE *f = fopen(frag, "w");
    if (!f) return 2;
    fprintf(f, "{\"case\": ");
    jstr(f, pc->name);
    fprintf(f, ", \"break\": ");
    jstr(f, pc->brk ? pc->brk : "");
    fprintf(f, ", \"profile\": ");
    jstr(f, sa2_devprofile_source());
    fprintf(f, ", \"profile_sha256\": ");
    jstr(f, sa2_devprofile_sha256());
    fprintf(f, ",\n  \"facts\": {");
    g_facts = f;
    configured(pc);
    if (!g_fail) pc->run();            /* a case on the wrong configuration proves nothing */
    check(g_pops_bad == 0, "%u of %u calls popped the wrong number of bytes, the last %u",
          g_pops_bad, g_pops, g_bad_pop_delta);
    g_facts = NULL;
    fprintf(f, "},\n  \"pops\": {\"calls\": %u, \"bad\": %u}, \"ok\": %s, \"checks\": %u, "
               "\"failures\": %u, \"first_failure\": ", g_pops, g_pops_bad,
            g_fail ? "false" : "true", g_checks, g_fail);
    jstr(f, g_first);
    fprintf(f, "}");
    fclose(f);
    return g_fail ? 1 : 0;
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

int main(int argc, char **argv) {
    const char *pe = NULL, *json_path = NULL, *scratch = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!strcmp(argv[i], "--scratch") && i + 1 < argc) scratch = argv[++i];
        else if (!pe) pe = argv[i];
    }
    if (!pe) { fprintf(stderr, "usage: dinput_probe <exe> [--json PATH] [--scratch DIR]\n"); return 2; }
    if (scratch) snprintf(g_scratch, sizeof g_scratch, "%s", scratch);
    else if (json_path) {
        snprintf(g_scratch, sizeof g_scratch, "%s", json_path);
        char *slash = strrchr(g_scratch, '/');
        if (slash) *slash = 0; else snprintf(g_scratch, sizeof g_scratch, ".");
    } else snprintf(g_scratch, sizeof g_scratch, ".");
    if (!write_profiles()) { fprintf(stderr, "cannot write the probe's profiles in %s\n", g_scratch); return 2; }
    if (sa2_runtime_init(pe, 4096) != SA2_OK) { fprintf(stderr, "runtime init failed\n"); return 2; }

    FILE *out = json_path ? fopen(json_path, "w") : NULL;
    if (out) fprintf(out, "{\n \"cases\": [");
    uint32_t cases_ok = 0, n = (uint32_t)(sizeof CASES / sizeof CASES[0]);
    char first[512] = "";
    for (uint32_t i = 0; i < n; i++) {
        const probe_case *pc = &CASES[i];
        char frag[600], err[600];
        snprintf(frag, sizeof frag, "%s/dinput-probe.%s.json", g_scratch, pc->name);
        snprintf(err, sizeof err, "%s/dinput-probe.%s.stderr", g_scratch, pc->name);
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
                fprintf(out, "%s\n  ", i ? "," : "");
                if (body && body[0]) fprintf(out, "%s", body);
                else { fprintf(out, "{\"case\": "); jstr(out, pc->name);
                       fprintf(out, ", \"ok\": false, \"exit\": %d}", code); }
            }
        } else {
            /* The configuration must refuse: sa2_trap_raise outside any call
             * prints the note and exits 3 (runtime/sa2_trap.c). */
            char where[200];
            const char *why = "";
            refusal_of(pc, where, sizeof where, &why);
            const char *text = slurp(err);
            const int named = text && strstr(text, where) != NULL && strstr(text, why) != NULL;
            ok = code == 3 && named;
            if (out) {
                fprintf(out, "%s\n  {\"case\": ", i ? "," : "");
                jstr(out, pc->name);
                fprintf(out, ", \"exit\": %d, \"wanted_exit\": 3, \"note_names\": [", code);
                jstr(out, where);
                fprintf(out, ", ");
                jstr(out, why);
                fprintf(out, "], \"named\": %s, \"ok\": %s}", named ? "true" : "false",
                        ok ? "true" : "false");
            }
        }
        cases_ok += (uint32_t)ok;
        if (!ok && !first[0]) snprintf(first, sizeof first, "case %s (exit %d)", pc->name, code);
        printf("dinput_probe: %-28s %s (exit %d)\n", pc->name, ok ? "ok" : "FAILED", code);
    }
    if (out) {
        fprintf(out, "],\n \"ok\": %s, \"cases_ok\": %u, \"case_count\": %u, \"first_failure\": ",
                cases_ok == n ? "true" : "false", cases_ok, n);
        jstr(out, first);
        fprintf(out, ",\n");
        publish_methods(out);
        fprintf(out, "\n}\n");
        fclose(out);
    }
    printf("dinput_probe: %u of %u cases ok%s%s\n", cases_ok, n, first[0] ? " -- first: " : "",
           first);
    return cases_ok == n ? 0 : 1;
}

#endif /* SA2_DINPUT_PROBE_PROFILES_ONLY */
