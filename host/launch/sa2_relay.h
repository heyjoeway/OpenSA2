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
// Pace (play_live's lead_frames): while `lead_frames` or more whole frames have been read and
// not yet handed on in full, the reader reads nothing, so the game's pipe fills and the game
// waits in its next Present's write. The guest reads only the virtual clock, so waiting changes
// nothing it computes. With lead_frames 0 the reader is held only by `cap_bytes` of queued data.
//
// If the player goes (its pipe breaks), the relay keeps draining the game's FIFO and drops what
// it reads, so the game can still be asked to stop and end through its own code.
#pragma once

#include "sa2_launch_platform.h"

#include <atomic>
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
    bool writer_seen = false;           // the game opened the FIFO and wrote
    bool ended_by_eof = false;          // the game closed the FIFO
    bool player_gone = false;           // the player's pipe broke
    std::string framing_problem;
};

class Relay {
public:
    Relay(Fd game_read, Fd player_write, uint32_t lead_frames, size_t cap_bytes)
        : in_(game_read), out_(player_write), lead_(lead_frames), cap_(cap_bytes) {}
    // Runs until the game closes the FIFO (or `game_alive` turns false with no writer ever
    // seen), then hands on what is queued and closes the player's pipe. Blocks.
    void run(const std::atomic<bool>& game_alive);
    RelayStats stats() const;
    uint64_t frames_out() const { return frames_out_.load(); }
    // Called first on the writer thread (the one that copies into the player's pipe), so a
    // host can find it: the Android session puts it in the player's ADPF session and counts it.
    std::function<void()> on_writer_start;

private:
    void writer();
    Fd in_, out_;
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
};

}  // namespace sa2l
