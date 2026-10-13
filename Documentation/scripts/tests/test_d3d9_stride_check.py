"""The object-stride check's normalisation, comparison and verdict, made to fail
one way at a time.

`tools/s4/d3d9_stride_check.py` claims that three Direct3D object layouts make
the same run, and a comparer that agrees with everything passes every
agreement test ever written. So each agreement below is paired with a mutation
that must disagree; the control arm, which must differ, is shown failing the
check when it agrees; and each refusal the rule-5 review of the tool's first
version asked for (2026-09-23) is driven through check() with a fake run in
place of the guest, which this suite never runs -- as is a wide layout that
stops short of the stage, which a later review made a failure, not a refusal.

The two real run records the first version wrote are the evidence for the keys
the whole-record comparison leaves out and for rewriting hex tokens only. They
are private, and were copied to private/generated/s4/d3d9-stride/first-version/
because the tool's stride16 run rewrites the first of them where it was
written. Nothing writes that directory, so that test is skipped only when it
is absent, as in a clone, and fails when a record there is not the one it was
written against.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import d3d9_stride_check as DS  # noqa: E402
import stage_boot as SB  # noqa: E402

POLLS = 5                  # so a run at the leash has 4 GameLoop returns


def runtime_constant(name: str) -> int:
    """`name` as runtime/sa2_d3d9.h or runtime/sa2_d3d9.c defines it, by #define
    or as an enum member."""
    for f in ("runtime/sa2_d3d9.h", "runtime/sa2_d3d9.c"):
        m = re.search(rf"(?:#define\s+{name}\s+|\b{name}\s*=\s*)(0x[0-9A-Fa-f]+|\d+)u?\b",
                      (ROOT / f).read_text())
        if m:
            return int(m.group(1), 0)
    raise AssertionError(f"the runtime defines no {name}")


def obj(i: int, stride: int, offset: int = 0) -> int:
    return DS.OBJ_BASE + i * stride + offset


def hx(v: int) -> str:
    return f"0x{v:08x}"


def record(stride: int = 16, fill: int = 0, polls: int = POLLS, objects: int = 3,
           note: str = "the guest called ExitProcess(0)") -> dict:
    """A run record with what the check reads, its object addresses laid out at
    `stride` the way crt_probe.c writes them, and a count inside the object
    region, as heap.live_bytes can be, which is the same at every stride."""
    def at(i: int) -> str:
        return hx(obj(i, stride))
    return {"pump": {"iterations": polls, "poll_limit": polls, "empty_polls": polls - 1,
                     "quit_by_host": 1, "quit_by_guest": 0, "injected": 0},
            "calls": {"total": 120},
            "trap": {"note": note},
            "heap": {"allocs": 7, "live_bytes": DS.OBJ_BASE + 0x40},
            "d3d9": {"object_stride": stride, "object_fill": fill, "objects_built": objects,
                     "method_calls": 50, "answers": {"noop": 3, "object": objects},
                     "frames": {"present": 4, "draw": 9},
                     "calls": [{"this": at(i), "args": [at((i + 1) % objects), "0x00000000"]}
                               for i in range(objects)]}}


FILES = {"ticks": "a" * 64, "trace": "b" * 64, "input_applied": "c" * 64}


def run_state(rec: dict, stride: int, files: dict | None = None) -> dict:
    return DS.state(rec, DS.normalise(rec, stride), files or dict(FILES))


# ------------------------------------------------ the runtime's definitions

def test_the_region_and_the_layouts_are_the_runtimes():
    assert DS.OBJ_BASE == runtime_constant("SA2_D3D9_OBJ_BASE")
    # the first block of the pool, the region the wide layouts share
    assert DS.OBJ_SPAN == (runtime_constant("SA2_D3D9_OBJ_FIRST_COUNT")
                           * runtime_constant("SA2_D3D9_OBJ_STRIDE"))
    # the vtables start past the region, so a vtable pointer is never an object
    assert runtime_constant("SA2_D3D9_VT_BASE") >= DS.OBJ_BASE + DS.OBJ_SPAN
    arms = (*DS.ARMS, DS.CONTROL)
    wide = runtime_constant("SA2_D3D9_WIDE_STRIDE")
    assert [a.stride for a in arms] == [runtime_constant("SA2_D3D9_OBJ_STRIDE"), wide, wide, wide]
    assert [a.fill for a in arms] == [runtime_constant(f"SA2_D3D9_FILL_{n}")
                                      for n in ("NONE", "SENTINEL", "ZERO", "VTABLE")]
    assert DS.WIDE_CEILING == DS.OBJ_SPAN // wide == 4096


def test_every_run_sets_its_break_the_shipping_one_to_empty():
    arms = (*DS.ARMS, DS.CONTROL)
    assert [(a.label, a.brk) for a in arms] == [
        ("stride16", ""), ("stride64-sentinel", "wideobj"), ("stride64-zero", "wideobjzero"),
        ("stride64-vtable", "wideobjvt")]
    for arm in arms:
        env = DS.arm_env(arm, DS.OUT / f"{arm.label}.trace.csv")
        # present on every arm, "" included: an absent key lets the caller's break in
        assert env["SA2_D3D9_BREAK"] == arm.brk
        assert env["SA2_STAGE_TRACE"] == f"private/generated/s4/d3d9-stride/{arm.label}.trace.csv"


def test_every_pinned_path_is_a_file_of_this_repository():
    # check_receipt_pins.py fails a receipt whose inputs.pipeline names anything else
    for t in DS.PIPELINE:
        assert not t.startswith(("private/", "/")) and (ROOT / t).is_file(), t
    # and none is ignored: an ignored file is on this disk and in no clone, a pin
    # nobody else can check. check-ignore exits 1 when it ignores none of the
    # paths, 0 naming the ones it does, and 128 when git fails.
    p = subprocess.run(["git", "check-ignore", "--", *DS.PIPELINE], cwd=str(ROOT),
                       capture_output=True, text=True)
    assert p.returncode == 1, p.stdout or p.stderr


# ------------------------------------------------------------ normalising

def test_the_same_object_reads_the_same_at_either_stride():
    assert DS.normalise({"this": hx(obj(4, 16))}, 16) == {"this": "obj[4]+0x0"}
    assert DS.normalise({"this": hx(obj(4, 64))}, 64) == {"this": "obj[4]+0x0"}
    assert (DS.normalise(hx(obj(4, 16, 8)), 16) == DS.normalise(hx(obj(4, 64, 8)), 64)
            == "obj[4]+0x8")


def test_different_objects_read_differently():
    assert DS.normalise(hx(obj(4, 16)), 16) != DS.normalise(hx(obj(5, 64)), 64)


def test_an_interior_pointer_never_reads_as_the_object_it_points_into():
    # what the offset is kept for: word 4 of a wide object 3, and its tag word
    assert DS.normalise(hx(obj(3, 64, 16)), 64) == "obj[3]+0x10"
    assert DS.normalise(hx(obj(3, 16, 4)), 16) == "obj[3]+0x4"
    assert DS.normalise(hx(obj(3, 64)), 64) == "obj[3]+0x0"
    # the same bytes past object 3 at the shipping stride are object 4 itself
    assert DS.normalise(hx(obj(3, 16, 16)), 16) == "obj[4]+0x0"


def test_an_integer_is_a_count_and_is_kept_whatever_its_value():
    # counts come near the region; read as objects, the same count would name
    # a different object at each stride and two agreeing runs would differ
    counts = {"heap": {"live_bytes": obj(4, 16)},
              "dsound": {"sound_buffers": [{"now_us": obj(5, 64, 8)}]}}
    assert DS.normalise(counts, 16) == DS.normalise(counts, 64) == counts


def test_everything_outside_the_region_is_kept_exactly():
    kept = {"below": hx(DS.OBJ_BASE - 1), "vtable": hx(DS.OBJ_BASE + DS.OBJ_SPAN),
            "end": DS.OBJ_BASE + DS.OBJ_SPAN, "count": 1299, "flag": True, "none": None,
            "ratio": 1.5, "steam_id": "0x0110000100000001", "caller": "0x007a58a9"}
    assert DS.normalise(kept, 16) == kept
    assert DS.normalise(hx(DS.OBJ_BASE + DS.OBJ_SPAN - 1), 16) == "obj[16383]+0xf"


def test_an_address_in_prose_or_in_a_key_is_rewritten_and_part_of_a_token_is_not():
    assert (DS.normalise(f"Release on {hx(obj(4, 16))} from 0x007a58a9", 16)
            == "Release on obj[4]+0x0 from 0x007a58a9")
    assert DS.normalise("0X03200040", 16) == "obj[4]+0x0"
    assert DS.normalise({hx(obj(2, 64)): 1}, 64) == {"obj[2]+0x0": 1}
    for token in (hx(obj(4, 16)) + "u", "x" + hx(obj(4, 16))):
        assert DS.normalise(token, 16) == token


def test_only_the_layouts_own_keys_are_left_out():
    rec = {"d3d9": {"object_stride": 16, "object_fill": 0, "objects_built": 3},
           "dsound": {"object_stride": 16}}
    assert DS.normalise(rec, 16) == {"d3d9": {"objects_built": 3}, "dsound": {"object_stride": 16}}
    assert DS.normalise(rec, 16, frozenset()) == rec


def test_where_different_names_each_place_with_both_values():
    a = {"pump": {"iterations": 5}, "calls": [1, 2], "only_a": 0, "flag": 1}
    b = {"pump": {"iterations": 4}, "calls": [1, 3, 4], "only_b": 0, "flag": True}
    assert DS.where_different(a, b) == [
        "pump.iterations: 5 != 4", "calls: 2 entries, not 3", "calls[1]: 2 != 3",
        "only_a: only in the first", "flag: 1 != true", "only_b: only in the second"]
    assert DS.where_different(a, a) == []
    assert len(DS.where_different({"k": list(range(20))}, {"k": list(range(1, 21))})) == 8


FIRST_VERSION = ROOT / "private" / "generated" / "s4" / "d3d9-stride" / "first-version"
FIRST = {FIRST_VERSION / "stage-metalharbor-input.stride16.json":
         "25295c6cd05050ce47f6370dc58a354ba668971619a1c95d9a72e61f6c8c8849",
         FIRST_VERSION / "stage-metalharbor-input.stride64.json":
         "516c46a5c65ee734eb47e0d9c3de6c9fea94ad72a0a3415b7ccafa4eb47a1f75"}


def integers(value):
    """Every integer in a decoded JSON value, booleans aside."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        for v in value:
            yield from integers(v)
    elif isinstance(value, int) and not isinstance(value, bool):
        yield value


