"""The player's host tweak pause-unfocused (host/player/tweaks/pause-unfocused.cpp): while the window is
away (out of focus, minimized, hidden or occluded) the player holds in its event pump, its sound's
device paused, until the window is back or the session ends.

The tweak's file and the host's reader (host/player/tweaks.cpp) are built with the system g++, as
host/player/build.sh builds them, against SDL3's own headers (private/s5-tools/install, the players'
SDL), and linked with a driver that defines the SDL functions the file calls: the window's flags
come from a script, each wait for an event moves a clock by its timeout, and every call is printed
in order. The pump the player hands the tweak is the driver's too, and calls the tweak again, as
Player::pump_events does from inside the hold. Each case is a process of its own, since the tweak
keeps its state (the window has had focus, what it has said) for the process's life.

The caller's cancellation is the player's own look at its cancellation pipe (sa2_cancel_waiting,
host/player/stream.cpp, linked in) on a real pipe the driver writes the cancel byte to while held;
host/player/test_stream.cpp checks that the live reader still takes that byte and ends Cancelled.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PLAYER = ROOT / "host" / "player"
SDL_INCLUDE = ROOT / "private" / "s5-tools" / "install" / "include"
CXX = ["g++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter"]
SAID = "sa2 host tweaks: held while unfocused: "

# argv[1]: the case. Flags: F focused, U out of focus, M focused but minimized. The window's
# flags change at the driver's waits (`at`: the flags from that wait on); each wait is 100 ms.
DRIVER = r"""
#include <SDL3/SDL.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <unistd.h>
#include "stream.h"
#include "tweaks.h"
#include "tweaks/pause-unfocused.h"

namespace {
SDL_WindowFlags flags = 0;
std::map<int, SDL_WindowFlags> at;   // wait number -> the window's flags from then on
int waits = 0, pumps = 0;
int end_after_pumps = -1;            // ending() says "the session is ending" once this many pumps have run
int cancel_at = -1;                  // the wait at which the caller writes its cancel byte
int cancel[2] = {-1, -1};            // the caller's cancellation pipe
bool paced = true;                   // the player's --pace
bool paused = false;                 // the audio device's state
Uint64 ns = 1000000000ull;
char window_storage, stream_storage;
SDL_Window* const win = reinterpret_cast<SDL_Window*>(&window_storage);
SDL_AudioStream* audio = reinterpret_cast<SDL_AudioStream*>(&stream_storage);
constexpr SDL_WindowFlags F = SDL_WINDOW_INPUT_FOCUS | SDL_WINDOW_MOUSE_FOCUS;
constexpr SDL_WindowFlags U = 0;
constexpr SDL_WindowFlags M = SDL_WINDOW_INPUT_FOCUS | SDL_WINDOW_MINIMIZED;
}

extern "C" {
SDL_WindowFlags SDLCALL SDL_GetWindowFlags(SDL_Window* w) { std::printf("flags\n"); return w == win ? flags : 0; }
bool SDLCALL SDL_AudioStreamDevicePaused(SDL_AudioStream*) { return paused; }
bool SDLCALL SDL_PauseAudioStreamDevice(SDL_AudioStream*) { std::printf("pause audio\n"); paused = true; return true; }
bool SDLCALL SDL_ResumeAudioStreamDevice(SDL_AudioStream*) { std::printf("resume audio\n"); paused = false; return true; }
const char* SDLCALL SDL_GetError(void) { return "no error"; }
Uint64 SDLCALL SDL_GetTicksNS(void) { return ns; }
bool SDLCALL SDL_WaitEventTimeout(SDL_Event* e, Sint32 ms) {
    ++waits;
    ns += Uint64(ms) * 1000000ull;
    if (at.count(waits)) flags = at[waits];
    if (waits == cancel_at) { const unsigned char one = 1; if (write(cancel[1], &one, 1) != 1) std::abort(); }
    return e == nullptr ? false : false;
}
}

