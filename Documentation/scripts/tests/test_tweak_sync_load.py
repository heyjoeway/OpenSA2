"""The sync-load tweak (runtime/tweaks/sync-load.c) on SOC_AsynDoWithNowLoading (0x00428470) and
SOC_AsynDoWithCurrentFrame (0x00428740).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the two lifted bodies, the
function table (sa2_ft_lookup), a work function, and the runtime's peek, poke, register,
control-word and trap entries. The guest memory is flat: the stack, an argument block, and the two
loading-screen words the bodies write (0x01A557E4, 0x01A557F0).

Each argument is one call, as the game's callers make it (0x0040C916 to 0x0040C936: push the
argument, push the work, call):

  now:W:A   0x00428470 with work W and argument A, return address 0x0040C925
  cur:W:A   0x00428740 with work W and argument A, return address 0x0040C936

The stand-in bodies do what the lifted ones do in the lifted runtime: the two stores, an AsynDo
thread (counted), one loading frame (counted), the work, then RET. The stand-in work (registered at
0x0041D2A0 and 0x00428B60; 0x00DEAD00 is not in the table) checks that it was entered at its
address with a return address and its argument above it, records the x87 control word it ran
under, writes 0x600D to the first dword of the argument block, scrambles every register, and RETs
(SYNC_BAD_RET: returns elsewhere). SYNC_BAD_STACK makes the stack unreadable.

After each call the driver prints "key=value" fields: the bodies' runs, threads and frames, the
works run, the argument the work saw and the block's dword, the two words, how far ESP moved, EIP,
the callee-saved registers, EAX, the control word and the host rounding mode after, and the
control word the work ran under.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SYNC_BAD_RET", "SYNC_BAD_STACK")

DRIVER = r"""
#include <fenv.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_trap.h"

#define SHOWN        0x01A557E4u
#define SHOWN_FRAMES 0x01A557F0u
#define STACK        0x00020000u
#define LOW          0x00010000u
#define HIGH         0x00030000u

/* Flat guest memory for the stack and the argument blocks; the two words apart. */
static uint8_t ram[HIGH - LOW];
static uint32_t shown, shown_frames;
static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= LOW && a + n <= HIGH) return ram + (a - LOW);
    if (a == SHOWN && n == 4) return (uint8_t *)&shown;
    if (a == SHOWN_FRAMES && n == 4) return (uint8_t *)&shown_frames;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p || (getenv("SYNC_BAD_STACK") && a >= LOW && a < HIGH)) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }

static uint32_t gpr[SA2_GPR_COUNT];
static uint16_t cw = 0x007F;
static int host_round = FE_TONEAREST;
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
uint16_t sa2_rt_get_cw(const void *state) { (void)state; return cw; }
void sa2_rt_set_cw(void *state, uint16_t v) { (void)state; cw = v; }
int sa2_rt_get_host_round(void) { return host_round; }
void sa2_rt_set_host_round(int mode) { host_round = mode; }
int sa2_rt_round_from_cw(uint16_t v) {
    static const int modes[4] = {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO};
    return modes[(v >> 10) & 3];
}
void sa2_trap_note(const char *fmt, ...) { fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}

static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }
static unsigned bodies, threads, frames, works;
static uint32_t work_arg, work_cw;

/* The work, cdecl with one argument, as the task method 0x00427DB0 calls it. */
static void *work(void *s, uint32_t pc, void *m) {
    (void)s;
    if (gpr[SA2_EIP] != pc) exit(5);
    works++;
    work_arg = rd32(gpr[SA2_ESP] + 4u);
    work_cw = cw;
    wr32(work_arg, 0x600Du);
    for (int i = 0; i < SA2_GPR_COUNT; i++) if (i != SA2_ESP && i != SA2_EIP) gpr[i] = 0xCC000000u + i;
    cw = 0x0C7F;                       /* a work that leaves another control word behind */
    host_round = FE_TOWARDZERO;
    if (getenv("SYNC_BAD_RET")) gpr[SA2_EIP] = 0x0041D2FFu;
    else ret_();
    return m;
}
sa2_lifted_fn sa2_ft_lookup(uint32_t va) {
    return va == 0x0041D2A0u || va == 0x00428B60u ? work : NULL;
}

