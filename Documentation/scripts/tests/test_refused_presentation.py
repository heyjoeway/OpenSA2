"""The refusal gate: reasons, native outcomes, runtime markers, and M4's count.

`Documentation/scripts/check_refused_presentation.py` holds every refusal the
runtime makes to a recorded reason and to the native outcome it reproduces or
does not (the user's M4 ruling of 2026-09-23). `tools/s4/refusal_audit.py`
derives the refusals, reading the runtime's `sa2_import_refused` markers once
any exists, and `tools/s4/import_status.py counted` leaves every
non-reproducing refusal out of M4's implemented count. Each test names a way
one of them could pass a record it should refuse.

The tests of the real record hold it to the runtime it is written for: the
tree's `runtime/sa2_imports.c` with every dynamic row given an implementation
(`integrated`), and a marker on every policy entry. The record removes the two
DirectDraw null-impl entries in the same commit that gives those rows
implementations, so on the tree before that commit the audit refuses the
record (the rows refuse with no entry), and on the tree after it `integrated`
changes nothing; the tests pass on both.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
import check_refused_presentation as G  # noqa: E402
import import_status as IS  # noqa: E402
import native_outcomes as NO  # noqa: E402
import reach_receipt as RR  # noqa: E402
import refusal_audit as RA  # noqa: E402

DYN = "static const dyn_row g_dyn_table[]"
STUBS = "static const stub_row g_stubs[]"


def src(tmp_path: Path, text: str, name: str = "fixture.c") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


# ---------------------------------------------------------------- the marker parser

def test_a_literal_marker_is_read_with_its_code_and_line(tmp_path: Path) -> None:
    p = src(tmp_path, 'uint32_t sa2_import_refused(const char *dll, const char *name, uint32_t code);\n'
                      "static uint32_t f(void) {\n"
                      '    return sa2_import_refused("DINPUT8.dll", "DirectInput8Create",\n'
                      "                              0x80004005u);\n"
                      "}\n")
    assert RA.markers([p]) == [{"dll": "DINPUT8.dll", "name": "DirectInput8Create",
                                "code": "0x80004005u", "file": str(p), "line": 3}]


def test_what_only_looks_like_a_marker_is_not_one(tmp_path: Path) -> None:
    p = src(tmp_path, "/* sa2_import_refused(\"A.dll\", \"f\", 1) in a comment */\n"
                      "// sa2_import_refused(\"A.dll\", \"g\", 1);\n"
                      'static const char *s = "sa2_import_refused(\\"A.dll\\", \\"h\\", 1)";\n'
                      "extern uint32_t sa2_import_refused(const char *dll, const char *name,\n"
                      "                                   uint32_t code);\n"
                      "uint32_t sa2_import_refused(const char *dll, const char *name, uint32_t code) {\n"
                      "    return code;\n"
                      "}\n")
    assert RA.markers([p]) == []


def test_a_marker_through_variables_is_refused_by_name(tmp_path: Path) -> None:
    p = src(tmp_path, "static uint32_t f(const char *d) { return sa2_import_refused(d, \"f\", 1); }\n")
    with pytest.raises(SystemExit, match=r"fixture\.c:1: sa2_import_refused must be called with"):
        RA.markers([p])


def test_markers_in_other_statement_positions_are_read(tmp_path: Path) -> None:
    p = src(tmp_path, "static void f(void) {\n"
                      '    if (x) (void)sa2_import_refused("A.dll", "f", E_FAIL);\n'
                      '    y = sa2_import_refused("B.dll", "Ordinal#2", 1167u);\n'
                      "}\n"
                      '#define REFUSE_C sa2_import_refused("C.dll", "g", 0u)\n')
    assert [(m["dll"], m["name"], m["code"], m["line"]) for m in RA.markers([p])] == [
        ("A.dll", "f", "E_FAIL", 2), ("B.dll", "Ordinal#2", "1167u", 3), ("C.dll", "g", "0u", 5)]


# ---------------------------------------------------------------- the join

def test_pefiles_invented_ordinal_names_join_the_runtimes_ordinals() -> None:
    """The census names OLEAUT32's ordinal 2 SysAllocString, from pefile's own
    table; the runtime names it Ordinal#2. Nothing else is case-folded."""
    assert RA.key("OLEAUT32.dll", "SysAllocString") == RA.key("OLEAUT32", "Ordinal#2")
    assert RA.key("XINPUT1_3.dll", "ordinal#4") == RA.key("XINPUT1_3", "Ordinal#4")
    assert RA.key("KERNEL32.dll", "getversion") != RA.key("KERNEL32.dll", "GetVersion")
    assert RA.key("KERNEL32.dll", "SysAllocString") == ("KERNEL32", "SysAllocString")


