"""tools/s8/android_game.py: the game for Android (aarch64-linux-android, PIE).

The compile command: cross_build's population command with the Android triple and -fPIC right
after it, nothing else changed. Why the Android triple: a lifted-style module under register
pressure, compiled with that command, never touches x18 (bionic's shadow-call-stack register),
where the same module for aarch64-linux-musl does. The link inputs: a cross build with a failed
module is refused, and the extras come in the link's order. The device paths: a path under the
checkout moves under the device root, anything else is left alone. The boot variant links the
tick probe's hook and wrap, as tick_replay's lifted run does, and no overlay.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s8"))
import android_game as AG  # noqa: E402

OBJDUMP = AG.NDK_BIN / "llvm-objdump"


def test_compile_command_is_cross_builds_with_the_android_triple_and_pic():
    AG._worker_init()
    cmd = AG.android_compile_command("map", AG.TRIPLE, Path("/i/sub_00401010.f80.ll"),
                                     Path("/o/ir_00401010.o"))
    assert cmd == [str(AG.ZIG), "cc", "-target", "aarch64-linux-android", "-fPIC", "-O2", "-w",
                   "-c", "/i/sub_00401010.f80.ll", "-o", "/o/ir_00401010.o"]
    # The workers' cross_build.one reaches the same command through the patched name.
    import cross_build as XB
    assert XB.compile_command("arms", AG.TRIPLE, Path("/a.ll"), Path("/a.o"))[3:5] == [
        "aarch64-linux-android", "-fPIC"]


# A module shaped like the lifted IR (32-bit x86 datalayout and triple, dso_local externals)
# that keeps 28 values live into a call of 28 arguments: the backend runs out of registers.
PRESSURE = "\n".join(
    ['target datalayout = "e-m:x-p:32:32-i64:64-f80:32-n8:16:32-a:0:32-S32"',
     'target triple = "i386-unknown-windows-msvc-coff"',
     "declare dso_local i64 @sink(" + ", ".join(["i64"] * 28) + ")",
     "define dso_local i64 @f(ptr %p) {"]
    + [f"  %a{i} = load volatile i64, ptr %p" for i in range(28)]
    + ["  %s0 = add i64 %a0, %a1"]
    + [f"  %s{i - 1} = add i64 %s{i - 2}, %a{i}" for i in range(2, 28)]
    + ["  %r = call i64 @sink(" + ", ".join(f"i64 %a{i}" for i in range(27)) + ", i64 %s26)",
       "  ret i64 %r", "}", ""])


def x18_uses(obj: Path) -> int:
    text = subprocess.run([str(OBJDUMP), "-d", "--no-show-raw-insn", str(obj)],
                          capture_output=True, text=True, check=True).stdout
    body = text.split("<f>:", 1)[1]
    return sum(1 for line in body.splitlines() if " x18" in line or " w18" in line
               or ",x18" in line or ",w18" in line)


@pytest.mark.skipif(not AG.ZIG.is_file() or not OBJDUMP.is_file(),
                    reason="the pinned zig or the NDK is not installed")
def test_the_android_triple_keeps_x18_free(tmp_path):
    AG._worker_init()
    ir = tmp_path / "p.ll"
    ir.write_text(PRESSURE)
    uses = {}
    for triple in ("aarch64-linux-android", "aarch64-linux-musl"):
        obj = tmp_path / f"{triple}.o"
        subprocess.run(AG.android_compile_command("map", triple, ir, obj), check=True,
                       capture_output=True)
        uses[triple] = x18_uses(obj)
    assert uses["aarch64-linux-android"] == 0
    assert uses["aarch64-linux-musl"] > 0          # the control: a generic triple allocates it


def fake_build(root: Path, recs: list[dict]) -> Path:
    objects = root / "objects"
    for r in recs:
        o = objects / r["object"]
        o.parent.mkdir(parents=True, exist_ok=True)
        o.write_bytes(b"obj")
    (objects / "build.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    return objects


def test_lifted_inputs_refuse_a_failed_module(tmp_path, monkeypatch):
    import cross_build as XB
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "ir_00401000.o").write_bytes(b"x")
    monkeypatch.setattr(XB, "STAGED", staged)
    objects = fake_build(tmp_path, [
        {"population": "map", "va": "0x00401000", "object": "map/ir_00401000.o",
         "compiled": False}])
    with pytest.raises(SystemExit, match="did not compile"):
        AG.lifted_inputs(objects, tmp_path / "linkdir")


def test_lifted_inputs_link_the_staged_names_and_order_the_extras(tmp_path, monkeypatch):
    import cross_build as XB
    staged = tmp_path / "staged"
    staged.mkdir()
    for n in ("ir_00401000.o", "ir_00402000.o"):
        (staged / n).write_bytes(b"x")
    state = tmp_path / "extra.json"
    state.write_text(json.dumps({"objects": ["/x/ir_00500000.o", "/x/ir_00400100.o"]}))
    monkeypatch.setattr(XB, "STAGED", staged)
    monkeypatch.setattr(XB, "EXTRA_STATE", state)
    objects = fake_build(tmp_path, [
        {"population": "map", "va": "0x00401000", "object": "map/ir_00401000.o", "compiled": True},
        {"population": "arms", "va": "0x00402000", "object": "arms/ir_00402000.o",
         "compiled": True},
        {"population": "extra", "va": "0x00400100", "object": "extra/ir_00400100.o",
         "compiled": True},
        {"population": "extra", "va": "0x00500000", "object": "extra/ir_00500000.o",
         "compiled": True}])
    got = AG.lifted_inputs(objects, tmp_path / "linkdir")
    assert got["whole"] == ["ir_00401000.o", "ir_00402000.o"]
    assert [p.name for p in got["extras"]] == ["ir_00500000.o", "ir_00400100.o"]
    assert (tmp_path / "linkdir" / "ir_00402000.o").samefile(objects / "arms" / "ir_00402000.o")
    (staged / "ir_00403000.o").write_bytes(b"x")      # the staged directory grew: refused
    with pytest.raises(SystemExit, match="1 missing"):
        AG.lifted_inputs(objects, tmp_path / "linkdir")


def test_device_paths():
    assert AG.dev_path(f"{AG.ROOT}/private/exe/sonic2app.exe") == \
        "/data/local/tmp/sa2/root/private/exe/sonic2app.exe"
    assert AG.dev_path("private/install") == "private/install"
    assert AG.dev_path("Z:\\home\\user\\game") == "Z:\\home\\user\\game"
    assert AG.dev_path("/elsewhere/x") == "/elsewhere/x"


def test_the_boot_variant_links_the_tick_probe_alone(tmp_path):
    plan = AG.plan_for("boot", AG.ROOT, tmp_path)
    assert plan["hooks"] == ["harness/replay/wrap_tick_probe.c"]
    assert plan["wraps"] == ["sub_433ee0"]
    assert plan["overlay"] is None
    assert plan["main"] == AG.ROOT / "harness" / "crt_probe.c"
    assert plan["extra"][-1] == AG.ROOT / "harness" / "replay" / "wrap_tick_probe.c"
    with pytest.raises(SystemExit, match="unknown variant"):
        AG.plan_for("arcade", AG.ROOT, tmp_path)
