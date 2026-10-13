/* Harness level 2, native side: the host's device answers, for the lifted replay to read back.
 *
 * Why it exists: the user's directive of 2026-09-23. The user's keyboard
 * receiver stays plugged in for every capture and for the recording, and the
 * replay must reproduce it, WMI path included; no pad is attached; and "the
 * device set is a recorded property of each capture: the runner records it in
 * the receipt, the lifted replay reads the same profile back, and a run with
 * no capture uses a plain keyboard-and-mouse default. A capture whose profile
 * the runtime cannot reproduce is refused, not approximated." The game asks
 * the host about its devices before any probe's Init, where the import probe
 * cannot see it (agents-journals/s4/2026-09-23-native-outcomes.md). So this
 * asks the same questions again at Init, the same way, and writes the answers
 * to mods/sa2-device-profile.txt in runtime/sa2_devprofile.h's grammar, which
 * the runtime reads back through SA2_DEVICE_PROFILE.
 *
 * What it asks, each the game's own call repeated with the game's own
 * arguments (the GUIDs and wide strings are read from the image's .rdata, so
 * the bytes passed are the game's bytes):
 *
 *   - GetSystemInfo's dwNumberOfProcessors and GetProcessAffinityMask
 *     (GetCurrentProcess())'s process and system masks. The game reads the
 *     count at 0x007FB9C7 (Sofdec's initialiser 0x007FBC90 builds its three
 *     decoder workers only above one processor, the boot replay's last
 *     divergence in the journal above) and, at 0x00865288, tests a thread's
 *     ideal processor's bit of the system mask, the third argument of the
 *     call at 0x00865277. Both are properties of the process, fixed at its
 *     start: the image imports no SetProcessAffinityMask, so the answer at
 *     Init is the answer at the game's calls;
 *   - DirectInput8Create(the game's module, 0x800, IID_IDirectInput8A at
 *     0x0087355C), as 0x00864D14-0x00864D33 (native-outcomes-v1a/
 *     native-run.log:13955); then CreateDevice for GUID_SysKeyboard
 *     (0x0087360C, called at 0x00863781, :13961) and GUID_SysMouse
 *     (0x008735FC, 0x00863208, :14716), each device released at once;
 *   - EnumDevices(DI8DEVCLASS_GAMECTRL, DIEDFL_ATTACHEDONLY), as
 *     0x00864E0B-0x00864E19 (:14762), recording every DIDEVICEINSTANCEA field
 *     the profile carries, and inside the callback CreateDevice on the
 *     instance GUID, as the game's callback does at 0x00864CAE (:14782),
 *     since a failure there skips the WMI query (0x00864CB2). The device is
 *     released at once and never unacquired, acquired or configured;
 *   - XInputGetCapabilities(slot, XINPUT_FLAG_GAMEPAD) for slots 0 to 3,
 *     through ordinal 4 as the game imports it (IAT 0x00873398; the scan at
 *     0x00864E21-0x00864EEE, :15250-:15259);
 *   - the WMI query of IsXInputDevice (0x00864680), step for step:
 *     CoInitialize(NULL) (0x008646BC); CoCreateInstance(CLSID_WbemLocator
 *     0x008FFF88, NULL, CLSCTX_INPROC_SERVER, IID_IWbemLocator 0x008FFF98)
 *     (0x008646DB, :14827); SysAllocString of the three strings at
 *     0x0089F068, 0x0089F088 and 0x0089F0A8 (:14844-:14846); ConnectServer
 *     with every other argument null or zero (0x0086474A, :14847);
 *     CoSetProxyBlanket(services, 10, 0, NULL, 3, 3, NULL, 0) (0x0086476B,
 *     :14850); CreateInstanceEnum(Win32_PNPEntity, 0, NULL) (0x00864787,
 *     :14854); Next(10000, 20) until a batch returns nothing (0x008647B4 and
 *     0x008648DD, :14860-:15235); Get(DeviceID, 0) on every object
 *     (0x008647E7). Unlike the game it reads every entity, where the game
 *     stops at the first IG_ match, because the lifted enumerator must hand
 *     back all of them in order. CoUninitialize balances a CoInitialize that
 *     succeeded, as 0x00864966-0x0086496C does (:15240).
 *     The CoInitialize answer is recorded for information and is not the
 *     game's. The game called at 0x008646BC in the single-threaded apartment
 *     Wine's IMM had made (:11399-:11404), and succeeded: its CoUninitialize at
 *     :15240 is reachable only past the setge at 0x008646D6. By Init the
 *     game's XAudio2 set-up has called CoInitializeEx(NULL, 0) on this thread
 *     (0x007841D0-0x007841D4, :15506-:15511) and moved it into the
 *     multithreaded apartment, which nothing leaves before the probe DLLs load
 *     (:15758), so this call answers RPC_E_CHANGED_MODE, as the next
 *     apartment-threaded request on that thread already does (:15531-:15532).
 *     The runtime's CoInitialize answers from its own depth counter, and no
 *     reader refuses or answers from this record (runtime/sa2_devprofile.h);
 *   - Direct3DCreate9(32) (0x00865B70) and each adapter's GetAdapterMonitor,
 *     then DirectDrawEnumerateExA(DDENUM_ATTACHEDSECONDARYDEVICES), as
 *     0x00865D3B-0x00865D96 (:13826-:13918), recording each device's GUID,
 *     strings and which adapter's monitor it is on: the game's callback
 *     (0x00865C20) keeps the GUID of the device on adapter 0's monitor
 *     (GetAdapterMonitor at 0x00865D39). Then DirectDrawCreate with that GUID,
 *     QueryInterface(IID_IDirectDraw7 at 0x0087354C) and
 *     GetAvailableVidMem(DDSCAPS_VIDEOMEMORY | DDSCAPS_LOCALVIDMEM), as
 *     0x00865DA4-0x00865E03 (:13919-:13948), whose total the game keeps as
 *     megabytes (0x00865E24-0x00865E2F);
 *   - the locale, as the game's C runtime asks for it when an archive loader
 *     calls setlocale(LC_ALL, "") (0x0070B6AE, 0x0085BFCA): GetUserDefaultLCID
 *     (0x007B7F9E), GetSystemDefaultLCID, GetACP and GetOEMCP (0x007AD2A5,
 *     0x007AD282), IsValidLocale(user, LCID_INSTALLED) (0x007B8002), and
 *     GetLocaleInfoW(user, lctype) for every LCTYPE of the runtime's
 *     SA2_DP_LOCALE_LCTYPES (runtime/sa2_devprofile.h names the call site of
 *     each), its answer converted to UTF-8 with WC_ERR_INVALID_CHARS. The
 *     answers are the host's -- Wine's locale data under the session's LANG and
 *     the prefix's user settings -- and GetLocaleInfoA is the same answer
 *     through the ANSI code page, which the runtime applies itself. The C
 *     runtime qualified the user's locale before Init (every native boot holds
 *     it at tick 1), and asking again changes none of its state;
 *   - the environment block, as GetEnvironmentStringsW hands it over (the C
 *     runtime's ___crtGetEnvironmentStringsA asked at start-up, before any
 *     Init), each string as UTF-8 (WC_ERR_INVALID_CHARS), and how many of its
 *     first strings the C runtime's _environ holds (0x01751B44, which
 *     _setenvp filled from the start-up block): _environ must be exactly the
 *     CP_ACP form of the block's first strings, in order, or the block at
 *     start-up is not known and the probe writes an `error` record. On the
 *     recording host the last two strings, SteamAppId and SteamGameId, are
 *     SteamAPI_Init's, added after start-up
 *     (private/generated/oracle/captures/n0-trace-v1a/sa2-n0-trace.txt, the
 *     `envw` and `crt-environ` lines). The block is freed as the game frees
 *     its own copy;
 *   - the standard handles, GetStdHandle(STD_INPUT_HANDLE, STD_OUTPUT_HANDLE,
 *     STD_ERROR_HANDLE), as __ioinit asks at start-up, and GetFileType of each
 *     that is neither NULL nor INVALID_HANDLE_VALUE. The image imports no
 *     SetStdHandle call the game makes before Init (the lifted reach run's
 *     table counts none), so the handles at Init are the ones __ioinit typed;
 *   - nothing, for XAudio2: the game already asked. Its mastering voice
 *     (CreateMasteringVoice at 0x00784208 into the global at 0x01AF0EDC,
 *     :15547) was asked for its XAUDIO2_VOICE_DETAILS at 0x0078421D (:15553),
 *     which it keeps at 0x01AF0EE0 and whose channel count it compares with 2
 *     at 0x007843A6 to choose an output matrix (:15558 took the stereo one).
 *     The probe reads those words, and whether the voice exists; opening a
 *     second audio engine to ask again would open the speakers again.
 *
 * What keeps it from moving what a replay compares:
 *   - It runs after the game's own device set-up. In the traced boot every
 *     call above was answered before the probe DLLs load (:15758), so the
 *     game already holds its answers; the probe asks again, of objects it
 *     makes itself.
 *   - Every object it uses is its own -- a second IDirectInput8A and its
 *     devices, a locator and what hangs off it, an IDirect3D9, an
 *     IDirectDraw -- and each is released before Init returns. It writes no
 *     game memory and calls no game code; it reads only the constants named
 *     above and the mastering voice's four words.
 *   - It allocates from the process heap, through the system DLLs and the
 *     UCRT's stdio, and from COM. The game's own objects come from its CRT's
 *     heap (HeapCreate at 0x007B517A, the handle kept at 0x01751FF4, which
 *     _malloc's HeapAlloc at 0x007A851C is given), and the probe never
 *     touches that heap. Where the game uses the process heap itself
 *     (GetProcessHeap at 0x007BF141, 0x007BF1C4, 0x007C7D99, 0x007C7E9B,
 *     0x007C9BD7, 0x007D0F32 and 0x007D152D) a probe allocation can move an
 *     address. The replay compares the pointer fields Documentation/replays.json
 *     names by identity, not by address (tools/s4/tick_replay.py), and
 *     harness/replay/tick_digest.h hashes only words chosen to hold no address.
 *   - It takes no input. Nothing is acquired, no cooperative level or data
 *     format is set, and no window is touched (AGENTS.md rule 11).
 *   - Outside the process it writes one thing: Wine's DirectInput saves its
 *     instance-GUID cache to the prefix's registry on every EnumDevices
 *     (:14780-:14781), and the game's own EnumDevices already does so on every
 *     boot, reusing the cached GUID (:14777), so the values written are the
 *     ones already there.
 *
 * What reasoning cannot settle, and why a control run decides it: Init holds
 * the game's main thread for as long as the questions take -- Direct3DCreate9,
 * DirectDrawCreate (which builds a wined3d device), the WMI enumeration and
 * DirectInput's HID scan -- and meanwhile the game's XAudio2 streaming thread
 * and XAudio2's own processing thread run on in wall time (threads 01d0 and
 * 01cc, still submitting and processing at :15754-:15755 as the probe DLLs
 * load). So by tick 1 those threads have run longer than they would have
 * without the probe, and whether any word a replay compares follows their
 * progress is a question about the game's data that no argument above
 * answers. The control does: a pair of captures that differ only in
 * --device-profile, each with harness/replay/native_vanilla_probe.c built
 * first, as the recorder's runs and the vanilla boot captures are, whose tick
 * captures must be byte-identical. That control has to be run, and pass, before a capture
 * taken with this probe is registered as a replay.
 *
 * Nothing here aborts. A step that cannot be measured -- a DLL that does not
 * load, an export that is missing, a string that does not convert -- is an
 * `error` record where the answer would have been, and the file ends in `end`
 * only if Init ran to the end, so a reader refuses a torn file.
 *
 * Built by tools/oracle/run_oracle_probe.py, which loads it after every other
 * probe and keeps the file and its sha256 in the capture's receipt:
 *     --device-profile
 * which is
 *     --build device_profile=harness/replay/native_device_profile.c
 */
