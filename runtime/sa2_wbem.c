/* The WMI objects IsXInputDevice asks for. See sa2_wbem.h. */
#include "sa2_wbem.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_devprofile.h"
#include "sa2_dinput.h"        /* its block ends, which this file's blocks sit above */
#include "sa2_trap.h"

/* OLEAUT32's BSTR allocator, defined in runtime/sa2_imports.c beside
 * SysAllocString (ordinal 2), which is the same function: a 4-byte byte count,
 * the UTF-16 units, a NUL unit, on the process heap GetProcessHeap hands out.
 * A Get's BSTR comes from there because native wbemprox allocates it with
 * SysAllocString too (one SysAllocStringLen line per Get, native-outcomes-v1a
 * :14903 onward), and the game could hand it to SysFreeString. */
extern uint32_t sa2_bstr_alloc(const uint16_t *units, uint32_t n);

/* ------------------------------------------------------------ the rows */

enum { DF_OUT = 1u << 0 };

typedef struct wb_ctx wb_ctx;
typedef uint32_t (*wb_method)(wb_ctx *c);

typedef struct {
    uint8_t     slot;
    uint8_t     argc;        /* stack dwords, NOT counting `this` */
    uint8_t     flags;
    const char *name;
    wb_method   fn;          /* NULL: named, and refused by name */
} wb_row;

typedef struct {
    const char *name;
    wb_row     *rows;
    uint32_t    vt;
} wb_type;

enum { T_LOC = 0, T_SVC = 1, T_ENUM = 2, T_OBJ = 3 };

typedef struct {
    uint32_t addr;
    uint16_t type;
    uint16_t alive;
    uint32_t refs;
    uint32_t entity;         /* the profile's entity, for T_OBJ */
    uint32_t built;          /* built at least once, so a later build is a revival */
} wb_obj;

struct wb_ctx {
    void           *s;
    uint32_t        pc;
    uint32_t        self;
    wb_obj         *obj;
    const uint32_t *a;
    uint32_t        caller;
    uint8_t         answered;
    uint32_t        out0, out1;
};

_Static_assert(SA2_WBEM_THUNK_BASE >= SA2_DI_THUNK_END && SA2_WBEM_THUNK_END <= 0x00300000u,
               "the wbem thunks must sit above sa2_dinput.h's and below 0x00300000");
_Static_assert(SA2_WBEM_THUNK_BASE + SA2_WBEM_MAX_TYPES * SA2_WBEM_MAX_SLOTS
               * SA2_WBEM_THUNK_STRIDE == SA2_WBEM_THUNK_END,
               "SA2_WBEM_THUNK_END is the thunk block's own product");
_Static_assert(SA2_WBEM_OBJ_BASE >= SA2_DI_SCRATCH_END
               && SA2_WBEM_OBJ_BASE + SA2_WBEM_OBJ_SLOTS * SA2_WBEM_OBJ_STRIDE <= 0x03273000u,
               "the wbem objects must sit above sa2_dinput.h's enumeration scratch and stay "
               "below 0x03273000");
_Static_assert(SA2_WBEM_FIRST_ENTITY + SA2_WBEM_MAX_ENTITIES == SA2_WBEM_OBJ_SLOTS,
               "every object slot past the three roles is one entity's class object");
_Static_assert(SA2_WBEM_VT_BASE + SA2_WBEM_MAX_TYPES * SA2_WBEM_VT_STRIDE <= SA2_WBEM_VT_END,
               "the wbem vtables must fit their declared block");
_Static_assert(SA2_WBEM_VT_BASE >= SA2_DI_VT_END && SA2_WBEM_VT_END <= SA2_GUEST_RT_ARENA_BASE,
               "the wbem vtables must sit above sa2_dinput.h's and below the runtime arena");
/* The profile's limits (runtime/sa2_devprofile.h), which both readers refuse
 * past: every profile a run can load is one this file answers whole. */
_Static_assert(SA2_DP_MAX_WMI_PNP <= SA2_WBEM_MAX_ENTITIES,
               "every entity a profile reader accepts needs a class-object slot");
_Static_assert(SA2_DP_DEVICEID_MAX - 1u <= SA2_WBEM_MAX_ID_UNITS,
               "every DeviceID a profile reader accepts must convert within "
               "SA2_WBEM_MAX_ID_UNITS: UTF-8 never takes fewer bytes than UTF-16 takes units");

static sa2_wbem_stats     g_st;
static sa2_wbem_call      g_log[SA2_WBEM_LOG_MAX];
static uint32_t           g_log_used, g_seq;
static sa2_wbem_slot_stat g_slot[SA2_WBEM_MAX_TYPES][SA2_WBEM_MAX_SLOTS];
static wb_obj             g_obj[SA2_WBEM_OBJ_SLOTS];
static uint32_t           g_enum_pos, g_enum_count;
static int                g_types_bound;

