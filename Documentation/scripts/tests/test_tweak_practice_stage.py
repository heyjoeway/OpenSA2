"""The practice-stage tweak (runtime/tweaks/practice-stage.c): the boot's start moved to one stage
(the data-DLL loader 0x0077DD10, GameModeHandler's case 13 arm 0x00434515, ResetSeedAndCharacter
0x004395C0) and the two save functions (0x004436A0, 0x00426760) skipped, chosen by SA2_PRACTICE;
and the stage replays' test spawn, which asks the same wraps through sa2_harness_test_spawn
(harness/replay/wrap_test_spawn.c).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the five lifted bodies,
LoadTipsTexs (0x00427EF0), LoadPNG (0x0041D490) and the runtime's peek, poke, register and trap
entries. A second build adds harness/replay/wrap_test_spawn.c, as a stage replay's link does.

Each argument is one call, made as the lifted CALL makes it (a return address at guest [ESP]):

  boot       the data-DLL loader. The stand-in sets EAX = 0x77 and returns. Prints
             "boot <CurrentLevel> <CurrentCharacter> <ChaoSaveIndexThing> <MissionNum>
             <MenuButtonImage> <tips calls> <tips ESI> <png calls> <png path> <EAX> <ECX> <ESI>
             <ESP moved>".
  title[:M]  case 13's arm. The stand-in writes GameMode = M (0x15 when left out, the first visit's
             store) and returns. Prints "title <GameMode>".
  reset      ResetSeedAndCharacter. The stand-in does what the body does: with [0x01748B94] = 1 and
             [0x01749564] != -2, CurrentCharacter = [0x01749564] and [0x01749564] = -2; otherwise
             CurrentCharacter = 0. Prints "reset <CurrentCharacter>".
  pending:N  sets [0x01749564] = N (a pending character for the next reset).
  save, chao ProbablySavesSaveFile and WriteSaveFileThing. The stand-ins count their calls and
             return, leaving EAX. Prints "save <real calls> <EIP> <ESP moved> <EAX>".
  eax:N      the EAX every later call is made with (0xEAEAEAEA when not given): the caller's own
             value, which a skipped save must not hand back as its status.

The guest starts as the image does: [0x01748B94] = 1, [0x01749564] = -2, MissionNum 0, with
TextLanguage 1 and [0x0174B5FC] = 1; CurrentLevel, CurrentCharacter, ChaoSaveIndexThing and
MenuButtonImage start at marker values, so a write shows.
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
VARS = ("SA2_TWEAKS", "SA2_PRACTICE", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "SA2_TEST_SPAWN_LEVEL",
        "SA2_TEST_SPAWN_CHARACTER", "PRACTICE_TIPS_BAD_RET")
CALLER_RET = 0x00401234

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_trap.h"

#define CURRENT_LEVEL 0x01934B70u
#define CURRENT_CHAR  0x01934B80u
#define CHAO_INDEX    0x0173D06Cu
#define LANGUAGE      0x0174AFD1u
#define MISSION       0x0174AFE3u
#define PAD           0x0174B5FCu
#define GAME_MODE     0x0174B040u
#define MENU_IMAGE    0x01A55868u
#define RESET_FLAG    0x01748B94u
#define PENDING       0x01749564u
#define STACK         0x00020000u
#define STACK_SIZE    0x400u

static struct { uint32_t addr; uint8_t v; } cells[256];
static unsigned ncells;
static uint8_t stack[STACK_SIZE];
static uint32_t gpr[SA2_GPR_COUNT];
static unsigned tips_calls, png_calls, save_calls;
static uint32_t tips_esi;
static char png_path[64] = "-";

static uint8_t *cell(uint32_t a) {
    if (a >= STACK && a < STACK + STACK_SIZE) return stack + (a - STACK);
    for (unsigned i = 0; i < ncells; i++) if (cells[i].addr == a) return &cells[i].v;
    if (ncells == sizeof cells / sizeof cells[0]) return NULL;
    cells[ncells].addr = a;
    cells[ncells].v = 0;
    return &cells[ncells++].v;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    for (uint32_t i = 0; i < n; i++) { uint8_t *c = cell(a + i); if (!c) return 1; ((uint8_t *)out)[i] = *c; }
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    for (uint32_t i = 0; i < n; i++) { uint8_t *c = cell(a + i); if (!c) return 1; *c = ((const uint8_t *)in)[i]; }
    return SA2_OK;
}
static uint32_t rd(uint32_t a, uint32_t n) { uint32_t v = 0; sa2_peek(a, n, &v); return v; }
static void wr(uint32_t a, uint32_t n, uint32_t v) { sa2_poke(a, n, &v); }
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
void sa2_trap_note(const char *fmt, ...) { fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}
static void ret_(void) { gpr[SA2_EIP] = rd(gpr[SA2_ESP], 4); gpr[SA2_ESP] += 4; }

static uint32_t title_mode, call_eax = 0xEAEAEAEAu;
void *__real_sub_77dd10(void *s, uint32_t pc, void *m) { (void)s; (void)pc; gpr[SA2_EAX] = 0x77; ret_(); return m; }
void *__real_sub_434515(void *s, uint32_t pc, void *m) { (void)s; (void)pc; wr(GAME_MODE, 4, title_mode); ret_(); return m; }
void *__real_sub_4395c0(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    if (rd(RESET_FLAG, 4) == 1 && rd(PENDING, 4) != 0xFFFFFFFEu) {
        wr(CURRENT_CHAR, 4, rd(PENDING, 4));
        wr(PENDING, 4, 0xFFFFFFFEu);
    } else {
        wr(CURRENT_CHAR, 4, 0);
    }
    gpr[SA2_ECX] = 0xDEAD0CABu;
    ret_();
    return m;
}
void *__real_sub_4436a0(void *s, uint32_t pc, void *m) { (void)s; (void)pc; save_calls++; ret_(); return m; }
void *__real_sub_426760(void *s, uint32_t pc, void *m) { (void)s; (void)pc; save_calls++; ret_(); return m; }
void *sub_427ef0(void *s, uint32_t pc, void *m) {
    (void)s;
    if (pc != 0x00427EF0u || gpr[SA2_EIP] != 0x00427EF0u) exit(5);
    tips_calls++;
    tips_esi = gpr[SA2_ESI];
    gpr[SA2_EAX] = 0xA0A0A0A0u;
    gpr[SA2_ECX] = 0xC1C1C1C1u;
    gpr[SA2_EDX] = 0xD1D1D1D1u;
    if (getenv("PRACTICE_TIPS_BAD_RET")) gpr[SA2_EIP] = 0x00428004u;
    else ret_();
    return m;
}
void *sub_41d490(void *s, uint32_t pc, void *m) {
    (void)s;
    if (pc != 0x0041D490u || gpr[SA2_EIP] != 0x0041D490u) exit(6);
    png_calls++;
    unsigned i = 0;
    for (; i + 1 < sizeof png_path; i++) { png_path[i] = (char)rd(gpr[SA2_ECX] + i, 1); if (!png_path[i]) break; }
    png_path[i] = 0;
    gpr[SA2_EAX] = 0x0B0B0B0Bu;
    gpr[SA2_ECX] = 0xC2C2C2C2u;
    ret_();
    return m;
}

void *__wrap_sub_77dd10(void *s, uint32_t pc, void *m);
void *__wrap_sub_434515(void *s, uint32_t pc, void *m);
void *__wrap_sub_4395c0(void *s, uint32_t pc, void *m);
void *__wrap_sub_4436a0(void *s, uint32_t pc, void *m);
void *__wrap_sub_426760(void *s, uint32_t pc, void *m);

static uint32_t call(void *(*fn)(void *, uint32_t, void *), uint32_t va) {
    const uint32_t esp = STACK + 0x300u;
    wr(esp, 4, 0x00401234u);
    gpr[SA2_ESP] = esp;
    gpr[SA2_EIP] = va;
    gpr[SA2_ECX] = 0x0C0C0C0Cu;
    gpr[SA2_ESI] = 0x05050505u;
    gpr[SA2_EAX] = call_eax;
    int token;
    if (fn(&token, va, &token) != &token) exit(3);
    return gpr[SA2_ESP] - esp;
}

int main(int argc, char **argv) {
    wr(RESET_FLAG, 4, 1);
    wr(PENDING, 4, 0xFFFFFFFEu);
    wr(LANGUAGE, 1, 1);
    wr(PAD, 4, 1);
    wr(MISSION, 1, 0);
    wr(CURRENT_LEVEL, 2, 0xBEEF);
    wr(CURRENT_CHAR, 4, 0xCC);
    wr(CHAO_INDEX, 4, 0xC4A0);
    wr(MENU_IMAGE, 4, 0x1111);
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        if (strcmp(a, "boot") == 0) {
            const uint32_t moved = call(__wrap_sub_77dd10, 0x0077DD10u);
            printf("boot %u %u %#x %u %#x %u %#x %u %s %#x %#x %#x %u\n", rd(CURRENT_LEVEL, 2),
                   rd(CURRENT_CHAR, 4), rd(CHAO_INDEX, 4), rd(MISSION, 1), rd(MENU_IMAGE, 4), tips_calls,
                   tips_esi, png_calls, png_path, gpr[SA2_EAX], gpr[SA2_ECX], gpr[SA2_ESI], moved);
        } else if (strncmp(a, "title", 5) == 0) {
            title_mode = a[5] == ':' ? (uint32_t)strtoul(a + 6, NULL, 0) : 0x15u;
            call(__wrap_sub_434515, 0x00434515u);
            printf("title %#x\n", rd(GAME_MODE, 4));
        } else if (strcmp(a, "reset") == 0) {
            call(__wrap_sub_4395c0, 0x004395C0u);
            printf("reset %u\n", rd(CURRENT_CHAR, 4));
        } else if (strncmp(a, "pending:", 8) == 0) {
            wr(PENDING, 4, (uint32_t)strtoul(a + 8, NULL, 0));
        } else if (strcmp(a, "save") == 0 || strcmp(a, "chao") == 0) {
            const int chao = a[0] == 'c';
            const uint32_t moved = call(chao ? __wrap_sub_426760 : __wrap_sub_4436a0,
                                        chao ? 0x00426760u : 0x004436A0u);
            printf("save %u %#x %u %#x\n", save_calls, gpr[SA2_EIP], moved, gpr[SA2_EAX]);
        } else if (strncmp(a, "eax:", 4) == 0) {
            call_eax = (uint32_t)strtoul(a + 4, NULL, 0);
        } else {
            return 2;
        }
    }
    return 0;
}
"""

