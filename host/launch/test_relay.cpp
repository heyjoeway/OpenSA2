// test_relay.cpp: sa2l::FrameParser (sa2_relay.cpp) against the parser it replaced, which copied
// every piece into a buffer of its own: the same frame ends, record count, header length and
// problem for streams fed in pieces of every size from 1 byte up, a frame header split across
// pieces at each of its bytes, a bad record size, and a stream that does not begin with the magic.
// Then the relay (POSIX), each case on a FIFO opened and sized as the launcher does (1 MB) and the
// relay with lead 1 onto a pipe made as the launcher makes it (make_pipe):
//   - the pace: a writer that writes each 300 KB frame in one write and then reads its pace byte,
//     as the recorder does at Present (SA2_REC_PACE_FD), and a reader that takes 15 ms a frame:
//     the writer never returns from a Present more than two frames ahead of the frames the reader
//     has read, every byte arrives in order, and the stall watch never fires;
//   - no resize under a writer: a writer that takes no pace bytes (a runtime without them) waits
//     on its full FIFO while the relay holds, and the FIFO's size, sampled every millisecond
//     throughout, never changes (a resize then can lose the writer's wakeup on Linux 5.8 to 6.7);
//   - the stall watch: a writer that stops part way through a frame is nudged about a second
//     later, with the watch's line, and again two seconds after that (it ignores the first),
//     and finishes the frame at the second;
//   - a hold that ends between two looks: the writer hands the held frame on after the reader's
//     wait has timed out and before it looks again (Relay::on_hold_wait); the reader is not
//     holding afterwards, and the stall watch nudges a writer that then stops part way through
//     a frame about a second later;
//   - the player gone: a reader that closes the player's pipe after three frames; the writer
//     reads end of file on the pace pipe and writes the rest unpaced, and the relay ends.
//
//   c++ -std=c++17 -O2 -Ihost/launch host/launch/test_relay.cpp host/launch/sa2_relay.cpp
//       host/launch/sa2_launch_posix.cpp -lpthread -o test_relay && ./test_relay [DIR]
//
// Prints RELAY_PARSER_OK, RELAY_PACE_OK, RELAY_NO_RESIZE_OK, RELAY_STALL_OK, RELAY_HOLD_END_OK and
// RELAY_GONE_OK, and exits 0 when every case holds, else names the first that does not. DIR holds
// the FIFOs (default $TMPDIR or /tmp).
#include "sa2_relay.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <random>
#include <string>
#include <thread>
#include <vector>

#ifndef _WIN32
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#endif

