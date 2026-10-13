"""Authored CPU checks for poll and relay producers, without a player or game.

The optional source-root override lets a private offer use the real repository's
unchanged dependencies. Four-log acceptance, synthetic refusal, MIN presses and
session/receipt publication belong to the later caller and are not proved here.
"""
from __future__ import annotations

import ast
import builtins
import errno
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(os.environ.get("SA2_TEST_LATENCY_REPO_ROOT",
                           Path(__file__).resolve().parents[3]))
SOURCE_ROOT = Path(os.environ.get("SA2_TEST_LATENCY_SOURCE_ROOT", ROOT))
DINPUT_HEADER = Path(os.environ.get("SA2_TEST_LATENCY_DINPUT_HEADER",
                                   ROOT / "runtime/sa2_dinput.h"))
for directory in (ROOT / "tools/s4", ROOT / "Documentation/scripts",
                  ROOT / "tools/s5", SOURCE_ROOT / "tools/s5"):
    sys.path.insert(0, str(directory))
def source_module(name):
    # Dependency modules may change sys.path; the offered source remains explicit.
    filename = SOURCE_ROOT / "tools/s5" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, filename)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load offered producer: {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != filename.resolve():
        raise AssertionError(f"wrong producer loaded: {module.__file__}")
    return module


PRC = source_module("play_receipt")
PL = source_module("present_live")
PLV = source_module("play_live")
if PLV.PL is not PL or PLV.PRC is not PRC:
    raise AssertionError("play_live producer aliases are not the exact offered modules")

POLL_HEADER = "poll\tfirst_seq\tlast_seq\trecords\ttick\tpresents\tmono_ns\n"
POLL_ROWS = ("1\t10\t10\t1\t1\t0\t10000001500\n"
             "2\t11\t12\t2\t1\t1\t10000002500\n"
             "3\t13\t13\t1\t2\t1\t10000003500\n")
ARRIVAL_ROWS = ("frame\tarrived_s\n1\t10.000001500\n"
                "2\t10.000002500\n3\t10.000002500\n")


def existing_driver() -> str:
    source = ROOT / "Documentation/scripts/tests/test_s5_play_receipt.py"
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "DRIVER"
                for target in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("the existing authored GameLoop DRIVER is absent")


# The existing driver owns guest words and both counters. The generated observer
# includes the actual unchanged tick probe. Only this authored fixture replaces
# clock/stdio calls and exposes static boundary values for overflow controls.
DRIVER_PREFIX = r"""
#define main inherited_driver_main
#define __real_sub_433ee0 inherited_gameloop_body
"""
DRIVER_SETUP = r"""
#undef main
#undef __real_sub_433ee0
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>
static const char *fixture_case;
static FILE *fixture_raw, *fixture_report;
static unsigned fixture_clocks, fixture_opens, fixture_raw_flushes;
static long fixture_ns_bias;
static int fixture_clock_gettime(clockid_t, struct timespec *);
static FILE *fixture_fopen(const char *, const char *);
static int fixture_fputs(const char *, FILE *);
static int fixture_fprintf(FILE *, const char *, ...);
static int fixture_fflush(FILE *);
static int fixture_fclose(FILE *);
static int fixture_rename(const char *, const char *);
#define clock_gettime fixture_clock_gettime
#define fopen fixture_fopen
#define fputs fixture_fputs
#define fprintf fixture_fprintf
#define fflush fixture_fflush
#define fclose fixture_fclose
#define rename fixture_rename
#include "sa2_play_ticks.c"
#undef clock_gettime
#undef fopen
#undef fputs
#undef fprintf
#undef fflush
#undef fclose
#undef rename

static int is_case(const char *value) { return !strcmp(fixture_case, value); }
static int fixture_clock_gettime(clockid_t id, struct timespec *t) {
    if (id != CLOCK_MONOTONIC) return -1;
    ++fixture_clocks;
    t->tv_sec = 10;
    t->tv_nsec = 1500 + (long)(fixture_clocks - 1u) * 1000L + fixture_ns_bias;
    if (fixture_clocks == 2u) {
        if (is_case("clock-error")) { errno = EIO; return -1; }
        if (is_case("clock-negative")) t->tv_sec = -1;
        if (is_case("clock-nsec")) t->tv_nsec = 1000000000L;
        if (is_case("clock-overflow")) t->tv_sec = LLONG_MAX;
        if (is_case("clock-backwards")) t->tv_nsec = 1499;
    }
    return 0;
}
static FILE *fixture_fopen(const char *path, const char *mode) {
    ++fixture_opens;
    int report = strstr(path, ".part") != NULL;
    if ((report && is_case("report-open")) || (!report && is_case("raw-open"))) {
        errno = EACCES; return NULL;
    }
    FILE *out = fopen(path, mode);
    if (out && !strcmp(mode, "wx")) {
        if (report) fixture_report = out; else fixture_raw = out;
    }
    return out;
}
static int fixture_fputs(const char *text, FILE *out) {
    if (out == fixture_raw) {
        if (is_case("header-write")) return EOF;
        if (is_case("slow-header")) { presents += 100u; fixture_ns_bias += 1000000L; }
    }
    if (out == fixture_report && is_case("report-write")) {
        /* Real stdio error state, rather than a fake negative return alone. */
        (void)close(fileno(out));
    }
    return fputs(text, out);
}
static int fixture_fprintf(FILE *out, const char *format, ...) {
    if (out == fixture_raw && is_case("row-write")) return -1;
    va_list ap;
    va_start(ap, format);
    int value = vfprintf(out, format, ap);
    va_end(ap);
    return value;
}
static int fixture_fflush(FILE *out) {
    if (out == fixture_raw) {
        ++fixture_raw_flushes;
        if ((is_case("header-flush") && fixture_raw_flushes == 1u) ||
            (is_case("row-flush") && fixture_raw_flushes == 2u) ||
            (is_case("final-flush") && fixture_raw_flushes == 5u)) return EOF;
    }
    if (out == fixture_report && is_case("report-flush")) return EOF;
    return fflush(out);
}
static int fixture_fclose(FILE *out) {
    int raw = out == fixture_raw, report = out == fixture_report;
    int result = fclose(out);
    if (raw) fixture_raw = NULL;
    if (report) fixture_report = NULL;
    if ((raw && is_case("raw-close")) || (report && is_case("report-close"))) return EOF;
    return result;
}
static int fixture_rename(const char *from, const char *to) {
    if (is_case("report-rename")) { errno = EACCES; return -1; }
    return rename(from, to);
}
void *__real_sub_433ee0(void *st, uint32_t pc, void *mem) {
    void *result = inherited_gameloop_body(st, pc, mem);
    sa2_dinput_host_records_read(2u, 11u, 12u, 2u);
    return result;
}
int main(void) {
    fixture_case = getenv("SA2_TEST_POLL_CASE");
    if (!fixture_case) fixture_case = "normal";
    if (is_case("counter-overflow")) play_poll_callbacks = UINT64_MAX;
    if (is_case("tick-overflow")) gameloop_returns = ULONG_MAX;
    if (is_case("seq-overflow")) {
        sa2_dinput_host_records_read(1u, UINT32_MAX, UINT32_MAX, 1u);
        sa2_dinput_host_records_read(2u, 0u, 0u, 1u);
    } else if (is_case("zero-records")) {
        sa2_dinput_host_records_read(1u, 10u, 10u, 0u);
    } else if (is_case("bad-range")) {
        sa2_dinput_host_records_read(1u, 10u, 12u, 1u);
    } else {
        sa2_dinput_host_records_read(1u, 10u, 10u, 1u);
        if (is_case("poll-reversal")) {
            sa2_dinput_host_records_read(1u, 11u, 11u, 1u);
        } else if (is_case("seq-gap")) {
            sa2_dinput_host_records_read(2u, 12u, 12u, 1u);
        } else if (!is_case("counter-overflow") && !is_case("tick-overflow")) {
            if (__wrap_sub_433ee0(&keys, 0, NULL) != &keys) return 2;
            sa2_dinput_host_records_read(3u, 13u, 13u, 1u);
        }
    }
    play_poll_finish();
    unsigned opens = fixture_opens;
    play_poll_finish();
    if (fixture_opens != opens) return 3;
    printf("observer-finished opens=%u clocks=%u\n", fixture_opens, fixture_clocks);
    return 0;
}
"""


def child(command, directory: Path, stem: str, *, input_text=None, env=None):
    environment = dict(os.environ) if env is None else env
    (directory / f"{stem}.command.json").write_text(json.dumps(
        {"argv": [str(value) for value in command], "cwd": str(directory),
         "env": {key: value for key, value in environment.items()
                 if key.startswith(("SA2_", "ZIG_", "TMP", "PYTHON"))}}, indent=2) + "\n")
    result = subprocess.run(command, cwd=directory, input=input_text,
                            text=True, capture_output=True, env=environment, timeout=60)
    (directory / f"{stem}.stdout").write_text(result.stdout)
    (directory / f"{stem}.stderr").write_text(result.stderr)
    (directory / f"{stem}.exit.json").write_text(json.dumps(
        {"exit_code": result.returncode}, indent=2) + "\n")
    return result


@pytest.fixture(scope="module")
def observer(tmp_path_factory):
    directory = tmp_path_factory.mktemp("latency-observer")
    (directory / "include").mkdir()
    (directory / "include/sa2_dinput.h").write_bytes(DINPUT_HEADER.read_bytes())
    (directory / "sa2_play_ticks.c").write_text(PRC.ticks_observer(ROOT))
    (directory / "driver.c").write_text(DRIVER_PREFIX + existing_driver() + DRIVER_SETUP)
    pinned = json.loads((ROOT / "tools/pins.json").read_text())["tools"]["zig"]["path"]
    zig = ROOT / pinned
    if not zig.is_file():
        pytest.fail(f"the pinned zig is absent: {zig}")
    flags = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror",
             "-I", str(directory / "include"), "-I", str(ROOT / "runtime"), "-I",
             str(ROOT / "private/lifters/remill/usr/local/include")]
    environment = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(directory / "zig-local"),
                       ZIG_GLOBAL_CACHE_DIR=str(directory / "zig-global"),
                       TMPDIR=str(directory))
    result = child([str(zig), "cc", *flags, str(directory / "driver.c"),
                    "-o", str(directory / "observer")], directory, "compile", env=environment)
    assert result.returncode == 0, result.stderr
    return directory / "observer"