def test_the_first_versions_two_real_records_normalise_equal():
    if not FIRST_VERSION.is_dir():
        pytest.skip(f"no {FIRST_VERSION.relative_to(ROOT)}: the private tree is not here")
    for p, digest in FIRST.items():
        # nothing writes there, so another digest is changed evidence, not a later run
        assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == digest, p
    s16, s64 = (json.loads(p.read_text()) for p in FIRST)
    # crt_probe.c prints addresses as hex: no integer is in the region, so
    # leaving integers alone leaves no address unrewritten
    for rec in (s16, s64):
        assert not [n for n in integers(rec) if DS.OBJ_BASE <= n < DS.OBJ_BASE + DS.OBJ_SPAN]
    assert DS.normalise(s16, 16) == DS.normalise(s64, 64)
    # LAYOUT_KEYS' evidence: with nothing left out, the stride is the only difference
    whole = DS.normalise(s16, 16, frozenset()), DS.normalise(s64, 64, frozenset())
    assert DS.where_different(*whole) == ["d3d9.object_stride: 16 != 64"]
    # read at the wrong stride they disagree, so the rewrite is what makes them agree
    assert DS.where_different(DS.normalise(s16, 16), DS.normalise(s64, 16), limit=1) == [
        "d3d9.textures_from_files.rows[0].texture: obj[38]+0x0 != obj[152]+0x0"]


