"""The host tweak slow-mode (host/player/tweaks/slow-mode.cpp): the game at 0.75 of real time.

slow-mode's two calls in the player (sa2tw::slow_mode::start, once the live stream's schedule and
the sound's device stream are made, and slow_mode::finish at the sound's end) is compiled here with the host's reader (host/player/tweaks.cpp)
and a driver standing in for player.cpp, against the install's SDL3 (SA2_S5_INSTALL, else
private/s5-tools/install), with SDL's dummy audio driver, which plays a stream in real time:

  * off (SA2_TWEAKS unset, or naming another tweak, or in a replay): the player's schedule is the
    very object it made, at its speed and slack, the device stream's ratio stays 1, and nothing is
    said;
  * on: the schedule is at 0.75 of the player's speed, a frame one virtual second on is due 4/3 of
    a second on, the live slack is the sound's 60 ms lead over 0.75 (80 ms), the stream's
    frequency ratio is 0.75, and the line says so; the device plays 0.6 s of the game's sound in
    about 0.8 s of wall time, 4/3 of the time it takes off, and its queue empties at the end once
    finish() has flushed it (SDL's resampler holds its last frames until then);
  * the cases it leaves alone, each said by name: no schedule (play without --pace), nothing
    slowed; no device stream (--no-sound), the frames slowed alone.

  .venv/bin/python -m pytest Documentation/scripts/tests/test_tweak_slow_mode.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLAYER = ROOT / "host/player"

DRIVER = r"""
#include <SDL3/SDL.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <vector>
#include "tweaks.h"
#include "tweaks/slow-mode.h"

static double now() { return double(SDL_GetTicksNS()) / 1e9; }

// The wall time the device takes to play `seconds` of 48 kHz stereo float queued at once, as
// player.cpp's end waits for it (finish_audio: slow_mode::finish, then the queue empty), or
// without the call, until `limit` seconds; and the bytes still queued then.
static double drain(SDL_AudioStream* s, double seconds, bool finish, double limit, int* left) {
    std::vector<float> buf(size_t(48000 * seconds) * 2, 0.0f);
    const double t0 = now();
    SDL_PutAudioStreamData(s, buf.data(), int(buf.size() * sizeof(float)));
    if (finish) sa2tw::slow_mode::finish(s);
    while (SDL_GetAudioStreamQueued(s) > 0 && now() - t0 < limit) SDL_Delay(1);
    *left = SDL_GetAudioStreamQueued(s);
    return now() - t0;
}

