"""tools/s5/poll_handoff_check.py's comparisons, with no build and no run.

The run record is compared leaf for leaf and byte for byte with the registered
one, and only the write overlay and the rule's one line may differ; the word log
is compared tick hash for tick hash, with a one-tick-shift control, and each of
its problems is shown on hashes made to differ; the baseline refuses a pin that
does not hold and a registration that already carries the rule; the receipt
refuses results run on other inputs or against another baseline, a NEW FILE pair
that is not one run up to the hang, and a boot and NEW FILE pair that ran
different binaries."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
import poll_handoff_check as P  # noqa: E402

RULE = {"rule": 1, "polls": 72, "not_done": 36, "quiet": 0, "hand_offs": 0, "switched": 0,
        "first_hand_off": {"poll": 0, "import_call": 0, "frame": 0}}


def record(**threads) -> dict:
    return {"files": {"write_root": "/a"}, "calls": {"total": 5},
            "threads": {"switches": 9, "rows": [{"index": 0, "slices": 1.5}], **threads}}


# ------------------------------------------------------------------ leaf for leaf

def test_the_same_record_but_its_overlay_and_the_new_counters_is_equal():
    got = P.record_compare(record(poll_handoff=RULE), dict(record(), files={"write_root": "/b"}))
    assert got["ok"] is True and got["differing"] == []
    assert got["allowed"][0] == "files.write_root" and "threads.poll_handoff.polls" in got["allowed"]


@pytest.mark.parametrize("change,leaf", [
    (lambda r: r["calls"].update(total=6), "calls.total"),
    (lambda r: r["threads"]["rows"][0].update(slices=1), "threads.rows[0].slices"),   # 1 is not 1.5
    (lambda r: r["threads"].pop("switches"), "threads.switches"),
])
def test_any_other_leaf_that_differs_fails(change, leaf):
    mine = record(poll_handoff=RULE)
    change(mine)
    got = P.record_compare(mine, record())
    assert got["ok"] is False and got["differing"] == [leaf]


def test_a_value_of_another_type_differs():
    mine, theirs = record(), record()
    theirs["calls"]["total"] = 5.0
    assert P.record_compare(mine, theirs)["differing"] == ["calls.total"]


def test_once_the_registered_record_has_the_counters_they_must_match():
    theirs = record(poll_handoff=RULE)
    moved = copy.deepcopy(RULE)
    moved["polls"] = 71
    got = P.record_compare(record(poll_handoff=moved), theirs)
    assert got["registered_has_rule"] is True and got["differing"] == ["threads.poll_handoff.polls"]
    assert P.record_compare(record(), theirs)["ok"] is False      # the re-run lost the wrapper


# ------------------------------------------------------------------ byte for byte

REGISTERED = (b'{\n "files": {"guest_root": "C:\\\\SA2", "write_root": "/reg/vfsroot",\n'
              b'   "read_root": "/r"},\n "threads": {"switches": 9,\n'
              b'   "spin_clock_moves": 0, "cs_handoff": 1,\n'
              b'   "timers": {"created": 0}}\n}\n')
LINE = (b'   "poll_handoff": {"rule": 1, "polls": 72, "not_done": 36, "quiet": 0, '
        b'"hand_offs": 0, "switched": 0, "first_hand_off": {"poll": 0, "import_call": 0, '
        b'"frame": 0}},\n')


def rerun(registered: bytes = REGISTERED, line: bytes = LINE, root: bytes = b"/mine/vfsroot"):
    """The registered record as a re-run prints it: the rule's line after the spin
    rule's counters (harness/crt_probe.c), and its own overlay."""
    at = registered.index(b'   "timers"')
    return (registered[:at] + line + registered[at:]).replace(b"/reg/vfsroot", root)


def test_the_rules_line_and_the_overlay_are_the_only_bytes_a_rerun_may_add():
    got = P.record_bytes(rerun(), REGISTERED)
    assert got["equal"] is True and got["rule_lines"] == 1 and got["problems"] == []
    assert got["sha256_without_rule"] == got["registered_sha256"]


