"""The always-recap tweak (runtime/tweaks/always-recap.c) on LoadStory (0x004586A0), the menus'
init (0x00666600) and Menu_StorySummary (0x0067E460).

The tweak's file is built with the pinned zig beside runtime/sa2_tweaks.c, as test_tweaks.py
builds no-event-bars, and linked with a driver that stands in for the three lifted bodies, the
three functions the wrap calls as story select's Continue does (0x00678D40, 0x0067E710,
0x0067E380), and the runtime's peek, poke, register and trap entries. The guest memory holds the
story state, the Hero story's entry types (0x0173A148), StorySummaries (0x00C686C0) with the
image's Hero rows, two Dark rows and the end, GameMode, the menu state, the lives and the HUD.

Each argument is one step of a story, as the game makes them:

  story:T:I    the story type and the entry to load next (T 1 Hero, 2 Dark)
  lives:A:B    Life_Count, and the HUD's copies
  load         GameMode 12's call of LoadStory (return address 0x004349C6); the stand-in body
               copies the index to the previous one, sets GameMode 7 and RETs
  loadfrom:R   the same with return address R
  menus        the menus entered: case 13's clearing of the story state, then the menu init,
               whose stand-in body records 0x01A559E8 (the chooser's case), sets CurrentMenu 0
               (its default) and the menu's state 11, clears 0x01A559E8 and RETs
  summary:S:E  Menu_StorySummary's frame: the stand-in body sets its state to S and EAX to E
  leave        the menus left: the first player's lives below 4 make both counts and their
               copies 4 (RunMenus, 0x006663D4), GameMode 12
  set:A:V      a dword of guest memory

After each step the driver prints "key=value" fields: LoadStory bodies run, the three calls
made, GameMode, CurrentMenu, the summary's state, the story state, MissionNum, the lives, the
HUD's copies, 0x01A559E8, how far ESP moved, and EBX and EDI (which the calls must put back).
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
VARS = ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS", "RECAP_BAD_RET")

# The image's Hero entry types (0 events, 1 a stage, 2 the end, 3 the credits) and its Hero rows of
# StorySummaries (StorySequenceID by row); then two Dark rows and the end of the list.
HERO_KINDS = [0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 1, 0, 1, 1, 0, 1, 0,
              1, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 3, 0, 2]
HERO_ROWS = [1, 2, 4, 6, 8, 10, 11, 12, 14, 16, 18, 20, 22, 23, 25, 26, 28, 30, 32, 33, 35, 37, 38, 41]

DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_trap.h"

#define GAME_MODE   0x0174B040u
#define NEXT_MODE   0x0174B044u
#define TYPE        0x01DEB320u
#define INDEX       0x01DEB31Eu
#define PREV        0x01DEB31Fu
#define FIRST       0x01DEB321u
#define MISSION     0x0174AFE3u
#define LIVES       0x0174B024u
#define HUD         0x01A5A230u
#define HUD_AT      0x00500000u
#define MENU_RETURN 0x01A559E8u
#define MENU        0x01D7BB10u
#define MSTATE      0x01D7BB14u
#define HERO        0x0173A148u
#define DARK        0x0173A370u
#define SUMMARIES   0x00C686C0u
#define STACK       0x00020000u

/* Sparse guest memory: bytes by address, every address below 0x7F000000 readable. */
static struct { uint32_t a; uint8_t v; } mem[4096];
static unsigned nmem;
static uint8_t *at(uint32_t a) {
    for (unsigned i = 0; i < nmem; i++) if (mem[i].a == a) return &mem[i].v;
    if (nmem == sizeof mem / sizeof mem[0]) exit(9);
    mem[nmem].a = a;
    mem[nmem].v = 0;
    return &mem[nmem++].v;
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a + n > 0x7F000000u) return 1;
    for (uint32_t i = 0; i < n; i++) ((uint8_t *)out)[i] = *at(a + i);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (a + n > 0x7F000000u) return 1;
    for (uint32_t i = 0; i < n; i++) *at(a + i) = ((const uint8_t *)in)[i];
    return SA2_OK;
}
static uint32_t rd32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static uint8_t rd8(uint32_t a) { uint8_t v = 0; sa2_peek(a, 1, &v); return v; }
static int16_t rd16(uint32_t a) { int16_t v = 0; sa2_peek(a, 2, &v); return v; }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static void wr16(uint32_t a, int16_t v) { sa2_poke(a, 2, &v); }
static void wr8(uint32_t a, uint8_t v) { sa2_poke(a, 1, &v); }

static uint32_t gpr[SA2_GPR_COUNT];
uint32_t sa2_rt_get_gpr(const void *state, int idx) { (void)state; return gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t v) { (void)state; gpr[idx] = v; }
void sa2_trap_note(const char *fmt, ...) { fprintf(stderr, "trap note: %s\n", fmt); }
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    exit(7);
}

static void ret_(void) { gpr[SA2_EIP] = rd32(gpr[SA2_ESP]); gpr[SA2_ESP] += 4; }
static unsigned loads, pick_calls, set_calls, mods_calls;
static uint32_t pick_ebx = 0xFFu, pick_edi = 0xFFu, set_eax = 0xFFu, set_ecx = 0xFFu;
static uint32_t summary_state, summary_eax, chooser_case = 0xFFu;

void *__real_sub_4586a0(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    loads++;
    wr8(PREV, rd8(INDEX));
    wr32(GAME_MODE, 7);
    gpr[SA2_EAX] = rd8(INDEX);
    ret_();
    return m;
}
void *__real_sub_666600(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    chooser_case = rd32(MENU_RETURN);
    wr32(MENU, 0);
    wr32(MSTATE, 11);
    wr32(MENU_RETURN, 0);
    gpr[SA2_EAX] = 0x7777u;
    ret_();
    return m;
}
void *__real_sub_67e460(void *s, uint32_t pc, void *m) {
    (void)s; (void)pc;
    wr32(MSTATE, summary_state);
    if (summary_eax == 1u) wr32(NEXT_MODE, 12);
    gpr[SA2_EAX] = summary_eax;
    ret_();
    return m;
}
/* A lifted function the wrap calls: entered at its address with the sentinel at [ESP]. */
static void entered(uint32_t pc, uint32_t va) {
    if (pc != va || gpr[SA2_EIP] != va || rd32(gpr[SA2_ESP]) != SA2_CALL_SENTINEL) exit(5);
}
static void leave_(void) {
    gpr[SA2_EDX] = 0xD0D0D0D0u;
    gpr[SA2_EBX] = 0xB0B0B0B0u;    /* restored by the wrap, as the body's own pops would */
    gpr[SA2_EDI] = 0xE0E0E0E0u;
    if (getenv("RECAP_BAD_RET")) gpr[SA2_EIP] = 0x00678DCFu;
    else ret_();
}
void *sub_678d40(void *s, uint32_t pc, void *m) {
    (void)s;
    entered(pc, 0x00678D40u);
    pick_calls++;
    pick_ebx = gpr[SA2_EBX];
    pick_edi = gpr[SA2_EDI];
    leave_();
    return m;
}
void *sub_67e710(void *s, uint32_t pc, void *m) {
    (void)s;
    entered(pc, 0x0067E710u);
    set_calls++;
    set_eax = gpr[SA2_EAX];
    set_ecx = gpr[SA2_ECX];
    wr8(TYPE, (uint8_t)gpr[SA2_ECX]);
    wr8(FIRST, 1);
    wr8(MISSION, 0);
    wr8(INDEX, (uint8_t)gpr[SA2_EAX]);
    wr8(PREV, (uint8_t)(gpr[SA2_EAX] ? gpr[SA2_EAX] - 1 : 0));
    leave_();
    return m;
}
void *sub_67e380(void *s, uint32_t pc, void *m) {
    (void)s;
    entered(pc, 0x0067E380u);
    mods_calls++;
    wr32(MSTATE, 0);
    leave_();
    return m;
}
void *__wrap_sub_4586a0(void *s, uint32_t pc, void *m);
void *__wrap_sub_666600(void *s, uint32_t pc, void *m);
void *__wrap_sub_67e460(void *s, uint32_t pc, void *m);

static const uint8_t hero_kinds[] = {@KINDS@};
static const int32_t rows[][2] = {@ROWS@{1, 1}, {1, 3}, {-1, 0}};

typedef void *(*fn_t)(void *, uint32_t, void *);
static uint32_t call(fn_t f, uint32_t pc, uint32_t ret) {
    const uint32_t esp = STACK + 0x80u;
    wr32(esp, ret);
    gpr[SA2_ESP] = esp;
    gpr[SA2_EIP] = pc;
    int token;
    if (f(&token, pc, &token) != &token) exit(3);
    return gpr[SA2_ESP] - esp;
}

int main(int argc, char **argv) {
    for (unsigned i = 0; i < sizeof hero_kinds; i++) wr8(HERO + 12u * i, hero_kinds[i]);
    wr8(DARK + 12u * 1u, 1);    /* Dark entries 1 and 3 are stages, 2 events */
    wr8(DARK + 12u * 3u, 1);
    for (unsigned i = 0; i < sizeof rows / sizeof rows[0]; i++) {
        wr32(SUMMARIES + 20u * i, (uint32_t)rows[i][0]);
        wr32(SUMMARIES + 20u * i + 4u, (uint32_t)rows[i][1]);
    }
    wr32(HUD, HUD_AT);
    wr32(MENU_RETURN, 1);
    wr32(MENU, 4);
    wr8(FIRST, 1);
    wr8(MISSION, 0);
    for (int i = 1; i < argc; i++) {
        uint32_t a = 0, b = 0, moved = 0;
        gpr[SA2_EBX] = 0x0B0B0B0Bu;
        gpr[SA2_EDI] = 0x0D1D1D1Du;
        if (sscanf(argv[i], "story:%u:%u", &a, &b) == 2) {
            wr8(TYPE, (uint8_t)a);
            wr8(INDEX, (uint8_t)b);
            wr8(PREV, (uint8_t)(b ? b - 1 : 0));
        } else if (sscanf(argv[i], "lives:%u:%u", &a, &b) == 2) {
            wr16(LIVES, (int16_t)a); wr16(LIVES + 2, (int16_t)b);
            wr16(HUD_AT + 8, (int16_t)a); wr16(HUD_AT + 10, (int16_t)b);
        } else if (sscanf(argv[i], "set:%i:%i", (int *)&a, (int *)&b) == 2) {
            wr32(a, b);
        } else if (strcmp(argv[i], "load") == 0 || sscanf(argv[i], "loadfrom:%i", (int *)&a) == 1) {
            wr32(GAME_MODE, 12);
            moved = call(__wrap_sub_4586a0, 0x004586A0u, strcmp(argv[i], "load") == 0 ? 0x004349C6u : a);
        } else if (strcmp(argv[i], "menus") == 0) {
            wr32(GAME_MODE, 14);
            wr8(TYPE, 0); wr8(INDEX, 0); wr8(PREV, 0); wr8(FIRST, 1);
            if (rd16(LIVES) == 0) wr16(LIVES, 4);
            moved = call(__wrap_sub_666600, 0x00666600u, 0x0067076Au);
        } else if (sscanf(argv[i], "summary:%u:%u", &summary_state, &summary_eax) == 2) {
            moved = call(__wrap_sub_67e460, 0x0067E460u, 0x006662C6u);
        } else if (strcmp(argv[i], "leave") == 0) {
            if (rd16(LIVES) < 4) {         /* both players' counts and copies, from the first's */
                wr16(LIVES, 4); wr16(LIVES + 2, 4); wr16(HUD_AT + 8, 4); wr16(HUD_AT + 10, 4);
            }
            wr32(GAME_MODE, 12);
        } else {
            return 2;
        }
        printf("step=%s loads=%u pick=%u:%u:%u set=%u:%u:%u mods=%u mode=%u menu=%u mstate=%u type=%u "
               "index=%u prev=%u first=%u mission=%u lives=%d,%d hud=%d,%d return=%u moved=%u ebx=%#x "
               "edi=%#x case=%u\n", argv[i], loads, pick_calls, pick_ebx, pick_edi, set_calls, set_eax, set_ecx,
               mods_calls, rd32(GAME_MODE), rd32(MENU), rd32(MSTATE), rd8(TYPE), rd8(INDEX), rd8(PREV),
               rd8(FIRST), rd8(MISSION), rd16(LIVES), rd16(LIVES + 2), rd16(HUD_AT + 8), rd16(HUD_AT + 10),
               rd32(MENU_RETURN), moved, gpr[SA2_EBX], gpr[SA2_EDI], chooser_case);
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
    d = tmp_path_factory.mktemp("recap")
    src = (DRIVER.replace("@KINDS@", ", ".join(map(str, HERO_KINDS)))
           .replace("@ROWS@", "".join(f"{{0, {s}}}, " for s in HERO_ROWS)))
    (d / "driver.c").write_text(src)
    out = d / "recap"
    done = subprocess.run([str(ZIG), "cc", *CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "always-recap.c"), "-o", str(out)],
                          capture_output=True, text=True, env=zig_env(d))
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, *steps: str, rc: int = 0, **env: str) -> tuple[list[dict], list[str]]:
    clean = {k: v for k, v in os.environ.items() if k not in VARS}
    p = subprocess.run([str(exe), *steps], capture_output=True, text=True, timeout=30,
                       env=dict(clean, **env))
    assert p.returncode == rc, p.stderr
    rows = [dict(f.split("=", 1) for f in line.split()) for line in p.stdout.splitlines()]
    return rows, [line for line in p.stderr.splitlines() if not line.startswith("sa2 tweaks: recap before stages (")]


ON = {"SA2_TWEAKS": "always-recap"}
P = "sa2 tweaks: recap before stages: "
# Hero entry 14 (Pumpkin Hill, StorySummaries row 8) after entry 13's events, with 2 lives.
TO_14 = ("story:1:14", "lives:2:3", "set:0x0174AFE3:5")
RECAP = ("load", "menus", "summary:3:0", "summary:4:1", "leave", "load")


def test_off_loadstory_is_the_lifted_body_and_says_nothing(exe):
    rows, err = run(exe, *TO_14, *RECAP)
    assert err == []
    load, menus, *_ , last = rows[3:]
    assert (load["loads"], load["mode"], load["moved"]) == ("1", "7", "4")
    assert (menus["pick"], menus["set"], menus["mods"], menus["menu"]) == ("0:255:255", "0:255:255", "0", "0")
    assert menus["case"] == "1"                                # the menu the story returns to
    assert rows[5]["mstate"] == "3"                            # B goes back to story select
    assert last["loads"] == "2" and last["lives"] == "4,4"     # the menus' own 4, as in the game


def test_on_the_recap_plays_before_a_stage_that_has_one_and_the_stage_then_loads_as_it_was(exe):
    rows, err = run(exe, *TO_14, *RECAP, **ON)
    load, menus, back, over, leave, again = rows[3:]
    # LoadStory: the entry is not loaded; GameMode 2 (to the menus) and the body's RET.
    assert (load["loads"], load["mode"], load["moved"], load["type"], load["index"]) == ("0", "2", "4", "1", "14")
    assert load["return"] == "13"                              # past the chooser's table
    # The menus: story select's Continue, made by the wrap; registers put back.
    assert (menus["pick"], menus["set"], menus["mods"]) == ("1:0:14", "1:14:1", "1")
    assert (menus["menu"], menus["mstate"], menus["type"], menus["index"], menus["prev"]) == ("5", "0", "1", "14", "13")
    assert (menus["ebx"], menus["edi"], menus["moved"]) == ("0xb0b0b0b", "0xd1d1d1d", "4")
    assert (menus["return"], menus["case"]) == ("0", "13")
    # B (state 3) becomes Continue (4) on this recap.
    assert back["mstate"] == "4"
    assert over["mstate"] == "4" and leave["lives"] == "4,4" and leave["hud"] == "4,4"
    # LoadStory again: the state held put back, then the body loads entry 14.
    assert (again["loads"], again["mode"], again["type"], again["index"], again["prev"]) == ("1", "7", "1", "14", "14")
    assert (again["lives"], again["hud"], again["mission"], again["first"], again["return"]) == ("2,3", "2,3", "5", "1", "1")
    assert err == [P + "Hero story entry 14 (a stage): its recap (summary row 8) plays first",
                   P + "B on the recap continues the story here",
                   P + "the recap is over: Hero story entry 14 loads, with lives 2 and 3 as they were"]


def test_a_recap_ends_by_itself_or_by_a_or_start_with_state_4(exe):
    rows, err = run(exe, *TO_14, "load", "menus", "summary:2:0", "summary:4:0", "summary:4:1", "leave",
                    "load", **ON)
    assert (rows[4]["menu"], rows[5]["mstate"], rows[6]["mstate"], rows[7]["mstate"]) == ("5", "2", "4", "4")
    assert rows[-1]["loads"] == "1" and rows[-1]["lives"] == "2,3"
    assert P + "B on the recap continues the story here" not in err


@pytest.mark.parametrize("index, why", [(13, "events with no recap of their own"),
                                        (15, "events with no recap of their own"),
                                        (0, "the first entry"),
                                        (43, "the credits"),
                                        (45, "the end")])
def test_entries_without_their_own_recap_load_at_once(exe, index, why):
    rows, err = run(exe, f"story:1:{index}", "load", **ON)
    assert (rows[1]["loads"], rows[1]["mode"]) == ("1", "7"), why
    assert err == []


def test_each_stage_with_a_recap_gets_it_and_a_dark_one_too(exe):
    for story, index, row in ((1, 12, 7), (1, 41, 23), (2, 3, 25)):
        rows, err = run(exe, f"story:{story}:{index}", "load", **ON)
        assert (rows[1]["loads"], rows[1]["mode"]) == ("0", "2")
        name = "Hero" if story == 1 else "Dark"
        assert err == [P + f"{name} story entry {index} (a stage): its recap (summary row {row}) plays first"]


def test_a_story_from_story_select_returns_to_no_menu_and_gets_the_recap_the_same_way(exe):
    """0x01A559E8 is 0 while a story started from story select runs: the wrap makes it 13 for the
    menus' load, as for 1, and puts the 0 back with the rest."""
    rows, err = run(exe, "set:0x01A559E8:0", *TO_14, *RECAP, **ON)
    load, menus, *_, again = rows[4:]
    assert (load["mode"], load["return"]) == ("2", "13")
    assert (menus["menu"], menus["pick"]) == ("5", "1:0:14")
    assert (again["loads"], again["return"], again["lives"]) == ("1", "0", "2,3")
    assert err[0] == P + "Hero story entry 14 (a stage): its recap (summary row 8) plays first"