#define DIRECTINPUT_VERSION 0x0800
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <windows.h>
#include <objbase.h>
#include <oleauto.h>
#include <wbemcli.h>
#include <dinput.h>
#include <xinput.h>
#include <ddraw.h>
#include <d3d9.h>

#define EXPORT __declspec(dllexport)

typedef struct {
    int32_t Version;
    void *Init, *Patches;
    int32_t PatchCount;
    void *Jumps;
    int32_t JumpCount;
    void *Calls;
    int32_t CallCount;
    void *Pointers;
    int32_t PointerCount;
} ModInfo;

EXPORT const ModInfo SA2ModInfo = {1, 0, 0, 0, 0, 0, 0, 0, 0, 0};

/* The game's own constants (.rdata), passed by address. */
#define GAME_IID_IDIRECTINPUT8A ((const IID *)(uintptr_t)0x0087355Cu)
#define GAME_GUID_SYSKEYBOARD   ((const GUID *)(uintptr_t)0x0087360Cu)
#define GAME_GUID_SYSMOUSE      ((const GUID *)(uintptr_t)0x008735FCu)
#define GAME_CLSID_WBEMLOCATOR  ((const CLSID *)(uintptr_t)0x008FFF88u)
#define GAME_IID_IWBEMLOCATOR   ((const IID *)(uintptr_t)0x008FFF98u)
#define GAME_IID_IDIRECTDRAW7   ((const IID *)(uintptr_t)0x0087354Cu)
#define GAME_WMI_NAMESPACE      ((const OLECHAR *)(uintptr_t)0x0089F068u)  /* \\.\root\cimv2 */
#define GAME_WMI_CLASS          ((const OLECHAR *)(uintptr_t)0x0089F088u)  /* Win32_PNPEntity */
#define GAME_WMI_DEVICEID       ((const OLECHAR *)(uintptr_t)0x0089F0A8u)  /* DeviceID */

