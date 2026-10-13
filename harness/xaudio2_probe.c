/* Does the recording XAudio2 engine behave like the one the game got?
 *
 *   usage: xaudio2_probe <sonic2app-unpacked.exe> --profile6 PATH [--json PATH]
 *
 * PATH is a device profile in the grammar of runtime/sa2_devprofile.h that
 * differs from the default only in its mastering voice: `xaudio2_master
 * voice=1 flags=0x00000002 channels=6 rate=44100`. Each of the three details
 * differs from the default's (0, 2, 48000), so each can match only if it comes
 * from the profile. The probe's run command writes it with the grammar's own
 * writer (tools/s4/device_profile.py --default) and one substitution, so it
 * carries every record the grammar requires, whatever the grammar's version
 * holds:
 *
 *   .venv/bin/python tools/s4/device_profile.py --default | sed 's/^xaudio2_master
 *     voice=1 flags=0x00000000 channels=2 rate=48000$/xaudio2_master voice=1
 *     flags=0x00000002 channels=6 rate=44100/' > private/generated/s4/xaudio2/profile-6ch.txt
 *
 * (one line; it is wrapped here). A profile with the default's flags or rate
 * is refused before any case runs.
 *
 * Why this exists rather than "the lifted boot reached it"
 * -------------------------------------------------------
 * A boot with the device on creates the engine, one mastering and one source
 * voice, and then runs the streaming thread -- whose every decision rests on
 * one number, GetState's BuffersQueued, and on that number moving at the rate
 * a 48 kHz voice consumes 512-frame buffers. A boot shows the thread ran; it
 * does not show the number was right at any instant. So this drives the
 * device the way harness/dsound_probe.c drives DirectSound: through the
 * fabricated object's own vtable in guest memory, with `this` and the
 * arguments pushed on a guest stack and the thunk entered with `sa2_call`,
 * and **the stack pointer after every call measured**. Nothing calls a C
 * function in runtime/sa2_xaudio2.c directly, and the engine itself comes
 * from ole32!CoCreateInstance's own import thunk, with the class and interface
 * GUIDs the image holds at 0x00902200 and 0x00902210.
 *
 * What it asserts
 * ---------------
 *  * every answered slot pops exactly 4 * (1 + argc) + 4 bytes;
 *  * the creation calls answer as native's did (native-outcomes-v1a/
 *    native-run.log:15544, :15546-15548, :15553, :15556-15558), and the
 *    mastering voice's details are exactly 12 bytes: the dword after them is
 *    untouched;
 *  * those details are the device profile's: the default profile's stereo
 *    master at 48000 Hz with flags 0, and the six-channel profile's flags,
 *    channels and rate under that profile;
 *  * the streaming thread's own loop -- GetState, top up to four buffers,
 *    10 ms -- run for the 1223 polls native made, sees at every poll the
 *    BuffersQueued and SamplesPlayed that a second implementation of
 *    `elapsed_us * 48000 / 1e6` frames, floored, predicts; and its first three
 *    answers are native's first three (1, 4, 3), inferred from the trace as 4
 *    minus the SubmitSourceBuffer lines that follow each GetState;
 *  * a stopped voice consumes nothing, a voice with nothing queued banks no
 *    time, and a submit to a voice that has run dry starts playing then;
 *  * SetFrequencyRatio scales the rate and is clamped as FAudio clamps it;
 *  * FlushSourceBuffers keeps the playing buffer only while the voice runs;
 *  * a slot with no handler, an argument the image never passes (the three
 *    addresses it pushes among them) and a call on a destroyed voice each
 *    refuse by name, and a refused CoCreateInstance names the import's own
 *    thunk;
 *  * configuration refuses a capture's profile with the device off or with
 *    the `refuse` break, naming the profile, and accepts the default profile
 *    with the device off. The refusing cases run sa2_imports_config_from_env
 *    and nothing else, as a lifted run does, so they fail if that function
 *    configures this device before the profile or not at all;
 *  * a capture's profile loaded after configuration turned the device off is
 *    still refused, at call time: CoCreateInstance traps at the import's own
 *    thunk, names the profile, pops nothing and leaves *ppv alone.
 *
 * Every BuffersQueued and SamplesPlayed it expects comes from the model, never
 * from an earlier reading of the device, so no expectation can be the
 * observation it is compared with. The one exception is the flush check in
 * section 8, which compares SamplesPlayed after a flush with the reading just
 * before it: what it asserts is that a flush does not move the position, a
 * relation between two readings rather than a value.
 *
 * `SA2_XAUDIO2_BREAK=argc` makes the argument-count check fail at GetState;
 * `=noconsume` and `=instant` make the polling checks fail from opposite
 * sides; `=refuse` leaves no engine to drive. That is how the checks are shown
 * able to fail rather than asserted to be.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_devprofile.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_threads.h"
#include "../runtime/sa2_trap.h"
#include "../runtime/sa2_xaudio2.h"

#define STACK_TOP  0x03eeff00u
#define SCRATCH    0x03004000u    /* guest scratch, clear of every fabricated block */
#define AUDIO      (SCRATCH + 0x10000u)

/* The image's own constants: the class, the interface and the downmix matrix
 * it passes, read out of the loaded image rather than copied here, and the two
 * globals it hands the engine: the master's pointer, pushed at 00784202, and
 * the details kept beside it, pushed at 00784217. The device requires all
 * five literally. */
#define IMAGE_CLSID      0x00902200u
#define IMAGE_IID        0x00902210u
#define IMAGE_MATRIX     0x0089E4A8u
#define IMAGE_PP_MASTER  0x01AF0EDCu
#define IMAGE_DETAILS    0x01AF0EE0u

#define RATE          48000u
#define FRAME_BYTES   24u         /* six channels of 32-bit float */
#define BUF_FRAMES    512u        /* 0x200, the frames 0x007845C0 fills per buffer */
#define BUF_BYTES     (BUF_FRAMES * FRAME_BYTES)
#define POLL_US       10000u      /* the thread's Sleep(10) */
#define NATIVE_POLLS  1223u       /* 01d0 GetState lines in native-run.log */

#define F32_ONE   0x3F800000u
#define F32_TWO   0x40000000u
#define F32_FOUR  0x40800000u

static int g_fail;
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

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static void pk16(uint32_t a, uint16_t v) { sa2_poke(a, 2, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}

static uint32_t g_argc_ok = 1, g_argc_calls;
static uint32_t g_last_status;

/* Call vtable slot `slot` of the fabricated object at `self` the way lifted
 * code does, and measure the pop. */
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
    /* A native returns by handing `memory` back, so a successful call comes
     * back as SA2_TRAP_CONTROL_LEFT at the thunk's own address. */
    g_last_status = (res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == thunk)
                    ? SA2_TRAP_NONE : res.status;
    check(g_last_status == SA2_TRAP_NONE, "slot %u of %#010x trapped: %s", slot, self, res.note);
    const uint32_t delta = st.gpr[SA2_ESP] - esp0;
    g_argc_calls++;
    if (delta != 4u * (argc + 2u)) {
        g_argc_ok = 0;
        check(0, "slot %u popped %u bytes, wanted %u", slot, delta, 4u * (argc + 2u));
    }
    return st.gpr[SA2_EAX];
}

