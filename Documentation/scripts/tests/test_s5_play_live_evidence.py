"""A crafted play_live session whose input latency evidence joins, for the play clause's tests.

Documentation/scripts/check_s5_exit.py's play clause recomputes a session's input latency
from four logs and two producer reports (latency_evidence), so every crafted session the
clause takes must carry them, consistent with its per-tick table, its player report and its
run record. This module builds them; test_s5_exit.py, test_s5_play_receipt.py and
test_s5_input.py craft their sessions with it, and the tests below hold the crafted session
to the join's literal result.

The session: a tick every TICK_NS from BASE_NS, frame k presented in tick k, PRESSES presses
of one key between entering the stage and the goal, each held HOLD ticks, every press and
release its own FIFO record read by the tick's one poll. Every stamp sits at a fixed offset
from its tick's start (OFFSETS_NS), so each presented press measures the same latency,
press to presented frame TOTAL_US, components COMPONENTS_US. No game, player or window runs.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_s5_exit as X  # noqa: E402

TICK_NS = 16_667_000          # a 60 Hz tick, a whole number of microseconds
BASE_NS = 1_000_000_000
HOLD = 3                      # ticks a key is held
PRESSES = 24
JUMP_DIK, JUMP_SCANCODE = 0x24, 13
# Each stamp's offset from its tick's start, in ns: the key event, its dequeue, its send, the
# poll that reads it, its frame whole at the relay, and the Present called and returned.
OFFSETS_NS = {"event": -14_000_000, "handled": -12_000_000, "sent": -11_500_000,
              "polled": -4_000_000, "arrived": 3_000_000, "called": 5_000_000,
              "returned": 6_000_000}
COMPONENTS_US = {"queue": 2000, "send": 500, "poll_wait": 7500, "game": 7000, "player": 2000,
                 "present_call": 1000}
TOTAL_US, FROM_DEQUEUE_US = 20000, 18000
STREAM_BYTES = 123_456
RECORDS = {"call": 4_181_795, "d3dx": 12, "frame": 0, "note": 0, "end": 1, "audio": 9000,
           "unknown": 0, "seq_gaps": 0, "after_end": 0}


def stamp(ns: int) -> str:
    """CLOCK_MONOTONIC seconds as the logs write them: nine decimals."""
    return f"{ns // 1_000_000_000}.{ns % 1_000_000_000:09d}"


def at(tick: int, what: str) -> int:
    return BASE_NS + tick * TICK_NS + OFFSETS_NS[what]


def press_ticks(first: int = 110, presses: int = PRESSES, every: int = 8) -> list[int]:
    """The ticks each press goes down in: `presses` of them, `every` ticks apart."""
    return [first + every * i for i in range(presses)]


def keys_column(ticks: list[int], tick: int) -> int:
    """The keys the runtime applied over `tick`'s poll: the key, while it is held."""
    return int(any(t <= tick < t + HOLD for t in ticks))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write(path: Path, data: str | dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=1) + "\n").encode() if isinstance(data, dict)
                     else data.encode())
    return path


def logs(ticks: list[int], frames: int) -> dict[str, str]:
    """The four raw logs for presses going down at `ticks`, the stream `frames` long."""
    keys = ["\t".join(X.PLAY_KEYS)]
    polls = ["\t".join(X.PLAY_POLLS)]
    change = seq = 0
    for down_tick in ticks:
        for tick, down in ((down_tick, 1), (down_tick + HOLD, 0)):
            change += 1
            keys.append("\t".join(map(str, (
                change, JUMP_SCANCODE, JUMP_DIK, down, 0,
                OFFSETS_NS["handled"] - OFFSETS_NS["event"], stamp(at(tick, "handled")),
                seq, stamp(at(tick, "sent")), "sent"))))
            polls.append("\t".join(map(str, (tick - 1, seq, seq, 1, tick, tick - 1,
                                              at(tick, "polled")))))
            seq += 1
    presents = ["\t".join(X.PLAY_PRESENTS)] + [
        "\t".join(map(str, (k, (k - 1) * 16667, stamp(at(k, "called")), stamp(at(k, "returned")))))
        for k in range(1, frames + 1)]
    arrivals = ["\t".join(X.RELAY_FRAMES)] + [f"{k}\t{stamp(at(k, 'arrived'))}"
                                              for k in range(1, frames + 1)]
    return {name: "\n".join(lines) + "\n" for name, lines in
            (("keys", keys), ("polls", polls), ("presents", presents), ("relay_frames", arrivals))}