#define WMI_TIMEOUT 10000u      /* 0x008647AB push 2710h */
#define WMI_BATCH   20u         /* 0x008647A9 push 14h */
#define ADAPTER_MONITORS 16u

typedef HRESULT(WINAPI *dinput8create_fn)(HINSTANCE, DWORD, REFIID, LPVOID *, LPUNKNOWN);
typedef DWORD(WINAPI *xgetcaps_fn)(DWORD, DWORD, XINPUT_CAPABILITIES *);
typedef HRESULT(WINAPI *coinit_fn)(LPVOID);
typedef void(WINAPI *couninit_fn)(void);
typedef HRESULT(WINAPI *cocreate_fn)(REFCLSID, LPUNKNOWN, DWORD, REFIID, LPVOID *);
typedef HRESULT(WINAPI *blanket_fn)(IUnknown *, DWORD, DWORD, OLECHAR *, DWORD, DWORD,
                                    RPC_AUTH_IDENTITY_HANDLE, DWORD);
typedef BSTR(WINAPI *sysalloc_fn)(const OLECHAR *);
typedef void(WINAPI *sysfree_fn)(BSTR);
typedef UINT(WINAPI *syslen_fn)(BSTR);
typedef void(WINAPI *varinit_fn)(VARIANTARG *);
typedef HRESULT(WINAPI *varclear_fn)(VARIANTARG *);
typedef IDirect3D9 *(WINAPI *d3dcreate_fn)(UINT);
typedef HRESULT(WINAPI *ddenum_fn)(LPDDENUMCALLBACKEXA, LPVOID, DWORD);
typedef HRESULT(WINAPI *ddcreate_fn)(GUID *, LPDIRECTDRAW *, IUnknown *);

static FILE *out;

/* ------------------------------------------------------------- the writer */

static void field_hex(const char *key, uint32_t v) { fprintf(out, " %s=0x%08x", key, (unsigned)v); }
static void field_dec(const char *key, uint32_t v) { fprintf(out, " %s=%u", key, (unsigned)v); }

static void field_guid(const char *key, const GUID *g) {
    if (!g) {
        fprintf(out, " %s=null", key);
        return;
    }
    fprintf(out, " %s={%08lX-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}", key,
            (unsigned long)g->Data1, (unsigned)g->Data2, (unsigned)g->Data3, g->Data4[0],
            g->Data4[1], g->Data4[2], g->Data4[3], g->Data4[4], g->Data4[5], g->Data4[6],
            g->Data4[7]);
}

/* The grammar's one spelling of a string: printable bytes as themselves, `"`
 * and `\` escaped, every other byte as \xhh. The caller never passes a NUL. */
static void field_str(const char *key, const char *s, size_t n) {
    fprintf(out, " %s=\"", key);
    for (size_t i = 0; i < n; i++) {
        const unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') fprintf(out, "\\%c", c);
        else if (c >= 0x20 && c <= 0x7e) fputc(c, out);
        else fprintf(out, "\\x%02x", c);
    }
    fputc('"', out);
}

static void done(void) {
    fputc('\n', out);
    fflush(out);
}

