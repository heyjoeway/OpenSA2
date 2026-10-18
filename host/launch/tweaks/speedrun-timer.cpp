// speedrun-timer.cpp: the speedrun-timer host tweak, the launcher's read-only watch over the game's
// timer (speedrun-timer.h has what it reads, what it sends, and the run's rules).
#include "speedrun-timer.h"

#include "tweaks.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>
#else
#include <arpa/inet.h>
#include <cerrno>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace sa2st {

namespace {

constexpr uint32_t kGuestSize = 0x20000000u;      // runtime/guest_memory.h SA2_GUEST_END
constexpr uint32_t kImage = 0x00400000u;          // the PE's ImageBase and its headers' VA
constexpr uint32_t kBlockA = 0x0174AFDAu, kSizeA = 0x6A;   // TimerStopped .. GameMode
constexpr uint32_t kBlockB = 0x01934B70u, kSizeB = 0x72;   // CurrentLevel .. GameState
constexpr uint16_t kUdpPort = 16838, kLiveSplitPort = 16834;
constexpr int kPollMs = 2, kFindMs = 50, kConnectEveryMs = 2000, kAskEveryMs = 250;

// The report this tweak's lines begin with, from its line in the list.
const char* report() {
#define SA2ST_REPORT(bit, name, report, summary, home) \
    if (std::strcmp(#bit, "SA2_TWEAK_SPEEDRUN_TIMER") == 0) return report;
    SA2_TWEAK_HOST_LIST(SA2ST_REPORT)
#undef SA2ST_REPORT
    return "speedrun-timer";
}

// The launcher's record (Watch's `said`), set before the watch's thread starts; one Watch at a time.
Watch::Sink g_said = nullptr;

// One line, to stderr and to the launcher's record (launcher.log), so a launch with no terminal
// (a Windows double-click) keeps it too.
void say(const std::string& s) {
    const std::string line = std::string("sa2 host tweaks: ") + report() + ": " + s;
    std::fprintf(stderr, "sa2recomp: %s\n", line.c_str());
    std::fflush(stderr);
    if (g_said) g_said(line);
}

uint16_t le16(const uint8_t* p) { return uint16_t(p[0] | p[1] << 8); }
uint32_t le32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
void put16(uint8_t* p, uint32_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); }
void put32(uint8_t* p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = uint8_t(v >> (8 * i)); }

// ---------------------------------------------------------------- the game's memory, read-only

class Guest {
public:
    explicit Guest(intptr_t game) : game_(game) {}
    ~Guest() {
#ifndef _WIN32
        if (mem_ >= 0) close(mem_);
#endif
    }
    // Opens the game's memory for reading; false with *why when the host refuses it. On Linux
    // find() opens it again at each try: a launcher's fork returns before the child's exec, and a
    // descriptor opened then holds the forked copy's memory, which exec replaces (measured: a
    // launcher's play found nothing until this). Once the guest is found, that descriptor holds
    // the game's own memory to the end, so a pid used again after the game is reaped is never read.
    bool open(std::string* why) {
#ifdef _WIN32
        (void)why;
        return true;   // CreateProcess's handle has PROCESS_VM_READ
#else
        const std::string path = "/proc/" + std::to_string(game_) + "/mem";
        mem_ = ::open(path.c_str(), O_RDONLY | O_CLOEXEC);
        if (mem_ < 0) {
            *why = path + ": " + std::strerror(errno) +
                   (errno == EACCES || errno == EPERM ? " (kernel.yama.ptrace_scope above 1 forbids even a parent)" : "");
            return false;
        }
        return true;
#endif
    }
    // Looks for the guest mapping once; true when found (base() is then its host address).
    bool find() {
#ifdef _WIN32
        HANDLE h = reinterpret_cast<HANDLE>(game_);
        MEMORY_BASIC_INFORMATION mi;
        uintptr_t at = 0;
        while (VirtualQueryEx(h, reinterpret_cast<void*>(at), &mi, sizeof mi) == sizeof mi) {
            const uintptr_t next = uintptr_t(mi.BaseAddress) + mi.RegionSize;
            MEMORY_BASIC_INFORMATION last;
            if (mi.State != MEM_FREE && mi.BaseAddress == mi.AllocationBase &&
                VirtualQueryEx(h, static_cast<char*>(mi.BaseAddress) + kGuestSize - 1, &last, sizeof last) == sizeof last &&
                last.AllocationBase == mi.AllocationBase && is_guest(uintptr_t(mi.BaseAddress)))
                return true;
            if (next <= at) break;
            at = next;
        }
        return false;
#else
        const std::string mem = "/proc/" + std::to_string(game_) + "/mem";
        const int fresh = ::open(mem.c_str(), O_RDONLY | O_CLOEXEC);
        if (fresh < 0) return false;
        if (mem_ >= 0) close(mem_);
        mem_ = fresh;
        const std::string path = "/proc/" + std::to_string(game_) + "/maps";
        FILE* f = std::fopen(path.c_str(), "r");
        if (!f) return false;
        char line[512];
        bool found = false;
        while (!found && std::fgets(line, sizeof line, f)) {
            unsigned long long lo = 0, hi = 0;
            char perms[8] = "";
            if (std::sscanf(line, "%llx-%llx %7s", &lo, &hi, perms) != 3) continue;
            if (hi - lo < kGuestSize || perms[0] != 'r') continue;
            found = is_guest(uintptr_t(lo)) || is_guest(uintptr_t(hi - kGuestSize));
        }
        std::fclose(f);
        return found;
#endif
    }
    uintptr_t base() const { return base_; }
    // Guest bytes [va, va + n); false when the game's memory can no longer be read.
    bool read(uint32_t va, void* buf, size_t n) const { return read_at(base_ + va, buf, n); }

private:
    bool read_at(uintptr_t at, void* buf, size_t n) const {
#ifdef _WIN32
        SIZE_T got = 0;
        return ReadProcessMemory(reinterpret_cast<HANDLE>(game_), reinterpret_cast<const void*>(at), buf, n, &got) &&
               got == n;
#else
        return pread(mem_, buf, n, off_t(at)) == ssize_t(n);
#endif
    }
    // Whether the image's PE headers are at `base` + 0x00400000.
    bool is_guest(uintptr_t base) {
        uint8_t mz[0x40], pe[0x40];
        if (!read_at(base + kImage, mz, sizeof mz) || mz[0] != 'M' || mz[1] != 'Z') return false;
        const uint32_t lfanew = le32(mz + 0x3C);
        if (lfanew > 0x1000 || !read_at(base + kImage + lfanew, pe, sizeof pe)) return false;
        if (std::memcmp(pe, "PE\0\0", 4) != 0 || le16(pe + 24) != 0x10B || le32(pe + 24 + 28) != kImage) return false;
        base_ = base;
        return true;
    }
    intptr_t game_;
    uintptr_t base_ = 0;
#ifndef _WIN32
    int mem_ = -1;
#endif
};

// ---------------------------------------------------------------- sockets (localhost only)

#ifdef _WIN32
// Winsock, loaded at run time as the launcher loads SDL3 (its link takes no ws2_32).
struct Ws {
    using Sock = SOCKET;
    static constexpr Sock kNone = INVALID_SOCKET;
    int(WSAAPI* startup)(WORD, LPWSADATA) = nullptr;
    Sock(WSAAPI* socket_)(int, int, int) = nullptr;
    int(WSAAPI* connect_)(Sock, const sockaddr*, int) = nullptr;
    int(WSAAPI* send_)(Sock, const char*, int, int) = nullptr;
    int(WSAAPI* recv_)(Sock, char*, int, int) = nullptr;
    int(WSAAPI* sendto_)(Sock, const char*, int, int, const sockaddr*, int) = nullptr;
    int(WSAAPI* close_)(Sock) = nullptr;
    int(WSAAPI* ioctl_)(Sock, long, u_long*) = nullptr;
    int(WSAAPI* select_)(int, fd_set*, fd_set*, fd_set*, const timeval*) = nullptr;
    int(WSAAPI* error_)() = nullptr;
    bool ok = false;
    std::string why;
    Ws() {
        HMODULE m = LoadLibraryW(L"ws2_32.dll");
        if (!m) {
            why = "ws2_32.dll did not load";
            return;
        }
#define SA2ST_SYM(field, name) field = reinterpret_cast<decltype(field)>(reinterpret_cast<void*>(GetProcAddress(m, name)))
        SA2ST_SYM(startup, "WSAStartup");
        SA2ST_SYM(socket_, "socket");
        SA2ST_SYM(connect_, "connect");
        SA2ST_SYM(send_, "send");
        SA2ST_SYM(recv_, "recv");
        SA2ST_SYM(sendto_, "sendto");
        SA2ST_SYM(close_, "closesocket");
        SA2ST_SYM(ioctl_, "ioctlsocket");
        SA2ST_SYM(select_, "select");
        SA2ST_SYM(error_, "WSAGetLastError");
#undef SA2ST_SYM
        WSADATA d;
        if (!startup || !socket_ || !connect_ || !send_ || !recv_ || !sendto_ || !close_ || !ioctl_ || !select_ || !error_)
            why = "ws2_32.dll lacks a function this tweak calls";
        else if (startup(MAKEWORD(2, 2), &d) != 0)
            why = "WSAStartup failed";
        else
            ok = true;
    }
    Sock socket(int type) { return socket_(AF_INET, type, 0); }
    void close(Sock s) { close_(s); }
    bool nonblocking(Sock s) {
        u_long on = 1;
        return ioctl_(s, long(FIONBIO), &on) == 0;
    }
    // 0 under way, 1 connected, -1 failed.
    int connect(Sock s, const sockaddr_in& a) {
        if (connect_(s, reinterpret_cast<const sockaddr*>(&a), sizeof a) == 0) return 1;
        const int e = error_();
        return e == WSAEWOULDBLOCK || e == WSAEINPROGRESS ? 0 : -1;
    }
    int connected(Sock s) {
        fd_set w, x;
        FD_ZERO(&w);
        FD_ZERO(&x);
        FD_SET(s, &w);
        FD_SET(s, &x);
        timeval tv{0, 0};
        if (select_(0, nullptr, &w, &x, &tv) <= 0) return 0;
        // FD_ISSET calls __WSAFDIsSet, a ws2_32 import this link does not take: the set's own array.
        for (u_int i = 0; i < x.fd_count; i++)
            if (x.fd_array[i] == s) return -1;
        return 1;
    }
    bool send(Sock s, const std::string& m) {
        return send_(s, m.data(), int(m.size()), 0) == int(m.size());
    }
    void sendto(Sock s, const uint8_t* b, size_t n, const sockaddr_in& a) {
        sendto_(s, reinterpret_cast<const char*>(b), int(n), 0, reinterpret_cast<const sockaddr*>(&a), sizeof a);
    }
    // Bytes waiting on a non-blocking socket: >0 read, 0 none yet, -1 closed or failed.
    int recv(Sock s, char* b, int n) {
        const int got = recv_(s, b, n, 0);
        if (got > 0) return got;
        return got < 0 && error_() == WSAEWOULDBLOCK ? 0 : -1;
    }
};
#else
struct Ws {
    using Sock = int;
    static constexpr Sock kNone = -1;
    bool ok = true;
    std::string why;
    Sock socket(int type) { return ::socket(AF_INET, type | SOCK_CLOEXEC, 0); }
    void close(Sock s) { ::close(s); }
    bool nonblocking(Sock s) { return fcntl(s, F_SETFL, fcntl(s, F_GETFL) | O_NONBLOCK) == 0; }
    int connect(Sock s, const sockaddr_in& a) {
        if (::connect(s, reinterpret_cast<const sockaddr*>(&a), sizeof a) == 0) return 1;
        return errno == EINPROGRESS ? 0 : -1;
    }
    int connected(Sock s) {
        pollfd p{s, POLLOUT, 0};
        if (poll(&p, 1, 0) <= 0) return 0;
        int err = 0;
        socklen_t n = sizeof err;
        if ((p.revents & (POLLERR | POLLHUP)) || getsockopt(s, SOL_SOCKET, SO_ERROR, &err, &n) != 0 || err) return -1;
        return 1;
    }
    bool send(Sock s, const std::string& m) { return ::send(s, m.data(), m.size(), MSG_NOSIGNAL) == ssize_t(m.size()); }
    void sendto(Sock s, const uint8_t* b, size_t n, const sockaddr_in& a) {
        ::sendto(s, b, n, MSG_NOSIGNAL, reinterpret_cast<const sockaddr*>(&a), sizeof a);
    }
    int recv(Sock s, char* b, int n) {
        const ssize_t got = ::recv(s, b, size_t(n), MSG_DONTWAIT);
        if (got > 0) return int(got);
        return got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) ? 0 : -1;
    }
};
#endif

sockaddr_in localhost(uint16_t port) {
    sockaddr_in a;
    std::memset(&a, 0, sizeof a);
    a.sin_family = AF_INET;
    a.sin_port = uint16_t((port >> 8) | (port << 8));          // htons, with no library call
    const uint8_t loop[4] = {127, 0, 0, 1};
    std::memcpy(&a.sin_addr, loop, 4);
    return a;
}

// LiveSplit Server's client: connects when it can, never waits for it; asks its timer's phase and
// reads the replies without waiting, to see the runner's reset (speedrun-timer.h, "The run's end").
class LiveSplit {
public:
    explicit LiveSplit(Ws& ws) : ws_(ws), at_(localhost(kLiveSplitPort)) {}
    ~LiveSplit() { drop(); }
    void poll(int64_t now_ms) {
        if (state_ == kConnected) {
            if (now_ms - asked_ms_ >= kAskEveryMs) {
                asked_ms_ = now_ms;
                if (!send("getcurrenttimerphase")) return;
                asked_++;
            }
            read();
            return;
        }
        if (state_ == kConnecting) {
            const int c = ws_.connected(s_);
            if (c > 0) {
                state_ = kConnected;
                asked_ms_ = now_ms - kAskEveryMs;
                connections_++;
                say("LiveSplit Server connected at 127.0.0.1:" + std::to_string(kLiveSplitPort));
            } else if (c < 0 || now_ms - tried_ms_ > kConnectEveryMs) {
                drop();
                absent();
            }
            return;
        }
        if (now_ms - tried_ms_ < kConnectEveryMs && tried_ms_ >= 0) return;
        tried_ms_ = now_ms;
        s_ = ws_.socket(SOCK_STREAM);
        if (s_ == Ws::kNone || !ws_.nonblocking(s_)) {
            drop();
            return;
        }
        const int c = ws_.connect(s_, at_);
        if (c < 0) {
            drop();
            absent();
        } else {
            state_ = kConnecting;
            if (c > 0) poll(now_ms);
        }
    }
    // Sends one command when connected; a failed send drops the connection, to be made again.
    void command(const std::string& c) {
        if (state_ == kConnected && send(c)) commands_++;
    }
    // A run begins here: replies to the questions asked before it tell of the time before it.
    void run_begins() { stale_ = asked_; }
    // Whether LiveSplit's timer came to NotRunning since the last call (the runner's reset).
    bool take_reset() {
        const bool r = reset_;
        reset_ = false;
        return r;
    }
    std::string summary() const {
        return connections_ ? "LiveSplit Server: " + std::to_string(commands_) + " commands over " +
                                  std::to_string(connections_) + " connection" + (connections_ == 1 ? "" : "s")
                            : "LiveSplit Server never connected at 127.0.0.1:" + std::to_string(kLiveSplitPort);
    }

private:
    bool send(const std::string& c) {
        if (ws_.send(s_, c + "\r\n")) return true;
        lost();
        return false;
    }
    void lost() {
        say("LiveSplit Server's connection was lost; it is tried again every 2 s");
        drop();
    }
    // The replies waiting, a line each ("\r\n" or "\n"); a line that is no phase is not a reply,
    // and a reply to a question asked before the run began is not taken.
    void read() {
        char b[512];
        for (;;) {
            const int got = ws_.recv(s_, b, int(sizeof b));
            if (got == 0) break;
            if (got < 0) {
                lost();
                return;
            }
            in_.append(b, size_t(got));
        }
        for (size_t nl; (nl = in_.find('\n')) != std::string::npos; in_.erase(0, nl + 1)) {
            std::string line = in_.substr(0, nl);
            if (!line.empty() && line.back() == '\r') line.pop_back();
            if (line != "NotRunning" && line != "Running" && line != "Paused" && line != "Ended") continue;
            if (++replies_ <= stale_) continue;
            if (line == "NotRunning" && phase_ != "NotRunning") reset_ = true;
            phase_ = line;
        }
        if (in_.size() > 4096) in_.clear();   // no line in 4 KiB: not LiveSplit's replies
    }
    void drop() {
        if (s_ != Ws::kNone) ws_.close(s_);
        s_ = Ws::kNone;
        state_ = kIdle;
        in_.clear();
        phase_.clear();
        asked_ = replies_ = stale_ = 0;
    }
    void absent() {
        if (said_absent_) return;
        said_absent_ = true;
        say("LiveSplit Server is not listening at 127.0.0.1:" + std::to_string(kLiveSplitPort) +
            "; it is tried every 2 s, and the datagrams go on without it");
    }
    enum { kIdle, kConnecting, kConnected } state_ = kIdle;
    Ws& ws_;
    sockaddr_in at_;
    Ws::Sock s_ = Ws::kNone;
    int64_t tried_ms_ = -1, asked_ms_ = 0;
    unsigned connections_ = 0, commands_ = 0;
    uint64_t asked_ = 0, replies_ = 0, stale_ = 0;
    std::string in_, phase_;      // unread reply bytes; the last phase taken ("" none yet)
    bool said_absent_ = false, reset_ = false;
};

std::string game_time(uint32_t frames) {
    const uint32_t ms = uint32_t(uint64_t(frames) * 1000 / 60);
    char b[48];
    std::snprintf(b, sizeof b, "%u:%02u:%02u.%03u", ms / 3600000, ms / 60000 % 60, ms / 1000 % 60, ms % 1000);
    return b;
}

// The run (speedrun-timer.h, "Game time" and "The run's end"): what a sample changes, and the
// commands it gives. `stage` and `latch` are the game's last reading, kept across runs.
struct Run {
    bool started = false, cleared = false;
    uint32_t completed = 0, stage = 0, latch = 0, splits = 0, sent_time = UINT32_MAX, runs = 0;
    uint32_t game_time() const { return started ? completed + stage : 0; }
    // A stage start with no run under way: a new run, from 0.
    void begin(LiveSplit& ls) {
        end();
        started = true;
        runs++;
        ls.run_begins();
        ls.command("starttimer");
        ls.command("initgametime");
        ls.command("pausegametime");
    }
    // LiveSplit's reset: no run until the next stage start.
    void end() {
        started = cleared = false;
        completed = splits = 0;
        sent_time = UINT32_MAX;
    }
    void step(uint32_t mode, uint32_t stage_now, uint32_t latch_now, LiveSplit& ls) {
        if (stage_now < stage && cleared) {
            completed += stage;
            cleared = false;
        }
        if (!started && mode == 5 && stage == 0 && stage_now > 0) {
            begin(ls);
            if (runs > 1) say("run " + std::to_string(runs) + " begins at this stage start (game time from 0:00:00.000)");
        }
        const bool goal = started && mode == 5 && latch == 0 && latch_now != 0;
        stage = stage_now;
        latch = latch_now;
        if (started && game_time() != sent_time) {
            sent_time = game_time();
            ls.command("setgametime " + sa2st::game_time(sent_time));
        }
        if (goal) {
            splits++;
            cleared = true;
            ls.command("split");
        }
    }
};

}  // namespace

