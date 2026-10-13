#!/usr/bin/env python3
"""Behaviour tests for M8's input: tools/s7/target_receipt.py and tools/s7/targets.py.

Both are measuring instruments (AGENTS.md rule 5). The receipt tool derives each clause's
verdict from the evidence; the generator recomputes each receipt with the receipt tool and
refuses one it cannot hold to the record, the tree and its recomputation. Each rule is run
on crafted evidence in a scratch tree, met and then with each part taken away, and every
negative case asserts the verdict as well as the reason. The checkers the reference
clauses call (the replay regression gate's readings and its run, G5's host predicate) are
replaced by stand-ins whose answers the tests set, since their own tests hold them; the
other targets' readings are derived by the regression gate's real functions. The last
group runs the instruments over the live record and the live receipts.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))

import check_replay_regression as R  # noqa: E402
import check_s5_exit as S5  # noqa: E402
import check_s7_exit as X  # noqa: E402
import metrics  # noqa: E402
import tick_replay as T  # noqa: E402  (tools/s4, on the path check_replay_regression sets)


def load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


TR = load("tools/s7/target_receipt.py", "s7_target_receipt")
TG = load("tools/s7/targets.py", "s7_targets")
TARGETS = ("x86_64-linux", "x86_64-windows", "arm64-linux")
UNPACKED = "c9" * 32
BINARY = "d4" * 32
WRITER = "tools/s7/writer.py"
RESIDUE = "host-timing-v1"
MAP = [0x401000 + 0x10 * i for i in range(20)]
ARMS = [0x500000 + 0x10 * i for i in range(10)]
EXTRA = [0x600000 + 0x10 * i for i in range(5)]
RESULT = {"native_ticks": 1200, "reported": None, "class": None, "judged_ticks": 1200,
          "mask": {"words": 700}, "residues": {"seen": [RESIDUE],
                                               "unclassed": {"tick": 4, "class": "bug"}}}
BUILT = {"boot": "b1" * 32, "metalharbor": "b2" * 32, "host": "b3" * 32}


def write(root: Path, rel: str, doc: object) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=1), encoding="utf-8")
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registry_entry(name: str) -> dict:
    return {"name": name, "stage": {"name": name}} if name == "metalharbor" else {"name": name}


@pytest.fixture
def root(tmp_path: Path, monkeypatch) -> Path:
    """The real record, a registry of two replays (metalharbor a stage replay), the real
    residue record they name, a writer tool, and the identity rule held."""
    (tmp_path / "Documentation").mkdir()
    shutil.copyfile(ROOT / TR.GREEN_MEANS, tmp_path / TR.GREEN_MEANS)
    write(tmp_path, TR.REGISTRY, {"replays": [registry_entry("boot"),
                                              registry_entry("metalharbor")]})
    rel = f"{TR.RESIDUES}/{RESIDUE}.json"
    write(tmp_path, rel, (ROOT / rel).read_text())
    write(tmp_path, WRITER, "# writer\n")
    monkeypatch.setattr(T, "identity_problems", lambda receipt, registry, root=None: [])
    return tmp_path


def run(root: Path, target: str, name: str) -> dict:
    return TR.clause(root, TR.green_means(root), target, name)


def false_because(entry: dict, reason: str) -> None:
    assert entry["verdict"] is False and reason in entry["why"], entry["why"]
    assert entry["facts"] == {}


# ------------------------------------------------------------------ the record


def test_the_record_lists_the_users_clauses_for_each_target() -> None:
    """The user's answers of 2026-10-11: Windows needs the user's native run beside the Wine
    runs; arm64 runs under qemu and has no host clause; every target builds and replays; only
    arm64's build applies target passes."""
    means = TR.green_means(ROOT)
    assert means.clauses == {"x86_64-linux": ["build", "replays", "host"],
                             "x86_64-windows": ["build", "replays", "host", "native_run"],
                             "arm64-linux": ["build", "replays"]}
    assert means.runner == {"x86_64-linux": "native", "x86_64-windows": "wine",
                            "arm64-linux": "qemu"}
    assert means.triple == {"x86_64-linux": "x86_64-linux-musl",
                            "x86_64-windows": "x86_64-windows-gnu",
                            "arm64-linux": "aarch64-linux-musl"}
    assert means.passes == {"x86_64-linux": [], "x86_64-windows": [],
                            "arm64-linux": ["tools/s7/f80_legalize.py", "tools/s7/f80_shims.c"]}
    assert means.sha256 == sha(ROOT / TR.GREEN_MEANS)


def test_the_three_instruments_read_the_record_alike() -> None:
    """The receipt tool, the generator and G7's predicate each parse the record on their own;
    on the live record they read the same clauses."""
    text = (ROOT / TR.GREEN_MEANS).read_text()
    assert TG.listed_clauses(text) == TR.green_means(ROOT).clauses == X.green_clauses(ROOT)


def test_the_instruments_count_the_same_three_targets() -> None:
    assert TR.TARGETS == TG.TARGETS == X.TARGETS == tuple(metrics.TARGETS) == TARGETS


def test_the_record_names_every_evidence_schema_and_the_arm64_receipt_is_g7_s() -> None:
    """The evidence schemas the receipt tool reads are the ones the record defines, and the
    arm64 replays evidence is the labels receipt G7's arm64 predicate reads: one file."""
    text = (ROOT / TR.GREEN_MEANS).read_text()
    for schema in (*TR.EVIDENCE_SCHEMAS.values(), TR.ARM64_REPLAYS[1]):
        assert f"`{schema}`" in text, schema
    assert TR.ARM64_REPLAYS == (X.ARM64_RECEIPT, X.ARM64_SCHEMA)
    assert TR.evidence_path("arm64-linux", "replays") == TR.ARM64_REPLAYS
    assert TR.evidence_path("x86_64-windows", "native_run") == (X.INSTALLER_RECEIPT,
                                                                X.INSTALLER_SCHEMA)


@pytest.mark.parametrize(("old", "new", "reason"), [
    ("| `arm64-linux` | `replays` |", "| `arm64-linux` | `labels` |", "have no rule here"),
    ("| `arm64-linux` | `build` |", "| `arm64-linux` | `replays` |", "lists replays twice"),
    ("| `x86_64-linux` | `native` |", "| `x86_64-linux` | `rosetta` |", "no runner of"),
    ("| `arm64-linux` | `qemu` |", "| arm64-linux | `qemu` |", "is not one `code` span"),
    ("`x86_64-linux-musl` | none |", "`x86_64-linux-musl` | `tools/a.py` |",
     "the reference x86_64-linux has target passes"),
    ("`aarch64-linux-musl` | `tools/s7/f80_legalize.py`,", "`aarch64-linux-musl` | tools/s7/f80_legalize.py,",
     "is not one `code` span"),
    ("| `x86_64-windows` | `wine` |", "| `arm64-android` | `wine` |", "no runner of"),
])
def test_the_record_is_held_to_its_form(root: Path, old: str, new: str, reason: str) -> None:
    path = root / TR.GREEN_MEANS
    text = path.read_text()
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new))
    with pytest.raises(ValueError, match=reason):
        TR.green_means(root)


def test_a_record_with_a_target_that_is_not_m8_s_is_refused(root: Path) -> None:
    path = root / TR.GREEN_MEANS
    path.write_text(path.read_text().replace(
        "| `arm64-linux` | `qemu` | `aarch64-linux-musl` |",
        "| `arm64-linux` | `qemu` | `aarch64-linux-musl` | none |\n"
        "| `arm64-android` | `qemu` | `aarch64-linux-android` |", 1))
    with pytest.raises(ValueError, match="not M8's: \\['arm64-android'\\]"):
        TR.green_means(root)


def test_a_record_without_a_target_s_rows_is_refused(root: Path) -> None:
    path = root / TR.GREEN_MEANS
    path.write_text("\n".join(line for line in path.read_text().splitlines()
                              if not line.startswith("| `arm64-linux` | `build`")
                              and not line.startswith("| `arm64-linux` | `replays`")))
    with pytest.raises(ValueError, match="lists no clause for arm64-linux"):
        TR.green_means(root)


