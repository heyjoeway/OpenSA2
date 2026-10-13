"""M7's regeneration (tools/s4/m7_inputs.py) and its staleness gate (check_m7_inputs.py).

The user's ruling of 2026-10-04 (Documentation/decisions/2026-10-04-S5-user-rulings.md): M7
was stale, regenerate it with the full denominator, and add a staleness check like run
reach's. These tests call the tool and the gate the way they are used, on crafted inputs, and
assert literal results. The historical test reads git (read-only) and the census test the
pinned executable; neither carries a shipped byte, and both fail rather than skip when what
they read is absent, as the gate does.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))

import check_m7_inputs as G  # noqa: E402
import m7_inputs as M  # noqa: E402
import run_all_gates  # noqa: E402

# ------------------------------------------------------------------ the runtime's tables

C_TEXT = """
#define BASETEXTURE_ROWS \\
    {11, 1, 0, "SetLOD", m_zero}, \\
    {13, 0, 0, "GetLevelCount", m_GetLevelCount}

#define RESOURCE_ROWS \\
    {0,  2, 0, "QueryInterface", m_QueryInterface}, \\
    {2,  0, 0, "Release", m_Release}

static const d3d_row ROWS_DEVICE[] = {
    {0,  2, 0, "QueryInterface", m_QueryInterface},
    {17, 0, DF_OUT | DF_CAP, "Present", m_Present},
    {25, 6, DF_OUT, "CreateCubeTexture",
     NULL},
    {0, 0, 0, NULL, NULL},
};

static const d3d_row ROWS_TEXTURE[] = {
    RESOURCE_ROWS,
    BASETEXTURE_ROWS,
    {18, 2, DF_VOID, "GetSurfaceLevel", m_TexGetSurfaceLevel},
    {0, 0, 0, NULL, NULL},
};

static d3d_type g_types[] = {
    {"IDirect3DDevice9",            ROWS_DEVICE,       0, 0},
    {"IDirect3DTexture9",           ROWS_TEXTURE,      0, 0},
};
"""


def test_the_device_rows_are_read_with_their_handlers_flags_and_macros() -> None:
    rows = M.parse_device_rows(C_TEXT)
    assert rows == {
        "IDirect3DDevice9": {
            0: {"method": "QueryInterface", "argc": 2, "flags": "", "implemented": True},
            17: {"method": "Present", "argc": 0, "flags": "out,cap", "implemented": True},
            25: {"method": "CreateCubeTexture", "argc": 6, "flags": "out", "implemented": False},
        },
        "IDirect3DTexture9": {
            0: {"method": "QueryInterface", "argc": 2, "flags": "", "implemented": True},
            2: {"method": "Release", "argc": 0, "flags": "", "implemented": True},
            11: {"method": "SetLOD", "argc": 1, "flags": "", "implemented": True},
            13: {"method": "GetLevelCount", "argc": 0, "flags": "", "implemented": True},
            18: {"method": "GetSurfaceLevel", "argc": 2, "flags": "void", "implemented": True},
        },
    }


def test_a_flag_the_parser_does_not_know_stops_it() -> None:
    with pytest.raises(M.Refused, match="DF_NEW"):
        M.parse_device_rows(C_TEXT.replace("DF_OUT | DF_CAP", "DF_OUT | DF_NEW"))


def test_the_d3dx9_rows_are_implemented_only_with_a_handler() -> None:
    text = """
    {"d3d9.dll",      "Direct3DCreate9",              1, SA2_IMP_STDCALL, imp_Direct3DCreate9},
    {"d3dx9_42.dll",  "D3DXCreateTexture",            8, SA2_IMP_STDCALL,
     imp_D3DXCreateTexture},
    {"d3dx9_42.dll",  "D3DXFilterTexture",            4, SA2_IMP_STDCALL, NULL},
    """
    assert M.parse_d3dx9_rows(text) == {"D3DXCreateTexture": True, "D3DXFilterTexture": False}


def test_the_cross_check_names_each_disagreement() -> None:
    tree = M.parse_device_rows(C_TEXT)
    printed = json.loads(json.dumps(tree), object_hook=lambda d: {
        (int(k) if k.isdigit() else k): v for k, v in d.items()})
    printed["IDirect3DDevice9"][25]["implemented"] = True
    printed["IDirect3DTexture9"][18]["argc"] = 1
    diffs = M.cross_check(printed, tree, {"D3DXFilterTexture": True}, {})
    assert diffs == [("IDirect3DDevice9::CreateCubeTexture", "implemented", True, False),
                     ("IDirect3DTexture9::GetSurfaceLevel", "argc", 1, 2),
                     ("d3dx9_42!D3DXFilterTexture", "implemented", True, False)]
    assert M.describe(diffs[0]) == ("IDirect3DDevice9::CreateCubeTexture: the numerator says "
                                    "implemented=True and runtime/sa2_d3d9.c says False")
    assert M.cross_check(tree, tree, {}, {}) == []


def git_show(commit: str, rel: str) -> str:
    return subprocess.run(["git", "show", f"{commit}:{rel}"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def test_the_cross_check_would_have_caught_the_stale_58_of_67() -> None:
    """The historical case. At development_snapshot M7 published 58/67 from the runtime of that commit;
    against the corrected runtime at the regeneration commit, the cross-check names exactly
    the nine methods M7 counted as called and not implemented, and nothing else."""
    corrected = "development_snapshot"
    old_d3dx9 = M.parse_d3dx9_rows(git_show("development_snapshot", M.IMPORTS_C))
    called = json.loads(git_show("development_snapshot", M.RECEIPT))["called"]
    imports = [c.split("!", 1)[1] for c in called if c.startswith("d3dx9_42!")]
    diffs = M.cross_check(M.parse_device_rows(git_show("development_snapshot", M.D3D9_C)),
                          M.parse_device_rows(git_show(corrected, M.D3D9_C)),
                          {n: old_d3dx9.get(n, False) for n in imports},
                          M.parse_d3dx9_rows(git_show(corrected, M.IMPORTS_C)))
    assert len(diffs) == 9 and all(d[1:] == ("implemented", False, True) for d in diffs), diffs
    assert sorted(d[0] for d in diffs) == [
        "IDirect3DVolumeTexture9::GetLevelDesc",
        "d3dx9_42!D3DXCreateCubeTexture",
        "d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx",
        "d3dx9_42!D3DXCreateVolumeTexture",
        "d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx",
        "d3dx9_42!D3DXFilterTexture",
        "d3dx9_42!D3DXLoadSurfaceFromSurface",
        "d3dx9_42!D3DXSaveSurfaceToFileA",
        "d3dx9_42!D3DXSaveTextureToFileA",
    ]
    assert len(called) == 67


# ------------------------------------------------------------------ derive

def table_row(interface, slot, method, implemented, argc=0, flags=""):
    return {"interface": interface, "slot": slot, "method": method, "argc": argc,
            "flags": flags, "implemented": implemented}


def inputs(**over) -> M.Inputs:
    base = dict(
        scan={"layer_b_attributed": {"methods": [
                  {"interface": "IDirect3DDevice9", "method": "Present", "slot": 17},
                  {"interface": "IDirect3DPixelShader9|IDirect3DVertexShader9",
                   "method": "GetFunction", "slot": 4}]},
              "d3dx9_42": {"imports": [{"name": "D3DXCreateTexture", "call_sites": 1},
                                       {"name": "D3DXFilterTexture", "call_sites": 0}]},
              "layer_a_shape_census": {"vtable_shaped_call_sites": 9,
                                       "unattributed_vtable_call_sites": 4,
                                       "unattributed_that_could_still_be_d3d9": 3,
                                       "unattributed_displacement_past_every_d3d9_vtable": 1}},
        table=[table_row("IDirect3DDevice9", 17, "Present", True),
               table_row("IDirect3DDevice9", 23, "CreateTexture", True),
               table_row("IDirect3DDevice9", 25, "CreateCubeTexture", False),
               table_row("IDirect3DPixelShader9", 4, "GetFunction", True),
               table_row("IDirect3DVertexShader9", 4, "GetFunction", True)],
        dynamic=[{"interface": "IDirect3DDevice9", "method": "Present"}],
        d3dx9_rows=[{"name": "D3DXCreateTexture", "implemented": True},
                    {"name": "D3DXFilterTexture", "implemented": False}],
        registry={"reached": {"IDirect3DDevice9::CreateTexture": 2},
                  "kept_from_earlier_counts": {}},
        supplement={"rows": [{"interface": "IDirect3DDevice9", "method": "CreateCubeTexture"}]},
    )
    base.update(over)
    return M.Inputs(**base)


def test_the_denominator_is_every_source_with_its_provenance() -> None:
    den, num = M.derive(inputs())
    assert den["called"] == ["IDirect3DDevice9::CreateCubeTexture",
                             "IDirect3DDevice9::CreateTexture",
                             "IDirect3DDevice9::Present",
                             "IDirect3DPixelShader9::GetFunction",
                             "IDirect3DVertexShader9::GetFunction",
                             "d3dx9_42!D3DXCreateTexture",
                             "d3dx9_42!D3DXFilterTexture"]
    assert den["from_the_static_scan"] == ["IDirect3DDevice9::Present",
                                           "IDirect3DPixelShader9::GetFunction",
                                           "IDirect3DVertexShader9::GetFunction"]
    assert den["attributed_to_more_than_one_interface"] == [
        "IDirect3DPixelShader9::GetFunction", "IDirect3DVertexShader9::GetFunction"]
    # A run-only method is called and missed; a supplement-only one is called and not missed.
    assert den["called_by_the_run_but_missed_by_the_scan"] == ["IDirect3DDevice9::CreateTexture"]
    assert den["from_the_census_supplement"] == ["IDirect3DDevice9::CreateCubeTexture"]
    assert den["named_by_neither_the_device_table_nor_the_d3dx9_imports"] == []
    assert den["unattributed_that_could_still_be_d3d9"] == 3
    # The user's three-way numerator, from no evidence: written unmeasured, so G5's M7
    # criterion reads it unmet.
    assert den["three_way_numerator"] == {
        "ruling": "Documentation/decisions/2026-10-04-S5-user-rulings-2.md",
        "ruled": ("M7's numerator: a method counts only when the device answers it, the "
                  "recorder captures it, and both players handle it."),
        "measured": False, "not_held": None,
        "why_not_measured": "the evidence was not read (load_three_way)"}
    assert num == {"implemented": ["IDirect3DDevice9::CreateTexture", "IDirect3DDevice9::Present",
                                   "IDirect3DPixelShader9::GetFunction",
                                   "IDirect3DVertexShader9::GetFunction",
                                   "d3dx9_42!D3DXCreateTexture"],
                   "named_methods": 5, "refuses": 1}


def test_a_name_kept_from_a_retired_run_stays_in_the_denominator() -> None:
    den, _ = M.derive(inputs(registry={"reached": {}, "kept_from_earlier_counts": {
        "IDirect3DDevice9::CreateTexture": {"re_recorded": [], "retired": ["r"], "because": "x"}}}))
    assert "IDirect3DDevice9::CreateTexture" in den["called"]
    assert den["called_by_the_run_but_missed_by_the_scan"] == ["IDirect3DDevice9::CreateTexture"]


def test_a_d3dx9_entry_the_scan_sited_is_not_missed_and_one_it_did_not_is() -> None:
    den, _ = M.derive(inputs(registry={"reached": {"d3dx9_42!D3DXCreateTexture": 1,
                                                   "d3dx9_42!D3DXFilterTexture": 1},
                                       "kept_from_earlier_counts": {}}))
    assert den["called_by_the_run_but_missed_by_the_scan"] == ["d3dx9_42!D3DXFilterTexture"]


def test_a_hand_attribution_never_shrinks_what_the_scan_missed() -> None:
    den, _ = M.derive(inputs(supplement={"rows": [{"interface": "IDirect3DDevice9",
                                                   "method": "CreateTexture"}]}))
    assert den["from_the_census_supplement"] == ["IDirect3DDevice9::CreateTexture"]
    assert den["called_by_the_run_but_missed_by_the_scan"] == ["IDirect3DDevice9::CreateTexture"]


def test_a_base_texture_attribution_counts_under_each_texture_interface() -> None:
    scan = inputs().scan
    scan["layer_b_attributed"]["methods"] = [
        {"interface": "IDirect3DBaseTexture9", "method": "GetLevelCount", "slot": 13}]
    den, _ = M.derive(inputs(scan=scan))
    assert den["from_the_static_scan"] == ["IDirect3DCubeTexture9::GetLevelCount",
                                           "IDirect3DTexture9::GetLevelCount",
                                           "IDirect3DVolumeTexture9::GetLevelCount"]
    # None of the three is a row of this synthetic table: naming drift, which the gate fails.
    assert den["named_by_neither_the_device_table_nor_the_d3dx9_imports"] == [
        "IDirect3DCubeTexture9::GetLevelCount", "IDirect3DTexture9::GetLevelCount",
        "IDirect3DVolumeTexture9::GetLevelCount"]


# ------------------------------------------------------------------ recorded runs

def counts(methods=(), entries=(), schema=M.COUNTS_SCHEMA) -> dict:
    return {"schema": schema, "writer": M.RECORDER,
            "methods": [{"interface": i, "method": m, "calls": c} for i, m, c in methods],
            "entries": [{"name": n, "calls": c} for n, c in entries]}


def test_counts_are_the_methods_and_entries_called_without_direct3dcreate9() -> None:
    doc = counts([("IDirect3DDevice9", "Present", 3), ("IDirect3DDevice9", "Reset", 0)],
                 [("Direct3DCreate9", 1), ("D3DXFilterTexture", 2), ("D3DXCreateTexture", 0)])
    assert M.counts_methods(doc) == {"IDirect3DDevice9::Present", "d3dx9_42!D3DXFilterTexture"}


def make_run(root: Path, rel: str, doc: dict, guest: bool = True) -> None:
    d = root / rel
    d.mkdir(parents=True)
    (d / "counts.json").write_text(json.dumps(doc))
    run = {"winmain": {}, "d3d9": {"implemented": []}} if guest else {"d3d9": {}}
    (d / "run.json").write_text(json.dumps(run))


CENSUS = """STREAMS=${STREAMS:-boot mh}
declare -A STREAM=(
  [boot]=$REPO/private/generated/s5/a/stream.sa2s
  [mh]=$REPO/private/generated/s5/trace/mh.sa2s
)
"""


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path
    make_run(root, "private/generated/s5/a", counts([("IDirect3DDevice9", "Present", 1)]))
    (root / "private/generated/s5/a/stream.sa2s").write_bytes(b"x")
    mh = counts([("IDirect3DDevice9", "Present", 1), ("IDirect3DDevice9", "SetTexture", 1)])
    mh["stream_bytes"], mh["stream_records"] = 5, {"call": 3, "frame": 1}
    make_run(root, "private/generated/s5/b", mh)
    make_run(root, "private/generated/s5/c", counts([("IDirect3DTexture9", "LockRect", 1)]))
    make_run(root, "private/generated/s5/test_x/out", counts(), guest=False)
    (root / "private/generated/s5/trace").mkdir(parents=True)
    (root / "private/generated/s5/trace/mh.sa2s").write_bytes(b"12345")
    (root / "private/generated/s5/trace/mh.sa2s.status").write_text(
        "closed: 4 records, 5 bytes, 1 frames\n")
    (root / "host/d3d9gpu").mkdir(parents=True)
    (root / "host/d3d9gpu/state_census.sh").write_text(CENSUS)
    (root / "Documentation/receipts").mkdir(parents=True)
    (root / "Documentation/receipts/r.json").write_text(json.dumps(
        {"runs": ["private/generated/s5/c/counts.json", "private/generated/s5/test_x/out"]}))
    (root / M.REGISTRY).write_text(json.dumps({"run": "private/generated/s5/a"}))
    return root


def test_the_disk_census_finds_guest_runs_only(tree: Path) -> None:
    assert M.disk_runs(tree) == ["private/generated/s5/a", "private/generated/s5/b",
                                 "private/generated/s5/c"]


def test_the_disk_census_leaves_out_runs_inside_another_checkout(tree: Path) -> None:
    """A scratch clone or a falsification overlay under private/generated holds a `.git`
    entry; a run planted or copied inside it is that copy's, not this tree's (review probe:
    a planted run in an overlay was swept into the census)."""
    clone = tree / "private/generated/s5/wave1/unit/clone"
    make_run(clone, "private/generated/s5/planted",
             counts([("ID3DXConstantTable", "AddRef", 1)]))
    (clone / ".git").mkdir()
    overlay = tree / "private/generated/s5/wave1/probe/tmp/v"
    make_run(overlay, "private/generated/s5/planted",
             counts([("ID3DXConstantTable", "AddRef", 1)]))
    (overlay / ".git").symlink_to(tree / "private")
    assert M.disk_runs(tree) == ["private/generated/s5/a", "private/generated/s5/b",
                                 "private/generated/s5/c"]
    (clone / ".git").rmdir()
    assert "private/generated/s5/wave1/unit/clone/private/generated/s5/planted" in \
        M.disk_runs(tree)


def test_receipts_name_runs_by_their_counts_their_directory_or_a_file_beside(tree: Path) -> None:
    # The registry is this tool's own file and names nothing; a test fixture's directory is a
    # recorder run when a receipt names it, guest or not.
    assert M.receipt_named_runs(tree) == {
        "private/generated/s5/c": {"Documentation/receipts/r.json"},
        "private/generated/s5/test_x/out": {"Documentation/receipts/r.json"}}


def test_a_census_stream_maps_to_its_run_beside_it_or_by_size_and_records(tree: Path) -> None:
    assert M.census_streams(tree) == {"boot": "private/generated/s5/a/stream.sa2s",
                                      "mh": "private/generated/s5/trace/mh.sa2s"}
    runs = ["private/generated/s5/a", "private/generated/s5/b", "private/generated/s5/c"]
    assert M.stream_run(tree, "private/generated/s5/a/stream.sa2s", runs) == "private/generated/s5/a"
    assert M.stream_run(tree, "private/generated/s5/trace/mh.sa2s", runs) == "private/generated/s5/b"
    # A run with the same record count and another byte size is not the stream's.
    decoy = counts()
    decoy["stream_bytes"], decoy["stream_records"] = 99, {"call": 3, "frame": 1}
    make_run(tree, "private/generated/s5/d", decoy)
    assert M.stream_run(tree, "private/generated/s5/trace/mh.sa2s",
                        runs + ["private/generated/s5/d"]) == "private/generated/s5/b"
    (tree / "private/generated/s5/trace/mh.sa2s.status").write_text("closed: 9 records, 5 bytes")
    assert M.stream_run(tree, "private/generated/s5/trace/mh.sa2s", runs) is None


def test_the_registry_counts_every_run_and_keeps_a_deleted_runs_names(tree: Path) -> None:
    first = M.build_registry(tree, None, disk=M.disk_runs(tree), retire_missing=False)
    assert [(r["run"], r["methods"], r["sources"]) for r in first["runs"]] == [
        ("private/generated/s5/a", 1, ["disk", "census:boot"]),
        ("private/generated/s5/b", 2, ["disk", "census:mh"]),
        ("private/generated/s5/c", 1, ["disk", "receipt:Documentation/receipts/r.json"]),
        ("private/generated/s5/test_x/out", 0, ["receipt:Documentation/receipts/r.json"])]
    assert first["reached"] == {"IDirect3DDevice9::Present": 2,
                                "IDirect3DDevice9::SetTexture": 1,
                                "IDirect3DTexture9::LockRect": 1}
    (tree / "private/generated/s5/c/counts.json").unlink()
    (tree / "Documentation/receipts/r.json").unlink()
    with pytest.raises(M.Refused, match="--retire-missing"):
        M.build_registry(tree, first, disk=[], retire_missing=False)
    second = M.build_registry(tree, first, disk=[], retire_missing=True)
    assert [r["run"] for r in second["runs"]] == ["private/generated/s5/a",
                                                  "private/generated/s5/b",
                                                  "private/generated/s5/test_x/out"]
    assert second["runs"][0]["sources"] == ["census:boot"]
    assert second["retired"][0]["run"] == "private/generated/s5/c"
    assert second["kept_from_earlier_counts"] == {"IDirect3DTexture9::LockRect": {
        "re_recorded": [], "retired": ["private/generated/s5/c"],
        "because": second["kept_from_earlier_counts"]["IDirect3DTexture9::LockRect"]["because"]}}
    assert M.registry_names(second) == {"IDirect3DDevice9::Present",
                                        "IDirect3DDevice9::SetTexture",
                                        "IDirect3DTexture9::LockRect"}


def test_a_census_stream_with_no_run_stops_the_registry(tree: Path) -> None:
    (tree / "private/generated/s5/trace/mh.sa2s").write_bytes(b"123456")
    with pytest.raises(M.Refused, match="census stream mh"):
        M.build_registry(tree, None, disk=M.disk_runs(tree), retire_missing=False)


# ------------------------------------------------------------------ the census supplement

def supplement() -> dict:
    return json.loads((ROOT / M.SUPPLEMENT).read_text())


def layout() -> dict:
    return M.parse_device_rows((ROOT / M.D3D9_C).read_text())


def test_every_census_row_decodes_in_the_pinned_executable() -> None:
    """Fails, not skips, without the executable: the gate does the same."""
    exe = ROOT / "private" / "exe" / M.EXE_PIN
    assert exe.is_file(), f"{exe} is absent"
    pin = json.loads((ROOT / M.PINS).read_text())["executables"][M.EXE_PIN]["sha256"]
    assert hashlib.sha256(exe.read_bytes()).hexdigest() == pin
    doc = supplement()
    assert len(doc["rows"]) == 18
    assert G.decode_supplement(ROOT, doc, layout()) == []


def test_a_census_row_one_slot_off_or_outside_the_module_is_refused() -> None:
    doc = supplement()
    row = next(r for r in doc["rows"] if r["method"] == "SetMatrix")
    row["displacement"] = "0x58"
    assert G.decode_supplement(ROOT, {**doc, "rows": [row]}, layout()) == [
        "census supplement row 0x00867280 (ID3DXConstantTable::SetMatrix): displacement 0x58 "
        "is not 4 x slot 21",
        "census supplement row 0x00867280 (ID3DXConstantTable::SetMatrix): the load reads "
        "displacement 0x54, not 0x58"]
    doc = supplement()
    row = dict(next(r for r in doc["rows"] if r["method"] == "SetMatrix"),
               va="0x00401010", load_va="0x00401000")
    assert G.decode_supplement(ROOT, {**doc, "rows": [row]}, layout()) == [
        "census supplement row 0x00401010 (ID3DXConstantTable::SetMatrix): the site and its load "
        "are not inside the renderer module 0x00865BA0..0x0086A000 in that order"]


def test_the_supplement_cannot_widen_the_module_its_sites_are_held_to() -> None:
    """The bounds are the gate's, not the supplement's: widening them in the file is a
    problem, and the rows are still held to the gate's module."""
    doc = supplement()
    doc["renderer_module"] = {"start": "0x00401000", "end": "0x00900000"}
    row = dict(doc["rows"][0], va="0x00401010", load_va="0x00401000")
    assert G.decode_supplement(ROOT, {**doc, "rows": [row]}, layout()) == [
        "the census supplement declares the renderer module as 0x00401000..0x00900000; this "
        "gate holds its sites to 0x00865BA0..0x0086A000, and the supplement cannot move its own "
        "bounds",
        "census supplement row 0x00401010 (IDirect3DDevice9::SetVertexShaderConstantI): the site "
        "and its load are not inside the renderer module 0x00865BA0..0x0086A000 in that order"]


