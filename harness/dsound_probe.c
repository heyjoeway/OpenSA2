/* Does the recording DirectSound device behave like a device?
 *
 *   usage: dsound_probe <sonic2app-unpacked.exe> [--json PATH]
 *
 * Why this exists rather than "the entry-point run reached it"
 * ------------------------------------------------------------
 * The entry-point run creates a primary buffer, sets a format on it and reads
 * it back -- and then stalls inside CRI's own server arrangement without ever
 * calling `Play`, `Lock` or `GetCurrentPosition` once. So the part of this
 * device most easily got subtly wrong, **the cursors**, is carried by that run
 * without being exercised by it. A claim whose falsification is invisible is
 * not yet a checked claim, and this project has now found that shape twelve
 * times.
 *
 * So this drives the device through *exactly the path the guest uses*: it
 * reads the vtable pointer out of the fabricated object in guest memory, reads
 * the thunk address out of the vtable slot, pushes `this` and the arguments on
 * a guest stack, and calls the thunk with `sa2_call`. Nothing calls a C
 * function in `runtime/sa2_dsound.c` directly. That matters for two separate
 * reasons: the answers go through the same argument marshalling a lifted
 * `call [edx+N]` does, and **the stack pointer after the call is measured**,
 * which is the one check that can see the defect class that has silently
 * corrupted a caller twice in this project (d3d9 at slice 6, Steam at slice 9).
 *
 * What it asserts
 * ---------------
 *  * every answered slot pops exactly `4 * (1 + argc) + 4` bytes -- return
 *    address, `this`, and its own arguments, and not one dword more or less;
 *  * `Lock` hands back real guest memory and what the guest writes into it is
 *    still there after an `Unlock` and a second `Lock`;
 *  * **the play cursor is `anchor_pos + elapsed_us * nAvgBytesPerSec / 1e6`,
 *    floored, rounded down to nBlockAlign**, recomputed here in the probe from
 *    the format the probe itself set, at instants chosen to separate that
 *    formula from its neighbours: one where the floor matters, one where the
 *    block alignment matters, one where a 32-bit intermediate would have
 *    wrapped, one that wraps the buffer and one that does not;
 *  * the write cursor is the play cursor plus a **fixed** lead: the same
 *    number of bytes at every instant, block-aligned, and the modulo of the
 *    buffer length;
 *  * `Play`, `Stop` and `SetCurrentPosition` re-anchor, so a cursor is
 *    elapsed-since-anchor and never elapsed-since-process-start;
 *  * a non-looping buffer halts at its end and loses DSBSTATUS_PLAYING; a
 *    looping one wraps and keeps it;
 *  * the cursor is a pure function of the virtual clock: two identical
 *    sequences at the same virtual instants give the same bytes.
 *
 * Every number is written to JSON, including a failing run's.
 *
 * `SA2_DSOUND_BREAK=argc` makes the argument-count check fail; `=frozen` makes
 * every cursor check fail; `=nolead` makes the write-cursor checks fail. That
 * is how the checks are shown able to fail rather than asserted to be.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_dsound.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_threads.h"
#include "../runtime/sa2_trap.h"

#define STACK_TOP  0x03eeff00u
#define SCRATCH    0x03004000u    /* guest scratch, clear of every fabricated block */

/* The format this probe sets, chosen so no two of the five cursor properties
 * can be confused: 44 100 Hz stereo 16-bit is 176 400 bytes/s, which is not a
 * multiple of 1 000 000, so the floor is observable; nBlockAlign is 4, so a
 * cursor that is not rounded down lands mid-sample at most instants; and
 * elapsed * 176 400 passes 2^32 after 24 seconds of virtual time. */
#define SAMPLES_PER_SEC 44100u
#define BLOCK_ALIGN     4u
#define AVG_BYTES       (SAMPLES_PER_SEC * BLOCK_ALIGN)   /* 176 400 */
#define BUF_BYTES       88200u                            /* exactly half a second */
#define WRITE_LEAD_US   15000u