def run_observer(observer, tmp_path, case="normal", *, configured=True):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("SA2_")}
    environment["SA2_TEST_POLL_CASE"] = case
    if configured:
        environment.update(SA2_PLAY_POLLS=str(tmp_path / "polls.tsv"),
                           SA2_PLAY_POLLS_REPORT=str(tmp_path / "poll-report.json"))
    result = child([str(observer)], tmp_path, "observer", env=environment,
                   input_text="10 5 16 1ab2000 0 1 0\n")
    assert result.returncode == 0, result.stderr
    return result


def test_generated_poll_observer_reads_real_return_counter_and_present_snapshot(observer, tmp_path):
    run_observer(observer, tmp_path)
    assert (tmp_path / "polls.tsv").read_text() == POLL_HEADER + POLL_ROWS
    report = json.loads((tmp_path / "poll-report.json").read_text())
    assert {key: report[key] for key in
            ("schema", "clock", "unit", "requested", "opened", "closed", "finalized",
             "ok", "callbacks", "rows", "records_observed", "records_logged",
             "first_seq", "last_seq", "last_poll", "clock_errors", "io_errors",
             "dropped_rows", "error")} == {
        "schema": "sa2-s5-play-polls/v1", "clock": "CLOCK_MONOTONIC", "unit": "ns",
        "requested": True, "opened": True, "closed": True, "finalized": True,
        "ok": True, "callbacks": 3, "rows": 3, "records_observed": 4,
        "records_logged": 4, "first_seq": 10, "last_seq": 13, "last_poll": 3,
        "clock_errors": 0, "io_errors": 0, "dropped_rows": 0, "error": None}


