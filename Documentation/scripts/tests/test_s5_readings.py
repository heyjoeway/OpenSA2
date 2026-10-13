"""G5's readings, as the user confirmed them, held to the user's records.

The user's ruling 4 of 2026-10-04 (`Documentation/decisions/2026-10-04-S5-user-rulings.md`):
"confirm all six readings." and "Test-spawn starts are approved for G5; S6's D1 still requires
normal entry." The record lists the six. This file ties each value the S5-exit predicates hold
(`Documentation/scripts/check_s5_exit.py`) to the record's own words, so a constant loosened
together with its test in test_s5_exit.py still fails here, by name of the user's record; and it
holds the play, audible and identical notes in `Documentation/gate-criteria.json` to what the
rulings settled: they quote them and put nothing back to the user. The one reading not among the
six, that the hosts criterion's pictures suffice for visible, the user confirmed in the second
rulings of the same day (`Documentation/decisions/2026-10-04-S5-user-rulings-2.md`), answering
"Visible" with "accepted as you proposed. The identical-frame-hash clause on Metal Harbor's
registered replay in both hosts carries it." So no note of the three is left for the user to
confirm.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_durable_records_fresh as D  # noqa: E402
import check_s5_exit as X  # noqa: E402

RULINGS = "Documentation/decisions/2026-10-04-S5-user-rulings.md"
RULINGS_2 = "Documentation/decisions/2026-10-04-S5-user-rulings-2.md"
DESIGN = "Documentation/SA2_RECOMP_DESIGN.md"
CARRY_FORWARD = "Documentation/S5_CARRY_FORWARD.md"
CONFIRMED = "confirm all six readings."
SPAWN = "Test-spawn starts are approved for G5; S6's D1 still requires normal entry."
RULING_2 = ("The real game on this machine already renders through DXVK's translation under "
            "Proton, so sharing it costs little independence, and the two-host comparison still "
            "checks everything else in host 2.")
# The six as the record lists them under ruling 4, in its order.
SIX = ('"audible" is at least one second at -40 dBFS RMS or louder;',
       "the four scenes are the title, the main menu, a Chao Garden and Metal Harbor;",
       '"playable" means a person plays to the goal ring in each host;',
       '"identically" means exact frame hashes;',
       "the two hosts are DXVK Native and the SDL3 GPU renderer;",
       "test-spawn starts count.")
# The second rulings on visible, the user's words as the record quotes them.
VISIBLE = ("accepted as you proposed. The identical-frame-hash clause on Metal Harbor's registered "
           "replay in both hosts carries it.")
VISIBLE_CONFIRMED = ("That this suffices for visible was the agent's reading, argued from design "
                     "7.4's Observed tier, \"Presentation compared by screenshot hash, not "
                     f"state\"; the user confirmed it on 2026-10-04 ({RULINGS_2}), answering that "
                     f"visible is \"{VISIBLE}\"")


def flat(text: str) -> str:
    """Prose with its Markdown quoting, emphasis and line breaks taken out."""
    lines = (line.strip().lstrip(">").strip() for line in text.splitlines())
    return " ".join(" ".join(lines).replace("**", "").split())


def record() -> str:
    return flat((ROOT / RULINGS).read_text())


def note(name: str) -> str:
    found = [entry for entry in json.loads((ROOT / "Documentation/gate-criteria.json").read_text())
             ["G5"] if entry["criterion"] == X.CRITERIA[name]]
    assert len(found) == 1, name
    return found[0]["criterion_note"]


# ------------------------------------------------------------------ the record and the module


def test_the_record_lists_the_six_readings_the_user_confirmed() -> None:
    text = record()
    assert f"4: {CONFIRMED} {SPAWN}" in text
    ruling = text.split("4. G5's readings: all six confirmed. ", 1)[1]
    assert ruling.startswith("- The six readings are confirmed as written in "
                             "`agents-journals/CURRENT.md`: - " + " - ".join(SIX))


def test_audible_is_the_record_s_threshold_over_one_second() -> None:
    """Ruling 4: "audible" is at least one second at the record's dBFS RMS or louder. The
    threshold is read off the record, and a second is the mixer's sample rate in frames."""
    found = re.search(r'"audible" is at least one second at (-\d+) dBFS RMS or louder;', record())
    assert found, RULINGS
    assert X.AUDIBLE_DBFS == float(found.group(1)), RULINGS
    assert (X.SAMPLE_RATE, X.CHANNELS) == (48000, 2)


