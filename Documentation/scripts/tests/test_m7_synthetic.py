"""M7's synthetic streams (tools/s5/m7_synthetic.py, tools/s5/tests/rec_harness_m7.c) and the
per-method predicate they share with the production evidence (tools/s4/m7_inputs.py).

The user's ruling of 2026-10-04 (Documentation/decisions/2026-10-04-S5-user-rulings-2.md):
"Methods no recorded run reaches are exercised by a synthetic stream per method, so the check
can fail." These tests build the fixture with the pinned zig from the real recording device and
recorder, record every target's positive and omit streams, read them back with
tools/s5/stream_dump.py, and hold the predicate to authored runs, one field at a time. No player
runs here (a player needs a GPU); tools/s5/m7_synthetic.py runs both on the same streams.

SA2_M7_SOURCE_ROOT names another checkout to build the fixture's C sources from (default: this
tree), for a tree whose runtime is mid-edit; the targets and the predicate are this tree's.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s5"))
sys.path.insert(0, str(ROOT / "tools" / "s4"))

import m7_inputs as M  # noqa: E402
import m7_synthetic as S  # noqa: E402

SOURCE_ROOT = Path(os.environ.get("SA2_M7_SOURCE_ROOT", str(ROOT)))

# Every target the fixture writes, by family.
TARGETS = [
    ("IDirect3DDevice9::CreateIndexBuffer", "indexed", False),
    ("IDirect3DIndexBuffer9::Lock", "indexed", False),
    ("IDirect3DIndexBuffer9::Unlock", "indexed", False),
    ("IDirect3DIndexBuffer9::Release", "indexed", False),
    ("IDirect3DDevice9::DrawIndexedPrimitive", "indexed", False),
    ("IDirect3DVertexBuffer9::Release", "indexed", False),
    ("IDirect3DDevice9::SetStreamSourceFreq", "freq", False),
    ("IDirect3DDevice9::SetVertexShaderConstantB", "vsbool", False),
    ("IDirect3DDevice9::SetVertexShaderConstantI", "vsint", False),
    ("IDirect3DDevice9::SetPixelShaderConstantB", "psbool", False),
    ("IDirect3DDevice9::SetPixelShaderConstantI", "psint", False),
    ("IDirect3DDevice9::QueryInterface", "query", False),
    ("IDirect3DDevice9::TestCooperativeLevel", "query", False),
    ("d3dx9_42!D3DXSaveSurfaceToFileA", "query", False),
    ("d3dx9_42!D3DXSaveTextureToFileA", "query", False),
    ("ID3DXConstantTable::GetConstantByName", "ctabquery", False),
    ("ID3DXConstantTable::GetConstantDesc", "ctabquery", False),
    ("ID3DXConstantTable::SetBool", "ctabset", False),
    ("ID3DXConstantTable::SetInt", "ctabset", False),
    ("ID3DXConstantTable::SetFloat", "ctabset", False),
    ("ID3DXConstantTable::SetVector", "ctabset", False),
    ("ID3DXConstantTable::SetMatrix", "ctabset", False),
    ("IDirect3DDevice9::Reset", "reset", False),
    ("IDirect3DDevice9::CreateCubeTexture", "cube", False),
    ("d3dx9_42!D3DXCreateCubeTexture", "cube", False),
    ("d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx", "cube", False),
    ("IDirect3DCubeTexture9::GetCubeMapSurface", "cube", False),
    ("IDirect3DCubeTexture9::GetLevelCount", "cube", False),
    ("IDirect3DCubeTexture9::GetLevelDesc", "cube", False),
    ("d3dx9_42!D3DXCreateVolumeTexture", "volume", False),
    ("d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx", "volume", False),
    ("IDirect3DVolumeTexture9::GetLevelCount", "volume", False),
    ("IDirect3DVolumeTexture9::GetLevelDesc", "volume", False),
]
# F7 and F8: the cube and volume families' targets, which have no effect on the picture.
CUBE_VOLUME = [t for t, f, _ in TARGETS if f in ("cube", "volume")]


@pytest.fixture(scope="module")
def fixture(tmp_path_factory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("m7")
    record = S.build(out, SOURCE_ROOT)
    return out, record


def test_the_fixture_writes_every_target_of_every_family(fixture) -> None:
    out, record = fixture
    assert S.harness_targets(ROOT / record["binary"]["path"]) == TARGETS
    assert {f for _, f, prepared in TARGETS if not prepared} == set(S.APPLIED)
    assert {f for _, f, prepared in TARGETS if prepared} == set(S.PREPARED)
    # The fixture's record pins what it was built from: the real device, recorder and the
    # stand-in runtime, by path in the checkout, and the tools that made its generated inputs.
    for src in ("runtime/sa2_d3d9.c", "runtime/sa2_guest_call.c", "host/sa2_present_rec.c",
                "tools/s5/tests/rec_harness.c", "tools/s5/tests/rec_harness_m7.c", "host/sa2_stream.h"):
        assert src in record["sources"], src
    assert sorted(record["tools"]) == sorted(S.GENERATORS)


def counted(case: Path) -> dict[str, int]:
    rows = M.recorder_rows(json.loads((case / "rec.counts.json").read_text()))
    return {n: r["calls"] for n, r in rows.items() if r["calls"]}


@pytest.mark.parametrize("target", [t for t, _, _ in TARGETS])
def test_each_target_is_in_its_positive_stream_and_left_out_of_its_omit_stream(fixture, target) -> None:
    out, record = fixture
    binary = ROOT / record["binary"]["path"]
    runs = {}
    for mode in ("positive", "omit"):
        case = out / "cases" / target.replace(":", "-").replace("!", "-") / mode
        exit_ = S.record_case(binary, target, mode, case)["exit"]
        assert S.scan_case(case) == {"problems": []}, mode
        answers = json.loads((case / "answers.json").read_text())
        assert (answers["target"], answers["mode"]) == (target, mode)
        # 3: a device answer the harness checked is not Direct3D 9's (the cube and volume
        # cases' checks, held in test_each_cube_and_volume_case_holds_the_devices_answers...).
        assert exit_ == (3 if answers.get("checks_failed") else 0), (mode, exit_)
        runs[mode] = case
    pos, omit = counted(runs["positive"]), counted(runs["omit"])
    assert pos.get(target, 0) > 0 and omit.get(target, 0) == 0
    # The omit stream leaves out the target and what needs an object only it makes, nothing more.
    dropped = {n for n in pos if n not in omit}
    assert target in dropped
    assert all(omit[n] <= pos[n] for n in omit), (pos, omit)
    # Both modes predict the positive stream's picture, so an omitted effect fails there.
    assert (runs["positive"] / "expect.tsv").read_text() == (runs["omit"] / "expect.tsv").read_text()
    assert not list(runs["positive"].parent.rglob("counts.json"))


DEPENDENTS = {
    "IDirect3DDevice9::CreateIndexBuffer": {"IDirect3DIndexBuffer9::Lock", "IDirect3DIndexBuffer9::Unlock",
                                            "IDirect3DIndexBuffer9::Release", "IDirect3DDevice9::SetIndices",
                                            "IDirect3DDevice9::DrawIndexedPrimitive"},
    "IDirect3DIndexBuffer9::Lock": {"IDirect3DIndexBuffer9::Unlock"},
    "ID3DXConstantTable::GetConstantByName": {"ID3DXConstantTable::GetConstantDesc"},
    # An outer constant-table setter nests through the guest device (runtime/sa2_d3d9.c
    # table_set): its device record goes with it.
    "ID3DXConstantTable::SetBool": {"IDirect3DDevice9::SetPixelShaderConstantB"},
    "ID3DXConstantTable::SetInt": {"IDirect3DDevice9::SetPixelShaderConstantI"},
    "ID3DXConstantTable::SetFloat": {"IDirect3DDevice9::SetPixelShaderConstantF"},
    "ID3DXConstantTable::SetVector": {"IDirect3DDevice9::SetPixelShaderConstantF"},
    "ID3DXConstantTable::SetMatrix": {"IDirect3DDevice9::SetPixelShaderConstantF"},
    # A cube or volume texture's queries need the texture its creator makes.
    "IDirect3DDevice9::CreateCubeTexture": {"IDirect3DCubeTexture9::GetLevelCount", "IDirect3DCubeTexture9::GetLevelDesc"},
    "d3dx9_42!D3DXCreateCubeTexture": {"IDirect3DCubeTexture9::GetLevelCount", "IDirect3DCubeTexture9::GetLevelDesc"},
    "d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx": {"IDirect3DCubeTexture9::GetLevelCount",
                                                         "IDirect3DCubeTexture9::GetLevelDesc"},
    # A face's surface is what the render target is set to, cleared through and released.
    "IDirect3DCubeTexture9::GetCubeMapSurface": {"IDirect3DDevice9::GetRenderTarget", "IDirect3DDevice9::SetRenderTarget",
                                                 "IDirect3DDevice9::Clear", "IDirect3DSurface9::Release"},
    "d3dx9_42!D3DXCreateVolumeTexture": {"IDirect3DVolumeTexture9::GetLevelCount", "IDirect3DVolumeTexture9::GetLevelDesc"},
    "d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx": {"IDirect3DVolumeTexture9::GetLevelCount",
                                                           "IDirect3DVolumeTexture9::GetLevelDesc"},
}


def test_an_omit_stream_drops_only_the_target_and_its_dependents(fixture) -> None:
    out, record = fixture
    binary = ROOT / record["binary"]["path"]
    for target, _, _ in TARGETS:
        counts = {}
        for mode in ("positive", "omit"):
            case = out / "deps" / target.replace(":", "-").replace("!", "-") / mode
            S.record_case(binary, target, mode, case)
            counts[mode] = counted(case)
        changed = {n for n in counts["positive"] if counts["omit"].get(n) != counts["positive"][n]}
        assert changed == {target} | DEPENDENTS.get(target, set()), target


def test_the_fixture_refuses_a_counts_file_named_counts_json(fixture, tmp_path) -> None:
    """A directory holding a recorder counts.json is a recorded run to M7's registry
    (m7_inputs.run_dir_named_by): a synthetic case named so would join M7's denominator."""
    _, record = fixture
    env = {k: v for k, v in os.environ.items() if not k.startswith("SA2_")}
    env.update(SA2_REC_TRACE=str(tmp_path / "stream.sa2s"), SA2_REC_COUNTS=str(tmp_path / "counts.json"))
    p = subprocess.run([str(ROOT / record["binary"]["path"]), "--target", TARGETS[0][0], "--out", str(tmp_path)],
                       env=env, capture_output=True, text=True)
    assert p.returncode == 2 and "rec.counts.json" in p.stderr
    assert not (tmp_path / "counts.json").exists() and not (tmp_path / "stream.sa2s").exists()


