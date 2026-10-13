// sa2_store.cpp: see sa2_store.h.
#include "sa2_store.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstring>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>
#endif

const char* const SA2_STORE_RECORD = "sa2recomp-store.json";
const char* const SA2_STORE_SCHEMA = "sa2-store/v1";
const char* const SA2_STORE_PART_SUFFIX = ".part";
const char* const SA2_STORE_TMP_DIR = ".sa2-tmp";
const char* const SA2_STORE_PARTIAL_MARKER = "sa2recomp-partial";

namespace {

void set_err(std::string* err, const std::string& what, const std::string& path,
             const std::string& why) {
    if (err) *err = what + " " + path + ": " + why;
}

std::atomic<unsigned> g_retries{0};

#ifdef _WIN32
std::wstring widen(const std::string& s) {
    if (s.empty()) return std::wstring();
    const int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), int(s.size()),
                                      nullptr, 0);
    if (n <= 0) return std::wstring();
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, s.data(), int(s.size()), &w[0], n);
    for (auto& c : w)
        if (c == L'/') c = L'\\';
    return w;
}

std::string narrow(const wchar_t* w) {
    const int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    if (n <= 1) return std::string();
    std::string s(size_t(n - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
    return s;
}

std::string last_error() {
    const DWORD e = GetLastError();
    char buf[256] = {0};
    FormatMessageA(FORMAT_MESSAGE_FROM_SYSTEM | FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, e,
                   MAKELANGID(LANG_ENGLISH, SUBLANG_DEFAULT), buf, sizeof buf - 1, nullptr);
    std::string s(buf);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' ')) s.pop_back();
    char code[32];
    std::snprintf(code, sizeof code, "Win32 error %lu", static_cast<unsigned long>(e));
    return s.empty() ? std::string(code) : s + " (" + code + ")";
}
#else
std::string last_error() { return std::strerror(errno); }
#endif

bool is_sep(char c) {
#ifdef _WIN32
    return c == '/' || c == '\\';
#else
    return c == '/';
#endif
}

}  // namespace

// ------------------------------------------------------------------------- Sa2File

Sa2File::~Sa2File() { close(nullptr); }

#ifdef _WIN32

bool Sa2File::is_open() const { return h_ != nullptr; }

bool Sa2File::open_read(const std::string& path, std::string* err) {
    close(nullptr);
    path_ = path;
    HANDLE h = CreateFileW(widen(path).c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        set_err(err, "cannot open", path, last_error());
        return false;
    }
    LARGE_INTEGER sz;
    if (!GetFileSizeEx(h, &sz)) {
        set_err(err, "cannot size", path, last_error());
        CloseHandle(h);
        return false;
    }
    h_ = h;
    size_ = uint64_t(sz.QuadPart);
    return true;
}

bool Sa2File::create(const std::string& path, std::string* err) {
    close(nullptr);
    path_ = path;
    HANDLE h = CreateFileW(widen(path).c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS,
                           FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        set_err(err, "cannot create", path, last_error());
        return false;
    }
    h_ = h;
    size_ = 0;
    return true;
}

bool Sa2File::read_at(uint64_t off, void* buf, size_t n, std::string* err) const {
    uint8_t* p = static_cast<uint8_t*>(buf);
    while (n) {
        const DWORD want = n > (DWORD(1) << 30) ? (DWORD(1) << 30) : DWORD(n);
        OVERLAPPED ov;
        std::memset(&ov, 0, sizeof ov);
        ov.Offset = DWORD(off & 0xFFFFFFFFu);
        ov.OffsetHigh = DWORD(off >> 32);
        DWORD got = 0;
        if (!ReadFile(static_cast<HANDLE>(h_), p, want, &got, &ov)) {
            set_err(err, "cannot read", path_, last_error());
            return false;
        }
        if (got == 0) {
            set_err(err, "cannot read", path_, "the file ended early");
            return false;
        }
        p += got;
        off += got;
        n -= got;
    }
    return true;
}