def test_a_census_row_whose_site_is_not_the_loaded_register_is_refused() -> None:
    doc = supplement()
    row = dict(doc["rows"][0], shape="call")       # a tail jump read as a call
    assert G.decode_supplement(ROOT, {**doc, "rows": [row]}, layout()) == [
        "census supplement row 0x00866F34 (IDirect3DDevice9::SetVertexShaderConstantI): the "
        "site is not `call r` through the loaded register"]


def test_a_census_row_whose_register_is_overwritten_before_the_site_is_refused() -> None:
    doc = supplement()
    row = dict(doc["rows"][0], load_va="0x00866F21")   # mov ecx,[ecx+38h], three before
    found = G.decode_supplement(ROOT, {**doc, "rows": [row]}, layout())
    assert ("census supplement row 0x00866F34 (IDirect3DDevice9::SetVertexShaderConstantI): "
            "0x866f2a writes the loaded register before the site") in found


# ------------------------------------------------------------------ the gate's place

def test_the_gate_runs_on_every_push_after_run_reach() -> None:
    names = [name for name, _ in run_all_gates.GATES]
    assert ("m7 inputs", ("check_m7_inputs.py",)) in run_all_gates.GATES
    assert names.index("m7 inputs") == names.index("run reach") + 1


def test_the_overlay_changes_only_what_it_is_told(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "a" / "b").mkdir(parents=True)
    (src / "a" / "b" / "f.txt").write_text("old")
    (src / "a" / "g.txt").write_text("keep")
    (src / "h.txt").write_text("drop")
    view = G.overlay(src, tmp_path / "view", {"a/b/f.txt": b"new", "h.txt": None,
                                              "a/b/n.txt": b"added"})
    assert (view / "a" / "b" / "f.txt").read_text() == "new"
    assert (view / "a" / "b" / "n.txt").read_text() == "added"
    assert (view / "a" / "g.txt").is_symlink() and (view / "a" / "g.txt").read_text() == "keep"
    assert not (view / "h.txt").exists()
    assert (src / "a" / "b" / "f.txt").read_text() == "old"


@pytest.mark.parametrize("rename", [False, True])
def test_the_current_layout_is_checked_without_the_scan_source_history(
        monkeypatch, tmp_path: Path, rename: bool) -> None:
    monkeypatch.setattr(M, "text_at_sha", lambda *args: None)
    source = (ROOT / M.D3D9_C).read_text()
    if rename:
        assert source.count('"SetRenderState"') == 1
        source = source.replace('"SetRenderState"', '"SetRenderStatus"', 1)
    view = G.overlay(ROOT, tmp_path / "view", {M.D3D9_C: source.encode()})
    found = G.problems(view, head={}, disk={})
    assert any("which neither the tree nor any commit holds" in p for p in found)
    assert any("inputs.scan.layout_digest (the current layout)" in p
               for p in found) is rename


def built_falsifications() -> list[str]:
    """FALSIFICATIONS, with the three-way numerator's cases where the tree's receipt builds
    them: the first once it records the evidence it was regenerated from, the rest once that
    evidence reads measured (check_m7_inputs.three_way_cases)."""
    receipt = json.loads((ROOT / M.RECEIPT).read_text())
    names = list(FALSIFICATIONS)
    at = names.index("the three-way numerator was set measured by hand") + 1
    extra = []
    if isinstance((receipt.get("inputs") or {}).get("three_way"), dict):
        extra.append("an evidence receipt changed after the regeneration")
        ev = M.load_three_way(ROOT) if (receipt.get("three_way_numerator") or {}).get("measured") is True else None
        if ev is not None and not (ev["production"]["problems"] or ev["synthetic"]["problems"] or M.stale_pins(ev)):
            extra += THREE_WAY_FALSIFICATIONS
    names = names[:at] + extra + names[at:]
    # The 2026-10-09 ruling's cases and the census's control by the probe, every one built
    # whatever the tree holds, so none can drop out of the count unseen.
    at = names.index("a guest run on disk reached a name M7 does not count") + 1
    return names[:at] + RULING_FALSIFICATIONS + names[at:] + list(G.DISCOVERY_CASES)


RULING_FALSIFICATIONS = [
    "a guest run record without the recorder reached a name M7 does not count",
    "a guest run record the census cannot read",
    "HEAD's census of every guest run counted a name the tree's does not",
    "the scan's ceiling grew against HEAD and no record names the cause",
    "a growth record names other figures than the growth against HEAD",
    "the misses grew against HEAD and no record names the cause",
    "the scan lists one ceiling site fewer than its ceiling",
    "the tracked record of the ceiling-site list was edited",
    "the reach run's record gained a key before \"va\", which the census does not recognise",
    "the probe's writer begins its run record with another first line",
    "the probe's device table no longer writes each method's calls",
]


THREE_WAY_FALSIFICATIONS = [
    "a host's report lost the row of a method the recording holds",
    "a synthetic omit run passes the predicate its positive run passes",
    "a synthetic case directory holds a counts.json",
    "a pin of the production evidence changed",
    "the synthetic evidence dropped the pin of a source the fixture was built from",
    "a source both players were built from moved",
    "a source the synthetic fixture was built from moved",
]


def test_the_gate_reports_every_falsification_a_blind_check_lets_through(monkeypatch) -> None:
    """A gate whose problems() saw nothing must report each of its cases as survived: the
    falsifications are counted from the cases built and none of them is decoration."""
    monkeypatch.setattr(G, "problems", lambda root=None, **kw: [])
    survived = G.falsifications()
    expected = built_falsifications()
    assert G.CASES == expected
    # The census's own cases do not run through problems(), so a blind problems() leaves them be.
    assert G.RAN == len(expected) == len(survived) + len(G.DISCOVERY_CASES)
    assert all(s.startswith("falsification survived: ") for s in survived)
    # A case is caught only by its own message: a gate that fails every case for one unrelated
    # reason has shown none of them.
    monkeypatch.setattr(G, "problems", lambda root=None, **kw: ["an unrelated problem"])
    assert len(G.falsifications()) == G.RAN - len(G.DISCOVERY_CASES)


def test_a_blind_census_lets_every_discovery_falsification_through(monkeypatch) -> None:
    """A census whose update finds nothing must report each of the census's own cases as
    survived; one that finds everything, none."""
    real = M.update_index
    monkeypatch.setattr(M, "update_index", lambda root, **kw: (
        real(root, **kw), M.Census([], {}, {}, {}, {}))[1])
    assert G.discovery_falsifications() == list(G.DISCOVERY_CASES)
    monkeypatch.setattr(M, "update_index", real)
    assert G.discovery_falsifications() == []


# Every case the gate builds on this tree, by name. Some are built only when the tree holds what
# they mutate (a census run for menu, a refused device row, a receipt-named run); a case that
# stops being built fails here instead of vanishing from the count.
FALSIFICATIONS = [
    "a pipeline tool changed (tools/s4/m7_inputs.py)",
    "a pipeline tool changed (tools/s4/device_status.py)",
    "a pipeline tool changed (tools/s4/d3d9_methods.py)",
    "the scan's sha256 changed",
    "the scan's exe_sha256 changed",
    "the scan's function_map_sha256 changed",
    "the scan's tool_sha256 changed",
    "the scan's layout_digest changed",
    "the scan's layout_read_from_sha256 changed",
    "the device status changed",
    "the reach run file changed",
    "the runtime moved and the numerator did not",
    "runtime/sa2_d3d9.c moved and the numerator did not",
    "the d3dx9 table changed",
    "the registry changed",
    "the census supplement changed",
    "the three-way numerator was set measured by hand",
    "the scan read another executable",
    "the scan read another function map",
    "the scan read another runtime layout",
    "the device status records another run's runtime",
    "the device status was written from another copy of the run",
    "the device status was written from another run",
    "a name was dropped from the device status's dynamic surface",
    "the reach run was measured on another runtime",
    "a registered run's method count was edited",
    "a name was removed from called",
    "a registered run was re-recorded",
    "the registry's union lost a name",
    "a registered run's counts were deleted",
    "the registry lost its kept map",
    "a census stream's run was dropped from the registry",
    "a receipt-named run was dropped from the registry",
    "a receipt names a run M7 does not count",
    "a receipt names a run M7 does not count inside a |-joined string",
    "a census stream written quoted, its run dropped from the registry",
    "a census stream written in a form the parser cannot read",
    "the census table leaves out a stream its default replays",
    "HEAD registered a run the tree neither registers nor retires",
    "HEAD's denominator held a name the tree's does not",
    "HEAD's supplement held a row the scan does not attribute, and the tree dropped it",
    "a guest run on disk reached a name M7 does not count",
    "a refused method was implemented since, and every digest refreshed",
    "a d3dx9 entry lost its handler, and every digest refreshed",
    "a slot was renamed in the layout",
    "reach.run_json points elsewhere",
    "a name was added to implemented",
    "a census row names a method the device table does not",
    "a census row's displacement is one slot off",
    "a census row's register is overwritten before the site",
    "a census row lies outside the renderer module",
    "the census supplement widened its renderer module around a row outside it",
]


