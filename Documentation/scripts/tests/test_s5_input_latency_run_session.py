"""Actual run_session cleanup with authored process/thread/FD/log/relay doubles.

No child process, player, SDL, compiler or game runs. The actual serializer
writes private fixture files. This covers post-relay orchestration only.
"""
from __future__ import annotations

import builtins
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(os.environ.get("SA2_TEST_LATENCY_REPO_ROOT", Path(__file__).resolve().parents[3]))
for directory in (ROOT / "tools/s4", ROOT / "tools/s5", ROOT / "Documentation/scripts"):
    sys.path.insert(0, str(directory))


def source_module(name, variable):
    path = Path(os.environ.get(variable, ROOT / "tools/s5" / f"{name}.py"))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load offered source: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if Path(module.__file__).resolve() != path.resolve():
        raise AssertionError(f"wrong offered source loaded: {module.__file__}")
    return module


PRC = source_module("play_receipt", "SA2_TEST_LATENCY_RECEIPT_SOURCE")
PL = source_module("present_live", "SA2_TEST_LATENCY_PRESENT_SOURCE")
PLV = source_module("play_live", "SA2_TEST_LATENCY_PLAY_LIVE_SOURCE")
if PLV.PL is not PL or PLV.PRC is not PRC:
    raise AssertionError("run_session aliases do not name the offered producer modules")


# The caller's threads this fixture starts but never runs: the disk watchdog, the player's
# end-request reader and the per-tick watcher.
WATCHERS = ("wait_game", "run_player", "watch_disk", "watch_end", "watch_ticks")


def diagnostic_prefix():
    # Authored relay double, not a claim that a game or player produced this.
    return {"arrivals": [(1, 10000001500)], "complete_frames": 1,
            "record_headers_seen": 1, "bytes_read": 44, "clock_errors": 0,
            "io_errors": 0, "error": None, "clock_info": {"implementation":
            "clock_gettime(CLOCK_MONOTONIC)", "monotonic": True, "adjustable": False,
            "resolution": 1e-9}, "transport_end": "the writer closed the pipe",
            "framing_problem": None, "feed_ended": True, "feed_error": None,
            "relay_finished": True, "copy_closed": True, "parser_partial": False}


