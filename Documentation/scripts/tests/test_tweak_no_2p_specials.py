"""The no-2p-specials tweak (runtime/tweaks/no-2p-specials.c): no special attacks in a 2-player
battle, from AddRings's 2P tail (0x0044CE10) or from GiveAssistSpecial (0x0046AA80).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the two lifted functions and the
runtime's peek, poke and register accessors over a small fake guest: TwoPlayerMode (0x0174AFDE),
the special slots and timers (0x0174AFED-0x0174AFF4), RingCount (0x0174B028), MainCharObj1
(0x01DEA6C0) and a stack at 0x20000.

The stand-in `__real_sub_44ce10` does what the lifted AddRings does with what the tweak touches:
RingCount[EAX & 1] += EDX, then, when TwoPlayerMode is not 0 and MainCharObj1[player] is not 0
(the tail's two tests, 0x0044CEBE and 0x0044CECB), lights the player's first special slot and
sets the timer to 0x3C; it notes the slot's value as the body saw it. The stand-in
`__real_sub_46aa80` lights the third slot of player ESI, sets the timer, and returns (EIP = the
return address, ESP + 4).

The AddRings half is also a function of its own, sa2_no_2p_specials_add_rings(state, pc, memory,
real), for a wrap of 0x0044CE10 that another tweak's file shares (ring-loss-cap's acts there too):
the driver's "shared" call passes a `real` that stands in for that tweak's way to the body, a loss
capped at 20 rings before the stand-in body runs.
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
OBJ = (0x0A000100, 0x0A000200)        # the two players' MainCharObj1 pointers in the fake guest
RET = 0x0046ACFB                      # after GamePlayerMissed's call at 0x0046ACF6

# Commands (argv): "mode N" sets TwoPlayerMode, "obj P V" sets MainCharObj1[P], "fail A" makes the
# guest address A fail to read and write, "rings P N" calls AddRings for player P with change N,
# "assist P" calls GiveAssistSpecial for player P with the return address RET at [ESP]
# ("assist-unreadable P" with ESP where nothing reads), "shared P N" calls
# sa2_no_2p_specials_add_rings for player P with change N and the `real` via(), "hit P N" calls
# AddRings from DropRings (return address 0x006C1B3F at [ESP]) for player P with change N. Each
# call prints one line.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define MODE   0x0174AFDEu
#define SLOTS  0x0174AFEDu   /* P1SpecialAttacks[3], P2SpecialAttacks[3], SpecialActivateTimer[2] */
#define RINGS  0x0174B028u
#define OBJ1   0x01DEA6C0u
#define STACK  0x20000u
#define RET    0x0046ACFBu

static uint8_t mode, slots[8], stack[0x100];
static int16_t rings[2];
static uint32_t obj1[2], gpr[SA2_GPR_COUNT], fail = 1;
static uint32_t seen_obj, real_calls;

static void *where(uint32_t a, uint32_t n) {
    if (fail != 1 && a <= fail && fail < a + n) return NULL;
    if (a == MODE && n == 1) return &mode;
    if (a >= SLOTS && a + n <= SLOTS + 8) return slots + (a - SLOTS);
    if (a >= RINGS && a + n <= RINGS + 4) return (uint8_t *)rings + (a - RINGS);
    if (a >= OBJ1 && a + n <= OBJ1 + 8) return (uint8_t *)obj1 + (a - OBJ1);
    if (a >= STACK && a + n <= STACK + sizeof stack) return stack + (a - STACK);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = where(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }

void *__real_sub_44ce10(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t p = gpr[SA2_EAX] & 1u;
    real_calls++;
    rings[p] = (int16_t)(rings[p] + (int32_t)gpr[SA2_EDX]);
    seen_obj = obj1[p];
    if (mode != 0 && obj1[p] != 0) {           /* the 2P tail */
        slots[3 * p] = 1;
        slots[6 + p] = 0x3C;
    }
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__real_sub_46aa80(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t p = gpr[SA2_ESI] & 1u;
    real_calls++;
    slots[3 * p + 2] = 1;
    slots[6 + p] = 0x3C;
    uint32_t back = 0xBAD00000u;               /* a stack that does not read: a poison EIP */
    sa2_peek(gpr[SA2_ESP], 4, &back);
    gpr[SA2_EIP] = back;
    gpr[SA2_ESP] += 4;                         /* the RET */
    return memory;
}
void *__wrap_sub_44ce10(void *state, uint32_t pc, void *memory);
void *__wrap_sub_46aa80(void *state, uint32_t pc, void *memory);
void *sa2_no_2p_specials_add_rings(void *state, uint32_t pc, void *memory,
                                   void *(*real)(void *, uint32_t, void *));

/* Another tweak's way to the lifted body from a shared wrap: a loss capped at 20, then the body. */
static uint32_t via_calls;
static void *via(void *state, uint32_t pc, void *memory) {
    via_calls++;
    if ((int32_t)gpr[SA2_EDX] < -20) gpr[SA2_EDX] = (uint32_t)-20;
    return __real_sub_44ce10(state, pc, memory);
}

static void print_slots(void) {
    printf(" slots=");
    for (int i = 0; i < 8; i++) printf("%02x", slots[i]);
    printf(" rings=%d,%d obj=%08x,%08x real=%u\n", rings[0], rings[1], obj1[0], obj1[1], real_calls);
}

int main(int argc, char **argv) {
    int token;
    for (int i = 1; i < argc; i++) {
        const char *c = argv[i];
        if (!strcmp(c, "mode")) { mode = (uint8_t)strtoul(argv[++i], NULL, 0); continue; }
        if (!strcmp(c, "fail")) { fail = (uint32_t)strtoul(argv[++i], NULL, 0); continue; }
        if (!strcmp(c, "obj")) {
            const int p = atoi(argv[++i]);
            obj1[p] = (uint32_t)strtoul(argv[++i], NULL, 0);
            continue;
        }
        const uint32_t esp = strcmp(c, "assist-unreadable") ? STACK + 0x80u : 0x90000u;
        const uint32_t ret = strcmp(c, "hit") ? RET : 0x006C1B3Fu;
        memcpy(stack + 0x80, &ret, 4);
        for (int r = 0; r < SA2_GPR_COUNT; r++) gpr[r] = 0x11110000u + (uint32_t)r;
        gpr[SA2_ESP] = esp;
        gpr[SA2_EIP] = 0xDEAD0000u;
        seen_obj = 0xFFFFFFFFu;
        if (!strcmp(c, "rings") || !strcmp(c, "hit")) {
            gpr[SA2_EAX] = (uint32_t)atoi(argv[++i]);
            gpr[SA2_EDX] = (uint32_t)atoi(argv[++i]);
            if (__wrap_sub_44ce10(&token, 0x0044CE10u, &token) != &token) return 3;
            printf("%s p=%u seen=%08x esp+%u", c, gpr[SA2_EAX] & 1u, seen_obj, gpr[SA2_ESP] - esp);
        } else if (!strcmp(c, "shared")) {
            gpr[SA2_EAX] = (uint32_t)atoi(argv[++i]);
            gpr[SA2_EDX] = (uint32_t)atoi(argv[++i]);
            if (sa2_no_2p_specials_add_rings(&token, 0x0044CE10u, &token, via) != &token) return 3;
            printf("shared p=%u seen=%08x esp+%u via=%u", gpr[SA2_EAX] & 1u, seen_obj,
                   gpr[SA2_ESP] - esp, via_calls);
        } else {
            gpr[SA2_ESI] = (uint32_t)atoi(argv[++i]);
            if (__wrap_sub_46aa80(&token, 0x0046AA80u, &token) != &token) return 3;
            printf("assist p=%u eip=%08x esp+%u eax=%08x", gpr[SA2_ESI], gpr[SA2_EIP],
                   gpr[SA2_ESP] - esp, gpr[SA2_EAX]);
        }
        print_slots();
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
    d = tmp_path_factory.mktemp("no2p")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "no-2p-specials.c"),
                           str(RUNTIME / "tweaks" / "ring-loss-cap.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


SETUP = ("obj", "0", hex(OBJ[0]), "obj", "1", hex(OBJ[1]))
OBJS = f"obj={OBJ[0]:08x},{OBJ[1]:08x}"
# A 2P battle: player 0 collects 10 rings, player 1 collects 5, then player 1 misses and gets
# the catch-up special (GamePlayerMissed takes the rings first, then calls GiveAssistSpecial).
BATTLE = (*SETUP, "mode", "1", "rings", "0", "10", "rings", "1", "5", "rings", "1", "-5",
          "assist", "1")
BATTLE_GAME = [
    f"rings p=0 seen={OBJ[0]:08x} esp+4 slots=010000000000" "3c00" f" rings=10,0 {OBJS} real=1",
    f"rings p=1 seen={OBJ[1]:08x} esp+4 slots=010000010000" "3c3c" f" rings=10,5 {OBJS} real=2",
    f"rings p=1 seen={OBJ[1]:08x} esp+4 slots=010000010000" "3c3c" f" rings=10,0 {OBJS} real=3",
    f"assist p=1 eip={RET:08x} esp+4 eax=11110000 slots=010000010001" "3c3c" f" rings=10,0 {OBJS} real=4",
]
BATTLE_TWEAK = [
    f"rings p=0 seen=00000000 esp+4 slots=0000000000000000 rings=10,0 {OBJS} real=1",
    f"rings p=1 seen=00000000 esp+4 slots=0000000000000000 rings=10,5 {OBJS} real=2",
    f"rings p=1 seen=00000000 esp+4 slots=0000000000000000 rings=10,0 {OBJS} real=3",
    f"assist p=1 eip={RET:08x} esp+4 eax=11110000 slots=0000000000000000 rings=10,0 {OBJS} real=3",
]
REPORT = "sa2 tweaks: no 2P specials (SA2_TWEAKS=no-2p-specials)"


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_is_the_lifted_code(exe, env):
    out, err = run(exe, *BATTLE, **env)
    assert out == BATTLE_GAME
    assert "no 2P specials" not in err


def test_on_no_special_is_given_in_2p_and_the_rings_still_change(exe):
    out, err = run(exe, *BATTLE, SA2_TWEAKS="no-2p-specials")
    assert out == BATTLE_TWEAK
    assert err.splitlines() == [
        REPORT,
        "sa2 tweaks: no 2P specials: player 0's ring specials not given",
        "sa2 tweaks: no 2P specials: player 1's ring specials not given",
        "sa2 tweaks: no 2P specials: player 1's catch-up special not given"]


def test_on_the_addrings_body_sees_0_for_its_player_only_and_the_slot_is_put_back(exe):
    """The body runs once (real=1) with MainCharObj1[0] read as 0; player 1's slot is untouched,
    and after the call both slots hold their pointers again."""
    out, _ = run(exe, *SETUP, "mode", "1", "rings", "2", "20", SA2_TWEAKS="no-2p-specials")
    assert out == [f"rings p=0 seen=00000000 esp+4 slots=0000000000000000 rings=20,0 {OBJS} real=1"]


def test_on_1p_addrings_is_untouched(exe):
    """In 1P (TwoPlayerMode 0) the body sees the player's pointer; the 2P tail does not run
    there with the tweak off either."""
    calls = (*SETUP, "mode", "0", "rings", "0", "10", "rings", "1", "7")
    on, err = run(exe, *calls, SA2_TWEAKS="no-2p-specials")
    assert on == run(exe, *calls)[0] == [
        f"rings p=0 seen={OBJ[0]:08x} esp+4 slots=0000000000000000 rings=10,0 {OBJS} real=1",
        f"rings p=1 seen={OBJ[1]:08x} esp+4 slots=0000000000000000 rings=10,7 {OBJS} real=2"]
    assert err == REPORT + "\n"


def test_on_a_player_with_no_object_is_left_to_the_body(exe):
    out, err = run(exe, "mode", "1", "rings", "0", "3", SA2_TWEAKS="no-2p-specials")
    assert out == ["rings p=0 seen=00000000 esp+4 slots=0000000000000000 rings=3,0 "
                   "obj=00000000,00000000 real=1"]
    assert err == REPORT + "\n"


def test_on_the_assist_wrap_is_a_ret_at_the_entry(exe):
    """The body is not called (real=0); EIP is the return address, ESP rises by 4, EAX and the
    other registers are as the caller left them."""
    out, _ = run(exe, *SETUP, "mode", "1", "assist", "0", SA2_TWEAKS="no-2p-specials")
    assert out == [f"assist p=0 eip={RET:08x} esp+4 eax=11110000 slots=0000000000000000 "
                   f"rings=0,0 {OBJS} real=0"]


@pytest.mark.parametrize("fail, call, line", [
    ((), ("assist-unreadable", "0"),
     "sa2 tweaks: no 2P specials: the guest stack at 0x00090000 does not read, so the catch-up "
     "special is left to the game"),
    (("fail", "0x0174AFDE"), ("rings", "0", "10"),
     "sa2 tweaks: no 2P specials: TwoPlayerMode at 0x0174AFDE does not read, so ring specials are "
     "left to the game"),
    (("fail", "0x01DEA6C4"), ("rings", "1", "10"),
     "sa2 tweaks: no 2P specials: MainCharObj1 at 0x01DEA6C4 does not read or write, so ring "
     "specials are left to the game"),
])
def test_a_guest_read_that_fails_is_said_once_and_left_to_the_game(exe, fail, call, line):
    """Twice: the lifted body runs each time (the special is given, as with the tweak off), and
    the line is said once."""
    out, err = run(exe, *SETUP, "mode", "1", *fail, *call, *call, SA2_TWEAKS="no-2p-specials")
    assert err.splitlines() == [REPORT, line]
    assert [o.split(" real=")[1] for o in out] == ["1", "2"]
    p = int(call[1])
    slots = [0] * 8
    slots[3 * p + (0 if call[0] == "rings" else 2)], slots[6 + p] = 1, 0x3C
    assert f" slots={bytes(slots).hex()} " in out[1]
    if call[0] == "rings":
        assert all(f" seen={OBJ[p]:08x} " in o for o in out)


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, *BATTLE, SA2_TWEAKS="no-2p-specials", **{replay: "/dev/null"})
    assert out == BATTLE_GAME
    assert err == ("sa2 tweaks: SA2_TWEAKS=no-2p-specials ignored in a replay (SA2_REPLAY_TICKS "
                   "or SA2_REPLAY_WORDS is set)\n")


def test_both_tweaks_named_turn_this_one_on(exe):
    out, err = run(exe, *BATTLE, SA2_TWEAKS="no-event-bars,no-2p-specials")
    assert out == BATTLE_TWEAK
    assert err.splitlines()[0] == ("sa2 tweaks: no 2P specials, event-bars off "
                                   "(SA2_TWEAKS=no-event-bars,no-2p-specials)")


# Player 1 has 30 rings in a 2P battle, then a call from the shared wrap with a loss of 50 that the
# other tweak caps at 20: the body runs once, through via(), and the player keeps 10.
SHARED = (*SETUP, "mode", "1", "rings", "1", "30", "shared", "1", "-50")


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_the_shared_addrings_function_only_calls_the_real_it_is_given(exe, env):
    out, err = run(exe, *SHARED, **env)
    assert out[1] == (f"shared p=1 seen={OBJ[1]:08x} esp+4 via=1 slots=000000010000" "003c"
                      f" rings=0,10 {OBJS} real=2")
    assert "no 2P specials" not in err


def test_on_the_shared_addrings_function_hides_the_slot_around_the_real_it_is_given(exe):
    """The other tweak's change and this one's act on the same single call of the body: the loss
    is capped (rings 10), the body sees MainCharObj1[1] as 0 and gives no special, and the slot
    holds its pointer again after the call."""
    out, err = run(exe, *SHARED, SA2_TWEAKS="no-2p-specials")
    assert out == [
        f"rings p=1 seen=00000000 esp+4 slots=0000000000000000 rings=0,30 {OBJS} real=1",
        f"shared p=1 seen=00000000 esp+4 via=1 slots=0000000000000000 rings=0,10 {OBJS} real=2"]
    assert err.splitlines() == [REPORT, "sa2 tweaks: no 2P specials: player 1's ring specials not given"]


# Player 1 has 50 rings in a 2P battle and is hit: DropRings calls AddRings with -50.
HIT = (*SETUP, "mode", "1", "rings", "1", "50", "hit", "1", "-50")


@pytest.mark.parametrize("tweaks, hit", [
    # ring-loss-cap alone: the loss is capped, and the body sees the player's object.
    ("ring-loss-cap", f"hit p=1 seen={OBJ[1]:08x} esp+4 slots=000000010000003c rings=0,30 {OBJS} real=2"),
    # both: the loss is capped and the 2P tail is skipped, on one call of the body.
    ("no-2p-specials,ring-loss-cap",
     f"hit p=1 seen=00000000 esp+4 slots=0000000000000000 rings=0,30 {OBJS} real=2"),
])
def test_ring_loss_cap_acts_in_this_files_addrings_wrap(exe, tweaks, hit):
    out, err = run(exe, *HIT, SA2_TWEAKS=tweaks)
    assert out[1] == hit
    assert "sa2 tweaks: ring loss capped at 20: player 1 lost 20 of 50 rings" in err.splitlines()