# -------------------------------------------------------------- comparing

MUTATIONS = {
    "ticks_sha256": lambda r, f: f.update(ticks="d" * 64),
    "trace_sha256": lambda r, f: f.update(trace="d" * 64),
    "input_applied_sha256": lambda r, f: f.update(input_applied="d" * 64),
    "stop": lambda r, f: r["trap"].update(note="d3d9: the fabricated-object pool is exhausted"),
    "iterations": lambda r, f: r["pump"].update(iterations=4),
    "import_calls": lambda r, f: r["calls"].update(total=121),
    "d3d9_method_calls": lambda r, f: r["d3d9"].update(method_calls=51),
    "d3d9_answers": lambda r, f: r["d3d9"]["answers"].update(noop=4),
    "d3d9_frames": lambda r, f: r["d3d9"]["frames"].update(draw=10),
    "objects_built": lambda r, f: r["d3d9"].update(objects_built=4),
    "record_sha256": lambda r, f: r["heap"].update(allocs=8),     # in nothing named
}


def test_every_compared_item_has_a_mutation():
    assert set(MUTATIONS) == set(DS.COMPARED)


def test_the_same_run_in_three_layouts_agrees_on_everything():
    states = [run_state(record(16, 0), 16), run_state(record(64, 1), 64),
              run_state(record(64, 2), 64)]
    assert DS.disagreements(states, list(DS.COMPARED)) == {}


