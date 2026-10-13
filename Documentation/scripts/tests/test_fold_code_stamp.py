"""The objects the reach links are tied to the fold code that made them.

The reach manifest digests the linked objects by name and size, and the emitter
determinism gate re-folds seven panel functions in a scratch directory, so an edit to
the fold code (tools/spike/pc24pass/pc24pass.py, tools/s2/corpus_run/internalize.py,
tools/s2/corpus_run/speed_rewrite.py, and since State localization
tools/s2/corpus_run/state_local.py) left every gate green over objects the previous
code had folded (review of the S5 speed change, SHOULD-FIX 3). Now:

* tools/s4/rebuild_image.py stamps the fold code, the pass's two helper modules and the
  command each tool that builds a linked object compiles with, when a rebuild completes;
  deletes the stamp before it starts; and writes none when the code moved during the
  rebuild or when an object the link reads was not written by it;
* tools/s3/whole_image/compile_all.py --force records a module whose latest lift or pass
  failed as not compiled, so the stager refuses the object its last good build left;
* tools/s4/reach_receipt.py pins the fold code in its pipeline, records the stamp in
  its manifest (`generated.fold_code`), and refuses to run over objects whose stamp is
  not the tree's fold code;
* Documentation/scripts/check_run_reach.py fails a receipt whose stamp is not the
  live one (a rebuild since the run) and a stamp that is not the tree's fold code (only
  a rebuild clears it).

No guest runs and nothing is rebuilt or compiled: every subprocess is stood in for, and
every stamp, log and object is under pytest's tmp_path.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(ROOT / "Documentation" / "scripts"))
sys.path.append(str(ROOT / "tools" / "s3" / "arms"))
import check_run_reach as CRR  # noqa: E402
import reach_receipt as RR  # noqa: E402
import rebuild_image as RI  # noqa: E402

FOLD_FILES = ("tools/spike/pc24pass/pc24pass.py", "tools/s2/corpus_run/internalize.py",
              "tools/s2/corpus_run/speed_rewrite.py", "tools/s2/corpus_run/state_local.py",
              "private/generated/spike/pc24pass/pc_round.ll",
              "private/generated/s2/corpus_run/helper/x87_int.ll")
# What each tool that builds a linked object compiles a folded module with, as the tree
# has it: tools/spike/spikelib.py ZIG, and each tool's flags.
COMMAND = ("private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig cc -target "
           "x86_64-linux-musl -O2 -w -c {ir} -o {object}")
COMMANDS = {"tools/s3/whole_image/compile_all.py:one": COMMAND,
            "tools/s3/arms/lift_arms.py:fold_and_compile": COMMAND,
            "tools/s4/lift_extra.py:fold_and_compile": COMMAND}
# The real restage check, kept before the rebuild fixture stands in for it.
STAGED_NOT_REBUILT = RI.staged_not_rebuilt


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ----------------------------------------------------------------- the stamp itself

def test_the_fold_code_is_the_four_scripts_and_the_two_helpers_by_content_and_the_commands(
        tmp_path):
    for rel in FOLD_FILES[:-1]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(rel)
    assert RI.fold_code(tmp_path) == {**{rel: sha(rel) for rel in FOLD_FILES[:-1]},
                                      FOLD_FILES[-1]: "", **COMMANDS}


def test_each_compile_command_is_read_from_the_code_that_runs_it_and_nothing_compiles(
        monkeypatch):
    """compile_all.py keeps its flags in TARGET and OPT, lift_extra.py in its own TARGET
    and OPT, and lift_arms.py types them inline: each step is run with a recorder for
    `subprocess`, so a flag moved in any of the three is a different command (NIT 4)."""
    import compile_all as CA
    import lift_arms as LA
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    assert RI.compile_commands() == COMMANDS
    monkeypatch.setattr(CA, "OPT", "-O3")
    monkeypatch.setattr(RI.LE, "TARGET", ["-target", "x86_64-linux-gnu"])
    source = textwrap.dedent(inspect.getsource(LA.fold_and_compile)).replace('"-O2", "-w"',
                                                                             '"-O1", "-w"')
    inline = {}
    exec(compile(source, LA.__file__, "exec"), LA.__dict__, inline)
    monkeypatch.setattr(LA, "fold_and_compile", inline["fold_and_compile"])
    assert RI.compile_commands() == {
        "tools/s3/whole_image/compile_all.py:one": COMMAND.replace("-O2", "-O3"),
        "tools/s3/arms/lift_arms.py:fold_and_compile": COMMAND.replace("-O2", "-O1"),
        "tools/s4/lift_extra.py:fold_and_compile": COMMAND.replace("linux-musl", "linux-gnu")}


def test_a_step_that_compiles_nothing_cannot_be_recorded(monkeypatch):
    """compile_all.one catches a fold that raises and compiles nothing: then nothing says
    what compiles its objects, and the fold code cannot be read."""
    import internalize
    monkeypatch.setattr(internalize, "rewrite", lambda text: 1 / 0)
    with pytest.raises(RuntimeError, match=r"^tools/s3/whole_image/compile_all.py:one ran no "
                                           r"command for a module"):
        RI.compile_commands()


def test_a_compile_command_the_tree_does_not_match_is_named_whole():
    now = {**{rel: sha(rel) for rel in FOLD_FILES}, **COMMANDS}
    other = COMMAND.replace("-O2", "-O1")
    stamped = dict(now, **{"tools/s4/lift_extra.py:fold_and_compile": other})
    assert RI.fold_code_problems(stamped, now) == [
        "the objects the link reads were compiled by tools/s4/lift_extra.py:fold_and_compile "
        f"with `{other}` and the tree's compiles with `{COMMAND}`: run tools/s4/rebuild_image.py"]
    # a stamp written before the commands were in it names each one
    files = {rel: sha(rel) for rel in FOLD_FILES}
    assert RI.fold_code_problems(files, now) == [
        f"the objects the link reads were compiled by {step} with no command and the tree's "
        f"compiles with `{COMMAND}`: run tools/s4/rebuild_image.py" for step in sorted(COMMANDS)]


def test_a_stamp_the_tree_does_not_match_is_named_file_by_file():
    now = {rel: sha(rel) for rel in FOLD_FILES}
    assert RI.fold_code_problems(dict(now), now) == []
    moved = dict(now, **{"tools/s2/corpus_run/speed_rewrite.py": "1" * 64})
    assert RI.fold_code_problems(moved, now) == [
        "the objects the link reads were folded with tools/s2/corpus_run/speed_rewrite.py at "
        f"111111111111 and the tree holds {sha('tools/s2/corpus_run/speed_rewrite.py')[:12]}: "
        "run tools/s4/rebuild_image.py"]
    [none] = RI.fold_code_problems({}, now)
    assert none.startswith("the objects the link reads carry no fold-code stamp (")
    assert none.endswith("so nothing says which fold code made them: run tools/s4/rebuild_image.py")


# --------------------------------------------------------------------- the rebuild

class _Pool:
    """multiprocessing's fork pool, for a rebuild with no extras: it maps in this process."""

    def __init__(self, jobs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def map(self, f, rows, chunksize=1):
        return [f(r) for r in rows]


@pytest.fixture
def rebuild(tmp_path, monkeypatch):
    """rebuild_image.main with every step stood in for: no lift, compile or restage runs,
    the object directories are empty ones of the test's, and the stamp is the test's.
    `codes` is what fold_code returns at the start and at the end; `steps` collects each
    command the rebuild would have run, and `actions` holds what a step does to the test's
    objects, by script."""
    state = {"codes": [{"a": "1"}, {"a": "1"}], "steps": [], "fail": False, "actions": {}}
    monkeypatch.setattr(sys, "argv", ["rebuild_image.py", "--jobs", "1"])
    monkeypatch.setattr(RI, "STAMP", tmp_path / "next+allarms.fold-code.json")
    monkeypatch.setattr(RI, "STATE", tmp_path / "extra_functions.json")
    (tmp_path / "extra_functions.json").write_text(json.dumps({"rows": []}))
    for name in ("MAP_BUILD", "ARM_BUILD", "MERGED_BUILD"):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(RI, name, tmp_path / name)

    def run(cmd):
        state["steps"].append(cmd[1])
        if state["fail"]:
            raise subprocess.CalledProcessError(1, cmd)
        state["actions"].get(cmd[1], lambda: None)()
        return 0.0
    monkeypatch.setattr(RI, "run", run)
    monkeypatch.setattr(RI, "fold_code", lambda root=None: state["codes"].pop(0))
    monkeypatch.setattr(RI, "fresh_fold_code", lambda: state["codes"].pop(0))
    monkeypatch.setattr(RI.C, "memory_image", lambda: b"")
    monkeypatch.setattr(RI.multiprocessing, "get_context",
                        lambda kind: types.SimpleNamespace(Pool=_Pool))
    monkeypatch.setattr(RI, "staged_not_rebuilt", lambda: [])
    return state


def test_a_complete_rebuild_stamps_the_fold_code_it_read_before_it_started(rebuild, capsys):
    assert RI.main() == 0
    assert json.loads(RI.STAMP.read_text()) == {"generated_by": "tools/s4/rebuild_image.py",
                                               "fold_code": {"a": "1"}}
    assert rebuild["steps"] == ["tools/s3/whole_image/lift_all.py",
                                "tools/s3/whole_image/compile_all.py",
                                "tools/s3/arms/lift_arms_image.py",
                                "tools/s3/arms/stage_merged_build.py"]
    assert json.loads(capsys.readouterr().out)["stamp"] == str(RI.STAMP)


def test_a_rebuild_that_stops_part_way_leaves_no_stamp(rebuild):
    RI.STAMP.write_text(json.dumps({"fold_code": {"a": "0"}}))
    rebuild["fail"] = True
    with pytest.raises(subprocess.CalledProcessError):
        RI.main()
    assert not RI.STAMP.exists() and rebuild["steps"] == ["tools/s3/whole_image/lift_all.py"]


def test_a_rebuild_during_which_the_fold_code_moved_writes_no_stamp(rebuild):
    rebuild["codes"] = [{"a": "1"}, {"a": "2"}]
    with pytest.raises(SystemExit, match="the fold code changed during the rebuild"):
        RI.main()
    assert not RI.STAMP.exists()


def test_the_end_of_a_rebuild_reads_the_tree_in_a_new_interpreter(rebuild, monkeypatch):
    """The rebuild imports the tools whose commands it records once, so at its end the
    tree is read by a new interpreter: a compile command edited while it ran is a change,
    though this process still holds the command it started with."""
    monkeypatch.setattr(RI, "fold_code", lambda root=None: {"a": "1"})
    monkeypatch.setattr(RI, "fresh_fold_code", lambda: {"a": "2"})
    with pytest.raises(SystemExit, match="the fold code changed during the rebuild"):
        RI.main()
    assert not RI.STAMP.exists()


def test_a_new_interpreter_reads_the_commands_the_files_hold(monkeypatch):
    import compile_all as CA
    files = {rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
             if (ROOT / rel).is_file() else "" for rel in FOLD_FILES}
    assert RI.fresh_fold_code() == {**files, **COMMANDS}
    monkeypatch.setattr(CA, "OPT", "-O3")            # this process's copy only
    assert RI.fold_code()["tools/s3/whole_image/compile_all.py:one"].endswith(
        "-O3 -w -c {ir} -o {object}")
    assert RI.fresh_fold_code() == {**files, **COMMANDS}


def write(path: Path, data: bytes) -> None:
    """As the compiler writes an object: a new file renamed into its place, so it never
    has the old one's inode, even with the same bytes."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def restage() -> None:
    """tools/s3/arms/stage_merged_build.py --force for the map's objects: each link in
    build/next+allarms replaced by one to the map's file of that name."""
    for p in RI.MAP_BUILD.glob("ir_*.o"):
        (RI.MERGED_BUILD / p.name).unlink(missing_ok=True)
        os.link(p, RI.MERGED_BUILD / p.name)


KEPT = ("object(s) the link reads were not written by this rebuild (the same inode and "
        "mtime as before it), so an earlier build's fold code made them and no stamp is "
        "written: first ")


def test_a_staged_object_the_rebuild_did_not_write_is_refused_and_no_stamp_is_written(
        rebuild, monkeypatch):
    """The review's case (SHOULD-FIX 1): a forced re-lift in which the pass now refuses
    one module. Its old object stays, the restage links it again, and it is the same file
    as the map's own, so the restage check passes; the stamp would name fold code that
    never touched it."""
    monkeypatch.setattr(RI, "staged_not_rebuilt", STAGED_NOT_REBUILT)
    write(RI.MAP_BUILD / "ir_00401000.o", b"OLD FOLD CODE OBJECT")
    write(RI.MAP_BUILD / "ir_00402000.o", b"OLD")
    restage()
    rebuild["actions"] = {
        "tools/s3/whole_image/compile_all.py":
            lambda: write(RI.MAP_BUILD / "ir_00402000.o", b"NEW"),    # 0x00401000 refused
        "tools/s3/arms/stage_merged_build.py": restage}
    with pytest.raises(SystemExit) as e:
        RI.main()
    assert str(e.value) == (f"1 {KEPT}{[str(RI.MERGED_BUILD / 'ir_00401000.o')]}. A module the "
                            "lift or the pass now refuses keeps its old object; its record in "
                            "the build log says why")
    assert not RI.STAMP.exists()
    assert STAGED_NOT_REBUILT() == []          # the inode check alone let it through


def test_a_rebuild_that_wrote_every_linked_object_is_stamped(rebuild, monkeypatch):
    monkeypatch.setattr(RI, "staged_not_rebuilt", STAGED_NOT_REBUILT)
    write(RI.MAP_BUILD / "ir_00401000.o", b"OLD")
    restage()
    rebuild["actions"] = {
        "tools/s3/whole_image/compile_all.py":
            lambda: write(RI.MAP_BUILD / "ir_00401000.o", b"OLD"),    # the same bytes again
        "tools/s3/arms/stage_merged_build.py": restage}
    assert RI.main() == 0
    assert json.loads(RI.STAMP.read_text())["fold_code"] == {"a": "1"}


def test_an_extra_the_rebuild_did_not_write_is_refused(rebuild, monkeypatch, tmp_path):
    obj = tmp_path / "extras" / "ir_00500000.o"
    obj.parent.mkdir()
    write(obj, b"OLD")
    RI.STATE.write_text(json.dumps({"rows": [
        {"va": "0x00500000", "end": "0x00500010", "object": str(obj), "compiled": True}]}))
    monkeypatch.setattr(RI, "_extra", lambda r: {"va": r["va"], "compiled": True,
                                                 "same_path": True})
    with pytest.raises(SystemExit) as e:
        RI.main()
    assert str(e.value).startswith(f"1 {KEPT}{[str(obj)]}. ")
    assert not RI.STAMP.exists()

    def rebuilt(r):
        write(obj, b"OLD")
        return {"va": r["va"], "compiled": True, "same_path": True}
    monkeypatch.setattr(RI, "_extra", rebuilt)
    rebuild["codes"] = [{"a": "1"}, {"a": "1"}]
    assert RI.main() == 0 and RI.STAMP.is_file()


def test_a_forced_compile_records_a_module_whose_pass_now_fails_and_the_stager_refuses_it(
        tmp_path, monkeypatch, capsys):
    """The root cause of the case above (SHOULD-FIX 1): compile_all.py --force gave a
    module whose latest pass failed no record, so its last good build's compiled record
    stayed the latest and tools/s3/arms/stage_merged_build.py linked that build's object.
    Now its record says it was not compiled, and the stager refuses the old link as a
    stray. The real compile_all.main and stage_merged_build.main, over the test's logs:
    no module qualifies for a compile, so nothing is compiled."""
    import common as C
    import compile_all as CA
    import stage_merged_build as SMB
    gen = tmp_path / "gen"
    for name, sub in (("LIFT", "lift"), ("FOLD", "fold"), ("BUILD", "build")):
        monkeypatch.setattr(C, name, gen / "s3" / "whole_image" / sub)
    monkeypatch.setattr(SMB, "ARMS", gen / "s3" / "arms")
    for d in (C.LIFT, C.BUILD / "next", C.BUILD / "next+allarms", SMB.ARMS / "build" / "next"):
        d.mkdir(parents=True)
    record = {"va": "0x00401000", "name": "f", "region": ".text", "lifted": True}
    (C.LIFT / "next.jsonl").write_text(
        json.dumps(dict(record, **{"pass": {"ok": True}})) + "\n"
        + json.dumps(dict(record, **{"pass": {"ok": False, "error_head": "ValueError: no"}}))
        + "\n")
    (C.BUILD / "next.jsonl").write_text(json.dumps(
        {"va": "0x00401000", "name": "f", "region": ".text", "compiled": True}) + "\n")
    (SMB.ARMS / "arms.next.jsonl").write_text("")
    write(C.BUILD / "next" / "ir_00401000.o", b"OLD FOLD CODE OBJECT")
    os.link(C.BUILD / "next" / "ir_00401000.o", C.BUILD / "next+allarms" / "ir_00401000.o")

    monkeypatch.setattr(sys, "argv", ["compile_all.py", "--tag", "next", "--force",
                                      "--jobs", "1"])
    CA.main()
    assert C.read_jsonl(C.BUILD / "next.jsonl")["0x00401000"] == {
        "va": "0x00401000", "name": "f", "region": ".text", "folded": False,
        "compiled": False, "error_head": "not compiled, its latest lift failed: the PC24 pass "
                                         "refused it: ValueError: no"}
    monkeypatch.setattr(sys, "argv", ["stage_merged_build.py", "--tag", "next", "--merged-tag",
                                      "next+allarms", "--force"])
    with pytest.raises(SystemExit, match="REFUSED: the staged directory does not match the "
                                         "merged log"):
        SMB.main()
    doc = json.loads((SMB.ARMS / "stage.next+allarms.json").read_text())
    assert doc["stray_objects_in_the_staged_directory"] == ["ir_00401000.o"]
    # A module remill did not lift this time is recorded the same way.
    (C.LIFT / "next.jsonl").write_text(json.dumps(dict(record, **{"pass": {"ok": False}}))
                                       + "\n" + json.dumps(dict(record, lifted=False,
                                                                error_head="rc 1")) + "\n")
    monkeypatch.setattr(sys, "argv", ["compile_all.py", "--tag", "next", "--force",
                                      "--jobs", "1"])
    CA.main()
    assert C.read_jsonl(C.BUILD / "next.jsonl")["0x00401000"]["error_head"] == (
        "not compiled, its latest lift failed: remill did not lift it: rc 1")


# ------------------------------------------------------------------- the reach run

def test_every_pin_list_of_the_fold_code_names_all_of_it():
    """The fold code is pinned in five lists: the stamp's, the reach manifest's, both
    build-time receipts' and the emitter determinism gate's. A script added to the stamp and
    left out of one of the others would move objects that list's receipt does not see."""
    import build_time_extras as BTE
    import build_time_jobs as BTJ
    import check_emitter_determinism as G
    assert set(RI.FOLD_CODE) == set(FOLD_FILES[:4])
    assert set(RI.FOLD_CODE) <= set(RR.TOOLS) & set(BTJ.PIPELINE) & set(BTE.PIPELINE) & set(
        G.PIPELINE_FILES.values())


def test_the_reach_pins_the_fold_code_and_records_the_stamp(tmp_path, monkeypatch):
    assert set(FOLD_FILES[:4]) == set(RI.FOLD_CODE) <= set(RR.TOOLS)
    stamp = tmp_path / "next+allarms.fold-code.json"
    stamp.write_text(json.dumps({"fold_code": {"tools/s2/corpus_run/speed_rewrite.py": "9" * 64}}))
    monkeypatch.setattr(RI, "STAMP", stamp)
    m = RR.manifest()
    assert m["generated"]["fold_code"] == {"tools/s2/corpus_run/speed_rewrite.py": "9" * 64}
    assert {rel: m["pipeline"][rel] for rel in RI.FOLD_CODE} == {
        rel: hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() for rel in RI.FOLD_CODE}
    stamp.unlink()
    assert RR.manifest()["generated"]["fold_code"] == {}


def test_the_reach_run_refuses_objects_another_fold_code_made(monkeypatch):
    monkeypatch.setitem(sys.modules, "rebuild_image", types.SimpleNamespace(
        staged_not_rebuilt=lambda: [],
        fold_code_problems=lambda: ["the objects were folded with x at 0 and the tree holds 1"]))
    monkeypatch.setattr(RR.subprocess, "run", lambda *a, **k: pytest.fail("nothing may run"))
    with pytest.raises(SystemExit) as e:
        RR.run("t")
    assert str(e.value) == "REACH_FAILED the objects were folded with x at 0 and the tree holds 1"


# --------------------------------------------------------------------------- the gate

def _manifest(stamp: dict) -> dict:
    return {"tag": "t", "main": "m", "exe_sha256": "e", "runtime_sha256": "r",
            "generated": {"mb_edges_c_sha256": "c", "lifted_extras_sha256": "x",
                          "lifted_module_digest": "l", "lifted_module_count": 1,
                          "whole_image_module_digest": "w", "fold_code": stamp},
            "tracked": {}, "environment": {}, "probe_args": [], "pipeline": {}}


def _problems(tmp_path: Path, recorded: dict, live: dict, now: dict) -> list[str]:
    receipt = tmp_path / "reach.json"
    receipt.write_text(json.dumps({
        "id": "s4-run-reach-v1", "manifest": _manifest(recorded),
        "reach": {"iterations": 10 ** 9, "import_calls": 1, "frames_presented": 1,
                  "import_table": {"path": "p", "sha256": "s"}}}))
    return CRR.problems(receipt, _manifest(live), {"source": "p", "source_sha256": "s"}, now)


def test_the_gate_holds_the_objects_stamp_to_the_tree_and_the_receipt_to_the_stamp(tmp_path):
    now = {rel: sha(rel) for rel in FOLD_FILES}
    old = dict(now, **{"tools/s2/corpus_run/internalize.py": "0" * 64})
    assert _problems(tmp_path, now, now, now) == []
    # The objects are another fold code's, and the receipt agrees with them: a reach
    # re-run cannot clear it, so the gate says to rebuild.
    assert _problems(tmp_path, old, old, now) == [
        "the objects the link reads were folded with tools/s2/corpus_run/internalize.py at "
        f"000000000000 and the tree holds {sha('tools/s2/corpus_run/internalize.py')[:12]}: "
        "run tools/s4/rebuild_image.py"]
    # The objects were rebuilt with the tree's code after the run: the receipt is stale.
    [stale] = _problems(tmp_path, old, now, now)
    assert stale.startswith("the reach receipt is stale: generated.fold_code was ")
    # No stamp at all.
    [none] = _problems(tmp_path, {}, {}, now)
    assert none.startswith("the objects the link reads carry no fold-code stamp")
    # The objects were compiled with another command and the receipt agrees with them.
    tree = {**now, **COMMANDS}
    other = COMMAND.replace("-O2", "-O0")
    compiled = dict(tree, **{"tools/s3/arms/lift_arms.py:fold_and_compile": other})
    assert _problems(tmp_path, compiled, compiled, tree) == [
        "the objects the link reads were compiled by tools/s3/arms/lift_arms.py:fold_and_compile "
        f"with `{other}` and the tree's compiles with `{COMMAND}`: run tools/s4/rebuild_image.py"]


def test_the_gate_s_falsifications_include_a_compile_command(monkeypatch, tmp_path):
    """check_run_reach.py's falsifications run on every pass; one holds the stamp's
    compile commands to the tree's, so a check 3 that stopped comparing them survives it."""
    receipt = tmp_path / "reach.json"
    live = _manifest({})
    receipt.write_text(json.dumps({"id": "s4-run-reach-v1", "manifest": live, "reach": {
        "iterations": 10 ** 9, "import_calls": 1, "frames_presented": 1,
        "import_table": {"path": "p", "sha256": "s"}}}))
    monkeypatch.setattr(CRR, "RECEIPT", receipt)
    monkeypatch.setattr(RR, "manifest", lambda: live)
    monkeypatch.setattr(CRR, "IMPORT_STATUS", tmp_path / "import-status.json")
    (tmp_path / "import-status.json").write_text(json.dumps({"source": "p", "source_sha256": "s"}))
    assert CRR.falsifications() == [] and CRR.RAN == 18
    files_only = RI.fold_code_problems
    monkeypatch.setattr(RI, "fold_code_problems", lambda stamped, now: files_only(
        {k: v for k, v in stamped.items() if k not in RI.COMPILE_STEPS},
        {k: v for k, v in now.items() if k not in RI.COMPILE_STEPS}))
    assert CRR.falsifications() == [
        "falsification survived: the objects were compiled with a command that is not the "
        "tree's -- the gate accepted a receipt it should have called stale"]