# ------------------------------------------------------------------ F7 and F8: cube and volume

def answers(case: Path) -> dict:
    return json.loads((case / "answers.json").read_text())


def dds_dxt1(cube: bool, depth: int = 0) -> bytes:
    """The DXT1 DDS rec_harness_m7.c authors, written here from its description: 4x4, one level,
    caps COMPLEX|TEXTURE, six faces (CUBEMAP and every face bit) or `depth` slices (VOLUME and
    DDSD_DEPTH), each block a colour pair and index word of its own."""
    h = [0] * 32
    h[0], h[1] = 0x20534444, 124
    h[2] = 0x00081007 | (0 if cube else 0x00800000)
    h[3], h[4], h[5], h[6] = 4, 4, 8, 0 if cube else depth
    h[19], h[20], h[21] = 32, 4, 0x31545844
    h[27], h[28] = 0x1008, 0x0000FE00 if cube else 0x00200000
    blob = b"".join(w.to_bytes(4, "little") for w in h)
    for i in range(6 if cube else depth):
        pair = ((0xF800 - 0x0800 * i) & 0xFFFF) | ((0x001F + i) << 16)
        blob += pair.to_bytes(4, "little") + (0x1B1B1B1B ^ (i * 0x01010101)).to_bytes(4, "little")
    return blob


@pytest.mark.parametrize("target", CUBE_VOLUME)
def test_each_cube_and_volume_case_holds_the_devices_answers_not_counts_alone(fixture, target) -> None:
    """Each F7 and F8 positive stream checks what the real device answered (answers.json
    "checks"), and the harness exits 3 when one is not what Direct3D 9 or D3DX gives. The one
    known divergence is the cube level description's Type, which the GetLevelDesc case holds
    to D3DRTYPE_SURFACE; every other check passes as the device stands."""
    out, record = fixture
    binary = ROOT / record["binary"]["path"]
    case = out / "answers" / target.replace(":", "-").replace("!", "-")
    exit_ = S.record_case(binary, target, "positive", case)["exit"]
    a = answers(case)
    assert a["checks"], target
    failed = [c for c in a["checks"] if c[2] != c[3]]
    assert a["checks_failed"] == len(failed) and exit_ == (3 if failed else 0)
    assert all(c[0] == "D3DSURFACE_DESC.Type" and c[2] == 1 for c in failed), failed
    assert not failed or target == "IDirect3DCubeTexture9::GetLevelDesc"
    names = {c[0] for c in a["checks"]}
    # The target's own behaviour is what its case checks.
    own = {"IDirect3DDevice9::CreateCubeTexture": {"IDirect3DDevice9::CreateCubeTexture HRESULT",
                                                   "CreateCubeTexture writes an object"},
           "d3dx9_42!D3DXCreateCubeTexture": {"D3DXCreateCubeTexture HRESULT", "D3DXCreateCubeTexture writes an object"},
           "d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx": {"D3DXIMAGE_INFO.ResourceType", "texsrc.parsed",
                                                                "texsrc.hashed_bytes"},
           "IDirect3DCubeTexture9::GetCubeMapSurface": {"GetCubeMapSurface writes a surface of the face's own",
                                                        "GetCubeMapSurface answers +X level 0 with the same interface"},
           "IDirect3DCubeTexture9::GetLevelCount": {"IDirect3DCubeTexture9::GetLevelCount"},
           "IDirect3DCubeTexture9::GetLevelDesc": {"D3DSURFACE_DESC.Type", "D3DSURFACE_DESC.Width"},
           "d3dx9_42!D3DXCreateVolumeTexture": {"D3DXCreateVolumeTexture HRESULT", "IDirect3DVolumeTexture9::GetLevelCount"},
           "d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx": {"D3DXIMAGE_INFO.Depth", "texsrc.file_caps2"},
           "IDirect3DVolumeTexture9::GetLevelCount": {"IDirect3DVolumeTexture9::GetLevelCount"},
           "IDirect3DVolumeTexture9::GetLevelDesc": {"D3DVOLUME_DESC.Type", "D3DVOLUME_DESC.Depth"}}[target]
    assert own <= names, own - names
    assert target not in M.PICTURE_EFFECT       # its falsifier is the recorder's count