@pytest.mark.parametrize("mine,needle", [
    (rerun().replace(b'"switches": 9', b'"switches": 8'), "does not hash to the registered"),
    (rerun(line=LINE + LINE), "2 lines of the rule's counters, not one"),
    (rerun(line=b""), "0 lines of the rule's counters, not one"),
    (rerun().replace(b'"write_root": "/mine/vfsroot",', b""), "write_root appears 0 times"),
    (rerun().replace(b'"cs_handoff": 1,', b'"cs_handoff": 1, '), "does not hash"),   # one space
])
def test_any_other_byte_fails(mine, needle):
    got = P.record_bytes(mine, REGISTERED)
    assert got["equal"] is False and any(needle in p for p in got["problems"])


# ------------------------------------------------------------------ the word log

INFO = {"version": 2, "ticks": 4, "whole": True, "clk_init": 1, "clk_end": 9, "path": "x"}


@pytest.fixture
def logs(monkeypatch):
    """WL.hashes and WL.info standing in for the reader: each log named by a key into
    the table this returns, which a test fills."""
    table: dict = {}
    monkeypatch.setattr(P.WL, "hashes", lambda p: dict(table[str(p)][0]))
    monkeypatch.setattr(P.WL, "info", lambda p: dict(table[str(p)][1]))
    return table


def hashes(*values: str) -> dict[int, str]:
    return {t: v for t, v in enumerate(values)}


def test_equal_logs_compare_equal_and_the_shift_control_sees_both_ways(logs):
    logs["mine"] = logs["reg"] = (hashes("k", "a", "b", "c", "d"), INFO)
    got = P.word_log_compare(Path("mine"), Path("reg"))
    assert got["ok"] is True and got["problems"] == [] and got["ticks_differing"] == 0
    assert got["ticks_mine"] == got["ticks_registered"] == 5
    assert got["shift_control_ticks_differing"] == {"registered_tick_before": 4,
                                                    "registered_tick_after": 4}


def test_one_tick_that_differs_fails(logs):
    logs["mine"] = (hashes("k", "a", "B", "c", "d"), INFO)
    logs["reg"] = (hashes("k", "a", "b", "c", "d"), INFO)
    got = P.word_log_compare(Path("mine"), Path("reg"))
    assert got["ok"] is False and got["first_differing"] == [2]
    assert got["problems"] == ["the word log differs from the registered run's at 1 ticks"]


def test_a_shorter_log_fails(logs):
    logs["mine"] = (hashes("k", "a", "b"), INFO)
    logs["reg"] = (hashes("k", "a", "b", "c", "d"), INFO)
    got = P.word_log_compare(Path("mine"), Path("reg"))
    assert got["problems"] == ["the word log differs from the registered run's at 2 ticks"]


def test_logs_that_cannot_show_a_shift_fail_the_control(logs):
    logs["mine"] = logs["reg"] = (hashes("s", "s", "s", "s"), INFO)
    got = P.word_log_compare(Path("mine"), Path("reg"))
    assert got["ticks_differing"] == 0 and got["ok"] is False
    assert got["problems"] == ["the tick hashes are equal a tick off, so this comparison could "
                               "not have seen the two logs shifted a tick"]


def test_a_log_field_that_differs_fails_and_the_hosts_clock_does_not(logs):
    logs["mine"] = (hashes("k", "a", "b"), dict(INFO, clk_end=99, path="y", version=3))
    logs["reg"] = (hashes("k", "a", "b"), INFO)
    got = P.word_log_compare(Path("mine"), Path("reg"))
    assert got["fields_differing"] == ["version"] and got["fields_compared"] == 3
    assert got["problems"] == ["the word log's own fields differ: ['version']"]


def test_no_log_fails(logs):
    logs["reg"] = (hashes("k", "a"), INFO)
    assert P.word_log_compare(None, Path("reg"))["problems"][0] == "the run wrote no word log"


# ------------------------------------------------------------------ the baseline

def baseline_tree(tmp_path: Path, monkeypatch, rec: dict | None = None, pin_ok: bool = True):
    """A registered tree of one run with a pinned capture and an unpinned record."""
    root = tmp_path / "root"
    (root / "reg").mkdir(parents=True)
    (root / "reg" / "lifted.csv").write_text("tick,x\n1,a\n")
    (root / "reg" / "run.json").write_text(json.dumps(rec or record()))
    pin = P.sha256(root / "reg" / "lifted.csv") if pin_ok else "0" * 64
    monkeypatch.setattr(P, "ROOT", root)
    monkeypatch.setattr(P, "SOURCES", ())
    monkeypatch.setattr(P, "provenance", lambda: {"commit": "c" * 40})
    monkeypatch.setattr(P.RR, "OUT", root / "reach.json")
    (root / "reach.json").write_text(json.dumps({"reach": dict(P.TR.reach_of(rec or record()),
                                                              device_profile_of_replay=None)}))
    monkeypatch.setattr(P, "baseline_sources", lambda: {"reach": {
        "tick capture": ("reg/lifted.csv", pin, "a test pin"),
        "run record": ("reg/run.json", None, "no pin")}})
    return root