namespace {

// The parser as it was before 2026-10-12 (sa2_relay.cpp at development_snapshot), kept as the reference.
struct OldParser {
    uint64_t pos_ = 0, next_ = 0;
    bool have_header_ = false;
    std::vector<uint8_t> pending_;
    std::vector<uint64_t> waiting_, ends_;
    uint64_t records_ = 0;
    uint32_t header_bytes_ = 0;
    std::string problem_;
    static uint32_t le32(const uint8_t* p) {
        return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
    }
    void feed(const uint8_t* data, size_t n) {
        static const uint8_t kMagic[8] = {'S', 'A', '2', 'D', '3', 'D', '9', 'S'};
        const uint64_t base = pos_ - pending_.size();
        std::vector<uint8_t> buf;
        buf.insert(buf.end(), pending_.begin(), pending_.end());
        buf.insert(buf.end(), data, data + n);
        pos_ += n;
        pending_.clear();
        if (!problem_.empty()) return;
        if (!have_header_) {
            if (buf.size() < 16) { pending_ = buf; return; }
            if (std::memcmp(buf.data(), kMagic, 8) != 0) { problem_ = "the stream does not begin with SA2D3D9S"; return; }
            header_bytes_ = le32(buf.data() + 12);
            next_ = (uint64_t(header_bytes_) + 3) & ~uint64_t(3);
            have_header_ = true;
        }
        for (;;) {
            const uint64_t at = next_ - base;
            if (at + 12 > buf.size()) {
                if (at < buf.size()) pending_.assign(buf.begin() + long(at), buf.end());
                break;
            }
            const uint32_t kind = le32(buf.data() + at), size = le32(buf.data() + at + 4);
            if (size < 12 || size % 4) {
                problem_ = "the record at offset " + std::to_string(next_) + " has size " + std::to_string(size);
                break;
            }
            if (kind == 3) waiting_.push_back(next_ + size);
            records_++;
            next_ += size;
        }
        while (!waiting_.empty() && waiting_.front() <= pos_) {
            ends_.push_back(waiting_.front());
            waiting_.erase(waiting_.begin());
        }
    }
};

void put32(std::vector<uint8_t>& s, uint32_t v) {
    for (int i = 0; i < 4; i++) s.push_back(uint8_t(v >> (8 * i)));
}

// A stream: the 16-byte file header (magic, version, flags, header length), its JSON padded to
// 4, then `records` records of random kind (a third of them frames) and size.
std::vector<uint8_t> make_stream(std::mt19937& rng, int records, bool bad_size_at_end, bool bad_magic) {
    std::vector<uint8_t> s = {'S', 'A', '2', 'D', '3', 'D', '9', bad_magic ? uint8_t('X') : uint8_t('S')};
    put32(s, 2);
    const uint32_t json = 16 + rng() % 300;
    put32(s, json);
    while (s.size() < json) s.push_back('{');
    while (s.size() % 4) s.push_back(' ');
    for (int i = 0; i < records; i++) {
        const uint32_t kind = rng() % 3 == 0 ? 3u : 1u + rng() % 2u;
        const uint32_t size = 12 + 4 * (rng() % (rng() % 8 == 0 ? 4000 : 40));
        put32(s, kind);
        put32(s, size);
        put32(s, uint32_t(i));
        for (uint32_t k = 12; k < size; k++) s.push_back(uint8_t(rng()));
    }
    if (bad_size_at_end) {
        put32(s, 3);
        put32(s, 13);
        put32(s, 0);
        for (int k = 0; k < 40; k++) s.push_back(0);
    }
    return s;
}

bool same(const sa2l::FrameParser& a, const OldParser& b, std::string* why) {
    if (a.frame_ends() != b.ends_) { *why = "frame ends differ"; return false; }
    if (a.records() != b.records_) { *why = "record counts differ"; return false; }
    if (a.problem() != b.problem_) { *why = "problems differ: '" + a.problem() + "' / '" + b.problem_ + "'"; return false; }
    if (a.header_bytes() != b.header_bytes_) { *why = "header lengths differ"; return false; }
    if (a.bytes() != b.pos_) { *why = "byte counts differ"; return false; }
    return true;
}

// Feed `s` to both in pieces from `piece` (a size each call), comparing after every piece.
bool run(const std::vector<uint8_t>& s, const std::function<size_t()>& piece, std::string* why) {
    sa2l::FrameParser a;
    OldParser b;
    size_t at = 0;
    while (at < s.size()) {
        const size_t n = std::min(s.size() - at, std::max<size_t>(1, piece()));
        a.feed(s.data() + at, n);
        b.feed(s.data() + at, n);
        at += n;
        if (!same(a, b, why)) { *why += " after " + std::to_string(at) + " bytes"; return false; }
    }
    return true;
}

}  // namespace

