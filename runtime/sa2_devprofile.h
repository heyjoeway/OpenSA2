/* The device profile: the host's device answers, recorded natively, read back lifted.
 *
 * Why this exists
 * ---------------
 * The game asks the host which devices it has before any probe can see it:
 * DirectInput enumerates game controllers, and for each one the game's
 * IsXInputDevice (0x00864680) runs a WMI query over every Plug and Play entity
 * on the machine; XInput is asked about four pad slots; DirectDraw enumerates
 * the displays and reports video memory; XAudio2's mastering voice takes the
 * speakers' channel count. The native answers are this host's
 * hardware, and on the recording host they include a keyboard-and-mouse
 * receiver that DirectInput classes as a game controller
 * (agents-journals/s4/2026-09-23-native-outcomes.md). The processor count is
 * an answer of the same kind: Sofdec's initialiser (0x007FBC90) builds its
 * three decoder workers only when GetSystemInfo reports more than one
 * processor, and a boot run on one processor against a capture taken on the
 * recording host's diverges in exactly those words (the same journal, "The
 * boot replay's last divergence").
 *
 * The user ruled on 2026-09-23 that "the device set is a recorded property of
 * each capture: the runner records it in the receipt, the lifted replay reads
 * the same profile back, and a run with no capture uses a plain
 * keyboard-and-mouse default. A capture whose profile the runtime cannot
 * reproduce is refused, not approximated." So no host device is written into
 * the runtime. harness/replay/native_device_profile.c records the answers at
 * the Mod Loader's Init into `sa2-device-profile.txt`, the oracle runner keeps
 * it in the capture and its sha256 in the receipt, and this module reads the
 * file named by SA2_DEVICE_PROFILE and answers from it. The DirectInput, WMI
 * and DirectDraw devices, the XInput stub, and GetSystemInfo and
 * GetProcessAffinityMask ask it, and so do the user's locale, the environment
 * block and the standard handles (runtime/sa2_imports.c). None of them keeps a
 * device of its own; runtime/sa2_imports.c copies the processor count and masks
 * at configuration, because SA2_GUEST_CPUS may override them on the default
 * profile.
 *
 * With SA2_DEVICE_PROFILE unset the profile is the built-in default: one
 * processor, in both affinity masks; the system keyboard and mouse, no
 * game-controller-class device, every XInput slot ERROR_DEVICE_NOT_CONNECTED,
 * no WMI Win32_PNPEntity entity, one Direct3D adapter, one DirectDraw device,
 * the default display (NULL GUID, no monitor), whose video memory is the named
 * placeholder below, a stereo mastering voice at the rate the game asks
 * for, no locale, environment or standard-handle section (Wine's 0x0409 data,
 * an empty environment block and no standard handles, runtime/sa2_imports.c).
 * The default is not a measurement of anything; it is what a run with no
 * capture gets, and its one processor is the answer the runtime gave before
 * profiles existed.
 *
 * A file this module cannot read, or one recording an answer the runtime
 * cannot give, traps at configuration naming the file and the line
 * (sa2_trap_raise(SA2_TRAP_FAULT, 0), as the window model's configuration
 * refusals do). tools/s4/device_profile.py implements the same grammar and the
 * same refusals, in the same order and with the same words, and the replay
 * tools run it before they hand a profile to a lifted run;
 * Documentation/scripts/tests/test_device_profile.py builds this file and
 * holds the two to each other.
 *
 * THE GRAMMAR, version 1
 * ----------------------
 * The file holds only the bytes 0x20-0x7e and LF. Every line ends in LF, the
 * last included, and no line is empty. The first line is exactly
 * `sa2-device-profile 1`. Every other line is one record: its kind, then each
 * of its fields as a single space and `key=value`, in exactly the order the
 * kind's row below gives, none missing, none repeated, nothing after the last.
 *
 * Values:
 *   hex   `0x` and eight lower-case hex digits
 *   dec   decimal digits, no sign, no leading zero, at most 4294967295
 *   guid  `{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}` in upper-case hex, the
 *         registry form (StringFromGUID2's spelling); `guid?` also takes `null`
 *   str   `"`, bytes, `"`. A byte 0x20-0x7e other than `"` and `\` stands for
 *         itself; `\"` is `"`, `\\` is `\`, and `\xhh` (two lower-case hex
 *         digits) is any other byte from 0x01 to 0xff. Nothing else, so a
 *         string has one spelling and the file's sha256 names its content.
 *         `str?` also takes `null`.
 *   mon   `null`, `other`, or `adapter` followed by a dec
 *
 * Records, in this order (`*` repeats; a count closes the run it counts):
 *   system processors=dec process_mask=hex system_mask=hex
 *   dinput8_create hr=hex
 *   dinput8_syskeyboard hr=hex
 *   dinput8_sysmouse hr=hex
 *   dinput8_gamectrl* index=dec instance=guid product=guid ffdriver=guid
 *                     devtype=hex usage_page=hex usage=hex instance_name=str
 *                     product_name=str create=hex
 *   dinput8_enum hr=hex count=dec
 *   xinput_caps slot=dec code=hex                    four, slots 0 to 3
 *   wmi_coinit hr=hex
 *   wmi_locator hr=hex
 *   wmi_connect hr=hex
 *   wmi_blanket hr=hex
 *   wmi_enum hr=hex
 *   { wmi_next hr=hex returned=dec, then `returned` of
 *     wmi_pnp index=dec get=hex vt=hex deviceid=str? }*
 *                                        until a wmi_next that returned 0
 *   wmi_entities count=dec
 *   d3d9_adapters count=dec
 *   ddraw_device* index=dec guid=guid? monitor=mon description=str name=str
 *   ddraw_enum hr=hex count=dec
 *   ddraw_create guid=guid hr=hex
 *   ddraw_query hr=hex
 *   ddraw_vidmem hr=hex total=hex
 *   xaudio2_master voice=dec flags=hex channels=dec rate=dec
 *   [ locale_user lcid=hex system=hex acp=dec oemcp=dec valid=dec
 *     locale_info lctype=hex error=hex value=str?
 *                            SA2_DP_LOCALE_TYPES of them, one per LCTYPE of
 *                            SA2_DP_LOCALE_LCTYPES, in that order ]
 *   [ environment strings=dec crt=dec
 *     env index=dec value=str
 *                            `strings` of them, in the block's order ]
 *   [ std_handle which=dec handle=hex type=dec
 *                            three: 0 stdin, 1 stdout, 2 stderr ]
 *   end
 *
 * The locale, environment and standard-handle sections are each optional as
 * a whole, and come in this order. A file without one asks for the runtime's
 * default answers there (the default profile's, below): Wine's 0x0409 locale
 * data (runtime/sa2_locale.h), an empty environment block, and no standard
 * handles (SA2_STD_HANDLES=pipe, the replay configuration's switch before
 * profiles recorded them, gives pipes). Every capture the probe takes has all
 * three, and tools/s4/device_profile.py capture_profile refuses a capture taken
 * with any other probe than the tree's.
 *
 * In place of any record the probe may write `error step=str code=hex`: a
 * step it could not measure (a DLL that did not load, an export missing, a
 * string that did not convert). A reader refuses the file there.
 *
 * What each record is, and the call it repeats, is in the probe's header.
 * Indices run 0, 1, 2 ... in file order, which is native order.
 *
 * What the runtime can reproduce, and so what a reader accepts
 * ------------------------------------------------------------
 *  - GetSystemInfo reported from 1 to 32 processors, and each of
 *    GetProcessAffinityMask's two masks names at least one of them and none
 *    past them: 32 is what a 32-bit process's masks hold, and the runtime
 *    answers GetSystemInfo (0x007FB9C7) and GetProcessAffinityMask
 *    (0x00865277) from these three numbers.
 *  - DirectInput8Create, EnumDevices, the WMI locator, ConnectServer,
 *    CreateInstanceEnum, each entity's Get("DeviceID"), DirectDrawEnumerateExA,
 *    DirectDrawCreate and QueryInterface(IID_IDirectDraw7) answered success
 *    natively: the runtime's objects always do.
 *  - At most SA2_DP_MAX_GAMECTRL controllers, and none has GUID_SysKeyboard,
 *    GUID_SysMouse or an earlier controller's instance GUID: the runtime's
 *    CreateDevice tells devices apart by that GUID (runtime/sa2_dinput.c), as
 *    DirectInput does.
 *  - Every XInput slot answered 1167, ERROR_DEVICE_NOT_CONNECTED: an attached
 *    pad is refused, because a pad's input is part of no recording.
 *  - Each WMI batch is what an enumerator answering min(20, remaining) with
 *    S_OK for a full batch and WBEM_S_FALSE (1) otherwise gives, 20 being the
 *    count the game asks for (0x008647A9); there are at most
 *    SA2_DP_MAX_WMI_PNP entities, one class object each; and every DeviceID is
 *    a non-null BSTR whose UTF-8 is valid, since the runtime hands it back as
 *    UTF-16 and the probe converted it from UTF-16 with WC_ERR_INVALID_CHARS.
 *  - Direct3D reported one adapter, as runtime/sa2_d3d9.c's IDirect3D9 does;
 *    there are at most SA2_DP_MAX_DDRAW DirectDraw devices, and every one's
 *    monitor is null or that adapter's.
 *  - The game had its XAudio2 mastering voice (`voice=1`): the runtime's
 *    engine makes one under every profile this refusal applies to, because
 *    configuration refuses a capture's profile with SA2_XAUDIO2_DEVICE off or
 *    SA2_XAUDIO2_BREAK=refuse (runtime/sa2_xaudio2.h). Only the default
 *    profile runs with the class refused.
 * Answers the runtime passes through whatever they were: CreateDevice for the
 * keyboard, the mouse and each controller, CoSetProxyBlanket,
 * GetAvailableVidMem with its total, and the mastering voice's details.
 *
 * The locale (the section above), which the game's statically linked C
 * runtime asks the host for when the game calls setlocale(LC_ALL, "") on each
 * of its archive loaders (0x0070B6A0, 0x0085BFCA): the user's LCID, the code
 * pages, and the locale data its category initialisers read. Natively every
 * category qualifies the user's locale (agents-journals/s4/2026-09-26-locale.md);
 * a runtime that refuses the data leaves LC_MONETARY, LC_NUMERIC and LC_TIME
 * in "C" (private/generated/s4/constwords/MANIFEST.md, "Locale").
 * The runtime answers from the section when a profile has one:
 *  - GetUserDefaultLCID the user's LCID; GetACP and GetOEMCP its code pages
 *    (IsValidLocale is Wine's rule over locale.nls, runtime/sa2_locale.h, and
 *    the configuration traps unless that calls the user's LCID valid);
 *  - GetLocaleInfoW, and GetLocaleInfoA through the ANSI code page, for the
 *    user's LCID, LOCALE_NEUTRAL, LOCALE_USER_DEFAULT (and LOCALE_SYSTEM_DEFAULT
 *    when the system's LCID is the user's) and each LCTYPE of
 *    SA2_DP_LOCALE_LCTYPES: the recorded string,
 *    or the recorded failure with its error;
 * and a reader accepts only what it can answer that way:
 *  - IsValidLocale(user, LCID_INSTALLED) answered 1 natively (`valid=1`);
 *  - GetACP answered 1252, the one ANSI code page runtime/sa2_codepage.c
 *    implements, and LOCALE_IDEFAULTANSICODEPAGE is that same number, so the
 *    C runtime's _setmbcp never installs a code page the conversions refuse;
 *  - each value is valid UTF-8 (the probe converted the UTF-16 answer with
 *    WC_ERR_INVALID_CHARS) of at most SA2_DP_LOCALE_VALUE_MAX bytes, holds no
 *    character outside the Basic Multilingual Plane, and every character has
 *    a byte of its own in code page 1252 (stricter than the runtime needs
 *    since it has 1252's best fit; kept so a recorded value never reaches the
 *    best fit unmeasured). A value is null exactly when its error is not 0.
 * The locales that are not the user's -- the installed-locale enumeration,
 * 0x0804, which the game's setlocale(LC_ALL, "Chinese-simplified") selects,
 * and code page 936 -- are platform data the runtime holds itself
 * (runtime/sa2_locale.h), not the host's configuration, and no section
 * carries them.
 *
 * The environment (the section above): the block GetEnvironmentStringsW hands
 * the game's C runtime at start-up (___crtGetEnvironmentStringsA), from which
 * _setenvp builds _environ. The call comes before any probe's Init, so the
 * probe reads the block at Init, one record per string in the block's order,
 * UTF-8 as WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS) gives it, and
 * `crt`: how many of its first strings _environ holds, each the CP_ACP form of
 * the block's string at its index -- the part of the block the C runtime saw.
 * The strings after them were added between start-up and Init (on the
 * recording host SteamAPI_Init's SteamAppId and SteamGameId), and the runtime
 * answers the block without them. The probe writes an `error` record when
 * _environ is not such a prefix, since then the block at start-up is not
 * known. A reader accepts:
 *  - 1 to SA2_DP_ENV_MAX_STRINGS strings, and a `crt` of 1 to `strings`;
 *  - each string non-empty, valid UTF-8 of at most SA2_DP_ENV_VALUE_MAX bytes,
 *    and within the Basic Multilingual Plane, as the runtime's block holds
 *    UTF-16 units with no surrogate pair;
 *  - the whole block, each string's units, its NUL and the block's final NUL,
 *    within SA2_DP_ENV_MAX_UNITS.
 *
 * The standard handles (the section above): GetStdHandle(STD_INPUT_HANDLE,
 * STD_OUTPUT_HANDLE, STD_ERROR_HANDLE), which the C runtime's __ioinit asks
 * at start-up, and GetFileType of each handle, whose FILE_TYPE_CHAR or
 * FILE_TYPE_PIPE marks the descriptor a device or a pipe. The handles the
 * oracle's runner gives the game are Unix pipes, which Wine types as
 * character devices (FILE_TYPE_CHAR; private/generated/oracle/captures/
 * n0-trace-v1a, the `host GetStdHandle` lines). The handle's number is the
 * host's and is recorded for information: the runtime answers a handle of its
 * own (runtime/sa2_imports.c, "The standard handles"), compared by what it
 * names (Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md).
 * A reader accepts, for each: no handle (NULL or INVALID_HANDLE_VALUE) with
 * type 0; or a handle of type 0 (FILE_TYPE_UNKNOWN), 2 (FILE_TYPE_CHAR) or 3
 * (FILE_TYPE_PIPE). A disk file (1) is refused: the runtime's standard
 * handles hold no file.
 *
 * One answer is information only, neither refused nor answered from:
 * CoInitialize at Init (`wmi_coinit`). It is the probe's own call, and by Init
 * the game's XAudio2 set-up has moved the main thread into the multithreaded
 * apartment (native-outcomes-v1a/native-run.log:15506-:15511), so on the
 * recording host it answers RPC_E_CHANGED_MODE. The game's own call at
 * 0x008646BC came earlier, in the apartment Wine's IMM had made (:11399-:11404),
 * and it succeeded: the CoUninitialize at 0x0086496C (:15240) is reachable only
 * past the setge at 0x008646D6. The runtime's CoInitialize answers from its
 * own depth counter.
 */
