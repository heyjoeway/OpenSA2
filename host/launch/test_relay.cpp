// test_relay.cpp: sa2l::FrameParser (sa2_relay.cpp) against the parser it replaced, which copied
// every piece into a buffer of its own: the same frame ends, record count, header length and
// problem for streams fed in pieces of every size from 1 byte up, a frame header split across
// pieces at each of its bytes, a bad record size, and a stream that does not begin with the magic.
// Then the relay's pace (POSIX): a writer that writes each 300 KB frame in one write, as the
// recorder does at Present, into a FIFO opened and sized as the launcher does (1 MB), the relay
// with lead 1 onto a pipe made as the launcher makes it (make_pipe, 1 MB), and a reader that
// takes 15 ms a frame: the writer never finishes a frame more than two frames ahead of the
// frames the reader has read, and every byte arrives in order.
//
//   c++ -std=c++17 -O2 -Ihost/launch host/launch/test_relay.cpp host/launch/sa2_relay.cpp
//       host/launch/sa2_launch_posix.cpp -lpthread -o test_relay && ./test_relay [DIR]
//
// Prints RELAY_PARSER_OK, then RELAY_PACE_OK, and exits 0 when every case holds, else names the
// first that does not. DIR holds the FIFO (default $TMPDIR or /tmp).
#include "sa2_relay.h"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <random>
#include <string>
#include <thread>
#include <vector>

#ifndef _WIN32
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
// The relay's pace, as the comment at the top says. Returns 0 when it holds.
int pace_case(const std::string& dir) {
    const std::string fifo = dir + "/test_relay_pace." + std::to_string(getpid()) + ".fifo";
    std::string why;
    unlink(fifo.c_str());
    if (!sa2l::make_fifo(fifo, &why)) { std::printf("RELAY_PACE_FAIL %s\n", why.c_str()); return 1; }
    sa2l::ignore_broken_pipes();
    sa2l::Fd relay_in = sa2l::open_fifo_reader(fifo, &why);
    sa2l::set_pipe_size(relay_in, 1u << 20);
    sa2l::Fd video_r = sa2l::kNoFd, video_w = sa2l::kNoFd;
    if (relay_in == sa2l::kNoFd || !sa2l::make_pipe(video_r, video_w, &why)) {
        std::printf("RELAY_PACE_FAIL %s\n", why.c_str());
        return 1;
    }
    constexpr int kFrames = 40;
    constexpr uint32_t kRecord = 100u << 10;           // three of these and a FRAME record a frame
    std::vector<uint8_t> head = {'S', 'A', '2', 'D', '3', 'D', '9', 'S'};
    put32(head, 2);
    put32(head, 32);
    while (head.size() < 32) head.push_back(' ');
    std::vector<std::vector<uint8_t>> frames;
    std::vector<uint8_t> all = head;
    for (int f = 0; f < kFrames; f++) {
        std::vector<uint8_t> b;
        for (int k = 0; k < 3; k++) {
            put32(b, 1);
            put32(b, kRecord);
            put32(b, uint32_t(f * 4 + k));
            for (uint32_t i = 12; i < kRecord; i++) b.push_back(uint8_t(f * 7 + k + i));
        }
        put32(b, 3);
        put32(b, 28);
        put32(b, uint32_t(f * 4 + 3));
        for (int i = 0; i < 16; i++) b.push_back(uint8_t(f));
        all.insert(all.end(), b.begin(), b.end());
        frames.push_back(std::move(b));
    }
    std::atomic<bool> alive{true};
    std::atomic<int> written{0};
    sa2l::Relay relay(relay_in, video_w, 1, 64u << 20);
    std::thread relay_thread([&] { relay.run(alive); });
    std::thread writer([&] {
        const int fd = open(fifo.c_str(), O_WRONLY);
        if (fd < 0) return;
        bool ok = sa2l::write_all(fd, head.data(), head.size());
        for (int f = 0; f < kFrames && ok; f++) {
            ok = sa2l::write_all(fd, frames[f].data(), frames[f].size());
            written.store(f + 1);
            std::this_thread::sleep_for(std::chrono::milliseconds(3));   // the game's next frame
        }
        close(fd);
    });
    sa2l::FrameParser seen;
    std::vector<uint8_t> got, buf(64u << 10);
    int most_ahead = 0, at_frame = 0;
    for (;;) {
        const long n = sa2l::read_wait(video_r, buf.data(), buf.size(), 5000);
        if (n == -1) continue;
        if (n <= 0) break;
        const size_t before = seen.frame_ends().size();
        seen.feed(buf.data(), size_t(n));
        got.insert(got.end(), buf.begin(), buf.begin() + n);
        const int consumed = int(seen.frame_ends().size());
        const int ahead = written.load() - consumed;
        if (ahead > most_ahead) { most_ahead = ahead; at_frame = consumed; }
        if (seen.frame_ends().size() > before) std::this_thread::sleep_for(std::chrono::milliseconds(15));
    }
    writer.join();
    alive = false;
    relay_thread.join();
    sa2l::close_fd(video_r);
    sa2l::close_fd(relay_in);
    unlink(fifo.c_str());
    const sa2l::RelayStats st = relay.stats();
    if (got != all) {
        std::printf("RELAY_PACE_FAIL the reader got %zu bytes, not the %zu written in order\n", got.size(), all.size());
        return 1;
    }
    if (int(seen.frame_ends().size()) != kFrames || st.frames_in != uint64_t(kFrames)) {
        std::printf("RELAY_PACE_FAIL %zu frames read, %llu relayed, of %d\n", seen.frame_ends().size(),
                    (unsigned long long)st.frames_in, kFrames);
        return 1;
    }
    // The relay holds the writer two frames ahead; on a loaded machine a shrink the kernel
    // refuses (the FIFO already holds the next frame) lets a few more through before the next
    // look. 5 keeps that slack and still fails HEAD's unpaced relay (10 ahead) by a wide margin.
    if (most_ahead > 5) {
        std::printf("RELAY_PACE_FAIL the writer finished %d frames ahead of the reader (at its frame %d)\n",
                    most_ahead, at_frame);
        return 1;
    }
    std::printf("RELAY_PACE_OK %d frames of %u bytes; the writer at most %d frames ahead; %llu holds\n", kFrames,
                unsigned(frames[0].size()), most_ahead, (unsigned long long)st.holds);
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
    if (pace_case(dir)) return 1;
#else
    (void)argc;
    (void)argv;
#endif
    return 0;
}