/* {4590F811-1D3A-11D0-891F-00AA004B2E24}, the 16 bytes at 0x008FFF88. */
static const uint8_t CLSID_WBEMLOCATOR[16] = {0x11,0xF8,0x90,0x45,0x3A,0x1D,0xD0,0x11,
                                              0x89,0x1F,0x00,0xAA,0x00,0x4B,0x2E,0x24};
/* {DC12A687-737F-11CF-884D-00AA004B2E24}, at 0x008FFF98. */
static const uint8_t IID_IWBEMLOCATOR[16] = {0x87,0xA6,0x12,0xDC,0x7F,0x73,0xCF,0x11,
                                             0x88,0x4D,0x00,0xAA,0x00,0x4B,0x2E,0x24};

/* The strings the image builds its three BSTRs from (0x0089F068, 0x0089F088,
 * 0x0089F0A8), and the Next arguments both sites push. */
#define NS_CIMV2        "\\\\.\\root\\cimv2"
#define CLASS_PNPENTITY "Win32_PNPEntity"
#define PROP_DEVICEID   "DeviceID"
#define NEXT_TIMEOUT    10000u     /* 0x2710, pushed at 0x008647AB and 0x008648D4 */
#define NEXT_COUNT      20u        /* 0x14, pushed at 0x008647A9 and 0x008648D2 */
#define VT_BSTR         8u         /* tested at 0x008647F1 */

/* ------------------------------------------------------------ plumbing */