@pytest.mark.parametrize("key", list(MUTATIONS))
def test_each_compared_item_disagrees_on_its_own(key):
    rec, files = record(64, 2), dict(FILES)
    MUTATIONS[key](rec, files)
    got = DS.disagreements([run_state(record(16, 0), 16), run_state(rec, 64, files)],
                           list(DS.COMPARED))
    in_the_record = key in ("stop", "record_sha256") or not key.endswith("_sha256")
    assert set(got) == {key} | ({"record_sha256"} if in_the_record else set())


def test_a_stop_that_names_an_object_agrees_across_strides():
    a = record(16, 0, note=f"d3d9: Release on {hx(obj(2, 16))}")
    b = record(64, 1, note=f"d3d9: Release on {hx(obj(2, 64))}")
    assert DS.disagreements([run_state(a, 16), run_state(b, 64)], list(DS.COMPARED)) == {}


def test_values_are_compared_as_json():
    assert DS.disagreements([{"k": 1}, {"k": True}], ["k"]) == {"k": [1, True]}
    assert DS.disagreements([{"k": {"a": 1, "b": 2}}, {"k": {"b": 2, "a": 1}}], ["k"]) == {}


def test_the_verdict_needs_agreeing_layouts_and_a_differing_control():
    same, other = {"k": 1, "j": 2}, {"k": 1, "j": 3}
    assert DS.judge([same, same, same], other, ["k", "j"]) == {
        "ok": True, "differences": {}, "control_differs_on": ["j"]}
    assert DS.judge([same, same, same], same, ["k", "j"]) == {
        "ok": False, "differences": {}, "control_differs_on": []}
    assert DS.judge([same, other, same], other, ["k", "j"]) == {
        "ok": False, "differences": {"j": [2, 3, 2]}, "control_differs_on": ["j"]}
    # a layout that did not cover the stage fails whatever its state
    assert DS.judge([same, same, same], other, ["k", "j"],
                    {"stride64-zero": ["3 iterations, not the 5 polls asked for"]}) == {
        "ok": False, "differences": {}, "control_differs_on": ["j"]}


def test_the_answer_names_what_was_compared_and_nothing_else():
    assert (DS.answer(["ticks_sha256", "stop"], ["stride16", "stride64-sentinel"],
                      "stride64-vtable", ["stop"])
            == "No read past +16 changed the tick capture and the stop: stride16 and "
               "stride64-sentinel agree on each. The control, stride64-vtable, differs on "
               "the stop.")


# --------------------------------------------------------------- coverage

ROWS = {"ticks": POLLS - 1, "trace": POLLS - 1, "input_applied": POLLS}
GUEST_QUIT = "the guest quit on its own, or the record does not say"


def test_a_run_to_the_leash_covers_the_stage():
    assert DS.coverage(record(), POLLS, ROWS, True) == []
    assert DS.coverage(record(), POLLS, dict(ROWS, input_applied=None), False) == []


@pytest.mark.parametrize("change, rows, reason", [
    (lambda p: p.update(iterations=3), {}, "3 iterations, not the 5 polls asked for"),
    (lambda p: p.update(quit_by_host=0), {}, "the host did not post the quit"),
    (lambda p: p.update(quit_by_guest=1), {}, GUEST_QUIT),
    (lambda p: p.pop("quit_by_guest"), {}, GUEST_QUIT),
    (lambda p: p.update(injected=1), {}, "injected 1 message(s), not 0"),
    (lambda p: p.pop("injected"), {}, "injected None message(s), not 0"),
    (lambda p: None, {"ticks": None}, "no tick-capture file"),
    (lambda p: None, {"trace": 0}, "no stage-trace rows"),
    (lambda p: None, {"ticks": 3}, "3 tick-capture rows, not one per GameLoop return (4)"),
    (lambda p: None, {"trace": 5}, "5 stage-trace rows, not one per GameLoop return (4)"),
    (lambda p: None, {"input_applied": 0}, "no input-applied log, with an input to apply"),
])
def test_a_run_short_of_the_leash_does_not(change, rows, reason):
    rec = record()
    change(rec["pump"])
    assert DS.coverage(rec, POLLS, dict(ROWS, **rows), True) == [reason]