static void error_record(const char *step, uint32_t code) {
    fputs("error", out);
    field_str("step", step, strlen(step));
    field_hex("code", code);
    done();
}

static void one(const char *kind, const char *key, uint32_t v) {
    fputs(kind, out);
    field_hex(key, v);
    done();
}

/* An export of a module `load` returned; nothing, and no second record, when
 * the module itself did not load. */
static FARPROC proc(HMODULE m, const char *name, const char *step) {
    if (!m) return NULL;
    FARPROC f = GetProcAddress(m, name);
    if (!f) error_record(step, (uint32_t)GetLastError());
    return f;
}

static HMODULE load(const char *dll, const char *step) {
    HMODULE m = LoadLibraryA(dll);
    if (!m) error_record(step, (uint32_t)GetLastError());
    return m;
}

/* ---------------------------------------------------------------- system */

/* The processors, as the game's GetSystemInfo (0x007FB9C7) and
 * GetProcessAffinityMask (0x00865277) see them: this process's, from KERNEL32
 * directly, as the game's own import-table slots resolve. */
static void processors(void) {
    SYSTEM_INFO si;
    memset(&si, 0, sizeof si);
    GetSystemInfo(&si);
    DWORD_PTR process = 0, system = 0;
    if (!GetProcessAffinityMask(GetCurrentProcess(), &process, &system)) {
        error_record("GetProcessAffinityMask(GetCurrentProcess())", (uint32_t)GetLastError());
        return;
    }
    fputs("system", out);
    field_dec("processors", (uint32_t)si.dwNumberOfProcessors);
    field_hex("process_mask", (uint32_t)process);
    field_hex("system_mask", (uint32_t)system);
    done();
}

/* ------------------------------------------------------------ DirectInput */

typedef struct { IDirectInput8A *di; uint32_t n; int failed; } di_ctx;

/* The game's callback returns 1 on every path (0x00864CDC, 0x00864CF1), so the
 * enumeration always runs to its end; this one does too, unless it could not
 * record a device. */
static BOOL CALLBACK gamectrl(LPCDIDEVICEINSTANCEA d, LPVOID ref) {
    di_ctx *c = (di_ctx *)ref;
    if (d->dwSize < sizeof(DIDEVICEINSTANCEA)) {
        error_record("a DIDEVICEINSTANCEA shorter than DirectInput 8's", (uint32_t)d->dwSize);
        c->failed = 1;
        return DIENUM_STOP;
    }
    const size_t ni = strnlen(d->tszInstanceName, MAX_PATH);
    const size_t np = strnlen(d->tszProductName, MAX_PATH);
    if (ni == MAX_PATH || np == MAX_PATH) {
        error_record("a device name with no NUL in MAX_PATH bytes", (uint32_t)c->n);
        c->failed = 1;
        return DIENUM_STOP;
    }
    IDirectInputDevice8A *dev = NULL;
    const HRESULT hr = c->di->lpVtbl->CreateDevice(c->di, &d->guidInstance, &dev, NULL);
    if (dev) dev->lpVtbl->Release(dev);
    fputs("dinput8_gamectrl", out);
    field_dec("index", c->n);
    field_guid("instance", &d->guidInstance);
    field_guid("product", &d->guidProduct);
    field_guid("ffdriver", &d->guidFFDriver);
    field_hex("devtype", (uint32_t)d->dwDevType);
    field_hex("usage_page", (uint32_t)d->wUsagePage);
    field_hex("usage", (uint32_t)d->wUsage);
    field_str("instance_name", d->tszInstanceName, ni);
    field_str("product_name", d->tszProductName, np);
    field_hex("create", (uint32_t)hr);
    done();
    c->n++;
    return DIENUM_CONTINUE;
}

static void dinput(void) {
    HMODULE m = load("dinput8.dll", "LoadLibraryA(dinput8.dll)");
    dinput8create_fn create = (dinput8create_fn)(void *)proc(
        m, "DirectInput8Create", "GetProcAddress(DirectInput8Create)");
    if (!create) goto out;
    IDirectInput8A *di = NULL;
    HRESULT hr = create(GetModuleHandleA(NULL), 0x0800, GAME_IID_IDIRECTINPUT8A, (LPVOID *)&di,
                        NULL);
    one("dinput8_create", "hr", (uint32_t)hr);
    if (FAILED(hr)) goto out;
    if (!di) {
        error_record("DirectInput8Create succeeded with no object", (uint32_t)hr);
        goto out;
    }
    IDirectInputDevice8A *dev = NULL;
    hr = di->lpVtbl->CreateDevice(di, GAME_GUID_SYSKEYBOARD, &dev, NULL);
    if (dev) dev->lpVtbl->Release(dev);
    one("dinput8_syskeyboard", "hr", (uint32_t)hr);
    dev = NULL;
    hr = di->lpVtbl->CreateDevice(di, GAME_GUID_SYSMOUSE, &dev, NULL);
    if (dev) dev->lpVtbl->Release(dev);
    one("dinput8_sysmouse", "hr", (uint32_t)hr);
    di_ctx c = {di, 0, 0};
    hr = di->lpVtbl->EnumDevices(di, DI8DEVCLASS_GAMECTRL, gamectrl, &c, DIEDFL_ATTACHEDONLY);
    if (!c.failed) {
        fputs("dinput8_enum", out);
        field_hex("hr", (uint32_t)hr);
        field_dec("count", c.n);
        done();
    }
    di->lpVtbl->Release(di);
out:
    if (m) FreeLibrary(m);
}

/* ----------------------------------------------------------------- XInput */