def test_the_cube_and_volume_answers_are_the_ones_d3d9_and_d3dx_give(fixture) -> None:
    """Literal answers: the D3DX rule for MipLevels 0 (a complete chain, 4 2 1), a volume's
    level 1 (2x2x1), the files' D3DXIMAGE_INFO, and the device's hash of the blob it read,
    which is the authored DDS's (the upload's readback)."""
    out, record = fixture
    binary = ROOT / record["binary"]["path"]

    def got(target: str) -> dict:
        case = out / "literal" / target.replace(":", "-").replace("!", "-")
        S.record_case(binary, target, "positive", case)
        return {(c[0], c[1]): (c[2], c[3]) for c in answers(case)["checks"]}, answers(case)

    dx, _ = got("d3dx9_42!D3DXCreateCubeTexture")
    assert dx[("IDirect3DCubeTexture9::GetLevelCount", -1)] == (3, 3)
    assert [dx[("D3DSURFACE_DESC.Width", lv)] for lv in range(3)] == [(4, 4), (2, 2), (1, 1)]
    vol, _ = got("IDirect3DVolumeTexture9::GetLevelDesc")
    assert [vol[(f, 1)] for f in ("D3DVOLUME_DESC.Width", "D3DVOLUME_DESC.Height", "D3DVOLUME_DESC.Depth",
                                  "D3DVOLUME_DESC.Type")] == [(2, 2), (2, 2), (1, 1), (2, 2)]
    for target, blob, info in (
            ("d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx", dds_dxt1(True), [4, 4, 1, 1, 0x31545844, 5, 4]),
            ("d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx", dds_dxt1(False, 2), [4, 4, 2, 1, 0x31545844, 4, 4])):
        checks, doc = got(target)
        fields = ("Width", "Height", "Depth", "MipLevels", "Format", "ResourceType", "ImageFileFormat")
        assert [checks[(f"D3DXIMAGE_INFO.{f}", -1)] for f in fields] == [(v, v) for v in info]
        assert checks[("texsrc.src_len", -1)] == (len(blob), len(blob))
        assert doc["texsrc_sha256"] == hashlib.sha256(blob).hexdigest()
    faces, _ = got("IDirect3DCubeTexture9::GetCubeMapSurface")
    assert [faces[("GetCubeMapSurface writes a surface of the face's own", f)] for f in range(6)] == [(1, 1)] * 6


@pytest.mark.parametrize("target", CUBE_VOLUME)
def test_a_cube_or_volume_omit_stream_fails_the_shared_predicate_on_the_count(fixture, target) -> None:
    """The falsifier: the omit stream's recorder counted no call of the target, which is the
    shared predicate's `count` clause (m7_inputs.method_problems), and its harness checked
    nothing the omitted call would have answered."""
    out, record = fixture
    binary = ROOT / record["binary"]["path"]
    case = out / "omitted" / target.replace(":", "-").replace("!", "-")
    assert S.record_case(binary, target, "omit", case) == {"exit": 0}
    rows = M.recorder_rows(json.loads((case / "rec.counts.json").read_text()))
    assert M.method_problems(target, rows, {}) == [("count", f"the recorder counted no call of {target}")]
    assert answers(case)["checks_failed"] == 0


def test_a_positive_stream_whose_fixture_found_a_wrong_answer_holds_nothing() -> None:
    """The harness's exit 3 (a device answer that is not Direct3D 9's) fails the positive stream's
    recorder clause, so a case whose counts all agree is still not held."""
    target = "IDirect3DCubeTexture9::GetLevelDesc"

    def as_cube_desc(r: dict) -> dict:
        r = json.loads(json.dumps(r))
        for m in r["counts"]["methods"]:
            m.update(type=3, slot=17, interface="IDirect3DCubeTexture9", method="GetLevelDesc")
        for h in r["hosts"].values():
            for m in h["report"]["methods"]:
                m.update(type="CubeTexture", slot=17, method="GetLevelDesc")
        return r
    positive = as_cube_desc(run(1))
    assert verdict(positive, as_cube_desc(run(0)), target)["held"] is True
    positive["harness_exit"] = 3
    v = verdict(positive, as_cube_desc(run(0)), target)
    assert v["held"] is False and "the fixture that recorded it exited 3" in v["why"], v["why"]


def test_a_synthetic_receipt_registers_no_recorded_run(tmp_path) -> None:
    """The receipt cites every case directory; none holds a counts.json, so none is a run of
    the game. One that does is registered, and its case is held by nothing."""
    case = tmp_path / "private/generated/syn/cases/t/positive"
    case.mkdir(parents=True)
    counts = {"schema": M.COUNTS_SCHEMA, "writer": M.RECORDER, "methods": [], "entries": []}
    (case / "rec.counts.json").write_text(json.dumps(counts))
    receipts = tmp_path / "Documentation/receipts"
    receipts.mkdir(parents=True)
    (receipts / "s5-m7-synthetic-v1.json").write_text(json.dumps(
        {"cases": [{"target": "t", "positive": {"dir": "private/generated/syn/cases/t/positive"}}]}))
    assert M.receipt_named_runs(tmp_path, receipts) == {}
    (case / "counts.json").write_text(json.dumps(counts))
    assert M.receipt_named_runs(tmp_path, receipts) == {
        "private/generated/syn/cases/t/positive": {"Documentation/receipts/s5-m7-synthetic-v1.json"}}
    loaded = M.load_case(tmp_path, {"target": "t", "positive": {"dir": "private/generated/syn/cases/t/positive"},
                                    "omit": {"dir": "private/generated/syn/cases/t/omit"}})
    assert "private/generated/syn/cases/t/positive holds a counts.json: a synthetic case would be a recorded run" \
        in loaded["problems"]
    assert M.case_verdict(loaded, "t")["held"] is False


# ------------------------------------------------------------------ the predicate, authored

CHECKER = "c" * 64
STREAM = "5" * 64
DRAWS = {"DrawPrimitiveUP": {"TRIANGLESTRIP": 1}}


def host(name: str, calls: int, *, pixels_fail: int = 0) -> dict:
    report = {"complete": True, "pass": not pixels_fail, "exit_status": 1 if pixels_fail else 0, "stage": 3,
              "options": {"stats_only": False}, "stream_records_read_to_end": True, "records": {"end": 1},
              "stop": "end of stream", "errors": 0,
              "calls": {"handled": 3, "ignored_by_design": 0, "skipped_by_stage": 0, "not_handled": 0},
              "stream_sha256": STREAM, "host_identity": {"d3d9_host": name}, "presents": 1,
              "methods": [{"type": "Device", "slot": 82, "method": "DrawIndexedPrimitive", "handled": calls}] if calls
              else [],
              "allowed_host_failures": {"rules": {}, "calls": []}, "host_failures": {"count": 0, "failed_like_guest": 0},
              "present_failures": 0,
              "expectations": {"file": "expect.tsv", "selected": 2, "pass": 2 - pixels_fail, "fail": pixels_fail}}
    h = {"report": report, "exit": 1 if pixels_fail else 0, "checked_by": CHECKER,
         "check": {"pass": not pixels_fail, "stream_sha256": STREAM, "stage": 3, "presents": 1,
                   "failures": ["expect: 1 expectation(s) failed"] if pixels_fail else []}}
    if name == "sdlgpu":
        h["device"] = {"complete": True, "failure": None, "not_implemented": {"count": 0},
                       "draws": {"drawn": 1, "total": 1, "fault_uncounted": 0, "accepted": 1, "unmapped_count": 0,
                                 "backend_failed_count": 0, "suppressed_count": 0, "encoded": DRAWS, "unmapped": {},
                                 "backend_failed": {}, "fault_suppressed": {}, "refused": {}, "empty": {},
                                 "failures": [], "per_frame": [[1, 1, 1]], "after_last_present": 0}}
    return h