#ifndef SA2_RUNTIME_DEVPROFILE_H
#define SA2_RUNTIME_DEVPROFILE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_DP_VERSION        1u
#define SA2_DP_ENV            "SA2_DEVICE_PROFILE"
#define SA2_DP_FILE           "sa2-device-profile.txt"
/* The limits, here once for the C reader; tools/s4/device_profile.py holds the
 * same numbers and the test holds the two together. Each is a consumer's
 * capacity, and each consumer _Static_asserts its own against the name here,
 * so a profile a reader accepts is never one a module refuses mid-run (the
 * 2026-09-23 review found 512 entities accepted here and 253 refused by the
 * WMI module, 8 displays here and 6 there). */
#define SA2_DP_MAX_GAMECTRL   16u    /* runtime/sa2_dinput.h SA2_DI_MAX_ENUM */
#define SA2_DP_MAX_WMI_PNP    253u   /* runtime/sa2_wbem.h: 256 object slots less the
                                      * locator, the services and the enumerator */
#define SA2_DP_MAX_DDRAW      6u     /* runtime/sa2_ddraw.h: the enumeration records its
                                      * 4 KiB block holds */
#define SA2_DP_MAX_PROCESSORS 32u    /* the bits of a 32-bit process's affinity mask */
#define SA2_DP_NAME_MAX       260u   /* MAX_PATH: DIDEVICEINSTANCEA's names, with the NUL */
#define SA2_DP_DEVICEID_MAX   512u   /* a DeviceID's UTF-8 bytes, with the NUL */
#define SA2_DP_XINPUT_SLOTS   4u
#define SA2_DP_WMI_BATCH      20u    /* IEnumWbemClassObject::Next's count at 0x008647A9 */
#define SA2_DP_NOT_CONNECTED  1167u  /* ERROR_DEVICE_NOT_CONNECTED */