static void xinput(void) {
    HMODULE m = load("xinput1_3.dll", "LoadLibraryA(xinput1_3.dll)");
    xgetcaps_fn caps = (xgetcaps_fn)(void *)proc(m, MAKEINTRESOURCEA(4),
                                                 "GetProcAddress(xinput1_3.dll, ordinal 4)");
    if (caps)
        for (DWORD slot = 0; slot < 4; slot++) {
            XINPUT_CAPABILITIES c;
            memset(&c, 0, sizeof c);
            const DWORD code = caps(slot, XINPUT_FLAG_GAMEPAD, &c);
            fputs("xinput_caps", out);
            field_dec("slot", (uint32_t)slot);
            field_hex("code", (uint32_t)code);
            done();
        }
    if (m) FreeLibrary(m);
}

/* -------------------------------------------------------------------- WMI */

typedef struct {
    sysalloc_fn alloc;
    sysfree_fn  free;
    syslen_fn   len;
    varinit_fn  vinit;
    varclear_fn vclear;
} oleaut;

/* One entity's DeviceID, as Get hands it back, into the record. */
static int entity(IWbemClassObject *o, BSTR deviceid, uint32_t index, const oleaut *a) {
    VARIANT v;
    a->vinit(&v);
    const HRESULT hr = o->lpVtbl->Get(o, deviceid, 0, &v, NULL, NULL);
    static char utf8[4096];
    int n = -1;
    if (SUCCEEDED(hr) && v.vt == VT_BSTR && v.bstrVal) {
        const UINT wn = a->len(v.bstrVal);
        for (UINT i = 0; i < wn; i++)
            if (!v.bstrVal[i]) {
                error_record("a DeviceID with a NUL inside it", index);
                a->vclear(&v);
                return 0;
            }
        n = wn ? WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, v.bstrVal, (int)wn, utf8,
                                     (int)sizeof utf8, NULL, NULL)
               : 0;
        if (wn && n <= 0) {
            error_record("a DeviceID that does not convert to UTF-8", (uint32_t)GetLastError());
            a->vclear(&v);
            return 0;
        }
    }
    fputs("wmi_pnp", out);
    field_dec("index", index);
    field_hex("get", (uint32_t)hr);
    field_hex("vt", SUCCEEDED(hr) ? (uint32_t)v.vt : 0u);
    if (n >= 0) field_str("deviceid", utf8, (size_t)n);
    else fputs(" deviceid=null", out);
    done();
    a->vclear(&v);
    return 1;
}

static void wmi(void) {
    HMODULE ole = load("ole32.dll", "LoadLibraryA(ole32.dll)");
    HMODULE aut = load("oleaut32.dll", "LoadLibraryA(oleaut32.dll)");
    coinit_fn coinit = (coinit_fn)(void *)proc(ole, "CoInitialize", "GetProcAddress(CoInitialize)");
    couninit_fn couninit =
        (couninit_fn)(void *)proc(ole, "CoUninitialize", "GetProcAddress(CoUninitialize)");
    cocreate_fn cocreate =
        (cocreate_fn)(void *)proc(ole, "CoCreateInstance", "GetProcAddress(CoCreateInstance)");
    blanket_fn blanket =
        (blanket_fn)(void *)proc(ole, "CoSetProxyBlanket", "GetProcAddress(CoSetProxyBlanket)");
    oleaut a = {
        (sysalloc_fn)(void *)proc(aut, "SysAllocString", "GetProcAddress(SysAllocString)"),
        (sysfree_fn)(void *)proc(aut, "SysFreeString", "GetProcAddress(SysFreeString)"),
        (syslen_fn)(void *)proc(aut, "SysStringLen", "GetProcAddress(SysStringLen)"),
        (varinit_fn)(void *)proc(aut, "VariantInit", "GetProcAddress(VariantInit)"),
        (varclear_fn)(void *)proc(aut, "VariantClear", "GetProcAddress(VariantClear)"),
    };
    if (!coinit || !couninit || !cocreate || !blanket || !a.alloc || !a.free || !a.len ||
        !a.vinit || !a.vclear)
        goto unload;

    const HRESULT init = coinit(NULL);
    one("wmi_coinit", "hr", (uint32_t)init);
    IWbemLocator *loc = NULL;
    IWbemServices *svc = NULL;
    IEnumWbemClassObject *en = NULL;
    BSTR ns = NULL, cls = NULL, id = NULL;
    HRESULT hr = cocreate(GAME_CLSID_WBEMLOCATOR, NULL, CLSCTX_INPROC_SERVER, GAME_IID_IWBEMLOCATOR,
                          (LPVOID *)&loc);
    one("wmi_locator", "hr", (uint32_t)hr);
    if (FAILED(hr)) goto release;
    if (!loc) {
        error_record("CoCreateInstance(CLSID_WbemLocator) succeeded with no object", (uint32_t)hr);
        goto release;
    }
    ns = a.alloc(GAME_WMI_NAMESPACE);
    cls = a.alloc(GAME_WMI_CLASS);
    id = a.alloc(GAME_WMI_DEVICEID);
    if (!ns || !cls || !id) {
        error_record("SysAllocString of the game's WMI strings", 0);
        goto release;
    }
    hr = loc->lpVtbl->ConnectServer(loc, ns, NULL, NULL, NULL, 0, NULL, NULL, &svc);
    one("wmi_connect", "hr", (uint32_t)hr);
    if (FAILED(hr)) goto release;
    if (!svc) {
        error_record("ConnectServer succeeded with no object", (uint32_t)hr);
        goto release;
    }
    hr = blanket((IUnknown *)svc, RPC_C_AUTHN_WINNT, RPC_C_AUTHZ_NONE, NULL, RPC_C_AUTHN_LEVEL_CALL,
                 RPC_C_IMP_LEVEL_IMPERSONATE, NULL, EOAC_NONE);
    one("wmi_blanket", "hr", (uint32_t)hr);
    hr = svc->lpVtbl->CreateInstanceEnum(svc, cls, 0, NULL, &en);
    one("wmi_enum", "hr", (uint32_t)hr);
    if (FAILED(hr)) goto release;
    if (!en) {
        error_record("CreateInstanceEnum succeeded with no object", (uint32_t)hr);
        goto release;
    }
    uint32_t total = 0;
    for (;;) {
        IWbemClassObject *objs[WMI_BATCH];
        memset(objs, 0, sizeof objs);
        ULONG got = 0;
        hr = en->lpVtbl->Next(en, WMI_TIMEOUT, WMI_BATCH, objs, &got);
        fputs("wmi_next", out);
        field_hex("hr", (uint32_t)hr);
        field_dec("returned", (uint32_t)got);
        done();
        if (FAILED(hr)) goto release;          /* the game's 0x008647B8 jl */
        if (got > WMI_BATCH) {
            error_record("Next returned more objects than it was asked for", (uint32_t)got);
            goto release;
        }
        int ok = 1;
        for (ULONG i = 0; i < got; i++) {
            if (ok && objs[i]) ok = entity(objs[i], id, total++, &a);
            else if (ok) {
                error_record("Next returned a null object", (uint32_t)i);
                ok = 0;
            }
            if (objs[i]) objs[i]->lpVtbl->Release(objs[i]);
        }
        if (!ok) goto release;
        if (!got) break;                       /* the game's 0x008647C4 je */
    }
    fputs("wmi_entities", out);
    field_dec("count", total);
    done();
release:
    if (en) en->lpVtbl->Release(en);
    if (svc) svc->lpVtbl->Release(svc);
    if (loc) loc->lpVtbl->Release(loc);
    if (ns) a.free(ns);
    if (cls) a.free(cls);
    if (id) a.free(id);
    if (SUCCEEDED(init)) couninit();
unload:
    if (aut) FreeLibrary(aut);
    if (ole) FreeLibrary(ole);
}

