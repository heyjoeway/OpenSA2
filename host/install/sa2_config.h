// sa2_config.h: the small configuration the installer writes and the launch path reads,
// $XDG_CONFIG_HOME/sa2recomp/sa2recomp.cfg (~/.config/... when unset; %LOCALAPPDATA%\sa2recomp
// on Windows).
//
// One "key=value" per line; '#' starts a comment line; the value is everything after the first
// '=' up to the end of the line, so a path may hold '=' or spaces but never a line break (such a
// path is refused when writing). Keys:
//
//   format=1
//   table.id, table.receipt_sha256      the table the source was verified under
//   source.kind=steam|folder|zip, source.path
//   read_root                           SA2_INSTALL: the folder the runtime reads (never written)
//   overlay                             SA2_VFS_WRITE_ROOT: where the game's writes land
//   exe                                 the shipped executable the runtime loads as-is
//   verified.at, verified.mode=full     when, and how, the source was last verified
//   manifest                            the quick size-and-mtime manifest of that verification
//   policy                              the user's policy, as applied
//   mod_loader=0|1, extras=N            what the scan found and skipped
//   substitution=<canonical> <- <backing>   one line per *_orig original used
#pragma once

#include <string>
#include <utility>
#include <vector>

struct Sa2Config {
    std::string table_id, receipt_sha256;
    std::string source_kind, source_path;
    std::string read_root, overlay, exe;
    std::string verified_at, verified_mode, manifest, policy;
    bool mod_loader = false;
    size_t extras = 0;
    std::vector<std::pair<std::string, std::string>> substitutions;  // canonical, backing
};

constexpr const char* SA2_POLICY =
    "refuse changed or missing files the game reads, and extras under resource/ but saves, user "
    "config and unused originals; skip other extras; use a matching *_orig original the runtime "
    "maps in the named file's place";

// ---- the game's own Config/ files: the defaults SA2's Launcher.exe would write ----
//
// SA2's Launcher.exe writes Config/UserConfig.cfg and Config/Keyboard.cfg on its first run. A
// Steam install that was never launched has an empty Config/ folder, and the table does not list
// either file (install-hashes-v1 leaves user config out), so such an install verifies and then
// cannot be played:
//   - without UserConfig.cfg the game stops ("Failure to load the user config file!", then a
//     read of guest 0x00000004): the user's native Windows run of 2026-10-11, all three routes;
//   - without Keyboard.cfg it reaches the title and takes no key: the Enter the launcher's
//     selftest sends is read by the game and moves nothing (measured 2026-10-11 under the Linux
//     fresh-host runner with the Steam game's Config/ emptied, CurrentMenu 0 after 4 Enters).
//
// So for each of SA2_USER_DEFAULTS that neither the game folder nor the overlay holds
// (case-blind, as the runtime resolves names), the installer writes the default into the overlay,
// where the runtime reads it first; never into the game folder. The templates are the files every
// registered replay ran with (private/install/Config/, byte for byte: 1280x720, windowed, 60 Hz,
// language 0, analytics off; player 1 on W, S, A, D, I, J, U, K, Q, E and Enter, the arrows
// player 2's); test_installer_core holds each equal to its file.
struct Sa2UserDefaultFile {
    const char* path;              // "Config/UserConfig.cfg", as the game names it under its folder
    const char* text;
};
extern const Sa2UserDefaultFile SA2_USER_DEFAULTS[];
extern const size_t SA2_USER_DEFAULT_COUNT;

struct Sa2UserDefault {
    std::string path;              // the default's path, SA2_USER_DEFAULTS' spelling
    bool in_read_root = false;     // the game folder (or the ZIP route's store) holds one
    bool in_overlay = false;       // the overlay already held one
    bool written = false;          // the default was written into the overlay
    std::string written_at;        // the overlay file written, when written
};

// Writes each default that neither `read_root` nor `overlay` holds into `overlay`, keeping the
// overlay's own spelling of Config/, so no second folder that differs only in case is made.
// `out` gets one entry per default. False (with `error`) when a write fails.
bool sa2_user_defaults_ensure(const std::string& read_root, const std::string& overlay,
                              std::vector<Sa2UserDefault>& out, std::string* error);

// <config home>/sa2recomp/sa2recomp.cfg, or "" when the host names no config home.
std::string sa2_config_path();
std::string sa2_config_text(const Sa2Config& c);
bool sa2_config_parse(const std::string& text, Sa2Config& c, std::string* error);
// Creates the directory and writes atomically. False (with `error`) on a value with a line
// break or an I/O failure.
bool sa2_config_write(const std::string& path, const Sa2Config& c, std::string* error);