def test_one_poll_covers_nothing():
    assert DS.coverage(record(polls=1), 1, {"ticks": 0, "trace": 0}, False) == [
        "no tick-capture rows", "no stage-trace rows"]


def test_a_run_must_report_the_layout_it_asked_for():
    sentinel = DS.ARMS[1]
    assert DS.layout(record(64, 1), sentinel) == []
    assert DS.layout(record(16, 0), sentinel) == ["d3d9.object_stride is 16, not 64",
                                                  "d3d9.object_fill is 0, not 1"]
    assert DS.layout(record(64, 2), sentinel) == ["d3d9.object_fill is 2, not 1"]
    before_the_fill = record(64, 1)
    del before_the_fill["d3d9"]["object_fill"]
    assert DS.layout(before_the_fill, sentinel) == ["d3d9.object_fill is None, not 1"]


def trace_rows(*states: int) -> list[dict]:
    return [{"tick": str(t), "game_state": f"0x{s:04x}"} for t, s in enumerate(states, 1)]


def test_level_loads_count_each_arrival_in_game():
    assert DS.level_loads(trace_rows(0, 1, 2, 7, 16, 16, 9, 5, 7, 16, 16)) == 2
    assert DS.level_loads(trace_rows(16, 16)) == 1
    assert DS.level_loads(trace_rows(0, 7, 9)) == 0


# ------------------------------------------------------------------ files

def test_rows_and_digests_tell_a_missing_file_from_an_empty_one(tmp_path):
    missing, empty, header, two = (tmp_path / n for n in ("m.csv", "e.csv", "h.csv", "t.csv"))
    empty.write_text("")
    header.write_text("tick,x\n")
    two.write_text("tick,x\n1,a\n2,b\n")
    assert [DS.rows_of(p) for p in (missing, empty, header, two)] == [None, 0, 0, 2]
    assert DS.sha_of(missing) is None
    assert DS.sha_of(empty) == hashlib.sha256(b"").hexdigest()
    # two missing files agree: coverage(), not the digest, keeps a missing file out
    assert DS.disagreements([{"k": DS.sha_of(missing)}, {"k": DS.sha_of(tmp_path / "x.csv")}],
                            ["k"]) == {}


def test_first_line_finds_where_two_files_part(tmp_path):
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    a.write_text("h\n1\n2\n3\n")
    b.write_text("h\n1\nX\n3\n")
    c.write_text("h\n1\n")
    assert DS.first_line(a, a) is None
    assert DS.first_line(a, b) == 3
    assert DS.first_line(a, c) == 3
    assert DS.first_line(a, tmp_path / "missing") == 1


def test_inputs_pin_the_pipeline_and_the_recording_beside_it(tmp_path):
    for t in DS.PIPELINE:
        (tmp_path / t).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / t).write_text(t)
    recording = tmp_path / "sa2-input.csv"
    recording.write_text("tick\n")
    got = DS.inputs(recording, tmp_path)
    assert got["pipeline"] == {t: hashlib.sha256(t.encode()).hexdigest() for t in DS.PIPELINE}
    assert got["input_csv"] == {"path": str(recording),
                                "sha256": hashlib.sha256(b"tick\n").hexdigest()}
    assert DS.inputs(None, tmp_path)["input_csv"] is None
    (tmp_path / "runtime" / "sa2_d3d9.h").unlink()
    with pytest.raises(SystemExit, match="does not exist"):
        DS.inputs(None, tmp_path)


def test_provenance_reads_this_repositorys_commit_and_tree(monkeypatch):
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "0")     # status must not take the index lock
    got = DS.provenance()
    assert re.fullmatch(r"[0-9a-f]{40}", got["commit"])
    assert isinstance(got["uncommitted_changes"], bool)


def test_provenance_refuses_a_git_that_fails_rather_than_record_a_clean_tree(tmp_path,
                                                                              monkeypatch):
    # unchecked, git's empty output outside a repository read as commit "" and no changes
    monkeypatch.setattr(DS, "ROOT", tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    monkeypatch.setenv("LC_ALL", "C")                  # git's message untranslated
    for var in ("GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(SystemExit, match=re.escape(
            "D3D9_STRIDE_REFUSED git rev-parse HEAD exited 128: fatal: not a git repository")):
        DS.provenance()


