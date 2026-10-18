// test_stream.cpp: the stream reader's refusals and framing checks, its header
// JSON parser, SHA-256 (FIPS 180-4 examples; the x86 SHA-extension path
// against the portable one), and its live input: a stream through a named pipe
// whose writer pauses is read whole, the idle hook called while the reader
// waits, and a regular file never calls it. Exit 0 when every check holds.
//
//   test_stream [DIR]     DIR holds the small crafted streams (default $TMPDIR or /tmp)
#include "sha256.h"
#include "stream.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <zstd.h>
#include <time.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

static int g_fail = 0, g_pass = 0;
#define CHECK(cond)                                                               \
    do {                                                                          \
        if (cond) g_pass++;                                                       \
        else { g_fail++; std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
    } while (0)

static std::string sha_of(const std::string& s) {
    Sha256 h;
    h.update(s.data(), s.size());
    return h.hex();
}

// A stream writer small enough to craft each case by hand.
struct W {
    std::vector<uint8_t> b;
    uint32_t seq = 0;
    void u32(uint32_t v) { for (int i = 0; i < 4; i++) b.push_back(uint8_t(v >> (8 * i))); }
    void u16(uint16_t v) { b.push_back(uint8_t(v)); b.push_back(uint8_t(v >> 8)); }
    void header(uint32_t version, std::string json) {
        while ((16 + json.size()) & 3) json += ' ';
        b.insert(b.end(), SA2S_MAGIC, SA2S_MAGIC + 8);
        u32(version);
        u32(16 + uint32_t(json.size()));
        b.insert(b.end(), json.begin(), json.end());
    }
    void record(uint32_t kind, const std::vector<uint8_t>& payload) {
        u32(kind);
        u32(12 + uint32_t(payload.size()));
        u32(seq++);
        b.insert(b.end(), payload.begin(), payload.end());
    }
    static std::vector<uint8_t> call(uint16_t type, uint16_t slot, std::vector<uint32_t> args, uint32_t extra = 0) {
        W p;
        p.u16(type); p.u16(slot); p.u32(0x03200010); p.u32(0);
        p.u32(uint32_t(args.size()));
        for (uint32_t a : args) p.u32(a);
        p.u32(0);           // eax
        p.u32(0);           // no blobs
        for (uint32_t i = 0; i < extra; i++) p.u32(0);
        return p.b;
    }
    bool save(const std::string& path) const {
        FILE* f = std::fopen(path.c_str(), "wb");
        if (!f) return false;
        const bool ok = std::fwrite(b.data(), 1, b.size(), f) == b.size();
        return std::fclose(f) == 0 && ok;
    }
};

static const char* kJson =
    "{\"schema\": \"sa2-d3d9-stream/v1\", \"writer\": \"test\", \"meta\": {\"k\": [1, 2.5e1, true, null, \"\\u00e9\\ud83d\\ude00\"]}, "
    "\"methods\": [[1, 17, \"Present\", 4], [1, 41, \"BeginScene\", 0]]}";

int main(int argc, char** argv) {
    const char* tmp = getenv("TMPDIR");
    const std::string dir = argc > 1 ? argv[1] : (tmp && *tmp ? tmp : "/tmp");

    // ---------------------------------------------------------------- SHA-256
    std::printf("sha256: SHA extensions %s\n", Sha256::hardware() ? "in use" : "absent");
    for (int portable = 0; portable < 2; portable++) {
        Sha256::force_portable(portable != 0);
        CHECK(sha_of("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        CHECK(sha_of("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        CHECK(sha_of("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq") ==
              "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
        CHECK(sha_of(std::string(1000000, 'a')) == "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
    }
    {
        // The two paths agree on every length from 0 to 300 and on 1 MB, fed in random pieces.
        std::mt19937 rng(12345);
        std::vector<uint8_t> data(1 << 20);
        for (auto& x : data) x = uint8_t(rng());
        bool same = true;
        for (size_t n = 0; n <= 300 && same; n++) {
            Sha256::force_portable(true);
            Sha256 a; a.update(data.data(), n);
            const std::string pa = a.hex();
            Sha256::force_portable(false);
            Sha256 b;
            for (size_t off = 0; off < n;) { const size_t k = std::min<size_t>(n - off, 1 + rng() % 97); b.update(data.data() + off, k); off += k; }
            same = pa == b.hex();
        }
        CHECK(same);
        Sha256::force_portable(true);
        Sha256 a; a.update(data.data(), data.size());
        Sha256::force_portable(false);
        Sha256 b; b.update(data.data(), data.size());
        CHECK(a.hex() == b.hex());
    }

    // ---------------------------------------------------------------- header JSON
    {
        Sa2Header h;
        std::string err;
        CHECK(sa2_parse_header_json(kJson, &h, &err));
        CHECK(h.schema == "sa2-d3d9-stream/v1" && h.writer == "test");
        CHECK(h.method_argc.size() == 2 && h.method_argc.at(1u << 16 | 17) == 4 && h.method_argc.at(1u << 16 | 41) == 0);
        CHECK(!sa2_parse_header_json("{\"methods\": []}", &h, &err));                          // no schema
        CHECK(!sa2_parse_header_json("{\"schema\": \"x\"}", &h, &err));                        // no methods
        CHECK(!sa2_parse_header_json("{\"schema\": \"x\", \"methods\": [[1, 2, \"a\"]]}", &h, &err));   // short row
        CHECK(!sa2_parse_header_json("{\"schema\": \"x\", \"methods\": [[1, 2, \"a\", 1], [1, 2, \"b\", 1]]}", &h, &err));
        CHECK(!sa2_parse_header_json("{\"schema\": \"x\", \"methods\": [[1, 2, \"a\", 1.5]]}", &h, &err));
        CHECK(!sa2_parse_header_json("{\"schema\": \"x\", \"methods\": []} x", &h, &err));    // trailing text
        CHECK(!sa2_parse_header_json("{\"schema\": \"x\\q\", \"methods\": []}", &h, &err));  // bad escape
        CHECK(!sa2_parse_header_json("{\"schema\": \"\\ud83d\", \"methods\": []}", &h, &err));  // lone surrogate
        CHECK(sa2_parse_header_json("{\"schema\": \"x\", \"methods\": []}", &h, &err) && h.method_argc.empty());
    }
    {
        // The D3DX "entries" table: read when present, each row held to its shape.
        Sa2Header h;
        std::string err;
        const std::string base = "{\"schema\": \"x\", \"methods\": [], \"entries\": ";
        CHECK(sa2_parse_header_json(base + "[[12, \"Direct3DCreate9\", 0, \"object_eax\"], "
                                    "[3, \"D3DXFilterTexture\", 4, \"in 1 1024\"]]}", &h, &err));
        CHECK(h.has_entries && h.entries.size() == 2 && h.entries.at(12).name == "Direct3DCreate9" &&
              h.entries.at(12).argc == 0 && h.entries.at(12).ops == "object_eax" && h.entries.at(3).argc == 4);
        CHECK(sa2_parse_header_json("{\"schema\": \"x\", \"methods\": []}", &h, &err) && !h.has_entries &&
              h.entries.empty());
        CHECK(sa2_parse_header_json(base + "[]}", &h, &err) && h.has_entries && h.entries.empty());
        CHECK(!sa2_parse_header_json(base + "{}}", &h, &err) && err.find("not a table") != std::string::npos);
        CHECK(!sa2_parse_header_json(base + "[[12, \"Direct3DCreate9\", 0]]}", &h, &err));      // short row
        CHECK(!sa2_parse_header_json(base + "[[12, 7, 0, \"-\"]]}", &h, &err));                  // name not a string
        CHECK(!sa2_parse_header_json(base + "[[12, \"a\", 0, 5]]}", &h, &err));                  // ops not a string
        CHECK(!sa2_parse_header_json(base + "[[-1, \"a\", 1, \"-\"]]}", &h, &err));             // not an entry number
        CHECK(!sa2_parse_header_json(base + "[[1, \"a\", 1, \"-\"], [1, \"b\", 1, \"-\"]]}", &h, &err) &&
              err.find("twice") != std::string::npos);
    }

    // ---------------------------------------------------------------- reader
    auto open = [&](const W& w, const char* name, std::string* err) {
        const std::string path = dir + "/test_stream-" + name + ".sa2d3d9";
        if (!w.save(path)) { std::printf("cannot write %s\n", path.c_str()); std::exit(2); }
        auto* rd = new Sa2StreamReader;
        const int rc = rd->open(path, err);
        return std::make_pair(rc, rd);
    };
    std::string err;
    {
        W w;
        w.header(1, kJson);
        w.record(SA2S_CALL, W::call(1, 41, {}));
        w.record(SA2S_CALL, W::call(1, 17, {0, 0, 0, 0}));
        W f; f.u32(16666); f.u32(0); f.u32(1); f.u32(3);
        w.record(SA2S_FRAME, f.b);
        w.record(SA2S_END, {});
        auto [rc, rd] = open(w, "good", &err);
        CHECK(rc == Sa2StreamReader::OPEN_OK);
        CHECK(rd->method_argc(1, 17) == 4 && rd->method_argc(1, 99) == -1);
        Sa2Record r;
        int n = 0, got;
        while ((got = rd->next(&r, &err)) == 1) n++;
        CHECK(got == 0 && n == 4 && r.kind == SA2S_END);
        CHECK(rd->whole_file_read());
        const std::string h = rd->file_sha256();
        CHECK(h == sha256_file(dir + "/test_stream-good.sa2d3d9"));
        CHECK(r.arg(0) == 0 && r.arg_missing);         // END carries no argument: asking is counted
        CHECK(!rd->header().has_entries && rd->entry_row(12) == nullptr);
        delete rd;
    }
    {
        // A reader that stops after one record still pins the whole file.
        W w;
        w.header(1, kJson);
        for (int i = 0; i < 2000; i++) w.record(SA2S_CALL, W::call(1, 41, {}));
        w.record(SA2S_END, {});
        auto [rc, rd] = open(w, "stop", &err);
        CHECK(rc == Sa2StreamReader::OPEN_OK);
        Sa2Record r;
        CHECK(rd->next(&r, &err) == 1 && !rd->whole_file_read());
        const std::string h = rd->hash_rest();
        CHECK(!h.empty() && h == sha256_file(dir + "/test_stream-stop.sa2d3d9"));
        CHECK(rd->whole_file_read() && rd->file_bytes_read() == w.b.size());
        delete rd;
    }
    {
        W w;
        w.header(2, kJson);
        auto [rc, rd] = open(w, "v2", &err);
        CHECK(rc == Sa2StreamReader::OPEN_MALFORMED && err.find("version 2") != std::string::npos);
        delete rd;
    }
    {
        W w;
        w.header(1, "{\"schema\": \"sa2-d3d9-stream/v9\", \"methods\": []}");
        auto [rc, rd] = open(w, "schema", &err);
        CHECK(rc == Sa2StreamReader::OPEN_MALFORMED && err.find("schema") != std::string::npos);
        delete rd;
    }
    {
        W w;
        w.header(1, "{\"schema\": \"sa2-d3d9-stream/v1\"}");
        auto [rc, rd] = open(w, "nomethods", &err);
        CHECK(rc == Sa2StreamReader::OPEN_MALFORMED && err.find("methods") != std::string::npos);
        delete rd;
    }
    {
        W w;
        w.b.assign({'N', 'O', 'T', 'S', 'A', '2', 'S', '!', 1, 0, 0, 0, 16, 0, 0, 0});
        auto [rc, rd] = open(w, "magic", &err);
        CHECK(rc == Sa2StreamReader::OPEN_MALFORMED);
        delete rd;
    }
    {
        std::string e2;
        Sa2StreamReader rd;
        CHECK(rd.open(dir + "/test_stream-no-such-file", &e2) == Sa2StreamReader::OPEN_IO);
    }
    // Records that do not fill themselves exactly, or are cut short.
    auto one_bad = [&](const char* name, uint32_t kind, const std::vector<uint8_t>& payload, bool cut) {
        W w;
        w.header(1, kJson);
        w.record(kind, payload);
        if (cut) w.b.resize(w.b.size() - 4);
        auto [rc, rd] = open(w, name, &err);
        Sa2Record r;
        std::string e;
        const int got = rc == Sa2StreamReader::OPEN_OK ? rd->next(&r, &e) : 99;
        delete rd;
        return got;
    };
    CHECK(one_bad("trailing", SA2S_CALL, W::call(1, 41, {}, 1), false) == -1);   // 4 bytes after the last blob
    CHECK(one_bad("frame15", SA2S_FRAME, std::vector<uint8_t>(12, 0), false) == -1);
    CHECK(one_bad("endpayload", SA2S_END, std::vector<uint8_t>(4, 0), false) == -1);
    CHECK(one_bad("cut", SA2S_CALL, W::call(1, 17, {0, 0, 0, 0}), true) == -1);
    CHECK(one_bad("unknownkind", 9, std::vector<uint8_t>(8, 0), false) == 1);     // the caller counts it

    // ---------------------------------------------------------------- live input
    // The same bytes through a named pipe, the writer pausing 150 ms after the header and
    // after the first record, as the game does between frames: the reader waits in
    // poll(2) slices, calls the idle hook between them, and reads every record; the
    // stream's sha256 is the bytes'. From a regular file the hook is never called.
    {
        W w;
        w.header(1, kJson);
        const size_t head = w.b.size();
        w.record(SA2S_CALL, W::call(1, 41, {}));
        const size_t first = w.b.size();
        w.record(SA2S_CALL, W::call(1, 17, {0, 0, 0, 0}));
        W f; f.u32(16666); f.u32(0); f.u32(1); f.u32(3);
        w.record(SA2S_FRAME, f.b);
        w.record(SA2S_END, {});
        const std::string fifo = dir + "/test_stream-live.fifo";
        unlink(fifo.c_str());
        CHECK(mkfifo(fifo.c_str(), 0600) == 0);
        const pid_t child = fork();
        if (child == 0) {                  // the writer: its own process, as the game is
            const int fd = ::open(fifo.c_str(), O_WRONLY);
            const timespec pause{0, 150000000};
            bool ok = fd >= 0;
            size_t at = 0;
            for (size_t upto : {head, first, w.b.size()}) {
                while (ok && at < upto) {
                    const ssize_t n = write(fd, w.b.data() + at, upto - at);
                    ok = n > 0;
                    at += ok ? size_t(n) : 0;
                }
                if (upto != w.b.size()) nanosleep(&pause, nullptr);
            }
            _exit(ok && close(fd) == 0 ? 0 : 1);
        }
        Sa2StreamReader rd;
        std::string e;
        CHECK(rd.open(fifo, &e) == Sa2StreamReader::OPEN_OK);
        int idles = 0;
        rd.set_idle([](void* a) { ++*static_cast<int*>(a); }, &idles, 20);
        Sa2Record r;
        int n = 0, got;
        while ((got = rd.next(&r, &e)) == 1) n++;
        int status = -1;
        waitpid(child, &status, 0);
        CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        CHECK(rd.live() && got == 0 && n == 4 && r.kind == SA2S_END);
        CHECK(idles >= 3 && rd.idle_slices() == uint64_t(idles) && rd.idle_waits() >= 1 && rd.idle_s() > 0.1);
        CHECK(rd.file_sha256() == sha_of(std::string(w.b.begin(), w.b.end())));
        unlink(fifo.c_str());

        auto [rc, fr] = open(w, "notlive", &err);
        CHECK(rc == Sa2StreamReader::OPEN_OK);
        int file_idles = 0;
        fr->set_idle([](void* a) { ++*static_cast<int*>(a); }, &file_idles, 20);
        n = 0;
        while ((got = fr->next(&r, &err)) == 1) n++;
        CHECK(!fr->live() && got == 0 && n == 4 && file_idles == 0 && fr->idle_waits() == 0);
        delete fr;
    }

    // Two records arrive together before a pause. Bytes already buffered by stdio
    // must be consumed without waiting for the writer's next chunk.
    {
        W w;
        w.header(1, kJson);
        w.record(SA2S_CALL, W::call(1, 41, {}));
        w.record(SA2S_CALL, W::call(1, 17, {0, 0, 0, 0}));
        const size_t pair = w.b.size();
        w.record(SA2S_END, {});
        const std::string fifo = dir + "/test_stream-buffered.fifo";
        unlink(fifo.c_str());
        CHECK(mkfifo(fifo.c_str(), 0600) == 0);
        const pid_t child = fork();
        if (child == 0) {
            const int fd = ::open(fifo.c_str(), O_WRONLY);
            bool ok = fd >= 0 && write(fd, w.b.data(), pair) == ssize_t(pair);
            const timespec pause{0, 200000000};
            nanosleep(&pause, nullptr);
            ok = ok && write(fd, w.b.data() + pair, w.b.size() - pair) == ssize_t(w.b.size() - pair);
            _exit(ok && close(fd) == 0 ? 0 : 1);
        }
        Sa2StreamReader rd;
        std::string e;
        CHECK(rd.open(fifo, &e) == Sa2StreamReader::OPEN_OK);
        int idles = 0;
        rd.set_idle([](void* a) { ++*static_cast<int*>(a); }, &idles, 20);
        Sa2Record r;
        CHECK(rd.next(&r, &e) == 1 && r.kind == SA2S_CALL && r.seq == 0);
        CHECK(rd.next(&r, &e) == 1 && r.kind == SA2S_CALL && r.seq == 1);
        CHECK(idles == 0);
        CHECK(rd.next(&r, &e) == 1 && r.kind == SA2S_END);
        CHECK(rd.next(&r, &e) == 0);
        int status = -1;
        waitpid(child, &status, 0);
        CHECK(WIFEXITED(status) && WEXITSTATUS(status) == 0);
        unlink(fifo.c_str());
    }

    // A complete compressed frame can fill the decoder's output buffer exactly.
    {
        W w;
        w.header(1, kJson);
        const size_t note_length = 65536 - w.b.size() - 28;
        W note;
        note.u32(uint32_t(note_length));
        note.b.insert(note.b.end(), note_length, 'a');
        w.record(SA2S_NOTE, note.b);
        w.record(SA2S_END, {});
        CHECK(w.b.size() == 65536);
        std::vector<uint8_t> wire(ZSTD_compressBound(w.b.size()));
        const size_t size = ZSTD_compress(wire.data(), wire.size(), w.b.data(), w.b.size(), 1);
        CHECK(!ZSTD_isError(size));
        if (ZSTD_isError(size)) return 1;
        wire.resize(size);
        std::vector<uint8_t> decoded(65536);
        ZSTD_DStream* codec = ZSTD_createDStream();
        CHECK(codec && !ZSTD_isError(ZSTD_initDStream(codec)));
        if (!codec) return 1;
        ZSTD_inBuffer input{wire.data(), wire.size(), 0};
        ZSTD_outBuffer output{decoded.data(), decoded.size(), 0};
        const size_t remaining = ZSTD_decompressStream(codec, &output, &input);
        ZSTD_freeDStream(codec);
        CHECK(remaining == 0 && output.pos == 65536 && input.pos == wire.size() && decoded == w.b);
        int video[2], cancel[2];
        CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, video) == 0);
        CHECK(pipe(cancel) == 0);
        CHECK(write(video[1], wire.data(), wire.size()) == ssize_t(wire.size()));
        CHECK(close(video[1]) == 0);
        Sa2StreamReader reader;
        CHECK(reader.open_live(video[0], cancel[0], nullptr, nullptr).status == Sa2ReadStatus::Ready);
        Sa2Record record;
        CHECK(reader.next_live(&record).status == Sa2ReadStatus::Ready && record.kind == SA2S_NOTE &&
              record.seq == 0 && record.note == std::string(note_length, 'a'));
        CHECK(reader.next_live(&record).status == Sa2ReadStatus::Ready && record.kind == SA2S_END && record.seq == 1);
        CHECK(reader.next_live(&record).status == Sa2ReadStatus::End);
        CHECK(reader.whole_file_read() && reader.file_bytes_read() == wire.size());
        CHECK(reader.file_sha256() == sha_of(std::string(wire.begin(), wire.end())));
        CHECK(close(cancel[1]) == 0);
        // The same bytes with the live hashing off (a live session's player): every record and
        // byte read as before, and no sha256.
        CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, video) == 0);
        CHECK(pipe(cancel) == 0);
        CHECK(write(video[1], wire.data(), wire.size()) == ssize_t(wire.size()));
        CHECK(close(video[1]) == 0);
        Sa2StreamReader unhashed;
        unhashed.set_live_hashing(false);
        CHECK(unhashed.open_live(video[0], cancel[0], nullptr, nullptr).status == Sa2ReadStatus::Ready);
        CHECK(unhashed.next_live(&record).status == Sa2ReadStatus::Ready && record.kind == SA2S_NOTE &&
              record.note == std::string(note_length, 'a'));
        CHECK(unhashed.next_live(&record).status == Sa2ReadStatus::Ready && record.kind == SA2S_END);
        CHECK(unhashed.next_live(&record).status == Sa2ReadStatus::End);
        CHECK(unhashed.whole_file_read() && unhashed.file_bytes_read() == wire.size());
        CHECK(unhashed.file_sha256().empty());
        CHECK(close(cancel[1]) == 0);
    }
    // Closing during a quiet or partial live header leaves the writer open.
    for (size_t prefix : {size_t(0), size_t(3), size_t(12)}) {
        W w;
        w.header(1, kJson);
        int video[2], cancel[2];
        CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, video) == 0);
        CHECK(pipe(cancel) == 0);
        CHECK(write(video[1], w.b.data(), prefix) == ssize_t(prefix));
        int services = 0;
        Sa2StreamReader reader;
        const auto outcome = reader.open_live(video[0], cancel[0], [](void* opaque) {
            return ++*static_cast<int*>(opaque) >= 3 ? Sa2ServiceDecision::Cancel : Sa2ServiceDecision::Continue;
        }, &services, 1);
        CHECK(outcome.status == Sa2ReadStatus::Cancelled && services >= 3);
        struct stat writer{};
        CHECK(fstat(video[1], &writer) == 0);
        CHECK(!reader.whole_file_read() && reader.file_sha256().empty() && reader.hash_rest().empty());
        CHECK(close(video[1]) == 0 && close(cancel[1]) == 0);
    }

    // sa2_cancel_waiting (pause-unfocused's hold, which does not read the stream) looks at the
    // cancellation pipe without taking its byte: nothing while the pipe is empty; the caller's cancel
    // said each time it looks, and left for the reader, whose next read still ends Cancelled; a
    // writer gone said as a closed pipe; no descriptor, nothing.
    {
        const auto said = [](const char* s) { return std::string(s ? s : "(nothing)"); };
        W w;
        w.header(1, kJson);
        int video[2], cancel[2];
        CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, video) == 0);
        CHECK(pipe(cancel) == 0);
        CHECK(write(video[1], w.b.data(), w.b.size()) == ssize_t(w.b.size()));
        Sa2StreamReader reader;
        CHECK(reader.live_cancel_fd() == -1);
        CHECK(reader.open_live(video[0], cancel[0], nullptr, nullptr, 1).status == Sa2ReadStatus::Ready);
        CHECK(reader.live_cancel_fd() == cancel[0]);
        CHECK(said(sa2_cancel_waiting(-1)) == "(nothing)");
        CHECK(said(sa2_cancel_waiting(cancel[0])) == "(nothing)");
        const uint8_t one = 1;
        CHECK(write(cancel[1], &one, 1) == 1);
        for (int look = 0; look < 3; look++) CHECK(said(sa2_cancel_waiting(cancel[0])) == "the caller cancelled");
        Sa2Record r;
        const auto outcome = reader.next_live(&r);
        CHECK(outcome.status == Sa2ReadStatus::Cancelled && outcome.detail == "explicit cancellation wake");
        CHECK(said(sa2_cancel_waiting(cancel[0])) == "(nothing)");   // the reader took the byte
        CHECK(close(cancel[1]) == 0);
        CHECK(said(sa2_cancel_waiting(cancel[0])) == "the caller's cancellation pipe is closed");
        CHECK(close(video[1]) == 0);
    }

    // A live reader parses into storage it keeps (next_live's record swapped with the caller's):
    // a CALL with arguments and a blob, then a FRAME, a NOTE and a CALL with none, each read as
    // into a fresh record, nothing of the one before left in it; and method_argc answers the
    // header's table for its rows and -1 for any other, in the flat range and past it.
    {
        W w;
        w.header(1, kJson);
        W c;
        c.u16(1); c.u16(17); c.u32(0x03200010); c.u32(7);
        c.u32(4);
        for (uint32_t a : {1u, 2u, 3u, 4u}) c.u32(a);
        c.u32(0x80004005u);
        c.u32(1);                                   // one blob: role 5, arg 2, three bytes
        c.u16(5); c.u16(2); c.u32(3);
        c.b.insert(c.b.end(), {9, 8, 7, 0});
        w.record(SA2S_CALL, c.b);
        // A record past the reader's 64 KB batch, so it is copied rather than viewed in place,
        // and the records after it cross reads.
        W big;
        big.u16(1); big.u16(17); big.u32(0x03200010); big.u32(0);
        big.u32(0);
        big.u32(0);
        big.u32(1);
        big.u16(6); big.u16(0); big.u32(70000);
        for (uint32_t i = 0; i < 70000; i++) big.b.push_back(uint8_t(i * 7));
        w.record(SA2S_CALL, big.b);
        W f;
        f.u32(16666); f.u32(0); f.u32(1); f.u32(2); // virtual_us (u64), frames, polls
        w.record(SA2S_FRAME, f.b);
        W n;
        n.u32(2);
        n.b.insert(n.b.end(), {'h', 'i', 0, 0});
        w.record(SA2S_NOTE, n.b);
        w.record(SA2S_CALL, W::call(1, 41, {}));
        w.record(SA2S_END, {});
        int video[2], cancel[2];
        CHECK(socketpair(AF_UNIX, SOCK_STREAM, 0, video) == 0);
        CHECK(pipe(cancel) == 0);
        CHECK(write(video[1], w.b.data(), w.b.size()) == ssize_t(w.b.size()));
        CHECK(close(video[1]) == 0);
        Sa2StreamReader reader;
        reader.set_live_hashing(false);
        CHECK(reader.open_live(video[0], cancel[0], nullptr, nullptr).status == Sa2ReadStatus::Ready);
        Sa2Record r;
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::Ready && r.kind == SA2S_CALL && r.seq == 0 &&
              r.type == 1 && r.slot == 17 && r.self == 0x03200010u && r.thread == 7 &&
              r.args == std::vector<uint32_t>({1, 2, 3, 4}) && r.eax == 0x80004005u && r.blobs.size() == 1 &&
              r.blobs[0].role == 5 && r.blobs[0].arg == 2 && r.blobs[0].len == 3 &&
              std::memcmp(r.blobs[0].data, "\x09\x08\x07", 3) == 0);
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::Ready && r.kind == SA2S_CALL && r.seq == 1 &&
              r.args.empty() && r.thread == 0 && r.eax == 0 && r.blobs.size() == 1 && r.blobs[0].role == 6 &&
              r.blobs[0].len == 70000 && r.blobs[0].data[0] == 0 && r.blobs[0].data[1] == 7 &&
              r.blobs[0].data[69999] == uint8_t(69999u * 7));
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::Ready && r.kind == SA2S_FRAME && r.seq == 2 &&
              r.virtual_us == 16666 && r.frames == 1 && r.polls == 2 && r.args.empty() && r.blobs.empty() &&
              r.type == 0 && r.slot == 0 && r.self == 0 && r.thread == 0 && r.eax == 0 && r.note.empty());
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::Ready && r.kind == SA2S_NOTE && r.seq == 3 &&
              r.note == "hi" && r.virtual_us == 0 && r.frames == 0 && r.args.empty() && r.blobs.empty());
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::Ready && r.kind == SA2S_CALL && r.seq == 4 &&
              r.type == 1 && r.slot == 41 && r.args.empty() && r.blobs.empty() && r.note.empty() &&
              r.virtual_us == 0 && r.thread == 0 && !r.arg_missing);
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::Ready && r.kind == SA2S_END && r.seq == 5);
        CHECK(reader.next_live(&r).status == Sa2ReadStatus::End);
        CHECK(reader.method_argc(1, 17) == 4 && reader.method_argc(1, 41) == 0 && reader.method_argc(1, 42) == -1 &&
              reader.method_argc(0, 17) == -1 && reader.method_argc(40, 17) == -1 && reader.method_argc(1, 300) == -1);
        CHECK(close(cancel[1]) == 0);
    }

    std::printf("stream: %d checks passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
