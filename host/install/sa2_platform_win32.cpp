// sa2_platform_win32.cpp: sa2_platform.h for Windows. UTF-8 at the interface, wide (W) calls
// underneath. The registry is read for the Steam roots (Steam's SteamPath and InstallPath) and the
// game's own key (Sega's SonicAd2, which the game's Steam install script writes), each in both
// registry views: Steam's client and that script are 32-bit, so on 64-bit Windows their HKLM
// values are under WOW6432Node, the 32-bit view.
#ifdef _WIN32

#include "sa2_platform.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <shellapi.h>

#include <cstdlib>
#include <ctime>
#include <thread>

namespace sa2p {

namespace {

std::wstring wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), &w[0], n);
    return w;
}

std::string narrow(const std::wstring& w) {
    if (w.empty()) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), int(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(size_t(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), int(w.size()), &s[0], n, nullptr, nullptr);
    return s;
}

std::string slashes(std::string s) {
    for (char& c : s)
        if (c == '\\') c = '/';
    return s;
}

int64_t filetime_ns(const FILETIME& ft) {
    uint64_t t = (uint64_t(ft.dwHighDateTime) << 32) | ft.dwLowDateTime;  // 100 ns since 1601
    return int64_t(t - 116444736000000000ULL) * 100;
}

}  // namespace

