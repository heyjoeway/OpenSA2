// sa2_launch_win32.cpp: sa2_launch_platform.h on Windows (zig c++ -target x86_64-windows-gnu).
//
// The session's channels. Windows has no FIFO; each of the launcher's is a named pipe
// (\\.\pipe\sa2recomp-<pid>-<n>-<name>, fifo_path) with one instance, whose server end the
// launcher makes and holds:
//  - the game's stream (open_fifo_reader): inbound. The recorder opens SA2_REC_TRACE as a client
//    with open(2) (host/sa2_present_rec.c), and writes it as it would a FIFO.
//  - the game's keyboard (open_fifo_holder): outbound. The runtime checks it exists before the
//    run and opens it as a client at its first keyboard poll (runtime/sa2_win32.c
//    sa2_win32_pipe_open), so the launcher makes it before the game starts and holds it for the
//    whole session: the game never reads end of file while the launcher lives.
//  - the player's stream (open_fifo_holder, player_reads_stream_by_path): outbound. Host 2's
//    Windows build is given the pipe's name in place of --video-fd: the selftest's player reads
//    it by path (host/player/stream.cpp open), play's opens it as --video-pipe for its
//    interactive mode (player.cpp open_session_pipe, stream.cpp open_live).
//  - play's cancel channel (open_fifo_holder, --cancel-pipe: outbound, byte 1 the cancel) and end
//    channel (open_fifo_reader, --end-pipe: inbound, one byte the window's close), the forms of
//    --cancel-fd and --end-fd. SIGUSR1, the launcher's end request to the player, is the named
//    event Local\sa2recomp-player-end-<the player's pid>, which the player makes and waits on
//    (player.cpp watch_end_event) and send_signal's User1 sets.
//  - play's keys. The player's key sender is the server of a pipe of its own (host/player/input.cpp
//    KeySender::start, player_serves_keys), made once its window is up, so the game's keyboard
//    pipe, which must exist before the game starts, stays the launcher's: the launcher opens the
//    player's as its client (open_pipe_client, overlapped so a read waits with a bound and wakes
//    at the first byte, read_wait) and hands every byte on (sa2_launch.cpp KeyForward).
// Each server is made non-blocking (PIPE_NOWAIT) so a read or a connect never waits for a client
// that is not there yet; ConnectNamedPipe is asked at each read, wait or write until a client has
// opened it. An outbound pipe is switched to blocking writes (PIPE_WAIT) once connected, so a
// write waits for room as a FIFO's does and the reader receives every byte; a write to a server
// no client opens within kConnectWaitMs fails as a write to a FIFO with no reader would.
//
// The processes. Each child the launcher starts with own_group gets a console of its own with no
// window (CREATE_NO_WINDOW): a Ctrl-C at the launcher's terminal reaches the launcher alone, as a
// process group of its own does on POSIX. SIGTERM, the host's quit, is the runtime's console
// control event (runtime/sa2_win32.c sa2_win32_on_console_stop): a Ctrl-C sent into the child's
// own console by a helper, this executable re-run as `--console-stop PID` (platform_entry), which
// attaches to that console alone, ignores the event itself and sends it to every process there,
// which is the child alone. A second one finds the runtime's handler declining it, and the
// default action ends the process, as a second SIGTERM does on Linux. Measured under Proton's
// Wine, 2026-10-11: a CTRL_BREAK_EVENT to a child made with CREATE_NEW_PROCESS_GROUP ends it with
// exit code 1 before its handler runs, and a CTRL_C_EVENT into its own console reaches its
// handler; so no process group is made. SIGKILL is TerminateProcess. There is no SIGUSR1.
//
// The descriptors a POSIX child inherits by number (Spawn::fds) have no Windows form here: a
// spawn that asks for one is refused, so the launcher never starts a child that waits on a
// descriptor it was never given.
//
// Every child is put in one job object that ends its processes when the launcher's last handle
// on it closes (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE): a launcher ended from outside (its console
// window closed, Task Manager) leaves no game or player behind. A host that refuses the job
// (an older Windows inside a job of its own) runs the children without it.
#include "sa2_launch_platform.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include <atomic>
#include <cstdlib>
#include <cstring>
#include <cwchar>
#include <map>
#include <mutex>
#include <vector>

