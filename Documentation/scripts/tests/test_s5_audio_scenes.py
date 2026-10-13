"""The audio-scenes receipt writer, tools/s5/audio_scenes.py, held without running the game.

G5's audio clause (Documentation/scripts/check_s5_exit.py audible()) reads the receipt this
tool writes, so the tool is a measuring instrument's feed (AGENTS.md rule 5) and each part
here has a case that must fail it:

  * the observer (observer_source), compiled for the lifted build's target with the pinned
    zig against the runtime's guest_memory.h, and driven by a stand-in for the runtime and
    the game (DRIVER_C): one row per GameLoop return, the virtual clock at the tick's end and
    the five state words at check_s5_exit.STATE_WORDS' addresses and widths, literally; the
    tick probe it wraps still writes its tick capture and stage trace, and ticks_check finds
    the three agreeing. A row written before the body, a clock read before it and a state
    word read at the wrong address each change the table;
  * ticks_check, on literal tables: a clock that stands still, a missing tick, a state word
    the stage trace does not read, a row count the tick capture does not have;
  * the keyboard (every key the script holds, exactly), configuration (a keyboard session's
    host_input excused and nothing else), prefix, sidecar, save and tree checks, and the
    verdict over them, each on literal records;
  * the player's command and environment: --stats-only into the WAV, SDL's dummy audio
    driver, no SA2_ variable but its own and no inherited DXVK log;
  * the sessions' commands: the observer in the tick probe's place and nowhere else, the
    recorder with sound, each session's input and spawn;
  * what the runs are pinned to: every hook, header and player source they are built from;
  * running again: no refusal moves an old run, and an old run is put back unless every
    session of the call is ok;
  * the receipt (write_receipt) on crafted session records in a project of its own:
    audible() reads it as met, and a record that is not ok, recorded no sound, was not made
    on a fresh build with a player its call built, was made by another pipeline, runtime or
    observer, or names a file that has changed since is refused, naming it.

Every case runs where pytest puts its temporary files, inside the checkout or not.
"""

from __future__ import annotations

import array
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import audio_scenes as A  # noqa: E402
import check_s5_exit as X  # noqa: E402
import stage_run as SR  # noqa: E402
import wordlog as W  # noqa: E402  the pinned zig and its cache, as test_lifted_wordlog.py

OBSERVER = "private/generated/s5/audio-scenes/hook/sa2_audio_ticks.c"


# ---------------------------------------------------------------- the observer, built

DRIVER_C = r"""
/* The runtime and the game, as far as the observer can tell: a guest space, the clock,
 * and GameLoop's body, which sets the five state words and moves the clock. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include "guest_memory.h"

sa2_guest_t sa2_guest;
int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    uint8_t *p = sa2_gp(addr, len);
    if (!p) return SA2_E_RANGE;
    memcpy(out, p, len);
    return SA2_OK;
}
static uint64_t clock_us;
uint64_t sa2_virtual_us(void) { return clock_us; }
void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory);
unsigned long sa2_replay_gameloop_returns(void);

static void put(uint32_t va, uint32_t width, uint32_t v) { memcpy(sa2_guest.base + va, &v, width); }

static unsigned long T;
void *__real_sub_433ee0(void *state, uint32_t pc, void *memory) {
    (void)pc, (void)memory;
    const unsigned long t = ++T;
    put(0x0174B040u, 4, t < 3 ? 14u : 5u);            /* GameMode */
    put(0x01934B70u, 2, t < 3 ? 0u : 90u);            /* CurrentLevel */
    put(0x01934BE0u, 2, t < 4 ? 0u : 16u);            /* GameState */
    put(0x01D7BB10u, 4, (uint32_t)t - 1u);            /* CurrentMenu */
    put(0x0134046Cu, 4, t < 5 ? 0xFFFFFFFFu : 1u);    /* CurrentChaoArea: -1 before an area */
    put(0x01D7BB14u, 4, 0xABCDu);                     /* CurrentSubMenu, beside CurrentMenu */
    put(0x01934B72u, 2, 7u);                          /* the halfword after CurrentLevel */
    clock_us += 1000u + t;                            /* the frame's wait, inside the body */
    return state;
}

#define MAPPED 0x02700000u                            /* every state word and digest block */
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    const unsigned long ticks = strtoul(argv[1], 0, 10);
    void *m = mmap(NULL, MAPPED, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (m == MAP_FAILED) return 3;
    sa2_guest.base = (uint8_t *)m;
    sa2_guest.size = SA2_GUEST_END;
    sa2_guest.ready = 1;
    for (unsigned long t = 1; t <= ticks; ++t)
        __wrap_sub_433ee0(0, 0, 0);
    printf("returns=%lu\n", sa2_replay_gameloop_returns());
    return 0;
}
"""

HEADER = "tick\tvirtual_us\tgame_mode\tcurrent_level\tgame_state\tcurrent_menu\tchao_area"
TABLE = [HEADER,
         "1\t1001\t14\t0\t0\t0\t4294967295",
         "2\t2003\t14\t0\t0\t1\t4294967295",
         "3\t3006\t5\t90\t0\t2\t4294967295",
         "4\t4010\t5\t90\t16\t3\t4294967295",
         "5\t5015\t5\t90\t16\t4\t1"]

ROW_BEFORE_BODY = ("    void *r = sa2_audio_scenes_tick_probe(state, pc, memory);\n"
                   "    audio_row(sa2_replay_gameloop_returns());\n",
                   "    audio_row(sa2_replay_gameloop_returns());\n"
                   "    void *r = sa2_audio_scenes_tick_probe(state, pc, memory);\n")
MENU_BESIDE = ("tp_read(0x01D7BB10u, 4)", "tp_read(0x01D7BB14u, 4)")
LEVEL_WIDE = ("tp_read(0x01934B70u, 2)", "tp_read(0x01934B70u, 4)")


