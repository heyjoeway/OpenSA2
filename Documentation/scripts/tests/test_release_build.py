"""tools/s7/release_build.py's f80 mode: the PC releases link the double build by default (the
user's decision of 2026-10-12, Documentation/decisions/2026-10-12-S7-pc-double-default.md) and the
exact native-x87 game is one switch away (`--f80 native`, or `--win-objects` on Windows); a
missing, stale or other-mode double build is refused with the perf_f80.py command that rebuilds
it, and so is one whose pass or shims inputs (the summary's `deps`) have changed since; a build
from before `deps` passes and names what it leaves unchecked; step_game links the build main
checked without checking it again; README.txt and release_zip.py say which build a release holds.

Each case runs release_build.main with its generated tree (GEN, F80_OBJECTS, WIN_OBJECTS) moved
under tmp_path and the two game steps replaced by recorders of what main hands them, or
f80_build or step_game alone with what they call stood in for, so nothing is compiled and nothing
under private/ is read or written. The f80 builds are synthetic: one module, its IR a file of
tmp_path, the pass and shims sources and their `deps` this checkout's own files.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


RB = load_module(ROOT / "tools" / "s7" / "release_build.py", "release_build_f80_under_test")
RZ = load_module(ROOT / "tools" / "s7" / "release_zip.py", "release_zip_f80_under_test")
TRIPLES = {"x86_64-linux": "x86_64-linux-musl", "x86_64-windows": "x86_64-windows-gnu"}
PASSES = {"double": ("tools/s8/f80_double.py", "tools/s8/f80_shims_double.c"),
          "exact": ("tools/s7/f80_legalize.py", "tools/s7/f80_shims.c")}
# What perf_f80.py `deps` records for each mode (Documentation/scripts/tests/test_perf_f80.py
# holds the derivation to these).
DEPS = {"double": ("tools/s8/f80_double.py", "tools/s7/f80_legalize.py", "tools/s8/f80_shims_double.c",
                   "tools/s7/f80_shims.c"),
        "exact": ("tools/s7/f80_legalize.py", "tools/s7/f80_shims.c")}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def f80_build_at(d: Path, triple: str, mode: str = "double", deps: dict | None = None,
                 with_deps: bool = True) -> Path:
    """A perf_f80.py build of one module at `d`, as its summary.json and build.jsonl lay it out:
    its `deps` this checkout's files (DEPS) updated by `deps`, or none (`with_deps` False, a
    build from before perf_f80.py recorded them)."""
    (d / "map").mkdir(parents=True)
    ir = d / "ir_00401000.ll"
    ir.write_text("; lifted\n")
    (d / "map" / "ir_00401000.o").write_bytes(b"obj")
    (d / "f80_shims.o").write_bytes(b"shims")
    (d / "build.jsonl").write_text(json.dumps({"object": "map/ir_00401000.o", "population": "map",
                                               "compiled": True, "ir": str(ir), "ir_sha256": sha(ir)}) + "\n")
    pass_, shims = PASSES[mode]
    summary = {
        "target": triple, "legalized": True, "f80_mode": mode, "modules": 1, "compiled": 1,
        "census": {"ok": True, "disallowed": {}}, "pass": pass_, "pass_sha256": sha(ROOT / pass_),
        "shims": {"source": shims, "source_sha256": sha(ROOT / shims),
                  "object_sha256": sha(d / "f80_shims.o")}}
    if with_deps:
        summary["deps"] = {p: sha(ROOT / p) for p in DEPS[mode]} | (deps or {})
    (d / "summary.json").write_text(json.dumps(summary))
    return d


@pytest.fixture
def rb(tmp_path, monkeypatch):
    """release_build with its generated tree under tmp_path and the game steps recorded."""
    gen = tmp_path / "gen"
    monkeypatch.setattr(RB, "GEN", gen)
    monkeypatch.setattr(RB, "F80_OBJECTS", {"x86_64-linux": gen / "s8/pc-double/linux-obj",
                                            "x86_64-windows": gen / "s8/pc-double/windows-obj"})
    native_win = gen / "s7/targets/x86_64-windows-gnu"
    native_win.mkdir(parents=True)
    (native_win / "build.jsonl").write_text("{}\n")
    monkeypatch.setattr(RB, "WIN_OBJECTS", (native_win,))
    calls: list[dict] = []

    def step_game(src, work, out, f80=None, f80_note=None):
        calls.append({"target": "x86_64-linux", "objects": f80, "f80": f80_note})
        return {}

    def step_game_windows(src, work, out, objects, f80_note=None):
        calls.append({"target": "x86_64-windows", "objects": objects, "f80": f80_note})
        return {}

    monkeypatch.setattr(RB, "step_game", step_game)
    monkeypatch.setattr(RB, "step_game_windows", step_game_windows)

    def main(*args: str) -> int:
        return RB.main(list(args) + ["--only", "game", "--out", str(gen / "release"),
                                     "--work", str(gen / "work")])
    return {"main": main, "calls": calls, "gen": gen, "native_win": native_win}


@pytest.mark.parametrize("target", list(TRIPLES))
def test_the_pc_releases_link_the_double_build_by_default(rb, target, capsys):
    d = f80_build_at(RB.F80_OBJECTS[target], TRIPLES[target])
    assert rb["main"]("--target", target) == 0
    [call] = rb["calls"]
    assert call["target"] == target and call["objects"] == d.resolve()
    assert (call["f80"]["mode"], call["f80"]["triple"], call["f80"]["objects"]) == \
        ("double", TRIPLES[target], str(d.resolve()))
    assert call["f80"]["pass"] == "tools/s8/f80_double.py"
    assert call["f80"]["shims_source"] == "tools/s8/f80_shims_double.c"
    assert f"== f80: the double build {d.resolve()}\n" in capsys.readouterr().out


def test_f80_native_builds_the_native_x87_game_as_before(rb, tmp_path, capsys):
    # No double build exists anywhere: the native-x87 game needs none.
    assert rb["main"]("--f80", "native") == 0
    assert rb["main"]("--target", "x86_64-windows", "--f80", "native") == 0
    other = tmp_path / "other-win-objects"
    other.mkdir()
    (other / "build.jsonl").write_text("{}\n")
    assert rb["main"]("--target", "x86_64-windows", "--win-objects", str(other)) == 0
    assert rb["calls"] == [{"target": "x86_64-linux", "objects": None, "f80": None},
                           {"target": "x86_64-windows", "objects": rb["native_win"], "f80": None},
                           {"target": "x86_64-windows", "objects": other, "f80": None}]
    assert capsys.readouterr().out.count("== f80: native-x87\n") == 3


def test_a_missing_stale_or_other_mode_double_build_is_refused_with_its_rebuild_command(rb, tmp_path):
    d = RB.F80_OBJECTS["x86_64-linux"].resolve()
    cmd = ("source tools/env.sh; tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build "
           f"--target x86_64-linux-musl --mode double --out {d} --jobs 4")
    with pytest.raises(SystemExit) as e:
        rb["main"]()
    assert str(e.value) == (f"release_build: {d} holds no f80 build (summary.json, build.jsonl, "
                            f"f80_shims.o); build it: {cmd}")
    f80_build_at(d, "x86_64-linux-musl")
    (d / "ir_00401000.ll").write_text("; lifted again\n")
    with pytest.raises(SystemExit) as e:
        rb["main"]()
    assert str(e.value) == (f"release_build: the f80 build {d}: 1 of its modules were compiled from IR "
                            f"that has changed or gone since: ['map/ir_00401000.o']; rebuild it: {cmd}")
    shutil.rmtree(d)
    f80_build_at(d, "x86_64-linux-musl", "exact")
    with pytest.raises(SystemExit) as e:
        rb["main"]()
    assert str(e.value) == f"release_build: the f80 build {d}: it is the 'exact' mode, not double; rebuild it: {cmd}"
    assert rb["calls"] == []
    # --f80-objects still names another build, in any legalised mode; with --f80 double, double only.
    exact = f80_build_at(tmp_path / "exact", "x86_64-linux-musl", "exact")
    assert rb["main"]("--f80-objects", str(exact)) == 0
    assert rb["calls"][-1]["f80"]["mode"] == "exact"
    with pytest.raises(SystemExit, match="it is the 'exact' mode, not double; rebuild it: .*--mode double"):
        rb["main"]("--f80", "double", "--f80-objects", str(exact))


def test_every_input_the_summary_records_is_checked(tmp_path, capsys):
    d = f80_build_at(tmp_path / "dbl", "x86_64-linux-musl").resolve()
    note = RB.f80_build(d, "x86_64-linux-musl", "double")
    assert (note["mode"], note["pass"], note["shims_source"]) == \
        ("double", "tools/s8/f80_double.py", "tools/s8/f80_shims_double.c")
    assert capsys.readouterr().out == ""
    # An input the pass imports, and one gone since: each refused with its path and both hashes.
    shutil.rmtree(d)
    built = "0" * 64
    f80_build_at(d, "x86_64-linux-musl", deps={"tools/s7/f80_legalize.py": built,
                                               "tools/s7/f80_gone_since.py": built})
    with pytest.raises(SystemExit) as e:
        RB.f80_build(d, "x86_64-linux-musl", "double")
    assert str(e.value) == (
        f"release_build: the f80 build {d}: its input tools/s7/f80_gone_since.py (built from {built}) "
        f"is gone from this checkout; its input tools/s7/f80_legalize.py is "
        f"{sha(ROOT / 'tools/s7/f80_legalize.py')} here, not the {built} it was built from; rebuild it: "
        "source tools/env.sh; tools/slot.sh .venv/bin/python tools/s8/perf_f80.py build "
        f"--target x86_64-linux-musl --mode double --out {d} --jobs 4")
    # The file the shims include, through main: refused before any step.
    shutil.rmtree(d)
    f80_build_at(d, "x86_64-linux-musl", deps={"tools/s7/f80_shims.c": built})
    with pytest.raises(SystemExit, match=r"its input tools/s7/f80_shims\.c is [0-9a-f]{64} here, not the 0{64}"):
        RB.f80_build(d, "x86_64-linux-musl")


def test_a_build_from_before_deps_passes_and_names_what_it_leaves_unchecked(tmp_path, capsys):
    d = f80_build_at(tmp_path / "dbl", "x86_64-linux-musl", with_deps=False).resolve()
    assert RB.f80_build(d, "x86_64-linux-musl", "double")["mode"] == "double"
    assert capsys.readouterr().out == (
        f"release_build: the f80 build {d} records no deps (built before perf_f80.py recorded them); "
        "unchecked: tools/s7/f80_legalize.py, tools/s7/f80_shims.c\n")
    # The exact mode's pass imports nothing of this checkout and its shims include nothing: no line.
    e = f80_build_at(tmp_path / "exact", "x86_64-linux-musl", "exact", with_deps=False).resolve()
    assert RB.f80_build(e, "x86_64-linux-musl")["mode"] == "exact"
    assert capsys.readouterr().out == ""


def test_step_game_links_the_build_main_checked_without_checking_it_again(tmp_path, monkeypatch, capsys):
    """step_game with main's f80_build note links that build and records that note, with no second
    check; handed none, it makes the check itself, once (its docstring)."""
    gen = tmp_path / "gen"
    (gen / "s3" / "link").mkdir(parents=True)
    monkeypatch.setattr(RB, "GEN", gen)
    l4 = SimpleNamespace(C=SimpleNamespace(BUILD=None))
    links = []

    def phase_compile(state, *args):
        state["compile_ok"] = True

    def phase_link(state, game, tag, _hooks, extra, objects, wraps, overlay):
        state["link"] = {"rc": 0, "sha256": "linked"}
        links.append((l4.C.BUILD, list(objects)))
        binary = l4.OUT / "mains" / game / "sa2_main"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"game")

    l4.phase_compile, l4.phase_link = phase_compile, phase_link
    modules = {"TR": SimpleNamespace(WRAP="wrap"), "PR": SimpleNamespace(RECORDER="rec.c", WRAPS=()),
               "RR": SimpleNamespace(MAIN="main.c", PROBE_ARGS=()), "L4": l4}
    checks, linkdirs = [], []
    monkeypatch.setattr(RB, "source_modules", lambda src: modules)
    monkeypatch.setattr(RB, "state_words", lambda src: {})
    monkeypatch.setattr(RB, "observer_source", lambda src, words: "/* observer */\n")
    monkeypatch.setattr(RB, "link_inputs", lambda: ([], [Path("ir_00402000.o")]))
    monkeypatch.setattr(RB, "f80_linkdir", lambda root, work, staged: (
        linkdirs.append((root, staged)) or (tmp_path / "f80-lifted", [root / "extra.o", root / "f80_shims.o"])))
    monkeypatch.setattr(RB, "f80_build", lambda root, triple, mode=None: (
        checks.append((root, triple, mode)) or {"mode": "double", "objects": "checked here"}))
    monkeypatch.setattr(RB, "run", lambda *a, **k: None)
    monkeypatch.setattr(RB, "env_lines", lambda m: [])
    f80 = tmp_path / "dbl"
    given = {"mode": "double", "objects": "checked by main"}
    for name in ("work", "work2"):
        (tmp_path / name / "overlay").mkdir(parents=True)
        (tmp_path / name / "overlay" / "sa2_exe_overlay_table.c").write_text("")
    note = RB.step_game(tmp_path / "src", tmp_path / "work", tmp_path / "out", f80, given)
    assert checks == []
    assert note["f80"] is given
    assert linkdirs == [(f80, [Path("ir_00402000.o")])]
    assert links == [(tmp_path / "f80-lifted", [f80 / "extra.o", f80 / "f80_shims.o"])]
    assert "  lifted: the double f80 build checked by main\n" in capsys.readouterr().out
    note = RB.step_game(tmp_path / "src", tmp_path / "work2", tmp_path / "out2", f80)
    assert checks == [(f80, "x86_64-linux-musl", None)]
    assert note["f80"] == {"mode": "double", "objects": "checked here"}


@pytest.mark.parametrize("args, refusal", [
    (("--f80", "native", "--f80-objects", "x"), "--f80 native links the native-x87 objects; drop --f80-objects"),
    (("--target", "x86_64-windows", "--f80", "double", "--win-objects", "w"),
     "--win-objects names native-x87 objects, not a double build"),
    (("--win-objects", "w"), "the Linux game's are the staged ones (--f80 native)"),
    (("--target", "x86_64-windows", "--f80-objects", "x", "--win-objects", "w"), "drop --win-objects"),
])
def test_contradictory_f80_flags_are_refused(rb, args, refusal):
    with pytest.raises(SystemExit) as e:
        rb["main"](*args)
    assert refusal in str(e.value)
    assert rb["calls"] == []


def test_the_readme_and_the_zip_say_which_build_the_release_holds(tmp_path):
    double_notes = {"game": {"sha256": "a", "f80": {"mode": "double"}}}
    native_notes = {"game": {"sha256": "b"}}
    double = RB.player_readme("x86_64-windows", RB.game_f80_mode(double_notes))
    native = RB.player_readme("x86_64-linux", RB.game_f80_mode(native_notes))
    assert ("This build\n  The game here is the double build: its x87 floating point runs in 64-bit "
            "doubles at\n  the precision the game sets for itself, about twice as fast as the exact "
            "build.\n") in double
    assert ("This build\n  The game here is the exact build: its x87 floating point runs in the "
            "processor's\n  own 80-bit registers, as the original game's does.\n") in native
    assert "double" not in native
    # The desktop's one place for a tweak: a line in share/game.env (runtime/sa2_tweaks.c).
    assert "  share\\game.env:\n    SA2_TWEAKS=no-event-bars " in double
    assert "  share/game.env:\n    SA2_TWEAKS=no-event-bars " in native
    for notes, mode in ((double_notes, "double"), (native_notes, "native-x87")):
        rel = tmp_path / mode
        rel.mkdir()
        (rel / "release.manifest").write_text("# " + json.dumps({"target": "x86_64-windows", **notes}) + "\n")
        assert RZ.f80_mode(rel) == mode