static int g_fail;
static char g_first_fail[256];
static uint32_t g_checks;

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

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static void pk16(uint32_t a, uint16_t v) { sa2_poke(a, 2, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}

/* The last call's measured stack behaviour, so every call can be checked
 * without threading three out-parameters through every call site. */
static uint32_t g_last_esp_delta, g_last_status, g_last_exit;
/* The fixed write-cursor lead in bytes, at file scope because the JSON at the
 * end has to print it whichever path got there. */
static uint32_t g_lead;
static uint32_t g_argc_ok = 1, g_argc_calls;

/* Call vtable slot `slot` of the fabricated object at `self`, the way lifted
 * code does: read the vtable out of the object, read the thunk out of the
 * vtable, push `this` and the arguments, and enter through `sa2_call`. */
static uint32_t call_slot(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv) {
    const uint32_t vt = peek32(self);
    const uint32_t thunk = peek32(vt + 4u * slot);
    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    const uint32_t esp0 = STACK_TOP - 4u * (argc + 2u);
    st.gpr[SA2_ESP] = esp0;
    pk32(esp0 + 4u, self);
    for (uint32_t i = 0; i < argc; i++) pk32(esp0 + 8u + 4u * i, argv[i]);
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    sa2_call_result_t res;
    sa2_call(thunk, &st, 30, &res);
    /* A native returns by handing `memory` back rather than by writing EIP, so
     * driven straight from `sa2_call` a *successful* call comes back as
     * SA2_TRAP_CONTROL_LEFT at the thunk's own address -- the same convention
     * harness/heap_probe.c documents for import thunks. */
    g_last_status = (res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == thunk)
                    ? SA2_TRAP_NONE : res.status;
    g_last_exit = res.exit_pc;
    g_last_esp_delta = st.gpr[SA2_ESP] - esp0;
    /* The whole point of entering this way: a __stdcall COM method pops the
     * return address, `this` and its own arguments. One dword short and the
     * caller's frame drifts silently for the rest of its life. */
    g_argc_calls++;
    if (g_last_esp_delta != 4u * (argc + 2u)) {
        g_argc_ok = 0;
        check(0, "slot %u popped %u bytes, wanted %u", slot, g_last_esp_delta,
              4u * (argc + 2u));
    }
    return st.gpr[SA2_EAX];
}

#define CALL0(self, slot)                 call_slot(self, slot, 0, NULL)
#define CALL(self, slot, n, ...)          call_slot(self, slot, n, (const uint32_t[]){__VA_ARGS__})

/* The cursor, recomputed here from the format this probe set. This is a second
 * implementation of the arithmetic on purpose: a check that called the
 * runtime's own helper would agree with it whatever it computed. */
static uint32_t expect_play(uint64_t anchor_us, uint32_t anchor_pos, uint64_t now_us,
                            int looping, int *halted) {
    if (halted) *halted = 0;
    const uint64_t elapsed = now_us > anchor_us ? now_us - anchor_us : 0ull;
    const uint64_t advance = (elapsed * (uint64_t)AVG_BYTES) / 1000000ull;
    uint64_t pos = (uint64_t)anchor_pos + advance;
    if (looping) {
        pos %= (uint64_t)BUF_BYTES;
    } else if (pos >= (uint64_t)BUF_BYTES) {
        pos = BUF_BYTES - BLOCK_ALIGN;
        if (halted) *halted = 1;
    }
    return (uint32_t)(pos - (pos % BLOCK_ALIGN));
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

/* One measured cursor reading, kept so a reader can redo the arithmetic. */
#define MAX_SAMPLES 24
static struct {
    const char *label;
    uint64_t    anchor_us, now_us;
    uint32_t    anchor_pos, play, write, want_play, want_write;
    int         looping;
} g_s[MAX_SAMPLES];
static uint32_t g_ns;

int main(int argc, char **argv) {
    const char *pe = NULL, *json_path = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!pe) pe = argv[i];
    }
    if (!pe) { fprintf(stderr, "usage: dsound_probe <exe> [--json PATH]\n"); return 2; }
    /* This probe exists only to drive the device, so it turns it on rather
     * than reporting "not enabled" as a pass. The write lead is pinned here so
     * the expected numbers below are not a function of the environment. */
    setenv("SA2_DSOUND_DEVICE", "1", 1);
    {
        char b[32];
        snprintf(b, sizeof b, "%u", WRITE_LEAD_US);
        setenv("SA2_DSOUND_WRITE_LEAD_US", b, 1);
    }
    {
        const uint32_t want_lead = (uint32_t)(((uint64_t)WRITE_LEAD_US * AVG_BYTES) / 1000000ull);
        g_lead = want_lead - (want_lead % BLOCK_ALIGN);
    }
    if (sa2_runtime_init(pe, 1024) != SA2_OK) { fprintf(stderr, "runtime init failed\n"); return 2; }
    sa2_imports_config_from_env();
    if (sa2_imports_bind() <= 0) { fprintf(stderr, "imports did not bind\n"); return 2; }

    /* ---- 1. DirectSoundCreate8 through its own IAT thunk ---- */
    const sa2_import_info *dsc = sa2_import_by_name("DSOUND.dll", "Ordinal#11");
    uint32_t device = 0, create_hr = 0xffffffffu;
    if (!dsc) {
        check(0, "DSOUND.dll ordinal 11 is not an import this runtime knows");
    } else {
        sa2_cpu_state_t st;
        sa2_cpu_state_reset(&st);
        st.gpr[SA2_ESP] = STACK_TOP - 16u;
        pk32(STACK_TOP - 12u, 0);            /* pcGuidDevice */
        pk32(STACK_TOP - 8u, SCRATCH);       /* ppDS8 */
        pk32(STACK_TOP - 4u, 0);             /* pUnkOuter */
        pk32(SCRATCH, 0);
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);
        sa2_call_result_t res;
        sa2_call(dsc->thunk, &st, 30, &res);
        create_hr = st.gpr[SA2_EAX];
        device = peek32(SCRATCH);
        check(create_hr == 0, "DirectSoundCreate8 returned %#010x, wanted DS_OK", create_hr);
        check(device != 0, "DirectSoundCreate8 wrote a null out-pointer");
    }
    if (!device) {
        fprintf(stderr, "no device; nothing further can be driven\n");
        if (json_path) {
            FILE *f = fopen(json_path, "w");
            if (f) { fprintf(f, "{\"ok\": false, \"checks\": %u, \"failures\": %u, "
                                "\"first_failure\": ", g_checks, g_fail);
                     jstr(f, g_first_fail[0] ? g_first_fail : "no device");
                     fprintf(f, "}\n"); fclose(f); }
        }
        return 1;
    }

    /* ---- 2. the calls the image's own sound init makes ---- */
    check(CALL(device, 6, 2, 0x00010004u, 2u) == 0, "SetCooperativeLevel did not return DS_OK");

    /* QueryInterface for IID_IDirectSound8 -- the one CRI's SetupSound asks
     * for at 0x007C6859, with the 16 bytes the image holds at 0x0087362C. */
    static const uint8_t IID_DS8[16] = {0x93,0x7E,0x0A,0xC5,0x95,0xF3,0x34,0x48,
                                        0x9E,0xF6,0x7F,0xA9,0x9D,0xE5,0x09,0x66};
    sa2_poke(SCRATCH + 0x100u, 16, IID_DS8);
    pk32(SCRATCH + 0x120u, 0);
    check(CALL(device, 0, 2, SCRATCH + 0x100u, SCRATCH + 0x120u) == 0,
          "QueryInterface(IID_IDirectSound8) did not return S_OK");
    check(peek32(SCRATCH + 0x120u) == device,
          "QueryInterface(IID_IDirectSound8) handed back %#010x, not this object",
          peek32(SCRATCH + 0x120u));
    /* And an IID it is not: an honest E_NOINTERFACE with a null out-pointer. */
    uint8_t other[16]; memcpy(other, IID_DS8, 16); other[0] ^= 0xffu;
    sa2_poke(SCRATCH + 0x140u, 16, other);
    pk32(SCRATCH + 0x160u, 0xbadbad00u);
    const uint32_t qi_no = CALL(device, 0, 2, SCRATCH + 0x140u, SCRATCH + 0x160u);
    check(qi_no == 0x80004002u, "QueryInterface of an unknown IID returned %#010x, "
                                "wanted E_NOINTERFACE", qi_no);
    check(peek32(SCRATCH + 0x160u) == 0,
          "QueryInterface of an unknown IID left %#010x in the out-pointer",
          peek32(SCRATCH + 0x160u));

    /* ---- 3. a secondary buffer with a format of this probe's choosing ---- */
    const uint32_t wfx = SCRATCH + 0x200u, desc = SCRATCH + 0x240u, out = SCRATCH + 0x280u;
    pk16(wfx + 0u, 1);                       /* WAVE_FORMAT_PCM */
    pk16(wfx + 2u, 2);                       /* nChannels */
    pk32(wfx + 4u, SAMPLES_PER_SEC);
    pk32(wfx + 8u, AVG_BYTES);
    pk16(wfx + 12u, BLOCK_ALIGN);
    pk16(wfx + 14u, 16);
    pk16(wfx + 16u, 0);
    pk32(desc + 0u, 36);                     /* dwSize: the DX8 descriptor */
    pk32(desc + 4u, 0x00008000u);            /* DSBCAPS_GLOBALFOCUS */
    pk32(desc + 8u, BUF_BYTES);
    pk32(desc + 12u, 0);
    pk32(desc + 16u, wfx);
    pk32(out, 0);
    check(CALL(device, 3, 3, desc, out, 0u) == 0, "CreateSoundBuffer did not return DS_OK");
    const uint32_t buf = peek32(out);
    check(buf != 0, "CreateSoundBuffer wrote a null out-pointer");
    if (!buf) goto finish;

    /* DSBCAPS: dwBufferBytes has to be the guest's own number. */
    pk32(SCRATCH + 0x300u, 20);
    check(CALL(buf, 3, 1, SCRATCH + 0x300u) == 0, "IDirectSoundBuffer8::GetCaps failed");
    check(peek32(SCRATCH + 0x308u) == BUF_BYTES,
          "GetCaps reported dwBufferBytes %u, wanted %u", peek32(SCRATCH + 0x308u), BUF_BYTES);

    /* GetFormat has to hand back what the descriptor supplied. */
    pk32(SCRATCH + 0x340u, 0);
    check(CALL(buf, 5, 3, SCRATCH + 0x340u, 18u, SCRATCH + 0x380u) == 0, "GetFormat failed");
    check(peek32(SCRATCH + 0x344u) == SAMPLES_PER_SEC,
          "GetFormat reported nSamplesPerSec %u", peek32(SCRATCH + 0x344u));
    check(peek32(SCRATCH + 0x348u) == AVG_BYTES,
          "GetFormat reported nAvgBytesPerSec %u", peek32(SCRATCH + 0x348u));
    check(peek32(SCRATCH + 0x380u) == 18u, "GetFormat wrote %u bytes, wanted 18",
          peek32(SCRATCH + 0x380u));

    /* ---- 4. Lock hands back guest memory and the guest's bytes persist ---- */
    uint32_t p1 = 0, b1 = 0, p2 = 0, b2 = 0;
    check(CALL(buf, 11, 7, 0u, 4096u, SCRATCH + 0x400u, SCRATCH + 0x404u,
               SCRATCH + 0x408u, SCRATCH + 0x40Cu, 0u) == 0, "Lock failed");
    p1 = peek32(SCRATCH + 0x400u); b1 = peek32(SCRATCH + 0x404u);
    check(b1 == 4096u, "Lock handed back %u bytes, wanted 4096", b1);
    check(p1 != 0 && sa2_guest_valid(p1, b1), "Lock handed back %#010x, not guest memory", p1);
    {
        uint8_t *host = sa2_gp(p1, b1);
        check(host != NULL, "the locked range is not addressable");
        if (host) for (uint32_t i = 0; i < b1; i++) host[i] = (uint8_t)(i * 7u + 3u);
    }
    check(CALL(buf, 19, 4, p1, b1, 0u, 0u) == 0, "Unlock failed");
    /* Lock the same range again: the bytes the guest wrote are still there. */
    check(CALL(buf, 11, 7, 0u, 4096u, SCRATCH + 0x410u, SCRATCH + 0x414u,
               SCRATCH + 0x418u, SCRATCH + 0x41Cu, 0u) == 0, "the second Lock failed");
    const uint32_t p1b = peek32(SCRATCH + 0x410u);
    check(p1b == p1, "the second Lock handed back %#010x, not %#010x", p1b, p1);
    {
        int same = 1;
        const uint8_t *host = sa2_gp(p1b, b1);
        if (!host) same = 0;
        else for (uint32_t i = 0; i < b1; i++)
            if (host[i] != (uint8_t)(i * 7u + 3u)) { same = 0; break; }
        check(same, "the bytes the guest wrote into the locked range did not persist");
    }
    CALL(buf, 19, 4, p1b, b1, 0u, 0u);
    /* A wrapping lock: the second pointer is the start of the buffer. */
    check(CALL(buf, 11, 7, BUF_BYTES - 1000u, 4000u, SCRATCH + 0x420u, SCRATCH + 0x424u,
               SCRATCH + 0x428u, SCRATCH + 0x42Cu, 0u) == 0, "the wrapping Lock failed");
    p2 = peek32(SCRATCH + 0x428u); b2 = peek32(SCRATCH + 0x42Cu);
    check(peek32(SCRATCH + 0x424u) == 1000u, "the wrapping Lock's first length is %u, wanted 1000",
          peek32(SCRATCH + 0x424u));
    check(b2 == 3000u, "the wrapping Lock's second length is %u, wanted 3000", b2);
    check(p2 == p1, "the wrapping Lock's second pointer is %#010x, wanted the buffer start "
                    "%#010x", p2, p1);
    CALL(buf, 19, 4, peek32(SCRATCH + 0x420u), 1000u, p2, b2);

    /* ---- 5. the cursors ---- */

    /* Before Play: a stopped buffer's cursors sit at zero and do not move. */
    uint64_t t0 = sa2_virtual_us();
    sa2_virtual_advance_to_us(t0 + 250000ull);
    pk32(SCRATCH + 0x500u, 0xffffffffu); pk32(SCRATCH + 0x504u, 0xffffffffu);
    check(CALL(buf, 4, 2, SCRATCH + 0x500u, SCRATCH + 0x504u) == 0, "GetCurrentPosition failed");
    check(peek32(SCRATCH + 0x500u) == 0,
          "a buffer that has never played reports play cursor %u", peek32(SCRATCH + 0x500u));
    check(peek32(SCRATCH + 0x504u) == 0,
          "a buffer that has never played reports write cursor %u", peek32(SCRATCH + 0x504u));

    /* Play, looping. The anchor is *now*, which is what makes every reading
     * below elapsed-since-anchor rather than elapsed-since-process-start: the
     * clock already stands at t0 + 250 000 and the first reading is 0. */
    const uint64_t anchor = sa2_virtual_us();
    check(CALL(buf, 12, 3, 0u, 0u, 1u) == 0, "Play(DSBPLAY_LOOPING) failed");
    pk32(SCRATCH + 0x510u, 7);
    check(CALL(buf, 9, 1, SCRATCH + 0x510u) == 0, "GetStatus failed");
    check(peek32(SCRATCH + 0x510u) == (1u | 4u),
          "GetStatus after Play(LOOPING) is %#x, wanted DSBSTATUS_PLAYING|LOOPING",
          peek32(SCRATCH + 0x510u));

    /* Five instants, each separating the formula from a neighbour it could be
     * confused with:
     *   5 us       floor: 5 * 176400 / 1e6 = 0 (a rate that rounded up would give 1)
     *   17 us      alignment: 2 bytes before rounding, 0 after
     *   1000 us    176.4 -> 176 -> 176 (aligned already)
     *   1234567 us 217 776.8... -> 217 776 -> wraps once: a 32-bit product would
     *              still fit here, and the next one would not
     *   29876543 us the product is 5 270 222 185 200, about 1 227 times 2^32,
     *              so a 32-bit intermediate is not wrong by a little; the
     *              expected byte is 66 420, which is neither 0 nor the value a
     *              frozen cursor gives, so this sample discriminates on its own
     */
    static const uint64_t AT[] = {5ull, 17ull, 1000ull, 1234567ull, 29876543ull};
    static const char *AT_LABEL[] = {"floor", "align", "plain", "wrap", "sixty-four-bit"};
    for (uint32_t i = 0; i < sizeof AT / sizeof AT[0]; i++) {
        sa2_virtual_advance_to_us(anchor + AT[i]);
        const uint64_t now = sa2_virtual_us();
        pk32(SCRATCH + 0x520u, 0xffffffffu); pk32(SCRATCH + 0x524u, 0xffffffffu);
        CALL(buf, 4, 2, SCRATCH + 0x520u, SCRATCH + 0x524u);
        const uint32_t play = peek32(SCRATCH + 0x520u), wr = peek32(SCRATCH + 0x524u);
        const uint32_t want = expect_play(anchor, 0, now, 1, NULL);
        uint32_t want_w = want + g_lead;
        if (want_w >= BUF_BYTES) want_w -= BUF_BYTES;
        want_w -= want_w % BLOCK_ALIGN;
        if (g_ns < MAX_SAMPLES) {
            g_s[g_ns].label = AT_LABEL[i];
            g_s[g_ns].anchor_us = anchor; g_s[g_ns].anchor_pos = 0;
            g_s[g_ns].now_us = now; g_s[g_ns].play = play; g_s[g_ns].write = wr;
            g_s[g_ns].want_play = want; g_s[g_ns].want_write = want_w;
            g_s[g_ns].looping = 1;
            g_ns++;
        }
        check(play == want, "%s: play cursor %u at +%llu us, wanted %u",
              AT_LABEL[i], play, (unsigned long long)AT[i], want);
        check(wr == want_w, "%s: write cursor %u at +%llu us, wanted %u",
              AT_LABEL[i], wr, (unsigned long long)AT[i], want_w);
        check(play % BLOCK_ALIGN == 0, "%s: play cursor %u is mid-sample", AT_LABEL[i], play);
        check(wr % BLOCK_ALIGN == 0, "%s: write cursor %u is mid-sample", AT_LABEL[i], wr);
        check(play < BUF_BYTES, "%s: play cursor %u is past the buffer", AT_LABEL[i], play);
    }
    /* The lead is fixed: the same number of bytes at every one of those
     * instants, not a value re-derived per call. */
    {
        int fixed = 1;
        for (uint32_t i = 0; i < g_ns; i++) {
            uint32_t d = g_s[i].write >= g_s[i].play ? g_s[i].write - g_s[i].play
                                                     : g_s[i].write + BUF_BYTES - g_s[i].play;
            if (d != g_lead) fixed = 0;
        }
        check(fixed, "the write-cursor lead is not the same at every instant");
    }

    /* ---- 6. Stop re-anchors, and a stopped cursor does not move ---- */
    check(CALL0(buf, 18) == 0, "Stop failed");
    pk32(SCRATCH + 0x530u, 0xffffffffu);
    CALL(buf, 4, 2, SCRATCH + 0x530u, 0u);
    const uint32_t at_stop = peek32(SCRATCH + 0x530u);
    sa2_virtual_advance_to_us(sa2_virtual_us() + 5000000ull);
    pk32(SCRATCH + 0x534u, 0xffffffffu);
    CALL(buf, 4, 2, SCRATCH + 0x534u, 0u);
    check(peek32(SCRATCH + 0x534u) == at_stop,
          "a stopped buffer's play cursor moved from %u to %u over five virtual seconds",
          at_stop, peek32(SCRATCH + 0x534u));
    pk32(SCRATCH + 0x538u, 7);
    CALL(buf, 9, 1, SCRATCH + 0x538u);
    check(peek32(SCRATCH + 0x538u) == 0, "GetStatus after Stop is %#x, wanted 0",
          peek32(SCRATCH + 0x538u));

    /* ---- 7. SetCurrentPosition re-anchors ---- */
    check(CALL(buf, 13, 1, 12345u) == 0, "SetCurrentPosition failed");
    const uint64_t anchor2 = sa2_virtual_us();
    check(CALL(buf, 12, 3, 0u, 0u, 1u) == 0, "the second Play failed");
    sa2_virtual_advance_to_us(anchor2 + 100000ull);
    pk32(SCRATCH + 0x540u, 0xffffffffu);
    CALL(buf, 4, 2, SCRATCH + 0x540u, 0u);
    {
        const uint32_t got = peek32(SCRATCH + 0x540u);
        const uint32_t want = expect_play(anchor2, 12344u /* 12345 rounded down */,
                                          sa2_virtual_us(), 1, NULL);
        if (g_ns < MAX_SAMPLES) {
            g_s[g_ns].label = "after-setposition";
            g_s[g_ns].anchor_us = anchor2; g_s[g_ns].anchor_pos = 12344u;
            g_s[g_ns].now_us = sa2_virtual_us(); g_s[g_ns].play = got;
            g_s[g_ns].write = 0; g_s[g_ns].want_play = want; g_s[g_ns].want_write = 0;
            g_s[g_ns].looping = 1; g_ns++;
        }
        check(got == want, "after SetCurrentPosition(12345) and 100 000 us the cursor is %u, "
                           "wanted %u", got, want);
    }

    /* ---- 8. a non-looping buffer halts at its end ---- */
    CALL0(buf, 18);
    check(CALL(buf, 13, 1, 0u) == 0, "SetCurrentPosition(0) failed");
    const uint64_t anchor3 = sa2_virtual_us();
    check(CALL(buf, 12, 3, 0u, 0u, 0u) == 0, "Play(no looping) failed");
    /* Half the buffer: still playing, still short of the end. */
    sa2_virtual_advance_to_us(anchor3 + 250000ull);
    pk32(SCRATCH + 0x550u, 0xffffffffu);
    CALL(buf, 4, 2, SCRATCH + 0x550u, 0u);
    check(peek32(SCRATCH + 0x550u) == expect_play(anchor3, 0, sa2_virtual_us(), 0, NULL),
          "a non-looping buffer at half its length reports %u", peek32(SCRATCH + 0x550u));
    pk32(SCRATCH + 0x554u, 0);
    CALL(buf, 9, 1, SCRATCH + 0x554u);
    check((peek32(SCRATCH + 0x554u) & 1u) != 0,
          "a non-looping buffer halfway through is not reporting DSBSTATUS_PLAYING");
    /* Past the end: halted at the last whole block, and no longer playing. */
    sa2_virtual_advance_to_us(anchor3 + 900000ull);
    pk32(SCRATCH + 0x558u, 0xffffffffu); pk32(SCRATCH + 0x55Cu, 0xffffffffu);
    CALL(buf, 4, 2, SCRATCH + 0x558u, SCRATCH + 0x55Cu);
    check(peek32(SCRATCH + 0x558u) == BUF_BYTES - BLOCK_ALIGN,
          "a non-looping buffer past its end reports %u, wanted %u",
          peek32(SCRATCH + 0x558u), BUF_BYTES - BLOCK_ALIGN);
    pk32(SCRATCH + 0x560u, 7);
    CALL(buf, 9, 1, SCRATCH + 0x560u);
    check(peek32(SCRATCH + 0x560u) == 0,
          "a non-looping buffer past its end still reports status %#x",
          peek32(SCRATCH + 0x560u));
    /* And it does not keep going. */
    sa2_virtual_advance_to_us(sa2_virtual_us() + 10000000ull);
    pk32(SCRATCH + 0x564u, 0xffffffffu);
    CALL(buf, 4, 2, SCRATCH + 0x564u, 0u);
    check(peek32(SCRATCH + 0x564u) == BUF_BYTES - BLOCK_ALIGN,
          "a halted buffer moved to %u ten virtual seconds later", peek32(SCRATCH + 0x564u));

    /* ---- 9. a slot with no handler refuses by name rather than answering ---- */
    {
        sa2_cpu_state_t st;
        sa2_cpu_state_reset(&st);
        const uint32_t vt = peek32(buf), thunk = peek32(vt + 4u * 23u);  /* GetObjectInPath */
        const uint32_t esp0 = STACK_TOP - 4u * 6u;
        st.gpr[SA2_ESP] = esp0;
        pk32(esp0 + 4u, buf);
        for (uint32_t i = 0; i < 4; i++) pk32(esp0 + 8u + 4u * i, 0);
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);
        sa2_call_result_t res;
        sa2_call(thunk, &st, 30, &res);
        check(res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == thunk
              && strstr(res.note, "GetObjectInPath") != NULL,
              "GetObjectInPath did not refuse by name: status %u note %s", res.status, res.note);
    }

    /* ---- 10. the runtime's own backwards-cursor detector fired at nothing ----
     *
     * This sequence contains three legitimate wraps and no backwards step, so
     * the counter has to read zero. It read 1 before the detector's wrap test
     * was replaced with the exact arithmetic, which is the only reason anybody
     * knows the first version of that test was wrong. */
    {
        sa2_ds_stats st;
        sa2_dsound_stats_get(&st);
        check(st.cursor_went_backwards == 0,
              "the runtime counted %u backwards cursor reads in a sequence that has none",
              st.cursor_went_backwards);
        check(st.cursor_wraps > 0, "the sequence contains no wrap, so the wrap path is "
                                   "carried by this probe without being exercised by it");
    }

