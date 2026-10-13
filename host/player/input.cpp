// input.cpp: the player window's keyboard, sent to the game. See input.h.
#include "input.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#ifdef _WIN32
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <csignal>
#include <cstdio>
#include <cstring>

namespace sa2_input {

namespace {

struct Map { SDL_Scancode sdl; uint8_t dik; };

// SDL scancodes (USB HID usages, SDL_scancode.h) to DIK codes (the PC/AT set 1
// scan codes DirectInput names, dinput.h), US layout. Every key the game binds
// is here: private/install's Config/Keyboard.cfg (player 1: W, S, A, D, I, J,
// U, K, Q, E, Enter; player 2: the arrows, End, Page Down, the keypad), and the
// image's own defaults at 0x008AE040 without it (runtime/sa2_dinput.h, "The
// host keyboard"). Documentation/scripts/tests/test_s5_input.py holds each
// value to zig's dinput.h, and the bindings to the image's tables.
constexpr Map kMap[] = {
    {SDL_SCANCODE_ESCAPE, 0x01}, {SDL_SCANCODE_1, 0x02}, {SDL_SCANCODE_2, 0x03},
    {SDL_SCANCODE_3, 0x04}, {SDL_SCANCODE_4, 0x05}, {SDL_SCANCODE_5, 0x06},
    {SDL_SCANCODE_6, 0x07}, {SDL_SCANCODE_7, 0x08}, {SDL_SCANCODE_8, 0x09},
    {SDL_SCANCODE_9, 0x0A}, {SDL_SCANCODE_0, 0x0B}, {SDL_SCANCODE_MINUS, 0x0C},
    {SDL_SCANCODE_EQUALS, 0x0D}, {SDL_SCANCODE_BACKSPACE, 0x0E}, {SDL_SCANCODE_TAB, 0x0F},
    {SDL_SCANCODE_Q, 0x10}, {SDL_SCANCODE_W, 0x11}, {SDL_SCANCODE_E, 0x12},
    {SDL_SCANCODE_R, 0x13}, {SDL_SCANCODE_T, 0x14}, {SDL_SCANCODE_Y, 0x15},
    {SDL_SCANCODE_U, 0x16}, {SDL_SCANCODE_I, 0x17}, {SDL_SCANCODE_O, 0x18},
    {SDL_SCANCODE_P, 0x19}, {SDL_SCANCODE_LEFTBRACKET, 0x1A}, {SDL_SCANCODE_RIGHTBRACKET, 0x1B},
    {SDL_SCANCODE_RETURN, 0x1C}, {SDL_SCANCODE_LCTRL, 0x1D}, {SDL_SCANCODE_A, 0x1E},
    {SDL_SCANCODE_S, 0x1F}, {SDL_SCANCODE_D, 0x20}, {SDL_SCANCODE_F, 0x21},
    {SDL_SCANCODE_G, 0x22}, {SDL_SCANCODE_H, 0x23}, {SDL_SCANCODE_J, 0x24},
    {SDL_SCANCODE_K, 0x25}, {SDL_SCANCODE_L, 0x26}, {SDL_SCANCODE_SEMICOLON, 0x27},
    {SDL_SCANCODE_APOSTROPHE, 0x28}, {SDL_SCANCODE_GRAVE, 0x29}, {SDL_SCANCODE_LSHIFT, 0x2A},
    {SDL_SCANCODE_BACKSLASH, 0x2B}, {SDL_SCANCODE_Z, 0x2C}, {SDL_SCANCODE_X, 0x2D},
    {SDL_SCANCODE_C, 0x2E}, {SDL_SCANCODE_V, 0x2F}, {SDL_SCANCODE_B, 0x30},
    {SDL_SCANCODE_N, 0x31}, {SDL_SCANCODE_M, 0x32}, {SDL_SCANCODE_COMMA, 0x33},
    {SDL_SCANCODE_PERIOD, 0x34}, {SDL_SCANCODE_SLASH, 0x35}, {SDL_SCANCODE_RSHIFT, 0x36},
    {SDL_SCANCODE_KP_MULTIPLY, 0x37}, {SDL_SCANCODE_LALT, 0x38}, {SDL_SCANCODE_SPACE, 0x39},
    {SDL_SCANCODE_CAPSLOCK, 0x3A}, {SDL_SCANCODE_F1, 0x3B}, {SDL_SCANCODE_F2, 0x3C},
    {SDL_SCANCODE_F3, 0x3D}, {SDL_SCANCODE_F4, 0x3E}, {SDL_SCANCODE_F5, 0x3F},
    {SDL_SCANCODE_F6, 0x40}, {SDL_SCANCODE_F7, 0x41}, {SDL_SCANCODE_F8, 0x42},
    {SDL_SCANCODE_F9, 0x43}, {SDL_SCANCODE_F10, 0x44}, {SDL_SCANCODE_NUMLOCKCLEAR, 0x45},
    {SDL_SCANCODE_SCROLLLOCK, 0x46}, {SDL_SCANCODE_KP_7, 0x47}, {SDL_SCANCODE_KP_8, 0x48},
    {SDL_SCANCODE_KP_9, 0x49}, {SDL_SCANCODE_KP_MINUS, 0x4A}, {SDL_SCANCODE_KP_4, 0x4B},
    {SDL_SCANCODE_KP_5, 0x4C}, {SDL_SCANCODE_KP_6, 0x4D}, {SDL_SCANCODE_KP_PLUS, 0x4E},
    {SDL_SCANCODE_KP_1, 0x4F}, {SDL_SCANCODE_KP_2, 0x50}, {SDL_SCANCODE_KP_3, 0x51},
    {SDL_SCANCODE_KP_0, 0x52}, {SDL_SCANCODE_KP_PERIOD, 0x53}, {SDL_SCANCODE_NONUSBACKSLASH, 0x56},
    {SDL_SCANCODE_F11, 0x57}, {SDL_SCANCODE_F12, 0x58}, {SDL_SCANCODE_KP_ENTER, 0x9C},
    {SDL_SCANCODE_RCTRL, 0x9D}, {SDL_SCANCODE_KP_DIVIDE, 0xB5}, {SDL_SCANCODE_PRINTSCREEN, 0xB7},
    {SDL_SCANCODE_RALT, 0xB8}, {SDL_SCANCODE_PAUSE, 0xC5}, {SDL_SCANCODE_HOME, 0xC7},
    {SDL_SCANCODE_UP, 0xC8}, {SDL_SCANCODE_PAGEUP, 0xC9}, {SDL_SCANCODE_LEFT, 0xCB},
    {SDL_SCANCODE_RIGHT, 0xCD}, {SDL_SCANCODE_END, 0xCF}, {SDL_SCANCODE_DOWN, 0xD0},
    {SDL_SCANCODE_PAGEDOWN, 0xD1}, {SDL_SCANCODE_INSERT, 0xD2}, {SDL_SCANCODE_DELETE, 0xD3},
    {SDL_SCANCODE_LGUI, 0xDB}, {SDL_SCANCODE_RGUI, 0xDC}, {SDL_SCANCODE_APPLICATION, 0xDD},
};

std::string jstr(const std::string& s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += char(c); }
        else if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += char(c);
    }
    return o + "\"";
}

