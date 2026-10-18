// sa2_relay.cpp: see sa2_relay.h.
#include "sa2_relay.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>

#ifndef _WIN32
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace sa2l {

namespace {
constexpr uint8_t kMagic[8] = {'S', 'A', '2', 'D', '3', 'D', '9', 'S'};
constexpr uint32_t kKindFrame = 3;
constexpr size_t kChunk = 1u << 20;

// Pace (sa2_relay.h). The player's pipe holds kPlayerPipe, Linux's default, so a frame is handed
// on in full only when the player has read all but that much of it. The game waits after its
// write of frame K until frame K - lead has been handed on: on the pace pipe's next byte, or, for
// a game that takes no credits, on its full FIFO while the reader holds.
//
// Until 2026-10-17 the game's FIFO did the waiting: the relay shrank it to one page while the
// lead held, so the game's next Present write waited after its first page, and grew it back to
// the launcher's 1 MB when it read again, so a frame passed in one or two writes. Both pipes were
// 1 MB on Linux and Android before the thermal unit (2026-10-12), and with a game faster than the
// player about four frames waited between them; on Linux a key pressed in a stage reached the
// Present of its first frame 112 to 137 ms after the player sent it, and 81 ms with the one-page
// FIFO and this player pipe (the unit's measurement over the synthetic keys of paced live
// sessions). A FIFO of 64 KB throughout bounds a large frame as well, but hands it over in ten
// pieces, each a wait on the relay while the game holds the guest lock; and the logos' and menus'
// frames are about 5 KB, so a fixed FIFO larger than a page lets many of them through.
// The grow came while the game's write slept on the full page, which is the resize Linux 5.8 to
// 6.7 can lose the wakeup of: pipe_resize_ring wakes the pipe's writers before pipe_set_size
// raises max_usage, a writer that looks in between sees the pipe still full and sleeps again, and
// no later read wakes it, since pipe_read wakes writers only when the pipe was full by the new
// max_usage. The game then waits in write(2) for good, holding the guest lock, and every guest
// thread with it. That is the likely cause of the AYN Thor's stall at the SEGA logo (Android 13 on
// a QCS8550, a 5.15-era kernel), and most likely when the writer shares a CPU with the relay, so
// that the wakeup preempts the resize. The pace pipe waits at the same point in the game's frame,
// after Present, with no resize, and for frames of any size.
constexpr size_t kPlayerPipe = 64u << 10;

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
double seconds_since(std::chrono::steady_clock::time_point t) {
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t).count();
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

// The pace pipe's bytes. Its write end is O_NONBLOCK: a game that takes no credits fills it, and
// the relay then counts what the full pipe refused rather than wait on the game.
void Relay::give_credits(uint64_t n) {
    static const uint8_t ones[64] = {};
    while (n && credits_ != kNoFd) {
        const size_t ask = size_t(std::min<uint64_t>(n, sizeof ones));
#ifndef _WIN32
        const ssize_t k = write(int(credits_), ones, ask);
        if (k < 0 && errno == EINTR) continue;
        if (k < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            std::lock_guard<std::mutex> lk(mu_);
            st_.credits_dropped += n;
            return;
        }
        if (k <= 0) {                     // the game is gone (EPIPE): nobody waits on it
            close_credits();
            return;
        }
        n -= uint64_t(k);
        std::lock_guard<std::mutex> lk(mu_);
        st_.credits += uint64_t(k);
#else
        (void)ask;
        return;
#endif
    }
}

// Closed when the player goes and when the stream ends: the recorder reads end of file and paces
// no more, so the game can still be asked to stop and end through its own code.
void Relay::close_credits() { close_fd(credits_); }

void Relay::writer() {
    if (on_writer_start) on_writer_start();
    uint64_t sent = 0, out = 0;
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
        uint64_t newly_out = 0;
        bool gone_now = false;
        {
            std::lock_guard<std::mutex> lk(mu_);
            queued_ -= chunk.size();
            if (!ok) {
                gone_now = !st_.player_gone;
                st_.player_gone = true;
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
                newly_out = st_.frames_out - out;
                out = st_.frames_out;
            }
            cv_.notify_all();
        }
        if (gone_now) close_credits();
        else if (newly_out) give_credits(newly_out);
    }
    close_fd(out_);
}

// The hold, mu_ held: the lead of whole frames read and not yet handed on, or the byte cap; never
// once the player is gone. Derived each time from what it depends on, never kept in a flag: a flag
// set at a hold's start and cleared where the reader sees it end stayed set when the writer ended
// the hold between two of the reader's looks, which turned the stall watch off for the session.
bool Relay::held_locked() const {
    if (st_.player_gone) return false;
    if (lead_ && st_.frames_in >= st_.frames_out + lead_) return true;
    return queued_ >= cap_;
}

// The stall watch (sa2_relay.h). The reader's thread, mu_ not held.
void Relay::watch_stall(const std::atomic<bool>& game_alive) {
    std::string line;
    {
        std::lock_guard<std::mutex> lk(mu_);
        const auto& ends = parser_.frame_ends();
        // Bytes past a whole frame: the start of a frame whose write has not finished. Before
        // the first frame the header waits, legitimately, for the game's first Present.
        if (!watching_.load() || !game_alive.load() || !st_.writer_seen || st_.player_gone || held_locked() ||
            ends.empty())
            return;
        const uint64_t partial = parser_.bytes() - ends.back();
        const double quiet = seconds_since(quiet_since_);
        if (partial == 0 || quiet < next_nudge_s_) return;
        st_.nudges++;
        const uint64_t n = st_.nudges;
        nudge_every_s_ = std::min(nudge_every_s_ * 2, 32.0);
        next_nudge_s_ = quiet + nudge_every_s_;
        char buf[320];
        std::snprintf(buf, sizeof buf,
                      "relay: no byte from the game for %.1f s with %llu bytes of frame %llu read (FIFO %llu bytes, "
                      "%llu frames in, %llu out); nudge %llu: ",
                      quiet, (unsigned long long)partial, (unsigned long long)ends.size() + 1,
                      (unsigned long long)pipe_size(in_), (unsigned long long)st_.frames_in,
                      (unsigned long long)st_.frames_out, (unsigned long long)n);
        line = buf;
    }
    const bool sent = nudge ? nudge() : false;
    line += sent ? "signalled the game" : "no signal to send on this host";
    if (note) note(line);
}

void Relay::run(const std::atomic<bool>& game_alive) {
    // The player's pipe to kPlayerPipe: only with a lead, and only where pipe sizes can be read
    // and set. Before the writer thread starts, so no write of the relay's waits on it.
    if (lead_ && pipe_size(in_) != 0) resize_pipe(out_, kPlayerPipe);
#ifndef _WIN32
    if (credits_ != kNoFd) {
        const int fl = fcntl(int(credits_), F_GETFL);
        if (fl < 0 || fcntl(int(credits_), F_SETFL, fl | O_NONBLOCK) != 0) close_credits();
    }
#endif
    // The game's first lead_ frames go without waiting (with lead 0 every frame does, and the
    // pace pipe is closed at once).
    if (lead_) give_credits(lead_);
    else close_credits();
    std::thread w([this] { writer(); });
    std::vector<uint8_t> buf(kChunk);
    for (;;) {
        bool still_held = false;
        {
            std::unique_lock<std::mutex> lk(mu_);
            if (held_locked()) {
                st_.holds++;
                cv_.wait_for(lk, std::chrono::milliseconds(200), [&] { return !held_locked(); });
                // The game's silence while the reader held was the reader's doing. Set after every
                // wait, so it is right whichever look sees the hold end: this one, or the next,
                // when the writer hands a frame on between the two.
                quiet_since_ = std::chrono::steady_clock::now();
                still_held = held_locked();
            }
        }
        if (still_held) {
            if (on_hold_wait) on_hold_wait();
            continue;
        }
        int r = wait_readable(in_, 100);
        if (r < 0) break;
        if (r == 0) {
            if (!game_alive.load() && !st_.writer_seen) break;
            watch_stall(game_alive);
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
        quiet_since_ = std::chrono::steady_clock::now();
        next_nudge_s_ = nudge_every_s_ = kStallSeconds;
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
    close_credits();
}

RelayStats Relay::stats() const {
    std::lock_guard<std::mutex> lk(mu_);
    RelayStats s = st_;
    const auto& ends = parser_.frame_ends();
    s.partial_bytes = parser_.bytes() - (ends.empty() ? 0 : ends.back());
    s.fifo_bytes = reading_done_ ? 0 : pipe_size(in_);
    s.held_now = !reading_done_ && held_locked();
    s.quiet_s = s.held_now ? 0.0 : seconds_since(quiet_since_);
    return s;
}

}  // namespace sa2l
