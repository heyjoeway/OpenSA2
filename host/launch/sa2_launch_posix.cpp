// sa2_launch_posix.cpp: sa2_launch_platform.h on Linux (and any POSIX host).
#include "sa2_launch_platform.h"

#include <cerrno>
#include <csignal>
#include <cstring>
#include <ctime>

#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

extern char** environ;

namespace sa2l {

namespace {
std::string err(const std::string& what) { return what + ": " + std::strerror(errno); }
volatile sig_atomic_t g_interrupts = 0;
void on_interrupt(int) { g_interrupts = g_interrupts + 1; }
}  // namespace

bool sessions_supported(std::string*) { return true; }
bool play_supported(std::string*) { return true; }
std::string fifo_path(const std::string& session, const std::string& name) { return session + "/" + name; }
bool player_reads_stream_by_path() { return false; }
bool player_serves_keys() { return false; }

Fd open_pipe_client(const std::string& path, bool* absent, std::string* error) {
    int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) {
        *absent = errno == ENOENT;
        if (!*absent && error) *error = err("open " + path);
    }
    return fd;
}

long read_wait(Fd fd, void* buf, size_t n, int timeout_ms) {
    const int r = wait_readable(fd, timeout_ms);
    if (r < 0) return -2;
    if (r == 0) return -1;
    return read_some(fd, buf, n);
}
bool platform_entry(const std::vector<std::string>&, int*) { return false; }
std::string release_own_console() { return ""; }
bool error_output_captured() {
    struct stat sb;
    return fstat(2, &sb) == 0 && (S_ISFIFO(sb.st_mode) || S_ISREG(sb.st_mode));
}

bool make_fifo(const std::string& path, std::string* error) {
    struct stat sb;
    if (lstat(path.c_str(), &sb) == 0) {
        if (error) *error = path + " exists already; a session's FIFOs are made fresh";
        return false;
    }
    if (mkfifo(path.c_str(), 0600) != 0) {
        if (error) *error = err("mkfifo " + path);
        return false;
    }
    return true;
}

// The stream's pipes hold 1 MB, Linux's unprivileged most (/proc/sys/fs/pipe-max-size): with the
// 64 KB default the game waited on a full pipe about 5 ms a tick behind a slow reader (unit
// perf-game, 2026-10-12: 21.3 ms against 15.9 ms per Metal Harbor tick). A refusal keeps the
// default. A paced relay sizes both of its pipes again while it runs (sa2_relay.cpp, kPlayerPipe
// and kFifoHeld), since the frames that wait between the game and the player depend on them.
static void widen_pipe(int fd) {
#ifdef F_SETPIPE_SZ
    if (fd >= 0) (void)fcntl(fd, F_SETPIPE_SZ, 1 << 20);
#else
    (void)fd;
#endif
}

Fd open_fifo_reader(const std::string& path, std::string* error) {
    int fd = open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0 && error) *error = err("open " + path);
    widen_pipe(fd);
    return fd;
}

Fd open_fifo_holder(const std::string& path, std::string* error) {
    int fd = open(path.c_str(), O_RDWR | O_CLOEXEC);
    if (fd < 0 && error) *error = err("open " + path);
    return fd;
}

bool make_pipe(Fd& r, Fd& w, std::string* error) {
    int p[2];
    if (pipe2(p, O_CLOEXEC) != 0) {
        if (error) *error = err("pipe");
        return false;
    }
    r = p[0];
    w = p[1];
    widen_pipe(p[1]);
    return true;
}

void close_fd(Fd& fd) {
    if (fd >= 0) close(int(fd));
    fd = kNoFd;
}

void set_pipe_size(Fd fd, size_t bytes) {
#ifdef F_SETPIPE_SZ
    (void)fcntl(int(fd), F_SETPIPE_SZ, int(bytes));
#else
    (void)fd;
    (void)bytes;
#endif
}

long read_some(Fd fd, void* buf, size_t n) {
    for (;;) {
        ssize_t got = read(int(fd), buf, n);
        if (got >= 0) return long(got);
        if (errno == EINTR) continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK) return -1;
        return -2;
    }
}

bool write_all(Fd fd, const void* buf, size_t n) {
    const char* p = static_cast<const char*>(buf);
    while (n) {
        ssize_t put = write(int(fd), p, n);
        if (put < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN) {
                struct pollfd q = {int(fd), POLLOUT, 0};
                (void)poll(&q, 1, 100);
                continue;
            }
            return false;
        }
        p += put;
        n -= size_t(put);
    }
    return true;
}

int wait_readable(Fd fd, int timeout_ms) {
    struct pollfd q = {int(fd), POLLIN, 0};
    for (;;) {
        int r = poll(&q, 1, timeout_ms);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0) return -1;
        return r > 0 ? 1 : 0;
    }
}

bool hung_up(Fd fd) {
    struct pollfd q = {int(fd), POLLIN, 0};
    return poll(&q, 1, 0) > 0 && (q.revents & POLLHUP);
}

