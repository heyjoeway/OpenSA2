"""runtime/sa2_cri_dsp.c: the CRI Sound Renderer's native DSP kernels, bit for bit the double
mode's lifted bodies.

tools/s8/native_kernels/cri_dsp_diff.py builds the differential harness: the runtime file as
tools/s4/link_s4.py compiles it, the double-mode lifted objects of 0x00842500 (biquad),
0x00843400 (comb), 0x00843460 (the reverb network) and 0x008418B0 (mix) as `__real_`, wrapped as
the production link wraps them. Each kernel runs a million ordinary cases (realistic values, edge
cases mixed in, wild finite bit patterns; every precision control under every rounding control,
the host's rounding mode the control word's or another; segment bases zero and not; the register
slots it is handed random bytes) twice, native bodies off and on, and the whole State and every
guest byte the kernel can reach must be equal. Every guard's cases (an infinity or NaN among the
inputs, an overlap the native order of reads and writes does not hold for, the network's outputs
overlapping without being one float and a signalling NaN reloaded through them, an access outside
the guest space, guest memory off its fast path, the bodies switched off) must take the lifted
body, and so must every call of a build without the double mode's marker, as the exact build is.
SA2_CRI_DSP's mask switches each kernel with the kernels it runs inline (the counters
SA2_CRI_DSP_STATS prints). The lifted objects must be the checkout's double mode: the harness
refuses a build whose pass, shims or their inputs have changed since it was made. The inputs are
private/ (the lifted objects); without them, or without the pinned zig, the build fails rather
than skips.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s8" / "native_kernels"))
import cri_dsp_diff as D  # noqa: E402

CASES = 1_000_000
SEED = "20261012"
GUARDS = ("nonfinite_input", "overlap", "outside_guest_space", "memory_off_fast_path",
          "switched_off")
KERNELS = ("biquad", "comb", "net", "mix")
# What the native bodies copy and what holds them to it: a change to any of these re-runs this
# file in the fast check (Documentation/scripts/fast_check.py picks a test that names a path).
INPUTS = ("runtime/sa2_cri_dsp.c", "tools/s8/f80_double.py", "tools/s8/f80_shims_double.c",
          "tools/s7/f80_shims.c", "tools/s7/f80_legalize.py",
          "tools/s8/native_kernels/cri_dsp_diff.c", "tools/s8/native_kernels/state_layout.cpp",
          "tools/s8/native_kernels/cri_dsp_diff.py")


@pytest.fixture(scope="module")
def double():
    return D.build("double")


@pytest.fixture(scope="module")
def nomarker():
    return D.build("nomarker")


def test_link_s4_compiles_and_wraps_the_kernels():
    assert "sa2_cri_dsp.c" in D.L4.RUNTIME_C
    # The poll wrap first; the CRI kernels in this order; other files' wraps (the Ninja math's,
    # runtime/sa2_ninja_math.c) may follow.
    assert D.L4.PRODUCTION_WRAPS[0] == "sub_427ee0"
    assert [w for w in D.L4.PRODUCTION_WRAPS if w in ("sub_842500", "sub_843400", "sub_843460",
                                                      "sub_8418b0")] == ["sub_842500", "sub_843400",
                                                                         "sub_843460", "sub_8418b0"]


def test_fast_check_picks_this_file_for_each_input():
    spec = importlib.util.spec_from_file_location(
        "fast_check", ROOT / "Documentation" / "scripts" / "fast_check.py")
    fc = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("fast_check", fc)
    spec.loader.exec_module(fc)
    me = Path(__file__).resolve()
    for p in INPUTS:
        assert (ROOT / p).is_file(), p
        assert me in fc.tests_for([p], [me]), p


def test_state_offsets_are_remills(double):
    r = D.run_harness(double, "layout")
    assert r["state_bytes"] == 3504
    assert r["ok"], {k: v for k, v in r["fields"].items() if v[0] != v[1]}
    assert len(r["fields"]) == 31


@pytest.mark.parametrize("kernel", KERNELS)
def test_native_body_equals_lifted_body(double, kernel):
    r = D.run_harness(double, kernel, str(CASES), SEED)
    assert r["mismatches"] == 0, r["first_mismatch"]
    ordinary = r["classes"]["none"]
    assert ordinary["cases"] == CASES
    # Realistic values never leave the native path; edge and wild ones may (a NaN made from an
    # overflow hands the call to the lifted body), and both precisions run natively.
    real = r["ordinary_by_values"]["realistic"]
    assert real["cases"] > CASES // 4 and real["native"] == real["cases"]
    assert ordinary["native"] > CASES // 2
    assert ordinary["native_pc24"] > CASES // 20 and ordinary["native_pc53_64"] > CASES // 3
    for g in GUARDS:
        assert r["classes"][g] == {"cases": CASES // 16, "native": 0, "native_pc24": 0,
                                   "native_pc53_64": 0, "mismatches": 0}, g
    if kernel == "net":
        # Among the overlap cases: outputs misaligned onto each other, and the first 512 of
        # those laid out so the lifted body's `fld [eax]` reloads a signalling NaN.
        assert r["partial_outputs"] > CASES // 16 // 8
        assert r["snan_reloads"] == {"sought": 512, "found": 512}


@pytest.mark.parametrize("kernel", KERNELS)
def test_exact_build_takes_the_lifted_body(nomarker, kernel):
    r = D.run_harness(nomarker, kernel, "100000", SEED)
    assert r["mismatches"] == 0, r["first_mismatch"]
    assert all(c["native"] == 0 for c in r["classes"].values()), r["classes"]
    assert r["classes"]["none"]["cases"] == 100000


# One realistic call of the biquad, the comb, the network and the mix (`calls`), under
# SA2_CRI_DSP: [native, lifted] per kernel as SA2_CRI_DSP_STATS prints them. A kernel switched off
# runs its lifted body, whose calls go through the wrappers: the lifted comb calls the biquad once,
# the lifted network calls the comb nine times.
ALL_ON = {"biquad": [1, 0], "comb": [1, 0], "net": [1, 0], "mix": [1, 0]}
ALL_OFF = {"biquad": [0, 11], "comb": [0, 10], "net": [0, 1], "mix": [0, 1]}
MASKS = {
    None: ALL_ON,
    "": ALL_ON,
    "0": ALL_OFF,
    "1": {"biquad": [11, 0], "comb": [0, 10], "net": [0, 1], "mix": [0, 1]},
    "2": ALL_OFF,                                    # the comb needs the biquad
    "3": {"biquad": [1, 0], "comb": [10, 0], "net": [0, 1], "mix": [0, 1]},
    "4": ALL_OFF,                                    # the network needs the comb and the biquad
    "5": {"biquad": [11, 0], "comb": [0, 10], "net": [0, 1], "mix": [0, 1]},
    "7": {"biquad": [1, 0], "comb": [1, 0], "net": [1, 0], "mix": [0, 1]},
    "8": {"biquad": [0, 11], "comb": [0, 10], "net": [0, 1], "mix": [1, 0]},
    "0xB": {"biquad": [1, 0], "comb": [10, 0], "net": [0, 1], "mix": [1, 0]},
    "0xE": {"biquad": [0, 11], "comb": [0, 10], "net": [0, 1], "mix": [1, 0]},
    "15": ALL_ON,
}


@pytest.mark.parametrize("mask", list(MASKS), ids=lambda m: "unset" if m is None else repr(m))
def test_sa2_cri_dsp_switches_each_kernel_with_what_it_inlines(double, mask):
    r = D.stats(double, mask)
    for k, (native, lifted) in MASKS[mask].items():
        assert (r[k]["native"], r[k]["lifted"], r[k]["off"]) == (native, lifted, lifted), k
        assert r["harness"]["calls"][k] == [native, lifted], k


# ------------------------------------------------------------------ the build the harness links


def fake_build(tmp: Path, summary_changes: dict | None = None, deps: dict | None = None) -> Path:
    """A copy of the x86_64 double build's records (its summary, the kernels' build.jsonl lines,
    its f80_shims.o), with `summary_changes` applied and `deps` recorded when given; the summary
    keeps the real one's time."""
    src = D.ARCHES["x86_64"][1]
    root = tmp / "build"
    root.mkdir()
    s = json.loads((src / "summary.json").read_text())
    s.update(summary_changes or {})
    if deps is not None:
        s["deps"] = deps
    (root / "summary.json").write_text(json.dumps(s))
    want = {f"0x{va:08x}" for va in D.KERNELS.values()}
    lines = [line for line in (src / "build.jsonl").read_text().splitlines()
             if json.loads(line).get("va") in want]
    (root / "build.jsonl").write_text("\n".join(lines) + "\n")
    shutil.copy(src / "f80_shims.o", root / "f80_shims.o")
    t = (src / "summary.json").stat().st_mtime
    os.utime(root / "summary.json", (t, t))
    return root


