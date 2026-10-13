"""The installer core's rules (host/install), each shown holding and each shown failing.

`sa2-install` is built once against a synthetic hash table that `tools/s7/install_table.py`
generates from a synthetic receipt, and driven on synthetic game trees under pytest's tmp_path:
small files and two small hand-built PE images, never a shipped byte. What each case pins:

* the KeyValues reader on both libraryfolders.vdf formats, with escapes, comments, conditional
  tags and a byte-order mark, and its errors;
* Steam discovery on a fake HOME: the libraries a vdf lists, roots deduplicated by realpath,
  the Flatpak root, the app manifest matched by appid, ambiguity and --library;
* the user's policy: a changed file the game reads is refused and named; a missing one too; a
  changed file the game never reads is reported and accepted; the Mod Loader's tree accepted
  through its renamed Data_DLL_orig.dll, its extras skipped and listed; an extra under
  resource/ (where the game probes for files a clean install lacks) refused unless it is a
  save, user config or an unused original; an *_orig original that does not match, or that the
  runtime would not read (runtime/sa2_module.c's pe_parse and self_name, each condition),
  refused;
* case-blind lookup, and the refusal of two names that differ only in case;
* the executable's per-section check, each header field and each section-table field on its
  own: another copy whose code or wrapper section differs passes, every other difference is
  refused with the field named;
* the table generator's guards, each on its own: the receipt's executable, both pins, the DRM
  receipt's inputs and equal regions, a modded receipt, two paths differing only in case;
* the config and quick manifest, --verify's quick and full paths, the overlay (table files,
  a substitution's backing, anything under resource/), the source changing during a run (an
  LD_PRELOAD shim moves an mtime at a chosen moment), unreadable files and folders (an I/O
  failure, not a refusal), the record's suffix and its paths, and the source left untouched;
* the ZIP route through host/install's own sa2_zip: the overlay checked before extraction, the
  archive's evidence before and after, the store's extras.

Building against another copy of host/install (a snapshot): SA2_INSTALL_SRC names it, and
SA2_INSTALL_BUILD_EXTRA adds build.sh arguments.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SRC = Path(os.environ.get("SA2_INSTALL_SRC", ROOT / "host" / "install"))
TABLE_TOOL = ROOT / "tools" / "s7" / "install_table.py"
EXIT = {"ok": 0, "usage": 2, "refused": 3, "not_found": 4, "io": 5, "ambiguous": 6}
DLL = "resource/gd_PC/DLL/Win32/Data_DLL.dll"
ORIG = "resource/gd_PC/DLL/Win32/Data_DLL_orig.dll"


# ------------------------------------------------------------------ synthetic PE images

def pe(sections: list[tuple[bytes, int, bytes]], *, dll: bool = False,
       export_name: bytes | None = None, machine: int = 0x14C, magic: int = 0x10B,
       align: int = 0x1000, export_size: int = 40) -> bytes:
    """A PE32 image: each section (name, characteristics, raw bytes) at its own 0x1000-aligned
    RVA. A DLL's export directory, when asked, sits in its own .edata section. `machine`,
    `magic`, `align` (SectionAlignment) and `export_size` (the export directory's size) are for
    the images the runtime refuses."""
    FA, SA = 0x200, 0x1000
    secs = list(sections)
    if export_name is not None:
        secs.append((b".edata", 0x40000040, b""))
    n = len(secs)
    lfanew = 0x80
    opt_size = 0xE0
    headers = 0x400
    rva = SA
    layout = []
    raw_ptr = headers
    for name, chars, data in secs:
        if name == b".edata":
            ed = bytearray(40)
            struct.pack_into("<I", ed, 12, rva + 40)       # Name
            data = bytes(ed) + export_name + b"\0"
        size = (len(data) + FA - 1) // FA * FA
        layout.append((name, chars, data, rva, raw_ptr, size))
        raw_ptr += size
        rva += (max(len(data), 1) + SA - 1) // SA * SA
    image = bytearray(raw_ptr)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, lfanew)
    image[lfanew:lfanew + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", image, lfanew + 4, machine, n, 0, 0, 0, opt_size,
                     0x2102 if dll else 0x0102)
    opt = lfanew + 24
    struct.pack_into("<H", image, opt, magic)
    struct.pack_into("<I", image, opt + 16, SA)               # entry point
    struct.pack_into("<I", image, opt + 28, 0x10000000 if dll else 0x400000)
    struct.pack_into("<II", image, opt + 32, align, FA)
    struct.pack_into("<II", image, opt + 56, rva, headers)    # SizeOfImage, SizeOfHeaders
    struct.pack_into("<I", image, opt + 92, 16)
    for name, chars, data, r, p, size in layout:
        if name == b".edata":
            struct.pack_into("<II", image, opt + 96, r, export_size)
    sec = opt + opt_size
    for i, (name, chars, data, r, p, size) in enumerate(layout):
        s = sec + 40 * i
        image[s:s + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", image, s + 8, max(len(data), 1), r, size, p)
        struct.pack_into("<I", image, s + 36, chars)
        image[p:p + len(data)] = data
    return bytes(image)


TEXT = 0x60000020
DATA = 0xC0000040
CODE_PLAIN = bytes(range(256)) * 4
CODE_WRAPPED = bytes(255 - b for b in CODE_PLAIN)
DATA_BYTES = b"game data " * 100
BIND = b"wrapper stub " * 50


def shipped_exe(code: bytes = CODE_WRAPPED, data: bytes = DATA_BYTES, bind: bytes = BIND) -> bytes:
    return pe([(b".text", TEXT, code), (b".data", DATA, data), (b".bind", TEXT, bind)])


def unpacked_exe() -> bytes:
    return pe([(b".text", TEXT, CODE_PLAIN), (b".data", DATA, DATA_BYTES)])


def data_dll() -> bytes:
    return pe([(b".data", DATA, b"asset tables " * 40)], dll=True, export_name=b"Data_DLL.dll")


def loader_proxy(**kw) -> bytes:
    kw.setdefault("dll", True)
    return pe([(b".text", TEXT, b"\xc3" * 64)], export_name=b"SA2ModLoader.dll", **kw)


# ------------------------------------------------------------------ the synthetic game

def game_files() -> dict[str, bytes]:
    return {
        "Launcher.exe": b"launcher " * 30,
        "steam_api.dll": b"steam api " * 30,
        "sonic2app.exe": shipped_exe(),
        DLL: data_dll(),
        "resource/gd_PC/ADX/title.adx": b"music " * 300,
        "resource/gd_PC/PRS/AL_ENTRANCE_TEX.prs": b"texture " * 200,
        "resource/gd_PC/SOC/menu.pak": b"menu " * 100,
        "resource/Shader/win32/obj.pak": b"shader " * 80,
    }


def write_tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return root


def tree_state(root: Path) -> list[tuple[str, int, int]]:
    return sorted((p.relative_to(root).as_posix(), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in root.rglob("*") if p.is_file())


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict:
    work = tmp_path_factory.mktemp("installer")
    files = game_files()
    receipt = {"schema": "sa2-recomp-receipt/v1", "id": "install-hashes-synthetic", "modded": False,
               "files": [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                         for p, b in sorted(files.items())]}
    rpath = work / "receipt.hashes"          # not .json: the M7 census reads every .json under private/generated
    rpath.write_text(json.dumps(receipt, indent=1))
    (work / "shipped.exe").write_bytes(files["sonic2app.exe"])
    (work / "unpacked.exe").write_bytes(unpacked_exe())
    table_dir = work / "table"
    gen = subprocess.run([sys.executable, str(TABLE_TOOL), "--receipt", str(rpath),
                          "--exe", str(work / "shipped.exe"), "--reference", str(work / "unpacked.exe"),
                          "--pins", "none", "--drm-receipt", "none", "--out-dir", str(table_dir)],
                         capture_output=True, text=True)
    assert gen.returncode == 0, gen.stderr
    exe = work / "sa2-install"
    extra = shlex.split(os.environ.get("SA2_INSTALL_BUILD_EXTRA", ""))
    b = subprocess.run(["bash", str(SRC / "build.sh"), "--src", str(SRC), "--table",
                        str(table_dir / "sa2_install_table.cpp"), "--out", str(exe), *extra],
                       capture_output=True, text=True)
    assert b.returncode == 0, b.stdout + b.stderr
    return {"exe": exe, "files": files, "table_dir": table_dir, "work": work, "receipt": rpath}


def run(built: dict, home: Path, *args: str, record: bool = True, env_extra: dict | None = None
        ) -> tuple[subprocess.CompletedProcess, dict]:
    home.mkdir(parents=True, exist_ok=True)
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), **(env_extra or {})}
    rec = home / "run.runrec"
    if rec.exists():
        rec.unlink()
    argv = [str(built["exe"]), *args] + (["--record", str(rec)] if record else [])
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    doc = json.loads(rec.read_text()) if record and rec.exists() else {}
    return p, doc


def game(tmp_path: Path, built: dict, mutate=None, name: str = "Sonic Adventure 2") -> Path:
    files = dict(built["files"])
    if mutate:
        mutate(files)
    return write_tree(tmp_path / "games" / name, files)


# ------------------------------------------------------------------ the table generator

def test_table_splits_required_from_known_and_checks_the_exe_by_section(built):
    summary = (built["table_dir"] / "sa2_install_table.txt").read_text()
    assert "entries 8 required 6 known 2" in summary
    assert ".text    va 0x00001000 not hashed: the code section" in summary
    assert ".bind" in summary and "not hashed: added by the wrapper" in summary
    assert ".data    va 0x00002000 hashed 1000 bytes" in summary
    src = (built["table_dir"] / "sa2_install_table.cpp").read_text()
    assert '{"sonic2app.exe", ' in src and "&kExe}" in src
    assert hashlib.sha256(built["files"]["sonic2app.exe"]).hexdigest() not in src


def test_table_refuses_a_differing_data_section(built, tmp_path):
    (tmp_path / "other.exe").write_bytes(shipped_exe(data=b"another data " * 77))
    r = subprocess.run([sys.executable, str(TABLE_TOOL), "--receipt", str(built["receipt"]),
                        "--exe", str(built["work"] / "shipped.exe"), "--reference", str(tmp_path / "other.exe"),
                        "--pins", "none", "--drm-receipt", "none", "--out-dir", str(tmp_path / "t")],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "section .data at 0x2000 differs from the unpacked file and is not the code section" in r.stderr


def test_table_check_mode_reports_stale(built, tmp_path):
    out = tmp_path / "t"
    shutil.copytree(built["table_dir"], out)
    base = [sys.executable, str(TABLE_TOOL), "--receipt", str(built["receipt"]),
            "--exe", str(built["work"] / "shipped.exe"), "--reference", str(built["work"] / "unpacked.exe"),
            "--pins", "none", "--drm-receipt", "none", "--out-dir", str(out), "--check"]
    assert subprocess.run(base, capture_output=True).returncode == 0
    (out / "sa2_install_table.cpp").write_text("// edited\n")
    assert subprocess.run(base, capture_output=True).returncode == 1


# ------------------------------------------------------------------ KeyValues

NEW_VDF = '''\ufeff"libraryfolders"
{
\t"contentstatsid"\t\t"-123"
\t"0"
\t{
\t\t"path"\t\t"/home/u/.local/share/Steam"
\t\t"label"\t\t""
\t\t"apps"
\t\t{
\t\t\t"213610"\t\t"3253731096"
\t\t}
\t}
\t// a comment
\t"1" { "path" "/mnt/games/Steam Library" [$LINUX] "totalsize" "0" }
}
'''

OLD_VDF = '''"LibraryFolders"
{
\t"TimeNextStatsReport"\t\t"1600000000"
\t"ContentStatsID"\t\t"-42"
\t"1"\t\t"D:\\\\SteamLibrary"
\t"2"\t\t"/mnt/with \\"quote\\""
}
'''


@pytest.mark.parametrize("text,want", [
    (NEW_VDF, ["/home/u/.local/share/Steam", "/mnt/games/Steam Library"]),
    (OLD_VDF, ["D:\\SteamLibrary", '/mnt/with "quote"']),
    ('libraryfolders { 0 { path /unquoted/lib } }', ["/unquoted/lib"]),
    ('"other" { "0" { "path" "/x" } }', []),
])
def test_vdf_both_formats(built, tmp_path, text, want):
    f = tmp_path / "libraryfolders.vdf"
    f.write_text(text, encoding="utf-8")
    p, _ = run(built, tmp_path / "h", "--read-vdf", str(f), record=False)
    assert p.returncode == 0, p.stderr
    assert p.stdout.splitlines() == [f"library {w}" for w in want]


@pytest.mark.parametrize("text,err", [
    ('"libraryfolders" { "0" { "path" "/x" }', "a block never closes"),
    ('"libraryfolders" { "0" "x" } }', "'}' with no open block"),
    ('"libraryfolders" { "path" ', "has no value"),
    ('"libraryfolders" { "path" "unterminated', "unterminated quoted string"),
])
def test_vdf_errors(built, tmp_path, text, err):
    f = tmp_path / "libraryfolders.vdf"
    f.write_text(text)
    p, _ = run(built, tmp_path / "h", "--read-vdf", str(f), record=False)
    assert p.returncode == EXIT["io"]
    assert err in p.stderr


# ------------------------------------------------------------------ Steam discovery

def acf(installdir: str = "Sonic Adventure 2", appid: str = "213610", flags: str = "4") -> str:
    return (f'"AppState"\n{{\n\t"appid"\t\t"{appid}"\n\t"name"\t\t"Sonic Adventure 2\u2122 "\n'
            f'\t"StateFlags"\t\t"{flags}"\n\t"installdir"\t\t"{installdir}"\n\t"buildid"\t\t"15280"\n'
            f'\t"LastOwner"\t\t"76561190000000000"\n}}\n')


def steam_library(lib: Path, built: dict, tmp_path: Path, with_game: bool = True, **kw) -> Path:
    (lib / "steamapps").mkdir(parents=True, exist_ok=True)
    if with_game:
        (lib / "steamapps" / "appmanifest_213610.acf").write_text(acf(**kw))
        write_tree(lib / "steamapps" / "common" / kw.get("installdir", "Sonic Adventure 2"), built["files"])
    return lib


def test_steam_finds_the_game_in_a_second_library(built, tmp_path):
    home = tmp_path / "home"
    root = home / ".local" / "share" / "Steam"
    steam_library(root, built, tmp_path, with_game=False)
    lib2 = steam_library(tmp_path / "lib2", built, tmp_path)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{root}" }} "1" {{ "path" "{lib2}" "apps" {{ "1" "2" }} }} }}')
    (home / ".steam").mkdir()
    (home / ".steam" / "root").symlink_to(root)
    (home / ".steam" / "steam").symlink_to(root)
    p, rec = run(built, home, "--source", "steam", "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    game_path = (lib2 / "steamapps" / "common" / "Sonic Adventure 2").resolve()
    assert rec["route"] == "steam-autodiscovery"
    assert rec["source"]["steam"]["roots"] == ["~/.local/share/Steam"]
    assert len(rec["source"]["steam"]["libraries"]) == 2
    assert [i["game_path"] for i in rec["source"]["steam"]["installs"]] == [str(game_path)]
    assert "76561190000000000" not in json.dumps(rec)
    assert rec["hash_checked"] is True and rec["installed"] is True and rec["manual_steps"] == 0
    assert rec["counts"]["ok"] == 8 and rec["exit"]["name"] == "accepted"
    cfg = (home / ".config" / "sa2recomp" / "sa2recomp.cfg").read_text()
    assert "source.kind=steam\n" in cfg
    assert f"read_root={game_path}\n" in cfg
    assert f"exe={game_path}/sonic2app.exe\n" in cfg
    assert f"overlay={home}/.local/share/sa2recomp/overlay\n" in cfg
    assert "table.id=install-hashes-synthetic\n" in cfg
    assert (home / ".local" / "share" / "sa2recomp" / "manifest.tsv").is_file()


def test_steam_old_format_vdf_and_config_fallback(built, tmp_path):
    home = tmp_path / "home"
    root = home / ".steam" / "debian-installation"
    (root / "config").mkdir(parents=True)
    lib = steam_library(tmp_path / "old lib", built, tmp_path)
    (root / "config" / "libraryfolders.vdf").write_text(
        f'"LibraryFolders"\n{{\n\t"TimeNextStatsReport"\t"1"\n\t"1"\t"{lib}"\n}}\n')
    p, rec = run(built, home, "--source", "steam")
    assert p.returncode == 0, p.stderr
    assert rec["installed"] is False                       # no --yes: a dry run
    assert not (home / ".config").exists()
    assert rec["source"]["read_root"].endswith("old lib/steamapps/common/Sonic Adventure 2")


def test_steam_flatpak_root_and_case_blind_steamapps(built, tmp_path):
    home = tmp_path / "home"
    root = home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam"
    (root / "SteamApps").mkdir(parents=True)
    (root / "SteamApps" / "appmanifest_213610.acf").write_text(acf())
    write_tree(root / "SteamApps" / "common" / "Sonic Adventure 2", built["files"])
    p, rec = run(built, home, "--source", "steam")
    assert p.returncode == 0, p.stderr
    assert rec["source"]["steam"]["roots"] == ["~/.var/app/com.valvesoftware.Steam/.local/share/Steam"]


def test_steam_manifest_is_matched_by_appid_and_state(built, tmp_path):
    home = tmp_path / "home"
    root = home / ".local" / "share" / "Steam"
    steam_library(root, built, tmp_path, flags="2")         # not fully installed
    p, rec = run(built, home, "--source", "steam")
    assert p.returncode == EXIT["not_found"]
    assert any("is not fully installed" in n for n in rec["source"]["steam"]["notes"])
    (root / "steamapps" / "appmanifest_213610.acf").write_text(acf(appid="71250"))
    p, rec = run(built, home, "--source", "steam")
    assert p.returncode == EXIT["not_found"]
    assert any("not app 213610's manifest" in n for n in rec["source"]["steam"]["notes"])


def test_steam_not_found_on_an_empty_home(built, tmp_path):
    p, rec = run(built, tmp_path / "home", "--source", "steam", "--yes")
    assert p.returncode == EXIT["not_found"]
    assert "no Steam installation of app 213610" in p.stderr
    assert rec["exit"]["name"] == "not found" and rec["installed"] is False


def test_steam_two_installs_are_ambiguous_until_library_picks_one(built, tmp_path):
    home = tmp_path / "home"
    root = steam_library(home / ".local" / "share" / "Steam", built, tmp_path)
    lib2 = steam_library(tmp_path / "lib2", built, tmp_path)
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{root}" }} "1" {{ "path" "{lib2}" }} }}')
    p, rec = run(built, home, "--source", "steam", "--yes")
    assert p.returncode == EXIT["ambiguous"]
    assert "--library 0" in p.stderr and "--library 1" in p.stderr
    assert not (home / ".config").exists()
    p, rec = run(built, home, "--source", "steam", "--library", "1", "--yes")
    assert p.returncode == 0, p.stderr
    assert rec["source"]["steam"]["chosen"] == 1
    assert rec["source"]["read_root"] == str((lib2 / "steamapps/common/Sonic Adventure 2").resolve())
    p, _ = run(built, home, "--source", "steam", "--library", "2")
    assert p.returncode == EXIT["usage"]


# ------------------------------------------------------------------ the policy

def test_unmodified_folder_is_accepted_and_left_untouched(built, tmp_path):
    g = game(tmp_path, built)
    before = tree_state(g)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stderr
    assert tree_state(g) == before
    assert rec["source_manifest"]["untouched"] is True and rec["source_manifest"]["files"] == 8
    assert rec["counts"] == {"entries": 8, "ok": 8, "missing": 0, "modified": 0, "substituted": 0,
                             "extras_skipped": 0, "extras_refused": 0, "known_missing": 0,
                             "known_modified": 0}
    assert rec["extras"] == []


def test_a_changed_file_the_game_reads_is_refused_and_named(built, tmp_path):
    def mutate(f):
        f["resource/gd_PC/ADX/title.adx"] = b"Music " + f["resource/gd_PC/ADX/title.adx"][6:]
    g = game(tmp_path, built, mutate)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/ADX/title.adx: changed (sha256" in p.stderr
    assert [x["path"] for x in rec["not_ok"]] == ["resource/gd_PC/ADX/title.adx"]
    assert rec["installed"] is False
    assert not (tmp_path / "home" / ".config").exists()


def test_a_missing_file_the_game_reads_is_refused(built, tmp_path):
    g = game(tmp_path, built, lambda f: f.pop("resource/gd_PC/SOC/menu.pak"))
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/SOC/menu.pak: missing" in p.stderr


def test_a_changed_file_the_game_never_reads_is_reported_not_refused(built, tmp_path):
    g = game(tmp_path, built, lambda f: f.update({"Launcher.exe": b"patched launcher"}))
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stderr
    assert rec["counts"]["known_modified"] == 1
    assert "not read by the game, modified: Launcher.exe" in p.stdout


def mod_loader(f: dict) -> None:
    f[ORIG] = f[DLL]
    f[DLL] = loader_proxy()
    f["SA2ModLoader.dll"] = b"mod loader"
    f["sa2mlver.txt"] = b"9"
    f["Codes.lst"] = b"codes"
    f["mods/SomeMod/mod.ini"] = b"[mod]"
    f["mods/SomeMod/gd_PC/title.adx"] = b"replacement music"


def test_mod_loader_tree_is_accepted_through_its_orig_original(built, tmp_path):
    g = game(tmp_path, built, mod_loader)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stderr
    assert rec["substitutions"] == [{"path": DLL, "backing": ORIG}]
    assert rec["mod_loader"] is True
    assert rec["markers"] == ["SA2ModLoader.dll", "sa2mlver.txt", "mods", ORIG]
    assert rec["counts"]["extras_skipped"] == 5 and rec["counts"]["extras_refused"] == 0
    assert sorted(x["path"] for x in rec["extras"]) == [
        "Codes.lst", "SA2ModLoader.dll", "mods/SomeMod/gd_PC/title.adx", "mods/SomeMod/mod.ini", "sa2mlver.txt"]
    assert {x["why"] for x in rec["extras"]} == {"outside resource/"}
    assert rec["counts"]["substituted"] == 1 and rec["counts"]["ok"] == 7
    cfg = (tmp_path / "home" / ".config" / "sa2recomp" / "sa2recomp.cfg").read_text()
    assert f"substitution={DLL} <- {ORIG}\n" in cfg and "mod_loader=1\n" in cfg
    manifest = (tmp_path / "home" / ".local/share/sa2recomp/manifest.tsv").read_text()
    assert f"\t{ORIG}\n" in manifest and f"\t{DLL}\n" in manifest


def test_an_orig_original_that_does_not_match_is_refused(built, tmp_path):
    def mutate(f):
        mod_loader(f)
        f[ORIG] = pe([(b".data", DATA, b"other tables " * 40)], dll=True, export_name=b"Data_DLL.dll")
    g = game(tmp_path, built, mutate)
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert f"its original {ORIG} does not match either" in p.stderr


def test_an_orig_original_the_runtime_would_not_read_is_refused(built, tmp_path):
    def mutate(f):
        f["resource/gd_PC/PRS/AL_ENTRANCE_TEX_orig.prs"] = f["resource/gd_PC/PRS/AL_ENTRANCE_TEX.prs"]
        f["resource/gd_PC/PRS/AL_ENTRANCE_TEX.prs"] = b"modded texture"
    g = game(tmp_path, built, mutate)
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "the runtime reads the named file, not a *_orig original, for anything but a DLL" in p.stderr


def test_a_dll_that_still_names_itself_is_not_substituted(built, tmp_path):
    def mutate(f):
        f[ORIG] = f[DLL]
        f[DLL] = pe([(b".data", DATA, b"edited tables " * 40)], dll=True, export_name=b"Data_DLL.dll")
    g = game(tmp_path, built, mutate)
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "the runtime maps the named file" in p.stderr


def test_two_files_naming_themselves_the_data_dll_are_refused(built, tmp_path):
    def mutate(f):
        mod_loader(f)
        f["resource/gd_PC/DLL/Win32/Data_DLL_backup.dll"] = f[ORIG]
    g = game(tmp_path, built, mutate)
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "but 2 files beside it name themselves Data_DLL.dll, and the runtime refuses anything but one" in p.stderr


def test_lookup_is_case_blind(built, tmp_path):
    files = {k.replace("resource/", "RESOURCE/").replace("gd_PC", "GD_PC").replace("title.adx", "TITLE.ADX"): v
             for k, v in built["files"].items()}
    g = write_tree(tmp_path / "games" / "SA2", files)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stderr
    assert rec["found_in_another_case"] == 5
    cfg = (tmp_path / "home" / ".config/sa2recomp/sa2recomp.cfg").read_text()
    assert f"read_root={g.resolve()}\n" in cfg


def test_lookup_takes_the_exact_folder_first_as_the_runtime_does(built, tmp_path):
    # resource/ exists as spelled, so the runtime never looks in RESOURCE/ for gd_PC.
    g = game(tmp_path, built)
    (g / "RESOURCE" / "gd_PC" / "SOC").mkdir(parents=True)
    (g / "resource/gd_PC/SOC/menu.pak").rename(g / "RESOURCE/gd_PC/SOC/menu.pak")
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/SOC/menu.pak: missing" in p.stderr


def test_two_names_differing_only_in_case_are_refused(built, tmp_path):
    g = game(tmp_path, built, lambda f: f.update(
        {"resource/gd_PC/ADX/TITLE.adx": f["resource/gd_PC/ADX/title.adx"]}))
    os.rename(g / "resource/gd_PC/ADX/title.adx", g / "resource/gd_PC/ADX/Title.adx")
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "two names on its path differ only in case" in p.stderr


def test_a_link_out_of_the_folder_is_not_followed(built, tmp_path):
    g = game(tmp_path, built)
    outside = tmp_path / "outside.pak"
    (g / "resource/gd_PC/SOC/menu.pak").rename(outside)
    (g / "resource/gd_PC/SOC/menu.pak").symlink_to(outside)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/SOC/menu.pak: changed (a link out of the folder" in p.stderr
    assert "a link out of the folder, not followed: resource/gd_PC/SOC/menu.pak" in rec["source_manifest"]["notes"]


def test_not_a_game_folder(built, tmp_path):
    (tmp_path / "empty").mkdir()
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(tmp_path / "empty"))
    assert p.returncode == EXIT["not_found"]
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(tmp_path / "absent"))
    assert p.returncode == EXIT["not_found"]


# ------------------------------------------------------------------ the executable

@pytest.mark.parametrize("exe,code", [
    (shipped_exe(code=b"\x90" * 1024), 0),                         # another copy's code section
    (shipped_exe(bind=BIND.upper()), 0),                          # another copy's wrapper bytes
    (shipped_exe(data=b"GAME data " + DATA_BYTES[10:]), EXIT["refused"]),
    (unpacked_exe(), EXIT["refused"]),                            # the section table differs
    (b"not a PE at all", EXIT["refused"]),
])
def test_the_executable_is_checked_by_section(built, tmp_path, exe, code):
    g = game(tmp_path, built, lambda f: f.update({"sonic2app.exe": exe}))
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == code, p.stdout + p.stderr
    if code:
        assert "refused: sonic2app.exe: changed (executable check:" in p.stderr


# ------------------------------------------------------------------ verify, records, zip

def test_verify_quick_then_full(built, tmp_path):
    g = game(tmp_path, built)
    home = tmp_path / "home"
    assert run(built, home, "--source", "folder", str(g), "--yes")[0].returncode == 0
    p, rec = run(built, home, "--verify")
    assert p.returncode == 0 and rec["verified_mode"] == "quick", p.stderr
    assert rec["source_manifest"]["untouched"] is True and rec["source_manifest"]["files"] == 8
    adx = g / "resource/gd_PC/ADX/title.adx"
    os.utime(adx, ns=(adx.stat().st_atime_ns, adx.stat().st_mtime_ns + 10**9))
    p, rec = run(built, home, "--verify")
    assert p.returncode == 0 and rec["verified_mode"] == "full", p.stdout + p.stderr
    assert rec["quick_check_differences"] == ["resource/gd_PC/ADX/title.adx: size or time changed"]
    p, rec = run(built, home, "--verify")
    assert rec["verified_mode"] == "quick"
    adx.write_bytes(b"x" + adx.read_bytes()[1:])
    p, rec = run(built, home, "--verify")
    assert p.returncode == EXIT["refused"]


def test_verify_sees_a_new_spelling_that_shadows_a_verified_file(built, tmp_path):
    # Verified through its case-folded spelling; then the exact spelling appears beside it, and
    # the runtime, which takes the exact spelling first, would read the new file.
    g = game(tmp_path, built)
    adx = g / "resource/gd_PC/ADX"
    (adx / "title.adx").rename(adx / "TITLE.ADX")
    home = tmp_path / "home"
    assert run(built, home, "--source", "folder", str(g), "--yes")[0].returncode == 0
    (adx / "title.adx").write_bytes(b"a file the runtime now reads instead")
    os.utime(adx, ns=(adx.stat().st_atime_ns, adx.stat().st_mtime_ns + 10**9))
    p, rec = run(built, home, "--verify")
    assert rec["quick_check_differences"] == ["resource/gd_PC/ADX/: a name in it was added, removed or renamed"]
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/ADX/title.adx: changed" in p.stderr


def test_verify_keeps_the_data_dir_the_install_used(built, tmp_path):
    g = game(tmp_path, built)
    home, data = tmp_path / "home", tmp_path / "data"
    assert run(built, home, "--source", "folder", str(g), "--yes", "--data-dir", str(data))[0].returncode == 0
    adx = g / "resource/gd_PC/ADX/title.adx"
    os.utime(adx, ns=(adx.stat().st_atime_ns, adx.stat().st_mtime_ns + 10**9))
    p, rec = run(built, home, "--verify")
    assert p.returncode == 0 and rec["verified_mode"] == "full", p.stderr
    cfg = (home / ".config/sa2recomp/sa2recomp.cfg").read_text()
    assert f"manifest={data}/manifest.tsv\n" in cfg and f"overlay={data}/overlay\n" in cfg
    assert not (home / ".local/share/sa2recomp").exists()


def test_the_installer_never_writes_inside_the_game_folder(built, tmp_path):
    g = game(tmp_path, built)
    before = tree_state(g)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes",
                 "--data-dir", str(g / "sa2recomp-data"))
    assert p.returncode == EXIT["usage"]
    assert "is inside the game folder, which the installer never writes" in p.stderr
    assert tree_state(g) == before and rec["installed"] is False


def test_a_shipped_file_in_the_overlay_is_refused(built, tmp_path):
    # The runtime reads the overlay first, so a copy there would shadow the verified file.
    g = game(tmp_path, built)
    home = tmp_path / "home"
    assert run(built, home, "--source", "folder", str(g), "--yes")[0].returncode == 0
    overlay = home / ".local/share/sa2recomp/overlay"
    write_tree(overlay, {"resource/gd_PC/SAVEDATA/SONIC2B__S01": b"a save: fine",
                         "Config/UserConfig.cfg": b"<Configs/>"})
    assert run(built, home, "--verify")[0].returncode == 0
    write_tree(overlay, {"resource/gd_PC/ADX/TITLE.ADX": b"a shadow"})   # found case-blind
    p, rec = run(built, home, "--verify")
    assert p.returncode == EXIT["refused"]
    assert rec["refusals"] == ["resource/gd_PC/ADX/title.adx: the overlay holds a copy, which the runtime would read first"]
    p, _ = run(built, home, "--source", "folder", str(g), "--yes")
    assert p.returncode == EXIT["refused"]


def test_verify_without_a_config(built, tmp_path):
    p, _ = run(built, tmp_path / "home", "--verify")
    assert p.returncode == EXIT["not_found"]


def test_record_must_not_be_json(built, tmp_path):
    g = game(tmp_path, built)
    p = subprocess.run([str(built["exe"]), "--source", "folder", str(g), "--record", str(tmp_path / "r.json")],
                       capture_output=True, text=True, env={"HOME": str(tmp_path)})
    assert p.returncode == EXIT["usage"]
    assert not (tmp_path / "r.json").exists()


def test_record_paths_are_home_relative(built, tmp_path):
    home = tmp_path / "home"
    g = game(home, built)
    p, rec = run(built, home, "--source", "folder", str(g), "--yes")
    assert p.returncode == 0
    assert rec["source"]["path"] == "~/games/Sonic Adventure 2"
    assert rec["config"]["path"] == "~/.config/sa2recomp/sa2recomp.cfg"
    assert str(home) not in json.dumps(rec)


def test_zip_route_needs_yes_and_a_file(built, tmp_path):
    z = tmp_path / "game.zip"
    z.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    p, _ = run(built, tmp_path / "home", "--source", "zip", str(z))
    assert p.returncode == EXIT["usage"]
    p, _ = run(built, tmp_path / "home", "--source", "zip", str(tmp_path / "absent.zip"), "--yes")
    assert p.returncode == EXIT["not_found"]


def test_usage_errors(built, tmp_path):
    for args in (["--source", "steam", "--verify"], ["--source", "disk"], [], ["--library", "1"],
                 ["--source", "folder", str(tmp_path), "--library", "0"]):
        p, _ = run(built, tmp_path / "home", *args, record=False)
        assert p.returncode == EXIT["usage"], args


# ------------------------------------------------------------------ the executable, field by field

LFANEW, OPT_SIZE = 0x80, 0xE0
OPT = LFANEW + 24
SECT = OPT + OPT_SIZE                 # .text, .data, .bind; 40 bytes each


def _set(fmt: str, off: int, value: int):
    def f(b: bytearray) -> bytearray:
        struct.pack_into(fmt, b, off, value)
        return b
    return f


def _bump(fmt: str, off: int, by: int):
    def f(b: bytearray) -> bytearray:
        struct.pack_into(fmt, b, off, struct.unpack_from(fmt, b, off)[0] + by)
        return b
    return f


def _move_pe_header(b: bytearray) -> bytearray:
    head = bytes(b[LFANEW:SECT + 120])
    b[LFANEW:SECT + 120] = bytes(SECT + 120 - LFANEW)
    b[LFANEW + 0x10:SECT + 120 + 0x10] = head
    struct.pack_into("<I", b, 0x3C, LFANEW + 0x10)
    return b


def _grow_optional_header(b: bytearray) -> bytearray:
    table = bytes(b[SECT:SECT + 120])
    b[SECT:SECT + 120] = bytes(120)
    b[SECT + 0x10:SECT + 0x10 + 120] = table
    struct.pack_into("<H", b, LFANEW + 20, OPT_SIZE + 0x10)
    return b


def _cut_data(b: bytearray) -> bytearray:
    raw_ptr = struct.unpack_from("<I", b, SECT + 40 + 20)[0]     # .data's raw pointer
    return b[:raw_ptr + 500]


@pytest.mark.parametrize("mutate,named", [
    (_move_pe_header, "e_lfanew"),
    (_set("<H", LFANEW + 6, 2), "section count"),
    (_grow_optional_header, "optional header size"),
    (_set("<H", OPT, 0x20B), "optional header magic"),
    (_set("<I", OPT + 28, 0x500000), "image base"),
    (_bump("<I", OPT + 56, 0x1000), "image size"),
    (_set("<I", OPT + 60, 0x600), "header size"),
    (_set("<I", OPT + 104, 0x2000), "import directory"),
    (_set("<I", OPT + 108, 8), "import directory"),
    (_set("<I", OPT + 112, 0x2000), "resource directory"),
    (_set("<I", OPT + 116, 8), "resource directory"),
    (_bump("<I", SECT + 12, 0x10000), "section .text at 0x1000: its table entry differs"),
    (_bump("<I", SECT + 80 + 8, 1), "section .bind at 0x3000: its table entry differs"),
    (_bump("<I", SECT + 16, 0x200), "section .text at 0x1000: its table entry differs"),
    (_set("<I", SECT + 36, 0xE0000020), "section .text at 0x1000: its table entry differs"),
    (_cut_data, "section .data: 500 bytes loaded, expected 1000"),
])
def test_each_executable_field_is_checked_on_its_own(built, tmp_path, mutate, named):
    exe = bytes(mutate(bytearray(shipped_exe())))
    g = game(tmp_path, built, lambda f: f.update({"sonic2app.exe": exe}))
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"], p.stdout + p.stderr
    assert f"refused: sonic2app.exe: changed (executable check: {named}" in p.stderr


def test_the_record_names_the_executable_check_compiled_in(built, tmp_path):
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(game(tmp_path, built)))
    assert p.returncode == 0
    assert rec["table"]["exe_check"] == {
        "kind": "per-section", "ruling": "Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md",
        "hashed": [".data"], "not_hashed": [".text", ".bind"]}


# ------------------------------------------------------------------ the table generator's guards

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def generate(built: dict, tmp_path: Path, *, receipt: dict | None = None, exe: bytes | None = None,
             pins: dict | None = None, drm: dict | None = None) -> subprocess.CompletedProcess:
    rpath = built["receipt"]
    if receipt is not None:
        rpath = tmp_path / "receipt.hashes"
        rpath.write_text(json.dumps(receipt))
    exe_path = built["work"] / "shipped.exe"
    if exe is not None:
        exe_path = tmp_path / "other.exe"
        exe_path.write_bytes(exe)
    args = [sys.executable, str(TABLE_TOOL), "--receipt", str(rpath), "--exe", str(exe_path),
            "--reference", str(built["work"] / "unpacked.exe"), "--out-dir", str(tmp_path / "t")]
    for flag, doc, name in (("--pins", pins, "pins.pins"), ("--drm-receipt", drm, "drm.receipt")):
        if doc is None:
            args += [flag, "none"]
        else:
            (tmp_path / name).write_text(json.dumps(doc))
            args += [flag, str(tmp_path / name)]
    return subprocess.run(args, capture_output=True, text=True)


def good_pins() -> dict:
    return {"executables": {"sonic2app.exe": {"sha256": _sha(shipped_exe())},
                            "sonic2app-unpacked.exe": {"sha256": _sha(unpacked_exe())}}}


def good_drm() -> dict:
    return {"inputs": {"shipped": {"sha256": _sha(shipped_exe())}, "unpacked": {"sha256": _sha(unpacked_exe())}},
            "regions": [{"va": "0x00400000", "shipped": ["(headers)"], "differing": 3},
                        {"va": "0x00401000", "shipped": [".text"], "differing": 1024},
                        {"va": "0x00402000", "shipped": [".data"], "differing": 0},
                        {"va": "0x00403000", "shipped": [".bind"], "differing": 650}]}


def synthetic_receipt(built: dict) -> dict:
    return json.loads(built["receipt"].read_text())


def test_table_holds_to_the_pins_and_the_drm_receipt(built, tmp_path):
    r = generate(built, tmp_path, pins=good_pins(), drm=good_drm())
    assert r.returncode == 0, r.stderr
    assert "held to the pins: yes; held to the DRM receipt: yes" in r.stdout


def _drm_with(**change) -> dict:
    d = good_drm()
    if "shipped" in change:
        d["inputs"]["shipped"]["sha256"] = change["shipped"]
    if "unpacked" in change:
        d["inputs"]["unpacked"]["sha256"] = change["unpacked"]
    if "data_differs" in change:
        d["regions"][2]["differing"] = 5
    return d


def _pins_with(which: str) -> dict:
    p = good_pins()
    p["executables"][which]["sha256"] = "0" * 64
    return p


@pytest.mark.parametrize("kw,err", [
    ({"drm": _drm_with(data_differs=True)}, "are not the DRM receipt's equal regions"),
    ({"drm": _drm_with(shipped="1" * 64)}, "the DRM receipt's inputs are not the executables used here"),
    ({"drm": _drm_with(unpacked="1" * 64)}, "the DRM receipt's inputs are not the executables used here"),
    ({"pins": _pins_with("sonic2app.exe")}, "shipped.exe does not match its pin"),
    ({"pins": _pins_with("sonic2app-unpacked.exe")}, "unpacked.exe does not match its pin"),
    ({"exe": shipped_exe(code=b"\x90" * 1024)}, "is not the receipt's sonic2app.exe"),
])
def test_table_guard_refuses(built, tmp_path, kw, err):
    r = generate(built, tmp_path, **kw)
    assert r.returncode == 1 and "INSTALL_TABLE_FAIL" in r.stderr
    assert err in r.stderr, r.stderr


def test_table_refuses_a_modded_receipt_and_case_twins(built, tmp_path):
    rec = synthetic_receipt(built)
    rec["modded"] = True
    r = generate(built, tmp_path, receipt=rec)
    assert r.returncode == 1 and "was taken from a modded tree" in r.stderr
    rec = synthetic_receipt(built)
    rec["files"].append(dict(rec["files"][-1], path=rec["files"][-1]["path"].upper()))
    r = generate(built, tmp_path, receipt=rec)
    assert r.returncode == 1 and "differ only in case" in r.stderr
    rec = synthetic_receipt(built)
    rec["files"] = [f for f in rec["files"] if f["path"] != "sonic2app.exe"]
    r = generate(built, tmp_path, receipt=rec)
    assert r.returncode == 1 and "0 entries named sonic2app.exe" in r.stderr


# ------------------------------------------------------------------ extras under resource/

def test_an_extra_the_game_would_read_is_refused_and_named(built, tmp_path):
    # The boot run probes resource/gd_PC/prs/forMovie.pak, which a clean install lacks.
    g = game(tmp_path, built, lambda f: f.update({"resource/gd_PC/prs/forMovie.pak": b"not shipped"}))
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == EXIT["refused"], p.stdout + p.stderr
    assert "refused: resource/gd_PC/prs/forMovie.pak: not in the table, under resource/" in p.stderr
    assert rec["counts"]["extras_refused"] == 1 and rec["installed"] is False
    assert rec["extras"] == [{"path": "resource/gd_PC/prs/forMovie.pak", "refused": True,
                              "why": rec["extras"][0]["why"]}]


def test_saves_user_config_unused_originals_and_files_outside_resource_may_stay(built, tmp_path):
    def mutate(f):
        f["resource/gd_PC/SAVEDATA/SONIC2B__ALF"] = b"a save"
        f["resource/gd_PC/StageSelectConfig.ini"] = b"[stage]"
        f["resource/gd_PC/PRS/AL_ENTRANCE_TEX_orig.prs"] = b"left behind"
        f["resource/gd_PC/DLL/Win32/Data_DLL_orig.dll"] = f[DLL]      # the named DLL is the shipped one
        f["redist/vcredist_x86.exe"] = b"redist"
        f["Config/UserConfig.cfg"] = b"<Configs/>"
    g = game(tmp_path, built, mutate)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    assert {x["path"]: x["why"] for x in rec["extras"]} == {
        "Config/UserConfig.cfg": "outside resource/",
        "redist/vcredist_x86.exe": "outside resource/",
        "resource/gd_PC/DLL/Win32/Data_DLL_orig.dll": "an unused original",
        "resource/gd_PC/PRS/AL_ENTRANCE_TEX_orig.prs": "an unused original",
        "resource/gd_PC/SAVEDATA/SONIC2B__ALF": "a user save",
        "resource/gd_PC/StageSelectConfig.ini": "user config"}
    assert rec["counts"]["extras_skipped"] == 6 and rec["substitutions"] == []


def test_an_orig_name_of_no_table_file_is_refused(built, tmp_path):
    g = game(tmp_path, built, lambda f: f.update({"resource/gd_PC/prs/forMovie_orig.pak": b"x"}))
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/prs/forMovie_orig.pak: not in the table" in p.stderr


def test_a_link_out_of_the_folder_under_resource_is_refused(built, tmp_path):
    g = game(tmp_path, built)
    (tmp_path / "elsewhere.pak").write_bytes(b"outside")
    (g / "resource/gd_PC/SOC/forMovie.pak").symlink_to(tmp_path / "elsewhere.pak")
    (g / "mods").symlink_to(tmp_path)                                  # outside resource/: skipped
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]
    assert "refused: resource/gd_PC/SOC/forMovie.pak: a link out of the folder under resource/" in p.stderr
    assert {"path": "mods", "refused": False,
            "why": "a link out of the folder, outside resource/: not followed"} in rec["extras"]


# ------------------------------------------------------------------ what the runtime loads as a DLL

@pytest.mark.parametrize("proxy,why", [
    (loader_proxy(dll=False), "the runtime refuses to load the named file (not a DLL)"),
    (loader_proxy(machine=0x8664), "the runtime refuses to load the named file (not an i386 image)"),
    (loader_proxy(magic=0x20B), "the runtime refuses to load the named file (not a PE32 optional header)"),
    (loader_proxy(align=0x1800), "the runtime refuses to load the named file (a section alignment that is not a power of two)"),
    (loader_proxy(export_size=0), "the runtime maps the named file"),
])
def test_a_proxy_the_runtime_would_not_substitute_is_refused(built, tmp_path, proxy, why):
    def mutate(f):
        mod_loader(f)
        f[DLL] = proxy
    g = game(tmp_path, built, mutate)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"], p.stdout + p.stderr
    assert why in p.stderr
    assert rec["substitutions"] == []


# ------------------------------------------------------------------ the overlay

def test_the_overlay_may_not_hold_a_substitutions_backing_or_files_under_resource(built, tmp_path):
    g = game(tmp_path, built, mod_loader)
    home = tmp_path / "home"
    assert run(built, home, "--source", "folder", str(g), "--yes")[0].returncode == 0
    overlay = home / ".local/share/sa2recomp/overlay"
    write_tree(overlay, {ORIG: b"an unverified data DLL"})
    p, rec = run(built, home, "--verify")
    assert p.returncode == EXIT["refused"]
    assert rec["refusals"] == [f"{ORIG}: the overlay holds a copy, which the runtime would read first"]
    (overlay / ORIG).unlink()
    write_tree(overlay, {"resource/gd_PC/prs/forMovie.pak": b"probed, unverified"})
    p, rec = run(built, home, "--verify")
    assert p.returncode == EXIT["refused"]
    assert rec["refusals"] == ["resource/gd_PC/prs/forMovie.pak: the overlay holds it under resource/, "
                               "where the game looks for files the install lacks: it would be read unverified"]


# ------------------------------------------------------------------ the source changing during a run

SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

/* SHIM_WHEN "open:<suffix>" or "rename:<suffix>": when the program opens (or renames onto) a path
 * ending in <suffix>, SHIM_TOUCH's mtime moves forward a second, once. */
static int done;
static void maybe(const char *path, const char *when) {
    const char *w = getenv("SHIM_WHEN"), *t = getenv("SHIM_TOUCH");
    size_t wl = strlen(when);
    if (done || !w || !t || strncmp(w, when, wl) || w[wl] != ':') return;
    const char *suf = w + wl + 1;
    size_t n = strlen(path), m = strlen(suf);
    if (n < m || strcmp(path + n - m, suf)) return;
    struct stat st;
    if (stat(t, &st)) return;
    struct timespec ts[2] = {st.st_atim, st.st_mtim};
    ts[1].tv_sec += 1;
    utimensat(AT_FDCWD, t, ts, 0);
    done = 1;
}
#define OPEN(name) int name(const char *p, int fl, ...) { \
    static int (*real)(const char *, int, ...); \
    if (!real) real = (int (*)(const char *, int, ...))dlsym(RTLD_NEXT, #name); \
    mode_t mode = 0; \
    if (fl & O_CREAT) { va_list ap; va_start(ap, fl); mode = va_arg(ap, int); va_end(ap); } \
    maybe(p, "open"); \
    return real(p, fl, mode); }
OPEN(open)
OPEN(open64)
int rename(const char *a, const char *b) {
    static int (*real)(const char *, const char *);
    if (!real) real = (int (*)(const char *, const char *))dlsym(RTLD_NEXT, "rename");
    int r = real(a, b);
    if (!r) maybe(b, "rename");
    return r;
}
'''


@pytest.fixture(scope="module")
def shim(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("shim")
    (d / "shim.c").write_text(SHIM)
    so = d / "shim.so"
    r = subprocess.run(["gcc", "-shared", "-fPIC", "-O1", "-o", str(so), str(d / "shim.c"), "-ldl"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return so


@pytest.mark.parametrize("when,message", [
    ("open:resource/gd_PC/ADX/title.adx", "the source changed while it was verified; run again"),
    ("rename:sa2recomp.cfg", "the source changed while the config was written; run again"),
])
def test_a_source_that_changes_during_the_run_is_not_accepted(built, tmp_path, shim, when, message):
    g = game(tmp_path, built)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), "--yes", "--threads", "1",
                 env_extra={"LD_PRELOAD": str(shim), "SHIM_WHEN": when, "SHIM_TOUCH": str(g / "Launcher.exe")})
    assert p.returncode == EXIT["io"], p.stdout + p.stderr
    assert message in p.stderr
    assert rec["source_manifest"]["untouched"] is False


# ------------------------------------------------------------------ unreadable is I/O, not refused

@pytest.fixture
def chmodded():
    paths: list[Path] = []
    yield paths
    for p in paths:
        p.chmod(0o755 if p.is_dir() else 0o644)


def test_an_unreadable_file_the_game_reads_is_an_io_error(built, tmp_path, chmodded):
    g = game(tmp_path, built)
    f = g / "resource/gd_PC/SOC/menu.pak"
    f.chmod(0)
    chmodded.append(f)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["io"], p.stdout + p.stderr
    assert "refused: resource/gd_PC/SOC/menu.pak: cannot be read: Permission denied" in p.stderr
    assert "check their permissions" in p.stderr
    assert rec["not_ok"][0]["io_error"] is True


def test_an_unreadable_folder_under_resource_is_an_io_error(built, tmp_path, chmodded):
    g = game(tmp_path, built)
    d = g / "resource/gd_PC/PRS"
    d.chmod(0)
    chmodded.append(d)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["io"], p.stdout + p.stderr
    assert "refused: resource/gd_PC/PRS/: cannot be read (Permission denied)" in p.stderr
    assert "resource/gd_PC/PRS/AL_ENTRANCE_TEX.prs: cannot be read: a folder on its path cannot be listed" in p.stderr


def test_an_unreadable_folder_outside_resource_is_listed_and_accepted(built, tmp_path, chmodded):
    g = game(tmp_path, built, lambda f: f.update({"redist/x.exe": b"redist"}))
    d = g / "redist"
    d.chmod(0)
    chmodded.append(d)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == 0, p.stdout + p.stderr
    assert {"path": "redist/", "refused": False,
            "why": "an unreadable folder outside resource/ (Permission denied): not indexed"} in rec["extras"]


def test_a_changed_file_beside_an_unreadable_one_is_refused(built, tmp_path, chmodded):
    g = game(tmp_path, built, lambda f: f.update({"resource/gd_PC/ADX/title.adx": b"changed"}))
    f = g / "resource/gd_PC/SOC/menu.pak"
    f.chmod(0)
    chmodded.append(f)
    p, _ = run(built, tmp_path / "home", "--source", "folder", str(g))
    assert p.returncode == EXIT["refused"]


# ------------------------------------------------------------------ Steam: dedup and bad flags

def test_one_game_folder_seen_through_two_libraries_is_one_install(built, tmp_path):
    home = tmp_path / "home"
    root = steam_library(home / ".local" / "share" / "Steam", built, tmp_path)
    lib2 = tmp_path / "lib2"
    (lib2 / "steamapps" / "common").mkdir(parents=True)
    (lib2 / "steamapps" / "appmanifest_213610.acf").write_text(acf())
    (lib2 / "steamapps" / "common" / "Sonic Adventure 2").symlink_to(root / "steamapps/common/Sonic Adventure 2")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders" {{ "0" {{ "path" "{root}" }} "1" {{ "path" "{lib2}" }} }}')
    p, rec = run(built, home, "--source", "steam")
    assert p.returncode == 0, p.stdout + p.stderr
    assert len(rec["source"]["steam"]["libraries"]) == 2 and len(rec["source"]["steam"]["installs"]) == 1


def test_a_state_flags_value_too_long_reads_as_not_installed(built, tmp_path):
    home = tmp_path / "home"
    steam_library(home / ".local" / "share" / "Steam", built, tmp_path, flags="99999999999999999999999")
    p, rec = run(built, home, "--source", "steam")
    assert p.returncode == EXIT["not_found"], p.stdout + p.stderr
    assert any("StateFlags 99999999999999999999999 is not fully installed" in n
               for n in rec["source"]["steam"]["notes"])


# ------------------------------------------------------------------ the record leaves the user out

def test_the_login_name_is_left_out_of_paths_outside_home(built, tmp_path):
    g = game(tmp_path / "media" / "sa2tester" / "Library", built)
    p, rec = run(built, tmp_path / "home", "--source", "folder", str(g), env_extra={"USER": "sa2tester"})
    assert p.returncode == 0
    text = json.dumps(rec)
    assert "sa2tester" not in text and "/media/<user>/Library/games/Sonic Adventure 2" in rec["source"]["path"]


# ------------------------------------------------------------------ the ZIP route (host/install's sa2_zip)

def make_zip(path: Path, files: dict[str, bytes], top: str = "Sonic Adventure 2/") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, data in sorted(files.items()):
            z.writestr(top + rel, data)
    return path


def test_zip_route_records_the_archive_and_the_store(built, tmp_path):
    z = make_zip(tmp_path / "sa2.zip", built["files"])
    home, data = tmp_path / "home", tmp_path / "data"
    p, rec = run(built, home, "--source", "zip", str(z), "--data-dir", str(data), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    sm = rec["source_manifest"]
    assert sm["method"] == "the archive's sha256, size and mtime, before and after the run"
    assert sm["files"] == 1 and sm["untouched"] is True
    assert sm["before"] == sm["after"] and sm["before"].startswith(f"sha256={_sha(z.read_bytes())} ")
    assert rec["store"]["path"] == str(data / "game") and rec["store"]["digest"]
    assert rec["store"]["files"] >= 8


def test_zip_route_puts_the_archives_user_files_in_the_overlay(built, tmp_path):
    files = dict(built["files"], **{"Config/Keyboard.cfg": b"<keys/>", "Config/UserConfig.cfg": b"<cfg/>"})
    z = make_zip(tmp_path / "sa2.zip", files)
    home, data = tmp_path / "home", tmp_path / "data"
    write_tree(data / "overlay", {"Config/UserConfig.cfg": b"the user's own"})
    p, rec = run(built, home, "--source", "zip", str(z), "--data-dir", str(data), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    assert rec["hash_checked"] is True and rec["zip"]["files_verified"] is True
    assert rec["zip"]["root"] == "Sonic Adventure 2/" and rec["zip"]["methods"] == [8]
    assert rec["zip"]["user_files_written"] == ["Config/Keyboard.cfg"]
    assert rec["zip"]["user_files_kept"] == ["Config/UserConfig.cfg"]
    assert (data / "overlay" / "Config" / "Keyboard.cfg").read_bytes() == b"<keys/>"
    assert (data / "overlay" / "Config" / "UserConfig.cfg").read_bytes() == b"the user's own"
    assert not (data / "game" / "Config").exists()


def test_zip_route_of_a_mod_loader_tree_writes_no_substitution(built, tmp_path):
    files = dict(built["files"])
    mod_loader(files)
    z = make_zip(tmp_path / "sa2.zip", files)
    home, data = tmp_path / "home", tmp_path / "data"
    p, rec = run(built, home, "--source", "zip", str(z), "--data-dir", str(data), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    # The store holds the original's verified bytes under the canonical name, and no *_orig file
    # for the runtime to map, so the config names no substitution; the record keeps it.
    assert rec["substitutions"] == [{"path": DLL, "backing": ORIG}]
    assert (data / "game" / DLL).read_bytes() == built["files"][DLL]
    assert not (data / "game" / ORIG).exists()
    cfg = (home / ".config" / "sa2recomp" / "sa2recomp.cfg").read_text()
    assert "substitution=" not in cfg and "source.kind=zip\n" in cfg


def test_zip_route_checks_the_overlay_before_extracting(built, tmp_path):
    z = make_zip(tmp_path / "sa2.zip", built["files"])
    home, data = tmp_path / "home", tmp_path / "data"
    write_tree(data / "overlay", {"resource/gd_PC/ADX/title.adx": b"a shadow"})
    p, rec = run(built, home, "--source", "zip", str(z), "--data-dir", str(data), "--yes")
    assert p.returncode == EXIT["refused"], p.stdout + p.stderr
    assert "nothing was extracted" in p.stderr
    assert not (data / "game").exists() and not (data / "game.partial").exists()


def test_zip_route_refuses_an_extra_the_game_would_read(built, tmp_path):
    files = dict(built["files"], **{"resource/gd_PC/prs/forMovie.pak": b"not shipped"})
    z = make_zip(tmp_path / "sa2.zip", files)
    p, rec = run(built, tmp_path / "home", "--source", "zip", str(z), "--data-dir", str(tmp_path / "data"), "--yes")
    assert p.returncode == EXIT["refused"], p.stdout + p.stderr
    # The ZIP route classes the archive's extras by the folder routes' rule before it extracts
    # anything (host/install/sa2_zip.cpp), so the refusal names the file as the archive spells it
    # and no store, finished or partial, is made.
    assert "resource/gd_PC/prs/forMovie.pak" in p.stderr and "would read it unverified" in p.stderr
    assert rec["installed"] is False and rec["hash_checked"] is False
    assert not (tmp_path / "data" / "game").exists() and not (tmp_path / "data" / "game.partial").exists()


# ------------------------------------------------------------------ the Config/ defaults

# The files every registered replay ran with: the installer's defaults are these bytes.
REPLAY_CONFIG = ROOT / "private" / "install" / "Config"
DEFAULTS = ("Config/UserConfig.cfg", "Config/Keyboard.cfg")


def replay_config() -> dict[str, bytes]:
    return {rel: (REPLAY_CONFIG / rel.split("/", 1)[1]).read_bytes() for rel in DEFAULTS}


def overlay_files(overlay: Path) -> dict[str, bytes]:
    return {p.relative_to(overlay).as_posix(): p.read_bytes() for p in sorted(overlay.rglob("*")) if p.is_file()}


def defaults_of(rec: dict) -> dict[str, tuple[bool, bool, bool]]:
    """{path: (in_read_root, in_overlay, default_written)} of the record's user_defaults."""
    return {d["path"]: (d["in_read_root"], d["in_overlay"], d["default_written"]) for d in rec["user_defaults"]}


def test_a_game_folder_with_no_user_config_gets_the_defaults_in_the_overlay(built, tmp_path):
    """A Steam install never launched has an empty Config/ (SA2's Launcher.exe writes
    UserConfig.cfg and Keyboard.cfg), and the game cannot be played without them: the installer
    writes the replays' files into the overlay and leaves the game folder alone."""
    g = game(tmp_path, built)
    (g / "Config").mkdir()                       # the empty folder a fresh Steam install has
    before = tree_state(g)
    home = tmp_path / "home"
    p, rec = run(built, home, "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    overlay = home / ".local/share/sa2recomp/overlay"
    assert overlay_files(overlay) == replay_config()
    assert tree_state(g) == before and not list((g / "Config").iterdir())
    assert rec["user_defaults"] == [
        {"path": rel, "in_read_root": False, "in_overlay": False, "default_written": True,
         "written_at": str(overlay / rel).replace(str(home), "~"), "default_sha256": _sha(data)}
        for rel, data in replay_config().items()]
    assert "wrote the default Config/UserConfig.cfg into the overlay" in p.stdout
    assert "wrote the default Config/Keyboard.cfg into the overlay" in p.stdout
    # The quick verify finds them there and writes nothing.
    p, rec = run(built, home, "--verify")
    assert p.returncode == 0 and rec["verified_mode"] == "quick", p.stdout + p.stderr
    assert defaults_of(rec) == {rel: (False, True, False) for rel in DEFAULTS}


def test_a_game_folders_own_config_files_are_left_alone(built, tmp_path):
    """The user's own files (any case, as the runtime resolves it) are read from the game folder:
    only the one it lacks is written, each on its own."""
    g = game(tmp_path, built, mutate=lambda f: f.update({"config/USERCONFIG.CFG": b"<Configs Width=\"1920\" />"}))
    home = tmp_path / "home"
    p, rec = run(built, home, "--source", "folder", str(g), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    assert overlay_files(home / ".local/share/sa2recomp/overlay") == {
        "Config/Keyboard.cfg": replay_config()["Config/Keyboard.cfg"]}
    assert defaults_of(rec) == {"Config/UserConfig.cfg": (True, False, False),
                                "Config/Keyboard.cfg": (False, False, True)}
    assert (g / "config/USERCONFIG.CFG").read_bytes() == b"<Configs Width=\"1920\" />"
    g2 = game(tmp_path, built, name="played", mutate=lambda f: f.update(
        {"Config/UserConfig.cfg": b"<Configs />", "Config/Keyboard.cfg": b"<Players />"}))
    p, rec = run(built, tmp_path / "home2", "--source", "folder", str(g2), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    assert overlay_files(tmp_path / "home2/.local/share/sa2recomp/overlay") == {}
    assert defaults_of(rec) == {rel: (True, False, False) for rel in DEFAULTS}


def test_an_overlay_config_file_is_kept_and_its_folder_spelling_too(built, tmp_path):
    g = game(tmp_path, built)
    home, data = tmp_path / "home", tmp_path / "data"
    write_tree(data / "overlay", {"config/userconfig.cfg": b"the user's own"})
    p, rec = run(built, home, "--source", "folder", str(g), "--yes", "--data-dir", str(data))
    assert p.returncode == 0, p.stdout + p.stderr
    assert overlay_files(data / "overlay") == {"config/userconfig.cfg": b"the user's own",
                                               "config/Keyboard.cfg": replay_config()["Config/Keyboard.cfg"]}
    assert defaults_of(rec)["Config/UserConfig.cfg"] == (False, True, False)
    # Removed, the default goes into the overlay's own config/ folder, not a second Config/, and
    # the quick verify writes it (an install made before the defaults gets them at its next launch).
    (data / "overlay/config/userconfig.cfg").unlink()
    p, rec = run(built, home, "--verify")
    assert p.returncode == 0 and rec["verified_mode"] == "quick", p.stdout + p.stderr
    assert overlay_files(data / "overlay") == {"config/Keyboard.cfg": replay_config()["Config/Keyboard.cfg"],
                                               "config/UserConfig.cfg": replay_config()["Config/UserConfig.cfg"]}
    assert defaults_of(rec) == {"Config/UserConfig.cfg": (False, False, True),
                                "Config/Keyboard.cfg": (False, True, False)}


def test_the_zip_route_writes_the_defaults_when_the_archive_has_none(built, tmp_path):
    """The store never holds Config/ (an archive's user files go to the overlay); an archive with
    none gets the defaults in the overlay, and one with its own keeps them."""
    home, data = tmp_path / "home", tmp_path / "data"
    p, rec = run(built, home, "--source", "zip", str(make_zip(tmp_path / "sa2.zip", built["files"])),
                 "--data-dir", str(data), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    assert overlay_files(data / "overlay") == replay_config()
    assert defaults_of(rec) == {rel: (False, False, True) for rel in DEFAULTS}
    assert not (data / "game" / "Config").exists()
    files = dict(built["files"], **{"Config/UserConfig.cfg": b"<cfg/>", "Config/Keyboard.cfg": b"<keys/>"})
    home2, data2 = tmp_path / "home2", tmp_path / "data2"
    p, rec = run(built, home2, "--source", "zip", str(make_zip(tmp_path / "own.zip", files)),
                 "--data-dir", str(data2), "--yes")
    assert p.returncode == 0, p.stdout + p.stderr
    assert overlay_files(data2 / "overlay") == {"Config/UserConfig.cfg": b"<cfg/>", "Config/Keyboard.cfg": b"<keys/>"}
    assert defaults_of(rec) == {rel: (False, True, False) for rel in DEFAULTS}


def test_a_dry_run_writes_no_default(built, tmp_path):
    g = game(tmp_path, built)
    home = tmp_path / "home"
    p, rec = run(built, home, "--source", "folder", str(g))
    assert p.returncode == 0, p.stdout + p.stderr
    assert not (home / ".local/share/sa2recomp").exists() and "user_defaults" not in rec


# ------------------------------------------------------------------ the fresh-host sandbox (host/install/fresh_host_run.py)

def test_fresh_host_sandbox_binds_only_the_game_and_its_two_manifests():
    import runpy
    from types import SimpleNamespace
    m = SimpleNamespace(**runpy.run_path(str(SRC / "fresh_host_run.py")))   # no __pycache__ in the tree
    home = Path("/home/u")
    steam = home / ".local/share/Steam"
    steam_s, ml_s = m.sessions(home, steam, Path("/srv/modloader"))
    apps = steam / "steamapps"
    want = [apps / "libraryfolders.vdf", apps / "appmanifest_213610.acf", apps / "common/Sonic Adventure 2"]
    assert steam_s["binds"] == [(p, p) for p in want]
    assert ml_s["binds"] == [(Path("/srv/modloader"), home / "Games/Sonic Adventure 2")]
    argv = m.bwrap_argv(home, steam_s["binds"], Path("/b/sa2-install"), Path("/o"), "u", "true")
    ro = [argv[i + 1] for i, x in enumerate(argv) if x == "--ro-bind"]
    assert [x for x in ro if x not in ("/usr", "/etc/ld.so.cache")] == [str(p) for p in want] + ["/b/sa2-install"]
    assert [argv[i + 1] for i, x in enumerate(argv) if x == "--bind"] == ["/o"]
    assert "--unshare-all" in argv and "--clearenv" in argv
    assert any(argv[i:i + 2] == ["--tmpfs", str(home)] for i in range(len(argv)))   # HOME starts empty
    script = m.session_script(home, steam_s["hidden"], steam_s["steps"], steam_s["probe"])
    for h in ("compatdata", "shadercache", "workshop", "downloading", "temp"):
        assert f"[ -e {apps / h} ]" in script
    assert "-name pfx" in script and "exit 90" in script
    assert [s[2] for s in steam_s["steps"]] == [0, 0, 0] and [s[2] for s in ml_s["steps"]] == [0, 4]


# ------------------------------------------------------------------ shipped files the table leaves out

def unhashed_receipt(built: dict) -> dict:
    doc = json.loads(built["receipt"].read_text())
    doc["excluded_under_resource"] = [{"path": "resource/gd_PC/StageSelectConfig.ini",
                                       "reason": "user config (.ini)"}]
    return doc


def generate_with_reference(built: dict, tmp_path: Path, strings: list[bytes]) -> subprocess.CompletedProcess:
    """The table generated with the reference executable carrying `strings` after its sections
    (where a string table's bytes would be), against the receipt that excludes the ini."""
    rpath = tmp_path / "receipt.hashes"
    rpath.write_text(json.dumps(unhashed_receipt(built)))
    ref = tmp_path / "unpacked.exe"
    ref.write_bytes(unpacked_exe() + b"\0" * 4 + (b"\0" * 4).join(strings) + b"\0" * 4)
    return subprocess.run([sys.executable, str(TABLE_TOOL), "--receipt", str(rpath),
                           "--exe", str(built["work"] / "shipped.exe"), "--reference", str(ref),
                           "--pins", "none", "--drm-receipt", "none", "--out-dir", str(tmp_path / "t")],
                          capture_output=True, text=True)


def test_an_unhashed_shipped_file_the_game_names_elsewhere_is_left_out(built, tmp_path):
    """resource/gd_PC/StageSelectConfig.ini is not hashed; the executable names it only under
    Config/ (ASCII here, UTF-16 too), so the game never reads the shipped copy."""
    r = generate_with_reference(built, tmp_path, [b".\\Config\\StageSelectConfig.ini",
                                                  "Config/stageselectconfig.ini".encode("utf-16-le")])
    assert r.returncode == 0, r.stderr
    summary = (tmp_path / "t" / "sa2_install_table.txt").read_text()
    assert "shipped under resource/ and not hashed: 1" in summary
    assert ("resource/gd_PC/StageSelectConfig.ini (user config (.ini)): unread by the game: the "
            "executable names it only as \".\\Config\\StageSelectConfig.ini\", "
            "\"Config/stageselectconfig.ini\"") in summary
    assert "StageSelectConfig" not in (tmp_path / "t" / "sa2_install_table.cpp").read_text()


@pytest.mark.parametrize("strings, words", [
    ([b"StageSelectConfig.ini"], "names it as 'StageSelectConfig.ini', which can reach the shipped copy"),
    ([b".\\resource\\gd_PC\\StageSelectConfig.ini"], "which can reach the shipped copy"),
    ([b"gd_PC\\stageselectconfig.INI"], "which can reach the shipped copy"),
    (["StageSelectConfig.ini".encode("utf-16-le")], "which can reach the shipped copy"),
    ([b"nothing here"], "names StageSelectConfig.ini nowhere"),
    ([b"%s\\resource\\gd_PC\\StageSelectConfig.ini"], "which can reach the shipped copy"),
    ([b"..\\resource\\gd_PC\\StageSelectConfig.ini"], "which can reach the shipped copy"),
    ([b"C:\\Games\\Sonic Adventure 2\\resource\\gd_PC\\StageSelectConfig.ini"],
     "which can reach the shipped copy"),
    ([b"%s\\StageSelectConfig.ini"], "which can reach the shipped copy"),
    ([b"$(GAME)\\StageSelectConfig.ini"], "which can reach the shipped copy"),
    ([b"..\\Config\\StageSelectConfig.ini"], "which can reach the shipped copy"),
], ids=["bare-name", "resource-path", "folder-suffix", "utf16-bare", "not-named",
        "placeholder-then-shipped-folder", "dotdot-shipped-folder", "absolute-shipped-folder",
        "placeholder-folder", "variable-folder", "dotdot-folder"])
def test_an_unhashed_shipped_file_the_game_may_read_is_refused(built, tmp_path, strings, words):
    r = generate_with_reference(built, tmp_path, strings)
    assert r.returncode == 1
    assert "INSTALL_TABLE_FAIL resource/gd_PC/StageSelectConfig.ini is not hashed" in r.stderr
    assert words.lower() in r.stderr.lower()


def test_a_file_both_hashed_and_excluded_is_refused(built, tmp_path):
    doc = unhashed_receipt(built)
    doc["excluded_under_resource"] = [{"path": "resource/gd_PC/ADX/title.adx", "reason": "x"}]
    r = generate(built, tmp_path, receipt=doc)
    assert r.returncode == 1 and "both hashes and excludes resource/gd_PC/ADX/title.adx" in r.stderr
