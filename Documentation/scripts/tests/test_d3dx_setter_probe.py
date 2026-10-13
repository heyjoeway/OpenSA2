"""Authored CPU CTAB setters through real live-state guest stdcall and vtables.

Default payload literals come from retained Wine/Proton PE014c observations,
not production conversion helpers. Fatal cases are runtime policies and retain
partial effects. No native, recorder/player, replay or M7 proof is supplied.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig"
PROBE = ROOT / "harness/d3dx_setter_probe.c"
RUNTIME = ROOT / "runtime/sa2_d3d9.c"


def child(folder, argv, env, timeout=120):
    folder.mkdir()
    (folder / "command.json").write_text(json.dumps({"argv": argv, "cwd": str(ROOT), "env": env}, indent=2)+"\n")
    done = subprocess.run(argv, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
    (folder / "stdout.log").write_bytes(done.stdout)
    (folder / "stderr.log").write_bytes(done.stderr)
    (folder / "exit.json").write_text(json.dumps({"exit": done.returncode,
        "stdout_sha256": hashlib.sha256(done.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(done.stderr).hexdigest()}, indent=2)+"\n")
    return done


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    assert ZIG.is_file(), f"required compiler missing: {ZIG}"
    d = tmp_path_factory.mktemp("setter-owner")
    (d / "child-tmp").mkdir()
    env = {"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": str(d / "child-tmp"),
           "ZIG_LOCAL_CACHE_DIR": str(d / "zig-local"), "ZIG_GLOBAL_CACHE_DIR": str(d / "zig-global")}
    source = RUNTIME.read_text()
    anchor = "payload[0][2] = 1u;"
    assert source.count(anchor) == 1
    mutant = d / "wrong-int-padding.c"
    mutant.write_text(source.replace(anchor, "payload[0][2] = 0u;", 1))
    exe = {}
    guard = "node.raw_count != d[3] || "
    assert source.count(guard) == 1
    widened = d / "widened-root.c"
    widened.write_text(source.replace(guard, "", 1))
    for name, runtime in [("current", RUNTIME), ("padding", mutant), ("widened", widened)]:
        exe[name] = d / name
        # A unit build: the device's closed world (runtime/sa2_d3d9.c) would trap the methods off the M7 list this probe drives, so it is compiled open.
        argv = [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-D_GNU_SOURCE", "-O1", "-Wall", "-Wextra", "-Werror",
                "-DSA2_D3D9_OPEN_WORLD_UNIT_BUILD",
                "-I", str(ROOT / "runtime"), str(PROBE), str(runtime), str(ROOT / "runtime/sa2_guest_call.c"), "-lm", "-o", str(exe[name])]
        done = child(d / f"compile-{name}", argv, env)
        assert done.returncode == 0, done.stdout.decode()+done.stderr.decode()
    return d, exe


def run(built, mode, name="current"):
    d, exe = built
    done = child(d / f"run-{name}-{mode}", [str(exe[name]), mode],
                 {"PATH": os.defpath, "LC_ALL": "C", "TMPDIR": str(d / "child-tmp")}, 30)
    rows = [json.loads(line) for line in done.stdout.decode().splitlines()]
    return done, rows


def events(rows, event):
    return [r for r in rows if r["event"] == event]


def assert_case(rows, expected, factor=1):
    case = expected["case"]
    calls = [r for r in events(rows, "sink") if r["case"] == case]
    want = expected["calls"]
    assert [(r["slot"], r["index"], r["count"], r["bytes"]) for r in calls] == [
        (r["slot"], r["register_index"], r["register_count"], r["bytes"]) for r in want]
    assert all(r["this"] == 0x03009000 and r["live_state"] is True and
               r["pc"] == 0x00410000+16*r["slot"] for r in calls)
    ret = [r for r in events(rows, "return") if r["case"] == case]
    assert len(ret) == 1
    api = case.split("-")[-1]
    slot = {"SetBool":13, "SetInt":15, "SetFloat":17, "SetVector":19, "SetMatrix":21}[api]
    assert ret[0] == {"event":"return", "case":case, "hr":expected["hr"], "helper_delta":factor*len(want),
        "scratch_alloc_delta":int(bool(want)), "scratch_free_delta":int(bool(want)), "heap_live_delta":0,
        "pop":20, "eip":0x00210000+16*(15*128+slot), "failures":0}
    logs = [r for r in events(rows, "log") if r["case"] == case]
    assert logs[0]["slot"] == slot and logs[0]["type"] == 15 and logs[0]["caller"] == 0x00401000
    assert logs[0]["answered"] == (2 if want else 1) and logs[0]["ret"] == expected["hr"]
    assert len(logs) == 1+int(factor==2)*len(want)
    if factor == 2:
        assert [(r["type"],r["slot"],r["answered"],r["caller"],r["ret"]) for r in logs[1:]] == [
            (1,w["slot"],2,0xDEAD0000,0) for w in want]


def assert_default(rows):
    assert [r["case"] for r in events(rows,"return")] == [r["case"] for r in DEFAULT_EXPECTED]
    for expected in DEFAULT_EXPECTED:
        assert_case(rows, expected)
    assert events(rows,"summary") == [{"event":"summary","failures":0,"sink_calls":44,
        "scratch_allocs":38,"scratch_frees":38,"heap_live":0,"helper_depth":1}]
    assert not events(rows,"check-failed")
    seq = [r["seq"] for r in events(rows,"log")]
    assert seq == sorted(set(seq))


def test_sixty_retained_literal_cases_use_foreign_sink_and_real_helper(built):
    done, rows = run(built,"default")
    assert done.returncode == 0 and done.stderr == b""
    assert_default(rows)
    setup = events(rows,"setup")
    assert len(setup) == 1 and setup[0]["device"] != setup[0]["sink"] == 0x03009000


def test_wrong_int_padding_is_detected_by_literal_observation_owner(built):
    done, rows = run(built,"default","padding")
    assert done.returncode == 0 and done.stderr == b""
    with pytest.raises(AssertionError):
        assert_default(rows)
    integer = [r for r in events(rows,"sink") if "-i-" in r["case"]]
    assert len(integer) == 10
    assert [r["bytes"][16:24] for r in integer] == ["00000000"]*10
    assert [w["bytes"][16:24] for e in DEFAULT_EXPECTED if "-i-" in e["case"] for w in e["calls"]] == ["01000000"]*10


def test_forwarding_sink_preserves_six_banks_neighbors_and_unset_maps(built):
    done, rows = run(built,"forward")
    assert done.returncode == 0 and done.stderr == b""
    chosen = [2,32,11,41,15,45]
    for i in chosen:
        assert_case(rows,DEFAULT_EXPECTED[i],2)
    words = [[0x3FA00000,0,0,0],[0xC0200000,0,0,0],[0xFFFFFFEF,0,1,0],[0xFFFFFFE1,0,1,0],[7],[0]]
    sentinel = [0x12345678,0x89ABCDEF,0x10203040,0x50607080]
    expected = []
    for step in range(6):
        for bank in range(6):
            expected.append({"event":"bank","step":step,"bank":bank,
                "words":words[bank] if bank<=step else [0]*(4 if bank<4 else 1),
                "neighbor":sentinel if bank<4 else sentinel[:1]})
    assert events(rows,"bank") == expected
    assert events(rows,"unset") == [{"event":"unset","step":i,"delta":5-i} for i in range(6)]
    assert events(rows,"summary") == [{"event":"summary","failures":0,"sink_calls":6,
        "scratch_allocs":6,"scratch_frees":6,"heap_live":0,"helper_depth":2}]


def test_nested_release_input_and_scratch_overwrite_preserve_outer_payload_and_log(built):
    done, rows = run(built,"nested")
    assert done.returncode == 0 and done.stderr == b""
    calls = events(rows,"sink")
    assert [(r["case"],r["slot"],r["index"],r["count"],r["bytes"]) for r in calls] == [
        ("nested-outer",94,24,1,"0000803f0000a0400000000000000000"),
        ("nested-inner",109,7,1,"000020c0000000000000000000000000"),
        ("nested-outer",94,25,1,"000000400000c0400000000000000000"),
        ("nested-outer",94,26,1,"000040400000e0400000000000000000")]
    assert calls[0]["scratch"] == calls[2]["scratch"] == calls[3]["scratch"] != calls[1]["scratch"]
    scratch = events(rows,"nested-scratch")
    assert scratch == [{"event":"nested-scratch","outer":calls[0]["scratch"],"inner":calls[1]["scratch"],"live16":2}]
    assert events(rows,"return") == [{"event":"return","case":"nested-outer","hr":0,"helper_delta":6,
        "scratch_alloc_delta":2,"scratch_free_delta":2,"heap_live_delta":-1,"pop":20,
        "eip":0x00210000+16*(15*128+21),"failures":0}]
    logs = events(rows,"log")
    assert [(r["slot"],r["caller"],r["answered"],r["ret"]) for r in logs] == [(21,0x00401000,2,0),(2,0xDEAD0000,2,0),(17,0xDEAD0000,2,0)]
    assert [r["seq"]-logs[0]["seq"] for r in logs] == [0,1,2]
    assert logs[0]["self"] == logs[1]["self"] != logs[2]["self"]
    assert events(rows,"summary") == [{"event":"summary","failures":0,"sink_calls":4,
        "scratch_allocs":2,"scratch_frees":2,"heap_live":0,"helper_depth":3}]


@pytest.mark.parametrize("mode",["fresh-slot","fresh-ft"])
def test_each_matrix_call_reloads_device_slot_and_live_registered_function(built,mode):
    done, rows = run(built,mode)
    assert done.returncode == 0 and done.stderr == b""
    calls = events(rows,"sink")
    first = 0x00410000+16*94
    assert [r["pc"] for r in calls] == ([first,first+2048,first+2048] if mode=="fresh-slot" else [first]*3)
    assert [r["index"] for r in calls] == [24,25,26]
    assert [r["bytes"] for r in calls] == ["0000803f0000a0400000000000000000","000000400000c0400000000000000000","000040400000e0400000000000000000"]
    assert len(events(rows,"replacement")) == 2
    assert events(rows,"summary")[0]["failures"] == 0 and events(rows,"summary")[0]["heap_live"] == 0


def test_only_retained_exact_scalar_sink_failure_returns_success(built):
    done, rows = run(built,"observed-failure")
    assert done.returncode == 0 and done.stderr == b""
    assert events(rows,"return")[0]["hr"] == 0
    assert events(rows,"sink")[0]["bytes"] == "0000a03f000000000000000000000000"
    assert events(rows,"summary") == [{"event":"summary","failures":0,"sink_calls":1,
        "scratch_allocs":1,"scratch_frees":1,"heap_live":0,"helper_depth":1}]


@pytest.mark.parametrize("mode",["null-handle","foreign-handle","stale-handle"])
def test_runtime_handle_policy_returns_invalidcall_without_dispatch_or_scratch(built,mode):
    done, rows = run(built,mode)
    assert done.returncode == 0 and done.stderr == b""
    assert events(rows,"policy-return") == [{"event":"policy-return","case":mode,"hr":0x8876086C,"sink_calls":0,"scratch_allocs":0,"scratch_frees":0}]
    assert events(rows,"summary")[0]["heap_live"] == 0
    assert events(rows,"log")[0]["answered"] == 2


@pytest.mark.parametrize("mode,status,calls,allocs,helper",[
    ("version-mismatch",1,0,0,0),("version2",1,0,0,0),
    ("array-child",1,0,0,0),("member-child",1,0,0,0),("shape",1,0,0,0),
    ("over-scalar",1,0,0,0),("over-vector",1,0,0,0),("over-rows",1,0,0,0),("over-columns",1,0,0,0),
    ("extent",1,0,0,0),("conversion",1,0,0,0),("conversion-overflow",1,0,0,0),("wrong-device",1,0,0,0),
    ("dead-device",1,0,0,0),("alloc",1,0,0,0),("unregistered",3,0,1,0),
    ("null-device",2,0,1,0),("null-vector",2,0,0,0),("null-matrix",2,0,0,0),
    ("single-failure",1,1,1,1),("bad-pop",1,1,1,3),("later-failure",1,2,1,6),
])
def test_separate_fatal_children_retain_actual_predispatch_or_partial_effects(built,mode,status,calls,allocs,helper):
    done, rows = run(built,mode)
    assert done.returncode == 3 and done.stderr
    trap = events(rows,"trap")
    assert len(trap) == 1
    assert (trap[0]["status"],trap[0]["sink_calls"],trap[0]["scratch_allocs"],trap[0]["scratch_frees"],trap[0]["helper_calls"]) == (status,calls,allocs,0,helper)
    assert len(events(rows,"sink")) == calls
    assert not events(rows,"return") and not events(rows,"summary")
    logs = events(rows,"log")
    assert logs[0]["answered"] == 0 and logs[0]["ret"] == 0
    assert [r["seq"]-logs[0]["seq"] for r in logs] == list(range(len(logs)))
    if mode=="bad-pop":
        assert events(rows,"completed-effect") == [{"event":"completed-effect","case":mode,"index":7,"words":[0x3FA00000,0,0,0]}]
        assert b"left ESP" in done.stderr
        assert [(r["type"],r["slot"],r["answered"]) for r in logs] == [(15,17,0),(1,94,2),(1,95,2)]
    if mode=="later-failure":
        assert events(rows,"completed-effect") == [{"event":"completed-effect","case":mode,"index":24,"words":[0x3F800000,0x40A00000,0,0]},
                                                   {"event":"completed-effect","case":mode,"index":25,"words":[0x40000000,0x40C00000,0,0]}]
        assert b"completed effects retained" in done.stderr
        assert [r["index"] for r in events(rows,"sink")] == [24,25]
        assert [(r["type"],r["slot"],r["answered"]) for r in logs] == [(15,21,0),(1,94,2),(1,95,2),(1,94,2),(1,95,2)]


@pytest.mark.parametrize("mode,calls",[("over-scalar",1),("over-vector",1),("over-rows",2),("over-columns",3)])
def test_normalized_reflection_cannot_widen_unobserved_root_setters(built,mode,calls):
    done, rows = run(built,mode,"widened")
    assert done.returncode == 0 and done.stderr == b""
    assert len(events(rows,"sink")) == calls
    assert events(rows,"return")[0]["hr"] == 0
    assert not events(rows,"trap")


def test_readable_guest_name_setter_is_labeled_runtime_policy(built):
    done, rows = run(built,"guest-name")
    assert done.returncode == 0 and done.stderr == b""
    assert events(rows,"policy-return") == [{"event":"policy-return","case":"guest-name","hr":0,"sink_calls":1,"scratch_allocs":1,"scratch_frees":1}]
    assert [(r["slot"],r["index"],r["count"],r["bytes"]) for r in events(rows,"sink")] == [(94,7,1,"0000a03f000000000000000000000000")]
    assert events(rows,"log")[0]["answered"] == 2
    assert events(rows,"summary") == [{"event":"summary","failures":0,"sink_calls":1,"scratch_allocs":1,"scratch_frees":1,"heap_live":0,"helper_depth":1}]

# Literal default payloads copied from retained numbered raw Wine observations.
DEFAULT_EXPECTED = json.loads(r'''
[
  {
    "case": "setter-vs-s-SetBool",
    "calls": [
      {
        "case": "setter-vs-s-SetBool",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000803f000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-s-SetInt",
    "calls": [
      {
        "case": "setter-vs-s-SetInt",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "000088c1000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-s-SetFloat",
    "calls": [
      {
        "case": "setter-vs-s-SetFloat",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000a03f000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-s-SetVector",
    "calls": [
      {
        "case": "setter-vs-s-SetVector",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000a03f000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-s-SetMatrix",
    "calls": [
      {
        "case": "setter-vs-s-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000803f000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-v-SetBool",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-v-SetInt",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-v-SetFloat",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-v-SetVector",
    "calls": [
      {
        "case": "setter-vs-v-SetVector",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 11,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000a03f000020c00000704000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-v-SetMatrix",
    "calls": [
      {
        "case": "setter-vs-v-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 11,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000803f000000400000404000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-i-SetBool",
    "calls": [
      {
        "case": "setter-vs-i-SetBool",
        "outcome": "device-call",
        "slot": 96,
        "method": "SetVertexShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "01000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-i-SetInt",
    "calls": [
      {
        "case": "setter-vs-i-SetInt",
        "outcome": "device-call",
        "slot": 96,
        "method": "SetVertexShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "efffffff000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-i-SetFloat",
    "calls": [
      {
        "case": "setter-vs-i-SetFloat",
        "outcome": "device-call",
        "slot": 96,
        "method": "SetVertexShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "01000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-i-SetVector",
    "calls": [
      {
        "case": "setter-vs-i-SetVector",
        "outcome": "device-call",
        "slot": 96,
        "method": "SetVertexShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "01000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-i-SetMatrix",
    "calls": [
      {
        "case": "setter-vs-i-SetMatrix",
        "outcome": "device-call",
        "slot": 96,
        "method": "SetVertexShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "01000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-b-SetBool",
    "calls": [
      {
        "case": "setter-vs-b-SetBool",
        "outcome": "device-call",
        "slot": 98,
        "method": "SetVertexShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "07000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-b-SetInt",
    "calls": [
      {
        "case": "setter-vs-b-SetInt",
        "outcome": "device-call",
        "slot": 98,
        "method": "SetVertexShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-b-SetFloat",
    "calls": [
      {
        "case": "setter-vs-b-SetFloat",
        "outcome": "device-call",
        "slot": 98,
        "method": "SetVertexShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-b-SetVector",
    "calls": [
      {
        "case": "setter-vs-b-SetVector",
        "outcome": "device-call",
        "slot": 98,
        "method": "SetVertexShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-b-SetMatrix",
    "calls": [
      {
        "case": "setter-vs-b-SetMatrix",
        "outcome": "device-call",
        "slot": 98,
        "method": "SetVertexShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-mr-SetBool",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mr-SetInt",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mr-SetFloat",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mr-SetVector",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mr-SetMatrix",
    "calls": [
      {
        "case": "setter-vs-mr-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 20,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000803f000000400000404000000000",
        "authored_return_hr": "00000000"
      },
      {
        "case": "setter-vs-mr-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 21,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000a0400000c0400000e04000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-vs-mc-SetBool",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mc-SetInt",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mc-SetFloat",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mc-SetVector",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-vs-mc-SetMatrix",
    "calls": [
      {
        "case": "setter-vs-mc-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 24,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000803f0000a0400000000000000000",
        "authored_return_hr": "00000000"
      },
      {
        "case": "setter-vs-mc-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 25,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "000000400000c0400000000000000000",
        "authored_return_hr": "00000000"
      },
      {
        "case": "setter-vs-mc-SetMatrix",
        "outcome": "device-call",
        "slot": 94,
        "method": "SetVertexShaderConstantF",
        "register_index": 26,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "000040400000e0400000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-s-SetBool",
    "calls": [
      {
        "case": "setter-ps-s-SetBool",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "00000000000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-s-SetInt",
    "calls": [
      {
        "case": "setter-ps-s-SetInt",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000f8c1000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-s-SetFloat",
    "calls": [
      {
        "case": "setter-ps-s-SetFloat",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "000020c0000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-s-SetVector",
    "calls": [
      {
        "case": "setter-ps-s-SetVector",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000b040000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-s-SetMatrix",
    "calls": [
      {
        "case": "setter-ps-s-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 7,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000ca42000000000000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-v-SetBool",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-v-SetInt",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-v-SetFloat",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-v-SetVector",
    "calls": [
      {
        "case": "setter-ps-v-SetVector",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 11,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000b0400000c8c00000f84000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-v-SetMatrix",
    "calls": [
      {
        "case": "setter-ps-v-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 11,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000ca420000cc420000ce4200000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-i-SetBool",
    "calls": [
      {
        "case": "setter-ps-i-SetBool",
        "outcome": "device-call",
        "slot": 111,
        "method": "SetPixelShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "00000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-i-SetInt",
    "calls": [
      {
        "case": "setter-ps-i-SetInt",
        "outcome": "device-call",
        "slot": 111,
        "method": "SetPixelShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "e1ffffff000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-i-SetFloat",
    "calls": [
      {
        "case": "setter-ps-i-SetFloat",
        "outcome": "device-call",
        "slot": 111,
        "method": "SetPixelShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "feffffff000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-i-SetVector",
    "calls": [
      {
        "case": "setter-ps-i-SetVector",
        "outcome": "device-call",
        "slot": 111,
        "method": "SetPixelShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "05000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-i-SetMatrix",
    "calls": [
      {
        "case": "setter-ps-i-SetMatrix",
        "outcome": "device-call",
        "slot": 111,
        "method": "SetPixelShaderConstantI",
        "register_index": 2,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "65000000000000000100000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-b-SetBool",
    "calls": [
      {
        "case": "setter-ps-b-SetBool",
        "outcome": "device-call",
        "slot": 113,
        "method": "SetPixelShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "00000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-b-SetInt",
    "calls": [
      {
        "case": "setter-ps-b-SetInt",
        "outcome": "device-call",
        "slot": 113,
        "method": "SetPixelShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-b-SetFloat",
    "calls": [
      {
        "case": "setter-ps-b-SetFloat",
        "outcome": "device-call",
        "slot": 113,
        "method": "SetPixelShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-b-SetVector",
    "calls": [
      {
        "case": "setter-ps-b-SetVector",
        "outcome": "device-call",
        "slot": 113,
        "method": "SetPixelShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-b-SetMatrix",
    "calls": [
      {
        "case": "setter-ps-b-SetMatrix",
        "outcome": "device-call",
        "slot": 113,
        "method": "SetPixelShaderConstantB",
        "register_index": 3,
        "register_count": 1,
        "words_per_register": 1,
        "bytes": "01000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-mr-SetBool",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mr-SetInt",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mr-SetFloat",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mr-SetVector",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mr-SetMatrix",
    "calls": [
      {
        "case": "setter-ps-mr-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 20,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000ca420000cc420000ce4200000000",
        "authored_return_hr": "00000000"
      },
      {
        "case": "setter-ps-mr-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 21,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000d2420000d4420000d64200000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  },
  {
    "case": "setter-ps-mc-SetBool",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mc-SetInt",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mc-SetFloat",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mc-SetVector",
    "calls": [],
    "hr": 0
  },
  {
    "case": "setter-ps-mc-SetMatrix",
    "calls": [
      {
        "case": "setter-ps-mc-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 24,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000ca420000d2420000000000000000",
        "authored_return_hr": "00000000"
      },
      {
        "case": "setter-ps-mc-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 25,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000cc420000d4420000000000000000",
        "authored_return_hr": "00000000"
      },
      {
        "case": "setter-ps-mc-SetMatrix",
        "outcome": "device-call",
        "slot": 109,
        "method": "SetPixelShaderConstantF",
        "register_index": 26,
        "register_count": 1,
        "words_per_register": 4,
        "bytes": "0000ce420000d6420000000000000000",
        "authored_return_hr": "00000000"
      }
    ],
    "hr": 0
  }
]
''')
