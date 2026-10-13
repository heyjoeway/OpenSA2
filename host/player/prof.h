// prof.h: the player's own CPU sampler, off unless SA2_PLAYER_PROF=<Hz> is set.
//
// The player's render thread (the thread that runs Player::run: the process's main thread on
// Linux, the app's SDL thread on Android) is sampled on its own CPU clock. A POSIX timer
// (timer_create on CLOCK_THREAD_CPUTIME_ID, SIGEV_THREAD_ID to that thread) sends it SIGPROF
// every 1/Hz s of its CPU time, and the handler records the interrupted PC, the link register
// (arm64) or the word at the stack pointer (x86-64: a leaf's return address), the stack and frame
// pointers, a frame-pointer walk of up to kDepth return addresses inside the thread's stack, the
// thread's CPU and monotonic clocks, the Present count and the player's zone (below). The kernel
// checks CPU timers at its tick, so the rate actually reached is at most the tick's (250 Hz on
// most phones' kernels); each sample's CPU clock lets a report weigh it by the CPU time it stands
// for, and si_overrun counts the periods a tick covered.
//
// Files, in SA2_PLAYER_PROF_OUT (made if missing), else $SA2_PLAY_OUT (the Android session's
// <session>/play), else the --report file's directory, else the working directory; refused, as
// every player output is, inside a git working tree outside its private/ (player.cpp,
// refuse_output). Each is named player-prof-<pid>-<tid>.<what>:
//   .samples       a 128-byte head and one 256-byte Sample per sample (layout below), in a
//                  MAP_SHARED file the handler writes, so a session that is killed keeps every
//                  sample taken; cut to the samples taken at stop
//   .frames        a 64-byte head and one 24-byte FrameRow per Present (its count, monotonic
//                  and thread CPU clocks), mapped the same way
//   .modules.json  every loaded module (dl_iterate_phdr): name, load bias, executable range,
//                  segments and GNU build-id, so a report can symbolize each address from an
//                  unstripped copy kept by build-id (tools/s8/player_prof.py); rewritten at the
//                  start, at Presents 1 and 600, every 18000th and at stop
//   .maps          /proc/self/maps at the same moments
// tools/s8/player_prof.py reads them. Zones are what the player's loop is doing (Zone below):
// the sampler's own split of a frame, beside the split by function.
//
// Header-only and included by player.cpp alone: the Linux and Windows builders list the player's
// translation units by name (host/player/build.sh, host/d3d9gpu/build.sh,
// tools/s7/host2_win_build.py), so the sampler adds none. On Windows it is a stub that says so.
#pragma once

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#if defined(__linux__) && (defined(__x86_64__) || defined(__aarch64__))
#define SA2_PROF_POSIX 1
#include <cerrno>
#include <climits>
#include <ctime>
#include <dirent.h>
#include <elf.h>
#include <fcntl.h>
#include <link.h>
#include <pthread.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <ucontext.h>
#include <unistd.h>
#else
#define SA2_PROF_POSIX 0
#endif