/* The bodies: the two stores, a thread, one loading frame, the work in it, RET. */
static void *body(void *s, uint32_t pc, void *m) {
    bodies++;
    wr32(SHOWN, 1);
    wr32(SHOWN_FRAMES, 0);
    threads++;
    frames++;
    const uint32_t esp = gpr[SA2_ESP], fn = rd32(esp + 4u), arg = rd32(esp + 8u);
    (void)fn;
    wr32(arg, 0xA5A5u);
    gpr[SA2_EAX] = 0x00427EE0u;
    gpr[SA2_ECX] = gpr[SA2_EDX] = 0x0BADu;
    (void)s; (void)pc;
    ret_();
    return m;
}
void *__real_sub_428470(void *s, uint32_t pc, void *m) { return body(s, pc, m); }
void *__real_sub_428740(void *s, uint32_t pc, void *m) { return body(s, pc, m); }
void *__wrap_sub_428470(void *s, uint32_t pc, void *m);
void *__wrap_sub_428740(void *s, uint32_t pc, void *m);

typedef void *(*fn_t)(void *, uint32_t, void *);

int main(int argc, char **argv) {
    for (int i = 1; i < argc; i++) {
        char kind[8] = {0};
        uint32_t w = 0, a = 0;
        if (sscanf(argv[i], "%3[a-z]:%i:%i", kind, (int *)&w, (int *)&a) != 3) return 2;
        const int now = strcmp(kind, "now") == 0;
        if (!now && strcmp(kind, "cur") != 0) return 2;
        const fn_t f = now ? __wrap_sub_428470 : __wrap_sub_428740;
        const uint32_t pc = now ? 0x00428470u : 0x00428740u, ret = now ? 0x0040C925u : 0x0040C936u;
        shown = 0;
        shown_frames = 3;
        work_arg = work_cw = 0;
        wr32(a, 0);
        const uint32_t esp = STACK + 0x80u;
        wr32(esp, ret);
        wr32(esp + 4u, w);
        wr32(esp + 8u, a);
        gpr[SA2_EAX] = 0xEAEAEAEAu;
        gpr[SA2_EBX] = 0x0B0B0B0Bu;
        gpr[SA2_EBP] = 0x0E0E0E0Eu;
        gpr[SA2_ESI] = 0x05050505u;
        gpr[SA2_EDI] = 0x0D0D0D0Du;
        gpr[SA2_ESP] = esp;
        gpr[SA2_EIP] = pc;
        int token;
        if (f(&token, pc, &token) != &token) return 3;
        printf("call=%s bodies=%u threads=%u frames=%u works=%u arg=%#x block=%#x shown=%u "
               "shown_frames=%u moved=%u eip=%#x ebx=%#x ebp=%#x esi=%#x edi=%#x eax=%#x cw=%#x "
               "round=%d work_cw=%#x\n", argv[i], bodies, threads, frames, works, work_arg, rd32(a),
               shown, shown_frames, gpr[SA2_ESP] - esp, gpr[SA2_EIP], gpr[SA2_EBX], gpr[SA2_EBP],
               gpr[SA2_ESI], gpr[SA2_EDI], gpr[SA2_EAX], cw, host_round == FE_TONEAREST, work_cw);
    }
    return 0;
}
"""


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("sync")
    (d / "driver.c").write_text(DRIVER)
    out = d / "sync"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "sync-load.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, rc: int = 0, **env: str) -> tuple[list[dict], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == rc, p.stderr
    rows = [dict(f.split("=", 1) for f in line.split()) for line in p.stdout.splitlines()]
    return rows, [line for line in p.stderr.splitlines() if not line.startswith("sa2 tweaks: loads in place (")]


ON = {"SA2_TWEAKS": "sync-load"}
P = "sa2 tweaks: loads in place: "
BOOT = ("now:0x0041D2A0:0x00020400", "cur:0x00428B60:0x00020500")


def test_off_each_load_is_the_lifted_body_and_says_nothing(exe):
    rows, err = run(exe, *BOOT)
    assert err == []
    assert [(r["bodies"], r["threads"], r["frames"], r["works"]) for r in rows] == [
        ("1", "1", "1", "0"), ("2", "2", "2", "0")]
    assert [r["block"] for r in rows] == ["0xa5a5", "0xa5a5"]
    assert [(r["moved"], r["eip"], r["eax"]) for r in rows] == [("4", "0x40c925", "0x427ee0"),
                                                                ("4", "0x40c936", "0x427ee0")]


def test_on_the_work_runs_in_place_with_no_thread_and_no_frame(exe):
    rows, err = run(exe, *BOOT, **ON)
    now, cur = rows
    assert (now["bodies"], now["threads"], now["frames"], now["works"]) == ("0", "0", "0", "1")
    assert (cur["bodies"], cur["threads"], cur["frames"], cur["works"]) == ("0", "0", "0", "2")
    # The work saw its argument and wrote its result into the caller's block.
    assert (now["arg"], now["block"], cur["arg"], cur["block"]) == ("0x20400", "0x600d", "0x20500", "0x600d")
    # The body's own stores, so GameLoop's loading draw runs its five frames after the load.
    assert (now["shown"], now["shown_frames"]) == ("1", "0")
    # The body's RET: ESP + 4, EIP the caller's return address, every register put back.
    assert (now["moved"], now["eip"], cur["moved"], cur["eip"]) == ("4", "0x40c925", "4", "0x40c936")
    for r in rows:
        assert (r["ebx"], r["ebp"], r["esi"], r["edi"], r["eax"]) == (
            "0xb0b0b0b", "0xe0e0e0e", "0x5050505", "0xd0d0d0d", "0xeaeaeaea")
        # The work ran under a new thread's control word; the main thread's is put back.
        assert (r["work_cw"], r["cw"], r["round"]) == ("0x27f", "0x7f", "1")
    assert err == [P + "work 0x0041D2A0 runs in place from 0x0040C925 (SOC_AsynDoWithNowLoading)",
                   P + "1 load run in place (no AsynDo thread, no loading frame)",
                   P + "work 0x00428B60 runs in place from 0x0040C936 (SOC_AsynDoWithCurrentFrame)"]


def test_the_count_is_said_at_1_and_10_and_each_pair_once(exe):
    rows, err = run(exe, *(BOOT * 6), **ON)
    assert rows[-1]["works"] == "12" and rows[-1]["bodies"] == "0"
    assert [line for line in err if "run in place (" in line] == [
        P + "1 load run in place (no AsynDo thread, no loading frame)",
        P + "10 loads run in place (no AsynDo thread, no loading frame)"]
    assert len([line for line in err if "runs in place from" in line]) == 2


def test_a_work_missing_from_the_function_table_loads_as_usual_and_says_so_once(exe):
    rows, err = run(exe, "now:0x00DEAD00:0x00020400", "cur:0x00DEAD00:0x00020400", *BOOT, **ON)
    assert [(r["bodies"], r["works"]) for r in rows] == [("1", "0"), ("2", "0"), ("2", "1"), ("2", "2")]
    assert rows[0]["block"] == "0xa5a5" and rows[0]["moved"] == "4"
    assert err[0] == P + "work 0x00DEAD00 (from 0x0040C925) is not in the function table, so its load " \
                         "runs as usual"
    assert sum("not in the function table" in line for line in err) == 1


def test_a_stack_that_does_not_read_loads_as_usual_and_says_so_once(exe):
    rows, err = run(exe, *BOOT, SYNC_BAD_STACK="1", **ON)
    assert [(r["bodies"], r["works"]) for r in rows] == [("1", "0"), ("2", "0")]
    assert err == [P + "the guest stack at 0x00020080 does not read, so SOC_AsynDoWithNowLoading's "
                       "load runs as usual"]


def test_a_work_that_does_not_return_to_the_sentinel_stops_the_run(exe):
    _, err = run(exe, BOOT[0], rc=7, SYNC_BAD_RET="1", **ON)
    assert err[-1] == "trap 1 at 0x0041d2ff"


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    on, _ = run(exe, *BOOT, **ON, **{replay: "/dev/null"})
    off, _ = run(exe, *BOOT)
    assert on == off


def test_the_android_line_says_the_save_is_not_changed():
    java = (ROOT / "host/android/java/com/sa2recomp/app/GameSettings.java").read_text()
    line = next(x for x in java.splitlines() if 'new Tweak("sync-load"' in x)
    assert "Your save is not changed." in line