# ------------------------------------------------------------------ the fold's additions

def test_a_rerecorded_run_that_reaches_fewer_methods_keeps_their_names(tree: Path) -> None:
    """Review finding (BLOCKER): a run re-recorded in place with fewer methods dropped them
    from the registry, and the gate's own fix command wrote the shrink. Every name the registry
    counted stays counted, with the runs that changed."""
    first = M.build_registry(tree, None, disk=M.disk_runs(tree), retire_missing=False)
    make = tree / "private/generated/s5/b/counts.json"
    mh = json.loads(make.read_text())
    mh["methods"] = [m for m in mh["methods"] if m["method"] != "SetTexture"]
    make.write_text(json.dumps(mh))
    second = M.build_registry(tree, first, disk=M.disk_runs(tree), retire_missing=False)
    assert "IDirect3DDevice9::SetTexture" not in second["reached"]
    assert second["kept_from_earlier_counts"] == {"IDirect3DDevice9::SetTexture": {
        "re_recorded": ["private/generated/s5/b"], "retired": [],
        "because": second["kept_from_earlier_counts"]["IDirect3DDevice9::SetTexture"]["because"]}}
    assert M.registry_names(second) == M.registry_names(first)
    # Recorded again with the method, the name is reached again and no longer kept.
    make.write_text(json.dumps(json.loads(make.read_text()) | {"methods": mh["methods"] + [
        {"interface": "IDirect3DDevice9", "method": "SetTexture", "calls": 1}]}))
    third = M.build_registry(tree, second, disk=M.disk_runs(tree), retire_missing=False)
    assert third["kept_from_earlier_counts"] == {}
    assert third["reached"]["IDirect3DDevice9::SetTexture"] == 1


def test_a_name_the_registry_counted_that_no_change_explains_stops_it(tree: Path) -> None:
    first = M.build_registry(tree, None, disk=M.disk_runs(tree), retire_missing=False)
    edited = dict(first, reached={**first["reached"], "IDirect3DDevice9::Reset": 1})
    with pytest.raises(M.Refused, match="IDirect3DDevice9::Reset as reached, and no registered run"):
        M.build_registry(tree, edited, disk=M.disk_runs(tree), retire_missing=False)


def test_a_path_is_found_wherever_a_receipt_joins_it() -> None:
    """Review finding: a run named inside a `|`- or `=`-joined string was not discovered."""
    text = json.dumps({"env": "SA2_LIFTED=private/generated/a/x.json|SA2_REC_COUNTS="
                              "private/generated/s5/run/counts.json",
                       "q": "see \"private/generated/s5/q\"",
                       "nested": "private/generated/s5/wave1/c/private/generated/s5/r/run.json"})
    assert M.cited_paths(text) == {
        "private/generated/a/x.json", "private/generated/s5/run/counts.json",
        "private/generated/s5/q",
        "private/generated/s5/wave1/c/private/generated/s5/r/run.json",
        "private/generated/s5/r/run.json"}


def test_the_census_table_is_read_whole_or_refused(tree: Path) -> None:
    """Review finding: a stream written quoted (valid bash) fell out of the parse silently."""
    script = tree / M.CENSUS_SCRIPT
    script.write_text(CENSUS.replace("[mh]=$REPO/private/generated/s5/trace/mh.sa2s",
                                     '[mh]="$REPO/private/generated/s5/trace/mh.sa2s"'))
    assert M.census_streams(tree) == {"boot": "private/generated/s5/a/stream.sa2s",
                                      "mh": "private/generated/s5/trace/mh.sa2s"}
    script.write_text(CENSUS.replace("[mh]=$REPO/", "[mh]=${MH:-$REPO/"))
    with pytest.raises(M.Refused, match="cannot read"):
        M.census_streams(tree)
    script.write_text(CENSUS.replace("boot mh}", "boot menu mh}"))
    with pytest.raises(M.Refused, match="would be left out"):
        M.census_streams(tree)


def test_a_stream_whose_frames_differ_is_not_the_runs(tree: Path) -> None:
    (tree / "private/generated/s5/trace/mh.sa2s.status").write_text(
        "closed: 4 records, 5 bytes, 2 frames\n")
    assert M.stream_run(tree, "private/generated/s5/trace/mh.sa2s",
                        ["private/generated/s5/b"]) is None


def test_the_tree_may_only_grow_from_head() -> None:
    """Review finding: "it only grows" was asserted and not enforced; a regeneration accepted a
    hand-shrunk registry or supplement as its previous state."""
    receipt = {"called": ["A::a", "B::b"], "from_the_static_scan": ["A::a"]}
    registry = {"runs": [{"run": "r1"}], "retired": [{"run": "r2"}]}
    supplement = {"rows": [{"va": "0x1", "interface": "A", "method": "a"}]}
    head = {M.RECEIPT: {"called": ["A::a", "B::b", "C::c"]},
            M.REGISTRY: {"runs": [{"run": "r1"}, {"run": "r2"}, {"run": "r3"}]},
            M.SUPPLEMENT: {"rows": [{"va": "0x1", "interface": "A", "method": "a"},
                                    {"va": "0x2", "interface": "A", "method": "a"},
                                    {"va": "0x3", "interface": "C", "method": "c"}]}}
    assert G.grows_from_head(head, receipt, registry, supplement) == [
        f"the registry at HEAD counts the run r3, which is neither registered nor retired now. "
        f"A run leaves the registry only through {M.COMMAND} --retire-missing, which keeps every "
        f"name only it reached",
        "M7's denominator at HEAD counts C::c, and the tree's does not. A name leaves M7's "
        "denominator only by the user's ruling (AGENTS.md rule 10); restore it",
        "the census supplement at HEAD holds the row 0x3 (C::c), and the tree's does not while "
        "the scan does not attribute that method. A row is retired only when the scan "
        "attributes it; restore it"]
    # The row at 0x2 left with its method attributed by the scan: the sunset, allowed.
    assert G.grows_from_head({k: None for k in head}, receipt, registry, supplement) == []


def test_the_device_status_records_the_runtime_its_run_was_measured_on(tmp_path: Path) -> None:
    """Review finding: the status recorded the tree's digest at write time, so a status
    written after the runtime moved, from an older run, read as current."""
    import device_status
    run = {"winmain": {}, "d3d9": {"implemented": [
        {"interface": "IDirect3DDevice9", "slot": 17, "method": "Present", "argc": 4,
         "flags": "", "implemented": True}], "dynamic_surface": []}}
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/sa2_d3d9.c").write_text("x")
    (tmp_path / "r").mkdir()
    (tmp_path / "r/reach.a.json").write_text(json.dumps(run))
    (tmp_path / "r/other.json").write_text(json.dumps(run))
    (tmp_path / "Documentation/receipts").mkdir(parents=True)
    (tmp_path / M.REACH).write_text(json.dumps({"reach": {"run_json": "r/reach.a.json"},
                                                "manifest": {"runtime_sha256": "ab" * 32}}))
    doc = device_status.build(tmp_path / "r/reach.a.json", tmp_path)
    assert (doc["inputs"]["run_runtime_sha256"], doc["inputs"]["run_runtime_from"]) == (
        "ab" * 32, "Documentation/receipts/s4-run-reach-v1.json manifest.runtime_sha256")
    assert doc["inputs"]["probe_run"]["path"] == "r/reach.a.json"
    other = device_status.build(tmp_path / "r/other.json", tmp_path)
    assert (other["inputs"]["run_runtime_sha256"], other["inputs"]["run_runtime_from"]) == \
        (None, None)


def test_the_tool_refuses_a_reach_run_measured_on_another_runtime(tmp_path: Path) -> None:
    """Review finding: after a runtime change the tool wrote a receipt the gate then failed with
    the same command, in a loop. It now refuses, and names the reach re-run first."""
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/sa2_d3d9.c").write_text("x")
    (tmp_path / "Documentation/receipts").mkdir(parents=True)
    (tmp_path / M.REACH).write_text(json.dumps({
        "reach": {"run_json": "r/reach.a.json",
                  "import_table": {"path": "r/t.json", "sha256": "cd" * 32}},
        "manifest": {"runtime_sha256": "ab" * 32}}))
    with pytest.raises(M.Refused, match="Re-run the reach first: " + "source tools/env.sh"):
        M.regenerate(tmp_path, ran_tool_sha=None, retire_missing=False)


# ------------------------------------------------------------------ the three-way numerator
#
# The user's numerator (Documentation/decisions/2026-10-04-S5-user-rulings-2.md): "a method
# counts only when the device answers it, the recorder captures it, and both players handle
# it." The production evidence below is authored: a recording that counted two methods, both
# hosts' clean replays of its stream, the second host's draw account and census, and the two
# players' build records, under a receipt written by the tool's own --production writer.

S = "5a" * 32                                   # the stream both hosts replayed
DRAWS = {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}}


def write(root: Path, rel: str, data) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else json.dumps(data, indent=1).encode())
    return hashlib.sha256(p.read_bytes()).hexdigest()


def host_report(root: Path, host: str, binary: str, rows: list[dict]) -> dict:
    return {"complete": True, "pass": True, "exit_status": 0, "stage": 3, "options": {"stats_only": False},
            "stream_records_read_to_end": True, "records": {"end": 1}, "stop": "end of stream", "errors": 0,
            "calls": {"handled": 4, "ignored_by_design": 0, "skipped_by_stage": 0, "not_handled": 0},
            "stream_sha256": S, "host_identity": {"d3d9_host": host}, "presents": 2, "methods": rows,
            "allowed_host_failures": {"rules": {}, "calls": []}, "host_failures": {"count": 0, "failed_like_guest": 0},
            "present_failures": 0,
            "files": {"player": {"real_path": str(root / "build" / binary), "sha256": "b" + host[0] * 63}}}


def production_tree(root: Path, *, mutate=None) -> Path:
    """The authored production evidence under `root`, with its receipt at M.PRODUCTION.
    `mutate(docs)` edits the documents before they are written."""
    checker = write(root, M.PLAY_REPORT, b"the checker\n")
    rec_sha = write(root, M.RECORDER, b"the recorder\n")
    fmt_sha = write(root, "host/sa2_stream.h", b"the format\n")
    player_sha = write(root, "host/player/player.cpp", b"the player\n")
    stream_sha = write(root, "host/player/stream.cpp", b"the stream reader\n")
    d9g_sha = write(root, "host/d3d9gpu/d9g_device.cpp", b"the second host\n")
    docs = {
        "counts": {"schema": M.COUNTS_SCHEMA, "writer": M.RECORDER, "counting": True, "stream": "closed",
                   "stream_records": {"note": 0}, "notes": {"lock": 0},
                   "methods": [{"type": 1, "slot": 17, "interface": "IDirect3DDevice9", "method": "Present",
                                "calls": 2, "entered": 2},
                               {"type": 1, "slot": 41, "interface": "IDirect3DDevice9", "method": "BeginScene",
                                "calls": 2, "entered": 2}],
                   "entries": [{"entry": 12, "name": "Direct3DCreate9", "calls": 1, "entered": 1}]},
        "present": {"ok": True, "checks": {"counts": {"ok": True},
                                           "stream": {"ok": True, "problems": [], "counts_mismatch": [], "end": True}},
                    "ready": {"stream_sha256": S},
                    "current": {"recorder_sha256": rec_sha, "stream_h_sha256": fmt_sha, "runtime_sha256": "r" * 64}},
        "census": {"stream_sha256": S, "stage": 3, "frames": 2,
                   "draws": {"total": 1, "skipped_no_shader_route": DRAWS, "refused": {}, "empty": {}}},
        "device": {"complete": True, "failure": None, "not_implemented": {"count": 0},
                   "library": {"sha256": "bs" + "s" * 62},
                   "draws": {"drawn": 1, "total": 1, "fault_uncounted": 0, "accepted": 1, "unmapped_count": 0,
                             "backend_failed_count": 0, "suppressed_count": 0, "encoded": DRAWS, "unmapped": {},
                             "backend_failed": {}, "fault_suppressed": {}, "refused": {}, "empty": {},
                             "failures": [], "per_frame": [[1, 2, 1]], "after_last_present": 0}},
        "check": {"pass": True, "stream_sha256": S, "stage": 3, "presents": 2},
        "exit": b"0\n",
        "checked_by": checker.encode() + b"\n",
        "records": {"sa2_player.json": {"binaries": {"sa2_player": "bd" + "d" * 62},
                                        "sources": {"host/player/player.cpp": player_sha,
                                                    "host/player/stream.cpp": stream_sha}},
                    "sa2_player_gpu.json": {"binaries": {"sa2_player_gpu": "bs" + "s" * 62},
                                            "sources": {"host/player/player.cpp": player_sha,
                                                        "host/player/stream.cpp": stream_sha,
                                                        "host/d3d9gpu/d9g_device.cpp": d9g_sha}}},
    }
    rows = [{"type": "Device", "method": "Present", "slot": 17, "handled": 2},
            {"type": "Device", "method": "BeginScene", "slot": 41, "handled": 2}]
    docs["reports"] = {h: host_report(root, h, M.PLAYER_BINARIES[h], json.loads(json.dumps(rows))) for h in M.HOSTS}
    if mutate:
        mutate(docs)
    write(root, "rec/counts.json", docs["counts"])
    write(root, "rec/present-run.json", docs["present"])
    write(root, "census.json", docs["census"])
    for name, doc in docs["records"].items():
        write(root, f"build/{name}", doc)
    for h in M.HOSTS:
        write(root, f"{h}/report.json", docs["reports"][h])
        write(root, f"{h}/check.json", docs["check"])
        write(root, f"{h}/exit", docs["exit"])
        write(root, f"{h}/{M.CHECKED_BY}", docs["checked_by"])
    write(root, "sdlgpu/d3d9gpu.json", docs["device"])
    for t in ("tools/s4/m7_inputs.py", "tools/s5/compare_hosts.py"):
        write(root, t, b"a tool\n")
    receipt = M.production_receipt(root, "rec", "dxvk", "sdlgpu", "census.json")
    write(root, M.PRODUCTION, receipt)
    return root


def three_way(root: Path, monkeypatch, called=("IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present",
                                                "IDirect3DDevice9::Reset"), registry: dict | None = None) -> dict:
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: "r" * 64)
    inp = inputs(**({"registry": registry} if registry is not None else {}))
    inp.three_way = M.load_three_way(root)
    return M.three_way_numerator(inp, set(called), set(called))


def synthetic_receipt(root: Path, cases: list | None = None, *, mutate=None) -> dict:
    """A synthetic receipt (no case unless given): present and whole, its fixture's sources and
    tools pinned as the tree holds them. `mutate(doc)` edits it before it is written."""
    def pinned(s: str, what: str) -> str:      # the tree's file, written when it has none
        return hashlib.sha256((root / s).read_bytes()).hexdigest() if (root / s).is_file() \
            else write(root, s, f"{what} {s}\n".encode())
    sources = {s: pinned(s, "fixture source") for s in M.SYNTHETIC_SOURCES}
    tools = {s: pinned(s, "generator") for s in M.SYNTHETIC_GENERATORS}
    doc = {"schema": M.SYNTHETIC_SCHEMA, "generated_by": M.SYNTHETIC_WRITER,
           "harness": {"binary": {"path": "fixture", "sha256": write(root, "fixture", b"f")},
                       "sources": sources, "tools": tools},
           "players": {"records": {n: {"path": f"build/{n}",
                                       "sha256": hashlib.sha256((root / "build" / n).read_bytes()).hexdigest()}
                                   for n in M.PLAYER_RECORDS.values()}},
           "cases": cases or []}
    if mutate:
        mutate(doc)
    write(root, M.SYNTHETIC, doc)
    return doc


