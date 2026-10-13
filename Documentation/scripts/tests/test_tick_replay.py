"""Harness level 2's comparison, made to fail one way at a time.

`tools/s4/tick_replay.py` decides M5's row, and M5 is one of the three
criteria that decide whether S4 is finished. Every assertion that a comparison
agrees is paired with a mutation that must make it disagree, because a
comparer that agrees with everything passes every agreement test ever written.

The 2026-09-22 review ran five mutants of the first version against the first
version of this file and every one survived: a residue label that ignored the
field, a run end that overrode an earlier value divergence, null not kept as
null, the loosest repeat winning instead of the tightest, and repeats compared
without the field policy. Each has a test below that fails on it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import tick_replay as T  # noqa: E402

HEADER = ["tick", "frame", "mode", "ptr"]
EVIDENCE = {"ptr": {"compare": "identity", "evidence": "a heap pointer: test fixture"}}
HEAP = 0x04001cd0          # inside runtime/guest_memory.h's guest heap
WINE = 0x03912860          # where Wine put the native object


def rows(*values):
    """rows((1, 2, 0x40), ...) -> [(1, ['0x00000001', ...]), ...] numbered from tick 1."""
    return [(k, [f"0x{v:08x}" for v in vs]) for k, vs in enumerate(values, 1)]


def cmp(tmp_path, native, lifted, policy=None, labels=(), judged=None, residues=None,
        header=HEADER):
    return T.compare(header, native, header, lifted, dict(policy or {}), list(labels),
                     tmp_path, judged=judged, residues=residues or tmp_path, name="t")


NATIVE = rows((1, 2, WINE), (2, 13, WINE), (3, 22, WINE), (4, 22, WINE))
AT3 = rows((1, 2, WINE), (2, 13, WINE), (3, 21, WINE), (4, 22, WINE))     # mode differs at 3


def residue(tmp_path, rid, **observed):
    (tmp_path / f"{rid}.json").write_text(json.dumps({"observed_in": [observed]}))


# ---------------------------------------------------------------- agreement

def test_identical_streams_have_no_divergence(tmp_path):
    r = cmp(tmp_path, NATIVE, NATIVE)
    assert r["first_divergence"] is None and r["class"] is None and r["reported"] is None
    assert r["compared_ticks"] == 4 and r["judged_ticks"] == 4


def test_a_value_difference_is_reported_at_its_tick_and_field(tmp_path):
    r = cmp(tmp_path, NATIVE, AT3)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["kind"]) == (3, "mode", "value")
    assert (d["native"], d["lifted"]) == ("0x00000016", "0x00000015")
    assert r["class"] == "bug" and r["reported"] == d


def test_the_earliest_tick_wins_over_the_earliest_column(tmp_path):
    lifted = rows((1, 2, WINE), (2, 99, WINE), (9, 22, WINE), (4, 22, WINE))
    d = cmp(tmp_path, NATIVE, lifted)["first_divergence"]
    assert (d["tick"], d["field"]) == (2, "mode")


# --------------------------------------------------------- identity fields

def test_an_identity_field_ignores_where_the_allocator_put_it(tmp_path):
    lifted = rows((1, 2, HEAP), (2, 13, HEAP), (3, 22, HEAP), (4, 22, HEAP))
    assert cmp(tmp_path, NATIVE, lifted, EVIDENCE)["first_divergence"] is None


def test_without_the_policy_the_same_streams_diverge_at_tick_one(tmp_path):
    """The control for the test above: the policy is what suppresses the raw
    difference, not a comparer that stopped looking at the column."""
    lifted = rows((1, 2, HEAP), (2, 13, HEAP), (3, 22, HEAP), (4, 22, HEAP))
    d = cmp(tmp_path, NATIVE, lifted)["first_divergence"]
    assert (d["tick"], d["field"]) == (1, "ptr")


def test_an_identity_field_still_diverges_when_one_side_reallocates(tmp_path):
    lifted = rows((1, 2, HEAP), (2, 13, HEAP), (3, 22, HEAP + 0x40), (4, 22, HEAP + 0x40))
    d = cmp(tmp_path, NATIVE, lifted, EVIDENCE)["first_divergence"]
    assert (d["tick"], d["field"], d["compared_as"]) == (3, "ptr", "identity")
    assert (d["native"], d["lifted"]) == (f"0x{WINE:08x}", f"0x{HEAP + 0x40:08x}")


def test_null_is_not_an_allocation(tmp_path):
    """Native null then a pointer, lifted a pointer then null. Numbered without
    a special case for null the two streams both read 1, 2, 2 and agree."""
    native = rows((1, 2, 0), (2, 13, WINE), (3, 22, WINE))
    lifted = rows((1, 2, HEAP), (2, 13, 0), (3, 22, 0))
    d = cmp(tmp_path, native, lifted, EVIDENCE)["first_divergence"]
    assert (d["tick"], d["field"]) == (1, "ptr")


def test_a_lifted_pointer_outside_the_guest_heap_never_matches(tmp_path):
    """A constant garbage pointer numbers as 1 exactly like a real one."""
    lifted = rows((1, 2, 0xDEADBEEF), (2, 13, 0xDEADBEEF), (3, 22, 0xDEADBEEF), (4, 22, 0xDEADBEEF))
    d = cmp(tmp_path, NATIVE, lifted, EVIDENCE)["first_divergence"]
    assert (d["tick"], d["field"], d["lifted"]) == (1, "ptr", "0xdeadbeef")


def test_two_identity_fields_that_alias_natively_must_alias_lifted(tmp_path):
    header = ["tick", "a", "b"]
    policy = {"a": {"compare": "identity", "evidence": "x"},
              "b": {"compare": "identity", "evidence": "x"}}
    native = rows((WINE, WINE), (WINE, WINE))
    lifted = rows((HEAP, HEAP + 0x40), (HEAP, HEAP + 0x40))
    d = cmp(tmp_path, native, lifted, policy, header=header)["first_divergence"]
    assert (d["tick"], d["field"]) == (1, "b")


# -------------------------------------------------------------- run ends

def test_a_lifted_run_that_stops_early_diverges_where_it_stopped(tmp_path):
    r = cmp(tmp_path, NATIVE, NATIVE[:2])
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["kind"]) == (3, T.RUN_ENDED, "lifted_run_ended")
    assert r["class"] == "bug" and r["compared_ticks"] == 2


BUDGET_STOP = ("the import-call budget of 400000 was reached at KERNEL32.dll!HeapAlloc from "
               "0x007a851e: this is SA2_IMPORT_BUDGET stopping the run at the same call in "
               "every run, not a wall")


def test_a_run_the_harness_budget_stopped_is_bounded_not_a_bug(tmp_path):
    """The build did not fail at tick 3; the harness stopped it. Still unmet."""
    r = T.compare(HEADER, NATIVE, HEADER, NATIVE[:2], {}, [], tmp_path, residues=tmp_path,
                  lifted_stop=BUDGET_STOP)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["kind"]) == (3, T.BOUNDED, "lifted_run_bounded")
    assert r["class"] == "bounded"


def test_a_trap_is_still_a_run_that_ended(tmp_path):
    """The control: any other stop note is the build failing."""
    r = T.compare(HEADER, NATIVE, HEADER, NATIVE[:2], {}, [], tmp_path, residues=tmp_path,
                  lifted_stop="read of 2 byte(s) at guest 0000000000 is outside the guest space")
    assert (r["first_divergence"]["kind"], r["class"]) == ("lifted_run_ended", "bug")


def test_the_host_s_wm_quit_at_the_poll_budget_is_bounded_not_a_bug(tmp_path):
    """The second leash: the host posts WM_QUIT at SA2_PUMP_POLLS and the guest
    leaves through its own exit path, whatever that path then meets."""
    run = {"pump": {"quit_by_host": 1, "poll_limit": 600},
           "trap": {"note": "no lifted block at 0x007a5929 (region .text)"}}
    note = T.stop_note(run)
    assert note.startswith(T.QUIT_NOTE) and "600" in note and "0x007a5929" in note
    r = T.compare(HEADER, NATIVE, HEADER, NATIVE[:2], {}, [], tmp_path, residues=tmp_path,
                  lifted_stop=note)
    assert (r["first_divergence"]["kind"], r["class"]) == ("lifted_run_bounded", "bounded")


def test_without_the_host_s_quit_the_trap_is_the_stop():
    run = {"pump": {"quit_by_host": 0, "poll_limit": 600}, "trap": {"note": "a trap"}}
    assert T.stop_note(run) == "a trap"


def test_the_reach_configuration_outlasts_every_retained_capture():
    """The replay refuses otherwise; this is the configuration it runs."""
    import json
    reg = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    for e in reg["replays"]:
        cap = ROOT / e["native"]["capture"]
        if cap.is_file():
            assert T.poll_budget(e) > len(T.read_capture(cap)[1]), e["name"]


def test_a_budget_stop_does_not_excuse_an_earlier_value_divergence(tmp_path):
    lifted = rows((1, 2, WINE), (2, 99, WINE))
    r = T.compare(HEADER, NATIVE, HEADER, lifted, {}, [], tmp_path, residues=tmp_path,
                  lifted_stop=BUDGET_STOP)
    assert (r["first_divergence"]["kind"], r["class"]) == ("value", "bug")


def test_an_earlier_value_divergence_beats_a_later_run_end(tmp_path):
    lifted = rows((1, 2, WINE), (2, 99, WINE))
    d = cmp(tmp_path, NATIVE, lifted)["first_divergence"]
    assert (d["tick"], d["kind"]) == (2, "value")


def test_an_empty_lifted_capture_ended_at_tick_one(tmp_path):
    d = cmp(tmp_path, NATIVE, [])["first_divergence"]
    assert (d["tick"], d["kind"]) == (1, "lifted_run_ended")


def test_a_lifted_run_that_outlasts_the_capture_is_end_of_capture(tmp_path):
    longer = NATIVE + [(5, ["0x00000005", "0x00000016", f"0x{WINE:08x}"])]
    r = cmp(tmp_path, NATIVE, longer)
    assert r["first_divergence"] is None
    assert r["judged_ticks"] == 4 and r["lifted_ticks"] == 5


# ------------------------------------------------------- the oracle's limit

def test_ticks_no_repeat_reproduces_are_unrepeatable_not_passed(tmp_path):
    """Judged over two ticks of four, a lifted run that stops after two must
    not read as agreeing with the capture."""
    r = cmp(tmp_path, NATIVE, NATIVE[:2], judged=2)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["kind"]) == (3, T.UNREPEATED, "native_unrepeated")
    assert r["class"] == "unrepeatable"
    assert T.m5_record({"name": "b", "result": r})["ticks"] == 4


def test_a_difference_past_the_judged_ticks_is_not_reported_as_the_lifted_builds(tmp_path):
    r = cmp(tmp_path, NATIVE, AT3, judged=2)
    assert r["first_divergence"]["kind"] == "native_unrepeated"
    assert cmp(tmp_path, NATIVE, AT3, judged=3)["first_divergence"]["kind"] == "value"


def repeat_entry(tmp_path, *repeats, policy=None):
    """Each repeat in a directory of its own, as each native run is."""
    reps = []
    for k, stream in enumerate(repeats):
        p = tmp_path / f"rep{k}" / "sa2-tick-capture.csv"
        p.parent.mkdir(parents=True, exist_ok=True)
        T.write_capture(p, HEADER, stream)
        reps.append({"capture": str(p), "sha256": T.sha256(p)})
    return {"name": "x", "field_policy": dict(policy or {}),
            "native": {"capture": "c.csv", "sha256": "", "repeats": reps}}


def test_a_repeat_in_the_captures_directory_is_one_run_listed_twice(tmp_path):
    """A repeat that is the registered capture again reads as a repeat agreeing
    on every tick, byte for byte: determinism shown by one run (U9's second
    review, W2). Refused, as a repeat listed twice is."""
    e = repeat_entry(tmp_path, NATIVE)
    e["native"]["capture"] = e["native"]["repeats"][0]["capture"]
    with pytest.raises(SystemExit, match="in the directory of the registered capture"):
        T.native_repeats(e, HEADER, NATIVE, tmp_path)
    e = repeat_entry(tmp_path, NATIVE)
    e["native"]["repeats"].append(dict(e["native"]["repeats"][0]))
    with pytest.raises(SystemExit, match="repeat 1 .* is in the directory of repeat 0"):
        T.native_repeats(e, HEADER, NATIVE, tmp_path)
    # "a/../a" is "a": the rule compares directories, not spellings.
    e = repeat_entry(tmp_path, NATIVE)
    rep = e["native"]["repeats"][0]["capture"]
    e["native"]["repeats"].append({"capture": str(Path(rep).parent / ".." / "rep0" / "x.csv"),
                                   "sha256": ""})
    with pytest.raises(SystemExit, match="in the directory of repeat 0"):
        T.native_repeats(e, HEADER, NATIVE, tmp_path)


def test_the_registered_boot_entry_is_one_run_per_directory():
    """Boot's capture and each repeat (the runs its own mask was chosen from and
    checked on, U6) sit in directories of their own, so the rule leaves the
    registered replay as it was."""
    boot = next(e for e in json.loads(T.REGISTRY.read_text())["replays"] if e["name"] == "boot")
    caps = [boot["native"]["capture"]] + [r["capture"] for r in boot["native"]["repeats"]]
    assert len({T.run_dir(c) for c in caps}) == len(caps) >= 3


def test_a_replay_without_a_native_repeat_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="no native repeat"):
        T.native_repeats(repeat_entry(tmp_path), HEADER, NATIVE, tmp_path)


def test_a_repeat_that_agrees_judges_every_tick(tmp_path):
    e = repeat_entry(tmp_path, NATIVE)
    assert T.native_repeats(e, HEADER, NATIVE, tmp_path)["judged_ticks"] == 4


def test_the_tightest_repeat_sets_the_judged_ticks(tmp_path):
    late = rows((1, 2, WINE), (2, 13, WINE), (3, 22, WINE), (4, 7, WINE))    # differs at 4
    early = rows((1, 2, WINE), (2, 7, WINE), (3, 22, WINE), (4, 22, WINE))   # differs at 2
    e = repeat_entry(tmp_path, late, early)
    assert T.native_repeats(e, HEADER, NATIVE, tmp_path)["judged_ticks"] == 1


def test_a_shorter_repeat_caps_the_judged_ticks(tmp_path):
    e = repeat_entry(tmp_path, NATIVE[:3])
    assert T.native_repeats(e, HEADER, NATIVE, tmp_path)["judged_ticks"] == 3


def test_repeats_are_compared_under_the_field_policy(tmp_path):
    moved = rows((1, 2, WINE + 8), (2, 13, WINE + 8), (3, 22, WINE + 8), (4, 22, WINE + 8))
    assert T.native_repeats(repeat_entry(tmp_path, moved, policy=EVIDENCE),
                            HEADER, NATIVE, tmp_path)["judged_ticks"] == 4
    assert T.native_repeats(repeat_entry(tmp_path, moved),
                            HEADER, NATIVE, tmp_path)["judged_ticks"] == 0


def test_a_repeat_that_does_not_hash_to_its_pin_is_refused(tmp_path):
    e = repeat_entry(tmp_path, NATIVE)
    e["native"]["repeats"][0]["sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="pin"):
        T.native_repeats(e, HEADER, NATIVE, tmp_path)


# ------------------------------------------------------------------ residues

def test_a_residue_label_needs_a_record_that_carries_the_observation(tmp_path):
    label = [{"tick": 3, "field": "mode", "residue": "fsin-tail-v1"}]
    with pytest.raises(SystemExit, match="no record"):
        cmp(tmp_path, NATIVE, AT3, labels=label)
    (tmp_path / "fsin-tail-v1.json").write_text("{}")
    with pytest.raises(SystemExit, match="observed_in"):
        cmp(tmp_path, NATIVE, AT3, labels=label)
    residue(tmp_path, "fsin-tail-v1", replay="t", tick=3, field="mode",
            native="0x00000016", lifted="0x00000015")
    r = cmp(tmp_path, NATIVE, AT3, labels=label)
    assert r["class"] == "residue fsin-tail-v1" and r["first_divergence"] is None
    assert r["reported"]["tick"] == 3


def test_a_label_for_another_field_at_the_same_tick_does_not_apply(tmp_path):
    residue(tmp_path, "fsin-tail-v1", replay="t", tick=3, field="frame",
            native="0x00000016", lifted="0x00000015")
    label = [{"tick": 3, "field": "frame", "residue": "fsin-tail-v1"}]
    assert cmp(tmp_path, NATIVE, AT3, labels=label)["class"] == "bug"


def test_a_label_for_another_tick_does_not_apply(tmp_path):
    residue(tmp_path, "fsin-tail-v1", replay="t", tick=4, field="mode",
            native="0x00000016", lifted="0x00000015")
    label = [{"tick": 4, "field": "mode", "residue": "fsin-tail-v1"}]
    assert cmp(tmp_path, NATIVE, AT3, labels=label)["class"] == "bug"


def test_a_crash_is_never_a_residue(tmp_path):
    (tmp_path / "pc53-double-rounding-v1.json").write_text("{}")
    label = [{"tick": 3, "field": T.RUN_ENDED, "residue": "pc53-double-rounding-v1"}]
    with pytest.raises(SystemExit, match="never one"):
        cmp(tmp_path, NATIVE, NATIVE[:2], labels=label)


def test_a_residue_does_not_hide_a_later_crash(tmp_path):
    residue(tmp_path, "fsin-tail-v1", replay="t", tick=2, field="mode",
            native="0x0000000d", lifted="0x0000000e")
    lifted = rows((1, 2, WINE), (2, 14, WINE))
    label = [{"tick": 2, "field": "mode", "residue": "fsin-tail-v1"}]
    r = cmp(tmp_path, NATIVE, lifted, labels=label)
    assert r["first_divergence"]["kind"] == "lifted_run_ended" and r["class"] == "bug"
    assert [m["tick"] for m in r["residues_masked"]] == [2]


def test_a_residue_does_not_hide_a_later_value_divergence(tmp_path):
    residue(tmp_path, "fsin-tail-v1", replay="t", tick=2, field="mode",
            native="0x0000000d", lifted="0x0000000e")
    lifted = rows((1, 2, WINE), (2, 14, WINE), (3, 22, WINE), (5, 22, WINE))
    label = [{"tick": 2, "field": "mode", "residue": "fsin-tail-v1"}]
    d = cmp(tmp_path, NATIVE, lifted, labels=label)["first_divergence"]
    assert (d["tick"], d["field"]) == (4, "frame")


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("policy,match", [
    ({"ptr": {"compare": "identity"}}, "needs evidence"),
    ({"ptr": {"compare": "identity", "evidence": "  "}}, "needs evidence"),
    ({"ptr": {"compare": "ignore", "evidence": "x"}}, "not one of"),
    ({"nope": {"compare": "identity", "evidence": "x"}}, "does not have"),
])
def test_a_policy_is_refused_without_its_warrant(tmp_path, policy, match):
    with pytest.raises(SystemExit, match=match):
        cmp(tmp_path, NATIVE, NATIVE, policy)


def test_different_fields_are_refused(tmp_path):
    with pytest.raises(SystemExit, match="fields differ"):
        T.compare(HEADER, NATIVE, ["tick", "frame", "mode", "other"], NATIVE, {}, [], tmp_path,
                  residues=tmp_path)


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_read_capture_refuses_a_gap_in_the_ticks(tmp_path):
    with pytest.raises(SystemExit, match="ticks must run"):
        T.read_capture(write(tmp_path / "c.csv", "tick,a\n1,0x00000001\n3,0x00000003\n"))


def test_read_capture_refuses_a_torn_row(tmp_path):
    with pytest.raises(SystemExit, match="fields of"):
        T.read_capture(write(tmp_path / "c.csv", "tick,a,b\n1,0x00000001,0x00000002\n2,0x00000001\n"))


def test_read_capture_accepts_a_whole_capture(tmp_path):
    header, body = T.read_capture(write(tmp_path / "c.csv", "tick,a\n1,0x00000001\n2,0x00000002\n"))
    assert header == ["tick", "a"] and [t for t, _ in body] == [1, 2]


@pytest.mark.parametrize("name", ["boot no divergence", "a;b", "x(residue y: z)", "", "a/b"])
def test_a_replay_name_m5_could_not_parse_back_is_refused(name):
    with pytest.raises(SystemExit, match="must match"):
        T.check_registry([{"name": name}])


def test_a_missing_pipeline_file_is_refused_not_hashed_to_empty(tmp_path):
    with pytest.raises(SystemExit, match="does not exist"):
        T.must_sha256(tmp_path / "absent.py")


# ----------------------------------------------------------- non-perturbation

REACH_GUEST = {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"}


def reach(tmp_path, **fields):
    p = tmp_path / "reach.json"
    base = {"iterations": 369, "import_calls": 165551, "frames_presented": 381, "stop": "s",
            "guest_paths": REACH_GUEST}
    p.write_text(json.dumps({"reach": dict(base, **fields)}))
    return p


def test_non_perturbation_accepts_the_reach_run(tmp_path):
    lifted = {"reach": {"iterations": 369, "import_calls": 165551, "frames_presented": 381,
                        "stop": "s"}, "guest_paths": REACH_GUEST}
    assert T.non_perturbation(lifted, reach(tmp_path))["equal"] is True


@pytest.mark.parametrize("key", list(T.REACH_KEYS))
def test_non_perturbation_refuses_any_moved_field(tmp_path, key):
    lifted = {"reach": {"iterations": 369, "import_calls": 165551, "frames_presented": 381,
                        "stop": "s"}, "guest_paths": REACH_GUEST}
    lifted["reach"][key] = "moved"
    with pytest.raises(SystemExit, match="not the reach run"):
        T.non_perturbation(lifted, reach(tmp_path))


def test_m5_record_shapes():
    none = {"name": "a", "result": {"native_ticks": 9, "reported": None, "class": None}}
    some = {"name": "b", "result": {"native_ticks": 9, "class": "bug",
                                    "reported": {"tick": 4, "field": "mode"}}}
    assert T.m5_record(none) == {"name": "a", "ticks": 9, "first_divergent_tick": None,
                                 "field": None, "class": None}
    assert T.m5_record(some) == {"name": "b", "ticks": 9, "first_divergent_tick": 4,
                                 "field": "mode", "class": "bug"}


# ------------------------------------------------------------ the vanilla oracle
#
# tools/oracle/oracle_mode.py is the one check of which oracle took a capture,
# and of whether a vanilla capture is the vanilla oracle's as it stands in the
# tree; tools/oracle/test_oracle_mode.py holds the log's grammar to it. What is
# held here is the replay tool's use of it: native_vanilla_problems asks it
# (the verify-profile review, 2026-09-23, found this file's own parser
# accepting what oracle_mode refused), and native_oracle holds a registry
# entry's flag to what its captures show. Every capture, address and byte here
# is made up; none is the executable's or a real run's (AGENTS.md rule 9).

PROBE_SOURCE = """/* a stand-in for harness/replay/native_vanilla_probe.c */
#ifndef RESTORE_IAT
#define RESTORE_IAT 1
#endif
static const uint32_t KEEP[][2] = {
    {0x00500100u, 0x00500105u},     /* the one kept range, {braced} in a comment */
};
"""
VANILLA_ROWS = ["0x00500100,5,e9a1a2a3a4,a1b2c3d4e5,kept",
                "0x00500200,5,e9b1b2b3b4,b2c3d4e5f6,restored",
                "0x00500300,5,e9c1c2c3c4,c3d4e5f6a7,restored",
                "0x00900010,4,f1e2d3c4,c4d3e2f1,iat-restored",
                "0x00500200,5,,,tick1-intact",
                "0x00500300,5,,,tick1-intact"]
LOADER = ["tick_probe"]
VANILLA = ["vanilla_probe", "tick_probe"]


def vanilla_tree(root: Path, source: str = PROBE_SOURCE) -> str:
    """The vanilla probe's source in `root`, as the tree would hold it; its sha256."""
    probe = root / T.VANILLA_PROBE
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(source)
    return T.sha256(probe)


def oracle_capture(root: Path, name: str, mods: list[str], rows: list[str] | None = None, *,
                   probe_sha: str, defines: list[str] = ()) -> Path:
    """A capture directory as tools/oracle/run_oracle_probe.py leaves one: `mods`
    built and loaded in that order, and for a vanilla capture its log with the
    artifact row the runner writes for it."""
    d = root / "private" / "captures" / name
    d.mkdir(parents=True)
    built = [{"mod_id": m, "source": f"/elsewhere/harness/replay/native_{m}.c",
              "source_sha256": probe_sha if m == "vanilla_probe" else "0" * 64,
              "defines": list(defines) if m == "vanilla_probe" else []} for m in mods]
    receipt = {"inputs": {"built_probes": built, "enabled_mods": list(mods),
                          "modloader_profile": {"Graphics": {"SkipIntro": False},
                                                "EnabledMods": list(mods)},
                          "game_args": []},
               "session": {}, "artifacts": []}
    if "vanilla_probe" in mods:
        log = d / "sa2-vanilla.csv"
        log.write_text("\n".join([T.OM.VANILLA_HEADER, *(VANILLA_ROWS if rows is None
                                                         else rows)]) + "\n")
        receipt["artifacts"].append({"file": log.name, "check": {"sha256": T.sha256(log)}})
    (d / "receipt.json").write_text(json.dumps(receipt))
    return d


def test_the_tree_s_vanilla_probe_is_the_one_the_check_reads(tmp_path):
    """It asserted the tree's four KEEP ranges until the probe kept only Present
    (development_snapshot) and the test failed (the integration review's F6). The check
    reads whatever the source declares, so what is held is that it can read the
    tree's source, and that a capture keeping exactly those ranges passes while
    one keeping another set does not."""
    keep = T.OM.keep_table(ROOT)
    assert keep and T.OM.default_restore_iat(ROOT) in (0, 1)
    slots = "iat-restored" if T.OM.default_restore_iat(ROOT) else "iat-kept"
    kept = [f"0x{va:08x},{n},e9a1a2a3a4,a1b2c3d4e5,kept" for va, n in sorted(keep)]
    rows = kept + VANILLA_ROWS[1:3] + [VANILLA_ROWS[3].replace("iat-restored", slots)] \
        + VANILLA_ROWS[4:]
    sha = T.sha256(ROOT / T.VANILLA_PROBE)
    good = oracle_capture(tmp_path, "good", VANILLA, rows, probe_sha=sha)
    assert T.native_vanilla_problems(good, root=ROOT) == []
    other = oracle_capture(tmp_path, "other", VANILLA, rows + [
        "0x00500400,5,e9d1d2d3d4,d4e5f6a7b8,kept"], probe_sha=sha)
    [problem] = T.native_vanilla_problems(other, root=ROOT)
    assert "KEEP table" in problem


@pytest.mark.parametrize("name,kw,rows,why", [
    ("ok", {}, None, None),
    # What this file's own parser accepted and oracle_mode refuses: the probe
    # built as an experiment (verify-profile, 2026-09-23).
    ("experiment", {"defines": ["RESTORE_IAT=1"]}, None, "an experiment, not the oracle"),
    ("old-probe", {"probe_sha": "1" * 64}, None, "not the one in the tree"),
    ("kept-more", {}, VANILLA_ROWS + ["0x00500400,5,e9d1d2d3d4,d4e5f6a7b8,kept"], "KEEP table"),
    ("slots-kept", {}, [r.replace("iat-restored", "iat-kept") for r in VANILLA_ROWS],
     "default leaves them restored"),
    ("rewritten", {}, VANILLA_ROWS + ["0x00500200,5,,,tick1-rewritten"], "code that ran unknown"),
    ("twice", {}, VANILLA_ROWS + [VANILLA_ROWS[1]], "repeats 0x00500200 restored"),
])
def test_the_replay_tool_asks_oracle_mode_and_takes_its_answer(tmp_path, name, kw, rows, why):
    """native_vanilla_problems is oracle_mode.vanilla_problems, word for word,
    so a capture one tool accepts the other cannot refuse (F8)."""
    sha = vanilla_tree(tmp_path)
    d = oracle_capture(tmp_path, name, VANILLA, rows, **dict({"probe_sha": sha}, **kw))
    got = T.native_vanilla_problems(d, root=tmp_path)
    assert got == T.OM.vanilla_problems(d, root=tmp_path)
    if why is None:
        assert got == []
    else:
        assert got and any(why in p for p in got), got


def test_there_is_one_vanilla_log_parser():
    """This file kept a second reader of sa2-vanilla.csv, and the two disagreed;
    what is left of it here is the call."""
    for name in ("vanilla_keep", "VANILLA_ACTIONS", "VANILLA_CSV", "VANILLA_HEADER", "_spans"):
        assert not hasattr(T, name), name


def test_a_loader_capture_is_not_the_vanilla_oracle_s(tmp_path):
    sha = vanilla_tree(tmp_path)
    d = oracle_capture(tmp_path, "loader", LOADER, probe_sha=sha)
    assert T.native_vanilla_problems(d, root=tmp_path) == [
        "loader was taken by the loader oracle, not the vanilla one"]


def test_a_tree_probe_the_check_cannot_read_is_a_reason_not_a_crash(tmp_path):
    """A caller that lists every reason (the stage run's refusal) still lists
    this one."""
    sha = vanilla_tree(tmp_path, "/* KEEP moved elsewhere */\n#define RESTORE_IAT 1\n")
    d = oracle_capture(tmp_path, "c", VANILLA, probe_sha=sha)
    [problem] = T.native_vanilla_problems(d, root=tmp_path)
    assert "declares no KEEP table this reads" in problem


# The two LOW findings of the verify-profile review reach the replay path
# through the one check: the second look at tick 1 must cover exactly what was
# restored, and the log must be the one the runner collected.

@pytest.mark.parametrize("rows,why", [
    (VANILLA_ROWS[:5], "the second look did not cover what was restored"),
    (VANILLA_ROWS[:4], "the second look never ran"),
    (VANILLA_ROWS[:3], "left the import slots not-logged"),
    (VANILLA_ROWS + ["0x00500600,5,,,tick1-intact"], "the second look did not cover"),
])
def test_tick1_rows_must_name_exactly_the_restored_ranges(tmp_path, rows, why):
    sha = vanilla_tree(tmp_path)
    problems = T.native_vanilla_problems(oracle_capture(tmp_path, "c", VANILLA, rows,
                                                        probe_sha=sha), root=tmp_path)
    assert problems and any(why in p for p in problems), problems


