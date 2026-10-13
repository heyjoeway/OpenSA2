"""S5's exit predicates (check_s5_exit.py), in both directions, on crafted evidence.

G5's four S5-exit criteria read receipts that, audible's aside, no session has made yet (and M7 reads a
row that is not met), so `check_criterion_clauses.py` defers most of their
falsifiers by its own design. The passing direction, and every way each check
can fail, is exercised here on literal inputs written under tmp_path: a crafted
project root holding the real selection record, a registry, tools/pins.json
with the crafted DXVK library's sha256, METRICS.md, the receipts and the
evidence they name. Nothing tracked is written.

The last group holds the live `Documentation/gate-criteria.json` to the
module: the four texts (and design 5's exit sentence they quote), the predicate
each check calls, each check run as gate_report.py runs it, the falsifiers a
criterion owes once its receipt exists, and what the notes quote and state.
Another group holds the module's constants to their sources: the Mod Loader's
headers, stage_boot.py, the player's and the runtime's own output formats.
"""

from __future__ import annotations

import array
import copy
import hashlib
import json
import math
import re
import shlex
import shutil
import struct
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
sys.path.insert(0, str(SCRIPTS))
import check_s5_exit as X  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_s5_play_live_evidence as E  # noqa: E402  a session whose latency evidence joins

# The live criteria. SA2_GATE_CRITERIA_UNDER_TEST points the last group at a scratch copy that a
# unit has rewritten, before the integration applies the rewrite to the live file.
CRITERIA_FILE = Path(os.environ.get("SA2_GATE_CRITERIA_UNDER_TEST")
                     or ROOT / "Documentation" / "gate-criteria.json")
MODLOADER = ROOT / "private/predecessor-tools/sa2-mod-loader/SA2ModLoader/include"
LEVEL = 10                     # the registry's Metal Harbor level in these fixtures
TICK_US = 16667                # one 60 Hz tick of virtual time, rounded


# ------------------------------------------------------------------ a crafted root


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": sha(path)}


def write_json(path: Path, doc: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1) + "\n")
    return path


def write_tsv(path: Path, header: tuple[str, ...], rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(header)] + ["\t".join(str(row[c]) for c in header) for row in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def patched(doc: dict, change: dict) -> dict:
    """`doc` with each change applied: `a__b` sets doc["a"]["b"], a plain key replaces."""
    for dotted, value in change.items():
        node = doc
        *path, last = dotted.split("__")
        for part in path:
            node = node[part]
        node[last] = value
    return doc


def renderer_bytes(host: str) -> bytes:
    """What the fixtures write as each host's renderer: DXVK's library, or the GPU player."""
    return f"renderer of {host}".encode()


PIN = hashlib.sha256(renderer_bytes("dxvk-native")).hexdigest()   # the crafted DXVK library
GPU_PLAYER = hashlib.sha256(renderer_bytes("sdl3-gpu")).hexdigest()


def project(tmp_path: Path, level: int = LEVEL) -> Path:
    """A root with the real selection record, a registry naming boot and Metal Harbor, and the
    real tools/pins.json pinning the crafted DXVK library in place of the real one."""
    root = tmp_path / "root"
    record = root / X.SELECTION
    record.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / X.SELECTION, record)
    write_json(root / X.REGISTRY, {"schema": "sa2-replay-registry/v1", "replays": [
        {"name": "boot"},
        {"name": "metalharbor", "stage": {"name": "metalharbor", "level": level}}]})
    pins = (ROOT / X.PINS).read_text()
    real = json.loads(pins)["tools"]["dxvk"]["library_sha256"]
    assert pins.count(real) == 1
    (root / X.PINS).parent.mkdir(parents=True, exist_ok=True)
    (root / X.PINS).write_text(pins.replace(real, PIN))
    return root


def stamped(tool: str, body: dict) -> dict:
    return {"schema": None, "generated_by": tool,
            "inputs": {"pipeline": {tool: "ab" * 32}}, **body}


def edit_record(root: Path, old: str, new: str) -> None:
    path = root / X.SELECTION
    text = path.read_text()
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new))


def host_files(host: str) -> dict:
    """The report's pins and identity checks as each host's player writes them."""
    if host == "dxvk-native":
        return {"files": {"player": {"path": "/proc/self/exe", "sha256": "e1" * 32},
                          "libdxvk_d3d9": {"path": "/x/libdxvk_d3d9.so.0", "sha256": PIN}},
                "host_identity": {"dxvk_file_verified": True}}
    return {"files": {"player": {"path": "/proc/self/exe", "sha256": GPU_PLAYER},
                      "libdxvk_d3d9": {"path": "", "real_path": "", "sha256": None}},
            "host_identity": {"dxvk_file_verified": False}}


def stream_header(run_id: str, host: str = "dxvk-native") -> str:
    """The recorder's header as the player reports it: JSON text whose meta is JSON text,
    naming the session's host as play_live's SA2_REC_META does."""
    meta = json.dumps({"audio": True, "build": "present-play-mh.x", "host": host,
                       "interactive": True, "run": run_id, "runner": "tools/s5/play_live.py",
                       "scene": "metalharbor", "transport": "fifo"})
    return json.dumps({"schema": "sa2-d3d9-stream/v1", "writer": "host/sa2_present_rec.c",
                       "meta": meta})


# ------------------------------------------------------------------ clause 2: M7

HEAD = "| Metric | Value | Denominator source | Status |\n|---|---|---|---|\n"
CEILING = "denominator is a floor -- at most {} of {} vtable-shaped call sites could still add to it"
MISSED = ", and the scan missed {} a driven run called"
MET_M7 = "9/9 (100.0%); " + CEILING.format(0, 9) + MISSED.format(0)
# The user's ruling of 2026-10-09 (item 1): neither the ceiling nor the misses is a pass condition.
MET_M7_NONZERO = "94/94 (100.0%); " + CEILING.format(4080, 4222) + MISSED.format(9)


def metrics(tmp_path: Path, value: str | None) -> Path:
    path = tmp_path / "METRICS.md"
    row = "" if value is None else f"| M7 device | {value} | analyzer call-site scan | measured |\n"
    path.write_text(HEAD + "| M6 stages | booting 1/66 (1.5%) | x | measured |\n" + row)
    return path


@pytest.mark.parametrize("value", [MET_M7, MET_M7_NONZERO,
                                   "9/9 (100.0%); " + CEILING.format(0, 9) + MISSED.format(2),
                                   "67/67 (100.0%); " + CEILING.format(4126, 4263) + MISSED.format(0)])
def test_m7_met_on_a_whole_ratio_stating_its_ceiling_and_misses_once(tmp_path: Path, value: str) -> None:
    """The user's ruling of 2026-10-09 (Documentation/decisions/2026-10-09-S5-user-rulings.md,
    item 1): "Drop "ceiling zero" and "missed zero" as G5 pass conditions." A whole ratio is met
    whatever the ceiling and the misses read, as long as the row states each once."""
    result = X.m7(metrics(tmp_path, value))
    assert (result.met, result.detail) == (True, value)


@pytest.mark.parametrize("value,why", [
    ("58/67 (86.6%); " + CEILING.format(4126, 4263) + MISSED.format(9), "the row at development_snapshot"),
    ("67/67 (100.0%)", "whole, with the ceiling and the misses unstated"),
    ("67/67 (100.0%); " + CEILING.format(4126, 4263), "whole, with the misses unstated"),
    ("67/67 (100.0%)" + MISSED.format(0), "whole, with the ceiling unstated"),
    ("67/67 (100.0%); " + CEILING.format(0, 9) + "; " + CEILING.format(3, 9) + MISSED.format(0),
     "the ceiling stated twice"),
    ("67/67 (100.0%); " + CEILING.format(0, 9) + MISSED.format(0) + MISSED.format(2),
     "the misses stated twice"),
    ("9/9 (100.0%); " + CEILING.format(0, 0) + MISSED.format(0), "a ceiling over no call sites"),
    ("9/9 (100.0%); " + CEILING.format(10, 9) + MISSED.format(0), "a ceiling above its call sites"),
    ("0/0: no methods called; " + CEILING.format(0, 9) + MISSED.format(0), "an empty denominator"),
    ("8/9 (88.9%); " + CEILING.format(0, 9) + MISSED.format(0), "one used method unimplemented"),
    ("not measured (input absent: private/generated/device-status.json)", "not measured"),
    (None, "no M7 row"),
])
def test_m7_unmet(tmp_path: Path, value: str | None, why: str) -> None:
    assert X.m7(metrics(tmp_path, value)).met is False, why


def test_m7_names_why_a_row_is_unmet(tmp_path: Path) -> None:
    unstated = "67/67 (100.0%); " + CEILING.format(4126, 4263)
    assert X.m7(metrics(tmp_path, unstated)).detail == (
        f"{unstated} (the row states what the scan missed 0 times, not once, so the instrument's "
        f"misses are unread)")
    assert X.m7(metrics(tmp_path, "67/67 (100.0%)" + MISSED.format(0))).detail == (
        "67/67 (100.0%), and the scan missed 0 a driven run called (the row states the scan's "
        "ceiling 0 times, not once, so the instrument's ceiling is unread)")


def test_m7_reads_the_first_m7_row_alone(tmp_path: Path) -> None:
    """The met state in gate-criteria.json inserts a row ahead and renames the measured one."""
    path = metrics(tmp_path, MET_M7)
    path.write_text(path.read_text() + "| M7 device as measured | 58/67 (86.6%) | x | measured |\n")
    assert X.m7(path).met is True
    path.write_text(path.read_text().replace("| M7 device | ", "| M7 device | 8/9 (88.9%); "
                                              + CEILING.format(0, 9) + MISSED.format(0)
                                              + " | x | measured |\n| M7 device displaced | ", 1))
    assert X.m7(path).met is False


def test_m7_with_no_metrics_file(tmp_path: Path) -> None:
    assert X.m7(tmp_path / "METRICS.md").met is False


# ------------------------------------------------------------------ clause 1: play

ENTRY, GOAL, END = 101, 300, 400
# Each host is played by hand in its own window, so the sessions differ: here, by when the
# keys were pressed. Each window presses one key E.PRESSES times between entering the stage
# and the goal, every press held E.HOLD ticks, each press and release a FIFO record of its own.
PRESS_TICKS = {"dxvk-native": E.press_ticks(110), "sdl3-gpu": E.press_ticks(111)}
PRESSES = E.PRESSES
KEYS_HELD = PRESSES * E.HOLD                 # the per-tick table's keys total
RECORDS = 2 * PRESSES                        # a record for each press and each release
RUN_IDS = {"dxvk-native": "20261003T101500-4242-0a1b2c3d", "sdl3-gpu": "20261003T113000-4343-4e5f6a7b"}


def play_rows(host: str = "dxvk-native", **change) -> list[dict]:
    """A session: loading to tick 100, Metal Harbor from 101, the host's presses, goal at 300."""
    rows = []
    for tick in range(1, END + 1):
        inside = tick >= ENTRY
        rows.append({"tick": tick, "current_level": LEVEL, "game_mode": 5 if inside else 4,
                     "game_state": 16 if inside else 1, "player": "0x01ab2000" if inside else "0x0",
                     "goal_latch": "0x01" if tick >= GOAL else "0x00", "frames": tick,
                     "keys": E.keys_column(PRESS_TICKS[host], tick)})
    for tick, values in change.items():
        rows[int(tick.lstrip("t")) - 1].update(values)
    return rows


def where(host: str) -> str:
    return f"private/play/{host}"


def latency_rel(host: str) -> dict[str, str]:
    """The latency logs as the player and the producers name them, below the root."""
    d = where(host)
    return {"keys": f"{d}/player-keys.tsv", "polls": f"{d}/play-polls.tsv",
            "presents": f"{d}/player-presents.tsv", "relay_frames": f"{d}/relay-frames.tsv",
            "fifo": f"{d}/input.fifo"}


def player_report(host: str = "dxvk-native", presses: int = PRESSES, **change) -> dict:
    """A final report of a session the goal ended, as host/player/player.cpp writes one: the
    stream read to its END, every Present made, the window's keys (`presses` presses, each a
    press and a release) sent and their logs closed."""
    report = {"tool": "host/player sa2_player", "phase": "done",
              **E.report_fields(latency_rel(host), presses, END),
              "present_failures": 0, "readback_failures": 0, "errors": 0,
              "calls": {"handled": 4181795, "ignored_by_design": 5788, "skipped_by_stage": 0,
                        "not_handled": 0},
              "textures": {"dds": 123, "png": 2, "placeholder_binds": 0},
              "host_failures": {"count": 0}, "stream_problems": {"count": 0, "first": []},
              "notes": {"count": 0, "allowed": False, "first": []},
              "stream_header": stream_header(RUN_IDS[host], host),
              "window": {"frames_with_flags": {"occluded": 0, "hidden": 0, "minimized": 0,
                                               "input_focus": 391}},
              **host_files(host)}
    return patched(report, change)


def run_record(rows: list[dict], report: dict, host: str = "dxvk-native", **change) -> dict:
    """The session's run record: the runtime read the FIFO the player wrote, every record."""
    sent = report.get("host_input", {})
    keys = sum(int(str(row["keys"]), 0) for row in rows)
    run = {"ok": True, "virtual": {"sleep_ms": 233461 if host == "dxvk-native" else 233977},
           "dinput": {"device_enabled": True, "host_input": {
        "source": "fifo", "answers_from_host": keys, "polls": len(rows) + 1, "polls_held": keys,
        "keys_written": keys, "script_lines": 0, "records": sent.get("records_written"),
        "last_seq": sent.get("last_seq"), "seq_gaps": 0, "polls_without_writer": 0,
        "partial_dropped": 0, "log_lines": 2, "stop_requested": 1, "quit_posted": 1,
        "quit_poll": len(rows)}}, **E.run_fields(len(rows) + 1, END)}
    return patched(run, change)


