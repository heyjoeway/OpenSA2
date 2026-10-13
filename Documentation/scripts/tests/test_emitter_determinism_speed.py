"""The emitter determinism gate's two checks on the S5 speed passes.

Documentation/scripts/check_emitter_determinism.py pins each panel member's fold counters
for the five passes of tools/s2/corpus_run/speed_rewrite.py (`SPEED_COUNTERS`; the fourth,
State localization, and the fifth, the guest-memory fast path, are
tools/s2/corpus_run/state_local.py), and requires each pass to rewrite something on some
member. So:

* a pass idle on every member fails the check, and a --refresh refuses to pin it;
* a pass idle on one member only is drift the check reports, and a --refresh pins it
  (the commit that refreshes says why).

No test covered either clause until the speed fold's review (NIT 1). Here `main()` runs
against a scratch `--root` with the pipeline stood in for: nothing is lifted, passed,
folded or compiled. The stand-in digests do not depend on the counts, so only the counter
clauses can see a count move. The counts are the panel's own as the gate read them after
the speed fold (private/generated/s5/speed/fold/determinism-gate-after-fold.txt), with
State localization's from the mirror tree's fold of the same pass output
(private/generated/s5/x87land/IMPLEMENT.md) and the fast path's from the mirror tree's fold
of the gate's own panel (private/generated/s5/next/fast/determinism/prediction.json), and the
fix-class counters are enough for every fix class to have a member, so the sensitivity checks
stay quiet. `SPEED_COUNTERS` is held to speed_rewrite.PASSES, so a pass added there without a
counter here fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_emitter_determinism as G  # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[3] / "tools" / "s2" / "corpus_run"))
import speed_rewrite as SR  # noqa: E402

# (speed_spill, speed_intrin, speed_pcround, speed_local, speed_fast) per panel member.
COUNTS = {"0x0077fc30": (128, 35, 1, 1, 1), "0x004273b0": (254, 54, 1, 1, 1),
          "0x0044b210": (14, 19, 0, 0, 0), "0x0086bf68": (90, 55, 1, 0, 0),
          "0x008290bd": (28, 49, 1, 0, 0), "0x007bdca0": (102, 32, 5, 0, 0),
          "0x0082c037": (4, 33, 0, 0, 0)}
FIXES = {"0x0077fc30": {"fld_quiet_fixed": 6}, "0x004273b0": {"fld_quiet_fixed": 11},
         "0x0086bf68": {"fpatan_rewrites": 1, "control_word_masks_fixed": 5,
                        "f80_accessor_attrs_fixed": 1, "fld_quiet_fixed": 1},
         "0x008290bd": {"control_word_masks_fixed": 5, "f80_accessor_attrs_fixed": 1,
                        "fld_quiet_fixed": 1},
         "0x007bdca0": {"fist_rewrites": 3, "f80_accessor_attrs_fixed": 1, "fld_quiet_fixed": 3},
         "0x0082c037": {"fxam_c1_stores_fixed": 2, "control_word_masks_fixed": 1}}
PASSES = ("spill", "intrin", "pcround", "local", "fast")
MOVED = ("If the pipeline was changed deliberately, re-run with --refresh and say so in the "
         "commit; otherwise this is a real regression.")


def digests(counts: dict) -> dict:
    """What run_pipeline returns, with these speed counts and digests that ignore them."""
    out = {}
    for address, (spill, intrin, pcround, local, fast) in counts.items():
        counters = {k: 0 for k in G.COUNTERS}
        counters.update(FIXES.get(address, {}))
        out[address] = {"ll_sha256": "l" + address, "pc24_sha256": "p" + address,
                        "counters": counters, "unfixed_pc24_sha256": "u" + address,
                        "fold_sha256": "f" + address, "object_sha256": "o" + address,
                        "fold_counters": {"stub_defines_removed": 0, "speed_spill": spill,
                                          "speed_intrin": intrin, "speed_pcround": pcround,
                                          "speed_local": local, "speed_local_kept": 1 - local,
                                          "speed_local_refused": 0, "speed_fast": fast,
                                          "speed_fast_kept": 0}}
    return out



def test_every_speed_pass_has_a_counter_the_gate_pins():
    """One counter per pass of tools/s2/corpus_run/speed_rewrite.py, in its order: a sixth
    pass cannot be added without the gate pinning what it rewrites."""
    assert PASSES == SR.PASSES
    assert G.SPEED_COUNTERS == tuple(f"speed_{name}" for name in SR.PASSES)


@pytest.fixture
def gate(tmp_path, monkeypatch, capsys):
    """`gate(counts, *args)` runs the gate's main() under tmp_path with the pipeline giving
    `counts`, and returns its exit status and every line it printed to stderr, stripped."""
    out = tmp_path / "private" / "generated" / "determinism"
    out.mkdir(parents=True)
    for va in G.PANEL:
        (out / f"sub_{va:08X}.pc24.stats.json").write_text("{}")
    monkeypatch.setattr(G, "project_root", lambda explicit=None: tmp_path)
    monkeypatch.setattr(G, "pipeline_digests", lambda root: {k: "x" for k in G.PIPELINE_FILES})
    monkeypatch.setattr(G, "class_off_digest",
                        lambda root, out, va, cls: ("p" if cls is None else "q") + f"0x{va:08x}")
    monkeypatch.setattr(G, "pass_tests", lambda root: [])
    monkeypatch.setattr(G, "compiler_version", lambda root: "0.15.2")

    def run(counts: dict, *args: str) -> tuple[int, list[str]]:
        monkeypatch.setattr(G, "run_pipeline", lambda root, panel, out: digests(counts))
        monkeypatch.setattr(sys, "argv", ["check_emitter_determinism.py", *args])
        capsys.readouterr()
        rc = G.main()
        return rc, [line.strip() for line in capsys.readouterr().err.splitlines()]
    run.receipt = tmp_path / G.RECEIPT
    assert run(COUNTS, "--refresh") == (0, [])
    assert run(COUNTS) == (0, [])
    return run


def idle(counts: dict, k: int) -> dict:
    """`counts` with pass `k` (an index into PASSES) at 0 on every member."""
    return {a: tuple(0 if j == k else n for j, n in enumerate(c)) for a, c in counts.items()}


def test_a_speed_pass_idle_on_every_member_fails_and_is_not_pinned(gate):
    before = gate.receipt.read_bytes()
    assert gate(idle(COUNTS, 2)) == (1, [
        "emitter determinism FAIL: the pipeline's output moved:",
        "no panel member exercises the speed pass(es): speed_pcround",
        "0x004273b0 speed_pcround: 0 != pinned 1",
        "0x0077fc30 speed_pcround: 0 != pinned 1",
        "0x007bdca0 speed_pcround: 0 != pinned 5",
        "0x008290bd speed_pcround: 0 != pinned 1",
        "0x0086bf68 speed_pcround: 0 != pinned 1",
        MOVED])
    assert gate(idle(COUNTS, 2), "--refresh") == (1, [
        "emitter determinism FAIL: not pinning a pass that fails its own tests or a panel "
        f"blind to a fix class; {G.RECEIPT} is left as it was:",
        "no panel member exercises the speed pass(es): speed_pcround"])
    assert gate.receipt.read_bytes() == before


@pytest.mark.parametrize("k", range(len(PASSES)))
def test_each_speed_pass_idle_everywhere_is_refused(gate, k):
    rc, lines = gate(idle(COUNTS, k))
    assert rc == 1
    assert f"no panel member exercises the speed pass(es): speed_{PASSES[k]}" in lines
    assert gate(idle(COUNTS, k), "--refresh")[0] == 1


@pytest.mark.parametrize("k, pinned", [(0, 128), (1, 35), (2, 1), (3, 1), (4, 1)])
def test_a_speed_pass_idle_on_one_member_is_drift_a_refresh_pins(gate, k, pinned):
    one = dict(COUNTS, **{"0x0077fc30": tuple(0 if j == k else n
                                              for j, n in enumerate(COUNTS["0x0077fc30"]))})
    assert gate(one) == (1, ["emitter determinism FAIL: the pipeline's output moved:",
                             f"0x0077fc30 speed_{PASSES[k]}: 0 != pinned {pinned}", MOVED])
    assert gate(one, "--refresh") == (0, [])
    pinned_now = json.loads(gate.receipt.read_text())["digests"]["0x0077fc30"]["fold_counters"]
    assert pinned_now[f"speed_{PASSES[k]}"] == 0
    assert gate(one) == (0, [])
