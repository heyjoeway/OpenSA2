"""tools/s4/locale_check.py and tools/s4/nls_data.py: what their checks stand on, and that they fail.

`tables` holds runtime/sa2_locale.c and the generated runtime/sa2_localedata.h
to a model of kernelbase over the oracle's NLS files, and the model to the
native traces; `run` drives five lifted boots and holds their answers to the
traces. What is tested here is what reads them: kernelbase's rules as the model
states them, the traces' and the recorders' lines, the keyed comparison, the
checks over recorded runs, and `tables` against a runtime one answer off. The
tests marked needs_private need the oracle's Proton, the native traces and the
pinned zig, which live under private/.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import device_profile as DP  # noqa: E402
import locale_check as LC  # noqa: E402
import nls_data as N  # noqa: E402

REAL = (all((N.NLS_DIR / n).is_file() for n in N.NLS) and LC.ZIG.is_file()
        and all(p.is_file() for p, _pin in LC.TRACES.values())
        and LC.USER_LOCALE_PROFILE[0].is_file())
needs_private = pytest.mark.skipif(not REAL, reason="the oracle's Proton, the native traces or "
                                                    "the pinned zig is not on this machine")
TIME = LC.CATEGORIES["LC_TIME"]


# ------------------------------------------------------------------ the words

def test_the_words_are_the_c_runtime_s():
    """__lc_handle[LC_COLLATE..LC_TIME] after _setlocale's copy at 0x007AA6FA,
    then __lc_codepage and __lc_collate_cp, ___getlocaleinfo's LC_INT_TYPE
    buffer and __lc_time_c's reference count."""
    assert [f"{LC.WORDS[k]:#010x}" for k in LC.WORDS] == [
        "0x01751b94", "0x01751b98", "0x01751b9c", "0x01751ba0", "0x01751ba4",
        "0x01751ba8", "0x01751bac", "0x01751ed4", "0x01751ed8", "0x00917fc4"]


def test_a_value_is_carried_from_its_change_to_the_next():
    assert LC.values({0: 5, 3: 7}, 4) == [5, 5, 5, 7, 7]
    assert LC.values({2: 9}, 3) == [0, 0, 9, 9]       # absent from the keyframe: zero


def test_two_series_differ_only_on_ticks_both_hold_from_1():
    blank = {va: {} for va in LC.WORDS.values()}
    native = ({**blank, TIME: {0: 0x409}}, 3)
    assert LC.words_differ(({**blank, TIME: {0: 0x409}}, 5), native) == {}
    assert LC.words_differ(({**blank, TIME: {0: 0}}, 5), native) == {"LC_TIME": [1, 2, 3]}
    # A keyframe that differs is not a tick; a change at 2 is.
    assert LC.words_differ(({**blank, TIME: {0: 0, 1: 0x409, 2: 0}}, 5), native) == \
        {"LC_TIME": [2, 3]}


# ------------------------------------------------------------------ kernelbase's rules

def codepage_toy() -> N.CodePage:
    """A double-byte code page with lead byte 0x81 alone: 0x41 'A', 0x80 U+20AC,
    0xFF U+F8F5, 0x81 0x40 U+4E00, invalid trail bytes the default, and U+00A5's
    best fit 0x81 0x41 (the pair of U+4E01)."""
    cp = N.CodePage.__new__(N.CodePage)
    cp.default = cp.unidefault = cp.trans = cp.transuni = 0x3F
    cp.single = [b if b < 0x80 else 0x3F for b in range(256)]
    cp.single[0x80], cp.single[0xFF], cp.single[0x81] = 0x20AC, 0xF8F5, 0
    cp.offsets = [0] * 256
    cp.offsets[0x81] = 256
    pairs = {0x40: 0x4E00, 0x41: 0x4E01}
    cp.pair = lambda lead, t: pairs.get(t, 0x3F) if lead == 0x81 else 0x3F
    cp.wc = [0x3F] * 0x10000
    for b in range(0x80):
        cp.wc[b] = b
    cp.wc[0x20AC], cp.wc[0xF8F5], cp.wc[0x4E00], cp.wc[0x4E01] = 0x80, 0xFF, 0x8140, 0x8141
    cp.wc[0x00A5] = 0x8141                           # best fit
    cp.leads = [0x81]
    return cp