/* The locale section's LCTYPEs, in the file's order: every LCTYPE the game's C
 * runtime passes GetLocaleInfoA/W, directly or through ___getlocaleinfo
 * (0x007B37FD) and __crtGetLocaleInfoA (0x007BC689), each pushed as an
 * immediate at the address given:
 *   0x0001        LOCALE_ILANGUAGE: __crtGetLocaleInfoA_stat's probe for the W
 *                 function (0x007BC578, LOCALE_NEUTRAL) and _TestDefaultLanguage
 *                 (0x007B7B30)
 *   0x0003 0x1001 LOCALE_SABBREVLANGNAME, LOCALE_SENGLANGUAGE: the language
 *                 enumeration callbacks (0x007B7D55, 0x007B7B83), and
 *                 __get_qualified_locale's name (0x007B8073)
 *   0x0007 0x1002 LOCALE_SABBREVCTRYNAME, LOCALE_SENGCOUNTRY: the country
 *                 callbacks (0x007B7A77, 0x007B7B83) and the name (0x007B8087)
 *   0x000B 0x1004 LOCALE_IDEFAULTCODEPAGE, LOCALE_IDEFAULTANSICODEPAGE:
 *                 _ProcessCodePage (0x007B79AB, 0x007B79DA) and ___ansicp (0x007B97A6)
 *   0x000E-0x0010 ___init_numeric (0x007B73E6-0x007B7413)
 *   0x0014-0x0018 0x0019 0x001A 0x0050-0x0057 ___init_monetary (0x007B7622-0x007B7743)
 *   0x001F 0x0020 0x0028-0x004F 0x1003 0x1009 __get_lc_time (0x007B6CF1-0x007B70BF)
 * plus the names the runtime answered before profiles carried a locale
 * (0x0004 0x0005 0x0008 0x0009 0x000A), so that no answer it gives a capture's
 * run comes from its built-in table. The list is sorted; SA2_DP_LOCALE_TYPES
 * is its length, and tools/s4/device_profile.py holds the same list. */