/* ------------------------------------------------------------- DirectDraw */

typedef struct {
    HMONITOR mon[ADAPTER_MONITORS];
    uint32_t adapters, n;
    GUID     chosen;                /* zeroed, as the game's context (0x00865D70-0x00865D80) */
    int      failed;
} dd_ctx;

static BOOL WINAPI ddraw_device(GUID *g, LPSTR desc, LPSTR name, LPVOID ref, HMONITOR hm) {
    dd_ctx *c = (dd_ctx *)ref;
    if (!desc || !name) {
        error_record("DirectDrawEnumerateExA passed a null string", c->n);
        c->failed = 1;
        return FALSE;
    }
    fputs("ddraw_device", out);
    field_dec("index", c->n);
    field_guid("guid", g);
    if (!hm) {
        fputs(" monitor=null", out);
    } else {
        uint32_t i = 0;
        while (i < c->adapters && i < ADAPTER_MONITORS && c->mon[i] != hm) i++;
        if (i < c->adapters && i < ADAPTER_MONITORS)
            fprintf(out, " monitor=adapter%u", (unsigned)i);
        else
            fputs(" monitor=other", out);
    }
    field_str("description", desc, strlen(desc));
    field_str("name", name, strlen(name));
    done();
    /* The game's callback: the monitor it asked Direct3D about, and on a
     * match the device's GUID copied into its context (0x00865C24-0x00865C48). */
    if (hm && hm == c->mon[0] && g) c->chosen = *g;
    c->n++;
    return TRUE;                    /* the game's returns 1 (0x00865C4B) */
}

static void ddraw(void) {
    HMODULE d9 = load("d3d9.dll", "LoadLibraryA(d3d9.dll)");
    HMODULE dd = NULL;
    IDirect3D9 *d3d = NULL;
    d3dcreate_fn create9 =
        (d3dcreate_fn)(void *)proc(d9, "Direct3DCreate9", "GetProcAddress(Direct3DCreate9)");
    if (!create9) goto out;
    d3d = create9(32u);             /* D3D_SDK_VERSION, as 0x00865B70 pushes it */
    if (!d3d) {
        error_record("Direct3DCreate9(32) answered no object", 0);
        goto out;
    }
    dd_ctx c;
    memset(&c, 0, sizeof c);
    c.adapters = (uint32_t)d3d->lpVtbl->GetAdapterCount(d3d);
    fputs("d3d9_adapters", out);
    field_dec("count", c.adapters);
    done();
    for (uint32_t i = 0; i < c.adapters && i < ADAPTER_MONITORS; i++)
        c.mon[i] = d3d->lpVtbl->GetAdapterMonitor(d3d, i);
    if (!c.adapters || !c.mon[0]) {
        error_record("IDirect3D9 has no monitor for adapter 0", c.adapters);
        goto out;
    }
    dd = load("ddraw.dll", "LoadLibraryA(ddraw.dll)");
    ddenum_fn enumx = (ddenum_fn)(void *)proc(dd, "DirectDrawEnumerateExA",
                                              "GetProcAddress(DirectDrawEnumerateExA)");
    ddcreate_fn create =
        (ddcreate_fn)(void *)proc(dd, "DirectDrawCreate", "GetProcAddress(DirectDrawCreate)");
    if (!enumx || !create) goto out;
    HRESULT hr = enumx(ddraw_device, &c, DDENUM_ATTACHEDSECONDARYDEVICES);
    if (c.failed) goto out;
    fputs("ddraw_enum", out);
    field_hex("hr", (uint32_t)hr);
    field_dec("count", c.n);
    done();
    IDirectDraw *d = NULL;
    hr = create(&c.chosen, &d, NULL);
    fputs("ddraw_create", out);
    field_guid("guid", &c.chosen);
    field_hex("hr", (uint32_t)hr);
    done();
    if (FAILED(hr)) goto out;
    if (!d) {
        error_record("DirectDrawCreate succeeded with no object", (uint32_t)hr);
        goto out;
    }
    IDirectDraw7 *d7 = NULL;
    hr = d->lpVtbl->QueryInterface(d, GAME_IID_IDIRECTDRAW7, (LPVOID *)&d7);
    one("ddraw_query", "hr", (uint32_t)hr);
    if (SUCCEEDED(hr) && !d7) error_record("QueryInterface(IID_IDirectDraw7) gave no object", 0);
    if (SUCCEEDED(hr) && d7) {
        DDSCAPS2 caps;
        memset(&caps, 0, sizeof caps);
        caps.dwCaps = DDSCAPS_VIDEOMEMORY | DDSCAPS_LOCALVIDMEM;   /* 0x10004000, 0x00865DF4 */
        DWORD total = 0;
        hr = d7->lpVtbl->GetAvailableVidMem(d7, &caps, &total, NULL);
        if (FAILED(hr)) total = 0;                                 /* 0x00865E09 */
        fputs("ddraw_vidmem", out);
        field_hex("hr", (uint32_t)hr);
        field_hex("total", (uint32_t)total);
        done();
        d7->lpVtbl->Release(d7);
    }
    /* The game never releases its IDirectDraw; this one is the probe's own. */
    d->lpVtbl->Release(d);
out:
    if (d3d) d3d->lpVtbl->Release(d3d);
    if (dd) FreeLibrary(dd);
    if (d9) FreeLibrary(d9);
}