finish:;
    sa2_ds_stats ds;
    sa2_dsound_stats_get(&ds);
    const int ok = g_fail == 0;
    if (json_path) {
        FILE *f = fopen(json_path, "w");
        if (f) {
            fprintf(f, "{\n \"ok\": %s, \"checks\": %u, \"failures\": %u,\n",
                    ok ? "true" : "false", g_checks, g_fail);
            fprintf(f, " \"first_failure\": ");
            jstr(f, g_first_fail[0] ? g_first_fail : "");
            fprintf(f, ",\n \"break\": ");
            jstr(f, getenv("SA2_DSOUND_BREAK") ? getenv("SA2_DSOUND_BREAK") : "");
            fprintf(f, ",\n \"argument_counts\": {\"calls\": %u, \"every_slot_popped_exactly_"
                       "this_plus_its_own_arguments\": %s},\n",
                    g_argc_calls, g_argc_ok ? "true" : "false");
            fprintf(f, " \"format\": {\"samples_per_sec\": %u, \"block_align\": %u, "
                       "\"avg_bytes_per_sec\": %u, \"buffer_bytes\": %u, "
                       "\"write_lead_us\": %u, \"write_lead_bytes\": %u},\n",
                    SAMPLES_PER_SEC, BLOCK_ALIGN, AVG_BYTES, BUF_BYTES,
                    WRITE_LEAD_US, g_lead);
            fprintf(f, " \"cursor_samples\": [");
            for (uint32_t i = 0; i < g_ns; i++) {
                fprintf(f, "%s\n  {\"at\": ", i ? "," : "");
                jstr(f, g_s[i].label);
                fprintf(f, ", \"anchor_us\": %llu, \"anchor_pos\": %u, \"now_us\": %llu, "
                           "\"elapsed_us\": %llu, \"looping\": %s,\n"
                           "   \"play\": %u, \"expected_play\": %u, \"write\": %u, "
                           "\"expected_write\": %u}",
                        (unsigned long long)g_s[i].anchor_us, g_s[i].anchor_pos,
                        (unsigned long long)g_s[i].now_us,
                        (unsigned long long)(g_s[i].now_us - g_s[i].anchor_us),
                        g_s[i].looping ? "true" : "false",
                        g_s[i].play, g_s[i].want_play, g_s[i].write, g_s[i].want_write);
            }
            fprintf(f, "],\n \"device\": {\"method_calls\": %u, \"distinct_methods\": %u, "
                       "\"buffers\": %u, \"guest_bytes_allocated\": %u, "
                       "\"clock_derived_answers\": %u, \"cursor_reads\": %u, "
                       "\"cursor_wraps\": %u, \"cursor_went_backwards\": %u}\n}\n",
                    ds.method_calls, ds.distinct_methods, ds.buffers,
                    ds.guest_bytes_allocated, ds.clock, ds.cursor_reads, ds.cursor_wraps,
                    ds.cursor_went_backwards);
            fclose(f);
        }
    }
    printf("dsound_probe: %u checks, %u failures%s%s\n", g_checks, g_fail,
           g_fail ? " -- first: " : "", g_fail ? g_first_fail : "");
    return ok ? 0 : 1;
}
