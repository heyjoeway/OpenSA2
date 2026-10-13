"""Literal F/I/B and VS/PS bank results through actual runtime guest thunks.

Guest memory/CPU/function-table/trap/window/scheduler/heap are fixture providers.
This is not native bounds, Reset, stateblock, lifetime, replay or M7 evidence.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig"
PROBE = ROOT / "harness/d3d9_shader_banks_probe.c"
D3D9_C = ROOT / "runtime/sa2_d3d9.c"


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    """Compile this harness and the real runtime, with isolated compiler caches."""
    assert ZIG.is_file(), f"required pinned compiler is missing: {ZIG}"
    d = tmp_path_factory.mktemp("d3d9-shader-banks")
    env = {"PATH": os.defpath, "LC_ALL": "C",
           "ZIG_LOCAL_CACHE_DIR": str(d / "zig-local"),
           "ZIG_GLOBAL_CACHE_DIR": str(d / "zig-global")}
    exe = d / "d3d9_shader_banks_probe"
    done = subprocess.run(
        # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open.
        [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-D_GNU_SOURCE", "-O1",
         "-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD",
         "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "runtime"), str(PROBE),
         str(D3D9_C), str(ROOT / "runtime/sa2_guest_call.c"), "-lm", "-o", str(exe)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return exe


def test_shader_constant_banks_have_independent_values_and_unset_maps(probe, tmp_path):
    out = tmp_path / "banks.json"
    done = subprocess.run([str(probe), str(out)], capture_output=True, text=True,
                          env={"PATH": os.defpath, "LC_ALL": "C"}, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stderr == ""
    assert done.stdout == "d3d9_shader_banks_probe: 86 checks, 0 failures\n"
    rec = json.loads(out.read_text())
    assert type(rec["checks"]) is int and rec["checks"] == 86
    assert type(rec["failures"]) is int and rec["failures"] == 0
    assert rec["scope"] == "F/I/B and VS/PS bank independence; authored CPU guest-vtable fixture"

    # Independent literals: do not use the probe's own expected_words as oracle.
    vs_float = [0x3F800000, 0x40000000, 0x40400000, 0x40800000]
    ps_float = [0x40A00000, 0x40C00000, 0x40E00000, 0x41000000]
    expected_values = [
        ("VS-F-alone", vs_float), ("PS-F-alone", ps_float),
        ("VS-I-alone", [11, 22, 33, 44]), ("PS-I-alone", [55, 66, 77, 88]),
        ("VS-F-after-I", vs_float), ("PS-F-after-I", ps_float),
        ("VS-B-alone", [1]), ("PS-B-alone", [1]),
        ("VS-F-after-B", vs_float), ("PS-F-after-B", ps_float),
        ("VS-I-after-B", [11, 22, 33, 44]), ("PS-I-after-B", [55, 66, 77, 88]),
        ("review-VS-B-zero", [0]), ("review-PS-B-one-survives", [1]),
        ("review-VS-B-one-survives", [1]), ("review-PS-B-zero", [0]),
        ("review-VS-I-unset-after-F", [0, 0, 0, 0]),
        ("review-VS-B-unset-after-F", [0]),
        ("review-PS-I-unset-after-F", [0, 0, 0, 0]),
        ("review-PS-B-unset-after-F", [0]),
    ]
    rows = rec["observations"]
    assert [(r["id"], r["actual_words"]) for r in rows] == expected_values
    assert all(type(w) is int for r in rows for w in r["actual_words"])
    assert all(r["matches"] is True for r in rows)
    assert [(r["id"], r["expected_words"]) for r in rows] == expected_values
    assert type(rec["unset_before"]) is int and rec["unset_before"] == 0
    assert type(rec["unset_after"]) is int and rec["unset_after"] == 4

    # CreateDevice has six arguments; all 31 Set/Get calls have three.
    # These rows hold actual guest EAX/ESP, independent of the C check count.
    slots = [16, 94, 109, 95, 110, 96, 111, 97, 112, 95, 110, 98, 113, 99,
             114, 95, 110, 97, 112, 98, 99, 114, 98, 113, 99, 114, 94, 109,
             97, 99, 112, 114]
    expected_abi = [{"slot": 16, "argc": 6, "eax": 0, "popped": 32}]
    expected_abi += [{"slot": s, "argc": 3, "eax": 0, "popped": 20}
                     for s in slots[1:]]
    assert rec["abi"] == expected_abi
    assert all(type(v) is int for row in rec["abi"] for v in row.values())