# ------------------------------------------------------------------ the reference's build


def reach(**generated) -> dict:
    fold = {"tools/spike/pc24pass/pc24pass.py": "e1" * 32,
            "tools/s2/corpus_run/internalize.py": "e4" * 32,
            "tools/s3/whole_image/compile_all.py:one":
                "zig cc -target x86_64-linux-musl -O2 -w -c {ir} -o {object}",
            "tools/s4/lift_extra.py:fold_and_compile":
                "zig cc -target x86_64-linux-musl -O2 -w -c {ir} -o {object}"}
    body = {"fold_code": fold, "lifted_extras_sha256": "e2" * 32, "mb_edges_c_sha256": "e3" * 32,
            "whole_image_module_count": 30}
    body.update(generated)
    return {"commit": "f" * 40, "manifest": {"tag": "next+allarms", "exe_sha256": UNPACKED,
                                             "generated": body}}


def built(pipeline: dict | None = None, **rows) -> dict:
    same = {"map": {"built": 20, "shipped": 20, "differ": [], "differ_count": 0, "ok": True,
                    "missing_from_build": [], "extra_in_build": []},
            "arms": {"built": 10, "shipped": 10, "differ": [], "differ_count": 0, "ok": True},
            "extras": {"built": 5, "shipped": 5, "differ": [], "differ_count": 0, "ok": True,
                       "failed": []}}
    for name, change in rows.items():
        same[name].update(change)
    return {"commit": "e" * 40, "objects_identical_to_the_build": same, "inputs": {
        "pipeline": pipeline if pipeline is not None else {
            "tools/spike/pc24pass/pc24pass.py": "e1" * 32,
            "tools/s2/corpus_run/internalize.py": "e4" * 32,
            TR.EXTRAS: "e2" * 32, "tools/s4/link_s4.py": "e5" * 32}}}


def staged(root: Path, whole: list[int] | None = None, extras: list[int] | None = None) -> None:
    for va in whole if whole is not None else MAP + ARMS:
        write(root, f"{TR.STAGED}/ir_{va:08X}.o", "")
    write(root, f"{TR.STAGED}/link.log", "not an object\n")
    write(root, TR.EXTRAS_LINKED, {"objects": [
        f"/elsewhere/private/generated/s4/lift/obj/ir_{va:08X}.o"
        for va in (extras if extras is not None else EXTRA)]})


def reference_build(root: Path, reach_doc: dict | None = None, built_doc: dict | None = None,
                    extras: dict | None = None, tick_commit: str = "f" * 40,
                    link: bool = True) -> None:
    write(root, TR.REACH, reach_doc or reach())
    write(root, TR.BUILD_TIME, built_doc or built())
    write(root, TR.EXTRAS, {"counts": extras or {"rows": 5, "lifted": 5, "compiled": 5}})
    write(root, TR.TICK_REPLAY, {"commit": tick_commit, "replays": [
        {"name": "boot", "lifted": {"binary_sha256": BINARY}}]})
    write(root, TR.PINS, {"executables": {TR.UNPACKED: {"sha256": UNPACKED}}})
    if link:
        staged(root)


def test_the_reference_build_reads_true_from_its_manifest(root: Path) -> None:
    reference_build(root)
    entry = run(root, "x86_64-linux", "build")
    assert entry["verdict"] is True, entry["why"]
    assert entry["why"] == ("x86_64-linux-musl: map 20, arms 10, extras 5 compiled, none failed, "
                            "linked at ffffffffffff")
    assert [e["path"] for e in entry["evidence"]] == [TR.REACH, TR.BUILD_TIME, TR.EXTRAS,
                                                      TR.TICK_REPLAY, TR.EXTRAS_LINKED]
    assert entry["evidence"][0]["sha256"] == sha(root / TR.REACH)
    facts = entry["facts"]
    assert facts["populations"] == {"map": {"modules": 20, "compiled": 20, "failed": 0},
                                    "arms": {"modules": 10, "compiled": 10, "failed": 0},
                                    "extras": {"modules": 5, "compiled": 5, "failed": 0}}
    assert facts["lifted_inputs"]["fold_code"] == {
        "tools/s2/corpus_run/internalize.py": "e4" * 32,
        "tools/spike/pc24pass/pc24pass.py": "e1" * 32}
    assert facts["lifted_inputs_digest"] == TR.digest(facts["lifted_inputs"])
    assert facts["fold_code_in_both"] == ["tools/s2/corpus_run/internalize.py",
                                          "tools/spike/pc24pass/pc24pass.py"]
    assert facts["modules"] == {"whole_image": TR.names(MAP + ARMS), "extras": TR.names(EXTRA)}
    assert facts["modules"]["whole_image"]["count"] == 30
    assert facts["pinned_unpacked_sha256"] == UNPACKED
    assert facts["binary_sha256"] == {"boot": BINARY}


def test_an_unrelated_pin_does_not_change_the_reference_build(root: Path) -> None:
    """tools/pins.json is read for one pin, kept as a fact: a pin added for another file (the
    qemu pin the user's answers ask for) leaves the clause as it was; the pin it reads moving
    changes it."""
    reference_build(root)
    before = run(root, "x86_64-linux", "build")
    write(root, TR.PINS, {"executables": {TR.UNPACKED: {"sha256": UNPACKED}},
                          "qemu": {"sha256": "00" * 32}})
    assert run(root, "x86_64-linux", "build") == before
    write(root, TR.PINS, {"executables": {TR.UNPACKED: {"sha256": "00" * 32}}})
    false_because(run(root, "x86_64-linux", "build"), "not the pinned sonic2app-unpacked.exe")


@pytest.mark.parametrize(("arrange", "reason"), [
    (lambda r: reference_build(r, reach_doc=dict(reach(), manifest=dict(
        reach()["manifest"], exe_sha256="00" * 32))), "not the pinned sonic2app-unpacked.exe"),
    (lambda r: (reference_build(r), write(r, TR.PINS, {"executables": {}})),
     "pins no sha256 for sonic2app-unpacked.exe"),
    (lambda r: reference_build(r, reach_doc=reach(fold_code={
        "a:one": "zig cc -target x86_64-windows-gnu -O2 -c {ir}"})), "-target x86_64-linux-musl"),
    (lambda r: reference_build(r, reach_doc=reach(fold_code={"a.py": "e1" * 32})),
     "-target x86_64-linux-musl"),
    (lambda r: reference_build(r, built_doc=built(pipeline={
        "tools/spike/pc24pass/pc24pass.py": "00" * 32, TR.EXTRAS: "e2" * 32})),
     "rebuilt with other fold code than"),
    (lambda r: reference_build(r, built_doc=built(pipeline={
        "tools/spike/pc24pass/pc24pass.py": "e1" * 32, TR.EXTRAS: "00" * 32})),
     "Documentation/lifted-extras.json"),
    (lambda r: reference_build(r, built_doc=built(pipeline={"tools/other.py": "e1" * 32})),
     "no fold-code file in common"),
    (lambda r: reference_build(r, built_doc=built(arms={"differ_count": 1})),
     "the arms population was not rebuilt whole and identical"),
    (lambda r: reference_build(r, built_doc=built(extras={"failed": ["0x401000"]})),
     "the extras population was not rebuilt"),
    (lambda r: reference_build(r, built_doc=built(map={"built": 19})), "the map population"),
    (lambda r: reference_build(r, built_doc=built(arms={"built": 0, "shipped": 0})),
     "the arms population was not rebuilt"),
    (lambda r: reference_build(r, built_doc=built(map={"missing_from_build": ["ir_1.o"]})),
     "the map population was not rebuilt"),
    (lambda r: reference_build(r, built_doc=built(map={"extra_in_build": ["ir_1.o"]})),
     "the map population was not rebuilt"),
    (lambda r: reference_build(r, reach_doc=reach(whole_image_module_count=29)),
     "links 29 whole-image objects"),
    (lambda r: reference_build(r, extras={"rows": 5, "lifted": 5, "compiled": 4}),
     "not every registered extra lifted and compiled"),
    (lambda r: (reference_build(r, link=False), staged(r, whole=MAP + ARMS[:-1])),
     "the link holds 29 whole-image objects and 5 extras, not the populations' 30 and 5"),
    (lambda r: (reference_build(r, link=False), staged(r, extras=EXTRA[:-1])),
     "the link holds 30 whole-image objects and 4 extras"),
    (lambda r: reference_build(r, link=False), "private/generated/s3/whole_image/build/"
                                               "next+allarms is absent"),
    (lambda r: (reference_build(r), write(r, TR.EXTRAS_LINKED, {"objects": ["a/b.o"]})),
     "not an ir_<VA>.o object"),
    (lambda r: reference_build(r, tick_commit="a" * 40), "did not run the build"),
])
def test_the_reference_build_reads_false_on_each_gap(root: Path, arrange, reason: str) -> None:
    arrange(root)
    false_because(run(root, "x86_64-linux", "build"), reason)