def test_poll_snapshot_precedes_header_io(observer, tmp_path):
    run_observer(observer, tmp_path, "slow-header")
    assert (tmp_path / "polls.tsv").read_text() == POLL_HEADER + (
        "1\t10\t10\t1\t1\t0\t10000001500\n"
        "2\t11\t12\t2\t1\t101\t10001002500\n"
        "3\t13\t13\t1\t2\t101\t10001003500\n")


def test_poll_without_paths_performs_no_file_io(observer, tmp_path):
    result = run_observer(observer, tmp_path, configured=False)
    assert result.stdout == "observer-finished opens=0 clocks=3\n"
    assert not (tmp_path / "polls.tsv").exists()
    assert not (tmp_path / "poll-report.json").exists()


@pytest.mark.parametrize("configuration", ["raw-only", "report-only", "same-path"])
def test_poll_incomplete_or_identical_path_configuration_refuses_readiness(
        observer, tmp_path, configuration):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("SA2_")}
    raw, report_path = tmp_path / "polls.tsv", tmp_path / "poll-report.json"
    if configuration != "report-only":
        environment["SA2_PLAY_POLLS"] = str(raw)
    if configuration != "raw-only":
        if configuration == "same-path":
            report_path = raw
        environment["SA2_PLAY_POLLS_REPORT"] = str(report_path)
    result = child([str(observer)], tmp_path, "observer", env=environment,
                   input_text="10 5 16 1ab2000 0 1 0\n")
    assert result.returncode == 0
    if configuration == "raw-only":
        assert not raw.exists() and not report_path.exists()
    else:
        report = json.loads(report_path.read_text())
        assert report["ok"] is False and report["opened"] is False and report["rows"] == 0
        assert report["error"] == "poll raw/report paths must both be distinct"


@pytest.mark.parametrize("case", ["clock-error", "clock-negative", "clock-nsec",
                                  "clock-overflow", "clock-backwards"])