def test_the_alarm_defaults_to_600_s_and_the_command_names_it(tmp_path, monkeypatch):
    # the probe's own 120 s surrounded runs of about 105 s, so the tool sets its own
    seen: list[argparse.Namespace] = []
    monkeypatch.setattr(DS, "OUT", tmp_path)
    monkeypatch.setattr(DS, "check", lambda a: seen.append(a) or 0)
    monkeypatch.setattr(sys, "argv", ["d3d9_stride_check.py"])
    assert DS.main() == 0
    assert DS.command(seen[0]) == (".venv/bin/python tools/s4/d3d9_stride_check.py --polls 1300 "
                                   "--timeout 600 --write")


# ------------------------------------------------- check(), without the guest

def write_files(where: Path, label: str, rows: int = POLLS - 1, ingame: bool = True) -> dict:
    """The files one run writes, under a directory of its own: the stage trace
    (in game from its second tick when `ingame`), the tick capture and the
    input-applied log."""
    d = where / label
    d.mkdir(exist_ok=True)
    trace = d / "trace.csv"
    with trace.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(SB.TRACE_HEADER)
        for t in range(1, rows + 1):
            state = 16 if ingame and t > 1 else 7
            w.writerow([t, "0x000a", "0x00000005", f"0x{state:04x}", f"0x{t:08x}",
                        "0x01dea6c0", "0x00000000", "0x00000000", "0x00000000"])
    ticks = d / "ticks.csv"
    ticks.write_text("tick,frame_count\n" + "".join(f"{t},0x{t:08x}\n" for t in range(1, rows + 1)))
    applied = d / "input-applied.csv"
    applied.write_text("tick,ord,matched\n" + "".join(f"{t},0,1\n" for t in range(rows + 1)))
    return {"trace": trace, "ticks": ticks, "input_applied": applied}


def fake(where: Path, change=None, ran: list | None = None):
    """check()'s `run` with no guest: what run_arm returns for each arm, the
    control stopping at its first method call the way wideobjvt's does, then
    `change(arm, got)` for the case under test."""
    def run(arm, a):
        if ran is not None:
            ran.append(arm.label)
        got = {"record": record(arm.stride, arm.fill, a.polls),
               "files": write_files(where, arm.label, a.polls - 1), "binary_sha256": "e" * 64}
        if arm == DS.CONTROL:
            got["record"]["pump"]["iterations"] = 1
            got["record"]["trap"]["note"] = "control left the lifted code for 0xd3d95e47"
        if change:
            change(arm, got)
        got["run_json"] = where / f"{arm.label}.json"
        got["run_json"].write_text(json.dumps(got["record"]))
        return got
    return run


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(DS, "ROOT", tmp_path)
    monkeypatch.setattr(DS, "RECEIPT", tmp_path / "receipt.json")
    monkeypatch.setattr(DS, "inputs", lambda csv_: {"pipeline": {"tools/s4/x.py": "f" * 64}})
    monkeypatch.setattr(DS, "provenance", lambda: {"commit": "c" * 40, "uncommitted_changes": True})
    return tmp_path


def args(where: Path, write: bool = True, recording: bool = True) -> argparse.Namespace:
    return argparse.Namespace(input=where / "sa2-input.csv" if recording else None,
                              polls=POLLS, timeout=600, write=write)


LABELS = ["stride16", "stride64-sentinel", "stride64-zero"]


def test_agreeing_layouts_and_a_differing_control_write_the_receipt(world, capsys):
    assert DS.check(args(world), fake(world)) == 0
    r = json.loads((world / "receipt.json").read_text())
    assert [(x["label"], x["break"], x["object_stride"], x["object_fill"]) for x in r["layouts"]] \
        == [("stride16", "", 16, 0), ("stride64-sentinel", "wideobj", 64, 1),
            ("stride64-zero", "wideobjzero", 64, 2)]
    control = ["stop", "iterations", "record_sha256"]
    assert (r["control"]["label"], r["control"]["differs_on"]) == ("stride64-vtable", control)
    assert r["compared"] == DS.COMPARED
    assert r["answer"] == DS.answer(list(DS.COMPARED), LABELS, "stride64-vtable", control)
    assert (r["objects_built"], r["wide_object_ceiling"], r["level_loads"]) == (3, 4096, 1)
    assert r["limits"][2:] == DS.limits(3, 1)[2:] and "this run built 3" in r["limits"][2]
    assert (r["commit"], r["uncommitted_changes"]) == ("c" * 40, True)
    assert r["inputs"] == {"pipeline": {"tools/s4/x.py": "f" * 64}}
    assert r["command"] == (f".venv/bin/python tools/s4/d3d9_stride_check.py --input "
                            f"{world / 'sa2-input.csv'} --polls 5 --timeout 600 --write")
    assert r["layouts"][2]["files"] == {"trace": "stride64-zero/trace.csv",
                                        "ticks": "stride64-zero/ticks.csv",
                                        "input_applied": "stride64-zero/input-applied.csv"}
    assert r["layouts"][2]["rows"] == {"trace": 4, "ticks": 4, "input_applied": 5}
    assert capsys.readouterr().out.startswith("D3D9_STRIDE OK")