namespace sa2l {

namespace {

volatile LONG g_interrupts = 0;
BOOL WINAPI on_console(DWORD type) {
    if (type != CTRL_C_EVENT && type != CTRL_BREAK_EVENT && type != CTRL_CLOSE_EVENT) return FALSE;
    InterlockedIncrement(&g_interrupts);
    return TRUE;
}

constexpr DWORD kConnectWaitMs = 60000;   // a pipe's client, at the latest, after its first use
constexpr DWORD kPipeBuffer = 1u << 20;
constexpr const char* kStopArg = "--console-stop";

std::wstring wide(const std::string& s) {
    if (s.empty()) return {};
    int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), &w[0], n);
    return w;
}

std::string narrow(const wchar_t* w, size_t len) {
    if (!len) return {};
    int n = WideCharToMultiByte(CP_UTF8, 0, w, int(len), nullptr, 0, nullptr, nullptr);
    std::string s(size_t(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, int(len), &s[0], n, nullptr, nullptr);
    return s;
}

std::string win_error(const std::string& what, DWORD e = GetLastError()) {
    return what + ": Windows error " + std::to_string(e);
}

bool is_pipe_name(const std::string& p) {
    if (p.size() <= 9) return false;
    for (int i = 0; i < 9; i++) {
        const char want = "\\\\.\\pipe\\"[i], got = p[size_t(i)];
        if (want == '\\' ? (got != '\\' && got != '/') : got != want) return false;
    }
    return true;
}

// The server ends this layer made, and whether a client has connected to each.
enum class Link { Listening, Connected, Gone };
struct Server {
    bool outbound = false;
    Link link = Link::Listening;
};
std::mutex g_mu;
std::map<HANDLE, Server> g_servers;
std::atomic<unsigned> g_pipe_serial{0};

// The overlapped clients open_pipe_client made: one read at a time, which may stay pending
// across read_wait calls, and what it brought that the caller has not taken yet.
struct Client {
    HANDLE event = nullptr;
    OVERLAPPED ov{};
    bool pending = false;
    std::vector<char> buf = std::vector<char>(1u << 16);
    DWORD have = 0, at = 0;
};
std::map<HANDLE, Client> g_clients;

constexpr const char* kEndEventPrefix = "Local\\sa2recomp-player-end-";   // player.cpp's

// The system timer at 1 ms for the launcher (winmm's timeBeginPeriod, found at run time, so the
// link gains no import): its waits on a pipe (the relay's, a writer's for its reader) sleep a
// millisecond, where Windows' default tick is 15.6 ms. Set before main, for the whole process.
struct TimerResolution {
    TimerResolution() {
        if (HMODULE mm = LoadLibraryW(L"winmm.dll"))
            if (void* begin = reinterpret_cast<void*>(GetProcAddress(mm, "timeBeginPeriod")))
                reinterpret_cast<UINT(WINAPI*)(UINT)>(begin)(1);
    }
} g_timer_resolution;

HANDLE g_job = nullptr;
std::once_flag g_job_once;
void make_job() {
    HANDLE j = CreateJobObjectW(nullptr, nullptr);
    if (!j) return;
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION info{};
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if (!SetInformationJobObject(j, JobObjectExtendedLimitInformation, &info, sizeof info)) {
        CloseHandle(j);
        return;
    }
    g_job = j;
}

HANDLE handle_of(Fd fd) { return reinterpret_cast<HANDLE>(fd); }

// The server's link now, asking ConnectNamedPipe once (non-blocking); Connected for a handle that
// is no server of ours (an anonymous pipe, which is always connected).
Link link_of(HANDLE h) {
    std::lock_guard<std::mutex> lk(g_mu);
    auto it = g_servers.find(h);
    if (it == g_servers.end()) return Link::Connected;
    Server& s = it->second;
    if (s.link != Link::Listening) return s.link;
    // Non-blocking: zero with ERROR_PIPE_LISTENING while no client has opened it,
    // ERROR_PIPE_CONNECTED once one has, ERROR_NO_DATA when one came and has closed already.
    if (!ConnectNamedPipe(h, nullptr)) {
        const DWORD e = GetLastError();
        if (e == ERROR_PIPE_LISTENING) return Link::Listening;
        s.link = e == ERROR_PIPE_CONNECTED ? Link::Connected : Link::Gone;
    } else {
        s.link = Link::Connected;
    }
    if (s.link == Link::Connected && s.outbound) {
        DWORD mode = PIPE_READMODE_BYTE | PIPE_WAIT;   // writes wait for room, as a FIFO's do
        SetNamedPipeHandleState(h, &mode, nullptr, nullptr);
    }
    return s.link;
}

Fd make_server(const std::string& path, bool outbound, std::string* error) {
    if (!is_pipe_name(path)) {
        if (error) *error = path + " is no named pipe (\\\\.\\pipe\\<name>), this host's FIFO";
        return kNoFd;
    }
    // One instance, refused when the name exists already (a session's pipes are made fresh).
    HANDLE h = CreateNamedPipeW(wide(path).c_str(),
                                (outbound ? PIPE_ACCESS_OUTBOUND : PIPE_ACCESS_INBOUND) | FILE_FLAG_FIRST_PIPE_INSTANCE,
                                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_NOWAIT | PIPE_REJECT_REMOTE_CLIENTS, 1,
                                outbound ? kPipeBuffer : 0, outbound ? 0 : kPipeBuffer, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        if (error) *error = win_error("CreateNamedPipe " + path);
        return kNoFd;
    }
    std::lock_guard<std::mutex> lk(g_mu);
    Server s;
    s.outbound = outbound;
    g_servers[h] = s;
    return reinterpret_cast<Fd>(h);
}

// The MSVC runtime's rule for one argument of a command line (CommandLineToArgvW reads it back).
void quote_arg(std::wstring& line, const std::wstring& a) {
    if (!a.empty() && a.find_first_of(L" \t\n\v\"") == std::wstring::npos) {
        line += a;
        return;
    }
    line += L'"';
    for (size_t i = 0;; i++) {
        size_t slashes = 0;
        while (i < a.size() && a[i] == L'\\') {
            i++;
            slashes++;
        }
        if (i == a.size()) {
            line.append(slashes * 2, L'\\');
            break;
        }
        if (a[i] == L'"') {
            line.append(slashes * 2 + 1, L'\\');
        } else {
            line.append(slashes, L'\\');
        }
        line += a[i];
    }
    line += L'"';
}

// An inheritable handle on `path` for a child's standard stream: "" is the null device.
HANDLE std_handle(const std::string& path, bool write, std::string* error) {
    SECURITY_ATTRIBUTES sa{sizeof sa, nullptr, TRUE};
    const std::wstring w = path.empty() ? std::wstring(L"NUL") : wide(path);
    HANDLE h = CreateFileW(w.c_str(), write ? GENERIC_WRITE : GENERIC_READ,
                           FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, &sa,
                           write && !path.empty() ? CREATE_ALWAYS : OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE && error) *error = win_error("open " + (path.empty() ? std::string("NUL") : path));
    return h;
}

}  // namespace