def test_the_baseline_copies_each_file_after_holding_it_to_its_pin(tmp_path, monkeypatch):
    root = baseline_tree(tmp_path, monkeypatch)
    doc = P.take_baseline(root / "base")
    files = doc["runs"]["reach"]["files"]
    assert files["tick capture"]["copy"] == "base/reach/lifted.csv"
    assert (root / "base" / "reach" / "lifted.csv").read_text() == "tick,x\n1,a\n"
    assert doc["runs"]["reach"]["has_rule"] is False
    base, sha = P.load_baseline(root / "base")
    assert base == doc and sha == P.sha256(root / "base" / "baseline.json")
    with pytest.raises(SystemExit, match="a baseline is taken once"):
        P.take_baseline(root / "base")


def test_the_baseline_refuses_a_file_that_is_not_its_pin(tmp_path, monkeypatch):
    root = baseline_tree(tmp_path, monkeypatch, pin_ok=False)
    with pytest.raises(SystemExit, match=r"reach: the registered tick capture reg/lifted.csv is "
                                         r"not its pin \(a test pin\)"):
        P.take_baseline(root / "base")
    assert not (root / "base").exists()


def test_the_baseline_refuses_a_registration_that_already_has_the_rule(tmp_path, monkeypatch):
    root = baseline_tree(tmp_path, monkeypatch, rec=record(poll_handoff=RULE))
    with pytest.raises(SystemExit, match="already carries threads.poll_handoff"):
        P.take_baseline(root / "base")


def test_a_changed_copy_is_refused(tmp_path, monkeypatch):
    root = baseline_tree(tmp_path, monkeypatch)
    P.take_baseline(root / "base")
    (root / "base" / "reach" / "lifted.csv").write_text("tick,x\n1,b\n")
    with pytest.raises(SystemExit, match="copies are missing or changed"):
        P.load_baseline(root / "base")


# ------------------------------------------------------------------ the NEW FILE runs

def newfile(rule_on=True, **change) -> dict:
    res = {"current_menu": {"file_select_at": 999, "main_menu_at": 1362 if rule_on else None},
           "pump": {"iterations": 2100 if rule_on else 1361, "quit_by_host": int(rule_on)},
           "poll_handoff": dict(RULE, rule=int(rule_on), quiet=190, hand_offs=190 if rule_on else 0,
                                switched=190 if rule_on else 0),
           "asyndo": {"count": 58, "finished": 58, "ready_with_no_slice": [] if rule_on else [62]}}
    for k, v in change.items():
        res[k].update(v)
    return res


def test_the_rule_and_its_break_each_read_as_they_must():
    assert P.newfile_problems(newfile(), True) == []
    assert P.newfile_problems(newfile(False), False) == []


@pytest.mark.parametrize("rule_on,change,needle", [
    (True, {"current_menu": {"main_menu_at": None}}, "main menu was never reached"),
    (True, {"poll_handoff": {"switched": 189}}, "a hand-off ran nobody"),
    (True, {"asyndo": {"ready_with_no_slice": [62]}}, "READY with no slice"),
    (True, {"pump": {"iterations": 1361}}, "did not last its poll budget"),
    (False, {"current_menu": {"main_menu_at": 1362}}, "break's run reached the main menu"),
    (False, {"poll_handoff": {"hand_offs": 1}}, "not the rule off at a pure spin"),
    (False, {"asyndo": {"ready_with_no_slice": []}}, "no AsynDo thread was left READY"),
])
def test_a_new_file_run_that_reads_otherwise_fails(rule_on, change, needle):
    assert any(needle in p for p in P.newfile_problems(newfile(rule_on, **change), rule_on))