def run(calls: int, *, pixels_fail: tuple[str, ...] = (), scan: tuple[str, ...] = ()) -> dict:
    counts = {"schema": M.COUNTS_SCHEMA, "writer": M.RECORDER, "counting": True, "stream": "closed",
              "stream_records": {"note": 0}, "notes": {},
              "methods": [{"type": 1, "slot": 82, "interface": "IDirect3DDevice9", "method": "DrawIndexedPrimitive",
                           "calls": calls, "entered": calls}], "entries": []}
    return {"dir": "case", "counts": counts, "stream_sha256": STREAM, "checker": CHECKER, "harness_exit": 0,
            "scan": list(scan),
            "census": {"stream_sha256": STREAM, "stage": 3, "frames": 1,
                       "draws": {"total": 1, "skipped_no_shader_route": DRAWS, "refused": {}, "empty": {}}},
            "hosts": {h: host(h, calls, pixels_fail=int(h in pixels_fail)) for h in M.HOSTS}}


EFFECT = "IDirect3DDevice9::DrawIndexedPrimitive"


def verdict(positive: dict, omit: dict, target: str = EFFECT, problems=()) -> dict:
    return M.case_verdict({"target": target, "problems": list(problems), "positive": positive, "omit": omit}, target)


def test_a_method_with_an_effect_is_held_when_its_omit_stream_fails_on_both_hosts_pixels() -> None:
    assert verdict(run(1), run(0, pixels_fail=("dxvk", "sdlgpu")))["held"] is True


@pytest.mark.parametrize("positive, omit, why", [
    (run(1, pixels_fail=("sdlgpu",)), run(0, pixels_fail=("dxvk", "sdlgpu")), "the positive stream: sdlgpu"),
    (run(1, scan=("record 7 has no blob",)), run(0, pixels_fail=("dxvk", "sdlgpu")), "the stream read back"),
    (run(1), run(0), "the omit stream does not fail where it must: ['pixels/dxvk', 'pixels/sdlgpu']"),
    (run(1), run(0, pixels_fail=("dxvk",)), "the omit stream does not fail where it must: ['pixels/sdlgpu']"),
    (run(1), run(1, pixels_fail=("dxvk", "sdlgpu")), "does not fail where it must: ['count']"),
    (run(1), run(0, pixels_fail=("dxvk", "sdlgpu"), scan=("seq gap",)), "fails where leaving"),
])
def test_each_clause_of_a_synthetic_case_can_fail(positive, omit, why) -> None:
    v = verdict(positive, omit)
    assert v["held"] is False and why in v["why"], v["why"]


def test_a_method_with_no_effect_is_held_when_its_omit_stream_fails_on_the_count_alone() -> None:
    target = "IDirect3DDevice9::DrawIndexedPrimitive"
    # As a no-effect target (the predicate's table names it as having one, so a stand-in name):
    rename = "IDirect3DIndexBuffer9::Release"

    def as_release(r: dict) -> dict:
        r = json.loads(json.dumps(r))
        for m in r["counts"]["methods"]:
            m.update(type=8, slot=2, interface="IDirect3DIndexBuffer9", method="Release")
        for h in r["hosts"].values():
            for m in h["report"]["methods"]:
                m.update(type="IndexBuffer", slot=2, method="Release")
        return r
    assert rename not in M.PICTURE_EFFECT and target in M.PICTURE_EFFECT
    assert verdict(as_release(run(1)), as_release(run(0)), rename)["held"] is True
    # A no-effect omit stream that also fails a host's pixels fails where leaving it out should not reach.
    v = verdict(as_release(run(1)), as_release(run(0, pixels_fail=("dxvk",))), rename)
    assert v["held"] is False and "fails where leaving" in v["why"]


def as_no_effect(r: dict) -> dict:
    """`r` with its one method renamed IDirect3DIndexBuffer9::Release, a target with no effect on
    the picture, whose falsifier is the recorder's count alone."""
    r = json.loads(json.dumps(r))
    for m in r["counts"]["methods"]:
        m.update(type=8, slot=2, interface="IDirect3DIndexBuffer9", method="Release")
    for h in r["hosts"].values():
        for m in h["report"]["methods"]:
            m.update(type="IndexBuffer", slot=2, method="Release")
    return r


NO_EFFECT = "IDirect3DIndexBuffer9::Release"


@pytest.mark.parametrize("spoil, why", [
    # The omit stream still makes the call, and the recorder's file is spoilt some other way: the
    # count clause is the falsifier, so another recorder fault does not stand in for it.
    (lambda r: r["counts"]["notes"].update(lock=1), "does not fail where it must: ['count']"),
    (lambda r: r.update(harness_exit=139), "does not fail where it must: ['count']"),
])
def test_an_omit_stream_that_still_makes_the_call_is_no_falsifier_whatever_else_fails(spoil, why) -> None:
    omit = as_no_effect(run(1))
    spoil(omit)
    v = verdict(as_no_effect(run(1)), omit, NO_EFFECT)
    assert v["held"] is False and why in v["why"], v["why"]


@pytest.mark.parametrize("spoil", [
    lambda r: r["counts"]["notes"].update(lock=1),
    lambda r: r.update(harness_exit=139),
    lambda r: r["counts"].update(stream="open"),
])
def test_an_omit_stream_without_the_call_must_fail_on_the_count_and_nothing_else(spoil) -> None:
    omit = as_no_effect(run(0))
    spoil(omit)
    v = verdict(as_no_effect(run(1)), omit, NO_EFFECT)
    assert v["held"] is False and "fails where leaving" in v["why"], v["why"]


def test_a_positive_stream_whose_recorder_counted_no_call_holds_nothing() -> None:
    v = verdict(run(0), run(0, pixels_fail=("dxvk", "sdlgpu")))
    assert v["held"] is False and "the recorder counted no call of IDirect3DDevice9::DrawIndexedPrimitive" in v["why"]


def test_set_stream_source_freq_is_held_on_both_hosts_pixels_not_on_its_count() -> None:
    """Instancing changes the picture (rec_harness_m7.c's freq family draws a second instance only
    the frequencies make): an omit stream that fails only on the count shows nothing about the hosts."""
    assert "IDirect3DDevice9::SetStreamSourceFreq" in M.PICTURE_EFFECT

    def as_freq(r: dict) -> dict:
        r = json.loads(json.dumps(r))
        for m in r["counts"]["methods"]:
            m.update(slot=102, method="SetStreamSourceFreq")
        for h in r["hosts"].values():
            for m in h["report"]["methods"]:
                m.update(slot=102, method="SetStreamSourceFreq")
        return r
    target = "IDirect3DDevice9::SetStreamSourceFreq"
    v = verdict(as_freq(run(1)), as_freq(run(0)), target)
    assert v["held"] is False and "['pixels/dxvk', 'pixels/sdlgpu']" in v["why"]
    # The second host that cannot draw stream 1's per-instance data fails the positive stream.
    v = verdict(as_freq(run(1, pixels_fail=("sdlgpu",))), as_freq(run(0, pixels_fail=("dxvk", "sdlgpu"))), target)
    assert v["held"] is False and v["why"].startswith("the positive stream: sdlgpu")
    assert verdict(as_freq(run(1)), as_freq(run(0, pixels_fail=("dxvk", "sdlgpu"))), target)["held"] is True