def headers(frames: int) -> int:
    """The stream's records, a FRAME for each frame, as the relay and the session count them."""
    return sum(RECORDS[k] for k in ("call", "d3dx", "note", "end", "audio")) + frames


def report_fields(rel: dict[str, str], presses: int, frames: int, key_events: int | None = None
                  ) -> dict:
    """The player's report members the latency join reads, for `presses` presses (each a press
    and a release, each its own record) and a stream `frames` long; `rel` names the logs as
    the player was given them."""
    records = 2 * presses
    return {
        "complete": True, "pass": True, "exit_status": 0, "failures": [], "stage": 3,
        "presents": frames, "frame_records": frames, "presents_without_frame_record": 0,
        "records": RECORDS | {"frame": frames}, "stream_records_read_to_end": True,
        "stream_sha256_after_stop": False, "stream_file_bytes_read": STREAM_BYTES,
        "stream_bytes": STREAM_BYTES,
        "options": {"max_frames": 0, "allow_truncated": False, "stats_only": False,
                    "hidden": False},
        "latency_diagnostics": {
            "method": "v1", "clock": "CLOCK_MONOTONIC", "event_age_clock": "SDL_GetTicksNS",
            "event_age_unit": "ns", "handled_at": "after successful SDL_PollEvent",
            "presented_at": "host Present returned", "requested_keys": True,
            "requested_presents": True, "requested_synthetic": False,
            "sender_start_attempted": True, "sender_started": True,
            "key_log_header_accepted": True, "present_log_header_accepted": True,
            "key_log_opened": True, "key_log_closed": True, "key_log_owner_io_errors": 0,
            "future_key_timestamps": 0, "event_clock_errors": 0, "invalid_key_timestamps": 0,
            "present_clock_errors": 0, "report_io_errors": 0, "closed": True,
            "evidence_ready": True, "error": None},
        "present_log": {"path": rel["presents"], "rows": frames, "opened": True, "closed": True,
                        "io_errors": 0, "dropped_rows": 0, "error": None},
        "host_input": {"path": rel.get("fifo", "input.fifo"), "started": True, "open": False,
                       "key_events": records if key_events is None else key_events,
                       "changes": records, "records_written": records, "retries": 0,
                       "write_errors": 0, "last_seq": records - 1, "held_now": 0,
                       "owed": False, "synthetic_key_events": 0,
                       "key_log": {"path": rel["keys"], "rows": records, "sent": records,
                                   "lost": 0, "unsent": 0, "io_errors": 0, "error": None},
                       "last_error": None},
        "end_request": {"fd": True, "requested": False, "by": None, "after_frame": None,
                        "at_s": None, "written": False, "closes_after": 0, "error": None}}


def run_fields(polls: int, frames: int) -> dict:
    """The run record members the join reads beyond the keyboard's."""
    return {"d3d9": {"frames": {"present": frames}},
            "pump": {"status": 0, "iterations": polls, "quit_by_host": 1, "quit_by_guest": 0}}


def sidecars(rel: dict[str, str], presses: int, frames: int, last_poll: int) -> dict[str, dict]:
    """The observer's report of the poll log and the relay's of its arrivals."""
    common = {"method": "v1", "clock": "CLOCK_MONOTONIC", "unit": "ns", "requested": True,
              "opened": True, "finalized": True, "closed": True, "ok": True,
              "clock_errors": 0, "io_errors": 0, "error": None}
    records = 2 * presses
    return {
        "poll_report": {"schema": "sa2-s5-play-polls/v1", **common, "path": rel["polls"],
                        "callbacks": records, "rows": records, "records_observed": records,
                        "records_logged": records, "first_seq": 0, "last_seq": records - 1,
                        "last_poll": last_poll, "dropped_rows": 0},
        "relay_report": {"schema": "sa2-s5-relay-arrivals/v1", **common,
                         "path": rel["relay_frames"], "rows": frames,
                         "clock_info": {"implementation": "clock_gettime(CLOCK_MONOTONIC)",
                                        "monotonic": True, "adjustable": False,
                                        "resolution": 1e-09},
                         "complete_frames": frames, "record_headers_seen": headers(frames),
                         "bytes_read": STREAM_BYTES, "transport_end": "the writer closed the pipe",
                         "framing_problem": None, "feed_ended": True, "feed_error": None,
                         "session_cleanup_errors": None, "dropped_frames": 0}}