bool Sa2File::write(const void* data, size_t n, std::string* err) {
    const uint8_t* p = static_cast<const uint8_t*>(data);
    while (n) {
        const DWORD want = n > (DWORD(1) << 30) ? (DWORD(1) << 30) : DWORD(n);
        DWORD put = 0;
        if (!WriteFile(static_cast<HANDLE>(h_), p, want, &put, nullptr) || put == 0) {
            set_err(err, "cannot write", path_, last_error());
            return false;
        }
        p += put;
        n -= put;
        size_ += put;
    }
    return true;
}

bool Sa2File::sync(std::string* err) {
    if (!FlushFileBuffers(static_cast<HANDLE>(h_))) {
        set_err(err, "cannot flush", path_, last_error());
        return false;
    }
    return true;
}

bool Sa2File::close(std::string* err) {
    if (!h_) return true;
    const BOOL ok = CloseHandle(static_cast<HANDLE>(h_));
    h_ = nullptr;
    if (!ok) {
        set_err(err, "cannot close", path_, last_error());
        return false;
    }
    return true;
}

#else

bool Sa2File::is_open() const { return fd_ >= 0; }

bool Sa2File::open_read(const std::string& path, std::string* err) {
    close(nullptr);
    path_ = path;
    const int fd = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        set_err(err, "cannot open", path, last_error());
        return false;
    }
    struct stat st;
    if (fstat(fd, &st) != 0) {
        set_err(err, "cannot stat", path, last_error());
        ::close(fd);
        return false;
    }
    if (!S_ISREG(st.st_mode)) {
        set_err(err, "cannot open", path, "not a regular file");
        ::close(fd);
        return false;
    }
    fd_ = fd;
    size_ = uint64_t(st.st_size);
    return true;
}

bool Sa2File::create(const std::string& path, std::string* err) {
    close(nullptr);
    path_ = path;
    const int fd = ::open(path.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) {
        set_err(err, "cannot create", path, last_error());
        return false;
    }
    fd_ = fd;
    size_ = 0;
    return true;
}

bool Sa2File::read_at(uint64_t off, void* buf, size_t n, std::string* err) const {
    uint8_t* p = static_cast<uint8_t*>(buf);
    while (n) {
        const ssize_t got = ::pread(fd_, p, n, off_t(off));
        if (got < 0) {
            if (errno == EINTR) continue;
            set_err(err, "cannot read", path_, last_error());
            return false;
        }
        if (got == 0) {
            set_err(err, "cannot read", path_, "the file ended early");
            return false;
        }
        p += got;
        off += uint64_t(got);
        n -= size_t(got);
    }
    return true;
}

bool Sa2File::write(const void* data, size_t n, std::string* err) {
    const uint8_t* p = static_cast<const uint8_t*>(data);
    while (n) {
        const ssize_t put = ::write(fd_, p, n);
        if (put < 0) {
            if (errno == EINTR) continue;
            set_err(err, "cannot write", path_, last_error());
            return false;
        }
        p += put;
        n -= size_t(put);
        size_ += uint64_t(put);
    }
    return true;
}

bool Sa2File::sync(std::string* err) {
    if (::fsync(fd_) != 0) {
        set_err(err, "cannot fsync", path_, last_error());
        return false;
    }
    return true;
}

bool Sa2File::close(std::string* err) {
    if (fd_ < 0) return true;
    const int rc = ::close(fd_);
    fd_ = -1;
    if (rc != 0) {
        set_err(err, "cannot close", path_, last_error());
        return false;
    }
    return true;
}

#endif

// ------------------------------------------------------------------------- paths

std::string sa2_path_join(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    if (b.empty()) return a;
    if (is_sep(a.back())) return a + b;
    return a + "/" + b;
}

std::string sa2_path_parent(const std::string& p) {
    size_t end = p.size();
    while (end > 1 && is_sep(p[end - 1])) end--;
    size_t i = end;
    while (i > 0 && !is_sep(p[i - 1])) i--;
    if (i == 0) return ".";
    size_t j = i;
    while (j > 1 && is_sep(p[j - 1])) j--;
    return p.substr(0, j);
}

// ------------------------------------------------------------------------- file system