def test_a_clean_recording_and_both_clean_replays_hold_every_method_the_recorder_counted(tmp_path, monkeypatch):
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["measured"] is True
    assert block["held_methods"] == ["IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"]
    assert block["not_held"] == ["IDirect3DDevice9::Reset"]
    assert block["why_not_held"] == {"IDirect3DDevice9::Reset": "no recorded run reaches it and no synthetic case "
                                                                 "exercises it"}
    assert block["held_by"] == {"production": ["IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"],
                                "synthetic": []}


def _set(path: str, value):
    def f(docs):
        d = docs
        keys = path.split(".")
        for k in keys[:-1]:
            d = d[int(k)] if isinstance(d, list) else d[k]
        d[int(keys[-1]) if isinstance(d, list) else keys[-1]] = value
    return f


@pytest.mark.parametrize("mutate, held, why", [
    # the run as a whole: every method it counted falls with it
    (_set("exit", b"1\n"), [], "dxvk: the player exited 1"),
    (_set("reports.dxvk.stage", 2), [], "dxvk: stage is 2, not 3"),
    (_set("reports.sdlgpu.options.stats_only", True), [], "sdlgpu: options.stats_only is True"),
    (_set("reports.dxvk.stream_records_read_to_end", False), [], "stream_records_read_to_end is False"),
    (_set("reports.dxvk.stream_sha256", "0" * 64), [], "dxvk: stream_sha256 is"),
    (_set("reports.sdlgpu.calls.not_handled", 1), [], "sdlgpu: calls.not_handled is 1"),
    (_set("check.pass", False), [], "tools/s5/play_report.py check"),
    (_set("device.not_implemented.count", 1), [], "the device's report"),
    (_set("device.draws.unmapped_count", 1), [], "draw account"),
    (_set("census.frames", 3), [], "sdlgpu: the census's frames is 3, not 2"),
    (_set("counts.notes.lock", 1), [], "the recorder noted"),
    (_set("present.checks.stream.ok", False), [], "present-run.json checks.stream.ok is False"),
    # one method: only it falls
    (_set("counts.methods.0.entered", 1), ["IDirect3DDevice9::BeginScene"], "counted 2 calls of IDirect3DDevice9::Present and 1 entered"),
    (_set("reports.dxvk.methods.0.handled", 1), ["IDirect3DDevice9::BeginScene"], "counts 1 calls by category, the recorder 2"),
    (_set("reports.sdlgpu.methods.0", {"type": "Device", "method": "Present", "slot": 17, "handled": 1,
                                       "skipped_by_stage": 1}), ["IDirect3DDevice9::BeginScene"], "skipped_by_stage 1"),
    (lambda docs: (docs["reports"]["dxvk"]["methods"][0].update(failed=1),
                   docs["reports"]["dxvk"]["host_failures"].update(count=1)),
     ["IDirect3DDevice9::BeginScene"], "outside the scene-order allowance"),
])
def test_each_clause_of_the_predicate_can_fail(tmp_path, monkeypatch, mutate, held, why):
    root = production_tree(tmp_path, mutate=mutate)
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["measured"] is True
    assert block["held_methods"] == held
    fallen = sorted({"IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"} - set(held))
    assert all(why in block["why_not_held"][n] for n in fallen), block["why_not_held"]


def test_a_scene_order_failure_counts_as_handled_only_under_its_own_rule(tmp_path, monkeypatch):
    """play_report.py's allowance: one BeginScene inside a scene, its rule's count all of the
    method's failures (the Metal Harbor reports read so). The same failure on Present is not."""
    call = "seq 9 Device::BeginScene: host 0x8876086c, guest 0x00000000 (scene order: BeginScene inside a scene)"

    def allowed(docs):
        for h in M.HOSTS:
            r = docs["reports"][h]
            r["methods"][1].update(failed=1, hr_differs_from_guest=1)
            r["allowed_host_failures"] = {"rules": {"scene order: BeginScene inside a scene": 1}, "calls": [call]}
    root = production_tree(tmp_path, mutate=allowed)
    synthetic_receipt(root)
    assert three_way(root, monkeypatch)["held_methods"] == ["IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"]

    def two(docs):
        allowed(docs)
        docs["reports"]["dxvk"]["methods"][1].update(failed=2, hr_differs_from_guest=2)
        docs["reports"]["dxvk"]["host_failures"]["count"] = 1
    root = production_tree(tmp_path / "two", mutate=two)
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert "IDirect3DDevice9::BeginScene" not in block["held_methods"]


def test_evidence_that_is_absent_changed_or_stale_reads_not_measured_and_says_why(tmp_path, monkeypatch):
    root = production_tree(tmp_path)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False and block["not_held"] is None
    assert block["why_not_measured"] == (f"synthetic: {M.SYNTHETIC} is absent (written by {M.SYNTHETIC_WRITER})")
    synthetic_receipt(root)
    # a pinned report rewritten after the receipt was written
    report = json.loads((root / "dxvk/report.json").read_text())
    report["presents"] = 3
    write(root, "dxvk/report.json", report)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False
    assert block["why_not_measured"] == "production: dxvk/report.json is absent or not the file the receipt pins"
    # a source the players were built from, moved in the tree
    root = production_tree(tmp_path / "stale")
    synthetic_receipt(root)
    write(root, "host/player/player.cpp", b"the player, edited\n")
    block = three_way(root, monkeypatch)
    assert block["measured"] is False
    assert block["why_not_measured"].endswith(f"make it again ({M.REFRESH}) and regenerate (production: "
                                              "host/player/player.cpp, synthetic: host/player/player.cpp)")
    # the checks were written by a play_report.py the tree does not hold
    root = production_tree(tmp_path / "checker", mutate=_set("checked_by", b"another checker\n"))
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False
    assert block["why_not_measured"].endswith("(production: tools/s5/play_report.py)")
    # the runtime the recording names is not the tree's
    root = production_tree(tmp_path / "runtime")
    synthetic_receipt(root)
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: "q" * 64)
    inp = inputs()
    inp.three_way = M.load_three_way(root)
    assert M.three_way_numerator(inp, {"IDirect3DDevice9::Present"}, {"IDirect3DDevice9::Present"})[
        "why_not_measured"].endswith("(production: the runtime digest)")


def test_a_method_the_device_does_not_answer_is_never_held(tmp_path, monkeypatch):
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: "r" * 64)
    inp = inputs()
    inp.three_way = M.load_three_way(root)
    called = {"IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"}
    block = M.three_way_numerator(inp, called, {"IDirect3DDevice9::BeginScene"})
    assert block["held_methods"] == ["IDirect3DDevice9::BeginScene"]
    assert block["why_not_held"] == {"IDirect3DDevice9::Present": "the recording device does not answer it"}


def test_the_measured_block_has_one_measured_and_one_not_held_key(tmp_path, monkeypatch):
    """G5's M7 criterion substitutes `"measured":` and `"not_held":` in the receipt, each of
    which must occur exactly once (check_criterion_clauses.py)."""
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    text = json.dumps({"three_way_numerator": three_way(root, monkeypatch)}, indent=1, sort_keys=True)
    assert text.count('"measured":') == 1 and text.count('"not_held":') == 1
    assert text.count('"three_way_numerator":') == 1


# ------------------------------------------------------------------ the three-way numerator: folds

def synthetic_case(root: Path, target: str, slot: int, method: str, *, positive_rows=None, omit_rows=None,
                   answers=None) -> dict:
    """One authored synthetic case of `target` (a Device method at `slot`) under root/syn: a positive
    run whose recorder and both hosts count one call, an omit run that holds none and fails both
    hosts' pixels, every file pinned as m7_synthetic.py pins it. Returns the receipt's entry. Its
    stream is no real one: a test using it stubs the read-back (m7_inputs.cached_scan), which
    test_m7_synthetic.py holds on the real fixture's streams."""
    entry = {"target": target, "family": "authored", "prepared": False}
    for mode in ("positive", "omit"):
        d = f"syn/{mode}"
        calls = 1 if mode == "positive" else 0
        stream = write(root, f"{d}/stream.sa2s", f"stream {mode}".encode())
        counts = {"schema": M.COUNTS_SCHEMA, "writer": M.RECORDER, "counting": True, "stream": "closed",
                  "stream_records": {"note": 0}, "notes": {},
                  "methods": [{"type": 1, "slot": slot, "interface": "IDirect3DDevice9", "method": method,
                               "calls": calls, "entered": calls}], "entries": []}
        files = {"stream.sa2s": stream,
                 "rec.counts.json": write(root, f"{d}/rec.counts.json", counts),
                 "answers.json": write(root, f"{d}/answers.json", (answers or {}).get(mode) or
                                       {"target": target, "mode": mode, "variant": ""}),
                 "expect.tsv": write(root, f"{d}/expect.tsv", b"frame\n"),
                 "census.json": write(root, f"{d}/census.json",
                                      {"stream_sha256": stream, "stage": 3, "frames": 2,
                                       "draws": {"total": 1, "skipped_no_shader_route": DRAWS, "refused": {},
                                                 "empty": {}}}),
                 "harness.exit": write(root, f"{d}/harness.exit", b"0\n")}
        hosts = {}
        for h in M.HOSTS:
            rows = (positive_rows if mode == "positive" else omit_rows) or \
                ([{"type": "Device", "method": method, "slot": slot, "handled": 1}] if calls else [])
            rep = host_report(root, h, M.PLAYER_BINARIES[h], json.loads(json.dumps(rows)))
            rep["stream_sha256"] = stream
            rep["expectations"] = {"file": "expect.tsv", "selected": 1, "pass": calls, "fail": 1 - calls}
            if not calls:
                rep.update({"pass": False, "exit_status": 1})
            hd = f"{d}/{h}"
            hfiles = {"report.json": write(root, f"{hd}/report.json", rep),
                      "check.json": write(root, f"{hd}/check.json", {"pass": bool(calls), "stream_sha256": stream,
                                                                     "stage": 3, "presents": 2,
                                                                     "failures": [] if calls else ["expect"]}),
                      "exit": write(root, f"{hd}/exit", b"0\n" if calls else b"1\n"),
                      M.CHECKED_BY: write(root, f"{hd}/{M.CHECKED_BY}",
                                          (root / "dxvk" / M.CHECKED_BY).read_bytes())}
            if h == "sdlgpu":
                hfiles["d3d9gpu.json"] = write(root, f"{hd}/d3d9gpu.json",
                                               json.loads((root / "sdlgpu" / "d3d9gpu.json").read_text()))
            hosts[h] = {"dir": hd, "files": hfiles}
        entry[mode] = {"dir": d, "files": files, "hosts": hosts}
    return entry