namespace sa2prof {

// What the render thread is doing, set by the player's loop (player.cpp): each sample carries it.
enum Zone : uint8_t {
    kZoneOther = 0,     // setup, teardown, anything unmarked
    kZoneStream = 1,    // reading and checking the next record (Sa2StreamReader, check_record)
    kZoneCall = 2,      // an IDirect3D* call record: the player's checks and the d3d9 device
    kZoneD3dx = 3,      // a D3DX record
    kZonePresent = 4,   // a Present: readback, pacing, the device's Present (submit, swapchain, overlay)
    kZoneAudio = 5,     // the mixer: AUDIO records and each frame's block to the device
    kZoneIdle = 6,      // the reader's idle service: SDL's event pump, the pads and the touch overlay
    kZoneCount = 7,
};
inline constexpr const char* kZoneNames[kZoneCount] = {"other", "stream", "call", "d3dx", "present", "audio", "idle"};

constexpr uint32_t kVersion = 1;
constexpr uint32_t kDepth = 25;               // frame-pointer return addresses per sample

// One sample, 256 bytes, little-endian (tools/s8/player_prof.py's SAMPLE).
struct Sample {
    uint64_t pc, lr, sp, fp;                  // lr: x30 on arm64; on x86-64 the word at sp
    uint64_t cpu_ns, mono_ns;                 // the thread's CPU clock, CLOCK_MONOTONIC
    uint32_t frame;                           // Presents so far
    uint8_t zone, depth;                      // Zone; return addresses in ret
    uint16_t overrun;                         // timer periods the tick covered beyond this one
    uint64_t ret[kDepth];                     // the frame-pointer walk, innermost first
};
static_assert(sizeof(Sample) == 256, "the report reads 256-byte samples");

// The .samples head, 128 bytes: magic "SA2PPRF1", then these fields in order.
struct SampleHead {
    char magic[8];
    uint32_t version, head_bytes, record_bytes, hz;
    uint32_t pid, tid, machine, depth;        // machine: EM_X86_64 (62) or EM_AARCH64 (183)
    uint64_t capacity, count, dropped;        // count: samples taken (records written: min(count, capacity))
    uint64_t t0_mono_ns, t0_cpu_ns, t1_mono_ns, t1_cpu_ns;
    uint64_t stack_lo, stack_hi;
    uint32_t complete, frames_at_stop;        // complete: 1 once stop wrote the head
    uint64_t reserved;
};
static_assert(sizeof(SampleHead) == 128, "the report reads a 128-byte head");

struct FrameRow { uint32_t frame, zone; uint64_t mono_ns, cpu_ns; };
static_assert(sizeof(FrameRow) == 24, "the report reads 24-byte frame rows");

// The .frames head, 64 bytes: magic "SA2PFRM1".
struct FrameHead {
    char magic[8];
    uint32_t version, head_bytes, record_bytes, pad;
    uint64_t capacity, count;
    uint64_t reserved[3];
};
static_assert(sizeof(FrameHead) == 64, "the report reads a 64-byte frames head");

// The zone, written by the player and read by the handler on the same thread: a relaxed store.
inline std::atomic<uint8_t> g_zone{kZoneOther};
inline void zone(Zone z) { g_zone.store(z, std::memory_order_relaxed); }

// The zone for a scope, the previous one again after it.
class ZoneScope {
public:
    explicit ZoneScope(Zone z) : prev_(g_zone.load(std::memory_order_relaxed)) { zone(z); }
    ~ZoneScope() { g_zone.store(prev_, std::memory_order_relaxed); }
    ZoneScope(const ZoneScope&) = delete;
    ZoneScope& operator=(const ZoneScope&) = delete;
private:
    uint8_t prev_;
};

#if SA2_PROF_POSIX

namespace detail {

struct State {
    volatile sig_atomic_t on = 0;
    pthread_t thread{};
    int pid = 0, tid = 0;
    uint32_t hz = 0;
    SampleHead* head = nullptr;
    Sample* buf = nullptr;
    size_t cap = 0, map_bytes = 0;
    int fd = -1;
    FrameHead* fhead = nullptr;
    FrameRow* frows = nullptr;
    size_t fcap = 0, fmap_bytes = 0;
    int ffd = -1;
    uintptr_t stack_lo = 0, stack_hi = 0;
    timer_t timer{};
    bool timer_made = false;
    std::atomic<uint32_t> frame{0};
    std::string base;                         // <dir>/player-prof-<pid>-<tid>
    struct sigaction old_action {};
    bool handler_set = false;
};
inline State g;

inline uint64_t clock_ns(clockid_t c) {
    timespec ts{};
    clock_gettime(c, &ts);
    return uint64_t(ts.tv_sec) * 1000000000ull + uint64_t(ts.tv_nsec);
}

// The SIGPROF handler: async-signal-safe (clock_gettime, plain loads within the thread's stack).
inline void on_sigprof(int, siginfo_t* si, void* ctx) {
    if (!g.on || !pthread_equal(pthread_self(), g.thread)) return;
    const int saved = errno;
    const uint64_t i = __atomic_fetch_add(&g.head->count, 1, __ATOMIC_RELAXED);
    if (i >= g.cap) {
        __atomic_fetch_add(&g.head->dropped, 1, __ATOMIC_RELAXED);
        errno = saved;
        return;
    }
    Sample& s = g.buf[i];
    const ucontext_t* uc = static_cast<const ucontext_t*>(ctx);
    uintptr_t pc = 0, lr = 0, sp = 0, fp = 0;
#if defined(__x86_64__)
    pc = uintptr_t(uc->uc_mcontext.gregs[REG_RIP]);
    sp = uintptr_t(uc->uc_mcontext.gregs[REG_RSP]);
    fp = uintptr_t(uc->uc_mcontext.gregs[REG_RBP]);
    if (sp >= g.stack_lo && sp % 8 == 0 && sp + 8 <= g.stack_hi) lr = *reinterpret_cast<const uintptr_t*>(sp);
#else
    pc = uintptr_t(uc->uc_mcontext.pc);
    sp = uintptr_t(uc->uc_mcontext.sp);
    fp = uintptr_t(uc->uc_mcontext.regs[29]);
    lr = uintptr_t(uc->uc_mcontext.regs[30]);
#endif
    s.pc = pc;
    s.lr = lr;
    s.sp = sp;
    s.fp = fp;
    s.cpu_ns = clock_ns(CLOCK_THREAD_CPUTIME_ID);
    s.mono_ns = clock_ns(CLOCK_MONOTONIC);
    s.frame = g.frame.load(std::memory_order_relaxed);
    s.zone = g_zone.load(std::memory_order_relaxed);
    const int over = si ? si->si_overrun : 0;
    s.overrun = uint16_t(over < 0 ? 0 : over > 65535 ? 65535 : over);
    // The frame records (fp: saved fp, fp + 8: return address), each inside this thread's stack
    // above the last; a register that is no frame pointer (code built without them) stops it.
    // The bound is written so it cannot wrap: a register near 2^64 (fp + 16 overflowing to a
    // small number) once passed `fp + 16 <= stack_hi` and read address 0 on the user's phone.
    uint32_t d = 0;
    uintptr_t lo = sp < g.stack_lo ? g.stack_lo : sp;
    while (d < kDepth && fp >= lo && fp % 8 == 0 && fp < g.stack_hi && g.stack_hi - fp >= 16) {
        const uintptr_t* rec = reinterpret_cast<const uintptr_t*>(fp);
        const uintptr_t next = rec[0], ret = rec[1];
        if (!ret) break;
        s.ret[d++] = ret;
        if (next <= fp) break;
        lo = fp + 16;
        fp = next;
    }
    s.depth = uint8_t(d);
    errno = saved;
}

inline bool make_dirs(const std::string& dir) {
    if (dir.empty()) return false;
    std::string at;
    size_t i = 0;
    while (i <= dir.size()) {
        const size_t slash = dir.find('/', i);
        at = dir.substr(0, slash == std::string::npos ? dir.size() : slash);
        if (!at.empty() && mkdir(at.c_str(), 0755) != 0 && errno != EEXIST) return false;
        if (slash == std::string::npos) break;
        i = slash + 1;
    }
    struct stat st{};
    return stat(dir.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

// A file of `bytes` mapped shared, its descriptor kept for the cut at stop.
inline void* map_file(const std::string& path, size_t bytes, int* fd_out, std::string* why) {
    const int fd = open(path.c_str(), O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) { *why = "cannot create " + path + ": " + std::strerror(errno); return nullptr; }
    if (ftruncate(fd, off_t(bytes)) != 0) {
        *why = "cannot size " + path + ": " + std::strerror(errno);
        close(fd);
        return nullptr;
    }
    void* m = mmap(nullptr, bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (m == MAP_FAILED) {
        *why = "cannot map " + path + ": " + std::strerror(errno);
        close(fd);
        return nullptr;
    }
    *fd_out = fd;
    return m;
}

inline std::string json_str(const std::string& s) {
    std::string o = "\"";
    for (const char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (static_cast<unsigned char>(c) < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += c;
    }
    return o + "\"";
}

inline std::string self_exe() {
    char b[PATH_MAX];
    const ssize_t n = readlink("/proc/self/exe", b, sizeof b - 1);
    return n > 0 ? std::string(b, size_t(n)) : std::string();
}

struct ModuleList {
    std::string json;
    bool first = true;
};

// A module's identity when it has no GNU build-id (the NDK links the app's libmain.so without
// one): a hash of its readable executable segments as loaded, which strip leaves as they were,
// so the unstripped copy a report symbolizes from hashes the same (tools/s8/player_prof.py
// text_hash). 64-bit words, FNV-1a's prime, each segment's length mixed in.
inline uint64_t text_hash(uint64_t h, const uint8_t* p, size_t n) {
    h ^= n;
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t w;
        std::memcpy(&w, p + i, 8);
        h = (h ^ w) * 1099511628211ull;
    }
    uint64_t w = 0;
    std::memcpy(&w, p + i, n - i);
    return (h ^ w) * 1099511628211ull;
}

// One module of dl_iterate_phdr as a JSON object. The first, the program itself, has no name on
// glibc: its path is /proc/self/exe's.
inline int module_json(dl_phdr_info* info, size_t, void* list_) {
    ModuleList& list = *static_cast<ModuleList*>(list_);
    std::string& out = list.json;
    std::string name = info->dlpi_name ? info->dlpi_name : "";
    if (name.empty() && list.first) name = self_exe();
    list.first = false;
    uint64_t lo = UINT64_MAX, hi = 0;
    std::string build_id, segs;
    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr)& ph = info->dlpi_phdr[i];
        if (ph.p_type == PT_LOAD) {
            char b[160];
            std::snprintf(b, sizeof b, "%s{\"vaddr\": %llu, \"memsz\": %llu, \"offset\": %llu, \"flags\": %u}",
                          segs.empty() ? "" : ", ", (unsigned long long)ph.p_vaddr, (unsigned long long)ph.p_memsz,
                          (unsigned long long)ph.p_offset, unsigned(ph.p_flags));
            segs += b;
            if (ph.p_flags & PF_X) {
                lo = std::min<uint64_t>(lo, info->dlpi_addr + ph.p_vaddr);
                hi = std::max<uint64_t>(hi, info->dlpi_addr + ph.p_vaddr + ph.p_memsz);
            }
        } else if (ph.p_type == PT_NOTE && build_id.empty()) {
            const uint8_t* p = reinterpret_cast<const uint8_t*>(info->dlpi_addr + ph.p_vaddr);
            const uint8_t* end = p + ph.p_memsz;
            while (p + 12 <= end) {
                uint32_t namesz, descsz, type;
                std::memcpy(&namesz, p, 4);
                std::memcpy(&descsz, p + 4, 4);
                std::memcpy(&type, p + 8, 4);
                const uint8_t* nm = p + 12;
                const uint8_t* desc = nm + ((namesz + 3u) & ~3u);
                if (desc + descsz > end) break;
                if (type == NT_GNU_BUILD_ID && namesz == 4 && std::memcmp(nm, "GNU", 4) == 0) {
                    for (uint32_t k = 0; k < descsz; k++) {
                        char h[3];
                        std::snprintf(h, sizeof h, "%02x", desc[k]);
                        build_id += h;
                    }
                    break;
                }
                p = desc + ((descsz + 3u) & ~3u);
            }
        }
    }
    std::string text_id;
    if (build_id.empty()) {
        static std::string cached_key, cached_id;   // one module lacks an id in practice: libmain.so
        const std::string key = name + "@" + std::to_string(info->dlpi_addr);
        if (key == cached_key) {
            text_id = cached_id;
        } else {
            uint64_t h = 1469598103934665603ull;
            bool any = false;
            for (int i = 0; i < info->dlpi_phnum; i++) {
                const ElfW(Phdr)& ph = info->dlpi_phdr[i];
                if (ph.p_type == PT_LOAD && (ph.p_flags & PF_X) && (ph.p_flags & PF_R)) {
                    h = text_hash(h, reinterpret_cast<const uint8_t*>(info->dlpi_addr + ph.p_vaddr), ph.p_filesz);
                    any = true;
                }
            }
            if (any) {
                char hx[24];
                std::snprintf(hx, sizeof hx, "%016llx", (unsigned long long)h);
                text_id = hx;
            }
            cached_key = key;
            cached_id = text_id;
        }
    }
    char b[256];
    std::snprintf(b, sizeof b, "%s\n  {\"name\": %s, \"bias\": %llu, \"exec_lo\": %llu, \"exec_hi\": %llu, ",
                  out.empty() ? "" : ",", json_str(name).c_str(), (unsigned long long)info->dlpi_addr,
                  (unsigned long long)(lo == UINT64_MAX ? 0 : lo), (unsigned long long)hi);
    out += b;
    out += "\"build_id\": " + json_str(build_id) + ", \"text_hash\": " + json_str(text_id) + ", \"segments\": [" + segs + "]}";
    return 0;
}

inline bool write_whole(const std::string& path, const std::string& text) {
    const std::string tmp = path + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) return false;
    const bool ok = std::fwrite(text.data(), 1, text.size(), f) == text.size();
    if (std::fclose(f) != 0 || !ok) { unlink(tmp.c_str()); return false; }
    return rename(tmp.c_str(), path.c_str()) == 0;
}

// The module map and /proc/self/maps, SIGPROF held off while the loader's list is walked.
inline void write_modules() {
    sigset_t prof, old;
    sigemptyset(&prof);
    sigaddset(&prof, SIGPROF);
    pthread_sigmask(SIG_BLOCK, &prof, &old);
    ModuleList list;
    dl_iterate_phdr(module_json, &list);
    pthread_sigmask(SIG_SETMASK, &old, nullptr);
    const std::string mods = "[" + list.json + "\n]";
    char head[160];
    std::snprintf(head, sizeof head, "{\"schema\": \"sa2-player-prof-modules/v1\", \"pid\": %d, \"tid\": %d, \"frame\": %u, "
                  "\"modules\": ", g.pid, g.tid, g.frame.load());
    write_whole(g.base + ".modules.json", head + mods + "}\n");
    std::string maps;
    if (FILE* f = std::fopen("/proc/self/maps", "rb")) {
        char b[8192];
        size_t n;
        while ((n = std::fread(b, 1, sizeof b, f)) > 0) maps.append(b, n);
        std::fclose(f);
        write_whole(g.base + ".maps", maps);
    }
}

// Undoes start: the timer, the handler, the maps and the descriptors (the files stay).
inline void close_all() {
    g.on = 0;
    if (g.timer_made) { timer_delete(g.timer); g.timer_made = false; }
    if (g.handler_set) { sigaction(SIGPROF, &g.old_action, nullptr); g.handler_set = false; }
    if (g.head) munmap(g.head, g.map_bytes);
    if (g.fhead) munmap(g.fhead, g.fmap_bytes);
    g.head = nullptr; g.buf = nullptr; g.fhead = nullptr; g.frows = nullptr;
    if (g.fd >= 0) close(g.fd);
    if (g.ffd >= 0) close(g.ffd);
    g.fd = g.ffd = -1;
}

}  // namespace detail

inline bool running() { return detail::g.on != 0; }

// Starts sampling the calling thread at `hz` into `dir`; "" or why not (nothing then runs).
inline std::string start(const std::string& dir, uint32_t hz, size_t capacity = 1u << 20) {
    using namespace detail;
    if (g.on || g.head) return "the sampler already runs";
    if (hz < 1 || hz > 20000) return "SA2_PLAYER_PROF must be 1 to 20000 (Hz)";
    if (!capacity) return "no room for a sample";
    if (!make_dirs(dir)) return "cannot make the sampler's directory " + dir;
    g.pid = int(getpid());
    g.tid = int(syscall(SYS_gettid));
    g.thread = pthread_self();
    g.hz = hz;
    g.base = dir + "/player-prof-" + std::to_string(g.pid) + "-" + std::to_string(g.tid);
    g.stack_lo = g.stack_hi = 0;
    pthread_attr_t attr;
    if (pthread_getattr_np(pthread_self(), &attr) == 0) {
        void* addr = nullptr;
        size_t size = 0;
        if (pthread_attr_getstack(&attr, &addr, &size) == 0) {
            g.stack_lo = uintptr_t(addr);
            g.stack_hi = uintptr_t(addr) + size;
        }
        pthread_attr_destroy(&attr);
    }
    if (!g.stack_hi) return "cannot read the render thread's stack bounds";
    std::string why;
    g.cap = capacity;
    g.map_bytes = sizeof(SampleHead) + g.cap * sizeof(Sample);
    void* m = map_file(g.base + ".samples", g.map_bytes, &g.fd, &why);
    if (!m) return why;
    g.fcap = 1u << 20;
    g.fmap_bytes = sizeof(FrameHead) + g.fcap * sizeof(FrameRow);
    void* fm = map_file(g.base + ".frames", g.fmap_bytes, &g.ffd, &why);
    if (!fm) {
        munmap(m, g.map_bytes);
        close(g.fd);
        g.fd = -1;
        return why;
    }
    g.head = static_cast<SampleHead*>(m);
    g.buf = reinterpret_cast<Sample*>(static_cast<char*>(m) + sizeof(SampleHead));
    g.fhead = static_cast<FrameHead*>(fm);
    g.frows = reinterpret_cast<FrameRow*>(static_cast<char*>(fm) + sizeof(FrameHead));
    SampleHead& h = *g.head;
    std::memcpy(h.magic, "SA2PPRF1", 8);
    h.version = kVersion;
    h.head_bytes = sizeof(SampleHead);
    h.record_bytes = sizeof(Sample);
    h.hz = hz;
    h.pid = uint32_t(g.pid);
    h.tid = uint32_t(g.tid);
#if defined(__x86_64__)
    h.machine = EM_X86_64;
#else
    h.machine = EM_AARCH64;
#endif
    h.depth = kDepth;
    h.capacity = g.cap;
    h.stack_lo = g.stack_lo;
    h.stack_hi = g.stack_hi;
    h.t0_mono_ns = clock_ns(CLOCK_MONOTONIC);
    h.t0_cpu_ns = clock_ns(CLOCK_THREAD_CPUTIME_ID);
    FrameHead& fh = *g.fhead;
    std::memcpy(fh.magic, "SA2PFRM1", 8);
    fh.version = kVersion;
    fh.head_bytes = sizeof(FrameHead);
    fh.record_bytes = sizeof(FrameRow);
    fh.capacity = g.fcap;
    write_modules();
    struct sigaction sa {};
    sa.sa_sigaction = on_sigprof;
    sa.sa_flags = SA_SIGINFO | SA_RESTART;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGPROF, &sa, &g.old_action) == 0) g.handler_set = true;
    else why = std::string("sigaction: ") + std::strerror(errno);
    if (why.empty()) {
        sigset_t prof;
        sigemptyset(&prof);
        sigaddset(&prof, SIGPROF);
        pthread_sigmask(SIG_UNBLOCK, &prof, nullptr);
        struct sigevent se {};
        se.sigev_notify = SIGEV_THREAD_ID;
        se.sigev_signo = SIGPROF;
#ifdef sigev_notify_thread_id
        se.sigev_notify_thread_id = g.tid;
#else
        se._sigev_un._tid = g.tid;
#endif
        if (timer_create(CLOCK_THREAD_CPUTIME_ID, &se, &g.timer) == 0) g.timer_made = true;
        else why = std::string("timer_create: ") + std::strerror(errno);
    }
    if (why.empty()) {
        g.on = 1;
        const long period = long(1000000000ull / hz);
        itimerspec its{};
        its.it_interval.tv_sec = period / 1000000000L;
        its.it_interval.tv_nsec = period % 1000000000L;
        its.it_value = its.it_interval;
        if (timer_settime(g.timer, 0, &its, nullptr) != 0) why = std::string("timer_settime: ") + std::strerror(errno);
    }
    if (!why.empty()) close_all();
    return why;
}