#ifdef _WIN32

Sa2FsKind sa2_fs_kind(const std::string& path) {
    const DWORD a = GetFileAttributesW(widen(path).c_str());
    if (a == INVALID_FILE_ATTRIBUTES) return Sa2FsKind::None;
    if (a & FILE_ATTRIBUTE_REPARSE_POINT) return Sa2FsKind::Other;
    if (a & FILE_ATTRIBUTE_DIRECTORY) return Sa2FsKind::Dir;
    return Sa2FsKind::File;
}

static bool mkdir_one(const std::string& p, std::string* err) {
    if (CreateDirectoryW(widen(p).c_str(), nullptr)) return true;
    if (GetLastError() == ERROR_ALREADY_EXISTS && sa2_fs_kind(p) == Sa2FsKind::Dir) return true;
    set_err(err, "cannot create directory", p, last_error());
    return false;
}

// Retries `op` while it fails the way a scanner or indexer holding the file makes it fail:
// about 1.6 s in all, then the last error stands.
template <class Op>
static bool with_retries(Op op) {
    static const DWORD kWaitMs[] = {5, 10, 20, 40, 80, 160, 320, 500, 500};
    for (size_t i = 0;; i++) {
        if (op()) return true;
        const DWORD e = GetLastError();
        if (i == sizeof kWaitMs / sizeof *kWaitMs ||
            (e != ERROR_ACCESS_DENIED && e != ERROR_SHARING_VIOLATION && e != ERROR_LOCK_VIOLATION))
            return false;
        g_retries++;
        Sleep(kWaitMs[i]);
        SetLastError(e);
    }
}

bool sa2_fs_rename(const std::string& from, const std::string& to, std::string* err) {
    DWORD flags = MOVEFILE_WRITE_THROUGH;
    if (sa2_fs_kind(from) != Sa2FsKind::Dir) flags |= MOVEFILE_REPLACE_EXISTING;
    const std::wstring f = widen(from), t = widen(to);
    if (!with_retries([&] { return MoveFileExW(f.c_str(), t.c_str(), flags) != 0; })) {
        set_err(err, "cannot rename", from, last_error());
        return false;
    }
    return true;
}

bool sa2_fs_remove_file(const std::string& path, std::string* err) {
    const std::wstring w = widen(path);
    const DWORD a = GetFileAttributesW(w.c_str());
    // A directory junction or symlink is removed as a directory, never followed.
    const bool as_dir = a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
    if (!with_retries([&] { return (as_dir ? RemoveDirectoryW(w.c_str()) : DeleteFileW(w.c_str())) != 0; })) {
        set_err(err, "cannot remove", path, last_error());
        return false;
    }
    return true;
}

bool sa2_fs_rename_noreplace(const std::string& from, const std::string& to, bool* existed,
                             std::string* err) {
    *existed = false;
    const std::wstring f = widen(from), t = widen(to);
    if (with_retries([&] { return MoveFileExW(f.c_str(), t.c_str(), MOVEFILE_WRITE_THROUGH) != 0; }))
        return true;
    const DWORD e = GetLastError();
    if (e == ERROR_ALREADY_EXISTS || e == ERROR_FILE_EXISTS) *existed = true;
    set_err(err, "cannot rename", from, last_error());
    return false;
}

bool sa2_fs_sync_dir(const std::string&, std::string*) { return true; }

static bool remove_dir_one(const std::string& p, std::string* err) {
    if (RemoveDirectoryW(widen(p).c_str())) return true;
    set_err(err, "cannot remove directory", p, last_error());
    return false;
}

bool sa2_fs_free_bytes(const std::string& path, uint64_t* out, std::string* err) {
    std::string p = path;
    while (sa2_fs_kind(p) == Sa2FsKind::None) {
        const std::string up = sa2_path_parent(p);
        if (up == p) break;
        p = up;
    }
    ULARGE_INTEGER avail;
    if (!GetDiskFreeSpaceExW(widen(p).c_str(), &avail, nullptr, nullptr)) {
        set_err(err, "cannot read free space of", p, last_error());
        return false;
    }
    *out = uint64_t(avail.QuadPart);
    return true;
}