def test_without_write_the_receipt_is_not_written(world):
    assert DS.check(args(world, write=False), fake(world)) == 0
    assert not (world / "receipt.json").exists()


def test_without_an_input_its_log_is_neither_required_nor_claimed(world):
    def no_log(arm, got):
        got["files"]["input_applied"].unlink()
    assert DS.check(args(world, recording=False), fake(world, no_log)) == 0
    r = json.loads((world / "receipt.json").read_text())
    assert "input_applied_sha256" not in r["compared"]
    assert "input-applied" not in r["answer"]
    assert r["layouts"][0]["files"]["input_applied"] is None
    assert r["command"] == (".venv/bin/python tools/s4/d3d9_stride_check.py --polls 5 "
                            "--timeout 600 --write")


def test_a_control_that_agrees_fails_the_check(world, capsys):
    def agree(arm, got):
        if arm == DS.CONTROL:
            got["record"] = record(arm.stride, arm.fill)
    assert DS.check(args(world), fake(world, agree)) == 1
    assert not (world / "receipt.json").exists()
    out = capsys.readouterr().out
    assert out.startswith("D3D9_STRIDE FAILED") and "the control agrees with stride16" in out


@pytest.mark.parametrize("key, change, where", [
    ("ticks_sha256", lambda got: got["files"]["ticks"].write_text(
        "tick,frame_count\n1,0x2\n2,0x2\n3,0x3\n4,0x4\n"),
     "stride64-zero: its ticks file first differs from stride16's at line 2"),
    ("d3d9_frames", lambda got: got["record"]["d3d9"]["frames"].update(draw=10),
     "stride64-zero against stride16: d3d9.frames.draw: 9 != 10"),
    ("record_sha256", lambda got: got["record"]["heap"].update(allocs=8),
     "stride64-zero against stride16: heap.allocs: 7 != 8"),
])
def test_a_layout_that_differs_fails_the_check_and_says_where(world, capsys, key, change, where):
    def differ(arm, got):
        if arm == DS.ARMS[2]:
            change(got)
    assert DS.check(args(world), fake(world, differ)) == 1
    assert not (world / "receipt.json").exists()
    out = capsys.readouterr().out
    assert out.startswith("D3D9_STRIDE FAILED")
    assert f"\n  {key} ({DS.COMPARED[key]}): " in out and where in out
    assert "a difference needs diagnosis before it is attributed to a read past +16" in out


def on(arm, change):
    """A change made to one arm's run only."""
    return lambda a, got: change(got) if a == arm else None


def short_ticks(got):
    got["files"]["ticks"].write_text("tick,frame_count\n1,0x1\n")


def wrong_stride_and_short(got):
    got["record"]["d3d9"].update(object_stride=16)
    got["record"]["pump"].update(iterations=3)


SENTINEL_TRAP = "the guest read unmapped memory at 0xd3d95e47"


def sentinel_trapped(got):
    """The sentinel read as a pointer, and the run trapping on it at poll 3."""
    got["record"]["pump"].update(iterations=3, quit_by_host=0)
    got["record"]["trap"]["note"] = SENTINEL_TRAP


def trapped_and_another_binary(arm, got):
    """The sentinel run trapping, and the control run from another binary."""
    if arm == DS.ARMS[1]:
        sentinel_trapped(got)
    if arm == DS.CONTROL:
        got.update(binary_sha256="0" * 64)


