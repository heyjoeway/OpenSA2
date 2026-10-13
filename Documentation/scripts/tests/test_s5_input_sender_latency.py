"""KeySender completion logs over real FIFOs, SDL headers only; no SDL library."""
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).parents[3]
HEADERS = Path(os.environ.get("SA2_SENDER_HEADERS", str(ROOT / "private/s5-tools/install/include")))
GXX = shutil.which(os.environ.get("CXX", "g++"))
HEADER = "change scancode dik down synthetic age_ns handled_s seq sent_s status".split()

DRIVER = r'''
#include "input.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

static std::vector<double> times;
static size_t clocks = 0, at_write = 0;
static bool check_prewrite = true;
static double clock_now() {
    if (clocks == times.size()) { std::fprintf(stderr, "unexpected clock\n"); std::exit(70); }
    return times[clocks++];
}
extern "C" ssize_t __real_write(int, const void*, size_t);
extern "C" ssize_t __wrap_write(int fd, const void* data, size_t size) {
    const auto* b = static_cast<const unsigned char*>(data);
    if (size == sa2_input::kRecordBytes && b[0] == 0x53 && b[1] == 0x41 && b[2] == 0x32 && b[3] == 0x4b) {
        if (check_prewrite && clocks <= at_write) { std::fprintf(stderr, "no pre-write sample\n"); std::exit(71); }
        at_write = clocks;
        std::printf("prewrite %.9f\n", clocks ? times[clocks - 1] : 0);
    }
    return __real_write(fd, data, size);
}
static void require(bool ok, const char* what) {
    if (!ok) { std::perror(what); std::exit(72); }
}
static SDL_Event key(Uint32 kind, SDL_Scancode sc, bool synthetic = false,
                     Uint32 win = 7, bool repeat = false) {
    SDL_Event e {};
    e.type = kind;
    e.key.windowID = win;
    e.key.which = synthetic ? sa2_input::kSyntheticKeyboard : 0;
    e.key.scancode = sc;
    e.key.down = kind == SDL_EVENT_KEY_DOWN;
    e.key.repeat = repeat;
    return e;
}
static SDL_Event window(Uint32 kind, Uint32 win = 7) {
    SDL_Event e {}; e.type = kind; e.window.windowID = win; return e;
}
static void drain(int fd) {
    unsigned char b[264];
    for (;;) {
        ssize_t n = ::read(fd, b, sizeof b);
        if (n <= 0) return;
        require(n == 264, "whole read");
        unsigned seq = b[4] | b[5] << 8 | b[6] << 16 | unsigned(b[7]) << 24;
        std::printf("record %u", seq);
        for (unsigned i = 0; i != 256; ++i) if (b[i + 8]) std::printf(" %02X=%02X", i, b[i + 8]);
        std::printf("\n");
    }
}
struct Cookie { bool fail = false; std::string bytes; };
static ssize_t cookie_write(void* data, const char* bytes, size_t size) {
    auto& c = *static_cast<Cookie*>(data);
    if (c.fail) { errno = EIO; return -1; }
    c.bytes.append(bytes, size); return ssize_t(size);
}
static int cookie_close(void*) { return 0; }

int main(int argc, char** argv) {
    require(argc == 3, "arguments");
    std::string folder = argv[1], mode = argv[2], fifo = folder + "/keys.fifo", log_path = folder + "/keys.tsv";
    require(::mkfifo(fifo.c_str(), 0600) == 0, "mkfifo");
    bool no_reader = mode == "grouped" || mode == "completion-order" || mode == "undo" || mode == "close-pending" || mode == "destructor";
    int rd = no_reader ? -1 : ::open(fifo.c_str(), O_RDONLY | O_NONBLOCK);
    require(no_reader || rd >= 0, "reader");
    Cookie cookie;
    bool io_case = mode == "header-io" || mode == "row-io" || mode == "flush-io";
    FILE* log = nullptr;
    if (io_case) {
        cookie_io_functions_t io {}; io.write = cookie_write; io.close = cookie_close;
        log = ::fopencookie(&cookie, "w", io);
        if (mode != "flush-io") require(std::setvbuf(log, nullptr, _IONBF, 0) == 0, "unbuffered");
        cookie.fail = mode == "header-io";
    } else log = std::fopen(log_path.c_str(), "w");
    require(log != nullptr, "log");
    {
        sa2_input::KeySender k(fifo, log, log_path, clock_now);
        if (mode == "no-start") {
            k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
            k.pump(); k.close();
        } else {
            require(k.start(), "start");
            if (mode == "immediate" || mode == "borrowed") {
                times = {1.25, 2.25};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 100000000);
                k.event(key(SDL_EVENT_KEY_UP, SDL_SCANCODE_A), 7, 2, 0);
            } else if (mode == "grouped") {
                times = {2, 3, 3.25};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_S), 7, 1.1, 10000000);
                rd = ::open(fifo.c_str(), O_RDONLY | O_NONBLOCK); require(rd >= 0, "late reader");
                k.pump();
            } else if (mode == "completion-order") {
                times = {2, 3, 3.25};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_S), 7, 1.1, 0);
                k.event(key(SDL_EVENT_KEY_UP, SDL_SCANCODE_A), 7, 1.2, 0);
                rd = ::open(fifo.c_str(), O_RDONLY | O_NONBLOCK); require(rd >= 0, "late reader");
                k.pump();
            } else if (mode == "undo") {
                times = {2};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_UP, SDL_SCANCODE_A), 7, 1.5, 0);
                rd = ::open(fifo.c_str(), O_RDONLY | O_NONBLOCK); require(rd >= 0, "late reader");
                k.pump();
            } else if (mode == "full-grouped" || mode == "full-undo") {
                times = mode == "full-grouped" ? std::vector<double>{1.25, 1.75, 2, 3, 3.25}
                                                : std::vector<double>{1.25, 1.75, 2};
                require(::fcntl(rd, F_SETPIPE_SZ, 4096) == 4096, "pipe size");
                int fill = ::open(fifo.c_str(), O_WRONLY | O_NONBLOCK); require(fill >= 0, "filler");
                unsigned char bytes[4096] {}; require(::write(fill, bytes, sizeof bytes) == 4096, "fill pipe");
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                k.event(key(mode == "full-grouped" ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP,
                            mode == "full-grouped" ? SDL_SCANCODE_S : SDL_SCANCODE_A), 7, 1.5, 0);
                require(::read(rd, bytes, sizeof bytes) == 4096, "drain filler");
                ::close(fill); k.pump();
            } else if (mode == "focus" || mode == "quit" || mode == "window-close") {
                times = {1.25, 1.75, 2.75};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_RETURN), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_UP, true), 7, 1.5, 0);
                k.event(window(SDL_EVENT_WINDOW_FOCUS_LOST, 8), 7, 2, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_RETURN, false, 7, true), 7, 2, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A, false, 8), 7, 2, 0);
                Uint32 kind = mode == "focus" ? SDL_EVENT_WINDOW_FOCUS_LOST : mode == "quit" ? SDL_EVENT_QUIT : SDL_EVENT_WINDOW_CLOSE_REQUESTED;
                k.event(window(kind), 7, 2.5, 0);
                k.event(window(kind), 7, 2.5, 0);
            } else if (mode == "close-generated") {
                times = {1.25, 2, 2.25};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
            } else if (mode == "close-unsent") {
                times = {1.25, 2.25};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                drain(rd); ::close(rd); rd = -1;
                k.event(key(SDL_EVENT_KEY_UP, SDL_SCANCODE_A), 7, 2, 0);
            } else if (mode == "close-pending" || mode == "destructor") {
                times = mode == "close-pending" ? std::vector<double>{2} : std::vector<double>{};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
            } else if (mode == "ignores") {
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A, false, 7, true), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A, false, 8), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_F13), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_UP, SDL_SCANCODE_A), 7, 1, 0);
                k.event(window(SDL_EVENT_WINDOW_FOCUS_LOST), 7, 1, 0);
            } else if (mode == "synthetic") {
                times = {1.25, 2.25};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A, true), 7, 1, 0);
                k.event(key(SDL_EVENT_KEY_UP, SDL_SCANCODE_A, true), 7, 2, 0);
            } else if (io_case) {
                times = {1.25}; cookie.fail = true;
                if (!k.log_ok()) check_prewrite = false;
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                require(!k.log_ok(), "log failure visible"); check_prewrite = false;
            } else if (mode == "bad-time") {
                times = {0.5};
                k.event(key(SDL_EVENT_KEY_DOWN, SDL_SCANCODE_A), 7, 1, 0);
                require(!k.log_ok(), "invalid time visible"); check_prewrite = false;
            } else { std::fprintf(stderr, "unknown mode\n"); return 73; }
            if (mode != "destructor") { k.close(); k.close(); }
        }
        if (rd >= 0) drain(rd);
        std::printf("report %s", k.report_json().c_str());
        std::printf("clock_calls %zu\nlog_ok %d\n", clocks, k.log_ok());
    }
    if (rd >= 0) ::close(rd);
    if (io_case) {
        cookie.fail = false; std::clearerr(log);
        require(std::fputs("", log) >= 0 && std::fflush(log) == 0, "borrowed stream remains alive");
        FILE* saved = std::fopen(log_path.c_str(), "w"); require(saved != nullptr, "cookie save");
        require(std::fwrite(cookie.bytes.data(), 1, cookie.bytes.size(), saved) == cookie.bytes.size(), "cookie bytes");
        std::fclose(saved);
    } else if (mode == "borrowed") {
        require(std::fputs("owner-kept-open\n", log) >= 0 && std::fflush(log) == 0, "borrowed log");
    }
    require(std::fclose(log) == 0, "owner fclose");
    return 0;
}
'''