def test_a_log_edited_after_the_runner_collected_it_is_refused(tmp_path):
    """A log that still reads as a correct run's, and is not the file whose
    sha256 the receipt recorded when the runner collected it."""
    sha = vanilla_tree(tmp_path)
    d = oracle_capture(tmp_path, "c", VANILLA, probe_sha=sha)
    assert T.native_vanilla_problems(d, root=tmp_path) == []
    log = d / "sa2-vanilla.csv"
    log.write_text(log.read_text().replace("e9b1b2b3b4", "e9b1b2b3b5"))
    [problem] = T.native_vanilla_problems(d, root=tmp_path)
    assert "is not the file the runner collected" in problem


def test_a_log_whose_receipt_records_no_sha256_is_refused(tmp_path):
    """Nothing then shows the log is the one the runner collected, so the
    check cannot hold it to the file the runner collected (verify-profile's
    first LOW finding; tools/oracle/oracle_mode.py vanilla_problems requires
    the sha256 from push D on, and native_vanilla_problems says so)."""
    sha = vanilla_tree(tmp_path)
    d = oracle_capture(tmp_path, "c", VANILLA, probe_sha=sha)
    rec = json.loads((d / "receipt.json").read_text())
    rec["artifacts"] = []
    (d / "receipt.json").write_text(json.dumps(rec))
    assert T.native_vanilla_problems(d, root=tmp_path) == [
        "c's receipt records no sha256 for sa2-vanilla.csv, so nothing shows the log is the "
        "one the runner collected"]


# ------------------------------------------------ the registry's oracle flag

def oracle_entry(oracle, capture: str, *repeats: str) -> dict:
    native = {"capture": f"private/captures/{capture}/sa2-tick-capture.csv",
              "receipt": f"private/captures/{capture}/receipt.json",
              "repeats": [{"capture": f"private/captures/{r}/sa2-tick-capture.csv",
                           "receipt": f"private/captures/{r}/receipt.json"} for r in repeats]}
    entry = {"name": "boot", "native": native}
    if oracle is not None:
        entry["oracle"] = oracle
    return entry


def test_an_entry_s_flag_names_the_mode_its_captures_show(tmp_path):
    """No flag is the Mod Loader's oracle and its captures must be the loader's;
    "vanilla" is the game's own code and its captures must be the vanilla
    oracle's as it stands in the tree. Each direction is refused."""
    sha = vanilla_tree(tmp_path)
    for name in ("l1", "l2"):
        oracle_capture(tmp_path, name, LOADER, probe_sha=sha)
    for name in ("v1", "v2"):
        oracle_capture(tmp_path, name, VANILLA, probe_sha=sha)
    mask_header(tmp_path, "l1")
    assert T.native_oracle(oracle_entry(None, "l1", "l2"), tmp_path) is None
    mask_header(tmp_path, "v1")
    assert T.native_oracle(oracle_entry("vanilla", "v1", "v2"), tmp_path) == "vanilla"
    with pytest.raises(SystemExit) as e:
        T.native_oracle(oracle_entry(None, "v1", "v2"), tmp_path)
    assert str(e.value) == (
        "REPLAY_REFUSED boot: the registry names no oracle, which is the Mod Loader's, and its "
        "native capture and repeats were taken by the vanilla oracle (vanilla (kept "
        "0x00500100+5; import slots restored; intro played)); the flag must say which oracle "
        "the replay compares with")
    with pytest.raises(SystemExit, match="the registry names oracle 'vanilla', and its native "
                                         r"capture and repeats were taken by the loader oracle"):
        T.native_oracle(oracle_entry("vanilla", "l1", "l2"), tmp_path)


def mask_header(root: Path, *captures: str) -> Path:
    """The tree's digest mask, naming one file in each capture as its provenance, as
    tools/s4/snapshot_fields.py --emit writes it."""
    lines = []
    for c in captures:
        f = root / "private" / "captures" / c / "sa2-snapshots.bin"
        f.write_bytes(c.encode())
        rp = f.parent / "receipt.json"            # the runner records each file it collected
        rec = json.loads(rp.read_text())
        rec["artifacts"] = [a for a in rec.get("artifacts") or [] if a.get("file") != f.name] + [
            {"file": f.name, "check": {"sha256": T.sha256(f)}}]
        rp.write_text(json.dumps(rec))
        lines.append(f" * native {c}: private/captures/{c}/sa2-snapshots.bin sha256 {T.sha256(f)}")
    h = root / "harness" / "replay" / "tick_digest.h"
    h.parent.mkdir(parents=True, exist_ok=True)
    h.write_text("/* GENERATED\n" + "\n".join(lines) + "\n */\n")
    return h


def test_a_mask_chosen_from_another_oracle_mode_is_refused(tmp_path):
    """The P6 review's critical: a vanilla replay compared through a mask chosen
    from loader runs compares across modes. A mask from the replay's own mode is
    accepted, and a tree with no mask is a refusal, not a crash."""
    sha = vanilla_tree(tmp_path)
    oracle_capture(tmp_path, "l1", LOADER, probe_sha=sha)
    for name in ("v1", "v2"):
        oracle_capture(tmp_path, name, VANILLA, probe_sha=sha)
    entry = oracle_entry("vanilla", "v1", "v2")
    mask_header(tmp_path, "l1")
    with pytest.raises(SystemExit, match="its native side and the digest mask's runs are not one "
                                         "oracle mode"):
        T.native_oracle(entry, tmp_path)
    mask_header(tmp_path, "v1", "v2")
    assert T.native_oracle(entry, tmp_path) == "vanilla"
    # The mode check reads the hash the capture's receipt recorded, not the 12 GB again
    # (the mask review, W1); a receipt that records another file is still refused.
    rp = tmp_path / "private" / "captures" / "v2" / "receipt.json"
    rec = json.loads(rp.read_text())
    rec["artifacts"][-1]["check"]["sha256"] = "0" * 64
    rp.write_text(json.dumps(rec))
    with pytest.raises(SystemExit, match="its capture's receipt records 000000000000"):
        T.native_oracle(entry, tmp_path)
    mask_header(tmp_path, "v1", "v2")
    gone = tmp_path / "private" / "captures" / "v1" / "sa2-snapshots.bin"
    kept = gone.read_bytes()
    gone.unlink()                                  # a source deleted after its receipt
    with pytest.raises(SystemExit, match="is missing"):
        T.native_oracle(entry, tmp_path)
    gone.write_bytes(kept)
    (tmp_path / "harness" / "replay" / "tick_digest.h").unlink()
    with pytest.raises(SystemExit, match="tick_digest.h cannot be read"):
        T.native_oracle(entry, tmp_path)


def test_the_receipt_hashes_the_mask_sources_before_any_run(tmp_path, monkeypatch):
    """The fold review's W2: the by-receipt mode check cannot see a source changed on
    disk after its capture was taken; --receipt hashes the files, first."""
    sha = vanilla_tree(tmp_path)
    for name in ("v1", "v2"):
        oracle_capture(tmp_path, name, VANILLA, probe_sha=sha)
    mask_header(tmp_path, "v1", "v2")
    T.require_mask_sources_hashed(tmp_path)
    src = tmp_path / "private" / "captures" / "v2" / "sa2-snapshots.bin"
    src.write_bytes(b"changed after its receipt")
    assert T.native_oracle(oracle_entry("vanilla", "v1", "v2"), tmp_path) == "vanilla"
    with pytest.raises(SystemExit, match="do not hash as its header names them"):
        T.require_mask_sources_hashed(tmp_path)

    def stop(root=None, entries=None):
        raise SystemExit("MASK CHECKED")
    def ran(*a, **k):
        raise AssertionError("a replay ran before the mask check")
    monkeypatch.setattr(T, "require_mask_sources_hashed", stop)
    monkeypatch.setattr(T, "replay", ran)
    monkeypatch.setattr(sys, "argv", ["tick_replay.py", "--receipt"])
    with pytest.raises(SystemExit, match="MASK CHECKED"):
        T.main()


def test_a_capture_and_a_repeat_of_two_modes_are_never_compared(tmp_path):
    sha = vanilla_tree(tmp_path)
    oracle_capture(tmp_path, "l1", LOADER, probe_sha=sha)
    oracle_capture(tmp_path, "v1", VANILLA, probe_sha=sha)
    for oracle in (None, "vanilla"):
        with pytest.raises(SystemExit, match="produced by different oracle modes"):
            T.native_oracle(oracle_entry(oracle, "l1", "v1"), tmp_path)


def test_a_vanilla_entry_holds_every_repeat_to_the_tree_s_vanilla_oracle(tmp_path):
    """One mode is not enough: a repeat built from an older vanilla probe shares
    the capture's mode and is still not the oracle the tree runs."""
    sha = vanilla_tree(tmp_path)
    oracle_capture(tmp_path, "v1", VANILLA, probe_sha=sha)
    oracle_capture(tmp_path, "old", VANILLA, probe_sha="1" * 64)
    with pytest.raises(SystemExit) as e:
        T.native_oracle(oracle_entry("vanilla", "v1", "old"), tmp_path)
    assert str(e.value).startswith(
        "REPLAY_REFUSED boot: private/captures/old/sa2-tick-capture.csv is registered as the "
        "vanilla oracle's and does not show it: old was taken with a native_vanilla_probe.c "
        "that is not the one in the tree (111111111111 against ")


def test_a_capture_the_registry_gives_no_receipt_is_refused_by_name(tmp_path):
    """Its directory is its receipt's, so without one there is no directory to
    read; the refusal names the capture, not whatever directory an empty path
    resolves to."""
    sha = vanilla_tree(tmp_path)
    for name in ("l1", "l2"):
        oracle_capture(tmp_path, name, LOADER, probe_sha=sha)
    entry = oracle_entry(None, "l1", "l2")
    del entry["native"]["repeats"][0]["receipt"]
    with pytest.raises(SystemExit) as e:
        T.native_oracle(entry, tmp_path)
    assert str(e.value) == ("REPLAY_REFUSED boot: private/captures/l2/sa2-tick-capture.csv "
                            "names no oracle receipt, so nothing says which oracle took it")


@pytest.mark.parametrize("oracle", ["vanila", "loader", ["vanilla"], {"mode": "vanilla"}])
def test_an_oracle_the_tool_does_not_know_is_refused_not_read_as_none(tmp_path, oracle):
    with pytest.raises(SystemExit, match="the registry names oracle "):
        T.native_oracle(oracle_entry(oracle, "v1"), tmp_path)


def test_a_tree_without_the_vanilla_probe_is_a_reason_not_a_crash(tmp_path):
    sha = vanilla_tree(tmp_path)
    d = oracle_capture(tmp_path, "c", VANILLA, probe_sha=sha)
    (tmp_path / T.VANILLA_PROBE).unlink()
    [problem] = T.native_vanilla_problems(d, root=tmp_path)
    assert problem.startswith("harness/replay/native_vanilla_probe.c cannot be read")


def test_the_oracle_s_check_is_a_comparison_input():
    """An edit to the vanilla probe or to oracle_mode.py must stale a recorded
    result whose captures they vouched for."""
    assert T.VANILLA_PROBE in T.COMPARE_PIPELINE
    assert "tools/oracle/oracle_mode.py" in T.COMPARE_PIPELINE


# ------------------------------------------ the headers a capture is tied to
#
# native_probe_current held every header beside the probe to the capture's
# receipt, so a header no probe includes -- a per-replay digest mask -- would
# have made every registered capture stale (the M5 path review's H5). It now
# holds the headers the probe includes, and these show an included one still
# stales a capture.

CORE = '#include <stdint.h>\n#include "tick_digest.h"\n#include "tick_fields.h"\n'


def probe_tree(root: Path) -> None:
    rep = root / "harness" / "replay"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "native_tick_probe.c").write_text(
        '/* #include "not_this.h" in a comment */\n#include <windows.h>\n'
        '#include "tick_probe_core.h"\n')
    (rep / "tick_probe_core.h").write_text(CORE)
    (rep / "tick_fields.h").write_text("#define F 1\n")
    (rep / "tick_digest.h").write_text("#define D 1\n")


def probe_capture(root: Path, name: str = "c") -> dict:
    """A capture whose receipt records the probe and every header beside it, as
    the runner does, and a registry entry naming it."""
    rep = root / "harness" / "replay"
    d = root / "private" / "captures" / name
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(json.dumps({"inputs": {"built_probes": [{
        "source": f"/elsewhere/{T.NATIVE_PROBE}",
        "source_sha256": T.sha256(rep / "native_tick_probe.c"),
        "headers_sha256": {h.name: T.sha256(h) for h in sorted(rep.glob("*.h"))}}]}}))
    return {"name": "boot", "native": {"capture": f"private/captures/{name}/t.csv",
                                       "receipt": f"private/captures/{name}/receipt.json"}}


def test_the_probe_s_headers_are_what_it_includes_through_every_level(tmp_path):
    probe_tree(tmp_path)
    got = T.probe_headers(tmp_path / T.NATIVE_PROBE)
    assert sorted(got) == ["tick_digest.h", "tick_fields.h", "tick_probe_core.h"]


def test_the_tree_s_probe_includes_the_field_list_and_not_the_mask():
    """What the capture is tied to in the tree: the field list and the word-log
    core it includes. The digest mask is the host's input since the word log
    (2026-09-25; test_wordlog.py holds the same rule from the probe's side), so a
    new mask is not a new native configuration."""
    got = set(T.probe_headers(ROOT / T.NATIVE_PROBE))
    assert {"tick_fields.h", "word_log_core.h"} <= got and "tick_digest.h" not in got


def test_a_header_the_probe_does_not_include_stales_no_capture(tmp_path):
    probe_tree(tmp_path)
    entry = probe_capture(tmp_path)
    (tmp_path / "harness" / "replay" / "per_replay_mask.h").write_text("#define M 1\n")
    assert T.native_probe_current(entry, tmp_path)["headers_sha256"].keys() == {
        "tick_digest.h", "tick_fields.h", "tick_probe_core.h"}


@pytest.mark.parametrize("header", ["tick_fields.h", "tick_digest.h", "tick_probe_core.h"])
def test_an_included_header_still_stales_the_capture(tmp_path, header):
    """The falsifier the narrowing owes: each included header, reached directly
    or through another, is still compared."""
    probe_tree(tmp_path)
    entry = probe_capture(tmp_path)
    assert T.native_probe_current(entry, tmp_path)
    p = tmp_path / "harness" / "replay" / header
    p.write_text(p.read_text() + "/* moved */\n")
    with pytest.raises(SystemExit, match=rf"headers that are not the ones native_tick_probe\.c "
                                         rf"includes now \({header}"):
        T.native_probe_current(entry, tmp_path)


def test_a_receipt_that_did_not_record_an_included_header_is_stale(tmp_path):
    probe_tree(tmp_path)
    entry = probe_capture(tmp_path)
    rec = tmp_path / entry["native"]["receipt"]
    doc = json.loads(rec.read_text())
    del doc["inputs"]["built_probes"][0]["headers_sha256"]["tick_digest.h"]
    rec.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match=r"includes now \(tick_digest\.h"):
        T.native_probe_current(entry, tmp_path)


@pytest.mark.parametrize("line,why", [
    ("#include MASK_HEADER\n", "names no file this can hash"),
    ('#include "masks/boot.h"\n', "from outside replay/"),
    ('#include "absent.h"\n', "which is not beside it"),
])
def test_an_include_the_check_cannot_follow_refuses(tmp_path, line, why):
    """Refused, naming the file that holds the include: here a header the
    probe reaches through another, not the probe."""
    probe_tree(tmp_path)
    core = tmp_path / "harness" / "replay" / "tick_probe_core.h"
    core.write_text(CORE + line)
    with pytest.raises(SystemExit, match=rf"^REPLAY_REFUSED tick_probe_core\.h includes .*{why}"):
        T.probe_headers(tmp_path / T.NATIVE_PROBE)


# ------------------------------------------------------------- stage entries
#
# A stage entry (a `stage` block) is a native replay of a recording and the
# lifted build replaying the same recording. What it is held to before
# anything runs, how its lifted runs are compared with each other and its trace
# with native's, and what its input log must show. Every file here is made up:
# a stage trace in native_stage_trace.c's format, an end record in
# native_input_probe.c's, input in the recorder's, none from a real run
# (AGENTS.md rule 9).

import stage_boot as SB  # noqa: E402

GOAL, TICKS = 40, 60
INPUT_HEADER = "tick,ord,on,off,press,release,r,l,x1,y1,x2,y2,old"


def trace_line(t, level=10, player=0x03900000, x=0x41200000, state=0x10):
    return (f"{t},0x{level:04x},0x00000005,0x{state:04x},0x{t:08x},0x{player:08x},"
            f"0x{x:08x},0x00000000,0x00000000")


def trace_text(n=TICKS, per_tick=None):
    """A stage trace; per_tick maps a tick to trace_line's keyword overrides."""
    per_tick = per_tick or {}
    lines = [",".join(SB.TRACE_HEADER)]
    lines += [trace_line(t, **per_tick.get(t, {})) for t in range(1, n + 1)]
    return "\n".join(lines) + "\n"


def input_text(ticks=TICKS, calls=1):
    rows = [INPUT_HEADER] + [f"{t},{o},0x{t:x},0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0,0x0"
                             for t in range(ticks + 1) for o in range(calls)]
    return "\n".join(rows) + "\n"


def applied_text(ticks=TICKS, calls=1, *, drop=None, miss=None, past=5):
    """The lifted wrap's log of that input, and `past` ticks of neutral calls
    after the recording's last row, as a run that outlasts it writes."""
    out = [",".join(T.APPLIED_HEADER)]
    for t in range(ticks + 1 + past):
        for o in range(calls):
            if (t, o) == drop:
                continue                 # the lifted game made this call not at all
            matched = t <= ticks and (t, o) != miss
            v = t if matched else 0
            out.append(f"{t},{o},{int(matched)},0x{v:x}" + ",0x0" * 10)
    return "\n".join(out) + "\n"


def write_file(root: Path, rel: str, text: str) -> dict:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return {"file": rel, "sha256": T.sha256(p)}


REC_NAME = "rec"                    # the recording's directory, so its input header's name


def native_receipt(root: Path, d: str, *, script: str = f"/x/input-scripts/{REC_NAME}.h",
                   mode: int = 2, collected: str | None = None) -> None:
    """A native replay's receipt as the runner writes one: the input probe's
    build replaying `script`, and the applied input it collected."""
    applied = root / d / "sa2-input-applied.csv"
    rec = {"cwd": "/c", "command": "wine-env runinprefix ./sonic2app-unpacked.exe",
           "inputs": {"built_probes": [
               {"mod_id": "vanilla_probe", "defines": []},
               {"mod_id": "input_probe",
                "defines": [f"INPUT_MODE={mode}", f'INPUT_SCRIPT="{script}"', "TICK_LIMIT=400"]},
               {"mod_id": "tick_probe", "defines": []}]},
           "artifacts": [{"file": "sa2-input-applied.csv",
                          "check": {"sha256": collected or T.sha256(applied)}}]}
    (root / d / "receipt.json").write_text(json.dumps(rec))


def edit_input_build(root: Path, d: str, change) -> None:
    """Edit the input probe's build in a native receipt; `change` None adds a second
    input-probe build replaying the same recording."""
    p = root / d / "receipt.json"
    rec = json.loads(p.read_text())
    probes = rec["inputs"]["built_probes"]
    one = next(b for b in probes if b["mod_id"] == "input_probe")
    if change is None:
        probes.append(json.loads(json.dumps(one)))
    else:
        change(one)
    p.write_text(json.dumps(rec))


STAGE_MASK_PROBLEMS = T.stage_mask_problems     # the real check, before the fixture answers it


@pytest.fixture(autouse=True)
def stage_mask_answered(monkeypatch):
    """The made-up stage entries here have no digest mask of their own, which
    tick_replay refuses (stage_mask_problems); the tests below that name it call
    the real check, and every other test here exercises what runs past it."""
    monkeypatch.setattr(T, "stage_mask_problems", lambda entry, root=None: [])


def stage_entry(root: Path, *, runs=2, goals=None, misses=None, traces=None) -> dict:
    """A stage entry over made-up native runs, each in its own directory with
    its tick capture, end record, stage trace, applied input and receipt beside
    it, each a replay of the registered recording."""
    goals = goals or [GOAL] * runs
    misses = misses or [0] * runs
    caps = []
    for k in range(runs):
        d = f"private/captures/run{k}"
        cap = write_file(root, f"{d}/sa2-tick-capture.csv",
                         "tick,frame\n" + "".join(f"{t},0x{t:08x}\n" for t in range(1, TICKS + 1)))
        end = write_file(root, f"{d}/sa2-input-end.csv",
                         f"goal_tick,end_tick,misses\n{goals[k]},{goals[k] + 300},{misses[k]}\n")
        trace = write_file(root, f"{d}/sa2-stage-trace.csv", (traces or {}).get(k, trace_text()))
        applied = write_file(root, f"{d}/sa2-input-applied.csv", applied_text())
        native_receipt(root, d)
        caps.append({"capture": cap["file"], "sha256": cap["sha256"], "end": end,
                     "stage_trace": {"file": trace["file"], "sha256": trace["sha256"]},
                     "input_applied": applied})
    script = write_file(root, f"private/captures/{REC_NAME}/sa2-input.csv", input_text())
    return {"name": "metalharbor-test", "stage": {"name": "metalharbor", "level": 10},
            "input": {"script": script["file"], "sha256": script["sha256"]},
            "oracle": "vanilla",
            "native": dict(caps[0], repeats=caps[1:]), "residues": []}


def test_a_whole_stage_entry_has_no_problem(tmp_path):
    assert T.stage_entry_problems(stage_entry(tmp_path), tmp_path) == []


# The check that replaced STAGE_MASKS_BUILT (the switch that refused every stage
# entry until a stage's own mask existed): a stage entry compares through its own
# digest mask, harness/replay/masks/<name>.h, chosen from runs configured like its
# native captures (oracle_mode.require_mask_configuration).

def word_stage_entry(root: Path, **kw) -> dict:
    """stage_entry with the word policy every stage entry must carry, named for
    its stage as stage_mask_problems requires (the U6 registration review, N2)."""
    return dict(stage_entry(root, **kw), name="metalharbor", word_policy={
        "identity": T.IDENTITY_RECORD, "compare": "identity", "evidence": "ruled"})


def own_mask(root: Path, name: str = "metalharbor", text: str = "/* a mask */\n") -> Path:
    p = root / T.MASKS_DIR / f"{name}.h"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_a_stage_entry_without_its_own_mask_is_refused(tmp_path, monkeypatch):
    """No masks/<name>.h: mask_for would fall back to the boot's tick_digest.h,
    which exists (as in the tree) and does not stand in for the stage's own, so
    the entry is refused before anything runs, by stage_entry_problems too."""
    monkeypatch.setattr(T, "stage_mask_problems", STAGE_MASK_PROBLEMS)
    boot_mask = tmp_path / T.BOOT_MASK
    boot_mask.parent.mkdir(parents=True, exist_ok=True)
    boot_mask.write_text("/* the boot's mask */\n")
    got = T.stage_entry_problems(word_stage_entry(tmp_path), tmp_path)
    assert got == ["metalharbor: its own digest mask harness/replay/masks/metalharbor.h "
                   "does not exist, so it would be compared through the boot's "
                   "harness/replay/tick_digest.h; a stage replay registers once "
                   "tools/s4/snapshot_fields.py --word-logs --replay metalharbor has chosen "
                   "it"]


def test_a_stage_entry_without_a_word_policy_is_refused(tmp_path, monkeypatch):
    """Without a word policy the comparison reads the stored captures' digest
    columns, which follow the tree's tick_digest.h, so its own mask is never
    applied: refused even when the mask exists."""
    own_mask(tmp_path)
    seen = []
    monkeypatch.setattr(T.OM, "require_mask_configuration",
                        lambda header, dirs, root: seen.append(header) or {})
    got = STAGE_MASK_PROBLEMS(dict(stage_entry(tmp_path), name="metalharbor"), tmp_path)
    assert len(got) == 1 and got[0].startswith("metalharbor: it has no word_policy, so its "
                                               "digest columns would be the stored captures'")


def test_a_stage_entry_s_own_mask_holds_every_native_capture(tmp_path, monkeypatch):
    """The entry's own header, and every native capture's directory in the
    registry's order, the repeats among them, go to
    oracle_mode.require_mask_configuration; its answer passing is no problem."""
    header = own_mask(tmp_path)
    calls = []
    monkeypatch.setattr(T.OM, "require_mask_configuration",
                        lambda h, dirs, root: calls.append((h, dirs, root)) or {"build": 1})
    assert STAGE_MASK_PROBLEMS(word_stage_entry(tmp_path, runs=3), tmp_path) == []
    assert calls == [(header, [tmp_path / f"private/captures/run{k}" for k in range(3)],
                      tmp_path)]


def test_a_native_capture_the_mask_refuses_refuses_the_entry(tmp_path, monkeypatch):
    """A capture another build or configuration than the mask's sources (here
    the repeat) is refused, with oracle_mode's reason."""
    own_mask(tmp_path)

    def refuse(h, dirs, root):
        raise T.OM.ModeError(f"{dirs[-1].name} is not configured like {h.name}'s sources: "
                             "probes[2].defines: [] against [\"STACK_PAD=8192\"]")
    monkeypatch.setattr(T.OM, "require_mask_configuration", refuse)
    got = STAGE_MASK_PROBLEMS(word_stage_entry(tmp_path), tmp_path)
    assert got == ["metalharbor: its own digest mask harness/replay/masks/"
                   "metalharbor.h does not hold its native captures to the mask rule: run1 "
                   "is not configured like metalharbor.h's sources: probes[2].defines: [] "
                   "against [\"STACK_PAD=8192\"]"]


def test_a_mask_the_rule_refuses_refuses_the_entry(tmp_path):
    """The real oracle_mode on a header under masks/ that names no source run:
    the mask rule refuses it as it is read, so the entry is refused."""
    own_mask(tmp_path)
    got = STAGE_MASK_PROBLEMS(word_stage_entry(tmp_path), tmp_path)
    assert len(got) == 1 and "does not hold its native captures to the mask rule" in got[0]
    assert "names no source capture" in got[0]


# The refusals the U6 registration review found untested (W3) or missing (N1 to N3).

def test_a_stage_entry_that_registers_no_native_capture_is_refused(tmp_path, monkeypatch):
    """A word policy and its own mask, and a native block naming no capture: the
    mask has nothing to hold, and that is the one problem (W3)."""
    own_mask(tmp_path)
    monkeypatch.setattr(T.OM, "require_mask_configuration",
                        lambda h, dirs, root: pytest.fail("asked with no capture"))
    entry = dict(word_stage_entry(tmp_path), native={"repeats": []})
    assert STAGE_MASK_PROBLEMS(entry, tmp_path) == [
        "metalharbor: registers no native capture to hold to its mask's build"]


def test_a_mask_whose_files_cannot_be_read_refuses_the_entry(tmp_path, monkeypatch):
    """A file the mask check reads vanished: one problem saying it cannot be
    read, not a crash and not a pass (W3)."""
    own_mask(tmp_path)

    def gone(h, dirs, root):
        raise FileNotFoundError(2, "No such file or directory", str(h))
    monkeypatch.setattr(T.OM, "require_mask_configuration", gone)
    got = STAGE_MASK_PROBLEMS(word_stage_entry(tmp_path), tmp_path)
    assert len(got) == 1 and "cannot be read" in got[0] and "No such file" in got[0]


def test_a_receipt_that_is_not_json_refuses_the_entry(tmp_path, monkeypatch):
    """A receipt the mask check reads is not JSON: refused, not raised (N3)."""
    own_mask(tmp_path)

    def corrupt(h, dirs, root):
        return json.loads("{not json")
    monkeypatch.setattr(T.OM, "require_mask_configuration", corrupt)
    got = STAGE_MASK_PROBLEMS(word_stage_entry(tmp_path), tmp_path)
    assert len(got) == 1 and "cannot be read" in got[0] and "Expecting property name" in got[0]


@pytest.mark.parametrize("policy", [None, {}, {"identity": T.IDENTITY_RECORD},
                                    {"identity": T.IDENTITY_RECORD, "compare": "identity",
                                     "evidence": " "},
                                    {"identity": T.IDENTITY_RECORD, "compare": "bits",
                                     "evidence": "ruled"}])
def test_a_word_policy_that_says_nothing_refuses_the_entry(tmp_path, monkeypatch, policy):
    """The key alone, null, an empty policy or one without its ruling (N1): the
    comparer would refuse it only when it runs, so the entry is refused here."""
    own_mask(tmp_path)
    monkeypatch.setattr(T.OM, "require_mask_configuration", lambda h, dirs, root: {})
    got = STAGE_MASK_PROBLEMS(dict(word_stage_entry(tmp_path), word_policy=policy), tmp_path)
    assert got == [f"metalharbor: its word_policy must name the identity record "
                   f"{T.IDENTITY_RECORD} (identity), say it is compared by identity (compare) "
                   f"and carry the ruling it rests on (evidence); it is {policy!r}"]


def test_a_stage_entry_named_for_another_stage_is_refused(tmp_path, monkeypatch):
    """The entry's mask is found by its name and item 5's by its stage's
    (retake_replays.stage_mask): an entry named otherwise would be judged under
    one file and compared under another (N2)."""
    own_mask(tmp_path, "metalharbor-2")
    monkeypatch.setattr(T.OM, "require_mask_configuration", lambda h, dirs, root: {})
    got = STAGE_MASK_PROBLEMS(dict(word_stage_entry(tmp_path), name="metalharbor-2"), tmp_path)
    assert got == ["metalharbor-2: its stage block names 'metalharbor', so its own mask "
                   "harness/replay/masks/metalharbor-2.h would not be the mask the re-take tool "
                   "judges that stage's item 5 under (harness/replay/masks/<stage>.h); an entry "
                   "is named for its stage"]


