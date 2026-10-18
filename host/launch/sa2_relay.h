// sa2_relay.h: the stream relay between the game and the player, tools/s5/present_live.py's
// relay rewritten in C++ for the product.
//
// The recorder (host/sa2_present_rec.c) write(2)s the stream into a FIFO, a whole frame per
// write at each Present, and never seeks. The relay holds the FIFO's read end from before the
// game starts (so the game's open never waits, and the game never writes into a pipe nobody
// reads, which would end it by SIGPIPE), and hands every byte on to the player's video pipe in
// order. It finds each SA2S_FRAME record by the framing alone (host/sa2_stream.h: a 16-byte file
// header whose last word is the header's length, the header JSON padded to 4, then records that
// each begin [kind, bytes, seq]), as present_live's FrameClock does.
//
// Pace (play_live's lead_frames), with a pace pipe (`pace_credits`, POSIX): the recorder reads one
// byte from the pipe after each frame it writes (host/sa2_present_rec.c, SA2_REC_PACE_FD), and the
// relay writes `lead_frames` bytes before the game's first frame and one more for each frame it
// has handed on in full. So the game waits after its write of frame K until frame K - lead_frames
// has been handed on, whatever the frames' sizes. The guest reads only the virtual clock, so
// waiting changes nothing it computes. The reader also holds: while `lead_frames` or more whole
// frames have been read and not yet handed on in full, it reads nothing, so a game that takes no
// credits waits on its full FIFO instead. With lead_frames 0 neither holds, and the reader is
// held only by `cap_bytes` of queued data. The relay closes the pace pipe when the player goes or
// the stream ends; the recorder then stops waiting.
//
// The game's FIFO keeps the size the launcher gave it before the game started: the relay never
// resizes it, since a resize while the game's write sleeps on the full pipe can lose that write's
// wakeup on Linux 5.8 to 6.7 (fixed upstream by "pipe: wakeup wr_wait after setting max_usage",
// backported to 5.10, 5.15, 6.1 and 6.6 in early 2024), and the game then waits in write(2)
// forever, holding the guest lock (sa2_relay.cpp, "Pace").
//
// The stall watch: when part of a frame has been read and no byte more has come for a second
// while the game runs and the reader is not holding, the relay calls `nudge` (the host's: a
// signal the runtime takes as a no-op with SA_RESTART, which restarts a write the kernel left
// asleep) and says so through `note`; again after 2, 4, ... up to 32 s while it lasts.
//
#pragma once

#include "sa2_launch_platform.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

namespace sa2l {

// The framing of host/sa2_stream.h, fed in arbitrary pieces.
class FrameParser {
public:
    void feed(const uint8_t* data, size_t n);
    uint64_t bytes() const { return pos_; }
    uint64_t records() const { return records_; }
    // The end offset of each whole FRAME record, in order.
    const std::vector<uint64_t>& frame_ends() const { return ends_; }
    const std::string& problem() const { return problem_; }
    uint32_t header_bytes() const { return header_bytes_; }

private:
    uint64_t pos_ = 0, next_ = 0;
    bool have_header_ = false;
    std::vector<uint8_t> pending_;
    std::vector<uint64_t> waiting_, ends_;
    uint64_t records_ = 0;
    uint32_t header_bytes_ = 0;
    std::string problem_;
};

struct RelayStats {
    uint64_t bytes_in = 0, bytes_out = 0, bytes_dropped = 0;
    uint64_t records = 0, frames_in = 0, frames_out = 0;
    uint64_t holds = 0;                 // times the reader waited on the lead
    uint64_t credits = 0;               // pace bytes written (lead_frames, then one a frame out)
    uint64_t credits_dropped = 0;       // pace bytes the full pipe refused (the game took none)
    uint64_t nudges = 0;                // the stall watch's calls of `nudge`
    // At the time stats() is called:
    uint64_t partial_bytes = 0;         // bytes read past the last whole frame
    uint64_t fifo_bytes = 0;            // the game FIFO's size (0 where it cannot be read)
    double quiet_s = 0;                 // seconds since the game's last byte, the reader not holding
    bool held_now = false;              // the reader is holding
    bool writer_seen = false;           // the game opened the FIFO and wrote
    bool ended_by_eof = false;          // the game closed the FIFO
    bool player_gone = false;           // the player's pipe broke
    std::string framing_problem;
};

class Relay {
public:
    // `pace_credits`: the write end of the pace pipe (above), which the relay owns and closes;
    // kNoFd for none.
    Relay(Fd game_read, Fd player_write, uint32_t lead_frames, size_t cap_bytes, Fd pace_credits = kNoFd)
        : in_(game_read), out_(player_write), credits_(pace_credits), lead_(lead_frames), cap_(cap_bytes) {}
    // Runs until the game closes the FIFO (or `game_alive` turns false with no writer ever
    // seen), then hands on what is queued and closes the player's pipe and the pace pipe. Blocks.
    void run(const std::atomic<bool>& game_alive);
    RelayStats stats() const;
    uint64_t frames_out() const { return frames_out_.load(); }
    // Called first on the writer thread (the one that copies into the player's pipe), so a
    // host can find it: the Android session puts it in the player's ADPF session and counts it.
    std::function<void()> on_writer_start;
    // The stall watch's (above), called on the reader's thread: nudge returns whether the signal
    // was sent; note gets one line, without a trailing newline.
    std::function<bool()> nudge;
    std::function<void(const std::string&)> note;
    // Called on the reader's thread, mu_ not held, after each wait of a hold that the reader
    // finds still in force; it then looks again. A test's seam: test_relay.cpp hands a frame on
    // there, between two looks.
    std::function<void()> on_hold_wait;

    // Ends the stall watch: the host calls it when it starts to stop the game, whose last bytes
    // may then wait on its exit.
    void end_watch() { watching_.store(false); }
    static constexpr double kStallSeconds = 1.0;        // the stall watch's first interval

private:
    void writer();
    void give_credits(uint64_t n);      // the writer's thread, or run() before and after it
    void close_credits();
    void watch_stall(const std::atomic<bool>& game_alive);
    bool held_locked() const;          // the reader's hold, mu_ held
    Fd in_, out_, credits_;
    uint32_t lead_;
    size_t cap_;
    mutable std::mutex mu_;
    std::condition_variable cv_;
    std::deque<std::vector<uint8_t>> queue_;
    size_t queued_ = 0;
    bool reading_done_ = false;
    RelayStats st_;
    FrameParser parser_;
    std::atomic<uint64_t> frames_out_{0};
    // The stall watch, read under mu_ by stats().
    std::chrono::steady_clock::time_point quiet_since_{std::chrono::steady_clock::now()};
    double next_nudge_s_ = kStallSeconds;  // the silence that brings the next nudge
    double nudge_every_s_ = kStallSeconds; // 1, then 2, 4, ... 32 s between nudges
    std::atomic<bool> watching_{true};
};

}  // namespace sa2l