bool sessions_supported(std::string*) { return true; }

// Play runs host 2's interactive mode over the session's named pipes (above).
bool play_supported(std::string*) { return true; }

std::string fifo_path(const std::string&, const std::string& name) {
    return "\\\\.\\pipe\\sa2recomp-" + std::to_string(GetCurrentProcessId()) + "-" +
           std::to_string(g_pipe_serial.fetch_add(1)) + "-" + name;
}

bool player_reads_stream_by_path() { return true; }
bool player_serves_keys() { return true; }

Fd open_pipe_client(const std::string& path, bool* absent, std::string* error) {
    *absent = false;
    HANDLE h = CreateFileW(wide(path).c_str(), GENERIC_READ, 0, nullptr, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        const DWORD e = GetLastError();
        *absent = e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND || e == ERROR_PIPE_BUSY;
        if (!*absent && error) *error = win_error("open " + path, e);
        return kNoFd;
    }
    HANDLE ev = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!ev) {
        if (error) *error = win_error("CreateEvent");
        CloseHandle(h);
        return kNoFd;
    }
    std::lock_guard<std::mutex> lk(g_mu);
    g_clients[h].event = ev;
    return reinterpret_cast<Fd>(h);
}

long read_wait(Fd fd, void* buf, size_t n, int timeout_ms) {
    if (fd == kNoFd) return -2;
    HANDLE h = handle_of(fd);
    Client* c = nullptr;
    {
        std::lock_guard<std::mutex> lk(g_mu);
        auto it = g_clients.find(h);
        if (it != g_clients.end()) c = &it->second;
    }
    if (!c) {                                 // not one of open_pipe_client's: poll as a FIFO
        const int r = wait_readable(fd, timeout_ms);
        return r < 0 ? -2 : r == 0 ? -1 : read_some(fd, buf, n);
    }
    auto gone = [](DWORD e) { return e == ERROR_BROKEN_PIPE || e == ERROR_PIPE_NOT_CONNECTED || e == ERROR_NO_DATA; };
    if (c->at == c->have) {
        if (!c->pending) {
            ResetEvent(c->event);
            c->ov = OVERLAPPED{};
            c->ov.hEvent = c->event;
            DWORD got = 0;
            if (ReadFile(h, c->buf.data(), DWORD(c->buf.size()), &got, &c->ov)) {
                c->have = got;
                c->at = 0;
            } else {
                const DWORD e = GetLastError();
                if (e != ERROR_IO_PENDING) return gone(e) ? 0 : -2;
                c->pending = true;
            }
        }
        if (c->pending) {
            const DWORD w = WaitForSingleObject(c->event, timeout_ms < 0 ? INFINITE : DWORD(timeout_ms));
            if (w == WAIT_TIMEOUT) return -1;
            if (w != WAIT_OBJECT_0) return -2;
            DWORD got = 0;
            const BOOL ok = GetOverlappedResult(h, &c->ov, &got, FALSE);
            c->pending = false;
            if (!ok) {
                const DWORD e = GetLastError();
                if (e != ERROR_MORE_DATA) return gone(e) ? 0 : -2;
            }
            c->have = got;
            c->at = 0;
        }
        if (c->have == 0) return -1;          // a message of no bytes: nothing yet
    }
    const DWORD take = DWORD(n < size_t(c->have - c->at) ? n : size_t(c->have - c->at));
    std::memcpy(buf, c->buf.data() + c->at, take);
    c->at += take;
    return long(take);
}