# ------------------------------------------------------------------ the reference's replays


def derived(root: Path, name: str, result: dict, goal: bool | None = None) -> dict:
    """A replay's reading as the receipt tool derives it from a tick-replay result."""
    return TR.reading(R, T, root, name, registry_entry(name),
                      {"tick_result": result, "goal_complete": goal}, [])[0]


def readings_of(root: Path) -> dict:
    return {"boot": derived(root, "boot", RESULT),
            "metalharbor": derived(root, "metalharbor", RESULT, goal=True)}


def reference_replays(root: Path, monkeypatch, readings: dict | None = None,
                      stale: list | None = None, gate: int = 0,
                      baseline: dict | None = None, m5: tuple = ("boot", "metalharbor")) -> None:
    now = readings if readings is not None else readings_of(root)
    write(root, TR.TICK_REPLAY, {"commit": "f" * 40,
                                 "replays": [{"name": "boot", "lifted": {"binary_sha256": BINARY}}],
                                 "m5": [{"name": n, "residues": [RESIDUE]} for n in m5]})
    write(root, TR.BASELINE, {"schema": R.SCHEMA, "replays": [dict(r, name=n) for n, r in (
        baseline if baseline is not None else now).items()]})
    write(root, TR.STAGE_BOOT, {"replays": []})
    monkeypatch.setattr(R, "current_readings", lambda _root: (now, list(stale or [])))
    monkeypatch.setattr(TR, "regression_gate", lambda _root: {
        "command": "gate", "returncode": gate, "stdout_sha256": "ab" * 32,
        "said": "replay regression OK" if gate == 0 else "replay regression FAIL: 1 problem"})
    monkeypatch.setattr(TR, "runner_here", lambda: {"kind": "native", "system": "Linux",
                                                    "machine": "x86_64", "by": "test"})


def test_a_derived_reading_is_the_regression_gate_s() -> None:
    """The crafted run reads as METRICS.md renders it and meets M5 over the real residue
    record; a divergence reads as a bug and does not."""
    reading = derived(ROOT, "boot", RESULT)
    assert reading == {"m5": f"boot residues only/1200 ({RESIDUE}) from 4", "ticks": 1200,
                       "first_divergent_tick": None, "field": None, "class": "residue",
                       "judged_ticks": 1200, "trace_judged_ticks": None, "compared_words": 700,
                       "m5_met": True, "goal_complete": None}
    bug = derived(ROOT, "boot", dict(RESULT, reported={"tick": 9, "field": "g_x"},
                                     **{"class": "bug"}))
    assert bug["m5"].startswith("boot 9/1200 (bug: g_x)") and bug["m5_met"] is False


def test_the_reference_replays_read_true_when_none_reads_worse(root: Path, monkeypatch) -> None:
    reference_replays(root, monkeypatch)
    entry = run(root, "x86_64-linux", "replays")
    assert entry["verdict"] is True, entry["why"]
    assert entry["why"].endswith(", none worse than the baseline")
    assert entry["facts"]["residues"] == {"boot": [RESIDUE], "metalharbor": [RESIDUE]}
    assert entry["facts"]["regression_gate"]["returncode"] == 0
    assert [e["path"] for e in entry["evidence"]] == [
        TR.REGISTRY, TR.TICK_REPLAY, TR.BASELINE, TR.STAGE_BOOT, f"{TR.RESIDUES}/{RESIDUE}.json"]


def test_the_reference_replays_read_false_on_a_reading_worse_than_the_baseline(
        root: Path, monkeypatch) -> None:
    now = readings_of(root)
    worse = {"boot": dict(now["boot"], compared_words=600), "metalharbor": now["metalharbor"]}
    reference_replays(root, monkeypatch, readings=worse, baseline=now)
    false_because(run(root, "x86_64-linux", "replays"),
                  "worse than the baseline: boot: compared 600 words, was 700")


@pytest.mark.parametrize(("arrange", "reason"), [
    (lambda r, mp: reference_replays(r, mp, stale=["boot: its recorded inputs are not the tree's"]),
     f"{TR.TICK_REPLAY} is not the tree's: boot: its recorded inputs are not the tree's"),
    (lambda r, mp: reference_replays(r, mp, readings={"boot": readings_of(r)["boot"]}),
     "metalharbor: not run"),
    (lambda r, mp: reference_replays(r, mp, m5=("boot",)), "metalharbor: not run"),
    (lambda r, mp: reference_replays(r, mp, gate=1),
     f"{TR.REGRESSION} exits 1: replay regression FAIL: 1 problem"),
    (lambda r, mp: (reference_replays(r, mp), (r / f"{TR.RESIDUES}/{RESIDUE}.json").unlink()),
     f"{RESIDUE}.json is absent"),
])
def test_the_reference_replays_read_false_when_stale_unrun_or_the_gate_fails(
        root: Path, monkeypatch, arrange, reason: str) -> None:
    arrange(root, monkeypatch)
    false_because(run(root, "x86_64-linux", "replays"), reason)


def test_the_reference_replays_need_this_machine_to_be_x86_64_linux(root: Path,
                                                                     monkeypatch) -> None:
    reference_replays(root, monkeypatch)
    monkeypatch.setattr(TR, "runner_here", lambda: {"kind": "native", "system": "Linux",
                                                    "machine": "aarch64", "by": "test"})
    false_because(run(root, "x86_64-linux", "replays"),
                  "this run is Linux aarch64, not x86_64-linux natively")


# ------------------------------------------------------------------ the reference's host


HEADER = "\t".join(S5.TIMES_HEADER)
ROW = "0\t1\t16666\t1\t2\t99\t0x00000000000000aa"


def reference_host(root: Path, monkeypatch, met: bool = True, replay: str = "metalharbor") -> None:
    frames = write(root, "private/generated/s5/frames.tsv", f"{HEADER}\n{ROW}\n")
    write(root, S5.SELECTION, "# selection\n")
    write(root, TR.HOST_COMPARE, {"replay": replay, "stream_sha256": "5e" * 32,
                                  "inputs": {"pipeline": {"x.py": "00" * 32}},
                                  "hosts": [{"host": "dxvk-native", "frames": {
                                      "path": "private/generated/s5/frames.tsv",
                                      "sha256": sha(frames)}}]})
    monkeypatch.setattr(S5, "identical", lambda _root: S5.Result(
        met, "metalharbor: frames equal" if met else "metalharbor frame 9: bgrx_hash differs"))


def test_the_reference_host_is_g5_s_predicate_over_the_registered_stage(root: Path,
                                                                         monkeypatch) -> None:
    reference_host(root, monkeypatch)
    entry = run(root, "x86_64-linux", "host")
    assert entry["verdict"] is True and entry["why"] == (
        "check_s5_exit.identical: metalharbor: frames equal")
    assert [e["path"] for e in entry["evidence"]] == [
        TR.REGISTRY, TR.HOST_COMPARE, "private/generated/s5/frames.tsv", S5.SELECTION]


