// sa2_steam.cpp: see sa2_steam.h.
#include "sa2_steam.h"

#include <set>

#include "sa2_install.h"
#include "sa2_platform.h"

namespace {

bool all_digits(const std::string& s) {
    if (s.empty()) return false;
    for (char c : s)
        if (c < '0' || c > '9') return false;
    return true;
}

// `name` inside `dir`: the exact spelling, else the one ASCII-case-folded match.
bool child_ci(const std::string& dir, const std::string& name, std::string& out) {
    sa2p::Stat st;
    if (sa2p::stat_path(dir + "/" + name, st)) {
        out = dir + "/" + name;
        return true;
    }
    std::vector<std::string> names;
    if (!sa2p::list_dir(dir, names)) return false;
    std::string want = sa2_install_fold(name), hit;
    int n = 0;
    for (const std::string& c : names)
        if (sa2_install_fold(c) == want) {
            hit = c;
            n++;
        }
    if (n != 1) return false;
    out = dir + "/" + hit;
    return true;
}

std::string slashes(std::string s) {
    for (char& c : s)
        if (c == '\\') c = '/';
    return s;
}

// The key one place is deduplicated by: its real path, folded on a host whose names are
// case-blind (Windows), where "s:/x" and "S:/x" are one folder.
std::string place_key(const std::string& real) {
    return sa2p::case_blind_paths() ? sa2_install_fold(real) : real;
}

}  // namespace

std::vector<std::string> sa2_steam_library_paths(const Sa2Kv& vdf) {
    std::vector<std::string> out;
    const Sa2Kv* top = vdf.find("libraryfolders");
    if (!top || !top->block) return out;
    for (const Sa2Kv& c : top->children) {
        if (!all_digits(c.key)) continue;  // contentstatsid, TimeNextStatsReport, ...
        if (c.block) {
            std::string p = c.get("path");
            if (!p.empty()) out.push_back(p);
        } else if (!c.value.empty()) {
            out.push_back(c.value);
        }
    }
    return out;
}

Sa2SteamScan sa2_steam_find(const std::vector<std::string>& roots) {
    Sa2SteamScan scan;
    std::set<std::string> seen_roots, seen_libs, seen_games;
    std::vector<std::string> libs;
    for (const std::string& r : roots) {
        scan.roots_tried.push_back(r);
        std::string real;
        sa2p::Stat st;
        if (!sa2p::stat_path(r, st) || !st.is_dir || !sa2p::real_path(r, real)) continue;
        if (!seen_roots.insert(place_key(real)).second) continue;
        scan.roots.push_back(real);
        libs.push_back(real);  // the root is always a library
        std::string steamapps, vdf_path, text;
        bool have = false;
        if (child_ci(real, "steamapps", steamapps) && child_ci(steamapps, "libraryfolders.vdf", vdf_path))
            have = sa2p::read_file(vdf_path, text, 4u << 20);
        if (!have) {
            std::string cfg;
            if (child_ci(real, "config", cfg) && child_ci(cfg, "libraryfolders.vdf", vdf_path))
                have = sa2p::read_file(vdf_path, text, 4u << 20);
        }
        if (!have) {
            scan.notes.push_back(real + ": no libraryfolders.vdf; the root alone is searched");
            continue;
        }
        Sa2Kv kv;
        std::string err;
        if (!sa2_kv_parse(text, kv, &err)) {
            scan.notes.push_back(vdf_path + ": not KeyValues (" + err + "); the root alone is searched");
            continue;
        }
        for (const std::string& p : sa2_steam_library_paths(kv)) libs.push_back(slashes(p));
    }
    for (const std::string& lib : libs) {
        std::string real;
        sa2p::Stat st;
        if (!sa2p::stat_path(lib, st) || !st.is_dir || !sa2p::real_path(lib, real)) {
            scan.notes.push_back(lib + ": a listed library that is not here");
            continue;
        }
        if (!seen_libs.insert(place_key(real)).second) continue;
        scan.libraries.push_back(real);
        std::string steamapps, acf, text;
        if (!child_ci(real, "steamapps", steamapps)) continue;
        if (!child_ci(steamapps, std::string("appmanifest_") + SA2_STEAM_APPID + ".acf", acf)) continue;
        Sa2Kv kv;
        std::string err;
        if (!sa2p::read_file(acf, text, 4u << 20) || !sa2_kv_parse(text, kv, &err)) {
            scan.notes.push_back(acf + ": unreadable app manifest" + (err.empty() ? "" : " (" + err + ")"));
            continue;
        }
        const Sa2Kv* app = kv.find("AppState");
        if (!app || !app->block || app->get("appid") != SA2_STEAM_APPID) {
            scan.notes.push_back(acf + ": not app " + SA2_STEAM_APPID + "'s manifest");
            continue;
        }
        Sa2SteamInstall in;
        in.library = real;
        in.manifest = acf;
        in.installdir = app->get("installdir");
        in.state_flags = app->get("StateFlags");
        in.buildid = app->get("buildid");
        // StateFlags is a decimal bit set; bit 4 (fully installed) is all that is read. A value
        // too long for 64 bits is not a state Steam writes, and reads as not installed.
        uint64_t flags = 0;
        bool parsed = all_digits(in.state_flags) && in.state_flags.size() <= 19;
        for (size_t i = 0; parsed && i < in.state_flags.size(); i++) flags = flags * 10 + uint64_t(in.state_flags[i] - '0');
        in.fully_installed = parsed && (flags & 4u) != 0;
        if (in.installdir.empty() || in.installdir.find('/') != std::string::npos ||
            in.installdir.find('\\') != std::string::npos || in.installdir == "." || in.installdir == "..") {
            scan.notes.push_back(acf + ": installdir \"" + in.installdir + "\" is not a folder name");
            continue;
        }
        if (!in.fully_installed) {
            scan.notes.push_back(acf + ": StateFlags " + in.state_flags + " is not fully installed");
            continue;
        }
        std::string common, game;
        if (!child_ci(steamapps, "common", common) || !child_ci(common, in.installdir, game) ||
            !sa2p::real_path(game, in.game_path)) {
            scan.notes.push_back(acf + ": the manifest's folder common/" + in.installdir + " is not here");
            continue;
        }
        if (!seen_games.insert(place_key(in.game_path)).second) continue;
        scan.installs.push_back(in);
    }
    return scan;
}