#ifndef _WIN32
namespace {

// The header and `n` frames of three `record`-byte records and a FRAME record each.
struct Stream {
    std::vector<uint8_t> head, all;
    std::vector<std::vector<uint8_t>> frames;
};
Stream make_frames(int n, uint32_t record) {
    Stream st;
    st.head = {'S', 'A', '2', 'D', '3', 'D', '9', 'S'};
    put32(st.head, 2);
    put32(st.head, 32);
    while (st.head.size() < 32) st.head.push_back(' ');
    st.all = st.head;
    for (int f = 0; f < n; f++) {
        std::vector<uint8_t> b;
        for (int k = 0; k < 3; k++) {
            put32(b, 1);
            put32(b, record);
            put32(b, uint32_t(f * 4 + k));
            for (uint32_t i = 12; i < record; i++) b.push_back(uint8_t(f * 7 + k + i));
        }
        put32(b, 3);
        put32(b, 28);
        put32(b, uint32_t(f * 4 + 3));
        for (int i = 0; i < 16; i++) b.push_back(uint8_t(f));
        st.all.insert(st.all.end(), b.begin(), b.end());
        st.frames.push_back(std::move(b));
    }
    return st;
}

// The launcher's ends: the FIFO's reader (1 MB), the player's pipe, and the pace pipe.
struct Ends {
    std::string fifo;
    sa2l::Fd relay_in = sa2l::kNoFd, video_r = sa2l::kNoFd, video_w = sa2l::kNoFd;
    sa2l::Fd pace_r = sa2l::kNoFd, pace_w = sa2l::kNoFd;
    bool make(const std::string& dir, const char* name, std::string* why) {
        fifo = dir + "/test_relay_" + name + "." + std::to_string(getpid()) + ".fifo";
        unlink(fifo.c_str());
        if (!sa2l::make_fifo(fifo, why)) return false;
        sa2l::ignore_broken_pipes();
        relay_in = sa2l::open_fifo_reader(fifo, why);
        if (relay_in == sa2l::kNoFd) return false;
        sa2l::set_pipe_size(relay_in, 1u << 20);
        return sa2l::make_pipe(video_r, video_w, why) && sa2l::make_pipe(pace_r, pace_w, why);
    }
    ~Ends() {
        sa2l::close_fd(video_r);
        sa2l::close_fd(relay_in);
        sa2l::close_fd(pace_r);
        if (!fifo.empty()) unlink(fifo.c_str());
    }
};

// The recorder's pace wait: one byte, or end of file (pacing ends). 1, 0 at end of file, -1 error.
int take_credit(sa2l::Fd fd) {
    uint8_t b;
    for (;;) {
        const ssize_t k = read(int(fd), &b, 1);
        if (k == 1) return 1;
        if (k < 0 && errno == EINTR) continue;
        return k == 0 ? 0 : -1;
    }
}

int fifo_size(sa2l::Fd fd) { return fcntl(int(fd), F_GETPIPE_SZ); }

}  // namespace

// The relay's pace, as the comment at the top says. Returns 0 when it holds.
int pace_case(const std::string& dir) {
    std::string why;
    Ends e;
    if (!e.make(dir, "pace", &why)) { std::printf("RELAY_PACE_FAIL %s\n", why.c_str()); return 1; }
    constexpr int kFrames = 40;
    const Stream sm = make_frames(kFrames, 100u << 10);
    std::atomic<bool> alive{true};
    std::atomic<int> presented{0};
    sa2l::Relay relay(e.relay_in, e.video_w, 1, 64u << 20, e.pace_w);
    e.video_w = e.pace_w = sa2l::kNoFd;                  // the relay's now
    std::thread relay_thread([&] { relay.run(alive); });
    std::thread writer([&] {
        const int fd = open(e.fifo.c_str(), O_WRONLY);
        if (fd < 0) return;
        bool ok = sa2l::write_all(fd, sm.head.data(), sm.head.size());
        for (int f = 0; f < kFrames && ok; f++) {
            ok = sa2l::write_all(fd, sm.frames[f].data(), sm.frames[f].size());
            if (take_credit(e.pace_r) < 0) ok = false;
            presented.store(f + 1);                                       // Present returns
            std::this_thread::sleep_for(std::chrono::milliseconds(3));   // the game's next frame
        }
        close(fd);
    });
    sa2l::FrameParser seen;
    std::vector<uint8_t> got, buf(64u << 10);
    int most_ahead = 0, at_frame = 0;
    for (;;) {
        const long n = sa2l::read_wait(e.video_r, buf.data(), buf.size(), 5000);
        if (n == -1) continue;
        if (n <= 0) break;
        const size_t before = seen.frame_ends().size();
        seen.feed(buf.data(), size_t(n));
        got.insert(got.end(), buf.begin(), buf.begin() + n);
        const int consumed = int(seen.frame_ends().size());
        const int ahead = presented.load() - consumed;
        if (ahead > most_ahead) { most_ahead = ahead; at_frame = consumed; }
        if (seen.frame_ends().size() > before) std::this_thread::sleep_for(std::chrono::milliseconds(15));
    }
    writer.join();
    alive = false;
    relay_thread.join();
    const sa2l::RelayStats st = relay.stats();
    if (got != sm.all) {
        std::printf("RELAY_PACE_FAIL the reader got %zu bytes, not the %zu written in order\n", got.size(), sm.all.size());
        return 1;
    }
    if (int(seen.frame_ends().size()) != kFrames || st.frames_in != uint64_t(kFrames)) {
        std::printf("RELAY_PACE_FAIL %zu frames read, %llu relayed, of %d\n", seen.frame_ends().size(),
                    (unsigned long long)st.frames_in, kFrames);
        return 1;
    }
    // The writer returns from Present K once frame K - 1 is handed on: at most two frames ahead
    // of the reader, the second being in the player's pipe.
    if (most_ahead > 2) {
        std::printf("RELAY_PACE_FAIL the writer returned from Present %d frames ahead of the reader (at its frame %d)\n",
                    most_ahead, at_frame);
        return 1;
    }
    if (st.credits != uint64_t(kFrames) + 1 || st.credits_dropped != 0 || st.nudges != 0) {
        std::printf("RELAY_PACE_FAIL %llu pace bytes given (%llu refused), %llu nudges; %d + 1 given and none "
                    "refused or nudged expected\n", (unsigned long long)st.credits,
                    (unsigned long long)st.credits_dropped, (unsigned long long)st.nudges, kFrames);
        return 1;
    }
    std::printf("RELAY_PACE_OK %d frames of %u bytes; the writer at most %d frames ahead; %llu holds; %llu pace bytes\n",
                kFrames, unsigned(sm.frames[0].size()), most_ahead, (unsigned long long)st.holds,
                (unsigned long long)st.credits);
    return 0;
}

