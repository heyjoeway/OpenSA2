// hold-toggle.cpp: the host tweak "hold-toggle" (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST, home
// "player"): B as a toggle, for a player who cannot hold a button down.
//
// Some moves ask for B held: a mech's lock-on (Tails, Eggman: hold B to sweep the cursor over
// targets, let go to fire), and the spin-dash charge. With the tweak on, a press of B that the
// player sends holds B for the game, and the next press lets it go; a release of B changes nothing.
// The game is sent ordinary held and released buttons, so the game is unchanged.
//
// It acts in Player::send_pads, on the four XInput slots as they go to the game, after slot 0 is
// shared with the touch controls: a pad's B (whatever button SA2_PAD_MAP makes send B) and the
// touch controls' B latch alike, one latch per slot. Every other output is sent as it is.
//
// A latch is let go, and said once by name each time:
//   - START pressed in any slot (the pause menu, and every other menu START opens), so no B stays
//     held through it;
//   - the window losing the input focus, as the key sender lets every key up (input.h). The pads
//     send no press while the window is not focused (SDL's default), so none latches then.
//     Player::service_pads asks owed() after each pump, so the release is sent even with no pad
//     event to carry it;
//   - the slot's pad removed (the slot empties).
// The case it leaves alone, said once at the start: the game's menus are not told apart from
// play (the player is not told the game's mode), so a B latched in a menu stays held until the
// next press of B or START.
//
// Off (sa2tw::on false: no SA2_TWEAKS naming it, out of play, every replay), apply and owed do
// nothing and the set is sent as the player made it.
#include "hold-toggle.h"

#include "../tweaks.h"

#include <cstdio>

namespace sa2tw::hold_toggle {

namespace {

constexpr uint16_t kB = 0x2000;       // XINPUT_GAMEPAD_B (input.h PadState)
constexpr uint16_t kStart = 0x0010;   // XINPUT_GAMEPAD_START
constexpr unsigned kSaid = 256;       // latch changes said in the log, then one line that no more are

Latches g_latches;
unsigned g_said = 0;

void say_stderr(const char* line) {
    if (g_said < kSaid) std::fprintf(stderr, "sa2 host tweaks: B toggles: %s\n", line);
    else if (g_said == kSaid) std::fprintf(stderr, "sa2 host tweaks: B toggles: %u changes said; the rest are not\n", kSaid);
    ++g_said;
}

void let_go(Latches* l, unsigned k, const char* why, void (*say)(const char*)) {
    if (!l->latched[k]) return;
    l->latched[k] = 0;
    if (say) {
        char line[96];
        std::snprintf(line, sizeof line, "slot %u B let go (%s)", k, why);
        say(line);
    }
}

}  // namespace

void step(Latches* l, sa2_input::PadSet* set, bool focused, void (*say)(const char* line)) {
    uint16_t pressed[sa2_input::kPadSlots] = {};
    bool start = false;
    for (unsigned k = 0; k < sa2_input::kPadSlots; ++k) {
        const sa2_input::PadState& s = set->slot[k];
        if (!s.connected) {
            let_go(l, k, "the slot emptied", say);
            l->raw[k] = 0;
            continue;
        }
        pressed[k] = uint16_t(s.buttons & ~l->raw[k]);
        l->raw[k] = s.buttons;
        start = start || (pressed[k] & kStart);
    }
    for (unsigned k = 0; k < sa2_input::kPadSlots; ++k) {
        if (!focused) {
            let_go(l, k, "the window lost the input focus", say);
            continue;
        }
        if (!(pressed[k] & kB)) continue;
        if (l->latched[k]) {
            let_go(l, k, "pressed again", say);
        } else {
            l->latched[k] = kB;
            if (say) {
                char line[32];
                std::snprintf(line, sizeof line, "slot %u B held", k);
                say(line);
            }
        }
    }
    if (start)
        for (unsigned k = 0; k < sa2_input::kPadSlots; ++k) let_go(l, k, "START", say);
    for (unsigned k = 0; k < sa2_input::kPadSlots; ++k)
        if (set->slot[k].connected) set->slot[k].buttons = uint16_t((set->slot[k].buttons & ~kB) | l->latched[k]);
}

void apply(sa2_input::PadSet* set, bool focused) {
    if (!on(SA2_TWEAK_HOLD_TOGGLE)) return;
    static bool told = false;
    if (!told) {
        told = true;
        std::fprintf(stderr, "sa2 host tweaks: B toggles: a press of B holds it and the next lets it go, on every pad "
                             "and the touch controls; START, losing the input focus and a pad's removal let it go; "
                             "left alone: the game's menus, which the player cannot tell from play (a B latched in "
                             "one stays held until the next press of B or START)\n");
    }
    step(&g_latches, set, focused, say_stderr);
}

bool owed(bool focused) {
    if (!on(SA2_TWEAK_HOLD_TOGGLE) || focused) return false;
    for (uint16_t l : g_latches.latched)
        if (l) return true;
    return false;
}

}  // namespace sa2tw::hold_toggle
