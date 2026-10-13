"""Guest-vtable COM and owned numeric/sampler CTAB reflection behavior.

The standalone authored probe drives actual runtime/sa2_d3d9.c over a local
memory/heap provider. It checks literal x86 descriptors, stable table-local
handles, caller-buffer independence, final ownership release and malformed
input refusal. Native observations guide selected ABI/reflection cases but
these tests supply no replay, recorder/player or complete M7 proof.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
PROBE = ROOT / "harness" / "d3dx_table_probe.c"
D3D9_C = ROOT / "runtime" / "sa2_d3d9.c"
MINGW = ZIG.parent / "lib" / "libc" / "include" / "any-windows-any"


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    """harness/d3dx_table_probe.c and runtime/sa2_d3d9.c, and nothing else."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("d3dx-table-probe")
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                   "ZIG_GLOBAL_CACHE_DIR",
                   str(ROOT / "private" / "generated" / "zig-cache-tests")))
    exe = d / "d3dx_table_probe"
    # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open.
    done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall",
                           "-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD",
                           "-Wextra", "-Werror", "-I", str(ROOT / "runtime"), str(PROBE),
                           str(D3D9_C), str(ROOT / "runtime/sa2_guest_call.c"), "-o", str(exe)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr
    return exe


def run(exe: Path, brk: str | None, tmp_path: Path) -> tuple[int, dict, str]:
    """The probe under SA2_D3D9_BREAK=`brk` (unset for None): its exit code,
    its JSON record and its stdout."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SA2_D3D9", "SA2_D3DX"))}
    if brk is not None:
        env["SA2_D3D9_BREAK"] = brk
    out = tmp_path / f"probe.{brk or 'none'}.json"
    done = subprocess.run([str(exe), "--json", str(out)], capture_output=True, text=True,
                          env=env, timeout=120)
    return done.returncode, json.loads(out.read_text()), done.stdout


def test_the_table_answers_com_and_owned_reflection(probe, tmp_path):
    code, rec, stdout = run(probe, None, tmp_path)
    assert code == 0, stdout
    assert rec["failures"] == 0 and rec["failed"] == []
    assert rec["break"] == ""
    assert rec["checks"] == 1807
    t = rec["tables"]
    # The original nine COM/header cases remain, now with real type/default
    # records. Two owned aggregate/sampler tables and 257 tables beyond the
    # diagnostic row capacity exercise independent semantic storage.
    assert (t["built"], t["destroyed"], t["qi_answered"], t["qi_declined"]) == (268, 268, 3, 2)
    assert [(r["version"], r["constants"], r["ctab_bytes"]) for r in t["rows"][:9]] == [
        ("0xfffe0300", 2, 172), ("0xfffe0300", 0, 64), ("0xffff0300", 1, 116),
        ("0xfffe0300", 2, 172), ("0xffff0300", 3, 228), ("0xfffe0300", 4, 284),
        ("0xffff0300", 5, 336), ("0xfffe0300", 6, 392), ("0xffff0300", 7, 448)]
    assert [(r["constants"], r["ctab_bytes"]) for r in t["rows"][9:11]] == [(3, 400), (3, 400)]
    assert len(t["rows"]) == 256
    assert all((r["version"], r["constants"], r["ctab_bytes"]) == ("0xfffe0300", 1, 116)
               for r in t["rows"][11:])
    assert all(r["refs"] == 0 and r["released"] for r in t["rows"])


def test_tableanswer_breaks_real_lookup_and_owned_descriptors(probe, tmp_path):
    code, rec, stdout = run(probe, "tableanswer", tmp_path)
    assert code == 1, stdout
    assert rec["break"] == "tableanswer"
    assert {"lookup-vFloat", "desc-vFloat", "desc-fields", "desc-count-zero"} <= set(rec["failed"])
    assert rec["failures"] == 279


def test_tablekeep_fails_the_release_count(probe, tmp_path):
    """A final Release that keeps the table alive: every table stays built and
    none destroyed, and a call through a released table is answered instead of
    refused."""
    code, rec, stdout = run(probe, "tablekeep", tmp_path)
    assert code == 1, stdout
    assert rec["break"] == "tablekeep"
    assert set(rec["failed"]) == {
        "final-release-destroys", "final-release-row", "after-final-addref",
        "after-final-release", "after-final-qi", "more-destroyed-in-order",
        "more-release-rows", "release-count", "owned-storage-release",
        "after-final-desc", "after-final-lookup", "all-owned-storage-freed"}
    assert (rec["tables"]["built"], rec["tables"]["destroyed"]) == (268, 0)


def test_an_unknown_break_is_refused_by_name(probe, tmp_path):
    """The break names are parsed, not matched loosely: a misspelt one stops
    the run at configuration rather than running as no break."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("SA2_D3D9", "SA2_D3DX"))}
    env["SA2_D3D9_BREAK"] = "tablekept"
    done = subprocess.run([str(probe)], capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 2
    assert "SA2_D3D9_BREAK=tablekept is not one of" in done.stderr
    assert "tableanswer, tablekeep" in done.stderr


# ------------------------------------------------------------------ the IIDs

def guid_bytes(fields: list[str]) -> bytes:
    """A DEFINE_GUID's eleven numbers as the GUID's sixteen bytes in memory."""
    n = [int(x, 16) for x in fields]
    return (n[0].to_bytes(4, "little") + n[1].to_bytes(2, "little")
            + n[2].to_bytes(2, "little") + bytes(n[3:]))


def c_bytes(text: str, name: str) -> bytes:
    """The sixteen bytes of `static const uint8_t <name>[16] = {...};`."""
    m = re.search(rf"{name}\[16\]\s*=\s*\{{([^}}]*)\}}", text)
    assert m, name
    return bytes(int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", m.group(1)))


def test_the_runtimes_iids_are_d3dx9_42s():
    if not (MINGW / "d3dx9shader.h").is_file():
        pytest.skip(f"the pinned zig's headers are not present: {MINGW}")
    shader = (MINGW / "d3dx9shader.h").read_text()
    core = (MINGW / "d3dx9core.h").read_text()
    # d3dx9_42 is SDK 42: the IID the header keeps for D3DX_SDK_VERSION < 43.
    old = re.search(r"#if D3DX_SDK_VERSION < 43\s*DEFINE_GUID\(IID_ID3DXConstantTable,([^)]*)\)",
                    shader)
    new = re.search(r"#else\s*DEFINE_GUID\(IID_ID3DXConstantTable,([^)]*)\)", shader)
    buf = re.search(r"DEFINE_GUID\(IID_ID3DXBuffer,([^)]*)\)", core)
    assert old and new and buf
    table42 = guid_bytes([f.strip() for f in old.group(1).split(",")])
    table43 = guid_bytes([f.strip() for f in new.group(1).split(",")])
    buffer = guid_bytes([f.strip() for f in buf.group(1).split(",")])
    runtime = D3D9_C.read_text()
    assert c_bytes(runtime, "IID_D3DXCONSTANTTABLE_42_BYTES") == table42
    assert c_bytes(runtime, "IID_D3DXBUFFER_BYTES") == buffer
    assert c_bytes(runtime, "IID_UNKNOWN_BYTES") == bytes(8) + bytes([0xC0]) + bytes(6) + b"\x46"
    # The probe spells them out on its own; they must be the same bytes, and
    # its declined IID must be SDK 43's.
    probe = PROBE.read_text()
    assert c_bytes(probe, "t42") == table42
    assert c_bytes(probe, "buf") == buffer
    assert c_bytes(probe, "t43") == table43 != table42
