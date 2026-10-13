"""thread_run.py --matrix judges its replay breaks, with no build and no run.

Each case hands matrix() canned run documents in place of run_rows and reads the
verdict. The noflsexit break must fail the matrix when its control's slots were
cleared without an FLS callback, when it is judged against the plain control,
and when its clause fires on a live worker."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import thread_run as T  # noqa: E402

CL = "every_ended_thread_released_its_crt_block"


def worker(i, state="finished", ptd=0, callbacks=0, start=0x008653D0):
    return {"index": i, "state": state, "crt_ptd": f"0x{ptd:08x}", "start_va": f"0x{start:08x}",
            "fls_exit_callbacks": callbacks, "trap_status": 0, "where": "LeaveCriticalSection"}


def doc(rows, clause_value, extra=()):
    names = [c for _, _, c, _ in T.BREAKS] + [CL] + list(extra)
    return {"calls": {"total": 1}, "trap": {"note": ""}, "threads": {"rows": rows},
            "winmain_loop": {"iterations": 1},
            "clauses": [{"name": n, "value": (clause_value if n == CL else True)} for n in names]}


def base_runs():
    main = worker(0, state="running", ptd=0x04000010, start=0)
    runs = {"control": doc([main, worker(1, state="blocked", ptd=0x05000000)], True)}
    for label, _, clause, _ in T.BREAKS:
        r = doc([main], True)
        for c in r["clauses"]:
            if c["name"] == clause:
                c["value"] = False
        runs[label] = r
    for label, _, _ in T.DIFF_BREAKS:
        runs[label] = doc([main], True)
        runs[label]["calls"]["total"] = 2
    runs["free"] = doc([main], True)
    runs["control-boot"] = doc([main, worker(1, callbacks=1), worker(2)], True)
    runs["noflsexit"] = doc([main, worker(1, ptd=0x058EAAC8), worker(2)], False)
    return runs


@pytest.fixture
def judge(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "OUTDIR", tmp_path)
    monkeypatch.setattr(T, "replay_env", lambda name=T.REPLAY: "SA2_IMPORT_BUDGET=4000000")

    def run(runs):
        monkeypatch.setattr(T, "run_rows", lambda rows, pt, build, extra_probe_args=None:
                            {l: copy.deepcopy(runs.get(l, {"error": "no run JSON"})) for l, _ in rows})
        m = T.matrix(240)
        return m, next(b for b in m["breaks"] if b["label"] == "noflsexit")
    return run


def test_the_break_moves_its_clause_against_a_control_whose_callbacks_released_the_block(judge):
    m, b = judge(base_runs())
    assert (m["ok"], b["moved"], b["control"]) == (True, True, "control-boot")


def test_a_control_that_cleared_the_slot_without_a_callback_fails_the_matrix(judge):
    runs = base_runs()
    runs["control-boot"]["threads"]["rows"][1]["fls_exit_callbacks"] = 0
    m, b = judge(runs)
    assert (m["ok"], b["moved"]) == (False, False)


def test_judged_against_the_plain_control_the_break_fails(judge):
    runs = base_runs()
    runs["control-boot"] = copy.deepcopy(runs["control"])
    m, b = judge(runs)
    assert (m["ok"], b["moved"]) == (False, False)


def test_a_clause_that_fires_on_a_live_worker_fails_the_matrix(judge):
    runs = base_runs()
    for c in runs["control"]["clauses"]:
        if c["name"] == CL:
            c["value"] = False
    m, b = judge(runs)
    assert (m["ok"], b["moved"]) == (False, False)


def test_a_missing_break_row_fails_the_matrix(judge):
    runs = base_runs()
    del runs["noflsexit"]
    m, b = judge(runs)
    assert (m["ok"], b["moved"]) == (False, False)


def test_a_control_thread_with_another_start_routine_does_not_count(judge):
    runs = base_runs()
    runs["control-boot"]["threads"]["rows"][1]["start_va"] = "0x007c5910"
    m, b = judge(runs)
    assert (m["ok"], b["moved"]) == (False, False)
