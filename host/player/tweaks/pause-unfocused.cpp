// pause-unfocused.cpp: the player's host tweak "pause-unfocused" (SA2_TWEAK_PAUSE_UNFOCUSED): the
// game and its sound stop while the player's window is out of focus, and go on when it is back.
//
// How: the player holds in its event pump and reads no more of the stream. A paced session's game
// is held to the player through the stream already (player.cpp, "Pace"): the relay
// (host/launch/sa2_relay.h, tools/s5/present_live.py's relay, the Android session's) takes no more
// from the game's pipe while it holds a whole frame the player has not taken, so the game waits in
// its next Present's write. The guest reads only the virtual clock, so its time, its stage timer
// and its sound stand still exactly, however long the window is away; the frames that follow carry
// on from the last virtual time. The sound already queued for the device stays queued: the device
// is paused (SDL_PauseAudioStreamDevice) and resumed with it, so it goes on in step with the frames.
// The live pace schedule absorbs the stall (pace.h): the first frame after it is late by more than
// the slack, which moves the schedule once.
//
// What it leaves to the rest of the player: the keys are let up when focus goes (input.h), and SDL
// sends no gamepad press to an unfocused window. The window's events are still pumped while held,
// so a close request or the caller's end request (SIGUSR1) ends the hold, and so does the caller's
// cancellation: the player's `ending()` looks at the cancellation pipe without taking its byte
// (sa2_cancel_waiting, stream.h), so the stream reader still takes it, and the run ends Cancelled,
// once the player reads the stream again. The Android session cancels a player that has not read
// the stream to its END kFeedGrace after the game has ended (it sends no SIGUSR1). The stream itself
// is not read while held, so its END is seen once the window is back or the session is ending.
//
// The cases it leaves alone, each said once, by name:
//   - a window that has never had focus (one that opens behind another): it is not held until it
//     has had focus once, so a session nobody has looked at yet still starts;
//   - an unpaced session (--no-pace: the launcher's, play_live.py's, the Android session's
//     sa2.no_pace): its relay does not hold the game (lead 0, only its 64 MB cap), so a held player
//     would leave the game running unseen, its time and stage timer going on with every key up,
//     until the relay and the pipes are full; it is not held;
//   - a session that is ending (a close or end request, a stopped run, the stream's END read, or
//     the caller's cancellation): the game must reach its END, so it is not held.
//
// Android: SDL already blocks the app's event pump while the activity is paused, and pauses its
// audio devices (its block-on-pause default); this holds the game and its sound as well while the
// app is visible but not focused (the notification shade, another app's window over it).
#include "pause-unfocused.h"

#include "../tweaks.h"

#include <cinttypes>
#include <cstdio>
#include <string>

namespace sa2tw {

namespace {

const char* const kSaid = "sa2 host tweaks: held while unfocused: ";
constexpr SDL_WindowFlags kAway = SDL_WINDOW_MINIMIZED | SDL_WINDOW_HIDDEN | SDL_WINDOW_OCCLUDED;

bool g_had_focus = false;      // the window has had input focus at least once
bool g_holding = false;        // in the hold: the pump it calls comes back here
bool g_said_no_focus = false;  // the case of a window that has not had focus, said
bool g_said_unpaced = false;   // the case of an unpaced session, said
bool g_said_ending = false;    // the case of a session that is ending, said
uint64_t g_holds = 0;

bool here(SDL_WindowFlags f) { return (f & SDL_WINDOW_INPUT_FOCUS) && !(f & kAway); }

// Why the window is away, from its flags: "out of focus, minimized" and so on.
std::string away(SDL_WindowFlags f) {
    std::string s;
    const auto add = [&](const char* w) { s += (s.empty() ? "" : ", ") + std::string(w); };
    if (!(f & SDL_WINDOW_INPUT_FOCUS)) add("out of focus");
    if (f & SDL_WINDOW_MINIMIZED) add("minimized");
    if (f & SDL_WINDOW_HIDDEN) add("hidden");
    if (f & SDL_WINDOW_OCCLUDED) add("occluded");
    return s;
}

}  // namespace

void hold_while_unfocused(SDL_Window* win, SDL_AudioStream* audio, bool paced,
                          const std::function<const char*()>& ending, const std::function<void()>& pump) {
    if (!on(SA2_TWEAK_PAUSE_UNFOCUSED) || !win || g_holding) return;
    const SDL_WindowFlags f = SDL_GetWindowFlags(win);
    if (here(f)) {
        g_had_focus = true;
        return;
    }
    if (!g_had_focus) {
        if (!g_said_no_focus) {
            g_said_no_focus = true;
            std::fprintf(stderr, "%snot holding a window that has not had focus yet (it is %s); held once it has\n",
                         kSaid, away(f).c_str());
        }
        return;
    }
    if (!paced) {
        if (!g_said_unpaced) {
            g_said_unpaced = true;
            std::fprintf(stderr,
                         "%snot holding an unpaced session (--no-pace): its relay does not stop the game, so the "
                         "game's time and stage timer would run on while held (the window is %s)\n",
                         kSaid, away(f).c_str());
        }
        return;
    }
    if (const char* why = ending()) {
        if (!g_said_ending) {
            g_said_ending = true;
            std::fprintf(stderr, "%snot holding a session that is ending (%s; the window is %s)\n", kSaid, why,
                         away(f).c_str());
        }
        return;
    }
    g_holding = true;
    const uint64_t n = ++g_holds;
    const uint64_t t0 = SDL_GetTicksNS();
    const bool pause_audio = audio && !SDL_AudioStreamDevicePaused(audio);
    if (pause_audio && !SDL_PauseAudioStreamDevice(audio))
        std::fprintf(stderr, "%shold %" PRIu64 ": SDL_PauseAudioStreamDevice failed: %s\n", kSaid, n, SDL_GetError());
    std::fprintf(stderr, "%shold %" PRIu64 ": the game and its sound are held (the window is %s)\n", kSaid, n,
                 away(f).c_str());
    const char* why = "the window is back";
    for (;;) {
        if (const char* e = ending()) {
            why = e;
            break;
        }
        if (here(SDL_GetWindowFlags(win))) break;
        SDL_WaitEventTimeout(nullptr, 100);
        pump();
    }
    if (pause_audio && !SDL_ResumeAudioStreamDevice(audio))
        std::fprintf(stderr, "%shold %" PRIu64 ": SDL_ResumeAudioStreamDevice failed: %s\n", kSaid, n, SDL_GetError());
    std::fprintf(stderr, "%shold %" PRIu64 ": goes on after %.3f s (%s)\n", kSaid, n,
                 double(SDL_GetTicksNS() - t0) / 1e9, why);
    g_holding = false;
}

}  // namespace sa2tw