int main(int argc, char** argv) {
    const char* mode = argv[1];   // "both", "no-pace", "no-audio"
    const char* drain_how = argc > 2 ? argv[2] : "";   // "drain" (with finish) or "hold" (without)
    sa2tw::g_on = sa2tw::read(std::getenv("SA2_TWEAKS"), sa2tw::off(nullptr), "player").on;
    if (!SDL_Init(SDL_INIT_AUDIO)) { std::printf("SDL_Init: %s\n", SDL_GetError()); return 2; }
    const SDL_AudioSpec spec{SDL_AUDIO_F32, 2, 48000};
    SDL_AudioStream* audio = nullptr;
    if (std::strcmp(mode, "no-audio") != 0) {
        audio = SDL_OpenAudioDeviceStream(SDL_AUDIO_DEVICE_DEFAULT_PLAYBACK, &spec, nullptr, nullptr);
        if (!audio || !SDL_ResumeAudioStreamDevice(audio)) { std::printf("stream: %s\n", SDL_GetError()); return 2; }
    }
    std::unique_ptr<Sa2Pace> pace;
    if (std::strcmp(mode, "no-pace") != 0) pace = std::make_unique<Sa2Pace>(1.0, 0.060);
    const Sa2Pace* made = pace.get();
    sa2tw::slow_mode::start(pace, audio, 1.0, 0.060);
    std::fflush(stderr);
    std::printf("same %d\n", pace.get() == made);
    std::printf("ratio %g\n", audio ? double(SDL_GetAudioStreamFrequencyRatio(audio)) : -1.0);
    if (pace) {
        const Sa2Pace::Slot a = pace->schedule(5000000, 100.0);
        const Sa2Pace::Slot b = pace->schedule(6000000, 100.0);
        std::printf("due %.6f %.6f\n", a.due - 100.0, b.due - 100.0);
        std::printf("pace %s\n", pace->json().c_str());
    }
    if (*drain_how && audio) {
        int left = 0;
        const bool finish = std::strcmp(drain_how, "drain") == 0;
        const double took = drain(audio, 0.6, finish, finish ? 5.0 : 1.5, &left);
        std::printf("drain %.4f\nleft %d\n", took, left);
    }
    SDL_Quit();
    return 0;
}
"""


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    gxx = shutil.which(os.environ.get("CXX", "g++"))
    if not gxx:
        pytest.fail("g++ is not installed, and the player's tweaks are built with it (host/player/build.sh)")
    install = Path(os.environ.get("SA2_S5_INSTALL") or ROOT / "private/s5-tools/install").resolve()
    if not (install / "lib/libSDL3.so.0").is_file():
        pytest.fail(f"no SDL3 under {install} (private/s5-tools/build.sh installs it; SA2_S5_INSTALL names another)")
    d = tmp_path_factory.mktemp("slow_mode")
    (d / "driver.cpp").write_text(DRIVER)
    exe = d / "driver"
    done = subprocess.run([gxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Wno-unused-parameter", "-Werror",
                           f"-I{PLAYER}", f"-I{install / 'include'}", str(d / "driver.cpp"),
                           str(PLAYER / "tweaks.cpp"), str(PLAYER / "tweaks/slow-mode.cpp"), "-o", str(exe),
                           f"-L{install / 'lib'}", "-Wl,--disable-new-dtags", f"-Wl,-rpath,{install / 'lib'}",
                           "-lSDL3"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return exe


def run(exe: Path, *args: str, **env: str) -> tuple[dict, str]:
    e = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    e.update(SDL_AUDIO_DRIVER="dummy", **env)
    r = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=60, env=e)
    assert r.returncode == 0, r.stdout + r.stderr
    out = {}
    for line in r.stdout.splitlines():
        key, _, value = line.partition(" ")
        out[key] = value
    return out, r.stderr


def pace(out: dict) -> dict:
    return json.loads(out["pace"])


OFF_ENVS = [{}, {"SA2_TWEAKS": "no-event-bars"}, {"SA2_TWEAKS": "Slow-Mode"},
            {"SA2_TWEAKS": "slow-mode", "SA2_REPLAY_WORDS": "/dev/null"}]


@pytest.mark.parametrize("env", OFF_ENVS, ids=["unset", "another", "misspelt", "replay"])
def test_off_is_the_player_as_it_was(driver, env):
    out, err = run(driver, "both", **env)
    assert err == ""
    assert out["same"] == "1" and out["ratio"] == "1"
    assert out["due"] == "0.000000 1.000000"
    p = pace(out)
    assert (p["live"], p["speed"], p["slack_ms"]) == (True, 1, 60.0)


def test_on_slows_the_schedule_and_the_sound(driver):
    out, err = run(driver, "both", SA2_TWEAKS="slow-mode")
    assert err == ("sa2 host tweaks: game at 0.75 speed: frames paced at speed 0.75 (0.75 of the player's 1), "
                   "the live schedule's slack 80 ms; the sound played at 0.75 of its rate (SDL frequency ratio "
                   "0.75; pitch lower), its queue's lead 60 ms of the game's sound\n")
    assert out["same"] == "0" and out["ratio"] == "0.75"
    assert out["due"] == "0.000000 1.333333"
    p = pace(out)
    assert (p["live"], p["speed"], p["slack_ms"]) == (True, 0.75, 80.0)


def test_on_the_device_plays_the_sound_at_three_quarters(driver):
    off, _ = run(driver, "both", "drain")
    on, _ = run(driver, "both", "drain", SA2_TWEAKS="slow-mode")
    t_off, t_on = float(off["drain"]), float(on["drain"])
    assert 0.55 < t_off < 0.75 and 0.75 < t_on < 0.95, (t_off, t_on)
    assert 1.2 < t_on / t_off < 1.5, (t_off, t_on)
    assert off["left"] == on["left"] == "0"


def test_the_end_flushes_what_the_resampler_holds(driver):
    """Without finish() the slowed stream never empties: SDL's resampler holds its last frames for
    samples that never come, and player.cpp's end would wait its whole limit for them."""
    held, _ = run(driver, "both", "hold", SA2_TWEAKS="slow-mode")
    assert float(held["drain"]) >= 1.5 and 0 < int(held["left"]) <= 64 * 8, held


def test_without_a_schedule_nothing_is_slowed_and_it_says_so(driver):
    out, err = run(driver, "no-pace", SA2_TWEAKS="slow-mode")
    assert err == ("sa2 host tweaks: game at 0.75 speed: left alone: play without --pace, which nothing holds to a "
                   "rate; the frames and the sound are not slowed\n")
    assert out["ratio"] == "1" and "pace" not in out


def test_without_sound_the_frames_alone_are_slowed_and_it_says_so(driver):
    out, err = run(driver, "no-audio", SA2_TWEAKS="slow-mode")
    assert err == ("sa2 host tweaks: game at 0.75 speed: frames paced at speed 0.75 (0.75 of the player's 1), "
                   "the live schedule's slack 80 ms; left alone: the sound, since there is no device stream "
                   "(--no-sound, or the device failed: the report's audio)\n")
    assert out["due"] == "0.000000 1.333333" and pace(out)["speed"] == 0.75


def test_the_player_makes_its_two_calls_where_the_schedule_and_the_sound_are_made_and_end():
    player = (PLAYER / "player.cpp").read_text()
    call = "    sa2tw::slow_mode::start(pace_, audio_stream_, o_.speed, rd_.live() ? kAudioLeadMs / 1000.0 : -1.0);\n"
    assert player.count("slow_mode::") == 2
    assert "    if (o_.audio && sdl_up_) setup_audio();\n" + call in player
    assert ("    if (audio_stream_) {\n        sa2tw::slow_mode::finish(audio_stream_);\n"
            "        audio_queued_at_end_ = SDL_GetAudioStreamQueued(audio_stream_);\n") in player
    assert player.index("pace_ = std::make_unique<Sa2Pace>") < player.index(call)