def live_session(run: dict, frames: int, scene: str = "metalharbor") -> dict:
    """play_live's play-live.json for the session, the members the join reads."""
    return {"schema": "sa2-s5-play-live/v1", "scene": scene, "build": "present-play-mh.x",
            "profile": "default",
            "commands": {"game": ["loop_run"], "player": ["sa2_player"], "player_env": ""},
            "runner_sha256": "a" * 64,
            "latency_transport_source": {"path": "tools/s5/present_live.py", "sha256": "b" * 64},
            "ticks_observer": {"path": "sa2_play_ticks.c", "sha256": "c" * 64},
            "limits": {"stream_cap_bytes": 200 * 10 ** 9, "disk_floor_bytes": 50 * 10 ** 9},
            "run_record": {"ok": True, "pump": dict(run["pump"])},
            "session": {"game_rc": 0, "player_rc": 0, "interrupted": False,
                        "player_cancelled": False, "player_error": None, "limit": None,
                        "stop": {"how": "SIGTERM: WM_QUIT at the game's next keyboard poll"},
                        "end": {"by": "the goal"},
                        "relay": {"end": "the writer closed the pipe", "bytes": STREAM_BYTES,
                                  "records": headers(frames), "frames_arrived": frames,
                                  "framing_problem": None, "feed_error": None}}}


def summary(presses: int, stage: int | None = None) -> dict:
    """The receipt's input_latency for `presses` presses, every one presented, `stage` of them
    (by default all) between entering the stage and the goal."""
    def same(value: int) -> dict:
        return {"min": value, "max": value, "mean": value, "p50": value, "p90": value,
                "p99": value}

    def part(n: int) -> dict:
        return {"presses": n,
                "by_status": {"presented": n, "lost": 0, "unsent": 0, "unpresented": 0},
                "total_us": same(TOTAL_US), "from_dequeue_us": same(FROM_DEQUEUE_US),
                "components_us": {name: same(us) for name, us in COMPONENTS_US.items()}}
    return {"method": "v1", "clock": "CLOCK_MONOTONIC", "press_at": "SDL key event timestamp",
            "presented_at": "host Present returned",
            "summary": part(presses) | {"stage": part(presses if stage is None else stage)}}


def session(where: Path, root: Path, ticks: list[int], frames: int) -> dict:
    """The four logs and two sidecars written in `where` for a session `frames` long whose
    presses go down at `ticks`; their paths below `root` as the player and producers name
    them, and each file's path."""
    paths = {"keys": where / "player-keys.tsv", "polls": where / "play-polls.tsv",
             "presents": where / "player-presents.tsv",
             "relay_frames": where / "relay-frames.tsv",
             "poll_report": where / "play-polls-report.json",
             "relay_report": where / "relay-frames-report.json"}
    rel = {name: path.relative_to(root).as_posix() for name, path in paths.items()}
    for name, text in logs(ticks, frames).items():
        write(paths[name], text)
    last_poll = max(ticks) + HOLD - 1
    for name, doc in sidecars(rel, len(ticks), frames, last_poll).items():
        write(paths[name], doc)
    return {"paths": paths, "rel": rel}


TICKS_HEADER = "\t".join(X.PLAY_TICKS)


def table(ticks: list[int], goal: int | None = 300, entry: int = 101, end: int = 400) -> str:
    """The game's per-tick table as the session's observer writes it: loading to entry-1,
    Metal Harbor (level 10) from `entry`, the key held over each press, the goal latch set
    from `goal` (never with None), a Present per tick."""
    lines = [TICKS_HEADER]
    for tick in range(1, end + 1):
        inside = tick >= entry
        lines.append("\t".join(str(v) for v in (
            tick, "0x000a", "0x00000005" if inside else "0x00000004",
            "0x0010" if inside else "0x0001", "0x01ab2000" if inside else "0x00000000",
            "0x01" if goal is not None and tick >= goal else "0x00", tick,
            keys_column(ticks, tick))))
    return "\n".join(lines) + "\n"