#define SA2_DP_LOCALE_LCTYPES                                                               \
    0x0001u, 0x0003u, 0x0004u, 0x0005u, 0x0007u, 0x0008u, 0x0009u, 0x000Au, 0x000Bu,       \
    0x000Eu, 0x000Fu, 0x0010u, 0x0014u, 0x0015u, 0x0016u, 0x0017u, 0x0018u, 0x0019u,       \
    0x001Au, 0x001Fu, 0x0020u, 0x0028u, 0x0029u, 0x002Au, 0x002Bu, 0x002Cu, 0x002Du,       \
    0x002Eu, 0x002Fu, 0x0030u, 0x0031u, 0x0032u, 0x0033u, 0x0034u, 0x0035u, 0x0036u,       \
    0x0037u, 0x0038u, 0x0039u, 0x003Au, 0x003Bu, 0x003Cu, 0x003Du, 0x003Eu, 0x003Fu,       \
    0x0040u, 0x0041u, 0x0042u, 0x0043u, 0x0044u, 0x0045u, 0x0046u, 0x0047u, 0x0048u,       \
    0x0049u, 0x004Au, 0x004Bu, 0x004Cu, 0x004Du, 0x004Eu, 0x004Fu, 0x0050u, 0x0051u,       \
    0x0052u, 0x0053u, 0x0054u, 0x0055u, 0x0056u, 0x0057u, 0x1001u, 0x1002u, 0x1003u,       \
    0x1004u, 0x1009u
