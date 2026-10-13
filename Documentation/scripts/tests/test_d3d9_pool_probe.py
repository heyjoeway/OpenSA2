"""The d3d9 object pool's two blocks, against the real runtime and a checked heap.

harness/d3d9_pool_probe.c drives runtime/sa2_d3d9.c through its guest vtable
thunks in a host window over guest 0x00000000..0x03300000. No executable, game
run, player or GPU.

The first block's literal report below -- its bytes' FNV-1a, every object's
address, the constant table's handle -- was produced identically by this probe
built against the runtime at development_snapshot, before the overflow block existed, and
against the runtime with it: the first 16384 objects are where they were, with
the bytes they had, so a run that builds no more is the run it was. Five
mutations of the runtime, each a mistake the overflow block invites, must fail
the observation that names it.
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
PROBE = ROOT / "harness/d3d9_pool_probe.c"
RUNTIME = ROOT / "runtime/sa2_d3d9.c"

# runtime/sa2_d3d9.c mutations: (anchor, replacement, replace every occurrence)
MUTATIONS = {
    # the pool's bound left at the first block
    "cap": ("? SA2_D3D9_POOL_OBJECTS\n", "? SA2_D3D9_OBJ_FIRST_COUNT\n", False),
    # level surfaces kept as sixteen-bit indices
    "lvl16": ("    uint32_t lvl[6u * LEVELS_MAX];", "    uint16_t lvl[6u * LEVELS_MAX];", False),
    # a constant handle's table index still read off the first block's address
    "handle": ("uint32_t slot=o->idx;", "uint32_t slot=(o->addr-SA2_D3D9_OBJ_BASE)/g_obj_stride;",
               True),
    # one word of every object changed: the first block's bytes are not the same
    "word3": ("    pk32(o->addr + 12u, o->idx);", "    pk32(o->addr + 12u, o->idx + 1u);", False),
    # a cube face's surface index read off the first block's address (the pool
    # review's mutation: the texture path's line is separate, so only cubes see it)
    "cubeaddr": ("            o->lvl[key] = s->idx + 1u;",
                 "            o->lvl[key] = (uint32_t)((s->addr - SA2_D3D9_OBJ_BASE) / g_obj_stride"
                 " + 1u);", False),
}

# FNV-1a 64 of guest 0x03200000..0x03240000 after the first block's recipe, and
# of the 16384 addresses it built, each as two 32-bit halves: the same from the
# runtime at development_snapshot and from this one.
FIRST_BLOCK = [16384, 11142, 504, 0, 0x03200280, 0x80280000,
               0xE90A4047, 0xC6690EB3, 0x05D83CBC, 0x5B396783]


def child(folder, argv, env, timeout=120):
    folder.mkdir()
    (folder / "command.json").write_text(json.dumps({"argv": argv, "cwd": str(ROOT), "env": env},
                                                    indent=2) + "\n")
    start = time.monotonic()
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    (folder / "stdout.log").write_bytes(done.stdout)
    (folder / "stderr.log").write_bytes(done.stderr)
    (folder / "exit.json").write_text(json.dumps({
        "exit": done.returncode, "seconds": time.monotonic() - start,
        "stdout_sha256": hashlib.sha256(done.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(done.stderr).hexdigest()}, indent=2) + "\n")
    return done


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    assert ZIG.is_file(), f"pinned compiler missing: {ZIG}"
    d = tmp_path_factory.mktemp("d3d9-pool")
    (d / "child-tmp").mkdir()
    source = RUNTIME.read_text()
    env = {"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": str(d / "child-tmp"),
           "ZIG_LOCAL_CACHE_DIR": str(d / "zig-local"),
           "ZIG_GLOBAL_CACHE_DIR": str(d / "zig-global")}
    binaries = {}
    for name in ["positive", *MUTATIONS]:
        runtime = RUNTIME
        if name in MUTATIONS:
            old, new, every = MUTATIONS[name]
            assert source.count(old) == (2 if every else 1), f"mutation anchor changed: {name}"
            runtime = d / f"{name}.c"
            runtime.write_text(source.replace(old, new))
        exe = d / name
        # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open.
        argv = [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-D_GNU_SOURCE", "-O1",
                "-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD",
                "-Wall", "-Wextra", "-Werror", "-I", str(ROOT / "runtime"),
                str(PROBE), str(runtime), str(ROOT / "runtime/sa2_guest_call.c"), "-lm",
                "-o", str(exe)]
        done = child(d / f"compile-{name}", argv, env)
        assert done.returncode == 0, ("compiler/setup failed:\n" + done.stdout.decode()
                                      + done.stderr.decode())
        binaries[name] = exe
    return d, binaries


def run(built, name, mode):
    d, binaries = built
    out = d / f"{name}-{mode}.json"
    env = {"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": str(d / "child-tmp")}
    done = child(d / f"run-{name}-{mode}", [str(binaries[name]), mode, str(out)], env, timeout=60)
    rec = json.loads(out.read_text()) if out.is_file() else None
    return done, rec


def obs(rec, name):
    return [r["actual"] for r in rec["observations"] if r["id"] == name]


def clean(done, rec):
    assert done.returncode == 0, done.stdout.decode() + done.stderr.decode()
    assert done.stderr == b""
    assert rec["failures"] == 0 and rec["failed_ids"] == []
    assert rec["heap"]["errors"] == 0
    assert rec["host_fault"] is None


def test_the_first_block_is_the_runtime_before_the_overflow_block(built):
    done, rec = run(built, "positive", "first")
    clean(done, rec)
    assert obs(rec, "first-block") == [FIRST_BLOCK]
    assert rec["stats"] == {"objects_built": 16384, "objects_destroyed": 11142,
                            "level_surfaces": 504, "unknown_this": 0, "calls_on_destroyed": 0,
                            "releases_past_zero": 0, "object_stride": 16, "object_fill": 0}


def test_allocation_crosses_into_the_overflow_block_to_capacity(built):
    done, rec = run(built, "positive", "pool")
    clean(done, rec)
    assert obs(rec, "first-block") == [FIRST_BLOCK]
    # index 16383's texture: level surfaces at 16384 and 16385, the first of the
    # overflow block, counts 3 2 1 0 on release, three objects destroyed
    assert obs(rec, "crossing") == [[0x00010000, 0x00010010, 16384, 3, 2, 1, 0, 3]]
    # 16386, released to destruction; the next object takes 16387, not its slot
    assert obs(rec, "no-reuse") == [[0x00010020, 0x00010030]]
    # a texture at 70000 and its level surface at 70001, the same one twice
    assert obs(rec, "past-65535") == [[0x000E1700, 0x000E1710, 0x000E1710, 70001]]
    # index 143359 at 0x001FFFF0; 143360 built and one more refused at
    # SA2_D3D9_OBJ_BASE; every object's words; a Lock through the destroyed
    # 16386 stops at its own address
    assert obs(rec, "capacity") == [[0x001FFFF0, 143359, 143360, 143360, 1, 0x03200000, 0,
                                     1, 0x00010020, 0, 1]]
    assert "the fabricated-object pool of 143360 is exhausted" in done.stdout.decode()


def test_a_wide_layout_keeps_the_first_block_alone(built):
    done, rec = run(built, "positive", "wide")
    clean(done, rec)
    assert obs(rec, "wide") == [[0x0323FFC0, 4096, 1, 0, 0, 0]]
    assert rec["stats"]["object_stride"] == 64 and rec["stats"]["object_fill"] == 1
    assert "the fabricated-object pool of 4096 is exhausted" in done.stdout.decode()


def test_constant_handles_carry_overflow_indices_and_refuse_past_fifteen_bits(built):
    done, rec = run(built, "positive", "table")
    clean(done, rec)
    assert obs(rec, "table") == [[0x0001E200, 0xCE200000, 0x0004FFF0, 0xFFFF0000, 1, 32768,
                                  32769, 0x00050000]]
    assert "the table would take pool index 32768" in done.stdout.decode()


def test_the_pool_left_at_the_first_block_traps_at_the_crossing(built):
    done, rec = run(built, "cap", "pool")
    assert done.returncode == 3 and rec is None
    assert b"the fabricated-object pool of 16384 is exhausted" in done.stderr


def test_sixteen_bit_level_indices_lose_the_surface_past_65535(built):
    done, rec = run(built, "lvl16", "pool")
    assert done.returncode == 1, done.stdout.decode() + done.stderr.decode()
    assert rec is not None, "the probe wrote no results file"
    assert rec["host_fault"] is None
    assert "past-65535" in rec["failed_ids"]
    assert obs(rec, "crossing") == [[0x00010000, 0x00010010, 16384, 3, 2, 1, 0, 3]]


def test_handles_read_off_the_first_blocks_address_fail_in_the_overflow_block(built):
    done, rec = run(built, "handle", "table")
    assert done.returncode == 1
    assert "table-20000" in rec["failed_ids"]
    done, rec = run(built, "handle", "first")
    clean(done, rec)
    assert obs(rec, "first-block") == [FIRST_BLOCK]


def test_a_changed_word_moves_the_first_blocks_bytes(built):
    done, rec = run(built, "word3", "first")
    assert done.returncode == 1
    assert rec["failed_ids"] == ["first-block-words"]
    got = obs(rec, "first-block")[0]
    assert got[3] == 16384 and got[6:8] != FIRST_BLOCK[6:8] and got[8:] == FIRST_BLOCK[8:]


def test_cube_faces_cross_into_the_overflow_block_and_past_65535(built):
    done, rec = run(built, "positive", "cube")
    clean(done, rec)
    # a cube at 16383 (0x0323FFF0) whose six faces are 16384 to 16389 in the overflow
    # block, each the same surface when asked again, released to destruction
    assert obs(rec, "cube-crossing") == [[0x0323FFF0, 0x00010000, 0x00010050, 0x00010050, 0,
                                          16390, 0]]
    # a cube at 70000 whose faces are 70001 to 70006
    assert obs(rec, "cube-past-65535") == [[0x000E1700, 0x000E1710, 0x000E1760, 0x000E1760, 0,
                                            70007, 0]]
    assert rec["stats"]["level_surfaces"] == 12


def test_cube_faces_indexed_off_the_first_blocks_address_fail_at_the_crossing(built):
    # The mutant's face index wraps below the overflow block's first surface, so the second
    # GetCubeMapSurface reads past the runtime's object array: a host fault, which the probe
    # records against the observation it struck. The results must exist and name the crossing
    # as the first failure; a mutant that crashed or failed before the crossing names another
    # (host-fault outside an observation) or writes none, and fails here.
    done, rec = run(built, "cubeaddr", "cube")
    assert done.returncode == 1, done.stdout.decode() + done.stderr.decode()
    assert rec is not None, "the probe wrote no results file"
    assert rec["failed_ids"][0] == "cube-crossing"
    assert rec["host_fault"] == {"signal": 11, "at": "cube-crossing"}
    assert "FAIL cube-crossing: host fault (signal 11)" in done.stdout.decode()
    done, rec = run(built, "cubeaddr", "first")
    clean(done, rec)
    assert obs(rec, "first-block") == [FIRST_BLOCK]