def test_poll_clock_errors_leave_literal_prefix_and_invalid_report(observer, tmp_path, case):
    run_observer(observer, tmp_path, case)
    assert (tmp_path / "polls.tsv").read_text() == POLL_HEADER + (
        "1\t10\t10\t1\t1\t0\t10000001500\n")
    report = json.loads((tmp_path / "poll-report.json").read_text())
    assert report["ok"] is False and report["rows"] == 1 and report["dropped_rows"] == 2
    assert report["clock_errors"] == 1
    assert report["error"] == "poll monotonic clock failed or moved backwards"


@pytest.mark.parametrize("case,error", [
    ("counter-overflow", "poll observer counter overflow"),
    ("tick-overflow", "poll tick or record range is invalid"),
    ("zero-records", "poll tick or record range is invalid"),
    ("bad-range", "poll tick or record range is invalid"),
    ("seq-overflow", "poll tick or record range is invalid"),
    ("poll-reversal", "poll tick or record range is invalid"),
    ("seq-gap", "poll tick or record range is invalid")])
def test_poll_counter_and_range_boundaries_refuse_readiness(observer, tmp_path, case, error):
    run_observer(observer, tmp_path, case)
    report = json.loads((tmp_path / "poll-report.json").read_text())
    assert report["ok"] is False and report["error"] == error
    if case in ("seq-overflow", "poll-reversal", "seq-gap"):
        assert report["rows"] == 1
    else:
        assert (tmp_path / "polls.tsv").read_text() == POLL_HEADER
        assert report["rows"] == 0


@pytest.mark.parametrize("case,error,rows,closed", [
    ("raw-open", "poll log open failed", 0, False),
    ("header-write", "poll header write failed", 0, True),
    ("header-flush", "poll header flush failed", 0, True),
    ("row-write", "poll row write failed", 0, True),
    ("row-flush", "poll row flush failed", 0, True),
    ("final-flush", "poll final flush failed", 3, True),
    ("raw-close", "poll close failed", 3, False)])
def test_poll_raw_io_failures_cannot_grant_readiness(observer, tmp_path, case, error, rows, closed):
    run_observer(observer, tmp_path, case)
    report = json.loads((tmp_path / "poll-report.json").read_text())
    assert report["ok"] is False and report["error"] == error
    assert report["rows"] == rows and report["closed"] is closed and report["io_errors"] >= 1


@pytest.mark.parametrize("case", ["report-open", "report-write", "report-flush",
                                  "report-close", "report-rename"])
def test_poll_report_failures_publish_no_ready_sidecar(observer, tmp_path, case):
    run_observer(observer, tmp_path, case)
    assert (tmp_path / "polls.tsv").read_text() == POLL_HEADER + POLL_ROWS
    assert not (tmp_path / "poll-report.json").exists()
    if case != "report-open":
        assert (tmp_path / "poll-report.json.part").is_file()


def test_poll_report_will_not_replace_an_existing_file(observer, tmp_path):
    (tmp_path / "poll-report.json").write_text("held-original\n")
    run_observer(observer, tmp_path)
    assert (tmp_path / "poll-report.json").read_text() == "held-original\n"
    assert (tmp_path / "poll-report.json.part").is_file()


def test_runtime_header_weak_hook_links_without_any_handler(tmp_path):
    (tmp_path / "include").mkdir()
    (tmp_path / "include/sa2_dinput.h").write_bytes(DINPUT_HEADER.read_bytes())
    source = tmp_path / "weak.c"
    source.write_text('#include "sa2_dinput.h"\n#include <stdio.h>\n'
                      'int main(void) { if (sa2_dinput_host_records_read) return 2; '
                      'puts("weak-unset"); return 0; }\n')
    zig = ROOT / json.loads((ROOT / "tools/pins.json").read_text())["tools"]["zig"]["path"]
    environment = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(tmp_path / "zig-local"),
                       ZIG_GLOBAL_CACHE_DIR=str(tmp_path / "zig-global"), TMPDIR=str(tmp_path))
    compiled = child([str(zig), "cc", "-target", "x86_64-linux-musl", "-O2",
                      "-Wall", "-Wextra", "-Werror", "-I", str(tmp_path / "include"),
                      "-I", str(ROOT / "runtime"),
                      str(source), "-o", str(tmp_path / "weak")], tmp_path, "compile", env=environment)
    assert compiled.returncode == 0, compiled.stderr
    ran = child([str(tmp_path / "weak")], tmp_path, "weak", env=environment)
    assert ran.returncode == 0 and ran.stdout == "weak-unset\n"


# Minimal authored SA2S framing. Literal assertions below do not derive expected
# timestamps, frame numbers or report booleans from this builder.
def stream():
    return (b"SA2D3D9S" + struct.pack("<II", 1, 16) +
            struct.pack("<IIIQII", 3, 28, 0, 100, 1, 0) +
            struct.pack("<IIIQII", 3, 28, 1, 200, 2, 0) +
            struct.pack("<IIIQII", 3, 28, 2, 300, 3, 0))


