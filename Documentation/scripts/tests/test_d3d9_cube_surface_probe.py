"""Finite cube surface observations against the real runtime and a bounded heap.

The children use authored guest memory and actual guest vtable thunks. Aliased
face storage and incomplete child destruction must fail this observable owner.
Capacity refusals are project guards, not native D3D9 HRESULT measurements.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig"
PROBE = ROOT / "harness/d3d9_cube_surface_probe.c"
RUNTIME = ROOT / "runtime/sa2_d3d9.c"


def child(folder, argv, env, timeout=120):
    folder.mkdir()
    (folder / "command.json").write_text(json.dumps({"argv": argv, "cwd": str(ROOT), "env": env}, indent=2)+"\n")
    start = time.monotonic()
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    (folder / "stdout.log").write_bytes(done.stdout)
    (folder / "stderr.log").write_bytes(done.stderr)
    (folder / "exit.json").write_text(json.dumps({"exit": done.returncode,
        "seconds": time.monotonic()-start,
        "stdout_sha256": hashlib.sha256(done.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(done.stderr).hexdigest()}, indent=2)+"\n")
    return done


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    assert ZIG.is_file(), f"pinned compiler missing: {ZIG}"
    d = tmp_path_factory.mktemp("cube-owner")
    (d / "child-tmp").mkdir()
    source = RUNTIME.read_text()
    alterations = {
        "alias": ("offset += (uint64_t)face * chain;", "offset += 0u * chain;"),
        "lifetime": ("for (uint32_t k = 0; k < 6u * LEVELS_MAX; k++) {",
                     "for (uint32_t k = 0; k < LEVELS_MAX; k++) {"),
    }
    binaries = {}
    env = {"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": str(d / "child-tmp"),
           "ZIG_LOCAL_CACHE_DIR": str(d / "zig-local"),
           "ZIG_GLOBAL_CACHE_DIR": str(d / "zig-global")}
    for name in ["positive", "alias", "lifetime"]:
        runtime = RUNTIME
        if name in alterations:
            old, new = alterations[name]
            assert source.count(old) == 1, f"mutation anchor changed: {name}"
            runtime = d / f"{name}.c"
            runtime.write_text(source.replace(old, new, 1))
        exe = d / name
        # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open.
        argv = [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-D_GNU_SOURCE", "-O1",
                "-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD",
                "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "runtime"),
                str(PROBE), str(runtime), str(ROOT / "runtime/sa2_guest_call.c"), "-lm", "-o", str(exe)]
        done = child(d / f"compile-{name}", argv, env)
        assert done.returncode == 0, "compiler/setup failed:\n" + done.stdout.decode() + done.stderr.decode()
        binaries[name] = exe
    return d, binaries


def run(built, name, mode):
    d, binaries = built
    folder = d / f"run-{name}-{mode}"
    # child owns the results path; subprocess stdout/stderr remain separate.
    out = d / f"{name}-{mode}.json"
    env = {"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": str(d / "child-tmp")}
    done = child(folder, [str(binaries[name]), mode, str(out)], env, timeout=30)
    assert out.is_file(), "product child did not write results:\n"+done.stdout.decode()+done.stderr.decode()
    return done, json.loads(out.read_text())


def rows(rec, name):
    return [r["actual"] for r in rec["observations"] if r["id"] == name]


def test_cube_surface_identity_storage_parent_binding_and_two_dimensional_control(built):
    done, rec = run(built, "positive", "positive")
    assert done.returncode == 0, done.stdout.decode()+done.stderr.decode()
    assert done.stderr == b""
    assert rec["failures"] == 0 and rec["failed_ids"] == []
    assert rec["scope"] == "authored CPU cube surfaces; no native, renderer, M7 or autogen pixel claim"
    assert rows(rec, "creator-storage") == [[95, 64]]
    assert rows(rec, "view-build-deltas") == [[12, 12]]
    assert rows(rec, "invalid-hr") == [[0x8876086C]*5]
    assert rows(rec, "invalid-deltas") == [[0, 0, 0, 0]]
    assert rows(rec, "invalid-span") == [[2, 0x032FFFFE, 0, 0]]
    assert rows(rec, "six-face-reserve") == [[1, 1, 511, 480, 64]]
    offsets = [0, 64, 80, 144, 160, 224, 240, 304, 320, 384, 400, 464]
    expected_surfaces = []
    for i, offset in enumerate(offsets):
        w, pitch, size = (2, 8, 16) if i % 2 else (4, 16, 64)
        expected_surfaces.append([i//2, i%2, offset, pitch, 21, 1, 0, 1, w, w,
                                  0xC0000000+i, 0xC0000000+i+size-4])
    assert rows(rec, "surface") == expected_surfaces
    assert rows(rec, "description") == [[21, 1, 0, 1, 0, 0, 2 if i%2 else 4, 2 if i%2 else 4] for i in range(12)]
    assert rows(rec, "after-copy-first-words") == [[0xC0000000+i if i != 2 else 0xC0000000 for i in range(12)]]
    assert rows(rec, "other-copy") == [[0xC0000000, 0xC000003C, 511]]
    assert rows(rec, "same-copy-all-words") == [[0xC0000000+4*i for i in range(16)]]
    assert rows(rec, "other-copy-all-words") == [[0xC0000000+4*i for i in range(16)]]
    assert rows(rec, "binding-lifetime") == [[0, 13, 1, 480]]
    assert rows(rec, "all-cube-frees") == [[4, 4, 0, 0]]
    assert rows(rec, "2d-existing") == [[1, 4, 1, 0x01020304, 115]]
    assert rows(rec, "2d-final") == [[2, 2, 2, 148]]
    assert rows(rec, "metadata") == [[0, 0, 3, 0x8876086C, 535],
                                      [9, 0, 3, 0x8876086C, 535],
                                      [2, 0x400, 1, 0x8876086C, 535]]
    assert rec["heap"] == {"allocations": 12, "frees": 12, "active": 0, "errors": 0}
    for a in rec["abi"]:
        assert type(a["eax"]) is int
        assert a["popped"] == 4*(a["argc"]+2)
    gets = [a for a in rec["abi"] if a["id"] == "get-cube"]
    assert len(gets) == 33
    assert all((a["slot"], a["argc"], a["eax"], a["popped"]) == (18, 3, 0, 20) for a in gets)
    literal_refs = {"parent-view-count": [14, 13], "repeat-parent-count": [15, 14],
                    "container-parent-count": [16, 15], "invalid-parent-count": [14, 13],
                    "binding-get-parent-ref": [2, 1]}
    for name, actual in literal_refs.items():
        assert [a["eax"] for a in rec["abi"] if a["id"] == name] == actual
    assert [a["eax"] for a in rec["abi"] if a["id"] == "view-release"] == list(range(12, 0, -1))
    assert [a["eax"] for a in rec["abi"] if a["id"] == "parent-release"] == [0]


def test_cube_unrepresentable_storage_refuses_before_object_or_heap_change(built):
    done, rec = run(built, "positive", "boundaries")
    assert done.returncode == 0, done.stdout.decode()+done.stderr.decode()
    assert done.stderr == b""
    assert rec["failures"] == 0 and rec["failed_ids"] == []
    cases = [(16384, 0, 21), (65536, 1, 21), (0xFFFFFFFF, 1, 0x31545844),
             (4, 1, 0xDEADBEEF), (0, 1, 21)]
    assert rows(rec, "capacity") == [[w, l, fmt, 1, 0, 0, 0, 0xA5A5A5A5] for w, l, fmt in cases]
    assert rec["heap"] == {"allocations": 0, "frees": 0, "active": 0, "errors": 0}
    text = done.stdout.decode()
    assert text.count("IDirect3DCubeTexture9::surface storage") == 5
    assert "six-face cube storage is not representable" in text
    assert "cube mip chain is not representable" in text
    assert "cube pitch is not representable" in text


def test_face_storage_alias_control_fails_literal_offsets_and_bytes(built):
    done, rec = run(built, "alias", "positive")
    assert done.returncode == 1, done.stdout.decode()+done.stderr.decode()
    assert done.stderr == b""
    assert rec["failures"] > 0
    assert "face-storage-offset" in rec["failed_ids"]
    assert "face-storage-stable" in rec["failed_ids"]
    assert [r[2] for r in rows(rec, "surface")] == [400, 464]*6
    assert [r[10] for r in rows(rec, "surface")] == [0xC000000A, 0xC000000B]*6
    assert rows(rec, "binding-lifetime") == [[0, 13, 1, 480]]
    assert rec["heap"]["errors"] == 0


def test_child_destruction_control_fails_binding_final_release_observation(built):
    done, rec = run(built, "lifetime", "positive")
    assert done.returncode == 1, done.stdout.decode()+done.stderr.decode()
    assert done.stderr == b""
    assert "binding-final-destroy" in rec["failed_ids"]
    assert rows(rec, "binding-lifetime") == [[0, 3, 1, 480]]
    assert rows(rec, "six-face-reserve") == [[1, 1, 511, 480, 64]]
    assert rec["heap"] == {"allocations": 12, "frees": 12, "active": 0, "errors": 0}