bool make_fifo(const std::string& path, std::string* error) {
    if (is_pipe_name(path)) return true;     // made by open_fifo_reader or open_fifo_holder
    if (error) *error = path + " is no named pipe (\\\\.\\pipe\\<name>), this host's FIFO";
    return false;
}

Fd open_fifo_reader(const std::string& path, std::string* error) { return make_server(path, false, error); }
Fd open_fifo_holder(const std::string& path, std::string* error) { return make_server(path, true, error); }

bool make_pipe(Fd& r, Fd& w, std::string* error) {
    HANDLE a = nullptr, b = nullptr;
    if (!CreatePipe(&a, &b, nullptr, 1u << 20)) {
        if (error) *error = win_error("CreatePipe");
        return false;
    }
    r = reinterpret_cast<Fd>(a);
    w = reinterpret_cast<Fd>(b);
    return true;
}

void close_fd(Fd& fd) {
    if (fd == kNoFd) return;
    HANDLE h = handle_of(fd);
    {
        std::lock_guard<std::mutex> lk(g_mu);
        g_servers.erase(h);
        auto it = g_clients.find(h);
        if (it != g_clients.end()) {
            if (it->second.pending) {             // the read is ours to finish before its buffer goes
                CancelIoEx(h, &it->second.ov);
                DWORD got = 0;
                GetOverlappedResult(h, &it->second.ov, &got, TRUE);
            }
            CloseHandle(it->second.event);
            g_clients.erase(it);
        }
    }
    // CloseHandle, not DisconnectNamedPipe: the client keeps what was written, as a FIFO's reader
    // does, and then reads end of file.
    CloseHandle(h);
    fd = kNoFd;
}