double monotonic_s() {
    struct timespec at {};
    if (::clock_gettime(CLOCK_MONOTONIC, &at) != 0) return -1;
    return double(at.tv_sec) + double(at.tv_nsec) / 1e9;
}

SDL_Scancode scancode_of(uint8_t dik) {
    for (const Map& m : kMap) if (m.dik == dik) return m.sdl;
    return SDL_SCANCODE_UNKNOWN;
}

}  // namespace

uint8_t dik_of(SDL_Scancode s) {
    for (const Map& m : kMap)
        if (m.sdl == s) return m.dik;
    return 0;
}

KeySender::KeySender(std::string path, FILE* key_log, std::string key_log_path, SendClock clock)
    : path_(std::move(path)), key_log_(key_log), key_log_path_(std::move(key_log_path)),
      clock_(clock ? clock : monotonic_s) {}

#ifdef _WIN32
// Windows has no FIFO. The keyboard's channel there is a named pipe (\\.\pipe\<name>), the
// runtime's SA2_INPUT_DI_SOURCE on that host (runtime/sa2_win32.h), which the game opens as its
// client and reads without waiting. The writer is its server: start() creates the one instance
// (refused when a pipe of that name exists already, as a path that is not a FIFO is refused),
// outbound, message-type so a record is written whole or not at all, and non-blocking. fd_ is
// then the connection: 0 while a reader is connected, -1 otherwise. ConnectNamedPipe at a pump
// answers ERROR_PIPE_LISTENING while no reader has opened it (ENXIO's part), a WriteFile that
// writes nothing is the pipe full (EAGAIN's), and ERROR_NO_DATA or ERROR_BROKEN_PIPE is the
// reader gone (EPIPE's): the instance is disconnected and listens again at once (a disconnected
// instance takes no reader until ConnectNamedPipe is called on it again).
namespace {
void relisten(HANDLE h) {
    DisconnectNamedPipe(h);
    ConnectNamedPipe(h, nullptr);   // non-blocking: ERROR_PIPE_LISTENING (or _CONNECTED), read at the pump
}

bool is_pipe_name(const std::string& path) {
    if (path.size() <= 9) return false;
    for (int i = 0; i < 9; i++) {
        const char want = "\\\\.\\pipe\\"[i], got = path[size_t(i)];
        if (want == '\\' ? (got != '\\' && got != '/') : got != want) return false;
    }
    return true;
}
}  // namespace