#define SA2_DP_LOCALE_TYPES     74u
/* A value's UTF-8 bytes: MAX_CBUF_LEN, the 128-byte buffer ___getlocaleinfo
 * reads an LC_STR_TYPE answer into first (0x007B3834), less its NUL. */
#define SA2_DP_LOCALE_VALUE_MAX 127u
/* The one ANSI code page the runtime's conversions implement (sa2_codepage.c). */
#define SA2_DP_LOCALE_ACP       1252u

/* The environment section's limits. The block lives in the runtime's 64 KiB
 * guest-side string arena beside the command line (runtime/sa2_imports.c
 * SA2_RT_STRINGS_LEN), so it may take half of it; the recording host's block
 * is 80 strings and 4266 units, its longest string 370
 * (private/generated/oracle/captures/n0-trace-v1a/sa2-n0-envblock.bin). */
#define SA2_DP_ENV_MAX_STRINGS  256u     /* strings in the block */
#define SA2_DP_ENV_MAX_UNITS    16384u   /* UTF-16 units, every NUL included */
#define SA2_DP_ENV_VALUE_MAX    4095u    /* one string's UTF-8 bytes */
/* The standard handles, in the file's order: stdin, stdout, stderr. */
#define SA2_DP_STD_HANDLES      3u

/* The largest file a reader accepts, derived rather than chosen: each record
 * kind's longest line times the most of it a file can hold. A round megabyte
 * stood here until the 2026-09-23 review showed 512 entities of escaped
 * DeviceIDs overflowing it. A line's longest form is its fixed text with every
 * hex at ten characters and every dec at the most digits an accepted file can
 * hold there (sa2_devprofile.c asserts the digits against the limits), plus
 * each GUID at 38 characters and each string at its longest spelling: the
 * quotes and \xhh for every byte. sizeof counts a literal's NUL, which stands
 * for the line's LF. */