static void gfault(uint32_t a, const char *what) {
    sa2_trap_note("wbem: %s at guest %#010x is outside the guest space", what, a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}
static void pk32(uint32_t a, uint32_t v) {
    if (!a) gfault(a, "a write through a null out-parameter");
    if (sa2_poke(a, 4, &v) != SA2_OK) gfault(a, "a write of 4 bytes");
}
static void pk16(uint32_t a, uint16_t v) {
    if (sa2_poke(a, 2, &v) != SA2_OK) gfault(a, "a write of 2 bytes");
}

static void refuse(const wb_ctx *c, const char *iface, const char *method, const char *why) {
    sa2_trap_note("%s::%s called from %#010x: %s", iface, method, c->caller, why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
}

/* A guest UTF-16 string, bounds-checked unit by unit: its length, or -1 when it
 * does not end within `cap` units. A unit outside the guest space traps. */
static int read_wstr(uint32_t p, uint16_t *out, uint32_t cap) {
    if (!p) gfault(p, "a read of a string through a null pointer");
    for (uint32_t i = 0; i < cap; i++) {
        uint16_t u = 0;
        if (sa2_peek(p + 2u * i, 2, &u) != SA2_OK) gfault(p + 2u * i, "a read of a UTF-16 unit");
        out[i] = u;
        if (!u) return (int)i;
    }
    return -1;
}

/* The guest string at `p` equals the ASCII literal `want`, exactly. */
static int wstr_is(uint32_t p, const char *want) {
    uint16_t w[64];
    const int n = read_wstr(p, w, 64u);
    if (n < 0 || (size_t)n != strlen(want)) return 0;
    for (int i = 0; i < n; i++)
        if (w[i] != (uint16_t)(unsigned char)want[i]) return 0;
    return 1;
}

/* UTF-8 to UTF-16, strictly: an overlong form, a surrogate code point, a value
 * past U+10FFFF or a truncated sequence is -1, and more than `cap` units is -2.
 * The profile is text written by a probe; a byte this cannot decode is a
 * profile this runtime cannot reproduce, not one to guess at. */
static int utf8_to_utf16(const char *s, uint16_t *out, uint32_t cap) {
    uint32_t n = 0;
    const unsigned char *p = (const unsigned char *)s;
    while (*p) {
        uint32_t cp, need;
        if (p[0] < 0x80u) { cp = p[0]; need = 0; }
        else if ((p[0] & 0xE0u) == 0xC0u) { cp = p[0] & 0x1Fu; need = 1; }
        else if ((p[0] & 0xF0u) == 0xE0u) { cp = p[0] & 0x0Fu; need = 2; }
        else if ((p[0] & 0xF8u) == 0xF0u) { cp = p[0] & 0x07u; need = 3; }
        else return -1;
        for (uint32_t k = 1; k <= need; k++) {
            if ((p[k] & 0xC0u) != 0x80u) return -1;
            cp = (cp << 6) | (p[k] & 0x3Fu);
        }
        if ((need == 1 && cp < 0x80u) || (need == 2 && cp < 0x800u)
            || (need == 3 && cp < 0x10000u) || cp > 0x10FFFFu
            || (cp >= 0xD800u && cp <= 0xDFFFu))
            return -1;
        p += need + 1u;
        if (cp >= 0x10000u) {
            if (n + 2u > cap) return -2;
            cp -= 0x10000u;
            out[n++] = (uint16_t)(0xD800u | (cp >> 10));
            out[n++] = (uint16_t)(0xDC00u | (cp & 0x3FFu));
        } else {
            if (n + 1u > cap) return -2;
            out[n++] = (uint16_t)cp;
        }
    }
    return (int)n;
}

/* --------------------------------------------------------- the objects */

static uint32_t type_vtable(uint32_t t);

static wb_obj *obj_of(uint32_t addr) {
    if (addr < SA2_WBEM_OBJ_BASE || (addr - SA2_WBEM_OBJ_BASE) % SA2_WBEM_OBJ_STRIDE) return NULL;
    const uint32_t i = (addr - SA2_WBEM_OBJ_BASE) / SA2_WBEM_OBJ_STRIDE;
    if (i >= SA2_WBEM_OBJ_SLOTS || !g_obj[i].built) return NULL;
    return &g_obj[i];
}

static const char *const ROLE[] = {"IWbemLocator", "IWbemServices", "IEnumWbemClassObject"};

/* Build the object for `slot`, fresh: refs 1. A slot whose object is still
 * alive refuses, because the image releases every WMI object before
 * IsXInputDevice returns and a second live one means the decode is wrong. */
static wb_obj *obj_make(uint32_t slot, uint32_t t, uint32_t entity, uint32_t pc) {
    if (slot >= SA2_WBEM_OBJ_SLOTS) {
        /* Unreachable while the profile readers hold SA2_DP_MAX_WMI_PNP, which
         * is asserted against the slots above; this keeps g_obj in bounds if
         * the two ever disagree. */
        sa2_trap_note("wbem: entity %u of profile %s needs object slot %u, and there are %u",
                      entity, sa2_devprofile_source(), slot, SA2_WBEM_OBJ_SLOTS);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    wb_obj *o = &g_obj[slot];
    if (o->alive) {
        sa2_trap_note("wbem: the %s in slot %u (%#010x) is still alive with %u references "
                      "when the guest asks for another; the image releases each one before "
                      "IsXInputDevice returns (0x0086490D..0x00864962)",
                      slot < SA2_WBEM_FIRST_ENTITY ? ROLE[slot] : "IWbemClassObject",
                      slot, o->addr, o->refs);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    if (o->built) g_st.revivals++;
    else g_st.objects_built++;
    o->addr = SA2_WBEM_OBJ_BASE + slot * SA2_WBEM_OBJ_STRIDE;
    o->type = (uint16_t)t;
    o->alive = 1;
    o->refs = 1;
    o->entity = entity;
    o->built = 1;
    pk32(o->addr, type_vtable(t));
    pk32(o->addr + 4u, 0x4D454257u);          /* 'WBEM' */
    pk32(o->addr + 8u, t);
    pk32(o->addr + 12u, slot);
    return o;
}

/* ---------------------------------------------------------- the methods */

static uint32_t m_Release(wb_ctx *c) {
    wb_obj *o = c->obj;
    c->answered = SA2_WBEM_ANS_GUEST;
    if (o->refs) o->refs--;
    if (!o->refs) o->alive = 0;
    return o->refs;
}

static uint32_t m_ConnectServer(wb_ctx *c) {
    const uint32_t *a = c->a;
    if (a[1] || a[2] || a[3] || a[4] || a[5] || a[6])
        refuse(c, "IWbemLocator", "ConnectServer",
               "strUser..pCtx are not all 0, and the one site pushes 0 for all six "
               "(0x00864742..0x00864747)");
    if (!a[7])
        refuse(c, "IWbemLocator", "ConnectServer",
               "ppNamespace is NULL; the one site passes a local (0x00864739)");
    if (!wstr_is(a[0], NS_CIMV2))
        refuse(c, "IWbemLocator", "ConnectServer",
               "strNetworkResource is not \\\\.\\root\\cimv2, the BSTR the image builds "
               "from 0x0089F068");
    wb_obj *o = obj_make(SA2_WBEM_SLOT_SERVICES, T_SVC, 0, c->pc);
    pk32(a[7], o->addr);
    g_st.connects++;
    c->out0 = o->addr;
    c->answered = SA2_WBEM_ANS_OBJECT;
    return SA2_WBEM_S_NO_ERROR;
}

static uint32_t m_CreateInstanceEnum(wb_ctx *c) {
    const uint32_t *a = c->a;
    if (a[1] || a[2])
        refuse(c, "IWbemServices", "CreateInstanceEnum",
               "lFlags or pCtx is not 0; the one site pushes 0 for both (0x00864780, "
               "0x00864781)");
    if (!a[3])
        refuse(c, "IWbemServices", "CreateInstanceEnum",
               "ppEnum is NULL; the one site passes a local (0x00864777)");
    if (!wstr_is(a[0], CLASS_PNPENTITY))
        refuse(c, "IWbemServices", "CreateInstanceEnum",
               "strFilter is not Win32_PNPEntity, the BSTR the image builds from 0x0089F088");
    wb_obj *o = obj_make(SA2_WBEM_SLOT_ENUM, T_ENUM, 0, c->pc);
    g_enum_pos = 0;
    g_enum_count = sa2_dp_wmi_pnp_count();
    pk32(a[3], o->addr);
    g_st.enumerators++;
    c->out0 = o->addr;
    c->out1 = g_enum_count;
    c->answered = SA2_WBEM_ANS_OBJECT;
    return SA2_WBEM_S_NO_ERROR;
}

/* Wine's enum_class_object_Next, step for step: nothing returned yet, then one
 * new class object per remaining entity up to uCount; WBEM_S_FALSE the moment
 * the entities run out, WBEM_S_NO_ERROR when uCount were returned. */
static uint32_t m_Next(wb_ctx *c) {
    const uint32_t timeout = c->a[0], count = c->a[1], objs = c->a[2], outn = c->a[3];
    if (timeout != NEXT_TIMEOUT || count != NEXT_COUNT) {
        char why[160];
        snprintf(why, sizeof why, "lTimeout %u uCount %u; both sites push 10000 and 20 "
                 "(0x008647AB/0x008647A9, 0x008648D4/0x008648D2)", timeout, count);
        refuse(c, "IEnumWbemClassObject", "Next", why);
    }
    if (!objs || !outn)
        refuse(c, "IEnumWbemClassObject", "Next",
               "apObjects or puReturned is NULL; both sites pass locals (0x008647A4, "
               "0x0086479F)");
    uint32_t n = 0, first = 0;
    while (n < count && g_enum_pos < g_enum_count) {
        const uint32_t e = g_enum_pos;
        wb_obj *o = obj_make(SA2_WBEM_FIRST_ENTITY + e, T_OBJ, e, c->pc);
        pk32(objs + 4u * n, o->addr);
        if (!n) first = o->addr;
        g_enum_pos++;
        n++;
    }
    pk32(outn, n);
    g_st.next_calls++;
    g_st.objects_returned += n;
    c->out0 = n;
    c->out1 = first;
    c->answered = SA2_WBEM_ANS_PROFILE;
    return n == count ? SA2_WBEM_S_NO_ERROR : SA2_WBEM_S_FALSE;
}

static uint32_t m_Get(wb_ctx *c) {
    const uint32_t name = c->a[0], flags = c->a[1], val = c->a[2], type = c->a[3],
                   flavor = c->a[4];
    if (flags || type || flavor)
        refuse(c, "IWbemClassObject", "Get",
               "lFlags, pType or plFlavor is not 0; the one site pushes 0 for all three "
               "(0x008647E1, 0x008647DB, 0x008647DA)");
    if (!val)
        refuse(c, "IWbemClassObject", "Get",
               "pVal is NULL; the one site passes a local VARIANT (0x008647DC)");
    if (!wstr_is(name, PROP_DEVICEID))
        refuse(c, "IWbemClassObject", "Get",
               "wszName is not DeviceID, the BSTR the image builds from 0x0089F0A8");
    const uint32_t e = c->obj->entity;
    const char *id = sa2_dp_wmi_pnp_deviceid(e);
    uint16_t units[SA2_WBEM_MAX_ID_UNITS];
    const int n = id ? utf8_to_utf16(id, units, SA2_WBEM_MAX_ID_UNITS) : -1;
    if (n < 0) {
        /* Both profile readers refuse a DeviceID that is not valid UTF-8, and
         * the static assertions above make every accepted one fit, so this is
         * the reader and this file disagreeing. The DeviceID is not printed:
         * it is the host's inventory, and a trap note can end up in a
         * receipt. */
        sa2_trap_note("IWbemClassObject::Get: profile %s entity %u's DeviceID %s, which the "
                      "profile reader refuses: the reader and runtime/sa2_wbem.c disagree",
                      sa2_devprofile_source(), e,
                      !id ? "is missing" : n == -2 ? "is longer than 512 UTF-16 units"
                                                   : "is not valid UTF-8");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
    }
    const uint32_t bstr = sa2_bstr_alloc(units, (uint32_t)n);
    if (!bstr) {
        sa2_trap_note("IWbemClassObject::Get: the process heap has no room for a %u-unit "
                      "BSTR (entity %u)", (uint32_t)n, e);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, c->pc);
    }
    /* Wine's set_variant writes V_VT and V_BSTR and nothing else, so the rest
     * of the guest's uninitialised VARIANT stays what it was. */
    pk16(val + 0u, (uint16_t)VT_BSTR);
    pk32(val + 8u, bstr);
    g_st.gets++;
    g_st.bstrs_allocated++;
    g_st.bstr_bytes += 4u + 2u * (uint32_t)n + 2u;
    c->out0 = bstr;
    c->out1 = (uint32_t)n;
    c->answered = SA2_WBEM_ANS_PROFILE;
    return SA2_WBEM_S_NO_ERROR;
}

/* ------------------------------------------------------------ the tables */

static wb_row ROWS_LOC[] = {
    {0, 2, DF_OUT, "QueryInterface", NULL},
    {1, 0, 0, "AddRef", NULL},
    {2, 0, 0, "Release", m_Release},
    {3, 8, DF_OUT, "ConnectServer", m_ConnectServer},
    {0, 0, 0, NULL, NULL},
};

/* IWbemServices, all 26 slots, argument counts from zig's
 * any-windows-any/wbemcli.h. QueryInterface has no handler: the image never
 * calls it. ole32's CoSetProxyBlanket does, natively (:14851), and
 * sa2_wbem_proxy_blanket answers that call where the runtime makes it. */
static wb_row ROWS_SVC[] = {
    {0,  2, DF_OUT, "QueryInterface", NULL},
    {1,  0, 0, "AddRef", NULL},
    {2,  0, 0, "Release", m_Release},
    {3,  5, DF_OUT, "OpenNamespace", NULL},
    {4,  1, 0, "CancelAsyncCall", NULL},
    {5,  2, DF_OUT, "QueryObjectSink", NULL},
    {6,  5, DF_OUT, "GetObject", NULL},
    {7,  4, 0, "GetObjectAsync", NULL},
    {8,  4, DF_OUT, "PutClass", NULL},
    {9,  4, 0, "PutClassAsync", NULL},
    {10, 4, DF_OUT, "DeleteClass", NULL},
    {11, 4, 0, "DeleteClassAsync", NULL},
    {12, 4, DF_OUT, "CreateClassEnum", NULL},
    {13, 4, 0, "CreateClassEnumAsync", NULL},
    {14, 4, DF_OUT, "PutInstance", NULL},
    {15, 4, 0, "PutInstanceAsync", NULL},
    {16, 4, DF_OUT, "DeleteInstance", NULL},
    {17, 4, 0, "DeleteInstanceAsync", NULL},
    {18, 4, DF_OUT, "CreateInstanceEnum", m_CreateInstanceEnum},
    {19, 4, 0, "CreateInstanceEnumAsync", NULL},
    {20, 5, DF_OUT, "ExecQuery", NULL},
    {21, 5, 0, "ExecQueryAsync", NULL},
    {22, 5, DF_OUT, "ExecNotificationQuery", NULL},
    {23, 5, 0, "ExecNotificationQueryAsync", NULL},
    {24, 7, DF_OUT, "ExecMethod", NULL},
    {25, 6, 0, "ExecMethodAsync", NULL},
    {0, 0, 0, NULL, NULL},
};

static wb_row ROWS_ENUM[] = {
    {0, 2, DF_OUT, "QueryInterface", NULL},
    {1, 0, 0, "AddRef", NULL},
    {2, 0, 0, "Release", m_Release},
    {3, 0, 0, "Reset", NULL},
    {4, 4, DF_OUT, "Next", m_Next},
    {5, 2, 0, "NextAsync", NULL},
    {6, 1, DF_OUT, "Clone", NULL},
    {7, 2, 0, "Skip", NULL},
    {0, 0, 0, NULL, NULL},
};

static wb_row ROWS_OBJ[] = {
    {0,  2, DF_OUT, "QueryInterface", NULL},
    {1,  0, 0, "AddRef", NULL},
    {2,  0, 0, "Release", m_Release},
    {3,  1, DF_OUT, "GetQualifierSet", NULL},
    {4,  5, DF_OUT, "Get", m_Get},
    {5,  4, 0, "Put", NULL},
    {6,  1, 0, "Delete", NULL},
    {7,  4, DF_OUT, "GetNames", NULL},
    {8,  1, 0, "BeginEnumeration", NULL},
    {9,  5, DF_OUT, "Next", NULL},
    {10, 0, 0, "EndEnumeration", NULL},
    {11, 2, DF_OUT, "GetPropertyQualifierSet", NULL},
    {12, 1, DF_OUT, "Clone", NULL},
    {13, 2, DF_OUT, "GetObjectText", NULL},
    {14, 2, DF_OUT, "SpawnDerivedClass", NULL},
    {15, 2, DF_OUT, "SpawnInstance", NULL},
    {16, 2, 0, "CompareTo", NULL},
    {17, 2, DF_OUT, "GetPropertyOrigin", NULL},
    {18, 1, 0, "InheritsFrom", NULL},
    {19, 4, DF_OUT, "GetMethod", NULL},
    {20, 4, 0, "PutMethod", NULL},
    {21, 1, 0, "DeleteMethod", NULL},
    {22, 1, 0, "BeginMethodEnumeration", NULL},
    {23, 4, DF_OUT, "NextMethod", NULL},
    {24, 0, 0, "EndMethodEnumeration", NULL},
    {25, 2, DF_OUT, "GetMethodQualifierSet", NULL},
    {26, 2, DF_OUT, "GetMethodOrigin", NULL},
    {0, 0, 0, NULL, NULL},
};

static wb_type g_types[] = {
    {"IWbemLocator",         ROWS_LOC,  0},
    {"IWbemServices",        ROWS_SVC,  0},
    {"IEnumWbemClassObject", ROWS_ENUM, 0},
    {"IWbemClassObject",     ROWS_OBJ,  0},
};
static uint32_t type_count(void) { return (uint32_t)(sizeof g_types / sizeof g_types[0]); }
_Static_assert(sizeof g_types / sizeof g_types[0] <= SA2_WBEM_MAX_TYPES,
               "every wbem type needs a thunk block");

static const wb_row *row_of(uint32_t t, uint32_t slot) {
    if (t >= type_count()) return NULL;
    for (const wb_row *p = g_types[t].rows; p->name; p++)
        if (p->slot == slot) return p;
    return NULL;
}

/* --------------------------------------------------------- the native */

static sa2_wbem_call      *g_cur;
static sa2_wbem_slot_stat *g_cur_slot;

static void bump(uint8_t answered, int by) {
    uint32_t *f;
    switch (answered) {
    case SA2_WBEM_ANS_REFUSED: f = &g_st.refused;    break;
    case SA2_WBEM_ANS_NOOP:    f = &g_st.noop;       break;
    case SA2_WBEM_ANS_GUEST:   f = &g_st.from_guest; break;
    case SA2_WBEM_ANS_OBJECT:  f = &g_st.object;     break;
    default:                   f = &g_st.profile;    break;
    }
    *f = (uint32_t)((int)*f + by);
}

static void note_reserve(uint32_t t, uint32_t slot, uint32_t self, uint32_t caller,
                         const uint32_t *argv, uint32_t argc) {
    g_seq++;
    g_st.method_calls++;
    bump(SA2_WBEM_ANS_REFUSED, +1);
    g_cur_slot = NULL;
    if (t < SA2_WBEM_MAX_TYPES && slot < SA2_WBEM_MAX_SLOTS) {
        sa2_wbem_slot_stat *st = &g_slot[t][slot];
        if (!st->used) {
            st->used = 1;
            st->first_seq = g_seq;
            st->first_caller = caller;
            st->answered = SA2_WBEM_ANS_REFUSED;
            g_st.distinct_methods++;
            g_cur_slot = st;
        }
        st->calls++;
    }
    g_cur = NULL;
    if (g_log_used >= SA2_WBEM_LOG_MAX) { g_st.log_dropped++; return; }
    sa2_wbem_call *r = &g_log[g_log_used++];
    memset(r, 0, sizeof *r);
    r->seq = g_seq;
    r->caller = caller;
    r->self = self;
    r->type = (uint16_t)t;
    r->slot = (uint16_t)slot;
    r->answered = SA2_WBEM_ANS_REFUSED;
    r->argc = (uint8_t)(argc > 8u ? 8u : argc);
    for (uint32_t k = 0; k < r->argc; k++) r->arg[k] = argv[k];
    g_cur = r;
}

static void note_settle(uint32_t ret, const wb_ctx *c) {
    bump(SA2_WBEM_ANS_REFUSED, -1);
    bump(c->answered, +1);
    if (g_cur) {
        g_cur->answered = c->answered;
        g_cur->ret = ret;
        g_cur->out0 = c->out0;
        g_cur->out1 = c->out1;
    }
    if (g_cur_slot) g_cur_slot->answered = c->answered;
    g_cur = NULL;
    g_cur_slot = NULL;
}

static void *wbem_native(void *s, uint32_t pc, void *m) {
    const uint32_t idx = (pc - SA2_WBEM_THUNK_BASE) / SA2_WBEM_THUNK_STRIDE;
    const uint32_t t = idx / SA2_WBEM_MAX_SLOTS, slot = idx % SA2_WBEM_MAX_SLOTS;
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t caller = 0;
    if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;

    if (t >= type_count()) {
        sa2_trap_note("wbem method thunk %#010x is past the %u fabricated interfaces",
                      pc, type_count());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const wb_row *row = row_of(t, slot);
    uint32_t raw[16];
    memset(raw, 0, sizeof raw);
    const uint32_t want = row ? (uint32_t)row->argc + 1u : 1u;
    for (uint32_t k = 0; k < want && k < 16u; k++) {
        if (sa2_peek(esp + 4u + 4u * k, 4, &raw[k]) != SA2_OK) {
            sa2_trap_note("%s::%s: argument %u at %#010x is outside the guest space",
                          g_types[t].name, row ? row->name : "an unnamed slot", k,
                          esp + 4u + 4u * k);
            sa2_trap_raise(SA2_TRAP_FAULT, esp + 4u + 4u * k);
        }
    }
    const uint32_t self = raw[0];
    if (!row) {
        note_reserve(t, slot, self, caller, raw + 1, 0);
        sa2_trap_note("%s vtable slot %u called from %#010x on %#010x: this runtime does not "
                      "name that slot, and popping a guessed argument count would corrupt "
                      "the caller's frame rather than answer wrongly",
                      g_types[t].name, slot, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    if (!row->fn) {
        note_reserve(t, slot, self, caller, raw + 1, row->argc);
        sa2_trap_note("%s::%s called from %#010x on %#010x: no site in the image calls it "
                      "(tools/s4/dinput_surface.py), so this runtime has no answer for it "
                      "and native's is unmeasured", g_types[t].name, row->name, caller, self);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    wb_ctx c;
    memset(&c, 0, sizeof c);
    c.s = s;
    c.pc = pc;
    c.self = self;
    c.obj = obj_of(self);
    c.a = raw + 1;
    c.caller = caller;
    c.answered = SA2_WBEM_ANS_NOOP;
    note_reserve(t, slot, self, caller, raw + 1, row->argc);
    if (!c.obj || c.obj->type != t || !c.obj->alive) {
        g_st.unknown_this++;
        sa2_trap_note("%s::%s called from %#010x on %#010x, which is no live %s this runtime "
                      "built: the caller's frame is out of step, or the object was released",
                      g_types[t].name, row->name, caller, self, g_types[t].name);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    const uint32_t ret = row->fn(&c);
    note_settle(ret, &c);
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u + 4u * want);
    return m;
}

/* --------------------------------------------------------- the build */

static uint32_t type_vtable(uint32_t t) {
    if (g_types[t].vt) return g_types[t].vt;
    if (!g_types_bound) {
        /* On the first object, never at configuration: crt_probe requires
         * rows_after == rows_before + bound across sa2_imports_config_from_env. */
        for (uint32_t i = 0; i < type_count(); i++)
            for (uint32_t k = 0; k < SA2_WBEM_MAX_SLOTS; k++) {
                const uint32_t va = SA2_WBEM_THUNK_BASE +
                                    (i * SA2_WBEM_MAX_SLOTS + k) * SA2_WBEM_THUNK_STRIDE;
                if (sa2_ft_add(va, wbem_native, "wbem-method", SA2_FN_NATIVE) < 0) {
                    sa2_trap_note("the function table refused the wbem method thunks");
                    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_WBEM_THUNK_BASE);
                }
            }
        g_types_bound = 1;
    }
    const uint32_t vt = SA2_WBEM_VT_BASE + t * SA2_WBEM_VT_STRIDE;
    for (uint32_t k = 0; k < SA2_WBEM_MAX_SLOTS; k++)
        pk32(vt + 4u * k, SA2_WBEM_THUNK_BASE + (t * SA2_WBEM_MAX_SLOTS + k) * SA2_WBEM_THUNK_STRIDE);
    g_types[t].vt = vt;
    return vt;
}

uint32_t sa2_wbem_cocreate(void *s, const uint8_t clsid[16], const uint8_t iid[16],
                           uint32_t ppv, uint32_t *handled) {
    if (handled) *handled = 0;
    if (!clsid || memcmp(clsid, CLSID_WBEMLOCATOR, 16)) return 0;
    if (handled) *handled = 1;
    /* The import dispatcher leaves ESP on the return address until the stub
     * returns (sa2_imports.c, import_native), so [ESP] is the caller and
     * pUnkOuter and dwClsContext are at [ESP+8] and [ESP+12]. */
    uint32_t caller = 0, outer = 0, context = 1u;
    if (s) {
        const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
        if (sa2_peek(esp, 4, &caller) != SA2_OK) caller = 0;
        if (sa2_peek(esp + 8u, 4, &outer) != SA2_OK) gfault(esp + 8u, "a read of pUnkOuter");
        if (sa2_peek(esp + 12u, 4, &context) != SA2_OK) gfault(esp + 12u, "a read of dwClsContext");
    }
    g_st.cocreate_calls++;
    g_st.cocreate_last_caller = caller;
    wb_ctx c;
    memset(&c, 0, sizeof c);
    c.pc = SA2_WBEM_THUNK_BASE;
    c.caller = caller;
    if (outer || context != 1u)
        refuse(&c, "ole32", "CoCreateInstance(CLSID_WbemLocator)",
               "pUnkOuter is not NULL or dwClsContext is not CLSCTX_INPROC_SERVER; the one "
               "site pushes 0 and 1 (0x008646CE, 0x008646CC)");
    if (!iid || memcmp(iid, IID_IWBEMLOCATOR, 16))
        refuse(&c, "ole32", "CoCreateInstance(CLSID_WbemLocator)",
               "riid is not IID_IWbemLocator, the 16 bytes at 0x008FFF98 the one site "
               "pushes at 0x008646C7");
    if (!ppv)
        refuse(&c, "ole32", "CoCreateInstance(CLSID_WbemLocator)",
               "ppv is NULL; the one site passes a local (0x008646C2)");
    wb_obj *o = obj_make(SA2_WBEM_SLOT_LOCATOR, T_LOC, 0, SA2_WBEM_THUNK_BASE);
    pk32(ppv, o->addr);
    return SA2_WBEM_S_NO_ERROR;
}

uint32_t sa2_wbem_proxy_blanket(const uint32_t *a, uint32_t caller) {
    g_st.blanket_calls++;
    g_st.blanket_caller = caller;
    for (uint32_t k = 0; k < 8u; k++) g_st.blanket_args[k] = a[k];
    wb_ctx c;
    memset(&c, 0, sizeof c);
    c.pc = SA2_WBEM_THUNK_BASE;
    c.caller = caller;
    const wb_obj *o = obj_of(a[0]);
    if (!o || o->type != T_SVC || !o->alive)
        refuse(&c, "ole32", "CoSetProxyBlanket",
               "pProxy is not the live IWbemServices ConnectServer returned, which the one "
               "site passes (0x0086476A, loaded at 0x00864754)");
    if (a[1] != 10u || a[2] || a[3] || a[4] != 3u || a[5] != 3u || a[6] || a[7])
        refuse(&c, "ole32", "CoSetProxyBlanket",
               "the blanket is not (10, 0, NULL, 3, 3, NULL, 0), which the one site pushes "
               "at 0x00864760..0x00864768");
    /* ole32 does three things here (native-outcomes-v1a:14851-14853): ask the
     * proxy for IClientSecurity, which wbemprox's services object answers;
     * SetBlanket, which wbemprox takes without doing anything; Release. The
     * same three are done and counted, on the same object, and nothing about
     * the object changes, because nothing natively did. The HRESULT is the one
     * the capture recorded: the game ignores it (0x00864771 reloads the
     * services pointer), and the profile passes it through whatever it was. */
    g_st.qi_client_security++;
    g_st.client_security_set_blanket++;
    g_st.client_security_release++;
    return sa2_dp_wmi_blanket_hr();
}

/* The entities a profile can hold are the ones this file answers: both profile
 * readers refuse more than SA2_DP_MAX_WMI_PNP and any DeviceID that is not
 * valid UTF-8 (runtime/sa2_devprofile.h), and the static assertions above hold
 * those limits to this file's, so the refusals live in the readers, once.
 * What stays here is cheap and on the path that would go wrong: obj_make keeps
 * the object table in bounds, and Get traps on a DeviceID it cannot convert.
 * The configuration only records the count, for the run record. */
void sa2_wbem_config_from_env(void) {
    g_st.profile_entities = sa2_dp_wmi_pnp_count();
}

/* ------------------------------------------------------------ readers */

void sa2_wbem_stats_get(sa2_wbem_stats *out) { if (out) *out = g_st; }
uint32_t             sa2_wbem_log_count(void) { return g_log_used; }
const sa2_wbem_call *sa2_wbem_log_at(uint32_t i) { return i < g_log_used ? &g_log[i] : NULL; }

uint32_t    sa2_wbem_type_count(void) { return type_count(); }
const char *sa2_wbem_type_name(uint32_t t) { return t < type_count() ? g_types[t].name : NULL; }
const char *sa2_wbem_method_name(uint32_t t, uint32_t slot) {
    const wb_row *r = row_of(t, slot);
    return r ? r->name : NULL;
}
int sa2_wbem_method_argc(uint32_t t, uint32_t slot) {
    const wb_row *r = row_of(t, slot);
    return r ? (int)r->argc : -1;
}
int sa2_wbem_method_implemented(uint32_t t, uint32_t slot) {
    const wb_row *r = row_of(t, slot);
    return r && r->fn ? 1 : 0;
}
const char *sa2_wbem_method_flags(uint32_t t, uint32_t slot) {
    const wb_row *r = row_of(t, slot);
    return r && (r->flags & DF_OUT) ? "out" : "";
}
const sa2_wbem_slot_stat *sa2_wbem_slot_stat_at(uint32_t t, uint32_t slot) {
    if (t >= SA2_WBEM_MAX_TYPES || slot >= SA2_WBEM_MAX_SLOTS) return NULL;
    return &g_slot[t][slot];
}
uint32_t sa2_wbem_object_addr(uint32_t slot) {
    return slot < SA2_WBEM_OBJ_SLOTS ? SA2_WBEM_OBJ_BASE + slot * SA2_WBEM_OBJ_STRIDE : 0u;
}
int sa2_wbem_object_alive(uint32_t slot) {
    return slot < SA2_WBEM_OBJ_SLOTS && g_obj[slot].alive;
}