# Literal values, so that a definition moved together with test_s5_exit.py's header reading still
# fails here (readings review, finding 4). From the Mod Loader's SA2Enums.h: GameMode_Advertise 14,
# GameMode_Level 5, GameStates_Ingame 16; Menus_TitleScreen 0, Menus_Main 1; LevelIDs_MetalHarbor
# 10, LevelIDs_ChaoWorld 90; ChaoArea Lobby 0, the Neutral, Hero and Dark gardens 1 to 3, and the
# other areas Race 4, Entrance 5, Kindergarten 6, Stadium 9, Karate 10 and NameMachine 12.
METAL_HARBOR = 10
NOT_GARDENS = (0, 4, 5, 6, 9, 10, 12)


@pytest.mark.parametrize(("scene", "row", "holds"), [
    ("title", {"game_mode": 14, "current_menu": 0}, True),
    ("title", {"game_mode": 14, "current_menu": 1}, False),
    ("title", {"game_mode": 5, "current_menu": 0}, False),
    ("menu", {"game_mode": 14, "current_menu": 1}, True),
    ("menu", {"game_mode": 14, "current_menu": 0}, False),
    ("menu", {"game_mode": 14, "current_menu": 2}, False),
    ("menu", {"game_mode": 14, "current_menu": 5}, False),
    ("menu", {"game_mode": 5, "current_menu": 1}, False),
    ("garden", {"current_level": 90, "chao_area": 1}, True),
    ("garden", {"current_level": 90, "chao_area": 2}, True),
    ("garden", {"current_level": 90, "chao_area": 3}, True),
    *(("garden", {"current_level": 90, "chao_area": area}, False) for area in NOT_GARDENS),
    ("garden", {"current_level": METAL_HARBOR, "chao_area": 1}, False),
    ("metalharbor", {"current_level": METAL_HARBOR, "game_mode": 5, "game_state": 16}, True),
    ("metalharbor", {"current_level": METAL_HARBOR, "game_mode": 5, "game_state": 15}, False),
    ("metalharbor", {"current_level": 11, "game_mode": 5, "game_state": 16}, False),
    ("metalharbor", {"current_level": 13, "game_mode": 5, "game_state": 16}, False),
    ("metalharbor", {"current_level": METAL_HARBOR, "game_mode": 14, "game_state": 16}, False),
])
def test_the_scenes_are_the_record_s_four(scene: str, row: dict, holds: bool) -> None:
    """Ruling 4: the title, the main menu (not any menu), a Chao Garden (not the lobby nor any
    other Chao area) and Metal Harbor (not any level) in play, in that order. The level passed is
    the registry's, and the registry's is Metal Harbor's."""
    assert X.SCENE_ORDER == ("title", "menu", "garden", "metalharbor"), RULINGS
    assert X.stage_level(ROOT) == METAL_HARBOR, RULINGS
    full = {"game_mode": 0, "current_menu": -1, "current_level": 0, "game_state": 0,
            "chao_area": -1} | row
    assert X.scene_holds(scene, full, X.stage_level(ROOT)) is holds


def test_the_hosts_are_the_record_s_two() -> None:
    assert X.HOSTS == (("dxvk-native", "DXVK Native"), ("sdl3-gpu", "SDL3's GPU API")), RULINGS
    assert X.selection(ROOT) == ["dxvk-native", "sdl3-gpu"]


def test_identically_is_exact_frame_hashes() -> None:
    assert "bgrx_hash" in X.COMPARED and "wall_us" not in X.COMPARED, RULINGS
    assert X.BGRX_HASH.pattern == r"0x[0-9a-f]{16}\Z"


def test_playable_is_a_person_at_the_window() -> None:
    assert (X.PLAY_META["interactive"], X.INPUT_SOURCE, X.MIN_KEY_EVENTS) == (True, "fifo", 1)
    assert X.RUN_KEYBOARD[0] == "script_lines", RULINGS


# ------------------------------------------------------------------ the notes

NOTES = ("play", "audible", "identical")


