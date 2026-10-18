"""tools/s7/win_link.py and tools/s7/wine_run.py: the x86-64 Windows link and its Wine runner.

The link line: zig's printed lld-link line has the probe's object replaced by the build's
objects, its outputs by the build's, and a `-wrap:` for each wrapped symbol before them; a
line it cannot read refuses. The objects: the cross build's map and arm objects must be the
staged link directory's names, every one a compiled record, its extras in the link's order.
A toy linked through the same functions with the pinned zig links only with the wrap (the
negative control leaves `__real_sub_427ee0` undefined, which is how zig's own driver fails).
The manifest: a toy linked with win_link's resource carries the UTF-8 active-code-page
manifest as RT_MANIFEST 1, byte for byte, and one linked without it carries none.
The runner: tick_replay's settings with the outputs in the run's directory, Steam's tree bound
read-only under bwrap, no display variable, and the verdict fields compared.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
import win_link as WL  # noqa: E402
import wine_run as WR  # noqa: E402

PROBE = Path("/w/probe/probe.o")
LINE = ("lld-link -lldmingw -ERRORLIMIT:0 -NOLOGO -DEBUG -PDB:probe.pdb -PDBALTPATH:probe.pdb "
        "-STACK:16777216 -MACHINE:X64 -BREPRO -OUT:probe.exe -IMPLIB:./probe.lib "
        "/w/probe/probe.o -ALTERNATENAME:__image_base__=__ImageBase /z/crt2.obj "
        "/z/libmingw32.lib /z/c++.lib /z/winpthreads.lib kernel32.lib")


# ------------------------------------------------------------------ the link line

def test_link_line_replaces_the_probe_and_its_outputs():
    args = WL.parse_line(LINE, PROBE)
    out = WL.link_line(args, PROBE, [Path("/b/obj/a.o"), Path("/b/ir_00401010.o")],
                       Path("/b/sa2_main.exe"), ["sub_427ee0", "sub_433ee0"])
    assert out == [
        "-lldmingw", "-ERRORLIMIT:0", "-NOLOGO", "-DEBUG", "-PDB:/b/sa2_main.pdb",
        "-PDBALTPATH:sa2_main.pdb", "-STACK:16777216", "-MACHINE:X64", "-BREPRO",
        "-OUT:/b/sa2_main.exe", "-IMPLIB:/b/sa2_main.lib",
        "-wrap:sub_427ee0", "-wrap:sub_433ee0", "/b/obj/a.o", "/b/ir_00401010.o",
        "-ALTERNATENAME:__image_base__=__ImageBase", "/z/crt2.obj", "/z/libmingw32.lib",
        "/z/c++.lib", "/z/winpthreads.lib", "kernel32.lib"]


def test_response_file_one_argument_a_line_quoting_spaces():
    assert WL.response_file(["-OUT:/a b/x.exe", "/c/d.o"]) == '"-OUT:/a b/x.exe"\n/c/d.o\n'
    with pytest.raises(SystemExit, match="double quote"):
        WL.response_file(['-OUT:"x"'])


@pytest.mark.parametrize("line,why", [
    ("ld.lld -m elf_x86_64 /w/probe/probe.o -o probe", "not an lld-link line"),
    (LINE.replace("-lldmingw ", ""), "no -lldmingw"),
    (LINE.replace("-MACHINE:X64 ", ""), "no -MACHINE:X64"),
    (LINE.replace("/w/probe/probe.o ", ""), "appears 0 times"),
    (LINE.replace("/w/probe/probe.o ", "/w/probe/probe.o /w/probe/probe.o "), "appears 2 times"),
    (LINE.replace("-OUT:probe.exe ", ""), "-OUT: other than once"),
    (LINE.replace("/z/c++.lib", '"/z/c++.lib"'), "quote"),
])
def test_a_line_it_cannot_read_refuses(line, why):
    with pytest.raises(SystemExit, match=why):
        WL.parse_line(line, PROBE)


# ------------------------------------------------------------------ the objects

def cross_build(tmp: Path, *, target=WL.TRIPLE, failed=(), stray=(), staged=None,
                extras=("ir_00900000.o", "ir_00900100.o")):
    """A cross build of two map objects, one arm, two extras, and the staged directory. Each
    record names its IR (an absolute path under tmp/ir) and that IR's sha256."""
    root = tmp / "xb"
    (tmp / "ir").mkdir()
    recs = []
    for pop, names in (("map", ["ir_00401010.o", "ir_00402000.o"]), ("arms", ["ir_00401500.o"]),
                       ("extra", list(extras))):
        (root / pop).mkdir(parents=True)
        for n in names:
            (root / pop / n).write_bytes(b"obj")
            ir = tmp / "ir" / f"{pop}_{n}.ll"
            ir.write_text(f"; {pop} {n}\n")
            recs.append({"population": pop, "object": f"{pop}/{n}", "compiled": n not in failed,
                         "ir": str(ir), "ir_sha256": WL.sha256(ir)})
        for n in stray:
            if n.startswith(pop + "/"):
                (root / n).write_bytes(b"stray")
    (root / "summary.json").write_text(json.dumps({"target": target, "modules": len(recs),
                                                   "compiled": len(recs)}))
    (root / "build.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    st = tmp / "staged"
    st.mkdir()
    for n in staged if staged is not None else ["ir_00401010.o", "ir_00402000.o", "ir_00401500.o"]:
        (st / n).write_bytes(b"x86")
    return root, st


@pytest.fixture
def extra_state(monkeypatch):
    state = {"objects": ["/l/ir_00900100.o", "/l/ir_00900000.o"]}
    monkeypatch.setattr(WL, "extra_objects_state", lambda tag: state)
    return state


def test_lifted_objects_in_the_link_order(tmp_path, extra_state):
    root, st = cross_build(tmp_path)
    extras, lifted, ident = WL.lifted_objects(root, WL.TAG, st)
    assert [p.relative_to(root).as_posix() for p in extras] == ["extra/ir_00900100.o",
                                                                "extra/ir_00900000.o"]
    assert [p.relative_to(root).as_posix() for p in lifted] == [
        "map/ir_00401010.o", "arms/ir_00401500.o", "map/ir_00402000.o"]
    assert ident["target"] == "x86_64-windows-gnu" and ident["modules"] == 5
    assert ident["ir_checked"] == 5


@pytest.mark.parametrize("kw,why", [
    ({"target": "aarch64-linux-musl"}, "not x86_64-windows-gnu"),
    ({"failed": ("ir_00402000.o",)}, "did not compile"),
    ({"stray": ("map/ir_00403000.o",)}, "does not name"),
    ({"staged": ["ir_00401010.o", "ir_00402000.o"]}, "not the staged link directory's"),
    ({"extras": ("ir_00900000.o",)}, "no cross-built object"),
    ({"extras": ("ir_00900000.o", "ir_00900100.o", "ir_00900200.o")}, "does not list"),
])
def test_lifted_objects_refuse(tmp_path, extra_state, kw, why):
    root, st = cross_build(tmp_path, **kw)
    with pytest.raises(SystemExit, match=why):
        WL.lifted_objects(root, WL.TAG, st)


@pytest.mark.parametrize("change", ["edit", "remove", "unhashed"])
def test_lifted_objects_refuse_ir_changed_since_the_cross_build(tmp_path, extra_state, change):
    root, st = cross_build(tmp_path)
    ir = tmp_path / "ir" / "arms_ir_00401500.o.ll"
    if change == "edit":
        ir.write_text("; lifted again\n")
    elif change == "remove":
        ir.unlink()
    else:
        log = root / "build.jsonl"
        recs = [json.loads(x) for x in log.read_text().splitlines()]
        for r in recs:
            if r["object"] == "arms/ir_00401500.o":
                del r["ir_sha256"]
        log.write_text("".join(json.dumps(r) + "\n" for r in recs))
    with pytest.raises(SystemExit, match=r"1 modules .* changed or gone since .*arms/ir_00401500\.o"):
        WL.lifted_objects(root, WL.TAG, st)


def test_the_compile_flags_are_link_s4s_with_the_windows_triple():
    c = WL.flags(False, Path("/src"))
    assert c[1:] == ["cc", "-target", "x86_64-windows-gnu", "-mno-ms-bitfields", "-O2", "-w", "-c",
                     "-I", "/src/runtime", "-I", str(WL.C.REMILL_INC)]
    assert WL.flags(True, Path("/src"))[1] == "c++" and "-std=c++17" in WL.flags(True, Path("/src"))


# ------------------------------------------------------------------ a toy link with zig

TOY_LIFTED = "int sub_427ee0(int x) { return x + 1; }\nint caller(int x) { return sub_427ee0(x) * 2; }\n"
TOY_HOOK = ("int __real_sub_427ee0(int);\nint __wrap_sub_427ee0(int x) { return __real_sub_427ee0(x) + 100; }\n"
            "int caller(int);\nint main(void) { return caller(1) == 204 ? 0 : 1; }\n")


def test_a_toy_links_with_the_wrap_and_not_without(tmp_path):
    assert WL.S.ZIG.is_file(), f"the pinned zig is not at {WL.S.ZIG}"
    objs = []
    for name, text in (("lifted", TOY_LIFTED), ("hook", TOY_HOOK)):
        src = tmp_path / f"{name}.c"
        src.write_text(text)
        o = tmp_path / f"{name}.o"
        p = subprocess.run([str(WL.S.ZIG), "cc", "-target", WL.TRIPLE, "-O2", "-c", str(src),
                            "-o", str(o)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        objs.append(o)
    probe = WL.probe_line(tmp_path / "probe")
    results = {}
    for wraps in (["sub_427ee0"], []):
        exe = tmp_path / f"toy{len(wraps)}.exe"
        rsp = tmp_path / f"toy{len(wraps)}.rsp"
        rsp.write_text(WL.response_file(WL.link_line(probe, tmp_path / "probe" / "probe.o",
                                                     objs, exe, wraps)))
        p = subprocess.run([str(WL.S.ZIG), "lld-link", f"@{rsp}"], capture_output=True,
                           text=True, cwd=str(tmp_path))
        results[len(wraps)] = (p.returncode, p.stderr + p.stdout, exe.is_file())
    assert results[1][0] == 0 and results[1][2], results[1][1]
    assert results[0][0] != 0 and "undefined symbol: __real_sub_427ee0" in results[0][1]
    assert open(tmp_path / "toy1.exe", "rb").read(2) == b"MZ"


def manifests(exe: Path) -> dict[int, bytes]:
    """The RT_MANIFEST resources of a PE, by id (each one's first language)."""
    import pefile
    pe = pefile.PE(str(exe))
    out: dict[int, bytes] = {}
    for kind in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", types.SimpleNamespace(entries=[])).entries:
        if kind.id != 24:
            continue
        for ident in kind.directory.entries:
            d = ident.directory.entries[0].data.struct
            out[ident.id] = pe.get_data(d.OffsetToData, d.Size)
    return out


def test_the_link_carries_the_utf8_code_page_manifest_and_a_link_without_it_none(tmp_path):
    assert WL.S.ZIG.is_file(), f"the pinned zig is not at {WL.S.ZIG}"
    src, obj = tmp_path / "main.c", tmp_path / "main.o"
    src.write_text("int main(void) { return 0; }\n")
    p = subprocess.run([str(WL.S.ZIG), "cc", "-target", WL.TRIPLE, "-O2", "-c", str(src), "-o",
                        str(obj)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    res = WL.manifest_resource(tmp_path / "res", "toy")
    assert Path(res["rc"]).read_text() == f'1 24 "{tmp_path / "res" / "toy.manifest"}"\n'
    probe = WL.probe_line(tmp_path / "probe")
    got = {}
    for name, extra in (("with", [Path(res["resource"])]), ("without", [])):
        exe = tmp_path / f"{name}.exe"
        rsp = tmp_path / f"{name}.rsp"
        rsp.write_text(WL.response_file(WL.link_line(probe, tmp_path / "probe" / "probe.o",
                                                     [obj] + extra, exe, [])))
        p = subprocess.run([str(WL.S.ZIG), "lld-link", f"@{rsp}"], capture_output=True,
                           text=True, cwd=str(tmp_path))
        assert p.returncode == 0, p.stderr + p.stdout
        got[name] = manifests(exe)
    assert got["with"] == {1: WL.MANIFEST.encode()}
    assert (b'<activeCodePage xmlns="http://schemas.microsoft.com/SMI/2019/WindowsSettings">'
            b'UTF-8</activeCodePage>') in got["with"][1]
    assert got["without"] == {}


# ------------------------------------------------------------------ the runner

def fake_modules():
    TR = types.SimpleNamespace(LIFTED_WORDS="lifted-words.bin",
                               DP=types.SimpleNamespace(Refusal=RuntimeError))
    RR = types.SimpleNamespace(ENV={"SA2_D3D9_DEVICE": "1", "SA2_PUMP_POLLS": "1300"},
                               profile_env=lambda p: {"SA2_DEVICE_PROFILE": f"{p}.txt"})
    LR = types.SimpleNamespace(BASE_ENV={"SA2_INSTALL": "private/install",
                                         "SA2_VFS_WRITE_ROOT": "private/generated/s4/vfsroot"})
    return TR, RR, LR


def test_replay_env_is_run_lifteds_with_the_outputs_in_the_run_directory():
    TR, RR, LR = fake_modules()
    out = WR.ROOT / "private" / "generated" / "s7" / "win" / "run"
    env, prof = WR.replay_env(TR, RR, LR, {"name": "boot", "word_policy": {}}, out, "cap")
    assert env == {"SA2_INSTALL": "private/install",
                   "SA2_VFS_WRITE_ROOT": "private/generated/s7/win/run/vfs",
                   "SA2_D3D9_DEVICE": "1", "SA2_PUMP_POLLS": "1300",
                   "SA2_REPLAY_TICKS": "private/generated/s7/win/run/lifted.csv",
                   "SA2_REPLAY_WORDS": "private/generated/s7/win/run/lifted-words.bin",
                   "SA2_DEVICE_PROFILE": "cap.txt"}
    assert prof == {"SA2_DEVICE_PROFILE": "cap.txt"}
    env, _ = WR.replay_env(TR, RR, LR, {"name": "x"}, out, "cap")
    assert "SA2_REPLAY_WORDS" not in env


def test_wine_runs_headless_inside_bwrap_with_steam_read_only():
    prefix = WR.ROOT / "private" / "generated" / "s7" / "wine" / "prefix"
    argv = WR.wrapped(["wine", "game.exe"], prefix)
    steam = str(WR.STEAM)
    assert argv[:8] == [WR.BWRAP, "--dev-bind", "/", "/", "--ro-bind", steam, steam,
                        "--die-with-parent"]
    assert argv[argv.index("--chdir") + 1] == str(WR.ROOT) and argv[-3:] == ["--", "wine", "game.exe"]
    env = WR.wine_environ({"DISPLAY": ":0", "WAYLAND_DISPLAY": "w", "HOME": "/h"}, prefix)
    assert env == {"HOME": "/h", "WINEDEBUG": "-all", "WINEPREFIX": str(prefix),
                   "WINEDLLOVERRIDES": "mscoree,mshtml=;winemenubuilder.exe=d"}


def test_verdict_reads_the_divergence_the_class_and_each_group():
    result = {"first_divergence": None, "class": "residue", "judged_ticks": 1200,
              "residues": {"groups": [{"record": "r1", "group": "g1", "rule": {"held": True},
                                       "lifted": {"differing": 3}}]}}
    assert WR.verdict(result) == {"first_divergence": None, "class": "residue",
                                  "judged_ticks": 1200,
                                  "groups": [{"record": "r1", "group": "g1", "held": True,
                                              "differing": 3}]}
    moved = json.loads(json.dumps(result))
    moved["residues"]["groups"][0]["lifted"]["differing"] = 4
    assert WR.verdict(moved) != WR.verdict(result)


def test_registered_refuses_a_capture_off_its_pin(tmp_path, monkeypatch):
    cap = tmp_path / "lifted.csv"
    cap.write_text("tick,a\n1,2\n")
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"commit": "c", "replays": [
        {"name": "boot", "lifted": {"capture": str(cap), "sha256": WR.sha256(cap)}, "result": {}}]}))
    TR = types.SimpleNamespace(RECEIPT=receipt)
    monkeypatch.setattr(WR, "ROOT", tmp_path)
    receipt_rel = receipt.relative_to(tmp_path)
    assert WR.registered(TR, "boot")["receipt"] == str(receipt_rel)
    cap.write_text("tick,a\n1,3\n")
    with pytest.raises(SystemExit, match="does not hash"):
        WR.registered(TR, "boot")
    with pytest.raises(SystemExit, match="0 entries named metalharbor"):
        WR.registered(TR, "metalharbor")


def test_sources_refuse_a_runtime_without_the_windows_layer(tmp_path, extra_state):
    (tmp_path / "runtime").mkdir()
    with pytest.raises(SystemExit, match="has no sa2_win32.c, the Windows host's runtime layer"):
        WL.sources(tmp_path, WL.TAG, WL.MAIN, WL.runner_links("tick")[0], None)


def test_the_run_record_must_say_the_executable_asked_for():
    assert WR.exe_problems({"exe": {"kind": "shipped"}}, "shipped") == []
    assert WR.exe_problems({"exe": {"kind": "unpacked"}}, "unpacked") == []
    assert WR.exe_problems({"exe": {"kind": "unpacked"}}, "shipped") == [
        "asked for the shipped executable; the run record says the loader mapped 'unpacked'"]
    assert WR.exe_problems({}, "unpacked") == [
        "the run record has no `exe` block, so which executable it loaded is not known "
        "(asked for the unpacked one)"]


# ------------------------------------------------------------------ the runners' links

def test_the_runners_hooks_and_wraps_are_read_from_their_files():
    assert WL.runner_links("tick") == (["harness/replay/wrap_tick_probe.c"], ["sub_433ee0"])
    hooks, wraps = WL.runner_links("stage")
    assert hooks == ["harness/replay/wrap_tick_probe.c", "harness/replay/wrap_test_spawn.c",
                     "harness/replay/wrap_input_replay.c", "harness/stage_goal_probe.c"]
    assert wraps == ["sub_433ee0", "sub_77dd10", "sub_4436a0", "sub_426760", "sub_77e780",
                     "sub_7a8948"]


def test_literal_refuses_a_name_the_file_does_not_assign(tmp_path):
    f = tmp_path / "m.py"
    f.write_text("HOOK = 'a.c'\nWRAP = some_call()\n")
    assert WL.literal(f, "HOOK") == "a.c"
    with pytest.raises(SystemExit, match="has no literal WRAPS"):
        WL.literal(f, "WRAPS")


def link_record(exe: Path, hooks, wrapped, sha=None):
    exe.write_bytes(b"MZ binary")
    (exe.parent / "link.record").write_text(json.dumps({
        "hooks": hooks, "wrapped": wrapped, "link": {"sha256": sha or WR.sha256(exe)}}))


def test_linked_for_holds_the_binary_to_its_link_record(tmp_path):
    exe = tmp_path / "sa2_main.exe"
    hooks, wraps = ["harness/replay/wrap_tick_probe.c"], ["sub_433ee0"]
    assert WR.linked_for(exe, hooks, wraps, ["sub_427ee0"]) == [
        f"no link.record beside {exe} (tools/s7/win_link.py writes it), so what the binary "
        f"links is not known"]
    link_record(exe, hooks, ["sub_427ee0", "sub_433ee0"])
    assert WR.linked_for(exe, hooks, wraps, ["sub_427ee0"]) == []
    stage_hooks = hooks + ["harness/stage_goal_probe.c"]
    assert WR.linked_for(exe, stage_hooks, wraps, ["sub_427ee0"]) == [
        "the binary links the hooks ['harness/replay/wrap_tick_probe.c'], and this replay's "
        "runner links ['harness/replay/wrap_tick_probe.c', 'harness/stage_goal_probe.c']"]
    assert WR.linked_for(exe, hooks, wraps + ["sub_77dd10"], ["sub_427ee0"]) == [
        "the binary wraps ['sub_427ee0', 'sub_433ee0'], and this replay's runner wraps "
        "['sub_427ee0', 'sub_433ee0', 'sub_77dd10']"]
    link_record(exe, hooks, ["sub_427ee0", "sub_433ee0"], sha="0" * 64)
    assert WR.linked_for(exe, hooks, wraps, ["sub_427ee0"]) == [
        "sa2_main.exe is not the binary its link.record names"]


def test_spec_env_is_loop_runs_reading_of_the_run_spec():
    cmd = ["py", "loop_run.py", "--tag", "t", "--run", "ticks|SA2_A=1|SA2_B=x=y|SA2_INSTALL=mine",
           "--hook", "h.c"]
    assert WR.spec_env(cmd, {"SA2_INSTALL": "private/install", "SA2_VFS_WRITE_ROOT": "v"}) == {
        "SA2_INSTALL": "mine", "SA2_VFS_WRITE_ROOT": "v", "SA2_A": "1", "SA2_B": "x=y"}
    with pytest.raises(SystemExit, match="2 --run specs"):
        WR.spec_env(cmd + ["--run", "b|SA2_C=1"], {})
    with pytest.raises(SystemExit, match="is not VAR=VALUE"):
        WR.spec_env(["--run", "ticks|SA2_A"], {})


def test_flag_values_reads_both_forms_in_order():
    cmd = ["--hook", "a.c", "--wrap", "s1", "--hook", "b.c", "--probe-arg=--timeout",
           "--probe-arg=600", "--timeout", "720"]
    assert WR.flag_values(cmd, "--hook") == ["a.c", "b.c"]
    assert WR.flag_values(cmd, "--probe-arg") == ["--timeout", "600"]


def test_stage_setup_is_stage_run_commands_run_with_the_outputs_in_the_run_directory(tmp_path,
                                                                                    monkeypatch):
    monkeypatch.setattr(WR, "ROOT", tmp_path)
    script = tmp_path / "rec.txt"
    script.write_text("recording\n")
    out = tmp_path / "private" / "run"
    seen = {}

    def command(stage, polls, **kw):
        seen.update(kw, stage=stage, polls=polls)
        return (["py", "loop_run.py", "--run",
                 f"ticks|SA2_PUMP_POLLS={polls}|SA2_REPLAY_TICKS=private/run/lifted.csv",
                 "--hook", "a.c", "--hook", "b.c", "--wrap", "sub_1", "--probe-arg=--x",
                 "--probe-arg=--timeout", "--probe-arg=600", "--timeout", "720"],
                tmp_path / "rundir" / "r.json", kw["out_dir"] / "lifted.csv")

    TR = types.SimpleNamespace(LIFTED_WORDS="lifted-words.bin", stage_poll_budget=lambda e: 1200)
    RR = types.SimpleNamespace(profile_env=lambda p: {"SA2_DEVICE_PROFILE": p})
    LR = types.SimpleNamespace(BASE_ENV={"SA2_INSTALL": "private/install",
                                         "SA2_VFS_WRITE_ROOT": "private/generated/s4/vfsroot"})
    SR = types.SimpleNamespace(command=command)
    entry = {"name": "metalharbor", "stage": {"name": "metalharbor"}, "word_policy": {},
             "input": {"script": "rec.txt", "sha256": WR.sha256(script)}}
    s = WR.stage_setup(TR, RR, LR, SR, entry, out, "prof")
    assert s["env"] == {"SA2_INSTALL": "private/install", "SA2_VFS_WRITE_ROOT": "private/run/vfs",
                        "SA2_PUMP_POLLS": "1200",
                        "SA2_REPLAY_TICKS": "private/run/lifted.csv"}
    assert s["probe"] == ["--x", "--timeout", "600", "--dump-imports", "private/run/imports.table"]
    assert (s["hooks"], s["wraps"], s["polls"], s["alarm"]) == (["a.c", "b.c"], ["sub_1"], 1200, 600)
    assert seen["extra_env"] == {"SA2_STAGE_TRACE": "private/run/stage-trace.csv",
                                 "SA2_REPLAY_WORDS": "private/run/lifted-words.bin"}
    assert (seen["stage"], seen["input_script"], seen["out_dir"], seen["observe"]) == (
        "metalharbor", script, out, True)
    script.write_text("another recording\n")
    with pytest.raises(SystemExit, match="does not hash to its pin"):
        WR.stage_setup(TR, RR, LR, SR, entry, out, "prof")


def test_a_stopped_runs_complete_rows_against_the_registered_first_rows():
    want = b"tick,a\n1,5\n2,6\n3,7\n"
    assert WR.prefix_compare(b"tick,a\n1,5\n2,6\n3,", want) == {
        "ticks_compared": 2, "registered_ticks": 3, "prefix_equal": True}
    assert WR.prefix_compare(b"tick,a\n1,5\n2,9\n", want)["prefix_equal"] is False
    assert WR.prefix_compare(b"tick,a\n", want) == {
        "ticks_compared": 0, "registered_ticks": 3, "prefix_equal": False}


# ------------------------------------------------------------------ the comparison's memory bound

def test_bounded_returns_the_result_with_the_memory_it_was_given():
    j = WR.bounded(lambda: {"equal": True, "ticks": [3, 3]}, 0)
    assert j["result"] == {"equal": True, "ticks": [3, 3]}
    assert j["exit"] == 0 and "problem" not in j
    assert j["bound_mb"] > 0 and j["peak_rss_mb"] > 0 and j["available_mb"] > 0


def test_bounded_ends_a_comparison_larger_than_the_bound_in_a_problem(monkeypatch):
    # 2 GB above the headroom: the process may grow by 2 GB, and asks for 4.
    monkeypatch.setattr(WR, "mem_available", lambda: 10 * WR.GB)
    j = WR.bounded(lambda: len(bytearray(4 * WR.GB)), 8 * WR.GB)
    assert "result" not in j and j["exit"] == 3
    assert j["problem"].startswith("MemoryError: the comparison needed more than its bound")
    assert j["available_mb"] == 10 * 1024 and j["headroom_mb"] == 8 * 1024


def test_bounded_does_not_start_without_a_gigabyte_above_the_headroom(monkeypatch):
    monkeypatch.setattr(WR, "mem_available", lambda: 8 * WR.GB + WR.GB // 2)
    monkeypatch.setattr(WR.os, "fork", lambda: pytest.fail("forked without the room to run"))
    j = WR.bounded(lambda: {"x": 1}, 8 * WR.GB)
    assert j == {"available_mb": 8704, "headroom_mb": 8192,
                 "problem": "not started: 8704 MB available, which leaves less than 1 GB above "
                            "the 8192 MB headroom"}


def test_bounded_records_a_refusal_inside_the_comparison():
    def refuse():
        raise SystemExit("tick_replay: the registered log does not parse")
    j = WR.bounded(refuse, 0)
    assert j["exit"] == 4 and "result" not in j
    assert j["problem"] == "SystemExit: tick_replay: the registered log does not parse"


# ------------------------------------------------------------------ the build census

def census_tree(tmp: Path, monkeypatch) -> dict:
    """A tree under tmp, set as win_link's ROOT: a cross build of three modules, a registry of
    boot and metalharbor (a stage replay), a link.record and binary per replay as win_link
    writes them, the replays evidence naming those binaries, host 2's Windows build record and
    player, and a reach manifest."""
    import tick_replay as T
    monkeypatch.setattr(WL, "ROOT", tmp)
    reg = tmp / "Documentation" / "replays.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"replays": [{"name": "boot"},
                                           {"name": "metalharbor", "stage": {"name": "mh"}}]}))
    monkeypatch.setattr(T, "REGISTRY", reg)
    objects = tmp / "private/generated/s7/xb"
    objects.mkdir(parents=True)
    recs = []
    for pop, va in (("map", 0x401010), ("arms", 0x401500), ("extra", 0x900000)):
        ir = tmp / f"private/ir/{pop}_{va:08X}.ll"
        ir.parent.mkdir(parents=True, exist_ok=True)
        ir.write_text(f"; {pop}\n")
        recs.append({"population": pop, "va": f"0x{va:08x}", "object": f"{pop}/ir_{va:08X}.o",
                     "ir": str(ir.relative_to(tmp)), "ir_sha256": WL.sha256(ir),
                     "compiled": True})
    (objects / "build.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    (objects / "summary.json").write_text(json.dumps({"target": WL.TRIPLE}))
    src = tmp / "runtime/x.c"
    src.parent.mkdir(parents=True)
    src.write_text("int x;\n")
    runs = {}
    for name, runner, build in (("boot", "tick", "build-boot"), ("metalharbor", "stage", "build-stage")):
        exe = tmp / "private/generated/s7/win" / build / "sa2_main.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(name.encode())
        hooks, wraps = WL.runner_links(runner)
        rec = {"schema": WL.LINK_SCHEMA, "triple": WL.TRIPLE, "src": str(tmp),
               "objects": {"root": str(objects),
                           "build_jsonl_sha256": WL.sha256(objects / "build.jsonl")},
               "hooks": hooks, "wrapped": list(WL.LS.PRODUCTION_WRAPS) + wraps,
               "flags": {"c": WL.flags(False, tmp)[2:], "cxx": WL.flags(True, tmp)[2:]},
               "manifest": {"manifest_sha256": WL.hashlib.sha256(WL.MANIFEST.encode()).hexdigest()},
               "compile": {"x": {"source": str(src), "source_sha256": WL.sha256(src), "rc": 0}},
               "link": {"rc": 0, "binary": str(exe), "sha256": WL.sha256(exe)}}
        (exe.parent / "link.record").write_text(json.dumps(rec))
        runs[name] = {"binary_sha256": WL.sha256(exe),
                      "run": {"binary": str(exe.relative_to(tmp))}}
    replays = tmp / WL.REPLAYS_TRACKED
    replays.parent.mkdir(parents=True, exist_ok=True)
    replays.write_text(json.dumps({"schema": WL.REPLAYS_SCHEMA, "target": WL.TARGET_NAME,
                                   "replays": runs}))
    host = tmp / "private/generated/s7/host2-win"
    (host / "bin").mkdir(parents=True)
    (host / WL.HOST_PLAYER).write_bytes(b"player")
    (host / WL.HOST_RECORD).write_text(json.dumps({
        "schema": WL.HOST_SCHEMA, "outputs": {WL.HOST_PLAYER: WL.sha256(host / WL.HOST_PLAYER)}}))
    fold = {"tools/fold.py": "aa" * 32, "fold_cmd": "zig cc -target x86_64-linux-musl -c"}
    (tmp / "Documentation/receipts/s4-run-reach-v1.json").write_text(json.dumps({"manifest": {
        "tag": "next+allarms", "exe_sha256": "c9" * 32,
        "generated": {"fold_code": fold, "lifted_extras_sha256": "e1" * 32,
                      "mb_edges_c_sha256": "e2" * 32}}}))
    return {"root": tmp, "objects": objects, "replays": replays, "host": host, "src": src,
            "recs": recs}


def test_the_census_names_each_replays_binary_and_the_host_player(tmp_path, monkeypatch):
    t = census_tree(tmp_path, monkeypatch)
    doc = WL.census(t["objects"], t["replays"], t["host"])
    assert (doc["schema"], doc["generated_by"], doc["target"], doc["triple"]) == (
        "sa2-s7-build-census/v1", "tools/s7/win_link.py", "x86_64-windows", "x86_64-windows-gnu")
    assert doc["link"]["returncode"] == 0
    win = tmp_path / "private/generated/s7/win"
    assert doc["link"]["binaries"] == {"boot": WL.sha256(win / "build-boot/sa2_main.exe"),
                                       "metalharbor": WL.sha256(win / "build-stage/sa2_main.exe"),
                                       "host": WL.sha256(t["host"] / WL.HOST_PLAYER)}
    assert doc["target_passes"] == {}
    assert doc["lifted_inputs"] == {"tag": "next+allarms", "exe_sha256": "c9" * 32,
                                    "fold_code": {"tools/fold.py": "aa" * 32},
                                    "lifted_extras_sha256": "e1" * 32,
                                    "mb_edges_c_sha256": "e2" * 32}
    assert doc["log"] == {"path": "private/generated/s7/xb/build.jsonl",
                          "sha256": WL.sha256(t["objects"] / "build.jsonl"), "modules": 3,
                          "ir_checked": 3}
    assert sorted(doc["link"]["records"]) == ["boot", "host", "metalharbor"]


def rewrite(path: Path, change) -> None:
    doc = json.loads(path.read_text())
    change(doc)
    path.write_text(json.dumps(doc))


@pytest.mark.parametrize(("arrange", "why"), [
    (lambda t: t["src"].write_text("int y;\n"), "runtime/x.c is not the source it compiled"),
    (lambda t: (t["root"] / t["recs"][1]["ir"]).write_text("; moved\n"),
     "were compiled from IR that has changed"),
    (lambda t: rewrite(t["objects"].parent / "win/build-stage/link.record",
                       lambda d: d.update(hooks=["harness/replay/wrap_tick_probe.c"])),
     "the stage runner's hooks or wraps have changed"),
    (lambda t: rewrite(t["objects"].parent / "win/build-boot/link.record",
                       lambda d: d.update(src="/elsewhere")), "not this tree"),
    (lambda t: rewrite(t["objects"].parent / "win/build-boot/link.record",
                       lambda d: d["flags"]["c"].append("-O3")), "the compile flags have changed"),
    (lambda t: rewrite(t["objects"].parent / "win/build-boot/link.record",
                       lambda d: d["objects"].update(build_jsonl_sha256="0" * 64)),
     "linked from another cross build"),
    (lambda t: (t["objects"].parent / "win/build-boot/sa2_main.exe").write_bytes(b"relinked"),
     "its binary is gone or changed"),
    (lambda t: rewrite(t["replays"], lambda d: d["replays"]["boot"].update(binary_sha256="0" * 64)),
     "its binary is not the one"),
    (lambda t: (t["host"] / WL.HOST_PLAYER).write_bytes(b"rebuilt"), "is not the player"),
    (lambda t: rewrite(t["replays"], lambda d: d.update(target="arm64-linux")),
     "is not x86_64-windows's"),
    (lambda t: rewrite(t["objects"] / "summary.json", lambda d: d.update(target="aarch64-linux-musl")),
     "is not a cross build for x86_64-windows-gnu"),
])
def test_the_census_refuses_a_binary_the_tree_would_not_link_now(tmp_path, monkeypatch,
                                                                 arrange, why):
    t = census_tree(tmp_path, monkeypatch)
    arrange(t)
    with pytest.raises(SystemExit, match=why):
        WL.census(t["objects"], t["replays"], t["host"])


def test_the_census_refuses_a_module_that_did_not_compile(tmp_path, monkeypatch):
    t = census_tree(tmp_path, monkeypatch)
    log = t["objects"] / "build.jsonl"
    log.write_text(log.read_text().replace('"compiled": true}', '"compiled": false}', 1))
    with pytest.raises(SystemExit, match="1 of the 3 modules"):
        WL.census(t["objects"], t["replays"], t["host"])


def test_the_tracked_census_names_the_binaries_the_windows_replays_ran():
    """The census in the tree names, for each registered replay, the binary the Windows replays
    evidence ran, and a host; and cites that evidence by its sha256 as it stands."""
    census = json.loads((ROOT / WL.CENSUS_TRACKED).read_text())
    replays = json.loads((ROOT / WL.REPLAYS_TRACKED).read_text())
    assert census["generated_by"] == WL.SELF and census["target"] == WL.TARGET_NAME
    assert {k: v for k, v in census["link"]["binaries"].items() if k != "host"} == {
        name: run["binary_sha256"] for name, run in replays["replays"].items()}
    assert len(census["link"]["binaries"]["host"]) == 64
    assert census["binaries_from"] == {"path": WL.REPLAYS_TRACKED,
                                       "sha256": WL.sha256(ROOT / WL.REPLAYS_TRACKED)}