// At each Present: its row, and the module map now and then (the device's driver loads late).
inline void frame(uint32_t n) {
    using namespace detail;
    g.frame.store(n, std::memory_order_relaxed);
    if (!g.on) return;
    const uint64_t i = g.fhead->count;
    if (i < g.fcap) {
        FrameRow& r = g.frows[i];
        r.frame = n;
        r.zone = g_zone.load(std::memory_order_relaxed);
        r.mono_ns = clock_ns(CLOCK_MONOTONIC);
        r.cpu_ns = clock_ns(CLOCK_THREAD_CPUTIME_ID);
        g.fhead->count = i + 1;
    }
    if (n == 1 || n == 600 || n % 18000 == 0) write_modules();
}

// Stops the sampler and closes its files; a line for the player's log ("" when it was off).
inline std::string stop() {
    using namespace detail;
    if (!g.on) return "";
    g.on = 0;
    if (g.timer_made) { timer_delete(g.timer); g.timer_made = false; }
    SampleHead& h = *g.head;
    h.t1_mono_ns = clock_ns(CLOCK_MONOTONIC);
    h.t1_cpu_ns = clock_ns(CLOCK_THREAD_CPUTIME_ID);
    h.frames_at_stop = g.frame.load();
    h.complete = 1;
    const uint64_t taken = h.count, kept = taken < g.cap ? taken : g.cap, dropped = h.dropped;
    const uint64_t frames = std::min<uint64_t>(g.fhead->count, g.fcap);
    const double cpu_s = double(h.t1_cpu_ns - h.t0_cpu_ns) / 1e9;
    write_modules();
    const bool cut = ftruncate(g.fd, off_t(sizeof(SampleHead) + kept * sizeof(Sample))) == 0 &&
                     ftruncate(g.ffd, off_t(sizeof(FrameHead) + frames * sizeof(FrameRow))) == 0;
    close_all();
    char b[640];
    std::snprintf(b, sizeof b, "player-prof: %llu samples (%llu dropped) at %u Hz asked, %.1f Hz reached, over %.3f s of the "
                  "render thread's CPU and %llu Presents%s: %s.samples",
                  (unsigned long long)kept, (unsigned long long)dropped, g.hz, cpu_s > 0 ? double(taken) / cpu_s : 0.0,
                  cpu_s, (unsigned long long)frames, cut ? "" : " (the files were not cut to size)", g.base.c_str());
    return b;
}