// The FIFO's size while a writer that takes no pace bytes waits on it. Returns 0 when it holds.
int no_resize_case(const std::string& dir) {
    std::string why;
    Ends e;
    if (!e.make(dir, "noresize", &why)) { std::printf("RELAY_NO_RESIZE_FAIL %s\n", why.c_str()); return 1; }
    const int size0 = fifo_size(e.relay_in);
    constexpr int kFrames = 12;
    const Stream sm = make_frames(kFrames, 200u << 10);         // 600 KB frames: two fill the FIFO
    std::atomic<bool> alive{true}, stop{false};
    std::atomic<int> in_write{0}, waits{0};
    sa2l::Relay relay(e.relay_in, e.video_w, 1, 64u << 20, e.pace_w);
    e.video_w = e.pace_w = sa2l::kNoFd;
    std::vector<int> sizes;
    int changes = 0;
    std::thread sampler([&] {
        int last = size0;
        while (!stop.load()) {
            const int z = fifo_size(e.relay_in);
            if (z != last) { changes++; sizes.push_back(z); last = z; }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    });
    std::thread relay_thread([&] { relay.run(alive); });
    std::thread writer([&] {
        const int fd = open(e.fifo.c_str(), O_WRONLY);
        if (fd < 0) return;
        bool ok = sa2l::write_all(fd, sm.head.data(), sm.head.size());
        for (int f = 0; f < kFrames && ok; f++) {
            in_write.store(1);
            const auto t = std::chrono::steady_clock::now();
            ok = sa2l::write_all(fd, sm.frames[f].data(), sm.frames[f].size());
            if (std::chrono::steady_clock::now() - t > std::chrono::milliseconds(10)) waits++;
            in_write.store(0);
        }
        close(fd);
    });
    std::vector<uint8_t> got, buf(64u << 10);
    sa2l::FrameParser seen;
    for (;;) {
        const long n = sa2l::read_wait(e.video_r, buf.data(), buf.size(), 5000);
        if (n == -1) continue;
        if (n <= 0) break;
        const size_t before = seen.frame_ends().size();
        seen.feed(buf.data(), size_t(n));
        got.insert(got.end(), buf.begin(), buf.begin() + n);
        if (seen.frame_ends().size() > before) std::this_thread::sleep_for(std::chrono::milliseconds(40));
    }
    writer.join();
    alive = false;
    relay_thread.join();
    stop = true;
    sampler.join();
    const sa2l::RelayStats st = relay.stats();
    if (got != sm.all) {
        std::printf("RELAY_NO_RESIZE_FAIL the reader got %zu bytes, not the %zu written in order\n", got.size(), sm.all.size());
        return 1;
    }
    if (waits.load() < 3) {
        std::printf("RELAY_NO_RESIZE_FAIL the writer waited on its FIFO in %d writes; the case needs it to wait\n",
                    waits.load());
        return 1;
    }
    if (changes) {
        std::printf("RELAY_NO_RESIZE_FAIL the FIFO's size changed %d times from %d (to %d first) while the writer wrote\n",
                    changes, size0, sizes[0]);
        return 1;
    }
    std::printf("RELAY_NO_RESIZE_OK %d frames of %u bytes; the writer waited on its full FIFO in %d writes; the FIFO "
                "kept %d bytes; %llu holds; the writer took none of the %llu pace bytes\n", kFrames,
                unsigned(sm.frames[0].size()), waits.load(), size0, (unsigned long long)st.holds,
                (unsigned long long)st.credits);
    return 0;
}

// The stall watch: a writer that stops part way through frame 3 and goes on at the second nudge.
// Returns 0 when it holds.
int stall_case(const std::string& dir) {
    std::string why;
    Ends e;
    if (!e.make(dir, "stall", &why)) { std::printf("RELAY_STALL_FAIL %s\n", why.c_str()); return 1; }
    const Stream sm = make_frames(4, 10u << 10);
    std::atomic<bool> alive{true};
    std::mutex mu;
    std::condition_variable cv;
    bool nudged = false;
    std::vector<std::string> notes;
    std::vector<std::chrono::steady_clock::time_point> nudges;
    std::chrono::steady_clock::time_point stopped_at;
    sa2l::Relay relay(e.relay_in, e.video_w, 1, 64u << 20, e.pace_w);
    e.video_w = e.pace_w = sa2l::kNoFd;
    relay.nudge = [&] {
        std::lock_guard<std::mutex> lk(mu);
        nudges.push_back(std::chrono::steady_clock::now());
        nudged = nudges.size() >= 2;
        cv.notify_all();
        return true;
    };
    relay.note = [&](const std::string& line) {
        std::lock_guard<std::mutex> lk(mu);
        notes.push_back(line);
    };
    std::thread relay_thread([&] { relay.run(alive); });
    std::thread writer([&] {
        const int fd = open(e.fifo.c_str(), O_WRONLY);
        if (fd < 0) return;
        bool ok = sa2l::write_all(fd, sm.head.data(), sm.head.size());
        for (int f = 0; f < 4 && ok; f++) {
            const size_t cut = f == 2 ? 5000 : sm.frames[f].size();
            ok = sa2l::write_all(fd, sm.frames[f].data(), cut);
            if (f == 2) {
                std::unique_lock<std::mutex> lk(mu);
                stopped_at = std::chrono::steady_clock::now();
                cv.wait_for(lk, std::chrono::seconds(10), [&] { return nudged; });
                lk.unlock();
                ok = ok && sa2l::write_all(fd, sm.frames[f].data() + cut, sm.frames[f].size() - cut);
            }
            if (ok && take_credit(e.pace_r) < 0) ok = false;
        }
        close(fd);
    });
    std::vector<uint8_t> got, buf(64u << 10);
    for (;;) {
        const long n = sa2l::read_wait(e.video_r, buf.data(), buf.size(), 15000);
        if (n == -1) continue;
        if (n <= 0) break;
        got.insert(got.end(), buf.begin(), buf.begin() + n);
    }
    writer.join();
    alive = false;
    relay_thread.join();
    const sa2l::RelayStats st = relay.stats();
    if (got != sm.all) {
        std::printf("RELAY_STALL_FAIL the reader got %zu bytes, not the %zu written in order\n", got.size(), sm.all.size());
        return 1;
    }
    if (nudges.size() != 2 || st.nudges != 2 || notes.size() != 2) {
        std::printf("RELAY_STALL_FAIL %zu nudges, %llu counted, %zu lines noted; two of each expected\n",
                    nudges.size(), (unsigned long long)st.nudges, notes.size());
        return 1;
    }
    const double after = std::chrono::duration<double>(nudges[0] - stopped_at).count();
    const double again = std::chrono::duration<double>(nudges[1] - nudges[0]).count();
    const std::string want = "relay: no byte from the game for 1.";
    const std::string tail = " with 5000 bytes of frame 3 read (FIFO 1048576 bytes, 2 frames in, ";
    if (notes[0].compare(0, want.size(), want) != 0 || notes[0].find(tail) == std::string::npos ||
        notes[0].find("nudge 1: signalled the game") == std::string::npos) {
        std::printf("RELAY_STALL_FAIL the line is '%s'\n", notes[0].c_str());
        return 1;
    }
    if (after < 0.9 || after > 2.0 || again < 1.8 || again > 3.0 ||
        notes[1].find("nudge 2: signalled the game") == std::string::npos) {
        std::printf("RELAY_STALL_FAIL nudged %.2f s after the writer stopped and %.2f s after that; about 1 s and "
                    "2 s expected; the second line is '%s'\n", after, again, notes[1].c_str());
        return 1;
    }
    std::printf("RELAY_STALL_OK nudged %.2f s after the writer stopped and again %.2f s later: %s\n", after, again,
                notes[0].c_str());
    return 0;
}

// A hold that the writer ends between two of the reader's looks: the player reads nothing until
// the reader's first wait of the hold on frame 1 has timed out; then, before the reader looks
// again (on_hold_wait), it reads, and the writer hands frame 1 on. The game then stops part way
// through frame 2. The reader is not holding afterwards, so the stall watch nudges about a second
// later. Returns 0 when it holds.
int hold_end_case(const std::string& dir) {
    std::string why;
    Ends e;
    if (!e.make(dir, "holdend", &why)) { std::printf("RELAY_HOLD_END_FAIL %s\n", why.c_str()); return 1; }
    const Stream sm = make_frames(3, 100u << 10);            // 300 KB frames: more than the player's pipe
    std::atomic<bool> alive{true};
    std::mutex mu;
    std::condition_variable cv;
    bool play = false, between = false, nudged = false;
    int hook_calls = 0;
    uint64_t out_after_hook = 0;
    sa2l::RelayStats at_nudge;
    std::vector<std::chrono::steady_clock::time_point> nudges;
    std::chrono::steady_clock::time_point stopped_at;
    sa2l::Relay relay(e.relay_in, e.video_w, 1, 64u << 20, e.pace_w);
    e.video_w = e.pace_w = sa2l::kNoFd;
    relay.on_hold_wait = [&] {
        std::unique_lock<std::mutex> lk(mu);
        if (hook_calls++) return;
        play = true;                                      // the player reads from now on
        cv.notify_all();
        lk.unlock();
        const auto until = std::chrono::steady_clock::now() + std::chrono::seconds(3);
        while (relay.frames_out() < 1 && std::chrono::steady_clock::now() < until)
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        lk.lock();
        out_after_hook = relay.frames_out();
        between = true;
        cv.notify_all();
    };
    relay.nudge = [&] {
        const sa2l::RelayStats s = relay.stats();
        std::lock_guard<std::mutex> lk(mu);
        if (nudges.empty()) at_nudge = s;
        nudges.push_back(std::chrono::steady_clock::now());
        nudged = true;
        cv.notify_all();
        return true;
    };
    std::thread relay_thread([&] { relay.run(alive); });
    std::thread writer([&] {
        const int fd = open(e.fifo.c_str(), O_WRONLY);
        if (fd < 0) return;
        bool ok = sa2l::write_all(fd, sm.head.data(), sm.head.size()) &&
                  sa2l::write_all(fd, sm.frames[0].data(), sm.frames[0].size()) && take_credit(e.pace_r) == 1;
        {
            std::unique_lock<std::mutex> lk(mu);
            cv.wait_for(lk, std::chrono::seconds(5), [&] { return between; });
        }
        const size_t cut = 5000;
        ok = ok && sa2l::write_all(fd, sm.frames[1].data(), cut);
        {
            std::unique_lock<std::mutex> lk(mu);
            stopped_at = std::chrono::steady_clock::now();
            cv.wait_for(lk, std::chrono::seconds(5), [&] { return nudged; });
        }
        ok = ok && sa2l::write_all(fd, sm.frames[1].data() + cut, sm.frames[1].size() - cut) &&
             take_credit(e.pace_r) == 1 && sa2l::write_all(fd, sm.frames[2].data(), sm.frames[2].size());
        close(fd);
    });
    {
        std::unique_lock<std::mutex> lk(mu);
        cv.wait_for(lk, std::chrono::seconds(5), [&] { return play; });
    }
    std::vector<uint8_t> got, buf(64u << 10);
    for (;;) {
        const long n = sa2l::read_wait(e.video_r, buf.data(), buf.size(), 15000);
        if (n == -1) continue;
        if (n <= 0) break;
        got.insert(got.end(), buf.begin(), buf.begin() + n);
    }
    writer.join();
    alive = false;
    relay_thread.join();
    const sa2l::RelayStats st = relay.stats();
    if (got != sm.all) {
        std::printf("RELAY_HOLD_END_FAIL the reader got %zu bytes, not the %zu written in order\n", got.size(),
                    sm.all.size());
        return 1;
    }
    if (hook_calls < 1 || out_after_hook != 1) {
        std::printf("RELAY_HOLD_END_FAIL the hold's wait timed out %d times, frames out %llu between the looks; "
                    "at least once and 1 expected\n", hook_calls, (unsigned long long)out_after_hook);
        return 1;
    }
    if (nudges.size() != 1 || st.nudges != 1) {
        std::printf("RELAY_HOLD_END_FAIL %zu nudges (%llu counted) after the writer stopped in frame 2; one expected "
                    "(no nudge: the reader still counted itself holding)\n", nudges.size(),
                    (unsigned long long)st.nudges);
        return 1;
    }
    const double after = std::chrono::duration<double>(nudges[0] - stopped_at).count();
    if (after < 0.9 || after > 2.0 || at_nudge.held_now || at_nudge.quiet_s < 0.9 || at_nudge.partial_bytes != 5000) {
        std::printf("RELAY_HOLD_END_FAIL nudged %.2f s after the writer stopped, holding %d, quiet %.2f s, %llu "
                    "partial bytes; about 1 s, not holding, quiet 0.9 s or more and 5000 expected\n", after,
                    int(at_nudge.held_now), at_nudge.quiet_s, (unsigned long long)at_nudge.partial_bytes);
        return 1;
    }
    std::printf("RELAY_HOLD_END_OK the writer handed frame 1 on between two looks at the hold; nudged %.2f s after "
                "the writer stopped in frame 2, not holding, quiet %.2f s; %llu holds\n", after, at_nudge.quiet_s,
                (unsigned long long)st.holds);
    return 0;
}

// The player gone: its pipe closed after three frames. Returns 0 when it holds.
int gone_case(const std::string& dir) {
    std::string why;
    Ends e;
    if (!e.make(dir, "gone", &why)) { std::printf("RELAY_GONE_FAIL %s\n", why.c_str()); return 1; }
    constexpr int kFrames = 12;
    const Stream sm = make_frames(kFrames, 50u << 10);
    std::atomic<bool> alive{true};
    std::atomic<int> eof_at{-1};
    sa2l::Relay relay(e.relay_in, e.video_w, 1, 64u << 20, e.pace_w);
    e.video_w = e.pace_w = sa2l::kNoFd;
    std::thread relay_thread([&] { relay.run(alive); });
    std::thread writer([&] {
        const int fd = open(e.fifo.c_str(), O_WRONLY);
        if (fd < 0) return;
        bool ok = sa2l::write_all(fd, sm.head.data(), sm.head.size());
        bool paced = true;
        for (int f = 0; f < kFrames && ok; f++) {
            ok = sa2l::write_all(fd, sm.frames[f].data(), sm.frames[f].size());
            if (ok && paced) {
                const int c = take_credit(e.pace_r);
                if (c == 0) { paced = false; eof_at = f + 1; }
                if (c < 0) ok = false;
            }
        }
        close(fd);
    });
    sa2l::FrameParser seen;
    std::vector<uint8_t> buf(64u << 10);
    while (seen.frame_ends().size() < 3) {
        const long n = sa2l::read_wait(e.video_r, buf.data(), buf.size(), 5000);
        if (n == -1) continue;
        if (n <= 0) break;
        seen.feed(buf.data(), size_t(n));
    }
    sa2l::close_fd(e.video_r);                           // the player goes
    writer.join();
    alive = false;
    relay_thread.join();
    const sa2l::RelayStats st = relay.stats();
    if (!st.player_gone || eof_at.load() < 3 || st.frames_in != uint64_t(kFrames) || !st.ended_by_eof) {
        std::printf("RELAY_GONE_FAIL player gone %d, the pace pipe's end of file at frame %d, %llu frames in of %d, "
                    "ended by end of file %d\n", int(st.player_gone), eof_at.load(), (unsigned long long)st.frames_in,
                    kFrames, int(st.ended_by_eof));
        return 1;
    }
    std::printf("RELAY_GONE_OK the writer read the pace pipe's end of file at frame %d and wrote the rest; %llu of "
                "%d frames relayed, %llu bytes dropped\n", eof_at.load(), (unsigned long long)st.frames_in, kFrames,
                (unsigned long long)st.bytes_dropped);
    return 0;
}
#endif

int main(int argc, char** argv) {
    std::mt19937 rng(20261012);
    int cases = 0;
    std::string why;
    for (int t = 0; t < 400; t++) {
        const bool bad_size = t % 7 == 3, bad_magic = t % 50 == 17;
        const std::vector<uint8_t> s = make_stream(rng, 1 + int(rng() % 300), bad_size, bad_magic);
        const size_t fixed[] = {1, 2, 3, 5, 11, 12, 13, 16, 17, 64, 4093, 65536, 1u << 20};
        for (size_t f : fixed) {
            cases++;
            if (!run(s, [&] { return f; }, &why)) {
                std::printf("RELAY_PARSER_FAIL stream %d, pieces of %zu: %s\n", t, f, why.c_str());
                return 1;
            }
        }
        for (int k = 0; k < 4; k++) {
            cases++;
            const size_t top = size_t(1) << (1 + rng() % 18);
            if (!run(s, [&] { return size_t(rng() % top); }, &why)) {
                std::printf("RELAY_PARSER_FAIL stream %d, random pieces under %zu: %s\n", t, top, why.c_str());
                return 1;
            }
        }
        if (!bad_magic) {
            // Each record header split at each of its 12 bytes once: the first piece ends there.
            sa2l::FrameParser a0;
            a0.feed(s.data(), s.size());
            uint64_t off = (a0.header_bytes() + 3u) & ~3u;
            for (int r = 0; r < 3 && off + 12 <= s.size(); r++) {
                for (uint64_t cut = off; cut < off + 12; cut++) {
                    cases++;
                    bool first = true;
                    const size_t rest = s.size();
                    if (!run(s, [&] { if (first) { first = false; return size_t(cut); } return rest; }, &why)) {
                        std::printf("RELAY_PARSER_FAIL stream %d, cut at %llu: %s\n", t, (unsigned long long)cut,
                                    why.c_str());
                        return 1;
                    }
                }
                off += OldParser::le32(s.data() + off + 4);
            }
        }
    }
    std::printf("RELAY_PARSER_OK %d cases\n", cases);
#ifndef _WIN32
    const char* tmp = std::getenv("TMPDIR");
    const std::string dir = argc > 1 ? argv[1] : (tmp && *tmp ? tmp : "/tmp");
    if (pace_case(dir) || no_resize_case(dir) || stall_case(dir) || hold_end_case(dir) || gone_case(dir)) return 1;
#else
    (void)argc;
    (void)argv;
#endif
    return 0;
}