def test_the_asyndo_rows_name_a_starved_worker():
    rec = {"threads": {"rows": [
        {"index": 1, "start_va": "0x008653D0", "state": "finished", "slices": 1},
        {"index": 2, "start_va": "0x008653d0", "state": "ready", "slices": 0},
        {"index": 3, "start_va": "0x007c53f0", "state": "ready", "slices": 0}]}}
    assert P.asyndo_threads(rec) == {"count": 2, "finished": 1, "ready_with_no_slice": [2]}


def test_leading_rows_are_counted_after_the_header(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_text("tick,x\n1,a\n2,b\n3,c\n")
    b.write_text("tick,x\n1,a\n2,b\n3,d\n4,e\n")
    assert P.first_equal_rows(a, b) == 2


# ------------------------------------------------------------------ the receipt

INPUTS = {"pipeline": {"tools/s5/poll_handoff_check.py": "1" * 64}, "runtime_sha256": "3" * 64}
BASE = {"commit": "c" * 40, "receipts": {}, "runs": {n: {"has_rule": False} for n in P.SILENT}}


def results(tmp_path: Path, rows: int = 3, hang: int = 3, binary: str = "b" * 64,
            inputs: dict | None = None, base_sha: str = "f" * 64) -> Path:
    for name in P.RUNS:
        d = tmp_path / name
        d.mkdir()
        res = {"run": name, "ok": True, "problems": [], "inputs": inputs or INPUTS,
               "build": {"sha256": "m" * 64 if name in ("metalharbor", "reach", "stage-boot")
                         else binary}}
        if name in P.SILENT:
            res.update(baseline_sha256=base_sha, record={"registered_has_rule": False})
        if name == "newfile-break":
            res["stage_trace_rows"] = hang
        if name.startswith("newfile"):
            body = "".join(f"{t},{'x' if name == 'newfile' or t <= rows else 'y'}\n"
                           for t in range(1, 6 if name == "newfile" else hang + 1))
            (d / "lifted.csv").write_text("tick,v\n" + body)
        (d / "result.json").write_text(json.dumps(res))
    return tmp_path


@pytest.fixture
def fixed(monkeypatch):
    monkeypatch.setattr(P, "tree_inputs", lambda: INPUTS)
    monkeypatch.setattr(P, "provenance", lambda: {"commit": "c" * 40})
    state = {"base": copy.deepcopy(BASE)}
    monkeypatch.setattr(P, "load_baseline", lambda d: (state["base"], "f" * 64))
    return state


def test_six_good_results_make_a_good_receipt(tmp_path, fixed):
    doc = P.receipt(results(tmp_path), tmp_path / "base")
    assert doc["ok"] is True and doc["problems"] == []
    assert doc["runs"]["newfile-break"]["capture_rows_shared_with_the_rule_on"] == 3
    assert doc["silent"] == ["reach", "replay:boot", "replay:metalharbor",
                             "stage-boot:metalharbor"]
    assert doc["baseline"]["sha256"] == "f" * 64


def test_the_receipt_refuses_a_result_from_other_inputs(tmp_path, fixed):
    other = copy.deepcopy(INPUTS)
    other["pipeline"]["tools/s5/poll_handoff_check.py"] = "4" * 64
    doc = P.receipt(results(tmp_path, inputs=other), tmp_path / "base")
    assert doc["ok"] is False
    assert "boot: run on other inputs than the tree's now: ['tools/s5/poll_handoff_check.py']" \
        in doc["problems"]


def test_the_receipt_refuses_a_run_compared_with_another_baseline(tmp_path, fixed):
    doc = P.receipt(results(tmp_path, base_sha="e" * 64), tmp_path / "base")
    assert doc["ok"] is False and doc["silent"] == ["reach", "replay:boot", "replay:metalharbor",
                                                    "stage-boot:metalharbor"]
    assert sum("compared with another baseline" in p for p in doc["problems"]) == 4


def test_the_receipt_refuses_a_baseline_that_has_the_rule(tmp_path, fixed):
    fixed["base"]["runs"]["metalharbor"]["has_rule"] = True
    doc = P.receipt(results(tmp_path), tmp_path / "base")
    assert doc["ok"] is False
    assert "metalharbor: its baseline record already carries the rule's counters" in doc["problems"]


def test_the_receipt_refuses_a_comparison_with_a_registration_that_had_the_rule(tmp_path, fixed):
    root = results(tmp_path)
    f = root / "boot" / "result.json"
    r = json.loads(f.read_text())
    r["record"]["registered_has_rule"] = True
    f.write_text(json.dumps(r))
    doc = P.receipt(root, tmp_path / "base")
    assert "boot: its comparison does not show a registered record without the rule" \
        in doc["problems"]


def test_the_receipt_refuses_a_break_that_left_the_rules_run_before_the_hang(tmp_path, fixed):
    doc = P.receipt(results(tmp_path, rows=2, hang=3), tmp_path / "base")
    assert doc["ok"] is False and any("up to the hang" in p for p in doc["problems"])


def test_the_receipt_refuses_a_boot_and_new_file_on_different_binaries(tmp_path, fixed):
    root = results(tmp_path)
    f = root / "newfile" / "result.json"
    r = json.loads(f.read_text())
    r["build"]["sha256"] = "z" * 64
    f.write_text(json.dumps(r))
    doc = P.receipt(root, tmp_path / "base")
    assert doc["ok"] is False and any("different binaries" in p for p in doc["problems"])


def test_the_receipt_refuses_a_missing_or_failed_run(tmp_path, fixed):
    root = results(tmp_path)
    (root / "metalharbor" / "result.json").unlink()
    f = root / "boot" / "result.json"
    r = json.loads(f.read_text())
    r.update(ok=False, problems=["the word log differs from the registered run's at 1 ticks"])
    f.write_text(json.dumps(r))
    doc = P.receipt(root, tmp_path / "base")
    assert doc["ok"] is False and "replay:boot" not in doc["silent"]
    assert "boot: the word log differs from the registered run's at 1 ticks" in doc["problems"]
    assert any(p.startswith("metalharbor: no result") for p in doc["problems"])


def test_the_receipt_pins_every_runtime_source_for_the_pin_gate(tmp_path, fixed, monkeypatch):
    """check_receipt_pins reads `inputs.pipeline`, not the runtime digest beside it, so the
    acceptance pins each runtime source there (runtime/*.c, *.cpp and *.h, the files the
    digest is made from), and the runs, whose inputs carry only the digest, still compare
    with the tree's (the 2026-10-02 confirm review's SHOULD-FIX: without the pins, an edit
    to runtime/sa2_threads.c folded into the fix's commit left the receipt naming a runtime
    no commit has, with every gate passing)."""
    root = tmp_path / "root"
    (root / "runtime").mkdir(parents=True)
    for name, body in (("sa2_threads.c", "t"), ("sa2_threads.h", "h"),
                       ("sa2_poll_handoff.c", "p"), ("remill_runtime.cpp", "r"),
                       ("README.txt", "not a source")):
        (root / "runtime" / name).write_text(body)
    monkeypatch.setattr(P, "ROOT", root)
    doc = P.receipt(results(tmp_path), tmp_path / "base")
    assert doc["ok"] is True and doc["problems"] == []
    assert doc["inputs"] == {"runtime_sha256": "3" * 64, "pipeline": {
        "tools/s5/poll_handoff_check.py": "1" * 64,
        "runtime/remill_runtime.cpp": "454349e422f05297191ead13e21d3db520e5abef52055e4964b82fb213f593a1",
        "runtime/sa2_poll_handoff.c": "148de9c5a7a44d19e56cd9ae1a554bf67847afb0c58f6e12fa29ac7ddfca9940",
        "runtime/sa2_threads.c": "e3b98a4da31a127d4bde6e43033f66ba274cab0eb7eb1c70ec41402bf6273dd8",
        "runtime/sa2_threads.h": "aaa9402664f1a41f40ebbc52c9993eb66aeb366602958fdfaa283b71e64db123"}}


def test_the_new_file_receipt_pins_both_pipelines_for_the_pin_gate(monkeypatch):
    monkeypatch.setattr(P, "provenance", lambda: {"commit": "c" * 40})
    manifest = {"build": {"pipeline": {"tools/s4/link_s4.py": "1" * 64}},
                "runtime_sha256": "2" * 64,
                "pipeline": {"tools/s5/poll_handoff_check.py": "3" * 64}}
    doc = P.regression_doc({"ok": True}, manifest)
    assert doc["id"] == "s5-newfile-wait-v1" and doc["ok"] is True
    assert doc["inputs"] == {"pipeline": {"tools/s4/link_s4.py": "1" * 64,
                                          "tools/s5/poll_handoff_check.py": "3" * 64}}
    assert doc["manifest"] == manifest
