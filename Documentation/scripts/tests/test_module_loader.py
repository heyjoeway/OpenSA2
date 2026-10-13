"""The data-module loader's rules, each shown holding and each shown failing.

`runtime/sa2_module.c` maps the game's data DLL as data. Its review found six
latent ways it could hand the guest something Windows would not -- an image
mapped without its relocations, a text file taken as a reason to load a
different DLL, a freed image coming back dirty -- and none showed on the real
file, which is exactly why they need synthetic ones. Each case below builds a
small PE, runs `harness/module_probe.c` over it, and reads the result.

The generator is the one the reviewer wrote to find the defects, rewritten
here so the cases that found them keep running.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
IB = 0x10000000


def make_dll(path: Path, *, selfname: bytes = b"Data_DLL.dll", reloc: bool = True,
             reloc_dir: tuple[int, int] | None = None, soi: int | None = None,
             spill: bool = False, text_byte: int = 0xC3, code_export: bool = False) -> Path:
    """A PE32 DLL: tblA (a pointer to tblB, which needs relocating) and tblB in
    .data, an export directory naming itself `selfname`, one base relocation."""
    FA, SA = 0x200, 0x1000
    text = bytes([text_byte]) + b"\0" * (FA - 1)
    data = bytearray(0x400)
    struct.pack_into("<I", data, 0x0, IB + 0x2004)
    struct.pack_into("<I", data, 0x4, 0x11223344)
    names = (b"tblA", b"tblB") + ((b"code",) if code_export else ())
    funcs = [0x2000, 0x2004] + ([0x1000] if code_export else [])
    ed = 0x100
    af, an, ao, nm = ed + 40, ed + 80, ed + 120, ed + 160
    struct.pack_into("<IIHHIIIIIII", data, ed, 0, 0, 0, 0, 0x2000 + nm, 1, len(funcs),
                     len(names), 0x2000 + af, 0x2000 + an, 0x2000 + ao)
    for i, f in enumerate(funcs):
        struct.pack_into("<I", data, af + 4 * i, f)
    off = nm
    data[off:off + len(selfname) + 1] = selfname + b"\0"
    off += len(selfname) + 1
    for i, n in enumerate(names):
        struct.pack_into("<I", data, an + 4 * i, 0x2000 + off)
        struct.pack_into("<H", data, ao + 2 * i, i)
        data[off:off + len(n) + 1] = n + b"\0"
        off += len(n) + 1
    edsize = off - ed
    rel = struct.pack("<IIHH", 0x2000, 12, 0x3000, 0)
    reloc_raw = rel + b"\0" * (FA - len(rel))
    secs = [(b".text", 0x1000, 0x1, text, 0x60000020),
            (b".data", 0x2000, 0x400, bytes(data), 0xC0000040)]
    reloc_va = 0x3000
    if spill:
        big = bytearray(bytes(data) + b"\0" * (0x1200 - len(data)))
        big[0x1000:0x1004] = b"SPIL"                 # raw bytes past .data's aligned size
        secs[1] = (b".data", 0x2000, 0x10, bytes(big), 0xC0000040)
        secs.append((b".bss", 0x3000, 0x1000, b"", 0xC0000080))
        reloc_va = 0x4000
    if reloc:
        secs.append((b".reloc", reloc_va, len(rel), reloc_raw, 0x42000040))
    raw = FA
    table = body = b""
    for name, va, vs, rd, ch in secs:
        table += struct.pack("<8sIIIIIIHHI", name, vs, va, len(rd), raw if rd else 0, 0, 0, 0, 0, ch)
        body += rd
        raw += len(rd)
    last = max(va + max(vs, len(rd)) for _, va, vs, rd, _ in secs)
    size_of_image = soi if soi is not None else (last + SA - 1) // SA * SA
    dirs = [(0, 0)] * 16
    dirs[0] = (0x2000 + ed, edsize)
    if reloc:
        dirs[5] = reloc_dir if reloc_dir is not None else (reloc_va, len(rel))
    opt = struct.pack("<HBBIIIIIIIIIHHHHHHIIIIHHIIIIII", 0x10b, 9, 0, 0x200, 0x400, 0, 0,
                      0x1000, 0x2000, IB, SA, FA, 5, 1, 0, 0, 5, 1, 0, size_of_image, FA, 0,
                      2, 0x140, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    opt += b"".join(struct.pack("<II", *d) for d in dirs)
    fh = struct.pack("<HHIIIHH", 0x14c, len(secs), 0, 0, 0, 224, 0x2102)
    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3c, 0x40)
    head = bytes(dos) + b"PE\0\0" + fh + opt + table
    head += b"\0" * (FA - len(head))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + body)
    return path


@pytest.fixture(scope="module")
def probe(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    sys.path.insert(0, str(ROOT / "tools" / "s3" / "whole_image"))
    import common as C
    out = tmp_path_factory.mktemp("probe") / "module_probe"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(out.parent / "zc"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    p = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1",
                        "-I", str(ROOT / "runtime"), "-I", str(C.REMILL_INC),
                        str(ROOT / "harness" / "module_probe.c"),
                        str(ROOT / "runtime" / "sa2_module.c"), "-o", str(out)],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    return out


def run(probe: Path, root: Path, guest: str = r".\d\Data_DLL.dll", *extra: str) -> dict:
    p = subprocess.run([str(probe), str(root), guest, *extra], capture_output=True, text=True)
    assert p.returncode in (0, 3), p.stderr
    return json.loads(p.stdout.strip().splitlines()[-1])


def test_a_data_dll_is_mapped_relocated_and_answers_its_names(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll")
    r = run(probe, tmp_path)
    b = r["base"]
    assert b and r["base_aligned"] and r["win32"] == 0 and not r["substituted"]
    assert r["relocations"] == 1 and r["tblA_points_at_tblB"] and r["tblB"] == 0x11223344
    assert (r["by_name_tblA"], r["by_name_tblB"], r["by_ordinal_1"]) == (b + 0x2000, b + 0x2004, b + 0x2000)
    assert r["by_name_missing"] == 0


def test_a_file_that_names_itself_otherwise_is_backed_by_the_sibling_that_does(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", selfname=b"SA2ModLoader.dll")
    make_dll(tmp_path / "d" / "Data_DLL_orig.dll")
    r = run(probe, tmp_path)
    assert r["base"] and r["substituted"] and r["backing"] == "d/Data_DLL_orig.dll"


def test_no_sibling_that_names_itself_so_is_mod_not_found(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", selfname=b"SA2ModLoader.dll")
    r = run(probe, tmp_path)
    assert (r["base"], r["win32"]) == (0, 126)


def test_two_siblings_that_name_themselves_so_is_mod_not_found(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", selfname=b"SA2ModLoader.dll")
    make_dll(tmp_path / "d" / "a.dll")
    make_dll(tmp_path / "d" / "b.dll")
    r = run(probe, tmp_path)
    assert (r["base"], r["win32"]) == (0, 126)


def test_a_file_that_is_not_a_dll_is_bad_format_not_a_reason_to_look_elsewhere(probe, tmp_path):
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "Data_DLL.dll").write_text("not a PE at all\n")
    make_dll(tmp_path / "d" / "backup_old.dll")
    r = run(probe, tmp_path)
    assert (r["base"], r["win32"]) == (0, 193)


def test_an_image_that_must_move_and_has_no_relocations_is_refused_loudly(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", reloc=False)
    assert "carries no relocations" in run(probe, tmp_path)["trap"]


@pytest.mark.parametrize("reloc_dir", [(0xFFFFFF00, 0x200), (0x3000, 4)])
def test_a_malformed_relocation_directory_is_refused_loudly(probe, tmp_path, reloc_dir):
    make_dll(tmp_path / "d" / "Data_DLL.dll", reloc_dir=reloc_dir)
    assert "malformed" in run(probe, tmp_path)["trap"]


def test_an_oversized_image_is_refused_loudly(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", soi=0x08001000)
    assert "SizeOfImage" in run(probe, tmp_path)["trap"]


def test_a_code_export_that_is_a_ret_becomes_a_native(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", code_export=True)
    r = run(probe, tmp_path)
    assert r["base"] and r["natives"] == 1


def test_a_code_export_that_is_not_a_ret_refuses_the_module(probe, tmp_path):
    """The control for the case above: the loader executes nothing."""
    make_dll(tmp_path / "d" / "Data_DLL.dll", code_export=True, text_byte=0x90)
    assert "not a bare ret" in run(probe, tmp_path)["trap"]


def test_raw_bytes_past_a_sections_aligned_size_do_not_spill(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll", spill=True)
    r = run(probe, tmp_path)
    assert r["base"] and r["word_at_0x3000"] == 0          # "SPIL" would be 0x4c495053


def test_a_module_freed_to_zero_is_gone_and_reloads_fresh(probe, tmp_path):
    make_dll(tmp_path / "d" / "Data_DLL.dll")
    r = run(probe, tmp_path, r".\d\Data_DLL.dll", "--dirty-reload")
    assert r["freed_is_gone"] and r["reload_base"] == r["base"]
    assert r["mappings"] == 2 and r["reload_restored"]


def test_a_path_that_leaves_the_installation_is_refused_loudly(probe, tmp_path):
    outside = make_dll(tmp_path / "outside" / "real.dll")
    (tmp_path / "root" / "d").mkdir(parents=True)
    os.symlink(outside, tmp_path / "root" / "d" / "Data_DLL.dll")
    assert "escape" in run(probe, tmp_path / "root")["trap"]
