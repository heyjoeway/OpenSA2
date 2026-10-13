// sa2_steam.h: finding the user's Steam installation of SA2 (app 213610).
//
// From each Steam root (sa2p::steam_roots, deduplicated by realpath), the libraries are the
// root itself and every path steamapps/libraryfolders.vdf lists (config/libraryfolders.vdf when
// that is absent), in either of the file's two formats:
//
//   "libraryfolders" { "0" { "path" "/home/u/.local/share/Steam" "apps" { "213610" "..." } } }
//   "LibraryFolders" { "TimeNextStatsReport" "..." "1" "D:\\SteamLibrary" }        (before 2021)
//
// Libraries are deduplicated by realpath too. In each, steamapps/appmanifest_213610.acf is read
// and matched by its "appid", never by name (the store name carries a trademark sign and a
// trailing space); the candidate is steamapps/common/<installdir>. The "apps" list is a hint
// only and is not read. Nothing is copied out of the manifests but the fields below: never
// LastOwner (a SteamID).
#pragma once

#include <string>
#include <vector>

#include "sa2_kv.h"

constexpr const char* SA2_STEAM_APPID = "213610";

struct Sa2SteamInstall {
    std::string game_path;            // realpath of <library>/steamapps/common/<installdir>
    std::string library;              // realpath of the library
    std::string manifest;             // the appmanifest file read
    std::string installdir, state_flags, buildid;
    bool fully_installed = false;     // StateFlags has bit 2 (4, "fully installed")
};

struct Sa2SteamScan {
    std::vector<std::string> roots_tried;   // lexical, in order
    std::vector<std::string> roots;         // realpath'd, deduplicated, existing
    std::vector<std::string> libraries;     // realpath'd, deduplicated, existing
    std::vector<Sa2SteamInstall> installs;  // fully installed, deduplicated by realpath
    std::vector<std::string> notes;         // what was skipped and why
};

// The library paths a parsed libraryfolders.vdf lists, in file order, in either format.
std::vector<std::string> sa2_steam_library_paths(const Sa2Kv& vdf);

// Scans the given roots (sa2p::steam_roots() on the host).
Sa2SteamScan sa2_steam_find(const std::vector<std::string>& roots);
