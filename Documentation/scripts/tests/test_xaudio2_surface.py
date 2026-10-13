"""The XAudio2 surface tool's rule, its censuses and its comparisons, each made
to fail one way at a time.

`tools/s4/xaudio2_surface.py` is what stands between runtime/sa2_xaudio2.c and
the defect class that corrupts a caller's frame without changing an answer: a
declared argument count that is not the one the image pushes. A checker that
agrees with everything passes every agreement test ever written, so every
agreement below is paired with the mutation that must disagree.

The first group runs on hand-encoded instructions and on the committed
runtime source, so it runs anywhere. The second decodes the unpacked
executable and the third Wine's xaudio2_6.dll from the oracle's prefix; both
are private, and those tests are skipped only when the file is absent, as in
a clone.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import xaudio2_surface as XS  # noqa: E402

WINE_DLL = ROOT / "private" / "proton-prefix" / "pfx" / "drive_c" / "windows" / "syswow64" \
    / "xaudio2_6.dll"

# push esi; mov eax,[esi+10h]; mov ecx,[eax]; lea edx,[esp+10h]; push edx;
# push eax; mov eax,[ecx+64h]; call eax; pop esi; ret -- the shape of the
# streaming thread's GetState at 0x0078478E, at a made-up address.
SITE = bytes.fromhex("56" "8B4610" "8B08" "8D542410" "52" "50" "8B4164" "FFD0" "5E" "C3")
BASE = 0x1000
LOAD = BASE + 1                     # the `mov eax,[esi+10h]`
SEEDS = {LOAD: (XS.SOURCE, "a test seed")}


def scan(blob: bytes, seeds: dict | None = None) -> list[dict]:
    return XS.scan_bytes(blob, BASE, {}, SEEDS if seeds is None else seeds, XS.NAMES)


# ---------------------------------------------------------------- the rule

def test_a_seeded_load_attributes_the_call_and_counts_one_argument() -> None:
    sites = scan(SITE)
    assert [(s["iface"], s["slot"], s["name"], s["argc_rule"], s["site"]) for s in sites] == \
        [(XS.SOURCE, 25, "GetState", 1, "0x0000100f")]
    assert sites[0]["this_from"] == "0x00001001"
    assert sites[0]["pushes_total"] == 3            # the save, the argument, `this`


def test_without_the_seed_there_is_no_site() -> None:
    assert scan(SITE, seeds={}) == []


def test_a_seed_on_an_instruction_that_is_not_a_load_is_reported() -> None:
    kinds: dict = {}
    XS.scan_bytes(SITE, BASE, {}, {BASE + 4: (XS.SOURCE, "points at mov ecx,[eax]")},
                  XS.NAMES, None, kinds)
    assert kinds == {BASE + 4: "load"}               # `mov ecx,[eax]` is a load...
    kinds = {}
    XS.scan_bytes(SITE, BASE, {}, {BASE + 6: (XS.SOURCE, "points at the lea")}, XS.NAMES,
                  None, kinds)
    assert kinds == {BASE + 6: "not a load"}         # ...and `lea` is not


def test_a_second_argument_push_is_counted() -> None:
    blob = SITE.replace(bytes.fromhex("52" "50"), bytes.fromhex("6A00" "52" "50"))
    sites = scan(blob)
    assert sites[0]["argc_rule"] == 2
    assert sites[0]["pushed"] == [0, None]


def test_a_push_of_a_register_not_written_since_the_last_call_is_a_save() -> None:
    # `push edi` with EDI untouched, then the argument and `this`: one argument.
    blob = SITE.replace(bytes.fromhex("52" "50"), bytes.fromhex("57" "52" "50"))
    assert scan(blob)[0]["argc_rule"] == 1


def test_a_pop_retires_the_push_it_undoes() -> None:
    # `push 0; pop edi` before the argument: without pop-matching the stale
    # immediate would be walked as a second argument.
    blob = SITE.replace(bytes.fromhex("52" "50"), bytes.fromhex("6A00" "5F" "52" "50"))
    assert scan(blob)[0]["argc_rule"] == 1


def test_push_minus_one_is_read_sign_extended() -> None:
    # 6A FF is `push -1`: iced's immediate32 is the raw 0xFF, and the witness
    # for Initialize at 0x0078411B depends on reading 0xFFFFFFFF.
    blob = SITE.replace(bytes.fromhex("52" "50"), bytes.fromhex("6AFF" "52" "50"))
    assert scan(blob)[0]["pushed"] == [0xFFFFFFFF, None]


# ---------------------------------------------------------------- the census

def census_of(blob: bytes) -> dict:
    """The global census of one hand-encoded range with no attributed call."""
    refs: list = []
    XS.scan_bytes(blob, BASE, {}, {}, XS.NAMES, refs=refs)
    return XS.global_census(refs, [])


@pytest.mark.parametrize("code, role", [
    ("68DC0EAF01", "out-parameter"),      # push 1AF0EDCh: its address, as 0x00784202 pushes it
    ("A3DC0EAF01", "store"),              # mov [1AF0EDCh],eax
    ("833DD80EAF0100", "compare"),        # cmp dword ptr [1AF0ED8h],0
])
def test_the_census_names_a_role_only_in_its_operand_shape(code: str, role: str) -> None:
    census = census_of(bytes.fromhex(code + "C3"))
    assert [r["role"] for r in census["references"]] == [role]
    assert census["ok"]


@pytest.mark.parametrize("code", [
    # push dword ptr [1AF0EDCh]: the master's value handed to a callee the scan
    # does not follow. Read as an out-parameter, it passed the census.
    "FF35DC0EAF01",
    # mov dword ptr [esp+4],1AF0EDCh: the address stored in a stack slot, not a
    # store to the global. Read as a store, it passed the census.
    "C7442404DC0EAF01",
])
def test_a_reference_in_another_shape_fails_the_census(code: str) -> None:
    census = census_of(bytes.fromhex(code + "C3"))
    assert not census["ok"]
    assert [(r["at"], r["role"]) for r in census["unaccounted"]] == [("0x00001000", None)]


# ---------------------------------------------------------------- --falsify

class _NoImage:
    """Stands for the executable: the scan it would feed is faked below."""

    def __init__(self, path) -> None:
        self.path = path


GETSTATE = {"iface": XS.SOURCE, "slot": 25, "name": "GetState", "site": "0x0078478e"}
START = {"iface": XS.SOURCE, "slot": 19, "name": "Start", "site": "0x00784437"}


@pytest.mark.parametrize("without_seeds, without_anything, status", [
    ([GETSTATE], [], 0),                  # each removal loses sites: both are load-bearing
    ([GETSTATE, START], [], 1),           # the seeds attribute nothing the roots do not
    ([GETSTATE], [GETSTATE], 1),          # a site survives with no roots and no seeds
])
def test_falsify_fails_the_run_when_seeds_or_roots_bear_nothing(
        monkeypatch, tmp_path: Path, without_seeds: list, without_anything: list,
        status: int) -> None:
    def scan_image(img, roots, seeds):
        found = [GETSTATE, START] if roots and seeds else without_seeds if roots \
            else without_anything
        return list(found), [], {}, {}, []

    monkeypatch.setattr(XS.IM, "Image", _NoImage)
    monkeypatch.setattr(XS.C, "sha256", lambda path: "0" * 64)
    monkeypatch.setattr(XS, "scan_image", scan_image)
    with pytest.raises(SystemExit) as done:
        XS.main(["--exe", str(tmp_path / "sonic2app-unpacked.exe"), "--falsify"])
    assert done.value.code == status


# ---------------------------------------------------------------- the rows

def test_the_committed_rows_are_the_2_6_table() -> None:
    declared = XS.declared_from_source(XS.RUNTIME_SOURCE)
    assert len(declared) == 16 + 19 + 29
    assert XS.names_check(declared)["ok"]
    answered = sorted((k[0], k[1]) for k, d in declared.items() if d["implemented"])
    assert answered == sorted([
        (XS.ENGINE, 2), (XS.ENGINE, 5), (XS.ENGINE, 8), (XS.ENGINE, 10),
        (XS.MASTER, 0), (XS.MASTER, 18),
        (XS.SOURCE, 16), (XS.SOURCE, 18), (XS.SOURCE, 19), (XS.SOURCE, 20),
        (XS.SOURCE, 21), (XS.SOURCE, 22), (XS.SOURCE, 25), (XS.SOURCE, 26)])


def test_a_2_8_getstate_in_the_source_fails_the_name_check(tmp_path: Path) -> None:
    src = XS.RUNTIME_SOURCE.read_text()
    row = '{25, 1, XF_OUT | XF_CLK, "GetState", m_GetState}'
    assert row in src
    bad = tmp_path / "sa2_xaudio2.c"
    bad.write_text(src.replace(row, row.replace("{25, 1,", "{25, 2,")))
    res = XS.names_check(XS.declared_from_source(bad))
    assert not res["ok"]
    assert res["mismatches"] == [{"interface": XS.SOURCE, "slot": 25, "want": ["GetState", 1],
                                  "declared": ["GetState", 2]}]


def test_argc_check_catches_each_way_the_rows_can_be_wrong() -> None:
    sites = scan(SITE)
    base = {(XS.SOURCE, 25): {"interface": XS.SOURCE, "slot": 25, "argc": 1,
                              "name": "GetState", "implemented": True}}
    assert XS.argc_check(sites, base, "t")["ok"]
    wrong = copy.deepcopy(base)
    wrong[(XS.SOURCE, 25)]["argc"] = 2
    assert [r["site"] for r in XS.argc_check(sites, wrong, "t")["disagreements"]] == ["0x0000100f"]
    refused = copy.deepcopy(base)
    refused[(XS.SOURCE, 25)]["implemented"] = False
    assert XS.argc_check(sites, refused, "t")["called_but_refused"]
    extra = copy.deepcopy(base)
    extra[(XS.SOURCE, 27)] = {"interface": XS.SOURCE, "slot": 27, "argc": 1,
                              "name": "GetFrequencyRatio", "implemented": True}
    res = XS.argc_check(sites, extra, "t")
    assert not res["ok"]
    assert res["answered_but_never_called"] == [{"interface": XS.SOURCE, "slot": 27,
                                                  "name": "GetFrequencyRatio"}]
    assert not XS.argc_check([], base, "t")["ok"]    # nothing compared is not a pass


# ---------------------------------------------------------------- the image

@pytest.fixture(scope="module")
def image():
    if not XS.IM.DEFAULT_EXE.is_file():
        pytest.skip(f"the unpacked executable is not present: {XS.IM.DEFAULT_EXE}")
    img = XS.IM.Image(XS.IM.DEFAULT_EXE)
    sites, _shape, _cover, kinds, refs = XS.scan_image(img, XS.GLOBAL_ROOTS, XS.LOAD_SEEDS)
    return img, sites, kinds, refs


SURFACE = {
    (XS.ENGINE, 2): ["0x0077ee31", "0x00784137", "0x00784269"],
    (XS.ENGINE, 5): ["0x0078411b"],
    (XS.ENGINE, 8): ["0x007843a0"],
    (XS.ENGINE, 10): ["0x00784208"],
    (XS.MASTER, 0): ["0x0078421d"],
    (XS.MASTER, 18): ["0x0077ee1a", "0x0078424e"],
    (XS.SOURCE, 16): ["0x007843c9"],
    (XS.SOURCE, 18): ["0x007843f3", "0x007844b3"],
    (XS.SOURCE, 19): ["0x00784437"],
    (XS.SOURCE, 20): ["0x0078449d"],
    (XS.SOURCE, 21): ["0x0078472e"],
    (XS.SOURCE, 22): ["0x007844a8"],
    (XS.SOURCE, 25): ["0x0078478e"],
    (XS.SOURCE, 26): ["0x007845af"],
}


def test_the_image_calls_fourteen_slots_at_eighteen_sites(image) -> None:
    _img, sites, _kinds, _refs = image
    got: dict = {}
    for s in sites:
        got.setdefault((s["iface"], s["slot"]), []).append(s["site"])
    assert {k: sorted(v) for k, v in got.items()} == SURFACE
    assert all(s["this_push_is_the_interface"] for s in sites)


def test_the_witnesses_seeds_and_censuses_hold(image) -> None:
    img, sites, kinds, refs = image
    assert XS.check(sites)["ok"]
    assert XS.seed_check(kinds)["ok"]
    census = XS.global_census(refs, sites)
    assert census["ok"]
    assert len(census["references"]) == 14
    assert XS.field_census(img, XS.LOAD_SEEDS)["ok"]


def test_the_committed_rows_match_every_site(image) -> None:
    _img, sites, _kinds, _refs = image
    res = XS.argc_check(sites, XS.declared_from_source(XS.RUNTIME_SOURCE), "source")
    assert res["ok"]
    assert len(res["compared"]) == 18


def test_the_argc_break_is_caught_at_the_streaming_thread(image) -> None:
    """SA2_XAUDIO2_BREAK=argc declares GetState with 2.8's two arguments;
    the probe publishes that count, and this is what the tool says of it."""
    _img, sites, _kinds, _refs = image
    declared = XS.declared_from_source(XS.RUNTIME_SOURCE)
    declared[(XS.SOURCE, 25)] = dict(declared[(XS.SOURCE, 25)], argc=2)
    res = XS.argc_check(sites, declared, "argc break")
    assert not res["ok"]
    assert [r["site"] for r in res["disagreements"]] == ["0x0078478e"]


def test_a_witness_with_the_wrong_count_fails(image, monkeypatch) -> None:
    _img, sites, _kinds, _refs = image
    w = dict(XS.WITNESSES)
    w[(XS.SOURCE, 25)] = ("GetState", 2, [[None, None]])
    monkeypatch.setattr(XS, "WITNESSES", w)
    res = XS.check(sites)
    assert not res["ok"] and not res["witnesses"]["IXAudio2SourceVoice:25"]["held"]


def test_an_unconsumed_root_load_fails_the_global_census(image) -> None:
    _img, sites, _kinds, refs = image
    without = [s for s in sites if (s["iface"], s["slot"]) != (XS.ENGINE, 8)]
    res = XS.global_census(refs, without)
    assert not res["ok"]
    assert [r["at"] for r in res["unaccounted"]] == ["0x00784370"]


def test_a_dropped_seed_fails_the_field_census(image) -> None:
    img, _sites, _kinds, _refs = image
    seeds = {k: v for k, v in XS.LOAD_SEEDS.items() if k != 0x00784780}
    res = XS.field_census(img, seeds)
    assert not res["ok"]
    assert [r["at"] for r in res["unexplained"]] == ["0x00784780"]


def test_the_seeds_and_roots_are_load_bearing(image) -> None:
    img, sites, _kinds, _refs = image
    no_seeds = XS.scan_image(img, XS.GLOBAL_ROOTS, {})[0]
    assert sorted(s["site"] for s in no_seeds) == [
        "0x0077ee1a", "0x0077ee31", "0x00784208", "0x0078421d", "0x0078424e",
        "0x00784269", "0x007843a0"]
    assert XS.scan_image(img, {}, {})[0] == []
    assert len(sites) == 18


# ---------------------------------------------------------------- Wine's DLL

@pytest.fixture(scope="module")
def wine():
    if not WINE_DLL.is_file():
        pytest.skip(f"Wine's xaudio2_6.dll is not present: {WINE_DLL}")
    return XS.wine_vtables(WINE_DLL)


def test_wine_serves_the_2_6_tables_the_runtime_declares(wine) -> None:
    assert {k: len(v["rows"]) for k, v in wine.items()} == {XS.ENGINE: 16, XS.MASTER: 19,
                                                            XS.SOURCE: 29}
    assert XS.wine_check(WINE_DLL, XS.declared_from_source(XS.RUNTIME_SOURCE))["ok"]
    # DestroyVoice ends in a tail call through an import slot; the pop is
    # read from that shape, not assumed.
    assert wine[XS.SOURCE]["rows"][18]["argc"] == 0
    assert wine[XS.SOURCE]["rows"][25]["argc"] == 1


def test_wine_disagrees_with_a_2_8_getstate(wine) -> None:
    declared = XS.declared_from_source(XS.RUNTIME_SOURCE)
    declared[(XS.SOURCE, 25)] = dict(declared[(XS.SOURCE, 25)], argc=2)
    res = XS.wine_check(WINE_DLL, declared)
    assert not res["ok"]
    assert res["mismatches"] == [{"interface": XS.SOURCE, "slot": 25,
                                  "wine": ["GetState", 1], "declared": ["GetState", 2]}]