def test_a_method_the_recording_reaches_is_not_held_by_a_passing_synthetic_case(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    """The ruling's synthetic streams are for "methods no recorded run reaches": a game call one
    host mishandles stays not held whatever an authored stream of it shows."""
    def mishandled(docs):
        docs["counts"]["methods"].append({"type": 1, "slot": 98, "interface": "IDirect3DDevice9",
                                          "method": "SetVertexShaderConstantB", "calls": 5, "entered": 5})
        docs["reports"]["dxvk"]["methods"].append({"type": "Device", "method": "SetVertexShaderConstantB",
                                                   "slot": 98, "handled": 3})
        docs["reports"]["sdlgpu"]["methods"].append({"type": "Device", "method": "SetVertexShaderConstantB",
                                                     "slot": 98, "handled": 5})
    root = production_tree(tmp_path, mutate=mishandled)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    synthetic_receipt(root, [synthetic_case(root, target, 98, "SetVertexShaderConstantB")])
    called = ("IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present", target)
    block = three_way(root, monkeypatch, called=called)
    assert block["measured"] is True
    assert target not in block["held_methods"] and block["held_by"]["synthetic"] == []
    assert block["why_not_held"][target] == (
        "the production evidence: dxvk: IDirect3DDevice9::SetVertexShaderConstantB counts 3 calls by category, "
        "the recorder 5; its synthetic case passes, and a method the recording reaches is held by the recording "
        "alone")
    # The same case, for a method the recording does not reach, holds it.
    root = production_tree(tmp_path / "unreached")
    synthetic_receipt(root, [synthetic_case(root, target, 98, "SetVertexShaderConstantB")])
    block = three_way(root, monkeypatch, called=called)
    assert block["held_by"]["synthetic"] == [target]


def test_a_method_another_recorded_run_reaches_is_not_held_by_a_passing_synthetic_case(tmp_path, monkeypatch):
    """The ruling's synthetic streams are for "methods no recorded run reaches", any registered run,
    not only the production recording: IDirect3DVertexBuffer9::Release, which three registered idle
    runs reach and Metal Harbor does not, is held by nothing until a recording that reaches it is
    replayed in both hosts."""
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    root = production_tree(tmp_path)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    synthetic_receipt(root, [synthetic_case(root, target, 98, "SetVertexShaderConstantB")])
    called = ("IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present", target)
    block = three_way(root, monkeypatch, called=called,
                      registry={"reached": {target: 3, "IDirect3DDevice9::Present": 101}, "kept_from_earlier_counts": {}})
    assert block["measured"] is True
    assert block["held_methods"] == ["IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"]
    assert block["held_by"] == {"production": ["IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"],
                                "synthetic": []}
    assert block["why_not_held"] == {target: (
        "3 registered recorded runs (Documentation/receipts/m7-recorded-runs.json `reached`) reach it and the "
        "production recording does not; a synthetic case holds only a method no recorded run reaches; its "
        "synthetic case passes")}
    one = three_way(root, monkeypatch, called=called, registry={"reached": {target: 1}})
    assert one["why_not_held"][target].startswith(
        "1 registered recorded run (Documentation/receipts/m7-recorded-runs.json `reached`) reaches it and")
    # No registered run reaches it now: a name kept from earlier counts is one no run reaches now.
    for registry in ({"reached": {}}, {"reached": {target: 0}},
                     {"reached": {}, "kept_from_earlier_counts": {target: {"re_recorded": ["r"], "retired": []}}}):
        assert three_way(root, monkeypatch, called=called, registry=registry)["held_by"]["synthetic"] == [target]


def test_a_pin_with_no_sha256_for_a_file_the_tree_lacks_is_stale(tmp_path, monkeypatch):
    """A build record naming a source with no hash, for a path the tree does not hold: the pin and
    the tree's hash are both None, and a None pin matches nothing (stale_pins)."""
    def unhashed(docs):
        docs["records"]["sa2_player.json"]["sources"]["host/player/gone.h"] = None
    root = production_tree(tmp_path, mutate=unhashed)
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False
    assert block["why_not_measured"].endswith("(production: host/player/gone.h, synthetic: host/player/gone.h)")


def test_a_synthetic_receipt_with_two_cases_of_one_target_reads_not_measured(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    root = production_tree(tmp_path)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    case = synthetic_case(root, target, 98, "SetVertexShaderConstantB")
    synthetic_receipt(root, [case, copy.deepcopy(case)])
    block = three_way(root, monkeypatch, called=("IDirect3DDevice9::Present", target))
    assert block["measured"] is False
    assert f"synthetic: {M.SYNTHETIC} holds two cases of {target}" in block["why_not_measured"]


def test_a_case_checked_by_another_play_report_than_the_trees_is_held_by_nothing(tmp_path, monkeypatch):
    """tools/s5/m7_synthetic.py evaluates each case against the tree's play_report.py (load_case's
    checker): a check.json another checker wrote, or no checker at all, holds nothing."""
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    root = production_tree(tmp_path)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    entry = synthetic_case(root, target, 98, "SetVertexShaderConstantB")
    checked = (root / "dxvk" / M.CHECKED_BY).read_text().strip()
    assert M.case_verdict(M.load_case(root, entry, checker=checked), target)["held"] is True
    v = M.case_verdict(M.load_case(root, entry, checker="f" * 64), target)
    assert v["held"] is False
    assert f"dxvk: its check.json was written by play_report.py {checked}, not {'f' * 64}" in v["why"]
    v = M.case_verdict(M.load_case(root, entry, checker=None), target)
    assert v["held"] is False and f"written by play_report.py {checked}, not None" in v["why"]


def test_the_three_way_falsifications_skip_a_case_whose_files_are_gone_and_never_raise(tmp_path, monkeypatch):
    """A held synthetic case whose stream was deleted: the block still reads measured (that case is
    not held), so the gate builds its falsifications from the clean evidence only, and the derivation
    names the case ("the receipt holds ..., which its evidence does not"); a read that fails anyway is
    a named falsification, not a traceback."""
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    root = production_tree(tmp_path)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    synthetic_receipt(root, [synthetic_case(root, target, 98, "SetVertexShaderConstantB")])
    called = ("IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present", target)
    block = three_way(root, monkeypatch, called=called)
    assert block["held_by"]["synthetic"] == [target]
    receipt = {"inputs": {"three_way": {w: {"path": rel, "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest()}
                                        for w, rel in (("production", M.PRODUCTION), ("synthetic", M.SYNTHETIC))}},
               "three_way_numerator": block}
    monkeypatch.setattr(G, "ROOT", root)

    def built() -> list[str]:
        names: list[str] = []
        G.three_way_cases(receipt, lambda name, changes, expect, **kw: names.append(name),
                          lambda obj: json.dumps(obj).encode(),
                          lambda d, f: (lambda c: (f(c), c)[1])(copy.deepcopy(d)))
        return names
    case_built = ["a synthetic omit run passes the predicate its positive run passes",
                  "a synthetic case directory holds a counts.json"]
    whole = built()
    assert whole == ["an evidence receipt changed after the regeneration",
                     "a host's report lost the row of a method the recording holds", *case_built,
                     "a pin of the production evidence changed",
                     "the synthetic evidence dropped the pin of a source the fixture was built from",
                     "a source both players were built from moved",
                     "a source the synthetic fixture was built from moved"]

    def unreadable(*args):
        raise OSError("unreadable")
    with monkeypatch.context() as m:
        m.setattr(G, "_three_way_cases", unreadable)
        assert built() == ["an evidence receipt changed after the regeneration",
                           "the three-way evidence could not be read to build its falsifications (unreadable)"]
    (root / "syn/positive/stream.sa2s").unlink()
    assert built() == [n for n in whole if n not in case_built]
    after = three_way(root, monkeypatch, called=called)
    assert after["measured"] is True and target in after["not_held"]
    assert "syn/positive/stream.sa2s is absent or not the file the receipt pins" in after["why_not_held"][target]
    assert any(f"the receipt holds {target}, which its evidence does not" in f
               for f in G.three_way_difference(block, after))


def test_evidence_pinned_outside_the_tree_holds_nothing_and_the_gate_builds_no_falsification_from_it(
        tmp_path, monkeypatch):
    """Every evidence file is pinned by its path in the tree, so the gate can overlay a changed copy
    of it; an absolute path (a receipt written in a clone whose private/ is a link) or one that climbs
    out with .. is a problem, never an overlay of a path outside the tree."""
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    root = production_tree(tmp_path)
    receipt = json.loads((root / M.PRODUCTION).read_text())
    receipt["hosts"]["dxvk"]["dir"] = str(root / "dxvk")
    receipt["census"]["path"] = "../census.json"
    write(root, M.PRODUCTION, receipt)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    case = synthetic_case(root, target, 98, "SetVertexShaderConstantB")
    synthetic_receipt(root, [case])
    called = ("IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present", target)
    block = three_way(root, monkeypatch, called=called)
    assert block["measured"] is False
    assert f"the dxvk directory {root / 'dxvk'} is not a path in the tree" in block["why_not_measured"]
    assert "the census ../census.json is not a path in the tree" in block["why_not_measured"]
    monkeypatch.setattr(G, "ROOT", root)
    built: list[str] = []
    G.three_way_cases({"inputs": {"three_way": {"production": {}, "synthetic": {}}},
                       "three_way_numerator": {"measured": True, "held_by": {"production": ["IDirect3DDevice9::Present"]}}},
                      lambda name, changes, expect, **kw: built.append(name), lambda obj: json.dumps(obj).encode(),
                      lambda d, f: (lambda c: (f(c), c)[1])(copy.deepcopy(d)))
    assert built == ["an evidence receipt changed after the regeneration"]
    # A synthetic case pinned outside the tree is held by nothing (the block itself still measured).
    root = production_tree(tmp_path / "case")
    case = synthetic_case(root, target, 98, "SetVertexShaderConstantB")
    case["positive"]["dir"] = str(root / case["positive"]["dir"])
    synthetic_receipt(root, [case])
    block = three_way(root, monkeypatch, called=called)
    assert block["measured"] is True and target in block["not_held"]
    assert f"the run directory {root / 'syn/positive'} is not a path in the tree" in block["why_not_held"][target]


def test_a_case_whose_answers_are_another_target_or_mode_is_held_by_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "cached_scan", lambda stream, counts: [])
    root = production_tree(tmp_path)
    target = "IDirect3DDevice9::SetVertexShaderConstantB"
    other = {"positive": {"target": "IDirect3DDevice9::SetPixelShaderConstantB", "mode": "positive", "variant": ""}}
    synthetic_receipt(root, [synthetic_case(root, target, 98, "SetVertexShaderConstantB", answers=other)])
    block = three_way(root, monkeypatch, called=("IDirect3DDevice9::Present", target))
    assert target in block["not_held"]
    assert "syn/positive/answers.json is the harness's 'IDirect3DDevice9::SetPixelShaderConstantB' 'positive'" \
        in block["why_not_held"][target]
    swapped = {"omit": {"target": target, "mode": "positive", "variant": ""}}
    root = production_tree(tmp_path / "mode")
    synthetic_receipt(root, [synthetic_case(root, target, 98, "SetVertexShaderConstantB", answers=swapped)])
    block = three_way(root, monkeypatch, called=("IDirect3DDevice9::Present", target))
    assert "not 'IDirect3DDevice9::SetVertexShaderConstantB' omit" in block["why_not_held"][target]


def test_a_report_from_another_player_than_the_build_records_reads_not_measured(tmp_path, monkeypatch):
    """The stale-pin check holds the build record's sources; a report whose player is not that
    record's binary could point it at any build."""
    root = production_tree(tmp_path, mutate=_set("reports.dxvk.files.player.sha256", "e" * 64))
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False
    assert f"the dxvk player that ran ({'e' * 64}) is not the build record's sa2_player" in block["why_not_measured"]
    root = production_tree(tmp_path / "gpu", mutate=_set("device.library.sha256", "e" * 64))
    synthetic_receipt(root)
    assert "the second host's device ran from " + "e" * 64 in three_way(root, monkeypatch)["why_not_measured"]


def test_a_scene_order_allowance_never_covers_more_differences_than_failures(tmp_path, monkeypatch):
    call = "seq 9 Device::BeginScene: host 0x8876086c, guest 0x00000000 (scene order: BeginScene inside a scene)"

    def differs(docs):
        for h in M.HOSTS:
            r = docs["reports"][h]
            r["methods"][1].update(failed=1, hr_differs_from_guest=2 if h == "dxvk" else 1)
            r["allowed_host_failures"] = {"rules": {"scene order: BeginScene inside a scene": 1}, "calls": [call]}
    root = production_tree(tmp_path, mutate=differs)
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["held_methods"] == ["IDirect3DDevice9::Present"]
    assert "dxvk: IDirect3DDevice9::BeginScene failed 1 and differed from the guest 2 times" \
        in block["why_not_held"]["IDirect3DDevice9::BeginScene"]


@pytest.mark.parametrize("mutate, why", [
    (lambda d: d["harness"]["sources"].pop("runtime/sa2_d3d9.c"), "the fixture's record pins no runtime/sa2_d3d9.c"),
    (lambda d: d["harness"].update(sources={}), "the fixture's record pins no tools/s5/tests/rec_harness_m7.c"),
    (lambda d: d["harness"].update(tools={}), "the fixture's record pins the tools []"),
    (lambda d: d["harness"]["tools"].pop("tools/s5/objcheck.py"), "the fixture's record pins the tools"),
])
def test_a_synthetic_receipt_that_drops_a_pin_reads_not_measured(tmp_path, monkeypatch, mutate, why):
    """A pin that is absent never goes stale: the receipt must pin the fixture's own sources."""
    root = production_tree(tmp_path)
    synthetic_receipt(root, mutate=mutate)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False and why in block["why_not_measured"], block["why_not_measured"]


def test_a_build_record_without_the_players_replay_sources_reads_not_measured(tmp_path, monkeypatch):
    root = production_tree(tmp_path, mutate=lambda docs: docs["records"]["sa2_player_gpu.json"].update(sources={}))
    synthetic_receipt(root)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False
    assert ("the build record build/sa2_player_gpu.json pins no host/player/player.cpp, host/player/stream.cpp, "
            "host/d3d9gpu/d9g_device.cpp") in block["why_not_measured"]


def test_a_recording_that_names_no_runtime_is_stale_even_when_the_tree_names_none(tmp_path, monkeypatch):
    root = production_tree(tmp_path, mutate=_set("present.current.runtime_sha256", None))
    synthetic_receipt(root)
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: None)
    inp = inputs()
    inp.three_way = M.load_three_way(root)
    block = M.three_way_numerator(inp, {"IDirect3DDevice9::Present"}, {"IDirect3DDevice9::Present"})
    assert block["measured"] is False and block["why_not_measured"].endswith("(production: the runtime digest)")


def test_the_production_writer_refuses_two_builds_or_a_report_naming_no_player(tmp_path):
    root = production_tree(tmp_path)
    report = json.loads((root / "sdlgpu/report.json").read_text())
    report["files"]["player"]["real_path"] = str(root / "elsewhere" / "sa2_player_gpu")
    write(root, "sdlgpu/report.json", report)
    with pytest.raises(M.Refused, match="the two hosts ran players from .* not one build"):
        M.production_receipt(root, "rec", "dxvk", "sdlgpu", "census.json")
    del report["files"]["player"]["real_path"]
    write(root, "sdlgpu/report.json", report)
    with pytest.raises(M.Refused, match="sdlgpu/report.json names no player file"):
        M.production_receipt(root, "rec", "dxvk", "sdlgpu", "census.json")
    root = production_tree(tmp_path / "out")
    assert M.production_receipt(root, "rec", "dxvk", "sdlgpu", "census.json", out="trial/p.json")["command"] \
        .endswith(" --out trial/p.json")


def test_a_recording_is_moved_when_any_source_it_names_is_not_the_trees_or_names_none(tmp_path):
    """recording_moved, which tools/s5/m7_synthetic.py refresh reads before it replays a
    recording: the stale_pins rule for the recording's own pins."""
    rec_sha = write(tmp_path, M.RECORDER, b"the recorder\n")
    made = {"recorder_sha256": rec_sha, "stream_h_sha256": None, "runtime_sha256": "r" * 64}
    # The tree lacks the stream format and the recording names none: both None, still moved.
    assert M.recording_moved(tmp_path, made, "r" * 64) == [("host/sa2_stream.h", None, None)]
    fmt = write(tmp_path, "host/sa2_stream.h", b"the format\n")
    assert M.recording_moved(tmp_path, dict(made, stream_h_sha256=fmt), "r" * 64) == []
    assert M.recording_moved(tmp_path, dict(made, stream_h_sha256=fmt, recorder_sha256="0" * 64), "r" * 64) == [
        (M.RECORDER, "0" * 64, rec_sha)]
    assert M.recording_moved(tmp_path, dict(made, stream_h_sha256=fmt), "q" * 64) == [
        ("the runtime digest", "r" * 64, "q" * 64)]
    assert M.recording_moved(tmp_path, dict(made, stream_h_sha256=fmt, runtime_sha256=None), None) == [
        ("the runtime digest", None, None)]


def test_a_host_entry_names_its_directory_and_files_or_takes_the_entrys():
    assert M.host_pins({"dir": "run", "hosts": {"dxvk": {"dir": "elsewhere", "files": {"exit": "e"}}}}, "dxvk") == \
        ("elsewhere", {"exit": "e"})
    assert M.host_pins({"dir": "run", "hosts": {"dxvk": {"exit": "e"}}}, "dxvk") == ("run/dxvk", {"exit": "e"})
    assert M.host_pins({"dir": "run", "hosts": {"dxvk": {"files": ["exit"]}}}, "dxvk") == ("run/dxvk", {})
    assert M.host_pins({"dir": "run"}, "sdlgpu") == ("run/sdlgpu", {})


def test_the_gate_names_how_a_written_block_differs_from_what_its_evidence_derives():
    derived = {"measured": True, "held_methods": ["A::a", "B::b"], "not_held": ["C::c"],
               "why_not_held": {"C::c": "its synthetic case: the omit stream does not fail"}}
    assert G.three_way_difference(dict(derived), derived) == []
    found = G.three_way_difference(dict(derived, held_methods=["A::a", "C::c"], not_held=["B::b"]), derived)
    assert [f.split(": ", 1)[1].split(". Run")[0] for f in found] == [
        "the receipt holds C::c, which its evidence does not (its synthetic case: the omit stream does not fail)",
        "its evidence holds B::b, which the receipt does not"]
    stale = {"measured": False, "not_held": None, "why_not_measured": "production: x is absent"}
    found = G.three_way_difference(derived, stale)
    assert len(found) == 1 and ("the receipt reads measured True and the evidence derives measured False "
                                "(production: x is absent)") in found[0]
    # Evidence gone stale is made again first (ACCEPT churn: the refresh is the one command).
    assert found[0].endswith(f". Make the evidence again ({M.REFRESH}), then run: {G.COMMAND}")
    assert G.three_way_difference(stale, derived)[0].endswith(f"measured True. Run: {G.COMMAND}")
    # Same held set, another difference (a why text edited by hand): named as the block.
    found = G.three_way_difference(dict(derived, why_not_held={"C::c": "edited"}), derived)
    assert len(found) == 1 and found[0].endswith(f"(a hand edit or a partial regeneration). Run: {G.COMMAND}")


def test_the_gate_names_an_evidence_receipt_m7_was_not_regenerated_from(tmp_path):
    receipt = json.loads((ROOT / M.RECEIPT).read_text())
    receipt.setdefault("inputs", {})["three_way"] = {
        "production": {"path": M.PRODUCTION, "sha256": "0" * 64},
        "synthetic": {"path": M.SYNTHETIC, "sha256": None}}
    evidence = b'{"schema": "sa2-recomp-m7-production/v1"}\n'
    view = G.overlay(ROOT, tmp_path / "view", {M.RECEIPT: (json.dumps(receipt) + "\n").encode(),
                                               M.PRODUCTION: evidence, M.SYNTHETIC: None})
    found = G.problems(view, head={}, disk={})
    assert any(f"inputs.three_way.production ({M.PRODUCTION})" in p and hashlib.sha256(evidence).hexdigest() in p
               for p in found), [p for p in found if "three_way" in p]
    del receipt["inputs"]["three_way"]
    view = G.overlay(ROOT, tmp_path / "none", {M.RECEIPT: (json.dumps(receipt) + "\n").encode(),
                                               M.PRODUCTION: evidence, M.SYNTHETIC: None})
    found = G.problems(view, head={}, disk={})
    assert f"M7 is stale: the three-way numerator's evidence ({M.PRODUCTION}) is on disk and the receipt " \
           f"records none. Run: {G.COMMAND}" in found


def test_the_three_way_falsifications_read_no_evidence_that_does_not_load(tmp_path, monkeypatch):
    """A receipt that reads measured over evidence since deleted: the gate builds only the case
    that needs no evidence file, and problems() is left to name the derivation (no traceback)."""
    monkeypatch.setattr(G, "ROOT", tmp_path)
    receipt = {"inputs": {"three_way": {"production": {"path": M.PRODUCTION, "sha256": "1" * 64},
                                        "synthetic": {"path": M.SYNTHETIC, "sha256": "2" * 64}}},
               "three_way_numerator": {"measured": True, "held_methods": ["A::a"], "not_held": [],
                                       "held_by": {"production": ["A::a"], "synthetic": []}}}
    built = []
    G.three_way_cases(receipt, lambda name, changes, expect, **kw: built.append(name),
                      lambda obj: json.dumps(obj).encode(), lambda d, f: (f(d), d)[1])
    assert built == ["an evidence receipt changed after the regeneration"]


def test_a_regeneration_that_moves_the_m7_row_names_what_else_its_commit_must_change(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
    import metrics
    unmeasured = ("94/94 (100.0%); denominator is a floor -- at most 4125 of 4263 vtable-shaped call sites could "
                  "still add to it, and the scan missed 14 a driven run called; numerator is the device's alone, an "
                  "upper bound until the recorder and both players are measured")
    measured = ("77/94 (81.9%); denominator is a floor -- at most 4125 of 4263 vtable-shaped call sites could still "
                "add to it, and the scan missed 14 a driven run called")
    (tmp_path / "METRICS.md").write_text(f"| Metric | Value | Source | State |\n| M7 device | {unmeasured} | "
                                         "analyzer call-site scan | measured |\n")

    class Row:
        def __init__(self, value):
            self.value = value
    monkeypatch.setattr(metrics, "m7_device", lambda root: Row(unmeasured))
    assert M.row_moved(tmp_path) == []
    monkeypatch.setattr(metrics, "m7_device", lambda root: Row(measured))
    lines = M.row_moved(tmp_path)
    assert lines[0] == f"M7_ROW_MOVED {unmeasured!r} -> {measured!r}"
    assert "answer design-S5-d3d9-surface in Documentation/kill-criteria.json again" in lines[1]
    assert "evidence_shape moves too" in lines[2]
    assert len(lines) == 3
    # Back to unmeasured (evidence gone stale): the refresh is named, so the flip is a choice.
    (tmp_path / "METRICS.md").write_text(f"| Metric | Value | Source | State |\n| M7 device | {measured} | "
                                         "analyzer call-site scan | measured |\n")
    monkeypatch.setattr(metrics, "m7_device", lambda root: Row(unmeasured))
    lines = M.row_moved(tmp_path)
    assert "evidence_shape moves too" in lines[2] and len(lines) == 4
    assert lines[3] == (f"  the three-way block now reads not measured; to keep it measured instead, make its "
                        f"evidence again first ({M.REFRESH}) and regenerate")
    (tmp_path / "METRICS.md").write_text(f"| Metric | Value | Source | State |\n| M7 device | {unmeasured} | "
                                         "analyzer call-site scan | measured |\n")
    # A move within one state (digits only) needs no repin.
    monkeypatch.setattr(metrics, "m7_device", lambda root: Row(unmeasured.replace("94/94 (100.0%)", "93/94 (98.9%)")))
    assert len(M.row_moved(tmp_path)) == 2


# ------------------------------------------------------------------ the scan's sound rule-outs

def rule_out_scan() -> dict:
    """A scan whose sound_rule_outs add up: a ceiling of 10, R1 removes two sites, R2 one,
    R3 none; three attributed call sites in two methods, none of them removed."""
    r1, r2, r3 = M.RULES
    return {
        "layer_b_attributed": {"call_sites": 3, "methods": [
            {"call_sites": ["0x00401000", "0x00401010"]}, {"call_sites": ["0x00401020"]}]},
        "sound_rule_outs": {
            "assumption": "a stated assumption",
            "ceiling_before_rules": 10,
            r1: {"removed": 2, "sites": ["0x00500000", "0x00500010"], "control_sites_ruled_out": [],
                 "globals": ["0x01d19cac"],
                 "objects": {"0x01d19c60": {"object_address_escapes": {"store": 1}}}},
            r2: {"removed": 1, "sites": ["0x00500020"], "control_sites_ruled_out": []},
            r3: {"removed": 0, "sites": [], "control_sites_ruled_out": []},
            "ceiling_after_R1": 8, "ceiling_after_R2": 7, "ceiling_after_R3": 7,
            "ceiling_after_sound_rule_outs": 7,
            "removed_sites": ["0x00500000", "0x00500010", "0x00500020"],
            "must_analysis": {"functions_whose_analysis_did_not_converge": []},
        },
    }


def test_sound_rule_outs_that_add_up_are_recorded_beside_the_ceiling() -> None:
    got = M.sound_rule_outs(rule_out_scan(), 10)
    assert {k: got[k] for k in ("ceiling_before_rules", "R1_removed", "R2_removed", "R3_removed",
                                "ceiling_after_sound_rule_outs")} == {
        "ceiling_before_rules": 10, "R1_removed": 2, "R2_removed": 1, "R3_removed": 0,
        "ceiling_after_sound_rule_outs": 7}
    assert got["R1_enclosing_object_escapes"] == {"0x01d19c60": {"store": 1}}
    assert "does not feed the kill threshold" in got["note"]
    assert "rule these ceiling sites out" in got["note"] and "prove" not in got["note"]
    assert M.sound_rule_outs({"layer_b_attributed": {}}, 10) is None


def _moved_into_r1(scan: dict, site: str) -> None:
    """An attributed site moved into R1's list with every count kept consistent."""
    ro = scan["sound_rule_outs"]
    r1 = M.RULES[0]
    ro[r1]["sites"].append(site)
    ro[r1]["removed"] += 1
    ro["removed_sites"] = sorted(set(ro["removed_sites"]) | {site})
    for k in ("ceiling_after_R1", "ceiling_after_R2", "ceiling_after_R3",
              "ceiling_after_sound_rule_outs"):
        ro[k] -= 1


@pytest.mark.parametrize("corrupt, says", [
    (lambda s: _moved_into_r1(s, "0x00401010"), "removed attributed d3d9 call sites"),
    (lambda s: s["sound_rule_outs"]["removed_sites"].append("0x00401020"),
     "removed attributed d3d9 call sites"),
    (lambda s: s["sound_rule_outs"][M.RULES[1]]["control_sites_ruled_out"].append("0x00401000"),
     "ruled out attributed d3d9 call sites"),
    # an absent control list is not an empty one
    (lambda s: s["sound_rule_outs"][M.RULES[2]].pop("control_sites_ruled_out"), "incomplete"),
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("control_sites_ruled_out", None),
     "incomplete"),
    # a control list that is not a list of call-site strings is refused before it is read: an
    # int is not iterable, and a string or an object would be read as its characters or keys
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("control_sites_ruled_out", 5),
     "control_sites_ruled_out is 5, not a list of call sites"),
    (lambda s: s["sound_rule_outs"][M.RULES[1]].__setitem__("control_sites_ruled_out",
                                                             "0x00401000"),
     "control_sites_ruled_out is '0x00401000', not a list of call sites"),
    (lambda s: s["sound_rule_outs"][M.RULES[2]].__setitem__("control_sites_ruled_out",
                                                             {"0x00401000": 1}),
     "control_sites_ruled_out is {'0x00401000': 1}, not a list of call sites"),
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("control_sites_ruled_out", [4096]),
     "control_sites_ruled_out is [4096], not a list of call sites"),
    (lambda s: s["sound_rule_outs"].__setitem__(M.RULES[1], ["0x00500020"]), "incomplete"),
    # an emptied method list holds nothing, even with the site's counts kept consistent
    (lambda s: (s["layer_b_attributed"].__setitem__("methods", []),
                _moved_into_r1(s, "0x00401010")), "attributed methods list 0 distinct"),
    (lambda s: s["layer_b_attributed"]["methods"].pop(), "attributed methods list 2 distinct"),
    # the list must equal the count, not only reach it: a padded list is refused too
    (lambda s: s["layer_b_attributed"]["methods"].append({"call_sites": ["0x00401030"]}),
     "attributed methods list 4 distinct call sites and it counts 3"),
    # a scan that counts no attributed sites holds the list to nothing, so it is refused
    (lambda s: s["layer_b_attributed"].pop("call_sites"), "and it counts None"),
    (lambda s: s["layer_b_attributed"].__setitem__("call_sites", "3"), "and it counts '3'"),
    # a JSON true is no count, though Python reads it as 1
    (lambda s: s["layer_b_attributed"].update(
        methods=[{"call_sites": ["0x00401000"]}], call_sites=True), "and it counts True"),
    (lambda s: s["layer_b_attributed"].pop("methods"), "cannot be read"),
    (lambda s: s["sound_rule_outs"].__setitem__("ceiling_after_sound_rule_outs", 1), "do not add up"),
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("removed", 1), "do not add up"),
    (lambda s: (s["sound_rule_outs"][M.RULES[2]]["sites"].append("0x00500000"),
                s["sound_rule_outs"][M.RULES[2]].__setitem__("removed", 1)), "do not add up"),
    (lambda s: s["sound_rule_outs"].__setitem__("ceiling_after_R2", 3), "do not add up"),
    (lambda s: s["sound_rule_outs"].pop("ceiling_after_R1"), "incomplete"),
])
def test_sound_rule_outs_that_touch_an_attributed_site_or_do_not_add_up_are_refused(corrupt, says) -> None:
    scan = rule_out_scan()
    corrupt(scan)
    with pytest.raises(M.Refused) as e:
        M.sound_rule_outs(scan, 10)
    assert says in str(e.value)
    assert "--rescan" in str(e.value)


