"""The shadow-lightdash-fx tweak (runtime/tweaks/shadow-lightdash-fx.c): around the light dash's
after-image (0x0071E460), Shadow's CharID (playerwk+1) reads 0 and plobjects[30] (0x01DE9714) reads
model 103 (0x01DE995C), so the body takes Sonic's path with Shadow's model; both are written back
before the wrap returns. With the tweak off, and for every other case, the body sees the game's
values.

The tweak's file and runtime/sa2_tweaks.c are built with the pinned zig as test_tweaks.py builds
them (-O2, x86_64-linux-musl, -Werror), linked with a driver that stands in for the guest: the stack
at ESP with playerwk at [ESP+4], the CharObj2Base's first four bytes (PlayerNum, CharID, Costume,
CharID2), the two plobjects model pointers and TwoPlayerMode (0x0174AFDE). The stand-in
`__real_sub_71e460` makes the body's decisions from what it reads, as 0x0071E472-0x0071E4DF do,
and prints them; after each call the driver prints the CharID and plobjects[30] the guest is left
with.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_tweaks import CC, RUNTIME, VARS, ZIG, zig_env  # noqa: E402

SONIC_30 = 0x01A00030   # stand-in model pointers
SHADOW_103 = 0x01A00103
PWP = 0x01B00000

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define ESP      0x0019FF00u
#define PWP      0x01B00000u
#define MODEL30  0x01DE9714u
#define MODEL103 0x01DE995Cu
#define TWO_P    0x0174AFDEu

static uint8_t stack[12], pwp[4], two_p;
static uint32_t model30, model103;
static const char *mode;
static int pokes;

static void *at(uint32_t a, uint32_t n) {
    if (a >= ESP && a + n <= ESP + sizeof stack) return strcmp(mode, "stack") ? stack + (a - ESP) : NULL;
    if (a >= PWP && a + n <= PWP + sizeof pwp) return pwp + (a - PWP);
    if (a == MODEL30 && n == 4) return strcmp(mode, "models") ? (void *)&model30 : NULL;
    if (a == MODEL103 && n == 4) return &model103;
    if (a == TWO_P && n == 1) return &two_p;
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    void *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    void *p = at(a, n);
    if (!p) return 1;
    if (!strcmp(mode, "no-write-model") && a == MODEL30) return 1;
    if (!strcmp(mode, "no-write-char") && a == PWP + 1) return 1;
    memcpy(p, in, n);
    pokes++;
    return SA2_OK;
}
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return idx == SA2_ESP ? ESP : 0; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; (void)idx; (void)v; }

/* 0x0071E472: CharID must be 0; 0x0071E47C: plobjects[30] must be set; 0x0071E4D8: one player. */
void *__real_sub_71e460(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    if (pwp[1] != 0) printf("body: char %u, no after-image\n", pwp[1]);
    else if (!model30) printf("body: no model 30, no after-image\n");
    else if (two_p) printf("body: two players, no draw\n");
    else printf("body: draws 0x%08X\n", model30);
    return memory;
}
void *__wrap_sub_71e460(void *state, uint32_t pc, void *memory);

int main(int argc, char **argv) {
    if (argc != 8) return 2;
    uint32_t p = PWP;
    memcpy(stack + 4, &p, 4);
    memcpy(stack + 8, &p, 4);
    pwp[0] = 0; pwp[1] = (uint8_t)atoi(argv[1]); pwp[2] = 0; pwp[3] = (uint8_t)atoi(argv[2]);
    model30 = (uint32_t)strtoul(argv[3], NULL, 0);
    model103 = (uint32_t)strtoul(argv[4], NULL, 0);
    two_p = (uint8_t)atoi(argv[5]);
    mode = argv[6];
    for (int i = 0, calls = atoi(argv[7]); i < calls; i++) {
        int token;
        if (__wrap_sub_71e460(&token, 0x0071E460u, &token) != &token) return 3;
        printf("after: char %u char2 %u model30 0x%08X\n", pwp[1], pwp[3], model30);
    }
    printf("pokes %d\n", pokes);
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("shadow_lightdash")
    (d / "driver.c").write_text(DRIVER)
    out = d / "tw"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "shadow-lightdash-fx.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, char: int = 1, char2: int = 1, model30: int = 0, model103: int = SHADOW_103,
        two_p: int = 0, mode: str = "readable", calls: int = 2, **env: str) -> tuple[list[str], str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), str(char), str(char2), hex(model30), hex(model103), str(two_p), mode,
                        str(calls)], capture_output=True, text=True, timeout=30, env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.splitlines(), p.stderr


ON = {"SA2_TWEAKS": "shadow-lightdash-fx"}
REPORT = "sa2 tweaks: shadow lightdash trail (SA2_TWEAKS=shadow-lightdash-fx)"
SWAP = ("sa2 tweaks: shadow lightdash trail: Shadow's light dash draws its after-image with model 103 "
        "(0x01A00103)")


def after(char: int = 1, char2: int = 1, model30: int = 0) -> str:
    return f"after: char {char} char2 {char2} model30 0x{model30:08X}"


def test_the_addresses_are_the_images():
    """The body reads CharID at [ESP+4]+1 and plobjects[30] at 0x01DE9714, and model 103's pointer
    is plobjects (0x01DE9620) + 103 * 8 + 4, when the private tree has the unpacked executable."""
    exe_path = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
    if not exe_path.is_file():
        pytest.skip("no unpacked executable in private/exe")
    pefile = pytest.importorskip("pefile")
    pe = pefile.PE(str(exe_path), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    body = pe.get_data(0x0071E460 - base, 0x30)
    assert body[0x08:0x0C].hex(" ") == "8b 6c 24 08"            # mov ebp,[esp+8] after push ebp
    assert body[0x12:0x16].hex(" ") == "80 7d 01 00"            # cmp byte [ebp+1],0
    assert body[0x1C:0x21].hex(" ") == "a1 14 97 de 01"         # mov eax,[0x01DE9714]
    assert 0x01DE9620 + 30 * 8 + 4 == 0x01DE9714 and 0x01DE9620 + 103 * 8 + 4 == 0x01DE995C
    two_p = pe.get_data(0x0071E4D8 - base, 7)
    assert two_p.hex(" ") == "80 3d de af 74 01 00"             # cmp byte [0x0174AFDE],0


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"}])
def test_off_shadow_draws_nothing_and_it_says_nothing(exe, env):
    out, err = run(exe, model30=SONIC_30, **env)
    assert out == ["body: char 1, no after-image", after(model30=SONIC_30)] * 2 + ["pokes 0"]
    assert "lightdash" not in err


def test_on_shadow_draws_model_103_and_both_are_written_back(exe):
    out, err = run(exe, calls=3, **ON)
    assert out == ["body: draws 0x01A00103", after()] * 3 + ["pokes 12"]
    assert err.splitlines() == [REPORT, SWAP]


def test_on_shadow_with_a_model_30_loaded_gets_it_back(exe):
    out, err = run(exe, model30=SONIC_30, calls=1, **ON)
    assert out == ["body: draws 0x01A00103", after(model30=SONIC_30), "pokes 4"]
    assert err.splitlines() == [REPORT, SWAP]


@pytest.mark.parametrize("char2", [0, 1])
def test_on_sonic_is_the_game_alone(exe, char2):
    """CharID 0 (Sonic, Amy's CharID2 differs) takes the body's own path, model 30, untouched."""
    out, err = run(exe, char=0, char2=char2, model30=SONIC_30, **ON)
    assert out == ["body: draws 0x01A00030", after(0, char2, SONIC_30)] * 2 + ["pokes 0"]
    assert err.splitlines() == [REPORT]


def test_on_metal_sonic_is_left_alone_by_name_once(exe):
    out, err = run(exe, char2=12, **ON)
    assert out == ["body: char 1, no after-image", after(char2=12)] * 2 + ["pokes 0"]
    assert err.splitlines() == [REPORT, "sa2 tweaks: shadow lightdash trail: character 12 on Shadow's "
                                "code is left alone (model 103 is Shadow's)"]


def test_on_two_players_is_left_alone_by_name_once(exe):
    out, err = run(exe, two_p=1, **ON)
    assert out == ["body: char 1, no after-image", after()] * 2 + ["pokes 0"]
    assert err.splitlines() == [REPORT, "sa2 tweaks: shadow lightdash trail: two-player play is left "
                                "alone (the game draws no after-image there for Sonic either)"]


def test_on_model_103_not_loaded_is_named_once(exe):
    out, err = run(exe, model103=0, model30=SONIC_30, **ON)
    assert out == ["body: char 1, no after-image", after(model30=SONIC_30)] * 2 + ["pokes 0"]
    assert err.splitlines() == [REPORT, "sa2 tweaks: shadow lightdash trail: model 103 (0x01DE995C) is "
                                "not loaded, so Shadow's light dash draws no after-image"]


@pytest.mark.parametrize("mode, said", [
    ("stack", "the player at [0x0019FF04] does not read, so this after-image is the game's"),
    ("models", "the character models at 0x01DE9714 do not read, so this after-image is the game's"),
])
def test_on_a_read_that_fails_is_the_game_by_name(exe, mode, said):
    out, err = run(exe, mode=mode, **ON)
    assert out == ["body: char 1, no after-image", after()] * 2 + ["pokes 0"]
    assert err.splitlines() == [REPORT, f"sa2 tweaks: shadow lightdash trail: {said}"]


def test_on_a_char_write_that_fails_changes_nothing(exe):
    out, err = run(exe, mode="no-write-char", **ON)
    assert out == ["body: char 1, no after-image", after()] * 2 + ["pokes 0"]
    assert err.splitlines() == [REPORT, "sa2 tweaks: shadow lightdash trail: Shadow's CharID at "
                                "0x01B00001 could not be written, so this after-image is the game's"]


def test_on_a_model_write_that_fails_puts_the_char_back(exe):
    out, err = run(exe, mode="no-write-model", **ON)
    assert out == ["body: char 1, no after-image", after()] * 2 + ["pokes 4"]
    assert err.splitlines() == [REPORT, "sa2 tweaks: shadow lightdash trail: plobjects[30] at "
                                "0x01DE9714 could not be written, so this after-image is the game's"]


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, **ON, **{replay: "/dev/null"})
    assert out == ["body: char 1, no after-image", after()] * 2 + ["pokes 0"]
    assert err == ("sa2 tweaks: SA2_TWEAKS=shadow-lightdash-fx ignored in a replay (SA2_REPLAY_TICKS or "
                   "SA2_REPLAY_WORDS is set)\n")
