/* The WMI objects IsXInputDevice (0x00864680) asks for, answering from the
 * capture's device profile.
 *
 * Why this exists
 * ---------------
 * The game's EnumDevices callback (0x00864C90) asks, for every game controller
 * DirectInput enumerates, whether it is an XInput device. The answer is
 * Microsoft's sample IsXInputDevice, inlined at 0x00864680: a WMI query for
 * every Plug and Play entity, looking for a DeviceID that carries "IG_" and
 * the controller's VID/PID. On the recording host DirectInput enumerates one
 * controller, the keyboard's receiver, so the native game runs the query
 * (native-outcomes-v1a:14826-15240) and the lifted game must too: the
 * 2026-09-23 ruling counts a refusal only where it reproduces native, and the
 * CoCreateInstance refusal did not.
 *
 * What the image calls, decoded (tools/s4/dinput_surface.py)
 * ------------------------------------------------------------
 *   0x008646BC  CoInitialize(NULL)
 *   0x008646DB  CoCreateInstance(CLSID_WbemLocator 0x008FFF88, NULL, 1,
 *                                IID_IWbemLocator 0x008FFF98, &locator)
 *   0x008646FE..0x00864724  SysAllocString x3: "\\.\root\cimv2" (0x0089F068),
 *                           "Win32_PNPEntity" (0x0089F088), "DeviceID" (0x0089F0A8)
 *   0x0086474A  IWbemLocator::ConnectServer(ns, 0, 0, 0, 0, 0, 0, &services)
 *   0x0086476B  CoSetProxyBlanket(services, 10, 0, NULL, 3, 3, NULL, 0)
 *   0x00864787  IWbemServices::CreateInstanceEnum(class, 0, NULL, &enum)
 *   0x008647B4, 0x008648DD  IEnumWbemClassObject::Next(10000, 20, objs, &n)
 *   0x008647E7  IWbemClassObject::Get(name, 0, &var, NULL, NULL)
 *   0x008648B1, 0x0086491E  IWbemClassObject::Release
 *   0x008648F9..0x0086490B  SysFreeString x3 on the three names
 *   0x00864938, 0x0086494C, 0x00864960  Release of enum, locator, services
 *   0x0086496C  CoUninitialize
 * Those slots have handlers; every other slot of the four vtables is named
 * with no handler and refuses by name, and so does any argument those sites
 * cannot pass.
 *
 * What the answers are, and their native evidence (native-outcomes-v1a)
 * ---------------------------------------------------------------------
 * ":N" is line N of private/generated/oracle/captures/native-outcomes-v1a/
 * native-run.log, a WINEDEBUG=+dinput,+xinput,+xaudio2,+wbemprox,+ole,+loaddll,
 * +ddraw boot on the recording host; the game's thread is 0024.
 *  - CoCreateInstance(WbemLocator): S_OK and a locator (:14827-14843).
 *  - ConnectServer("\\.\root\cimv2"): S_OK and a services object (:14847-14849).
 *  - CoSetProxyBlanket: native ole32 asks the services object for
 *    IClientSecurity (:14851), calls SetBlanket (:14852) and releases it
 *    (:14853); wbemprox's SetBlanket does nothing. The runtime's import stub
 *    calls sa2_wbem_proxy_blanket, which does the same three things to the
 *    same object, records them, and answers the HRESULT the capture recorded
 *    (sa2_dp_wmi_blanket_hr; S_OK in the default).
 *  - CreateInstanceEnum("Win32_PNPEntity"): S_OK and an enumerator over the
 *    profile's entities, in the profile's order (:14854-14859; 74 on the
 *    recording host, :14857).
 *  - Next(10000, 20): WBEM_S_NO_ERROR with 20 objects while 20 remain, then
 *    WBEM_S_FALSE with the short count, then WBEM_S_FALSE with 0 -- exactly
 *    Wine's enum_class_object_Next, which the trace shows as five calls for 74
 *    entities (:14860, :14962, :15063, :15164, :15235).
 *  - Get("DeviceID"): S_OK with VT_BSTR (the word at +0) and a new BSTR (the
 *    dword at +8) holding the profile's DeviceID in UTF-16; nothing else in
 *    the VARIANT is written, which is what Wine's set_variant does
 *    (:14902-14903, then one SysAllocStringLen per entity).
 *  - Release: the reference count; each object dies at 0 (:14904, :15236-15239).
 *
 * The BSTR a Get returns is **never freed, natively or here**. The image imports
 * OLEAUT32 ordinals 2 and 6 only (SysAllocString, SysFreeString), so there is
 * no VariantClear; the only SysFreeString calls are the three at
 * 0x008648F9..0x0086490B on the names the game allocated itself. Wine's
 * class_object_Release frees the class object, not the caller's VARIANT. So a
 * Get's BSTR stays allocated on the process heap, as native's does, and a run
 * under the recording host's profile leaves one live BSTR per entity.
 *
 * **Where that allocation lands still differs from native.** Natively the
 * process heap is its own heap, and the game's CRT makes another with
 * HeapCreate (0x007B517A), so no BSTR moves a CRT block. In this runtime every
 * heap is a view of one guest arena (runtime/sa2_imports.c, heap_alloc), so
 * the BSTRs a profile's entities leave behind take arena space the CRT's later
 * blocks would otherwise have, and those blocks land elsewhere. The digest
 * excludes words that hold addresses, so the difference can show only through
 * behaviour that depends on an address. **Unmeasured.**
 *
 * What would show it is a control that removes the BSTRs and nothing else:
 * the same replay under the capture's own profile with only its `wmi_pnp`
 * entities removed (one empty `wmi_next`, `wmi_entities` counting 0):
 * dump(dataclasses.replace(parse(capture), pnp=())) with
 * tools/s4/device_profile.py, written to scratch when the run is made and
 * never committed, because the rest of it is the capture's host inventory.
 * **Not the default profile**, which differs from a capture's in more than
 * the entities: it answers one processor where the recording host's capture
 * answers 16, and with one the boot run diverges in d_01760000 from tick 379
 * (agents-journals/s4/2026-09-23-native-outcomes.md, "The boot replay's last
 * divergence"); it enumerates no controller, so IsXInputDevice never runs;
 * and its DirectDraw devices and mastering voice differ.
 *
 * Under the control the controller is still enumerated and created, and
 * IsXInputDevice still runs: it allocates and frees its three names, connects,
 * and enumerates, and its one Next answers WBEM_S_FALSE with 0 objects. What
 * that must change: no Get, no class object, no DeviceID BSTR, so the heap
 * blocks allocated after the query land where they would with no BSTR. What
 * it must not change: the DirectInput calls, on a capture whose
 * IsXInputDevice answered "no" as the recording host's did
 * (native-outcomes-v1a:15241-15244), since with no entity it finds no "IG_"
 * and answers "no" again; and every compared column that depends on no
 * address. A compared column whose first divergence from native, after
 * input initialisation, differs between the capture-profile run and this
 * control is the heap shift reaching behaviour.
 *
 * The entities are the profile's
 * ------------------------------
 * The DeviceIDs are host inventory and live only in the private capture's
 * profile (runtime/sa2_devprofile.h). This file stores none of them; it reads
 * each when a Get asks for it. With the default profile there are none, and
 * the query is never reached anyway: no controller is enumerated, so the
 * callback that runs IsXInputDevice is never called.
 *
 * Addresses are fixed per role, not allocated per call: the locator, the
 * services object and the enumerator each have one slot, and entity i's class
 * object has slot SA2_WBEM_FIRST_ENTITY + i. The image releases every one of
 * them before IsXInputDevice returns (0x0086490D..0x00864962), so a second
 * enumeration reuses the same addresses, as Wine's heap reused freed class
 * objects' (0293CE60 created at :14891, destroyed at :14946, created again at
 * :14964 and :15166). Creating a role whose object is still alive refuses by
 * name.
 */