# What each call prints when nothing is changed: the lifted bodies alone.
UNTOUCHED_BOOT = "boot 48879 204 0xc4a0 0 0x1111 0 0 0 - 0x77 0xc0c0c0c 0x5050505 4"
PATH = r"\SOC\menuButton1\menu_button_1.png"


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


@pytest.fixture(scope="module")
def exes(tmp_path_factory) -> dict:
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("practice")
    (d / "driver.c").write_text(DRIVER)
    out = {}
    for name, extra in (("game", []), ("stage", [str(ROOT / "harness/replay/wrap_test_spawn.c")])):
        out[name] = d / name
        done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                               str(RUNTIME / "tweaks" / "practice-stage.c"), *extra, "-o", str(out[name])],
                              capture_output=True, text=True, env=zig_env(d))
        assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *calls: str, rc: int = 0, **env: str) -> tuple[list[str], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *calls], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == rc, p.stderr
    return p.stdout.splitlines(), [l for l in p.stderr.splitlines() if l]


ALL = ("boot", "reset", "title", "save", "chao", "title")
UNTOUCHED = [UNTOUCHED_BOOT, "reset 0", "title 0x15", "save 1 0x401234 4 0xeaeaeaea",
             "save 2 0x401234 4 0xeaeaeaea", "title 0x15"]