/* ---------------------------------------------------------------- XAudio2 */

#define GAME_XAUDIO2_MASTER  0x01AF0EDCu   /* IXAudio2MasteringVoice *, or 0 */
#define GAME_XAUDIO2_DETAILS 0x01AF0EE0u   /* XAUDIO2_VOICE_DETAILS: flags, channels, rate */

static void xaudio2(void) {
    const volatile uint32_t *voice = (const volatile uint32_t *)(uintptr_t)GAME_XAUDIO2_MASTER;
    const volatile uint32_t *d = (const volatile uint32_t *)(uintptr_t)GAME_XAUDIO2_DETAILS;
    fputs("xaudio2_master", out);
    field_dec("voice", *voice ? 1u : 0u);
    field_hex("flags", d[0]);
    field_dec("channels", d[1]);
    field_dec("rate", d[2]);
    done();
}

/* ----------------------------------------------------------------- locale */

/* runtime/sa2_devprofile.h SA2_DP_LOCALE_LCTYPES, which the reader holds the
 * file to, in its order. */
static const uint32_t LOCALE_LCTYPES[] = {
    0x0001u, 0x0003u, 0x0004u, 0x0005u, 0x0007u, 0x0008u, 0x0009u, 0x000Au, 0x000Bu,
    0x000Eu, 0x000Fu, 0x0010u, 0x0014u, 0x0015u, 0x0016u, 0x0017u, 0x0018u, 0x0019u,
    0x001Au, 0x001Fu, 0x0020u, 0x0028u, 0x0029u, 0x002Au, 0x002Bu, 0x002Cu, 0x002Du,
    0x002Eu, 0x002Fu, 0x0030u, 0x0031u, 0x0032u, 0x0033u, 0x0034u, 0x0035u, 0x0036u,
    0x0037u, 0x0038u, 0x0039u, 0x003Au, 0x003Bu, 0x003Cu, 0x003Du, 0x003Eu, 0x003Fu,
    0x0040u, 0x0041u, 0x0042u, 0x0043u, 0x0044u, 0x0045u, 0x0046u, 0x0047u, 0x0048u,
    0x0049u, 0x004Au, 0x004Bu, 0x004Cu, 0x004Du, 0x004Eu, 0x004Fu, 0x0050u, 0x0051u,
    0x0052u, 0x0053u, 0x0054u, 0x0055u, 0x0056u, 0x0057u, 0x1001u, 0x1002u, 0x1003u,
    0x1004u, 0x1009u,
};
#define LOCALE_VALUE_MAX 127     /* runtime/sa2_devprofile.h SA2_DP_LOCALE_VALUE_MAX */

static void locale(void) {
    const LCID user = GetUserDefaultLCID();
    fputs("locale_user", out);
    field_hex("lcid", (uint32_t)user);
    field_hex("system", (uint32_t)GetSystemDefaultLCID());
    field_dec("acp", (uint32_t)GetACP());
    field_dec("oemcp", (uint32_t)GetOEMCP());
    field_dec("valid", IsValidLocale(user, LCID_INSTALLED) ? 1u : 0u);
    done();
    for (unsigned i = 0; i < sizeof LOCALE_LCTYPES / sizeof LOCALE_LCTYPES[0]; i++) {
        const LCTYPE t = LOCALE_LCTYPES[i];
        WCHAR w[LOCALE_VALUE_MAX + 1];
        SetLastError(0);
        const int n = GetLocaleInfoW(user, t, w, LOCALE_VALUE_MAX + 1);
        if (n <= 0) {
            const DWORD e = GetLastError();
            if (e == ERROR_INSUFFICIENT_BUFFER || !e) {
                error_record(e ? "a locale answer longer than the runtime holds"
                               : "GetLocaleInfoW failed and set no error", (uint32_t)t);
                return;
            }
            fputs("locale_info", out);
            field_hex("lctype", (uint32_t)t);
            field_hex("error", (uint32_t)e);
            fputs(" value=null", out);
            done();
            continue;
        }
        char u[4 * LOCALE_VALUE_MAX + 4];
        const int m = n > 1 ? WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, w, n - 1, u,
                                                  (int)sizeof u, NULL, NULL)
                            : 0;
        if (n > 1 && m <= 0) {
            error_record("a locale answer that does not convert to UTF-8", (uint32_t)t);
            return;
        }
        if (m > LOCALE_VALUE_MAX) {
            error_record("a locale answer longer than the runtime holds", (uint32_t)t);
            return;
        }
        fputs("locale_info", out);
        field_hex("lctype", (uint32_t)t);
        field_hex("error", 0);
        field_str("value", u, (size_t)m);
        done();
    }
}