def refusal(root: Path) -> str:
    with pytest.raises(SystemExit) as e:
        D.lifted_objects("x86_64", root)
    msg = str(e.value)
    assert "rebuild it: source tools/env.sh; tools/slot.sh .venv/bin/python tools/s8/perf_f80.py " \
           f"build --target x86_64-linux-musl --mode double --out {root} --jobs 4" in msg, msg
    return msg


def current_deps() -> dict:
    return {p: D.sha256(ROOT / p) for p in ("tools/s8/f80_double.py", "tools/s7/f80_legalize.py",
                                            "tools/s8/f80_shims_double.c", "tools/s7/f80_shims.c")}


def test_harness_takes_a_current_build(tmp_path):
    root = fake_build(tmp_path)
    assert [p.name for p in D.lifted_objects("x86_64", root)] == [
        "ir_008418B0.o", "ir_00842500.o", "ir_00843400.o", "ir_00843460.o"]
    other = tmp_path / "with-deps"
    other.mkdir()
    assert len(D.lifted_objects("x86_64", fake_build(other, deps=current_deps()))) == 4


def test_harness_refuses_a_build_of_another_pass(tmp_path):
    msg = refusal(fake_build(tmp_path, {"pass_sha256": "0" * 64}))
    assert f"its pass tools/s8/f80_double.py was {'0' * 64} and is " \
           f"{D.sha256(ROOT / 'tools/s8/f80_double.py')} here" in msg