def started(level: int, character: int, mission: int) -> str:
    return f"boot {level} {character} 0 {mission} 0xb0b0b0b 1 0x1 1 {PATH} 0x77 0xc0c0c0c 0x5050505 4"


@pytest.mark.parametrize("env", [{}, {"SA2_PRACTICE": "13,0"}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_TEST_SPAWN_LEVEL": "10"}])
def test_off_is_the_lifted_bodies_alone_and_says_nothing(exes, env):
    out, err = run(exes["game"], *ALL, **env)
    assert out == UNTOUCHED
    # Another tweak's report line is the reader's; nothing of this tweak's.
    assert err == (["sa2 tweaks: event-bars off (SA2_TWEAKS=no-event-bars)"] if "SA2_TWEAKS" in env else [])


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exes, replay):
    out, err = run(exes["game"], *ALL, SA2_TWEAKS="practice-stage", SA2_PRACTICE="4,1", **{replay: "x"})
    assert out == UNTOUCHED
    assert not any("practice stage:" in l for l in err)


def test_unset_starts_city_escape_with_sonic_and_never_saves(exes):
    out, err = run(exes["game"], *ALL, SA2_TWEAKS="practice-stage")
    # The boot's reset stores 0 over Sonic; the title's first 0x15 becomes 4, its later visits are
    # the game's; both save functions return at once, the lifted bodies never called, each with
    # its success status in EAX (1 saved, 0 written).
    assert out == [started(13, 0, 0), "reset 0", "title 0x4", "save 0 0x401234 4 0x1",
                   "save 0 0x401234 4 0", "title 0x15"]
    assert err == ["sa2 tweaks: practice stage (SA2_TWEAKS=practice-stage)",
                   "sa2 tweaks: practice stage: City Escape (level 13), Sonic (0), mission 1 "
                   "(SA2_PRACTICE is not set); the logos, title and menus are skipped and this run "
                   "never saves",
                   "sa2 tweaks: practice stage: a save skipped (0x004436A0); this run writes no save"]


@pytest.mark.parametrize("value, level, character, mission, said", [
    ("4,1", 4, 1, 0, "White Jungle (level 4), Shadow (1), mission 1"),
    ("whitejungle,shadow,2", 4, 1, 1, "White Jungle (level 4), Shadow (1), mission 2"),
    ("White Jungle, Shadow, 5", 4, 1, 4, "White Jungle (level 4), Shadow (1), mission 5"),
    ("22,0", 22, 0, 0, "Crazy Gadget (level 22), Sonic (0), mission 1"),
    ("10", 10, 0, 0, "Metal Harbor (level 10), Sonic (0), mission 1"),
    ("9", 9, 6, 0, "Prison Lane (level 9), Tails (6), mission 1"),
    ("ironGate", 11, 7, 0, "Iron Gate (level 11), Eggman (7), mission 1"),
    ("cannon's-core", 34, 0, 0, "Cannon's Core (level 34), Sonic (0), mission 1"),
    ("13,knuckles", 13, 4, 0, "City Escape (level 13), Knuckles (4), mission 1"),
    ("13,mechtails", 13, 6, 0, "City Escape (level 13), Tails (6), mission 1"),
    (",rouge", 13, 5, 0, "City Escape (level 13), Rouge (5), mission 1"),
    ("44,,3", 44, 5, 2, "Mad Space (level 44), Rouge (5), mission 3"),
    ("31,0,1", 31, 0, 0, "Green Hill (level 31), Sonic (0), mission 1"),
    ("13,0,1", 13, 0, 0, "City Escape (level 13), Sonic (0), mission 1"),
    (" 13 , 0 , 2 ", 13, 0, 1, "City Escape (level 13), Sonic (0), mission 2"),
    ("green-forest,sonic,2", 3, 0, 1, "Green Forest (level 3), Sonic (0), mission 2"),
    ("0013,mech-tails", 13, 6, 0, "City Escape (level 13), Tails (6), mission 1"),
])
def test_the_chosen_stage_character_and_mission(exes, value, level, character, mission, said):
    out, err = run(exes["game"], "boot", "reset", "title", SA2_TWEAKS="practice-stage", SA2_PRACTICE=value)
    # ResetSeedAndCharacter's store of 0 (0x004395F3) is written back to the chosen character.
    assert out == [started(level, character, mission), f"reset {character}", "title 0x4"]
    assert err[1] == (f"sa2 tweaks: practice stage: {said}; the logos, title and menus are skipped "
                      "and this run never saves")
    assert len(err) == 2


@pytest.mark.parametrize("value, why", [
    ("0", "level 0 is a test level, which the game turns into Chao World"),
    ("1", "level 1 is a test level, which the game turns into Chao World"),
    ("19", "level 19 is not one of the stage select's 30 action stages"),
    ("70", "level 70 is not one of the stage select's 30 action stages"),
    ("90", "level 90 is not one of the stage select's 30 action stages"),
    ("35", "level 35 is not one of the stage select's 30 action stages"),
    ("chaoworld", "\"chaoworld\" is not the name of one of the stage select's 30 action stages"),
    ("13,2", "character 2 is not one of the six"),
    ("13,3", "character 3 is not one of the six"),
    ("13,8", "character 8 is not one of the six"),
    ("13,amy", "\"amy\" is not one of the six characters"),
    ("13,0,0", "mission \"0\" is not 1 to 5"),
    ("13,0,6", "mission \"6\" is not 1 to 5"),
    ("13,0,two", "mission \"two\" is not 1 to 5"),
    ("31,0,2", "Green Hill has mission 1 only"),
    ("13,0,1,1", "more than three words (level, character, mission)"),
    # A word with a digit is a number, only digits: never squashed into another number.
    ("13,0,-1", "mission \"-1\" is not a whole number"),
    ("13,0, -1 ", "mission \"-1\" is not a whole number"),
    ("13,0,1.5", "mission \"1.5\" is not a whole number"),
    ("1-3", "level \"1-3\" is not a whole number"),
    ("-13", "level \"-13\" is not a whole number"),
    ("13,-1", "character \"-1\" is not a whole number"),
    ("13,0,1st", "mission \"1st\" is not a whole number"),
    ("-", "level \"-\" is not a name or a number"),
    ("13,?", "character \"?\" is not a name or a number"),
    ("123456", "level 123456 is not one of the stage select's 30 action stages"),
])
def test_a_setting_that_does_not_parse_is_named_and_the_game_starts_as_usual(exes, value, why):
    out, err = run(exes["game"], *ALL, SA2_TWEAKS="practice-stage", SA2_PRACTICE=value)
    assert out == UNTOUCHED
    assert len(err) == 2
    assert err[1].startswith(f"sa2 tweaks: practice stage: SA2_PRACTICE={value} refused: {why}")
    assert err[1].endswith("; the game starts as usual")


def test_a_title_load_that_is_not_the_intro_is_said_and_the_stage_does_not_start(exes):
    out, err = run(exes["game"], "boot", "title:14", "title", SA2_TWEAKS="practice-stage")
    assert out == [started(13, 0, 0), "title 0xe", "title 0x15"]
    assert err[-1] == ("sa2 tweaks: practice stage: the title's first load chose game mode 14, not the "
                       "intro (0x15), so the title follows and the stage does not start")


def test_a_pending_character_at_the_reset_is_left_alone_and_said(exes):
    out, err = run(exes["game"], "boot", "pending:5", "reset", SA2_TWEAKS="practice-stage",
                   SA2_PRACTICE="4,1")
    assert out == [started(4, 1, 0), "reset 5"]
    assert err[-1] == ("sa2 tweaks: practice stage: ResetSeedAndCharacter took the pending character 5, "
                       "which is left alone")


def test_only_the_boots_reset_is_changed(exes):
    out, _ = run(exes["game"], "boot", "reset", "reset", SA2_TWEAKS="practice-stage", SA2_PRACTICE="4,1")
    assert out == [started(4, 1, 0), "reset 1", "reset 0"]


def test_a_failed_return_from_loadtipstexs_stops_the_run_by_name(exes):
    _, err = run(exes["game"], "boot", rc=7, SA2_TWEAKS="practice-stage", PRACTICE_TIPS_BAD_RET="1")
    assert err[-1] == "trap 1 at 0x00428004"


def test_the_stage_replays_test_spawn_is_the_harness_start_unchanged(exes):
    # Level and character from the harness, no mission written, case 13 and the reset left to the
    # harness's own wrap and the game, both saves skipped, and nothing printed.
    out, err = run(exes["stage"], *ALL, SA2_TEST_SPAWN_LEVEL="10")
    # The harness's skipped saves keep the caller's EAX, as its own wraps did.
    assert out == [started(10, 0, 0), "reset 0", "title 0x15", "save 0 0x401234 4 0xeaeaeaea",
                   "save 0 0x401234 4 0xeaeaeaea", "title 0x15"]
    assert err == []
    out, _ = run(exes["stage"], "boot", SA2_TEST_SPAWN_LEVEL="13", SA2_TEST_SPAWN_CHARACTER="1")
    assert out == [started(13, 1, 0)]


def test_the_stage_replays_link_without_a_test_spawn_is_untouched(exes):
    assert run(exes["stage"], *ALL) == (UNTOUCHED, [])


@pytest.mark.parametrize("caller_eax", ["0", "0x019455d0", "0xfffffffc"])
def test_a_skipped_save_reports_success_whatever_the_callers_eax(exes, caller_eax):
    # 0x0066E973 calls ProbablySavesSaveFile with EAX 0 and 0x0045684F calls WriteSaveFileThing with
    # the task pointer in EAX; each caller tests the result, so the skipped save hands back the
    # success status (1, the body's 0x0044414C; 0, its 0x00426847), never the caller's EAX.
    out, _ = run(exes["game"], f"eax:{caller_eax}", "save", "chao", "save", "chao",
                 SA2_TWEAKS="practice-stage")
    assert out == ["save 0 0x401234 4 0x1", "save 0 0x401234 4 0", "save 0 0x401234 4 0x1",
                   "save 0 0x401234 4 0"]
    # Off, the lifted bodies run and EAX is theirs (the stand-ins leave the caller's).
    out, _ = run(exes["game"], f"eax:{caller_eax}", "save", "chao")
    want = "0" if int(caller_eax, 0) == 0 else hex(int(caller_eax, 0))
    assert out == [f"save 1 0x401234 4 {want}", f"save 2 0x401234 4 {want}"]
