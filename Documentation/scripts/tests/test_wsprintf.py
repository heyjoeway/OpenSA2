"""The runtime's USER32!wvsprintfA, held to the oracle's user32 case by case.

`tools/s4/format_oracle.py` ran every case in `harness/format_cases.h` through
the oracle's own user32 -- Proton's -- and wrote what it produced to
`Documentation/receipts/s4-wsprintf-oracle-v1.json`. This builds
`harness/format_probe.c` over `runtime/sa2_format.c` and requires, for every
case, the same bytes, return value and extent of writing, except in the cases
the receipt names as the oracle's defects, where it requires the documented
output instead. It does not run Proton: the receipt is the oracle's answer,
and a receipt taken over a different case table is refused rather than read.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import format_oracle as FO  # noqa: E402

RECEIPT = json.loads(FO.RECEIPT.read_text())


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    if not FO.ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {FO.ZIG}")
    return FO.build_probe(tmp_path_factory.mktemp("format"))


def test_the_receipt_was_taken_over_this_case_table_and_driver():
    """What decides the oracle's answers is the case table and the program
    that hands them over; either changing makes the receipt another run's."""
    for path in ("harness/format_cases.h", "harness/format_oracle.c"):
        pinned = RECEIPT["inputs"]["pipeline"][path]
        now = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        assert pinned == now, (f"{path} changed since the oracle ran it; rerun "
                               ".venv/bin/python tools/s4/format_oracle.py --write")


def test_the_documented_expectations_are_the_tool_s():
    """A documented expectation is read from the receipt by the comparison, so
    it has to be the one the tool derives, not one edited into the receipt."""
    for c in RECEIPT["cases"]:
        if "documented" in c:
            assert c["documented"] == FO.documented(c["name"]), c["name"]


def test_the_runtime_writes_what_the_oracle_writes(probe):
    assert FO.mismatches(RECEIPT["cases"], FO.run_probe(probe)) == []


def test_the_image_s_own_formats_are_among_the_cases():
    names = {c["name"] for c in RECEIPT["cases"]}
    assert {"image-mblk", "image-ptr", "image-kinds", "image-name", "image-total"} <= names
    for c in RECEIPT["cases"]:
        if c["name"].startswith("image-"):
            assert "defect" not in c, "the formats SA2 passes must be matched, not excused"


def test_the_only_departures_are_the_named_defects():
    departed = sorted(c["name"] for c in RECEIPT["cases"] if "documented" in c)
    assert departed == sorted(FO.DEFECTS)
    for c in RECEIPT["cases"]:
        if "documented" in c:
            assert c["oracle"] != c["documented"], f"{c['name']}: no defect left to excuse"
            assert c["defect"] == FO.DEFECTS[c["name"]]["reason"]


def test_every_refusal_names_the_address_user32_would_fault_on(probe):
    rows = FO.run_probe(probe, "--faults")
    assert [r["fault_case"] for r in rows] == [r["case"] for r in RECEIPT["refusals"]]
    assert FO.judge_faults(rows) == []


def test_the_comparison_catches_a_changed_byte_and_a_changed_return(probe):
    rows = FO.run_probe(probe)
    for field, value in (("out", "00002: MBLK:0a1b2c3dH(0000001aH) \u0000"), ("ret", 34)):
        cases = copy.deepcopy(RECEIPT["cases"])
        mblk = next(c for c in cases if c["name"] == "image-mblk")
        mblk["oracle"][field] = value
        found = FO.mismatches(cases, rows)
        assert len(found) == 1 and found[0].startswith("image-mblk:"), found


def test_the_comparison_catches_a_defect_excused_wrongly(probe):
    """A documented expectation is a claim too: the runtime is held to it."""
    rows = FO.run_probe(probe)
    cases = copy.deepcopy(RECEIPT["cases"])
    dec = next(c for c in cases if c["name"] == "prefix-dec")
    dec["documented"] = dec["oracle"]
    found = FO.mismatches(cases, rows)
    assert len(found) == 1 and found[0].startswith("prefix-dec:"), found


def test_a_refusal_that_names_the_wrong_address_fails():
    rows = [{"fault_case": "string-outside", "status": -1, "fault": 0x2000, "expect": 0x1000,
             "written": ""},
            {"fault_case": "out-straddles", "status": -1, "fault": 5, "expect": 5,
             "written": "abcde"}]
    found = FO.judge_faults(rows)
    assert len(found) == 2 and found[0].startswith("string-outside") and "written" in found[1]
