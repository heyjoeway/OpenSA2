"""all-upgrades (runtime/tweaks/all-upgrades.c): after the lifted body of 0x0044C1A0 each frame,
player 1's Upgrades gets every character's set, 0x1FFF7FFF (Codes.lst's "Have All Upgrades"), when
the character plays its own code (CharID == CharID2 <= 7) in one-player play, found through
MainCharacter[0] (0x01DEA6E0) -> task+0x40 -> CharObj2Base.

The tweak acts in start-assists.c's wrap of sub_44c1a0, so it is built as
test_tweak_start_assists.py builds that tweak, with that test's driver: the reader, start-assists.c
and all-upgrades.c, linked with a stand-in `__real_sub_44c1a0` that counts its calls and the
runtime's accessors over a fake guest heap, counting every peek and poke so "off" can be shown to
touch nothing. Each `frame` prints "<Powerups> <Upgrades> <real calls> <peeks> <pokes> <EAX>" (hex,
counters since the last frame).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_tweak_start_assists as SA  # noqa: E402

ROOT, RUNTIME = SA.ROOT, SA.RUNTIME
ALL = 0x1FFF7FFF


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if not SA.ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {SA.ZIG}")
    d = tmp_path_factory.mktemp("all_upgrades")
    (d / "driver.c").write_text(SA.DRIVER)
    out = d / "au"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
    done = subprocess.run([str(SA.ZIG), "cc", *SA.CC, str(d / "driver.c"), str(RUNTIME / "sa2_tweaks.c"),
                           str(RUNTIME / "tweaks" / "start-assists.c"),
                           str(RUNTIME / "tweaks" / "all-upgrades.c"), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return out


run, player = SA.run, SA.player
ON = {"SA2_TWEAKS": "all-upgrades"}
REPORT = "sa2 tweaks: all upgrades (SA2_TWEAKS=all-upgrades)"


@pytest.mark.parametrize("env", [{}, {"SA2_TWEAKS": ""}, {"SA2_TWEAKS": "no-event-bars"},
                                 {"SA2_TWEAKS": "all-upgrades", "SA2_REPLAY_TICKS": "/dev/null"},
                                 {"SA2_TWEAKS": "all-upgrades", "SA2_REPLAY_WORDS": "/dev/null"}])
def test_off_calls_the_lifted_body_and_touches_no_guest_memory(exe, env):
    out, err = run(exe, *player("0,0", pw=0x0001, up=0x5), "frame", "frame", **env)
    assert out == ["0001 00000005 1 0 0 00C0FFEE"] * 2
    assert not [line for line in err if "all upgrades" in line]


def test_sonic_gets_every_upgrade_once_and_powerups_are_left_alone(exe):
    out, err = run(exe, *player("0,0", level=10), "frame", "frame", **ON)
    # The first frame writes Upgrades alone; the next reads it and writes nothing.
    assert out == ["0000 1FFF7FFF 1 6 1 00C0FFEE", "0000 1FFF7FFF 1 6 0 00C0FFEE"]
    assert err == [REPORT, "sa2 tweaks: all upgrades: Sonic in level 10: Upgrades 0x00000000 -> 0x1FFF7FFF"]


def test_super_sonics_bit_is_never_added_but_kept_when_there(exe):
    out, _ = run(exe, *player("0,0", up=0x8010), "frame", "up=0x10", "frame", **ON)
    assert out == ["0000 1FFFFFFF 1 6 1 00C0FFEE", "0000 1FFF7FFF 1 6 1 00C0FFEE"]


@pytest.mark.parametrize("char, name", [
    ("0,0", "Sonic"), ("1,1", "Shadow"), ("2,2", "Tails"), ("3,3", "Eggman"), ("4,4", "Knuckles"),
    ("5,5", "Rouge"), ("6,6", "Mech Tails"), ("7,7", "Mech Eggman")])
def test_each_character_playing_its_own_code_gets_every_set(exe, char, name):
    out, err = run(exe, *player(char, up=0x4, level=11), "frame", **ON)
    assert out == ["0000 1FFF7FFF 1 6 1 00C0FFEE"]
    assert err[1] == f"sa2 tweaks: all upgrades: {name} in level 11: Upgrades 0x00000004 -> 0x1FFF7FFF"


@pytest.mark.parametrize("level", [19, 42])
@pytest.mark.parametrize("char, name, own", [("0,0", "Sonic", 0x0000003F), ("1,1", "Shadow", 0x000F0000)])
def test_in_the_sonic_vs_shadow_fights_each_hedgehog_gets_only_his_own_set(exe, level, char, name, own):
    # Sonic_Display serves both and draws by the bits alone, and both hedgehogs' model slots are
    # loaded in these two levels, so the other's bits would draw the other's upgrade models.
    out, err = run(exe, *player(char, level=level), "frame", "frame", **ON)
    assert out == [f"0000 {own:08X} 1 6 1 00C0FFEE", f"0000 {own:08X} 1 6 0 00C0FFEE"]
    assert err == [REPORT, f"sa2 tweaks: all upgrades: {name} in level {level}: Upgrades 0x00000000 -> "
                           f"0x{own:08X} (own upgrades only: the Sonic vs Shadow fight)"]


@pytest.mark.parametrize("level", [18, 20, 41, 43])
@pytest.mark.parametrize("char", ["0,0", "1,1"])
def test_outside_the_two_fights_each_hedgehog_gets_every_set(exe, level, char):
    out, _ = run(exe, *player(char, level=level), "frame", **ON)
    assert out == [f"0000 {ALL:08X} 1 6 1 00C0FFEE"]


def test_the_fight_rule_follows_the_level_as_it_changes(exe):
    # The game loads the character anew in each level; "up=0" stands for that between levels.
    out, _ = run(exe, *player("1,1", level=42), "frame", "level=13", "frame", "up=0", "level=19", "frame",
                 **ON)
    assert out == ["0000 000F0000 1 6 1 00C0FFEE", f"0000 {ALL:08X} 1 6 1 00C0FFEE",
                   "0000 000F0000 1 6 1 00C0FFEE"]


@pytest.mark.parametrize("level", [19, 42])
@pytest.mark.parametrize("char, own", [("0,0", "0000003F"), ("1,1", "000F0000")])
def test_with_start_assists_the_fights_still_give_only_the_hedgehogs_own_set(exe, level, char, own):
    # start-assists shares the wrap and gives the character's own set; all-upgrades adds none of
    # the other hedgehog's bits on top. (rival-upgrades leaves Shadow alone in these two levels for
    # the same reason, so with it on too Shadow still holds only his own bits.)
    out, _ = run(exe, *player(char, level=level), "frame", SA2_TWEAKS="start-assists,all-upgrades")
    assert out[0].split()[1] == own


@pytest.mark.parametrize("char, name, code", [
    ("0,8", "Amy", "Sonic"), ("1,12", "Metal Sonic", "Shadow"), ("6,13", "Chao Walker", "Mech Tails"),
    ("7,14", "Dark Chao Walker", "Mech Eggman"), ("4,15", "Tikal", "Knuckles"), ("5,16", "Chaos", "Rouge"),
    ("0,9", "Super Sonic", "Sonic"), ("0,40", "character 40", "Sonic")])
def test_a_character_playing_another_characters_code_is_left_alone_and_said(exe, char, name, code):
    out, err = run(exe, *player(char, up=0x4), "frame", "frame", **ON)
    assert out == ["0000 00000004 1 6 0 00C0FFEE"] * 2
    assert err == [REPORT, f"sa2 tweaks: all upgrades: {name} in level 13: left alone (it plays {code}'s code)"]


@pytest.mark.parametrize("char, name", [("8,8", "Amy"), ("12,12", "Metal Sonic"), ("40,40", "character 40")])
def test_a_character_past_mech_eggman_with_its_own_code_is_left_alone(exe, char, name):
    out, err = run(exe, *player(char), "frame", **ON)
    assert out == ["0000 00000000 1 6 0 00C0FFEE"]
    assert err[1] == f"sa2 tweaks: all upgrades: {name} in level 13: left alone (it has no upgrades of its own)"


@pytest.mark.parametrize("char", ["6,6", "7,7"])
def test_the_walkers_hit_points_are_left_to_the_game(exe, char):
    # The loader's InitMechHP gave 10.0 (0x41200000) from the saved bits alone. The walker's own
    # routine runs InitMechHP again at game state 7, once the armor bit is held, which lifts Mech
    # Eggman to 12.0, so the tweak writes Upgrades and nothing at +0x48.
    out, _ = run(exe, *player(char, up=0x4), "hp=0x41200000", "frame", "hp?", "frame", "hp?", **ON)
    assert out == ["0000 1FFF7FFF 1 6 1 00C0FFEE", "41200000", "0000 1FFF7FFF 1 6 0 00C0FFEE", "41200000"]


def test_null_pointers_are_skipped_without_a_write(exe):
    out, err = run(exe, "task=0", "frame", f"task={SA.TASK:#x}", "cp=0", "frame", **ON)
    assert out == ["0000 00000000 1 1 0 00C0FFEE", "0000 00000000 1 2 0 00C0FFEE"]
    assert err == [REPORT]


def test_two_player_mode_is_left_alone_and_said_once(exe):
    out, err = run(exe, *player("0,0"), "two=1", "frame", "frame", **ON)
    assert out == ["0000 00000000 1 3 0 00C0FFEE"] * 2
    assert err == [REPORT, "sa2 tweaks: all upgrades: two-player mode is left alone"]


def test_an_unreadable_character_is_left_alone_and_said_once(exe):
    out, err = run(exe, f"task={SA.TASK:#x}", f"cp={SA.UNMAPPED:#x}", "frame", "frame", **ON)
    assert out == ["0000 00000000 1 4 0 00C0FFEE"] * 2
    assert err == [REPORT, "sa2 tweaks: all upgrades: player 1's CharObj2Base at 0x0F000000 does not "
                           "read or write, so this frame is left alone"]


def test_each_character_and_level_is_named_once(exe):
    _, err = run(exe, *player("0,0"), "frame", "level=14", "frame", "level=13", "frame",
                 "char=4,4", "frame", "char=0,8", "frame", **ON)
    assert err[1:] == [
        "sa2 tweaks: all upgrades: Sonic in level 13: Upgrades 0x00000000 -> 0x1FFF7FFF",
        "sa2 tweaks: all upgrades: Sonic in level 14: Upgrades 0x1FFF7FFF -> 0x1FFF7FFF",
        "sa2 tweaks: all upgrades: Knuckles in level 13: Upgrades 0x1FFF7FFF -> 0x1FFF7FFF",
        "sa2 tweaks: all upgrades: Amy in level 13: left alone (it plays Sonic's code)"]


def test_with_start_assists_both_act_and_upgrades_are_the_union(exe):
    out, err = run(exe, *player("4,4", up=0x8000), "frame", "frame",
                   SA2_TWEAKS="start-assists,all-upgrades")
    # start-assists writes Powerups and Knuckles' own bits, then all-upgrades the rest.
    assert out == ["8003 1FFFFFFF 1 13 3 00C0FFEE", "8003 1FFFFFFF 1 13 0 00C0FFEE"]
    assert err == ["sa2 tweaks: all upgrades, start assists (SA2_TWEAKS=start-assists,all-upgrades)",
                   "sa2 tweaks: start assists: Knuckles in level 13: Powerups 0x0000 -> 0x8003, "
                   "Upgrades 0x00008000 -> 0x0000FC00",
                   "sa2 tweaks: all upgrades: Knuckles in level 13: Upgrades 0x0000FC00 -> 0x1FFFFFFF"]


def test_start_assists_alone_still_gives_only_the_characters_own_upgrades(exe):
    out, err = run(exe, *player("0,0"), "frame", SA2_TWEAKS="start-assists")
    assert out == ["8003 0000003F 1 7 2 00C0FFEE"]
    assert not [line for line in err if "all upgrades" in line]


def test_with_start_assists_amy_gets_the_powerups_and_no_upgrades(exe):
    out, err = run(exe, *player("0,8"), "frame", SA2_TWEAKS="start-assists,all-upgrades")
    assert out == ["8003 00000000 1 13 1 00C0FFEE"]
    assert err[2] == "sa2 tweaks: all upgrades: Amy in level 13: left alone (it plays Sonic's code)"


def test_the_android_note_says_what_the_save_keeps():
    java = (ROOT / "host" / "android" / "java" / "com" / "sa2recomp" / "app" /
            "GameSettings.java").read_text()
    line = next(x for x in java.splitlines() if 'new Tweak("all-upgrades"' in x)
    assert "not saved" in line and "upgrades you pick up" in line and "Lost Chao" in line, line
    assert "One-player only" in line and "Amy, Metal Sonic" in line, line
    assert "Sonic vs Shadow fights" in line and "only his own upgrades" in line, line