def test_after_a_resume_the_recap_the_menu_just_played_is_not_played_again(exe):
    """Story select's Continue: the menu's recap ends for entry 14, then LoadStory loads it."""
    rows, err = run(exe, "story:1:14", "summary:4:1", "load", **ON)
    assert (rows[2]["loads"], rows[2]["mode"]) == ("1", "7")
    assert err == []


def test_the_resume_mark_holds_for_one_loadstory_so_the_scene_list_gets_the_recap(exe):
    """A resume at entry 14 plays the menu's recap and loads 14. Back in the menus, story select's
    scene list (0x00670EC7, then state 7 at 0x00670F61) sets entry 14 and LoadStory follows with
    no Menu_StorySummary in between: that load gets its recap."""
    rows, err = run(exe, "story:1:14", "summary:4:1", "load", "menus", "story:1:14", "load", **ON)
    assert (rows[2]["loads"], rows[2]["mode"]) == ("1", "7")       # the resume: no second recap
    assert (rows[-1]["loads"], rows[-1]["mode"]) == ("1", "2")     # the scene list: diverted
    assert err == [P + "Hero story entry 14 (a stage): its recap (summary row 8) plays first"]


def test_after_the_tweaks_own_recap_a_later_visit_to_that_entry_gets_it_again(exe):
    """The tweak's recap at entry 14 leaves the menu's mark on 14; the put-back LoadStory uses it
    up, so choosing entry 14 again from the scene list diverts for the recap."""
    rows, err = run(exe, *TO_14, *RECAP, "menus", "story:1:14", "load", **ON)
    assert (rows[-1]["loads"], rows[-1]["mode"], rows[-1]["return"]) == ("1", "2", "13")
    assert err[-1] == P + "Hero story entry 14 (a stage): its recap (summary row 8) plays first"
    assert err.count(err[-1]) == 2