// As Player::hold_ending: the run's own end first, then the caller's cancellation, looked at.
static const char* ending() {
    if (end_after_pumps >= 0 && pumps >= end_after_pumps) return "the session is ending";
    return sa2_cancel_waiting(cancel[0]);
}
static void pump();
static void hold() { sa2tw::hold_while_unfocused(win, audio, paced, ending, pump); }
// The player's pump calls the tweak again at its end; inside the hold that call returns at once.
static void pump() { ++pumps; hold(); }

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    const char* c = argv[1];
    sa2tw::g_on = std::strcmp(c, "off") ? sa2tw::SA2_TWEAK_PAUSE_UNFOCUSED.mask : 0;
    if (pipe(cancel) != 0) return 3;
    if (!std::strcmp(c, "off")) {
        flags = F; hold(); flags = U; hold();
    } else if (!std::strcmp(c, "never-focused")) {
        flags = U; hold(); hold(); flags = F; hold(); flags = U; at[3] = F; hold();
    } else if (!std::strcmp(c, "away-and-back")) {
        flags = F; hold(); flags = U; at[300] = F; hold(); hold();
    } else if (!std::strcmp(c, "minimized")) {
        flags = F; hold(); flags = M; at[2] = F; hold();
    } else if (!std::strcmp(c, "audio-not-started")) {
        paused = true; flags = F; hold(); flags = U; at[1] = F; hold();
    } else if (!std::strcmp(c, "no-audio")) {
        audio = nullptr; flags = F; hold(); flags = U; at[1] = F; hold();
    } else if (!std::strcmp(c, "ending-before")) {
        end_after_pumps = 0; flags = F; hold(); flags = U; hold(); hold();
    } else if (!std::strcmp(c, "ending-during")) {
        end_after_pumps = 2; flags = F; hold(); flags = U; hold(); hold();
    } else if (!std::strcmp(c, "unpaced")) {
        paced = false; flags = F; hold(); flags = U; hold(); hold(); flags = F; hold(); flags = U; hold();
    } else if (!std::strcmp(c, "cancel-during")) {
        cancel_at = 2; flags = F; hold(); flags = U; hold(); hold();
    } else if (!std::strcmp(c, "twice")) {
        flags = F; hold(); flags = U; at[1] = F; hold(); flags = U; at[2] = F; hold(); hold();
    } else {
        return 2;
    }
    // The cancel byte is still in the pipe for the stream reader to take.
    unsigned char left = 0;
    close(cancel[1]);
    const ssize_t n = read(cancel[0], &left, 1);
    if (n == 1) std::printf("cancel byte left %d\n", int(left));
    std::printf("waits %d pumps %d paused %d\n", waits, pumps, int(paused));
    return 0;
}
"""


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if shutil.which("g++") is None:
        pytest.fail("g++ is not installed, and the player's tweaks are built with it (host/player/build.sh)")
    if not (SDL_INCLUDE / "SDL3" / "SDL.h").is_file():
        pytest.fail(f"no SDL3 headers under {SDL_INCLUDE.relative_to(ROOT)} (run private/s5-tools/build.sh)")
    d = tmp_path_factory.mktemp("pause-unfocused")
    (d / "driver.cpp").write_text(DRIVER)
    out = d / "driver"
    done = subprocess.run([*CXX, "-I", str(SDL_INCLUDE), "-I", str(PLAYER), "-I", str(ROOT / "host"),
                           str(d / "driver.cpp"), str(PLAYER / "tweaks" / "pause-unfocused.cpp"),
                           str(PLAYER / "tweaks.cpp"), str(PLAYER / "stream.cpp"), str(PLAYER / "sha256.cpp"),
                           "-o", str(out), "-lzstd"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return out


def run(exe: Path, case: str) -> tuple[list[str], list[str]]:
    env = {k: v for k, v in os.environ.items() if k not in ("SA2_TWEAKS", "SA2_REPLAY_TICKS", "SA2_REPLAY_WORDS")}
    done = subprocess.run([str(exe), case], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines(), done.stderr.splitlines()


def test_off_calls_nothing_and_says_nothing(exe):
    """Off is the player as it was: not one SDL call, not one line."""
    assert run(exe, "off") == (["waits 0 pumps 0 paused 0"], [])


def test_a_window_that_has_not_had_focus_is_not_held_and_that_is_said_once(exe):
    out, err = run(exe, "never-focused")
    # Two pumps unfocused (not held), one focused, then away: held three waits, back.
    assert out == ["flags", "flags", "flags", "flags", "pause audio",
                   "flags", "flags", "flags", "flags", "resume audio", "waits 3 pumps 3 paused 0"]
    assert err == [SAID + "not holding a window that has not had focus yet (it is out of focus); held once it has",
                   SAID + "hold 1: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 1: goes on after 0.300 s (the window is back)"]


def test_away_for_30_s_holds_for_30_s_with_the_sound_paused_and_goes_on(exe):
    out, err = run(exe, "away-and-back")
    assert out[:3] == ["flags", "flags", "pause audio"]
    assert out[3:-3] == ["flags"] * 301                      # before each wait, and once back
    assert out[-3:] == ["resume audio", "flags", "waits 300 pumps 300 paused 0"]
    assert err == [SAID + "hold 1: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 1: goes on after 30.000 s (the window is back)"]


def test_a_minimized_window_is_held_though_its_focus_flag_stays(exe):
    out, err = run(exe, "minimized")
    assert out[-1] == "waits 2 pumps 2 paused 0"
    assert err == [SAID + "hold 1: the game and its sound are held (the window is minimized)",
                   SAID + "hold 1: goes on after 0.200 s (the window is back)"]


def test_a_device_the_player_has_not_started_is_left_paused(exe):
    out, err = run(exe, "audio-not-started")
    assert "pause audio" not in out and "resume audio" not in out
    assert out[-1] == "waits 1 pumps 1 paused 1"
    assert err[0] == SAID + "hold 1: the game and its sound are held (the window is out of focus)"


def test_with_no_sound_the_game_is_held_all_the_same(exe):
    out, err = run(exe, "no-audio")
    assert out == ["flags", "flags", "flags", "flags", "waits 1 pumps 1 paused 0"]
    assert err == [SAID + "hold 1: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 1: goes on after 0.100 s (the window is back)"]


def test_a_session_that_is_ending_is_not_held_and_that_is_said_once(exe):
    out, err = run(exe, "ending-before")
    assert out == ["flags", "flags", "flags", "waits 0 pumps 0 paused 0"]
    assert err == [SAID + "not holding a session that is ending (the session is ending; the window is out of focus)"]


def test_an_end_while_held_ends_the_hold(exe):
    """A close or end request reaches the player's pump inside the hold, and the hold goes."""
    out, err = run(exe, "ending-during")
    assert out[-1] == "waits 2 pumps 2 paused 0"
    assert err == [SAID + "hold 1: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 1: goes on after 0.200 s (the session is ending)",
                   SAID + "not holding a session that is ending (the session is ending; the window is out of focus)"]


