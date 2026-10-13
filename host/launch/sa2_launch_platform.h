// sa2_launch_platform.h: the operating-system calls the launcher makes beyond host/install's
// sa2_platform.h: processes, pipes, FIFOs and signals. sa2_launch_posix.cpp implements it for
// Linux; sa2_launch_win32.cpp for Windows (zig c++ -target x86_64-windows-gnu), where a FIFO is
// a named pipe whose server end the launcher holds, and SIGTERM a console Ctrl-C to the child's
// own console (runtime/sa2_win32.h, the runtime's side of both).
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace sa2l {

using Fd = intptr_t;                 // a file descriptor (POSIX) or a HANDLE (Win32)
constexpr Fd kNoFd = -1;

// The suffix of an executable's file name on this host.
#ifdef _WIN32
constexpr const char* kExeSuffix = ".exe";
#else
constexpr const char* kExeSuffix = "";
#endif

struct Proc {
    intptr_t id = -1;                // pid, or the process HANDLE
    bool running = false;
    int status = 0;                  // exit code, or -signal
    bool signalled = false;
};

struct Spawn {
    std::string exe;
    std::vector<std::string> argv;                        // argv[0] included
    std::vector<std::pair<std::string, std::string>> env; // the child's whole environment
    std::string cwd;
    std::vector<std::pair<Fd, int>> fds;                  // a descriptor, and its number in the child
    std::string stdin_path = "", stdout_path = "", stderr_path = "";  // "" is the null device
    bool own_group = false;                               // a process group of its own
};

// Whether the platform layer can run a session at all, and why not.
bool sessions_supported(std::string* why);
// Whether it can run play (a person's session, the player's window taking the keys), and why
// not. The selftest needs only sessions_supported.
bool play_supported(std::string* why);

// The path of the session's FIFO `name`: <session>/<name> on POSIX. Windows has no FIFO: there
// it is a named pipe, \\.\pipe\sa2recomp-<pid>-<n>-<name>, whose server end open_fifo_reader or
// open_fifo_holder makes (make_fifo only checks the name).
std::string fifo_path(const std::string& session, const std::string& name);
// Whether the player's channels are named pipes the launcher serves, given by name, rather than
// descriptors it inherits (--video-fd, --cancel-fd, --end-fd): Windows. The selftest's player
// reads the stream by path (a named pipe served through open_fifo_holder); play's runs its
// interactive mode on --video-pipe and --cancel-pipe (open_fifo_holder) and --end-pipe
// (open_fifo_reader), host/player/player.cpp.
bool player_reads_stream_by_path();
// Whether the player's key sender is the server of the pipe it sends on (Windows: a named pipe it
// creates, host/player/input.cpp KeySender::start), which the launcher then reads as its client
// and hands on to the game's keyboard pipe (sa2_launch.cpp KeyForward); false where the player
// opens the game's input FIFO and writes it itself (POSIX).
bool player_serves_keys();
// A reading client's end of the pipe `path` another process serves. kNoFd with *absent true while
// there is none to open yet (no such pipe, or its one instance taken); with *error set otherwise.
Fd open_pipe_client(const std::string& path, bool* absent, std::string* error);
// Reads what `fd` (from open_pipe_client) holds, waiting at most timeout_ms for its first byte:
// >0 bytes read; 0 end of file (the server closed it); -1 nothing yet; -2 an error.
long read_wait(Fd fd, void* buf, size_t n, int timeout_ms);
// The platform's own command lines, read before main's: on Windows the launcher re-run as its
// console-stop helper (send_signal). True when it handled `args`, with the exit code in *code.
bool platform_entry(const std::vector<std::string>& args, int* code);
// Gives up a console made for this process alone, so its window does not sit over the game's:
// Windows, where Explorer makes one for a console program it starts (a double-click), which is the
// only console that has this process alone attached and the launcher's own output on it. A console
// a shell shares, or output redirected to a file or pipe (a runner capturing it), is kept. What
// was done, in words, for the record ("released: ..." when it was given up, "kept: ..." or
// "none: ..." when not); "" where the host has no consoles (POSIX).
std::string release_own_console();
// Whether this process's error stream is read by a program: a pipe or a file (a runner, a test,
// a log redirect), rather than a terminal, a console, a socket (a desktop session's journal) or
// nowhere. Play shows an error in a box only when it is not (sa2_launch.cpp main): a box would
// wait for a person a runner does not have.
bool error_output_captured();

bool make_fifo(const std::string& path, std::string* error);
// The read end, O_NONBLOCK, so it opens before any writer and a writer's open never waits.
Fd open_fifo_reader(const std::string& path, std::string* error);
// O_RDWR: held by the launcher so the game never reads end of file before the player's first
// write, and a writer's open never fails for want of a reader.
Fd open_fifo_holder(const std::string& path, std::string* error);
bool make_pipe(Fd& read_end, Fd& write_end, std::string* error);
void close_fd(Fd& fd);
void set_pipe_size(Fd fd, size_t bytes);

// >0 bytes read; 0 end of file; -1 nothing now (would block); -2 an error.
long read_some(Fd fd, void* buf, size_t n);
// False on an error (EPIPE when the reader is gone included).
bool write_all(Fd fd, const void* buf, size_t n);
// 1 readable (or hung up), 0 timed out, -1 error.
int wait_readable(Fd fd, int timeout_ms);
bool hung_up(Fd fd);

bool spawn(const Spawn& s, Proc& p, std::string* error);
// Reaps `p` when it has exited; waits at most timeout_s (0: just look). True when it has exited.
bool wait_exit(Proc& p, double timeout_s);
enum class Signal { Terminate, User1, Kill };
// User1 is the player's end request (SIGUSR1; on Windows the player's named event,
// sa2_launch_win32.cpp).
bool send_signal(const Proc& p, Signal s, bool group = false);

std::vector<std::pair<std::string, std::string>> environment();
void ignore_broken_pipes();
// SIGINT and SIGTERM to the launcher set a counter instead of ending it.
void catch_interrupts();
int interrupts();
void sleep_ms(int ms);
uint32_t process_id();
// The directory holding this executable ("" when the host cannot say).
std::string self_dir();

}  // namespace sa2l