def test_multibytetowidechar_pairs_a_lead_byte_with_its_trail_as_kernelbase_does():
    cp = codepage_toy()
    assert N.mbtowc(cp, 1, b"A\x81\x40\xff", 8) == (3, [0x41, 0x4E00, 0xF8F5], 0)
    assert N.mbtowc(cp, 1, b"A\x81\x40\xff", 0) == (3, [], 0)        # the count
    assert N.mbtowc(cp, 1, b"\x81", 4) == (1, [0x3F], 0)             # a lead byte alone
    assert N.mbtowc(cp, 1, b"\x81\x00", 4) == (2, [0x3F, 0], 0)      # before a NUL
    assert N.mbtowc(cp, 1, b"AAA", 2) == (0, [0x41, 0x41], 122)      # short buffer


def test_mb_err_invalid_chars_refuses_a_private_use_byte_and_a_bad_pair():
    """0xFF maps to U+F8F5, which kernelbase's check_invalid_chars refuses, so
    the C runtime's MB_ERR_INVALID_CHARS call over bytes 1-127 stops short of it."""
    cp = codepage_toy()
    assert N.mbtowc(cp, 9, bytes(range(1, 128)), 0) == (127, [], 0)
    assert N.mbtowc(cp, 9, b"\xff", 0) == (0, [], 1113)
    assert N.mbtowc(cp, 9, b"\x81\x7f", 0) == (0, [], 1113)
    assert N.mbtowc(cp, 9, b"\x81", 0) == (0, [], 1113)              # a partial character
    assert N.mbtowc(cp, 0x10, b"A", 0) == (0, [], 1004)


def test_widechartomultibyte_uses_the_best_fit_unless_asked_not_to():
    cp = codepage_toy()
    assert N.wctomb(cp, 0, [0x00A5, 0], 8) == (3, b"\x81\x41\x00", 0, 0)
    assert N.wctomb(cp, 0x400, [0x00A5, 0], 8) == (2, b"?\x00", 1, 0)
    assert N.wctomb(cp, 0, [0x4E02], 8) == (1, b"?", 1, 0)          # neither
    assert N.wctomb(cp, 0, [0x4E00, 0x41], 2) == (0, b"\x81\x40", 0, 122)
    assert N.wctomb(cp, 0, [0x41, 0x4E00], 2) == (0, b"A", 0, 122)   # no partial character
    assert N.wctomb(cp, 0, [0x4E02], 8, defchar=b"*\x00") == (1, b"*", 1, 0)


def test_the_valid_mapping_rule_is_kernelbase_s():
    cp = codepage_toy()
    assert cp.valid_mapping(0x00A5, False) and not cp.valid_mapping(0x00A5, True)
    assert cp.valid_mapping(0x4E00, True) and not cp.valid_mapping(0x4E02, False)
    assert cp.valid_mapping(0x3F, True)                              # '?' is its own


def test_isvalidlocale_is_false_for_the_three_defaults_and_true_for_custom_ones():
    m = {"lcids": [0x0409, 0x0804]}
    assert [LC.model_valid(m, x) for x in (0, 0x400, 0x800)] == [0, 0, 0]
    assert [LC.model_valid(m, x) for x in (0xC00, 0x1000, 0x1400)] == [1, 1, 1]
    assert (LC.model_valid(m, 0x0804), LC.model_valid(m, 0x0407)) == (1, 0)


# ------------------------------------------------------------------ the traces and the recorders