@pytest.mark.parametrize(("arrange", "reason"), [
    (lambda r, mp: reference_host(r, mp, met=False),
     "check_s5_exit.identical: metalharbor frame 9: bgrx_hash differs"),
    (lambda r, mp: reference_host(r, mp, replay="boot"),
     "compares 'boot', not the registered metalharbor"),
    (lambda r, mp: (reference_host(r, mp), write(r, TR.REGISTRY, {"replays": [{"name": "boot"}]})),
     "compares 'metalharbor', not the registered metalharbor"),
])
def test_the_reference_host_reads_false_on_each_gap(root: Path, monkeypatch, arrange,
                                                    reason: str) -> None:
    arrange(root, monkeypatch)
    false_because(run(root, "x86_64-linux", "host"), reason)


# ------------------------------------------------------------------ the other targets


def reference_receipt(root: Path, monkeypatch) -> dict:
    """The reference's receipt, written by the tool over crafted evidence, green."""
    reference_build(root)
    reference_replays(root, monkeypatch)
    reference_host(root, monkeypatch)
    doc = TR.build_receipt(root, "x86_64-linux")
    assert doc["green"] is True, {k: v["why"] for k, v in doc["clauses"].items()}
    write(root, TR.RECEIPT.format(target="x86_64-linux"), doc)
    return doc


def at(target: str, clause: str) -> str:
    return TR.evidence_path(target, clause)[0]


def log_records(target: str) -> list[dict]:
    return [{"population": population, "va": f"0x{va:08x}",
             "ir": TR.FOLDED_IR[population].format(va=va), "ir_sha256": "ab" * 32,
             "compiled": True}
            for population, vas in (("map", MAP), ("arms", ARMS), ("extra", EXTRA))
            for va in vas]


def census(root: Path, who: str, records: list[dict] | None = None, **changes) -> dict:
    means = TR.green_means(root)
    ref = json.loads((root / TR.RECEIPT.format(target="x86_64-linux")).read_text())
    log = write(root, f"private/generated/s7/targets/{means.triple[who]}/build.jsonl",
                "".join(json.dumps(r) + "\n" for r in (records if records is not None
                                                       else log_records(who))))
    passes = {}
    for rel in means.passes[who]:
        passes[rel] = sha(write(root, rel, f"# {rel}\n"))
    roles = ["boot", "metalharbor"] + (["host"] if who == "x86_64-windows" else [])
    doc = {"schema": TR.EVIDENCE_SCHEMAS["build"], "generated_by": WRITER, "target": who,
           "triple": means.triple[who],
           "lifted_inputs": ref["clauses"]["build"]["facts"]["lifted_inputs"],
           "target_passes": passes,
           "log": {"path": str(log.relative_to(root)), "sha256": sha(log)},
           "link": {"returncode": 0, "binaries": {role: BUILT[role] for role in roles}}}
    doc.update(changes)
    write(root, at(who, "build"), doc)
    return doc


def runs(root: Path, who: str, replays: dict | None = None, **changes) -> dict:
    schema = TR.evidence_path(who, "replays")[1]
    per = {"boot": {"binary_sha256": BUILT["boot"], "tick_result": RESULT, "goal_complete": None},
           "metalharbor": {"binary_sha256": BUILT["metalharbor"], "tick_result": RESULT,
                           "goal_complete": True}}
    if who == "arm64-linux":
        for run_ in per.values():
            run_.update(result="residues only", residues=[RESIDUE], first_divergent_tick=4,
                        transcendental_sites=3, magnitude="host timing words only")
    doc = {"schema": schema, "generated_by": WRITER, "target": who,
           "runner": {"kind": TR.green_means(root).runner[who], "headless": True},
           "registry_sha256": sha(root / TR.REGISTRY),
           "replays": replays if replays is not None else per}
    doc.update(changes)
    write(root, at(who, "replays"), doc)
    return doc


def arm64(root: Path, monkeypatch) -> None:
    reference_receipt(root, monkeypatch)
    census(root, "arm64-linux")
    runs(root, "arm64-linux")


def test_arm64_reads_green_on_its_census_and_runs_against_the_reference(root: Path,
                                                                        monkeypatch) -> None:
    arm64(root, monkeypatch)
    doc = TR.build_receipt(root, "arm64-linux")
    assert doc["green"] is True, {k: v["why"] for k, v in doc["clauses"].items()}
    assert sorted(doc["clauses"]) == ["build", "replays"]
    assert doc["runner"] == "qemu" and doc["triple"] == "aarch64-linux-musl"
    build = doc["clauses"]["build"]
    assert [e["path"] for e in build["evidence"]] == [
        at("arm64-linux", "build"), "tools/s7/f80_legalize.py", "tools/s7/f80_shims.c",
        "private/generated/s7/targets/aarch64-linux-musl/build.jsonl", TR.REGISTRY]
    assert build["facts"]["populations"] == {"map": {"modules": 20, "compiled": 20, "failed": 0},
                                             "arms": {"modules": 10, "compiled": 10, "failed": 0},
                                             "extras": {"modules": 5, "compiled": 5, "failed": 0}}
    assert build["facts"]["binaries"] == {"boot": BUILT["boot"],
                                          "metalharbor": BUILT["metalharbor"]}
    assert sorted(build["facts"]["reference"]["facts_sha256"]) == [
        "build.lifted_inputs", "build.modules", "build.populations"]
    replays = doc["clauses"]["replays"]
    assert replays["facts"]["readings"] == readings_of(root)
    assert replays["facts"]["residues"] == {"boot": [RESIDUE], "metalharbor": [RESIDUE]}


def test_a_rewritten_reference_with_the_same_facts_does_not_stale_another_target(
        root: Path, monkeypatch) -> None:
    """Another target cites the reference by the digest of the facts it reads, so rewriting
    the reference receipt (a new written instant) leaves its receipt as it was; the facts it
    reads changing does not."""
    arm64(root, monkeypatch)
    before = TR.build_receipt(root, "arm64-linux")
    rel = TR.RECEIPT.format(target="x86_64-linux")
    held = json.loads((root / rel).read_text())
    write(root, rel, dict(held, written="2030-01-01T00:00:00+00:00", commit="0" * 40))
    assert TR.moved(before, TR.build_receipt(root, "arm64-linux")) == []
    held["clauses"]["build"]["facts"]["lifted_inputs"]["tag"] = "other"
    write(root, rel, held)
    after = TR.build_receipt(root, "arm64-linux")
    assert after["clauses"]["build"]["verdict"] is False
    assert "the lifted inputs are not x86_64-linux's (tag)" in after["clauses"]["build"]["why"]


def drop_one(records: list[dict], population: str) -> list[dict]:
    first = next(i for i, r in enumerate(records) if r["population"] == population)
    return records[:first] + records[first + 1:]


def one(records: list[dict], index: int, **change) -> list[dict]:
    return [dict(r, **change) if i == index else r for i, r in enumerate(records)]


