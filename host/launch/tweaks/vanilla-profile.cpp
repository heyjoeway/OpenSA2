// vanilla-profile.cpp: a vanilla run without removing the list. When share/game.env names
// vanilla-profile in SA2_TWEAKS, the launcher gives the game and the player SA2_TWEAKS=vanilla-profile
// and reads that value for itself too, so no other tweak is on, the launcher's own included: a
// speedrun or a comparison runs as the game does, and taking the one name out turns the rest back
// on. The game's line then names vanilla-profile as the launcher's and turns nothing on ("sa2
// tweaks: none on ..."), the player's line says none of its tweaks are on, and the launcher's names
// vanilla-profile alone.
//
// Its own line, one in each play session's launcher.log, before the launcher's reading:
//   sa2 host tweaks: vanilla: every other tweak off: the game, the player and the launcher get
//   SA2_TWEAKS=vanilla-profile; dropped no-event-bars, hide-hud
// or, when the value names no other tweak, that nothing was dropped. Every other name is dropped,
// a name no list holds too, and named in that line.
//
// The reader (host/player/tweaks.cpp) turns it on only in play: the selftest drops game.env's
// SA2_TWEAKS before the reader sees it, and a replay turns every host tweak off.
#include "vanilla-profile.h"

#include <cstring>
#include <vector>

#include "tweaks.h"

namespace sa2tw {

namespace {

// With vanilla-profile on (g_on), sets `value` to "vanilla-profile" alone and returns the line;
// off, returns "".
std::string vanilla_profile(std::string& value) {
    if (!on(SA2_TWEAK_VANILLA_PROFILE)) return "";
    // Split as the readers split: at commas, each name trimmed of spaces and tabs, a name given
    // twice is one, an empty one is skipped.
    std::vector<std::string> dropped;
    for (const char* p = value.c_str(); *p;) {
        const char* end = std::strchr(p, ',');
        if (!end) end = p + std::strlen(p);
        const char *a = p, *b = end;
        while (a < b && (*a == ' ' || *a == '\t')) a++;
        while (b > a && (b[-1] == ' ' || b[-1] == '\t')) b--;
        const std::string name(a, b);
        bool seen = name.empty() || name == "vanilla-profile";
        for (const auto& d : dropped) seen = seen || d == name;
        if (!seen) dropped.push_back(name);
        p = *end ? end + 1 : end;
    }
    const std::string head = "sa2 host tweaks: vanilla: every other tweak off: ";
    if (dropped.empty()) {
        const std::string line = head + "no other tweak is named, so none is dropped (SA2_TWEAKS=" + value + ")";
        value = "vanilla-profile";
        return line;
    }
    std::string list;
    for (const auto& d : dropped) list += (list.empty() ? "" : ", ") + d;
    value = "vanilla-profile";
    return head + "the game, the player and the launcher get SA2_TWEAKS=vanilla-profile; dropped " + list;
}

}  // namespace

Reading launcher_reading(std::string& value, std::string& said, const char* why_off) {
    Reading r = read(value.c_str(), why_off, "launcher");
    g_on = r.on;                       // vanilla_profile asks for its own bit
    said = vanilla_profile(value);
    if (said.empty()) return r;
    r = read(value.c_str(), why_off, "launcher");   // the launcher is vanilla too
    g_on = r.on;
    return r;
}

}  // namespace sa2tw