#define CALL0(self, slot)         call_slot(self, slot, 0, NULL)
#define CALL(self, slot, n, ...)  call_slot(self, slot, n, (const uint32_t[]){__VA_ARGS__})

/* A call that must refuse by name: it traps inside the device and never
 * pops, so there is no ESP to measure, only the note. A native that returned
 * would come back at the same exit_pc; the note and the unmoved ESP are what
 * tell a refusal from an answer. */
static void expect_refusal(const char *label, uint32_t self, uint32_t slot, uint32_t argc,
                           const uint32_t *argv, const char *must_name) {
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
    check(res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == thunk
          && strstr(res.note, must_name) != NULL && st.gpr[SA2_ESP] == esp0,
          "%s did not refuse by name: status %u, note %s", label, res.status, res.note);
}

/* ole32!CoCreateInstance through its own IAT thunk, pushed as 0x00784103
 * pushes it (the image's class and interface, no outer object, `context`,
 * and a local for the engine, here SCRATCH, preset to a sentinel). */
static uint32_t cocreate(const sa2_import_info *cci, uint32_t context, sa2_call_result_t *res,
                         uint32_t *popped) {
    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    const uint32_t esp0 = STACK_TOP - 24u;
    st.gpr[SA2_ESP] = esp0;
    pk32(esp0 + 4u, IMAGE_CLSID);        /* rclsid */
    pk32(esp0 + 8u, 0u);                 /* pUnkOuter */
    pk32(esp0 + 12u, context);           /* dwClsContext */
    pk32(esp0 + 16u, IMAGE_IID);         /* riid */
    pk32(esp0 + 20u, SCRATCH);           /* ppv */
    pk32(SCRATCH, 0xBADBAD00u);
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    sa2_call(cci->thunk, &st, 30, res);
    *popped = st.gpr[SA2_ESP] - esp0;
    return st.gpr[SA2_EAX];
}

/* The independent model: frames a 48 kHz voice has consumed `elapsed_us`
 * after its anchor at ratio num/den, floored, in plain 64-bit arithmetic. The
 * runtime forms the product in 128 bits from the float's mantissa; this is a
 * second implementation on purpose. */
static uint64_t frames_in(uint64_t elapsed_us, uint64_t num, uint64_t den) {
    return (elapsed_us * (uint64_t)RATE * num) / (1000000ull * den);
}

/* BuffersQueued in the model. The buffers lie end to end in stream frames from
 * 0, BUF_FRAMES each, and a voice that runs dry waits at the end of the last
 * one, so at stream position `pos` the buffers still queued are the ones that
 * end past it. */
static uint32_t queued_at(uint32_t submitted, uint64_t pos) {
    const uint64_t done = pos / BUF_FRAMES;
    return done >= submitted ? 0u : submitted - (uint32_t)done;
}

static uint32_t g_state_calls;
static void get_state(uint32_t voice, uint32_t *queued, uint64_t *samples, uint32_t *context) {
    const uint32_t p = SCRATCH + 0x200u;
    for (uint32_t k = 0; k < 5u; k++) pk32(p + 4u * k, 0xA5A5A5A5u);
    CALL(voice, 25, 1, p);
    g_state_calls++;
    if (context) *context = peek32(p);
    if (queued) *queued = peek32(p + 4u);
    if (samples) *samples = (uint64_t)peek32(p + 8u) | ((uint64_t)peek32(p + 12u) << 32);
    check(peek32(p + 16u) == 0xA5A5A5A5u,
          "GetState wrote past the 16 bytes of XAUDIO2_VOICE_STATE");
}

static uint32_t g_submits, g_ring;
static uint32_t submit(uint32_t voice) {
    const uint32_t b = SCRATCH + 0x300u;
    pk32(b + 0u, 0u);                                   /* Flags */
    pk32(b + 4u, BUF_BYTES);                            /* AudioBytes */
    pk32(b + 8u, AUDIO + (g_ring++ % 4u) * BUF_BYTES);  /* pAudioData: a ring of four, as the image's */
    for (uint32_t k = 3; k < 9u; k++) pk32(b + 4u * k, 0u);
    g_submits++;
    return CALL(voice, 21, 2, b, 0u);
}

/* Creates the mastering voice as 0x007841EB does, reads its details back into
 * the image's own 0x01AF0EE0 as 0x0078420E does, and returns the voice. The
 * three dwords land in `details`; the fourth dword is checked untouched. */
static uint32_t master_and_details(uint32_t engine, uint32_t details[3]) {
    pk32(IMAGE_PP_MASTER, 0u);
    check(CALL(engine, 10, 6, IMAGE_PP_MASTER, 0u, RATE, 0u, 0u, 0u) == 0u,
          "CreateMasteringVoice did not return S_OK");
    const uint32_t master = peek32(IMAGE_PP_MASTER);
    check(master != 0u, "CreateMasteringVoice wrote a null voice");
    if (!master) return 0;
    for (uint32_t k = 0; k < 3u; k++) pk32(IMAGE_DETAILS + 4u * k, 0xFFFFFFFFu);
    pk32(IMAGE_DETAILS + 12u, 0xA5A5A5A5u);
    CALL(master, 0, 1, IMAGE_DETAILS);
    for (uint32_t k = 0; k < 3u; k++) details[k] = peek32(IMAGE_DETAILS + 4u * k);
    check(peek32(IMAGE_DETAILS + 12u) == 0xA5A5A5A5u,
          "GetVoiceDetails wrote a fourth dword: 2.6's structure is 12 bytes");
    return master;
}

/* Runs `body(arg)` in a child process with its stderr piped back. A
 * configuration refusal ends the process (sa2_trap_raise outside any sa2_call
 * prints the note and exits 3, runtime/sa2_trap.c), so only a child can show
 * one, and a child keeps a case's profile and switches out of this process.
 * Returns the exit status, with what the child wrote on stderr in `err`. */
static int in_child(void (*body)(const void *), const void *arg, char *err, size_t cap) {
    int fd[2];
    err[0] = 0;
    if (pipe(fd)) return -1;
    fflush(NULL);
    const pid_t pid = fork();
    if (pid < 0) { close(fd[0]); close(fd[1]); return -1; }
    if (pid == 0) {
        dup2(fd[1], 2);
        close(fd[0]);
        close(fd[1]);
        body(arg);
        fflush(NULL);
        _exit(0);
    }
    close(fd[1]);
    size_t n = 0;
    ssize_t got;
    while (n + 1 < cap && (got = read(fd[0], err + n, cap - 1 - n)) > 0) n += (size_t)got;
    err[n] = 0;
    close(fd[0]);
    int status = 0;
    if (waitpid(pid, &status, 0) != pid) return -1;
    return WIFEXITED(status) ? WEXITSTATUS(status)
                             : 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
}

