#!/usr/bin/env python3
"""Behaviour tests for check_criterion_clauses.py.

The gate exists for two incidents, and the first two tests are those two
incidents, rebuilt from the checks that were actually committed.

  * **C2** paired the criterion "trap count recorded and every trap opcode
    listed" with a check that asserted only that the Traps row of `METRICS.md`
    did not read "not measured". `C2_CHECK` below is that check, recovered
    from `git show development_snapshot^:Documentation/gate-criteria.json`.
  * **D2** paired "M1 = 100% emitted in the game region" with a check that
    read the emitted ratio and ignored the instruction-level coverage
    companion an interrogation had added to the same row. `D2_CHECK` is that
    check, recovered from `git show development_snapshot^:Documentation/gate-criteria.json`.

Both tests take the criterion as it stands in the live
`Documentation/gate-criteria.json` -- its real clauses, its real falsifiers --
and put the old check back. The gate must fail, because the old check survives
a mutation the criterion says must break it. Written against the live file on
purpose: if someone weakens the clauses, these tests stop reproducing the
incident and say so.

Every test runs the real script as a subprocess with `--root` at the live
repository (the falsifiers name real evidence files) and `--criteria` at a
doctored copy under tmp_path. Nothing tracked is written.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parents[1]
GATE = SCRIPTS / "check_criterion_clauses.py"
CRITERIA = ROOT / "Documentation/gate-criteria.json"

sys.path.insert(0, str(SCRIPTS))
from check_criterion_clauses import shape  # noqa: E402

# The checks that were committed, verbatim.
C2_CHECK = (
    '{python} -c "import re,sys; t=open(\'METRICS.md\').read(); '
    "m=re.search(r'\\\\| Traps \\\\| ([^|]+)\\\\|', t); v=m.group(1).strip() if m else ''; "
    "print('Traps', v); sys.exit(0 if v and 'not measured' not in v else 1)\"")
D2_CHECK = (
    '{python} -c "import re,sys; t=open(\'METRICS.md\').read(); '
    "m=re.search(r'M1 emitted \\\\|[^|]*?game (\\\\d+)/(\\\\d+)', t); "
    "print('M1 game', m.group(0).split('|')[-1].strip() if m else 'absent'); "
    "sys.exit(0 if m and m.group(1)==m.group(2) else 1)\"")

TRAPS = "trap count recorded and every trap opcode listed (design 5, S3)"
M1_G3 = "M1 = 100% emitted in the game region (design 5, S3)"
M3_G2 = "M3 = 100% of fixtures passing (design 5, S2)"


def run(criteria: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(ROOT), "--criteria", str(criteria), *args],
        cwd=ROOT, capture_output=True, text=True)


def live() -> dict:
    return json.loads(CRITERIA.read_text(encoding="utf-8"))


def entry(document: dict, gate: str, criterion: str) -> dict:
    for item in document[gate]:
        if item["criterion"] == criterion:
            return item
    raise AssertionError(f"{criterion!r} is no longer a {gate} criterion")


def only(gate: str, item: dict, tmp_path: Path) -> Path:
    """A criteria file holding one gate and one criterion."""
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({"schema": "sa2-recomp-gate-criteria/v1", gate: [item]}))
    return path


# ------------------------------------------------------- the two incidents


def test_c2_the_traps_check_that_could_not_fail_on_its_opcode_half(tmp_path: Path) -> None:
    item = entry(live(), "G3", TRAPS)
    item["check"] = C2_CHECK
    result = run(only("G3", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "still PASSES" in result.stdout
    assert "every trap opcode listed" in result.stdout
    assert "no check that can fail" in result.stdout


def test_d2_the_m1_check_blind_to_the_companion_on_its_own_row(tmp_path: Path) -> None:
    item = entry(live(), "G3", M1_G3)
    item["check"] = D2_CHECK
    result = run(only("G3", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "still PASSES" in result.stdout
    assert "coverage companion" in result.stdout


def test_the_real_file_passes() -> None:
    result = run(CRITERIA)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "criterion clauses OK" in result.stdout
    assert result.stdout.strip().splitlines()[-1].startswith("marker_sha256=")


# ------------------------------------------------------- declaration rules


def test_c2_in_its_text_form_two_promises_and_one_clause(tmp_path: Path) -> None:
    item = entry(live(), "G3", TRAPS)
    item["clauses"] = item["clauses"][:1]
    result = run(only("G3", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "declares 1 clause(s) for text that promises 2" in result.stdout


def test_a_reinterpretation_without_a_criterion_note(tmp_path: Path) -> None:
    item = entry(live(), "G3", TRAPS)
    item.pop("criterion_note")
    result = run(only("G3", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "carries no 'criterion_note'" in result.stdout


def test_a_criterion_note_with_nothing_declared_as_reinterpreted(tmp_path: Path) -> None:
    item = entry(live(), "G2", M3_G2)
    item["criterion_note"] = " ".join(["the"] * 30)
    result = run(only("G2", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "no clause says what it is checked as" in result.stdout


def test_an_inline_check_that_declares_no_clauses(tmp_path: Path) -> None:
    item = entry(live(), "G2", M3_G2)
    item.pop("clauses")
    result = run(only("G2", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "declares no 'clauses'" in result.stdout


def test_a_promise_the_criterion_text_does_not_contain(tmp_path: Path) -> None:
    item = entry(live(), "G2", M3_G2)
    item["clauses"][0]["promise"] = "every fixture compared something"
    result = run(only("G2", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "is not in the criterion's own text" in result.stdout


def test_a_script_check_the_full_suite_does_not_run(tmp_path: Path) -> None:
    item = {"criterion": "some gate green",
            "check": "{python} Documentation/scripts/s1_sweep.py"}
    result = run(only("G0", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "the full gate suite does not run" in result.stdout


# ------------------------------------------------------- evidence rules


def test_a_falsifier_anchor_that_has_drifted(tmp_path: Path) -> None:
    item = entry(live(), "G2", M3_G2)
    item["clauses"][0]["falsifiers"][0]["replace"] = "M3 fixtures | 3/4 mangoes"
    result = run(only("G2", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "occurs 0 times" in result.stdout


def test_d2_as_the_gate_would_have_seen_it_a_row_that_gained_a_clause(
        tmp_path: Path) -> None:
    """The M1 row's shape pinned before the companion was added to it.

    This is D2 at the moment it happened: `METRICS.md` grows an
    instruction-level companion, no criterion is edited, and nothing has to
    notice. The pinned shape is computed here over the row with the companion
    removed -- using the criterion's own deletion falsifier, so the test
    cannot drift away from the row it is about.
    """
    document = live()
    item = entry(document, "G3", M1_G3)
    companion = next(
        falsifier["replace"]
        for clause in item["clauses"] if "checked_as" in clause
        for falsifier in clause["falsifiers"] if falsifier.get("with") == "")
    metrics = (ROOT / "METRICS.md").read_text(encoding="utf-8")
    assert companion in metrics
    rows = item["evidence_shape"][0]["rows"]
    before, _, lines = shape(metrics.replace(companion, ""), rows)
    assert lines == 1
    item["evidence_shape"][0]["shape_sha256"] = before
    result = run(only("G3", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "shape has changed" in result.stdout
    assert "This is the D2 event" in result.stdout


def test_a_number_moving_does_not_break_a_pinned_shape() -> None:
    """The other half of the shape rule: digits are normalised."""
    rows = ["| M1 emitted |"]
    metrics = (ROOT / "METRICS.md").read_text(encoding="utf-8")
    moved = metrics.replace("968324/971553 (99.7%)", "912345/971553 (93.9%)")
    assert moved != metrics
    assert shape(metrics, rows)[0] == shape(moved, rows)[0]


def test_a_pinned_row_that_matches_no_line(tmp_path: Path) -> None:
    item = entry(live(), "G2", M3_G2)
    item["evidence_shape"][0]["rows"] = ["| M3 fixtures renamed |"]
    result = run(only("G2", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "match no line" in result.stdout


def test_a_check_reading_a_file_with_no_shape_pinned(tmp_path: Path) -> None:
    item = entry(live(), "G2", M3_G2)
    item.pop("evidence_shape")
    result = run(only("G2", item, tmp_path))
    assert result.returncode == 1, result.stdout
    assert "pins no shape for it" in result.stdout


# ------------------------------------------------------- unmet criteria


def test_an_unmet_criterion_defers_its_falsifiers_with_a_note(tmp_path: Path) -> None:
    """A future gate's criteria are supposed to fail during the stage."""
    item = copy.deepcopy(entry(live(), "G2", M3_G2))
    item["criterion"] = "M3 = 100% of fixtures passing (design 5, S4)"
    item["check"] = ('{python} -c "import sys; t=open(\'METRICS.md\').read(); '
                     "print('M3 not yet'); sys.exit(1)\"")
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({"schema": "sa2-recomp-gate-criteria/v1", "G4": [item]}))
    result = run(path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "note:" in result.stdout
    assert "deferred" in result.stdout


@pytest.mark.parametrize("flag", ["--static-only"])
def test_static_only_says_so_in_its_summary(flag: str) -> None:
    result = run(CRITERIA, flag)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no check was run" in result.stdout


# ------------------------------------------- M5's one rule, in a state where M5 is met
#
# M5 is unmet while the boot replay diverges, so the gate defers its falsifiers
# with a note, and the identity clauses the user's rulings of 2026-09-24 added
# (Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md) would
# go unexercised until then. These build a root in which M5 is met -- the M5
# row clean, and the receipt holding a boot replay whose identity comparison
# holds under the tree's record and mask -- and run the gate on M5 there: every
# falsifier must then fail the check. The second is C2 for the rule: the check
# M5 had before, which read the row alone, survives every identity falsifier.

M5_OLD_CHECK = (
    "{python} -c \"import sys; sys.path.insert(0,'Documentation/scripts'); import stage_exit "
    "as s; r=s.PREDICATES['M5']('METRICS.md'); print('M5', r.detail); "
    "sys.exit(0 if r.met else 1)\"")
RECEIPT_REL = "Documentation/receipts/s4-tick-replay-v1.json"
IDENTITY_CLAUSES = [
    "every replay carries the comparison, in streams the numbering writes", "the pairing",
    "the open tick", "the close tick", "the words so compared are the record's",
    "the words so compared leave the digest mask"]
# The user's direction of 2026-09-26, decision 3's last sentence, as M5's last clause.
RESIDUE_CLAUSE = "a residue that changes a gameplay value is a bug"


def held_rule(T, g: dict) -> dict:
    """What the comparer records for a residue group (tick_replay.residue_groups) whose
    lifted values keep its rule and equal native, a native-relative one included (unit
    SF's boot groups and the host-thread-race class's): what residue_problems re-derives
    as held."""
    kind = g["rule"]["kind"]
    if kind in ("none", "zero"):
        return dict({"kind": kind, "held": True, "broken": None},
                    **({"nonzero_words": 0} if kind == "zero" else {}))
    if kind == "below":
        return {"kind": "below", "held": True, "broken": None, "limits": g["rule"]["limits"],
                "max": {a: "0x00000000" for a in g["rule"]["limits"]}}
    form = g["rule"]["form"]
    params = T.native_params(g["rule"], g["table"], g.get("effect"))
    observed = ({"words": [f"0x{va:08x}" for va in g["words"]], "apart_ticks": 0, "apart": []}
                if form == "one-tick-scan" else
                {**{k: {"ticks": 0, "first": None} for k in ("pre", "shape", "decode", "copy")},
                 "decode_context": {"lifted": {}, "native": {}},
                 "episodes": {"count": 0, "listed": [], "longest": None, "first_long": None}})
    if "uploads" in params:
        # the uploads its record's effect lists moved, and no other (the thread-race class)
        moved = sorted(params["uploads"]["moved"])
        observed["uploads"] = {
            "word": params["uploads"]["word"], "initial": ["0x00000000", "0x00000000"],
            "through": max((max(m[1], m[2]) for m in moved), default=1),
            "lifted": " ".join(f"{m[1]}:{m[0]}" for m in moved), "lifted_uploads": len(moved),
            "native": " ".join(f"{m[2]}:{m[0]}" for m in moved), "native_uploads": len(moved)}
    return {"kind": "native", "form": form, "params": params,
            "observed": observed, "held": True, "broken": None}


def m5_met_root(tmp_path: Path, extra: dict[str, str] | None = None) -> tuple[Path, dict]:
    """A root in which M5 is met, with `extra` files written over the tree's."""
    import re
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    import tick_replay as T
    from check_criterion_clauses import build_overlay
    registry = json.loads((ROOT / "Documentation/replays.json").read_text())
    fields = [n for n, _, _ in T.WL.named_fields(ROOT / "harness/replay/tick_fields.h")]
    events = ["1 g_01a557c0 0x00000000 0x00000001", "1 g_01af19f0 0x00000000 0x00000002",
              "380 h_01755254 0x00000000 0x00000001", "380 h_01755284 0x00000000 0x00000002",
              "381 h_01755220 0x00000000 0x00000001", "381 h_01755270 0x00000000 0x00000001"]
    # One clean identity comparison per registered word-policy replay, each under its
    # own mask (the boot, and Metal Harbor since U6), as the receipt records them, over
    # the ticks the tree's receipt judged it: M5's Metal-Harbor-only falsifiers anchor on
    # its identity columns ending before its judged ticks (the U6 registration review, W4).
    real = {}
    if (ROOT / RECEIPT_REL).is_file():
        real = {d.get("name"): ((d.get("result") or {}).get("identity") or {}).get("judged_ticks")
                for d in json.loads((ROOT / RECEIPT_REL).read_text()).get("replays", [])}
    docs = []
    for e in (x for x in registry["replays"] if "word_policy" in x):
        spec = T.identity_spec(e)
        columns = [n for n in fields if n in spec["named"]] + [c for c, _, _ in spec["extra"]]
        mask = T.mask_for(e)
        words = T.OM.read_mask_words(mask)
        record = {va for _, va, _ in spec["extra"]} | {0x01a557c0, 0x01af19f0}
        judged = real.get(e["name"]) or 1200
        # The residue classes the registry names (the user's direction of 2026-09-26):
        # the mask less their words too, the open ticks of the shifting columns held
        # within the bound the native repeats give, and their report, settled by the
        # comparer's own functions. The replay diverges at its last tick, so a residue
        # typed into the row for it is not its result.
        classes = T.residue_classes(e)
        residue = (words - record) & T.residue_words(classes)
        shifting = T.open_shift_columns(classes, spec)
        spec_shift = {"columns": shifting, "bound": 1} if shifting else None
        mask_info = {"mask": str(mask.relative_to(ROOT)), "mask_sha256": T.sha256(mask),
                     "identity_words_removed": len(words & record),
                     "words": len(words - record - residue)}
        if classes:
            mask_info["residue_words_removed"] = len(residue)
        # With shifting columns the open check records what its pairs were, so the
        # handle words' events are left out: a part blinded in the root's tick_replay.py
        # must leave this clean comparison what it records, or M5's control fails and
        # every falsifier is deferred rather than shown to depend on that part.
        evs = [e for e in events if not e.split()[1].startswith("h_")] if shifting else events
        ident = {"columns": columns, "named": spec["named"], "judged_ticks": judged,
                 "native_events": evs, "lifted_events": evs,
                 "checks": T.identity_checks(evs, evs, columns, spec["named"], judged,
                                             shift=spec_shift),
                 "record": spec["record"]}
        bug = {"tick": judged, "field": "frame_count", "kind": "value",
               "native": "0x00000001", "lifted": "0x00000002", "compared_as": "exact"}
        result = {"native_ticks": judged, "lifted_ticks": judged, "judged_ticks": judged,
                  "compared_ticks": judged, "first_divergence": bug, "residues_masked": [],
                  "reported": bug, "class": "bug", "identity": ident, "mask": mask_info}
        det = {"judged_ticks": judged, "repeats": []}
        if shifting:
            ident["open_shift"] = {"shifting": shifting, "bound": 1}
            det["repeats"] = [{"open_shift": {"pairs": 1, "shifted": 1, "widest": 1,
                                              "first_shifted": None}, "residues": []}]
        if classes:
            block = {"classes": [{"id": c["id"], "class": c["class"]} for c in classes],
                     "compared_ticks": judged,
                     "groups": [{"record": g["record"], "group": g["group"],
                                 "listed": len(g["words"]), "in_mask": 0,
                                 "rule": held_rule(T, g),
                                 "lifted": {"differing": 1, "first": None,
                                            "first_in_mask": None, "words": [],
                                            "listing_sha256": "0" * 64}}
                                for g in T.residue_groups(classes)]}
            if shifting:
                block["open_shift"] = dict(ident["checks"]["open"]["shift"],
                                           record=next(c["id"] for c in classes
                                                       if c["record"].get("identity_open_shift")))
            T.settle(result, block, det)
        docs.append({"name": e["name"], "native": {"determinism": det}, "result": result})
    receipt = json.dumps({"id": "s4-tick-replay-v1", "replays": docs}, indent=1,
                         sort_keys=True) + "\n"
    clean = "; ".join(f"{d['name']} no divergence/1200" for d in docs)
    metrics = re.sub(r"^\| M5 replays \|[^|]*\|", f"| M5 replays | {clean} |",
                     (ROOT / "METRICS.md").read_text(), count=1, flags=re.M)
    root = tmp_path / "root"
    build_overlay(ROOT, root, {"METRICS.md": metrics, RECEIPT_REL: receipt, **(extra or {})})
    item = copy.deepcopy(entry(live(), "G4", next(
        c["criterion"] for c in live()["G4"] if c["criterion"].startswith("M5:"))))
    for s in item["evidence_shape"]:
        s["shape_sha256"] = shape((root / s["file"]).read_text(), s["rows"])[0]
    return root, item


def test_m5_s_identity_clauses_each_fail_the_check_once_m5_is_met(tmp_path: Path) -> None:
    root, item = m5_met_root(tmp_path)
    identity = [c for c in item["clauses"] if "checked_as" in c]
    assert [c["promise"].split("names: ", 1)[-1] for c in identity] == IDENTITY_CLAUSES + [
        RESIDUE_CLAUSE]
    result = subprocess.run(
        [sys.executable, str(GATE), "--root", str(root),
         "--criteria", str(only("G4", item, tmp_path))],
        cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    falsifiers = sum(len(c["falsifiers"]) for c in item["clauses"])
    assert f"over {falsifiers} executed falsification(s)" in result.stdout, result.stdout
    assert "deferred" not in result.stdout


def test_c2_the_m5_check_that_read_the_row_alone_survives_the_identity_falsifiers(
        tmp_path: Path) -> None:
    root, item = m5_met_root(tmp_path)
    item["check"] = M5_OLD_CHECK
    result = subprocess.run(
        [sys.executable, str(GATE), "--root", str(root),
         "--criteria", str(only("G4", item, tmp_path))],
        cwd=root, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout
    for promise in IDENTITY_CLAUSES + [RESIDUE_CLAUSE]:
        assert promise in result.stdout and "still PASSES" in result.stdout, promise


# Each part of M5's identity half, blinded in the root's tick_replay.py: the
# gate must then fail, and on the falsifiers written for that part alone
# (clause, falsifier), clauses numbered as in the entry. The first take's
# falsifiers let the recompute, the numbering, the columns and the mask cut go
# blind with the gate still OK (the gate review of 2026-09-25, W1 to W3), and
# a failed open or a close to INVALID_HANDLE_VALUE was no event at all
# (the identity review, W2).
MASK_CUT = (
    '        if mask.get("mask") != str(want.relative_to(root)) \\\n'
    '                or mask.get("mask_sha256") != sha256(want) \\\n'
    '                or mask.get("identity_words_removed") != len(words & record) \\\n'
    '                or mask.get("residue_words_removed", 0 if not ids else None) != (\n'
    '                    len(cut) if ids else 0) \\\n'
    '                or mask.get("words") != len(words - record - residue):\n')
BLIND = {
    "the pairing's recompute": (
        '    out["pairing"] = {"ok": not bad_cells, "mismatches": len(bad_cells), '
        '"first": first}',
        '    out["pairing"] = {"ok": True, "mismatches": 0, "first": None}', {(3, 1)}),
    "the open's recompute": (
        '                opens.add((t, c, "" if b else now))', '                pass',
        {(4, 1), (4, 2)}),
    "a change between two nulls": (
        '                opens.add((t, c, "" if b else now))',
        '                opens.add((t, c, "")) if b else None', {(4, 2)}),
    "the close's recompute": (
        '            elif not b:\n                closes.add(', '            elif False:\n'
        '                closes.add(', {(5, 1), (5, 2)}),
    "the null a close goes to": (
        '                closes.add((t, c, now))', '                closes.add((t, c, ""))',
        {(5, 2)}),
    "the numbering": (
        '                out += [f"{name}: the {side} stream: {p}"\n'
        '                        for p in event_problems(ident.get(f"{side}_events") or [], '
        'named)]', '                pass', {(2, 1), (2, 2)}),
    "the comparison's presence": (
        '        if not isinstance(ident, dict):\n            out.append(',
        '        if not isinstance(ident, dict):\n            continue\n            out.append(',
        {(2, 0)}),
    "each check holding": (
        '            if not again[check]["ok"]:', '            if False:',
        # (8, 10) records the open check's verdict, failing, under the bound
        {(3, 0), (4, 0), (5, 0), (8, 10)}),
    "the record's sha256": (
        '        if rec.get("path") != IDENTITY_RECORD or rec.get("sha256") != '
        'sha256(root / IDENTITY_RECORD):', '        if False:', {(6, 0)}),
    # the columns and the mask cut each have a second falsifier since the U6 fold, the
    # one that edits Metal Harbor's comparison alone (the registration review, W4)
    "the columns": (
        '        if ident.get("columns") != order or named != spec["named"]:',
        '        if False:', {(6, 2), (6, 3)}),
    "the mask cut": (MASK_CUT, '        if False:\n', {(7, 0), (7, 1)}),
}
SURVIVED = re.compile(r"clause\[(\d+)\] \([^)]*\) falsifier\[(\d+)\]: the check still PASSES")


@pytest.mark.parametrize("part", sorted(BLIND))
def test_each_part_of_m5_s_identity_half_blinded_leaves_its_own_falsifiers_passing(
        tmp_path: Path, part: str) -> None:
    old, new, want = BLIND[part]
    source = (ROOT / "tools/s4/tick_replay.py").read_text()
    assert source.count(old) == 1, part
    root, item = m5_met_root(tmp_path, {"tools/s4/tick_replay.py": source.replace(old, new)})
    result = subprocess.run(
        [sys.executable, str(GATE), "--root", str(root),
         "--criteria", str(only("G4", item, tmp_path))],
        cwd=root, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout
    got = {(int(c), int(f)) for c, f in SURVIVED.findall(result.stdout)}
    assert got == want, result.stdout


# Each part of M5's residue clause (the user's direction of 2026-09-26), blinded in
# the root's tick_replay.py: the gate must then fail on that part's own falsifiers
# alone (clause 8, falsifier k). Falsifier 2 (frame_count, which both masks select)
# is also caught by the mask cut, so no blind leaves it passing, and so is falsifier 3
# (the input probe's goal latch) since Metal Harbor's mask came to select its word
# (2026-09-27: the pointer test no longer counts an image value as an address); 8
# records the open check's failing verdict, so a blind recompute still disagrees with it.
RESIDUE_BLIND = {
    "the gameplay words": (
        "            if touch:\n", "            if False:\n", {(8, 0), (8, 1), (8, 4)}),
    "the class": (
        '            if result.get("class") != want_cls:',
        '            if False:', {(8, 5)}),
    "the row held to the results": (
        "            if own != part:", "            if False:", {(8, 6), (8, 7)}),
    "the registry's classes": (
        "            if now != was:", "            if False:", {(8, 8)}),
    "a broken rule": (
        '                if b and (div is None or div["tick"] > b["tick"]):',
        '                if False:', {(8, 9)}),
    "the bound's distance": (
        "            if bound is not None and abs(tn - tl) > bound:",
        "            if False:", {(8, 11)}),
    "the bound's derivation": (
        '                if (shift or {}).get("bound") != widest or (spec or {}).get("bound") '
        '!= widest:', '                if False:', {(8, 12)}),
    # fold:rc and fold2:rc (unit SF's native-relative rule and the per-replay scope)
    "a rule's verdict re-derived": (
        "    if held is not want:", "    if False:", {(8, 13), (8, 17)}),
    # since fold:final the scope holds the race rule's parameters, so the nineteenth
    # falsifier (a record edit) is the scope's too; the twenty-ninth edits the receipt
    "the rule held to its record": (
        "                if not as_now:\n", "                if False:\n", {(8, 14), (8, 28)}),
    "the groups held to the records": (
        "            if [(g[\"record\"], g[\"name\"]) for g in own] != \\\n",
        "            if False and [(g[\"record\"], g[\"name\"]) for g in own] != \\\n",
        {(8, 15)}),
    "the scope's replays registered": (
        "                    if stray:\n", "                    if False:\n", {(8, 16)}),
    # the user's ruling of 2026-09-27: the host-thread-race class's scope and its effect.
    # Blind, a word, group, replay or routine added and an unlisted word of the four
    # contexts in host timing pass; the race's word in host timing is still caught as a
    # word two classes list, the Steam record made a thread-race record as that class
    # named on Metal Harbor, the class named on Metal Harbor by the result compared
    # without it, and a rule parameter or the effect edited in the record (the effect
    # taken away among them) by the rule held to its record. The G4 interrogation's
    # two (Reviewer B's finding 2), edits that keep the record consistent with itself
    # and with what the rule observed, pass blind: only the scope's pins refuse them; and
    # so does the thirty-fourth, the effect's registered capture renamed (the review of
    # the round-one fold, Reviewer A's note 3).
    "the thread race's scope": (
        "    out += thread_race_problems(rec, rid, root)\n", "    pass\n",
        {(8, 19), (8, 20), (8, 21), (8, 22), (8, 27), (8, 31), (8, 32), (8, 33)}),
    # fold:final (the race comparer review, finding 3; the race gate review, findings 3
    # and 10): the four contexts' whole span refused in another class, and the uploads
    # clause, each blinded alone
    "the contexts' span": ("        if span:\n", "        if False:\n", {(8, 27)}),
    "the uploads clause": (
        "            found.append(b)\n    return min(found,",
        "            pass\n    return min(found,", {(8, 29)}),
}


@pytest.mark.parametrize("part", sorted(RESIDUE_BLIND))
def test_each_part_of_m5_s_residue_clause_blinded_leaves_its_own_falsifiers_passing(
        tmp_path: Path, part: str) -> None:
    old, new, want = RESIDUE_BLIND[part]
    source = (ROOT / "tools/s4/tick_replay.py").read_text()
    assert source.count(old) == 1, part
    root, item = m5_met_root(tmp_path, {"tools/s4/tick_replay.py": source.replace(old, new)})
    assert item["clauses"][8]["promise"] == RESIDUE_CLAUSE
    result = subprocess.run(
        [sys.executable, str(GATE), "--root", str(root),
         "--criteria", str(only("G4", item, tmp_path))],
        cwd=root, capture_output=True, text=True)
    assert result.returncode == 1, result.stdout
    got = {(int(c), int(f)) for c, f in SURVIVED.findall(result.stdout)}
    assert got == want, result.stdout


def test_m5_s_note_quotes_the_direction_of_2026_09_26_in_full() -> None:
    """The repointing's record (rule 14) quotes decisions 2 and 3 as the decision
    record gives them, each sentence of the bullet."""
    record = ROOT / "Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md"
    text = " ".join(" ".join(line.lstrip("> ").strip().lstrip("- ").split())
                    for line in record.read_text().splitlines() if line.startswith(">"))
    note = next(c for c in live()["G4"] if c["criterion"].startswith("M5:"))["criterion_note"]
    for start, end in (("Decision 2: B.", "the exact words and the reason."),
                       ("Decision 3: host-timing", "is a bug.")):
        quoted = text[text.index(start):text.index(end, text.index(start)) + len(end)]
        assert f'"{quoted}' in note, quoted


def test_m5_s_note_quotes_the_ruling_of_2026_09_27_in_full() -> None:
    """The host-thread-race class's repointing (rule 14): M5's note quotes the user's
    ruling on the Sofdec decoder's race as the decision record gives it, the option and
    all three conditions, and names the class's record."""
    record = ROOT / "Documentation/decisions/2026-09-27-S4-sofdec-thread-race-residue.md"
    text = " ".join(" ".join(line.lstrip("> ").strip().lstrip("- ").split())
                    for line in record.read_text().splitlines() if line.startswith(">"))
    start, end = "B, with three conditions:", "weighed once for all of them."
    quoted = text[text.index(start):text.index(end) + len(end)]
    note = next(c for c in live()["G4"] if c["criterion"].startswith("M5:"))["criterion_note"]
    assert f'"{quoted}"' in note, quoted
    assert "Documentation/receipts/residues/host-thread-race-sofdec-v1.json" in note


def test_m5_s_note_quotes_the_rulings_it_rests_on_in_full() -> None:
    """The note is the record of the repointing (rule 14): it quotes rulings 1
    and 2 as the decision record gives them, every sentence (the gate review of
    2026-09-25, N1: ruling 1's last sentence was left out, and the note then
    said the condition it sets was met)."""
    record = (ROOT / "Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md")
    quoted = " ".join(line[2:].strip() for line in record.read_text().splitlines()
                      if line.startswith("> "))
    rulings = re.findall(r"(?:^| )([12]: .*?)(?= [1-9]: |$)", quoted)
    assert [r[:2] for r in rulings] == ["1:", "2:"]
    note = next(c for c in live()["G4"] if c["criterion"].startswith("M5:"))["criterion_note"]
    for ruling in rulings:
        assert f'"{ruling}"' in note, ruling


# ------------------------------------------------------- a declared met state
#
# An unmet criterion defers its falsifiers; a declared met state lets them run
# now. These use M3's real clauses on a root whose M3 row reads 'failed 3', so
# the criterion is unmet there, and a met state that writes 'failed 0' back.

M3_ROW_MET = "(100.0%); failed 0; regressions 0"
M3_ROW_UNMET = "(100.0%); failed 3; regressions 0"
MET_NOTE = ("The M3 row as it reads with every fixture passing, which is what the real "
            "file holds; the test root breaks it so the criterion is unmet there.")


def m3_unmet_root(tmp_path: Path) -> Path:
    from check_criterion_clauses import build_overlay
    text = (ROOT / "METRICS.md").read_text()
    assert text.count(M3_ROW_MET) == 1
    root = tmp_path / "root"
    build_overlay(ROOT, root, {"METRICS.md": text.replace(M3_ROW_MET, M3_ROW_UNMET)})
    return root


def run_at(root: Path, item: dict, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), "--root", str(root),
         "--criteria", str(only("G2", item, tmp_path))],
        cwd=root, capture_output=True, text=True)


def m3_with_met_state(**kw) -> dict:
    item = copy.deepcopy(entry(live(), "G2", M3_G2))
    item["met_state"] = kw.get("subs", [{"file": "METRICS.md", "replace": M3_ROW_UNMET,
                                         "with": M3_ROW_MET}])
    if kw.get("note", MET_NOTE) is not None:
        item["met_state_note"] = kw.get("note", MET_NOTE)
    return item


def test_a_met_state_lets_an_unmet_criterion_s_falsifiers_run(tmp_path: Path) -> None:
    root = m3_unmet_root(tmp_path)
    result = run_at(root, m3_with_met_state(), tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "falsified on its declared met state" in result.stdout
    assert "over 3 executed falsification(s)" in result.stdout
    assert "3 of the executed on a declared met state" in result.stdout


def test_a_falsifier_that_survives_on_the_met_state_fails_the_gate(tmp_path: Path) -> None:
    item = m3_with_met_state()
    item["clauses"][0]["falsifiers"].append(
        {"file": "METRICS.md", "replace": "; regressions 0", "with": "; regressions 0 "})
    result = run_at(m3_unmet_root(tmp_path), item, tmp_path)
    assert result.returncode == 1, result.stdout
    assert "still PASSES" in result.stdout and "on its declared met state" in result.stdout


def test_a_met_state_the_check_still_fails_on_defers_the_falsifiers(tmp_path: Path) -> None:
    """The real evidence is what keeps such a check from passing: deferred, as an
    unmet criterion without a met state is (its falsifiers' anchors still held
    to the real file)."""
    item = m3_with_met_state(subs=[{"file": "METRICS.md", "replace": "| M3 fixtures |",
                                    "with": "| M3 fixtures  |"}])
    item["criterion"] = "M3 = 100% of fixtures passing (design 5, S4)"
    item["check"] = ('{python} -c "import sys; t=open(\'METRICS.md\').read(); '
                     "print('M3 not yet'); sys.exit(1)\"")
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({"schema": "sa2-recomp-gate-criteria/v1", "G4": [item]}))
    result = run(path)
    assert result.returncode == 0, result.stdout
    assert "fails on its declared met state too" in result.stdout and "deferred" in result.stdout


@pytest.mark.parametrize("kw,why", [
    ({"note": None}, "met_state_note"),
    ({"note": "too short"}, "met_state_note"),
    ({"subs": [{"file": "METRICS.md", "absent": True}]}, "leaves a file out"),
    ({"subs": []}, "not a non-empty list"),
])
def test_a_met_state_declared_wrong_is_refused(tmp_path: Path, kw: dict, why: str) -> None:
    result = run_at(m3_unmet_root(tmp_path), m3_with_met_state(**kw), tmp_path)
    assert result.returncode == 1, result.stdout
    assert why in result.stdout
    static = subprocess.run(
        [sys.executable, str(GATE), "--root", str(ROOT), "--static-only",
         "--criteria", str(only("G2", m3_with_met_state(**kw), tmp_path))],
        cwd=ROOT, capture_output=True, text=True)
    assert static.returncode == 1 and why in static.stdout, static.stdout


def test_a_falsifier_on_another_file_runs_on_top_of_the_met_state(tmp_path: Path) -> None:
    """M5's case: the met state rewrites METRICS.md and the identity falsifiers
    mutate other files, so each falsifier's overlay must carry the met state
    too. Here a falsifier on the registry that changes nothing the check reads
    must survive -- and be reported -- which it can only do on the met state:
    without it the check fails for the unmet row, and the falsifier would read
    as caught."""
    root = m3_unmet_root(tmp_path)
    check = ("{python} -c \"import sys; t=open('METRICS.md').read(); "
             "r=open('Documentation/replays.json').read(); "
             "ok=('failed 0;' in t) and ('\\\"boot\\\"' in r); print('M3', ok); "
             "sys.exit(0 if ok else 1)\"")
    item = m3_with_met_state()
    item["check"] = check
    item["clauses"] = [{"promise": "M3 = 100% of fixtures passing", "falsifiers": [
        {"file": "METRICS.md", "replace": "(100.0%); failed 0;", "with": "(100.0%); failed 1;"},
        {"file": "Documentation/replays.json", "replace": '"name": "boot"',
         "with": '"name": "boat"'},
        {"file": "Documentation/replays.json", "replace": '"tool": ', "with": '"tool":  '}]}]
    item["evidence_shape"] = [
        {"file": f, "rows": [row], "shape_sha256": shape((root / f).read_text(), [row])[0]}
        for f, row in (("METRICS.md", "| M3 fixtures |"),
                       ("Documentation/replays.json", '"name": "boot"'))]
    result = run_at(root, item, tmp_path)
    assert result.returncode == 1, result.stdout
    survivors = [ln for ln in result.stdout.splitlines() if "still PASSES" in ln]
    assert len(survivors) == 1 and "falsifier[2]" in survivors[0], result.stdout
    assert "on its declared met state" in survivors[0]


def test_a_script_check_declaring_a_met_state_is_refused(tmp_path: Path) -> None:
    item = {"criterion": "all gates green on an empty project",
            "check": "{python} Documentation/scripts/run_all_gates.py",
            "met_state": [{"file": "METRICS.md", "replace": "| M3 fixtures |",
                           "with": "| M3 fixtures  |"}], "met_state_note": MET_NOTE}
    path = tmp_path / "gate-criteria.json"
    path.write_text(json.dumps({"schema": "sa2-recomp-gate-criteria/v1", "G0": [item]}))
    result = run(path, "--static-only")
    assert result.returncode == 1 and "is a script check and declares a met state" in result.stdout
