"""tools/s7/arm64_replays.py and tools/s7/arm64_link.py: the arm64 replay runs' measuring
instrument.

What is tested, each against literal expected values:

- the run is tick_replay's: a stage entry's loop_run command, as stage_run.command makes it, is
  parsed back to its one --run, its probe arguments and its build's hooks and wraps; the probe's
  wall-clock alarm is the only argument scaled;
- the labels: "no divergence" exactly when the M5 record has neither a residue nor a divergent
  tick and the identity rule finds nothing; "residues only" with the first tick the classes
  absorbed; "divergence" otherwise;
- the float reading of a differing word: ulp distances across zero, and NaN kinds;
- the per-site transcendental counts, end to end: a static aarch64 program whose functions are
  named as lifted ones call the shims, built with -DSA2_F80_SITES and run under qemu-user, is
  read back to the right function, shim and count through the binary's own symbol table;
- the fused multiply-add count the link records sees contraction, and -ffp-contract=off removes
  it;
- the receipt: built from the reference's own tick results it reads "residues only" with the
  reference's residues and check_s7_exit.py's arm64 predicate is met; an unclassed divergence
  reads "divergence" and the predicate is unmet; a run that is not qemu's is refused;
- the tracked receipt, when it exists: its labels are the ones its own tick results derive.

The hardware tests need the pinned zig and qemu-user under private/, and skip without them.
"""
from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
TOOLS = HERE.parents[3] / "tools" / "s7"
sys.path.insert(0, str(TOOLS))

import arm64_link as A  # noqa: E402
import arm64_replays as R  # noqa: E402

ROOT = R.ROOT
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))


@pytest.fixture(scope="module")
def t(tmp_path_factory):
    """The tools, with tools/s4/wordlog.py's build directory and tick_replay's and wordlog's
    caches put back afterwards: R.tools points the first under the test's work directory and
    holds the caches read-once, and other test files in the same process read both."""
    import os  # noqa: PLC0415
    env = os.environ.get("SA2_WORDLOG_BUILD")
    for sub in ("tools/s4", "tools/s3/whole_image", "tools/oracle"):
        if str(ROOT / sub) not in sys.path:
            sys.path.insert(0, str(ROOT / sub))
    import tick_replay  # noqa: PLC0415
    import wordlog  # noqa: PLC0415
    mods = {"T": tick_replay, "WL": wordlog}
    kept = {(m, n): getattr(mods[m], n) for m, n, _ in R.CACHES}
    got = R.tools(tmp_path_factory.mktemp("work"))
    yield got
    for (m, n), cache in kept.items():
        setattr(mods[m], n, cache)
    if env is None:
        os.environ.pop("SA2_WORDLOG_BUILD", None)
    else:
        os.environ["SA2_WORDLOG_BUILD"] = env
    got["WL"].BUILD = Path(env or ROOT / "private" / "generated" / "s4" / "wordlog")