@pytest.mark.parametrize("change,why", [
    # the recording itself: its input probe recorded (INPUT_MODE=1) and wrote an end record too
    (lambda e, r: native_receipt(r, "private/captures/run1", mode=1),
     "repeat 0: its input probe was not built to replay rec"),
    # a replay of another recording, every pin its own
    (lambda e, r: native_receipt(r, "private/captures/run1", script="/x/input-scripts/other.h"),
     "repeat 0: its input probe was not built to replay rec"),
    # a recording whose name only ends like this one's (the M6 fold review, nit 3)
    (lambda e, r: native_receipt(r, "private/captures/run1", script="/x/input-scripts/xrec.h"),
     "repeat 0: its input probe was not built to replay rec"),
    # no INPUT_MODE at all (nit 2), and two input-probe builds (nit 1)
    (lambda e, r: edit_input_build(r, "private/captures/run1",
                                   lambda b: b["defines"].remove("INPUT_MODE=2")),
     "repeat 0: its input probe was not built to replay rec"),
    (lambda e, r: edit_input_build(r, "private/captures/run1", None),
     "repeat 0: its input probe was not built to replay rec"),
    # the registry's receipt is not the one beside the capture (nit 7)
    (lambda e, r: e["native"]["repeats"][0].__setitem__("receipt",
                                                        "private/captures/run0/receipt.json"),
     "repeat 0: its registered receipt .* is not the one beside its tick capture"),
    (lambda e, r: (r / "private/captures/run0/receipt.json").unlink(),
     "the registered capture: its receipt .* cannot be read"),
    (lambda e, r: e["native"]["repeats"][0].pop("input_applied"),
     "repeat 0: registers no applied input"),
    (lambda e, r: (r / e["native"]["input_applied"]["file"]).write_text(applied_text(ticks=3)),
     "the registered capture: its applied input .* does not hash to its pin"),
    # the log of another recording's rows, re-pinned in the registry and in its receipt
    (lambda e, r: e["native"]["repeats"][0].__setitem__("input_applied", write_file(
        r, "private/captures/run1/sa2-input-applied.csv",
        applied_text().replace("\n5,0,1,0x5,", "\n5,0,1,0x9,"))) or native_receipt(
        r, "private/captures/run1"),
     "repeat 0: its applied input: the native replay applied other values than the "
     "recording's at tick 5"),
    # the file on disk re-pinned in the registry, not the one its run collected
    (lambda e, r: e["native"]["repeats"][0].__setitem__("input_applied", write_file(
        r, "private/captures/run1/sa2-input-applied.csv",
        applied_text().replace("\n5,0,1,0x5,", "\n5,0,1,0x9,"))),
     "repeat 0: its applied input .* is not the file its receipt collected"),
    (lambda e, r: e["native"]["repeats"][0].__setitem__("input_applied", write_file(
        r, "private/captures/elsewhere/sa2-input-applied.csv", applied_text())),
     "repeat 0: its applied input .* is not beside its tick capture"),
])
def test_a_native_capture_that_did_not_replay_the_registered_recording_is_refused(
        tmp_path, change, why):
    """The M6 plumbing review's W2 and the re-take review's W6: an end record with
    no miss is written by the recording itself and by a replay of another
    recording; the receipt's input probe and the applied log say which it was."""
    e = stage_entry(tmp_path)
    change(e, tmp_path)
    got = T.stage_entry_problems(e, tmp_path)
    assert any(re.search(why, p) for p in got), got


@pytest.mark.parametrize("change,why", [
    (lambda e, r: e["stage"].__setitem__("name", "cityescape"), "names no stage"),
    (lambda e, r: e["stage"].__setitem__("level", 13), "is level 10 in tools/s4/stage_run.py"),
    (lambda e, r: e.pop("input"), "no recording's input is registered"),
    (lambda e, r: (r / e["input"]["script"]).write_text(input_text(ticks=3)),
     "its input .* does not hash to its pin"),
    (lambda e, r: e["native"].__setitem__("repeats", []), "1 native capture"),
    (lambda e, r: (r / e["native"]["end"]["file"]).write_text("x"),
     "end record .* does not hash to its pin"),
    (lambda e, r: e["native"]["repeats"][0].pop("stage_trace"), "registers no stage trace"),
    (lambda e, r: (r / e["native"]["repeats"][0]["capture"]).write_text("tick,frame\n"),
     "repeat 0's tick capture .* does not hash"),
])
def test_a_stage_entry_that_is_not_what_item_5_asks_is_refused(tmp_path, change, why):
    e = stage_entry(tmp_path)
    change(e, tmp_path)
    got = T.stage_entry_problems(e, tmp_path)
    assert any(re.search(why, p) for p in got), got


def test_native_captures_naming_two_goal_ticks_are_refused_before_any_build(tmp_path):
    got = T.stage_entry_problems(stage_entry(tmp_path, goals=[GOAL, GOAL + 1]), tmp_path)
    assert got == [f"metalharbor-test: the native end records name goal ticks [{GOAL}, "
                   f"{GOAL + 1}]: stage-route item 5's replays set the goal latch on one tick, "
                   "above zero"]
    assert T.stage_entry_problems(stage_entry(tmp_path / "z", goals=[0, 0]), tmp_path / "z")


def test_a_native_replay_that_missed_input_rows_is_refused(tmp_path):
    got = T.stage_entry_problems(stage_entry(tmp_path, misses=[0, 2]), tmp_path)
    assert len(got) == 1 and "misses [0, 2]" in got[0]


def test_one_native_run_listed_twice_is_refused_by_the_rule_m6_s_writer_applies(tmp_path):
    """The registered capture again as its repeat, and a repeat listed twice:
    stage_boot.native_run_problems's sentence, so the two tools refuse alike."""
    e = stage_entry(tmp_path)
    e["native"]["repeats"] = [{k: e["native"][k] for k in ("capture", "sha256", "end",
                                                           "stage_trace")}]
    assert any("are one run listed 2 times" in p for p in T.stage_entry_problems(e, tmp_path))
    e = stage_entry(tmp_path / "b")
    e["native"]["repeats"].append(dict(e["native"]["repeats"][0]))
    assert any("are one run listed 2 times" in p
               for p in T.stage_entry_problems(e, tmp_path / "b"))


def test_an_end_record_or_trace_from_another_run_is_refused(tmp_path):
    e = stage_entry(tmp_path)
    for key in ("end", "stage_trace"):
        moved = write_file(tmp_path, f"private/captures/elsewhere/{key}.csv",
                           (tmp_path / e["native"][key]["file"]).read_text())
        e2 = json.loads(json.dumps(e))
        e2["native"][key] = moved
        got = T.stage_entry_problems(e2, tmp_path)
        assert any("is not beside its tick capture" in p for p in got), (key, got)


def test_stage_poll_budget_is_the_native_rows_and_the_margin(tmp_path):
    e = stage_entry(tmp_path)
    assert T.stage_poll_budget(e, tmp_path) == TICKS + T.STAGE_LEASH_MARGIN == TICKS + 60


def test_replay_refuses_a_budget_no_longer_than_the_capture(tmp_path, monkeypatch):
    """The leash per entry: checked before any build, and before the profile."""
    e = stage_entry(tmp_path)
    monkeypatch.setattr(T, "ROOT", tmp_path)
    for f in ("native_probe_current", "native_oracle", "native_start"):
        monkeypatch.setattr(T, f, lambda *a, **k: None)
    monkeypatch.setattr(T, "stage_entry_problems", lambda *a, **k: [])
    monkeypatch.setattr(T, "stage_poll_budget", lambda entry, root=None: TICKS)
    monkeypatch.setattr(T, "native_device_profile", lambda *a: pytest.fail("ran past the leash"))
    with pytest.raises(SystemExit, match="would judge the harness's leash"):
        T.replay(e, run=True)


def test_replay_refuses_a_stage_entry_with_a_problem_before_anything(tmp_path, monkeypatch):
    e = stage_entry(tmp_path, goals=[GOAL, GOAL + 1])
    monkeypatch.setattr(T, "ROOT", tmp_path)
    monkeypatch.setattr(T, "native_probe_current", lambda *a, **k: pytest.fail("read a probe"))
    with pytest.raises(SystemExit, match="goal ticks"):
        T.replay(e, run=True)


# The stage trace, compared.

def traces(tmp_path, native: str, lifted: str, repeat: str | None = None, *, labels=()):
    e = stage_entry(tmp_path, traces={0: native, 1: repeat if repeat is not None else native})
    e["residues"] = list(labels)
    lp = tmp_path / "lifted-trace.csv"
    lp.write_text(lifted)
    return T.compare_trace(e, lp, tmp_path, root=tmp_path, residues=tmp_path)


def test_identical_traces_have_no_divergence(tmp_path):
    r = traces(tmp_path, trace_text(), trace_text().replace("0x03900000", "0x04001cd0"))
    assert r["first_divergence"] is None and r["class"] is None
    assert r["judged_ticks"] == TICKS and r["compared_ticks"] == TICKS


def test_the_position_diverging_at_tick_7_is_trace_x_at_7(tmp_path):
    lifted = trace_text(per_tick={7: {"x": 0x41300000}}).replace("0x03900000", "0x04001cd0")
    d = traces(tmp_path, trace_text(), lifted)["first_divergence"]
    assert (d["tick"], d["field"], d["kind"]) == (7, "trace.x", "value")
    assert (d["native"], d["lifted"]) == ("0x41200000", "0x41300000")


def test_a_lifted_trace_that_ends_first_is_a_run_that_ended(tmp_path):
    lifted = trace_text(n=TICKS - 10).replace("0x03900000", "0x04001cd0")
    r = traces(tmp_path, trace_text(), lifted)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], r["class"]) == (TICKS - 9, T.RUN_ENDED, "bug")


def test_the_player_is_compared_by_which_allocation_it_names(tmp_path):
    """Native reallocates the player at tick 30. The lifted side reallocating on
    the same tick agrees; a tick late disagrees there, at trace.player."""
    native = trace_text(per_tick={t: {"player": 0x03950000} for t in range(30, TICKS + 1)})
    same = trace_text(per_tick={t: {"player": 0x04002000 if t < 30 else 0x04003000}
                                for t in range(1, TICKS + 1)})
    late = trace_text(per_tick={t: {"player": 0x04002000 if t < 31 else 0x04003000}
                                for t in range(1, TICKS + 1)})
    assert traces(tmp_path / "a", native, same)["first_divergence"] is None
    d = traces(tmp_path / "b", native, late)["first_divergence"]
    assert (d["tick"], d["field"], d["compared_as"]) == (30, "trace.player", "identity")


def test_a_repeat_trace_that_differs_caps_the_judged_ticks(tmp_path):
    lifted = trace_text().replace("0x03900000", "0x04001cd0")
    r = traces(tmp_path, trace_text(), lifted, repeat=trace_text(per_tick={20: {"x": 1}}))
    assert r["judged_ticks"] == 19 and r["first_divergence"]["field"] == T.UNREPEATED


def test_a_trace_residue_label_applies_only_to_its_tick_and_field(tmp_path):
    lifted = trace_text(per_tick={7: {"x": 0x41300000}}).replace("0x03900000", "0x04001cd0")
    (tmp_path / "rx.json").write_text(json.dumps({"observed_in": [
        {"replay": "metalharbor-test", "tick": 7, "field": "trace.x",
         "native": "0x41200000", "lifted": "0x41300000"}]}))
    r = traces(tmp_path, trace_text(), lifted,
               labels=[{"tick": 7, "field": "trace.x", "residue": "rx"}])
    assert r["first_divergence"] is None and r["class"] == "residue rx"


def test_the_capture_s_labels_and_the_trace_s_labels_are_kept_apart():
    e = {"residues": [{"field": "trace.x"}, {"field": "d_01750000"}]}
    assert T.trace_labels(e) == [{"field": "trace.x"}]
    assert T.capture_labels(e) == [{"field": "d_01750000"}]


# The first divergence is the earlier of the two streams'.

def result(div=None, masked=(), cls=None, ticks=100):
    rep = div or (masked[0] if masked else None)
    return {"native_ticks": ticks, "first_divergence": div, "residues_masked": list(masked),
            "reported": rep, "class": cls}


def test_the_first_divergence_is_the_earlier_of_the_capture_s_and_the_trace_s():
    cap = result({"tick": 50, "field": "d_01750000"}, cls="bug")
    trace = result({"tick": 7, "field": "trace.x"}, cls="bug")
    assert T.m5_record({"name": "s", "result": dict(cap, trace=trace)}) == {
        "name": "s", "ticks": 100, "first_divergent_tick": 7, "field": "trace.x", "class": "bug"}
    early = result({"tick": 3, "field": "frame"}, cls="bug")
    assert T.m5_record({"name": "s", "result": dict(early, trace=trace)})["field"] == "frame"
    clean = result()
    assert T.m5_record({"name": "s", "result": dict(clean, trace=result())})[
        "first_divergent_tick"] is None


def test_a_residue_in_one_stream_never_hides_a_bug_in_the_other():
    """compare() reports a bug after a residue rather than the residue; across
    the two streams the same holds, or M5 would read a labelled residue while
    the trace holds a bug."""
    residue = {"tick": 5, "field": "d_01750000", "residue": "r1"}
    cap = result(masked=[residue], cls="residue r1")
    trace = result({"tick": 9, "field": "trace.y"}, cls="bug")
    rec = T.m5_record({"name": "s", "result": dict(cap, trace=trace)})
    assert (rec["first_divergent_tick"], rec["field"], rec["class"]) == (9, "trace.y", "bug")
    rec = T.m5_record({"name": "s", "result": dict(cap, trace=result())})
    assert (rec["first_divergent_tick"], rec["class"]) == (5, "residue r1")


def test_boot_s_record_is_what_it_was():
    doc = {"name": "boot", "result": result({"tick": 4, "field": "d_01750000"}, cls="bug",
                                             ticks=1200)}
    assert T.m5_record(doc) == {"name": "boot", "ticks": 1200, "first_divergent_tick": 4,
                                "field": "d_01750000", "class": "bug"}


# The input the lifted run applied.

def applied_record(tmp_path, **kw):
    e = stage_entry(tmp_path)
    ap = tmp_path / "input-applied.csv"
    ap.write_text(applied_text(**kw))
    return T.input_record(e, ap, TICKS, tmp_path)


def test_an_input_log_that_applied_every_recorded_row_has_no_problem(tmp_path):
    rec = applied_record(tmp_path)
    assert rec["problems"] == [] and rec["misses"] == 0
    assert rec["recorded_rows"] == rec["applied_rows"] == TICKS + 1


def test_a_call_that_found_no_recorded_row_is_a_miss(tmp_path):
    rec = applied_record(tmp_path, miss=(12, 0))
    assert rec["misses"] == 1 and any("found no recorded row" in p for p in rec["problems"])


def test_a_recorded_row_never_applied_is_a_keying_drift(tmp_path):
    """A lifted game that made one call fewer: no miss is counted, and only
    the row comparison sees it."""
    rec = applied_record(tmp_path, drop=(12, 0))
    assert rec["misses"] == 0
    assert rec["problems"] == [f"the lifted run applied tick 13 call 0 where the recording's row "
                               f"13 is tick 12 call 0"]


def test_a_run_that_ended_before_the_native_capture_applied_too_few_rows(tmp_path):
    """Every row it applied was the recording's, in order, and it stopped at
    tick 30: only the count shows the rows it never reached."""
    e = stage_entry(tmp_path)
    ap = tmp_path / "input-applied.csv"
    ap.write_text(applied_text(ticks=30, past=0))
    assert T.input_record(e, ap, TICKS, tmp_path)["problems"] == [
        f"the lifted run applied 31 recorded rows up to tick {TICKS}, and the recording holds "
        f"{TICKS + 1}"]


def test_calls_past_the_native_capture_are_not_judged(tmp_path):
    """The lifted run outlasts the native capture by the leash margin; its
    neutral calls after the recording's last row are not misses."""
    assert applied_record(tmp_path, past=T.STAGE_LEASH_MARGIN)["problems"] == []


@pytest.mark.parametrize("line", ["1,0,2,0x1" + ",0x0" * 10, "1,0,1,1" + ",0x0" * 10,
                                  "x,0,1,0x1" + ",0x0" * 10])
def test_an_applied_log_the_wrap_cannot_have_written_is_refused(tmp_path, line):
    p = tmp_path / "a.csv"
    p.write_text(",".join(T.APPLIED_HEADER) + "\n" + line + "\n")
    with pytest.raises(SystemExit, match="not a row wrap_input_replay.c writes"):
        T.read_applied(p)


# The observed run against its baseline.