@pytest.mark.parametrize(("arrange", "reason"), [
    (lambda r: census(r, "arm64-linux", triple="aarch64-linux-gnu"),
     "is a build for 'aarch64-linux-gnu', not aarch64-linux-musl"),
    (lambda r: census(r, "arm64-linux", target="x86_64-windows"),
     "is 'x86_64-windows''s evidence, not arm64-linux's"),
    (lambda r: census(r, "arm64-linux", generated_by="tools/none.py"), "names no writer in the tree"),
    (lambda r: census(r, "arm64-linux", schema="sa2-s7-build-census/v0"), "has schema"),
    (lambda r: census(r, "arm64-linux", lifted_inputs={"tag": "other"}),
     "the lifted inputs are not x86_64-linux's"),
    (lambda r: census(r, "arm64-linux", target_passes={}), "names target passes []"),
    (lambda r: census(r, "arm64-linux", target_passes={
        "tools/s7/f80_legalize.py": "00" * 32, "tools/s7/f80_shims.c": "00" * 32}),
     "the target pass tools/s7/f80_legalize.py is not the tree's"),
    (lambda r: (census(r, "arm64-linux"), write(r, "private/generated/s7/targets/"
                                                   "aarch64-linux-musl/build.jsonl", "\n")),
     "build.jsonl is not the file"),
    (lambda r: census(r, "arm64-linux", records=one(log_records("arm64-linux"), 0,
                                                    compiled=False)),
     "map compiled 19 of 20 with 1 failed"),
    (lambda r: census(r, "arm64-linux", records=drop_one(log_records("arm64-linux"), "arms")),
     "arms compiled 9 of 9 with 0 failed; x86_64-linux has 10"),
    (lambda r: census(r, "arm64-linux", records=one(log_records("arm64-linux"), 0,
                                                    va="0x00401001",
                                                    ir=TR.FOLDED_IR["map"].format(va=0x401001))),
     "the modules compiled are not the x86_64-linux link's"),
    (lambda r: census(r, "arm64-linux", records=one(log_records("arm64-linux"), 0,
                                                    ir="private/other/sub_00401000.ll")),
     "is not the reference's folded IR for that module"),
    (lambda r: census(r, "arm64-linux", records=one(log_records("arm64-linux"), 0,
                                                    population=["map"])),
     "line 1: not a module record"),
    (lambda r: census(r, "arm64-linux", link={"returncode": 1, "binaries": BUILT}),
     "the link did not return zero"),
    (lambda r: census(r, "arm64-linux", link={"returncode": 0, "binaries": {"boot": BINARY}}),
     "with a binary for each of ['boot', 'metalharbor']"),
    (lambda r: (r / at("arm64-linux", "build")).unlink(),
     f"no build evidence for arm64-linux: {at('arm64-linux', 'build')} is absent"),
])
def test_arm64_s_build_reads_false_on_each_gap(root: Path, monkeypatch, arrange,
                                               reason: str) -> None:
    arm64(root, monkeypatch)
    arrange(root)
    false_because(run(root, "arm64-linux", "build"), reason)


def tick(**change) -> dict:
    return dict(RESULT, **change)


def replay(name: str, **change) -> dict:
    base = {"binary_sha256": BUILT[name], "tick_result": RESULT,
            "goal_complete": True if name == "metalharbor" else None,
            "result": "residues only", "residues": [RESIDUE], "first_divergent_tick": 4,
            "transcendental_sites": 3, "magnitude": "host timing words only"}
    base.update(change)
    return base


@pytest.mark.parametrize(("arrange", "reason"), [
    (lambda r, mp: runs(r, "arm64-linux", runner={"kind": "native", "headless": True}),
     "is not arm64-linux's runs under qemu"),
    (lambda r, mp: runs(r, "arm64-linux", runner={"kind": "qemu"}), "not recorded headless"),
    (lambda r, mp: runs(r, "arm64-linux", registry_sha256="00" * 32), "under another"),
    (lambda r, mp: runs(r, "arm64-linux", target="x86_64-linux"), "evidence, not arm64-linux's"),
    (lambda r, mp: runs(r, "arm64-linux", schema=TR.EVIDENCE_SCHEMAS["replays"]),
     "not 'sa2-s7-arm64-replays/v1'"),
    (lambda r, mp: runs(r, "arm64-linux", replays={"boot": replay("boot")}),
     "metalharbor: no run on arm64-linux"),
    (lambda r, mp: runs(r, "arm64-linux", replays={"boot": replay("boot", binary_sha256=BINARY),
                                                   "metalharbor": replay("metalharbor")}),
     "boot on arm64-linux ran binary"),
    (lambda r, mp: runs(r, "arm64-linux", replays={"boot": replay("boot", tick_result=None),
                                                   "metalharbor": replay("metalharbor")}),
     "boot: the run records no tick_result"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot"), "metalharbor": replay("metalharbor", goal_complete=None)}),
     "metalharbor: a stage replay's run records no goal_complete verdict"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot"), "metalharbor": replay("metalharbor", goal_complete=False)}),
     "metalharbor: its lifted run no longer reaches the goal"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", tick_result=tick(judged_ticks=1100)),
        "metalharbor": replay("metalharbor")}), "boot: judged 1100 ticks, was 1200"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", tick_result=tick(reported={"tick": 9, "field": "g_x"},
                                                **{"class": "bug"})),
        "metalharbor": replay("metalharbor")}), "boot: no longer meets M5"),
    (lambda r, mp: (mp.setattr(T, "identity_problems", lambda receipt, registry, root=None:
                               ["boot: compared without the identity rule"]),
                    runs(r, "arm64-linux")), "boot: no longer meets M5"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", tick_result=tick(residues={"seen": ["arm64-libm-v1"]}),
                       residues=["arm64-libm-v1"]),
        "metalharbor": replay("metalharbor")}), "arm64-libm-v1.json is absent"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", residues=[]), "metalharbor": replay("metalharbor")}),
     "boot is labelled with residues []"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", result="no divergence"), "metalharbor": replay("metalharbor")}),
     "boot is labelled 'no divergence', and its run reads a divergence or residues"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", tick_result={k: v for k, v in RESULT.items()
                                            if k != "native_ticks"}),
        "metalharbor": replay("metalharbor")}),
     "malformed evidence: KeyError"),
    (lambda r, mp: runs(r, "arm64-linux", replays={
        "boot": replay("boot", tick_result=tick(mask={"words": "700"})),
        "metalharbor": replay("metalharbor")}), "malformed evidence: TypeError"),
])
def test_arm64_s_replays_read_false_on_each_gap(root: Path, monkeypatch, arrange,
                                                reason: str) -> None:
    arm64(root, monkeypatch)
    arrange(root, monkeypatch)
    entry = run(root, "arm64-linux", "replays")
    assert entry["verdict"] is False and reason in entry["why"], entry["why"]
    assert TR.build_receipt(root, "arm64-linux")["green"] is False


def test_a_target_without_evidence_or_a_green_reference_reads_not_green(root: Path,
                                                                         monkeypatch) -> None:
    doc = TR.build_receipt(root, "x86_64-windows")
    assert doc["green"] is False
    assert {k: v["why"] for k, v in doc["clauses"].items()} == {
        **{name: f"no {name} evidence for x86_64-windows: {at('x86_64-windows', name)} is absent"
           for name in ("build", "replays", "host")},
        "native_run": f"{X.INSTALLER_RECEIPT} is absent (the installer receipt)"}
    assert all(v["evidence"] == [] and v["verdict"] is False for v in doc["clauses"].values())
    arm64(root, monkeypatch)
    rel = TR.RECEIPT.format(target="x86_64-linux")
    held = json.loads((root / rel).read_text())
    held["clauses"]["build"]["verdict"] = False
    write(root, rel, held)
    false_because(run(root, "arm64-linux", "build"),
                  f"{rel}: the reference's build clause does not read true")
    write(root, rel, dict(held, target="arm64-linux"))
    false_because(run(root, "arm64-linux", "build"), "is not x86_64-linux's receipt written by")


def windows(root: Path, monkeypatch) -> tuple[dict, Path]:
    """The reference, and the Windows build census, runs and a host run equal to it."""
    reference_receipt(root, monkeypatch)
    census(root, "x86_64-windows")
    runs(root, "x86_64-windows")
    mine = write(root, "private/generated/s7/windows/frames.tsv", f"{HEADER}\n{ROW}\n")
    host = {"schema": TR.EVIDENCE_SCHEMAS["host"], "generated_by": WRITER,
            "target": "x86_64-windows", "replay": "metalharbor", "stream_sha256": "5e" * 32,
            "runner": {"kind": "wine"}, "binary_sha256": BUILT["host"],
            "frames": {"path": "private/generated/s7/windows/frames.tsv", "sha256": sha(mine)}}
    write(root, at("x86_64-windows", "host"), host)
    return host, mine


def test_the_windows_build_and_replays_read_green_under_wine(root: Path, monkeypatch) -> None:
    windows(root, monkeypatch)
    for name in ("build", "replays", "host"):
        entry = run(root, "x86_64-windows", name)
        assert entry["verdict"] is True, (name, entry["why"])
    assert run(root, "x86_64-windows", "build")["facts"]["binaries"] == BUILT
    runs(root, "x86_64-windows", runner={"kind": "qemu"})
    false_because(run(root, "x86_64-windows", "replays"), "is not x86_64-windows's runs under wine")