/* Configuration with `device` as SA2_XAUDIO2_DEVICE, `brk` as
 * SA2_XAUDIO2_BREAK and `profile` as SA2_DEVICE_PROFILE (NULL: unset).
 *
 * A case that must refuse runs sa2_imports_config_from_env and nothing else,
 * as a lifted run does (harness/crt_probe.c), and a return from it is a
 * failure: a configuration that ran this module before the profile, or left it
 * out, would accept the capture, where calling the modules here in the right
 * order would hide that. Every other device module refuses a capture's
 * profile with its own device off, so those are on and their breaks cleared,
 * and SA2_XINPUT_PAD and SA2_GUEST_CPUS are cleared because the runtime
 * refuses either beside a capture's profile: the one refusal left to meet is
 * this module's. A case that must be accepted configures the profile and this
 * module only, since what it shows is that this module accepts. */
typedef struct { const char *device, *brk, *profile; int must_refuse; } config_env;

static void config_body(const void *arg) {
    const config_env *c = arg;
    setenv("SA2_XAUDIO2_DEVICE", c->device, 1);
    if (c->brk) setenv("SA2_XAUDIO2_BREAK", c->brk, 1); else unsetenv("SA2_XAUDIO2_BREAK");
    if (c->profile) setenv(SA2_DP_ENV, c->profile, 1); else unsetenv(SA2_DP_ENV);
    if (!c->must_refuse) {
        sa2_devprofile_config_from_env();
        sa2_xaudio2_config_from_env();
        return;
    }
    setenv("SA2_DINPUT_DEVICE", "1", 1);
    setenv("SA2_DDRAW_DEVICE", "1", 1);
    unsetenv("SA2_DINPUT_BREAK");
    unsetenv("SA2_DDRAW_BREAK");
    unsetenv("SA2_XINPUT_PAD");
    unsetenv("SA2_GUEST_CPUS");
    sa2_imports_config_from_env();
    fprintf(stderr, "sa2_imports_config_from_env returned: it accepted a configuration it "
                    "must refuse\n");
    fflush(NULL);
    _exit(1);
}

static int config_in_child(const char *device, const char *brk, const char *profile,
                           int must_refuse, char *err, size_t cap) {
    const config_env c = {device, brk, profile, must_refuse};
    return in_child(config_body, &c, err, cap);
}

/* The call-time backstop: the device configured off under the default
 * profile, a capture's profile loaded after that, and then the class asked
 * for. The child reports what the call did on its stderr, one line of
 * numbers and then the trap note. */
typedef struct { const sa2_import_info *cci; const char *profile; } backstop_env;

static void backstop_body(const void *arg) {
    const backstop_env *b = arg;
    setenv("SA2_XAUDIO2_DEVICE", "0", 1);
    unsetenv("SA2_XAUDIO2_BREAK");
    unsetenv(SA2_DP_ENV);
    sa2_devprofile_config_from_env();
    sa2_xaudio2_config_from_env();
    setenv(SA2_DP_ENV, b->profile, 1);
    sa2_devprofile_config_from_env();
    sa2_call_result_t res;
    uint32_t popped = 0;
    (void)cocreate(b->cci, 1u, &res, &popped);
    fprintf(stderr, "backstop: %u %u %u %u %d\n%s", (unsigned)res.status, res.exit_pc, popped,
            peek32(SCRATCH), sa2_devprofile_is_default(), res.note);
}

static struct {
    int      ran, exit_code, loaded;
    uint32_t status, exit_pc, thunk, popped, ppv;
    char     note[512];
} g_backstop;

static void backstop_case(const sa2_import_info *cci, const char *profile) {
    char err[1024];
    const backstop_env b = {cci, profile};
    g_backstop.exit_code = in_child(backstop_body, &b, err, sizeof err);
    g_backstop.thunk = cci->thunk;
    unsigned st = 0, pc = 0, pop = 0, ppv = 0;
    int is_default = 1, off = 0;
    const char *line = strstr(err, "backstop: ");
    g_backstop.ran = g_backstop.exit_code == 0 && line
                     && sscanf(line, "backstop: %u %u %u %u %d%n", &st, &pc, &pop, &ppv,
                               &is_default, &off) == 5;
    g_backstop.status = st;
    g_backstop.exit_pc = pc;
    g_backstop.popped = pop;
    g_backstop.ppv = ppv;
    g_backstop.loaded = !is_default;
    snprintf(g_backstop.note, sizeof g_backstop.note, "%s",
             g_backstop.ran ? line + off + (line[off] == '\n') : err);
    check(g_backstop.ran && g_backstop.loaded && st == SA2_TRAP_CONTROL_LEFT && pc == cci->thunk
          && pop == 0u && ppv == 0xBADBAD00u
          && strstr(g_backstop.note, "xaudio2 CoCreateInstance") != NULL
          && strstr(g_backstop.note, profile) != NULL,
          "CoCreateInstance with the device configured off and %s loaded afterwards did not "
          "refuse at the import's thunk %#010x naming the profile: child exit %d, status %u at "
          "%#010x, popped %u, ppv %#010x, note %s", profile, cci->thunk, g_backstop.exit_code, st,
          pc, pop, ppv, g_backstop.note);
}

/* The default configuration's answer: the device off under the default
 * profile, the path every reach run and the boot replay take. The refusal
 * moved from imp_CoCreateInstance into sa2_xaudio2_cocreate when this device
 * was integrated, and the guest's failure branch at 0078410B never reads
 * *ppv, so no replay would see a regression here: REGDB_E_CLASSNOTREG, the
 * five arguments popped, a NULL written through ppv, and the refusal marker
 * counted exactly once on the import (the P3 integration review, 2026-09-23). */
static struct {
    int      ran, exit_code;
    uint32_t status, exit_pc, hr, popped, ppv, marked, refused;
} g_off;

static void off_body(const void *arg) {
    const sa2_import_info *cci = arg;
    setenv("SA2_XAUDIO2_DEVICE", "0", 1);
    unsetenv("SA2_XAUDIO2_BREAK");
    unsetenv(SA2_DP_ENV);
    sa2_devprofile_config_from_env();
    sa2_xaudio2_config_from_env();
    sa2_xa2_stats before, after;
    sa2_xaudio2_stats_get(&before);
    const uint32_t marked0 = cci->refused_calls;
    sa2_call_result_t res;
    uint32_t popped = 0;
    const uint32_t hr = cocreate(cci, 1u, &res, &popped);
    sa2_xaudio2_stats_get(&after);
    fprintf(stderr, "off: %u %u %u %u %u %u %u\n", (unsigned)res.status, res.exit_pc, hr,
            popped, peek32(SCRATCH), cci->refused_calls - marked0,
            after.cocreate_refused - before.cocreate_refused);
}