/* ------------------------------------------------------------ environment */

#define GAME_CRT_ENVIRON  0x01751B44u   /* _environ: char **, NULL-terminated */
#define ENV_MAX_WALK      4096u         /* past any block a reader accepts */

static int readable(const void *p, size_t n) {
    MEMORY_BASIC_INFORMATION mi;
    if (!p || !VirtualQuery(p, &mi, sizeof mi)) return 0;
    if (mi.State != MEM_COMMIT || (mi.Protect & (PAGE_NOACCESS | PAGE_GUARD))) return 0;
    return (const char *)p + n <= (const char *)mi.BaseAddress + mi.RegionSize;
}

/* Whether the C runtime's copy `a` is the CP_ACP form of the block's string
 * `w`, as ___crtGetEnvironmentStringsA converted it at start-up. */
static int crt_holds(const char *a, const WCHAR *w) {
    const int need = WideCharToMultiByte(CP_ACP, 0, w, -1, NULL, 0, NULL, NULL);
    if (need <= 0) return 0;
    char *buf = (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)need);
    if (!buf) return 0;
    int same = WideCharToMultiByte(CP_ACP, 0, w, -1, buf, need, NULL, NULL) == need;
    for (int i = 0; same && i < need; i++) {
        if (!readable(a + i, 1) || a[i] != buf[i]) same = 0;
    }
    HeapFree(GetProcessHeap(), 0, buf);
    return same;
}

static void environment(void) {
    WCHAR *blk = GetEnvironmentStringsW();
    if (!blk) {
        error_record("GetEnvironmentStringsW answered NULL", (uint32_t)GetLastError());
        return;
    }
    uint32_t strings = 0;
    for (const WCHAR *q = blk; *q; q += lstrlenW(q) + 1) strings++;
    char *const *env = NULL;
    uint32_t crt = 0;
    if (readable((const void *)(uintptr_t)GAME_CRT_ENVIRON, sizeof env))
        env = *(char *const *const *)(uintptr_t)GAME_CRT_ENVIRON;
    int whole = env != NULL;
    while (whole && crt < ENV_MAX_WALK) {
        if (!readable(env + crt, sizeof env[0])) whole = 0;
        else if (!env[crt]) break;
        else crt++;
    }
    if (!whole || crt >= ENV_MAX_WALK || crt > strings) {
        error_record("the C runtime's _environ is not a prefix of the environment block", crt);
        FreeEnvironmentStringsW(blk);
        return;
    }
    const WCHAR *q = blk;
    for (uint32_t i = 0; i < crt; i++, q += lstrlenW(q) + 1) {
        if (!crt_holds(env[i], q)) {
            error_record("the C runtime's _environ is not a prefix of the environment block", i);
            FreeEnvironmentStringsW(blk);
            return;
        }
    }
    fputs("environment", out);
    field_dec("strings", strings);
    field_dec("crt", crt);
    done();
    q = blk;
    for (uint32_t i = 0; i < strings; i++, q += lstrlenW(q) + 1) {
        const int n = lstrlenW(q);
        const int need = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, q, n, NULL, 0, NULL,
                                             NULL);
        char *u = need > 0 ? (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)need) : NULL;
        if (!u || WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, q, n, u, need, NULL,
                                      NULL) != need) {
            if (u) HeapFree(GetProcessHeap(), 0, u);
            error_record("an environment string that does not convert to UTF-8", i);
            FreeEnvironmentStringsW(blk);
            return;
        }
        fputs("env", out);
        field_dec("index", i);
        field_str("value", u, (size_t)need);
        done();
        HeapFree(GetProcessHeap(), 0, u);
    }
    FreeEnvironmentStringsW(blk);
}

/* -------------------------------------------------------- standard handles */

static void std_handles(void) {
    static const DWORD WHICH[3] = {STD_INPUT_HANDLE, STD_OUTPUT_HANDLE, STD_ERROR_HANDLE};
    for (uint32_t i = 0; i < 3u; i++) {
        const HANDLE h = GetStdHandle(WHICH[i]);
        const DWORD t = h && h != INVALID_HANDLE_VALUE ? GetFileType(h) : FILE_TYPE_UNKNOWN;
        fputs("std_handle", out);
        field_dec("which", i);
        field_hex("handle", (uint32_t)(uintptr_t)h);
        field_dec("type", (uint32_t)t);
        done();
    }
}

/* ------------------------------------------------------------------ Init */

EXPORT void __cdecl Init(const char *path, const void *helpers, unsigned int mod_index) {
    (void)path;
    (void)helpers;
    (void)mod_index;
    out = fopen("mods/sa2-device-profile.txt", "wb");       /* binary: LF, as the grammar */
    if (!out) return;
    fputs("sa2-device-profile 1\n", out);
    fflush(out);
    processors();
    dinput();
    xinput();
    wmi();
    ddraw();
    xaudio2();
    locale();
    environment();
    std_handles();
    fputs("end\n", out);
    fclose(out);
    out = NULL;
}