@pytest.mark.parametrize("change, refusal", [
    (on(DS.ARMS[0], lambda got: got["record"]["pump"].update(iterations=3)),
     "stride16: 3 iterations, not the 5 polls asked for"),
    (on(DS.ARMS[0], short_ticks),
     "stride16: 1 tick-capture rows, not one per GameLoop return (4)"),
    (on(DS.ARMS[0], lambda got: got["record"]["d3d9"].update(object_stride=64, object_fill=1)),
     "stride16: d3d9.object_stride is 64, not 16; d3d9.object_fill is 1, not 0"),
    (on(DS.ARMS[1], lambda got: got["record"]["d3d9"].update(object_fill=2)),
     "stride64-sentinel: d3d9.object_fill is 2, not 1"),
    # a wide run that did not run its layout is refused, stopped short or not
    (on(DS.ARMS[2], wrong_stride_and_short),
     "stride64-zero: d3d9.object_stride is 16, not 64; 3 iterations, not the 5 polls asked for"),
    (on(DS.CONTROL, lambda got: got["record"]["d3d9"].update(object_stride=0, object_fill=None)),
     "stride64-vtable: d3d9.object_stride is 0, not 64; d3d9.object_fill is None, not 3; a "
     "control that did not run its layout shows nothing"),
    (on(DS.ARMS[0], lambda got: got["record"]["d3d9"].update(objects_built=0)),
     "stride16: 0 objects built, so no layout was used"),
    (on(DS.ARMS[0], lambda got: write_files(got["files"]["trace"].parents[1], "stride16",
                                            ingame=False)),
     "stride16: GameState never reached 16 in its stage trace, so no stage was compared"),
    (on(DS.CONTROL, lambda got: got.update(binary_sha256="0" * 64)),
     "the runs were not one binary"),
    (on(DS.ARMS[1], lambda got: got.update(binary_sha256=None)),
     "the runs were not one binary"),
    # a failure needs one binary as much as a pass does
    (trapped_and_another_binary, "the runs were not one binary"),
])
def test_what_cannot_be_compared_is_refused_and_nothing_written(world, change, refusal):
    with pytest.raises(SystemExit, match=re.escape(f"D3D9_STRIDE_REFUSED {refusal}")):
        DS.check(args(world), fake(world, change))
    assert not (world / "receipt.json").exists()


@pytest.mark.parametrize("arm, change, why, stop", [
    (DS.ARMS[1], sentinel_trapped,
     "3 iterations, not the 5 polls asked for; the host did not post the quit", SENTINEL_TRAP),
    (DS.ARMS[2], short_ticks, "1 tick-capture rows, not one per GameLoop return (4)",
     "the guest called ExitProcess(0)"),
])
def test_a_wide_layout_that_does_not_cover_the_stage_fails_the_check(world, capsys, arm, change,
                                                                     why, stop):
    ran: list[str] = []
    assert DS.check(args(world), fake(world, on(arm, change), ran)) == 1
    assert not (world / "receipt.json").exists()
    # every run still ran: whether the other wide layout stops too is the diagnosis
    assert ran == [*LABELS, DS.CONTROL.label]
    out = capsys.readouterr().out
    assert out.startswith("D3D9_STRIDE FAILED")
    assert (f"\n  {arm.label} did not cover the stage stride16 covered ({why}); its stop: "
            f"{stop}\n") in out
    assert "a difference needs diagnosis before it is attributed to a read past +16" in out


def test_a_wide_layout_stopped_by_the_wall_clock_alarm_is_refused_not_failed(world):
    def alarmed(got):
        got["record"]["pump"].update(iterations=3, quit_by_host=0)
        got["record"]["trap"].update(status=DS.TRAP_TIMEOUT,
                                     note="call exceeded its wall-clock budget")
    with pytest.raises(SystemExit, match=re.escape(
            "D3D9_STRIDE_REFUSED stride64-sentinel: stopped by the harness's wall-clock alarm")):
        DS.check(args(world), fake(world, on(DS.ARMS[1], alarmed)))
    assert not (world / "receipt.json").exists()


def test_a_run_longer_than_the_wide_layouts_hold_is_refused_before_they_run(world):
    ran: list[str] = []
    change = on(DS.ARMS[0], lambda got: got["record"]["d3d9"].update(
        objects_built=DS.WIDE_CEILING + 1))
    with pytest.raises(SystemExit, match=re.escape(
            "D3D9_STRIDE_REFUSED stride16 built 4097 objects and the wide layouts hold 4096")):
        DS.check(args(world), fake(world, change, ran))
    assert ran == ["stride16"]


def test_an_input_that_moves_while_the_runs_run_is_refused(world, monkeypatch):
    seen = iter([{"pipeline": {"tools/s4/x.py": d * 64}} for d in "12"])
    monkeypatch.setattr(DS, "inputs", lambda csv_: next(seen))
    with pytest.raises(SystemExit, match="D3D9_STRIDE_REFUSED an input moved while the runs ran"):
        DS.check(args(world), fake(world))
    assert not (world / "receipt.json").exists()