#define SA2_DP_GUID_CHARS       38u
#define SA2_DP_STR_CHARS(bytes) (2u + 4u * (bytes))
#define SA2_DP_LINE(text)       ((uint32_t)sizeof(text))
#define SA2_DP_MAX_BYTES ((uint32_t)(                                                        \
    SA2_DP_LINE("sa2-device-profile 1")                                                      \
    + SA2_DP_LINE("system processors=32 process_mask=0x00000000 system_mask=0x00000000")     \
    + SA2_DP_LINE("dinput8_create hr=0x00000000")                                            \
    + SA2_DP_LINE("dinput8_syskeyboard hr=0x00000000")                                       \
    + SA2_DP_LINE("dinput8_sysmouse hr=0x00000000")                                          \
    + SA2_DP_MAX_GAMECTRL * (SA2_DP_LINE("dinput8_gamectrl index=15 instance= product= "     \
                                         "ffdriver= devtype=0x00000000 "                     \
                                         "usage_page=0x00000000 usage=0x00000000 "           \
                                         "instance_name= product_name= create=0x00000000")  \
                             + 3u * SA2_DP_GUID_CHARS                                        \
                             + 2u * SA2_DP_STR_CHARS(SA2_DP_NAME_MAX - 1u))                  \
    + SA2_DP_LINE("dinput8_enum hr=0x00000000 count=16")                                     \
    + SA2_DP_XINPUT_SLOTS * SA2_DP_LINE("xinput_caps slot=3 code=0x00000000")                \
    + SA2_DP_LINE("wmi_coinit hr=0x00000000")                                                \
    + SA2_DP_LINE("wmi_locator hr=0x00000000")                                               \
    + SA2_DP_LINE("wmi_connect hr=0x00000000")                                               \
    + SA2_DP_LINE("wmi_blanket hr=0x00000000")                                               \
    + SA2_DP_LINE("wmi_enum hr=0x00000000")                                                  \
    /* every full batch, one short batch and the empty one that ends the run */              \
    + (SA2_DP_MAX_WMI_PNP / SA2_DP_WMI_BATCH + 2u)                                           \
      * SA2_DP_LINE("wmi_next hr=0x00000000 returned=20")                                    \
    + SA2_DP_MAX_WMI_PNP * (SA2_DP_LINE("wmi_pnp index=252 get=0x00000000 vt=0x00000008 "    \
                                        "deviceid=")                                         \
                            + SA2_DP_STR_CHARS(SA2_DP_DEVICEID_MAX - 1u))                    \
    + SA2_DP_LINE("wmi_entities count=253")                                                  \
    + SA2_DP_LINE("d3d9_adapters count=1")                                                   \
    + SA2_DP_MAX_DDRAW * (SA2_DP_LINE("ddraw_device index=5 guid= monitor=adapter0 "         \
                                      "description= name=")                                  \
                          + SA2_DP_GUID_CHARS + 2u * SA2_DP_STR_CHARS(SA2_DP_NAME_MAX - 1u)) \
    + SA2_DP_LINE("ddraw_enum hr=0x00000000 count=6")                                        \
    + SA2_DP_LINE("ddraw_create guid= hr=0x00000000") + SA2_DP_GUID_CHARS                    \
    + SA2_DP_LINE("ddraw_query hr=0x00000000")                                               \
    + SA2_DP_LINE("ddraw_vidmem hr=0x00000000 total=0x00000000")                             \
    + SA2_DP_LINE("xaudio2_master voice=1 flags=0x00000000 channels=4294967295 "             \
                  "rate=4294967295")                                                         \
    + SA2_DP_LINE("locale_user lcid=0x00000000 system=0x00000000 acp=4294967295 "            \
                  "oemcp=4294967295 valid=1")                                               \
    + SA2_DP_LOCALE_TYPES * (SA2_DP_LINE("locale_info lctype=0x00000000 error=0x00000000 "   \
                                         "value=")                                          \
                             + SA2_DP_STR_CHARS(SA2_DP_LOCALE_VALUE_MAX))                   \
    + SA2_DP_LINE("environment strings=256 crt=256")                                         \
    /* each string's line and quotes; then each unit of the block as three \xhh */           \
    + SA2_DP_ENV_MAX_STRINGS * (SA2_DP_LINE("env index=255 value=") + SA2_DP_STR_CHARS(0u))  \
    + 4u * 3u * SA2_DP_ENV_MAX_UNITS                                                         \
    + SA2_DP_STD_HANDLES * SA2_DP_LINE("std_handle which=2 handle=0x00000000 type=3")        \
    + SA2_DP_LINE("end")))

