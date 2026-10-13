"""The S5 carry-forward keeps its promise (check_durable_records_fresh.py, S5_SPEC).

`Documentation/S5_CARRY_FORWARD.md` is held by the S4 file's check, given the S5
file as a spec: every item carries **Evidence:** with a path that exists,
**Resume at:** and **Re-take:**, and each ruling that sent an item here has its
entry: the user's ruling 4 of 2026-10-04
(`Documentation/decisions/2026-10-04-S5-user-rulings.md`), one item titled for
the real game's audio, and the user's second rulings of the same day
(`Documentation/decisions/2026-10-04-S5-user-rulings-2.md`), one item titled for
the translated shaders not compiled or run on Metal, each citing its ruling and
carrying its sentence.
`main()` reads the S4 file alone, so this file is the S5 file's hold, run by the
script tests suite at every push. It reads the working tree only, where `main()`
reads the S4 file on the index as well (check_durable_records_fresh.py, "The S5
carry-forward", says what that leaves open).

The live file is checked first; then each way an item can break the promise is
shown on literal text, each held to the message the check gives.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_durable_records_fresh as D  # noqa: E402

S5 = "Documentation/S5_CARRY_FORWARD.md"
RULINGS = "Documentation/decisions/2026-10-04-S5-user-rulings.md"
SENTENCE = 'an audio check against the real game\'s audio, beyond "audible"'
RULINGS_2 = "Documentation/decisions/2026-10-04-S5-user-rulings-2.md"
METAL_SENTENCE = "The Metal compile-and-run check goes to carry-forward, as you proposed."
SDL_SENTENCE = ("This withdraws my earlier instruction to prepare it as an upstream contribution; "
                "nothing is submitted to SDL, respecting their AI policy.")
SDL_HOME = "patches/sdl3"
SDL_NOT_YET = f"The directory `{SDL_HOME}/` is not in the tree yet."


def flat(text: str) -> str:
    """Prose with its Markdown quoting, emphasis and line breaks taken out."""
    lines = (line.strip().lstrip(">").strip() for line in text.splitlines())
    return " ".join(" ".join(lines).replace("**", "").split())


def answer() -> str:
    """The user's answer as the second rulings' record quotes it: its question-and-answer section
    after the transcript line, before what the record commits the project to."""
    return flat((ROOT / RULINGS_2).read_text()).split(
        "Copied from the session transcript:", 1)[1].split("## What each part commits", 1)[0]


def test_the_live_s5_carry_forward_keeps_its_promise() -> None:
    assert D.carry_forward_problems(ROOT, spec=D.S5_SPEC) == []


def test_the_s5_spec_is_the_users_rulings() -> None:
    """The spec names the S5 file, no titles of the user's (those are S4's), and the two entries
    the rulings asked for, ruling 4's and the second rulings', each sentence the user's word for
    word."""
    assert D.S5_SPEC == D.CarryForward(S5, (), (
        ("real game's audio", RULINGS, SENTENCE),
        ("compiled or run on Metal", RULINGS_2, METAL_SENTENCE)))
    assert f'Add to the carry-forward file: {SENTENCE}.' in flat((ROOT / RULINGS).read_text())
    assert METAL_SENTENCE in answer()        # the user's answer as the record quotes it


def test_the_sdl_item_keeps_the_patch_in_this_repository() -> None:
    """The second rulings replaced ruling 3's last sentence: the SDL patch stays in this
    repository under patches/sdl3/ and nothing is prepared for or submitted to SDL. The SDL item
    cites them, quotes the user's withdrawal whole and word for word as the record quotes it, says
    nothing is prepared for or submitted to SDL, and no longer says the patch is prepared upstream
    or that submitting it is the user's. It says the directory is not in the tree yet exactly while
    that is so (check_cited_files.py does not hold a path that ends in a slash), so the landing
    that adds patches/sdl3/ fails here until the item stops saying it."""
    [body] = [body for title, body in D.carry_forward_items((ROOT / S5).read_text())
              if title.startswith("The SDL patch's")]
    text = " ".join(body.split())
    assert RULINGS_2 in text and f"`{SDL_HOME}/`" in text
    assert f'"{SDL_SENTENCE}"' in text       # quoted whole in the item
    assert SDL_SENTENCE in answer()          # the user's answer as the record quotes it
    assert "nothing is prepared for or submitted to SDL." in text
    assert (SDL_NOT_YET in text) is not (ROOT / SDL_HOME).is_dir()
    for words in ("prepared as an upstream contribution to SDL", "upstream form",
                  "Submitting"):
        assert words not in text, words


def test_the_default_spec_is_still_the_s4_file() -> None:
    assert D.S4_SPEC == D.CarryForward("Documentation/S4_CARRY_FORWARD.md",
                                       D.CARRY_FORWARD_NAMED, D.CARRY_FORWARD_RULED)
    assert D.carry_forward_problems(ROOT, text="# S4\n", exists=lambda rel: True) == [
        "Documentation/S4_CARRY_FORWARD.md has no items (no `##` sections)"]


# ------------------------------------------------------------------ literal text

EVIDENCE = "tools/s5/stream_dump.py"
AUDIO_ITEM = (
    "## An audio check against the real game's audio, beyond audible\n\n"
    f"The user's ruling 4 (`{RULINGS}`): \"Add to the carry-forward file: an audio check\n"
    "against the real game's audio, beyond \"audible\".\"\n\n"
    f"**Evidence:** `{RULINGS}`.\n\n"
    "**Resume at:** a native capture of what the game submits.\n\n"
    "**Re-take:** yes.\n\n")
OTHER_ITEM = (
    "## Another deferral\n\n"
    "Text.\n\n"
    f"**Evidence:** `{EVIDENCE}`.\n\n"
    "**Resume at:** here.\n\n"
    "**Re-take:** no.\n\n")
TITLE = "An audio check against the real game's audio, beyond audible"
METAL_ITEM = (
    "## The translated shaders are not compiled or run on Metal\n\n"
    f"The user's second rulings (`{RULINGS_2}`): \"The Metal compile-and-run check goes\n"
    "to carry-forward, as you proposed.\"\n\n"
    f"**Evidence:** `{RULINGS_2}`.\n\n"
    "**Resume at:** a Mac.\n\n"
    "**Re-take:** no.\n\n")
METAL_TITLE = "The translated shaders are not compiled or run on Metal"


def problems(*items: str) -> list[str]:
    text = "# S5 carry-forward\n\nPreamble.\n\n" + "".join(items)
    return D.carry_forward_problems(ROOT, text=text,
                                    exists={RULINGS, RULINGS_2, EVIDENCE}.__contains__,
                                    spec=D.S5_SPEC)


def test_conforming_text_passes() -> None:
    assert problems(AUDIO_ITEM, METAL_ITEM, OTHER_ITEM) == []


@pytest.mark.parametrize(("items", "said"), [
    ((METAL_ITEM, OTHER_ITEM),
     f"{S5} has 0 items titled for \"real game's audio\", and the user's ruling ({RULINGS}) "
     "asks for one"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM, AUDIO_ITEM.replace("beyond audible", "again")),
     f"{S5} has 2 items titled for \"real game's audio\", and the user's ruling ({RULINGS}) "
     "asks for one"),
    ((AUDIO_ITEM.replace(f"`{RULINGS}`", f"`{EVIDENCE}`"), METAL_ITEM, OTHER_ITEM),
     f"{S5} item {TITLE!r} does not cite the ruling it answers, {RULINGS}"),
    ((AUDIO_ITEM.replace("real game's audio, beyond \"audible\"",
                         "real game's sound, beyond \"audible\""), METAL_ITEM, OTHER_ITEM),
     f"{S5} item {TITLE!r} does not carry the ruling's sentence: {SENTENCE!r}"),
    ((AUDIO_ITEM.replace("**Re-take:** yes.", "**Re-take:**"), METAL_ITEM, OTHER_ITEM),
     f"{S5} item {TITLE!r} has no **Re-take:** (whether resuming needs a native re-take)"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM.replace(f"`{EVIDENCE}`", "TBD")),
     [f"{S5} item 'Another deferral' has no **Evidence:** (its evidence)",
      f"{S5} item 'Another deferral' cites no repository path under **Evidence:**, so nothing "
      "shows what it rests on"]),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM.replace(f"`{EVIDENCE}`", "`tools/s5/not_there.py`")),
     f"{S5} item 'Another deferral' cites evidence that does not exist: "
     "tools/s5/not_there.py"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM.replace(f"`{EVIDENCE}`", "the review")),
     f"{S5} item 'Another deferral' cites no repository path under **Evidence:**, so nothing "
     "shows what it rests on"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM.replace("**Re-take:** no.", "**Re-take:** maybe.")),
     f"{S5} item 'Another deferral' does not answer **Re-take:** with yes, no or not, so "
     "whether resuming needs a native re-take is unsaid"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM.replace("**Resume at:** here.\n\n", "")),
     f"{S5} item 'Another deferral' has no **Resume at:** (where to resume)"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM.replace(f"`{EVIDENCE}`", f"`{S5}`")),
     f"{S5} item 'Another deferral' cites no repository path under **Evidence:**, so nothing "
     "shows what it rests on"),
    ((AUDIO_ITEM, OTHER_ITEM),
     f"{S5} has 0 items titled for 'compiled or run on Metal', and the user's ruling "
     f"({RULINGS_2}) asks for one"),
    ((AUDIO_ITEM, METAL_ITEM, OTHER_ITEM, METAL_ITEM.replace("on Metal\n", "on Metal, again\n")),
     f"{S5} has 2 items titled for 'compiled or run on Metal', and the user's ruling "
     f"({RULINGS_2}) asks for one"),
    ((AUDIO_ITEM, METAL_ITEM.replace(f"`{RULINGS_2}`", f"`{EVIDENCE}`"), OTHER_ITEM),
     f"{S5} item {METAL_TITLE!r} does not cite the ruling it answers, {RULINGS_2}"),
    ((AUDIO_ITEM, METAL_ITEM.replace("as you proposed", "as agreed"), OTHER_ITEM),
     f"{S5} item {METAL_TITLE!r} does not carry the ruling's sentence: {METAL_SENTENCE!r}"),
    ((AUDIO_ITEM, METAL_ITEM.replace("compiled or run on Metal", "compiled on Metal"),
      OTHER_ITEM),
     f"{S5} has 0 items titled for 'compiled or run on Metal', and the user's ruling "
     f"({RULINGS_2}) asks for one"),
], ids=["no audio item", "two audio items", "no ruling path", "sentence changed",
        "empty re-take", "placeholder evidence", "absent evidence", "evidence names no path",
        "re-take unanswered", "no resume", "the file as its own evidence", "no metal item",
        "two metal items", "no second-rulings path", "metal sentence changed",
        "metal item retitled"])
def test_each_broken_promise_is_named(items: tuple[str, ...], said: str | list[str]) -> None:
    assert problems(*items) == (said if isinstance(said, list) else [said])


def test_no_items_at_all() -> None:
    assert D.carry_forward_problems(ROOT, text="# S5 carry-forward\n\nPreamble only.\n",
                                    exists=lambda rel: True, spec=D.S5_SPEC) == [
        f"{S5} has no items (no `##` sections)"]