def stream_meta(run_id: str, host: str = "dxvk-native") -> str:
    meta = json.dumps({"audio": True, "build": "present-play-mh.x", "host": host,
                       "interactive": True, "run": run_id, "runner": X.PLAY_TOOL,
                       "scene": X.STAGE, "transport": "fifo"}, separators=(",", ":"),
                      sort_keys=True)
    return json.dumps({"schema": "sa2-d3d9-stream/v2", "writer": "host/sa2_present_rec.c",
                       "meta": meta})


def play_live_session(p: dict, root: Path, run_id: str, goal: int | None = 300,
                      ticks: list[int] | None = None, host: str = "dxvk-native") -> dict:
    """What tools/s5/play_live.py's run_session leaves in the session directory `p` (its
    paths()) for a dxvk-native Metal Harbor session the goal ended, whose evidence the play
    clause takes; and what run_session returns for it. The DXVK library the report names is
    written in the session's directory."""
    ticks = press_ticks() if ticks is None else ticks
    frames = 400
    write(p["ticks"], table(ticks, goal))
    made = session(p["out"], root, ticks, frames)
    lib = p["out"] / "libdxvk_d3d9.so.0"
    lib.write_bytes(b"a DXVK library")
    rel = dict(made["rel"], fifo=p["input_fifo"].relative_to(root).as_posix())
    report = {"tool": "host/player sa2_player", "phase": "done",
              **report_fields(rel, len(ticks), frames),
              "present_failures": 0, "readback_failures": 0, "errors": 0,
              "calls": {"handled": 4181795, "skipped_by_stage": 0, "not_handled": 0},
              "textures": {"placeholder_binds": 0}, "host_failures": {"count": 0},
              "stream_problems": {"count": 0}, "notes": {"count": 0},
              "stream_header": stream_meta(run_id, host),
              "window": {"frames_with_flags": {"occluded": 0, "hidden": 0, "minimized": 0,
                                               "input_focus": frames}},
              "files": {"player": {"path": "/proc/self/exe", "sha256": "e1" * 32},
                        "libdxvk_d3d9": {"path": str(lib), "real_path": str(lib),
                                         "sha256": sha(lib.read_bytes())}},
              "host_identity": {"dxvk_file_verified": True}}
    write(p["player"]["report"], report)
    keys = sum(keys_column(ticks, t) for t in range(1, frames + 1))
    write(p["run"], {"ok": True, "dinput": {"host_input": {
        "source": "fifo", "polls": frames + 1, "keys_written": keys, "script_lines": 0,
        "records": 2 * len(ticks), "last_seq": 2 * len(ticks) - 1, "seq_gaps": 0,
        "partial_dropped": 0}}, **run_fields(frames + 1, frames)})
    return {"interrupted": False, "game_rc": 0, "player_rc": 0, "player_error": None,
            "player_cancelled": False,
            "stop": {"how": "SIGTERM: WM_QUIT at the game's next keyboard poll", "pid": 1,
                     "signalled": [1], "seconds": 0.4},
            "limit": None, "end": {"by": "the goal", "goal_tick": goal, "at_s": 9.0},
            "seconds": {},
            "relay": {"end": "the writer closed the pipe", "bytes": STREAM_BYTES,
                      "records": headers(frames), "frames_arrived": frames,
                      "framing_problem": None, "max_gap_s": 0.1, "held": None,
                      "feed_error": None}}


# ------------------------------------------------------------------ the crafted session