def test_a_trace_s_calls_are_keyed_as_the_recorders_key_them(tmp_path, monkeypatch):
    lines = [
        '4 f3 t516 007b7eae EnumSystemLocalesA proc=007b7d55 flags=00000001 begin',
        '5 f3 t516 007b7da1 GetLocaleInfoA 00000436 00001001 120 -> 10 "Afrikaans"',
        '6 f3 t516 00000000   enum-callback "00000436" -> 1',
        '7 f3 t516 00000000   enum-callback "00000804" -> 0',
        '8 f3 t516 007b7eae EnumSystemLocalesA end -> 1',
        '9 f3 t516 007bc636 GetLocaleInfoW 00000804 00000014 2 -> 2 L"\\u00a5"',
        '10 f3 t516 007b8008 IsValidLocale 00000804 00000001 -> 1',
        '11 f3 t516 007a92b4 GetCPInfo 936 -> 1 max=2 default=3f00 lead=81fe00000000000000000000',
        '12 f3 t516 007bc658 WideCharToMultiByte cp=936 00000000 -1 128 -> 3 defchar=null '
        'used=null in=00a50000 out=a3a400',
    ]
    p = tmp_path / "t.txt"
    p.write_text("\n".join(lines) + "\n", encoding="latin-1")
    monkeypatch.setattr(LC, "TRACES", {"n0-trace-v1a": (p, LC.sha256(p))})
    got = LC.native_calls("n0-trace-v1a")
    assert got["enums"] == [((0x0436, 1), (0x0804, 0))]
    assert got["info"][("GetLocaleInfoA", 0x007B7DA1, 0x0436, 0x1001, 120)] == \
        {(10, b"Afrikaans\0", 0)}
    assert got["info"][("GetLocaleInfoW", 0x007BC636, 0x0804, 0x14, 2)] == \
        {(2, b"\xa5\x00\x00\x00", 0)}
    assert got["valid"] == {("IsValidLocale", 0x0804): {1}}
    assert got["cpinfo"] == {936: {bytes.fromhex("020000003f0081fe00000000000000000000")}}
    assert got["conv"] == {("WideCharToMultiByte", 0x007BC658, 0, -1, 128, (0xA5, 0)):
                           {(3, (0xA3, 0xA4, 0))}}


def test_a_trace_that_is_not_the_pinned_one_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "t.txt"
    p.write_text("x\n")
    monkeypatch.setattr(LC, "TRACES", {"n0-trace-v1a": (p, "0" * 64)})
    with pytest.raises(SystemExit, match="not the pinned native trace"):
        LC.native_calls("n0-trace-v1a")