class Session:
    """Deterministic doubles around the real caller, with literal fault labels."""
    def __init__(self, monkeypatch, directory, *, primary=None, failures=(),
                 serializer_failure=False, retention_failure=False, alive=False,
                 receipt=True):
        self.directory = directory
        self.primary = primary
        self.failures = {name: OSError(f"authored {name} failure") for name in failures}
        self.fired = set()
        self.serializer_error = OSError("authored serializer publication failure")
        self.retention_error = OSError("authored diagnostic retention failure")
        self.serializer_failure = serializer_failure
        self.retention_failure = retention_failure
        self.alive = alive
        self.events, self.threads, self.pending, self.async_errors = [], [], [], []
        self.event_objects = []
        self.processes = []
        self.caught = self.result = self.diagnostics = None
        self.serialize_calls = 0
        self.raw_open = builtins.open
        self.raw_close = os.close
        self.fd_labels = {100: "close input hold", 101: "close game stream",
                          102: "close video read", 103: "relay video write",
                          104: "close cancel read", 105: "close cancel pipe",
                          106: "close end pipe", 107: "close end write",
                          200: "close inherited video", 201: "close inherited cancel",
                          202: "close inherited end"}
        self.paths = {name: directory / name for name in
                      ("ticks", "polls", "keys", "presents", "relay_frames", "poll_report",
                       "relay_report", "input_fifo", "game_fifo", "copy", "run",
                       "ticks_observer", "game_log", "matrix")}
        self.paths.update(out=directory, player={"report": directory / "player-report",
                                                "pace_log": directory / "pace-log",
                                                "log": directory / "player-log"})
        self.plan = {"paths": self.paths, "receipt": {"on": receipt, "level": 10},
                     "pace": {"on": False, "lead_frames": None},
                     "cmd": ["authored-loop"], "player_cmd": ["authored-player"],
                     "player_env": {}, "binary": directory / "authored-binary",
                     "build": "authored-no-process", "limits": {}}
        self.install(monkeypatch)

    def action(self, name):
        self.events.append(name)
        if name in self.failures and name not in self.fired:
            self.fired.add(name)
            raise self.failures[name]

    def install(self, monkeypatch):
        session = self
        class Event:
            def __init__(self):
                self.index = len(session.event_objects)
                self.set_value = False
                session.event_objects.append(self)
            def is_set(self):
                return self.set_value
            def set(self):
                self.set_value = True
            def wait(self, *_):
                if self.index == 0:
                    # Player appears while caller waits, not at pre-wait observation.
                    session.complete_pending()
                    session.action("wait game")
                    return self.set_value
                session.action("wait player stream")
                return not session.alive
        class Lock:
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
        class Thread:
            def __init__(self, target, args=(), **_):
                self.target, self.args = target, args
                self.name = target.__name__
                self.started = self.completed = False
                self.ident = None
                session.threads.append(self)
            def start(self):
                if self.name not in WATCHERS:
                    session.action("start drainer")
                self.started, self.ident = True, 1
                if self.name in ("wait_game", "run_player"):
                    session.pending.append(self)
                elif self.name not in WATCHERS:
                    self.target(*self.args)
                    self.completed = True
                # Watchdog, end-request and per-tick scheduling is deliberately outside this
                # cleanup fixture (test_s5_play_live_session.py runs them over real pipes).
            def join(self, **_):
                label = {"run_player": "join player watcher",
                         "watch_end": "join end reader"}.get(self.name, "join drainer")
                session.action(label)
                if not self.started:
                    raise RuntimeError("cannot join thread before it is started")
            def is_alive(self):
                return self.started and not self.completed
        class Process:
            def __init__(self, player):
                self.player, self.pid, self.returncode = player, 4321 if player else 1234, None
            def wait(self, timeout=None):
                if self.player and timeout is not None:
                    session.action("wait player")
                    self.returncode = 0
                elif not self.player or not session.alive:
                    self.returncode = 0
                return self.returncode
            def poll(self):
                session.action("poll player")
                return self.returncode
            def kill(self):
                session.action("kill player")
                self.returncode = -9
        def popen(command, **_):
            session.events.append("spawn double " + command[0])
            process = Process(command == session.plan["player_cmd"])
            session.processes.append(process)
            return process
        def close(fd):
            if fd in session.fd_labels:
                session.action(session.fd_labels[fd])
                return
            return session.raw_close(fd)
        pairs = iter([(102, 103), (104, 105), (106, 107)])
        def open_fd(path, *_):
            if Path(path) != session.paths["input_fifo"]:
                raise AssertionError(f"unexpected descriptor open: {path}")
            session.events.append("open input hold")
            return 100
        def written(fd, data):
            assert fd == 105 and data == b"\x01"
            session.action("cancel player")
            return 1
        def relay(_fd, _copy, _sink, _alive, **kwargs):
            session.events.append("relay double")
            diagnostics = kwargs.get("arrival_diagnostics")
            session.diagnostics = diagnostics
            if diagnostics is not None:
                diagnostics.update(diagnostic_prefix())
                if session.primary is not None:
                    diagnostics.update(transport_end=None, error="authored original relay error")
            if session.primary is not None:
                raise session.primary
            return {"end": "the writer closed the pipe", "bytes": 44, "records": 1,
                    "frames_arrived": 1, "framing_problem": None, "max_gap_s": 0,
                    "frame_arrival_s": [0.000001], "t0_monotonic": 10,
                    "feed": {"error": None}}
        def stop(*_):
            session.action("stop game")
            return {"how": "authored stop double"}
        def drain(*_):
            session.events.append("drain double")
        class Log:
            def __init__(self, file, label):
                self.file, self.label = file, label
            def write(self, value):
                return self.file.write(value)
            def flush(self):
                return self.file.flush()
            def close(self):
                self.file.close()
                session.action(self.label)
        def opened(path, *args, **kwargs):
            path = Path(path)
            if path == session.paths["relay_report"].with_name("relay_report.part") and session.serializer_failure:
                raise session.serializer_error
            if path == session.directory / "relay-diagnostic-error.txt" and session.retention_failure:
                raise session.retention_error
            file = session.raw_open(path, *args, **kwargs)
            if path == session.paths["game_log"]:
                return Log(file, "close game log")
            if path == session.paths["player"]["log"]:
                return Log(file, "close player log")
            return file
        actual_serializer = PLV.write_arrivals_ns
        def serialize(*args):
            session.events.append("serialize actual arrival files")
            session.serialize_calls += 1
            return actual_serializer(*args)
        monkeypatch.setattr(PLV, "VIDEO_FD", 200)
        monkeypatch.setattr(PLV, "CANCEL_FD", 201)
        monkeypatch.setattr(PLV, "END_FD", 202)
        monkeypatch.setattr(PLV, "free_fd", lambda _: True)
        monkeypatch.setattr(PLV, "fresh_fifo", lambda path: session.events.append("fresh FIFO double"))
        monkeypatch.setattr(PLV, "game_env", lambda: {})
        monkeypatch.setattr(PLV, "game_pid", lambda *_: 4321)
        monkeypatch.setattr(PLV, "stop_game", stop)
        monkeypatch.setattr(PLV, "drain_pipe", drain)
        monkeypatch.setattr(PLV, "open", opened, raising=False)
        monkeypatch.setattr(PLV, "write_arrivals_ns", serialize)
        monkeypatch.setattr(PLV.subprocess, "Popen", popen)
        monkeypatch.setattr(PLV.threading, "Event", Event)
        monkeypatch.setattr(PLV.threading, "Lock", Lock)
        monkeypatch.setattr(PLV.threading, "Thread", Thread)
        monkeypatch.setattr(PLV.os, "open", open_fd)
        monkeypatch.setattr(PLV.os, "pipe", lambda: next(pairs))
        monkeypatch.setattr(PLV.os, "dup2", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(PLV.os, "close", close)
        monkeypatch.setattr(PLV.os, "write", written)
        monkeypatch.setattr(PLV.PL, "open_pipe", lambda _: 101)
        monkeypatch.setattr(PLV.PL, "relay", relay)

    def complete_pending(self):
        pending, self.pending = self.pending, []
        for name in ("run_player", "wait_game"):
            for thread in pending:
                if thread.name == name:
                    try:
                        thread.target(*thread.args)
                    except BaseException as error:
                        self.async_errors.append(f"{type(error).__name__}: {error}")
                    thread.completed = True

    def invoke(self):
        try:
            self.result = PLV.run_session(self.plan)
        except BaseException as error:
            self.caught = error
        evidence = {"method": "authored run_session doubles, actual serializer files",
                    "events": self.events, "async_errors": self.async_errors,
                    "caught_type": type(self.caught).__name__ if self.caught else None,
                    "caught_text": str(self.caught) if self.caught else None,
                    "caught_is_primary": self.primary is not None and self.caught is self.primary,
                    "serialize_calls": self.serialize_calls,
                    "diagnostics": self.diagnostics, "result": self.result}
        (self.directory / "fixture-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
        return self

    def report(self):
        return json.loads(self.paths["relay_report"].read_text())

    def terminal_attempts(self):
        return [event for event in self.events if event in
                ("close game stream", "join player watcher", "close cancel pipe",
                 "close input hold", "close game log", "close player log")]


TERMINALS = ["close game stream", "join player watcher", "close cancel pipe",
             "close input hold", "close game log", "close player log"]


def assert_terminal_cleanup_before_serialization(session):
    assert session.terminal_attempts() == TERMINALS
    assert session.events.index("serialize actual arrival files") > session.events.index("close player log")
    assert session.serialize_calls == 1
    assert session.async_errors == []


def test_session_serializes_after_cleanup_and_observes_player_after_game_wait(monkeypatch, tmp_path):
    session = Session(monkeypatch, tmp_path).invoke()
    assert session.caught is None
    assert_terminal_cleanup_before_serialization(session)
    assert session.events.index("poll player") > session.events.index("wait game")
    assert "wait player stream" not in session.events
    assert session.report()["ok"] is True
    assert session.paths["relay_frames"].read_text() == "frame\tarrived_s\n1\t10.000001500\n"
    assert session.result["arrival_diagnostics"] == session.report()


@pytest.mark.parametrize("failure", ["start drainer", "stop game", "join drainer",
                                    "wait game", "close game stream", "poll player",
                                    "cancel player", "wait player", "join player watcher",
                                    "close cancel pipe", "close input hold",
                                    "close game log", "close player log"])
def test_primary_relay_error_survives_each_cleanup_failure_with_published_details(
        monkeypatch, tmp_path, failure):
    original = OSError("authored original relay exception")
    session = Session(monkeypatch, tmp_path, primary=original, failures=(failure,),
                      alive=failure in ("poll player", "cancel player", "wait player")).invoke()
    assert session.caught is original
    assert_terminal_cleanup_before_serialization(session)
    report = session.report()
    assert report["ok"] is False and report["error"] == "authored original relay error"
    details = report["session_cleanup_errors"]
    assert details[0] == {"operation": failure, "error": f"OSError: authored {failure} failure"}
    assert failure in session.fired
    assert "start drainer" in session.events and "stop game" in session.events and "join drainer" in session.events


@pytest.mark.parametrize("failure", ["wait game", "close game stream", "poll player",
                                    "wait player stream", "cancel player", "wait player",
                                    "join player watcher", "close cancel pipe",
                                    "close input hold", "close game log", "close player log"])
def test_cleanup_only_error_is_rethrown_after_all_closes_and_diagnostic_publication(
        monkeypatch, tmp_path, failure):
    session = Session(monkeypatch, tmp_path, failures=(failure,),
                      alive=failure in ("poll player", "wait player stream", "cancel player", "wait player")).invoke()
    assert session.caught is session.failures[failure]
    assert_terminal_cleanup_before_serialization(session)
    report = session.report()
    assert report["ok"] is False
    assert report["session_cleanup_errors"][0] == {
        "operation": failure, "error": f"OSError: authored {failure} failure"}
    assert report["error"] == f"session cleanup {failure}: OSError: authored {failure} failure"


def test_multiple_close_failures_all_attempted_and_retained_in_order(monkeypatch, tmp_path):
    failures = ("close game stream", "close cancel pipe", "close input hold",
                "close game log", "close player log")
    session = Session(monkeypatch, tmp_path, failures=failures).invoke()
    assert session.caught is session.failures["close game stream"]
    assert_terminal_cleanup_before_serialization(session)
    assert session.report()["session_cleanup_errors"] == [
        {"operation": "close game stream", "error": "OSError: authored close game stream failure"},
        {"operation": "close cancel pipe", "error": "OSError: authored close cancel pipe failure"},
        {"operation": "close input hold", "error": "OSError: authored close input hold failure"},
        {"operation": "close game log", "error": "OSError: authored close game log failure"},
        {"operation": "close player log", "error": "OSError: authored close player log failure"}]


@pytest.mark.parametrize("primary,cleanup", [(True, False), (False, True), (True, True)])
def test_serializer_failure_does_not_mask_primary_or_cleanup_error(monkeypatch, tmp_path, primary, cleanup):
    original = OSError("authored original relay exception") if primary else None
    session = Session(monkeypatch, tmp_path, primary=original,
                      failures=("close game stream",) if cleanup else (),
                      serializer_failure=True).invoke()
    assert session.caught is (original if primary else session.failures["close game stream"])
    assert_terminal_cleanup_before_serialization(session)
    assert not session.paths["relay_report"].exists()
    assert session.paths["relay_frames"].read_text() == "frame\tarrived_s\n1\t10.000001500\n"
    assert (tmp_path / "relay-diagnostic-error.txt").read_text() == (
        "OSError: authored serializer publication failure\n")


def test_serializer_only_error_is_rethrown_after_cleanup(monkeypatch, tmp_path):
    session = Session(monkeypatch, tmp_path, serializer_failure=True).invoke()
    assert session.caught is session.serializer_error
    assert_terminal_cleanup_before_serialization(session)
    assert not session.paths["relay_report"].exists()


def test_diagnostic_retention_failure_cannot_mask_original_error(monkeypatch, tmp_path, capsys):
    original = OSError("authored original relay exception")
    session = Session(monkeypatch, tmp_path, primary=original, serializer_failure=True,
                      retention_failure=True).invoke()
    assert session.caught is original
    assert_terminal_cleanup_before_serialization(session)
    assert "diagnostic retention failed: authored diagnostic retention failure" in capsys.readouterr().err


def test_keyboard_interrupt_keeps_graceful_session_semantics_after_serialization(monkeypatch, tmp_path):
    session = Session(monkeypatch, tmp_path, primary=KeyboardInterrupt()).invoke()
    assert session.caught is None and session.result["interrupted"] is True
    assert_terminal_cleanup_before_serialization(session)
    assert session.report()["ok"] is False


def test_keyboard_interrupt_with_cleanup_failure_rethrows_cleanup_after_serialization(monkeypatch, tmp_path):
    session = Session(monkeypatch, tmp_path, primary=KeyboardInterrupt(),
                      failures=("close game log",)).invoke()
    assert session.caught is session.failures["close game log"]
    assert_terminal_cleanup_before_serialization(session)
    assert session.report()["ok"] is False


def test_disabled_diagnostics_keeps_legacy_cleanup_without_new_serialization(monkeypatch, tmp_path):
    session = Session(monkeypatch, tmp_path, receipt=False).invoke()
    assert session.caught is None and session.diagnostics is None
    assert session.serialize_calls == 0 and "arrival_diagnostics" not in session.result
    assert session.terminal_attempts() == TERMINALS
    assert session.paths["relay_frames"].read_text() == "frame\tarrived_s\n1\t10.000001\n"


def test_disabled_diagnostics_keeps_historical_close_failure_behavior(monkeypatch, tmp_path):
    session = Session(monkeypatch, tmp_path, receipt=False,
                      failures=("close game stream",)).invoke()
    assert session.caught is session.failures["close game stream"]
    assert session.terminal_attempts() == ["close game stream"]
    assert session.serialize_calls == 0