RESET = "IDirect3DDevice9::Reset"


def as_reset(r: dict) -> dict:
    r = json.loads(json.dumps(r))
    for m in r["counts"]["methods"]:
        m.update(slot=16, method="Reset")
    for h in r["hosts"].values():
        for m in h["report"]["methods"]:
            m.update(slot=16, method="Reset")
    return r


def refused(r: dict, *, hosts=M.HOSTS, named="Device::Reset") -> dict:
    """`r` as both players report a stream whose Reset they refuse: every call of it not handled,
    named among the not-handled values, the run failed for that alone."""
    r = as_reset(r)
    for name in hosts:
        h = r["hosts"][name]
        h["exit"] = 4
        rep = h["report"]
        rep.update({"pass": False, "exit_status": 4})
        rep["calls"]["not_handled"] = 1
        rep["not_handled_values"] = {"count": 1, "first": [f"seq 17 {named}: Reset to 32x32 format 21, other "
                                                          "presentation parameters than CreateDevice's"]}
        for m in rep["methods"]:
            m.update(handled=0, not_handled=1)
        h["check"] = {"pass": False, "stream_sha256": STREAM, "stage": 3, "presents": 1,
                      "failures": ["replay: 1 call(s) not handled"]}
    return r


def test_a_reset_is_held_only_when_both_players_refuse_a_reset_to_other_parameters_by_name() -> None:
    """A Reset to the creation parameters is no host call: a player that ignored every Reset would
    pass its positive stream. Its case also carries a Reset to 32x32, which both must refuse."""
    pos, omit = as_reset(run(1)), as_reset(run(0))
    assert RESET in M.REFUSED_VARIANTS and RESET not in M.PICTURE_EFFECT

    def v(variants: dict | None) -> dict:
        return M.case_verdict({"target": RESET, "problems": [], "positive": pos, "omit": omit,
                               "variants": variants or {}}, RESET)
    assert v({"other-pp": refused(run(1))})["held"] is True
    assert "the case has no other-pp run" in v(None)["why"]
    # a player that accepted the other parameters (it planned Reset as no host call)
    why = v({"other-pp": as_reset(run(1))})["why"]
    assert "the other-pp stream is not refused by both players" in why and "dxvk: " in why
    # one host refuses, the other does not
    assert "sdlgpu: " in v({"other-pp": refused(run(1), hosts=("dxvk",))})["why"]
    # refused, but for another reason than the Reset
    assert "no not-handled value names Device::Reset" in v({"other-pp": refused(run(1), named="Device::Clear")})["why"]
    # the variant's own stream does not read back
    assert "the stream read back" in v({"other-pp": refused(run(1, scan=("seq gap",)))})["why"]


def test_a_receipt_under_documentation_receipts_needs_the_permanent_case_directory(tmp_path, capsys) -> None:
    """A committed receipt pins every case file; cases under a unit's scratch directory are
    deleted with it, and the gate then reads the evidence as absent."""
    rc = S.main(["--out", str(tmp_path / "cases"), "--players", str(tmp_path),
                 "--receipt", str(ROOT / M.SYNTHETIC)])
    assert rc == 2 and S.PERMANENT_OUT in capsys.readouterr().err


def test_a_case_whose_files_are_not_the_ones_pinned_is_held_by_nothing() -> None:
    v = verdict(run(1), run(0, pixels_fail=("dxvk", "sdlgpu")), problems=["case/stream.sa2s is absent or not the file "
                                                                          "the receipt pins"])
    assert v == {"held": False, "why": "case/stream.sa2s is absent or not the file the receipt pins",
                 "positive": [], "omit": []}


def test_a_reset_variant_one_host_accepts_is_no_refusal_whatever_it_names() -> None:
    """Each clause of refused_variant_problems on its own: the run exits 0 and passes, or leaves a
    call of the Reset handled, while every other clause still reads as a refusal by name."""
    pos, omit = as_reset(run(1)), as_reset(run(0))

    def why(variant: dict) -> str:
        return M.case_verdict({"target": RESET, "problems": [], "positive": pos, "omit": omit,
                               "variants": {"other-pp": variant}}, RESET)["why"]
    accepted = refused(run(1))
    accepted["hosts"]["dxvk"]["exit"] = 0
    accepted["hosts"]["dxvk"]["report"].update({"pass": True, "exit_status": 0})
    assert "dxvk: the player exited 0 and passed True" in why(accepted)
    passed = refused(run(1))
    passed["hosts"]["sdlgpu"]["report"]["pass"] = True
    assert "sdlgpu: the player exited 4 and passed True" in why(passed)
    handled = refused(run(1))
    handled["hosts"]["dxvk"]["report"]["methods"][0].update(handled=1, not_handled=0)
    assert "dxvk: IDirect3DDevice9::Reset not handled 0 of 1 calls, 1 not handled in all" in why(handled)
    other = refused(run(1))
    other["hosts"]["sdlgpu"]["report"]["calls"]["not_handled"] = 2
    assert "sdlgpu: IDirect3DDevice9::Reset not handled 1 of 1 calls, 2 not handled in all" in why(other)
    assert M.case_verdict({"target": RESET, "problems": [], "positive": pos, "omit": omit,
                           "variants": {"other-pp": refused(run(1))}}, RESET)["held"] is True


# ------------------------------------------------------------------ run_cases and refresh

def test_a_run_records_into_a_fresh_directory_only(tmp_path, monkeypatch, capsys) -> None:
    """A report a crashed player did not rewrite would otherwise be pinned as this run's."""
    (tmp_path / "out" / "cases" / "t").mkdir(parents=True)
    monkeypatch.setattr(S, "build", lambda *a, **k: pytest.fail("built over earlier cases"))
    assert S.run_cases(tmp_path / "out", tmp_path, families=list(S.APPLIED)) == 2
    assert "already holds cases" in capsys.readouterr().err


def test_a_target_a_registered_run_reaches_is_printed_not_held_whatever_its_case_shows(tmp_path, monkeypatch,
                                                                                    capsys) -> None:
    out = tmp_path / "out"
    (out / "build").mkdir(parents=True)
    (out / "build" / "harness.json").write_text(json.dumps({"binary": {"path": str(tmp_path / "fixture")}}))
    reached, free = "IDirect3DVertexBuffer9::Release", "IDirect3DIndexBuffer9::Release"
    monkeypatch.setattr(S, "harness_targets", lambda binary: [(reached, "indexed", False), (free, "indexed", False)])
    monkeypatch.setattr(S, "case_files", lambda case: {"dir": S.rel(case)})
    monkeypatch.setattr(M, "load_case", lambda root, entry, checker=None: {})
    monkeypatch.setattr(M, "case_verdict", lambda case, target: {"held": True, "why": ""})
    assert S.run_cases(out, tmp_path, families=["indexed"], evaluate_only=True,
                       registry={"reached": {reached: 3}}) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed == [f"{reached}: not held -- 3 registered recorded runs reach it ({M.REGISTRY}), so no synthetic "
                       "case holds it (its case passes)",
                       f"{free}: HELD", "M7_SYNTHETIC held=1 of 2"]


