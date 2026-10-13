// sa2_platform_posix.cpp: sa2_platform.h for Linux and other POSIX hosts.
#ifndef _WIN32

#include "sa2_platform.h"

#include <dirent.h>
#include <fcntl.h>
#include <limits.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <thread>

namespace sa2p {

bool stat_path(const std::string& path, Stat& out) {
    out = Stat{};
    struct stat st;
    struct stat lst;
    if (::lstat(path.c_str(), &lst) != 0) return false;
    out.is_link = S_ISLNK(lst.st_mode);
    if (::stat(path.c_str(), &st) != 0) {
        return false;  // a dangling link counts as absent
    }
    out.exists = true;
    out.is_dir = S_ISDIR(st.st_mode);
    out.is_file = S_ISREG(st.st_mode);
    out.size = uint64_t(st.st_size);
    out.mtime_ns = int64_t(st.st_mtim.tv_sec) * 1000000000LL + st.st_mtim.tv_nsec;
    return true;
}

std::string last_error(bool* absent) {
    int e = errno;
    if (absent) *absent = e == ENOENT || e == ENOTDIR;
    return std::strerror(e);
}

bool list_dir(const std::string& dir, std::vector<std::string>& names) {
    names.clear();
    DIR* d = ::opendir(dir.c_str());
    if (!d) return false;
    while (struct dirent* e = ::readdir(d)) {
        if (!std::strcmp(e->d_name, ".") || !std::strcmp(e->d_name, "..")) continue;
        names.emplace_back(e->d_name);
    }
    ::closedir(d);
    return true;
}

bool real_path(const std::string& path, std::string& out) {
    char* r = ::realpath(path.c_str(), nullptr);
    if (!r) return false;
    out = r;
    std::free(r);
    return true;
}

bool make_dirs(const std::string& path) {
    if (path.empty()) return false;
    std::string cur;
    size_t i = 0;
    if (path[0] == '/') { cur = "/"; i = 1; }
    while (i <= path.size()) {
        size_t j = path.find('/', i);
        if (j == std::string::npos) j = path.size();
        if (j > i) {
            if (!cur.empty() && cur.back() != '/') cur += '/';
            cur += path.substr(i, j - i);
            if (::mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) return false;
            struct stat st;
            if (::stat(cur.c_str(), &st) != 0 || !S_ISDIR(st.st_mode)) return false;
        }
        i = j + 1;
    }
    return true;
}

FILE* open_read(const std::string& path) {
    int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return nullptr;
    FILE* f = ::fdopen(fd, "rb");
    if (!f) ::close(fd);
    return f;
}

bool write_file_atomic(const std::string& path, const std::string& data) {
    std::string tmp = path + ".part";
    int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return false;
    size_t done = 0;
    while (done < data.size()) {
        ssize_t n = ::write(fd, data.data() + done, data.size() - done);
        if (n < 0) {
            if (errno == EINTR) continue;
            ::close(fd);
            ::unlink(tmp.c_str());
            return false;
        }
        done += size_t(n);
    }
    if (::fsync(fd) != 0 || ::close(fd) != 0) {
        ::unlink(tmp.c_str());
        return false;
    }
    if (::rename(tmp.c_str(), path.c_str()) != 0) {
        ::unlink(tmp.c_str());
        return false;
    }
    return true;
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
    const char* v = std::getenv(name);
    return v ? std::string(v) : std::string();
}

std::string home_dir() { return env("HOME"); }

std::vector<std::string> utf8_args(int argc, char** argv) {
    std::vector<std::string> out;
    for (int i = 0; i < argc; i++) out.emplace_back(argv[i]);
    return out;
}

std::string user_name() {
    std::string u = env("USER");
    return u.empty() ? env("LOGNAME") : u;
}

bool case_blind_paths() { return false; }

std::string config_home() {
    std::string x = env("XDG_CONFIG_HOME");
    if (!x.empty() && x[0] == '/') return x;
    std::string h = home_dir();
    return h.empty() ? std::string() : h + "/.config";
}

std::string data_home() {
    std::string x = env("XDG_DATA_HOME");
    if (!x.empty() && x[0] == '/') return x;
    std::string h = home_dir();
    return h.empty() ? std::string() : h + "/.local/share";
}

std::vector<std::string> steam_roots(std::vector<std::string>* notes) {
    std::vector<std::string> out;
    std::string h = home_dir();
    if (h.empty()) {
        if (notes) notes->push_back("HOME is unset: no Steam root to try");
        return out;
    }
    out.push_back(h + "/.steam/steam");
    out.push_back(h + "/.steam/root");
    std::string x = env("XDG_DATA_HOME");
    if (!x.empty() && x[0] == '/') out.push_back(x + "/Steam");
    out.push_back(h + "/.local/share/Steam");
    out.push_back(h + "/.steam/debian-installation");
    out.push_back(h + "/.var/app/com.valvesoftware.Steam/.local/share/Steam");   // Flatpak
    out.push_back(h + "/.var/app/com.valvesoftware.Steam/data/Steam");           // older Flatpak
    out.push_back(h + "/snap/steam/common/.local/share/Steam");                  // Snap
    return out;
}

bool sega_registry_install_path(std::string& out) {
    out.clear();
    return false;  // no registry on this host
}

std::string now_utc_iso8601() {
    time_t t = ::time(nullptr);
    struct tm tm;
    ::gmtime_r(&t, &tm);
    char buf[40];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%S+00:00", &tm);
    return buf;
}

double monotonic_seconds() {
    struct timespec ts;
    ::clock_gettime(CLOCK_MONOTONIC, &ts);
    return double(ts.tv_sec) + double(ts.tv_nsec) * 1e-9;
}

std::string os_name() {
#if defined(__linux__)
    return "linux";
#elif defined(__APPLE__)
    return "macos";
#else
    return "posix";
#endif
}

std::string arch_name() {
#if defined(__x86_64__)
    return "x86_64";
#elif defined(__aarch64__)
    return "aarch64";
#else
    return "unknown";
#endif
}

std::string self_exe_path() {
#if defined(__linux__)
    char buf[PATH_MAX];
    ssize_t n = ::readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n <= 0) return {};
    buf[n] = 0;
    return buf;
#else
    return {};
#endif
}

unsigned cpu_count() {
    unsigned n = std::thread::hardware_concurrency();
    return n ? n : 1;
}

}  // namespace sa2p

#endif  // !_WIN32