#else  // no sampler on this platform

inline bool running() { return false; }
inline std::string start(const std::string&, uint32_t, size_t = 0) { return "the sampler is Linux and Android only"; }
inline void frame(uint32_t) {}
inline std::string stop() { return ""; }

#endif

// SA2_PLAYER_PROF=<Hz>: started on the calling thread into the directory the head comment names,
// held to `refuse` (the player's output rule: "" when allowed). Returns a line for the log, or ""
// when the variable is unset.
template <class Refuse>
inline std::string start_from_env(const std::string& report_path, Refuse refuse) {
    const char* hz_text = std::getenv("SA2_PLAYER_PROF");
    if (!hz_text || !*hz_text || std::strcmp(hz_text, "0") == 0) return "";
    char* end = nullptr;
    const unsigned long hz = std::strtoul(hz_text, &end, 10);
    if (!end || *end || hz < 1 || hz > 20000) return std::string("player-prof: SA2_PLAYER_PROF=") + hz_text + " is not 1 to 20000 Hz; off";
    std::string dir;
    if (const char* d = std::getenv("SA2_PLAYER_PROF_OUT"); d && *d) dir = d;
    else if (const char* p = std::getenv("SA2_PLAY_OUT"); p && *p) dir = p;
    else if (!report_path.empty()) {
        const size_t slash = report_path.rfind('/');
        dir = slash == std::string::npos ? "." : slash == 0 ? "/" : report_path.substr(0, slash);
    } else dir = ".";
    if (const std::string why = refuse(dir + "/player-prof.samples"); !why.empty()) return "player-prof: refused: " + why;
    const std::string why = start(dir, uint32_t(hz));
    return why.empty() ? "player-prof: sampling the render thread at " + std::to_string(hz) + " Hz into " + dir
                       : "player-prof: off: " + why;
}

}  // namespace sa2prof
