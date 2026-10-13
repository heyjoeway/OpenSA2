"""runtime/sa2_widescreen.c: play's config answer (SA2_PLAY_RES, SA2_PLAY_ASPECT) on ReadConfig, and
the HUD at its 4:3 positions (SA2_PLAY_HUD43) around the HUD's layout functions.

The wrap is built with the pinned zig, as tools/s4/link_s4.py compiles a runtime source (-O2,
x86_64-linux-musl), and linked with a driver that stands in for the lifted ReadConfig (its
`__real_sub_405310` returns EAX = the config object + 4, or 0, over a small guest memory) and the
runtime's peek, poke and register accessor. Each case reads the guest config back after the
wrap: only Width ([EAX+4]) and Height ([EAX+8]) may change, and only when play asks.

The HUD cases drive the file's HUD43_SITES wraps over a guest HorizontalResolution and
VerticalResolution (0x0174BA64, 0x0174BA68): each stand-in `__real_` prints the H it reads, then
calls the next wrap of a chain the case names, so a case reads what a layout function and the
guards it reaches see, nested, and what H is when the outermost wrap returns. "then" in a chain
starts the next call at the top level, after the previous one returned. Two stand-ins also model
the HUD's x origin O (0x01A559E4), which outlives the call that writes it: the score window's
writes O = 40 - 276(k-1) from the k it reads, as 0x0044E080 does, and the trick score's prints
its top-tier sprite's x from O, k and the 1.6 test, as 0x0044D5BD-0x0044D72B compute it.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import re

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
import link_s4 as LS  # noqa: E402

ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
RUNTIME = ROOT / "runtime"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]
VARS = ("SA2_PLAY_RES", "SA2_PLAY_ASPECT", "SA2_PLAY_HUD43", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")
SOURCE = (RUNTIME / "sa2_widescreen.c").read_text()
# HUD43_SITES as the file lists them: (va, kind).
SITES = re.findall(r"^\s*X\(([0-9a-f]+), (HUD43_\w+),", SOURCE, re.M)
LAYOUT = [va for va, kind in SITES if kind == "HUD43_LAYOUT"]
GUARD = [va for va, kind in SITES if kind == "HUD43_GUARD"]

# The config object at guest 0x10000 (EBX), as ReadConfig fills it: FullScreen, Width, Height,
# RefreshRate, Language, Display, Res, Analytics, then a guard word. EAX = EBX + 4.
DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"

#define BASE 0x10000u
#define RES 0x0174BA60u   /* HorizontalResolution at +4, VerticalResolution at +8 */
static uint8_t mem[0x100];
static uint8_t res[0x10];
static uint32_t eax;

static uint8_t *at(uint32_t a, uint32_t n) {
    if (a >= BASE && a + n <= BASE + sizeof mem) return mem + (a - BASE);
    if (a >= RES && a + n <= RES + sizeof res) return res + (a - RES);
    return NULL;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(out, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    uint8_t *p = at(a, n);
    if (!p) return 1;
    memcpy(p, in, n);
    return SA2_OK;
}
static uint64_t switches;
uint64_t sa2_sched_switches(void) { return switches; }
uint64_t sa2_sched_frames(void) { return 0; }

/* The HUD chain: argv tokens "va" or "va!" (that call's body hands off), "va-" (the stand-in
 * called directly, as an unwrapped function would run), or "then" (the next call starts at the
 * top level). */
static char **chain;
static int nchain, pos;
static void *call_wrap(const char *va);
static float h_now(void) { float f; memcpy(&f, res + 4, 4); return f; }
static float v_now(void) { float f; memcpy(&f, res + 8, 4); return f; }
static float k_now(void) { volatile float a = h_now() / 640.0f, b = v_now() / 480.0f; return a / b; }
static float origin;    /* the HUD's x origin O, 0x01A559E4 */
static void *body(uint32_t va, void *memory) {
    printf("%06x %.4f\n", va, (double)h_now());
    if (va == 0x44e9c0u) origin = (float)(40.0 - 276.0 * ((double)k_now() - 1.0));
    if (va == 0x44d360u) {
        const double x = 16.0 + ((double)k_now() - 1.0) * (16.0 - 320.0);
        const int wide = (double)h_now() / (double)v_now() > 1.6;
        printf("trick sprite x %.4f\n", wide ? x - origin - 1.0 : x + origin - 1.0);
    }
    if (strchr(chain[pos], '!')) switches++;
    if (++pos < nchain && strcmp(chain[pos], "then") != 0) call_wrap(chain[pos]);
    return memory;
}
@STUBS@
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return idx == SA2_EAX ? eax : 0xDEADu; }

static int found = 1;
void *__real_sub_405310(void *state, uint32_t pc, void *memory) {
    (void)state; (void)pc;
    const uint32_t cfg[9] = {0, 1280, 720, 60, 1, 0, 23, 0, 0xA5A5A5A5u};
    memcpy(mem + 4, cfg, sizeof cfg);
    eax = found ? BASE + 4u : 0u;
    return memory;
}
void *__wrap_sub_405310(void *state, uint32_t pc, void *memory);

uint32_t sa2_hud43_value(uint32_t v_bits);

int main(int argc, char **argv) {
    if (argc >= 4 && strcmp(argv[1], "hud") == 0) {      /* hud H V chain... */
        const float h = (float)atof(argv[2]), v = (float)atof(argv[3]);
        memcpy(res + 4, &h, 4);
        memcpy(res + 8, &v, 4);
        chain = argv + 4;
        nchain = argc - 4;
        pos = 0;
        while (pos < nchain) {
            if (strcmp(chain[pos], "then") == 0) pos++;
            else call_wrap(chain[pos]);
        }
        printf("after %.4f\n", (double)h_now());
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "values") == 0) {     /* the 4:3 H for every height */
        int bad = 0;
        for (uint32_t lines = 200; lines <= 8640; lines++) {
            const float v = (float)lines;
            uint32_t vb, hb;
            memcpy(&vb, &v, 4);
            hb = sa2_hud43_value(vb);
            float hv, up;
            memcpy(&hv, &hb, 4);
            const uint32_t ub = hb + 1u;
            memcpy(&up, &ub, 4);
            volatile float a = hv / 640.0f, b = v / 480.0f, ua = up / 640.0f;
            const float k = a / b, ku = ua / b;
            const double exact = (double)lines * 4.0 / 3.0;
            /* not above 4/3 of V, k not above 1, and the next float up breaks one of the two */
            if (!((double)hv <= exact && k <= 1.0f && ((double)up > exact || ku > 1.0f)) ||
                (lines % 3 == 0 && (double)hv != exact)) {
                if (bad++ < 5) printf("bad %u %.6f %.9f\n", lines, (double)hv, (double)k);
            }
        }
        printf("values bad %d\n", bad);
        return 0;
    }
    found = argc < 2 || strcmp(argv[1], "missing") != 0;
    int token;
    if (__wrap_sub_405310(&token, 0x00401C0Eu, &token) != &token) return 3;
    for (int k = 0; k < 9; k++) {
        uint32_t v;
        memcpy(&v, mem + 4 + 4 * k, 4);
        printf("%s%u", k ? " " : "", v);
    }
    printf("\n");
    return 0;
}
"""
UNTOUCHED = "0 1280 720 60 1 0 23 0 2779096485"


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("widescreen")
    stubs = "".join(f"void *__real_sub_{va}(void *s, uint32_t pc, void *m) {{ (void)s; (void)pc; return body(0x{va}u, m); }}\n"
                    f"void *__wrap_sub_{va}(void *s, uint32_t pc, void *m);\n" for va, _ in SITES)
    table = "".join(f'    if (strcmp(t, "{va}") == 0) return __wrap_sub_{va}(&token, 0, &token);\n' for va, _ in SITES)
    direct = "".join(f'    if (strcmp(t, "{va}") == 0) return __real_sub_{va}(&token, 0, &token);\n' for va, _ in SITES)
    stubs += ("static void *call_wrap(const char *va) {\n    static int token;\n    char t[16];\n"
              "    snprintf(t, sizeof t, \"%s\", va);\n    char *bang = strchr(t, '!');\n    if (bang) *bang = 0;\n"
              "    char *dash = strchr(t, '-');\n    if (dash) {\n        *dash = 0;\n" + direct.replace("    if", "        if") + "    }\n"
              + table + '    fprintf(stderr, "no site %s\\n", t);\n    exit(4);\n}\n')
    (d / "driver.c").write_text(DRIVER.replace("@STUBS@", stubs))
    out = d / "ws"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"),
                           str(RUNTIME / "sa2_widescreen.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *args: str, **env: str) -> tuple[str, str]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == 0, p.stderr
    return p.stdout.strip(), p.stderr


def test_switch_off_changes_nothing_and_says_nothing(exe):
    assert run(exe) == (UNTOUCHED, "")
    assert run(exe, SA2_PLAY_RES="", SA2_PLAY_ASPECT="") == (UNTOUCHED, "")


def test_play_res_rewrites_width_and_height_only(exe):
    out, err = run(exe, SA2_PLAY_RES="2340x1080")
    assert out == "0 2340 1080 60 1 0 23 0 2779096485"
    assert err == "widescreen: config 1280x720 -> 2340x1080 (SA2_PLAY_RES=2340x1080)\n"
    assert run(exe, SA2_PLAY_RES="1560X720")[0] == "0 1560 720 60 1 0 23 0 2779096485"
    assert run(exe, SA2_PLAY_RES="320x320")[0] == "0 320 320 60 1 0 23 0 2779096485"
    assert run(exe, SA2_PLAY_RES="16384x16384")[0] == "0 16384 16384 60 1 0 23 0 2779096485"


def test_play_aspect_keeps_the_height(exe):
    out, err = run(exe, SA2_PLAY_ASPECT="21:9")
    assert out == "0 1680 720 60 1 0 23 0 2779096485"
    assert err == "widescreen: config 1280x720 -> 1680x720 (SA2_PLAY_ASPECT=21:9)\n"
    assert run(exe, SA2_PLAY_ASPECT="2340:1080")[0] == "0 1560 720 60 1 0 23 0 2779096485"
    assert run(exe, SA2_PLAY_ASPECT="2.39:1")[0] == "0 1721 720 60 1 0 23 0 2779096485"
    # 16:9 on a 16:9 config: the same size, said so, nothing written
    out, err = run(exe, SA2_PLAY_ASPECT="16:9")
    assert (out, err) == (UNTOUCHED, "widescreen: config 1280x720 -> 1280x720 (SA2_PLAY_ASPECT=16:9)\n")


def test_play_res_wins_over_aspect(exe):
    out, err = run(exe, SA2_PLAY_RES="2340x1080", SA2_PLAY_ASPECT="21:9")
    assert out == "0 2340 1080 60 1 0 23 0 2779096485" and "SA2_PLAY_RES=2340x1080" in err


@pytest.mark.parametrize("env", [{"SA2_PLAY_RES": "off"}, {"SA2_PLAY_RES": "OFF"},
                                 {"SA2_PLAY_ASPECT": "off"},
                                 {"SA2_PLAY_RES": "off", "SA2_PLAY_ASPECT": "21:9"},
                                 {"SA2_PLAY_RES": "2340x1080", "SA2_PLAY_ASPECT": "off"}])
def test_off_changes_nothing(exe, env):
    out, err = run(exe, **env)
    assert out == UNTOUCHED and err.startswith("widescreen: off (") and err.count("\n") == 1


@pytest.mark.parametrize("env", [{"SA2_PLAY_RES": v} for v in
                                 ("2340", "2340x", "x1080", "2340x1080x", "2340*1080", "-2340x1080",
                                  "319x1080", "2340x16385", "123456x1080", "2340 x1080", "0x1080")]
                         + [{"SA2_PLAY_ASPECT": v} for v in
                            ("21", "21:", ":9", "0:9", "21:0", "21-9", "-21:9", "21:9:1", "1e3:9",
                             "1:9", "100:1")])
def test_malformed_changes_nothing_with_one_line(exe, env):
    out, err = run(exe, **env)
    name, value = next(iter(env.items()))
    assert out == UNTOUCHED
    assert err.startswith(f"widescreen: {name}={value} ") and err.count("\n") == 1


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    out, err = run(exe, SA2_PLAY_RES="2340x1080", **{replay: "/dev/null"})
    assert out == UNTOUCHED and "ignored in a replay" in err
    assert run(exe, **{replay: "/dev/null"}) == (UNTOUCHED, "")


def test_no_config_read_is_left(exe):
    out, err = run(exe, "missing", SA2_PLAY_RES="2340x1080")
    assert out == UNTOUCHED and "read no Config/UserConfig.cfg" in err


def test_every_link_wraps_read_config_and_the_hud_sites(exe):
    assert "sa2_widescreen.c" in LS.RUNTIME_C and "sub_405310" in LS.PRODUCTION_WRAPS
    assert LS.PRODUCTION_WRAPS[0] == "sub_427ee0"
    # After ReadConfig, the file's HUD43_SITES in its order, and nothing else.
    after = LS.PRODUCTION_WRAPS[LS.PRODUCTION_WRAPS.index("sub_405310") + 1:]
    assert after == [f"sub_{va}" for va, _ in SITES]
    assert "44e9c0" in LAYOUT and {"420210", "420390", "4200f0", "77e980", "42b230", "41b0c0"} <= set(GUARD)
    # Every reader of the HUD's x origin that also reads the width: the trick score, the kart
    # race's HUD and time extension, the boss rush results and the countdown at 0x006F41B0.
    assert {"44d360", "61bba0", "61b6c0", "4526d0", "6f41b0"} <= set(LAYOUT)
    import device_profile as DP
    assert {"SA2_PLAY_RES", "SA2_PLAY_ASPECT", "SA2_PLAY_HUD43"} <= set(DP.OWNED)


def hud(exe: Path, h: float, v: float, *chain: str, **env: str) -> tuple[list[str], str]:
    out, err = run(exe, "hud", str(h), str(v), *chain, **env)
    return out.splitlines(), err


def test_hud43_off_changes_nothing_and_says_nothing(exe):
    for env in ({}, {"SA2_PLAY_HUD43": ""}, {"SA2_PLAY_HUD43": "0"}):
        lines, err = hud(exe, 2340, 1080, "44e9c0", "420210", "44e9c0", **env)
        assert lines == ["44e9c0 2340.0000", "420210 2340.0000", "44e9c0 2340.0000", "after 2340.0000"]
        assert err == ""


def test_hud43_lays_the_score_window_out_at_4_3_and_guards_keep_the_real_width(exe):
    lines, err = hud(exe, 2340, 1080, "44e9c0", "420210", "44e9c0", "4200f0", SA2_PLAY_HUD43="1")
    assert lines == ["44e9c0 1440.0000", "420210 2340.0000", "44e9c0 1440.0000", "4200f0 2340.0000",
                     "after 2340.0000"]
    assert err.splitlines() == [
        "widescreen: HUD at 4:3: H 2340 -> 1440.0000 in the HUD's layout (k 1.00000000, SA2_PLAY_HUD43=1)",
        "widescreen: HUD at 4:3: the score window: score, time, rings, lives (0x0044E9C0) laid out at 4:3",
        "widescreen: HUD at 4:3: the viewport setter (0x00420210) runs with the real width inside the HUD's layout",
        "widescreen: HUD at 4:3: the viewport getter (0x004200F0) runs with the real width inside the HUD's layout"]
    # 3120x1440 and the Pixel's 2856x1280 (1280 lines: the largest float with k not above 1)
    assert hud(exe, 3120, 1440, "44e9c0", SA2_PLAY_HUD43="1")[0] == ["44e9c0 1920.0000", "after 3120.0000"]
    lines, err = hud(exe, 2856, 1280, "44e9c0", SA2_PLAY_HUD43="1")
    assert lines == ["44e9c0 1706.6666", "after 2856.0000"] and "(k 0.99999994," in err


@pytest.mark.parametrize("site", LAYOUT)
def test_hud43_every_layout_site_swaps(exe, site):
    lines, _ = hud(exe, 2560, 1080, site, "420390", SA2_PLAY_HUD43="1")
    lines = [x for x in lines if not x.startswith("trick sprite x")]    # the trick score's model
    assert lines == [f"{site} 1440.0000", "420390 2560.0000", "after 2560.0000"]


def test_hud43_the_trick_score_reads_the_origin_the_score_window_left_at_4_3(exe):
    # The score window runs, returns, and leaves O = 40 - 276(k-1) behind; the trick score's
    # top-tier sprite reads it on a later call. Its x in the 4:3 layout is 16 + O - 1 = 55.
    chain = ("44e9c0", "then", "44d360")
    assert hud(exe, 1440, 1080, *chain, SA2_PLAY_HUD43="1")[0][-2:] == ["trick sprite x 55.0000", "after 1440.0000"]
    lines, _ = hud(exe, 2340, 1080, *chain, SA2_PLAY_HUD43="1")
    assert lines == ["44e9c0 1440.0000", "44d360 1440.0000", "trick sprite x 55.0000", "after 2340.0000"]
    # Off, both read the real width: the port's x, 16 + 0.625 x (16-320) - O - 1 with O = -132.5.
    assert hud(exe, 2340, 1080, *chain)[0][-2:] == ["trick sprite x -42.5000", "after 2340.0000"]
    # Run unwrapped after a swapped score window (the review's case): the 4:3 O with the real k.
    assert hud(exe, 2340, 1080, "44e9c0", "then", "44d360-", SA2_PLAY_HUD43="1")[0][-2:] == [
        "trick sprite x -215.0000", "after 2340.0000"]


@pytest.mark.parametrize("site", GUARD)
def test_hud43_a_guard_outside_the_hud_does_nothing(exe, site):
    assert hud(exe, 2340, 1080, site, SA2_PLAY_HUD43="1") == ([f"{site} 2340.0000", "after 2340.0000"], "")


@pytest.mark.parametrize("h,v", [(1440, 1080), (1280, 1024), (640, 480)])
def test_hud43_no_wider_than_4_3_moves_nothing(exe, h, v):
    lines, err = hud(exe, h, v, "44e9c0", "420210", SA2_PLAY_HUD43="1")
    assert lines == [f"44e9c0 {h}.0000", f"420210 {h}.0000", f"after {h}.0000"]
    assert err == f"widescreen: HUD at 4:3: the screen {h}x{v} is no wider than 4:3; nothing to move\n"


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_hud43_ignored_in_a_replay(exe, replay):
    lines, err = hud(exe, 2340, 1080, "44e9c0", SA2_PLAY_HUD43="1", **{replay: "/dev/null"})
    assert lines == ["44e9c0 2340.0000", "after 2340.0000"]
    assert err == "widescreen: SA2_PLAY_HUD43=1 ignored in a replay (SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is set)\n"


@pytest.mark.parametrize("value", ["2", "yes", "on", "10", " 1"])
def test_hud43_other_values_leave_it_off_with_one_line(exe, value):
    lines, err = hud(exe, 2340, 1080, "44e9c0", SA2_PLAY_HUD43=value)
    assert lines == ["44e9c0 2340.0000", "after 2340.0000"]
    assert err == f"widescreen: SA2_PLAY_HUD43={value} is not 0 or 1; the HUD keeps the port's positions\n"


def test_hud43_says_when_a_swap_spans_a_hand_off(exe):
    lines, err = hud(exe, 2340, 1080, "44e9c0!", SA2_PLAY_HUD43="1")
    assert lines == ["44e9c0 1440.0000", "after 2340.0000"]
    assert ("widescreen: HUD at 4:3: a guest thread hand-off happened inside the score window: score, "
            "time, rings, lives (0x0044E9C0); another thread could have read the 4:3 width") in err


def test_hud43_value_for_every_height(exe):
    assert run(exe, "values")[0] == "values bad 0"