def test_the_recorder_s_lines_are_keyed_as_the_trace_s(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text(
        "7 t1 007bc636 GetLocaleInfoW 00000804 00000014 00000000 00000002 -> 00000002 "
        "out=00a50000 err=0\n"
        "8 t1 007b8008 IsValidLocale 00000804 00000001 -> 00000001 out= err=0\n"
        "9 t1 007b7eae EnumSystemLocalesA 007b7d55 00000001 -> 00000001 cb=00000436:1,"
        "00000804:0 err=0\n"
        "10 t1 007a7eb3 GetStdHandle fffffff6 -> 7b000000 out= err=0\n")
    got = LC.lifted_answers(p)
    assert got["info"] == {("GetLocaleInfoW", 0x007BC636, 0x0804, 0x14, 2):
                           {(2, b"\xa5\x00\x00\x00", 0)}}
    assert got["valid"] == {("IsValidLocale", 0x0804): {1}}
    assert got["enums"] == [((0x0436, 1), (0x0804, 0))]
    assert got["std"] == [("GetStdHandle", 0xFFFFFFF6, 0x7B000000)]


def test_a_key_the_lifted_run_makes_and_native_does_not_is_a_failure():
    native = {"a": {1}, "b": {2}}
    assert LC.follows(LC.keyed(native, {"a": {1}, "b": {2}}))
    assert not LC.follows(LC.keyed(native, {"a": {1}, "b": {3}}))           # differs
    assert not LC.follows(LC.keyed(native, {"a": {1}}))                     # uncovered
    assert not LC.follows(LC.keyed(native, {"a": {1}, "b": {2}, "c": {0}}))  # not native
    assert not LC.follows(LC.keyed(native, {}))                             # nothing compared


def test_the_start_up_probe_for_the_w_function_is_held_to_the_profile_s_answer():
    """GetLocaleInfoW(LOCALE_NEUTRAL, LOCALE_ILANGUAGE, NULL, 0) at 0x007BC578
    comes before any probe's Init; its native answer is the recorded value's
    length and NUL."""
    info = tuple((0, b"0409") if t == 0x0001 else (0, b"x") for t in DP.LOCALE_LCTYPES)
    p = DP.Profile(locale=DP.Locale(0x0409, 0x0409, 1252, 437, info))
    key = ("GetLocaleInfoW", 0x007BC578, 0, 0x0001, 0)
    assert LC.pre_init({key: {(5, b"", 0)}}, p) == {key: {(5, b"", 0)}}
    other = ("GetLocaleInfoW", 0x007BC5CD, 0, 0x0001, 0)     # any other site is traced
    assert LC.pre_init({other: {(5, b"", 0)}}, p) == {}


# ------------------------------------------------------------------ run's checks

# QueryPerformanceFrequency's answer: the native word log's start record (ref_hz) and the
# answer the lifted guest kept at Init (Sofdec's copy on tick 1)
ANSWERED = {"native_answer": 10_000_000, "lifted_answer": 10_000_000}
QPF_ANSWERED = {"native_answer": 10_000_000, "lifted_answer": 3_579_545}


def recorded(**over) -> dict:
    """One run as one_run records it, every comparison passing."""
    ok = {"compared": 3, "not_native": [], "not_native_count": 0, "differ": [],
          "differ_count": 0, "uncovered": [], "uncovered_count": 0}
    r = {"enumerations": {"lifted": 4, "not_native": 0, "whole": True, "stops_at_0804": True},
         "info": dict(ok), "valid": dict(ok), "cpinfo": dict(ok), "conversions": dict(ok),
         "environment": {"calls": 1, "follows": True},
         "std_handles": {"want": [2, None, 3], "got": [2, None, 3], "follows": True},
         "words": [{"native": "n", "compared_through": 1200, "differ": {}}],
         "time_words": [dict(ANSWERED, native="n", compared_through=1200, differ={})]}
    r.update(over)
    return r


QPF_MOVED = [dict(QPF_ANSWERED, native="n", compared_through=1200,
                  differ={"sofdec_frequency": [1], "sofdec_timer_unit": [380]})]


BROKEN = {"compared": 3, "not_native": [], "not_native_count": 0, "differ": ["k"],
          "differ_count": 1, "uncovered": [], "uncovered_count": 0}
NONE_936 = {"compared": 0, "not_native": [], "not_native_count": 0, "differ": [],
            "differ_count": 0, "uncovered": ["k"], "uncovered_count": 58}
NO_ENUM = {"lifted": 4, "not_native": 4, "whole": False, "stops_at_0804": False}


def runs(**over) -> dict:
    base = {"control": recorded(),
            "refuse": recorded(enumerations=NO_ENUM, info=BROKEN,
                               words=[{"native": "n", "compared_through": 1200,
                                       "differ": {"LC_TIME": [1]}}]),
            "noenum": recorded(enumerations=NO_ENUM, conversions=NONE_936,
                               info=dict(NONE_936, compared=3)),
            "env": recorded(environment={"calls": 1, "follows": False}),
            "stdio": recorded(std_handles={"want": [2, None, 3], "got": [3, None, 3],
                                           "follows": False}),
            "qpf": recorded(time_words=QPF_MOVED)}
    base.update(over)
    return base


def test_run_s_checks_pass_on_the_runs_the_breaks_should_give():
    assert all(LC.run_checks(runs()).values())


def test_a_break_that_moved_nothing_fails_its_check():
    got = LC.run_checks(runs(env=recorded(), stdio=recorded(), noenum=recorded(),
                             refuse=recorded(), qpf=recorded()))
    assert [k for k, v in got.items() if not v] == [
        "refuse_moves_them", "noenum_moves_them", "env_break_moves_it", "stdio_break_moves_it",
        "qpf_break_moves_it"]


def test_the_frequency_words_are_sofdec_s_copy_and_its_timer_unit():
    """QueryPerformanceFrequency's answer as the game keeps it: Sofdec's copy,
    written during Init, and its timer unit, written on tick 380 of the boot."""
    assert LC.TIME_WORDS == {"sofdec_frequency": 0x01C05CD0, "sofdec_timer_unit": 0x0176A608}
    assert LC.BREAKS["qpf"] == ("SA2_TIME_BREAK", "qpf")


def test_a_frequency_other_than_native_s_fails_and_the_break_must_move_both_words():
    """The G4 interrogation, Reviewer B's finding 1: 3,579,545 where every native
    boot holds 10,000,000. A control that differs on either word does not follow
    native; a break that moves one word of the two, or is compared through no
    tick, has not shown the check failing."""
    c = recorded(time_words=[dict(ANSWERED, native="n", compared_through=1200,
                                  differ={"sofdec_timer_unit": [380]})])
    assert LC.run_checks(runs(control=c))["frequency_words_follow_native"] is False
    one = recorded(time_words=[dict(QPF_ANSWERED, native="n", compared_through=1200,
                                    differ={"sofdec_frequency": [1]})])
    assert LC.run_checks(runs(qpf=one))["qpf_break_moves_it"] is False
    unheld = recorded(time_words=[dict(QPF_MOVED[0], compared_through=0)])
    assert LC.run_checks(runs(qpf=unheld))["qpf_break_moves_it"] is False
    assert LC.run_checks(runs(qpf=recorded(time_words=[])))["qpf_break_moves_it"] is False


def test_the_frequency_answer_is_held_to_each_native_log_s_recorded_answer():
    """The review of the G4 round-one fold, its runtime reviewer's finding 1: every
    native capture records QueryPerformanceFrequency's answer, the word log's start
    record (ref_hz), which the tick probe reads in the game's process. A lifted
    answer other than one native log's fails, however the words compare, and the
    break must move the answer as well as both words."""
    other = recorded(time_words=[dict(ANSWERED, native="n", compared_through=1200, differ={}),
                                 dict(ANSWERED, native="m", compared_through=1200, differ={},
                                      native_answer=3_579_545)])
    got = LC.run_checks(runs(control=other))
    assert got["frequency_answer_is_native"] is False
    assert got["frequency_words_follow_native"] is True
    assert LC.run_checks(runs(control=recorded(time_words=[])))[
        "frequency_answer_is_native"] is False
    kept = [dict(QPF_MOVED[0], lifted_answer=10_000_000)]         # words moved, answer not
    assert LC.run_checks(runs(qpf=recorded(time_words=kept)))["qpf_break_moves_it"] is False


def test_the_frequency_series_reads_the_keyframe_and_every_change():
    blank = {va: {} for va in LC.TIME_WORDS.values()}
    native = ({**blank, 0x01C05CD0: {0: 10_000_000}, 0x0176A608: {380: 10_000_000}}, 1200)
    lifted = ({**blank, 0x01C05CD0: {0: 3_579_545}, 0x0176A608: {380: 3_579_545}}, 1299)
    got = LC.words_differ(lifted, native, LC.TIME_WORDS)
    assert got == {"sofdec_frequency": [1, 2, 3, 4, 5], "sofdec_timer_unit": [380, 381, 382,
                                                                             383, 384]}
    assert LC.words_differ(native, native, LC.TIME_WORDS) == {}


def test_the_refuse_break_moves_the_words_only_on_ticks_both_logs_hold():
    unheld = recorded(enumerations=NO_ENUM, info=BROKEN,
                      words=[{"native": "n", "compared_through": 0, "differ": {}}])
    assert LC.run_checks(runs(refuse=unheld))["refuse_moves_them"] is False


def test_a_control_whose_words_were_compared_through_no_tick_does_not_follow_native():
    c = recorded(words=[{"native": "n", "compared_through": 0, "differ": {}}])
    assert LC.run_checks(runs(control=c))["locale_words_follow_native"] is False


def test_a_control_that_never_enumerates_the_whole_catalogue_fails():
    c = recorded(enumerations={"lifted": 2, "not_native": 0, "whole": False,
                               "stops_at_0804": True})
    assert LC.run_checks(runs(control=c))["enumeration_follows_native"] is False


# ------------------------------------------------------------------ tables, and its failures

@needs_private
def test_the_tree_s_tables_pass():
    got = LC.tables()
    assert got["checks"] == {"header_is_generated": True, "data_follows_wine": True,
                             "data_follows_native": True}
    assert (got["installed"], got["lcids"], got["answers"], got["fits"]) == (255, 438, 654, 412)
    assert got["counts"] == {"enumerations": 72, "info_keys": 741, "conversion_keys": 58}


def runtime_copy(tmp_path: Path, edit) -> None:
    """runtime/sa2_locale.{c,h} and sa2_localedata.h in tmp_path, the data edited."""
    for name in ("sa2_locale.c", "sa2_locale.h", "sa2_localedata.h"):
        shutil.copy(ROOT / "runtime" / name, tmp_path / name)
    h = tmp_path / "sa2_localedata.h"
    t = h.read_text()
    new = edit(t)
    assert new != t, "the edit changed nothing"
    h.write_text(new)


@needs_private
def test_a_best_fit_one_byte_off_fails_the_wine_check(tmp_path, monkeypatch):
    # U+00A5's best fit, 0xA3A4 (the fullwidth yen), made 0xA3A5.
    runtime_copy(tmp_path, lambda t: t.replace("{0x00A5u, 0xA3A4u},", "{0x00A5u, 0xA3A5u},"))
    monkeypatch.setattr(N, "HEADER", tmp_path / "sa2_localedata.h")
    monkeypatch.setattr(LC, "LOCALE_C", tmp_path / "sa2_locale.c")
    got = LC.tables()
    assert got["checks"] == {"header_is_generated": False, "data_follows_wine": False,
                             "data_follows_native": True}
    assert any(d.startswith("U+00A5:") for d in got["differences"]["wine"])


@needs_private
def test_an_enumeration_out_of_wine_s_order_fails(tmp_path, monkeypatch):
    # The first two installed locales swapped: 0x0436 (af-ZA) and 0x045E (am-ET).
    runtime_copy(tmp_path, lambda t: t.replace("    0x00436u, 0x0045Eu,", "    0x0045Eu, 0x00436u,", 1))
    monkeypatch.setattr(N, "HEADER", tmp_path / "sa2_localedata.h")
    monkeypatch.setattr(LC, "LOCALE_C", tmp_path / "sa2_locale.c")
    got = LC.tables()
    assert not got["checks"]["data_follows_wine"]
    assert "the installed-locale order differs from locale.nls's enumeration" in \
        got["differences"]["wine"]


@needs_private
def test_the_model_s_0804_and_0409_answers_are_the_traces_():
    m = N.model()
    assert LC.model_answer(m, 0x0804, 0x0014, True, 2) == (2, b"\xa5\x00\x00\x00", 0)
    assert LC.model_answer(m, 0x0804, 0x1004, False, 8) == (4, b"936\x00", 0)
    assert LC.model_answer(m, 0x0481, 0x1001, False, 120) == (6, b"Maori\x00", 0)   # UTF-8's ACP
    assert m["installed"][:2] == [(0x0436, m["installed"][0][1]), (0x045E, m["installed"][1][1])]


def test_noenum_s_check_reads_the_noenum_run_s_enumeration():
    """The noenum break must stop the enumeration at 0x0804 in its own run; the
    refuse run's enumeration says nothing about it."""
    enum_ok = {"lifted": 28, "not_native": 0, "whole": True, "stops_at_0804": True}
    still = runs(noenum=recorded(enumerations=enum_ok, conversions=NONE_936,
                                   info=dict(NONE_936, compared=3)))
    assert LC.run_checks(still)["noenum_moves_them"] is False
    other = runs(refuse=recorded(enumerations=dict(enum_ok, not_native=1), info=BROKEN,
                                   words=[{"native": "n", "compared_through": 1200,
                                           "differ": {"LC_TIME": [1]}}]))
    assert LC.run_checks(other)["noenum_moves_them"] is True


def test_the_refuse_break_with_no_word_comparison_moved_nothing():
    assert LC.run_checks(runs(refuse=recorded(enumerations=NO_ENUM, info=BROKEN,
                                                words=[])))["refuse_moves_them"] is False



@needs_private
def test_getcpinfo_s_lead_bytes_one_off_fails_the_wine_check(tmp_path, monkeypatch):
    for name in ("sa2_locale.c", "sa2_locale.h", "sa2_localedata.h"):
        shutil.copy(ROOT / "runtime" / name, tmp_path / name)
    c = tmp_path / "sa2_locale.c"
    c.write_text(c.read_text().replace("    out[1] = SA2_CP936_LEAD_LAST;", "    out[1] = 0xFFu;"))
    monkeypatch.setattr(LC, "LOCALE_C", tmp_path / "sa2_locale.c")
    got = LC.tables()
    assert not got["checks"]["data_follows_wine"]
    assert any(d.startswith("GetCPInfo(936)'s lead-byte ranges") for d in got["differences"]["wine"])


@needs_private
def test_sa2_locale_numeric_one_lctype_off_fails_the_wine_check(tmp_path, monkeypatch):
    """LOCALE_IDEFAULTANSICODEPAGE (0x1004) is a number; the driver asks
    sa2_locale_numeric of every LCTYPE the profile holds."""
    for name in ("sa2_locale.c", "sa2_locale.h", "sa2_localedata.h"):
        shutil.copy(ROOT / "runtime" / name, tmp_path / name)
    c = tmp_path / "sa2_locale.c"
    text = c.read_text()
    assert "    case 0x1004u: case 0x1009u:\n" in text
    c.write_text(text.replace("    case 0x1004u: case 0x1009u:\n", "    case 0x1009u:\n"))
    monkeypatch.setattr(LC, "LOCALE_C", tmp_path / "sa2_locale.c")
    got = LC.tables()
    assert not got["checks"]["data_follows_wine"]
    assert "LCTYPE 0x1004: sa2_locale_numeric says 0, the model's kind is number" in \
        got["differences"]["wine"]


# ------------------------------------------------------------------ the receipt

def ran(r: dict, name: str) -> dict:
    """A recorded() run with what one_run adds beside the comparisons."""
    return dict(r, env={} if name == "control" else dict([LC.BREAKS[name]]),
                capture_sha256="c" * 64,
                answers={"locale": f"private/generated/replay/locale-{name}/locale-answers.txt",
                         "locale_sha256": "a" * 64,
                         "ctype": f"private/generated/replay/locale-{name}/ctype-answers.txt",
                         "ctype_sha256": "b" * 64})


def run_result(**over) -> dict:
    rs = {name: ran(r, name) for name, r in runs(**over).items()}
    checks = LC.run_checks(rs)
    return {"checks": checks, "profile": "p", "profile_sha256": "d" * 64, "runs": rs,
            "ok": all(checks.values())}


TABLES = {"checks": {"header_is_generated": True, "data_follows_wine": True,
                     "data_follows_native": True},
          "counts": {"enumerations": 72, "info_keys": 741, "conversion_keys": 58},
          "installed": 255, "lcids": 438, "answers": 654, "fits": 412, "ok": True}
INPUTS = {"runtime_sha256": "e" * 64, "pipeline": {q: "f" * 64 for q in LC.PIPELINE},
          "capture": {"registered": None, "path": "private/generated/oracle/captures/x"},
          "device_profile": {"path": "p", "sha256": "d" * 64}}
ORIGIN = {"commit": "0" * 40, "uncommitted_changes": True}


def test_the_receipt_holds_the_inputs_the_command_and_every_check():
    doc = LC.receipt(TABLES, run_result(), INPUTS, ORIGIN, "cmd")
    assert (doc["schema"], doc["id"], doc["generated_by"], doc["command"]) == (
        "sa2-recomp-receipt/v1", "s4-locale-check-v1", "tools/s4/locale_check.py", "cmd")
    assert (doc["commit"], doc["uncommitted_changes"]) == ("0" * 40, True)
    assert doc["inputs"] == INPUTS
    assert list(doc["tables"]["checks"]) == list(TABLES["checks"])
    assert len(doc["run"]["checks"]) == 15 and all(doc["run"]["checks"].values())
    assert sorted(doc["run"]["runs"]) == ["control", "env", "noenum", "qpf", "refuse", "stdio"]
    assert doc["run"]["runs"]["qpf"]["env"] == {"SA2_TIME_BREAK": "qpf"}
    assert doc["run"]["runs"]["qpf"]["time_words"] == [
        {"native": "n", "compared_through": 1200,
         "differ": ["sofdec_frequency", "sofdec_timer_unit"],
         "native_answer": 10_000_000, "lifted_answer": 3_579_545}]
    refuse = doc["run"]["runs"]["refuse"]
    assert refuse["env"] == {"SA2_LOCALE_BREAK": "refuse"}
    assert refuse["info"] == {"compared": 3, "not_native_count": 0, "differ_count": 1,
                              "uncovered_count": 0}
    assert refuse["words"] == [{"native": "n", "compared_through": 1200, "differ": ["LC_TIME"]}]
    assert doc["answer"].startswith("Yes: every check holds, 3 of tables")


def test_no_receipt_when_any_check_of_either_mode_fails():
    with pytest.raises(LC.Refusal, match="run stdio_break_moves_it failed"):
        LC.receipt(TABLES, run_result(stdio=recorded()), INPUTS, ORIGIN, "cmd")
    bad = dict(TABLES, checks=dict(TABLES["checks"], data_follows_native=False))
    with pytest.raises(LC.Refusal, match="tables data_follows_native failed"):
        LC.receipt(bad, run_result(), INPUTS, ORIGIN, "cmd")
    with pytest.raises(LC.Refusal, match="another profile"):
        LC.receipt(TABLES, dict(run_result(), profile_sha256="0" * 64), INPUTS, ORIGIN, "cmd")


def test_write_refuses_a_profile_file_and_a_mode_other_than_run(tmp_path):
    for argv in (["run", "--write", "--profile", str(tmp_path / "p.txt")],
                 ["tables", "--write"]):
        with pytest.raises(LC.Refusal, match="a stand-in"):
            LC.main(argv)


def stub_write(monkeypatch, tmp_path, run, inputs_seq):
    monkeypatch.setattr(LC, "RECEIPT", tmp_path / "s4-locale-check-v1.json")
    monkeypatch.setattr(LC, "provenance", lambda: ORIGIN)
    monkeypatch.setattr(LC, "tables", lambda: TABLES)
    monkeypatch.setattr(LC, "profile_of", lambda capture, profile_file: ({}, []))
    seq = iter(inputs_seq)
    monkeypatch.setattr(LC, "receipt_inputs", lambda prof, natives, capture: next(seq))
    monkeypatch.setattr(LC, "run_mode", lambda capture, profile_file: run)


def test_write_writes_the_receipt_when_everything_holds(tmp_path, monkeypatch):
    stub_write(monkeypatch, tmp_path, run_result(), [INPUTS, INPUTS])
    got = LC.write_mode(tmp_path / "capture", None)
    assert got["written"].endswith("s4-locale-check-v1.json")
    doc = json.loads((tmp_path / "s4-locale-check-v1.json").read_text())
    assert doc["command"].startswith(".venv/bin/python tools/s4/locale_check.py run --write "
                                     "--capture ")
    assert set(doc["inputs"]["pipeline"]) == set(LC.PIPELINE)


def test_write_writes_nothing_when_a_run_check_fails(tmp_path, monkeypatch):
    stub_write(monkeypatch, tmp_path, run_result(env=recorded()), [INPUTS, INPUTS])
    got = LC.write_mode(None, None)
    assert got["written"] is None and not got["ok"]
    assert not (tmp_path / "s4-locale-check-v1.json").exists()


def test_write_writes_nothing_when_an_input_moved_under_the_boots(tmp_path, monkeypatch):
    moved = dict(INPUTS, runtime_sha256="9" * 64)
    stub_write(monkeypatch, tmp_path, run_result(), [INPUTS, moved])
    with pytest.raises(LC.Refusal, match="moved while the boots ran"):
        LC.write_mode(None, None)
    assert not (tmp_path / "s4-locale-check-v1.json").exists()


def test_the_pipeline_the_receipt_pins_is_tracked_files_of_the_tree():
    assert all((ROOT / q).is_file() for q in LC.PIPELINE)
    assert not any(q.startswith("private/") for q in LC.PIPELINE)


# ------------------------------------------------------------------ the receipt's refusals (review-lc-fold)

def test_write_writes_nothing_when_the_commit_moved_under_the_boots(tmp_path, monkeypatch):
    stub_write(monkeypatch, tmp_path, run_result(), [INPUTS, INPUTS])
    seen = iter([ORIGIN, dict(ORIGIN, commit="1" * 40)])
    monkeypatch.setattr(LC, "provenance", lambda: next(seen))
    with pytest.raises(LC.Refusal, match="moved while the boots ran"):
        LC.write_mode(None, None)
    assert not (tmp_path / "s4-locale-check-v1.json").exists()


def test_provenance_refuses_a_tree_git_cannot_read(tmp_path, monkeypatch):
    """Refused, not recorded as commit "" on a clean tree."""
    monkeypatch.setattr(LC, "ROOT", tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    with pytest.raises(LC.Refusal, match="git rev-parse HEAD exited"):
        LC.provenance()


def test_the_receipt_pins_the_probe_and_reader_that_make_the_profile_evidence():
    """DP.capture_profile takes a capture only from the probe in the tree, read
    through device_profile.py; a receipt that did not pin both would not say
    which probe wrote the profile it vouches for."""
    assert DP.PROBE in LC.PIPELINE and "tools/s4/device_profile.py" in LC.PIPELINE


def test_the_registered_boot_s_inputs_name_its_capture():
    """The landing's `run --write` takes the registered boot (no --capture)."""
    import tick_replay as TR
    entry = next(e for e in json.loads(TR.REGISTRY.read_text())["replays"] if e["name"] == "boot")
    got = LC.receipt_inputs({"path": "p", "sha256": "d" * 64}, [], None)
    assert got["capture"] == {"registered": "boot", "path": entry["native"]["capture"]}
    assert set(got["pipeline"]) == set(LC.PIPELINE) and got["native_word_logs"] == []
