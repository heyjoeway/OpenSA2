"""The swap-audio host tweak (host/player/tweaks/swap-audio.cpp; runtime/sa2_tweaks.h
SA2_TWEAK_HOST_LIST, home "player"): in play, the player's left channel plays right and its right
channel left.

The tweak acts on the mixer's block in player.cpp's audio_block, right after mono-audio's call and
before the block is written to --audio-wav and queued to the device. The streams and the player's
helpers are test_tweak_mono_audio.py's, and the two levels are its levels:

  * the swap, with the system g++: the mixer, the host's reader and both audio tweaks' files, linked
    with a driver that does what audio_block does with each block (mono-audio, swap-audio, then the
    WAV). A tone panned hard left plays hard right; two different channels trade places to the bit;
    with mono-audio on as well the WAV is mono-audio's alone, and the swap's line says it changes
    nothing. Off is the mixer to the byte: with the value unset, naming a game tweak only, out of
    play and in a replay, the driver's WAV is the plain renderer's, byte for byte.
  * the player itself (host 2's, built by host/d3d9gpu/build.sh unless SA2_PLAYER_GPU names one):
    the hard-left stream played live in play with SA2_TWEAKS=swap-audio writes the tone in the right
    channel and says so; with SA2_TWEAKS unset, in the left. Replayed from its file with the tweak
    named, the tone stays left and the value is said to be ignored. It needs a display.

  .venv/bin/python -m pytest Documentation/scripts/tests/test_tweak_swap_audio.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("tweak_mono_audio", Path(__file__).with_name("test_tweak_mono_audio.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)
A, PLAYER, N = M.A, M.PLAYER, M.N

LINE = "sa2 host tweaks: swap audio: "
SAID = LINE + ("the left channel plays right and the right left, after the limiter, in the speakers and "
               "--audio-wav; the report's audio levels are the mix before it")
SAID_WITH_MONO = LINE + "changes nothing with mono audio on, which plays the same sound in both channels"

# What audio_block does with each block, in its order: mono-audio, swap-audio, then the WAV.
DRIVER = M.DRIVER.replace('#include "player/tweaks/mono-audio.h"\n',
                          '#include "player/tweaks/mono-audio.h"\n#include "player/tweaks/swap-audio.h"\n') \
                 .replace("        sa2tw::mono_audio(mx);\n", "        sa2tw::mono_audio(mx);\n        sa2tw::swap_audio(mx);\n")
assert DRIVER.count("sa2tw::swap_audio(mx);") == 1 and DRIVER.count("swap-audio.h") == 1


@pytest.fixture(scope="module")
def exes(tmp_path_factory) -> dict:
    if not M.GXX:
        pytest.fail("g++ is not on PATH: the player's objects are built with it (host/player/build.sh)")
    d = tmp_path_factory.mktemp("swap")
    (d / "driver.cpp").write_text(DRIVER)
    out = {"driver": d / "driver", "render": d / "sa2_audio_render", "dir": d}
    tweaks = [PLAYER / "tweaks.cpp", PLAYER / "tweaks" / "mono-audio.cpp", PLAYER / "tweaks" / "swap-audio.cpp"]
    for cmd in ([M.GXX, *M.FLAGS, "-o", str(out["driver"]), str(d / "driver.cpp"), *map(str, M.SOURCES),
                 *map(str, tweaks), "-lzstd"],
                [M.GXX, *M.FLAGS, "-DSA2_MIXER_MAIN", "-o", str(out["render"]), *map(str, M.SOURCES), "-lzstd"]):
        done = subprocess.run(cmd, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    return out


def test_a_tone_panned_hard_left_plays_hard_right(exes, tmp_path):
    _, off, err_off = M.drive(exes, tmp_path, M.stream(M.HARD_LEFT), {}, name="off")
    _, on, err_on = M.drive(exes, tmp_path, M.stream(M.HARD_LEFT), {"SA2_TWEAKS": "swap-audio"}, name="on")
    tone = M.frames_of(M.HARD_LEFT)
    assert off == [tone[n % 48] for n in range(N)]
    assert on == [(0.0, tone[n % 48][0]) for n in range(N)]
    assert max(r for _, r in on) == 0.5 and [l for l, _ in on] == [0.0] * N
    assert err_off == ""
    assert err_on.splitlines() == ["sa2 host tweaks (player): swap audio (SA2_TWEAKS=swap-audio)", SAID]


def test_two_channels_trade_places_to_the_bit(exes, tmp_path):
    wav_off, off, _ = M.drive(exes, tmp_path, M.stream(M.TWO_CH), {}, name="off")
    wav_on, on, _ = M.drive(exes, tmp_path, M.stream(M.TWO_CH), {"SA2_TWEAKS": "no-event-bars, swap-audio"}, name="on")
    assert on == [(r, l) for l, r in off]
    assert sum(l != r for l, r in off) > N // 2               # the swap moved something
    # As bytes: the WAV's data is the off WAV's with each 4-byte float pair's halves exchanged.
    i = wav_off.index(b"data") + 8
    assert wav_on[:i] == wav_off[:i]
    body = wav_off[i:]
    assert wav_on[i:] == b"".join(body[k + 4:k + 8] + body[k:k + 4] for k in range(0, len(body), 8))


def test_with_mono_audio_on_the_swap_changes_nothing_and_says_so(exes, tmp_path):
    wav_mono, mono, _ = M.drive(exes, tmp_path, M.stream(M.TWO_CH), {"SA2_TWEAKS": "mono-audio"}, name="mono")
    wav_both, both, err = M.drive(exes, tmp_path, M.stream(M.TWO_CH), {"SA2_TWEAKS": "swap-audio,mono-audio"},
                                  name="both")
    assert wav_both == wav_mono and all(l == r for l, r in both)
    assert err.splitlines() == [
        "sa2 host tweaks (player): mono audio, swap audio (SA2_TWEAKS=swap-audio,mono-audio)",
        M.LINE + "both channels play (L + R) / 2, after the limiter, in the speakers and --audio-wav; "
                 "the report's audio levels are the mix before it",
        SAID_WITH_MONO]


def test_the_swap_keeps_a_limited_mix_as_the_limiter_left_it(exes, tmp_path):
    _, off, _ = M.drive(exes, tmp_path, M.loud(), {}, limit=True, name="off")
    _, on, _ = M.drive(exes, tmp_path, M.loud(), {"SA2_TWEAKS": "swap-audio"}, limit=True, name="on")
    assert len(on) == len(off) == 480
    assert on == [(r, l) for l, r in off]
    assert max(r for _, r in on) == M.f32(0.98)


@pytest.mark.parametrize("env, not_play, said", [
    ({}, "-", ""),
    ({"SA2_TWEAKS": ""}, "-", ""),
    ({"SA2_TWEAKS": "no-event-bars"}, "-", "sa2 host tweaks (player): none on (SA2_TWEAKS=no-event-bars)\n"),
    ({"SA2_TWEAKS": "swap-audio"}, "outside play",
     "sa2 host tweaks (player): SA2_TWEAKS=swap-audio ignored outside play\n"),
    ({"SA2_TWEAKS": "swap-audio,mono-audio", "SA2_REPLAY_WORDS": "x"}, "-",
     "sa2 host tweaks (player): SA2_TWEAKS=swap-audio,mono-audio ignored in a replay (SA2_REPLAY_TICKS or "
     "SA2_REPLAY_WORDS is set)\n"),
])
def test_off_is_the_mixers_wav_to_the_byte(exes, tmp_path, env, not_play, said):
    wav, _, err = M.drive(exes, tmp_path, M.stream(M.HARD_LEFT), env, not_play)
    assert wav == M.plain(exes, tmp_path, M.stream(M.HARD_LEFT))
    assert err == said


def test_the_player_swaps_in_audio_block_after_mono_and_before_both_readers():
    src = (PLAYER / "player.cpp").read_text()
    body = src[src.index("void Player::audio_block() {\n"):]
    assert body.splitlines()[1:3] == ["    sa2tw::mono_audio(*mixer_);", "    sa2tw::swap_audio(*mixer_);"]
    assert src.count("sa2tw::swap_audio(") == 1
    body = body[:body.index("\n}\n")]
    assert "SDL_PutAudioStreamData(" in body and "wav_.write(" in body


# ---------------------------------------------------------------- the player itself

@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        pytest.fail("no display: the interactive mode opens a window")
    t = tmp_path_factory.mktemp("swap_player")
    player = M.build_player(t / "bin")
    data = M.stream(M.HARD_LEFT)
    return {"on": M.play_live(player, data, t / "on", "swap-audio"), "off": M.play_live(player, data, t / "off", None),
            "replay": M.replay(player, data, t / "replay", "swap-audio")}


def test_the_player_in_play_swaps_its_wav_and_says_so(runs):
    on, off = runs["on"], runs["off"]
    assert on["exit"] == 0 and off["exit"] == 0, (on["err"] + on["out"])[-3000:]
    tone = M.frames_of(M.HARD_LEFT)
    assert off["frames"] == [tone[n % 48] for n in range(len(off["frames"]))] and len(off["frames"]) == N
    assert on["frames"] == [(r, l) for l, r in off["frames"]]
    assert "sa2 host tweaks (player): swap audio (SA2_TWEAKS=swap-audio)" in on["err"].splitlines()
    assert [line for line in on["err"].splitlines() if line.startswith(LINE)] == [SAID]
    assert "sa2 host tweaks" not in off["err"]


def test_the_player_out_of_play_leaves_the_sound_alone(runs):
    r = runs["replay"]
    assert r["exit"] == 0, r["err"][-3000:]
    tone = M.frames_of(M.HARD_LEFT)
    assert r["frames"] == [tone[n % 48] for n in range(N)]
    assert ("sa2 host tweaks (player): SA2_TWEAKS=swap-audio ignored outside play (play is --interactive with "
            "--input-path)") in r["err"].splitlines()
    assert LINE not in r["err"]