def write_play(root: Path, host: str, rows: list[dict] | None = None,
               report: dict | None = None, renderer: bytes | None = None,
               receipt: dict | None = None, run: dict | None = None,
               presses: list[int] | None = None) -> Path:
    """The host's session in root/private/play/<host> and its receipt: the table, the report,
    the run record, the renderer, the latency logs of `presses` (by default the host's) and
    the session's play-live.json; the receipt's input_latency is the crafted session's."""
    d = root / where(host)
    rows = play_rows(host) if rows is None else rows
    ticks = write_tsv(d / "ticks.tsv", X.PLAY_TICKS, rows)
    report = player_report(host) if report is None else report
    rep = write_json(d / "report.json", report)
    run = run_record(rows, report, host) if run is None else run
    record = write_json(d / "run.json", run)
    lib = d / "renderer"
    lib.write_bytes(renderer if renderer is not None else renderer_bytes(host))
    made = E.session(d, root, PRESS_TICKS[host] if presses is None else presses, END)
    live = write_json(d / "play-live.json", E.live_session(run, END))
    evidence = {"ticks": ref(root, ticks), "player_report": ref(root, rep),
                "run": ref(root, record), "session": ref(root, live)}
    evidence.update({name: ref(root, path) for name, path in made["paths"].items()})
    doc = stamped(X.PLAY_TOOL, {"host": host, "stage": "metalharbor", "run_id": RUN_IDS[host],
                                "renderer": ref(root, lib), "evidence": evidence,
                                "input_latency": E.summary(PRESSES)})
    doc["schema"] = X.PLAY_SCHEMA
    doc.update(receipt or {})
    return write_json(root / X.PLAY_RECEIPT.format(host=host), doc)


def both_hosts(tmp_path: Path) -> Path:
    root = project(tmp_path)
    for host, _ in X.HOSTS:
        write_play(root, host)
    return root


LATENCY_SAID = (f"input latency, press to presented frame, over {PRESSES} presses in the stage: "
                "median 20.0 ms, p90 20.0 ms, max 20.0 ms")


def test_play_met_in_both_hosts(tmp_path: Path) -> None:
    result = X.play(both_hosts(tmp_path))
    assert result.met is True, result.detail
    assert result.detail == "; ".join(
        f"{host}: level 10 entered at tick 101, goal at tick 300, 400 frames presented of the "
        f"360 made by 60 ticks past it, {RECORDS} host key events; {LATENCY_SAID}"
        for host, _ in X.HOSTS)


def test_play_today_names_both_missing_receipts_and_their_writer(tmp_path: Path) -> None:
    result = X.play(project(tmp_path))
    assert result.met is False
    for host, _ in X.HOSTS:
        assert f"no receipt {X.PLAY_RECEIPT.format(host=host)} (its writer: {X.PLAY_TOOL}" \
            in result.detail