void set_pipe_size(Fd, size_t) {}   // the buffer is the server's, set when it is made

long read_some(Fd fd, void* buf, size_t n) {
    if (fd == kNoFd) return -2;
    HANDLE h = handle_of(fd);
    const Link l = link_of(h);
    if (l == Link::Listening) return -1;
    // Gone (a client that came and closed before the first look) may still hold what it wrote.
    DWORD avail = 0;
    if (!PeekNamedPipe(h, nullptr, 0, nullptr, &avail, nullptr)) {
        const DWORD e = GetLastError();
        return l == Link::Gone || e == ERROR_BROKEN_PIPE || e == ERROR_PIPE_NOT_CONNECTED || e == ERROR_NO_DATA ? 0 : -2;
    }
    if (!avail) return l == Link::Gone ? 0 : -1;
    DWORD got = 0;
    const DWORD want = n < avail ? DWORD(n) : avail;
    if (!ReadFile(h, buf, want, &got, nullptr)) {
        const DWORD e = GetLastError();
        if (e == ERROR_NO_DATA) return -1;
        return e == ERROR_BROKEN_PIPE || e == ERROR_PIPE_NOT_CONNECTED ? 0 : -2;
    }
    return long(got);
}

bool write_all(Fd fd, const void* buf, size_t n) {
    if (fd == kNoFd) return false;
    HANDLE h = handle_of(fd);
    for (DWORD waited = 0; link_of(h) == Link::Listening; waited += 5) {
        if (waited >= kConnectWaitMs) return false;     // no reader came: as EPIPE
        Sleep(5);
    }
    if (link_of(h) == Link::Gone) return false;
    const char* p = static_cast<const char*>(buf);
    while (n) {
        DWORD put = 0;
        const DWORD chunk = n > (1u << 30) ? (1u << 30) : DWORD(n);
        if (!WriteFile(h, p, chunk, &put, nullptr)) return false;
        if (put == 0) {
            Sleep(1);
            continue;
        }
        p += put;
        n -= put;
    }
    return true;
}

int wait_readable(Fd fd, int timeout_ms) {
    if (fd == kNoFd) return -1;
    HANDLE h = handle_of(fd);
    const ULONGLONG until = GetTickCount64() + ULONGLONG(timeout_ms > 0 ? timeout_ms : 0);
    for (;;) {
        const Link l = link_of(h);
        if (l == Link::Gone) return 1;                  // the read reports end of file
        if (l == Link::Connected) {
            DWORD avail = 0;
            if (!PeekNamedPipe(h, nullptr, 0, nullptr, &avail, nullptr)) return 1;   // broken: the read says
            if (avail) return 1;
        }
        if (timeout_ms <= 0 || GetTickCount64() >= until) return 0;
        Sleep(1);       // the relay's wake for the game's next frame: about a millisecond (TimerResolution)
    }
}

bool hung_up(Fd fd) {
    if (fd == kNoFd) return true;
    HANDLE h = handle_of(fd);
    const Link l = link_of(h);
    if (l == Link::Listening) return false;
    if (l == Link::Gone) return true;
    DWORD avail = 0;
    return !PeekNamedPipe(h, nullptr, 0, nullptr, &avail, nullptr);
}