static void off_case(const sa2_import_info *cci) {
    char err[1024];
    g_off.exit_code = in_child(off_body, cci, err, sizeof err);
    const char *line = strstr(err, "off: ");
    unsigned st = 0, pc = 0, hr = 0, pop = 0, ppv = 0, marked = 0, refused = 0;
    g_off.ran = g_off.exit_code == 0 && line
                && sscanf(line, "off: %u %u %u %u %u %u %u", &st, &pc, &hr, &pop, &ppv, &marked,
                          &refused) == 7;
    g_off.status = st; g_off.exit_pc = pc; g_off.hr = hr; g_off.popped = pop;
    g_off.ppv = ppv; g_off.marked = marked; g_off.refused = refused;
    /* Entering an import's thunk with sa2_call ends with control leaving at the
     * thunk whether the stub returned or trapped (call_slot reads it the same
     * way); the pop is what tells them apart, a trap popping nothing. */
    check(g_off.ran && (st == SA2_TRAP_NONE || (st == SA2_TRAP_CONTROL_LEFT && pc == cci->thunk))
          && hr == 0x80040154u && pop == 24u && ppv == 0u
          && marked == 1u && refused == 1u,
          "CoCreateInstance with the device off under the default profile did not refuse as "
          "the default configuration must: child exit %d, status %u at %#010x, hr %#010x "
          "(wanted 0x80040154), popped %u (24), ppv %#010x (0), marker %u (1), device refusals "
          "%u (1); %s", g_off.exit_code, st, pc, hr, pop, ppv, marked, refused, err);
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

/* Labelled readings, kept so a reader can redo the arithmetic. */
#define MAX_SAMPLES 32
static struct {
    const char *label;
    uint64_t    anchor_us, now_us, anchor_frame;
    uint32_t    queued, want_queued;
    uint64_t    samples, want_samples;
} g_s[MAX_SAMPLES];
static uint32_t g_ns;

static void sample(const char *label, uint64_t anchor_us, uint64_t anchor_frame, uint64_t now,
                   uint32_t q, uint32_t wq, uint64_t smp, uint64_t wsmp) {
    if (g_ns < MAX_SAMPLES) {
        g_s[g_ns].label = label;
        g_s[g_ns].anchor_us = anchor_us; g_s[g_ns].anchor_frame = anchor_frame;
        g_s[g_ns].now_us = now;
        g_s[g_ns].queued = q; g_s[g_ns].want_queued = wq;
        g_s[g_ns].samples = smp; g_s[g_ns].want_samples = wsmp;
        g_ns++;
    }
    check(q == wq, "%s: BuffersQueued %u, wanted %u", label, q, wq);
    check(smp == wsmp, "%s: SamplesPlayed %llu, wanted %llu", label,
          (unsigned long long)smp, (unsigned long long)wsmp);
}

/* The answers the streaming thread got natively, inferred from
 * native-run.log lines 15569..28998 as 4 minus the SubmitSourceBuffer lines
 * on 01d0 that follow each GetState. */
static const uint32_t NATIVE_ANSWERS[5] = {0u, 1u, 32u, 1104u, 86u};
static uint32_t g_hist[5];

/* The mastering voice's details under each profile, and what that profile says. */
static struct {
    const char *profile;          /* "default", or the --profile6 path */
    int         ran;
    uint32_t    got[3], want[3];  /* CreationFlags, InputChannels, InputSampleRate */
} g_details[2];

/* Configuration's answer to each device switch and profile. */
#define MAX_CONFIG 4
static struct {
    const char *name, *device, *brk, *note_names;
    int         with_profile, exit_code, wanted_exit, named;
} g_config[MAX_CONFIG];
static uint32_t g_nconfig;

static void write_json(const char *path, const sa2_xa2_stats *xs, uint32_t engine,
                       uint32_t loop_submits, uint64_t loop_frames) {
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "{\n \"ok\": %s, \"checks\": %u, \"failures\": %u,\n",
            g_fail ? "false" : "true", g_checks, g_fail);
    fprintf(f, " \"first_failure\": ");
    jstr(f, g_first_fail);
    fprintf(f, ",\n \"break\": ");
    jstr(f, getenv("SA2_XAUDIO2_BREAK") ? getenv("SA2_XAUDIO2_BREAK") : "");
    fprintf(f, ",\n \"engine\": \"0x%08x\",\n", engine);
    fprintf(f, " \"argument_counts\": {\"calls\": %u, \"every_slot_popped_exactly_"
               "this_plus_its_own_arguments\": %s},\n", g_argc_calls, g_argc_ok ? "true" : "false");
    fprintf(f, " \"format\": {\"samples_per_sec\": %u, \"frame_bytes\": %u, \"buffer_frames\": %u, "
               "\"poll_us\": %u},\n", RATE, FRAME_BYTES, BUF_FRAMES, POLL_US);
    fprintf(f, " \"streaming_loop\": {\"polls\": %u, \"submits\": %u, \"frames_played\": %llu,\n"
               "   \"answers\": {\"1\": %u, \"2\": %u, \"3\": %u, \"4\": %u},\n"
               "   \"native_answers\": {\"1\": %u, \"2\": %u, \"3\": %u, \"4\": %u, "
               "\"source\": \"native-outcomes-v1a/native-run.log:15569-28998, 4 minus the "
               "SubmitSourceBuffer lines on thread 01d0 after each GetState\"}},\n",
            NATIVE_POLLS, loop_submits, (unsigned long long)loop_frames,
            g_hist[1], g_hist[2], g_hist[3], g_hist[4],
            NATIVE_ANSWERS[1], NATIVE_ANSWERS[2], NATIVE_ANSWERS[3], NATIVE_ANSWERS[4]);
    fprintf(f, " \"samples\": [");
    for (uint32_t i = 0; i < g_ns; i++) {
        fprintf(f, "%s\n  {\"at\": ", i ? "," : "");
        jstr(f, g_s[i].label);
        fprintf(f, ", \"anchor_us\": %llu, \"anchor_frame\": %llu, \"now_us\": %llu, "
                   "\"queued\": %u, \"expected_queued\": %u, \"samples_played\": %llu, "
                   "\"expected_samples_played\": %llu}",
                (unsigned long long)g_s[i].anchor_us, (unsigned long long)g_s[i].anchor_frame,
                (unsigned long long)g_s[i].now_us, g_s[i].queued, g_s[i].want_queued,
                (unsigned long long)g_s[i].samples, (unsigned long long)g_s[i].want_samples);
    }
    /* The details and what each profile says, so a reader sees the answer
     * follow the profile rather than a constant. */
    fprintf(f, "],\n \"master_details\": [");
    for (uint32_t i = 0; i < 2u; i++) {
        fprintf(f, "%s\n  {\"profile\": ", i ? "," : "");
        jstr(f, g_details[i].profile ? g_details[i].profile : "");
        fprintf(f, ", \"ran\": %s, \"creation_flags\": \"0x%08x\", \"input_channels\": %u, "
                   "\"input_sample_rate\": %u, \"profile_says\": {\"flags\": \"0x%08x\", "
                   "\"channels\": %u, \"rate\": %u}}",
                g_details[i].ran ? "true" : "false", g_details[i].got[0], g_details[i].got[1],
                g_details[i].got[2], g_details[i].want[0], g_details[i].want[1],
                g_details[i].want[2]);
    }
    fprintf(f, "],\n \"configuration\": [");
    for (uint32_t i = 0; i < g_nconfig; i++) {
        fprintf(f, "%s\n  {\"case\": ", i ? "," : "");
        jstr(f, g_config[i].name);
        fprintf(f, ", \"SA2_XAUDIO2_DEVICE\": ");
        jstr(f, g_config[i].device);
        fprintf(f, ", \"SA2_XAUDIO2_BREAK\": ");
        jstr(f, g_config[i].brk ? g_config[i].brk : "");
        fprintf(f, ", \"profile\": \"%s\", \"exit\": %d, \"wanted_exit\": %d, \"note_names\": ",
                g_config[i].with_profile ? "six-channel" : "default", g_config[i].exit_code,
                g_config[i].wanted_exit);
        jstr(f, g_config[i].note_names ? g_config[i].note_names : "");
        fprintf(f, ", \"named\": %s}", g_config[i].named ? "true" : "false");
    }
    fprintf(f, "],\n \"default_off\": {\"ran\": %s, \"child_exit\": %d, \"status\": %u, "
               "\"exit_pc\": \"0x%08x\", \"hr\": \"0x%08x\", \"popped\": %u, "
               "\"ppv_after\": \"0x%08x\", \"marker_calls\": %u, \"device_refusals\": %u}",
            g_off.ran ? "true" : "false", g_off.exit_code, g_off.status, g_off.exit_pc, g_off.hr,
            g_off.popped, g_off.ppv, g_off.marked, g_off.refused);
    fprintf(f, ",\n \"call_time_backstop\": {\"ran\": %s, \"child_exit\": %d, "
               "\"profile_loaded\": %s, \"status\": %u, \"exit_pc\": \"0x%08x\", "
               "\"import_thunk\": \"0x%08x\", \"popped\": %u, \"ppv_after\": \"0x%08x\", "
               "\"note\": ", g_backstop.ran ? "true" : "false", g_backstop.exit_code,
            g_backstop.loaded ? "true" : "false", g_backstop.status, g_backstop.exit_pc,
            g_backstop.thunk, g_backstop.popped, g_backstop.ppv);
    jstr(f, g_backstop.note);
    fprintf(f, "},\n \"device\": {\"cocreate_calls\": %u, \"cocreate_refused\": %u, "
               "\"engines\": %u, \"method_calls\": %u, \"distinct_methods\": %u, "
               "\"refused\": %u, \"from_profile\": %u, \"unknown_this\": %u, "
               "\"dead_this\": %u, \"submits\": %u, \"getstate_calls\": %u, "
               "\"buffers_completed\": %u, \"starvations\": %u, \"log_dropped\": %u},\n",
            xs->cocreate_calls, xs->cocreate_refused, xs->engines, xs->method_calls,
            xs->distinct_methods, xs->refused, xs->from_profile, xs->unknown_this,
            xs->dead_this, xs->submits, xs->getstate_calls, xs->buffers_completed,
            xs->starvations, xs->log_dropped);
    /* The declared rows, which tools/s4/xaudio2_surface.py --argc reads: the
     * counts the dispatcher pops with, under whatever break this run set. */
    fprintf(f, " \"xaudio2\": {\"methods\": [");
    uint32_t n = 0;
    for (uint32_t t = 0; t < sa2_xaudio2_type_count(); t++)
        for (uint32_t k = 0; k < SA2_XA2_MAX_SLOTS; k++) {
            const char *nm = sa2_xaudio2_method_name(t, k);
            if (!nm) continue;
            const sa2_xa2_slot_stat *st = sa2_xaudio2_slot_stat_at(t, k);
            fprintf(f, "%s\n  {\"interface\": ", n++ ? "," : "");
            jstr(f, sa2_xaudio2_type_name(t));
            fprintf(f, ", \"name\": ");
            jstr(f, nm);
            fprintf(f, ", \"slot\": %u, \"argc\": %d, \"implemented\": %s, \"calls\": %u, "
                       "\"flags\": ", k, sa2_xaudio2_method_argc(t, k),
                    sa2_xaudio2_method_implemented(t, k) ? "true" : "false",
                    st ? st->calls : 0u);
            jstr(f, sa2_xaudio2_method_flags(t, k));
            fprintf(f, "}");
        }
    fprintf(f, "]}\n}\n");
    fclose(f);
}

