// input.h: the player window's keyboard, sent to the game (sa2_player --input-path).
//
// Design 3.5: "Input: xinput/dinput stubs fed from the host." The game reads
// its keyboard as the 256-byte DirectInput state GetDeviceState answers, and
// the runtime's device takes that state from a FIFO when SA2_INPUT_DI_SOURCE
// names one (runtime/sa2_dinput.h, "The host keyboard"). This is the writing
// end: the SDL key events of the player's own window, mapped from SDL
// scancodes to DIK codes (a US layout), kept as 256 bytes (0x80 held, 0 up),
// and sent whole whenever they change, as one record of kRecordBytes:
// uint32 kMagic, uint32 seq (from 0, one more per key record written), uint8 keys[256],
// little-endian. A record is under PIPE_BUF, so one write(2) to a pipe is
// atomic: the reader never sees half of one from this writer.
//
// FIFO transport does not block. Diagnostic FILE logging flushes synchronously.
// The FIFO is opened O_WRONLY|O_NONBLOCK, again at every
// pump while it answers ENXIO (no reader yet). A write that answers EAGAIN
// (the pipe full) leaves the state owed, and the next pump sends the state as
// it is then. The game holds at a poll every key the records it read held, so
// a press and its release both sent reach it even between two polls; only a
// state the full pipe kept back is lost. Repeats, other
// windows' events and keys the table does not map change nothing. Losing
// focus, the window closing and SDL_EVENT_QUIT let every key up, so no key
// stays held in the game while the player cannot see its release; so does
// close(). No SDL function is called: events are read, never polled, here.
// Until start() succeeds, event() and pump() do nothing, so a sender made
// before the window is up (the report then carries it) opens nothing early.
//
// Gamepads (runtime/sa2_dinput.h, "The host gamepads"): send_pads() writes the
// four XInput slots as one record of kPadRecordBytes on the same FIFO, uint32
// kPadMagic, uint32 seq (the pad records' own counter, from 0: key records keep
// theirs, so the key seqs tile from 0 as they did without pads), then per slot u8
// connected, u8 0, u16 buttons (XINPUT_GAMEPAD_* bits), u8 left and right
// trigger, i16 LX, LY, RX, RY, u8 0[2], little-endian; a slot not connected is
// written as zeroes. A record is written when the set differs from the last one
// written or one is owed (a full pipe, a reader gone), and **again on every new
// connection**, because the game makes its pad objects at one scan at boot and
// its runtime waits there, a bounded time, for the first gamepad record: a
// player sends its pads as soon as it knows them, before the first frame, and
// calls pump() while it waits for the stream. close() sends every slot
// disconnected, as it lets every key up. A sender whose send_pads() was never
// called writes no gamepad record and reports exactly as before. The game reads
// a pad for player 1 only when the game runs with SA2_GUEST_CMDLINE=-r (it then
// gives player 1 the pad connected at its boot scan, and the keyboard when none
// is; runtime/sa2_dinput.h): the session that starts the game sets that.
#pragma once