def child(argv, folder):
    folder.mkdir()
    command = {"argv": list(map(str, argv)), "cwd": os.getcwd(),
               "env": {name: os.environ.get(name) for name in
                       ("TMPDIR", "PYTHONPYCACHEPREFIX", "XDG_CACHE_HOME", "SA2_SENDER_HEADERS")}}
    (folder / "command.json").write_text(json.dumps(command, indent=2) + "\n")
    result = subprocess.run(argv, capture_output=True, timeout=30)
    (folder / "stdout.log").write_bytes(result.stdout)
    (folder / "stderr.log").write_bytes(result.stderr)
    (folder / "exit.json").write_text(json.dumps({"exit": result.returncode,
        "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(result.stderr).hexdigest()}, indent=2) + "\n")
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout.decode()


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    assert GXX is not None, "C++ compiler is required; missing is not a skip"
    assert (HEADERS / "SDL3/SDL_events.h").is_file(), "pinned SDL headers are required"
    folder = tmp_path_factory.mktemp("sender-driver")
    source = folder / "driver.cpp"
    source.write_text(DRIVER)
    exe = folder / "driver"
    child([GXX, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
           f"-I{HEADERS}", f"-I{ROOT / 'host/player'}", str(source),
           str(ROOT / "host/player/input.cpp"), "-Wl,--wrap=write", "-o", str(exe)], folder / "compile")
    return exe


def run(driver, tmp_path, mode):
    out = child([str(driver), str(tmp_path), mode], tmp_path / "child")
    report = next(line for line in out.splitlines() if line.startswith("report "))
    report = json.loads("{" + report.removeprefix("report ").strip().rstrip(",") + "}")["host_input"]
    raw = (tmp_path / "keys.tsv").read_text()
    lines = raw.splitlines()
    assert not lines or lines[0].split("\t") == HEADER
    rows = list(csv.DictReader(lines, delimiter="\t")) if lines else []
    records = [line for line in out.splitlines() if line.startswith("record ")]
    return report, rows, records, out


def row(change, scancode, dik, down, synthetic, age_ns, handled, seq, sent, status):
    return dict(zip(HEADER, map(str, (change, scancode, dik, down, synthetic,
                                     age_ns, handled, seq, sent, status))))


def test_immediate_rows_use_prewrite_samples_and_whole_sequences(driver, tmp_path):
    report, rows, records, out = run(driver, tmp_path, "immediate")
    assert rows == [row(1, 4, 30, 1, 0, 100000000, "1.000000000", 0, "1.250000000", "sent"),
                    row(2, 4, 30, 0, 0, 0, "2.000000000", 1, "2.250000000", "sent")]
    assert records == ["record 0 1E=80", "record 1"]
    assert "clock_calls 2\nlog_ok 1" in out
    assert [line for line in out.splitlines() if line.startswith("prewrite")] == [
        "prewrite 1.250000000", "prewrite 2.250000000"]
    assert report["key_log"] == {"path": str(tmp_path / "keys.tsv"), "rows": 2,
                                 "sent": 2, "lost": 0, "unsent": 0, "io_errors": 0, "error": None}


@pytest.mark.parametrize("mode,retries,enxio", [("grouped", 0, 1), ("full-grouped", 2, 0)])
def test_deferred_keys_share_actual_whole_write_time(driver, tmp_path, mode, retries, enxio):
    report, rows, records, _ = run(driver, tmp_path, mode)
    assert [(r["change"], r["dik"], r["seq"], r["sent_s"], r["status"]) for r in rows] == [
        ("1", "30", "0", "2.000000000", "sent"), ("2", "31", "0", "2.000000000", "sent"),
        ("3", "30", "1", "3.250000000", "sent"), ("4", "31", "1", "3.250000000", "sent")]
    assert records == ["record 0 1E=80 1F=80", "record 1"]
    assert report["retries"] == retries and report["enxio"] == enxio
    assert (report["key_events"], report["changes"], report["clears"]) == (2, 2, 1)


@pytest.mark.parametrize("mode", ["undo", "full-undo", "close-pending"])
def test_pending_undo_logs_both_lost_without_a_send_claim(driver, tmp_path, mode):
    report, rows, records, _ = run(driver, tmp_path, mode)
    assert rows[0] == row(1, 4, 30, 1, 0, 0, "1.000000000", "-", "-", "lost")
    assert rows[1] == row(2, 4, 30, 0, 0, 0,
                          "2.000000000" if mode == "close-pending" else "1.500000000", "-", "-", "lost")
    assert report["key_log"]["lost"] == 2 and report["key_log"]["sent"] == 0
    assert records == ([] if mode == "close-pending" else ["record 0"])


def test_close_labels_remaining_release_unsent_after_epipe(driver, tmp_path):
    report, rows, records, _ = run(driver, tmp_path, "close-unsent")
    assert rows[1] == row(2, 4, 30, 0, 0, 0, "2.000000000", "-", "-", "unsent")
    assert report["write_errors"] == 1 and report["key_log"]["unsent"] == 1
    assert records == ["record 0 1E=80"]


def test_destructor_labels_pending_press_unsent_without_adding_a_fifo_clear(driver, tmp_path):
    _, rows, records, out = run(driver, tmp_path, "destructor")
    assert rows == [row(1, 4, 30, 1, 0, 0, "1.000000000", "-", "-", "unsent")]
    assert records == [] and "clock_calls 0" in out


@pytest.mark.parametrize("mode", ["focus", "quit", "window-close"])
def test_generated_releases_preserve_provenance_and_old_counters(driver, tmp_path, mode):
    report, rows, records, _ = run(driver, tmp_path, mode)
    assert rows[2:] == [row(3, 40, 28, 0, 0, 0, "2.500000000", 2, "2.750000000", "sent"),
                       row(4, 82, 200, 0, 1, 0, "2.500000000", 2, "2.750000000", "sent")]
    assert (report["key_events"], report["synthetic_key_events"], report["changes"], report["clears"]) == (1, 1, 2, 1)
    assert (report["repeats_ignored"], report["other_window"]) == (1, 1)
    assert records == ["record 0 1C=80", "record 1 1C=80 C8=80", "record 2"]


def test_explicit_close_generates_one_release_and_remains_idempotent(driver, tmp_path):
    report, rows, records, out = run(driver, tmp_path, "close-generated")
    assert rows[1] == row(2, 4, 30, 0, 0, 0, "2.000000000", 1, "2.250000000", "sent")
    assert records == ["record 0 1E=80", "record 1"]
    assert (report["changes"], report["clears"]) == (1, 1) and "clock_calls 3" in out


def test_synthetic_events_are_separate_and_keep_the_same_fifo_state(driver, tmp_path):
    report, rows, records, _ = run(driver, tmp_path, "synthetic")
    assert (report["key_events"], report["synthetic_key_events"]) == (0, 2)
    assert [r["synthetic"] for r in rows] == ["1", "1"]
    assert records == ["record 0 1E=80", "record 1"]


@pytest.mark.parametrize("mode", ["ignores", "no-start"])
def test_nonchanges_and_unstarted_sender_never_log_or_write(driver, tmp_path, mode):
    report, rows, records, out = run(driver, tmp_path, mode)
    assert rows == records == [] and "clock_calls 0" in out
    assert report["changes"] == report["clears"] == report["records_written"] == 0
    if mode == "no-start":
        assert report["started"] is False and report["open_attempts"] == 0
        assert (tmp_path / "keys.tsv").read_bytes() == b""
    else:
        assert (report["key_events"], report["unmapped"], report["repeats_ignored"], report["other_window"]) == (2, 1, 1, 1)


@pytest.mark.parametrize("mode", ["header-io", "row-io", "flush-io"])
def test_log_io_failure_is_explicit_and_never_changes_fifo_delivery(driver, tmp_path, mode):
    report, rows, records, out = run(driver, tmp_path, mode)
    assert records == ["record 0 1E=80", "record 1"] and rows == []
    assert "log_ok 0" in out and report["key_log"]["io_errors"] == 1
    assert report["key_log"]["rows"] == report["key_log"]["sent"] == 0
    assert report["key_log"]["error"].startswith("key log ")
    assert (report["records_written"], report["changes"], report["clears"]) == (2, 1, 1)


def test_invalid_clock_cannot_be_published_as_sent(driver, tmp_path):
    report, rows, records, out = run(driver, tmp_path, "bad-time")
    assert records == ["record 0 1E=80", "record 1"] and rows == []
    assert report["key_log"]["error"] == "key log invalid timestamp" and "log_ok 0" in out


def test_borrowed_file_remains_owned_by_the_caller(driver, tmp_path):
    out = child([str(driver), str(tmp_path), "borrowed"], tmp_path / "child")
    assert "log_ok 1" in out
    assert (tmp_path / "keys.tsv").read_text().endswith("owner-kept-open\n")


def test_change_ids_recover_event_order_from_completion_order(driver, tmp_path):
    report, rows, records, _ = run(driver, tmp_path, "completion-order")
    assert [(r["change"], r["status"], r["seq"]) for r in rows] == [
        ("1", "lost", "-"), ("3", "lost", "-"), ("2", "sent", "0"), ("4", "sent", "1")]
    assert records == ["record 0 1F=80", "record 1"]
    assert (report["key_log"]["rows"], report["key_log"]["sent"], report["key_log"]["lost"]) == (4, 2, 2)