static bool walk_into(const std::string& root, const std::string& rel,
                      std::vector<std::string>* files, std::vector<std::string>* dirs,
                      std::string* err) {
    const std::string dir = rel.empty() ? root : sa2_path_join(root, rel);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW((widen(dir) + L"\\*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) {
        if (GetLastError() == ERROR_FILE_NOT_FOUND) return true;
        set_err(err, "cannot list", dir, last_error());
        return false;
    }
    bool ok = true;
    do {
        if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
        const std::string name = narrow(fd.cFileName);
        const std::string sub = rel.empty() ? name : rel + "/" + name;
        const DWORD a = fd.dwFileAttributes;
        if ((a & FILE_ATTRIBUTE_DIRECTORY) && !(a & FILE_ATTRIBUTE_REPARSE_POINT)) {
            if (dirs) dirs->push_back(sub);
            if (!walk_into(root, sub, files, dirs, err)) {
                ok = false;
                break;
            }
        } else if (files) {
            files->push_back(sub);
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
    return ok;
}

#else

Sa2FsKind sa2_fs_kind(const std::string& path) {
    struct stat st;
    if (lstat(path.c_str(), &st) != 0) return Sa2FsKind::None;
    if (S_ISREG(st.st_mode)) return Sa2FsKind::File;
    if (S_ISDIR(st.st_mode)) return Sa2FsKind::Dir;
    return Sa2FsKind::Other;
}

static bool mkdir_one(const std::string& p, std::string* err) {
    if (::mkdir(p.c_str(), 0755) == 0) return true;
    if (errno == EEXIST && sa2_fs_kind(p) == Sa2FsKind::Dir) return true;
    set_err(err, "cannot create directory", p, last_error());
    return false;
}

bool sa2_fs_rename(const std::string& from, const std::string& to, std::string* err) {
    if (sa2_fs_kind(from) == Sa2FsKind::Dir && sa2_fs_kind(to) != Sa2FsKind::None) {
        set_err(err, "cannot rename", from, "the destination " + to + " exists");
        return false;
    }
    if (::rename(from.c_str(), to.c_str()) != 0) {
        set_err(err, "cannot rename", from, last_error());
        return false;
    }
    return true;
}

bool sa2_fs_rename_noreplace(const std::string& from, const std::string& to, bool* existed,
                             std::string* err) {
    *existed = false;
    if (::link(from.c_str(), to.c_str()) == 0) {
        if (::unlink(from.c_str()) != 0) {
            set_err(err, "cannot remove", from, last_error());
            return false;
        }
        return true;
    }
    if (errno == EEXIST) {
        *existed = true;
        set_err(err, "cannot rename", from, "the destination " + to + " exists");
        return false;
    }
    if (errno != EPERM && errno != ENOTSUP && errno != EOPNOTSUPP && errno != EXDEV &&
        errno != EMLINK) {
        set_err(err, "cannot link", from, last_error());
        return false;
    }
    // No hard links here (FAT, exFAT, some network and FUSE file systems): the installer is the
    // only writer of the destination while it runs, so a check then a rename is enough.
    if (sa2_fs_kind(to) != Sa2FsKind::None) {
        *existed = true;
        set_err(err, "cannot rename", from, "the destination " + to + " exists");
        return false;
    }
    return sa2_fs_rename(from, to, err);
}

bool sa2_fs_remove_file(const std::string& path, std::string* err) {
    if (::unlink(path.c_str()) == 0) return true;
    set_err(err, "cannot remove", path, last_error());
    return false;
}

bool sa2_fs_sync_dir(const std::string& path, std::string* err) {
    const int fd = ::open(path.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) {
        set_err(err, "cannot open directory", path, last_error());
        return false;
    }
    const int rc = ::fsync(fd);
    ::close(fd);
    if (rc != 0) {
        set_err(err, "cannot fsync directory", path, last_error());
        return false;
    }
    return true;
}

static bool remove_dir_one(const std::string& p, std::string* err) {
    if (::rmdir(p.c_str()) == 0) return true;
    set_err(err, "cannot remove directory", p, last_error());
    return false;
}

bool sa2_fs_free_bytes(const std::string& path, uint64_t* out, std::string* err) {
    std::string p = path;
    while (sa2_fs_kind(p) == Sa2FsKind::None) {
        const std::string up = sa2_path_parent(p);
        if (up == p) break;
        p = up;
    }
    struct statvfs sv;
    if (statvfs(p.c_str(), &sv) != 0) {
        set_err(err, "cannot read free space of", p, last_error());
        return false;
    }
    *out = uint64_t(sv.f_bavail) * uint64_t(sv.f_frsize);
    return true;
}

static bool walk_into(const std::string& root, const std::string& rel,
                      std::vector<std::string>* files, std::vector<std::string>* dirs,
                      std::string* err) {
    const std::string dir = rel.empty() ? root : sa2_path_join(root, rel);
    DIR* d = opendir(dir.c_str());
    if (!d) {
        set_err(err, "cannot list", dir, last_error());
        return false;
    }
    std::vector<std::string> names;
    while (dirent* e = readdir(d)) {
        if (!std::strcmp(e->d_name, ".") || !std::strcmp(e->d_name, "..")) continue;
        names.push_back(e->d_name);
    }
    closedir(d);
    std::sort(names.begin(), names.end());
    for (const auto& name : names) {
        const std::string sub = rel.empty() ? name : rel + "/" + name;
        if (sa2_fs_kind(sa2_path_join(root, sub)) == Sa2FsKind::Dir) {
            if (dirs) dirs->push_back(sub);
            if (!walk_into(root, sub, files, dirs, err)) return false;
        } else if (files) {
            files->push_back(sub);
        }
    }
    return true;
}

#endif

unsigned sa2_fs_retry_count() { return g_retries.load(); }

bool sa2_fs_mkdirs(const std::string& path, std::string* err) {
    if (path.empty() || sa2_fs_kind(path) == Sa2FsKind::Dir) return true;
    const std::string up = sa2_path_parent(path);
    if (up != path && up != "." && !sa2_fs_mkdirs(up, err)) return false;
    return mkdir_one(path, err);
}

bool sa2_fs_walk(const std::string& dir, std::vector<std::string>* files,
                 std::vector<std::string>* dirs, std::string* err) {
    return walk_into(dir, "", files, dirs, err);
}

bool sa2_fs_remove_tree(const std::string& dir, std::string* err) {
    const Sa2FsKind k = sa2_fs_kind(dir);
    if (k == Sa2FsKind::None) return true;
    if (k != Sa2FsKind::Dir) return sa2_fs_remove_file(dir, err);
    std::vector<std::string> files, dirs;
    if (!sa2_fs_walk(dir, &files, &dirs, err)) return false;
    for (const auto& f : files)
        if (!sa2_fs_remove_file(sa2_path_join(dir, f), err)) return false;
    // Deepest first: a walk lists a directory before what is under it.
    for (auto it = dirs.rbegin(); it != dirs.rend(); ++it)
        if (!remove_dir_one(sa2_path_join(dir, *it), err)) return false;
    return remove_dir_one(dir, err);
}

// ------------------------------------------------------------------------- the store

std::string sa2_store_partial_dir(const std::string& store_dir) {
    std::string s = store_dir;
    while (s.size() > 1 && is_sep(s.back())) s.pop_back();
    return s + ".partial";
}

namespace {

std::string partial_marker_text() { return std::string(SA2_STORE_SCHEMA) + " partial store\n"; }

bool ieq_ascii(const std::string& a, const char* b) {
    const size_t n = std::strlen(b);
    if (a.size() != n) return false;
    for (size_t i = 0; i < n; i++) {
        char x = a[i], y = b[i];
        if (x >= 'A' && x <= 'Z') x = char(x + 32);
        if (y >= 'A' && y <= 'Z') y = char(y + 32);
        if (x != y) return false;
    }
    return true;
}

bool device_name(const std::string& comp) {
    std::string base = comp.substr(0, comp.find('.'));
    while (!base.empty() && base.back() == ' ') base.pop_back();
    static const char* const kDev[] = {"CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$"};
    for (const char* d : kDev)
        if (ieq_ascii(base, d)) return true;
    if (base.size() == 4) {
        const std::string head = base.substr(0, 3);
        const char c = base[3];
        if ((ieq_ascii(head, "COM") || ieq_ascii(head, "LPT")) && c >= '0' && c <= '9')
            return true;
    }
    return false;
}

}  // namespace

bool sa2_store_reserved_name(const std::string& name) {
    const std::string record_part = std::string(SA2_STORE_RECORD) + SA2_STORE_PART_SUFFIX;
    return ieq_ascii(name, SA2_STORE_RECORD) || ieq_ascii(name, record_part.c_str()) ||
           ieq_ascii(name, SA2_STORE_TMP_DIR) || ieq_ascii(name, SA2_STORE_PARTIAL_MARKER);
}

bool sa2_store_write_partial_marker(const std::string& partial_dir, std::string* err) {
    const std::string text = partial_marker_text();
    const std::string path = sa2_path_join(partial_dir, SA2_STORE_PARTIAL_MARKER);
    Sa2File f;
    if (!f.create(path, err) || !f.write(text.data(), text.size(), err) || !f.sync(err) ||
        !f.close(err))
        return false;
    return sa2_fs_sync_dir(partial_dir, err);
}

bool sa2_store_has_partial_marker(const std::string& partial_dir) {
    Sa2File f;
    if (!f.open_read(sa2_path_join(partial_dir, SA2_STORE_PARTIAL_MARKER), nullptr)) return false;
    const std::string want = partial_marker_text();
    if (f.size() != want.size()) return false;
    std::string got(want.size(), '\0');
    return f.read_at(0, &got[0], got.size(), nullptr) && got == want;
}

bool sa2_store_safe_relpath(const std::string& rel, std::string* why) {
    auto no = [&](const std::string& w) {
        if (why) *why = w;
        return false;
    };
    if (rel.empty()) return no("an empty name");
    if (rel.size() > 1024) return no("a name longer than 1024 bytes");
    if (rel[0] == '/') return no("an absolute name");
    if (rel.size() >= 2 && rel[1] == ':') return no("a drive-letter name");
    size_t start = 0;
    while (start <= rel.size()) {
        size_t end = rel.find('/', start);
        if (end == std::string::npos) end = rel.size();
        const std::string comp = rel.substr(start, end - start);
        if (comp.empty()) return no("an empty path component");
        if (comp == "." || comp == "..") return no("a '" + comp + "' path component");
        if (comp.size() > 255) return no("a path component longer than 255 bytes");
        for (unsigned char c : comp) {
            if (c < 0x20 || c == 0x7F) return no("a control character");
            if (std::strchr(":*?\"<>|\\", c)) return no(std::string("the character '") +
                                                        char(c) + "'");
        }
        if (comp.back() == '.' || comp.back() == ' ')
            return no("a path component ending in a dot or a space");
        if (device_name(comp)) return no("the device name '" + comp + "'");
        start = end + 1;
    }
    return true;
}

std::string sa2_json_escape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 8);
    const size_t n = s.size();
    for (size_t i = 0; i < n;) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80) {
            if (c == '"' || c == '\\') {
                o += '\\';
                o += char(c);
            } else if (c < 0x20 || c == 0x7F) {
                char b[8];
                std::snprintf(b, sizeof b, "\\u%04x", c);
                o += b;
            } else {
                o += char(c);
            }
            i++;
            continue;
        }
        // A complete, shortest-form UTF-8 sequence passes through; anything else is escaped.
        size_t len = 0;
        uint32_t cp = 0;
        if ((c & 0xE0) == 0xC0) len = 2, cp = c & 0x1F;
        else if ((c & 0xF0) == 0xE0) len = 3, cp = c & 0x0F;
        else if ((c & 0xF8) == 0xF0) len = 4, cp = c & 0x07;
        bool ok = len && i + len <= n;
        for (size_t k = 1; ok && k < len; k++) {
            const unsigned char d = static_cast<unsigned char>(s[i + k]);
            if ((d & 0xC0) != 0x80) ok = false;
            cp = (cp << 6) | (d & 0x3F);
        }
        if (ok) {
            const uint32_t min = len == 2 ? 0x80 : len == 3 ? 0x800 : 0x10000;
            if (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) ok = false;
        }
        if (ok) {
            o.append(s, i, len);
            i += len;
        } else {
            char b[8];
            std::snprintf(b, sizeof b, "\\u%04x", c);
            o += b;
            i++;
        }
    }
    return o;
}