def test_a_starting_ceiling_other_than_the_census_is_refused() -> None:
    with pytest.raises(M.Refused, match="ceiling_before_rules 10 is not the census ceiling 11"):
        M.sound_rule_outs(rule_out_scan(), 11)


# ------------------------------------------------------------------ the user's ruling of 2026-10-09
#
# Documentation/decisions/2026-10-09-S5-user-rulings.md, item 1: the ceiling and the misses are
# printed and guarded against regression as instrument health, every ceiling site is listed, and
# the device's own table is counted from every game run, with or without the recorder.

import check_boundary  # noqa: E402


@pytest.mark.parametrize("corrupt, says", [
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("sites", 7), "R1_constant_function_table_global's sites is 7"),
    (lambda s: s["sound_rule_outs"][M.RULES[1]].__setitem__("sites", "0x00500000"), "sites is '0x00500000'"),
    (lambda s: s["sound_rule_outs"][M.RULES[2]].__setitem__("sites", [5]), "sites is [5]"),
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("removed", "2"), "removed is '2'"),
    (lambda s: s["sound_rule_outs"][M.RULES[1]].__setitem__("removed", None), "removed is None"),
    (lambda s: s["sound_rule_outs"].__setitem__("ceiling_after_R2", 7.0), "ceiling_after_R2 is 7.0"),
    (lambda s: s["sound_rule_outs"].__setitem__("ceiling_before_rules", "10"), "ceiling_before_rules is '10'"),
    (lambda s: s["sound_rule_outs"].__setitem__("removed_sites", "0x00500000"), "removed_sites is"),
    (lambda s: s["sound_rule_outs"][M.RULES[0]].__setitem__("objects", ["x"]), "R1's objects"),
    (lambda s: s["sound_rule_outs"].__setitem__("must_analysis", ["x"]), "must_analysis"),
])
def test_a_malformed_rule_out_field_is_refused_cleanly_not_raised(corrupt, says) -> None:
    """The wave-1 minor: a rule's `sites` or `removed` of the wrong type raised a TypeError from
    the sums; each is now refused by name, as an incomplete block."""
    scan = rule_out_scan()
    corrupt(scan)
    with pytest.raises(M.Refused) as e:
        M.sound_rule_outs(scan, 10)
    assert "incomplete" in str(e.value) and says in str(e.value), str(e.value)


def site_rows(n: int, start: int = 0x00500000) -> list[dict]:
    return [{"va": hex(start + 4 * k), "function": hex(start), "disp": "0x8", "receiver": "global-vt" if k % 2 else "ret",
             "region": "game" if k % 3 else "cri", "ruled_out_by": M.RULES[0] if k % 5 == 0 else None}
            for k in range(n)]


def sited_scan(rows: list[dict] | None, ceiling: int) -> dict:
    census = {"unattributed_that_could_still_be_d3d9": ceiling}
    if rows is not None:
        census["ceiling_sites"] = rows
    return {"layer_a_shape_census": census}