def test_frameclock_stamps_only_whole_frames_and_coalesced_frames_share_read():
    diagnostics = {}
    clock = PL.FrameClock(diagnostics)
    payload = stream()
    clock.feed(payload[:43], 1.0, 10000000500)
    assert diagnostics["arrivals"] == []
    clock.feed(payload[43:44], 2.0, 10000001500)
    assert diagnostics["arrivals"] == [(1, 10000001500)]
    clock.feed(payload[44:], 3.0, 10000002500)
    assert diagnostics["arrivals"] == [(1, 10000001500), (2, 10000002500), (3, 10000002500)]
    assert clock.frames == [2.0, 3.0, 3.0]
    assert diagnostics["complete_frames"] == 3 and diagnostics["record_headers_seen"] == 3


def test_frameclock_missing_stamp_and_bad_framing_are_diagnostic_failures():
    diagnostics = {}
    clock = PL.FrameClock(diagnostics)
    clock.feed(stream(), 2.0)
    assert diagnostics["arrivals"] == [] and diagnostics["complete_frames"] == 3
    assert diagnostics["dropped_frames"] == 3
    bad = bytearray(stream())
    struct.pack_into("<I", bad, 20, 13)
    broken = PL.FrameClock({})
    broken.feed(bytes(bad), 1.0, 10000001500)
    assert broken.problem == "the record at offset 16 has size 13"
    assert broken.frames == []


@pytest.mark.parametrize("invalid", [-1, True, 1.5, OSError("clock fixture failed")])
def test_arrival_clock_invalid_samples_are_sticky_without_fabricated_ns(monkeypatch, invalid):
    def sample():
        if isinstance(invalid, Exception):
            raise invalid
        return invalid
    monkeypatch.setattr(PL.time, "monotonic_ns", sample)
    diagnostics = {}
    assert PL.arrival_stamp(diagnostics) is None
    assert diagnostics["clock_errors"] == 1
    monkeypatch.setattr(PL.time, "monotonic_ns", lambda: pytest.fail("failed clock was retried"))
    assert PL.arrival_stamp(diagnostics) is None
    assert "last_read_ns" not in diagnostics


def test_arrival_clock_refuses_raw_submicrosecond_reversal(monkeypatch):
    stamps = iter([10000001500, 10000001499])
    monkeypatch.setattr(PL.time, "monotonic_ns", lambda: next(stamps))
    diagnostics = {}
    assert PL.arrival_stamp(diagnostics) == 10000001500
    assert PL.arrival_stamp(diagnostics) is None
    assert diagnostics == {"last_read_ns": 10000001500, "clock_errors": 1,
                           "error": "ValueError: monotonic nanosecond sample moved backwards"}