bool spawn(const Spawn& s, Proc& p, std::string* error) {
    std::vector<std::string> envs;
    for (const auto& kv : s.env) envs.push_back(kv.first + "=" + kv.second);
    std::vector<char*> argv, envp;
    for (const auto& a : s.argv) argv.push_back(const_cast<char*>(a.c_str()));
    argv.push_back(nullptr);
    for (auto& e : envs) envp.push_back(const_cast<char*>(e.c_str()));
    envp.push_back(nullptr);
    int in = open(s.stdin_path.empty() ? "/dev/null" : s.stdin_path.c_str(), O_RDONLY | O_CLOEXEC);
    int out = open(s.stdout_path.empty() ? "/dev/null" : s.stdout_path.c_str(),
                   O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    int er = open(s.stderr_path.empty() ? "/dev/null" : s.stderr_path.c_str(),
                  O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (in < 0 || out < 0 || er < 0) {
        if (error) *error = err("open the child's standard streams");
        if (in >= 0) close(in);
        if (out >= 0) close(out);
        if (er >= 0) close(er);
        return false;
    }
    pid_t pid = fork();
    if (pid < 0) {
        if (error) *error = err("fork");
        close(in); close(out); close(er);
        return false;
    }
    if (pid == 0) {
        if (s.own_group) setpgid(0, 0);
        signal(SIGPIPE, SIG_DFL);
        signal(SIGINT, SIG_DFL);
        signal(SIGTERM, SIG_DFL);
        sigset_t none;
        sigemptyset(&none);
        sigprocmask(SIG_SETMASK, &none, nullptr);
        // Move every inherited descriptor above the numbers it is to take, then into place:
        // a source that is some other entry's target is never overwritten first.
        std::vector<int> moved;
        for (const auto& f : s.fds) moved.push_back(fcntl(int(f.first), F_DUPFD_CLOEXEC, 100));
        dup2(in, 0);
        dup2(out, 1);
        dup2(er, 2);
        for (size_t i = 0; i < s.fds.size(); i++) {
            if (moved[i] < 0 || dup2(moved[i], s.fds[i].second) < 0) _exit(126);
        }
        if (!s.cwd.empty() && chdir(s.cwd.c_str()) != 0) _exit(126);
        execve(s.exe.c_str(), argv.data(), envp.data());
        _exit(127);
    }
    close(in); close(out); close(er);
    if (s.own_group) setpgid(pid, pid);
    p.id = pid;
    p.running = true;
    p.status = 0;
    p.signalled = false;
    return true;
}

bool wait_exit(Proc& p, double timeout_s) {
    if (!p.running) return true;
    struct timespec t0;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (;;) {
        int st = 0;
        pid_t r = waitpid(pid_t(p.id), &st, WNOHANG);
        if (r == pid_t(p.id)) {
            p.running = false;
            p.signalled = WIFSIGNALED(st);
            p.status = WIFEXITED(st) ? WEXITSTATUS(st) : WIFSIGNALED(st) ? -WTERMSIG(st) : -1;
            return true;
        }
        if (r < 0 && errno != EINTR) {
            p.running = false;
            p.status = -1;
            return true;
        }
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        double gone = double(t.tv_sec - t0.tv_sec) + double(t.tv_nsec - t0.tv_nsec) / 1e9;
        if (gone >= timeout_s) return false;
        usleep(20000);
    }
}

bool send_signal(const Proc& p, Signal s, bool group) {
    if (!p.running || p.id <= 0) return false;
    int sig = s == Signal::Terminate ? SIGTERM : s == Signal::User1 ? SIGUSR1 : SIGKILL;
    return kill(group ? -pid_t(p.id) : pid_t(p.id), sig) == 0;
}

std::vector<std::pair<std::string, std::string>> environment() {
    std::vector<std::pair<std::string, std::string>> out;
    for (char** e = environ; e && *e; e++) {
        const char* eq = std::strchr(*e, '=');
        if (!eq) continue;
        out.emplace_back(std::string(*e, size_t(eq - *e)), std::string(eq + 1));
    }
    return out;
}

void ignore_broken_pipes() { signal(SIGPIPE, SIG_IGN); }

void catch_interrupts() {
    struct sigaction sa;
    std::memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_interrupt;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, nullptr);
    sigaction(SIGTERM, &sa, nullptr);
}

int interrupts() { return int(g_interrupts); }

void sleep_ms(int ms) { usleep(useconds_t(ms) * 1000u); }

uint32_t process_id() { return uint32_t(getpid()); }

std::string self_dir() {
    char buf[PATH_MAX];
    ssize_t n = readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n <= 0) return "";
    buf[n] = 0;
    std::string p(buf);
    size_t slash = p.rfind('/');
    return slash == std::string::npos ? "" : p.substr(0, slash);
}

}  // namespace sa2l