bool spawn(const Spawn& s, Proc& p, std::string* error) {
    if (!s.fds.empty()) {
        if (error) *error = "a child given descriptors by number (" + s.exe + ") has no Windows form: this host's "
                            "channels are named pipes, passed by name";
        return false;
    }
    std::wstring line;
    for (size_t i = 0; i < s.argv.size(); i++) {
        if (i) line += L' ';
        quote_arg(line, wide(s.argv[i]));
    }
    // The environment block: UTF-16 "name=value" strings, each ended by a NUL, then one more.
    std::wstring block;
    for (const auto& e : s.env) {
        block += wide(e.first + "=" + e.second);
        block += L'\0';
    }
    if (block.empty()) block += L'\0';
    block += L'\0';
    HANDLE in = std_handle(s.stdin_path, false, error);
    if (in == INVALID_HANDLE_VALUE) return false;
    HANDLE out = std_handle(s.stdout_path, true, error);
    if (out == INVALID_HANDLE_VALUE) {
        CloseHandle(in);
        return false;
    }
    HANDLE err = s.stderr_path == s.stdout_path && !s.stdout_path.empty() ? out : std_handle(s.stderr_path, true, error);
    if (err == INVALID_HANDLE_VALUE) {
        CloseHandle(in);
        CloseHandle(out);
        return false;
    }
    STARTUPINFOW si{};
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdInput = in;
    si.hStdOutput = out;
    si.hStdError = err;
    PROCESS_INFORMATION pi{};
    // own_group: a console of its own with no window, so a Ctrl-C at the launcher's terminal
    // reaches the launcher alone, and send_signal's Ctrl-C reaches this child alone.
    const DWORD flags = CREATE_UNICODE_ENVIRONMENT | (s.own_group ? CREATE_NO_WINDOW : 0);
    const std::wstring exe = wide(s.exe), cwd = wide(s.cwd);
    const BOOL ok = CreateProcessW(exe.c_str(), &line[0], nullptr, nullptr, TRUE, flags, &block[0],
                                   cwd.empty() ? nullptr : cwd.c_str(), &si, &pi);
    const DWORD e = GetLastError();
    CloseHandle(in);
    CloseHandle(out);
    if (err != out) CloseHandle(err);
    if (!ok) {
        if (error) *error = win_error("CreateProcess " + s.exe, e);
        return false;
    }
    std::call_once(g_job_once, make_job);
    if (g_job) AssignProcessToJobObject(g_job, pi.hProcess);   // refused: it runs without the job
    CloseHandle(pi.hThread);
    p.id = reinterpret_cast<intptr_t>(pi.hProcess);
    p.running = true;
    p.status = 0;
    p.signalled = false;
    return true;
}

bool wait_exit(Proc& p, double timeout_s) {
    if (!p.running) return true;
    // A wait past what a DWORD of milliseconds holds (about 49 days) is a wait without end;
    // converting it would be undefined, and wrap to a short wait.
    const double ms = timeout_s * 1000.0;
    const DWORD wait = ms >= double(INFINITE - 1) ? INFINITE : ms <= 0 ? 0 : DWORD(ms);
    DWORD r = WaitForSingleObject(reinterpret_cast<HANDLE>(p.id), wait);
    if (r != WAIT_OBJECT_0) return false;
    DWORD code = 0;
    GetExitCodeProcess(reinterpret_cast<HANDLE>(p.id), &code);
    p.running = false;
    p.status = int(code);
    return true;
}

bool send_signal(const Proc& p, Signal s, bool) {
    if (!p.running) return false;
    HANDLE h = reinterpret_cast<HANDLE>(p.id);
    if (s == Signal::Kill) return TerminateProcess(h, 1) != 0;
    if (s == Signal::Nudge) return false;            // no such signal (sa2_launch_platform.h)
    if (s == Signal::User1) {
        // The player's end request: its named event (player.cpp watch_end_event); false while
        // the player has not made it yet, as a SIGUSR1 before its handler would not be taken.
        const std::string name = kEndEventPrefix + std::to_string(GetProcessId(h));
        HANDLE ev = OpenEventA(EVENT_MODIFY_STATE, FALSE, name.c_str());
        if (!ev) return false;
        const BOOL ok = SetEvent(ev);
        CloseHandle(ev);
        return ok != 0;
    }
    // The console-stop helper: this executable, with no console of its own (DETACHED_PROCESS),
    // attaches to the child's and sends Ctrl-C there (platform_entry).
    wchar_t self[32768];
    const DWORD n = GetModuleFileNameW(nullptr, self, DWORD(sizeof self / sizeof self[0]));
    if (!n || n >= sizeof self / sizeof self[0]) return false;
    std::wstring line;
    quote_arg(line, std::wstring(self, n));
    line += L' ';
    line += wide(kStopArg);
    line += L' ' + std::to_wstring(GetProcessId(h));
    STARTUPINFOW si{};
    si.cb = sizeof si;
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(self, &line[0], nullptr, nullptr, FALSE, DETACHED_PROCESS, nullptr, nullptr, &si, &pi))
        return false;
    CloseHandle(pi.hThread);
    DWORD code = 1;
    if (WaitForSingleObject(pi.hProcess, 10000) == WAIT_OBJECT_0) GetExitCodeProcess(pi.hProcess, &code);
    else TerminateProcess(pi.hProcess, 1);
    CloseHandle(pi.hProcess);
    return code == 0;
}

