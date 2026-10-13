"""The instructions remill cannot lift, executed by the runtime, held to the CPU.

`runtime/sa2_emulate.cpp` executes `paddusb`, `paddusw`, `cvtps2pi`,
`fnstenv`, `fldenv`, `fnsave`, `frstor` and both forms of `psubsw` for the
lifted code, which cannot.
`tools/s4/emulate_check.py` builds `harness/emulate_probe.cpp` and runs every
case twice: through the emulator over remill's State, and natively on the host,
which executes them all. These tests require the two to agree wherever
remill's model says they must, for every site lifted code reaches, with each
site's own bytes, and for every addressing shape, rounding mode and refusal.
Each comparison is also shown failing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import emulate_check as EC  # noqa: E402


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    if not EC.ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {EC.ZIG}")
    cases, skipped = EC.all_cases()
    probe = EC.build_probe(tmp_path_factory.mktemp("emulate"))
    return cases, skipped, EC.run(probe, cases)


def test_the_emulator_does_what_the_cpu_does(run):
    cases, _skipped, results = run
    problems = [p for c, r in zip(cases, results) for p in EC.compare(c, r)]
    assert problems == [], problems[:20]


def test_every_site_lifted_code_reaches_is_run_with_its_own_bytes(run):
    cases, skipped, _results = run
    assert skipped == []
    sites = {c.name.split()[1] for c in cases if c.name.startswith("site ")}
    kinds = {c.kind for c in cases if c.name.startswith("site ")}
    assert kinds == {"paddusb", "paddusw", "cvtps2pi", "fnstenv", "fldenv", "fnsave", "frstor",
                     "psubsw", "psubsw xmm"}
    assert "0x0080d42a" in sites            # the cvtps2pi the decoder thread died on


def test_the_refusals_are_refused(run):
    cases, _skipped, results = run
    for c, r in zip(cases, results):
        if c.mode == "r":
            assert EC.compare(c, r) == [], c.name


def _first(cases, results, pred):
    return next((c, r) for c, r in zip(cases, results) if pred(c))


def test_a_changed_mmx_result_is_caught(run):
    cases, _skipped, results = run
    c, r = _first(cases, results, lambda c: c.mode == "m" and c.kind == "paddusb")
    nat, emu = r.split("|", 1)
    n = nat.split()
    ctx = bytearray(bytes.fromhex(n[1]))
    ctx[32] ^= 1                                 # mm0's low byte, native side
    bad = f"{n[0]} {ctx.hex()} {' '.join(n[2:])} |{emu}"
    assert any("mm0" in p for p in EC.compare(c, bad))


def test_a_changed_xmm_result_is_caught(run):
    """psubsw's xmm form is the first emulated instruction that writes an XMM
    register, so the comparison of XMM registers is shown failing on it."""
    cases, _skipped, results = run
    c, r = _first(cases, results, lambda c: c.mode == "m" and c.kind == "psubsw xmm")
    nat, emu = r.split("|", 1)
    n = nat.split()
    ctx = bytearray(bytes.fromhex(n[1]))
    ctx[96] ^= 1                                 # xmm0's low byte, native side
    bad = f"{n[0]} {ctx.hex()} {' '.join(n[2:])} |{emu}"
    assert any("xmm0" in p for p in EC.compare(c, bad))


def test_a_changed_x87_register_is_caught(run):
    cases, _skipped, results = run
    c, r = _first(cases, results, lambda c: c.kind == "frstor")
    nat, emu = r.split("|", 1)
    n = nat.split()
    ctx = bytearray(bytes.fromhex(n[1]))
    ctx[224 + 28] ^= 1                           # ST(0)'s low byte
    bad = f"{n[0]} {ctx.hex()} {' '.join(n[2:])} |{emu}"
    assert any("ST(0)" in p for p in EC.compare(c, bad))


def test_a_wrong_continuation_is_caught(run):
    cases, _skipped, results = run
    c, r = _first(cases, results, lambda c: c.kind == "cvtps2pi" and c.mode == "m")
    nat, emu = r.split("|", 1)
    e = emu.split()
    e[2] = f"{int(e[2], 16) + 1:08x}"
    assert any("continues at" in p for p in EC.compare(c, f"{nat}| {' '.join(e)}"))


def test_a_refusal_that_executes_is_caught(run):
    cases, _skipped, results = run
    c, r = _first(cases, results, lambda c: c.mode == "r" and c.expect == 1)
    nat, emu = r.split("|", 1)
    e = emu.split()
    e[1] = "0"
    assert any("status 0" in p for p in EC.compare(c, f"{nat}| {' '.join(e)}"))


@pytest.mark.parametrize("name", sorted(EC.MUTATIONS))
def test_a_broken_emulator_is_caught(run, name, tmp_path):
    """Each mutation is a defect of a shape this emulator had or could have;
    the first is the FSAVE view the review found and the first harness passed."""
    cases, _skipped, _results = run
    probe = EC.build_probe(tmp_path, EC.mutated(name, tmp_path))
    results = EC.run(probe, cases)
    problems = [p for c, r in zip(cases, results) for p in EC.compare(c, r)]
    assert problems, f"{name}: the comparison passed a broken emulator"


def test_the_model_offsets_are_the_lifted_ir_s():
    offs = EC.lifted_fx_offsets()
    assert offs == {"cwd": 0, "swd": 2, "ftw": 4, "fop": 6, "ip": 8, "cs": 12, "dp": 16,
                    "ds": 20, "mxcsr": 24}


def _continuation(tmp_path, emulator=True):
    import json
    import subprocess
    exe = EC.build_continuation_probe(tmp_path, emulator)
    out = subprocess.run([str(exe), "200000"], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_the_continuation_runs_in_constant_host_stack(tmp_path):
    """A codec loop emulates an instruction every iteration; a continuation
    that was a call rather than a tail call would add frames per instruction."""
    if not EC.ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {EC.ZIG}")
    d = _continuation(tmp_path)
    loop = d["loop"]
    assert loop["status"] == 0 and loop["iterations"] == 200000
    # the entry emulates once, and every iteration but the last emulates again
    assert loop["emulated"] == loop["continued"] == 200000
    assert loop["frame_spread"] == 0
    assert loop["mm0"] == "ffffffffffffffff"          # paddusb saturated as the loop added


def test_no_row_after_the_instruction_is_a_named_trap_and_consumes_the_continuation(tmp_path):
    if not EC.ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {EC.ZIG}")
    d = _continuation(tmp_path)
    assert d["no_row"]["status"] == 1 and d["no_row"]["exit_pc"] == 0x00401003
    assert "no lifted continuation" in d["no_row"]["note"]
    assert d["bare_after"]["status"] == 2 and d["bare_after"]["exit_pc"] == 0x00401003


def test_a_build_without_the_emulator_traps_at_the_instruction(tmp_path):
    """S2's closed builds compile remill_runtime.cpp without sa2_emulate.cpp;
    the weak reference must leave them the trap they always had."""
    if not EC.ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {EC.ZIG}")
    d = _continuation(tmp_path, emulator=False)
    assert d["no_row"]["status"] == 2 and d["no_row"]["exit_pc"] == 0x00401000
    assert "links no emulator" in d["no_row"]["note"]


def test_every_emulate_hypercall_in_the_lifted_ir_is_a_census_site():
    """remill's IR is the ground truth for which instructions it could not
    lift. Every module's hypercall-256 count must equal the census sites a
    remill-faithful walk reaches, except the named data tables."""
    import json
    sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
    import common as C
    import emulated as EM
    extras = json.loads((ROOT / "Documentation" / "lifted-extras.json").read_text())["rows"]
    bad = [d for d in EM.ir_disagreements(C, extras) if not d[3] or d[1] is None]
    assert bad == [], bad


def test_the_lifted_code_reaches_the_continuation_by_a_tail_call():
    """The runtime's side is a guaranteed tail call; the lifted side is the
    optimizer's, so it is pinned here: in every object whose IR emulates an
    instruction, each reference to __remill_error is a jmp, not a call. A call
    would leave a frame per emulated instruction in a codec loop."""
    import json
    import shutil
    import subprocess
    objdump = shutil.which("objdump")
    assert objdump, "objdump is needed to read the lifted objects"
    sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
    import common as C
    import emulated as EM
    extras = json.loads((ROOT / "Documentation" / "lifted-extras.json").read_text())["rows"]
    objs = EM.hypercall_objects(C, extras)
    assert objs, "no lifted object emulates anything, which the census contradicts"
    calls, jumps, missing = [], 0, [o for o in objs if not o.is_file()]
    for o in objs:
        if not o.is_file():
            continue
        text = subprocess.run([objdump, "-dr", "--no-show-raw-insn", str(o)],
                              capture_output=True, text=True).stdout.splitlines()
        for i, line in enumerate(text):
            if "R_X86_64_PLT32\t__remill_error" in line or "R_X86_64_PLT32 __remill_error" in line:
                prev = text[i - 1]
                if "jmp" in prev:
                    jumps += 1
                else:
                    calls.append(f"{o.name}: {prev.strip()}")
    assert missing == [] and calls == [], (missing[:5], calls[:5])
    assert jumps >= len(objs)