KeySender::~KeySender() {
    finish_pending("unsent");
    if (pipe_) CloseHandle(static_cast<HANDLE>(pipe_));
}

bool KeySender::start() {
    if (started_ || closed_) return started_;
    if (!is_pipe_name(path_)) {
        last_error_ = path_ + " is not a named pipe (\\\\.\\pipe\\<name>), this host's FIFO";
    } else {
        const HANDLE h = CreateNamedPipeA(path_.c_str(), PIPE_ACCESS_OUTBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                                          PIPE_TYPE_MESSAGE | PIPE_READMODE_BYTE | PIPE_NOWAIT |
                                              PIPE_REJECT_REMOTE_CLIENTS,
                                          1, DWORD(64 * kRecordBytes), 0, 0, nullptr);
        const DWORD e = h == INVALID_HANDLE_VALUE ? GetLastError() : 0;
        if (h == INVALID_HANDLE_VALUE)
            last_error_ = path_ + ": CreateNamedPipe failed (error " + std::to_string(e) +
                          (e == ERROR_ACCESS_DENIED ? ", a pipe of that name exists" : "") + ")";
        else
            pipe_ = h;
    }
    if (!last_error_.empty()) {
        closed_ = true;
        return false;
    }
    started_ = true;
    if (key_log_) {
        if (std::fputs("change\tscancode\tdik\tdown\tsynthetic\tage_ns\thandled_s\tseq\tsent_s\tstatus\n", key_log_) < 0
                || std::fflush(key_log_) != 0 || std::ferror(key_log_))
            log_failed("key log header write/flush");
    }
    pump();
    return true;
}
#else
KeySender::~KeySender() {
    finish_pending("unsent");
    if (fd_ >= 0) ::close(fd_);
}

bool KeySender::start() {
    if (started_ || closed_) return started_;
    struct stat sb {};
    if (::stat(path_.c_str(), &sb) != 0) last_error_ = path_ + ": " + std::strerror(errno);
    else if (!S_ISFIFO(sb.st_mode)) last_error_ = path_ + " is not a FIFO";
    if (!last_error_.empty()) {
        closed_ = true;
        return false;
    }
    std::signal(SIGPIPE, SIG_IGN);
    started_ = true;
    if (key_log_) {
        if (std::fputs("change\tscancode\tdik\tdown\tsynthetic\tage_ns\thandled_s\tseq\tsent_s\tstatus\n", key_log_) < 0
                || std::fflush(key_log_) != 0 || std::ferror(key_log_))
            log_failed("key log header write/flush");
    }
    pump();
    return true;
}
#endif

void KeySender::event(const SDL_Event& e, uint32_t window_id, double handled_s, uint64_t age_ns) {
    if (closed_ || !started_) return;
    switch (e.type) {
    case SDL_EVENT_KEY_DOWN:
    case SDL_EVENT_KEY_UP: {
        if (e.key.windowID != window_id) { other_window_++; return; }
        if (e.key.repeat) { repeats_++; return; }
        const bool synthetic = e.key.which == kSyntheticKeyboard;
        if (synthetic) synthetic_key_events_++;
        else key_events_++;
        const uint8_t dik = dik_of(e.key.scancode);
        if (!dik) { unmapped_++; return; }
        const uint8_t v = e.key.down ? 0x80 : 0;
        if (keys_[dik] == v) return;
        keys_[dik] = v;
        synthetic_held_[dik] = synthetic;
        change(e.key.scancode, dik, e.key.down, synthetic, handled_s, age_ns);
        changes_++;
        owed_ = true;
        send();
        return;
    }
    case SDL_EVENT_WINDOW_FOCUS_LOST:
    case SDL_EVENT_WINDOW_CLOSE_REQUESTED:
        if (e.window.windowID == window_id) clear(handled_s);
        return;
    case SDL_EVENT_QUIT:
        clear(handled_s);
        return;
    default:
        return;
    }
}