bool platform_entry(const std::vector<std::string>& args, int* code) {
    if (args.size() != 3 || args[1] != kStopArg) return false;
    const DWORD pid = DWORD(std::strtoul(args[2].c_str(), nullptr, 10));
    *code = 1;
    if (!pid) return true;
    FreeConsole();                                   // none, as started; nothing to leave
    if (!AttachConsole(pid)) return true;
    SetConsoleCtrlHandler(nullptr, TRUE);             // the helper ignores its own Ctrl-C
    *code = GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) ? 0 : 2;
    FreeConsole();
    return true;
}

std::string release_own_console() {
    if (!GetConsoleWindow()) return "none: no console";
    // Output a runner reads (a pipe, a file) is no console's: kept, so nothing it captures is lost.
    DWORD mode = 0;
    if (!GetConsoleMode(GetStdHandle(STD_OUTPUT_HANDLE), &mode) || !GetConsoleMode(GetStdHandle(STD_ERROR_HANDLE), &mode))
        return "kept: the output is redirected";
    DWORD ids[2] = {};
    const DWORD n = GetConsoleProcessList(ids, 2);
    if (n != 1)
        return "kept: " + std::to_string(n ? n - 1 : 0) + " other process(es) share it (a shell, or the program that "
               "started this one)";
    if (!FreeConsole()) return win_error("kept: FreeConsole");
    return "released: the console was made for this launch alone (a double-click); the session's launcher.log keeps "
           "what the launcher says";
}

bool error_output_captured() {
    // A console's (FILE_TYPE_CHAR), or none (a console given up, or never had): no program reads it.
    const DWORD t = GetFileType(GetStdHandle(STD_ERROR_HANDLE));
    return t == FILE_TYPE_PIPE || t == FILE_TYPE_DISK;
}

std::vector<std::pair<std::string, std::string>> environment() {
    std::vector<std::pair<std::string, std::string>> out;
    LPWCH block = GetEnvironmentStringsW();
    if (!block) return out;
    for (LPWCH e = block; *e; e += wcslen(e) + 1) {
        const std::string s = narrow(e, wcslen(e));
        size_t eq = s.find('=', 1);                   // "=C:=C:\..." keeps its leading '='
        if (eq != std::string::npos) out.emplace_back(s.substr(0, eq), s.substr(eq + 1));
    }
    FreeEnvironmentStringsW(block);
    return out;
}

void ignore_broken_pipes() {}   // a write to a closed pipe fails with an error; there is no signal
void catch_interrupts() { SetConsoleCtrlHandler(on_console, TRUE); }
int interrupts() { return int(g_interrupts); }
void sleep_ms(int ms) { Sleep(DWORD(ms)); }
uint32_t process_id() { return uint32_t(GetCurrentProcessId()); }

std::string self_dir() {
    wchar_t buf[32768];
    DWORD n = GetModuleFileNameW(nullptr, buf, DWORD(sizeof buf / sizeof buf[0]));
    if (!n) return "";
    std::string s = narrow(buf, n);
    for (char& c : s)
        if (c == '\\') c = '/';
    size_t slash = s.rfind('/');
    return slash == std::string::npos ? "" : s.substr(0, slash);
}

}  // namespace sa2l