/* The default profile's video memory: not a measurement, the same named
 * placeholder runtime/sa2_d3d9.c answers GetAvailableTextureMem with, so a run
 * with no capture gets one figure for both. */
#define SA2_DP_DEFAULT_VIDMEM 0x10000000u
/* The default mastering voice: stereo, at the 48000 Hz the game asks
 * CreateMasteringVoice for (0x007841FB). Also not a measurement. */
#define SA2_DP_DEFAULT_CHANNELS 2u
#define SA2_DP_DEFAULT_RATE     48000u

typedef struct {
    uint8_t  guid_instance[16], guid_product[16], guid_ffdriver[16];
    uint32_t dev_type;
    uint16_t usage_page, usage;
    char     instance_name[260], product_name[260];
} sa2_dp_didevice;

typedef struct {
    int     has_guid;
    uint8_t guid[16];
    char    description[260], name[260];
} sa2_dp_ddraw;

/* Reads SA2_DEVICE_PROFILE; traps naming the file and line on anything
 * malformed or anything this runtime cannot reproduce. Unset: the default.
 * Set and empty is refused: an empty value is a caller's mistake, and
 * "default" has to be asked for by leaving the variable unset. */
void sa2_devprofile_config_from_env(void);
int sa2_devprofile_is_default(void);
const char *sa2_devprofile_source(void);      /* the path, or "default" */

/* CreateDevice(GUID_SysKeyboard) and (GUID_SysMouse) natively; 0 in the default. */
uint32_t sa2_dp_syskeyboard_hr(void);
uint32_t sa2_dp_sysmouse_hr(void);
/* EnumDevices(DI8DEVCLASS_GAMECTRL, DIEDFL_ATTACHEDONLY), in native order. */
uint32_t sa2_dp_gamectrl_count(void);
const sa2_dp_didevice *sa2_dp_gamectrl(uint32_t i);
/* XInputGetCapabilities(slot, XINPUT_FLAG_GAMEPAD): 1167 in every slot, since
 * a reader accepts nothing else; ERROR_BAD_ARGUMENTS (160) past slot 3. */
uint32_t sa2_dp_xinput_caps_hr(uint32_t slot);
/* Win32_PNPEntity through IWbemServices::CreateInstanceEnum, in native order;
 * each DeviceID in UTF-8, valid, since a reader refuses any other. */
uint32_t sa2_dp_wmi_pnp_count(void);
const char *sa2_dp_wmi_pnp_deviceid(uint32_t i);
/* DirectDrawEnumerateExA(DDENUM_ATTACHEDSECONDARYDEVICES), in native order. */
uint32_t sa2_dp_ddraw_count(void);
const sa2_dp_ddraw *sa2_dp_ddraw_device(uint32_t i);

/* Added to the contract: answers the game reads that the contract's rows do
 * not carry, each recorded natively by the probe. */

/* The sha256 of the bytes this module parsed, 64 lower-case hex digits; "" in
 * the default. The replay tools compare it with the capture's receipt, so the
 * run record says which profile the run read, not which one a tool meant. */
const char *sa2_devprofile_sha256(void);
/* GetSystemInfo's dwNumberOfProcessors, and GetProcessAffinityMask
 * (GetCurrentProcess())'s process and system masks, natively: from 1 to 32,
 * and each mask non-zero with no bit past the count. 1, 0x1 and 0x1 in the
 * default. The game reads the count at 0x007FB9CD, and at 0x00865288 tests the
 * ideal processor's bit of the system mask -- the third argument's local,
 * pushed at 0x0086526A -- before SetThreadIdealProcessor (0x00865293). */