def test_the_windows_host_compares_its_frames_with_the_reference_host(root: Path,
                                                                      monkeypatch) -> None:
    host, mine = windows(root, monkeypatch)
    rel = at("x86_64-windows", "host")
    entry = run(root, "x86_64-windows", "host")
    assert entry["verdict"] is True, entry["why"]
    assert entry["why"] == "metalharbor: 1 frames equal to x86_64-linux's dxvk-native host"
    mine.write_text(f"{HEADER}\n0\t1\t16666\t1\t2\t5\t0x00000000000000bb\n")
    write(root, rel, dict(host, frames=dict(host["frames"], sha256=sha(mine))))
    false_because(run(root, "x86_64-windows", "host"),
                  "frame 0: bgrx_hash 0x00000000000000bb on x86_64-windows, 0x00000000000000aa "
                  "on x86_64-linux")
    mine.write_text(f"{HEADER}\n{ROW}\n{ROW.replace('0', '1', 1)}\n")
    write(root, rel, dict(host, frames=dict(host["frames"], sha256=sha(mine))))
    false_because(run(root, "x86_64-windows", "host"),
                  "x86_64-windows presented 2 frames, x86_64-linux's dxvk-native 1")
    write(root, rel, host)
    false_because(run(root, "x86_64-windows", "host"), "frames.tsv is not the file")


@pytest.mark.parametrize(("change", "reason"), [
    ({"stream_sha256": "00" * 32}, "replays another stream"),
    ({"runner": {"kind": "native"}}, "host run under wine"),
    ({"replay": "boot"}, "is not x86_64-windows's registered metalharbor host run"),
    ({"binary_sha256": BUILT["boot"]}, "ran binary"),
    ({"binary_sha256": None}, "ran binary None, not the host binary"),
])
def test_the_windows_host_reads_false_on_each_gap(root: Path, monkeypatch, change: dict,
                                                  reason: str) -> None:
    host, _ = windows(root, monkeypatch)
    write(root, at("x86_64-windows", "host"), dict(host, **change))
    false_because(run(root, "x86_64-windows", "host"), reason)


def test_the_windows_host_needs_metalharbor_registered(root: Path, monkeypatch) -> None:
    windows(root, monkeypatch)
    write(root, TR.REGISTRY, {"replays": [registry_entry("boot")]})
    false_because(run(root, "x86_64-windows", "host"), "registered metalharbor host run")


NATIVE_RUNNER = {"kind": "native", "os": "win32 Windows_NT", "machine": "AMD64",
                 "windows_version": "Microsoft Windows [Version 10.0.26100.4652]"}
WINE_RUNNER = {"kind": "wine", "wine": "~/proton/bin/wine", "wine_version": "wine-11.0"}


def route_entry(runner: dict, player: str = BUILT["host"], **change) -> dict:
    """One route's x86_64-windows entry as tools/s7/installer_receipt.py keeps it: the last
    run's verdict, whether it passed, and its evidence with its runner and launch record."""
    entry = {"hash_checked": True, "installed": True, "manual_steps": 0, "launched": True,
             "last_run": {"passed": True, "problems": [], "sources_current": True},
             "evidence": {"runner": runner,
                          "launch": {"record_schema": "sa2-s7-launch-run/v1",
                                     "player_sha256": player}}}
    entry.update(change)
    return entry


def installer(root: Path, routes: dict | None = None, **change) -> None:
    """The installer receipt: the three routes under Wine on both targets unless `routes`
    replaces an x86_64-windows route's entry, and installer_receipt.py in the tree."""
    write(root, TR.INSTALLER_TOOL, "# installer receipt\n")
    per = {route: {"x86_64-linux": route_entry({}),
                   "x86_64-windows": route_entry(WINE_RUNNER)} for route in TR.ROUTES}
    for route, entry in (routes or {}).items():
        per[route]["x86_64-windows"] = entry
    doc = {"schema": X.INSTALLER_SCHEMA, "generated_by": TR.INSTALLER_TOOL, "routes": per}
    doc.update(change)
    write(root, X.INSTALLER_RECEIPT, doc)


def test_the_native_run_is_pending_while_the_installer_holds_only_wine_runs(
        root: Path, monkeypatch) -> None:
    windows(root, monkeypatch)
    installer(root)
    entry = run(root, "x86_64-windows", "native_run")
    false_because(entry, "pending: the user's native Windows run")
    assert entry["why"] == TR.NATIVE_PENDING
    assert [e["path"] for e in entry["evidence"]] == [X.INSTALLER_RECEIPT]
    doc = TR.build_receipt(root, "x86_64-windows")
    assert doc["green"] is False
    assert {k: v["verdict"] for k, v in doc["clauses"].items()} == {
        "build": True, "replays": True, "host": True, "native_run": False}


@pytest.mark.parametrize(("routes", "change", "reason"), [
    ({"folder": route_entry(NATIVE_RUNNER)}, {}, None),
    ({"zip": route_entry(NATIVE_RUNNER, launched=False), "folder": route_entry(NATIVE_RUNNER)},
     {}, None),
    ({"folder": route_entry(dict(NATIVE_RUNNER, wine_version="wine-11.0"))}, {},
     "folder: the run is not native Windows without Wine"),
    ({"folder": route_entry(dict(NATIVE_RUNNER, os="linux"))}, {},
     "folder: the run is not native Windows without Wine"),
    ({"folder": route_entry(NATIVE_RUNNER, launched=False)}, {},
     "folder: not hash-checked, installed and launched"),
    ({"folder": route_entry(NATIVE_RUNNER, hash_checked=False)}, {},
     "folder: not hash-checked, installed and launched"),
    ({"folder": route_entry(NATIVE_RUNNER, last_run={"passed": False})}, {},
     "folder: not hash-checked, installed and launched"),
    ({"folder": route_entry(NATIVE_RUNNER, player=BUILT["boot"])}, {},
     "not the host binary the build census names"),
    ({"folder": route_entry(NATIVE_RUNNER, player=None)}, {},
     "the launch ran player None"),
    ({"folder": route_entry(NATIVE_RUNNER)}, {"generated_by": "tools/s7/none.py"},
     "is not tools/s7/installer_receipt.py's"),
    ({"folder": route_entry(NATIVE_RUNNER)}, {"schema": "sa2-s7-installer/v0"},
     "has schema 'sa2-s7-installer/v0'"),
])
def test_the_users_native_windows_run(root: Path, monkeypatch, routes: dict, change: dict,
                                      reason: str | None) -> None:
    windows(root, monkeypatch)
    installer(root, routes, **change)
    entry = run(root, "x86_64-windows", "native_run")
    if reason is None:
        assert entry["verdict"] is True and entry["why"] == (
            "the user's run on Windows: folder installed and launched")
        assert entry["facts"]["operator"] == "user"
        assert entry["facts"]["binary_sha256"] == BUILT["host"]
        assert [e["path"] for e in entry["evidence"]] == [X.INSTALLER_RECEIPT,
                                                          at("x86_64-windows", "build")]
        assert TR.build_receipt(root, "x86_64-windows")["green"] is True
    else:
        false_because(entry, reason)
        assert TR.build_receipt(root, "x86_64-windows")["green"] is False


def test_the_native_run_is_windows_only_whatever_the_record_lists(root: Path,
                                                                   monkeypatch) -> None:
    """A record that listed native_run for arm64 would not make a user's run of anything
    count there: the clause is the user's native Windows run."""
    path = root / TR.GREEN_MEANS
    path.write_text(path.read_text().replace(
        "| `arm64-linux` | `replays` |",
        "| `arm64-linux` | `native_run` | `clauses.native_run` | x |\n| `arm64-linux` | `replays` |"))
    arm64(root, monkeypatch)
    installer(root, {"folder": route_entry(NATIVE_RUNNER)})
    false_because(run(root, "arm64-linux", "native_run"),
                  "is not the user's own x86_64-windows run")


