"""tools/s4/ctype_check.py: what its checks stand on, and that they fail.

The tool's `run` mode drives three lifted boots; its `tables` mode builds
runtime/sa2_codepage.c against the oracle's Wine data and the native locale
trace. What is tested here is what reads them: the native trace's calls and the
code page each rides on, the lifted recording's lines, the comparison of the two,
Wine's conversion rule, and the `tables` checks against a runtime whose data is
one row off. The last group needs the oracle's Proton, the native trace and the
pinned zig, which live under private/.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import ctype_check as CC  # noqa: E402

REAL = (all((CC.NLS_DIR / n).is_file() for n in CC.NLS) and CC.NATIVE_TRACE.is_file()
        and CC.ZIG.is_file())
needs_private = pytest.mark.skipif(not REAL, reason="the oracle's Proton, the native locale "
                                                    "trace or the pinned zig is not on this "
                                                    "machine")


def trace(tmp_path: Path, lines: list[str], pin: bool = True) -> Path:
    p = tmp_path / "trace.txt"
    p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    return p


def calls_of(tmp_path, monkeypatch, lines: list[str]) -> list[dict]:
    p = trace(tmp_path, lines)
    monkeypatch.setattr(CC, "NATIVE_TRACE_SHA256", CC.sha256(p))
    return CC.native_calls(p)


# ------------------------------------------------------------------ the native trace

def test_the_trace_s_strings_are_read_unit_for_unit():
    assert CC.units(r"A\u00aaB\x83\u005c") == [0x41, 0xAA, 0x42, 0x83, 0x5C]


def test_an_unpinned_trace_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="not the pinned native locale trace"):
        CC.native_calls(trace(tmp_path, ["1 f3 t1 007b69d0 GetStringTypeW 1 1 -> 1 L\"a\" [0302]"]))


def test_each_call_is_keyed_by_site_flags_length_and_input_with_its_code_page(tmp_path,
                                                                              monkeypatch):
    calls = calls_of(tmp_path, monkeypatch, [
        '1 f3 t1 007b69be MultiByteToWideChar cp=936 00000001 2 2 -> 2 "a\\xff"',
        '2 f3 t1 007b69d0 GetStringTypeW 1 2 -> 1 L"a\\uf8f5" [0302 0200]',
        '3 f3 t1 007b69be MultiByteToWideChar cp=1252 00000001 2 2 -> 2 "a\\xaa"',
        '4 f3 t1 007b69d0 GetStringTypeW 1 2 -> 1 L"a\\u00aa" [0302 0312]',
        '5 f3 t2 007b69be MultiByteToWideChar cp=936 00000001 1 1 -> 1 "a"',
        '6 f3 t2 007b69d0 GetStringTypeW 1 1 -> 1 L"a" [0302]',
        '7 f3 t1 007b69be MultiByteToWideChar cp=1252 00000001 1 1 -> 1 "a"',
        '8 f3 t1 007b69d0 GetStringTypeW 1 1 -> 1 L"a" [0302]',
        '9 f3 t1 007ab644 LCMapStringW 00000409 00000200 1 1 -> 1 L"\\u0192" L"\\u0191"',
        '10 f3 t1 007ab5a2 LCMapStringW 00000409 00000200 1 0 -> 1 L"\\u0192"',
    ])
    got = CC.native_answers(calls)
    by_seq = {v["seq"]: (k, v) for k, v in got.items()}
    assert sorted(by_seq) == [2, 4, 6, 9]      # the size query at seq 10 answers nothing
    assert by_seq[2][1]["cps"] == {936}
    assert by_seq[4][1]["cps"] == {1252} and by_seq[4][1]["out"] == (0x0302, 0x0312)
    assert by_seq[6][1]["cps"] == {936, 1252}  # an ASCII input rides both code pages
    assert by_seq[9][0] == ("LCMapStringW", 0x007AB644, 0x200, 1, (0x192,))
    assert by_seq[9][1]["out"] == (0x191,)


def test_one_input_answered_twice_differently_is_refused(tmp_path, monkeypatch):
    calls = calls_of(tmp_path, monkeypatch, [
        '1 f3 t1 007b69d0 GetStringTypeW 1 1 -> 1 L"\\u00aa" [0312]',
        '2 f3 t2 007b69d0 GetStringTypeW 1 1 -> 1 L"\\u00aa" [0302]'])
    with pytest.raises(SystemExit, match="differently"):
        CC.native_answers(calls)


# ------------------------------------------------------------------ the lifted recording

LIFTED = [
    "129 t0 007b69d0 GetStringTypeW 00000001 03eef738 00000002 03eef9d4 -> 00000001 "
    "in=000000aa out=02200312",
    "138 t0 007ab644 LCMapStringW 00000409 00000200 03eef708 00000001 03eef4f8 00000001 "
    "-> 00000001 in=0192 out=0191",
    "139 t0 007ab667 WideCharToMultiByte 000004e4 00000000 03eef4f8 00000002 03eefcd4 "
    "00000002 00000000 00000000 -> 00000002 in=01910100 out=8341",
]


def test_a_recorded_line_is_read_in_the_import_s_units(tmp_path):
    p = tmp_path / "answers.txt"
    p.write_text("\n".join(LIFTED) + "\n")
    g, lc, wc = CC.lifted_calls(p)
    assert (g["caller"], g["in"], g["out"]) == (0x007B69D0, [0x0000, 0x00AA], [0x0220, 0x0312])
    assert CC.lifted_key(g) == ("GetStringTypeW", 0x007B69D0, 1, 2, (0x0000, 0x00AA))
    assert CC.lifted_key(lc) == ("LCMapStringW", 0x007AB644, 0x200, 1, (0x0192,))
    assert (wc["in"], wc["out"]) == ([0x0191, 0x0100], [0x83, 0x41])
    assert CC.lifted_key(wc) is None


def test_a_line_the_reader_cannot_read_is_refused(tmp_path):
    p = tmp_path / "answers.txt"
    p.write_text("129 t0 007b69d0 GetStringTypeW 00000001 -> 00000001 in=0 out=\n")
    with pytest.raises(SystemExit, match="whole units"):
        CC.lifted_calls(p)


def native(key, cps, ret, out, seq=1):
    return {key: {"cps": set(cps), "ret": ret, "out": tuple(out), "seq": seq}}


def lifted(name, caller, args, ret, inp, out):
    return {"seq": 1, "thread": "t0", "caller": caller, "name": name, "args": args,
            "ret": ret, "in": list(inp), "out": list(out)}


def test_a_lifted_answer_is_held_to_native_s_on_either_code_page_s_path():
    """Since the lifted run enters setlocale(LC_ALL, "Chinese-simplified")
    (2026-09-26), a code page 936 call is compared as a 1252 one is, and one the
    lifted run never makes is uncovered, not listed."""
    key = ("LCMapStringW", 0x007AB644, 0x200, 1, (0x192,))
    other = ("LCMapStringW", 0x007AB644, 0x100, 1, (0xF8F5,))
    nat = {**native(key, [1252], 1, [0x191], seq=5), **native(other, [936], 1, [0xF8F5], seq=6)}
    args = [0x409, 0x200, 0, 1, 0x1000, 1]
    args936 = [0x804, 0x100, 0, 1, 0x1000, 1]
    both = [lifted("LCMapStringW", 0x007AB644, args, 1, [0x192], [0x191]),
            lifted("LCMapStringW", 0x007AB644, args936, 1, [0xF8F5], [0xF8F5])]
    same = CC.compare_answers(nat, both)
    assert [r["differing_calls"] for r in same["compared"]] == [0, 0]
    assert same["uncovered"] == [] and same["other_code_page"] == []
    only1252 = CC.compare_answers(nat, both[:1])
    assert len(only1252["uncovered"]) == 1 and "0x100" in only1252["uncovered"][0]
    before = CC.compare_answers(nat, [lifted("LCMapStringW", 0x007AB644, args, 1, [0x192],
                                             [0x192])])
    assert before["compared"][0]["differences"] == [
        {"unit": "U+0192", "native": "0x0191", "lifted": "0x0192"}]
    missing = CC.compare_answers(nat, [])
    assert missing["compared"] == [] and len(missing["uncovered"]) == 2


def test_a_native_call_on_a_code_page_neither_compared_nor_listed_is_refused():
    """Only 1252's calls are compared and only 936's listed; a call whose code
    page is anything else, or unknown, would otherwise vanish from both."""
    key = ("GetStringTypeW", 0x007B69D0, 1, 1, (0x41,))
    for cps in ([None], [1252, 850]):
        with pytest.raises(SystemExit, match="only 1252's calls are compared"):
            CC.compare_answers(native(key, cps, 1, [0x0381]), [])


# ------------------------------------------------------------------ run's checks

def recorded(differing=0, words=(), through=1200, conversions=()):
    """One run as one_run records it: one native call compared, three native
    word logs, and what WideCharToMultiByte got wrong."""
    return {"compared": [{"call": "c", "lifted_calls": 1, "differing_calls": differing,
                          "differences": []}],
            "uncovered": [], "other_code_page": [], "conversions": list(conversions),
            "mb_tables": [{"native": f"n{i}", "compared_through": through,
                           "words": list(words)} for i in range(3)]}


MOVED_WORD = {"va": "0x009176d8", "first_tick": 1, "lifted": "0x3f000000",
              "native": "0x83000000"}
OFF_BY_BYTE = "line 9 from 0x007ab667: U+0191 became 0x3f, Wine writes 0x83"
UNJUDGED = "line 9: code page 437, default 0x0: not a call this check can judge"


def test_run_s_checks_pass_on_the_runs_the_breaks_should_give():
    runs = {"control": recorded(), "rules": recorded(differing=1),
            "nobestfit": recorded(words=[MOVED_WORD], conversions=[OFF_BY_BYTE])}
    assert all(CC.run_checks(runs).values())


def test_a_break_whose_logs_were_compared_through_no_tick_moved_nothing():
    """The nobestfit run stops before tick 1: its start-up conversions still
    write 0x3F, but _mbcasemap was never compared, so nothing was shown."""
    runs = {"control": recorded(), "rules": recorded(differing=1),
            "nobestfit": recorded(through=0, conversions=[OFF_BY_BYTE])}
    assert CC.run_checks(runs)["nobestfit_moves_case_tables"] is False


def test_a_conversion_the_check_cannot_judge_is_not_a_dropped_best_fit():
    runs = {"control": recorded(), "rules": recorded(differing=1),
            "nobestfit": recorded(words=[MOVED_WORD], conversions=[UNJUDGED])}
    assert CC.run_checks(runs)["nobestfit_moves_case_tables"] is False


def test_the_control_compared_through_no_tick_does_not_follow_native():
    runs = {"control": recorded(through=0), "rules": recorded(differing=1),
            "nobestfit": recorded(words=[MOVED_WORD], conversions=[OFF_BY_BYTE])}
    assert CC.run_checks(runs)["case_tables_follow_native"] is False


def test_a_break_not_compared_with_every_native_log_moved_nothing():
    """The word moved against two native logs, and the third was compared through
    no tick: tables_moved asks every log to have been compared, not one."""
    nobestfit = recorded(words=[MOVED_WORD], conversions=[OFF_BY_BYTE])
    nobestfit["mb_tables"][2] = {"native": "n2", "compared_through": 0, "words": []}
    runs = {"control": recorded(), "rules": recorded(differing=1), "nobestfit": nobestfit}
    assert CC.run_checks(runs)["nobestfit_moves_case_tables"] is False


def test_each_other_check_fails_on_its_own_shape():
    good = {"control": recorded(), "rules": recorded(differing=1),
            "nobestfit": recorded(words=[MOVED_WORD], conversions=[OFF_BY_BYTE])}
    uncovered = dict(recorded(), uncovered=["a native call no lifted call made"])
    for name, edit in [
            ("covers_native", {"control": dict(recorded(), compared=[])}),
            ("covers_native", {"control": uncovered}),
            ("covers_native", {"rules": dict(uncovered, compared=recorded(differing=1)["compared"])}),
            ("answers_follow_native", {"control": recorded(differing=1)}),
            ("conversions_follow_wine", {"control": recorded(conversions=[OFF_BY_BYTE])}),
            ("rules_moves_answers", {"rules": recorded()}),
            ("rules_moves_answers", {"rules": dict(recorded(), compared=[])})]:
        assert CC.run_checks({**good, **edit})[name] is False, (name, edit)


def test_the_rules_run_enumerates_no_locale_and_only_its_936_calls_may_go_missing():
    """Unit IN (2026-09-26): since the locale take the C runtime enters
    Chinese-simplified and asks GetStringTypeW about code page 936's characters,
    which the rules trap on, so the rules run enumerates no locale (BREAK_ENV) and
    the 936 path's calls, which its C runtime never makes, are the only native
    calls it may leave without a lifted one. The control and the nobestfit run
    keep the enumeration, and cover every call."""
    assert CC.BREAK_ENV == {"rules": {"SA2_CTYPE_BREAK": "rules", "SA2_LOCALE_BREAK": "noenum"},
                            "nobestfit": {"SA2_CTYPE_BREAK": "nobestfit"}}
    key1252 = ("LCMapStringW", 0x007AB644, 0x200, 1, (0x192,))
    key936 = ("LCMapStringW", 0x007AB644, 0x100, 1, (0xF8F5,))
    keyboth = ("GetStringTypeW", 0x007B69D0, 1, 1, (0x41,))
    nat = {**native(key1252, [1252], 1, [0x191], seq=5),
           **native(key936, [936], 1, [0xF8F5], seq=6),
           **native(keyboth, [936, 1252], 1, [0x0381], seq=7)}
    only1252 = [lifted("LCMapStringW", 0x007AB644, [0x409, 0x200, 0, 1, 0x1000, 1], 1, [0x192],
                       [0x192])]
    cmp = CC.compare_answers(nat, only1252)
    assert len(cmp["uncovered"]) == 2 and len(cmp["uncovered_936_alone"]) == 1
    assert "0x100" in cmp["uncovered_936_alone"][0]
    good = {"control": recorded(), "rules": recorded(differing=1),
            "nobestfit": recorded(words=[MOVED_WORD], conversions=[OFF_BY_BYTE])}
    alone = ["LCMapStringW from 0x007ab644, flags 0x100, 1 units (trace line 6)"]
    noenum = dict(recorded(differing=1), env=CC.BREAK_ENV["rules"], uncovered=list(alone),
                  uncovered_936_alone=list(alone))
    assert CC.run_checks({**good, "rules": noenum})["covers_native"] is True
    # the same missing call in a run that enumerated locales, or a 1252 call missing
    # from the noenum run, or a control that enumerated none, is not covered
    enum = dict(noenum, env={"SA2_CTYPE_BREAK": "rules"})
    assert CC.run_checks({**good, "rules": enum})["covers_native"] is False
    also1252 = dict(noenum, uncovered=alone + ["a 1252 call no lifted call made"])
    assert CC.run_checks({**good, "rules": also1252})["covers_native"] is False
    control = dict(recorded(), env={"SA2_LOCALE_BREAK": "noenum"}, uncovered=list(alone),
                   uncovered_936_alone=list(alone))
    assert CC.run_checks({**good, "control": control})["covers_native"] is False


def cp1252_toy():
    """A code page with 0x41 'A' and 0x83 U+0192, the rest unmapped to '?', and a
    best fit of U+0100 and U+0191."""
    mb = [0x3F] * 256
    mb[0x41], mb[0x83], mb[0x3F] = 0x41, 0x192, 0x3F
    wc = bytearray([0x3F] * 0x10000)
    wc[0x41], wc[0x192], wc[0x100], wc[0x191] = 0x41, 0x83, 0x41, 0x83
    return mb, bytes(wc), 0x3F


def test_wine_s_rule_for_a_unit_the_code_page_lacks():
    mb, wc, d = cp1252_toy()
    assert CC.best_fit_kind(0x192, mb, wc, d) == (1, 0x83)       # the code page has it
    assert CC.best_fit_kind(0x191, mb, wc, d) == (2, 0x83)       # best fit
    assert CC.best_fit_kind(0x4E00, mb, wc, d) == (0, 0x3F)      # neither
    assert CC.best_fit_kind(0x3F, mb, wc, d) == (1, 0x3F)        # '?' is its own


def test_a_conversion_that_drops_the_best_fit_is_named():
    mb, wc, d = cp1252_toy()
    args = [1252, 0, 0x1000, 2, 0x2000, 2, 0, 0]
    ok = lifted("WideCharToMultiByte", 0x007AB667, args, 2, [0x191, 0x100], [0x83, 0x41])
    assert CC.conversion_problems([ok], mb, wc, d) == []
    bad = dict(ok, out=[0x3F, 0x41])
    assert CC.conversion_problems([bad], mb, wc, d) == [
        "line 1 from 0x007ab667: U+0191 became 0x3f, Wine writes 0x83"]
    # WC_NO_BEST_FIT_CHARS asks for no best fit, and then '?' is Wine's answer.
    nobf = dict(bad, args=[1252, CC.WC_NO_BEST_FIT_CHARS] + args[2:], out=[0x3F, 0x3F])
    assert CC.conversion_problems([nobf], mb, wc, d) == []


@needs_private
def test_a_code_page_936_conversion_is_judged_against_c_936():
    """U+4E00 is 0xD2 0xBB in code page 936 (the native trace's WideCharToMultiByte
    of 0x0804's day names); without the table the call is not judged."""
    import nls_data as N
    mb, wc, d = cp1252_toy()
    args = [936, 0, 0x1000, 2, 0x2000, 128, 0, 0]
    ok = lifted("WideCharToMultiByte", 0x007BC658, args, 3, [0x4E00, 0], [0xD2, 0xBB, 0])
    assert CC.conversion_problems([ok], mb, wc, d, cp936=N.cp936()) == []
    bad = dict(ok, out=[0x3F, 0])
    assert CC.conversion_problems([bad], mb, wc, d, cp936=N.cp936()) == [
        "line 1 from 0x007bc658: code page 936 wrote 3f00 at byte 0, Wine writes d2bb"]
    assert CC.conversion_problems([bad], mb, wc, d) == [
        "line 1: code page 936, default 0x0: not a call this check can judge"]


def test_the_case_tables_are_the_image_s():
    """_mbctype at 0x00917550 (257 bytes) and _mbcasemap at 0x00917658 (256), the
    copies _setmbcp makes at 0x007AD5B8 and 0x007AD5D1, as whole words."""
    lo, hi = CC.MB_TABLES
    assert (lo, hi) == (0x00917550, 0x00917758) and lo % 4 == 0 and hi % 4 == 0
    assert lo + 257 <= 0x00917658 and 0x00917658 + 256 == hi


def test_the_domain_is_the_code_page_s_characters_and_two_more():
    d = CC.domain()
    assert d[:256] == list(range(256)) and len(d) == 256 + 27 + 2
    assert {0x0191, 0xF8F5} <= set(d) and d == sorted(d)


# ------------------------------------------------------------------ tables, and its failures

@needs_private
def test_the_tree_s_tables_pass():
    got = CC.tables()
    assert got["checks"] == {"header_is_generated": True, "data_follows_wine": True,
                             "best_fit_follows_wine": True, "data_follows_native": True}
    assert got["domain"] == 285 and got["native_characters"] == {"ctype1": 257, "case": 513}


def runtime_copy(tmp_path: Path, edit) -> None:
    """runtime/sa2_codepage.{c,h} and sa2_chardata.h in tmp_path, the header edited."""
    for name in ("sa2_codepage.c", "sa2_codepage.h", "sa2_chardata.h"):
        shutil.copy(ROOT / "runtime" / name, tmp_path / name)
    h = tmp_path / "sa2_chardata.h"
    h.write_text(edit(h.read_text()))


@needs_private
def test_a_header_one_character_off_fails_three_checks(tmp_path, monkeypatch):
    # U+00AA's CT_CTYPE1 back to the rules' 0x0302.
    runtime_copy(tmp_path, lambda t: t.replace("{0x00AAu, 0x0312u,", "{0x00AAu, 0x0302u,"))
    monkeypatch.setattr(CC, "HEADER", tmp_path / "sa2_chardata.h")
    monkeypatch.setattr(CC, "CODEPAGE_C", tmp_path / "sa2_codepage.c")
    got = CC.tables()
    assert got["checks"] == {"header_is_generated": False, "data_follows_wine": False,
                             "best_fit_follows_wine": True, "data_follows_native": False}
    assert got["differences"]["native"] == ["U+00AA: CT_CTYPE1 0x0302, native 0x0312"]


@needs_private
def test_a_lost_best_fit_fails_its_check(tmp_path, monkeypatch):
    runtime_copy(tmp_path, lambda t: t.replace("{0x0191u, 0x83u},", "{0x0191u, 0x3Fu},"))
    monkeypatch.setattr(CC, "HEADER", tmp_path / "sa2_chardata.h")
    monkeypatch.setattr(CC, "CODEPAGE_C", tmp_path / "sa2_codepage.c")
    got = CC.tables()
    assert not got["checks"]["best_fit_follows_wine"] and not got["checks"]["header_is_generated"]
    assert got["differences"]["best_fit"][0].startswith("U+0191:")


@needs_private
def test_a_code_page_936_conversion_s_return_and_length_are_judged():
    import nls_data as N
    mb, wc, d = cp1252_toy()
    args = [936, 0, 0x1000, 2, 0x2000, 128, 0, 0]
    ok = lifted("WideCharToMultiByte", 0x007BC658, args, 3, [0x4E00, 0], [0xD2, 0xBB, 0])
    for bad in (dict(ok, ret=0, out=[]), dict(ok, ret=1, out=[0xD2]), dict(ok, ret=7)):
        assert CC.conversion_problems([bad], mb, wc, d, cp936=N.cp936()), bad
    query = lifted("WideCharToMultiByte", 0x007BC658, [936, 0, 0x1000, 2, 0, 0, 0, 0], 9,
                   [0x4E00, 0], [])
    assert CC.conversion_problems([query], mb, wc, d, cp936=N.cp936())


def test_run_hands_each_run_its_own_environment_and_prints_the_excuse(tmp_path, monkeypatch,
                                                                     capsys):
    """The instrument review of unit IN, findings 2 and 5: the control runs with no
    break, each break run with BREAK_ENV's own environment (a run_mode that dropped it
    passed every test, and would stop the rules run in its own trap), and `run` prints
    how many native calls each run left uncovered and, in a run that enumerated no
    locale, how many of them were code page 936's alone, excused."""
    import json as J
    import tick_replay as TR
    log = tmp_path / "w.bin.zst"
    log.write_bytes(b"a word log")
    reg = tmp_path / "replays.json"
    reg.write_text(J.dumps({"replays": [{"name": "boot", "native": {
        "word_log": {"file": str(log), "sha256": CC.sha256(log)}}}]}))
    monkeypatch.setattr(TR, "REGISTRY", reg)
    monkeypatch.setattr(TR, "native_device_profile", lambda entry: {"profile": "p"})
    monkeypatch.setattr(CC, "ROOT", Path("/"))              # the log is outside the tree
    monkeypatch.setattr(CC, "native_calls", lambda: [])
    monkeypatch.setattr(CC, "native_answers", lambda calls: {})
    monkeypatch.setattr(CC, "cp1252_tables", lambda: (None, None, None))
    envs = {}
    alone = ["a code page 936 call (trace line 6)"]

    def one_run(tr, name, env, profile, native, natives, cp1252):
        envs[name] = dict(env)
        noenum = env.get("SA2_LOCALE_BREAK") == "noenum"
        return dict(recorded(), env=env, uncovered=list(alone) if noenum else [],
                    uncovered_936_alone=list(alone) if noenum else [])
    monkeypatch.setattr(CC, "one_run", one_run)
    got = CC.run_mode()
    assert envs == {"ctype-control": {}, **{f"ctype-{b}": CC.BREAK_ENV[b] for b in CC.BREAKS}}
    assert got["runs"]["rules"]["env"]["SA2_LOCALE_BREAK"] == "noenum"
    monkeypatch.setattr(CC, "run_mode", lambda: got)
    CC.main(["run"])
    out = capsys.readouterr().out
    assert "CTYPE rules: 1 native calls compared, 0 only listed, 1 uncovered (1 on code page " \
           "936 alone, excused: no locale enumerated)," in out, out
    assert "CTYPE control: 1 native calls compared, 0 only listed, 0 uncovered, differing" in out