# ---------------------------------------------------------------- runtimes to hold the record to

def integrated(tmp_path: Path) -> Path:
    """runtime/sa2_imports.c with every NULL dynamic implementation given a
    stand-in, as the device units' integration leaves it (module docstring)."""
    lines = RA.SRC.read_text().split("\n")
    for r in RA.table(lines, DYN):
        if r["impl"] in (None, "NULL"):
            i = r["line"] - 1
            while "}" not in lines[i]:
                i += 1
            lines[i] = re.sub(r"\bNULL(\s*\})", r"imp_stand_in\1", lines[i], count=1)
    p = tmp_path / "sa2_imports.integrated.c"
    p.write_text("\n".join(lines))
    assert all(r["impl"] not in (None, "NULL") for r in RA.table(p.read_text().split("\n"), DYN))
    return p


def with_null_row(tmp_path: Path) -> tuple[Path, dict]:
    """The integrated runtime with its first answered dynamic row made NULL:
    the tree's state before the integration, in miniature."""
    base = integrated(tmp_path)
    lines = base.read_text().split("\n")
    row = RA.table(lines, DYN)[0]
    i = row["line"] - 1
    lines[i] = lines[i].replace(row["impl"] + "}", "NULL}", 1)
    p = tmp_path / "sa2_imports.nullrow.c"
    p.write_text("\n".join(lines))
    return p, row


def stub_everything(tmp_path: Path) -> Path:
    """The integrated runtime with a stub row for every census import that has
    none, the day the no-stub work list is empty."""
    lines = integrated(tmp_path).read_text().split("\n")
    have = {RA.key(r["dll"], r["name"]) for r in RA.table(lines, STUBS)}
    census = json.loads(RA.CENSUS.read_text())["imports"]
    rows = [f'    {{"{i["dll"]}", "{i["name"]}", 0, SA2_IMP_STDCALL, imp_stand_in}},'
            for i in census if RA.key(i["dll"], i["name"]) not in have]
    at = next(n for n, ln in enumerate(lines) if ln.startswith(STUBS + " = {"))
    lines[at + 1:at + 1] = rows
    p = tmp_path / "sa2_imports.stubbed.c"
    p.write_text("\n".join(lines))
    return p