# ------------------------------------------------------------------ the receipt tool's CLI


def test_the_receipt_tool_writes_checks_and_takes_no_evidence_arguments(
        root: Path, monkeypatch, capsys) -> None:
    """--check reads CURRENT on what the tool just wrote, ignoring only when and at which
    commit it was written, and STALE on a moved verdict, green flag, pipeline or clause;
    --out writes only there; evidence paths are the record's, not arguments."""
    arm64(root, monkeypatch)
    rel = TR.RECEIPT.format(target="arm64-linux")
    args = ["--target", "arm64-linux", "--root", str(root)]
    assert TR.main(args) == 0 and (root / rel).is_file()
    assert TR.main(args + ["--check"]) == 0
    assert "target receipt CURRENT: arm64-linux green=True" in capsys.readouterr().out
    held = json.loads((root / rel).read_text())
    assert TR.UNCOMPARED == ("written", "commit")
    for key, value in (("written", "2030-01-01T00:00:00+00:00"), ("commit", "0" * 40)):
        write(root, rel, dict(held, **{key: value}))
        assert TR.main(args + ["--check"]) == 0, key

    def edited(change) -> dict:
        doc = json.loads(json.dumps(held))
        change(doc)
        return doc

    for change, field in ((lambda d: d["clauses"]["build"].update(verdict=False), "clauses"),
                          (lambda d: d.update(green=False), "green"),
                          (lambda d: d["inputs"]["pipeline"].update({TR.SELF: "00" * 32}),
                           "inputs"),
                          (lambda d: d["clauses"].pop("replays"), "clauses"),
                          (lambda d: d.update(command="typed"), "command")):
        write(root, rel, edited(change))
        capsys.readouterr()
        assert TR.main(args + ["--check"]) == 1, field
        assert f"differs in {field}" in capsys.readouterr().out
    out = root / "private/scratch/arm.receipt"
    write(root, rel, held)
    assert TR.main(args + ["--out", str(out)]) == 0 and json.loads(out.read_text())["green"]
    assert json.loads((root / rel).read_text()) == held
    with pytest.raises(SystemExit):
        TR.main(args + ["--evidence", f"build={at('arm64-linux', 'build')}"])


def test_the_receipt_pins_the_tool_the_record_and_the_checkers_it_calls(root: Path) -> None:
    for rel in TR.PIPELINE:
        if rel != TR.GREEN_MEANS:
            write(root, rel, f"# {rel}\n")
    doc = TR.build_receipt(root, "arm64-linux")
    assert doc["inputs"]["pipeline"] == {rel: sha(root / rel) for rel in TR.PIPELINE}
    assert TR.PIPELINE == ("tools/s7/target_receipt.py", "Documentation/s7-green-means.md",
                           "Documentation/scripts/check_replay_regression.py",
                           "Documentation/scripts/check_s5_exit.py")


# ------------------------------------------------------------------ the generator


@pytest.fixture
def receipts(root: Path, monkeypatch) -> Path:
    """The three receipts as the tool writes them: x86_64-linux and arm64-linux green,
    x86_64-windows without its evidence; the generator recomputing them with this TR. The
    two tools are present where G7's predicate looks, before any receipt pins them."""
    for rel in (TG.SELF, TR.SELF):
        write(root, rel, "# tool\n")
    arm64(root, monkeypatch)
    for target in ("arm64-linux", "x86_64-windows"):
        write(root, TR.RECEIPT.format(target=target), TR.build_receipt(root, target))
    monkeypatch.setattr(TG, "receipt_tool", lambda: TR)
    return root


def test_the_generator_lists_exactly_the_targets_whose_receipts_read_green(receipts) -> None:
    doc = TG.generate(receipts)
    assert doc["green"] == ["x86_64-linux", "arm64-linux"]
    assert doc["generated_by"] == TG.SELF and doc["green_means"] == TG.GREEN_MEANS
    assert doc["green_means_sha256"] == sha(receipts / TG.GREEN_MEANS)
    rel = TG.RECEIPT.format(target="x86_64-windows")
    assert doc["receipts"]["x86_64-windows"] == {"path": rel, "sha256": sha(receipts / rel),
                                                 "green": False}


def test_the_generator_lists_an_absent_receipt_as_null_and_not_green(receipts) -> None:
    for target in ("arm64-linux", "x86_64-windows"):
        (receipts / TG.RECEIPT.format(target=target)).unlink()
    doc = TG.generate(receipts)
    assert doc["green"] == ["x86_64-linux"]
    assert doc["receipts"]["x86_64-windows"] is None and doc["receipts"]["arm64-linux"] is None


def edit(root: Path, target: str, change) -> None:
    rel = TG.RECEIPT.format(target=target)
    doc = json.loads((root / rel).read_text())
    change(doc)
    write(root, rel, doc)


def typed(root: Path, target: str) -> None:
    """A hand-typed receipt (the review of S7's second wave): every listed clause true on a
    tracked file at its real sha256, green true, no facts."""
    evidence = [{"path": TR.REGISTRY, "sha256": sha(root / TR.REGISTRY)}]
    edit(root, target, lambda d: d.update(green=True, clauses={
        name: {"evidence": evidence, "facts": {}, "verdict": True, "why": "typed"}
        for name in d["clauses"]}))


@pytest.mark.parametrize(("arrange", "reason"), [
    (lambda r: edit(r, "x86_64-linux", lambda d: d["clauses"]["replays"].update(verdict=False)),
     "reads green True, and its clause verdicts False"),
    (lambda r: edit(r, "x86_64-linux", lambda d: d["clauses"].pop("host")),
     "holds no verdict for the host clause"),
    (lambda r: edit(r, "x86_64-linux", lambda d: d.update(generated_by="tools/other.py")),
     "written by tools/s7/target_receipt.py"),
    (lambda r: edit(r, "x86_64-linux", lambda d: d.update(target="arm64-linux")),
     "is not x86_64-linux's receipt"),
    (lambda r: edit(r, "x86_64-linux", lambda d: d["clauses"]["build"].update(evidence=[])),
     "the build clause reads True with no evidence"),
    # Malformed and stale at once: refused, since staleness is read only of a receipt that is
    # otherwise whole.
    (lambda r: (write(r, TR.REACH, dict(reach(), note="moved")),
                edit(r, "x86_64-linux", lambda d: d.update(green=False))),
     "reads green False, and its clause verdicts True"),
])
def test_the_generator_refuses_a_receipt_it_cannot_hold(receipts, arrange, reason: str) -> None:
    arrange(receipts)
    with pytest.raises(TG.Refused, match=reason.replace("(", "\\(").replace(")", "\\)")) as e:
        TG.generate(receipts)
    assert not isinstance(e.value, TG.Stale)


@pytest.mark.parametrize(("target", "arrange", "reason"), [
    ("x86_64-linux", lambda r: edit(r, "x86_64-linux",
                                    lambda d: d.update(green_means_sha256="00" * 32)),
     "s7-target-x86_64-linux-v1.json was written under another version of "
     "Documentation/s7-green-means.md; re-run tools/s7/target_receipt.py --target x86_64-linux"),
    ("x86_64-linux", lambda r: write(r, TR.REACH, dict(reach(), note="moved")),
     "s7-target-x86_64-linux-v1.json: Documentation/receipts/s4-run-reach-v1.json is not the "
     "file its build clause read; re-run tools/s7/target_receipt.py --target x86_64-linux"),
    ("arm64-linux", lambda r: typed(r, "arm64-linux"),
     "s7-target-arm64-linux-v1.json is not what tools/s7/target_receipt.py writes from the "
     "evidence now (it differs in clauses); re-run tools/s7/target_receipt.py --target "
     "arm64-linux"),
    ("x86_64-windows", lambda r: typed(r, "x86_64-windows"),
     "s7-target-x86_64-windows-v1.json is not what tools/s7/target_receipt.py writes"),
    ("arm64-linux",
     lambda r: write(r, "private/generated/s7/targets/aarch64-linux-musl/build.jsonl", "\n"),
     "s7-target-arm64-linux-v1.json is not what tools/s7/target_receipt.py writes from the "
     "evidence now (it differs in clauses, green)"),
])
def test_a_stale_or_typed_receipt_is_not_green_and_the_file_says_why(receipts, target: str,
                                                                     arrange, reason: str) -> None:
    """A receipt that is not current is not counted, and the generator still writes the file:
    the target reads not green, its entry says why, and every other target reads as its own
    receipt does."""
    before = TG.generate(receipts)
    arrange(receipts)
    doc = TG.generate(receipts)
    rel = TG.RECEIPT.format(target=target)
    entry = doc["receipts"][target]
    assert entry["path"] == rel and entry["sha256"] == sha(receipts / rel)
    assert entry["green"] is False and reason in entry["stale"], entry
    assert target not in doc["green"]
    assert doc["green"] == [t for t in before["green"] if t != target]
    assert all("stale" not in (doc["receipts"][t] or {}) for t in TARGETS if t != target)