struct Watch::State {
    intptr_t game;
    std::atomic<bool> stop{false};
    std::thread thread;

    void run() {
        const auto t0 = std::chrono::steady_clock::now();
        auto ms = [&] {
            return int64_t(std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - t0).count());
        };
        say("UDP datagrams to 127.0.0.1:" + std::to_string(kUdpPort) +
            " (64 bytes each, host/launch/tweaks/speedrun-timer.h), and LiveSplit Server at 127.0.0.1:" +
            std::to_string(kLiveSplitPort) + " when it runs; the game's memory is read, never written");
        say("game time is each cleared stage's time plus the running stage's timer; left alone: a stage "
            "left or restarted without its goal adds nothing, and a checkpoint restart keeps the game's own timer");
        say("a run ends only by LiveSplit's reset (never sent from here; seen as its timer's phase coming to "
            "NotRunning), and the next stage start begins a new one; with no LiveSplit Server connected, the "
            "datagram's game time and splits are the game process's first run, to its end");
        Guest guest(game);
        std::string why;
        if (!guest.open(&why)) {
            say("cannot read the game's memory, so nothing is published: " + why);
            return;
        }
        Ws ws;
        if (!ws.ok) {
            say("no sockets, so nothing is published: " + ws.why);
            return;
        }
        const Ws::Sock udp = ws.socket(SOCK_DGRAM);
        if (udp == Ws::kNone) {
            say("no UDP socket could be made, so nothing is published");
            return;
        }
        const sockaddr_in to = localhost(kUdpPort);
        LiveSplit ls(ws);
        Run run;
        uint8_t prev[64] = {}, pkt[64];
        uint32_t seq = 0, retries = 0;
        bool found = false;
        std::string ended = "the launcher ended the watch";
        while (!stop) {
            const int64_t now = ms();
            if (!found) {
                if (!(found = guest.find())) {
                    std::this_thread::sleep_for(std::chrono::milliseconds(kFindMs));
                    continue;
                }
                char at[32];
                std::snprintf(at, sizeof at, "%#llx", (unsigned long long)guest.base());
                say(std::string("the game's guest memory found at ") + at + ", " + std::to_string(now) + " ms after the game started");
            }
            uint8_t a[kSizeA], b[kSizeB], a2[kSizeA];
            bool read = false;
            for (int i = 0; i < 4 && !read; i++) {
                if (!guest.read(kBlockA, a, kSizeA) || !guest.read(kBlockB, b, kSizeB) || !guest.read(kBlockA, a2, kSizeA))
                    break;
                read = std::memcmp(a, a2, kSizeA) == 0;
                if (!read) retries++;
                if (i == 3) read = true;   // four frames in a row half seen: the last reading stands
            }
            if (!read) {
                ended = "the game's memory can no longer be read (the game ended)";
                break;
            }
            auto A = [&](uint32_t va) { return a2 + (va - kBlockA); };
            auto B = [&](uint32_t va) { return b + (va - kBlockB); };
            const uint8_t mins = *A(0x0174AFDB), secs = *A(0x0174AFDC), frames = *A(0x0174AFDD);
            const uint32_t stage = (uint32_t(mins) * 60 + secs) * 60 + frames;
            const uint32_t mode = le32(A(0x0174B040));
            run.step(mode, stage, *A(0x0174B002), ls);
            std::memset(pkt, 0, sizeof pkt);
            std::memcpy(pkt, "SA2T", 4);
            put16(pkt + 4, 1);
            put16(pkt + 6, 64);
            put32(pkt + 12, le32(A(0x0174B038)));
            put32(pkt + 16, le32(A(0x0174B03C)));
            put32(pkt + 20, mode);
            put16(pkt + 24, le16(B(0x01934BE0)));
            put16(pkt + 26, le16(B(0x01934B70)));
            pkt[28] = *B(0x01934B80);
            pkt[29] = mins;
            pkt[30] = secs;
            pkt[31] = frames;
            pkt[32] = *A(0x0174AFDA);
            pkt[33] = *A(0x0174AFF9);
            pkt[34] = *A(0x0174AFF7);
            pkt[35] = *A(0x0174B002);
            put32(pkt + 36, stage);
            put32(pkt + 40, run.game_time());
            put32(pkt + 44, run.splits);
            put32(pkt + 48, run.started ? 1 : 0);
            if (seq == 0 || std::memcmp(pkt + 12, prev + 12, 52 - 12) != 0) {
                put32(pkt + 8, ++seq);
                put32(pkt + 52, uint32_t(now));
                ws.sendto(udp, pkt, sizeof pkt, to);
                std::memcpy(prev, pkt, sizeof pkt);
            }
            ls.poll(now);
            if (ls.take_reset() && run.started) {
                say("LiveSplit's timer was reset after " + std::to_string(run.splits) + " splits at game time " +
                    sa2st::game_time(run.game_time()) + "; the run ended, and the next stage start begins a new one");
                run.end();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(kPollMs));
        }
        ws.close(udp);
        say(std::string(found ? "" : "the game's guest memory was never found (no mapping held sonic2app.exe's "
                                     "headers at guest 0x00400000); ") +
            std::to_string(seq) + " datagrams, " +
            (run.runs > 1 ? std::to_string(run.runs) + " runs; the last: " : std::string()) +
            std::to_string(run.splits) + " splits, game time " +
            sa2st::game_time(run.game_time()) + ", " + std::to_string(retries) + " polls read again; " + ls.summary() +
            "; " + ended);
    }
};

Watch::Watch(intptr_t game, Sink said) {
    if (!sa2tw::on(sa2tw::SA2_TWEAK_SPEEDRUN_TIMER)) return;
    g_said = said;
    s_ = new State;
    s_->game = game;
    s_->thread = std::thread([st = s_] { st->run(); });
}

Watch::~Watch() {
    if (!s_) return;
    s_->stop = true;
    s_->thread.join();
    delete s_;
}

}  // namespace sa2st