def bits(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


# ------------------------------------------------------------------ the run is tick_replay's


def test_a_stage_entry_s_command_is_parsed_back_to_its_run(t):
    entry = R.entries()["metalharbor"]
    # stage_run.command names its observers relative to the tree, so they must be under it.
    base = ROOT / "private" / "generated" / "s7"
    base.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix="arm64-replays-test-", dir=base))
    try:
        cmd, read = R.loop_command(t, entry, out)
        plan = R.parse_loop_cmd(cmd)
    finally:
        shutil.rmtree(out, ignore_errors=True)
    assert read["stage"] is True
    assert plan["label"] == "ticks"
    assert plan["hooks"] == t["SR"].HOOKS and plan["wraps"] == t["SR"].WRAPS
    assert plan["main"] == t["RR"].MAIN
    polls = t["T"].stage_poll_budget(entry)
    assert plan["env"]["SA2_PUMP_POLLS"] == str(polls)
    assert plan["env"]["SA2_STAGE_TRACE"] == R.rel(out / "stage-trace.csv")
    assert plan["env"]["SA2_REPLAY_WORDS"] == R.rel(out / "lifted-words.bin")
    assert plan["env"]["SA2_INPUT_SCRIPT"] == str((ROOT / entry["input"]["script"]).resolve())
    # tick_replay's alarm, max(600, polls // 5), then the import-table dump it adds.
    alarm = max(600, polls // 5)
    assert plan["probe_args"][-4:] == ["--timeout", str(alarm), "--dump-imports",
                                       R.rel(out / "imports.table.rec")]


def test_the_boot_command_is_run_lifted_s():
    cmd = ["py", "loop_run.py", "--tag", "next+allarms", "--main", "harness/crt_probe.c",
           "--hook", "harness/replay/wrap_tick_probe.c", "--wrap", "sub_433ee0",
           "--run", "ticks|SA2_PUMP_POLLS=1300|SA2_REPLAY_TICKS=a/lifted.csv"]
    plan = R.parse_loop_cmd(cmd)
    assert plan == {"label": "ticks", "env": {"SA2_PUMP_POLLS": "1300",
                                              "SA2_REPLAY_TICKS": "a/lifted.csv"},
                    "probe_args": [], "timeout": 600, "main": "harness/crt_probe.c",
                    "hooks": ["harness/replay/wrap_tick_probe.c"], "wraps": ["sub_433ee0"]}


def test_two_runs_in_one_command_are_refused():
    with pytest.raises(SystemExit, match="2 --run"):
        R.parse_loop_cmd(["--run", "a", "--run", "b"])


def test_only_the_alarm_is_scaled():
    assert R.scaled_probe_args(["--x", "1", "--timeout", "3129", "--dump-imports", "t"], 30) == (
        ["--x", "1", "--dump-imports", "t", "--timeout", "93870"],
        {"tick_replay": 3129, "ran": 93870, "scale": 30, "passed_by_tick_replay": True})
    assert R.scaled_probe_args([], 30) == (
        ["--timeout", "3600"],
        {"tick_replay": 120, "ran": 3600, "scale": 30, "passed_by_tick_replay": False})


# ------------------------------------------------------------------ labels


@pytest.mark.parametrize("rec, identity, want", [
    ({"first_divergent_tick": None}, [], ("no divergence", [], None)),
    ({"first_divergent_tick": None, "residues": []}, [], ("no divergence", [], None)),
    ({"first_divergent_tick": None, "residues": ["host-timing-v1"], "residues_from": 4}, [],
     ("residues only", ["host-timing-v1"], 4)),
    ({"first_divergent_tick": 17, "residues": ["host-timing-v1"]}, [],
     ("divergence", ["host-timing-v1"], 17)),
    ({"first_divergent_tick": None}, ["boot: an identity word paired twice"],
     ("divergence", [], None)),
])
def test_labels(rec, identity, want):
    assert R.label(rec, identity) == want


def test_ulp_distance_counts_floats_across_zero():
    one = bits(1.0)
    assert R.ulp_distance(one, one + 1) == 1
    assert R.ulp_distance(bits(0.0), bits(-0.0)) == 0
    assert R.ulp_distance(bits(-1.0), bits(1.0)) == 2 * one
    assert R.ulp_distance(0x7FC00000, one) is None          # a NaN reads no distance
    assert R.ulp_distance(0x7F800000, one) is None          # nor an infinity


@pytest.mark.parametrize("a, b, kind", [
    (0xFFC00000, 0x7FC00000, "nan sign"),
    (0x7FC00001, 0x7FC00000, "nan payload"),
    (0x7FC00000, 0x3F800000, "nan against a number"),
    (0x3F800000, 0x3F800001, "number"),
])
def test_word_kinds(a, b, kind):
    assert R.word_kind(a, b) == kind


# ------------------------------------------------------------------ hardware

HAVE = A.ZIG.is_file() and R.QEMU.is_file()
needs_tools = pytest.mark.skipif(not HAVE, reason="the pinned zig or qemu-aarch64 is absent")

CALLER = r"""
#include <stdint.h>
typedef unsigned __int128 u128;
u128 sa2_lf80_atan2(u128, u128);
u128 sa2_lf80_sin(u128);
u128 sa2_lf80_from_f64(double);
/* Each uses the shim's result, as lifted code stores it, so no call is a tail call: a tail
 * call returns to its caller's caller, and the site would be read back there. Lifted functions
 * return remill's memory pointer, never the shim's i128, so their calls are never tail calls. */
__attribute__((noinline)) u128 sub_00401000(u128 x) { return sa2_lf80_atan2(x, x) + 1; }
__attribute__((noinline)) u128 sub_0040A000(u128 x) { return sa2_lf80_sin(x) + 1; }
int main(void) {
    volatile u128 acc = sa2_lf80_from_f64(0.5);
    for (int i = 0; i < 3; i++) acc = sub_00401000(acc);
    for (int i = 0; i < 2; i++) acc = sub_0040A000(acc);
    return 0;
}
"""


def zig(*args: str, cwd: Path | None = None) -> None:
    subprocess.run([str(A.ZIG), *args], check=True, capture_output=True, cwd=cwd)


def build_prog(d: Path, source: str) -> dict:
    """`source` compiled for aarch64 as one lifted module, linked static with the measuring
    shims, run under qemu with SA2_F80_STATS, and a run record naming the binary, the counts
    and a link record over a one-module log, as arm64_link writes them."""
    (d / "caller.c").write_text(source)
    zig("cc", "-target", R.TRIPLE, "-O2", "-c", *A.SHIM_DEFINES, str(TOOLS / "f80_shims.c"),
        "-o", str(d / "shims.o"))
    zig("cc", "-target", R.TRIPLE, "-O2", "-c", str(d / "caller.c"), "-o", str(d / "caller.o"))
    zig("cc", "-target", R.TRIPLE, "-static", str(d / "caller.o"), str(d / "shims.o"),
        "-o", str(d / "prog"))
    stats = d / R.STATS
    subprocess.run([str(R.QEMU), str(d / "prog")], check=True, env={"SA2_F80_STATS": str(stats)})
    log = d / "build.jsonl"
    log.write_text(json.dumps({"object": "caller.o", "transcendental": {"atan2l": 1, "sinl": 1}})
                   + "\n" + json.dumps({"object": "other.o", "transcendental": {}}) + "\n")
    link = d / R.LINK_REC
    link.write_text(json.dumps({"lifted": {"log": str(log), "log_sha256": R.sha256(log)}}))
    return {"replay": "t", "f80_stats": {"path": str(stats), "sha256": R.sha256(stats)},
            "binary": {"path": str(d / "prog"), "sha256": R.sha256(d / "prog")},
            "link": {"path": str(link), "sha256": R.sha256(link)}}


@needs_tools
def test_each_transcendental_call_site_is_read_back_to_its_function(tmp_path):
    got = R.transcendentals(build_prog(tmp_path, CALLER))
    assert got["calls"] == {"atan2l": 3, "sinl": 2}
    assert got["site_count"] == 2
    assert [(s["function"], s["guest_va"], s["shim"], s["calls"]) for s in got["sites"]] == [
        ("sub_00401000", "0x00401000", "atan2l", 3), ("sub_0040A000", "0x0040a000", "sinl", 2)]
    assert got["static"] == {"modules": 1, "calls": 2, "tail_calls": 0, "by_shim": {
        "sa2_lf80_atan2": {"call": 1, "tail": 0}, "sa2_lf80_sin": {"call": 1, "tail": 0}}}


@needs_tools
def test_a_tail_call_to_a_transcendental_is_refused(tmp_path):
    tail = CALLER.replace("return sa2_lf80_sin(x) + 1;", "return sa2_lf80_sin(x);")
    with pytest.raises(SystemExit, match="tail-call a transcendental shim 1 times"):
        R.transcendentals(build_prog(tmp_path, tail))


@needs_tools
def test_a_run_without_per_site_counts_is_refused(tmp_path):
    (tmp_path / "s").write_text(json.dumps({"calls": {"sinl": 0}}))
    run = {"replay": "t", "f80_stats": {"path": str(tmp_path / "s"),
                                         "sha256": R.sha256(tmp_path / "s")},
           "binary": {"path": str(tmp_path / "s")}}
    with pytest.raises(SystemExit, match="no per-site counts"):
        R.transcendentals(run)


@needs_tools
def test_the_fma_count_sees_contraction_and_the_flag_removes_it(tmp_path):
    src = tmp_path / "f.c"
    src.write_text("float f(float a, float b, float c) { return a * b + c; }\n"
                   "double g(double a, double b, double c) { return a * b - c; }\n")
    zig("cc", "-target", R.TRIPLE, "-O2", "-c", str(src), "-o", str(tmp_path / "on.o"))
    zig("cc", "-target", R.TRIPLE, "-O2", *A.FP_FLAGS, "-c", str(src),
        "-o", str(tmp_path / "off.o"))
    assert A.fma_words(tmp_path / "on.o") == 2
    assert A.fma_words(tmp_path / "off.o") == 0


FUSED = r"""
__attribute__((noinline)) float fused_f(float a, float b, float c) { return a * b + c; }
__attribute__((noinline)) double fused_g(double a, double b, double c) { return a * b - c; }
__attribute__((noinline)) double plain(double a, double b) { return a * b; }
int main(int argc, char **argv) {
    (void)argv;
    return (int)(fused_f(argc, 2, 3) + fused_g(argc, 2, 3) + plain(argc, 2));
}
"""


def fused_binary(d: Path) -> Path:
    (d / "fused.c").write_text(FUSED)
    zig("cc", "-target", R.TRIPLE, "-O2", "-static", str(d / "fused.c"), "-o", str(d / "fused"))
    return d / "fused"


@needs_tools
def test_the_fused_multiply_adds_are_read_by_the_function_holding_each(tmp_path):
    binary = fused_binary(tmp_path)
    assert R.Elf(binary).fma_functions() == {"fused_f": 1, "fused_g": 1}
    assert A.fma_words(binary) == 2
    link = tmp_path / R.LINK_REC
    link.write_text(json.dumps({"fma_words": {"binary": 2, "lifted_whole_image": 0}}))
    run = {"replay": "t", "link": {"path": str(link)}}
    assert R.fma_reading(R.Elf(binary), run) == {
        "words": 2, "functions": {"fused_f": 1, "fused_g": 1},
        "by_group": {"binary": 2, "lifted_whole_image": 0}}
    link.write_text(json.dumps({"fma_words": {"binary": 3}}))
    with pytest.raises(SystemExit, match="holds 2 fused multiply-add words, and its link record "
                                         "counts 3"):
        R.fma_reading(R.Elf(binary), run)


@needs_tools
def test_a_binary_other_than_the_one_that_ran_is_refused(tmp_path):
    run = build_prog(tmp_path, CALLER)
    run["binary"]["sha256"] = "0" * 64
    with pytest.raises(SystemExit, match="is not the binary it ran"):
        R.transcendentals(run)


# ------------------------------------------------------------------ the receipt


def reference_results() -> dict[str, dict]:
    doc = json.loads((ROOT / "Documentation/receipts/s4-tick-replay-v1.json").read_text())
    return {r["name"]: r["result"] for r in doc["replays"]}


@pytest.fixture(scope="module")
def stats_binary(tmp_path_factory):
    """A run's binary, its per-site counts and its link record, for receipts built in tests."""
    if not HAVE:
        pytest.skip("the pinned zig or qemu-aarch64 is absent")
    return build_prog(tmp_path_factory.mktemp("prog"), CALLER)


def fake_work(work: Path, stats_binary: dict, results: dict[str, dict],
              runner: str = "qemu") -> None:
    for name, result in results.items():
        d = work / name
        d.mkdir(parents=True)
        kind = R.qemu_runner() if runner == "qemu" else {"kind": runner}
        run = {"replay": name, "runner": kind, "triple": R.TRIPLE,
               "binary": stats_binary["binary"], "link": stats_binary["link"],
               "f80_stats": stats_binary["f80_stats"],
               "argv": [], "env": {}, "probe_alarm": {}, "seconds": 1.0, "started": "",
               "lifted": {"stop": {"note": "n"}, "capture": "c", "sha256": "0" * 64}}
        (d / R.RUN_REC).write_text(json.dumps(run))
        comp = {"run": R.pinned(d / R.RUN_REC), "result": result,
                "registry_sha256": R.sha256(ROOT / R.REGISTRY),
                "goal_complete": True if name == "metalharbor" else None}
        (d / R.COMPARE_REC).write_text(json.dumps(comp))


def predicate_root(tmp: Path, doc: dict) -> Path:
    """A root holding what check_s7_exit.arm64 reads: the registry, the residue records, the
    receipt, and the tool the receipt names."""
    root = tmp / "root"
    (root / "Documentation" / "receipts").mkdir(parents=True)
    shutil.copy(ROOT / R.REGISTRY, root / R.REGISTRY)
    shutil.copytree(ROOT / R.RESIDUES, root / R.RESIDUES)
    (root / "tools" / "s7").mkdir(parents=True)
    (root / R.SELF).write_text("")
    (root / R.RECEIPT).write_text(json.dumps(doc))
    return root


def test_the_reference_s_results_read_residues_only_and_meet_the_predicate(
        t, tmp_path, stats_binary):
    import check_s7_exit as X  # noqa: PLC0415
    results = reference_results()
    fake_work(tmp_path / "w", stats_binary, results)
    doc = R.receipt(t, tmp_path / "w", None, list(results))
    for name, run in doc["replays"].items():
        m5 = t["T"].m5_record({"name": name, "result": results[name]})
        assert run["result"] == "residues only"
        assert run["residues"] == m5["residues"]
        assert run["first_divergent_tick"] == m5["residues_from"]
        assert run["transcendental_sites"] == 2
    got = X.arm64(predicate_root(tmp_path, doc))
    assert got.met, got.detail


def test_an_unclassed_divergence_reads_divergence_and_fails_the_predicate(
        t, tmp_path, stats_binary):
    import check_s7_exit as X  # noqa: PLC0415
    results = reference_results()
    boot = json.loads(json.dumps(results["boot"]))
    boot["reported"] = {"tick": 33, "field": "d_01740000", "kind": "value"}
    boot["class"] = "bug"
    results["boot"] = boot
    fake_work(tmp_path / "w", stats_binary, results)
    doc = R.receipt(t, tmp_path / "w", None, list(results))
    assert doc["replays"]["boot"]["result"] == "divergence"
    assert doc["replays"]["boot"]["first_divergent_tick"] == 33
    got = X.arm64(predicate_root(tmp_path, doc))
    assert not got.met and "boot: result 'divergence'" in got.detail


def test_a_residue_with_no_record_is_refused(t, tmp_path, stats_binary):
    results = reference_results()
    boot = json.loads(json.dumps(results["boot"]))
    boot["residues"]["seen"] = list(boot["residues"]["seen"]) + ["arm64-no-such-class-v1"]
    results["boot"] = boot
    fake_work(tmp_path / "w", stats_binary, results)
    with pytest.raises(SystemExit, match="names the residue arm64-no-such-class-v1, and "
                                         "Documentation/receipts/residues/arm64-no-such-class"):
        R.receipt(t, tmp_path / "w", None, list(results))


def test_a_run_not_under_qemu_is_refused(t, tmp_path, stats_binary):
    fake_work(tmp_path / "w", stats_binary, reference_results(), runner="native")
    with pytest.raises(SystemExit, match="not an aarch64-linux-musl run under qemu"):
        R.receipt(t, tmp_path / "w", None, list(reference_results()))


def test_the_receipt_covers_every_registered_replay(t, tmp_path, stats_binary):
    fake_work(tmp_path / "w", stats_binary, {"boot": reference_results()["boot"]})
    with pytest.raises(SystemExit, match="every registered replay"):
        R.receipt(t, tmp_path / "w", None, ["boot"])


TRACKED = ROOT / R.RECEIPT


@pytest.mark.skipif(not TRACKED.is_file(), reason="no arm64 receipt is tracked yet")
def test_the_tracked_receipt_s_labels_are_its_tick_results(t):
    import check_s7_exit as X  # noqa: PLC0415
    doc = json.loads(TRACKED.read_text())
    assert doc["schema"] == R.SCHEMA and doc["generated_by"] == R.SELF
    assert doc["runner"]["kind"] == "qemu" and doc["runner"]["headless"] is True
    assert doc["registry_sha256"] == R.sha256(ROOT / R.REGISTRY)
    assert sorted(doc["replays"]) == sorted(R.entries())
    for name, run in doc["replays"].items():
        rec = t["T"].m5_record({"name": name, "result": run["tick_result"]})
        assert R.label(rec, run["identity_problems"]) == (
            run["result"], run["residues"], run["first_divergent_tick"])
        assert run["transcendental_sites"] == len(run["transcendentals"]["sites"])
    got = X.arm64(ROOT)
    assert got.met, got.detail


def test_a_comparison_under_another_registry_is_refused(t, tmp_path, stats_binary):
    results = reference_results()
    fake_work(tmp_path / "w", stats_binary, results)
    comp = tmp_path / "w" / "boot" / R.COMPARE_REC
    doc = json.loads(comp.read_text())
    doc["registry_sha256"] = "0" * 64
    comp.write_text(json.dumps(doc))
    with pytest.raises(SystemExit, match="boot: its comparison was made under another"):
        R.receipt(t, tmp_path / "w", None, list(results))


# ------------------------------------------------------------------ against the x86-64 run


def group(record: str, name: str, differing: int, tick: int, listing: str) -> dict:
    return {"record": record, "group": name, "rule": {"held": True},
            "lifted": {"differing": differing, "first": {"tick": tick}, "listing_sha256": listing}}


def compare_file(path: Path, groups: list[dict]) -> Path:
    path.write_text(json.dumps({"result": {"residues": {"groups": groups}}}))
    return path


def test_residue_groups_are_read_beside_the_x86_64_run_s(tmp_path):
    a = compare_file(tmp_path / "a", [group("host-timing-v1", "frames", 3, 1, "aa"),
                                      group("host-timing-v1", "reads", 18, 381, "bb")])
    b = compare_file(tmp_path / "b", [group("host-timing-v1", "frames", 3, 1, "aa"),
                                      group("host-timing-v1", "reads", 17, 381, "cc")])
    assert R.residue_groups(a, b) == [
        {"record": "host-timing-v1", "group": "frames", "equal": True,
         "arm64": {"differing": 3, "first_tick": 1, "listing_sha256": "aa", "rule_held": True},
         "x86_64": {"differing": 3, "first_tick": 1, "listing_sha256": "aa", "rule_held": True}},
        {"record": "host-timing-v1", "group": "reads", "equal": False,
         "arm64": {"differing": 18, "first_tick": 381, "listing_sha256": "bb", "rule_held": True},
         "x86_64": {"differing": 17, "first_tick": 381, "listing_sha256": "cc",
                    "rule_held": True}}]
    assert R.residue_groups(a, tmp_path / "absent") is None


def link_file(path: Path, sources: dict[str, str]) -> Path:
    path.write_text(json.dumps({"compile": {"objects": {
        k: {"source": k, "source_sha256": v} for k, v in sources.items()}}}))
    return path


def test_the_reference_must_be_linked_from_the_same_sources(tmp_path):
    a = link_file(tmp_path / "a", {"sa2_trap.c": "11", "main.c": "22"})
    same = link_file(tmp_path / "b", {"main.c": "22", "sa2_trap.c": "11"})
    other = link_file(tmp_path / "c", {"main.c": "22", "sa2_trap.c": "33"})
    assert R.same_sources(a, same)["sources"] == 2
    with pytest.raises(SystemExit, match=r"not linked from the same sources: \['sa2_trap.c'\]"):
        R.same_sources(a, other)


@needs_tools
def test_a_qemu_pin_is_held_to_its_hash(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    pins = tmp_path / "tools" / "pins.json"
    monkeypatch.setattr(R, "ROOT", tmp_path)
    pins.write_text(json.dumps({"tools": {}}))
    assert R.qemu_runner()["pin"]["pinned"] is False
    pins.write_text(json.dumps({"tools": {R.QEMU_PIN: {"sha256": R.sha256(R.QEMU)}}}))
    got = R.qemu_runner()
    assert got["pin"]["pinned"] is True and got["kind"] == "qemu" and got["headless"] is True
    pins.write_text(json.dumps({"tools": {R.QEMU_PIN: {"sha256": "0" * 64}}}))
    with pytest.raises(SystemExit, match="is not the qemu"):
        R.qemu_runner()
    pins.write_text(json.dumps({"tools": {R.QEMU_PIN: {"version": "10"}}}))
    with pytest.raises(SystemExit, match="names no sha256"):
        R.qemu_runner()


# ------------------------------------------------------------------ the build census


def census_inputs(tmp: Path, pass_sha: str, shims_sha: str) -> tuple[Path, Path, dict]:
    """A cross build's summary and log, and a link record per registered replay naming a
    binary, as tools/s7/cross_build.py and arm64_link.py write them."""
    objs, out = tmp / "objs", tmp / "out"
    objs.mkdir(parents=True)
    log = objs / "build.jsonl"
    log.write_text(json.dumps({"population": "map", "va": "0x00401000", "compiled": True}) + "\n")
    (objs / "summary.json").write_text(json.dumps(
        {"target": A.TRIPLE, "legalized": True, "pass_sha256": pass_sha}))
    shas = {}
    for name in R.entries():
        d = out / "bin" / name
        d.mkdir(parents=True)
        (d / "sa2_main").write_bytes(name.encode())
        shas[name] = R.sha256(d / "sa2_main")
        (d / A.LINK_REC).write_text(json.dumps({
            "returncode": 0, "binary": str(d / "sa2_main"), "binary_sha256": shas[name],
            "lifted": {"log_sha256": R.sha256(log)}, "shims": {"source_sha256": shims_sha}}))
    return objs, out, shas


def test_the_census_names_each_replay_s_binary_and_the_tree_s_passes(tmp_path):
    passes = {p: R.sha256(ROOT / p) for p in A.PASSES}
    objs, out, shas = census_inputs(tmp_path, passes[A.PASSES[0]], passes[A.SHIMS])
    A.main(["census", "--out", str(out), "--objects", str(objs),
            "--census-out", str(tmp_path / "census.rec")])
    doc = json.loads((tmp_path / "census.rec").read_text())
    assert doc["schema"] == "sa2-s7-build-census/v1" and doc["target"] == "arm64-linux"
    assert doc["triple"] == "aarch64-linux-musl"
    assert doc["link"] == {"returncode": 0, "binaries": shas}
    assert doc["target_passes"] == passes
    assert doc["log"]["sha256"] == R.sha256(objs / "build.jsonl")


def test_the_census_refuses_objects_or_shims_made_by_other_passes(tmp_path):
    passes = {p: R.sha256(ROOT / p) for p in A.PASSES}
    objs, out, _ = census_inputs(tmp_path / "a", "0" * 64, passes[A.SHIMS])
    with pytest.raises(SystemExit, match="was built by another tools/s7/f80_legalize.py"):
        A.main(["census", "--out", str(out), "--objects", str(objs)])
    objs, out, _ = census_inputs(tmp_path / "b", passes[A.PASSES[0]], "0" * 64)
    with pytest.raises(SystemExit, match="linked shims compiled from another tools/s7/f80_shims.c"):
        A.main(["census", "--out", str(out), "--objects", str(objs)])


def test_the_files_the_comparison_reads_are_held_equal_one_by_one():
    run = {"sha256": "c1", "words": {"path": "w.zst", "sha256": "z1", "check": {"log_sha256": "w1"}},
           "stage_trace": {"sha256": "s1"}}
    assert R.files_equal(run, json.loads(json.dumps(run))) == {"tick capture": True,
                                                               "stage trace": True}
    # the word log is not one of them: its bytes carry the host clock (cross_target diffs it
    # word by word)
    other = json.loads(json.dumps(run))
    other["words"]["check"]["log_sha256"] = "w2"
    assert R.files_equal(run, other) == {"tick capture": True, "stage trace": True}
    # a file only one run keeps reads unequal; one neither keeps is left out
    del other["stage_trace"]
    other["sha256"] = "c2"
    assert R.files_equal(run, other) == {"tick capture": False, "stage trace": False}


# ------------------------------------------------------------------ one native side, many runs


class _FakeTick:
    """tick_replay's comparison functions as compare_one calls them, recording what each call
    was given: word_native returns a context, word_compare reads one field of the run it is given
    and writes a file in the directory it is given."""

    def __init__(self) -> None:
        self.native_calls = 0
        self.compare_dirs: list[Path] = []

    def residue_classes(self, entry):
        return []

    def native_probe_current(self, entry):
        return {"current": True}

    def read_capture(self, path):
        return ["tick"], [(0, [1])]

    def word_native(self, entry, work):
        self.native_calls += 1
        (work / "mask").write_text("m")
        return {"mask": work / "mask"}

    def word_compare(self, entry, ctx, lifted, work):
        assert ctx["mask"].read_text() == "m"
        (work / "listing").write_text(lifted["capture"])
        self.compare_dirs.append(work)
        return {"first_divergence": None, "seen": lifted["capture"]}

    def is_stage(self, entry):
        return False

    def m5_record(self, rec):
        return {"name": rec["name"]}


def test_runs_of_one_replay_share_one_native_side_and_keep_their_own_results(tmp_path):
    entry = R.entries()["boot"]
    if not (ROOT / entry["native"]["capture"]).is_file():
        pytest.skip("the registered boot capture is absent")
    works = []
    for side in ("arm64", "x86"):
        w = tmp_path / side
        (w / "boot").mkdir(parents=True)
        cap = w / "boot" / "lifted.csv"
        cap.write_text(side)
        (w / "boot" / R.RUN_REC).write_text(json.dumps(
            {"lifted": {"capture": str(cap), "sha256": R.sha256(cap), "stop": {"note": ""}}}))
        works.append(w)
    fake = _FakeTick()
    recs = R.compare_one({"T": fake, "SB": None}, "boot", works)
    assert fake.native_calls == 1
    assert len(set(fake.compare_dirs)) == 2
    for w, rec in zip(works, recs):
        on_disk = json.loads((w / "boot" / R.COMPARE_REC).read_text())
        assert on_disk["result"] == {"first_divergence": None, "seen": str(w / "boot" / "lifted.csv")}
        assert on_disk["run"] == R.pinned(w / "boot" / R.RUN_REC)
        assert on_disk["native_side"]["shared_with"] == [R.rel(x / "boot" / R.RUN_REC)
                                                         for x in works]
        assert rec == on_disk
    # a run whose kept file moved after its record is refused before anything is compared
    (works[1] / "boot" / "lifted.csv").write_text("moved")
    fake = _FakeTick()
    with pytest.raises(SystemExit, match="changed since its run was recorded"):
        R.compare_one({"T": fake, "SB": None}, "boot", works)
    assert fake.native_calls == 0


# ------------------------------------------------------------------ the cross-target reading


class _FakeWordLog:
    """tools/s4/wordlog.py diff as cross_target calls it: (rows, meta) for the two logs given,
    the x86-64 run's first."""

    def __init__(self, rows: list[dict], through: int) -> None:
        self.rows, self.through, self.calls = rows, through, []

    def diff(self, a, b):
        self.calls.append((Path(a).name, Path(b).name))
        return [dict(r) for r in self.rows], {"compared_through": self.through}


def cross_work(tmp: Path) -> tuple[Path, Path]:
    """An arm64 work directory and an x86-64 one, each holding a boot run record over a word
    log and a link record of the same sources, as arm64_replays run and arm64_link write them."""
    works = []
    for side, runner, triple in (("a64", R.qemu_runner() if HAVE else {"kind": "qemu"},
                                  R.TRIPLE), ("x86", {"kind": "native"}, "x86_64-linux-musl")):
        d = tmp / side / "boot"
        d.mkdir(parents=True)
        words = d / f"{side}-words.bin.zst"
        words.write_bytes(side.encode())
        link = link_file(d / R.LINK_REC, {"sa2_trap.c": "11", "main.c": "22"})
        (d / R.RUN_REC).write_text(json.dumps({
            "runner": runner, "triple": triple, "binary": {"sha256": side * 4},
            "link": R.pinned(link),
            "lifted": {"sha256": "c", "words": {"path": str(words), "sha256": R.sha256(words)},
                       "stage_trace": {"sha256": "s"}}}))
        works.append(tmp / side)
    return works[0], works[1]


def test_the_cross_target_reading_lists_each_word_with_its_float_distance(tmp_path):
    a64, x86 = cross_work(tmp_path)
    one = bits(1.0)
    rows = [{"va": 0x01740010, "first_tick": 9, "last_tick": 12, "ticks_differing": 4,
             "a": one, "b": one + 3},
            {"va": 0x01740000, "first_tick": 9, "last_tick": 9, "ticks_differing": 1,
             "a": 0xFFC00000, "b": 0x7FC00000},
            {"va": 0x00100000, "first_tick": 2, "last_tick": 2, "ticks_differing": 1,
             "a": bits(-1.0), "b": bits(1.0)}]
    wl = _FakeWordLog(rows, 1299)
    got = R.cross_target({"WL": wl}, "boot", a64, x86)
    assert wl.calls == [("x86-words.bin.zst", "a64-words.bin.zst")]   # x86-64 first
    assert got["compared"] is True and got["compared_through_tick"] == 1299
    assert got["words_differing"] == 3 and got["first_tick"] == 2
    assert got["kinds"] == {"nan sign": 1, "number": 2}
    assert got["max_ulp"] == 2 * one
    assert [(w["va"], w["first_tick"], w["x86_64"], w["arm64"], w["kind"], w["ulp"])
            for w in got["words"]] == [
        ("0x00100000", 2, f"0x{bits(-1.0):08x}", f"0x{bits(1.0):08x}", "number", 2 * one),
        ("0x01740000", 9, "0xffc00000", "0x7fc00000", "nan sign", None),
        ("0x01740010", 9, f"0x{one:08x}", f"0x{one + 3:08x}", "number", 3)]
    assert got["files_equal"] == {"tick capture": True, "stage trace": True}
    assert got["residue_groups"] is None                 # neither run was compared yet
    assert R.cross_target({"WL": wl}, "boot", a64, None) is None


def test_the_cross_target_reading_refuses_a_reference_that_is_not_native_x86_64(tmp_path):
    a64, _ = cross_work(tmp_path)
    with pytest.raises(SystemExit, match="the reference run is not a native x86-64 run"):
        R.cross_target({"WL": _FakeWordLog([], 0)}, "boot", a64, a64)


def test_a_word_log_moved_since_its_run_is_refused(tmp_path):
    a64, x86 = cross_work(tmp_path)
    (x86 / "boot" / "x86-words.bin.zst").write_bytes(b"moved")
    with pytest.raises(SystemExit, match="changed since its run was recorded"):
        R.cross_target({"WL": _FakeWordLog([], 0)}, "boot", a64, x86)


def test_the_magnitude_says_what_differs_from_the_capture_and_from_the_x86_64_run():
    tr = {"site_count": 1, "sites": [{"function": "sub_0086BCD8", "shim": "atan2l", "calls": 7}]}
    rule = {"kind": "below", "held": True, "broken": None,
            "limits": {"0x0174b494": 30, "0x01a557ec": 180}, "max": {"0x0174b494": "0x0000001c",
                                                                     "0x01a557ec": "0x00000025"}}
    result = {"residues": {"groups": [{"record": "host-timing-v1", "group": "frames", "rule": rule,
                                       "lifted": {"differing": 3, "first": {"tick": 1}}}]}}
    cross = {"compared": True, "words_differing": 0, "compared_through_tick": 1299,
             "files_equal": {"tick capture": True, "stage trace": True},
             "residue_groups": [{"group": "frames", "equal": True}]}
    assert R.magnitude_text("boot", "residues only", {}, result, cross, tr) == (
        "boot: against the native capture, the residue groups' words differ: frames "
        "(host-timing-v1) 3 words from tick 1, each below its limit: 0x0174b494 at most 28 of 30, "
        "0x01a557ec at most 37 of 180; against the x86-64 run of the same sources, no "
        "word of the logged section differs at any of 1299 ticks; byte-identical: tick capture, "
        "stage trace; the 1 residue groups read exactly as in the x86-64 run (the same differing "
        "words, first ticks and listings); 1 transcendental call sites reached: sub_0086BCD8 "
        "atan2l x7.")
    moved = dict(cross, words_differing=2, first_tick=40, kinds={"number": 2}, max_ulp=1,
                 residue_groups=[{"group": "frames", "equal": False}])
    text = R.magnitude_text("boot", "residues only", {}, result, moved, tr)
    assert "2 words differ from tick 40 (number 2; largest float distance 1 ulp)" in text
    assert "differently from the x86-64 run in frames" in text
    groups = {"binary": 7, "runtime_and_harness": 0, "shims": 0, "lifted_extras": 0,
              "lifted_whole_image": 0}
    fma = {"words": 7, "functions": {"log": 1, "log2": 3, "pow": 3}, "by_group": groups}
    assert R.magnitude_text("boot", "residues only", {}, result, cross, dict(tr, fma=fma)).endswith(
        "sub_0086BCD8 atan2l x7; the binary holds 7 fused multiply-add words: log x1, log2 x3, "
        "pow x3, and its lifted objects, runtime, harness and shims hold none.")
    fma = dict(fma, by_group=dict(groups, lifted_extras=2))
    assert R.magnitude_text("boot", "residues only", {}, result, cross, dict(tr, fma=fma)).endswith(
        "pow x3, by object group lifted_extras 2, lifted_whole_image 0, runtime_and_harness 0, "
        "shims 0.")


@pytest.mark.parametrize("rule, want", [
    ({"kind": "zero", "held": True, "nonzero_words": 0},
     "zero in the run in every listed word, where the capture holds the host's values"),
    ({"kind": "none", "held": True}, "no bound (the record's rule is none)"),
    ({"kind": "native", "held": True, "form": "one-tick-scan"},
     "held to the native capture's own one-tick-scan form"),
    ({"kind": "below", "held": False, "broken": {"tick": 9, "va": "0x0174b494",
                                                 "value": "0x0000001e"}},
     "its below rule broken at tick 9 (0x0174b494 = 0x0000001e)"),
])
def test_each_rule_says_by_how_much_its_words_differ(rule, want):
    assert R.rule_bound(rule) == want


# ------------------------------------------------------------------ the caches, read once


def test_the_caches_are_held_read_once_and_a_renamed_one_is_refused(t):
    import types  # noqa: PLC0415
    assert {(m, n): type(getattr(t[m], n)).__name__ for m, n, _ in R.CACHES} == {
        ("T", "_STREAMS"): "_ReadOnce", ("T", "_CANONICAL"): "_ReadOnce",
        ("T", "_WORDS"): "_ReadOnce", ("WL", "_FIELDS"): "_ReadOnce",
        ("WL", "_CAPTURES"): "_Unkept", ("WL", "_DIFFS"): "_Unkept"}
    once = R._ReadOnce()
    once["k"] = 1
    assert "k" in once and once["k"] == 1 and "k" not in once     # one read drops it
    unkept = R._Unkept()
    unkept["k"] = 1
    assert "k" not in unkept                                        # every lookup misses
    fake_t = types.SimpleNamespace(__name__="tick_replay", _STREAMS={}, _CANONICAL={}, _WORDS={})
    fake_wl = types.SimpleNamespace(__name__="wordlog", _FIELDS={}, _CAPTURES={})
    with pytest.raises(SystemExit, match="wordlog._DIFFS is not a dict"):
        R.read_once(fake_t, fake_wl)


def test_a_stream_read_through_the_read_once_caches_is_the_kept_one(t, tmp_path):
    """tick_replay.log_stream over a registered native log, once with its own caches and once
    with the read-once ones: the same header and rows, and nothing left held."""
    entry = R.entries()["boot"]
    T, WL = t["T"], t["WL"]
    try:
        log = T.native_log(entry, entry["native"])
    except SystemExit as e:
        pytest.skip(f"the registered boot log is not here: {e}")
    spec = T.identity_spec(entry)
    mask = T.mask_for(entry)
    kept = {(m, n): getattr(t[m], n) for m, n, _ in R.CACHES}
    try:
        for m, n, _ in R.CACHES:
            setattr(t[m], n, {})
        want = T.log_stream(log, mask, spec["extra"], native=True)
        R.read_once(T, WL)
        got = T.log_stream(log, mask, spec["extra"], native=True)
        assert got == want
        assert all(len(getattr(t[m], n)) == 0 for m, n, _ in R.CACHES)
    finally:
        for (m, n), cache in kept.items():
            setattr(t[m], n, cache)