def test_an_unpaced_session_is_never_held_and_that_is_said_once_by_name(exe):
    """--no-pace: the relay does not stop the game (lead 0), so a held player would leave the game
    running unseen with every key up; it is left alone, its sound too, and said once."""
    out, err = run(exe, "unpaced")
    assert out == ["flags"] * 5 + ["waits 0 pumps 0 paused 0"]
    assert err == [SAID + "not holding an unpaced session (--no-pace): its relay does not stop the game, so the "
                   "game's time and stage timer would run on while held (the window is out of focus)"]


def test_the_callers_cancellation_while_held_ends_the_hold_and_is_left_for_the_reader(exe):
    """The Android session cancels a player that has not read its END kFeedGrace after the game
    ended, and sends no SIGUSR1: the cancel byte, written while held, ends the hold by name, and is
    still in the pipe for the stream reader, so the run ends Cancelled."""
    out, err = run(exe, "cancel-during")
    assert out[-2:] == ["cancel byte left 1", "waits 2 pumps 2 paused 0"]
    assert out.count("pause audio") == 1 and out.count("resume audio") == 1
    assert err == [SAID + "hold 1: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 1: goes on after 0.200 s (the caller cancelled)",
                   SAID + "not holding a session that is ending (the caller cancelled; the window is out of focus)"]


def test_each_time_the_window_goes_is_a_hold_of_its_own(exe):
    out, err = run(exe, "twice")
    assert out.count("pause audio") == 2 and out.count("resume audio") == 2
    assert err == [SAID + "hold 1: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 1: goes on after 0.100 s (the window is back)",
                   SAID + "hold 2: the game and its sound are held (the window is out of focus)",
                   SAID + "hold 2: goes on after 0.100 s (the window is back)"]


def test_the_player_calls_it_at_the_end_of_its_pump_and_ends_it_with_the_session():
    """The one call in player.cpp: at the end of Player::pump_events, with the sound's stream and the
    player's --pace, held no longer once the run stops, the end is asked for, the stream's END is
    read or the caller's cancel byte waits (before the stream is opened in the player's descriptor,
    after it in the reader's); its pump is the player's own (the window's events and the pads)."""
    text = (PLAYER / "player.cpp").read_text()
    start = text.index("void Player::pump_events() {")
    body = text[start:text.index("\n}\n", start) + 3]
    call = ("    if (input_) input_->pump();\n"
            "    sa2tw::hold_while_unfocused(win_, audio_stream_, o_.pace, [this] { return hold_ending(); },\n"
            "                                [this] { pump_events(); service_pads(); });\n}\n")
    assert body.rstrip().endswith(call.rstrip())
    ending = ("    const char* hold_ending() const {\n"
              "        if (stop_ || end_requested_ || end_records_) return \"the session is ending\";\n"
              "        return sa2_cancel_waiting(cancel_fd_ >= 0 ? cancel_fd_ : rd_.live_cancel_fd());\n"
              "    }\n")
    assert text.count(ending) == 1
    assert text.count("sa2tw::hold_while_unfocused(") == 1
    assert '#include "tweaks/pause-unfocused.h"' in text
