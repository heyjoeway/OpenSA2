// sa2_platform.h: every operating-system call the installer core makes, so the rest of
// host/install is portable C++17. sa2_platform_posix.cpp implements it for Linux (and any POSIX
// host); sa2_platform_win32.cpp for Windows (zig c++ -target x86_64-windows-gnu), where paths
// are UTF-8 at this interface and wide underneath.
//
// Paths use '/' between components at this interface on every host; Win32 accepts it.
#pragma once

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace sa2p {

struct Stat {
    bool exists = false;
    bool is_dir = false;
    bool is_file = false;
    bool is_link = false;      // the name itself is a symbolic link (lstat)
    uint64_t size = 0;
    int64_t mtime_ns = 0;
};

// Follows symbolic links (on Windows, reparse points); `is_link` reports whether the name
// itself is one.
bool stat_path(const std::string& path, Stat& out);
// The host's reason the last call above failed, as text ("Permission denied"); `absent` (when
// given) is set when the reason is that the file or a folder on its path does not exist.
std::string last_error(bool* absent = nullptr);
// The names in a directory, without "." and "..". False when it cannot be read.
bool list_dir(const std::string& dir, std::vector<std::string>& names);
// The canonical absolute path of an existing file or directory.
bool real_path(const std::string& path, std::string& out);
bool make_dirs(const std::string& path);
FILE* open_read(const std::string& path);
// Write `data` to `path` through a temporary sibling, flushed and renamed over it.
bool write_file_atomic(const std::string& path, const std::string& data);
bool read_file(const std::string& path, std::string& out, size_t limit = 64u << 20);

// The command line as UTF-8: argv on POSIX; on Windows the wide command line, since argv is
// in the ANSI code page there and cannot carry every path.
std::vector<std::string> utf8_args(int argc, char** argv);

std::string env(const char* name);     // "" when unset
std::string home_dir();
// The login name ($USER, else $LOGNAME; %USERNAME% on Windows; "" when unset), so a record can
// leave it out of paths outside the home directory.
std::string user_name();
// Whether this host's file names are case-blind (Windows): paths are then compared folded.
bool case_blind_paths();
// $XDG_CONFIG_HOME or ~/.config on Linux; %LOCALAPPDATA% on Windows.
std::string config_home();
// $XDG_DATA_HOME or ~/.local/share on Linux; %LOCALAPPDATA% on Windows.
std::string data_home();
// The Steam roots this host's conventions name, before deduplication: on Linux the ones of
// the installer survey (~/.steam/steam, ~/.steam/root, $XDG_DATA_HOME/Steam or
// ~/.local/share/Steam, ~/.steam/debian-installation, Flatpak, Snap); on Windows the registry's
// HKCU\Software\Valve\Steam SteamPath and HKLM\SOFTWARE\Valve\Steam InstallPath (each in
// both registry views, so WOW6432Node too), then %ProgramFiles(x86)%\Steam. `notes` says which
// registry value named which root, and which was not set.
std::vector<std::string> steam_roots(std::vector<std::string>* notes);
// HKLM\Software\Sega\SonicAd2's install_path, the 32-bit view first, then the 64-bit one.
// False on a host without that registry (POSIX) or when neither view holds it.
bool sega_registry_install_path(std::string& out);

std::string now_utc_iso8601();         // "2026-10-11T14:03:07+00:00"
double monotonic_seconds();
std::string os_name();                 // "linux", "windows", ...
std::string arch_name();               // "x86_64", "aarch64", ...
std::string self_exe_path();           // "" when the host cannot say
unsigned cpu_count();

}  // namespace sa2p