void KeySender::clear(double handled_s) {
    bool any = false;
    for (unsigned dik = 0; dik < 256; ++dik) {
        if (!keys_[dik]) continue;
        any = true;
        keys_[dik] = 0;
        change(scancode_of(uint8_t(dik)), uint8_t(dik), false, synthetic_held_[dik], handled_s, 0);
    }
    if (!any) return;
    clears_++;
    owed_ = true;
    send();
}

void KeySender::pump() {
    if (closed_ || !started_) return;
#ifdef _WIN32
    if (fd_ < 0) {
        open_attempts_++;
        if (!ConnectNamedPipe(static_cast<HANDLE>(pipe_), nullptr)) {
            const DWORD e = GetLastError();
            if (e == ERROR_PIPE_LISTENING) { enxio_++; return; }     // no reader yet: next pump
            if (e == ERROR_NO_DATA) {                                 // a reader came and went
                relisten(static_cast<HANDLE>(pipe_));
                enxio_++;
                return;
            }
            if (e != ERROR_PIPE_CONNECTED) {
                last_error_ = "ConnectNamedPipe: error " + std::to_string(e);
                return;
            }
        }
        fd_ = 0;
        if (pads_known_) pads_owed_ = true;    // every new reader learns the pads
    }
    if (owed_) send();
    if (pads_owed_) send_pad_record();
}

void KeySender::drop(const char* why) {
    write_errors_++;
    last_error_ = why;
    relisten(static_cast<HANDLE>(pipe_));
    fd_ = -1;
}
#else
    if (fd_ < 0) {
        open_attempts_++;
        const int fd = ::open(path_.c_str(), O_WRONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) {
            if (errno == ENXIO) enxio_++;                 // no reader yet: next pump
            else last_error_ = "open: " + std::string(std::strerror(errno));
            return;
        }
        struct stat sb {};
        if (::fstat(fd, &sb) != 0 || !S_ISFIFO(sb.st_mode)) {
            ::close(fd);
            last_error_ = path_ + " is no longer a FIFO";
            closed_ = true;
            return;
        }
        fd_ = fd;
        if (pads_known_) pads_owed_ = true;    // every new reader learns the pads
    }
    if (owed_) send();
    if (pads_owed_) send_pad_record();
}

void KeySender::drop(const char* why) {
    write_errors_++;
    last_error_ = why;
    ::close(fd_);
    fd_ = -1;
}
#endif

// One record, whole or not at all: 1 written; 0 not, with the reason counted (the pipe full,
// a retry at the next pump) or the connection dropped (reopened at the next pump).
int KeySender::write_record(const uint8_t* rec, size_t n) {
    if (fd_ < 0) return 0;
#ifdef _WIN32
    DWORD wrote = 0;
    if (!WriteFile(static_cast<HANDLE>(pipe_), rec, DWORD(n), &wrote, nullptr)) {
        const DWORD e = GetLastError();
        drop(e == ERROR_NO_DATA || e == ERROR_BROKEN_PIPE || e == ERROR_PIPE_NOT_CONNECTED
                 ? "write: the reader went away (EPIPE)" : "write failed");
        return 0;
    }
    if (wrote == 0) {
        retries_++;                                   // the pipe is full: the next pump
        return 0;
    }
    const ssize_t got = ssize_t(wrote);
#else
    const ssize_t got = ::write(fd_, rec, n);
#endif
    if (got == ssize_t(n)) return 1;
    if (got < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) {
        retries_++;                                   // the pipe is full: the next pump
        return 0;
    }
    // EPIPE (the reader went away; SIGPIPE is ignored) or a short write, which a
    // pipe never gives a record under PIPE_BUF: reopened at the next pump, and
    // the state sent whole again.
    drop(got < 0 ? (errno == EPIPE ? "write: the reader went away (EPIPE)" : "write failed")
                 : "write: short");
    return 0;
}

// The state as it is now, as one record. Sent or still owed; never waited for.
void KeySender::send() {
    if (fd_ < 0 || !owed_) return;
    uint8_t rec[kRecordBytes];
    for (int i = 0; i < 4; i++) {
        rec[i] = uint8_t(kMagic >> (8 * i));
        rec[4 + i] = uint8_t(seq_ >> (8 * i));
    }
    std::memcpy(rec + 8, keys_, sizeof keys_);
    // Sample BEFORE write: once the write succeeds the game can already poll.
    const double sent_s = key_log_ && log_ok() ? clock_() : 0;
    if (!write_record(rec, sizeof rec)) return;
    for (const Change& row : pending_) log_change(row, "sent", seq_, sent_s);
    pending_.clear();
    records_++;
    seq_++;
    owed_ = false;
}