uint32_t sa2_dp_processors(void);
uint32_t sa2_dp_process_mask(void);
uint32_t sa2_dp_system_mask(void);
/* CreateDevice(guidInstance) inside the game's EnumDevices callback, whose
 * failure skips the WMI query (0x00864CB2); natively per device. */
uint32_t sa2_dp_gamectrl_create_hr(uint32_t i);
/* Information only: what the probe's own CoInitialize answered at Init, which
 * is the apartment the main thread was in by then, not the game's answer at
 * 0x008646BC (see "One answer is information only" above). No reader refuses
 * any value and nothing in the runtime answers from it; 0 in the default. */
uint32_t sa2_dp_wmi_coinit_hr(void);
/* CoSetProxyBlanket as IsXInputDevice meets it (0x0086476B), passed through
 * whatever it was; S_OK in the default, where no controller reaches it. */
uint32_t sa2_dp_wmi_blanket_hr(void);
/* A DirectDraw device's monitor as the Direct3D adapter whose
 * GetAdapterMonitor returns it, or -1 for a null HMONITOR. The game's
 * callback 0x00865C20 compares it with IDirect3D9::GetAdapterMonitor(0) and
 * keeps the GUID of the device that matches. */
int32_t  sa2_dp_ddraw_monitor_adapter(uint32_t i);
/* IDirectDraw7::GetAvailableVidMem(DDSCAPS_VIDEOMEMORY|DDSCAPS_LOCALVIDMEM),
 * which 0x00865D20 stores as megabytes at +0x48; the total is what the game's
 * local holds after the call, 0 when it failed (0x00865E09). */
uint32_t sa2_dp_ddraw_vidmem_hr(void);
uint32_t sa2_dp_ddraw_vidmem_total(void);
/* XAUDIO2_VOICE_DETAILS of the game's mastering voice, as GetVoiceDetails
 * (0x0078421D) left them in the game's own globals at 0x01AF0EE0: the
 * creation flags, the input channels -- the speakers' count, which the game
 * compares with 2 at 0x007843A6 to choose its output matrix -- and the rate. */
/* The locale section (see "The locale" above). sa2_dp_locale_recorded is 0
 * when the profile has none, the default's case, and then the runtime answers
 * as it did before profiles carried a locale; every other accessor answers
 * from the section and reads 0 without one. */
int      sa2_dp_locale_recorded(void);
uint32_t sa2_dp_locale_lcid(void);
uint32_t sa2_dp_locale_system(void);
uint32_t sa2_dp_locale_acp(void);
uint32_t sa2_dp_locale_oemcp(void);
/* GetLocaleInfoW(user, lctype) natively: 1 with the answer's UTF-16 units (no
 * NUL) in *w and *n; 0 with the error it failed with in *error; -1 when the
 * section records no such LCTYPE or there is no section. */
int      sa2_dp_locale_info(uint32_t lctype, const uint16_t **w, uint32_t *n, uint32_t *error);
/* The environment section (see "The environment" above). sa2_dp_env_recorded
 * is 0 without one, the default's case, and the runtime then answers an empty
 * block. sa2_dp_env_block: the part of the block the C runtime saw, its `crt`
 * strings each with its NUL, in *units (the block's final NUL is the
 * caller's). sa2_dp_env_strings and sa2_dp_env_crt: the counts. */
int             sa2_dp_env_recorded(void);
uint32_t        sa2_dp_env_strings(void);
uint32_t        sa2_dp_env_crt(void);
const uint16_t *sa2_dp_env_block(uint32_t *units);
/* The standard-handle section (see "The standard handles" above), 0 without
 * one. sa2_dp_std_handle: GetStdHandle's native answer for `which` (0 stdin,
 * 1 stdout, 2 stderr), for information; sa2_dp_std_type: GetFileType's, 0 for
 * no handle. */
int      sa2_dp_std_recorded(void);
uint32_t sa2_dp_std_handle(unsigned which);
uint32_t sa2_dp_std_type(unsigned which);
uint32_t sa2_dp_xaudio2_master_flags(void);
uint32_t sa2_dp_xaudio2_master_channels(void);
uint32_t sa2_dp_xaudio2_master_rate(void);

#ifdef __cplusplus
}
#endif
#endif