bool stat_path(const std::string& path, Stat& out) {
    out = Stat{};
    const std::wstring w = wide(path);
    DWORD attrs = GetFileAttributesW(w.c_str());
    if (attrs == INVALID_FILE_ATTRIBUTES) return false;
    out.is_link = (attrs & FILE_ATTRIBUTE_REPARSE_POINT) != 0;
    // Follow a reparse point the way stat(2) follows a link: open the target (access 0 needs no
    // share mode) and ask the handle. A dangling link counts as absent.
    HANDLE h = CreateFileW(w.c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    BY_HANDLE_FILE_INFORMATION fi;
    BOOL ok = GetFileInformationByHandle(h, &fi);
    DWORD e = GetLastError();
    CloseHandle(h);
    if (!ok) {
        SetLastError(e);
        return false;
    }
    out.exists = true;
    out.is_dir = (fi.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    out.is_file = !out.is_dir;
    out.size = (uint64_t(fi.nFileSizeHigh) << 32) | fi.nFileSizeLow;
    out.mtime_ns = filetime_ns(fi.ftLastWriteTime);
    return true;
}

std::string last_error(bool* absent) {
    DWORD e = GetLastError();
    if (absent) *absent = e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND;
    wchar_t* buf = nullptr;
    DWORD n = FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                                 FORMAT_MESSAGE_IGNORE_INSERTS,
                             nullptr, e, 0, reinterpret_cast<LPWSTR>(&buf), 0, nullptr);
    std::string s = n ? narrow(std::wstring(buf, n)) : "error " + std::to_string(e);
    if (buf) LocalFree(buf);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ' || s.back() == '.')) s.pop_back();
    return s;
}

bool list_dir(const std::string& dir, std::vector<std::string>& names) {
    names.clear();
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(wide(dir + "/*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return false;
    do {
        std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        names.push_back(narrow(n));
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return true;
}

bool real_path(const std::string& path, std::string& out) {
    HANDLE h = CreateFileW(wide(path).c_str(), 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    wchar_t buf[32768];
    DWORD n = GetFinalPathNameByHandleW(h, buf, 32768, FILE_NAME_NORMALIZED);
    CloseHandle(h);
    if (n == 0 || n >= 32768) return false;
    std::wstring w(buf, n);
    if (w.rfind(L"\\\\?\\UNC\\", 0) == 0) w = L"\\\\" + w.substr(8);
    else if (w.rfind(L"\\\\?\\", 0) == 0) w = w.substr(4);
    // The drive letter upper-case, as Windows itself writes it: Wine gives it back as the caller
    // spelled it, so "s:/x" and "S:/x" would otherwise read as two folders (measured 2026-10-11).
    if (w.size() >= 2 && w[1] == L':' && w[0] >= L'a' && w[0] <= L'z') w[0] = wchar_t(w[0] - L'a' + L'A');
    out = slashes(narrow(w));
    return true;
}

bool make_dirs(const std::string& path) {
    std::string p = slashes(path);
    for (size_t i = 0; i <= p.size(); i++) {
        if (i == p.size() || p[i] == '/') {
            std::string cur = p.substr(0, i);
            if (cur.empty() || (cur.size() == 2 && cur[1] == ':')) continue;
            if (!CreateDirectoryW(wide(cur).c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS)
                return false;
        }
    }
    return true;
}

FILE* open_read(const std::string& path) { return _wfopen(wide(path).c_str(), L"rb"); }

bool write_file_atomic(const std::string& path, const std::string& data) {
    std::string tmp = path + ".part";
    HANDLE h = CreateFileW(wide(tmp).c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD w = 0;
    bool ok = WriteFile(h, data.data(), DWORD(data.size()), &w, nullptr) && w == data.size();
    ok = FlushFileBuffers(h) && ok;
    CloseHandle(h);
    if (ok) ok = MoveFileExW(wide(tmp).c_str(), wide(path).c_str(),
                             MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) != 0;
    if (!ok) DeleteFileW(wide(tmp).c_str());
    return ok;
}

bool read_file(const std::string& path, std::string& out, size_t limit) {
    out.clear();
    FILE* f = open_read(path);
    if (!f) return false;
    char buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) {
        out.append(buf, n);
        if (out.size() > limit) { std::fclose(f); return false; }
    }
    bool ok = !std::ferror(f);
    std::fclose(f);
    return ok;
}

std::string env(const char* name) {
    std::wstring n = wide(name);
    DWORD len = GetEnvironmentVariableW(n.c_str(), nullptr, 0);
    if (len == 0) return {};
    std::wstring v(len, L'\0');
    len = GetEnvironmentVariableW(n.c_str(), &v[0], len);
    v.resize(len);
    return narrow(v);
}

std::string home_dir() { return slashes(env("USERPROFILE")); }

std::vector<std::string> utf8_args(int argc, char** argv) {
    std::vector<std::string> out;
    int n = 0;
    LPWSTR* w = CommandLineToArgvW(GetCommandLineW(), &n);
    if (!w) {
        for (int i = 0; i < argc; i++) out.emplace_back(argv[i]);
        return out;
    }
    for (int i = 0; i < n; i++) out.push_back(narrow(w[i]));
    LocalFree(w);
    return out;
}

std::string user_name() { return env("USERNAME"); }

bool case_blind_paths() { return true; }
std::string config_home() { return slashes(env("LOCALAPPDATA")); }
std::string data_home() { return slashes(env("LOCALAPPDATA")); }

namespace {

// A string value of `root`\`sub` in one registry view (KEY_WOW64_32KEY or KEY_WOW64_64KEY), with
// REG_EXPAND_SZ's variables expanded; "" when the key or the value is absent or not a string.
std::string reg_string(HKEY root, const wchar_t* sub, const wchar_t* name, REGSAM view) {
    HKEY k = nullptr;
    if (RegOpenKeyExW(root, sub, 0, KEY_QUERY_VALUE | view, &k) != ERROR_SUCCESS) return {};
    DWORD type = 0, bytes = 0;
    std::wstring v;
    LONG rc = RegQueryValueExW(k, name, nullptr, &type, nullptr, &bytes);
    if (rc == ERROR_SUCCESS && (type == REG_SZ || type == REG_EXPAND_SZ) && bytes) {
        v.resize(bytes / sizeof(wchar_t) + 1, L'\0');
        rc = RegQueryValueExW(k, name, nullptr, &type, reinterpret_cast<BYTE*>(&v[0]), &bytes);
        v.resize(rc == ERROR_SUCCESS ? wcsnlen(v.c_str(), v.size()) : 0);
    }
    RegCloseKey(k);
    if (type == REG_EXPAND_SZ && !v.empty()) {
        DWORD n = ExpandEnvironmentStringsW(v.c_str(), nullptr, 0);
        std::wstring x(n, L'\0');
        if (n && ExpandEnvironmentStringsW(v.c_str(), &x[0], n)) v.assign(x.c_str());
    }
    return narrow(v);
}

// "C:/x/" and "c:\\x" as "c:/x": a root as the registry spells it, trailing separators gone.
std::string as_root(const std::string& v) {
    std::string s = slashes(v);
    while (s.size() > 3 && s.back() == '/') s.pop_back();
    return s;
}

struct RegPlace {
    HKEY root;
    const char* root_name;
    const wchar_t* sub;
    const char* sub_name;
    const wchar_t* value;
    const char* value_name;
    REGSAM view;
    const char* view_name;
};

}  // namespace

std::vector<std::string> steam_roots(std::vector<std::string>* notes) {
    // Where a Windows Steam install records its folder, in the order read: the user's SteamPath
    // (the client writes it at every start), then the machine's InstallPath (the installer
    // writes it, 32-bit, so under WOW6432Node), each in both views.
    static const RegPlace places[] = {
        {HKEY_CURRENT_USER, "HKCU", L"Software\\Valve\\Steam", "Software\\Valve\\Steam", L"SteamPath",
         "SteamPath", KEY_WOW64_64KEY, "64-bit view"},
        {HKEY_LOCAL_MACHINE, "HKLM", L"SOFTWARE\\Valve\\Steam", "SOFTWARE\\Valve\\Steam", L"InstallPath",
         "InstallPath", KEY_WOW64_32KEY, "32-bit view (WOW6432Node)"},
        {HKEY_LOCAL_MACHINE, "HKLM", L"SOFTWARE\\Valve\\Steam", "SOFTWARE\\Valve\\Steam", L"InstallPath",
         "InstallPath", KEY_WOW64_64KEY, "64-bit view"},
    };
    std::vector<std::string> out;
    for (const RegPlace& p : places) {
        const std::string v = reg_string(p.root, p.sub, p.value, p.view);
        const std::string where = std::string(p.root_name) + "\\" + p.sub_name + " " + p.value_name + " (" + p.view_name + ")";
        if (v.empty()) {
            if (notes) notes->push_back("the registry's " + where + " is not set");
            continue;
        }
        if (notes) notes->push_back("the registry's " + where + " names " + as_root(v));
        out.push_back(as_root(v));
    }
    std::string pf = env("ProgramFiles(x86)");
    if (!pf.empty()) out.push_back(as_root(pf) + "/Steam");
    else if (notes) notes->push_back("%ProgramFiles(x86)% is not set");
    return out;
}

bool sega_registry_install_path(std::string& out) {
    // The game's own key, which its Steam install script writes (installscript.vdf:
    // hkey_local_machine\\software\\sega\\sonicad2, install_path "%INSTALLDIR%"): the 32-bit
    // view first, where a 32-bit Steam client's write lands, then the 64-bit one.
    out.clear();
    for (REGSAM view : {KEY_WOW64_32KEY, KEY_WOW64_64KEY}) {
        const std::string v = reg_string(HKEY_LOCAL_MACHINE, L"Software\\Sega\\SonicAd2", L"install_path", view);
        if (!v.empty()) {
            out = as_root(v);
            return true;
        }
    }
    return false;
}

std::string now_utc_iso8601() {
    time_t t = std::time(nullptr);
    struct tm tm;
    gmtime_s(&tm, &t);
    char buf[40];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%S+00:00", &tm);
    return buf;
}

double monotonic_seconds() {
    LARGE_INTEGER f, c;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&c);
    return double(c.QuadPart) / double(f.QuadPart);
}

std::string os_name() { return "windows"; }

std::string arch_name() {
#if defined(__x86_64__) || defined(_M_X64)
    return "x86_64";
#elif defined(__aarch64__) || defined(_M_ARM64)
    return "aarch64";
#else
    return "unknown";
#endif
}

std::string self_exe_path() {
    wchar_t buf[32768];
    DWORD n = GetModuleFileNameW(nullptr, buf, 32768);
    if (n == 0 || n >= 32768) return {};
    return slashes(narrow(std::wstring(buf, n)));
}

unsigned cpu_count() {
    unsigned n = std::thread::hardware_concurrency();
    return n ? n : 1;
}

}  // namespace sa2p

#endif  // _WIN32