def test_play_in_one_host_is_not_both(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_play(root, "dxvk-native")
    result = X.play(root)
    assert result.met is False
    assert "dxvk-native: level 10 entered" in result.detail
    assert "no receipt Documentation/receipts/s5-play-sdl3-gpu-v1.json" in result.detail


ROW_FAULTS = [
    ({f"t{t}": {"goal_latch": "0x00"} for t in range(GOAL, END + 1)}, "the goal latch never rises"),
    ({"t50": {"goal_latch": "0x01"}}, "the latch is set before the stage is entered"),
    ({f"t{t}": {"game_mode": 5, "game_state": 16, "player": "0x1"} for t in range(1, ENTRY)},
     "in the stage from its first tick: the start is not shown"),
    ({"t200": {"current_level": 11}}, "the session leaves Metal Harbor before the goal"),
    ({f"t{t}": {"keys": 0} for t in range(ENTRY, GOAL + 1)},
     "no host key reached the game in the stage"),
    ({f"t{t}": {"current_level": 11} for t in range(1, END + 1)}, "another stage altogether"),
    ({f"t{t}": {"player": "0x0"} for t in range(1, END + 1)}, "no player"),
]


@pytest.mark.parametrize("change,why", ROW_FAULTS)
def test_play_unmet_on_the_session_table(tmp_path: Path, change: dict, why: str) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", rows=play_rows("sdl3-gpu", **change))
    assert X.play(root).met is False, why


KEYS_OUTSIDE = {"before the stage": range(50, 61), "after the goal": range(310, 321)}


@pytest.mark.parametrize("where", list(KEYS_OUTSIDE))
def test_play_unmet_on_keys_only_outside_the_stage(tmp_path: Path, where: str) -> None:
    """Confirm review NIT 2: keys before the stage (the test spawn's load) or after the goal do
    not show the stage played; the run record agrees with the table, so only the window refuses."""
    root = both_hosts(tmp_path)
    change = {f"t{t}": {"keys": 0} for t in range(ENTRY, GOAL + 1)}
    change.update({f"t{t}": {"keys": 1} for t in KEYS_OUTSIDE[where]})
    write_play(root, "sdl3-gpu", rows=play_rows("sdl3-gpu", **change))
    result = X.play(root)
    assert result.met is False, where
    assert ("sdl3-gpu: no host key reached the game between entering the stage and the goal"
            in result.detail), where


@pytest.mark.parametrize("meta,said", [
    ({"runner": "tools/s5/present_run.py"}, "runner 'tools/s5/present_run.py'"),
    ({"runner": None}, "runner None"),
    ({"interactive": False}, "interactive False"),
    ({"interactive": "true"}, "interactive 'true'"),
    ({"interactive": 1}, "interactive 1"),
    ({"interactive": None}, "interactive None"),
    ({"scene": "boot"}, "scene 'boot'"),
    ({"scene": None}, "scene None"),
])
def test_play_unmet_unless_the_stream_is_play_live_s_metal_harbor_session(
        tmp_path: Path, meta: dict, said: str) -> None:
    """Confirm review SHOULD-FIX: the stream header's meta names the session's writer, a player
    taking the window's keys, and the stage; a stream another runner made is refused."""
    root = both_hosts(tmp_path)
    header = json.loads(stream_header(RUN_IDS["sdl3-gpu"], "sdl3-gpu"))
    fields = json.loads(header["meta"])
    for key, value in meta.items():
        if value is None:
            fields.pop(key)
        else:
            fields[key] = value
    header["meta"] = json.dumps(fields)
    write_play(root, "sdl3-gpu", report=player_report("sdl3-gpu", stream_header=json.dumps(header)))
    result = X.play(root)
    assert result.met is False, said
    assert (f"player report: its stream header's meta reads {said}, not tools/s5/play_live.py's "
            "interactive metalharbor session") in result.detail, said


@pytest.mark.parametrize("ok", [False, None, "true", 1])
def test_play_unmet_unless_the_run_record_is_ok(tmp_path: Path, ok: object) -> None:
    """Confirm review NIT 3: the run record's own ok is read."""
    root = both_hosts(tmp_path)
    rows, report = play_rows("sdl3-gpu"), player_report("sdl3-gpu")
    run = run_record(rows, report, "sdl3-gpu")
    if ok is None:
        run.pop("ok")
    else:
        run["ok"] = ok
    write_play(root, "sdl3-gpu", rows=rows, report=report, run=run)
    assert f"s5-play-sdl3-gpu-v1.json run record: the run record's ok is {ok!r}" \
        in X.play(root).detail


def test_play_unmet_when_the_session_ends_before_a_second_past_the_goal(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", rows=play_rows("sdl3-gpu")[:GOAL + X.AFTER_GOAL_TICKS - 1])
    detail = X.play(root).detail
    assert "sdl3-gpu: the session ends 59 ticks after the goal, under 60" in detail


@pytest.mark.parametrize("change,why", [
    ({"presents": 359}, "the goal ring was not presented a second past the goal"),
    ({"complete": False}, "a report written before the player finished"),
    ({"present_failures": 1}, "a Present failed"),
    ({"readback_failures": 2}, "a frame was not read back"),
    ({"errors": 1}, "the player counted an error"),
    ({"calls__not_handled": 3}, "calls the player did not handle"),
    ({"host_failures__count": 1}, "a host call failed where the guest was told success"),
    ({"stream_problems__count": 1}, "a malformed stream"),
    ({"notes__count": 2}, "a stream whose writer left something out"),
    ({"window__frames_with_flags__hidden": 1}, "the window was hidden"),
    ({"window__frames_with_flags__minimized": 4}, "the window was minimized"),
    ({"window__frames_with_flags__occluded": 1}, "the compositor suspended the window"),
    ({"host_input__started": False}, "the window's keyboard never reached the FIFO"),
    ({"host_input__key_events": 0}, "no key was pressed in the window"),
    ({"host_input__records_written": 0}, "nothing was written to the game"),
    ({"host_input__write_errors": 1}, "a write to the game failed"),
    ({"presents": "400"}, "a count that is not a count"),
])
def test_play_unmet_on_the_player_report(tmp_path: Path, change: dict, why: str) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "dxvk-native", report=player_report("dxvk-native", **change))
    assert X.play(root).met is False, why


# The blocker of the fold review: a player below its full stage draws geometry with white
# placeholders ("wireframe playable"), and its report still passes.
BELOW_FULL_STAGE = [
    ({"stage": 0}, "stage 0: Clear, BeginScene, EndScene and Present only"),
    ({"stage": 1}, "stage 1: every texture a white placeholder"),
    ({"stage": 2}, "stage 2: no lock uploads, PNG or render targets"),
    ({"stage": None}, "no stage reported"),
    ({"stage": True}, "a stage that is not a number"),
    ({"calls__skipped_by_stage": 250000}, "calls skipped by the stage"),
    ({"textures__placeholder_binds": 3}, "placeholder textures bound"),
    ({"calls": {"handled": 9, "ignored_by_design": 0, "not_handled": 0}},
     "a report that does not count skipped calls"),
    ({"textures": {"dds": 9}}, "a report that does not count placeholder binds"),
]


@pytest.mark.parametrize("change,why", BELOW_FULL_STAGE)
@pytest.mark.parametrize("host", ["dxvk-native", "sdl3-gpu"])
def test_play_unmet_below_the_player_s_full_stage(tmp_path: Path, host: str, change: dict,
                                                 why: str) -> None:
    root = both_hosts(tmp_path)
    write_play(root, host, report=player_report(host, **change))
    result = X.play(root)
    assert result.met is False, why
    assert f"s5-play-{host}-v1.json player report" in result.detail


def test_play_names_the_stage_it_refuses(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", report=player_report("sdl3-gpu", stage=1))
    assert ("Documentation/receipts/s5-play-sdl3-gpu-v1.json player report: stage 1, not the "
            "player's full stage 3") in X.play(root).detail


@pytest.mark.parametrize("change,why", [
    ({"schema": "sa2-s5-play/v0"}, "another schema"),
    ({"generated_by": "tools/s5/present_live.py"}, "another writer"),
    ({"inputs": {"pipeline": {}}}, "no digest for its writer"),
    ({"inputs": {"pipeline": {X.PLAY_TOOL: "typed"}}}, "a digest that is not one"),
    ({"host": "sdl3-gpu"}, "the other host's id"),
    ({"stage": "boot"}, "another stage"),
    ({"run_id": ""}, "no run id"),
    ({"run_id": None}, "a run id that is not one"),
])
def test_play_unmet_on_a_receipt_off_contract(tmp_path: Path, change: dict, why: str) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "dxvk-native", receipt=change)
    assert X.play(root).met is False, why


def test_play_unmet_when_evidence_changed_after_its_receipt(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    ticks = root / "private/play/dxvk-native/ticks.tsv"
    ticks.write_text(ticks.read_text().replace("\t0x01\t300\t", "\t0x01\t301\t"))
    assert "does not hash to the receipt's sha256" in X.play(root).detail


@pytest.mark.parametrize("name", ["report.json", "run.json"])
def test_play_unmet_when_evidence_is_gone(tmp_path: Path, name: str) -> None:
    root = both_hosts(tmp_path)
    (root / f"private/play/sdl3-gpu/{name}").unlink()
    assert f"private/play/sdl3-gpu/{name} is absent" in X.play(root).detail


# ---- the session's own run: the player's report is the session's, and the keys the window's


def test_play_unmet_on_a_report_of_another_run(tmp_path: Path) -> None:
    """A longer, unrelated report (fold review: presents 999999) is not the session's."""
    root = both_hosts(tmp_path)
    other = player_report("dxvk-native", presents=999999,
                          stream_header=stream_header("20261001T090000-1-ffffffff"))
    write_play(root, "dxvk-native", report=other)
    assert ("player report: its stream is run '20261001T090000-1-ffffffff', not the receipt's "
            f"'{RUN_IDS['dxvk-native']}'") in X.play(root).detail


@pytest.mark.parametrize("header,why", [
    (None, "no stream header"),
    ("not json", "a header that does not parse"),
    (json.dumps({"schema": "sa2-d3d9-stream/v1", "meta": ""}), "an empty meta"),
    (json.dumps({"schema": "sa2-d3d9-stream/v1", "meta": json.dumps({"build": "x"})}),
     "a meta naming no run"),
    (json.dumps(["meta"]), "a header that is not an object"),
])
def test_play_unmet_when_the_stream_header_names_no_run(tmp_path: Path, header: object,
                                                         why: str) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", report=player_report("sdl3-gpu", stream_header=header))
    assert "its stream header's meta names no run" in X.play(root).detail, why


@pytest.mark.parametrize("change,why", [
    ({"dinput__host_input__source": "script", "dinput__host_input__script_lines": 2,
      "dinput__host_input__records": 0, "dinput__host_input__last_seq": 0},
     "the second host replayed the first session's log as a script"),
    ({"dinput__host_input__source": "script"}, "the keyboard read a script"),
    ({"dinput__host_input__source": ""}, "no host keyboard at all"),
    ({"dinput__host_input__script_lines": 1}, "a script line beside the FIFO"),
    ({"dinput__host_input__records": RECORDS - 1}, "a record the player wrote was never read"),
    ({"dinput__host_input__records": RECORDS + 1}, "a record the player never wrote was read"),
    ({"dinput__host_input__last_seq": RECORDS}, "another writer's sequence"),
    ({"dinput__host_input__seq_gaps": 1}, "records out of order"),
    ({"dinput__host_input__partial_dropped": 8}, "a record cut short"),
    ({"dinput__host_input__keys_written": KEYS_HELD + 1}, "a per-tick table with other keys"),
    ({"dinput__host_input__keys_written": str(KEYS_HELD)}, "a count that is not a count"),
    ({"dinput": {"device_enabled": True}}, "a run record with no host keyboard"),
])
def test_play_unmet_unless_the_runtime_read_the_window_s_keys(tmp_path: Path, change: dict,
                                                               why: str) -> None:
    root = both_hosts(tmp_path)
    rows, report = play_rows("sdl3-gpu"), player_report("sdl3-gpu")
    write_play(root, "sdl3-gpu", rows=rows, report=report,
               run=run_record(rows, report, "sdl3-gpu", **change))
    result = X.play(root)
    assert result.met is False, why
    assert "s5-play-sdl3-gpu-v1.json run record" in result.detail, why


def test_play_names_a_script_source(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    rows, report = play_rows("dxvk-native"), player_report("dxvk-native")
    write_play(root, "dxvk-native", rows=rows, report=report,
               run=run_record(rows, report, dinput__host_input__source="script"))
    assert ("run record: the runtime's keyboard source is 'script', not 'fifo', the player "
            "window's") in X.play(root).detail


def test_play_unmet_when_the_table_s_keys_are_not_the_runtime_s(tmp_path: Path) -> None:
    """The per-tick table is tied to the run by its keys: one more key in the table is refused."""
    root = both_hosts(tmp_path)
    report = player_report("sdl3-gpu")
    rows = play_rows("sdl3-gpu")
    run = run_record(rows, report, "sdl3-gpu")
    assert E.keys_column(PRESS_TICKS["sdl3-gpu"], 172) == 0
    write_play(root, "sdl3-gpu", rows=play_rows("sdl3-gpu", t172={"keys": 1}), report=report,
               run=run)
    assert (f"the per-tick table's keys total {KEYS_HELD + 1}; the runtime applied {KEYS_HELD}"
            in X.play(root).detail)


# ---- two independent hosts: each renderer is the file its player loaded


@pytest.mark.parametrize("host,renderer,change,said", [
    ("dxvk-native", b"another build of DXVK", {}, "its renderer is not the DXVK library"),
    ("dxvk-native", None, {"files__libdxvk_d3d9__sha256": "77" * 32},
     "the player did not load and verify the pinned DXVK library"),
    ("dxvk-native", None, {"host_identity__dxvk_file_verified": False},
     "the player did not load and verify the pinned DXVK library"),
    ("dxvk-native", None, {"host_identity": None},
     "the player did not load and verify the pinned DXVK library"),
    ("sdl3-gpu", renderer_bytes("dxvk-native"), {"files__player__sha256": PIN},
     "its renderer is DXVK's library"),
    ("sdl3-gpu", None, {"files__player__sha256": "e1" * 32},
     "the player that ran (files.player) is not the receipt's renderer"),
    ("sdl3-gpu", None, {"files__libdxvk_d3d9__sha256": PIN}, "the player pins a DXVK library"),
    ("sdl3-gpu", None, {"files__libdxvk_d3d9__sha256": "77" * 32},
     "the player pins a DXVK library"),
    ("sdl3-gpu", None, {"host_identity__dxvk_file_verified": True},
     "the player pins a DXVK library"),
])
def test_play_holds_each_renderer_to_what_its_player_loaded(tmp_path: Path, host: str,
                                                           renderer: bytes | None, change: dict,
                                                           said: str) -> None:
    root = both_hosts(tmp_path)
    write_play(root, host, report=player_report(host, **change), renderer=renderer)
    result = X.play(root)
    assert result.met is False and said in result.detail, result.detail


def test_play_met_when_the_gpu_player_names_no_dxvk_file_at_all(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    report = player_report("sdl3-gpu")
    del report["files"]["libdxvk_d3d9"]
    write_play(root, "sdl3-gpu", report=report)
    assert X.play(root).met is True


@pytest.mark.parametrize("pins,said", [
    (None, "absent: tools/pins.json"),
    ("{}", "tools/pins.json holds no sha256 at tools.dxvk.library_sha256"),
])
def test_play_unmet_without_the_dxvk_pin(tmp_path: Path, pins: str | None, said: str) -> None:
    root = both_hosts(tmp_path)
    if pins is None:
        (root / X.PINS).unlink()
    else:
        (root / X.PINS).write_text(pins)
    result = X.play(root)
    assert result.met is False and said in result.detail


def test_play_unmet_when_both_hosts_name_one_renderer(tmp_path: Path) -> None:
    root = project(tmp_path)
    for host, _ in X.HOSTS:
        write_play(root, host, renderer=renderer_bytes("dxvk-native"),
                   report=player_report(host, files__player__sha256=PIN))
    result = X.play(root)
    assert result.met is False
    assert "sdl3-gpu: its renderer is DXVK's library" in result.detail


# ---- the two sessions are two: neither host's evidence is the other's


def test_play_unmet_when_both_hosts_show_one_player_s_report(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    one = json.loads((root / X.PLAY_RECEIPT.format(host="dxvk-native")).read_text())
    other = root / X.PLAY_RECEIPT.format(host="sdl3-gpu")
    doc = json.loads(other.read_text())
    doc["evidence"]["player_report"] = one["evidence"]["player_report"]
    write_json(other, doc)
    assert X.play(root).met is False


def test_play_unmet_when_both_hosts_show_the_same_game(tmp_path: Path) -> None:
    """One per-tick table in both hosts can only be one session's keys replayed: each host is
    played in its own window (fold review; it was accepted before)."""
    root = project(tmp_path)
    for host, _ in X.HOSTS:
        write_play(root, host, rows=play_rows("dxvk-native"))
    tables = [(root / f"private/play/{host}/ticks.tsv").read_bytes() for host, _ in X.HOSTS]
    result = X.play(root)
    assert tables[0] == tables[1]
    assert (result.met, result.detail.endswith("the hosts' sessions share one per-tick table")) \
        == (False, True)


def test_play_unmet_when_both_hosts_name_one_run_record(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    one = json.loads((root / X.PLAY_RECEIPT.format(host="dxvk-native")).read_text())
    other = root / X.PLAY_RECEIPT.format(host="sdl3-gpu")
    doc = json.loads(other.read_text())
    doc["evidence"]["run"] = one["evidence"]["run"]
    write_json(other, doc)
    result = X.play(root)
    assert (result.met, result.detail.endswith("the hosts' sessions share one run record")) \
        == (False, True)


def test_play_unmet_when_both_hosts_name_one_run(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_play(root, "dxvk-native")
    write_play(root, "sdl3-gpu", receipt={"run_id": RUN_IDS["dxvk-native"]},
               report=player_report("sdl3-gpu",
                                    stream_header=stream_header(RUN_IDS["dxvk-native"],
                                                                "sdl3-gpu")))
    result = X.play(root)
    assert (result.met, result.detail.endswith("the hosts' sessions share one run id")) \
        == (False, True)


@pytest.mark.parametrize("old,new,why", [
    ("5. **Smallest falsifying experiment.**", "5. **Next experiment.**", "not rule 12's format"),
    ("**Fallback route, and what it inherits.**", "**Fallback.**", "no fallback route named"),
    ("the second is a renderer on SDL3's GPU API", "the second is a renderer on bgfx",
     "the record selects another second host"),
    ("the first host is DXVK Native", "the first host is a native renderer",
     "the record selects another first host"),
])
def test_play_unmet_when_the_selection_record_changes(tmp_path: Path, old: str, new: str,
                                                      why: str) -> None:
    root = both_hosts(tmp_path)
    edit_record(root, old, new)
    assert X.play(root).met is False, why


def test_play_unmet_without_the_selection_record(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    (root / X.SELECTION).unlink()
    assert X.play(root).detail == f"no selection record {X.SELECTION}"


def test_play_reads_metal_harbor_s_level_from_the_registry(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    write_json(root / X.REGISTRY, {"replays": [
        {"name": "metalharbor", "stage": {"name": "metalharbor", "level": 11}}]})
    assert "never in Metal Harbor (level 11" in X.play(root).detail


def test_play_refuses_a_table_that_is_not_text(tmp_path: Path) -> None:
    """A per-tick table that is not UTF-8 is refused with a verdict line, not a traceback."""
    root = both_hosts(tmp_path)
    receipt_path = write_play(root, "sdl3-gpu")
    ticks = root / "private/play/sdl3-gpu/ticks.tsv"
    ticks.write_bytes(b"tick\t\xff\xfe\n")
    doc = json.loads(receipt_path.read_text())
    doc["evidence"]["ticks"] = ref(root, ticks)
    write_json(receipt_path, doc)
    assert "ticks does not read as UTF-8 text" in X.play(root).detail


# ---- how the session ended: a report the real player writes


def test_play_met_on_a_session_its_window_closed_by_hand(tmp_path: Path) -> None:
    """The player's end request (--end-fd): the first close lets every key up, tells
    play_live, which ends the game, and the player reads on to the stream's END. Its report is
    final and passes, with end_request saying so; the clause takes it."""
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", report=player_report(
        "sdl3-gpu", end_request={"fd": True, "requested": True, "by": "the window's close",
                                 "after_frame": 362, "at_s": 6.04, "written": True,
                                 "closes_after": 0, "error": None},
        events={"discarded": 900, "close_requests": 1}))
    result = X.play(root)
    assert result.met is True, result.detail


def test_play_unmet_on_a_session_its_window_closed_hard(tmp_path: Path) -> None:
    """A close the player could not hand on (no --end-fd, or a second close) stops it: the
    live reader is cancelled before END, and the report it then writes is not final."""
    root = both_hosts(tmp_path)
    write_play(root, "dxvk-native", report=player_report(
        "dxvk-native", complete=False, phase="incomplete", **{"pass": False}, exit_status=3,
        failures=["stream: 2 problem(s); first: live reader cancelled: SDL close after the "
                  "accepted record", "replay: the window was closed before the run finished"],
        stream_problems={"count": 2, "first": [
            "live reader cancelled: SDL close after the accepted record",
            "live stream incomplete: clean byte EOF and a logical END are both required"]},
        stream_records_read_to_end=False, events={"discarded": 900, "close_requests": 1}))
    result = X.play(root)
    assert result.met is False
    assert ("Documentation/receipts/s5-play-dxvk-native-v1.json player report is not final "
            "(complete is False)") in result.detail


# ---- the input latency: recomputed from its four logs and held equal


def test_play_unmet_on_a_receipt_latency_one_microsecond_off(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    off = E.summary(PRESSES)
    off["summary"]["stage"]["total_us"]["p90"] += 1
    write_play(root, "sdl3-gpu", receipt={"input_latency": off})
    assert ("Documentation/receipts/s5-play-sdl3-gpu-v1.json: input_latency is not the latency "
            "its evidence gives") in X.play(root).detail


@pytest.mark.parametrize("latency", [None, "20 ms", {}])
def test_play_unmet_on_a_receipt_with_no_latency_or_another(tmp_path: Path, latency) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "dxvk-native", receipt={"input_latency": latency})
    assert "input_latency is not the latency its evidence gives" in X.play(root).detail


def test_play_unmet_on_a_latency_of_another_kind(tmp_path: Path) -> None:
    """Exact JSON types: a float where the join gives an integer is not the join's."""
    root = both_hosts(tmp_path)
    other = E.summary(PRESSES)
    other["summary"]["total_us"]["p50"] = float(other["summary"]["total_us"]["p50"])
    write_play(root, "dxvk-native", receipt={"input_latency": other})
    assert "input_latency is not the latency its evidence gives" in X.play(root).detail


@pytest.mark.parametrize("name", list(X.LATENCY_EVIDENCE) + [X.SESSION_EVIDENCE])
def test_play_unmet_when_a_latency_file_is_gone(tmp_path: Path, name: str) -> None:
    root = both_hosts(tmp_path)
    receipt = json.loads((root / X.PLAY_RECEIPT.format(host="sdl3-gpu")).read_text())
    (root / receipt["evidence"][name]["path"]).unlink()
    assert (f"evidence.{name} {receipt['evidence'][name]['path']} is absent"
            in X.play(root).detail)


@pytest.mark.parametrize("name", list(X.LATENCY_EVIDENCE) + [X.SESSION_EVIDENCE])
def test_play_unmet_on_a_receipt_naming_no_latency_file(tmp_path: Path, name: str) -> None:
    root = both_hosts(tmp_path)
    path = root / X.PLAY_RECEIPT.format(host="dxvk-native")
    doc = json.loads(path.read_text())
    del doc["evidence"][name]
    write_json(path, doc)
    assert f"evidence.{name} is not a {{path, sha256}} reference" in X.play(root).detail


def test_play_unmet_on_a_dropped_key_log_row(tmp_path: Path) -> None:
    """The sender's log loses its last row; the report still counts it."""
    root = both_hosts(tmp_path)
    path = write_play(root, "sdl3-gpu")
    keys = root / latency_rel("sdl3-gpu")["keys"]
    keys.write_text("".join(keys.read_text().splitlines(keepends=True)[:-1]))
    doc = json.loads(path.read_text())
    doc["evidence"]["keys"] = ref(root, keys)
    write_json(path, doc)
    assert "host_input.key_log.rows is 48, expected 47" in X.play(root).detail


def test_play_unmet_under_the_press_floor(tmp_path: Path) -> None:
    """Fewer presented presses in the stage than MIN_LATENCY_PRESSES: no distribution."""
    root = both_hosts(tmp_path)
    few = X.MIN_LATENCY_PRESSES - 1
    ticks = E.press_ticks(110, few)
    rows = [row | {"keys": E.keys_column(ticks, row["tick"])} for row in play_rows("dxvk-native")]
    report = player_report("dxvk-native", **{
        "host_input__key_events": 2 * few, "host_input__changes": 2 * few,
        "host_input__records_written": 2 * few, "host_input__last_seq": 2 * few - 1,
        "host_input__key_log__rows": 2 * few, "host_input__key_log__sent": 2 * few})
    write_play(root, "dxvk-native", rows=rows, report=report, presses=ticks,
               receipt={"input_latency": E.summary(few)})
    assert (f"{few} presses presented between entering the stage and the goal, under "
            f"{X.MIN_LATENCY_PRESSES}") in X.play(root).detail


@pytest.mark.parametrize("events", [1, 48])
def test_play_unmet_on_synthetic_key_events(tmp_path: Path, events: int) -> None:
    """A --synthetic-keys session (the player's own queue) verifies the transport; it is not a
    person playing, whatever else it shows."""
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", report=player_report(
        "sdl3-gpu", host_input__synthetic_key_events=events))
    assert (f"sdl3-gpu: {events} synthetic key events (--synthetic-keys, the player's own "
            "queue): a scripted session is not a person playing") in X.play(root).detail


@pytest.mark.parametrize("change", [{"host_input__synthetic_key_events": None},
                                    {"host_input__synthetic_key_events": "0"}])
def test_play_unmet_unless_the_synthetic_count_is_a_count(tmp_path: Path, change: dict) -> None:
    root = both_hosts(tmp_path)
    write_play(root, "sdl3-gpu", report=player_report("sdl3-gpu", **change))
    assert "host_input.synthetic_key_events is" in X.play(root).detail


def test_play_unmet_on_a_synthetic_key_row(tmp_path: Path) -> None:
    """A synthetic event the report does not count is still in the sender's log."""
    root = both_hosts(tmp_path)
    path = write_play(root, "dxvk-native")
    keys = root / latency_rel("dxvk-native")["keys"]
    lines = keys.read_text().splitlines(keepends=True)
    cells = lines[5].split("\t")
    cells[X.PLAY_KEYS.index("synthetic")] = "1"
    lines[5] = "\t".join(cells)
    keys.write_text("".join(lines))
    doc = json.loads(path.read_text())
    doc["evidence"]["keys"] = ref(root, keys)
    write_json(path, doc)
    assert "input latency: synthetic raw key row" in X.play(root).detail


def test_play_unmet_on_a_session_cut_short(tmp_path: Path) -> None:
    """A session the disk ended keeps its own record of it, and the join refuses it."""
    root = both_hosts(tmp_path)
    path = write_play(root, "sdl3-gpu")
    doc = json.loads(path.read_text())
    live = root / doc["evidence"]["session"]["path"]
    session = json.loads(live.read_text())
    session["session"]["limit"] = {"why": "the stream copy passed 1 bytes (--stream-cap-gb)"}
    write_json(live, session)
    doc["evidence"]["session"] = ref(root, live)
    write_json(path, doc)
    assert "session.limit is" in X.play(root).detail


@pytest.mark.parametrize("how", ["a second SIGTERM", "SIGKILL to loop_run's group",
                                 "no game process to ask: SIGKILL to loop_run's group"])
def test_play_unmet_on_a_session_whose_game_was_stopped_by_force(tmp_path: Path, how: str) -> None:
    """tools/s5/play_live.py's stop_game: the first SIGTERM is WM_QUIT at the game's next
    keyboard poll; the second meets the runtime's reset handler, the default action, and
    SIGKILL is SIGKILL. A game stopped either way did not leave its loop through its own code,
    whatever else its evidence shows (play-live.json says how), so the clause refuses it."""
    root = both_hosts(tmp_path)
    path = write_play(root, "sdl3-gpu")
    doc = json.loads(path.read_text())
    live = root / doc["evidence"]["session"]["path"]
    session = json.loads(live.read_text())
    session["session"]["stop"] = {"how": how, "pid": 4242, "signalled": [4242, 4242],
                                  "seconds": 150.2}
    write_json(live, session)
    doc["evidence"]["session"] = ref(root, live)
    write_json(path, doc)
    result = X.play(root)
    assert result.met is False
    assert ("Documentation/receipts/s5-play-sdl3-gpu-v1.json input latency: forced/unknown "
            "game stop") in result.detail


@pytest.mark.parametrize("how", list(X.CLEAN_STOPS))
def test_play_met_on_a_game_that_ended_itself_or_at_the_first_sigterm(tmp_path: Path,
                                                                      how: str) -> None:
    root = both_hosts(tmp_path)
    path = write_play(root, "sdl3-gpu")
    doc = json.loads(path.read_text())
    live = root / doc["evidence"]["session"]["path"]
    session = json.loads(live.read_text())
    session["session"]["stop"] = {"how": how, "seconds": 0.3}
    write_json(live, session)
    doc["evidence"]["session"] = ref(root, live)
    write_json(path, doc)
    result = X.play(root)
    assert result.met is True, result.detail


@pytest.mark.parametrize("named,said", [("dxvk-native", "'dxvk-native'"), (None, "None"),
                                        (["sdl3-gpu"], "['sdl3-gpu']")])
def test_play_unmet_when_the_stream_names_another_host(tmp_path: Path, named, said: str) -> None:
    """play_live writes the session's host into SA2_REC_META, so the stream header's meta
    names it: a receipt for one host whose stream another host's session recorded, or that
    names none, is refused."""
    root = both_hosts(tmp_path)
    header = json.loads(stream_header(RUN_IDS["sdl3-gpu"], "sdl3-gpu"))
    fields = json.loads(header["meta"])
    if named is None:
        del fields["host"]
    else:
        fields["host"] = named
    header["meta"] = json.dumps(fields)
    write_play(root, "sdl3-gpu", report=player_report("sdl3-gpu", stream_header=json.dumps(header)))
    result = X.play(root)
    assert result.met is False
    assert ("Documentation/receipts/s5-play-sdl3-gpu-v1.json player report: its stream "
            f"header's meta names the host {said}, not the receipt's 'sdl3-gpu'") in result.detail


# Presses outside the stage, on both sides of each edge: loading (20, 97: the stage is entered
# at tick 101), its first tick (101), its goal tick (300) and past the goal (304, 350). Each is
# polled in the tick it goes down in (test_s5_play_live_evidence.logs).
OUTSIDE_BEFORE, EDGE_IN, OUTSIDE_AFTER = [20, 97], [101, 300], [304, 350]


def edged_presses(host: str) -> tuple[list[int], int]:
    """The host's presses with those at and outside the stage's edges; how many are in it."""
    presses = sorted(OUTSIDE_BEFORE + EDGE_IN + OUTSIDE_AFTER + PRESS_TICKS[host])
    return presses, PRESSES + len(EDGE_IN)


def write_edged_play(root: Path, host: str, stage: int | None = None) -> Path:
    presses, inside = edged_presses(host)
    rows = [row | {"keys": E.keys_column(presses, row["tick"])} for row in play_rows(host)]
    return write_play(root, host, rows=rows, report=player_report(host, presses=len(presses)),
                      presses=presses,
                      receipt={"input_latency": E.summary(len(presses),
                                                          inside if stage is None else stage)})


def test_play_counts_only_the_presses_between_entering_the_stage_and_the_goal(tmp_path: Path
                                                                            ) -> None:
    """The stage's summary, and the floor, are the presses polled from the tick the stage is
    entered to the goal's, both included: none before it (the test spawn's load) and none
    after the goal. The whole session's summary counts every press."""
    root = both_hosts(tmp_path)
    write_edged_play(root, "sdl3-gpu")
    presses, inside = edged_presses("sdl3-gpu")
    assert (len(presses), inside) == (PRESSES + 6, PRESSES + 2)
    result = X.play(root)
    assert result.met is True, result.detail
    assert (f"sdl3-gpu: level 10 entered at tick 101, goal at tick 300, 400 frames presented "
            f"of the 360 made by 60 ticks past it, {2 * len(presses)} host key events; input "
            f"latency, press to presented frame, over {inside} presses in the stage: median "
            "20.0 ms, p90 20.0 ms, max 20.0 ms") in result.detail


@pytest.mark.parametrize("stage", [PRESSES + 6, PRESSES + 4, PRESSES + 1])
def test_play_unmet_on_a_stage_summary_that_counts_presses_outside_it(tmp_path: Path,
                                                                     stage: int) -> None:
    """A receipt whose stage summary counts the whole session (or the load's presses, or loses
    an edge's) is not the join's: the presses before the stage and after the goal are not the
    stage's, and the presses on its first and goal ticks are."""
    root = both_hosts(tmp_path)
    write_edged_play(root, "sdl3-gpu", stage=stage)
    assert ("Documentation/receipts/s5-play-sdl3-gpu-v1.json: input_latency is not the latency "
            "its evidence gives") in X.play(root).detail


# ------------------------------------------------------------------ clause 3: audio

SCENE_TICKS = {"title": (21, 220), "menu": (221, 420), "garden": (421, 620),
               "metalharbor": (621, 820)}
LAST_TICK = 840
STREAM_OF_THE_SCENES = "cd" * 32


def audio_rows(**change) -> list[dict]:
    rows = []
    for tick in range(1, LAST_TICK + 1):
        row = {"tick": tick, "virtual_us": tick * TICK_US, "game_mode": 0, "current_level": 0,
               "game_state": 0, "current_menu": 0, "chao_area": 0}
        if SCENE_TICKS["title"][0] <= tick <= SCENE_TICKS["title"][1]:
            row.update(game_mode=14, current_menu=0)
        elif SCENE_TICKS["menu"][0] <= tick <= SCENE_TICKS["menu"][1]:
            row.update(game_mode=14, current_menu=1)
        elif SCENE_TICKS["garden"][0] <= tick <= SCENE_TICKS["garden"][1]:
            row.update(game_mode=5, game_state=16, current_level=90, chao_area=2)
        elif tick >= SCENE_TICKS["metalharbor"][0]:
            row.update(game_mode=5, current_level=LEVEL,
                       game_state=16 if tick <= SCENE_TICKS["metalharbor"][1] else 1)
        rows.append(row)
    for tick, values in change.items():
        rows[int(tick.lstrip("t")) - 1].update(values)
    return rows


def frame_at(tick: int) -> int:
    """The output frame at the end of `tick`, for a mix whose origin is virtual time 0."""
    return tick * TICK_US * X.SAMPLE_RATE // 1_000_000


def wav(path: Path, levels: dict[str, float], tag: int = 3) -> int:
    """The player's WAV layout (host/player/mixer.cpp, wav_header): a 480 Hz sine at
    each scene's amplitude, silence elsewhere. Returns the frame count."""
    period = [math.sin(2 * math.pi * k / 100) for k in range(100)]
    samples = array.array("f")
    cursor = 0
    for scene, (first, last) in SCENE_TICKS.items():
        start, end = frame_at(first - 1), frame_at(last)
        samples.extend(array.array("f", [0.0]) * (2 * (start - cursor)))
        one = array.array("f", [v * levels.get(scene, 0.1) for v in period for _ in (0, 1)])
        reps, rest = divmod(end - start, 100)
        samples.extend(one * reps)
        samples.extend(one[:2 * rest])
        cursor = end
    samples.extend(array.array("f", [0.0]) * (2 * (frame_at(LAST_TICK) - cursor)))
    frames = len(samples) // 2
    data = frames * 8
    head = (b"RIFF" + struct.pack("<I", 50 + data) + b"WAVEfmt " + struct.pack("<I", 18)
            + struct.pack("<HHIIHHH", tag, 2, 48000, 48000 * 8, 8, 32, 0)
            + b"fact" + struct.pack("<II", 4, frames) + b"data" + struct.pack("<I", data))
    assert len(head) == 58
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + samples.tobytes())
    return frames


def cached_wav(root: Path, levels: dict[str, float], tag: int) -> tuple[Path, int]:
    """One WAV per distinct content for the whole session (each is about 5 MB), shared through
    symlinks: the gate runs this file at every push, under the main thread's TMPDIR."""
    cache = root.parent.parent / "s5-exit-wav-cache"
    cache.mkdir(exist_ok=True)
    key = hashlib.sha256(json.dumps([sorted(levels.items()), tag]).encode()).hexdigest()[:16]
    path = cache / f"{key}.wav"
    if not path.exists():
        part = cache / f"{key}.part"
        wav(part, levels, tag)
        part.rename(path)
    return path, (path.stat().st_size - 58) // 8


def write_audio(root: Path, levels: dict[str, float] | None = None, rows: list[dict] | None = None,
                side: dict | None = None, tag: int = 3, drop: str | None = None,
                own_copy: bool = False, recording: dict | None = None,
                drop_recording: bool = False) -> Path:
    where = root / "private" / "audio"
    where.mkdir(parents=True, exist_ok=True)
    source, frames = cached_wav(root, levels or {}, tag)
    run = where / "run.wav"
    run.unlink(missing_ok=True)
    if own_copy:
        shutil.copyfile(source, run)
    else:
        run.symlink_to(source)
    sidecar = {"tool": "host/player sa2_player --audio-wav", "wav": "private/audio/run.wav",
               "ok": True, "format": "float32", "sample_rate": 48000, "channels": 2,
               "frames": frames, "origin_us": 0, "first_frame_us": TICK_US, "presents": LAST_TICK,
               "audio_problems": 0, "stream": "private/audio/run.sa2s",
               "stream_sha256": STREAM_OF_THE_SCENES}
    sidecar.update(side or {})
    write_json(where / "run.wav.json", sidecar)
    ticks = write_tsv(where / "ticks.tsv", X.AUDIO_TICKS, audio_rows() if rows is None else rows)
    made = {"schema": "sa2-present-run/v3", "replay": "scenes", "ok": True, "audio": True,
            "ready": {"path": "private/audio/run.READY", "stream": "private/audio/run.sa2s",
                      "stream_sha256": STREAM_OF_THE_SCENES},
            "ticks": ref(root, ticks)}
    record = write_json(where / "present-run.json", patched(made, recording or {}))
    one = {"wav": ref(root, where / "run.wav"), "sidecar": ref(root, where / "run.wav.json"),
           "ticks": ref(root, ticks), "recording": ref(root, record)}
    if drop_recording:
        del one["recording"]
    scenes = {scene: copy.deepcopy(one) for scene in X.SCENE_ORDER if scene != drop}
    doc = stamped(X.AUDIO_TOOL, {"scenes": scenes})
    doc["schema"] = X.AUDIO_SCHEMA
    return write_json(root / X.AUDIO_RECEIPT, doc)


def test_audible_met_on_all_four_scenes(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_audio(root)
    result = X.audible(root)
    assert result.met is True, result.detail
    assert result.detail.startswith("title -23.0 dBFS at second 1 of the scene, at least -40")
    for scene in ("menu", "garden", "metalharbor"):
        assert f"{scene} -23.0 dBFS" in result.detail


def test_audible_today_names_the_missing_receipt_and_tool(tmp_path: Path) -> None:
    detail = X.audible(project(tmp_path)).detail
    assert detail == (f"no receipt {X.AUDIO_RECEIPT} (its writer: {X.AUDIO_TOOL}, "
                      f"not written yet)")


@pytest.mark.parametrize("scene", X.SCENE_ORDER)
def test_audible_unmet_when_one_scene_is_below_the_threshold(tmp_path: Path, scene: str) -> None:
    root = project(tmp_path)
    write_audio(root, levels={scene: 0.001})     # a sine at 0.001: -63 dBFS
    result = X.audible(root)
    assert result.met is False
    assert f"{scene}: loudest of 3 whole seconds -63.0 dBFS, under -40" in result.detail


@pytest.mark.parametrize("scene", ["menu"])   # each scene's own threshold: the test above
def test_audible_unmet_when_one_scene_is_silent(tmp_path: Path, scene: str) -> None:
    root = project(tmp_path)
    write_audio(root, levels={scene: 0.0})
    assert f"{scene}: loudest of 3 whole seconds -inf dBFS" in X.audible(root).detail


@pytest.mark.parametrize("scene", X.SCENE_ORDER)
def test_audible_unmet_when_the_receipt_leaves_a_scene_out(tmp_path: Path, scene: str) -> None:
    root = project(tmp_path)
    write_audio(root, drop=scene)
    assert f"no evidence for the scene {scene}" in X.audible(root).detail


@pytest.mark.parametrize("change,scene", [
    ({f"t{t}": {"current_menu": 9} for t in range(21, 221)}, "title"),
    ({f"t{t}": {"current_menu": 0, "game_mode": 13} for t in range(221, 421)}, "menu"),
    ({f"t{t}": {"chao_area": 0} for t in range(421, 621)}, "garden"),       # the Chao Lobby
    ({f"t{t}": {"chao_area": 4} for t in range(421, 621)}, "garden"),       # the race
    ({f"t{t}": {"current_level": 89} for t in range(421, 621)}, "garden"),
    ({f"t{t}": {"game_state": 17} for t in range(621, 821)}, "metalharbor"),  # paused
    ({f"t{t}": {"game_mode": 4} for t in range(621, 821, 2)}, "metalharbor"),
])
def test_audible_reads_each_scene_from_the_game_s_state(tmp_path: Path, change: dict,
                                                       scene: str) -> None:
    root = project(tmp_path)
    write_audio(root, rows=audio_rows(**change))
    assert f"{scene} never held 180 ticks" in X.audible(root).detail


def test_audible_unmet_on_a_scene_passed_through_rather_than_held(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_audio(root, rows=audio_rows(**{f"t{t}": {"current_menu": 1} for t in range(150, 221)}))
    assert "title never held 180 ticks (longest 129)" in X.audible(root).detail


def test_audible_reads_metal_harbor_s_level_from_the_registry(tmp_path: Path) -> None:
    root = project(tmp_path, level=11)
    write_audio(root)
    assert "metalharbor never held 180 ticks (longest 0)" in X.audible(root).detail


@pytest.mark.parametrize("side,why", [
    ({"audio_problems": 1}, "the mixer mispredicted the runtime's state"),
    ({"ok": False}, "the WAV was not written whole"),
    ({"sample_rate": 44100}, "another rate"),
    ({"channels": 6}, "another layout"),
    ({"frames": 1}, "the sidecar and the WAV disagree"),
    ({"origin_us": -5}, "an origin that is not a count"),
])
def test_audible_unmet_on_the_sidecar(tmp_path: Path, side: dict, why: str) -> None:
    root = project(tmp_path)
    write_audio(root, side=side)
    assert X.audible(root).met is False, why


# ---- each scene's WAV is the mix of the stream its run made, and the run names the table


@pytest.mark.parametrize("kwargs,said", [
    ({"side": {"stream_sha256": "ee" * 32}}, "the WAV's stream 'eeee"),
    ({"side": {"stream_sha256": None}}, "the WAV's stream None"),
    ({"recording": {"ready__stream_sha256": "ee" * 32}}, "is not the one its run made"),
    ({"recording": {"ready": {"path": "private/audio/run.READY"}}}, "is not the one its run made"),
    ({"recording": {"ok": False}}, "is not an ok run that recorded sound"),
    ({"recording": {"audio": False}}, "is not an ok run that recorded sound"),
    ({"recording": {"ticks": None}}, "does not name the scene's per-tick table as its own"),
    ({"recording": {"ticks__sha256": "0f" * 32}}, "does not name the scene's per-tick table"),
    ({"recording": {"ticks__path": "private/audio/other.tsv"}},
     "does not name the scene's per-tick table"),
    ({"drop_recording": True}, "scenes.title.recording is not a {path, sha256} reference"),
])
def test_audible_ties_each_scene_to_the_run_that_made_its_stream(tmp_path: Path, kwargs: dict,
                                                                 said: str) -> None:
    root = project(tmp_path)
    write_audio(root, **kwargs)
    result = X.audible(root)
    assert result.met is False and said in result.detail, result.detail


def test_audible_unmet_on_a_wav_that_is_not_float(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_audio(root, tag=1)
    assert "not float32 stereo at 48000 Hz" in X.audible(root).detail


def test_audible_unmet_when_the_wav_changed_after_its_receipt(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_audio(root, own_copy=True)
    with (root / "private/audio/run.wav").open("r+b") as handle:
        handle.seek(100)
        handle.write(b"\x01\x02\x03\x04")
    assert "does not hash to the receipt's sha256" in X.audible(root).detail


def test_audible_unmet_when_virtual_time_stands_still(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_audio(root, rows=audio_rows(t300={"virtual_us": 299 * TICK_US}))
    assert "virtual_us does not advance on every tick" in X.audible(root).detail


# ------------------------------------------------------------------ clause 4: both hosts

STREAM = "5e" * 32
FRAMES = 6


def frame_rows(host: str, **change) -> list[dict]:
    rows = [{"frame": f, "seq": 200 + 30 * f, "virtual_us": f * TICK_US, "guest_frames": f,
             "polls": 0, "wall_us": 1_000_000 + f * (17_000 if host == "dxvk-native" else 19_000),
             "bgrx_hash": f"0x{0xa1c69ef15ba5d800 + f:016x}"} for f in range(1, FRAMES + 1)]
    for frame, values in change.items():
        rows[int(frame.lstrip("f")) - 1].update(values)
    return rows


def fnv1a64(data: bytes) -> int:
    """FNV-1a, 64 bits, over bytes, written apart from the module's."""
    digest = 0xCBF29CE484222325
    for byte in data:
        digest = ((digest ^ byte) * 0x100000001B3) % (1 << 64)
    return digest


def fnv_digest(hashes: list[str]) -> str:
    """The player's bgrx_digest (host/player/player.cpp, digest_step): FNV-1a 64 over each frame
    hash's eight bytes, low byte first. A hash that is not one leaves the digest unmatchable."""
    try:
        data = b"".join(int(text, 16).to_bytes(8, "little") for text in hashes)
    except (ValueError, OverflowError):
        return "0x" + "0" * 16
    return f"0x{fnv1a64(data):016x}"


def test_the_frames_digest_is_the_player_s() -> None:
    """FNV-1a's published 64-bit vectors, then the module's digest against this one. The
    module's was also checked against three DXVK player reports of the boot and Metal Harbor
    replays when it was written (fold B, 2026-10-03)."""
    assert (fnv1a64(b""), fnv1a64(b"a"), fnv1a64(b"foobar")) == \
        (0xCBF29CE484222325, 0xAF63DC4C8601EC8C, 0x85944171F73967E8)
    for rows in (frame_rows("dxvk-native"), [{"bgrx_hash": "0xffffffffffffffff"}], []):
        assert X.frames_digest(rows) == fnv_digest([row["bgrx_hash"] for row in rows])


def compare_report(root: Path, times: Path, rows: list[dict], host: str, **change) -> dict:
    report = {"tool": "host/player sa2_player", "complete": True, "pass": True, "stage": 3,
              "stream_sha256": STREAM, "stream_records_read_to_end": True, "presents": len(rows),
              "frame_records": len(rows), "present_failures": 0, "readback_failures": 0,
              "errors": 0,
              "calls": {"handled": 4181795, "ignored_by_design": 5788, "skipped_by_stage": 0,
                        "not_handled": 0},
              "textures": {"dds": 123, "png": 2, "placeholder_binds": 0},
              "host_failures": {"count": 0}, "stream_problems": {"count": 0},
              "notes": {"count": 0, "allowed": False, "first": []},
              "frames_out": {"path": "private/compare/frames.y4m", "frames": len(rows),
                             "times": times.relative_to(root).as_posix(), "times_rows": len(rows),
                             "bgrx_digest": fnv_digest([row["bgrx_hash"] for row in rows])},
              **host_files(host)}
    return patched(report, change)


def write_compare(root: Path, rows: dict[str, list[dict]] | None = None,
                  reports: dict[str, dict] | None = None, recording: dict | None = None,
                  hosts: tuple[str, ...] = ("dxvk-native", "sdl3-gpu"),
                  renderers: dict[str, bytes] | None = None, **receipt_change) -> Path:
    entries = []
    for host in hosts:
        where = root / "private" / "compare" / host
        mine = (rows or {}).get(host, frame_rows(host))
        times = write_tsv(where / "frames.y4m.times.tsv", X.TIMES_HEADER, mine)
        rep = write_json(where / "report.json",
                         compare_report(root, times, mine, host, **(reports or {}).get(host, {})))
        lib = where / "renderer"
        lib.write_bytes((renderers or {}).get(host, renderer_bytes(host)))
        entries.append({"host": host, "renderer": ref(root, lib), "report": ref(root, rep),
                        "frames": ref(root, times)})
    run = {"schema": "sa2-present-run/v3", "replay": "metalharbor", "ok": True,
           "ready": {"path": "private/mh.READY", "stream": "private/mh.sa2s",
                     "stream_sha256": STREAM}}
    run.update(recording or {})
    present_run = write_json(root / "private" / "compare" / "present-run.json", run)
    doc = stamped(X.COMPARE_TOOL, {"replay": "metalharbor", "stream_sha256": STREAM,
                                   "recording": ref(root, present_run), "hosts": entries})
    doc["schema"] = X.COMPARE_SCHEMA
    doc.update(receipt_change)
    return write_json(root / X.COMPARE_RECEIPT, doc)


def test_identical_met_when_every_frame_matches(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_compare(root)
    result = X.identical(root)
    assert (result.met, result.detail) == (True, (
        "metalharbor: 6 frames, each equal in dxvk-native and sdl3-gpu in frame, seq, "
        "virtual_us, guest_frames, polls, bgrx_hash; 6 distinct pictures"))


def test_identical_today_names_the_missing_receipt_and_tool(tmp_path: Path) -> None:
    assert X.identical(project(tmp_path)).detail == (
        f"no receipt {X.COMPARE_RECEIPT} (its writer: {X.COMPARE_TOOL}, not written yet)")


def test_identical_names_the_first_frame_that_differs(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_compare(root, rows={"sdl3-gpu": frame_rows("sdl3-gpu", f3={"bgrx_hash": "0x" + "0" * 16})})
    assert X.identical(root).detail == (
        "metalharbor frame 3 (virtual_us 50001): bgrx_hash dxvk-native 0xa1c69ef15ba5d803, "
        "sdl3-gpu 0x0000000000000000")


@pytest.mark.parametrize("column,value", [("seq", 999), ("virtual_us", 1), ("guest_frames", 9),
                                          ("polls", 1)])
def test_identical_compares_each_frame_at_its_own_tick(tmp_path: Path, column: str,
                                                      value: int) -> None:
    root = project(tmp_path)
    write_compare(root, rows={"sdl3-gpu": frame_rows("sdl3-gpu", f5={column: value})})
    assert f"metalharbor frame 5" in X.identical(root).detail


def test_identical_unmet_on_a_frame_one_host_did_not_present(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_compare(root, rows={"sdl3-gpu": frame_rows("sdl3-gpu")[:-1]})
    assert X.identical(root).detail == "metalharbor: dxvk-native presented 6 frames, sdl3-gpu 5"


def test_identical_unmet_on_one_picture_throughout(tmp_path: Path) -> None:
    root = project(tmp_path)
    black = {f"f{f}": {"bgrx_hash": "0x4bd87a07a4536325"} for f in range(1, FRAMES + 1)}
    write_compare(root, rows={host: frame_rows(host, **black) for host, _ in X.HOSTS})
    assert X.identical(root).detail.endswith("1 distinct picture(s): a comparison of nothing")


@pytest.mark.parametrize("host,change,why", [
    ("sdl3-gpu", {"stream_sha256": "77" * 32}, "another stream"),
    ("sdl3-gpu", {"stream_records_read_to_end": False}, "the stream not read to its end"),
    ("dxvk-native", {"pass": False}, "a failed replay"),
    ("dxvk-native", {"complete": False}, "a report written before the end"),
    ("sdl3-gpu", {"presents": 7}, "presents and rows disagree"),
    ("sdl3-gpu", {"frame_records": 7}, "a frame record never presented"),
    ("dxvk-native", {"readback_failures": 1}, "a frame not read back"),
    ("dxvk-native", {"calls__not_handled": 1}, "a call not handled"),
    ("sdl3-gpu", {"notes": {"count": 1, "allowed": True, "first": ["x"]}}, "a NOTE record"),
    ("sdl3-gpu", {"frames_out__times": "private/compare/x.tsv"},
     "the report names another times file"),
])
def test_identical_unmet_on_a_host_report(tmp_path: Path, host: str, change: dict, why: str) -> None:
    root = project(tmp_path)
    write_compare(root, reports={host: change})
    assert X.identical(root).met is False, why


@pytest.mark.parametrize("change,why", BELOW_FULL_STAGE)
@pytest.mark.parametrize("host", ["dxvk-native", "sdl3-gpu"])
def test_identical_unmet_below_the_player_s_full_stage(tmp_path: Path, host: str, change: dict,
                                                      why: str) -> None:
    """The blocker again: two hosts drawing the same white placeholders agree on every frame."""
    root = project(tmp_path)
    write_compare(root, reports={host: change})
    result = X.identical(root)
    assert result.met is False and result.detail.startswith(f"{host} report"), (why, result.detail)


@pytest.mark.parametrize("change,said", [
    ({"frames_out__bgrx_digest": "0x0123456789abcdef"}, "rows digesting to '0x0123456789abcdef'"),
    ({"frames_out__bgrx_digest": None}, "rows digesting to None"),
    ({"frames_out__times_rows": 5}, "the report's 5 rows"),
])
def test_identical_ties_each_times_file_to_its_report_by_the_player_s_digest(
        tmp_path: Path, change: dict, said: str) -> None:
    root = project(tmp_path)
    write_compare(root, reports={"sdl3-gpu": change})
    result = X.identical(root)
    assert result.met is False and said in result.detail, result.detail


def test_identical_unmet_on_a_times_file_another_report_digested(tmp_path: Path) -> None:
    """A times table swapped for another run's, with the swapped report's path: the digest the
    report made of its own frames is not the table's."""
    root = project(tmp_path)
    write_compare(root, reports={"dxvk-native": {
        "frames_out__bgrx_digest": fnv_digest([r["bgrx_hash"] for r in frame_rows("x")[:-1]])}})
    assert "dxvk-native: the report's 6 rows digesting to" in X.identical(root).detail


def test_identical_accepts_the_times_file_by_its_absolute_path(tmp_path: Path) -> None:
    """Reports from some runs record the times file absolutely; the same file is the same file."""
    root = project(tmp_path)
    times = (root / "private/compare/dxvk-native/frames.y4m.times.tsv").resolve()
    write_compare(root, reports={"dxvk-native": {"frames_out__times": str(times)}})
    assert X.identical(root).met is True


@pytest.mark.parametrize("host,renderers,change,said", [
    ("dxvk-native", {"dxvk-native": b"another build of DXVK"}, {},
     "its renderer is not the DXVK library tools/pins.json pins"),
    ("dxvk-native", {}, {"files__libdxvk_d3d9__sha256": "77" * 32},
     "the player did not load and verify the pinned DXVK library"),
    ("sdl3-gpu", {"sdl3-gpu": renderer_bytes("dxvk-native")}, {"files__player__sha256": PIN},
     "its renderer is DXVK's library"),
    ("sdl3-gpu", {}, {"files__libdxvk_d3d9__sha256": PIN}, "the player pins a DXVK library"),
    ("sdl3-gpu", {}, {"files__player__sha256": "e1" * 32},
     "the player that ran (files.player) is not the receipt's renderer"),
])
def test_identical_holds_each_renderer_to_what_its_player_loaded(tmp_path: Path, host: str,
                                                                renderers: dict, change: dict,
                                                                said: str) -> None:
    """Fold review: two renderers one byte apart passed; now each is the file its player
    loaded, DXVK's at the pin, and the GPU host loads no DXVK."""
    root = project(tmp_path)
    write_compare(root, reports={host: change}, renderers=renderers)
    result = X.identical(root)
    assert result.met is False and said in result.detail, result.detail


@pytest.mark.parametrize("change,why", [
    ({"ok": False}, "the presentation run did not hold to the replay"),
    ({"replay": "boot"}, "a run of another replay"),
    ({"schema": "sa2-present-run/v2"}, "an older run receipt"),
    ({"ready": {"stream_sha256": "66" * 32}}, "a run that made another stream"),
])
def test_identical_unmet_on_the_recording(tmp_path: Path, change: dict, why: str) -> None:
    root = project(tmp_path)
    write_compare(root, recording=change)
    assert X.identical(root).met is False, why


@pytest.mark.parametrize("kwargs,why", [
    ({"hosts": ("dxvk-native",)}, "one host"),
    ({"hosts": ("dxvk-native", "dxvk-native")}, "one host twice"),
    ({"hosts": ("dxvk-native", "sdl3-gpu", "unity")}, "a host the record does not select"),
    ({"renderers": {"dxvk-native": b"same", "sdl3-gpu": b"same"}}, "one renderer"),
    ({"replay": "garden"}, "a replay the registry does not hold"),
    ({"replay": ["metalharbor"]}, "a replay that is not a name"),
    ({"stream_sha256": "typed"}, "a stream digest that is not one"),
    ({"schema": "sa2-s5-host-compare/v0"}, "another schema"),
])
def test_identical_unmet_on_the_receipt(tmp_path: Path, kwargs: dict, why: str) -> None:
    root = project(tmp_path)
    write_compare(root, **kwargs)
    assert X.identical(root).met is False, why


def test_identical_compares_metal_harbor_s_replay_and_no_other(tmp_path: Path) -> None:
    """Fold review: a boot replay passed with six frames. The boot is registered and both hosts
    agree on it here, and it still is not the exit's stage."""
    root = project(tmp_path)
    write_compare(root, replay="boot", recording={"replay": "boot"})
    assert X.identical(root).detail == (f"{X.COMPARE_RECEIPT}: replay 'boot', not 'metalharbor', "
                                        f"the stage the play clause names")


@pytest.mark.parametrize("value", ["0xA1C69EF15BA5D801", "a1c69ef15ba5d801", "0x123", ""])
def test_identical_unmet_on_a_hash_that_is_not_the_player_s(tmp_path: Path, value: str) -> None:
    root = project(tmp_path)
    write_compare(root, rows={host: frame_rows(host, f1={"bgrx_hash": value})
                              for host, _ in X.HOSTS})
    assert X.identical(root).met is False


def test_identical_ignores_wall_time_alone(tmp_path: Path) -> None:
    """Each host's own clock: the rows above already differ in wall_us at every frame."""
    root = project(tmp_path)
    write_compare(root)
    rows = {host: frame_rows(host) for host, _ in X.HOSTS}
    assert all(a["wall_us"] != b["wall_us"] for a, b in zip(rows["dxvk-native"], rows["sdl3-gpu"]))
    assert X.identical(root).met is True


def test_identical_unmet_when_the_selection_record_names_another_host(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_compare(root)
    edit_record(root, "the second is a renderer on SDL3's GPU API", "the second is a renderer on bgfx")
    assert X.identical(root).detail == f"{X.SELECTION} item 1 does not name SDL3's GPU API"


# ------------------------------------------------------------------ the constants' sources


def enum(header: str, name: str) -> dict[str, int]:
    text = (MODLOADER / header).read_text(errors="replace")
    body = re.search(r"enum\s+" + name + r"\b[^{]*\{(.*?)\};", text, re.S).group(1)
    body = re.sub(r"//[^\n]*|/\*.*?\*/", "", body, flags=re.S)
    values, following = {}, 0
    for item in (part.strip() for part in body.split(",")):
        if not item:
            continue
        key, _, value = (piece.strip() for piece in item.partition("="))
        following = int(value, 0) if value else following
        values[key] = following
        following += 1
    return values


def test_the_game_state_values_are_the_mod_loader_s() -> None:
    assert enum("SA2Enums.h", "GameMode")["GameMode_Level"] == X.GAME_MODE_LEVEL
    assert enum("SA2Enums.h", "GameMode")["GameMode_Advertise"] == X.GAME_MODE_ADVERTISE
    assert enum("SA2Enums.h", "GameStates")["GameStates_Ingame"] == X.GAME_STATE_INGAME
    menus = enum("SA2Enums.h", "Menus")
    assert (menus["Menus_TitleScreen"], menus["Menus_Main"]) == (X.MENU_TITLE, X.MENU_MAIN)
    levels = enum("SA2Enums.h", "LevelIDs")
    assert levels["LevelIDs_ChaoWorld"] == X.LEVEL_CHAO_WORLD
    assert levels["LevelIDs_MetalHarbor"] == X.stage_level(ROOT)
    areas = enum("SA2Enums.h", "ChaoArea")
    assert tuple(areas[f"ChaoArea_{g}Garden"] for g in ("Neutral", "Hero", "Dark")) == X.CHAO_GARDENS


WIDTHS = {"int": 4, "__int16": 2, "EntityData1*": 4}


def test_the_state_words_are_where_the_mod_loader_puts_them() -> None:
    text = (MODLOADER / "SA2Variables.h").read_text(errors="replace")
    for column, (name, address, width) in X.STATE_WORDS.items():
        if name is None:
            continue
        found = re.search(r"Data(?:Pointer|Array)\(\s*([^,]+?)\s*,\s*" + name
                          + r",\s*(0x[0-9A-Fa-f]+)", text)
        assert found and int(found.group(2), 16) == address, column
        assert WIDTHS[found.group(1)] == width, column
    assert X.STATE_WORDS["goal_latch"][1:] == (0x0174B002, 1)


def test_the_session_reading_is_stage_boot_s() -> None:
    if str(ROOT / "tools" / "s4") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import stage_boot as SB
    assert (SB.GAME_MODE_LEVEL, SB.GAME_STATE_INGAME) == (X.GAME_MODE_LEVEL, X.GAME_STATE_INGAME)
    assert int(SB.GOAL_LATCH, 16) == X.STATE_WORDS["goal_latch"][1]


def test_the_formats_are_the_player_s() -> None:
    player = (ROOT / "host/player/player.cpp").read_text()
    assert '"' + "\\t".join(X.TIMES_HEADER) + "\\n" + '"' in player
    assert "static constexpr uint32_t kRate = 48000, kChannels = 2;" in \
        (ROOT / "host/player/mixer.h").read_text()
    assert (X.SAMPLE_RATE, X.CHANNELS) == (48000, 2)
    keyboard = (ROOT / "host/player/input.cpp").read_text()
    for name in ("host_input", "started", "key_events", "records_written", "write_errors",
                 "last_seq"):
        assert f'\\"{name}\\"' in keyboard, name
    for name in (*X.WINDOW_UNSEEN, "frames_with_flags", "complete", "presents", "frame_records",
                 "stream_records_read_to_end", "frames_out", "times", "stream_sha256", "stage",
                 "stream_header", "files", "player", "libdxvk_d3d9", "host_identity",
                 "dxvk_file_verified", "times_rows", "bgrx_digest",
                 *(part for dotted in X.REPORT_ZERO for part in dotted.split("."))):
        assert f'\\"{name}\\"' in player, name
    # --stage 3 is the player's full stage, and the digest is the one frames_digest computes.
    assert "--stage N" in player and "3: + lock uploads, PNG, D3DX mip chains" in player
    assert "bgrx_digest_ = 0xcbf29ce484222325ull" in player
    assert "d *= 0x100000001b3ull;" in player
    assert (X.FNV_BASIS, X.FNV_PRIME) == (0xCBF29CE484222325, 0x100000001B3)


def test_the_run_record_is_the_runtime_s() -> None:
    """The keyboard fields the play clause reads are the ones harness/crt_probe.c writes from
    runtime/sa2_dinput.c's own counts, and "fifo" is the runtime's name for the player's FIFO."""
    probe = (ROOT / "harness/crt_probe.c").read_text()
    assert '\\"dinput\\": {' in probe and '\\"host_input\\": {\\"source\\": ' in probe
    for name in X.RUN_KEYBOARD:
        assert f'\\"{name}\\": ' in probe, name
    runtime = (ROOT / "runtime/sa2_dinput.c").read_text()
    assert 'g_in.kind == IN_FIFO ? "fifo"' in runtime and X.INPUT_SOURCE == "fifo"
    recorder = (ROOT / "host/sa2_present_rec.c").read_text()
    assert 'the text the stream header carries as "meta"' in recorder


def test_the_session_meta_and_wrap_exclusion_are_play_live_s() -> None:
    """PLAY_META is what tools/s5/play_live.py writes into SA2_REC_META, and the input replay
    the contract says a session does not link is the one play_live takes out of stage_run."""
    live = (ROOT / X.PLAY_TOOL).read_text()
    assert X.PLAY_META == {"runner": X.PLAY_TOOL, "interactive": True, "scene": "metalharbor"}
    assert ('"runner": "tools/s5/play_live.py",\n            "transport": "fifo", '
            '"interactive": True,') in live
    assert 'SCENES = {"boot": ("present-play-boot", "boot"), "metalharbor": ' in live
    assert 'INPUT_REPLAY_HOOK = "harness/replay/wrap_input_replay.c"' in live
    assert 'INPUT_REPLAY_WRAP = "sub_77e780"' in live
    assert '"SA2_INPUT_SCRIPT", "SA2_INPUT_APPLIED")' in live
    assert "harness/replay/wrap_input_replay.c" in (ROOT / "tools/s4/stage_run.py").read_text()
    for words in ("links no", "harness/replay/wrap_input_replay.c", "SA2_INPUT_SCRIPT"):
        assert words in " ".join(X.__doc__.split()), words


def test_the_selection_record_as_committed_selects_both_hosts() -> None:
    assert X.selection(ROOT) == ["dxvk-native", "sdl3-gpu"]


def test_the_dxvk_pin_as_committed_is_a_sha256() -> None:
    assert X.SHA256.match(X.dxvk_pin(ROOT))


def test_the_definitions_hold_their_values() -> None:
    """The shape pins normalise digits, so a threshold could move without failing them; these
    are the values the notes state and the falsifiers anchor on."""
    assert (X.AFTER_GOAL_TICKS, X.MIN_KEY_EVENTS, X.MIN_DISTINCT_PICTURES, X.SCENE_MIN_TICKS,
            X.AUDIBLE_DBFS, X.PLAYER_STAGE, X.INPUT_SOURCE, X.STAGE) == \
        (60, 1, 2, 180, -40.0, 3, "fifo", "metalharbor")
    assert X.REPORT_ZERO == ("present_failures", "readback_failures", "errors",
                             "calls.not_handled", "calls.skipped_by_stage",
                             "textures.placeholder_binds", "host_failures.count",
                             "stream_problems.count", "notes.count")
    assert X.RUN_KEYBOARD == ("script_lines", "records", "last_seq", "seq_gaps",
                              "partial_dropped", "keys_written")
    assert [key for key, _ in X.SESSION_APART] == ["renderer", "report", "ticks", "run", "run_id"]
    assert (X.MIN_LATENCY_PRESSES, X.LATENCY_EVIDENCE, X.SESSION_EVIDENCE) == (
        20, ("keys", "polls", "presents", "relay_frames", "poll_report", "relay_report"),
        "session")
    assert (X.PINS, X.DXVK_PIN, X.STREAM_OF_RECORDING) == \
        ("tools/pins.json", "tools.dxvk.library_sha256", "ready.stream_sha256")


# ------------------------------------------------------------------ the live criteria


def g5() -> list[dict]:
    return json.loads(CRITERIA_FILE.read_text())["G5"]


def criterion(name: str) -> dict:
    found = [entry for entry in g5() if entry["criterion"] == X.CRITERIA[name]]
    assert len(found) == 1, name
    return found[0]


def flat(text: str) -> str:
    """Prose with its Markdown quoting, emphasis and line breaks taken out."""
    lines = (line.strip().lstrip(">").strip() for line in text.splitlines())
    return " ".join(" ".join(lines).replace("**", "").split())


def test_the_criteria_quote_design_5_s_exit_sentence() -> None:
    """Each criterion is a clause of the design's exit sentence, word for word, with the
    struck host left out (fold review: nothing tied the texts to the design)."""
    design = (ROOT / "Documentation/SA2_RECOMP_DESIGN.md").read_text()
    section = design.split("### S5. Presentation", 1)[1].split("### S6.", 1)[0]
    sentence = section.split("Exit: ", 1)[1].split("\n\n", 1)[0]
    clauses = flat(re.sub(r"~~.*?~~", "", sentence, flags=re.S)).rstrip(".").split("; ")
    assert [f"{clause} (design 5, S5 exit)" for clause in clauses] == list(X.CRITERIA.values())


@pytest.mark.parametrize("name", list(X.CRITERIA))
def test_each_clause_is_a_criterion_of_its_own_calling_its_predicate(name: str) -> None:
    entry = criterion(name)
    assert entry["criterion"].endswith("(design 5, S5 exit)")
    assert f"r=s.{name}(" in entry["check"] and "import check_s5_exit as s" in entry["check"]


def test_g5_holds_four_s5_exit_criteria_and_its_floor_reads_them() -> None:
    entries = g5()
    assert [e["criterion"] for e in entries if e["criterion"].endswith("(design 5, S5 exit)")] \
        == list(X.CRITERIA.values())
    floor = next(e for e in entries if e["criterion"].startswith("every clause of S5's exit"))
    done = subprocess.run(floor["check"].replace("{python}", shlex.quote(sys.executable)),
                          shell=True, cwd=ROOT, capture_output=True, text=True)
    assert (done.returncode, done.stdout.strip()) == (0, "S5 exit criteria: 4")


def run_check(name: str, root: Path, install: bool = True) -> tuple[int, str]:
    """The criterion's check, run from `root` exactly as gate_report.py runs it."""
    if install:
        scripts = root / "Documentation" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPTS / "check_s5_exit.py", scripts / "check_s5_exit.py")
    command = criterion(name)["check"].replace("{python}", shlex.quote(sys.executable))
    done = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    return done.returncode, lines[-1] if lines else ""


def test_the_play_criterion_runs_as_gate_report_runs_it(tmp_path: Path) -> None:
    root = both_hosts(tmp_path)
    status, last = run_check("play", root)
    assert (status, last.startswith("S5 play dxvk-native: level 10 entered")) == (0, True)
    (root / X.PLAY_RECEIPT.format(host="sdl3-gpu")).unlink()
    assert run_check("play", root)[0] == 1


THREE_WAY_RECEIPT = "Documentation/receipts/d3d9-methods.json"
HELD = {"measured": True, "not_held": [], "why_not_measured": "measured"}


def three_way(root: Path, block: dict | None) -> None:
    """M7's receipt carrying the user's three-way numerator block, or no block at all."""
    path = root / THREE_WAY_RECEIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {} if block is None else {"three_way_numerator": block}
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")


def test_the_m7_criterion_runs_as_gate_report_runs_it(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    metrics(root, MET_M7)
    three_way(root, HELD)
    assert run_check("m7", root) == (0, f"M7 {MET_M7}; three-way numerator held for every "
                                        f"called method")
    metrics(root, "58/67 (86.6%); " + CEILING.format(4126, 4263) + MISSED.format(9))
    assert run_check("m7", root)[0] == 1
    # The user's ruling of 2026-10-09: a whole row with a nonzero ceiling and misses is met.
    metrics(root, MET_M7_NONZERO)
    assert run_check("m7", root) == (0, f"M7 {MET_M7_NONZERO}; three-way numerator held for every "
                                        f"called method")


def test_the_m7_criterion_holds_the_users_three_way_numerator(tmp_path: Path) -> None:
    """The user's second ruling of 2026-10-04: a method counts only when the device answers
    it, the recorder captures it and both players handle it. A whole device-only row is not
    enough: the check reads the receipt's three-way block, and is unmet while it is absent or
    unmeasured, or names a called method not held (M7 fold review, BLOCKER)."""
    root = tmp_path / "root"
    root.mkdir()
    metrics(root, MET_M7)
    assert run_check("m7", root) == (1, f"M7 {MET_M7}; three-way numerator absent from "
                                        f"{THREE_WAY_RECEIPT}")
    three_way(root, None)
    assert run_check("m7", root) == (1, f"M7 {MET_M7}; three-way numerator absent from "
                                        f"{THREE_WAY_RECEIPT}")
    three_way(root, {"measured": False, "not_held": None, "why_not_measured": "no tool yet"})
    assert run_check("m7", root) == (1, f"M7 {MET_M7}; three-way numerator not shown: measured "
                                        f"False, not held None")
    three_way(root, {**HELD, "not_held": ["IDirect3DDevice9::Reset"]})
    assert run_check("m7", root) == (1, f"M7 {MET_M7}; three-way numerator not shown: measured "
                                        f"True, not held 1")
    three_way(root, HELD)
    assert run_check("m7", root)[0] == 0


def test_the_audio_criterion_runs_as_gate_report_runs_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_audio(root)
    assert run_check("audible", root)[0] == 0
    write_audio(root, levels={"menu": 0.0})
    assert run_check("audible", root)[0] == 1


def test_the_hosts_criterion_runs_as_gate_report_runs_it(tmp_path: Path) -> None:
    root = project(tmp_path)
    write_compare(root)
    assert run_check("identical", root)[0] == 0
    write_compare(root, rows={"sdl3-gpu": frame_rows("sdl3-gpu", f2={"bgrx_hash": "0x" + "1" * 16})})
    assert run_check("identical", root)[0] == 1


def owed(entry: dict, landed: list[str]) -> list[str]:
    """What a criterion still owes for the receipts that exist: a pinned shape for each, and a
    falsifier naming one of them in every clause (fold review: per clause, not per criterion)."""
    pinned = {shape["file"] for shape in entry.get("evidence_shape", [])}
    missing = [f"no shape pinned for {receipt}" for receipt in landed if receipt not in pinned]
    if landed:
        missing += [f"clause {clause['promise']!r} names none of {landed} in a falsifier"
                    for clause in entry["clauses"]
                    if not {f["file"] for f in clause["falsifiers"]} & set(landed)]
    return missing


@pytest.mark.parametrize("name", list(X.CRITERIA))
def test_a_receipt_that_exists_is_falsified_in_every_clause_and_pinned(name: str) -> None:
    """While a receipt is absent its criterion fails and its falsifiers are deferred, so the
    ones it declares mutate the evidence that exists. Once the receipt lands, every clause of
    the criterion owes a falsifier on it, and the criterion a pinned shape for it."""
    landed = [receipt for receipt in X.RECEIPTS[name] if (ROOT / receipt).exists()]
    assert owed(criterion(name), landed) == []


def test_the_owed_rule_can_fail() -> None:
    """The rule fails an entry that owes: the real play entry with its receipt shapes and every
    falsifier naming a receipt taken out (the receipts exist now, so the entry itself owes
    nothing) owes a shape and one falsifier in each clause."""
    receipts = set(X.RECEIPTS["play"])
    entry = copy.deepcopy(criterion("play"))
    entry["evidence_shape"] = [s for s in entry.get("evidence_shape", []) if s["file"] not in receipts]
    for clause in entry["clauses"]:
        clause["falsifiers"] = [f for f in clause["falsifiers"] if f["file"] not in receipts]
    landed = [X.PLAY_RECEIPT.format(host="dxvk-native")]
    assert len(owed(entry, landed)) == 1 + len(entry["clauses"])


def test_the_notes_state_the_module_s_numbers() -> None:
    audio = criterion("audible")["criterion_note"]
    assert f"{X.AUDIBLE_DBFS:.0f} dBFS" in audio and f"{X.SCENE_MIN_TICKS} ticks" in audio
    for value in (X.GAME_MODE_ADVERTISE, X.MENU_TITLE, X.MENU_MAIN, X.LEVEL_CHAO_WORLD):
        assert f"({value})" in audio
    play = criterion("play")["criterion_note"]
    assert f"{X.AFTER_GOAL_TICKS} ticks past the goal" in play
    for name in ("play", "identical"):
        note = criterion(name)["criterion_note"]
        assert f"full stage ({X.PLAYER_STAGE})" in note and "placeholder texture" in note, name
        assert "skipped by stage" in note and X.PINS in note, name
    assert f"FIFO ({X.INPUT_SOURCE})" in play
    assert f"at least {X.MIN_LATENCY_PRESSES} presses presented between entering the stage" in play


def test_the_play_criterion_holds_the_latency_ruling_in_a_clause_of_its_own() -> None:
    """The user's ruling of 2026-10-04: the live-play receipt reports input latency. The play
    criterion declares it as a clause, whose falsifiers (applied to met evidence above) move
    the press floor, the poll log's header, the receipt's equality to the recomputed join and
    each synthetic refusal, the report's count and the sender log's row, inverted; the stream
    meta's host, in the session and in the join, inverted; and the clean stops emptied: each
    then refuses the met session, so each refusal is shown live in the check, not only its
    field read."""
    clause = next(c for c in criterion("play")["clauses"] if "input latency" in c["promise"])
    assert clause["promise"].startswith("its live-play receipt reports input latency, button "
                                        "press to presented frame")
    assert {f["replace"]: f["with"] for f in clause["falsifiers"]
            if f["file"] == "Documentation/scripts/check_s5_exit.py"} == {
        "MIN_LATENCY_PRESSES = 20": "MIN_LATENCY_PRESSES = 1000000000",
        'PLAY_POLLS = ("poll", ': 'PLAY_POLLS = ("polled", ',
        "    if synthetic:\n": "    if not synthetic:\n",
        "if row['synthetic'] != 0 and not synthetic:":
            "if row['synthetic'] == 0 and not synthetic:",
        'if not same_json_value(latency, doc.get("input_latency")):':
            'if same_json_value(latency, doc.get("input_latency")):',
        'or meta["host"] != host:': 'or meta["host"] == host:',
        "expect(meta, 'host', host)": "expect(meta, 'host', None)",
        'CLEAN_STOPS = ("the game had ended", "SIGTERM: WM_QUIT at the game\'s next keyboard poll")':
            "CLEAN_STOPS = ()"}


# Sentences the notes quote, and the record each is quoted from (AGENTS.md rule 15: quote
# what the records already decided, rather than asking it again).
QUOTED = {
    "play": [("The stage route (test spawn for S4) is ratified.",
              "Documentation/decisions/2026-09-23-S4-stage-route.md"),
             ("Test spawn does not count toward D1 at S6; keep the criterion_note scoping M6's "
              "boot count to G4.", "Documentation/decisions/2026-09-23-S4-stage-route.md"),
             ("That is S5's two-host exit.", X.SELECTION),
             ("Test-spawn starts are approved for G5; S6's D1 still requires normal entry.",
              "Documentation/decisions/2026-10-04-S5-user-rulings.md"),
             ("confirm all six readings.", "Documentation/decisions/2026-10-04-S5-user-rulings.md"),
             ("Presentation compared by screenshot hash, not state",
              "Documentation/SA2_RECOMP_DESIGN.md"),
             ("Its live-play receipt reports input latency, button press to presented frame.",
              "Documentation/decisions/2026-10-04-S5-user-rulings.md")],
    "identical": [("checked against the DXVK picture frame by frame", X.SELECTION),
                  ("That is S5's two-host exit.", X.SELECTION)],
    "m7": [("A later stage that reads this file as a renderer's coverage would be reading it "
            "wrongly.", "tools/s4/device_status.py"),
           ("d3d9/d3dx9 methods implemented / methods called by the binary",
            "Documentation/SA2_RECOMP_DESIGN.md"),
           ("Recording d3d9/d3dx9 device: implement the methods the binary uses (measured from "
            "the import table and call sites; M7's denominator).",
            "Documentation/SA2_RECOMP_DESIGN.md"),
           ("Regenerate M7 with the full denominator (recorded runs plus static call sites) and "
            "add a staleness check like run reach's, so M7 cannot silently go stale again.",
            "Documentation/decisions/2026-10-04-S5-user-rulings.md"),
           ("M7's numerator: a method counts only when the device answers it, the recorder "
            "captures it, and both players handle it.",
            "Documentation/decisions/2026-10-04-S5-user-rulings-2.md"),
           ("the methods the scan proves plus what runs observe, which is the union I ruled on "
            "2026-10-04, not every method the binary could call.",
            "Documentation/decisions/2026-10-09-S5-user-rulings.md"),
           ("The could-call space is handled by enforcing the closed world at run time.",
            "Documentation/decisions/2026-10-09-S5-user-rulings.md"),
           ("Record that as my interpretation.",
            "Documentation/decisions/2026-10-09-S5-user-rulings.md"),
           ("Count the device's per-method table from every game run, with or without the "
            "recorder, so a new method grows the list and turns M7 red at the next push.",
            "Documentation/decisions/2026-10-09-S5-user-rulings.md")],
}


@pytest.mark.parametrize("name", list(QUOTED))
def test_the_notes_quote_the_records_word_for_word(name: str) -> None:
    note = criterion(name)["criterion_note"]
    for quote, source in QUOTED[name]:
        assert f'"{quote}"' in note, quote
        text = (ROOT / source).read_text()
        if source.endswith(".py"):
            text = re.sub(r'"\s*\n\s*"', "", text)      # a Python string split over lines
        assert quote in flat(text), (quote, source)


def test_the_play_note_quotes_the_users_test_spawn_ruling() -> None:
    """The user's ruling 4 of 2026-10-04 approved test-spawn starts for G5 and kept S6's D1 on
    normal entry, so the note names the route and quotes the ruling (QUOTED), and no longer puts
    the route, or a replayed log in the second host, to the user. The ruling approved test-spawn
    starts, not every route, so the note admits no start "by any route" (readings review,
    finding 1)."""
    play = criterion("play")["criterion_note"]
    for words in ("harness/replay/wrap_test_spawn.c", "tools/s4/stage_run.py", "2026-09-23",
                  "Documentation/decisions/2026-10-04-S5-user-rulings.md"):
        assert words in play, words
    for words in ("The route there is for the user to confirm", "Neither sentence rules on S5's "
                  "exit", "The agent's argument that the route", "for the user to decide",
                  "not an open question", "by any route"):
        assert words not in play, words


def test_the_play_note_claims_no_more_than_the_run_record_shows() -> None:
    """Wording review NITs: the check sees DirectInput script lines, not the SA2_INPUT_SCRIPT
    wrap; and a replay through the keyboard's script is what fails, as the residual says."""
    play = criterion("play")["criterion_note"]
    assert "and no DirectInput script line did" in play
    assert "and no script did" not in play
    assert "A session replayed through the keyboard's script fails" in play
    assert "replayed from a log" not in play


def test_the_play_note_claims_the_keyboard_no_further_than_the_check() -> None:
    """Confirm review SHOULD-FIX: "and only it" overclaimed what the run record shows; the note
    names the input replay wrap play_live leaves out and the meta the check reads."""
    play = criterion("play")["criterion_note"]
    assert "and only it" not in play
    for words in ("harness/replay/wrap_input_replay.c", "SA2_INPUT_SCRIPT",
                  "rests on the receipt's writer", "runner tools/s5/play_live.py",
                  "interactive true", "scene metalharbor", "the run record reads ok"):
        assert words in play, words


def test_the_m7_note_names_its_numerator() -> None:
    """Fold review: the numerator is S4's "the device answers"; the note says so, and that the
    players' handling of a method no run reaches is held by nothing here."""
    note = criterion("m7")["criterion_note"]
    for words in ("tools/s4/device_status.py", "not written into the stream",
                  "Documentation/decisions/2026-10-04-S5-user-rulings-2.md",
                  "necessary for the criterion and not sufficient", "synthetic stream",
                  "three_way_numerator", "one both players refuse is held by neither",
                  "design 8's M7 row", "S5's work item"):
        assert words in note, words
    assert "Strengthened, not loosened" not in note


def test_the_m7_note_records_the_users_ruling_of_2026_10_09() -> None:
    """The user's ruling of 2026-10-09, item 1: their reading of "methods called by the binary",
    recorded as theirs; "ceiling zero" and "missed zero" dropped as pass conditions, a loosening
    that is the user's; the ceiling and the misses printed and guarded as instrument health; and
    the device's table counted from every game run."""
    note = criterion("m7")["criterion_note"]
    for words in ("Documentation/decisions/2026-10-09-S5-user-rulings.md",
                  "recorded as theirs on 2026-10-09", "closed world",
                  'Drop "ceiling zero" and "missed zero" as G5 pass conditions.',
                  "dropping them is a loosening, and it is the user's", "AGENTS.md rule 10",
                  "neither is required to be zero", "instrument health", "instrument_growth",
                  "ceiling_sites", "Documentation/scripts/check_boundary.py",
                  "with or without the recorder", "device_table_census", "a fall is accepted"):
        assert words in note, words
    for words in ("and it reads zero", "a row that says the scan missed any is unmet",
                  "100 % is not a goal until that ceiling is zero",
                  "The row's predicate is unchanged"):
        assert words not in note, words
    clause = next(c for c in criterion("m7")["clauses"] if c["promise"] == "of used device methods")
    assert "neither is required to be zero" in clause["checked_as"]
    assert "zero" not in criterion("m7")["met_state"][0]["with"].split("|")[2].split(";")[0]


def test_the_m7_met_state_states_a_nonzero_ceiling_and_misses(tmp_path: Path) -> None:
    """The met state stands in for the day M7 is met under the user's ruling, so its row carries a
    ceiling and misses above zero, and the check passes on it."""
    met = criterion("m7")["met_state"][0]
    row = met["with"].split("\n")[0]
    value = row.split("|")[2].strip()
    assert X.M7_FLOOR.search(value) and X.M7_MISSED.search(value)
    assert int(X.M7_FLOOR.search(value).group(1)) > 0 and int(X.M7_MISSED.search(value).group(1)) > 0
    assert X.m7(metrics(tmp_path, value)).met is True


def test_the_m7_note_names_its_full_denominator() -> None:
    """The user's ruling 1 of 2026-10-04: the denominator is the recorded runs plus the static
    call sites, held to the tree by a staleness gate that also holds it to only grow, and a hand
    attribution is never the scan's."""
    note = criterion("m7")["criterion_note"]
    for words in ("Documentation/receipts/m7-recorded-runs.json",
                  "Documentation/m7-census-supplement.json",
                  "Documentation/scripts/check_m7_inputs.py", "The denominator only grows",
                  "re-recorded or retired", "never counted as the scan's",
                  "this is a strengthening"):
        assert words in note, words
    assert "for the user to confirm" not in note


LABELS = {"play": "S5 play", "m7": "M7", "audible": "S5 audio", "identical": "S5 hosts"}
LABEL_WORDS = {name: len(label.split()) for name, label in LABELS.items()}


def met_root(tmp_path: Path, name: str) -> Path:
    """A crafted root on which `name`'s criterion is met, with check_s5_exit.py installed."""
    root = project(tmp_path)
    if name == "play":
        for host, _ in X.HOSTS:
            write_play(root, host)
    elif name == "m7":
        metrics(root, MET_M7)
        three_way(root, HELD)
    elif name == "audible":
        write_audio(root)
    else:
        write_compare(root)
    assert run_check(name, root)[0] == 0, name
    return root


@pytest.mark.parametrize("name", list(X.CRITERIA))
def test_every_declared_falsifier_fails_its_check_on_met_evidence(tmp_path: Path,
                                                                  name: str) -> None:
    """check_criterion_clauses.py defers a criterion's falsifiers while its control fails, which
    is today for three of these four. Here each falsifier gate-criteria.json declares is applied
    as that gate applies it, to a root where the criterion is met: every one must fail the check,
    and undoing it must pass it again."""
    root = met_root(tmp_path, name)
    applied = 0
    for clause in criterion(name)["clauses"]:
        for falsifier in clause["falsifiers"]:
            path = root / falsifier["file"]
            saved = path.read_bytes()
            if falsifier.get("absent") is True:
                path.unlink()
            else:
                text = saved.decode()
                assert text.count(falsifier["replace"]) == falsifier.get("occurrences", 1)
                path.write_text(text.replace(falsifier["replace"], falsifier["with"]))
            status, last = run_check(name, root, install=False)
            path.write_bytes(saved)
            # The check's own verdict line, not a traceback: a crash also exits 1.
            assert (status, last.split(" ", 2)[:LABEL_WORDS[name]]) == \
                (1, LABELS[name].split()), (clause["promise"], falsifier, last)
            applied += 1
    assert applied >= len(criterion(name)["clauses"])
    assert run_check(name, root, install=False)[0] == 0


def scene_met(scene: str) -> str:
    return f"{scene} -23.0 dBFS at second 1 of the scene, at least -40"


# What each falsifier of the audio criterion makes its verdict say, scene by scene, in the
# order gate-criteria.json declares them; a scene a falsifier does not name stays met. The gate
# and the test above read only the exit status and the label, so a falsifier that merely broke
# the receipt's JSON, or failed another scene than its clause's, passed both (audible fold
# review, SHOULD-FIX).
AUDIBLE_FAILS = [
    ("audio audible", "AUDIBLE_DBFS = 40.0",
     {scene: f"{scene}: loudest of 3 whole seconds -23.0 dBFS, under 40"
      for scene in X.SCENE_ORDER}),
    ("audio audible", 'STREAM_OF_RECORDING = "ready.stream_sha512"',
     {scene: (f"{X.AUDIO_RECEIPT} scenes.{scene}: the WAV's stream '{STREAM_OF_THE_SCENES}' is "
              f"not the one its run made (ready.stream_sha512 None)")
      for scene in X.SCENE_ORDER}),
    ("audio audible", '"title": {\n   "unmixed": {',
     {"title": f"{X.AUDIO_RECEIPT}: scenes.title.wav is not a {{path, sha256}} reference"}),
    ("the title", "MENU_TITLE = 77", {"title": "title never held 180 ticks (longest 0)"}),
    ("the title", '"not_title": {',
     {"title": f"{X.AUDIO_RECEIPT}: no evidence for the scene title"}),
    ("menu", "MENU_MAIN = 77", {"menu": "menu never held 180 ticks (longest 0)"}),
    ("menu", '"not_menu": {', {"menu": f"{X.AUDIO_RECEIPT}: no evidence for the scene menu"}),
    ("garden", "CHAO_GARDENS = (0,)", {"garden": "garden never held 180 ticks (longest 0)"}),
    ("garden", '"not_garden": {',
     {"garden": f"{X.AUDIO_RECEIPT}: no evidence for the scene garden"}),
    ("Metal Harbor", '"level": 11',
     {"metalharbor": "metalharbor never held 180 ticks (longest 0)"}),
    ("Metal Harbor", '"not_metalharbor": {',
     {"metalharbor": f"{X.AUDIO_RECEIPT}: no evidence for the scene metalharbor"}),
]


def test_each_audio_falsifier_fails_its_own_scenes_for_its_own_reason(tmp_path: Path) -> None:
    """Each falsifier gate-criteria.json declares for the audio criterion, applied as the gate
    applies it to a root where the criterion is met, fails exactly the scenes AUDIBLE_FAILS
    names, each with the reason it gives, and leaves every other scene met."""
    root = met_root(tmp_path, "audible")
    declared = [(clause["promise"], falsifier) for clause in criterion("audible")["clauses"]
                for falsifier in clause["falsifiers"]]
    assert [(promise, falsifier.get("with", "absent")) for promise, falsifier in declared] == \
        [(promise, replacement) for promise, replacement, _ in AUDIBLE_FAILS]
    for (promise, falsifier), (_, _, fails) in zip(declared, AUDIBLE_FAILS):
        path = root / falsifier["file"]
        saved = path.read_bytes()
        assert saved.decode().count(falsifier["replace"]) == 1, (promise, falsifier)
        path.write_text(saved.decode().replace(falsifier["replace"], falsifier["with"]))
        status, last = run_check("audible", root, install=False)
        path.write_bytes(saved)
        assert last.startswith("S5 audio "), (promise, falsifier, last)
        assert (status, last.removeprefix("S5 audio ").split("; ")) == \
            (1, [fails.get(scene, scene_met(scene)) for scene in X.SCENE_ORDER]), \
            (promise, falsifier)
    assert run_check("audible", root, install=False) == \
        (0, "S5 audio " + "; ".join(scene_met(scene) for scene in X.SCENE_ORDER))