def test_harness_refuses_a_build_of_other_shims(tmp_path):
    s = json.loads((D.ARCHES["x86_64"][1] / "summary.json").read_text())
    msg = refusal(fake_build(tmp_path, {"shims": dict(s["shims"], source_sha256="1" * 64)}))
    assert f"its shims source tools/s8/f80_shims_double.c was {'1' * 64}" in msg


def test_harness_refuses_a_build_whose_recorded_input_changed(tmp_path):
    deps = dict(current_deps(), **{"tools/s7/f80_shims.c": "2" * 64})
    msg = refusal(fake_build(tmp_path, deps=deps))
    assert f"its input tools/s7/f80_shims.c was {'2' * 64} and is " \
           f"{D.sha256(ROOT / 'tools/s7/f80_shims.c')} here" in msg
    assert "f80_legalize" not in msg


def test_harness_refuses_a_build_older_than_its_unrecorded_inputs(tmp_path):
    root = fake_build(tmp_path, {"wall_seconds": 0})
    t = min((ROOT / p).stat().st_mtime for p in ("tools/s7/f80_legalize.py",
                                                  "tools/s7/f80_shims.c")) - 60
    os.utime(root / "summary.json", (t, t))
    msg = refusal(root)
    for p in ("tools/s7/f80_legalize.py", "tools/s7/f80_shims.c"):
        assert f"its input {p} was modified at" in msg and "records no deps" in msg, p


def test_harness_refuses_a_build_without_its_shims_object(tmp_path):
    root = fake_build(tmp_path)
    (root / "f80_shims.o").write_bytes(b"not the object")
    assert "its f80_shims.o is not the object its summary names" in refusal(root)


def test_android_build_is_rebuilt_by_android_game():
    assert D.rebuild_command("aarch64", D.ARCHES["aarch64"][1]) == (
        "source tools/env.sh; tools/slot.sh .venv/bin/python tools/s8/android_game.py compile "
        "--f80 double --jobs 4")
