// sa2_relay.cpp: see sa2_relay.h.
#include "sa2_relay.h"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <thread>

#ifndef _WIN32
#include <fcntl.h>
#endif

namespace sa2l {

namespace {
constexpr uint8_t kMagic[8] = {'S', 'A', '2', 'D', '3', 'D', '9', 'S'};
constexpr uint32_t kKindFrame = 3;
constexpr size_t kChunk = 1u << 20;

// The pace's two pipe sizes (sa2_relay.h, "Pace"). The player's pipe holds kPlayerPipe, Linux's
// default, so a frame is handed on in full only when the player has read all but that much of
// it. The game's FIFO holds one page while the lead is reached, so the game's next Present
// write waits after its first page, and its own size again (the launcher's 1 MB) when the
// relay reads, so a frame passes in one or two writes.
//
// Both were 1 MB on Linux and Android before the thermal unit (2026-10-12), so with a game
// faster than the player about four frames waited between them (1 MB in each pipe and the
// relay's frame): on Linux a key pressed in a stage reached the Present of its first frame 112
// to 137 ms after the player sent it, and 81 ms with these sizes (the unit's measurement over
// the synthetic keys of paced live sessions, private/generated/s8/thermal/key_latency.py). A
// FIFO of 64 KB throughout would bound it as well, but made the game hand each frame over in ten
// pieces, each a wait on the relay while it holds the guest lock (unit perf-game's measurement,
// sa2_launch_posix.cpp). The kernel rounds a size up to whole pages.
constexpr size_t kPlayerPipe = 64u << 10;
constexpr size_t kFifoHeld = 4096;

// The pipe's size, 0 where it cannot be read or set (Windows: a named pipe's buffer is its
// server's); the size it has after asking for `bytes` (the kernel rounds up, and refuses to
// shrink below what the pipe holds).
size_t pipe_size(Fd fd) {
#ifdef F_GETPIPE_SZ
    const int n = fcntl(int(fd), F_GETPIPE_SZ);
    return n > 0 ? size_t(n) : 0;
#else
    (void)fd;
    return 0;
#endif
}
size_t resize_pipe(Fd fd, size_t bytes) {
#ifdef F_SETPIPE_SZ
    (void)fcntl(int(fd), F_SETPIPE_SZ, int(bytes));
#else
    (void)bytes;
#endif
    return pipe_size(fd);
}
uint32_t le32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
}  // namespace

// The bytes are read where they are: only the start of a record header (or of the file header)
// that a piece ends inside is kept, in pending_, at most 15 bytes. Before the thermal unit
// (2026-10-12) every byte was copied into a buffer of its own, allocated afresh for each piece:
// the whole stream, 0.64 MB a frame in a session (its game.fifo.status: 4,200,655,984 bytes in
// 6,593 frames).
void FrameParser::feed(const uint8_t* data, size_t n) {
    const uint64_t start = pos_;                      // the stream offset of data[0]
    const uint64_t kept = start - pending_.size();    // ... and of pending_[0]
    pos_ += n;
    if (!problem_.empty()) {
        pending_.clear();
        return;
    }
    // The `len` bytes at stream offset `at`, which lie in [kept, pos_).
    auto take = [&](uint64_t at, uint8_t* out, size_t len) {
        for (size_t i = 0; i < len; i++, at++)
            out[i] = at < start ? pending_[size_t(at - kept)] : data[size_t(at - start)];
    };
    // Keep [from, pos_) for the next piece (from >= kept).
    auto keep = [&](uint64_t from) {
        uint8_t tail[16];
        const size_t len = size_t(pos_ - from);
        take(from, tail, len);
        pending_.assign(tail, tail + len);
    };
    if (!have_header_) {
        if (pos_ < 16) {
            keep(kept);
            return;
        }
        uint8_t head[16];
        take(0, head, 16);
        if (std::memcmp(head, kMagic, 8) != 0) {
            problem_ = "the stream does not begin with SA2D3D9S";
            pending_.clear();
            return;
        }
        header_bytes_ = le32(head + 12);
        next_ = (uint64_t(header_bytes_) + 3) & ~uint64_t(3);
        have_header_ = true;
    }
    bool kept_tail = false;
    for (;;) {
        if (next_ + 12 > pos_) {
            if (next_ < pos_) {
                keep(next_);
                kept_tail = true;
            }
            break;
        }
        uint8_t rec[8];
        if (next_ >= start) std::memcpy(rec, data + (next_ - start), 8);
        else take(next_, rec, 8);
        const uint32_t kind = le32(rec), size = le32(rec + 4);
        if (size < 12 || size % 4) {
            problem_ = "the record at offset " + std::to_string(next_) + " has size " + std::to_string(size);
            break;
        }
        if (kind == kKindFrame) waiting_.push_back(next_ + size);
        records_++;
        next_ += size;
    }
    if (!kept_tail) pending_.clear();
    size_t done = 0;
    while (done < waiting_.size() && waiting_[done] <= pos_) ends_.push_back(waiting_[done++]);
    if (done) waiting_.erase(waiting_.begin(), waiting_.begin() + long(done));
}

void Relay::writer() {
    if (on_writer_start) on_writer_start();
    uint64_t sent = 0;
    for (;;) {
        std::vector<uint8_t> chunk;
        {
            std::unique_lock<std::mutex> lk(mu_);
            cv_.wait(lk, [&] { return !queue_.empty() || reading_done_; });
            if (queue_.empty()) break;
            chunk = std::move(queue_.front());
            queue_.pop_front();
        }
        bool ok = !st_.player_gone && write_all(out_, chunk.data(), chunk.size());
        std::lock_guard<std::mutex> lk(mu_);
        queued_ -= chunk.size();
        if (!ok) {
            if (!st_.player_gone) st_.player_gone = true;
            st_.bytes_dropped += chunk.size();
            for (auto& c : queue_) st_.bytes_dropped += c.size();
            queue_.clear();
            queued_ = 0;
        } else {
            sent += chunk.size();
            st_.bytes_out = sent;
            const auto& ends = parser_.frame_ends();
            st_.frames_out = uint64_t(std::upper_bound(ends.begin(), ends.end(), sent) - ends.begin());
            frames_out_.store(st_.frames_out);
        }
        cv_.notify_all();
    }
    close_fd(out_);
}

void Relay::run(const std::atomic<bool>& game_alive) {
    // The pace's pipes (kPlayerPipe, kFifoHeld): only with a lead, and only where both sizes can
    // be read and set; elsewhere the sizes are left as they are.
    const size_t fifo_open = lead_ ? pipe_size(in_) : 0;
    const bool sized = fifo_open > kFifoHeld && resize_pipe(out_, kPlayerPipe) != 0;
    bool shrink_ok = sized;   // cleared when a grow-back is refused (pipe-user-pages-soft)
    bool fifo_small = false;
    std::thread w([this] { writer(); });
    std::vector<uint8_t> buf(kChunk);
    for (;;) {
        {
            // The hold: the lead of whole frames not handed on, or the byte cap.
            std::unique_lock<std::mutex> lk(mu_);
            auto lead_reached = [&] { return !st_.player_gone && lead_ && st_.frames_in >= st_.frames_out + lead_; };
            auto held = [&] {
                if (st_.player_gone) return false;
                if (lead_reached()) return true;
                return queued_ >= cap_;
            };
            // The FIFO one page while the lead holds, its own size again when it does not. A
            // shrink the kernel refuses (the FIFO holds more than a page) is asked again at the
            // next look; it is made at the look after the read that reached the lead, when the
            // game has just finished its write.
            // A grow-back the kernel refuses (the user's pipe pages over pipe-user-pages-soft:
            // a shrink is always allowed, a grow is not) stops the shrinking for the session;
            // the grow is still asked at every look, so the FIFO gets its size back once pages
            // are free, and is never shrunk again into frames of one page each.
            auto fit_fifo = [&] {
                if (!sized) return;
                if (fifo_small) {
                    if (lead_reached() && shrink_ok) return;
                    const size_t now = resize_pipe(in_, fifo_open);
                    fifo_small = now == 0 || now < fifo_open;
                    if (fifo_small) shrink_ok = false;
                    return;
                }
                if (!shrink_ok || !lead_reached()) return;
                const size_t now = resize_pipe(in_, kFifoHeld);
                fifo_small = now != 0 && now < fifo_open;
            };
            fit_fifo();
            if (held()) {
                st_.holds++;
                cv_.wait_for(lk, std::chrono::milliseconds(200), [&] { return !held(); });
                if (held()) continue;
                fit_fifo();
            }
        }
        int r = wait_readable(in_, 100);
        if (r < 0) break;
        if (r == 0) {
            if (!game_alive.load() && !st_.writer_seen) break;
            continue;
        }
        long n = read_some(in_, buf.data(), buf.size());
        if (n == -1) continue;
        if (n == -2) break;
        if (n == 0) {
            // End of file: the writer closed, or none has opened yet (Linux does not report a
            // hang-up on a FIFO no writer ever opened, so a 0 here before data is a writer
            // that opened and closed with nothing written, or a spurious wake).
            if (st_.writer_seen || !game_alive.load()) {
                st_.ended_by_eof = st_.writer_seen;
                break;
            }
            sleep_ms(20);
            continue;
        }
        std::lock_guard<std::mutex> lk(mu_);
        st_.writer_seen = true;
        st_.bytes_in += uint64_t(n);
        parser_.feed(buf.data(), size_t(n));
        st_.records = parser_.records();
        st_.frames_in = parser_.frame_ends().size();
        if (st_.framing_problem.empty()) st_.framing_problem = parser_.problem();
        if (st_.player_gone) {
            st_.bytes_dropped += uint64_t(n);
        } else {
            queue_.emplace_back(buf.begin(), buf.begin() + n);
            queued_ += size_t(n);
            cv_.notify_all();
        }
    }
    {
        std::lock_guard<std::mutex> lk(mu_);
        reading_done_ = true;
        cv_.notify_all();
    }
    w.join();
}

RelayStats Relay::stats() const {
    std::lock_guard<std::mutex> lk(mu_);
    return st_;
}

}  // namespace sa2l