namespace {
void put16(uint8_t* p, uint16_t v) {
    p[0] = uint8_t(v);
    p[1] = uint8_t(v >> 8);
}
bool same_pad(const PadState& a, const PadState& b) {
    return a.connected == b.connected && a.buttons == b.buttons && a.lt == b.lt && a.rt == b.rt &&
           a.lx == b.lx && a.ly == b.ly && a.rx == b.rx && a.ry == b.ry;
}
}  // namespace

// The pads as they are now, as one SA2P record. Sent or still owed; never waited for.
void KeySender::send_pad_record() {
    if (fd_ < 0 || !pads_owed_) return;
    uint8_t rec[kPadRecordBytes] = {};
    for (int i = 0; i < 4; i++) {
        rec[i] = uint8_t(kPadMagic >> (8 * i));
        rec[4 + i] = uint8_t(pad_seq_ >> (8 * i));
    }
    for (size_t s = 0; s < kPadSlots; s++) {
        const PadState& p = pads_.slot[s];
        if (!p.connected) continue;
        uint8_t* q = rec + 8 + 16 * s;
        q[0] = 1;
        put16(q + 2, p.buttons);
        q[4] = p.lt;
        q[5] = p.rt;
        put16(q + 6, uint16_t(p.lx));
        put16(q + 8, uint16_t(p.ly));
        put16(q + 10, uint16_t(p.rx));
        put16(q + 12, uint16_t(p.ry));
    }
    if (!write_record(rec, sizeof rec)) return;
    pad_records_++;
    pad_seq_++;
    pads_owed_ = false;
}

bool KeySender::send_pads(const PadSet& pads) {
    if (closed_ || !started_) return false;
    pad_calls_++;
    PadSet now {};
    for (size_t s = 0; s < kPadSlots; s++)
        if (pads.slot[s].connected) now.slot[s] = pads.slot[s];   // a slot not connected is zeroes
    bool same = pads_known_;
    for (size_t s = 0; same && s < kPadSlots; s++) same = same_pad(now.slot[s], pads_.slot[s]);
    if (!same) {
        pads_ = now;
        pads_owed_ = true;
        pad_changes_++;
    }
    pads_known_ = true;
    send_pad_record();
    return !pads_owed_ && fd_ >= 0;
}

void KeySender::close() {
    if (closed_) { finish_pending("unsent"); return; }
    bool held = false;
    for (uint8_t key : keys_) held |= key != 0;
    clear(key_log_ && log_ok() && held ? clock_() : 0);
    bool pad_held = false;
    for (const PadState& p : pads_.slot) pad_held |= p.connected;
    if (pad_held) send_pads(PadSet {});              // every slot let go, as every key is
    finish_pending("unsent");
#ifdef _WIN32
    // CloseHandle, not DisconnectNamedPipe: the reader keeps what was written, as a FIFO's does.
    if (pipe_) { CloseHandle(static_cast<HANDLE>(pipe_)); pipe_ = nullptr; }
    fd_ = -1;
#else
    if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
#endif
    closed_ = true;
}

void KeySender::log_failed(const char* why, bool io) {
    if (!log_error_.empty()) return;
    log_error_ = why;
    if (io) {
        ++log_io_errors_;
        log_error_ += ": " + std::string(std::strerror(errno));
    }
}

void KeySender::log_change(const Change& row, const char* status, uint32_t seq, double sent_s) {
    if (!key_log_ || !log_ok()) return;
    if (!std::isfinite(row.handled_s) || row.handled_s < 0
            || row.handled_s < double(row.age_ns) / 1e9
            || (!std::strcmp(status, "sent") && (!std::isfinite(sent_s) || sent_s < row.handled_s))) {
        log_failed("key log invalid timestamp", false);
        return;
    }
    const int wrote = !std::strcmp(status, "sent")
        ? std::fprintf(key_log_, "%llu\t%d\t%u\t%u\t%u\t%llu\t%.9f\t%u\t%.9f\tsent\n",
                       (unsigned long long)row.id, int(row.scancode), unsigned(row.dik),
                       unsigned(row.down), unsigned(row.synthetic), (unsigned long long)row.age_ns,
                       row.handled_s, seq, sent_s)
        : std::fprintf(key_log_, "%llu\t%d\t%u\t%u\t%u\t%llu\t%.9f\t-\t-\t%s\n",
                       (unsigned long long)row.id, int(row.scancode), unsigned(row.dik),
                       unsigned(row.down), unsigned(row.synthetic), (unsigned long long)row.age_ns,
                       row.handled_s, status);
    if (wrote < 0 || std::fflush(key_log_) != 0 || std::ferror(key_log_)) {
        log_failed("key log row write/flush");
        return;
    }
    ++log_rows_;
    if (!std::strcmp(status, "sent")) ++log_sent_;
    else if (!std::strcmp(status, "lost")) ++log_lost_;
    else ++log_unsent_;
}