class Observer:
    """observer_source compiled with DRIVER_C, mutated or not, and run."""

    def __init__(self, work: Path):
        self.work = work
        (work / "driver.c").write_text(DRIVER_C)
        self.n = 0

    def build(self, mutate: tuple[str, str] | None = None) -> Path:
        self.n += 1
        text = A.observer_source(ROOT)
        if mutate:
            assert text.count(mutate[0]) == 1, mutate[0]
            text = text.replace(*mutate)
        src = self.work / f"observer{self.n}.c"
        src.write_text(text)
        exe = self.work / f"driver{self.n}"
        env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(self.work / "zig-cache"),
                   ZIG_GLOBAL_CACHE_DIR=str(W.BUILD / "zig-global"))
        p = subprocess.run([str(W.ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-Wall",
                            "-Wextra", "-Werror", str(self.work / "driver.c"), str(src), "-o",
                            str(exe), "-I", str(ROOT / "runtime")], cwd=self.work, env=env,
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return exe

    def run(self, exe: Path, ticks: int = 5) -> dict:
        self.n += 1
        d = self.work / f"run{self.n}"
        d.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
        env.update({A.OBSERVER_VAR: str(d / "audio-ticks.tsv"), "SA2_STAGE_TRACE": str(d / "stage.csv"),
                    "SA2_REPLAY_TICKS": str(d / "lifted.csv")})
        p = subprocess.run([str(exe), str(ticks)], capture_output=True, text=True, timeout=120, env=env)
        assert p.returncode == 0, (p.stdout, p.stderr)
        return {"dir": d, "stdout": p.stdout, "table": d / "audio-ticks.tsv",
                "stage": d / "stage.csv", "capture": d / "lifted.csv"}


@pytest.fixture(scope="module")
def obs(tmp_path_factory):
    if not W.ZIG.is_file():
        pytest.fail(f"the pinned zig is missing: {W.ZIG}")
    work = tmp_path_factory.mktemp("audio-observer")
    yield Observer(work)
    shutil.rmtree(work, ignore_errors=True)


def test_the_observer_writes_the_clock_and_the_state_words_at_each_gameloop_return(obs) -> None:
    got = obs.run(obs.build())
    assert got["stdout"] == "returns=5\n"
    assert got["table"].read_text().splitlines() == TABLE


def test_the_tick_probe_inside_the_observer_still_writes_and_agrees(obs) -> None:
    got = obs.run(obs.build())
    assert got["stage"].read_text().splitlines()[1:4] == [
        "1,0x0000,0x0000000e,0x0000,0x00000000,0x00000000,0x00000000,0x00000000,0x00000000",
        "2,0x0000,0x0000000e,0x0000,0x00000000,0x00000000,0x00000000,0x00000000,0x00000000",
        "3,0x005a,0x00000005,0x0000,0x00000000,0x00000000,0x00000000,0x00000000,0x00000000"]
    assert len(got["capture"].read_text().splitlines()) == 6
    check = A.ticks_check(got["table"], got["stage"], got["capture"])
    assert (check["ok"], check["problems"], check["rows"], check["first_virtual_us"],
            check["last_virtual_us"]) == (True, [], 5, 1001, 5015)


def test_a_row_written_before_the_body_is_a_tick_early(obs) -> None:
    got = obs.run(obs.build(ROW_BEFORE_BODY))
    lines = got["table"].read_text().splitlines()
    assert lines[1:3] == ["0\t0\t0\t0\t0\t0\t0", "1\t1001\t14\t0\t0\t0\t4294967295"]
    assert A.ticks_check(got["table"], got["stage"], got["capture"])["problems"][0] == (
        "tick does not run 1, 2, 3, ... with one row each")


def test_a_state_word_read_beside_its_address_changes_its_column(obs) -> None:
    got = obs.run(obs.build(MENU_BESIDE))
    assert [line.split("\t")[5] for line in got["table"].read_text().splitlines()[1:]] == ["43981"] * 5


def test_a_state_word_read_wider_than_its_width_is_caught_by_the_stage_trace(obs) -> None:
    got = obs.run(obs.build(LEVEL_WIDE))
    check = A.ticks_check(got["table"], got["stage"], got["capture"])
    assert check["ok"] is False
    assert check["problems"] == ["a state word wider than its width at tick 1 (unreadable)",
                                 "tick 1: level 458752, mode 14, state 0; the stage trace's tick 1 "
                                 "reads 0, 14, 0"]


def test_the_observer_reads_the_contract_s_words_in_its_order() -> None:
    text = A.observer_source(ROOT)
    assert ("tp_read(0x0174B040u, 4), tp_read(0x01934B70u, 2), tp_read(0x01934BE0u, 2), "
            "tp_read(0x01D7BB10u, 4), tp_read(0x0134046Cu, 4));") in text
    assert '"tick\\tvirtual_us\\tgame_mode\\tcurrent_level\\tgame_state\\tcurrent_menu\\tchao_area\\n"' in text
    assert f'#include "{ROOT / "harness" / "replay" / "wrap_tick_probe.c"}"' in text
    assert text.count("#define __wrap_sub_433ee0 sa2_audio_scenes_tick_probe") == 1


# ---------------------------------------------------------------- ticks_check on literal tables

def write_lines(path: Path, lines: list[str]) -> Path:
    path.write_text("".join(line + "\n" for line in lines))
    return path


def tables(tmp_path: Path, rows: list[tuple[int, ...]], trace: list[tuple[int, int, int, int]] | None = None,
           captured: int | None = None) -> tuple[Path, Path, Path]:
    """An observer table of `rows` (tick, virtual_us, mode, level, state, menu, area), a stage
    trace of `trace` (tick, level, mode, state; default the rows' own) and a tick capture of
    `captured` rows (default as many)."""
    table = write_lines(tmp_path / "t.tsv", [HEADER] + ["\t".join(map(str, r)) for r in rows])
    trace = trace if trace is not None else [(r[0], r[3], r[2], r[4]) for r in rows]
    stage = write_lines(tmp_path / "s.csv", ["tick,current_level,game_mode,game_state,ingame_frames,player,x,y,z"]
                        + [f"{t},0x{lv:04x},0x{m:08x},0x{s:04x},0x0,0x0,0x0,0x0,0x0" for t, lv, m, s in trace])
    n = len(rows) if captured is None else captured
    capture = write_lines(tmp_path / "c.csv", ["tick,frame_count"] + [f"{t},0x0" for t in range(1, n + 1)])
    return table, stage, capture


GOOD = [(1, 16666, 14, 0, 0, 0, 0), (2, 33332, 14, 0, 0, 1, 0), (3, 49998, 5, 90, 16, 1, 1)]


def test_ticks_check_holds_a_good_table(tmp_path: Path) -> None:
    check = A.ticks_check(*tables(tmp_path, GOOD))
    assert (check["ok"], check["rows"], check["problems"]) == (True, 3, [])


@pytest.mark.parametrize("rows,trace,captured,problems", [
    ([GOOD[0], (2, 16666, 14, 0, 0, 1, 0), GOOD[2]], None, None,
     ["virtual_us does not advance at tick 2 (1 ticks)"]),
    ([GOOD[0], GOOD[2]], [(1, 0, 14, 0), (3, 90, 5, 16)], 2,
     ["tick does not run 1, 2, 3, ... with one row each"]),
    (GOOD, [(1, 0, 14, 0), (2, 0, 14, 0), (3, 90, 5, 0)], None,
     ["tick 3: level 90, mode 5, state 16; the stage trace's tick 3 reads 90, 5, 0"]),
    (GOOD, None, 4, ["3 rows; the tick capture has 4"]),
    (GOOD, [(1, 0, 14, 0), (2, 0, 14, 0)], None,
     ["3 rows; the stage trace has 2"]),
    ([GOOD[0], GOOD[1], (3, 49998, 5, 65536, 16, 1, 1)], [(1, 0, 14, 0), (2, 0, 14, 0), (3, 65536, 5, 16)],
     None, ["a state word wider than its width at tick 3 (unreadable)"]),
])
def test_ticks_check_refuses(tmp_path: Path, rows, trace, captured, problems) -> None:
    check = A.ticks_check(*tables(tmp_path, rows, trace, captured))
    assert (check["ok"], check["problems"]) == (False, problems)


def test_ticks_check_refuses_another_header(tmp_path: Path) -> None:
    table, stage, capture = tables(tmp_path, GOOD)
    table.write_text(table.read_text().replace("chao_area", "chao_areas", 1))
    check = A.ticks_check(table, stage, capture)
    assert check["ok"] is False
    assert check["problems"][0].endswith(
        "header is not tick virtual_us game_mode current_level game_state current_menu chao_area")


# ---------------------------------------------------------------- the other checks

HOST = {"source": "script", "answers_from_host": 38, "polls": 3601, "polls_held": 38,
        "keys_written": 38, "script_lines": 7, "records": 0, "last_seq": 0, "seq_gaps": 0,
        "polls_without_writer": 0, "partial_dropped": 0, "log_lines": 7}


def test_the_keyboard_scripts_are_the_sessions_found() -> None:
    assert (A.key_lines(A.KEYS["menu"]), A.key_lines(A.KEYS["garden"])) == (7, 2)
    assert A.KEYS["garden"].splitlines()[1:] == [
        "800 805 0x24     # J: continue without saving, into the Chao Lobby",
        "1300 1700 0x11   # W: through the stone arch ahead, into the Neutral Garden"]
    # Six polls each for five of menu's keys and four for two; six and 401 for garden's.
    assert (A.key_presses(A.KEYS["menu"]), A.key_presses(A.KEYS["garden"])) == (38, 407)


def test_key_presses_counts_a_key_held_twice_at_a_poll_once() -> None:
    assert A.key_presses("1 3 0x1C\n2 4 0x1C  # overlaps\n2 2 0x11\n# only a comment\n\n") == 5


@pytest.mark.parametrize("change,wrong", [
    ({}, {}),
    ({"source": "fifo"}, {"source": "fifo"}),
    ({"script_lines": 6}, {"script_lines": 6}),
    ({"records": 3}, {"records": 3}),
    ({"seq_gaps": 1}, {"seq_gaps": 1}),
    ({"keys_written": 0}, {"keys_written": 0}),
    ({"keys_written": 37}, {"keys_written": 37}),        # a script cut short
    ({"keys_written": 39}, {"keys_written": 39}),
])
def test_keyboard_check(change: dict, wrong: dict) -> None:
    check = A.keyboard_check({"dinput": {"host_input": {**HOST, **change}}}, A.KEYS["menu"])
    assert (check["ok"], check["wrong"]) == (not wrong, wrong)


def test_keyboard_check_refuses_a_run_with_no_host_keyboard() -> None:
    assert A.keyboard_check({"dinput": {}}, A.KEYS["menu"]) == {
        "ok": False, "why": "the run's record keeps no dinput.host_input"}


def run_record(host_input: bool = True, **enabled: bool) -> dict:
    """A run's record on the default device profile with every ENV switch shown on, but
    `enabled`'s (device: on or off), and the keyboard's host_input block when asked."""
    on = {d: enabled.get(d, True) for d in ("d3d9", "dsound", "dinput", "xaudio2", "ddraw")}
    rec = {"device_profile": {"default": True}, "virtual": dict(A.DP.DEFAULT_GUEST),
           "d3d9": {"device_enabled": on["d3d9"], "constant_tables": {"switch": True}},
           **{d: {"device_enabled": on[d]} for d in ("dsound", "dinput", "xaudio2", "ddraw")}}
    if host_input:
        rec["dinput"]["host_input"] = dict(HOST)
    return rec


HOST_INPUT_LINE = ("the run's record reads dinput.host_input (source 'script'): a host keyboard "
                   "answered its DirectInput device, which only tools/s5/play_live.py sets")


@pytest.mark.parametrize("record,keyboard,problems", [
    (run_record(), True, []),                            # the keyboard session's own keys
    (run_record(), False, [HOST_INPUT_LINE]),            # keys in a session that has none
    (run_record(host_input=False), False, []),
    (run_record(dsound=False), True,                     # excused host_input excuses nothing else
     ["SA2_DSOUND_DEVICE=1 was set and the run's record reads dsound.device_enabled = False"]),
    ({**run_record(), "device_profile": {"source": "x.txt", "sha256": "e" * 64}}, True,
     ["the run read device profile x.txt (eeeeeeeeeeee) and was handed default"]),
])
def test_configuration_check(record: dict, keyboard: bool, problems: list[str]) -> None:
    check = A.configuration_check(record, A.DP.DEFAULT, dict(A.RR.ENV), keyboard)
    assert (check["ok"], check["problems"]) == (not problems, problems)


def test_configuration_check_refuses_a_run_with_no_record() -> None:
    assert A.configuration_check({}, A.DP.DEFAULT, dict(A.RR.ENV), True)["ok"] is False


def test_prefix_check(tmp_path: Path) -> None:
    theirs = write_lines(tmp_path / "r.csv", ["h", "1", "2", "3"])
    pin = A.sha256(theirs)
    mine = write_lines(tmp_path / "m.csv", ["h", "1", "2"])
    got = A.prefix_check(mine, theirs, pin)
    assert (got["ok"], got["lines"], got["registered_lines"], got["first_differing_line"]) == (True, 3, 4, None)
    write_lines(mine, ["h", "1", "x"])
    assert A.prefix_check(mine, theirs, pin)["first_differing_line"] == 2
    assert A.prefix_check(mine, theirs, pin)["ok"] is False
    write_lines(mine, ["h", "1", "2", "3", "4"])
    assert A.prefix_check(mine, theirs, pin)["ok"] is False          # longer than the registered run
    write_lines(mine, ["h"])
    assert A.prefix_check(mine, theirs, pin)["ok"] is False          # no row
    write_lines(mine, ["h", "1"])
    assert A.prefix_check(mine, theirs, "0" * 64)["why"].endswith(f"does not hash to its pin {'0' * 64}")


def test_saves_check() -> None:
    p = A.paths(ROOT / "private" / "generated" / "s5" / "audio-scenes", "menu")
    good = {"installation_matches_pin": True, "overlay": A.rel(p["overlay"]), "left": {},
            "pin": {"digest": "d"}}
    assert A.saves_check(good, p) == {"pin": "d", "overlay": good["overlay"], "problems": [], "ok": True}
    assert A.saves_check({**good, "left": {"a": 1}}, p)["problems"] == [
        "the overlay held ['a'] after the reset"]
    assert A.saves_check({**good, "overlay": "elsewhere"}, p)["problems"] == [
        "the reset cleared 'elsewhere', not the run's own overlay"]


def write_wav(path: Path, seconds: float, amplitude: float, rate: int = 48000) -> int:
    """A float32 stereo WAV of a 1 kHz sine at `amplitude`: RMS amplitude/sqrt(2)."""
    frames = int(seconds * rate)
    samples = array.array("f")
    for n in range(frames):
        v = amplitude * math.sin(2 * math.pi * 1000 * n / rate)
        samples.extend((v, v))
    if sys.byteorder != "little":
        samples.byteswap()
    data = samples.tobytes()
    fmt = (3).to_bytes(2, "little") + (2).to_bytes(2, "little") + rate.to_bytes(4, "little") + \
        (rate * 8).to_bytes(4, "little") + (8).to_bytes(2, "little") + (32).to_bytes(2, "little")
    body = b"WAVE" + b"fmt " + len(fmt).to_bytes(4, "little") + fmt + b"data" + \
        len(data).to_bytes(4, "little") + data
    path.write_bytes(b"RIFF" + len(body).to_bytes(4, "little") + body)
    return frames


SIDECAR = {"tool": "host/player sa2_player --audio-wav", "ok": True, "format": "float32",
           "sample_rate": 48000, "channels": 2, "origin_us": 0, "first_frame_us": 16666,
           "presents": 3, "audio_problems": 0, "stream_sha256": "a" * 64}


def test_wav_check(tmp_path: Path) -> None:
    frames = write_wav(tmp_path / "s.wav", 0.5, 0.1)
    side = tmp_path / "s.wav.json"
    side.write_text(json.dumps({**SIDECAR, "frames": frames}))
    got = A.wav_check(tmp_path / "s.wav", side, "a" * 64)
    assert (got["ok"], got["frames"], got["seconds"], got["wrong"]) == (True, 24000, 0.5, {})
    assert A.wav_check(tmp_path / "s.wav", side, "b" * 64)["wrong"] == {"stream_sha256": "a" * 64}
    side.write_text(json.dumps({**SIDECAR, "frames": frames + 1, "audio_problems": 2}))
    assert A.wav_check(tmp_path / "s.wav", side, "a" * 64)["wrong"] == {"audio_problems": 2,
                                                                         "frames": 24001}


def test_player_check(tmp_path: Path) -> None:
    rep = {"exit_status": 0, "complete": True, "stream_sha256": "a" * 64,
           "stream_records_read_to_end": True, "presents": 3, "audio": {"stream_records": 9}}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(rep))
    assert A.player_check(path, 0, "a" * 64)["ok"] is True
    assert A.player_check(path, 0, "b" * 64)["problems"] == ["the player did not read this stream to its end"]
    path.write_text(json.dumps({**rep, "audio": {"stream_records": 0}}))
    assert A.player_check(path, 0, "a" * 64)["problems"] == ["the player read 0 AUDIO records"]
    path.write_text(json.dumps({**rep, "exit_status": 3, "failures": ["x"]}))
    assert A.player_check(path, 3, "a" * 64)["problems"] == ["the player exited 3 (report 3): ['x']"]


@pytest.mark.parametrize("checks,ok", [
    ({}, False),                                          # no check is no evidence
    ({"a": {"ok": True}}, True),
    ({"a": {"ok": True}, "b": {"ok": False}}, False),
    ({"a": {"ok": True}, "b": {"why": "no ok at all"}}, False),
    ({"a": {"ok": 1}}, False),                            # true, not truthy
])
def test_verdict(checks: dict, ok: bool) -> None:
    assert A.verdict(checks) is ok


def test_tree_check(tmp_path: Path) -> None:
    root = project(tmp_path)
    made = A.pins(root, PIPELINE)
    assert A.tree_check(made, root, PIPELINE) == {"moved": [], "ok": True}
    (root / "runtime" / "sa2_dsound.c").write_text("/* edited while the game ran */\n")
    assert A.tree_check(made, root, PIPELINE) == {"moved": ["runtime/sa2_dsound.c"], "ok": False}


def test_the_player_runs_on_no_device_and_inherits_no_sa2_variable(tmp_path: Path, monkeypatch) -> None:
    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(A.subprocess, "run", fake_run)
    monkeypatch.setenv("SA2_INPUT_SCRIPT", "x.csv")
    monkeypatch.setenv("DXVK_LOG_PATH", "/somewhere/else")
    monkeypatch.setenv("SDL_AUDIO_DRIVER", "alsa")
    p = A.paths(tmp_path / "out", "menu")
    assert A.player_run(p, tmp_path / "bin")[0] == 0
    (cmd, kw), = calls
    d = tmp_path / "out" / "menu"
    r = A.rel(d)                  # repository-relative when pytest's directory is inside the checkout
    assert cmd == ["bash", str(ROOT / "host" / "player" / "run_player.sh"), f"{r}/stream.sa2s",
                   "--stats-only", "--stage", "3", "--audio-wav", f"{r}/player/sound.wav",
                   "--report", f"{r}/player/report.json"]
    env = kw["env"]
    assert (kw["cwd"], env["SDL_AUDIO_DRIVER"], env["OUT"], env["SA2_PLAY_OUT"], "DXVK_LOG_PATH" in env) == (
        ROOT, "dummy", str(tmp_path / "bin"), f"{d}/player", False)
    assert [k for k in env if k.startswith("SA2_")] == ["SA2_PLAY_OUT"]
    assert (d / "player" / "player.log").read_text() == "$ " + " ".join(cmd) + "\n"


def test_rel_and_ref_outside_the_checkout(tmp_path: Path) -> None:
    inside = ROOT / "private" / "generated" / "s5" / "audio-scenes" / "menu" / "audio-ticks.tsv"
    assert A.rel(inside) == "private/generated/s5/audio-scenes/menu/audio-ticks.tsv"
    assert A.rel(Path("relative/as/given.tsv")) == "relative/as/given.tsv"
    outside = write_lines(tmp_path / "t.tsv", [HEADER])
    assert A.rel(outside, tmp_path / "elsewhere") == str(outside)
    with pytest.raises(ValueError, match="is not under"):
        A.ref(outside, tmp_path / "elsewhere")
    assert A.ref(outside, tmp_path) == {"path": "t.tsv", "sha256": A.sha256(outside)}


# ---------------------------------------------------------------- the sessions' commands

def test_the_sessions_cover_the_contract_s_scenes() -> None:
    assert A.SCENE_SESSION == {"title": "menu", "menu": "menu", "garden": "garden",
                               "metalharbor": "metalharbor"}
    assert sorted(A.SCENE_SESSION) == sorted(X.SCENE_ORDER)
    assert {n: s.polls for n, s in A.SESSIONS.items()} == {"menu": 3600, "garden": 4200,
                                                           "metalharbor": 2400}


def test_the_observer_takes_the_tick_probe_s_place_and_no_other() -> None:
    assert A.hooks(A.SESSIONS["menu"], OBSERVER) == ([OBSERVER], ["sub_433ee0"])
    for name in ("garden", "metalharbor"):
        hs, ws = A.hooks(A.SESSIONS[name], OBSERVER)
        assert hs == [OBSERVER, "harness/replay/wrap_test_spawn.c", "harness/replay/wrap_input_replay.c",
                      "harness/stage_goal_probe.c"]
        assert ws == SR.WRAPS


def spec(cmd: list[str]) -> dict[str, str]:
    items = cmd[cmd.index("--run") + 1].split("|")
    return dict(item.split("=", 1) for item in items[1:])


def session_command(name: str) -> list[str]:
    session = A.SESSIONS[name]
    p = A.paths(ROOT / "private" / "generated" / "s5" / "audio-scenes", name)
    meta = {"runner": A.TOOL, "run": "r", "audio": True}
    env = A.run_env(session, p, meta, {"SA2_DEVICE_PROFILE": "private/profile.txt"},
                    ROOT / "private" / "recording.csv" if session.recording else None)
    return A.command(session, p, A.build_name(session), env, OBSERVER)


@pytest.mark.parametrize("name", list(A.SESSIONS))
def test_each_session_records_its_sound_and_its_table(name: str) -> None:
    cmd = session_command(name)
    assert "harness/replay/wrap_tick_probe.c" not in cmd
    hooks = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--hook"]
    assert hooks[0] == OBSERVER and hooks[-1] == "host/sa2_present_rec.c"
    env = spec(cmd)
    out = f"private/generated/s5/audio-scenes/{name}"
    assert {k: env[k] for k in ("SA2_REC_AUDIO", "SA2_AUDIO_TICKS", "SA2_REC_TRACE", "SA2_STAGE_TRACE",
                                "SA2_REPLAY_TICKS", "SA2_VFS_WRITE_ROOT", "SA2_DEVICE_PROFILE")} == {
        "SA2_REC_AUDIO": "1", "SA2_AUDIO_TICKS": f"{out}/audio-ticks.tsv",
        "SA2_REC_TRACE": f"{out}/stream.sa2s", "SA2_STAGE_TRACE": f"{out}/stage-trace.csv",
        "SA2_REPLAY_TICKS": f"{out}/lifted.csv", "SA2_VFS_WRITE_ROOT": f"{out}/vfsroot",
        "SA2_DEVICE_PROFILE": "private/profile.txt"}
    assert env["SA2_PUMP_POLLS"] == str(A.SESSIONS[name].polls)
    assert json.loads(env["SA2_REC_META"]) == {"runner": "tools/s5/audio_scenes.py", "run": "r", "audio": True}


def test_the_sessions_inputs_and_spawns() -> None:
    menu, garden, mh = (spec(session_command(n)) for n in ("menu", "garden", "metalharbor"))
    out = "private/generated/s5/audio-scenes"
    assert (menu["SA2_INPUT_DI_SCRIPT"], "SA2_TEST_SPAWN_LEVEL" in menu, "SA2_INPUT_SCRIPT" in menu) == (
        f"{out}/menu/keys.txt", False, False)
    assert (garden["SA2_INPUT_DI_SCRIPT"], garden["SA2_TEST_SPAWN_LEVEL"], garden["SA2_TEST_SPAWN_CHARACTER"],
            garden["SA2_GUEST_CMDLINE"], "SA2_INPUT_SCRIPT" in garden) == (
        f"{out}/garden/keys.txt", "90", "0", "--level chaoworld --character sonic", False)
    # The recording's path is compared resolved on both sides: in a ship_subset.py worktree
    # private/ is a symbolic link to the main tree's, and the tool names the file it reaches.
    assert (os.path.realpath(mh["SA2_INPUT_SCRIPT"]), mh["SA2_INPUT_APPLIED"], mh["SA2_GOAL_LATCH"],
            mh["SA2_TEST_SPAWN_LEVEL"], mh["SA2_GUEST_CMDLINE"], "SA2_INPUT_DI_SCRIPT" in mh) == (
        os.path.realpath(ROOT / "private" / "recording.csv"), f"{out}/metalharbor/input-applied.csv",
        f"{out}/metalharbor/goal-latch.csv", "10", "--level metalharbor --character sonic", False)


def test_the_builds_are_named_apart() -> None:
    names = {A.build_name(s) for s in A.SESSIONS.values()}
    assert {n.split(".")[0] for n in names} == {"present-as-boot", "present-as-spawn"}
    assert all(A.PR.build_name_problem(n) is None for n in names)


# ---------------------------------------------------------------- what the runs are pinned to

INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


def test_every_file_the_runs_are_built_from_is_pinned() -> None:
    pinned = set(A.pins())
    named = {"tools/s5/audio_scenes.py", "host/sa2_present_rec.c", "host/sa2_stream.h",
             "harness/replay/wrap_tick_probe.c", "tools/s4/stage_run.py",
             "harness/replay/wrap_test_spawn.c", "harness/replay/wrap_input_replay.c",
             "harness/stage_goal_probe.c", "harness/replay/tick_probe_core.h",
             "harness/replay/tick_digest.h", "harness/replay/word_log_core.h",
             "tools/s5/present_run.py", "tools/s5/stream_dump.py", "tools/s5/objcheck.py",
             "tools/s4/loop_run.py", "Documentation/replays.json", "host/player/build.sh",
             "host/player/run_player.sh", "host/player/player.cpp", "host/player/mixer.cpp",
             "host/player/mixer.h", "runtime/sa2_dsound.c", "runtime/sa2_imports.c"}
    assert sorted(named - pinned) == []
    # The DXVK player (host/player/build.sh) reads nothing of the second host's: its pins
    # name no host/d3d9gpu file (finding A20 reverted once the second host became its own
    # binary, host/d3d9gpu/build.sh's sa2_player_gpu).
    assert [p for p in pinned if p.startswith("host/d3d9gpu/")] == []
    # Every quoted include of a pinned C source, where its compile finds it: beside it, in
    # runtime/ (link_s4's -I) or host/ (the player's -I for sa2_stream.h). Two are not the
    # repository's: the player's expectations header, generated by the pinned
    # host/player/generate_expectations.py, and remill's own headers (link_s4's -I for them).
    unpinned = []
    for name in sorted(n for n in pinned if n.endswith((".c", ".cpp", ".h"))):
        for inc in INCLUDE.findall((ROOT / name).read_text()):
            if inc == "sa2_host_expectations.h" or inc.startswith("remill/"):
                continue
            found = [os.path.normpath(f"{d}/{inc}") for d in (str(Path(name).parent), "runtime", "host")
                     if (ROOT / d / inc).is_file()]
            if not found or found[0] not in pinned:
                unpinned.append(f"{name}: {inc}")
    assert unpinned == []


def test_pins_name_each_runtime_source_as_well(tmp_path: Path) -> None:
    root = project(tmp_path)
    assert list(A.pins(root, PIPELINE)) == [*PIPELINE, "runtime/sa2_dsound.c", "runtime/sa2_imports.c"]


@pytest.mark.parametrize("argv,match", [
    ([".venv/bin/python", "tools/s5/audio_scenes.py", "all", "--replace"], True),
    (["/usr/bin/python3", "/elsewhere/tree/tools/s5/audio_scenes.py", "run", "menu"], True),
    ([".venv/bin/python", "tools/s5/audio_scenes.py", "receipt"], False),
    (["bash", "-c", ".venv/bin/python tools/s5/audio_scenes.py all"], False),
    ([".venv/bin/python", "-m", "pytest", "Documentation/scripts/tests/test_s5_audio_scenes.py"], False),
    ([], False),
])
def test_another_audio_scenes_run_is_busy(argv: list[str], match: bool) -> None:
    assert A.is_audio_scenes_run(argv) is match


def test_a_busy_audio_scenes_run_is_named_once(monkeypatch) -> None:
    """present_live.is_presentation_run sees an audio-scenes run as well (a live session's
    guard), so refuse_busy names it once, as an audio-scenes run, beside a presentation run
    that is not one. The machine's processes are stood in by a table, read by the real
    predicates."""
    import present_live as PL  # noqa: PLC0415  the module refuse_busy reads
    table = {4242: [".venv/bin/python", "tools/s5/audio_scenes.py", "all", "--replace"],
             4343: [".venv/bin/python", "tools/s5/present_run.py", "--stage", "boot"]}
    monkeypatch.setattr(PL, "gate_passes", lambda: [])
    monkeypatch.setattr(PL, "processes", lambda match: [
        f"{pid}: {' '.join(argv)}" for pid, argv in table.items() if match(argv)])
    with pytest.raises(SystemExit) as refused:
        A.refuse_busy()
    assert str(refused.value) == (
        "refusing: presentation run 4343: .venv/bin/python tools/s5/present_run.py --stage boot; "
        "audio-scenes run 4242: .venv/bin/python tools/s5/audio_scenes.py all --replace")


# ---------------------------------------------------------------- running again

def old_runs(tmp_path: Path, names=tuple(A.SESSIONS)) -> Path:
    out = tmp_path / "out"
    for name in names:
        (out / name).mkdir(parents=True)
        (out / name / A.RECORD).write_text(f"old {name}\n")
    return out


def on_disk(out: Path) -> dict[str, str | None]:
    """Each directory under `out`, hidden ones too, and its record's text."""
    return {d.name: (d / A.RECORD).read_text() if (d / A.RECORD).is_file() else None
            for d in sorted(out.iterdir())}


OLD = {name: f"old {name}\n" for name in A.SESSIONS}


def stand_ins(monkeypatch, ok: dict[str, bool], interrupt: str | None = None) -> None:
    """plan and run_session as the game would leave them, without it: a session writes its
    record into its own directory, which must not exist yet, and is ok as `ok` says."""
    monkeypatch.setattr(A, "refuse_busy", lambda: None)
    monkeypatch.setattr(A, "plan", lambda session, out, bin_dir, link: A.Plan(
        session, A.paths(out, session.name), "b", A.DP.DEFAULT, {}, None, None))

    def run_session(pl, out, bin_dir, made, link=True, player_built=True) -> dict:
        if pl.session.name == interrupt:
            raise KeyboardInterrupt
        pl.p["dir"].mkdir(parents=True)
        (pl.p["dir"] / A.RECORD).write_text(f"new {pl.session.name} {ok[pl.session.name]}\n")
        return {"ok": ok[pl.session.name], "checks": {"ticks": {"ok": True}, "wav": {"ok": True}},
                "run": {"stream_bytes": 1}}

    monkeypatch.setattr(A, "run_session", run_session)


def call(out: Path, tmp_path: Path, replace: bool = True) -> dict:
    return A.run_sessions(list(A.SESSIONS), out, tmp_path / "bin", link=True, replace=replace,
                          build_player=False)


def test_a_refused_call_moves_no_old_run(tmp_path: Path, monkeypatch) -> None:
    """The review's case: --replace removed the runs the receipt cites, then refused."""
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    out = old_runs(tmp_path)

    def busy() -> None:
        raise SystemExit("refusing: gate pass 1: python Documentation/scripts/run_all_gates.py")

    monkeypatch.setattr(A, "refuse_busy", busy)
    with pytest.raises(SystemExit, match="refusing: gate pass 1"):
        call(out, tmp_path)
    assert on_disk(out) == OLD


def test_a_session_refused_before_it_runs_moves_no_old_run(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    out = old_runs(tmp_path)
    stand_ins(monkeypatch, {})

    def no_player(session, out, bin_dir, link):
        raise SystemExit("no player at bin/sa2_player")

    monkeypatch.setattr(A, "plan", no_player)
    with pytest.raises(SystemExit, match="no player at"):
        call(out, tmp_path)
    assert on_disk(out) == OLD


def test_every_session_ok_replaces_the_old_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    out = old_runs(tmp_path)
    stand_ins(monkeypatch, {"menu": True, "garden": True, "metalharbor": True})
    assert {n: r["ok"] for n, r in call(out, tmp_path).items()} == {
        "menu": True, "garden": True, "metalharbor": True}
    assert on_disk(out) == {"garden": "new garden True\n", "menu": "new menu True\n",
                            "metalharbor": "new metalharbor True\n"}


def test_one_session_not_ok_puts_every_old_run_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    out = old_runs(tmp_path)
    stand_ins(monkeypatch, {"menu": True, "garden": False, "metalharbor": True})
    call(out, tmp_path)
    assert on_disk(out) == {"garden": "old garden\n", "garden.unkept": "new garden False\n",
                            "menu": "old menu\n", "menu.unkept": "new menu True\n",
                            "metalharbor": "old metalharbor\n",
                            "metalharbor.unkept": "new metalharbor True\n"}
    stand_ins(monkeypatch, {"menu": True, "garden": True, "metalharbor": True})
    call(out, tmp_path)                                   # again: the unkept runs make no difference
    assert on_disk(out) == {"garden": "new garden True\n", "garden.unkept": "new garden False\n",
                            "menu": "new menu True\n", "menu.unkept": "new menu True\n",
                            "metalharbor": "new metalharbor True\n",
                            "metalharbor.unkept": "new metalharbor True\n"}


def test_a_stopped_call_puts_every_old_run_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    out = old_runs(tmp_path)
    stand_ins(monkeypatch, {"menu": True, "garden": True, "metalharbor": True}, interrupt="garden")
    with pytest.raises(KeyboardInterrupt):
        call(out, tmp_path)
    assert on_disk(out) == {"garden": "old garden\n", "menu": "old menu\n",
                            "menu.unkept": "new menu True\n", "metalharbor": "old metalharbor\n"}


def test_a_first_run_that_is_not_ok_stays_where_it_ran(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    out = tmp_path / "out"
    stand_ins(monkeypatch, {"menu": False, "garden": True, "metalharbor": True})
    call(out, tmp_path, replace=False)
    assert on_disk(out) == {"garden": "new garden True\n", "menu": "new menu False\n",
                            "metalharbor": "new metalharbor True\n"}


def test_what_a_call_may_not_run_into(tmp_path: Path, monkeypatch) -> None:
    out = old_runs(tmp_path, ("menu",))
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path)
    with pytest.raises(SystemExit, match="menu exists: a session directory is used once"):
        A.may_use(out / "menu", replace=False)
    A.may_use(out / "menu", replace=True)
    A.may_use(out / "garden", replace=False)                  # a first run
    (out / ".garden.previous").mkdir()
    with pytest.raises(SystemExit, match="holds the run an interrupted --replace set aside"):
        A.may_use(out / "garden", replace=True)
    monkeypatch.setattr(A, "REPLACEABLE", tmp_path / "elsewhere")
    with pytest.raises(SystemExit, match="--replace moves only a directory under"):
        A.may_use(out / "menu", replace=True)
    with pytest.raises(SystemExit, match="refusing to remove"):
        A.remove(out / "menu")
    assert (out / "menu" / A.RECORD).read_text() == "old menu\n"


# ---------------------------------------------------------------- the receipt

PIPELINE = ("tools/s5/audio_scenes.py", "host/sa2_present_rec.c")


def project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "Documentation").mkdir(parents=True)
    (root / "Documentation" / "replays.json").write_text(json.dumps(
        {"replays": [{"name": "metalharbor", "stage": {"name": "metalharbor", "level": 10}}]}))
    for name in (*PIPELINE, "runtime/sa2_dsound.c", "runtime/sa2_imports.c"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(f"# {name}\n")
    return root


# Each crafted session's table: 600 ticks a frame apart, and the scenes it holds.
def scene_row(session: str, t: int) -> tuple[int, ...]:
    if session == "menu":
        return (t, 16667 * t, 14, 0, 0, 0 if t <= 250 else 1, 0)
    if session == "garden":
        return (t, 16667 * t, 5, 90, 16, 0, 1)
    return (t, 16667 * t, 5, 10, 16, 0, 0xFFFFFFFF)


def craft_session(root: Path, out: Path, name: str, amplitude: float = 0.1, **record) -> Path:
    d = out / name
    (d / "player").mkdir(parents=True)
    stream = "c" * 64
    frames = write_wav(d / "player" / "sound.wav", 11.0, amplitude)
    (d / "player" / "sound.wav.json").write_text(json.dumps({**SIDECAR, "frames": frames,
                                                              "stream_sha256": stream}))
    write_lines(d / "audio-ticks.tsv", [HEADER] + ["\t".join(map(str, scene_row(name, t)))
                                                   for t in range(1, 601)])
    observer = out / "hook" / A.OBSERVER_FILE
    observer.parent.mkdir(parents=True, exist_ok=True)
    observer.write_bytes(A.observer_source(root).encode())
    made = {"schema": A.RUN_SCHEMA, "generated_by": A.TOOL, "session": name,
            "scenes": list(A.SESSIONS[name].scenes), "ok": True, "audio": True,
            "ready": {"path": A.rel(d / "stream.READY", root), "stream": A.rel(d / "stream.sa2s", root),
                      "stream_sha256": stream},
            "ticks": A.ref(d / "audio-ticks.tsv", root),
            "inputs": {"pipeline": A.pins(root, PIPELINE), "commit": "abc",
                       "runtime_sha256": A.RR.runtime_digest(root), "fresh_build": True,
                       "player_built": True, "binary_sha256": "b" * 64, "device_profile": "default"},
            "run": {"build": A.build_name(A.SESSIONS[name]), "polls": A.SESSIONS[name].polls},
            "files": {"wav": A.ref(d / "player" / "sound.wav", root),
                      "sidecar": A.ref(d / "player" / "sound.wav.json", root),
                      "observer": A.ref(observer, root)}}
    made.update(record)
    (d / A.RECORD).write_text(json.dumps(made))
    return d


def crafted(tmp_path: Path, **per_session) -> tuple[Path, Path]:
    root = project(tmp_path)
    out = root / "private" / "s"
    for name in A.SESSIONS:
        craft_session(root, out, name, **per_session.get(name, {}))
    return root, out


def test_the_receipt_is_read_as_met(tmp_path: Path) -> None:
    root, out = crafted(tmp_path)
    doc = A.write_receipt(out, root, PIPELINE)
    assert sorted(doc["scenes"]) == sorted(X.SCENE_ORDER)
    assert doc["scenes"]["title"] == doc["scenes"]["menu"]           # one session, shared files
    assert doc["inputs"]["pipeline"] == A.pins(root, PIPELINE)
    assert "runtime/sa2_dsound.c" in doc["inputs"]["pipeline"]
    assert (doc["inputs"]["runtime_sha256"], doc["inputs"]["observer_sha256"]) == (
        A.RR.runtime_digest(root), A.sha256(out / "hook" / A.OBSERVER_FILE))
    result = X.audible(root)
    assert result.met is True, result.detail
    assert result.detail == "; ".join(f"{scene} -23.0 dBFS at second 1 of the scene, at least -40"
                                      for scene in X.SCENE_ORDER)
    levels = A.scene_levels(root)
    assert levels["title"] == {"held_ticks": [[1, 250]], "seconds": 4, "loudest_dbfs": -23.01,
                               "quietest_dbfs": -23.01, "seconds_at_or_over": 4}
    assert levels["menu"]["held_ticks"] == [[251, 600]]
    written = A.write_levels(out, root)
    assert json.loads((out / "scene-levels.json").read_text()) == written
    assert (written["receipt"], written["scenes"]) == (
        {"path": X.AUDIO_RECEIPT, "sha256": A.sha256(root / X.AUDIO_RECEIPT)}, levels)


def test_a_quiet_session_is_written_and_read_as_not_met(tmp_path: Path) -> None:
    root, out = crafted(tmp_path, garden={"amplitude": 0.001})
    A.write_receipt(out, root, PIPELINE)
    result = X.audible(root)
    assert result.met is False
    assert "garden: loudest of 9 whole seconds -63.0 dBFS, under -40" in result.detail


@pytest.mark.parametrize("change,why", [
    ({"ok": False}, "private/s/garden/audio-run.json: ok False, audio True"),
    ({"audio": False}, "private/s/garden/audio-run.json: ok True, audio False"),
    ({"ready": "not written: stream"}, "private/s/garden/audio-run.json: no READY"),
    ({"generated_by": "tools/s5/present_run.py"},
     "private/s/garden/audio-run.json is not a record of tools/s5/audio_scenes.py "
     "(sa2-s5-audio-scene-run/v1)"),
])
def test_the_receipt_refuses_a_session_record(tmp_path: Path, change: dict, why: str) -> None:
    root, out = crafted(tmp_path, garden=change)
    with pytest.raises(A.ReceiptRefused) as e:
        A.write_receipt(out, root, PIPELINE)
    assert str(e.value) == why
    assert not (root / X.AUDIO_RECEIPT).exists()


def edit_record(out: Path, name: str, change) -> None:
    path = out / name / A.RECORD
    rec = json.loads(path.read_text())
    change(rec)
    path.write_text(json.dumps(rec))


GARDEN = "private/s/garden/audio-run.json"


@pytest.mark.parametrize("change,why", [
    (lambda r: r["inputs"].update(fresh_build=False),
     f"{GARDEN} was not made on a fresh build with a player its call built (fresh_build False, "
     "player_built True: --no-build or --no-player-build)"),
    (lambda r: r["inputs"].pop("player_built"),
     f"{GARDEN} was not made on a fresh build with a player its call built (fresh_build True, "
     "player_built None: --no-build or --no-player-build)"),
    (lambda r: r["inputs"].update(runtime_sha256="r" * 64),
     f"{GARDEN} was made by the runtime '{'r' * 64}', not the tree's {{runtime}}"),
    (lambda r: r["files"]["observer"].update(sha256="0" * 64),
     f"{GARDEN} linked the observer '{'0' * 64}', not the one this tree generates ({{observer}})"),
    (lambda r: r["files"].pop("observer"),
     f"{GARDEN} linked the observer None, not the one this tree generates ({{observer}})"),
])
def test_the_receipt_refuses_a_run_not_made_by_this_tree(tmp_path: Path, change, why: str) -> None:
    root, out = crafted(tmp_path)
    edit_record(out, "garden", change)
    with pytest.raises(A.ReceiptRefused) as e:
        A.write_receipt(out, root, PIPELINE)
    assert str(e.value) == why.format(runtime=A.RR.runtime_digest(root),
                                      observer=A.observer_sha256(root))
    assert not (root / X.AUDIO_RECEIPT).exists()


def test_the_receipt_refuses_runs_made_on_another_runtime(tmp_path: Path) -> None:
    root, out = crafted(tmp_path)
    (root / "runtime" / "sa2_dsound.c").write_text("/* changed after the runs */\n")
    with pytest.raises(A.ReceiptRefused) as e:
        A.write_receipt(out, root, PIPELINE)
    assert str(e.value) == ("private/s/garden/audio-run.json was made by other files than the "
                            "tree's: runtime/sa2_dsound.c")


def test_the_receipt_refuses_runs_made_by_another_pipeline(tmp_path: Path) -> None:
    root, out = crafted(tmp_path)
    (root / "host" / "sa2_present_rec.c").write_text("/* changed after the runs */\n")
    with pytest.raises(A.ReceiptRefused) as e:
        A.write_receipt(out, root, PIPELINE)
    assert str(e.value) == ("private/s/garden/audio-run.json was made by other files than the "
                            "tree's: host/sa2_present_rec.c")


def test_the_receipt_refuses_a_file_changed_since_its_record(tmp_path: Path) -> None:
    root, out = crafted(tmp_path)
    table = out / "metalharbor" / "audio-ticks.tsv"
    table.write_text(table.read_text() + "601\t10017067\t5\t10\t16\t0\t0\n")
    with pytest.raises(A.ReceiptRefused) as e:
        A.write_receipt(out, root, PIPELINE)
    assert str(e.value).startswith("private/s/metalharbor/audio-run.json: {'path': "
                                   "'private/s/metalharbor/audio-ticks.tsv'")


def test_each_scene_is_a_key_once_in_the_receipt(tmp_path: Path) -> None:
    """Falsifiers on the receipt anchor on a scene's key; the runs list names sessions in values."""
    root, out = crafted(tmp_path)
    A.write_receipt(out, root, PIPELINE)
    text = (root / X.AUDIO_RECEIPT).read_text()
    assert [text.count(f'"{scene}": {{') for scene in X.SCENE_ORDER] == [1, 1, 1, 1]
    assert [run["session"] for run in json.loads(text)["runs"]] == ["garden", "menu", "metalharbor"]


def test_the_game_and_the_player_inherit_no_sa2_variable(monkeypatch) -> None:
    monkeypatch.setenv("SA2_FRAME_US", "1")
    monkeypatch.setenv("SA2_INPUT_SCRIPT", "x.csv")
    monkeypatch.setenv("SA2_AUDIO_TICKS", "t.tsv")
    monkeypatch.setenv("PATH_FOR_THE_TEST", "kept")
    env = A.game_env()
    assert [k for k in env if k.startswith("SA2_")] == []
    assert env["PATH_FOR_THE_TEST"] == "kept"