def test_read_pipe_samples_before_resize_copy_spool_and_frame_publication(monkeypatch, tmp_path):
    events = []
    chunks = iter([stream(), b""])
    class Poll:
        def register(self, *_):
            pass
        def poll(self, *_):
            return [(123, PL.select.POLLIN | PL.select.POLLHUP)]
    def read(*_):
        events.append("read")
        return next(chunks)
    def stamp():
        events.append("stamp")
        return 10000001500
    def resize(_fd, held):
        events.append("resize")
        held["pipe_bytes"] = PL.PIPE_MIN + 1
    def write(_fd, data):
        events.append("copy")
        assert data == stream()
    class Spool:
        feeding = False
        def grew(self, size):
            events.append("spool")
            assert size == 100
        def unfed(self, _):
            return 0
    diagnostics = {"bytes_read": 0}
    clock = PL.FrameClock(diagnostics)
    original_feed = clock.feed
    def framed(*args):
        events.append("frame")
        original_feed(*args)
    clock.feed = framed
    monkeypatch.setattr(PL.select, "poll", Poll)
    monkeypatch.setattr(PL.os, "read", read)
    monkeypatch.setattr(PL.time, "monotonic_ns", stamp)
    monkeypatch.setattr(PL, "resize_pipe", resize)
    monkeypatch.setattr(PL, "fit_pipe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(PL, "write_all", write)
    result = PL.read_pipe(123, 456, Spool(), clock, lambda: False, lead_frames=1)
    assert events == ["resize", "read", "stamp", "resize", "copy", "spool", "frame", "read", "resize"]
    assert diagnostics["arrivals"] == [(1, 10000001500), (2, 10000001500), (3, 10000001500)]
    assert diagnostics["bytes_read"] == 100
    assert result["bytes"] == 100 and result["chunks"] == 1
    assert result["end"] == "the writer closed the pipe"


def pipe_relay(tmp_path, diagnostics=None):
    read_fd, write_fd = os.pipe()
    sink_read, sink_write = os.pipe()
    os.write(write_fd, stream())
    os.close(write_fd)
    try:
        result = PL.relay(read_fd, tmp_path / "copy.sa2s", sink_write, lambda: False,
                          grace=2, arrival_diagnostics=diagnostics)
        assert os.read(sink_read, 4096) == stream()
        assert os.read(sink_read, 4096) == b""
        assert (tmp_path / "copy.sa2s").read_bytes() == stream()
        assert not result["feed_thread"].is_alive()
        return result
    finally:
        os.close(read_fd)
        os.close(sink_read)


def test_disabled_relay_preserves_transport_keys_and_takes_no_ns_sample(monkeypatch, tmp_path):
    monkeypatch.setattr(PL.time, "monotonic_ns", lambda: pytest.fail("disabled relay sampled ns"))
    result = pipe_relay(tmp_path)
    assert set(result) == {"bytes", "chunks", "first_byte_s", "last_byte_s", "max_gap_s",
                           "end", "closed_monotonic", "t0_monotonic", "frames_arrived",
                           "records", "framing_problem", "frame_arrival_s", "feed",
                           "feed_ended", "feed_thread"}
    assert result["frames_arrived"] == 3 and result["records"] == 3
    assert result["feed_ended"] is True and result["feed"]["closed"] is True


def test_enabled_relay_forwards_real_pipe_bytes_and_joins_before_return(monkeypatch, tmp_path):
    monkeypatch.setattr(PL.time, "monotonic_ns", lambda: 10000001500)
    diagnostics = {}
    result = pipe_relay(tmp_path, diagnostics)
    assert result["arrival_diagnostics"] is diagnostics
    assert diagnostics["arrivals"] == [(1, 10000001500), (2, 10000001500), (3, 10000001500)]
    assert diagnostics["complete_frames"] == 3 and diagnostics["bytes_read"] == 100
    assert diagnostics["clock_errors"] == 0 and diagnostics["io_errors"] == 0
    assert diagnostics["copy_closed"] is True and diagnostics["feed_ended"] is True
    assert diagnostics["relay_finished"] is True and diagnostics["parser_partial"] is False
    assert diagnostics["transport_end"] == "the writer closed the pipe"
    assert diagnostics["clock_info"]["monotonic"] is True
    assert diagnostics["clock_info"]["adjustable"] is False
    assert "CLOCK_MONOTONIC)" in diagnostics["clock_info"]["implementation"]


@pytest.mark.parametrize("bad_clock", [
    SimpleNamespace(implementation="clock_gettime(CLOCK_MONOTONIC_RAW)", monotonic=True,
                    adjustable=False, resolution=1e-9),
    SimpleNamespace(implementation="clock_gettime(CLOCK_MONOTONIC)", monotonic=False,
                    adjustable=False, resolution=1e-9),
    SimpleNamespace(implementation="clock_gettime(CLOCK_MONOTONIC)", monotonic=True,
                    adjustable=True, resolution=1e-9)])
def test_relay_clock_domain_refusal_keeps_transport_but_drops_evidence(monkeypatch, tmp_path, bad_clock):
    monkeypatch.setattr(PL.time, "get_clock_info", lambda _: bad_clock)
    monkeypatch.setattr(PL.time, "monotonic_ns", lambda: pytest.fail("invalid clock was sampled"))
    diagnostics = {}
    pipe_relay(tmp_path, diagnostics)
    assert diagnostics["arrivals"] == [] and diagnostics["complete_frames"] == 3
    assert diagnostics["dropped_frames"] == 3 and diagnostics["clock_errors"] == 1
    assert diagnostics["error"] == "clock info ValueError: monotonic clock is not CLOCK_MONOTONIC"


@pytest.mark.parametrize("cleanup_fault", ["none", "copy-close", "join"])
def test_relay_read_failure_retains_prefix_cleanup_and_original_exception(
        monkeypatch, tmp_path, cleanup_fault):
    original_error = OSError(errno.EIO, "authored read failure")
    copy_descriptors = []
    actual_close = os.close
    actual_join = PL.threading.Thread.join
    def failing(_fd, copy_fd, spool, clock, _alive, **_):
        copy_descriptors.append(copy_fd)
        os.write(copy_fd, stream()[:44])
        spool.grew(44)
        clock.arrival_diagnostics["bytes_read"] = 44
        clock.feed(stream()[:44], 0.0, 10000001500)
        raise original_error
    monkeypatch.setattr(PL, "read_pipe", failing)
    if cleanup_fault == "copy-close":
        def closed(fd):
            actual_close(fd)
            if fd in copy_descriptors:
                raise OSError("authored copy close failure")
        monkeypatch.setattr(PL.os, "close", closed)
    elif cleanup_fault == "join":
        def joined(thread, **kwargs):
            actual_join(thread, **kwargs)
            raise OSError("authored join failure")
        monkeypatch.setattr(PL.threading.Thread, "join", joined)
    sink_read, sink_write = os.pipe()
    diagnostics = {}
    try:
        with pytest.raises(OSError) as caught:
            PL.relay(-1, tmp_path / "prefix.sa2s", sink_write, lambda: False,
                     grace=2, arrival_diagnostics=diagnostics)
        assert caught.value is original_error
        assert os.read(sink_read, 4096) == stream()[:44]
        assert os.read(sink_read, 4096) == b""
        assert diagnostics["arrivals"] == [(1, 10000001500)]
        assert diagnostics["copy_closed"] is (cleanup_fault != "copy-close")
        assert diagnostics["feed_ended"] is (cleanup_fault != "join")
        assert diagnostics["relay_finished"] is True and diagnostics["complete_frames"] == 1
        assert diagnostics["transport_end"] is None
        assert diagnostics["error"] == "transport OSError: [Errno 5] authored read failure"
    finally:
        actual_close(sink_read)


def good_diagnostics():
    return {"arrivals": [(1, 10000001500), (2, 10000002500), (3, 10000002500)],
            "complete_frames": 3, "record_headers_seen": 3, "bytes_read": 100,
            "clock_errors": 0, "io_errors": 0, "error": None,
            "clock_info": {"implementation": "clock_gettime(CLOCK_MONOTONIC)",
                           "monotonic": True, "adjustable": False, "resolution": 1e-9},
            "transport_end": "the writer closed the pipe", "framing_problem": None,
            "feed_ended": True, "feed_error": None, "relay_finished": True,
            "copy_closed": True, "parser_partial": False}


def test_arrival_serializer_uses_integer_nine_digit_samples_and_closed_report(tmp_path):
    raw, report_path = tmp_path / "arrivals.tsv", tmp_path / "arrival-report.json"
    report = PLV.write_arrivals_ns(raw, good_diagnostics(), report_path)
    assert raw.read_text() == ARRIVAL_ROWS
    assert json.loads(report_path.read_text()) == report
    assert report["schema"] == "sa2-s5-relay-arrivals/v1"
    assert report["clock"] == "CLOCK_MONOTONIC" and report["unit"] == "ns"
    assert report["opened"] is True and report["closed"] is True and report["finalized"] is True
    assert report["ok"] is True and report["rows"] == 3 and report["dropped_frames"] == 0
    assert report["io_errors"] == 0 and report["error"] is None
    assert not report_path.with_name(report_path.name + ".part").exists()


@pytest.mark.parametrize("sample", [(True, 10000002500), (2, True), (2, -1)])
def test_arrival_serializer_invalid_sample_retains_literal_valid_prefix(tmp_path, sample):
    diagnostics = good_diagnostics() | {"arrivals": [(1, 10000001500), sample]}
    report = PLV.write_arrivals_ns(tmp_path / "arrivals.tsv", diagnostics,
                                  tmp_path / "arrival-report.json")
    assert (tmp_path / "arrivals.tsv").read_text() == "frame\tarrived_s\n1\t10.000001500\n"
    assert report["ok"] is False and report["rows"] == 1 and report["dropped_frames"] == 2
    assert report["error"] == "ValueError: invalid held arrival sample"


@pytest.mark.parametrize("change", [
    {"clock_errors": 1}, {"io_errors": 1}, {"feed_ended": False},
    {"feed_error": "sink failed"}, {"copy_closed": False}, {"relay_finished": False},
    {"parser_partial": True}, {"framing_problem": "bad frame"},
    {"transport_end": "the game ended without opening the pipe"},
    {"complete_frames": 4}, {"error": "original transport failure"}])
def test_arrival_report_requires_complete_closed_transport(tmp_path, change):
    report = PLV.write_arrivals_ns(tmp_path / "arrivals.tsv", good_diagnostics() | change,
                                    tmp_path / "arrival-report.json")
    assert report["ok"] is False


class FaultFile:
    def __init__(self, actual, operation, occurrence=1):
        self.actual, self.operation, self.occurrence = actual, operation, occurrence
        self.counts = {"write": 0, "flush": 0, "close": 0}
    def write(self, body):
        self.counts["write"] += 1
        if self.operation == "write" and self.counts["write"] == self.occurrence:
            self.actual.write(body[:2])
            return 2
        return self.actual.write(body)
    def flush(self):
        self.counts["flush"] += 1
        if self.operation == "flush" and self.counts["flush"] == self.occurrence:
            raise OSError("authored flush failure")
        return self.actual.flush()
    def close(self):
        self.actual.close()
        if self.operation == "close":
            raise OSError("authored close failure")
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


def fault_open(monkeypatch, target, operation, occurrence=1):
    actual_open = builtins.open
    def opened(path, *args, **kwargs):
        if Path(path) == target and operation == "open":
            raise OSError("authored open failure")
        value = actual_open(path, *args, **kwargs)
        return FaultFile(value, operation, occurrence) if Path(path) == target else value
    monkeypatch.setattr(builtins, "open", opened)


@pytest.mark.parametrize("operation,occurrence,rows,closed", [
    ("open", 1, 0, False), ("write", 1, 0, True), ("write", 3, 1, True),
    ("flush", 1, 0, True), ("flush", 3, 1, True), ("close", 1, 3, False)])
def test_arrival_raw_io_failures_keep_partial_evidence_and_invalid_sidecar(
        monkeypatch, tmp_path, operation, occurrence, rows, closed):
    raw, report_path = tmp_path / "arrivals.tsv", tmp_path / "arrival-report.json"
    fault_open(monkeypatch, raw, operation, occurrence)
    report = PLV.write_arrivals_ns(raw, good_diagnostics(), report_path)
    assert report["ok"] is False and report["rows"] == rows and report["closed"] is closed
    assert report["io_errors"] == 1 and report["dropped_frames"] == 3 - rows
    assert report["error"] is not None
    assert json.loads(report_path.read_text()) == report
    if operation == "write" and occurrence == 3:
        assert raw.read_text() == "frame\tarrived_s\n1\t10.000001500\n2\t"


@pytest.mark.parametrize("operation", ["open", "write", "flush", "close", "rename"])
def test_arrival_report_publication_failures_leave_no_final_report(monkeypatch, tmp_path, operation):
    raw, report_path = tmp_path / "arrivals.tsv", tmp_path / "arrival-report.json"
    part = report_path.with_name(report_path.name + ".part")
    if operation == "rename":
        monkeypatch.setattr(PLV.os, "rename", lambda *_: (_ for _ in ()).throw(
            OSError("authored rename failure")))
    else:
        fault_open(monkeypatch, part, operation)
    with pytest.raises(OSError):
        PLV.write_arrivals_ns(raw, good_diagnostics(), report_path)
    assert raw.read_text() == ARRIVAL_ROWS
    assert not report_path.exists()
    assert part.exists() is (operation != "open")


def test_arrival_failure_report_keeps_original_transport_error(monkeypatch, tmp_path):
    raw = tmp_path / "arrivals.tsv"
    fault_open(monkeypatch, raw, "close")
    diagnostics = good_diagnostics() | {"error": "original read failure"}
    report = PLV.write_arrivals_ns(raw, diagnostics, tmp_path / "arrival-report.json")
    assert report["ok"] is False and report["error"] == "original read failure"
    assert report["io_errors"] == 1 and report["closed"] is False


def test_arrival_publication_refuses_existing_final_and_retains_it(tmp_path):
    report_path = tmp_path / "arrival-report.json"
    report_path.write_text("held-original\n")
    with pytest.raises(FileExistsError):
        PLV.write_arrivals_ns(tmp_path / "arrivals.tsv", good_diagnostics(), report_path)
    assert report_path.read_text() == "held-original\n"
    assert report_path.with_name(report_path.name + ".part").is_file()


def fresh_paths(tmp_path):
    paths = {key: tmp_path / key for key in
             ("ticks", "polls", "keys", "presents", "relay_frames", "poll_report", "relay_report",
              "input_fifo", "game_fifo", "copy", "run", "ticks_observer")}
    paths.update(out=tmp_path, player={"report": tmp_path / "player-report",
                                      "pace_log": tmp_path / "pace-log"})
    return paths


def test_latency_output_paths_require_fresh_distinct_session_files(tmp_path):
    paths = fresh_paths(tmp_path)
    PLV.validate_latency_paths(paths)
    paths["polls"].write_text("held-original\n")
    with pytest.raises(ValueError, match="must be fresh"):
        PLV.validate_latency_paths(paths)
    assert paths["polls"].read_text() == "held-original\n"


@pytest.mark.parametrize("alias", ["keys", "input_fifo", "copy", "ticks_observer"])
def test_latency_output_aliases_are_refused_before_io(tmp_path, alias):
    paths = fresh_paths(tmp_path)
    paths["polls"] = paths[alias]
    with pytest.raises(ValueError, match="alias"):
        PLV.validate_latency_paths(paths)
    assert list(tmp_path.iterdir()) == []


def test_latency_output_symlink_and_outside_session_are_refused(tmp_path):
    paths = fresh_paths(tmp_path)
    paths["polls"].symlink_to(tmp_path / "absent-target")
    with pytest.raises(ValueError, match="fresh"):
        PLV.validate_latency_paths(paths)
    paths["polls"] = tmp_path.parent / "outside-polls.tsv"
    with pytest.raises(ValueError, match="fresh"):
        PLV.validate_latency_paths(paths)