def trial(tmp: Path) -> list[str]:
    return ["--root", str(tmp / "evidence"), "--production-out", str(tmp / "p.json"),
            "--synthetic-out", str(tmp / "s.json")]


def recording(tmp: Path, name: str = S.REPLAY, **current) -> Path:
    """A recording directory of the production run `name` whose present-run.json names the tree's
    recorder and stream format and the runtime digest r*64, unless `current` says otherwise; its
    stream is a file of its own."""
    rec = tmp / "rec"
    rec.mkdir(parents=True, exist_ok=True)
    (tmp / "mh.sa2s").write_bytes(b"stream")
    made = {"recorder_sha256": M.sha256_file(ROOT / M.RECORDER),
            "stream_h_sha256": M.sha256_file(ROOT / "host/sa2_stream.h"), "runtime_sha256": "r" * 64}
    made.update(current)
    (rec / "present-run.json").write_text(json.dumps(
        {"ok": True, "ready": {"stream": str(tmp / "mh.sa2s"), "stream_sha256": "5" * 64}, "current": made,
         **S.run_identity(name)}))
    return rec


def forbid_steps(monkeypatch) -> None:
    for step in ("build_players", "run_cases", "replay_recording"):
        monkeypatch.setattr(S, step, lambda *a, **k: pytest.fail("refresh ran a step"))


def test_refresh_prints_the_re_recording_and_runs_nothing_when_the_recorder_or_runtime_moved(
        tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: "r" * 64)
    forbid_steps(monkeypatch)
    rec = recording(tmp_path, recorder_sha256="0" * 64, runtime_sha256="q" * 64)
    for extra in ([], ["--plan"]):
        assert S.main(["refresh", "--record", str(rec), *trial(tmp_path), *extra]) == 3
        out = capsys.readouterr().out
        assert out.startswith(f"M7_EVIDENCE_RERECORD the Metal Harbor recording {S.rel(rec)} (--record)")
        # The release route has no recording of this tree's (none under the trial's root, and the
        # one the production receipt names, if any, was made at another runtime digest than the
        # stand-in r*64): it is recorded by this tool's record-release.
        rbase = tmp_path / "evidence" / f"record-{S.recording_key(ROOT, 'r' * 64)}"
        assert ("M7_EVIDENCE_RERECORD the release route's recording: no recording: " in out
                or "M7_EVIDENCE_RERECORD the release route's recording " in out
                and "is not one this tree can replay as its own" in out.split(
                    "M7_EVIDENCE_RERECORD the release route's recording ", 1)[1]), out
        assert (f"  {S.COMMAND} record-release --out {S.rel(rbase / 'release')} "
                f"--trace {S.rel(rbase / 'trace' / 'release.sa2s')}\n") in out
        assert (f"  moved: {M.RECORDER}: the recording was made at {'0' * 64}, the tree's is "
                f"{M.sha256_file(ROOT / M.RECORDER)}\n") in out
        assert f"  moved: the runtime digest: the recording was made at {'q' * 64}, the tree's is {'r' * 64}\n" in out
        base = tmp_path / "evidence" / f"record-{S.recording_key(ROOT, 'r' * 64)}"
        assert (f"  source tools/env.sh && .venv/bin/python tools/s5/present_run.py --replay metalharbor --out "
                f"{S.rel(base / 'metalharbor')} --trace {S.rel(base / 'trace' / 'metalharbor.sa2s')}\n") in out
    # The stream format, a recording that is not whole, and a stream that is gone are named too.
    rec = recording(tmp_path / "b", stream_h_sha256=None)
    doc = json.loads((rec / "present-run.json").read_text())
    doc["ok"] = False
    (rec / "present-run.json").write_text(json.dumps(doc))
    (tmp_path / "b" / "mh.sa2s").unlink()
    assert S.main(["refresh", "--record", str(rec), *trial(tmp_path / "b")]) == 3
    out = capsys.readouterr().out
    assert "reads ok False: the recording is not whole" in out and "mh.sa2s is absent" in out
    assert "  moved: host/sa2_stream.h: the recording was made at None" in out
    assert not (tmp_path / "evidence").exists() and not (tmp_path / "b" / "evidence").exists()