def reasons(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "reasons.json"
    p.write_text(json.dumps({"schema": G.SCHEMA, "reasons": entries}))
    return p


POLICY = {"dll": "DINPUT8.dll", "name": "DirectInput8Create", "class": "presentation",
          "kind": "policy"}


# ---------------------------------------------------------------- the audit's regimes

def test_before_any_marker_a_policy_entry_is_taken_at_its_word(tmp_path: Path) -> None:
    a = RA.audit(reasons(tmp_path, [POLICY]), sources=[], src=integrated(tmp_path))
    assert a["marker_checks"] is False
    row = next(r for r in a["refused"] if r["name"] == "DirectInput8Create")
    assert row["kind"] == "policy" and row["reason"]["kind"] == "policy"
    assert a["markers_without_entry"] == [] and a["policy_without_marker"] == []


def test_once_a_marker_exists_a_policy_entry_needs_one(tmp_path: Path) -> None:
    m = src(tmp_path, 'static uint32_t f(void) { return sa2_import_refused("ole32.dll", '
                      '"CoCreateInstance", 0x80040154u); }\n')
    a = RA.audit(reasons(tmp_path, [POLICY]), sources=[m], src=integrated(tmp_path))
    assert a["marker_checks"] is True
    assert a["policy_without_marker"] == ["DINPUT8!DirectInput8Create"]
    assert [(r["dll"], r["name"]) for r in a["markers_without_entry"]] == [
        ("ole32.dll", "CoCreateInstance")]


def test_a_marker_naming_nothing_the_runtime_answers_is_refused(tmp_path: Path) -> None:
    m = src(tmp_path, 'static uint32_t f(void) { return sa2_import_refused("NOPE.dll", "x", 1); }\n')
    with pytest.raises(SystemExit, match="has no implementation of it to refuse from"):
        RA.audit(reasons(tmp_path, [POLICY]), sources=[m], src=integrated(tmp_path))


def test_a_null_impl_entry_for_a_name_that_now_answers_is_stale(tmp_path: Path) -> None:
    """A dynamic row given an implementation stops refusing, and its old reason
    must stop excusing it: the DirectDraw entries' fate at the integration."""
    runtime = integrated(tmp_path)
    answered = RA.table(runtime.read_text().split("\n"), DYN)[0]
    entry = {"dll": answered["dll"], "name": answered["name"], "class": "presentation",
             "kind": "null-impl"}
    a = RA.audit(reasons(tmp_path, [entry]), sources=[], src=runtime)
    assert a["stale_reasons"] == [f"{RA.module_key(answered['dll'])}!{answered['name']}"]


def test_a_null_implementation_with_no_entry_stops_the_audit(tmp_path: Path) -> None:
    """A dynamic name the census does not cover carries its class in its entry,
    so a NULL row without one is unclassified; with a null-impl entry it is an
    excused refusal."""
    runtime, row = with_null_row(tmp_path)
    with pytest.raises(SystemExit, match=f"dynamic {row['dll']}!{row['name']} refuses "
                                         r"\(null-impl\) and has no reasons-file entry"):
        RA.audit(reasons(tmp_path, []), sources=[], src=runtime)
    entry = {"dll": row["dll"], "name": row["name"], "class": "presentation",
             "kind": "null-impl"}
    a = RA.audit(reasons(tmp_path, [entry]), sources=[], src=runtime)
    assert [(r["name"], r["kind"], r["surface"]) for r in a["refused"]
            if r["name"] == row["name"]] == [(row["name"], "null-impl", "dynamic")]


# ---------------------------------------------------------------- the claim rules

REC = {"outcomes": [
    {"id": "works", "imports": ["A.dll!f", "A.dll!g"], "answer": "success"},
    {"id": "absent", "imports": ["B.dll!Ordinal#4"], "answer": "failure"}]}
ENV = {"SA2_ON": "1", "SA2_ZERO": "0", "SA2_SET": "1"}


def entry(name: str = "f", dll: str = "A.dll", **native) -> dict:
    return {"dll": dll, "name": name, "native": native}


# A reach run that called both of outcome `works`'s imports and refused neither.
CALLED = {G.key("A.dll", "f"): {"calls": 1, "refused": 0},
          G.key("A.dll", "g"): {"calls": 1, "refused": 0}}


def claims(*entries: dict, rec: dict = REC, env: dict = ENV,
           refused: dict | str | None = None) -> list[str]:
    """By default against a reach run whose recomputed counts show both of
    outcome `works`'s imports called and never refused."""
    return G.claim_problems({"reasons": list(entries)}, rec, env,
                            CALLED if refused is None else refused)


@pytest.mark.parametrize("native", [
    {"outcome": "works", "reproduces": True, "switches": ["SA2_ON"]},
    {"outcome": "works", "reproduces": False},
    {"outcome": "works", "reproduces": False, "switches": ["SA2_OFF"]},
    {"outcome": None, "reproduces": False},
])
def test_claims_the_evidence_supports_pass(native: dict) -> None:
    assert claims(entry(**native)) == []


def test_a_refusal_reproducing_a_failure_needs_no_switch() -> None:
    assert claims(entry("Ordinal#4", "B.dll", outcome="absent", reproduces=True)) == []


@pytest.mark.parametrize("native,why", [
    # The 2026-09-23 review's case: true, and the switches deleted.
    ({"outcome": "works", "reproduces": True},
     "A.dll!f claims to reproduce outcome works, which answers success natively, and names "
     "no switch"),
    ({"outcome": "works", "reproduces": True, "switches": []}, "answers success natively"),
    ({"outcome": "works", "reproduces": True, "switches": ["SA2_ON", "SA2_OFF"]},
     "A.dll!f claims to reproduce the native outcome, but SA2_OFF is not on in the replay "
     "configuration"),
    ({"outcome": "works", "reproduces": True, "switches": ["SA2_ZERO"]},
     "SA2_ZERO is not on"),
    ({"outcome": None, "reproduces": True}, "A.dll!f names no native outcome (outcome null)"),
    ({"outcome": None}, "names no native outcome"),
    ({"outcome": "works", "reproduces": 1}, "native.reproduces is 1, not true or false"),
    ({"outcome": "nope", "reproduces": False}, "names native outcome 'nope', which"),
    ({"outcome": "works", "reproduces": False, "switches": "SA2_ON"},
     "native.switches is 'SA2_ON', not a list"),
    ({"outcome": "works", "reproduces": False, "requires_unset": ["lower_case"]},
     "native.requires_unset is ['lower_case'], not a list"),
])
def test_a_claim_that_says_more_than_its_evidence_is_refused(native: dict, why: str) -> None:
    got = claims(entry(**native))
    assert any(why in p for p in got), got


def test_an_outcome_about_another_import_is_refused() -> None:
    assert claims(entry("f", "C.dll", outcome="works", reproduces=False)) == [
        "C.dll!f: outcome works is about A.dll!f, A.dll!g, not this import"]


def test_an_outcome_whose_answer_class_is_unknown_is_refused() -> None:
    rec = {"outcomes": [{"id": "works", "imports": ["A.dll!f"], "answer": "maybe"}]}
    got = claims(entry(outcome="works", reproduces=False), rec=rec)
    assert got == ["A.dll!f: outcome works's answer class is 'maybe', not one of success, "
                   "failure, so whether a refusal can reproduce it is unknown"]


def test_a_variable_the_answer_needs_unset_refuses_the_claim_while_it_is_set() -> None:
    held = entry("Ordinal#4", "B.dll", outcome="absent", reproduces=True,
                 requires_unset=["SA2_SET"])
    assert claims(held) == [
        "B.dll!Ordinal#4 claims to reproduce the native outcome, but SA2_SET is set in the "
        "replay configuration, and the entry's answer holds only while it is unset "
        "(native.requires_unset)"]
    assert claims(held, env={k: v for k, v in ENV.items() if k != "SA2_SET"}) == []
    # A non-reproducing entry says nothing the variable could make false.
    assert claims(dict(held, native=dict(held["native"], reproduces=False))) == []


def test_a_success_claim_holds_only_while_the_reach_run_never_refused_the_import() -> None:
    """The gate fold's verification, N1: a switch's name is the entry's own
    word, so the runtime's own refusal count is what holds the claim."""
    held = entry(outcome="works", reproduces=True, switches=["SA2_ON"])
    assert claims(held) == []
    assert claims(held, refused={G.key("A.dll", "f"): {"calls": 2, "refused": 0}}) == []
    got = claims(held, refused={G.key("A.dll", "f"): {"calls": 5, "refused": 3}})
    assert got == ["A.dll!f claims to reproduce outcome works, which answers success "
                   "natively, but the reach run's runtime refused it 3 time(s): a switch "
                   "that is on is not a device that answered"]
    # Another import's refusals are not this one's.
    assert claims(held, refused={**CALLED, G.key("A.dll", "g"): {"calls": 3, "refused": 3}}) \
        == []


def test_a_missing_count_is_not_a_zero() -> None:
    """The review's C2: the table dumps the import-address-table rows only, so
    a dynamic import, or one the run never reached, has no row; neither it nor a
    row the run never called shows a device answering."""
    held = entry(outcome="works", reproduces=True, switches=["SA2_ON"])
    got = claims(held, refused={G.key("A.dll", "g"): {"calls": 1, "refused": 0}})
    assert got == ["A.dll!f claims to reproduce outcome works, which answers success "
                   "natively, but the reach run's import table has no row for it, and a count "
                   "nobody recorded is not a count of zero: harness/crt_probe.c --dump-imports "
                   "dumps the import-address-table rows only, so an import resolved through "
                   "GetProcAddress, or one the table does not hold, has no count"]
    got = claims(held, refused={G.key("A.dll", "f"): {"calls": 0, "refused": 0}})
    assert got == ["A.dll!f claims to reproduce outcome works, which answers success "
                   "natively, but the reach run never called it, so no answer of the "
                   "runtime's shows the refusal replaced"]
    # A failure is what a refusal answers, so it needs no count.
    assert claims(entry("Ordinal#4", "B.dll", outcome="absent", reproduces=True),
                  refused={}) == []


def test_counts_that_cannot_be_recomputed_refuse_every_success_claim() -> None:
    held = entry(outcome="works", reproduces=True, switches=["SA2_ON"])
    assert claims(held, refused="the table is gone") == [
        "A.dll!f claims to reproduce outcome works, which answers success natively, but "
        "the table is gone"]
    assert claims(entry("Ordinal#4", "B.dll", outcome="absent", reproduces=True),
                  refused="the table is gone") == []


def test_a_reach_receipt_without_refusal_counts_refuses_every_success_claim() -> None:
    got = G.claim_problems({"reasons": [entry(outcome="works", reproduces=True,
                                              switches=["SA2_ON"])]}, REC, ENV, None)
    assert len(got) == 1 and "records no refusal counts" in got[0]
    # A failure is what a refusal answers, so it needs no count.
    assert G.claim_problems({"reasons": [entry("Ordinal#4", "B.dll", outcome="absent",
                                               reproduces=True)]}, REC, ENV, None) == []


@pytest.mark.parametrize("kind", ["no-stub", "null-impl"])
def test_a_trap_reproduces_no_outcome(kind: str) -> None:
    """The fold review of the gate push: a null-impl entry that claimed to
    reproduce, with its switch on and a count showing its import called and
    never refused, passed every other rule. A trap stops the run when the
    guest calls the import, so the guest gets no answer at all."""
    held = dict(entry(outcome="works", reproduces=True, switches=["SA2_ON"]), kind=kind)
    assert claims(held) == [
        f"A.dll!f is a {kind} refusal, which traps when the guest calls it and answers "
        "nothing, and claims to reproduce the native outcome; a trap reproduces no outcome"]
    # A failure outcome is no different: a trap does not answer failure either.
    failing = dict(entry("Ordinal#4", "B.dll", outcome="absent", reproduces=True), kind=kind)
    assert claims(failing) == [
        f"B.dll!Ordinal#4 is a {kind} refusal, which traps when the guest calls it and "
        "answers nothing, and claims to reproduce the native outcome; a trap reproduces no "
        "outcome"]
    # The same claims from a policy refusal are the ones the other rules pass,
    assert claims(dict(held, kind="policy"), dict(failing, kind="policy")) == []
    # and a trap that says it does not reproduce says nothing wrong.
    assert claims(dict(held, native=dict(held["native"], reproduces=False))) == []


TABLE_ROWS = [{"dll": "A.DLL", "name": "f", "calls": 4, "refused_calls": 2},
              {"dll": "B.dll", "name": "Ordinal#4", "calls": 1, "refused_calls": 0}]


def reach_with_table(tmp_path: Path, rows: list[dict] | None = TABLE_ROWS, **table) -> Path:
    """A reach receipt naming an import table in tmp_path, its sha256 and the
    counts tools/s4/reach_receipt.py refusals() makes of it, each overridable."""
    t = tmp_path / "reach.imports.table.json"
    if rows is not None:
        t.write_text(json.dumps({"rows": rows}))
    rec = {"path": str(t), "sha256": hashlib.sha256(t.read_bytes()).hexdigest()
           if t.is_file() else "", "refused": RR.refusals(t)}
    rec.update(table)
    p = tmp_path / "e.json"
    p.write_text(json.dumps({"reach": {"import_table": rec}}))
    return p


def test_the_reach_receipt_records_every_row_zeros_included(tmp_path: Path) -> None:
    t = tmp_path / "t.json"
    t.write_text(json.dumps({"rows": TABLE_ROWS + [
        {"dll": "C.dll", "name": "h", "calls": 0, "refused_calls": 0}]}))
    assert RR.refusals(t) == {"A.DLL!f": {"calls": 4, "refused": 2},
                                "B.dll!Ordinal#4": {"calls": 1, "refused": 0},
                                "C.dll!h": {"calls": 0, "refused": 0}}
    t.write_text(json.dumps({"rows": [{"dll": "A.dll", "name": "f", "calls": 1}]}))
    assert RR.refusals(t) is None


def test_the_refusal_counts_are_recomputed_from_the_table_the_receipt_names(tmp_path):
    counts, bad = G.replay_refusals(reach_with_table(tmp_path))
    assert bad == []
    assert counts == {G.key("A.dll", "f"): {"calls": 4, "refused": 2},
                      G.key("B.dll", "Ordinal#4"): {"calls": 1, "refused": 0}}
    # The review's C1: a receipt whose counts were emptied beside a table that
    # refused is reported, and the claims are judged on the table.
    counts, bad = G.replay_refusals(reach_with_table(tmp_path, refused={}))
    assert counts[G.key("A.dll", "f")] == {"calls": 4, "refused": 2}
    assert len(bad) == 1 and "is not what tools/s4/reach_receipt.py refusals() makes of " \
        "the import table it names" in bad[0] and "2 import(s) differ" in bad[0]
    assert G.replay_refusals(reach_with_table(tmp_path, refused=None)) == (None, [])


@pytest.mark.parametrize("table,why", [
    ({"path": "/nonexistent/reach.imports.table.json"}, "is not on disk"),
    ({"path": None}, "is not on disk"),
    ({"sha256": "0" * 64}, "is not the table the counts were written from"),
])
def test_counts_whose_table_is_not_on_disk_as_recorded_are_not_evidence(tmp_path, table, why):
    counts, bad = G.replay_refusals(reach_with_table(tmp_path, **table))
    assert isinstance(counts, str) and why in counts and bad == []


def test_a_table_without_the_refusal_column_backs_no_count(tmp_path: Path) -> None:
    receipt = reach_with_table(tmp_path, rows=[{"dll": "A.dll", "name": "f", "calls": 1}],
                               refused={"A.dll!f": {"calls": 1, "refused": 0}})
    counts, bad = G.replay_refusals(receipt)
    assert isinstance(counts, str) and "has no refused_calls column" in counts
    assert len(bad) == 1 and "no refused_calls column they could have been written" in bad[0]


@pytest.mark.parametrize("record,why", [
    ({"A.DLL!f": {"calls": None, "refused": 2}}, "for A.DLL!f is {'calls': None"),
    ({"A.DLL!f": [4, 2]}, "for A.DLL!f is [4, 2]"),
    ({"A.DLL!f": 2}, "for A.DLL!f is 2, not"),
    ({"A.DLL!f": {"calls": 4, "refused": -1}}, "whole non-negative counts"),
    ({"A.DLL!f": {"calls": 4, "refused": True}}, "whole non-negative counts"),
    ({"f": {"calls": 4, "refused": 2}}, "has the key 'f', which names no DLL!import"),
])
def test_a_malformed_count_or_key_is_reported_not_raised_or_dropped(tmp_path, record, why):
    counts, bad = G.replay_refusals(reach_with_table(tmp_path, refused=record))
    assert any(why in p for p in bad), bad
    # And the claims are still judged on the table's own counts.
    assert counts[G.key("A.dll", "f")] == {"calls": 4, "refused": 2}


def test_a_refusal_record_that_is_not_a_mapping_records_no_counts(tmp_path: Path) -> None:
    counts, bad = G.replay_refusals(reach_with_table(tmp_path, refused=["A.DLL!f"]))
    assert counts is None and len(bad) == 1 and "is list, not a mapping" in bad[0]


def test_the_criterion_reads_exactly_the_files_it_is_handed(tmp_path: Path) -> None:
    """G4's M4 criterion runs claim_problems_in in the clause gate's overlay."""
    (tmp_path / "r.json").write_text(json.dumps({"reasons": [
        entry(outcome="works", reproduces=True)]}))
    (tmp_path / "o.json").write_text(json.dumps(REC))
    (tmp_path / "e.json").write_text(json.dumps({"manifest": {"environment": ENV},
                                                 "reach": {"import_table": {"refused": {}}}}))
    got = G.claim_problems_in(tmp_path / "r.json", tmp_path / "o.json", tmp_path / "e.json")
    # The claim names no switch, and the counts name no table to recompute them
    # from, so it is refused twice and taken on neither.
    assert len(got) == 2 and "and names no switch" in got[0], got
    assert "the import table it names (None) is not on disk" in got[1]
    # A malformed record in the handed receipt is among what the criterion reports.
    (tmp_path / "e.json").write_text(json.dumps({"manifest": {"environment": ENV},
                                                 "reach": {"import_table": {"refused": {
                                                     "f": {"calls": 1, "refused": 0}}}}}))
    got = G.claim_problems_in(tmp_path / "r.json", tmp_path / "o.json", tmp_path / "e.json")
    assert any("which names no DLL!import" in p for p in got), got
    assert G.claim_problems_in(tmp_path / "r.json", tmp_path / "o.json", tmp_path / "x.json")[0] \
        .startswith("the reasons, the native outcomes or the replay configuration cannot be read")


# ---------------------------------------------------------------- the falsification harness

def test_a_case_is_caught_only_by_a_problem_the_record_did_not_have() -> None:
    """The 2026-09-23 review: every case passed while the live gate failed with
    four problems, because a phrase already among them satisfied any case."""
    base = ["A!f is a policy entry with no sa2_import_refused marker in runtime/*.c"]
    assert G.caught(base, base, ("with no sa2_import_refused marker",)) is False
    assert G.caught(base + ["B!g is a policy entry with no sa2_import_refused marker"], base,
                    ("with no sa2_import_refused marker",)) is True
    assert G.caught(["B!g has no why"], base, ("has no falsifier",)) is False


# ---------------------------------------------------------------- the gate on the real record

REAL = all((ROOT / NO.CAPTURES / k / NO.LOG_NAME).is_file() for k in NO.LOGS) \
    and NO.exe_path().is_file() and NO.RECEIPT.is_file() and G.REACH.is_file()
needs_private = pytest.mark.skipif(not REAL, reason="the traced native boots, the executable "
                                                    "or the reach receipt are not on this machine")


def planned(tmp_path: Path) -> Path:
    """A marker for every policy entry the record holds, as the integration
    places them in runtime/*.c."""
    record = json.loads(RA.REASONS.read_text())["reasons"]
    return G._marker_source(tmp_path / "planned.c", [(r["dll"], r["name"]) for r in record
                                                     if r["kind"] == "policy"])


@needs_private
def test_the_record_passes_before_any_marker(tmp_path: Path) -> None:
    assert G.problems(sources=[], src=integrated(tmp_path)) == []


@needs_private
def test_the_record_passes_with_every_policy_refusal_marked(tmp_path: Path) -> None:
    assert G.problems(sources=[planned(tmp_path)], src=integrated(tmp_path)) == []


@needs_private
def test_every_falsification_fails_the_gate_for_its_own_reason(tmp_path: Path) -> None:
    assert G.falsifications(src=integrated(tmp_path), sources=[planned(tmp_path)]) == []
    assert G.RAN >= 25
    assert G.INAPPLICABLE == []


@needs_private
def test_a_case_the_data_cannot_build_is_inapplicable_not_failed(tmp_path: Path) -> None:
    """The day every import slot has a stub, no policy entry can name one that
    has none; the gate says so in its summary instead of failing for ever."""
    assert G.falsifications(src=stub_everything(tmp_path), sources=[planned(tmp_path)]) == []
    assert [x.split(" ")[0] for x in G.INAPPLICABLE] == ["nostub"]


@needs_private
def test_the_falsifications_still_build_once_every_device_is_on(tmp_path, monkeypatch) -> None:
    """The 2026-09-23 review's case: turn every switch the entries name on in the
    replay configuration and let those entries claim to reproduce, as the
    device work is meant to end -- and, since the replay tools' switch-on
    (2026-09-24), as it has. The switch case once needed an entry whose switch
    was off, and the gate then failed for ever; it is synthetic now."""
    record = json.loads(RA.REASONS.read_text())
    switched = set()
    for r in record["reasons"]:
        # A null-impl row has no implementation for a device to answer
        # through, so only the policy refusals a device replaces claim here.
        if r["native"].get("switches") and r["kind"] == "policy":
            r["native"]["reproduces"] = True
            switched.update(r["native"]["switches"])
    # The three recording devices, and D3DXGetShaderConstantTable's table with
    # the d3d9 device it answers through.
    assert switched == {"SA2_DINPUT_DEVICE", "SA2_XAUDIO2_DEVICE", "SA2_DDRAW_DEVICE",
                        "SA2_D3D9_DEVICE", "SA2_D3DX_CONSTANT_TABLE"}
    reasons_path = tmp_path / "reasons.json"
    reasons_path.write_text(json.dumps(record))
    reach = json.loads(G.REACH.read_text())
    reach["manifest"]["environment"].update({s: "1" for s in switched})
    # With the devices on, the reach run calls each import they replace and
    # refuses none, and its table says so. All three are import-address-table
    # rows, which the table dumps; a dynamic import could not claim here.
    table = tmp_path / "reach.imports.table.json"
    table.write_text(json.dumps({"rows": [
        {"dll": r["dll"], "name": r["name"], "calls": 1, "refused_calls": 0}
        for r in record["reasons"] if r["native"].get("reproduces") is True
        and r["native"].get("switches")]}))
    reach.setdefault("reach", {})["import_table"] = {
        "path": str(table), "sha256": hashlib.sha256(table.read_bytes()).hexdigest(),
        "refused": RR.refusals(table)}
    reach_path = tmp_path / "reach.json"
    reach_path.write_text(json.dumps(reach))
    monkeypatch.setattr(G, "REASONS", reasons_path)
    monkeypatch.setattr(RA, "REASONS", reasons_path)
    monkeypatch.setattr(G, "REACH", reach_path)
    runtime, marks = integrated(tmp_path), planned(tmp_path)
    assert G.problems(sources=[marks], src=runtime) == []
    assert G.falsifications(src=runtime, sources=[marks]) == []
    assert G.INAPPLICABLE == []


@needs_private
def test_the_trap_case_builds_once_no_trap_entry_is_left(tmp_path, monkeypatch) -> None:
    """The DirectDraw push removes the two null-impl entries and no no-stub
    entry exists, so the trap-claim case is synthetic: with no trap entry in
    the record it still builds, and still fails the gate for its reason."""
    record = json.loads(RA.REASONS.read_text())
    record["reasons"] = [r for r in record["reasons"] if r["kind"] not in G.TRAPS]
    reasons_path = tmp_path / "reasons.json"
    reasons_path.write_text(json.dumps(record))
    monkeypatch.setattr(G, "REASONS", reasons_path)
    monkeypatch.setattr(RA, "REASONS", reasons_path)
    runtime, marks = integrated(tmp_path), planned(tmp_path)
    assert G.problems(sources=[marks], src=runtime) == []
    assert G.falsifications(src=runtime, sources=[marks]) == []
    assert G.INAPPLICABLE == []
    # and the case is there and is what holds the rule: with it blinded, it alone survives.
    monkeypatch.setattr(G, "TRAPS", ())
    got = G.falsifications(src=runtime, sources=[marks])
    assert len(got) == 1 and got[0].startswith(
        "falsification survived: an entry declared a trap (null-impl) that claims to reproduce")


@needs_private
def test_a_runtime_the_audit_refuses_fails_the_falsifications_by_name(tmp_path: Path) -> None:
    runtime, row = with_null_row(tmp_path)
    got = G.falsifications(src=runtime, sources=[planned(tmp_path)])
    assert len(got) == 1 and got[0].startswith("the audit refused to run, so no falsification "
                                               "can be built")
    assert f"dynamic {row['dll']}!{row['name']} refuses (null-impl)" in got[0]


@needs_private
def test_the_record_claims_pass_the_rules_m4_runs() -> None:
    assert G.claim_problems_in(RA.REASONS, NO.RECEIPT, G.REACH) == []


# ---------------------------------------------------------------- M4's count

ROWS = [{"dll": "DINPUT8.dll", "name": "DirectInput8Create", "implemented": True},
        {"dll": "XINPUT1_3.dll", "name": "Ordinal#4", "implemented": True},
        {"dll": "ole32.dll", "name": "CoCreateInstance", "implemented": True},
        {"dll": "OLEAUT32.dll", "name": "Ordinal#2", "implemented": False},
        {"dll": "d3dx9_42.dll", "name": "D3DXGetShaderConstantTable", "implemented": True},
        {"dll": "KERNEL32.dll", "name": "GetVersion", "implemented": True}]


def test_m4_counts_a_refusal_only_when_it_reproduces_native() -> None:
    doc = {"reasons": [
        {"dll": "DINPUT8", "name": "DirectInput8Create",
         "native": {"outcome": "dinput8-create", "reproduces": False}},
        {"dll": "XINPUT1_3", "name": "ordinal#4",
         "native": {"outcome": "xinput-not-connected", "reproduces": True}},
        {"dll": "ole32.dll", "name": "CoCreateInstance"},
        {"dll": "d3dx9_42.dll", "name": "D3DXGetShaderConstantTable",
         "native": {"outcome": None, "reproduces": False}},
        {"dll": "OLEAUT32.dll", "name": "SysAllocString",
         "native": {"outcome": "wbem-scan", "reproduces": False}}]}
    implemented, left_out = IS.counted(ROWS, doc)
    assert implemented == ["KERNEL32.dll!GetVersion", "XINPUT1_3.dll!Ordinal#4"]
    assert left_out == [
        {"import": "DINPUT8.dll!DirectInput8Create", "outcome": "dinput8-create",
         "reproduces": False},
        {"import": "d3dx9_42.dll!D3DXGetShaderConstantTable", "outcome": None,
         "reproduces": False},
        {"import": "ole32.dll!CoCreateInstance", "outcome": None, "reproduces": None}]


def test_m4_on_the_real_record_leaves_out_exactly_the_non_reproducing_refusals() -> None:
    """The real record, and the real record with each entry whose import the
    rows count made non-reproducing in turn. Once every entry reproduced (P6),
    the real record alone left nothing out, and the comparison held with both
    sides empty whatever counted() did (P6's review, N6)."""
    doc = json.loads(RA.REASONS.read_text())
    counted_rows = {RA.key(r["dll"], r["name"]) for r in ROWS if r["implemented"]}
    variants = [doc]
    for i, r in enumerate(doc["reasons"]):
        if RA.key(r["dll"], r["name"]) in counted_rows:
            flipped = json.loads(json.dumps(doc))
            flipped["reasons"][i]["native"]["reproduces"] = False
            variants.append(flipped)
    # the record, then DirectInput8Create, CoCreateInstance, D3DX and Ordinal#4 flipped
    assert len(variants) == 5
    for d in variants:
        nonrep = {RA.key(r["dll"], r["name"]) for r in d["reasons"]
                  if r["native"]["reproduces"] is not True}
        implemented, left_out = IS.counted(ROWS, d)
        assert {RA.key(*x["import"].split("!", 1)) for x in left_out} == nonrep & counted_rows
        assert not {RA.key(*i.split("!", 1)) for i in implemented} & nonrep
        assert "KERNEL32.dll!GetVersion" in implemented
    assert "XINPUT1_3.dll!Ordinal#4" in IS.counted(ROWS, doc)[0]
    assert "XINPUT1_3.dll!Ordinal#4" not in IS.counted(ROWS, variants[-1])[0]


def test_the_second_clause_names_a_counted_import_whose_refusal_does_not_reproduce(tmp_path):
    status = tmp_path / "import-status.json"
    status.write_text(json.dumps({"implemented": ["KERNEL32.dll!GetVersion",
                                                  "XINPUT1_3.dll!Ordinal#4"]}))
    doc = {"reasons": [{"dll": "XINPUT1_3", "name": "ordinal#4",
                        "native": {"outcome": "xinput-not-connected", "reproduces": False}}]}
    r = tmp_path / "reasons.json"
    r.write_text(json.dumps(doc))
    assert IS.counted_nonreproducing(status, r) == ["XINPUT1_3.dll!Ordinal#4"]
    doc["reasons"][0]["native"]["reproduces"] = True
    r.write_text(json.dumps(doc))
    assert IS.counted_nonreproducing(status, r) == []