#include <SDL3/SDL_events.h>
#include <SDL3/SDL_scancode.h>

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace sa2_input {

constexpr uint32_t kMagic = 0x4B324153u;   // "SA2K": runtime/sa2_dinput.h SA2_DI_KEYREC_MAGIC
constexpr size_t kRecordBytes = 264;       // SA2_DI_KEYREC_BYTES
constexpr uint32_t kPadMagic = 0x50324153u;  // "SA2P": SA2_DI_PADREC_MAGIC
constexpr size_t kPadSlots = 4;              // SA2_DI_PAD_SLOTS, XInput's four
constexpr size_t kPadRecordBytes = 72;       // SA2_DI_PADREC_BYTES
constexpr SDL_KeyboardID kSyntheticKeyboard = 0x5341324bu;

// One XInput slot: buttons are XINPUT_GAMEPAD_* bits (DPAD_UP 0x0001, DOWN 0x0002,
// LEFT 0x0004, RIGHT 0x0008, START 0x0010, BACK 0x0020, LEFT_THUMB 0x0040,
// RIGHT_THUMB 0x0080, LEFT_SHOULDER 0x0100, RIGHT_SHOULDER 0x0200, A 0x1000,
// B 0x2000, X 0x4000, Y 0x8000); triggers 0..255; sticks -32768..32767, up positive.
struct PadState {
    bool connected = false;
    uint16_t buttons = 0;
    uint8_t lt = 0, rt = 0;
    int16_t lx = 0, ly = 0, rx = 0, ry = 0;
};
struct PadSet {
    PadState slot[kPadSlots];
};

// The DirectInput key code (DIK_*, zig's any-windows-any/dinput.h) of an SDL
// scancode on a US layout; 0, which no key has, when the table maps none.
uint8_t dik_of(SDL_Scancode s);

class KeySender {
public:
    using SendClock = double (*)();
    // Borrowed stream/path: caller owns the FILE through close/destruction. No
    // path is opened here. Null clock selects CLOCK_MONOTONIC.
    explicit KeySender(std::string path, FILE* key_log = nullptr,
                       std::string key_log_path = {}, SendClock clock = nullptr);
    ~KeySender();
    KeySender(const KeySender&) = delete;
    KeySender& operator=(const KeySender&) = delete;

    // The path must name a FIFO; false, with last_error() saying why, when it
    // does not. Ignores SIGPIPE, so a reader that goes away is an EPIPE answer
    // and not the end of the player; then makes the first open attempt.
    bool start();
    // One SDL event, of this window; caller supplies CLOCK_MONOTONIC dequeue
    // time and the SDL timestamp's age. No legacy event overload is kept.
    void event(const SDL_Event& e, uint32_t window_id, double handled_s, uint64_t age_ns);
    // Once per event pump: the FIFO opened if it is not yet, and the state sent
    // if a send is owed (the pads too, on every new connection).
    void pump();
    // The four pad slots, one SA2P record when they differ from the last one
    // written or one is owed; written now if the FIFO is open, else owed to the
    // next pump that opens it. True when the game's FIFO holds this state (sent
    // now or before); false when it is owed, or before start() / after close().
    bool send_pads(const PadSet& pads);
    // Every key up, sent if it can be without waiting; then the FIFO closed.
    void close();
    // The report's "host_input" member, ending ",\n".
    std::string report_json() const;
    // The last thing that went wrong, kept after the FIFO is open again: a
    // record of what happened, not the sender's state (open_ says that).
    const std::string& last_error() const { return last_error_; }
    // A failed diagnostic log never changes FIFO delivery. The caller must
    // refuse complete latency evidence when this sticky result is false.
    bool log_ok() const { return log_error_.empty(); }
    const std::string& log_error() const { return log_error_; }

private:
    struct Change {
        uint64_t id;
        SDL_Scancode scancode;
        uint8_t dik;
        bool down, synthetic;
        uint64_t age_ns;
        double handled_s;
    };
    void clear(double handled_s);
    void change(SDL_Scancode scancode, uint8_t dik, bool down, bool synthetic,
                double handled_s, uint64_t age_ns);
    void log_change(const Change& row, const char* status, uint32_t seq = 0, double sent_s = 0);
    void finish_pending(const char* status);
    void log_failed(const char* why, bool io = true);
    void send();
    void send_pad_record();
    int write_record(const uint8_t* rec, size_t n);
    void drop(const char* why);

    std::string path_;
    int fd_ = -1;
#ifdef _WIN32
    void* pipe_ = nullptr;   // the named pipe's server handle (input.cpp); fd_ is its connection
#endif
    uint8_t keys_[256] = {};
    bool synthetic_held_[256] = {};
    FILE* key_log_ = nullptr;
    std::string key_log_path_, log_error_;
    SendClock clock_;
    std::vector<Change> pending_;
    uint64_t change_id_ = 0, log_rows_ = 0, log_sent_ = 0, log_lost_ = 0,
             log_unsent_ = 0, log_io_errors_ = 0, synthetic_key_events_ = 0;
    bool owed_ = false, closed_ = false, started_ = false;
    uint32_t seq_ = 0, pad_seq_ = 0;    // each kind's next seq: key records, pad records
    PadSet pads_ {};
    bool pads_known_ = false, pads_owed_ = false;
    uint64_t pad_records_ = 0, pad_changes_ = 0, pad_calls_ = 0;
    uint64_t open_attempts_ = 0, enxio_ = 0, key_events_ = 0, repeats_ = 0, other_window_ = 0,
             unmapped_ = 0, changes_ = 0, clears_ = 0, records_ = 0, retries_ = 0,
             write_errors_ = 0;
    std::string last_error_;
};

}  // namespace sa2_input