def test_every_ceiling_site_is_carried_by_its_count_and_digest_alone() -> None:
    rows = site_rows(6)
    record, problems = M.ceiling_site_record(sited_scan(rows, 6))
    assert problems == []
    assert record["count"] == 6
    assert record["listed_in"] == M.SCAN and record["field"] == "layer_a_shape_census.ceiling_sites"
    assert record["by_region"] == {"cri": 2, "game": 4}
    assert record["by_receiver"] == {"global-vt": 3, "ret": 3}
    assert record["by_sound_rule"] == {"none": 4, M.RULES[0]: 2}
    want = hashlib.sha256(json.dumps(sorted(rows, key=lambda r: int(r["va"], 16)), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()
    assert record["sha256"] == want
    # The canonical form is the list in address order: the scan's order does not move it.
    assert M.ceiling_site_record(sited_scan(list(reversed(rows)), 6))[0]["sha256"] == want
    # Any edit to a row moves it.
    edited = json.loads(json.dumps(rows))
    edited[3]["receiver"] = "this"
    assert M.ceiling_site_record(sited_scan(edited, 6))[0]["sha256"] != want


@pytest.mark.parametrize("rows, ceiling, says", [
    (None, 6, "lists no ceiling sites"),
    (site_rows(5), 6, "lists 5 sites and its ceiling is 6"),
    (site_rows(6) + site_rows(1), 7, "lists a site twice"),
    ([{"va": "0x00500000"}], 1, "is not a list of"),
    ([{**site_rows(1)[0], "va": "here"}], 1, "not hex"),
    ("0x00500000", 1, "is not a list of"),
])
def test_a_ceiling_site_list_that_is_absent_short_or_malformed_is_a_problem(rows, ceiling, says) -> None:
    record, problems = M.ceiling_site_record(sited_scan(rows, ceiling))
    assert record is None and len(problems) == 1 and says in problems[0], problems
    assert "--rescan" in problems[0]


def test_the_tracked_receipt_stays_under_the_boundarys_address_limit_and_the_list_would_not() -> None:
    """check_boundary.py refuses a tracked file with GUEST_ADDRESS_LIMIT or more distinct guest
    addresses, so the list stays private and the tracked receipt carries its count and digest."""
    rows = site_rows(4 * check_boundary.GUEST_ADDRESS_LIMIT)
    record, _ = M.ceiling_site_record(sited_scan(rows, len(rows)))
    tracked = json.dumps({"ceiling_sites": record}, indent=1).encode()
    assert check_boundary.lifted_content(tracked) is None
    carrying = json.dumps({"ceiling_sites": rows}, indent=1).encode()
    assert "distinct guest-address literals" in check_boundary.lifted_content(carrying)


def test_the_derived_denominator_carries_the_ceiling_site_record() -> None:
    scan = inputs().scan
    scan["layer_a_shape_census"]["ceiling_sites"] = site_rows(3)
    den, _ = M.derive(inputs(scan=scan))
    assert den["ceiling_sites"]["count"] == 3
    assert "what_the_ceiling_and_the_misses_mean" in den
    assert "not every method the binary could call" in den["what_the_ceiling_and_the_misses_mean"]
    assert "why_100_percent_is_not_reachable_today" not in den
    # A scan made before the list derives no record; the gate and the tool name the problem.
    assert M.derive(inputs())[0]["ceiling_sites"] is None


# ---- instrument health: growth against HEAD

def grown(ceiling=4080, missed=("A::a", "B::b")) -> dict:
    return {"unattributed_that_could_still_be_d3d9": ceiling,
            "called_by_the_run_but_missed_by_the_scan": list(missed)}


@pytest.mark.parametrize("head, now, growth", [
    (grown(), grown(), None),
    (grown(), grown(4079), None),                                     # a fall
    (grown(), grown(missed=("A::a",)), None),                         # a miss fixed
    (None, grown(9999), None),                                        # no HEAD receipt
    (grown(), grown(4081), {"against_head": {"ceiling": 4080, "missed": ["A::a", "B::b"]},
                             "ceiling": 4081, "missed_added": []}),
    (grown(), grown(missed=("A::a", "C::c")), {"against_head": {"ceiling": 4080, "missed": ["A::a", "B::b"]},
                                                "ceiling": 4080, "missed_added": ["C::c"]}),
])
def test_growth_against_head_is_a_ceiling_above_heads_or_a_miss_head_did_not_have(head, now, growth) -> None:
    assert M.instrument_growth(head, now) == growth


def test_growth_fails_until_the_receipt_names_its_cause() -> None:
    head, now = grown(), grown(4081, ("A::a", "B::b", "C::c"))
    problems = M.growth_problems(head, now)
    assert len(problems) == 1
    assert "the scan's ceiling grew from 4080 to 4081" in problems[0]
    assert "the scan now misses C::c" in problems[0]
    assert "--growth-cause" in problems[0]
    record = {**M.instrument_growth(head, now), "cause": "a census of every game run counts more runs"}
    assert M.growth_problems(head, {**now, "instrument_growth": record}) == []
    for bad in ({**record, "cause": " "}, {**record, "ceiling": 4082},
                {**record, "against_head": {"ceiling": 4079, "missed": ["A::a", "B::b"]}},
                {k: v for k, v in record.items() if k != "missed_added"}):
        assert M.growth_problems(head, {**now, "instrument_growth": bad}), bad
    # A fall needs no record, and the record of an earlier growth does not hide a new one.
    assert M.growth_problems(head, grown(10)) == []
    assert M.growth_problems(grown(4081, ("A::a", "B::b", "C::c")),
                             {**grown(4082, ("A::a", "B::b", "C::c")), "instrument_growth": record})


def test_the_tool_records_growth_only_with_its_cause_or_a_record_of_exactly_it() -> None:
    head, now = grown(), grown(4081)
    with pytest.raises(M.Refused) as e:
        M.growth_record(head, now, None, None)
    assert "--growth-cause" in str(e.value)
    rec = M.growth_record(head, now, None, "the scanner lost a transfer rule")
    assert rec["cause"] == "the scanner lost a transfer rule" and rec["ceiling"] == 4081
    assert M.growth_record(head, now, {"instrument_growth": rec}, None) == rec
    with pytest.raises(M.Refused):
        M.growth_record(head, grown(4082), {"instrument_growth": rec}, None)
    assert M.growth_record(head, grown(4079), {"instrument_growth": rec}, None) is None


def test_the_gate_holds_the_growth_against_head() -> None:
    """problems() reads HEAD's receipt (head_docs) and fails a receipt grown against it."""
    import inspect
    src = inspect.getsource(G.problems)
    assert "M.growth_problems(head.get(M.RECEIPT), receipt)" in src


# ---- the census of every guest run

def probe_record(path: Path, calls: dict[str, int], interface: str = "IDirect3DDevice9",
                 refused: tuple[str, ...] = ()) -> Path:
    """A record in harness/crt_probe.c's shape; a method in `refused` is a row the device does
    not implement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"interface": interface, "method": m, "slot": k, "argc": 0, "implemented": m not in refused,
             "calls": c, "flags": ""} for k, (m, c) in enumerate(calls.items())]
    path.write_text(M.PROBE_HEADER.decode() + '007a801c", "rc": -1,\n "winmain": {"reached": true},\n "d3d9": '
                    + json.dumps({"implemented": rows}) + "}\n")
    return path


def age(*paths: Path, ns: int = 1_000_000_000) -> None:
    for p in paths:
        os.utime(p, ns=(ns, ns))


def test_a_guest_run_records_table_names_every_method_it_called() -> None:
    # A row the device does not implement counts too: a new method looks like that (the closed
    # world counts a trapped call on its slot before it traps).
    doc = {"winmain": {}, "d3d9": {"implemented": [
        {"interface": "IDirect3DDevice9", "method": "Present", "implemented": True, "calls": 3},
        {"interface": "IDirect3DDevice9", "method": "Reset", "implemented": True, "calls": 0},
        {"interface": "IDirect3DDevice9", "method": "SetClipPlane", "implemented": False, "calls": 1},
        {"interface": "IDirect3DDevice9", "method": "GetClipPlane", "implemented": False, "calls": 0},
        {"interface": "IDirect3DTexture9", "method": "LockRect", "calls": 1}]}}
    assert M.device_table_names(doc) == {"IDirect3DDevice9::Present", "IDirect3DDevice9::SetClipPlane",
                                         "IDirect3DTexture9::LockRect"}
    for bad in ({"interface": "IDirect3DDevice9", "method": "Present", "calls": "3"},
                {"interface": "IDirect3DDevice9", "calls": 1}, "Present"):
        assert M.device_table_names({"d3d9": {"implemented": [bad]}}) is None


def test_a_file_is_a_guest_run_record_only_with_the_probes_header(tmp_path: Path) -> None:
    rec = probe_record(tmp_path / "x.json", {"Present": 1})
    assert M.file_verdict(str(rec), rec.name, rec.stat().st_mtime_ns) == ("g", {"IDirect3DDevice9::Present"})
    planted = tmp_path / "run.json"
    planted.write_text(json.dumps({"winmain": {}, "d3d9": {"implemented": []}}))
    assert M.file_verdict(str(planted), "run.json", 0) == ("q", None)
    other = tmp_path / "planted.json"
    other.write_text(json.dumps({"winmain": {}, "d3d9": {"implemented": [
        {"interface": "IDirect3DDevice9", "method": "Present", "calls": 1}]}}))
    assert M.file_verdict(str(other), other.name, 0) == ("n", None)
    counts_file = tmp_path / "counts.json"
    counts_file.write_text(json.dumps(counts([("IDirect3DDevice9", "Present", 1)])))
    assert M.file_verdict(str(counts_file), "counts.json", 0) == ("c", None)
    torn = tmp_path / "torn.json"
    torn.write_text(M.PROBE_HEADER.decode() + '007a801c", "rc"')
    assert M.file_verdict(str(torn), torn.name, 1_000_000_000)[0] == "t"
    assert M.file_verdict(str(torn), torn.name, torn.stat().st_mtime_ns)[0] == "p"
    bad = probe_record(tmp_path / "bad.json", {"Present": 1})
    bad.write_text(bad.read_text().replace('"calls": 1', '"calls": "1"'))
    assert M.file_verdict(str(bad), bad.name, 0) == ("u", None)


@pytest.fixture
def indexed(tmp_path: Path) -> Path:
    g = tmp_path / "private" / "generated"
    probe_record(g / "s4" / "run" / "reach.a.json", {"Present": 2, "Reset": 0})
    (g / "s4" / "run" / "matrix.json").write_text('{"rows": []}')
    probe_record(g / "s5" / "u" / "vfsroot" / "run.json", {"SetFVF": 1})
    probe_record(g / "s5" / "clone" / "w" / "run.json", {"SetFVF": 1})
    (g / "s5" / "clone" / ".git").mkdir()
    elsewhere = tmp_path / "elsewhere"
    probe_record(elsewhere / "run.json", {"SetFVF": 1})
    (g / "s5" / "link").symlink_to(elsewhere)
    age(*[p for p in g.rglob("*") if not p.is_symlink()], g)
    c = M.update_index(tmp_path, build=True, by="test")
    assert c.device_runs == {"private/generated/s4/run/reach.a.json": {"IDirect3DDevice9::Present"}}
    return tmp_path


def test_an_index_that_is_absent_is_refused_at_a_push_and_built_on_request(tmp_path: Path) -> None:
    probe_record(tmp_path / "private/generated/s4/run/a.json", {"Present": 1})
    with pytest.raises(M.Refused) as e:
        M.update_index(tmp_path)
    assert "--index" in str(e.value) and not (tmp_path / M.INDEX_DIR).exists()
    c = M.update_index(tmp_path, build=True, by="test")
    assert c.stats["built"] is True and set(c.device_runs) == {"private/generated/s4/run/a.json"}
    assert M.update_index(tmp_path).stats["built"] is False


def test_the_census_finds_a_run_however_it_reaches_the_disk(indexed: Path) -> None:
    g = indexed / "private" / "generated"
    probe_record(g / "s4" / "run" / "second.json", {"Clear": 1, "SetClipPlane": 2},
                 refused=("SetClipPlane",))                                   # a new file
    probe_record(g / "s5" / "new" / "a" / "b" / "run.json", {"EndScene": 1})  # a new deep directory
    c = M.update_index(indexed)
    assert c.device_runs["private/generated/s4/run/second.json"] == {"IDirect3DDevice9::Clear",
                                                                      "IDirect3DDevice9::SetClipPlane"}
    assert c.device_runs["private/generated/s5/new/a/b/run.json"] == {"IDirect3DDevice9::EndScene"}
    # a JSON file rewritten in place as a run, its directory's mtime unchanged
    age(g / "s4" / "run")
    M.update_index(indexed)
    probe_record(g / "s4" / "run" / "matrix.json", {"BeginScene": 1})
    age(g / "s4" / "run")
    assert M.update_index(indexed).device_runs["private/generated/s4/run/matrix.json"] == \
        {"IDirect3DDevice9::BeginScene"}
    # a checkout whose .git goes: its runs are the tree's
    (g / "s5" / "clone" / ".git").rmdir()
    assert "private/generated/s5/clone/w/run.json" in M.update_index(indexed).device_runs
    # never: the guest's own files, or anything through a link below private/generated's children
    c = M.update_index(indexed)
    assert not any("vfsroot" in r or "/link/" in r for r in c.indexed)


def test_a_run_landing_within_the_clock_tick_its_directory_was_read_in_is_found(indexed: Path) -> None:
    run = indexed / "private/generated/s4/run"
    tick = time.time_ns()
    age(run, ns=tick)
    M.update_index(indexed)
    probe_record(run / "tick.json", {"Clear": 1})
    age(run, ns=tick)
    assert "private/generated/s4/run/tick.json" in M.update_index(indexed).device_runs


def test_a_run_deleted_or_rewritten_keeps_every_name_it_reached(indexed: Path) -> None:
    g = indexed / "private" / "generated"
    rec = g / "s4" / "run" / "reach.a.json"
    probe_record(rec, {"Clear": 1})
    c = M.update_index(indexed)
    assert c.device_runs["private/generated/s4/run/reach.a.json"] == {"IDirect3DDevice9::Clear",
                                                                       "IDirect3DDevice9::Present"}
    rec.unlink()
    c = M.update_index(indexed)
    assert "private/generated/s4/run/reach.a.json" not in c.device_runs
    assert c.indexed["private/generated/s4/run/reach.a.json"] == {"IDirect3DDevice9::Clear",
                                                                   "IDirect3DDevice9::Present"}
    runs = json.loads((indexed / M.INDEX_DIR / "runs.json").read_text())["runs"]
    assert runs["private/generated/s4/run/reach.a.json"]["gone"] is True


def test_a_record_the_census_cannot_read_is_reported(indexed: Path) -> None:
    run = indexed / "private/generated/s4/run"
    torn = run / "torn.json"
    torn.write_text(M.PROBE_HEADER.decode() + '007a801c", "rc"')
    age(torn)
    c = M.update_index(indexed)
    assert c.unreadable == {"private/generated/s4/run/torn.json": M.UNREADABLE["t"]}
    (run / "tab\tname.json").write_text("{}")
    assert "private/generated/s4/run/'tab\\tname.json'" in M.update_index(indexed).unreadable


def test_a_launcher_indexes_each_run_as_soon_as_it_is_written(indexed: Path) -> None:
    out = probe_record(indexed / "private/generated/s4/run/loop.next+allarms.baseline.json", {"Clear": 1})
    assert M.index_run_record(indexed, out, by="a launcher") is True
    runs = json.loads((indexed / M.INDEX_DIR / "runs.json").read_text())["runs"]
    assert runs["private/generated/s4/run/loop.next+allarms.baseline.json"]["names"] == ["IDirect3DDevice9::Clear"]
    # replaced before the next update, its names stay
    probe_record(out, {"Present": 1})
    M.index_run_record(indexed, out, by="a launcher")
    c = M.update_index(indexed)
    assert c.indexed["private/generated/s4/run/loop.next+allarms.baseline.json"] == {
        "IDirect3DDevice9::Clear", "IDirect3DDevice9::Present"}
    other = indexed / "private/generated/s4/run/notes.json"
    other.write_text("{}")
    assert M.index_run_record(indexed, other, by="x") is False


def test_the_recorder_census_reads_the_index_with_its_old_rule(tree: Path) -> None:
    """disk_runs keeps its rule (a recorder counts.json beside a run.json in the guest record's
    shape, planted or not) and reads it from the census instead of a walk."""
    c = M.update_index(tree, build=True, by="test")
    assert c.recorder_runs == ["private/generated/s5/a", "private/generated/s5/b", "private/generated/s5/c"]
    assert c.device_runs == {}       # planted run.json files are not the probe's


def test_the_registry_counts_the_census_and_keeps_what_an_earlier_one_counted(tree: Path) -> None:
    device = {"private/generated/s4/run/a.json": {"IDirect3DDevice9::Present", "IDirect3DDevice9::Clear"},
              "private/generated/s4/run/b.json": {"IDirect3DDevice9::Clear"}}
    reg = M.build_registry(tree, None, disk=M.disk_runs(tree), retire_missing=False, device=device)
    assert reg["device_table_census"]["reached"] == {"IDirect3DDevice9::Clear": 2, "IDirect3DDevice9::Present": 1}
    assert reg["device_table_census"]["kept_from_earlier_census"] == []
    assert reg["device_table_census"]["runs_indexed"] == 2
    # the run index lost a run: its names stay counted
    later = M.build_registry(tree, reg, disk=M.disk_runs(tree), retire_missing=False,
                             device={"private/generated/s4/run/b.json": {"IDirect3DDevice9::Clear"}})
    assert later["device_table_census"]["kept_from_earlier_census"] == ["IDirect3DDevice9::Present"]
    assert M.census_names(later) == {"IDirect3DDevice9::Clear", "IDirect3DDevice9::Present"}
    # no census given: the previous record is carried forward
    assert M.build_registry(tree, later, disk=M.disk_runs(tree), retire_missing=False)[
        "device_table_census"] == later["device_table_census"]


def test_the_census_enters_the_denominator_and_what_the_scan_missed() -> None:
    registry = {"reached": {}, "kept_from_earlier_counts": {},
                "device_table_census": {"reached": {"IDirect3DDevice9::CreateTexture": 3},
                                        "kept_from_earlier_census": ["IDirect3DDevice9::CreateCubeTexture"]}}
    den, _ = M.derive(inputs(registry=registry, supplement={"rows": []}))
    assert den["from_the_guest_run_census"] == ["IDirect3DDevice9::CreateCubeTexture",
                                                "IDirect3DDevice9::CreateTexture"]
    assert {"IDirect3DDevice9::CreateCubeTexture", "IDirect3DDevice9::CreateTexture"} <= set(den["called"])
    assert den["called_by_the_run_but_missed_by_the_scan"] == ["IDirect3DDevice9::CreateCubeTexture",
                                                               "IDirect3DDevice9::CreateTexture"]


def test_the_gate_fails_a_census_name_outside_the_denominator_or_a_record_it_cannot_read() -> None:
    receipt = json.loads((ROOT / M.RECEIPT).read_text())
    called = set(receipt["called"])
    empty = M.Census([], {}, {}, {}, {})
    here = {M.RECEIPT: receipt, M.REGISTRY: json.loads((ROOT / M.REGISTRY).read_text()),
            M.SUPPLEMENT: json.loads((ROOT / M.SUPPLEMENT).read_text())}
    out = G.problems(ROOT, head=here, disk={}, census=M.Census(
        [], {}, {"private/generated/x.json": {"IDirect3DDevice9::NotCountedByM7"} | set(sorted(called)[:1])}, {}, {}))
    assert any("x.json (the census of every game run, its device table) reached "
               "IDirect3DDevice9::NotCountedByM7," in p for p in out), out
    out = G.problems(ROOT, head=here, disk={}, census=M.Census([], {}, {}, {"private/generated/t.json": "torn"}, {}))
    assert any("t.json cannot be counted by the census of every game run: torn" in p for p in out), out
    assert not any("census of every game run" in p for p in G.problems(ROOT, head=here, disk={}, census=empty))


# ---- several production runs (the contract of 2026-10-09 with tools/s5/m7_synthetic.py)

def second_run(root: Path, mutate=None) -> dict:
    """A second production run beside production_tree's: rec2, dxvk2, sdlgpu2 copied from the
    first, `mutate(docs)` applied to {counts, reports}, its block as production_receipt writes it."""
    import shutil
    for a, b in (("rec", "rec2"), ("dxvk", "dxvk2"), ("sdlgpu", "sdlgpu2")):
        shutil.copytree(root / a, root / b)
    docs = {"counts": json.loads((root / "rec2/counts.json").read_text()),
            "reports": {h: json.loads((root / f"{h}2/report.json").read_text()) for h in M.HOSTS}}
    if mutate:
        mutate(docs)
    write(root, "rec2/counts.json", docs["counts"])
    for h in M.HOSTS:
        write(root, f"{h}2/report.json", docs["reports"][h])
    one = M.production_receipt(root, "rec2", "dxvk2", "sdlgpu2", "census.json")
    return {"name": "second", **{k: one[k] for k in ("recording", "census", "hosts", "players")}}


def runs_receipt(root: Path, *blocks: dict) -> dict:
    single = json.loads((root / M.PRODUCTION).read_text())
    first = {"name": "first", **{k: single[k] for k in ("recording", "census", "hosts", "players")}}
    doc = {**{k: v for k, v in single.items() if k not in ("recording", "census", "hosts", "players")},
           "runs": [first, *blocks]}
    write(root, M.PRODUCTION, doc)
    return doc


def drop_row(method: str):
    def f(docs):
        for h in M.HOSTS:
            docs["reports"][h]["methods"] = [r for r in docs["reports"][h]["methods"] if r["method"] != method]
    return f


def test_a_method_is_held_when_any_listed_production_run_holds_it(tmp_path, monkeypatch):
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    runs_receipt(root, second_run(root, mutate=drop_row("Present")))
    block = three_way(root, monkeypatch)
    assert block["measured"] is True
    # the second run's hosts lost Present's row; the first run holds it, so it is held
    assert block["held_methods"] == ["IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present"]


def test_a_method_only_one_production_run_counts_falls_with_that_run(tmp_path, monkeypatch):
    def add_reset(docs):
        docs["counts"]["methods"].append({"type": 1, "slot": 16, "interface": "IDirect3DDevice9",
                                          "method": "Reset", "calls": 1, "entered": 1})
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    runs_receipt(root, second_run(root, mutate=add_reset))
    block = three_way(root, monkeypatch)
    assert "IDirect3DDevice9::Reset" in block["not_held"]
    assert block["why_not_held"]["IDirect3DDevice9::Reset"].startswith(
        "the production evidence: production run 2 (second): ")


def test_the_single_run_form_is_still_read(tmp_path, monkeypatch):
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    ev = M.load_production(root)
    assert len(ev["runs"]) == 1 and ev["run"] is ev["runs"][0]
    assert three_way(root, monkeypatch)["held_methods"] == ["IDirect3DDevice9::BeginScene",
                                                           "IDirect3DDevice9::Present"]


@pytest.mark.parametrize("edit, says", [
    (lambda doc: doc.__setitem__("runs", []), "`runs` is not a nonempty list"),
    (lambda doc: doc.__setitem__("runs", ["rec"]), "`runs` is not a nonempty list"),
    (lambda doc: doc["runs"][1]["recording"]["files"].__setitem__("counts.json", "0" * 64),
     "run 2 (second): rec2/counts.json is absent or not the file the receipt pins"),
    (lambda doc: doc["runs"].append(doc["runs"][0]), "rec is named by two production runs"),
])
def test_a_runs_receipt_that_does_not_describe_its_files_reads_not_measured(tmp_path, monkeypatch, edit, says):
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    doc = runs_receipt(root, second_run(root))
    edit(doc)
    write(root, M.PRODUCTION, doc)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False and says in block["why_not_measured"], block["why_not_measured"]


def test_every_production_run_must_be_made_from_the_trees_sources(tmp_path, monkeypatch):
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    runs_receipt(root, second_run(root))
    present = json.loads((root / "rec2/present-run.json").read_text())
    present["current"]["runtime_sha256"] = "q" * 64
    sha = write(root, "rec2/present-run.json", present)
    doc = json.loads((root / M.PRODUCTION).read_text())
    doc["runs"][1]["recording"]["files"]["present-run.json"] = sha
    write(root, M.PRODUCTION, doc)
    block = three_way(root, monkeypatch)
    assert block["measured"] is False and "production: the runtime digest" in block["why_not_measured"]
    # two runs pinning one source at different sha256s hold neither
    tree: dict = {}
    M._merge_pins(tree, {"host/player/player.cpp": "a"})
    M._merge_pins(tree, {"host/player/player.cpp": "b", "x": "c"})
    assert tree == {"host/player/player.cpp": None, "x": "c"}


def test_the_row_loss_falsification_takes_the_row_from_every_production_run_that_counts_it(tmp_path, monkeypatch):
    """With several production runs, a method any one of them holds is held, so the gate's
    falsification takes the method's row out of every run that counts it, and the block then
    derives it not held."""
    root = production_tree(tmp_path)
    synthetic_receipt(root)
    runs_receipt(root, second_run(root))
    called = ("IDirect3DDevice9::BeginScene", "IDirect3DDevice9::Present")
    block = three_way(root, monkeypatch, called=called)
    assert block["held_methods"] == list(called)
    receipt = {"inputs": {"three_way": {w: {"path": rel, "sha256": hashlib.sha256((root / rel).read_bytes()).hexdigest()}
                                        for w, rel in (("production", M.PRODUCTION), ("synthetic", M.SYNTHETIC))}},
               "three_way_numerator": block}
    monkeypatch.setattr(G, "ROOT", root)
    got: dict = {}
    G.three_way_cases(receipt, lambda name, changes, expect, **kw: got.setdefault(name, (changes, expect)),
                      lambda obj: (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode(),
                      lambda d, f: (lambda c: (f(c), c)[1])(copy.deepcopy(d)))
    changes, expect = got["a host's report lost the row of a method the recording holds"]
    assert sorted(k for k in changes if k.endswith("report.json")) == ["dxvk/report.json", "dxvk2/report.json"]
    for rel, data in changes.items():
        (root / rel).write_bytes(data)
    after = three_way(root, monkeypatch, called=called)
    assert after["measured"] is True and expect.removeprefix("the receipt holds ") in after["not_held"]
    assert "a pin of the production evidence changed" in got


def test_a_torn_index_is_refused_at_a_push_and_rebuilt_by_a_build(indexed: Path) -> None:
    tree = indexed / M.INDEX_DIR / "tree.tsv"
    tree.write_text(tree.read_text() + "D\tnot-a-number\n")
    with pytest.raises(M.Refused) as e:
        M.update_index(indexed)
    assert "--index" in str(e.value)
    c = M.update_index(indexed, build=True, by="test")
    assert c.stats["built"] is True and "private/generated/s4/run/reach.a.json" in c.device_runs
    assert M.update_index(indexed).stats["built"] is False


def test_a_run_index_whose_record_of_runs_is_gone_is_refused_until_it_is_rebuilt(indexed: Path) -> None:
    """runs.json is the append-only record of every run read; one gone while its tree is there
    would lose the names of runs deleted since, so a push refuses it and only --index rebuilds."""
    run = indexed / "private/generated/s4/run"
    probe_record(run / "gone.json", {"Clear": 1})
    M.update_index(indexed)
    (run / "gone.json").unlink()
    (indexed / M.INDEX_DIR / "runs.json").unlink()
    with pytest.raises(M.Refused) as e:
        M.update_index(indexed)
    assert "is gone while its tree is there" in str(e.value) and "--index" in str(e.value)
    with pytest.raises(M.Refused):
        M.index_run_record(indexed, run / "reach.a.json", by="a launcher")
    assert not (indexed / M.INDEX_DIR / "runs.json").exists()
    # A regeneration (a build) does not start it again either: only --index does.
    with pytest.raises(M.Refused) as e:
        M.update_index(indexed, build=True, by="tools/s4/m7_inputs.py")
    assert "is gone while its tree is there" in str(e.value)
    assert not (indexed / M.INDEX_DIR / "runs.json").exists()
    c = M.update_index(indexed, build=True, by="test", rebuild_runs=True)
    assert set(c.indexed) == {"private/generated/s4/run/reach.a.json"}
    assert M.update_index(indexed).indexed == c.indexed


def test_a_tree_made_by_another_reading_is_refused_at_a_push_and_read_anew_by_a_build(
        indexed: Path, monkeypatch) -> None:
    """The tree caches each file's verdict by its size and mtime, so it records the reading that
    made it (reader_id): a run the probe wrote in a shape the census's reading had drifted from
    is cached as not a game run, and once the reading is brought back to the probe that cache must
    not be trusted, or the run is never read again (the drift-then-fix sequence)."""
    run = indexed / "private/generated/s4/run"
    drifted = b'{\n "probe": 1, "va": "0x'
    rec = probe_record(run / "drifted.json", {"BrandNewMethod": 3}, refused=("BrandNewMethod",))
    rec.write_bytes(drifted + rec.read_bytes()[len(M.PROBE_HEADER):])
    age(rec, run)
    # A push under the old reading caches the run as not a game run.
    c = M.update_index(indexed)
    assert "private/generated/s4/run/drifted.json" not in c.device_runs
    # The reading is brought to the probe: a push refuses the old tree rather than trust it.
    monkeypatch.setattr(M, "PROBE_HEADER", drifted)
    with pytest.raises(M.Refused) as e:
        M.update_index(indexed)
    assert "another reading" in str(e.value) and "--index" in str(e.value)
    # A build reads every file anew and finds the run, with the name it reached.
    c = M.update_index(indexed, build=True, by="test")
    assert c.stats["built"] is True
    assert c.device_runs["private/generated/s4/run/drifted.json"] == {"IDirect3DDevice9::BrandNewMethod"}
    assert M.update_index(indexed).stats["built"] is False
    # Any function the verdict is read by moves the reading too, not only the header.
    before = M.reader_id()
    monkeypatch.setattr(M, "is_guest_run", lambda doc: isinstance(doc, dict))
    assert M.reader_id() != before


def test_the_gate_waits_briefly_for_a_held_index_lock_and_names_its_holder(indexed: Path) -> None:
    with M._IndexLock(indexed / M.INDEX_DIR, by="a hung build"):
        started = time.monotonic()
        with pytest.raises(M.Refused) as e:
            M.update_index(indexed, by="check_m7_inputs.py", lock_wait=0.3)
        assert time.monotonic() - started < 5
    assert "a hung build" in str(e.value) and "stayed locked" in str(e.value)
    assert M.GATE_LOCK_WAIT_SECONDS <= 120 < M.LOCK_WAIT_SECONDS
    assert M.update_index(indexed, lock_wait=0.3).stats["built"] is False


def test_a_live_sessions_record_the_census_cannot_read_is_preserved_before_anything_else() -> None:
    assert M.LIVE_SESSIONS in M.UNREADABLE_REMEDY and "private/evidence/" in M.UNREADABLE_REMEDY
    assert "never only delete" in M.UNREADABLE_REMEDY


PROBE_TEXT = (ROOT / M.PROBE_WRITER).read_text(encoding="utf-8")


def test_the_census_reads_the_record_the_probe_writes() -> None:
    """PROBE_HEADER and the keys is_guest_run and device_table_names read are the probe's: the
    first line it writes to the record file, its WinMain section and its device table."""
    assert M.probe_writer_problems(ROOT) == []
    lits = M.c_string_literals(PROBE_TEXT)
    opened = PROBE_TEXT.index("out = fopen(json_path")
    first = next(v for k, v in lits if k > opened and PROBE_TEXT.rfind("fprintf(out, ", 0, k) > opened)
    assert first.startswith(M.PROBE_HEADER.decode()) and first[len(M.PROBE_HEADER):].startswith("%08x")
    # The real records the census must recognise read as guest run records with methods called.
    assert M.control_records(ROOT)[0] == json.loads((ROOT / M.REACH).read_text())["reach"]["run_json"]
    assert M.probe_control_problems(ROOT) == []
    for rel in M.control_records(ROOT):
        verdict, names = M.file_verdict(str(ROOT / rel), Path(rel).name, 0)
        assert verdict == "g" and names, rel


@pytest.mark.parametrize("old, new, says", [
    ('fprintf(out, "{\\n \\"va\\"', 'fprintf(out, "{\\n \\"probe\\": 1, \\"va\\"',
     "begins its run record with '{\\n \"probe\": 1, \"va\": \"0x'"),
    ('out = fopen(json_path', 'out = fopen(path', "cannot find where the probe opens its run record"),
    ('" \\"winmain\\": {', '" \\"main\\": {', 'the WinMain section'),
    ('" \\"d3d9\\": {', '" \\"device\\": {', "the device's section"),
    ('"],\\n   \\"implemented\\": ["', '"],\\n   \\"table\\": ["', "the device's table"),
    ('\\"calls\\": %u, \\"flags\\": ', '\\"count\\": %u, \\"flags\\": ', "interface, method and calls"),
    ('{\\"interface\\": ", n++', '{\\"iface\\": ", n++', "interface, method and calls"),
    ('if (!out) { fprintf(stderr, "crt_probe: cannot write %s\\n", json_path); break; }',
     'if (!out) { fprintf(stderr, "crt_probe: cannot write %s\\n", json_path); break; }\n'
     '            fputc(\' \', out);', "writes to its run record (fputc) before the fprintf"),
])
def test_a_probe_writing_another_shape_is_a_problem(tmp_path: Path, old: str, new: str, says: str) -> None:
    assert PROBE_TEXT.count(old) >= 1, old
    (tmp_path / "harness").mkdir()
    (tmp_path / M.PROBE_WRITER).write_text(PROBE_TEXT.replace(old, new, 1))
    found = M.probe_writer_problems(tmp_path)
    assert len(found) == 1 and says in found[0], found


def test_a_control_record_the_census_does_not_recognise_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "harness").mkdir()
    (tmp_path / M.PROBE_WRITER).write_text(PROBE_TEXT)
    write(tmp_path, M.REACH, {"reach": {"run_json": "private/generated/s4/run/reach.a.json"}})
    rec = probe_record(tmp_path / "private/generated/s4/run/reach.a.json", {"Present": 2})
    assert M.probe_control_problems(tmp_path) == []
    # a record whose first bytes moved (a key before "va"), one with no method called, and one
    # whose table is not the device's
    good = rec.read_text()
    for text, says in ((good.replace('{\n "va"', '{\n "written_by": "crt_probe",\n "va"', 1), "not a guest run record"),
                       (good.replace('"calls": 2', '"calls": 0'), "counted no call"),
                       (good.replace('"calls": 2', '"calls": "2"'), M.UNREADABLE["u"])):
        rec.write_text(text)
        os.utime(rec, ns=(1_000_000_000, 1_000_000_000))
        found = M.probe_control_problems(tmp_path)
        assert len(found) == 1 and "does not recognise private/generated/s4/run/reach.a.json" in found[0] \
            and says in found[0], found
    # A production recording's run record is held the same way.
    write(tmp_path, M.PRODUCTION, {"recording": {"dir": "private/generated/s5/rec"}})
    rec.write_text(good)
    os.utime(rec, ns=(2_000_000_000, 2_000_000_000))
    other = probe_record(tmp_path / "private/generated/s5/rec/run.json", {"Present": 1})
    other.write_text(other.read_text().replace('{\n "va"', '{\n "x": 1,\n "va"', 1))
    found = M.probe_control_problems(tmp_path)
    assert len(found) == 1 and "private/generated/s5/rec/run.json" in found[0], found


def test_the_census_self_test_plants_nothing_a_census_of_the_real_tree_counts(tmp_path: Path, monkeypatch) -> None:
    """The gate's own discovery falsifications plant runs in a small tree under TMPDIR; when
    TMPDIR is under private/generated, a census of the real tree running beside them must not
    count those runs, so the small tree carries a `.git` (a checkout, never entered)."""
    outer = tmp_path / "outer"
    scratch = outer / "private" / "generated" / "s5" / "main-tmp"
    scratch.mkdir(parents=True)
    M.update_index(outer, build=True, by="test")
    seen = {}
    real = M.update_index

    def spy(root, **kw):
        c = real(root, **kw)
        seen.setdefault("outer", real(outer, by="test"))      # the real tree's census, mid-test
        return c
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setattr(M, "update_index", spy)
    assert G.discovery_falsifications() == []
    assert seen["outer"].indexed == {} and seen["outer"].device_runs == {}