def test_refresh_plans_from_a_current_recording_and_finds_a_re_recording_by_its_key(tmp_path, monkeypatch,
                                                                                    capsys) -> None:
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: "r" * 64)
    forbid_steps(monkeypatch)
    rec = recording(tmp_path)
    rel_ = recording(tmp_path / "rel", S.RELEASE)
    # A recording of one run filed under the other is not that run's (review of the release route):
    # each is refused by what its present-run.json says it is, and nothing runs.
    assert S.main(["refresh", "--plan", "--record", str(rel_), "--record-release", str(rec), *trial(tmp_path)]) == 3
    out = capsys.readouterr().out
    assert f"{S.rel(rel_)}/present-run.json reads schema {S.RELEASE_SCHEMA!r}, not 'sa2-present-run/v3': it is " \
           "not a recording of the metalharbor run" in out
    assert f"{S.rel(rec)}/present-run.json reads route None, not 'release': it is not a recording of the " \
           "release run" in out
    assert S.main(["refresh", "--plan", "--record", str(rec), "--record-release", str(rel_), *trial(tmp_path)]) == 0
    out = capsys.readouterr().out
    key = S.content_key(S.players_inputs(ROOT, S.install_dir()))
    assert f"  recording  {S.rel(rec)} (metalharbor: --record), stream {S.rel(tmp_path / 'mh.sa2s')}\n" in out
    assert f"  recording  {S.rel(rel_)} (release: --record-release), stream {S.rel(tmp_path / 'rel' / 'mh.sa2s')}\n" in out
    assert f"  players    {S.rel(tmp_path / 'evidence' / f'players-{key}')} (built by host/player/build.sh and " \
           "host/d3d9gpu/build.sh)\n" in out
    assert f"  receipts   {S.rel(tmp_path / 'p.json')}, {S.rel(tmp_path / 's.json')}\n" in out
    assert not (tmp_path / "evidence").exists()
    # With no --record, the re-recording of this tree's recorder and runtime, by its key.
    mine = tmp_path / "evidence" / f"record-{S.recording_key(ROOT, 'r' * 64)}"
    recording(mine)
    (mine / "rec").rename(mine / "metalharbor")
    recording(mine / "r", S.RELEASE)
    (mine / "r" / "rec").rename(mine / "release")
    assert S.main(["refresh", "--plan", *trial(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"  recording  {S.rel(mine / 'metalharbor')} (metalharbor: the re-recording of this tree's recorder and " \
           f"runtime" in out
    assert f"  recording  {S.rel(mine / 'release')} (release: the re-recording of this tree's recorder and runtime" in out


def test_refresh_writes_receipts_to_documentation_only_from_the_permanent_evidence_root(tmp_path, capsys) -> None:
    assert S.main(["refresh", "--plan", "--root", str(tmp_path / "evidence")]) == 2
    assert f"pins files under {S.EVIDENCE_ROOT} only" in capsys.readouterr().err
    assert S.main(["refresh", "--plan", "--root", str(tmp_path / "evidence"),
                   "--production-out", str(tmp_path / "p.json")]) == 2
    assert "both receipts" in capsys.readouterr().err
    assert S.main(["refresh", "--plan", "--production-out", str(tmp_path / "p.json"),
                   "--synthetic-out", str(tmp_path / "s.json")]) == 2
    assert "names its own --root" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        S.main(["refresh", "--regenerate", *trial(tmp_path)])


def test_the_players_directory_is_named_by_what_the_builds_read(tmp_path) -> None:
    def tree(at: Path, player: bytes = b"player", lib: bytes = b"sdl") -> tuple[Path, Path]:
        for rel_, data in (("host/player/player.cpp", player), ("host/d3d9gpu/d9g_device.cpp", b"d9g"),
                           ("host/sa2_stream.h", b"fmt"), ("host/dxvk_smoke.cpp", b"smoke"),
                           ("tools/pins.json", b"{}"), ("host/runtime_elsewhere.c", b"x")):
            (at / rel_).parent.mkdir(parents=True, exist_ok=True)
            (at / rel_).write_bytes(data)
        (at / "install" / "lib").mkdir(parents=True, exist_ok=True)
        (at / "install" / "lib" / "libSDL3.so.0").write_bytes(lib)
        return at, at / "install"
    a = S.content_key(S.players_inputs(*tree(tmp_path / "a")))
    root, install = tree(tmp_path / "b")
    assert S.content_key(S.players_inputs(root, tmp_path / "a" / "install")) == a
    (root / "host/player/__pycache__").mkdir()
    (root / "host/player/__pycache__/x.pyc").write_bytes(b"cache")
    (root / "host/runtime_elsewhere.c").write_bytes(b"y")
    assert S.content_key(S.players_inputs(root, tmp_path / "a" / "install")) == a
    assert S.content_key(S.players_inputs(*tree(tmp_path / "c", player=b"edited"))) != \
        S.content_key(S.players_inputs(tmp_path / "c", tmp_path / "a" / "install"))
    assert S.content_key(S.players_inputs(*tree(tmp_path / "d", lib=b"another sdl"))) != \
        S.content_key(S.players_inputs(tmp_path / "d", tmp_path / "a" / "install"))
    assert len(a) == 16 and S.players_inputs(root, install)["install"]["lib"] == {
        "lib/libSDL3.so.0": M.sha256_bytes(b"sdl")}


def test_a_finished_build_of_the_key_is_reused_and_any_other_is_built_and_held_to_provenance(
        tmp_path, monkeypatch) -> None:
    install = tmp_path / "install"
    (install / "lib").mkdir(parents=True)
    inputs = S.players_inputs(ROOT, install)
    key = S.content_key(inputs)
    players = tmp_path / f"players-{key}"
    ran: list[list[str]] = []
    verdict = {"pass": True, "failures": []}
    monkeypatch.setattr(S.CH, "provenance", lambda build, pins: dict(verdict))
    monkeypatch.setattr(S.subprocess, "run", lambda argv, **kw: (ran.append([argv[1], kw["env"]["OUT"]]),
                                                                  subprocess.CompletedProcess(argv, 0))[1])
    assert S.build_players(players, install, inputs, key) == f"built {S.rel(players)}"
    assert ran == [[str(ROOT / "host/player/build.sh"), str(players)], [str(ROOT / "host/d3d9gpu/build.sh"), str(players)]]
    assert json.loads((players / S.BUILD_MARK).read_text())["key"] == key
    ran.clear()
    assert S.build_players(players, install, inputs, key).startswith("reused")
    assert ran == []
    # A finished build the provenance no longer accepts is built again, and must then be accepted.
    verdict.update({"pass": False, "failures": ["sa2_player.json: source host/player/player.cpp is a in the tree"]})
    with pytest.raises(S.Stop, match="provenance refuses the build"):
        S.build_players(players, install, inputs, key)
    assert len(ran) == 2


def single_run(root, record, dxvk, sdlgpu, census, out) -> dict:
    """What m7_inputs.production_receipt writes for one recording, as a stand-in: its shape."""
    return {"schema": M.PRODUCTION_SCHEMA, "id": M.PRODUCTION_ID, "generated_by": "tools/s4/m7_inputs.py",
            "command": f"m7_inputs --production --record {record}", "written": "now", "note": "one run",
            "inputs": {"pipeline": {}}, "recording": {"dir": record, "files": {}, "stream_sha256": "5" * 64},
            "census": {"path": census, "sha256": "c" * 64},
            "hosts": {"dxvk": {"dir": dxvk, "files": {}}, "sdlgpu": {"dir": sdlgpu, "files": {}}},
            "players": {"build": "players", "records": {}}}


def test_refresh_writes_both_receipts_only_after_every_step_finished(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(M, "tree_runtime_digest", lambda root: "r" * 64)
    rec = recording(tmp_path)
    rel_ = recording(tmp_path / "rel", S.RELEASE)
    steps: list[str] = []
    fail: dict = {}
    monkeypatch.setattr(S, "build_players", lambda players, install, inputs, key: steps.append("players") or "built")

    def cases(out, players, *, families, receipt_path):
        steps.append("synthetic")
        assert out.name.startswith("synthetic-") and families == list(S.APPLIED)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text('{"synthetic": true}\n')
        return 0

    def replay(players, stream, out_dir):
        steps.append(f"replay {out_dir.name}")
        assert out_dir.parent.name.startswith("replay-")
        assert stream == {"metalharbor": tmp_path / "mh.sa2s", "release": tmp_path / "rel" / "mh.sa2s"}[out_dir.name]
        if fail and out_dir.name == "release":
            raise S.Stop("the replay failed")
        return out_dir
    monkeypatch.setattr(S, "run_cases", cases)
    monkeypatch.setattr(S, "replay_recording", replay)
    monkeypatch.setattr(M, "production_receipt", single_run)
    args = ["refresh", "--record", str(rec), "--record-release", str(rel_)]
    assert S.main([*args, *trial(tmp_path)]) == 0
    assert steps == ["players", "synthetic", "replay metalharbor", "replay release"]
    assert json.loads((tmp_path / "s.json").read_text()) == {"synthetic": True}
    prod = json.loads((tmp_path / "p.json").read_text())
    # The contract with tools/s4/m7_inputs.py: `runs`, each block one recorder run with its counts
    # and both hosts' reports, shaped as the single-run receipt; nothing of a run at the top.
    assert [r["name"] for r in prod["runs"]] == ["metalharbor", "release"]
    assert [r["recording"]["dir"] for r in prod["runs"]] == [S.rel(rec), S.rel(rel_)]
    for r in prod["runs"]:
        assert sorted(r) == ["census", "command", "hosts", "name", "players", "recording"]
        assert r["hosts"]["dxvk"]["dir"].endswith(f"/{r['name']}/dxvk") and r["census"]["path"].endswith(
            f"/{r['name']}/census.json")
    assert not {"recording", "hosts", "census", "players"} & set(prod)
    assert (prod["schema"], prod["id"], prod["assembled_by"]) == (M.PRODUCTION_SCHEMA, M.PRODUCTION_ID, S.SELF)
    assert "-> " in capsys.readouterr().out
    fail["replay"] = True
    steps.clear()
    assert S.main([*args, *trial(tmp_path / "second")]) == 1
    assert "M7_EVIDENCE_STOPPED the replay failed; no receipt was written" in capsys.readouterr().err
    assert not (tmp_path / "second" / "p.json").exists() and not (tmp_path / "second" / "s.json").exists()


# ------------------------------------------------------------------ the second production run

def test_a_production_receipt_names_each_run_s_recording_and_the_single_run_form_names_metal_harbor() -> None:
    multi = {"runs": [{"name": "metalharbor", "recording": {"dir": "a"}}, {"name": "release", "recording": {"dir": "b"}}]}
    assert [S.receipt_run_dir(multi, n) for n in ("metalharbor", "release")] == ["a", "b"]
    single = {"recording": {"dir": "a"}}
    assert [S.receipt_run_dir(single, n) for n in ("metalharbor", "release")] == ["a", None]
    assert S.receipt_run_dir({"runs": []}, "metalharbor") is None


def test_the_multi_run_receipt_refuses_runs_replayed_by_two_builds(monkeypatch, tmp_path) -> None:
    def one(root, record, dxvk, sdlgpu, census, out):
        doc = single_run(root, record, dxvk, sdlgpu, census, out)
        doc["players"]["build"] = f"players-of-{record}"
        return doc
    monkeypatch.setattr(M, "production_receipt", one)
    runs = [("metalharbor", tmp_path / "a", tmp_path / "ra"), ("release", tmp_path / "b", tmp_path / "rb")]
    with pytest.raises(S.Stop, match="not one build"):
        S.production_runs_receipt(runs, out="p.json")
    with pytest.raises(S.Stop, match="no production run"):
        S.production_runs_receipt([], out="p.json")


def test_the_release_route_is_the_registered_idle_run_s_settings_with_the_recorder(tmp_path) -> None:
    """The registered recorded run it re-records (m7-recorded-runs.json; its loop_run.log):
    16000 polls on the reach configuration, the import and virtual-time budgets its runner derives
    from them, the boot capture's device profile, a script with no key, the probe's 2400 s alarm;
    and present_run.py's boot hooks with the recorder, writing a partial stream and the import table."""
    import present_run as PR
    p = S.release_paths(tmp_path / "out", tmp_path / "trace" / "release.sa2s")
    meta = {"build": "present-m7release.t", "route": "release", "run": "r"}
    profile_env = {"SA2_DEVICE_PROFILE": "profile.txt"}
    cmd, spec = S.release_command("present-m7release.t", p, profile_env, meta)
    env = dict(kv.split("=", 1) for kv in spec.split("|")[1:])
    assert spec.split("|")[0] == "release"
    assert {k: env[k] for k in ("SA2_PUMP_POLLS", "SA2_IMPORT_BUDGET", "SA2_VIRTUAL_BUDGET_MS", "SA2_D3D9_DEVICE",
                                "SA2_DEVICE_PROFILE")} == {
        "SA2_PUMP_POLLS": "16000", "SA2_IMPORT_BUDGET": "80000000", "SA2_VIRTUAL_BUDGET_MS": "320000",
        "SA2_D3D9_DEVICE": "1", "SA2_DEVICE_PROFILE": "profile.txt"}
    assert env["SA2_REC_TRACE"].endswith(f"release.sa2s.{PR.tree_tag()}.partial")
    assert env["SA2_REC_COUNTS"].endswith("out/counts.json") and json.loads(env["SA2_REC_META"]) == meta
    assert env["SA2_INPUT_DI_SCRIPT"].endswith("out/script.txt")
    assert cmd[cmd.index("--hook") + 1] == PR.TR.HOOK and PR.RECORDER in cmd
    assert all(f"{w}" in cmd for w in PR.WRAPS)
    assert cmd[-4:] == ["--probe-arg=--timeout", "--probe-arg=2400", "--probe-arg=--dump-imports",
                        f"--probe-arg={S.rel(p['imports'])}"]
    assert S.RELEASE_SCRIPT.startswith("#") and "\n" not in S.RELEASE_SCRIPT.rstrip("\n")
    # The registered run's own settings, from the command its loop_run.log records.
    first = (ROOT / S.RELEASE_REGISTERED / "loop_run.log").read_text().splitlines()[0]
    theirs = dict(kv.split("=", 1) for kv in first.split(" --run ", 1)[1].split(" --probe-arg")[0].split("|")[1:])
    for k in ("SA2_PUMP_POLLS", "SA2_IMPORT_BUDGET", "SA2_VIRTUAL_BUDGET_MS", "SA2_D3D9_DEVICE", "SA2_DSOUND_DEVICE",
              "SA2_DINPUT_DEVICE", "SA2_XAUDIO2_DEVICE", "SA2_DDRAW_DEVICE", "SA2_D3DX_CONSTANT_TABLE"):
        assert env[k] == theirs[k], k
    assert "--probe-arg=--timeout --probe-arg=2400" in first


def counts_with(release: int, entered: int | None = None, present: int = 2) -> dict:
    return {"schema": M.COUNTS_SCHEMA, "writer": M.RECORDER, "entries": [],
            "methods": [{"type": 7, "slot": 2, "interface": "IDirect3DVertexBuffer9", "method": "Release",
                         "calls": release, "entered": release if entered is None else entered},
                        {"type": 1, "slot": 17, "interface": "IDirect3DDevice9", "method": "Present",
                         "calls": present, "entered": present}]}


def test_the_release_route_holds_only_a_run_that_reached_the_release_and_returned_from_every_call() -> None:
    ok = S.release_route_check(counts_with(1701), counts_with(1701))
    assert ok["ok"] is True and ok["calls"] == 1701 and ok["differs_from_registered"] == []
    assert S.release_route_check(counts_with(0), counts_with(1701))["ok"] is False
    assert S.release_route_check(counts_with(5, entered=4), None)["ok"] is False
    assert S.release_route_check(None, None)["ok"] is False
    # A difference from the registered run, made on an earlier tree's runtime, is kept and decides nothing.
    other = S.release_route_check(counts_with(1701, present=3), counts_with(1701))
    assert other["ok"] is True and other["differs_from_registered"] == ["IDirect3DDevice9::Present: 3 vs 2"]


def test_record_release_refuses_outputs_git_would_hold(tmp_path, capsys) -> None:
    assert S.main(["record-release", "--out", str(ROOT / "Documentation" / "x"), "--trace", str(tmp_path / "t.sa2s")]) == 2
    assert "refused" in capsys.readouterr().err


def test_the_route_accepts_its_keyboard_script_only_when_the_run_s_record_shows_it_inert(monkeypatch) -> None:
    """present_run.py's configuration check refuses any host keyboard source; the route's registered
    run had a script that holds no key, which the route keeps only while the run's record shows it
    answered, held and wrote nothing. Every other configuration problem stands."""
    import present_run as PR
    host_problem = ("the run's record reads dinput.host_input (source 'script'): a host keyboard answered its "
                    "DirectInput device, which only tools/s5/play_live.py sets")
    other = "SA2_D3D9_DEVICE=1 was set and the run's record reads d3d9.device_enabled = None"
    found: list[str] = []
    monkeypatch.setattr(PR, "check_configuration", lambda rec, profile, env: {"problems": list(found), "ok": not found})
    inert = {"source": "script", "answers_from_host": 0, "polls": 16001, "polls_held": 0, "keys_written": 0,
             "script_lines": 0, "records": 0}
    found[:] = [host_problem]
    assert S.release_configuration({"dinput": {"host_input": inert}}, None, {})["ok"] is True
    for spoil in ({"keys_written": 3}, {"answers_from_host": 1}, {"source": "fifo"}, {"script_lines": 1}):
        v = S.release_configuration({"dinput": {"host_input": {**inert, **spoil}}}, None, {})
        assert v["ok"] is False and v["problems"] == [host_problem], spoil
    found[:] = [host_problem, other]
    assert S.release_configuration({"dinput": {"host_input": inert}}, None, {})["problems"] == [other]
    found[:] = []
    assert S.release_configuration({}, None, {})["ok"] is False      # no run record at all