static void config_case(const char *name, const char *device, const char *brk,
                        const char *profile, int wanted_exit, const char *note_names) {
    char err[1024];
    const int code = config_in_child(device, brk, profile, wanted_exit != 0, err, sizeof err);
    const int named = !note_names || (strstr(err, note_names) != NULL
                                      && (!profile || strstr(err, profile) != NULL));
    check(code == wanted_exit && named,
          "configuration with SA2_XAUDIO2_DEVICE=%s%s%s under the %s profile exited %d, wanted "
          "%d%s: %s", device, brk ? " SA2_XAUDIO2_BREAK=" : "", brk ? brk : "",
          profile ? "six-channel" : "default", code, wanted_exit,
          named ? "" : ", and the note does not name the switch and the profile", err);
    if (g_nconfig < MAX_CONFIG) {
        g_config[g_nconfig].name = name;
        g_config[g_nconfig].device = device;
        g_config[g_nconfig].brk = brk;
        g_config[g_nconfig].note_names = note_names;
        g_config[g_nconfig].with_profile = profile != NULL;
        g_config[g_nconfig].exit_code = code;
        g_config[g_nconfig].wanted_exit = wanted_exit;
        g_config[g_nconfig].named = named;
        g_nconfig++;
    }
}

int main(int argc, char **argv) {
    const char *pe = NULL, *json_path = NULL, *profile6 = NULL;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
        else if (!strcmp(argv[i], "--profile6") && i + 1 < argc) profile6 = argv[++i];
        else if (!pe) pe = argv[i];
    }
    if (!pe) {
        fprintf(stderr, "usage: xaudio2_probe <exe> --profile6 PATH [--json PATH]\n");
        return 2;
    }
    /* This probe exists to drive the device, so it turns it on rather than
     * reporting "not enabled" as a pass. The first pass is the default
     * profile's whatever the shell exported: the six-channel profile is loaded
     * by name, later. */
    setenv("SA2_XAUDIO2_DEVICE", "1", 1);
    unsetenv(SA2_DP_ENV);
    if (sa2_runtime_init(pe, 1024) != SA2_OK) { fprintf(stderr, "runtime init failed\n"); return 2; }
    /* The import configuration alone, as in a lifted run: it reads the profile
     * and then configures this device, so the switch set above has to show. */
    sa2_imports_config_from_env();
    check(sa2_xaudio2_device_enabled(), "sa2_imports_config_from_env left the device off with "
                                        "SA2_XAUDIO2_DEVICE=1: it does not configure it");
    if (sa2_imports_bind() <= 0) { fprintf(stderr, "imports did not bind\n"); return 2; }
    check(sa2_devprofile_is_default(), "the first pass runs on the default profile, and %s is "
                                       "loaded", sa2_devprofile_source());

    sa2_xa2_stats xs;
    uint32_t loop_submits = 0;
    uint64_t loop_frames = 0;
    uint32_t popped = 0;
    sa2_call_result_t res;

    /* ---- 1. CoCreateInstance through its own IAT thunk ---- */
    const sa2_import_info *cci = sa2_import_by_name("ole32.dll", "CoCreateInstance");
    uint32_t engine = 0, hr = 0xFFFFFFFFu;
    if (!cci) {
        check(0, "ole32.dll!CoCreateInstance is not an import this runtime knows");
    } else {
        hr = cocreate(cci, 1u /* CLSCTX_INPROC_SERVER */, &res, &popped);
        engine = peek32(SCRATCH);
        sa2_xaudio2_stats_get(&xs);
        check(xs.cocreate_calls == 1u, "CoCreateInstance never reached sa2_xaudio2_cocreate: "
                                       "the import is not wired to the device");
        check(hr == 0u, "CoCreateInstance returned %#010x, wanted S_OK "
                        "(native-run.log:15544)", hr);
        check(engine != 0u && engine != 0xBADBAD00u, "CoCreateInstance wrote %#010x", engine);
        check(popped == 24u, "CoCreateInstance popped %u bytes, wanted 24", popped);
    }
    if (!engine || engine == 0xBADBAD00u) {
        fprintf(stderr, "no engine; nothing further can be driven\n");
        if (json_path) {
            sa2_xaudio2_stats_get(&xs);
            write_json(json_path, &xs, 0u, 0u, 0u);
        }
        printf("xaudio2_probe: %u checks, %u failures -- first: %s\n", g_checks, g_fail,
               g_first_fail);
        return 1;
    }

    /* ---- 2. the calls 0x007841C0 makes ---- */
    check(CALL(engine, 5, 2, 0u, 0xFFFFFFFFu) == 0u, "Initialize(0, -1) did not return S_OK");
    /* Rule 5 on the addresses: any other ppMasteringVoice is a site the decode
     * does not have. Refused before a voice exists, so the real call follows. */
    expect_refusal("CreateMasteringVoice into another pointer", engine, 10, 6,
                   (const uint32_t[]){SCRATCH + 0x20u, 0u, RATE, 0u, 0u, 0u}, "0x01AF0EDC");
    uint32_t details[3] = {0};
    const uint32_t master = master_and_details(engine, details);
    g_details[0].profile = "default";
    g_details[0].ran = master != 0u;
    memcpy(g_details[0].got, details, sizeof details);
    g_details[0].want[0] = 0u;
    g_details[0].want[1] = 2u;
    g_details[0].want[2] = RATE;
    /* The default profile's master: stereo at the game's 48000 Hz
     * (runtime/sa2_devprofile.h), creation flags 0. */
    check(details[0] == 0u, "CreationFlags %#x, wanted the default profile's 0", details[0]);
    check(details[1] == 2u, "InputChannels %u, wanted the default profile's stereo master",
          details[1]);
    check(details[2] == RATE, "InputSampleRate %u, wanted the default profile's 48000",
          details[2]);
    if (!master) goto finish;
    expect_refusal("GetVoiceDetails into another buffer", master, 0, 1,
                   (const uint32_t[]){SCRATCH + 0x40u}, "0x01AF0EE0");

    /* ---- 3. the calls 0x00784280 makes, with the format it builds ---- */
    const uint32_t wfx = SCRATCH + 0x100u;
    pk16(wfx + 0u, 0xFFFEu); pk16(wfx + 2u, 6u); pk32(wfx + 4u, RATE);
    pk32(wfx + 8u, RATE * FRAME_BYTES); pk16(wfx + 12u, FRAME_BYTES); pk16(wfx + 14u, 32u);
    pk16(wfx + 16u, 22u); pk16(wfx + 18u, 32u); pk32(wfx + 20u, 0x3Fu);
    {
        static const uint8_t ieee_float[16] = {0x03,0x00,0x00,0x00,0x00,0x00,0x10,0x00,
                                               0x80,0x00,0x00,0xAA,0x00,0x38,0x9B,0x71};
        sa2_poke(wfx + 24u, 16, ieee_float);
    }
    pk32(SCRATCH + 0x60u, 0u);
    check(CALL(engine, 8, 7, SCRATCH + 0x60u, wfx, 0u, F32_TWO, 0u, 0u, 0u) == 0u,
          "CreateSourceVoice did not return S_OK");
    const uint32_t voice = peek32(SCRATCH + 0x60u);
    check(voice != 0u, "CreateSourceVoice wrote a null voice");
    if (!voice) goto finish;
    expect_refusal("SetOutputMatrix from another matrix", voice, 16, 5,
                   (const uint32_t[]){master, 6u, 2u, SCRATCH + 0x180u, 0u}, "0x0089E4A8");
    check(CALL(voice, 16, 5, master, 6u, 2u, IMAGE_MATRIX, 0u) == 0u,
          "SetOutputMatrix did not return S_OK");

    uint32_t q = 0, ctx = 0;
    uint64_t smp = 0;
    get_state(voice, &q, &smp, &ctx);
    check(q == 0u && smp == 0u && ctx == 0u,
          "a new voice reports queued %u, played %llu, context %#x", q,
          (unsigned long long)smp, ctx);

    /* One buffer before Start, as 0x00784426 submits it: a voice that has not
     * started consumes nothing, however long it waits. */
    submit(voice);
    sa2_virtual_advance_to_us(sa2_virtual_us() + 50000ull);
    get_state(voice, &q, &smp, NULL);
    sample("before-start", 0, 0, sa2_virtual_us(), q, queued_at(1u, 0u), smp, 0u);

    /* ---- 4. the streaming thread's loop, 0x00784760 ---- */
    const uint64_t t0 = sa2_virtual_us();
    check(CALL(voice, 19, 2, 0u, 0u) == 0u, "Start(0, 0) did not return S_OK");
    uint32_t submitted = 1u;
    uint64_t frames_ok = 1;
    for (uint32_t k = 0; k < NATIVE_POLLS; k++) {
        sa2_virtual_advance_to_us(t0 + (uint64_t)k * POLL_US);
        const uint64_t now = sa2_virtual_us();
        get_state(voice, &q, &smp, NULL);
        const uint64_t f = frames_in(now - t0, 1u, 1u);
        /* The loop never lets the queue empty (four buffers are 42.7 ms and it
         * polls every 10), so the model needs no starvation term -- and this
         * checks that assumption instead of trusting it. */
        if (f > (uint64_t)submitted * BUF_FRAMES) frames_ok = 0;
        const uint32_t want_q = queued_at(submitted, f);
        if (k < 3u || k == 9u || k == 16u || k + 1u == NATIVE_POLLS) {
            static const char *L[] = {"poll-0", "poll-1", "poll-2"};
            sample(k < 3u ? L[k] : k == 9u ? "poll-9-sixty-four-bit"
                               : k == 16u ? "poll-16" : "poll-last",
                   t0, 0, now, q, want_q, smp, f);
        } else {
            check(q == want_q && smp == f, "poll %u: queued %u played %llu, wanted %u and %llu",
                  k, q, (unsigned long long)smp, want_q, (unsigned long long)f);
        }
        if (q <= 4u) g_hist[q]++;
        if (q < 4u) for (uint32_t n = 4u - q; n; n--) { submit(voice); submitted++; loop_submits++; }
    }
    loop_frames = frames_in((uint64_t)(NATIVE_POLLS - 1u) * POLL_US, 1u, 1u);
    check(frames_ok, "the streaming loop's model assumed a queue that never empties, and it did");
    /* Native's first three answers: 1 (three submits follow, :15574-15576),
     * 4 (none follow :15587), 3 (one follows :15613). */
    check(g_s[1].queued == 1u && g_s[2].queued == 4u && g_s[3].queued == 3u,
          "the first three polls answered %u, %u, %u; native answered 1, 4, 3",
          g_s[1].queued, g_s[2].queued, g_s[3].queued);

    /* ---- 5. Stop, re-anchoring, the floor ---- */
    /* The loop's last poll is where the voice stops: no virtual time passes
     * between it and Stop, so the model's position there is the stop's. */
    const uint64_t at_stop = loop_frames;
    get_state(voice, &q, &smp, NULL);
    sample("at-stop", t0, 0, sa2_virtual_us(), q, queued_at(submitted, at_stop), smp, at_stop);
    check(CALL(voice, 20, 2, 0u, 0u) == 0u, "Stop(0, 0) did not return S_OK");
    const uint64_t t_stop = sa2_virtual_us();
    sa2_virtual_advance_to_us(t_stop + 5000000ull);
    get_state(voice, &q, &smp, NULL);
    sample("stopped-five-seconds", t_stop, at_stop, sa2_virtual_us(), q,
           queued_at(submitted, at_stop), smp, at_stop);
    const uint64_t t1 = sa2_virtual_us();
    check(CALL(voice, 19, 2, 0u, 0u) == 0u, "the second Start did not return S_OK");
    sa2_virtual_advance_to_us(t1 + 10666ull);
    get_state(voice, &q, &smp, NULL);
    /* 10 666 us is 511.968 frames: the floor is 511, not 512. */
    check(smp == at_stop + 511u, "10666 us after re-anchoring: played %llu, wanted %llu",
          (unsigned long long)smp, (unsigned long long)(at_stop + 511u));
    sa2_virtual_advance_to_us(t1 + 10667ull);
    get_state(voice, &q, &smp, NULL);
    sample("floor-10667us", t1, at_stop, sa2_virtual_us(), q,
           queued_at(submitted, at_stop + 512u), smp, at_stop + 512u);

    /* ---- 6. running dry banks no time; a submit then starts at once ---- */
    const uint64_t end_of_queue = (uint64_t)submitted * BUF_FRAMES;
    sa2_virtual_advance_to_us(sa2_virtual_us() + 2000000ull);
    get_state(voice, &q, &smp, NULL);
    sample("ran-dry", t1, at_stop, sa2_virtual_us(), q, queued_at(submitted, end_of_queue), smp,
           end_of_queue);
    sa2_virtual_advance_to_us(sa2_virtual_us() + 1000000ull);
    const uint64_t t2 = sa2_virtual_us();
    submit(voice); submitted++;
    sa2_virtual_advance_to_us(t2 + 5000ull);
    get_state(voice, &q, &smp, NULL);
    const uint64_t w_5ms = end_of_queue + frames_in(5000ull, 1u, 1u);
    sample("resubmitted-5ms", t2, end_of_queue, sa2_virtual_us(), q, queued_at(submitted, w_5ms),
           smp, w_5ms);
    sa2_virtual_advance_to_us(t2 + 10667ull);
    get_state(voice, &q, &smp, NULL);
    sample("resubmitted-played-out", t2, end_of_queue, sa2_virtual_us(), q,
           queued_at(submitted, end_of_queue + BUF_FRAMES), smp, end_of_queue + BUF_FRAMES);

    /* ---- 7. SetFrequencyRatio: the rate follows, clamped as FAudio clamps ---- */
    /* The four buffers land on a voice that ran dry, so it waits at `base`,
     * and each expected position below is chained from the model's last. */
    for (int n = 0; n < 4; n++) { submit(voice); submitted++; }
    const uint64_t base = end_of_queue + BUF_FRAMES;
    uint64_t t3 = sa2_virtual_us();
    check(CALL(voice, 26, 2, F32_TWO, 0u) == 0u, "SetFrequencyRatio(2.0) did not return S_OK");
    sa2_virtual_advance_to_us(t3 + 5000ull);
    get_state(voice, &q, &smp, NULL);
    const uint64_t w_r2 = base + frames_in(5000ull, 2u, 1u);
    sample("ratio-2", t3, base, sa2_virtual_us(), q, queued_at(submitted, w_r2), smp, w_r2);
    t3 = sa2_virtual_us();
    CALL(voice, 26, 2, F32_FOUR, 0u);            /* past MaxFrequencyRatio: held at 2.0 */
    sa2_virtual_advance_to_us(t3 + 5000ull);
    get_state(voice, &q, &smp, NULL);
    const uint64_t w_r4 = w_r2 + frames_in(5000ull, 2u, 1u);
    sample("ratio-4-clamped-to-2", t3, w_r2, sa2_virtual_us(), q, queued_at(submitted, w_r4),
           smp, w_r4);
    t3 = sa2_virtual_us();
    CALL(voice, 26, 2, 0u, 0u);                  /* 0.0: held at 1/1024 */
    sa2_virtual_advance_to_us(t3 + 1000000ull);
    get_state(voice, &q, &smp, NULL);
    const uint64_t w_r0 = w_r4 + frames_in(1000000ull, 1u, 1024u);
    sample("ratio-0-clamped-to-min", t3, w_r4, sa2_virtual_us(), q, queued_at(submitted, w_r0),
           smp, w_r0);
    CALL(voice, 26, 2, F32_ONE, 0u);

    /* ---- 8. FlushSourceBuffers ---- */
    get_state(voice, &q, &smp, NULL);
    check(q >= 2u, "the flush test needs at least two queued buffers, and has %u", q);
    CALL0(voice, 22);
    uint64_t smp2 = 0;
    get_state(voice, &q, &smp2, NULL);
    check(q == 1u, "a flush while playing left %u buffers, wanted the playing one", q);
    check(smp2 == smp, "a flush moved SamplesPlayed from %llu to %llu",
          (unsigned long long)smp, (unsigned long long)smp2);
    CALL(voice, 20, 2, 0u, 0u);
    CALL0(voice, 22);
    get_state(voice, &q, NULL, NULL);
    check(q == 0u, "a flush of a stopped voice left %u buffers, wanted none", q);

    /* ---- 9. refusals by name ---- */
    expect_refusal("GetDeviceCount", engine, 3, 1, (const uint32_t[]){SCRATCH + 0x80u},
                   "GetDeviceCount");
    {
        const uint32_t b = SCRATCH + 0x340u;
        pk32(b + 0u, 0x40u);                     /* XAUDIO2_END_OF_STREAM */
        pk32(b + 4u, BUF_BYTES); pk32(b + 8u, AUDIO);
        for (uint32_t k = 3; k < 9u; k++) pk32(b + 4u * k, 0u);
        expect_refusal("END_OF_STREAM", voice, 21, 2, (const uint32_t[]){b, 0u},
                       "SubmitSourceBuffer");
    }
    /* A refused CoCreateInstance names the import being answered: its exit_pc
     * is ole32!CoCreateInstance's own thunk, not a method of an engine that
     * does not exist. CLSCTX_LOCAL_SERVER is a context the image never passes. */
    (void)cocreate(cci, 4u, &res, &popped);
    check(res.status == SA2_TRAP_CONTROL_LEFT && res.exit_pc == cci->thunk
          && strstr(res.note, "xaudio2 CoCreateInstance") != NULL && popped == 0u
          && peek32(SCRATCH) == 0xBADBAD00u,
          "CoCreateInstance(CLSCTX_LOCAL_SERVER) did not refuse at the import's thunk %#010x: "
          "status %u at %#010x, popped %u, note %s", cci->thunk, res.status, res.exit_pc, popped,
          res.note);

    /* ---- 10. teardown, in the image's order ---- */
    CALL0(voice, 18);                                          /* 0x007844B3 */
    expect_refusal("GetState on a destroyed voice", voice, 25, 1,
                   (const uint32_t[]){SCRATCH + 0x200u}, "destroyed");
    CALL0(master, 18);                                         /* 0x0077EE1A */
    check(CALL0(engine, 2) == 0u, "the final Release did not return 0");  /* 0x0077EE31 */
    expect_refusal("Release of a released engine", engine, 2, 0, NULL, "released");

    /* ---- 11. configuration against the profile ---- */
    /* A capture's profile says its game had this engine, so the device off, or
     * the `refuse` break, is refused before the guest starts, naming the
     * switch and the profile. The default profile with the device off is the
     * before measurement, and legal. */
    check(profile6 != NULL, "no --profile6 was given, so the six-channel pass and the "
                            "configuration cases did not run");
    if (!profile6) goto finish;
    config_case("capture-profile-device-off", "0", NULL, profile6, 3,
                "SA2_XAUDIO2_DEVICE is off");
    config_case("capture-profile-refuse-break", "1", "refuse", profile6, 3,
                "SA2_XAUDIO2_BREAK=refuse");
    config_case("default-profile-device-off", "0", NULL, NULL, 0, NULL);
    config_case("capture-profile-device-on", "1", NULL, profile6, 0, NULL);
    /* The call-time backstop, for a profile loaded after configuration. */
    if (cci) off_case(cci);
    if (cci) backstop_case(cci, profile6);

    /* ---- 12. the master's details are the profile's ---- */
    /* In-process only once a child has read the same profile with the device
     * on: a profile the reader refuses would end this process at
     * configuration, and the child has already said so. */
    if (g_nconfig != MAX_CONFIG || g_config[3].exit_code != 0) goto finish;
    setenv(SA2_DP_ENV, profile6, 1);
    sa2_devprofile_config_from_env();
    sa2_xaudio2_config_from_env();
    g_details[1].profile = profile6;
    g_details[1].want[0] = sa2_dp_xaudio2_master_flags();
    g_details[1].want[1] = sa2_dp_xaudio2_master_channels();
    g_details[1].want[2] = sa2_dp_xaudio2_master_rate();
    /* Every detail unlike the default's, or a device that ignored the profile
     * for one of them would still pass. */
    check(!sa2_devprofile_is_default() && g_details[1].want[0] != 0u
          && g_details[1].want[1] == 6u && g_details[1].want[2] != RATE,
          "%s is not a profile whose master has flags other than 0, six channels and a rate "
          "other than %u (it says {%#x, %u, %u})", profile6, RATE, g_details[1].want[0],
          g_details[1].want[1], g_details[1].want[2]);
    {
        const uint32_t engine6 = (cocreate(cci, 1u, &res, &popped) == 0u) ? peek32(SCRATCH) : 0u;
        check(engine6 != 0u && engine6 != 0xBADBAD00u,
              "CoCreateInstance under the six-channel profile wrote %#010x: %s", engine6,
              res.note);
        if (engine6 && engine6 != 0xBADBAD00u) {
            check(CALL(engine6, 5, 2, 0u, 0xFFFFFFFFu) == 0u,
                  "Initialize under the six-channel profile did not return S_OK");
            uint32_t d6[3] = {0};
            const uint32_t master6 = master_and_details(engine6, d6);
            g_details[1].ran = master6 != 0u;
            memcpy(g_details[1].got, d6, sizeof d6);
            check(d6[0] == g_details[1].want[0] && d6[1] == 6u && d6[2] == g_details[1].want[2],
                  "GetVoiceDetails under the six-channel profile wrote {%#x, %u, %u}, and the "
                  "profile says {%#x, 6, %u}", d6[0], d6[1], d6[2], g_details[1].want[0],
                  g_details[1].want[2]);
            if (master6) CALL0(master6, 18);
            check(CALL0(engine6, 2) == 0u, "the six-channel engine's final Release did not "
                                           "return 0");
        }
    }
    unsetenv(SA2_DP_ENV);
    sa2_devprofile_config_from_env();

finish:;
    sa2_xaudio2_stats_get(&xs);
    check(xs.unknown_this == 0u, "%u calls on a `this` the device did not make", xs.unknown_this);
    check(xs.getstate_calls == g_state_calls, "the device counted %u GetState calls, the probe "
                                              "made %u", xs.getstate_calls, g_state_calls);
    check(xs.submits == g_submits, "the device counted %u submits, the probe made %u",
          xs.submits, g_submits);
    if (json_path) write_json(json_path, &xs, engine, loop_submits, loop_frames);
    printf("xaudio2_probe: %u checks, %u failures%s%s\n", g_checks, g_fail,
           g_fail ? " -- first: " : "", g_fail ? g_first_fail : "");
    return g_fail ? 1 : 0;
}