def test_the_next_stage_after_a_recap_gets_its_own(exe):
    rows, err = run(exe, *TO_14, *RECAP, "story:1:16", "load", **ON)
    assert (rows[-1]["loads"], rows[-1]["mode"]) == ("1", "2")
    assert err[-1] == P + "Hero story entry 16 (a stage): its recap (summary row 9) plays first"


@pytest.mark.parametrize("address, value, field", [
    ("0x01A559E8", 3, "the menu to return to 3"), ("0x0174AFDE", 1, "two players 1"),
    ("0x0174B01B", 1, "0x0174B01B 1"), ("0x01749934", 1, "0x01749934 1"),
    ("0x0174B005", 6, "0x0174B005 6")])
def test_a_state_the_menus_cannot_take_loads_the_stage_and_says_why_once(exe, address, value, field):
    rows, err = run(exe, f"set:{address}:{value}", "story:1:14", "load", "story:1:16", "load", **ON)
    assert [(r["loads"], r["mode"]) for r in rows[2::2]] == [("1", "7"), ("2", "7")]
    assert len(err) == 1 and err[0].startswith(P + "the game is not in the state a story leaves for the menus")
    assert field in err[0] and err[0].endswith("so Hero entry 14 loads without its recap")


def test_a_call_from_elsewhere_loads_the_stage_and_says_so_once(exe):
    rows, err = run(exe, "story:1:14", "loadfrom:0x00401000", "loadfrom:0x00401000", **ON)
    assert [r["loads"] for r in rows[1:]] == ["1", "2"]
    assert err == [P + "LoadStory called from 0x00401000, not game mode 12's call, so Hero entry 14 "
                       "loads without its recap"]