def stage_run_record(**over):
    base = {"reach": {"iterations": 101, "import_calls": 5000, "frames_presented": 99,
                      "stop": "s"},
            "device_profile": {"path": "p", "sha256": "a" * 64},
            "guest_paths": {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"},
            "import_table": {"path": "t", "sha256": "b" * 64}, "binary_sha256": "c" * 64}
    for k, v in over.items():
        if k in T.REACH_KEYS:
            base["reach"] = dict(base["reach"], **{k: v})
        else:
            base[k] = v
    return base


def test_non_perturbation_stage_accepts_equal_runs():
    r = T.non_perturbation_stage(stage_run_record(), stage_run_record(), "s")
    assert r["equal"] is True and set(r["fields"]) >= set(T.REACH_KEYS) | {"import_table"}


@pytest.mark.parametrize("key,value", [
    *[(k, "moved") for k in T.REACH_KEYS],
    ("device_profile", {"path": "p", "sha256": "d" * 64}),
    ("device_profile", "default"),
    ("guest_paths", {"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"}),
    ("guest_paths", None),
    ("guest_paths", {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app.exe"}),
    ("guest_paths", {"cwd": "Z:\\d", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"}),
    ("import_table", {"path": "t", "sha256": "e" * 64}),
    ("binary_sha256", "f" * 64),
])
def test_non_perturbation_stage_refuses_each_moved_field(key, value):
    with pytest.raises(SystemExit, match="is not its baseline"):
        T.non_perturbation_stage(stage_run_record(**{key: value}), stage_run_record(), "s")


def test_non_perturbation_stage_refuses_two_runs_with_no_directory():
    """The working-directory review's W2(b): neither run carrying a directory is not
    one directory."""
    with pytest.raises(SystemExit, match="is not its baseline"):
        T.non_perturbation_stage(stage_run_record(guest_paths=None),
                                 stage_run_record(guest_paths=None), "s")


def test_a_stage_result_with_no_baseline_is_refused():
    with pytest.raises(SystemExit, match="no baseline run is recorded"):
        T.non_perturbation_stage(stage_run_record(), None, "s")


def test_a_stage_entry_s_inputs_hold_its_probes_and_the_stage_run():
    e = {"stage": {"name": "metalharbor", "level": 10}}
    import stage_run as SR
    assert set(T.STAGE_PROBES) | {"tools/s4/stage_boot.py", "tools/oracle/record_stage.py"} \
        <= set(T.compare_pipeline(e))
    assert set(SR.HOOKS) | {"tools/s4/stage_run.py", "harness/stage_goal_probe.c"} \
        <= set(T.lifted_pipeline(e))
    assert T.compare_pipeline({}) == T.COMPARE_PIPELINE
    assert T.lifted_pipeline({}) == T.LIFTED_PIPELINE


# The stage entry's two lifted runs, with loop_run replaced by a stand-in that
# writes what the real run writes. Nothing is built or run.

@pytest.fixture
def stage_runs(tmp_path, monkeypatch):
    """T and stage_run pointed at tmp_path, the rebuild, save and device
    guards answered, and subprocess.run a stand-in loop_run that writes a run
    record, a matrix, an import table and every observer file the command's
    environment names. `calls` collects each command's environment."""
    import rebuild_image as RI
    import stage_run as SR
    for var in set(T.DP.OWNED) | set(T.RR.DEVICE_VARS):
        monkeypatch.delenv(var, raising=False)
    for mod in (T, SR):
        monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(SR, "OUT", tmp_path / "replay")
    monkeypatch.setattr(SR, "RUNDIR", tmp_path / "run")
    monkeypatch.setattr(T, "OUT", tmp_path / "replay")
    monkeypatch.setattr(T, "RUNDIR", tmp_path / "run")
    monkeypatch.setattr(RI, "staged_not_rebuilt", lambda: [])
    (tmp_path / "run").mkdir()
    state = {"calls": [], "table": {"rows": [{"dll": "k.dll", "name": "f", "implemented": True,
                                              "calls": 3}]},
             "profile_read": None, "skip": set(), "switch": True, "left": {}}
    monkeypatch.setattr(T.SS, "reset_lifted",
                        lambda cmd, root: {"removed": {}, "left": dict(state["left"])})

    def fake_run(cmd, **kw):
        env = dict(kv.split("=", 1) for kv in cmd[cmd.index("--run") + 1].split("|")[1:])
        label = cmd[cmd.index("--run") + 1].split("|")[0]
        build = cmd[cmd.index("--name") + 1]
        state["calls"].append((label, env, cmd))
        polls = int(env["SA2_PUMP_POLLS"])
        record = {"pump": {"iterations": polls, "quit_by_host": 1, "poll_limit": polls},
                  "calls": {"total": 777}, "d3d9": {"frames": {"present": polls},
                                                    "device_enabled": True,
                                                    "constant_tables": {"switch":
                                                                        state["switch"]}},
                  "trap": {"note": "the guest called ExitProcess(0)"},
                  "device_profile": state["profile_read"] or {
                      "default": False, "source": env.get("SA2_DEVICE_PROFILE"),
                      "sha256": "a" * 64},
                  # what the runtime answers: the directory the run was handed
                  "virtual": {"cwd": env.get("SA2_GUEST_CWD", "C:\\SA2"),
                              "exe_path": env.get("SA2_GUEST_EXE_PATH", "C:\\SA2\\sonic2app.exe")},
                  **{k: {"device_enabled": True} for k in ("dsound", "dinput", "xaudio2",
                                                            "ddraw")}}
        (tmp_path / "run" / f"{build}.{label}.json").write_text(json.dumps(record))
        (tmp_path / "run" / f"{build}.matrix.json").write_text(json.dumps({"sha256": "c" * 64}))
        table = next(a.split("=", 1)[1] for a in cmd if a.startswith("--probe-arg=")
                     and a.endswith(".table.json"))
        (tmp_path / table).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / table).write_text(json.dumps(state["table"]))
        texts = {"SA2_REPLAY_TICKS": "tick,frame\n" + "".join(f"{t},0x{t:08x}\n"
                                                             for t in range(1, polls)),
                 "SA2_STAGE_TRACE": trace_text(n=polls - 1).replace("0x03900000", "0x04001cd0"),
                 "SA2_GOAL_LATCH": "tick,goal_latch\n" + "".join(
                     f"{t},{'0x01' if t >= GOAL else '0x00'}\n" for t in range(polls)),
                 "SA2_INPUT_APPLIED": applied_text(past=polls - TICKS - 1)}
        for var, text in texts.items():
            if var in env and var not in state["skip"]:
                (tmp_path / env[var]).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / env[var]).write_text(text)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    import subprocess
    monkeypatch.setattr(T.subprocess, "run", fake_run)
    return state


PROFILE = {"path": "private/captures/run0/sa2-device-profile.txt", "sha256": "a" * 64,
           "counts": {}, "guest": {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"}}


def test_the_stage_runs_are_stage_run_s_command_with_the_observers_on_and_off(tmp_path,
                                                                              stage_runs):
    import stage_run as SR
    e = stage_entry(tmp_path)
    base = T.run_stage_lifted(e, PROFILE, observe=False)
    obs = T.run_stage_lifted(e, PROFILE, observe=True)
    (lb, off, cmd_off), (lo, on, cmd_on) = stage_runs["calls"]
    assert (lb, lo) == ("baseline", "ticks")
    polls = TICKS + T.STAGE_LEASH_MARGIN
    for env, cmd in ((off, cmd_off), (on, cmd_on)):
        assert env["SA2_PUMP_POLLS"] == str(polls)
        assert env["SA2_INPUT_SCRIPT"] == str((tmp_path / e["input"]["script"]).resolve())
        assert env["SA2_DEVICE_PROFILE"] == PROFILE["path"]
        assert cmd[cmd.index("--name") + 1] == "replay-metalharbor-test"
        assert "--probe-arg=--dump-imports" in cmd
        assert cmd[cmd.index("--timeout") + 1] == str(max(600, polls // 5) + 120)
        assert "harness/stage_goal_probe.c" in cmd and "sub_7a8948" in cmd
    assert not {v for v in SR.OBSERVERS if v in off}
    assert {v for v in SR.OBSERVERS if v in on} == set(SR.OBSERVERS)
    for var in SR.OBSERVERS:
        assert on[var].startswith("replay/metalharbor-test/"), (var, on[var])
    assert base["import_table"]["path"] == "replay/metalharbor-test/baseline/imports.table.json"
    assert obs["goal_latch"]["path"] == "replay/metalharbor-test/goal-latch.csv"
    assert obs["polls"] == polls and "capture" not in base
    assert T.non_perturbation_stage(obs, base, "s")["equal"] is True


def test_a_stage_run_that_read_another_profile_is_refused(tmp_path, stage_runs):
    stage_runs["profile_read"] = {"default": True}
    with pytest.raises(SystemExit, match="is not the one asked for"):
        T.run_stage_lifted(stage_entry(tmp_path), PROFILE, observe=True)


@pytest.mark.parametrize("var,what", [("SA2_GOAL_LATCH", "goal-latch column"),
                                      ("SA2_INPUT_APPLIED", "applied-input log"),
                                      ("SA2_STAGE_TRACE", "stage trace"),
                                      ("SA2_REPLAY_TICKS", "tick capture")])
def test_an_observed_run_that_wrote_no_observer_file_is_refused(tmp_path, stage_runs, var, what):
    stage_runs["skip"] = {var}
    with pytest.raises(SystemExit, match=f"wrote no {what}"):
        T.run_stage_lifted(stage_entry(tmp_path), PROFILE, observe=True)


def test_a_stage_run_whose_record_shows_a_switch_off_is_refused(tmp_path, stage_runs):
    """The D3DX table on in its environment and off in its record: the run
    is not its configuration's (reach_receipt.switch_problems, P6's N4)."""
    stage_runs["switch"] = False
    with pytest.raises(SystemExit, match="SA2_D3DX_CONSTANT_TABLE=1 was set"):
        T.run_stage_lifted(stage_entry(tmp_path), PROFILE, observe=False)


def test_a_stage_run_over_an_overlay_that_holds_anything_is_refused(tmp_path, stage_runs):
    """The observed run is held to its baseline field for field, so both
    start from an empty overlay, the only state each can reproduce alone."""
    stage_runs["left"] = {"Config/UserConfig.cfg": "0" * 64}
    with pytest.raises(SystemExit, match="REPLAY_REFUSED"):
        T.run_stage_lifted(stage_entry(tmp_path), PROFILE, observe=True)
    assert stage_runs["calls"] == []            # refused before the run


def test_a_stage_run_that_dumped_an_empty_import_table_is_refused(tmp_path, stage_runs):
    stage_runs["table"] = {"rows": []}
    with pytest.raises(SystemExit, match="dumped no import table"):
        T.run_stage_lifted(stage_entry(tmp_path), PROFILE, observe=False)


def recorded_stage(tmp_path, stage_runs) -> tuple[dict, dict]:
    e = stage_entry(tmp_path)
    base = T.run_stage_lifted(e, PROFILE, observe=False)
    lifted = T.run_stage_lifted(e, PROFILE, observe=True)
    lifted["baseline"] = base
    lifted["input"] = T.input_record(e, tmp_path / lifted["input_applied"]["path"], TICKS,
                                     tmp_path)
    return e, lifted


def test_a_recorded_stage_result_rests_on_its_kept_files(tmp_path, stage_runs):
    e, lifted = recorded_stage(tmp_path, stage_runs)
    assert T.stage_recheck(e, lifted, TICKS, tmp_path) == []


@pytest.mark.parametrize("mutate,why", [
    (lambda t, L: L["baseline"]["reach"].__setitem__("import_calls", 778),
     "the baseline run's recorded reach"),
    (lambda t, L: (t / L["goal_latch"]["path"]).write_text("tick,goal_latch\n0,0x00\n"),
     "goal-latch column .* does not hash to its pin"),
    (lambda t, L: (t / L["baseline"]["import_table"]["path"]).write_text("{}"),
     "baseline run's import table .* does not hash to its pin"),
    (lambda t, L: (t / L["input_applied"]["path"]).write_text(applied_text(miss=(7, 0))),
     "applied-input log .* does not hash to its pin"),
    (lambda t, L: L.__setitem__("polls", TICKS), "does not outlast the native capture"),
    (lambda t, L: L["input"].__setitem__("misses", 0) or L["input"].__setitem__("calls", 1),
     "the recorded input record is not what"),
    (lambda t, L: L.__setitem__("device_profile", "default"),
     "the observed run's recorded device profile is not its run record's"),
    (lambda t, L: L.pop("baseline"), "no baseline run is recorded"),
    # One half of the directory at a time (the working-directory fold review, W1).
    (lambda t, L: L["baseline"].__setitem__(
        "guest_paths", dict(L["baseline"]["guest_paths"], exe_path="Z:\\c\\other.exe")),
     "the baseline run's recorded guest directory is not its run record's"),
    (lambda t, L: L.__setitem__("guest_paths", dict(L["guest_paths"], cwd="Z:\\d")),
     "the observed run's recorded guest directory is not its run record's"),
])
def test_each_recorded_stage_fact_is_rechecked(tmp_path, stage_runs, mutate, why):
    e, lifted = recorded_stage(tmp_path, stage_runs)
    mutate(tmp_path, lifted)
    got = T.stage_recheck(e, lifted, TICKS, tmp_path)
    assert any(re.search(why, p) for p in got), got


def test_an_applied_log_repinned_after_a_miss_is_still_refused(tmp_path, stage_runs):
    """Moved and repinned, so no pin catches it: the recomputed record does."""
    e, lifted = recorded_stage(tmp_path, stage_runs)
    p = tmp_path / lifted["input_applied"]["path"]
    p.write_text(applied_text(past=T.STAGE_LEASH_MARGIN - 1, miss=(7, 0)))
    lifted["input_applied"]["sha256"] = T.sha256(p)
    got = T.stage_recheck(e, lifted, TICKS, tmp_path)
    assert any("found no recorded row" in x for x in got), got


def test_the_stage_probes_must_be_the_tree_s(tmp_path):
    """A stage capture built with another stage-trace or input probe is another
    capture; boot's check is unchanged."""
    probe_tree(tmp_path)
    rep = tmp_path / "harness" / "replay"
    for rel in T.STAGE_PROBES:
        (tmp_path / rel).write_text(f"/* {rel} */\n")
    entry = probe_capture(tmp_path)
    rec = tmp_path / entry["native"]["receipt"]
    doc = json.loads(rec.read_text())
    doc["inputs"]["built_probes"] += [{"source": f"/x/{r}", "source_sha256": T.sha256(tmp_path / r)}
                                     for r in T.STAGE_PROBES]
    rec.write_text(json.dumps(doc))
    entry["stage"] = {"name": "metalharbor", "level": 10}
    assert set(T.native_probe_current(entry, tmp_path)["stage_probes"]) == set(T.STAGE_PROBES)
    (tmp_path / T.STAGE_PROBES[1]).write_text("/* moved */\n")
    with pytest.raises(SystemExit, match="native_input_probe.c that is not the one in the tree"):
        T.native_probe_current(entry, tmp_path)
    del entry["stage"]
    assert "stage_probes" not in T.native_probe_current(entry, tmp_path)
    assert rep.is_dir()


def test_what_a_stage_entry_records_is_what_m6_s_writer_reads(tmp_path, stage_runs):
    """The hand-off to tools/s4/stage_boot.py: the lifted capture, goal-latch
    column and stage trace under the key names goal_evidence reads, beside
    each other, so the goal is judged on the run M5 judges. The stand-in's
    latch rises on the native goal tick, so the verdict completes; a column a
    tick late does not."""
    e, lifted = recorded_stage(tmp_path, stage_runs)
    doc = {"name": e["name"], "lifted": lifted}
    v = SB.goal_verdict(SB.goal_evidence(e, doc), tmp_path)
    assert (v["native_goal_tick"], v["lifted_goal_tick"], v["complete"]) == (GOAL, GOAL, True)
    latch = tmp_path / lifted["goal_latch"]["path"]
    latch.write_text(latch.read_text().replace(f"\n{GOAL},0x01\n", f"\n{GOAL},0x00\n"))
    lifted["goal_latch"]["sha256"] = T.sha256(latch)
    v = SB.goal_verdict(SB.goal_evidence(e, {"name": e["name"], "lifted": lifted}), tmp_path)
    assert (v["lifted_goal_tick"], v["complete"]) == (GOAL + 1, False)


def test_the_lifted_run_is_held_to_its_capture_s_directory(tmp_path):
    """The working-directory review's W1: a recorded lifted run in another directory
    than its capture's game ran in, or a capture whose receipt names none, is refused."""
    d = tmp_path / "private/captures/c0"
    d.mkdir(parents=True)
    (d / "receipt.json").write_text(json.dumps(
        {"cwd": "/c", "command": "wine-env runinprefix ./sonic2app-unpacked.exe"}))
    entry = {"name": "boot", "native": {"receipt": "private/captures/c0/receipt.json"}}
    here = {"cwd": "Z:\\c", "exe_path": "Z:\\c\\sonic2app-unpacked.exe"}
    T.lifted_guest(entry, {"guest_paths": here}, tmp_path)
    # Each half on its own too: the executable's path is the half that moved the
    # 708 calls (the working-directory fold review, W1).
    for other in ({"cwd": "C:\\SA2", "exe_path": "C:\\SA2\\sonic2app.exe"}, None,
                  dict(here, exe_path="Z:\\c\\sonic2app.exe"), dict(here, cwd="Z:\\d")):
        with pytest.raises(SystemExit, match="recorded guest directory .* is not its capture's"):
            T.lifted_guest(entry, {"guest_paths": other}, tmp_path)
    (d / "receipt.json").write_text(json.dumps({"command": "x runinprefix ./a.exe"}))
    with pytest.raises(SystemExit, match="does not say which guest directory"):
        T.lifted_guest(entry, {"guest_paths": here}, tmp_path)


# ------------------------------------------------ values the host chooses: one rule
#
# The user's rulings of 2026-09-24, decisions 1 and 2: an allocation address and
# a Win32 handle number are compared by what they name, one rule on M5, with
# three checks that can each fail -- the pairing, the open tick and the close
# tick. The first tests are what the rule must let through (Wine reusing a freed
# number, a heap that moved); each mutation after them breaks exactly one of the
# three checks. Columns: a named heap word, a digest, three handle words.

WHEADER = ["tick", "frame", "g_heap", "d_x", "h_a", "h_b", "h_c"]
WIDENT = {"g_heap": "heap", "h_a": "handle", "h_b": "handle", "h_c": "handle"}
F, E = 0x230, 0x22C                   # a file handle and an event, as Wine numbers them
LF, LE = 0x7D000000, 0x7F03B000       # the runtime's numbers for the same two


def wcmp(tmp_path, native, lifted, judged=None, labels=()):
    return T.compare(WHEADER, native, WHEADER, lifted, {}, list(labels), tmp_path,
                     judged=judged, residues=tmp_path, name="t", identity=dict(WIDENT))


# entry opens F at h_a and h_b and E at h_c on tick 2, closes on tick 4, and
# Wine hands F and E out again on tick 5 (its reuse of a freed number)
WNATIVE = rows((1, WINE, 7, 0, 0, 0), (2, WINE, 7, F, F, E), (3, WINE, 8, F, F, E),
               (4, WINE, 8, 0, 0, 0), (5, WINE, 9, F, F, E), (6, WINE, 9, F, F, E))
WLIFTED = rows((1, HEAP, 7, 0, 0, 0), (2, HEAP, 7, LF, LF, LE), (3, HEAP, 8, LF, LF, LE),
               (4, HEAP, 8, 0, 0, 0), (5, HEAP, 9, LF + 0x1000, LF + 0x1000, LE + 0x1000),
               (6, HEAP, 9, LF + 0x1000, LF + 0x1000, LE + 0x1000))


def checks_failing(r):
    return [c for c, v in r["identity"]["checks"].items() if not v["ok"]]


def test_the_rule_agrees_where_the_host_s_numbers_differ(tmp_path):
    """Wine reuses F and E after the close; the runtime hands out new numbers.
    Released at the close, F and E take new numbers natively too, and the two
    streams agree on every tick."""
    r = wcmp(tmp_path, WNATIVE, WLIFTED)
    assert r["first_divergence"] is None and r["class"] is None
    assert checks_failing(r) == []
    assert r["identity"]["native_events"][:3] == [
        "1 g_heap 0x00000000 0x00000001", "2 h_a 0x00000000 0x00000001",
        "2 h_b 0x00000000 0x00000001"]


def test_without_the_release_wine_s_reuse_would_diverge(tmp_path, monkeypatch):
    """The control for the test above: numbered without the release, Wine's F
    comes back as number 1 and the runtime's new number as 3, and the pairing
    fails where the entry reopens. The release is what lets the reuse through."""
    def no_release(header, rows_, identity, lifted=False):
        cols = [(i, identity[f]) for i, f in enumerate(header[1:]) if f in identity]
        seen: dict = {}
        out = []
        for tick, words in rows_:
            w = list(words)
            for i, k in cols:
                v = int(w[i], 16)
                if v not in T.NULLS[k]:
                    n = seen.setdefault((k, v), 1 + sum(1 for kk, _ in seen if kk == k))
                    w[i] = f"0x{n:08x}"
            out.append((tick, w))
        return out
    monkeypatch.setattr(T, "canonical", no_release)
    r = wcmp(tmp_path, WNATIVE, WLIFTED)
    assert (r["first_divergence"]["tick"], r["first_divergence"]["check"]) == (5, "pairing")
    assert checks_failing(r) == ["pairing"]


def test_a_heap_shifted_by_0x30_still_agrees(tmp_path):
    """The judge's run B: the heap moved by 0x30 with every position the same.
    The native repeat is compared under the same rule, so it judges every tick."""
    moved = [(t, [w[0], f"0x{int(w[1], 16) + 0x30:08x}"] + w[2:]) for t, w in WNATIVE]
    lifted = [(t, [w[0], f"0x{int(w[1], 16) + 0x30:08x}"] + w[2:]) for t, w in WLIFTED]
    assert wcmp(tmp_path, WNATIVE, lifted)["first_divergence"] is None
    e = {"name": "x", "native": {"capture": "a/c.csv", "sha256": "0",
                                 "repeats": [{"capture": "b/c.csv", "sha256": "1"}]}}
    det = T.native_repeats(e, WHEADER, WNATIVE, tmp_path,
                           streams=[(e["native"]["repeats"][0], WHEADER, moved)],
                           identity=dict(WIDENT))
    assert det["judged_ticks"] == 6 and det["repeats"][0]["first_divergent_tick"] is None


def test_swapping_two_handles_fails_the_pairing_alone(tmp_path):
    """The lifted build puts the event in the file handle's word and the file in
    the event's on tick 3, with no close between: every word stays open on both
    sides, so only the pairing can see it."""
    lifted = [(t, list(w)) for t, w in WLIFTED]
    lifted[2][1][3], lifted[2][1][5] = f"0x{LE:08x}", f"0x{LF:08x}"
    r = wcmp(tmp_path, WNATIVE, lifted)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"], d["compared_as"]) == (3, "h_a", "pairing",
                                                                      "identity")
    assert checks_failing(r) == ["pairing"]
    assert r["identity"]["checks"]["pairing"]["first"]["tick"] == 3 and r["class"] == "bug"


def test_an_open_one_tick_late_fails_the_open_check_alone(tmp_path):
    lifted = [(t, list(w)) for t, w in WLIFTED]
    for c in (3, 4, 5):
        lifted[1][1][c] = "0x00000000"             # nothing open on tick 2; open on 3
    r = wcmp(tmp_path, WNATIVE, lifted)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"]) == (2, "h_a", "open")
    assert checks_failing(r) == ["open"]
    first = r["identity"]["checks"]["open"]["first"]
    assert (first["native"], first["lifted"]) == ("opens at 2", "opens at 3")


def test_a_close_one_tick_late_fails_the_close_check_alone(tmp_path):
    lifted = [(t, list(w)) for t, w in WLIFTED]
    for c in (3, 4, 5):
        lifted[3][1][c] = lifted[2][1][c]          # still open on tick 4; closed on 5
    lifted[4][1][3:6] = ["0x00000000"] * 3
    lifted = lifted[:5]
    native = WNATIVE[:5]
    native5 = list(native[4][1])
    native5[3:6] = ["0x00000000"] * 3              # natively closed from 4, reopened on 6
    native = native[:4] + [(5, native5)]
    r = wcmp(tmp_path, native, lifted)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"]) == (4, "h_a", "close")
    assert checks_failing(r) == ["close"]


def test_two_words_holding_one_handle_must_hold_one_lifted(tmp_path):
    """h_a and h_b hold one file handle natively; lifted they hold two."""
    lifted = [(t, list(w)) for t, w in WLIFTED]
    for k in (1, 2):
        lifted[k][1][4] = f"0x{LF + 0x40:08x}"
    r = wcmp(tmp_path, WNATIVE, lifted)
    assert (r["first_divergence"]["field"], r["first_divergence"]["check"]) == ("h_b",
                                                                                "pairing")


def test_an_invalid_handle_value_is_a_null_compared_by_its_bits(tmp_path):
    """A failed CreateFile leaves INVALID_HANDLE_VALUE: no number, and no open."""
    native = [(t, list(w)) for t, w in WNATIVE]
    lifted = [(t, list(w)) for t, w in WLIFTED]
    native[0][1][3] = lifted[0][1][3] = "0xffffffff"
    r = wcmp(tmp_path, native, lifted)
    assert r["first_divergence"] is None
    assert "1 h_a 0x00000000 0xffffffff" in r["identity"]["native_events"]
    lifted[0][1][3] = f"0x{LF:08x}"
    assert wcmp(tmp_path, native, lifted)["first_divergence"]["check"] == "open"


def test_a_null_read_differently_on_each_side_fails_the_open_or_the_close(tmp_path):
    """INVALID_HANDLE_VALUE on one side and 0 on the other is no value on
    either, and still a divergence: a failed open (0 to INVALID) is an open, and
    a close names the null it goes to (the identity review of 2026-09-25, W2)."""
    native = [(t, list(w)) for t, w in WNATIVE]
    native[0][1][3] = "0xffffffff"                 # a failed open natively; lifted stays 0
    r = wcmp(tmp_path, native, WLIFTED)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"]) == (1, "h_a", "open") and r["class"] == "bug"
    assert checks_failing(r) == ["open"]
    first = r["identity"]["checks"]["open"]["first"]
    assert (first["native"], first["lifted"]) == ("opens at 1 to 0xffffffff", "opens at 2")
    both = [(t, list(w)) for t, w in WLIFTED]
    both[0][1][3] = "0xffffffff"
    assert wcmp(tmp_path, native, both)["first_divergence"] is None
    native = [(t, list(w)) for t, w in WNATIVE]
    native[3][1][3:6] = ["0xffffffff"] * 3         # closed to INVALID natively, to 0 lifted
    r = wcmp(tmp_path, native, WLIFTED)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"]) == (4, "h_a", "close")
    assert checks_failing(r) == ["close"]
    first = r["identity"]["checks"]["close"]["first"]
    assert (first["native"], first["lifted"]) == ("closes at 4 to 0xffffffff", "closes at 4")


def test_the_three_checks_hold_exactly_when_the_identity_words_agree(tmp_path):
    """identity_checks' docstring and M5's note: the three hold exactly when the
    two streams agree cell for cell at the identity words, and a divergence at
    one carries a check that fails. Random streams of two handle words and a
    heap word over nulls (0, INVALID_HANDLE_VALUE), reused handle numbers and a
    lifted heap value outside the guest heap, seeded, half of them the native
    stream relabelled so that they agree (the review of 2026-09-25: identity W2,
    gate N4). Without the null in an open or a close, a failed open on one side
    and a close to another null passed all three."""
    import random
    rng = random.Random(20260925)
    header = ["tick", "a_01", "h_01", "h_02"]
    ident = {"a_01": "heap", "h_01": "handle", "h_02": "handle"}
    cols = ["a_01", "h_01", "h_02"]
    nulls = {"heap": [0], "handle": [0, 0xFFFFFFFF]}
    nat_vals = {"heap": [0x03900010, 0x03900020], "handle": [0x230, 0x22C]}
    lif_vals = {"heap": [0x04000010, 0x04000020, 0x100], "handle": [0x7D000000, 0x7F03B000]}
    kinds = [ident[c] for c in cols]
    seen = {"agree": 0, "differ": 0}
    for trial in range(1500):
        ticks = rng.randint(1, 6)
        nat = [[rng.choice(nulls[k] + nat_vals[k]) for k in kinds] for _ in range(ticks)]
        if rng.random() < 0.5:
            # relabelled: each native value its own lifted one, nulls kept, then
            # up to two cells changed at random
            relabel = {k: dict(zip(nat_vals[k], rng.sample(lif_vals[k][:2], 2))) for k in nulls}
            lif = [[relabel[k].get(v, v) for k, v in zip(kinds, row)] for row in nat]
            for _ in range(rng.randint(0, 2)):
                t, c = rng.randrange(ticks), rng.randrange(3)
                lif[t][c] = rng.choice(nulls[kinds[c]] + lif_vals[kinds[c]])
        else:
            lif = [[rng.choice(nulls[k] + lif_vals[k]) for k in kinds] for _ in range(ticks)]
        native = [(t, [f"0x{v:08x}" for v in row]) for t, row in enumerate(nat, 1)]
        lifted = [(t, [f"0x{v:08x}" for v in row]) for t, row in enumerate(lif, 1)]
        n = T.canonical(header, native, ident)
        lf = T.canonical(header, lifted, ident, lifted=True)
        agree = all(a == b for (_, x), (_, y) in zip(n, lf) for a, b in zip(x, y))
        checks = T.identity_checks(T.events(header, n, ident), T.events(header, lf, ident),
                                   cols, {}, ticks)
        held = all(v["ok"] for v in checks.values())
        assert held == agree, (trial, nat, lif, checks)
        seen["agree" if agree else "differ"] += 1
        if trial % 10 == 0 and not agree:
            r = T.compare(header, native, header, lifted, {}, [], tmp_path, residues=tmp_path,
                          name="t", identity=dict(ident))
            d = r["first_divergence"]
            assert d["field"] in ident and not r["identity"]["checks"][d["check"]]["ok"], (
                trial, nat, lif, d)
    assert min(seen.values()) > 300, seen


def test_the_runtime_s_reuse_of_a_freed_number_agrees_too(tmp_path):
    """The runtime's file layer hands a freed handle number out again, as Wine
    does (runtime/sa2_files.c handle_alloc takes the lowest free slot; the
    lifted Metal Harbor run reopens entry 0 as 0x7d000000 five times), so the
    lifted side is released too: here Wine gives the reopened file a new number
    and the runtime its old one (the identity review of 2026-09-25, N1)."""
    native = [(t, list(w)) for t, w in WNATIVE]
    for k in (4, 5):
        native[k][1][3:6] = [f"0x{F + 4:08x}", f"0x{F + 4:08x}", f"0x{E + 4:08x}"]
    lifted = [(t, list(w)) for t, w in WLIFTED]
    for k in (4, 5):
        lifted[k][1][3:6] = [f"0x{LF:08x}", f"0x{LF:08x}", f"0x{LE:08x}"]
    r = wcmp(tmp_path, native, lifted)
    assert r["first_divergence"] is None and checks_failing(r) == []
    assert "5 h_a 0x00000000 0x00000003" in r["identity"]["lifted_events"]


def test_a_heap_word_that_goes_null_and_returns_keeps_its_number(tmp_path):
    """Decision 2 confirms heap identity as it stood: a heap value keeps its number
    for good; only handle numbers are released (ruling 1). A heap word that goes
    null and comes back to the same address natively, and to another object
    lifted, is a divergence: the pairing fails (the U5 fold review, W1; releasing
    heap numbers would hide a re-set pointer, and is the user's to allow)."""
    native = [(t, list(w)) for t, w in WNATIVE]
    lifted = [(t, list(w)) for t, w in WLIFTED]
    native[2][1][1] = lifted[2][1][1] = "0x00000000"
    for k in (3, 4, 5):
        lifted[k][1][1] = f"0x{HEAP + 0x40:08x}"
    r = wcmp(tmp_path, native, lifted)
    assert r["first_divergence"] is not None and "pairing" in checks_failing(r)


def test_a_heap_word_that_comes_back_to_its_address_is_one_clean_stream():
    """The fold review's W2: with heap numbers kept, a heap word A, 0, A numbers
    as 1, 0, 1, and the event check finds nothing wrong with it."""
    hdr = ["tick", "g_heap"]
    rows = [(1, [f"0x{HEAP:08x}"]), (2, ["0x00000000"]), (3, [f"0x{HEAP:08x}"])]
    canon = T.canonical(hdr, rows, {"g_heap": "heap"})
    assert [w[0] for _, w in canon] == ["0x00000001", "0x00000000", "0x00000001"]
    assert T.event_problems(T.events(hdr, canon, {"g_heap": "heap"}), {"g_heap": "heap"}) == []

def test_an_open_a_tick_apart_between_native_runs_is_unrepeatable_not_a_bug(tmp_path):
    """Metal Harbor's native runs open some files a tick apart; the boot's
    v14c-stackpad opened entry 1 on 388 against v14a's 389. The repeat diverges
    there under the one rule, so the replay is judged to the tick before it,
    and a lifted open on either tick is not judged."""
    repeat = [(t, list(w)) for t, w in WNATIVE]
    for c in (3, 4, 5):
        repeat[1][1][c] = "0x00000000"             # the repeat opens on 3
    e = {"name": "x", "native": {"capture": "a/c.csv", "sha256": "0",
                                 "repeats": [{"capture": "b/c.csv", "sha256": "1"}]}}
    det = T.native_repeats(e, WHEADER, WNATIVE, tmp_path,
                           streams=[(e["native"]["repeats"][0], WHEADER, repeat)],
                           identity=dict(WIDENT))
    rep = det["repeats"][0]
    assert det["judged_ticks"] == 1
    assert (rep["first_divergent_tick"], rep["first_divergent_field"]) == (2, "h_a")
    assert rep["identity"]["open"]["ok"] is False and rep["identity"]["pairing"]["ok"] is True
    late = [(t, list(w)) for t, w in WLIFTED]
    for c in (3, 4, 5):
        late[1][1][c] = "0x00000000"
    r = wcmp(tmp_path, WNATIVE, late, judged=det["judged_ticks"])
    d = r["first_divergence"]
    assert (d["tick"], d["kind"], r["class"]) == (2, "native_unrepeated", "unrepeatable")


def test_a_lifted_heap_value_outside_the_guest_heap_pairs_with_nothing(tmp_path):
    lifted = [(t, [w[0], "0xdeadbeef"] + w[2:]) for t, w in WLIFTED]
    r = wcmp(tmp_path, WNATIVE, lifted)
    assert (r["first_divergence"]["field"], r["first_divergence"]["check"]) == ("g_heap",
                                                                                "pairing")
    assert "outside the guest heap" in r["identity"]["checks"]["pairing"]["first"]["why"]


def test_a_residue_label_on_a_value_the_host_chooses_is_refused(tmp_path):
    label = [{"tick": 3, "field": "h_a", "residue": "fsin-tail-v1"}]
    with pytest.raises(SystemExit, match="compared by what it names"):
        wcmp(tmp_path, WNATIVE, WLIFTED, labels=label)


def test_recorded_events_must_be_ones_the_numbering_writes():
    named = {"g_heap": "heap"}
    good = ["1 h_a 0x00000000 0x00000001", "2 h_a 0x00000001 0x00000000",
            "3 h_a 0x00000000 0x00000002"]
    assert T.event_problems(good, named) == []
    back = good[:2] + ["3 h_a 0x00000000 0x00000001"]
    assert any("comes back after its release" in p for p in T.event_problems(back, named))
    skip = ["1 h_a 0x00000000 0x00000002"]
    assert any("before number 1" in p for p in T.event_problems(skip, named))
    torn = ["1 h_a 0x00000000 0x00000001", "2 h_a 0x00000003 0x00000000"]
    assert any("held 0x00000001" in p for p in T.event_problems(torn, named))


def identity_doc(name, native, lifted, judged, cols=("h_a",)):
    return {"name": name, "result": {"identity": {
        "columns": list(cols), "named": {}, "judged_ticks": judged,
        "native_events": native, "lifted_events": lifted,
        "checks": T.identity_checks(native, lifted, list(cols), {}, judged)}}}


def test_identity_problems_recompute_each_check_from_the_recorded_events():
    ok = identity_doc("r", ["1 h_a 0x00000000 0x00000001"], ["1 h_a 0x00000000 0x00000001"], 2)
    assert T.identity_problems({"replays": [ok]}, {"replays": []}) == []
    late = identity_doc("r", ["1 h_a 0x00000000 0x00000001"], ["2 h_a 0x00000000 0x00000001"], 3)
    assert T.identity_problems({"replays": [late]}, {"replays": []}) == [
        "r: the open check fails at tick 1 h_a (native opens at 1, lifted opens at 2)"]
    lied = identity_doc("r", ["1 h_a 0x00000000 0x00000001"], ["2 h_a 0x00000000 0x00000001"], 3)
    lied["result"]["identity"]["checks"] = ok["result"]["identity"]["checks"]
    got = T.identity_problems({"replays": [lied]}, {"replays": []})
    assert "r: its recorded identity checks are not what its streams give" in got
    assert T.identity_problems({"replays": [{"name": "r", "result": {}}]}, {"replays": []}) == [
        "r: compared without the identity rule (no identity record in its result), so values "
        "the host chooses were compared by their bits"]


def test_the_gate_s_injected_replays_each_fail_their_own_check_alone():
    """M5's falsifiers in Documentation/gate-criteria.json that prepend a replay
    to the receipt: each must fail the check on its own clause and nothing else.
    A check's first falsifier records the verdicts identity_checks gives, so
    only that check's failure is reported; the others record all three clean,
    so the recompute disagrees with the record as well (the gate review of
    2026-09-25, W2); the numbering clause's fail on the comparison missing or
    on its streams alone (W3)."""
    crit = json.loads((ROOT / "Documentation" / "gate-criteria.json").read_text())
    m5 = next(c for c in crit["G4"] if c["criterion"].startswith("M5:"))
    seen: dict[str, list[list[str]]] = {}
    for clause in m5["clauses"]:
        if "names: " not in clause["promise"]:
            continue                 # the residue clause's: their own test, below
        for f in clause["falsifiers"]:
            if f.get("replace") != '"replays": [':
                continue
            doc = json.loads("[" + f["with"][len(f["replace"]):].rstrip(", ") + "]")[0]
            got = T.identity_problems({"replays": [doc]}, {"replays": []})
            seen.setdefault(clause["promise"].split("names: ", 1)[1], []).append(got)
    carries = seen.pop("every replay carries the comparison, in streams the numbering writes")
    assert [len(g) for g in carries] == [1, 1, 2], carries
    assert "compared without the identity rule" in carries[0][0]
    assert "the lifted stream: tick 1 h_01755220: number 2 of kind handle before number 1" \
        in carries[1][0]
    # both streams bring number 1 back, so that the three checks hold
    assert [p.split(": ", 1)[1] for p in carries[2]] == [
        f"the {side} stream: tick 3 h_01755220: number 1 of kind handle comes back after its "
        "release" for side in ("native", "lifted")]
    assert sorted(seen) == ["the close tick", "the open tick", "the pairing"]
    recorded = "its recorded identity checks are not what its streams give"
    for promise, got in seen.items():
        check = promise.split()[1]
        assert len(got) == (2 if check == "pairing" else 3), got
        for k, problems in enumerate(got):
            fails = [p for p in problems if " check fails at " in p]
            assert len(fails) == 1 and f"the {check} check fails" in fails[0], problems
            rest = [p for p in problems if p not in fails]
            assert rest == ([] if k == 0 else [f"{problems[0].split(':')[0]}: {recorded}"]), \
                problems


def test_the_gate_falsifies_a_word_policy_replay_eight_ways(monkeypatch):
    """check_replay_results.word_cases, for a registry whose first replay has a
    word policy and a receipt carrying the identity comparison: eight cases, the
    last added for the recorded native determinism, which only the recomputed
    repeats can see (the gate review of 2026-09-25, W4). A word_cases that
    returned nothing would print OK with the other cases alone."""
    import check_replay_results as R
    monkeypatch.setattr(R, "live_tree", lambda mutate: {"moved": True})
    registry = {"replays": [{"name": "boot", "word_policy": {},
                             "native": {"word_log": {"sha256": "1"},
                                        "repeats": [{"word_log": "a"}, {"word_log": "b"}]}}]}
    receipt = {"replays": [{"name": "boot", "native": {"determinism": {"judged_ticks": 387,
               "repeats": [{"first_divergent_tick": None}, {"first_divergent_tick": 388}]}},
               "lifted": {"words": {"sha256": "2"}},
               "result": {"identity": {"lifted_events": ["1 h_a 0x00000000 0x00000001"],
                                       "checks": {"open": {"mismatches": 0}}}}}]}
    base = {"registry": registry, "receipt": receipt, "live": {}}

    def case(name, part, f=None, value=None, outputs=False, names=None):
        docs = json.loads(json.dumps(base))
        if f is not None:
            f(docs[part])
        else:
            docs[part] = value
        return name, dict(docs, outputs=outputs, names=names)
    got = dict(R.word_cases(base, case, lambda rel: rel))
    assert list(got) == [
        "a recorded identity event was edited", "the recorded identity checks were edited",
        "the recorded native determinism was edited", "a native word log was re-pinned",
        "two repeats' word logs registered for each other",
        "the lifted word log was re-pinned", "the identity record changed",
        "the word-log reader changed", "boot's own digest mask changed"]
    # the boot's own mask moved (the U6 registration review, W5), named as the stale boot
    assert got["boot's own digest mask changed"]["names"] == ("replay boot is stale",)
    moved = got["the recorded native determinism was edited"]
    assert moved["receipt"]["replays"][0]["native"]["determinism"]["repeats"][0] == {
        "first_divergent_tick": 1000}
    assert moved["names"] == ("native repeats no longer judge",) and moved["outputs"]
    assert moved["receipt"]["replays"][0]["native"]["determinism"]["judged_ticks"] == 387
    receipt["replays"][0]["result"] = {}
    assert R.word_cases(base, case, lambda rel: rel) == []


def test_the_field_policy_and_the_identity_record_are_one_rule(tmp_path):
    entry = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"][0]
    spec = T.identity_spec(entry)
    assert spec["named"] == {"g_01a557c0": "heap", "g_01af19f0": "heap"}
    assert len(spec["extra"]) == 320 and {k for _, _, k in spec["extra"]} == {"handle"}
    assert spec["extra"][0][:2] == ("h_01755220", 0x01755220)
    one = dict(entry, field_policy={"g_01a557c0": entry["field_policy"]["g_01a557c0"]})
    with pytest.raises(SystemExit, match="one rule reads one"):
        T.identity_spec(one)
    other = dict(entry, word_policy=dict(entry["word_policy"], identity="elsewhere.json"))
    with pytest.raises(SystemExit, match="must name the identity record"):
        T.identity_spec(other)


def test_the_boot_s_snapshot_mask_leaves_its_four_handle_words(tmp_path):
    """tick_digest.h held entry 0's four handle words; the recompute's mask is
    it less those, and a word-log mask of the replay's own would be used as it
    is (`mask_for`)."""
    entry = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"][0]
    spec = T.identity_spec(entry)
    mask, info = T.effective_mask(entry, spec, tmp_path)   # no classes given: the residue words below
    words = T.OM.read_mask_words(mask)
    assert not words & ({va for _, va, _ in spec["extra"]} | {0x01a557c0, 0x01af19f0})
    assert len(words) == info["words"]
    if info["mask"] == T.BOOT_MASK:                        # until U6 lands masks/boot.h
        assert info["identity_words_removed"] == 4
        assert 0x01755008 in words and 0x0175500c in words     # in-use and read-request stay
        assert len(words) == len(T.OM.read_mask_words(ROOT / T.BOOT_MASK)) - 4
    else:                                                  # a word-log mask, used as it is
        assert info["mask"] == "harness/replay/masks/boot.h" and mask == ROOT / info["mask"]
        assert info["identity_words_removed"] == 0


def test_a_heap_value_and_a_handle_number_of_the_same_bits_are_two_values(tmp_path):
    """Heap values and handle values are numbered apart: the runtime may hand a
    handle the bits of one of its heap addresses, and that names nothing twice."""
    lifted = [(t, [w[0], w[1], w[2], f"0x{HEAP:08x}" if int(w[3], 16) else w[3], *w[4:]])
              for t, w in WLIFTED]
    for t, w in lifted:
        if int(w[4], 16):
            w[4] = f"0x{HEAP:08x}"                 # h_b holds what h_a holds, as natively
    assert wcmp(tmp_path, WNATIVE[:3], lifted[:3])["first_divergence"] is None


def test_a_stream_from_a_word_log_is_its_capture_and_its_identity_words(tmp_path):
    """log_stream: the tick capture the log gives under the mask, then one
    column per identity word, every tick; a native log is held to the native
    probe's rules (here: a log without the probe's own blocks is refused)."""
    import test_snapshot_fields as TSF
    log = TSF.write_log(tmp_path / "n.bin", TSF.base_ticks())
    extra = [("h_01755220", 0x01755220, "handle"), ("h_01755254", 0x01755254, "handle")]
    header, got = T.log_stream(log, ROOT / T.BOOT_MASK, extra, native=True)
    names = [n for n, _, _ in T.WL.named_fields()]
    assert header[:1 + len(names)] == ["tick", *names] and header[-2:] == ["h_01755220",
                                                                         "h_01755254"]
    assert [w[-2:] for _, w in got[2:4]] == [["0x00000000"] * 2, ["0x00000230", "0x00000000"]]
    assert len(got) == TSF.TICKS and all(len(w) == len(header) - 1 for _, w in got)
    bare = TSF.write_log(tmp_path / "bare.bin", TSF.base_ticks(), own=None)
    with pytest.raises(SystemExit, match="blocks of its own"):
        T.log_stream(bare, ROOT / T.BOOT_MASK, extra, native=True)
    assert T.log_stream(bare, ROOT / T.BOOT_MASK, extra, native=False)[1][3][1][-2] == "0x00000230"


def registered_boot_doc() -> tuple[dict, dict]:
    """The registry, and a boot result whose identity comparison holds under the
    tree's record and mask: the shape identity_problems holds a registered replay
    to (test_criterion_clauses.py builds the same for the gate)."""
    registry = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    boot = next(e for e in registry["replays"] if e["name"] == "boot")
    spec = T.identity_spec(boot)
    fields = [n for n, _, _ in T.WL.named_fields(ROOT / "harness/replay/tick_fields.h")]
    columns = [n for n in fields if n in spec["named"]] + [c for c, _, _ in spec["extra"]]
    events = ["1 g_01a557c0 0x00000000 0x00000001", "380 h_01755254 0x00000000 0x00000001"]
    mask = T.mask_for(boot)
    words = T.OM.read_mask_words(mask)
    record = {va for _, va, _ in spec["extra"]} | {0x01a557c0, 0x01af19f0}
    doc = {"name": "boot", "result": {
        "compared_ticks": 400,
        "identity": {"columns": columns, "named": dict(spec["named"]), "judged_ticks": 400,
                     "native_events": events, "lifted_events": list(events),
                     "checks": T.identity_checks(events, events, columns, spec["named"], 400),
                     "record": dict(spec["record"])},
        "mask": {"mask": str(mask.relative_to(ROOT)), "mask_sha256": T.sha256(mask),
                 "identity_words_removed": len(words & record), "words": len(words - record)}}}
    return registry, doc


@pytest.mark.parametrize("edit,why", [
    (lambda d: d["identity"]["record"].__setitem__("sha256", "0" * 64),
     "an identity record that is not the tree's"),
    (lambda d: d["identity"]["columns"].pop(), "identity columns are not the 322 words"),
    (lambda d: d["identity"]["named"].__setitem__("g_01a557c0", "handle"),
     "identity columns are not the 322 words"),
    (lambda d: d["mask"].__setitem__("identity_words_removed",
                                     d["mask"]["identity_words_removed"] + 1),
     "were not recomputed under harness/replay/"),
    # another mask than mask_for's, whichever the boot has (masks/boot.h since U6)
    (lambda d: d["mask"].__setitem__("mask", "harness/replay/tick_digest.h"
                                     if d["mask"]["mask"] != "harness/replay/tick_digest.h"
                                     else "harness/replay/masks/boot.h"),
     "were not recomputed under harness/replay/"),
    # the U5 fold review, W3: the mask's sha256 and the words it kept, each alone
    (lambda d: d["mask"].__setitem__("mask_sha256", "0" * 64),
     "were not recomputed under harness/replay/"),
    (lambda d: d["mask"].__setitem__("words", d["mask"]["words"] + 1),
     "were not recomputed under harness/replay/"),
    # the identity review of 2026-09-25, N2: the checks judged the ticks compared
    (lambda d: d["identity"].__setitem__("judged_ticks", 0),
     "its identity checks judged 0 ticks, and the replay compared 400"),
])
def test_a_registered_replay_is_held_to_the_record_its_columns_and_its_mask(edit, why):
    """Each of identity_problems' holds on a registered replay, alone: the
    record's sha256, the columns and kinds it names, the mask cut, and the
    ticks its checks judged (the ones it compared: M5's check alone would
    otherwise take a receipt's judged_ticks of 0 and three empty checks)."""
    registry, doc = registered_boot_doc()
    assert T.identity_problems({"replays": [doc]}, registry) == []
    edit(doc["result"])
    got = T.identity_problems({"replays": [doc]}, registry)
    assert len(got) == 1 and why in got[0], got


def test_a_word_policy_run_keeps_its_word_log_checked_beside_its_capture(tmp_path, monkeypatch):
    """run_lifted with `words`: the hook is handed SA2_REPLAY_WORDS beside the
    tick capture, and the log it wrote is held to the capture (the named fields
    on every tick, and the hook's own digest columns against the host's
    recompute) and kept compressed, its sha256 in the record. Without `words`
    nothing asks for a log. The guest is a stand-in that writes a made-up log."""
    import subprocess
    import test_lifted_saves as TLS
    import test_snapshot_fields as TSF
    import device_profile as DP
    T2 = TLS.tick(tmp_path, monkeypatch)
    real = subprocess.run
    asked = []

    def run(cmd, **kw):
        if "--out-prefix" not in cmd:
            return real(cmd, **kw)             # zstd and the log reader
        env = dict(kv.split("=", 1) for kv in cmd[cmd.index("--run") + 1].split("|")[1:])
        asked.append(env.get("SA2_REPLAY_WORDS"))
        prefix = cmd[cmd.index("--out-prefix") + 1]
        (tmp_path / "run").mkdir(parents=True, exist_ok=True)
        (tmp_path / "run" / f"{prefix}.ticks.json").write_text(json.dumps(TLS.devices_on(
            {"device_profile": {"default": True}, "pump": {"iterations": 3},
             "calls": {"total": 9}, "d3d9": {"frames": {"present": 2}}, "trap": {"note": "t"}})))
        (tmp_path / "run" / f"{prefix}.matrix.json").write_text(json.dumps({"sha256": "b" * 64}))
        csv_text = "tick,a\n1,0x1\n"
        if env.get("SA2_REPLAY_WORDS"):
            log = TSF.write_log(tmp_path / env["SA2_REPLAY_WORDS"], TSF.base_ticks(), own=None)
            csv_text = T2.WL.capture_csv(log, native=False)
        (tmp_path / env["SA2_REPLAY_TICKS"]).write_text(csv_text)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(T2.subprocess, "run", run)
    lifted = T2.run_lifted("boot", device_profile=DP.DEFAULT, words=True)
    assert asked == ["replay/boot/lifted-words.bin"]
    kept = lifted["words"]
    assert kept["path"] == "replay/boot/lifted-words.bin.zst"
    assert kept["sha256"] == T2.sha256(tmp_path / kept["path"])
    assert not (tmp_path / "replay/boot/lifted-words.bin").exists()
    assert (kept["check"]["named_cells_differing"], kept["check"]["digest_cells_differing"]) == (0, 0)
    assert kept["check"]["ticks"] == TSF.TICKS
    plain = T2.run_lifted("boot", device_profile=DP.DEFAULT)
    assert asked[-1] is None and "words" not in plain


def test_a_replay_s_own_word_log_mask_holds_its_native_side_to_the_mask_s_build(tmp_path,
                                                                               monkeypatch):
    """A word-policy entry's mask is harness/replay/masks/<name>.h once it
    exists (mask_for), and then its native side is held to the build and
    configuration the mask's sources ran with (oracle_mode
    require_mask_configuration, the U4 masks unit's request); before, the
    tree's tick_digest.h, with the mode check alone."""
    sha = vanilla_tree(tmp_path)
    for name in ("v1", "v2"):
        oracle_capture(tmp_path, name, VANILLA, probe_sha=sha)
    mask_header(tmp_path, "v1")
    entry = dict(oracle_entry("vanilla", "v1", "v2"), word_policy={})
    asked = []
    monkeypatch.setattr(T.OM, "require_mask_configuration",
                        lambda header, dirs, root=None: asked.append((header.name, len(dirs))))
    assert T.mask_for(entry, tmp_path) == tmp_path / T.BOOT_MASK
    assert T.native_oracle(entry, tmp_path) == "vanilla" and asked == []
    own = tmp_path / T.MASKS_DIR / "boot.h"
    own.parent.mkdir(parents=True)
    own.write_text((tmp_path / T.BOOT_MASK).read_text())
    monkeypatch.setattr(T.OM, "digest_mask_sources",
                        lambda header, root=None, by_receipt=False:
                        [tmp_path / "private" / "captures" / "v1"])
    assert T.mask_for(entry, tmp_path) == own
    assert T.native_oracle(entry, tmp_path) == "vanilla" and asked == [("boot.h", 2)]
    assert "harness/replay/masks/boot.h" in T.compare_pipeline(entry, tmp_path)

    def refuse_build(header, dirs, root=None):
        raise T.OM.ModeError("v2 is not configured like boot.h's sources: probes[0].defines")
    monkeypatch.setattr(T.OM, "require_mask_configuration", refuse_build)
    with pytest.raises(SystemExit, match="not one build and configuration"):
        T.native_oracle(entry, tmp_path)


def test_a_word_policy_stage_result_rests_on_its_lifted_word_log(tmp_path, stage_runs):
    """A stage entry compared from word logs keeps its observed run's log among
    the kept files stage_recheck holds to their pins, beside the tick capture;
    an entry without the policy asks for none."""
    e, lifted = recorded_stage(tmp_path, stage_runs)
    assert "words" not in lifted and T.stage_recheck(e, lifted, TICKS, tmp_path) == []
    worded = dict(e, word_policy={})
    got = T.stage_recheck(worded, lifted, TICKS, tmp_path)
    assert any("the lifted word log None is missing" in p for p in got), got
    log = Path(lifted["capture"]).parent / "lifted-words.bin.zst"
    (tmp_path / log).write_bytes(b"a kept log")
    lifted["words"] = {"path": str(log), "sha256": T.sha256(tmp_path / log)}
    assert T.stage_recheck(worded, lifted, TICKS, tmp_path) == []
    lifted["words"]["sha256"] = "0" * 64
    got = T.stage_recheck(worded, lifted, TICKS, tmp_path)
    assert any("the lifted word log" in p and "does not hash to its pin" in p for p in got), got


# The boot's stack control, a checked control outside the repeats (the U6 reviews:
# masks W3, registration W6): what it shows against the entry's mask is recorded,
# and recomputed on every pass.

def control_world(tmp_path, monkeypatch):
    """Two synthetic word logs (test_snapshot_fields' writer): the registered
    capture's, and a control's that holds W_VAR apart at tick 9 and W_STILL, which
    the mask leaves out, from tick 5; a mask of W_VAR and W_ONCE_LATE; and an entry
    registering the second as its control."""
    import test_snapshot_fields as TSF
    import snapshot_fields as SF
    base = TSF.write_log(tmp_path / "a.bin", TSF.base_ticks())
    moved_ticks = TSF.base_ticks()
    moved_ticks[9][TSF.W_VAR] = 1234
    moved_ticks[5][TSF.W_STILL] = 77        # apart outside the mask: not the control's to show
    moved = TSF.write_log(tmp_path / "b.bin", moved_ticks, clock=7_000_000)
    mask = tmp_path / "m.h"
    SF.write_mask([TSF.W_VAR, TSF.W_ONCE_LATE], [" * test"], mask)
    cap = write_file(tmp_path, "private/captures/pad/sa2-tick-capture.csv", "tick\n1\n")
    entry = {"name": "boot", "word_policy": {"identity": T.IDENTITY_RECORD,
                                             "compare": "identity", "evidence": "ruled"},
             "native": {"capture": "private/captures/v14a/sa2-tick-capture.csv", "repeats": []},
             "controls": [{"capture": cap["file"], "sha256": cap["sha256"],
                           "receipt": "private/captures/pad/receipt.json",
                           "what": "the stack control"}]}
    monkeypatch.setattr(T, "native_log", lambda e, c, root=None: base
                        if c is e["native"] else moved)
    monkeypatch.setattr(T, "identity_spec", lambda e, root=None: {"extra": [], "named": {}})
    monkeypatch.setattr(T, "effective_mask", lambda e, spec, out, root=None, classes=None: (
        mask, {"effective_sha256": T.sha256(mask)}))
    monkeypatch.setattr(T.SS, "native_start_problems", lambda d, root=None: [])
    return entry, TSF


def test_a_control_s_verdict_is_what_it_shows_against_the_mask(tmp_path, monkeypatch):
    entry, TSF = control_world(tmp_path, monkeypatch)
    got = T.control_verdict(entry, entry["controls"][0], tmp_path)
    assert (got["words"], got["first_tick"], got["last_tick"], got["blocks"],
            got["compared_through"]) == (1, 9, 9, ["0x01000000"], TSF.TICKS)
    entry["controls"][0]["verdict"] = got
    assert T.control_problems(entry, tmp_path) == []


@pytest.mark.parametrize("edit,why", [
    (lambda c: c["verdict"].__setitem__("words", 0), "no longer shows what its recorded verdict"),
    (lambda c: c["verdict"].__setitem__("listing_sha256", "0" * 64),
     "no longer shows what its recorded verdict"),
    (lambda c: c.__setitem__("sha256", "0" * 64), "does not hash to its pin"),
    (lambda c: c.pop("what"), "registers no capture, sha256 or what it controls for"),
])
def test_a_control_that_moved_is_refused(tmp_path, monkeypatch, edit, why):
    entry, _ = control_world(tmp_path, monkeypatch)
    c = entry["controls"][0]
    c["verdict"] = T.control_verdict(entry, c, tmp_path)
    edit(c)
    got = T.control_problems(entry, tmp_path)
    assert len(got) == 1 and why in got[0], got


def test_a_control_that_is_also_a_repeat_is_refused(tmp_path, monkeypatch):
    """A control judges nothing: registered among the repeats it would."""
    entry, _ = control_world(tmp_path, monkeypatch)
    c = entry["controls"][0]
    c["verdict"] = T.control_verdict(entry, c, tmp_path)
    entry["native"]["repeats"].append({"capture": c["capture"]})
    got = T.control_problems(entry, tmp_path)
    assert got == [f"boot: its control {c['capture']} is also its capture or a repeat; a "
                   "control judges nothing"]


def test_the_registered_boot_s_stack_control_is_held_against_its_mask():
    """The tree's registry keeps the stack control as a checked control, outside
    the repeats, with its verdict against the boot's own mask."""
    registry = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    boot = next(e for e in registry["replays"] if e["name"] == "boot")
    caps = [c["capture"] for c in boot.get("controls") or []]
    assert [Path(c).parent.name for c in caps] == ["boot-ticks-v15c-stackpad"]
    assert all(T.run_dir(c) not in {T.run_dir(r["capture"]) for r in
                                    [boot["native"]] + boot["native"]["repeats"]} for c in caps)
    # against the words the boot judges: its own mask less the identity words and,
    # since the residue classes (2026-09-26), their words (effective_mask)
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        _, info = T.effective_mask(boot, T.identity_spec(boot), Path(td),
                                   classes=T.residue_classes(boot))
    assert boot["controls"][0]["verdict"]["mask_sha256"] == info["effective_sha256"]
    assert info["mask_sha256"] == T.sha256(T.mask_for(boot))


def test_the_gate_keeps_a_recompute_only_while_what_it_reads_is_unchanged(tmp_path, monkeypatch):
    """check_replay_results.recompute keeps a result within a run by the entry,
    the recorded replay and the sha256 of every file they name (the U6 fold: the
    gate took 56 minutes once Metal Harbor registered). A file changed in place,
    another entry or another recorded replay is computed again."""
    import check_replay_results as R
    calls = []
    monkeypatch.setattr(R, "compute", lambda e, d: calls.append(1) or {"n": len(calls)})
    assert R.recompute({"name": "boot"}, {}) == {"n": 1}         # outside a gate run: afresh
    assert R.recompute({"name": "boot"}, {}) == {"n": 2}
    calls.clear()
    monkeypatch.setattr(R, "_RECOMPUTED", {})                     # inside one (R.kept)
    log = tmp_path / "run" / "sa2-word-log.bin.zst"
    log.parent.mkdir()
    log.write_bytes(b"one")
    entry = {"name": "boot", "native": {"word_log": {"file": str(log), "sha256": "x"}}}
    doc = {"name": "boot", "lifted": {"capture": str(tmp_path / "none.csv")}}
    assert R.recompute(entry, doc) == {"n": 1} == R.recompute(entry, doc) and len(calls) == 1
    log.write_bytes(b"two")                                   # the same path, other bytes
    assert R.recompute(entry, doc) == {"n": 2}
    assert R.recompute(dict(entry, name="other"), doc) == {"n": 3}
    assert R.recompute(entry, dict(doc, result={"edited": 1})) == {"n": 4}
    assert R.recompute(entry, doc) == {"n": 2} and len(calls) == 4


def test_split_reports_and_canonical_numberings_are_kept_by_content(tmp_path, monkeypatch):
    """split and canonical keep what they computed per process by their inputs'
    content (the U6 fold: a Metal Harbor recompute spent 240 of 267 seconds in
    Omochao Split): another stream, another identity map or the other side is
    computed again, and the same inputs give the same answer."""
    monkeypatch.setattr(T, "_SPLITS", {})
    monkeypatch.setattr(T, "_CANONICAL", {})
    header = ["tick", "frame", "g_a"]
    same = [(1, ["0x00000001", "0x04000010"]), (2, ["0x00000002", "0x04000010"])]
    moved = [(1, ["0x00000001", "0x04000010"]), (2, ["0x00000003", "0x04000010"])]
    agree = T.split(header, same, same, tmp_path, "a")
    differ = T.split(header, same, moved, tmp_path, "b")
    assert agree["status"] != differ["status"] and differ["status"] == "different"
    assert T.split(header, same, same, tmp_path, "c") == agree
    assert T.split(header, moved, same, tmp_path, "d")["status"] == "different"
    heap = {"g_a": "heap"}
    native = T.canonical(header, same, heap)
    assert native[0][1][1] == "0x00000001"                        # the first heap number
    assert T.canonical(header, same, {}) == same                  # no identity map: as it is
    far = [(1, ["0x00000001", "0x00000010"])]                      # below the guest heap
    assert T.canonical(header, far, heap, lifted=True)[0][1][1] == T.OUT_OF_HEAP
    assert T.canonical(header, far, heap)[0][1][1] == "0x00000001"
    kept = T.canonical(header, same, heap)
    kept[0][1][1] = "edited"                                       # a caller's copy
    assert T.canonical(header, same, heap) == native


def test_a_log_stream_is_kept_by_the_log_s_bytes(tmp_path, monkeypatch):
    """log_stream keeps a stream by the log's and the mask's content, not their
    paths: a log rewritten in place is read again, and a copy elsewhere is the
    same stream (the U6 fold)."""
    import test_snapshot_fields as TSF
    monkeypatch.setattr(T, "_STREAMS", {})
    log = TSF.write_log(tmp_path / "n.bin", TSF.base_ticks())
    extra = [("h_01755220", 0x01755220, "handle")]
    first = T.log_stream(log, ROOT / T.BOOT_MASK, extra, native=False)
    moved_ticks = TSF.base_ticks()
    moved_ticks[4][0x01755220] = 0x444
    TSF.write_log(log, moved_ticks)                                # the same path, other bytes
    second = T.log_stream(log, ROOT / T.BOOT_MASK, extra, native=False)
    assert second != first and second[1][3][1][-1] == "0x00000444"
    copy = tmp_path / "copy.bin"
    copy.write_bytes(log.read_bytes())
    assert T.log_stream(copy, ROOT / T.BOOT_MASK, extra, native=False) == second


def test_a_control_that_did_not_start_from_the_pinned_saves_is_refused(tmp_path, monkeypatch):
    entry, _ = control_world(tmp_path, monkeypatch)
    c = entry["controls"][0]
    c["verdict"] = T.control_verdict(entry, c, tmp_path)
    monkeypatch.setattr(T.SS, "native_start_problems",
                        lambda d, root=None: ["started from other saves"])
    assert T.control_problems(entry, tmp_path) == [
        f"boot: its control {c['capture']}: started from other saves"]


def test_controls_on_an_entry_without_a_word_policy_are_refused(tmp_path, monkeypatch):
    entry, _ = control_world(tmp_path, monkeypatch)
    entry["controls"][0]["verdict"] = T.control_verdict(entry, entry["controls"][0], tmp_path)
    entry.pop("word_policy")
    got = T.control_problems(entry, tmp_path)
    assert len(got) == 1 and "has no word_policy" in got[0], got


def test_the_gate_falsifies_a_registered_control_s_verdict(monkeypatch):
    """The control's case (the U6 reviews, masks W3): a registry control's recorded
    verdict edited, named by the sentence only the recomputed verdict writes."""
    import check_replay_results as R
    monkeypatch.setattr(R, "live_tree", lambda mutate: {"moved": True})
    registry = {"replays": [{"name": "boot", "word_policy": {},
                             "native": {"word_log": {"sha256": "1"},
                                        "repeats": [{"word_log": "a"}, {"word_log": "b"}]},
                             "controls": [{"capture": "c", "verdict": {"words": 216}}]}]}
    receipt = {"replays": [{"name": "boot", "native": {"determinism": {"judged_ticks": 387,
               "repeats": [{"first_divergent_tick": None}, {"first_divergent_tick": 388}]}},
               "lifted": {"words": {"sha256": "2"}},
               "result": {"identity": {"lifted_events": ["1 h_a 0x00000000 0x00000001"],
                                       "checks": {"open": {"mismatches": 0}}}}}]}
    base = {"registry": registry, "receipt": receipt, "live": {}}

    def case(name, part, f=None, value=None, outputs=False, names=None):
        docs = json.loads(json.dumps(base))
        if f is not None:
            f(docs[part])
        else:
            docs[part] = value
        return name, dict(docs, outputs=outputs, names=names)
    got = dict(R.word_cases(base, case, lambda rel: rel))
    moved = got["boot's control verdict was edited"]
    assert moved["names"] == ("no longer shows what its recorded verdict",) and moved["outputs"]
    assert moved["registry"]["replays"][0]["controls"][0]["verdict"]["words"] == 217


# ------------------------------------ the U6 fold review's survivors (fold two)

def test_a_control_s_verdict_names_its_words_not_only_how_many(tmp_path, monkeypatch):
    """The fold review's TR6: the verdict carries its listing's sha256, so a
    control that holds another mask word apart, on the same tick and in the same
    block, shows something else and is refused."""
    import hashlib
    entry, TSF = control_world(tmp_path, monkeypatch)
    c = entry["controls"][0]
    c["verdict"] = T.control_verdict(entry, c, tmp_path)
    assert set(c["verdict"]) == {"mask_sha256", "compared_through", "words", "first_tick",
                                 "last_tick", "blocks", "listing_sha256"}
    listing = [[f"{TSF.W_VAR:#010x}", 9, 9]]
    assert c["verdict"]["listing_sha256"] == hashlib.sha256(
        json.dumps(listing).encode()).hexdigest()
    other_ticks = TSF.base_ticks()
    other_ticks[9][TSF.W_ONCE_LATE] = 1234     # another mask word: tick 9, block 0x01000000
    other = TSF.write_log(tmp_path / "c.bin", other_ticks, clock=9_000_000)
    base = T.native_log(entry, entry["native"], tmp_path)
    monkeypatch.setattr(T, "native_log", lambda e, cc, root=None: base
                        if cc is e["native"] else other)
    now = T.control_verdict(entry, c, tmp_path)
    assert now != c["verdict"] and {k: v for k, v in now.items() if k != "listing_sha256"} \
        == {k: v for k, v in c["verdict"].items() if k != "listing_sha256"}
    got = T.control_problems(entry, tmp_path)
    assert len(got) == 1 and "no longer shows what its recorded verdict" in got[0], got


def test_the_gate_s_kept_recompute_reads_the_lifted_run_s_files_and_the_tree_s(
        tmp_path, monkeypatch):
    """The fold review's CR2 and CR3: a kept recompute's key holds the sha256 of
    every file the recorded lifted run names and of the comparison's own tree
    files (the replay's mask, the identity record), so each rewritten in place
    is computed again."""
    import check_replay_results as R
    calls = []
    monkeypatch.setattr(R, "compute", lambda e, d: calls.append(1) or {"n": len(calls)})
    monkeypatch.setattr(R, "_RECOMPUTED", {})
    monkeypatch.setattr(R, "ROOT", tmp_path)
    mask, ident = tmp_path / T.MASKS_DIR / "boot.h", tmp_path / T.IDENTITY_RECORD
    for p in (mask, ident):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("one")
    monkeypatch.setattr(T, "mask_for", lambda e, root=None: mask)
    words = tmp_path / "run" / "lifted-words.bin.zst"
    words.parent.mkdir()
    words.write_bytes(b"one")
    entry = {"name": "boot", "word_policy": {}}
    doc = {"name": "boot", "lifted": {"words": {"path": "run/lifted-words.bin.zst",
                                                "sha256": "x"}}}
    assert R.recompute(entry, doc) == {"n": 1} == R.recompute(entry, doc)
    words.write_bytes(b"two")                   # the lifted run's word log, in place
    assert R.recompute(entry, doc) == {"n": 2}
    mask.write_text("two")                      # the replay's own mask, in place
    assert R.recompute(entry, doc) == {"n": 3}
    ident.write_text("two")                     # the identity record, in place
    assert R.recompute(entry, doc) == {"n": 4}
    assert R.recompute(entry, doc) == {"n": 4} and len(calls) == 4
    # a residue class record the entry names by id (2026-09-26), in place
    rec = tmp_path / "Documentation" / "receipts" / "residues" / "host-timing-v1.json"
    rec.parent.mkdir(parents=True)
    rec.write_text("one")
    classed = dict(entry, residue_classes=["host-timing-v1"])
    assert R.recompute(classed, doc) == {"n": 5} == R.recompute(classed, doc)
    rec.write_text("two")
    assert R.recompute(classed, doc) == {"n": 6}


def test_the_falsifications_leave_no_scratch_when_they_stop(tmp_path, monkeypatch):
    """The fold review's nit: the moved import probe was a file of its own in the
    temporary directory, removed only once the cases had run, and two passes
    stopped by hand left theirs in /tmp. It and the copied runs are now made in
    the falsifications' scratch, which falsifications() removes however the
    pass ends: here, an exception after both were made."""
    import tempfile
    import check_replay_results as R
    import native_imports as NI
    (tmp_path / "tmp").mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    run = tmp_path / "private" / "captures" / "r1"
    run.mkdir(parents=True)
    (run / "receipt.json").write_text("{}")
    monkeypatch.setattr(R, "ROOT", tmp_path)
    made = []

    def stops(scratch):
        made.append(R.moved_probe(scratch))
        made.append(R.copied_run("private/captures/r1", scratch, lambda c: None))
        assert made[0].read_bytes() == NI.PROBE.read_bytes() + b"\n"
        assert (made[1] / "receipt.json").is_file()
        raise RuntimeError("stopped")
    monkeypatch.setattr(R, "_falsifications", stops)
    with pytest.raises(RuntimeError, match="stopped"):
        R.falsifications()
    assert len(made) == 2 and list((tmp_path / "tmp").iterdir()) == []

    def ends(scratch):                          # and a pass that ends as passes do
        made.append(R.moved_probe(scratch))
        made.append(R.copied_run("private/captures/r1", scratch, lambda c: None))
        return []
    monkeypatch.setattr(R, "_falsifications", ends)
    assert R.falsifications() == []
    assert len(made) == 4 and list((tmp_path / "tmp").iterdir()) == []


def test_a_gate_pass_stopped_with_sigterm_removes_its_scratch(tmp_path):
    """A pass stopped by hand (kill, timeout) never ran the falsifications'
    `finally`: main() raises SIGTERM as `Stopped` and returns 143, so it does,
    and gives the process back the handler it had."""
    import subprocess
    import textwrap
    scratch = tmp_path / "scratch"
    code = textwrap.dedent(f"""
        import os, signal, sys, time
        from pathlib import Path
        sys.path[:0] = [{str(ROOT / 'Documentation' / 'scripts')!r},
                        {str(ROOT / 'tools' / 's4')!r}]
        import check_replay_results as R

        def stopped():
            d = Path({str(scratch)!r})
            d.mkdir()
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(60)
            finally:
                d.rmdir()
        R.falsifications = stopped
        R.problems = lambda: []             # a broken handler must not start a real pass
        before = signal.getsignal(signal.SIGTERM)
        try:
            sys.exit(R.main())                  # as the script's own entry does
        finally:
            assert signal.getsignal(signal.SIGTERM) is before
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 143 and not scratch.exists(), (r.returncode, r.stderr[-2000:])


def test_a_sigterm_inside_a_check_that_catches_systemexit_still_stops_the_pass(tmp_path):
    """The checks catch SystemExit, the tools' refusal (the recompute among them,
    where a pass spends its time), so the stop must not be one: here the stop
    lands inside such a check, and the pass still ends with 143."""
    import subprocess
    import textwrap
    code = textwrap.dedent(f"""
        import os, signal, sys, time
        sys.path[:0] = [{str(ROOT / 'Documentation' / 'scripts')!r},
                        {str(ROOT / 'tools' / 's4')!r}]
        import check_replay_results as R

        def a_check():
            try:
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(60)
            except (SystemExit, Exception) as e:      # a refusal or an error, as checks catch them
                return [f"cannot be recomputed: {{e}}"]
            return []
        R.falsifications = a_check
        R.problems = lambda: []
        sys.exit(R.main())                      # as the script's own entry does
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 143, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])


def test_nothing_on_the_gate_s_path_catches_the_stop():
    """main() stops a pass by raising `Stopped`, a BaseException, because the
    checks catch SystemExit, the tools' refusal, and recorded a SIGTERM as one
    more problem (the second fold review, W1). The test above holds it against
    a stand-in check; this holds the premise against the real ones: no module
    the gate loads catches BaseException, by name or with a bare `except:`,
    unless the handler raises it again."""
    import ast
    import subprocess
    code = ("import sys, importlib\n"
            f"sys.path[:0] = [{str(ROOT / 'Documentation' / 'scripts')!r}, "
            f"{str(ROOT / 'tools' / 's4')!r}]\n"
            "import check_replay_results\n"
            # what the gate and the tools it calls import inside functions
            "for m in ('tick_replay', 'import_status', 'native_imports', 'native_outcomes',\n"
            "          'refusal_audit', 'wordlog', 'snapshot_fields', 'stage_boot', 'stage_run',\n"
            "          'record_stage', 'rebuild_image', 'device_profile'):\n"
            "    importlib.import_module(m)\n"
            "print('\\n'.join(sorted({m.__file__ for m in list(sys.modules.values())\n"
            "                         if getattr(m, '__file__', None)})))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    ours = (ROOT / "tools", ROOT / "Documentation" / "scripts")
    files = sorted({Path(f).resolve() for f in r.stdout.split("\n") if f.endswith(".py")
                    and any(Path(f).resolve().is_relative_to(d) for d in ours)})

    def catches_the_stop(h):
        if h.type is None:
            return True
        kinds = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
        return any(getattr(k, "id", getattr(k, "attr", None)) == "BaseException" for k in kinds)
    def escapes(block):
        """A way out of `block` other than the exception carrying on: a return,
        or a break or continue whose loop is outside the block. A handler that
        ends in `raise` but can return first still swallows the stop, and so
        does a finally that can leave (the third fold review). Nested functions
        and classes are scopes of their own."""
        stack = [(s, False) for s in block]
        while stack:
            n, in_loop = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(n, ast.Return) or (isinstance(n, (ast.Break, ast.Continue))
                                             and not in_loop):
                return True
            for field, v in ast.iter_fields(n):
                inner = in_loop or (isinstance(n, (ast.For, ast.AsyncFor, ast.While))
                                    and field == "body")
                stack.extend((c, inner) for c in (v if isinstance(v, list) else [v])
                             if isinstance(c, ast.AST))
        return False
    swallowing = []
    for f in files:
        for node in ast.walk(ast.parse(f.read_text())):
            if isinstance(node, ast.ExceptHandler) and catches_the_stop(node) and (
                    not (isinstance(node.body[-1], ast.Raise) and node.body[-1].exc is None)
                    or escapes(node.body)):
                swallowing.append(f"{f.relative_to(ROOT)}:{node.lineno}")
            if isinstance(node, (ast.Try, ast.TryStar)) and escapes(node.finalbody):
                swallowing.append(f"{f.relative_to(ROOT)}:{node.lineno} (leaves its finally)")
    assert {"check_replay_results.py", "tick_replay.py", "import_status.py", "wordlog.py",
            "native_imports.py"} <= {f.name for f in files}
    assert swallowing == []


def test_the_gate_makes_its_scratch_through_moved_probe_and_copied_run_alone():
    """What a pass writes outside the tree is removed because moved_probe and
    copied_run put it in the scratch, which the scratch tests above hold for
    both. A directory made anywhere else is not: started_elsewhere made its
    own until the second fold review (nit 1), and a mutation leaving it out of
    the scratch survived every test. So every call in the gate that makes a
    temporary path which outlives its call is in one of the two."""
    import ast
    lasting = {"mkdtemp", "mkstemp", "mktemp", "NamedTemporaryFile", "gettempdir", "gettempdirb"}
    sites = []

    def visit(node, fn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Call):
                f = child.func
                if (f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)) in lasting:
                    sites.append(fn or "<module>")
            visit(child, fn)
    visit(ast.parse((ROOT / "Documentation" / "scripts" / "check_replay_results.py").read_text()),
          None)
    assert sorted(sites) == ["copied_run", "moved_probe"]


# ------------------------------------------------ residue classes of words (2026-09-26)
#
# The user's direction of 2026-09-26, decisions 2 and 3
# (Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md): a host-services
# class for the Steam achievement table and a host-timing class for words whose value
# depends on when background work finished. Each rule below is shown failing when broken.

STEAM, TIMING = "host-services-steam-stats-v1", "host-timing-v1"
RACE_ID = "host-thread-race-sofdec-v1"      # the user's ruling of 2026-09-27: a class of its own
RESIDUES_DIR = ROOT / "Documentation" / "receipts" / "residues"


def record(rid):
    return json.loads((RESIDUES_DIR / f"{rid}.json").read_text())


def test_a_table_definition_gives_its_words_in_address_order():
    assert T.table_words({"at": ["0x0000000c", "0x00000004"]}) == [4, 12]
    got = T.table_words({"base": "0x100", "stride": "0x10", "count": 2,
                         "offsets": {"from": "0x4", "to": "0xc", "step": 4}})
    assert got == [0x104, 0x108, 0x10c, 0x114, 0x118, 0x11c]
    assert T.table_words({"base": "0x100", "stride": "0x10", "count": 2,
                          "offsets": ["0x8"]}) == [0x108, 0x118]
    for bad in ({"at": []}, {"at": ["0x4", "0x4"]}, {"base": "0x100", "stride": "0x10",
                                                     "count": 2, "offsets": ["0x10"]},
                {"base": "0x100", "count": 1, "offsets": ["0x0"]}, {"at": ["0x4"], "x": 1},
                {"base": "0x100", "stride": "0x10", "count": 0, "offsets": ["0x0"]}):
        with pytest.raises(ValueError):
            T.table_words(bad)


def test_the_two_records_list_the_words_the_residue_reader_listed():
    """1,455 Steam words, the three loading-screen words, 720 XECI read-completion
    words, unit SF's stream-scan group (4 words, the boot's alone) and 320 file-open
    words: each generated from its table and none a problem. SF's decoder-context
    group is its own class since the user's ruling of 2026-09-27, in neither
    (`test_the_thread_race_record_is_the_ruled_scope`)."""
    steam, timing = record(STEAM), record(TIMING)
    assert [len(g["words"]) for g in steam["groups"]] == [1455]
    assert [(g["name"], len(g["words"])) for g in timing["groups"]] == [
        ("loading-screen-frames", 3), ("xeci-read-completion", 720),
        ("sofdec-opening-scan", 4)]
    assert len(timing["identity_open_shift"]["words"]) == 320
    assert T.record_problems(steam, STEAM) == [] and T.record_problems(timing, TIMING) == []
    # the open-shift words are every handle word of the identity record
    handles = {va for va, k in T.identity_record().items() if k == "handle"}
    assert {int(w, 16) for w in timing["identity_open_shift"]["words"]} == handles


def with_group(rec, word, rule=None):
    rec = json.loads(json.dumps(rec))
    rec["groups"].append({"name": "added", "table": {"at": [word]}, "words": [word],
                          "owner": "a test's group of one word", "why_not_gameplay":
                          "none, a test adds it to be refused", "lifted_rule": rule or
                          {"kind": "none", "why": "a test's group"},
                          "evidence": ["harness/replay/tick_fields.h"],
                          "retire_when": "at the end of the test that adds it"})
    return rec


@pytest.mark.parametrize("word,why", [
    ("0x0174b038", "the named field frame_count"),
    ("0x01934b80", "the named field current_character"),
    ("0x01934b70", "the named field current_level"),          # a named field and a trace source
    ("0x01dea6c0", "the stage trace's source MAIN_CHAR_OBJ1"),
    ("0x01934be0", "the stage trace's source GAME_STATE"),
    ("0x0174b03c", "the stage trace's source FRAMES_INGAME"),
    ("0x01755220", "compared by what it names"),                # an identity word
])
def test_a_gameplay_word_or_an_identity_word_is_never_a_residue(word, why):
    """Decision 3: "A residue that changes a gameplay value is a bug." The words a
    replay's gameplay state is read from cannot be listed, so no class can hide one."""
    got = T.record_problems(with_group(record(TIMING), word), TIMING)
    assert len(got) == 1 and why in got[0], got


@pytest.mark.parametrize("edit,why", [
    (lambda r: r["groups"][0]["words"].append("0x0174c000"), "not the ones its table"),
    (lambda r: r["groups"][0]["table"].__setitem__("count", 14), "not the ones its table"),
    (lambda r: r["groups"][0].__setitem__("owner", ""), "does not say owner"),
    (lambda r: r["groups"][0].__setitem__("why_not_gameplay", "no"), "why_not_gameplay"),
    (lambda r: r["groups"][0].__setitem__("retire_when", ""), "retire_when"),
    (lambda r: r["groups"][0].__setitem__("evidence", []), "lists no evidence"),
    (lambda r: r["groups"][0]["evidence"].append("agents-journals/s4/no-such-journal.md"),
     "which does not exist"),
    (lambda r: r["groups"][0].__setitem__("lifted_rule", {"kind": "some"}), "lifted_rule"),
    (lambda r: r["groups"][0].__setitem__("lifted_rule", {"kind": "none"}), "says not why"),
    (lambda r: r["ruling"].__setitem__("quote", "Decision 2: A."), "quote is not in"),
    (lambda r: r["ruling"].__setitem__("record", "Documentation/SA2_RECOMP_DESIGN.md"),
     "not a decision record"),
    (lambda r: r["design"].__setitem__("quote", "a residue is fixed"), "quote is not in"),
    (lambda r: r.__setitem__("class", "host-convenience"), "none the user ruled"),
    (lambda r: r.__setitem__("id", "other-v1"), "its id"),
    (lambda r: r.__setitem__("identity_open_shift", record(TIMING)["identity_open_shift"]),
     "host timing, and this is a host-services record"),
])
def test_a_record_that_does_not_stand_is_refused(edit, why):
    rec = record(STEAM)
    edit(rec)
    got = T.record_problems(rec, STEAM)
    assert got and any(why in p for p in got), got


def test_a_below_rule_limits_only_words_its_group_lists():
    rec = record(TIMING)
    rec["groups"][0]["lifted_rule"]["limits"]["0x0174c328"] = 5
    assert any("limits words it does not list" in p for p in T.record_problems(rec, TIMING))


def test_open_shift_words_must_be_handle_words_of_the_identity_record():
    rec = record(TIMING)
    rec["identity_open_shift"]["table"]["offsets"] = ["0x218", "0x004"]
    rec["identity_open_shift"]["words"] = [
        f"0x{va:08x}" for va in T.table_words(rec["identity_open_shift"]["table"])]
    got = T.record_problems(rec, TIMING)
    # +0x004 is also a word of xeci-read-completion's, which is how the record lists it
    assert any("are not handle words" in p for p in got), got


def test_an_entry_names_its_classes_as_a_word_policy_entry_alone():
    entry = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"][0]
    assert [c["id"] for c in T.residue_classes(entry)] == [STEAM, TIMING, RACE_ID]
    assert T.residue_classes({k: v for k, v in entry.items() if k != T.RESIDUE_FIELD}) == []
    for bad, why in (({k: v for k, v in entry.items() if k != "word_policy"}, "no word_policy"),
                     (dict(entry, residue_classes=[STEAM, STEAM]), "none twice"),
                     (dict(entry, residue_classes=[]), "none twice"),
                     (dict(entry, residue_classes=["no-such-class-v1"]), "no residue class"),
                     (dict(entry, residue_classes="host-timing-v1"), "none twice")):
        with pytest.raises(SystemExit, match=why):
            T.residue_classes(bad)


def test_the_registry_names_the_two_classes_for_every_word_policy_replay():
    """And the host-thread-race class on the boot alone (the user's ruling of 2026-09-27)."""
    for e in json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"]:
        if "word_policy" in e:
            assert e[T.RESIDUE_FIELD] == classes_of(e["name"]), e["name"]
            assert f"Documentation/receipts/residues/{TIMING}.json" in T.compare_pipeline(e)


def test_residue_words_leave_the_judged_digests_as_identity_words_do(tmp_path):
    entry = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"][0]
    spec = T.identity_spec(entry)
    classes = T.residue_classes(entry)
    mask, info = T.effective_mask(entry, spec, tmp_path, classes=classes)
    words = T.OM.read_mask_words(mask)
    own = T.OM.read_mask_words(T.mask_for(entry))
    residue = T.residue_words(classes)
    assert not words & residue and own & residue                 # it held some; they left
    assert info["residue_words_removed"] == len(own & residue - set(T.identity_record()))
    assert info["words"] == len(words) == len(own) - info["residue_words_removed"] \
        - info["identity_words_removed"]
    _, bare = T.effective_mask(entry, spec, tmp_path)
    assert "residue_words_removed" not in bare and bare["words"] == len(own) - \
        bare["identity_words_removed"]
    # native_imports.word_captures holds native runs to each other under the mask less
    # its identity words alone, as before the classes
    import native_imports as NI
    import inspect
    assert "T.effective_mask(entry, spec, Path(td), root)" in inspect.getsource(NI.word_captures)


# the open shift: WNATIVE opens F at h_a and h_b and E at h_c on tick 2 and closes on 4
SHIFTING = ["h_a", "h_b", "h_c"]


def scmp(tmp_path, native, lifted, bound, judged=None):
    return T.compare(WHEADER, native, WHEADER, lifted, {}, [], tmp_path, judged=judged,
                     residues=tmp_path, name="t", identity=dict(WIDENT),
                     shift={"columns": SHIFTING, "bound": bound})


def early(rows_, tick, cols=(3, 4, 5)):
    """The lifted rows with the tick-`tick`+1 opens made a tick early."""
    out = [(t, list(w)) for t, w in rows_]
    for c in cols:
        out[tick - 1][1][c] = out[tick][1][c]
    return out


def test_an_open_a_tick_early_is_within_a_bound_of_one(tmp_path):
    lifted = early(WLIFTED, 1)
    r = scmp(tmp_path, WNATIVE, lifted, 1)
    assert r["first_divergence"] is None and checks_failing(r) == []
    shift = r["identity"]["checks"]["open"]["shift"]
    assert (shift["pairs"], shift["shifted"], shift["widest"]) == (6, 3, 1)
    assert shift["first_shifted"] == {"tick": 1, "field": "h_a"}
    assert r["identity"]["open_shift"] == {"shifting": SHIFTING, "bound": 1}


def test_an_open_further_off_than_the_bound_is_a_bug_at_the_earlier_tick(tmp_path):
    """The bound holds: the same open a tick early with a bound of 0 is the replay's
    divergence, on the handle word, classed bug; without the shift it is too."""
    lifted = early(WLIFTED, 1)
    r = scmp(tmp_path, WNATIVE, lifted, 0)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"], r["class"]) == (1, "h_a", "open", "bug")
    assert d["said"]["lifted"] == "opens at 1, 1 ticks apart and the bound is 0"
    assert wcmp(tmp_path, WNATIVE, lifted)["first_divergence"]["tick"] == 1


def test_under_the_shift_a_close_a_tick_late_is_still_a_bug(tmp_path):
    lifted = [(t, list(w)) for t, w in WLIFTED]
    for c in (3, 4, 5):
        lifted[3][1][c] = lifted[2][1][c]          # still open on tick 4
    r = scmp(tmp_path, WNATIVE[:4], lifted[:4], 2)
    d = r["first_divergence"]
    assert (d["tick"], d["check"], r["class"]) == (4, "close", "bug")


def test_under_the_shift_an_open_one_side_lacks_or_to_another_null_is_a_bug(tmp_path):
    none = [(t, list(w)) for t, w in WLIFTED]
    for t in range(6):
        none[t][1][5] = "0x00000000"               # h_c never opens lifted
    r = scmp(tmp_path, WNATIVE, none, 5)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["check"]) == (2, "h_c", "open")
    failed = [(t, list(w)) for t, w in WLIFTED]
    failed[1][1][5] = "0xffffffff"                 # h_c's first open fails lifted
    r = scmp(tmp_path, WNATIVE, failed, 5)
    assert r["first_divergence"]["check"] in ("open", "close") and r["class"] == "bug"


def test_a_shifted_open_to_another_place_is_a_mismatch_at_any_distance():
    """Paired opens must go to the same place: a value on one side and the null a
    failed open writes on the other is a mismatch however near, and so is an open
    the other side lacks."""
    native = {(2, "h_c", "")}
    got = T.shifted_opens(native, {(2, "h_c", "0xffffffff")}, ["h_c"], 5)
    assert got["mismatches"] == 1 and got["first"] == {
        "tick": 2, "field": "h_c", "native": "opens at 2", "lifted": "opens at 2 to 0xffffffff"}
    got = T.shifted_opens(native | {(5, "h_c", "")}, {(3, "h_c", "")}, ["h_c"], 5)
    assert got["mismatches"] == 1 and got["first"] == {
        "tick": 5, "field": "h_c", "native": "opens at 5", "lifted": "has no open 2"}
    got = T.shifted_opens(native, {(3, "h_c", "")}, ["h_c"], None)
    assert got["mismatches"] == 0 and got["summary"]["widest"] == 1


def test_the_bound_is_the_repeats_widest_shift_over_the_ticks_they_judge(tmp_path):
    """A repeat whose reopen is a tick late after another repeat has ended the
    judged ticks says nothing about the lifted run: the bound is taken over the
    judged ticks alone, so here it stays 0."""
    cut = [(t, list(w)) for t, w in WNATIVE]
    cut[2][1][0] = "0x00000063"                    # frame apart at tick 3: judged 2
    late = [(t, list(w)) for t, w in WNATIVE]
    for c in (3, 4, 5):
        late[4][1][c] = "0x00000000"               # the reopen on 6, not 5
    entry, streams = repeat_world(cut, late)
    det = T.native_repeat_streams(entry, WHEADER, WNATIVE, tmp_path, streams, dict(WIDENT),
                                  SHIFTING)
    assert det["judged_ticks"] == 2 and det["open_shift"]["bound"] == 0
    assert [r["first_divergent_tick"] for r in det["repeats"]] == [3, None]


def test_under_the_shift_a_swapped_handle_still_fails_the_pairing(tmp_path):
    lifted = [(t, list(w)) for t, w in WLIFTED]
    lifted[2][1][3], lifted[2][1][5] = lifted[2][1][5], lifted[2][1][3]
    r = scmp(tmp_path, WNATIVE, lifted, 2)
    assert r["first_divergence"]["check"] == "pairing" and r["class"] == "bug"


def repeat_world(*others):
    entry = {"name": "t", "native": {"capture": "a/c.csv", "sha256": "0",
                                     "repeats": [{"capture": f"r{k}/c.csv", "sha256": str(k)}
                                                 for k in range(len(others))]}}
    return entry, [(entry["native"]["repeats"][k], WHEADER, rows_)
                   for k, rows_ in enumerate(others)]


def test_repeats_open_a_tick_apart_judge_every_tick_and_give_the_bound(tmp_path):
    """The repeatability test ignores where an open lands within the repeats' own
    widest shift, which is the bound the lifted run is held to: computed, never
    typed. Without the shift the same repeat, apart from tick 1, judges no tick."""
    late = early(WNATIVE, 1)
    entry, streams = repeat_world(WNATIVE, late)
    det = T.native_repeat_streams(entry, WHEADER, WNATIVE, tmp_path, streams, dict(WIDENT),
                                  SHIFTING)
    assert det["judged_ticks"] == 6 and det["open_shift"] == {"columns": 3, "bound": 1}
    assert [r["open_shift"]["widest"] for r in det["repeats"]] == [0, 1]
    bare = T.native_repeat_streams(entry, WHEADER, WNATIVE, tmp_path, streams, dict(WIDENT))
    assert bare["judged_ticks"] == 0 and "open_shift" not in bare


def test_a_repeat_whose_opens_differ_in_number_still_cuts_the_judged_ticks(tmp_path):
    fewer = [(t, list(w)) for t, w in WNATIVE]
    for t in range(6):
        fewer[t][1][5] = "0x00000000"
    entry, streams = repeat_world(fewer)
    det = T.native_repeat_streams(entry, WHEADER, WNATIVE, tmp_path, streams, dict(WIDENT),
                                  SHIFTING)
    assert det["judged_ticks"] == 1 and det["repeats"][0]["first_divergent_field"] == "h_c"


def held_uploads(params):
    """The uploads observation of a lifted run whose uploads are the capture's frames
    in its order, moved on exactly the ticks the record's effect lists."""
    moved = sorted(params["uploads"]["moved"])
    return {"word": params["uploads"]["word"], "initial": ["0x00000000", "0x00000000"],
            "through": max((max(m[1], m[2]) for m in moved), default=1),
            "lifted": " ".join(f"{m[1]}:{m[0]}" for m in moved), "lifted_uploads": len(moved),
            "native": " ".join(f"{m[2]}:{m[0]}" for m in moved), "native_uploads": len(moved)}


def held_rule(group, effect=None):
    """The rule report of a group whose lifted values keep its rule and equal native:
    what the comparer records for it, as residue_problems re-derives it (with its
    record's effect, the uploads the effect lists moved and no other)."""
    rule = group["lifted_rule"]
    if rule["kind"] == "zero":
        return {"kind": "zero", "held": True, "broken": None, "nonzero_words": 0}
    if rule["kind"] == "below":
        return {"kind": "below", "held": True, "broken": None, "limits": rule["limits"],
                "max": {a: "0x00000000" for a in rule["limits"]}}
    if rule["kind"] == "none":
        return {"kind": "none", "held": True, "broken": None}
    params = T.native_params(rule, group["table"], effect)
    observed = ({"words": group["words"], "apart_ticks": 0, "apart": []}
                if rule["form"] == "one-tick-scan" else
                {**{k: {"ticks": 0, "first": None} for k in ("pre", "shape", "decode", "copy")},
                 "decode_context": {"lifted": {}, "native": {}},
                 "episodes": {"count": 0, "listed": [], "longest": None, "first_long": None}})
    if "uploads" in params:
        observed["uploads"] = held_uploads(params)
    return {"kind": "native", "form": rule["form"], "params": params, "observed": observed,
            "held": True, "broken": None}


def classes_of(name):
    """The records the registry names for replay `name`: the boot's three, the others'
    two (the host-thread-race class is the boot's alone)."""
    return [STEAM, TIMING] + ([RACE_ID] if name == "boot" else [])


def clean_groups(name, skip=()):
    """Every group the records give replay `name` (`applies`), as a report that
    saw none of its words differ and its rule held."""
    return [{"record": rid, "group": g["name"], "listed": len(g["words"]), "in_mask": 0,
             "rule": held_rule(g, record(rid).get("effect")),
             "lifted": {"differing": 0, "first": None, "first_in_mask": None, "words": [],
                        "listing_sha256": "0" * 64}}
            for rid in classes_of(name) for g in record(rid)["groups"]
            if T.applies(g, name) and (rid, g["name"]) not in skip]


def a_block(differing=5, rule=None, shifted=0, name=None):
    """The Steam group's report as given; with `name`, every other group the records
    give that replay too, clean, as a real report lists them (residue_problems holds
    a result's groups to its records')."""
    block = {"classes": [{"id": STEAM, "class": "host-services"},
                         {"id": TIMING, "class": "host-timing"}],
             "compared_ticks": 9,
             "groups": [{"record": STEAM, "group": "steam-achievement-table", "listed": 1455,
                         "in_mask": 203,
                         "rule": rule or {"kind": "zero", "held": True, "broken": None,
                                          "nonzero_words": 0},
                         "lifted": {"differing": differing,
                                    "first": {"tick": 4, "va": "0x0174c328"} if differing
                                    else None,
                                    "first_in_mask": {"tick": 4, "va": "0x0174c328",
                                                      "column": "d_01740000"}
                                    if differing else None, "words": [],
                                    "listing_sha256": "0" * 64}}],
             "open_shift": {"record": TIMING, "bound": 1, "shifted": shifted, "widest": shifted,
                            "pairs": 2, "columns": 320,
                            "first_shifted": {"tick": 6, "field": "h_01755220"} if shifted
                            else None}}
    if name is not None:
        block["groups"] += clean_groups(name, skip={(STEAM, "steam-achievement-table")})
        if name == "boot":
            block["classes"].append({"id": RACE_ID, "class": T.THREAD_RACE_CLASS})
    return block


def a_result(div=None):
    return {"native_ticks": 9, "lifted_ticks": 9, "compared_ticks": 9, "judged_ticks": 9,
            "first_divergence": div, "residues_masked": [], "reported": div,
            "class": T.class_of(div, [])}


# one repeat, whose opens were at most a tick from the capture's: the bound is 1
DET = {"judged_ticks": 9, "repeats": [{"residues": [], "open_shift": {
    "pairs": 2, "shifted": 1, "widest": 1, "first_shifted": {"tick": 9, "field": "h_01755220"}}}]}


def test_a_replay_whose_only_differences_are_residue_words_reads_residue():
    r = T.settle(a_result(), a_block(), DET)
    assert r["class"] == "residue" and r["first_divergence"] is None
    assert r["residues"]["seen"] == [STEAM]
    assert r["residues"]["unclassed"] == {"tick": 4, "field": "d_01740000", "class": "bug"}
    rec = T.m5_record({"name": "boot", "result": r})
    assert rec == {"name": "boot", "ticks": 9, "first_divergent_tick": None, "field": None,
                   "class": "residue", "residues": [STEAM], "residues_from": 4}


def test_a_judged_divergence_always_wins_and_the_classes_are_named_beside_it():
    bug = {"tick": 7, "field": "d_01c00000", "kind": "value", "native": "1", "lifted": "2"}
    r = T.settle(a_result(bug), a_block(shifted=1), DET)
    assert r["class"] == "bug" and r["first_divergence"] == bug
    assert T.m5_record({"name": "boot", "result": r})["residues"] == [STEAM, TIMING]


def test_a_broken_lifted_rule_is_a_bug_at_its_tick_before_a_later_judged_one():
    broken = {"kind": "zero", "held": False, "nonzero_words": 1,
              "broken": {"tick": 5, "va": "0x0174c32c", "value": "0x00000001",
                         "native": "0x00000000"}}
    later = {"tick": 7, "field": "d_01c00000", "kind": "value", "native": "1", "lifted": "2"}
    r = T.settle(a_result(later), a_block(rule=broken), DET)
    d = r["first_divergence"]
    assert (d["tick"], d["field"], d["kind"], r["class"]) == (5, "w_0174c32c", "residue_rule",
                                                             "bug")
    r = T.settle(a_result(), a_block(rule=broken), DET)
    assert r["class"] == "bug" and r["first_divergence"]["tick"] == 5


def test_with_no_class_a_repeat_that_differs_on_a_residue_word_cuts_the_window():
    det = {"judged_ticks": 9, "repeats": [{"residues": [{"first_in_mask_tick": 3}],
                                           "open_shift": {"first_shifted": None}}]}
    r = T.settle(a_result(), a_block(), det)
    assert r["residues"]["unclassed"] == {"tick": 3, "field": T.UNREPEATED,
                                          "class": "unrepeatable"}
    det["repeats"][0]["residues"] = []
    det["repeats"][0]["open_shift"]["first_shifted"] = {"tick": 2, "field": "h_01755220"}
    assert T.settle(a_result(), a_block(), det)["residues"]["unclassed"]["tick"] == 2


def test_a_group_s_rule_is_read_from_the_lifted_log(tmp_path):
    """zero: every listed word 0 on every tick, its keyframe counted as tick 1;
    below: each limited word under its limit, its maximum recorded."""
    import test_snapshot_fields as TSF
    ticks = TSF.base_ticks()
    zero = {"record": STEAM, "group": "g", "words": [0x0174c328, 0x0174c32c],
            "rule": {"kind": "zero"}}
    below = {"record": TIMING, "group": "l", "words": [0x01a557ec],
             "rule": {"kind": "below", "limits": {"0x01a557ec": 180}}}
    log = TSF.write_log(tmp_path / "a.bin", ticks)
    assert T.evaluate_rule(log, zero, 12) == {"kind": "zero", "held": True, "broken": None,
                                              "nonzero_words": 0}
    for t in range(1, 13):
        ticks[t][0x01a557ec] = 170 + t
    ticks[6][0x0174c32c] = 0x41
    log = TSF.write_log(tmp_path / "b.bin", ticks, clock=5)
    got = T.evaluate_rule(log, zero, 12)
    assert got["broken"] == {"tick": 6, "va": "0x0174c32c", "value": "0x00000041"}
    assert got["held"] is False and got["nonzero_words"] == 1
    assert T.evaluate_rule(log, zero, 5)["held"] is True             # not in the ticks judged
    got = T.evaluate_rule(log, below, 12)
    assert got["broken"] == {"tick": 10, "va": "0x01a557ec", "value": "0x000000b4"}
    assert got["max"] == {"0x01a557ec": "0x000000b6"}
    assert T.evaluate_rule(log, below, 9)["held"] is True


def test_every_listed_word_is_compared_on_both_sides_masked_or_not(tmp_path):
    import test_snapshot_fields as TSF
    a = TSF.base_ticks()
    b = json.loads(json.dumps(a), object_hook=lambda d: {int(k): v for k, v in d.items()})
    for t in range(4, 13):
        b[t][0x0174c328] = 0x00000041                               # apart from tick 4
    b[8][0x0174c400] = 1                                            # and one more at 8
    la = TSF.write_log(tmp_path / "a.bin", a)
    lb = TSF.write_log(tmp_path / "b.bin", b, clock=3)
    group = {"record": STEAM, "group": "g", "words": [0x0174c328, 0x0174c400, 0x0174c404]}
    got = T.group_diffs(la, lb, [group], 12, {0x0174c400})[0]
    assert (got["differing"], got["listed"], got["in_mask"]) == (2, 3, 1)
    assert got["first"] == {"tick": 4, "va": "0x0174c328", "native": "0x00000000",
                            "lifted": "0x00000041"}
    assert got["first_in_mask"] == {"tick": 8, "va": "0x0174c400", "column": "d_01740000"}
    assert T.group_diffs(la, lb, [group], 7, {0x0174c400})[0]["differing"] == 1


# ------------------------------------------------ residue_problems, M5's residue clause

def gate_residue_falsifiers():
    crit = json.loads((ROOT / "Documentation" / "gate-criteria.json").read_text())
    m5 = next(c for c in crit["G4"] if c["criterion"].startswith("M5:"))
    clause = next(c for c in m5["clauses"]
                  if c["promise"] == "a residue that changes a gameplay value is a bug")
    return clause["falsifiers"]


def test_the_gate_s_injected_residue_replays_each_fail_their_own_check_alone():
    """M5's residue clause prepends five results to the receipt: each is the shape
    the comparer records but for one mutation, and fails the one check written for
    it -- the class, the broken rule, the open check under the bound (its verdict,
    then the recompute alone) and the bound's derivation -- and nothing else."""
    want = {
        "injected-residue-over-bug": ("residue", "its class is 'residue'"),
        "injected-steam-word-nonzero": ("residue", "lifted rule breaks at tick 4"),
        "injected-open-outside-the-bound": ("identity", "the open check fails at tick 1 "
                                            "h_01755220 (native opens at 1, lifted opens at 4, "
                                            "3 ticks apart and the bound is 1)"),
        "injected-open-outside-the-bound-recorded-clean": ("identity", "recorded identity "
                                                           "checks are not what"),
        "injected-bound-typed": ("residue", "the bound is computed, never typed"),
    }
    seen = {}
    for f in gate_residue_falsifiers():
        if f.get("replace") != '"replays": [':
            continue
        doc = json.loads("[" + f["with"][len(f["replace"]):].rstrip(", ") + "]")[0]
        receipt = {"replays": [doc]}
        seen[doc["name"]] = (T.residue_problems(receipt, {"replays": []}),
                             T.identity_problems(receipt, {"replays": []}))
    assert sorted(seen) == sorted(want)
    for name, (check, text) in want.items():
        residue, identity = seen[name]
        got = residue if check == "residue" else identity
        other = identity if check == "residue" else residue
        assert other == [], (name, other)
        assert got and all(name in p for p in got) and any(text in p for p in got), (name, got)
        if name != "injected-open-outside-the-bound-recorded-clean":
            assert len(got) == 1, (name, got)
        # without its mutation, the same shape holds
    clean = next(f for f in gate_residue_falsifiers()
                 if "injected-residue-over-bug" in f["with"])
    doc = json.loads("[" + clean["with"][len(clean["replace"]):].rstrip(", ") + "]")[0]
    doc["result"]["class"] = "bug"
    assert T.residue_problems({"replays": [doc]}, {"replays": []}) == []


GAMEPLAY_ANCHOR = '"name": "loading-screen-frames",'
RACE_WORD = "0x01c0a7a8"         # the race's first word, which M5's clause lists in host timing
SPAN_WORD = "0x01c0c338"         # context 1's unlisted offset 0x7b8, which it lists there too


def test_the_gate_s_added_groups_are_refused_by_the_gameplay_rule():
    """M5's residue clause adds five gameplay words, each to the loading-screen-frames
    group, in its table and its word list: each record reads as generated, and only the
    gameplay refusal fails it."""
    added = [f for f in gate_residue_falsifiers() if f["replace"].startswith(GAMEPLAY_ANCHOR)
             and RACE_WORD not in f["with"] and SPAN_WORD not in f["with"]]
    words = []
    for f in added:
        text = (ROOT / f["file"]).read_text()
        assert text.count(f["replace"]) == f.get("occurrences", 1)
        rec = json.loads(text.replace(f["replace"], f["with"]))
        (g,) = [g for g in rec["groups"] if g["name"] == "loading-screen-frames"]
        (w,) = set(g["words"]) - {"0x01a557ec", "0x01af1970", "0x0174b494"}
        words.append(w)
        got = T.record_problems(rec, Path(f["file"]).stem)
        assert len(got) == 1 and "a residue that changes a gameplay value is a bug" in got[0], got
    assert words == ["0x01934b80", "0x01dea6c0", "0x0174b038", "0x0174b000", "0x01a52944"]


def measured_docs():
    """A receipt's two results as the comparer gives them, for residue_problems:
    a boot that diverges at 7 with both classes seen, and one read residue-only."""
    bug = {"tick": 7, "field": "d_01c00000", "kind": "value", "native": "1", "lifted": "2"}
    boot = {"name": "boot", "native": {"determinism": DET},
            "result": T.settle(a_result(bug), a_block(shifted=1, name="boot"), DET)}
    mh = {"name": "metalharbor", "native": {"determinism": DET},
          "result": T.settle(a_result(), a_block(name="metalharbor"), DET)}
    shifting = T.open_shift_columns([{"id": TIMING, "record": record(TIMING)}])
    for d in (boot, mh):
        d["result"]["identity"] = {"open_shift": {"shifting": shifting, "bound": 1}}
    return boot, mh


def test_residue_problems_hold_a_registered_result_to_the_registry_s_classes():
    registry = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    boot, mh = measured_docs()
    assert T.residue_problems({"replays": [boot]}, registry) == []
    for e in registry["replays"]:
        e.pop(T.RESIDUE_FIELD)
    got = T.residue_problems({"replays": [boot]}, registry)
    assert got == [f"boot: its result was compared under residue classes "
                   f"{[STEAM, TIMING, RACE_ID]} and the registry names []; without them it reads "
                   "4/9 (bug: d_01740000)"]


def test_a_part_of_m5_s_row_that_names_a_residue_must_be_the_recorded_result_s():
    import stage_exit as SE
    boot, mh = measured_docs()
    receipt = {"replays": [boot, mh]}
    row = "; ".join(SE.m5_part(T.m5_record(d)) for d in (boot, mh))
    assert row == (f"boot 7/9 (bug: d_01c00000) + residues ({STEAM}, {TIMING}) from 4; "
                   f"metalharbor residues only/9 ({STEAM}) from 4")
    assert [g["group"] for g in boot["result"]["residues"]["groups"]] == [
        "steam-achievement-table", "loading-screen-frames", "xeci-read-completion",
        "sofdec-opening-scan", "sofdec-mpv-slice-race"]
    assert [g["group"] for g in mh["result"]["residues"]["groups"]] == [
        "steam-achievement-table", "loading-screen-frames", "xeci-read-completion"]
    metrics = f"| M5 replays | {row} | harness level 2/3 | measured |\n"
    assert T.residue_problems(receipt, {"replays": []}, metrics=metrics) == []
    for typed in (f"boot residues only/9 ({STEAM}, {TIMING})",          # over its bug
                  f"metalharbor residues only/9 ({STEAM}, {TIMING})",   # a class not seen
                  f"garden residues only/9 ({STEAM})",                  # no result at all
                  f"boot 7/9 (residue {TIMING}: d_01c00000)"):          # a class as a label
        got = T.residue_problems(receipt, {"replays": []},
                                 metrics=metrics.replace("| M5 replays | ",
                                                         f"| M5 replays | {typed}; "))
        assert len(got) == 1 and typed in got[0], (typed, got)


def test_residue_problems_rederive_what_a_result_holds():
    boot, _ = measured_docs()
    for edit, why in (
            (lambda r: r.__setitem__("class", "residue"), "its class is 'residue'"),
            (lambda r: r["residues"].__setitem__("seen", [STEAM]), "seen"),
            (lambda r: r["residues"].__setitem__("unclassed", None), "no residue class is not"),
            (lambda r: r["residues"]["open_shift"].__setitem__("bound", 2), "never typed"),
            (lambda r: r["residues"]["groups"][0]["rule"].__setitem__("nonzero_words", 2),
             "recorded held, and its recorded values break it")):
        doc = json.loads(json.dumps(boot))
        edit(doc["result"])
        got = T.residue_problems({"replays": [doc]}, {"replays": []})
        assert len(got) == 1 and why in got[0], (why, got)
    doc = json.loads(json.dumps(boot))
    doc["result"].pop("residues")
    doc["result"]["class"] = "residue"
    assert T.residue_problems({"replays": [doc]}, {"replays": []}) == [
        "boot: its class is residue and no residue class report backs it"]


def test_the_gate_falsifies_the_residue_classes_eight_ways(monkeypatch):
    """check_replay_results.residue_cases, for a registry whose first replay names
    residue classes and a receipt whose result carries their report: the classes
    dropped from the registry, a record moved, the report, a rule's verdict, the
    class, the open-shift bound and each native-relative form's observations each
    edited, and each named by what must catch it. Without the report it has none,
    as word_cases has none without identity."""
    import check_replay_results as R
    monkeypatch.setattr(R, "live_tree", lambda mutate: {"moved": True})
    boot, _ = measured_docs()
    real = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"]
    registry = {"replays": [next(e for e in real if e["name"] == "boot")]}
    base = {"registry": registry, "receipt": {"replays": [boot]}, "live": {}}
    assert T.residue_problems(base["receipt"], registry) == []

    def case(name, part, f=None, value=None, outputs=False, names=None):
        docs = json.loads(json.dumps(base))
        if f is not None:
            f(docs[part])
        else:
            docs[part] = value
        return name, dict(docs, outputs=outputs, names=names)
    cases, na = R.residue_cases(base, case, lambda rel: rel)
    assert na == []
    got = dict(cases)
    assert list(got) == [
        "the registry's residue classes removed", f"residue class {STEAM}'s record changed",
        "a recorded residue report was edited",
        "a lifted rule recorded held over values that break it",
        "the recorded class typed over a judged divergence", "the open-shift bound typed",
        "a native-relative rule (one-tick-scan) recorded held over observed values that break it",
        "a native-relative rule (context-permutation) recorded held over observed values that "
        "break it"]
    for name, docs in got.items():
        if docs.get("receipt") is None or name == f"residue class {STEAM}'s record changed":
            continue
        after = T.residue_problems(docs["receipt"], docs["registry"])
        if name == "a recorded residue report was edited":
            continue                 # the recompute from the logs is what sees it
        assert any(n in p for p in after for n in docs["names"]), (name, after)
    # each form's case reaches its own rule (the race gate review, finding 2: one case
    # on the first native group never reached the context-permutation rule)
    for form, group in (("one-tick-scan", "host-timing-v1/sofdec-opening-scan"),
                        ("context-permutation", f"{RACE_ID}/sofdec-mpv-slice-race")):
        docs = got[f"a native-relative rule ({form}) recorded held over observed values that "
                   "break it"]
        after = T.residue_problems(docs["receipt"], docs["registry"])
        assert [p for p in after if f"{group}'s lifted rule" in p], (form, after)
    boot["result"].pop("residues")
    assert R.residue_cases(base, case, lambda rel: rel) == ([], [])
    # a registry whose first replay names no class still falsifies the one that does
    boot, _ = measured_docs()
    other = dict(registry["replays"][0], name="other")
    other.pop(T.RESIDUE_FIELD)
    base2 = {"registry": {"replays": [other, registry["replays"][0]]},
             "receipt": {"replays": [boot]}, "live": {}}
    base = base2                     # `case` copies `base`
    assert len(R.residue_cases(base2, case, lambda rel: rel)[0]) == 8


@pytest.mark.parametrize("va,what", [
    (0x0174b000, "GOAL"),                    # the goal latch byte 0x0174B002
    (0x01a52920, "RAW0+0x8"),                # the applied input's "on" word
    (0x01a52944, "RAW0+0x2c"),               # its "old" word
])
def test_the_input_probe_s_sources_are_never_a_residue(va, what):
    """The goal latch and the input the stage probe applies are gameplay values: a
    group listing a word of them is refused as a stage-trace source is."""
    rec = json.loads((ROOT / "Documentation/receipts/residues/host-timing-v1.json").read_text())
    g = rec["groups"][0]
    g["table"] = {"at": g["table"]["at"] + [f"0x{va:08x}"]}
    g["words"] = [f"0x{w:08x}" for w in T.table_words(g["table"])]
    problems = T.record_problems(rec, "host-timing-v1")
    assert any(f"0x{va:08x}" in p and what in p for p in problems), problems


def test_an_open_past_the_judged_window_is_a_bug_at_the_last_tick(tmp_path):
    """RC's choice, pinned: an open the capture makes on the last judged tick and the
    lifted run makes a tick later, past the window, is unmatched and a bug there, even
    within the bound; the window is never read as hiding it."""
    lifted = [(t, list(w)) for t, w in WLIFTED]
    for c in (3, 4, 5):
        lifted[1][1][c] = lifted[0][1][c]      # the tick-2 opens a tick late, on 3
    r = scmp(tmp_path, WNATIVE, lifted, 5, judged=2)
    d = r["first_divergence"]
    assert (d["tick"], d["check"], r["class"]) == (2, "open", "bug"), d


# ---------------------------- fold:rc: scoped groups and native-relative rules (unit SF)
# Unit SF traced the boot's two Sofdec groups to host timing
# (private/generated/s4/sf/deliver/MANIFEST.md) and held them by rules that compare
# the lifted series with the native one (its sf_rules.py). They are the boot's alone
# (`replays`), and tick_replay evaluates the rules on both logs (`evaluate_native`)
# and re-derives each verdict from what it observed (`rule_problems`).

def test_a_scoped_group_applies_to_its_replays_alone():
    reg = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"]
    boot, mh = (next(e for e in reg if e["name"] == n) for n in ("boot", "metalharbor"))
    wb, wm = (T.residue_words(T.residue_classes(e)) for e in (boot, mh))
    sofdec = {0x01c07b58, 0x01c07b5c, 0x01c07b60, 0x01c07b64}
    race = set(T.table_words(T.THREAD_RACE_SCOPE["table"]))
    assert sofdec <= wb and not sofdec & wm and len(wb - wm) == 4 + 244
    # the decoder contexts are the boot's host-thread-race class, and Metal Harbor names none
    assert race <= wb and not race & wm and {0x01c0a7a8, 0x01c0f134} <= race
    # a class read for no named replay applies no scoped group
    assert not sofdec & T.residue_words([{"id": TIMING, "record": record(TIMING)}])
    assert [g["group"] for g in T.residue_groups(T.residue_classes(mh))] == [
        "steam-achievement-table", "loading-screen-frames", "xeci-read-completion"]


def test_both_stage_probes_are_read_by_the_comparison_that_applies_classes():
    entry = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"][0]
    assert set(T.STAGE_SOURCES) == {"harness/replay/native_stage_trace.c",
                                    "harness/replay/native_input_probe.c"}
    assert set(T.STAGE_SOURCES) <= set(T.compare_pipeline(entry))


SCAN, RACE = "sofdec-opening-scan", "sofdec-mpv-slice-race"


@pytest.mark.parametrize("k,edit,why", [
    (SCAN, lambda g: g.pop("replays"), "must name the replays it applies to"),
    (SCAN, lambda g: g.__setitem__("replays", []), "not a list of replay names"),
    (SCAN, lambda g: g.__setitem__("replays", ["boot", "boot"]), "not a list of replay names"),
    (SCAN, lambda g: g.__setitem__("replays", ["no such"]), "not a list of replay names"),
    (SCAN, lambda g: g["lifted_rule"].__setitem__("form", "some-form"), "form is not one of"),
    (SCAN, lambda g: g["lifted_rule"].pop("max_ticks"), "names no max_ticks"),
    (SCAN, lambda g: g["lifted_rule"].__setitem__("progress", "0x01c07b58"),
     "progress is not a word outside its group"),
    (SCAN, lambda g: g["lifted_rule"].__setitem__("reset", "zz"), "does not read"),
    (SCAN, lambda g: g["lifted_rule"].pop("text"), "does not say what it holds"),
    (SCAN, lambda g: g.__setitem__("table", {"at": g["words"]}), "two or more entries"),
    (RACE, lambda g: g["lifted_rule"].__setitem__("dispatcher", 4), "dispatcher is not an entry"),
    (RACE, lambda g: g["lifted_rule"].pop("dispatcher"), "dispatcher is not an entry"),
    (RACE, lambda g: g["lifted_rule"].pop("kept_from"), "names no kept_from"),
    (RACE, lambda g: g["lifted_rule"].__setitem__("episode_max", 0), "names no kept_from"),
    (RACE, lambda g: g["lifted_rule"].__setitem__("form", "context-race"), "form is not one of"),
])
def test_a_native_rule_or_a_scope_that_does_not_read_is_refused(k, edit, why):
    rid = TIMING if k == SCAN else RACE_ID
    rec = record(rid)
    (g,) = [g for g in rec["groups"] if g["name"] == k]
    assert g["lifted_rule"]["kind"] == "native"
    edit(g)
    got = T.record_problems(rec, rid)
    assert got and any(why in p for p in got), got


S_WORDS = [0x01c07b58, 0x01c07b5c, 0x01c07b60, 0x01c07b64]
S_PROGRESS, RESET = 0x01c07ac8, 0xffffffff
S_TABLE = {"base": "0x01c07b58", "stride": "0x8", "count": 2, "offsets": ["0x0", "0x4"]}
R_TABLE = {"base": "0x01c0a480", "stride": "0x1700", "count": 4, "offsets": ["0x328", "0x32c"]}


def native_group(name, table, **rule):
    return {"record": TIMING, "group": name, "table": table, "words": T.table_words(table),
            "rule": dict(rule, kind="native", text="a test's rule")}


def s_group(**rule):
    return native_group("sofdec-opening-scan", S_TABLE, **dict(
        dict(form="one-tick-scan", max_ticks=1, reset="0xffffffff", progress="0x01c07ac8"),
        **rule))


def s_world(tmp_path, apart=(5,), value=(0xfcaf8, 0, 0xfcaf8, 0), native_value=None,
            running=True):
    """Both sides' copies at the reset value on every tick but `apart`, where the lifted
    copies hold `value` (and the native `native_value`, when given); the lifted pass's
    progress word moves over ticks 4 to 8 when `running`, and stands still when not."""
    import test_snapshot_fields as TSF
    nat = {t: dict.fromkeys(S_WORDS, RESET) for t in range(1, TSF.TICKS + 1)}
    lif = {t: dict(w) for t, w in nat.items()}
    for t in apart:
        lif[t].update(zip(S_WORDS, value))
        if native_value:
            nat[t].update(zip(S_WORDS, native_value))
    for t in lif:
        lif[t][S_PROGRESS] = (0x100 * t if 4 <= t <= 8 else 0x900 if t > 8 else RESET) \
            if running else 0x2861
    return (TSF.write_log(tmp_path / "l.bin", lif, clock=3),
            TSF.write_log(tmp_path / "n.bin", nat))


def test_a_one_tick_scan_holds_the_boot_s_shape_and_records_what_it_saw(tmp_path):
    lifted, native = s_world(tmp_path)
    got = T.evaluate_rule(lifted, s_group(), 12, native)
    assert (got["held"], got["broken"]) == (True, None)
    assert got["params"] == {"form": "one-tick-scan", "max_ticks": 1, "reset": "0xffffffff",
                             "progress": "0x01c07ac8", "entries": 2}
    assert got["observed"] == {
        "words": ["0x01c07b58", "0x01c07b5c", "0x01c07b60", "0x01c07b64"], "apart_ticks": 1,
        "apart": [{"tick": 5, "lifted": ["0x000fcaf8", "0x00000000", "0x000fcaf8", "0x00000000"],
                   "native": ["0xffffffff"] * 4, "progress": ["0x00000500", "0x00000600"]}]}
    assert T.rule_problems("boot", {"record": TIMING, "group": "g", "rule": got}) == []
    lifted, native = s_world(tmp_path, apart=())
    assert T.evaluate_rule(lifted, s_group(), 12, native)["observed"]["apart_ticks"] == 0


@pytest.mark.parametrize("world,tick,why", [
    (dict(apart=(5, 6)), 6, "more than 1 tick"),
    (dict(value=(1, 0, 2, 0)), 5, "copies are not one value"),
    (dict(native_value=(5, 0, 5, 0)), 5, "not at the reset value"),
    (dict(running=False), 5, "is not moving at the sample"),
    (dict(apart=(10,)), 10, "is not moving at the sample"),          # the pass ended at 9
])
def test_a_one_tick_scan_breaks_at_the_tick_its_shape_fails(tmp_path, world, tick, why):
    """SF's falsifiers of rule_s: two ticks apart, a lifted pair that is not two copies
    of one time, a native value other than the reset value, and a difference after the
    pass has ended (or with it standing still) -- each a bug at its tick."""
    lifted, native = s_world(tmp_path, **world)
    got = T.evaluate_rule(lifted, s_group(), 12, native)
    assert got["held"] is False and got["broken"]["tick"] == tick and why in got["broken"]["why"]
    assert T.rule_problems("boot", {"record": TIMING, "group": "g", "rule": got}) == []
    assert T.evaluate_rule(lifted, s_group(), tick - 1, native)["held"] is True
    with pytest.raises(SystemExit, match="needs the native capture's log"):
        T.evaluate_rule(lifted, s_group(), 12)


def r_group(**rule):
    return native_group("sofdec-mpv-slice-race", R_TABLE, **dict(
        dict(form="context-permutation", dispatcher=0, kept_from=2, episode_max=4), **rule))


UPLOADS = "0x01d0c6d0"            # Sofdec's frame number, the scope's frame word


def frames_at(advances, ticks):
    """The frame word's value on each tick, advancing to frame f on each tick of
    `advances` {tick: frame}, 0 before the first."""
    out, v = {}, 0
    for t in range(1, ticks + 1):
        v = advances.get(t, v)
        out[t] = v
    return out


def r_world(tmp_path, lifted_at=None, native_at=None, same=False, lifted_frames=None,
            native_frames=None):
    """Every context 1 up to tick 3. Natively the dispatcher's context 0 decodes 7 at
    tick 4 (the others hold its copy, 1), the next picture copies that 7 into
    contexts 1 to 3 at 6 while context 0 decodes 1, and the one after copies the 1
    back at 8: (7,1,1,1), then (1,7,7,7), then all 1. Lifted, worker context 1
    decodes the same 7 at 4, and at 6 the copy of the dispatcher's unchanged context
    0 is 1 again and context 1 decodes 1: (1,7,1,1), then all 1. `lifted_at` and
    `native_at` {(tick, context): value} edit a side; `same` makes the lifted log the
    native one (a native run fed as the lifted side). `lifted_frames` and
    `native_frames` {tick: frame} are each side's movie uploads, the frame word
    advancing on those ticks."""
    import test_snapshot_fields as TSF
    entries = T.table_entries(R_TABLE)

    def row(values):
        return {va: v for e, v in zip(entries, values) for va in e}
    nat_v = {t: (7, 1, 1, 1) if 4 <= t <= 5 else (1, 7, 7, 7) if 6 <= t <= 7 else (1, 1, 1, 1)
             for t in range(1, TSF.TICKS + 1)}
    lif_v = {t: (1, 7, 1, 1) if 4 <= t <= 5 else (1, 1, 1, 1) for t in nat_v}
    for vals, edits in ((lif_v, lifted_at), (nat_v, native_at)):
        for (t, k), v in (edits or {}).items():
            vals[t] = vals[t][:k] + (v,) + vals[t][k + 1:]
    nat = {t: row(v) for t, v in nat_v.items()}
    lif = {t: row(v) for t, v in lif_v.items()}
    for rows, adv in ((lif, lifted_frames), (nat, native_frames)):
        if adv:
            for t, v in frames_at(adv, TSF.TICKS).items():
                rows[t][int(UPLOADS, 16)] = v
    return (TSF.write_log(tmp_path / "l.bin", nat if same else lif, clock=3),
            TSF.write_log(tmp_path / "n.bin", nat))


def test_a_context_permutation_holds_the_boot_s_shape_and_records_what_it_saw(tmp_path):
    lifted, native = r_world(tmp_path)
    got = T.evaluate_rule(lifted, r_group(), 12, native)
    assert (got["held"], got["broken"]) == (True, None)
    assert got["params"] == {"form": "context-permutation", "dispatcher": 0, "kept_from": 2,
                             "episode_max": 4, "entries": 4}
    assert got["observed"] == {"pre": {"ticks": 0, "first": None},
                               "shape": {"ticks": 0, "first": None},
                               "decode": {"ticks": 0, "first": None},
                               "copy": {"ticks": 0, "first": None},
                               "decode_context": {"lifted": {"1": 1, "same": 1},
                                                  "native": {"0": 2, "same": 1}},
                               "episodes": {"count": 1, "listed": [[4, 7]], "longest": [4, 7],
                                            "first_long": None}}
    assert T.rule_problems("boot", {"record": RACE_ID, "group": "g", "rule": got}) == []
    # a native run fed as the lifted side resolves the race as natively: it holds too
    lifted, native = r_world(tmp_path, same=True)
    assert T.evaluate_rule(lifted, r_group(), 12, native)["held"] is True
    # and so does the lifted pattern with its worker decoding into context 3 instead
    lifted, native = r_world(tmp_path, lifted_at={(4, 1): 1, (5, 1): 1, (4, 3): 7, (5, 3): 7})
    assert T.evaluate_rule(lifted, r_group(), 12, native)["held"] is True


@pytest.mark.parametrize("world,rule,tick,why", [
    (dict(lifted_at={(5, 1): 8}), {}, 5, "the lifted decode is not the native one"),  # a changed decode
    (dict(lifted_at={(4, 1): 1}), {}, 4, "the lifted decode is not the native one"),  # a tick late
    (dict(lifted_at={(5, 2): 9}), {}, 5, "the lifted contexts are not one decode"),   # a stray write
    (dict(lifted_at={(6, 0): 3, (6, 2): 3, (6, 3): 3}), {}, 6,
     "the lifted copies are not context 0's values of the tick before"),              # not the copy
    (dict(native_at={(5, 2): 9}), {}, 5, "the native contexts are not one decode"),   # the capture
    ({}, dict(episode_max=3), 7, "a run longer than 3"),                               # too long
    (dict(lifted_at={(1, 2): 9}), {}, 1, "differs from native at or before tick 2"),  # before
    (dict(lifted_at={(2, 2): 9}), {}, 2, "differs from native at or before tick 2"),  # at kept_from
])
def test_a_context_permutation_breaks_at_the_tick_its_shape_fails(tmp_path, world, rule, tick,
                                                                   why):
    """The rule's falsifiers: a changed decode value, the same decode a tick late, a
    write into a copy, copies that are not the dispatcher's, a capture not of the
    shape, a run longer than its bound, and a difference before the contexts exist --
    each a bug at its tick, re-derived from what the rule observed."""
    lifted, native = r_world(tmp_path, **world)
    got = T.evaluate_rule(lifted, r_group(**rule), 12, native)
    assert got["held"] is False and got["broken"]["tick"] == tick and why in got["broken"]["why"]
    assert T.rule_problems("boot", {"record": RACE_ID, "group": "g", "rule": got}) == []
    assert T.evaluate_rule(lifted, r_group(**rule), tick - 1, native)["held"] is True


def r_effect(*moved):
    """A record's effect as the thread-race record names it: its frame word and the
    uploads moved, [frame, lifted tick, native tick] each."""
    return {"word": UPLOADS, "moved": [{"frame": f, "lifted": a, "native": b,
                                         "owner": f"{RACE_ID}/sofdec-mpv-slice-race"}
                                        for f, a, b in moved]}


NATIVE_UPLOADS = {4: 1, 6: 2, 8: 3}


@pytest.mark.parametrize("lifted_frames,moved,tick,why", [
    ({4: 1, 6: 2, 8: 3}, (), None, None),                       # every upload on its tick
    ({5: 1, 6: 2, 8: 3}, ((1, 5, 4),), None, None),             # the one the effect lists
    ({5: 1, 6: 2, 8: 3}, (), 4, "which the record's effect does not list"),
    ({4: 1, 6: 2, 8: 3}, ((1, 5, 4),), 4, "and the run does not move it so"),
    ({4: 1, 6: 2, 9: 3}, ((1, 5, 4),), 4, "and the run does not move it so"),
    ({5: 1, 6: 2, 9: 3}, ((1, 5, 4),), 8, "frame 3's upload is at tick 9 lifted and 8 native"),
    ({4: 1, 6: 3, 8: 2}, (), 6, "are not the capture's frames in its order"),
    ({4: 1, 6: 2}, (), 8, "upload 3 is frame none lifted and 3 native"),
])
def test_a_context_permutation_holds_the_uploads_to_the_record_s_effect(
        tmp_path, lifted_frames, moved, tick, why):
    """The race gate review, finding 3: the named effect was checked for presence
    alone. With its record's effect the rule reads the frame word on both logs on every
    pass: the uploads are the capture's frames in its order, and the ones on another
    tick than the capture's are exactly those the effect lists; an upload moved that it
    does not list, one it lists that is not moved so, and the frames out of order each
    break it at the earlier of their ticks, re-derived from what it observed."""
    lifted, native = r_world(tmp_path, lifted_frames=lifted_frames, native_frames=NATIVE_UPLOADS)
    group = dict(r_group(), effect=r_effect(*moved))
    got = T.evaluate_rule(lifted, group, 12, native)
    assert got["params"]["uploads"] == {"word": UPLOADS, "moved": [list(m) for m in moved]}
    up = got["observed"]["uploads"]
    assert (up["native"], up["native_uploads"]) == ("4:1 6:2 8:3", 3)
    if tick is None:
        assert (got["held"], got["broken"]) == (True, None)
    else:
        assert got["held"] is False and got["broken"]["tick"] == tick, got["broken"]
        assert why in got["broken"]["why"] and got["broken"]["va"] == UPLOADS
        assert T.evaluate_rule(lifted, group, tick - 1, native)["held"] is True
    assert T.rule_problems("boot", {"record": RACE_ID, "group": "g", "rule": got}) == []
    # without an effect the rule reads no uploads, as before the ruling's condition 2 held it
    assert "uploads" not in T.evaluate_rule(lifted, r_group(), 12, native)["observed"]


def test_an_uploads_observation_that_does_not_agree_with_itself_is_refused(tmp_path):
    lifted, native = r_world(tmp_path, lifted_frames={5: 1, 6: 2}, native_frames={4: 1, 6: 2})
    got = T.evaluate_rule(lifted, dict(r_group(), effect=r_effect((1, 5, 4))), 12, native)
    assert got["held"] is True
    for edit, why in (
            (lambda u: u.__setitem__("lifted_uploads", 3), "records uploads that do not agree"),
            (lambda u: u.__setitem__("lifted", "6:2 5:1"), "records uploads that do not agree"),
            (lambda u: u.__setitem__("through", 5), "records uploads that do not agree"),
            (lambda u: u.__setitem__("word", "0x01d0c6d4"), "records uploads that do not agree"),
            (lambda u: u.__setitem__("lifted", "4:1 6:2"), "its recorded values break it at 4")):
        r = json.loads(json.dumps(got))
        edit(r["observed"]["uploads"])
        problems = T.rule_problems("boot", {"record": RACE_ID, "group": "g", "rule": r})
        assert len(problems) == 1 and why in problems[0], (why, problems)


def test_a_decode_a_tick_apart_is_named_so(tmp_path):
    """The race comparer review's nit: a native capture that decodes a tick earlier (v15b,
    v15k) read "the lifted decode is not the native one in any context", which sends a
    reader looking for a wrong decode. The verdict now says when the lifted decode is the
    native decode of the tick before or after, and says nothing of it for a wrong one."""
    lifted, native = r_world(tmp_path, lifted_at={(3, 1): 7})          # a tick early
    got = T.evaluate_rule(lifted, r_group(kept_from=2), 12, native)
    assert got["broken"]["tick"] == 3
    assert "(it is the native decode of tick 4: the same decode a tick apart)" in \
        got["broken"]["why"], got["broken"]
    assert T.rule_problems("boot", {"record": RACE_ID, "group": "g", "rule": got}) == []
    lifted, native = r_world(tmp_path, lifted_at={(5, 1): 8})           # a changed decode
    got = T.evaluate_rule(lifted, r_group(), 12, native)
    assert got["broken"]["tick"] == 5 and "a tick apart" not in got["broken"]["why"]


def test_a_native_rule_s_recorded_verdict_is_re_derived_from_what_it_observed(tmp_path):
    lifted, native = s_world(tmp_path)
    scan = T.evaluate_rule(lifted, s_group(), 12, native)
    lifted, native = r_world(tmp_path)
    race = T.evaluate_rule(lifted, r_group(), 12, native)
    for rule, edit, why in (
            (scan, lambda o: o["apart"][0]["native"].__setitem__(0, "0x00000005"),
             "recorded broken at None, and its recorded values break it at 5"),
            (scan, lambda o: o.__setitem__("apart_ticks", 2), "not the first ones"),
            (race, lambda o: o.__setitem__("decode", {"ticks": 1, "first": None}),
             "records 1 ticks for decode"),
            (race, lambda o: o.__setitem__("pre", {"ticks": 1, "first": None}),
             "records 1 ticks for pre"),
            (race, lambda o: o["pre"].update(ticks=1, first={
                "tick": 1, "va": "0x01c0a7a8", "value": "0x00000009", "native": "0x00000001"}),
             "its recorded values break it at 1"),
            (race, lambda o: o["episodes"].__setitem__("longest", [4, 12]),
             "do not agree with each other"),
            (race, lambda o: o["copy"].update(ticks=1, first={
                "tick": 9, "va": "0x01c0bb28", "value": "0x00000007", "native": "0x00000001",
                "side": "lifted"}), "its recorded values break it at 9")):
        r = json.loads(json.dumps(rule))
        edit(r["observed"])
        got = T.rule_problems("boot", {"record": RACE_ID, "group": "g", "rule": r})
        assert len(got) == 1 and why in got[0], (why, got)


def test_a_result_is_held_to_its_records_groups_and_rules_as_they_are_now(monkeypatch):
    """residue_problems: a group the records give the replay dropped from its report,
    a native rule's parameters or a limit edited in the receipt, and a group scoped to a
    replay the registry does not register each fail it."""
    registry = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    boot, _ = measured_docs()
    assert T.residue_problems({"replays": [boot]}, registry) == []
    for edit, why in (
            (lambda gs: gs.pop(3), "its report lists groups"),
            (lambda gs: gs[3]["rule"]["params"].__setitem__("max_ticks", 2),
             "recorded as something other than its record's rule"),
            (lambda gs: gs[1]["rule"]["limits"].__setitem__("0x01a557ec", 1800),
             "recorded as something other than its record's rule")):
        doc = json.loads(json.dumps(boot))
        edit(doc["result"]["residues"]["groups"])
        got = T.residue_problems({"replays": [doc]}, registry)
        assert any(why in p for p in got), (why, got)
    real = T.residue_record

    def scoped(rid, root=None):
        rec = real(rid, root)
        for g in rec["groups"]:
            if "replays" in g:
                g["replays"] = g["replays"] + ["garden"]
        return rec
    monkeypatch.setattr(T, "residue_record", scoped)
    got = T.residue_problems({"replays": [boot]}, registry)
    assert any("applies to replays ['garden'] the registry does not register" in p
               for p in got), got


def test_a_broken_rule_that_differs_from_nothing_is_not_what_m5_reads_with_no_class():
    """The comparer review's nit: a lifted Steam word that equals native breaks "zero"
    and differs from nothing, so with no class M5 would not read it."""
    broken = {"kind": "zero", "held": False, "nonzero_words": 1,
              "broken": {"tick": 5, "va": "0x0174c32c", "value": "0x00000041",
                         "native": "0x00000041"}}
    r = T.settle(a_result(), a_block(differing=0, rule=broken), DET)
    assert r["class"] == "bug" and r["first_divergence"]["kind"] == "residue_rule"
    # DET's repeat opens a shifted column at 9, which is all M5 would see with no class
    assert r["residues"]["unclassed"] == {"tick": 9, "field": T.UNREPEATED,
                                          "class": "unrepeatable"}


def test_the_gate_s_rule_and_scope_falsifiers_each_fail_their_own_check_alone(monkeypatch):
    """M5's residue clause, fold:rc's and fold2:rc's four: the boot's native-relative
    rule recorded broken over what it observed (on the receipt as tick_replay writes
    it), its bound edited in its record after the comparison, and the boot's group
    scoped to Metal Harbor too and to a replay not registered; the decoder-context
    group's two, back with its own class (the user's ruling of 2026-09-27); and the
    class's eight: a word, a group, a replay and a routine added to it, a race recorded
    under it elsewhere, one of its words in another class, its effect taken away and
    the class named on Metal Harbor; and fold:final's four: a word of the four contexts
    no record lists in host timing, the race rule's bound edited in the receipt, the
    lifted first upload moved in the recorded uploads, and the named effect's upload
    edited in the record's list; the G4 interrogation's two (the named effect renamed to
    another moved upload, and an upload given to another record's group); and the review
    of the round-one fold's one (the registered capture the effect is measured against
    renamed). Each fails residue_problems for its own reason --
    once per registered replay that names the record it breaks, the class named on
    Metal Harbor once more for the result compared without it, and a record edit of
    the rule's parameters or of its effect (the effect taken away among them, since the
    rule reads its uploads from it) once more for the rule held to its record, the scope
    refusing it first -- and the unedited files pass."""
    registry_text = (ROOT / "Documentation" / "replays.json").read_text()
    boot, mh = measured_docs()
    receipt = json.dumps({"id": "s4-tick-replay-v1", "replays": [boot, mh]}, indent=1,
                         sort_keys=True) + "\n"
    records = {f"Documentation/receipts/residues/{rid}.json": rid for rid in (STEAM, TIMING, RACE_ID)}
    texts = {rid: (RESIDUES_DIR / f"{rid}.json").read_text() for rid in records.values()}
    assert T.residue_problems(json.loads(receipt), json.loads(registry_text)) == []
    scan = "host-timing-v1/sofdec-opening-scan's lifted rule"
    race = f"{RACE_ID}/sofdec-mpv-slice-race's lifted rule"
    want = [(1, f"boot: {scan} (native) is recorded broken"),
            (1, f"boot: {scan} was recorded as something other than its record's rule"),
            (1, "metalharbor: its report lists groups"),
            (1, "applies to replays ['garden'] the registry does not register"),
            (1, f"boot: {race} (native) is recorded broken"),
            (2, "its lifted rule's episode_max 7 is not the ruled scope's"),
            (1, "its words (table) are not the ruled scope's"),
            (1, "it holds one group, sofdec-mpv-slice-race, and it lists 2"),
            (1, "its replays are not the ruled scope's"),
            (1, "its owner's routines are not the ruled scope's"),
            (2, f"a host-thread-race record other than {RACE_ID}"),
            (2, f"{RACE_WORD} is Sofdec's decoder-context race, which is the host-thread-race "
                "class's alone"),
            (2, "does not name its one observable effect"),
            (2, "metalharbor: names host-thread-race-sofdec-v1, whose class holds Sofdec's "
                "decoder-context race on ['boot'] alone"),
            (2, f"{SPAN_WORD} is a word of Sofdec's four decoder contexts (0x01c0a480 to "
                "0x01c10080)"),
            (1, f"boot: {race} was recorded as something other than its record's rule"),
            (1, f"boot: {race} (native) is recorded broken at None, and its recorded values "
                "break it at 383"),
            (2, "lists [[270, 1031, 1033]] as this group's moved uploads, and the one "
                "observable effect it names is [270, 1032, 1033]"),
            (1, "the ruled scope's is frame 270's at 1032 and 1033"),
            (1, "the ruled scope lists"),
            (1, "1033 native on 'boot-ticks-v15b', and the ruled scope's is frame 270's at "
                "1032 and 1033 on 'boot-ticks-v15a'")]
    mine = [f for f in gate_residue_falsifiers()
            if f.get("replace") not in ('"replays": [', "| M5 replays | ", '"residue_classes": [')
            and not (f["replace"].startswith(GAMEPLAY_ANCHOR) and RACE_WORD not in f["with"]
                     and SPAN_WORD not in f["with"])]
    assert len(mine) == len(want)
    real = T.residue_record
    for f, (n, why) in zip(mine, want):
        assert f.get("occurrences", 1) == 1
        doc, registry, edited = json.loads(receipt), json.loads(registry_text), {}
        if f["file"] == "Documentation/receipts/s4-tick-replay-v1.json":
            assert receipt.count(f["replace"]) == 1
            doc = json.loads(receipt.replace(f["replace"], f["with"]))
        elif f["file"] == "Documentation/replays.json":
            assert registry_text.count(f["replace"]) == 1
            registry = json.loads(registry_text.replace(f["replace"], f["with"]))
        else:
            rid = records[f["file"]]
            assert texts[rid].count(f["replace"]) == 1
            edited[rid] = json.loads(texts[rid].replace(f["replace"], f["with"]))
        monkeypatch.setattr(T, "residue_record", lambda rid, root=None, edited=edited: (
            json.loads(json.dumps(edited[rid])) if rid in edited else real(rid, root)))
        got = T.residue_problems(doc, registry)
        assert len(got) == n and why in got[0], (why, got)


def test_the_report_evaluates_a_native_rule_on_both_logs(tmp_path, monkeypatch):
    """residue_report hands a native-relative rule the native capture's log beside the
    lifted one, and records what it observed; a scoped group is reported only for the
    replay it names."""
    lifted, native = s_world(tmp_path, apart=(5, 6))           # two ticks apart: broken
    group = dict(record(TIMING)["groups"][2])
    assert group["name"] == "sofdec-opening-scan"
    rec = {"groups": [group]}
    monkeypatch.setattr(T, "native_log", lambda entry, cap, root=None: native)
    blocks = {}
    for name in ("boot", "metalharbor"):
        ctx = {"classes": [{"id": TIMING, "class": "host-timing", "record": rec, "replay": name}],
               "mask_words": set()}
        blocks[name] = T.residue_report({"name": name, "native": {}}, ctx,
                                        {"words": {"path": str(lifted)}}, {"compared_ticks": 12},
                                        root=Path("/"))
    assert blocks["metalharbor"]["groups"] == []
    (g,) = blocks["boot"]["groups"]
    assert g["rule"]["kind"] == "native" and g["rule"]["held"] is False
    assert g["rule"]["broken"]["tick"] == 6 and g["rule"]["observed"]["apart_ticks"] == 2
    assert g["lifted"]["differing"] == 4


# The residue records' groups as reviewed (the fold review of fold:rc, finding 3): each
# group's scope, the sha256 of its word list and its lifted rule but the prose, the held
# host-thread-race record's group among them. evidence_shape's pins normalise digits, so
# they cannot see a threshold move (max_ticks 1 -> 50, kept_from 378 -> 1300, a below
# limit 180 -> 1800) or a word swapped for one of the same letter pattern; a loosened
# threshold is the user's (AGENTS.md rule 10), so a change here is a reviewed edit of this
# literal, made in the same commit as the record. The user's ruling of 2026-09-27 moved the
# held decoder-context group to its own class with its words unchanged (the same
# words_sha256) and SF's context-race rule replaced by the context-permutation rule, which
# the registered capture keeps (unit RACE).
REVIEWED_GROUPS = {
    "host-services-steam-stats-v1/steam-achievement-table": {
        "replays": None,
        "words_sha256": "a902eaea959ca7226361f6f102364fb9076e4fec38fc0fc9d17d0de9944c01b2",
        "rule": {"kind": "zero"}},
    "host-timing-v1/loading-screen-frames": {
        "replays": None,
        "words_sha256": "57a2092da13363f3ad11e99bdc95195aa592d80c99ece2853964a4b2d1c3cbaf",
        "rule": {"kind": "below",
                 "limits": {"0x01a557ec": 180, "0x01af1970": 24, "0x0174b494": 30}}},
    "host-timing-v1/xeci-read-completion": {
        "replays": None,
        "words_sha256": "1a5ec786e2b7f122b339b85f934d6b13892fac1fc5f4c5819ebfa38a6c248fd9",
        "rule": {"kind": "none"}},
    "host-timing-v1/sofdec-opening-scan": {
        "replays": ["boot"],
        "words_sha256": "6692bd9798b93a07cf460ff6c28de4e9cee9628ebcd9f5d28fb45fdd92178bd6",
        "rule": {"kind": "native", "form": "one-tick-scan", "max_ticks": 1,
                 "reset": "0xffffffff", "progress": "0x01c07ac8"}},
    "host-timing-v1/identity_open_shift": {
        "replays": None,
        "words_sha256": "b8db9ad3681829dd29a755c6d9712d705c82bfed3528cb9027448291e0c88204",
        "rule": None},
    "host-thread-race-sofdec-v1/sofdec-mpv-slice-race": {
        "replays": ["boot"],
        "words_sha256": "18261b2dc2f35f4fa941f590a6a163b038810661370ff13dd2256c5082b597e7",
        "rule": {"kind": "native", "form": "context-permutation", "dispatcher": 0,
                 "kept_from": 378, "episode_max": 6}},
}


def test_the_residue_records_hold_the_reviewed_scopes_words_and_thresholds():
    import hashlib
    got = {}
    for rid, rec in ((STEAM, record(STEAM)), (TIMING, record(TIMING)), (RACE_ID, record(RACE_ID)),
                     *((f"held/{h}", r) for h, r in held_records().items())):
        for g in rec["groups"]:
            got[f"{rid}/{g['name']}"] = {
                "replays": g.get("replays"),
                "words_sha256": hashlib.sha256(json.dumps(g["words"]).encode()).hexdigest(),
                "rule": {k: v for k, v in g["lifted_rule"].items() if k not in ("text", "why")}}
        if rec.get("identity_open_shift"):
            got[f"{rid}/identity_open_shift"] = {"replays": None, "rule": None, "words_sha256":
                hashlib.sha256(json.dumps(rec["identity_open_shift"]["words"]).encode()).hexdigest()}
    assert got == REVIEWED_GROUPS


# ------------------------------------------------ groups held for the user's ruling
#
# A group proposed for a class and not the agent's to rule in is held in
# Documentation/receipts/residues/held/, which no comparison reads, under a schema and
# a class record_problems refuses (RESIDUE_CLASSES), so its words stay judged until the
# user rules. The one held group, unit SF's decoder contexts, was ruled on 2026-09-27
# (its own class, host-thread-race-sofdec-v1) and none is held now; the tests below hold
# every held record there, and still read once none is.

HELD_DIR = RESIDUES_DIR / "held"
HELD_SCHEMA = "sa2-residue-held/v1"


def held_records():
    return {p.stem: json.loads(p.read_text()) for p in sorted(HELD_DIR.glob("*.json"))}


def test_a_held_record_is_no_class_and_no_comparison_reads_it():
    import stage_exit as SE
    # a record of the held schema and class is refused as a class, whatever it holds
    fake = dict(record(TIMING), schema=HELD_SCHEMA, **{"class": "held"})
    got = T.record_problems(fake, TIMING)
    assert "held" not in T.RESIDUE_CLASSES
    assert any("its class 'held' is none the user ruled" in p for p in got), got
    assert any(f"its schema is '{HELD_SCHEMA}'" in p for p in got), got
    entry = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"][0]
    for rid, held in held_records().items():
        assert (held["schema"], held["class"], held["id"]) == (HELD_SCHEMA, "held", rid)
        got = T.record_problems(held, rid)
        assert any("is none the user ruled" in p for p in got), got
        # the comparer reads Documentation/receipts/residues/<id>.json alone
        for name in (rid, f"held/{rid}"):
            with pytest.raises(SystemExit, match="no residue class record"):
                T.residue_record(name)
        # so a registry entry naming it is refused before anything runs
        with pytest.raises(SystemExit, match="no residue class record"):
            T.residue_classes(dict(entry, **{T.RESIDUE_FIELD: entry[T.RESIDUE_FIELD] + [rid]}))
        # and M5's row cannot name it as a class
        assert not SE.residue_class(rid)
        assert not SE.residues_only(f"boot residues only/1200 ({rid})")


# ------------------------------------------ the host-thread-race class (2026-09-27)
#
# The user's ruling (Documentation/decisions/2026-09-27-S4-sofdec-thread-race-residue.md):
# a class of its own for the Sofdec decoder's worker-thread race, scoped to that race
# alone, naming its one observable effect. The scope is THREAD_RACE_SCOPE in the
# comparer; each edit below that widens it is refused by record_problems (condition 1),
# as is a record that does not name its effect (condition 2).

def test_the_thread_race_record_is_the_ruled_scope():
    rec = record(RACE_ID)
    assert (rec["class"], rec["ruling"]["record"]) == (T.THREAD_RACE_CLASS,
                                                       T.THREAD_RACE_SCOPE["ruling"])
    assert T.record_problems(rec, RACE_ID) == []
    (g,) = rec["groups"]
    assert (g["name"], g["replays"], g["routines"]) == (
        T.THREAD_RACE_SCOPE["group"], ["boot"], ["0x007fbed0", "0x007fbe40"])
    assert len(g["words"]) == 244 and g["table"] == T.THREAD_RACE_SCOPE["table"]
    # the effect the user named, in the replay's ticks and in SF's GameLoop returns
    e = rec["effect"]
    assert (e["frame"], e["lifted"], e["native"]["tick"], e["native"]["gameloop_returns"]) == (
        270, {"tick": 1032, "gameloop_returns": 1031}, 1033, 1032)
    # every upload the lifted boot makes on another tick than the capture, and whose each
    # is: the named effect, this race's, and the opening pass's first frame, host timing's
    assert [(m["frame"], m["lifted"], m["native"], m["owner"]) for m in e["moved"]] == [
        (1, 389, 383, "host-timing-v1/sofdec-opening-scan"),
        (270, 1032, 1033, f"{RACE_ID}/sofdec-mpv-slice-race")]
    assert e["word"] == T.THREAD_RACE_SCOPE["uploads_word"]
    # the named effect and the moved uploads are the scope's own, owners included (the G4
    # interrogation, Reviewer B's finding 2)
    pin = T.THREAD_RACE_SCOPE["effect"]
    assert (e["frame"], e["lifted"]["tick"], e["native"]["tick"], e["native"]["capture"]) == (
        pin["frame"], pin["lifted"], pin["native"], pin["capture"]) == (270, 1032, 1033,
                                                                          "boot-ticks-v15a")
    assert [[m["frame"], m["lifted"], m["native"], m["owner"]] for m in e["moved"]] == \
        T.THREAD_RACE_SCOPE["moved"]
    assert {k: g["lifted_rule"][k] for k in T.THREAD_RACE_SCOPE["rule"]} == \
        T.THREAD_RACE_SCOPE["rule"] == {"dispatcher": 0, "kept_from": 378, "episode_max": 6}


def race_rec():
    return json.loads(json.dumps(record(RACE_ID)))


def renamed_effect(rec):
    """The named effect renamed to the movie's first upload, the two moved uploads'
    owners swapped to match: the record stays consistent with itself and with the
    uploads the rule observes, whose ticks do not move."""
    e = rec["effect"]
    e["frame"], e["lifted"], e["native"] = 1, {"tick": 389, "gameloop_returns": 388}, dict(
        e["native"], tick=383, gameloop_returns=382)
    a, b = e["moved"]
    a["owner"], b["owner"] = b["owner"], a["owner"]


def widened_table(rec):
    g = rec["groups"][0]
    g["table"]["offsets"] = g["table"]["offsets"] + ["0x7b8"]
    g["words"] = [f"0x{va:08x}" for va in T.table_words(g["table"])]


@pytest.mark.parametrize("edit,why", [
    (widened_table, "its words (table) are not the ruled scope's"),
    (lambda r: r["groups"].append(dict(r["groups"][0], name="another-race", table={
        "at": ["0x01c10000"]}, words=["0x01c10000"])), "it holds one group"),
    (lambda r: r["groups"][0].__setitem__("replays", ["boot", "metalharbor"]),
     "its replays are not the ruled scope's"),
    (lambda r: r["groups"][0].__setitem__("routines", ["0x007fbed0", "0x007fbe40", "0x007fbb90"]),
     "its owner's routines are not the ruled scope's"),
    (lambda r: r["groups"][0].__setitem__("owner", "a dispatcher, somewhere, posting a job"),
     "its owner does not name"),
    (lambda r: r["groups"][0].__setitem__("name", "sofdec-race"), "its name is not"),
    (lambda r: r["groups"][0]["lifted_rule"].update(form="one-tick-scan", max_ticks=1,
                                                   reset="0x0", progress="0x01c07ac8"),
     "is not the context-permutation rule"),
    (lambda r: r["ruling"].__setitem__(
        "record", "Documentation/decisions/2026-09-26-S4-fast-path-and-S5-host.md"),
     "its ruling is not"),
    (lambda r: r.pop("effect"), "does not name its one observable effect"),
    (lambda r: r["effect"].pop("word"), "does not name its one observable effect"),
    (lambda r: r["effect"].__setitem__("evidence", ["private/no-such-file.json"]),
     "effect cites 'private/no-such-file.json'"),
    # the rule's parameters are the scope's (the race comparer review, finding 2)
    (lambda r: r["groups"][0]["lifted_rule"].__setitem__("episode_max", 1200),
     "its lifted rule's episode_max 1200 is not the ruled scope's"),
    (lambda r: r["groups"][0]["lifted_rule"].__setitem__("kept_from", 1),
     "its lifted rule's kept_from 1 is not the ruled scope's"),
    (lambda r: r["groups"][0]["lifted_rule"].__setitem__("dispatcher", 1),
     "its lifted rule's dispatcher 1 is not the ruled scope's"),
    # the effect is read from the scope's frame word, and lists every upload it moves,
    # one of them the named effect and each other one another record's group's
    (lambda r: r["effect"].__setitem__("word", "0x01d0c6d4"), "not the scope's frame word"),
    (lambda r: r["effect"].pop("moved"), "does not list the uploads the lifted run makes"),
    (lambda r: r["effect"]["moved"][1].__setitem__("lifted", 1031),
     "and the one observable effect it names is [270, 1032, 1033]"),
    (lambda r: r["effect"]["moved"].pop(1),
     "lists [] as this group's moved uploads"),
    (lambda r: r["effect"]["moved"][0].__setitem__("owner", f"{RACE_ID}/sofdec-mpv-slice-race"),
     "lists [[1, 389, 383], [270, 1032, 1033]] as this group's moved uploads"),
    (lambda r: r["effect"]["moved"][0].__setitem__("owner", "host-timing-v1/no-such-group"),
     "which is no group of another residue class record"),
    (lambda r: r["effect"]["moved"][0].__setitem__("native", 389),
     "does not list the uploads the lifted run makes"),
    # consistent with itself, and refused by the scope's pins alone (the G4 interrogation,
    # Reviewer B's finding 2): the effect renamed to the first frame with the owners swapped
    # to match, frame 1 given to another record's group, and an upload added to the list
    # with a real owner
    (renamed_effect, "the ruled scope's is frame 270's at 1032 and 1033"),
    (lambda r: r["effect"]["moved"][0].__setitem__("owner", "host-timing-v1/loading-screen-frames"),
     "the ruled scope lists"),
    (lambda r: r["effect"]["moved"].insert(1, {"frame": 100, "lifted": 694, "native": 693,
                                               "owner": "host-timing-v1/loading-screen-frames"}),
     "the ruled scope lists"),
    (lambda r: r["effect"]["native"].__setitem__("capture", "boot-ticks-v15b"),
     "the ruled scope's is frame 270's at 1032 and 1033 on 'boot-ticks-v15a'"),
])
def test_a_thread_race_record_that_leaves_the_ruled_scope_is_refused(edit, why):
    rec = race_rec()
    edit(rec)
    got = T.record_problems(rec, RACE_ID)
    assert got and any(why in p for p in got), got


def test_a_consistent_edit_of_the_effect_and_its_observation_is_refused_by_the_scope():
    """Reviewer B's finding 2 in full: an upload the lifted run moves, added to the
    record's list with an owner that exists, and the observation moved to match. The
    uploads clause, which reads the list, accepts the pair, as every check did before
    the scope pinned the list; the scope refuses the record."""
    # a tick for each frame, in order, as the boot's: frame 1 at 383, then every other tick
    # from 497, frame 270 at 1033; lifted, frame 1 at 389 and frame 270 at 1032, and frame
    # 100 a tick late
    native = {1: 383, **{f: 497 + 2 * (f - 2) for f in range(2, 270)},
              **{f: 1033 + 2 * (f - 270) for f in range(270, 300)}}
    lifted = dict(native)
    lifted[1], lifted[270], lifted[100] = 389, 1032, native[100] + 1
    assert (native[100], lifted[100]) == (693, 694)
    obs = {"word": T.THREAD_RACE_SCOPE["uploads_word"], "through": 1200,
           "initial": ["0x00000000", "0x00000000"],
           "lifted": " ".join(f"{t}:{f}" for f, t in sorted(lifted.items(), key=lambda x: x[1])),
           "native": " ".join(f"{t}:{f}" for f, t in sorted(native.items(), key=lambda x: x[1]))}
    rec = race_rec()
    rec["effect"]["moved"].insert(1, {"frame": 100, "lifted": lifted[100], "native": native[100],
                                      "owner": "host-timing-v1/loading-screen-frames"})
    params = T.native_params(rec["groups"][0]["lifted_rule"], rec["groups"][0]["table"],
                             rec["effect"])["uploads"]
    assert params["moved"] == [[1, 389, 383], [100, 694, 693], [270, 1032, 1033]]
    assert T.uploads_verdict(params, obs) is None               # the rule alone accepts it
    unedited = T.native_params(race_rec()["groups"][0]["lifted_rule"],
                               race_rec()["groups"][0]["table"], race_rec()["effect"])["uploads"]
    assert "frame 100's upload" in T.uploads_verdict(unedited, obs)["why"]
    got = T.thread_race_problems(rec, RACE_ID)
    assert any("the ruled scope lists" in p for p in got), got


def test_a_race_anywhere_else_is_refused_until_the_scope_is_changed():
    """Condition 1's other half: another record of the class (a race recorded anywhere
    else under it), a word of the race in another class's record, and the class named on
    a replay it does not cover are each refused, whatever else they hold."""
    other = dict(record(STEAM), **{"class": T.THREAD_RACE_CLASS})
    got = T.record_problems(other, STEAM)
    assert got == [f"residue class {STEAM}: a host-thread-race record other than {RACE_ID}: the "
                   "class holds Sofdec's decoder-context race alone, and a race anywhere else is a "
                   f"bug until reviewed ({T.THREAD_RACE_SCOPE['ruling']}, condition 1)"]
    timing = with_group(record(TIMING), "0x01c0a7a8")
    got = T.record_problems(timing, TIMING)
    assert any("0x01c0a7a8 is Sofdec's decoder-context race, which is the host-thread-race "
               "class's alone" in p for p in got), got
    # and any other word of the four contexts' span, which no record lists (the race
    # comparer review, finding 3): context 1's unlisted offset 0x7b8 and the span's ends
    for word in ("0x01c0c338", "0x01c0a480", "0x01c1007c"):
        got = T.record_problems(with_group(record(TIMING), word), TIMING)
        assert [p for p in got if f"{word} is a word of Sofdec's four decoder contexts "
                "(0x01c0a480 to 0x01c10080)" in p] == got and len(got) == 1, (word, got)
    for word in ("0x01c0a47c", "0x01c10080"):                     # just outside it
        assert not [p for p in T.record_problems(with_group(record(TIMING), word), TIMING)
                    if "decoder contexts" in p]
    reg = json.loads((ROOT / "Documentation" / "replays.json").read_text())["replays"]
    mh = next(e for e in reg if e["name"] == "metalharbor")
    with pytest.raises(SystemExit, match="whose class holds Sofdec's decoder-context race on "
                                         r"\['boot'\] alone"):
        T.residue_classes(dict(mh, **{T.RESIDUE_FIELD: mh[T.RESIDUE_FIELD] + [RACE_ID]}))


def test_the_class_named_on_another_replay_is_refused_by_that_check_alone(tmp_path):
    """The race gate review, finding 10: M5's twenty-seventh falsifier (the class named
    on Metal Harbor in the registry) is also caught by the check that holds a result to
    the registry's classes, so the gate cannot show the refusal failing on its own. Here
    Metal Harbor's result was compared under the class too, so only the refusal sees it;
    with the refusal gone, nothing does."""
    registry = json.loads((ROOT / "Documentation" / "replays.json").read_text())
    boot, mh = measured_docs()
    for e in registry["replays"]:
        if e["name"] == "metalharbor":
            e[T.RESIDUE_FIELD] = e[T.RESIDUE_FIELD] + [RACE_ID]
    mh["result"]["residues"]["classes"].append({"id": RACE_ID, "class": T.THREAD_RACE_CLASS})
    receipt = {"replays": [boot, mh]}
    got = T.residue_problems(receipt, registry)
    assert got == ["metalharbor: names host-thread-race-sofdec-v1, whose class holds Sofdec's "
                   f"decoder-context race on ['boot'] alone ({T.THREAD_RACE_SCOPE['ruling']})"], got
    # the comparer with that refusal taken out, as M5's blinding tests take a part out
    import importlib.util
    refusal = '        if rec["class"] == THREAD_RACE_CLASS and name not in THREAD_RACE_SCOPE["replays"]:'
    source = (ROOT / "tools" / "s4" / "tick_replay.py").read_text()
    assert source.count(refusal) == 1
    blind = tmp_path / "tick_replay_blind.py"
    blind.write_text(source.replace(refusal, "        if False:"))
    spec = importlib.util.spec_from_file_location("tick_replay_blind", blind)
    B = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(B)
    assert B.ROOT == T.ROOT
    assert B.residue_problems(receipt, registry) == []


def test_the_race_words_leave_no_judged_digest_they_were_in():
    """Build B so that it checks more, not less (unit RACE's brief): no word of the class
    leaves a judged digest it was in. Each one the boot's mask does not select is one its
    own source runs disagree on (the re-take batch's boots split the race among
    themselves; unit IN, 2026-09-26), so the class takes nothing out of the digests and
    adds its rule over all 244 on both sides."""
    words = set(T.table_words(T.THREAD_RACE_SCOPE["table"]))
    for rid in (STEAM, TIMING):
        assert not words & {va for g in record(rid)["groups"] for va in T.table_words(g["table"])}
    out = words - T.OM.read_mask_words(ROOT / "harness" / "replay" / "masks" / "boot.h")
    if out:
        receipt = json.loads((ROOT / "Documentation" / "receipts"
                              / "s4-mask-boot-v1.json").read_text())
        logs = [ROOT / s["log"] for s in receipt["sources"]]
        apart: set[int] = set()
        for log in logs[1:]:
            rows, _ = T.WL.diff(logs[0], log, min(out), max(out) + 4)
            apart |= {r["va"] for r in rows}
        assert out <= apart, sorted(f"{w:#010x}" for w in out - apart)


def test_a_from_tick_that_ends_a_window_is_rendered_with_its_class():
    """The fold review of fold:rc, finding 4: what M5 would read with no class may be a
    window's end, not a lifted difference; the row says which (`residues_from_class`)."""
    import stage_exit as SE
    det = {"judged_ticks": 9, "repeats": [{"residues": [{"first_in_mask_tick": 3}],
                                           "open_shift": {"first_shifted": None}}]}
    r = T.settle(a_result(), a_block(), det)
    rec = T.m5_record({"name": "boot", "result": r})
    assert (rec["residues_from"], rec["residues_from_class"]) == (3, "unrepeatable")
    assert SE.m5_part(rec) == f"boot residues only/9 ({STEAM}) from 3 (unrepeatable)"
    # the class m5_record carries is the unclassed reading's, whichever window it ends
    r["residues"]["unclassed"] = {"tick": 3, "field": T.BOUNDED, "class": "bounded"}
    rec = T.m5_record({"name": "boot", "result": r})
    assert rec["residues_from_class"] == "bounded"
    assert SE.m5_part(rec) == f"boot residues only/9 ({STEAM}) from 3 (bounded)"
    # a bug keeps the plain form
    r = T.settle(a_result(), a_block(), DET)
    rec = T.m5_record({"name": "boot", "result": r})
    assert "residues_from_class" not in rec and SE.m5_part(rec).endswith(" from 4")