def test_the_confirmed_notes_put_nothing_confirmed_to_the_user() -> None:
    """Every reading the three notes make is confirmed, visible's by the second rulings: none is
    put to the user, called the agent's choice or left for the user to decide, and the play note
    says visible is confirmed, in the record's words, and no longer that it stays open. Outside
    the quoted "confirm all six readings." no note says "to confirm", "to decide" or "to rule" at
    all (the g5-residuals review, NIT 4: "visible is the user's to confirm" passed the fixed
    phrases)."""
    counts = {name: note(name).count("for the user to confirm") for name in NOTES}
    assert counts == {"play": 0, "audible": 0, "identical": 0}
    assert VISIBLE_CONFIRMED in note("play")
    assert f'"Visible": {VISIBLE}' in flat((ROOT / RULINGS_2).read_text())
    for name in NOTES:
        bare = note(name).replace(f'"{CONFIRMED}"', "").lower()
        for words in ("confirm or overrule", "the agent's choice", "for the user to decide",
                      "The route there is for the user", "stays open", "is not one of the six "
                      "readings the user confirmed", "to confirm", "to decide", "to rule",
                      "the user's to"):
            assert words.lower() not in bare, (name, words)


def test_the_play_note_admits_only_the_approved_starts() -> None:
    """Ruling 4 approved test-spawn starts for G5, not every route (readings review, finding 1).
    The note names the menus and the test spawn at the level's own start, counts a test-spawn
    start toward nothing at S6 (the ruling's second half, the I1 review's finding 1), and says the check
    holds neither the route nor the start position; the writer's side of that claim is held
    here: the lifted test spawn reads only a level and a character, and play_live sets only those
    two and a guest command line with no --position."""
    text = note("play")
    for words in ("through the menus or the Mod Loader's test spawn, at the level's own start",
                  "toward nothing at S6, whose D1 still requires normal entry",
                  "not the route there nor where the player starts: both rest on the receipt's "
                  "writer", "--position", "harness/replay/wrap_test_spawn.c reproduces only its "
                  "--level and --character"):
        assert words in text, words
    assert "by any route" not in text
    wrap = (ROOT / "harness/replay/wrap_test_spawn.c").read_text()
    assert set(re.findall(r'getenv\("(SA2_TEST_SPAWN_\w+)"\)', wrap)) == {
        "SA2_TEST_SPAWN_LEVEL", "SA2_TEST_SPAWN_CHARACTER"}
    live = (ROOT / "tools/s5/play_live.py").read_text()
    assert set(re.findall(r"SA2_TEST_SPAWN_\w+", live)) == {
        "SA2_TEST_SPAWN_LEVEL", "SA2_TEST_SPAWN_CHARACTER"}
    stages = re.search(r"^STAGES = .*$", (ROOT / "tools/s4/stage_run.py").read_text(), re.M)
    assert stages and "--level metalharbor --character sonic" in stages.group(0)
    assert "--position" not in stages.group(0) and "--position" not in live


# Sentences the three notes quote, and the record each is quoted from.
QUOTED_READINGS = {
    "play": [(CONFIRMED, RULINGS), (SPAWN, RULINGS), (VISIBLE, RULINGS_2),
             ("Presentation compared by screenshot hash, not state", DESIGN)],
    "audible": [(CONFIRMED, RULINGS), (SPAWN, RULINGS)],
    "identical": [(CONFIRMED, RULINGS), (RULING_2, RULINGS)],
}


@pytest.mark.parametrize("name", NOTES)
def test_the_notes_quote_the_rulings_word_for_word(name: str) -> None:
    text = note(name)
    assert RULINGS in text, name
    for record in {source for _, source in QUOTED_READINGS[name]} - {DESIGN}:
        assert record in text, (name, record)     # each ruling quoted is cited by its path
    for quote, source in QUOTED_READINGS[name]:
        assert f'"{quote}"' in text, (name, quote)
        assert quote in flat((ROOT / source).read_text()), (quote, source)


def test_the_audible_note_points_at_the_carry_forward_item() -> None:
    """Whether the sound is the game's went to the carry-forward, as the user asked."""
    assert CARRY_FORWARD in note("audible")
    assert D.S5_SPEC.path == CARRY_FORWARD
    word = D.S5_SPEC.ruled[0][0]
    titles = [title for title, _ in D.carry_forward_items((ROOT / CARRY_FORWARD).read_text())]
    assert [title for title in titles if word in title.lower()] == [
        "An audio check against the real game's audio, beyond audible"]


def test_the_m7_note_claims_no_confirmation() -> None:
    """M7's numerator question is not among the six: the M7 note does not quote ruling 4's
    confirmation of the readings. It may cite the record for ruling 1, which is M7's (the M7
    plan, section 2.5); whether its question stays open is the M7 entry's writer's, not this
    file's."""
    assert CONFIRMED not in note("m7")