#ifndef SA2_RUNTIME_WBEM_H
#define SA2_RUNTIME_WBEM_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Method thunks, above sa2_dinput.h's (0x00230000..0x00230800); the objects
 * above its enumeration scratch and the vtables above its vtables. Each is
 * asserted against sa2_dinput.h's own names in sa2_wbem.c. */
#define SA2_WBEM_THUNK_BASE   0x00231000u
#define SA2_WBEM_THUNK_STRIDE 16u
#define SA2_WBEM_MAX_TYPES    4u
#define SA2_WBEM_MAX_SLOTS    32u     /* IWbemClassObject has 27 */
#define SA2_WBEM_THUNK_END    0x00231800u

/* Objects, 16 bytes each (vtable, tag, type, index), and the vtables. */
#define SA2_WBEM_OBJ_BASE     0x03272000u
#define SA2_WBEM_OBJ_STRIDE   16u
#define SA2_WBEM_OBJ_SLOTS    256u
#define SA2_WBEM_VT_BASE      0x03281000u
#define SA2_WBEM_VT_END       0x03281400u
#define SA2_WBEM_VT_STRIDE    (SA2_WBEM_MAX_SLOTS * 4u)

/* Slot roles. */
#define SA2_WBEM_SLOT_LOCATOR  0u
#define SA2_WBEM_SLOT_SERVICES 1u
#define SA2_WBEM_SLOT_ENUM     2u
#define SA2_WBEM_FIRST_ENTITY  3u
/* The most Win32_PNPEntity rows this file answers: one class-object slot each.
 * Both profile readers refuse a profile with more than
 * runtime/sa2_devprofile.h's SA2_DP_MAX_WMI_PNP, and sa2_wbem.c asserts that
 * limit against this one. */