std::string sa2_store_record_json(const Sa2StoreRecord& r) {
    auto q = [](const std::string& s) { return "\"" + sa2_json_escape(s) + "\""; };
    auto list = [&](const std::vector<std::string>& v) {
        std::string o = "[";
        for (size_t i = 0; i < v.size(); i++) o += (i ? ", " : "") + q(v[i]);
        return o + "]";
    };
    std::string o;
    o += "{\n";
    o += " \"schema\": " + q(SA2_STORE_SCHEMA) + ",\n";
    o += " \"table_id\": " + q(r.table_id) + ",\n";
    o += " \"receipt_sha256\": " + q(r.receipt_sha256) + ",\n";
    o += " \"complete\": true,\n";
    o += " \"source\": {\"kind\": " + q(r.source_kind) +
         ", \"bytes\": " + std::to_string(r.source_bytes) + ", \"root\": " + q(r.source_root) +
         "},\n";
    o += " \"markers\": " + list(r.markers) + ",\n";
    o += " \"missing\": " + list(r.missing) + ",\n";
    o += " \"modified\": " + list(r.modified) + ",\n";
    o += " \"file_count\": " + std::to_string(r.files.size()) + ",\n";
    o += " \"files\": [\n";
    for (size_t i = 0; i < r.files.size(); i++) {
        const Sa2StoreFile& f = r.files[i];
        o += "  {\"path\": " + q(f.path) + ", \"bytes\": " + std::to_string(f.size) +
             ", \"sha256\": " + q(f.sha256) + ", \"kind\": " + q(f.kind);
        if (!f.from.empty() && f.from != f.path) o += ", \"from\": " + q(f.from);
        o += i + 1 < r.files.size() ? "},\n" : "}\n";
    }
    o += " ]\n}\n";
    return o;
}

