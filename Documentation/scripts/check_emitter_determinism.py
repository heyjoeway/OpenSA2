#!/usr/bin/env python3
"""The pipeline must produce byte-identical output from the same inputs.

Design 3.4: "the emitter is deterministic; regenerating from the same binary
and map produces byte-identical output, and a gate checks it." Design 5 (S2)
exits on that gate being green. This is it.

The gate re-runs the adopted pipeline (remill 5.0.7 lifting a function's
bytes at its guest address, then the PC24 pass) over a small fixed panel of
functions and compares, against the values pinned in
Documentation/receipts/emitter-determinism-v1.json:

  * the sha256 of the lifted IR, of the patched IR, of the **folded** IR
    (`internalize.rewrite`, which `build.py` runs over all 165 modules) and of
    the **object** `zig cc` produces from it. Before the G2b fold the gate
    stopped at the patched IR: `build.py` then removed 364 stub defines and 207
    declares, redirected 207 references and compiled every module, and not one
    byte of that was pinned;
  * the pass's own counters for every fix class S2 made -- `ops_instrumented`,
    `fist_rewrites`, `fpatan_rewrites`, `control_word_masks_fixed`,
    `f80_accessor_attrs_fixed`, `fxam_c1_stores_fixed` (and the two refusal
    counts) -- so a fix that silently stops firing is drift, not a
    byte-identical pass;
  * the fold's counters for the five S5 speed passes (`speed_spill`,
    `speed_intrin`, `speed_pcround`, `speed_local`, `speed_fast`), pinned per
    member the same way, and each pass must rewrite something on some member
    (`SPEED_COUNTERS`); njScalor and njCalcVector are the members State
    localization and the guest-memory fast path rewrite;
  * the sha256 of the pipeline itself: the lifter binary, its x86 semantics
    bitcode, `pc24pass.py`, both helper IRs, and the four scripts that decide
    what is lifted, folded, tabulated and compiled -- `lift_set.py`,
    `internalize.py`, `build.py` and `gen_function_table.py`, none of which was
    pinned before -- and `speed_rewrite.py` with `state_local.py`, whose five
    passes `internalize.rewrite` has applied to every folded module since S5, so
    the folded IR and the object above are the speed-rewritten ones the build
    links. Plus the compiler's own version string. A digest pinned
    without the version of the code that produced it is a digest of nothing.

and then runs a **sensitivity** check: the same lifted IR through the pass with
`--no-fix-control-word --no-fix-f80-accessors --no-fix-fpatan --no-fix-fxam
--no-fix-fld-quiet` and no integer helper must produce *different* bytes for every panel member whose
counters are non-zero, and **per class**: with that one class turned off and
every other left on, a member whose counter for the class is non-zero must
produce different bytes too, and with none turned off the same bytes as the
pipeline (so the control reproduces the pass it is controlling). Without the
per-class runs a member carrying two classes -- 0x0082c037 carries FXAM and a
control-word mask -- stays visible through either one, and a `--refresh` would
pin an FXAM rewrite that is counted but not emitted, or a control run that
ignores `--no-fix-fxam`. Until the G2 fold the panel was three functions with
`control_word_masks_fixed 0, f80_accessor_attrs_fixed 0, fpatan_rewrites 0,
fist_rewrites 0`, and their pinned digests were byte-identical with the fixes
turned off: the gate could not have failed if every fix S2 made had been
deleted. The panel now carries one function per fix class:

  0x0077fc30  njScalor          5 arithmetic ops, no fix (the PC24 rounding itself)
  0x004273b0  njCalcVector      15 arithmetic ops, no fix
  0x0044b210  njPushUnitMatrix  no floating point at all (the control case)
  0x0086bf68  CRT acos body     fpatan 1, control word 5, f80 1, sqrtl
  0x008290bd  CRT sqrt body     control word 5, f80 1, sqrtl
  0x007bdca0  CRT float->int    fist 3, f80 1
  0x0082c037  CRT __trandisp2   fxam 2 (C1's sign on 6 arms), control word 1

The FLD quieting class (2026-10-12: remill quiets every FLD m32/m64 load while the sticky
IE flag is set; the pass makes the test the load's own signalling-NaN test, and m80 never
quiets) needed no new member: njScalor (6 sites) and njCalcVector (11) load through FLD
m32, and three of the CRT members through FLD m80 (0x0086bf68 1, 0x008290bd 1, 0x007bdca0
3; 0x0082c037 has none). No panel member has an FLD m64, so the panel does not see the m64
rewrite (`FLD_SNAN_TEST[64]` and the f64 read in `fld_snan_read`) stop firing; only
`tools/spike/pc24pass/test_pc24pass.py`'s pin on the lifted sub_00420210 (8 m32 sites,
1 m64) does. The per-class control turns the class off with `--no-fix-fld-quiet`.

The last was added with the FXAM fix class (2026-09-26): remill's FXAM stores C1 = 0
on a zero, NaN or infinity, the pass stores the sign, and a panel without an FXAM
would pin a pipeline in which that fix could stop firing unseen -- the G2 finding
again, one fix class later. The sensitivity run turns it off with `--no-fix-fxam`.
Every control reads its switches from one table, `CLASS_OFF`, and a `fix_*` switch the
pass reports that the table does not name fails the gate: a class without a control is
refused, not left uncontrolled. A `--refresh` pins nothing while any of this is blind.

Last, the pass's own tests (`tools/spike/pc24pass/test_pc24pass.py`) run in every
mode, and a `--refresh` pins nothing unless every check passes with none skipped.
The digests and the controls see a fix that stops firing, or fires and changes
nothing; they cannot see one that fires and writes the wrong value, because that
moves the bytes exactly as the real fix does. A pass that stores 1 into C1 instead
of the sign passed a refresh that way (the FX gate review's M4); the tests hold the
rewritten FXAM to this host's own `fxam` and fail it.

Hashes are not content: no lifted output is committed, only its digest, the
way the installation receipt pins the game's files.

`lift_set.py` also carries a dead `WRAP_LIFT` constant pointing at the wrap's
own lift directory. The G2b fold checked whether the eleven modules that exist
in both trees are *reused* rather than re-lifted, which would have put them
outside this pipeline: they are not. `WRAP_LIFT` is referenced nowhere, all 165
corpus modules come from `lift_set.lift_one`, and four of the eleven differ
byte-for-byte from the wrap's copies because the corpus pipeline passes
`--int-helper-ll` and the wrap's did not. The claim is recorded as checked and
dismissed rather than left implied.

The panel is deliberately small (seven functions, a few seconds) so this can run
in every gate pass. The whole-image determinism evidence lives in
Documentation/receipts/s2-build-time-sample-v1.json, which re-lifted 500
functions and found 500 identical, and compared every object across two
parallelism levels.

A pipeline this cannot run is a failure, not a skip: the private tree and
remill are what the project builds with. `--refresh` rewrites the receipt,
and is only honest after deliberately changing the pipeline; the gate's
whole purpose is that an accidental change is loud.

Usage: .venv/bin/python Documentation/scripts/check_emitter_determinism.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from gitlog import project_root

RECEIPT = "Documentation/receipts/emitter-determinism-v1.json"

# One member per fix class, plus the three the panel started with. See the docstring.
PANEL = [0x0077FC30, 0x004273B0, 0x0044B210, 0x0086BF68, 0x008290BD, 0x007BDCA0, 0x0082C037]

# The counters the pass reports for the fixes S2 made. A digest that matches while one of
# these has gone to zero is a fix that stopped firing, which is exactly what this gate
# could not see before.
COUNTERS = ("ops_instrumented", "fist_rewrites", "fpatan_rewrites", "fpatan_refused",
            "control_word_masks_fixed", "f80_accessor_attrs_fixed", "fxam_c1_stores_fixed",
            "fxam_refused", "fld_quiet_fixed", "fld_quiet_refused")

# The fold's own counters for the S5 speed passes (`internalize.rewrite`'s statistics, from
# tools/s2/corpus_run/speed_rewrite.py), held like the fix classes above. Each member's count
# is pinned, so a count that moves on any member is drift; a --refresh pins the new count,
# and the commit that refreshes must say why. Each pass must also rewrite something on some
# member, so a pass that stops firing on every member fails the gate and a --refresh refuses
# to pin it. A pass that stops on some members only is drift a --refresh would pin.
# Documentation/scripts/tests/test_emitter_determinism_speed.py holds all five. The
# localization pass's other two counts (`speed_local_kept`, `speed_local_refused`) and the
# fast path's `speed_fast_kept` are pinned in the receipt with the rest of the fold's
# statistics and not compared: a function kept or refused comes out of the pass as it went
# in, so none of them moves a digest.
SPEED_COUNTERS = ("speed_spill", "speed_intrin", "speed_pcround", "speed_local", "speed_fast")

# The switch that turns each fix class off alone; `None` is the FIST/FISTP rewrite, which is
# off when the pass is given no `--int-helper-ll`. The all-off control and the per-class
# controls both read this table, so a class cannot be off in one and on in the other; a
# class added to the pass without a switch here fails the gate (its `fix_*` stats key).
CLASS_OFF = {"fist_rewrites": None, "fpatan_rewrites": "--no-fix-fpatan",
             "control_word_masks_fixed": "--no-fix-control-word",
             "f80_accessor_attrs_fixed": "--no-fix-f80-accessors",
             "fxam_c1_stores_fixed": "--no-fix-fxam",
             "fld_quiet_fixed": "--no-fix-fld-quiet"}

# The pass's own tests, run in every mode (see `pass_tests`).
PASS_TESTS = "tools/spike/pc24pass/test_pc24pass.py"

# The pipeline whose output is being pinned. Every one of these changes the bytes.
PIPELINE_FILES = {
    "lifter": "private/lifters/remill/usr/local/bin/remill-lift-14",
    "semantics": "private/lifters/remill/usr/local/share/remill/14/semantics/x86.bc",
    "pass": "tools/spike/pc24pass/pc24pass.py",
    "pc_round_ll": "private/generated/spike/pc24pass/pc_round.ll",
    "x87_int_ll": "private/generated/s2/corpus_run/helper/x87_int.ll",
    # G2 finding 5: the gate stopped one stage before the compiler. It hashed `LIFT/*.ll`
    # and `LIFT/*.pc24.ll` and nothing else, while `build.py` then ran `internalize.rewrite`
    # over every module -- 364 stub defines removed, 207 declares, 207 references redirected
    # across 165 modules -- and compiled `FOLD/*`. Not one byte of that was pinned, and none
    # of the four scripts that decide it was either.
    "lift_set": "tools/s2/corpus_run/lift_set.py",
    "internalize": "tools/s2/corpus_run/internalize.py",
    # S5: `internalize.rewrite` ends with this module's passes, so it decides the folded
    # bytes as much as internalize.py does; the fourth and fifth passes live in
    # state_local.py.
    "speed_rewrite": "tools/s2/corpus_run/speed_rewrite.py",
    "state_local": "tools/s2/corpus_run/state_local.py",
    "build": "tools/s2/corpus_run/build.py",
    "gen_function_table": "tools/s2/wrap/gen_function_table.py",
}

# The compiler the fold is handed to. `zig cc` is the pipeline's clang; its version string
# is pinned so a toolchain move is drift rather than a silent recompile.
FOLD_TARGET = ["-target", "x86_64-linux-musl"]
FOLD_OPT = "-O2"


def sha256_text(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pipeline_digests(root: Path) -> dict[str, str]:
    out = {}
    for name, rel in PIPELINE_FILES.items():
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"emitter determinism: {rel} is missing; the pipeline is incomplete")
        out[name] = sha256_text(path)
    return out


def run_pipeline(root: Path, panel: list[int], out: Path) -> dict[str, dict]:
    """Lift and patch each address, returning {address: {digests, counters, sensitivity}}."""
    sys.path.insert(0, str(root / "tools" / "s2" / "corpus_run"))
    import lift_set  # the same code the corpus run used; never a second copy

    out.mkdir(parents=True, exist_ok=True)
    original = lift_set.LIFT
    lift_set.LIFT = out
    try:
        image = lift_set.memory_image()
        fmap = lift_set.function_map()
        digests: dict[str, dict] = {}
        for va in panel:
            record = fmap.get(va)
            if record is None:
                raise SystemExit(f"emitter determinism: {va:#010x} is not in the function map")
            size = int(record["size"])
            base = 0x400000
            raw = image[va - base: va - base + size]
            entry = lift_set.lift_one(va, raw)
            if not entry.get("lifted") or not (entry.get("pass") or {}).get("ll"):
                raise SystemExit(f"emitter determinism: pipeline failed at {va:#010x}: "
                                 f"{entry.get('error_head') or entry.get('pass')}")
            stats = json.loads((out / f"sub_{va:08X}.pc24.stats.json").read_text())
            patched = root / entry["pass"]["ll"]
            counters = {k: int(stats.get(k) or 0) for k in COUNTERS}
            digests[f"0x{va:08x}"] = {
                "ll_sha256": sha256_text(out / f"sub_{va:08X}.ll"),
                "pc24_sha256": sha256_text(patched),
                "counters": counters,
                "unfixed_pc24_sha256": unfixed_digest(root, out, va),
                **fold_digests(root, out, va),
            }
        return digests
    finally:
        lift_set.LIFT = original


def fold_digests(root: Path, out: Path, va: int) -> dict:
    """The post-fold IR and its object, for one panel member.

    `check_emitter_determinism` used to stop at `sub_<va>.pc24.ll`. What the corpus
    actually links is `internalize.rewrite` of that file, compiled by `zig cc`. Both are
    pinned here, so a change to the rewriter or to the compiler is drift the gate sees.
    """
    sys.path.insert(0, str(root / "tools" / "s2" / "corpus_run"))
    import internalize

    src = out / f"sub_{va:08X}.pc24.ll"
    folded, stats = internalize.rewrite(src.read_text(errors="replace"))
    fold_ll = out / f"sub_{va:08X}.fold.ll"
    fold_ll.write_text(folded)
    obj = out / f"sub_{va:08X}.fold.o"
    zig = root / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
    p = subprocess.run([str(zig), "cc"] + FOLD_TARGET + [FOLD_OPT, "-w", "-c", str(fold_ll),
                                                         "-o", str(obj)],
                       capture_output=True, text=True, cwd=str(out))
    if p.returncode:
        raise SystemExit(f"emitter determinism: the fold compile failed at {va:#010x}:\n{p.stderr}")
    return {"fold_sha256": sha256_text(fold_ll),
            "object_sha256": sha256_text(obj),
            "fold_counters": {k: (len(v) if isinstance(v, list) else v)
                              for k, v in sorted(stats.items())}}


def compiler_version(root: Path) -> str:
    zig = root / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
    p = subprocess.run([str(zig), "version"], capture_output=True, text=True)
    return (p.stdout or p.stderr).strip()


def unfixed_digest(root: Path, out: Path, va: int) -> str:
    """The same lifted IR through the pass with every S2 fix turned off.

    When this equals `pc24_sha256` the gate is blind to that function's fixes, which is
    what the three-function panel was: its pinned digests were byte-identical with
    `--no-fix-control-word --no-fix-f80-accessors --no-fix-fpatan`. `--no-fix-fxam`
    joined them with the FXAM class. The switches are `CLASS_OFF`'s, every one of them.
    """
    return pass_digest(root, out, va, list(CLASS_OFF), "unfixed")


def pass_digest(root: Path, out: Path, va: int, off: list[str], name: str) -> str:
    """The pass over `sub_<va>.ll` as `lift_set.lift_one` runs it, with the classes in `off`
    turned off by their `CLASS_OFF` switch, written to `sub_<va>.<name>.ll`."""
    dst = out / f"sub_{va:08X}.{name}.ll"
    cmd = [sys.executable, str(root / "tools" / "spike" / "pc24pass" / "pc24pass.py"),
           "--helper-ll", str(root / PIPELINE_FILES["pc_round_ll"])]
    if "fist_rewrites" not in off:
        cmd += ["--int-helper-ll", str(root / PIPELINE_FILES["x87_int_ll"])]
    cmd += [CLASS_OFF[k] for k in off if CLASS_OFF[k]]
    p = subprocess.run(cmd + [str(out / f"sub_{va:08X}.ll"), str(dst)], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"emitter determinism: the {name} control run failed at {va:#010x}:\n{p.stderr}")
    return sha256_text(dst)


def class_off_digest(root: Path, out: Path, va: int, cls: str | None) -> str:
    """The pass with one class off (`cls`), or none (None): the per-class controls."""
    return pass_digest(root, out, va, [cls] if cls else [], f"off-{cls or 'none'}")


def pass_tests(root: Path) -> list[str]:
    """The pass's own tests, run as `python test_pc24pass.py` runs them: [] when every check
    passed and none was skipped, otherwise what did not.

    The digests and the per-class controls see a fix that stops firing, or that fires and
    changes nothing; they cannot see one that fires and writes the wrong thing. A pass that
    stores 1 into C1 instead of the sign moves the FXAM member's bytes like the real fix does,
    so a `--refresh` after it pins it and every later check passes (the FX gate review's M4).
    What catches it is `test_pc24pass.py`, which holds the rewritten FXAM to this host's own
    `fxam`, and until this it ran in no gate. A skip is a check that did not run: the tree this
    gate runs in holds everything the three private-data checks need.
    """
    p = subprocess.run([sys.executable, str(root / PASS_TESTS)], capture_output=True, text=True,
                       cwd=str(root))
    lines = (p.stdout + p.stderr).splitlines()
    problems = [f"{PASS_TESTS}: {line.strip()}" for line in lines
                if line.startswith(("FAIL", "skipped:", "Traceback"))]
    if p.returncode or "all checks passed" not in lines:
        problems.append(f"{PASS_TESTS}: exit status {p.returncode}"
                        + ("" if "all checks passed" in lines else ", no 'all checks passed' line"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--refresh", action="store_true",
                        help="rewrite the pinned digests; only honest after a deliberate "
                             "pipeline change, and the commit must say what changed")
    args = parser.parse_args()
    root = project_root(args.root)
    out = root / "private" / "generated" / "determinism"

    pipeline = pipeline_digests(root)
    digests = run_pipeline(root, PANEL, out)

    # Sensitivity: every fix class must be represented by a panel member whose output
    # actually moves when the fixes are off. This is the check the old panel failed.
    fixed_classes = {k: 0 for k in CLASS_OFF}
    blind = []
    for address, now in sorted(digests.items()):
        active = [k for k in fixed_classes if now["counters"][k]]
        for k in active:
            fixed_classes[k] += now["counters"][k]
        if active and now["unfixed_pc24_sha256"] == now["pc24_sha256"]:
            blind.append(f"{address}: {', '.join(active)} non-zero but the output is "
                         f"byte-identical with the fixes off")
        va = int(address, 16)
        if class_off_digest(root, out, va, None) != now["pc24_sha256"]:
            blind.append(f"{address}: the per-class control does not reproduce the pipeline's "
                         f"pass with nothing turned off, so it controls nothing")
            continue
        for k in active:
            if class_off_digest(root, out, va, k) == now["pc24_sha256"]:
                blind.append(f"{address}: {k} is {now['counters'][k]} but the output is "
                             f"byte-identical with that class alone turned off")
    missing = [k for k, n in fixed_classes.items() if n == 0]
    if missing:
        blind.append("no panel member exercises: " + ", ".join(sorted(missing)))
    idle = [k for k in SPEED_COUNTERS
            if not any(now["fold_counters"].get(k) for now in digests.values())]
    if idle:
        blind.append("no panel member exercises the speed pass(es): " + ", ".join(idle))
    # A fix class the pass has and CLASS_OFF does not turn off has no control at all. The pass
    # reports each class's switch as `fix_<name>` (`--no-fix-<name>`), so one added without an
    # entry here refuses instead of passing uncontrolled.
    controlled = {"fix_" + s[len("--no-fix-"):].replace("-", "_") for s in CLASS_OFF.values() if s}
    for va in PANEL:
        stats = json.loads((out / f"sub_{va:08X}.pc24.stats.json").read_text())
        unknown = sorted(k for k in stats if k.startswith("fix_") and k not in controlled)
        if unknown:
            blind.append(f"0x{va:08x}: the pass reports fix switch(es) {', '.join(unknown)} that "
                         f"CLASS_OFF does not turn off, so no control covers them")
    failing = pass_tests(root)

    path = root / RECEIPT
    if args.refresh or not path.is_file():
        if failing or blind:
            print(f"emitter determinism FAIL: not pinning a pass that fails its own tests or a "
                  f"panel blind to a fix class; {RECEIPT} is left as it was:", file=sys.stderr)
            for line in failing + blind:
                print(f"  {line}", file=sys.stderr)
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": "sa2-recomp-receipt/v1",
            "id": "emitter-determinism-v1",
            "question": "Does the adopted pipeline produce byte-identical output from the same "
                        "binary and map, and is this gate able to see the fixes S2 made? "
                        "(design 3.4; the S2 exit's determinism gate)",
            "inputs": {"exe": "private/exe/sonic2app-unpacked.exe",
                       "function_map": "private/generated/function-map.json",
                       "pipeline": "remill-lift-14 --arch x86 --os windows, then "
                                   "tools/spike/pc24pass/pc24pass.py --helper-ll pc_round.ll "
                                   "--int-helper-ll x87_int.ll"},
            "command": "Documentation/scripts/check_emitter_determinism.py",
            "pipeline_files": PIPELINE_FILES,
            "pipeline_sha256": pipeline,
            "compiler": compiler_version(root),
            "post_fold": "`fold_sha256` is internalize.rewrite of the patched IR and "
                         "`object_sha256` is `zig cc -target x86_64-linux-musl -O2 -c` of "
                         "that: the two stages between the pinned IR and the program the "
                         "corpus runs, neither of which was pinned before the G2 fold",
            "panel": [f"0x{va:08x}" for va in PANEL],
            "panel_covers": {
                "0x0077fc30": "njScalor: 5 arithmetic ops, FLD m32 quieting tests",
                "0x004273b0": "njCalcVector: 15 arithmetic ops, FLD m32 quieting tests",
                "0x0044b210": "njPushUnitMatrix: no floating point (the control case)",
                "0x0086bf68": "CRT acos body: fpatan, control-word masks, f80 accessors, sqrtl",
                "0x008290bd": "CRT sqrt body: control-word masks, f80 accessors, sqrtl",
                "0x007bdca0": "CRT float-to-int: FIST/FISTP, f80 accessors",
                "0x0082c037": "CRT __trandisp2: FXAM's C1 sign on the zero, NaN and infinity arms, "
                              "control-word mask",
            },
            "fix_class_totals": fixed_classes,
            "digests": digests,
            "sensitivity": "every panel member with a non-zero fix counter produces different "
                           "bytes under --no-fix-control-word --no-fix-f80-accessors "
                           "--no-fix-fpatan --no-fix-fxam --no-fix-fld-quiet and no --int-helper-ll "
                           "(`unfixed_pc24_sha256` is that control run's digest), and under each "
                           "class's own switch alone, with every other class on; the same run "
                           "with nothing switched off reproduces `pc24_sha256`. Checked at every "
                           "run, pinned nowhere: each is compared with the pipeline's own digest; "
                           "every `fix_*` switch the pass reports is one of them, and a --refresh "
                           "writes nothing while any member is blind",
            "pass_tests": f"{PASS_TESTS} runs before anything is pinned and at every check: "
                          "every check passes and none is skipped, or the gate fails and a "
                          "--refresh writes nothing",
            "authority": "observed",
        }, indent=2, sort_keys=True) + "\n")
        print(f"emitter determinism: pinned {len(digests)} function(s) in {RECEIPT}")
        return 0

    pinned = json.loads(path.read_text())
    drift = list(blind) + failing
    if pinned.get("compiler") and pinned["compiler"] != compiler_version(root):
        drift.append(f"compiler: {compiler_version(root)} != pinned {pinned['compiler']}")
    for name, want in (pinned.get("pipeline_sha256") or {}).items():
        if pipeline.get(name) != want:
            drift.append(f"pipeline {name}: {pipeline.get(name)} != pinned {want}")
    for name in PIPELINE_FILES:
        if name not in (pinned.get("pipeline_sha256") or {}):
            drift.append(f"pipeline {name}: not pinned in {RECEIPT}")
    for address, now in sorted(digests.items()):
        was = pinned["digests"].get(address)
        if was is None:
            drift.append(f"{address}: not pinned in {RECEIPT}")
            continue
        for field in ("ll_sha256", "pc24_sha256", "unfixed_pc24_sha256",
                      "fold_sha256", "object_sha256"):
            if was.get(field) != now[field]:
                drift.append(f"{address} {field}: {now[field]} != pinned {was.get(field)}")
        for key, value in now["counters"].items():
            if (was.get("counters") or {}).get(key) != value:
                drift.append(f"{address} {key}: {value} != pinned "
                             f"{(was.get('counters') or {}).get(key)}")
        for key in SPEED_COUNTERS:
            if (was.get("fold_counters") or {}).get(key) != now["fold_counters"].get(key):
                drift.append(f"{address} {key}: {now['fold_counters'].get(key)} != pinned "
                             f"{(was.get('fold_counters') or {}).get(key)}")
    for address in sorted(set(pinned["digests"]) - set(digests)):
        drift.append(f"{address}: pinned but not re-run (the panel changed)")
    if drift:
        print("emitter determinism FAIL: the pipeline's output moved:", file=sys.stderr)
        for line in drift:
            print(f"  {line}", file=sys.stderr)
        print("  If the pipeline was changed deliberately, re-run with --refresh and say so in "
              "the commit; otherwise this is a real regression.", file=sys.stderr)
        return 1
    print(f"emitter determinism OK: {len(digests)} function(s) re-lifted, re-passed, "
          f"re-folded and re-compiled, every digest, counter and pipeline hash matches "
          f"{RECEIPT}; "
          f"{sum(fixed_classes.values())} fix sites across "
          f"{len([k for k, n in fixed_classes.items() if n])} classes, all visible to this gate, each "
          f"alone; {PASS_TESTS} passed with nothing skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