#define SA2_WBEM_MAX_ENTITIES  253u
/* The longest DeviceID this file converts, in UTF-16 units. A reader accepts
 * at most SA2_DP_DEVICEID_MAX - 1 bytes of UTF-8, and UTF-8 never takes fewer
 * bytes than UTF-16 takes units, so every accepted DeviceID fits (asserted in
 * sa2_wbem.c). Windows bounds a device instance ID at MAX_DEVICE_ID_LEN, 200
 * characters. */
#define SA2_WBEM_MAX_ID_UNITS  512u

/* HRESULTs, by their published values. */
#define SA2_WBEM_S_NO_ERROR    0x00000000u
#define SA2_WBEM_S_FALSE       0x00000001u
#define SA2_WBEM_E_INVALID_PARAMETER 0x80041008u

/* How an answer was arrived at. */
enum {
    SA2_WBEM_ANS_REFUSED = 0,   /* recorded, then trapped by name */
    SA2_WBEM_ANS_NOOP    = 1,   /* S_OK with nothing to answer */
    SA2_WBEM_ANS_GUEST   = 2,   /* answered from what the guest passed */
    SA2_WBEM_ANS_OBJECT  = 3,   /* answered by fabricating an interface */
    SA2_WBEM_ANS_PROFILE = 4,   /* the capture's device profile supplied the answer */
};

#define SA2_WBEM_LOG_MAX 2048u

typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t self;
    uint16_t type;
    uint16_t slot;
    uint8_t  answered;
    uint8_t  argc;
    uint32_t arg[8];
    uint32_t ret;
    uint32_t out0, out1;
} sa2_wbem_call;

typedef struct {
    uint32_t calls;
    uint32_t first_seq;
    uint32_t first_caller;
    uint8_t  answered;
    uint8_t  used;
} sa2_wbem_slot_stat;

typedef struct {
    uint32_t cocreate_calls, cocreate_last_caller;
    uint32_t method_calls, distinct_methods, objects_built, revivals, unknown_this;
    uint32_t refused, noop, from_guest, object, profile;
    uint32_t connects, enumerators, next_calls, objects_returned, gets;
    uint32_t bstrs_allocated;             /* by Get, and never freed: see above */
    uint32_t bstr_bytes;                  /* their payload bytes, prefix and NUL included */
    /* CoSetProxyBlanket, answered as ole32 answers it: QueryInterface of the
     * proxy for IClientSecurity, SetBlanket, Release. */
    uint32_t blanket_calls, blanket_caller;
    uint32_t blanket_args[8];
    uint32_t qi_client_security, client_security_set_blanket, client_security_release;
    uint32_t log_dropped;
    uint32_t profile_entities;
} sa2_wbem_stats;

void sa2_wbem_config_from_env(void);

/* ole32!CoCreateInstance for the classes this file serves. `*handled` is set
 * to 1 when `clsid` is CLSID_WbemLocator, and the return value is then the
 * HRESULT; for any other class it is 0, nothing is touched and the caller
 * moves on. `s` is the CPU state of the import call, whose ESP still points
 * at the return address: the caller is read from [esp], and pUnkOuter and
 * dwClsContext, which must be the one site's NULL and CLSCTX_INPROC_SERVER,
 * from [esp+8] and [esp+12]. */
uint32_t sa2_wbem_cocreate(void *s, const uint8_t clsid[16], const uint8_t iid[16],
                           uint32_t ppv, uint32_t *handled);

/* ole32!CoSetProxyBlanket(pProxy, dwAuthnSvc, dwAuthzSvc, pServerPrincName,
 * dwAuthnLevel, dwImpLevel, pAuthInfo, dwCapabilities): `a` is the import's
 * eight stack arguments. */
uint32_t sa2_wbem_proxy_blanket(const uint32_t *a, uint32_t caller);

void sa2_wbem_stats_get(sa2_wbem_stats *out);
uint32_t             sa2_wbem_log_count(void);
const sa2_wbem_call *sa2_wbem_log_at(uint32_t i);

uint32_t    sa2_wbem_type_count(void);
const char *sa2_wbem_type_name(uint32_t type);
const char *sa2_wbem_method_name(uint32_t type, uint32_t slot);   /* NULL when unnamed */
int         sa2_wbem_method_argc(uint32_t type, uint32_t slot);   /* -1 when unnamed */
int         sa2_wbem_method_implemented(uint32_t type, uint32_t slot);
const char *sa2_wbem_method_flags(uint32_t type, uint32_t slot);
const sa2_wbem_slot_stat *sa2_wbem_slot_stat_at(uint32_t type, uint32_t slot);

/* The guest address of the object in `slot`, and whether it is alive. */
uint32_t sa2_wbem_object_addr(uint32_t slot);
int      sa2_wbem_object_alive(uint32_t slot);

#ifdef __cplusplus
}
#endif
#endif