bool sa2_store_write_record(const std::string& dir, const Sa2StoreRecord& rec, std::string* err) {
    const std::string text = sa2_store_record_json(rec);
    const std::string path = sa2_path_join(dir, SA2_STORE_RECORD);
    const std::string part = path + SA2_STORE_PART_SUFFIX;
    Sa2File f;
    if (!f.create(part, err) || !f.write(text.data(), text.size(), err) || !f.sync(err) ||
        !f.close(err))
        return false;
    return sa2_fs_rename(part, path, err);
}

bool sa2_store_read_identity(const std::string& store_dir, std::string* table_id,
                             std::string* receipt_sha256) {
    Sa2File f;
    if (!f.open_read(sa2_path_join(store_dir, SA2_STORE_RECORD), nullptr)) return false;
    const size_t n = size_t(std::min<uint64_t>(f.size(), 4096));
    std::string head(n, '\0');
    if (!n || !f.read_at(0, &head[0], n, nullptr)) return false;
    auto field = [&](const char* key, std::string* out) {
        const std::string k = std::string("\"") + key + "\": \"";
        const size_t at = head.find(k);
        if (at == std::string::npos) return false;
        const size_t b = at + k.size();
        const size_t e = head.find('"', b);
        if (e == std::string::npos) return false;
        if (out) *out = head.substr(b, e - b);
        return true;
    };
    return head.find(std::string("\"schema\": \"") + SA2_STORE_SCHEMA + "\"") != std::string::npos &&
           head.find("\"complete\": true") != std::string::npos && field("table_id", table_id) &&
           field("receipt_sha256", receipt_sha256);
}