def crafted(tmp_path: Path, ticks: list[int] | None = None) -> dict:
    """latency_evidence's arguments for a crafted session: 400 ticks, the stage from 101,
    the goal at 300, PRESSES presses from tick 110 (or presses going down at `ticks`)."""
    root = tmp_path / "root"
    where = root / "private" / "session"
    ticks = press_ticks() if ticks is None else ticks
    made = session(where, root, ticks, 400)
    rows = [{"tick": t, "frames": t, "keys": keys_column(ticks, t)} for t in range(1, 401)]
    report = report_fields(made["rel"], len(ticks), 400)
    report["stream_header"] = json.dumps({"meta": json.dumps(
        {"run": "crafted", "runner": X.PLAY_TOOL, "interactive": True, "scene": X.STAGE,
         "host": "dxvk-native"})})
    for name in X.REPORT_ZERO:
        node = report
        *parts, last = name.split(".")
        for part in parts:
            node = node.setdefault(part, {})
        node[last] = 0
    report["window"] = {"frames_with_flags": {"hidden": 0, "minimized": 0, "occluded": 0}}
    run = {"ok": True, "dinput": {"host_input": {
        "source": "fifo", "script_lines": 0, "seq_gaps": 0, "partial_dropped": 0,
        "records": 2 * len(ticks), "last_seq": 2 * len(ticks) - 1, "polls": 401,
        "keys_written": sum(row["keys"] for row in rows)}}} | run_fields(401, 400)
    files = {name: X.HeldLatencyFile(made["rel"][name], sha(path.read_bytes()), path.read_bytes())
             for name, path in made["paths"].items()}
    return {"root": root, "what": "crafted", "host": "dxvk-native", "files": files,
            "report": report, "run": run, "live_session": live_session(run, 400),
            "ticks": rows, "entry": 100, "goal": 299}


def test_the_crafted_session_joins_to_its_literal_latency(tmp_path: Path) -> None:
    got = X.latency_evidence(**crafted(tmp_path))
    assert X.latency_summary(got) == summary(PRESSES)
    assert [p["tick"] for p in got["presses"]] == press_ticks()
    assert {p["status"] for p in got["presses"]} == {"presented"}
    first = got["presses"][0]
    assert {k: first[k] for k in ("seq", "poll", "tick", "frame", "dik")} == {
        "seq": 0, "poll": 109, "tick": 110, "frame": 110, "dik": JUMP_DIK}


def test_the_crafted_components_sum_to_the_total() -> None:
    assert sum(COMPONENTS_US.values()) == TOTAL_US
    assert TOTAL_US - COMPONENTS_US["queue"] == FROM_DEQUEUE_US
    steps = list(OFFSETS_NS.values())
    assert steps == sorted(steps)
    assert (steps[-1] - steps[0]) // 1000 == TOTAL_US


def test_the_crafted_session_meets_the_floor_and_one_press_fewer_does_not(tmp_path: Path) -> None:
    b = crafted(tmp_path)
    assert PRESSES > X.MIN_LATENCY_PRESSES
    b["entry"], b["goal"] = 109 + 8 * (PRESSES - X.MIN_LATENCY_PRESSES), 299   # tick 110 + 8i, index - 1
    assert X.latency_evidence(**b)["summary"]["stage"]["presses"] == X.MIN_LATENCY_PRESSES
    b["entry"] += 8
    try:
        X.latency_evidence(**b)
    except X.Refused as refused:
        assert str(refused) == (f"crafted: {X.MIN_LATENCY_PRESSES - 1} presses presented between "
                                f"entering the stage and the goal, under {X.MIN_LATENCY_PRESSES}")
    else:
        raise AssertionError("one press under the floor was taken")


def test_the_stage_is_the_presses_from_entering_it_to_the_goal_both_included(tmp_path: Path
                                                                            ) -> None:
    """Presses polled in the load (20, 97: the stage is entered at tick 101), on the stage's
    first tick (101) and the goal's (300), and past the goal (304, 350), each released HOLD
    ticks later: the stage's summary, which the floor reads, holds the two on its edges and
    the crafted session's, and the whole session's every press."""
    edges = [20, 97, 101] + press_ticks() + [300, 304, 350]
    b = crafted(tmp_path, edges)
    b["entry"], b["goal"] = 100, 299                     # ticks 101 and 300, zero-based
    got = X.latency_evidence(**b)
    assert X.latency_summary(got) == summary(len(edges), PRESSES + 2)
    inside = [p["tick"] for p in got["presses"] if b["entry"] <= p["tick"] - 1 <= b["goal"]]
    assert inside == [101] + press_ticks() + [300]