def test_the_generator_refuses_a_record_without_a_target(root: Path) -> None:
    path = root / TG.GREEN_MEANS
    path.write_text("\n".join(line for line in path.read_text().splitlines()
                              if not line.startswith("| `arm64-linux` | `b")
                              and not line.startswith("| `arm64-linux` | `r")))
    with pytest.raises(TG.Refused, match="lists no clause for arm64-linux"):
        TG.generate(root)


def test_the_generator_writes_checks_and_refuses_from_its_command_line(receipts, capsys) -> None:
    out = receipts / "private/generated/targets.json"
    assert TG.main(["--root", str(receipts)]) == 0
    assert json.loads(out.read_text())["green"] == ["x86_64-linux", "arm64-linux"]
    assert TG.main(["--root", str(receipts), "--check"]) == 0
    assert "targets CURRENT: 2/3 green (x86_64-linux, arm64-linux)" in capsys.readouterr().out
    (receipts / TG.RECEIPT.format(target="x86_64-windows")).unlink()
    assert TG.main(["--root", str(receipts), "--check"]) == 1
    # A typed receipt reads stale: the file is written, without it, and says why.
    capsys.readouterr()
    typed(receipts, "arm64-linux")
    assert TG.main(["--root", str(receipts)]) == 0
    printed = capsys.readouterr().out
    assert ("targets STALE_RECEIPT arm64-linux: Documentation/receipts/s7-target-arm64-linux-v1"
            ".json is not what tools/s7/target_receipt.py writes from the evidence now") in printed
    assert ("1/3 green (x86_64-linux); no receipt for x86_64-windows; stale, so not green: "
            "arm64-linux") in printed
    held = json.loads(out.read_text())
    assert held["green"] == ["x86_64-linux"]
    assert "is not what tools/s7/target_receipt.py writes" in held["receipts"]["arm64-linux"]["stale"]
    assert TG.main(["--root", str(receipts), "--check"]) == 0
    # A receipt it cannot read is refused, and the written file is left as it was.
    edit(receipts, "x86_64-linux", lambda d: d.update(generated_by="tools/other.py"))
    assert TG.main(["--root", str(receipts)]) == 1
    assert "targets REFUSED" in capsys.readouterr().err
    assert json.loads(out.read_text())["green"] == ["x86_64-linux"]


def test_what_the_generator_writes_reads_met_at_g7_and_a_typed_receipt_does_not(
        receipts, monkeypatch) -> None:
    """End to end: the receipt tool's receipts for all three targets, the generator's file,
    and G7's predicate recomputing them and reading met; then the reviewer's probe, typed
    receipts for the two other targets with targets.json naming them, reads unmet."""
    monkeypatch.setattr(X, "target_tool", lambda _root: TR)
    write(receipts, TG.OUT, TG.generate(receipts))
    result = X.targets_input(receipts)
    assert result.met, result.detail
    assert result.detail == ("2 of 3 green by their receipts' clause verdicts, written by "
                             "tools/s7/targets.py")
    for target in ("x86_64-windows", "arm64-linux"):
        typed(receipts, target)
    doc = json.loads((receipts / TG.OUT).read_text())
    for target in ("x86_64-windows", "arm64-linux"):
        rel = TG.RECEIPT.format(target=target)
        doc["receipts"][target] = {"path": rel, "sha256": sha(receipts / rel), "green": True}
    write(receipts, TG.OUT, dict(doc, green=list(TARGETS)))
    result = X.targets_input(receipts)
    assert not result.met
    for target in ("x86_64-windows", "arm64-linux"):
        assert (f"{TG.RECEIPT.format(target=target)} is not what tools/s7/target_receipt.py "
                "derives from the evidence now: it differs in clauses") in result.detail


def test_a_stale_reference_reads_not_green_in_m8_and_unmet_at_g7(receipts, monkeypatch) -> None:
    """The generator's file with the reference stale (its reach receipt re-run since): M8
    renders the file, without the reference; G7's predicate reads it unmet, naming why."""
    monkeypatch.setattr(X, "target_tool", lambda _root: TR)
    write(receipts, TR.REACH, dict(reach(), note="moved"))
    doc = TG.generate(receipts)
    assert "x86_64-linux" not in doc["green"]
    write(receipts, TG.OUT, doc)
    for tool in (X.TARGETS_TOOL, X.TARGET_TOOL):
        assert (receipts / tool).is_file()
    metric = metrics.m8_targets(receipts)
    listed = ", ".join(doc["green"]) or "none"
    assert metric.value.endswith(f": {listed}") and "x86_64-linux" not in metric.value
    result = X.targets_input(receipts)
    assert not result.met
    assert "s4-run-reach-v1.json" in result.detail, result.detail


# ------------------------------------------------------------------ the live receipts


def test_the_tracked_reference_receipt_is_to_its_contract() -> None:
    """The x86_64-linux receipt in the tree is the tool's, under the record as it stands, with
    a verdict for each clause the record lists, green their conjunction, and every verdict
    true, as the falsifiers G7 declares on it expect."""
    rel = TR.RECEIPT.format(target="x86_64-linux")
    doc = json.loads((ROOT / rel).read_text())
    means = TR.green_means(ROOT)
    assert (doc["schema"], doc["generated_by"], doc["target"]) == (TR.SCHEMA, TR.SELF,
                                                                    "x86_64-linux")
    assert doc["green_means"] == TR.GREEN_MEANS and doc["green_means_sha256"] == means.sha256
    assert list(doc["clauses"]) == means.clauses["x86_64-linux"]
    assert doc["green"] is True and all(c["verdict"] for c in doc["clauses"].values())
    assert all(c["evidence"] for c in doc["clauses"].values())


@pytest.mark.parametrize("target", TARGETS)
def test_every_tracked_target_receipt_is_current_or_read_stale(target: str) -> None:
    """Each target receipt in the tree reproduces from the tree's evidence on this machine
    (`--check`), or it is stale and then counts for nothing: the generator reads it not green
    and says why, and G7's targets_input reads it unmet (`reproduced`). A receipt is stale from
    a change that moves its evidence (a runtime source edited, so the replays and their receipt
    are re-run) until the milestone re-runs it (tools/s7/refresh_after_runtime.py; the fast
    mode of Documentation/decisions/2026-10-11-S7-fast-mode.md moves receipt refreshes there),
    so G7 still closes only on current receipts. A target with no receipt yet has nothing to
    reproduce; targets_input reads it unmet."""
    rel = TR.RECEIPT.format(target=target)
    if not (ROOT / rel).is_file():
        assert target != "x86_64-linux", rel
        return
    if TR.main(["--target", target, "--check", "--root", str(ROOT)]) == 0:
        return
    entry = TG.generate(ROOT)["receipts"][target]
    assert entry["green"] is False, entry
    assert f"re-run tools/s7/target_receipt.py --target {target}" in entry["stale"], entry
    with pytest.raises(X.Unmet, match="is not what tools/s7/target_receipt.py derives"):
        X.reproduced(ROOT, target)
