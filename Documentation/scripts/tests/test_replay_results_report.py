"""What check_replay_results prints when it fails.

The gate once printed only its surviving falsifications and returned before
reading the live record's problems. When the live record already failed, every
mutation could survive for that reason alone, and the problems that explained
the survivors were never printed (the P5 gate fold's review, C3).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_replay_results as R  # noqa: E402


def test_the_live_problems_are_printed_beside_the_survivors(monkeypatch, capsys) -> None:
    monkeypatch.setattr(R, "falsifications", lambda: ["falsification survived: x"])
    monkeypatch.setattr(R, "problems", lambda: ["replay boot is stale", "falsification survived: x"])
    assert R.main() == 1
    assert capsys.readouterr().out.splitlines() == [
        "  falsification survived: x",
        "  replay boot is stale",
        "REPLAY_RESULTS_FAILED falsifications=1 problems=2"]


def test_problems_alone_still_fail(monkeypatch, capsys) -> None:
    monkeypatch.setattr(R, "falsifications", lambda: [])
    monkeypatch.setattr(R, "problems", lambda: ["replay boot is stale"])
    assert R.main() == 1
    assert capsys.readouterr().out.splitlines()[-1] == \
        "REPLAY_RESULTS_FAILED falsifications=0 problems=1"


needs_records = pytest.mark.skipif(
    not (R.RECEIPT.is_file() and R.RESULTS.is_file() and R.IMPORT_STATUS.is_file()),
    reason="the replay receipt, M5's input or M4's import status is not on this machine")


@pytest.fixture(scope="module", autouse=True)
def one_gate_run():
    """The record tests below each run the gate's falsifications twice, with a check
    blinded; the recomputed comparisons do not read the checks they blind (problems,
    native_start, lifted_guest), so the module keeps them as one gate run does
    (check_replay_results.kept). Each call was a whole gate pass: six of them took
    hours once Metal Harbor registered (the U6 MANIFEST)."""
    with R.kept():
        yield


@needs_records
def test_a_survivor_says_when_the_unmutated_record_already_fails(monkeypatch) -> None:
    # A gate that sees nothing: every case survives. Against a record with a
    # problem of its own, each survivor says so; against a clean one, none does.
    monkeypatch.setattr(R, "problems", lambda *a, **k: ["the live record's own problem"])
    got = R.falsifications()
    assert got and all(g.startswith("falsification survived: ") and g.endswith(
        "; the unmutated record already fails with 1 problem(s), and the mutation added none")
        for g in got), got
    monkeypatch.setattr(R, "problems", lambda *a, **k: [])
    got = R.falsifications()
    assert got and not any("already fails" in g for g in got), got


@needs_records
def test_a_blind_native_start_leaves_the_saves_case_surviving(monkeypatch) -> None:
    """The saves case is caught only by a problem that names the saves: with
    native_start blinded, another check firing on the moved receipt must not
    count as catching it (the P6 fold review, W1)."""
    import tick_replay as T
    name = "a registered capture did not start from the pinned saves"
    got = R.falsifications()
    assert not any(name in g for g in got), got
    monkeypatch.setattr(T, "native_start", lambda *a, **k: None)
    got = R.falsifications()
    assert any(name in g for g in got), got


@needs_records
def test_a_blind_lifted_guest_leaves_the_directory_case_surviving(monkeypatch) -> None:
    """The directory case is caught only by a problem that names the guest
    directory: with lifted_guest blinded, it survives (the working-directory
    review, W1)."""
    import tick_replay as T
    name = "the recorded lifted run ran in another guest directory"
    got = R.falsifications()
    assert not any(name in g for g in got), got
    monkeypatch.setattr(T, "lifted_guest", lambda *a, **k: None)
    got = R.falsifications()
    assert any(name in g for g in got), got