void KeySender::change(SDL_Scancode scancode, uint8_t dik, bool down, bool synthetic,
                       double handled_s, uint64_t age_ns) {
    if (!key_log_ || !log_ok()) return;
    const Change row {++change_id_, scancode, dik, down, synthetic, age_ns, handled_s};
    auto old = std::find_if(pending_.begin(), pending_.end(),
                            [dik](const Change& pending) { return pending.dik == dik; });
    if (old != pending_.end()) {
        log_change(*old, "lost");
        log_change(row, "lost");
        pending_.erase(old);
    } else {
        pending_.push_back(row);
    }
}

void KeySender::finish_pending(const char* status) {
    for (const Change& row : pending_) log_change(row, status);
    pending_.clear();
}

std::string KeySender::report_json() const {
    uint32_t held = 0;
    for (uint8_t k : keys_) held += k != 0;
    char b[640];
    std::snprintf(b, sizeof b,
                  "\"open\": %s, \"open_attempts\": %llu, \"enxio\": %llu, "
                  "\"key_events\": %llu, \"repeats_ignored\": %llu, \"other_window\": %llu, "
                  "\"unmapped\": %llu, \"changes\": %llu, \"clears\": %llu, \"records_written\": %llu, "
                  "\"retries\": %llu, \"write_errors\": %llu, \"last_seq\": %s, \"held_now\": %u, \"owed\": %s",
                  fd_ >= 0 ? "true" : "false", (unsigned long long)open_attempts_,
                  (unsigned long long)enxio_,
                  (unsigned long long)key_events_, (unsigned long long)repeats_,
                  (unsigned long long)other_window_, (unsigned long long)unmapped_,
                  (unsigned long long)changes_, (unsigned long long)clears_,
                  (unsigned long long)records_, (unsigned long long)retries_,
                  (unsigned long long)write_errors_,
                  records_ ? std::to_string(seq_ - 1).c_str() : "null", held, owed_ ? "true" : "false");
    std::string log = "null";
    if (key_log_) {
        char counts[256];
        std::snprintf(counts, sizeof counts,
                      ", \"rows\": %llu, \"sent\": %llu, \"lost\": %llu, \"unsent\": %llu, \"io_errors\": %llu",
                      (unsigned long long)log_rows_, (unsigned long long)log_sent_,
                      (unsigned long long)log_lost_, (unsigned long long)log_unsent_,
                      (unsigned long long)log_io_errors_);
        log = "{\"path\": " + jstr(key_log_path_) + counts + ", \"error\": " +
              (log_error_.empty() ? std::string("null") : jstr(log_error_)) + "}";
    }
    // Gamepads only once send_pads() has been called, so a keyboard session's report is as it was.
    std::string pads;
    if (pads_known_) {
        uint32_t connected = 0;
        for (const PadState& p : pads_.slot) connected += p.connected;
        char pb[256];
        std::snprintf(pb, sizeof pb,
                      ", \"pads\": {\"calls\": %llu, \"changes\": %llu, \"records_written\": %llu, "
                      "\"connected_now\": %u, \"owed\": %s}",
                      (unsigned long long)pad_calls_, (unsigned long long)pad_changes_,
                      (unsigned long long)pad_records_, connected, pads_owed_ ? "true" : "false");
        pads = pb;
    }
    return "  \"host_input\": {\"path\": " + jstr(path_) + ", \"started\": " +
           (started_ ? "true" : "false") + ", " + b + pads + ", \"synthetic_key_events\": " +
           std::to_string(synthetic_key_events_) + ", \"key_log\": " + log + ", \"last_error\": " +
           (last_error_.empty() ? std::string("null") : jstr(last_error_)) + "},\n";
}

}  // namespace sa2_input