def test_menus_entered_again_during_a_recap_drop_what_was_held(exe):
    rows, err = run(exe, *TO_14, "load", "menus", "menus", "story:1:14", "load", **ON)
    assert err[1] == P + "the menus were entered again before the recap ended, so the story state held " \
                         "for Hero entry 14 is dropped"
    # Nothing held any more: the second menus are the game's own, and entry 14 diverts afresh.
    assert rows[5]["menu"] == "0" and rows[5]["pick"].startswith("1:")


def test_loadstory_meeting_another_entry_after_the_recap_drops_what_was_held(exe):
    rows, err = run(exe, *TO_14, "load", "menus", "summary:4:1", "story:2:4", "leave", "load", **ON)
    assert rows[-1]["loads"] == "1" and rows[-1]["lives"] == "4,4"
    assert err[-1] == P + "LoadStory after the recap meets another story or entry, so the story state " \
                          "held for Hero entry 14 is dropped"


def test_a_lifted_call_that_does_not_return_to_the_sentinel_stops_the_run(exe):
    _, err = run(exe, *TO_14, "load", "menus", rc=7, RECAP_BAD_RET="1", **ON)
    assert err[-1] == "trap 1 at 0x00678dcf"


@pytest.mark.parametrize("replay", ["SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS"])
def test_ignored_in_a_replay(exe, replay):
    on, _ = run(exe, *TO_14, *RECAP, **ON, **{replay: "/dev/null"})
    off, _ = run(exe, *TO_14, *RECAP)
    assert on == off
